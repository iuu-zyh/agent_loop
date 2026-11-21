/// <summary>
/// DramaSegmentProbe —— 「剧情相遇分段器」的**定标探针：只记日志，不做任何判定、不改任何行为**。
///
/// 为什么要有它：分段器（把一次当面相遇的多页原文攒成一段、供「对话」按钮整段注入）的判据里，
/// 有四个量只能靠现场采样定，不许猜：
///   ① **读条期哪个探针先亮** —— 点「应战」→ 剧情窗关 → 进战斗之间有一段加载；若那段时间
///      三个转场探针（逻辑战斗位 / 战斗界面 / 加载层）都还是假，则"关窗 + 紧接着有转场"这条
///      交接判据会在读条期失效，段会被误切。采样看谁先亮、隔多久亮。
///   ② **翻页时窗是否连续** —— 同一扇窗翻页（如战后台词两页）若中途有一两帧"窗不可见"，
///      会被判成一次关窗 ⇒ 误收口。点位是 W 的每一次真假跃迁。
///   ③ **关窗与转场跃迁的间隔分布** —— 交接判据里的相邻窗口（ε，秒级）取多大，靠这张分布定。
///   ④ **页事件 / 选项点击的时序** —— 谁先谁后、隔几毫秒，用来核对"点击≠关窗"等前提。
///
/// 开关：`ModPaths.DiagDir` 下的 `_diag_segment_probe.txt`（同 `DiagSwitches` 家族的文件哨兵，
/// **运行中增删即生效**，每 120 帧复查一次）。
/// ⚠ 实测（09-22 19:28）：发行布局下 `DiagDir` = `DataRoot` = **`&lt;Mod根&gt;\ModAssets\`**
///   （判据是 ModAssets 下存在 config.json/prompts），**不是 Mod 根** —— `DiagSwitches.cs` 头部
///   那条"与 Mod 根同层"的注释是过期的，照它放哨兵会**恒不生效**（本次就这样白跑了一局）。
/// 文件不存在时本类唯一开销 = 每次页事件/点击一次 `File.Exists`（约每 2 秒一次），
/// 日志一条不打 —— 所以它保留在发行版里也不吵人。
///
/// 记什么（每条都自带全量快照，单行可读，不必回头对照上下文）：
///   · 页事件（每页一条）：页 id、对方名/精确编号、本页成品句字数、说话人侧别
///   · 选项点击：配表 id + function + text（后两者**可读性未验**，读不到就明写"读不到"）
///   · 探针跃迁：W（剧情窗可见）/ B1（逻辑战斗位）/ B2（战斗界面可见）/ L（加载层）/
///     P（进战斗预告窗）/ D（我方确认窗）/ **A（动作 UI 进行中，09-23 补）** 任一变真变假即一行。
///     A 为什么必须采：核拿它的**电平**当"页间隙还没走完"的证据（论道型多窗交互的关键），
///     而页开着时它被剧情窗盖住 —— 只有关窗那一刻的值有意义，快照里没有它就只能靠核的日志反推。
///   · 心跳：每 600 帧一行（证明帧回调还活着，也覆盖"长时间无跃迁"的战斗段）
///
/// **09-22 追加两项**（都还是采样件，攒够样本就该删或升成判据）：
///   ⑤ **处置页顺序**（`NoteVerdictPageProbe`）：attack 的 B 支（玩家战胜）要求台账活到处置页
///      20403/20404 才收口；而唯一能提前掐死它的规则（硬信号 `BattleEndMgr.BattleEnd` + 已抓台词）
///      **不问时机**。若实测硬信号更早，处置页会被当普通页处理、`verdict_page` 永不置位 ——
///      此时**除了这条日志，别的日志长得跟"玩家自己打的架"一模一样**。
///   ⑥ **进战斗预告窗的真身**（`LayerTopLine`，在关窗/进战斗后的 12s 观察窗里每秒采一次）：
///      09-22 已定案 —— 真名是 **`MapBattlePre`**（UI 层层顶，**没有 `UI` 前缀**），
///      而 `BattleOutcome` 旧写法按类名 `UIMapBattlePre` + `GetUI(...)` 猜，恒 false 且**不报错**
///      （D 支与②b 静默退化）。现在两边都问共用判据 `NpcInitiativeMonitor.BattlePreShowing()`，
///      本行只负责把"层顶是谁 + 两个判据的取值"摊在日志里。
///
/// 时间戳必须自己打：`Player.log` **没有时间戳**（已踩过），而墙钟是跨进程对账（Python 侧日志）
/// 的唯一钥匙。每行同时给"相对启用时刻的秒数"与"墙钟 HH:mm:ss.fff"。
///
/// 与既有实现的关系：W 直接复用 `NpcInitiativeMonitor.DramaWindowName()`（**唯一实现**，
/// 与忙判据/面板闸门共用，不许另写一份）；其余五个是只读直查（读法与共用实现同源，
/// 本类临时存在，故不反过来侵入那些文件）。**任何读取都吞异常** —— 探针绝不许影响游戏链。
/// </summary>
using System;
using System.IO;
using HarmonyLib;
using UnityEngine;

