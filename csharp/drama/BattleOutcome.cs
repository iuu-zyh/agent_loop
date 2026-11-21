/// <summary>
/// BattleOutcome —— 战斗类动作（`world_ai_action` 的 spar / attack）跨过战斗之后的**结局收口器**：
/// 只负责**"什么时候"算结局已定**；**内容**（链上原文 + 玩家选择）一律读段的区间
/// （`DramaSegmentRecorder.TryReadRangeFromChain`）—— 09-23 用户定：段是唯一内容流。
///
/// 09-22 从 `SparOutcome` 泛化而来（原名只说得出切磋，而攻击链现在走的是同一套机械）。
/// `_op` 是这份台账唯一的分叉点，两支的差异全部集中在**收口判据**上，见下面的 A/B/C 三节。
///
/// ## 09-23 改动（用户定：这条链的内容也交给段）
///   · 删掉自建的 `_lines` 与 `NoteDramaLine` —— 收口时读 `[锚点, 现在]` 的区间，
///     读走即标消费（之后玩家点「对话」不会把这批内容再投一遍）；
///   · 收口判据里"有没有内容"改问区间行数（`Lines()`，只读不消费）；
///   · 每 5s 的续期顺带发一次**链心跳**（`DramaSegmentRecorder.RenewChain`）：段在挂起期
///     不被固定预算掐断 —— 几分钟的长战斗不会把"战前那句"切到上一段去；
///   · 锚点 = **凭证首次登记时刻**（早于 `CreateAction`）⇒ 战前的 21204 提问页与
///     「接受切磋」都在区间里（拿武装时刻当锚会漏掉它们）；
///   · 处置页（玩家胜）**先记页再收口**（顺序在 `DramaAiOption` 里钉着）：收口读区间，
///     先收口就缺那一行；收口之后的选择/尾页留在段里（用户 09-23 定：后面的交段）。
///
/// ## 为什么要有它（切磋那次的真机实证，别改回去）
/// 原设计是「选项一落定就回，不等战斗」（09-14 拍板，理由写在 DramaDrillChoice 头注释里）。
/// 那条拍板的前提**是错的**：它假设 `UnitActionRoleDrill.OnEnd` 要等整场切磋打完才触发。
/// 真机 Player.log 4895→4899 打的是另一个故事 ——
///   `WorldBattleMgr:IntoBattle` → `UnitActionRoleDrill:OnEnd()` → `[UnitAction] spar OnEnd 挂起命中 slot=2`
/// OnEnd 在**进战斗那一刻**就跑了。于是兜底先收口成「没有明确答复」，而选项判据（挂在
/// `ClickOption` 的 postfix 上）要等整个选项效果同步跑完才轮到 —— 晚了一步，req_id 已被
/// WsClient 摘走（`WsClient.cs:373 if (reqId == null) return;`），**真判据被静默丢弃**。
/// 玩家看到的后果：明明应战并打完了，NPC 却说「你未作答复」，按"你没应战"演下去。
///
/// ## 收口靠事件，不靠把超时放大
/// 一场战斗多久不可知（取决于玩家怎么打、开不开自动），所以"给个大超时数字"两头都错：
/// 数字小了误伤慢的战斗，数字大了对端真失联时人要干等。本类的口径是：
///   · **证据**（读到"还在战斗中"、抓到一句台词、读到 BattleEnd、链里有点击）→ 滑动续期，
///     于是**打多久都不会被掐**；
///   · 静默 60s / 总上限 10 分钟 → 收口成"没看到结局"；
///   · 世界重载/读档 → 立刻按**已抓到的部分**收口（`DramaGate.ForceResolveAll` 收其余槽位）。
///     `ModIds.AiActionGate` 这个 busy 键是所有动作工具共用的，不释放 = 整套动作系统瘫到重启。
///
/// ## 切磋（`_op == "spar"`）的收口规则 —— 逐字未动
///   · 主 = `BattleEndMgr.BattleEnd` 且已抓到台词 → 立刻收。实证那条链是
///     `ClickOption → NextDialogue → BattleEndMgr.BattleEnd`（Player.log 5088-5096），即玩家
///     **翻完战后窗最后一页**才跑，天然排在所有台词之后；
///   · 辅 = `NpcInitiativeMonitor.BattleActive()`（`isBattle` **或** `UIBattleInfo` 可见）翻假，
///     它可能落在战后窗**之前** ⇒ 只认到软信号时必须再等静默 20s（有台词）/15s（没有）；
///   · 战后那两页是**同一个剧情窗翻页**，抓到第一句就收会把第二页切掉 + 凭证已吊销 ⇒
///     第二页重新长出那颗没用的「对话」按钮（用户报的原症状）。
///
/// ## 攻击（`_op == "attack"`）为什么不能照抄
/// 攻击链比切磋长，而且**夹着玩家的决定**（配表事实，见 `docs/APPENDIX.md` 的 attack 行）：
///   · 链上没有"我方认得的先决窗" —— 进战斗前那扇 `UIMapBattlePre` **不是剧情窗**，
///     `DramaAiOption`/`ClickOption` 两个挂点都看不见它 ⇒ 登记必须在 `CreateAction` 之前就摘走
///     （`ToolExecutor` 的 attack 分支），于是本类**不依赖 attack 的 OnEnd 何时跑**；
///   · 链里可能夹着**道具选择面板**（选项 `204036`/`204044` 的 `function=itemSelect…`），
///     那段时间一句台词都不产生 ⇒ 若沿用"静默 60s 就收"，会把"玩家正在翻背包"误杀成没结局；
///   · 所以攻击额外认两类证据：**场上有剧情窗开着**（= 玩家在读，链没断）、
///     **链里点了选项**（= 链在推进，且总上限随之顺延）。这两条刻意**不喂给切磋**，
///     免得把上面那条逐字保留的静默规则改掉。
///
/// ### 三种终局与判据（全是配表确定事实，不解析文本）
///   A. **NPC 战胜**（玩家败）→ 等到所有 UI 走完：玩家点到带 `battleExit_*` 的那个选项，
///      即 id ∈ {100004, 100005}（全表 7987 行里 `battleExit_*` 只有 30 个，落在通用位段
///      100001~100006 的**只有这两个**，其余 28 个是各剧情自己的 id，攻击链吃不到）。
///      实证尾页：`20407`「东西还不错，那我便饶了你这一次。」=100005、
///      `20406`/`20413`「…那么我先走了。」=100004。
///      ⚠ 切磋的尾页 `21402` 用的是 `100002「离开」`（`function=0`，**不带** battleExit）⇒
///      这条判据天然吃不到切磋，切磋继续用上面那三条，别拿它去替换 `BattleEnd`。
///   B. **玩家战胜**（NPC 败）→ 收在**处置页** `dramaId ∈ {20403, 20404}`（NPC 求饶 /
///      NPC 任凭处置，选项是胜方手里的「击杀此人」「放你一马」…）。之后是玩家的处置权，
///      不该让 NPC 的回合干等"别人替我决定生死"；收口的同一刻吊销凭证并**放行「对话」按钮**
///      （见 `DramaAiOption`），玩家点它就能接着和正在求饶的 NPC 说话。
///      这一支的结果带 `verdict_page=true`：Python 据此写"他还没决定我的生死"，
///      模型才不会自己编一个结局出来。
///   C. **玩家逃跑** → 同 A 那把尺子（实证 `20303` 的「离开」就是 `100005 = battleExit_0`）。
///   D. 战斗压根没起来（没点 `UIMapBattlePre`）→ `battle_seen=false`，静默 60s 收口。
///
/// ### 09-22 真机实测（两场 attack，探针 `_diag_segment_probe.txt`，结论都别再推翻）
///   · **B 支成立**：赢局是"战斗结束**直达**处置页"（没有先弹 20402 要玩家点一下），处置页在台账
///     还活着时被认领并当场收口（22:53:14.818 探针 → .833 收口 → .866 该页放行注入）；
///     `BattleEndMgr.BattleEnd` 三次事件全在收口之后或同刻（赢局两次 `arg0=True（台账在飞=False）`、
///     逃跑一次 `arg0=False` 与"离开"同毫秒）⇒ **规则 2 不会咬掉 B 支**；
///   · **承重的是 B1**（见 `NpcInitiativeMonitor.BattleActive` 的实测注）：B2 在战斗结束后先落，
///     B1 撑到整条链收尾（51 秒那段的后半程全靠它）；attack 那条"剧情窗可见=续期"这两场**没派上用场**，
///     留着可以，**别当它是承重件**；
///   · **进战斗预告窗的真名 = `MapBattlePre`**（UI 层层顶，无 `UI` 前缀）⇒ 判据改用共用的
///     `NpcInitiativeMonitor.BattlePreShowing()`：旧写法按类名 `UIMapBattlePre` 猜，恒 false 且不报错；
///   · **加载层全程未亮**（12s 观察窗里也没亮）⇒ 交接判据不该用它；
///   · **规则 1 会被规则 2 抢先**：逃跑那支"点到离场选项"与 `BattleEnd` 同毫秒，17ms 后由规则 2 收口，
///     比规则 1 的 1.5s 宽限早 1.48s ⇒ 规则 1 挪到规则 2 **之后**当兜底（只在 `BattleEnd` 不来时才有戏）；
///   · **收口 ≠ 链演完**：处置页收口后尾页 10 秒才来（22:53:24.474），关窗在 22:53:26.019 ——
///     那段时间 busy 键已释放而 `isBattle` 仍为真（游戏自己在结算）⇒ 见 `DramaGate.ChainHold`。
///
/// ## 判胜负：不解析，原样带归属
/// `RoleLogLocal` 是带 `condition/group/{call|B|A}` 占位的**语句池**（措辞随人设、关系、性别
/// 称谓变），拿"我输了/我赢了"做关键词匹配迟早翻车。所以本类只把**游戏自己的成品句 + 谁在说**
/// 原样交给模型，谁赢谁活由模型读出来。这与"行动记录原文一字不改"的既有铁律同构。
/// 同理，`battleExit_0` / `battleExit_1` 的 0/1 **不许当生死位用**（放过页两支都出现过），
/// 与 `BattleEnd(Boolean)` 那个 bool 一样只当触发用。
///
/// 线程：全部 Unity 主线程（`ClickOption` prefix / `InitData` postfix / 帧回调 / Harmony 钩子）。
/// 铁律：所有对外入口都要能被异常吞掉而不影响游戏链（同 UnitActionHooks）。
/// </summary>
using System;
using System.Collections.Generic;
using Newtonsoft.Json.Linq;
using UnityEngine;

