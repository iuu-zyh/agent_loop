/// <summary>
/// DramaSegmentRecorder —— 「剧情相遇分段器」的**接线层**：把真机上的**页事件**与**探针采样**
/// 喂给纯逻辑核 <see cref="DramaSegmentCore"/>，并对外只提供两件事：**记页**、**取增量**。
///
/// 为什么要有这一层（而不是把核直接塞进 `DramaAiOption`）：
///   · 判据表全在核里、且被单元测试锁住（`csharp/tests/DramaSegmentCoreTests`，与核同一份源码）；
///   · 核零 Unity 依赖 ⇒ 它能被测试，而**采样与日志必须碰 Unity**（`g.ui` / `g.timer` / 探针），
///     那些东西一旦混进核，判据就再也测不了了；
///   · 于是"错"与"接线错"能分开定位：核绿而行为不对 ⇒ 看这一层的 `[SegRec]` 日志。
///
/// ## 采样（每帧，主线程）
/// 读四个位，与定标探针 `DramaSegmentProbe` **同源**：
///   W  = 剧情窗可见（`NpcInitiativeMonitor.DramaWindowName()`，**唯一实现**，与忙判据/面板闸门共用）；
///   B1 = 逻辑战斗位（`g.world.battle.isBattle`）；
///   B2 = 战斗界面可见（`UIBattleInfo`，可见性判据复用 `NpcInitiativeMonitor.UiVisible`）；
///   A  = **游戏动作进行中**（`NpcInitiativeMonitor.ActionShowing()`，层顶 `ActionCourseInfo`）——
///        判"这次关窗是不是同一场交互的下一页"用（09-23 论道实测：页间隙层顶就是它）。
/// **加载层 / 预告窗不进判据**：09-22 真机实测加载层全程未亮、预告窗是攻击链专属（见探针记录）。
/// 帧回调在**第一次页事件**时注册（那时必然已经有剧情在跑），此后常驻——因为"关窗 → 收口"
/// 这一步必须继续被采样到，页停了也不能停。
///
/// ## 为什么记页的字段是**外部传进来**的
/// 说话人（谁在说）、对方编号、成品句，全部由 `DramaAiOption` 现成的判据算好后传进来 ——
/// 本层**不重复实现任何判据**（那是"唯一实现"规矩：`DetectSpeaker`/`ExtractText`/`FindNpcOf`
/// 各只有一份，在 UI 层）。判不出就传空串：核照记、字段留空，**绝不猜**（猜错是喂反事实）。
///
/// ## 记页的三种性质
///   · **正常注入的页**：入段，正常可投（`noInject=false`）；
///   · **闸 1 挡下的页**（`DramaGate.SuppressAiOption`，工具执行期弹的窗）：照记，**标记不投**；
///   · **闸 1.5 认领的页**（`DramaNativeClaim`，我方动作的异步后续）：照记，**标记不投**。
/// 后两种的内容已经当 tool result 给过模型，再投就是同一件事说两遍（模型会当成"又发生了一次"）。
///
/// 线程：全部主线程（`InitData` postfix / 点击回调 / `g.timer.Frame` 都是主线程）。
/// </summary>
using System;
using System.Collections.Generic;
using UnityEngine;

namespace AgentLoopBridge
{
    internal static class DramaSegmentRecorder
    {
        /// <summary>玩家显示名（`UnitIdentity.PlayerName` 的薄委托；跨文件 2 处调用点）。</summary>
        internal static string PlayerDisplayName()
        {
            return UnitIdentity.PlayerName();
        }

        /// <summary>诊断日志前缀。与定标探针 `[SegProbe]` 分开：那一个是"临时采样"，
        /// 这一条是**常驻功能的判据日志**，排障时各查各的。</summary>
        private const string Tag = "[SegRec] ";

        /// <summary>私有段上界（含）。`ModIds` 的段表最大偏移 999 ⇒ 覆盖全部自制窗。</summary>
        private const int PrivateIdMaxOffset = 999;

        private static readonly DramaSegmentCore Core =
            new DramaSegmentCore(ModIds.Mod, ModIds.Mod + PrivateIdMaxOffset, Log);

        // (帧回调的句柄/存活判断由 `FrameTicker` 持有 —— 11-19 去掉了全进程一次的 `_tickRegistered`)
        private static bool _warnedNoUnitId;

        // ==================================================================== 对外 API

