# 开发规范（DEV_RULES）

> 本文件是**唯一**的开发规范来源。`AGENTS.md` 只做入口。
> 文档索引见 [`README.md`](README.md)；测试细节见 [`../tests/README.md`](../tests/README.md)。

---

## 一、开发流程（五步）

```
① 开工   →  docs/journal/YYYY-MM-DD-功能名/
② 开发   →  只跑受影响测试；判定与副作用分文件
③ 验证   →  需要实机的必须实机
④ 收尾   →  按 §十二 清单逐项过
⑤ 沉淀   →  结论升格进 docs/features/
```

### ① 开工

每次开发都建 `docs/journal/2026-11-19-功能名/`，至少一个 `README.md`，边做边记。
过程详文（日志解读、弯路、被推翻的假设、探针输出）都放这个文件夹。

### ② 开发：判定与副作用分开

| 层 | 放什么 | 例子 |
|---|---|---|
| 判据核 | 纯逻辑，零 Unity 依赖，可单测 | `state/BusyWatchCore.cs` |
| 接线层 | 碰 Unity / 打日志 / 注册回调 | `state/BusyWatch.cs` |
| 单测 | net8.0 控制台工程 | `csharp/tests/BusyWatchCoreTests/` |

纯逻辑必须单独成文件。一个文件里不混「判定」和「副作用」。

### ③ 验证

改完先自问：这个行为能不能只靠单测证明？

- 能（纯逻辑、协议解析、状态机）→ 单测覆盖
- 不能（碰 `g.world` / `g.ui` / 真实游戏循环）→ **必须实机验证**

实机验证过才算完成。

### ④ 收尾

见 §十二，强制清单。

### ⑤ 沉淀

`journal` 里有长期价值的结论 → `docs/features/<功能>.md`；过程 → `docs/archive/`（命名见 §八）。

```
journal/（进行中）→ archive/journal/（归档）→ features/（现状）
```

---

## 二、测试

> 完整说明见 [`tests/README.md`](../tests/README.md)。这里只讲什么时候跑什么。

### 全量只在收尾跑

```bash
python3 -m pytest tests/ -q                    # 约 2.8 分钟 —— 收尾才跑
```

平时只跑受影响部分：

```bash
python3 scripts/dev/run_tests_for.py --changed                # 推荐
python3 -m pytest tests/test_xxx_contract.py -q               # 单文件
python3 -m pytest tests/ -q -k "frame or tick or battle"      # 按关键字
```

### 验证强度匹配改动性质

| 改动性质 | 要求 |
|---|---|
| 探针 / 诊断代码 | 编译 → 部署 → 上机取数。**不写契约、不跑全量** |
| 定案改逻辑 | 契约测试 + 受影响子集（收尾时全量） |

### 每条真机事故配一条契约测试

凡是「某行注册 / 某处接线被删掉也不报错」的地方，都要有测试按字面断言它还在。

```python
# tests/test_xxx_contract.py
def test_resident_callback_is_registered():
    assert re.search(r"FrameTicker\.Ensure\(...", MODMAIN_SRC)
```

### 断言要变异验证

写完断言要证明它会红：删掉实现跑一遍，必须失败。

```bash
python3 scripts/dev/mutate_check_*.py
```

### C# 测试

```bash
python3 scripts/dev/run_csharp_tests.py                        # 全部工程
python3 scripts/dev/run_csharp_tests.py --only FrameTickerCoreTests
```

---

## 三、测试快照

`run_tests_for.py` 靠 `scripts/dev/.depmap.json` 挑子集。新增 / 修改 / 删除文件后快照会过期。

| 情形 | 要做什么 |
|---|---|
| 新增源文件 | 无需处理 |
| 新增 / 修改测试文件 | 想加速就增量采 |
| **删除 / 重命名源文件** | **必须重采** |

```bash
python3 scripts/dev/run_tests_for.py --probe                                  # 全量
python3 scripts/dev/run_tests_for.py --probe --probe-files tests/test_new.py  # 增量
```

收尾时动过文件结构（增删改名）必须重采。

---

## 四、构建与部署

### 编译

```powershell
dotnet build csharp\AgentLoopBridge.csproj -c Release
```