namespace AgentLoopBridge
{
    internal static class DramaSegmentProbe
    {
        private const string SentinelName = "_diag_segment_probe.txt";
        /// <summary>哨兵复查间隔（帧）。★09-28 从 120（~2s）放宽到 600（~10s）★：实机采样显示
        /// `segprobe` 每帧 avg 0.29~0.78ms，其摊薄主要来自这里的 `File.Exists`（Mod 目录在游戏盘上，
        /// 杀软挂钩下单次可达毫秒级），而它只为"运行中增删哨兵"这一诊断操作服务 —— 10s 粒度完全够
        /// （`DiagSwitches` 那一组复查仍是 120 帧/2s，不受影响）。</summary>
        private const int SentinelRecheckFrames = 600;
        /// <summary>心跳间隔（帧）。~10s 一行，用来证明帧回调活着且给出"无跃迁期"的状态。</summary>
        private const int HeartbeatFrames = 600;
        /// <summary>`UILayer` 枚举里 3 = 加载层（层序：0 UI / 1 UITop / 2 Guide / 3 Loading / …）。
        /// 用序号而不用枚举名：枚举成员名未经实证，序号有 `NpcInitiativeMonitor.LayerSnapshot` 的注释为据。
        /// ★09-22 实测：两场 attack 里它**全程未亮**（关窗/进战斗后的 12s 观察窗里也没亮过一次）
        /// ⇒ 分段判据的"交接"不该用它（现有设计也没用），这个位只留作对照。</summary>
        private const int LoadingLayerIndex = 3;
        // 【09-22 删】原来这里有一份 `BattlePreUiName = "UIMapBattlePre"` 的只读镜像 —— 实测那**不是**
        // 真名（真名是 `MapBattlePre`，UI 层层顶，无 `UI` 前缀），按类名 `GetUI(...)` 查恒 miss。
        // 现在预告窗一律问共用判据 `NpcInitiativeMonitor.BattlePreShowing()`，本文件不再留字面量。
        /// <summary>我方自制确认窗真名（`ShowDramaService` 造的那扇，与游戏自己的剧情窗区分用）。</summary>
        private const string OwnDramaUiName = "UICustomDramaDyn";

        // (帧回调的句柄/存活判断由 `FrameTicker` 持有 —— 11-19 去掉了全进程一次的 `_tickRegistered`)
        private static bool _on;
        private static int _frame;
        private static int _lastCheckFrame = -99999;
        private static float _t0;
        private static bool _baseline;      // 首帧只记基线，不做跃迁判定

        // 上一帧的七个探针（A 是 09-23 补的：核拿它的**电平**当"页间隙"证据，
        // 而快照里没有它时，排障只能靠 `[SegRec] 关窗…动作 UI 还亮着` 那一行反推）
        private static bool _pW, _pB1, _pB2, _pL, _pP, _pD, _pA;

        // 转场观察窗（见 ⑥）：关窗/进战斗之后的一小段里，每秒采一次"八层 + 预告窗真身"
        private static float _burstUntil;
        private static float _burstNextAt;
        private static string _burstLast = "";

        // 最近一页（点击日志里带上，好把点击对到页上）
        private static int _lastPageId;
        private static string _lastNpc = "";

        // ------------------------------------------------------------------ 入口（补丁调）

