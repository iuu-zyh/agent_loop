/// <summary>
/// 启动器 —— 拉起 / 重启 Python 平台
///
/// 在 ModMain.Init 时调用一次，解析出「跑哪个可执行」并 Process.Start 拉起；
/// 配置 UI「需重启生效」的改动保存后，由 ConfigPresenter 经本类编排重启：
///   1) 先请 Python 优雅退出（shutdown RPC，见 ws_channel.py handle_request）
///   2) 等旧进程让位 —— 判据是「WsClient 连接已断 / 句柄已退出 / 计时兜底」，
///      **不做端口探测**：本机对无监听端口的 connect 约 2s 才回 ConnectionRefused，
///      400ms 窗口的探测恒误判「占用」，曾把重启永久卡死（事故）
///   3) 用与首次拉起相同的入口重新拉起（入口已解析则直接复用，不再逐个探测）
/// 连接侧：WsClient 自带断线自动重连（常规 2s 一轮；重启期间经 SetFastRetry 缩到 0.3s）。
/// 分层：C# 只负责「拉起进程 + 建端口」，不参与对话逻辑；Python 是大脑。
///
/// 【两种启动形态】判据是**可执行文件名**（<see cref="ModPaths.IsInterpreter"/>），
/// 不是"有没有 .exe"——因为两种形态对「要不要补一个脚本路径」的答案正好相反：
///   ① 自包含 exe（发行版，`&lt;ModAssets&gt;\AgentLoopServer.exe`）：**一个参数都不传**，用户零安装。
///      注意它在磁盘上是**一个文件**，运行时却是**两个进程**（bootloader 解压载荷 → 起副本跑
///      Python），故强杀必须连子孙一起（见 <see cref="KillTree"/>）。
///   ② 解释器 + 脚本（开发机 / 用户自备 Python）：`python.exe -X utf8 &lt;源码树&gt;\scripts\server.py`。
///
/// 【发行版可移植性】路径解析全部移交 <see cref="ModPaths"/>，
/// 本类不再出现任何盘符字面量；并新增「C# → Python 环境变量契约」（见下），
/// 让两侧对「配置在哪、端口是几」有同一个答案，而不是各自猜。
/// </summary>
using System;
using System.Diagnostics;
using System.IO;

namespace AgentLoopBridge
{
    public static class Launcher
    {
        // ---- 进程所有权（重启用）：本类拉起成功才非空；手动起的 server.py 不属于本类 ----
        private static Process _proc;
        private static string _serverPath;
        private static string _pythonExe;

        /// <summary>Python 是否由本 mod 拉起（owned → 重启时可 KillOwned；非 owned 只能靠 shutdown RPC）。
        ///
        /// 旧注释称「进程句柄只用来强杀、不用来判断是否已退出」，理由是
        /// 「UseShellExecute 拉起时句柄未必追踪真实 python」——**那条理由描述的是一个没在用的配置**：
        /// <see cref="LaunchCore"/> 用的是 `UseShellExecute = false`（只有它才能下发环境变量），
        /// 此时 Process.Start 返回的就是直接创建的那个进程的句柄，HasExited 对它可靠。
        /// 真正的限制只有一条：onefile 形态下句柄是 **bootloader**，跑 Python 的是它的子进程——
        /// 但 bootloader 会 waitpid 子进程再退出，故「bootloader 退出 ⟺ 整个应用结束」依然成立。
        /// 模型梳理见 docs/brain-liveness-design.md §3.2。</summary>
        public static bool IsOwned => _proc != null;

        /// <summary>本 mod 是否**尝试过**拉起（= 我们是这套进程的负责人）。
        /// 玩家自己 `python scripts/server.py` 起的实例不在此列 → 不该被杀、也不该被自动重拉。</summary>
        public static bool LaunchAttempted { get; private set; }

        /// <summary>是否解析到了可执行入口。没解析到（缺 exe / 哨兵指错）→ 重拉多少次都没用，
        /// BrainLink 据此直接给结论，而不是白试三轮退避。</summary>
        public static bool EntryResolved { get; private set; }

        /// <summary>BrainLink 的可拉起判据：我们负责 + 有入口。</summary>
        public static bool CanRelaunch => LaunchAttempted && EntryResolved;

        /// <summary>进程层观测（BrainLink 的唯一进程判据）。
        /// <c>Unknown</c> = 句柄失效、**不可判定** —— 调用方必须当成"别下结论"，
        /// **绝不能**当成"死了"（那会去拉第二份抢端口）。</summary>
        public enum ProcState { Unknown, NotOwned, Alive, Dead }

