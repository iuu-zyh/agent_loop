/// <summary>
/// ContactDuty —— 通讯录"常驻职责"执行层（**静态、不依赖任何面板实例**）。
///
/// 背景（世界输入失效事故）：通讯录/配置面板改为"按需创建"（方案 A）后，
/// 原先挂在 ContactPresenter（面板实例上的组件）上的常驻职责必须搬到这里：
///   ① 订阅 WsClient.UiEvent：npc_reply 分流（同格→当面弹出对话窗；不同格→未读登记+HUD 红点）
///   ② list_contacts / list_sessions RPC（结果写 ContactStore，由视图层打开时应用）
///   ③ 加/移好友 RPC（NpcPanelAddContact 经此走，不再要求通讯录面板在场）
///
/// 生命周期：ModMain.Init 里 ws 建立后 Attach（幂等；Init 重跑换了 WsClient 实例就换绑，
/// 见 `Attach` 注释）；常驻至游戏退出。
/// 线程约定：UiEvent/RPC 回调均由 WsClient 主线程派发 ✓。
/// </summary>
using System;
using System.Collections.Generic;
using System.Text;
using Newtonsoft.Json.Linq;

namespace AgentLoopBridge
{
    internal static class ContactDuty
    {
        private static WsClient _ws;
        private static bool _attached;

        // ------------------------------------------------------------------
        // 回包判废的基准（10-18「新开存档继承了通讯录」；与 09-20 的 world_known 是一对）
        //
        // 09-20 那次补的 `world_known` 只覆盖"Python **不知道**是哪个存档"（空表 = 拒绝回答）。
        // 它没覆盖**更坏**的一半：Python 知道，但知道的是**另一个**存档 —— 那时回包一切正常
        // （`world_known=true`，名单也是真名单），只是属于上一个存档。一次回包只要跨过了换档
        // 那一瞬（面板刚开过 / 连接正在更替 / 两条 socket 的帧交错），旧档名单就会被写进新档镜像。
        //
        // 判据：**本机最近一次宣告的身份**（`_expectedWorld`） vs **回包自带的身份**
        // （`world_id`，10-18 起 Python 每个档级回包都带）。不匹配 = 这个答案是别的存档的 → 不写。
        // 向后兼容：老 Python 的回包没有这个键 → 无从判废 → 视为匹配（与 `WorldKnown` 缺键即放行同源）。
        // ------------------------------------------------------------------

        /// <summary>本机最近一次**宣告**的世界身份（`ModMain.FillWorldIdentity` 读到就设、
        /// 进世界读不到就置空 = "本机也还没确认是哪个世界"）。空 = 一律不许写镜像（fail-closed）。</summary>
        private static string _expectedWorld = "";

        /// <summary>当前镜像里的名单属于哪个世界（成功写入时记下）。空 = 无主/未知。
        /// 用途：跨档回包迟到时判断"手上这份还可不可信" —— 若它本来就是别的档的，才标注不可信；
        /// 若它已经确认是本档的（新回包先到、旧回包后到），那就静默丢弃旧包，不去动状态条。</summary>
        private static string _mirrorWorld = "";

        private static bool _staleReplyLogged;

        /// <summary>宣告本机当前所在的世界（由 `ModMain.FillWorldIdentity` 调用；`null` = 未确认）。</summary>
        public static void ExpectWorld(string worldId)
        {
            _expectedWorld = worldId ?? "";
        }

        /// <summary>换存档：镜像整表作废（`ContactStore`）+ 清掉两个基准（本机还没宣告新身份）。
        /// 调用点只有一个 —— `ModMain.OnIntoWorldEvent`，且在**重拉名单之前**（顺序即正确性）。</summary>
        public static void ResetForWorld()
        {
            ContactStore.ResetForWorld();
            _mirrorWorld = "";
            _expectedWorld = "";
            _staleReplyLogged = false;
            _worldUnknownLogged = false;
        }

