/// <summary>
/// ModIds —— 本 Mod 在游戏全局 ID 空间里的私有领地（唯一事实来源）。
///
/// 为什么需要这个文件：
///   UICustomDramaDyn(dramaID) 的 dramaID 是全游戏共享的命名空间。剧情配置表条目由
///   **官方模组编辑器**管理：编辑器为本模组随机分配 MID（-803158451，"修改游戏配置"
///   界面显示的"当前配置ID"），配置表 xlsx 里 ID 写 `MID&偏移`，导出时真 ID = MID+偏移。
///   因此本 Mod 的私有段 = MID + ModIds 偏移——与 ModExcel 配置表（DramaDialogue 等
///   三张 xlsx）**必须同源同步**：改这里就要同步改表，改表就要同步这里。
///
/// 一经发布不得更改：dramaID 可能被存档/运行态引用，改号 = 旧引用全部失效
/// （MID 由编辑器固定；编辑器里"重置ID"按钮永远不要点）。
/// 新增占用一律先在本表登记，再写代码。
/// </summary>
internal static class ModIds
{
    /// <summary>私有段基址 = 官方编辑器分配的本模组 MID（编辑器"修改游戏配置"界面可查）。
    /// 旧值 2_000_000_000 为自造区间——剧情配置表无条目，UICustomDramaDyn 静默不开窗
    /// （"赠送灵石不弹窗"事故根因），已废弃。</summary>
    internal const int Mod = -803_158_451;

    // ---------- 分段总表（每段 10 个：窗 1 + 选项至多 9） ----------
    //
    //  偏移           用途                          段内约定
    //  -------------- ----------------------------- -------------------------------
    //  +0    ~ +99    系统保留（调试/通用模板）      ——
    //  +100  ~ +109   economy_item 赠送窗            +0 窗 / +1 确认 / +2 取消 / +3~8 追问 / +9 预留
    //  +110  ~ +119   item_acquire 偷窃/讨要确认窗   同上
    //  +120  ~ +129   social_relation 关系确认窗     同上
    //  +130  ~ +139   movement 召唤/传送确认窗       同上
    //  +140  ~ +149   world_ai_action 二次确认窗     +0 窗 / +1 确认 / +2 取消 / +3~8 追问 / +9 AI动作完成挂起忙键（不开窗）
    //  +150  ~ +159   查询信息展示窗（长文本）       +0 窗 / +1 知道了 / +2~9 预留
    //  +160  ~ +169   对话 AI 追问多选窗（回填输入） +0 窗 / +1 主选项 / +2~5 追问 / +6~9 兜底
    //  +170  ~ +179   自主互动当面确认窗（同意/不同意）+0 窗 / +1 同意 / +2 婉拒 / +3~9 预留
    //  +180  ~ +989   预留新工具（每工具 10 个，登记后使用）
    //  +990  ~ +999   实验/临时（禁止进正式分支）
    //  +1000 ~        未来非剧情用途（自定义 conf 条目等，另行规划）

    // ---------- 段基址（窗口 dramaId = 段基址 + 0） ----------
    internal const int DramaEconomyItemBase   = Mod + 100;
    internal const int DramaItemAcquireBase   = Mod + 110;
    internal const int DramaSocialRelationBase = Mod + 120;
    internal const int DramaMovementBase      = Mod + 130;
    internal const int DramaWorldAiActionBase = Mod + 140;
    internal const int DramaInspectInfoBase   = Mod + 150;
    internal const int DramaChatFollowupBase  = Mod + 160;
    internal const int DramaInitiativeBase    = Mod + 170;

    /// <summary>
    /// world_ai_action 段 +9（预留位征用，登记）：AI 动作完成挂起的 DramaGate 忙键。
    /// 论道/双修（AI 动作）与赠送/讨要/邀约/传功（原生 UnitAction + OnEnd postfix 挂起，
    /// 晚扩面）都不弹自制剧情窗（无窗号/选项号可占），但完成结果要经 DramaGate
    /// 延迟补发——复用其"同 key 同时只允许一个 pending"的撞窗检查，实现
    /// 「同一时刻至多一个交互在等玩家完成」。本键永不作为 dramaID 开窗。
    /// </summary>
    internal const int AiActionGate = Mod + 140 + OffReserve;

    // ---------- 段内偏移约定（选项 ID = 段基址 + 偏移） ----------
    internal const int OffWindow    = 0;   // 窗 dramaId（dialogueText 的 key 必须用它）
    internal const int OffPrimary   = 1;   // 主选项（确认/知道了）
    internal const int OffCancel    = 2;   // 取消
    internal const int OffFollowup0 = 3;   // 追问选项起始（+3 ~ +8，至多 6 个）
    internal const int OffReserve   = 9;   // 段内预留

    /// <summary>取某工具段的窗 ID（= 段基址 + 0）。dialogueText 的 key 必须与此一致。</summary>
    internal static int Window(int dramaBase) => dramaBase + OffWindow;

    /// <summary>取某工具段内指定偏移的选项 ID。</summary>
    internal static int Option(int dramaBase, int offset) => dramaBase + offset;
}