        /// <summary>页事件。兼任"注册帧回调"的入口：本类没有自己的初始化点，
        /// 而页事件在任何一次相遇里必然先到，从那一刻起 Tick 常驻（此后即使页停了也一直跑）。</summary>
        internal static void OnPage(UIDramaBase ui, int id, DramaData data)
        {
            EnsureTick();
            if (!On()) return;
            string npc; string uid;
            ResolveNpc(data, out npc, out uid);
            _lastPageId = id;
            _lastNpc = npc;
            string text = "";
            try { text = DramaTextCapture.LastUiText ?? ""; } catch { }
            int side = 0;
            try { side = DramaTextCapture.LastSpeakerSide; } catch { }
            Log("页 id=" + id + " 对方=" + (string.IsNullOrEmpty(npc) ? "(无)" : npc)
                + (string.IsNullOrEmpty(uid) ? "" : "(" + uid + ")")
                + " 侧别=" + side + " 成品句=" + text.Length + "字" + " 窗=" + WindowNameSafe());
            // ⑤ 处置页探针：判据白名单从 BattleOutcome 取（不在这里再抄一份页号）
            if (BattleOutcome.IsVerdictDramaId(id)) NoteVerdictPageProbe(id);
        }

        /// <summary>选项点击（prefix：**生效之前**，与需要"点击那一刻"状态的判据同拍）。</summary>
        internal static void OnOptionClick(ConfDramaOptionsItem opt)
        {
            EnsureTick();
            if (!On()) return;
            Log("点了选项 " + Describe(opt) + "（上一页=" + _lastPageId + " " + _lastNpc + "）");
        }

        // ------------------------------------------------------------------ 帧回调

        private static void Tick()
        {
            try
            {
                _frame++;
                if (!On()) return;

                string wName = WindowNameSafe();
                bool W = wName.Length > 0;
                bool B1 = false;
                try
                {
                    var b = g.world != null ? g.world.battle : null;
                    B1 = b != null && b.isBattle;
                }
                catch { }
                bool B2 = false;
                try
                {
                    var ui = g.ui;
                    if (ui != null) B2 = Vis(ui.GetUI<UIBattleInfo>(UIType.BattleInfo));
                }
                catch { }
                bool L = false;
                string lName = "";
                try
                {
                    var ui = g.ui;
                    if (ui != null)
                    {
                        var top = ui.GetLayerTopUI((UILayer)LoadingLayerIndex, 0);
                        if (top != null) { L = Vis(top); lName = top.name ?? ""; }
                    }
                }
                catch { }
                // 预告窗：**问共用判据**（真名 `MapBattlePre` 实测见 `NpcInitiativeMonitor.BattlePreShowing`），
                // 不再自己按类名 `GetUI(...)` 猜 —— 探针量的必须与判据量的是同一个量。
                bool P = false;
                try { P = NpcInitiativeMonitor.BattlePreShowing(); } catch { }
                bool D = false;
                try
                {
                    var ui = g.ui;
                    if (ui != null) D = Vis(ui.GetUI(new UIType.UITypeBase(OwnDramaUiName, (UILayer)0)));
                }
                catch { }
                // A（09-23 补）：游戏动作进行中（层顶 `ActionCourseInfo`）—— **问共用判据**，
                // 量的是核在关窗那一刻读的同一个量（页开着时它被剧情窗盖住，采不到边沿，只有电平）。
                bool A = false;
                try { A = NpcInitiativeMonitor.ActionShowing(); } catch { }

                if (!_baseline)
                {
                    _baseline = true;
                    _pW = W; _pB1 = B1; _pB2 = B2; _pL = L; _pP = P; _pD = D; _pA = A;
                    Log("基线快照 " + Snap(W, B1, B2, L, P, D, A, wName, lName) + "（此后只记跃迁）");
                    return;
                }

                bool changed = false;
                if (W != _pW)
                {
                    Log("W " + B2S(_pW) + "→" + B2S(W) + " 剧情窗=" + (W ? wName : "无"));
                    // W 由真翻假 = 一次相遇的关窗时刻，也是"关窗 →（预告窗）→ 读条 → 进战斗"那段转场的起点：
                    // 开 12s 观察窗（⑥），把这段里开过的窗一个不漏地采下来。
                    if (_pW && !W) StartBurst("关窗");
                    changed = true;
                }
                if (B1 != _pB1)
                {
                    Log("B1(逻辑战斗位) " + B2S(_pB1) + "→" + B2S(B1));
                    // 进战斗那一刻预告窗必然已经收场（实证 `IntoBattle ← UIMapBattlePre:<UpdateUI>b__10_3`），
                    // 此时采一次能同时看清"它到底在第几层、叫什么"，以及那几帧还有没有别的窗。
                    if (!_pB1 && B1) StartBurst("进战斗");
                    changed = true;
                }
                if (B2 != _pB2) { Log("B2(战斗界面) " + B2S(_pB2) + "→" + B2S(B2)); changed = true; }
                if (L != _pL) { Log("L(加载层) " + B2S(_pL) + "→" + B2S(L) + " 层顶=" + (lName.Length == 0 ? "空" : lName)); changed = true; }
                if (P != _pP)
                {
                    Log("P(预告窗) " + B2S(_pP) + "→" + B2S(P));
                    // 顺手把"层顶是谁"打出来：预告窗是层顶窗，这条既是它的真身、也是判据的取样现场
                    Log("  ↳ " + LayerTopLine());
                    changed = true;
                }
                if (D != _pD) { Log("D(我方确认窗) " + B2S(_pD) + "→" + B2S(D)); changed = true; }
                if (A != _pA) { Log("A(动作UI) " + B2S(_pA) + "→" + B2S(A)); changed = true; }
                if (changed) Log("  ↳ 快照 " + Snap(W, B1, B2, L, P, D, A, wName, lName));
                else if (_frame % HeartbeatFrames == 0) Log("心跳 " + Snap(W, B1, B2, L, P, D, A, wName, lName));
                BurstTick();

                _pW = W; _pB1 = B1; _pB2 = B2; _pL = L; _pP = P; _pD = D; _pA = A;
            }
            catch { }
        }

