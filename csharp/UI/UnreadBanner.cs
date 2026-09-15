/// <summary>
/// 未读传音横幅（HUD 级常驻宿主，**纯提示、不可点**）—— NPC 主动开口落在"不能当面弹窗"的场合
/// （异地 / 玩家正忙 / 正开着别人的对话窗）时，给一次显眼的视觉提示；未读红点体系不变（错过不丢）。
///
/// 为什么另建宿主：旧版 `UnreadBanner` 是**通讯录面板 canvas 的子节点**
/// （`ContactUiBuilder` 建、`AbContactPanel` 绑），面板一关就跟着 OFF；而且 09-10 把常驻职责
/// 从 `ContactPresenter` 搬到 `ContactDuty` 时，**横幅的"显示"这一半丢了**——全工程无一处
/// `SetActive(true)` / 写文本，只剩 `ContactPresenter` 的"到点隐藏"（`_bannerUntil` 只被读、
/// 从未被赋值）。故本次补齐：独立常驻 Canvas 挂 `g.root`（游戏根，**非 Canvas**，避免嵌套画布被
/// 父级 renderMode/scale 吃掉），与三个面板彻底解耦。旧节点保留在 AB 预制件里但不再使用。
///
/// 交互约定（用户 拍板）：**纯提示、不可点** → 不建 Button、**不挂 GraphicRaycaster**，
/// 完全不参与输入拾取（战斗中/菜单里弹出也不会误触、不抢 EventSystem 焦点）。
/// 显示 8s 自动隐藏；期间再来一条 → 刷新内容并重置计时。
///
/// 线程：仅游戏主线程（`ContactDuty` 的 UiEvent 回调天然满足）。
/// IL2CPP：纯 GameObject/Component 构建，**不新增 MonoBehaviour 类型**（免 RegisterTypeInIl2Cpp）。
/// </summary>
using System;
using UnityEngine;
using UnityEngine.UI;

namespace AgentLoopBridge
{
    internal static class UnreadBanner
    {
        /// <summary>显示时长（秒）。用户拍板沿用旧版 8s。</summary>
        private const float ShowSeconds = 8f;
        /// <summary>排序层：高于游戏 HUD（order≈20），低于我们的三个面板（3000/3050）。</summary>
        private const int SortingOrder = 2400;
        /// <summary>顶部居中、略低于 HUD 顶栏（避开操作热区，也不压住游戏自己的顶部信息条）。</summary>
        private const float TopOffset = -56f;
        /// <summary>横幅宽度。按 13px 字号算：可用宽 = 宽 - 34(左) - 12(右)，中文约 13px/字，
        /// 故 480 宽 ≈ 33 字预算（抬头「某某」传音：约 7 字 + 正文）。</summary>
        private const float BannerWidth = 480f;
        /// <summary>正文预览基准字数（单行）。多条时再按 <see cref="CountSuffixBudget"/> 压缩，
        /// 否则「（共 N 条）」会把正文挤出框外（Overflow 模式会溢出横幅）。</summary>
        private const int PreviewChars = 22;
        /// <summary>「（共 N 条）」占用的字数预算。</summary>
        private const int CountSuffixBudget = 8;
        private static readonly Color UnreadRed = UnreadStore.UnreadRed;   // #FF3B30，与红点/角标同源（UnreadStore）

        private static GameObject _root;    // 常驻 canvas（挂 g.root 下，随场景销毁 → Unity fake-null 自动判缺）
        private static GameObject _banner;  // 横幅本体（显隐对象）
        private static Text _text;          // 正文
        private static float _until;        // 自动隐藏时刻（Time.unscaledTime）
        private static bool _ticking;       // 帧回调只注册一次（场景重建时随宿主一起重建）

