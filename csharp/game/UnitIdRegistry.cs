/// <summary>
/// UnitIdRegistry —— 「名字 → unitID」的**全局登记表**（进程内内存 + 一个落盘文件）。
///
/// ── 它是什么，不是什么 ──────────────────────────────────────────────────────
/// **是**：一份"我曾经**手里握着真身**、因此确知这个名字对应哪个 unitID"的记录。
/// **不是**：按名解析的结果缓存。**按名查出来的结果永远不许写进来** —— 那是把一次猜测
/// 固化到磁盘、跨会话生效，而且再也看不出它是猜的（`UnitLookup.Pin` 的原注释同款理由）。
/// 这条纪律由**写入点只有两个**来保证（见 `Remember` 的 docstring），不靠调用方自觉。
///
/// ── 它取代了什么 ────────────────────────────────────────────────────────────
/// `UnitLookup._pinned`（进程内、只在本次游戏会话有效）。两者的**写入时刻完全相同**
/// （打开对话 UI 的那一刻），区别只有两点：
///   ① 本表**落盘** ⇒ 重开游戏仍然精确，而 `_pinned` 一重启就没；
///   ② 本表**多一个写入点**（加好友），覆盖面比 `_pinned` 大。
/// 所以 `_pinned` 被整体删除，`ResolveInner` 的 ⓪ 分支改为查本表。
///
/// ── 为什么必须按存档隔离 ────────────────────────────────────────────────────
/// unitID **跨存档会整体失效**（同一个中文名在新档里是另一个串）。若把两个档的登记混在
/// 一起，最坏情形不是"查不到"（那会自愈）而是**那个 id 在新档里恰好是另一个人** ——
/// 静默错人，正是本工程最怕的失败形态。
/// 隔离手段：文件里记 `world`（= 玩家 unitID，与 Python 的 `worlds/&lt;world_id&gt;/` 同源），
/// 加载时**对不上就整表作废**。于是不需要把文件放到每个存档的目录里，也就不需要
/// C# 知道 `~/.sessions` 在哪 —— 一份文件、一个字段，就把跨档污染堵死了。
///
/// ── 落盘时机：**只跟游戏存档**（09-17 用户拍板） ─────────────────────────────
/// `Remember` / `Forget` 只改内存并置 `_pending`，**一个字节都不写**；真正写盘只有
/// `Flush()`，而它的唯一调用点是 `ModMain.OnSaveDataEvent`（手动/自动存档都触发）。
/// 为什么不每次更新就写（原实现是幂等 + 原子写）：
///   ① 本表与 Python 侧的对话增量**是同一次握手产出的两份数据**，寿命语义必须一致
///      （存档=固化，不存档=丢弃）。只固化其中一份，就会出现"NPC 不记得这次对话，
///      但登记表里留着这一局的 unitID"这种半状态，事后无法归因；
///   ② 登记点都落在主线程 UI 事件里（点开对话 / 加好友），而写盘要走 `File.Replace` ——
///      挪到存档时，代价与"玩家点了多少次"彻底解耦（幂等只省掉重复写，省不掉这一次）；
///   ③ 白捡一条：切到别的存档、又没在那边登记过任何人时 `_pending` 为假 ⇒ `Flush`
///      直接返回，**别人的表不会被这一档覆盖**。
/// 由此得到一条**不变式**：待写内容必然属于当前存档 —— 换档时 `ResetForWorld` 把内存表
/// 与 `_pending` 一起丢掉，所以 `Save()` 里"world 取不到就不写"的兜底不会被跨档内容钻空子。
///
/// ── 谁写、谁读 ──────────────────────────────────────────────────────────────
/// **C# 独家写**（Python 从不碰这个文件，它有自己的 `contacts.json` / `rooms/*`）。
/// 消费方只有两处：`UnitLookup.ResolveInner` 的 ⓪ 分支、以及工具层的"id 不行就回落"分支。
/// 读失败 / 文件损坏 / 键缺失一律当"空表"（降级成按名解析，行为与本功能上线前一致），
/// **绝不抛**。
///
/// 线程：全部方法只在主线程调用（与 `UnitLookup` 同约束）。
/// </summary>
using System;
using System.Collections.Generic;
using System.IO;
using Newtonsoft.Json.Linq;

