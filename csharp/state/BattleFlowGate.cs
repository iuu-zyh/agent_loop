/// <summary>
/// BattleFlowGate —— 「战前 / 战中」两态的**接线层**（10-25，用户拍板）。
///
/// 【它解决什么】见 `state/BattleFlowCore.cs` 头注释（那里是判据与理由）。
/// 本文件只做四件事：
///   · 采样：按帧（节流）问两个电平 → `BattlePreShowing()` / `BattleActive()`；
///   · 喂事件：`CreateOptions`（页级）/ `ClickOption`（点选项）/ `IntoBattle`（开打）；
///   · 落盘：学到的"开战按钮"编号写进 `<数据根>/battle_flow_ids.json`（跨存档共用，可手改）；
///   · 给工具层一个答案：`VerdictOf(name, op)` 与 `RejectTextOf(...)`。
///
/// 【为什么"开战按钮"要学】
/// 三个案子的入口信号源不同（见核头注释）：寻仇页与切磋确认页靠**按钮编号**，
/// 副本靠**剧情命令说进副本**（`DramaFunction:IntoDungeon`）。后者的编号我们读不到
/// （`ConfDramaOptionsItem.function` 在真机上恒为 0，10-25 四个样本全是 0），
/// 但**可以在"命令说进副本"那一刻把"刚刚点的那一枚"记下来** —— 那是读取，不是猜：
/// 命令是在处理这次点击的过程里同步跑起来的。
///
/// 【为什么初始表是空的两个 + 三个已知的】
///   · `100003` = 10-25 寻仇页 20203 上那枚「战斗」（游戏自己打出的 `选项点击："战斗"`）；
///   · `212042` / `212041` 那一页（`21204`）—— `212042` 是「好，就让我和你切磋一下」，
///     配表事实（`DramaDialogue` id=21204 `options="212042|212041"`，仓库里早就有常量）。
/// 其余靠学。**表的两种错，代价不对称**（10-25 复核更正了原先那句"绝不会多挡"——那是错的）：
///   · **学少了**（表里没有这枚编号）⇒ 少挡一次，表现为"那次没拦住" —— 可观测、可补；
///   · **学错了**（把一枚普通选项记成开战按钮）⇒ 含这枚选项的页**一弹出来就进入战前**
///     ⇒ 攻击/切磋/传送/召唤被**持续误拒**，而且 `battle_flow_ids.json` 落盘后**跨存档、跨重启**
///     都在。这一条才是真正危险的，所以"学"的路径必须有护栏（`ClickScope`：只在点击的
///     调用栈里学，且**帧号不符就地作废**），而不是靠"反正只会少挡"的乐观假设。
///
/// 【为什么不在这里读 `OptionsFunction` 的函数名】
/// 实测它在一页剧情里被调用**三次以上**（开页 `DramaTool.OpenDrama`、装监听
/// `WorldSystemMgr.AddSystemInMap`、点选项 `ClickOption`），本身分不清"这是点击"；
/// 而寻仇那条路**根本没调用它**（10-25 18:51 从点「战斗」到进战斗，整段日志零 `DramaFunction`）。
/// 所以它只能当"副本这一类"的**补充证据**，不能当主判据。
/// </summary>
using System;
using System.Collections.Generic;
using System.IO;
using Newtonsoft.Json.Linq;
using UnityEngine;

namespace AgentLoopBridge
{
    internal static class BattleFlowGate
    {
        /// <summary>数据根下的文件名（跨存档共用：按钮编号是配表事实，不随存档变）。</summary>
        private const string FileName = "battle_flow_ids.json";

        /// <summary>采样节流（秒）。层顶扫描要跨语言读名，10-21 那次卡顿就是这类判据每帧全量算。
        /// 0.25s 对"几秒的战斗转场"远远够（真机最短的空洞是 2.2 秒）。</summary>
        private const double SampleSeconds = 0.25;

