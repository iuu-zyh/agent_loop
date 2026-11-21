/// <summary>
/// WorldStateFacts —— 「此刻世界是什么样」的**唯一读数层**（10-25 从 `NpcInitiativeMonitor`
/// 整段搬来；那次搬动的理由见本文件末尾的"为什么单独一个文件"）。
///
/// ## 它只报"电平"，不做任何判断
/// 本类每个方法回答的都是**一个可以直接量出来的事实**：
///   · 「逻辑战斗位亮着吗」   → `IsBattleLevel()`
///   · 「UI 层层顶叫什么」     → `UiLayerTopName(out name)`
///   · 「Mask 层有没有遮罩」   → `MaskLayerName()`
///   · 「剧情窗显示着吗」     → `DramaWindowReason()`（含"是哪一扇"）
/// 至于"这算不算忙""该不该拦""能不能弹窗"—— 那是 `WorldStateCore`（纯逻辑）的事。
///
/// ★为什么必须这样分★ 同一个"忙"字在本仓有**四个真实不同的视角**（能不能说话 /
/// 能不能做动作 / 排队器要不要让路 / 排队器能不能落刀），它们的**组合不同、顺序不同**，
/// 而且差异是**刻意的**（`YieldReason` 刻意不含对话窗，见那边的注释）。
/// 所以不能合并成一个"忙"；但**读数**必须只有一份，否则同一个电平会被读成两个值
/// （本仓已踩过：`DramaWindowName` 与面板闸门各判一套，同一时刻结论相反）。
///
/// ## 每个读者都吞异常，读不到一律当"没有"
/// 这一层的失败形态是"判据从不成立且不报错"，所以每个入口都必须自己兜住 ——
/// 抛出去会顺着游戏的帧回调/点击链往上炸，吞掉最坏只是"少拦一次"。
///
/// ## 为什么单独一个文件
/// 原先这 451 行住在 `npc/NpcInitiativeMonitor.cs`（1702 行）里，与主动互动的日节拍、
/// 候选扫描、日历、诊断开关**混在一起**。后果是：谁都能往那儿加判据，而它是全仓
/// 最危险的文件（一个假信号能让 NPC 永久哑掉且不报错）。搬出来之后：
///   · 读世界 → 本文件；组合判断 → `WorldStateCore`；接线 → `WorldStateGate`；
///   · 边界由 `tests/test_state_module_boundary.py` 钉住（对外依赖可数、零反向依赖）。
/// `NpcInitiativeMonitor` 保留了同名转发（见那边的注释），所以 24 个文件、73 处调用
/// **一行都不用改** —— 那些调用问的本来就是"现在什么状态"，位置变了，语义没变。
/// </summary>
using System;
using UnityEngine;

namespace AgentLoopBridge
{
    internal static class WorldStateFacts
    {
        /// <summary>我方自制确认窗的 UI 名（`ShowDramaService` 用游戏剧情类造的那扇：
        /// 赠送/偷窃/结缘/追问/自主互动确认都是它）。</summary>
        internal const string OwnDramaUiName = "UICustomDramaDyn";

        // ==================================================================
        // 一、最底层：可见性 / 层顶 / 名字
        // ==================================================================

        /// <summary>UI 实例是否**真的显示着**（存在 ≠ 可见）。`UIMgr.GetUI` 返回的是
        /// 已创建的实例——本项目的 AB 面板正是用 `GetUI(...)!=null` 判"是否已创建"，
        /// 可见性得另判。判法与 AbHotkeys 的层顶诊断同源：activeInHierarchy + Canvas.enabled。
        /// 拿不到可见性时保守返回 true（维持原判据语义，宁可拦不可漏判）。
        ///
        /// `internal`（09-22）：`DramaSegmentRecorder` 也要判战斗界面 `UIBattleInfo` 可不可见
        /// （分段判据的采样位 B2），**复用这一份**而不是再抄一遍——"判据只写一处"。
        /// ⚠ 定标探针 `DramaSegmentProbe` 里那份**是刻意留的副本**（它是临时采样件，
        /// 不反过来侵入本文件），那边改不改都不影响功能。</summary>
        internal static bool UiVisible(MonoBehaviour uiObj)
        {
            if (uiObj == null) return false;
            try { if (!uiObj.gameObject.activeInHierarchy) return false; }
            catch { return true; }
            try { if (!uiObj.gameObject.activeSelf) return false; }
            catch { }
            try
            {
                var cv = uiObj.GetComponent<Canvas>();
                if (cv != null && !cv.enabled) return false;
            }
            catch { }
            return true;
        }