        // ------------------------------------------------------------------ 内部

        /// <summary>哨兵状态（每 120 帧复查一次，运行中增删即生效）。</summary>
        private static bool On()
        {
            try
            {
                int fc = 0;
                try { fc = Time.frameCount; } catch { fc = _frame; }
                if (fc - _lastCheckFrame < SentinelRecheckFrames) return _on;
                _lastCheckFrame = fc;
                bool was = _on;
                try { _on = File.Exists(SentinelPath()); } catch { _on = false; }
                if (_on && !was)
                {
                    _t0 = Time.unscaledTime;
                    _baseline = false;
                    Log("探针启用（哨兵=" + SentinelPath() + "；此前的状态不回溯）");
                }
                else if (!_on && was) Log("探针停用（哨兵文件已删）");
            }
            catch { }
            return _on;
        }

        /// <summary>确保帧回调在跑（11-19：句柄交给 `FrameTicker`，不再用全进程一次的布尔）。</summary>
        private static void EnsureTick()
        {
            FrameTicker.Ensure(FrameTicker.KSegProbe, "segprobe", () =>
            {
                // 耗时采样（TickProfiler，09-28 卡顿定位）：每 120 帧一次哨兵 File.Exists 也在内
                long __t = TickProfiler.Begin();
                try { Tick(); }
                finally { TickProfiler.End("segprobe", __t); }
            });
        }

        // ------------------------------------------------------------------ ⑤ 处置页探针

        /// <summary>处置页（20403/20404）出现时**无论台账在不在**都出声 —— 这是它存在的意义。
        /// 要回答的是"台账活到处置页了吗？没活到的话死于哪条判据"（见类头 ⑤）。</summary>
        private static void NoteVerdictPageProbe(int id)
        {
            try
            {
                bool alive = BattleOutcome.IsVerdictPage(id);
                string last = BattleOutcome.LastResolveText();
                Log("★处置页探针 dramaId=" + id
                    + " 台账=" + (alive ? "在飞（op=attack；稍后应见『★处置页已收口』）" : "不在")
                    + "｜上次收口=" + (last.Length == 0 ? "(本局还没有过收口)" : last));
            }
            catch { }
        }

        // ------------------------------------------------------------------ ⑥ 预告窗真身 / 转场观察窗

