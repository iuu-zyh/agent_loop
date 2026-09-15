/// <summary>
/// ModPaths —— 本 Mod 全部「外部路径」的唯一事实来源（发行版可移植性的地基）。
///
/// 为什么要有这个文件：
///   旧实现把开发机的绝对路径写死在 Launcher/DiagSwitches 里（源码树路径、
///   开发机用户目录下的 venv python），换一台机器必然找不到 Python；而且「相对 DLL 定位」这条
///   唯一在所有安装形态下都成立的路子反而没走。本类收编全部路径解析：
///   任何其它文件都不许再出现盘符字面量。
///
/// 解析优先级（全部失败才返回 null，调用方按「缺件」降级而不是崩）：
///   ① 环境变量 `AGENT_LOOP_ROOT` —— 显式覆盖（开发机 / 高级用户 / 便携版）
///   ② DLL 自身位置向上找到「Mod 根」—— **最可靠**，且同时覆盖两种形态：
///        · 发行包：&lt;Mod根&gt;\ModCode\dll\MOD_Jgmg5L.dll → 该层有 ModCode/ModRes
///        · 源码树：&lt;树根&gt;\csharp\bin\Debug\MOD_Jgmg5L.dll → 该层有 scripts\server.py
///      不依赖游戏是否已初始化，故优先于官方 API
///   ③ `g.mod.GetModPathRoot(ModId)` —— 游戏官方 API（工坊/本地两种安装形态都成立）
///   ④ `ModMgr.pathModExportData/Mod_Jgmg5L` —— 官方路径常量兜底
///
/// 目录约定（发行版布局，&lt;ModRoot&gt; = ModExportData/Mod_Jgmg5L）：
///   &lt;ModRoot&gt;/ModCode/dll/MOD_Jgmg5L.dll      本程序集（游戏只枚举这一层）
///   &lt;ModRoot&gt;/ModAssets/AgentLoopServer.exe    自包含 Python 入口（一个文件，勿手改）
///   &lt;ModRoot&gt;/ModAssets/config.json            用户可编辑配置
///   &lt;ModRoot&gt;/ModAssets/prompts/               用户可编辑提示词
///   &lt;ModRoot&gt;/logs/                            日志与诊断哨兵
/// 为什么数据（config/prompts）与 exe 同在 ModAssets：它们是「用户要看得见、改得动」的东西，
/// 而 ModAssets 是官方编辑器唯一会**逐字节原样带进发行包**的槽位（见 RuntimeDirNames 注释）。
///
/// **为什么运行时放 &lt;ModRoot&gt;/ModAssets\，而不是 &lt;ModRoot&gt;/ModCode/python/**（实证）：
///   ① 游戏把 `ModCode\dll\` 里**所有** dll 都当 mod 程序集加载——工坊 mod 2814703549 连
///      `UnityEngine.InputLegacyModule.dll` 都往那儿塞即是明证。CPython 的 python3xx.dll
///      落进去就会被当 mod 程序集加载。而 `ModCode\` 下放非 dll 子目录**是否被递归枚举无从实证**
///      （全部工坊 mod 的 ModCode 下都只有 dll\ 一层），不赌。
///   ② 反向实证：本 mod 的 `portrait_cache\` 就是运行时新建在 **ModRoot 根层**的目录，
///      游戏照常加载、零告警——根层放自有目录已被实地证明安全。
/// 开发机形态（源码树）由 `AGENT_LOOP_ROOT` 或 `_dev_root.txt` 指过来，无需改代码。
/// </summary>
using System;
using System.IO;
using System.Reflection;

namespace AgentLoopBridge
{
    internal static class ModPaths
    {
        internal const string ModId = "Jgmg5L";

