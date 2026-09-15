/// <summary>
/// UnitActionHooks —— 原生 UnitAction 完成钩子（仅主工程编译；verify 工程不引 0Harmony，
/// 故与登记表 UnitActionPending.cs 拆成两个文件——铁律：新 .cs 须登记 AgentLoopBridge.csproj，
/// 本文件只进主工程，UnitActionPending.cs 进两工程）。
///
/// 赠送/讨要/邀约/传功四个 UnitActionRole* 的 OnEnd() postfix：
/// UnitActionPending 登记表命中（本 mod 挂起的动作）→ 组真实结局 → DramaGate.Resolve。
///
/// 依据（unit_action_sigs.txt / types38_steal_marry.txt 反编实证）：
///   - 四类都有私有 OnEnd()（NativeMethodInfoPtr_OnEnd_Private_Void_0），Harmony 按名 patch 无碍；
///   - StealItem 无 OnEnd（结算钩子是 StealEnd）——偷取按用户拍板不挂起，不在此 patch；
///   - OnEnd 在玩家选择完剧情（我收下了/我不需要这个、同意/拒绝）后才触发，结局字段
///     （receive/refuseProps、isAskforComplete、isInviteComplete、gainSkill）此时已落定。
///
/// 真机观察点：各工具完成后 Player.log 应出现 "[UnitAction] <op> OnEnd 挂起命中 slot=N"；
/// 若工具挂起后 120s 超时且无此日志 = 对应动作不调 OnEnd（或 CreateAction 被引擎拒绝），
/// 届时改轮询 isComplete 方案。
/// </summary>
using HarmonyLib;

namespace AgentLoopBridge
{
    [HarmonyPatch(typeof(UnitActionRoleGive), "OnEnd")]
    internal static class UnitActionGiveHook
    {
        private static void Postfix(UnitActionRoleGive __instance)
        {
            // 必须吞异常：Postfix 抛异常会顺着游戏自身的动作结束链往上炸，
            // 导致该动作"结束不掉" → 游戏停在"等待行动" → 世界输入被门控
            try { UnitActionPending.Complete(__instance.Pointer); } catch { }
        }
    }

    [HarmonyPatch(typeof(UnitActionRoleAskfor), "OnEnd")]
    internal static class UnitActionAskforHook
    {
        private static void Postfix(UnitActionRoleAskfor __instance)
        {
            try { UnitActionPending.Complete(__instance.Pointer); } catch { }
        }
    }

    [HarmonyPatch(typeof(UnitActionRoleInvite), "OnEnd")]
    internal static class UnitActionInviteHook
    {
        private static void Postfix(UnitActionRoleInvite __instance)
        {
            try { UnitActionPending.Complete(__instance.Pointer); } catch { }
        }
    }

    [HarmonyPatch(typeof(UnitActionRoleTeachSkill), "OnEnd")]
    internal static class UnitActionTeachHook
    {
        private static void Postfix(UnitActionRoleTeachSkill __instance)
        {
            try { UnitActionPending.Complete(__instance.Pointer); } catch { }
        }
    }

    /// <summary>切磋：**只作兜底**，不作主判据。
    ///
    /// 主路径是 `UI/DramaDrillChoice.cs` 盯 `UIDramaBase.ClickOption` —— 玩家一点选项就定案
    /// （用户拍板"选项一落定就回，不等战斗"；且选项 id 是配表事实、免校准）。
    /// 本钩子接的是**没点选项就关窗**那条路（ESC / 点窗外）：动作结束 → 跑
    /// `ToolExecutor` case "spar" 注册的那份"没有明确答复"结果 → 免得白等 120s 超时。
    /// 正常路径上 `DrillChoiceGate` 会先 `UnitActionPending.Remove` 掉登记，这里找不到条目、零动作。
    ///
    /// 反编依据：`UnitActionRoleDrill` 与已挂的 Invite/TeachSkill 同构，own member 里有
    /// `public unsafe void OnEnd()` 与落定字段 `public unsafe bool isDrillComplete`
    /// （`F:\DecompDump\dump\unit_action_sigs.txt`）；`UnitActionRoleAttack` **没有 OnEnd**，
    /// 所以攻击接不了这条链（保持立即返回）。</summary>
    [HarmonyPatch(typeof(UnitActionRoleDrill), "OnEnd")]
    internal static class UnitActionDrillHook
    {
        private static void Postfix(UnitActionRoleDrill __instance)
        {
            try { UnitActionPending.Complete(__instance.Pointer); } catch { }
        }
    }
}
