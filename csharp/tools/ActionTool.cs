/// <summary>
/// ActionTool —— **动作域·互动**：`world_ai_action`（切磋/攻击/双修/论道/邀约/传功 + 宗门入/退）＋传功要用的功法解析。
/// （11-19 由 6711 行的 `ToolExecutor.cs` 拆出，11-20 按 Python 的域归并；成员逐字搬移。）
/// 跨类调用一律 `<类名>.<方法>`；`internal` = 仅本程序集可见。
/// 【Python 对应】动作域·互动（`world_ai_action`，含 join_sect / leave_sect） —— 划分以 Python 侧为准，别在这里另立一套分类。
/// </summary>
using System;
using System.Collections.Generic;
using Newtonsoft.Json.Linq;
using static AgentLoopBridge.ToolResult;

namespace AgentLoopBridge
{
    internal static class ActionTool
    {
        internal static JObject WorldAiAction(JObject args)
        {
            // 注：对话窗已迁移到游戏 UI 管理器体系，层级由游戏自动排——
            // 这里唤起的确认/论道等弹窗后开自然盖住对话窗，无需再手动让位
            string op = (string)args["op"] ?? "";
            // 发起方恒为 harness 注入的 initiator（dialogue_agent._execute_tool_calls）。
            // `target`（09-16）= **对手方**：本工具的动作对象**只能是玩家**，schema 里也写明了不要传。
            var wub = ToolArgs.BindActor(args, out string initiator, out JObject bindFail);
            if (bindFail != null) return bindFail;

            // 宗门人事（10-29）：入宗/退宗**不是对玩家的行动**，actor 恒为当前对话 NPC，
            // 也不吃 `target`/`mode` ⇒ 必须在 ResolveCounterparty / AiActionTargetCore 之前分流，
            // 否则“玩家只是提议”会被当成对目标的动作而拒掉。见 SectJoin / SectLeave。
            if (op == "join_sect") return ActionTool.SectJoin(wub, args);
            if (op == "leave_sect") return ActionTool.SectLeave(wub, args);

            var player = ToolArgs.ResolveCounterparty(args, out string cpName, out JObject cpFail);
            if (cpFail != null)
                return cpFail;
            // 对 NPC 的处置（10-26 起 attack 例外；10-27 加 `mode` 两分支）：
            //   · `mode=solo`（默认）**单独应战** —— 玩家不参与，在旁边看 A 与 B 真打。
            //     走 `NpcDuel`（引擎的观战正门：`npcFightUnits` + `IntoBattle(null, 4001, …)`）。
            //     为什么不走 `A.CreateAction(new UnitActionRoleAttack(B))`：发起方是 NPC 时
            //     走 `NPCToNPCAction`，引擎**直接结算、没有场景**（反编 0x183709480，无 IntoBattle）。
            //   · `mode=together` **一起上** —— 玩家参战，队友（含 A）跟着进战场真出手。
            //     做法 = 让 B 当"敌人"、玩家当**发起方**：`player.CreateAction(new UnitActionRoleAttack(B))`。
            //     这条路引擎自己就在用：`UINPCInfoProp::OnAttackClick`@0x330CC30
            //     （反编 0x330CD24 构造 / 0x330CD3F 取玩家 / 0x330CD67 走 vtable+0x178 = CreateAction）。
            //     详见 `JoinPlayerAttack` 的方法注释。
            //   · 其余 op 仍显式拒绝：yao_yue/chuan_gong 走的是给玩家看的原生窗与面板，
            //     shuang_xiu/lun_dao 的完成信号也挂在玩家交互上 —— 没有对等物，别装作能做。
            // 判定全在 `AiActionTargetCore`（纯逻辑、可表驱动测）。这里只做分发。
            var route = AiActionTargetCore.Decode(op, (string)args["mode"], !string.IsNullOrEmpty(cpName));
            if (route.Fail != null) return Fail(route.Fail);
            if (route.Route == AiActionTargetCore.Route.Solo
                || route.Route == AiActionTargetCore.Route.Together)
            {
                // ⚠ 变量名容易骗人：`player` 在这里**不是玩家**，是 `target` 解析出来的对手方。
                //    `ResolveCounterparty(target=某NPC)` 返回的就是那个 NPC，`cpName` 才是它的名字。
                //    together 要的是"打谁"，所以传进去的是 `player`（= B）；
                //    真正的玩家单位由 `JoinPlayerAttack` 内部自己取 `g.world.playerUnit`。
                if (route.Route == AiActionTargetCore.Route.Together)
                    return NativeActionService.JoinPlayerAttack(wub, cpName, foe: player);
                return NpcDuel.Attack(wub, player, cpName);
            }
            string target = "";
            target = UnitIdentity.Name(wub, initiator);   // 取不到就用 harness 给的字符串

            switch (op)
            {
                case "spar":   // 切磋：UnitActionRoleDrill（types36:38182/46297 “双方发起了切磋!”）
                {
                    // ：由「发了就回」改为「等玩家在原生剧情窗里选完」
                    // 原生 21204 剧情窗弹「好，就让我和你切磋一下 | 我现在没有空」两条分支，
                    // 旧实现 `CreateAction` 完立刻 Ok ⇒ 玩家**婉拒了**，Python 侧那句写死的
                    // 「已向你发起切磋，即将进入战斗/切磋界面」照发，NPC 于是继续按"双方已开始切磋"演
                    // （用户报的"都是一样的，不太合适"）。
                    // 判据取**选项本身**（`DrillChoiceGate` 盯 `UIDramaBase.ClickOption` 的 prefix），
                    // 不取动作结束后的 `isDrillComplete` —— 后者语义没有真机样本（同类字段
                    // isInviteComplete 曾把"接受"误判成"拒绝"）。
                    // 09-22 更正：原先记的「OnEnd 在同意路径要等整场切磋打完才触发」是**错的** ——
                    // 真机 Player.log 4896→4897 显示 OnEnd 在**进战斗那一刻**就跑了。所以现在
                    // ① 判据挂 prefix（postfix 会晚于兜底，真判据被静默丢弃），
                    // ② 应战路径的结局改由 `BattleOutcome` 跨过战斗收（战后那两页的原话 + 谁在说）。
                    if (!ToolArgs.SameGrid(wub, player)) return Fail("切磋需同格，当前异地");
                    var drill = new UnitActionRoleDrill(player);
                    try { ((UnitActionRoleToUnitBase)(object)drill).isAutoAddLog = true; } catch { }
                    string drillUid = "";
                    try { drillUid = DramaNativeClaim.UidOf(wub); } catch { }
                    // BuildResult = **玩家没选就关窗**那条路专用（ESC / 点窗外：没有 ClickOption，
                    // 也就没有 BattleOutcome）。刻意不读 drill.isDrillComplete —— 语义未校准就不猜，
                    // 只如实报"没有明确答复"。应战路径由 DrillChoiceGate 把登记 Take 走交给
                    // BattleOutcome，这份委托在那条路上不会执行。
                    var drillSlot = NativeActionService.DeferNativeUnitAction(drill, "spar", drillUid, () =>
                        Ok(new JObject { ["op"] = "spar", ["target"] = target, ["answered"] = false }),
                        out var drillBusyFail);
                    if (drillBusyFail != null) return drillBusyFail;
                    if (drillSlot != null) DrillChoiceGate.Arm(drillSlot.Value, drill.Pointer, target);
                    // 09-23：内容不再由 `TalkOutcome` 单独收 —— 切磋走 `BattleOutcome`（它武装时的
                    // 锚点早于 CreateAction，21204 选择页与玩家的「接受切磋」都在区间里），
                    // 结果由 `ReadRange` 从段里读（页 + 选择，按时序）。
                    // ★ 09-24 同攻击：`CreateAction` 的返回值必须看 ★（裁决码表见 `RelationTool.cs`；
                    // 实证与理由见下面 case "attack" 里那段长注释 —— 非 0 = 引擎拒绝、什么都没做，
                    // 丢了它就会挂 60s 等一场不存在的战斗）。切磋与攻击同族、`costEnergy` 同源，
                    // 精力不足同样会被引擎拒（游戏面板提示键 `npcInfo_jinglibuzu`=「精力不足」）。
                    int drillRet;
                    try { drillRet = wub.CreateAction(drill, true); }
                    catch (Exception e)
                    {
                        try { UnitActionPending.Remove(drill.Pointer); } catch { }
                        if (drillSlot != null)
                        {
                            DrillChoiceGate.Disarm(drillSlot.Value); DramaGate.Cancel(drillSlot.Value);
                            // 解武装台账：`Cancel` 不发 `OnResolved`，不清就会留下"在飞"的残台账。
                            try { TalkOutcome.OnSlotResolved(drillSlot.Value, null); } catch { }
                        }
                        return Fail("发起切磋失败: " + e.Message);
                    }
                    if (drillRet != 0)
                    {
                        // 引擎拒绝 = 动作没创建 ⇒ 与上面 catch 同一套解武装
                        try { UnitActionPending.Remove(drill.Pointer); } catch { }
                        if (drillSlot != null)
                        {
                            DrillChoiceGate.Disarm(drillSlot.Value); DramaGate.Cancel(drillSlot.Value);
                            try { TalkOutcome.OnSlotResolved(drillSlot.Value, null); } catch { }
                        }
                        try { UnityEngine.Debug.Log("[UnitAction] spar 被引擎拒绝 code=" + drillRet + "：" + NativeActionService.NativeRefuseWhy(wub, drill, "发起切磋")); } catch { }
                        return Fail(NativeActionService.Refused("发起切磋", "发起切磋", wub, drill, drillRet));
                    }
                    try { UITipItem.AddTip($"已向{target}发起切磋", 5f); } catch { }
                    if (drillSlot != null) return DramaGate.MakePendingMarker(drillSlot.Value);
                    return Ok(new JObject { ["op"] = op, ["target"] = target, ["pending"] = true });
                }

                case "attack":   // 攻击：跨过整场战斗 + 战后那串求饶/处置窗，才定工具结果（09-22）
                {
                    // 旧实现是 `CreateAction` 完立刻 Ok ⇒ 模型拿到一句写死的"已向你发起攻击，
                    // 即将进入战斗"，然后按"仗还没打"演台词，而玩家那边整条链（逃跑 / 战败求饶 /
                    // 战胜处置）早就走完了。现在与切磋同构：结局由 `BattleOutcome` 带回来
                    // （链上每一页的原话 + 谁在说），谁赢谁活由模型读原话判，我们不解析。
                    if (!ToolArgs.SameGrid(wub, player)) return Fail("攻击需同格，当前异地");
                    var atk = new UnitActionRoleAttack(player);
                    string atkUid = "";
                    try { atkUid = DramaNativeClaim.UidOf(wub); } catch { }
                    // BuildResult = 兜底：登记没被摘走、OnEnd 自己跑了（战斗压根没起来等）时
                    // 如实报"没看到结局"，字段缺失 ≠ 否定。
                    var atkSlot = NativeActionService.DeferNativeUnitAction(atk, "attack", atkUid, () =>
                        Ok(new JObject { ["op"] = "attack", ["target"] = target,
                                         ["battle_seen"] = false, ["battle_finished"] = false }),
                        out var atkBusyFail);
                    if (atkBusyFail != null) return atkBusyFail;
                    if (atkSlot != null)
                    {
                        // ★ 登记必须在 `CreateAction` **之前**摘走。切磋是在 `ClickOption` 的 prefix
                        //   里摘的（那里有"玩家先选"的剧情窗 21204）；攻击的前置窗是 `UIMapBattlePre`，
                        //   **它不是剧情窗** —— `InitData`/`ClickOption` 两个挂点都看不见它
                        //   （真机栈帧 Player.log 1556-1560：`IntoBattle ← UIMapBattlePre:<UpdateUI>b__10_3()`），
                        //   于是链上没有任何"我方认得的点击时机"早于战斗。留在表里的后果就是切磋
                        //   09-22 那个竞态的同一形状：OnEnd 抢先收口成"没看到结局"，真结局几分钟后
                        //   回来时 req_id 已被摘走。摘走之后本分支也**不再依赖"attack 的 OnEnd 何时跑"**。
                        var e = UnitActionPending.Take(atk.Pointer);
                        BattleOutcome.Arm(atkSlot.Value, target, e != null ? e.UnitID : atkUid, "attack");
                        // 09-23：攻击同走 `BattleOutcome` 一个读者（内容 = 段区间）。区间**截止在处置页
                        // 收口**（玩家胜利那支提前返回）；那之后的「击杀/放过」选择与尾页留在段里
                        // （用户 09-23 定：后面的交段处理上下文和选项）。
                    }
                    // ★ 09-24 定叉结论：`CreateAction` 的返回值**必须看** ★
                    // `RelationTool.cs` 里的裁决码表写明：**非 0 = 引擎拒绝且什么都没做**。
                    // 旧实现把这个返回值丢了，于是"引擎压根没接"与"接了但预告窗没弹"在日志里
                    // 一模一样 —— 都是干等 60s 静默收口（用户 09-24 两次复现：`区间=0 行`，
                    // 工具只拿到"没看到结局"，模型以为"仗还没打"继续演）。
                    // 真机实证（同一天，同一对人，`[AttackProbe]` 样本）：
                    //   · 玩家→管星贤：返回码 **0** → `OnCreate` 跑了 → 正常开打；
                    //   · 管星贤→玩家：返回码 **2** → `OnCreate` **没跑** → 什么都没发生。
                    // 两次唯一差别是"谁出刀"；被拒那次的现场是 **「管星贤」精力 1/80**
                    // （玩家出手那次精力 54→44，即一次战斗≈10 精力）。游戏自己的说明原文
                    // `role_attribute_desc_energy`：「角色在进行一些行为时（如战斗），需要消耗精力，
                    // 精力不足则无法进行」；NPC 面板同源的提示键 `npcInfo_jinglibuzu`=「精力不足」、
                    // `npcInfo_xiaohaojingli`=「需要消耗{0}精力」。
                    // ⇒ 非 0 一律当"**发起失败**"如实回（与 catch 走同一套解武装），
                    //   绝不挂 60s 去等一场根本没发生的战斗；理由由引擎自己的两个数（精力/费用）自证。
                    int atkRet;
                    try { atkRet = wub.CreateAction(atk, true); }
                    catch (Exception e)
                    {
                        try { UnitActionPending.Remove(atk.Pointer); } catch { }
                        if (atkSlot != null)
                        {
                            BattleOutcome.Abort(); DramaGate.Cancel(atkSlot.Value);
                            try { TalkOutcome.OnSlotResolved(atkSlot.Value, null); } catch { }   // 解武装台账（Cancel 不发 OnResolved）
                        }
                        return Fail("发起攻击失败: " + e.Message);
                    }
                    if (atkRet != 0)
                    {
                        // 引擎拒绝 = 动作没创建 ⇒ 与上面 catch 同一套解武装（槽位/台账/凭证一个不留）
                        try { UnitActionPending.Remove(atk.Pointer); } catch { }
                        if (atkSlot != null)
                        {
                            BattleOutcome.Abort(); DramaGate.Cancel(atkSlot.Value);
                            try { TalkOutcome.OnSlotResolved(atkSlot.Value, null); } catch { }
                        }
                        try { UnityEngine.Debug.Log("[UnitAction] attack 被引擎拒绝 code=" + atkRet + "：" + NativeActionService.NativeRefuseWhy(wub, atk)); } catch { }
                        return Fail(NativeActionService.Refused("发起攻击", "发起战斗", wub, atk, atkRet));
                    }
                    try { UITipItem.AddTip($"向{target}发起攻击", 5f); } catch { }
                    if (atkSlot != null) return DramaGate.MakePendingMarker(atkSlot.Value);
                    return Ok(new JObject { ["op"] = op, ["target"] = target, ["pending"] = true });
                }

                case "shuang_xiu": // 双修：需同格（已移除C端好感校验）
                    if (!ToolArgs.SameGrid(wub, player)) return Fail("双修需同格，当前异地");
                    // 完成挂起：神识传音 case17 实证 1031 挂 Action<bool>，p==true 即"双修完成"
                    return NativeActionService.StartToUnitAiActionDeferred(new WorldUnitAIAction1031 { toUnit = player },
                        wub, op, target);

                case "lun_dao":   // 论道：言语往来，可远程（神识传音 IL 实证 = 1037）
                    // 完成挂起：工具结果等玩家在原生论道界面操作完（30s~1min）才回给 LLM——
                    // 消除"UI 刚弹出、玩家还没选，NPC 就已经回话"的时序错位。
                    // 神识传音 case22 实证 1037 挂 Action<bool>，p==true 即"论道完成"。
                    return NativeActionService.StartToUnitAiActionDeferred(new WorldUnitAIAction1037 { toUnit = player },
                        wub, op, target);

                case "yao_yue":   // 邀约：必弹原版邀约剧情（UnitActionRoleInvite）
                {
                    // 已移除C端好感校验
                    // 完成挂起：原版邀约剧情玩家可选"同意/拒绝"（Invite.NPCToPlayerAction 带 3 个
                    // 选择回调 + forceAgree 旁路字段反证默认必弹选择），工具等选择后才返回。
                    var invite = new UnitActionRoleInvite(player);
                    // 武装剧情文本捕获：邀约接受后弹的第二层剧情里，{0} = 约定地点（真机实证）。
                    // 必须在 CreateAction 之前武装——第一层 81002 是随 CreateAction 同步弹出的。
                    DramaTextCapture.BeginInvite(wub);
                    var slot = NativeActionService.DeferNativeUnitAction(invite, "yao_yue", DramaNativeClaim.UidOf(wub), () =>
                    {
                        bool accepted = false, npcUpset = false;
                        try { accepted = invite.isInviteComplete; } catch { }
                        try { npcUpset = invite.isNPCReduceIntim; } catch { }
                        try { UnityEngine.Debug.Log("[UnitAction] yao_yue isInviteComplete=" + accepted + " isNPCReduceIntim=" + npcUpset); } catch { }
                        // 判定实测校准（两路径真机样本）：接受=(isInviteComplete=True, isNPCReduceIntim=True)、
                        // 拒绝=(False, True) → isInviteComplete 是唯一判据。isNPCReduceIntim 两条路径都为 true
                        //（更像"本次邀约挂了好感惩罚条件"——接受后 NPC 等三个月、爽约才扣，台词"勿要让我久候"佐证），
                        // 不作判据、仅随 data 透出。旧判定 `!accepted || npcUpset` 把接受路径误报成拒绝（用户踩中）。
                        // 取走捕获（收枪）：只在"接受"时透出原句——拒绝路径没有第二层剧情，
                        // 末条会停在第一层的邀请语，带上反而误导模型。
                        // 原句=第二层剧情的成品句（「我先前在新达镇附近发现了一处幽静之地…」），
                        // 地点就在句子里；抓不到就不写该字段（契约：字段缺失 ≠ 否定）。
                        string inviteText;
                        try { DramaTextCapture.TryTakeInvite(out inviteText); }
                        catch { inviteText = null; }
                        // `@` 标记解成人话（与 DramaAiOption.ExtractText 同规约）：
                        // 成品句里可能带 `@q_<unitID>|<hash>@`（人名）或 `@w_<hash>|…|<道具ID>|<数量>|@`
                        // （道具），直接透出模型读到的是 hash 乱码。解不出来会退回字段0 并留痕。
                        try { inviteText = UnitSnapshot.CleanLogText(inviteText); } catch { }
                        var jo = new JObject { ["op"] = "yao_yue", ["target"] = target, ["accepted"] = accepted, ["upset"] = npcUpset };
                        if (accepted && !string.IsNullOrEmpty(inviteText)) jo["invite_text"] = inviteText;
                        return Ok(jo);
                    }, out var busyFail);
                    if (busyFail != null) { DramaTextCapture.Abort(); return busyFail; }
                    // 返回码必须消费（同 attack 的长注释：`RelationTool.cs` 裁决码表，非 0 = 引擎拒绝、什么都没做）。
                    // 邀约请的是一整页原生剧情（81002 邀请语 + 接受/拒绝），引擎不接就**没有窗** ——
                    // 丢了码就会挂到 `DramaGate` 120s 超时、只回"玩家长时间未响应"（把"办不成"说成"玩家没理你"）。
                    int inviteRet;
                    try { inviteRet = wub.CreateAction(invite, true); }
                    catch (Exception e)
                    {
                        DramaTextCapture.Abort();
                        if (slot != null) { UnitActionPending.Remove(invite.Pointer); DramaGate.Cancel(slot.Value); }
                        return Fail("发起邀约失败: " + e.Message);
                    }
                    if (inviteRet != 0)
                    {
                        DramaTextCapture.Abort();
                        if (slot != null) { UnitActionPending.Remove(invite.Pointer); DramaGate.Cancel(slot.Value); }
                        try { ModMain.P("[UnitAction] yao_yue 被引擎拒绝 code=" + inviteRet + "：" + NativeActionService.NativeRefuseWhy(wub, invite, "发起邀约")); } catch { }
                        return Fail(NativeActionService.Refused("发起邀约", "发起邀约", wub, invite, inviteRet));
                    }
                    try { UITipItem.AddTip($"已向{target}发起邀约", 5f); } catch { }
                    if (slot != null) return DramaGate.MakePendingMarker(slot.Value);
                    return Ok(new JObject { ["op"] = op, ["target"] = target, ["pending"] = true });
                }

                case "chuan_gong": // 传授功法：需同格；skill 可选——省略时交游戏原生功法面板由玩家自选（09-11 定案）
                    // 落点 UnitActionRoleTeachSkill（原版 case31 反编实锤，types36_fulldll L47009）：
                    //   发起方.CreateAction(new UnitActionRoleTeachSkill(接收方, skill, TeachType.Teach))，
                    //   Teach=0 传授 / Study=1 学习（GGBH_API.TeachType 枚举实证）；校验用官方 SkillCanTeach。
                {
                    string skill = (string)args["skill"] ?? "";
                    if (!ToolArgs.SameGrid(wub, player)) return Fail("传功需同格，当前异地");

                    DataUnit.ActionMartialData skillData = null;
                    string seedName = "";
                    if (!string.IsNullOrEmpty(skill))
                    {
                        skillData = ActionTool.ResolveMartial(wub, skill);
                        if (skillData == null)
                            return Fail($"未找到功法「{skill}」，请用功法 ID（inspect_unit abilities 里的 id）或装备中的功法名");
                        if (!UnitActionRoleTeachSkill.SkillCanTeach(wub, skillData))
                            return Fail($"功法「{skill}」不可传授（官方 SkillCanTeach 校验未过）");
                        seedName = skill;
                    }
                    else
                    {
                        // 玩家决定：不给 skill 时仍要一本作为挂载（构造必填），从 NPC 自己功法槽里
                        // 取一本可传的作 seed；游戏原生功法面板照常弹出，由玩家自选实际所学（送 gainSkill）。
                        try { skillData = ActionTool.FirstTeachableSkill(wub); } catch { }
                        if (skillData == null)
                            return Fail("对方暂无已学功法可传");
                        try { string nm = ActionTool.MartialDisplayName(skillData); if (!string.IsNullOrEmpty(nm)) seedName = nm; } catch { }
                    }

                    // 玩家为接收方（toUnit=player），NPC 发起（wub.CreateAction）——与原版 case31 语义一致。
                    // 完成挂起：原生询问剧情（TeachSkill.PlayAskDrama + ConfDramaOptionsItem 选项回调
                    // 反编实锤）玩家自选功法/接受拒绝；接受了才有 gainSkill——实际所学以面板选择为准。
                    var teach = new UnitActionRoleTeachSkill(player, skillData, UnitActionRoleTeachSkill.TeachType.Teach);
                    var slot = NativeActionService.DeferNativeUnitAction(teach, "chuan_gong", DramaNativeClaim.UidOf(wub), () =>
                    {
                        bool hasSkill = false;
                        try { hasSkill = teach.gainSkill != null; } catch { }
                        // 实际所学以玩家面板选择为准：优先回读 gainSkill 的中文名，回退 seed。
                        // 校准点：gainSkill 是否=玩家所选（而非构造种子）尚无真机样本——
                        // 诊断行把 seed / teachSkill(构造种子解码) / gainSkill(所学解码) 全部打出，
                        // 一次真实传功（选一本≠种子的功法）即可定案（gainSkillName≠seed 即成立）。
                        string gainedName = "";
                        try { gainedName = ActionTool.MartialDisplayName(teach.gainSkill); } catch { }
                        string teachSkillName = "";
                        try { teachSkillName = ActionTool.MartialDisplayName(teach.teachSkill); } catch { }
                        try { UnityEngine.Debug.Log("[UnitAction] chuan_gong seed=" + seedName
                            + " teachSkill=" + teachSkillName
                            + " gainSkillName=" + (hasSkill ? gainedName : "(null)")); } catch { }
                        bool byGainSkill = hasSkill && !string.IsNullOrEmpty(gainedName);
                        string gained = byGainSkill ? gainedName : seedName;
                        string source = byGainSkill ? "gainSkill" : "seed";
                        if (!hasSkill)
                            return Ok(new JObject { ["op"] = "chuan_gong", ["target"] = target, ["skill"] = gained, ["accepted"] = false, ["skill_source"] = source });
                        return Ok(new JObject { ["op"] = "chuan_gong", ["target"] = target, ["skill"] = gained, ["accepted"] = true, ["skill_source"] = source });
                    }, out var busyFail);
                    if (busyFail != null) return busyFail;
                    // 返回码必须消费（同 attack 的长注释）。传功的原生确认窗（玩家选学/不学）也靠 CreateAction
                    // 触发；引擎不接就没有窗，丢了码只能等 120s 超时。
                    int teachRet;
                    try { teachRet = wub.CreateAction(teach, true); }
                    catch (Exception e)
                    {
                        if (slot != null) { UnitActionPending.Remove(teach.Pointer); DramaGate.Cancel(slot.Value); }
                        return Fail("发起传功失败: " + e.Message);
                    }
                    if (teachRet != 0)
                    {
                        if (slot != null) { UnitActionPending.Remove(teach.Pointer); DramaGate.Cancel(slot.Value); }
                        try { ModMain.P("[UnitAction] chuan_gong 被引擎拒绝 code=" + teachRet + "：" + NativeActionService.NativeRefuseWhy(wub, teach, "发起传功")); } catch { }
                        return Fail(NativeActionService.Refused("发起传功", "发起传功", wub, teach, teachRet));
                    }
                    if (slot != null) return DramaGate.MakePendingMarker(slot.Value);
                    // AutoConfirm（verify 工程）旧立即路径：
                    return Ok(new JObject { ["op"] = op, ["target"] = target, ["skill"] = seedName, ["pending"] = true });
                }

                default:
                    return Fail("unsupported world_ai_action op: " + op);
            }
        }

