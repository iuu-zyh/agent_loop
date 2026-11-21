/// <summary>
/// 点击头像 → 全屏放大立绘（屏幕正中；**ESC / 点空白处 / 点关闭** 退出）。
///
/// ## 需求（用户 10-18 拍板）
///   气泡左侧那张 80×80 的头像**点一下就放大**，居中显示，ESC 退出；
///   **不是**在界面上另加一个固定按钮。
///
/// ## 两条路：先试游戏原生，失败才自建
///
/// **① 原生 `UIBigPortraitModel`（首选）** —— 游戏自己就有"放大立绘"这个功能：
///   ```
///   UIType.BigPortraitModel            static UITypeBase（已注册的 UI 类型）
///   UIBigPortraitModelBase : UIBase
///       Button   btnMask      全屏遮罩按钮（点任意处关闭）
///       Button   btnClose     关闭按钮
///       RawImage rimgUnit     大立绘槽位
///   UIBigPortraitModel : UIBigPortraitModelBase
///       void InitData(WorldUnitBase unit)     ← 传单位即可
///   ```
///   游戏自己就是这么用的：`UINPCInfoBase/UnitInfo`、`UIPlayerInfoBase/Property`、
///   `UIDLCStoreBase` 三个面板都挂着 `btnBigPortrait` + `goKeyBigPortrait`（按钮 + 按键提示）。
///   ⇒ 走它 = **和游戏里"正常的立绘"逐像素一致**，且尺寸/取景/关闭全由游戏负责。
///
/// **② 自建浮层（兜底）** —— 原生开不起来时（版本差异 / InitData 抛异常 / OpenUI 返回 null）
///   自己渲一张：`PortraitService.Fill(unit, bigSlot)` 传一个大槽位。
///
/// ## 为什么"传个大槽位"就能渲出高清大图（关键事实，别再算错）
///   `PortraitModel.CreateTextureInModelData` 的签名里**没有任何尺寸参数**：
///   ```
///   (PortraitModelData data, RawImage rimg, Vector2 modelOffsetPosi, Single size,
///    Boolean async, Boolean useDyn, Action<GameObject> onCreateCall)
///   ```
///   第 4 个 `size` 是**模型缩放**（同 `UICreatePlayerFacade.InitPortraitScale(model, offset, scale)`
///   的用法），不是渲染尺寸。⇒ **唯一携带尺寸的输入就是那个 RawImage 槽位**，
///   渲染分辨率**跟着槽位走**。实机佐证：槽位 80×80 → `[PortraitCache]` 打出 `RenderTexture 120x120`
///   （1.5 倍）；用户早年用 170×230 的大槽位时拿到的就是高清大图。
///   ⇒ 想要大而清晰，**把槽位做大就行**，不需要存图、不需要换 API。
///
///   ⚠ 取景常量 `ModelOffset=(0,-4.5)` / `ModelScale=1f` 是从神识传音**逐字抄来的**，
///   而它原来就用在**高瘦立绘**槽位上（旧预制件 `PortraitLeft/Right = 170×230`）。
///   所以放大槽位要用**高瘦比例**（本文件 `SlotW/SlotH` ≈ 0.74），
///   用正方形只会把 80×80 那张方头像拉大，不是"正常立绘"。
///
/// ## 交互与关闭（10-18 二测实机定案，别再照旧文档写）
///   · **ESC** —— `Tick()` 每帧判一次，只在浮层开着时判（关着零开销）；
///   · **右键** —— 游戏原生窗自己的退出键（我们没接它，日志里也没有对应记录 ⇒ 在游戏内部）；
///   · **点立绘本身不关** —— 槽位 `raycastTarget=true` 吃掉射线。
///
///   ⚠ **"点空白处关闭"只对自建路成立**，原生路不成立。实机量到原生窗
///     `btnClose=隐藏  btnMask=显示`，但点蒙版区域无反应、点击会**穿透**到下面的对话窗
///     ⇒ 那个 `btnMask` **显示但不吃射线**。所以原生路（实际在走的那条）的出口就是
///     ESC + 右键两个键。自建路那层遮罩是我们自己挂的 Button，"点空白处"才算数。
///
///   ⚠⚠ **ESC 必须"抢"，不能只"也响应"**：`ChatPresenter.Update` 里另有一份
///     ESC＝关对话窗，两个处理器**互不知情** ⇒ 同一次按键会把立绘**和对话窗一起关掉**
///     （10-18 用户实测："点 esc 就联通对话 UI 一起退出了"）。收口在 `ConsumesEscape()`，
///     对话窗那条路先问它。判据两条（`IsOpen` + "本帧已被我关过"）**合起来与执行顺序无关**：
///       ① 对话窗先跑 → 此刻 `IsOpen` 还是 true → 让路；
///       ② 本类先跑（已关窗） → `IsOpen` 已 false，但 `_escFrame` 是本帧 → 让路。
///
/// ## 模态：游戏那层蒙版拦不住我们的对话窗，必须自己拦
///   实机现象（10-18 二测）：立绘开着时对话窗**已经被压暗**（⇒ 游戏蒙版确实在它之上），
///   却**照样能点**。原因：那层蒙版不吃射线（见上），而对话窗是 mod UI，
///   在游戏普通层里拾取优先级最高。
///   ⇒ 开窗时挂一层 `SetBlocker`：从点击源头像**向上找最近带 Canvas 的节点**
///     （＝游戏 UIMgr 给该窗实例挂的 Canvas，`AbChatPanel` 类头记着这条），
///     挂进它 + `SetAsLastSibling()` ⇒ **同一个 Canvas 内后兄弟在上**，必然压住该窗全部内容。
///     这条判据**不依赖任何层号**，所以游戏换了 UI 层排序也不会失效。
///   ⚠ 拦截层**只吞点击、不关窗、不加暗色**（10-18 用户拍板：「点击的话吞掉就行」；
///     压暗已由游戏蒙版负责，再加一层就是双重压暗）。
///
/// ## 宿主选谁：根 Canvas 的**直接子节点**
///   照 `HoverTip` 的成熟做法（那是"盖在最上层"的既有唯一实现）：
///   挂到**根 Canvas**下 + `SetAsLastSibling()`。为什么不是 `g.root` 自建 Canvas
///   （`UnreadBanner` 那条路）：那条路**要自带 GraphicRaycaster**，而本浮层是**模态**的，
///   自建 raycaster 一旦漏关就是"全游戏点不动"；挂在既有根 Canvas 下则复用游戏已有的拾取，
///   我们只多一个可关闭的节点。**漏关的代价天差地别，故选后者。**
///
/// 线程：仅 Unity 主线程。全函数 try/catch + 失败静默降级（浮层挂掉不该影响对话）。
/// IL2CPP：不改 AB 预制体、不新增 MonoBehaviour 类型（免 `RegisterTypeInIl2Cpp`）。
/// </summary>
using System;
using System.Text;                  // 诊断拾取：拼命中链
using UnhollowerBaseLib;      // IL2CPP.il2cpp_object_get_class / il2cpp_class_get_name（诊断探针取真类名）
using UnityEngine;
using UnityEngine.EventSystems;     // 诊断拾取：EventSystem / PointerEventData / RaycastResult
using UnityEngine.UI;

namespace AgentLoopBridge
{
    internal static class BigPortrait
    {
        // ------------------------------------------------------------------
        // 尺寸 / 键位
        // ------------------------------------------------------------------

        /// <summary>大立绘槽位宽（canvas 单位）。与 `SlotH` 的比例照旧预制件的
        /// `PortraitLeft/Right = 170×230`（≈0.739）——那是取景常量被调出来的那个比例。</summary>
        private const float SlotW = 460f;
        /// <summary>大立绘槽位高。渲染分辨率 ≈ 本尺寸 × 屏幕缩放（见文件头"关键事实"）。</summary>
        private const float SlotH = 620f;