namespace AgentLoopBridge
{
    internal static class BattleOutcome
    {
        /// <summary>无证据静默多久就放弃等结局（秒）。有证据就一直续，所以这个值只惩罚"什么都没发生"。</summary>
        private const int SilenceSeconds = 60;
        /// <summary>钝兜底：从武装（攻击：从最后一次点击）算起的总上限（秒）。只防证据源本身出毛病。</summary>
        private const int HardCeilingSeconds = 600;
        /// <summary>战斗结束但一句台词都没抓到时，再宽限几秒（秒）。</summary>
        private const int AfterBattleEndGrace = 15;
        /// <summary>只靠轮询信号判出"战斗结束"时，还要看几句**静默**（秒）。
        /// 为什么要给静默：战后那几行是**同一个剧情窗翻页**（实证 21401 由 TimerMgr 弹出、
        /// 21402 由玩家点「继续」经 `UIDramaDialogue:OnNextClick`→`NextDialogue` 出来），
        /// 页与页之间隔的是"玩家读完一句的时间"，不是帧。抓到第一句就收口 = 第二句永远拿不到，
        /// 而且凭证已吊销 → 第二页会重新长出那颗没用的「AI 对话」按钮（用户报的原始症状）。</summary>
        private const int SettleSeconds = 20;
        /// <summary>续凭证/续槽位的节流间隔（秒）—— 不必每帧写表。</summary>
        private const int RenewEverySeconds = 5;
        /// <summary>点到 `battleExit_*` 之后再宽限几秒收口（秒）。
        /// 为什么要宽限：那一击**同时**会关掉这一页并触发游戏的离场结算，而台账是在
        /// `ClickOption` 的 **prefix** 上收到通知的 —— 排在后面的结算若还弹一页（实测
        /// `20406`/`20413` 那类"我先走了"就是尾页本身，但顺序未逐支验过），立刻收口会把它切掉。</summary>
        private const float ExitGraceSeconds = 1.5f;

