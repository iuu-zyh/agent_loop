/// <summary>
/// ContactStore —— 通讯录数据中转层（**纯静态、不持有任何 Unity 对象引用**）。
///
/// 背景（世界输入失效事故）：通讯录/配置面板改为"按需创建"（方案 A）后，
/// 面板实例在首次打开前不存在、且游戏关闭面板时可能销毁实例——因此所有
/// "面板不在场也必须可用"的数据必须搬到与实例无关的静态层：
///   ① 手动好友镜像（真相在 Python contacts.json，此处只是 C# 侧缓存）
///   ② 会话文件 mtime 索引（npc_id → 秒）—— **只是"上次存档"的时刻**，见 ③
///   ③ 互动时间戳（npc_reply 到达时刻）—— 「最近」排序的**权威**源
///
/// ── 「最近」为什么必须优先用 ③（10-21 用户报障，勿再改回 max）────────────────
/// 用户现象：「NPC 主动传音后点开通讯录，他从『最近』里的位置没变」。
/// 根因是 ② 的 mtime **不是**"上次说话的时刻"，而是"上次**存档**的时刻"：
///   · 存档时 `flush_all` 把**所有** NPC 一次重写，十几个文件落在同一秒
///     （真机实测：姜萌 .953 / 江宇文 .956，同一秒）⇒ 截断成整秒后**全相等**，排序退化成随机；
///   · 而且存档稀疏：今天说的话在存档前一个字都没落盘，mtime 还停在上次存档
///     ⇒ 主动传音的 NPC 在「最近」里纹丝不动。
/// ⇒ `LastInteract` = **③ 优先**（Python 现在随 `list_sessions` 回传持久化的 `last`），
///   ③ 缺失时才回落到 ②（给没有新记录的老存档一个可用排序）。
///   ⚠ **不要写成 Math.Max(②, ③)**：存档那一刻 ② 立刻又盖过 ③，原 bug 原样复发。
///
/// 消费方：NpcInitiativeMonitor（候选/冷却）、NpcPanelAddContact（按钮状态）、
///         ContactPresenter（渲染时读取并应用）。
///
/// 铁律：**只存 id/名字/时间戳等纯托管数据，绝不缓存 WorldUnitBase/GameObject 等
/// IL2CPP 引用**（僵尸引用风险）；单位引用一律在使用现场经 UnitLookup.Resolve 现查。
/// 线程约定：全部在 Unity 主线程调用（RPC 回调经 WsClient 主线程派发）。
/// </summary>
using System;
using System.Collections.Generic;

namespace AgentLoopBridge
{
    /// <summary>关系层快照的一条（`known.json` 的镜像单元）。**纯托管数据**，不含任何 Unity 引用。</summary>
    internal sealed class KnownContact
    {
        public string Name;
        /// <summary>精确 unitID（快照写入时手握真身读到的）。空 = 老记录没存，只能按名猜。</summary>
        public string UnitId;
        /// <summary>关系层里的中文关系（道侣/师傅/徒弟/结义…）。**只在活着时抓得到** ——
        /// 死者快照的 `GetRelation(玩家)` 返回 None。空串 = 当时没有正式关系（好友簿成员）。</summary>
        public string Relation;
    }