        /// <summary>回包是否属于本机当前确认的世界。`replyWorld` = 应记进镜像的出处
        /// （缺键/老服务端 → 当作本档：无从判废，与旧行为一致）。</summary>
        private static bool WorldMatches(JObject resp, out string replyWorld)
        {
            replyWorld = _expectedWorld;                       // 老 Python：无从判废 → 当作本档
            var tok = resp?["data"]?["world_id"];
            if (tok == null || tok.Type == JTokenType.Null) return true;
            string got = tok.ToString();
            if (got.Length == 0) return false;                 // Python 未定档（正常已被 WorldKnown 拦下）
            if (_expectedWorld.Length == 0) return false;      // 本机尚未确认是哪个世界 → 谁都不许写
            replyWorld = got;
            return got == _expectedWorld;
        }

        /// <summary>丢弃一个跨档回包：**一个字节都不写**，并按"手上这份还可不可信"决定要不要标注。</summary>
        private static void NoteStaleReply()
        {
            // 手上这份若本来就属于别的档（或压根没有）→ 标注不可信，面板如实提示
            // （`_manualLoaded` 保持 false → 显示"加载中…"；标注后显示"尚未取得存档身份"）。
            // 已经确认是本档的（新回包先到、旧回包后到）→ 什么都不动，静默丢弃即可。
            if (_mirrorWorld != _expectedWorld) ContactStore.MarkWorldUnknown();
            if (!_staleReplyLogged)
            {
                _staleReplyLogged = true;
                ModMain.P("[ContactDuty] 丢弃跨档回包：它属于 " + (_mirrorWorld.Length == 0 ? "(未知)" : _mirrorWorld)
                          + "，而本机当前世界是 " + (_expectedWorld.Length == 0 ? "(未确认)" : _expectedWorld)
                          + " —— 不写镜像（换档那一瞬的旧回包，正是「新档显示旧档好友」的入口）");
            }
        }

        /// <summary>Python 连接建立后调用（重复调用安全）。
        ///
        /// ★ 同一实例重复 Attach 直接跳过（幂等）；**实例变了就换绑**（先从旧实例摘订阅再挂新）。
        ///   背景：ModMain.Init 每次进世界 / 读档都会重跑并 new 一个新的 WsClient，初版
        ///   `if (_attached) return;` 会永远订阅在第一个已废弃实例上 —— 本类的 npc_reply
        ///   分流（同格当面弹窗 / 未读红点 / 好友镜像）整场静默失效。2026-09-21 与
        ///   `ChatFrameTee.Attach` 同一批修（实机日志钉死，见那边的注释）。</summary>
        public static void Attach(WsClient ws)
        {
            if (ws == null || (_attached && ws == _ws)) return;
            if (_attached && _ws != null) _ws.UiEvent -= OnUiEvent;   // 换实例：先从旧的摘下来
            _ws = ws;
            _ws.UiEvent += OnUiEvent;
            _attached = true;
            ModMain.P("[ContactDuty] 已附着（常驻职责接管：消息分流/好友镜像/会话索引）");
        }

        // ------------------------------------------------------------------
        // ① npc_reply 分流（原 ContactPresenter.OnUiEvent 的决策部分）
        // ------------------------------------------------------------------

