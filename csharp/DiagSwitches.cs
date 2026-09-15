/// <summary>
/// 运行期诊断开关（世界输入失效事故排查用，轻量文件哨兵）。
///
/// 目的：让"禁止某部件"的二分实验**不需要重启游戏**——用户在游戏运行中增删这两个文件，
/// 本类每 ~2 秒（120 帧）复查一次并即时生效（销毁/恢复对应部件），用户当场就能感觉出
/// 能不能移动，从而快速定位到底是哪个部件让游戏屏蔽了世界输入。
///
///   存在 &lt;Mod根&gt;\_diag_no_panels.txt  → 禁用通讯录/配置常驻宿主（并销毁已存在的）
///   存在 &lt;Mod根&gt;\_diag_no_hudbtn.txt  → 禁用 HUD「传音簿」按钮注入（并移除已注入的）
///
/// 【发行版可移植性】哨兵路径原本写死开发机源码树的绝对路径。
/// 现全部改为 <see cref="ModPaths.DiagDir"/>（= Mod 根）动态拼接——开发机 Mod 根就是
/// 源码树，老用法一字不变；发行版则是 ModExportData\Mod_Jgmg5L\，用户也能自助排障。
/// 本类**保留在发行版里**（不 #if 掉）：它是「用户报障 → 二分定位」的现场工具，
/// 且常态下这些文件都不存在，零开销、零副作用。
/// </summary>
#if AB_UI
using System;
using System.IO;

namespace AgentLoopBridge
{
    public static class DiagSwitches
    {
        /// <summary>哨兵文件路径 = &lt;Mod根&gt;/&lt;name&gt;（ModPaths.DiagDir 解析不到时退回 cwd）。</summary>
        private static string P(string name)
        {
            try { return Path.Combine(ModPaths.DiagDir, name); }
            catch { return name; }
        }

        private static string PathNoPanels { get { return P("_diag_no_panels.txt"); } }

        /// <summary>关掉 BrainLink 的**自动重新拉起**（保留状态观测与横幅提示）。
        /// 用途：万一自动重生在玩家机器上表现异常（杀软反复拦截 → 拉起风暴），
        /// 让作者能在**不重新编译**的前提下远程指导用户放一个空文件止血。</summary>
        private static string PathNoRelaunch { get { return P("_diag_no_relaunch.txt"); } }
        private static string PathNoHudBtn { get { return P("_diag_no_hudbtn.txt"); } }
        /// <summary>整机停用本 mod（撤 Harmony 补丁 + 销毁我们的 UI + 停掉各帧回调的实质工作）。
        /// 用途：判定"某个现象是不是本 mod 引起的"——这是最干净的因果对照实验。</summary>
        private static string PathNoMod { get { return P("_diag_no_mod.txt"); } }
        /// <summary>只撤销全部 Harmony 补丁（保留 UI 与帧回调）。</summary>
        private static string PathNoPatches { get { return P("_diag_no_patches.txt"); } }
        /// <summary>只撤销"剧情开启钩子"（WorldSystemMgr.OpenMapDrama 的 Postfix）。</summary>
        private static string PathNoPatchDrama { get { return P("_diag_no_patch_drama.txt"); } }
        /// <summary>只停"主动互动监测器"（NpcInitiativeMonitor：过日触发 NPC 主动传音 → 可能自动弹对话窗）。</summary>
        private static string PathNoMonitor { get { return P("_diag_no_monitor.txt"); } }
        /// <summary>改为"懒创建"：禁止进世界自动创建通讯录/配置宿主，只在用户主动用时才建
        /// （对话面板就是这个模式，从未出过问题 —— 用于验证"创建时机"假说）。</summary>
        private static string PathLazyPanels { get { return P("_diag_lazy_panels.txt"); } }
        /// <summary>完全不调游戏立绘 API（`PortraitService.Fill` 直接返回 false）——用于验证
        /// "打开面板时触碰 PortraitModel/RenderTexture 是否破坏游戏"。</summary>
        private static string PathNoPortraits { get { return P("_diag_no_portraits.txt"); } }
        /// <summary>通讯录"只创建不显示"——二分 创建/显示 哪个破坏世界输入。</summary>
        private static string PathCreateOnly { get { return P("_diag_create_only.txt"); } }
        /// <summary>诊断：强制触发 NPC 主动交互（自主交互系统的观测加速器）。
        /// 文件存在即生效；删文件即恢复常态。内容可选（逐行 key=value，`#` 开头忽略）：
        ///   interval=20      触发节拍（现实秒，默认 20，最小 1）
        ///   name=林婉清      只对该 NPC 触发（默认：候选集里好感最高者）
        ///   intent=malice    指定意图键（默认按亲密度真实派发，见 NpcInitiativeMonitor.PickIntent）
        /// 语义边界：**只放宽"时间"四闸**（日节拍/同日夜守卫/每日概率/双冷却），
        /// 候选集、同格门槛、意图派发、当面确认窗仍走真实链路——测的是真系统，不是假数据。
        /// 详见 NpcInitiativeMonitor.TryTriggerForced。</summary>
        private static string PathForceInitiative { get { return P("_diag_initiative_force.txt"); } }
        /// <summary>关掉**大地图快捷键拦截**（`MapWorldMgr.FastKey` 前缀），退回"只计数不拦截"。
        /// 用途：万一拦 FastKey 在你机器上表现异常（某个大地图操作失灵），放一个空文件即可止血，
        /// **不用重启游戏**（每 120 帧复查）。删文件即恢复拦截。</summary>
        private static string PathNoFastKey { get { return P("_diag_no_fastkey.txt"); } }
        private const int RecheckFrames = 120;

