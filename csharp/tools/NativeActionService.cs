/// <summary>
/// NativeActionService —— 原生动作的下发与结局归属：`StartToUnitAiAction*` / 排队 / pending 标记 / 拒绝判据。
/// （11-19 由 6711 行的 `ToolExecutor.cs` 拆出，11-20 按 Python 的域归并；成员逐字搬移。）
/// 跨类调用一律 `<类名>.<方法>`；`internal` = 仅本程序集可见。
/// </summary>
using System;
using System.Collections.Generic;
using Newtonsoft.Json.Linq;
using static AgentLoopBridge.ToolResult;

namespace AgentLoopBridge
{
    internal static class NativeActionService
    {
        // ---------- 辅助：真实校验 ----------

        /// <summary>
        /// 通用：AI 载体挂到 npc，执行"面向玩家"的 WorldUnitAIAction（toUnit 已由调用方在实例上设好）。
        /// 模式来自神识传音 IL 实证：ai.Init(actor) → action.Init(ai, 1, null) → ActionStart(onEndCall)。
        /// ActionStart 回调为 Il2CppSystem.Action<bool>（bool=结算结果）；当前传 null（结算捕获/推
        /// npc_initiative 事件为后续增强）；真机须核验游戏对 null 回调的容忍度，必要时补委托互操作。
        /// </summary>
        internal static void StartToUnitAiAction(WorldUnitAIActionBase action, WorldUnitBase npc)
        {
            var ai = new WorldUnitAIBase();
            ai.Init(npc);
            action.Init(ai, 1, null);
            action.ActionStart(NewAiActionCallback());
        }