        /// <summary>内置的两个已知编号（见类头注释）。落盘文件缺失时用作初值。</summary>
        private static readonly int[] SeedIds = { 100003, 212042 };

        private static BattleFlowCore _core;
        // (加载标志随 Ensure 的 `_core != null` 判断，无需单独字段)
        // (帧回调的句柄/存活判断由 `FrameTicker` 持有 —— 11-19 去掉了全进程一次的 `_tickRegistered`)
        private static float _lastSampleAt = -999f;
        private static string _dirOverride;

        /// <summary>落盘目录（`ModMain` 注入；不注入则退回 `ModPaths.DiagDir`）。</summary>
        internal static void SetDir(string dir) { _dirOverride = dir; }

        internal static BattleFlowState State
        {
            get { Ensure(); return _core.State; }
        }

        internal static bool Armed
        {
            get { Ensure(); return _core.Armed; }
        }

        internal static bool IsBattleOption(int optionId)
        {
            Ensure();
            return _core.IsBattleOption(optionId);
        }

        // ------------------------------------------------------------------
        // 事件入口（全部吞异常：探针/判据绝不许拖垮游戏自己的链）
        // ------------------------------------------------------------------

        /// <summary>`CreateOptions` postfix 调（页选项建好了）。
        /// `dramaId` 是这一页的配表 id（`ui.item.id`），`optionIds` 是 `ui.activeOptionsData` 的 id 列表。</summary>
        internal static void NotePage(UIDramaBase ui)
        {
            try
            {
                Ensure();
                if (ui == null) return;
                int dramaId = 0;
                try { dramaId = ui.item == null ? 0 : ui.item.id; } catch { return; }
                int[] ids = ReadOptionIds(ui);
                string why;
                _core.NotePage(dramaId, ids, DateTime.UtcNow, out why);
                if (why.Length > 0) Log("状态：" + _core.State + " ⇐ " + why);
            }
            catch (Exception e) { Log("NotePage 异常（忽略）: " + e.Message); }
        }

        /// <summary>
        /// `ClickOption` **prefix** 进入：开启一个"正在处理这次点击"的作用域。
        /// 返回上一层的作用域编号（postfix 里还回去；正常恒为 0）。
        ///
        /// 判据本身在 `ClickScope`（纯逻辑、有 20 项断言），这里只负责把**当前帧号**喂进去 ——
        /// 帧号是本仓唯一允许的"结构"证据，理由见 `ClickScope` 的注释（同步调用 ⇒ 同一帧）。
        /// </summary>
        internal static int EnterClickScope(int optionId)
        {
            try { return _click.Enter(optionId, FrameNow()); }
            catch { return 0; }
        }

        /// <summary>`ClickOption` **postfix** 退出作用域。`prevId` 是进入时还回来的那一层。</summary>
        internal static void ExitClickScope(int prevId)
        {
            try { _click.Exit(prevId); } catch { }
        }

        /// <summary>当前帧号（取不到给 -1：`ClickScope` 会把它当"不是开启它的那一帧"⇒ 不学，
        /// 方向安全 —— 少挡一次，而不是学错一枚）。</summary>
        private static int FrameNow()
        {
            try { return Time.frameCount; } catch { return -1; }
        }

        /// <summary>`ClickOption` **prefix** 调（玩家点了选项，早于引擎跑效果）。
        /// 返回 true = 这一枚是开战按钮（诊断用）。</summary>
        internal static bool NoteOptionClicked(int optionId)
        {
            try
            {
                Ensure();
                if (optionId <= 0) return false;
                string why;
                _core.NoteOptionClicked(optionId, DateTime.UtcNow, out why);
                // `why` 非空 = 这条点击改变了状态（进战前 / 婉拒退出）；空 = 与状态无关（战中、或没点过开战按钮）。
                if (why.Length > 0) Log("状态：" + _core.State + " ⇐ " + why);
                return _core.IsBattleOption(optionId);
            }
            catch (Exception e) { Log("NoteOptionClicked 异常（忽略）: " + e.Message); return false; }
        }