        /// <summary>UI 名（诊断用；失败返回空串，不抛）。</summary>
        internal static string UiName(UIBase uiObj)
        {
            try { return uiObj == null ? "" : (uiObj.name ?? ""); }
            catch { return ""; }
        }

        /// <summary>取某层的层顶 UI（诊断/闸门共用；ui 未就绪返回 null）。</summary>
        internal static UIBase TopOfLayer(UILayer layer)
        {
            var ui = g.ui;
            return ui == null ? null : ui.GetLayerTopUI(layer, 0);
        }

        /// <summary>按名字取 UI（诊断/闸门共用；fail-open：key 不匹配只会返回 null）。</summary>
        internal static UIBase TopOfLayerOrNamed(string uiName)
        {
            var ui = g.ui;
            return ui == null ? null : ui.GetUI(new UIType.UITypeBase(uiName, (UILayer)0));
        }

        // ★════ UI 层顶电平缓存（10-21 segrec 零分配改造，逐字搬来，别再"优化"）════★
        //
        // 症状（10-21 真机 TickProfiler）：segrec 的每帧 Tick 问 `DramaWindowName` + `ActionShowing`，
        // 两者各读一次"层顶窗的名字"。`.name` **每读一次都跨引擎边界造一个新字符串**；
        // 而录制器钩子剧情一出现就常驻不摘 ⇒ gc0 从 +1~2/5s 涨到 +8~10/5s ⇒
        // 每 ~0.5s 一次 12~28ms 的 GC 停顿（用户体感＝"走路时走几步顿一下"）。
        //
        // 复用的依据：**窗口活着，名字就不变** ⇒ 按"层顶的原生指针"缓存名字；
        // 指针没动 ⇒ 直接用缓存，稳态零跨语言字符串。可见性**不**缓存（每帧照读，读它无分配）。
        //
        // 防伪：原生内存存在**地址复用**（旧窗销毁 → 新窗恰好建在同一地址），
        // 单靠指针判等可能被骗。对策是 TTL：每 `TopVerifyFrames` 帧强制重读一次名字对账
        // （~0.15s@128fps）。层顶任何真实变化都会让指针变化、当场重读；即便地址撞车，
        // 最坏电平错 0.15s 且自愈 —— 远小于核的交接判据粒度（A↑ 边沿实测 0.5s 级）。
        private const int TopVerifyFrames = 20;
        private static IntPtr _topPtr = IntPtr.Zero;       // 上次采到的 UI 层层顶原生指针
        private static string _topName = "";               // 该指针对应的名字（只在重读时刷新）
        private static bool _topNameValid;                 // 名字缓存是否可用（空层顶/异常后为 false）
        private static int _topVerifyCd = TopVerifyFrames; // TTL 计数（每次重读后复位）

        // 我方确认窗的查询键：整局建一次反复用（原实现每帧 `new` 一个一次性垃圾）。
        private static UIType.UITypeBase _dynKey;
        private static bool _dynKeyInit;

        private static void ResetTopCache()
        {
            _topPtr = IntPtr.Zero;
            _topName = "";
            _topNameValid = false;
            _topVerifyCd = TopVerifyFrames;
        }

