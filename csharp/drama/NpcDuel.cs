/// <summary>
/// NpcDuel —— 「NPC 打 NPC」的**观战**入口（10-26）。
///
/// ## 它解决什么
///
/// `world_ai_action(attack)` 原先只能打玩家（`new UnitActionRoleAttack(player)` 写死）。
/// 而"让 A 去打 B（两边都是 NPC）"在引擎里其实有两条完全不同的路：
///
/// | 走法 | 引擎分支 | 有没有场景 |
/// |---|---|---|
/// | `A.CreateAction(new UnitActionRoleAttack(B))` | `UnitActionRoleBattle::NPCToNPCAction` | ❌ 直接结算（无 IntoBattle） |
/// | 本类 | `DramaFunction::DramaUnitBattle` → `IntoBattle(null, 4001, …)` | ✅ 真场景，玩家旁观 |
///
/// 第一条第三方 mod 已在用（`types36_fulldll.txt:38463`），但它**没有画面**；
/// 玩家要的是"两个 NPC 在眼前打起来"，所以走第二条。
///
/// ## 引擎侧依据（逐条反编复核过，不是推测）
///
/// `DramaFunctionTool::OptionsFunction`@0x4634CC0
///   → `new DramaFunction().OptionsFunction(str, data)`@0x2268F60
///     ① `IsBlankString` 直接返回；② `str == <哨兵>` 直接返回（配表用 `"0"` 表示无）；
///     ③ `this._data(+0x10) = data`（空则 new 一个）；
///     ④ `str.Split('|')` → 逐条再 `Split('_')`；
///     ⑤ **按 `parts[0]` 字符串分派**（`String::op_Equality` 二分链），命中后
///        `DramaUnitBattle(this, parts)`@0x224F110。
///     ⇒ 传 `"dramaUnitBattle_1_2"` 得到的 `parts = ["dramaUnitBattle","1","2"]`，
///       与配表 14 条 `dramaUnitBattle_0_1_<dramaId>` 同一条分派路径。
///
/// `DramaFunction::DramaUnitBattle`@0x224F110（`private void DramaUnitBattle(string[] values)`）：
///
/// ```text
///   values.Length > 1 → values[1] → Int32.Parse → GetUnitInType(n)  = 左
///   values.Length > 2 → values[2] → Int32.Parse → GetUnitInType(n)  = 右
///   （两者非空且不相等才继续）
///   r15b = (左==playerUnit) || (右==playerUnit)
///   battleData = new WorldBattleData()
///   battleData.npcFightUnits(+0xD8) = DataStruct(左, 右)
///   values.Length > 3 ⇒ values[3] 写进 battleData.battleCustomData(+0x30)（dramaId，我们没有）
///   if (!r15b)                                  ← 两边都不是玩家
///       g.world.battle.IntoBattle(toUnit:null, dungeonID:0xFA1=4001, onInitCall:null, battleData)
///   else
///       UIMapBattlePre::InitData(…)              ← 战前窗（等玩家点）
/// ```
///
/// `DramaFunction::GetUnitInType(int)`@0x2253A90：`-1`=playerUnit、`0`=unitA(+0x10)、
/// **`1`=unitLeft(+0x18)**、**`2`=unitRight(+0x20)**、`10`=宗门那套、其余走 `ConfNpcCondition`。
/// `DramaFunctionData` 的字段偏移正好是 0x10/0x18/0x20 ⇒ 我们塞进去的就是它读到的。
///
/// ## 关键的时序事实：**开打是同步的**
///
/// `WorldBattleMgr::IntoBattle`@0x27FADA0 内部第 6 条指令就是
/// `movb $0x1,0x10(%rsi)`（`WorldBattleMgr.isBattle = true`，`IntoBattleInit`@0x27FA86F），
/// **然后**才 `SceneMgr::LoadSceneAsync`。所以 `OptionsFunction` 一返回，
/// `isBattle` 必然是真 —— 这就是下面"同步判有没有开打"的依据：**不是等一个时长，是读一个事实**。
///
/// ## 结果从哪来（不猜胜负）
///
/// 观战里玩家不是参战单位，`BattleOutcome` 那套"读页面原话"的判据在这里全是空的。
/// 所以只认两处事实，按优先级：
///   ① **战场单位** `SceneBattle.unit` 里那两个 `UnitCtrlBase.isDie`（引擎自己也是这么判的，
///      见 `SpecialBattle79::OnUnitDie`）—— `BattleSceneFacts`；
///   ② 战场读不到时退到**世界单位**的 `isDie`。
/// 两边都读不到 ⇒ 结果里**不出现** `winner`（字段缺失 ≠ 否定），渲染层说"胜负未定"。
///
/// ## 收口（不靠"猜多久打完"）
///
/// 三条独立证据，先到先算：
///   · `BattleEndFront` —— 游戏自己的"战斗即将结束"，抓快照；
///   · `BattleExit`     —— 战斗场景关闭；
///   · `isBattle` 由真翻假（`BattleFlowGate` 每 0.25s 采样的**边沿**，不是超时）。
/// 兜底只有 `DramaGate` 的既有过期（180s）——那是"玩家永远不回应"的通用底线，不是本类新造的定时器；
/// 那一路由 `DramaGate.OnResolved` 回调兜住，槽位被外力收掉时这里同步清 `_watch`。
///
/// ## 战后处置页（10-26 追加）：在战斗里**拦住**一次 `BattleEndMgr.BattleEnd`
///
/// 观战副本 4001 是 `type=79`，`WorldBattleData::InitData` 对它显式置 `isRealBattle=false`
/// ⇒ 这种战斗里单位**永远不会死**（`isDie` 恒 false、世界单位的 hp 也不回写），引擎自己
/// 也不会为它弹处置页。于是"打赢了怎么处置败者"这一步必须我们拦：
///
/// 1. `BattleEndHook.Prefix` 先照**原顺序**报信（`BattleOutcome.NoteBattleEnded` /
///    `DeferredActionQueue.NoteChainOver` / 那行日志），**然后**才问本类要不要拦
///    —— 顺序不能反：这一拍正是"战场单位还活着、还读得到 hp"的最后一刻；
/// 2. 本类在 `NoteBattleEnding` 里**当场做胜负快照**。不能等 `BattleEndFront`：那个事件
///    就在被我们拦住的这个方法体内发，拦住它 = 永远等不到它；
/// 3. 判出一个确定的败者、且败者是**世界 NPC**（不是玩家/null）⇒ 拦住：`Prefix` 返回 false。
///    这是干净的"整场还没结束"——反编 `SpecialBattle79::BattleEnd`@0x2BFDCD0 结尾是一条
///    **尾跳** `jmp 0x1828FA0D0`（= `BattleEndMgr::BattleEnd`），调用方在它之后什么都不做
///    （父代理 10-26 实测），所以跳过它没有"半结束"的中间态；
/// 4. 立刻 `DramaTool.OpenDrama(20403, …)` —— 原版处置页（败者求饶 + 五个选项）。正文由
///    `RoleLogLocal` 按关系/好感/宗门职位/性格选句，不是死文案（见 03 §8.8）；
/// 5. 玩家选完 / 把窗关掉之后**放行**：在既有的 0.25s `Tick` 里判，反射调回被拦下的那次
///    `BattleEnd`（它是**实例方法**，依据见 `NoteBattleEnding` 的注释）。
///
/// **无限等**（用户 10-26 拍板）：**不设超时** —— 玩家不选就一直拦着。
///
/// **fail-open 是铁律**：任何异常 / 处置页开不出来（`OpenDrama` 抛异常或返回 false）/
/// 开完下一拍仍看不到剧情窗 / 槽位被外力收掉 ⇒ **立刻放行**。宁可少弹一次页，
/// 也绝不能把玩家卡在一场结束不了的战斗里。
/// </summary>
using System;
using System.Collections.Generic;
using System.IO;
using System.Reflection;
using System.Text;
using Newtonsoft.Json.Linq;

namespace AgentLoopBridge
{
    internal static class NpcDuel
    {
        private const string Tag = "[NpcDuel] ";

        /// <summary>观战用的剧情函数：左 = `unitLeft`（槽 1），右 = `unitRight`（槽 2）。
        /// 配表里另有 `dramaUnitBattle_1_2_<dramaId>` 两条（5015903 / 5016404），同一处理体。</summary>
        private const string WatchFunction = "dramaUnitBattle_1_2";

        /// <summary>挂起槽位的过期上限（秒）。只防"玩家永远不回应"那种僵死，不参与正常收口。</summary>
        private const int ExpireSeconds = 180;

        /// <summary>战后处置页（原版 `DramaDialogue` 20403：败者求饶 + 五个选项）。</summary>
        private const int VerdictPageId = 20403;

        // 注：剧情页的类名（`UIDramaDialogue`）**不在这里定义**。
        // 按名查窗的读法只有一份，住在 `WorldStateFacts.DramaPageVisible()`
        // （`test_drama_gate_contract` 钉住了"第三处各写各的就会分叉"这条历史教训）。

        // ── 20403 的五个选项（配表 `ConfDramaOptionsItem`，10-26 定案）──
        //   204032 东西不错我收下了，放你一马    function=0
        //   204033 击杀此人                      function=sound_sharen（**只是一个音效**）
        //   204035 收下物品，羞辱此人            function=0
        //   204034 算了，东西不要了，你走吧      function=0
        //   204036 （隐藏，需持有 110 类物品）   function=itemSelectNotCost_0_1_1_110_0_0|notNextDialogue
        //   ⇒ 配表里除了音效与 204036 的选物，**没有任何"击杀/羞辱/收物品"的效果**：
        //     原版那些副作用是攻击动作自己的 C# 回调干的（见 docs/archive/npc-vs-npc/03 §8.8）。
        //   ⇒ 10-26 起这四支的**真实效果由本类自己落**（用户口径，别再退回"只记账"）：
        //     · 204032/204035：开页时先按口径挑好"他会献什么"，点下去才从败者背包转到玩家背包；
        //     · 204035 另加"羞辱"：败者对玩家 `AddHate(+10)` + `SetIntim(好感-20)`；
        //     · 204033：`WorldUnitBase.Die()`，并**事后回读 `isDie`** 留证据；
        //     · 204034：真的什么都不做（只把结局记成 `spare`，不是"未落账"）。
        //   ⇒ 204036 仍是原生链（选物 + 28001 `addClose`），本类只负责在开页时把
        //     `dramaData.functionData` 填好（否则 `addClose_1_0` 取不到 unitA ⇒ 好感不生效）。
        private const int OptSpareTookItems = 204032;
        private const int OptKill = 204033;
        private const int OptInsult = 204035;
        private const int OptSpare = 204034;
        private const int OptPill = 204036;

        /// <summary>灵石的道具 sid。`DataProps` 里货币也是一条 `PropsData`（`allProps` 里看得见），
        /// 所以挑"可交易物品"时必须显式跳过它，货币走下面"取 50%"那条独立路径。</summary>
        private const int MoneyPropsId = 10001;

        /// <summary>羞辱 → `relationData.AddHate(玩家unitID, 请求值)` 的请求值（保守值）。
        /// 引擎按 `RoleClose` 系数换算（好感越高实得越少），我们只回读、不补差 —— 与 `IntimService.ApplyIntim` 同口径。</summary>
        private const int InsultHate = 10;

        /// <summary>羞辱 → `AddHate` 之后再按绝对值压一档好感（`SetIntim`，**只降不升**）。</summary>
        private const int InsultIntimDrop = 20;

        /// <summary>连续几个采样都"看不到剧情窗"才认定玩家真把页关了（见 `HoldTick` 的理由）。</summary>
        private const int VerdictClosedStrikes = 2;

        /// <summary>
        /// 战败重伤的**兜底**气运 id（`RoleBattle.hurtFeatureID` 的出厂值 5011 = 「重伤」，
        /// `RoleCreateFeature`：type=2 / duration=6 月 / tips「体力上限暂时减少30%」）。
        /// 正常路径读表，只有读表失败才用它 —— 并在日志里写明"用了兜底"。
        /// </summary>
        private const int HurtLuckFallback = 5011;

        /// <summary>
        /// 处置页**优先用游戏原生实现**（10-27）：`new UICustomDramaBattleNPCFailed2(props, 败者).OpenUI()`。
        /// 它就是 20403 那一页（`GetDramaID`@`0x2EEB290` = `mov eax,0x4FB3`），`OpenUI`@`0x2EE8F40`
        /// 里先 `Init()` 给 204032..204036 注册**引擎自己的**回调，再 `DramaTool::OpenDrama(20403, dd)`。
        /// 打开失败（构造 / `OpenUI` 抛异常）自动退回下面"我们自己开页"的那条路，**不会什么都不做**。
        /// 设为 `false` 就永远走自研那一条（对照用）。
        /// </summary>
        internal static bool UseNativeVerdictPage = true;

        private sealed class Watch
        {
            public int Slot;
            public string ActorName = "";
            public string TargetName = "";
            public string ActorUid = "";
            public string TargetUid = "";
            public string PlayerName = "";
            /// <summary>`IntoBattle` 被调到过（= 这一场真的开了）。</summary>
            public bool Started;
            /// <summary>`isBattle` 真过（采样到的）。没有它时"现在不在战斗"不能当结束证据。</summary>
            public bool SawActive;
            public bool Resolved;
            /// <summary>本次由 `_diag_npc_duel.txt` 触发 ⇒ 写详细报告。</summary>
            public bool Diag;
            public DateTime BornAt = DateTime.Now;
            public DateTime NextSampleAt = DateTime.MinValue;

            // ── 结果证据（`*Seen` = 这个人的状态确实读到了；没读到就不许当"活着"）──
            public bool ActorSeen, TargetSeen;
            public bool ActorDead, TargetDead;
            public int ActorHp = -1, TargetHp = -1, ActorHpMax = -1, TargetHpMax = -1;
            /// <summary>当场有没有别的单位在场上（含玩家）——只进诊断，不进结果。</summary>
            public int RosterCount = -1;
            public bool PlayerOnField;
            public string Evidence = "";

            // ── 战后处置（10-26）──
            /// <summary>本次观战的"拦住 → 放行"已经走过一遍。之后同一场再来的 `BattleEnd` 一律放行。</summary>
            public bool HoldDone;
            /// <summary>败者（`Die()` 与处置页右侧立绘都用它）。</summary>
            public string HoldLoserName = "";
            public string HoldLoserUid = "";
            /// <summary>`OpenDrama` 之后还没看见页 ⇒ 下一拍核对；仍看不到就 fail-open 放行。</summary>
            public bool AwaitVerdictPage;
            public bool SawVerdictPage;
            /// <summary>连续"看不到剧情窗"的采样数（≥ `VerdictClosedStrikes` 才算玩家真关了窗）。</summary>
            public int ClosedStrikes;
            /// <summary>玩家在处置页上的结局：`kill` / `spare_took_items` / `insult` / `spare` / `pill` / `closed`。</summary>
            public bool OutcomeRecorded;
            public string Outcome = "";
            public string OutcomeLabel = "";
            /// <summary>开页时算好的"败者会献什么"（**只读计划**；玩家选 204032/204035 才落刀）。</summary>
            public Offer VerdictOffer;
            /// <summary>204036 的原生好感靠这个：`dramaData.functionData` 是否填好（`unitA` 是根因）。</summary>
            public bool PillFunctionDataFilled;
            public string PillFunctionDataWhy = "";

            // ── 羞辱第二页（10-26 用户要求："收下物品，羞辱此人"之后还要有羞辱性的语句）──
            /// <summary>玩家点了「羞辱」，等处置页关掉之后再开羞辱页（**不能当场开**：那一拍
            /// 引擎正在关当前页，同时开新页会被跟着关掉）。</summary>
            public bool PendingInsultPage;
            /// <summary>羞辱页已经开过了（之后再关窗就是真的结束，别再开第二遍）。</summary>
            public bool InsultPageOpened;
            /// <summary>羞辱页开没开的**事实**（进日志与诊断，不参与判据）。</summary>
            public string InsultPageWhy = "";

            // ── 战败重伤（10-26 用户要求：除击杀外，都要给败者挂重伤）──
            /// <summary>本场已经给败者挂过重伤（幂等：同一场只挂一次）。</summary>
            public bool HurtApplied;
            /// <summary>挂重伤的实际结果（`气运 id / 改前改后`，进日志与结果）。</summary>
            public string HurtText = "";

            /// <summary>本场经历已经写过（幂等：`Finish` 只该写一次，异常兜底也不能重复写）。</summary>
            public bool MemoriesWritten;

            /// <summary>
            /// 这一页是用**游戏原生** `UICustomDramaBattleNPCFailed2.OpenUI()` 开的 ⇒
            /// 五个选项的效果由引擎自己的 `OnOptionBackCall` 回调执行，
            /// `NoteOptionClicked` 只准**记录**，绝不能再执行一遍。
            /// </summary>
            public bool NativePage;

            /// <summary>
            /// ★原生模式的主放行信号★ 引擎的 `dd.onEndCall` 已经回调 = 这条剧情链（含后续页）走完了。
            ///
            /// 为什么以它为准（10-27 真机实证，`Player.log` 16:26:32 点 → 16:26:37.829 回调）：
            ///   `UICustomDramaBase::OpenUI`@0x2EE8F40 把我们的委托 `Delegate::Combine` 进 `dd+0x60`，
            ///   而后续页（羞辱页 1450005/1450006、击杀确认页 20410）都由
            ///   `DramaTool::OpenDrama(String, dd)` **带着同一个 dd** 打开 ⇒ `dd+0x60` 一路继承。
            ///   于是它 = "这一整串页面都结束了"，正好是我要的边界。
            ///   比轮询层顶强：层顶是代理（上一格 bug 的根因），这是引擎自己发的事件。
            /// </summary>
            public bool NativeEnded;

            /// <summary>
            /// 按名字取 `UIDramaDialogue` **曾经真的量到过一次** ⇒ 之后判"页还开着吗"
            /// 一律走名字判据，永久退出层顶兜底（理由见 `NativePageAlive` 的三级判据）。
            /// </summary>
            public bool NativeSawWindow;
        }

        /// <summary>
        /// 开页时算好的"败者会献什么"——**只读计划，不动背包**。
        /// 口径（用户 10-26 定，别自由发挥）：优先一件可交易物品；没有物品就取灵石 50%
        /// （向下取整、最少 1）；什么都没有 ⇒ `Has=false`，只叙事（日志"他身上什么都没有"）。
        /// </summary>
        private sealed class Offer
        {
            public bool Has;
            public bool IsMoney;
            public int PropsId;
            public string SoleId = "";
            public int Count;
            public string Name = "";
            /// <summary>填 `{0}` 的展示名（物品名 / `灵石×N`）。</summary>
            public string Display = "";
            /// <summary>那件物品的 `PropsData` 本体（`UnitActionRoleRelease` 要吃它；灵石没有 ⇒ null）。</summary>
            public DataProps.PropsData Prop;
            /// <summary>没有可献之物时的原因（只进日志，不编）。</summary>
            public string Why = "";
        }

        private static Watch _watch;
        private static bool _installed;
        // 强引用必须留：委托被 GC 掉，订阅了就永不回调
        private static Action<ETypeData> _frontHandler;
        private static Action<ETypeData> _exitHandler;
        private static Action<int, JObject> _resolvedHandler;

        // ── 被拦下的那一次 `BattleEnd`（同一时刻只可能有一场观战，所以是静态的）──
        /// <summary>正拦着一场收尾（= `BattleEndMgr.BattleEnd` 被跳过、还没打回去）。</summary>
        private static bool _holdActive;
        /// <summary>正在把原方法打回去。重入闸：反射调用会**再次**进 `NoteBattleEnding`。</summary>
        private static bool _releasing;
        /// <summary>"同一场的重复 BattleEnd"只喊一次日志。</summary>
        private static bool _holdDupLogged;
        private static MethodBase _heldMethod;
        private static object _heldInstance;
        private static bool _heldIsWin;
        private static Watch _heldWatch;

        internal static bool Busy { get { return _watch != null; } }

        // ══════════════════════════════════════════════════════════
        //  安装
        // ══════════════════════════════════════════════════════════

        internal static void EnsureInstalled()
        {
            if (_installed) return;
            _installed = true;
            try
            {
                if (g.events == null)
                {
                    ModMain.P(Tag + "g.events 为空（世界未加载？）→ 收口退化为 isBattle 边沿");
                    return;
                }
                _frontHandler = OnBattleEndFront;
                _exitHandler = OnBattleExit;
                g.events.On(EBattleType.BattleEndFront, _frontHandler, 0, false);
                g.events.On(EBattleType.BattleExit, _exitHandler, 0, false);
                // 槽位被**外力**收掉（过期 / 世界重载 ForceResolveAll）时同步清 `_watch`，
                // 否则下一场永远撞 `_watch != null` —— 这叫"票没了人还站着"。
                _resolvedHandler = OnSlotResolved;
                DramaGate.OnResolved += _resolvedHandler;
                // 长等续期：DramaGate 每 BeatSeconds 发一次心跳，这里借这一刻**按事实**续期
                // （还在打就续，没打就不续）—— 不是"猜一场架要打多久"，而是"看见了就延"。
                DramaGate.OnStillPending += OnSlotBeat;
                EnsureDiag();                       // 观测加速器（_diag_npc_duel*.txt，可删）
                ModMain.P(Tag + "★BattleEndFront/BattleExit/OnResolved 已挂★");
            }
            catch (Exception e)
            {
                ModMain.P(Tag + "订阅失败（不影响 isBattle 边沿收口）: " + e.GetType().Name + " " + e.Message);
            }
        }

        // ══════════════════════════════════════════════════════════
        //  入口
        // ══════════════════════════════════════════════════════════

        /// <summary>
        /// 让 <paramref name="actor"/> 与 <paramref name="target"/> 当场打一场，玩家旁观。
        /// 成功返回 `DramaGate` 的挂起标记（这一轮工具结果等打完才回）；失败返回 `{success:false,error}`。
        /// </summary>
        internal static JObject Attack(WorldUnitBase actor, WorldUnitBase target, string targetName)
        {
            return AttackCore(actor, target, targetName, false);
        }

