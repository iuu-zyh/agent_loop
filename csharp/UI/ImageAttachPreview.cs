/// <summary>
/// 输入框上方的**图片附件预览条**（用户要求：「否则根本不知道有没有上传图片」）。
///
/// 为什么需要它：图片走「路径」通道，输入框里只有一行路径文本；截图粘贴落盘成临时文件后
/// 更是连文件名都没有意义。**没有预览 = 玩家无法判断到底附上了没有**。
///
/// 布局契约（二次修正，用户明确三条）：
///   · **左对齐**，不居中；
///   · **多图从左到右**依次排列；
///   · **不要任何染色/滤镜**，缩略图原样显示。
///
/// 实现要点（全在规避风险，没有一处动 AB 预制体）：
///   · **不改预制体**：运行时在输入框**同级**新建节点。父级可能是 LayoutGroup，故加
///     `LayoutElement.ignoreLayout = true` —— 布局组会跳过它，原布局零扰动。
///   · **容器同宽同锚点**：预览容器的 anchors/sizeDelta.x 与输入框保持一致 ⇒ 它的左缘
///     **就是**输入框的左缘，"左对齐"自然成立，不需要任何 world→local 坐标换算。
///     格子再以 pivot.x=0 从容器左缘依次排开 ⇒ 从左到右。
///   · **绘制顺序**：Unity UI 里**子节点画在父节点之上**。首版把描边做成 cell 的子节点，
///     结果那层半透明描边**整个盖住了缩略图**，实机表现为"整张图蒙了一层绿"
///     （用户反馈"绿色滤镜很丑"）。现结构改为：cell（空壳）→ 兄弟 Border（先画）+ Photo
///     （后画），描边只在照片外沿露出 2px。
///   · **只在路径集合变化时重建**（调用方用输入文本做键去重）：解码整图不便宜，绝不能每帧跑。
///   · 全函数 try/catch + 失败静默降级：预览挂掉不该影响发图。
///   · 仅 Unity 主线程调用。
/// </summary>
using System;
using System.Collections.Generic;
using UnityEngine;
using UnityEngine.UI;

namespace AgentLoopBridge
{
    internal static class ImageAttachPreview
    {
        private const int ThumbSide = 72;      // 缩略图边长（px）
        private const int ThumbPad = 6;        // 缩略图间距
        private const int DecodeSide = 160;    // 解码边长（> ThumbSide，留缩放余量）
        private const float Gap = 8f;          // 与输入框的垂直间距
        private const float BorderPx = 1.5f;   // 描边宽度（描边在照片外沿露出这么多）

        private static GameObject _root;
        private static readonly List<Texture2D> _texs = new List<Texture2D>();
        private static string _key;            // 当前显示的路径集合指纹（去重用）

        /// <summary>显示/刷新预览。paths 为空 → 隐藏。key 由调用方给（一般是输入框文本），
        /// 与上次相同则**直接返回**（避免重复解码）。</summary>
        public static void Sync(InputField input, string key, List<string> paths, int maxCount)
        {
            try
            {
                if (string.IsNullOrEmpty(key) || paths == null || paths.Count == 0)
                {
                    if (_root != null) Hide();
                    _key = null;
                    return;
                }
                if (key == _key && _root != null) return;   // 没变，不重建
                Hide();
                _key = key;
                if (input == null) return;
                var irt = input.GetComponent<RectTransform>();
                if (irt == null || irt.parent == null) return;

                int n = Mathf.Min(paths.Count, Mathf.Max(1, maxCount));
                float rowW = n * ThumbSide + (n - 1) * ThumbPad;

                // 容器：与输入框**同锚点、同宽度** ⇒ 左缘即输入框左缘（左对齐由此天然成立）
                var go = new GameObject("ImageAttachPreview");
                _root = go;
                var rt = go.AddComponent<RectTransform>();
                rt.SetParent(irt.parent, false);
                var le = go.AddComponent<LayoutElement>();   // ★ 让布局组跳过它，原布局零扰动
                le.ignoreLayout = true;
                rt.anchorMin = irt.anchorMin;
                rt.anchorMax = irt.anchorMax;
                rt.pivot = irt.pivot;
                rt.sizeDelta = new Vector2(irt.sizeDelta.x, ThumbSide);
                // 让容器**下缘**落在输入框**上缘**之上 Gap 处（一般式，对任意 pivot 都成立）：
                //   容器下缘 = 容器pos.y − ThumbSide*pivot.y
                //   输入框上缘 = 框pos.y + 框高*(1−pivot.y)
                float py = irt.pivot.y;
                rt.anchoredPosition = new Vector2(irt.anchoredPosition.x,
                    irt.anchoredPosition.y + irt.rect.height * (1f - py) + Gap + ThumbSide * py);
                go.transform.SetAsLastSibling();             // 画在输入框之上

                int made = 0;
                for (int i = 0; i < paths.Count && made < n; i++)
                {
                    int sw, sh;
                    var tex = ImageInput.LoadThumb(paths[i], DecodeSide, out sw, out sh);
                    if (tex == null) continue;
                    _texs.Add(tex);
                    MakeCell(rt, tex, made);                 // made 即序号 ⇒ 从左到右
                    made++;
                }
                if (made == 0) { Hide(); _key = null; }
                else
                {
                    // 连位置一起打：预览"日志说有、屏幕上没看到"时，靠这行能区分是没建还是建歪了
                    try
                    {
                        ModMain.P("[ImageInput] 预览 " + made + " 张（输入框上方·左对齐）pos=" +
                                  rt.anchoredPosition.x.ToString("F0") + "," + rt.anchoredPosition.y.ToString("F0") +
                                  " 行宽=" + rowW.ToString("F0") + " 父=" + rt.parent.name +
                                  " 框高=" + irt.rect.height.ToString("F0") + " pivot=" + py.ToString("F1"));
                    }
                    catch { }
                }
            }
            catch (Exception e)
            {
                try { ModMain.P("[ImageInput] 预览构建失败: " + e.Message); } catch { }
                Hide();
            }
        }