        /// <summary>UI 层（层 0）层顶的**可见性 + 名字**（带指针缓存与 TTL，见上）。
        /// 返回层顶此刻是否可见；`name` 为层顶名（层顶空/取不到时为 ""）。
        /// 调用方组合"可见 && 名字含 X"，与改造前三个判据逐条等价 —— 唯一实现仍是这一处。</summary>
        internal static bool TopLayerState(out string name)
        {
            name = "";
            try
            {
                var ui = g.ui;
                if (ui == null) { ResetTopCache(); return false; }
                var top = TopOfLayer(UILayer.UI);
                IntPtr p = top == null ? IntPtr.Zero : top.Pointer;
                // 重读时机：层顶换人（指针变化）/ 缓存无效 / TTL 到期对账。三者之外零字符串跨语言。
                if (p != _topPtr || !_topNameValid || --_topVerifyCd <= 0)
                {
                    _topPtr = p;
                    _topName = UiName(top);
                    _topNameValid = p != IntPtr.Zero;   // 空层顶没有"名字"可缓存（下个新指针必然重读）
                    _topVerifyCd = TopVerifyFrames;
                }
                name = _topName;
                return UiVisible(top);
            }
            catch { ResetTopCache(); return false; }
        }

        // ==================================================================
        // 二、电平读数（每个只回答一个"亮不亮/叫什么"）
        // ==================================================================

        /// <summary>玩家单位读数（三态枚举定义在 `WorldStateCore.PlayerUnitRead` —— 核是零依赖的，
        /// 测试工程只链接那一个文件，所以枚举必须住在核那一侧）。</summary>
        internal static PlayerUnitRead ReadPlayerUnit()
        {
            try
            {
                if (g.world == null) return PlayerUnitRead.Absent;
                return g.world.playerUnit == null ? PlayerUnitRead.Absent : PlayerUnitRead.Present;
            }
            catch { return PlayerUnitRead.Error; }
        }

        /// <summary>**逻辑战斗位**：`g.world.battle.isBattle`。
        ///
        /// ⚠ 它是战斗位的**一半**，不是"在不在打"全部答案 —— 结算期它可能先落
        /// （见 `BattleActive` 的实测注）。要不要取或，由调用方按自己的语义决定：
        /// `BusyReason` 把它单列一条、`BattleActive` 与界面位取或，两处**刻意不同**。</summary>
        internal static bool IsBattleLevel()
        {
            try
            {
                var b = g.world != null ? g.world.battle : null;
                return b != null && b.isBattle;
            }
            catch { return false; }
        }

        /// <summary>**逻辑战斗位的原始读数**（11-19 加，只为诊断）。
        ///
        /// ★为什么需要它★ 用户报「检测会频繁显示自己正在忙/正在处于战斗中，但实际不是」。
        /// `IsBattleLevel()` 把三种完全不同的情况压成同一个 false：
        ///   · `g.world == null`（还没进世界）；
        ///   · `g.world.battle == null`（战斗管理器没建）；
        ///   · `isBattle` 真的为 false（确实没打）。
        /// 而反过来，"报战斗中"时的现场是哪种，也直接影响怎么修：
        ///   · `isBattle=true` 但玩家看画面不像在打 ⇒ **游戏自己的位卡住了**（我们无能为力，只能记证）；
        ///   · `isBattle=false` 却判战斗中 ⇒ **我们的判据有 bug**（要修我们的）。
        /// 两种修法完全相反，所以必须能分开。异常也单独报（`err`），不许再被吞成 false。</summary>
        internal static string BattleLevelRaw()
        {
            try
            {
                if (g.world == null) return "world=null（未进世界）";
                var b = g.world.battle;
                if (b == null) return "world.battle=null（战斗管理器未建）";
                return "isBattle=" + b.isBattle;
            }
            catch (Exception e) { return "读异常：" + e.GetType().Name; }
        }

        /// <summary>**战斗界面**是否可见（`UIBattleInfo`）。战斗位的另一半。</summary>
        internal static bool BattleUiVisible()
        {
            try
            {
                var ui = g.ui;
                if (ui == null) return false;
                var bi = ui.GetUI<UIBattleInfo>(UIType.BattleInfo);
                return UiVisible(bi);
            }
            catch { return false; }
        }