        /// <summary>关闭键。**只改这一处**（`test_big_portrait_contract.py` 锁着它）。</summary>
        private static readonly KeyCode CloseKey = KeyCode.Escape;

        /// <summary>主机名（诊断用；`g.root` 下同名节点一眼可辨）。</summary>
        private const string HostName = "AgentLoopBigPortrait";

        /// <summary>模态拦截层节点名（诊断用：拾取日志里一眼看出"这一击被谁吃了"）。</summary>
        private const string PickName = "AgentLoopBigPortraitPick";

        /// <summary>
        /// 拦截层的颜色：**极低但不为零**的不透明度。
        ///
        /// ★ 刻意**不是 0** ★：`alpha=0` 的 Graphic 有被 canvas 判为剔除
        /// （`CanvasRenderer.cull = true`）的风险，而 `GraphicRaycaster` **会跳过被剔除的图形**
        /// —— 那这层就成了"看不见、也拦不住"。10-18 二测的拾取 A/B **完全相同**，
        /// 两个候选原因之一就是它（另一个是我量的时机太早：同一帧内 `absoluteDepth` 还是 -1，
        /// 而 raycaster 跳过 depth=-1 的图形 —— 现在改成延迟复测，见 `LogBlockerProbe`）。
        /// `1/255` 实机**不可见**（底下游戏蒙版本来就压暗了），但足以避开那条剔除路径。
        /// </summary>
        private static readonly Color PickTint = new Color(0f, 0f, 0f, 1f / 255f);

        // ★ 刻意**不挂**可见的「关闭」按钮 —— 别再往回加 ★
        // 10-18 曾按需求挂过一个右上角「关闭」按钮（位置按实机坐标算过、不压立绘），
        // 用户看到实机效果后拍板**撤掉，只留快捷键**：「去掉按钮，只留快捷键」。
        // 理由：加出来的按钮是画在**游戏原生立绘窗**上的**外来控件**，与那扇窗的原生样式
        // 不一路；而退出本来就有**零控件**的路：
        //   · **ESC**（本文件的 `CloseKey`，Tick 每帧判一次，日志实证可用）；
        //   · **右键**（游戏原生窗自己的退出键 —— 我们没接它、日志里也没有对应记录）。
        // ⚠ 曾经这里写"点空白处也算一条"，**是错的**：实机量到 `btnMask` 显示但
        //   `raycastTarget=False`（只压暗、不吃射线），原生路点空白处**什么都不会发生**。
        //   "点空白处关闭"只对**自建路**那层我们自己挂的遮罩 Button 成立。
        // ⇒ 可见控件一个都不要，退出走键盘与右键。若将来又要加，
        //   先确认它不会变成"盖在立绘上的一块补丁"（这正是被撤掉的原因）。

        // ------------------------------------------------------------------
        // 状态
        // ------------------------------------------------------------------

        /// <summary>自建浮层根（含全屏遮罩）。null = 没建/已销毁（Unity fake-null 也算）。</summary>
        private static GameObject _host;
        /// <summary>大立绘槽位（`PortraitService.Fill` 的目标）。</summary>
        private static RawImage _slot;
        /// <summary>当前是否走的是**游戏原生**那条路（关闭方式不同：走 `g.ui.CloseUI`）。</summary>
        private static bool _nativeOpen;
        /// <summary>原生窗代理（只在诊断探针里用：延迟复查它的 `rimgUnit` 渲成了多大）。</summary>
        private static UIBigPortraitModel _native;

        /// <summary>
        /// 模态拦截层：挂在**你点的那个窗自己的子树里**，全透明、只吃射线。
        /// 见文件头「模态」一节 —— 它存在的唯一理由是"游戏蒙版不吃射线，拦不住我们的对话窗"。
        /// </summary>
        private static GameObject _blocker;

        /// <summary>
        /// 本类最后一次用 ESC 关窗的帧号（`Time.frameCount`）。
        /// 给同帧的 `ChatPresenter` 看：它先跑也拦得住（`IsOpen`），后跑也拦得住（本帧号）。
        /// </summary>
        private static int _escFrame = -1;

        // ------------------------------------------------------------------
        // 诊断探针 —— 一次开机把**所有**调参要用的量拿全
        // ------------------------------------------------------------------
        // 为什么必须一次拿全：这条路有**两个只能真机量的未知数**——
        //   ① **渲染分辨率到底跟什么走**：槽位 canvas 单位 × Canvas 缩放？还是 × 一个常数（1.5）？
        //      实机已知：80×80 槽位 → `RenderTexture 120x120`；而当时 Canvas 缩放是 1.333
        //      （2560/1920）⇒ 1.333 与 1.5 对不上，**两种解释都还站着**，必须再量一次带缩放的那种。
        //   ② **游戏原生窗**的立绘槽位有多大、开在屏幕哪 —— 决定"原生路够不够用、要不要调自建"。
        // 每量一次要开一次游戏，所以这里一次把所有相关量全打出来，五个数一比即可反解公式：
        //      Screen 尺寸 · 根画布 scaleFactor · 槽位 rect(canvas 单位) · 槽位屏幕像素 · texture 尺寸
        // 另：立绘是**异步**渲的（`PortraitCache` 用 0.15s 等"渲完当帧"），故立刻打一次、
        // 延迟再打一次 —— 只打立刻那次很可能量到"还没渲"的空纹理，那就白开一次游戏。
        private const float ProbeDelay = 0.4f;
        private static float _probeAt;      // realtimeSinceStartup；<=0 = 无待办
        private static float _openedAt;     // 本次开窗时刻（算总耗时）
        private static string _route = "";  // 本次走的路（"原生"/"自建"/"失败"）
        private static int _bindOk, _bindSkipEmpty, _bindFail;   // 每行气泡都调 Bind，只打前几个防刷屏
        /// <summary>其中**带 unitId**（＝群聊行）的打过几条明细（只打前 3 条）。</summary>
        private static int _bindWithId;

        /// <summary>浮层是否开着（两条路任一）。</summary>
        public static bool IsOpen
        {
            get { return _nativeOpen || _host != null; }
        }

        /// <summary>
        /// 这一帧的 ESC 是否归我（大立绘）。**`ChatPresenter` 的 ESC 分支必须先问它**，
        /// 否则同一次按键会把立绘和对话窗一起关掉（10-18 用户实测）。
        ///
        /// 两个判据缺一不可，合起来才与执行顺序无关：
        ///   · `IsOpen` —— 对话窗那条路**先**跑时，立绘还开着 ⇒ 让路；
        ///   · `_escFrame` —— 本类**先**跑（窗已关）时 `IsOpen` 已 false，
        ///     但那一帧还是我消费的 ⇒ 也要让路。
        /// 下一帧再按 ESC，`_escFrame` 过期 ⇒ 对话窗照常能被关。
        ///
        /// 名字用"消费"而不是"是否开着"：调用方问的是**这一次按键**归谁，
        /// 不是窗的状态 —— 语义写错就会在"关窗那一帧"漏掉（正是本 bug 的形状）。
        /// </summary>
        public static bool ConsumesEscape()
        {
            if (IsOpen) return true;
            try { return _escFrame == Time.frameCount; }
            catch { return false; }
        }

        // ------------------------------------------------------------------
        // 入口：把点击接到头像节点上
        // ------------------------------------------------------------------