        private static void OnUiEvent(JObject payload)
        {
            string ev = (string)payload?["event"] ?? "";
            if (ev != "npc_reply") return;
            string npc = (string)payload["npc_id"] ?? "";
            if (npc.Length == 0) return;
            long now = DateTimeOffset.UtcNow.ToUnixTimeSeconds();
            ContactStore.MarkLocal(npc, now);   // 本地互动时间戳：所有 npc_reply 都记（跨面板生命周期）

            // ★★ 回合登记表的**唯一**终点（10-17）★★
            //   位置必须在这里，**不能**挪进 `ChatPresenter.OnReplyEvent`：那边有一条 npc 准入
            //   过滤（`npcId != _window.CurrentNpcId → DiagDrop`），玩家把窗口切到别的 NPC 之后，
            //   这个 NPC 的收口帧**根本进不去**（真机日志实锤：
            //   `★帧被丢弃(npc不匹配 payload=姜萌 面板=江宇文) event=npc_reply npc=姜萌`）。
            //   写在那边的话，被切走的那个 NPC 会**永远停在"在跑"**，切回去看到永久忙标。
            //   本方法在一切早退（`!initiative` / `isError` / 各条分流）**之前**无条件看到每一帧
            //   npc_reply，且本类是常驻职责（`ModMain` 附着，与面板实例解耦）。
            //   ⚠ 必须在 `initiative` 判断之前：玩家主动对话（initiative=false）同样要销账。
            try { TurnRegistry.End(npc, "收口帧"); } catch { }

            // NPC 主动开口的收尾分流（README.md 附录二 G（功能设计底稿））：
            // 对话窗开着且就是该 NPC → 直播已读；同格且空闲且没开别的对话窗 → 当面弹出；
            // 其余 → 未读登记（HUD「传」按钮红点；面板打开时行上点亮）。
            bool initiative = payload["initiative"]?.Value<bool>() ?? false;
            bool isError = payload["error"]?.Value<bool>() ?? false;

            // ★ 「临时」页的成员来源（10-24）★
            //   "他说过一句话"这件事必须记下来，否则通讯录里根本没有这个人 —— 因为它既不在
            //   游戏关系数据里（我们的传音对游戏来说没发生过），也不在 contacts.json 里。
            //   位置要求两条：
            //     ① **在 `initiative` 判断之前** —— 玩家自己发起的那一轮同样算"说过话"
            //        （`!initiative` 那条分支下面紧接着就 return 了）；
            //     ② **在 `isError` 判断之后** —— 失败兜底帧（"他这一阵正忙着，没接上你的话"）
            //        不是他说的话，不该让他凭空出现在名单里。
            //   ⚠ 刻意**不动**上面的 `ContactStore.MarkLocal`：那份时间戳的写入点必须留在
            //     "一切早退之前"（「最近」排序靠它，见该方法的注释与 `test_busy_label_contract`）。
            if (!isError) ContactStore.MarkHeard(npc);

            if (!initiative) { FireChanged(); return; }
            // 该 NPC 的自主开口回合到此收口 → 撤销"动作完成自动开窗"的回合标记（ActionWatcher）
            ActionWatcher.ClearInitiativeTurn(npc);
            if (isError) return;   // 失败兜底帧不进未读
            // 隐藏者再次主动传音 ⇒ 重新出现在通讯录（09-26 用户拍板的重现规则，**唯一**出口）。
            // 放在分流之前：无论这一帧最终是"直播已读 / 当面开窗 / 未读登记"，人已经回来了。
            UnhideOnInitiative(npc);
            if (UnreadStore.IsChatOpenFor(npc)) return;

            string text = payload["text"]?.ToString() ?? "";
            var unit = UnitLookup.Resolve(npc);
            bool sameGrid = unit != null && UnitSnapshot.IsSameGrid(unit, g.world.playerUnit);
            bool chatOpenForNpc = UnreadStore.IsChatOpenFor(npc);
            bool anyChatOpen = UnreadStore.AnyChatOpen;
            bool busy = NpcInitiativeMonitor.IsPlayerBusy();
            // 用户拍板(b)：能不能"当面弹出"以**触发那一刻**的判定为准
            // （NpcInitiativeMonitor.RememberTriggerPop 记的"本回合走过确认窗"），
            // 不再用"回复到达这一刻"的同格/忙闲重判一遍——否则异地传音的 NPC 在你等回复的
            // 几十秒里走进同一格，就会变成"我没点同意，怎么就当面开窗了"（22:17 / 22:58 两次实机）。
            //
            // ★ 10-16 语义扩展 ★ 触发时记的不再是"sameGrid && !busy"，而是 **needConsent**
            //   （同格，或异地+动作意图）。异地动作现在也弹确认窗并预开对话窗 ⇒ 它的回复
            //   该走分流①/②，不该被登记成未读（玩家刚点过同意）。
            //   回复时仍要求当前同格（避免"弹当面窗但人已不在跟前"）+ 无窗 + 不忙。
            bool canPopAtTrigger = NpcInitiativeMonitor.CanPopAtTrigger(npc);
            bool canPopNow = canPopAtTrigger && sameGrid && !anyChatOpen && !busy;
            // 分流决策日志：这几条判据此前**完全没日志**，导致
            // "为什么没弹确认窗却直接开窗了" 只能靠推理。现在每次主动开口都留一行现场。
            string route = chatOpenForNpc ? "①直播已读（该 NPC 对话窗开着）"
                         : canPopNow ? "②当面弹出对话窗"
                         : "③未读登记 + 红点/横幅";
            try
            {
                ModMain.P("[ContactDuty] 主动开口分流：" + route +
                          " ｜ npc=" + npc + " 触发时可当面=" + canPopAtTrigger +
                          "（触发时异地/忙 → 事后不补弹）" +
                          " 现在同格=" + sameGrid + " 该npc窗=" + chatOpenForNpc +
                          " 别的窗=" + anyChatOpen + " 忙=" + busy +
                          (busy ? "（" + NpcInitiativeMonitor.BusyReasonText() + "）" : "") +
                          " 正文=" + (text.Length > 30 ? text.Substring(0, 30) + "…" : text));
            }
            catch { }
            if (chatOpenForNpc) return;
            if (canPopNow)
            {
                ChatLauncher.OpenForUnit(unit);   // 当面开口：直接弹出对话 UI（对话窗按需创建 ✓）
                return;
            }

            UnreadStore.Mark(npc, text);
            try { MapMainContactButton.SetUnread(UnreadStore.Any); } catch { }
            // HUD 级横幅提示：异地/忙碌/开着别人的窗这三种"只亮红点"的场合，
            // 给一次显眼的视觉提示。纯提示不可点、不参与输入拾取（见 UnreadBanner）。
            try { UnreadBanner.Show(npc, text); } catch { }
            FireChanged();
        }

