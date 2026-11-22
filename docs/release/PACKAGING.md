# 打包与分发（2026-09-19 改版：**发行档 = Nuitka**；Python 侧 = 一个自包含 exe）

本文回答三件事：**怎么打出能给别人用的包**、**路径为什么这么排**、**Python 源码能保护到什么程度**。

> **一句话发行规则：上线只用 Nuitka（§1 步骤 ②），PyInstaller 只用于本地自测。
> 误报代价已知并接受，机制见 §6.1。**

---

## 0. 形态一句话

Python 侧**不再是「便携 CPython + 源码目录树」**，而是**一个 ~18 MB 的自包含 `AgentLoopServer.exe`**
（**Nuitka onefile**，内含解释器 + 我们的全部代码 + websockets/openai 及依赖，编译成机器码）。

> **发行档 = Nuitka**（2026-09-19 起，见 §6）。PyInstaller 档只用于本地快速自测，
> **不要拿它出包**。

```
Mod_Jgmg5L/
├── ModAssets/                       ★ 官方「自带文件」槽位，编辑器「导出模组」逐字节原样带走
│   ├── AgentLoopServer.exe          Python 侧全部（~18 MB，一个文件，Nuitka 编译产物）
│   ├── config.json                  出厂模板（空 key）；首次启动复制到 %USERPROFILE%\.agent_loop
│   └── prompts/                     出厂提示词；首次启动复制到 %USERPROFILE%\.agent_loop
├── ModCode/dll/MOD_Jgmg5L.dll       C# 端（游戏只枚举这一层）
├── ModRes/AssetBundle/              UI 预制体 AB
├── ModExcel/                        剧情配置壳表
├── ModExportData.cache              编辑器生成，游戏按它识别 mod
└── 安装说明.txt
```

**为什么数据放在 `ModAssets/`、但运行时不从那里读写**：`ModAssets\` 是官方编辑器唯一保证会被
带进发行包的槽位（md5 实证：工程内文件与导出产物逐字节相同），所以出厂模板放这里；而工坊更新
会整包同步 `ModAssets/`，用户数据必须落在 `%USERPROFILE%\.agent_loop`（首次启动由
`UserDataMigrationCore` 只补缺失地迁过去），否则每次更新都会盖掉 Key / 提示词 / 自建人设。

---

## 1. 一条命令

```bash
# ① 编 Release 版 DLL（打包器只收 Release，原因见 §5）
"/mnt/c/Program Files/dotnet/dotnet.exe" build csharp/AgentLoopBridge.csproj -c Release

# ② 用 Nuitka 编译 Python 侧（★ 发行必走这条，约 5~15 分钟）
cd /mnt/f/.agent_loop_build          # 别在 /home 下跑：cwd 会变成 UNC 路径，cmd.exe 不认
/mnt/f/.agent_loop_build/pybuild/venv-nuitka/Scripts/python.exe -X utf8 \
    'F:\agent_loop\scripts\build_exe_nuitka.py' --jobs 12
#    产物：F:\.agent_loop_build\nuitka\AgentLoopServer.exe

# ③ 把编译好的 exe 铺进模组编辑器工程
python3 scripts/pack_release.py \
    --exe 'F:\.agent_loop_build\nuitka\AgentLoopServer.exe' --install-to-project
#    然后：打开模组编辑器 → 点「导出模组」→ 得到完整 mod
```

**本地快速自测**（只改提示词、想看效果时）不必等 Nuitka —— PyInstaller 档 16 秒就出，
功能等价，**但绝不能拿它出包**：

```bash
python3 scripts/pack_release.py --build-exe --install-to-project   # ⚠ 自测用，非发行
```

> **⚠ `--build-exe` 与 `--deploy` / `--install-to-project` 之间有一个坑（11-21 实测）**
>
> `--build-exe` 的产物落在 `../.agent_loop_build/pybuild/dist/`，
> 而 `--deploy` 默认从 `../.agent_loop_build/stage/` 取 exe —— **两者可能不是同一份**
> （实测差 3 天、体积差 3MB，部署上去的是上一次的包，功能改动全部丢失）。
>
> 因此**部署时要用 `--exe` 显式指向刚打出来的那个**：
>
> ```bash
> python3 scripts/pack_release.py \
>     --exe 'F:\.agent_loop_build\pybuild\dist\AgentLoopServer.exe' --deploy
> ```
>
> 另：exe 被占用时 `--deploy` 会 `PermissionError`。游戏进程退出**不杀** Python
> （`Launcher` 刻意复用进程），必须先
> `Stop-Process -Name AgentLoopServer -Force`。详见 `docs/DEV_RULES.md` §四「Python 侧」。

装进游戏目录试跑（不动编辑器工程）：

```bash
python3 scripts/pack_release.py --deploy
```

| 开关 | 用途 |
|---|---|
| `--build-exe` | 先跑 **PyInstaller** 重打 exe（约 16 秒）。**自测用**；发行走 ② 的 Nuitka |
| `--exe <路径>` | 用已有的 exe。**发行时用它指向 Nuitka 产物** |
| `--install-to-project` | 铺进编辑器工程的标准槽位 |
| `--deploy` | 整包装进游戏目录（自动迁移旧布局的 config.json，保留玩家的 key） |
| `--zip` | 额外压一个 zip。**默认不压**——zip 只是传输容器，走工坊/文件拷贝都不需要它 |
| `--portable` | 回退到旧的「便携 CPython + 源码树」形态（仅兼容老部署） |
| `--config Debug` | 取 Debug 版 DLL（**只给自己调试**，会把 PDB 路径写进 PE，见 §5） |

游戏目录自动探测（`--game-root` 或 `GGBH_GAME_ROOT` 覆盖）。构建缓存在**仓库之外**的
`../.agent_loop_build/`——仓库根就是 Python 包本身，构建垃圾不能住在包里（§5）。

**首次构建 exe 需要一次性准备**（干净 venv —— exe 里装什么**完全取决于打包环境装了什么**，
用开发环境打等于赌「我这儿碰巧没装多余东西」）：

```bash
# A. 自测档（PyInstaller，16 秒）
py -3.12 -m venv /mnt/f/.agent_loop_build/pybuild/venv
/mnt/f/.agent_loop_build/pybuild/venv/Scripts/python.exe -m pip install -U pip pyinstaller
/mnt/f/.agent_loop_build/pybuild/venv/Scripts/python.exe -m pip install -r 'F:\agent_loop\requirements.txt'