        // ---------- 环境变量契约（C# ↔ Python 的显式接口，勿改名） ----------
        /// <summary>Mod 数据根目录（config.json / prompts/ / logs/ 所在）。C# 拉起 Python 时下发。</summary>
        internal const string EnvDataRoot = "AGENT_LOOP_DATA";
        /// <summary>WS 端口。已有规约（server.py 早已支持），C# 按 config.json 下发以闭合端口回环。</summary>
        internal const string EnvWsPort = "AGENT_LOOP_WS";
        /// <summary>日志总闸。已有规约，透传即可。</summary>
        internal const string EnvLog = "AGENT_LOOP_LOG";
        /// <summary>Mod 根目录显式覆盖。</summary>
        internal const string EnvRootOverride = "AGENT_LOOP_ROOT";
        /// <summary>游戏进程号。Python 侧拿它做「父进程看门狗」——游戏一退，Python 立刻自己退。
        ///
        /// 为什么必须有：Steam 会把**游戏拉起的子进程也当作游戏本体跟踪**
        /// （Steam 日志原文：`AppID 1468810 adding PID … as a tracked process "…\AgentLoopServer.exe"`），
        /// 于是子进程不走，Steam 的「正在停止」就结束不了。而我们的服务原本是**永不退出**的
        /// （WS 断了只等重连），全靠游戏/Steam 来杀 —— 于是：
        ///   ① 杀不干净的场合会留下**孤儿进程占着端口**（实测复现过），下一次启动直接 bind 失败；
        ///   ② Steam 得多等一个不属于它的进程。
        /// 判据用**父进程存活**而不是「WS 断线超时」：配置面板保存会重启 Python，
        /// 那一刻 WS 同样会断，但游戏活得好好的——用断线做判据会把自己误杀。</summary>
        internal const string EnvParentPid = "AGENT_LOOP_PPID";

        private static bool _resolved;
        private static string _modRoot;
        private static string _rootSource;

        /// <summary>Mod 根目录；解析不到返回 null（调用方降级，勿抛）。</summary>
        internal static string ModRoot
        {
            get { Resolve(); return _modRoot; }
        }

        /// <summary>根目录是怎么来的（诊断用，进日志）。</summary>
        internal static string RootSource
        {
            get { Resolve(); return _rootSource; }
        }

        /// <summary>
        /// 用户可编辑数据根（config.json / prompts / logs 都锚这层）。
        ///
        /// 两种布局都认：
        ///   · `&lt;Mod根&gt;\ModAssets\` —— **官方管线布局**：config.json / prompts 放 here，
        ///     编辑器导出会一起带走（与神识传音把 AIPrompt.json 放 ModAssets 同款）。
        ///   · `&lt;Mod根&gt;\` —— 手工拼装 / 开发机（源码树根）布局，历史行为不变。
        /// 判据用「ModAssets 下是否真有我们的数据文件」，而不是「ModAssets 存不存在」——
        /// 后者任何 mod 都有（里面至少有一张说明纸），拿它当判据会把数据根错误地下移一层。
        /// </summary>
        internal static string DataRoot
        {
            get
            {
                string r = ModRoot;
                if (string.IsNullOrEmpty(r)) return null;
                try
                {
                    string ma = Path.Combine(r, "ModAssets");
                    if (Directory.Exists(ma) &&
                        (File.Exists(Path.Combine(ma, "config.json")) ||
                         Directory.Exists(Path.Combine(ma, "prompts"))))
                        return ma;
                }
                catch { }
                return r;
            }
        }

        /// <summary>诊断哨兵目录（`_diag_*.txt`）——与 Mod 根同层，开发机即源码树根。</summary>
        internal static string DiagDir
        {
            get
            {
                string r = DataRoot;
                if (!string.IsNullOrEmpty(r)) return r;
                // 极端兜底：连 Mod 根都解析不到时退到 cwd（至少不写死盘符）
                try { return Directory.GetCurrentDirectory(); } catch { return "."; }
            }
        }