        private static int _timer;
        private static bool _patchesOff;      // 幂等：补丁只能单向撤销
        private static bool _dramaHookOff;
        public static bool NoPanels { get; private set; }

        /// <summary>见 <see cref="PathNoRelaunch"/>。BrainLink 只在真正要拉起前读它。</summary>
        public static bool NoRelaunch { get; private set; }
        public static bool NoHudButton { get; private set; }
        public static bool NoMonitor { get; private set; }
        public static bool LazyPanels { get; private set; }
        public static bool NoPortraits { get; private set; }
        public static bool CreateOnly { get; private set; }
        /// <summary>强制触发主动交互（见 PathForceInitiative）。</summary>
        public static bool InitiativeForce { get; private set; }
        /// <summary>见 <see cref="PathNoFastKey"/>：true = 只观察不拦截大地图快捷键。</summary>
        public static bool NoFastKeyGate { get; private set; }
        /// <summary>强制触发节拍秒（文件 interval= 可改，实时生效）。</summary>
        public static float InitiativeForceInterval { get; private set; }
        /// <summary>强制触发只对指定 NPC（文件 name=，空=好感最高者）。</summary>
        public static string InitiativeForceName { get; private set; }
        /// <summary>强制触发指定意图键（文件 intent=，空=按亲密度真实派发）。</summary>
        public static string InitiativeForceIntent { get; private set; }
        /// <summary>强制触发的选人策略：false=好感最高者（默认，可复现）；true=候选集里随机（pick=random）。</summary>
        public static bool InitiativeForceRandom { get; private set; }
        /// <summary>强制触发放行状态闸（ignore_state=1）：状态闸误报时仍能测链路；
        /// 放行时照样把命中原因打进日志（修闸依据）。默认 false=尊重状态闸。</summary>
        public static bool InitiativeForceIgnoreState { get; private set; }
        /// <summary>强制触发"连发模式"（repeat=1）：保持旧行为（每 interval 一次，不停表）。
        /// 默认 false = **一次即停**：成功发起过一次（同意/婉拒/已发出）后
        /// 就不再弹，改一下开关文件即重新武装——避免同格确认窗每 20s 弹一次打扰游玩。</summary>
        public static bool InitiativeForceRepeat { get; private set; }
        /// <summary>强制触发时**按异地判定**（文件 force_remote=1）：把 sameGrid 视作 false。
        /// 用途 = 可复现地观察"异地传音"这条路（不弹确认窗 → 直发 → 收尾落分流③ → HUD 横幅 + 传钮红点），
        /// 不必真的走到别的格子去。**只影响诊断强制触发**，日节拍/概率路径完全不受影响。</summary>
        public static bool InitiativeForceForceRemote { get; private set; }
        /// <summary>强制触发时**一个工具都不调**（文件 no_tools=1）：事件帧带 `speech_only=true`
        /// → Python 尾部追加调试约束 + 执行层拦下**全部**工具（连只读也拦），得到纯传音形态。
        /// 用途 = 观测横幅/红点（不带行动记录、不弹原版模态）。**只影响诊断强制触发**。</summary>
        public static bool InitiativeForceNoTools { get; private set; }
        /// <summary>开关文件的最后写入时刻（Ticks；0=读不到）。用于"一次即停"后的**重新武装**判定：
        /// 用户在文件里改任何内容并保存 → mtime 变化 → 重新武装一次。</summary>
        public static long InitiativeForceStamp { get; private set; }
        private static bool _applied;   // 首次是否已打印过状态