# B. 发行档（Nuitka，5~15 分钟）—— **上线必须用这套**
py -3.12 -m venv /mnt/f/.agent_loop_build/pybuild/venv-nuitka
/mnt/f/.agent_loop_build/pybuild/venv-nuitka/Scripts/python.exe -m pip install -U pip nuitka zstandard ordered-set
/mnt/f/.agent_loop_build/pybuild/venv-nuitka/Scripts/python.exe -m pip install -r 'F:\agent_loop\requirements.txt'
```

> Windows 侧的程序看不懂 `/mnt/...` 路径，喂给它们的路径要用 `F:\...` 形式。
> 打包器内部有 `win_path()` 做这层翻译，但手工执行上面的命令时要自己注意。

Nuitka 还需要一个 **C 编译器**：本机是 MSVC 14.50（"Visual Studio 生成工具 2026"）+
Windows SDK 10.0.26100。缺了就 `--compiler mingw64`（Nuitka 自动下载 winlibs，不依赖 MSVC）。

**关于版本**：venv 的解释器版本 = **玩家最终跑的解释器版本**（exe 是自包含的，玩家机器上
没有任何 Python 参与）。本机 `py -0p` 是 `3.14`（默认）+ `3.12` 两个，这里选 **3.12**：
不是因为 3.14 不被支持（PyInstaller 6.22.3 的 `Requires-Python` 是 `<3.16,>=3.8`），
而是 3.12 是被 PyInstaller hook 覆盖得最透的一档，发行包不适合压在最新的解释器上。
**Nuitka 档同理，也钉在 3.12**（`venv-nuitka` 实测 `3.12.2, MSC v.1937`）——
两个 venv 必须同版本，否则「自测通过、发行炸」这类差异又回来了。

> ⚠️ 注意这里有个**版本错位**：WSL 侧跑测试的是 `3.13.5`，而发行 exe 里是 `3.12.2`。
> 测试验的是**逻辑**，不是这个解释器组合。引入 3.13+ 独有语法/标准库行为时，测试会绿、
> 发行包会在玩家机器上炸——`match` 之类老特性无所谓，新加的 stdlib 用法要留个心。

---

## 2. exe 形态的四个坑（都踩过，别改回去）

1. **必须 `console=True`，绝不能用 `--noconsole`。** PyInstaller ≥5.7 的 `--noconsole` 会把
   `sys.stdout/stderr` 设成 `None`，日志 handler 一写就 `AttributeError` 崩在启动期。
   「玩家不该看见黑框」由 C# 侧 `Launcher` 的 `CreateNoWindow` 管——它才是父进程。
2. **`websockets` 要 `--collect-submodules`。** 它是懒加载子模块的，静态分析扫不到；
   本机可能侥幸能跑，换台机器就 `ModuleNotFoundError`。
3. **exe 是自解压的 ⇒ 绝不能靠 `__file__` 找 `config.json`/`prompts`。** 每次启动解压到随机
   `%TEMP%\_MEIxxxxxx`。数据根一律走 `AGENT_LOOP_DATA`（`paths.py` 优先读它）。
4. **onefile 运行时是「两个进程」**：bootloader 父进程解压载荷 → 起副本真正跑 Python。
   实测 `taskkill /PID <父> /F`（≈ `Process.Kill()`）**只杀父进程，子进程继续占着端口**，
   后果是「改完设置重启 → 新进程 bind 失败 → 再也连不上」。故 `Launcher.KillOwned()`
   走 `taskkill /T /F`（连子孙）。`Process.Kill(entireProcessTree)` 是 .NET Core 3.0+ 的
   API，本工程目标 .NET Framework 4.7.2，用不了。

另外：exe 只带一份**内置提示词兜底**（用户删了 `prompts/` 时仍能起来），**绝不内置 `config.json`**
——那里面是用户的 API key。

---

## 3. 可移植性：三处曾经的「本机色彩」

改造前，这个 mod 只能跑在作者那台机器上。三个真实故障点：

| 位置 | 原来 | 现在 |
|---|---|---|
| `Launcher.cs` | 写死 `F:\agent_loop\scripts\server.py`、`E:\...`、`C:\Users\<用户名>\...\python.exe` | `ModPaths` 从 **DLL 自身位置**上溯定位，兼容源码树与发行包两种形态 |
| `DiagSwitches.cs` | 10 个哨兵路径写死 `F:\agent_loop\_diag_*.txt` | `ModPaths.DiagDir`（= 数据根）动态拼接 |
| `persistence.py` / `session.py` / `agent_loop.py` | 默认值 `/home/zyh/.sessions` | `~/.sessions`（`os.path.expanduser` 展开） |
| `ModConfigFile.cs` | 从 `<Mod根>/config.json` 读端口 | 改从**数据根**读（`ModAssets/` 布局下原来会读错文件 → 退回默认端口 → 改端口即失联） |

### 两种启动形态（判据是**可执行文件名**，不是「有没有 .exe」）

| | 自包含 exe（发行版） | 解释器 + 脚本（开发机） |
|---|---|---|
| 可执行 | `<ModAssets>\AgentLoopServer.exe` | `python.exe` |
| 参数 | **一个都不传** | `-X utf8 "<源码树>\scripts\server.py"` |
| 解析结果 | `ModPaths.IsInterpreter() == false` | `== true` |

`FindPython()` 对解释器会跑 `-c "import websockets"` 探测（缺依赖的 python 会让新进程秒崩，
比「什么都没起」更难查），对自包含 exe **不探测、存在即用**：`-c` 会被它当未知参数忽略然后
一路起服务不退出，把探测拖成超时 → 好好的 exe 被判成「不可用」；而误判（冷盘 + 杀软扫描让启动
超过阈值）代价更大。要单独验 exe 用 `AgentLoopServer.exe --selftest`（自检依赖与数据根后立刻退出）。

### C# ↔ Python 环境变量契约（改名前先读这里）

| 变量 | 含义 | 消费方 |
|---|---|---|
| `AGENT_LOOP_DATA` | 用户数据根（config.json / prompts / logs 所在） | `paths.py` → `config_loader` / `log_setup` / `system_prompt` / `prompt_files` |
| `AGENT_LOOP_WS` | WS 端口（C# 从 config.json 读出后下发，**闭合了端口回环**） | `scripts/server.py` |
| `AGENT_LOOP_LOG` | 日志总闸 | `scripts/server.py` |
| `AGENT_LOOP_ROOT` | Mod 根显式覆盖（开发机用） | `ModPaths` |
| `PYTHONUTF8` / `PYTHONIOENCODING` | UTF-8 I/O | CPython |
| `PYTHONDONTWRITEBYTECODE` / `PYTHONNOUSERSITE` | 不写 pyc / 屏蔽用户 site-packages | CPython |
| `PYTHONPATH` / `PYTHONHOME` | **显式 Remove**（外部带进来的会挂错 Python 树） | — |

> **端口回环**是修掉的一个真 bug：配置面板允许改 `network.port`，而 C# 原先恒连 8766，
> 用户一改就**永久失联**，重启游戏也没用。现在 C# 读同一个 `config.json` 取端口再下发。

### 开发机怎么跑活源码

在**数据根**（`ModAssets\`，或 root 布局的 Mod 根）放一个 `_dev_root.txt`，内容一行 = 源码树路径
（`F:\agent_loop` 或 `/mnt/f/agent_loop` 都认）。此后游戏直接跑活代码、不碰随包 exe，
改完 Python 重启 Python 即生效。**这个文件绝不能进发行包**（泄漏闸已覆盖）。

> ⚠ 哨兵生效时会**跳过**自包含 exe 候选——这是有意的：作者要的是跑活源码，
> 不能让打包产物盖掉正在改的代码。

### `AGENT_LOOP_DATA` 未下发时会怎样

`paths.data_root()` 退回**包目录**——这正是开发机（源码树 == 包）与全部单测的现状，
所以 `python scripts/server.py`、`pytest` 都不需要设任何环境变量，行为一字未变。

---

## 4. 编码：中文 Windows 的必修课

游戏装在 `…\鬼谷八荒\`，中文 Windows 的 ANSI 代码页是 GBK(936)。三处必须显式：

1. **子进程 I/O**：`PYTHONUTF8=1` + `PYTHONIOENCODING=utf-8`（`Launcher.cs` 已下发；
   解释器形态另加 `-X utf8`）。不设的后果是日志乱码/inline 输出 `UnicodeEncodeError`。
2. **读用户手写文件**：`config.json` 与 `prompts/` 是玩家拿记事本/Notepad++/WPS 改的，
   编码什么都有。统一走 `textio.read_text()`（`utf-8-sig` → `gbk` → 替换），
   永不因 BOM 抛 `Expecting value: line 1 column 1`。
3. **写回**：一律 `encoding="utf-8", ensure_ascii=False`。

---

## 5. 泄漏闸（每次打包自动跑）

`pack_release.py` 在出包前扫全树，命中即**中止**。它抓到过两个真泄漏，都不是误报：

- **`.pyc` 里的 `co_filename`** —— 编译时的绝对源码路径会写进**每个 code object**。
  不处理的话 `strings` 一跑就是 `/mnt/f/agent_loop/...`。修法：`compileall(stripdir=...)`。
- **Debug 版 DLL 的 RSDS 目录** —— PE 的 CodeView 调试目录里存着 PDB 的绝对路径
  （实测 `F:\agent_loop\csharp\obj\Debug\MOD_Jgmg5L.pdb`），跟着 DLL 一起发给玩家。
  修法：Release 配置用 `<DebugType>none</DebugType>`，彻底不生成 PDB、不写 RSDS。

闸门分两档：**密钥类永不放行**（`config.json` 里带 `api_key` = 直接中止）；
路径类可用 `--allow-dev-paths` 降级为警告（只给自己迭代用，别带这个开关发包）。

> exe 里的路径是**压缩存放**的，字节级扫描扫不到（实测 `F:\agent_loop` 0 命中）——
> 但解压后仍在。**别把「扫描通过」读成「里面没有路径」。**

---

## 6. Python 源码保护：能到什么程度（如实说）

### 发行形态：**Nuitka onefile**（2026-09-19 起，唯一发行档）

> **当前决策（2026-09-19）：以后所有上线一律用 Nuitka。**
> `scripts/build_exe_nuitka.py` 是**发行路径**；PyInstaller 档
> （`scripts/AgentLoopServer.spec` + `pack_release.build_exe()`）降级为
> **本地快速自测用**（16 秒 vs 14 分钟，改提示词时仍然值），**不再对外发**。
>
> - **为什么改**：PyInstaller 的保护是「防手滑」级别 —— `pyinstxtractor` 一条命令就出 `.pyc`，
>   再上 `pycdc` / **PyLingual**（IEEE S&P 2025，公开服务）就能读回源码。
>   本项目的提示词与编排逻辑是主要工程量所在，不该让人一键抄走。
>   旧的「先不切」理由（链路没验通，换打包器会混淆归因）已过期，见 §6.3。
> - **误报代价已知并接受**：Nuitka onefile 被杀软误报的概率明显更高，机制见 §6.1，
>   本机实测见 §6.2。这是**用误报风险换源码保护**的有意取舍，不是没看见。
> - **切换不动 C#**：`ModPaths` 的判据是**文件名** `AgentLoopServer.exe`，
>   两种产物在 C# 眼里是同一种东西（已实测，见 §6.4）。

| | Nuitka（发行） | PyInstaller（仅自测） |
|---|---|---|
| 挡得住 | 玩家拿到的是机器码，**没有字节码可反** | 解压后直接翻到 `.py` |
| 挡不住 | 免费版常量仍是明文（见下） | `pyinstxtractor` → `.pyc` → `pycdc`/PyLingual |
| 定位 | **安全边界（弱）** | **防手滑** |

### 6.1 为什么 Nuitka 的杀软误报率高一个数量级

先分清两件事。**「19/71 vs 4/71」是一次 VirusTotal 数量级调研**，样本是当时某一次构建，
**本机从未用 71 引擎跑过**（§6.2 实测的只是本机 Defender 一家）——
所以别把它当精确数字引用，它的价值只在于「两边差一个数量级」这个**方向**。
但**机制是清楚的，且有硬证据** —— 触发误报的是**运行期行为**，不是文件内容。

**硬证据 ①：Nuitka 官方文档自己承认。** [Solutions to the Common Issues § Windows Virus Scanners](https://nuitka.net/user-documentation/common-issue-solutions.html) 原文：

> Some Antivirus Vendors may flag compile binaries using Nuitka's default settings on Windows as
> malware. **That happens a lot if you compile without additional steps.** You can avoid this by
> purchasing the **Nuitka Commercial** plan and following the instructions given.

官方给的解法是**买商业版**。PyInstaller 那边没有任何对应表述 —— 这个不对称本身就是信号。

**硬证据 ②：微软给出了具体判据。** [Nuitka issue #3842](https://github.com/Nuitka/Nuitka/issues/3842)
（2026-04-01 提，**至今 open**，挂在 4.1 milestone）：一个**已代码签名**的 Nuitka onefile 应用
被 Defender 判 `Trojan:Win32/Malgent!MSR`，**签名被封**。作者与微软情报部门沟通一个月后拿到的原话：

> On execution, **it drops Python files into the temporary directory along with an executable.**
> Based on this analysis, the risk rating level for these programs will **remain** at
> Trojan:Win32/Malgent!MSR with a recommended default action to users of **Quarantine**.

同帖还有一句关键限定：**"There's no such issue with programs created without the onefile option."**
—— 即 `--standalone`（目录形态）**不触发**。

所以机制是：onefile 引导器在运行期把「一个 exe + 一堆 Python 文件」释放到 `%TEMP%` 再执行。
这在行为沙箱里和 **dropper（释放器）完全同形** —— 而 dropper 是恶意软件最常见的落地手法之一。
注意微软用的是 "**remain**"：这不是等两天就自动消的临时误报，是**评级会一直挂着**。

**那 PyInstaller onefile 也释放到 `%TEMP%`，凭什么它少？** 三个不对称，按重要性排：

1. **信誉 / 流行度（最主要）。** PyInstaller 的 bootloader 是**随包发行的同一个二进制** ——
   本机实证：`venv/Lib/site-packages/PyInstaller/bootloader/Windows-64bit-intel/run.exe`
   = **311,808 B 的固定文件**，装了哪个版本的 PyInstaller 就一直是那一个，被几十万个
   已发布程序复用，厂商早把它标成 known-good。
   而 Nuitka 的 bootstrap 是**每次构建现编的**（`server.onefile-build/` 里那次 Scons 编译
   就是它），于是每个版本都是一枚**全新的、零信誉的、未签名的**二进制 ——
   偏偏又带 dropper 行为。ML 打分直接命中。
   > 直白说：PyInstaller 是「一堆人共用一张早就被认识的身份证」，Nuitka 是「每次换一张新脸」。
2. **能不能被「看进去」。** PyInstaller 归档有固定 magic（`MEI\014\013\012\013\016`）和公开的
   TOC 结构，杀软有现成的解包器，解开看到里面是 `.pyc` → 可判良性。Nuitka onefile 的载荷是
   **整块 zstd 压缩的原生代码**，没有语义解包器 → 厂商只能靠启发式猜 → 猜就保守。
3. **厂商侧的对抗投入。** PyInstaller 社区长期在跟误报斗争（有专门的
   [antivirus issue 模板](https://github.com/pyinstaller/pyinstaller/blob/develop/.github/ISSUE_TEMPLATE/antivirus.md)、
   还为了躲 YARA 规则[改过 bootloader 字符串](https://github.com/pyinstaller/pyinstaller/issues/8425)）；
   Nuitka 的官方答复是「买商业版」。

> **顺带否掉一个想当然的解释**：不是「Nuitka 加密了、熵高所以被报」。本机实测两个产物
> **都是**高熵自解压归档（PyInstaller 档整文件熵 7.995 bit/byte，15 个 1 MB 窗口里 12 个 >7.99）。
> 静态熵解释不了差异，**差异在行为与信誉**。

### 6.2 本机实测（2026-09-19，Windows Defender）

调研数字不是实测。这次用本机 Defender 的完整扫描真跑了一遍：

```bash
# 引擎 1.1.26080.3 / 特征库 1.459.267.0（实测时）
"/mnt/c/ProgramData/Microsoft/Windows Defender/Platform/4.18.26080.4-0/MpCmdRun.exe" \
    -Scan -ScanType 3 -File 'F:\.agent_loop_build\nuitka\AgentLoopServer.exe' -DisableRemediation
