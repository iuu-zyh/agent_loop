/// <summary>
/// UGUI 构件工厂 —— **全仓唯一一份**（11-19 批次 4 第 1 步）。
///
/// 【为什么有这个文件】
/// 11-19 复核：同一段"建节点 / 拉满 / 设层 / 找子节点"在 6 个文件里各写了一遍：
///   · `MakeRect` 3 份 + `NewRect` 3 份（同一个东西两个名字）；
///   · `Stretch` 6 份逐字相同，另有 `StretchFill` 2 份只是改了个名；
///   · `SetLayerRecursive` 4 份；
///   · `EnsureEventSystem` 3 份逐字相同；
///   · `FindGo` 3 份 + `FindRt` 3 份 + `FindTr` 3 份（Ab 三面板，走 `Transform.Find`）
///     与 `FindByPath`/`FindGo`/`FindRt` 1 套（公告栏，手工逐段匹配）—— **同一件事两套语义**；
///   · `AsTransform`/`AsRt` 3 份，而 `UiRects.SelfOf` 早就存在却**零调用**。
///
/// 这一批只搬**逐字相同**的，语义一个字不改（第 2~4 步才是参数化 `MakeImage`/`MakeText`）。
///
/// 【两处需要说明的取舍】
/// 1. `MakeRect` 的顺序：`new GameObject` → `AddComponent&lt;RectTransform&gt;` → `SetParent(false)`。
///    旧的 `MakeRect` 是"先挂父级再补组件"、旧的 `NewRect` 是"先补组件再挂父级"。
///    两者终点相同（`SetParent(false)` 把 localScale 归 1、localPosition 保持 0；
///    RectTransform 的默认 sizeDelta 与创建路径无关），这里统一成"先补组件"，
///    与 Unity 自己的 UI 创建顺序一致。**这是本批唯一一处靠推理而非逐字相同的地方**，
///    真机验收时请留意各面板元素位置有无偏移。
/// 2. 查找统一到**手工逐段匹配**那一套（公告栏那套）：它对空段、空节点、异常都短路，
///    而 `Transform.Find` 在路径有多余分隔符时直接给 null。实际使用的路径两者等价。
///
/// 线程：主线程（全部是 UGUI 操作）。
/// </summary>
using System;
using System.Collections.Generic;
using UnityEngine;
using UnityEngine.UI;

namespace AgentLoopBridge
{
    internal static class UiKit
    {
        // ---------------------------------------------------------------- 建节点

        /// <summary>建一个空 UI 节点（已挂到 <paramref name="parent"/> 下）。</summary>
        public static RectTransform MakeRect(string name, Transform parent)
        {
            var go = new GameObject(name);
            var rt = go.AddComponent<RectTransform>();
            rt.SetParent(parent, false);
            return rt;
        }

        // ---------------------------------------------------------------- 建控件

        /// <summary>
        /// 建一张图片节点。`raycast` 决定它**吃不吃饭点**：面板/按钮要 true，
        /// 纯装饰（未读点、分隔线、蒙版底）必须 false，否则会把点击吞掉（无日志）。
        /// </summary>
        public static RectTransform MakeImage(string name, Transform parent, Sprite sprite, Color color,
                                              Image.Type type = Image.Type.Simple, bool raycast = true)
        {
            var go = new GameObject(name);
            var rt = go.AddComponent<RectTransform>();
            rt.SetParent(parent, false);
            go.AddComponent<CanvasRenderer>();
            var img = go.AddComponent<Image>();
            img.sprite = sprite;
            img.type = type;
            img.color = color;
            img.raycastTarget = raycast;
            return rt;
        }

