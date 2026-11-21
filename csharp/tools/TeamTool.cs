/// <summary>
/// TeamTool —— `team` 工具的 C# 执行体（组队 / 解散 / 查询）。
///
/// 【为什么单独一个文件】
///   `ToolExecutor` 已经 5000+ 行。组队逻辑自成一体（气运挂载 + 条件组筛选 + 多人遍历），
///   而且它是**唯一**会写「队友标记气运」的工具，集中在一处便于审计。
///
/// ══════════════════════════════════════════════════════════════════════════
/// ★ 本文件其实有 **5 簇**职责（10-21 标注；拆不拆见 docs/features/team.md §9）★
/// ══════════════════════════════════════════════════════════════════════════
///
/// 名字叫 `TeamTool`，但它**不只是"工具执行体"** —— 一半是组队专用的通用设施，
/// 且这些设施已被组队之外的代码调用（`ToolExecutor` 也调 `NameOf`）。
/// 动这个文件之前先确认你改的是哪一簇：
///
/// | 簇 | 行号约 | 内容 | 谁在用 |
/// |---|---|---|---|
/// | **A. 工具执行**（7 个方法） | 84–620 | `Execute` / `Invite` / `Dismiss` / `Status` / 好感扣减 | 只有 `ToolExecutor`（`Execute`） |
/// | **B. 气运设施**（17 个） | 670–1660 | 按名反查、`AddLuck`/`RemoveLuck`/`HasLuck`、`LuckLayerState`、`IsDataNullTrap`、号位 ID | 全组 + `BattleKillStats` |
/// | **C. 名册**（16 个） | 750–1070 | 号位→人、`Members`、成员缓存、老数据迁移 | 全组（`Members` 是热路径） |
/// | **D. 文案调度**（6 个） | 239–415 | `SyncTeamState` / `Tick` / 文案重试 | `DiagSwitches`（`Tick`）、`ModMain`（`ResetTickState`） |
/// | **E. 通用小工具**（4 个） | 740–1725 | `NameOf` / `UnitIdOf` / `IsPlayer` / `Show` | **全工程**（`NameOf` 外部调用 27 处） |
///
/// **E 簇是唯一被组队之外广泛调用的部分**（`TeamBattleFollowup` 与工具层都在用 `NameOf`）。
/// 它是"取名字/取 unitID/判玩家"这类单位读取，与组队无关；
/// 若要迁移，正确去处是 `game/UnitSnapshot.cs`（那里已有 `IsPlayerUnit` / `PlayerUnitId`）。
/// ⚠ 但**不要简单用 `UnitSnapshot.IsPlayerUnit` 替换 `IsPlayer`** —— 两者兜底不同：
///   · `TeamTool.IsPlayer`  末尾兜底 = `u.Pointer == p.Pointer`（**引用/指针**判等）
///   · `UnitSnapshot.IsPlayerUnit` 末尾兜底 = **按名字**比较
///   组队这边刻意选指针：按名字兜底会把**同名 NPC** 判成玩家。
///
/// 【数据侧依赖】（`ModExcel` 配置表，见 `docs/features/team.md` §四）
///   · `RoleCreateFeature` `MID&51`「结伴而行」—— 队友标记气运（代码挂它 = 成为队友）
///   · `RoleCreateFeature` `MID&52`「内外重伤」—— 队友阵亡后 4 个月的减益
///   · `NpcCondition` `MID&003` —— 筛出「带结伴而行」的 NPC 组
///   · `DungeonTrigger` `MID&001` —— 进副本时把容器槽 0/1/2 的队友放进来（3 人上限）
///   · `BattleEffect` `MID&001` + `DramaDialogue` `MID&190` —— 阵亡 → 挂重伤
///
/// 【气运 ID 取法】
///   **不硬编码整数**。`MID&51` 导出后是 `excelMID + 51`，而 `excelMID` 由官方编辑器
///   随机分配（本 mod 的值见 `ModIds.Mod`，不要抄到这里）。所以运行时按**已知偏移**取：
///   `g.conf.roleCreateFeature.GetItem(ModIds.LuckTeam + 偏移)`。
///   ★ 11-16 已**删掉按名反查**（`_allConfList` 逐行比对名字）：工坊里有另一个组队 mod
///   的同名气运，按名会挂到别人那枚上 ⇒ 队友不上场而工具报成功（全静默）；
///   且扫描代价 75ms/次，偏偏总发生在"表还没加载好"那几秒。完整理由见 `SlotLuckId` 上方。
///
///   ★ 11-19 补：**取不到不等于没有**。世界刚进时 `roleCreateFeature` 里只有 vanilla 的
///   1309 行，本 mod 的 6 行（`MID&51..56`）是后并进来的；所以"取不到"一律当"还没到"，
///   退避重试到拿到为止（判据与节奏见 `DoDeferredSync`）。
///
/// 【为什么不依赖 DramaDialogue 弹「队伍」选项】
///   第三方组队 mod 是「对话 → 选『队伍』→ 再弹三按钮」，要覆盖原版剧情 22604。
///   本实现走**代码直挂**：`new DataUnit.LuckData{id} → unit.CreateLuck(data)`，
///   探针实测策略 #2 成功（`★ 挂气运策略 #2 成功`）。少建两张表、不碰原版剧情。
///
/// 【主线程约束】
///   全部方法须在 Unity 主线程调用（ToolExecutor 天然满足）。气运增删会触达引擎对象图。
/// 【Python 对应】动作域·组队（`team`） —— 划分以 Python 侧为准，别在这里另立一套分类。
/// </summary>
using System;
using System.Collections.Generic;
using System.IO;
using Newtonsoft.Json.Linq;
using static AgentLoopBridge.ToolResult;

namespace AgentLoopBridge
{
    internal static class TeamTool
    {
        // ---------- 气运名（与 RoleCreateFeature 表里的 key 一致） ----------
        /// <summary>队友标记。挂上 = 该 NPC 是队友；摘掉 = 退队。
        ///
        /// ★ 只有它在代码里还有用 ★（仅在"气运表里查不到这一枚"的错误文案里露脸）。
        /// 另外两枚（「内外重伤」`ModIds.LuckHurt`、「组队中」`ModIds.LuckSelf`）自从
        /// 按名反查被删掉之后就不再需要名字常量了 —— 它们的用途见各自的 `HurtLuckId` /
        /// `SelfLuckId` 注释。**不要因为"看着对仗"再把它们加回来**：没人读的常量
        /// 只会让下一个人以为还有按名解析的路径存在。
        /// </summary>
        internal const string LuckMateName = "结伴而行";

        /// <summary>队友上限。与 `DungeonTrigger MID&001` 里写的容器槽数（0/1/2）必须一致。</summary>
        internal const int MaxMembers = 3;

        // ══════════════════════════════════════════════════════════
        //  ★ 槽位气运：一槽一枚 id，结构上杜绝"重复队友" ★
        //
        //  为什么不能三个槽共用一枚气运（10-18 真机日志定案，别改回去）：
        //    `NpcCondition MID&003` 原先条件1/2/3 全是 `feature_1_<结伴而行>`
        //    —— 三个**完全相同**的筛选。而条件组**没有去重**（尾部只有
        //    `RemoveAll(u => u == null)`，闭包只判 null），排序 `sortType=0`
        //    走 `CommonTool.SortRandom` ⇒ 每段都是"从同一池子里独立随机抽一个"。
        //    真机日志（`[TeamBattleEvent] 名单三对比`）：
        //        ② 气运持有者 = 方成和、温芸、祁曼荷、张票、江宇文     （5 人）
        //        ① 实际进副本 = 方成和、温芸、方成和                   （重复！）
        //    ⇒ 5 个人里抽 3 次、可重复：3 人互不相同的概率只有 48%。
        //
        //  改法：每个槽筛**不同的**气运 id ⇒ 每段候选集恰好 1 人。
        //    随机也好、排序也好，结果都只能是那一个人 ⇒ 不可能重复、不可能串位。
        //  ★ 代价 ★ 队伍不满 3 人时，缺的那一槽筛不出人，`addBattleNPCData_<槽>`
        //    会取到 null。所以**下副本前请凑满 3 人**（`status` 会报 slots_left）。
        // ══════════════════════════════════════════════════════════
        private static readonly int[] SlotKnownIds = { ModIds.LuckTeam, ModIds.LuckTeam2, ModIds.LuckTeam3 };
        private static readonly int[] _slotLuckIds = new int[MaxMembers];
        /// <summary>该槽的气运 id 是否**已经拿到**（只有成功才置真）。
        ///
        /// ★ 11-19 收窄语义：只缓存成功 ★ 旧版把"表里有行但没这一枚"也当成确定结论缓存，
        /// 结果是一拍采样撞在"vanilla 的行已到位、本 mod 的行还没并进来"的窗口上，就把
        /// "暂时没有"永久固化成"永远没有"（真机：11-16 13:21 正常、14:52 起连续三局
        /// 「气运表里没有…表共 1309 行」⇒ 整局组队不可用，跨读档也不恢复）。
        /// `rows &gt; 0` 只证明**表对象建好了**，证明不了**我们的行并进来了** —— 后者的时序
        /// 我们无法观测，所以干脆不下这个结论：失败一律当"还没到"，由 `DoDeferredSync`
        /// 退避重试（≈1s × 15 次后转 ≈5s，无上限），拿到即止。
        /// </summary>
        private static readonly bool[] _slotLuckSettled = new bool[MaxMembers];

        /// <summary>连续"还没拿到"的次数（退避节奏 + 一次性告警用；拿到即归零）。</summary>
        private static int _luckMiss;
        /// <summary>最近一次问表时表里有多少行（-1 = 读不到）。**只用于日志** ——
        /// 排障时要能分清"行数一直 1309 不动（才可能是配置缺项）"与"行数在涨（正在并表）"。</summary>
        private static int _lastRows = -1;

        /// <summary>槽位数（= MaxMembers）。`NpcCondition MID&003` 的段数必须 ≥ 它。</summary>
        internal static int SlotCount { get { return SlotKnownIds.Length; } }

        // ---------- 气运 ID 缓存（只成功才缓存，失败的每次重试） ----------
        private static int _hurtLuckId;
        private static int _selfLuckId;

        /// <summary>上一次"组队就绪等待"的原因（只在变化时打日志，免得每秒刷一行）。</summary>
        private static string _lastWaitWhy;

        /// <summary>
        /// 气运表当前有多少行。**-1 = 读不到**（表对象还没建 / 字段取不到）。
        ///
        /// 用途：把"表对象还没建"与"有行但暂时没这一枚"分开 —— 两者**都是临时状态**
        /// （11-19 起不再用行数推断"配置确实缺"），只是日志措辞不同，排障时能看清到了哪一步。
        /// 成本 = 一次字段读 + 一次 `Count`，微秒级 —— 它是每秒重试的**唯一**常规开销。
        ///
        /// 为什么不用枚举 `_allConfList` 来查气运（探针走过的路）：Il2Cpp 的 `List&lt;T&gt;`
        /// 不实现 `IList&lt;object&gt;`，探针实测能读到 `Count=1372` 却取不到元素。
        /// 所以这里**只读 Count，绝不索引**。
        /// </summary>
        private static int LuckTableRowCount()
        {
            try
            {
                var conf = g.conf.roleCreateFeature;
                if (conf == null) return -1;
                var all = conf._allConfList;
                if (all == null) return -1;
                return all.Count;
            }
            catch { return -1; }
        }