        /// <summary>「此刻在战斗中吗」—— 两个信号**取或**的**战斗位合成读数**。
        ///
        /// 给 `BattleOutcome` 判"切磋打完没有"、`BattleFlowCore` 判战中、`BattleEndHook`
        /// 与热键闸共用（**判据只写一处**，别再各写各的）。
        ///
        /// 两个信号取或，因为我们对"切磋这场战斗"到底触发哪个位是零样本的：
        ///   · `g.world.battle.isBattle` —— 真判据（`BusyReason` 的注释记着：判空是恒真的错判据）；
        ///   · `UIBattleInfo` 可见 —— 原注释写着「结算期 isBattle 可能已落，故保留」，
        ///     也就是说这两个位**本来就会不一致**，只押一个等于把功能压在未验证的单一事实上
        ///     （同 `DramaData.unitLeft/unitRight` 那次教训：错的数据源会自信地给出反事实）。
        /// 任一读不到都不算数（异常一律当"没有这个信号"），两个都读不到 → false。
        ///
        /// ★09-22 真机实测（"取或"是对的选择，别改成只押一个）★
        /// attack 那两场：B2（`UIBattleInfo`）在战斗结束后**先落**（22:52:44.3），而 B1（`isBattle`）
        /// 一直为真**撑到整条链收尾**（22:53:26）—— 从进战斗到处置页那 51 秒里，后半段唯一续命的
        /// 就是 B1。若当初只认 B2（或以为 B1 会在结算期先落），台账早在处置页之前就被静默规则收口了。</summary>
        internal static bool BattleActive()
        {
            return IsBattleLevel() || BattleUiVisible();
        }

        /// <summary>
        /// **进战斗预告窗是否正显示**（`BattleOutcome` 的②b/D 支 + `BattleFlow` 的战前判据共用）。
        ///
        /// ★09-22 真机探针改正（别再退回 `GetUI(类名)`）★
        /// 那扇窗的**节点真名是 `MapBattlePre`**（没有 `UI` 前缀，占 UI 层层顶）—— 实测两场攻击一致：
        /// 关窗那一帧与进战斗后各采到一次 `层0顶=MapBattlePre(on)`。旧实现按**类名**
        /// `UIMapBattlePre` + `GetUI(new UIType.UITypeBase(...))` 去查，结果那个位**全程为 0 且不报错**，
        /// 让 D 支与②b 静默退化成一堆永远不成立的代码（正是本仓最警惕的"错数据源自信地沉默"）。
        /// 现在改用与 `DramaWindowReason` **同一套**"UI 层层顶名字族"判据 —— 那正是这次真正采到它的路径。
        ///
        /// 查不到/取不到一律 false，且**只意味着"没看见"**，不许当"玩家没应战"的证据（见 D 支的口径）。
        /// </summary>
        internal static bool BattlePreShowing()
        {
            try
            {
                string n;
                return TopLayerState(out n) &&
                       n.Length > 0 && n.IndexOf("MapBattlePre", StringComparison.OrdinalIgnoreCase) >= 0;
            }
            catch { return false; }
        }

        /// <summary>
        /// **"游戏动作进行中"的界面是否正显示**（`DramaSegmentCore` 判"同一场交互的下一页"用）。
        ///
        /// ★09-23 论道实测★：一次论道被切成 3 段（22811 / 22813 / 22831+22848+22847+22815），
        /// 玩家在最后一页点「对话」只投得到最近那段 —— 前两句（含"论道请求"）静默丢。
        /// 根因：核的交接判据只认"同窗翻页"与"战斗转场（B1/B2 起跳）"，而原生多窗交互页间是
        /// "关窗 → 空 1~2s → 新窗"，两者都不满足。日志给了**结构性信号**：那几段页间隙里
        /// **层顶就是 `ActionCourseInfo`** ⇒ 用它当"同一个场景还在进行"的交接证据。
        ///
        /// 读**层顶**（页开着时它被剧情窗盖住 → false），这正是核在关窗那一刻要的语义。
        /// </summary>
        internal static bool ActionShowing()
        {
            try
            {
                string n;
                return TopLayerState(out n) &&
                       n.Length > 0 && n.IndexOf("ActionCourseInfo", StringComparison.OrdinalIgnoreCase) >= 0;
            }
            catch { return false; }
        }