        /// <summary>
        /// 建一个文本节点。**所有文字共用的七件事**在这里：字号（四舍五入）、颜色、对齐、
        /// 中文字体（`ResolveSharedFont` 的三级兜底）、不吃射线、纵向溢出、横向换行。
        /// 三处真实差异做成参数，不许默默统一：
        ///   · `wrap`：公告条/未读横幅要**单行到底**（Overflow），其余换行；
        ///   · `richText`：公告条要关（文案里的 `&lt;` `&gt;` 否则被当标签吞掉）；
        ///   · `lineSpacing`：公告条用 1.2。
        /// </summary>
        public static Text MakeText(string name, Transform parent, string text, float size, Color color,
                                    TextAnchor align, FontStyle style = FontStyle.Normal,
                                    bool wrap = true, bool richText = true, float lineSpacing = 1f,
                                    bool roundSize = true)
        {
            var rt = MakeRect(name, parent);
            rt.gameObject.AddComponent<CanvasRenderer>();
            var t = rt.gameObject.AddComponent<Text>();
            t.text = text ?? "";
            t.fontSize = roundSize ? Mathf.RoundToInt(size) : (int)size;
            t.font = ChatUiBuilder.ResolveSharedFont();
            t.fontStyle = style;
            t.color = color;
            t.alignment = align;
            if (lineSpacing != 1f) t.lineSpacing = lineSpacing;
            t.raycastTarget = false;
            t.horizontalOverflow = wrap ? HorizontalWrapMode.Wrap : HorizontalWrapMode.Overflow;
            t.verticalOverflow = VerticalWrapMode.Overflow;
            t.supportRichText = richText;
            return t;
        }

        // ---------------------------------------------------------------- 布局

        /// <summary>拉满父级（四边贴齐、pivot 居中）。</summary>
        public static void Stretch(RectTransform rt)
        {
            rt.anchorMin = Vector2.zero;
            rt.anchorMax = Vector2.one;
            rt.offsetMin = Vector2.zero;
            rt.offsetMax = Vector2.zero;
            rt.pivot = new Vector2(0.5f, 0.5f);
        }

        /// <summary>四边贴齐并留内缩（左/下/右/上）。</summary>
        public static void Stretch(RectTransform rt, float l, float b, float r, float t)
        {
            rt.anchorMin = Vector2.zero;
            rt.anchorMax = Vector2.one;
            rt.offsetMin = new Vector2(l, b);
            rt.offsetMax = new Vector2(-r, -t);
            rt.pivot = new Vector2(0.5f, 0.5f);
        }

        /// <summary>顶部横条：左右拉伸、顶锚、固定高（距顶 <paramref name="top"/>）。</summary>
        public static void StretchTopBar(RectTransform rt, float left, float right, float top, float height)
        {
            rt.anchorMin = new Vector2(0f, 1f);
            rt.anchorMax = new Vector2(1f, 1f);
            rt.pivot = new Vector2(0.5f, 1f);
            rt.offsetMin = new Vector2(left, -(top + height));
            rt.offsetMax = new Vector2(-right, -top);
        }

        /// <summary>递归设置整个子树到指定层（Canvas 渲染依赖 layer，漏设会不渲染）。</summary>
        public static void SetLayerRecursive(Transform t, int layer)
        {
            if (t == null) return;
            t.gameObject.layer = layer;
            for (int i = 0; i < t.childCount; i++) SetLayerRecursive(t.GetChild(i), layer);
        }

        /// <summary>同上，从 GameObject 起步（公告栏那套的签名）。</summary>
        public static void SetLayerRecursive(GameObject go, int layer)
        {
            if (go == null) return;
            SetLayerRecursive(go.transform, layer);
        }

        /// <summary>没有 EventSystem 就补一个（否则 UGUI 完全不响应输入）。</summary>
        public static void EnsureEventSystem(Transform parent)
        {
            if (UnityEngine.Object.FindObjectOfType<UnityEngine.EventSystems.EventSystem>() != null)
                return;
            var go = new GameObject("AgentLoopChatEventSystem");
            go.transform.SetParent(parent, false);
            go.AddComponent<UnityEngine.EventSystems.EventSystem>();
            go.AddComponent<UnityEngine.EventSystems.StandaloneInputModule>();
        }