        /// <summary>
        /// 给一张头像节点挂"点开大立绘"。
        ///
        /// 判据（**必须空名早退**）：`name` 为空时**不挂**。空名的语义是
        /// "群聊里不知道这条是谁说的"（见 `ChatWindow.SpeakerOf` 的三态说明）——
        /// 那时头像本身都按"不画错人"隐藏了，再挂个点了弹别人脸的按钮就是撒谎。
        ///
        /// `unitId` 可选：有精确 id 就不按名解析（重名时按名会猜错，见 `RowAvatars.Apply`）。
        ///
        /// 捕获的是**节点本身**而不是当时算好的画布：行会被回收重建，而点击只可能发生在
        /// 活着的节点上 ⇒ 点击那一刻再从它反查根画布，永远拿的是**当前**那棵树。
        /// </summary>
        public static void Bind(RawImage avatar, string name, string unitId)
        {
            if (avatar == null) return;
            if (string.IsNullOrEmpty(name))
            {
                _bindSkipEmpty++;                        // 见上方判据（群聊未知 → 不挂）
                return;
            }
            try
            {
                var go = avatar.gameObject;
                var btn = go.GetComponent<Button>();
                if (btn == null) btn = go.AddComponent<Button>();
                // 头像不做任何视觉反馈：它现在是张立绘，按下变暗会像"图坏了"
                btn.transition = Selectable.Transition.None;
                var origin = avatar;
                string n = name, u = unitId;
                // 走 ClickUtils：IL2CPP 下 UnityAction 要「Action → 隐式转换 → AddListener」三步
                ClickUtils.Attach(btn, () => Show(n, u, origin));
                _bindOk++;
                // 前 3 个打明细（确认接线通了就够），之后每 50 个一行汇总 —— 长对话里行很多
                if (_bindOk <= 3 || _bindOk % 50 == 0)
                    ModMain.P("[BigPortrait] 已挂点击 #" + _bindOk + "：节点=" + Path(avatar.transform) +
                              " 目标='" + name + "' unitId='" + (unitId ?? "") + "'"
                              + "｜累计 跳过空名=" + _bindSkipEmpty + " 失败=" + _bindFail);
                // ★ 群聊行单独留痕 ★（10-18 二测加）：**带 unitId 的绑定就是群聊行的指纹** ——
                //   单聊与玩家行走按名解析，`unitId` 传的是 null。群聊"点不开"时第一个要回答的
                //   就是"到底挂上没有"，而上面那条 #N 的明细在长会话里早被 50 取模跳过了。
                //   只记前 3 个（够证明接线通），不刷屏。
                else if (!string.IsNullOrEmpty(unitId) && _bindWithId < 3)
                {
                    _bindWithId++;
                    ModMain.P("[BigPortrait] 已挂点击（群聊行 #" + _bindWithId + "）：节点=" +
                              Path(avatar.transform) + " 目标='" + name + "' unitId='" + unitId + "'");
                }
            }
            catch (Exception e)
            {
                _bindFail++;
                ModMain.P("[BigPortrait] Bind('" + name + "') 失败: " + e.Message);
            }
        }

        // ------------------------------------------------------------------
        // 开关
        // ------------------------------------------------------------------

        /// <summary>点开。`origin` 只用来反查根画布（自建浮层那条路才需要，可为 null）。</summary>
        public static void Show(string name, string unitId, RawImage origin = null)
        {
            ModMain.P("[BigPortrait] ── 点击头像：'" + name + "'（unitId='" + (unitId ?? "") +
                      "'）节点=" + (origin != null ? Path(origin.transform) : "<null>") +
                      "｜Bind 累计 成功=" + _bindOk + " 跳过空名=" + _bindSkipEmpty + " 失败=" + _bindFail);
            var unit = Resolve(name, unitId);
            if (unit == null)
            {
                ModMain.P("[BigPortrait] 解析不到单位：'" + name + "'（unitId='" + (unitId ?? "") +
                          "'）→ 不开窗");
                return;
            }
            Show(unit, origin);
        }

        /// <summary>点开（已经拿到单位时走这个 —— 单聊窗口手上就有 `WorldUnitBase`）。</summary>
        public static void Show(WorldUnitBase unit, RawImage origin = null)
        {
            if (unit == null) return;
            Close();                                  // 先收掉上一张，绝不叠窗
            _openedAt = Now();
            _probeAt = 0f;
            _route = "";
            LogEnv(origin);                           // ① 环境（Screen / 根画布 / 缩放）
            ModMain.P("[BigPortrait] 目标：" + SafeName(unit) + " unitID=" + SafeId(unit));
            if (TryNative(unit)) _route = "原生";
            else if (TryOwn(unit, origin)) _route = "自建";
            else
            {
                _route = "失败";
                ModMain.P("[BigPortrait] ── 结果：两条路都没成（原生 + 自建），本次不显示");
                return;
            }
            // ★ 模态拦截（两条路都要）：立绘一开，你点的那个窗就不该再收得到点击 ★
            // 放在"路已确定"之后：失败时不该留一层吃掉对话窗点击的透明层。
            SetBlocker(origin);
            ModMain.P("[BigPortrait] ── 结果：路=" + _route + " 开窗耗时=" + Ms(_openedAt) + "ms" +
                      "（立刻量的 texture 见上一行；" + (int)(ProbeDelay * 1000) + "ms 后再量一次）");
            _probeAt = Now() + ProbeDelay;            // ② 延迟复查（异步渲染，立刻量可能是空的）
        }

        /// <summary>关掉（两条路都收）。**幂等**：没开时是空操作（也因此不会刷日志）。</summary>
        public static void Close()
        {
            if (!IsOpen) return;
            float dur = _openedAt > 0f ? (Now() - _openedAt) * 1000f : -1f;
            ModMain.P("[BigPortrait] ── 关窗：路=" + (_route.Length > 0 ? _route : "?") +
                      (dur >= 0f ? " 开了 " + (int)dur + "ms" : ""));
            _probeAt = 0f;
            ClearBlocker();                       // ★ 先撤模态：漏撤 = 对话窗永久点不动且零报错 ★
            if (_nativeOpen)
            {
                _nativeOpen = false;
                try { g.ui.CloseUI(UIType.BigPortraitModel, true); }
                catch (Exception e) { ModMain.P("[BigPortrait] 关原生窗失败: " + e.Message); }
                _native = null;
            }
            if (_host != null)
            {
                try { _host.SetActive(false); }
                catch { }
            }
        }

        /// <summary>
        /// 每帧（`AbHotkeys.OnFrame` 调）：浮层开着时判一次关闭键。
        ///
        /// ★ 关着时**一行早退**（`IsOpen` 是两次引用比较，零 Unity 调用）——
        ///   与本项目其它常驻 tick 同规矩（`TickContactHotkey` 的"常态早退"）。
        /// 诊断探针排在早退**之前**：它是"一次浮点比较"级的（`_probeAt` 常态恒为 0），
        /// 而排后面就会被早退吃掉 —— 那样"用户手快先关掉窗"时就量不到渲染结果了。
        /// </summary>
        public static void Tick()
        {
            if (_probeAt > 0f) TickProbe();
            WatchExternalClose();                                        // 游戏自己关的窗（右键）→ 我方跟着收
            if (_blocker != null && !IsOpen) ClearBlocker();             // 自愈：浮层没了，拦截层必撤
            if (!IsOpen) return;
            // 宿主被场景销毁（读档/切场景）时 `_host` 变 fake-null：清干净，别留着"看着开着"的状态
            if (!_nativeOpen && _host == null) return;
            bool pressed;
            try { pressed = Input.GetKeyDown(CloseKey); }
            catch { return; }
            if (!pressed) return;
            ModMain.P("[BigPortrait] 关闭键 " + KeyLabel(CloseKey) + " 按下");
            try { _escFrame = Time.frameCount; } catch { }               // ★ 标记这一击归我（同帧 ChatPresenter 让路）
            Close();
        }