        // ===== 宗门人事：入宗 / 退宗（10-29） =====
        //
        // 用户定调：「npc 自由决定是否加宗门还是退出……有一个参数，作为加入或者退出宗门的理由，
        // 你在这个参数描述的时候需要让 npc 自己考虑是否加入还是退出」「决定权在 npc 上面」。
        // 所以这里**不设任何我们自己的门槛**：门槛要么是引擎的（`IsCreate` 反编实证），
        // 要么是可查事实（在任人数、该州境界区间、需备之物），由 NPC 自己权衡。
        //
        // 引擎侧事实（RVA 见 docs/plan-sect.md §6）：
        //   · 加入 = `new UnitActionSchoolJoin(schoolID, postType, dept)`。游戏自己 33 处构造，
        //     实参覆盖 post 1~6 全部（宗主/大长老/长老/真传/内门/外门）⇒ **能直接以任意职位入**。
        //   · `schoolID` 用 `MapBuildSchool.schoolNameID`（跨引用 ID：`curHoldSchoolNameID`、
        //     `enemySchoolID` 都是这一套，真机已验过能解析出宗名）。
        //   · 加入的拒绝全在 `IsCreate`（0x37C8250）：base（月结算/日耗）、州境界上限
        //     （`ConfWorldAreaBaseItem.gradeMax` @0x28）、宗门交情、特殊剧情 NPC（码 12）。
        //   · 成功后的副作用全在引擎里：扣引荐之物（`CostPropItem`）、加好感（`AddIntim`）、
        //     离开旧职位（`List::Remove`）、挂 `UnitActionSchoolSetPostType`、写宗门日志。
        //     ⇒ **绝不自己改 postData**（否则缓存/日志/剧情全都不同步）。
        //   · 退出 = `new UnitActionSchoolExit()`（无参，`Init` 自己从 unit 推出 school/post/dept），
        //     `IsCreate`（0x37C6560）**只调 base、无任何门槛**；`OnCreate` 只清标记 + 写日志。