        /// <summary>Store 变更通知（视图层订阅：面板开着时重渲）。</summary>
        public static event Action Changed;
        private static void FireChanged() { try { Changed?.Invoke(); } catch { } }

        // ------------------------------------------------------------------
        // ② RPC：手动好友镜像 + 会话索引（结果写 ContactStore）
        // ------------------------------------------------------------------

        /// <summary>拉取手动好友名单（真相在 Python contacts.json）。失败静默保持现状。
        ///
        /// `unit_id` 是**可选键**：本功能上线前加的记录没有它（读作空 → 该好友只能按名解析）。
        /// 解析必须用 `t?["unit_id"]` 容错 —— 老回包里根本没有这个键。
        ///
        /// ★ `world_known`（09-20 实机事故，勿删）★
        /// Python 未取得存档身份时，`list_contacts` 返回**空表**表示"我拒绝回答"，
        /// 而不是"这个存档没有好友"。若把空表当真相，`ContactStore.SetManualNames` 的
        /// 整体替换语义会**清空 C# 手上正确的镜像** ⇒ 用户看到手动好友全部消失、
        /// 只剩关系层那一个人（实证：姜萌），而 `contacts.json` 里四个人好好的。
        /// 故 `world_known=false` 时**一个字都不写**：保持现状，等身份到位后重拉校准
        /// （`OnWsReconnected` / `OnIntoWorldEvent` 都会重拉）。
        /// 老 DLL 收到未知键只是忽略；新 DLL 收到老 Python 的回包（无此键）→ 视为 true
        /// （缺键 = 老服务端，其语义就是"没这道闸"，照旧处理）。
        /// </summary>
        public static void RequestManualContacts()
        {
            if (_ws == null) return;
            _ws.SendRequest("list_contacts", new JObject(), resp =>
            {
                if (resp["ok"]?.Value<bool>() != true) return;
                if (!WorldKnown(resp)) return;      // 未定档：空表是"拒绝回答"，不许当真相
                string rw;
                if (!WorldMatches(resp, out rw)) { NoteStaleReply(); return; }   // 跨档回包：不许写
                var arr = resp["data"]?["contacts"] as JArray;
                var items = new List<KeyValuePair<string, string>>();
                if (arr != null)
                {
                    foreach (var t in arr)
                    {
                        string npc = t?["npc_id"]?.ToString();
                        if (string.IsNullOrEmpty(npc)) continue;
                        string uid = t?["unit_id"]?.ToString() ?? "";   // 老记录没有此键 → ""
                        items.Add(new KeyValuePair<string, string>(npc, uid));
                    }
                }
                ContactStore.SetManualNames(items);
                _mirrorWorld = rw;                  // 记下这份镜像的出处（跨档回包判废要用）
                _staleReplyLogged = false;          // 恢复了：允许下次再提示一次
            }, 8000);
        }

