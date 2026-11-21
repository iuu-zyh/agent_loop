/// <summary>
/// 两枚「须知」入口按钮的运行时创建（10-23）。
///
/// ## 为什么单独一个文件、而且全是"克隆 + 改坐标"
/// 对话窗（`UIChatAi`）与设置窗（`UIConfigAi`）的预制件都在官方 Unity 工程里，
/// 改它们要重打 `.ab`（手工步骤 + Unity 许可证风险），改错了会连累整个窗。
/// 所以两枚按钮一律**运行时克隆同排的既有按钮**（`ToolPanel.MakeToolButton` 的成熟手法），
/// 连字号/字体/描边/Image 精灵/Button 过渡色都逐字相同 —— 不会出现"这颗按钮的字比隔壁大一号"。
///
/// ## 两枚按钮的位置（坐标全部实测自预制件，见 `docs/reference/csharp-iron-laws.md` §E）
/// 对话窗顶栏（右锚点，右缘坐标；实测 `BG/*` 子节点）：
/// <code>
///   ✕ CloseBtn(-0, 宽40)  ⚙ ConfigBtn(-39.8, 宽60)  ✎ DeleteBtn(-105.8, 宽64)
///   压缩 CompactBtn(-177.8, 宽64)  拉人 InviteBtn(-249.8)  踢人 KickBtn(-321.8)
/// </code>
/// 「须知」要**避开 ⚙ 与 ✎ 之间那 6px 缝隙**（-39.8 到 -105.8 之间只有 6px），
/// 因此放在**压缩键左边**（`-249.8`）—— 那一格原本是「工具」钮的位置（`ToolPanel.ToolBtnX`），
/// 而工具只在单聊显示、拉人/踢人只在群聊显示，两者永不同屏。于是：
///   · 单聊：… 压缩(-177.8) ｜ 工具(-249.8) ｜ **须知(-321.8)** ｜ 踢人(群聊时 -321.8，永不同屏)
///   · 群聊：… 拉人(-249.8) 踢人(-321.8) —— 须知与它们在群聊同屏会撞，
///     故 `SetRoomButtonsVisible(true)` 时把须知钮一并隐藏（见 `ChatWindow` 的调用点）。
///
/// 设置窗标题栏（`BG/Title`，实测 `CloseBtn` 右锚点满高、宽 36）：
///   在 ✕ 左边加一枚 56×36 的「须知」文字钮（`Title` 高 42，够放）。
/// </summary>
using System;
using UnityEngine;
using UnityEngine.UI;

namespace AgentLoopBridge
{
    internal static class NoticeButton
    {
        /// <summary>对话窗顶栏「须知」钮的名字（`ChatWindow` 显隐群聊按钮时要能找到它）。</summary>
        internal const string ChatButtonName = "NoticeBtn";
        /// <summary>设置窗标题栏「须知」钮的名字。</summary>
        internal const string ConfigButtonName = "NoticeBtn";

        /// <summary>对话窗顶栏须知钮的右缘坐标（= 工具钮左边 8px）。</summary>
        private const float ChatBtnX = -321.8f;
        private const float ChatBtnW = 64f;

        private static GameObject _chatBtn;
        private static GameObject _cfgBtn;

        /// <summary>
        /// 在对话窗顶栏造「须知」钮（克隆 `BG/ConfigBtn`）。重复调用幂等（已存在直接返回）。
        /// 失败只记一行日志，绝不影响开窗。
        /// </summary>
        internal static GameObject EnsureChatButton(RectTransform bg)
        {
            try
            {
                if (bg == null) return null;
                if (Alive(_chatBtn) && _chatBtn.transform.parent == bg) return _chatBtn;
                var old = bg.Find(ChatButtonName);
                if (old != null) { _chatBtn = old.gameObject; return _chatBtn; }

                var src = bg.Find("ConfigBtn");
                GameObject go;
                if (src != null)
                {
                    go = UnityEngine.Object.Instantiate(src.gameObject) as GameObject;
                    if (go != null) go.name = ChatButtonName;
                }
                else go = null;
                if (go == null)
                {
                    // 老 AB / 代码版没有 ConfigBtn：纯色兜底钮（与 ToolPanel.MakeFallbackButton 同族）
                    go = MakeFallback(bg, ChatButtonName, "须知", ChatBtnW);
                    if (go == null) return null;
                }
                else
                {
                    go.transform.SetParent(bg, false);
                    var rt = go.GetComponent<RectTransform>();
                    if (rt != null)
                    {
                        rt.anchorMin = rt.anchorMax = new Vector2(1f, 1f);
                        rt.pivot = new Vector2(1f, 0.5f);
                        rt.sizeDelta = new Vector2(ChatBtnW, 40f);
                        rt.anchoredPosition = new Vector2(ChatBtnX, -20f);
                    }
                    var label = go.GetComponentInChildren<Text>(true);
                    if (label != null) label.text = "须知";
                }
                var btn = go.GetComponent<Button>();
                if (btn != null) ClickUtils.Attach(btn, () => NoticePanel.Show(NoticeCore.TabGuide));
                _chatBtn = go;
                ModMain.P("[Notice] 对话窗顶栏「须知」钮已就位（x=" + ChatBtnX + "，= 工具钮左侧 8px）");
                return go;
            }
            catch (Exception e)
            {
                ModMain.P("[Notice] 对话窗须知钮建失败（不影响其余功能）: " + e.Message);
                return null;
            }
        }