    internal static class ContactStore
    {
        private static readonly HashSet<string> _manualNames = new HashSet<string>();
        /// <summary>手动好友的**精确 unitID**（name → unitID）。缺失 = 这条好友没有精确身份，
        /// 消费方只能按名解析（全图扫，可能撞重名）。来源见 `SetManualContact` 的注释。</summary>
        private static readonly Dictionary<string, string> _manualUnitIds = new Dictionary<string, string>();
        private static readonly Dictionary<string, long> _sessionsLast = new Dictionary<string, long>();
        /// <summary>Python 持久化的"最后一次真的说话"时刻（npc_id → unix 秒，来自
        /// `list_sessions` 的 `last` 字段 / interactions.json）。**「最近」排序的权威源**：
        /// 它跨存档、跨重启都活着，且不受"存档把 mtime 刷成同一秒"的影响。
        /// 缺失（老 Python 不回这个字段 / 这个 NPC 从没说过话）→ 回落到 `_sessionsLast`。</summary>
        private static readonly Dictionary<string, long> _serverLast = new Dictionary<string, long>();
        private static readonly Dictionary<string, long> _localLast = new Dictionary<string, long>();
        /// <summary>「我们之间说过话」的**名字集合**（10-24「临时」页的成员来源）。
        ///
        /// ★ 为什么不复用 `_localLast` ★
        /// `_localLast` 是**时间戳**表、而且写入点在 `npc_reply` 的**所有**早退之前（含
        /// `error=true` 的失败兜底帧）—— 那是给「最近」排序用的，语义是"这一帧到过"。
        /// 而「临时」要回答的是"**他说过话**"：失败兜底帧（"他这一阵正忙着，没接上你的话"）
        /// 不是他说的话，不该让他凭空进名单。故另记一份、只收非失败帧（见 `MarkHeard`），
        /// **不动 `_localLast` 的写入点**（动它就是改「最近」排序的既有语义，风险不对等）。
        ///
        /// 只存名字、不存时间：排序统一走 `LastInteract`（那份已经有完整的三级回落）。</summary>
        private static readonly HashSet<string> _heard = new HashSet<string>();
        /// <summary>关系层快照镜像（Python `known.json`）：名字 → (unitID, 关系标签)。
        ///
        /// ★ 它回答的问题只有一个 ★：「这个人**曾经**在我的通讯录里吗？」
        /// 关系层是每次渲染现取游戏数据的（`RelationNetwork.CollectKnownUnits`），而死者
        /// `GetUnit(id)` 恒 null ⇒ 三条采集路全部 `if (u == null) return` ⇒ 人一死就从
        /// 名单里**静默消失**（连"已不在世间"都看不到）。死后也反推不出来：`ClearDieUnit`
        /// 清了关系簿、`DieData.relationData.GetRelation(玩家)` 返回 None（真机确认）。
        /// 于是"他来过"这件事**只有活着时能抓**，这份就是那份记录。
        ///
        /// 消费规则（唯一一条，别放宽）：**只在 `GetUnit(id)` 解析不到时才用它**。
        /// 活人永远由关系层现取 —— 断交/改嫁/离场的活人解析得到，就不会借快照复活。
        /// </summary>
        private static readonly Dictionary<string, KnownContact> _known = new Dictionary<string, KnownContact>();
        private static bool _manualLoaded;
        /// <summary>会话索引是否**拉到过**（`list_sessions` 成功回包过）。
        /// 用途同 `_manualLoaded`：区分"还没拉到"与"确实没有"，否则「临时」页在第一次
        /// 打开面板时会先说一句"还没有"、再突然冒出一堆人。</summary>
        private static bool _heardLoaded;
        /// <summary>关系层快照是否**拉到过**（`list_known_contacts` 成功回包过）。
        /// 未拉到就不补"已故"行 —— 否则面板会先少几行、回包后再突然冒出来。</summary>
        private static bool _knownLoaded;
        /// <summary>Python 明确说过"未定档"（见 `WorldUnknown` 注释）。</summary>
        private static bool _worldUnknown;