        /// <summary>运行时目录的候选相对路径（按优先级）。
        ///
        /// `ModAssets`（**裸根，不带子目录**）排第一 —— 发行形态是「一个自包含 exe」，
        ///   它直接躺在 `ModAssets\AgentLoopServer.exe`，与 `config.json` / `prompts\` 并排。
        ///   为什么放这儿、为什么能进发行包（实证）：
        ///   `ModAssets\` 是官方给 mod 留的"自带文件"槽位，目录里那张说明纸写的就是
        ///   「这个目录下的所有文件都会被一起导出」，且编辑器**逐字节原样复制**它
        ///   （实测：工程内文件与导出产物 md5 都是 d33f979a…）。
        ///   参考实现：神识传音（MOD_9CiDqJ）把加密程序集 `assert1.bin/assert2.bin` 与 8 个
        ///   明文 JSON（提示词/人设池）全放在 `ModAssets\` 下，另有一个 loader DLL 在
        ///   `ModCode\dll\` —— 与本 mod「C# 拉起自有进程」是同一套路数。
        ///   所以放这里 = **编辑器导出即完整包**，不必再手工合并。
        ///
        /// `ModAssets\AgentLoop`（子目录形态）保留为第二候选：早期"便携 CPython + 源码树"
        /// 的部署形态长这样，用户若已按那版铺过包，不该因为改版就起不来。
        /// `AgentLoop`（Mod 根层）第三：手工拼装 / 便携版，且根层放自有目录同样被实证安全
        /// （`portrait_cache\` 就是运行时建在根层的）。
        /// `ModCode\python` 只作历史实验形态的兼容，排最后。
        /// </summary>
        private static readonly string[] RuntimeDirNames = { "ModAssets", "ModAssets\\AgentLoop", "AgentLoop", "ModCode\\python" };

        /// <summary>自包含入口 exe 的候选文件名（PyInstaller/Nuitka 产物）。
        ///
        /// 注意这不是"另一个 Python 解释器"：它**不接受脚本路径参数**，启动方式与
        /// `python server.py` 根本不同（见 <see cref="IsInterpreter"/>）。</summary>
        private static readonly string[] SelfContainedExeNames = { "AgentLoopServer.exe", "server.exe" };

        /// <summary>
        /// 随包分发的运行时目录：返回**第一个真实存在**的候选，都不存在则返回首选路径（诊断用）。
        /// 旧实现固定返回 <c>RuntimeDirNames[0]</c>，加了候选后那样会报出一个并不存在的目录。
        /// </summary>
        internal static string PythonHome
        {
            get
            {
                string r = ModRoot;
                if (string.IsNullOrEmpty(r)) return null;
                try
                {
                    foreach (string dirName in RuntimeDirNames)
                    {
                        string home = Path.Combine(r, dirName);
                        if (Directory.Exists(home)) return home;
                    }
                }
                catch { }
                return Path.Combine(r, RuntimeDirNames[0]);
            }
        }

        /// <summary>
        /// 某个可执行文件是不是「Python 解释器」——即**要不要在后面补一个脚本路径**。
        ///
        /// 这是发行形态切换后新增的唯一判据，用**文件名**而不是"有没有 .exe"：
        ///   · `python.exe` / `py.exe` / PATH 上的裸名 `python` → 是解释器，要传 `-X utf8 server.py`
        ///   · `AgentLoopServer.exe`（自包含）→ 不是解释器，**不能传脚本路径**
        ///     （它会把路径当自己的 argv[1]，而我们没解析位置参数，等于白传；更糟的是
        ///     探测命令 `-c "import websockets"` 会被它当未知参数忽略、然后一路起服务不退出，
        ///     把 WaitForExit 拖到超时 → 好好的 exe 被判成"不可用"）
        /// 用文件名而不是维护白名单，是为了让**改名也不坏**（例如测试时叫 `test.exe`）。
        /// </summary>
        internal static bool IsInterpreter(string exe)
        {
            if (string.IsNullOrEmpty(exe)) return false;
            string n;
            try { n = Path.GetFileNameWithoutExtension(exe); } catch { return false; }
            if (string.IsNullOrEmpty(n)) return false;
            switch (n.ToLowerInvariant())
            {
                case "python":
                case "pythonw":
                case "python3":
                case "python3w":
                case "py":
                case "pyw":
                    return true;
                default:
                    return false;
            }
        }

        /// <summary>日志目录（缺则尽力创建；失败返回 null 由调用方降级）。</summary>
        internal static string LogDir
        {
            get
            {
                string r = DataRoot;
                if (string.IsNullOrEmpty(r)) return null;
                string d = Path.Combine(r, "logs");
                try { Directory.CreateDirectory(d); } catch { return null; }
                return d;
            }
        }