        public static ProcState ProcessState()
        {
            if (!LaunchAttempted) return ProcState.NotOwned;
            var p = _proc;
            if (p == null) return ProcState.Dead;      // 拉过但句柄空：上次拉起失败，或已被清
            try { return p.HasExited ? ProcState.Dead : ProcState.Alive; }
            catch { return ProcState.Unknown; }        // 句柄失效：不下结论
        }

        /// <summary>当前实际使用的端口（供 WsClient / 配置面板取用，单一事实来源）。</summary>
        public static int WsPort { get; private set; } = ModConfigFile.DefaultPort;

        /// <summary>Init 时调用：**确保有一个活着的 Python**，而不是"一辈子只拉一次"。
        ///
        /// 旧版是「`_pythonStarted` 闩锁 + 在拉起**之前**就置位 + 丢弃返回值」，
        /// 两个后果：
        ///   ① 首次拉起失败（杀软拦截 / 路径不对）→ 闩锁已置位 → **这一局再也不会尝试**；
        ///   ② <see cref="ModMain.Destroy"/> 刻意不停 Python（回主界面复用），所以进程可能在
        ///      世界会话**之间**死掉，重进世界时旧闩锁会让它永远不再拉起。
        /// 现在按**进程层事实**判断：活着就跳过，死了就拉。</summary>
        public static void LaunchPythonOnce()
        {
            if (ProcessState() == ProcState.Alive)
            {
                ModMain.P("[Launcher] python 已在运行，跳过拉起（Destroy 不停进程，重进世界复用）");
                ModMain._pythonStarted = true;
                return;
            }
            ModMain._pythonStarted = true;
            Process proc;
            string err = LaunchCore(out proc);
            _proc = (err == null) ? proc : null;
            if (err != null)
                ModMain.P("[Launcher] 首次拉起失败：" + err + "（世界内由 BrainLink 按退避重试）");
        }

        /// <summary>BrainLink 专用：再拉一次。
        /// 与 <see cref="RelaunchPython"/> 的区别是**调用方已确认旧进程已死**，
        /// 故不清句柄、不走"请它优雅退出 + 等让位"那套编排（那套归 ConfigPresenter）。</summary>
        public static string SupervisorRelaunch()
        {
            ModMain.P("[Launcher] supervisor 重新拉起 Python（旧进程已判定死亡）");
            Process proc;
            string err = LaunchCore(out proc);
            if (err != null) { _proc = null; return err; }
            _proc = proc;
            return null;
        }

        /// <summary>
        /// 重启（配置「需重启」改动保存后调用；调用方已确认旧进程让位）：
        /// 绕过 once 锁再拉一次，成功后替换进程句柄。
        /// 返回错误文案（"" = 成功启动）。
        /// </summary>
        public static string RelaunchPython()
        {
            // 旧版在这里先 `_proc = null;` 再拉。后果：拉起失败时句柄被清空，
            // `ProcessState()` 于是分不清"拉过但失败"和"我们从没拉过"，BrainLink 会误判成
            // NotOwned 而拒绝重试。现在**保留旧句柄**——它指向的进程调用方已确认退出，
            // 留着既不会误杀（KillOwned 里有 HasExited 判断），又能让状态可判定。
            Process proc;
            string err = LaunchCore(out proc);
            if (err != null)
            {
                ModMain.P("[Launcher] 重启失败: " + err);
                return err;
            }
            _proc = proc;
            ModMain.P("[Launcher] Python 已重启：" + _pythonExe + " " + _serverPath);
            return "";
        }

        /// <summary>
        /// owned 进程仍在跑时强制结束（非 owned 走 shutdown RPC，不 Kill）。
        /// 供编排方在 shutdown 后超时兜底用。
        /// </summary>
        public static void KillOwned()
        {
            var p = _proc;
            if (p == null) return;
            try
            {
                if (!p.HasExited) KillTree(p);
                p.WaitForExit(2000);
                ModMain.P("[Launcher] owned python 已强制结束（含子进程）");
            }
            catch (Exception e)
            {
                ModMain.P("[Launcher] KillOwned: " + e.Message);
            }
        }