        /// <summary>「链还在收尾」占位的最长生命（秒）—— 钝兜底：窗一直不关也不会永久占住领票口。</summary>
        private const int ChainHoldSeconds = 90;
        /// <summary>收尾期间拦下新动作时给 LLM 看的那句话（`ToolExecutor` 原样返回给模型）。
        /// 必须与"已有一个行动在等待玩家响应"**分开** —— 两者的正确处置完全不同：
        /// 那句会让模型以为"玩家还没回应，等会儿重试"，而这里是"这场戏还没演完，别插手"。</summary>
        private const string ChainHoldReasonText =
            "战局刚了、剧情还没演完（窗还开着）—— 这一步先别动手，等它收场；要说的话可以写在台词里";

        /// <summary>判据 2（A/C 两支）：带 `battleExit_*` 的通用选项位。配表实锤见文件头。</summary>
        private static bool IsExitOption(int optionId) => optionId == 100004 || optionId == 100005;

        /// <summary>判据 1（B 支）：NPC 战败的处置页 —— 选项是**胜方（玩家）**手里的刀。
        /// `20403`=NPC 送东西求饶（5 个处置位）/ `20404`=NPC 任凭处置（4 个位）。</summary>
        private static bool IsVerdictDrama(int dramaId) => dramaId == 20403 || dramaId == 20404;

        private static int _slot = -1;
        private static string _op = "";
        private static string _npc = "";
        private static string _uid = "";
        /// <summary>本场是"你单独应战"还是"叫上玩家一起"（`solo` / `together`）。
        /// **必须随结果回给模型**：两个分支里模型的处境不同 —— `solo` 时他不是战斗的
        /// 任何一方（在场的是两个 NPC，玩家旁观），`together` 时玩家才是发起方。
        /// 不传的话模型只能从 `op=attack` 猜，猜出来的话术往往是"我向你发起了攻击"
        /// （那是"打玩家"分支的说法），与实际发生的事相反。</summary>
        private static string _mode = "";
        /// <summary>上一次 Tick 看到的区间行数 —— 把"链上出现了新内容"变成一条证据
        /// （等价于旧的 `NoteDramaLine → Evidence`，让静默窗跟着内容走）。</summary>
        private static int _linesSeen;

        private static float _armedAt;
        private static float _lastEvidenceAt;
        private static float _lastRenewAt;
        private static bool _battleSeen;
        private static bool _battleEnded;
        /// <summary>`battleEnded` 里"**游戏自己报的收尾**"那一路（`BattleEndMgr.BattleEnd` 钩子）。
        /// 与轮询翻假的区别有实证价值：真机那次切磋 `BattleEnd` 是玩家在**最后一页**点「继续」时
        /// 跑的（Player.log 5088-5096：`BattleEnd` ← `NextDialogue` ← `ClickOption`），
        /// 也就是"战后那几行已经翻完了"的硬证据 —— 见到它就不必再等静默。</summary>
        private static bool _battleEndedHard;
        private static bool _inBattleLast;
        /// <summary>攻击链专用：玩家点到了 `battleExit_*`（A/C 两支的终局），此刻的时刻见 `_exitAt`。</summary>
        private static bool _exitClicked;
        private static float _exitAt;
        /// <summary>收口发生在**处置页**（B 支）⇒ 生死未定，Python 要照这个写文案。</summary>
        private static bool _atVerdictPage;
        /// <summary>收口原因 = "对方没应战就退了"（反馈页 20102）。Python 据此写"没能打起来"。</summary>
        private static bool _noBattlePage;

        /// <summary>上一次收口的 op / 原因 / 时刻。**收口后保留（`Reset` 不清）**，只给探针读：
        /// B 支（玩家战胜）要求台账活到处置页 20403/20404，而唯一能提前掐死它的是一条不问时机的
        /// 硬信号（规则 2）。若实测硬信号更早，处置页就会被当成普通页处理、「verdict_page」永不置位 ——
        /// 那时**除了这一份留档，日志里看不出任何异常**（长得跟"玩家自己打的架"一模一样）。
        /// 见 `DramaSegmentProbe` 的处置页探针。</summary>
        private static string _lastResolveOp = "";
        private static string _lastResolveHow = "";
        private static float _lastResolveAt;

        /// <summary>给界面看的一句话（随心跳帧带出去）。</summary>
        private static string PhaseText
        {
            get
            {
                if (_op == "attack")
                {
                    if (_exitClicked) return "正在离开战斗…";
                    if (_inBattleLast) return "战斗进行中…";
                    return _battleEnded ? "打完了，等他开口…" : "等你应战后的战斗结果…";
                }
                return _battleEnded ? "切磋已结束，正在收尾" : (_inBattleLast ? "战斗进行中…" : "等你应战后的战斗结果…");
            }
        }

        /// <summary>是否在跟踪一场战斗的结果（`DramaAiOption` 用它决定要不要把台词喂进来）。</summary>
        public static bool Active { get { return _slot > 0; } }