        /// <summary>
        /// **"游戏里此刻有一个行动在进行"的存在性判据**（09-23，投递「动作进行中」标记用）。
        ///
        /// 与 `ActionShowing()` 的区别：那个读**层顶**（页开着时被剧情窗盖住 → false —— 核在
        /// 关窗那一刻用它当交接证据）；本判据读**存在性**（按名 GetUI + 可见性），窗盖不盖住都算数：
        /// 论道/邀约这类交互进行中时，点「对话」投给模型的上下文要带上"还在进行"这个事实，
        /// 否则模型会把已发生的交互当成没开始、再去发起一遍同款工具（实测：论道页上又调论道）。
        ///
        /// 判据与 `BusyReason` 里查 `UIChatAi` 同形（按名 GetUI + 可见性），**不看层顶**。
        /// 取不到一律 false（只意味着"没看见"，不阻止任何事）。
        /// </summary>
        internal static bool ActionActive()
        {
            try
            {
                var ui = g.ui;
                if (ui == null) return false;
                var course = ui.GetUI(new UIType.UITypeBase("ActionCourseInfo", (UILayer)0));
                return UiVisible(course);
            }
            catch { return false; }
        }

        /// <summary>我方自制确认窗（`UICustomDramaDyn`）此刻是否**真的显示着**。
        /// 判可见而不是判存在：`CloseUI` 不一定销毁实例，判存在会把"刚关掉的窗"也算剧情中。</summary>
        internal static bool OwnDramaWindowVisible()
        {
            try
            {
                var ui = g.ui;
                if (ui == null) return false;
                if (!_dynKeyInit)
                {
                    _dynKey = new UIType.UITypeBase(OwnDramaUiName, (UILayer)0);
                    _dynKeyInit = true;
                }
                return UiVisible(ui.GetUI(_dynKey));
            }
            catch { return false; }
        }

        /// <summary>本次读到的 UI 层层顶名字（空 = 没有 / 取不到）。
        /// **注意它不含"是否可见"** —— 要"可见且名字是 X"请用 `TopLayerState` 或
        /// `DramaWindowReason` / `BattlePreShowing` / `ActionShowing`。</summary>
        internal static string UiLayerTopName()
        {
            try
            {
                string n;
                return TopLayerState(out n) ? (n ?? "") : "";
            }
            catch { return ""; }
        }

        /// <summary>对话窗（`UIChatAi`）是否可见 —— 正在与某个 NPC 对话。
        /// 用 GetUI 而不是"层顶名"：对话窗可能被别的面板盖住，但"正在对话"这个事实不变。</summary>
        internal static bool ChatWindowVisible()
        {
            try
            {
                var ui = g.ui;
                if (ui == null) return false;
                var chat = ui.GetUI(new UIType.UITypeBase("UIChatAi", (UILayer)0));
                return UiVisible(chat);
            }
            catch { return false; }
        }

        /// <summary>
        /// 游戏自己的**剧情页**（`UIDramaDialogue`）是否可见 —— **按名查那一扇窗本身**。
        ///
        /// ★为什么需要它（10-27 真机事故）★
        ///   原来判"我开的那页还在不在"用的是 `DramaWindowReason()`，而它判的是
        ///   **UI 层层顶名字含不含 `Drama`** —— 那是个**代理量**：
        ///   量的是"此刻最上面是谁"，不是"我那扇窗还在不在"。
        ///   真机：页 t=275.1s 开出、玩家看到 t=384s 才点，中间只是引擎自己的动作条/奖励窗
        ///   盖到上面过一次，旧判据就判成"玩家关窗" ⇒ 提前 46 秒放行 + 结局错记成 closed。
        ///
        /// 与 `ChatWindowVisible` 同一款判据（`GetUI(按名)` + `UiVisible`，判可见不判存在）：
        /// 同一个对象，问它自己的 `activeInHierarchy`，不经过层顶。
        ///
        /// ⚠ 别改成持有 `UICustomDramaBattleNPCFailed2` 实例来判 —— 那个类基链止于
        ///   `Il2CppObjectBase`，**不继承 MonoBehaviour**，它是**控制器**不是窗口，没有 gameObject。
        /// </summary>
        internal static bool DramaPageVisible()
        {
            try
            {
                var ui = g.ui;
                if (ui == null) return false;
                return UiVisible(ui.GetUI(new UIType.UITypeBase(DramaPageUiName, (UILayer)0)));
            }
            catch { return false; }
        }