        /// <summary>
        /// 游戏**自己**把原生窗关掉时（右键 / 游戏内其它入口）我方状态必须跟着收。
        ///
        /// 为什么非收不可：`_nativeOpen` 一旦卡在 true，`IsOpen` 就永远为真 ⇒
        /// ① 拦截层（`_blocker`）撤不掉 ⇒ **对话窗被永久吞掉点击，且零报错**；
        /// ② 后续 ESC 会去 `CloseUI` 一个早就没了的窗，把"关窗耗时"日志也带偏。
        /// 这与 `PortraitCache`/`TurnRegistry` 那几处"外部状态变了、我方缓存必须跟着变"同款教训。
        ///
        /// 判活用 `activeInHierarchy`（游戏的关窗是销毁或置灰，两种都覆盖）；
        /// `_native == null` 先判，免得对 fake-null 取 `.gameObject`。
        /// **不调 `g.ui.CloseUI`** —— 窗已经没了，再调一次是拿已关的 UI 去走关闭流程。
        /// </summary>
        private static void WatchExternalClose()
        {
            if (!_nativeOpen) return;
            bool alive = false;
            try { alive = _native != null && _native.gameObject.activeInHierarchy; } catch { }
            if (alive) return;
            ModMain.P("[BigPortrait] 原生窗已被游戏自己关掉（右键等）→ 我方状态收尾，撤拦截层");
            _nativeOpen = false;
            _native = null;
            _probeAt = 0f;
        }

        /// <summary>世界重载时清干净（`ModMain` 与其它 `ClearAll` 并列调用）。
        ///
        /// 为什么必须清：宿主挂在**游戏的根 Canvas** 下，而根 Canvas 随场景销毁
        /// ⇒ 我们的引用会变僵尸条目（读取恒失败且拒绝重建，与 `PortraitCache.ResetForWorld` 同款教训）。
        /// —— 只清引用，**不去 `Destroy`**：节点已随场景没了，多此一举反而可能踩到已销毁对象。</summary>
        public static void ResetForWorld()
        {
            _host = null;
            _slot = null;
            _nativeOpen = false;
            _native = null;
            _probeAt = 0f;
            _escFrame = -1;
            // 拦截层挂在你点的那个窗（`UIChatAi`）下，**不随换存档必然销毁**：对话窗是常驻 mod UI，
            // 可能活过换存档。若只清引用，它就会变成"没人认领但仍盖在对话窗上"的一层 ⇒
            // 对话窗永久点不动。而 `_host` 那套"只清引用不 Destroy"的理由（节点已随场景没了）
            // 在这里**不成立于必然**，故这里真 Destroy 一次（已随场景没了的会被 fake-null 判掉）。
            try { if (_blocker != null) UnityEngine.Object.Destroy(_blocker); } catch { }
            _blocker = null;
        }

        // ------------------------------------------------------------------
        // 路 ①：游戏原生 UIBigPortraitModel
        // ------------------------------------------------------------------

        /// <summary>
        /// 开游戏自己的放大立绘。成功返回 true。
        ///
        /// ★ **不得对 Il2Cpp 返回值做向下转型**（本工程铁律：代理类型恒为声明基类，
        ///   `as 子类` 静默 null、`(子类)` 抛 InvalidCastException）。
        ///   `OpenUI` 的声明返回类型正是 `UIBase`，而我们要的是 `UIBigPortraitModel`
        ///   ⇒ 不能 cast，只能**用同一个 native 指针另造一个真类型的代理**：
        ///   `new UIBigPortraitModel(ui.Pointer)`。这不是转型，是"同指针、真类型"的构造，
        ///   类型正确性由我们自己保证（我们开的就是这个 UI 类型）。
        /// </summary>
        private static bool TryNative(WorldUnitBase unit)
        {
            try
            {
                // ★ 先取 UIType 再判空 ★：`UIType.BigPortraitModel` 是静态属性（由 `UIType..cctor`
                //   从配表灌进来）。万一这个游戏版本没注册它，属性返回 null ⇒ 拿 null 去调
                //   `OpenUI` 会在**游戏 UIMgr 内部**炸 —— 那正是 09-10 那类"从游戏 UI 内部炸出来、
                //   打断游戏 UI 流程"的事故形状（当时是 `Instantiate(null)`）。
                //   所以宁可不调，也不要拿 null 去调。
                var uiType = UIType.BigPortraitModel;
                if (uiType == null)
                {
                    ModMain.P("[BigPortrait] 原生：UIType.BigPortraitModel 未注册 → 转自建");
                    return false;
                }
                var ui = g.ui.OpenUI(uiType);
                if (ui == null)
                {
                    ModMain.P("[BigPortrait] 原生：OpenUI(BigPortraitModel) 返回 null → 转自建");
                    return false;
                }
                // 造代理前**先记下 native 真类名**：`ui` 的 CLR 类型恒为声明基类 `UIBase`
                // （代理不做运行时类型解析），只有走 il2cpp 反射才看得到它到底是不是那个类。
                ModMain.P("[BigPortrait] 原生：OpenUI 返回 native 类=" + RealClassName(ui.Pointer));
                var big = new UIBigPortraitModel(ui.Pointer);
                big.InitData(unit);
                _native = big;
                _nativeOpen = true;
                // ② 立刻量一次原生窗（槽位 rect / texture）—— 这是"游戏自己的大立绘多大"的唯一来源
                LogNative("原生·立刻");
                return true;
            }
            catch (Exception e)
            {
                ModMain.P("[BigPortrait] 原生开窗失败（转自建）: " + e.Message);
                // 半开状态要收干净：OpenUI 成功而 InitData 炸了的话，窗已经挂在那儿了
                try { g.ui.CloseUI(UIType.BigPortraitModel, true); } catch { }
                _nativeOpen = false;
                _native = null;
                return false;
            }
        }

        // ------------------------------------------------------------------
        // 路 ②：自建浮层（大槽位 + PortraitService）
        // ------------------------------------------------------------------

        private static bool TryOwn(WorldUnitBase unit, RawImage origin)
        {
            try
            {
                if (!EnsureBuilt(origin)) return false;
                _host.SetActive(true);
                _host.transform.SetAsLastSibling();      // 盖在最上层（同 HoverTip）
                float t0 = Now();
                bool ok = PortraitService.Fill(unit, _slot);
                // ③ 自建路的关键量：槽位 rect（canvas 单位）× 画布缩放 = 屏幕像素，再对上 texture 尺寸
                //    ⇒ 一次就能反解"渲染分辨率跟什么走"（见文件头"关键事实"与字段区注释）。
                ModMain.P("[BigPortrait] 自建：PortraitService.Fill → " + (ok ? "成功" : "失败") +
                          " 耗时=" + Ms(t0) + "ms｜槽位设定=" + SlotW + "×" + SlotH);
                LogSlot("自建·立刻");
                if (!ok)
                {
                    // 渲染失败（单位模型不可用等）：收掉，别留一个只有黑遮罩的空窗
                    ModMain.P("[BigPortrait] 自建：Fill 失败 → 收窗，转不出图");
                    _host.SetActive(false);
                    return false;
                }
                return true;
            }
            catch (Exception e)
            {
                ModMain.P("[BigPortrait] 自建浮层失败: " + e.Message);
                try { if (_host != null) _host.SetActive(false); } catch { }
                return false;
            }
        }

