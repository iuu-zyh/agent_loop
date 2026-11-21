/// <summary>
/// 对话窗位置/尺寸的**判定核**（零 Unity、零 Newtonsoft、零文件 IO）—— 与 `NoticeCore`
/// / `DragCore` / `TimeSepCore` 同款：判据全在这里，`csharp/tests/WindowLayoutCoreTests`
/// 直接链接本文件在 WSL 里跑断言；磁盘读写与 Unity 侧应用在 `WindowLayoutStore` / `ChatWindow`。
///
/// ── 它回答三个问题 ────────────────────────────────────────────────────────
///   ① 盘上读出来的数**还能不能用**（NaN / Inf / 负宽 / 离谱尺寸一律判废，退回出厂值）；
///   ② 尺寸**夹进当前视口**（窗口不该比屏幕还大——那是"看不见的窗"）；
///   ③ 这次和上次**是不是同一个位置**（没动就别写盘：拖动松手才落盘，一次拖动只写一次）。
///
/// ── 为什么位置要跨局记住（11-21 用户定案）────────────────────────────────
/// 原先的契约是"位置不跨局持久（重开回默认右贴边）"。用户实测后改为**跨局记住**：
/// 「位置和尺寸跨局记住，这就和配置里面的设置一样的啊」。于是写盘，但**不写 config.json**——
/// 那个文件归 Python 的 `config_loader` 独占解释，C# 侧 `ModConfigFile` 只读不写，
/// 两边同时写一个文件必然互相覆盖。落盘走独立文件 `window_layout.json`，与
/// `notice_seen.json` 同层同款（数据根 = `ModAssets\`）。
///
/// ── 数值口径 ──────────────────────────────────────────────────────────────
/// X/Y 是 `BG.anchoredPosition`（**画布单位**，不是屏幕像素——两者差一个 CanvasScaler 的
/// scaleFactor，见 `DragCore.ScreenToCanvas`）。W/H 是 `BG.sizeDelta`。
/// 这三个数只有在"锚点是点锚"时才有稳定含义；AB 预制体里 `BG` 是居中点锚
/// （`anchor=pivot=(0.5,0.5)`），所以存下来的值换个分辨率仍然成立，
/// 应用时再按当前视口夹一次即可。
/// </summary>
namespace AgentLoopBridge
{
    /// <summary>一份窗口版式快照。`HasPos/HasSize` 区分"盘上真的写过"与"默认 0"——
    /// 首次安装时 `X=Y=0` 恰好等于出厂居中位，不能靠数值判"有没有存过"。</summary>
    internal struct WindowLayout
    {
        internal float X, Y;
        internal float W, H;
        internal bool HasPos;
        internal bool HasSize;

        internal static WindowLayout None
        {
            get { return new WindowLayout(); }
        }
    }

    internal static class WindowLayoutCore
    {
        /// <summary>落盘文件名（数据根，与 `config.json` / `notice_seen.json` 同层）。</summary>
        internal const string FileName = "window_layout.json";

        // ---- 出厂尺寸：与 `UIChatAi.prefab` 里 BG 的 m_SizeDelta 一致，改这里必须同步预制体 ----
        internal const float DefaultW = 1200f;
        internal const float DefaultH = 800f;

        // ---- 尺寸下限：B 阶段（缩放）会用到，A 阶段只做校验不应用 ----
        /// <summary>最小宽：头像 80 + 行间距 6 + 气泡最小 96 + 安全边 24×2 ≈ 230，
        /// 再留出滚动条与两侧 15 的内缩 ⇒ 取 360（再窄就会出横向裁切）。</summary>
        internal const float MinW = 360f;
        /// <summary>最小高：顶栏 60 + 输入区 110 + 至少两行消息 ≈ 300，取 320。</summary>
        internal const float MinH = 320f;

        /// <summary>写盘去抖：位置差小于这个值就当没动过（浮点噪声，不值得一次磁盘写）。</summary>
        internal const float SameEpsilon = 0.5f;

        /// <summary>数值可用吗（NaN/Inf 会把 UGUI 布局整个算崩，必须挡在门口）。</summary>
        internal static bool Finite(float v)
        {
            return !float.IsNaN(v) && !float.IsInfinity(v);
        }

        /// <summary>
        /// 校验并夹住尺寸。`maxW/maxH` = 当前视口（画布单位）。
        /// 返回 false = 盘上的尺寸不可用，调用方应保持出厂值（**不要**用夹过的半个值，
        /// 那会得到"宽是新的、高是旧的"这种半吊子形态）。
        ///
        /// **上限只在知道视口时才夹**：读盘那一刻还没有画布尺寸，传 0/NaN 表示"未知"，
        /// 此时只做下限校验、不夹上限 —— 否则 4K 屏上存的 2000 宽会被出厂值 1200 砍掉。
        /// </summary>
        internal static bool SanitizeSize(float w, float h, float maxW, float maxH,
                                          out float ow, out float oh)
        {
            ow = DefaultW; oh = DefaultH;
            if (!Finite(w) || !Finite(h)) return false;
            if (w < MinW || h < MinH) return false;          // 小于下限 = 写坏了，整份作废
            ow = w; oh = h;
            // 上限夹到视口：窗口比屏幕还大就等于"看不见"，钳制域也会退化成居中锁死
            if (Finite(maxW) && maxW >= MinW && ow > maxW) ow = maxW;
            if (Finite(maxH) && maxH >= MinH && oh > maxH) oh = maxH;
            return true;
        }

        /// <summary>校验位置（只判数值可用；夹进视口要 Unity 的 rect，在 `DragCore.ClampToViewport`）。</summary>
        internal static bool SanitizePos(float x, float y, out float ox, out float oy)
        {
            ox = 0f; oy = 0f;
            if (!Finite(x) || !Finite(y)) return false;
            ox = x; oy = y;
            return true;
        }

        /// <summary>位置变了没有（用于"松手才落盘、且只在真动过时写"）。</summary>
        internal static bool PosChanged(float x1, float y1, float x2, float y2)
        {
            return System.Math.Abs(x1 - x2) > SameEpsilon || System.Math.Abs(y1 - y2) > SameEpsilon;
        }

        /// <summary>尺寸变了没有。</summary>
        internal static bool SizeChanged(float w1, float h1, float w2, float h2)
        {
            return System.Math.Abs(w1 - w2) > SameEpsilon || System.Math.Abs(h1 - h2) > SameEpsilon;
        }
    }
}