namespace AgentLoopBridge
{
    internal static class UnitIdRegistry
    {
        private const string FileName = "unit_ids.json";

        /// <summary>两张表 + 跨存档校验的**纯逻辑**（零 Unity / 零 Newtonsoft，可单测）。
        /// 本文件只负责：找路径、读写文件、拿当前存档 id、打日志 —— **一行判断都不做**
        /// （判断全在核心里，那样才测得动，见 `UnitIdRegistryCore` 的文件头）。</summary>
        private static readonly UnitIdRegistryCore _core = new UnitIdRegistryCore();

        private static bool _loaded;
        private static bool _foreign;        // 文件属于**别的存档**（加载被拒）⇒ 下次落盘整表覆盖它
        private static bool _pending;        // 内存里有未落盘的改动（只在**游戏存档**时写，见文件头）
        private static string _dir;          // 惰性解析（照 PortraitCache.Dir 的做法）

        // ---------- 读 ----------

        /// <summary>这个名字登记过的 unitID；**没登记返回空串**（不是 null、不是占位）。</summary>
        internal static string IdOf(string name)
        {
            EnsureLoaded();
            return _core.IdOf(name);
        }

        /// <summary>这个 unitID 登记过的名字；**没登记返回空串**。
        ///
        /// 为什么要反查：工具层的回落分支是「id 不行 → 拿中文名去操作」，而 **Python 送上来的是
        /// 中文名、模型送上来的是中文名**，两边都不需要它。真正需要的是**模型把 unitID
        /// 误填进 `target` 的场合** —— 有了反查就能把这个 id 翻回名字、给出"你填错字段了"
        /// 这种能自纠的提示，而不是一句"未找到 Xs6JDI"。</summary>
        internal static string NameOf(string unitId)
        {
            EnsureLoaded();
            return _core.NameOf(unitId);
        }

        internal static int Count { get { EnsureLoaded(); return _core.Count; } }

        // ---------- 写（**只有两个合法调用点**） ----------

        /// <summary>
        /// 登记一条 `名字 → unitID`。**幂等**：值没变则一个字节都不写。
        ///
        /// ★ **调用方必须真的握着那个 `WorldUnitBase`**（不是按名查出来的）。合法调用点
        ///   **只有两个**，与用户拍板的口径一致：
        ///     ① `ChatLauncher.OpenForUnit` 的 `!guessed` 分支（点「AI 对话」）；
        ///     ② `NpcPanelAddContact.ToggleContact`（点「加好友」）—— 那里 `panel.unit` 就是本人。
        ///   在别处调它（尤其是 `ContactPresenter.ResolveEntry` 那种按名兜底之后）就是
        ///   把猜测固化下来，比不登记更糟。
        ///
        /// `why` 只进日志（诊断"这条是谁写进来的"），不落盘。
        /// </summary>
        internal static void Remember(string name, string unitId, string why)
        {
            EnsureLoaded();
            string nm = (name ?? "").Trim();
            string before = _core.IdOf(nm);
            if (!_core.Remember(name, unitId)) return;     // 幂等 / 空参：一个字节都不动
            _pending = true;                               // ★ 只置位、不落盘（写盘时机＝游戏存档）
            if (before.Length > 0)
                // 覆盖已有一条**不同**的登记：同一个名字在一个存档里出现两个 unitID 是可疑事件，
                // 如实打出来，别让它悄悄过去（"玩家刚点开的这个"通常更可信，故保留新值）。
                ModMain.P("[UnitIdRegistry] ★覆盖登记：'" + nm + "' " + before + " → " + unitId.Trim()
                          + "（" + (why ?? "") + "）—— 同一个名字登记了两个 unitID，"
                          + "若这不是换存档，说明重名者被先后点开过；后续按名解析以**最后这次**为准；"
                          + "存档时落盘）");
            else
                ModMain.P("[UnitIdRegistry] 登记：'" + nm + "' → " + unitId.Trim() + "（" + (why ?? "") + "；"
                          + "手中有真身，后续按名解析优先用它；存档时落盘）");
        }