        /// <summary>
        /// 入口：`team` 工具分发。op = invite / dismiss / status。
        /// </summary>
        /// <param name="self">发起方（NPC 本人）</param>
        /// <param name="counterparty">对象（缺省 = 玩家）</param>
        /// <param name="counterpartyName">对象名（空 = 玩家）</param>
        internal static JObject Execute(string op, WorldUnitBase self, WorldUnitBase counterparty,
                                       string counterpartyName, JObject args)
        {
            // 一次性迁移：老版本三个人共用一枚气运，真机留下了 5 个持有者。
            // 从气运本身**推不出**谁才是真队友 ⇒ 只能清空重来（见 EnsureSlotsMigrated）。
            // 放在这里而不是启动时：不用组队的人不该替这一步买单（一次全图扫描）。
            EnsureSlotsMigrated();

            switch (op)
            {
                case "invite":  return Invite(self, counterparty, counterpartyName);
                case "dismiss": return Dismiss(self, counterparty, counterpartyName);
                case "status":  return Status(self, args);
                default:
                    return Fail("未知的 team.op「" + op + "」（可选 invite / dismiss / status）");
            }
        }

        // ══════════════════════════════════════════════════════════
        //  invite —— 邀请入队
        //
        //  语义：发起方（NPC）把自己的「结伴而行」挂到**对象**身上。
        //        —— 注意方向：气运挂在队员身上，不是队长身上。
        //        `NpcCondition MID&003` 筛的就是「带这个气运的人」，
        //        `DungeonTrigger MID&001` 再把这些人塞进副本容器。
        //
        //  为什么挂到对方身上而不是自己：条件组是按「谁是队员」筛的。
        //        队长是谁不重要 —— 队友名单 = 有气运的人。
        // ══════════════════════════════════════════════════════════
        private static JObject Invite(WorldUnitBase self, WorldUnitBase who, string whoName)
        {
            if (who == null) return Fail("邀请对象不可用（未找到该人物）");
            string selfName = NameOf(self, "发起方");
            string targetName = NameOf(who, whoName);
            bool targetIsPlayer = IsPlayer(who);

            // ★ 谁是"队员"（气运挂在谁身上）★ —— 这是本工具最容易搞反的一处，写清楚：
            //
            //   `DungeonTrigger MID&001` 靠 `npcConditionExamine_MID&003` 挑人进副本，
            //   而 `NpcCondition MID&003` = 「带『结伴而行』气运的人」。
            //   ⇒ **气运必须挂在"要跟着下副本的 NPC"身上**，不能挂玩家：
            //     挂到玩家身上会让条件组把玩家也算成队员，容器槽里塞进玩家（真机必炸）。
            //
            //   target 填玩家（最常见）→ 语义是"我这个 NPC 跟你走" ⇒ 队员 = **self**
            //   target 填另一个 NPC     → 语义是"他跟你走"         ⇒ 队员 = **that NPC**
            var mate = targetIsPlayer ? self : who;
            if (mate == null) return Fail("找不到要入队的那个人");

            string mateName = NameOf(mate, targetIsPlayer ? selfName : targetName);

            // 发起方不能把玩家自己当成队员往副本里塞
            if (IsPlayer(mate))
                return Fail("玩家不能作为队伍成员被带入副本（队友必须是 NPC）");

            // ★ 重伤未愈不能入队（10-19）★
            //   判据用「内外重伤」气运本身，而不是某个临时标记：
            //   气运在 → 无论怎么重开/读档都还在；气运消失 → 自动恢复可邀请。
            int hurtId = HurtLuckId();
            if (hurtId != 0 && HasLuck(mate, hurtId))
            {
                int months = 4;
                try
                {
                    var lk = mate.GetLuck(hurtId);
                    int d = (int)((dynamic)lk).luckData.duration;
                    if (d > 0) months = d;
                }
                catch { }
                return Fail("「" + mateName + "」身负重伤（内外重伤，剩约 "
                            + months + " 个月），需静养，暂时无法入队");
            }

            // ★ 幂等判重：已经在某个槽里了？★
            //   注意判的是"占着任意一个槽"，不是"有没有 1 号位气运" ——
            //   老路径只认一枚气运，改成一槽一枚后必须整组判，否则同一个人会被挂两枚。
            int alreadySlot = SlotOf(mate);
            if (alreadySlot >= 0)
            {
                // 幂等路径也要刷描述：可能上次刷新失败过，或玩家侧气运丢了
                SyncTeamState(0);
                return Ok(new JObject
                {
                    ["op"] = "invite", ["target"] = mateName, ["already"] = true,
                    ["slot"] = alreadySlot + 1,
                    ["note"] = mateName + " 已经在队伍里了"
                });
            }

            // 找一个空槽（编号小的优先 ⇒ 队员顺序稳定，副本里出场顺序也稳定）
            int slot = -1;
            for (int s = 0; s < MaxMembers; s++)
            {
                if (SlotHolders(s).Count == 0) { slot = s; break; }
            }
            if (slot < 0)
            {
                var extra = new JObject
                {
                    ["limit"] = MaxMembers,
                    ["members"] = new JArray(ListMates().ToArray())
                };
                return FailWith("队伍已满（上限 " + MaxMembers + " 人）。请先 dismiss 一位队友再邀请「"
                                + mateName + "」", extra);
            }

            int luckId = SlotLuckId(slot, out string idErr);
            if (luckId == 0) return Fail(idErr);

            string err;
            if (!AddLuck(mate, luckId, out err))
                return Fail("为「" + mateName + "」挂气运失败：" + err);

            // 读回复查：挂完必须能读回来，读不到就是失败了（不谎报成功）
            if (!HasLuck(mate, luckId))
                return Fail("气运已写入但读回失败（「" + mateName + "」）。这次组队没有生效，请重试");

            // ★ 槽位唯一性：一个槽只能有一个人 ★
            //   刚被邀请的这个人**定义上**就是现任队员，同槽的其它人只能是旧数据残留
            //   （老版本三个人共用一枚气运，实测留下 5 个持有者）。
            //   不清掉的话，这一槽的候选集又变成"多人随机抽" ⇒ 重复队友会复发。
            int purged = PurgeSlotExcept(slot, mate);

            // ★ 写缓存（10-19）：此刻"谁进了哪一槽"是**确定事实**，
            //   记下来之后 Members/SlotHolders/SlotOf 全是 O(1)，不再扫全图。
            SetSlotMember(slot, mate);

            // ★ 玩家侧「组队中」气运 + 两边描述（用户要求：描述要跟着关系走）
            SyncTeamState(0);

            var res = new JObject
            {
                ["op"] = "invite", ["target"] = mateName, ["joined"] = true,
                ["slot"] = slot + 1,
                ["initiator"] = selfName,
                ["note"] = mateName + " 加入了队伍（下次进入副本时同行）"
            };
            if (purged > 0)
                res["purged"] = purged;   // 顺手清掉了几条同槽残留（正常应为 0）
            return Ok(res);
        }

        // ══════════════════════════════════════════════════════════
        //  组队状态的**双向同步**：玩家侧气运 + 两边文案
        //
        //  用户口径（10-17）：
        //    · NPC 的气运描述要说"正和玩家组队中"
        //    · 玩家也要有一个气运，描述"和谁组队"（多人就列出所有人）
        //    · 建立/解散关系时描述都要跟着更新 ⇒ 由这里统一刷
        //
        //  为什么只在这一处刷：invite / dismiss 是两个唯一会改变队友集合的动作。
        //  漏调一次的代价是"气运还在、描述没跟上"，玩家看到过期名单 —— 所以
        //  两个动作的**成功路径末尾**都必须经过本方法（契约测试钉住）。
        //
        //  全程不抛：纯展示层，失败只让描述停在上一次的值。
        // ══════════════════════════════════════════════════════════
        internal static void SyncTeamState(int mateLuckId)
        {
            try
            {
                var player = g.world?.playerUnit;
                if (player == null) return;

                string playerName = NameOf(player, "玩家");
                var names = ListMates();          // 以当前实际队友为准（幂等，跨全部槽位）

                // ★ 11-16：空名单**不一定等于"队里没人"** ★
                // `ListMates` → `Members` → `EnsureCache` → 建表；而建表在"世界还没读到
                // 单位列表"时会**静默失败**（`GetUnits` 返回 null ⇒ 直接返回，`_cacheReady`
                // 仍是 false）⇒ 这里拿到的就是空名单。
                // 旧写法据此去摘玩家身上的「组队中」气运 —— 于是读档后那几秒，玩家
                // "正在组队"这个状态被静默抹掉（面板看着像退队了），而队友气运其实还在。
                // 只有缓存**真的建好了**，空名单才代表"队里确实没人"。
                bool rosterKnown = _cacheReady;

                int selfLuckId = SelfLuckId();
                if (selfLuckId != 0)
                {
                    if (names.Count > 0)
                    {
                        if (!HasLuck(player, selfLuckId))
                        {
                            string e;
                            if (!AddLuck(player, selfLuckId, out e))
                                ModMain.P("[team] 玩家侧「组队中」气运挂载失败：" + e);
                        }
                    }
                    else if (rosterKnown && HasLuck(player, selfLuckId))
                    {
                        string e;
                        if (!RemoveLuck(player, selfLuckId, out e))
                            ModMain.P("[team] 撤玩家侧「组队中」气运失败：" + e);
                    }
                }

                TeamDescService.RefreshAll(names, playerName);

                // 缓存名单：文案重试时直接复用，**不再重跑全图扫描**
                // （旧版重试时又调一次 RefreshAll → ListMates → GetUnits(true)，
                //   等于把 115ms 的扫描乘上重试次数）。
                _lastNames = names;
                _lastPlayerName = playerName;

                // 文案表缺 key 时 SetText 静默失败 → 表现为"描述永远是静态那句"。
                // 世界刚加载那一拍表可能还没合并，所以排几次**有上限**的重试
                // （见 DoRetryText；只做字典查询 + 复用上面缓存的名单，不扫全图）。
                if (TeamDescService.MissingKeys().Count > 0 && _retryLeft <= 0)
                {
                    _retryLeft = RetryMax;
                    _retryFrame = RetryGapFrames;
                }
            }
            catch (Exception ex)
            {
                try { ModMain.P("[team] 同步组队状态失败：" + ex.Message); } catch { }
            }
        }

        // ══════════════════════════════════════════════════════════
        //  延迟一次性刷新（**已删除 300 帧周期心跳**，10-19 20:5x 用户拍板）
        //
        //  ── 为什么删掉原来的周期心跳 ─────────────────────────────────
        //  旧实现每 300 帧（≈4~5s）无条件做一次 **全图扫描**
        //  （`ListMates` → `Members` → `GatherSlots` → `AllNpcUnits` → `GetUnits(true)`，
        //    实测 ~115ms / 1556 个单位）来判断"成员签名变没变"。
        //
        //  真机日志实证它的代价（`Player.log` 的 `[TickProfiler]` 段）：
        //    · `diag` 的 max 恒定 ~115ms，周期**精确等于 300 帧** ——
        //      176fps 时 1.69s 一次、70fps 时 4.19s 一次，逐档吻合 300/fps；
        //    · `diag` 整个窗口的总耗时（111ms）≈ 那一次尖峰（115ms），占比 103%
        //      ⇒ diag 的开销几乎全部来自这一次扫描，其余三项都是亚毫秒级；
        //    · 70fps 下等于**每 4.2 秒硬卡 8 帧**，这是用户报"很卡"的主因。
        //
        //  而它换来的收益，同一份日志里是 **0 次触发**：
        //  整场 10 分钟约 140 次心跳，`SyncTeamState` 因"成员变了"被触发的次数 = 0。
        //  它想兜的"非 mod 渠道摘气运"（阵亡离队 / 别的 mod / 调试开关）经用户确认
        //  **不存在这种场景**；而 invite / dismiss 两条路径本来就直接调 `SyncTeamState`，
        //  压根不需要心跳转发。
        //
        //  ── 现在怎么做 ───────────────────────────────────────────────
        //  改成**事件驱动的一次性刷新**：只有真的发生了"会让描述过期"的事件时才排一次，
        //  执行完就归零 ⇒ 常态每帧只做一次 int 递减，开销可忽略（不再有任何全图扫描）。
        //
        //  触发点：
        //   · `ResetTickState()`（读档/换世界）—— 文案表 `allText` 会随世界重载重新初始化，
        //     我们运行时覆盖的气运描述全部丢失，必须补刷一遍（这是旧心跳真正在兜的事）；
        //   · `SyncTeamState` 发现文案表缺 key —— 排几次**有上限**的重试，
        //     且重试只用缓存的名单（`_lastNames`），**不再重复全图扫描**。
        // ══════════════════════════════════════════════════════════

