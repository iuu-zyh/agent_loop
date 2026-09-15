/// <summary>
/// 悬停气泡引擎（纯 Unity 视图件，不依赖主工程类型）。
///
/// 用法：_tip = HoverTip.Ensure(host, canvasRoot, prefabTemplateOrNull);
///       _tip.Register(rect, "说明文字"); —— 登记若干悬停区（整行/控件父节点）
///       _tip.Clear();                    —— 登记整体失效（列表重建时），随后重新 Register
///       _tip.ResetHover();               —— 只藏气泡不清登记（切 Tab 等）
///
/// 检测：Update 轮询 RectangleContainsScreenPoint。**相机必须随画布**：Overlay 传 null，
///       ScreenSpaceCamera/WorldSpace 必须传画布相机——传错不报错、只是**恒不命中**（气泡一个都不出）。
///       真机定案：游戏 UIMgr 给 AB 面板挂的 Canvas 是 ScreenSpaceCamera（Player.log dump 实证：
///       `Canvas mode=ScreenSpaceCamera`），只有代码自建面板（ConfigUiBuilder）才是 Overlay——
///       同一份 HoverTip 于是「预览/代码版有气泡、AB 版一个都没有」。现改为运行期从画布解析相机。
///       判模式取**根 Canvas**（嵌套 Canvas 的 renderMode 只是序列化残留、不生效）；
///       定位锚点另需 = 画布 pivot（见 Show）。
/// IL2CPP 下比 IPointerEnter 接口注入——需 InterfaceImplementation 注册、失败静默——稳得多。
/// 行为：悬停 0.3s 浮出（防扫过闪烁）→ 跟随鼠标右下 → 贴右/下缘自动翻转 → 移出/区域隐藏即收起。
/// 气泡本体：预制体模板（BG/Tooltip）优先，缺失时经 ConfigUiBuilder.BuildTooltipTemplate 现造。
/// IL2CPP：AddComponent 前需 RegisterTypeInIl2Cpp&lt;HoverTip&gt;()（ModMain 负责）。
/// </summary>
using System;
using System.Collections.Generic;
using UnityEngine;
using UnityEngine.UI;

namespace AgentLoopBridge
{
#if MELONLOADER
    public class HoverTip : MonoBehaviour
    {
        /// <summary>IL2CPP 互操作标准构造（Unhollower 按此签名实例化）</summary>
        public HoverTip(System.IntPtr ptr) : base(ptr) { }
#else
    public class HoverTip : MonoBehaviour
    {
#endif
        /// <summary>`Go` 是**登记时缓存**的：`Component.gameObject` 在 Il2CppInterop 下**每次都 new 一个包装体**
        /// （无对象池），放在每帧循环里就是十几条 × 60fps 的小对象分配（复核修）。</summary>
        private struct Entry { public RectTransform Rect; public GameObject Go; public string Text; }

        private readonly List<Entry> _entries = new List<Entry>();
        private GameObject _tip;          // 气泡实例（预制体模板或运行时现造）
        private Text _tipText;
        private RectTransform _canvasRt;  // 定位参考系（面板 Canvas 根）
        private Canvas _canvas;           // 定位画布（解析相机用）
        private Camera _cam;              // 画布相机（Overlay=null）
        private bool _camReady;           // 相机已解析（含「确认是 Overlay」这一结论）
        private string _camNote = "未解析";
        private int _hoverIdx = -1;       // 当前悬停项（-1=无）
        private float _hoverSince;
        private bool _shown;
        private bool _loggedHit;          // 首次命中只记一行（成功路径留痕）
        private bool _loggedEntries;      // 登记总数只报一次（重建后重报）
        private bool _loggedShowFail;     // 浮出失败只记一行
        private const float TipDelay = 0.3f;   // 悬停 0.3s 才浮出，防扫过列表闪烁
        private const float TipWidth = 260f;

        /// <summary>诊断日志：预览工程不编 ModMain（见文件头 #if MELONLOADER 同规约）</summary>
        private static void Diag(string msg)
        {
#if MELONLOADER
            try { ModMain.P("[HoverTip] " + msg); } catch { }
#endif
        }

        /// <summary>取/建宿主上的 HoverTip 并完成气泡装配。canvasRoot=定位参考（面板 Canvas 根）；
        /// prefabTemplate=预制体里的气泡模板（BG/Tooltip），null 则代码现造。</summary>
        public static HoverTip Ensure(Component host, GameObject canvasRoot, GameObject prefabTemplate)
        {
            var h = host != null ? (host.GetComponent<HoverTip>() ?? host.gameObject.AddComponent<HoverTip>()) : null;
            if (h == null) return null;
            h.Setup(canvasRoot, prefabTemplate);
            return h;
        }

