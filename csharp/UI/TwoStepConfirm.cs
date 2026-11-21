/// <summary>
/// 两段确认（不可恢复按钮的「首击武装 → 窗口内再击执行」）—— **零 Unity 依赖的纯逻辑**
/// （与 `BubbleCommitPolicy` / `ToolFoldPolicy` 同款，刻意不 using UnityEngine，好让 net8.0 单测直接编进去跑判定表）。
///
/// ## 为什么抽出这个类（2026-09-16，配「重置为默认」）
/// 「删除文件」与「重置为默认」都是**不可恢复**操作，都要：首击只武装并改按钮文案、窗口内再击才执行、
/// 换目标/超时立刻解除武装。原先这段内联在 `ConfigPresenter.DeleteCurrentFile` 里，加第二个按钮时
/// 就要么复制一份（两份判定迟早走偏），要么共用一份（那正是本类）。
/// 顺带把边界钉进单测：**恰好 3.0 秒**、过期后再点、换文件解除 —— 这些边界内联时最容易写错，
/// 而实机上几乎不可能稳定复现（差一毫秒的手感差异没人测得出来，但"武装卡死"能被用户骂）。
///
/// ## 语义（与既有删除流程逐字一致，不许悄悄改）
///   `First`   —— 本次点击**只武装**：调用方把按钮文案改成「确认删除？/确认重置？」并提示"再点一次"。
///   `Second`  —— 窗口内第二次点击：调用方执行真正的操作。**返回前已自动解除武装**
///                （调用方不必也不该再调一次，误调无害）。
///   `Expired` —— 武装已过期：本次点击**视作重新开始**，返回时 `Armed == false`；
///                调用方提示「确认已超时，请重新点击」，用户下一次点击就是新的 `First`。
///
/// ## 不变量（单测逐条锁着）
///   ① `Second` 与 `Expired` 返回后 `Armed` 必为 false（绝不把武装状态留过界）；
///   ② 窗口判据是**严格大于**（`now - ArmedAt > Window`）：恰好等于窗口那一刻仍算"来得及"，
///      与既有 `Time.time - _deleteArmTime > 3f` 的行为一字不差；
///   ③ 未武装时任何时刻点击都只得到 `First`（不会出现"没武装却直接执行"这种最危险的错）；
///   ④ 与真实时间解耦：`now` 由调用方传入（`Time.time`），所以测试不需要等 3 秒。
/// </summary>
namespace AgentLoopBridge
{
    internal sealed class TwoStepConfirm
    {
        /// <summary>确认窗口秒数（与删除流程既有值一致；改这里等于同时改两个按钮的手感）。</summary>
        internal const float DefaultWindow = 3f;

        internal enum Step
        {
            /// <summary>首击：已武装，等第二次点击。</summary>
            First,
            /// <summary>窗口内再击：执行操作（返回时已解除武装）。</summary>
            Second,
            /// <summary>武装已过期：本次点击作废，请重新开始（返回时已解除武装）。</summary>
            Expired,
        }

        private readonly float _window;

        /// <summary>当前是否处于"已武装"态（调用方据此决定按钮文案与提示语）。</summary>
        internal bool Armed { get; private set; }

        /// <summary>武装发生的时刻（`Click` 传入的 now 原值）。仅诊断/测试用，未武装时为 0。</summary>
        internal float ArmedAt { get; private set; }

        /// <summary>窗口可定制（测试用短窗口；非法值回落到默认，避免 0/负值让确认形同虚设）。</summary>
        internal TwoStepConfirm(float window = DefaultWindow)
        {
            _window = window > 0f ? window : DefaultWindow;
        }

        /// <summary>收到一次点击。<paramref name="now"/> 由调用方给（生产传 `Time.time`）。</summary>
        internal Step Click(float now)
        {
            if (!Armed)
            {
                Armed = true;
                ArmedAt = now;
                return Step.First;
            }
            if (now - ArmedAt > _window)
            {
                Disarm();
                return Step.Expired;
            }
            Disarm();
            return Step.Second;
        }

        /// <summary>解除武装（换目标文件、执行完毕、超时都走这里）。幂等。</summary>
        internal void Disarm()
        {
            Armed = false;
            ArmedAt = 0f;
        }
    }
}