        /// <summary>换存档（`ModMain.OnIntoWorldEvent`）：**整表作废**，因为这份镜像里的名字
        /// 全部属于**刚离开的那个世界**。
        ///
        /// ★ 为什么必须清、而不是"等回包覆盖"（10-18「新开存档继承了通讯录」）★
        /// 回包没回来之前，面板会先把这份**旧档名单**渲染出来（`ContactPresenter.Show()` 第 ③ 步
        /// "先把 Store 里已有的镜像并进来，RPC 未回前也能显示上次拉到的名单"）—— 于是新存档里
        /// 显示的是上一个存档的好友。清掉之后面板只会显示关系层 + 状态条
        /// （`_manualLoaded=false` → "通讯录名单加载中…"；若 Python 未定档则 → "尚未取得存档身份"）。
        ///
        /// 为什么**不**顺手 `_worldUnknown = true`：那是在替 Python 说话（"它说它不知道"）。
        /// 此刻的事实只是"我们还没拉到" —— 两者文案不同，混用会让一次普通换档看起来像故障。
        /// 回包回来会各自落到正确的那一支（`SetManualNames` 清标注 / `MarkWorldUnknown` 置标注）。
        ///
        /// 不碰盘、不碰 Python：只丢 C# 手上这份缓存，重新拉一次就有。
        /// </summary>
        public static void ResetForWorld()
        {
            _manualNames.Clear();
            _manualUnitIds.Clear();
            _sessionsLast.Clear();
            _serverLast.Clear();       // 服务端持久化的互动时刻同理：上一档的名字/时间不该带到新档
            _localLast.Clear();        // 本地互动时间戳同理：上一档的"最近"排序不该带到新档
            _heard.Clear();            // 「说过话」的名字同理：上一档的人绝不能出现在新档的「临时」页
            _hiddenNames.Clear();      // 隐藏名单同理（它只在面板打开时拉，换档不清会残留到新档）
            _known.Clear();            // 关系层快照同理：那是**别的存档**里的人，留着会让新档的名单凭空多出已故行
            _manualLoaded = false;
            _heardLoaded = false;
            _knownLoaded = false;
            _worldUnknown = false;
            Fire();
        }

        /// <summary>数据变更通知（视图层订阅：面板开着时重渲行；未开则忽略）。</summary>
        public static event Action Changed;
        private static void Fire() { try { Changed?.Invoke(); } catch { } }

        /// <summary>手动好友镜像是否已从 Python 拉到过（避免"未拉取"与"空列表"混淆）。
        ///
        /// ⚠ 09-20 补充：本标志**不再**在"未定档被拒答"时置真（那种回包一个字都不写，
        /// 见 `ContactDuty.WorldKnown`）—— 否则面板会一边显示残缺名单、一边说"已加载完"，
        /// 玩家以为好友丢了。空表**有效**（Python 确知是本存档且没有好友）才置真。</summary>
        public static bool ManualLoaded => _manualLoaded;

        /// <summary>Python 是否**明确说过**"我还不知道这是哪个存档"（未定档）。
        ///
        /// 用途：面板据此如实显示"未取得存档身份，请存/读一次档"，而不是把残缺名单
        /// 伪装成完整名单。它回答的是"这份镜像可不可信"，与"有没有好友"无关。
        /// 定档后的成功回包会把它清回 false。</summary>
        public static bool WorldUnknown => _worldUnknown;

        /// <summary>手动好友名集合（只读视图；NPC 面板按钮状态 / 主动传音候选用）。</summary>
        public static IEnumerable<string> ManualContactNames => _manualNames;

        public static bool IsManualContact(string name)
        {
            return !string.IsNullOrEmpty(name) && _manualNames.Contains(name);
        }

        /// <summary>某手动好友的精确 unitID（没有则 null）。
        ///
        /// **它回答的是"这个人是谁，我们确定吗"**：非 null = 精确（可以直接 `g.world.unit.GetUnit`），
        /// null = 不知道（消费方只能 `UnitLookup.Resolve(name)` 按名猜，重名时会挂到别人身上）。
        /// 没有它的情形有二：① 这条好友是在本功能上线前加的（老 contacts.json）；② C# 侧
        /// 还没在"握着真身"的时刻回填过（见 `ContactDuty.BackfillManualUnitId`）。</summary>
        public static string ManualUnitId(string name)
        {
            if (string.IsNullOrEmpty(name)) return null;
            string id;
            return _manualUnitIds.TryGetValue(name, out id) ? id : null;
        }