        /// <summary>懒建宿主（含遮罩 + 槽位 + 提示）。宿主随场景销毁后下次自动重建。</summary>
        private static bool EnsureBuilt(RawImage origin)
        {
            if (_host != null) return true;
            _host = null; _slot = null;                       // 场景重建：全部重挂
            try
            {
                Transform parent = ResolveCanvasRoot(origin);
                if (parent == null)
                {
                    ModMain.P("[BigPortrait] 找不到根画布 → 建不出来");
                    return false;
                }
                _host = new GameObject(HostName);
                _host.transform.SetParent(parent, false);
                var hostRt = _host.AddComponent<RectTransform>();
                FullScreen(hostRt);

                // —— 全屏遮罩：点空白处关闭 ——
                var maskGo = new GameObject("Mask");
                maskGo.transform.SetParent(_host.transform, false);
                FullScreen(maskGo.AddComponent<RectTransform>());
                var maskImg = maskGo.AddComponent<Image>();
                maskImg.color = new Color(0f, 0f, 0f, 0.72f);   // sprite=null → 纯色块
                maskImg.raycastTarget = true;                    // 模态：吃掉全屏点击
                var maskBtn = maskGo.AddComponent<Button>();
                maskBtn.transition = Selectable.Transition.None;
                ClickUtils.Attach(maskBtn, Close);

                // —— 大立绘槽位（居中；raycastTarget 吃掉射线 ⇒ 点图本身不关）——
                var slotGo = new GameObject("Slot");
                slotGo.transform.SetParent(maskGo.transform, false);
                var slotRt = slotGo.AddComponent<RectTransform>();
                slotRt.anchorMin = slotRt.anchorMax = slotRt.pivot = new Vector2(0.5f, 0.5f);
                slotRt.sizeDelta = new Vector2(SlotW, SlotH);
                slotRt.anchoredPosition = Vector2.zero;
                _slot = slotGo.AddComponent<RawImage>();
                _slot.raycastTarget = true;

                // —— 关闭提示（不可点，免得挡着遮罩）——
                var hintGo = new GameObject("Hint");
                hintGo.transform.SetParent(maskGo.transform, false);
                var hintRt = hintGo.AddComponent<RectTransform>();
                hintRt.anchorMin = hintRt.anchorMax = hintRt.pivot = new Vector2(0.5f, 0f);
                hintRt.sizeDelta = new Vector2(SlotW, 34f);
                hintRt.anchoredPosition = new Vector2(0f, 60f);
                var hint = hintGo.AddComponent<Text>();
                hint.font = ChatUiBuilder.ResolveSharedFont();
                hint.text = "点击空白处或按 " + KeyLabel(CloseKey) + " 关闭";
                hint.alignment = TextAnchor.MiddleCenter;
                hint.color = new Color(1f, 1f, 1f, 0.55f);
                hint.fontSize = 20;
                hint.raycastTarget = false;

                _host.SetActive(false);
                ModMain.P("[BigPortrait] 自建宿主已建：宿主=" + Path(_host.transform) +
                          " 槽位设定=" + SlotW + "×" + SlotH +
                          "（父级画布 mode=" + CanvasMode(parent) + "）");
                return true;
            }
            catch (Exception e)
            {
                ModMain.P("[BigPortrait] EnsureBuilt 失败: " + e.Message);
                _host = null; _slot = null;
                return false;
            }
        }

        /// <summary>根画布 —— 本浮层的宿主父级。
        ///
        /// 取**根** Canvas（嵌套 Canvas 的 renderMode 只是序列化残留、不生效，
        /// 这条是 `HoverTip` 用真机定案的）。从点击源头像反查，取不到再退到 `g.root`。</summary>
        private static Transform ResolveCanvasRoot(RawImage origin)
        {
            try
            {
                if (origin != null)
                {
                    var c = origin.canvas;                       // Graphic.canvas（原生查询）
                    if (c != null)
                    {
                        var rc = c.rootCanvas;
                        if (rc != null) return rc.transform;
                    }
                }
            }
            catch { }
            try
            {
                var c2 = origin != null ? origin.GetComponentInParent<Canvas>() : null;
                if (c2 != null) return c2.transform;
            }
            catch { }
            try { if (g.root != null) return g.root.transform; } catch { }
            return null;
        }

        // ------------------------------------------------------------------
        // 模态拦截层 —— 见文件头「模态」一节
        // ------------------------------------------------------------------

        /// <summary>
        /// 挂一层"全透明、只吃射线"的拦截层，把**你点的那个窗**（对话窗）挡在立绘之下。
        ///
        /// 为什么必须自己挂：游戏那层蒙版虽然盖住了对话窗（用户实测"对话窗是压暗的"），
        /// 但它**不吃射线** ⇒ 点击穿透过去，落在我们的对话窗上。用户实测原话：
        /// 「确实有一层蒙版，但是我依旧可以点击对话 ui 的一些东西」。
        ///
        /// 为什么挂进"那个窗自己的子树"：Unity 拾取的先后 = 各自 Canvas 的排序 + 同 Canvas 内的兄弟序。
        /// 挂成该窗 Canvas 根的**最后一个子节点** ⇒ 同 Canvas 内它在最上 ⇒ 必然赢过该窗全部内容。
        /// 这样**不需要知道任何层号**；反之挂到根画布会掉到该窗自己的 Canvas 之下，等于白挂。
        ///
        /// 三个刻意的选择：
        ///   · `raycastTarget=true` + `PickTint`（**极低但不为零**的不透明度）—— 只吃射线，
        ///     不产生任何可见效果（压暗已由游戏蒙版负责，再加一层暗色就是双重压暗）。
        ///     ⚠ 不要"顺手"把 `PickTint` 的 alpha 改成 0：那有被 canvas 剔除的风险，见它的注释；
        ///   · **不挂 Button、不接 onClick** —— 用户 10-18 拍板「点击的话吞掉就行」：
        ///     拦截层只吞不关。挂 Button 会把"点对话窗"变成"关立绘"，与拍板不符；
        ///   · `ignoreLayout` —— 宿主要是挂了 LayoutGroup，别让一个全屏子节点去挤动它的布局。
        /// </summary>
        private static void SetBlocker(RawImage origin)
        {
            ClearBlocker();
            try
            {
                Transform root = ResolvePickRoot(origin);
                if (root == null)
                {
                    // 降级但不报错：找不到宿主窗时对话窗仍可点，这比"拦错整屏"安全得多
                    ModMain.P("[BigPortrait] 模态拦截：取不到宿主窗 → 本次不拦（对话窗仍可点）");
                    return;
                }
                PickLog("·修前", root);                       // ① 先量：这一击本来被谁吃了
                // 建节点照 `HoverTip`（既有的"盖在最上层"实现）的写法：先建再 SetParent，
                // RectTransform 用**原生查询**取（`typeof(RectTransform)` 那条重载在本环境里
                // 要 `Il2CppReferenceArray<Il2CppSystem.Type>`，编译期就过不去）。
                var go = new GameObject(PickName);
                go.transform.SetParent(root, false);
                var rt = UiRects.Of(go.transform);            // ★ 铁律唯一收口点：禁止 `as RectTransform` ★
                if (rt == null) rt = go.AddComponent<RectTransform>();
                if (rt == null)
                {
                    ModMain.P("[BigPortrait] 模态拦截：RectTransform 取不到 → 放弃（对话窗仍可点）");
                    UnityEngine.Object.Destroy(go);
                    return;
                }
                FullScreen(rt);
                var img = go.AddComponent<Image>();
                img.color = PickTint;                          // 极低但不为零：见 PickTint 注释（alpha=0 会被剔除）
                img.raycastTarget = true;
                try { go.AddComponent<LayoutElement>().ignoreLayout = true; } catch { }
                go.transform.SetAsLastSibling();              // ★ 同 Canvas 内后兄弟在上 ⇒ 压住该窗全部内容 ★
                _blocker = go;
                LogBlockerHost(root);
                // ② 同一帧再量一次 —— ⚠ 这次**不足为凭**：新建的 Graphic 此刻
                //    `absoluteDepth` 多半还是 -1，而 raycaster 跳过 depth=-1 ⇒ 量到"没生效"是假象。
                //    真正作数的是 `LogBlockerProbe` 的**延迟**那一次（canvas 已重建过）。
                PickLog("·修后（同帧，仅供对照）", root);
            }
            catch (Exception e)
            {
                ModMain.P("[BigPortrait] 模态拦截挂载失败（对话窗仍可点）: " + e.Message);
                try { if (_blocker != null) UnityEngine.Object.Destroy(_blocker); } catch { }
                _blocker = null;
            }
        }