        /// <summary>显示一条未读提示（主线程）。npc=发信 NPC，text=正文（会被截断成单行预览）。</summary>
        public static void Show(string npc, string text)
        {
            if (string.IsNullOrEmpty(npc)) return;
            try
            {
                if (!EnsureBuilt()) return;
                int count = 1;
                try { var it = UnreadStore.Peek(npc); if (it != null && it.Count > 0) count = it.Count; } catch { }
                _text.text = Preview(npc, text, count);
                _until = Time.unscaledTime + ShowSeconds;
                if (!_banner.activeSelf) _banner.SetActive(true);
                EnsureTick();
            }
            catch (Exception e) { ModMain.P("[UnreadBanner] Show: " + e.Message); }
        }

        /// <summary>显示一条**系统**提示（非 NPC 传音）：连接中断 / 正在重启 Python / 已放弃重启等。
        /// 复用同一条横幅（同层、同 8s 自动隐藏），只是抬头换成「系统」并且正文预算给得更足
        /// （系统文案没有"某某传音"那层语境，玩家需要看完整句子才知道该做什么）。
        /// 线程：仅游戏主线程（调用方 BrainLink 由 g.timer.Frame 驱动，满足）。</summary>
        public static void ShowSystem(string text)
        {
            if (string.IsNullOrEmpty(text)) return;
            try
            {
                if (!EnsureBuilt()) return;
                string body = Clean(text);
                const int budget = 34;      // 480 宽 - 左右边距 - 抬头「系统」≈ 34 字
                if (body.Length > budget) body = body.Substring(0, budget) + "…";
                _text.text = body;
                _until = Time.unscaledTime + ShowSeconds;
                if (!_banner.activeSelf) _banner.SetActive(true);
                EnsureTick();
            }
            catch (Exception e) { ModMain.P("[UnreadBanner] ShowSystem: " + e.Message); }
        }

        /// <summary>帧回调：到点隐藏（宿主被销毁后什么都不做，下次 Show 会重建）。</summary>
        private static void Tick()
        {
            try
            {
                if (_banner == null) return;
                if (!_banner.activeSelf) return;
                if (Time.unscaledTime >= _until) _banner.SetActive(false);
            }
            catch (Exception e) { ModMain.P("[UnreadBanner] Tick: " + e.Message); }
        }

        private static void EnsureTick()
        {
            if (_ticking) return;
            try { g.timer.Frame(new Action(Tick), 1, true); _ticking = true; }
            catch (Exception e) { ModMain.P("[UnreadBanner] EnsureTick: " + e.Message); }
        }

        /// <summary>建宿主（懒建；宿主随场景销毁后下一次 Show 自动重建）。失败返回 false（只记日志）。</summary>
        private static bool EnsureBuilt()
        {
            if (_banner != null) return true;
            _root = null; _banner = null; _text = null; _ticking = false;   // 场景重建：全部重挂
            try
            {
                var gameRoot = g.root;
                if (gameRoot == null) return false;
                var host = new GameObject("AgentLoopUnreadBanner");
                host.transform.SetParent(gameRoot.transform, false);
                var canvas = host.AddComponent<Canvas>();
                canvas.renderMode = RenderMode.ScreenSpaceOverlay;
                canvas.sortingOrder = SortingOrder;
                canvas.overrideSorting = true;
                var scaler = host.AddComponent<CanvasScaler>();
                scaler.uiScaleMode = CanvasScaler.ScaleMode.ScaleWithScreenSize;
                scaler.referenceResolution = new Vector2(1920f, 1080f);
                scaler.matchWidthOrHeight = 0.5f;
                // 刻意不挂 GraphicRaycaster：纯提示，不参与任何输入拾取

                var bannerGo = new GameObject("Banner");
                bannerGo.transform.SetParent(host.transform, false);
                var rt = bannerGo.AddComponent<RectTransform>();
                rt.anchorMin = rt.anchorMax = rt.pivot = new Vector2(0.5f, 1f);
                rt.sizeDelta = new Vector2(BannerWidth, 46f);
                rt.anchoredPosition = new Vector2(0f, TopOffset);
                var img = bannerGo.AddComponent<Image>();
                img.sprite = ChatUiBuilder.MakeSlicedSprite(new Color(0.08f, 0.09f, 0.11f, 0.94f), 12, 5);
                img.type = Image.Type.Sliced;
                img.color = Color.white;
                img.raycastTarget = false;

                var dotRt = MakeImage("BannerDot", bannerGo.transform, ChatUiBuilder.MakeCircleSprite(UnreadRed, 32));
                dotRt.anchorMin = dotRt.anchorMax = dotRt.pivot = new Vector2(0f, 0.5f);
                dotRt.sizeDelta = new Vector2(10f, 10f);
                dotRt.anchoredPosition = new Vector2(18f, 0f);

                _text = MakeText("BannerText", bannerGo.transform);
                var trt = _text.rectTransform;
                trt.anchorMin = Vector2.zero;
                trt.anchorMax = Vector2.one;
                trt.offsetMin = new Vector2(34f, 0f);
                trt.offsetMax = new Vector2(-12f, 0f);

                _root = host;
                _banner = bannerGo;
                bannerGo.SetActive(false);
                ModMain.P("[UnreadBanner] 常驻宿主已建（g.root 下，order=" + SortingOrder +
                          "，纯提示不可点，显示 " + ShowSeconds + "s）");
                return true;
            }
            catch (Exception e)
            {
                ModMain.P("[UnreadBanner] EnsureBuilt 失败: " + e.Message);
                _root = null; _banner = null; _text = null;
                return false;
            }
        }

