/// <summary>
/// TeamJoinGate —— 「决斗要不要带队友」的**接线层**（核是 `TeamJoinCore`，纯逻辑可单测）。
///
/// ## 它在整条链里的位置
/// <code>
///   应战窗亮（层顶 MapBattlePre）
///     → 本类：问玩家（UICustomDramaDyn 确认窗，段 +200）
///       → 玩家点完 ⇒ 答案存在核里
///         → 战斗初始化：`TeamContainerInject.Prefix` 调 `TakeConsent()` 取走
///           → 决斗(type=40) + 同意 ⇒ 队友按原有容器直塞进场
///           → 决斗 + 没同意 ⇒ 清空容器、跳过原方法（一个人打）
/// </code>
///
/// ## 三条硬约束
/// ① **只问一次、只在一处判**：判据全在 `TeamJoinCore.ShouldAsk`，这里不做第二套；
/// ② **不改游戏行为**：只读 UI 层顶 / 只读配置表 / 只弹自己的窗；钩子恒放行、全程 try/catch；
/// ③ **判不出就闭嘴**：取不到玩家、取不到对手、窗模板注册不上 ⇒ 不问、不带队友，
///    但**必须留一行日志**（本仓最忌讳静默失败）。
///
/// ## 为什么本文件住在 `tools/` 而不是 `state/`（10-26 复核）
/// 它**不是判据**，是接线：读 UI 层顶、弹剧情窗、挂 `IntoBattle` 补丁、注册配表行。
/// `csharp/state/` 有一条硬边界（`tests/test_state_module_boundary.py`）：
/// state 不许反向引用 `drama/` 的业务类型（`DramaGate` / `DramaNativeClaim` / `DramaSegment*`），
/// 否则依赖成环，表现为"初始化顺序敏感、偶发不生效"。
/// 本类要读 `DramaAiOption.LastDramaUnit` 与 `DramaNativeClaim`（对手判据），
/// 所以它属于 `tools/` —— 与它唯一的调用方 `TeamContainerInject` 同目录。
/// 判据核 `TeamJoinCore` 仍留在 `state/`（零 Unity、可单测）。
///
/// ## 为什么本文件是**自包含**的（不并进 `BattleFlowGate`）
/// 它需要三样东西：一个节拍、`BattleFlowGate.State` 的读数、`IntoBattle` 的 dungeonID。
/// 前两样都能**只读**拿到（`State` 是公开属性），第三样自己挂一个 patch 即可。
/// 这样本功能对既有判据文件**零改动** —— 判据是 10-25 才落地的新面，正在被并行改动，
/// 少一个改点就少一次互相覆盖的机会。
///
/// ## 为什么窗模板要在运行时兜底注册
/// 新段 `+200` 的行要等 MOD 编辑器导出才进游戏；而"没导出 = 点应战时静默没有窗"
/// 是最难排查的失败（`DramaTool.OpenDrama` 只打一行「找不到剧情ID」）。
/// 所以照 `TeamBattleFollowup.EnsureDramaLogTemplate` 的既有做法：**先查表，
/// 没有就克隆一份现成壳**（克隆源 `+180` 组队邀请窗 —— 同族、同 uiType、同两选项结构）。
/// 表里有了（导出过）就走表里那份，运行时注册直接跳过。
/// </summary>
using System;
using System.Collections.Generic;
using System.Reflection;
using HarmonyLib;

namespace AgentLoopBridge
{
    internal static class TeamJoinGate
    {
        private const string Tag = "[TeamJoin] ";

        /// <summary>采样节流（秒）。与 `BattleFlowGate` 同量级 —— 两者都是"读一次层顶"。</summary>
        private const double SampleSeconds = 0.25;

        private static TeamJoinCore _core;

        /// <summary>本次要问的那个"对手"的名字（确认窗文案 + 写经历时的 `{1}`）。
        /// 对手对象本身只在 `Ask` 里当实参传一次，不留字段（留了就是会发霉的副本）。</summary>
        private static string _attackerName = "";

        /// <summary>
        /// "刚问过，下一拍核对窗有没有真的上来"（**只用于日志，不参与任何判据**）。
        ///
        /// 10-26 复核改掉的老写法：`_askOpen` 原先既表示"已经问过"、又喂给
        /// `ShouldAsk(ownWindowVisible: _askOpen)`。一旦窗被引擎拆掉而没走我们的回调
        /// （玩家直接点了应战、或战斗转场强拆 UI），它就**永久为真** ⇒ 此后所有决斗都不再问，
        /// 而且没有一行报错（本仓最忌的形态）。现在"窗在不在"一律按 UI 实况读。
        /// </summary>
        private static bool _askPendingCheck;
        /// <summary>本场"窗没上来"的告警只打一次（核对逻辑每场只跑一拍，这里再兜一层）。</summary>
        private static bool _askCheckDone;