        // ---------- 关键件解析 ----------

        /// <summary>
        /// 开发机逃生口：&lt;Mod根&gt;\_dev_root.txt 存在时，其内容（一行路径）指回**源码树**，
        /// 让游戏直接跑活源码而不是随包分发的副本。
        ///
        /// 为什么必须有这个口子（自查发现的回归）：
        ///   游戏加载的是**部署到游戏目录的那份 DLL**，所以 `ResolveFromDll` 会把 Mod 根定位到
        ///   `&lt;游戏&gt;\ModExportData\Mod_Jgmg5L`（那里有 ModCode/ModRes），而**不是** `F:\agent_loop`。
        ///   改造前 Launcher 把源码树路径写死在候选表最前面，所以永远跑活源码；
        ///   改成动态解析后，部署态的 DLL 就再也找不到 `F:\agent_loop\scripts\server.py` 了
        ///   —— 除非 `&lt;Mod根&gt;\AgentLoop\` 里已经躺着一份随包副本。
        ///   实测：改造后若不修，作者本机"DLL 部署上去了、Python 却起不来"。
        ///
        /// 判据用**文件哨兵**而不是环境变量：与仓库既有的 `_diag_*.txt` 文化一致，
        /// 增删即时生效、不需要重启系统或设用户环境变量，而且这个文件天然不会进发行包。
        /// 内容支持 Windows 形式（`F:\agent_loop`）与 WSL 形式（`/mnt/f/agent_loop`）。
        /// </summary>
        internal static string DevRootOverride()
        {
            try
            {
                // 两个位置都找：Mod 根（手工拼装形态的老习惯）与数据根（= ModAssets，官方管线形态）。
                // 分开找是有必要的——发行布局下 config/prompts 都搬进了 ModAssets，作者自然会
                // 把哨兵也放那儿；而 root 布局下哨兵本来就在根层。两处都认，放哪儿都行。
                foreach (string dir in new[] { DataRoot, ModRoot })
                {
                    if (string.IsNullOrEmpty(dir)) continue;
                    string f = Path.Combine(dir, "_dev_root.txt");
                    if (!File.Exists(f)) continue;
                    foreach (string raw in File.ReadAllLines(f))
                    {
                        string line = (raw ?? "").Trim().Trim('"');
                        if (line.Length == 0 || line[0] == '#') continue;
                        if (Directory.Exists(line)) return Path.GetFullPath(line);
                        ModMain.P("[ModPaths] _dev_root.txt 里写的路径不存在，已忽略：" + line);
                        return null;
                    }
                    ModMain.P("[ModPaths] _dev_root.txt 是空的（应写一行源码树路径），已忽略");
                    return null;
                }
            }
            catch (Exception e)
            {
                ModMain.P("[ModPaths] 读 _dev_root.txt 失败：" + e.Message);
            }
            return null;
        }

        /// <summary>server.py 候选（按可靠性排序）。</summary>
        internal static string[] ServerCandidates()
        {
            var list = new System.Collections.Generic.List<string>();
            // ⓪ 开发机覆盖：哨兵指到哪儿就用哪儿的 scripts/server.py（见 DevRootOverride）
            string dev = DevRootOverride();
            if (!string.IsNullOrEmpty(dev))
            {
                list.Add(Path.Combine(dev, "scripts", "server.py"));
                list.Add(Path.Combine(dev, "server.py"));
            }
            string root = ModRoot;
            if (!string.IsNullOrEmpty(root))
            {
                foreach (string dirName in RuntimeDirNames)
                {
                    string home = Path.Combine(root, dirName);
                    list.Add(Path.Combine(home, "server.py"));                        // 发行版布局
                    list.Add(Path.Combine(home, "agent_loop", "scripts", "server.py"));
                    list.Add(Path.Combine(home, "scripts", "server.py"));
                }
                // 源码树形态：树根恰好就是 Mod 根（独立跑、未部署时）
                list.Add(Path.Combine(root, "scripts", "server.py"));
            }
            // 最终兜底：游戏根目录（历史行为，保留但排最后）
            try
            {
                string baseDir = AppDomain.CurrentDomain.BaseDirectory;
                if (!string.IsNullOrEmpty(baseDir))
                {
                    list.Add(Path.Combine(baseDir, "scripts", "server.py"));
                    list.Add(Path.Combine(baseDir, "server.py"));
                }
            }
            catch { }
            return list.ToArray();
        }

