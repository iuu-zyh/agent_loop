/// <summary>
/// NPC 主动互动触发监测 —— 感知侧（只在游戏主线程跑）
///
/// 分层分权（对齐神识传音 §3.4 主动交互）：
///  - 本类只做「何时 / 对谁 / 什么意图」的触发检测：挂 g.events.On(WorldAddDay) 每游戏日一次，
///    内部节流一日一试 + 单NPC双冷却（现实600s + 游戏3日），产出事件经 WsClient.SendInitiative 发出；
///  - Python 端 ChatHub.handle_initiative 负责把意图变成开口回合（LLM 编排），
///    本类不碰对话逻辑、不碰提示词（意图文案在 Python initiative.py，可热改）。
///
/// 触发实现（混合日节拍 + 状态闸）：
///  - 全局：每游戏日最多尝试一次（OnWorldAddDay），先过状态闸（战斗/确认窗），再过 0-100 日概率
///    （<阈值好感减半，对齐神识传音 us_strings:2047）；
///  - 单NPC：双冷却 max(现实 NpcCooldownRealSec, 游戏 NpcCooldownDays)；
///  - 候选 = 玩家关系记录全集（十容器+好友簿+**仇人簿**+GetAllGoodRelationUnitID 含敌）+ 手动通讯录好友
///    + 同格所有人（陌生人当面可搭话）；门槛：非通讯录成员仅同格可发起（见 README.md 附录二 G（功能设计底稿））；
///  - 意图按亲密度派发：负（含仇人）→负向4条，正→5条，高好感≥120 40%进思念/情感；BuildReason 配缘由。
///
/// 真机联调 TODO（与 ToolExecutor 同风格）：
///  - 战斗/确认窗等状态闸的准确判定待真机核对，当前 best-effort + try/catch 放行。
///
/// 诊断强制触发（自主交互系统观测加速器）：
///  - `&lt;Mod根&gt;\_diag_initiative_force.txt` 存在即进入强制模式（DiagSwitches 每 ~2s 复查，
///    增删文件即时生效，不需要重启游戏/Python）；
///  - 只放宽「时间」四闸：日节拍、同日夜守卫、每日概率、单人双冷却 → 改为按现实秒节拍
///    （默认 20s，文件 `interval=` 可改）持续触发，不必推进游戏日；
///  - 候选集、通讯录/同格门槛、意图派发（`intent=` 可指定）、当面确认窗全部仍走真实链路；
///  - 强制模式**不写冷却表**（关掉开关后不留痕，不影响正常游玩判定）。
/// </summary>
using System;
using System.Collections;
using System.Collections.Generic;
using System.Reflection;
using UnityEngine;

namespace AgentLoopBridge
{
    public class NpcInitiativeMonitor
    {
        private readonly WsClient _ws;

        // —— 游戏日节拍 ——
        private int _lastDay = -1; // 上次尝试的 totalDays

        // —— 单NPC冷却（双闸）——
        private readonly Dictionary<string, DateTime> _cooldownReal = new Dictionary<string, DateTime>();
        private readonly Dictionary<string, int> _cooldownDay = new Dictionary<string, int>();

        // —— 可配参数（默认值对齐混合方案，config 热更）——
        private int _dailyChance = 15;          // 0-100，每日总闸，0=关闭，100=日日必试
        private int _npcCooldownDays = 3;       // 单人游戏日冷却
        private int _npcCooldownRealSec = 600;  // 单人现实秒冷却（防挂机不推日）
        private int _lowThresh = 60;            // <阈值视为低好感（2星≈60）
        private bool _lowHalve = true;          // 低好感概率减半
        /// <summary>主动互动总开关（config `initiative.enabled`，新增）。
        /// false = 关掉**日节拍**（玩家可见的正常触发：`TryTriggerForDay` 直接返回）；
        /// `_diag_initiative_force.txt` 强制路径**不受影响**——那是开发诊断，不是玩法开关。
        /// 玩家主动开窗对话、工具、其他一切都不受影响（本开关只管 NPC 自己找上门）。</summary>
        private bool _enabled = true;
        private bool _disabledLogged;           // 「总开关关着」只吼一次（重新打开时复位）

        // 意图键（与 Python initiative.py 的 INITIATIVE_INTENTS 对齐）
        private static readonly string[] PositiveIntents = { "greet", "smalltalk", "courteous", "life", "recent" };
        private static readonly string[] PositiveCloseIntents = { "missing", "affection" };
        private static readonly string[] NegativeIntents = { "malice", "vent", "provocation", "disdain" };
        /// <summary>全集：诊断强制模式校验 intent= 是否合法用。</summary>
        private static readonly string[] AllIntents = {
            "greet", "smalltalk", "courteous", "life", "recent",
            "missing", "affection",
            "malice", "vent", "provocation", "disdain",
        };

        // —— 触发时刻的「可否当面弹出」备忘（09-13 用户拍板 (b)）——
        // 收尾分流②此前用**回复到达那一刻**的同格/忙闲重新判一次，于是出现
        // "触发时异地（走传音）→ 回复时 NPC 走过来变同格 → 没同意就当面开窗"
        // （实机复现两次：22:17 触发 sameGrid=False / 22:18 分流② 同格=True；
        //   22:58 又复现一次，两条日志并列摆着）。现在把**触发时**的判定记下来，收尾只认它。
        // 值 = (sameGrid && !busy)：busy 的触发本就走直发传音（弹窗=打扰），同样不该事后补弹。
        private static readonly Dictionary<string, bool> _canPopAtTrigger = new Dictionary<string, bool>();

        /// <summary>记下本次触发的"可否当面弹出"（触发时判定；ContactDuty 收尾分流②只认它）。</summary>
        internal static void RememberTriggerPop(string npc, bool sameGrid, bool isBusy)
        {
            if (string.IsNullOrEmpty(npc)) return;
            _canPopAtTrigger[npc] = sameGrid && !isBusy;
        }

        /// <summary>该 NPC 最近一次触发时是否"同格且不忙"（未知=false：不弹，宁可只亮红点）。</summary>
        internal static bool CanPopAtTrigger(string npc)
        {
            if (string.IsNullOrEmpty(npc)) return false;
            return _canPopAtTrigger.TryGetValue(npc, out var v) && v;
        }

        // —— 诊断强制模式计时（_diag_initiative_force.txt）——
        private float _forceAcc;    // 距上次强制触发的现实秒累计
        private bool _forceSpent;   // 「一次即停」：本次武装已收口（见 MarkForceSpent）
        private long _forceSpentStamp;  // 收口时的开关文件时间戳（变更即重新武装）

