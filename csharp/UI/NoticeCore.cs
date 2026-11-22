/// <summary>
/// 须知面板的**判定核**（零 Unity、零 Newtonsoft、零文件 IO）—— 与 `TimeSepCore` / `ContactTimeCore`
/// / `DragCore` 同款：判据全在这里，`csharp/tests/NoticeCoreTests` 直接链接本文件在 WSL 里跑断言。
///
/// ── 它回答的问题只有一个：**该不该自动弹、弹哪一页、哪几条算新的** ────────────
/// 文件读写（`notice_seen.json`）与 UI 都在 `NoticeStore` / `NoticePanel`，本类不碰磁盘也不碰
/// UnityEngine，所以"文件不存在/写坏了/水位倒退"这些边界能在单测里逐条钉死，
/// 而不必进游戏试。
///
/// ── 水位（watermark）语义 ────────────────────────────────────────────────
/// "已看到的最高公告序号"。判据只有一句：`maxSeq > seenSeq` ⇒ 有没看过的东西。
///   · `seenSeq == 0` = **从没打开过**（文件不存在 / 解析失败 / 首次安装）⇒ 弹到「使用须知」；
///   · `seenSeq == maxSeq` = 都看过 ⇒ 不弹；
///   · `seenSeq > maxSeq` = 文件里的水位比当前版本还高（玩家降级了 mod）⇒ **不弹**，
///     且**回写时不许下调**（见 `NextSeen`）—— 否则回退再升级会重复弹一次。
///
/// ── 为什么不用版本字符串比较 ──────────────────────────────────────────────
/// `1.0.10 < 1.0.9` 是字符串比较的经典陷阱；更要命的是"内容改了忘改版本号"时字符串判据会
/// **静默失效**（公告从此不再出现，且没有任何报错）。用整数序号时，"加了一条公告"这个动作
/// 本身就带着判据。`tests/test_notice_panel_contract.py` 另外钉住"最新一条的 `Version`
/// 必须等于 `About.Version`"，专防忘记提版本。
/// </summary>
namespace AgentLoopBridge
{
    /// <summary>「已看到哪」的水位快照。`HasFile=false` 时 `Seq` 无意义（按 0 处理）。</summary>
    internal struct NoticeSeen
    {
        /// <summary>已看到的最高公告序号；0 = 从没看过。</summary>
        internal int Seq;
        /// <summary>写下这条水位时的版本（只作页脚展示与诊断，**不参与判定**）。</summary>
        internal string Version;
        /// <summary>水位最后一次前进的时间（ISO 串，只作诊断）。</summary>
        internal string At;
        /// <summary>盘上真的有这个文件（区分"文件不存在"与"文件在但序号是 0"）。</summary>
        internal bool HasFile;

        internal static NoticeSeen None
        {
            get { return new NoticeSeen { Seq = 0, Version = "", At = "", HasFile = false }; }
        }
    }

    internal static class NoticeCore
    {
        /// <summary>水位文件名（落在数据根：`%USERPROFILE%\.agent_loop\notice_seen.json`，与 config.json 同层）。</summary>
        internal const string FileName = "notice_seen.json";

        /// <summary>两个页签的下标（`NoticePanel` 与判据共用，避免各写各的数字）。</summary>
        internal const int TabGuide = 0;
        internal const int TabUpdates = 1;

        /// <summary>
        /// 该不该自动弹。`force` = 诊断哨兵 `_diag_notice_always.txt` 存在（调试版面用）。
        /// `maxSeq <= 0`（内容表为空）时恒 false —— 没东西可看就别打扰玩家。
        /// </summary>
        internal static bool ShouldAutoShow(int seenSeq, int maxSeq, bool force = false)
        {
            if (maxSeq <= 0) return false;
            if (force) return true;
            return seenSeq < maxSeq;
        }

        /// <summary>
        /// 自动弹出该落在哪一页：从没看过 ⇒「使用须知」（先教怎么用）；看过旧版本 ⇒「更新公告」。
        /// 依据是 `seenSeq` 而不是 `HasFile`：文件在但序号 0（被写坏过）同样当"没看过"。
        /// </summary>
        internal static int TabFor(int seenSeq)
        {
            return seenSeq <= 0 ? TabGuide : TabUpdates;
        }

        /// <summary>某条公告算不算"新"（挂「新」小胶囊）。`seenSeq=0` 时全部算新。</summary>
        internal static bool IsNew(int itemSeq, int seenSeq)
        {
            return itemSeq > seenSeq;
        }

        /// <summary>
        /// 写回时的新水位：只许前进，**绝不后退**（`seenSeq > maxSeq` = 玩家降级过 mod，
        /// 回退时把水位拉低会导致再次升级时重复弹）。
        /// </summary>
        internal static int NextSeen(int seenSeq, int maxSeq)
        {
            return maxSeq > seenSeq ? maxSeq : seenSeq;
        }

        /// <summary>水位有没有真的变化（没变就不写盘 —— 反复开关面板时零 IO）。</summary>
        internal static bool NeedsWrite(int seenSeq, int maxSeq)
        {
            return maxSeq > seenSeq;
        }

        /// <summary>页脚左侧那句：`已看过 v1.0.0` / `第一次打开`（`seenSeq<=0` 或版本空）。</summary>
        internal static string FooterSeen(int seenSeq, string seenVersion)
        {
            string v = (seenVersion ?? "").Trim();
            if (seenSeq <= 0 || v.Length == 0) return "第一次打开";
            return "上次看过 v" + v;
        }

        /// <summary>
        /// 页脚右侧那句：还剩多少没看。用于"我知道有更新但我先不看"的场合 ——
        /// 关掉面板**不推进水位**（见 `NoticePanel.MarkSeen` 只在打开时推进），所以下次还会提示。
        /// </summary>
        internal static string FooterUnread(int seenSeq, int maxSeq)
        {
            int n = maxSeq - seenSeq;
            if (n <= 0) return "已是最新";
            return n == 1 ? "有 1 条新公告" : "有 " + n + " 条新公告";
        }

        /// <summary>
        /// 诊断开关的裁决（两个哨兵文件同时存在时以"永不弹"为准 —— 一个文件就能止血，
        /// 不该被另一个文件抵消）。
        /// </summary>
        internal static bool AutoShowAllowed(bool noNoticeFile, bool alwaysFile)
        {
            if (noNoticeFile) return false;
            return true;
        }
    }
}