        /// <summary>
        /// 「剧情命令说进副本」—— `DramaFunction:IntoDungeon` 跑过（由 `BattleFlowHooks` 的前缀报信）。
        /// **这是"学"的唯一入口**：把**正在被点击的那一枚**记成开战按钮。
        ///
        /// ★判据是**调用栈作用域**，不是"最近一次点击"、更不是时间窗★
        /// 真机栈（10-25 全量日志 4 次一致）：
        ///     UIDramaBase:ClickOption(ConfDramaOptionsItem)
        ///       → DramaFunction:OptionsFunction(String, DramaFunctionData)
        ///         → DramaFunction:IntoDungeon(String[])
        ///           → WorldBattleMgr:IntoBattle(…)
        /// 也就是说 `IntoDungeon` 是**同步跑在 ClickOption 内部**的。所以只要"此刻还在
        /// ClickOption 的作用域里"，因果就是确定的 —— 作用域外来的进副本命令
        /// （引擎自己发起的、与任何点击无关）**一律不学**（只当"开打了"用）。
        /// 这样就不存在"隔了多久还算不算"这个问题，也没有任何秒数参与。
        /// 作用域的记账与**泄漏自愈**（帧号）在 `ClickScope`（纯逻辑、有断言），这里只用它。
        /// </summary>
        internal static void NoteDungeonCommand(string how)
        {
            try
            {
                Ensure();
                int clicked;
                ClickScope.Probe hit = _click.OptionInScope(FrameNow(), out clicked);
                if (hit == ClickScope.Probe.Outside)
                {
                    Log("进副本命令（" + how + "）不在任何点击作用域内 ⇒ **不学**（引擎自己发起的，"
                        + "无从知道是哪一枚选项引起的；照常进战中）");
                }
                else if (hit == ClickScope.Probe.Expired)
                {
                    Log("进副本命令（" + how + "）落在**上一帧残留**的作用域上 ⇒ **不学**（已作废）。"
                        + "出现本行说明上次 ClickOption 的 postfix 没跑到（引擎那边抛过异常）——"
                        + "若不判帧号，这里就会把这条命令记到一枚无关选项上，之后永久误挡");
                }
                else if (hit == ClickScope.Probe.NoId)
                {
                    Log("进副本命令（" + how + "）在点击作用域内，但那枚选项的编号读不到 ⇒ 不学"
                        + "（`ConfDramaOptionsItem.id` 漏读——这行出现说明配表字段名变了）");
                }
                else if (!_core.IsBattleOption(clicked) && _core.AddBattleOption(clicked))
                {
                    Log("学到开战按钮 " + clicked + "（由" + how + "在点击作用域内确认）");
                    Save();
                }
                string why;
                _core.NoteBattleStart("IntoDungeon", DateTime.UtcNow, out why);
                Log("状态：" + _core.State + " ⇐ 剧情命令进副本（" + how + "）");
            }
            catch (Exception e) { Log("NoteDungeonCommand 异常（忽略）: " + e.Message); }
        }

        /// <summary>`WorldBattleMgr.IntoBattle` 前缀调（游戏真的要开打了）。
        /// 三个重载都在这里汇合 —— 我们只用它当"现在开打"的**边沿**，不拿重载当分类器
        /// （理由：`(WorldUnitBase, Int32, …)` 那一支今天只见过 1 次，样本不够当判据用）。</summary>
        internal static void NoteBattleStart(string overload)
        {
            try
            {
                Ensure();
                string why;
                if (_core.NoteBattleStart(overload, DateTime.UtcNow, out why))
                    Log("状态：" + _core.State + " ⇐ " + why);
            }
            catch (Exception e) { Log("NoteBattleStart 异常（忽略）: " + e.Message); }
        }

        /// <summary>世界重载（读档/换档）：状态清空，**学到的编号保留**。</summary>
        internal static void OnWorldChanged()
        {
            try { Ensure(); _core.Reset(); } catch { }
            // 作用域也要清：旧世界的一次点击不该给新世界的进副本命令当"因"。
            try { _click = new ClickScope(); } catch { }
        }