### 部署：三个位置

```powershell
$src = "F:\agent_loop\csharp\bin\Release\MOD_Jgmg5L.dll"
Copy-Item $src "E:\SteamLibrary\steamapps\common\鬼谷八荒\ModExportData\Mod_Jgmg5L\ModCode\dll\MOD_Jgmg5L.dll" -Force
Copy-Item $src "F:\mod\ModProject_Jgmg5L\ModProject\ModCode\dll\MOD_Jgmg5L.dll" -Force
Copy-Item $src "F:\mod\ModProject_Jgmg5L\ModProject\ModCode\ModMain\bin\Release\MOD_Jgmg5L.dll" -Force
```

| 位置 | 谁用 |
|---|---|
| 游戏目录 `ModCode/dll/` | 运行时加载的那份 |
| 编辑器 `ModCode/dll/` | 编辑器导出时打包的那份 |
| 编辑器 `ModMain/bin/Release/` | 编辑器工程编译输出 |

三处必须 md5 一致，收尾报告写 md5。改了 C# 必须重启游戏。

### 部署完就结束

部署命令退出码正常、md5 打出来、备份存在 —— 到此为止。

不再做：解包 exe 查 code object、`strings` / `grep` 查 DLL 符号、重跑 `deploy_runtime.py --check`、
抽查部署后的时间戳 / 大小、为「确认生效」补一条核对命令。

判据：部署看 `deploy_runtime.py` 的退出码 + md5 输出；生效看实机日志 / 行为。

### Python 侧：改源码必须重新打包

游戏默认跑的是 `ModAssets/AgentLoopServer.exe`，不是源码。改完 `agent_loop/` 里任何 `.py`
不重新打包，游戏里跑的还是旧代码。开发用 PyInstaller，发布用 Nuitka。

### 两档打包

| 场景 | 用什么 | 命令 | 耗时 |
|---|---|---|---|
| 开发 / 自测 | **PyInstaller** | `--build-exe` | 约 16 秒 |
| 正式发布 | **Nuitka** | 见 §十 / `release/PACKAGING.md` | 5~15 分钟 |

两档产物同名、同位置、同启动契约（`ModPaths` 只认文件名 `AgentLoopServer.exe`）。

- **发行必须 Nuitka，禁止用 PyInstaller 出包。**
- **禁止源码树模式**（`_dev_root.txt` 哨兵）。本项目只以打包形式跑 Python。

### 打包与部署

```bash
python3 scripts/pack_release.py --build-exe \
    --exe '/mnt/f/.agent_loop_build/pybuild/dist/AgentLoopServer.exe' --deploy
```

必须显式 `--exe` 指向刚打的包。

### 三个坑

**① `--deploy` 默认从 `stage/` 取，不是刚打的包**
`--build-exe` 的产物在 `../.agent_loop_build/pybuild/dist/`，`--deploy` 默认从
`../.agent_loop_build/stage/` 取。必须显式 `--exe` 指过去。

**② exe 被占用时 `--deploy` 直接 `PermissionError`**

```powershell
Stop-Process -Name AgentLoopServer -Force
Get-Process -Name AgentLoopServer      # 应当为空
```

onefile 在磁盘是一个文件、运行时两个进程，按名字杀能一并清掉。

**③ `[Build]` 那行只验证 DLL，不验证 exe**
Python 的判据是 exe 时间戳，或日志里新增的字段 / 文案。

### 参数被接受 ≠ 新代码在跑

判据必须是「新增的东西出现了」，不是「旧行为还在」。改协议时盯新增字段，不盯新增参数。

---

## 五、代码约定

### 注释写「为什么」，不写「做了什么」

```csharp
// ✗ 代码已经说了
// 遍历 keys 并调用 Stop

// ✓ 决定背后有代价
// 为什么必须主动 Stop，而不是等场景销毁：真机上 Destroy ok 之后体检表还在继续打。
// 详见 docs/archive/2026-11-19-常驻帧回调句柄化.md
```

### 注释边界