        // —— 同意后自动开窗（用户拍板：立刻开窗吃流式）——
        // 为什么要延迟几帧：选项回调此刻正处在"剧情窗关闭/翻页"的窗口期，而 09-10 事故的
        // 根因之一就是"在剧情窗创建/销毁期调 g.ui.OpenUI"（会打乱 UI 层布局、把立绘槽位
        // 打成 0 尺寸）。推迟到窗口关完再开，既保住"点同意立刻有反应"，又绕开那个窗口期。
        //
        // 修（同格实感延迟的真凶）：旧版是"3 帧后**只试一次**"，失败就退化成
        // "回复到达时再弹"（= 玩家点完同意干等十几秒才蹦出窗口）。而失败的原因恰恰是那把
        // 铁律闸：`AbPanelProber.CanCreateNow` 判 `g.ui.GetUI("UICustomDramaDyn")!= null`
        // —— `DramaUiName` **就是我们自己这个确认窗的名字**，且判的是**存在**不是可见
        // （`AbChatPanel` 注释明说"管理器 CloseUI 不一定销毁实例"）。所以"本局第一次同格互动"
        // 极易命中：点同意 → 3 帧后闸门还拦着 → 宿主建不出来 → 回退成回复时才开窗。
        // 现在改成：3 帧后**每帧重试**，成功即止，`PendingOpenTimeoutSec` 秒超时仍回落旧路径。
        // 零回归：若 3 帧时闸门本就通过，第 1 次就成功，行为与旧版完全一致（日志多一个"第 1 次"）。
        private WorldUnitBase _pendingOpenUnit;
        private int _pendingOpenFrames;
        private float _pendingOpenDeadline;   // 重试截止（Time.unscaledTime）
        private int _pendingOpenTries;        // 已尝试次数（成功日志/超时日志用）
        private bool _pendingOpenWarned;      // 首次失败打一行（之后静默重试，防刷屏）
        private const int PendingOpenDelayFrames = 3;
        private const float PendingOpenTimeoutSec = 5f;

        public NpcInitiativeMonitor(WsClient ws)
        {
            _ws = ws;
        }

        /// <summary>热更配置（ModMain 拉 config 后调）。 clamp 0-100。
        /// `enabled` = 主动互动总开关（新增）：保存配置后由 `ConfigPresenter` 立刻重拉一次
        /// （`ModMain.RefreshInitiativeFromConfig`）→ 全部字段热生效，不必重启 Python。</summary>
        public void Configure(int dailyChance, int cooldownDays, int cooldownRealSec, int lowThresh,
                              bool lowHalve, bool enabled = true)
        {
            _dailyChance = Math.Max(0, Math.Min(100, dailyChance));
            _npcCooldownDays = Math.Max(0, cooldownDays);
            _npcCooldownRealSec = Math.Max(0, cooldownRealSec);
            _lowThresh = Math.Max(0, lowThresh);
            _lowHalve = lowHalve;
            bool changed = _enabled != enabled;
            _enabled = enabled;
            if (enabled) _disabledLogged = false;   // 重新打开后，下次关闭还要能吼一声
            ModMain.P($"[NpcInitiativeMonitor] config: dailyChance={_dailyChance} cooldownDays={_npcCooldownDays} realSec={_npcCooldownRealSec} lowThresh={_lowThresh} lowHalve={_lowHalve} enabled={_enabled}"
                      + (changed ? "（总开关状态变化 → 即时生效，无需重启）" : ""));
        }

        /// <summary>兼容旧 Frame 挂载：保留但转日节拍（过渡期双挂不重）。</summary>
        public void OnUpdate()
        {
            // 诊断（因果对照用）：mod 被整体停用 / 单独停监测器后不再做任何实质工作
            if (ModMain.Suspended) return;
            // 待开窗（同意后自动开窗）：放在所有闸与强制模式分支**之前**——
            // 它是一次 UI 收尾动作，与"今天还要不要触发"无关；放在后面会被 IgnoreState/
            // 强制模式/日节拍的 return 吃掉。
            TickPendingOpen();
            if (DiagSwitches.NoMonitor) return;
            // 诊断强制模式（_diag_initiative_force.txt）：跳过日节拍/概率/冷却，按现实秒节拍持续触发
            if (DiagSwitches.InitiativeForce) { ForceTick(); return; }
            // 旧 Frame 仍挂着时，按日节拍兜底（若 WorldAddDay 未触发，如读档当日）
            try
            {
                int cur = CurrentGameDay();
                if (cur == -1) return; // 取不到日历时静默
                if (cur == _lastDay) return;
                // 仅当距离上次超过 1 日才走 Frame 兜底，避免与事件重复
                if (_lastDay != -1 && cur - _lastDay < 1) return;
                TryTriggerForDay(cur);
            }
            catch (Exception e)
            {
                ModMain.P("[NpcInitiativeMonitor] OnUpdate tick error: " + e.Message);
            }
        }

        /// <summary>同意后自动开窗的帧级延迟执行（OnUpdate 每帧调一次；见 _pendingOpenUnit 注释）。
        /// 3 帧后开始尝试，失败则**每帧重试**至 5s 超时（旧版只试一次 → 退化成回复时才开窗）。</summary>
        private void TickPendingOpen()
        {
            if (_pendingOpenUnit == null) return;
            if (--_pendingOpenFrames > 0) return;
            var unit = _pendingOpenUnit;
            if (_pendingOpenTries == 0) _pendingOpenDeadline = Time.unscaledTime + PendingOpenTimeoutSec;
            _pendingOpenTries++;
            try
            {
                if (unit == null) { ResetPendingOpen(); return; }
                string name = NameOf(unit);
                if (ChatLauncher.OpenForUnit(unit))
                {
                    ChatLauncher.SetThinking(true);   // 首 token 前 4~13s 的"对方正在斟酌…"
                    ModMain.P("[NpcInitiativeMonitor] 同意后已自动开窗（流式直落该窗）：name=" + name +
                              "（第 " + _pendingOpenTries + " 次尝试成功）");
                    ResetPendingOpen();
                    return;
                }
                // 未成功：多半是宿主创建闸撞上"我们自己那个确认窗实例还没销毁"（见字段区注释）。
                // 首次失败留一行现场，之后静默重试——每帧都打会把日志刷爆。
                if (!_pendingOpenWarned)
                {
                    _pendingOpenWarned = true;
                    ModMain.P("[NpcInitiativeMonitor] 同意后开窗第 1 次未成功（宿主创建被闸住，多为确认窗实例未销毁）" +
                              "→ 每帧重试至 " + PendingOpenTimeoutSec + "s：name=" + name);
                }
                if (Time.unscaledTime >= _pendingOpenDeadline)
                {
                    ModMain.P("[NpcInitiativeMonitor] 同意后自动开窗重试 " + _pendingOpenTries +
                              " 次仍未成功（回退：回复到达时 ContactDuty 分流②再弹）name=" + name);
                    ResetPendingOpen();
                }
            }
            catch (Exception e)
            {
                ModMain.P("[NpcInitiativeMonitor] TickPendingOpen: " + e.Message);
                ResetPendingOpen();
            }
        }