        /// <summary>观察窗长度（秒）：覆盖"读条慢一点"的情况（`IntoBattle` 是同步的，但读条不是）。</summary>
        private const float BurstSeconds = 12f;
        /// <summary>观察窗内采样间隔（秒）：1s 够看清"哪扇窗什么时候开着"，又不刷屏。</summary>
        private const float BurstStepSeconds = 1f;

        /// <summary>开观察窗（重复触发只延长；内容比对串只在"新开一个窗"时清）。</summary>
        private static void StartBurst(string why)
        {
            try
            {
                float now = Time.unscaledTime;
                bool fresh = now >= _burstUntil;
                _burstUntil = now + BurstSeconds;
                _burstNextAt = now;
                if (fresh) _burstLast = "";
                Log("↓ 转场观察窗开始（" + why + "；每 " + (int)BurstStepSeconds + "s 采一次，共 "
                    + (int)BurstSeconds + "s，内容不变不出声）");
            }
            catch { }
        }

        /// <summary>观察窗内采样，**只在内容变化时出声** —— 每个窗最多十来行。</summary>
        private static void BurstTick()
        {
            try
            {
                float now = Time.unscaledTime;
                if (now >= _burstUntil) return;
                if (now < _burstNextAt) return;
                _burstNextAt = now + BurstStepSeconds;
                string line = LayerTopLine();
                if (line == _burstLast) return;
                _burstLast = line;
                Log("  [剩 " + (int)(_burstUntil - now) + "s] " + line);
            }
            catch { }
        }

        /// <summary>
        /// 层顶一行：**UI 层层顶是谁 + 两个判据的结果**。
        ///
        /// ★09-22 改版的理由（别再退回"八层快照"那版）★
        /// 旧版打的是 `NpcInitiativeMonitor.LayerSnapshot()`，实测输出是
        /// `UI=MapBattlePre(on) UITop=(err) Guide=(err) …` —— **1~7 层全是 `(err)`**，
        /// 因为 `GetLayerTopUI` 对 1..7 直接抛异常。也就是说那一版**实际只读了层 0**，
        /// 却在日志里长得像"看了八层"（本仓最忌讳的那种"看着像实测、其实是假象"）。
        /// 而我们的窗**全在层 0 顶**（`DramaDialogue`/`BattleInfo`/`BattleEnd`/`GetReward`/`BattleExit`/
        /// `MapBattlePre` 各自都当过层顶），所以现在直接给**真正量到的那一层**，
        /// 并把预告窗/剧情窗两个判据结果并列 —— 探针量的必须与判据量的是同一个量。
        /// </summary>
        private static string LayerTopLine()
        {
            string top = "";
            try { top = NpcInitiativeMonitor.LayerTopNameOfUi(); } catch { }
            bool pre = false;
            try { pre = NpcInitiativeMonitor.BattlePreShowing(); } catch { }
            string drama = "";
            try { drama = NpcInitiativeMonitor.DramaWindowName(); } catch { }
            return "UI层顶=" + (top.Length == 0 ? "(空)" : top)
                 + " 预告窗=" + (pre ? "可见" : "无")
                 + " 剧情窗=" + (drama.Length == 0 ? "无" : drama);
        }

        private static string SentinelPath()
        {
            try { return Path.Combine(ModPaths.DiagDir, SentinelName); }
            catch { return SentinelName; }
        }

        /// <summary>剧情窗人话原因（复用**唯一实现**；异常自吞）。</summary>
        private static string WindowNameSafe()
        {
            try { return NpcInitiativeMonitor.DramaWindowName() ?? ""; }
            catch { return ""; }
        }

        /// <summary>可见性判据，与 `NpcInitiativeMonitor.UiVisible` 同源（`activeInHierarchy` +
        /// `activeSelf` + `Canvas.enabled`），拿不到可见性时**保守返回 true**（与共用实现同口径：
        /// 宁可多算"开着"，也不把"开着"读成"关着"——后者会让探针给出反的结论）。</summary>
        private static bool Vis(UnityEngine.Component o)
        {
            if (o == null) return false;
            try { if (!o.gameObject.activeInHierarchy) return false; }
            catch { return true; }
            try { if (!o.gameObject.activeSelf) return false; }
            catch { }
            try
            {
                var cv = o.GetComponent<Canvas>();
                if (cv != null && !cv.enabled) return false;
            }
            catch { }
            return true;
        }