        /// <summary>
        /// 连同**子进程**一起结束。
        ///
        /// 为什么不能只 `p.Kill`（打包后实测踩到，务必别改回去）：
        ///   PyInstaller onefile 的 exe 运行时是**两个进程**——bootloader 父进程把 ~15MB 载荷解压到
        ///   %TEMP%，再 CreateProcess 起一个自己的副本真正跑 Python。实测 `taskkill /PID &lt;父&gt; /F`
        ///   只杀父进程时，**子进程活得好好的、端口照占**（PyInstaller 那套 job-object 兜底在这里没生效）。
        ///   后果致命且眼熟：重启时旧进程没死 → 新进程 bind 失败 → 「改完设置就再也连不上」，
        ///   与 2026-09-12 那次重启死锁是同一类故障。
        ///   `Process.Kill(entireProcessTree: true)` 是 .NET Core 3.0+ 的 API，本工程目标是
        ///   .NET Framework 4.7.2（MelonLoader 0.5.4），**用不了** —— 故借系统自带的
        ///   `taskkill /T /F`（/T = 连子孙一起；对单进程的解释器形态同样正确，无副作用）。
        /// </summary>
        private static void KillTree(Process p)
        {
            try
            {
                var psi = new ProcessStartInfo("taskkill", "/PID " + p.Id + " /T /F")
                {
                    UseShellExecute = false,
                    CreateNoWindow = true,
                    RedirectStandardOutput = true,   // 必须重定向：否则 taskkill 的输出会飘进游戏日志
                    RedirectStandardError = true,
                };
                using (var k = Process.Start(psi))
                {
                    if (k != null) k.WaitForExit(5000);
                }
            }
            catch (Exception e)
            {
                // taskkill 不可用（被安全软件拦 / PATH 异常）时退回单进程 Kill：
                // 至少把解释器形态处理对，exe 形态的子进程只能靠 shutdown RPC 那一侧了。
                ModMain.P("[Launcher] taskkill 失败，退回单进程 Kill：" + e.Message);
                try { if (!p.HasExited) p.Kill(); } catch { }
            }
        }

        // ---------- 内部：一次真实的进程拉起 ----------