        /// <summary>清掉"同意后待开窗"的全部状态（成功/超时/异常/被顶替共用）。</summary>
        private void ResetPendingOpen()
        {
            _pendingOpenUnit = null;
            _pendingOpenFrames = 0;
            _pendingOpenTries = 0;
            _pendingOpenWarned = false;
            _pendingOpenDeadline = 0f;
        }

        /// <summary>游戏日事件（g.events.On WorldAddDay 调）。</summary>
        public void OnWorldAddDay(ETypeData e)
        {
            if (DiagSwitches.NoMonitor) return;   // 诊断开关：停监测器
            try
            {
                int cur = CurrentGameDay();
                if (cur == -1) { cur = _lastDay + 1; if (_lastDay == -1) cur = 0; }
                if (cur == _lastDay) return;
                TryTriggerForDay(cur);
            }
            catch (Exception ex)
            {
                ModMain.P("[NpcInitiativeMonitor] OnWorldAddDay error: " + ex.Message);
            }
        }

        // ---------- 日触发 ----------

        private void TryTriggerForDay(int curDay)
        {
            _lastDay = curDay;
            if (!_enabled)
            {
                // 主动互动总开关关着（config initiative.enabled）→ 日节拍整条不走。
                // 只吼一次（每天一次太吵；重新打开后下次关闭会再吼）。
                // 注意：诊断强制路径（_diag_initiative_force.txt）不走这里，照旧可用。
                if (!_disabledLogged)
                {
                    _disabledLogged = true;
                    ModMain.P("[NpcInitiativeMonitor] 主动互动总开关已关闭（initiative.enabled=false）" +
                              "→ 日节拍不再触发；玩家主动对话/工具不受影响。要恢复：配置面板（对话窗 ⚙）打开开关，或改 config.json");
                }
                return;
            }
            if (_ws == null) return;
            string speak = SpeechBlockReason();
            if (speak.Length > 0)
            {
                // 一日一次，不刷屏；这行是"正常游玩时 NPC 从不主动"的第一现场证据
                ModMain.P("[NpcInitiativeMonitor] 日节拍跳过：能说闸命中（原因：" + speak + "）day=" + curDay);
                return;
            }
            // 忙碌不禁言语（两级闸）：只把 busy 交给 FireOne → 事件帧带 busy:true，
            // Python 端本回合只给只读工具（动作工具剔除），同格也不弹确认窗而直接传音。
            string busy = BusyReason();

            // 全局日概率（0=关，100=必试；低好感减半在候选内按人再半）
            if (_dailyChance <= 0) return;
            if (_dailyChance < 100)
            {
                // 全局骰：<dailyChance 才继续
                if (UnityEngine.Random.Range(0, 100) >= _dailyChance) return;
            }

            var contactIds = new HashSet<string>();
            var candidates = BuildCandidates(contactIds, false);
            if (candidates.Count == 0) return;
            var player = g.world.playerUnit;

            // 按好感降序，优先高好感（思念/情感更可信），同好感随机打散
            candidates.Sort((a, b) =>
            {
                int ia = SafeIntim(a);
                int ib = SafeIntim(b);
                if (ib != ia) return ib.CompareTo(ia);
                return UnityEngine.Random.Range(-1, 2);
            });

            foreach (var wub in candidates)
            {
                try
                {
                    if (wub == null) continue;
                    string name = NameOf(wub);
                    if (string.IsNullOrEmpty(name)) continue;
                    if (IsCoolingDown(name, curDay)) continue;

                    // 通讯录门槛：非通讯录成员只有同格（当面）才能发起；通讯录成员任意位置可传音
                    string unitId = null;
                    try { unitId = wub.data.unitData.unitID; } catch { }
                    bool inContacts = unitId != null && contactIds.Contains(unitId);
                    bool sameGrid = player != null && UnitSnapshot.IsSameGrid(wub, player);
                    if (!inContacts && !sameGrid) continue;   // 结构上不会发生（非同格陌生人不在候选里），保险断言

                    int intim = SafeIntim(wub);
                    // 低好感减半：陌生/仇人 50% 跳过（对齐 us_strings:2047）
                    if (_lowHalve && intim < _lowThresh)
                    {
                        // 也处理仇恨负值：Math.Abs
                        // 负仇恨绝对值小也算低
                        if (UnityEngine.Random.value < 0.5f) continue;
                    }

                    string intent = PickIntent(intim);
                    string reason = BuildReason(intim);

                    // 冷却先落：无论玩家同意与否，本次自主交互都视为已发生（README.md 附录二 G（功能设计底稿））
                    _cooldownReal[name] = DateTime.Now;
                    _cooldownDay[name] = curDay;

                    FireOne(wub, player, name, intim, sameGrid, intent, reason, curDay, false, busy);
                    return; // 一日只一人
                }
                catch (Exception e)
                {
                    ModMain.P("[NpcInitiativeMonitor] candidate skipped: " + e.Message);
                }
            }
        }

        // ---------- 发起（日节拍与诊断强制模式共用，保证两条路径同源） ----------

