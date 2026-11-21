/// <summary>
/// 对话窗拖动的**纯算术**件（零 Unity ⇒ net8.0 可单测）：屏幕像素位移 → 画布单位增量，
/// 以及把窗口钉在"至少露出可视区"的合法域里。
///
/// ── 为什么单独抽出来 ──────────────────────────────────────────────────────
/// 拖动本体（Input 轮询、命中判定）没法脱离 Unity 测，但**算错的全在这两小段**：
///   ① 不除 CanvasScaler 的 scaleFactor → 非 1080p 分辨率下窗口"游得比手快/慢"
///      （本项目 CanvasScaler=ScaleWithScreenSize，anchoredPosition 是画布单位、
///      Input.mousePosition 是屏幕像素，两套尺子）；
///   ② 不做钳制 → 一拽就飞出屏外，只剩一条边能再抓回来（玩家视角="窗口丢了"）。
/// 这两类错误在编辑器里（恰好 1.0 缩放、屏幕够大）全都不显形，装机才炸——正是
/// 单测该钉的东西。
///
/// ── 两套锚点，两个函数（11-21 修 bug 时拆开）──────────────────────────────
/// **本仓的对话窗有两种构建模式，窗口锚点不同，用错公式的后果是"只能往一个方向拖"**：
///
///   · `ClampAnchored`  —— 右中锚 `anchor=pivot=(1, 0.5)`：**代码版构建器**（`ChatUiBuilder`
///     的 `Window`，`anchoredPosition=(-24,0)` 右贴边）。合法域 `x ∈ [winW-viewW, 0]`。
///   · `ClampToViewport` —— **任意点锚**（`anchorMin==anchorMax`）：**AB 预制体版**（`BG`
///     是 `anchor=pivot=(0.5,0.5)` 居中锚，1200×800）。
///
///   11-21 之前的实机 bug 就是拿 `ClampAnchored` 去钳 AB 版的居中锚 BG：
///   把 1200×800 的窗塞进 1920×1080 的视口，公式算出 `x ∈ [-720, 0]`（正确是 `±360`）
///   ⇒ **窗口只能往左拖，往右一格都拖不动**，玩家视角=「拖动坏了」。
///
/// ── 通用公式（`ClampToViewport` 用的）────────────────────────────────────
/// 设父级（画布根，全屏 stretch）pivot=(0.5,0.5) ⇒ 父级坐标域 `[-viewW/2, +viewW/2]`。
/// 点锚 `a`、自身 pivot `p` 时，窗口在父级坐标里的左右缘：
///     left  = (a.x - 0.5)·viewW + pos.x - p.x·winW
///     right = left + winW
/// 要求 `left ≥ -viewW/2` 且 `right ≤ +viewW/2`，解得
///     pos.x ∈ [ -viewW/2 - (a.x-0.5)·viewW + p.x·winW ,
///                viewW/2 - (a.x-0.5)·viewW + p.x·winW - winW ]
/// y 轴同构。窗口比视口还大时区间反转（lo > hi）⇒ 取中点（居中），
/// 因为"两边都露一点"比"贴死某一侧、另一侧完全看不见"更容易再抓住。
/// </summary>

namespace AgentLoopBridge
{
    internal static class DragCore
    {
        /// <summary>屏幕像素位移 → 画布单位位移（scaleFactor 异常时原样返回，绝不吞拖动）。</summary>
        public static float ScreenToCanvas(float screenDelta, float scaleFactor)
        {
            return scaleFactor > 0.001f ? screenDelta / scaleFactor : screenDelta;
        }

        /// <summary>把**右中锚**（`anchor=pivot=(1,0.5)`）窗口的偏移钳进可视域。单位：全部画布单位。
        ///
        /// 只给代码版构建器用；AB 预制体版（居中锚）走 <see cref="ClampToViewport"/>。
        /// 超宽窗贴右（`x=0`）是历史行为，为不破坏既有单测而保留——
        /// 通用版在同样情形下取中点，两者仅在"窗口比视口还大"这个异常态下不同。</summary>
        public static void ClampAnchored(float x, float y, float winW, float winH,
                                         float viewW, float viewH,
                                         out float ox, out float oy)
        {
            if (viewW >= winW)
            {
                ox = x;
                if (ox > 0f) ox = 0f;
                float minX = winW - viewW;
                if (ox < minX) ox = minX;
            }
            else ox = 0f;

            float hi = (viewH - winH) * 0.5f;
            if (hi < 0f) { oy = 0f; return; }
            oy = y;
            if (oy > hi) oy = hi;
            if (oy < -hi) oy = -hi;
        }

        /// <summary>把**任意点锚**窗口的 anchoredPosition 钳进可视域（AB 预制体版对话窗用）。
        ///
        /// 前提：`anchorMin == anchorMax`（点锚）、父级是 pivot(0.5,0.5) 的全屏 stretch 节点
        /// ——正是 `UIChatAi`（根，归一化后全屏）→ `BG`（居中锚）这一层。
        /// 拉伸锚（`anchorMin != anchorMax`）不适用本函数：那种窗口的尺寸由父级决定，
        /// 位置由 offset 决定，没有"拖到哪"这个概念。
        ///
        /// 参数全部是画布单位（与 `anchoredPosition` 同一把尺子），
        /// 屏幕像素要先过 <see cref="ScreenToCanvas"/>。</summary>
        public static void ClampToViewport(float x, float y, float winW, float winH,
                                           float viewW, float viewH,
                                           float anchorX, float anchorY,
                                           float pivotX, float pivotY,
                                           out float ox, out float oy)
        {
            // 锚点相对父级中心的偏移：anchor=0.5 → 0（居中），anchor=1 → +viewW/2（贴右）
            float ax = (anchorX - 0.5f) * viewW;
            float ay = (anchorY - 0.5f) * viewH;

            // 窗口左缘落在 ax + pos - pivot*winW；解出 pos 的两端
            float loX = -viewW * 0.5f - ax + pivotX * winW;
            float hiX = viewW * 0.5f - ax + pivotX * winW - winW;
            ox = ClampAxis(x, loX, hiX);

            float loY = -viewH * 0.5f - ay + pivotY * winH;
            float hiY = viewH * 0.5f - ay + pivotY * winH - winH;
            oy = ClampAxis(y, loY, hiY);
        }

        /// <summary>单轴钳制；区间反转（窗口比视口大）时取中点 = 居中。
        /// 区间退化成一个点（窗口恰好等于视口）时 lo == hi，两个分支结果相同。</summary>
        private static float ClampAxis(float v, float lo, float hi)
        {
            if (lo > hi) return (lo + hi) * 0.5f;
            if (v < lo) return lo;
            if (v > hi) return hi;
            return v;
        }
    }
}