        /// <summary>入宗/退宗的状态文本（按返回码）。**码的含义只写反编能证实的**。
        ///
        /// 码表来源（全部逐条反编）：
        ///   `UnitActionSchoolJoin::IsCreate` @0x37C8250（主流程）、
        ///   `UnitActionSchoolSetPostType::IsCreate` @0x37CABD0（职位/名额那一层，被折叠成 4）、
        ///   `UnitActionSchoolExit::IsCreate` @0x37C6560（退宗，只可能有 1 / 2）。
        ///
        /// | 码 | 事实 |
        /// |---|---|
        /// | 1  | `UnitActionBase::IsCreate` → `IsDayCost(day)`（换日/月结算/日数不足） |
        /// | 2  | join：`unitData.schoolID`（`UnitInfoData` @0x30）**不是空** ⇒ 引擎认为他还在宗里 |
        /// |    | leave：`unit.data.unitData` 上一个谓词为假 ⇒ 引擎认为这个退宗动作不该发生 |
        /// | 3  | `IsHerdNPC()` 为真（兽群/生灵一类特殊角色） |
        /// | 4  | 内层 `SetPostType.IsCreate` 返回非 0 —— 引擎把内层原因**折叠**成 4 |
        /// | 7  | 目标宗 `buildData.exitSchoolUnit` 里记着此人、离宗月数还不够（退宗冷却） |
        /// | 8  | `GetUnitAI(unit)` 排程里的字符串与宗门 `curHoldSchoolNameID` 对得上且未到期 |
        /// | 11 | `setPostType` 缺失（`[action+0x48]==0`）⇒ 动作没被正确初始化（我们的问题） |
        /// | 12 | `ConfDramaNpc::GetFiveFlowerDramaNpcID` 命中（五花生灵/剧情限定角色） |
        /// | 5  | 只在 Lua 桥那条构造路径出现（脚本给 id），我们的链路碰不到 |
        /// </summary>
        /// <summary>
        /// 把一次入宗/退宗写进这个 NPC 自己的**经历**（11-11）。
        ///
        /// ## 为什么值得写
        /// `join_sect` / `leave_sect` 强制模型填 `reason`（"写清**你自己**为什么想进这个宗门"），
        /// 但原先它只回显在**工具结果**里：玩家下一轮一开口，那条返回就从上下文里滚掉了，
        /// 理由随之蒸发。而"我为什么入了青木宗""我为什么离开丹阳谷"恰恰是这个 NPC
        /// 该记一辈子的事 —— 写进经历，日后他与人聊起这段，才真有据可依
        /// （agent 读经历走 `UnitSnapshot.BuildLogs` → `WorldUnitLogMgr`）。
        ///
        /// ## 为什么理由原样照写
        /// 它是模型写的第一人称动机。改写或摘要会失真，而这个失真会**长期留在存档里**。
        /// 文案做成 `入{0}为{1}。{2}` 这种"自由文本槽"就是为了给一整段话留位置
        /// （与 `eventLogTeamDungeonDrama` 的 `{0}` 同款），不塞进语义槽。
        ///
        /// ## pending 为什么要单起一套文案
        /// `CreateAction` 只是把动作**排进队列**，返回 0 ≠ 已落地。落地与否靠回读名册判断：
        /// 读到了才 `joined/left=true`，读不到是 `pending`。pending 时**也要写**（不写这条
        /// 记录就永久丢了 —— 模型已收到"已受理"，下一轮不会重调这个工具），
        /// 但措辞必须是"已提请"而不是"已入"，否则日后他会跟人吹一件没成的事。
        ///
        /// ## 失败不吭声
        /// 写经历只是"留痕"，写不进去不该让入宗这件事本身变成失败 ——
        /// 全程 try/catch，失败只打日志（`TryAddLog` 内部已有回读，会打 `❌未落库`）。
        /// 注意顺序：**必须在 res 组装完、读完名册之后**调用，那时 `joined/pending` 才定得下来。
        /// </summary>
        internal static void WriteSectLog(WorldUnitBase npc, bool join, string sectName,
                                         string postCn, string reason, bool pending)
        {
            try
            {
                if (npc == null) return;
                var logMgr = g.world != null ? g.world.unitLog : null;
                if (logMgr == null) { TeamBattleFollowup.SafeLog("[Sect] 写经历跳过：g.world.unitLog 为空"); return; }
                if (string.IsNullOrEmpty(reason)) reason = "（未记理由）";

                string key, tpl, label;
                int id;
                string[] values;
                if (join && !pending)
                {
                    id = ModIds.LogSectJoin; key = ModIds.LogSectJoinKey;
                    tpl = "入{0}为{1}。{2}"; label = "入宗";
                    values = new string[] { "「" + sectName + "」", postCn ?? "", reason };
                }
                else if (join)
                {
                    id = ModIds.LogSectJoinPending; key = ModIds.LogSectJoinPendingKey;
                    tpl = "已提请入{0}为{1}。{2}"; label = "入宗（已提请）";
                    values = new string[] { "「" + sectName + "」", postCn ?? "", reason };
                }
                else if (!pending)
                {
                    id = ModIds.LogSectLeave; key = ModIds.LogSectLeaveKey;
                    tpl = "退出{0}。{1}"; label = "退宗";
                    values = new string[] { "「" + sectName + "」", reason };
                }
                else
                {
                    id = ModIds.LogSectLeavePending; key = ModIds.LogSectLeavePendingKey;
                    tpl = "已提请退出{0}。{1}"; label = "退宗（已提请）";
                    values = new string[] { "「" + sectName + "」", reason };
                }

                // 模板先就位：表里没有就运行时克隆注册（否则引擎只打「找不到日志」然后静默丢弃）
                if (!RoleLogTemplate.EnsureLogTemplate(id, key, tpl, label, "[TeamFollowup] "))
                {
                    TeamBattleFollowup.SafeLog("❌ [Sect] " + label + "经历未写：模板既不在表里、运行时也注册失败");
                    return;
                }

                UnitConditionData cond = null;
                try { cond = new UnitConditionData(npc, npc); } catch { }

                string who = null;
                try { who = npc.data.unitData.propertyData.GetName(); } catch { }
                if (string.IsNullOrEmpty(who)) who = "?";

                // 日志里展示的成品句（与引擎按模板渲染的结果一致），排障时一眼能对上
                string shown = tpl;
                for (int i = 0; i < values.Length; i++)
                    shown = shown.Replace("{" + i + "}", values[i] ?? "");

                // probeValue=reason：回读校验串必须是**独一无二**的那段。
                //   默认的 values[0] 是宗门名，而这个 NPC 的经历里本来就可能已有
                //   「退出「青木宗」」这种旧条目 ⇒ 拿它校验会让回读恒真、机制形同失效。
                // displayText=shown：否则会落进 TryAddLog 最后的 else，
                //   打出「与「青木宗」同闯秘境，并肩作战」这种完全错误的文案。
                // ══ 探针（11-15 联调，验完删）══ 先报"我要写什么"，再报"回读结果"。
                // 这是**两个不同的失败点**：模板没注册 → 引擎静默丢弃；写了但没合并 →
                // 面板里看不见（要等月结）。分开打才分得清是哪一种。grep `[MAIL-PROBE]`
                ModMain.P("[MAIL-PROBE] 宗门经历写入：npc=" + who + "｜模板id=" + id
                          + "｜key=" + key + "｜理由=" + reason + "｜成品=" + shown);
                bool _sectLogOk = TeamBattleFollowup.TryAddLog(logMgr, npc, key, values, cond, who,
                                                               false, reason, shown);
                ModMain.P("[MAIL-PROBE] 宗门经历回读：" + (_sectLogOk ? "✓ 已落库" : "✗ 未落库（看上面 ❌ 那行）"));
                if (_sectLogOk)
                {
                    // AddLogData 只进暂存层，面板读的是解码缓存 ⇒ 必须主动合并，否则要等月结/存档。
                    //
                    // ⚠ 第二个参数**只用于回读校验**（在解码后的 allLogData 里找这串值），
                    //   不参与合并本身。这里传 **reason** 而不是 `values[0]`（宗门名）：
                    //   宗门名会重复出现 —— 这个 NPC 的经历里本来就可能有「退出「青木宗」」这种
                    //   旧条目，拿它当校验串会让"这次到底写进去了没有"永远为真，回读就失效了。
                    //   理由是一整句话，独一无二。
                    try
                    {
                        TeamBattleFollowup.ForceMergeToPanel(
                            new List<WorldUnitBase> { npc },
                            new List<string> { reason });
                    }
                    catch { }
                }
            }
            catch (Exception e)
            {
                TeamBattleFollowup.SafeLog("[Sect] 写经历异常（不影响入宗/退宗本身）："
                                           + e.GetType().Name + " " + e.Message);
            }
        }