        /// <summary>游戏剧情页在 UI 系统里的**类名**（`g.ui.GetUI` 的键）。
        /// 实机层顶名与 `DramaSegmentProbe` 的窗型读数都是它。</summary>
        private const string DramaPageUiName = "UIDramaDialogue";

        /// <summary>玩家死亡/复活选择窗（`UIMapDie`）是否可见。
        /// 原本的 `isDead` 判据不存在（全装配扫描 hits=0），改判这扇窗。</summary>
        internal static bool DeathWindowVisible()
        {
            try { return UiVisible(TopOfLayerOrNamed("UIMapDie")); }
            catch { return false; }
        }

        /// <summary>`MaskLayerName` / `WindowLayerName` 在"层顶可见、但名字读到空"时给的占位符。
        /// **不能返回空串**：上层判"这一层挡不挡人"用的是 `!string.IsNullOrEmpty(name)`。
        /// 理由见 `VisibleLayerName`。</summary>
        private const string UnnamedLayerTop = "(未命名)";

        /// <summary>Mask 层（4 是 Window，5 是 Mask）层顶 UI 的名字；"" = 该层没有可见的层顶。
        /// Mask = 全屏输入遮罩层（模态确认/剧情/结算在挡输入）。</summary>
        internal static string MaskLayerName()
        {
            try
            {
                var m = TopOfLayer(UILayer.Mask);
                return VisibleLayerName(m);
            }
            catch { return ""; }
        }

        /// <summary>Window 层（游戏自带窗口）层顶 UI 的名字；"" = 该层没有可见的层顶。
        /// "可见但没名字"同 `MaskLayerName`，返回占位符。</summary>
        internal static string WindowLayerName()
        {
            try
            {
                var w = TopOfLayer(UILayer.Window);
                return VisibleLayerName(w);
            }
            catch { return ""; }
        }

        /// <summary>"可见 ⇒ 给名字（没名字就给占位符）；不可见 ⇒ 空串"。两个层判据共用这一处，
        /// 免得哪天一边加了占位符、一边忘了（那就会一层拦一层不拦）。
        ///
        /// ★"可见但没名字"为什么必须返回非空串（10-25 复核修正）★
        /// 改造前那两个判据写的是 `if (UiVisible(top)) return "Mask 层有模态遮罩：" + UiName(top)`，
        /// 搬进 `state/` 时一度简化成 `return UiVisible(m) ? UiName(m) : ""`，上层再判
        /// `!string.IsNullOrEmpty(name)`。两者只在**一个**情形下不同：层顶**可见、但名字读到空** ——
        /// `UiName` 在"读名抛异常"或"GameObject 名恰好为空"时给 ""，而 `UiVisible` 那时仍可能
        /// 是 true（它把"取可见性抛异常"当**保守可见**，宁可拦）。
        /// 也就是说那次简化的方向是 **fail-open**：模态遮罩层明明挡着，我们却去弹原生窗 ——
        /// 那些窗会沉进游戏系统队列不出队，正是本模块存在的理由。
        /// 契约见 `tests/test_state_gate_wiring.py::test_layer_top_blocked_is_not_encoded_as_empty_name`。</summary>
        private static string VisibleLayerName(UIBase top)
        {
            if (!UiVisible(top)) return "";
            try
            {
                string n = UiName(top);
                return n.Length > 0 ? n : UnnamedLayerTop;
            }
            catch { return UnnamedLayerTop; }
        }

