/// <summary>
/// 终稿提交决策 —— **纯逻辑，零 Unity 依赖**（故意不引 UnityEngine，这样它能被
/// `csharp/tests/BubbleCommitPolicyTests` 直接编译做单元测试；实机行为无法在 WSL 里跑，
/// 但这条判定规则可以）。
///
/// ── 为什么需要它 ──────────────────────────────────────────────
/// 同一段正文会**两路到达** UI：
///   ① `step(text)`  —— 每步正文（也是非流式后端的整段正文）
///   ② `npc_reply`    —— 回合收口帧
/// 后者要"画一遍"还是"什么都不做"，原先写成一句字符串比对：
///
///     if (fullText == _lastRenderText &amp;&amp; _curTurn == _lastRenderTurn) return;
///
/// 这句隐含一个假设：**"我记不记得画过" == "屏幕上有没有"**。而实际上往屏幕上放正文的有
/// 三条路（`FinishNpcBubble` / `ReplaceHistory` 的 `AppendNpcStatic` / 流式的 `AppendDelta`），
/// 只要有一条没参与记账（2026-09-16 实机：回放没记），这个假设就崩，判据漏判 → 同一段话两遍。
///
/// ── 规则（按优先级）──────────────────────────────────────────
///   Skip             本回合同段正文已经定稿在屏幕上（**且那条行仍在**）→ 什么都不做。
///                    ★ 注意：走到这里仍可能有一条"多余的流式重绘泡"活着（流式门没有去重），
///                      调用方必须把它撤掉 —— 这是"模型复述同一段旁白"那条独立的重复来源。
///   OverwriteStreaming 有活跃流式泡 → 覆盖它（正常流式定稿）。
///   OverwriteLastRow   无流式泡，但本回合最后成的那条泡还在 → 覆盖它。
///                      用于终稿与已显示文本**有差异**的情况（`_collect_reply` 会剥
///                      &lt;think&gt;、剥行首自名前缀、strip，而 `step(text)` 发的是原文）。
///                      这种差异**不能**新建泡（新建 = 同一段话说两遍）。
///   CreateNew          本回合确实没有可用的行（首条正文 / 行已被重建销毁 / 被行数上限裁掉）。
///
/// ── 不可动摇的语义（两条都别改）──────────────────────────────
///   1. 判据**带回合号**：不同回合哪怕正文逐字相同（模型跨回合复读）也必须照常成泡。
///   2. 判据**绝不放宽到"文本相等就跳过"**：那会在"屏幕被重建、正文实际没显示"时把回复
///      整条吃掉（比重复严重得多）。回合号是唯一的"同一份"凭证。
/// </summary>
namespace AgentLoopBridge
{
    /// <summary>一次终稿提交应执行的动作。</summary>
    internal enum BubbleCommit
    {
        /// <summary>本回合同段正文已在屏幕上 → 不新建、不覆盖（但调用方要撤掉多余的重绘泡）。</summary>
        Skip = 0,
        /// <summary>覆盖当前活跃的流式泡。</summary>
        OverwriteStreaming = 1,
        /// <summary>覆盖本回合最后成的那条泡（终稿与已显示文本有规范化差异）。</summary>
        OverwriteLastRow = 2,
        /// <summary>新建一条泡。</summary>
        CreateNew = 3,
    }

    internal static class BubbleCommitPolicy
    {
        /// <summary>判定终稿提交动作。</summary>
        /// <param name="fullText">本次终稿全文（空串视为"没有正文"，永不判重）。</param>
        /// <param name="lastRenderText">屏幕上最后一条正文泡的文本（记账，未登记为 null）。</param>
        /// <param name="curTurn">当前直播回合号（`NoteTurn` 写；未知为 -1）。</param>
        /// <param name="lastRenderTurn">那条正文泡所属的回合号（未登记为 -1）。</param>
        /// <param name="hasStreamingBubble">是否存在活跃流式泡（`_deltaBubble != null &amp;&amp; _bubbleText != null`）。</param>
        /// <param name="lastRowAlive">那条正文泡的行是否还存在（Unity 已销毁对象 == null，故由调用方判）。</param>
        internal static BubbleCommit Decide(
            string fullText,
            string lastRenderText,
            int curTurn,
            int lastRenderTurn,
            bool hasStreamingBubble,
            bool lastRowAlive)
        {
            // ── 判重：本回合同段正文已经定稿 **且那条行还活着** ──
            // 四个条件缺一不可：有正文、回合号已知、回合号相同且文本逐字相同、行仍在屏幕上。
            // 末条 `lastRowAlive` 不是多余的：把"已经画过"当成"屏幕上有"才是原 bug 的根源，
            // 所以这里坚持问屏幕。行已不在（被重建/被裁）时宁可按"没画过"处理 ——
            // 丢失比重复严重得多。
            if (!string.IsNullOrEmpty(fullText) &&
                curTurn > 0 &&
                curTurn == lastRenderTurn &&
                lastRowAlive &&
                fullText == lastRenderText)
            {
                return BubbleCommit.Skip;
            }

            if (hasStreamingBubble) return BubbleCommit.OverwriteStreaming;

            // 同回合并有可覆盖的行 → 覆盖（终稿的规范化差异走这里，绝不新建）
            if (curTurn > 0 && curTurn == lastRenderTurn && lastRowAlive)
                return BubbleCommit.OverwriteLastRow;

            return BubbleCommit.CreateNew;
        }
    }
}