        /// <summary>发起一次主动交互——只做派发，不判定任何闸门（闸门判定在各自调用方）。
        /// 抽出来是为让"日节拍"与"诊断强制"两条路径共享同一份 意图文案/确认窗/日志，
        /// 避免诊断路径悄悄偏离真实路径。curDay 仅供日志。
        ///
        /// busy（两级闸）：玩家正忙时 —— ① 同格**不弹确认窗**，降级为直接传音
        /// （弹窗=打扰，与"忙碌只言语"矛盾）；② 事件帧带 busy:true，Python 端本回合剔除动作工具。</summary>
        private void FireOne(WorldUnitBase wub, WorldUnitBase player, string name, int intim, bool sameGrid,
                             string intent, string reason, int curDay, bool forced, string busy)
        {
            // 末道断言：自主交互的语义恒为「NPC → 玩家」，目标永不可为玩家本人。
            // 候选集已在 BuildCandidates 末端剔除，这里是**与来源无关的兜底**——万一今后
            // 又冒出新的候选来源（或 ForceTick 直连），也绝不再出现"自己给自己传音"。
            if (UnitSnapshot.IsPlayerUnit(wub))
            {
                ModMain.P("[NpcInitiativeMonitor] 拒绝发起：目标是玩家自身（name=" + name +
                          "）——候选集过滤失守的兜底，请查候选来源");
                return;
            }
            bool isBusy = busy.Length > 0;
            // 诊断开关：把本次强制触发**按异地判定**，用于可复现地观察"异地传音"那条路
            // （不弹确认窗 → 直发 → 收尾落分流③ → HUD 横幅 + 传钮红点）。只影响 forced 路径。
            bool forceRemoteDiag = forced && DiagSwitches.InitiativeForceForceRemote;
            if (forceRemoteDiag) sameGrid = false;
            bool noToolsDiag = forced && DiagSwitches.InitiativeForceNoTools;
            // 触发时刻的判定存下来：收尾分流只认它，不再用"回复到达时"的状态重判一次
            // （否则异地传音的 NPC 走过来就会变成"没同意就当面开窗"）。
            RememberTriggerPop(name, sameGrid, isBusy);
            // 标记"该 NPC 有一个自主开口回合在路上"：本回合若真的执行了动作工具，ActionWatcher
            // 会在**动作完成那一刻**按当时的同格/忙闲决定是否把对话 UI 推给玩家。
            // 只对自主开口回合生效——玩家自己发起的对话，窗本来就在玩家眼前。
            // 第二参数（第三版）= 本回合是否**已经**因"当面确认窗 + 点同意"开过窗：
            // 同格当面路径（下面 if 分支）点同意即开窗 → 动作完成不再补开；异地/force_remote 传 false
            // → 保留"动作完成即开窗"。（同格+忙 不会到这里：忙 = 无动作工具，见 ActionWatcher 头注释）
            ActionWatcher.NoteInitiativeTurn(name, sameGrid && !isBusy);
            if (sameGrid && !isBusy)
            {
                // 当面：先弹确认窗（立绘照常），玩家同意才生成对话——婉拒=无声中断不浪费回合，
                // 冷却已前置所以婉拒同样计入（用户拍板的语义）。异地（传音）不弹窗，维持直发。
                string npcName = name;
                ShowDramaService.ShowConfirmSimple(
                    ModIds.DramaInitiativeBase,
                    player,     // left = 屏幕左位 = 玩家（★09-12 修正★：原先传的是 wub，
                                //             立绘左右与其余四个工具确认窗相反 → 用户实机看到
                                //             "右侧立绘是玩家"；语义见 ShowConfirmSimple 注释）
                    wub,        // right = 屏幕右位 = 对方 NPC
                    $"{name} 希望向你发出互动",
                    "同意", "不同意",
                    onOk: () =>
                    {
                        // ① 先发事件：让回合尽早开始（LLM 首 token 要 4~13s，不占在开窗上）；
                        // ② 再排"延迟几帧开窗"：绕开剧情窗关闭窗口期，开完就吃流式。
                        _ws.SendInitiative(npcName, intent, reason, consented: true, debug: forced, busy: isBusy,
                                           speechOnly: noToolsDiag);
                        ModMain.P($"[NpcInitiativeMonitor] consented trigger name={npcName} intent={intent} intim={intim}");
                        ResetPendingOpen();   // 清上一次的残留计数（重试次数/告警位）
                        _pendingOpenUnit = wub;
                        _pendingOpenFrames = PendingOpenDelayFrames;
                        if (forced) MarkForceSpent("玩家已同意");
                    },
                    onNo: () =>
                    {
                        ModMain.P($"[NpcInitiativeMonitor] declined by player: name={npcName} intent={intent}");
                        // 婉拒也停表：玩家已经明确表态过这一轮，再每 20s 弹一次纯属打扰；
                        // 想继续测就改一下开关文件或写 repeat=1。
                        if (forced) MarkForceSpent("玩家已婉拒");
                    });
            }
            else
            {
                // 异地，或同格但玩家忙（降级传音：不弹窗、只走未读+横幅）
                _ws.SendInitiative(name, intent, reason, debug: forced, busy: isBusy, speechOnly: noToolsDiag);
                // 这条路径没有"玩家点击"这个收口点，发出即算一次（一次即停：不再连发）
                if (forced) MarkForceSpent(busy.Length > 0 ? "已降级直发传音（玩家忙）" : "已发出传音（异地）");
            }
            string tail = isBusy ? (" busy=True（" + busy + "）") : "";
            if (forced)
            {
                ModMain.P($"[NpcInitiativeMonitor] forced trigger（诊断强制）day={curDay} name={name} intent={intent} intim={intim} sameGrid={sameGrid} interval={DiagSwitches.InitiativeForceInterval}s{tail}" +
                          (forceRemoteDiag ? " forceRemote=ON（按异地判定 → 应落分流③：横幅+红点）" : "") +
                          (noToolsDiag ? " noTools=ON（本回合一个工具都不调）" : ""));
            }
            else
            {
                ModMain.P($"[NpcInitiativeMonitor] trigger day={curDay} name={name} intent={intent} intim={intim} sameGrid={sameGrid} dailyChance={_dailyChance}{tail}");
            }
        }

        // ---------- 诊断强制模式（_diag_initiative_force.txt） ----------

        /// <summary>强制模式节拍：按现实秒累计（unscaled，不受暂停/倍速影响），到点触发一次。</summary>
        private void ForceTick()
        {
            try
            {
                // 一次即停：成功发起过一次（同意/婉拒/已发出传音）后就停表，
                // 不再每 interval 秒弹同格确认窗打扰游玩。重新武装 = 改一下开关文件
                // （内容/保存时间变化 → mtime 变化即视为"再来一次"）；写 repeat=1 恢复连发。
                if (_forceSpent && !DiagSwitches.InitiativeForceRepeat)
                {
                    long stamp = DiagSwitches.InitiativeForceStamp;
                    if (stamp == _forceSpentStamp) { _forceAcc = 0f; return; }
                    _forceSpent = false;
                    ModMain.P("[NpcInitiativeMonitor] 诊断强制：开关文件已变更 → 重新武装（可再来一次）");
                }
                _forceAcc += Time.unscaledDeltaTime;
                float interval = DiagSwitches.InitiativeForceInterval;
                if (interval < 1f) interval = 1f;
                if (_forceAcc < interval) return;
                _forceAcc = 0f;
                TryTriggerForced();
            }
            catch (Exception e)
            {
                ModMain.P("[NpcInitiativeMonitor] force tick error: " + e.Message);
            }
        }