        /// <summary>开始跟踪。`slot` = 已挂起的 DramaGate 槽位；`unitID` = 这场战斗的对手方 NPC
        /// （归因凭证的键，收口时由本类吊销）；`op` = "spar" / "attack"（决定用哪套收口判据）。</summary>
        public static void Arm(int slot, string npcName, string unitID, string op, string mode = "")
        {
            Reset();
            // 新一场战斗的台账开始 ⇒ 上一条链无论怎样都已经结束了：把可能还挂着的收尾占位撤掉，
            // 免得它挡住这一场之后要发的动作（占位只该在"链还开着"时存在）。
            try { DramaGate.ClearChainHold("新的战斗台账开始"); } catch { }
            if (slot <= 0) return;
            _slot = slot;
            _op = op ?? "";
            _mode = mode ?? "";
            _npc = npcName ?? "";
            _uid = unitID ?? "";
            _armedAt = _lastEvidenceAt = _lastRenewAt = Time.unscaledTime;
            EnsureTick();
            // 切到"跨战斗"口径：开始发心跳，过期交给滑动续期
            DramaGate.SetWaitMode(slot, SilenceSeconds, true, PhaseText);
            Log("开始跟踪一场战斗的结果 slot=" + slot + " op=" + _op + " npc=" + _npc);
        }

        /// <summary>
        /// 链上已抓到几**页**（只读不消费；**选择不算**）—— 09-23 起内容不再由本类收集
        /// （段是唯一内容流），收口判据里那句"已经抓到台词了"改问段的区间页数。
        ///
        /// 两个刻意的口径：
        ///   · **只数页**：玩家点了个选择只说明他答了话，不代表链上已经演到结局；
        ///   · 从**武装时刻**起算（不是凭证锚点）：与旧的自建台账同窗口 ——
        ///     锚点更早（凭证创建时刻）会把**战前**那页也算进来，让硬信号规则提前满足。
        ///     （内容读数用的是凭证锚点，那是**要**把战前那页带给模型；两个口径各司其职。）
        /// </summary>
        private static int Lines()
        {
            if (_slot <= 0 || string.IsNullOrEmpty(_uid)) return 0;
            try { return DramaSegmentRecorder.CountPages(_uid, _armedAt, Time.unscaledTime); }
            catch { return 0; }
        }

        /// <summary>战斗结束信号。`hard=true` 只给 `BattleEndMgr.BattleEnd` 钩子（游戏自己的收口，
        /// 实证发生在战后窗翻完之后）；轮询那条（战斗位翻假）传 false —— 它可能落在战后窗**之前**
        /// （`NpcInitiativeMonitor` 的旧注释就写着"结算期 isBattle 可能已落"）。</summary>
        public static void NoteBattleEnded(string how, bool hard = false)
        {
            if (_slot <= 0) return;
            if (_battleEnded && !hard) return;   // 已有更硬（或同级）的结束信号，弱的不再覆盖
            _battleEnded = true;
            _battleEndedHard = hard;
            Evidence("战斗结束（" + how + "）");
        }

        /// <summary>玩家在链里点了某个选项（`ClickOption` 的 **prefix** 上报）。
        /// 两个用途：① 命中 `battleExit_*` = A/C 两支的终局；② 任何一次点击都是"链在推进"的证据，
        /// 并把总上限的起点挪到这一刻（攻击链的时长由玩家的手速决定，600s 从武装算起会掐到慢的人）。</summary>
        public static void NoteOptionClicked(int optionId, string dramaTag)
        {
            if (_slot <= 0) return;
            if (_op == "attack" && _armedAt > 0f) _armedAt = Time.unscaledTime;
            if (!IsExitOption(optionId))
            {
                Evidence("链里点了选项 id=" + optionId + "（" + (dramaTag ?? "") + "）");
                return;
            }
            if (!_exitClicked)
            {
                _exitClicked = true;
                _exitAt = Time.unscaledTime;
            }
            Evidence("点到离场选项 id=" + optionId + "（battleExit，" + (dramaTag ?? "") + "）");
        }

        /// <summary>这一页是不是 B 支的处置页（`DramaAiOption` 在认领分支里问一次）。
        /// 只有攻击的台账在飞时才算数 —— 切磋永远不会命中。</summary>
        public static bool IsVerdictPage(int dramaId)
        {
            return _slot > 0 && _op == "attack" && IsVerdictDrama(dramaId);
        }

        /// <summary>页号是不是 B 支的处置页（**只看页号、不看台账**）。给探针用：
        /// 判据本体仍是 `IsVerdictDrama`，这里只是把页号白名单开一个只读口，避免探针里再抄一份数字。</summary>
        public static bool IsVerdictDramaId(int dramaId) { return IsVerdictDrama(dramaId); }

        /// <summary>上一次收口的人话摘要（本局还没收过口则返回空串）。给探针对账用。</summary>
        public static string LastResolveText()
        {
            if (string.IsNullOrEmpty(_lastResolveHow)) return "";
            return "op=" + _lastResolveOp + " 原因=" + _lastResolveHow
                   + " 距今=" + (Time.unscaledTime - _lastResolveAt).ToString("0.0") + "s";
        }

        /// <summary>
        /// 这一页是不是**"没打起来"的反馈页**（10-28 真机：姜萌逃跑那场）。
        ///
        /// ## 为什么需要它
        /// `player.CreateAction(new UnitActionRoleAttack(B))` 返回 **0 只表示"动作被创建"**，
        /// 不代表战斗会打起来。引擎可以在 `OnCreate` 里直接判"对方逃了"：
        ///
        /// ```text
        ///   被他人发起战斗时的反馈
        ///   战力评分：-15934     ← 实力差距
        ///   仇恨度评分：-522     ← 仇恨太低
        ///   UnitActionFeedback101:OnCreate()
        ///   逃跑成功率：34.78261
        ///   → 弹剧情页 20102（只有一个「确定」）
        /// ```
        ///
        /// 这场**从没进过战斗**：`battle_seen` 永远为假、`BattleEndMgr.BattleEnd` 不会来、
        /// 战斗位从不翻真。旧实现只能等**静默 60 秒**兜底 —— 真机实测卡了 67 秒才回工具结果。
        ///
        /// ## 判据
        /// 页号 `20102`（配表 `DramaDialogue`，实测该页 `options="100001"` 单选项）。
        /// **只用页号，不匹配台词** —— 台词是语句池，措辞随人设/关系变（本仓铁律）。
        /// 只在 `attack` 台账在飞、且**还没观察到战斗**时才算数：
        /// 已经打起来的链里出现同号页不能当"没打"。
        /// </summary>
        public static bool IsNoBattleFeedbackPage(int dramaId)
        {
            return _slot > 0 && _op == "attack" && !_battleSeen && dramaId == NoBattleFeedbackDramaId;
        }

