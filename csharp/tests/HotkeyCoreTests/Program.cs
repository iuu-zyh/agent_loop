// 通讯录快捷键（C）判定核（HotkeyCore）的单元测试。
//
// 背景（09-24 用户需求）：给传音簿加一个键盘快捷键（键位 C），三条硬要求 ——
//   ① 不与游戏快捷键重叠；
//   ② 与游戏快捷键行为一致：**战斗中不触发**；
//   ③ 自己的对话窗 / 配置面板 / 传音簿**开着时不触发**。
// 判定规则被切成零 Unity 依赖的核（UI/HotkeyCore.cs），本文件把每条规则钉死。
//
// 覆盖（每条都对应一个**会在真机静默现形**的分支）：
//   ① 同帧去重：重复注册的帧回调里第二份必须跳过（F9/F10/F11"按了没反应"的根因）；
//   ② 四条拦截判据各自生效，且**被拦下的按下同样消耗该帧**（防同帧重复打日志）；
//   ③ 判据优先级稳定（日志原因可预期：界面 → 输入框 → 战斗 → 游戏闸）；
//   ④ 未按下不消耗任何状态；
//   ⑤ 下一帧重按能正常触发（去重不误伤）；`frame=0` 不被初值吃掉（故初值必须是 -1）；
//   ⑥ Describe 文案：五个原因互不相同且非空（日志是唯一的现场诊断面）。

using System;
using System.Collections.Generic;
using AgentLoopBridge;

namespace AgentLoopBridge.Tests
{
    internal static class Program
    {
        private static int _pass;
        private static readonly List<string> _fails = new List<string>();

        private static void Check(string name, bool ok, string detail = "")
        {
            if (ok) { _pass++; return; }
            _fails.Add(name + (detail.Length > 0 ? "  ｜ " + detail : ""));
            Console.WriteLine("  ✗ " + name + (detail.Length > 0 ? "  ｜ " + detail : ""));
        }

        private static void Eq<T>(string name, T got, T want) =>
            Check(name, EqualityComparer<T>.Default.Equals(got, want),
                  "实际=" + got + " 期望=" + want);

        /// <summary>判据全开的"世界正常"基线：按下就该触发。</summary>
        private static HotkeyCore.Decision Press(int frame, int last,
                                                bool uiOpen = false, bool focused = false,
                                                bool mapOk = true, bool battle = false)
            => HotkeyCore.Decide(frame, last, true, uiOpen, focused, mapOk, battle);

        private static int Main()
        {
            Console.WriteLine("HotkeyCore 单元测试");

            TestFireBaseline();
            TestSameFrameDedup();
            TestNotPressedKeepsState();
            TestBlockersEach();
            TestBlockedPressStillConsumesFrame();
            TestBlockerPriority();
            TestFrameZeroNotEaten();
            TestDescribeCoverage();

            Console.WriteLine();
            if (_fails.Count == 0)
            {
                Console.WriteLine("全部通过（" + _pass + " 断言）");
                return 0;
            }
            Console.WriteLine("失败 " + _fails.Count + " 条 / 共 " + (_pass + _fails.Count) + " 断言");
            foreach (var f in _fails) Console.WriteLine("  - " + f);
            return 1;
        }

        // ---------- ① 基线：世界正常 + 按下 → 触发 ----------

        private static void TestFireBaseline()
        {
            var d = Press(100, -1);
            Eq("正常按下 → Fire", d.Fire, true);
            Eq("正常按下 → Why=Fire", d.Why, HotkeyCore.Reason.Fire);
            Eq("正常按下 → 记账本帧", d.NewLastFrame, 100);
        }

        // ---------- ② 同帧去重（本核存在的一半理由） ----------

        private static void TestSameFrameDedup()
        {
            // 第一份回调：正常触发并记账
            var d1 = Press(100, -1);
            Eq("第一份：触发", d1.Fire, true);

            // 第二份回调（重复注册）：同帧同键 → 必须跳过
            var d2 = Press(100, d1.NewLastFrame);
            Eq("同帧第二份：不触发", d2.Fire, false);
            Eq("同帧第二份：原因=SameFrame", d2.Why, HotkeyCore.Reason.SameFrame);
            Eq("同帧第二份：不改记账", d2.NewLastFrame, 100);

            // 下一帧再按：必须正常（去重不能把之后的按键一起吃掉）
            var d3 = Press(101, d2.NewLastFrame);
            Eq("下一帧重按：恢复触发", d3.Fire, true);
            Eq("下一帧重按：记账推进", d3.NewLastFrame, 101);
        }

        // ---------- ③ 未按下：不消耗也不触发 ----------

        private static void TestNotPressedKeepsState()
        {
            var d = HotkeyCore.Decide(100, 55, pressed: false,
                                      ourUiOpen: false, inputFocused: false,
                                      mapEnabled: true, battleActive: false);
            Eq("未按下：不触发", d.Fire, false);
            Eq("未按下：原因=NotPressed", d.Why, HotkeyCore.Reason.NotPressed);
            Eq("未按下：不改记账", d.NewLastFrame, 55);
        }