        /// <summary>
        /// 把 `UIMgr.CreateUI` 建出来的实例根**归一化**：`UIMgr` 会把它置成 scale=0 / rect=0×0
        /// （真机 dump 实证），scale 停在 0 就是完全不可见。这里统一设 scale=1 + 全屏 stretch。
        ///
        /// 四份逐字相同的实现（三个 AB 面板 + 公告栏）收在这里，唯一差别是日志前缀 ⇒ 参数化。
        /// </summary>
        public static void NormalizeLayout(GameObject go, string tag)
        {
            tag = string.IsNullOrEmpty(tag) ? "" : "[" + tag + "] ";
            try
            {
                var rt = go != null ? UiRects.Of(go.transform) : null;
                if (rt == null) { ModMain.P(tag + "归一化跳过：根无 RectTransform"); return; }
                rt.localScale = Vector3.one;
                rt.anchorMin = Vector2.zero;
                rt.anchorMax = Vector2.one;
                rt.offsetMin = Vector2.zero;
                rt.offsetMax = Vector2.zero;
                rt.anchoredPosition = Vector2.zero;
                ModMain.P(tag + "布局已归一化: scale=1 全屏stretch rect=" + rt.rect.width + "x" + rt.rect.height);
            }
            catch (Exception e) { ModMain.P(tag + "NormalizeLayout: " + e.Message); }
        }

        // ---------------------------------------------------------------- 屏幕坐标 ↔ 画布

        /// <summary>
        /// 取"这个 Transform 所在的**根 Canvas**"的渲染相机 —— 给
        /// `RectTransformUtility.RectangleContainsScreenPoint` / `ScreenPointToLocalPointInRectangle`
        /// / `WorldToScreenPoint` 用。
        ///
        /// **这是全仓踩过两次真机事故的那条**（`HoverTip` 气泡一个都不出、`DramaAiOption`
        /// 屏坐标整个算错）：Overlay 必须传 null，ScreenSpaceCamera/WorldSpace 必须传画布相机；
        /// 传错的后果不是报错，而是**恒不命中**。
        ///
        /// 判模式必须看**根 Canvas** —— Unity 规定嵌套 Canvas 的 `renderMode` 不生效，
        /// 子节点上那个只是序列化残留。真机实证：游戏 UIMgr 给 AB 面板挂的是
        /// `ScreenSpaceCamera`（Player.log：`Canvas mode=ScreenSpaceCamera`），
        /// 而预制体根节点自己序列化的那个不是它。
        ///
        /// 取不到时返回 null（= 当 Overlay 处理）：这是**最不会崩**的降级 ——
        /// 真 Overlay 下正好正确，真 ScreenSpaceCamera 下退化成"判据恒不命中"，
        /// 即回到修之前的行为，而不是把坐标算成别的东西。
        /// </summary>
        public static Camera CanvasCamera(Transform t)
        {
            try
            {
                if (t == null) return null;
                var c = t.GetComponentInParent<Canvas>();
                if (c == null) return null;
                var root = c;
                try { var rc = c.rootCanvas; if (rc != null) root = rc; } catch { }
                if (root.renderMode == RenderMode.ScreenSpaceOverlay) return null;
                var cam = c.worldCamera;
                if (cam == null && root != c) cam = root.worldCamera;
                if (cam == null) cam = Camera.main;      // 兜底：WorldSpace 画布未设 event camera
                return cam;
            }
            catch { return null; }
        }

        // ---------------------------------------------------------------- 建树自检

        // 键值**不在本文件定义**（11-20 收编）：`FrameTicker` 的五张表全以键为索引，
        // 号段必须集中一处才可能被"全仓唯一性"测试守住。历史教训见
        // `FrameTicker.KUIAudit` 的注释（撞号会让先挂的那条成幽灵、后挂那条永不执行）。
        private const int AuditKey = FrameTicker.KUIAudit;
        private static readonly List<KeyValuePair<RectTransform, string>> _auditQueue =
            new List<KeyValuePair<RectTransform, string>>();