```

| 产物 | sha256（前 12） | 体积 | Defender 结果 |
|---|---|---|---|
| PyInstaller onefile | `11e31c4ae172` | 15,666,665 B | `found no threats` |
| **Nuitka onefile** | `c0fac7fff30e` | 18,595,840 B | **`found no threats`** |

**结论：两者在本机 Defender 下都干净。** 也就是说 —— 那条 19/71 的调研数字**在本机复现不出来**，
不能拿它当「Nuitka 一定会被报毒」的证据。但**这不等于安全**，三条理由：

1. **Defender 只是 71 家里的一家，而且是最宽容的一家。** 国内玩家机器上更常见的是
   360 / 腾讯管家，启发式比 Defender 激进得多，且**不在本机可测范围**。
2. **同一个 exe 的判定会随时间变。** [#3842](https://github.com/Nuitka/Nuitka/issues/3842) 那位
   开发者是应用**发布一段时间后**才开始被报的 —— Defender 的 ML 模型与云信誉是动态的，
   「今天干净」不构成「以后也干净」。
3. **机制仍然成立，且在本机被直接看到。** 跑 `--selftest` 时它自己打印了数据根：

   ```
   [selftest] data_root=C:\Users\<用户名>\AppData\Local\Temp\onefile_41800_491683_CaHnjNUvc3s\agent_loop
   ```

   这就是 §6.1 里微软描述的那条判据 —— **onefile 引导器把「一个 exe + 一堆文件」释放到 `%TEMP%`
   再执行**。它每次运行都发生，只是**当前**没触发那家的模型。

**处置预案（真被报了再走）**：① 玩家加白名单 / 从隔离区恢复；② 去
[微软误报提交](https://www.microsoft.com/en-us/wdsi/filesubmission)或对应厂商提交；
③ 顽固不改就退到 §6.2 末尾的 `--standalone` 备选。**别为此回退到 PyInstaller** ——
那等于用「源码一键被抄」换「少数玩家要加一次白名单」，不划算。

> **想彻底躲开误报的话有条备选路**：改用 `--standalone`（目录形态）。按 #3842 微软的原话，
> 不触发那条 dropper 判据。代价是 `ModAssets/` 下多一个文件夹、要改 C# 的路径解析。
> **本方案未评估、未实测**，仅备案。

### 6.3 为什么以前写的是「先不切」

2026-09-14 那次跑通 Nuitka 后，文档记的决策是**先不切**，理由是：
「整条创意工坊链路（编辑器导出 → 上传 → 订阅 → 玩家零安装）还没验通，
这时候换打包器，一旦出问题就分不清是链路的锅还是打包器的锅」，
并留了「等链路验通 + 杀软误报实测做完再评估」的前置条件。

2026-09-19 改为**切**：链路侧的阻塞理由已经不成立，误报侧则从「没实测」变成了
§6.1/§6.2 的已知代价 —— 权衡下来源码保护更值。

### 6.4 Nuitka 档实测详情（2026-09-14 跑通 / 2026-09-19 定为发行档）

不是纸上谈兵 —— 本机已真打出来并验证过。工具是 `scripts/build_exe_nuitka.py`
（**必须由 Windows 侧 python 执行**，原因见下面坑 6；用法写在文件头）。

```bash
cd /mnt/f/.agent_loop_build          # 别在 /home 下跑：cwd 会变成 UNC 路径，cmd.exe 不认
/mnt/f/.agent_loop_build/pybuild/venv-nuitka/Scripts/python.exe -X utf8 \
    'F:\agent_loop\scripts\build_exe_nuitka.py' --jobs 12