        /// <summary>窗模板注册结果：0 未试 / 1 用表里 / 2 运行时注册 / -1 都不行。</summary>
        private static int _windowState;

        /// <summary>上一次"为什么不问"（只在变化时打日志，免得 0.25s 刷一行）。</summary>
        private static string _lastWhy = "";

        private static bool _attached;
        private static float _lastSampleAt = -999f;
        private static BattleFlowState _lastFlow = BattleFlowState.Idle;
        /// <summary>上一次看到的**世界原生物件指针**（换世界判据；不是托管包装引用，见 `Tick`）。
        /// 0 = 还没进世界（或正处在读档空档）。</summary>
        private static IntPtr _lastWorldPtr;

        /// <summary>"决斗触发器自检"每局只做一次。</summary>
        private static bool _duelProbeDone;

        internal static string InstallReport = "(未执行)";

        // ==================================================================== 安装

        /// <summary>
        /// 挂帧回调 + `IntoBattle` 补丁（幂等）。由 `TeamContainerInject.EnsureInstalled` 调
        /// —— 那条链在 `ModMain` 启动时必走一次（本模块不新增 `ModMain` 改点）。
        /// </summary>
        internal static void Attach()
        {
            // `_attached` 只管**补丁与自检**（Harmony 补丁重复打没有意义，且成本高）；
            // 帧回调单独交给 `FrameTicker`（11-19）：它按帧号去重、并在世界重载后自愈重挂。
            // 老写法是同一个 `_attached` 连帧回调一起挡 —— 而重载会把游戏那边的登记清掉，
            // 于是 `teamjoin` 与 `deferq`/`battleflow` 一样永久静默（真机日志里这三个键
            // 在重进世界后就再没出现过）。
            bool firstTime = !_attached;
            if (firstTime) { _attached = true; Ensure(); }

            FrameTicker.Ensure(FrameTicker.KTeamJoin, "teamjoin", () =>
            {
                long __t = TickProfiler.Begin();
                try { Tick(); }
                finally { TickProfiler.End("teamjoin", __t); }
            });

            if (!firstTime) return;

            // `WorldBattleMgr.IntoBattle` 补丁：只为拿到 dungeonID（判"这场是不是决斗"）。
            // 与 `BattleFlowHooks.BattleStartHook` 挂在同一批方法上 —— Harmony 允许多个 prefix，
            // 互不依赖：那个管状态机，这个只管"把表态绑到具体这一场"。
            try
            {
                var t = AccessTools.TypeByName("WorldBattleMgr");
                if (t == null) { SafeLog("IntoBattle 补丁：WorldBattleMgr 没找到（表态改由注入层判类型）"); }
                else
                {
                    var pre = typeof(TeamJoinGate).GetMethod(nameof(IntoBattlePrefix),
                        BindingFlags.Static | BindingFlags.NonPublic);
                    int n = 0;
                    foreach (var cand in AccessTools.GetDeclaredMethods(t))
                    {
                        if (cand.Name != "IntoBattle") continue;
                        new HarmonyLib.Harmony("AgentLoopBridge.TeamJoin" + n)
                            .Patch(cand, prefix: new HarmonyMethod(pre));
                        n++;
                    }
                    SafeLog("IntoBattle 补丁=" + n + " 个重载");
                }
            }
            catch (Exception e) { SafeLog("IntoBattle 补丁失败（不影响注入层判据）: " + e.Message); }

            InstallReport = "窗模板=" + _windowState + " 采样=已挂";
            SafeLog("已安装：" + InstallReport);
        }

        /// <summary>`IntoBattle` prefix：只读参数，恒放行。</summary>
        private static void IntoBattlePrefix(MethodBase __originalMethod, object[] __args)
        {
            try
            {
                // 新一场开打 ⇒ 本场的"已决定/已注入"清零（自愈点，理由见 TeamContainerInject.OnBattleStarting）
                try { TeamContainerInject.OnBattleStarting(); } catch { }
                int dungeonId = 0;
                // 只有 `(WorldUnitBase, Int32, Action, WorldBattleData)` 这一支带 dungeonID；
                // 其余重载的参数里没有"要进哪个副本"这个数 —— 读不到就不猜（交给注入层）。
                if (__originalMethod != null && __args != null)
                {
                    var ps = __originalMethod.GetParameters();
                    for (int i = 0; i < ps.Length && i < __args.Length; i++)
                    {
                        if (ps[i].ParameterType == typeof(int)) { try { dungeonId = Convert.ToInt32(__args[i]); } catch { } break; }
                    }
                }
                OnBattleStart(dungeonId);
            }
            catch { }
        }