        /// <summary>
        /// AI 动作「完成挂起」启动（论道/双修专用）：动作发出后工具不立即给结果，而是返回
        /// DramaGate pending 标记 → WsClient 憋住 response → LLM 回合挂起 → 玩家在原生界面
        /// （论道/双修，30s~1min）操作完 → 引擎回调 ActionStart(bool) → Resolve(slot, 真实结果)
        /// → 补发 response → LLM 才按真实结局说话。与 StartRelationAction 的确认窗同一条延迟链。
        ///
        /// 依据：神识传音 case17/22 对 1031/1037 同款挂 Action<bool>，p==true 即"完成"。
        /// 真机观察点：论道完成后 Player.log 应出现 [AiAction] 日志；若始终不出现说明 1037
        /// 完成时不回调（与 "1037 容忍 null"的旧观察一致），届时需改轮询动作状态。
        /// 超时：DramaGate 90s 自动 Resolve"玩家长时间未响应"（与确认窗一致；玩家更慢时
        /// Python 侧 request_timeout 先到，LLM 收到超时错误，迟到补发被幂等丢弃）。
        /// </summary>
        /// <param name="doneNote">完成时给 LLM 的 note（工具结果语义，NPC 视角描述世界）</param>
        internal static JObject StartToUnitAiActionDeferred(WorldUnitAIActionBase action, WorldUnitBase npc, string op, string npcName)
        {
            if (ShowDramaService.AutoConfirm)
            {
                // verify 直通：无玩家交互，立即启动并返回真实结果形态（pending 无人应答会被判 FAIL）
                StartToUnitAiAction(action, npc);
                return Ok(new JObject { ["op"] = op, ["target"] = npcName, ["completed"] = true });
            }
            if (!DramaGate.TryDefer(ModIds.AiActionGate, out int slot))
                return Fail(DramaGate.RejectText("已有一个行动在等待玩家完成（论道/双修界面未关闭），请稍后再试"));
            // ★闸 1.5 归因凭证（09-23 修）★：本方法原先**没发券** —— 论道界面是 `Execute` 返回
            //   之后由 AI 动作异步弹出的，既躲过闸 1（只在 Execute 执行期亮），又没券可认
            //   ⇒ `DramaAiOption` 每页都照常注入「AI 对话」按钮（用户报的"工具调用论道怎么也
            //   显示了对话按钮"）。与 `DeferNativeUnitAction`（yao_yue 那条）对齐：
            //   发起前发券 → 挂起成功绑槽（完成回调 / 120s 超时 Resolve 时自动吊销）→ 启动失败收回。
            string claimUid = "";
            try { claimUid = DramaNativeClaim.UidOf(npc); } catch { }
            // 新链：锚点（=凭证首次登记时刻，工具结果的区间读数起点）重置到此刻
            DramaNativeClaim.ClaimFor(claimUid, op, true);
            try
            {
                var ai = new WorldUnitAIBase();
                ai.Init(npc);
                action.Init(ai, 1, null);
                // ★★ 10-17 更正：**绝不**在 `ActionStart` 之前调 `CreateActionInit()` ★★
                //
                //   09-29 曾在这里插了一句 `created = action.CreateActionInit();`，本意是补
                //   "AI 动作族没有裁决码"的洞（`ActionStart` 是 void、`WorldUnitAITool.CreateAction`
                //   也是 void，这条链确实**没有任何返回码**）。
                //
                //   **但那个位置是错的，它让论道 100% 失败**（10-17 真机实锤）：
                //     `[AiAction] lun_dao CreateActionInit 抛异常: NullReferenceException`
                //     `tool_result: {"success": false, "error": "lun_dao 启动失败: NullReferenceException"}`
                //   两次重试同一 NRE（模型被规则允许重试一次，`slot=1`/`slot=2` 都栽在这里）。
                //
                //   机理：`ActionStart` **内部自己会创建动作**（对比旧代码快照
                //   `reference/decomp_tmp/built_types.txt:1456` 的可用形态 —— 只有
                //   `Init(npc)` + `action.Init(ai,1,null)` + `ActionStart(cb)`，**没有**这一句）。
                //   在它之前先创建一次 ⇒ 同一个 `WorldUnitAIAction1037` 实例被创建两遍，
                //   第二遍（`ActionStart` 内部那次）撞上空引用。
                //   换言之：**那个探针不是"读到拒绝码"，而是"把动作对象弄坏"**。
                //
                //   ⇒ 恢复 09-13 已验证可跑通的形态（只 `ActionStart`）。
                //   ⇒ "引擎接没接"改用**启动前自证**回答（精力/成本读数，见下面的探针与
                //     `NativeRefuseWhy`）—— 那条路不需要引擎给码。
                // ★锚点必须早于动作启动（09-23）★：`ActionStart` 会同步弹首窗（论道开窗页，
                //   如 22821「一同论道」）—— Arm 放在它之后就漏第一页（该页进不了工具结果）。
                //   结果由完成回调经 `FillResult` 读段的区间（页 + 选择，按时序）。
                TalkOutcome.Arm(slot, npcName, op, claimUid);
                // 好感快照（10-12）：在 ActionStart **之前**取，才能覆盖整场交互的原生 Δ。
                //   · 论道：游戏配表里**没有** lun_dao 键（ConfRole* 全枚举实证）⇒ 由我们 top-up（+10 双方）；
                //   · 双修：ConfRoleTrains.closeAddMin/Max 是原生的 ⇒ 只观察、不落刀（探针见回调）。
                int intimNpcBefore = IntimService.IntimBetween(npc, g.world.playerUnit);
                int intimPlayerBefore = IntimService.IntimBetween(g.world.playerUnit, npc);
                // ★★ 10-21 定案②：`ActionStart` 会**同步**回调 —— 这条必须和异步收口分开处理 ★★
                //
                //   真机 10-21（Player.log，20:11/20:13 两次）：
                //     [TalkOutcome] 开始跟踪 slot=2 ...
                //     [AiAction] lun_dao 完成回调 ok=False slot=2     ← 紧挨着
                //     [TalkOutcome] 槽位结束（op=lun_dao）→ 清状态
                //     [AiAction] lun_dao 已挂起（slot=2 ...）          ← 反而排在最后
                //   「已挂起」排在「完成回调」**后面** ⇒ 回调是在 `ActionStart` 里同步跑完的。
                //
                //   机理（`WorldUnitAIAction1037::ActionStart` @0x45E2210 反汇编）：
                //     al = WorldUnitAITool::MovePoint(npc, toUnit);
                //     if (!al) { onEndCall(false); return; }   // ← 同步，且**根本没弹窗**
                //     ... 绑 onEndCall、开论道 ...
                //
                //   旧实现在这条同步路径上直接 `DramaGate.Resolve(slot, result)`，而此刻
                //   `WsClient` **还没登记** `_deferred[req_id] = slot`（登记发生在 ExecuteRequest
                //   返回之后）⇒ `OnDramaResolved` 反查不到 req_id，**结果被静静丢掉**；
                //   更糟的是 Resolve 已把 slot 从 `_pending` 摘走，过期 Tick 也不会再管它。
                //   于是那一帧 response 永远不发 → Python 干等满 120s 判「请求超时」。
                //   **这就是"论道静默"的真因**，与"对方拒绝"无关。
                //
                //   修法：同步回调不走挂起通道 —— `Cancel` 收回票据、撤锚点，把失败**直接**
                //   作为工具结果 return 给模型（毫秒级可见，没有 120s 空等）。
                bool startReturned = false;   // `ActionStart` 已经返回了吗 → 用来区分同步/异步回调
                // 同步回调的结论（见下面 `if (!startReturned)` 那段）：非 null 就代表
                // "引擎当场没接"，由 `ActionStart` 返回后的代码直接 return 给模型。
                JObject syncResult = null;
                System.Action<bool> managed = ok =>
                {
                    // 论道/双修这类「挂起」动作的完成回调：`ok` 是引擎给的最终结果。
                    //   若为 false，原因与文案见下面 else 分支的定案注释。
                    JObject result;
                    if (ok)
                    {
                        var data = new JObject { ["op"] = op, ["target"] = npcName, ["completed"] = true };
                        // 好感结算（10-15 口径：**只传请求值、实得由引擎按 RoleClose 换算**；须在 Resolve
                        // 之前落定，理由同下面的 FillResult）
                        if (op == "lun_dao")
                        {
                            try
                            {
                                IntimService.ApplyLunDaoIntim(npc, out int dNpcToPlayer, out int dPlayerToNpc);
                                var intimObj = new JObject();
                                if (dNpcToPlayer != int.MinValue) intimObj["npc_to_player"] = dNpcToPlayer;
                                if (dPlayerToNpc != int.MinValue) intimObj["player_to_npc"] = dPlayerToNpc;
                                data["intim"] = intimObj;
                            }
                            catch { }
                        }
                        else if (op == "shuang_xiu")
                        {
                            // 探针：双修好感由原生配表自算，我们只记录实得（真机核对用，不落刀）
                            try
                            {
                                int d1 = intimNpcBefore >= 0 ? IntimService.IntimBetween(npc, g.world.playerUnit) - intimNpcBefore : int.MinValue;
                                int d2 = intimPlayerBefore >= 0 ? IntimService.IntimBetween(g.world.playerUnit, npc) - intimPlayerBefore : int.MinValue;
                                ModMain.P("[Intim] 双修探针（原生 RoleTrains.closeAddMin/Max 自算，未落刀）：NPC对玩家Δ=" + d1 + " 玩家对NPCΔ=" + d2);
                            }
                            catch { }
                        }
                        // 言语类台账（09-23）：交互原文（带谁在说）+ 玩家每次的选择，随结果送回模型。
                        // 必须在 Resolve **之前**放进 data —— OnResolved 按订阅顺序跑，本类订阅晚于
                        // WsClient，事件里补的字段进不了已发出的 response。
                        try { TalkOutcome.FillResult(data); } catch { }
                        result = Ok(data);
                    }
                    else
                    {
                        // ★★ 2026-10-23 定案（终）：`ok=false` = 引擎拒了这次「互动动作创建」★★
                        //
                        //   完整链路（逐条反汇编 + 真机验证，详见 `docs/archive/2026-10-22-论道静默失败/修法与清理.md`）：
                        //     ActionStart @0x45E2210
                        //       → MovePoint(npc, toUnit) 返回 true（人到了）
                        //       → new UnitActionRoleDiscovery(toUnit)
                        //       → npc.CreateAction(discovery, isTip:false)   ← 返回码被引擎丢弃
                        //            → discovery.IsCreate @0x3F1A070
                        //                 → ① UnitActionRoleToUnitBase::IsCreate（剧情包/恶魔）
                        //                 → ② IsDayCost( Int32.Parse(conf.roleDiscovery.costDay) )
                        //                        ★ 这个 day 是**配置常数 5**，不是 action.day
                        //                        ★ 比的是 action.unit.data.unitData.residueDay
                        //                      ⇒ residueDay < 5 时返回 **1**
                        //                 → ③ WorldUnitEffectSpecialBannedFunc（命中返回 **3**）
                        //       → 返回码没人看 ⇒ 无 UI、无提示、只有回调 false
                        //
                        //   ⇒ 本次真机码是 **1**（天数不足），不是 3（禁令）。
                        //     `residueDay` 被扣光的原因是**旧的传送实现用了 `UnitActionMoveNPC`**：
                        //     它 `Init` 按距离算 `day`（61 格 ⇒ 15 天），`Create` 只看 `day>0` 就扣，
                        //     **即使 `OnCreate` 走的是瞬移分支**。已改用 `UnitActionSetPoint`（day 恒 0）。
                        //
                        //   ⚠ 两条**曾被写在这里、现已证伪**的结论，留一行防止重犯：
                        //     · "整月边界（roundDayResidue==0）导致拒绝" —— 假的，见 §文档「踩坑」；
                        //     · "`ok=false` 表示他还没走到你跟前" —— 假的，MovePoint 当时返回 **true**。
                        // 真机 11-10：只写"常见原因"玩家看着莫名其妙 —— 把**实际余暇**读出来。
                        // 反编依据不变：论道的门槛是 `IsDayCost(conf.roleDiscovery.costDay=5)`，
                        // 比的就是 `unit.data.unitData.residueDay`；读不到就不写这一段。
                        string leftDays = "";
                        if (op == "lun_dao")
                        {
                            try
                            {
                                leftDays = "（他此刻的当月行动余暇：" + npc.data.unitData.residueDay + " 天）";
                            }
                            catch { }
                        }
                        result = Fail(op + "没能生效：引擎没有接受这次互动动作，画面不会弹出界面。"
                                    + "常见原因：他当月的行动余暇不够（论道要 5 天）。" + leftDays
                                    + "换个日子、或先让他休整再来 —— 这不代表对方拒绝");
                    }
                    try { UnityEngine.Debug.Log("[AiAction] " + op + " 完成回调 ok=" + ok + " slot=" + slot
                        + " 同步=" + (!startReturned) + "）"); } catch { }
                    if (!startReturned)
                    {
                        // ★★ 10-21 定案②（本类 10-21 上一次真机的静默真因）★★
                        //   `ActionStart` 还没返回 ⇒ `WsClient` **尚未登记** `_deferred[req_id] = slot`
                        //   （登记发生在 ExecuteRequest 返回之后）。此刻若 `DramaGate.Resolve`：
                        //     · `OnDramaResolved` 反查不到 req_id ⇒ **结果被静静丢掉**；
                        //     · 而 Resolve 已把 slot 从 `_pending` 摘走 ⇒ 过期 Tick 也不会再管它。
                        //   ⇒ 那一帧 response 永远不发 → Python 干等满 120s 判「请求超时」。
                        //   这就是"论道静默"的真因，与"对方拒绝"无关。
                        //   修法：不 Resolve，把结果交给 `ActionStart` 返回后的代码当普通工具结果 return。
                        syncResult = result;
                        try { DramaGate.Cancel(slot); } catch { }   // 收票：不留给过期 Tick
                        try { DramaNativeClaim.Release(claimUid); } catch { }
                        try { TalkOutcome.Disarm(); } catch { }     // 动作没起来：撤锚点
                        return;
                    }
                    DramaGate.Resolve(slot, result);
                };
                action.ActionStart(UnhollowerRuntimeLib.DelegateSupport.ConvertDelegate<Il2CppSystem.Action<bool>>(managed));
                startReturned = true;
                // 同步回调已给出结论（引擎当场没接）⇒ 不再挂起，直接当普通工具结果返回。
                // 这一句就是"论道静默 120s"的修复点：模型毫秒级拿到失败原因，而不是空等到超时。
                if (syncResult != null) return syncResult;
            }
            catch (Exception e)
            {
                try { DramaNativeClaim.Release(claimUid); } catch { }
                try { TalkOutcome.Disarm(); } catch { }   // 动作没起来：撤销锚点
                DramaGate.Cancel(slot);   // 启动失败：静默注销（WsClient 尚未登记该 slot）
                return Fail(op + " 启动失败: " + e.Message);
            }
            try { DramaNativeClaim.BindSlot(claimUid, slot); } catch { }
            // 真机观察点（09-23 探针）：论道期间界面页应显示「闸1.5 认领」而**不再**注入按钮；
            // 结束时应有"完成回调"行 —— 若只有本行、始终没有回调行，说明 1037 不回调（届时改轮询）。
            try { UnityEngine.Debug.Log("[AiAction] " + op + " 已挂起（slot=" + slot + " npc=" + npcName
                + " uid=" + claimUid + " 凭证=已发 裁决=本动作族不给码）"); } catch { }
            return DramaGate.MakePendingMarker(slot);
        }