        public void Setup(GameObject canvasRoot, GameObject prefabTemplate)
        {
            if (_tip != null || canvasRoot == null) return;
            var go = prefabTemplate;
            string src = go != null ? "预制体模板" : "代码现造";
            if (go == null) go = ConfigUiBuilder.BuildTooltipTemplate(canvasRoot.transform);
            if (go == null) { Diag("装配失败：气泡模板既无预制体节点、现造也返回 null"); return; }
            // 统一挂到 Canvas 根下，定位按屏幕坐标换算（预制体里模板挂在 BG 下也无所谓）
            if (go.transform.parent != canvasRoot.transform)
                go.transform.SetParent(canvasRoot.transform, false);
            _tip = go;
            var txtTr = go.transform.Find("TooltipText");
            _tipText = txtTr != null ? txtTr.GetComponent<Text>() : go.GetComponentInChildren<Text>(true);
            var rt = go.GetComponent<RectTransform>();
            if (rt != null) rt.pivot = new Vector2(0f, 1f);   // 以左上角为定位锚
            go.SetActive(false);
            // 事故① 旧写法 `canvasRoot.transform as RectTransform` 在本环境**静默返回 null**
            // （IL2CPP 铁律，见 UiRects 注释）→ `_canvasRt` 恒 null → `Update()` 第一行就 return →
            // 悬停检测代码从未执行过：登记全在、`_camNote` 永远停在"画布未挂（下帧重试）"、
            // **气泡一个都不出**，且装配日志之后一行诊断都没有（真机现场正是如此）。
            _canvasRt = UiRects.Of(canvasRoot.transform);
            _canvas = null; _cam = null; _camReady = false;   // 画布/相机改为运行期解析（见 ResolveCamera）
            ResolveCamera();
            // 成功路径也要留痕：装配信息一行给出「有没有气泡、能不能定位」的全部判据
            Diag("装配：模板=" + src + " 文本=" + (_tipText != null ? "有" : "**无**")
                 + " 画布=" + _camNote + " 宿主=" + canvasRoot.name);
        }

        /// <summary>
        /// 解析定位画布的渲染相机（**这是气泡能不能出的命门**）。
        /// Overlay 必须传 null、ScreenSpaceCamera/WorldSpace 必须传画布相机——
        /// 传错的后果不是报错，而是 RectangleContainsScreenPoint 拿屏幕坐标当世界坐标，
        /// 恒返回 false：登记全在、Update 照跑、气泡一个都不出（真机事故）。
        /// 画布可能晚于本方法挂上（游戏 UIMgr 自己 AddComponent），故未找到时留待下帧重试。
        /// </summary>
        private void ResolveCamera()
        {
            try
            {
                if (_canvas == null && _canvasRt != null) _canvas = _canvasRt.GetComponent<Canvas>();
                if (_canvas == null && _canvasRt != null) _canvas = _canvasRt.GetComponentInParent<Canvas>();
                if (_canvas == null) { _camNote = "画布未挂（下帧重试）"; return; }   // 不置 _camReady
                // ⚠ 判模式必须看**根 Canvas**：子 Canvas 的 renderMode 只是序列化残留，实际渲染模式随根
                // （Unity 规定嵌套 Canvas 的 renderMode 不起作用）。只看本节点会把「根是 ScreenSpaceCamera」
                // 的子节点误判成 Overlay → 相机又传回 null → 又变回"一个气泡都不出"。
                var root = _canvas;
                try { var rc = _canvas.rootCanvas; if (rc != null) root = rc; } catch { }   // 互操作异常不拖垮取相机
                if (root.renderMode == RenderMode.ScreenSpaceOverlay)
                {
                    _cam = null;
                    _camReady = true;
                    _camNote = "Overlay(相机=null)";
                    return;
                }
                var c = _canvas.worldCamera;
                if (c == null && root != _canvas) c = root.worldCamera;
                if (c == null) c = Camera.main;   // 兜底：WorldSpace 画布未设 event camera 时
                _cam = c;
                _camReady = true;
                _camNote = root.renderMode + "(本节点=" + _canvas.renderMode + " 相机=" + (c != null ? c.name : "**缺失**") + ")";
            }
            catch (Exception e)
            {
                // 复核修 异常路径**不能**置 `_camReady=true`：一次瞬时互操作异常
                // （`rootCanvas`/`worldCamera` 取值）会让 ScreenSpaceCamera 画布**永久**拿 null 相机
                // = 恒不命中、气泡再也不出，且只有一行日志、之后 Update 不再重试。
                // 保持未就绪继续重试，但**有界**：`MaxCamTries` 次后放弃并明确打一行，免得每帧空转。
                _camTries++;
                _camNote = "解析异常(第" + _camTries + "次):" + e.Message;
                if (_camTries >= MaxCamTries)
                {
                    _camReady = true;
                    Diag("相机解析连续失败 " + _camTries + " 次，放弃（气泡将不可用）：" + _camNote);
                }
            }
        }

        private int _camTries;
        private const int MaxCamTries = 120;   // 约 2 秒（60fps）