        internal static string SectActionRefuseText(int code, bool join)
        {
            string what = join ? "入门" : "离宗";
            if (code == 1)
                return "此刻无法办理" + what + "（引擎的通用闸 `IsDayCost`：换日/月结算期间，或这个动作要花的日数不够）。过一会儿再说。";
            if (code == 2 && join)
                return "引擎拒绝了入门（返回码 2）：他名下的宗门编号（`unitData.schoolID`）**还不是空的** —— "
                     + "引擎仍当他有宗门。多半是退宗的数据还没结算完（跨月才清干净）。隔一天/过个月再试。";
            if (code == 2 && !join)
                return "引擎拒绝了离宗（返回码 2）：引擎判定这个退宗动作此刻不该发生（他自己的状态前提不满足）。过一会儿再试。";
            if (code == 3 && join)
                return "引擎不许他入宗（返回码 3）：`IsHerdNPC` 命中 —— 他是兽群/生灵一类、被游戏排除在宗门体系外的角色。";
            if (code == 4 && join)
                return "引擎拒绝了入门（返回码 4）：**职位/名额那一关**没过（引擎把内层"
                     + "`SetPostType.IsCreate` 的具体原因折叠成了 4）。换一个身份（外门→内门）、"
                     + "或换一个宗门再试。";
            // 返回码 7：11-10 真机 + 反编定案 —— `UnitActionSchoolJoin::IsCreate` @0x37C8BB0 处的循环
            //   读 `school.buildData.exitSchoolUnit`（SchoolData 偏移 0xD0，`List<string>`，每条形如
            //   "unitID|离宗月"）：命中此人且 `当前月 − 离宗月 < 要求月数` 时 `AddTip` 后 **return 7**。
            //   实机对照：同一人 15:28 刚退「锦霄派永宁州分舵」，17:00 想回去 → 连吃两次 7；
            //   改投从没退过的「观莲教」→ 一次成功。配置 `SchoolPostParam.outExitSchoolMinMonth/MaxMonth = 1/12`。
            if (code == 7 && join)
                return "引擎拒绝了入门（返回码 7）：**他刚从这个宗门退出去，冷却期还没过** —— "
                     + "退宗的人会记进该宗的离宗名单（`exitSchoolUnit`），隔若干月才收他回来。"
                     + "换一个宗门，或等过了那个月再来——这条规矩是宗门自己的，不是我们拦的。";
            if (code == 8 && join)
                return "引擎拒绝了入门（返回码 8）：被他**自己的行事安排**挡住了 —— "
                     + "引擎那边记着一条跟这个宗门相关、还没到期的安排。换个时机或换个宗门再试。";
            if (code == 11 && join)
                return "入门动作没被正确初始化（返回码 11，`setPostType` 缺失）。这是**我们这边**的问题："
                     + "再试一次；要是连着出现，把这行日志发我。";
            if (code == 12 && join)
                return "引擎不许他入宗（返回码 12）：这类角色被剧情限定了身份（五花生灵一类）。";
            if (code == 5 && join)
                return "引擎拒绝了入门（返回码 5）：这条码只出现在 Lua 桥那条构造路径上，"
                     + "正常链路不该碰到——出现了请把这行日志发我。";
            return "引擎拒绝了" + what + "（返回码 " + code + "）。";
        }