        /// <summary>
        /// 原生 UnitAction（Give/Askfor/Invite/TeachSkill）「完成挂起」登记。
        /// 返回非 null = 已登记 slot，CreateAction 成功后应返回 MakePendingMarker；
        /// 返回 null 且 busyFail=null = AutoConfirm（verify 工程）→ 调用方走旧立即路径；
        /// busyFail 非空 = 撞窗 busy（已有 AI 行动/确认窗等玩家响应），直接返回给 LLM。
        ///
        /// 结果不用这里给——Harmony postfix（UnitActionHooks，仅主工程）在原生 OnEnd() 后
        /// 调 UnitActionPending.Complete 组真实结局（读 receive/refuseProps、isAskforComplete、
        /// isInviteComplete、gainSkill 等落定字段）。真机截图实证赠送剧情"我收下了/我不需要这个"，
        /// 讨要/邀约/传功同理有选择——结局由玩家决定，工具必须等选择后再返回。
        /// 超时：DramaGate 120s 自动 Resolve"玩家长时间未响应"（与论道/确认窗一致）。
        /// </summary>
        /// <summary>
        /// 闸 1.5 包装：登记归因凭证 → 开确认窗 → 按结果处置。
        /// 凭证让 `DramaAiOption` 在「Execute 返回之后才弹的原生剧情窗」上仍能认出这是我方动作的
        /// 后续（机理见 DramaNativeClaim 头注释）。
        /// **没真挂起**（撞窗 busy / 开窗失败）→ 立刻收回，免得留一张空券在 ClaimTTL 内误挡玩家的窗；
        /// **真挂起** → 把券绑到槽位，槽位一 Resolve（点确定/拒绝、或 120s 超时）就自动吊销。
        /// </summary>
        internal static JObject Claimed(WorldUnitBase npc, Func<JObject> open)
        {
            string uid = DramaNativeClaim.UidOf(npc);
            DramaNativeClaim.ClaimFor(uid, "", true);   // 新链：区间锚点重置到此刻
            JObject res = open();
            DramaNativeClaim.ReleaseUnlessPending(res, uid);
            // `__slot__` 由 DramaGate.MakePendingMarker 写入，只在真挂起时才有意义
            if (res != null && res["__pending__"]?.Value<bool>() == true)
                DramaNativeClaim.BindSlot(uid, res["__slot__"]?.Value<int>() ?? 0);
            return res;
        }