        /// <summary>每帧调用（内部按 RecheckFrames 节流）；状态变化时打日志。</summary>
        public static void Tick()
        {
            if (--_timer > 0) return;
            _timer = RecheckFrames;
            bool np = Exists(PathNoPanels);
            bool nh = Exists(PathNoHudBtn);
            bool nm = Exists(PathNoMod);
            bool npat = Exists(PathNoPatches);
            bool ndr = Exists(PathNoPatchDrama);
            bool nmon = Exists(PathNoMonitor);
            bool lz = Exists(PathLazyPanels);
            bool npor = Exists(PathNoPortraits);
            bool conly = Exists(PathCreateOnly);
            bool fini = Exists(PathForceInitiative);
            bool nfk = Exists(PathNoFastKey);
            // 独立于下面那个 _applied 汇总闸：BrainLink 随时可能读它，少一层耦合少一个坑
            bool nrl = Exists(PathNoRelaunch);
            if (nrl != NoRelaunch)
            {
                NoRelaunch = nrl;
                ModMain.P("[DiagSwitches] noRelaunch=" + NoRelaunch
                          + "（BrainLink 自动重新拉起已" + (NoRelaunch ? "关闭" : "恢复") + "）");
            }
            if (!_applied || np != NoPanels || nh != NoHudButton || nmon != NoMonitor || lz != LazyPanels || npor != NoPortraits || conly != CreateOnly || fini != InitiativeForce || nfk != NoFastKeyGate)
            {
                _applied = true;
                NoPanels = np;
                NoHudButton = nh;
                NoMonitor = nmon;
                LazyPanels = lz;
                NoPortraits = npor;
                CreateOnly = conly;
                InitiativeForce = fini;
                NoFastKeyGate = nfk;
                ModMain.P("[DiagSwitches] noPanels=" + NoPanels + " noHudButton=" + NoHudButton +
                          " noMonitor=" + NoMonitor + " lazyPanels=" + LazyPanels +
                          " noPortraits=" + NoPortraits + " createOnly=" + CreateOnly +
                          " initiativeForce=" + InitiativeForce +
                          " noFastKey=" + NoFastKeyGate + (NoFastKeyGate ? "（大地图快捷键拦截已关，只观察）" : "") +
                          " noMod=" + nm + " noPatches=" + npat + " noPatchDrama=" + ndr);
                // 即时生效：HUD 按钮是"注入体"，开关打开时主动移除（面板由 AbPanelProber 处理）
                if (NoHudButton)
                {
                    try { MapMainContactButton.RemoveInjected(); } catch { }
                }
            }
            // 强制触发：文件存在时每次复查都重解析内容（改 interval/name/intent 即时生效）
            if (fini) ReadInitiativeForce();
            // 补丁撤销是单向的（重新打补丁必须重启游戏）
            if (npat && !_patchesOff) { _patchesOff = true; ModMain.UnpatchAllPatches(); }
            if (ndr && !_dramaHookOff) { _dramaHookOff = true; ModMain.UnpatchDramaHook(); }
            if (nm) ModMain.SuspendAll("_diag_no_mod.txt 存在");
        }

        private static bool Exists(string p)
        {
            try { return File.Exists(p); } catch { return false; }
        }

        /// <summary>读文本行并容忍中文编码差异：有 BOM（UTF-8/UTF-16）交给 ReadAllLines；
        /// 无 BOM 先按严格 UTF-8 解，失败退 GBK(936)——`cmd` 重定向写的是 ANSI/GBK、
        /// PowerShell 5.1 `>` 写 UTF-16(带BOM)、VSCode/记事本默认 UTF-8，
        /// 用户手写 `name=林婉清` 三种来源都要能用，否则名字变乱码只会显示"候选集里没有"。</summary>
        private static string[] ReadAllLinesSmart(string p)
        {
            byte[] b = File.ReadAllBytes(p);
            bool bom = (b.Length >= 3 && b[0] == 0xEF && b[1] == 0xBB && b[2] == 0xBF)
                    || (b.Length >= 2 && ((b[0] == 0xFF && b[1] == 0xFE) || (b[0] == 0xFE && b[1] == 0xFF)));
            if (bom) return File.ReadAllLines(p);
            try
            {
                var strict = new System.Text.UTF8Encoding(false, true);
                return SplitLines(strict.GetString(b));
            }
            catch
            {
                try { return SplitLines(System.Text.Encoding.GetEncoding(936).GetString(b)); }
                catch { return File.ReadAllLines(p); }
            }
        }