        /// <summary>从名字找到"某一堂的长老"，返回其部门；找不到返回 0 并给候选。
        /// 引擎的 `InheritData` 只吃 `departmentType`（谁当师父由"哪一堂"决定），所以必须落到部门。</summary>
        internal static int SectResolveMasterDept(MapBuildSchool ms, string master, out string fail)
        {
            fail = null;
            if (string.IsNullOrEmpty(master)) return 0;
            DataBuildSchool.PostData pd = null;
            try { pd = ms.buildData.postData; } catch { }
            if (pd == null) { fail = "读不到这个宗门的人事，无法拜师"; return 0; }

            var names = new List<string>();
            for (int d = 1; d <= 5; d++)
            {
                DataBuildSchool.PostData.PostElders pe = null;
                try { pe = pd.GetElders((SchoolDepartmentType)d); } catch { }
                if (pe == null || pe.unitData == null || string.IsNullOrEmpty(pe.unitData.unitID)) continue;
                string nm = UnitIdentity.NameOfId(pe.unitData.unitID);
                names.Add(SectQueryTool.SectDeptName(d) + "·" + (string.IsNullOrEmpty(nm) ? "（未名）" : nm));
                if (!string.IsNullOrEmpty(nm) && nm == master) return d;
            }
            fail = "「" + master + "」不是这个宗门任何一堂的长老。" +
                   (names.Count > 0 ? "可拜的师父：" + string.Join("、", names.ToArray()) : "（该宗目前没有在任长老）");
            return 0;
        }

