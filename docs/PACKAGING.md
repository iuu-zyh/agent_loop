# 打包与分发（2026-09-13 改版：Python 侧 = 一个自包含 exe）

本文回答三件事：**怎么打出能给别人用的包**、**路径为什么这么排**、**Python 源码能保护到什么程度**。

---

## 0. 形态一句话

Python 侧**不再是「便携 CPython + 源码目录树」**，而是**一个 15 MB 的自包含 `AgentLoopServer.exe`**
（PyInstaller onefile，内含解释器 + 我们的全部代码 + websockets/openai 及依赖）。

```
Mod_Jgmg5L/
├── ModAssets/                       ★ 官方「自带文件」槽位，编辑器「导出模组」逐字节原样带走
│   ├── AgentLoopServer.exe          Python 侧全部（15 MB，一个文件）
│   ├── config.json                  ★ 用户填 API key（由 config.example.json 生成，空 key）
│   ├── prompts/                     ★ 用户可编辑提示词
│   └── logs/                        运行时生成
├── ModCode/dll/MOD_Jgmg5L.dll       C# 端（游戏只枚举这一层）
├── ModRes/AssetBundle/              UI 预制体 AB
├── ModExcel/                        剧情配置壳表
├── ModExportData.cache              编辑器生成，游戏按它识别 mod
└── 安装说明.txt
```

**为什么数据与 exe 同在 `ModAssets/`**：`ModAssets\` 是官方编辑器唯一保证会被带进发行包的槽位
（md5 实证：工程内文件与导出产物逐字节相同）。参考实现：神识传音（MOD_9CiDqJ）把加密程序集与
提示词 JSON 全放在 `ModAssets\` 下，loader DLL 放 `ModCode\dll\`——与本 mod 同一套路数。

---

## 1. 一条命令

```bash
# ① 编 Release 版 DLL（打包器只收 Release，原因见 §5）
"/mnt/c/Program Files/dotnet/dotnet.exe" build csharp/AgentLoopBridge.csproj -c Release

# ② 重建 exe + 铺进模组编辑器工程（默认就是这个流程，26 秒）
python3 scripts/pack_release.py --build-exe --install-to-project
#    然后：打开模组编辑器 → 点「导出模组」→ 得到完整 mod

# ③ 或者直接装进游戏目录试跑（不动编辑器工程）
python3 scripts/pack_release.py --deploy
```

| 开关 | 用途 |
|---|---|
| `--build-exe` | 先跑 PyInstaller 重打 exe（约 16 秒）。**改了 Python 代码就要带它** |
| `--exe <路径>` | 用已有的 exe（默认取上次构建产物） |
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
py -3.12 -m venv /mnt/f/.agent_loop_build/pybuild/venv
/mnt/f/.agent_loop_build/pybuild/venv/Scripts/python.exe -m pip install -U pip pyinstaller
/mnt/f/.agent_loop_build/pybuild/venv/Scripts/python.exe -m pip install -r 'F:\agent_loop\requirements.txt'
```

> Windows 侧的程序看不懂 `/mnt/...` 路径，喂给它们的路径要用 `F:\...` 形式。
> 打包器内部有 `win_path()` 做这层翻译，但手工执行上面的命令时要自己注意。

**关于版本**：venv 的解释器版本 = **玩家最终跑的解释器版本**（exe 是自包含的，玩家机器上
没有任何 Python 参与）。本机 `py -0p` 是 `3.14`（默认）+ `3.12` 两个，这里选 **3.12**：
不是因为 3.14 不被支持（PyInstaller 6.22.3 的 `Requires-Python` 是 `<3.16,>=3.8`），
而是 3.12 是被 PyInstaller hook 覆盖得最透的一档，发行包不适合压在最新的解释器上。

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
| `Launcher.cs` | 写死 `F:\agent_loop\scripts\server.py`、`E:\...`、`C:\Users\iu\...\python.exe` | `ModPaths` 从 **DLL 自身位置**上溯定位，兼容源码树与发行包两种形态 |
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

### 当前形态：PyInstaller onefile

| 挡得住 | 挡不住 |
|---|---|
| 玩家解压后直接翻到 `.py` | `pyinstxtractor` 一条命令解出 `.pyc` |
| 顺手复制走 / 改一行就转发 | 再上 `pycdc`（啃到 ~3.11）、**PyLingual**（IEEE S&P 2025，公开服务） |

**结论：这是「防手滑」级别，不是安全边界。** 选它是因为它**快**（16 秒出包）且整条链路
（编辑器导出 → 工坊 → 玩家零安装）能一次跑通；把链路验通之后再换 Nuitka 才划算。

### 升级到真保护：Nuitka（2026-09-14 **已实测跑通**）