        /// <summary>文案重试间隔（帧，≈2s）。</summary>
        private const int RetryGapFrames = 120;
        /// <summary>文案重试次数上限（× 2s ⇒ 约 12s）。</summary>
        private const int RetryMax = 6;

        /// <summary>待执行的同步倒计时（帧）。-1 = 无待办。</summary>
        private static int _syncFrame = -1;
        /// <summary>
        /// 重试间隔（帧）。≈1s@60fps / ≈0.3s@193fps。
        ///
        /// ★ 11-16：**没有"重试上限"了** —— 原来 `_syncGiveUp = 10` 用完就永久放弃，
        /// 而放弃之后缓存永久是冷的：玩家正常游玩时没人问，所以也不卡；可一旦进战斗、
        /// 预告窗那一帧第一次问"队里有谁"，就现场扫全图（实测 `diag.team` 峰值 175.93ms）。
        /// 现在单次尝试的代价只有一两次引擎属性读（气运 id 未定时多一次表查询），
        /// 所以可以一直等 —— 等不到就等不到，成本可忽略；等到就立刻建好。
        /// </summary>
        private const int SyncRetryFrames = 60;
        /// <summary>快速重试的失败次数上限：前 15 次按 `SyncRetryFrames`（≈1s），之后退到
        /// `SlowRetryFrames`（≈5s）。进世界后最初十几秒是"随时可能就绪"的窗口，密一点跟得上；
        /// 过了还没好就不必每秒打扰，但也**永不停止**（11-19：等不到只意味着晚一点，不意味着没有）。
        /// </summary>
        private const int FastRetryMisses = 15;
        /// <summary>退避后的重试间隔（帧，≈5s@60fps）。代价 = 一次 `GetItem` + 一次 `Count`。</summary>
        private const int SlowRetryFrames = 300;
        /// <summary>第几次失败时打一条"仍未就绪"（一次性现场快照：带上当时表里多少行）。
        /// 有了它，"配置真缺"（行数不动）与"并表慢"（行数在涨）在日志上才能分开。</summary>
        private const int ReportAfterMisses = 20;
        /// <summary>待执行的文案重试倒计时（帧）。-1 = 无待办。</summary>
        private static int _retryFrame = -1;
        /// <summary>文案重试剩余次数。</summary>
        private static int _retryLeft;
        /// <summary>上次同步时的成员名单/玩家名（供重试复用，避免二次全图扫描）。</summary>
        private static List<string> _lastNames;
        private static string _lastPlayerName;

        /// <summary>排一次"下一帧同步组队状态"（幂等：已有待办就不重复排）。</summary>
        internal static void RequestSync()
        {
            if (_syncFrame >= 0) return;
            _syncFrame = 1;
        }

        /// <summary>
        /// 每帧调用。**常态零开销**：无待办时只做两次 int 比较就返回，不碰引擎、不扫全图。
        /// （保留这个名字是为了不动 `DiagSwitches.FrameTick` 的调用点契约。）
        /// </summary>
        internal static void Tick()
        {
            try
            {
                if (_syncFrame > 0 && --_syncFrame == 0) DoDeferredSync();
                if (_retryFrame > 0 && --_retryFrame == 0) DoRetryText();
            }
            catch { /* 刷新失败永不打断主循环 */ }
        }

        /// <summary>
        /// 执行一次延迟同步。**没就绪就一直等**（不放弃，理由见 `SyncRetryFrames`）。
        ///
        /// 每次尝试只做三件廉价的事，任何一件不成 ⇒ 按退避节奏再来
        /// （≈1s × 15 次，之后 ≈5s，**无上限、永不放弃**）：
        ///   ① 拿 1 号位气运 id（表里零行 = 还没加载 ⇒ 不缓存，下一拍重新问）；
        ///   ② 玩家单位在不在；
        ///   ③ 真正 `SyncTeamState` 之后，队员缓存有没有建起来（世界还没读到单位列表时建不起来）。
        /// 第 ③ 条以前是漏的：建不起来时 `ListMates()` 返回空，而 `SyncTeamState` 会拿这个
        /// 空名单去**摘掉玩家身上的「组队中」**——世界半加载的那几秒，玩家的"正在组队"
        /// 就这么被静默抹掉了（见 `SyncTeamState` 里 `rosterKnown` 的注释）。
        /// </summary>
        private static void DoDeferredSync()
        {
            _syncFrame = -1;
            int slot0 = SlotLuckId(0, out string why);
            var player = g.world != null ? g.world.playerUnit : null;
            if (slot0 == 0 || player == null)
            {
                // ★ 11-19：只等，不判死 ★
                //   旧版在这里把"表里有行却缺这一枚"当成 permanent，报一次就**停掉重试** ——
                //   而 `rows > 0` 证明不了"本 mod 的行已并进来"（vanilla 的 1309 行先到，
                //   我们的 6 行后到），于是第一拍采样偏早就把整局固化成"组队不可用"。
                //   现在：无论哪种情况都继续等，只把节奏退避（≈1s × 15 次 → ≈5s，无上限）。
                //   每次尝试仍然只做"查一次气运 + 读一次玩家引用"，**不扫全图**
                //   （`SyncTeamState` 还没跑，全图扫描只在三件事都齐了之后发生一次）。
                if (player == null)
                {
                    _syncFrame = SyncRetryFrames;
                }
                else
                {
                    _luckMiss++;
                    _syncFrame = _luckMiss <= FastRetryMisses ? SyncRetryFrames : SlowRetryFrames;
                    if (_luckMiss == ReportAfterMisses)
                        ModMain.P("[team] 气运仍未就绪（已连续 " + _luckMiss + " 次；最近一次问表读到 "
                                  + _lastRows + " 行）—— 继续低频重试，不放弃；只有行数一直不变，"
                                  + "才可能是配置缺项");
                }
                if (string.IsNullOrEmpty(why))
                    why = player == null ? "玩家单位尚未就绪" : "气运尚未就绪";
                if (why != _lastWaitWhy)
                {
                    _lastWaitWhy = why;
                    ModMain.P("[team] 组队状态等待：" + why
                              + "（每 ~1s~5s 重试，不放弃；缓存仍是冷的，期间不会扫全图）");
                }
                return;
            }
            if (_luckMiss > 0)
            {
                // ★ 这一行是"并表时序"的现场仪表 ★ 以后不必再猜"什么时候才完整"：
                //   写"1 次"= 并表几乎瞬时（纯粹是采样太早）；写"十几次"= 并表要十来秒。
                ModMain.P("[team] 气运就绪：" + _luckMiss + " 次重试后拿到（最近一次问表读到 "
                          + _lastRows + " 行）");
                _luckMiss = 0;
            }
            _lastWaitWhy = null;
            SyncTeamState(0);
            if (!_cacheReady)
            {
                // 世界还没读到单位列表 ⇒ 缓存没建成。继续等，别让它冷着过整局。
                _syncFrame = SyncRetryFrames;
                if (_lastWaitWhy == null)
                {
                    _lastWaitWhy = "单位列表尚未就绪";
                    ModMain.P("[team] 组队状态等待：世界还没读到单位列表（每 ~1s 重试，不放弃）");
                }
            }
        }

        /// <summary>文案表就绪重试。只用缓存名单，**不做全图扫描**。</summary>
        private static void DoRetryText()
        {
            _retryFrame = -1;
            if (_retryLeft <= 0) return;

            if (TeamDescService.MissingKeys().Count == 0)
            {
                ModMain.P("[team] 文案表已就绪（重试 " + (RetryMax - _retryLeft + 1) + " 次后成功），气运描述已刷新");
                _retryLeft = 0;
                return;
            }

            _retryLeft--;
            try
            {
                TeamDescService.RefreshAll(_lastNames ?? new List<string>(), _lastPlayerName ?? "玩家");
            }
            catch { }

            if (_retryLeft == 0)
            {
                var miss = TeamDescService.MissingKeys();
                ModMain.P("[team] ⚠ 文案表重试 " + RetryMax + " 次仍缺 key："
                          + string.Join("、", miss.ToArray())
                          + "（如果这几个 key 在 LocalText.xlsx 里确实有，就是导出没带上它）");
            }
            else _retryFrame = RetryGapFrames;
        }

        /// <summary>换世界/读档时复位：排一次同步，把气运描述重新写回；
        /// 并**丢掉上一局关于气运的一切结论与计数**（11-19）。</summary>
        internal static void ResetTickState()
        {
            _retryFrame = -1;
            _retryLeft = 0;
            _lastNames = null;
            _lastPlayerName = null;
            // ★ 11-19：气运结论一律不跨世界继承 ★ 旧版这里什么都不清，于是上一局的误判会
            //   带进下一局：`SlotLuckId` 在 settled 处直接返回 0、`why` 为空，日志被兜底成
            //   "气运尚未就绪" —— 看着像"表还没加载"，其实那一刻已经判死了。
            //   （玩家 11-19 日志的第二次进世界就是这种形状。）
            _luckMiss = 0;
            _lastRows = -1;
            _lastWaitWhy = null;
            for (int i = 0; i < MaxMembers; i++) { _slotLuckSettled[i] = false; _slotLuckIds[i] = 0; }
            // ★ 进世界/读档：丢掉上一个存档的队员缓存，下次查询时重建一次
            //   （这是**唯一**会触发全图扫描的时刻，一个存档正常只发生一次）。
            ResetMemberCache();
            RequestSync();
        }