        /// <summary>把 `MapBuildSchool` 换成**引擎动作真认**的那个 build id。
        ///
        /// 反编实证（真机 11-10 两次入宗全 `NullReferenceException` 后定案）：
        ///   `UnitActionSchoolJoin::Init` @0x37C7FE0 → `InitData` @0x37CA800 里，
        ///   `schoolID` 最终喂给 `WorldBuildMgr.GetBuild(string)`；**取到 null 之后一路解引用**，
        ///   于是抛出裸 NRE（没有任何提示文字）。而 `MapBuildSchool.schoolNameID` 是
        ///   **算出来的宗名族 id**（getter @0x35F80C0 读 `schoolData` 再查表），**不是 build 的键**。
        ///   引擎自己造这个动作时读的是 `buildData` 偏移 0x18 的那个字符串
        ///   （33 个构造点全是 `mov rbx,[buildData+0x20…+0x18]`），也就是 `buildData.id`。
        ///
        /// 这里不硬押一个：候选逐个用**同一把** `GetBuild` 验一遍，**指针相同**才算命中；
        /// 一个都不中就交第一条出去（仍然让引擎裁决），并把候选全写进日志。
        /// </summary>
        internal static string ResolveJoinSchoolId(MapBuildSchool ms)
        {
            if (ms == null) return null;
            var ids = new System.Collections.Generic.List<string>();
            var names = new System.Collections.Generic.List<string>();
            try
            {
                string v = ms.buildData != null ? ms.buildData.id : null;
                if (!string.IsNullOrEmpty(v)) { ids.Add(v); names.Add("buildData.id"); }
            }
            catch { }
            try { string v = ms.schoolNameID; if (!string.IsNullOrEmpty(v)) { ids.Add(v); names.Add("schoolNameID"); } } catch { }
            try { string v = ms.curHoldSchoolNameID; if (!string.IsNullOrEmpty(v)) { ids.Add(v); names.Add("curHoldSchoolNameID"); } } catch { }
            for (int i = 0; i < ids.Count; i++)
            {
                try
                {
                    var b = g.world.build.GetBuild(ids[i]);
                    if (b != null && b.Pointer == ms.Pointer)
                    {
                        try { ModMain.P("[Sect] 入宗 schoolID 命中 " + names[i] + "=" + ids[i]); } catch { }
                        return ids[i];
                    }
                }
                catch { }
            }
            try
            {
                var sb = new System.Text.StringBuilder();
                for (int i = 0; i < ids.Count; i++) sb.Append(names[i]).Append('=').Append(ids[i]).Append("  ");
                ModMain.P("[Sect] 入宗 schoolID 候选都没通过 GetBuild 指针校验：" + sb.ToString().Trim()
                          + " ⇒ 仍按第一条发起（引擎若拒会照实回报）");
            }
            catch { }
            return ids.Count > 0 ? ids[0] : null;
        }