        /// <summary>
        /// `mode=together`：**玩家当发起方**去打 <paramref name="foe"/>，玩家的队友（含调用工具的
        /// 这位 NPC，如果他是队友）会被引擎自己拉进同一场战斗。
        ///
        /// ## 为什么这条能复用现成的确认窗（10-27 反编定案，推翻我先前的"同步链没机会问"）
        ///
        /// `WorldBattleMgr::IntoBattle` 有 **三个重载**，职责分得很清：
        /// ```text
        /// IntoBattle@0x27FAAA0   ← 建数据 + 弹战前窗，**不进场景**
        ///     IntoBattleInit(...)                    @0x27FAC0E
        ///     UIMgr::OpenUI(...)                     @0x27FACF9
        ///     UIMapBattlePre::InitData(...)          @0x27FAD59   ← 战前窗
        /// IntoBattle@0x27FAD70   ← 薄壳，把 dungeonID 填 -1 转给上面那个
        /// IntoBattle@0x27FADA0   ← 真正进场景（`IntoBattleInit` + `ActiveMap` + 关 UI）
        ///     ← 由 `UIMapBattlePre::<UpdateUI>b__10_3`（玩家点「应战」）调
        /// ```
        /// `UIMapBattlePre::InitData` 全仓**只有 4 个调用者**，其中 `0x27FAD59` 就在这个重载里。
        ///
        /// ⇒ "弹窗 → 等玩家点 → 才进场景"在引擎里本来就是**两个阶段**，
        ///   `CreateAction` 返回时战斗根本还没开始。所以：
        ///   `BattleFlowGate` 照常采样到 `preShowing` → `TeamJoinGate` 照常弹
        ///   「一起应战 / 我一个人」→ 玩家点完 answer 落库 →
        ///   点「应战」→ `DungeonTrigger` → `TeamContainerInject` 取 answer → 队友进场。
        ///   **确认窗、注入链、战后归队、写经历，一个都不用改。**
        ///
        /// ## 这就是引擎自己的路
        /// `UINPCInfoProp::OnAttackClick`@0x330CC30（玩家在 NPC 面板点「攻击」）：
        /// ```text
        ///   0x330CD24  call UnitActionRoleAttack::.ctor       ← new UnitActionRoleAttack(那个NPC)
        ///   0x330CD3F  call 0x3F877C0 ; mov rdx,[rax+0x10]     ← 取玩家 = g.world.playerUnit
        ///   0x330CD67  call [r9+0x178]                         ← CreateAction(player, atk, isCreate)
        /// ```
        /// 我们只是把"玩家亲手点"换成"NPC 请你一起上"。
        ///
        /// ## ⚠ 不要把发起方写成 NPC
        /// `A.CreateAction(new UnitActionRoleAttack(B))`（A、B 都是 NPC）走
        /// `UnitActionRoleToUnitBase::NPCToNPCAction` **直接结算、没有场景**（0x183709480，无 IntoBattle）。
        /// 差别只在 `rcx` 是谁 —— 同一个动作类，接收者错了就完全不同。
        /// </summary>
        /// <param name="caller">调用工具的 NPC（= A，请玩家一起上的那位）</param>
        /// <param name="foe">要打的人（= B，`target` 解析出来的那个 NPC）</param>
        /// <param name="foeName">B 的名字（日志/结果用）</param>
        internal static JObject JoinPlayerAttack(WorldUnitBase caller, string foeName, WorldUnitBase foe)
        {
            if (foe == null) return Fail("认不出要打的人（单位已离场？）");
            if (caller == null) return Fail("推不动：请人一起上的那位已离场");
            var player = g.world != null ? g.world.playerUnit : null;
            if (player == null) return Fail("取不到玩家单位（未进世界？）");
            if (player == foe) return Fail("目标是玩家本人 —— 这条是「请你一起上」用的，要打玩家请把 target 留空");
            if (caller == foe) return Fail("不能请他打自己");
            if (TeamTool.IsPlayer(caller)) return Fail("发起方是玩家自己，请把 target 留空走原生攻击");

            // 队友才拉得进去。A 不是队友也能打，但那就是"玩家一个人上"——
            // 如实回给模型，别让它以为队伍里那位一定到场了。
            // （`TeamContainerInject` 读的是 `TeamTool.Members()`，只认号位气运。）
            int callerSlot = -1;
            try { callerSlot = TeamTool.SlotOf(caller); } catch { }
            bool callerIsMate = callerSlot >= 0;
            int mateCount = 0;
            try { mateCount = TeamTool.Members(out _).Count; } catch { }

            // 同格：玩家必须和 B 同格。
            //
            // ★ 校正（10-28 反编）：这条**不是**引擎在 `IsCreate` 里判的 ★
            //   早先这里写着"`ToUnitBase::IsCreate`@0x37B8430 会判"——**错的**。
            //   逐个查过：`WorldUnitBase::CreateAction`@0x32B8D40 只是 `IsCreate`→`Create` 的调度；
            //   `UnitActionRoleAttack::IsCreate`@0x3702150 只调父类 + 判攻击力/禁用；
            //   `UnitActionRoleToUnitBase::IsCreate`@0x37B8430（534 行）全是剧情包/行动类型判定，
            //   **没有任何坐标比较**。`CreateAction` 返回 0 时引擎还没查过位置。
            //   ⇒ 同格是**我们**的语义约束（隔着几个州打不着人），不是引擎的；
            //     在这里判是为了给一句能自纠的人话，而不是等引擎给个我们没样本的拒绝码。
            if (!ToolArgs.SameGrid(player, foe))
                return Fail("你与" + foeName + "不同格，无法开战（先走到同一处）");
            // ★ 请人者（A）自己也必须在场 ★（10-28 与 `solo` 对齐）
            //   为什么单列：玩家和 B 同格、而 A 在几个州之外，那就不是"A 请你一起上"，
            //   而是"玩家替 A 出头"—— 与工具语义不符（`together` = A 拉玩家入伙同打 B）。
            //   `solo` 那边已判 A 与 B 同格（`NpcDuel.AttackCore`），两条分支口径一致。
            //   同一个关闭开关：`_diag_no_duel_samegrid.txt`。
            if (!DiagSwitches.NoDuelSameGrid && !ToolArgs.SameGrid(caller, foe))
            {
                string cname = "";
                try { cname = caller.data.unitData.propertyData.GetName(); } catch { }
                if (string.IsNullOrEmpty(cname)) cname = "你";
                return Fail(cname + "与" + foeName + "不同格，无法一起出手（先走到同一处）");
            }

            var atk = new UnitActionRoleAttack(foe);
            string foeUid = "";
            try { foeUid = DramaNativeClaim.UidOf(foe); } catch { }

            // 登记必须在 `CreateAction` **之前**（同 attack 分支的理由：战前窗不是剧情窗，
            // 链上没有我方认得的 ClickOption 时机；OnEnd 会抢先收口成"没看到结局"）。
            var slot = DeferNativeUnitAction(atk, "attack", foeUid, () =>
                Ok(new JObject
                {
                    ["op"] = "attack", ["target"] = foeName, ["mode"] = "together",
                    ["battle_seen"] = false, ["battle_finished"] = false,
                }),
                out var busyFail);
            if (busyFail != null) return busyFail;
            if (slot != null)
            {
                var e = UnitActionPending.Take(atk.Pointer);
                // op 记 "attack"（不是 "together"）：`BattleOutcome` 的收口判据按 op 分支，
                // 而 together 的战斗形态与"NPC 打玩家"完全同形（都是 type=40 决斗），共用一套。
                BattleOutcome.Arm(slot.Value, foeName, e != null ? e.UnitID : foeUid, "attack", "together");
            }

            // ★★★ 接收者 = 玩家 ★★★
            //   这一行 = 整条需求的全部技术内容。
            //   写成 `caller.CreateAction(...)` 就会变成 NPCToNPCAction（直接结算、无场景）。
            int rc;
            try { rc = player.CreateAction(atk, true); }
            catch (Exception ex)
            {
                try { UnitActionPending.Remove(atk.Pointer); } catch { }
                if (slot != null)
                {
                    BattleOutcome.Abort(); DramaGate.Cancel(slot.Value);
                    try { TalkOutcome.OnSlotResolved(slot.Value, null); } catch { }
                }
                return Fail("拉你参战失败: " + ex.Message);
            }
            if (rc != 0)
            {
                try { UnitActionPending.Remove(atk.Pointer); } catch { }
                if (slot != null)
                {
                    BattleOutcome.Abort(); DramaGate.Cancel(slot.Value);
                    try { TalkOutcome.OnSlotResolved(slot.Value, null); } catch { }
                }
                try
                {
                    UnityEngine.Debug.Log("[UnitAction] together 被引擎拒绝 code=" + rc + "："
                                          + NativeRefuseWhy(player, atk));
                }
                catch { }
                return Fail(Refused("拉你参战", "发起战斗", player, atk, rc));
            }

            try
            {
                UnityEngine.Debug.Log("[UnitAction][together] 玩家当发起方 ⇒ 已 CreateAction"
                    + " 对手=" + foeName + "(" + foeUid + ")"
                    + " 请人者=" + (callerIsMate ? "队友" : "非队友") + "(slot=" + callerSlot + ")"
                    + " 队=" + mateCount
                    + "｜等战前窗 UIMapBattlePre → TeamJoinGate 会照常问「一起应战」");
            }
            catch { }
            try { UITipItem.AddTip("与你一同迎战" + foeName, 5f); } catch { }

            var data = new JObject
            {
                ["op"] = "attack",
                ["mode"] = "together",
                ["target"] = foeName,
                ["caller_is_mate"] = callerIsMate,
                ["team_size"] = mateCount,
            };
            if (slot != null)
            {
                // ⚠ 这里**不能**再往 marker 里塞东西 —— `MakePendingMarker` 只有
                //   `{__pending__, __slot__}` 两个键，没有 `data`。真正回给模型的是
                //   收口时 `BattleOutcome` 自己组装的那份（`mode` 由 `Arm(..., "together")`
                //   带过去）。10-27 先在这里写了个 `marker["data"]["mode"] = ...`，
                //   看着像生效、其实 `d` 恒为 null，字段从来没到过模型那儿。
                return DramaGate.MakePendingMarker(slot.Value);
            }
            return Ok(data);
        }