        // ══════════════════════════════════════════════════════════
        //  dismiss —— 解散 / 请离
        //
        //  ★ 两条路径的判据是「target 有没有点名一个具体 NPC」★，不是 `who == null`：
        //    `ResolveCounterparty` 在 target 留空时返回**玩家**（既有约定），
        //    所以 `who` 永远不为 null —— 早期按 `who == null` 判"解散整队"是死代码。
        //
        //    · target 留空 / target=玩家  → **解散整队**（玩家说"都散了吧"）
        //    · target=某个 NPC 真名        → 只请离那一位
        //
        //  好感惩罚还没接（用户选的是 -10~-6，需要配表值，见文档"待办"）。
        // ══════════════════════════════════════════════════════════
        private static JObject Dismiss(WorldUnitBase self, WorldUnitBase who, string whoName)
        {
            // 点名了一个 NPC ⇒ 只请离他；否则解散整队
            bool singleTarget = who != null && !IsPlayer(who);

            if (!singleTarget)
            {
                // 用 AllHolders 而不是 Members：Members 每槽只取一人，
                // 万一旧数据在同一槽挂了多个人，解散必须**全部**清掉，不能留尾巴。
                var holders = AllHolders();
                if (holders.Count == 0)
                {
                    // 幂等：本来就没队伍也要把玩家侧残留清干净
                    SyncTeamState(0);
                    return Ok(new JObject { ["op"] = "dismiss", ["disbanded"] = 0, ["note"] = "当前没有队友" });
                }

                var removed = new JArray();
                var failed = new JArray();
                var intimParts = new JArray();
                int intimTotal = 0;
                foreach (var m in holders)
                {
                    // ★ 先扣好感、再摘气运（顺序有意义，见 ApplyDismissIntim 的注释）
                    var loss = ApplyDismissIntim(m, "解散整队");
                    if (loss != null)
                    {
                        // `delta` 键**可能不存在** —— 读不到起点好感时 ApplyDismissIntim
                        // 刻意不写它（缺失=未知）。直接 `(int)loss["delta"]` 会抛，
                        // 而且 int.MinValue 混进总和会把结果变成 -21 亿这种垃圾大数。
                        var dv = loss["delta"];
                        if (dv != null) intimTotal += (int)dv;
                        intimParts.Add(loss);
                    }

                    string nm = NameOf(m, "?");
                    string e;
                    if (RemoveAllSlotLucks(m, out e)) removed.Add(nm);
                    else failed.Add(nm + "：" + e);
                }
                var res = new JObject
                {
                    ["op"] = "dismiss", ["disbanded"] = removed.Count,
                    ["removed"] = removed,
                    ["note"] = "队伍已解散"
                };
                if (failed.Count > 0) res["failed"] = failed;
                AttachIntim(res, intimParts, intimTotal);
                // ★ 清缓存（10-19）：整队已解散
                ResetMemberCache();
                // ★ 玩家侧气运 + 两边文案：此刻队友已清空 ⇒ 摘掉「组队中」并把描述复位
                SyncTeamState(0);
                return Ok(res);
            }

            string targetName = NameOf(who, whoName);
            int whoSlot = SlotOf(who);
            if (whoSlot < 0)
            {
                SyncTeamState(0);
                return Ok(new JObject
                {
                    ["op"] = "dismiss", ["target"] = targetName, ["already"] = true,
                    ["note"] = targetName + " 本来就不在队伍里"
                });
            }

            // ★ 先扣好感、再摘气运
            var singleLoss = ApplyDismissIntim(who, "请离单人");

            string err2;
            if (!RemoveAllSlotLucks(who, out err2))
                return Fail("为「" + targetName + "」摘除气运失败：" + err2);
            if (SlotOf(who) >= 0)
                return Fail("气运已删除但读回仍在（「" + targetName + "」）。这次请离没有生效，请重试");

            // ★ 清缓存（10-19）：这个人已经确定离队
            SetSlotMember(whoSlot, null);

            // ★ 只请离了一位：还有人则保留「组队中」，描述里的名单跟着变短
            SyncTeamState(0);

            var r2 = new JObject
            {
                ["op"] = "dismiss", ["target"] = targetName, ["left"] = true,
                ["note"] = targetName + " 离开了队伍"
            };
            if (singleLoss != null)
                AttachIntim(r2, new JArray { singleLoss }, (int)singleLoss["delta"]);
            return Ok(r2);
        }

        // ══════════════════════════════════════════════════════════
        //  解散的好感代价（用户口径 10-18：**单边**、**代码直接扣**）
        //
        //  为什么是单边：被请离的那个 NPC 心里不痛快 ⇒ **他对玩家**的好感掉；
        //  玩家对这位 NPC 的看法不受影响（玩家是主动放手的一方，谈不上记恨）。
        //  ⇒ `holder` = 被请离的 NPC，`other` = 玩家，delta < 0。
        //
        //  取值 = 组队 mod 的 `abClose_1_0_-10_-6`：参数1=B（对话对象=该 NPC）、
        //  参数2=A（玩家），区间 -10 ~ -6。这里同样在这个区间取随机，避免每次都一样。
        //
        //  为什么"直接扣"而不是配表动作：解散本身就是**代码摘气运**实现的
        //  （见 `RemoveLuck`），没有原生动作可挂好感钩子 —— 配表那套 `abClose` 只在
        //  剧情命令里有意义。所以这里直接走同一条写入层 `IntimService.ApplyIntim`
        //  （只传请求值、引擎按 RoleClose 系数换算、回读实得），与赠礼/讨要完全同源。
        //
        //  顺序：**先扣好感、再摘气运**。理由是"关系断了"才是因、好感掉是果；
        //  反过来先摘气运再扣，中间若抛异常就成了"人踢走了却一点代价没付"。
        //  扣好感失败也不阻断流程（已有其他工具同样口径：好感是副作用，不是前置条件）。
        // ══════════════════════════════════════════════════════════
        internal const int DismissIntimMin = 6;    // 扣得最少 6（mod 的 -6 端）
        internal const int DismissIntimMax = 10;   // 扣得最多 10（mod 的 -10 端）

        /// <summary>本次解散要扣多少（正数 = 要扣掉的绝对值）。抽出来便于测试与将来调参。</summary>
        internal static int RollDismissIntimLoss()
        {
            int lo = Math.Min(DismissIntimMin, DismissIntimMax);
            int hi = Math.Max(DismissIntimMin, DismissIntimMax);
            if (lo <= 0) return 0;
            if (lo == hi) return lo;
            try { return UnityEngine.Random.Range(lo, hi + 1); }   // Range(int,int) 上界独占
            catch { return (lo + hi) / 2; }
        }

        /// <summary>
        /// 给「被请离的 NPC」扣一次好感（他 → 玩家，单边）。
        /// 返回 `{name, delta}`（delta = 实得，负数）；玩家/无效单位/读不到起点时返回 null。
        /// </summary>
        private static JObject ApplyDismissIntim(WorldUnitBase mate, string tag)
        {
            try
            {
                if (mate == null || IsPlayer(mate)) return null;
                var player = g.world?.playerUnit;
                if (player == null) return null;

                int loss = RollDismissIntimLoss();
                if (loss <= 0) return null;

                // 单边：holder = NPC（他的好感掉），other = 玩家（掉的是他"对玩家"的那一条）
                int actual = IntimService.ApplyIntim(mate, player, -loss, "组队解散·" + tag);

                var o = new JObject { ["name"] = NameOf(mate, "?") };
                if (actual != int.MinValue) o["delta"] = actual;   // 读不到起点就不报数字
                return o;
            }
            catch (Exception e)
            {
                try { ModMain.P("[team] 解散扣好感失败：" + e.Message); } catch { }
                return null;
            }
        }

        /// <summary>
        /// 把好感结果挂到返回帧上。
        ///
        /// 两个键分工（渲染层据此措辞）：
        ///   · `intim_lost` = **总共掉了多少**（负数，实得之和）
        ///   · `intim_detail` = 逐人明细 `[{name, delta}]`
        /// 一个人的 `delta` 读不到（`int.MinValue`）时**不写这个键** —— 与 `PutIntim` 同口径：
        /// 缺失=未知，绝不把 int.MinValue 当数字发给模型。
        /// </summary>
        private static void AttachIntim(JObject res, JArray parts, int total)
        {
            if (res == null) return;
            if (parts != null && parts.Count > 0) res["intim_detail"] = parts;
            if (total != 0) res["intim_lost"] = total;
        }

        // ══════════════════════════════════════════════════════════
        //  status —— 查队伍
        //
        //  两层信息：
        //    1. 全图谁是队友（气运扫描）
        //    2. 各自是否「重伤」（另一个气运）
        // ══════════════════════════════════════════════════════════
        private static JObject Status(WorldUnitBase self, JObject args)
        {
            MateLuckId2(out int hurtId);   // 重伤 id 拿不到不算致命（只影响"是否重伤"一列）

            var mates = new JArray();
            int hurtCount = 0;
            int dupSlots = 0;

            for (int s = 0; s < MaxMembers; s++)
            {
                var hs = SlotHolders(s);
                if (hs.Count > 1) dupSlots++;
                if (hs.Count == 0) continue;

                var u = hs[0];
                var o = new JObject
                {
                    ["slot"] = s + 1,                    // 1/2/3 号位（= 副本里出场顺序）
                    ["name"] = NameOf(u, "?")
                };
                bool hurt = hurtId != 0 && HasLuck(u, hurtId);
                o["hurt"] = hurt;
                if (hurt) hurtCount++;
                if (hs.Count > 1) o["dup"] = hs.Count;   // 同一槽挂了多人 = 脏数据，报出来
                mates.Add(o);
            }

            // ★ 坐标不再输出（用户口径 10-18）：副本组队**不要求同屏**，
            //   离得远照样一起进本。给坐标只会让模型误判"人不齐"。
            var res = new JObject
            {
                ["op"] = "status",
                ["count"] = mates.Count,
                ["limit"] = MaxMembers,
                ["slots_left"] = MaxMembers - mates.Count,
                ["members"] = mates
            };
            if (hurtCount > 0) res["hurt_count"] = hurtCount;

            if (dupSlots > 0)
                res["warning"] = "有 " + dupSlots + " 个号位挂着多个人（旧数据残留），"
                               + "会导致副本里出现重复队友。请 dismiss 后重新邀请";
            if (mates.Count == 0) res["note"] = "当前没有队友（用 team.invite 邀请）";
            else if (mates.Count >= MaxMembers) res["note"] = "队伍已满（" + MaxMembers + " 人），下副本会三人同行";
            else res["note"] = "还差 " + (MaxMembers - mates.Count) + " 人满编"
                             + "（不满编时副本里只会来已入队的人，缺的号位会取到空）";
            return Ok(res);
        }

        // ══════════════════════════════════════════════════════════
        //  气运 ID 反查
        // ══════════════════════════════════════════════════════════

        // ══════════════════════════════════════════════════════════
        //  槽位气运 ID / 持有者查询
        // ══════════════════════════════════════════════════════════