        // ---------- 小工具（ChatUiBuilder 的 MakeImage/MakeText 是 private，这里各留一份最小实现） ----------

        private static RectTransform MakeImage(string name, Transform parent, Sprite sprite)
        {
            var go = new GameObject(name);
            go.transform.SetParent(parent, false);
            var rt = go.AddComponent<RectTransform>();
            var img = go.AddComponent<Image>();
            img.sprite = sprite;
            img.color = Color.white;
            img.type = Image.Type.Simple;
            img.raycastTarget = false;
            return rt;
        }

        private static Text MakeText(string name, Transform parent)
        {
            var go = new GameObject(name);
            go.transform.SetParent(parent, false);
            go.AddComponent<RectTransform>();
            go.AddComponent<CanvasRenderer>();
            var txt = go.AddComponent<Text>();
            txt.text = "";
            txt.fontSize = 13;
            txt.color = Color.white;
            txt.alignment = TextAnchor.MiddleLeft;
            txt.font = ChatUiBuilder.ResolveSharedFont();   // 中文动态字体（SimHei 回退链，见 ChatUiBuilder）
            txt.raycastTarget = false;
            txt.horizontalOverflow = HorizontalWrapMode.Overflow;
            txt.verticalOverflow = VerticalWrapMode.Overflow;
            return txt;
        }

        /// <summary>横幅文案：`「姜萌」传音：唐郎，可还记得我？…`（多条标「共 N 条」）。
        /// 单行预览：换行压成空格、去非 BMP 字符（表情在游戏字体里无字形，留着是方块）、按字数截断。</summary>
        private static string Preview(string npc, string raw, int count)
        {
            string body = Clean(raw);
            int budget = PreviewChars - (count > 1 ? CountSuffixBudget : 0);
            if (budget < 4) budget = 4;
            if (body.Length > budget) body = body.Substring(0, budget) + "…";
            string head = "「" + npc + "」传音";
            if (count > 1) head += "（共 " + count + " 条）";
            return body.Length > 0 ? head + "：" + body : head;
        }

        /// <summary>单行化：换行压成空格、去非 BMP 字符（表情在游戏字体里无字形，留着是方块）、
        /// 去控制符。NPC 传音与系统提示共用（从 Preview 里提出来）。</summary>
        private static string Clean(string raw)
        {
            string body = (raw ?? "").Replace("\r", " ").Replace("\n", " ").Trim();
            var sb = new System.Text.StringBuilder(body.Length);
            foreach (char ch in body)
            {
                if (char.IsHighSurrogate(ch) || char.IsLowSurrogate(ch)) continue;
                if (ch < ' ') continue;
                sb.Append(ch);
            }
            return sb.ToString();
        }
    }
}
