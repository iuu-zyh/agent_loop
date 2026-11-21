/// <summary>
/// TeamJoinCore —— 「决斗要不要带队友」的**状态机核**（10-25 立；零 Unity / 零 Newtonsoft）。
///
/// ## 它回答的唯一问题
/// 「现在该不该问玩家一句：这次要不要叫上队友？」以及「玩家答过了没有、答的是什么」。
///
/// ## 为什么判据要跟 `BattleFlowState` 绑在一起，而不是自己数秒
/// 决斗的时序是（真机 `Player.log` 10-25 18:51）：
/// <code>
///   页 20203（寻仇开场页）→ 玩家点「战斗」100003
///     → 应战窗 MapBattlePre（**唯一能问玩家的时刻**）
///       → 玩家点应战 → IntoBattle（同毫秒窗关）→ 场景加载 ≈1s
///         → SceneBattle.Init → DungeonTrigger → addBattleNPCData（**注入点**）
/// </code>
/// 所以"表态"必须发生在应战窗亮着时，而"消费"发生在它关掉之后。中间不能有任何
/// "等 N 秒作废"的东西 —— 那会把"玩家点得慢"和"玩家不打算打"混为一谈。
///
/// 本核因此只认两类事实：
///   ① **应战窗亮着**（`preShowing`，接线层从 UI 层层顶读，见 `WorldStateFacts.BattlePreShowing`）；
///   ② **战斗流程状态转移**（`BattleFlowState`，由 `BattleFlowCore` 用页/窗/战斗位三条证据算）。
/// 两者都是事件或状态，不是时间。
///
/// ## 一张表（谁在什么时候改什么）
/// | 事件 | 动作 |
/// |---|---|
/// | 应战窗亮 + 队非空 + 知道对手 + 我方窗没开着 | 返回 true（接线层弹窗），并记「本场已问」 |
/// | 玩家点「一起应战」/「我一个人」 | `Answer(true/false)` |
/// | 注入层要答案 | `Take()`（取走并清空 = 一次性） |
/// | 流程进 `InBattle` | 什么都不做（**答案要留给注入层消费**） |
/// | 流程回 `Idle`（打完了，或应战被取消、根本没打） | 答案作废、本场重置 |
///
/// ★ 为什么"回 Idle"就要作废，而不是"回 Idle 且没打过"才作废 ★
/// 打完那一场之后，上一个答案绝不该被下一场继承 —— 下一场会重新问。两者用同一条出口，
/// 只是日志措辞不同（`why` 里会写清是"打完"还是"没打成"）。
/// </summary>
using System;

namespace AgentLoopBridge
{
    /// <summary>玩家的表态。`None` = 没答（含"窗没弹出来"、"点了应战但没理我们"）。</summary>
    internal enum JoinAnswer
    {
        /// <summary>没有明确表态。**默认按"不带队友"处理**（没点 = 没同意）。</summary>
        None = 0,
        /// <summary>玩家点了「一起应战」。</summary>
        Yes = 1,
        /// <summary>玩家点了「我一个人」。</summary>
        No = 2,
    }

    internal sealed class TeamJoinCore
    {
        /// <summary>当前是否处在一次「战前」流程里（应战窗出现过）。</summary>
        private bool _episode;

        /// <summary>本场是否已经问过（防重复弹窗：窗重建 / 采样反复都会让条件反复成立）。</summary>
        private bool _asked;

        private JoinAnswer _answer = JoinAnswer.None;

        private int _asks;
        private int _takes;
        private int _dropped;

        /// <summary>累计问了几次（日志/诊断用）。</summary>
        internal int AskCount { get { return _asks; } }
        /// <summary>累计被注入层取走几次。</summary>
        internal int TakeCount { get { return _takes; } }
        /// <summary>累计有多少次表态因为"这场没打成/打完了"而作废。</summary>
        internal int DroppedCount { get { return _dropped; } }

        internal JoinAnswer Answer { get { return _answer; } }
        internal bool HasAnswer { get { return _answer != JoinAnswer.None; } }
        internal bool Episode { get { return _episode; } }
        internal bool Asked { get { return _asked; } }