        private static string[] SplitLines(string s)
        {
            return (s ?? "").Split(new[] { "\r\n", "\n" }, StringSplitOptions.None);
        }

        /// <summary>解析 _diag_initiative_force.txt（每次复查重读，内容改动即时生效）。
        /// 容错优先：文件读不到/行不合法一律退回默认，绝不抛给调用方（诊断件不拖垮主循环）。</summary>
        private static void ReadInitiativeForce()
        {
            float iv = 20f;
            string nm = null;
            string it = null;
            bool rnd = false;
            bool ign = false;
            bool rep = false;
            bool fr = false;
            bool nt = false;
            try
            {
                // 重新武装用的时间戳：与解析成功与否无关，先取（读不到=0）
                try { InitiativeForceStamp = File.GetLastWriteTimeUtc(PathForceInitiative).Ticks; }
                catch { InitiativeForceStamp = 0L; }
                foreach (string raw in ReadAllLinesSmart(PathForceInitiative))
                {
                    string line = (raw ?? "").Trim();
                    if (line.Length == 0 || line[0] == '#') continue;
                    int eq = line.IndexOf('=');
                    if (eq <= 0) continue;
                    string k = line.Substring(0, eq).Trim().ToLowerInvariant();
                    string v = line.Substring(eq + 1).Trim();
                    if (k == "interval")
                    {
                        float f;
                        if (float.TryParse(v, System.Globalization.NumberStyles.Float,
                                           System.Globalization.CultureInfo.InvariantCulture, out f) && f >= 1f)
                            iv = f;
                    }
                    else if (k == "name") { if (v.Length > 0) nm = v; }
                    else if (k == "intent") { if (v.Length > 0) it = v.ToLowerInvariant(); }
                    else if (k == "pick") { rnd = v.ToLowerInvariant() == "random"; }   // top(默认)/random
                    else if (k == "ignore_state") { ign = v == "1" || v.ToLowerInvariant() == "true"; }
                    else if (k == "repeat") { rep = v == "1" || v.ToLowerInvariant() == "true"; }
                    else if (k == "force_remote") { fr = v == "1" || v.ToLowerInvariant() == "true"; }
                    else if (k == "no_tools") { nt = v == "1" || v.ToLowerInvariant() == "true"; }
                }
            }
            catch { }
            if (Math.Abs(iv - InitiativeForceInterval) > 0.001f || nm != InitiativeForceName
                || it != InitiativeForceIntent || rnd != InitiativeForceRandom
                || ign != InitiativeForceIgnoreState || rep != InitiativeForceRepeat
                || fr != InitiativeForceForceRemote || nt != InitiativeForceNoTools)
            {
                InitiativeForceInterval = iv;
                InitiativeForceName = nm;
                InitiativeForceIntent = it;
                InitiativeForceRandom = rnd && nm == null;   // name= 定点优先于随机
                InitiativeForceIgnoreState = ign;
                InitiativeForceRepeat = rep;
                InitiativeForceForceRemote = fr;
                InitiativeForceNoTools = nt;
                ModMain.P("[DiagSwitches] initiativeForce: interval=" + iv + "s name=" + (nm ?? (InitiativeForceRandom ? "(随机候选)" : "(好感最高者)")) +
                          " intent=" + (it ?? "(按亲密度派发)") + " ignoreState=" + ign +
                          " repeat=" + rep + (rep ? "（连发）" : "（一次即停：成功一次后不再弹，改文件重新武装）") +
                          " forceRemote=" + fr + (fr ? "（诊断：本次按异地判定 → 横幅+红点）" : "") +
                          " noTools=" + nt + (nt ? "（诊断：本回合一个工具都不调）" : ""));
            }
        }
    }
}
#endif // AB_UI