        /// <summary>
        /// 记一页（`UIDramaBase.InitData` postfix 调）。**开段与续页的唯一入口**。
        /// </summary>
        /// <param name="unitId">对方的**精确编号**（`unitID`）。空串 = 判不出 ⇒ 本页不记（判不出就闭嘴）。</param>
        /// <param name="dramaId">页面 id。</param>
        /// <param name="speaker">"npc"/"player"/""（判不出）—— 由 UI 层判据给出。</param>
        /// <param name="name">说话人显示名（日志/对账用；判不出传空串）。</param>
        /// <param name="text">屏幕上的成品句，一字不改。</param>
        /// <param name="noInject">true = 本页属我方工具链（内容已当 tool result 给过模型）⇒ 照记但不投。</param>
        internal static void NotePage(string unitId, int dramaId, string speaker, string name, string text,
                                      bool noInject)
        {
            try
            {
                EnsureTick();
                // ★ 账本**先记**，且不受下面任何一条分支影响（10-25）★
                //   账本是全量流水：判不出归属的页（进来时 `unitId` 为空）也要留痕 ——
                //   真机上「进入副本」那一页（`610911`，选项「深入探索」）就是在这里被丢掉的。
                //   段保持原判据（判不出就闭嘴，不许猜归属），两者各管各的，互不反哺。
                DramaLedger.NotePage(unitId, dramaId, speaker, name, text);

                if (string.IsNullOrEmpty(unitId))
                {
                    if (!_warnedNoUnitId)
                    {
                        _warnedNoUnitId = true;
                        Log("对方取不到 unitID ⇒ 本页不入段（此后同类不再重复提示；账本照记）dramaId=" + dramaId);
                    }
                    return;
                }
                float now = Now();
                Core.OnPage(unitId, dramaId, speaker, name, text, noInject, now);
                Log("记页 dramaId=" + dramaId + " 对方=" + unitId
                    + " 说话人=" + (string.IsNullOrEmpty(speaker) ? "(判不出)" : speaker)
                    + (noInject ? "【不投：我方工具链】" : "")
                    + " " + Core.Snapshot(now));
            }
            catch (Exception e)
            {
                // 分段器是"附加价值"，任何异常都不许影响剧情链本身（同探针规矩）
                try { ModMain.P(Tag + "记页异常: " + e.Message); } catch { }
            }
        }

        /// <summary>
        /// 玩家点了某个选项（`DramaAiOption` 的选项监听调，09-23）。
        /// 文本 = **选项按钮上的成品句**（屏幕上的就是权威，与正文同一条哲学）；读不到就传空 ⇒ 核不记。
        /// 只往**该对方的活动段**里插一行；没有活动段时核会丢弃（选项不属于任何相遇就不该凭空造段）。
        /// </summary>
        internal static void NoteOption(string unitId, int dramaId, string text, string name)
        {
            try
            {
                if (string.IsNullOrEmpty(text)) return;
                // ★ 账本先记，且**不看归属**（10-25）★
                //   段要求"这个人的活动段存在"才记（没有段不凭空造），而账本是流水 ——
                //   真机里「选项点击：本页没有归属…（深入探索）」正是发生在**进副本**那一页上，
                //   恰恰是最该留下的一笔。段这边一个字都不改。
                DramaLedger.NoteOption(unitId, dramaId, text, name);

                if (string.IsNullOrEmpty(unitId)) return;
                Core.NoteOption(unitId, dramaId, text, name, Now());
            }
            catch (Exception e)
            {
                try { ModMain.P(Tag + "记选项异常: " + e.Message); } catch { }
            }
        }

        /// <summary>
        /// 点击那一刻重判的归属写回去（`DramaAiOption.Trigger` 调）。
        ///
        /// 为什么需要（真机教训的形状）：页事件发生在 `InitData`，而立绘压暗是 UI 侧逐页刷的，
        /// 刚回来的那一页读到的可能是**上一页**的明暗 —— 于是页时刻的归属有反的可能。
        /// 点击那一刻本页已彻底画好，所以那一刻的判定才作数（接线层原话），整段注入的最后一条
        /// 正是当前这一页 ⇒ 用它校正。判不出（空串）时核里会**拒绝覆盖**（已知不降级为未知）。
        /// </summary>
        internal static void NoteClickSpeaker(string unitId, int dramaId, string speaker, string name)
        {
            try
            {
                if (string.IsNullOrEmpty(unitId)) return;
                Core.OverrideSpeaker(unitId, dramaId, speaker, name, Now());
            }
            catch (Exception e)
            {
                try { ModMain.P(Tag + "点击重判写回失败: " + e.Message); } catch { }
            }
        }