> **当前决策（2026-09-14）：先不切。** 发行档仍是 PyInstaller，Nuitka 作为**已验证的备用档**
> 留在 `scripts/build_exe_nuitka.py`，**不接入 `pack_release.py`**。
> 理由：整条创意工坊链路（编辑器导出 → 上传 → 订阅 → 玩家零安装）还没验通，
> 这时候换打包器，一旦出问题就分不清是链路的锅还是打包器的锅。
> 等链路验通、且**杀软误报实测**做完（调研数字 Nuitka ~19/71 vs PyInstaller ~4/71，
> 本机未实测），再回来做这个切换。
> **切的时候不需要动 C#**：判据是文件名，两种产物在 `ModPaths` 眼里是同一种东西。

不是纸上谈兵了 —— 本机已真打出来并验证过一遍。工具是 `scripts/build_exe_nuitka.py`
（**必须由 Windows 侧 python 执行**，原因见下面坑 6；用法写在文件头）。

```bash
cd /mnt/f/.agent_loop_build          # 别在 /home 下跑：cwd 会变成 UNC 路径，cmd.exe 不认
/mnt/f/.agent_loop_build/pybuild/venv-nuitka/Scripts/python.exe -X utf8 \
    'F:\agent_loop\scripts\build_exe_nuitka.py' --jobs 12
```

同一份源码、同一台机器的实测对比：

| | PyInstaller onefile | Nuitka onefile |
|---|---|---|
| 构建耗时 | **16 秒** | **14.4 分钟**（863 s） |
| 编译规模 | — | **1263 个模块 → 1266 个 `.obj`** |
| 产物体积 | 15.4 MB | **18.2 MB**（载荷 71 MB → 压缩 25.3%） |
| 中间产物 | ~70 MB | 数 GB（`server.build/`） |
| 反编译 | `pyinstxtractor` 一条命令出 `.pyc` | **没有字节码可出** |
| 工具链依赖 | 无（纯 Python） | MSVC 14.50 + Windows SDK 10.0.26100 |
| `sys.frozen` | `True` | **`False`** ← 坑 5 |
| 需要 `taskkill /T /F` | 是 | **同样是**（实测一次杀掉 3 个进程） |

功能验证（全部通过）：

- `--selftest` → `就绪`；`data_root` / `prompts` 都解析进自解压目录，行为与 PyInstaller 版一致。
- 真起服务：日志 `WS 通道已监听 127.0.0.1:8791` → 测试客户端握手成功 →
  服务端记下 `C# 已连接（本地 :8791）` → 客户端断开也被正确识别。
- 18 MB 载荷的解压 + 启动：**热缓存实测 PyInstaller 1.84s / Nuitka 2.16s**
  （`--selftest` 全程，各连跑两次）。早先这里写的"15 秒内完成"是把一次 `sleep 15` 的
  **上界当成了耗时**，已更正。冷启动（首次运行 + 杀软完整扫描未签名 exe）会明显更慢，
  这一档**本机无法实测**，35 秒的启动宽限期就是为它留的（见 docs/brain-liveness-design.md §6.1）。

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
- [ ] `python3 scripts/pack_release.py --build-exe` 的**泄漏扫描通过**
- [ ] 包里 `config.json` 的 `api_key` 是**空的**
- [ ] `config.json` 与 `prompts/` 在 `ModAssets/`（不在 Mod 根层）
- [ ] 没有 `.bak` / `portrait_cache` / `logs` / `_dev_root.txt` / `_diag_*.txt` 被打进去
- [ ] `ModCode/` 下**只有** `MOD_Jgmg5L.dll` 一个 dll（官方文档明写「不要有多余的DLL」）
- [ ] 找一台**没装过 Python** 的机器（或干净 Windows 沙箱）试跑一次
- [ ] 首次启动填 key → 开对话 → 能正常回话（不是复读）
- [ ] 报障指引指向 `ModAssets/logs/agent_loop.log`

---

## 9. 排障入口

| 现象 | 先看 |
|---|---|
| AI 只会复读玩家的话 | `config.json` 的 `llm` 块没填。日志里有一条显式 WARNING 点名缺哪项 |
| mod 装了但毫无反应 | `logs/agent_loop.log` 里 `[Launcher]` 那一行（打印了 root/来源/数据根/入口四项解析结果） |
| exe 到底能不能跑 | 在 `ModAssets/` 下开命令行：`AgentLoopServer.exe --selftest` |
| 面板开不出来 | `ModRes/AssetBundle/**/*.ab` 在不在；`[ModAbRes]` 日志 |
| 改端口后连不上 | 现在会自动跟随 `config.json`；若仍异常看 `[WsClient] starting, url=` |
| 杀软报毒 | 自解压 exe 的固有代价（见 §6）。加白名单或去杀软官网提交误报 |
| 想二分定位是哪个部件出问题 | 在**数据根**放 `_diag_no_*.txt` 哨兵（见 `DiagSwitches.cs` 顶部清单），**运行中增删即时生效、免重启** |