        internal static int? DeferNativeUnitAction(UnhollowerBaseLib.Il2CppObjectBase action, string op, string unitID, Func<JObject> buildResult, out JObject busyFail)
        {
            busyFail = null;
            if (ShowDramaService.AutoConfirm) return null;
            if (!DramaGate.TryDefer(ModIds.AiActionGate, out int slot))
            {
                // 链在收尾时给的是"战局刚了、处置还没走完…"那句（`RejectText`），不是"撞窗 busy"那句 ——
                // 后者会让模型以为"玩家还没回应，等会儿重试"，于是它在同一条链里反复试。
                busyFail = Fail(DramaGate.RejectText(
                    "已有一个行动在等待玩家响应（剧情/论道界面未关闭），请先完成当前交互再发起"));
                return null;
            }
            UnitActionPending.Register(action.Pointer, slot, op, unitID, buildResult);
            try { UnityEngine.Debug.Log("[UnitAction] " + op + " 已挂起等待玩家选择 slot=" + slot); } catch { }
            return slot;
        }

        /// <summary>
        /// 原生动作被引擎拒绝（`CreateAction` 返回非 0）时，用**引擎自己的两个数**拼一句可自查的理由。
        ///
        /// 为什么不直接翻译返回码：**码表语义零样本**（能拿到的只有"0 = 放行、非 0 = 拒绝"这条，
        /// 见 `RelationTool.cs` 里 `UnitActionRelationSet` 的码表，**不能推给别的动作类**）。
        ///
        /// ★★ 10-21 反汇编定案（`UnitActionBase::IsCreate` @0x1835083B0 → `IsDayCost` @0x1835083E0）★★
        ///
        ///   `UnitActionBase::IsCreate(day, isTip)`：
        ///       this.isTip = isTip;
        ///       eax = IsDayCost(day);  if (eax != 0) return eax;   // ← 非 0 原样上抛，什么都不做
        ///       this.isCreateCall = true; return 0;
        ///
        ///   `UnitActionBase::IsDayCost(day)`：判定链 + **两个返回点的实际机器码**（逐字节核对）：
        ///       cmp [某上限], day ;  jl  → 拒绝       （day 超过该上限）
        ///       residue = roundDayResidue();          （= 当月已过天数 mod 每月天数）
        ///       if (residue != 0) → 放行              （jne 0x183508486：`xor eax,eax` = 返回 0）
        ///       if (day > 0)      → 拒绝              （jg  0x183508501：`mov eax,1`   = 返回 1）
        ///       → 放行
        ///
        ///   ⇒ **`residue == 0`（整月边界）且 `day > 0` 时一律拒**。
        ///     `UnitActionMoveNPC::IsCreate` @0x1838E49C0 第一句就是 `esi = UnitActionBase::IsCreate(...)`，
        ///     非 0 直接 `mov eax,esi; ret` 原样返回 ⇒ **`code=1` 就是 `IsDayCost` 拒的**。
        ///     而 `UnitActionMoveNPC::Init` @0x1838E4810 把「两点距离 / 配表 timeCostPara」算成 `this.day`，
        ///     所以**跨城的传送/召唤会带 day>0**，正好撞在这条边界上 —— 这是用户报的
        ///     「传送到我这里显示失败 code=1」的真因。
        ///
        ///   ⇒ 另一条结论：**`code=1` 与"精力不足"没有半点关系**。旧文案把它写成"精力不足"
        ///     （因为 `costEnergy` 读不到、走到兜底句，而兜底句里硬编码了"战斗需要消耗精力"），
        ///     是**假事实** —— 模型据此让 NPC 去"休息补精力"，做的全是无用功。
        ///
        /// 现在只报**能自证的量**（精力 / costEnergy，读不到就说读不到），并把
        /// "整月边界拒绝跨日动作"这条**已核实的机理**明说出来 —— 它是唯一可行动的线索。
        ///
        /// ⚠️ 10-21 自我更正记录：本注释的**第一版读反了跳转方向**（把 `jne 0x…486` 当成"返回 1"），
        ///   于是写成"月结算期间一律拒"。实际 `0x…486` 是 `xor eax,eax`（放行）。
        ///   教训：看 `jXX <addr>` 必须回查那个地址**实际写了什么**，别靠上下文猜方向。
        /// </summary>
        internal static string NativeRefuseWhy(WorldUnitBase unit, object action, string verb = "发起战斗")
        {
            string name = "";
            try { name = (string)unit.data.unitData.propertyData.GetName() ?? ""; } catch { }
            string who = string.IsNullOrEmpty(name) ? "对方" : "「" + name + "」";

            int? energy = null, energyMax = null, cost = null;
            try { energy = (int)unit.data.unitData.propertyData.energy; } catch { }
            try { energyMax = (int)unit.data.unitData.propertyData.energyMax; } catch { }
            try { dynamic a = action; cost = (int)a.costEnergy; } catch { }

            // `roundDayResidue` = 当月已过天数 mod 每月天数。
            // ★ 拒动作的条件是 **residue == 0**（整月边界）且该动作要跨日 —— 不是"residue != 0"。
            //   第一次读反了跳转方向，这里连带把判据也纠正过来（详见本方法注释末尾的自我更正记录）。
            int? residue = null;
            try
            {
                dynamic wr = g.world.run;
                if (wr != null) residue = (int)wr.roundDayResidue;
            }
            catch { }

            // 该动作要跨几天（`UnitActionBase.day`；传送按距离算，见 MoveNPC.Init）
            int? day = null;
            try { dynamic a2 = action; day = (int)a2.day; } catch { }

            string have = energy == null ? "精力读不到"
                        : "精力 " + energy + (energyMax == null ? "" : "/" + energyMax);
            string need = (cost == null || cost <= 0) ? "" : "，本次需 " + cost;

            if (energy != null && cost != null && cost > 0 && energy < cost)
                return who + have + need + "：精力不足，无法" + verb
                     + "（游戏规则：角色在进行一些行为时（如战斗），需要消耗精力，精力不足则无法进行）";

            // 整月边界：只有**两个数都探到**且真的满足拒绝条件时才这么说（探不到就不猜）
            bool crossDay = day == null || day > 0;     // day 读不到时按"可能跨日"处理，仍只作提示
            if (residue == 0 && crossDay)
                return who + have + need + "：此刻正处在整月边界上（本月的最后一天），"
                     + "游戏不允许在此时发起需要跨日的动作 —— 过一天再试即可，不是对方拒绝";

            return who + have + need + "：引擎拒绝了这次动作"
                 + "（码 1 来自 UnitActionBase::IsCreate→IsDayCost：跨日动作在整月边界会被拒；"
                 + "另可能受精力/状态限制）";
        }