        /// <summary>拉起核心：成功返回 null，失败返回错误文案（proc 置为 null）。</summary>
        private static string LaunchCore(out Process proc)
        {
            proc = null;
            LaunchAttempted = true;      // 从这一刻起"我们是负责人"（BrainLink 的判据之一）

            // 路径解析结果先落日志：用户报「没反应」时，这一行就能定位是缺文件还是缺入口
            ModMain.P("[Launcher] " + ModPaths.Report());

            // 先定「跑哪个可执行」，它反过来决定「要不要脚本」：
            //   自包含 exe（AgentLoopServer.exe）→ 自己就是程序，**不能**再补脚本路径
            //   Python 解释器（python.exe / py）  → 必须补 `-X utf8 <server.py>`
            string py = FindPython();
            if (py == null)
            {
                // 失败必须**把试过的路径全打出来**：否则玩家/作者看到的就是"mod 装了但没反应"，
                // 而真正的原因（少了 ModAssets 目录 / 哨兵指错）藏在看不见的候选表里。
                ModMain.P("[Launcher] 未找到可用的 Python 入口（自包含 exe 与解释器都没有），跳过拉起");
                ModMain.P("[Launcher] 已尝试以下可执行：");
                foreach (string cand in ModPaths.PythonCandidates())
                    ModMain.P("[Launcher]   ✗ " + cand);
                ModMain.P("[Launcher] 提示：发行包应含 <Mod根>/ModAssets/AgentLoopServer.exe；"
                          + "开发机跑源码树则在 <Mod根>/_dev_root.txt 写一行源码树路径。");
                return "未找到可用的 Python 入口（如需对话请手动启动）";
            }

            bool needsScript = ModPaths.IsInterpreter(py);
            string serverPath = null;
            if (needsScript)
            {
                serverPath = ModPaths.FindServerScript();
                if (serverPath == null)
                {
                    ModMain.P("[Launcher] 解释器 " + py + " 需要脚本，但未找到 server.py，跳过拉起");
                    ModMain.P("[Launcher] 已尝试以下位置：");
                    foreach (string cand in ModPaths.ServerCandidates())
                        ModMain.P("[Launcher]   ✗ " + cand);
                    ModMain.P("[Launcher] 提示：把源码树路径写进 <Mod根>/_dev_root.txt，或改用自包含 exe。");
                    return "未找到 server.py（如需对话请手动启动）";
                }
                ModMain.P("[Launcher] server.py = " + serverPath +
                          (string.IsNullOrEmpty(ModPaths.DevRootOverride()) ? "" : "（来自 _dev_root.txt 源码树覆盖）"));
            }
            else
            {
                ModMain.P("[Launcher] 入口 = " + py + "（自包含 exe：不传脚本参数）");
            }

            // 走到这里 = 可执行入口已解析成功（自包含 exe 或 解释器+脚本 两条路都通了）。
            // BrainLink 之后的重拉判据靠这个位：没解析到入口时重拉多少次都是白试。
            EntryResolved = true;

            int port = ModConfigFile.ReadWsPort();
            WsPort = port;

            try
            {
                var psi = new ProcessStartInfo
                {
                    FileName = py,
                    // 解释器形态：`-X utf8` 让 Python 自己按 UTF-8 处理 I/O 与文件系统。
                    // 中文版 Windows 的 ANSI 代码页是 GBK(936)，而游戏路径含「鬼谷八荒」，
                    // 不显式指定就会出现日志乱码 / UnicodeEncodeError。
                    // 自包含 exe 形态：**一个参数都不传**——它自己就是程序，且 -X utf8 会被它
                    // 当未知参数吞掉（等价效果由下面的 PYTHONUTF8=1 覆盖）。
                    Arguments = needsScript ? ("-X utf8 \"" + serverPath + "\"") : "",
                    // 工作目录 = Mod 根（不是 scripts/）：相对路径（config.json/logs）自然落在
                    // 用户可见的那一层；Python 侧另有 AGENT_LOOP_DATA 显式锚定，cwd 只是双保险。
                    WorkingDirectory = ModPaths.DataRoot
                                       ?? (serverPath != null ? Path.GetDirectoryName(serverPath) : ModPaths.ModRoot)
                                       ?? "",
                    UseShellExecute = false,     // 必须 false：只有它才能下发环境变量
                    CreateNoWindow = !ShowConsole, // 发行版默认无黑框；开发机可开哨兵要回来
                };
                // ---- C# → Python 环境变量契约 ----
                if (!string.IsNullOrEmpty(ModPaths.DataRoot))
                    psi.EnvironmentVariables[ModPaths.EnvDataRoot] = ModPaths.DataRoot;
                psi.EnvironmentVariables[ModPaths.EnvWsPort] = port.ToString();
                // 游戏进程号 → Python 侧的「父进程看门狗」：游戏一退，Python 自己立刻退。
                // 不下发它的话，Python 会一直等 WS 重连（永不退出），只能靠游戏/Steam 来杀：
                // Steam 把子进程也算作游戏本体在跟踪（其日志原文 `adding PID … as a tracked process`），
                // 于是「正在停止」要等它；杀不干净时还会留下孤儿占着端口（实测复现过）。
                try
                {
                    psi.EnvironmentVariables[ModPaths.EnvParentPid] =
                        Process.GetCurrentProcess().Id.ToString();
                }
                catch { }
                // 编码三件套（同上，中文 Windows + 中文路径的必备项）：
                psi.EnvironmentVariables["PYTHONUTF8"] = "1";
                psi.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";
                // 不写 .pyc：发行包已是只读字节码，运行时再生成 __pycache__ 只会污染目录
                psi.EnvironmentVariables["PYTHONDONTWRITEBYTECODE"] = "1";
                // 屏蔽「用户级 site-packages」：否则玩家机器上碰巧装过的同名包会盖住我们的
                psi.EnvironmentVariables["PYTHONNOUSERSITE"] = "1";
                // 清掉可能从外部带进来的路径变量，避免把无关的 Python 树挂进来
                psi.EnvironmentVariables.Remove("PYTHONPATH");
                psi.EnvironmentVariables.Remove("PYTHONHOME");
                // 日志总闸：若用户已在系统环境变量里设了 AGENT_LOOP_LOG，原样透传（不覆盖成空）
                try
                {
                    string envLog = Environment.GetEnvironmentVariable(ModPaths.EnvLog);
                    if (!string.IsNullOrEmpty(envLog)) psi.EnvironmentVariables[ModPaths.EnvLog] = envLog;
                }
                catch { }

                var p = Process.Start(psi);
                if (p == null)
                {
                    ModMain.P("[Launcher] 拉起失败：Process.Start 返回 null");
                    return "Process.Start 返回 null";
                }
                proc = p;
                _serverPath = serverPath;
                _pythonExe = py;
                ModMain.P("[Launcher] 已拉起：" + py
                          + (needsScript ? " \"" + serverPath + "\"" : "（自包含 exe，无脚本参数）")
                          + " 端口=" + port +
                          " 数据根=" + (ModPaths.DataRoot ?? "(未解析)") + " 控制台=" + (ShowConsole ? "显示" : "隐藏"));
                if (!needsScript)
                    ModMain.P("[Launcher] 排查提示：若长时间连不上，可直接运行 " + py + " --selftest 看依赖与数据根");
                return null;
            }
            catch (Exception e)
            {
                ModMain._pythonStarted = false;
                ModMain.P("[Launcher] 拉起失败：" + e.Message);
                return "拉起失败：" + e.Message;
            }
        }