        /// <summary>**单层原始读数**（11-19 加，只为诊断）。
        ///
        /// `MaskLayerName` / `WindowLayerName` 把四种情况压成同一个 ""：
        ///   · 这一层压根没有层顶；
        ///   · 层顶存在但 `activeInHierarchy/Self` 为假；
        ///   · 层顶存在但承载它的 `Canvas.enabled` 为假；
        ///   · 读的时候抛异常。
        /// 而"用户报频繁判忙、但看着不忙"时，**第二、三种正是最像误报的形态** ——
        /// 游戏把面板设成不可见却留在层顶，或者 Canvas 被关掉但节点还在。
        /// 那时我们判"不忙"是对的；反过来说，如果这三种里我们判了"忙"，
        /// 就说明 `UiVisible` 的保守分支（读可见性抛异常 ⇒ 当可见）在那台机器上生效了。
        /// 两种结论对应完全不同的修法，所以必须能分开看。
        ///
        /// 输出形如：`有:UIConfirmDrama(on)` / `有:X(off:hierarchy)` / `有:X(off:canvas)` / `空` / `err:Type`。</summary>
        internal static string LayerRaw(UILayer layer)
        {
            try
            {
                var top = TopOfLayer(layer);
                if (top == null) return "空";
                string n = "";
                try { n = UiName(top); } catch { }
                if (n.Length == 0) n = UnnamedLayerTop;
                try
                {
                    if (!top.gameObject.activeInHierarchy || !top.gameObject.activeSelf)
                        return "有:" + n + "(off:hierarchy)";
                }
                catch { return "有:" + n + "(?hierarchy)"; }
                try
                {
                    var cv = top.GetComponent<Canvas>();
                    if (cv != null && !cv.enabled) return "有:" + n + "(off:canvas)";
                }
                catch { }
                return "有:" + n + "(on)";
            }
            catch (Exception e) { return "err:" + e.GetType().Name; }
        }

        /// <summary>主世界 HUD（`MapMain`）是否就绪。场景 / 加载 / 主菜单里为 false。</summary>
        internal static bool MainWorldReady()
        {
            try { return g.ui != null && g.ui.GetUI(UIType.MapMain) != null; }
            catch { return false; }
        }

        // ==================================================================
        // 三、合成读数（只有一个，且组合极简 —— 有判断的都在 WorldStateCore）
        // ==================================================================

        /// <summary>
        /// **"此刻有没有剧情窗正显示"**（`BusyReason` / `YieldReason` / `AbPanelProber` 共用）。
        ///
        /// 判什么（覆盖两族窗口，缺一不可）：
        ///   ① **我方自制确认窗 `UICustomDramaDyn`** —— `ShowDramaService` 用游戏剧情类造的
        ///      （赠送/偷窃/结缘/追问/自主互动确认都是它）。必须**判可见**（见
        ///      `OwnDramaWindowVisible` 的注释）。
        ///   ② **游戏自己的剧情窗家族** —— 实机名字是 `UIDramaDialogue` / `UIDramaBase`
        ///      （日志实证：UICustomDramaDyn 全程 0 次、UIDramaBase 13 次、UIDramaDialogue 5 次），
        ///      共同点是名字含 `Drama`。按层顶扫描认家族，而不是硬编码单名。
        ///
        /// 返回：空串 = 没有剧情窗；非空 = 人话原因（"剧情窗开着（UIDramaDialogue）"）。
        ///
        /// ★为什么必须共用（历史）★ 之前是两套各写各的 —— 这里判「可见 + 名字家族」，
        /// 面板那边判「存在 + 单一硬编码名」。后果是**同一时刻两边结论相反**：
        /// 面板闸门说"剧情窗挡着不让建"，忙判据却说"玩家不忙"。
        /// 现在唯一的组合逻辑在 `WorldStateCore.DramaWindowReason`（纯函数、可单测），
        /// 本方法只负责把两个电平喂给它 —— 这样"读"和"判"各只有一份。
        /// </summary>
        internal static string DramaWindowReason()
        {
            try
            {
                return WorldStateCore.DramaWindowReason(OwnDramaWindowVisible(), UiLayerTopName());
            }
            catch { return ""; }
        }

