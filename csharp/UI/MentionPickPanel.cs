/// <summary>
/// 群聊 @ 候选浮层（11-21）。
///
/// 【它是什么】
/// 玩家在群聊输入框里打 `@`，就在输入框**上方**浮出一列成员名；继续打字按子串过滤。
/// 点一行（或按 ↑↓ 选中后 Enter/Tab）就把 `@查询词` 换成 `@完整名字 `。
///
/// 【为什么浮层在输入框**上方**而不是下方】
/// 输入框贴着窗口底边（`ChatUiBuilder` 把 `ChatInput` 放在 `bottomRt` 里），
/// 下方没有空间 —— 浮层会被窗口裁掉。放上方与微信一致。
///
/// 【为什么自己建树，不复用预制件】
/// 预制件（AB）里没有这个节点，而"重打 AB"是发行流程（见 `docs/rebuild-ab-checklist.md`），
/// 不该为一行浮层付这个代价。本仓已有先例：`ToolPanel` / `NoticePanel` / `CompactEditPanel`
/// 都是运行期用 `UiKit` 现建的树。
///
/// 【它不认识游戏与协议】
/// 本类只做"画一列可点的名字 + 告诉调用方选了哪个"。候选从哪来、选中后怎么改输入框，
/// 都由 `ChatPresenter` 决定 —— 与 `ToolPanel` 只画目录、不解释工具的分层一致。
///
/// 【一个必须写下来的坑】
/// `InputField.onValueChanged` 在**每次**文本变化时触发，而选中候选时我们要**改文本**。
/// 改文本会再次触发 `onValueChanged` ⇒ 若不挡住就会递归（实测表现为候选闪烁、
/// 或光标跳到开头）。挡法在 `ChatPresenter.OnChatInputChanged` 里（`_suppressInputEvent`），
/// 本类不掺和 —— 那是调用方的时序问题，不是绘制问题。
/// </summary>
using System;
using System.Collections.Generic;
using UnityEngine;
using UnityEngine.UI;

namespace AgentLoopBridge
{
    internal static class MentionPickPanel
    {
        // ---- 版式 ----
        private const float RowH = 34f;          // 一行候选
        private const float Gap = 2f;            // 行距
        private const float PadY = 6f;           // 上下内缩
        private const float MinW = 180f;         // 最窄（成员名都很短时）
        private const float MaxW = 340f;         // 最宽（浮层不该比输入框还宽太多）
        private const float CharW = 17f;         // 估算字宽（与候选字号配套，只用来定宽度）

        // ---- 配色（与对话窗同一套浅色底，见 ToolPanel 的取色）----
        private static readonly Color BgColor = new Color(0.964f, 0.960f, 0.956f, 0.99f);
        private static readonly Color RowHot = new Color(0.850f, 0.900f, 0.985f, 1f);
        private static readonly Color RowIdle = new Color(0f, 0f, 0f, 0f);
        private static readonly Color TextColor = new Color(0.10f, 0.11f, 0.16f, 1f);
        private static readonly Color HintColor = new Color(0.44f, 0.45f, 0.52f, 1f);

        // ---- 状态 ----
        private static GameObject _root;
        private static RectTransform _content;
        private static readonly List<GameObject> _rows = new List<GameObject>();
        private static List<string> _names = new List<string>();
        private static int _hot = -1;
        private static Action<string> _onPick;

        /// <summary>
        /// 当前**拥有**这层浮层的 presenter（11-21 修，见 `IsOwnedBy`）。
        ///
        /// 为什么需要：`ChatPresenter` 是 MonoBehaviour，一个面板宿主一个实例。
        /// 实测 Player.log 里同时存在 5 个实例（#1~#5）—— 换 NPC / 重开窗时旧的
        /// presenter 未必立刻销毁，而它们的 `Update()` **每帧都在跑**。
        /// 非群聊实例会走到 "`!IsRoomMode` ⇒ `MentionPickPanel.Hide()`" 那条早退，
        /// 于是**单聊窗每帧都在关掉群聊窗的候选浮层**。
        /// 失败形态完全静默：玩家看到浮层闪一下就没了，或者按回车时它刚好被关掉。
        /// 判据改成"只有所有者能收起"，从根上断掉这条跨实例干扰。
        /// </summary>
        private static object _owner;