```

同一份源码、同一台机器的实测对比：

| | PyInstaller onefile | Nuitka onefile |
|---|---|---|
| 构建耗时 | **16 秒** | **14.4 分钟**（863 s，首次）<br>**4.9 分钟**（293 s，第二次；clcache 命中） |
| 编译规模 | — | **1263 个模块 → 1259 个 `.obj`** |
| 产物体积 | 15.7 MB | **18.6 MB**（载荷 71 MB → 压缩 25.3%） |
| 中间产物 | ~70 MB | 数 GB（`server.build/`） |
| 反编译 | `pyinstxtractor` 一条命令出 `.pyc` | **没有字节码可出** |
| 工具链依赖 | 无（纯 Python） | MSVC 14.50 + Windows SDK 10.0.26100 |
| `sys.frozen` | `True` | **`False`** ← 坑 5 |
| 需要 `taskkill /T /F` | 是 | **同样是**（实测一次杀掉 2 个进程） |
| **杀软误报** | 少（见 §6.1 三个不对称） | 机制上更高，本机 Defender 实测干净（§6.2） |

**「没有字节码可出」是实测的，不是推断**（2026-09-19 复核）：

```bash
find server.dist -name '*.py' -o -name '*.pyc' -o -name '*.pyo' | wc -l   # → 0
find server.dist -type f | sed 's/.*\.//' | sort | uniq -c | sort -rn
#   22 pyd   7 txt   7 dll   1 pem   1 md      ← 只有扩展模块与 DLL，我们的代码是机器码
```

`server.dist/agent_loop/` 下**只剩 `prompts/`**（8 个文件，与仓库逐字节相同，`cmp` 验过）——
`.py` 一个都不在里面。这正是换 Nuitka 的全部意义。

功能验证（全部通过）：

- `--selftest` → `就绪`；`data_root` / `prompts` 都解析进自解压目录，行为与 PyInstaller 版一致。
- 真起服务：日志 `WS 通道已监听 127.0.0.1:8791` → 测试客户端握手成功 →
  服务端记下 `C# 已连接（本地 :8791）` → 客户端断开也被正确识别。