        /// <summary>
        /// 一格缩略图。**结构必须是两兄弟**：
        ///     cell（纯壳）
        ///       ├─ Border（先画，比 cell 每边大 BorderPx）
        ///       └─ Photo （后画，正好铺满 cell）→ 盖住描边，只在四周露出 BorderPx 的环
        /// 首版把 Border 做成 cell 的子节点、RawImage 挂在 cell 上 —— Unity UI **子节点画在父节点
        /// 之上**，于是描边把照片整个盖住，实机就是"整张图蒙了一层绿"。
        /// </summary>
        private static void MakeCell(RectTransform parent, Texture2D tex, int index)
        {
            var cell = new GameObject("Thumb" + index);
            var crt = cell.AddComponent<RectTransform>();
            crt.SetParent(parent, false);
            crt.anchorMin = crt.anchorMax = new Vector2(0f, 0.5f);   // 贴容器左缘
            crt.pivot = new Vector2(0f, 0.5f);                       // pivot.x=0 ⇒ 从左到右依次排
            crt.sizeDelta = new Vector2(ThumbSide, ThumbSide);
            crt.anchoredPosition = new Vector2(index * (ThumbSide + ThumbPad), 0f);

            // ① 描边（先画）：中性深色细环 —— 白底截图贴浅色面板上，没描边看不出边界。
            //    用中性色而不是主题绿：绿描边一旦因绘制顺序出问题就会变成"绿色滤镜"，很丑。
            var bd = new GameObject("Border");
            var brt = bd.AddComponent<RectTransform>();
            bd.AddComponent<CanvasRenderer>();
            brt.SetParent(crt, false);
            brt.anchorMin = Vector2.zero; brt.anchorMax = Vector2.one;
            brt.offsetMin = new Vector2(-BorderPx, -BorderPx);
            brt.offsetMax = new Vector2(BorderPx, BorderPx);
            var bimg = bd.AddComponent<Image>();
            bimg.color = new Color(0.12f, 0.12f, 0.14f, 0.55f);
            bimg.raycastTarget = false;

            // ② 照片（后画，盖在上面）—— 原样显示，**不做任何染色**
            var ph = new GameObject("Photo");
            var prt = ph.AddComponent<RectTransform>();
            ph.AddComponent<CanvasRenderer>();
            prt.SetParent(crt, false);
            prt.anchorMin = Vector2.zero; prt.anchorMax = Vector2.one;
            prt.offsetMin = Vector2.zero; prt.offsetMax = Vector2.zero;
            var raw = ph.AddComponent<RawImage>();
            raw.texture = tex;
            raw.color = Color.white;      // ★ 必须显式给白：非白会当整体染色
            raw.raycastTarget = false;
        }

        /// <summary>销毁预览节点与其纹理（纹理是我们 new 的，必须自己 Destroy，否则泄漏）。</summary>
        public static void Hide()
        {
            try { if (_root != null) UnityEngine.Object.Destroy(_root); } catch { }
            _root = null;
            for (int i = 0; i < _texs.Count; i++)
            {
                try { if (_texs[i] != null) UnityEngine.Object.Destroy(_texs[i]); } catch { }
            }
            _texs.Clear();
        }
    }
}