        /// <summary>自愈：登记失效（换存档后那个 id 已不是他、或单位消失）时摘掉。返回是否真的摘了。
        /// `unitId` 非空时**必须与当前值相等才摘** —— 否则会把刚登记的正确值误删。</summary>
        internal static bool Forget(string name, string unitId)
        {
            EnsureLoaded();
            if (!_core.Forget(name, unitId)) return false;
            _pending = true;                                // 同 `Remember`：只在存档时落盘
            return true;
        }

        // ---------- 查询结果的顺带登记（09-17） ----------

        /// <summary>
        /// **查询结果的顺带登记**：模型查人 / 查关系时，C# 手里本来就握着那些 `WorldUnitBase`
        /// （`{name, unit_id}` 是从对象上读的，不是按名猜的），顺手记下来能让**后续**的按名解析
        /// （自身段 / 立绘 / 工具兜底）直接精确命中 —— 此前查询结果一律不登记，
        /// 于是"模型查到的人"下一次仍要靠全图按名扫。
        ///
        /// 三条纪律，每条都对应一种静默错误：
        ///   ① **绝不覆盖**已有登记（`RememberIfAbsent`）—— 查询是"路过看见"，
        ///      压掉一次"手握真身"的登记，就可能把名字钉到另一个人身上；
        ///   ② **一次载荷里同名多值 ⇒ 这个名字整组不登记**（那是重名，登记谁都是错的）。
        ///      这一条**顺带覆盖了 `inspect_unit` 的同名歧义**：那次载荷里被查者与候选同名，
        ///      于是整组自动出局 —— 不需要额外判 `ambiguous`；
        ///   ③ 空名 / 空 id 忽略（`{name, unit_id}` 只有一半不算"一个人的引用"）。
        ///
        /// 深度收集而不是逐个工具写死：`{name, unit_id}` 是全工程"人的引用"的统一形状
        /// （`UnitSnapshot.UnitNames` / `IntimBookNames` / `married`、`search_units` 的行、
        /// `inspect_unit` 的档案头与候选块），一处收敛就不会漏掉将来新增的块。
        /// </summary>
        internal static int RememberObservedFromPayload(JToken data, string why)
        {
            if (data == null) return 0;
            var pairs = new List<KeyValuePair<string, string>>();
            CollectRefs(data, pairs);
            if (pairs.Count == 0) return 0;
            var seen = new Dictionary<string, string>();
            var dup = new HashSet<string>();
            foreach (var kv in pairs)
            {
                string cur;
                if (seen.TryGetValue(kv.Key, out cur))
                {
                    if (cur != kv.Value) dup.Add(kv.Key);   // 同名两个人 ⇒ 这个名字作废
                }
                else seen[kv.Key] = kv.Value;
            }
            EnsureLoaded();
            int n = 0;
            foreach (var kv in seen)
            {
                if (dup.Contains(kv.Key)) continue;
                if (_core.RememberIfAbsent(kv.Key, kv.Value)) { _pending = true; n++; }
            }
            ModMain.P("[UnitIdRegistry] 查询顺带登记 " + n + " 条（" + (why ?? "") + "；"
                      + pairs.Count + " 条引用，去重后 " + seen.Count
                      + (dup.Count > 0 ? "，其中 " + dup.Count + " 个名字因重名跳过" : "")
                      + "；存档时落盘）");
            return n;
        }

        /// <summary>深度收集 `{name, unit_id}` 对（"一个人的引用"的统一形状，见 `UnitSnapshot`）。</summary>
        private static void CollectRefs(JToken t, List<KeyValuePair<string, string>> outPairs)
        {
            if (t == null) return;
            var o = t as JObject;
            if (o != null)
            {
                var nm = o["name"];
                var id = o["unit_id"];
                if (nm != null && id != null
                    && nm.Type != JTokenType.Object && nm.Type != JTokenType.Array
                    && id.Type != JTokenType.Object && id.Type != JTokenType.Array)
                {
                    string n = (nm.ToString() ?? "").Trim();
                    string u = (id.ToString() ?? "").Trim();
                    if (n.Length > 0 && u.Length > 0)
                        outPairs.Add(new KeyValuePair<string, string>(n, u));
                }
                foreach (var kv in o) CollectRefs(kv.Value, outPairs);
                return;
            }
            var a = t as JArray;
            if (a != null) foreach (var x in a) CollectRefs(x, outPairs);
        }

        // ---------- 换存档 ----------