| 内容 | 该在哪 | 代码里留什么 |
|---|---|---|
| 真机日志逐行解读、时间线 | `docs/archive/` | 一行指针 |
| 被推翻的假设、「第 N 次尝试」 | `docs/archive/` | 一行指针 |
| 反编地址、字段偏移、分派路径 | `docs/reference/` | 一行指针 |
| 改动沿革 | 归档或 `CHANGELOG.md` | 一行指针 |
| 反直觉决定的原因 | 代码 | 完整写 |
| 常量为什么是这个值 | 代码 | 完整写 |
| 顺序为什么不能换 | 代码 | 完整写 |
| 这里静默失败过，别改回去 | 代码 | 完整写 + 文档指针 |

判据：把这段注释删掉，代码还能不能改对？

- 能 → 写的是过程，搬走，留一行 `// 详见 docs/archive/xxx.md`
- 不能 → 写的是约束，留下，写多长都行

不设行数上限，看内容不看行数。

禁止新写：日期开头的叙事（「09-23 改动如下」）、多行 ASCII 时间线、`Player.log` 行号摘抄。

### 旧代码不清

- 不做批量清理。
- 改功能时不顺手整理旁边的历史注释。
- 例外：确认它已经说错了（指向不存在的文件、描述已删除的机制），那就改对。

### 禁止出现的形状

| 形状 | 改用 |
|---|---|
| 全进程一次的布尔守「每世界都要重做」的动作 | 句柄 + 帧号心跳（`FrameTicker`） |
| 裸 `g.timer.Frame(体, 1, true)` 不接返回值 | `FrameTicker.Ensure(键, 名, 体)` |
| `On` 订阅了不退订 | `Destroy` 里 `Off` + `Init` 注册前先 `Off` |
| `x as RectTransform` | `UiRects.Of(...)` / `GetComponent<RectTransform>()` |
| 后台线程碰 `UnityEngine` | `ModMain.P`（内部按线程分流） |

### 日志

- 统一走 `ModMain.P(...)`，不直接调 `Debug.Log`。
- 一行说清「发生了什么 + 依据」。
- 主线程 → `Player.log`；后台线程 → `MelonLoader/Logs/AgentLoopBridge-thread.log`。

### Python 侧

- docstring 一句话 + 为什么，不写设计长文。
- `storage/images.py` 文件头约 20 行是上限样板，不是起点。

---

## 六、环境路径

| 项 | 路径 |
|---|---|
| 本仓库 | `F:\agent_loop` |
| 游戏 mod 目录 | `E:\SteamLibrary\steamapps\common\鬼谷八荒\ModExportData\Mod_Jgmg5L` |
| 编辑器工程 | `F:\mod\ModProject_Jgmg5L\ModProject` |
| C# 日志 | `C:\Users\<用户名>\AppData\LocalLow\guigugame\guigubahuang\Player.log` |
| 用户数据根 | `C:\Users\<用户名>\.agent_loop`（config / prompts / logs / 窗口位置 / 公告水位 / 剧情账本） |
| Python 日志 | `C:\Users\<用户名>\.agent_loop\logs\agent_loop.log` |
| 反编译素材 | `reference/`（必留，多个源码与测试引用它） |

`Player.log` 是 Unity 的；`MelonLoader/Latest.log` 没有我们的行。

---

## 七、项目结构与分工

```
┌─ 游戏进程（IL2CPP / Unity）────────┐        ┌─ Python 进程 ─────────┐
│  csharp/  MelonLoader + Harmony   │        │  agent_loop.py 装配    │
│    ├ app/     入口、帧回调、日志    │  ws    │    ├ agent/   对话引擎  │
│    ├ drama/   剧情/战斗/队列        │◄──────►│    ├ tools/   游戏工具  │
│    ├ state/   世界状态闸            │  8766  │    ├ room/    群聊      │
│    ├ UI/      游戏内面板            │ 单连接  │    ├ llm/     模型客户端 │
│    └ tools/   动作执行              │ 全双工  │    └ storage/ 落盘      │
└───────────────────────────────────┘        └───────────────────────┘
```

**分工**：Python 是大脑（决策「说什么、做什么」），C# 是双手（真正碰 `g.world` / `g.conf`）。
任何触及游戏数据的调用**必须在主线程**，后台线程只入队。