        /// <summary>RPC 回包：整体替换手动好友镜像（名字 + 可选精确 unitID）。
        ///
        /// ★ 签名从 `IEnumerable<string>` 改成键值对是**刻意的**：全工程只有 `ContactDuty` 一个
        ///   调用方（编译器会逼它一起改），比加一个重载更不容易漏 —— 而漏掉的后果是"镜像里
        ///   永远没有 unitID"，静默退化成按名猜，不报错。（Value 为空串 = 该好友没有精确身份。）</summary>
        public static void SetManualNames(IEnumerable<KeyValuePair<string, string>> items)
        {
            _manualNames.Clear();
            _manualUnitIds.Clear();
            if (items != null)
            {
                foreach (var kv in items)
                {
                    if (string.IsNullOrEmpty(kv.Key)) continue;
                    _manualNames.Add(kv.Key);
                    if (!string.IsNullOrEmpty(kv.Value)) _manualUnitIds[kv.Key] = kv.Value;
                }
            }
            _manualLoaded = true;
            _worldUnknown = false;      // 定档后的有效回包（哪怕是空表）⇒ 镜像可信
            Fire();
        }

        /// <summary>Python 回包说"未定档"（`world_known=false`）：**不动镜像**，只记下"这份镜像
        /// 不可信"这一事实，供面板如实提示。与 `SetManualNames` 分开是刻意的 —— 前者是"写数据"，
        /// 这里是"标注数据可不可信"，混在一起就会再次出现"空表把镜像清掉"的事故。</summary>
        public static void MarkWorldUnknown()
        {
            if (_worldUnknown) return;
            _worldUnknown = true;
            Fire();     // 面板开着时立刻如实改状态条
        }

        /// <summary>加/移除一个手动好友（本地即时生效；真相仍以 Python 回包校准为准）。
        ///
        /// `unitId` 非空 = **调用方此刻真的握着那个 `WorldUnitBase`**（只有这种调用才允许写进精确表）。
        /// ★ 铁律：按名解析出来的结果**绝不许**从这里传进来 —— 那是把猜测洗成权威
        ///   （`UnitIdRegistry.Remember` 注释同款理由：按名查出来的结果永远不许写进权威层。）
        /// ★ 不传 `unitId`（默认 null）**不会清掉**已有的精确 ID —— 否则一次"移除→再加"
        ///   或任何不带 id 的调用就会把它抹掉。</summary>
        public static void SetManualContact(string name, bool add, string unitId = null)
        {
            if (string.IsNullOrEmpty(name)) return;
            bool changed = add ? _manualNames.Add(name) : _manualNames.Remove(name);
            if (add)
            {
                if (!string.IsNullOrEmpty(unitId) && ManualUnitId(name) != unitId)
                {
                    _manualUnitIds[name] = unitId;
                    changed = true;
                }
            }
            else if (_manualUnitIds.Remove(name)) changed = true;   // 移除好友 → 精确表同步清
            if (changed) Fire();
        }

        // ------------------------------------------------------------------
        // 隐藏名单镜像（真相同样在 Python `hidden.json`）
        //
        // 语义（09-26 用户拍板）：通讯录行内 ✕ = **尽量把这个人从列表移除** ——
        //   · 好友行 → 删好友（走手动层）；若关系层仍有他，顺带隐藏；
        //   · 纯关系行（道侣/子女/仇人…）→ 隐藏（**关系数据一点不动**，只是不在通讯录渲染）。
        // 重现规则：该 NPC **再次主动传音**时由 `ContactDuty` 取消隐藏 —— **唯一**出口。
        // 好友优先：渲染过滤不得挡好友（玩家重新加为好友 = 立即显示）。
        // ------------------------------------------------------------------

        private static readonly HashSet<string> _hiddenNames = new HashSet<string>();