        /// <summary>登记一条悬停区（rect 通常传整行：控件所在父节点）</summary>
        public void Register(RectTransform rect, string text)
        {
            if (rect == null || string.IsNullOrEmpty(text)) return;
            _entries.Add(new Entry { Rect = rect, Go = rect.gameObject, Text = text });
        }

        /// <summary>清空全部登记并收起气泡（列表重建时用；随后需重新 Register）</summary>
        public void Clear()
        {
            if (_entries.Count > 0) Diag("登记清空：原 " + _entries.Count + " 条（列表重建）");
            _entries.Clear();
            _loggedEntries = false;   // 重登记后再报一次总数
            ResetHover();
        }

        /// <summary>只收起气泡、保留登记（切 Tab 等场景）</summary>
        public void ResetHover()
        {
            _hoverIdx = -1;
            if (_shown) Hide();
        }

        private void Update()
        {
            if (_tip == null || _canvasRt == null) return;
            if (!_camReady) ResolveCamera();          // 画布晚挂：解析到为止（代价一次 GetComponent/帧）
            if (_entries.Count > 0 && !_loggedEntries)
            {
                _loggedEntries = true;
                Diag("登记就绪：" + _entries.Count + " 条 画布=" + _camNote);   // 悬停检测的全部前提，一行给全
            }
            Vector2 mp = Input.mousePosition;
            int hit = -1;
            string text = null;
            for (int i = 0; i < _entries.Count; i++)
            {
                var e = _entries[i];
                if (e.Rect == null || e.Go == null || !e.Go.activeInHierarchy) continue;   // 面板/页隐藏自然失效
                if (RectTransformUtility.RectangleContainsScreenPoint(e.Rect, mp, _cam)) { hit = i; text = e.Text; break; }
            }
            if (hit != _hoverIdx)
            {
                _hoverIdx = hit;
                _hoverSince = Time.unscaledTime;
                if (_shown) Hide();
            }
            if (hit >= 0 && !_loggedHit)
            {
                _loggedHit = true;
                Diag("首次命中 区域#" + hit + " 相机=" + _camNote + " 文本=" + Short(text));
            }
            if (hit >= 0 && !_shown && Time.unscaledTime - _hoverSince >= TipDelay)
                Show(text, mp);
        }

        private static string Short(string s)
        {
            if (string.IsNullOrEmpty(s)) return "(空)";
            return s.Length > 24 ? s.Substring(0, 24) + "…" : s;
        }

        private void Show(string text, Vector3 mousePos)
        {
            if (_tip == null || _tipText == null || _canvasRt == null)
            {
                if (!_loggedShowFail)
                {
                    _loggedShowFail = true;
                    Diag("浮出失败：模板=" + (_tip != null) + " 文本=" + (_tipText != null) + " 画布=" + (_canvasRt != null));
                }
                return;
            }
            _tipText.text = text ?? "";
            var rt = _tip.GetComponent<RectTransform>();
            if (rt == null) return;
            // 锚点必须对齐「画布 pivot」：ScreenPointToLocalPointInRectangle 给的是**以画布 pivot 为原点**
            // 的局部坐标，而 anchoredPosition 是**以自身锚点为原点**——只有锚点==画布 pivot 时两套坐标同系。
            // 画布 pivot 未必是 (0.5,0.5)（预制件根节点常见 (0,0)）；写死 (0.5,0.5) 会让气泡整体偏
            // 半个画面，从屏幕外定位 = 等于「没有气泡」（与相机传错的症状一模一样，且同样静默）。
            rt.anchorMin = _canvasRt.pivot;
            rt.anchorMax = _canvasRt.pivot;
            rt.sizeDelta = new Vector2(TipWidth, Mathf.Max(30f, _tipText.preferredHeight + 16f));
            if (!RectTransformUtility.ScreenPointToLocalPointInRectangle(_canvasRt, mousePos, _cam, out var lp))
                return;
            // 默认挂鼠标右下；贴右缘翻左侧、贴下缘翻上方，再夹进画布
            var rect = _canvasRt.rect;
            float h = rt.rect.height;
            float offX = 18f, offY = -18f;
            if (lp.x + offX + TipWidth > rect.xMax - 2f) offX = -(TipWidth + 18f);
            if (lp.y + offY - h < rect.yMin + 2f) offY = 18f;
            lp += new Vector2(offX, offY);
            lp.x = Mathf.Clamp(lp.x, rect.xMin + 2f, rect.xMax - TipWidth - 2f);
            lp.y = Mathf.Clamp(lp.y, rect.yMin + 2f, rect.yMax - h - 2f);
            rt.anchoredPosition = lp;
            _tip.transform.SetAsLastSibling();   // 盖在最上层
            _tip.SetActive(true);
            _shown = true;
        }

        private void Hide()
        {
            _shown = false;
            if (_tip != null) _tip.SetActive(false);
        }
    }
}