        // ==================================================================== 生命周期

        internal static void Ensure()
        {
            if (_core != null) return;
            _core = new TeamJoinCore();
            // 窗模板**不在这里注册**：本类的 `Attach()` 跑在 `ModMain.Init` 那一拍，
            // 而配置表（`g.conf.dramaDialogue`）不一定已经就绪。注册放到 `Ask()` 里懒试，
            // 失败也不锁死（下一场再来一次）—— 见 `EnsureWindowTemplate` 的守卫。
        }

        /// <summary>换世界（读档/换档）：表态、对手、自检位一起清（窗模板保留 —— 它是配表事实）。</summary>
        internal static void Reset()
        {
            try
            {
                Ensure();
                _core.Reset();
                try { TeamContainerInject.OnBattleStarting(); } catch { }
                _attackerName = "";
                _askPendingCheck = false;
                _askCheckDone = false;
                _lastWhy = "";
                _duelProbeDone = false;
                _lastFlow = BattleFlowState.Idle;
                SafeLog("换世界 ⇒ 表态与对手已清空");
            }
            catch { }
        }

        // ==================================================================== 采样

        private static void Tick()
        {
            try
            {
                float now = UnityEngine.Time.unscaledTime;
                if (now - _lastSampleAt < SampleSeconds) return;
                _lastSampleAt = now;

                // 换世界：用**原生物件指针**判（★10-26 真机踩过，别改回 ReferenceEquals★）
                //   IL2CPP 的托管包装可能"每次取都新建一个"（同一个 native 对象 → 不同 wrapper），
                //   于是 `ReferenceEquals(g.world, _lastWorld)` **恒为 false** ⇒ 每 0.25s 跑一次 Reset()。
                //   真机症状（10-26 13:0x，`Player.log` 3555 行）：`换世界 ⇒ 表态与对手已清空` 与
                //   `进入战前 ⇒ 本场可问` 交替刷屏；`_asked` 被反复清 ⇒ **确认窗一直重弹**，
                //   玩家点过的"同意"也在下一次 Reset 里被抹掉（注入层只会看到 None）。
                IntPtr worldPtr = IntPtr.Zero;
                try { var w = g.world; if (w != null) worldPtr = w.Pointer; } catch { }
                if (worldPtr != _lastWorldPtr)
                {
                    bool hadWorld = _lastWorldPtr != IntPtr.Zero;   // 上一次确实有世界
                    _lastWorldPtr = worldPtr;
                    if (hadWorld) Reset();                        // 离开旧世界（去 0 或去新世界）都算换档
                }

                // 只读"应战窗亮不亮"。战斗位/流程状态由 `BattleFlowGate` 管（这里读它的公开属性），
                // 本类**不自己再算一套** —— 两份判据迟早给出相反结论（本仓有过这种事故）。
                bool pre = false;
                try { pre = WorldStateFacts.BattlePreShowing(); } catch { }

                // ① 状态转移（读 `BattleFlowGate` 的公开属性，不改它）
                BattleFlowState flow = BattleFlowState.Idle;
                try { flow = BattleFlowGate.State; } catch { }
                if (flow != _lastFlow)
                {
                    BattleFlowState old = _lastFlow;
                    _lastFlow = flow;
                    string why;
                    _core.OnFlowStateChanged(old, flow, out why);
                    if (!string.IsNullOrEmpty(why)) SafeLog(why + "（" + old + " → " + flow + "｜" + _core.Brief() + "）");
                }

                // ② 问过之后的第一拍：核对我们的窗到底上来了没有
                //   位置必须**在 `if (!pre) return;` 之前** —— 我们的窗盖住应战窗后
                //   `pre` 会变假，放在后面就永远核不到（那正是"弹了窗却没显示"的样子）。
                //   只记日志，不改判据：真没上来 ⇒ 答案保持 None ⇒ 注入层按"未同意"处理。
                if (_askPendingCheck)
                {
                    _askPendingCheck = false;
                    CheckWindowShown();
                }

                // ③ 该不该问（判据全在核里）
                if (!pre) { _lastWhy = ""; return; }
                WorldUnitBase attacker = PickAttacker();
                int teamCount = 0;
                try { teamCount = TeamTool.Members(out _).Count; } catch { }

                // 窗在不在，**只信 UI 实况**（10-26 复核：不再拿"我们以为自己正在问"当输入）
                bool ownVisible = false;
                try { ownVisible = WorldStateFacts.OwnDramaWindowVisible(); } catch { }

                string q;
                if (!_core.ShouldAsk(pre, teamCount > 0, attacker != null, ownVisible, out q))
                {
                    if (!string.IsNullOrEmpty(q) && q != _lastWhy)
                    {
                        _lastWhy = q;
                        SafeLog("本次不问：" + q + "（队=" + teamCount + "）");
                    }
                    return;
                }
                _lastWhy = "";
                Ask(attacker, teamCount);
            }
            catch (Exception e) { SafeLog("采样异常（忽略，不影响战斗）: " + e.GetType().Name + " " + e.Message); }
        }