        // ------------------------------------------------------------------
        // 给工具层
        // ------------------------------------------------------------------

        /// <summary>这个动作工具此刻该怎么处置（见 `BattleFlowCore.Grade` 的粒度说明）。</summary>
        internal static ToolVerdict VerdictOf(string toolName, string op)
        {
            try
            {
                Ensure();
                if (!_core.Armed) return ToolVerdict.Pass;
                return BattleFlowCore.Grade(toolName, op);
            }
            catch { return ToolVerdict.Pass; }   // 判据坏了退回旧行为，绝不因此卡死动作系统
        }

        /// <summary>拒绝时回给模型的那句话（含"别重复调用"，缺了它会反复重试）。</summary>
        internal static string RejectTextOf(string toolName, string op)
        {
            try
            {
                Ensure();
                return BattleFlowCore.RejectText(toolName, op, _core.State);
            }
            catch { return "（这个动作此刻不能执行。）"; }
        }

        /// <summary>一行诊断（给「点对话」那一刻的日志用）。</summary>
        internal static string Describe()
        {
            try
            {
                Ensure();
                return "state=" + _core.State + ( _core.Confirmed ? "(已确认)" : "")
                     + " 表=" + _core.Count + " 号 lastPage=" + _core.LastPageId;
            }
            catch { return "state=(读不到)"; }
        }

        // ------------------------------------------------------------------
        // 采样（帧回调）
        // ------------------------------------------------------------------

        /// <summary>确保帧回调在跑（幂等）。接线层与 `ModMain` 都调都行。
        ///
        /// ★这个组件对"回调被清掉"最没有容错★ 全仓只有 `ModMain.Init` 一个调用点，它自己
        /// 不会因为"下次用到"而复活；而 `Attach` 在 `Init` 里、`Init` 在 `OnWorldChanged` 之前跑，
        /// 所以清标志的方案救不了它（详见 `FrameTicker` 头注释）。
        /// 现在交给 `FrameTicker`：`Init` 每次无条件走这里，若发现三十帧没动静就撤旧挂新。
        ///
        /// 为什么必须一直采样（而不是"用到时再查"）：它锁的是一条**记忆**——
        /// 真机 18:51:28.141 剧情页关 → 18:51:30.395 应战窗才弹，中间 2.2 秒两个电平同时为假，
        /// 而排队那条攻击正是 28.687 落刀。只在"有工具调用那一瞬"查，看到的永远是"没事"。</summary>
        internal static void Attach()
        {
            Ensure();
            FrameTicker.Ensure(FrameTicker.KBattleFlow, "battleflow", () =>
            {
                long __t = TickProfiler.Begin();
                try { Tick(); }
                finally { TickProfiler.End("battleflow", __t); }
            });
        }

        private static void Tick()
        {
            try
            {
                float now = Time.unscaledTime;
                if (now - _lastSampleAt < SampleSeconds) return;
                _lastSampleAt = now;

                bool pre = false, inBattle = false;
                // 判据只问事实层（同模块内，不经 `NpcInitiativeMonitor` 的转发）——
                // 这两个电平是 10-21 卡顿事故的敏感面，多绕一层就少一层排障依据。
                try { pre = WorldStateFacts.BattlePreShowing(); } catch { }
                try { inBattle = WorldStateFacts.BattleActive(); } catch { }

                // NPC 对 NPC 观战：复用这份采样做**状态边沿**收口（打过、现在不打了）。
                // 不新增定时器 —— 这里本来就在按 0.25s 采两个电平。
                try { NpcDuel.Tick(pre, inBattle); } catch { }

                string why;
                if (_core.NoteSampled(pre, inBattle, DateTime.UtcNow, out why))
                    Log("状态：" + _core.State + " ⇐ " + why);

                // 边沿诊断：真机对账要能看出"那一刻三个电平各是什么"
                if (pre != _lastPre || inBattle != _lastInBattle)
                {
                    Log("采样 应战窗=" + pre + " 战斗中=" + inBattle + " ⇒ " + _core.State);
                    _lastPre = pre;
                    _lastInBattle = inBattle;
                }
            }
            catch (Exception e) { Log("Tick 异常（忽略）: " + e.Message); }
        }