- **2026-09-19 带新提示词复测一次**（隔离数据根 + 端口 8793，未碰真实存档）：
  `--selftest` → `就绪`；起服务 → `WS 通道已监听 127.0.0.1:8793`；WS `read_prompt` 取回
  `sections/tool_usage.txt` = **899 字符，与仓库 `cmp` 逐字节相同**，四条新规则
  （指人只写名字 / 逐字照抄唯一名 / 异地先 movement 本轮别再动手 / 提案线私信应法）全部在位。
  → **新提示词确实被编进了 exe**，不是只在仓库里改了。
- **2026-09-19 LLM 依赖栈构造实测**（这是 Nuitka 最该担心的一条：`openai` 是**函数体内懒导入**，
  少一个模块不会报错，只会**静默退化成 Echo 复读** —— 最难排查的一种坏）。
  用 WS `test_llm` 打一个**必然失败**的地址（`http://127.0.0.1:9/v1`，不花钱）：

  | | Nuitka | PyInstaller（对照） |
  |---|---|---|
  | `error.kind` | **`server`**（502） | **`server`**（502） |
  | 说明 | 内芯**构造成功**并真发了 HTTP、解析了响应 | 同左 |
  | 若缺模块会是 | `kind="build"`（内芯构造失败） | — |
  | 两档日志里 `ModuleNotFoundError`/`ImportError` 命中 | **0** | **0** |

  两档行为**逐字段一致** ⇒ `openai`+`httpx`+`pydantic`+`anyio`+`certifi` 整条链都编进 exe 了。