        /// <summary>浮层是否正开着（`ChatPresenter` 据此决定要不要抢键盘）。
        ///
        /// ★ 整个判据包在 try 里 ★：`ChatPresenter` 每帧都问一次，而 `_root` 可能是
        /// 一个"已被 Destroy 但字段还没清"的对象（Unity 的销毁是延迟的）。那种对象与
        /// null 比较为 true，但**访问 `activeSelf` 会抛** `MissingReferenceException`。
        /// 每帧抛一次异常会刷满日志并拖慢帧率 —— 这里吞掉并按"没开"处理是安全方向
        /// （最坏结果是补全不弹，而不是每帧报错）。</summary>
        public static bool IsOpen
        {
            get
            {
                try { return _root != null && _root.activeSelf && _names.Count > 0; }
                catch { return false; }
            }
        }

        /// <summary>当前候选个数（测试/日志用）。</summary>
        public static int Count { get { return _names.Count; } }

        /// <summary>当前高亮行（-1 = 没有）。</summary>
        public static int HotIndex { get { return _hot; } }

        /// <summary>
        /// 建树（幂等）。`anchor` = 输入框的 RectTransform，浮层锚在它**上方**。
        /// `onPick` 在玩家选中一行时回调（参数 = 成员名）。
        ///
        /// 为什么挂在 `anchor.parent` 而不是 `anchor` 自己：浮层要盖在输入框**外面**
        /// （上方），挂成子节点会被父级的 `RectMask2D`（滚动区）或布局组裁掉。
        /// 挂到同一父级、用绝对锚点定位，才不会被兄弟布局挤走。
        /// </summary>
        public static void EnsureBuilt(RectTransform anchor, Action<string> onPick, object owner = null)
        {
            _onPick = onPick;
            if (owner != null) _owner = owner;
            if (anchor == null) return;
            // ★ 树还在、但**已经不可用**时必须重建（11-21 复核补）★
            //   `_root != null` 不足以说明它还能用：Unity 的 `Destroy` 是**延迟**的
            //   （帧末才真正销毁），而 IL2CPP 下被销毁的 UnityEngine.Object 与 `null`
            //   比较为 true 但**访问成员会抛**或静默失效。窗口重建（`ResetContent` /
            //   切 NPC / 关开窗）会把父节点整棵换掉，此时旧 `_root` 挂在一个已死的父上
            //   ⇒ 浮层"看起来在、实际不显示"，而且没有任何报错。
            //   判据用"父节点是否还是同一个" —— 那是"这棵树还在原来的位置"的直接证据。
            bool reusable = _root != null && anchor.parent != null
                            && _root.transform != null
                            && _root.transform.parent == anchor.parent;
            if (reusable) return;
            if (_root != null) { try { UnityEngine.Object.Destroy(_root); } catch { } }
            _root = null;
            _content = null;
            _rows.Clear();
            var parent = anchor.parent;
            if (parent == null) return;

            var root = UiKit.MakeRect("MentionPick", parent);
            // 锚点/位置由 `Follow` 负责（它在建完树后立刻调一次，之后尺寸变化也调）。
            // 这里只给一个"能跑起来"的初值：宽度由 `FitWidth` 定，高度由 ContentSizeFitter 撑。
            root.anchorMin = new Vector2(0f, 0f);
            root.anchorMax = new Vector2(0f, 0f);
            root.pivot = new Vector2(0f, 0f);
            root.sizeDelta = new Vector2(MinW, RowH);
            root.gameObject.AddComponent<CanvasRenderer>();
            var bg = root.gameObject.AddComponent<Image>();
            bg.color = BgColor;
            bg.raycastTarget = true;         // 浮层要吃射线：否则点候选会穿透到下面的聊天区

            var vlg = root.gameObject.AddComponent<VerticalLayoutGroup>();
            vlg.spacing = Gap;
            vlg.padding = new RectOffset(6, 6, (int)PadY, (int)PadY);
            vlg.childControlWidth = true;
            vlg.childControlHeight = true;
            vlg.childForceExpandWidth = true;
            vlg.childForceExpandHeight = false;

            var csf = root.gameObject.AddComponent<ContentSizeFitter>();
            csf.verticalFit = ContentSizeFitter.FitMode.PreferredSize;
            csf.horizontalFit = ContentSizeFitter.FitMode.Unconstrained;

            _root = root.gameObject;
            _content = root;
            _root.SetActive(false);
            Follow(anchor);
        }