        /// <summary>
        /// 每一拍问一次：**现在该不该弹窗**。
        ///
        /// 返回 true 时调用方立刻弹；返回 false 时 `why` 说明为什么（空串 = 没到时机，不必记日志）。
        /// 本方法**有副作用**：返回 true 时把"本场已问"置上 —— 调用方不需要再自己防重。
        /// </summary>
        /// <param name="preShowing">应战窗是否亮着（判据见 `WorldStateFacts.BattlePreShowing`）。</param>
        /// <param name="teamPresent">玩家是否有队友（`TeamTool.Members()` 非空）。</param>
        /// <param name="attackerKnown">是否知道"对面是谁"（判不出就不问 —— 立绘与文案都要它）。</param>
        /// <param name="ownWindowVisible">我方自己的确认窗是否已经开着（防重叠弹窗）。</param>
        internal bool ShouldAsk(bool preShowing, bool teamPresent, bool attackerKnown,
                                bool ownWindowVisible, out string why)
        {
            why = "";
            if (!preShowing) return false;                 // 只在应战窗亮着时问（其余时机零开销）
            if (!_episode) { _episode = true; _asked = false; }
            if (_asked) { why = "本场已问过"; return false; }

            // ★ 已经表过态就不再问（10-26 复核补，真机形态推演过）★
            //   我们的窗会盖住应战窗 ⇒ `BattlePreShowing()` 变假（层顶换人）⇒
            //   `BattleFlowGate` 可能判"应战窗关了"退出战前；玩家点完我们的窗后
            //   应战窗又回到层顶 ⇒ 流程**再进一次 PreBattle**（那里会复位 `_asked`）
            //   ⇒ 第二次弹窗，而第二次的表态会把第一次的**覆盖掉**。
            //   玩家点过一次就是一次；答案只由注入层 `Take()` 消费或由"回 Idle"作废。
            if (_answer != JoinAnswer.None)
            {
                why = "本场已表态（" + _answer + "）";
                return false;
            }

            if (ownWindowVisible) { why = "我方窗口正开着"; return false; }
            if (!teamPresent) { why = "没有队友"; return false; }
            if (!attackerKnown)
            {
                // 判不出对手 ⇒ 不问，也不重试（否则每 0.25s 试一次，日志会被刷满）
                _asked = true;
                why = "判不出对手（不问）";
                return false;
            }
            _asked = true;
            _asks++;
            return true;
        }

        /// <summary>玩家表态。允许被后来的表态覆盖（同一场里理论上不会发生，覆盖比拒绝安全）。</summary>
        internal void SetAnswer(bool yes)
        {
            _answer = yes ? JoinAnswer.Yes : JoinAnswer.No;
        }

        /// <summary>取走表态（一次性）。注入层每场只该调一次；重复调用拿到 `None`。</summary>
        internal JoinAnswer Take()
        {
            JoinAnswer a = _answer;
            _answer = JoinAnswer.None;
            if (a != JoinAnswer.None) _takes++;
            return a;
        }

        /// <summary>
        /// 战斗流程状态转移（由 `BattleFlowGate` 在状态**真的变了**时调）。
        ///
        /// 只关心两件事：
        ///   · 进 `InBattle` —— 答案必须留着，注入层马上要用（这里**什么都不做**）；
        ///   · 回 `Idle` —— 这一场结束了/没打成，答案作废、本场重置。
        /// </summary>
        internal void OnFlowStateChanged(BattleFlowState before, BattleFlowState now, out string why)
        {
            why = "";
            if (now == BattleFlowState.InBattle) return;

            if (now == BattleFlowState.Idle)
            {
                bool had = _answer != JoinAnswer.None;
                if (had) _dropped++;
                _answer = JoinAnswer.None;
                _episode = false;
                _asked = false;
                if (had)
                    why = before == BattleFlowState.InBattle
                        ? "战斗结束 ⇒ 上一场的表态作废"
                        : "战前流程结束（没打成）⇒ 表态作废";
                return;
            }

            if (now == BattleFlowState.PreBattle && before != BattleFlowState.PreBattle)
            {
                _episode = true;
                _asked = false;
                why = "进入战前 ⇒ 本场可问";
            }
        }

        /// <summary>换世界/读档：全清（与 `BattleFlowCore.Reset` 同一个时机）。</summary>
        internal void Reset()
        {
            _episode = false;
            _asked = false;
            _answer = JoinAnswer.None;
        }

        /// <summary>一行诊断。</summary>
        internal string Brief()
        {
            return "episode=" + _episode + " asked=" + _asked + " answer=" + _answer
                 + " asks=" + _asks + " takes=" + _takes + " drops=" + _dropped;
        }
    }
}