        private static JObject AttackCore(WorldUnitBase actor, WorldUnitBase target, string targetName, bool diag)
        {
            if (actor == null || target == null)
                return Fail("认不出要对战的两个人（单位已离场？）");
            if (SameUnit(actor, target))
                return Fail("不能对同一个人发起对战");
            // ★ 世界没进来就别开打 ★（10-26 真机：读档的加载屏里世界数据已可读，
            //   这时开打会在 `IntoWorld` 善后时被 `ForceResolveAll` 收票 ⇒ 静默作废、连
            //   `BattleEndMgr.BattleEnd` 都不来。诊断那条已经有同一道闸，这里补上入口侧的。）
            if (!ModMain.WorldEntered)
                return Fail("世界还没加载完（等进了地图再开战）");
            // ★ 必须两个人都是 NPC ★ 引擎按"两边有没有玩家"分派：只要有一边是玩家，
            //   `DramaUnitBattle` 就转去 `UIMapBattlePre`（战前窗，等玩家点）。
            //   那条路是"玩家参战"，不是观战 —— 在这里放行会把一个战前窗打开却没人负责，
            //   所以直接拒。打玩家请走 `target` 留空的原生攻击链。
            if (IsPlayer(actor) || IsPlayer(target))
                return Fail("这条是给两个 NPC 用的：要对玩家出手，请把 target 留空");
            // ★ 同格：这条是**我们自己的**产品约束，不是引擎要求（10-27 复核 + 10-28 定）★
            //
            //   引擎侧事实（反编，别改回去）：
            //     · `WorldUnitBase::CreateAction`@0x32B8D40 只是 `IsCreate` → `Create` 的调度；
            //     · `UnitActionRoleAttack::IsCreate`@0x3702150 与
            //       `UnitActionRoleToUnitBase::IsCreate`@0x37B8430（534 行）里**没有坐标比较**；
            //     · `UnitActionRoleDrill::IsCreate`@0x3F1ACF0（切磋，对照组）调用序列几乎逐条相同。
            //     ⇒ 引擎在 `CreateAction` 返回 0 时**还没检查过两人是否同格**。
            //     · 我们走的是 `dramaUnitBattle_*` 观战副本，`SpecialBattle79::OneIntoRoomEnd`
            //       拿 `BattleMapMgr::PointToWorld` 在**战场上**重新摆位，与地图格子无关；
            //       配表里那 14 条剧情对战也从不带距离条件。
            //
            //   所以"必须先走到一起"是**语义上的**判断（隔着几个州看人打架说不通），
            //   不是引擎逼的 —— 用户 10-28 明确要求加。代价是 `solo` 触发率会下降
            //   （NPC 平时各在各的城里），因此留了开关：`_diag_no_duel_samegrid.txt`
            //   存在即退回旧行为，**不必重新编译**。
            if (!DiagSwitches.NoDuelSameGrid && !SameGrid(actor, target))
                return Fail("你与" + targetName + "不同格，无法开战（先走到同一处）");
            if (_watch != null)
                return Fail("已有一场对战在进行中，等它结束再动手");
            // 拦住状态与台账必须成对：只剩"拦着"没有台账 = 上一次收尾没收干净（异常路径）。
            // 先把它放行，否则新一场的 `BattleEnd` 会被上面那个 `_holdActive` 分支一直拦住。
            if (_holdActive && _watch == null)
            {
                try { ReleaseHold("新一场开始前清理残留的拦住状态"); } catch { }
            }

            if (!DramaGate.TryDefer(ModIds.DramaNpcDuelWatch, ExpireSeconds, true, out int slot))
                return Fail(DramaGate.RejectText("已有一个行动在等待玩家响应（剧情/论道界面未关闭），请先完成当前交互"));

            var w = new Watch
            {
                Slot = slot,
                ActorName = NameOf(actor),
                TargetName = string.IsNullOrEmpty(targetName) ? NameOf(target) : targetName,
                ActorUid = UidOf(actor),
                TargetUid = UidOf(target),
                PlayerName = PlayerName(),
                Diag = diag,
            };
            _watch = w;
            DiagHeader(w);

            // ★ 必须在调用**之前**把 `_watch` 就位 ★
            //   两边都不是玩家 ⇒ `DramaUnitBattle` **同步**调 `IntoBattle`（0x18224F374），
            //   `BattleStartHook` 的前缀会在 `OptionsFunction` 返回之前就打进来。
            try
            {
                DramaFunctionTool.OptionsFunction(WatchFunction, new DramaFunctionData
                {
                    unitLeft = actor,
                    unitRight = target,
                });
            }
            catch (Exception e)
            {
                _watch = null;
                try { DramaGate.Cancel(slot); } catch { }
                ModMain.P(Tag + "OptionsFunction 抛异常：" + e.GetType().Name + " " + e.Message);
                DiagLine(w, "✗ OptionsFunction 抛异常：" + e.GetType().Name + " " + e.Message);
                return Fail("这场对战没能开起来：" + e.Message);
            }

            // 同步判"到底开没开"：这是**事实**，不是等一个数字。
            // `IntoBattleInit` 第 6 条指令就置 isBattle=true，所以我们这里读到 false = 引擎压根没接。
            bool started = w.Started;
            if (!started)
            {
                try { started = BattleActive(); } catch { }
            }
            ModMain.P(Tag + " 发起观战 " + w.ActorName + "(" + w.ActorUid + ") vs "
                      + w.TargetName + "(" + w.TargetUid + ") slot=" + slot + " 同格=" + SameGrid(actor, target)
                      + " 已开打=" + started);
            DiagLine(w, "调用返回：Started=" + w.Started + " isBattle=" + started
                        + "（同步判据 ⇒ 分派" + (started ? "成功" : "失败") + "）");
            if (!started)
            {
                _watch = null;
                try { DramaGate.Cancel(slot); } catch { }
                return Fail("这场对战没能开起来（引擎没有进入战斗）。可能两人此刻无法交手，或该副本未就绪。");
            }
            w.Started = true;
            DiagBattle(w, "开打那一刻");
            return DramaGate.MakePendingMarker(slot);
        }

        // ══════════════════════════════════════════════════════════
        //  证据入口（由其它模块调用）
        // ══════════════════════════════════════════════════════════

        /// <summary>`BattleStartHook` 前缀调用：游戏真的开打了。只认我们这一场在等的时候。</summary>
        internal static void NoteBattleStarted(string sig)
        {
            var w = _watch;
            if (w == null || w.Resolved) return;
            w.Started = true;
            try { ModMain.P(Tag + " 已开打：" + sig); } catch { }
            DiagLine(w, "BattleStartHook 报信：" + sig);
        }

        /// <summary>`DramaGate` 的心跳回调：这一场还在打就续期（同一刻也顺手记下"真打过"）。</summary>
        private static void OnSlotBeat(int slot)
        {
            var w = _watch;
            if (w == null || w.Resolved || w.Slot != slot) return;
            // ★ 拦住收尾期间**必须**续期 ★ 这段时间 `isBattle` 随时可能因为那条被拦下的收尾
            //   而翻假；只按 `BattleActive()` 续期会让 180s 到期把玩家选到一半的处置页收掉
            //   （槽位没了 ⇒ 结局没处送、战斗还拦着）。"正拦着"本身就是最强的"还在等"。
            if (_holdActive)
            {
                try { DramaGate.Renew(slot, ExpireSeconds); } catch { }
                return;
            }
            bool active = false;
            try { active = BattleActive(); } catch { }
            if (!active) return;
            w.SawActive = true;
            try { DramaGate.Renew(slot, ExpireSeconds); } catch { }
        }

        /// <summary>`DramaGate.OnResolved`：槽位被外力收掉（过期 / 世界重载）⇒ 清本地状态。</summary>
        private static void OnSlotResolved(int slot, JObject result)
        {
            // ★ 先放行被拦住的战斗收尾 ★ 槽位被外力收掉（过期 / 世界重载）意味着这条链已经没有
            //   归宿了：再拦着就是"一场永远结束不了的战斗"（fail-open 铁律）。
            try
            {
                if (_holdActive && _heldWatch != null && _heldWatch.Slot == slot)
                    ReleaseHold("槽位被外力收掉（slot=" + slot + "）");
            }
            catch { }
            var w = _watch;
            if (w == null || w.Slot != slot || w.Resolved) return;
            w.Resolved = true;
            _watch = null;
            DiagLine(w, "槽位被外力收掉（过期/世界重载）→ 本地状态已清");
            try { ModMain.P(Tag + " 槽位 " + slot + " 被外力收掉 → 清 _watch"); } catch { }
        }

        /// <summary>`BattleFlowGate` 的采样边沿（0.25s，与战前/战中判据同一份采样，不新增定时器）。
        /// 只在"打过一场、现在不打了"这一条下降沿上收口 —— 这是**状态边沿**，不是超时。</summary>
        internal static void Tick(bool preShowing, bool inBattle)
        {
            // ⓪ 正拦着一场收尾：这一拍只判"能不能放行"。战斗还没结束（那是被我们拦住的），
            //    所以下面"isBattle 由真翻假"的收口在这一拍不该发生。
            if (_holdActive)
            {
                try { HoldTick(); }
                catch (Exception e) { try { ModMain.P(Tag + " HoldTick 异常（下一拍重试）: " + e.Message); } catch { } }
                return;
            }
            var w = _watch;
            if (w == null || w.Resolved) return;
            if (inBattle)
            {
                w.SawActive = true;
                DiagSample(w);
                return;
            }
            if (w.Started && w.SawActive)
            {
                // 这条边沿是"事件没来"时的收口路径 ⇒ 此刻还没有快照，补一次再组装结果，
                // 否则交付给模型的就是一份"打过、但什么都不知道"的空结果。
                if (!w.ActorSeen || !w.TargetSeen) Snapshot(w);
                Finish(w, "isBattle 由真翻假", BuildResult(w));
            }
        }

        // ══════════════════════════════════════════════════════════
        //  战斗事件
        // ══════════════════════════════════════════════════════════

        /// <summary>战斗即将结束：**唯一还能读到战场单位状态的时刻**（同 `TeamBattleFollowup` 的口径）。</summary>
        private static void OnBattleEndFront(ETypeData e)
        {
            var w = _watch;
            if (w == null || w.Resolved || !w.Started) return;
            Snapshot(w);
            DiagLine(w, "BattleEndFront 已到：证据=" + w.Evidence);
        }

        /// <summary>战斗场景关闭 = 这一场结束。与 `BattleEndFront` 谁先到算谁。</summary>
        private static void OnBattleExit(ETypeData e)
        {
            var w = _watch;
            if (w == null || w.Resolved || !w.Started) return;
            if (!w.ActorSeen || !w.TargetSeen) Snapshot(w);   // EndFront 没到过/缺一边就补一次（已读到的不会被覆盖）
            DiagLine(w, "BattleExit 已到：证据=" + w.Evidence);
            Finish(w, "BattleExit", BuildResult(w));
        }

        // ══════════════════════════════════════════════════════════
        //  战后处置页：拦住这一次 BattleEnd（10-26）
        // ══════════════════════════════════════════════════════════

        /// <summary>
        /// `BattleEndHook.Prefix` 在**报完信之后**问这一句：这次 `BattleEnd` 要不要拦？
        /// 返回 **false = 拦住**（跳过原方法）；true = 照常放行。
        ///
        /// 只有同时满足才拦：① 有一场我们发起的观战、已判出胜者；② 败者是**世界 NPC**
        /// （不是玩家、不是 null）；③ 本次观战的 hold 还没放行过（`w.HoldDone`）。
        ///
        /// ## 为什么 `BattleEndMgr.BattleEnd(Boolean)` 是**实例方法**（反射放行的依据）
        /// 反编 `BattleEndMgr::BattleEnd`@0x28FA0D0（`il2cpp_disasm.py 0x28FA0D0`，10-26）：
        /// ```text
        ///   1828fa0d0: mov [rsp+0x10],dl          ; dl = 那个 bool（isWin）
        ///   1828fa0e3: mov r14,rcx                ; rcx = 第一个参数 = this
        ///   1828fa314: cmp BYTE PTR [r14+0x10],r15b    ; 读 this+0x10
        ///   1828fa52b: mov BYTE PTR [r14+0x11],al      ; 写 this+0x11
        ///   1828fa52f: mov BYTE PTR [r14+0x10],0x1     ; 写 this+0x10
        /// ```
        /// ⇒ rcx 被反复**当对象读写字段**，所以它是 `this`，不是"静态方法的第一个业务参数"。
        /// 调用方 `SpecialBattle79::BattleEnd`@0x2BFDCD0 也印证：先 `movss [rcx+0x18],xmm6`
        /// （把结算倍率写进管理器），再 `movzx edx,bl; jmp 0x1828FA0D0` —— rcx 一路带同一个实例。
        /// 所以放行必须 `Invoke(__instance, …)`，不能 `Invoke(null, …)`。
        ///
        /// ## 为什么要一个 `_releasing` 重入闸
        /// Harmony 的 `__originalMethod` 就是**被补丁的那个方法**本身（detour 挂在它的入口上）：
        /// 反射调它 ⇒ 前缀会**再次**进来。放行时置 `_releasing`，重入的那一次直接 `return true`
        /// 让原方法体跑完；没有这道闸就是"每次都再拦一次，战斗永远结束"或"原方法跑两遍"。
        /// </summary>
        internal static bool NoteBattleEnding(bool isWin, object instance, MethodBase original)
        {
            try
            {
                if (_releasing) return true;                 // 放行引起的重入：放行
                if (_holdActive)
                {
                    // 同一场的重复收尾（拦住的这段时间里引擎又喊了一次）⇒ 继续拦。
                    if (!_holdDupLogged)
                    {
                        _holdDupLogged = true;
                        try { ModMain.P(Tag + " 重复的 BattleEnd（已在拦）→ 继续拦"); } catch { }
                    }
                    return false;
                }

                var w = _watch;
                if (w == null || w.Resolved || w.HoldDone || !w.Started) return true;

                // ★ 快照必须在**这里**做 ★ `BattleEndFront` 就在被拦住的这个方法体内发，
                //   一旦拦住就永远等不到它；而这一刻战场单位还活着（场景还没拆）。
                Snapshot(w);
                if (!w.ActorSeen || !w.TargetSeen || w.ActorDead == w.TargetDead) return true;   // 判不出胜者就不拦

                bool actorLost = w.ActorDead;
                string loserUid = actorLost ? w.ActorUid : w.TargetUid;
                string loserName = actorLost ? w.ActorName : w.TargetName;
                var loser = UnitOf(loserUid);
                if (loser == null)
                {
                    try { ModMain.P(Tag + " 判出败者 " + loserName + " 但找不到它的世界单位 → 不拦（没法处置）"); } catch { }
                    return true;
                }
                if (IsPlayer(loser))
                {
                    try { ModMain.P(Tag + " 败者是玩家 → 不拦（这张页是给玩家处置 NPC 用的）"); } catch { }
                    return true;
                }
                var player = g.world != null ? g.world.playerUnit : null;
                if (player == null)
                {
                    try { ModMain.P(Tag + " playerUnit 为空 → 不拦（这页左位硬编码读玩家）"); } catch { }
                    return true;
                }

                // ── 进入拦住状态：先把"怎么打回去"存好，再开页 ──
                _holdActive = true;
                _holdDupLogged = false;
                _heldMethod = original;
                _heldInstance = instance;
                _heldIsWin = isWin;
                _heldWatch = w;
                w.HoldLoserName = string.IsNullOrEmpty(loserName) ? NameOf(loser) : loserName;
                w.HoldLoserUid = loserUid;
                try
                {
                    ModMain.P(Tag + " ★拦住 BattleEnd★（isWin=" + isWin + "）败者=" + w.HoldLoserName
                              + "(" + loserUid + ") 胜者=" + (actorLost ? w.TargetName : w.ActorName)
                              + " 方法=" + DescribeMethod(original, instance)
                              + " → 原地不动等玩家处置（无限等，不设超时）");
                }
                catch { }
                DiagLine(w, "★拦住 BattleEnd（isWin=" + isWin + "）败者=" + w.HoldLoserName);

                // ── 开处置页；开不出来就立刻放行（fail-open）──
                string why;
                if (!OpenVerdictPage(w, player, loser, out why))
                {
                    try { ModMain.P(Tag + " 处置页没能开出来（" + why + "）→ 立刻放行这次 BattleEnd"); } catch { }
                    DiagLine(w, "✗ 处置页没开出来（" + why + "）→ fail-open 放行");
                    bool invoked = ReleaseHold("处置页没开出来：" + why);
                    // 已经反射调过原方法 ⇒ 本次必须返回 false（否则原方法会跑第二遍）
                    return !invoked;
                }
                return false;                                // 拦住
            }
            catch (Exception e)
            {
                try { ModMain.P(Tag + " NoteBattleEnding 异常（fail-open）: " + e.GetType().Name + " " + e.Message); } catch { }
                bool invoked = false;
                try { if (_holdActive) invoked = ReleaseHold("拦住路径异常"); } catch { }
                return !invoked;
            }
        }

        /// <summary>
        /// 在**战斗场景里**开原版处置页 20403。返回 false = 没开成（调用方必须立刻放行）。
        ///
        /// ## 立绘位怎么填（逐条反编，不是猜）
        /// 配表 `DramaDialogue 20403`：`npcLeft=1` / `npcRight=0`。`UIDramaDialogue.UpdateUnit`
        /// @0x3300D80 的 id 分支（`TeamContainerInject` 的 190 那段已逐条核过、真机用过）：
        /// ```text
        ///   id==1  → g.world.playerUnit（**硬编码玩家**，写 unitLeft 也改不了它）
        ///   id==0  → 按 isLeft 取 unitLeft/unitRight，两边都空才退回 dramaData.unit
        ///   id==11 → dramaData.unitLeft      id==12 → dramaData.unitRight
        /// ```
        /// ⇒ 左位（npcLeft=1）**恒是玩家**；右位（npcRight=0，isLeft=false）读 `unitRight`。
        ///   所以只要 `unitRight = 败者`，画面就是"玩家在左、败者在右"。
        ///
        /// ⚠ **不要**写 `data.unit = loser` ★ `DramaData.set_unit`@0x223AB70 会**同时**写
        ///   `_unitLeft(+0x10)` / `_unitRight(+0x18)`（以及 functionData 那两个）；
        ///   对象初始化器里写在 `unitLeft` 之后就会把左侧也改成败者。
        ///   `set_unitLeft`@0x223AA90 另有用途：它**单向回填** `functionData.unitLeft`（仅当为空），
        ///   而 `{call|B|A}` 这类占位要按左右取人 —— 所以 `unitLeft = 玩家` 照配表语义写上。
        ///   ★ 两边立绘到底反没反只能真机看；日志把两侧名字都打出来，反了就换位。★
        ///
        /// ## 返回值语义（反编 `DramaTool.OpenDrama(string,DramaData)`@0x463F1F0，`bool`）
        /// `RandomDramaID` 解不出 id ⇒ `xor al,al`（返回 false，引擎自己只打一行「找不到剧情ID」）；
        /// 正常开 ⇒ `OpenDramaInID`（或"已有剧情界面"时排进 `WorldSystemMgr`）后 `mov al,1`。
        /// 所以 false 是**确定的失败证据**，必须立刻 fail-open。
        /// </summary>
        private static bool OpenVerdictPage(Watch w, WorldUnitBase player, WorldUnitBase loser, out string why)
        {
            why = "";
            string leftName = NameOf(player), rightName = NameOf(loser);
            // 顺序有意义：先 unitLeft（会回填 functionData.unitLeft），再 unitRight；
            // **不碰** `unit`（那个 setter 会把两侧都改成同一个人，见上面的反编）。
            var dd = new DramaData { unitLeft = player, unitRight = loser };
            try { dd.unitLeftName = leftName; dd.unitRightName = rightName; } catch { }

            // ══════════════════════════════════════════════════════════════════
            //  ★ 204036「喂丹药」的原生好感：把 functionData 填好 ★（10-26）
            //
            //  反编 `DramaFunction.AddClose`@0x223B760（`addClose_1_0_50_70` 的处理体）：
            //    holder = GetUnitInType(values[1]=1) = functionData.unitLeft
            //    target = GetUnitInType(values[2]=0) = functionData.unitA
            //    两边任一为 null ⇒ 0x223B7F5 直接 return（这就是"好感未生效"的根因）
            //    → `relationData.AddHate(targetUnitID, +Random(50,70))`
            //  `DramaData.ctor`@0x223A710 必定 new 一个 `DramaFunctionData`（并反指回 dramaData），
            //  `set_unitLeft/set_unitRight`@0x223AA90/0x223AB20 又会单向回填 functionData 的左右，
            //  **只有 unitA 没人填** —— 这里补上。
            //
            //  ★ 但配表函数名叫 addClose、实现调的却是 `RelationData::AddHate`：按本仓已用的
            //    `ApplyIntim` 口径（正 AddIntim / 负 AddHate）与 `RoleKill.hateAddKill=500` 的语义，
            //    正数 AddHate = 仇恨↑/好感↓。也就是说这条原生副作用**方向是"降好感"**，
            //    与"好感 +50~70"的字面命名相反。我们只负责"让它能跑"，方向如实写进日志/文档。
            // ══════════════════════════════════════════════════════════════════
            try
            {
                var fd = dd.functionData;
                if (fd == null)
                {
                    w.PillFunctionDataWhy = "dramaData.functionData 为空";
                }
                else
                {
                    fd.unitA = loser;          // ← "好感未生效"的那一格
                    fd.unitLeft = player;      // 与 addClose_1_0 的 1=unitLeft 对齐（已回填，这里写明）
                    fd.unitRight = loser;
                    w.PillFunctionDataFilled = true;
                }
            }
            catch (Exception e)
            {
                w.PillFunctionDataWhy = "填 functionData 异常 " + e.GetType().Name + " " + e.Message;
            }

            // ── 开页前把"他会献什么"算好（只读）──
            //  ① `{0}` 要在这之前填（正文是"这里的{0}是我最珍贵的东西"）；
            //  ② 玩家点下去那一拍世界可能已被放行拆掉，先把 sid/数量定下来最稳。
            w.VerdictOffer = PlanOffer(loser, player);

            // ══════════════════════════════════════════════════════════════
            //  ★首选：让**游戏自己**开这一页★（10-27，开关 `UseNativeVerdictPage`）
            //  为什么它能开：`UICustomDramaBattleNPCFailed2` 就是 20403 的原生实现
            //  （`GetDramaID`@`0x2EEB290` = `mov eax,0x4FB3; ret`），`OpenUI`@`0x2EE8F40` 里
            //  先 `Init()` 给 204032..204036 注册引擎自己的回调，再 `DramaTool::OpenDrama(20403, dd)`。
            //  交回引擎的部分：选项分派、`{0}`（`HretProps` 按 props 拼）、缴物件数、
            //  以及 `Init` 后面那个第 6 个动态选项（id 存在 `this+0x44`）。
            //  构造 / `OpenUI` 抛异常 ⇒ 原样退回下面"我们自己开页"的那条路。
            // ══════════════════════════════════════════════════════════════
            if (UseNativeVerdictPage)
            {
                string nwhy;
                if (TryOpenNativePage(w, player, loser, out nwhy))
                {
                    w.NativePage = true;
                    // ★原生模式的可见性判据与自研页**不同**★（见 `NativePageAlive`）：
                    //  这里量的是"我 new 出来的那个实例还在不在"，不是层顶名字。
                    bool nvis = NativePageAlive(w) || VerdictPageVisible();
                    w.SawVerdictPage = nvis;
                    w.AwaitVerdictPage = !nvis;
                    w.ClosedStrikes = 0;
                    try
                    {
                        // 三级判据的读数全打出来 —— 下次真机一眼就能看出是哪一级在起作用
                        bool winVis = false;
                        string winWhy = "";
                        try { winVis = WorldStateFacts.DramaPageVisible(); }
                        catch (Exception e) { winWhy = " 读异常 " + e.GetType().Name; }
                        ModMain.P(Tag + " 开处置页（原生）：" + nwhy
                                  + "｜同帧可见=" + nvis
                                  + "（①引擎未收尾=" + !w.NativeEnded
                                  + " ②剧情页可见=" + winVis + winWhy
                                  + " ③层顶含Drama=" + VerdictPageVisible() + "）"
                                  + "｜五选项效果交给引擎回调，本仓只记录；不选就一直等");
                    }
                    catch { }
                    DiagLine(w, "处置页=原生 UICustomDramaBattleNPCFailed2｜" + nwhy);
                    return true;
                }
                w.NativePage = false;
                try { ModMain.P(Tag + " ⚠ 原生页开不出来，退回自研开页：" + nwhy); } catch { }
                DiagLine(w, "原生页开不出来，退回自研：" + nwhy);
            }
            bool zeroFilled = false;
            if (w.VerdictOffer != null && w.VerdictOffer.Has)
            {
                try
                {
                    // 反编 `UIDramaBase.GetDialogueText`@0x32F9B10：`dialogueValues[item.id]` 取出
                    // `string[]` → `GameTool::StrPar`@0x2FD3F30 逐槽替换 `{0}`/`{1}`…
                    // 不填就原样留 `{0}`（真机截图"这里有{0}"就是这么来的）。
                    // 给两份：20403 的 8204934 那一版写的是 `{1}`，其余默认版写 `{0}`。
                    dd.dialogueValues[VerdictPageId] = new string[] { w.VerdictOffer.Display, w.VerdictOffer.Display };
                    zeroFilled = true;
                }
                catch (Exception e)
                {
                    try { ModMain.P(Tag + " ✗ {0} 填充失败（引擎侧会裸露）: " + e.GetType().Name + " " + e.Message); } catch { }
                }
            }

            try
            {
                ModMain.P(Tag + " 开处置页 " + VerdictPageId + "：左(unitLeft/玩家)=" + leftName
                          + " 右(unitRight/败者)=" + rightName
                          + "｜献物=" + DescribeOffer(w.VerdictOffer)
                          + "｜{0}=" + (zeroFilled ? "\"" + w.VerdictOffer.Display + "\"" : "未填（无可献之物）")
                          + "｜functionData=" + (w.PillFunctionDataFilled
                              ? "已填(unitLeft=" + leftName + ", unitA=" + rightName + ")"
                              : "未填(" + w.PillFunctionDataWhy + ")")
                          + "｜真机若看到左右立绘反了，就是这一行的 unitLeft/unitRight 要换位");
            }
            catch { }
            bool ok;
            try { ok = DramaTool.OpenDrama(VerdictPageId, dd); }
            catch (Exception e)
            {
                why = "OpenDrama 抛异常 " + e.GetType().Name + " " + e.Message;
                return false;
            }
            if (!ok)
            {
                why = "OpenDrama 返回 false（配表里没有这一页 / RandomDramaID 解不出 id）";
                return false;
            }
            // 同帧可见性**不假设**：本仓 `TeamJoinGate.CheckWindowShown` 就是"开窗后下一拍核对"。
            // 这一拍看不到 ⇒ 交给 0.25s 采样再核一次（`HoldTick`），仍看不到才放行。
            bool visible = VerdictPageVisible();
            w.SawVerdictPage = visible;
            w.AwaitVerdictPage = !visible;
            w.ClosedStrikes = 0;
            try
            {
                ModMain.P(Tag + " 处置页已请求（同帧可见=" + visible + "，下一拍再核对=" + w.AwaitVerdictPage
                          + "）→ 等玩家选；不选就一直等");
            }
            catch { }
            // 成品句 = 引擎 `GetDialogueText` 的返回值（`{0}` 填没填上，以这一行为准；
            // 读不到就明说"未捕获"，不假装）。玩家点选项那一刻还会再打一行（见 NoteOptionClicked）。
            try
            {
                string ui = DramaTextCapture.LastUiText;
                ModMain.P(Tag + " 处置页成品句（引擎 GetDialogueText 返回值）: "
                          + (string.IsNullOrEmpty(ui) ? "（此刻未捕获，点击时再核对）" : ui));
            }
            catch { }
            DiagLine(w, "处置页 OpenDrama 返回 true，同帧可见=" + visible
                        + "｜献物=" + DescribeOffer(w.VerdictOffer)
                        + "｜{0}=" + (zeroFilled ? "已填" : "未填")
                        + "｜functionData=" + (w.PillFunctionDataFilled ? "已填" : "未填"));
            return true;
        }