---

## 八、文档结构

目录与每层职责见 [`README.md`](README.md)。

### 命名规则

- `archive/` 与 `journal/` 的子条目一律日期开头：`YYYY-MM-DD-功能名`
- 一件事写一份 → `<日期>-<事>.md`；一件事写多份 → `<日期>-<事>/` 目录
- 目录内文件名用描述性中文，不用 `01-` `02-` 编号（除非有真实阅读顺序）
- `features/` `reference/` 用功能名 / 主题名，不加日期

### 文档不许出现

- 裸的过期状态行（「状态：待真机验收」）—— 状态归 `CHANGELOG.md`
- 断链 —— 移动文件后校验相对链接
- 「待补充」「TODO」

---

## 九、并行开发须知

- `git status` 里未跟踪的 `csharp/**`、`agent/**` 文件可能是别人正在写的，**不要删、不要格式化**。
- 编译会把工作区**所有**改动编进 DLL；只验证自己那块用 `git stash push -u -- <别人的文件>`。
- 删任何目录前，先确认解析到的绝对路径就是你想删的那个。
- 判断某条测试红是不是自己弄的：`git stash push -u -- <可疑文件>` 后再跑，变绿就是别人留下的。
- 大范围改路径 / 重命名前，先 `grep` 统计影响面。

---

## 十、发布

发行档是 **Nuitka 打的单文件 `AgentLoopServer.exe`**，不是源码。
开发过程不要用 Nuitka（5~15 分钟），一律用 PyInstaller（16 秒），规则见 §四。

- 打包脚本：`scripts/pack_release.py`（**白名单式**，只收根目录 `*.py` + 列名子包 + `prompts`）
- **`config.json` 含真实 API key，永不进包**；发布只给 `config.example.json`
- 导出到工坊前要在编辑器里改 **workshop 版本号**
- 详见 [`release/PACKAGING.md`](release/PACKAGING.md)

---

## 十一、排查顺序

> 只在已经出问题时用。正常部署完不做这些（见 §四「部署完就结束」）。

1. **确认跑的是新代码**
   - C#：看 `[Build]` 行的「写入 vs 进程启动」判定
   - Python：`[Build]` 无效，看 exe 时间戳或日志里新增的字段 / 文案
2. **确认挂钩上了**：看各组件的 `InstallReport`
3. **确认 tick 在跑**：`常驻帧回调（60 帧后）：… idle=N`，个位数才正常
4. **看量值**：`[TickProfiler]` 每 5 秒一行，`n` 与 `帧=+N` 应当一比一
5. **再读业务日志**

---

## 十二、收尾（强制清单）

> 做完一件事，必须按顺序过这张表。缺一项不算做完了。

### 12.1 清单

| # | 动作 | 判据（做不到就是没做完） |
|---|---|---|
| 1 | **判据归核** | 新增判定逻辑在 `*Core.cs` / 纯函数里，能在 `csharp/tests/` 或 `tests/` 单独跑 |
| 2 | **补契约测试** | 每条「别退回去」的结论都有断言；断言**做过变异验证**（删实现要变红） |
| 3 | **跑定向测试** | `run_tests_for.py --changed` 全绿。**不全量**（全量时机见 12.2） |
| 4 | **真机验证** | 需要实机的必须跑过。**没读到的写进「未验证」，不写护栏** |
| 5 | **探针清理** | 一次性探针删掉；要留的写明「留到什么时候、删之前先看哪条」 |
| 6 | **注释体检（只看新写的）** | 新增注释按 §五：写「为什么」不写过程。**不动存量** |
| 7 | **更新 `features/`** | 现状变了就改对应那节。**不许只改代码不改文档** |
| 8 | **归档过程** | 中途记录 / 被推翻的结论 / 真机时间线 → `archive/<日期>-<事>.md`（§八） |
| 9 | **写 `CHANGELOG.md`** | 追加一条，**最新在最上面**。写「为什么改」和「验证到哪」 |
| 10 | **清 journal** | 详文移入 `archive/journal/`，子文件夹只留 `README.md` |
| 11 | **重采快照** | 动过文件结构（增删改名）就必须 `run_tests_for.py --probe` |
| 12 | **全量测试** | `pytest tests/ -q` 全绿 + `run_csharp_tests.py` 全绿 |
| 13 | **链接校验** | 移动过文档就跑一遍相对链接检查，**0 断链** |
| 14 | **构建部署** | `dotnet build -c Release` 0 错误 → 拷三处 → **三处 md5 一致**。**部署完即结束** |
| 15 | **git 提交与推送** | 一功能一提交，信息写「为什么」；推送前 `check_no_secrets.py` 必须 0 命中（12.5） |
| 16 | **报告** | 改了什么 / 验证到哪 / **没验证的是什么** / 下一步要他做什么 |