        /// <summary>把浮层重新贴到输入框上方（窗口尺寸/布局变化后调用）。
        ///
        /// ★ 用**锚点对齐**，不用 `rect.height` ★（11-21 复核改）
        ///   输入框 `ChatInput` 是 stretch 锚定（`anchorMin=0, anchorMax=1`），它的
        ///   `anchoredPosition` 恒为 (0,0)，而 `rect.height` 只有在布局结算之后才是真值 ——
        ///   `EnsureBuilt` 那一帧往往还没结算，读到 0 ⇒ 浮层贴在输入框**中间**而不是上方。
        ///   改成"把浮层的底边锚到输入框的顶边"：这是纯锚点运算，与运行时尺寸无关，
        ///   布局怎么变都自动跟随（也不需要每帧重算）。
        ///
        ///   浮层的 pivot 是 (0,0)（左下角），所以：
        ///     anchorMin/anchorMax 的 y 对齐输入框顶边，pivot.y=0 ⇒ 浮层往上长。
        ///   x 与输入框左缘对齐（输入框在 BottomBar 里左侧起，右侧让开 64 给发送键）。
        /// </summary>
        public static void Follow(RectTransform anchor)
        {
            if (_root == null || anchor == null) return;
            var rt = _root.GetComponent<RectTransform>();
            if (rt == null) return;
            if (anchor.parent == null || rt.parent != anchor.parent) return;   // 不同父级：坐标系不可比

            // ★ 只动**锚点与位置**，绝不动 `offsetMin/offsetMax` ★
            //   本节点上有 `ContentSizeFitter(verticalFit=PreferredSize)`，它每帧都会
            //   重写 `sizeDelta.y`；而设 offset 等于也在写尺寸 ⇒ 两者互相覆盖，
            //   表现为浮层高度在"一行"和"整个列表"之间跳（抖动），或干脆塌成 0 高。
            //   分工：**锚点/位置归本方法**，**尺寸归布局系统**。
            //
            //   锚点取输入框的**顶边**（`anchorMax.y`），pivot 在左下角 ⇒ 浮层从顶边
            //   往上长。x 沿用输入框的横向区间，视觉上左右对齐。
            rt.anchorMin = new Vector2(anchor.anchorMin.x, anchor.anchorMax.y);
            rt.anchorMax = new Vector2(anchor.anchorMax.x, anchor.anchorMax.y);
            rt.pivot = new Vector2(0f, 0f);
            // 底边贴输入框顶边 + 4px 间隙
            rt.anchoredPosition = new Vector2(0f, 4f);
        }

        /// <summary>把候选换成这一批（空列表 = 收起浮层）。</summary>
        public static void SetCandidates(List<string> names, RectTransform anchor)
        {
            try
            {
                _names = names ?? new List<string>();
                _hot = _names.Count > 0 ? 0 : -1;
                if (_root == null) return;
                if (_names.Count == 0)
                {
                    _root.SetActive(false);
                    ClearRows();
                    return;
                }
                ClearRows();
                _root.SetActive(true);
                Follow(anchor);
                FitWidth();

                for (int i = 0; i < _names.Count; i++)
                {
                    int idx = i;      // 闭包捕获：不能直接用 i（循环结束后 i == count）
                    MakeRow(_names[i], idx);
                }
                RepaintHot();
            }
            catch (Exception e)
            {
                try { ModMain.P("[MentionPickPanel] SetCandidates 异常：" + e.Message); } catch { }
            }
        }

        /// <summary>
        /// **无条件收起**（不看 owner）。给"窗口真的关了"这条路径用。
        ///
        /// ★ 为什么不能只靠 `Hide(owner)` ★
        ///   关窗时浮层的 owner 可能是**另一个** presenter（换 NPC 时旧实例可能还活着、
        ///   甚至反过来 —— 该关的一方不是建树的一方）。`Hide(owner)` 在那种情况下会
        ///   直接 return，于是窗口关了、树还在，而它 `raycastTarget = true`
        ///   ⇒ **吃掉聊天区/界面的点击，且完全没有日志**（玩家只会说"点了没反应"）。
        ///   所以"窗口关了"这件事必须能越过 owner 判据。
        ///
        /// 与 `Dispose()` 的区别：这里只隐藏 + 清候选，**不销毁树** ——
        /// 同一个窗口再次打开时可以复用（省一次建树，也避免对象在下一帧被销毁的时序问题）。
        /// </summary>
        public static void HideForce()
        {
            try
            {
                _names = new List<string>();
                _hot = -1;
                _onPick = null;
                _owner = null;
                if (_root != null) _root.SetActive(false);
                ClearRows();
            }
            catch { }
        }

