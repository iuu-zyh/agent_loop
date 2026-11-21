/// <summary>
/// 历史**分页窗口**的纯逻辑核（零 Unity、零游戏类型 ⇒ 可编成 net8.0 单测）。
///
/// ── 它解决什么 ────────────────────────────────────────────────────────────
/// 真机：单聊窗 575 条历史，全部建成行之后**常驻 575 个行对象**
/// （每行 ≈ 8 个 Graphic：头像 RectMask2D+RawImage、气泡 Image、Text、几个 Layout 件）。
/// 开窗卡顿已经由 `HistoryBuildCore` 的分帧构建解决了，但**建完之后仍然卡** ——
/// 因为那 575 个对象一直挂在 `Content` 下参与布局与渲染。
/// 玩家观察得很准：「消息少的话就不会卡，但是消息一多就很卡」。
///
/// ── 方案：分页窗口 ────────────────────────────────────────────────────────
/// 只渲染**最近 N 条**（`FirstPageRows`），上方留一个"加载更早"入口；
/// 用户点它（或滚到顶）时把窗口往前扩一页。
///
/// **不虚拟化**（不回收屏幕外的行）：本仓的滚动/折叠/删除选区逻辑都是围绕
/// "行对象稳定存在"写的，虚拟化要重写渲染层，代价与收益不相称（同 `HistoryBuildCore`
/// 文件头对"虚拟列表"的判断）。分页把常驻行数从 575 压到 40，已经拿掉了 93% 的开销。
///
/// ── 数据来源：**服务端分页**（11-21 二次定案，推翻了本地切片）─────────────
/// 第一版设计是"`open_chat` 拿全量 + 本地只渲染最近 N 条"。用户否决，理由是
/// **每次开窗仍要把全部历史传一遍**（真机 42.1KB / 169 条），"等很久"一点没省。
///
/// 现在：`open_chat` 只取最近 `HistoryPageTurns` 个回合；更早的由
/// `get_history(before_seq, before_turn)` 按游标拉回来，走 `ChatWindow.InsertEarlier`
/// 插到顶部。本文件仍然只提供**纯算术**（切在哪 / 要不要入口 / 插入后滚动怎么补偿），
/// 与数据从哪来无关 —— 所以两种方案下这些判据都成立。
///
/// ── 为什么单位是"条目"而不是"行" ──────────────────────────────────────────
/// 一个条目可能产出 0 行（被过滤器丢掉）、1 行（普通气泡）、或多行（工具折叠组）。
/// 窗口按**条目下标**切，因为插入历史时要按原顺序 `ApplyHistoryItem`；
/// 若按"行"切，就没法在插入后还原出正确的条目边界。
/// </summary>
namespace AgentLoopBridge
{
    internal static class HistoryWindowCore
    {
        /// <summary>首屏渲染的条目数。40 ≈ 填满一屏（视口高约 620 单位，一行约 50）再多一些余量，
        /// 用户进来就能往上翻一点而不立刻触发加载。</summary>
        public const int FirstPageRows = 40;

        /// <summary>每次"加载更早"追加的条目数。与首屏同量级：翻页次数少、每次都不卡。</summary>
        public const int PageRows = 40;

        /// <summary>窗口切在哪：条目总数 <paramref name="total"/>、只保留最后
        /// <paramref name="page"/> 条时，起始下标是多少。
        ///
        /// `total <= page` ⇒ 0（全放得下，没有截断）；否则 `total - page`。
        /// 负总数/负页长一律当 0（防御：调用方传脏值不该算出负下标）。</summary>
        public static int StartIndex(int total, int page)
        {
            if (total <= 0) return 0;
            if (page <= 0) return total;          // 页长 0 = 一条都不显示（= 全在"更早"里）
            if (total <= page) return 0;
            return total - page;
        }

        /// <summary>这一份历史要不要截断（= 需不需要"加载更早"入口）。
        ///
        /// 判据是**条目数**而不是"估算行数"：条目数在拿到 `JArray` 那一刻就有，
        /// 估算行数要先建才知道（鸡生蛋，同 `HistoryBuildCore.ShouldDefer` 的取舍）。
        /// </summary>
        public static bool NeedsPaging(int total, int page)
        {
            if (total <= 0) return false;
            if (page <= 0) return total > 0;
            return total > page;
        }

        /// <summary>上一页的页长（"加载更早"一次扩多少）。
        /// 首次从 <see cref="FirstPageRows"/> 开始，之后每页 <see cref="PageRows"/>。 */
        public static int NextPage(int currentPage)
        {
            return currentPage <= 0 ? FirstPageRows : currentPage + PageRows;
        }

        /// <summary>给"加载更早"那一行用的文案（空串 = 不该显示）。
        ///
        /// 说清"还剩多少"很重要：只写"加载更早"时，用户不知道还有 500 条还是 5 条，
        /// 点了又要等。<paramref name="shown"/> = 当前已显示的条目数。</summary>
        public static string MoreLabel(int total, int shown, bool loading)
        {
            if (!NeedsPaging(total, shown)) return "";
            int remain = total - shown;
            if (remain < 0) remain = 0;
            if (loading) return "正在加载更早的消息…";
            return "↑ 上划加载更早的消息（还有 " + remain + " 条）";
        }

        /// <summary>
        /// 插入历史后要让视口**停在原处**（不跳）。
        ///
        /// 这是分页最容易出 bug 的地方：往 `Content` 顶部插入 N 行会让内容整体变高，
        /// 而 `ScrollRect` 的 `verticalNormalizedPosition` 是**比例**（0=底 1=顶），
        /// 内容变高后同样的比例对应的是**不同的绝对位置** ⇒ 画面会往上跳一段。
        ///
        /// 解法：插入前记下"离底部多少像素"，插入后按新的内容高度换算回比例。
        /// 返回要写进 `verticalNormalizedPosition` 的值（0..1）。
        ///
        /// 参数全部是同一把尺子（像素 / 与 rect 无关的比值）：
        ///   `prevScrollPx`    插入前“离底部”的像素距离（= 内容高 - 视口高 - 已滚距离）
        ///   `addedHeightPx`   插入行占的总高度（= 插入后内容高 - 插入前内容高）
        ///   `viewportH`       视口高
        ///   `contentH`        插入**后**的内容总高
        ///
        /// 退化情形（读不到高度、内容比视口还矮）一律返回 1（= 顶）：
        /// 那时"保持位置"没有意义，而 1 是 `verticalNormalizedPosition` 在
        /// "内容不够长"时的自然值，不会把画面拽到别处。
        /// </summary>
        public static float ScrollAfterPrepend(float prevScrollPx, float addedHeightPx,
                                               float viewportH, float contentH)
        {
            if (contentH <= 0f || viewportH <= 0f) return 1f;
            float range = contentH - viewportH;
            if (range <= 0.5f) return 1f;                  // 内容还没视口高：没得滚，等价于顶
            float newScrollPx = prevScrollPx + addedHeightPx;
            if (newScrollPx < 0f) newScrollPx = 0f;
            if (newScrollPx > range) newScrollPx = range;
            float v = 1f - newScrollPx / range;            // 0=底 1=顶
            if (v < 0f) v = 0f;
            if (v > 1f) v = 1f;
            return v;
        }
    }
}