        /// <summary>「一次即停」落表：记录本次收口原因 + 当时的开关文件时间戳
        /// （之后只有 mtime 变化才重新武装）。仅对诊断强制路径调用。</summary>
        private void MarkForceSpent(string why)
        {
            if (_forceSpent) return;
            _forceSpent = true;
            _forceSpentStamp = DiagSwitches.InitiativeForceStamp;
            ModMain.P("[NpcInitiativeMonitor] 诊断强制：本次已收口（" + why + "）→ **停表**，不再弹。" +
                      "要再来一次：改一下 _diag_initiative_force.txt（保存即重新武装）；" +
                      "要连发：文件里写 repeat=1。");
        }

        /// <summary>强制触发一次：只放宽「时间」四闸（日节拍/同日夜守卫/每日概率/双冷却），
        /// 其余全部走真实链路——候选集与门槛照旧（BuildCandidates + 通讯录/同格判定），
        /// 意图默认按亲密度真实派发（intent= 可指定），同格仍弹确认窗。
        /// 不写冷却表：关掉开关后不留痕，不影响正常游玩的判定。</summary>
        private void TryTriggerForced()
        {
            if (_ws == null) return;
            int curDay = CurrentGameDay();
            // 能说闸（未进世界/死亡窗）→ 丢弃；忙碌不拦言语（两级闸），只把 busy 带到事件里
            string speak = SpeechBlockReason();
            string busy = BusyReason();
            // 层快照：拦与放行都打，用来核对层判据在真实状态下的取值
            string layers = LayerSnapshot();
            if (speak.Length > 0 && !DiagSwitches.InitiativeForceIgnoreState)
            {
                ModMain.P("[NpcInitiativeMonitor] forced trigger 跳过：能说闸命中（原因：" + speak +
                          "）——判定误报时可在 _diag_initiative_force.txt 写 ignore_state=1 强制放行（热生效）" +
                          "｜层快照：" + layers);
                return;
            }
            if (speak.Length > 0)
            {
                // 放行了也要留证：这条原因就是修闸的依据
                ModMain.P("[NpcInitiativeMonitor] forced trigger：能说闸命中（原因：" + speak +
                          "）但 ignore_state=1 放行｜层快照：" + layers);
            }
            else
            {
                ModMain.P("[NpcInitiativeMonitor] 层快照（能说闸通过" +
                          (busy.Length > 0 ? "；玩家忙：" + busy : "") + "）：" + layers);
            }

            var contactIds = new HashSet<string>();
            var candidates = BuildCandidates(contactIds, true);
            if (candidates.Count == 0)
            {
                ModMain.P("[NpcInitiativeMonitor] forced trigger 跳过：候选集为空（关系网/通讯录/同格都没人）");
                return;
            }
            var player = g.world.playerUnit;

            // 按好感降序（同好感不随机打散：强制模式要可复现）
            candidates.Sort((a, b) => SafeIntim(b).CompareTo(SafeIntim(a)));

            string wantName = DiagSwitches.InitiativeForceName;
            WorldUnitBase target = null;
            if (!string.IsNullOrEmpty(wantName))
            {
                foreach (var wub in candidates)
                {
                    if (NameOf(wub) == wantName) { target = wub; break; }
                }
                if (target == null)
                {
                    ModMain.P("[NpcInitiativeMonitor] forced trigger 跳过：候选集里没有 name=" + wantName +
                              "（非通讯录成员必须与玩家同格才会进候选）");
                    return;
                }
            }
            else
            {
                if (DiagSwitches.InitiativeForceRandom)
                {
                    // pick=random：在候选里随机取一人（看"不同 NPC 都会来找我"用；代价是每次新建一个
                    // session/agent，且同格者会弹确认窗）。先收有效姓名成列表，避免随机到空名。
                    var valid = new List<WorldUnitBase>();
                    foreach (var wub in candidates)
                    {
                        if (!string.IsNullOrEmpty(NameOf(wub))) valid.Add(wub);
                    }
                    if (valid.Count == 0)
                    {
                        ModMain.P("[NpcInitiativeMonitor] forced trigger 跳过：候选集里取不到有效姓名");
                        return;
                    }
                    target = valid[UnityEngine.Random.Range(0, valid.Count)];
                }
                else
                {
                    foreach (var wub in candidates)
                    {
                        if (!string.IsNullOrEmpty(NameOf(wub))) { target = wub; break; }
                    }
                    if (target == null)
                    {
                        ModMain.P("[NpcInitiativeMonitor] forced trigger 跳过：候选集里取不到有效姓名");
                        return;
                    }
                }
            }

            string name = NameOf(target);
            // 与日节拍同款门槛断言（候选集本已保证，这里只是"两条路径判定一致"的显式对齐）
            string unitId = null;
            try { unitId = target.data.unitData.unitID; } catch { }
            bool inContacts = unitId != null && contactIds.Contains(unitId);
            bool sameGrid = player != null && UnitSnapshot.IsSameGrid(target, player);
            if (!inContacts && !sameGrid)
            {
                ModMain.P("[NpcInitiativeMonitor] forced trigger 跳过：name=" + name +
                          " 既不在通讯录也不与玩家同格（自主交互不允许异地陌生人传音）");
                return;
            }

            string intentOverride = DiagSwitches.InitiativeForceIntent;
            if (!string.IsNullOrEmpty(intentOverride) && Array.IndexOf(AllIntents, intentOverride) < 0)
            {
                ModMain.P("[NpcInitiativeMonitor] forced trigger: 未知 intent=" + intentOverride + "（改用按亲密度派发）");
                intentOverride = null;
            }
            int intim = SafeIntim(target);
            string intent = string.IsNullOrEmpty(intentOverride) ? PickIntent(intim) : intentOverride;
            string reason = BuildReason(intim);

            FireOne(target, player, name, intim, sameGrid, intent, reason, curDay, true, busy);
        }