        /// <summary>
        /// 面板建完后的**一次**自检（延迟 2 帧，等布局结算）。
        ///
        /// 为什么必须有它：这一层错了**一律不报错**——按钮没 target 就是点不响、
        /// 缩放为 0 就是整屏不见、层级漏设就是"能点但看不见"、单行文字超宽就是穿模。
        /// 全部只能靠人看。所以建完树自检一遍，把"看着正常其实坏了"的形态变成一行日志。
        /// </summary>
        public static void AuditSoon(RectTransform root, string tag)
        {
            if (root == null) return;
            _auditQueue.Add(new KeyValuePair<RectTransform, string>(root, tag));
            FrameTicker.Ensure(AuditKey, "uiaudit", PumpAudit);
        }

        private static int _auditWait;
        private static void PumpAudit()
        {
            if (_auditQueue.Count == 0) { FrameTicker.Stop(AuditKey); return; }
            if (++_auditWait < 2) return;             // 等两帧让布局组/ContentSizeFitter 结算
            _auditWait = 0;
            var batch = new List<KeyValuePair<RectTransform, string>>(_auditQueue);
            _auditQueue.Clear();
            foreach (var it in batch) Audit(it.Key, it.Value);
            if (_auditQueue.Count == 0) FrameTicker.Stop(AuditKey);
        }

        private static void Audit(RectTransform root, string tag)
        {
            try
            {
                int nodes = 0, zero = 0, scale0 = 0, layerBad = 0, buttons = 0, noTarget = 0;
                int raycastImgs = 0, wideText = 0;
                var zeroNames = new List<string>();
                var layerNames = new List<string>();
                var noTargetNames = new List<string>();
                var wideNames = new List<string>();

                // 根自己都没布局（0×0）⇒ 整棵子树的 rect 全是 0，这时报"零尺寸=N"只会误导。
                // 只报一句"根未布局"，尺寸判据整体跳过。
                bool laidOut = true;
                try
                {
                    var rr = root.rect;
                    laidOut = rr.width >= 0.5f && rr.height >= 0.5f;
                }
                catch { }
                if (!laidOut)
                    ModMain.P("[UiKit] 建树自检 tag=" + tag + "：根未布局（0×0），尺寸类判据跳过");

                var stack = new Stack<Transform>();
                stack.Push(root);
                while (stack.Count > 0 && nodes < 800)
                {
                    var tr = stack.Pop();
                    nodes++;
                    var rt = UiRects.Of(tr);
                    var go = tr.gameObject;
                    if (rt != null)
                    {
                        var r = rt.rect;
                        if (laidOut && go.activeSelf && (r.width < 0.5f || r.height < 0.5f))
                        { zero++; Pick(zeroNames, tr.name); }
                        var sc = rt.localScale;
                        if (laidOut && (Mathf.Abs(sc.x) < 0.001f || Mathf.Abs(sc.y) < 0.001f)) scale0++;
                        if (go.layer == 0) { layerBad++; Pick(layerNames, tr.name); }
                    }
                    var txt = go.GetComponent<Text>();
                    if (txt != null && txt.horizontalOverflow == HorizontalWrapMode.Overflow && rt != null)
                    {
                        // 单行到底的文本：量出来的宽超出它自己的框 ⇒ 会压到别的元素上
                        float need = txt.preferredWidth;
                        if (need > rt.rect.width + 1f) { wideText++; Pick(wideNames, tr.name); }
                    }
                    var img = go.GetComponent<Image>();
                    if (img != null && img.raycastTarget) raycastImgs++;
                    var btn = go.GetComponent<Button>();
                    if (btn != null)
                    {
                        buttons++;
                        if (btn.targetGraphic == null) { noTarget++; Pick(noTargetNames, tr.name); }
                    }
                    for (int i = 0; i < tr.childCount; i++) stack.Push(tr.GetChild(i));
                }

                ModMain.P("[UiKit] 建树自检 tag=" + tag + " 节点=" + nodes
                          + " 零尺寸=" + zero + Fmt(zeroNames)
                          + " 缩放0=" + scale0
                          + " 层错=" + layerBad + Fmt(layerNames)
                          + " 按钮=" + buttons + "(无target=" + noTarget + Fmt(noTargetNames) + ")"
                          + " 吃射线图=" + raycastImgs
                          + " 单行超宽=" + wideText + Fmt(wideNames));
            }
            catch (Exception e)
            {
                ModMain.P("[UiKit] 建树自检异常 tag=" + tag + "：" + e.GetType().Name + " " + e.Message);
            }
        }