        /// <summary>隐藏名单是否非空（渲染过滤的快速短路：空名单不付出任何遍历成本）。</summary>
        public static bool HasHidden => _hiddenNames.Count > 0;

        public static bool IsHidden(string name)
        {
            return !string.IsNullOrEmpty(name) && _hiddenNames.Contains(name);
        }

        /// <summary>RPC 回包：整体替换隐藏名单（`list_hidden`）。</summary>
        public static void SetHiddenNames(IEnumerable<string> names)
        {
            _hiddenNames.Clear();
            if (names != null)
            {
                foreach (var n in names)
                    if (!string.IsNullOrEmpty(n)) _hiddenNames.Add(n);
            }
            Fire();     // 面板开着时立刻重渲（隐藏/取消隐藏即时可见）
        }

        /// <summary>加/移除一条隐藏（本地即时生效；真相由 `list_hidden` 回包校准）。</summary>
        public static void SetHidden(string name, bool hide)
        {
            if (string.IsNullOrEmpty(name)) return;
            bool changed = hide ? _hiddenNames.Add(name) : _hiddenNames.Remove(name);
            if (changed) Fire();
        }

        // ------------------------------------------------------------------
        // 关系层快照镜像（真相在 Python `known.json`）
        //
        // 用途**只有一个**：关系层现取不到的人（死者 / unitID 失效），靠它把行补回来。
        // 所以消费方必须自己先判"解析得到 ⇒ 不用它"（见 `_known` 字段注释）。
        // ------------------------------------------------------------------

        /// <summary>快照是否已从 Python 拉到过（区分"还没拉到"与"确实没有"）。
        /// 未拉到时不补任何行 —— 否则第一次打开面板会先少几行、回包后再突然冒出来。</summary>
        public static bool KnownLoaded => _knownLoaded;

        /// <summary>快照里有没有他（名字键）。**不回答"他是不是还活着"** —— 那是 `UnitSnapshot.IsDead`。</summary>
        public static bool IsKnown(string name)
        {
            return !string.IsNullOrEmpty(name) && _known.ContainsKey(name);
        }

        /// <summary>快照里的精确 unitID（没有则 null）。死者判定用它走 O(1) 的 `allUnitDie[id]`。</summary>
        public static string KnownUnitId(string name)
        {
            KnownContact rec;
            return (!string.IsNullOrEmpty(name) && _known.TryGetValue(name, out rec)) ? rec.UnitId : null;
        }

        /// <summary>快照里的关系标签（道侣/师傅…）；没有则空串。</summary>
        public static string KnownRelation(string name)
        {
            KnownContact rec;
            return (!string.IsNullOrEmpty(name) && _known.TryGetValue(name, out rec)) ? (rec.Relation ?? "") : "";
        }

        /// <summary>快照全量（渲染层遍历用）。**不要修改返回的对象**。</summary>
        public static IEnumerable<KnownContact> KnownContacts => _known.Values;

        public static int KnownCount => _known.Count;

        /// <summary>RPC 回包（`list_known_contacts`）：整体替换快照镜像。</summary>
        public static void SetKnown(IEnumerable<KnownContact> items)
        {
            _known.Clear();
            if (items != null)
            {
                foreach (var it in items)
                {
                    if (it == null || string.IsNullOrEmpty(it.Name)) continue;
                    _known[it.Name] = it;
                }
            }
            _knownLoaded = true;
            Fire();
        }