        /// <summary>撤拦截层。**幂等**；宿主窗已随场景销毁时 `_blocker` 是 fake-null，`, == null` 直接早退。</summary>
        private static void ClearBlocker()
        {
            if (_blocker == null) return;
            try { UnityEngine.Object.Destroy(_blocker); } catch { }
            _blocker = null;
        }

        /// <summary>
        /// 拦截层挂哪儿：从点击源头像**向上找最近带 `Canvas` 的节点**。
        ///
        /// 那个 Canvas 是游戏 UIMgr 给窗实例挂的（`AbChatPanel` 类头记着这条：
        /// "实例根上的 Canvas/GraphicRaycaster 是游戏 UIMgr 自己挂的"），
        /// 挂进它内部才保证"同 Canvas 内后兄弟在上"。
        /// 取不到**不猜**（宁可少拦，不可拦错）。
        /// </summary>
        private static Transform ResolvePickRoot(RawImage origin)
        {
            try
            {
                if (origin != null)
                {
                    var t = origin.transform;
                    for (int i = 0; i < 12 && t != null; i++)
                    {
                        if (t.GetComponent<Canvas>() != null) return t;
                        t = t.parent;
                    }
                }
            }
            catch { }
            return null;
        }

        /// <summary>拦截层宿主诊断：路径 / 画布排序 / 双方兄弟序（末位才压得住）。
        ///
        /// ⚠ 两个分母的含义**不同**，10-18 二测这里写错过一次（我方用了宿主父级的子节点数）：
        ///   · 宿主的兄弟序 → 分母是**宿主父级**的子节点数（它和别的窗谁在上）；
        ///   · 我方的兄弟序 → 分母是**宿主自己**的子节点数（我在这扇窗里是不是末位）。</summary>
        private static void LogBlockerHost(Transform root)
        {
            string info;
            try
            {
                var cv = root.GetComponent<Canvas>();
                string cvs = cv == null
                    ? "<宿主自身无 Canvas>"
                    : "order=" + cv.sortingOrder + " override=" + cv.overrideSorting + " mode=" + cv.renderMode;
                int hostParent = root.parent != null ? root.parent.childCount : -1;
                int mine = _blocker != null ? _blocker.transform.GetSiblingIndex() : -1;
                info = "宿主窗=" + Path(root) + "｜画布=" + cvs +
                       "｜宿主兄弟序=" + root.GetSiblingIndex() + "/" + (hostParent - 1) +
                       "（父级 " + hostParent + " 个窗）" +
                       "｜我方兄弟序=" + mine + "/" + (root.childCount - 1) +
                       "（宿主共 " + root.childCount + " 个子节点；末位才压得住本窗全部内容）";
            }
            catch (Exception e) { info = "<异常 " + e.Message + ">"; }
            ModMain.P("[BigPortrait] 模态拦截已挂：" + info);
        }

        /// <summary>
        /// ★ 延迟复测拦截层 —— **这才是"它到底吃不吃射线"的硬判据** ★
        ///
        /// 为什么必须延迟：新建的 Graphic 在**同一帧**里 `absoluteDepth` 多半还是 `-1`，
        /// 而 `GraphicRaycaster` **跳过 depth=-1 的图形** ⇒ 同帧那次拾取量到"没生效"是假象
        /// （10-18 二测：`·修前` 与 `·修后` **逐字相同**，我的拦截层一次都没进命中链，
        ///   两个候选原因就是"时机太早"与"alpha=0 被剔除"，本方法把它们分开：
        ///   `depth`/`cull` 直接给出图形在 canvas 眼里的真实状态）。
        ///
        /// 三个数各自回答一件事：
        ///   · `depth >= 0`  ⇒ 它已被 canvas 登记（同帧 -1 的假象排除）；
        ///   · `cull = False` ⇒ 没被剔除（`alpha=0` 那条路径排除）；
        ///   · 延迟拾取的命中链首位是 `…/AgentLoopBigPortraitPick` ⇒ **真的拦住了点击**。
        /// </summary>
        private static void LogBlockerProbe(string tag)
        {
            try
            {
                if (_blocker == null) { ModMain.P("[BigPortrait] " + tag + "·拦截层：已不在（未挂/已撤）"); return; }
                string depth = "?", cull = "?", rct = "?", rect = "?";
                var img = _blocker.GetComponent<Image>();
                if (img != null)
                {
                    try { depth = img.depth.ToString(); } catch { }
                    rct = img.raycastTarget.ToString();
                }
                var cr = _blocker.GetComponent<CanvasRenderer>();
                if (cr != null) { try { cull = cr.cull.ToString(); } catch { } }
                var rt = UiRects.Of(_blocker.transform);
                if (rt != null) rect = F(rt.rect.width) + "x" + F(rt.rect.height);
                var parent = _blocker.transform.parent;
                ModMain.P("[BigPortrait] " + tag + "·拦截层：depth=" + depth + " cull=" + cull +
                          " raycastTarget=" + rct + " rect=" + rect +
                          " 兄弟序=" + _blocker.transform.GetSiblingIndex() + "/" +
                          (parent != null ? parent.childCount - 1 : -1) +
                          "（depth>=0 且 cull=False 才可能吃射线；矩形要盖住窗口）");
                if (parent != null) PickLog(tag + "·拦截层", parent);   // 同一时刻再拾取：此刻 canvas 已重建过
            }
            catch (Exception e) { ModMain.P("[BigPortrait] " + tag + "·拦截层探针异常: " + e.Message); }
        }

        /// <summary>
        /// 诊断：在宿主窗上取 5 个点（中心 + 四角内缩），各做一次**真实拾取**，打命中链前 3 个。
        ///
        /// 这是本功能唯一"引擎级"的判据 —— **谁排在前面谁就吃掉这一击**，
        /// 不必去推画布层号（层号是游戏 UIMgr 排的，会随版本变）。
        /// 修前/修后各打一次，A/B 一对即可证明拦截层真的生效。
        /// 全程 try/catch：探针失败绝不影响功能。
        /// </summary>
        private static void PickLog(string tag, Transform root)
        {
            try
            {
                var es = EventSystem.current;
                if (es == null) { ModMain.P("[BigPortrait] 拾取" + tag + "：<无 EventSystem>"); return; }
                var rt = UiRects.Of(root);
                if (rt == null) { ModMain.P("[BigPortrait] 拾取" + tag + "：宿主不是 RectTransform"); return; }
                var cv = root.GetComponentInParent<Canvas>();
                Camera cam = null;
                if (cv != null)
                {
                    var rc = cv.rootCanvas;
                    cam = rc != null ? rc.worldCamera : cv.worldCamera;
                }
                float w = rt.rect.width, h = rt.rect.height;
                Vector2[] offs =
                {
                    new Vector2(0f, 0f),
                    new Vector2(-w * 0.4f, h * 0.4f), new Vector2(w * 0.4f, h * 0.4f),
                    new Vector2(-w * 0.4f, -h * 0.4f), new Vector2(w * 0.4f, -h * 0.4f)
                };
                var sb = new StringBuilder();
                for (int i = 0; i < offs.Length; i++)
                {
                    Vector2 world = rt.TransformPoint(rt.rect.center + offs[i]);
                    Vector2 sp = RectTransformUtility.WorldToScreenPoint(cam, world);
                    if (i > 0) sb.Append(" ｜ ");
                    sb.Append("(" + (int)sp.x + "," + (int)sp.y + ")→" + TopHits(es, sp));
                }
                ModMain.P("[BigPortrait] 拾取" + tag + "：" + sb);
            }
            catch (Exception e)
            {
                ModMain.P("[BigPortrait] 拾取" + tag + " 异常: " + e.Message);
            }
        }

