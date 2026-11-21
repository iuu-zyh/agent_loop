/// <summary>
/// 历史回放**分帧构建**的纯逻辑核（零 Unity、零游戏类型 ⇒ 可编成 net8.0 单测）。
///
/// 【它解决什么】
/// 真机 11-20：单聊窗 575 条历史，按下开窗键之后**画面整整停住 1.13 秒**
/// （`[MFrame][FrameStall] ms=770` + 紧接着 `ms=358`），期间连 ESC 都不响应。
/// 那两帧里做了：克隆 575 行节点树、给 575 个头像挂点击按钮、写 2 张立绘快照。
///
/// 根因不是"某一步特别慢"，是**全部塞进同一帧**。玩家感受到的不是"575 行要建多久"，
/// 而是"屏幕停止响应多久"。
///
/// 【为什么不让它变快而要摊开】
/// 每行 ~1.34ms 的开销（克隆整行 + 文字网格 + 挂按钮）省不掉 —— 除非不建这一行
/// （虚拟列表 = 重写渲染层，代价与这个 bug 不相称）。能做的是把活摊到多帧：
/// 总量不变，但**没有一帧是卡的**。
///
/// 【为什么判据是耗时而不是条数】
/// 行与行不等价：纯文本气泡 ~0.5ms，带 `<color>` 富文本 + 工具折叠组的 ~5ms。
/// 只按条数分，撞上一堆富文本行时单帧照样超。所以每建完一行就问一次"该停了吗"。
///
/// 【为什么小回放不分帧】
/// ≤ <see cref="SyncTotalLimit"/> 条**故意保持同步**：十几行的回放分帧反而更糟 ——
/// 多出"历史还没画全"的中间态，而省下的耗时是零（本来就没超一帧）。只在真会卡时启用。
/// </summary>
namespace AgentLoopBridge
{
    internal static class HistoryBuildCore
    {
        /// <summary>单帧构建预算（毫秒）。8ms ≈ 60fps 下一半的帧预算：
        /// 留一半给游戏自己的逻辑与渲染，会掉几帧但不至于肉眼可见地停住。</summary>
        public const double FrameBudgetMs = 8.0;

        /// <summary>条目总数不超过它就**同步建完**（不分帧）。
        /// 60 条 ≈ 80ms @1.34ms/条，低于人能察觉的停顿阈值（~100ms）。</summary>
        public const int SyncTotalLimit = 60;

        /// <summary>每帧**至少**推进这么多行，与耗时预算取大。
        /// 防"第一条就超预算 ⇒ 每帧只前进一行"把 575 行拖成 575 帧（≈10 秒，
        /// 比卡一下更糟）。</summary>
        public const int MinRowsPerFrame = 6;

        /// <summary>每帧**至多**这么多行。防某批全是极便宜的行时一口气全建完
        /// （那等于没分帧）。</summary>
        public const int MaxRowsPerFrame = 40;

        /// <summary>这次回放要不要分帧（`totalItems` = 会产出行的条目数）。</summary>
        public static bool ShouldDefer(int totalItems)
        {
            return totalItems > SyncTotalLimit;
        }

        /// <summary>
        /// 本帧是否该收手（还剩 <paramref name="remaining"/> 条没建）。
        ///
        /// 调用方在**每建完一行之后**问一次；返回 true 就把剩下的留给下一帧。
        /// </summary>
        /// <param name="builtThisFrame">本帧已建行数（&gt;= 0）。</param>
        /// <param name="remaining">还没建的条目数（&lt;= 0 表示建完了）。</param>
        /// <param name="elapsedMs">本帧构建已耗时（毫秒，&gt;= 0）。</param>
        public static bool ShouldStopFrame(int builtThisFrame, int remaining, double elapsedMs)
        {
            if (remaining <= 0) return true;                 // 建完了
            if (builtThisFrame <= 0) return false;           // 一行都没建：必须推进
            if (builtThisFrame < MinRowsPerFrame) return false;   // 保底前进量
            if (builtThisFrame >= MaxRowsPerFrame) return true;   // 封顶
            return elapsedMs >= FrameBudgetMs;               // 预算用完
        }

        /// <summary>帧数估算（只给日志/自检用，不参与判据）。
        /// 按 <see cref="MinRowsPerFrame"/> 与实测均值估一个量级，免得日志里写个漂亮但假的数。</summary>
        public static int EstimateFrames(int totalItems)
        {
            if (totalItems <= 0) return 0;
            if (!ShouldDefer(totalItems)) return 1;
            const double perRowMs = 1.34;                    // 真机实测均值（770ms / 575 行）
            double perFrameMs = FrameBudgetMs;
            int rowsPerFrame = (int)(perFrameMs / perRowMs);
            if (rowsPerFrame < MinRowsPerFrame) rowsPerFrame = MinRowsPerFrame;
            if (rowsPerFrame > MaxRowsPerFrame) rowsPerFrame = MaxRowsPerFrame;
            return (totalItems + rowsPerFrame - 1) / rowsPerFrame;
        }
    }
}