> **仍未验的**（别把上面读成「游戏里已经能跑」）：① 没跑过**真实模型对话**（打的是必败地址）；
> ② 没走过**真 C# 握手**（用的是自写 Python 冒烟客户端，不是 `MOD_Jgmg5L.dll`）；
> ③ 没在**游戏里**跑过；④ 没在**干净机器**（无 Python/无 VS）上装过；⑤ 群聊/提案/压缩
> 未在 exe 上单独验。这四条要等部署 + 开游戏才能收口。
- 18 MB 载荷的解压 + 启动：**热缓存实测 PyInstaller 1.84s / Nuitka 2.16s**
  （`--selftest` 全程，各连跑两次）。早先这里写的"15 秒内完成"是把一次 `sleep 15` 的
  **上界当成了耗时**，已更正。冷启动（首次运行 + 杀软完整扫描未签名 exe）会明显更慢，
  这一档**本机无法实测**，35 秒的启动宽限期就是为它留的（见 docs/features/brain-liveness.md §6.1）。

**Nuitka 档专属的四个坑**（承接 §2 的坑 1–4，都在这台机器上踩过）：

5. **`sys.frozen` 在 Nuitka 下是 `False`。** Nuitka 给每个被编译的模块注入 `__compiled__`，
   不设 `sys.frozen`。`scripts/server.py` 与 `scripts/chat_cli.py` 的 `_bootstrap_sys_path()`
   原本只认 `frozen` → **该守卫在 Nuitka 产物里静默失效**。当前恰好无害（自解压目录里只有
   `agent_loop/prompts/`，没有 `__init__.py`，四层候选全部落空），但那是巧合不是保证。
   已改成两个判据都认。