        /// <summary>
        /// 取"这个对方**还没投过**的增量"并推进游标（点击「对话」时调）。**投完什么都不清。**
        ///
        /// 返回 false = 分段器名下**没有这个人的段** ⇒ 调用方退回旧的"单页注入"
        /// （分段器没记上时入口不许哑掉）；返回 true 且 `lines` 为空 = **有段但没有新增**
        /// ⇒ 调用方**什么也不发**（同一页反复点不重投；工具链的段本就不投）。
        /// </summary>
        internal static bool TryTakeDelta(string unitId, out List<SegmentLine> lines)
        {
            lines = new List<SegmentLine>();
            try
            {
                EnsureTick();
                if (string.IsNullOrEmpty(unitId)) return false;
                if (!Core.HasAnySegment(unitId)) return false;
                lines = Core.TakeDelta(unitId, Now());
                Log("取增量 对方=" + unitId + " 本次 " + lines.Count + " 条；" + Core.Snapshot(Now()));
                return true;
            }
            catch (Exception e)
            {
                try { ModMain.P(Tag + "取增量异常: " + e.Message); } catch { }
                lines = new List<SegmentLine>();
                return false;      // 异常 ⇒ 当"没有段"，退回旧的单页注入（宁可重复，不可哑掉）
            }
        }

        /// <summary>
        /// **区间读数**（工具结果专用，09-23）：读 `unitId` 在 `[from, until]` 内的**所有行**
        /// （页 + 选项，按时序）—— 工具结果不再自建内容台账，改读段的区间。
        /// **不碰投递游标**（「对话」的增量语义不受影响）；`consume=true` 读走即标消费。
        /// 返回 false = 这个人的段一个都没有（调用方按"没内容"处理，不猜）。
        /// </summary>
        internal static bool TryReadRange(string unitId, float from, float until, bool consume,
                                          out List<SegmentLine> lines)
        {
            lines = new List<SegmentLine>();
            try
            {
                if (string.IsNullOrEmpty(unitId)) return false;
                EnsureTick();
                lines = Core.ReadRange(unitId, from, until, consume);
                Log("区间读数 对方=" + unitId + " [" + from.ToString("0.0") + "," + until.ToString("0.0")
                    + "] 本次 " + lines.Count + " 行" + (consume ? "（已标消费）" : "（只读）"));
                return true;
            }
            catch (Exception e)
            {
                try { ModMain.P(Tag + "区间读数异常: " + e.Message); } catch { }
                lines = new List<SegmentLine>();
                return false;
            }
        }

        /// <summary>
        /// 按**链的锚点**读区间（工具结果的标准入口）：锚点取凭证的首次登记时刻
        /// （`DramaNativeClaim.AnchorOf`，早于 `CreateAction`/`ActionStart` ⇒ 覆盖首窗）；
        /// 凭证已没了（极少见）就回落 `fallbackFrom`。
        /// 返回 false = 这个人名下没有段（调用方按"没内容"处理）。
        /// </summary>
        internal static bool TryReadRangeFromChain(string unitId, float fallbackFrom, bool consume,
                                                   out List<SegmentLine> lines)
        {
            float from = fallbackFrom;
            try
            {
                float a = DramaNativeClaim.AnchorOf(unitId);
                if (a >= 0f && (from <= 0f || a < from)) from = a;
            }
            catch { }
            return TryReadRange(unitId, from, Now(), consume, out lines);
        }

        /// <summary>玩家显示名（渲染层要用）；取不到返回空串（判不出就闭嘴，不猜）。</summary>

        /// <summary>
        /// 区间行 → 结果载荷的 `lines` **单流**（Python 按顺序渲染，不再需要"按页号交错"）。
        /// 每行带 `kind`（page/option）/`who`（npc/player/choice）/`name`/`text`/`drama_id`；
        /// **顺序 = 发生时序**（追加序）。`playerName` 只在行自带名字缺失时兜玩家名。
        /// </summary>
        internal static Newtonsoft.Json.Linq.JArray LinesToJson(List<SegmentLine> lines, string playerName)
        {
            Newtonsoft.Json.Linq.JArray arr = new Newtonsoft.Json.Linq.JArray();
            if (lines == null) return arr;
            for (int i = 0; i < lines.Count; i++)
            {
                SegmentLine l = lines[i];
                string nm = l.Name ?? "";
                if (string.IsNullOrEmpty(nm) && (l.Speaker == "player" || l.Speaker == "choice"))
                    nm = playerName ?? "";
                arr.Add(new Newtonsoft.Json.Linq.JObject
                {
                    ["kind"] = string.IsNullOrEmpty(l.Kind) ? "page" : l.Kind,
                    ["who"] = l.Speaker ?? "",
                    ["name"] = nm,
                    ["text"] = l.Text ?? "",
                    ["drama_id"] = l.DramaId,
                });
            }
            return arr;
        }

        /// <summary>区间行数（只读、不消费）：给收口判据当"链上已经有内容了"的证据
        /// （`BattleOutcome` 的三条规则用它替代旧的自建计数）。</summary>
        internal static int CountRange(string unitId, float from, float until)
        {
            try
            {
                if (string.IsNullOrEmpty(unitId)) return 0;
                return Core.CountRange(unitId, from, until);
            }
            catch { return 0; }
        }