        // ---------- 游戏日历 ----------

        // internal：ModMain/UI 的进世界探测（AbPanelProber.OnFrame）复用本判据——
        // 能取到游戏日历 = 存档世界已加载、登录 UI 早已完成
        internal static int CurrentGameDay()
        {
            try
            {
                var run = g.world.run;
                if (run != null)
                {
                    int m = 0, d = 0;
                    try { m = (int)run.roundMonth; } catch { }
                    try { d = (int)run.roundDay; } catch { }
                    // roundMonth 为总月数，roundDay 为当月日（0起），30天≈1月近似
                    return m * 30 + d;
                }
            }
            catch { }
            return -1;
        }

        /// <summary>
        /// 当前**账面月**（1年1月=1、2年1月=13；与 `DataUnitLog.LogItemData.month` 同标度，
        /// 换算见 `UnitSnapshot.CnYearMonth`）。日历不可用（未进世界 / 世界未加载）返回 -1。
        ///
        /// 标度实证：`roundMonth` 是**0 起总月数** —— 两处参照 mod 独立写法都印证
        /// （`roundMonth/12+1` 年 + `roundMonth%12+1` 月；`ConvertToYearsMonths(roundMonth+1)`），
        /// 且 `roundDay` 同样是 0 起（各处以 `roundDay+1` 当日）。故账面月 = `roundMonth + 1`。
        /// IL2CPP 反编只读得到 `WorldRunMgr{ roundMonth, roundDay, roundDayResidue, roundDayMax }`
        /// ——**没有 roundYear**，年必须由总月数除出来，别去找不存在的字段。
        /// </summary>
        internal static int CurrentAccountMonth()
        {
            try
            {
                var run = g.world.run;
                if (run == null) return -1;
                int m = 0;
                try { m = (int)run.roundMonth; } catch { return -1; }
                return m + 1;                      // 0 起总月数 → 账面月
            }
            catch { return -1; }
        }

        /// <summary>
        /// 当月**日**（1 起，与参照 mod 的 `roundDay + 1` 一致）；日历取不到返回 -1。
        /// `roundDay` 是 0 起的**月内**日（`roundDayMax` 为当月天数，`CurrentGameDay()` 的
        /// `roundMonth*30 + roundDay` 近似即建立在此）。
        /// </summary>
        internal static int CurrentRoundDay()
        {
            try
            {
                var run = g.world.run;
                if (run == null) return -1;
                int d = 0;
                try { d = (int)run.roundDay; } catch { return -1; }
                return d + 1;                      // 0 起 → 1 起
            }
            catch { return -1; }
        }

        /// <summary>
        /// 当前游戏日期文本，形如 `1年1月3日`；日历取不到返回**空串**。
        /// **L1 的 `raw.now.text` 与消息时间戳共用本函数**（经 `UnitSnapshot.CnDate`），
        /// 保证模型在「当前时间」段与消息前缀里看到的是同一个串。
        /// </summary>
        internal static string CurrentDateText()
        {
            return UnitSnapshot.CnDate(CurrentAccountMonth(), CurrentRoundDay());
        }

        /// <summary>
        /// 玩家消息时间戳标签，形如 `[1年1月3日]`；日历取不到时返回**空串**。
        ///
        /// 调用方必须把空串当"不打时间戳"正常放行 —— 时间戳是锦上添花，绝不能因为读不到
        /// 日历就把玩家的话卡住不发（世界未加载/存档切换的瞬间就可能读不到）。
        ///
        /// 由 `ChatPresenter.OnSubmitRequested` 拼在玩家消息**最前面**再进 WS：NPC 此前完全
        /// 感知不到时间，账本带上它之后才能说出"上次见你是三月前"。
        /// </summary>
        internal static string CurrentTimeLabel()
        {
            string d = CurrentDateText();
            return d.Length > 0 ? "[" + d + "]" : "";
        }

        // ---------- 状态闸（两级：能不能说 / 能不能做动作） ----------

        /// <summary>诊断用：只要能做动作闸的原因串（空=不忙）。收尾分流日志（ContactDuty）用。</summary>
        internal static string BusyReasonText() => BusyReason();


        /// <summary>玩家是否处于"别弹窗打扰"的状态。收尾分流（ContactDuty 同格弹出前）用：
        /// 与"能说闸"或"能做动作闸"任一命中即不弹窗（保持 之前的语义）。</summary>
        internal static bool IsPlayerBusy() => SpeechBlockReason().Length > 0 || BusyReason().Length > 0;

        /// <summary>**能说闸**（拆级）：只拦"根本不该开口"的状态 ——
        /// 未进世界（没有 NPC 上下文可谈）与玩家死亡/复活选择中。非空 = 本次触发丢弃。
        /// 注意：**战斗中不在此列** —— 战斗中允许传音（只弹顶部横幅，非模态、不挡操作）。</summary>
        private static string SpeechBlockReason()
        {
            try
            {
                if (g.world.playerUnit == null) return "playerUnit=null（未进世界）";
            }
            catch { return "playerUnit 读取异常"; }
            // 玩家死亡/复活选择中：原 isDead 判据不存在（全装配扫描 hits=0），改判死亡窗是否显示
            try
            {
                var die = TopOfLayerOrNamed("UIMapDie");
                if (UiVisible(die)) return "UIMapDie 可见（玩家死亡/复活中）";
            }
            catch { }
            return "";
        }