        /// <summary>
        /// `IntoBattle` 命中：**把表态绑到具体这一场**。
        ///
        /// 能读到 dungeonID 就当场查表判类型：不是决斗 ⇒ 立刻作废（绝不让它漏进下一场副本）；
        /// 是决斗 ⇒ 顺手做一次触发器自检（回答"你到底导出没有"）。
        /// 读不到 ⇒ 什么都不做，由注入层按 `type=40` 判（那条是权威判据）。
        /// </summary>
        private static void OnBattleStart(int dungeonId)
        {
            try
            {
                Ensure();
                if (dungeonId <= 0) return;

                int type = -1;
                try
                {
                    var baseConf = g.conf != null ? g.conf.dungeonBase : null;
                    var dbi = baseConf != null ? baseConf.GetItem(dungeonId) : null;
                    if (dbi != null) type = dbi.type;
                }
                catch (Exception e) { SafeLog("查副本配置失败 id=" + dungeonId + ": " + e.Message); }

                if (type < 0) { SafeLog("开打 id=" + dungeonId + " → 查不到 dbi.type（交给注入层判）"); return; }

                if (type != ModIds.DungeonTypeDuel)
                {
                    if (_core.HasAnswer)
                    {
                        _core.Take();
                        SafeLog("这场不是决斗（id=" + dungeonId + " type=" + type + "）⇒ 上一场的表态作废");
                    }
                    return;
                }

                SafeLog("决斗开打：id=" + dungeonId + " type=40 表态=" + _core.Answer + "｜" + _core.Brief());
                ProbeDuelTriggerOnce(dungeonId);
            }
            catch { }
        }

        // ==================================================================== 给注入层

        /// <summary>
        /// 取走玩家表态（一次性）。**只有决斗分支该调** —— 副本分支不消费，
        /// 否则一场普通副本会把"下一场决斗"的答案吃掉。
        /// </summary>
        internal static JoinAnswer TakeConsent()
        {
            try
            {
                Ensure();
                JoinAnswer a = _core.Take();
                SafeLog("注入层取走表态：" + a + "｜" + _core.Brief());
                return a;
            }
            catch { return JoinAnswer.None; }
        }

        /// <summary>本次决斗的对手名（写经历用；判不出时是空串）。</summary>
        internal static string LastAttackerName
        {
            get { try { return _attackerName ?? ""; } catch { return ""; } }
        }

        internal static string StatusLine()
        {
            try
            {
                Ensure();
                return "窗模板=" + _windowState + " 对手=" + (_attackerName.Length > 0 ? _attackerName : "-")
                     + "｜" + _core.Brief();
            }
            catch { return "(读不到)"; }
        }

        // ==================================================================== 询问