        /// <summary>拉取会话索引（`mtime` = 上次存档时刻 / `last` = 最后一次真说话时刻）。
        /// `world_known=false` 时同样**不写**——空索引会把「最近」页清空（与名单同源同因）。
        ///
        /// ★ 两个字段都要收（10-21）★ 只收 `mtime` 正是「主动传音后『最近』位置不动」的根因：
        /// 存档把十几个文件刷成同一秒（真机 .953/.956），排序全相等。
        /// `last` 缺失 = 老 Python / 这个 NPC 没说过话 → 0，由 `ContactStore.LastInteract` 回落。</summary>
        public static void RequestSessions()
        {
            if (_ws == null) return;
            _ws.SendRequest("list_sessions", new JObject(), resp =>
            {
                if (resp["ok"]?.Value<bool>() != true) return;
                if (!WorldKnown(resp)) return;
                string rw;
                if (!WorldMatches(resp, out rw)) { NoteStaleReply(); return; }
                var arr = resp["data"]?["sessions"] as JArray;
                var mtimes = new List<KeyValuePair<string, long>>();
                var lasts = new List<KeyValuePair<string, long>>();
                if (arr != null)
                {
                    foreach (var t in arr)
                    {
                        string npc = t?["npc_id"]?.ToString();
                        if (string.IsNullOrEmpty(npc)) continue;
                        long m = t?["mtime"]?.Value<long>() ?? 0;
                        if (m > 0) mtimes.Add(new KeyValuePair<string, long>(npc, m));
                        // `last` 缺键（老 Python）→ 0 → 不进表（`SetSessionIndex` 只收 >0）
                        long l = t?["last"]?.Value<long>() ?? 0;
                        if (l > 0) lasts.Add(new KeyValuePair<string, long>(npc, l));
                    }
                }
                ContactStore.SetSessionIndex(mtimes, lasts);
            }, 8000);
        }

        // ------------------------------------------------------------------
        // ②b 关系层快照（known.json）——「死者不消失」的唯一依据
        // ------------------------------------------------------------------
        //
        // 为什么需要它：关系层是**每次渲染现取**游戏数据的，而死者 `GetUnit(id)` 恒 null
        // （三条采集路都 `if (u == null) return`）⇒ 人一死就从名单里静默消失。
        // 死后也反推不出"他来过"：`ClearDieUnit` 清了关系簿、死者快照的
        // `GetRelation(玩家)` 返回 None（真机确认）。
        // 所以只能在**活着且握着真身**的那一刻留一份，死后据 unit_id 判"已故"并把行补回来。
        //
        // 写侧（`SyncKnownContacts`）由 `ContactPresenter.BuildRelations` 与存档钩子驱动；
        // 读侧（`RequestKnownContacts`）在进世界 / 重连 / 开面板时各拉一次（与手动名单同刻）。