        /// <summary>**能做动作闸**（忙 = 能说话但不能发起行动）：战斗中、有模态遮罩/游戏窗口、
        /// 我们自己的确认窗或挂起的 AI 行动、战斗 UI。非空 → 事件帧带 `busy:true`，
        /// Python 端把 5 个动作工具从本回合工具表剔除，只留 3 个只读（见 README 附录二 G.5）。</summary>
        private static string BusyReason()
        {
            try
            {
                // 战斗中（真判据：WorldBattleMgr.isBattle；判空是恒真的错判据）
                try
                {
                    var b = g.world.battle;
                    if (b != null && b.isBattle) return "world.battle.isBattle（战斗中）";
                }
                catch { }
                // 确认窗（模态）弹着：我们自己的挂起 AI 行动
                try
                {
                    if (DramaGate.HasPending) return "DramaGate.HasPending";
                }
                catch { }
                // 兜底：确认窗 UI（UICustomDramaDyn）真的显示着
                try
                {
                    var ui = g.ui;
                    if (ui != null)
                    {
                        var dyn = ui.GetUI(new UIType.UITypeBase("UICustomDramaDyn", (UILayer)0));
                        if (UiVisible(dyn)) return "UICustomDramaDyn 可见";
                    }
                }
                catch { }
                // 战斗 UI 真的显示着（结算期 isBattle 可能已落，故保留）
                try
                {
                    var ui2 = g.ui;
                    if (ui2 != null)
                    {
                        var bi = ui2.GetUI<UIBattleInfo>(UIType.BattleInfo);
                        if (UiVisible(bi)) return "UIBattleInfo 可见";
                    }
                }
                catch { }

                // ---- UI(0)层里的"两个必须算忙的界面"（第二轮修正）----
                // 用户的规则原话："我的对话 UI 打开的时候必须要判忙，否则我在和别的 NPC 对话呢，
                // 你给我直接弹出窗口或者动作打断了，这很不好；其余的不算忙（看 NPCInfo、地图之类），
                // 但是 DramaDialogue 需要算忙。"
                // 所以：**不按层判 UI(0)**（那会把所有游戏面板一网打尽），只按**具体界面**判这两个：
                //   ① 我方对话窗 UIChatAi —— 正在与某个 NPC 对话。判忙的后果恰好是用户要的：
                //      同格**不弹确认窗**、动作工具被禁、回来的传音走未读红点 + 顶部横幅（不抢焦点）；
                //   ② 剧情窗（名字含 Drama 的家族：DramaDialogue / UIDrama* / 我方 UICustomDramaDyn）
                //      —— 玩家正在看剧情，别插话弹窗。
                // 其余 UI(0) 面板（NPCInfo/PlayerInfo/PlayerTask/FateFeature/GetReward/MinMap…）
                // **不算忙**：看资料时希望 NPC 照常来互动（照常弹确认窗、照常调行动）。
                try
                {
                    var ui3 = g.ui;
                    if (ui3 != null)
                    {
                        // 用 GetUI 而不是"层顶名"——对话窗可能被别的面板盖住，但"正在对话"这个事实不变
                        var chat = ui3.GetUI(new UIType.UITypeBase("UIChatAi", (UILayer)0));
                        if (UiVisible(chat)) return "对话窗开着（UIChatAi）：正在与 NPC 对话，不弹窗不行动";
                    }
                }
                catch { }
                try
                {
                    var top = TopOfLayer(UILayer.UI);
                    if (UiVisible(top))
                    {
                        string topName = UiName(top);
                        if (!string.IsNullOrEmpty(topName) &&
                            topName.IndexOf("Drama", StringComparison.OrdinalIgnoreCase) >= 0)
                            return "剧情窗开着（" + topName + "）";
                    }
                }
                catch { }

                // 游戏自己的 UI 分层判据（用户要求"别在战斗/别的时候打断"）：
                // UILayer.Mask = 全屏输入遮罩层（模态确认/剧情/结算在挡输入），
                // UILayer.Window = 窗口层（游戏自带界面）。
                // 两者任一有**可见**的层顶 UI = 玩家正被界面占着 → 本回合只许言语。
                // 只判 Mask/Window：**绝不判 UITop**（HUD/地图常驻 UITop，判了就永远拦）；
                // UI(0) 层只判上面点名的那两个界面（其余面板要放行——用户第二轮明确）。
                try
                {
                    var m = TopOfLayer(UILayer.Mask);
                    if (UiVisible(m)) return "Mask 层有模态遮罩：" + UiName(m);
                }
                catch { }
                try
                {
                    var w = TopOfLayer(UILayer.Window);
                    if (UiVisible(w)) return "Window 层有窗口：" + UiName(w);
                }
                catch { }
            }
            catch { }
            return "";
        }

        /// <summary>按名字取 UI（诊断/闸门共用；fail-open：key 不匹配只会返回 null）。</summary>
        private static UIBase TopOfLayerOrNamed(string uiName)
        {
            var ui = g.ui;
            return ui == null ? null : ui.GetUI(new UIType.UITypeBase(uiName, (UILayer)0));
        }

        /// <summary>取某层的层顶 UI（诊断/闸门共用；ui 未就绪返回 null）。</summary>
        private static UIBase TopOfLayer(UILayer layer)
        {
            var ui = g.ui;
            return ui == null ? null : ui.GetLayerTopUI(layer, 0);
        }

        /// <summary>UI 名（诊断用；失败返回空串，不抛）。</summary>
        private static string UiName(UIBase uiObj)
        {
            try { return uiObj == null ? "" : (uiObj.name ?? ""); }
            catch { return ""; }
        }

        /// <summary>逐层快照：8 层各层顶 UI 名 + 可见性。强制触发时每次打一行，
        /// 用来确认"什么状态下哪一层有东西"，据此定/校状态闸的层判据（加）。
        /// 层序与 UILayer 枚举一致：0 UI / 1 UITop / 2 Guide / 3 Loading / 4 Window / 5 Mask / 6 FullEffect / 7 TempUI。</summary>
        internal static string LayerSnapshot()
        {
            var names = new[] { "UI", "UITop", "Guide", "Loading", "Window", "Mask", "FullEffect", "TempUI" };
            var sb = new System.Text.StringBuilder();
            for (int i = 0; i < names.Length; i++)
            {
                string seg;
                try
                {
                    var ui = TopOfLayer((UILayer)i);
                    seg = ui == null ? "(空)" : (UiName(ui) + (UiVisible(ui) ? "(on)" : "(off)"));
                }
                catch { seg = "(err)"; }
                if (sb.Length > 0) sb.Append(' ');
                sb.Append(names[i]).Append('=').Append(seg);
            }
            return sb.ToString();
        }

        /// <summary>UI 实例是否**真的显示着**（存在 ≠ 可见）。`UIMgr.GetUI` 返回的是
        /// 已创建的实例——本项目的 AB 面板正是用 `GetUI(...)!=null` 判"是否已创建"，
        /// 可见性得另判。判法与 AbHotkeys 的层顶诊断同源：activeInHierarchy + Canvas.enabled。
        /// 拿不到可见性时保守返回 true（维持原判据语义，宁可拦不可漏判）。</summary>
        private static bool UiVisible(UnityEngine.MonoBehaviour uiObj)
        {
            if (uiObj == null) return false;
            try { if (!uiObj.gameObject.activeInHierarchy) return false; }
            catch { return true; }
            try { if (!uiObj.gameObject.activeSelf) return false; }
            catch { }
            try
            {
                var cv = uiObj.GetComponent<Canvas>();
                if (cv != null && !cv.enabled) return false;
            }
            catch { }
            return true;
        }