        /// <summary>
        /// 引擎拒绝（`CreateAction` 返回非 0）时的**统一失败文案**：`&lt;what&gt;失败：&lt;理由&gt;（引擎拒绝码 N）`。
        ///
        /// 九个动作工具共用一份 —— 这段字符串**直接进模型上下文**（失败帧不走渲染器，
        /// 见 `tools/text_render.py` 的 `if not res.get("success"): return None` 回退到全量 JSON），
        /// 所以只写一处、别各说各的；`verb` = 接在「无法」后面的动词短语（发起战斗 / 邀约 / 传送 / 偷取…）。
        /// </summary>
        internal static string Refused(string what, string verb, WorldUnitBase actor, object action, int code)
        {
            return what + "失败：" + NativeRefuseWhy(actor, action, verb) + "（引擎拒绝码 " + code + "）";
        }

        /// <summary>
        /// 异常栈的**首条非 interop 帧**（`at Xxx.Yyy(...)`）—— 只留这一行就够定位"谁抛的"。
        ///
        /// 为什么必须过滤：真机样本（09-24）里栈首是
        /// `at UnhollowerBaseLib.Il2CppException.RaiseExceptionIfNecessary(...)` —— interop 的壳帧，
        /// 有用的游戏侧帧在它后面。旧写法只截 160 字符，正好把那条吃掉，白等一轮。
        /// </summary>
        internal static string ExcStack1(Exception e)
        {
            try
            {
                string st = e == null ? null : e.StackTrace;
                if (string.IsNullOrEmpty(st)) return "(无栈)";
                foreach (string raw in st.Split('\n'))
                {
                    string s = raw.Trim();
                    if (s.Length == 0) continue;
                    if (s.Contains("UnhollowerBaseLib") || s.Contains("Il2CppException")
                        || s.Contains("System.Runtime.CompilerServices")) continue;   // interop 壳帧，跳过
                    return s.Length > 200 ? s.Substring(0, 200) + "…" : s;
                }
                return "(栈里只有 interop 帧)";
            }
            catch { return "(读栈失败)"; }
        }