        /// <summary>拉取关系层快照（`list_known_contacts`）。同 `list_contacts`：
        /// `world_known=false`（未定档）时**一个字都不写**——空表是"拒绝回答"，不是"没有"。</summary>
        public static void RequestKnownContacts()
        {
            if (_ws == null) return;
            _ws.SendRequest("list_known_contacts", new JObject(), resp =>
            {
                if (resp["ok"]?.Value<bool>() != true) return;
                if (!WorldKnown(resp)) return;
                string rw;
                if (!WorldMatches(resp, out rw)) { NoteStaleReply(); return; }
                var arr = resp["data"]?["known"] as JArray;
                var items = new List<KnownContact>();
                if (arr != null)
                {
                    foreach (var t in arr)
                    {
                        string npc = t?["npc_id"]?.ToString();
                        if (string.IsNullOrEmpty(npc)) continue;
                        items.Add(new KnownContact
                        {
                            Name = npc,
                            UnitId = t?["unit_id"]?.ToString() ?? "",
                            Relation = t?["relation"]?.ToString() ?? "",
                        });
                    }
                }
                ContactStore.SetKnown(items);
            }, 8000);
        }

        /// <summary>把此刻看到的关系层推给 Python（`sync_known_contacts`，幂等 upsert）。
        ///
        /// 内容签名没变就**一个字节都不发**：面板每次打开都会重建一次关系层，而它是
        /// 几十上百条 —— 逐次上行既没必要，也会让日志/抓包看不出"到底哪次真变了"。
        /// 失败清签名，下次开面板自然重试（不做退避轮询，符合本工程的"事件驱动"约定）。</summary>
        public static void SyncKnownContacts(List<KnownContact> items)
        {
            if (_ws == null || items == null) return;
            var sorted = new List<KnownContact>(items);
            sorted.Sort((a, b) => string.CompareOrdinal(a.Name ?? "", b.Name ?? ""));
            var sig = new StringBuilder();
            foreach (var it in sorted)
                sig.Append(it.Name).Append('\u0001').Append(it.UnitId ?? "").Append('\u0001')
                   .Append(it.Relation ?? "").Append('\u0002');
            string s = sig.ToString();
            if (s == _knownSyncSig) return;
            _knownSyncSig = s;

            var arr = new JArray();
            foreach (var it in sorted)
            {
                var o = new JObject { ["npc_id"] = it.Name };
                if (!string.IsNullOrEmpty(it.UnitId)) o["unit_id"] = it.UnitId;
                if (!string.IsNullOrEmpty(it.Relation)) o["relation"] = it.Relation;
                arr.Add(o);
            }
            _ws.SendRequest("sync_known_contacts", new JObject { ["items"] = arr }, resp =>
            {
                if (resp["ok"]?.Value<bool>() != true)
                {
                    _knownSyncSig = null;      // 失败：下次开面板自然重试
                    ModMain.P("[ContactDuty] 通讯录快照同步失败：" + (resp["error"]?.ToString() ?? "?"));
                    return;
                }
                var d = resp["data"];
                ModMain.P("[ContactDuty] 通讯录快照同步：" + arr.Count + " 条 → 新增 "
                          + (d?["added"]?.ToString() ?? "?") + " / 更新 "
                          + (d?["updated"]?.ToString() ?? "?") + " / 共 "
                          + (d?["total"]?.ToString() ?? "?"));
                // 首次进游戏时镜像还没拉到（面板打开顺序不定）→ 补拉一次，否则这一轮的
                // "已故行"要等到下次开面板才出现。已拉到就不重复拉。
                if (!ContactStore.KnownLoaded) RequestKnownContacts();
            }, 8000);
        }

        private static string _knownSyncSig;