        // ---------- 候选（RelationNetwork 关系记录全集 + 手动好友 + 同格陌生人） ----------

        /// <summary>
        /// 候选 = ①关系十容器+好友 ②仇人簿 ③关系记录簿(GetAllGoodRelationUnitID 含敌)
        ///       ④手动通讯录好友 ⑤同格所有人（陌生人当面可搭话）。
        /// contactIds 回填"通讯录成员"来源的 unitID（①~④）；⑤同格陌生人不记。
        /// 门槛：非通讯录成员必须与玩家同格才可发起（当面），异地只有通讯录成员能传音。
        ///
        /// dump=true（诊断强制模式）额外打印候选快照，用于肉眼确认玩家自身是否混入。
        /// </summary>
        private List<WorldUnitBase> BuildCandidates(HashSet<string> contactIds, bool dump)
        {
            var list = new List<WorldUnitBase>();
            var seen = new HashSet<string>();
            try { RelationNetwork.CollectKnownUnits(list, seen, contactIds); } catch { }

            // ④ 手动通讯录好友（镜像来自 ContactPresenter，真相在 Python contacts.json）
            try
            {
                var manual = ContactStore.ManualContactNames;   // 静态镜像：与通讯录面板实例解耦（09-10 定案）
                if (manual != null)
                {
                    foreach (var nm in manual)
                    {
                        if (string.IsNullOrEmpty(nm)) continue;
                        var u = UnitLookup.Resolve(nm);
                        if (u == null) continue;
                        // 玩家真名被当成"好友"加进来时（自加/误加），这里同样要挡掉
                        if (UnitSnapshot.IsPlayerUnit(u))
                        {
                            ModMain.P("[NpcInitiativeMonitor] 通讯录条目命中玩家自身，跳过：" + nm);
                            continue;
                        }
                        string id = null;
                        try { id = u.data.unitData.unitID; } catch { }
                        if (string.IsNullOrEmpty(id) || !seen.Add(id)) continue;
                        string name = null;
                        try { name = u.data.unitData.propertyData.GetName(); } catch { }
                        if (string.IsNullOrEmpty(name)) continue;
                        list.Add(u);
                        contactIds.Add(id);
                    }
                }
            }
            catch (Exception e) { ModMain.P("[NpcInitiativeMonitor] manual contacts: " + e.Message); }

            // ⑤ 同格所有人（含陌生人）
            try { RelationNetwork.CollectSameGridUnits(list, seen); } catch { }

            // 实锤修复 末端统一剔除玩家自身 —— 与来源无关，今后新增候选来源也自动安全。
            // 玩家在游戏里就是一个 WorldUnitBase，GetUnits(true) 会把他一并返回；
            // 而他与"自己"必然同格、好感恒 0，非通讯录也能过"同格才可发起"的门槛，
            // 于是变成"自己给自己传音"（当日日志：forced trigger name=缪嘉歆 = 玩家真名）。
            string pid = UnitSnapshot.PlayerUnitId();
            if (dump) ModMain.P("[NpcInitiativeMonitor] 候选快照（玩家=" + (pid.Length > 0 ? pid : "?") + "）：" + DumpCandidates(list));
            for (int i = list.Count - 1; i >= 0; i--)
            {
                if (!UnitSnapshot.IsPlayerUnit(list[i])) continue;
                ModMain.P("[NpcInitiativeMonitor] 候选集剔除玩家自身：" + NameOf(list[i]) +
                          "(" + (pid.Length > 0 ? pid : "?") + ")");
                list.RemoveAt(i);
            }
            return list;
        }

        /// <summary>候选快照（诊断强制模式打印；上限 12 人，超出只报总数）。主线程。</summary>
        private static string DumpCandidates(List<WorldUnitBase> list)
        {
            var sb = new System.Text.StringBuilder();
            int cap = Math.Min(list.Count, 12);
            for (int i = 0; i < cap; i++)
            {
                if (i > 0) sb.Append(" ｜ ");
                var u = list[i];
                sb.Append(NameOf(u)).Append("(好感").Append(SafeIntim(u));
                try { if (UnitSnapshot.IsSameGrid(u, g.world.playerUnit)) sb.Append(",同格"); } catch { }
                if (UnitSnapshot.IsPlayerUnit(u)) sb.Append(",★玩家自身");
                sb.Append(")");
            }
            if (list.Count == 0) sb.Append("（空）");
            else if (list.Count > cap) sb.Append(" …等共").Append(list.Count).Append("人");
            return sb.ToString();
        }

        private bool IsCoolingDown(string name, int curDay)
        {
            // 现实冷却
            if (_cooldownReal.TryGetValue(name, out var lastReal))
            {
                if ((DateTime.Now - lastReal).TotalSeconds < _npcCooldownRealSec)
                    return true;
            }
            // 游戏日冷却
            if (_cooldownDay.TryGetValue(name, out var lastDay))
            {
                if (curDay - lastDay < _npcCooldownDays)
                    return true;
            }
            return false;
        }

        /// <summary>取姓名（IL2CPP 取名字段链可能抛，统一吞掉返回空串）。</summary>
        private static string NameOf(WorldUnitBase wub)
        {
            try { return wub == null ? "" : (wub.data.unitData.propertyData.GetName() ?? ""); }
            catch { return ""; }
        }

        private static int SafeIntim(WorldUnitBase wub)
        {
            try { return wub.data.unitData.relationData.GetIntim(g.world.playerUnit); }
            catch { return 0; }
        }

        private static string PickIntent(int intim)
        {
            var rnd = UnityEngine.Random.value;
            if (intim < 0) return RandomOf(NegativeIntents);
            if (intim >= 120 && rnd < 0.4f) return RandomOf(PositiveCloseIntents);
            return RandomOf(PositiveIntents);
        }

        private static string BuildReason(int intim)
        {
            if (intim >= 120) return "念及与你多日情谊，特来以神识传音寻你";
            if (intim >= 60) return "想起你，想与你聊上几句";
            if (intim < 0) return "心中不忿，寻你理论";
            return "";
        }

        private static string RandomOf(string[] arr)
        {
            return arr.Length == 0 ? "" : arr[(int)(UnityEngine.Random.value * arr.Length) % arr.Length];
        }
    }
}
