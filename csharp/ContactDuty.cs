/// <summary>
/// ContactDuty —— 通讯录"常驻职责"执行层（**静态、不依赖任何面板实例**）。
///
/// 背景（世界输入失效事故）：通讯录/配置面板改为"按需创建"（方案 A）后，
/// 原先挂在 ContactPresenter（面板实例上的组件）上的常驻职责必须搬到这里：
///   ① 订阅 WsClient.UiEvent：npc_reply 分流（同格→当面弹出对话窗；不同格→未读登记+HUD 红点）
///   ② list_contacts / list_sessions RPC（结果写 ContactStore，由视图层打开时应用）
///   ③ 加/移好友 RPC（NpcPanelAddContact 经此走，不再要求通讯录面板在场）
///
/// 生命周期：ModMain.Init 里 ws 建立后 Attach 一次；常驻至游戏退出。
/// 线程约定：UiEvent/RPC 回调均由 WsClient 主线程派发 ✓。
/// </summary>
using System;
using System.Collections.Generic;
using Newtonsoft.Json.Linq;

namespace AgentLoopBridge
{
    internal static class ContactDuty
    {
        private static WsClient _ws;
        private static bool _attached;

        /// <summary>Python 连接建立后调用一次（重复调用安全）。</summary>
        public static void Attach(WsClient ws)
        {
            if (_attached || ws == null) return;
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

            // NPC 主动开口的收尾分流（README.md 附录二 G（功能设计底稿））：
            // 对话窗开着且就是该 NPC → 直播已读；同格且空闲且没开别的对话窗 → 当面弹出；
            // 其余 → 未读登记（HUD「传」按钮红点；面板打开时行上点亮）。
            bool initiative = payload["initiative"]?.Value<bool>() ?? false;
            if (!initiative) { FireChanged(); return; }
            // 该 NPC 的自主开口回合到此收口 → 撤销"动作完成自动开窗"的回合标记（ActionWatcher）
            ActionWatcher.ClearInitiativeTurn(npc);
            bool isError = payload["error"]?.Value<bool>() ?? false;
            if (isError) return;   // 失败兜底帧不进未读
            if (UnreadStore.IsChatOpenFor(npc)) return;

            string text = payload["text"]?.ToString() ?? "";
            var unit = UnitLookup.Resolve(npc);
            bool sameGrid = unit != null && UnitSnapshot.IsSameGrid(unit, g.world.playerUnit);
            bool chatOpenForNpc = UnreadStore.IsChatOpenFor(npc);
            bool anyChatOpen = UnreadStore.AnyChatOpen;
            bool busy = NpcInitiativeMonitor.IsPlayerBusy();
            // 用户拍板(b)：能不能"当面弹出"以**触发那一刻**的判定为准
            // （NpcInitiativeMonitor.RememberTriggerPop 记的 sameGrid && !busy），
            // 不再用"回复到达这一刻"的同格/忙闲重判一遍——否则异地传音的 NPC 在你等回复的
            // 几十秒里走进同一格，就会变成"我没点同意，怎么就当面开窗了"（22:17 / 22:58 两次实机）。
            // 回复时仍要求当前同格（避免"弹当面窗但人已不在跟前"）+ 无窗 + 不忙。
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

        /// <summary>拉取手动好友名单（真相在 Python contacts.json）。失败静默保持现状。</summary>
        public static void RequestManualContacts()
        {
            if (_ws == null) return;
            _ws.SendRequest("list_contacts", new JObject(), resp =>
            {
                if (resp["ok"]?.Value<bool>() != true) return;
                var arr = resp["data"]?["contacts"] as JArray;
                var names = new List<string>();
                if (arr != null)
                {
                    foreach (var t in arr)
                    {
                        string npc = t?["npc_id"]?.ToString();
                        if (!string.IsNullOrEmpty(npc)) names.Add(npc);
                    }
                }
                ContactStore.SetManualNames(names);
            }, 8000);
        }

        /// <summary>拉取会话索引（npc_id → 会话文件 mtime 秒）。</summary>
        public static void RequestSessions()
        {
            if (_ws == null) return;
            _ws.SendRequest("list_sessions", new JObject(), resp =>
            {
                if (resp["ok"]?.Value<bool>() != true) return;
                var arr = resp["data"]?["sessions"] as JArray;
                var list = new List<KeyValuePair<string, long>>();
                if (arr != null)
                {
                    foreach (var t in arr)
                    {
                        string npc = t?["npc_id"]?.ToString();
                        long m = t?["mtime"]?.Value<long>() ?? 0;
                        if (!string.IsNullOrEmpty(npc) && m > 0) list.Add(new KeyValuePair<string, long>(npc, m));
                    }
                }
                ContactStore.SetSessionMtimes(list);
            }, 8000);
        }

        // ------------------------------------------------------------------
        // ③ 加/移好友（NpcPanelAddContact 入口；不再要求通讯录面板在场）
        // ------------------------------------------------------------------

        /// <summary>本地镜像即时翻转（按钮状态秒变），真相随后由 list_contacts 回包校准。</summary>
        public static void SetManualContact(string name, bool add)
        {
            ContactStore.SetManualContact(name, add);
        }

        /// <summary>加/移好友 RPC（add/remove_contact），回包后重拉名单校准镜像。</summary>
        public static void ToggleManualContact(string name, bool add)
        {
            if (_ws == null || string.IsNullOrEmpty(name)) return;
            var req = new JObject { ["npc_id"] = name };
            _ws.SendRequest(add ? "add_contact" : "remove_contact", req, resp =>
            {
                RequestManualContacts();   // 回包校准镜像（Store 变更 → 视图自动刷新）
            }, 8000);
        }
    }
}