        /// <summary>`20102` = 「被他人发起战斗时的反馈」（逃跑判定）。配表事实，勿改。</summary>
        public const int NoBattleFeedbackDramaId = 20102;

        /// <summary>反馈页当场收口：这一场**没能打起来**，如实报，不再等静默兜底。
        /// 返回 false = 槽位已不在（调用方照常走注入，别卡住）。</summary>
        public static bool ResolveAtNoBattlePage(string how)
        {
            if (_slot <= 0) return false;
            _noBattlePage = true;
            Resolve(how);
            return true;
        }

        /// <summary>处置页当场收口（`DramaAiOption` 抓到该页台词之后调）。
        /// 顺序是刻意的：**先收口再让出注入路径** —— `InitData` 一页只跑一次，
        /// 错过这一次就没机会给这页补按钮。返回 false = 槽位已不在（调用方照常走注入，别卡住）。</summary>
        public static bool ResolveAtVerdictPage(string how)
        {
            if (_slot <= 0) return false;
            _atVerdictPage = true;
            Resolve(how);
            return true;
        }

        /// <summary>世界重载/读档：那场战斗已随旧世界消失，结局不会再来了 —— 按**已抓到的部分**
        /// 立刻收口（不静默丢弃，也不留槽位占住所有动作工具的 busy 键）。
        /// 其余非战斗链的长等槽位由 `DramaGate.ForceResolveAll` 一起收。
        /// ★ 玩家被斩杀那一支很可能就是读档/回主菜单 ⇒ 走的就是这条路，而它**已经抓到了求饶句**
        /// ⇒ 必须仍按 success + 台词收口，不能退成"没等到结局"。</summary>
        public static void ForceClose(string reason)
        {
            // 换世界/读档：链随旧世界消失，收尾占位也一并撤（放在 `_slot <= 0` 早退**之前** ——
            // 处置页收口之后槽位已经是 -1，只清槽位的话占位会留到新世界里继续挡动作）。
            try { DramaGate.ClearChainHold(reason); } catch { }
            if (_slot <= 0) return;
            int have = Lines();
            if (have > 0) { Resolve(reason + "（按段里已有的 " + have + " 行收口）"); return; }
            Resolve(reason + "：这场战斗不会再有结果");
        }

        /// <summary>动作压根没发起（`CreateAction` 抛异常）时静默撤销：不发结果、不吊销后
        /// 再补发。调用方紧接着要 `DramaGate.Cancel(slot)`（此时 WsClient 还没登记 req_id，
        /// 发 Resolve 只会撞进"无主槽位"）。与 `ForceClose` 的区别就是**不发**。</summary>
        public static void Abort()
        {
            if (_slot <= 0) return;
            ReleaseClaim();
            Reset();
        }

        // ------------------------------------------------------------------