        private static void Ask(WorldUnitBase attacker, int teamCount)
        {
            var player = g.world != null ? g.world.playerUnit : null;
            if (player == null) { SafeLog("不问：取不到玩家单位"); return; }

            try { EnsureWindowTemplate(); }
            catch (Exception wex) { SafeLog("窗模板准备异常（按未同意处理）: " + wex.Message); }

            // 名字分两种用途：`_attackerName` 给"写经历"（空 = 判不出，那边会回落成「强敌」）；
            // 窗口文案另算一个 `who`（空名字会让句子变成" 来势不善。"）。
            _attackerName = NameOf(attacker);
            string who = string.IsNullOrEmpty(_attackerName) ? "对方" : _attackerName;
            string text = who + " 来势不善。可要叫上同行之人，一起应战？";

            // 层顶只用于日志（回答"应战窗 vs 我们的窗谁在上面"这个真机问题）
            string topBefore = "";
            try { topBefore = WorldStateFacts.UiLayerTopName(); } catch { }

            SafeLog("弹出确认窗：对手=" + who + " 队=" + teamCount + " 窗模板=" + _windowState
                    + " 层顶=" + topBefore);
            try
            {
                ShowDramaService.ShowConfirmSimple(
                    ModIds.DramaTeamJoinBase,
                    player,          // left  = 屏幕左位 = 玩家
                    attacker,        // right = 屏幕右位 = 对手
                    text,
                    "一起应战", "我一个人",
                    onOk: () =>
                    {
                        _core.SetAnswer(true);
                        SafeLog("玩家选择：一起应战（对手=" + who + "）");
                    },
                    onNo: () =>
                    {
                        _core.SetAnswer(false);
                        SafeLog("玩家选择：我一个人（对手=" + who + "）");
                    });
            }
            catch (Exception e)
            {
                SafeLog("弹窗异常（按未同意处理）: " + e.GetType().Name + " " + e.Message);
            }
            // 下一拍核对"窗到底上来了没有"（只记日志；答案该是 None 就还是 None）
            _askPendingCheck = true;
            _askCheckDone = false;
        }

        /// <summary>
        /// 问过之后的第一拍：把"窗在不在"这件事写成一行**确定的**日志。
        ///
        /// 为什么要它：`ShowConfirmSimple` 只在"抛异常"时说明失败；而模板缺行/被别的窗压住
        /// 这类失败是**静默的**（`DramaTool.OpenDrama` 只打一行「找不到剧情ID」）。
        /// 这一行就是那次真机探针要回答的问题之一（`docs/archive/2026-10-25-队友进决斗/事实链.md` U3）。
        /// </summary>
        private static void CheckWindowShown()
        {
            if (_askCheckDone) return;
            _askCheckDone = true;
            try
            {
                bool up = WorldStateFacts.OwnDramaWindowVisible();
                string top = "";
                try { top = WorldStateFacts.UiLayerTopName(); } catch { }
                if (up)
                    SafeLog("确认窗已显示（层顶=" + top + "，模板=" + _windowState + "）");
                else
                    SafeLog("⚠ 确认窗没显示出来（层顶=" + top + "，模板=" + _windowState
                            + "）⇒ 本场按未同意处理（队友不参战）");
            }
            catch (Exception e) { SafeLog("窗核对异常: " + e.GetType().Name + " " + e.Message); }
        }

        /// <summary>最近一页剧情的对方；判不出（null / 玩家 / 自家队友 / 已离开世界）返回 null。</summary>
        private static WorldUnitBase PickAttacker()
        {
            try
            {
                var page = DramaAiOption.LastDramaUnit;
                string pageUid = null;
                try { pageUid = page != null ? DramaNativeClaim.UidOf(page) : null; } catch { }

                // ★ 攻击链优先（10-26 补：为了"用工具发起攻击"这条测试路能稳）★
                //   工具发起的攻击，开场页可能是在**工具执行期**开的（闸 1 那一支：
                //   不注入按钮，甚至判不出归属）。这时 `LastDramaUnit` 要么没被更新
                //   （还停在上一个说话的人），要么是别人 —— 拿它当对手，确认窗的立绘/人名
                //   以及后来写进队友经历的那个名字都会**错**。
                //   而动作工具由 busy 键串行化：`CurrentChainUid()` 只在**恰好一张**凭证时给值，
                //   再要求 `op == "attack"`，这张凭证就是"此刻正在对你动手的那个人"。
                //   原生寻仇没有我方凭证 ⇒ 自动走下面的页面判据（一行都不变）。
                string chain = "";
                try { chain = DramaNativeClaim.CurrentChainUid(); } catch { }
                if (!string.IsNullOrEmpty(chain) && DramaNativeClaim.OpOf(chain) == "attack")
                {
                    bool samePage = !string.IsNullOrEmpty(pageUid) && pageUid == chain;
                    if (samePage && IsFoe(page)) return page;      // 页面和链指向同一人：用页面对象
                    var v = UnitLookup.Resolve(chain);
                    if (IsFoe(v))
                    {
                        SafeLog("对手取自攻击链目标 uid=" + chain
                                + (string.IsNullOrEmpty(pageUid) ? "（页面上判不出）"
                                                                : "（页面是 " + pageUid + "，以链为准）"));
                        return v;
                    }
                    SafeLog("攻击链目标 uid=" + chain + " 解析不到单位 ⇒ 回落页面判据");
                }

                return IsFoe(page) ? page : null;
            }
            catch { return null; }
        }