        /// <summary>
        /// 取某个槽位的气运 ID。0 = **现在拿不到**（靠 `err` 区分"表还没加载"与"表里确实没有"）。
        ///
        /// ══════════════════════════════════════════════════════════════
        ///  ★ 11-16 删掉按名反查（用户拍板："ID 不可能变，没必要遍历气运表"）★
        ///
        ///  原来的写法：`GetItem(已知偏移)` 失败 ⇒ 读磁盘拿各 mod 的 excelMID ⇒
        ///  在 `[mid, mid+300]` 逐条 `GetItem` + 翻译名字比对 ⇒ 再兜底扫原版 `1..1500`。
        ///  删掉的理由是三条，任何一条都足够：
        ///
        ///   ① **它救不了它声称要救的情况。** 候选基址里第一位就是 `ModIds.Mod` 本身
        ///      （`InstalledModExcelMids` 无条件把自己塞进去），而扫描范围 `+0..+300`
        ///      必然包含 `+51` —— 也就是刚刚 `GetItem` 失败的那个位置。所以它只可能
        ///      找回"同一个不存在的 id"，或一个偏移对不上的 id（那说明代码与表不同源）。
        ///   ② **它会挂错人的气运**（10-18 真机事故的完整版本）：工坊里另一个组队 mod
        ///      的同名气运会被按名命中，于是队友身上挂的是**别人的**气运 ——
        ///      我们的 `NpcCondition MID&003` / `DungeonTrigger` 匹配不到 ⇒
        ///      **队友不上场，工具却报成功**（全静默）。
        ///      偏移与表是否同源，`tests/test_team_tool_contract.py::
        ///      test_luck_offsets_match_config_table` 在**开发期**逐行对过 xlsx ——
        ///      那才是正确的防线，不是让玩家在游戏里替我们扫 1500 条。
        ///   ③ **它在最坏的时机烧 75ms**：读 `ModData.cache`（玩家机器上根本没这个文件，
        ///      部署目录只有二进制的 `ModExportData.cache`）+ 探 300 + 探 1500 条逐条翻译。
        ///      而它偏偏总在"气运表还没加载好"时触发 —— 也就是进世界那几秒。
        ///      实测日志（`Player.log` 00:25）：每次重试 ~75ms，10 次 ≈ **750ms** 白卡。
        ///
        /// ★ 缓存语义（正确性，11-19 改）★
        ///   · 表里零行 ⇒ 还没加载 ⇒ **不记**；
        ///   · 表有行但缺这一枚 ⇒ **也不记**（这就是旧版的漏洞：`rows > 0` 分不清
        ///     "vanilla 的行已到位"与"本 mod 的行也并进来了"，真机两台机器都卡在 1309 行）；
        ///   · 只有**拿到 id** 才记 —— 成功了才不存在"下次还要不要问"的问题。
        /// "要不要放弃重试"由 `DoDeferredSync` 的退避节奏管，不在这里下结论。
        /// ══════════════════════════════════════════════════════════════
        /// </summary>
        internal static int SlotLuckId(int slot, out string err)
        {
            err = null;
            if (slot < 0 || slot >= MaxMembers)
            {
                err = "队友槽位越界：" + slot + "（应为 0~" + (MaxMembers - 1) + "）";
                return 0;
            }
            if (_slotLuckSettled[slot]) return _slotLuckIds[slot];

            int known = SlotKnownIds[slot];
            try
            {
                if (g.conf.roleCreateFeature.GetItem(known) != null)
                {
                    _slotLuckIds[slot] = known;
                    _slotLuckSettled[slot] = true;
                    return known;
                }
            }
            catch { }

            // 未命中：**两种都是"还没到"**，一律不记，由调用方退避重试。
            int rows = LuckTableRowCount();
            _lastRows = rows;               // 诊断：让日志能看出"当时表里多少行"
            if (rows <= 0)
            {
                // 表对象还没建 / 一行都没有 ⇒ 还没加载。**不记**，等下一拍。
                err = "气运表尚未加载（等待「" + LuckMateName + "」" + (slot + 1) + " 号位）";
                return 0;
            }
            // ★ 这里**不置 settled**（11-19）★ `rows > 0` 只说明表对象建好了。世界刚进时
            //   vanilla 的 1309 行先到位，本 mod 的 6 行（MID&51..56）是后并进来的 ——
            //   在这一拍判"确实没有"，就把一次采样提前固化成整局失效（11-16~11-19 真机，
            //   两台机器都是 1309 行、都跨读档不恢复）。
            err = "气运表里暂时没有「" + LuckMateName + "」" + (slot + 1) + " 号位（现 " + rows
                  + " 行，可能还没并进来）";
            return 0;
        }

        // ══════════════════════════════════════════════════════════
        //  队员缓存（**10-19 21:xx 用户拍板**：进世界遍历一次建表，之后只查表）
        //
        //  ── 为什么必须有它 ───────────────────────────────────────────
        //  旧实现的每一次"问一句队里有谁"都是**全图扫描**：
        //      SlotHolders / Members / AllHolders / AllNpcUnits
        //        → `g.world.unit.GetUnits(true)`（实测 ~100ms / 1556 个单位）
        //  而一次 `invite` 会问到 **5 次**（找空槽最多 3 次 + PurgeSlotExcept 1 次
        //  + SyncTeamState 1 次）⇒ 单次邀请可卡半秒；`dismiss` 同理。
        //
        //  但这些信息**在我们自己手里**：`AddLuck` 成功的那一刻，人就确定进队了，
        //  `slot` 也是刚算出来的。没有任何理由再回头扫描整个天下。
        //
        //  ── 存什么、怎么存 ───────────────────────────────────────────
        //  `_memberIds[slot]` = 该号位队员的 **unitID**（空串 = 空位）。
        //  **必须存 unitID，不能存对象引用**：IL2CPP 的包装器每桥接一次就新建一个对象，
        //  引用判等不可靠 —— 工程里已有此教训（早期用 `u == player` 判等失败，
        //  把「结伴而行」挂到了玩家自己身上）。unitID 是世界唯一的权威标识，
        //  且 `g.world.unit.GetUnit(unitID)` 是 O(1) 索引查找。
        //
        //  ── 什么时候重建 ─────────────────────────────────────────────
        //  `_cacheReady == false` ⇒ 下一次查询时**扫一次**全图建立：
        //    · 进世界 / 读档（`ResetMemberCache`，此时存档里的老队员才第一次被我们知道）
        //    · 缓存被判定为不可信（下面 Wipe 的几种情形）
        //  之后 invite / dismiss 全部 O(1)，**一次全图扫描都不做**。
        // ══════════════════════════════════════════════════════════

        /// <summary>号位 → 队员 unitID（空串 = 空位）。</summary>
        private static readonly string[] _memberIds = new string[MaxMembers];
        /// <summary>缓存是否已建立（false ⇒ 下次查询前要扫一次全图重建）。</summary>
        private static bool _cacheReady;
        /// <summary>重建次数（诊断用：正常情况下一个存档只该是 1）。</summary>
        private static int _cacheBuilds;

        /// <summary>取某单位的 unitID（读不到返回空串）。</summary>
        internal static string UnitIdOf(WorldUnitBase u)
        {
            return UnitIdentity.Id(u);
        }

        /// <summary>
        /// 按 unitID 拿回单位。**带回验**：`GetUnit` 在查不到时会按名字兜底，
        /// 不回验就可能把"随便一个同名的人"当成命中（UnitLookup 里同款教训）。
        /// 同时回验"他身上确实还带着这一槽的气运" —— 气运被别人摘掉时这里会返回 null，
        /// 调用方据此把该槽清空（这就是以前要靠周期心跳才能发现的情形）。
        /// </summary>
        private static WorldUnitBase ResolveSlot(int slot)
        {
            string id = _memberIds[slot];
            if (string.IsNullOrEmpty(id)) return null;
            WorldUnitBase u = null;
            try { u = g.world?.unit?.GetUnit(id); } catch { return null; }
            if (u == null) return null;
            string got = UnitIdOf(u);
            if (string.IsNullOrEmpty(got) || got != id) return null;      // 回验失败：不是他
            int luckId = SlotLuckId(slot, out _);
            if (luckId == 0) return u;                                     // 气运表没就绪时不做二次判
            return HasLuck(u, luckId) ? u : null;                          // 气运已不在 ⇒ 视为离队
        }

        /// <summary>把缓存建起来（**唯一的全图扫描点**，一个存档正常只跑一次）。</summary>
        private static void BuildMemberCache()
        {
            for (int s = 0; s < MaxMembers; s++) _memberIds[s] = "";
            try
            {
                var all = g.world?.unit?.GetUnits(true);
                int n = 0;
                try { n = all?.Count ?? 0; } catch { n = 0; }
                if (all == null) return;                   // 世界没就绪 ⇒ 保持 !ready，下次再试

                for (int i = 0; i < n; i++)
                {
                    WorldUnitBase u = null;
                    try { u = all[i]; } catch { continue; }
                    if (u == null || IsPlayer(u)) continue;
                    for (int s = 0; s < MaxMembers; s++)
                    {
                        int id = SlotLuckId(s, out _);
                        if (id == 0 || !HasLuck(u, id)) continue;
                        // 同槽多人（老版本脏数据）⇒ 保留先扫到的那个，其余交给
                        // PurgeSlotExcept / 清理逻辑处理，并把情况记进日志。
                        if (!string.IsNullOrEmpty(_memberIds[s]))
                        {
                            ModMain.P("[team] 缓存重建：号位 " + (s + 1) + " 有多个持有者（"
                                      + NameOf(u, "?") + " 与已记录的 " + _memberIds[s]
                                      + "），保留前者，多余的请用 dismiss 清掉");
                            continue;
                        }
                        _memberIds[s] = UnitIdOf(u);
                    }
                }
                _cacheReady = true;
                _cacheBuilds++;
                ModMain.P("[team] 队员缓存已建立（第 " + _cacheBuilds + " 次；全图 " + n + " 单位）：1号="
                          + Show(_memberIds[0]) + " 2号=" + Show(_memberIds[1]) + " 3号=" + Show(_memberIds[2]));
            }
            catch (Exception e)
            {
                ModMain.P("[team] 队员缓存重建失败：" + e.Message);
            }
        }

        private static string Show(string unitId)
        {
            if (string.IsNullOrEmpty(unitId)) return "(空)";
            try
            {
                var u = g.world?.unit?.GetUnit(unitId);
                string nm = NameOf(u, null);
                if (!string.IsNullOrEmpty(nm)) return nm;
            }
            catch { }
            return unitId;
        }

        /// <summary>确保缓存可用（必要时重建一次）。</summary>
        private static void EnsureCache()
        {
            if (_cacheReady) return;
            BuildMemberCache();
        }

        /// <summary>进世界 / 读档时调用：丢掉上一个存档的缓存，下次查询时重建。</summary>
        internal static void ResetMemberCache()
        {
            for (int s = 0; s < MaxMembers; s++) _memberIds[s] = "";
            _cacheReady = false;
        }

        /// <summary>
        /// 有人挂了号位气运 ⇒ 自动登记进缓存。
        /// 由 `AddLuck` 出口调用，因此**任何**挂气运的路径（invite / 探针 / 未来新入口）
        /// 都不会漏登记。luckId 不在三个号位里时什么都不做。
        /// </summary>
        private static void SyncCacheOnAdd(WorldUnitBase u, int luckId)
        {
            try
            {
                if (u == null || luckId == 0) return;
                if (IsPlayer(u)) return;                      // 玩家侧「组队中」不算队员
                string id = UnitIdOf(u);
                if (string.IsNullOrEmpty(id)) return;
                for (int s = 0; s < MaxMembers; s++)
                {
                    if (SlotLuckId(s, out _) != luckId) continue;
                    if (_memberIds[s] != id)
                    {
                        ModMain.P("[team] 缓存登记：" + NameOf(u, "?") + " → " + (s + 1) + " 号位");
                        _memberIds[s] = id;
                    }
                    _cacheReady = true;
                    return;
                }
            }
            catch { }
        }

        /// <summary>写入/清空某槽的缓存（invite / dismiss 成功后调用）。</summary>
        internal static void SetSlotMember(int slot, WorldUnitBase u)
        {
            if (slot < 0 || slot >= MaxMembers) return;
            _memberIds[slot] = u == null ? "" : UnitIdOf(u);
        }

        /// <summary>某个槽位现在挂着几个人（正常 ≤ 1）。走缓存，O(1)。</summary>
        private static List<WorldUnitBase> SlotHolders(int slot)
        {
            var r = new List<WorldUnitBase>();
            if (slot < 0 || slot >= MaxMembers) return r;
            EnsureCache();
            var u = ResolveSlot(slot);
            if (u != null) { r.Add(u); return r; }
            // 缓存说有人但读不到了（被外部摘掉 / 换档）⇒ 清空该槽。
            // 注意：**不在这里扫全图** —— 重建只在进世界/读档时发生，
            // 否则又回到"每问一次扫一次"的老路上。
            if (!string.IsNullOrEmpty(_memberIds[slot]))
            {
                _memberIds[slot] = "";
            }
            return r;
        }

        /// <summary>该单位占着哪个号位。-1 = 不在队里。</summary>
        internal static int SlotOf(WorldUnitBase u)
        {
            if (u == null) return -1;
            // 先查缓存（O(1)）；缓存没命中且还没建立过 ⇒ 建一次再看。
            string id = UnitIdOf(u);
            if (!string.IsNullOrEmpty(id))
            {
                EnsureCache();
                for (int s = 0; s < MaxMembers; s++)
                    if (_memberIds[s] == id) return s;
            }
            // 兜底：直接问这个人身上有没有号位气运（只看单个单位，不扫全图）。
            // 用途：调用方传进来一个我们缓存里没有、但他确实挂着气运的人
            // （例如别的存档残留 / 手动挂的）——这时要能如实回答。
            for (int s = 0; s < MaxMembers; s++)
            {
                int lid = SlotLuckId(s, out _);
                if (lid != 0 && HasLuck(u, lid)) return s;
            }
            return -1;
        }

