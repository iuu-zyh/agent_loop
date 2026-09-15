/// <summary>
/// 未读传音登记表（静态，主线程）—— NPC 主动开口的"信箱"侧。
///
/// 分流契约（见 README.md 附录二 G（功能设计底稿））：
///  - npc_reply(initiative=true) 到达时，若对话窗正开着且就是该 NPC → 直播已读，不登记；
///  - **触发时可当面**（触发那一刻同格且玩家不忙，见 NpcInitiativeMonitor.CanPopAtTrigger）
///    且无对话窗且玩家空闲 → 当面开口（弹出对话 UI），不登记；
///    ⚠ 以**触发时**判定为准，不用回复到达时的状态重判——否则异地传音的 NPC 走到你面前会变成
///    "没点同意就当面开窗"（09-13 用户拍板 (b)，两次实机复现）。
///  - 其余（异地/忙/开着别的 NPC）→ Mark 登记：通讯录行红点 + 游戏内横幅；
///  - 打开该 NPC 对话 → Clear（OpenChatFor / NotifyChatOpen 路径）。
///
/// 消息本体永远在该 NPC 的 session 账本（主动开口回合正常落账），本表只是
/// 客户端"没看过"的标记，不持久化（游戏重启红点消失，历史仍可回放）。
/// 硬约束：仅 Unity 主线程调用（UiEvent 回调/按钮回调天然满足）。
/// </summary>
using System;
using System.Collections.Generic;

namespace AgentLoopBridge
{
    internal static class UnreadStore
    {
        /// <summary>
        /// 未读统一色 #FF3B30 —— HUD「传」钮角标 / 通讯录行红点 / 顶部横幅圆点
        /// **三处共用这一个值**（09-13 收敛：此前 HUD 那份是自己手写的 #F24040，
        /// 和面板不是一套色，看着就是"外挂件"）。
        /// </summary>
        public static readonly UnityEngine.Color UnreadRed =
            new UnityEngine.Color(1f, 0.231f, 0.188f, 1f);
        /// <summary>角标白描边（HUD 红点外圈；暖白，避免纯白在墨底上发青）。</summary>
        public static readonly UnityEngine.Color UnreadRing =
            new UnityEngine.Color(0.99f, 0.98f, 0.95f, 1f);

        public class Item
        {
            public string NpcId;
            public string Text;      // 最新一条正文（横幅预览用）
            public long TimeSec;     // unix 秒
            public int Count;        // 未读条数（多次累计，打开即清零）
        }

        private static readonly Dictionary<string, Item> _unread = new Dictionary<string, Item>();

        /// <summary>当前打开着的对话对象（空=没有对话窗开着）。最后打开者为准。</summary>
        public static string ActiveChatNpc = "";

        public static bool IsChatOpenFor(string npc)
        {
            return !string.IsNullOrEmpty(npc) && ActiveChatNpc == npc;
        }

        public static bool AnyChatOpen => !string.IsNullOrEmpty(ActiveChatNpc);

        /// <summary>对话窗打开（ChatPresenter.OpenFor* / AbChatPanel.InitData 调；顺带清该 NPC 未读）。</summary>
        public static void NotifyChatOpen(string npc)
        {
            ActiveChatNpc = npc ?? "";
            if (!string.IsNullOrEmpty(npc)) Clear(npc);
        }

        /// <summary>对话窗关闭（按 npc 匹配才清：防旧面板销毁时误关掉新面板的登记）。</summary>
        public static void NotifyChatClosed(string npc)
        {
            if (string.IsNullOrEmpty(npc) || ActiveChatNpc == npc) ActiveChatNpc = "";
        }

        public static bool Has(string npc)
        {
            return !string.IsNullOrEmpty(npc) && _unread.ContainsKey(npc);
        }

        public static Item Peek(string npc)
        {
            return npc != null && _unread.TryGetValue(npc, out var it) ? it : null;
        }

        /// <summary>登记一条未读（同一 NPC 累计 Count、覆盖预览文本）。</summary>
        public static void Mark(string npc, string text)
        {
            if (string.IsNullOrEmpty(npc)) return;
            long now = DateTimeOffset.UtcNow.ToUnixTimeSeconds();
            if (_unread.TryGetValue(npc, out var it))
            {
                it.Text = text;
                it.TimeSec = now;
                it.Count++;
                return;
            }
            _unread[npc] = new Item { NpcId = npc, Text = text ?? "", TimeSec = now, Count = 1 };
        }

        public static void Clear(string npc)
        {
            if (!string.IsNullOrEmpty(npc)) _unread.Remove(npc);
        }

        /// <summary>当前未读总数（横幅"N 条新传音"用）。</summary>
        public static int TotalCount
        {
            get
            {
                int n = 0;
                foreach (var kv in _unread) n += kv.Value.Count;
                return n;
            }
        }

        /// <summary>是否有任意未读（横幅显示判定）。</summary>
        public static bool Any => _unread.Count > 0;

        /// <summary>最新一条未读（横幅展示对象；按时间取最大）。</summary>
        public static Item Latest()
        {
            Item best = null;
            foreach (var kv in _unread)
            {
                if (best == null || kv.Value.TimeSec > best.TimeSec) best = kv.Value;
            }
            return best;
        }
    }
}