        /// <summary>join_sect：入宗。三种身份（外门/内门/真传），真传要指定是哪一堂的长老。
        /// 没给 post / 真传没给 master 时**不执行**，只回一句"要问玩家什么"的素材（`ask=true`）。</summary>
        internal static JObject SectJoin(WorldUnitBase npc, JObject args)
        {
            string reason = "";
            try { reason = ((string)args["reason"] ?? "").Trim(); } catch { }
            if (string.IsNullOrEmpty(reason))
                return Fail("入宗必须填 reason：写清**你自己**为什么想进这个宗门（向往其功法偏好、气运、某位长老、宗主有旧、想找个靠山…）。"
                          + "玩家只是提议，去不去由你判断；这条理由也会写进结果里。");

            string sectName = "";
            try { sectName = ((string)args["sect"] ?? "").Trim(); } catch { }
            if (string.IsNullOrEmpty(sectName))
                return Fail("join_sect 需要 sect：要入哪个宗门（可用 query_world topic=sects 先看清楚，同名分宗用 region 消歧）");

            string region = "";
            try { region = (string)args["region"]; } catch { }
            string rfail;
            MapBuildSchool ms = SectQueryTool.ResolveSchoolByName(sectName, region, out rfail);
            if (ms == null) return Fail(rfail);

            // 已在宗门：一人只有一个 schoolID —— 不替他退宗（退不退是另一件事、得他自己愿意）
            MapBuildSchool cur = null;
            try { cur = npc.data.school; } catch { }
            if (cur != null)
            {
                string curName = null;
                try { curName = cur.name; } catch { }
                bool same = false;
                try { same = cur.Pointer == ms.Pointer; } catch { }
                if (same)
                    return Fail("他已经在「" + (curName ?? sectName) + "」了，无需再入门。");
                return Fail("他还在「" + (curName ?? "别的宗门") + "」门下：一个人只能属于一个宗门，"
                          + "要换门得先退宗（leave_sect）——退不退，也由他自己判断。");
            }

            string schoolId = ResolveJoinSchoolId(ms);
            if (string.IsNullOrEmpty(schoolId))
                return Fail("拿不到这个宗门的内部编号，无法发起入门（换用全名或换一州试试）");

            string postRaw = "";
            try { postRaw = (string)args["post"]; } catch { }
            int post = SectQueryTool.SectPostOf(postRaw);

            // —— 没定身份：不执行，回"要问玩家的话"素材 ——
            if (post == 0)
            {
                var ask = new JObject
                {
                    ["op"] = "join_sect",
                    ["ask"] = true,
                    ["sect"] = sectName,
                    ["roster"] = SectQueryTool.SectRoster(ms),
                    ["reason"] = reason,
                };
                ask["question"] = "他想进「" + sectName + "」，但还没定以什么身份。要先问清玩家："
                                + "是入外门、入内门，还是拜在哪一堂的长老门下做真传弟子？";
                return Ok(ask);
            }

            int dept = 0;
            if (post == 4)
            {
                string master = "";
                try { master = ((string)args["master"] ?? "").Trim(); } catch { }
                if (string.IsNullOrEmpty(master))
                {
                    var ask = new JObject
                    {
                        ["op"] = "join_sect",
                        ["ask"] = true,
                        ["sect"] = sectName,
                        ["roster"] = SectQueryTool.SectRoster(ms),
                        ["reason"] = reason,
                        ["question"] = "做真传弟子得先定拜谁为师（引擎里「真传」是挂在某一堂长老名下的）。"
                                     + "要先问清玩家：拜在哪一堂的长老门下？",
                    };
                    return Ok(ask);
                }
                string mfail;
                dept = SectResolveMasterDept(ms, master, out mfail);
                if (dept == 0) return Fail(mfail);
            }

            // —— 真发起 ——
            string unitId = null;
            try { unitId = npc.data.unitData.unitID; } catch { }
            try
            {
                ModMain.P("[Sect] 入宗发起：宗门=" + sectName + " schoolID=" + schoolId
                          + " post=" + SectQueryTool.SectPostCn(post) + " dept=" + dept);
            }
            catch { }
            int code;
            try
            {
                var act = new UnitActionSchoolJoin(schoolId, (SchoolPostType)post, (SchoolDepartmentType)dept);
                code = npc.CreateAction(act);
            }
            catch (Exception e)
            {
                // 只回一句人话给模型，**完整栈进 Player.log**：11-10 那次 NRE 就因为只回 Message、
                // 没有栈，白猜了一轮（引擎侧到底是 GetBuild 拿空还是 ConfSchoolPost 拿空看不出来）。
                try { ModMain.P("[Sect] 发起入门异常（schoolID=" + schoolId + " 宗门=" + sectName + "）：" + e); } catch { }
                return Fail("发起入门失败: " + e.Message);
            }

            if (code != 0)
            {
                return Fail(SectActionRefuseText(code, true) + "（理由已记下：" + reason + "）");
            }

            // —— 读回验证（"返回 0" 只代表动作被创建，不代表已经落地） ——
            string after = null;
            int afterPost = -1;
            try
            {
                var s2 = npc.data.school;
                if (s2 != null)
                {
                    try { after = s2.name; } catch { }
                    try { if (unitId != null) afterPost = (int)s2.buildData.GetPostType(unitId); } catch { }
                }
            }
            catch { }

            var res = new JObject
            {
                ["op"] = "join_sect",
                ["sect"] = sectName,
                ["post"] = SectQueryTool.SectPostCn(post),
                ["reason"] = reason,
            };
            if (dept > 0) res["master_dept"] = SectQueryTool.SectDeptName(dept);
            try { string who = npc.data.unitData.propertyData.GetName(); if (!string.IsNullOrEmpty(who)) res["who"] = who; } catch { }
            if (!string.IsNullOrEmpty(after))
            {
                res["joined"] = true;
                res["now_sect"] = after;
                if (afterPost > 0) res["now_post"] = SectQueryTool.SectPostCn(afterPost);
            }
            else
            {
                res["joined"] = false;
                res["pending"] = true;
                res["note"] = "引擎已受理，但这一帧还没写进名册（动作可能排在下一拍）。别当失败说。";
            }
            // 写经历（11-11）：放在 res 组装完之后 —— 那时 joined/pending 才定得下来，
            // 文案要按"已成"还是"已提请"二选一。字段名与上面保持一致。
            WriteSectLog(npc, true, sectName, SectQueryTool.SectPostCn(post), reason,
                         !(res["joined"] != null && res["joined"].Value<bool>()));
            return Ok(res);
        }