        private static void Tick()
        {
            try
            {
                // 收尾占位**必须跑在 `_slot <= 0` 早退之前**：它生效的时段恰恰是"结果已收、槽位已释放"
                // 的那段（见 `Resolve` 里置位的那段注释）。
                ChainHoldTick();
                if (_slot <= 0) return;

                // ⓪ 链上出现新内容（页/选择）= 一条证据（滑动静默窗）—— 09-23 起内容在段里，
                //    "抓到了台词"这个信号改由区间行数增长来表达（等价于旧的 NoteDramaLine → Evidence）。
                {
                    int lc = Lines();
                    if (lc != _linesSeen) { _linesSeen = lc; Evidence(null); }
                }

                // ① "还在打仗"本身就是证据 —— 两个信号任一为真即算，理由见 NpcInitiativeMonitor.BattleActive
                bool inBattle = false;
                try { inBattle = NpcInitiativeMonitor.BattleActive(); } catch { }
                if (inBattle)
                {
                    if (!_inBattleLast) { _inBattleLast = true; _battleSeen = true; Evidence("进入战斗"); }
                    else Evidence(null);          // 续期，不打日志
                }
                else if (_inBattleLast)
                {
                    _inBattleLast = false;
                    NoteBattleEnded("战斗位由真翻假");
                }

                // ② 攻击专有：场上有剧情窗开着 = 玩家正在读，链没断 ⇒ 只续期，绝不收口。
                //    刻意不喂给切磋 —— 上面那三条静默规则是逐字保留的实证结果，不动它。
                //    判据只许用共用那一份（`DramaWindowName`），见 tests/test_drama_gate_contract.py。
                if (_op == "attack")
                {
                    try { if (NpcInitiativeMonitor.IsDramaWindowShowing()) Evidence(null); } catch { }

                    // ②b 预告窗（见文件末 `BattlePreOpen` 那段）：开着 = 等玩家点应战，也是证据
                    bool pre = BattlePreOpen();
                    if (pre)
                    {
                        if (!_preOpenLast) { _preOpenLast = true; _preSeen = true; Evidence("弹出进战斗的预告窗（等你点应战）"); }
                        else Evidence(null);
                    }
                    else if (_preOpenLast)
                    {
                        _preOpenLast = false;
                        _preClosedAt = Time.unscaledTime;
                        Evidence("进战斗的预告窗关了");
                    }

                    // ②c ★D 支这一版**只记日志、不收口**★。拿"预告窗关了 + 战斗位没翻真"当
                    //   "玩家没应战"的判据，前提是那两个战斗位认这场攻击 —— 而它们对攻击**一个样本
                    //   都还没有**（切磋也只有两场）。押上去就会在"战斗位不认这场架"时把一场正在
                    //   打的战斗误报成"没应战"，那正是 `unitLeft/unitRight` 那次"错数据源自信地给
                    //   反事实"的形状。先攒日志，样本够了再谈要不要升成判据。
                    if (_preSeen && !_preOpenLast && !_battleSeen && !_preNoBattleLogged &&
                        Time.unscaledTime - _preClosedAt >= NoBattleAfterPreSeconds)
                    {
                        _preNoBattleLogged = true;
                        Log("预告窗关了 " + (int)(Time.unscaledTime - _preClosedAt) +
                            "s 而战斗位未翻真（可能没应战，也可能这两个位不认攻击）→ 只记录，不收口");
                    }
                }

                // ③ 续期（节流）：槽位滑动过期 + 归因凭证 TTL（ClaimTTL=120s，一场战斗很容易超过它）
                if (Time.unscaledTime - _lastRenewAt >= RenewEverySeconds)
                {
                    _lastRenewAt = Time.unscaledTime;
                    if (!DramaGate.Renew(_slot, SilenceSeconds))
                    {
                        Log("槽位已不在（被超时或被别处收口）→ 停止跟踪 slot=" + _slot);
                        ReleaseClaim();
                        Reset();
                        return;
                    }
                    DramaGate.SetWaitMode(_slot, SilenceSeconds, true, PhaseText);
                    if (!string.IsNullOrEmpty(_uid))
                    {
                        DramaNativeClaim.ClaimFor(_uid);
                        // ★链心跳（09-23 用户定）★：这一跳告诉分段器"这场戏还没演完" —— 段在挂起期
                        //   不被固定预算掐断（一场几分钟的战斗不会把战前那句切到上一段去）。
                        //   心跳只在台账活着的这段时间有：收口即 `ReleaseChain`（见 `ReleaseClaim`）。
                        DramaSegmentRecorder.RenewChain(_uid);
                    }
                }

                // ④ 收口判定（顺序即优先级；`idle` = 距最后一个证据多久）。
                //    `have` = 段里这场链已有多少行（页 + 选择）—— 09-23 起内容不再由本类收集，
                //    "抓到台词"改问段的区间（只读不消费；真正的"读走即消费"发生在 `Resolve`）。
                float idle = Time.unscaledTime - _lastEvidenceAt;
                int have = Lines();
                // ① 先看**游戏自己报的收尾**（硬信号 + 已抓到台词）：它天然排在战后那几页之后
                //    （实证链见 `_battleEndedHard`），是"链演完了"最可靠的证据。
                //    ★09-22 实测把这条提到最前：逃跑那支"点到离场选项"与 `BattleEnd` **同毫秒**，
                //      旧顺序下规则 1 那 1.5s 宽限还没到点就被规则 2 收掉（早 1.48s）——
                //      也就是说规则 1 在实战里**拿不到先手**，留着它挡在前面只会推迟收口。
                if (_battleEndedHard && have > 0)
                { Resolve("游戏报了战斗结束，链上已有内容 " + have + " 行"); return; }
                // ② 攻击 A/C 两支的兜底：点到 `battleExit` 选项 + 宽限到点 ⇒ 链走完了。
                //    只在 `BattleEnd` **不来**的分支才有戏（实证：它要来就与离场点击同毫秒）。
                //    宽限的意义：那一击会同时关页并触发离场结算，若后面还弹一页，别把它切掉。
                if (_exitClicked && Time.unscaledTime - _exitAt >= ExitGraceSeconds)
                { Resolve("玩家点了离场选项，宽限 " + (int)ExitGraceSeconds + "s 到点（链上 " + have + " 行）"); return; }
                // ③ 只有轮询信号：战后窗是**玩家手动翻页**的，静默够久才敢断定他不再往下翻了
                if (_battleEnded && idle >= (have > 0 ? SettleSeconds : AfterBattleEndGrace))
                { Resolve("战斗结束且静默 " + (int)idle + "s（链上 " + have + " 行）"); return; }
                if (idle >= SilenceSeconds) { Resolve("静默满 " + SilenceSeconds + "s 无结局证据"); return; }
                if (Time.unscaledTime - _armedAt >= HardCeilingSeconds) { Resolve("超过总上限 " + HardCeilingSeconds + "s"); return; }
            }
            catch (Exception e) { try { Log("Tick 异常: " + e.Message); } catch { } }
        }