        /// <summary>回包是否来自"已定档"的服务端。缺键 = 老 Python（没这道闸）→ 视为 true。
        /// 显式 false = Python 明确说"我还不知道这是哪个存档"，其空表是拒绝回答。</summary>
        private static bool WorldKnown(JObject resp)
        {
            var tok = resp?["data"]?["world_known"];
            if (tok == null || tok.Type == JTokenType.Null) return true;   // 老服务端：无此闸
            bool known = tok.Value<bool>();
            if (!known)
            {
                // 只标注"镜像不可信"，**不碰数据**（事故根因就是"空表被当成真相去清镜像"）
                ContactStore.MarkWorldUnknown();
                if (!_worldUnknownLogged)
                {
                    _worldUnknownLogged = true;
                    ModMain.P("[ContactDuty] Python 尚未取得存档身份 → 本次名单/索引回包为"
                              + "「拒绝回答」（空表），**保持现有镜像不动**；身份到位后会自动重拉校准");
                }
            }
            else
            {
                _worldUnknownLogged = false;    // 恢复定档：允许下次再提示一次
            }
            return known;
        }

        /// <summary>「未定档」提示只打一次（`list_contacts` 是面板轮询级频率，逐次刷屏没意义）。</summary>
        private static bool _worldUnknownLogged;

        // ------------------------------------------------------------------
        // ③ 加/移好友（NpcPanelAddContact 入口；不再要求通讯录面板在场）
        // ------------------------------------------------------------------

        /// <summary>本地镜像即时翻转（按钮状态秒变），真相随后由 list_contacts 回包校准。
        /// `unitId` 非空 = 调用方握着真身（见 `ContactStore.SetManualContact` 的铁律）。</summary>
        public static void SetManualContact(string name, bool add, string unitId = null)
        {
            ContactStore.SetManualContact(name, add, unitId);
        }

        /// <summary>加/移好友 RPC（add/remove_contact），回包后重拉名单校准镜像。
        /// `unitId` 非空时随请求上行（落进 contacts.json 的精确身份）。</summary>
        public static void ToggleManualContact(string name, bool add, string unitId = null)
        {
            if (_ws == null || string.IsNullOrEmpty(name)) return;
            var req = new JObject { ["npc_id"] = name };
            // 知道才塞：不塞空串 —— 空串在 Python 侧同样被当"没提供"，但发一个明确的
            // "unit_id":"" 会让抓包/日志看起来像"我们知道但值是空的"，是误导。
            if (!string.IsNullOrEmpty(unitId)) req["unit_id"] = unitId;
            _ws.SendRequest(add ? "add_contact" : "remove_contact", req, resp =>
            {
                RequestManualContacts();   // 回包校准镜像（Store 变更 → 视图自动刷新）
            }, 8000);
        }

        /// <summary>回填 / 纠正某手动好友的精确 unitID（`add_contact` 幂等语义里的"补空 + 纠错"）。
        ///
        /// 用途：本功能上线前加的记录（`contacts.json` 里没有 `unit_id`）在玩家某次"手里握着
        /// 真身"的时刻自愈，从此不必按名全图猜。
        ///
        /// ★★ 硬约束：**只有"调用方此刻真的握着那个 `WorldUnitBase`"时才许调**。
        ///    按名解析（`UnitLookup.Resolve`）出来的结果**永远不许**走这条路 —— 那是把一次
        ///    猜测写进磁盘、跨会话生效，而且猜错**不报错、不缺字段**，只是从此把别人的档案
        ///    端给模型（`UnitLookup` 文件头记的那次真机事故；`Pin` 注释原话"比不登记更糟"）。
        ///
        /// 调用点（09-20 更正）：`ChatLauncher.OpenForUnit` 经 `BackfillManualIdentity`
        /// 调用 —— 那是全工程唯一"拿着 `WorldUnitBase` 去开对话窗"的入口（NPC 面板按钮 /
        /// 通讯录行点击 / 剧情窗按钮全汇到它），且**只在 `guessed == false` 时**调，
        /// 满足上面那条硬约束。
        ///
        /// ⚠ 本行原文曾是"第 1 步只提供它，**不接任何调用点**（接线与 UI 改动一起做）"。
        /// 那句话早已过时（接线后来做了，注释没跟着改），却足以把人骗过去 —— 09-20 排查
        /// "濮阳彤萱为何没有 unit_id"时我就据此误判成"通道从未接线"。**注释陈旧 = 假证据**，
        /// 与 `ContactPanelOpener` 里那句骗过一次排障的"F10 本次不响应"同源。勿再照抄旧结论。
        ///
        /// 失败静默（回填是尽力而为，坏了不该影响任何主流程）。</summary>
        public static void BackfillManualUnitId(string name, string unitId)
        {
            if (_ws == null || string.IsNullOrEmpty(name) || string.IsNullOrEmpty(unitId)) return;
            var req = new JObject { ["npc_id"] = name, ["unit_id"] = unitId };
            _ws.SendRequest("add_contact", req, resp =>
            {
                ModMain.P("[ContactDuty] 回填手动好友精确身份：" + name + " → " + unitId
                          + " ok=" + resp?["ok"]);
                RequestManualContacts();
            }, 8000);
        }