        private static bool _lastPre;
        private static bool _lastInBattle;

        // ------------------------------------------------------------------
        // 落盘
        // ------------------------------------------------------------------

        private static void Ensure()
        {
            if (_core != null) return;
            var ids = new List<int>(SeedIds);
            try
            {
                string path = FilePath();
                if (!string.IsNullOrEmpty(path) && File.Exists(path))
                {
                    var o = JObject.Parse(File.ReadAllText(path));
                    var arr = o["battle_option_ids"] as JArray;
                    if (arr != null)
                        foreach (var t in arr)
                        {
                            int v = 0;
                            try { v = t.Value<int>(); } catch { }
                            if (v > 0) ids.Add(v);
                        }
                }
            }
            catch (Exception e) { Log("读 " + FileName + " 失败（用内置表）: " + e.Message); }

            _core = new BattleFlowCore(ids, BattleFlowCore.DefaultFallbackSeconds);
            Log("已接线：内置/落盘的认战前按钮共 " + _core.Count + " 个，兜底 "
                + _core.FallbackSeconds + "s，采样 " + SampleSeconds + "s/次");
        }

        private static string Dir()
        {
            if (!string.IsNullOrEmpty(_dirOverride)) return _dirOverride;
            try { return ModPaths.DiagDir; } catch { return null; }
        }

        private static string FilePath()
        {
            string d = Dir();
            return string.IsNullOrEmpty(d) ? null : Path.Combine(d, FileName);
        }

        private static void Save()
        {
            try
            {
                string path = FilePath();
                if (string.IsNullOrEmpty(path)) return;
                var arr = new JArray();
                foreach (int id in _core.BattleOptionIds) arr.Add(id);
                var o = new JObject
                {
                    ["_note"] = "认战前的通用选项编号（本 mod 自动积累，跨存档共用）。"
                              + "删掉某一项 = 那个按钮不再被当成开战入口；全删 = 只剩内置的两个。",
                    ["battle_option_ids"] = arr,
                };
                File.WriteAllText(path, o.ToString(Newtonsoft.Json.Formatting.Indented));
            }
            catch (Exception e) { Log("写 " + FileName + " 失败（本次不落盘）: " + e.Message); }
        }

        // ------------------------------------------------------------------
        // 读这一页有哪些选项编号（与 DramaConfDump 同源读法；读不到就给空数组）
        // ------------------------------------------------------------------

        private static int[] ReadOptionIds(UIDramaBase ui)
        {
            try
            {
                var list = ui.activeOptionsData;
                if (list == null) return new int[0];
                int n = list.Count;
                var ids = new List<int>(n);
                for (int i = 0; i < n; i++)
                {
                    ConfDramaOptionsItem oi = null;
                    try { oi = list[i]; } catch { continue; }
                    if (oi == null) continue;
                    int id = 0;
                    try { id = oi.id; }
                    catch { try { id = (int)((dynamic)oi).id; } catch { id = 0; } }
                    if (id > 0) ids.Add(id);
                }
                return ids.ToArray();
            }
            catch { return new int[0]; }
        }

        /// <summary>
        /// "正在被点击的那一枚选项"（`ClickOption` 作用域内有效，见 `NoteDungeonCommand`）。
        /// **不是"最近一次点击"**：作用域外它报 0，代表"此刻没有点击在进行"——
        /// 引擎自己发起的进副本命令一律不学。记账与自愈都在 `ClickScope`。
        /// </summary>
        private static ClickScope _click;
        private static void Log(string msg)
        {
            try { ModMain.P("[BattleFlow] " + msg); } catch { }
        }
    }
}