        /// <summary>这层浮层是不是 `owner` 建起来的（没有所有者也返回 true = 不拦）。</summary>
        public static bool IsOwnedBy(object owner)
        {
            return _owner == null || ReferenceEquals(_owner, owner);
        }

        /// <summary>收起浮层（Esc / 发送 / 光标离开 @ 段 / 切走窗口）。
        ///
        /// ★ 只有**所有者**可以收起 ★（见 `_owner` 的注释）：非所有者的 presenter
        ///   每帧都会走到它自己的早退分支，若无条件 Hide 就会互相打架。
        ///   传 `owner` 进来时，非所有者这一击是**空操作**。
        /// </summary>
        public static void Hide(object owner = null)
        {
            if (owner != null && !IsOwnedBy(owner)) return;
            try
            {
                _names = new List<string>();
                _hot = -1;
                if (_root != null) _root.SetActive(false);
                ClearRows();
            }
            catch { }
        }

        /// <summary>键盘上下移动高亮。返回是否消费了这次按键（调用方据此决定要不要继续传）。</summary>
        public static bool MoveHot(int delta)
        {
            if (!IsOpen) return false;
            int next = MentionPickCore.MoveSelection(_hot, delta, _names.Count);
            if (next < 0) return false;
            _hot = next;
            RepaintHot();
            return true;
        }

        /// <summary>当前高亮行的名字（没有则 null）。给调用方打日志/核对用。</summary>
        public static string HotName
        {
            get
            {
                try
                {
                    if (_hot < 0 || _hot >= _names.Count) return null;
                    return _names[_hot];
                }
                catch { return null; }
            }
        }

        /// <summary>确认当前高亮行。返回是否真选了（没有可选项 ⇒ false）。
        ///
        /// ★ `_hot` 无效时**兜底取第一行**，不再直接返回 false ★（11-21 修）
        ///   原来的写法是 `if (!IsOpen || _hot < 0 || _hot >= _names.Count) return false;`。
        ///   而 `_hot` 是会变成 -1 的（`Hide()` 置 -1；`SetCandidates` 遇空列表也置 -1）。
        ///   一旦它是 -1，玩家**明明看见**浮层上有名字、按回车却"什么都没发生"，
        ///   然后这一击落到发送分支 —— 与"选了人却发出去"是同一个观感、同一个后果。
        ///   调用方已在校准；这里再兜一次底，让「浮层开着 ⇒ 回车一定选中某一行」
        ///   成为**不变式**（这也是本方法返回 true 的唯一前提）。
        /// </summary>
        public static bool AcceptHot()
        {
            if (!IsOpen) return false;
            if (_hot < 0 || _hot >= _names.Count) _hot = 0;   // 兜底：选第一行
            string name = _names[_hot];
            var cb = _onPick;
            Hide();
            if (cb != null)
            {
                try { cb(name); } catch (Exception e)
                {
                    try { ModMain.P("[MentionPickPanel] onPick 异常：" + e.Message); } catch { }
                }
            }
            return true;
        }

        /// <summary>销毁整棵树（窗口关闭时）。
        ///
        /// ★ 唯一**不看 owner** 的入口 ★：树是挂在某个窗口节点下的，窗口没了树就必须跟着走
        ///   （否则残留下一个 raycastTarget=true 的浮层，吃掉聊天区点击且无日志）。
        ///   所以这里只认"树在不在"，不认"谁建的"。`_owner` 一并清空，
        ///   下一个建树的 presenter 会自然成为新所有者。
        /// </summary>
        public static void Dispose()
        {
            try
            {
                ClearRows();
                if (_root != null) UnityEngine.Object.Destroy(_root);
            }
            catch { }
            _root = null;
            _content = null;
            _names = new List<string>();
            _hot = -1;
            _onPick = null;
            _owner = null;
        }

        // ---------------------------------------------------------------- 内部

