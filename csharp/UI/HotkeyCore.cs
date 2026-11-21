// HotkeyCore —— 通讯录快捷键（C）的**判定核**（零 Unity 依赖：全部输入由接线层传入）。
//
// 为什么把这几行切出来单独一个文件：
//   · 本工程踩过最贵的一课正好在这里 —— 历史上的 F9/F10/F11 三个热键**能注册、能按下、
//     就是"没反应"**，根因是帧回调被重复注册（`g.timer` 是游戏全局对象，不随场景销毁，
//     每"回主界面 → 再进世界"一轮就多挂一份 `AbHotkeys.OnFrame`），同帧两次按键判定
//     ⇒ Toggle 型一开一关互相抵消。这条"同帧只认一次"的规则必须被测试锁死，
//     而挂在 Unity 帧回调里、靠 `Input.GetKeyDown` 取值的代码没办法单测。
//   · 四条拦截判据全是布尔（我们的界面 / 输入框 / 战斗 / 游戏闸），纯函数最好写断言。
//
// 接线（读 Input、调 `HotkeyGate`/`FastKeyGate`/`NpcInitiativeMonitor`、触发
// `ContactPanelOpener`、打日志）在 `UI/AbHotkeys.cs` 的 `TickContactHotkey`；
// 跨文件接线契约由 `tests/test_contact_hotkey_contract.py` 锁（两边合起来才完整：
// 这里测"规则对不对"，那边测"接线有没有被改回去"）。
namespace AgentLoopBridge
{
    /// <summary>「这次 C 键该不该响应」的判定核。规则与文案都是契约，改动即契约改动。</summary>
    public static class HotkeyCore
    {
        /// <summary>响应 / 未响应的原因（日志按它决定打什么、节流成什么样）。</summary>
        public enum Reason
        {
            /// <summary>该响应（唯一会真正打开传音簿的分支）。</summary>
            Fire,
            /// <summary>这一帧没按下（接线层常态早退，一般不走到这里）。</summary>
            NotPressed,
            /// <summary>本帧已处理过一次 —— 重复注册的帧回调里的第二份。</summary>
            SameFrame,
            /// <summary>我们自己的面板开着（对话窗 / 配置面板 / 传音簿）。</summary>
            OurUiOpen,
            /// <summary>输入框聚焦中（玩家在打字，字母键属于输入）。</summary>
            InputFocused,
            /// <summary>战斗中（与游戏快捷键行为一致）。</summary>
            InBattle,
            /// <summary>游戏自己关着大地图闸（`isEnableMap=false`：剧情 / 读档 / 模态……）。</summary>
            MapDisabled,
        }

        /// <summary>一次按下的判定结果。</summary>
        public struct Decision
        {
            /// <summary>是否响应这次按键。</summary>
            public bool Fire;

            /// <summary>原因（`Fire` 为真时等于 `Reason.Fire`）。</summary>
            public Reason Why;

            /// <summary>接线层应当写回的"上次已处理帧号"（未消耗本帧时原值返回）。</summary>
            public int NewLastFrame;
        }

        /// <summary>
        /// 判定一次按下。判据顺序 = 日志原因的优先级，**不是随手排的**：
        ///   ① 同帧去重（必须最先：重复注册的回调里第二份看到的是同一帧同一个键）；
        ///   ② 我们的界面 → ③ 输入框 → ④ 战斗 → ⑤ 游戏闸（isEnableMap）。
        /// ②③ 是"玩家在跟我们自己的 UI 交互"，④⑤ 是"游戏此刻不许用快捷键"。
        /// ④ 排在 ⑤ 前面是为了诊断价值：报"战斗中"是确定的事实（`isBattle` 直读），
        /// 报 isEnableMap=False 是推论（还可能是剧情 / 读档 / 模态），
        /// 两者分开报，真机第一次实验就能把"游戏闸到底在哪些场景关"校准出来。
        /// </summary>
        public static Decision Decide(int frame, int lastFrame, bool pressed,
                                      bool ourUiOpen, bool inputFocused,
                                      bool mapEnabled, bool battleActive)
        {
            Decision d;
            d.Fire = false;
            d.Why = Reason.Fire;
            d.NewLastFrame = lastFrame;

            if (!pressed) { d.Why = Reason.NotPressed; return d; }

            // ★ 同帧去重：本核存在的一半理由（见文件头）★
            if (frame == lastFrame) { d.Why = Reason.SameFrame; return d; }

            // "记帧"与"是否响应"**解耦**：被拦下的按下同样算用过这一帧 ——
            // 否则同帧第二份回调会把同一次按下的日志再打一遍（去重就白做了）。
            d.NewLastFrame = frame;

            if (ourUiOpen) { d.Why = Reason.OurUiOpen; return d; }
            if (inputFocused) { d.Why = Reason.InputFocused; return d; }
            if (battleActive) { d.Why = Reason.InBattle; return d; }
            if (!mapEnabled) { d.Why = Reason.MapDisabled; return d; }

            d.Fire = true;
            d.Why = Reason.Fire;
            return d;
        }

        /// <summary>原因 → 人话（日志文案；热键是低频事件，不做字符串缓存）。</summary>
        public static string Describe(Reason r)
        {
            switch (r)
            {
                case Reason.SameFrame: return "同一帧已处理过（帧回调重复注册）";
                case Reason.OurUiOpen: return "我们的界面开着（对话窗/配置/传音簿）";
                case Reason.InputFocused: return "输入框聚焦中（在打字）";
                case Reason.InBattle: return "战斗中";
                case Reason.MapDisabled: return "游戏此刻不允许大地图快捷键（isEnableMap=False）";
                case Reason.NotPressed: return "未按下";
                default: return "响应";
            }
        }
    }
}