        /// <summary>RPC 回包（`list_sessions`）：整体替换会话索引。
        ///
        /// 两个字段**分开存、语义不同**（10-21，勿再合并）：
        ///   · `mtime` = 文件最后落盘时刻 = **上次存档**的时刻（一次存档全批同时刻，无区分度）；
        ///   · `last`  = 这个 NPC **最后一次真的说话**的时刻（Python 从 interactions.json 持久化读回；
        ///               缺键/为 0 = 没有记录，老 Python 不回这个字段时整列都是 0）。
        /// 排序判据见 `LastInteract`。
        ///
        /// ⚠ `last == 0` **不能**当成"比谁都新"或"比谁都旧"来特判 —— 它只表示"这条没数据"，
        ///   由 `LastInteract` 回落处理。写成 `> 0` 才存（与 mtime 同款）即可。
        /// </summary>
        public static void SetSessionMtimes(IEnumerable<KeyValuePair<string, long>> items)
        {
            SetSessionIndex(items, null);
        }

        /// <summary>`SetSessionMtimes` 的双列版本（先两列都清、再各自填）。</summary>
        public static void SetSessionIndex(IEnumerable<KeyValuePair<string, long>> mtimes,
                                           IEnumerable<KeyValuePair<string, long>> lasts)
        {
            _sessionsLast.Clear();
            _serverLast.Clear();
            if (mtimes != null)
            {
                foreach (var kv in mtimes)
                {
                    if (!string.IsNullOrEmpty(kv.Key) && kv.Value > 0) _sessionsLast[kv.Key] = kv.Value;
                }
            }
            if (lasts != null)
            {
                foreach (var kv in lasts)
                {
                    if (!string.IsNullOrEmpty(kv.Key) && kv.Value > 0) _serverLast[kv.Key] = kv.Value;
                }
            }
            // 「说过话」的名字来源 = 账本文件键 ∪ 持久化互动时刻。
            //
            // ★ 这里是**只增不减**（不清 `_heard`）★
            // `SetSessionIndex` 每次回包都整体替换 `_sessionsLast`/`_serverLast`，但 `_heard`
            // 还混着**本地即时**那一份（`MarkLocal` 记的、他说过话但还没存档/还没落盘的人）。
            // 清掉就会让"刚跟我说完话的人"在下次拉索引时从「临时」页消失 —— 正是本次要修的
            // 那个形态。换档由 `ResetForWorld` 单独整表清，那里才是唯一的清空点。
            foreach (var k in _sessionsLast.Keys) _heard.Add(k);
            foreach (var k in _serverLast.Keys) _heard.Add(k);
            _heardLoaded = true;
            Fire();
        }

        /// <summary>记下"这个人跟我说过话"（「临时」页的成员来源，**不需要存档**）。
        ///
        /// 调用点唯一：`ContactDuty.OnUiEvent` 的收口帧分流里，**排除失败兜底帧之后**。
        /// 与 `MarkLocal`（时间戳）刻意分开：那个在一切早退之前无条件跑（供「最近」排序），
        /// 这个只认"真的说出了一句话"（见 `_heard` 的注释）。
        ///
        /// 幂等：已在集合里就直接返回（不 `Fire`）—— 它会被高频调用，没必要每帧重渲面板。</summary>
        public static void MarkHeard(string npc)
        {
            if (string.IsNullOrEmpty(npc)) return;
            if (_heard.Add(npc)) Fire();
        }

        /// <summary>「说过话」的名字（只读快照；「临时」页的候选来源）。
        /// 返回 `List` 而不是 `IEnumerable`：消费方要 `foreach` 两遍（过滤 + 建行），
        /// 而 `HashSet` 直接在 `foreach` 里被 `Fire()` 的订阅者改到就会抛。
        /// 集合为空返回**共享空表**（常路径零分配）。</summary>
        public static List<string> HeardNames()
        {
            if (_heard.Count == 0) return _heardEmpty;
            return new List<string>(_heard);
        }

        private static readonly List<string> _heardEmpty = new List<string>();

        /// <summary>会话索引拉到过没有（「临时」页据此区分"加载中"与"确实没有"）。
        /// 与 `ManualLoaded` 同一套语义：**空表有效**才算拉到（Python 未定档的拒绝回答
        /// 走不到这里 —— `ContactDuty.RequestSessions` 在写之前就把那种回包拦掉了）。</summary>
        public static bool HeardLoaded => _heardLoaded;