6. **`--include-package` 靠 `PYTHONPATH` 解析，而 WSL 的环境变量传不到 Windows 进程**
   （实测 `PYTHONPATH` 与自定义变量到了 Windows 侧**都是 `None`**）。所以构建脚本必须由
   **Windows 侧 python** 执行、在进程内部设好 env 再拉子进程。漏传时的报错是
   `FATAL: failed to locate package 'agent_loop'` —— 这句话指向 Nuitka 的包查找，
   离真因差着两层，所以脚本里加了 0.2 秒的 `import` 探针把这类失败挡在前面。
7. **`--include-package=openai` 会把它 **915 个 `.py`** 全部编译成 C**，这是 14 分钟的主要来源。
   而 `openai/__init__.py` → `_client` → `resources` 的导入链决定了躲不掉（除非改我们的调用方式）。
   `clcache` 从第二次构建起命中缓存，会明显变快。
8. **`--jobs` 别贪。** 本机 16 核 / 15.2 GB，`--jobs=12` 稳。单文件 C 编译不吃内存，
   但链接与 onefile 压缩是单线程的，堆并发在那里没有收益。

另外两条仍然成立：
- `--windows-console-mode=force` 对应坑 1：**不能**用 `disable`（同样会把 stdio 设成 `None`）。
- **免费版常量仍是明文**（Nuitka 自己的商业页承认），别把秘密写成字符串常量。
- **合法**：Nuitka 是 AGPLv3 + **Runtime Library Exception**，编译闭源产物不传染。
- 换过去之后 `ModPaths` / `Launcher` **一行都不用改**——判据是文件名 `AgentLoopServer.exe`，
  两种产物在 C# 眼里是同一种东西。

### 6.5 运维差异：09-17 那个「换 exe 换出 zlib.error」的事故，在 Nuitka 下**不会发生**

`docs/archive/2026-09-17-群聊编排/设计定稿.md` 记过一次事故：**进程活着时换掉 `AgentLoopServer.exe`**，之后凡是
「第一次走到」的功能全部炸 `zlib.error: Error -3 while decompressing data`。根因是
PyInstaller onefile 的特性 —— 纯 Python 模块**按需从 exe 内嵌归档里现读**：
`open(sys.executable)` → `seek(编译期写死的偏移)` → `zlib.decompress`。
exe 一换，偏移全错位。**只有没被 import 过的模块会中**，所以现场极像「代码 bug」。

**这个失效模式是 PyInstaller 独有的，Nuitka 下不存在。** 2026-09-19 实测：

| 步骤 | 结果 |
|---|---|
| 起 Nuitka 档服务，`read_prompt` 基线 | `ok=True`，712 B |
| **把同路径的 exe 换成另一个二进制**（PyInstaller 档，sha 前缀 `c0fac7` → `11e31c`） | 换成功 |
| 再打 8 个**该进程从未走过的**方法（`list_prompts`/`list_sessions`/`get_config`/`list_contacts`/`list_rooms`/`get_history`/2×`read_prompt`） | **7 个 `ok=True`**，1 个是正常的参数校验错（`缺少 npc_id`） |
| 日志里 `zlib`/`decompress`/`incorrect header` 命中数 | **0** |
| 两个进程（引导器 + 子进程） | 都还活着 |