        /// <summary>按最长候选名定宽度。
        ///
        /// ★ 用 `offsetMin.x/offsetMax.x`（左缩进 + 右缩进）而不是 `sizeDelta.x` ★
        ///   因为 `Follow` 把本节点的 `anchorMin.x/anchorMax.x` 设成了输入框的**区间**
        ///   （`0` 到 `1` 这类非零跨度）。在这种锚点下 `sizeDelta.x` 的语义是
        ///   "比锚点区间宽/窄多少"，写死一个像素值会得到"区间宽 + 该值"的结果 ——
        ///   宽度完全不受控。而 offset 是**相对锚点区间的内缩**，语义稳定。
        ///   右侧留 0（宽度 = 区间宽 − 左内缩 − 右内缩），再夹到 [MinW, MaxW]。
        /// </summary>
        private static void FitWidth()
        {
            if (_content == null) return;
            int longest = 0;
            for (int i = 0; i < _names.Count; i++)
                if (_names[i] != null && _names[i].Length > longest) longest = _names[i].Length;
            float w = Mathf.Clamp(longest * CharW + 28f, MinW, MaxW);
            var rt = _content.GetComponent<RectTransform>();
            if (rt == null) return;
            // 锚点区间宽度（= 输入框宽）；用它反推右内缩，使浮层实际宽度 = w
            float span = Mathf.Abs(rt.anchorMax.x - rt.anchorMin.x);
            // ★ 取 RectTransform 必须走 `UiRects.Of`，不许 `as RectTransform` ★
            //   IL2CPP 下 `as` 在类型未注册时会**静默返回 null**（本仓已因此翻车三次，
            //   见 `test_big_portrait_contract.test_no_as_recttransform_anywhere`）。
            var parentRt = UiRects.Of(rt.parent);
            float parentW = parentRt != null ? parentRt.rect.width : 0f;
            if (span <= 0.0001f || parentW <= 0f)
            {
                // 锚点退化（同点锚定）或父级尺寸未知：退回直接设 sizeDelta（这种情形下语义正确）
                rt.sizeDelta = new Vector2(w, rt.sizeDelta.y);
                return;
            }
            float intervalW = span * parentW;
            rt.offsetMin = new Vector2(0f, rt.offsetMin.y);
            rt.offsetMax = new Vector2(-Mathf.Max(0f, intervalW - w), rt.offsetMax.y);
        }

        private static void MakeRow(string name, int idx)
        {
            var rowRt = UiKit.MakeRect("Row" + idx, _content);
            var le = rowRt.gameObject.AddComponent<LayoutElement>();
            le.minHeight = RowH;
            le.preferredHeight = RowH;

            rowRt.gameObject.AddComponent<CanvasRenderer>();
            var bg = rowRt.gameObject.AddComponent<Image>();
            bg.color = RowIdle;
            bg.raycastTarget = true;

            // 点击：用一个透明 Button 承接（比手写 IPointerClickHandler 少一个组件类型注册）。
            // ★ 必须走 `ClickUtils.Attach`，不能直接 `onClick.AddListener(lambda)` ★
            //   IL2CPP 下 `UnityAction` 是 Unhollower 生成的类而非 C# delegate，
            //   直接传 lambda 会 CS1660（本仓已踩过，见 ChatUiBuilder 里 ClickUtils 的注释）。
            var btn = rowRt.gameObject.AddComponent<Button>();
            btn.targetGraphic = bg;
            btn.transition = Selectable.Transition.None;
            ClickUtils.Attach(btn, () =>
            {
                _hot = idx;
                AcceptHot();
            });

            var txt = UiKit.MakeText("Label", rowRt, name, 15f, TextColor, TextAnchor.MiddleLeft);
            UiKit.Stretch(txt.rectTransform, 10f, 0f, 8f, 0f);

            _rows.Add(rowRt.gameObject);
        }

        private static void RepaintHot()
        {
            for (int i = 0; i < _rows.Count; i++)
            {
                if (_rows[i] == null) continue;
                var img = _rows[i].GetComponent<Image>();
                if (img != null) img.color = (i == _hot) ? RowHot : RowIdle;
            }
        }

        private static void ClearRows()
        {
            for (int i = 0; i < _rows.Count; i++)
            {
                try { if (_rows[i] != null) UnityEngine.Object.Destroy(_rows[i]); } catch { }
            }
            _rows.Clear();
        }
    }
}