        /// <summary>
        /// 拦住期间每 0.25s 一拍（复用 `BattleFlowGate` 的既有采样节拍，**没有新定时器**）。
        ///
        /// 判据分两套（**原生页 vs 自研页**），别混用 —— 这是 10-27 真机事故的直接教训：
        ///
        /// **原生页**（`w.NativePage`）：
        ///   ① 主信号 = `w.NativeEnded`（引擎 `dd.onEndCall` 回调）。整串页面（20403 → 羞辱页
        ///      1450005 → 1450006 → …）都带同一个 `dd` ⇒ 这个事件 = "这一整串都结束了"。
        ///   ② 兜底 = 按名字取 `UIDramaDialogue` 并判它**可见**（`NativePageAlive`）。
        ///      **不再**用 `DramaWindowReason()`（层顶名字含不含 `Drama`）——
        ///      那量的是"此刻最上面是谁"，不是"我那扇窗还在不在"。
        ///      真机反例：页在 t=275.1s 开出、玩家看到 t=384s，中间层顶被引擎自己的条盖住过一次，
        ///      旧判据判成"玩家关窗"，提前 46 秒放行 + 把结局错记成 closed。
        ///
        /// **自研页**（`!w.NativePage`）：维持原判据（层顶含 `Drama` 算开着），
        ///   那条路是我们自己 `DramaTool.OpenDrama(20403, dd)` 开的，没有 `onEndCall` 可用。
        ///
        /// 三件事，顺序即优先级：
        ///   ① **开页核对**：说开成功了、但下一拍仍看不到 ⇒ 立刻放行（fail-open）。
        ///   ② **关窗**：连续**两拍**看不到才算"玩家把窗关了/关掉了"。
        ///      为什么不是一拍：`DramaSegmentProbe` 头注释实测过"同一扇窗翻页时会有一两帧窗不可见"
        ///      ⇒ 单拍判关会把 `204036 → 28001` 这种跳页当成玩家关窗，提前把战斗放掉。
        ///   ③ 放行：窗关着 + 结局已定（或把"关窗"记成放过）⇒ 反射调回原 `BattleEnd`。
        /// </summary>
        private static void HoldTick()
        {
            var w = _heldWatch;
            if (w == null) { ReleaseHold("台账已经不在了（只拦着、没人认领）"); return; }

            bool visible = w.NativePage ? NativePageAlive(w) : VerdictPageVisible();
            if (w.AwaitVerdictPage)
            {
                if (!visible)
                {
                    ReleaseHold("处置页没能开出来（开完下一拍仍看不到）");
                    return;
                }
                w.AwaitVerdictPage = false;
            }
            if (visible)
            {
                w.SawVerdictPage = true;
                w.ClosedStrikes = 0;
                return;
            }
            // 我方对话面板（`UIChatAi`）盖在页上**不算"关窗"**：玩家能在处置页上点「对话」
            // （`BattleOutcome` 的 B 支注释写的就是这个场景），那一刻层顶变成 `UIChatAi`
            // （不含 `Drama`）—— 只看层顶会把"玩家正在聊天"误判成"他把页关了"，
            // 于是提前放掉战斗、还把结局错记成"放过"。所以要求两者都没有。
            if (ChatPanelVisible()) { w.ClosedStrikes = 0; return; }
            if (!w.SawVerdictPage) return;          // 还没看见过页（Await 那条路会先兜住，这里只是保险）
            if (++w.ClosedStrikes < VerdictClosedStrikes) return;

            // ★ 羞辱第二页（10-26 用户要求）★
            //   "收下物品，羞辱此人"之后还要有羞辱性的语句 —— 原版就是这么演的：
            //   `RoleBattleSub.abuseDialogueID` 里 32 条 `1450001/1450005/…`（说话人 speaker=1，
            //   骂人的那一方），`nextDialogue` 再接一条 `1450002/…` 挨骂方回嘴（speaker=2）。
            //   我们不能在 `ClickOption` 那一拍开（引擎正在关当前页，同时开新页会被一起关掉），
            //   所以记一个 `PendingInsultPage`，等**处置页真的关掉了**（连续两拍看不到）再开。
            //   开过之后 `InsultPageOpened=true` ⇒ 再关窗就真的放行了，不会无限循环。
            if (w.PendingInsultPage && !w.InsultPageOpened)
            {
                string iwhy;
                bool iok = OpenInsultPage(w, out iwhy);
                w.InsultPageOpened = true;          // 开没开成都算"试过了"（否则失败会每 0.5s 重试）
                w.InsultPageWhy = (iok ? "已开" : "没开出来") + "（" + iwhy + "）";
                w.ClosedStrikes = 0;
                w.SawVerdictPage = false;           // 新页还没被看见过；下一拍重新建立"看见过"
                w.AwaitVerdictPage = iok;           // 开成功就下一拍核对可见性
                try
                {
                    ModMain.P(Tag + (iok ? " ★羞辱页已开★ " : " ⚠羞辱页没开出来（不影响放行）：")
                              + iwhy);
                }
                catch { }
                DiagLine(w, (iok ? "羞辱页已开：" : "羞辱页没开出来：") + iwhy);
                if (iok) return;                    // 等玩家看完/关掉这一页
                // 没开出来就**继续往下走放行**（不能因为一个装饰性页面卡住战斗）
            }

            if (!w.OutcomeRecorded) SetOutcome(w, "closed", "", "玩家没选就关窗 ⇒ 按放过算，只挂重伤");
            // ★原生模式：这条 `closed` 只能是**没点过任何选项**的情况★（10-27）
            //   `SetOutcome` 是幂等的（`OutcomeRecorded`），点了选项就轮不到这里；
            //   但在原生模式下还要多一道保险：引擎的 `onEndCall` 没来、页却已经不可见了
            //   （玩家按 ESC 关掉 / 引擎异常收场），照样按"放过"收 —— 这是 fail-open，
            //   不能因为没人回话就把玩家永久卡在战斗里。
            //   真机反例见 `NativePageAlive`：上一版就是靠层顶判据在这里**误**判了一次。
            // ★把"是哪个信号触发放行"写进日志★ 下一轮测试要能自证，不能靠猜：
            try
            {
                ModMain.P(Tag + " 关窗判定成立（原生=" + w.NativePage + "）"
                          + "｜①onEndCall 已回调=" + w.NativeEnded
                          + "｜②剧情页曾量到过=" + w.NativeSawWindow
                          + "｜③层顶含Drama=" + VerdictPageVisible()
                          + "｜连续看不到=" + w.ClosedStrikes + " 拍"
                          + "｜已记结局=" + (w.OutcomeRecorded ? w.Outcome : "无"));
            }
            catch { }
            // ★ 战败重伤（10-26 用户要求）★ 除"击杀"外都要给败者挂重伤 ——
            //   语义与正常攻击战斗一致：那条链是 `UnitActionRoleBattle::BattleEnd` →
            //   `UnitActionLuckAdd(hurtFeatureID=5011)`（角色被击败分支），
            //   5011 = 「重伤」（`RoleCreateFeature`：type=2、duration=6 月、
            //   tips「体力上限暂时减少30%」）。我们走的是观战（isRealBattle=false），
            //   引擎**不会**自己挂，所以在这里补上。**必须在放行之前**做：放行会把场景拆掉。
            ApplyBattleHurt(w);
            ReleaseHold("处置页已关（结局=" + w.Outcome + "）");
            DiagLine(w, "放行：处置页已关，结局=" + w.Outcome + "｜" + w.HurtText);
        }

        /// <summary>
        /// 开"羞辱页"（玩家羞辱败者那一页）。
        ///
        /// ## 页号从哪来：整串丢给引擎，让它自己挑（与游戏点「羞辱」同一条链）
        /// `RoleBattleSub.abuseDialogueID` 是 32 条 `1450001|1450005|…|1450125`，
        /// **每条都有自己的 `condition`**（例如 `1450001`=`0` 无条件、`1450025`=`abRelation_0_6`
        /// 只有夫妻成立、`1450029`=`abRelation_0_5`）。这些 id 是**候选池**，不是"任选一条都能开"。
        ///
        /// 引擎的选择逻辑（反编实证，不是猜）：
        /// ```
        /// DramaTool::OpenDrama(string, DramaData)@0x463F1F0
        ///   CommonTool::StrSplitIntList(str, '|'=0x7c)          // 切候选
        ///   DramaTool::RandomDramaID(int[], DramaData)@0x463FBB0
        ///       foreach id:
        ///           ConfDramaDialogueBase::GetItem(id)
        ///           new UnitConditionData(player, dd._unitLeft, dd._unitRight, potmon…)
        ///           UnitConditionTool::Condition(item.unitCondition, condData)   // 假 → 丢
        ///       CommonTool::RandomRate(剩下的, weight)                          // 按权重抽
        ///       return 0 if 全被过滤
        ///   0 → OpenDrama 返回 false；否则 UIMgr 开页 → OpenDramaInID(id, data)
        /// ```
        /// 所以**只能把这串原样传进去**：换成 `OpenDrama(int)` 传单个 id 时，
        /// 引擎仍会拿那一条再过一次 `Condition`，一旦这条的 condition 不成立（比如
        /// `1450025` 要求夫妻），整个 `OpenDrama` 直接返回 false —— 这正是 10-26
        /// 那一轮 `⚠羞辱页没开出来：OpenDrama(1450025) 返回 false` 的真因：
        /// 是我们自己在 32 条里随手挑了 id，绕过了引擎的条件过滤。
        ///
        /// ## 页号仍然记进日志（诊断用）
        /// `DramaTool.RandomDramaID(string, DramaData)` 是 **public static**，且就是
        /// `OpenDrama(string)` 内部用的同一个抽签函数（反编：`0x463F1F0` 里 `StrSplitIntList`
        /// 之后直接 `call 0x463FBB0`）。所以**先调它一次"预演"**拿到引擎会抽中的 id，
        /// 再调 `OpenDrama` 真正开页。代价是抽两次（可能抽到同族的另一句），
        /// 但两次都是引擎按 condition+weight 抽的，同族同语义；换来的是日志里能写清
        /// "引擎按关系挑中了哪一页"，出问题时一眼能看出是条件全不满足还是别的原因。
        /// </summary>
        private static bool OpenInsultPage(Watch w, out string why)
        {
            why = "";
            var loser = UnitOf(w == null ? "" : w.HoldLoserUid);
            var player = g.world != null ? g.world.playerUnit : null;
            if (loser == null) { why = "认不出败者的世界单位（uid=" + (w == null ? "?" : w.HoldLoserUid) + "）"; return false; }
            if (player == null) { why = "playerUnit 为空"; return false; }

            string raw = "";
            try
            {
                var sub = g.conf != null ? g.conf.roleBattleSub : null;
                raw = sub != null ? (sub.abuseDialogueID ?? "") : "";
            }
            catch (Exception e) { why = "读 RoleBattleSub.abuseDialogueID 异常 " + e.GetType().Name; return false; }
            if (string.IsNullOrEmpty(raw) || raw == "0")
            {
                why = "配表 RoleBattleSub.abuseDialogueID 为空";
                return false;
            }

            try
            {
                var dd = new DramaData { unitLeft = player, unitRight = loser };
                try { dd.unitLeftName = NameOf(player); dd.unitRightName = NameOf(loser); } catch { }

                // 预演：同一个抽签函数，只为把"引擎会挑哪页"写进日志
                int preview = 0;
                try { preview = DramaTool.RandomDramaID(raw, dd); } catch { preview = 0; }

                bool ok = DramaTool.OpenDrama(raw, dd);          // ★ 引擎按 condition+weight 自己抽 ★
                if (!ok)
                {
                    why = "OpenDrama(候选 " + CountIds(raw) + " 条) 返回 false —— 全部 condition 在（"
                          + NameOf(player) + "→" + NameOf(loser) + "）这组单位上都不成立"
                          + "（预演抽签也返回 " + preview + "）";
                    return false;
                }
                why = "候选=" + CountIds(raw) + " 条｜引擎抽中=" + (preview > 0 ? preview.ToString() : "?")
                      + "｜左(unitLeft/玩家)=" + NameOf(player)
                      + " 右(unitRight/败者)=" + NameOf(loser) + "（说话人=左位=你）";
                return true;
            }
            catch (Exception e)
            {
                why = "OpenDrama 抛异常 " + e.GetType().Name + " " + e.Message;
                return false;
            }
        }

        /// <summary>数一下候选串里有几个 id（只用于日志）。</summary>
        private static int CountIds(string raw)
        {
            if (string.IsNullOrEmpty(raw)) return 0;
            int n = 0;
            foreach (var p in raw.Split('|'))
            {
                int v;
                if (int.TryParse((p ?? "").Trim(), out v) && v > 0) n++;
            }
            return n;
        }

        /// <summary>
        /// 战败重伤：给败者挂「重伤」气运。**击杀 / 没败者 / 已挂过** 一律不动。
        ///
        /// ## 依据（配表事实 + 反编）
        /// · `RoleBattle.hurtFeatureID = 5011`（`ConfRoleBattleBase.hurtFeatureID`，
        ///   反射核过是 `Int32` 属性）；`RoleCreateFeature 5011` = type=2、`duration=6`、
        ///   名「重伤」、tips「体力上限暂时减少30%」。
        /// · 引擎在正常攻击战斗里就是这么挂的：`UnitActionRoleBattle::BattleEnd`@0x3703110
        ///   的角色被击败分支 → `new UnitActionLuckAdd(luckId)`@0x38DF0B0 → `CreateAction`
        ///   （反编里那一段是 `call 0x1838DF0B0` 之后紧接着 vtable+0x1c8 的 CreateAction）。
        /// · 我们**不造 Action**，直接走 `TeamTool.AddLuck` —— 那条是 10-19 已定案的
        ///   "引擎三步序列就地执行"（`AddAddLuck` → `CreateLuck` → `NewCreateLuck`），
        ///   且在组队那边已经真机跑了几十场。
        ///
        /// ## 为什么用表里的 id 而不是写死 5011
        /// 写死就等于把配表抄进了代码：这张表被 mod 改过时（本仓的 `MID&52` 就是同族），
        /// 写死的 id 会静默挂错气运。读表失败才退回 5011，并把"用了兜底"打进日志。
        /// </summary>
        private static void ApplyBattleHurt(Watch w)
        {
            if (w == null || w.HurtApplied) return;
            // `native_other` = 原生页的第 6 个动态选项（大概率噬魂那类），效果不明 ⇒ 不擅自挂重伤。
            if (w.Outcome == "kill" || w.Outcome == "native_other")
            {
                w.HurtText = "战败重伤=不需要（结局=" + w.Outcome + "）";
                w.HurtApplied = true;
                return;
            }
            w.HurtApplied = true;

            var loser = UnitOf(w.HoldLoserUid);
            if (loser == null) { w.HurtText = "战败重伤=未落账（认不出败者的世界单位）"; return; }
            if (IsPlayer(loser)) { w.HurtText = "战败重伤=跳过（败者是玩家）"; return; }

            int id = 0; string from = "";
            try
            {
                var rb = g.conf != null ? g.conf.roleBattle : null;
                if (rb != null) { id = rb.hurtFeatureID; from = "表 RoleBattle.hurtFeatureID"; }
            }
            catch (Exception e) { from = "读表异常 " + e.GetType().Name; }
            if (id <= 0) { id = HurtLuckFallback; from = "兜底字面量（读表失败：" + from + "）"; }

            bool hadBefore = false;
            try { hadBefore = TeamTool.HasLuck(loser, id); } catch { }
            if (hadBefore)
            {
                w.HurtText = "战败重伤=已在身（气运 " + id + "，未重复挂）";
                try { ModMain.P(Tag + " 战败重伤：" + w.HoldLoserName + " 已带气运 " + id + "，不重复挂"); } catch { }
                return;
            }

            string err = "";
            bool ok = false;
            try { ok = TeamTool.AddLuck(loser, id, out err); }
            catch (Exception e) { err = e.GetType().Name + " " + e.Message; }
            bool hasAfter = false;
            try { hasAfter = TeamTool.HasLuck(loser, id); } catch { }

            w.HurtText = "战败重伤=气运 " + id + "（" + from + "）挂载" + (hasAfter ? "成功" : "失败")
                       + (err == null || err.Length == 0 ? "" : "｜err=" + err);
            try
            {
                ModMain.P(Tag + " " + (hasAfter ? "★" : "⚠") + " 战败重伤：" + w.HoldLoserName
                          + "(" + w.HoldLoserUid + ") 气运=" + id + "（" + from + "）"
                          + " 挂前=" + hadBefore + " → 挂后=" + hasAfter
                          + "｜AddLuck 返回=" + ok
                          + (err == null || err.Length == 0 ? "" : "｜err=" + err)
                          + (hasAfter ? "（「重伤」已生效）" : "（⚠ 没挂上）"));
            }
            catch { }
            DiagLine(w, "战败重伤：" + w.HurtText);
        }