        /// <summary>
        /// 是否显示 Python 控制台窗口。
        /// 发行版默认**隐藏**（游戏启动时弹黑框很吓人，且普通玩家不需要）；
        /// 开发机放一个 &lt;Mod根&gt;/_diag_console.txt 即可要回来（无需改代码重编）。
        /// </summary>
        private static bool ShowConsole
        {
            get
            {
                try
                {
                    string dir = ModPaths.DiagDir;
                    if (string.IsNullOrEmpty(dir)) return false;
                    return File.Exists(Path.Combine(dir, "_diag_console.txt"));
                }
                catch { return false; }
            }
        }

        /// <summary>
        /// 找到可用的 Python 入口：按 <see cref="ModPaths.PythonCandidates"/> 顺序探测。
        ///
        /// 两类候选的探测方式**根本不同**（见 <see cref="ModPaths.IsInterpreter"/>）：
        ///   · 解释器：跑 `-c "import websockets; print(1)"`，**要求 exit 0**——否则自动重启会拉到
        ///     无依赖的 python 导致新进程秒崩，比"什么都没起"更难排查。
        ///   · 自包含 exe：**不探测，存在即用**。两个理由：
        ///     ① 探测命令不成立：`-c` 会被它当未知参数忽略（server.py 用 parse_known_args），
        ///        然后一路起服务永不退出 → WaitForExit 超时 → 把好端端的 exe 判成"不可用"；
        ///        想探测只能用我们自己认的 `--selftest`（见 scripts/server.py），那又要多付一次
        ///        解压启动（onefile 每次启动都要解压 ~15MB）。
        ///     ② 探测失败也没有退路：它不行就只剩玩家机器上未必存在的系统 Python；
        ///        而**误判**（冷盘 + 杀软扫描让启动超过阈值）反而会把能用的 exe 拒掉，代价更大。
        ///        真出问题时 `AgentLoopServer.exe --selftest` 随时能单独验（日志里会提示这条命令）。
        /// 解析结果缓存，后续重启零探测。
        /// </summary>
        private static string FindPython()
        {
            // 已解析过的入口直接复用（重启路径不再逐个起进程探测：每个候选 = Process.Start +
            // WaitForExit(3s)，最坏 3×3s 全压在主线程上）。首次解析才做 websockets 校验。
            if (!string.IsNullOrEmpty(_pythonExe)) return _pythonExe;
            foreach (string name in ModPaths.PythonCandidates())
            {
                // 带路径的候选先验存在性——不存在就别起进程了（PATH 上的裸名交给 CreateProcess 解析）
                bool looksPathed = name.IndexOf('\\') >= 0 || name.IndexOf('/') >= 0;
                if (looksPathed)
                {
                    try { if (!File.Exists(name)) continue; } catch { continue; }
                }
                if (!ModPaths.IsInterpreter(name))
                {
                    _pythonExe = name;   // 自包含 exe：不探测（理由见方法注释）
                    return name;
                }
                try
                {
                    // 校验解释器可用 + 具备 websockets（server 的硬依赖）
                    using (var p = new Process
                    {
                        StartInfo = new ProcessStartInfo(name, "-c \"import websockets; print(1)\"")
                        {
                            UseShellExecute = false,
                            RedirectStandardOutput = true,
                            RedirectStandardError = true,
                            CreateNoWindow = true,
                        }
                    })
                    {
                        if (p.Start())
                        {
                            p.WaitForExit(3000);
                            if (p.ExitCode == 0)
                            {
                                _pythonExe = name;   // 记住可用解释器（后续重启零探测）
                                return name;
                            }
                        }
                    }
                }
                catch { /* 该解释器不可用，试下一个 */ }
            }
            return null;
        }
    }
}