        /// <summary>
        /// 换存档（`ModMain.OnIntoWorldEvent`）时调用：**整表清空并重载**。
        ///
        /// 为什么不能只清内存就完事：`world` 对不上时加载逻辑**本来就会作废**（见文件头），
        /// 所以"重新加载"是安全的；但**必须把 `_loaded` 打回 false**，否则新档会继续用
        /// 旧档那份内存表 —— 那就正好绕过了那道防线。
        /// 与 `PortraitCache.ClearNameIndex` / `ChatFrameQueue.ClearAll` 并列，同一个钩子。
        ///
        /// 还有 `_pending`：它记的是"内存里有未落盘的改动"，而**那份改动属于刚离开的那个存档**。
        /// 不清就会在下一次存档时把它写进新档 —— 正是本表最怕的静默错人
        /// （与"不存档=丢弃"的存档语义也自相矛盾）。
        /// </summary>
        internal static void ResetForWorld()
        {
            _core.Reset();
            _loaded = false;
            _foreign = false;
            _pending = false;      // ★ 未落盘的登记随档作废
            ModMain.P("[UnitIdRegistry] 换存档：登记表已清空（未落盘的登记一并作废；"
                      + "下一次使用时按新 world 重载）");
        }

        // ---------- 落盘 ----------

        private static string Dir()
        {
            if (_dir != null) return _dir;
            try
            {
                string root = ModAbRes.ResolveRoot();
                if (!string.IsNullOrEmpty(root)) _dir = root;
            }
            catch { }
            // 兜底与 PortraitCache 同款：Mod 根解析不到时退到游戏可写目录（至少不写死盘符）
            if (string.IsNullOrEmpty(_dir))
            {
                try { _dir = UnityEngine.Application.persistentDataPath; } catch { }
            }
            return _dir;
        }

        private static string FilePath()
        {
            string d = Dir();
            return string.IsNullOrEmpty(d) ? null : Path.Combine(d, FileName);
        }

        /// <summary>当前存档 id（= 玩家 unitID）。取不到返回空串（此时**不写盘** —— 宁可这次不登记，
        /// 也不能把一条无法判定归属的记录写进文件让下一个存档去踩）。</summary>
        private static string WorldNow()
        {
            try { return UnitSnapshot.PlayerUnitId() ?? ""; } catch { return ""; }
        }

        private static void EnsureLoaded()
        {
            if (_loaded) return;
            _loaded = true;                 // 先置位：加载途中绝不重入
            string path = FilePath();
            if (string.IsNullOrEmpty(path)) return;
            try
            {
                if (!File.Exists(path)) return;
                string txt = File.ReadAllText(path);
                if (string.IsNullOrEmpty(txt)) return;
                var o = JObject.Parse(txt);
                string fileWorld = (string)o["world"] ?? "";
                string nowWorld = WorldNow();
                var units = new Dictionary<string, string>();
                var uo = o["units"] as JObject;
                if (uo != null)
                {
                    foreach (var kv in uo)
                    {
                        string nm = (kv.Key ?? "").Trim();
                        string id = (kv.Value == null ? "" : kv.Value.ToString()).Trim();
                        if (nm.Length > 0 && id.Length > 0) units[nm] = id;
                    }
                }
                // ★ 跨档防线在核心里（`Load` 的 world 校验，单独可测）。
                //   **不抛、不删文件** —— 删了会丢掉那个存档的登记（用户切回去就没了）；
                //   留着，等切回去时它又对得上了。
                if (!_core.Load(fileWorld, units, nowWorld))
                {
                    _foreign = true;
                    ModMain.P("[UnitIdRegistry] 文件里的存档对不上（文件=" + (fileWorld.Length == 0 ? "(空)" : fileWorld)
                              + " 当前=" + (nowWorld.Length == 0 ? "(未进档)" : nowWorld)
                              + "），本次作废整表，回落按名解析");
                    return;
                }
                ModMain.P("[UnitIdRegistry] 已加载 " + _core.Count + " 条（world=" + nowWorld + "）");
            }
            catch (Exception e)
            {
                // 损坏 / 半截文件一律当空表：登记表是**加速器**，不是正确性依赖，绝不能拖垮解析
                _core.Reset();
                ModMain.P("[UnitIdRegistry] 加载失败（已当空表）：" + e.Message);
            }
        }