        /// <summary>
        /// 放行：把被拦下的那次 `BattleEnd` 原样打回去。
        /// 返回 true = **真的调用了**原方法（调用方若身处 Harmony Prefix，必须返回 false 以免跑两遍）；
        /// false = 没调（本来就没在拦 / 原方法句柄丢了 / 调用抛异常）。
        ///
        /// `_releasing` 是重入闸（理由见 `NoteBattleEnding`）；`w.HoldDone = true` 是第二道保险：
        /// 之后同一场再来的 `BattleEnd` 一律放行（用户口径："本次 hold 放行过就不再拦"）。
        /// </summary>
        private static bool ReleaseHold(string why)
        {
            if (!_holdActive) return false;
            _holdActive = false;
            _holdDupLogged = false;
            var m = _heldMethod;
            var inst = _heldInstance;
            bool isWin = _heldIsWin;
            var w = _heldWatch;
            _heldMethod = null;
            _heldInstance = null;
            _heldWatch = null;
            if (w != null) { w.HoldDone = true; w.ClosedStrikes = 0; }

            if (m == null)
            {
                try { ModMain.P(Tag + " ⚠ 放行失败：被拦方法的引用丢了（这一场可能收不了尾）"); } catch { }
                return false;
            }
            if (w != null && w.NativePage)
            {
                try { string nf = NativeVerdictFacts(w); ModMain.P(Tag + " " + nf); DiagLine(w, nf); } catch { }
            }
            // ★ 战败重伤在这一步兜底 ★
            //   为什么放在**放行之前、且在这里**而不是只在 `HoldTick` 的"玩家选完"那条路上：
            //   放行有六条路径（玩家选完 / 关窗 / 页没开出来 / 页卡住 / 异常 / 外力收票），
            //   除了"击杀"以外**每一条**都要给败者挂重伤。挂在放行口 ⇒ 不可能漏；
            //   `HurtApplied` 保证同一场只挂一次（`HoldTick` 里那次是提前挂，这里是兜底）。
            try { ApplyBattleHurt(w); } catch { }
            _releasing = true;
            try
            {
                try
                {
                    ModMain.P(Tag + " 放行（isWin=" + isWin + "）：" + why
                              + "｜方法=" + DescribeMethod(m, inst));
                }
                catch { }
                DiagLine(w, "放行（isWin=" + isWin + "）：" + why);
                m.Invoke(m.IsStatic ? null : inst, new object[] { isWin });
                return true;
            }
            catch (Exception e)
            {
                try { ModMain.P(Tag + " ⚠ 放行调用失败（这一场可能收不了尾）: " + e.GetType().Name + " " + e.Message); } catch { }
                return false;
            }
            finally { _releasing = false; }
        }

        /// <summary>拦住/放行日志用的方法描述（静态还是实例是**放行方式**的依据，必须打出来）。</summary>
        private static string DescribeMethod(MethodBase m, object instance)
        {
            if (m == null) return "(null)";
            try
            {
                string owner = m.DeclaringType != null ? m.DeclaringType.Name : "?";
                return owner + "." + m.Name + "[" + (m.IsStatic ? "static" : "instance")
                     + "]实例=" + (instance == null ? "null" : instance.GetType().Name);
            }
            catch { return m.Name; }
        }

        /// <summary>
        /// 原生页此刻是否还"活着" —— **按窗口名直接问**，不用"层顶"那个代理量。
        ///
        /// ## 为什么不能持有实例（10-27 反编 + 反射双重否掉）
        ///   `UICustomDramaBattleNPCFailed2` 的基链是
        ///   `UICustomDramaBattleNPCFailed2 &lt; UICustomDramaBase &lt; Il2CppSystem.Object &lt; Il2CppObjectBase`
        ///   —— **它根本不继承 MonoBehaviour**。它是**控制器**，不是窗口。
        ///   真正显示在屏上的是 `UIDramaDialogue`（`UICustomDramaBase::OpenUI`@0x2EE8F40
        ///   最后走 `DramaTool::OpenDrama` → 由 `DramaTool` 那边 new 出来）。
        ///   ⇒ 持有控制器没有 `gameObject` 可问，`WorldStateFacts.UiVisible` 也接不了它。
        ///
        /// ## 为什么"层顶"也不算数（这才是真机那个 bug）
        ///   `DramaWindowReason()` 量的是 **UI 层层顶的名字含不含 `Drama`**。
        ///   层顶是"此刻最上面是谁"，不是"我那扇窗还在不在"：
        ///   真机 16:25:42 开页 → 16:26:28 我判"关窗" → 16:26:32 玩家才点，
        ///   中间只是引擎自己的动作条/奖励窗盖到上面，**页一直活着且可点**。
        ///   ⇒ 我提前 46 秒放行，还把结局错记成 `closed`。
        ///
        /// ## 现在的判据：**先问引擎事件，再问窗本身**（最多退回层顶一次）
        ///   ① `w.NativeEnded` 为真 ⇒ 整串演完了（引擎 `dd.onEndCall` 已回调）⇒ 结束。
        ///      真机时序：16:26:32 点「羞辱」→ 1450005 → 1450006 → 16:26:37.829 回调。
        ///      它是**事件**不是电平，所以优先于任何轮询。
        ///   ② `WorldStateFacts.DramaPageVisible()` —— 按名查 `UIDramaDialogue` 那一扇窗本身
        ///      （与 `ChatWindowVisible` 同款：`GetUI(按名)` + `UiVisible`，判可见不判存在）。
        ///      ★读法住在 `WorldStateFacts`（那里是唯一允许按名查窗的地方，
        ///      `test_drama_gate_contract` 钉住了这一点）★ 本文件只做组合，不做读法。
        ///   ③ **只在从没量到过那扇窗时**，才退回层顶（`DramaWindowReason`）。
        ///      为什么留这一级：万一 `GetUI` 那个键名在这版引擎上取不到，
        ///      ③ 就是唯一的信号 —— 宁可偶发误判（有 `ClosedStrikes` 两拍兜底 + fail-open），
        ///      也不要因为一个名字拼错就把玩家卡死在战斗里。
        ///      `NativeSawWindow` 只在"②真的量到过一次"时置真，之后 ③ 永久退出，
        ///      所以正常路径下一次都不会用到代理量。
        ///
        /// 取不到一律当**看不见**（安全方向：看不见 ⇒ 放行，不会把玩家卡在战斗里）。
        /// </summary>
        private static bool NativePageAlive(Watch w)
        {
            if (w == null) return false;
            if (w.NativeEnded) return false;          // 引擎说这一串演完了 ⇒ 可以放行

            // ② 按名取那扇窗（正常路径；读法在 WorldStateFacts）
            bool vis = false;
            try { vis = WorldStateFacts.DramaPageVisible(); } catch { }
            if (vis) w.NativeSawWindow = true;
            if (w.NativeSawWindow) return vis;

            // ③ 从没量到过那扇窗 ⇒ 退回层顶（仅在名字对不上时才会走到）
            try { return WorldStateFacts.DramaWindowReason().Length > 0; }
            catch { return false; }
        }

        /// <summary>
        /// 自研页的"此刻有没有剧情窗正显示" —— **共用判据的唯一入口**
        /// （`WorldStateFacts.DramaWindowReason()`：我方确认窗可见 + UI 层层顶名字含 `Drama`）。
        /// 为什么不自己写一套：09-21 的事故就是两套判据同一时刻给出相反结论
        /// （`tests/test_drama_gate_contract.py` 头注释）；而且它明确要求"判可见不判存在"
        /// —— `CloseUI` 不保证销毁实例，判存在会把刚关掉的页也算"还开着"，那就永远放不了行。
        /// 取不到一律当**看不见**（这个方向安全：看不见 ⇒ 放行，不会卡住玩家）。
        ///
        /// ⚠ **原生页不要用它**（见 `NativePageAlive` 头注释里的真机事故）：
        ///   层顶名字是代理量，"此刻最上面是谁" ≠ "我那扇窗还在不在"。
        /// </summary>
        private static bool VerdictPageVisible()
        {
            try { return WorldStateFacts.DramaWindowReason().Length > 0; }
            catch { return false; }
        }

        /// <summary>
        /// 我方对话面板（`UIChatAi`，`AbChatPanel`）是否可见 —— **只用于"关窗"判据**。
        /// 为什么要它：玩家能在处置页上点「对话」（面板盖在页上，`BattleOutcome` 的 B 支注释
        /// 写的就是这个场景），而那一刻 UI 层顶会变成 `UIChatAi`（不含 `Drama`）⇒ 只看层顶
        /// 会把"玩家正在聊天"误判成"他把页关了"，提前把战斗放掉、并把结局错记成"放过"。
        /// 判据本身也是共用的（`WorldStateFacts.ChatWindowVisible`，判可见），不另写一套。
        /// </summary>
        private static bool ChatPanelVisible()
        {
            try { return WorldStateFacts.ChatWindowVisible(); }
            catch { return false; }
        }

        /// <summary>
        /// 玩家在处置页上点了某个选项（`DramaSegmentProbe` 的 `ClickOption` **prefix** 转进来）。
        /// 只认 20403 那五枚 id，别的页的点击一律不理。
        ///
        /// | 选项 | 原生做了什么 | 我们做什么（10-26 起全部真落账） |
        /// |---|---|---|
        /// | 204032 东西不错我收下了，放你一马 | 配表 function=0（关页） | **原生 `OnPassClick`**：`Release(败者, props=页面所示那件, isAddHate:true)`；**放过** |
        /// | 204033 击杀此人 | 配表 function=sound_sharen（只有音效） | **原生 `OnKillClick`**：`Kill(败者, isMercy:true, isShowDrame:true, isCatlikeKill:false, isForceDie:false)` → `UnitActionRoleDying`（遗物/残魂/死亡气运/信件） |
        /// | 204035 收下物品，羞辱此人 | 关页（function=0） | **原生 `OnInsultClick` 的道具那半**（`Release` 缴物）＋ `Humiliate`（`AddHate(abuseAddHate=300)`＋气运）；随后演羞辱页 |
        /// | 204034 算了，东西不要了，你走吧 | 关页（function=0） | **原生 `OnPassNotPropClick`**：`Release(败者, props=空表 ⇒ 一件不拿, isAddHate:false)` |
        /// | 204036 选物（隐藏） | `itemSelectNotCost_…` 开选物面板，随后自己跳 `28001`（`addClose_1_0_50_70`） | 只记 `pill`；开页时已把 `functionData.unitA` 填好让原生 `addClose` 能取到人，后续页**不拦** |
        ///
        /// ★ 只在这**一次** hold 里、只认第一枚 ★ 防"窗已经关了、玩家又点了别的页"。
        /// ★ 效果必须**同步**跑完（放行会拆场景，之后改世界就晚了；`Die()` 也是同一拍）★。
        /// </summary>
        internal static void NoteOptionClicked(ConfDramaOptionsItem opt)
        {
            try
            {
                if (!_holdActive) return;
                var w = _heldWatch;
                if (w == null || w.OutcomeRecorded) return;

                int id = OptionIdOf(opt);
                if (id <= 0) return;
                string text = OptionTextOf(opt);

                // 成品句只打一行（证明 `{0}` 到底填上没有）—— 读不到就不打，不假装。
                try
                {
                    string ui = DramaTextCapture.LastUiText;
                    if (!string.IsNullOrEmpty(ui))
                        ModMain.P(Tag + " 处置页当时显示句：" + ui);
                }
                catch { }

                // ══════════════════════════════════════════════════════════════
                //  ★原生页模式：只记录，不执行★（10-27）
                //  这一页是 `UICustomDramaBattleNPCFailed2.OpenUI()` 开的，
                //  `Init()` 已经把 204032..204036 的回调注册给引擎了 ⇒ 那一侧正在干活。
                //  我们**再执行一遍**就是双份（缴两遍物、挂两遍仇恨、杀两次）—— 绝对不行。
                //  这里只留三件事：记结局（给记忆/结果用）、羞辱第二页的延后开页、观测事实。
                // ══════════════════════════════════════════════════════════════
                if (w.NativePage)
                {
                    string oc = id == OptKill ? "kill"
                              : id == OptSpareTookItems ? "spare_took_items"
                              : id == OptInsult ? "insult"
                              : id == OptSpare ? "spare"
                              : id == OptPill ? "pill" : "";
                    if (oc.Length == 0)
                    {
                        // 第 6 个动态选项（`Init` 后面按条件注册，id 存 `this+0x44`）—— 我们不认它，
                        // 但**不能当成"关窗"**：那样会被记成"放过"并挂重伤。
                        // 如实记一个中性的 `native_other`，效果仍由引擎那条回调负责。
                        SetOutcome(w, "native_other", text,
                            "原生页模式：这是第 6 个动态选项（id=" + id + "），效果归引擎；本仓不认识它，只记账");
                        return;
                    }
                    string extra = "";
                    if (id == OptInsult)
                    {
                        // 原版 `OnInsultClick` 只做三件：缴物 + `InsultUnit`（体力上限/声望/仇恨）。
                        // **它全链没有一处 `AddLuck`**（`InsultUnit` 只 `GetLuck` 读），
                        // 而配表 `abuseAddFeature1/2=9001101/9001102` + 概率明明在——
                        // 所以这两条气运原版确实没人挂，只能我们补（不补就等于配表这两个字段是死的）。
                        //
                        // ★羞辱页**不要**我们开★（10-27 真机实证，这条推翻了我 10-26 的写法）
                        //   引擎自己会开：`OnInsultClick` → `UnitActionRoleBattle::InsultUnit`@0x3708090
                        //   → `<>c__DisplayClass46_0::<InsultUnit>b__1`@0x3ADB470 → `DramaTool::OpenDrama(string, dd)`
                        //   真机栈（Player.log 16:26:32）：
                        //     剧情ID:1450005 ← UICustomDramaBase:OpenUI()
                        //                   ← <>c__DisplayClass46_0:<InsultUnit>b__1
                        //                   ← UnitActionRoleBattle:InsultUnit
                        //   我们再 `OpenInsultPage` 一次 = 多开一页、演两遍。
                        //   所以这里**只置 false**，那句话留下当反例证据。
                        w.PendingInsultPage = false;
                        extra = "｜气运（原版链不挂，我们补）=" + ApplyAbuseLuck(w)
                              + "｜羞辱页=引擎自己开（InsultUnit→OpenDrama，实测 1450005）";
                    }
                    SetOutcome(w, oc, text,
                        "原生页模式：效果由引擎自己的 OnOptionBackCall 回调执行（本仓不再重复）"
                        + extra);
                    return;
                }

                switch (id)
                {
                    case OptSpareTookItems:
                        {
                            // 204032 = 原生 `OnPassClick`@0x2EEA9D0：
                            //   new UnitActionRoleRelease(败者, props=页面所示那件, isAddHate:true) + 处置者.CreateAction
                            // ★ 空 props 表 = 一件都不拿（`Release`@0x3ABAAB0 开头 Count==0 直接跳过取物循环），
                            //   所以这里必须把"页面上说的那件"塞进去。
                            if (w.VerdictOffer != null && w.VerdictOffer.IsMoney)
                                SetOutcome(w, "spare_took_items", text,
                                    TakeOffer(w) + " → 放过（献物是灵石：PropsData 表达不了，走自研取灵石）");
                            else
                            {
                                string nf;
                                if (NativeRelease(w, true, true, out nf))
                                    SetOutcome(w, "spare_took_items", text,
                                        "缴物=原生 UnitActionRoleRelease（props=页面所示那件，isAddHate=true）｜" + nf);
                                else
                                    SetOutcome(w, "spare_took_items", text,
                                        TakeOffer(w) + " → 放过（原生失败，退回自研）｜" + nf);
                            }
                        }
                        break;
                    case OptKill:
                        {
                            // 204033 = 原生 `OnKillClick`@0x2EEA860：
                            //   new UnitActionRoleKill(败者, isMercy:true, isShowDrame:true, isCatlikeKill:false, isForceDie:false)
                            // ★ isForceDie 必须 false：true 会走 `UnitActionRoleDie` → `WorldUnitMgr::DelUnit`（瞬删，
                            //   没有遗物/残魂/死亡气运/信件）。原版 204033 两个闸都不置 ⇒ `UnitActionRoleDying`。
                            string nf;
                            if (NativeKill(w, out nf))
                                SetOutcome(w, "kill", text,
                                    "击杀=原生 UnitActionRoleKill(isMercy:true,isShowDrame:true,isCatlikeKill:false,isForceDie:false)"
                                    + " → UnitActionRoleDying（遗物/残魂/死亡气运/信件）｜" + nf);
                            else
                                SetOutcome(w, "kill", text, KillLoser(w) + "｜原生失败：" + nf);
                        }
                        break;
                    case OptInsult:
                        // 羞辱 = 取物 + 加仇恨/降好感 + **再演一页骂人的话**（用户 10-26 要求）。
                        // 那一页不在这里开：这一拍引擎正在关当前页，同时开新页会被一起关掉
                        // ⇒ 记一个 `PendingInsultPage`，由 `HoldTick` 在"页真的关掉了"之后开。
                        {
                            // 缴物：原版 `OnInsultClick` 用的是 `CostPropItem`+`RewardPropItem`，
                            // 不是 `Release` ⇒ 那一支**不加仇恨**（仇恨由 `InsultUnit` 自己加）。
                            // 所以这里 `isAddHate:false`，否则仇恨会挂两遍。
                            string nf, nfu;
                            string take = (w.VerdictOffer != null && w.VerdictOffer.IsMoney)
                                ? TakeOffer(w) + "（献物是灵石，走自研取灵石；仇恨交给 InsultUnit）"
                                : (NativeRelease(w, true, false, out nf)
                                    ? "缴物=原生 UnitActionRoleRelease(isAddHate:false，仇恨交给 InsultUnit)｜" + nf
                                    : TakeOffer(w) + "（原生 Release 失败：" + nf + "；仇恨交给 InsultUnit）");
                            string hurt;
                            if (NativeInsult(w, out nfu))
                                hurt = nfu + ApplyAbuseLuck(w);
                            else
                                hurt = Humiliate(w) + "（⚠ 原生 InsultUnit 失败，已退回自研：" + nfu + "）";
                            w.PendingInsultPage = true;
                            SetOutcome(w, "insult", text, take + "｜" + hurt + " → 随后演羞辱页");
                        }
                        break;
                    case OptSpare:
                        {
                            // 204034 = 原生 `OnPassNotPropClick`@0x2EEAB00：
                            //   new UnitActionRoleRelease(败者, props=空表, isAddHate:false) ⇒ 一件不拿、不加仇恨。
                            string nf;
                            if (NativeRelease(w, false, false, out nf))
                                SetOutcome(w, "spare", text,
                                    "放过=原生 UnitActionRoleRelease(props=空表 ⇒ 一件不拿, isAddHate=false)｜" + nf);
                            else
                                SetOutcome(w, "spare", text, "放过：一件不拿、不改关系（原生失败，未做任何事）｜" + nf);
                        }
                        break;
                    case OptPill:
                        SetOutcome(w, "pill", text, PillEffectText(w));
                        break;
                    default: return;                       // 不是这一页的选项
                }
            }
            catch (Exception e)
            {
                try { ModMain.P(Tag + " NoteOptionClicked 异常（忽略）: " + e.GetType().Name + " " + e.Message); } catch { }
            }
        }

        /// <summary>记下玩家这一下的结局 + **实际落了什么账**（`effect` 由调用方给，事实字符串）。
        /// `label` 读不到就用 `FallbackLabel`，**不编**。</summary>
        private static void SetOutcome(Watch w, string outcome, string label, string effect)
        {
            w.Outcome = outcome;
            w.OutcomeLabel = string.IsNullOrEmpty(label) ? FallbackLabel(outcome) : label;
            w.OutcomeRecorded = true;
            try
            {
                ModMain.P(Tag + " 玩家选了：" + outcome + "（" + w.OutcomeLabel + "）｜败者="
                          + w.HoldLoserName + "｜" + (effect ?? ""));
            }
            catch { }
            DiagLine(w, "玩家选了：" + outcome + "（" + w.OutcomeLabel + "）｜" + (effect ?? ""));
        }

        /// <summary>选项文案读不到时的兜底（字面量来自配表 20403 的五个选项，见 03 §8.8）。</summary>
        private static string FallbackLabel(string outcome)
        {
            switch (outcome)
            {
                case "kill": return "击杀此人";
                case "spare_took_items": return "东西不错我收下了，放你一马";
                case "insult": return "收下物品，羞辱此人";
                case "spare": return "算了，东西不要了，你走吧";
                case "pill": return "不杀此人，喂其丹药";
                case "closed": return "（玩家直接关掉了这一页，按放过算）";
                default: return outcome ?? "";
            }
        }