        /// <summary>只留前 3 个名字：日志要能一眼看出"是哪几个"，但不能把一行撑成十行。</summary>
        private static void Pick(List<string> list, string name)
        {
            if (list.Count < 3) list.Add(name);
        }

        private static string Fmt(List<string> names)
        {
            return names.Count == 0 ? "" : "(" + string.Join(",", names.ToArray()) + ")";
        }

        // ---------------------------------------------------------------- 找节点

        /// <summary>
        /// 按层级路径找节点（`"Window/Scroll/Viewport/Content"`）。
        ///
        /// 为什么不用 `Transform.Find`：它在路径有多余分隔符时直接给 null，且不兜异常；
        /// 这里逐段匹配，空段跳过、空节点与异常一律返回 null —— 调用方本来就要判 null
        /// （判不到就走"实例节点缺失"的装配失败分支）。
        /// </summary>
        public static Transform FindByPath(Transform root, string path)
        {
            if (root == null || string.IsNullOrEmpty(path)) return null;
            try
            {
                var cur = root;
                int start = 0;
                while (start <= path.Length)
                {
                    int slash = path.IndexOf('/', start);
                    string seg = slash < 0 ? path.Substring(start) : path.Substring(start, slash - start);
                    if (seg.Length > 0)
                    {
                        Transform next = null;
                        for (int i = 0; i < cur.childCount; i++)
                        {
                            var c = cur.GetChild(i);
                            if (c != null && c.name == seg) { next = c; break; }
                        }
                        if (next == null)
                        {
                            NoteMissing(root, path, seg);
                            return null;
                        }
                        cur = next;
                    }
                    if (slash < 0) break;
                    start = slash + 1;
                }
                return cur;
            }
            catch { return null; }
        }

        // ---------------------------------------------------------------- 缺失日志

        private const int LogCap = 40;
        private static readonly HashSet<string> _missLogged = new HashSet<string>();
        private static int _missCount;

        /// <summary>
        /// 节点路径找不到 —— **必须留一行**。这是"界面看着正常但功能不响应"的最常见根因
        /// （拿不到节点 → 装配提前返回 → 玩家点按钮毫无反应，而日志里往往一个字都没有）。
        /// 同 (根节点名, 路径) 只打一次，全进程 40 条封顶。
        /// </summary>
        private static void NoteMissing(Transform root, string path, string seg)
        {
            string at = (root != null ? root.name : "?") + " :: " + path;
            if (!_missLogged.Add(at)) return;
            if (++_missCount > LogCap)
            {
                if (_missCount == LogCap + 1)
                    ModMain.P("[UiKit] 节点缺失已达 " + LogCap + " 条上限，后续不再打印");
                return;
            }
            ModMain.P("[UiKit] 节点缺失：" + at + "（断在 \"" + seg + "\"）");
        }

        public static GameObject FindGo(Transform root, string path)
        {
            var t = FindByPath(root, path);
            return t != null ? t.gameObject : null;
        }

        public static RectTransform FindRt(Transform root, string path)
        {
            // ★ 铁律：取 RectTransform 只能走 `UiRects.Of` ★
            //   `Transform as RectTransform` 在本环境静默返回 null（09-13 两次、10-23 第三次、
            //   11-19 气泡宽度第四次），节点明明在也会被判成"缺失"。
            return UiRects.Of(FindByPath(root, path));
        }

        public static T FindComp<T>(Transform root, string path) where T : Component
        {
            var go = FindGo(root, path);
            return go != null ? go.GetComponent<T>() : null;
        }
    }
}