原因很直接：Nuitka onefile 在**启动时一次性**把整个载荷解压到
`%TEMP%\onefile_<pid>_<rand>\`，之后进程只读那个目录，**不再碰原 exe**
（`--selftest` 打印的 `data_root` 就指着那儿）。所以原 exe 什么时候被换，跟跑着的进程无关。

> ⚠ **但「换 exe / DLL 前先停进程」这条规矩继续有效，别删。** 理由换了两条：
> ① 真正的硬约束是 **C# 侧** —— DLL 由游戏进程加载，Python 与 C# 的协议是**成对**的
>    （`create_room` 只有新 exe 认），只换一边的后果是「点了没反应」且不报错；
> ② 进程活着时换件 ⇒ **跑着的实例与盘上的产物不是同一个东西**，
>    下次重启行为突变，排障时会误判成「改了代码却没生效」或反之。
> `scripts/dev/deploy_runtime.py` 的进程闸**保留原样**，只是它现在防的是 ①②，
> 不再需要防 zlib 那条。

### 明确不要用的

| 方案 | 为什么不用 |
|---|---|
| `--noconsole`（任意打包器） | 把 `sys.stdout/stderr` 设成 `None`，本项目日志一写就崩 |
| PyArmor 9（Pro 以下） | 免费/Basic 档已被公开**静态**脱壳工具覆盖到 9.2.x（算法自 v8 起未变） |
| `pyobfuscate` / `pyminifier` | 纯化妆，字节码照样被反 |
| 自写 AES 加密 + loader | 密钥必须出现在进程里，等于没加密 |

### 顺带一句：C# 端才是软柿子

.NET 程序集用 dnSpy/ILSpy **几秒钟**还原成接近原始的 C#。真要藏东西，藏在 Python 侧，
C# 侧只留协议胶水。**永远不要把 API key 写进 DLL 或 Python 常量。**

---

## 7. 许可与合规

- **必须随分发保留 CPython 的 PSF 许可声明**。旧形态靠 `AgentLoop/LICENSE.txt` 满足；
  改成 exe 后解释器被打进 exe 里，**没有天然的落点**——所以打包器会往 `ModAssets/` 里写
  一份 `开源许可.txt`（列明各组件与许可，并附随包依赖的 LICENSE 原文）。
- 随包分发的依赖：`websockets`(BSD)、`openai`(Apache-2.0)、`httpx`/`anyio`/`h11`/`certifi`/
  `sniffio`/`distro`(MIT/BSD)、`pydantic`(MIT)、`pydantic-core`/`jiter`(MIT)、
  `typing-extensions`(PSF)、`tqdm`(MIT)。
- 游戏 mod 圈的惯例做法：在说明里写明**禁止转载/二次分发**——这是你唯一的法律抓手。

---

## 8. 发之前过一遍

- [ ] `dotnet build -c Release` 成功且 DLL 时间是新的
- [ ] **exe 是 Nuitka 档**：跑完 `build_exe_nuitka.py`，且 `--exe` 指向
      `F:\.agent_loop_build\nuitka\AgentLoopServer.exe`（**不是** `pybuild\dist\` 那个）
- [ ] `python3 scripts/pack_release.py --exe <nuitka产物> --install-to-project` 的**泄漏扫描通过**
- [ ] 包里 `config.json` 的 `api_key` 是**空的**
- [ ] `config.json` 与 `prompts/` 在 `ModAssets/` 作为**出厂模板**（用户数据在 `%USERPROFILE%\.agent_loop`）
- [ ] 没有 `.bak` / `portrait_cache` / `logs` / `_dev_root.txt` / `_diag_*.txt` 被打进去
- [ ] `ModCode/` 下**只有** `MOD_Jgmg5L.dll` 一个 dll（官方文档明写「不要有多余的DLL」）
- [ ] **杀软自查**：`MpCmdRun.exe -Scan -ScanType 3 -File <exe>` 干净（§6.2 的命令）
- [ ] 找一台**没装过 Python** 的机器（或干净 Windows 沙箱）试跑一次
- [ ] 首次启动填 key → 开对话 → 能正常回话（不是复读）
- [ ] 报障指引指向 `%USERPROFILE%\.agent_loop\logs\agent_loop.log`，且**写明被杀软拦截时怎么办**

---

## 9. 排障入口

| 现象 | 先看 |
|---|---|
| AI 只会复读玩家的话 | `config.json` 的 `llm` 块没填。日志里有一条显式 WARNING 点名缺哪项 |
| mod 装了但毫无反应 | `logs/agent_loop.log` 里 `[Launcher]` 那一行（打印了 root/来源/数据根/入口四项解析结果） |
| exe 到底能不能跑 | 在 `ModAssets/` 下开命令行：`AgentLoopServer.exe --selftest` |
| 面板开不出来 | `ModRes/AssetBundle/**/*.ab` 在不在；`[ModAbRes]` 日志 |
| 改端口后连不上 | 现在会自动跟随 `config.json`；若仍异常看 `[WsClient] starting, url=` |
| **杀软报毒 / exe 被删** | Nuitka onefile 的**已知代价**，机制见 §6.1。处置顺序：① 让玩家加白名单/恢复隔离区；② 去对应厂商提交误报（Defender：<https://www.microsoft.com/en-us/wdsi/filesubmission>）；③ 若某家顽固不改，考虑 §6.2 末尾的 `--standalone` 备选。**先确认日志里 exe 是被删了还是启动失败**——两者现象都是「没反应」 |
| 想二分定位是哪个部件出问题 | 在**数据根**放 `_diag_no_*.txt` 哨兵（见 `DiagSwitches.cs` 顶部清单），**运行中增删即时生效、免重启** |