        /// <summary>
        /// 走**原版处置页自己的那四个回调**（10-27 反编实证）。
        ///
        /// ## 为什么认这一类，而不是之前那个 `BattleNPCFailed`
        /// `UICustomDramaBattleNPCFailed2::GetDramaID`@`0x2EEB290` 是 `mov eax,0x4FB3; ret`
        /// ⇒ 返回 **20403**；`OpenUI`@`0x2EE8F40` 里面就是 `DramaTool::OpenDrama(20403, dramaData)`；
        /// `Init`@`0x2EE99D0` 再逐条 `UICustomDramaBase::OnOptionBackCall`
        /// **0x31D00/01/02/03/04 = 204032/204033/204034/204035/204036**。
        /// 也就是说：本仓开的那一页（配表 `20403.options`）**就是这个类管的那一页**，
        /// 五个选项的原生实现都在这里：
        /// `OnKillClick`@`0x2EEA860` / `OnPassClick`@`0x2EEA9D0` /
        /// `OnPassNotPropClick`@`0x2EEAB00` / `OnInsultClick`@`0x2EEA500` / `OnFeedClick`@`0x2EEA420`。
        /// 之前参考的 `WorldUnitEffectSpecialNpcRelationSurrenderMeet::BattleNPCFailed`@`0x3D67B80`
        /// 是**另一条链**（投降/求饶，自己用 `UICustomDramaDyn` 模板页 22001 搭 4 个选项），
        /// 参数组合与 20403 并不通用（它的"击杀"支还要额外 `mov byte [act+0xc0],1`）。
        ///
        /// ## 三条共用事实（每条都有反编出处）
        /// 1. **接收者恒是"处置者"，不是败者**。四个回调清一色
        ///    `rcx = [[0x3F877C0]()+0x10]`（= 玩家）→ `WorldUnitBase::CreateAction(act, false)`。
        ///    早先写成 `loser.CreateAction(...)` 是**错的**：action 的 `+0x10`（creator）
        ///    会变成败者，缴物 / 仇恨 / 门派日志 / `GetEffects` 全都用错人。
        /// 2. **`CreateAction` 的返回值就是 `IsCreate` 的返回码**（`WorldUnitBase::CreateAction`@`0x32B8D40`：
        ///    `edi = IsCreate(isForce); if (edi != 0) return edi; UnitActionBase::Create(...)`）。
        ///    `0` = 已执行；`1` = 硬失败（creator/target 已死，`0x3BEBC59`）；
        ///    **`2` = `OnParentBless` 已接管**（`0x3BEBC49`）——
        ///    原版语义是"有人出面保下"，动作被 `UnitActionParentBless` 替换掉，
        ///    **不是失败，也绝不能再补一刀**。
        /// 3. `UnitActionRoleKill` 五个参数名由托管签名确定：
        ///    `(WorldUnitBase toUnit, bool isMercy, bool isShowDrame, bool isCatlikeKill, bool isForceDie)`，
        ///    落点 `r8→+0xd0 / r9→+0xd1 / [rsp+0x70]→+0xaa / [rsp+0x78]→+0xd2`（`0x3BEFAA0` 已核）。
        ///
        /// ## 各选项照抄原版（只去掉 UI 侧的 `onEndCall` 钩子）
        /// | 选项 | 原生体 | 出处 |
        /// |---|---|---|
        /// | 204033 击杀 | `new UnitActionRoleKill(u, isMercy:true, isShowDrame:true, isCatlikeKill:false, isForceDie:false)` | `OnKillClick` |
        /// | 204032 收下物品 | `new UnitActionRoleRelease(u, props=页面所示那件, isAddHate:true)` | `OnPassClick` |
        /// | 204034 算了 | `new UnitActionRoleRelease(u, props=空表, isAddHate:false)` | `OnPassNotPropClick` |
        ///
        /// ★ **`isForceDie` 必须 false** ★ `Kill`@`0x3BEBCB0` 的闸门是这个：
        ///   `cmp byte [this+0xd2],0 / jne → UnitActionRoleDie`。
        ///   `isForceDie=true` 走 `UnitActionRoleDie` → `OnCreate` → `WorldUnitMgr::DelUnit`，
        ///   是**瞬删**：没有遗物、没有残魂、没有死亡气运、没有信件。
        ///   原版 204033 两个闸（`+0xd2` / `+0xc0`）都不置 ⇒ 走 `UnitActionRoleDying`@`0x3F1D4C0`：
        ///   `ConfRoleDyingBase::GetItem` + `UnitActionLuckAdd` + `UnitActionRoleSight::OnAdd` + 门派日志。
        ///   也就是 `RoleDying.json` 那一套（`deadFeatureID` 死亡气运 / `ghost`+`ghostLife` 残魂月数 /
        ///   `itemID` 遗物 / `letterID` 信件 / `goalID`）—— **"NPC 到底怎么死的"就在这一层**。
        ///
        /// ★ **空 props 表 = 一件都不拿** ★ `UnitActionRoleRelease::Release`@`0x3ABAAB0` 开头
        ///   `cmp dword [props+0x18],0 / je 0x3ABADD2` 直接跳过整个取物循环。
        ///   早先"传空表让引擎按 `RoleBattle.lostItemCount/Percent` 自己算"的说法是**错的**，已删。
        ///
        /// 任何异常 / 返回码异常都返回 false（`fact` 里带原因），调用方回退到自研实现，
        /// **绝不因为原生失败而什么都不做**。
        /// </summary>
        private static WorldUnitBase VerdictActor()
        {
            try { return g.world == null ? null : g.world.playerUnit; } catch { return null; }
        }

        /// <summary>`CreateAction` 返回码 → 人话（语义见上面第 2 条）。</summary>
        private static string RcText(int rc)
        {
            if (rc == 0) return "0=已执行";
            if (rc == 1) return "1=原生拒绝（_isDie 前置检查没过：creator 或 target 已死）";
            if (rc == 2) return "2=被 OnParentBless 接管（原版语义=有人出面保下，动作被替换，不能再补刀）";
            return rc + "=未知返回码";
        }

        /// <summary>204033 击杀：照抄 `UICustomDramaBattleNPCFailed2::OnKillClick`@`0x2EEA860`。</summary>
        private static bool NativeKill(Watch w, out string fact)
        {
            fact = "";
            var loser = UnitOf(w == null ? "" : w.HoldLoserUid);
            var actor = VerdictActor();
            if (loser == null) { fact = "认不出败者的世界单位（uid=" + (w == null ? "?" : w.HoldLoserUid) + "）"; return false; }
            if (actor == null) { fact = "拿不到 g.world.playerUnit（处置者）"; return false; }
            if (IsPlayer(loser)) { fact = "败者就是玩家，不走原生动作"; return false; }

            bool dieBefore = false;
            try { dieBefore = loser.isDie; } catch { }

            object act;
            try { act = new UnitActionRoleKill(loser, true, true, false, false); }
            catch (Exception e) { fact = "构造 UnitActionRoleKill 抛异常 " + e.GetType().Name + " " + e.Message; return false; }

            int rc;
            try { rc = actor.CreateAction((UnitActionBase)act, false); }
            catch (Exception e) { fact = "CreateAction 抛异常 " + e.GetType().Name + " " + e.Message; return false; }

            bool dieAfter = dieBefore;
            try { dieAfter = loser.isDie; } catch { }

            fact = "处置者=" + NameOf(actor) + "｜CreateAction=" + RcText(rc)
                   + "｜败者 isDie " + dieBefore + "→" + dieAfter
                   + (rc == 0 && !dieAfter ? "（⚠ 未立刻 DelUnit 属正常：原版走 UnitActionRoleDying 死亡处理，不是瞬删）" : "");
            return rc == 0 || rc == 2;
        }

        /// <summary>
        /// 204032 / 204034：照抄 `OnPassClick`@`0x2EEA9D0` / `OnPassNotPropClick`@`0x2EEAB00`。
        /// `useOffer=true` ⇒ props 传"页面上说的那一件"；`false` ⇒ 传空表（一件不拿）。
        /// `addHate`：`OnPassClick` 传 true，`OnPassNotPropClick` 传 false（`Release`@`0x3ABADD2` 才读它）。
        /// </summary>
        private static bool NativeRelease(Watch w, bool useOffer, bool addHate, out string fact)
        {
            fact = "";
            var loser = UnitOf(w == null ? "" : w.HoldLoserUid);
            var actor = VerdictActor();
            if (loser == null) { fact = "认不出败者的世界单位（uid=" + (w == null ? "?" : w.HoldLoserUid) + "）"; return false; }
            if (actor == null) { fact = "拿不到 g.world.playerUnit（处置者）"; return false; }
            if (IsPlayer(loser)) { fact = "败者就是玩家，不走原生动作"; return false; }

            var list = new Il2CppSystem.Collections.Generic.List<DataProps.PropsData>();
            string got = "空表（一件不拿）";
            if (useOffer)
            {
                var prop = w == null || w.VerdictOffer == null ? null : w.VerdictOffer.Prop;
                if (prop == null) { fact = "页面所示献物不是 PropsData（灵石/无可献）⇒ 原生 Release 表达不了"; return false; }
                try { list.Add(prop); got = "props=[sid=" + w.VerdictOffer.PropsId + " 名=" + w.VerdictOffer.Name + " 数=" + w.VerdictOffer.Count + "]"; }
                catch (Exception e) { fact = "往 props 里塞献物失败 " + e.GetType().Name + " " + e.Message; return false; }
            }

            string bagBefore = BagText(loser), moneyBefore = MoneyText(loser);
            object act;
            try { act = new UnitActionRoleRelease(loser, list, addHate); }
            catch (Exception e) { fact = "构造 UnitActionRoleRelease 抛异常 " + e.GetType().Name + " " + e.Message; return false; }

            int rc;
            try { rc = actor.CreateAction((UnitActionBase)act, false); }
            catch (Exception e) { fact = "CreateAction 抛异常 " + e.GetType().Name + " " + e.Message; return false; }

            fact = "处置者=" + NameOf(actor) + "｜" + got + "｜isAddHate=" + addHate
                   + "｜CreateAction=" + RcText(rc)
                   + "｜败者背包 " + bagBefore + "→" + BagText(loser)
                   + "｜灵石 " + moneyBefore + "→" + MoneyText(loser);
            return rc == 0;
        }

        private static string BagText(WorldUnitBase u)
        {
            try
            {
                var dp = u == null ? null : u.data.unitData.propData as DataProps;
                var l = dp == null ? null : dp.allProps;
                return (l == null ? 0 : l.Count) + "件";
            }
            catch { return "?"; }
        }

        private static string MoneyText(WorldUnitBase u)
        {
            try
            {
                var dp = u == null ? null : u.data.unitData.propData as DataProps;
                return dp == null ? "?" : dp.GetPropsNum(MoneyPropsId).ToString();
            }
            catch { return "?"; }
        }

        /// <summary>
        /// 真击杀：`WorldUnitBase.Die()`（公开无参虚方法，
        /// `F:\_dt_dump\types\WorldUnitBase.txt:109` / RVA 0x32BBED0）。
        /// 反编里它走 `DataUnitDie.UnitDie::AddUnitDie` + 两次 `EventsMgr::Emit` + 退宗门动作，
        /// 并在函数前段 `mov BYTE PTR [this+0x78],1`（= `_isDie`）—— 所以**事后回读 `isDie` 是真证据**。
        /// 本仓此前没有 `Die()` 调用先例，这一路必须留"改前→改后"两行日志。
        ///
        /// ⚠ 调用时机是**玩家点下 204033 的那一拍**（页还开着、战斗还被我们拦着）。
        ///   观战副本 `isRealBattle=false` ⇒ 引擎不会因为世界单位死了而自己改战斗状态
        ///   （战场那份 hp/isDie 与结算无关，见类头第 1 条）。真机 10-26 已走过，没炸。
        /// </summary>
        private static string KillLoser(Watch w)
        {
            var u = UnitOf(w == null ? "" : w.HoldLoserUid);
            if (u == null)
            {
                try { ModMain.P(Tag + " ✗ 击杀未落账：认不出败者的世界单位（uid=" + (w == null ? "?" : w.HoldLoserUid) + "）"); } catch { }
                return "击杀=未落账（认不出败者的世界单位）";
            }
            bool before = false;
            try { before = u.isDie; } catch { }
            try
            {
                u.Die();
                try
                {
                    ModMain.P(Tag + " ★已调用 WorldUnitBase.Die()：败者=" + w.HoldLoserName
                              + "(" + w.HoldLoserUid + ")★");
                }
                catch { }
            }
            catch (Exception e)
            {
                try { ModMain.P(Tag + " ✗ Die() 抛异常 → 击杀未落账: " + e.GetType().Name + " " + e.Message); } catch { }
                return "击杀=失败（Die() 抛异常：" + e.GetType().Name + "）";
            }
            // ★ 事后核对（用户 10-26 要求）：读**世界单位**自己的 isDie，改前/改后都打出来 ★
            bool after = before;
            try { after = u.isDie; } catch { }
            try
            {
                ModMain.P(Tag + " Die() 事后核对：败者=" + w.HoldLoserName + "(" + w.HoldLoserUid + ")"
                          + " 世界单位.isDie 改前=" + before + " → 改后=" + after
                          + (after ? "（已死亡 = 击杀真的落了账）" : "（⚠ 读回仍是 false，击杀可能没落账）"));
            }
            catch { }
            DiagLine(w, "Die() 事后核对：世界单位.isDie 改前=" + before + " → 改后=" + after);
            return "击杀=Die() 已调，isDie " + before + "→" + after + (after ? "（已死）" : "（⚠未确认）");
        }

        // ══════════════════════════════════════════════════════════
        //  取物（204032 / 204035）：从败者 propData 转到玩家 propData
        //
        //  口径（用户 10-26 定，别自由发挥）：
        //    ① 优先从 `allProps` 里挑**一件可交易物品**（跳过灵石 sid=10001）；
        //       "可交易"= 仓里赠送/交易同一条 `UnitActionRoleGive.PropsItemCanGive`；
        //    ② 没物品就取灵石：`GetPropsNum(10001)/2` 向下取整、最少 1；
        //    ③ 什么都没有 ⇒ 只叙事，日志写"他身上什么都没有"。
        //
        //  落刀 API（互操作程序集实证，`DataProps`）：
        //    `DelProps(String soleID, Int32 count) -> PropsData`（引擎拆栈，返回独立实例）
        //    `AddProps(PropsData) -> List<PropsData>`
        //    `GetPropsNum(Int32|String) -> Int32`
        //    灵石走 `WorldUnitData.CostPropItem(10001,n,false)` + `RewardPropMoney(n)`
        //    （与 `ToolExecutor.TransferLingshi` 同一条原生路，带补偿回滚）。
        //  **全程 try/catch：失败只记日志、绝不抛**（放行不能被取物失败影响）。
        // ══════════════════════════════════════════════════════════

        /// <summary>开页时算"他会献什么"（只读；见上面口径）。异常一律降级成 `Has=false`。</summary>
        private static Offer PlanOffer(WorldUnitBase loser, WorldUnitBase player)
        {
            var o = new Offer();
            try
            {
                var dp = loser == null ? null : loser.data.unitData.propData as DataProps;
                if (dp == null) { o.Why = "败者背包不可用（propData 转型失败）"; return o; }

                Il2CppSystem.Collections.Generic.List<DataProps.PropsData> list = null;
                try { list = dp.allProps; } catch (Exception e) { o.Why = "读 allProps 失败：" + e.Message; }
                int n = 0;
                try { n = list == null ? 0 : list.Count; } catch { }
                for (int i = 0; i < n; i++)
                {
                    DataProps.PropsData p = null;
                    try { p = list[i]; } catch { continue; }
                    if (p == null) continue;
                    int pid = -1;
                    try { pid = p.propsID; } catch { continue; }
                    if (pid == MoneyPropsId) continue;              // 灵石走"取 50%"，不当物品挑
                    if (!PropsCanGive(loser, player, p, pid)) continue;
                    string sole = "";
                    try { sole = p.soleID ?? ""; } catch { }
                    if (string.IsNullOrEmpty(sole)) continue;       // 无 soleID 无法安全拆栈
                    int cnt = 1;
                    try { cnt = p.propsCount; } catch { }
                    if (cnt < 1) cnt = 1;
                    string nm = "";
                    try { nm = GameTool.LS(p.propsItem.name) ?? ""; } catch { }
                    if (string.IsNullOrEmpty(nm)) { try { nm = p.propsItem.name ?? ""; } catch { } }
                    if (string.IsNullOrEmpty(nm)) nm = "sid=" + pid;
                    o.Has = true; o.IsMoney = false; o.PropsId = pid; o.SoleId = sole;
                    o.Count = cnt; o.Name = nm; o.Display = nm;
                    o.Prop = p;                                  // ← 原生 Release 要用这一件
                    return o;
                }

                int money = -1;
                try { money = dp.GetPropsNum(MoneyPropsId); } catch { }
                if (money > 0)
                {
                    int take = money / 2;
                    if (take < 1) take = 1;
                    o.Has = true; o.IsMoney = true; o.PropsId = MoneyPropsId; o.Count = take;
                    o.Name = "灵石"; o.Display = "灵石×" + take;
                    return o;
                }
                o.Why = money < 0 ? "读不到他身上有多少灵石（GetPropsNum 失败）"
                                  : "他身上什么都没有（无可交易物品、灵石=0）";
                return o;
            }
            catch (Exception e)
            {
                o.Has = false;
                o.Why = "规划取物异常：" + e.GetType().Name + " " + e.Message;
                return o;
            }
        }

        /// <summary>"可交易"判据 = `UnitActionRoleGive.PropsItemCanGive`（赠送/交易同一条）。
        /// 校验自己抛异常时**不阻断**（真正的拆栈另有 try/catch）——与 `ToolExecutor.TakePropsFromBag` 同口径。</summary>
        private static bool PropsCanGive(WorldUnitBase loser, WorldUnitBase player, DataProps.PropsData p, int pid)
        {
            try { return UnitActionRoleGive.PropsItemCanGive(loser, player, p); }
            catch (Exception e)
            {
                try { ModMain.P(Tag + " 可交易校验抛异常（按可交易处理）sid=" + pid + ": " + e.GetType().Name + " " + e.Message); } catch { }
                return true;
            }
        }

        /// <summary>日志/诊断里的献物描述。</summary>
        private static string DescribeOffer(Offer o)
        {
            if (o == null) return "（未规划）";
            if (!o.Has) return "无（" + o.Why + "）";
            if (o.IsMoney) return "灵石×" + o.Count + "（sid=" + MoneyPropsId + "，取 50%）";
            return o.Name + "×" + o.Count + "（sid=" + o.PropsId + "）";
        }

        /// <summary>落刀：把开页时规划的那件东西从败者背包转到玩家背包。返回一句可核对的事实。</summary>
        private static string TakeOffer(Watch w)
        {
            var o = w == null ? null : w.VerdictOffer;
            if (o == null || !o.Has)
                return "取物=无（" + (o == null ? "开页时没规划" : o.Why) + "）";

            var loser = UnitOf(w.HoldLoserUid);
            var player = g.world != null ? g.world.playerUnit : null;
            if (loser == null) return "取物=未落账（认不出败者的世界单位 uid=" + w.HoldLoserUid + "）";
            if (player == null) return "取物=未落账（playerUnit 为空）";

            try
            {
                var ldp = loser.data.unitData.propData as DataProps;
                var pdp = player.data.unitData.propData as DataProps;
                if (ldp == null) { LogTakeFail(w, "败者背包不可用（propData 转型失败）"); return "取物=未落账（败者背包不可用）"; }
                if (pdp == null) { LogTakeFail(w, "玩家背包不可用（propData 转型失败）"); return "取物=未落账（玩家背包不可用）"; }
                return o.IsMoney ? TakeMoney(w, loser, player, ldp, pdp, o)
                                 : TakeItem(w, loser, player, ldp, pdp, o);
            }
            catch (Exception e)
            {
                LogTakeFail(w, "取物异常 " + e.GetType().Name + " " + e.Message);
                return "取物=异常（" + e.GetType().Name + "，" + e.Message + "）";
            }
        }

        private static void LogTakeFail(Watch w, string msg)
        {
            try { ModMain.P(Tag + " ✗ 取物失败：" + msg + "｜败者=" + (w == null ? "?" : w.HoldLoserName)); } catch { }
            DiagLine(w, "✗ 取物失败：" + msg);
        }

        private static string TakeItem(Watch w, WorldUnitBase loser, WorldUnitBase player,
                                       DataProps ldp, DataProps pdp, Offer o)
        {
            int haveBefore = -1;
            try { haveBefore = pdp.GetPropsNum(o.PropsId); } catch { }

            DataProps.PropsData taken = null;
            try { taken = ldp.DelProps(o.SoleId, o.Count); }
            catch (Exception e)
            {
                LogTakeFail(w, "DelProps 抛异常 soleID=" + o.SoleId + " sid=" + o.PropsId + "：" + e.GetType().Name + " " + e.Message);
                return "取物=未落账（扣除异常）";
            }
            if (taken == null)
            {
                LogTakeFail(w, "DelProps 返回 null（物品可能已不在身上）soleID=" + o.SoleId + " sid=" + o.PropsId + " 数量=" + o.Count);
                return "取物=未落账（物品已不在身上）";
            }
            int realCnt = o.Count;
            try { if (taken.propsCount > 0) realCnt = taken.propsCount; } catch { }

            try { pdp.AddProps(taken); }
            catch (Exception e)
            {
                bool rolled = false;
                try { ldp.AddProps(taken); rolled = true; } catch { }
                LogTakeFail(w, "入玩家背包抛异常（原样退回=" + rolled + "）sid=" + o.PropsId + "：" + e.GetType().Name + " " + e.Message);
                return "取物=未落账（入包异常，已回滚）";
            }

            int haveAfter = -1, leftAfter = -1;
            try { haveAfter = pdp.GetPropsNum(o.PropsId); } catch { }
            try { leftAfter = ldp.GetPropsNum(o.PropsId); } catch { }
            try
            {
                ModMain.P(Tag + " ★取物完成：败者=" + w.HoldLoserName + " → 玩家=" + NameOf(player)
                          + "｜" + o.Name + "×" + realCnt + "（sid=" + o.PropsId + " soleID=" + o.SoleId + "）"
                          + "｜玩家持有 " + haveBefore + "→" + haveAfter
                          + "｜败者剩 " + leftAfter + "★");
            }
            catch { }
            DiagLine(w, "取物完成：sid=" + o.PropsId + " 数量=" + realCnt
                        + " 玩家持有 " + haveBefore + "→" + haveAfter + " 败者剩 " + leftAfter);
            return "取物=" + o.Name + "×" + realCnt + "（sid=" + o.PropsId + "）";
        }

        private static string TakeMoney(Watch w, WorldUnitBase loser, WorldUnitBase player,
                                        DataProps ldp, DataProps pdp, Offer o)
        {
            int have = -1;
            try { have = ldp.GetPropsNum(MoneyPropsId); } catch { }
            int take = o.Count;
            if (have >= 0 && take > have) take = have;
            if (take <= 0)
            {
                LogTakeFail(w, "灵石余额不足（读到 " + have + "，计划取 " + o.Count + "）");
                return "取物=未落账（灵石不足）";
            }
            int playerBefore = -1;
            try { playerBefore = pdp.GetPropsNum(MoneyPropsId); } catch { }
            try
            {
                // 与 `ToolExecutor.TransferLingshi` 同一条原生路：扣货币 + 加货币（玩家收失败则退回败者）
                loser.data.CostPropItem(MoneyPropsId, take, false);
                try { player.data.RewardPropMoney(take); }
                catch (Exception e2)
                {
                    try { loser.data.RewardPropMoney(take); } catch { }
                    throw e2;
                }
            }
            catch (Exception e)
            {
                LogTakeFail(w, "灵石转账异常 sid=" + MoneyPropsId + " 数量=" + take + "：" + e.GetType().Name + " " + e.Message);
                return "取物=未落账（灵石转账异常）";
            }
            int playerAfter = -1, loserAfter = -1;
            try { playerAfter = pdp.GetPropsNum(MoneyPropsId); } catch { }
            try { loserAfter = ldp.GetPropsNum(MoneyPropsId); } catch { }
            try
            {
                ModMain.P(Tag + " ★取物完成：败者=" + w.HoldLoserName + " → 玩家=" + NameOf(player)
                          + "｜灵石×" + take + "（sid=" + MoneyPropsId + "，取 50%：持有 " + have + "/2）"
                          + "｜玩家 " + playerBefore + "→" + playerAfter
                          + "｜败者 " + have + "→" + loserAfter + "★");
            }
            catch { }
            DiagLine(w, "取物完成：灵石×" + take + " 玩家 " + playerBefore + "→" + playerAfter
                        + " 败者 " + have + "→" + loserAfter);
            return "取物=灵石×" + take + "（sid=" + MoneyPropsId + "）";
        }