        private static void Resolve(string how)
        {
            int slot = _slot;
            // ★`_mode` 必须在这里取出来★（10-28 真机 bug）
            //   下面 `Reset()` 会把 `_mode` 清空，而 data 是在 Reset **之后**才组装的 ——
            //   原来直接读 `_mode`，于是永远拿到空串，`mode` 从来没到过模型手上。
            //   失败形态：`together` 的战报退回"打玩家"那条话术（"你向<玩家名>动了手"）。
            string op = _op, npc = _npc, mode = _mode;

            // ★工具结果 = 读段的**区间**★（09-23：本类不再自己收内容）—— 页 + 选择按时序，
            //   读走即标消费（之后玩家点「对话」不会把这批内容再投一遍）。
            //   读在 `Reset()` 之前（锚点与目标 uid 都还在）；处置页的收口要求调用方**先记页**
            //   （见 `DramaAiOption` 认领分支的顺序注释）。
            List<SegmentLine> range = null;
            bool hasRange = false;
            try
            {
                // 锚点 = 凭证首次登记时刻（早于 CreateAction ⇒ 战前的 21204 提问页与「接受切磋」
                // 都在区间里）；凭证没了回落本类武装时刻。
                hasRange = DramaSegmentRecorder.TryReadRangeFromChain(_uid, _armedAt, true, out range);
            }
            catch { }
            JArray lines = DramaSegmentRecorder.LinesToJson(hasRange ? range : null, PlayerName());
            bool ended = _battleEnded;
            bool seen = _battleSeen;
            bool verdict = _atVerdictPage;
            bool noBattle = _noBattlePage;   // 同样要在 Reset 之前取（Reset 会清）

            // 留档：给处置页探针对账"台账是什么时候、死于哪条判据的"（Reset 不清这三个）
            _lastResolveOp = op;
            _lastResolveHow = how;
            _lastResolveAt = Time.unscaledTime;

            ReleaseClaim();
            Reset();          // 先清台账：Resolve 会同步回调进 WsClient 补发 response

            var data = new JObject
            {
                ["op"] = op,
                // `solo` / `together`：模型的处境（见 `_mode` 字段注释）。空串 = 调用方没给
                // （旧调用点），此时不写该键 —— 字段缺失 ≠ 猜一个。
                ["target"] = npc,
                ["player"] = PlayerName(),   // 玩家显示名（Python 认不出玩家名，见 room_flow.cs:400
                                             // 那条边界交给 C# 的约定）→ 群聊公共行要写"与某某切磋"
                ["battle_seen"] = seen,      // 观察到过战斗（false = 两个战斗位都不认这场战斗）
                ["battle_finished"] = ended,
                ["crossed_battle"] = seen || ended || lines.Count > 0,  // 真的动过手 → WsClient 据此不抢着自动开窗
                // 段区间的**单流**（页 + 选择按时序；`kind` 区分）—— 原样带归属，不解析胜负。
                // 处置页收口时（`verdict`）这里读不到还没发生的「击杀/放过」——那一下留给段（点对话可见）。
                ["lines"] = lines,
            };
            if (mode.Length > 0) data["mode"] = mode;
            if (op == "spar") data["accepted"] = true;      // 玩家确实应战了（婉拒路径不进本类）
            if (op == "spar") data["answered"] = true;
            // B 支：收在处置页 ⇒ 生死由玩家手里那一下决定，**还没发生**。Python 据此写文案。
            if (verdict) data["verdict_page"] = true;
            // ★"没打起来"要明说★（10-28）：`battle_seen=false` 只说明"没看到战斗"，
            // 但模型分不清是"对方逃了"、"还没打"、还是"我们在等"。
            // 反馈页 20102 是**确凿证据**：引擎已经走完逃跑判定，这场不会打了。
            if (noBattle) data["no_battle"] = true;
            Log("收口：" + op + " 结果已定（" + how + "）区间=" + lines.Count + " 行 slot=" + slot +
                (verdict ? " 处置页收口（生死未定）" : ""));
            DramaGate.Resolve(slot, new JObject { ["success"] = true, ["data"] = data });

            // ★「链还在收尾」占位（09-22 实测）：收口只代表**结果已定**，不代表链演完了 ——
            //   处置页之后尾页 10 秒才来（收口 22:53:14.833 → 尾页 22:53:24.474 → 关窗 22:53:26.019），
            //   那段时间 busy 键已释放、而 `isBattle` 仍为真（游戏自己在结算）。
            //   判据刻意**不写"处置页"**，而是"收口这一刻剧情窗还开着"：处置页必然开着（正是它触发的收口），
            //   而逃跑/战败那两支的离场点击与关窗同毫秒（实测），天然不吃这一条。
            //   期间：工具结果照发（模型能说话），**新动作一律被 `DramaGate.TryDefer` 挡住**。
            bool wOpen = false;
            try { wOpen = NpcInitiativeMonitor.IsDramaWindowShowing(); } catch { }
            if (wOpen) DramaGate.SetChainHold(ChainHoldReasonText, ChainHoldSeconds);
        }

        /// <summary>两个"链还活着"的信号**同时**静默之后，再连续安静这么久才撤占位（秒）。
        /// 为什么不是"剧情窗一不可见就撤"（09-22 复核）：`IsDramaWindowShowing` 判的是 **UI 层层顶**，
        /// 而玩家在处置页点「对话」把我方面板盖上去的那一刻，层顶就不再是剧情窗 ——
        /// 那时链的尾页可能还没来（实测收口 → 尾页 10s → 关窗），单看窗 = 在尾页演出期间把动作放行，
        /// 正是占位要防的"与链并行"。加窄的条件是**战斗位也落了**：实测 B1（`isBattle`）一直撑到
        /// 关窗那一刻（22:53:26），所以"盖顶"时它仍为真、不会误撤；只有真关窗（或游戏自己结束战斗）
        /// 才会两样同时成立。再叠一个 1.5s 静默，吃掉 UI 重建/销毁中间帧的抖动。</summary>
        private const float ChainHoldQuietSeconds = 1.5f;

        /// <summary>两个"链还活着"的信号同时静默的起点（-1 = 还没开始计时）。</summary>
        private static float _holdQuietSince = -1f;

        /// <summary>撤占位的判据：剧情窗不可见 **且** 战斗位已落，并连续 <see cref="ChainHoldQuietSeconds"/> 秒。
        /// 两个信号刻意**取与**（不是"窗不可见即撤"）——理由见上面那条常量。
        /// 另一条铁律：**取不到（异常）一律当"信号还在"**，绝不把"查不到"当"结束了" ——
        /// 那会在钩子失效/UI 未就绪时提前放行（同 `BattlePreShowing` 的"查不到 ≠ 关着"）。
        /// 超时那条由 `DramaGate.Tick` 兜（90s）；换世界/新台账另有兜底撤。
        /// 为什么必须放行而不是一直占着：玩家在处置页上就能点「对话」开始聊，聊着聊着链早演完了，
        /// 那时他若想让 NPC 递个东西，不该再被一条早已过期的占位挡住。</summary>
        private static void ChainHoldTick()
        {
            if (!DramaGate.ChainHold) { _holdQuietSince = -1f; return; }
            bool w = true, fighting = true;   // 默认"还在"：异常/取不到时保守不撤
            try { w = NpcInitiativeMonitor.IsDramaWindowShowing(); } catch { }
            try { fighting = NpcInitiativeMonitor.BattleActive(); } catch { }
            if (w || fighting) { _holdQuietSince = -1f; return; }
            if (_holdQuietSince < 0f) { _holdQuietSince = Time.unscaledTime; return; }
            if (Time.unscaledTime - _holdQuietSince < ChainHoldQuietSeconds) return;
            _holdQuietSince = -1f;
            DramaGate.ClearChainHold("剧情窗已关且战斗位已落（持续 " + ChainHoldQuietSeconds + "s）");
        }