        /// <summary>
        /// 把内存表落盘。**唯一调用点是 `ModMain.OnSaveDataEvent`**（手动/自动存档都触发），
        /// 与 Python 侧 `save_happened → flush_all` 同一个钩子、同一个语义（见文件头「落盘时机」）。
        ///
        /// 没有待写内容时**直接返回**：不加载、不写。这条不只是省事 —— 它保证了
        /// "本档没登记过任何人 ⇒ 磁盘上别的存档那份表原样不动"。
        /// </summary>
        internal static void Flush()
        {
            if (!_pending) return;
            if (Save()) _pending = false;   // 写失败 ⇒ 保留 `_pending`，下一次存档再试
        }

        /// <summary>原子落盘：写临时文件 → 替换。返回**是否写完**（false ⇒ 调用方保留 `_pending`）。
        ///
        /// 为什么必须原子：这份表是**跨会话的真相**，半截 JSON 会让下一个存档加载失败
        /// （虽然有"当空表"兜底，但那就等于白登记了）。同目录内的 Move 在 NTFS 上是原子的。
        /// 失败只记日志 —— 写不进去最多是这次不生效，**不影响本次会话**（内存里已经更新了）。
        /// 成功时打一行日志：真机验收就靠它确认"存档那一刻真的写了"。
        /// </summary>
        private static bool Save()
        {
            string path = FilePath();
            if (string.IsNullOrEmpty(path))
            {
                ModMain.P("[UnitIdRegistry] 存档：Mod 根解析不到，登记表未能落盘");
                return false;
            }
            string world = WorldNow();
            // 取不到 world ⇒ 不写。写下去就是一条"归属不明"的记录，下一个存档要踩。
            if (world.Length == 0)
            {
                ModMain.P("[UnitIdRegistry] 存档：取不到当前存档 id，登记表未落盘"
                          + "（避免写出一条归属不明的记录）");
                return false;
            }
            // 表是空的、且核心也没接受过任何 world ⇒ 没有属于本档的内容可写：当"写完"处理
            // （让 `Flush` 清掉 `_pending`），**绝不去动**那个属于别的存档的文件（别凭空造个空表覆盖它）。
            if (_core.Count == 0 && _core.World.Length == 0) return true;
            try
            {
                var units = new JObject();
                foreach (var kv in _core.Snapshot()) units[kv.Key] = kv.Value;
                var o = new JObject
                {
                    ["version"] = UnitIdRegistryCore.Version,
                    // ★ 写**核心已接受的那个 world**（不是现查的）：两者在 `Load` 之后一致，
                    //   而用核心的值能把"加载被拒 ⇒ 表属于别的存档"这种情形**排除掉** ——
                    //   那种情况下一写就把别的存档的表覆盖成当前档的空表了。
                    ["world"] = _core.World.Length > 0 ? _core.World : world,
                    ["units"] = units,
                };
                string tmp = path + ".tmp";
                File.WriteAllText(tmp, o.ToString(Newtonsoft.Json.Formatting.Indented));
                if (File.Exists(path))
                {
                    try { File.Replace(tmp, path, null); }
                    catch { File.Delete(path); File.Move(tmp, path); }   // Replace 不支持时退回（同目录 Move 仍原子）
                }
                else File.Move(tmp, path);
                _foreign = false;      // 这一份写完，文件就是本档的了
                ModMain.P("[UnitIdRegistry] 存档：已落盘 " + _core.Count + " 条（world="
                          + (_core.World.Length > 0 ? _core.World : world) + "）");
                return true;
            }
            catch (Exception e)
            {
                ModMain.P("[UnitIdRegistry] 写盘失败（内存表已更新，本次会话仍生效；下次存档再试）：" + e.Message);
                return false;
            }
        }

        // ---------- 诊断 ----------

        internal static string Report()
        {
            EnsureLoaded();
            string path = FilePath();
            return "登记 " + _core.Count + " 条 ｜ world=" + (_core.World.Length > 0 ? _core.World : "(未进档)")
                 + (_pending ? " ｜ ★待落盘（存档时写入）" : "")
                 + (_foreign ? " ｜ 文件属于别的存档（下次存档时覆盖）" : "")
                 + " ｜ 文件=" + (path ?? "(无法解析 Mod 根)");
        }
    }
}