        /// <summary>能不能当"对手"：非空、未销毁、不是玩家、不是自家队友（四条全是硬判据）。</summary>
        private static bool IsFoe(WorldUnitBase u)
        {
            try
            {
                if (u == null) return false;
                if (u.data == null || u.data.unitData == null) return false;   // 已销毁
                if (TeamTool.IsPlayer(u)) return false;
                if (TeamTool.SlotOf(u) >= 0) return false;                     // 对手是自家队友 ⇒ 脏数据
                return true;
            }
            catch { return false; }
        }

        private static string NameOf(WorldUnitBase u)
        {
            try { return TeamTool.NameOf(u, ""); } catch { return ""; }
        }

        // ==================================================================== 窗模板

        private static void EnsureWindowTemplate()
        {
            if (_windowState == 1 || _windowState == 2) return;   // 成功过就不再试
            // 失败过（-1）**允许重试**：本类在 `ModMain.Init` 就被装上，那一刻配置表可能还没就绪；
            // 一次失败就锁死会让"决斗确认窗"永久失效，而且只有一行启动日志可查（本仓最忌静默）。
            try
            {
                var conf = g.conf != null ? g.conf.dramaDialogue : null;
                if (conf == null) { _windowState = -1; SafeLog("窗模板：g.conf.dramaDialogue 为空"); return; }

                int win = ModIds.Window(ModIds.DramaTeamJoinBase);

                // ① 表里已有（导出过 mod）⇒ 直接用
                try
                {
                    var exist = conf.GetItem(win);
                    if (exist != null)
                    {
                        _windowState = 1;
                        SafeLog("窗模板：用表里的 " + win);
                        EnsureOptionRows(win, alreadyInTable: true);
                        return;
                    }
                }
                catch { }

                // ② 运行时克隆（源 = 组队邀请窗 +180）
                var proto = conf.GetItem(ModIds.Window(ModIds.DramaTeamInviteBase));
                if (proto == null) { _windowState = -1; SafeLog("窗模板：克隆源 +180 取不到"); return; }

                var row = proto.Clone();
                if (row == null) { _windowState = -1; SafeLog("窗模板：Clone() 返回空"); return; }
                row.id = win;
                row.weight = 1;
                row.options = "0";       // 选项全交运行时（`ShowConfirmSimple` 也会再清一次）
                conf.AddItem(row);

                // ③ 回读确认（不确认就等于不知道）
                var back = conf.GetItem(win);
                if (back == null)
                {
                    _windowState = -1;
                    SafeLog("窗模板：注册后回读不到 id=" + win + " ⇒ 决斗确认窗弹不出来");
                    return;
                }
                _windowState = 2;
                SafeLog("窗模板：运行时注册成功 id=" + win
                        + "（建议后续固化进 DramaDialogue.xlsx 的 MID&200）");

                // ④ 两个选项行也必须能查到（见 EnsureOptionRows 的为什么）
                EnsureOptionRows(win, alreadyInTable: false);
            }
            catch (Exception e)
            {
                _windowState = -1;
                SafeLog("窗模板注册异常: " + e.GetType().Name + " " + e.Message);
            }
        }