        /// <summary>leave_sect：退宗。引擎侧**没有门槛**（`IsCreate` 只调 base），
        /// 我们只拦两类会造成乱子的：本就无宗、以及宗主/大长老（一退就空位，牵出继承流程）。</summary>
        internal static JObject SectLeave(WorldUnitBase npc, JObject args)
        {
            string reason = "";
            try { reason = ((string)args["reason"] ?? "").Trim(); } catch { }
            if (string.IsNullOrEmpty(reason))
                return Fail("退宗必须填 reason：写清**你自己**为什么想走（宗门待你不薄就该掂量；"
                          + "退宗会记进宗门日志、伤旧同门的脸面）。走不走由你判断，玩家只是提议。");

            MapBuildSchool cur = null;
            try { cur = npc.data.school; } catch { }
            if (cur == null)
                return Fail("他本就是散修，没有宗门可退。");

            string curName = null;
            try { curName = cur.name; } catch { }
            string unitId = null;
            try { unitId = npc.data.unitData.unitID; } catch { }
            int post = 0;
            try { if (unitId != null) post = (int)cur.buildData.GetPostType(unitId); } catch { }

            if (post == 1)
                return Fail("他是「" + (curName ?? "本宗") + "」的宗主，不能一走了之（宗门立刻空位、要牵继承）。"
                          + "真要散伙得先传位，这件事目前不支持。");
            if (post == 2)
                return Fail("他是「" + (curName ?? "本宗") + "」的大长老，走之前得先卸任（一退就是空位）。目前不支持。");

            int code;
            try { code = npc.CreateAction(new UnitActionSchoolExit()); }
            catch (Exception e) { return Fail("发起退宗失败: " + e.Message); }
            if (code != 0) return Fail(SectActionRefuseText(code, false) + "（理由已记下：" + reason + "）");

            string after = "散修";
            try
            {
                var s2 = npc.data.school;
                after = s2 == null ? "散修" : s2.name;
            }
            catch { }

            var res = new JObject
            {
                ["op"] = "leave_sect",
                ["from"] = curName ?? "",
                ["post"] = SectQueryTool.SectPostCn(post),
                ["reason"] = reason,
            };
            try { string who = npc.data.unitData.propertyData.GetName(); if (!string.IsNullOrEmpty(who)) res["who"] = who; } catch { }
            if (after == "散修" || string.IsNullOrEmpty(after))
            {
                res["left"] = true;
                res["now_sect"] = "散修";
            }
            else
            {
                res["left"] = false;
                res["pending"] = true;
                res["now_sect"] = after;
                res["note"] = "引擎已受理，但这一帧名册还没变。别当失败说。";
            }
            // 写经历（11-11）：与入宗同一条链。退宗记录的宗门名用**退之前那个**（curName），
            // 因为"退出{宗门}"说的是离开的那一家；此时 after 可能已是"散修"。
            WriteSectLog(npc, false, curName ?? "本宗", null, reason,
                         !(res["left"] != null && res["left"].Value<bool>()));
            return Ok(res);
        }

        /// <summary>
        /// 解析传功目标功法：优先 skill 参数直接按功法 ID 查（UnitInfoData.GetActionMartial(string)，
        /// 反编实锤：返回 DataUnit.ActionMartialData）；失败则按功法中文名在前几槽位（灵技/绝技/身法/神通/心法）
        /// 逐个匹配（名称经 GetActionMartial(id).data -> ConfBattleSkillPrefixName.GetName 组合中文名）。
        /// 返回 null 表示未找到。
        /// </summary>
        internal static DataUnit.ActionMartialData ResolveMartial(WorldUnitBase wub, string skill)
        {
            try
            {
                var ud = wub.data.unitData;
                // 1) 按 ID 直查（inspect_unit abilities 里的 id 字段）
                try
                {
                    var byId = ud.GetActionMartial(skill);
                    if (byId != null) return byId;
                }
                catch { }

                // 2) 按中文名匹配已学功法（仅查已知槽位 + 心法，避免遍历整个 allActionMartial 字典）
                var slotIds = new List<string>();
                try { slotIds.Add((string)ud.skillLeft); } catch { }
                try { slotIds.Add((string)ud.skillRight); } catch { }
                try { slotIds.Add((string)ud.step); } catch { }
                try { slotIds.Add((string)ud.ultimate); } catch { }
                try
                {
                    var abilitys = ud.abilitys;   // Il2CppStringArray（多心法）
                    if (abilitys != null)
                        for (int i = 0; i < abilitys.Length; i++)
                        {
                            try { slotIds.Add(abilitys[i]); } catch { }
                        }
                }
                catch { }

                foreach (var sid in slotIds)
                {
                    if (string.IsNullOrEmpty(sid) || sid == skill) continue;
                    try
                    {
                        var am = ud.GetActionMartial(sid);
                        if (am == null) continue;
                        var md = (dynamic)am.data;   // DataProps.MartialData（互操作层运行时绑定，见 UnitSnapshot.MartialSlot）
                        if (md == null) continue;
                        string nm = "";
                        try { nm = g.conf.battleSkillPrefixName.GetName(md); } catch { }
                        if (string.IsNullOrEmpty(nm)) continue;
                        if (nm.IndexOf(skill, StringComparison.Ordinal) >= 0 || skill.IndexOf(nm, StringComparison.Ordinal) >= 0)
                            return am;
                    }
                    catch { }
                }
            }
            catch { }
            return null;
        }

        /// <summary>
        /// 传功不指定 skill 时的 seed：从 NPC(wub) 自己的功法槽（灵技/绝技/身法/神通/心法）里
        /// 挑第一本能通过官方布尔校验（SkillCanTeach(wub, skillData)）的功法作为挂载——
        /// 只满足 UnitActionRoleTeachSkill 构造必填；实际学哪本仍由玩家面板选择（落 gainSkill）。
        /// 不用 SkillCanTeach(wub,player) 二参单值重载：其语义（返回 null）并不可靠，真机一度让
        /// 「身负多门功法的 NPC 也被判无可学」。
        /// </summary>
        internal static DataUnit.ActionMartialData FirstTeachableSkill(WorldUnitBase wub)
        {
            var slotIds = new List<string>();
            var ud = wub.data.unitData;
            try { if (!string.IsNullOrEmpty((string)ud.skillLeft)) slotIds.Add(ud.skillLeft); } catch { }
            try { if (!string.IsNullOrEmpty((string)ud.skillRight)) slotIds.Add(ud.skillRight); } catch { }
            try { if (!string.IsNullOrEmpty((string)ud.step)) slotIds.Add(ud.step); } catch { }
            try { if (!string.IsNullOrEmpty((string)ud.ultimate)) slotIds.Add(ud.ultimate); } catch { }
            try
            {
                var abilitys = ud.abilitys;   // Il2CppStringArray（多心法）
                if (abilitys != null)
                    for (int i = 0; i < abilitys.Length; i++)
                    {
                        try { if (!string.IsNullOrEmpty((string)abilitys[i])) slotIds.Add(abilitys[i]); } catch { }
                    }
            }
            catch { }
            foreach (var sid in slotIds)
            {
                try
                {
                    var am = ud.GetActionMartial(sid);
                    if (am == null) continue;
                    if (UnitActionRoleTeachSkill.SkillCanTeach(wub, am)) return am;
                }
                catch { }
            }
            return null;
        }

        /// <summary>
        /// ActionMartialData → 中文名（对齐 UnitSnapshot.MartialSlot 官方路径：
        /// am.data.To&lt;MartialData&gt;().martialInfo.name → GameTool.LS，兜底 battleSkillPrefixName）。
        /// 用于把传功 gainSkill（玩家实际所选）解码成可读名字；拿不到返回 ""。
        /// </summary>
        internal static string MartialDisplayName(DataUnit.ActionMartialData am)
        {
            if (am == null) return "";
            try
            {
                var dataObj = am.data;
                if (dataObj == null) return "";
                var md = ((dynamic)dataObj).To<DataProps.MartialData>();
                if (md == null) return "";
                if (md.martialInfo != null)
                {
                    string raw = null;
                    try { raw = (string)md.martialInfo.name; } catch { }
                    if (!string.IsNullOrEmpty(raw))
                    {
                        try { string ls = GameTool.LS(raw); if (!string.IsNullOrEmpty(ls)) return ls; } catch { }
                        return raw;
                    }
                }
                try { var n = g.conf.battleSkillPrefixName.GetName(md); if (!string.IsNullOrEmpty(n)) return n; } catch { }
            }
            catch { }
            return "";
        }
    }
}