        /// <summary>某屏幕点的命中链（前 3 个，**最上面的在最前**）。</summary>
        private static string TopHits(EventSystem es, Vector2 screenPos)
        {
            try
            {
                var ped = new PointerEventData(es);
                ped.position = screenPos;
                // ★ 必须是 Il2Cpp 的 List ★：本环境里 `EventSystem.RaycastAll` 的签名要
                //   `Il2CppSystem.Collections.Generic.List<RaycastResult>`，
                //   喂 `System.Collections.Generic.List` 编译期就过不去（同 `ToolExecutor` 里的取物列表）。
                var list = new Il2CppSystem.Collections.Generic.List<RaycastResult>();
                es.RaycastAll(ped, list);
                if (list.Count == 0) return "<无命中>";
                var sb = new StringBuilder();
                for (int i = 0; i < list.Count && i < 3; i++)
                {
                    var g = list[i].gameObject;
                    if (i > 0) sb.Append(" < ");
                    sb.Append(g != null ? Path(g.transform) : "?");
                }
                if (list.Count > 3) sb.Append(" < …共" + list.Count + "个");
                return sb.ToString();
            }
            catch (Exception e) { return "<异常 " + e.Message + ">"; }
        }

        /// <summary>拉满父级（锚点四角 + 零偏移）。</summary>
        private static void FullScreen(RectTransform rt)
        {
            rt.anchorMin = Vector2.zero;
            rt.anchorMax = Vector2.one;
            rt.offsetMin = Vector2.zero;
            rt.offsetMax = Vector2.zero;
        }

        // ------------------------------------------------------------------
        // 小工具
        // ------------------------------------------------------------------

        /// <summary>名字 → 单位。`unitId` 给了就不按名猜（重名会猜错，见 `RowAvatars.Apply`）。</summary>
        private static WorldUnitBase Resolve(string name, string unitId)
        {
            try
            {
                if (!string.IsNullOrEmpty(unitId))
                {
                    var byId = UnitLookup.ResolveEx(name, unitId, out _);
                    if (byId != null) return byId;
                }
                var byName = UnitLookup.Resolve(name);
                if (byName != null) return byName;
                // 兜底：**玩家**。玩家行只拿得到名字（`ChatWindow.SetPlayerName` 只收一个 string），
                // 而玩家未必能命中 `UnitLookup` 的全图按名扫（那份名单的语义是"天下修士"）。
                // 这里直接比一次名字 —— 相等就用 `playerUnit`，不猜。
                if (IsPlayer(name)) return g.world.playerUnit;
                return null;
            }
            catch (Exception e)
            {
                ModMain.P("[BigPortrait] Resolve('" + name + "'): " + e.Message);
                return null;
            }
        }

        /// <summary>这个名字是不是玩家本人（异常安全；只在前一条路没命中时才问）。</summary>
        private static bool IsPlayer(string name)
        {
            try
            {
                var p = g.world.playerUnit;
                return p != null && SafeName(p) == name;
            }
            catch { return false; }
        }

        /// <summary>单位名（诊断日志用，异常安全）。</summary>
        private static string SafeName(WorldUnitBase unit)
        {
            try { return unit.data.unitData.propertyData.GetName(); }
            catch { return "?"; }
        }

        /// <summary>键名给人看（提示文案用）。只覆盖本文件会用的键，未知回落枚举名。</summary>
        private static string KeyLabel(KeyCode k)
        {
            if (k == KeyCode.Escape) return "ESC";
            return k.ToString();
        }

        // ==================================================================
        // 诊断探针实现（字段区有"为什么必须一次拿全"的完整理由）
        // ==================================================================

        private static float Now()
        {
            try { return Time.realtimeSinceStartup; } catch { return 0f; }
        }

        /// <summary>距 `since` 过了多少毫秒（`since<=0` 时返回 -1 = 无意义）。</summary>
        private static int Ms(float since)
        {
            if (since <= 0f) return -1;
            return (int)((Now() - since) * 1000f);
        }

        /// <summary>① 环境：`Screen` + **根**画布（缩放就在它身上）。
        ///
        /// 为什么必须打 `scaleFactor`：它就是"槽位 canvas 单位 → 屏幕像素"的换算系数，
        /// 而"渲染分辨率跟什么走"这个未知数**只能靠它和 texture 尺寸一起反解**
        /// （实机已知 80×80→120×120，而缩放 1.333 ⇒ 1.333 与 1.5 两种解释都还站着）。</summary>
        private static void LogEnv(RawImage origin = null)
        {
            string s = "?";
            try { s = Screen.width + "x" + Screen.height; } catch { }
            string c = "<none>";
            try
            {
                // 与 `ResolveCanvasRoot` **同一个解析口**（同一个判据、同一棵画布）——
                // 诊断若走别的路，就可能量的是"另一个画布"的缩放，等于白量。
                var p = ResolveCanvasRoot(origin);
                if (p != null)
                {
                    var cc = p.GetComponent<Canvas>();
                    if (cc == null) cc = p.GetComponentInParent<Canvas>();
                    if (cc != null)
                    {
                        var root = cc.rootCanvas != null ? cc.rootCanvas : cc;
                        c = "'" + root.name + "' 路径=" + Path(root.transform) +
                            " mode=" + root.renderMode +
                            " scaleFactor=" + root.scaleFactor.ToString("0.###");
                    }
                    else c = "'" + p.name + "'（父级上没有 Canvas）";
                }
            }
            catch { }
            ModMain.P("[BigPortrait] 环境：Screen=" + s + " 根画布=" + c);
        }

        /// <summary>② 原生窗（延迟复查用：`InitData` 是异步渲的，立刻量可能是空纹理）。</summary>
        private static void LogNative(string tag)
        {
            try
            {
                var big = _native;
                if (big == null) { ModMain.P("[BigPortrait] " + tag + "：原生窗代理为空"); return; }
                string rootInfo = "<取不到>";
                try
                {
                    // ★ 取 RectTransform 只能走 `UiRects.Of` ★：本环境里 `transform as RectTransform`
                    //   的托管包装器静态类型就是 `Transform`，向下转型**静默返回 null、不抛异常**
                    //   （09-13 两次实机事故都栽在这）。`UiRects` 是该铁律的唯一收口点。
                    var rrt = UiRects.Of(big.transform);
                    if (rrt != null)
                        rootInfo = Path(big.transform) + " rect=" + F(rrt.rect.width) + "x" + F(rrt.rect.height) +
                                   " pos=" + F(rrt.anchoredPosition.x) + "," + F(rrt.anchoredPosition.y) +
                                   " active=" + big.gameObject.activeInHierarchy;
                }
                catch (Exception e) { rootInfo = "<异常 " + e.Message + ">"; }
                ModMain.P("[BigPortrait] " + tag + "：原生窗=" + rootInfo);
                // ★ 关键：游戏自己那个大立绘槽位有多大 + 它渲出来的 texture 多大 ★
                // 游戏自己那两个关闭抓手的状态 —— 落日志是为了回答"我们还需要自己挂按钮吗"：
                // 若 btnClose 本来就显示，那用户看不到多半是样式/位置问题；若它隐藏，就说明必须挂。
                // 10-18 二测加量 `raycastTarget`：现场正是"btnMask 显示在、却拦不住点击"，
                // 而**只有 `raycastTarget` 才决定它吃不吃射线**。若为 false，则原生路的
                // "点空白处关闭"其实**不成立**（用户实测点它无反应），出口只剩 ESC / 右键 ——
                // 我们的对话窗也就必须靠 `SetBlocker` 自己拦。
                try
                {
                    var bc = big.btnClose;
                    var bm = big.btnMask;
                    string maskInfo = "null";
                    if (bm != null)
                    {
                        string rect = "?", rct = "?";
                        try
                        {
                            var rt = UiRects.Of(bm.transform);
                            if (rt != null) rect = F(rt.rect.width) + "x" + F(rt.rect.height);
                        }
                        catch { }
                        try
                        {
                            Graphic gi = bm.GetComponent<Image>();
                            if (gi == null) gi = bm.targetGraphic;
                            rct = gi != null ? gi.raycastTarget.ToString() : "<无 Graphic>";
                        }
                        catch { }
                        maskInfo = (bm.gameObject.activeInHierarchy ? "显示" : "隐藏") +
                                   " raycastTarget=" + rct + " rect=" + rect +
                                   " interactable=" + bm.interactable;
                    }
                    ModMain.P("[BigPortrait] " + tag + "：游戏自带 btnClose=" +
                              (bc == null ? "null" : (bc.gameObject.activeInHierarchy ? "显示" : "隐藏")) +
                              " btnMask=" + maskInfo);
                }
                catch { }
                // 原生窗自己的画布排序 + 兄弟序 —— 判断"它和对话窗谁在上"的原始依据
                try
                {
                    var cv = big.GetComponentInParent<Canvas>();
                    string cvs = cv == null
                        ? "<无 Canvas>"
                        : "order=" + cv.sortingOrder + " override=" + cv.overrideSorting + " mode=" + cv.renderMode;
                    var pt = big.transform.parent;
                    ModMain.P("[BigPortrait] " + tag + "：原生窗画布=" + cvs +
                              " 兄弟序=" + big.transform.GetSiblingIndex() + "/" + (pt != null ? pt.childCount - 1 : -1));
                }
                catch { }
                var ri = big.rimgUnit;
                if (ri == null) { ModMain.P("[BigPortrait] " + tag + "：原生 rimgUnit=null（这版没有这个节点？）"); return; }
                LogNode(tag + "：原生立绘槽位", ri);
            }
            catch (Exception e)
            {
                ModMain.P("[BigPortrait] " + tag + "：原生探针异常 " + e.Message);
            }
        }