        /// <summary>
        /// 「羞辱」（204035）：`败者.relationData.AddHate(玩家unitID, RoleBattle.abuseAddHate)`
        /// ＋ 按配表概率挂两条负面气运。**不再 SetIntim**。
        ///
        /// ## 签名来源（互操作程序集反射实证，不是猜）
        /// `DataUnit+RelationData`：
        ///   `AddHate(String unitID, Single value, Int32 clampNewIntim, String uiTip, Boolean isTip)`
        ///   `SetIntim(String unitID, Single value)`
        /// 反编：`AddHate`@0x3CEEEB0（内部按 `RoleClose` 分档系数换算后 `SetIntim`）。
        ///
        /// ## 方向（本仓既有口径）
        /// `IntimService.ApplyIntim`：正走 `AddIntim`、负走 `AddHate(-delta)`；
        /// 配表 `RoleKill.hateAddKill=500`（杀人加仇恨）。⇒ **正数 AddHate = 仇恨↑ / 好感↓**。
        ///
        /// ## 数值全部来自配表（10-27 改，勿再写死）
        /// 原版「羞辱」的效果链是 `UnitActionRoleBattle::InsultUnit` 的闭包
        /// `<>c__DisplayClass46_0::<InsultUnit>b__1`@0x3ADB470：
        /// ```
        /// Random(abuseMaxhpDecMin, abuseMaxhpDecMax)   → TaoistHeart::AddHeartEffectHP   （体力上限）
        /// propertyData -= abuseReputationDecMin                                            （声望）
        /// relationData::AddHate(玩家, abuseAddHate)                                        （仇恨）
        /// abuseAddFeature1/2 按 abuseUpFeatureProbability1/2 概率各挂一条（意志消沉/愁肠百结）
        /// ```
        /// 对应 `RoleBattle.json`：`abuseMaxhpDecMin/Max=25/40`、`abuseReputationDecMin=500`、
        /// `abuseAddHate=300`、`abuseAddFeature1/2=9001101/9001102`、`abuseUpFeatureProbability1/2=50`。
        ///
        /// ⚠ **不再 `SetIntim`**：10-15 已定案"换算交引擎、只回读实得"，
        ///   而 `AddHate` 内部就按 `RoleClose` 分档系数换算（当前好感 180+ ⇒ `hateIncrease=12.5%`，
        ///   所以 `AddHate(300)` 实得约 +37 是**正确行为**，不是失败）。写绝对值会绕过这张表。
        ///
        /// ## 原版 204035 的真正入口（10-27 补证，别再找错地方）
        /// `UICustomDramaBattleNPCFailed2::OnInsultClick`@`0x2EEA500` 的正文只有三段：
        /// ```
        /// WorldUnitData::CostPropItem(loser, ..., 1)   // 0x32C0680：从败者身上扣
        /// WorldUnitData::RewardPropItem(player, ..., 1)// 0x32C4660：给玩家      （= 缴物那半）
        /// UnitActionRoleBattle::InsultUnit(win, fail, this.onEndCall)  // 0x3708090
        /// ```
        /// `InsultUnit` 才是体力上限/声望/仇恨的真身（闭包 `0x3ADB470`）。
        ///
        /// ## 现在的分工（10-27 委托探针之后）
        /// **主路** = `NativeInsult()` 直接调 `InsultUnit`（体力上限/声望/`AddHate(abuseAddHate)` 全交给它），
        /// 气运那两条由 `ApplyAbuseLuck()` 补（原生全链没有一处 `AddLuck`）。
        /// **本函数只在原生 `InsultUnit` 抛异常时兜底**：`AddHate` + 气运，
        /// 此时体力上限/声望**确实没落**，结果串里如实写"未落"，不假装。
        /// </summary>
        private static string Humiliate(Watch w)
        {
            var loser = UnitOf(w == null ? "" : w.HoldLoserUid);
            var player = g.world != null ? g.world.playerUnit : null;
            if (loser == null) return "羞辱=未落账（认不出败者世界单位）";
            if (player == null) return "羞辱=未落账（playerUnit 为空）";
            string puid = UidOf(player);
            if (string.IsNullOrEmpty(puid)) return "羞辱=未落账（读不到玩家 unitID）";

            DataUnit.RelationData rel = null;
            try { rel = loser.data.unitData.relationData; } catch { }
            if (rel == null) return "羞辱=未落账（败者 relationData 为空）";

            // ── 配表数值（读不到才用字面量，并写进结果）──
            int addHate = InsultHate; string src = "兜底字面量";
            try
            {
                var rb = g.conf != null ? g.conf.roleBattle : null;
                if (rb != null) { addHate = rb.abuseAddHate; src = "RoleBattle.abuseAddHate"; }
            }
            catch (Exception e) { src = "读表异常 " + e.GetType().Name; }
            if (addHate <= 0) { addHate = InsultHate; src = "兜底字面量（读表失败：" + src + "）"; }

            int before = -1;
            try { before = rel.GetIntim(player); } catch { }

            // ★走共用的写入层 `IntimService.ApplyIntim`（负 delta = AddHate）★
            //   `test_intim_wiring_contract` 钉死"全仓只有 ApplyIntim 能写好感"：
            //   散开写 = 各路径自己发明方向与值。这里原本直接 `rel.AddHate(...)`，
            //   是 10-26 写这段时绕过了那条约定，10-27 收回来。
            //   `ApplyIntim` 内部就是 `AddHate(oid, -delta, 0, "", true)`，与原来逐字等价，
            //   另外自带 before/after 与"引擎按 RoleClose 系数换算"的日志。
            string err = "";
            try { IntimService.ApplyIntim(loser, player, -addHate, "羞辱"); }
            catch (Exception e) { err = "ApplyIntim异常:" + e.GetType().Name + " " + e.Message; }

            int after = before;
            try { after = rel.GetIntim(player); } catch { }

            string luckLog = ApplyAbuseLuck(w);

            int delta = (before >= 0 && after >= 0) ? (after - before) : 0;
            try
            {
                ModMain.P(Tag + " ★羞辱：败者=" + w.HoldLoserName + " 对玩家(" + puid + ") "
                          + "AddHate 请求+" + addHate + "，来自 " + src + " 好感 " + before + "→" + after
                          + "（实得Δ=" + delta + "；`RoleClose` 按当前好感档位换算，属预期）"
                          + luckLog
                          + "｜体力上限/声望=本支没走原生 InsultUnit，未落"
                          + (err.Length > 0 ? "｜⚠ " + err : "") + "★");
            }
            catch { }
            DiagLine(w, "羞辱：AddHate +" + addHate + " 好感 " + before + "→" + after + luckLog);
            return "羞辱=AddHate+" + addHate + "(" + src + ")（好感 " + before + "→" + after
                   + (err.Length > 0 ? "，有异常" : "") + "）" + luckLog;
        }


        /// <summary>
        /// 羞辱那两条负面气运（`RoleBattle.abuseAddFeature1/2` + `abuseUpFeatureProbability1/2`）。
        /// 单独成函数有两个原因：
        ///   ① 原生 `UnitActionRoleBattle::InsultUnit`@`0x3708090` **只管**体力上限/声望/仇恨
        ///      （`TaoistHeart::AddHeartEffectHP` + `PropertyData -=` + `AddHate`），
        ///      全链没有一处 `UnitActionLuckAdd`/`AddLuck` —— 气运是配表这边的事，得我们补；
        ///   ② 原生一旦成功，`Humiliate` 里的 `AddHate` 就必须**跳过**（否则仇恨挂两遍）。
        /// </summary>
        private static string ApplyAbuseLuck(Watch w)
        {
            var loser = UnitOf(w == null ? "" : w.HoldLoserUid);
            if (loser == null) return "｜气运=未落账（认不出败者）";
            int f1 = 0, f2 = 0, p1 = 50, p2 = 50;
            try
            {
                var rb = g.conf != null ? g.conf.roleBattle : null;
                if (rb != null)
                {
                    f1 = rb.abuseAddFeature1; f2 = rb.abuseAddFeature2;
                    p1 = rb.abuseUpFeatureProbability1; p2 = rb.abuseUpFeatureProbability2;
                }
            }
            catch { }
            string luckLog = "";
            try
            {
                if (f1 > 0 && UnityEngine.Random.Range(0, 100) < p1)
                {
                    string e1;
                    bool ok1 = TeamTool.AddLuck(loser, f1, out e1);
                    luckLog += "｜气运" + f1 + (ok1 ? "已挂" : "未挂(" + e1 + ")");
                }
                else if (f1 > 0) luckLog += "｜气运" + f1 + "未中签(" + p1 + "%)";

                if (f2 > 0 && UnityEngine.Random.Range(0, 100) < p2)
                {
                    string e2;
                    bool ok2 = TeamTool.AddLuck(loser, f2, out e2);
                    luckLog += "｜气运" + f2 + (ok2 ? "已挂" : "未挂(" + e2 + ")");
                }
                else if (f2 > 0) luckLog += "｜气运" + f2 + "未中签(" + p2 + "%)";
            }
            catch (Exception e) { luckLog += "｜气运异常 " + e.GetType().Name; }
            return luckLog;
        }

        /// <summary>
        /// 用**游戏原生**的处置页：`new UICustomDramaBattleNPCFailed2(props, 败者) { isWaitKillEffect=false }.OpenUI()`。
        ///
        /// ## 依据（全是读过原文的，不是猜）
        /// - 类 = 20403 的原生实现：`GetDramaID`@`0x2EEB290` 是 `mov eax,0x4FB3; ret`；
        ///   `UICustomDramaBase::OpenUI`@`0x2EE8F40` 里 `Init()` → `DramaTool::OpenDrama(id, [this+0x18])`
        ///   → `GameTool::RunInMainThread(...)`。
        /// - 构造：`.ctor(List&lt;PropsData&gt; props, WorldUnitBase npcUnit)`（托管反射实证）；
        ///   基类 ctor 建 `DramaData` + 两个 `Dictionary`（`0x2EE92E0` → `DramaData::.ctor`@`0x223A710`），
        ///   派生 ctor 再 `0x223AB70(dramaData, npcUnit, 0)` 把败者写进 `+0x10`/`+0x18`。
        /// - `Init`@`0x2EE99D0` 只读 `this+0x18`(dd) / `+0x30`(props) / `+0x44` / `+0x48`，
        ///   **不读 `onEndCall`(+0x38)** ⇒ 不给回调也能开。
        /// - `isWaitKillEffect`(+0x40) 只被 `OnKillClick` 读一次：`0` ⇒ 收尾回调直接交给 action，
        ///   `非0` ⇒ 攒进 `action+0xb8` 等"击杀特效放完"。我们这场是观战副本、**没有**击杀特效
        ///   ⇒ 必须 `false`，否则那条链永远等不到。
        /// - 委托探针（真机 `Player.log`）证实 `onEndCall` 就是 `Il2CppSystem.Action`，
        ///   可经 `UnhollowerRuntimeLib.DelegateSupport.ConvertDelegate&lt;T&gt;` 构造（本仓 `ToolExecutor` 已用）。
        ///
        /// ## 传给它的 props
        /// 只放**页面上说的那一件**（`w.VerdictOffer.Prop`）；灵石不是 `PropsData` ⇒ 空表。
        /// 原版这里是 `UnitActionFeedback103::GetBegItem(...)` 的整张表，我们还没搞清那个接收者是谁
        /// （它和 `WorldUnitBase::CreateAction` 的接收者是同一个对象，而那两处 API 归属矛盾），
        /// 所以先老实传一件，并在日志里写明"props=N件"。
        /// </summary>
        private static bool TryOpenNativePage(Watch w, WorldUnitBase player, WorldUnitBase loser, out string why)
        {
            why = "";
            try
            {
                // ══════════════════════════════════════════════════════════
                //  props 用**引擎自己的算法**：`UnitActionFeedback103::GetBegItem`
                //  托管反射实证（10-27）：`public static List<PropsData> GetBegItem(WorldUnitBase battleUnit, WorldUnitBase unit)`
                //  ⇒ 之前"它和 `CreateAction` 接收者冲突"的疑虑不成立 —— 它是 static，
                //     `rcx` 就是第一个参数（= `[[0x3F877C0]()+0x10]` = 玩家），两处都成立。
                //  `NpcBegMercy`@`0x2907DE9` 与 `BatlePlayerWin` 都是拿它填 ctor 的 props，
                //  所以这一步由我们**调**，但**算法是原版的**。
                //  ══════════════════════════════════════════════════════════
                Il2CppSystem.Collections.Generic.List<DataProps.PropsData> props = null;
                string got;
                try
                {
                    props = UnitActionFeedback103.GetBegItem(player, loser);
                    got = props == null ? "引擎 GetBegItem=null"
                                        : "引擎 GetBegItem=" + props.Count + " 件";
                }
                catch (Exception e)
                {
                    props = null;
                    got = "引擎 GetBegItem 抛 " + e.GetType().Name;
                }
                if (props == null) props = new Il2CppSystem.Collections.Generic.List<DataProps.PropsData>();
                if (props.Count == 0 && w != null && w.VerdictOffer != null && w.VerdictOffer.Prop != null)
                {
                    props.Add(w.VerdictOffer.Prop);
                    got += "→引擎没给，退回 PlanOffer 1 件（sid=" + w.VerdictOffer.PropsId
                           + " 名=" + w.VerdictOffer.Name + " 数=" + w.VerdictOffer.Count + "）";
                }

                var ui = new UICustomDramaBattleNPCFailed2(props, loser);
                ui.isWaitKillEffect = false;
                string cbNote = "onEndCall=未设（Init 不读它）";
                try
                {
                    ui.onEndCall = UnhollowerRuntimeLib.DelegateSupport.ConvertDelegate<Il2CppSystem.Action>(
                        new System.Action(() =>
                        {
                            // ★这是原生模式的**主放行信号**★（10-27 真机实证就是这个顺序：
                            //   16:26:32 玩家点「羞辱」→ 1450005 羞辱页 → 1450006 回嘴页
                            //   → 16:26:37.829 本回调 → 之后页面才真正全关）。
                            //   别在这里放行（`ReleaseHold`）—— 回调发生在引擎收尾链内部，
                            //   此刻调 `BattleEnd` 会重入它自己那条链；只置位，交给 `HoldTick` 下一拍。
                            try
                            {
                                if (w != null) w.NativeEnded = true;
                                ModMain.P(Tag + " 原生处置页 onEndCall 触发（引擎自己的收尾链；下一拍放行）");
                            }
                            catch { }
                        }));
                    cbNote = "onEndCall=已设(Il2CppSystem.Action，兼作放行信号)";
                }
                catch (Exception e) { cbNote = "onEndCall 设失败（继续开）:" + e.GetType().Name; }

                ui.OpenUI();
                // ★不持有实例★ `UICustomDramaBattleNPCFailed2` 是**控制器**不是窗口
                //   （基链止于 `Il2CppObjectBase`，没有 gameObject），留着也没法判可见。
                //   判"还开着吗"改为按名字取 `UIDramaDialogue`，见 `NativePageAlive`。
                why = "new UICustomDramaBattleNPCFailed2(props=" + got + "，败者=" + NameOf(loser)
                      + ")｜isWaitKillEffect=false｜" + cbNote + "｜OpenUI() 已调";
                return true;
            }
            catch (Exception e)
            {
                why = "原生页开不出来 " + e.GetType().Name + " " + e.Message;
                return false;
            }
        }

        /// <summary>
        /// 原生页模式下**唯一的验收手段**：放行前把能观测到的事实打一行。
        /// 原生回调要是没执行（引擎没把点击派发到我们注册不上的那条链），
        /// 这里就会显示"背包没少 / isDie 没变 / 好感没动"——一眼就能判，不用猜。
        /// </summary>
        private static string NativeVerdictFacts(Watch w)
        {
            try
            {
                var loser = UnitOf(w == null ? "" : w.HoldLoserUid);
                if (loser == null) return "原生页收尾核对：认不出败者";
                string die = "?";
                try { die = loser.isDie.ToString(); } catch { }
                string intim = "?";
                try
                {
                    var rel = loser.data.unitData.relationData;
                    var pl = VerdictActor();
                    if (rel != null && pl != null) intim = rel.GetIntim(pl).ToString();
                }
                catch { }
                return "原生页收尾核对（结局=" + (w == null ? "?" : w.Outcome) + "）：败者=" + NameOf(loser)
                       + "｜isDie=" + die + "｜背包=" + BagText(loser) + "｜败者灵石=" + MoneyText(loser)
                       + "｜玩家灵石=" + MoneyText(VerdictActor()) + "｜好感=" + intim;
            }
            catch (Exception e) { return "原生页收尾核对异常 " + e.GetType().Name; }
        }

        /// <summary>
        /// 204035 羞辱的**原生本体**：`UnitActionRoleBattle::InsultUnit`@`0x3708090`。
        ///
        /// ## 为什么现在能调了（10-27 委托探针给的答案，不再是猜）
        /// 探针在真进程里读到（`Player.log` 搜 `★委托探针★`）：
        /// ```
        /// ⑤ InsultUnit 静态=True｜参数=WorldUnitBase winUnit, WorldUnitBase failUnit, Il2CppSystem.Action call
        /// ③ Il2CppSystem.Action 可读；是 C# 委托=False；ctor(Il2CppSystem.Object, System.IntPtr)
        /// ④ UnhollowerBaseLib :: UnhollowerRuntimeLib.DelegateSupport → ConvertDelegate/1
        /// ```
        /// ⇒ 参数类型就是 `Il2CppSystem.Action`，而本仓 `ToolExecutor` 早就在用
        ///   `UnhollowerRuntimeLib.DelegateSupport.ConvertDelegate<Il2CppSystem.Action<bool>>(...)`
        ///   把托管 `Action` 换成 Il2Cpp 委托，**这条路是本仓已验证过的**。
        ///   （PowerShell 侧 `GetParameters()` 抛 `TypeLoadException` 是**探针工具**的限制，
        ///    不是这个类型真的拿不到 —— 之前据此下的结论是错的，已改。）
        ///
        /// ## 参数语义
        /// `OnInsultClick`@`0x2EEA500` 的调用是 `InsultUnit([玩家], [败者], this.onEndCall)`
        /// ⇒ `winUnit=玩家`、`failUnit=败者`、`call=收尾回调`。
        ///
        /// ## 它替我们做了哪些（`<>c__DisplayClass46_0::<InsultUnit>b__1`@`0x3ADB470`）
        /// `Random(abuseMaxhpDecMin, abuseMaxhpDecMax)` → `TaoistHeart::AddHeartEffectHP`（体力上限）
        /// ＋ 属性 `-= abuseReputationDecMin`（声望）＋ `relationData::AddHate(玩家, abuseAddHate)`（仇恨）。
        /// **不再调用 `Humiliate` 的 `AddHate`**，否则仇恨挂两遍。
        /// </summary>
        private static bool NativeInsult(Watch w, out string fact)
        {
            fact = "";
            var loser = UnitOf(w == null ? "" : w.HoldLoserUid);
            var actor = VerdictActor();
            if (loser == null) { fact = "认不出败者的世界单位（uid=" + (w == null ? "?" : w.HoldLoserUid) + "）"; return false; }
            if (actor == null) { fact = "拿不到 g.world.playerUnit（胜者/处置者）"; return false; }
            if (IsPlayer(loser)) { fact = "败者就是玩家，不走原生羞辱"; return false; }

            Il2CppSystem.Action cb;
            try
            {
                cb = UnhollowerRuntimeLib.DelegateSupport.ConvertDelegate<Il2CppSystem.Action>(
                    new System.Action(() =>
                    {
                        try { ModMain.P(Tag + " 原生 InsultUnit 收尾回调触发（羞辱特效/飘字走完）"); } catch { }
                    }));
            }
            catch (Exception e) { fact = "造 Il2CppSystem.Action 失败 " + e.GetType().Name + " " + e.Message; return false; }

            try { UnitActionRoleBattle.InsultUnit(actor, loser, cb); }
            catch (Exception e) { fact = "调 InsultUnit 抛 " + e.GetType().Name + " " + e.Message; return false; }

            fact = "羞辱=原生 UnitActionRoleBattle.InsultUnit(win=" + NameOf(actor) + ", fail=" + NameOf(loser)
                   + ", call=Action)（体力上限 abuseMaxhpDec、声望 abuseReputationDec、AddHate abuseAddHate 由它落；"
                   + "气运不走它，见后）";
            return true;
        }

        /// <summary>204036 的"我们这边做了什么"（原生链是否具备跑起来的条件）。</summary>
        private static string PillEffectText(Watch w)
        {
            if (w == null) return "选物/好感=原生（无台账）";
            string fd = w.PillFunctionDataFilled
                ? "functionData 已填(unitLeft=玩家, unitA=败者) ⇒ 28001 的 addClose_1_0 原生可跑"
                : "functionData 未填(" + w.PillFunctionDataWhy + ") ⇒ 好感未生效";
            return "选物=原生 itemSelectNotCost；" + fd + "；后续 28001/28101 由引擎自己走（不拦）";
        }

        /// <summary>选项编号（读法与 `DramaSegmentProbe.Describe` 同源：强类型优先，失败退 dynamic）。</summary>
        private static int OptionIdOf(ConfDramaOptionsItem opt)
        {
            if (opt == null) return 0;
            try { return (int)opt.id; }
            catch { try { return (int)((dynamic)opt).id; } catch { return 0; } }
        }

        /// <summary>选项文案（`ConfDramaOptionsItem.text` 是 **LocalText 键**，不是中文：
        /// 204035 的原文是 `drama_option204035`，要靠 `GameTool.LS` 才解析成"收下物品，羞辱此人"。
        /// 解析失败就退回原键 —— 不编。）</summary>
        private static string OptionTextOf(ConfDramaOptionsItem opt)
        {
            if (opt == null) return "";
            string raw = "";
            try { raw = opt.text ?? ""; }
            catch { try { raw = (string)((dynamic)opt).text ?? ""; } catch { return ""; } }
            if (string.IsNullOrEmpty(raw)) return "";
            try
            {
                string ls = GameTool.LS(raw);
                if (!string.IsNullOrEmpty(ls)) return ls;
            }
            catch { }
            return raw;
        }

        // ══════════════════════════════════════════════════════════
        //  内部
        // ══════════════════════════════════════════════════════════