        /// <summary>是否已在队里（任意号位）。给 `ToolExecutor` 判「别重复弹窗」用。</summary>
        internal static bool HoldsAnyMateLuck(WorldUnitBase u)
        {
            return SlotOf(u) >= 0;
        }

        /// <summary>
        /// 一次全图扫描，把每个号位的持有者同时收齐。
        ///
        /// ★ 为什么不是每槽各扫一遍（10-19 卡顿治理）★
        ///   `AllNpcUnits()` = `GetUnits(true)`，单次 ~100ms / 1500+ 个单位
        ///   （实测数据见 `game/RelationNetwork.cs` 的同格扫描区块）。
        ///   而 `TeamTool.Tick` 每 5s 就要一份队员名单 —— 三个号位各扫一次就是 300ms/5s。
        ///   合成一次扫描后，`HasLuck` 只是遍历单个单位的 `allLuck`，代价可忽略。
        /// </summary>
        private static List<WorldUnitBase>[] GatherSlots()
        {
            // ★ 10-19 改：走队员缓存（O(1)/槽），**不再全图扫描** ★
            //   旧实现是 `foreach (AllNpcUnits())` 判每个人身上有没有号位气运 ——
            //   单次 ~100ms / 1556 单位，而它被 invite/dismiss/Tick 反复调用。
            //   现在只有 `BuildMemberCache`（进世界/读档时一次）会遍历全图。
            var slots = new List<WorldUnitBase>[MaxMembers];
            for (int s = 0; s < MaxMembers; s++)
                slots[s] = SlotHolders(s);
            return slots;
        }

        /// <summary>当前队伍成员（按号位排序，每槽取第一人）。dupSlots = 有几个槽挂了多人（脏数据）。</summary>
        internal static List<WorldUnitBase> Members(out int dupSlots)
        {
            dupSlots = 0;
            var slots = GatherSlots();
            var list = new List<WorldUnitBase>();
            for (int s = 0; s < MaxMembers; s++)
            {
                if (slots[s].Count > 1) dupSlots++;
                if (slots[s].Count > 0) list.Add(slots[s][0]);
            }
            return list;
        }

        /// <summary>队里**所有**挂着号位气运的人（含同槽脏数据）。解散时必须用它。</summary>
        private static List<WorldUnitBase> AllHolders()
        {
            // 解散必须清**全部**持有者。缓存每槽最多记一人，
            // 所以这里额外把"同槽脏数据的其它持有者"也带上 —— 但只有在本槽缓存
            // 命中的前提下才去查（避免又变成无条件全图扫描）。
            var r = new List<WorldUnitBase>();
            EnsureCache();
            for (int s = 0; s < MaxMembers; s++)
            {
                var u = ResolveSlot(s);
                if (u != null) { r.Add(u); continue; }
                if (!string.IsNullOrEmpty(_memberIds[s])) _memberIds[s] = "";
            }
            return r;
        }

        /// <summary>把某人身上**所有**号位气运都摘掉（正常只有一枚）。</summary>
        internal static bool RemoveAllSlotLucks(WorldUnitBase u, out string err)
        {
            err = null;
            bool any = false;
            for (int s = 0; s < MaxMembers; s++)
            {
                int id = SlotLuckId(s, out string idErr);
                if (id == 0)
                {
                    if (err == null) err = idErr;
                    continue;
                }
                if (!HasLuck(u, id)) continue;
                any = true;
                string e;
                if (!RemoveLuck(u, id, out e) && err == null) err = e;
            }
            if (!any && err == null) err = "该单位身上没有队友标记气运";
            return err == null;
        }

        /// <summary>
        /// 清掉某槽里除 `keep` 之外的持有者，返回清掉的人数。
        ///
        /// 为什么需要：老版本三个槽共用一枚气运（`MID&51`），真机实测留下
        /// **5 个持有者**。这枚气运现在是 1 号位 —— 不清掉的话这一槽的候选集
        /// 又变成"多人里随机抽一个"，重复队友立刻复发。
        /// 判据：刚被邀请的这个人**定义上**就是现任队员，同槽的其它人只能是残留。
        /// </summary>
        private static int PurgeSlotExcept(int slot, WorldUnitBase keep)
        {
            // ★ 不再扫全图（10-19 21:xx 用户拍板）★
            //   旧实现 `FindUnitsWithLuck(id)` = 全图扫描（~100ms/1556 单位），
            //   为的是清"同一槽里的**其它**持有者"（老版本共用一个气运留下的脏数据）。
            //
            //   但现在这条路已经不需要了：
            //   ① `AddLuck` 出口自动登记缓存，`invite` 每次只挂一个人
            //      ⇒ 正常路径下"同槽多人"**不可能产生**；
            //   ② 万一真有脏数据（旧存档遗留），`BuildMemberCache` 在进世界那一次
            //      全图扫描时就会**发现并打日志报出来**：
            //        「缓存重建：号位 N 有多个持有者（X 与 Y），保留前者…」
            //      也就是说这个异常本来就有检测点，不需要每次 invite 再花 100ms 去防；
            //   ③ 真要清理，`dismiss` / 探针的 `clear` 都能定向清到位。
            //
            //   本方法现在只做一件事：若该槽缓存里记的人**不是**刚邀请的这位
            //   （正常不会发生），把他的气运摘掉 —— 只看这一个人，O(1)。
            int id = SlotLuckId(slot, out _);
            if (id == 0 || keep == null) return 0;

            string keepId = UnitIdOf(keep);
            string cachedId = _memberIds[slot];
            if (string.IsNullOrEmpty(cachedId) || cachedId == keepId) return 0;

            // 缓存里记的是另一个人：把他身上的这一槽气运摘掉（他不该占着这一槽了）
            WorldUnitBase stale = null;
            try { stale = g.world?.unit?.GetUnit(cachedId); } catch { }
            if (stale == null || IsSameUnit(stale, keep))
            {
                _memberIds[slot] = "";
                return 0;
            }
            string e;
            if (RemoveLuck(stale, id, out e))
            {
                ModMain.P("[team] 清掉同槽旧队员：" + NameOf(stale, "?") + " 身上的 " + (slot + 1) + " 号位气运");
                _memberIds[slot] = "";
                return 1;
            }
            ModMain.P("[team] 清理同槽旧队员失败（" + NameOf(stale, "?") + "）：" + e);
            return 0;
        }

        /// <summary>同一个人的判定（引用判等在 IL2CPP 包装器上不可靠，比 unitID）。</summary>
        private static bool IsSameUnit(WorldUnitBase a, WorldUnitBase b)
        {
            if (a == null || b == null) return false;
            try
            {
                string x = null, y = null;
                try { x = a.data?.unitData?.unitID; } catch { }
                try { y = b.data?.unitData?.unitID; } catch { }
                if (!string.IsNullOrEmpty(x) && !string.IsNullOrEmpty(y)) return x == y;
            }
            catch { }
            return a.Pointer == b.Pointer;
        }

        /// <summary>
        /// 一次性迁移（标记文件保证只跑一次）：清空所有旧版队友标记。
        ///
        /// 为什么只能清空、不能"挑出真的 3 个"：老版本三个人共用**同一枚**气运，
        /// 气运里没有任何字段记录"这是哪一槽"。真机留下的 5 个持有者里谁是现任、
        /// 谁是上个存档的残留，从气运本身**无从判断** —— 挑错会把陌生人带进副本。
        /// 所以宁可清空让用户重邀（3 次 invite，代价很小），也不猜。
        /// </summary>
        private static bool _slotsMigrated;
        private static void EnsureSlotsMigrated()
        {
            if (_slotsMigrated) return;
            _slotsMigrated = true;
            try
            {
                string marker = null;
                try { marker = Path.Combine(ModPaths.DiagDir ?? ".", "_team_slots_v2.migrated"); } catch { }
                if (marker != null && File.Exists(marker)) return;

                int stripped = 0;
                var names = new List<string>();
                foreach (var u in AllNpcUnits())
                {
                    for (int s = 0; s < MaxMembers; s++)
                    {
                        int id = SlotLuckId(s, out _);
                        if (id == 0 || !HasLuck(u, id)) continue;
                        string e;
                        if (RemoveLuck(u, id, out e))
                        {
                            stripped++;
                            string nm = NameOf(u, null);
                            if (!string.IsNullOrEmpty(nm) && !names.Contains(nm)) names.Add(nm);
                        }
                        else ModMain.P("[team] 迁移清理失败（" + NameOf(u, "?") + "）：" + e);
                    }
                }

                try
                {
                    if (marker != null)
                        File.WriteAllText(marker,
                            "version=2\nslots=" + MaxMembers + "\nstripped=" + stripped +
                            "\nunits=" + string.Join(",", names.ToArray()) + "\n");
                }
                catch { }

                ModMain.P("[team] 槽位迁移完成（v2 一槽一枚气运）：清掉旧版队友标记 " + stripped
                          + " 个" + (stripped > 0 ? "（之前挂过的人：" + string.Join("、", names.ToArray())
                                                   + "）—— 请重新邀请队友" : ""));
            }
            catch (Exception ex)
            {
                try { ModMain.P("[team] 槽位迁移失败：" + ex.Message); } catch { }
            }
        }

        private static int MateLuckId2(out int id)
        {
            if (_hurtLuckId != 0) { id = _hurtLuckId; return id; }
            _hurtLuckId = ResolveLuckById(ModIds.LuckHurt);
            id = _hurtLuckId;
            return id;
        }

        /// <summary>
        /// 「内外重伤」气运 ID（缓存）。0 = 表里没找到。
        ///
        /// 10-19 起对外暴露：`TeamBattleFollowup` 要在战后判定队友是否重伤，
        /// 邀请入口也要用它拦人。不要在调用方按名字反查 —— 同名气运可能命中别人的。
        /// </summary>
        internal static int HurtLuckId()
        {
            MateLuckId2(out int id);
            return id;
        }

        /// <summary>
        /// 玩家侧「组队中」气运 ID（缓存）。0 = 查不到。
        ///
        /// 查不到时**不报错**：这条纯展示，缺了组队照常能用（副本出人靠队友身上的气运）。
        /// 所以调用方都当"可选"处理，绝不因它失败而中断流程。
        /// </summary>
        private static int SelfLuckId()
        {
            if (_selfLuckId != 0) return _selfLuckId;
            _selfLuckId = ResolveLuckById(ModIds.LuckSelf);
            return _selfLuckId;
        }

        /// <summary>
        /// 气运 ID 解析：**只按本 mod 登记的偏移查**（`knownId` 由 `ModIds` 给出）。
        ///
        /// 11-16 删掉按名兜底 —— 完整理由见 `SlotLuckId` 的头注（救不了、会挂错人的气运、
        /// 而且恰好在"表没加载好"时烧 75ms）。这里只留一件事：问表有没有这枚 id。
        ///
        /// 返回 0 的两种含义与 `SlotLuckId` 一致：
        ///   · 表还没加载 ⇒ 临时，调用方下次会再进来；
        ///   · 表里有行却缺这一枚 ⇒ 确定缺失（本函数的调用方都是"可选/纯展示"，不阻塞流程）。
        /// </summary>
        private static int ResolveLuckById(int knownId)
        {
            if (knownId == 0) return 0;
            try
            {
                return g.conf.roleCreateFeature.GetItem(knownId) != null ? knownId : 0;
            }
            catch { return 0; }
        }

