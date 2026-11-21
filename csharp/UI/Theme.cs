/// <summary>
/// 跨面板共享的**深色主题**样式常量（10-22 主题统一）。
///
/// ── 为什么存在 ────────────────────────────────────────────────────────────
/// 对话（ChatUiBuilder）与设置（ConfigUiBuilder）两个深色面板历史上各自抄了一份色板，
/// 连变量名都撞车（两边都有 `TextDim`，值恰好一样）。"恰好一样"就是漂移的前夜：
/// 下一次只改一处，两个面板同屏就开始分家（本仓对付这类问题的老药方：单一来源）。
///
/// ── 收录规矩（克制，别做成调色盘大全）────────────────────────────────────
///   · 只收**至少两个面板共用**、且值已逐位对齐过的色；
///   · 面板专属色（气泡蓝/奶白、滚动区黑、通讯录整套浅色）**留在各自 builder**——
///     通讯录是刻意对标的白主题参考图，硬并进深色系反而毁掉设计意图；
///   · 改这里 = 同时改所有深色面板，动手前先想清楚是不是真要全局生效。
///
/// 数值来源：2026-10-22 从 ChatUiBuilder/ConfigUiBuilder 原样提取（一个字节没动），
/// tests/test_theme_contract.py 把数值钉死在提取时的原值上——漂了会红。
/// </summary>
using UnityEngine;

namespace AgentLoopBridge
{
    internal static class Theme
    {
        /// <summary>深色面板底（95% 不透明）。</summary>
        public static readonly Color DarkPanelBg = new Color(0.043f, 0.043f, 0.075f, 0.95f);

        /// <summary>控件底（按钮/输入框/开关轨道）。</summary>
        public static readonly Color DarkCtrlBg = new Color(0.16f, 0.18f, 0.26f, 1f);

        /// <summary>控件按下/激活态。</summary>
        public static readonly Color DarkCtrlBgActive = new Color(0.24f, 0.27f, 0.38f, 1f);

        /// <summary>主文字（近白）。</summary>
        public static readonly Color DarkTextLight = new Color(0.94f, 0.95f, 0.98f, 1f);

        /// <summary>弱文字（占位/说明/状态条）。</summary>
        public static readonly Color DarkTextDim = new Color(0.60f, 0.62f, 0.70f, 1f);

        /// <summary>成功态绿（状态条"已保存"一类）。</summary>
        public static readonly Color DarkTextOk = new Color(0.55f, 0.85f, 0.60f, 1f);

        // ==================================================================
        // 宣纸浅色主题（10-23 须知面板）—— **刻意与上面那套深色并存，不合并**
        //
        // 为什么另立一套、而不是复用或改深色：须知是一整页长文，深底白字读三段以上就发晕；
        // 而设置面板的 AB 预制件底图本来就是宣纸（`panel_bg.png.png`，米白纸纹 + 墨色云纹角饰），
        // 「须知」从设置窗打开、用同一套纸底墨字才连贯。两套同屏不会撞：须知是独立画布，
        // 开在对话/设置之上，不与它们的控件并排比较。
        //
        // 数值口径 = `docs/release/ui-skin-prompts.md` §2「配置面板」的宣纸基准（那节早就定好这套色，
        // 只是从来没人写进代码）；`tests/test_theme_contract.py::test_paper_values_pinned` 钉死。
        // ==================================================================

        /// <summary>宣纸页底 #F5F1E4（缺底图时的兜底纯色，也是卡片的对照底色）。</summary>
        public static readonly Color PaperPageBg = new Color(0.961f, 0.945f, 0.894f, 1f);

        /// <summary>卡片底 #FEFCF7（比纸底更亮一档 ⇒ 卡片"浮"在纸上）。</summary>
        public static readonly Color PaperCardBg = new Color(0.996f, 0.988f, 0.969f, 1f);

        /// <summary>发丝线 / 卡片描边 #EAE5D8（1px，靠它把相邻卡片分开）。</summary>
        public static readonly Color PaperHairline = new Color(0.918f, 0.898f, 0.847f, 1f);

        /// <summary>墨色正文 #3A362E。</summary>
        public static readonly Color PaperInk = new Color(0.227f, 0.212f, 0.180f, 1f);

        /// <summary>弱墨（日期、页脚、说明文）#8A8578。</summary>
        public static readonly Color PaperInkDim = new Color(0.541f, 0.522f, 0.471f, 1f);

        /// <summary>青瓷点缀 #4CA69C（选中页签下划线、版本胶囊、「新」标记、键帽上的字）。</summary>
        public static readonly Color PaperAccent = new Color(0.298f, 0.651f, 0.612f, 1f);

        /// <summary>键帽底 #FFFDF7（比卡片再亮一点，模拟实心白键帽）。</summary>
        public static readonly Color PaperKeycapBg = new Color(1f, 0.992f, 0.969f, 1f);
    }
}