        /// <summary>区间里的**页行**数（只读；选项不算）：收口判据要的是"抓到了台词"。</summary>
        internal static int CountPages(string unitId, float from, float until)
        {
            try
            {
                if (string.IsNullOrEmpty(unitId)) return 0;
                return Core.CountPages(unitId, from, until);
            }
            catch { return 0; }
        }

        /// <summary>
        /// **链心跳**（09-23 用户定：段寿命由链驱动）：工具侧"还在等结果"时每几秒跳一次，
        /// 给这个人的挂起段续命 —— 长战斗/长论道不会被固定预算切成两段（战前那句因此不会
        /// 永远投不出去）。链一停（心跳过期）现行规则立刻接管。
        /// </summary>
        internal static void RenewChain(string unitId)
        {
            try
            {
                if (string.IsNullOrEmpty(unitId)) return;
                Core.RenewChain(unitId, Now());
            }
            catch { }
        }

        /// <summary>链结束（收口 / 取消）⇒ 提前清心跳，别让最后一跳多护一个 TTL。</summary>
        internal static void ReleaseChain(string unitId)
        {
            try
            {
                if (string.IsNullOrEmpty(unitId)) return;
                Core.ReleaseChain(unitId, Now());
            }
            catch { }
        }

        /// <summary>世界被换掉（读档/新建/重载，由 `ModMain.OnIntoWorldEvent` 统一调）：
        /// 那场相遇属于刚离开的世界，内容跟着存档走 ⇒ 段立刻全丢（同 `UnitIdRegistry` 的道理）。</summary>
        internal static void OnWorldChanged()
        {
            try
            {
                Core.OnWorldChanged(Now());
                _warnedNoUnitId = false;
            }
            catch (Exception e)
            {
                try { ModMain.P(Tag + "换世界清理失败: " + e.Message); } catch { }
            }
        }

        // ==================================================================== 帧采样

        /// <summary>确保帧回调在跑（11-19：句柄交给 `FrameTicker`）。
        ///
        /// ★注册形态变了，契约测试同步改了★ 原先这里锁的是字面量
        /// `g.timer.Frame(new Action(Tick), 1, true)`；现在「常驻 + 每帧一次」由 `FrameTicker`
        /// 统一保证（它按帧号去重，重复登记也只有一份干活），本处只剩「用哪个键 + 挂哪个体」。
        /// 契约改成断言"必须经 `FrameTicker.Ensure` 且键是 `KSegRec`" —— 比锁字面量更强：
        /// 锁字面量只能证明"这行代码还在"，锁键能证明"它进了那条统一的生命周期"。</summary>
        private static void EnsureTick()
        {
            FrameTicker.Ensure(FrameTicker.KSegRec, "segrec", Tick);
        }

        private static void Tick()
        {
            // 耗时采样（TickProfiler，09-28 卡顿定位）：每帧 UI 层扫描判据（DramaWindowName/ActionShowing）
            long __tp = TickProfiler.Begin();
            try
            {
                bool w = false;
                try { w = !string.IsNullOrEmpty(NpcInitiativeMonitor.DramaWindowName()); } catch { }
                bool b1 = false;
                try
                {
                    var b = g.world != null ? g.world.battle : null;
                    b1 = b != null && b.isBattle;
                }
                catch { }
                bool b2 = false;
                try
                {
                    var ui = g.ui;
                    if (ui != null) b2 = NpcInitiativeMonitor.UiVisible(ui.GetUI<UIBattleInfo>(UIType.BattleInfo));
                }
                catch { }
                // A：游戏动作 UI（`ActionCourseInfo`）。页开着时它被剧情窗盖住、采不到，
                // 只有**关窗那一刻**的层顶能读到它 —— 核正是拿那一刻的电平当交接证据。
                bool a = false;
                try { a = NpcInitiativeMonitor.ActionShowing(); } catch { }
                Core.Tick(w, b1, b2, a, Now());
            }
            catch { }
            finally { TickProfiler.End("segrec", __tp); }
        }

        // ==================================================================== 内部

        /// <summary>时间基准：`Time.unscaledTime`（与探针/DramaNativeClaim 同源）。
        /// **不用 `Time.time`**：它会被 `timeScale` 影响，而战斗/读条期我们正要它继续走。</summary>
        private static float Now()
        {
            try { return Time.unscaledTime; } catch { return 0f; }
        }

        /// <summary>
        /// 核的诊断出口。**必须自带墙钟**：`Player.log` 没有时间戳（踩过），而墙钟是
        /// 跨进程对账（Python 侧 `agent_loop.log`）的唯一钥匙 —— 用户报"NPC 说话怪怪的"时，
        /// 就靠这一行把它和 Python 那一侧的动作对上。
        /// </summary>
        private static void Log(string what)
        {
            try { ModMain.P(Tag + DateTime.Now.ToString("HH:mm:ss.fff") + " " + what); } catch { }
        }
    }
}