        /// <summary>找到第一个存在的 server.py；都没有返回 null（**自包含 exe 形态下本来就该是 null**）。</summary>
        internal static string FindServerScript()
        {
            foreach (string cand in ServerCandidates())
            {
                try { if (File.Exists(cand)) return Path.GetFullPath(cand); } catch { }
            }
            return null;
        }

        /// <summary>自包含入口 exe 候选（与 <see cref="PythonCandidates"/> 的前几项同源，这里只挑 exe）。</summary>
        internal static string[] SelfContainedCandidates()
        {
            var list = new System.Collections.Generic.List<string>();
            string r = ModRoot;
            if (!string.IsNullOrEmpty(r))
            {
                foreach (string dirName in RuntimeDirNames)
                {
                    string home = Path.Combine(r, dirName);
                    foreach (string exeName in SelfContainedExeNames)
                        list.Add(Path.Combine(home, exeName));
                }
            }
            return list.ToArray();
        }

        /// <summary>找到第一个存在的自包含 exe；没有返回 null。</summary>
        internal static string FindSelfContainedExe()
        {
            foreach (string cand in SelfContainedCandidates())
            {
                try { if (File.Exists(cand)) return Path.GetFullPath(cand); } catch { }
            }
            return null;
        }

        /// <summary>
        /// 可执行候选（解释器**或**自包含 exe）。**顺序即语义**：
        ///   ① 随包自包含 exe（&lt;ModAssets&gt;\AgentLoopServer.exe）—— 发行版默认，用户零安装
        ///   ② 随包便携 CPython（&lt;ModAssets&gt;\AgentLoop\python.exe 等）—— 早期部署形态
        ///   ③ 项目根下的虚拟环境 + PATH 上的 python / python3 / py —— 开发机与「用户自备 Python」
        /// 注意：这里**不做任何探测**（那是 Launcher 的职责，且探测有进程开销）。
        ///
        /// 开发覆盖（`_dev_root.txt`）生效时**跳过 ①**：那时作者要的是"跑活源码"，
        /// 若仍把随包 exe 排第一，就会拿打包产物盖掉正在改的源码（本末倒置）。
        /// </summary>
        internal static string[] PythonCandidates()
        {
            var list = new System.Collections.Generic.List<string>();
            string r = ModRoot;
            if (!string.IsNullOrEmpty(r))
            {
                // ① 自包含 exe：用户零安装；它就是我们应该跑的那一份
                if (string.IsNullOrEmpty(DevRootOverride()))
                {
                    foreach (string dirName in RuntimeDirNames)
                    {
                        string home = Path.Combine(r, dirName);
                        foreach (string exeName in SelfContainedExeNames)
                            list.Add(Path.Combine(home, exeName));
                    }
                }
                // ② 便携 CPython（embeddable / python-build-standalone）
                foreach (string dirName in RuntimeDirNames)
                {
                    string home = Path.Combine(r, dirName);
                    list.Add(Path.Combine(home, "python.exe"));
                    list.Add(Path.Combine(home, "runtime", "python.exe"));
                    list.Add(Path.Combine(home, "python", "python.exe"));
                    list.Add(Path.Combine(home, "Scripts", "python.exe"));
                }
                // ③ 项目根下的虚拟环境（**通用约定**，不是某一台机器的路径）：
                //    开发机自建 venv 时最自然的落点；玩家侧一般不存在，存在性检查会跳过。
                list.Add(Path.Combine(r, ".venv", "Scripts", "python.exe"));
                list.Add(Path.Combine(r, "venv", "Scripts", "python.exe"));
            }
            // ④ PATH 上的解释器：开发机 / 用户自备 Python 场景（探测要求能 import websockets）
            list.Add("python");
            list.Add("python3");
            list.Add("py");
            return list.ToArray();
        }

        // ---------- 内部 ----------