### 12.2 只在收尾做的事

```bash
python3 -m pytest tests/ -q                      # 全量
python3 scripts/dev/run_csharp_tests.py          # C# 全部工程
python3 scripts/dev/run_tests_for.py --probe     # 重采测试快照
```

平时不做这三件。

### 12.3 三条铁律

1. **没真机读数的结论不写进护栏。** 没读到就写「未验证」，等读到了再写护栏。
2. **文档里不许留「待验证」状态行。** 要么写清「验证什么、怎么验」，要么标上日期和「之后发生了什么」。
3. **提交前 grep 一遍临时物。**

```bash
grep -rn 'TODO\|FIXME\|临时\|PROBE\|XXX\|调试用' \
     --include='*.cs' --include='*.py' csharp/ agent/ room/ tools/ | grep -v tests/
```

### 12.4 收尾报告模板

```
## 改了什么
<按模块列，不按文件列>

## 验证到哪
- 单测：N 条全绿（列出新建的契约测试）
- 真机：<读了哪行日志、值是多少>
- 构建：md5 <值>，三处一致

## 没验证的
<明确列出。没验证就说没验证>

## 下一步需要你做的
<重启游戏 / 点一次某功能 / 导出配表 / 改版本号>
```

「验证到哪」和「没验证的」缺一不可。

### 12.5 git：提交、推送、外来贡献

**仓库只有一个**：<https://github.com/iuu-zyh/agent_loop>，默认分支 `full-source`。
不要另开「干净副本」再推。

**维护者 / 协作者（有 Write 权限，可直接推 `full-source`）**

```bash
git status                                # 必须干净
python scripts/dev/check_no_secrets.py    # 必须 0 命中
git add -A
git diff --cached --stat
git commit -m "<类型>: <为什么>"
git push                                  # master 已跟踪 origin/full-source
```

一功能一提交；提交信息写「为什么」，不写「改了哪几行」。

加人：仓库 Settings → Collaborators → Add people → 角色选 **Write**（不要 Maintain，它还能改仓库设置和 Webhook）。

`full-source` 没有分支保护和 CI，分支头等于可发布状态，全靠下面四条自律：

- 推之前先跑 `python3 scripts/dev/run_tests_for.py --changed`，红的不要推
- **不许 `git push --force`**，不许删 `full-source`
- **不许 `git add -f`** 绕过 `.gitignore`，尤其是 `config.json`
- 没验证的部分在提交信息里写明「没验证什么」

**外部贡献者（没有写权限）**

1. GitHub 上 **Fork**
2. clone 自己的 fork，**新建分支**再改
3. 提交并推到自己 fork 的分支
4. 向 `iuu-zyh/agent_loop` 的 `full-source` 发 **Pull Request**
5. 等待维护者审阅合并

不要尝试直接推原仓库，也不要另建一份历史让维护者 force push 覆盖。

**永远不入库（已在 `.gitignore`，别用 `-f` 绕过）**

| 文件 | 原因 |
|---|---|
| `config.json` | 本机真实配置，含 API key |
| `dist/` | 打包产物 |
| `.depmap.json` / `.test_durations.json` / `.pytest_last_subset_run` | 本机测试缓存 |
| `debug-*.md` | 临时排查记录，结论进 `archive/` 后再删 |

**行尾**：`* text=auto` + 二进制标记，索引统一 LF，Windows 工作区 CRLF。
看到「整文件改动」先跑 `git diff --ignore-cr-at-eol -- <file>`；为空就是行尾，不要手改。