        /// <summary>本地互动时间戳（npc_reply 到达即记，跨面板生命周期）。
        ///
        /// 这是"刚刚有人说了一句"的最快证据（**不需要等 RPC 往返**）—— 但它活在内存里，
        /// 重启/读档即失，所以它只是 `_serverLast` 的**即时补充**，不是唯一依据。
        /// 它**自己触发 Fire**：否则"动作完成直接弹窗"那条路（`ContactDuty.OnUiEvent` 里
        /// 早退、不登记未读）不会走到任何刷新点，「最近」页开着时看不到变化。
        /// </summary>
        public static void MarkLocal(string npc, long unixSeconds)
        {
            if (string.IsNullOrEmpty(npc) || unixSeconds <= 0) return;
            long prev = 0;
            if (_localLast.TryGetValue(npc, out prev) && prev >= unixSeconds) return;   // 幂等/单调
            _localLast[npc] = unixSeconds;
            Fire();
        }

        /// <summary>这个 NPC 有没有**真实互动记录**（`last` 或本地时间戳，任意一个 > 0）。
        ///
        /// ★ 为什么必须有这个判据（10-21 模拟复盘发现，别删）★
        /// 只"按时间戳大小排序"不够：存档会把 `mtime` 刷成**此刻**，而此刻系统性晚于所有人的
        /// 真实说话时刻 ⇒ **从没说过话的老 NPC 反而排到最前面**（原 bug 的镜像形态）。
        /// 所以「最近」排序必须**先分层、再比时间**：
        ///   · 第 1 层 = 有真实记录的（按真实时刻倒序，这才是"最近说过话的"）；
        ///   · 第 2 层 = 只有文件时间的（未知，按 mtime 倒序仅作稳定次序）。
        /// "未知"永远排在"已知"之后 —— 这条不随存档变化，所以对存档完全免疫。
        /// </summary>
        public static bool HasInteraction(string npc)
        {
            if (npc == null) return false;
            long t;
            if (_serverLast.TryGetValue(npc, out t) && t > 0) return true;
            if (_localLast.TryGetValue(npc, out t) && t > 0) return true;
            return false;
        }

        /// <summary>最近互动时间；无记录返回 0。
        ///
        /// ★ 判据（10-21 用户报障后定稿，**不要再改回"取大"**）★
        /// 顺序是 **服务端持久化的 `last` → 本地内存时间戳 → 文件 mtime**：
        ///   · 前两者都是"真的说话了"的证据，取其中**较新**的一个（本地那条可能比服务端回包新，
        ///     因为 `npc_reply` 先到、`list_sessions` 后到）；
        ///   · `mtime` **只在上面两者都没有时**才用 —— 它是"上次存档"的时刻，
        ///     一旦参与"取大"，存档就会把所有 NPC 刷成同一个时间戳（真机：.953/.956 同秒），
        ///     排序当场退化成随机。这正是这次报障的根因。
        ///
        /// ⚠ 光有"mtime 兜底"还不够：兜底出来的值**可能比真实时刻更新**（刚存完档）。
        ///   消费方（`ContactPresenter` 的「最近」排序）必须**先按 `HasInteraction` 分层**
        ///   再比时间，否则从没说过话的老 NPC 会靠"刚存档"跳到最前面。
        /// </summary>
        public static long LastInteract(string npc)
        {
            if (npc == null) return 0;
            long last = 0;
            if (_serverLast.TryGetValue(npc, out var s) && s > last) last = s;
            if (_localLast.TryGetValue(npc, out var l) && l > last) last = l;
            if (last > 0) return last;                       // 有真实互动证据：绝不让 mtime 掺和
            if (_sessionsLast.TryGetValue(npc, out var m) && m > 0) return m;   // 兜底（仅用于展示/稳定次序）
            return 0;
        }
    }
}