        /// <summary>
        /// 抓"这一场谁倒了"的快照。顺序 = 先战场单位（引擎事实），战场读不到才退世界单位。
        /// 两处都读不到 ⇒ 什么都不记（`*Seen` 保持 false），结果里就不出现 `alive`/`winner`。
        /// </summary>
        private static void Snapshot(Watch w)
        {
            try
            {
                string rosterDiag;
                var roster = BattleSceneFacts.Roster(out rosterDiag);
                w.RosterCount = roster == null ? -1 : roster.Count;
                DiagLine(w, "战场名单：" + rosterDiag + " → " + BattleSceneFacts.Describe(roster));

                // ★ 判"打输了"用的是**战场单位的血量**，不是 `isDie` ★
                //   10-26 真机（49.6s 那场，观战副本 4001）：姜萌战场 hp 打到 0，`isDie` 仍是 False
                //   —— 因为 `isRealBattle=False`（`WorldBattleData.InitData` 对 type=79 显式置假，
                //   见 01 §F9.4），这种"非真实战斗"里 0 血 = 判负，不置死亡位。
                //   反过来说：`isDie` 在观战里**永远不会为真**，拿它当胜负判据必然恒"未定"。
                //   两个都认（`isDie || hp==0`）—— 将来的其他副本里若真会死人，也照收。
                bool sceneA = false, sceneB = false;
                int sceneHpA = -1, sceneHpB = -1;
                if (roster != null)
                {
                    var a = BattleSceneFacts.Match(roster, w.ActorUid, w.ActorName);
                    var b = BattleSceneFacts.Match(roster, w.TargetUid, w.TargetName);
                    if (a != null)
                    {
                        sceneA = true; w.ActorSeen = true;
                        w.ActorDead = a.IsDie || (a.Hp == 0 && a.HpMax > 0);
                        sceneHpA = a.Hp;
                        if (a.Hp >= 0) { w.ActorHp = a.Hp; w.ActorHpMax = a.HpMax; }
                    }
                    if (b != null)
                    {
                        sceneB = true; w.TargetSeen = true;
                        w.TargetDead = b.IsDie || (b.Hp == 0 && b.HpMax > 0);
                        sceneHpB = b.Hp;
                        if (b.Hp >= 0) { w.TargetHp = b.Hp; w.TargetHpMax = b.HpMax; }
                    }
                    if (a != null || b != null) w.Evidence = "战场单位";
                    // 玩家在不在场上 —— 只进诊断（结果里由 `spectator` 表达"玩家在旁观"）
                    try
                    {
                        string puid = "";
                        if (g.world != null && g.world.playerUnit != null)
                            puid = g.world.playerUnit.data.unitData.unitID ?? "";
                        w.PlayerOnField = BattleSceneFacts.Match(roster, puid, w.PlayerName) != null;
                    }
                    catch { }
                }

                // 战场读不到这个人，才退世界单位（那份 hp 是**开打前**的，真机上没被回写，
                // 所以只当兜底；两者都拿到时以战场为准，日志里并排列出来供对账）。
                int worldHpA = -1, worldHpB = -1;
                bool worldUsed = false;
                var ua = UnitOf(w.ActorUid);
                var ub = UnitOf(w.TargetUid);
                if (ua != null)
                {
                    try { var pd = ua.data.unitData.propertyData; worldHpA = pd.hp; } catch { }
                    if (!sceneA)
                    {
                        w.ActorSeen = true; worldUsed = true;
                        if (worldHpA >= 0) { w.ActorHp = worldHpA; try { w.ActorHpMax = ua.data.unitData.propertyData.hpMax; } catch { } }
                        try { w.ActorDead = ua.isDie; } catch { }
                    }
                }
                if (ub != null)
                {
                    try { var pd = ub.data.unitData.propertyData; worldHpB = pd.hp; } catch { }
                    if (!sceneB)
                    {
                        w.TargetSeen = true; worldUsed = true;
                        if (worldHpB >= 0) { w.TargetHp = worldHpB; try { w.TargetHpMax = ub.data.unitData.propertyData.hpMax; } catch { } }
                        try { w.TargetDead = ub.isDie; } catch { }
                    }
                }
                if (worldUsed)
                {
                    if (!string.IsNullOrEmpty(w.Evidence)) w.Evidence += "＋世界单位";
                    else w.Evidence = "世界单位";
                }

                ModMain.P(Tag + " 战斗结束快照（" + w.Evidence + "）："
                          + w.ActorName + " 读到=" + w.ActorSeen + " hp=" + w.ActorHp + "/" + w.ActorHpMax
                          + " 判负=" + w.ActorDead + "（世界hp " + worldHpA + "）"
                          + " ｜ " + w.TargetName + " 读到=" + w.TargetSeen + " hp=" + w.TargetHp + "/" + w.TargetHpMax
                          + " 判负=" + w.TargetDead + "（世界hp " + worldHpB + "）"
                          + " ｜ 场上单位=" + w.RosterCount + " 玩家在场=" + w.PlayerOnField);
                DiagLine(w, "快照：证据=" + w.Evidence + " 左=" + w.ActorName + " 战场hp=" + sceneHpA + "→报 " + w.ActorHp
                            + " 判负=" + w.ActorDead + "（世界hp " + worldHpA + "）"
                            + " ｜ 右=" + w.TargetName + " 战场hp=" + sceneHpB + "→报 " + w.TargetHp
                            + " 判负=" + w.TargetDead + "（世界hp " + worldHpB + "）"
                            + " ｜ 玩家在场=" + w.PlayerOnField);
            }
            catch (Exception e)
            {
                ModMain.P(Tag + " 快照异常（结果按字段缺失处理）: " + e.GetType().Name + " " + e.Message);
            }
        }

        private static void Finish(Watch w, string why, JObject result)
        {
            if (w == null || w.Resolved) return;
            w.Resolved = true;
            if (ReferenceEquals(_watch, w)) _watch = null;
            // 异常路径兜底：收口时若还拦着，先把战斗放掉 —— 否则"结果送出去了、战斗还停着"，
            // 那正是 fail-open 要防的形状。
            if (_holdActive && ReferenceEquals(_heldWatch, w))
            {
                try { ReleaseHold("收口时仍拦着（异常路径）"); } catch { }
            }
            // ★ 写经历（10-26 用户要求）★ 位置在 `DramaGate.Resolve` **之前**：
            //   Resolve 会把结果送回 Python、Python 那边可能立刻推进下一步（甚至开始下一场），
            //   而写经历要读的是"刚刚这一场"的现场。先写、再送，顺序不可换。
            try { WriteMemories(w); } catch (Exception me) { ModMain.P(Tag + " 写经历异常（不影响结果）：" + me.Message); }
            try { DramaGate.Resolve(w.Slot, result); }
            catch (Exception e) { ModMain.P(Tag + " 补发结果失败：" + e.Message); }
            string winner = (result["data"] != null && result["data"]["winner"] != null)
                          ? result["data"]["winner"].ToString() : "（未定）";
            ModMain.P(Tag + " 收口（" + why + "）：" + w.ActorName + " vs " + w.TargetName + " 胜者=" + winner);
            DiagLine(w, "★收口（" + why + "）：胜者=" + winner + "，结果=" + SafeJson(result));
            DiagFooter(w);
        }

        /// <summary>
        /// 组装给模型的结果。**只报事实**：谁在场、谁倒下、血量。
        /// `winner` 仅在"确实读到过两方状态，且恰好一方倒下"时给 ——
        /// 两边都站着、或有一方读不到，就**不给**（本仓铁律：字段缺失 ≠ 否定）。
        /// </summary>
        private static JObject BuildResult(Watch w)
        {
            var left = new JObject { ["name"] = w.ActorName, ["unit_id"] = w.ActorUid };
            var right = new JObject { ["name"] = w.TargetName, ["unit_id"] = w.TargetUid };
            if (w.ActorSeen)
            {
                left["alive"] = !w.ActorDead;
                if (w.ActorHp >= 0) { left["hp"] = w.ActorHp; left["hp_max"] = w.ActorHpMax; }
            }
            if (w.TargetSeen)
            {
                right["alive"] = !w.TargetDead;
                if (w.TargetHp >= 0) { right["hp"] = w.TargetHp; right["hp_max"] = w.TargetHpMax; }
            }

            var data = new JObject
            {
                ["op"] = "attack",
                ["target"] = w.TargetName,
                ["counterparty"] = w.TargetName,
                ["mode"] = "solo",   // 与工具参数同名同值（10-27 改名：watch → solo）
                ["watched"] = true,
                ["battle_seen"] = w.Started,
                ["battle_finished"] = true,
                ["left"] = left,
                ["right"] = right,
                // 与既有的"跨过一场战斗才回"同义：WsClient 据此不抢着弹 1:1 对话窗
                ["crossed_battle"] = true,
            };
            if (!string.IsNullOrEmpty(w.PlayerName)) data["spectator"] = w.PlayerName;
            if (w.ActorSeen && w.TargetSeen && w.ActorDead != w.TargetDead)
                data["winner"] = w.ActorDead ? w.TargetName : w.ActorName;
            // 战后处置（10-26）：**只在真的走到处置并拿到玩家的选择时**才出现这个键。
            // 没人倒下 / 页没开成 / 玩家还没选 ⇒ 一个字段都不加（字段缺失 ≠ 否定）。
            if (w.OutcomeRecorded)
            {
                data["judgement"] = new JObject
                {
                    ["loser"] = w.HoldLoserName,
                    ["outcome"] = w.Outcome,
                    ["label"] = w.OutcomeLabel,
                    // 战败重伤（10-26）：除"击杀"外都会挂「重伤」，把**实际结果**如实带上。
                    // 没挂（击杀 / 败者认不出 / 已带）时也带这一句事实，渲染层据此措辞。
                    ["hurt"] = w.HurtText ?? "",
                    ["insult_page"] = w.InsultPageOpened
                        ? ("acted" + (string.IsNullOrEmpty(w.InsultPageWhy) ? "" : "：" + w.InsultPageWhy))
                        : "",
                };
            }
            return new JObject { ["success"] = true, ["data"] = data };
        }

        private static JObject Fail(string error)
        {
            return new JObject { ["success"] = false, ["error"] = error };
        }

        private static bool BattleActive()
        {
            try { return g.world != null && g.world.battle != null && g.world.battle.isBattle; }
            catch { return false; }
        }

        private static WorldUnitBase UnitOf(string uid)
        {
            if (string.IsNullOrEmpty(uid)) return null;
            try { return g.world.unit.GetUnit(uid, true); }
            catch { return null; }
        }

        private static string NameOf(WorldUnitBase u)
        {
            try { return (string)u.data.unitData.propertyData.GetName() ?? ""; }
            catch { return ""; }
        }

        private static string UidOf(WorldUnitBase u)
        {
            try { return u.data.unitData.unitID ?? ""; }
            catch { return ""; }
        }

        private static string PlayerName()
        {
            try { return g.world != null && g.world.playerUnit != null ? NameOf(g.world.playerUnit) : ""; }
            catch { return ""; }
        }

        private static bool IsPlayer(WorldUnitBase u)
        {
            try
            {
                var p = g.world.playerUnit;
                if (p == null) return false;
                string a = UidOf(u), b = UidOf(p);
                if (!string.IsNullOrEmpty(a) && !string.IsNullOrEmpty(b)) return a == b;
                // id 读不到时退一步比名字（宁可误拒，也不要误开一个战前窗却没人负责）
                string na = NameOf(u), nb = NameOf(p);
                return na.Length > 0 && na == nb;
            }
            catch { return false; }
        }

        private static bool SameUnit(WorldUnitBase a, WorldUnitBase b)
        {
            // IL2CPP 里不同调用点拿到的托管包装器不保证引用同一个（本仓铁律）⇒ 一律比 unitID
            string ua = UidOf(a), ub = UidOf(b);
            if (!string.IsNullOrEmpty(ua) && !string.IsNullOrEmpty(ub)) return ua == ub;
            return ReferenceEquals(a, b);
        }

        private static bool SameGrid(WorldUnitBase a, WorldUnitBase b)
        {
            try
            {
                var da = a.data.unitData;
                var db = b.data.unitData;
                return da.pointX == db.pointX && da.pointY == db.pointY;
            }
            catch { return false; }
        }

        // ══════════════════════════════════════════════════════════
        //  诊断（观测加速器，与 `_diag_initiative_force.txt` 同一套路）
        //
        //  为什么要有它：这条链**只能靠模型主动调工具**才会跑，而"模型今天想不想打"不是
        //  我们能控的。要验引擎侧（场景开不开、谁赢、玩家什么身份），必须先有一条
        //  **确定会跑**的入口。文件存在即触发一次；改文件（mtime 变）重新武装。
        //
        //  一次触发会写一份**完整报告**（`_diag_npc_duel_report.txt`，每次触发先清空）：
        //    触发 → 解析 → 调用 → 开打那一刻的战斗数据 → 战场名单（有人在打吗、玩家在吗）
        //    → 战斗中每 2s 一行战场血量 → BattleEndFront / BattleExit → 收口与最终结果。
        //  ⇒ **一趟就能判完**"场景开不开、玩家什么身份、胜负从哪读"。
        //
        //  格式（UTF-8 或 GBK 均可，`#` 开头忽略）：
        //      left=江宇文
        //      right=姜萌
        //
        //  ★ 只是**触发**，判据与收口完全走生产链路（`AttackCore` 本体）★
        // ══════════════════════════════════════════════════════════

        private const int DiagRecheckFrames = 120;   // 与 DiagSwitches 同节拍（≈2s）
        private const int DiagSampleSeconds = 2;     // 战斗中战场名单采样间隔
        /// <summary>上次跑 `DiagTick` 的帧号（节流用）。</summary>
        private static int _diagAtFrame;
        /// <summary>已经**真正触发过**的那一版文件（mtime）。只有跑成了才消费，见 `DiagTick`。</summary>
        private static DateTime _diagDone = DateTime.MinValue;
        /// <summary>上次告警对应的那一版文件 —— 同一版只喊一次，免得每 2s 刷屏。</summary>
        private static DateTime _diagWarned = DateTime.MinValue;
        private static string _diagReport;

        private static string TriggerPath()
        {
            try { return Path.Combine(ModPaths.DiagDir, "_diag_npc_duel.txt"); }
            catch { return ""; }
        }

        private static string ReportPath()
        {
            try { return Path.Combine(ModPaths.DiagDir, "_diag_npc_duel_report.txt"); }
            catch { return ""; }
        }

        /// <summary>诊断节拍交给 `FrameTicker`（11-19）：手柄化后换世界能救活、`StopAll` 能撤。
        /// 重活看 `_diagAtFrame` 节流，本回调每帧只做一次整数比较。</summary>
        private static void EnsureDiag()
        {
            FrameTicker.Ensure(FrameTicker.KNpcDuelDiag, "npcduel.diag", () =>
            {
                int now = UnityEngine.Time.frameCount;
                if (now - _diagAtFrame < DiagRecheckFrames) return;
                _diagAtFrame = now;
                try { DiagTick(); } catch { }
            });
        }