        // ==================================================================
        // 四、诊断（定标探针用；不属于判据，但读的是同一批原始件）
        // ==================================================================

        /// <summary>UI 层层顶的名字（空 = 该层没有可见的顶 / 取不到）。给定标探针用：
        /// 量"层顶是谁"这件事本身（实测我们的窗全在层 0 顶：`DramaDialogue`/`BattleInfo`/
        /// `BattleEnd`/`GetReward`/`BattleExit`/`MapBattlePre`）。</summary>
        internal static string LayerTopNameOfUi()
        {
            try { return UiName(TopOfLayer(UILayer.UI)); } catch { return ""; }
        }

        /// <summary>逐层快照：8 层各层顶 UI 名 + 可见性。强制触发时每次打一行，
        /// 用来确认"什么状态下哪一层有东西"，据此定/校状态闸的层判据。
        /// 层序与 UILayer 枚举一致：0 UI / 1 UITop / 2 Guide / 3 Loading / 4 Window / 5 Mask / 6 FullEffect / 7 TempUI。</summary>
        internal static string LayerSnapshot()
        {
            var names = new[] { "UI", "UITop", "Guide", "Loading", "Window", "Mask", "FullEffect", "TempUI" };
            var sb = new System.Text.StringBuilder();
            for (int i = 0; i < names.Length; i++)
            {
                string seg;
                try
                {
                    var ui = TopOfLayer((UILayer)i);
                    seg = ui == null ? "(空)" : (UiName(ui) + (UiVisible(ui) ? "(on)" : "(off)"));
                }
                catch { seg = "(err)"; }
                if (sb.Length > 0) sb.Append(' ');
                sb.Append(names[i]).Append('=').Append(seg);
            }
            return sb.ToString();
        }

        /// <summary>**判据直接依赖的那几个位的原始读数**（11-19 加，用户报"频繁判忙/判战斗中"）。
        ///
        /// 与 `LayerSnapshot` 的分工：那个是**八层横向**扫（回答"哪一层有东西"）；
        /// 这个是**判据纵向**（回答"`BusyReason` 那五条各自此刻读到什么"）。
        /// 排查误报时要的是后者 —— 因为误报一定来自某一条判据把某个电平读反了，
        /// 而那一条具体读的位，往往不在层顶上（例如 `isBattle`、`UIChatAi` 的可见性）。
        ///
        /// 三项都**保留原始形态**（不压成 bool）：
        ///   · 战斗位：`isBattle=…　UI=on/off`（并给出 `world`/`battle` 是否为空）；
        ///   · Mask / Window 层：`有:X(off:canvas)` 能看出"节点在但不可见"；
        ///   · 挂起/收尾两个注入位：注入缺失会显示 `未注入`（那是恒 false，会静默放宽判据）。
        /// </summary>
        internal static string BusyEvidenceRaw()
        {
            var sb = new System.Text.StringBuilder();
            sb.Append("战斗位[").Append(BattleLevelRaw()).Append(']');
            sb.Append(" 战斗UI[").Append(BattleUiVisible() ? "on" : "off").Append(']');
            sb.Append(" Mask[").Append(LayerRaw(UILayer.Mask)).Append(']');
            sb.Append(" Window[").Append(LayerRaw(UILayer.Window)).Append(']');
            try { sb.Append(" 对话窗[").Append(ChatWindowVisible() ? "on" : "off").Append(']'); } catch { }
            try { sb.Append(" 我方剧情窗[").Append(OwnDramaWindowVisible() ? "on" : "off").Append(']'); } catch { }
            try { sb.Append(" 主世界[").Append(MainWorldReady() ? "ready" : "not-ready").Append(']'); } catch { }
            return sb.ToString();
        }
    }
}