        // ---------- ④ 四条判据各自生效 ----------

        private static void TestBlockersEach()
        {
            var a = Press(200, -1, uiOpen: true);
            Eq("面板开着 → 不触发", a.Fire, false);
            Eq("面板开着 → 原因=OurUiOpen", a.Why, HotkeyCore.Reason.OurUiOpen);

            var b = Press(200, -1, focused: true);
            Eq("输入框聚焦 → 不触发", b.Fire, false);
            Eq("输入框聚焦 → 原因=InputFocused", b.Why, HotkeyCore.Reason.InputFocused);

            var c = Press(200, -1, battle: true);
            Eq("战斗中 → 不触发", c.Fire, false);
            Eq("战斗中 → 原因=InBattle", c.Why, HotkeyCore.Reason.InBattle);

            var e = Press(200, -1, mapOk: false);
            Eq("游戏闸关闭 → 不触发", e.Fire, false);
            Eq("游戏闸关闭 → 原因=MapDisabled", e.Why, HotkeyCore.Reason.MapDisabled);
        }

        // ---------- ⑤ 被拦下的按下同样消耗该帧 ----------

        private static void TestBlockedPressStillConsumesFrame()
        {
            // 战斗中按 C：第一次报"战斗中"
            var d1 = Press(300, -1, battle: true);
            Eq("战斗中按下：不触发", d1.Fire, false);
            Eq("战斗中按下：**要记账**（否则同帧第二份会把日志再打一遍）", d1.NewLastFrame, 300);

            // 同帧第二份回调：应报 SameFrame，而不是再报一次"战斗中"
            var d2 = Press(300, d1.NewLastFrame, battle: true);
            Eq("同帧第二份：原因=SameFrame（不是重复的 InBattle）",
               d2.Why, HotkeyCore.Reason.SameFrame);
        }

        // ---------- ⑥ 判据优先级（= 日志原因的优先级，必须稳定） ----------

        private static void TestBlockerPriority()
        {
            // 界面 > 输入框 > 战斗 > 游戏闸：同时成立时报最靠前的那条
            Eq("界面+战斗 → 报界面",
               Press(400, -1, uiOpen: true, battle: true).Why, HotkeyCore.Reason.OurUiOpen);
            Eq("输入框+游戏闸 → 报输入框",
               Press(400, -1, focused: true, mapOk: false).Why, HotkeyCore.Reason.InputFocused);
            Eq("战斗+游戏闸 → 报战斗（战斗是直读事实，游戏闸是推论，分开报才好校准）",
               Press(400, -1, battle: true, mapOk: false).Why, HotkeyCore.Reason.InBattle);
            Eq("四条全中 → 报最靠前的界面",
               Press(400, -1, uiOpen: true, focused: true, battle: true, mapOk: false).Why,
               HotkeyCore.Reason.OurUiOpen);
        }

        // ---------- ⑦ `frame=0` 不被初值吃掉 ----------

        private static void TestFrameZeroNotEaten()
        {
            // 接线层的初值是 -1（字段注释里写明"不能用 0"的原因）；游戏第 0 帧的按下必须能触发
            var d = Press(0, -1);
            Eq("frame=0：触发", d.Fire, true);
            Eq("frame=0：记账", d.NewLastFrame, 0);
            // 用 0 当初值就会在这里静默吃掉一次按键 —— 这条断言就是那个 bug 的探针
            Eq("frame=0 配错初值（0）→ 会被误判 SameFrame（反证）",
               Press(0, 0).Why, HotkeyCore.Reason.SameFrame);
        }

        // ---------- ⑧ 原因文案：日志是唯一的现场诊断面 ----------

        private static void TestDescribeCoverage()
        {
            var reasons = new[]
            {
                HotkeyCore.Reason.SameFrame, HotkeyCore.Reason.OurUiOpen,
                HotkeyCore.Reason.InputFocused, HotkeyCore.Reason.InBattle,
                HotkeyCore.Reason.MapDisabled,
            };
            var seen = new List<string>();
            for (int i = 0; i < reasons.Length; i++)
            {
                string s = HotkeyCore.Describe(reasons[i]);
                Check("Describe(" + reasons[i] + ") 非空", !string.IsNullOrEmpty(s));
                Check("Describe(" + reasons[i] + ") 不与前面重复", !seen.Contains(s), s);
                seen.Add(s);
            }
            Check("Describe 提到 isEnableMap（真机核对该值的现场依据）",
                  HotkeyCore.Describe(HotkeyCore.Reason.MapDisabled).Contains("isEnableMap"));
            Check("Describe 的四条拦截原因里区分'界面'与'输入框'",
                  HotkeyCore.Describe(HotkeyCore.Reason.OurUiOpen).Contains("界面")
                  && HotkeyCore.Describe(HotkeyCore.Reason.InputFocused).Contains("输入框"));
        }
    }
}