        private static void DiagTick()
        {
            string path = TriggerPath();
            if (string.IsNullOrEmpty(path) || !File.Exists(path)) return;

            DateTime stamp;
            try { stamp = File.GetLastWriteTimeUtc(path); }
            catch { return; }
            if (stamp == _diagDone) return;       // 这一版已经跑过了

            string left = "", right = "";
            try
            {
                // 编码自适应：手写文件可能是记事本存的 UTF-8，也可能是 GBK（`File.ReadAllLines`
                // 只认 UTF-8，GBK 会读成乱码 ⇒ 名字永远"认不出"，而报错还像是名字打错了）。
                // 先严格 UTF-8（非法字节会抛），失败退 936；都不行再松 UTF-8 兜底。
                string text;
                try { text = new UTF8Encoding(false, true).GetString(File.ReadAllBytes(path)); }
                catch
                {
                    try { text = Encoding.GetEncoding(936).GetString(File.ReadAllBytes(path)); }
                    catch { text = File.ReadAllText(path); }
                }
                if (text.Length > 0 && text[0] == '\uFEFF') text = text.Substring(1);
                foreach (string raw in text.Split(new[] { '\r', '\n' }, StringSplitOptions.RemoveEmptyEntries))
                {
                    string line = (raw ?? "").Trim();
                    if (line.Length == 0 || line.StartsWith("#")) continue;
                    int eq = line.IndexOf('=');
                    if (eq <= 0) continue;
                    string k = line.Substring(0, eq).Trim().ToLowerInvariant();
                    string v = line.Substring(eq + 1).Trim();
                    if (k == "left") left = v;
                    else if (k == "right") right = v;
                }
            }
            catch (Exception e) { ModMain.P(Tag + "诊断文件读取失败: " + e.Message); return; }

            // ★ 只有**真的触发**才消费这一版文件（`_diagDone = stamp`）★
            //   为什么：文件常被在**主菜单**保存（此时世界还没加载 ⇒ `UnitLookup.Resolve` 必然为空），
            //   旧写法读完就把 mtime 记下 ⇒ 等进了世界也再不会看它，报告永远不出现。
            //   现在"读不到人"不消费、每 2s 自动重试，同一版只喊一次日志。
            if (left.Length == 0 || right.Length == 0)
            {
                DiagWarn(stamp, "诊断：left/right 未填全，等填（或改文件）");
                return;
            }
            // ★ 必须在"世界真的进来了"之后 ★（10-26 真机踩出来的形态）
            //   读档时 `g.world` 与世界数据在**加载屏里就可读** ⇒ `Resolve` 能认人，
            //   于是诊断在加载屏里就开打了；而 `IntoWorld` 善后那句
            //   `DramaGate.ForceResolveAll("世界重载/读档")` 会当场把票收掉
            //   （真机：`发起观战 … 已开打=True` → `WorldMgr:IntoWorld()` →
            //    `槽位 1 被外力收掉` → 那场仗静默作废，连 `BattleEndMgr.BattleEnd` 都不来）。
            //   不消费文件 ⇒ 每 2s 重试一次，直到世界就绪；同一版只喊一次日志。
            if (!ModMain.WorldEntered)
            {
                DiagWarn(stamp, "诊断：世界还没加载完（等 IntoWorld 之后自动重试）");
                return;
            }
            var a = UnitLookup.Resolve(left);
            var b = UnitLookup.Resolve(right);
            if (a == null || b == null)
            {
                DiagWarn(stamp, "诊断：认不出 " + (a == null ? left : right) + "（进世界后会自动重试）");
                return;
            }
            if (_watch != null)
            {
                // 上一场还在打：不覆盖报告、不再开一场（诊断要的是"一趟看清"，不是并发）
                DiagWarn(stamp, "诊断：上一场观战还没收口，稍后自动重试");
                return;
            }
            _diagDone = stamp;
            _diagReport = ReportPath();
            try { File.WriteAllText(_diagReport, ""); } catch { }
            ModMain.P(Tag + "诊断触发：" + left + " vs " + right + "（走生产链路 Attack）");
            DiagRaw("=== NpcDuel 观战诊断 " + DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss") + " ===");
            DiagRaw("[0] 触发：left=" + left + " right=" + right + "（文件 _diag_npc_duel.txt）");
            DiagRaw("[0] 解析：" + left + " uid=" + UidOf(a) + " 格=(" + Safe(() => a.data.unitData.pointX) + "," + Safe(() => a.data.unitData.pointY) + ") 是玩家=" + IsPlayer(a));
            DiagRaw("[0] 解析：" + right + " uid=" + UidOf(b) + " 格=(" + Safe(() => b.data.unitData.pointX) + "," + Safe(() => b.data.unitData.pointY) + ") 是玩家=" + IsPlayer(b));
            DiagRaw("[0] 战前：isBattle=" + BattleActive() + " 玩家=" + PlayerName()
                    + " SceneBattle=" + (BattleSceneFacts.Scene() != null ? "有" : "无"));
            var res = AttackCore(a, b, right, true);
            try { ModMain.P(Tag + "诊断返回：" + res.ToString(Newtonsoft.Json.Formatting.None)); } catch { }
            if (res["__pending__"] != null && res["__pending__"].Value<bool>())
                DiagRaw("[0] 已挂起（slot=" + Str(res["__slot__"]) + "），等这场打完再回结果");
            else if (res["success"] == null || !(bool)res["success"])
                DiagRaw("[0] 没打起来：" + (res["error"] != null ? res["error"].ToString() : "?"));
        }

        /// <summary>报告里的一行（不是诊断触发时什么都不做）。</summary>
        private static void DiagLine(Watch w, string line)
        {
            if (w == null || !w.Diag) return;
            double t = (DateTime.Now - w.BornAt).TotalSeconds;
            DiagRaw("[" + t.ToString("0.0") + "s] " + line);
        }

        /// <summary>同一版文件只喊一次（重试不刷屏）。</summary>
        private static void DiagWarn(DateTime stamp, string msg)
        {
            if (stamp == _diagWarned) return;
            _diagWarned = stamp;
            try { ModMain.P(Tag + msg); } catch { }
        }

        private static void DiagRaw(string line)
        {
            if (string.IsNullOrEmpty(_diagReport)) return;
            try { File.AppendAllText(_diagReport, line + Environment.NewLine); } catch { }
        }

        private static void DiagHeader(Watch w)
        {
            if (!w.Diag) return;
            DiagRaw("[1] 调用 DramaFunctionTool.OptionsFunction(\"" + WatchFunction + "\", unitLeft="
                    + w.ActorName + ", unitRight=" + w.TargetName + ")");
            DiagRaw("[1] 玩家=" + w.PlayerName + " slot=" + w.Slot);
        }

        /// <summary>开打那一刻的战斗数据（`battle.data` 就是 `DramaUnitBattle` 亲手造的那一个）。</summary>
        private static void DiagBattle(Watch w, string when)
        {
            if (w == null || !w.Diag) return;
            try
            {
                object data = null;
                try { data = g.world != null && g.world.battle != null ? (object)g.world.battle.data : null; } catch { }
                if (data == null) { DiagLine(w, when + "：battle.data 为空"); return; }
                object dbi = Refl.Get(data, "dungeonBaseItem");
                DiagLine(w, when + "：isBattle=" + BattleActive()
                            + " 副本 id=" + Str(Refl.Get(dbi, "id"))
                            + " type=" + Str(Refl.Get(dbi, "type"))
                            + " sceneBaseID=" + Str(Refl.Get(dbi, "sceneBaseID"))
                            + " roomBaseID=" + Str(Refl.Get(dbi, "roomBaseID"))
                            + " isRealBattle=" + Str(Refl.Get(data, "isRealBattle"))
                            + " isSelfBattle=" + Str(Refl.Get(data, "isSelfBattle")));
                object pair = Refl.Get(data, "npcFightUnits");
                DiagLine(w, when + "：npcFightUnits=" + PairText(pair));
            }
            catch (Exception e) { DiagLine(w, when + "：读战斗数据异常 " + e.GetType().Name); }
        }

        /// <summary>战斗中每 `DiagSampleSeconds` 记一行战场血量（只有诊断触发时才走这里）。</summary>
        private static void DiagSample(Watch w)
        {
            if (w == null || !w.Diag) return;
            if (DateTime.Now < w.NextSampleAt) return;
            w.NextSampleAt = DateTime.Now.AddSeconds(DiagSampleSeconds);
            string d;
            var roster = BattleSceneFacts.Roster(out d);
            DiagLine(w, "战中 isBattle=True 场上 " + (roster == null ? d : roster.Count + " 个：" + BattleSceneFacts.Describe(roster)));
        }

        private static void DiagFooter(Watch w)
        {
            if (w == null || !w.Diag) return;
            DiagRaw("[9] 收尾：证据=" + w.Evidence + " 玩家在场=" + w.PlayerOnField
                    + " 场上单位=" + w.RosterCount + " 玩家=" + w.PlayerName);
            DiagRaw("=== 结束 ===");
            _diagReport = null;
        }

        /// <summary>
        /// `npcFightUnits` 那一对是谁。`DataStruct&lt;T1,T2&gt;` 的**成员名本仓没有类型 dump**
        /// （`_dt_dump/types/` 里没有它），所以不猜名字 —— 直接扫这个对象的**所有字段**，
        /// 谁的值是 `WorldUnitBase` 就报谁。这样哪怕游戏改字段名也照样读得出来。
        /// </summary>
        private static string PairText(object pair)
        {
            if (pair == null) return "（空）";
            try
            {
                var parts = new List<string>();
                foreach (var f in pair.GetType().GetFields(
                             System.Reflection.BindingFlags.Instance |
                             System.Reflection.BindingFlags.Public |
                             System.Reflection.BindingFlags.NonPublic))
                {
                    object v = null;
                    try { v = f.GetValue(pair); } catch { continue; }
                    if (v is WorldUnitBase) parts.Add(f.Name + "=" + NameOfObj(v));
                }
                if (parts.Count > 0) return string.Join(" ", parts.ToArray());
                return "（" + pair.GetType().Name + " 里没有 WorldUnitBase 字段）";
            }
            catch (Exception e) { return "（读异常 " + e.GetType().Name + "）"; }
        }

        private static string NameOfObj(object o)
        {
            if (o == null) return "（空）";
            var u = o as WorldUnitBase;
            if (u != null) return NameOf(u) + "(" + UidOf(u) + ")";
            return o.GetType().Name;
        }

        private static string Str(object o) { return o == null ? "?" : o.ToString(); }

        private static string Safe(Func<int> f)
        {
            try { return f().ToString(); } catch { return "?"; }
        }

        private static string SafeJson(JObject o)
        {
            try { return o.ToString(Newtonsoft.Json.Formatting.None); } catch { return "?"; }
        }

        // ══════════════════════════════════════════════════════════
        //  写经历（10-26 用户要求）：这一场要落到**两个 NPC 自己的经历**里
        //
        //  ## 为什么不能复用 `TeamBattleFollowup` 那条
        //  那条写的是 `_mates`（**玩家一侧的队友**，判据是"号位气运"）。观战的当事人是
        //  两个 NPC 自己，玩家只是旁观 —— 号位气运跟他们无关，`_mates` 在这条链上恒为空
        //  （真机日志里 `[TeamFollowup]` 在本场一条都没打，就是因为它没 arm）。
        //  ⇒ 观战必须有自己的一份，写**双方**，各一条。
        //
        //  ## 为什么内容从账本取、而不是从流程拼
        //  用户在处置页上做了什么（取物/羞辱/击杀/放过）、败者说了什么求饶的话、
        //  玩家点了哪个按钮 —— 这些**全都在账本里**：`BattleEndFront` 之后的所有剧情页
        //  与选项都由 `DramaSegmentRecorder` 记进了 `DramaLedger`（真机日志已验证：
        //  `[SegRec] 记页 dramaId=20403`、`记选项「收下物品，羞辱此人」`）。
        //  所以经历正文用 `DungeonDramaCore.Format` 把**这一场窗口内的原话**串起来，
        //  前半句（谁向谁挑战、谁赢了）由我们按战场事实写 —— 事实 + 原话，两类都不编。
        //
        //  ## 三条边界
        //  ① 判不出胜负 ⇒ 不写（字段缺失 ≠ 否定：没有"谁赢了"这件事，就没有"败者"可写）；
        //  ② 击杀 ⇒ 败方写 `为其所杀` 那条专用模板（不然"我力不能敌：被击杀"读着别扭）；
        //  ③ 全部 try/catch：写经历失败**绝不影响结果回送**（`Finish` 里已经包好了）。
        // ══════════════════════════════════════════════════════════

        private static readonly HashSet<int> _readyTpl = new HashSet<int>();

        private static void WriteMemories(Watch w)
        {
            // 幂等位（不是 `w.Resolved`：`Finish` 里调进来时它**已经是 true**了 ——
            // 拿它当判据会一条都写不出去，且没有任何报错）。
            if (w == null || w.MemoriesWritten) return;
            w.MemoriesWritten = true;
            if (!w.ActorSeen || !w.TargetSeen || w.ActorDead == w.TargetDead)
            {
                try { ModMain.P(Tag + " 写经历跳过：判不出胜负（双方状态 " + w.ActorSeen + "/" + w.TargetSeen
                                          + "，倒下 " + w.ActorDead + "/" + w.TargetDead + "）"); } catch { }
                return;
            }
            var winnerU = UnitOf(w.ActorDead ? w.TargetUid : w.ActorUid);
            var loserU = UnitOf(w.ActorDead ? w.ActorUid : w.TargetUid);
            string winnerName = w.ActorDead ? w.TargetName : w.ActorName;
            string loserName = w.ActorDead ? w.ActorName : w.TargetName;
            if (winnerU == null || loserU == null)
            {
                try { ModMain.P(Tag + " 写经历跳过：认不出世界单位（胜=" + winnerName + " 败=" + loserName + "）"); } catch { }
                return;
            }

            string tail = MemoryTail(w);
            bool killed = w.Outcome == "kill";
            string winText = Fill2(NpcDuelWinTpl, loserName, tail);
            string loseText = killed ? Fill2(NpcDuelKilledTpl, winnerName, tail)
                                     : Fill2(NpcDuelLoseTpl, winnerName, tail);

            bool tplWin = EnsureTpl(ModIds.LogNpcDuelWin, ModIds.LogNpcDuelWinKey, NpcDuelWinTpl, "观战·胜方");
            bool tplLose = EnsureTpl(killed ? ModIds.LogNpcDuelKilled : ModIds.LogNpcDuelLose,
                                     killed ? ModIds.LogNpcDuelKilledKey : ModIds.LogNpcDuelLoseKey,
                                     killed ? NpcDuelKilledTpl : NpcDuelLoseTpl,
                                     killed ? "观战·被杀" : "观战·败方");

            int ok = 0;
            if (tplWin && AddLog(winnerU, ModIds.LogNpcDuelWinKey, new[] { loserName, tail }, winnerName, winText)) ok++;
            if (tplLose) { if (AddLog(loserU, killed ? ModIds.LogNpcDuelKilledKey : ModIds.LogNpcDuelLoseKey,
                                      new[] { winnerName, tail }, loserName, loseText)) ok++; }
            try
            {
                ModMain.P(Tag + " 写经历：" + (ok > 0 ? "成功 " + ok + " 条" : "❌全部未落库")
                          + "｜胜方[" + winnerName + "]=" + winText
                          + "｜败方[" + loserName + "]=" + loseText);
            }
            catch { }
            DiagLine(w, "写经历：" + ok + "/2｜胜=" + winText + "｜败=" + loseText);
            if (ok > 0) { try { ForceMerge(new List<WorldUnitBase> { winnerU, loserU }); } catch { } }
        }

        /// <summary>
        /// 经历正文的后半句：**先取账本里这一场的原话**，没有原话就用处置结论兜底。
        ///
        /// 账本窗口用的是与队友那条**同一对钉子**（`begin`/`end` 由
        /// `TeamContainerInject` 的直塞与 `TeamBattleFollowup.OnBattleExit` 插）——
        /// 观战走的是同一条 `IntoBattle`，所以钉子照插，真机日志里 `[SegRec]` 也照记。
        /// 取窗口是**读**（`TakeWindow` 不消费），所以与队友那条互不影响。
        /// </summary>
        private static string MemoryTail(Watch w)
        {
            string outcome = "";
            if (w.OutcomeRecorded)
            {
                string label = string.IsNullOrEmpty(w.OutcomeLabel) ? w.Outcome : w.OutcomeLabel;
                outcome = w.Outcome == "kill" ? "被击杀"
                        : w.Outcome == "native_other" ? "被处置（原生动态选项，效果未记）"
                        : label;
            }
            try
            {
                var win = DramaLedger.TakeBattleWindow();
                if (win != null && win.Items.Count > 0)
                {
                    var buf = new List<DungeonDramaLine>();
                    for (int i = 0; i < win.Items.Count; i++)
                    {
                        var e = win.Items[i];
                        if (e == null || e.Kind == "event") continue;
                        if (e.Private) continue;                      // 我们自己弹的确认窗不进经历
                        DungeonDramaCore.Add(buf, e.Kind, e.Speaker, e.Name, e.Text, e.DramaId, e.At);
                    }
                    var lines = DungeonDramaCore.Format(buf, PlayerName());
                    if (lines != null && lines.Count > 0)
                    {
                        string s = string.Join("；", lines.ToArray());
                        if (!string.IsNullOrEmpty(outcome)) s = s + "；" + outcome;
                        return Clip(s, 220);
                    }
                }
                try { ModMain.P(Tag + " 写经历：账本窗口内无可写原话（" + (win == null ? "null" : win.Items.Count + " 条")
                                          + "）⇒ 只用处置结论"); } catch { }
            }
            catch (Exception e)
            {
                try { ModMain.P(Tag + " 写经历：取账本异常（只用处置结论）：" + e.Message); } catch { }
            }
            return string.IsNullOrEmpty(outcome) ? "此事就此揭过。" : outcome;
        }

        private static string Clip(string s, int max)
        {
            if (string.IsNullOrEmpty(s) || s.Length <= max) return s ?? "";
            return s.Substring(0, max) + "…";
        }

        /// <summary>两条观战经历的模板（`{0}`=对面的人，`{1}`=这一场的经过与处置）。</summary>
        private const string NpcDuelWinTpl = "{0}向我出手，为我所败：{1}";
        private const string NpcDuelLoseTpl = "我向{0}出手，力不能敌：{1}";
        private const string NpcDuelKilledTpl = "我败于{0}之手，为其所杀。";

        private static string Fill2(string tpl, string a, string b)
        {
            return (tpl ?? "").Replace("{0}", a ?? "").Replace("{1}", b ?? "");
        }

        /// <summary>
        /// 确保模板可用（先查表，没有就运行时克隆注册）。
        ///
        /// 「查表 → 克隆 `MID&amp;1` → 改 id/keyID/ch → `AddItem` → 回读」这套流程
        /// `TeamBattleFollowup.EnsureLogTemplate` 已经实现且真机跑过，但它是 `private`。
        /// 这里**照抄同一套**而不是把它改成 internal：那个文件正被并行的组队功能改动，
        /// 少一个改点就少一次互相覆盖的机会（同样的理由写在 `TeamJoinGate` 文件头）。
        /// 代价是两份"注册逻辑"，所以两边的常量都在 `ModIds` 里、且 id 不重叠。
        /// </summary>
        private static bool EnsureTpl(int id, string key, string ch, string label)
        {
            if (_readyTpl.Contains(id)) return true;
            try
            {
                var conf = g.conf != null ? g.conf.roleLogLocal : null;
                if (conf == null) { ModMain.P(Tag + " 模板注册失败（" + label + "）：g.conf.roleLogLocal 为空"); return false; }
                try
                {
                    var exist = conf.GetItem(id);
                    if (exist != null && !string.IsNullOrEmpty(exist.ch))
                    {
                        _readyTpl.Add(id);
                        ModMain.P(Tag + " 模板[" + label + "]：用表里的 " + id);
                        return true;
                    }
                }
                catch { }
                var proto = conf.GetItem(ModIds.LogTeamDungeonDrama - 903);   // MID&1（同行那条）
                if (proto == null) { ModMain.P(Tag + " 模板注册失败（" + label + "）：克隆源 MID&1 取不到"); return false; }
                var row = proto.Clone();
                if (row == null) { ModMain.P(Tag + " 模板注册失败（" + label + "）：Clone() 返回空"); return false; }
                row.id = id; row.keyID = key;
                row.ch = ch; row.tc = ch; row.en = ch; row.kr = ch;
                conf.AddItem(row);
                var back = conf.GetItem(id);
                if (back == null || string.IsNullOrEmpty(back.ch))
                {
                    ModMain.P(Tag + " ❌ 模板[" + label + "]注册后回读不到（id=" + id + "）⇒ 这条经历写不进去");
                    return false;
                }
                _readyTpl.Add(id);
                ModMain.P(Tag + " 模板[" + label + "]：运行时注册成功 id=" + id + "（建议固化进 RoleLogLocal.xlsx）");
                // ★ 必须再补一次按键索引（10-26 真机踩出来的）★
                //   `ConfRoleLogLocal::AddItem`@0x4A21950 的尾部只有一句
                //   `jmp System.Collections.Generic.List::Add`（往 `allConfList(+0x50)` 追加），
                //   **没有**更新 `allItemInKey(+0x40)` —— 那个字典只在 `OnInit` 里建一次。
                //   后果：`GetItem(id)` 查得到（走 `allConfDic`），但 `AddLogData` 是走**字符串 key**
                //   查 `allItemInKey` 的，查不到就只打一行 `找不到日志：eventLogNpcDuelWin`
                //   然后**静默丢弃**。真机形态正是"模板注册成功、写经历全部未落库"。
                //   反证：导出进表的那几条（`MID&904~907`）写成功（`成功 2 / 未落库 0`），
                //   因为它们在 `OnInit` 里被建进了字典。
                PatchKeyIndex(conf, row, label);
                return true;
            }
            catch (Exception e)
            {
                ModMain.P(Tag + " 模板注册异常（" + label + "）：" + e.GetType().Name + " " + e.Message);
                return false;
            }
        }

        /// <summary>
        /// 把刚注册的那行补进 `ConfRoleLogLocal.allItemInKey`（`+0x40`）。
        ///
        /// 字典值类型是 `List&lt;ConfRoleLogLocalItem&gt;`，查的时候取列表首元素
        /// （`OnInit` 建出来的也是同一形状）⇒ **重复塞同一个实例无害**。
        /// 全程反射 + try/catch：这条链坏掉最坏是"经历写不进去"（日志里看得见），
        /// 绝不能把引擎的配置表弄脏，所以任何异常都只记一行、不外抛。
        /// </summary>
        private static void PatchKeyIndex(object conf, object row, string label)
        {
            try
            {
                var f = conf.GetType().GetField("allItemInKey",
                    BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
                if (f == null) { ModMain.P(Tag + " 按键索引补登记失败（" + label + "）：找不到 allItemInKey 字段"); return; }
                object dict = f.GetValue(conf);
                if (dict == null) { ModMain.P(Tag + " 按键索引补登记失败（" + label + "）：allItemInKey 为空"); return; }

                string key = null;
                try { key = (string)row.GetType().GetProperty("keyID").GetValue(row); } catch { }
                if (string.IsNullOrEmpty(key)) { ModMain.P(Tag + " 按键索引补登记失败（" + label + "）：读不到 keyID"); return; }

                var dType = dict.GetType();
                var contains = dType.GetMethod("ContainsKey");
                var getItem = dType.GetMethod("get_Item");
                if (contains == null || getItem == null)
                {
                    ModMain.P(Tag + " 按键索引补登记失败（" + label + "）：字典接口不像 Dictionary");
                    return;
                }
                bool has = (bool)contains.Invoke(dict, new object[] { key });
                object list = has ? getItem.Invoke(dict, new object[] { key }) : null;
                if (list == null)
                {
                    var valueType = dType.GetGenericArguments()[1];
                    list = Activator.CreateInstance(valueType);
                    var setItem = dType.GetMethod("set_Item");
                    if (setItem != null) setItem.Invoke(dict, new object[] { key, list });
                    else dType.GetMethod("Add").Invoke(dict, new object[] { key, list });
                }
                var add = list.GetType().GetMethod("Add");
                if (add == null) { ModMain.P(Tag + " 按键索引补登记失败（" + label + "）：List 没有 Add"); return; }
                add.Invoke(list, new object[] { row });

                // 回读：现在这个 key 在字典里到底有没有（不依赖 LS，LS 会跑条件求值）
                bool hasNow = false;
                try
                {
                    object list2 = getItem.Invoke(dict, new object[] { key });
                    if (list2 != null) hasNow = (int)list2.GetType().GetProperty("Count").GetValue(list2) > 0;
                }
                catch { }
                ModMain.P(Tag + " 按键索引补登记（" + label + "）：key=" + key
                          + (has ? "（原先已有，追加一份同实例）" : "（原先没有，新建列表）")
                          + "｜回读=" + (hasNow ? "✅查得到" : "❌仍查不到"));
            }
            catch (Exception e)
            {
                ModMain.P(Tag + " 按键索引补登记异常（" + label + "）：" + e.GetType().Name + " " + e.Message);
            }
        }

        /// <summary>
        /// 写一条经历，并**回读暂存层**确认引擎真的收了（缺 key 时引擎只打一行
        /// `找不到日志：…` 然后静默丢弃 —— 不回读就等于不知道）。
        /// </summary>
        private static bool AddLog(WorldUnitBase unit, string key, string[] values, string who, string rendered)
        {
            try
            {
                var mgr = g.world != null ? g.world.unitLog : null;
                if (mgr == null) { ModMain.P(Tag + " 写经历失败（" + who + "）：g.world.unitLog 为空"); return false; }
                UnitConditionData cd = null;
                try { cd = new UnitConditionData(unit, unit); } catch { }
                mgr.AddLogData(unit, key, values, cd);
                bool ok = PendingHas(unit, values != null && values.Length > 1 ? values[1] : (values != null && values.Length > 0 ? values[0] : ""));
                ModMain.P(Tag + " 写经历 → " + who + "：" + rendered
                          + (ok ? "  ✅已落库" : "  ❌未落库（RoleLogLocal 缺 key=" + key + "？）"));
                return ok;
            }
            catch (Exception e)
            {
                ModMain.P(Tag + " 写经历失败（" + who + "）：" + e.GetType().Name + " " + e.Message);
                return false;
            }
        }

        /// <summary>回读暂存层最近 3 条，看 `values` 里有没有我们写的那串。</summary>
        private static bool PendingHas(WorldUnitBase unit, string value)
        {
            if (string.IsNullOrEmpty(value)) return false;
            try
            {
                string uid = UidOf(unit);
                if (string.IsNullOrEmpty(uid)) return false;
                var pend = g.world.unitLog.GetCacheWriteLog(uid);
                if (pend == null) return false;
                int n = 0; try { n = pend.Count; } catch { return false; }
                int from = n > 3 ? n - 3 : 0;
                for (int i = from; i < n; i++)
                {
                    object item = null;
                    try { var e = pend[i]; if (e != null) item = e.logData; } catch { continue; }
                    if (item == null) continue;
                    try
                    {
                        var logs = ((dynamic)item).logs;
                        int ln = logs == null ? 0 : (int)logs.Count;
                        for (int j = 0; j < ln; j++)
                        {
                            var vs = ((dynamic)logs[j]).values;
                            if (vs == null) continue;
                            int vn = 0;
                            try { vn = (int)vs.Length; } catch { try { vn = (int)vs.Count; } catch { } }
                            for (int k = 0; k < vn; k++) if (vs[k] == value) return true;
                        }
                    }
                    catch { }
                }
            }
            catch { }
            return false;
        }

        /// <summary>
        /// 把刚写的经历立刻并进面板（否则要等月结/存档才显示）。
        /// 与 `TeamBattleFollowup.ForceMergeToPanel` 同一套引擎调用
        /// （`WriteLogData` + 把 `lastUpdate*Month` 置脏），这里只保留最小可用版本。
        /// </summary>
        private static void ForceMerge(List<WorldUnitBase> units)
        {
            try
            {
                var mgr = g.world != null ? g.world.unitLog : null;
                if (mgr == null) return;
                object regular = Refl.Get(mgr, "allAddLogData");
                object vital = Refl.Get(mgr, "allAddVitalLogData");
                if (regular == null && vital == null) return;

                // ★ 必须走反射调 `WriteLogData` ★
                //   它的形参类型是 `Dictionary<string, List<WorldUnitLogMgr.LogData>>`
                //   （**不是** `DataUnitLog.LogData`）—— 那两个同名不同类型在编译期一混就报
                //   CS1503（已经踩过一次）。形参类型只能从 `GetParameters()` 现取。
                var mi = mgr.GetType().GetMethod("WriteLogData",
                    BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
                if (mi == null) { ModMain.P(Tag + " 并进面板失败：找不到 WriteLogData"); return; }
                var ps = mi.GetParameters();
                if (ps.Length != 4) { ModMain.P(Tag + " 并进面板失败：WriteLogData 参数数=" + ps.Length); return; }

                object allUnit = null;
                try { allUnit = Activator.CreateInstance(ps[0].ParameterType); } catch { }
                if (allUnit == null) { ModMain.P(Tag + " 并进面板失败：建不出 allUnit 字典"); return; }

                int month = 0;
                try { month = NpcInitiativeMonitor.CurrentAccountMonth(); } catch { }
                if (month <= 0) month = 1;

                mi.Invoke(mgr, new object[] { allUnit, regular, vital, month });
                ModMain.P(Tag + " 合并到面板：WriteLogData 已执行（month=" + month + "）");

                foreach (var u in units)
                {
                    if (u == null) continue;
                    string uid = UidOf(u);
                    if (string.IsNullOrEmpty(uid)) continue;
                    try
                    {
                        var ld = g.world.unitLog.GetLogDataSync(uid);
                        if (ld == null) { ModMain.P(Tag + " 面板回读 → " + NameOf(u) + "：GetLogDataSync=null"); continue; }
                        // 强制解码缓存下次 getter 重建（字段名见 `DataUnitLog.LogData` 反编译）
                        SetMember(ld, "lastUpdateLogDataMonth", -1);
                        SetMember(ld, "lastUpdateVitalLogDataMonth", -1);
                        ModMain.P(Tag + " 面板回读 → " + NameOf(u) + "：缓存已置脏（下次读面板会重新解码）");
                    }
                    catch { }
                }
            }
            catch (Exception e)
            {
                ModMain.P(Tag + " 并进面板失败（经历仍在暂存层，月结时会显示）：" + e.GetType().Name + " " + e.Message);
            }
        }


        private static void SetMember(object o, string name, object v)
        {
            if (o == null) return;
            try
            {
                var f = o.GetType().GetField(name, BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
                if (f != null) { f.SetValue(o, v); return; }
                var p = o.GetType().GetProperty(name, BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
                if (p != null && p.CanWrite) p.SetValue(o, v);
            }
            catch { }
        }


    }
}