        /// <summary>
        /// 设置窗标题栏造「须知」钮（克隆 `BG/Title/CloseBtn` 再改文案与宽度）。
        /// `title` = `BG/Title` 的 RectTransform。幂等。
        /// </summary>
        internal static GameObject EnsureConfigButton(RectTransform title)
        {
            try
            {
                if (title == null) return null;
                if (Alive(_cfgBtn) && _cfgBtn.transform.parent == title) return _cfgBtn;
                var old = title.Find(ConfigButtonName);
                if (old != null) { _cfgBtn = old.gameObject; return _cfgBtn; }

                var src = title.Find("CloseBtn");
                GameObject go = null;
                if (src != null)
                {
                    go = UnityEngine.Object.Instantiate(src.gameObject) as GameObject;
                    if (go != null)
                    {
                        go.name = ConfigButtonName;
                        go.transform.SetParent(title, false);
                        var rt = go.GetComponent<RectTransform>();
                        if (rt != null)
                        {
                            rt.anchorMin = new Vector2(1f, 0f);
                            rt.anchorMax = new Vector2(1f, 1f);
                            rt.pivot = new Vector2(1f, 0.5f);
                            rt.sizeDelta = new Vector2(56f, 0f);     // 高度随标题栏拉伸
                            rt.anchoredPosition = new Vector2(-40f, 0f);  // ✕（宽36）左边留 4px
                        }
                        var label = go.GetComponentInChildren<Text>(true);
                        if (label != null) { label.text = "须知"; label.fontSize = 14; }
                    }
                }
                if (go == null)
                {
                    go = MakeFallback(title, ConfigButtonName, "须知", 56f);
                    if (go == null) return null;
                    var rt = go.GetComponent<RectTransform>();
                    if (rt != null) rt.anchoredPosition = new Vector2(-40f, 0f);
                }
                var btn = go.GetComponent<Button>();
                if (btn != null) ClickUtils.Attach(btn, () => NoticePanel.Show(NoticeCore.TabGuide));
                _cfgBtn = go;
                ModMain.P("[Notice] 设置窗标题栏「须知」钮已就位（✕ 左侧）");
                return go;
            }
            catch (Exception e)
            {
                ModMain.P("[Notice] 设置窗须知钮建失败（不影响其余功能）: " + e.Message);
                return null;
            }
        }

        /// <summary>对话窗切群聊时把须知钮一起收起（群聊那两格是拉人/踢人，会让位撞车）。</summary>
        internal static void SetChatButtonVisible(bool show)
        {
            try { if (Alive(_chatBtn)) _chatBtn.SetActive(show); } catch { }
        }

        private static GameObject MakeFallback(RectTransform parent, string name, string label, float w)
        {
            try
            {
                var go = new GameObject(name);
                var rt = go.AddComponent<RectTransform>();
                rt.SetParent(parent, false);
                rt.anchorMin = rt.anchorMax = new Vector2(1f, 1f);
                rt.pivot = new Vector2(1f, 0.5f);
                rt.sizeDelta = new Vector2(w, 40f);
                rt.anchoredPosition = new Vector2(ChatBtnX, -20f);
                go.AddComponent<CanvasRenderer>();
                var img = go.AddComponent<Image>();
                img.sprite = ChatUiBuilder.MakeSlicedSprite(Theme.DarkCtrlBg, 12, 4);
                img.type = Image.Type.Sliced;
                img.color = Color.white;
                img.raycastTarget = true;
                var btn = go.AddComponent<Button>();
                btn.targetGraphic = img;
                var t = new GameObject("Label");
                var trt = t.AddComponent<RectTransform>();
                trt.SetParent(rt, false);
                trt.anchorMin = Vector2.zero; trt.anchorMax = Vector2.one;
                trt.offsetMin = Vector2.zero; trt.offsetMax = Vector2.zero;
                t.AddComponent<CanvasRenderer>();
                var txt = t.AddComponent<Text>();
                txt.text = label;
                txt.fontSize = 15;
                txt.color = Theme.DarkTextLight;
                txt.alignment = TextAnchor.MiddleCenter;
                txt.raycastTarget = false;
                try { txt.font = ChatUiBuilder.ResolveSharedFont(); } catch { }
                return go;
            }
            catch (Exception e)
            {
                ModMain.P("[Notice] 兜底钮建失败: " + e.Message);
                return null;
            }
        }

        private static bool Alive(GameObject go)
        {
            if (go == null) return false;
            try { return go != null; } catch { return false; }   // GC 僵尸：判空即抛
        }
    }
}
