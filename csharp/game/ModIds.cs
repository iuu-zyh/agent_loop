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
    //  +180  ~ +189   team 组队邀请窗（同意/不同意）  +0 窗 / +1 同意 / +2 不同意 / +3~9 预留
    //  +190  ~ +199   team 重伤剧情（无选项，纯展示） +0 窗 / +1~9 预留
    //  +200  ~ +209   team 决斗参战确认窗（一起应战/我一个人） +0 窗 / +1 同意 / +2 婉拒 / +3~9 预留
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
    internal const int DramaTeamInviteBase    = Mod + 180;
    internal const int DramaTeamHurtBase      = Mod + 190;
    /// <summary>「决斗/寻仇要不要带队友一起上」确认窗（10-25）。三个 id 都在本段：
    /// 窗 = `+200`，一起应战 = `+201`，我一个人 = `+202`。</summary>
    internal const int DramaTeamJoinBase      = Mod + 200;
    /// <summary>「NPC 对 NPC 观战」的挂起槽位键（10-26）。**不是剧情窗**，只当
    /// `DramaGate.TryDefer` 的 busy 键用（同一时刻只允许一场观战挂着）。
    /// 放在 +210 段：+200~+209 已归 team 决斗参战，+210 起是本功能。</summary>
    internal const int DramaNpcDuelWatch      = Mod + 210;

    // ---------- 经历（RoleLogLocal.xlsx）----------
    //
    // ★ 这张表**按数字 id 索引**，不是按 keyID 字符串 ★（`ConfRoleLogLocal.GetItem(int)`）。
    //   运行时写经历的入口是 `AddLogData(unit, string logKey, …)`，字符串 key 会先被
    //   `ConfRoleLogLocal.OnInit` 建好的 `allItemInKey` 字典翻译成 id；字典里没有那个 key ⇒
    //   引擎只打一行 `找不到日志：…` 然后**静默丢弃**（这条坑见 `TeamBattleFollowup` 文件头）。
    //
    // 为什么还需要一个"剧情见闻"模板（10-25）：
    //   现成的击杀模板是 `与{0}同闯秘境，斩杀{1}。` —— 两个占位符都是**语义槽**，
    //   塞自由文本进去会把"战绩"那个位置变成一段话，读起来是病句。而且基础游戏
    //   `RoleLogLocal.json` 全表 26712 行里**没有**任何"只有 `{0}`"的中性模板可借
    //   （唯一接近的 232 行是 `……`/`！` 这种剧情台词行，借来当履历等于把台词写进人生履历）。
    //   ⇒ 只能自带一行。
    //
    // ⚠ **这个数字一旦写进存档就不能改**：存档存的是 id，不是文字。改了 id，
    //   老存档里那条经历会指向别的模板或查不到。`Mod + 904` 取自本文件已登记的
    //   "私有段上界 999"之内、且与上面各剧情段/气运段都不冲突的偏移。
    internal const int LogTeamDungeonDrama = Mod + 904;

    /// <summary>「副本见闻」经历的 keyID（= `RoleLogLocal.xlsx` 里 `MID&amp;904` 那行的 keyID）。
    /// 运行时会按这个 key 兜底注册（表里没有也能用），所以它与表里的写法必须**逐字一致**。</summary>
    internal const string LogTeamDungeonDramaKey = "eventLogTeamDungeonDrama";

    // ---------- 经历：决斗（type=40，攻击 / 寻仇）----------
    //
    // 为什么必须另起两条：现有三条模板全是「同闯秘境 / 斩杀{1}」，而决斗既没有秘境、
    // 也没有成堆的怪 —— 硬套会写出「与某某同闯秘境，斩杀未知单位 ×12」。
    // 决斗的两条语义是「与谁并肩」「对面是谁」，所以 `{1}` 放**对手名**。
    //
    // ⚠ 与 `LogTeamDungeonDrama` 同规约：数字 id 一旦写进存档就不能改。
    //   `905/906` 在私有上界 999 之内，与各剧情段（+100~+209）、气运段（+51/55/56）都不冲突。
    /// <summary>决斗主经历：`与{0}并肩应战{1}。`（{0}=同行者名单，{1}=对手）。</summary>
    internal const int LogTeamDuel = Mod + 905;
    internal const string LogTeamDuelKey = "eventLogTeamDuel";
    /// <summary>决斗见闻：`与{0}共历此战：{1}`（{1}=战斗中剧情的成品句）。</summary>
    internal const int LogTeamDuelDrama = Mod + 906;
    internal const string LogTeamDuelDramaKey = "eventLogTeamDuelDrama";
    /// <summary>决斗里倒下的那条：`与{0}并肩应战{1}，战斗中身负重伤。`</summary>
    internal const int LogTeamDuelHurt = Mod + 907;
    internal const string LogTeamDuelHurtKey = "eventLogTeamDuelHurt";

    // ---------- 经历：NPC 对 NPC 观战（10-26）----------
    //
    // 观战是"两个 NPC 打、玩家在旁边看"，它跟上面那几条（副本 / 决斗）**当事人完全不同**：
    //   · 副本/决斗写的是**队友**（玩家一侧），当事人是玩家；
    //   · 观战要写的是**两个 NPC 自己**（胜方与败方各一条），玩家不是当事人。
    // 所以模板也必须另起：`{0}` 放"对面那个人"，`{1}` 放处置/胜负那一句。
    //
    // ⚠ 与上面同规约：数字 id 一旦写进存档就不能改。`908/909/910` 仍在私有上界 999 内，
    //   与各剧情段（+100~+209）、气运段（+51/55/56）都不冲突。
    /// <summary>观战 · 胜方：`{0}向我挑战，为我所败：{1}`（{0}=对手，{1}=处置结论）。</summary>
    internal const int LogNpcDuelWin = Mod + 908;
    internal const string LogNpcDuelWinKey = "eventLogNpcDuelWin";
    /// <summary>观战 · 败方：`我向{0}挑战，力不能敌：{1}`。</summary>
    internal const int LogNpcDuelLose = Mod + 909;
    internal const string LogNpcDuelLoseKey = "eventLogNpcDuelLose";
    /// <summary>观战 · 击杀（败方专用，替代 909）：`我败于{0}之手，为其所杀。`</summary>
    internal const int LogNpcDuelKilled = Mod + 910;
    internal const string LogNpcDuelKilledKey = "eventLogNpcDuelKilled";

    // ---------- 经历：入宗 / 退宗（11-11）----------
    //
    // 为什么要有这几条：`world_ai_action` 的 `join_sect` / `leave_sect` 强制模型填 `reason`
    // （"写清**你自己**为什么想进这个宗门"），但原先这个理由只回显在**工具结果**里 ——
    // 玩家下一轮一开口，那次调用的返回就从上下文里滚掉了，理由随之蒸发。
    // 而"我为什么入了青木宗""我为什么离开丹阳谷"正是这个 NPC 该记一辈子的事，
    // 也是 agent 日后能读到的唯一记忆通道（`UnitSnapshot.BuildLogs` 走 `WorldUnitLogMgr`）。
    //
    // 文案形态：**自由文本槽**，理由原样填进去（与 `eventLogTeamDungeonDrama` 的 `{0}` 同款）。
    // 理由是第一人称动机，改写会失真，所以不裁不改。做成
    //   入{0}为{1}。{2}      （0=宗门 1=身份 2=理由）
    //   退出{0}。{1}         （0=宗门 1=理由）
    // 而不是把理由硬塞进某个语义槽 —— 理由长短不定，塞进"身份"那种位置会出病句。
    //
    // ⚠ 与上面同规约：数字 id 一旦写进存档就不能改。`911~914` 仍在私有上界 999 内，
    //   与各剧情段（+100~+209）、气运段（+51/55/56）、既有经历（+1~3 / +904~910）都不冲突。
    /// <summary>入宗经历：`入{0}为{1}。{2}`（{0}=宗门，{1}=身份，{2}=他自己的理由）。</summary>
    internal const int LogSectJoin = Mod + 911;
    internal const string LogSectJoinKey = "eventLogSectJoin";
    /// <summary>退宗经历：`退出{0}。{1}`（{0}=宗门，{1}=他自己的理由）。</summary>
    internal const int LogSectLeave = Mod + 912;
    internal const string LogSectLeaveKey = "eventLogSectLeave";
    /// <summary>入宗**已提请但尚未落地**（`pending`）时的经历：`已提请入{0}为{1}。{2}`。
    /// 单列一条而不是复用 911 —— 措辞必须区分"已经成了"与"正在办"：
    /// 拿 pending 当既成事实，日后这个 NPC 会跟人吹一件没成的事。</summary>
    internal const int LogSectJoinPending = Mod + 913;
    internal const string LogSectJoinPendingKey = "eventLogSectJoinPending";
    /// <summary>退宗**已提请但尚未落地**时的经历：`已提请退出{0}。{1}`。</summary>
    internal const int LogSectLeavePending = Mod + 914;
    internal const string LogSectLeavePendingKey = "eventLogSectLeavePending";

    // ---------- 副本类型（ConfDungeonBaseItem.type）----------
    //
    // 攻击 / 寻仇开出来的战斗 = `dungeonID 11`，它的 dbi `type = 40`（官方语义「决斗」）。
    // 真机实证：`Player.log` 的 `进入副本：11，等级：1` + 栈 `IntoBattle(WorldUnitBase, Int32, …)`。
    // ★ 这是**配表事实**，不是从重载名推出来的分类器 —— 重载样本只有 1 次，不够当判据。
    internal const int DungeonTypeDuel = 40;

    // ---------- 气运段（RoleCreateFeature.xlsx）----------
    //
    // ★ 与上面的剧情段**互不干扰**：ID 的唯一性只在同一张表内成立。
    //   `RoleCreateFeature.MID&51` 与 `DramaDialogue.MID&51` 是两个东西，
    //   各自 = Mod + 51，互不冲突。本段登记的是**气运表**的占用。
    //
    // 为什么要有这几个常量（10-18 真机事故，别改回按名识别）：
    //   原先全靠「按名字反查」。而工坊里另有一个组队 mod（excelMID -2004589739）也把
    //   自己的气运叫「结伴而行」—— **两个 mod 同名**。按名反查撞到谁，完全取决于扫描
    //   顺序；真机日志里就撞到了工坊的 -2004589688。后果不是显示错，而是**功能失效**：
    //   我们的 `NpcCondition MID&003` = `feature_1_MID&51` 匹配的是**本 mod** 的气运 id，
    //   挂上工坊那枚，我们的 `DungeonTrigger` 永远不命中 ⇒ 队友不上场，工具却照样报成功。
    //
    // 所以：**识别一律用已知偏移**（本表登记，确定性），按名反查只留作兜底。
    // 名字只用于显示，不参与识别。
    // ★ 队友标记必须**一槽一枚**（10-18 深夜真机日志定案，别合并回一枚）★
    //   合成一枚时，`NpcCondition MID&003` 的条件1/2/3 是三个**完全相同**的筛选，
    //   而条件组不去重、`sortType=0` 走 SortRandom ⇒ 三段各自独立随机采样，
    //   同一人可能被抽中两次。真机实测：气运持有者 5 人，进副本的却是
    //   「方成和、温芸、方成和」—— 有人重复、有人根本没进。
    //   ⇒ 改成每槽一枚独立 id：每段的候选集**恰好只有 1 人**，
    //     引擎再怎么随机、怎么排序，结果也只能是那一个人，结构上不可能重复。
    internal const int LuckTeam = Mod + 51;   // 结伴而行 · 1 号位（队友标记，挂在 NPC 身上）
    internal const int LuckTeam2 = Mod + 55;  // 结伴而行 · 2 号位
    internal const int LuckTeam3 = Mod + 56;  // 结伴而行 · 3 号位
    internal const int LuckHurt = Mod + 52;   // 内外重伤（阵亡后 4 个月减益）
    internal const int LuckTalk = Mod + 53;   // 对话对象（隐藏，引擎内部用）
    internal const int LuckSelf = Mod + 54;   // 组队中（玩家侧，纯展示）

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