        /// <summary>
        /// 兜底注册**两个选项行**（`+201` 一起应战 / `+202` 我一个人）。
        ///
        /// ## 为什么不能只注册窗口行（10-26 复核发现）
        /// 窗口的按钮是运行时写进 `dramaData.dialogueOptions` 的，**文案不依赖表**；
        /// 但玩家点下去走的是引擎的 `UIDramaBase.ClickOption(<ConfDramaOptionsItem>)` ——
        /// 它**按选项 id 反查 `ConfDramaOptions` 那一行**（`ClearShellOptions` 的注释写过：
        /// "那两行 `DramaOptions` 条目必须保留"）。我们这两行是这次新加的，没导出时
        /// 游戏里根本没有 ⇒ 按钮画得出来、点下去没有回调 ⇒ 玩家点了"一起应战"却
        /// 一个队友都不来，且没有异常。只注册窗口行会留下这个半截状态。
        ///
        /// ## 做法
        /// 照抄同族那两行（`+181` 同意 / `+182` 不同意，function=0 纯回调）：
        /// clone → 只改 `id` → `AddItem` → 回读。**文案不动**：显示用的是
        /// `dialogueOptions` 里覆盖的那两串中文，表里的 `text` 只在兜底路径才被读。
        /// </summary>
        private static void EnsureOptionRows(int win, bool alreadyInTable)
        {
            var opts = g.conf != null ? g.conf.dramaOptions : null;
            if (opts == null) { SafeLog("选项行：g.conf.dramaOptions 为空（按钮可能点了没反应）"); return; }

            int[] targets = { win + ModIds.OffPrimary, win + ModIds.OffCancel };
            int[] sources = { ModIds.Window(ModIds.DramaTeamInviteBase) + ModIds.OffPrimary,
                              ModIds.Window(ModIds.DramaTeamInviteBase) + ModIds.OffCancel };
            for (int i = 0; i < targets.Length; i++)
            {
                try
                {
                    if (opts.GetItem(targets[i]) != null) continue;      // 表里有 ⇒ 不动
                    if (alreadyInTable)
                    {
                        // 窗口行在表里、选项行却不在：说明表只导出了一半（值得记一笔）
                        SafeLog("选项行：表里有窗 " + win + " 却没有选项 " + targets[i] + " ⇒ 补注册");
                    }
                    var proto = opts.GetItem(sources[i]);
                    if (proto == null) { SafeLog("选项行：克隆源 " + sources[i] + " 取不到 ⇒ 该按钮可能点不动"); continue; }
                    var row = proto.Clone();
                    if (row == null) { SafeLog("选项行：Clone() 返回空（id=" + targets[i] + "）"); continue; }
                    row.id = targets[i];
                    opts.AddItem(row);
                    var back = opts.GetItem(targets[i]);
                    SafeLog("选项行：" + (back != null ? "✅ 注册成功" : "❌ 注册后回读不到")
                            + " id=" + targets[i] + "（源 " + sources[i] + "）");
                }
                catch (Exception e)
                {
                    SafeLog("选项行注册异常 id=" + targets[i] + ": " + e.GetType().Name + " " + e.Message);
                }
            }
        }

        // ==================================================================== 触发器自检