        /// <summary>③ 自建槽位（延迟复查用）。</summary>
        private static void LogSlot(string tag)
        {
            try
            {
                if (_slot == null) { ModMain.P("[BigPortrait] " + tag + "：自建槽位为空"); return; }
                LogNode(tag + "：自建槽位", _slot);
            }
            catch (Exception e)
            {
                ModMain.P("[BigPortrait] " + tag + "：自建探针异常 " + e.Message);
            }
        }

        /// <summary>
        /// 一个 RawImage 槽位的**全部相关量**：路径 / rect(canvas 单位) / 世界缩放 /
        /// **屏幕像素尺寸** / texture 尺寸与真类名。
        ///
        /// `屏幕像素` = `rect × lossyScale`（画布缩放已含在 lossyScale 里）。
        /// 它和 `texture` 两个数一比，就能判定"渲染分辨率跟槽位走"到底是不是 × 画布缩放
        /// —— 这正是本功能最需要真机确认的那条地基。
        /// </summary>
        private static void LogNode(string tag, RawImage img)
        {
            string path = "<?>", rect = "?", lossy = "?", px = "?", tex = "<无纹理>";
            try { path = Path(img.transform); } catch { }
            try
            {
                var rt = img.rectTransform;
                if (rt != null)
                {
                    rect = F(rt.rect.width) + "x" + F(rt.rect.height);
                    var ls = rt.lossyScale;
                    lossy = F(ls.x) + "," + F(ls.y);
                    px = F(rt.rect.width * ls.x) + "x" + F(rt.rect.height * ls.y);
                }
            }
            catch { }
            try
            {
                var t = img.texture;
                if (t == null) tex = "<无纹理>";
                else tex = t.width + "x" + t.height + " 类=" + RealClassName(t.Pointer);
            }
            catch (Exception e) { tex = "<异常 " + e.Message + ">"; }
            ModMain.P("[BigPortrait] " + tag + "：" + path +
                      "｜rect=" + rect + "（canvas 单位）lossyScale=" + lossy +
                      " 屏幕像素≈" + px + "｜texture=" + tex);
        }

        /// <summary>零点几秒后再量一次（异步渲染完成后的真实尺寸）。</summary>
        private static void TickProbe()
        {
            if (_probeAt <= 0f) return;
            if (Now() < _probeAt) return;
            _probeAt = 0f;
            ModMain.P("[BigPortrait] ── 延迟复查（+" + (int)(ProbeDelay * 1000) + "ms，渲染应已完成）");
            if (_nativeOpen) LogNative("延迟·原生");
            if (_host != null) LogSlot("延迟·自建");
            LogBlockerProbe("延迟");     // ★ 拦截层的硬判据在这里（同帧那次不足为凭，见其注释）★
            // 不再重复打环境：决定性的一组数是 `LogNode` 里那三个 ——
            // rect(canvas 单位) · 屏幕像素(rect×lossyScale，**画布缩放已含在内**) · texture 尺寸。
            // 画布缩放单独再打一遍没有增量信息，反而要多存一个 origin 引用（行销毁后会变僵尸）。
        }

        /// <summary>节点路径（诊断用，限深 + 异常安全）。
        ///
        /// 限深 6 → 12（10-18 二测）：6 层**量不到对话窗根**（头像节点到 `UIChatAi` 正好 7 层），
        /// 于是"头像挂在哪个窗下"这个判据在日志里被截断，害得那轮只能靠推。
        /// 深度只影响日志长度，不影响任何逻辑。</summary>
        private static string Path(Transform t)
        {
            try
            {
                string s = t != null ? t.name : "?";
                for (int i = 0; i < 12 && t != null && t.parent != null; i++)
                {
                    t = t.parent;
                    s = t.name + "/" + s;
                }
                return s;
            }
            catch { return "?"; }
        }

        /// <summary>浮点转短串（日志用，避免 459.99997 这种噪声）。</summary>
        private static string F(float v)
        {
            return v.ToString("0.#");
        }

        /// <summary>画布渲染模式（宿主父级诊断用）。</summary>
        private static string CanvasMode(Transform t)
        {
            try
            {
                var c = t != null ? t.GetComponent<Canvas>() : null;
                if (c == null && t != null) c = t.GetComponentInParent<Canvas>();
                if (c == null) return "无";
                var root = c.rootCanvas != null ? c.rootCanvas : c;
                return root.renderMode + "/scale=" + root.scaleFactor.ToString("0.###");
            }
            catch { return "?"; }
        }

        /// <summary>单位 unitID（诊断用，异常安全）。
        ///
        /// 打它是为了排查"按名解析到了谁" —— 重名时按名解析会**猜错人**，
        /// 而日志里只有名字看不出错（两个"益婉容"看着一模一样）。</summary>
        private static string SafeId(WorldUnitBase unit)
        {
            try { return unit.data.unitData.unitID; }
            catch { return "?"; }
        }

        /// <summary>native 真实 Il2Cpp 类名（诊断用）。
        ///
        /// ⚠ **必须 `Marshal.PtrToStringAnsi`，绝不能 `IL2CPP.Il2CppStringToManaged`**：
        /// `il2cpp_class_get_name` 返回的是**裸 `const char*`**，喂给后者会被当成托管字符串对象
        /// 去读 length 字段 → 读到 C 串前 4 字节当长度（"Rend" ≈ 16.8 亿）→ 越界读几个 GB →
        /// **访问违例硬崩，托管 catch 抓不到**（09-13 实机崩溃根因）。
        /// 完整推导见 `PortraitCache.RealClassName`（同一份实现、同一份警告）。
        /// 这里是本文件自带的副本：不去调那个类，是为了让"放大立绘不碰像素缓存"这条
        /// 契约（`test_big_portrait_contract.py`）保持字面可查。</summary>
        private static string RealClassName(IntPtr ptr)
        {
            try
            {
                if (ptr == IntPtr.Zero) return "?";
                var cls = IL2CPP.il2cpp_object_get_class(ptr);
                if (cls == IntPtr.Zero) return "?";
                var name = System.Runtime.InteropServices.Marshal.PtrToStringAnsi(
                    IL2CPP.il2cpp_class_get_name(cls));
                return string.IsNullOrEmpty(name) ? "?" : name;
            }
            catch { return "?"; }
        }
    }
}