        private static void Evidence(string what)
        {
            _lastEvidenceAt = Time.unscaledTime;
            if (what != null) Log(what);
        }

        // ---- 进战斗前那扇预告窗（**不是剧情窗**，两个 Harmony 挂点都看不见它）----
        //
        // 真机栈帧实锤它是"进战斗的那一下"：Player.log 1556-1560
        //   `WorldBattleMgr:IntoBattle` ← `<>c:<IntoBattle>b__1()` ← `UIMapBattlePre:<UpdateUI>b__10_3()`
        // 攻击链上没有任何"我方认得的点击时机"早于战斗，所以这扇窗的开/关只能**按名轮询**观测。
        // 两个用途：① 探针 —— 日志能看出"玩家到底点没点应战"；② 开着 = 等玩家点，本身就是证据。
        // ★ D 支（没应战）这一版**只记日志不收口**，理由写在 Tick 的 ②c 那段里。
        // ★★ 09-22 实测改正：节点真名是 **`MapBattlePre`**（无 `UI` 前缀），判据改用共用那一份
        //    `NpcInitiativeMonitor.BattlePreShowing()`（层顶名字族，与 `DramaWindowName` 同形）。
        //    旧实现按**类名** `UIMapBattlePre` + `GetUI(...)` 查，恒 false 且不报错（详见那边的注释）。
        // ★ 查不到时恒 false ⇒ `_preSeen` 立不起来 ⇒ ②自动失效，退化成原来的静默口径。
        //   **绝不把"查不到"当成"关着"** —— 那会当场把整条链掐掉。
        /// <summary>预告窗关了之后，再等几秒才敢断定战斗没起来（`IntoBattle` 是同步的，3s 纯余量）。
        /// ⚠ 这一版**只记日志不收口**，所以这个数字**不需要精确**（别把它当判据的阈值看）。</summary>
        private const float NoBattleAfterPreSeconds = 3f;

        private static bool _preSeen;
        private static bool _preOpenLast;
        private static bool _preNoBattleLogged;
        private static float _preClosedAt;

        /// <summary>预告窗是否可见（**唯一实现**在 `NpcInitiativeMonitor`，与定标探针共用同一份）。</summary>
        private static bool BattlePreOpen()
        {
            try { return NpcInitiativeMonitor.BattlePreShowing(); } catch { return false; }
        }

        private static void ReleaseClaim()
        {
            if (string.IsNullOrEmpty(_uid)) return;
            try { DramaNativeClaim.Release(_uid); } catch { }
            // 链到此为止 ⇒ 心跳也停（段随后按现行规则收口；处置页之后来的尾页/「击杀·放过」
            // 选择留在段里，玩家点「对话」即得）。
            try { DramaSegmentRecorder.ReleaseChain(_uid); } catch { }
            // ★链已定案（收口）或已失联：把"同一场剧情后续翻页"的**会话**也放行。
            //   不放行的话，收口之后同窗翻出来的页会被会话认领，而那时台账已不在 ⇒
            //   不注入按钮（玩家聊不到）、不进 tool result（模型没看过）、段里还标"照记不投"（投不出）
            //   ⇒ 玩家正盯着的那句话三处皆无。见 `DramaNativeClaim.ReleaseSession` 头注释。
            //   （marry/结拜那类不走战斗台账，它们的会话照旧护着第二层翻页。）
            try { DramaNativeClaim.ReleaseSession(_uid); } catch { }
        }

        private static string PlayerName()
        {
            try
            {
                var p = g.world != null ? g.world.playerUnit : null;
                if (p == null) return "玩家";
                string n = p.data.unitData.propertyData.GetName();
                return string.IsNullOrEmpty(n) ? "玩家" : n;
            }
            catch { return "玩家"; }
        }

        private static void Reset()
        {
            _slot = -1;
            _op = "";
            _mode = "";
            _npc = "";
            _uid = "";
            _linesSeen = 0;
            _battleSeen = false;
            _battleEnded = false;
            _battleEndedHard = false;
            _inBattleLast = false;
            _exitClicked = false;
            _atVerdictPage = false;
            _noBattlePage = false;
            _holdQuietSince = -1f;   // 新一轮收口/新台账：占位静默计时重来（`ChainHoldTick` 起算）
            _preSeen = false;
            _preOpenLast = false;
            _preNoBattleLogged = false;
            _preClosedAt = 0f;
        }

        /// <summary>确保常驻帧回调在跑（11-19：句柄交给 `FrameTicker`）。
        /// 每次登记一个新凭证时都走到这里 ⇒ 世界重载清掉帧登记之后能自动救活。</summary>
        private static void EnsureTick()
        {
            FrameTicker.Ensure(FrameTicker.KBattleOutcome, "battle.tick", () =>
            {
                // 耗时采样（TickProfiler，09-28 卡顿定位）
                long __t = TickProfiler.Begin();
                try { Tick(); }
                finally { TickProfiler.End("battle.tick", __t); }
            });
        }

        /// <summary>日志自带**墙钟**：`Player.log` 没有时间戳，而"处置页出现 vs 台账收口"这类
        /// 顺序问题要跟 `[SegProbe]`（自带墙钟）逐毫秒对账 —— 只有文件行序是不够的，
        /// 两处日志各自缓冲，插队一行就再也分不清谁先。</summary>
        private static void Log(string s)
        {
            try { ModMain.P("[BattleOutcome] " + DateTime.Now.ToString("HH:mm:ss.fff") + " " + s); } catch { }
        }
    }
}