        // ------------------------------------------------------------------
        // ④ 隐藏名单（通讯录行内 ✕ 的"关系层隐藏"侧）—— 09-26 用户拍板
        //
        // 语义：某 NPC 被隐藏后不再出现在通讯录（**关系数据一点不动**）；
        // 重现出口只有一个 —— 他再次主动传音（`UnhideOnInitiative`）。
        // 好友优先：重新加为好友时渲染过滤直接放行（见 `ContactPresenter.ExcludeHidden`）。
        // ------------------------------------------------------------------

        /// <summary>拉取隐藏名单（`list_hidden`）。未定档（`world_known=false`）→ **不写镜像**
        /// —— 同 `RequestManualContacts` 的事故教训：空表是"拒绝回答"，不是"没有"。</summary>
        public static void RequestHidden()
        {
            if (_ws == null) return;
            _ws.SendRequest("list_hidden", new JObject(), resp =>
            {
                if (resp["ok"]?.Value<bool>() != true) return;
                if (!WorldKnown(resp)) return;
                string rw;
                if (!WorldMatches(resp, out rw)) { NoteStaleReply(); return; }
                var arr = resp["data"]?["hidden"] as JArray;
                var names = new List<string>();
                if (arr != null)
                {
                    foreach (var t in arr)
                    {
                        string npc = t?["npc_id"]?.ToString();
                        if (!string.IsNullOrEmpty(npc)) names.Add(npc);
                    }
                }
                ContactStore.SetHiddenNames(names);
            }, 8000);
        }

        /// <summary>写一条隐藏/取消隐藏（`hide_contact` / `unhide_contact`）。
        /// 本地镜像**即时翻转**（列表秒变），真相由回包后的 `list_hidden` 校准。</summary>
        public static void SetHidden(string name, bool hide, string unitId = null)
        {
            if (_ws == null || string.IsNullOrEmpty(name)) return;
            ContactStore.SetHidden(name, hide);
            SetHiddenRpc(name, hide, unitId);
        }

        /// <summary>只发 RPC（本地已改或不需要改时用：主动传音取消隐藏走这条）。</summary>
        private static void SetHiddenRpc(string name, bool hide, string unitId = null)
        {
            if (_ws == null || string.IsNullOrEmpty(name)) return;
            var req = new JObject { ["npc_id"] = name };
            if (!string.IsNullOrEmpty(unitId)) req["unit_id"] = unitId;
            _ws.SendRequest(hide ? "hide_contact" : "unhide_contact", req, resp =>
            {
                RequestHidden();   // 回包校准镜像（失败的写入会被这次重拉抹掉，不会假装成功）
            }, 8000);
        }

        /// <summary>主动传音到达 → 若在隐藏名单里则取消隐藏（本地即时 + RPC 落地）。
        /// 这是隐藏的**唯一**重现出口；失败静默（他下次再来还会再试一次）。</summary>
        private static void UnhideOnInitiative(string npc)
        {
            try
            {
                if (!ContactStore.IsHidden(npc)) return;
                ContactStore.SetHidden(npc, false);
                ModMain.P("[ContactDuty] 主动传音取消隐藏：" + npc + "（重新出现在通讯录）");
                SetHiddenRpc(npc, false);
            }
            catch { }
        }
    }
}