        // ══════════════════════════════════════════════════════════
        //  气运读写
        // ══════════════════════════════════════════════════════════

        internal static bool HasLuck(WorldUnitBase u, int luckId)
        {
            try
            {
                var luck = u.GetLuck(luckId);
                return luck != null;
            }
            catch { return false; }
        }

        /// <summary>
        /// 挂气运。**判据是「面板能不能看见」**，不是「GetLuck 能不能读回来」。
        ///
        /// ★ 10-18 面板事故（必读，别把判据改回去）★
        ///   旧版三条策略都以 `HasLuck(u, id)`（= `u.GetLuck(id) != null`）当成功判据，
        ///   而 `CreateLuck(data)` **只建运行时对象**，不写 `propertyData.addLuck`。
        ///   于是：GetLuck 读得到（自己写的自己读，自证）→ 判成功；
        ///         可 NPC 面板的「后天气运」读的是 `addLuck` 列表 → **一片空白**；
        ///         而且不进存档：读一次世界就没了（真机日志里"挂 3 个 → 重进世界 → 又挂 3 个"）。
        ///   玩家原话："我这边没看到，没从npc面板看到呀"。这个 bug 存在了好几轮才被发现，
        ///   根因就是**判据选错了**，不是 API 用错了。
        ///
        /// 现在的口径（10-19 续查二轮定案）：**两层都真进才算成功**——
        ///   运行时层 GetLuck 命中 且 存档/面板层 addLuck 含该 id。
        /// 挂运主路径 = 引擎原生序列（见下）；直塞 addLuck 保留为兜底④。
        ///   ★ 10-19 续查二轮 Q9'：引擎原生挂运 = UnitActionLuckAdd::OnCreate @0x1838DE970
        ///   的三步序列【raw 定案】：①AddAddLuck ②CreateLuck（内部 vtable+0x188 Init
        ///   填 luckData.id）③NewCreateLuck —— 与引擎唯一的差别是"就地执行，不造 Action"。
        ///   E13 探针"先 CreateLuckInID 再取 lk.luckData"是 hollow 根因，别回去。
        /// 老三策略的兜底（直塞 addLuck）保留为 ④。
        /// </summary>
        internal static bool AddLuck(WorldUnitBase u, int luckId, out string err)
        {
            err = null;

            // ①存档/面板层（换局材质化回运行时层的家）
            try
            {
                var data = NewLuckData(luckId);
                u.data.unitData.propertyData.AddAddLuck(data);
            }
            catch (Exception e1) { err = "AddAddLuck:" + e1.Message; }

            // ②运行时层（守卫幂等：GetLuck 命中即跳过 = 引擎"重复同 id 是 Del 重挂"的平替）
            object luckObj = null;
            try
            {
                if (u.GetLuck(luckId) == null)
                {
                    var data = NewLuckData(luckId);
                    luckObj = u.CreateLuck(data);
                }
            }
            catch (Exception e2) { err = (err == null ? "" : err + " / ") + "CreateLuck:" + e2.Message; }

            // ③NewCreateLuck（引擎第三步）。托管签名返回值可能是 void ⇒ 拿不到对象时
            //   按"allLuck 末位"取（CreateLuck = 无条件 List.Add，刚建的必在末位）。
            try
            {
                object lk = luckObj;
                if (lk == null)
                {
                    var all = ((dynamic)u).allLuck;
                    int n = (int)((dynamic)all).Count;
                    if (n > 0) lk = ((dynamic)all)[n - 1];
                }
                if (lk != null)
                {
                    try { ((dynamic)lk).NewCreateLuck(0); }          // 原生 raw：this, edx=0
                    catch { try { ((dynamic)lk).NewCreateLuck(); } catch { } }
                }
            }
            catch (Exception e3) { err = (err == null ? "" : err + " / ") + "NewCreateLuck:" + e3.Message; }

            // ④兜底：面板层仍未进就直接塞（老 #3，封条别拆）
            try
            {
                if (!InAddLuck(u, luckId))
                {
                    var data = NewLuckData(luckId);
                    var arr = u.data.unitData.propertyData.addLuck;
                    if (arr != null) arr.Add(data);
                }
            }
            catch (Exception e4) { err = (err == null ? "" : err + " / ") + "addLuck.Add:" + e4.Message; }

            // ★ 两层都真进才算"挂上"（收紧口径）★
            //   10-19 用户质问的真相坑：面板明明有人带气运（张票/方成和 —— 前几轮旧挂残留，
            //   只进过存档层），进副本却没人 —— 三策略的旧判定只看 InAddLuck（存档层），
            //   运行时层是否真进 GetLuck 从未被验证过。
            bool runtimeOk = false, panelOk = false;
            try { runtimeOk = u.GetLuck(luckId) != null; } catch { }
            panelOk = InAddLuck(u, luckId);
            if (runtimeOk && panelOk)
            {
                // ★ 自动同步队员缓存（10-19）★
                //   挂在**这里**而不是各调用方的成功路径上：探针
                //   （`TeamMultiProbe` 的 give/givemap/pair）也直接调 `AddLuck` 挂气运，
                //   它走的是"按位置挑人"而不是 team.invite。若只在 invite 里写缓存，
                //   探针挂完的人不会进缓存 ⇒ `Members()` 返回 0 ⇒ 容器注入器判定
                //   "没组队"直接放行 ⇒ 副本 0 人，而气运明明挂着。
                //   放在 AddLuck 出口 ⇒ 任何路径挂上气运都会登记，不可能漏。
                SyncCacheOnAdd(u, luckId);
                return true;
            }

            // ★ 动态气运陷阱（10-19 续查 Q9'' 定案链）：type=2 的气运经 u.CreateLuck
            //   进 allLuck 后 **luckData=null**（WorldUnitLuckDyn ctor 不填）⇒ GetLuck
            //   按内嵌 luckData.id 值相等匹配永远失败 ⇒ 本局 feature_1_x 条件段看不见
            //   ⇒ 三槽全空 ⇒ 副本 0 人。10-18 晚"成功"靠的是 addLuck 存档 +
            //   换局材质化（EnterWorldFixData）——即同路径的**跨局**效果。
            //   此处给出**专门错误**而不是笼统"两层验证失败"。
            if (!runtimeOk && panelOk && IsDataNullTrap(u, luckId))
            {
                err = "动态气运陷阱：对象已进 allLuck 但 luckData=null（GetLuck 匹配不上）"
                    + "｜本局条件组看不见，换局材质化后生效（10-18 晚成功即此机理）"
                    + (string.IsNullOrEmpty(err) ? "" : "（原报错: " + err + "）");
                return false;
            }
            err = "两层验证失败：运行时层(GetLuck)=" + (runtimeOk ? "✅" : "❌")
                + "  存档/面板层(addLuck)=" + (panelOk ? "✅" : "❌")
                + (string.IsNullOrEmpty(err) ? "" : "（原报错: " + err + "）");
            return false;
        }

        /// <summary>★动态气运陷阱★判定（只读）：面板层已有该 id、且 GetLuck 匹配不上、
        /// 且 allLuck 里存在 luckData=null 的对象（无法读它的 id，只能按"面板已有+
        /// 运行时不可见+场上存在空 data 对象"三角判定）——三条件满足即高置信认作
        /// "刚创建的 type=2 动态气运未物化"。</summary>
        internal static bool IsDataNullTrap(WorldUnitBase u, int luckId)
        {
            try
            {
                if (u.GetLuck(luckId) != null) return false;
                var all = ((dynamic)u).allLuck;
                int n = (int)((dynamic)all).Count;
                int nullData = 0;
                for (int i = 0; i < n; i++)
                {
                    object el = null, ld = null;
                    try { el = ((dynamic)all)[i]; } catch { continue; }
                    try { ld = ((dynamic)el).luckData; } catch { }
                    if (ld == null) nullData++;
                }
                return nullData > 0;
            }
            catch { return false; }
        }

        /// <summary>单枚气运的**分层诊断**（只读不改）：给探针逐人打印用。
        /// "运行时=✅ 面板=✅" —— feature_1_X 条件读运行时层；面板/存档读面板层。
        /// ★ 10-19 追加 allLuck 直扫：type=2 动态气运的对象**可以在 allLuck 里但
        ///   luckData=null**（E13② WorldUnitLuckDyn ctor 不填；GetLuck 匹配的是
        ///   内嵌 luckData.id 值相等）⇒ "GetLuck=❌ 但对象在场且永远匹配不上"的
        ///   陷阱必须显形，否则又是假成功。★
        /// </summary>
        internal static string LuckLayerState(WorldUnitBase u, int luckId)
        {
            bool r = false, p = false;
            try { r = u.GetLuck(luckId) != null; } catch { }
            try { p = InAddLuck(u, luckId); } catch { }
            string scan = "";
            try
            {
                var all = ((dynamic)u).allLuck;
                int n = (int)((dynamic)all).Count;
                var ids = new System.Collections.Generic.List<string>();
                int nullData = 0, hitIdx = -1;
                for (int i = 0; i < n; i++)
                {
                    object el = null;
                    try { el = ((dynamic)all)[i]; } catch { continue; }
                    int id = int.MinValue;
                    object ld = null;
                    try { ld = ((dynamic)el).luckData; } catch { }
                    try { if (ld != null) id = (int)((dynamic)ld).id; } catch { }
                    if (ld == null) nullData++;
                    if (id == luckId) hitIdx = i;
                    if (ids.Count < 10) ids.Add(id == int.MinValue ? "null-data" : ("id=" + id));
                }
                scan = "  allLuck共" + n + "枚[" + string.Join(",", ids.ToArray()) + "]"
                     + (nullData > 0 ? " （" + nullData + " 枚 luckData=null：动态气运未物化）" : "")
                     + (hitIdx >= 0 ? " ★id 第 " + hitIdx + " 枚命中★" : "");
            }
            catch (Exception e) { scan = "  allLuck 扫描异常：" + e.GetType().Name + " " + e.Message; }
            return "运行时(GetLuck)=" + (r ? "✅" : "❌") + "  面板(addLuck)=" + (p ? "✅" : "❌") + scan;
        }

        /// <summary>造一枚 LuckData。`duration=-1` = 永久（队友标记不该自然消失）。</summary>
        private static DataUnit.LuckData NewLuckData(int luckId)
        {
            return new DataUnit.LuckData
            {
                id = luckId,
                duration = -1,
                createTime = 0
            };
        }

        /// <summary>
        /// 气运是否真的进了 `propertyData.addLuck` —— **面板可见性的唯一判据**。
        ///
        /// 与 `HasLuck`（`GetLuck`，只看运行时对象）的区别见 `AddLuck` 的 ★ 段。
        /// 任一环节读不到都当"没进"，宁可报失败让人看见，也不要假成功。
        /// </summary>
        internal static bool InAddLuck(WorldUnitBase u, int luckId)
        {
            try
            {
                var arr = u.data.unitData.propertyData.addLuck;
                if (arr == null) return false;
                int n = 0;
                try { n = arr.Count; } catch { return false; }
                for (int i = 0; i < n; i++)
                {
                    object ld = null;
                    try { ld = arr[i]; } catch { continue; }
                    if (ld == null) continue;
                    int id = 0;
                    try { id = (int)((dynamic)ld).id; } catch { continue; }
                    if (id == luckId) return true;
                }
            }
            catch { }
            return false;
        }