        /// <summary>页里那个"非玩家的 NPC"（镜像 `DramaAiOption.FindNpcOf` 的判据：三个人位里
        /// 第一个非玩家、且名字非空者）。取不到就返回空 —— 探针**不许猜**。</summary>
        private static void ResolveNpc(DramaData data, out string name, out string uid)
        {
            name = ""; uid = "";
            if (data == null) return;
            try
            {
                var arr = new[] { data.unitLeft, data.unitRight, data.unit };
                for (int i = 0; i < arr.Length; i++)
                {
                    var u = arr[i];
                    if (u == null) continue;
                    try { if (UnitSnapshot.IsPlayerUnit(u)) continue; } catch { }
                    string id = "";
                    try { id = u.data.unitData.unitID ?? ""; } catch { }
                    if (string.IsNullOrEmpty(id)) continue;
                    uid = id;
                    try { name = u.data.unitData.propertyData.GetName() ?? ""; } catch { }
                    return;
                }
            }
            catch { }
        }

        private static string Describe(ConfDramaOptionsItem opt)
        {
            if (opt == null) return "选项=null";
            int id = 0;
            try { id = (int)opt.id; }
            catch { try { id = (int)((dynamic)opt).id; } catch { } }
            string fn;
            try { fn = opt.function ?? ""; }
            catch { try { fn = (string)((dynamic)opt).function ?? ""; } catch { fn = "(读不到)"; } }
            string t = "";
            try { t = opt.text ?? ""; } catch { }
            if (t.Length > 24) t = t.Substring(0, 24) + "…";
            return "id=" + id + " function=" + fn + " text=" + t;
        }

        private static string Snap(bool W, bool B1, bool B2, bool L, bool P, bool D, bool A,
                                  string wName, string lName)
        {
            return "W=" + B2S(W) + " B1=" + B2S(B1) + " B2=" + B2S(B2) + " L=" + B2S(L)
                 + " P=" + B2S(P) + " D=" + B2S(D) + " A=" + B2S(A)
                 + "｜窗=" + (wName.Length == 0 ? "无" : wName)
                 + " 层顶=" + (lName.Length == 0 ? "空" : lName);
        }

        private static string B2S(bool b) { return b ? "1" : "0"; }

        private static void Log(string s)
        {
            try
            {
                float dt = 0f;
                try { dt = Time.unscaledTime - _t0; } catch { }
                ModMain.P("[SegProbe] t=+" + dt.ToString("F3") + "s"
                          + "(" + DateTime.Now.ToString("HH:mm:ss.fff") + ") " + s);
            }
            catch { }
        }
    }

    /// <summary>页事件挂点（`InitData` postfix）。**只读**：与 `DramaAiOption`/`DramaTextCapture`
    /// 的同名挂点并存（Harmony 支持多补丁），不做任何判定与改动。</summary>
    [HarmonyPatch(typeof(UIDramaBase), nameof(UIDramaBase.InitData))]
    internal static class DramaSegmentProbePageHook
    {
        private static void Postfix(UIDramaBase __instance, int id, DramaData data)
        {
            try { DramaSegmentProbe.OnPage(__instance, id, data); } catch { }
        }
    }

    /// <summary>选项点击挂点（`ClickOption` **prefix**：与"点击那一刻"读状态的判据同拍）。
    /// 本类自己**只读**；10-26 起这里顺手把点击转给 `NpcDuel` 一次（NPC 对 NPC 观战的战后处置页
    /// 要靠它收玩家的选择 —— 它只在这**一次** hold 里、只认 20403 那五枚选项 id，内部全程
    /// try/catch，与上面那条探针互不影响）。</summary>
    [HarmonyPatch(typeof(UIDramaBase), "ClickOption")]
    internal static class DramaSegmentProbeClickHook
    {
        private static void Prefix(ConfDramaOptionsItem __0)
        {
            try { DramaSegmentProbe.OnOptionClick(__0); } catch { }
            // ★10-26 追加★ 观战的战后处置页（20403）选择收集。顺序与上面那条无关
            //   （探针只记日志，NpcDuel 只读 id + 记账），所以放在后面、单独 try/catch。
            try { NpcDuel.NoteOptionClicked(__0); } catch { }
        }
    }
}