        /// <summary>
        /// 通用：AI 载体挂到目标，执行"自身状态类"的 WorldUnitAIAction（疗伤/提升心情，无 toUnit）。
        /// </summary>
        internal static void StartSelfAiAction(WorldUnitAIActionBase action, WorldUnitBase npc)
        {
            var ai = new WorldUnitAIBase();
            ai.Init(npc);
            action.Init(ai, 1, null);
            action.ActionStart(NewAiActionCallback());
        }

        /// <summary>
        /// AI 动作回调（空操作，用于无需挂起等待的自身类动作）。真机实证（工具验证工程）：
        /// 1044 邀约/1034 疗伤/1041 提升心情在原生内部会调用 ActionStart 的回调，
        /// 传 null 直接 NRE（1037 论道/1031 双修当时容忍 null；起二者改走
        /// StartToUnitAiActionDeferred 挂真实回调，见该方法注释的真机观察点）。
        /// 原生代码同样传真实回调（types36_fulldll L46555/L46645），此处对齐。
        /// </summary>
        internal static Il2CppSystem.Action<bool> NewAiActionCallback()
        {
            return UnhollowerRuntimeLib.DelegateSupport.ConvertDelegate<Il2CppSystem.Action<bool>>(
                new System.Action<bool>(_ => { }));
        }
    }
}