        private static void Resolve()
        {
            if (_resolved) return;
            _resolved = true;

            // ① 显式覆盖
            try
            {
                string env = Environment.GetEnvironmentVariable(EnvRootOverride);
                if (!string.IsNullOrEmpty(env) && Directory.Exists(env))
                {
                    _modRoot = Path.GetFullPath(env);
                    _rootSource = "env:" + EnvRootOverride;
                    return;
                }
            }
            catch { }

            // ② DLL 位置向上找（最可靠，不依赖游戏状态）
            string fromDll = ResolveFromDll();
            if (fromDll != null)
            {
                _modRoot = fromDll;
                _rootSource = "dll";
                return;
            }

            // ③ 游戏官方 API
            try
            {
                string r = g.mod.GetModPathRoot(ModId);
                if (!string.IsNullOrEmpty(r) && Directory.Exists(r))
                {
                    _modRoot = Path.GetFullPath(r);
                    _rootSource = "g.mod";
                    return;
                }
            }
            catch { }

            // ④ 官方路径常量
            try
            {
                string r = Path.Combine(ModMgr.pathModExportData, "Mod_" + ModId);
                if (Directory.Exists(r))
                {
                    _modRoot = Path.GetFullPath(r);
                    _rootSource = "ModMgr";
                    return;
                }
            }
            catch { }

            _modRoot = null;
            _rootSource = "未解析";
        }

        /// <summary>
        /// 从本程序集位置向上找「Mod 根 / 源码树根」。**两种形态都要认**，这是本类的核心：
        ///   · 源码树（开发机）：DLL 在 &lt;树根&gt;\csharp\bin\Debug\，判据 = 该层有 `scripts\server.py`
        ///     —— 认出来后 Launcher 直接跑**活源码**（改 Python 重启即生效，无需打包）
        ///   · 发行包（玩家机）：DLL 在 &lt;Mod根&gt;\ModCode\dll\，判据 = 该层有 `ModCode` 或 `ModRes`
        /// 取**最近的**匹配祖先（第一个命中的就返回），所以两种形态互不干扰。
        ///
        /// 为什么不能只认 ModCode：开发树里根本没有 ModCode 目录 —— 只认它会让开发机
        /// 解析落到游戏部署目录（g.mod.GetModPathRoot），Launcher 就再也找不到
        /// &lt;源码树&gt;\scripts\server.py，作者本机反而先坏（自查发现）。
        /// </summary>
        private static string ResolveFromDll()
        {
            string loc = null;
            try { loc = Assembly.GetExecutingAssembly().Location; } catch { }
            if (string.IsNullOrEmpty(loc)) return null;

            string dir;
            try { dir = Path.GetDirectoryName(loc); } catch { return null; }
            if (string.IsNullOrEmpty(dir)) return null;

            string probe = dir;
            for (int i = 0; i < 6 && !string.IsNullOrEmpty(probe); i++)
            {
                try
                {
                    // ① 发行包：有 ModCode / ModRes
                    if (Directory.Exists(Path.Combine(probe, "ModCode")) ||
                        Directory.Exists(Path.Combine(probe, "ModRes")))
                        return probe;
                    // ② 源码树：有 scripts\server.py（开发机）
                    if (File.Exists(Path.Combine(probe, "scripts", "server.py")))
                        return probe;
                }
                catch { }
                try { probe = Path.GetDirectoryName(probe); } catch { break; }
            }
            return null;
        }

        /// <summary>Mod 根解析结果一行摘要（进启动日志，便于用户报障时定位）。</summary>
        internal static string Report()
        {
            Resolve();
            string dev = DevRootOverride();
            string exe = string.IsNullOrEmpty(dev) ? FindSelfContainedExe() : null;
            string script = FindServerScript();
            return "root=" + (_modRoot ?? "(未解析)") + " 来源=" + _rootSource +
                   " 数据根=" + (DataRoot ?? "(无)") +
                   " 入口=" + (exe != null ? exe + "（自包含 exe）"
                                            : (script ?? "(未找到 server.py 也无 exe)")) +
                   (string.IsNullOrEmpty(dev) ? "" : " ⚠开发覆盖=" + dev + "（跑源码树，非随包副本）");
        }
    }
}