        /// <summary>
        /// 每局一次，回答："这场决斗，引擎到底认不认我们那条 `type=40` 的触发器？"
        ///
        /// 配表没导出时**一切静默**：应战窗照弹、确认窗照问、玩家点「一起应战」，
        /// 然后队友一个都没来。这里直接调引擎自己的判定
        /// （`g.conf.dungeonTrigger.GetItem(dbi)`，与 `TriggerSelfCheck` 同一条已验证的路，
        /// **不挂补丁**），三层一次问全：候选清单 / 我们那条在不在 / `IsTrigger` 过不过。
        /// </summary>
        private static void ProbeDuelTriggerOnce(int dungeonId)
        {
            if (_duelProbeDone) return;
            _duelProbeDone = true;
            int ours = ModIds.Mod + 4;          // MID&004（决斗：把队友塞进容器）
            int hurtRow = ModIds.Mod + 2;       // MID&002（决斗：给 3/4/5 号位挂 BattleEffect 6009）
            // 为什么两个都要查（10-26 真机）：只查 MID&004 时，日志说"✅ 在候选里"，
            // 而实际上**弹 UI 那半条链是断的** —— MID&002 的 dungeonType 里没有 40，
            // 于是 6009 从没挂到队友身上，队友倒下也不会开 190。
            // 一个"✅"掩盖了另一条断链，正是本仓最忌的形态。
            try
            {
                var conf = g.conf != null ? g.conf.dungeonTrigger : null;
                var baseConf = g.conf != null ? g.conf.dungeonBase : null;
                if (conf == null || baseConf == null) { SafeLog("触发器自检：g.conf 不可用"); return; }
                var dbi = baseConf.GetItem(dungeonId);
                if (dbi == null) { SafeLog("触发器自检：dbi 取不到（id=" + dungeonId + "）"); return; }

                // ① 候选清单 —— 与 `TriggerSelfCheck` 同一条已验证的反射路（先精确签名，再宽松兜底）
                object listObj = null;
                string err = null;
                try
                {
                    var mi = conf.GetType().GetMethod("GetItem",
                        BindingFlags.Public | BindingFlags.Instance, null, new[] { dbi.GetType() }, null);
                    if (mi == null)
                    {
                        foreach (var m in conf.GetType().GetMethods(BindingFlags.Public | BindingFlags.Instance))
                        {
                            if (m.Name != "GetItem") continue;
                            var ps = m.GetParameters();
                            if (ps.Length == 1 && ps[0].ParameterType.IsInstanceOfType(dbi)) { mi = m; break; }
                        }
                    }
                    if (mi != null) listObj = mi.Invoke(conf, new object[] { dbi });
                    else err = "找不到接受 " + dbi.GetType().Name + " 的 GetItem";
                }
                catch (Exception e) { err = e.GetType().Name + ": " + e.Message; }

                if (listObj == null)
                {
                    SafeLog("触发器自检：候选清单拿不到（" + (err ?? "返回 null")
                            + "）—— 这只是本模块没拿到，不等于触发器没跑");
                    return;
                }

                // ② 清单内容（Count + 索引器；Il2Cpp 枚举器不可靠）
                var t = listObj.GetType();
                var piCount = t.GetProperty("Count", BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance);
                var miItem = t.GetMethod("get_Item", BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance);
                if (piCount == null || miItem == null) { SafeLog("触发器自检：候选清单不可读"); return; }
                int n = Convert.ToInt32(piCount.GetValue(listObj, null));

                object mine = null, hurt = null;
                int withInject = 0;
                var ids = new List<string>();
                for (int i = 0; i < n; i++)
                {
                    object row = null;
                    try { row = miItem.Invoke(listObj, new object[] { i }); } catch { continue; }
                    if (row == null) continue;
                    string idStr = Convert.ToString(Refl.Get(row, "id"));
                    ids.Add(idStr);
                    string bf = Convert.ToString(Refl.Get(row, "battleFunction")) ?? "";
                    if (bf.IndexOf("addBattleNPCData", StringComparison.OrdinalIgnoreCase) >= 0) withInject++;
                    int rid;
                    if (!int.TryParse(idStr, out rid)) continue;
                    if (rid == ours) mine = row;
                    else if (rid == hurtRow) hurt = row;
                }
                SafeLog("触发器自检：决斗 id=" + dungeonId + " 候选 " + n + " 条 ["
                        + string.Join(",", ids.ToArray()) + "] 其中带 addBattleNPCData 的 " + withInject + " 条");

                // ③ 我们在不在、过不过（IsTrigger 是引擎自己的判定函数）
                if (mine == null)
                {
                    SafeLog("触发器自检 ⇒ ❌ 我们那条 id=" + ours + " **不在候选里** ⇒ "
                            + "① 配表还没导出（xlsx 改了但 ModExcel/*.json 还是旧的）；"
                            + "② 或 dungeonType/dungeonID 被过滤。⇒ 这场决斗不会有队友");
                    return;
                }
                string mineBf = Convert.ToString(Refl.Get(mine, "battleFunction")) ?? "";
                if (mineBf.Length > 60) mineBf = mineBf.Substring(0, 60) + "…";
                SafeLog("触发器自检 ⇒ ✅ id=" + ours + " 在候选里；IsTrigger=" + JudgeTrigger(mine)
                        + " bf=" + mineBf);

                // ④ 重伤那条（阵亡 → 6009 → 190 弹窗 → 表挂「内外重伤」）
                if (hurt == null)
                {
                    SafeLog("触发器自检 ⇒ ❌ 重伤链 id=" + hurtRow + " **不在候选里** ⇒ "
                            + "队友倒下时不会弹 190、表也不会挂「内外重伤」（代码会在战后补挂，但**没有 UI**）"
                            + "｜多半是 MID&002 的 dungeonType 里没有 40，或还没导出");
                    return;
                }
                string hurtBf = Convert.ToString(Refl.Get(hurt, "battleFunction")) ?? "";
                SafeLog("触发器自检 ⇒ ✅ 重伤链 id=" + hurtRow + " 在候选里；IsTrigger=" + JudgeTrigger(hurt)
                        + " bf=" + (hurtBf.Length > 60 ? hurtBf.Substring(0, 60) + "…" : hurtBf));
            }
            catch (Exception e) { SafeLog("触发器自检异常（忽略）: " + e.GetType().Name + " " + e.Message); }
        }

        /// <summary>调引擎的 `IsTrigger(item)`。拿不到就明说拿不到，绝不默认成 false（同 `TriggerSelfCheck`）。</summary>
        private static string JudgeTrigger(object row)
        {
            try
            {
                var mi = g.conf.dungeonTrigger.GetType().GetMethod("IsTrigger",
                    BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static,
                    null, new[] { row.GetType() }, null);
                if (mi == null) return "?(找不到 IsTrigger(" + row.GetType().Name + "))";
                object r = mi.Invoke(null, new object[] { row });
                if (r is bool b)
                    return b ? "TRUE（引擎判定它会执行）" : "FALSE（引擎判定它不执行 ⇒ 条件/时机不对）";
                return "?(" + r + ")";
            }
            catch (Exception e) { return "?(调用失败：" + e.GetType().Name + ")"; }
        }

        private static void SafeLog(string s)
        {
            try { ModMain.P(Tag + s); } catch { }
        }
    }
}