        /// <summary>
        /// 摘气运。用 `DestroyLuck(WorldUnitLuckBase)` —— 注意**没有** `RemoveLuck` 这个 API
        /// （早期按猜的名字调，编译期就挂）。所以先 `GetLuck` 拿到对象再销毁。
        /// </summary>
        internal static bool RemoveLuck(WorldUnitBase u, int luckId, out string err)
        {
            err = null;
            try
            {
                // ★ 摘气运后同步清缓存（10-19）：否则缓存会一直指向一个已经离队的人，
                //   表现为"请离了但名单里还有他"。清空后 `ResolveSlot` 会如实返回 null。
                try
                {
                    string uid = UnitIdOf(u);
                    if (!string.IsNullOrEmpty(uid))
                    {
                        for (int s = 0; s < MaxMembers; s++)
                        {
                            if (SlotLuckId(s, out _) == luckId && _memberIds[s] == uid)
                            {
                                _memberIds[s] = "";
                                break;
                            }
                        }
                    }
                }
                catch { }
                // ★ 两层都要清（10-19 反汇编定案）★
                //   `DestroyLuck` 只删**运行时层**（allLuck，GetLuck 读的那份）；
                //   `propertyData.addLuck` 是**存档/面板层**，`DelAddLuck(id)` 才管它。
                //   只删运行时层 ⇒ 面板上气运还在，一读档又被物化回来（"删不掉"的真相）。
                bool runtime = true, panel = true;
                try
                {
                    var lk = u.GetLuck(luckId);
                    if (lk != null) u.DestroyLuck(lk);
                    runtime = !HasLuck(u, luckId);
                }
                catch (Exception e1) { err = "DestroyLuck:" + e1.Message; runtime = false; }
                try
                {
                    if (InAddLuck(u, luckId))
                        u.data.unitData.propertyData.DelAddLuck(luckId);
                    panel = !InAddLuck(u, luckId);
                }
                catch (Exception e2) { err = (err == null ? "" : err + " / ") + "DelAddLuck:" + e2.Message; panel = false; }

                if (!runtime) return false;
                return panel;   // 运行时层是功能判据，面板层只影响显示——但两层都报
            }
            catch (Exception e) { err = e.Message; return false; }
        }

        // ══════════════════════════════════════════════════════════
        //  队员清单
        // ══════════════════════════════════════════════════════════

        /// <summary>全图所有 NPC（不含玩家）。一次扫描，供多个查询复用。</summary>
        private static List<WorldUnitBase> AllNpcUnits()
        {
            var r = new List<WorldUnitBase>();
            // `g.world.unit.GetUnits(true)` —— 探针实测可用的全图遍历入口
            // （`g.world.worldUnitList` 不存在，是想象出来的名字；`WorldMgr.unit` 才是持有者）
            Il2CppSystem.Collections.Generic.List<WorldUnitBase> all = null;
            try { all = g.world?.unit?.GetUnits(true); } catch { }
            if (all == null) return r;

            int n = 0;
            try { n = all.Count; } catch { }
            for (int i = 0; i < n; i++)
            {
                WorldUnitBase u = null;
                try { u = all[i]; } catch { continue; }
                if (u == null) continue;
                if (IsPlayer(u)) continue;          // 玩家不会是"队友"
                r.Add(u);
            }
            return r;
        }

        /// <summary>全图扫描：谁身上有指定的气运。</summary>
        internal static List<WorldUnitBase> FindUnitsWithLuck(int luckId)
        {
            var result = new List<WorldUnitBase>();
            if (luckId == 0) return result;
            foreach (var u in AllNpcUnits())
            {
                if (HasLuck(u, luckId)) result.Add(u);
            }
            return result;
        }

        /// <summary>
        /// 当前队员名字（走缓存，O(1)/槽，**不扫全图**）。给探针做"清运后复核"用。
        /// </summary>
        internal static List<string> CachedMembers()
        {
            var r = new List<string>();
            EnsureCache();
            for (int s = 0; s < MaxMembers; s++)
            {
                var u = ResolveSlot(s);
                if (u == null) { if (!string.IsNullOrEmpty(_memberIds[s])) _memberIds[s] = ""; continue; }
                string nm = NameOf(u, null);
                r.Add((s + 1) + "号:" + (string.IsNullOrEmpty(nm) ? "?" : nm));
            }
            return r;
        }

        /// <summary>队员名字数组（按号位排序，给 status / invite 的返回值用）。</summary>
        internal static List<string> ListMates()
        {
            var names = new List<string>();
            foreach (var u in Members(out _))
            {
                string nm = NameOf(u, null);
                if (!string.IsNullOrEmpty(nm)) names.Add(nm);
            }
            return names;
        }

        // ══════════════════════════════════════════════════════════
        //  小工具
        // ══════════════════════════════════════════════════════════

        internal static bool IsPlayer(WorldUnitBase u)
        {
            if (u == null) return false;
            try
            {
                var p = g.world?.playerUnit;
                if (p == null) return false;
                // 判据 = unitID 相等（引用判等在 IL2CPP 包装器上不可靠 —— 实测踩过：
                // 早期用 `u == player` 判等，结果在探针里把玩家自己也挂上了气运）
                string a = null, b = null;
                try { a = u.data?.unitData?.unitID; } catch { }
                try { b = p.data?.unitData?.unitID; } catch { }
                if (!string.IsNullOrEmpty(a) && !string.IsNullOrEmpty(b)) return a == b;
                return u.Pointer == p.Pointer;
            }
            catch { return false; }
        }

        internal static string NameOf(WorldUnitBase u, string fallback)
        {
            return UnitIdentity.Name(u, fallback);
        }

        /// <summary>带附加数据的失败（`ToolResult.Fail` 是私有方法，本类用自己的）。</summary>

        /// <summary>解析动作的**对手方**（09-16）：`target` 缺省 = 玩家（老行为，逐字节不变）；
        /// 填了真名则解析成那个单位。解析不到 → `fail` 带明确原因。
        ///
        /// ★绝不静默落回玩家★：静默落回会造成"想对张三动手、结果打到玩家身上"这类**写错对象**，
        /// 比直接失败严重得多（`target` 是模型显式表达的意图，读不懂就如实拒绝）。
        /// `counterparty` 非空 = 对手方不是玩家 —— Python 渲染层据此改用第三人称措辞。
        ///
        /// 09-17：`target_id`（Python 从群成员身份表翻译来的精确 id）与 `target` **一起读**。
        /// 有 id 走 ②unitID精确，没有/失效回落按名 —— 见上面那段"谁给的 id"的说明。
        /// </summary>
        /// ══════════════════════════════════════════════════════════════════
        ///  team —— 组队 / 解散 / 查询（10-17 新增）
        ///
        /// 语义定案（用户口径）：
        ///   · **发起方默认是 NPC**，所以只需要一个参数「对象」——模型填**玩家名或 NPC 真名**。
        ///   · 实际的组队/解散动作在 C# 侧完成（挂/摘「结伴而行」气运），
        ///     副本内的出人由 `DungeonTrigger` 配置表负责。
        ///
        /// 三个 op：
        ///   invite  —— 邀请入队。弹自制确认窗（同意/不同意），玩家点「同意」才真的挂气运。
        ///   dismiss —— 解散/请离。同样弹窗确认；不填 target = 解散整队。
        ///   status  —— 只读查询，不弹窗。
        ///
        /// 为什么 `status` 不弹窗：查询是只读的，弹窗只会打扰玩家。
        /// 为什么 `invite` 必须弹窗：拉人入队会改存档状态，且玩家有拒绝权。
        /// ══════════════════════════════════════════════════════════════════
        internal static JObject Team(JObject args)
        {
            string op = (string)args["op"] ?? "";
            var wub = ToolArgs.BindActor(args, out string initiator, out JObject bindFail);
            if (bindFail != null) return bindFail;

            // 对象（缺省 = 玩家）。`target` 留空时 ResolveCounterparty 直接返回玩家单位，
            // 这是既有约定（economy_item / item_acquire 同款），组队复用。
            var counterparty = ToolArgs.ResolveCounterparty(args, out string cpName, out JObject cpFail);
            if (cpFail != null) return cpFail;

            string selfName = TeamTool.NameOf(wub, initiator);
            string otherName = string.IsNullOrEmpty(cpName) ? "玩家" : cpName;

            if (op == "status")
                return TeamTool.Execute(op, wub, counterparty, otherName, args);

            if (counterparty == null)
                return Fail("对象单位不可用（未找到「" + otherName + "」）");

            // 自己不能拉自己
            if (TeamTool.IsPlayer(wub) && TeamTool.IsPlayer(counterparty))
                return Fail("发起方与对象是同一个人（都是玩家）");

            // ★ 谁是"队员" —— 与 `TeamTool.Invite` 同一口径（那边注释有完整理由）★
            //   target 填玩家 → 我这个 NPC 跟你走 ⇒ 队员 = 发起方自己
            //   target 填 NPC  → 他跟你走        ⇒ 队员 = 那个 NPC
            bool targetIsPlayer = TeamTool.IsPlayer(counterparty);
            var mate = targetIsPlayer ? wub : counterparty;
            string mateName = TeamTool.NameOf(mate, targetIsPlayer ? selfName : otherName);

            // ── invite：弹确认窗，点「同意」才挂气运 ──────────────────
            if (op == "invite")
            {
                if (TeamTool.IsPlayer(mate))
                    return Fail("玩家不能作为队伍成员被带入副本（队友必须是 NPC）");

                // ★ 重伤未愈：连确认窗都不弹，直接如实拒绝 ★
                //   `TeamTool.Invite` 里还有一道同样的判据（工具被直接调用时也拦得住）。
                int hurtId = TeamTool.HurtLuckId();
                if (hurtId != 0 && TeamTool.HasLuck(mate, hurtId))
                    return Fail("「" + mateName + "」身负重伤，需静养，暂时无法同行");

                // 已在队伍里就不用弹窗了，直接如实回答（省玩家一次点击）。
                // 判的是**队员**而不是对象：target=玩家时队员是发起方自己。
                // 判"任意号位"—— 一槽一枚气运，只查 1 号位会漏（人会从 2/3 号位入队）。
                if (TeamTool.HoldsAnyMateLuck(mate))
                    return TeamTool.Execute(op, wub, counterparty, otherName, args);

                string text = targetIsPlayer
                    ? selfName + "想与你结伴同行，日后同闯秘境，可好？"
                    : selfName + "想让「" + mateName + "」一同随行，可好？";
                return NativeActionService.Claimed(wub, () => ShowDramaService.ShowConfirm(
                    ModIds.DramaTeamInviteBase,
                    g.world.playerUnit,      // 左位 = 玩家（立绘位约定，见 ShowDramaService 注释）
                    wub,                     // 右位 = 发起方 NPC
                    text,
                    "同行", "不了",
                    onOk: () => TeamTool.Execute(op, wub, counterparty, otherName, args),
                    okTip: null,
                    noTip: "玩家婉拒了「" + selfName + "」的同行邀请"));
            }

            // ── dismiss：弹确认窗，点「同意」才摘气运 ────────────────
            //   判据 = target 有没有点名一个具体 NPC（不能用 `who == null`：
            //   `ResolveCounterparty` 缺省返回**玩家**，永远不会给 null）
            {
                bool disbandAll = !(!string.IsNullOrEmpty((string)args["target"]) && !targetIsPlayer);
                string text = disbandAll
                    ? "要解散队伍、就此别过吗？"
                    : "要让「" + otherName + "」离队吗？";
                return NativeActionService.Claimed(wub, () => ShowDramaService.ShowConfirm(
                    ModIds.DramaTeamInviteBase,
                    g.world.playerUnit,
                    wub,
                    text,
                    disbandAll ? "就此别过" : "让他走", "再想想",
                    onOk: () => TeamTool.Execute(op, wub, counterparty, otherName, args),
                    okTip: null,
                    noTip: "玩家没有同意解散队伍"));
            }
        }
    }
}
