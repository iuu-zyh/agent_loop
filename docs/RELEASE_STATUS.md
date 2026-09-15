# 发行进度与待办（2026-09-14 起）

> **这份文档是"现在做到哪了"的唯一出处。** 与 [PACKAGING.md](PACKAGING.md) 分工：
> PACKAGING.md 回答**怎么做**（命令、布局、原理、保护、许可），本文回答**做到哪了、还差什么**。
> 每完成一项就改这里；本文件不写"计划中的设计"，只写**已实证的事实**和**明确的待办**。

---

## 一、目标

把一个**自建的 NPC 对话系统**做成**创意工坊式的 mod**：玩家订阅/拷贝即用，**不需要装 Python、不需要 pip**。

交付形态（用户拍板）：

```
Mod_Jgmg5L/
├── ModAssets/                       ★ 官方「自带文件」槽位，编辑器导出时逐字节原样带走
│   ├── AgentLoopServer.exe          Python 侧全部 = 一个 15.4 MB 的自包含 exe
│   ├── config.json                  ★ 玩家填 API key
│   ├── prompts/                     ★ 玩家可编辑提示词
│   └── 开源许可.txt                 合规文件（PSF / Apache-2.0 原文）
├── ModCode/dll/MOD_Jgmg5L.dll       C# 侧（游戏只枚举这一层的 dll）
├── ModRes/AssetBundle/**/*.ab       UI 预制体（14 个）
├── ModExcel/*.json                  剧情配置壳表
└── ModExportData.cache              编辑器生成，游戏按它识别 mod
```

**架构**：C# 是双手（Harmony/Il2Cpp 钩进游戏），Python 是大脑（对话/工具决策），两侧走
`127.0.0.1` 上的单 WebSocket，C# 作客户端、Python 作服务端，C# 负责把 Python 拉起来。

---

## 二、已实证的事实（别再重复验证）

| 事实 | 证据 |
|---|---|
| 打包形态**在真游戏里跑通了** | 真机会话日志：`[Launcher] 已拉起 …AgentLoopServer.exe（自包含 exe，无脚本参数）` → 0.25 秒后 `C# 已连接` → 读档进世界 |
| exe 启动开销可忽略 | 从 `已拉起` 到 `C# 已连接` **0.25 秒**；onefile 首跑解压 15MB 也没拖慢 |
| **编辑器「导出模组」会搬** `ModAssets/`、`ModCode/dll/`、`ModRes/AssetBundle/` | 2026-09-14 00:49 那次导出逐项核对通过（14 个 .ab、DLL md5 与 Release 一致、exe md5 与构建产物一致） |
| 编辑器取 DLL **只认 `ModCode/dll/`** | 导出产物里**没有** `ModCode/ModMain/` 任何东西 → 往 `ModMain/bin/Release/` 铺副本是多余的 |
| `ModAssets/` 是**逐字节原样复制** | 09-11 那次导出：工程里只有一张说明纸 → 产物里也只有那一张，md5 相同 |
| 游戏从 `<游戏>/ModExportData/Mod_Jgmg5L/` 加载 mod | `ModMgr:LoadMod(String)` + 全盘只有一份 `MOD_Jgmg5L.dll` |
| Steam 把我们的 exe **当游戏进程跟踪** | Steam `gameprocess_log.txt`：`adding PID … as a tracked process "…\AgentLoopServer.exe"` |
| 游戏内「删除模组」会**递归删整个 mod 目录** | `Player.log` 异常栈：`FileTool.DeleteDir ← UIModLocal.UpdateInfoUI`（鼠标点击触发） |

---

## 三、本次做完的事（2026-09-13 夜 ~ 09-14 凌晨）

### 3.1 Python 侧从"源码树"换成"一个 exe"

- 新增 `scripts/AgentLoopServer.spec`（PyInstaller onefile；`console=True`、`upx=False`、
  显式收 `websockets` 子模块、只内置 prompts 兜底而**绝不内置 config.json**）
- 新增 `scripts/server.py --selftest`：自检依赖与数据根后立刻退出，供 C# 探测与玩家排障
- 构建：干净 Windows venv（`py -3.12`）→ `pyinstaller`，**16 秒**出 15.4 MB

### 3.2 C# 侧路径与启动方式

| 改动 | 为什么 |
|---|---|
| `ModPaths.RuntimeDirNames` 加 `"ModAssets"` | exe 直接躺在 `ModAssets\` 根，不是子目录 |
| `ModPaths.IsInterpreter()`：按**文件名**判断要不要补脚本 | 自包含 exe 一个参数都不能传，`python.exe` 必须传 |
| `IsInterpreter` 为假时**不探测、存在即用** | `-c "import websockets"` 会被 exe 当未知参数吞掉然后起服务不退出 → 探测超时 → **好端端的 exe 被判成不可用**，且本机有系统 Python 时会"降级成功"从而永远发现不了 |
| `ModConfigFile.Path` 从 `ModRoot` 改成 **`DataRoot`** | 新布局下 config.json 在 `ModAssets\`，用 ModRoot 会读错文件 → 退回默认端口 → **"改端口即永久失联"复发** |
| `Launcher.KillOwned()` 改用 `taskkill /T /F` | onefile 运行时是**两个进程**（bootloader + 真身），`Process.Kill()` 只杀父 → **子进程继续占端口**（实测复现）。.NET Framework 4.7.2 没有 `Kill(entireProcessTree)` |
| `ModPaths.DevRootOverride()` 同时认 `DataRoot` 与 `ModRoot` | 新布局下作者自然会把哨兵放进 `ModAssets\` |
| 新增 `EnvParentPid`（`AGENT_LOOP_PPID`） | 见 3.4 |

### 3.3 面板打不开（`ModAbRes` 静默失败）

**症状**：AB 加载成功（`直载 bundle 数=14`、`注入 g.res.allRes： 3/3`），但按 F9/F10/F11 毫无反应。

**根因**：`Inject()` 第一行 `if (… || ab == null) return false;` —— 游戏读档/切场景会卸载
AssetBundle，缓存引用变成 Unity fake-null，代码**不打任何日志就放弃**，上层只看到
「预制体注入失败，本轮不创建」。

**关键证据（同一份代码、同一个 DLL md5、同一份 AB 指纹，结果相反）**：

| | 00:40 会话（源码形态） | 00:52 会话（导出形态） |
|---|---|---|
| 读档次数 | 1 | 3 |
| 重注入 | **成功 ×3** | **失败 ×6** |

⇒ 是**一直存在的竞态**（游戏卸 bundle 与重注入的先后），不是打包形态引入的。

**修复**：缓存失效 → 从磁盘重新直载再试；两处静默 `return false` 全部补日志。

### 3.4 服务永不退出（Steam「正在停止」）

**症状**：关掉游戏后 Steam 长时间显示「正在停止」。

**机制**：Python 服务原本 `while True: await asyncio.sleep(3600)`，WS 断了只等重连，
**永不主动退出**，全靠游戏/Steam 来杀。杀不干净就留**孤儿进程占着端口**（实测复现过）。

**修复**：C# 下发游戏 PID，Python 每 2 秒查一次父进程存活，游戏一退立刻退。
判据刻意用「父进程存活」而**不是**「WS 断线超时」——配置面板保存会重启 Python，
那时 WS 同样会断但游戏活得好好的，用断线当判据会**自己把自己误杀**。

**实测三项全过**：父进程不存在 → 2.0 秒自杀；父进程活着 → 6 秒不误杀；父进程被杀 → 1 秒内归零。

> ⚠ **诚实备注**：**没有证据**说这次的「正在停止」延迟一定是它造成的 ——
> `content_log` 里 `Terminating` 状态只持续了 **6 秒**，且 Steam 的 `no longer tracking`
> 时间戳是**批量吐出**的（源码形态那个 1 秒就该退出的探测进程也被记成 13 分钟后 release），
> 不能当精确计时用。**但机制性隐患是真的**，所以照修。

### 3.5 打包器 `scripts/pack_release.py`

| 改动 | 为什么 |
|---|---|
| **exe 成为默认形态**（`--portable` 才回退旧形态） | 发行形态已定 |
| `--build-exe` / `--exe` | 一条命令重打 exe 再铺包（27 秒走完全程） |
| **`--zip` 改成 opt-in**（原来默认出 zip） | zip 只是传输容器，走工坊/文件拷贝都不需要 |
| **`--from-export <dir>`**（新） | 吃编辑器导出的产物：**逐项核对 + 装进游戏**，且装的是**玩家真拿到的那份**（`--check-only` 只看不装） |
| `sync_ab()`（新） | 把 AB 铺满**四个可能有用的位置**，方向按"最新 .ab 的时间戳"自动判 |
| `stage_game_assets` 改从**工程**读 AB | 原来从游戏目录读 —— 游戏目录是**下游**，会把新 AB 覆盖成旧的 |
| `stage_licenses()`（新） | 生成 `开源许可.txt`（19 个组件、95 KB、许可原文全取到）。改 exe 后 CPython 被打进 exe，PSF 的"版权声明随二进制保留"没了落点 |
| `deploy()` 清理旧布局残留 | 根层 `AgentLoop/`、`prompts/`、`config.json` 一旦被 `ModAssets/` 取代就是"看着像配置、其实没人读"的死文件 |
| `deploy()` 迁移旧 config.json | 老布局的 key 在根层，新布局认 `ModAssets/` —— 直接铺空模板 = key 凭空消失 |

### 3.6 合规

- **PSF / Apache-2.0 许可原文随包**：`ModAssets/开源许可.txt`（19 个组件全取到；含 tqdm 的
  英式拼法 `LICENCE`、PEP 639 的 `licenses/` 子目录这些坑）

---

## 四、当前版本与位置

| 位置 | DLL | exe | 说明 |
|---|---|---|---|
| 源码构建产物 | `8ea8bdd65c54` | `8ef81f6f576a` | 最新（含 3.3 / 3.4 两个修复） |
| 编辑器工程 `ModProject/` | `8ea8bdd65c54` | `8ef81f6f576a` | 已同步 |
| 游戏目录 `ModExportData/Mod_Jgmg5L/` | `8ea8bdd65c54` | `8ef81f6f576a` | 已部署（**手拼版**，见下） |
| 最近一次导出 `F:\modtest\ModExportData_2026_9_14_0_49_30\` | `f04f8254ab1e` | `5902b03d3d8c` | ⚠️ **旧版**（导出发生在修复之前） |

> **"手拼版" vs "导出产物"**：`--deploy` 是 `编辑器工程 → 游戏目录` 的**直连**，跳过编辑器；
> `--from-export` 才是把**编辑器导出的产物**装进游戏。前者快（用于验修复），
> **后者才是"玩家真拿到的那份"**。发布前必须走后者。

---

## 五、待办

### 5.1 等用户测（当前卡在这）

- [x] **面板开着时游戏快捷键不误触（09-15 结案，ESC 除外）**。用户原话：
      「不是只修复按下 X 键，而是杜绝我打开我的 UI 的时候还能使用游戏本身的快捷键的情况」。
      **用户 09-15 晚实测通过**（日志 `★FastKey=820/跳过820★`，100% 拦下）。

      **正解**：`csharp/UI/FastKeyGate.cs` —— 给 **`MapWorldMgr.FastKey()`** 挂前缀，
      我们的界面开着（`HotkeyGate.ShouldBlock()`）就跳过，没开则原样放行。
      · `MapWorldMgr.FastKey()` 是**全程序集唯一**含 "FastKey" 的方法（`re:Fast` 命中 51 条，
        其余是 `FastBlink`/`FastClose`/`PathSearchFast`）；`MapWorldMgr` = 大地图世界管理器
        （`SceneMap.world` 持有）；它是 **private 零参**、由同类 `OnUpdate` 调用 = 每帧派发一次
        （实测与 `UIMapMain.Update` 计数 **1:1**）。
      · 同时采到了游戏自己的闸：**`MapWorldMgr.isEnableMap`**（`public static bool { get; }`）
        —— 进世界时日志出现 `isEnableMap 初值=False` → `★False→True★`。这是"游戏凭什么在
        剧情/战斗里屏蔽快捷键"的直接观测点，长期保留作金丝雀。
      · `ESC` 不在键表里（`DefaultKeys.json` 33 条无 keyID 27/13）⇒ 拦截不会吃掉 ESC。
      · 逃生开关 `<Mod根>\_diag_no_fastkey.txt`（存在 ⇒ 退回只计数不拦截，**改文件即时生效**）。
      · 入口：`ModMain` 第 9d 步调 `FastKeyGate.EnsureInstalled()`；
        判据：`UI/HotkeyGate.cs`；每帧采样：`AbHotkeys.OnFrame` → `FastKeyGate.Sample()`。

      **前三轮的教训（值钱，已沉淀进 `HotkeyGate.cs` 文件头的"别再试"清单）**：
      · 三轮全错在同一个方法上 —— **都在猜"哪个 `Update` 在轮询按键"**。
        第四轮换成**读 IL2CPP 元数据、按游戏自己起的名字找**，一次命中。
      · 09-13 那条 `UIFastClick.Update` 补丁**从头到尾一次都没被调用过**（大地图 HUD
        `G:btnEmail` 全套组件里根本没有 `UIFastClick`，场景内 0 个实例）。
      · `InputBase.Update`（73 万次）/`InputHandShank.Update`/`UIOperationGroup.Update`/
        `UIMapMain.Update` 跳过了都无效 ⇒ 都不是派发点。
      · `IsCanOperation`/`IsTopUI` 命中 **0 次**；UI 层级也不是判据（我们本来就是层顶）。
      · ★**最贵的一课**★：`UIBase.UpdateHandleInput` **整局只被调用 1 次** —— 它是"把输入控件
        填充好、按钮接上回调"的**接线方法**，不是轮询。把它挂成"面板开着就跳过"⇒
        **面板空白 + 关闭按钮点不动**（用户实证）。**动前缀之前先确认那方法是"派发"还是"接线"。**
      · 调用栈追踪挂在 `UIMgr.OpenUI` 上追派发者，三次抓到的全是**我们自己**开面板的调用
        （`AbChatPanel.EnsureResident`/`AbConfigPanel.HandleToggle`），预算被自身噪声吃光
        —— 追踪器必须先排除自身帧，否则等于没装。

      **已退役的探针（09-15 晚清理）**：`UI/InputGate.cs` 整文件删除（只计数观察层，
      结论已全部沉淀到 `HotkeyGate.cs` 文件头）；`HotkeyGate` 从 575 行瘦到 131 行
      （删掉 `DiagTick`/`[对照]` 表/`LeakWatch`/`RefreshCoverage`/12 键覆盖度扫描/
      `UIFastClickHotkeyGate` 补丁），只留判据 `ShouldBlock()` + `AnyInputFocused()`。
      闸门代码总量 1062 → 344 行。
- [ ] **重新导出一次**（工程已是最新）→ `--from-export "F:\modtest"` → 启动游戏
- [ ] 进世界后按 **F9 / F10 / F11**，确认面板能开
- [ ] 看日志里走的是哪条路：
      `[ModAbRes] bundle 缓存已失效（游戏卸载过 AB），重新直载 N 个后重试 …` = 修复生效
- [ ] 关游戏时留意 Steam「正在停止」还有没有卡顿（这次能量准了）
- [ ] 填 API key（F11）验真对话 —— 空 key 时复读是**预期行为**，不是 bug
- [ ] **Python 自愈实测**（09-14 新增）：进世界后开任务管理器杀掉 `AgentLoopServer.exe`
      → 应看到横幅「AI 服务已停止，正在自动重启（第 1/3 次）…」→ 十几秒后
      「AI 连接已恢复」→ 再发消息应正常。日志搜 `[BrainLink]`
- [ ] **断线提示实测**：杀掉进程后**立刻**发消息，应看到「AI 服务已停止，正在自动重启…」
      而不是像以前那样毫无反应

### 5.1.1 C 端探针大清理（09-15 晚，-1208 行）

**起因**：用户问"还有哪些是确认好之后没清理的探针轮询"。全量审计 45 个 `.cs`（27,155 行）后，
按"探针 vs 功能"逐类判定，**只删确认已定案的探针，保留所有功能性代码与抢救工具**。

| 删除对象 | 行数 | 判定依据 |
|---|---|---|
| `UI/UiGateDump.cs` | 418 | **无入口**：F12/F1 热键在 09-14「删除全部 F 键」时就摘了，文件没跟着删；`Dump()` 全仓 0 调用 |
| `NpcInitiativeMonitor.GateReport()` / `GateProbes()` | 89 | 唯一调用方是上面的 UiGateDump |
| `UI/OperationProbe.cs` | 197 | 注释自写「**A3/A4 取证：只读探针，定案后删**」，A3/A4 早已定案（走了克隆视觉行方案） |
| `OperationProbe` 3 个调用点 | 8 | `DramaAiOption` ×1（每次剧情窗注入）、`NpcPanelButton` ×2（每次 InitData / 每次切页签） |
| `AbPanelProber` 四条周期诊断 | ~110 | ① `UI诊断`（`DumpZeroSizeRawImages` + `WalkRawImages`：**递归遍历整棵 UI 树**，日志实测"访问节点=1011"）② `层顶监测#N`（每 240 帧，实测跑到 #60）③ `闸门诊断#N` ④ `StartGameTip 结构打印`。服务的 09-10/09-11 事故均已定案 |
| `AbHotkeys` 探针 helper | ~220 | `WorldInputSnapshot` / `TipUiPresent` / `PendingTipCount` / `DumpUiTree` / `DumpNode` / `DumpLayers` / `DescribeTopUi` —— 只剩已删的 AbPanelProber 在用 |
| `ModMain.DIAG_MINIMAL` / `DIAG_NO_WS` | 4 处守卫 | 09-10 登录期崩溃二分诊断开关，恒 `false`（且是 `static` 非 `const`，分支真的在跑）；连带 Init 里三大段包裹 |

**关键发现**：`AbPanelProber._uiDumpCountdown` **从未被赋值**（只读不写）⇒ 那个"创建后延迟扫一次"
的分支**从写下那天起就没执行过**——典型的"确认完忘了删"。

**保留（不删，附理由）**：
- `DiagSwitches` 全部文件哨兵 —— 常态零开销（每 120 帧 11 次 `File.Exists`），且是**发行版里给用户自助排障**的现场工具，文件头明确写了这个定位。
- `AbHotkeys` 的四个**抢救工具**（`ClearPendingTips` / `ClickUiConfirmButton` / `CloseAllButMap` / `CloseAllButMapSafe`）—— 无调用方但保留作现场手册，已在各方法 doc 上标注「抢救工具 · 无调用方」。
- `FastKeyGate.Sample()` —— `isEnableMap` 金丝雀，每 10 帧一次静态属性读，仅在值翻转时打一行。

#### 5.1.1.1 编译警告逼出来的第二批（同一晚）

清完探针后 `dotnet build` 冒出 4 条 **CS0414（字段已赋值但从未使用）**——编译器把
"只写不读"的死状态全点名了。顺着查，挖出两件事：

**A. `AbPanelProber` 已退化成"只剩闸门"的空壳**（`_created` / `_stable` / `_dramaQuiet`
三个字段只写不读，外加 `StableFramesNeeded` / `RetryCooldownFrames` / `AliveCheckIntervalFrames`
三个只声明、无人使用的常量）。根因写在它自己的注释里：**09-10 定案后"自动创建已彻底废除"**，
面板一律按需创建 ⇒ 那套"连续满足 N 帧 / 静置 N 帧才创建"的计数机制随之作废，但代码没删。
已全部移除，`OnFrame` 现在只服务两个诊断哨兵（`_diag_no_panels` / `_diag_lazy_panels`）。

**B. ★发现一个从未生效过的安全钩子（已修）★**
`DramaActivityHook`（`WorldSystemMgr.OpenMapDrama` 的 Harmony postfix）**是个纯空操作**：
它唯一做的事是调 `AbPanelProber.NotifyDramaOpening()`，而后者只把上面那两个**没人读的**计数器清零。
⇒ 原设计的「⑤ 地图剧情期间静置」这道闸**从写下那天起就没生效过**，
`OpenMapDrama` 上白挂了一个补丁。

它的文件头还留着一句关键顾虑：**"比按 UI 名猜剧情窗可靠（地图剧情可能不走 `UICustomDramaDyn`）"**
—— 而 `EvalGate` 的 ④ 恰恰只按 `UICustomDramaDyn` 这个 UI 名判。**即 09-10 那类事故存在缺口**。
现在把钩子接成真的：`NotifyDramaOpening()` 记下 `Time.frameCount`，
`EvalGate` 在随后 `DramaQuietFramesNeeded`(300 帧 ≈5~10s) 内一律判"剧情中"。
**待实测**：进地图剧情后立刻点地图「传」按钮 / NPC 面板「AI 对话」，应当**不弹面板**（并非常态，
只有剧情刚开始那 5~10s）；剧情结束后一切照旧。

**C. `ModMain._chatUiRoot`** —— 声明 + 一处 `= null`，从无读取，已删。

**代码量**：`csharp/**/*.cs` 27,155 → 25,954 行；DLL 501,760 → 478,208 字节。
**DLL**：`9321cb78ffb9cf7e1105df54e96d1913`（三端一致），构建戳 `0915-clean2`。
**编译**：0 error / **0 代码警告**（只剩 4 条已知的 `CS1668` LIB 路径警告，与本项目无关）。

### 5.2 我的（按优先级）

- [ ] **`ModProjectPreview.png`（工坊封面）** —— 缺它工坊列表只显示默认图。**需要用户提供图**
- [ ] **`安装说明.txt` 补数据路径**：玩家得知道对话记录在 `C:\Users\<用户名>\.sessions\`
- [ ] **`.sessions` 是否改名**（→ `.agent_loop`）+ 一次性迁移。
      理由：现在这名字太通用，两个同框架 mod 会互相覆盖。
      **发布后再改就要背迁移包袱，现在是最后窗口。** 待用户拍板
- [ ] **`pack_release.py` 简化**：删掉往 `ModCode/ModMain/bin/Release/` 铺副本那段（已证实多余）
- [ ] **游戏内「删除模组」撞运行中的 exe**：看门狗只缓解了"游戏退出后"的孤儿，
      **游戏运行中**玩家点删除仍必然失败（`UnauthorizedAccessException`）。
      最坏情况：游戏改了删除顺序 → 先删掉 DLL 再撞上锁 → mod 变**半删除状态**。待评估
- [x] ~~Python 侧存活检测（一条故障链三个环节）~~ → **09-14 已实施**，
      模型与风险见 `docs/brain-liveness-design.md`（含 §8.1 自动拉起的九条风险与对应的闸）
- [ ] **应用层心跳（#5）未做**：socket 通但 Python 事件循环卡死时，
      `BrainLink` 仍判 `Ready`。需要心跳 RPC 才能覆盖

### 5.2.1 主动开口开窗后"没有历史" —— 根因已定位，兜底已实施（09-14）

**现象**：NPC 主动开口 → 点同意 → 对话窗立刻打开，但**历史全空**，底部统计栏停在
预制体默认的 `New Text`。

**根因（实证，非推理）**：

| 时刻 | 事件 | 来源 |
|---|---|---|
| 16:44:03 | 主动开口回合开始（玩家放行） | `agent_loop.log` |
| 16:44:19 | `open_chat` 到达 Python，**回了 34 项历史** | 同上 |
| 16:44:33 | 回合才收口 | 同上 |

**请求发了、Python 也答了，是 C# 把响应丢了。** 丢在 `ChatPresenter.OpenChatAndReplay`
回调侧的守卫 `if (_busy || _window.HasActiveBubble) return;` —— 响应比首 token 晚了十几秒，
到达时回合已在流式输出。**一个 return 解释两个症状**：`ReplaceHistory` 没跑（历史空）、
`SetStats` 没跑（统计栏停在 `New Text`）。

那 16 秒全在 C# 侧（`open_chat` 在 Python 侧是瞬答，不抢回合锁）：**对话宿主是"首次打开时
懒创建"的**（`AbPanelProber.cs:28` 明写），而 `g.ui.OpenUI("UIChatAi")` 全量实例化吃掉了
首 token 前那 4~13s 的 TTFT 空窗预算（`NpcInitiativeMonitor.cs:386`）。

**已实施（兜底）**：把"丢弃"改成"记欠账"—— 冲突时置 `ChatPresenter._wantReplay`，
在**回合收口**（`OnReplyEvent` 的 finally，代码里已声明为"这一回合没在飞了的唯一权威信号"）
补做，且**重新请求**而非套用旧响应（旧快照不含刚收口的回合，套用会把玩家刚看完的回复擦掉）。
同时给那 8 个静默 `return` 全加了原因日志 —— 这次故障此前在 C# 侧**零痕迹**。

**为什么不做"让流式等历史"**：重放是幂等的（全量重投影），流式是增量的（不能应用两次）。
让增量方等待必须解决"这份历史里是否已含我缓冲的这一回合"，而流式事件**没有账本回合号**，
判不了 → 会重复渲染。故让幂等方等待。

**未做（治本）**：把对话宿主从"懒创建"改成"进世界预热"，让重放赶在首 token 前落地，
冲突根本不发生。前置条件：**主线程日志缺时间戳**（`ModMain.P` 走 `Debug.Log` → Player.log
无时间戳），"开窗花了多久"在 C# 侧量不出来，不先补这个就无法验收治本是否奏效。

### 5.2.2 上游挂死 6 分钟零报错 —— 已修（09-14 17:xx）

**症状**：游戏里「主动传音」回合显示
`回合异常：peer closed connection without sending complete message body (incomplete chunked read)`。

**实证时间线**（`ModAssets/logs/agent_loop.log`）：

| 时刻 | 事件 | 距起点 |
|---|---|---|
| `17:06:33.686` | 主动开口回合开始（intent=missing） | 0s |
| `17:07:03.7` / `17:08:03.7` / `17:10:03.7` | 慢告警 1/3、2/3、3/3 | 30 / 90 / **210s** |
| `17:11:18.255` | `open_chat`（历史回放 40 项）← 玩家开窗看了一眼 | 285s |
| `17:11:19.5` | `dispose_agent`×2 ← 1 秒后关窗 | 286s |
| `17:12:39.254` | `RemoteProtocolError` | **365.5s** |

**根因是三条，不是一条**：

1. **没人给它设上限**。线上 `config.json` 的 `llm` 块没有 `timeout` → `self.timeout=None` →
   httpx 用 SDK 默认 **read=600s**。365 < 600，SDK 认为一切正常。上游若不掐，玩家要等满 10 分钟。
2. **该重试的判成了"不可重试"**。`_is_retryable` 只枚举 Python 内建
   `ConnectionError/TimeoutError/OSError`，而实测 `httpx.RemoteProtocolError` 的 MRO 是
   `RemoteProtocolError → ProtocolError → TransportError → RequestError → HTTPError → Exception`
   —— **三条内建全不相交**，`status_code` 也是 None。同族还有 `openai.APITimeoutError`
   （MRO 走 `APIConnectionError → APIError → Exception`），日志里 16:36:38 那次超时同样被误判。
3. **SDK 层结构上救不了**。异常是在 `_drain` **迭代响应体**时抛的，而 SDK 的重试循环只包住
   `self._client.send(...)`；`stream=True` 时该方法收到**响应头**就返回，body 迭代在重试循环
   **外面**（`_base_client._request` 里 `break` 之后才 `_process_response`）。
   所以 `max_retries` 设成几都无关 —— 日志里也没有一条 `Retrying request to ...`。

**已实施**（`llm/openai_client.py` + `config_loader.py` + `llm/factory.py`）：

| 旋钮 | 默认 | 管什么 |
|---|---|---|
| `llm.timeout` | 45s | **流式**单次上限。量的是**相邻两块数据之间**的间隔（httpx read timeout 每读一次就重置），不是整个请求时长 → 持续吐字的长回复永不被砍 |
| `llm.timeout_nonstream` | 180s | **非流式**单次上限。非流式在整段生成完前一个字节都不发，量纲完全不同；唯一调用方是回合边界的上下文压缩，用 45s 卡它会**静默废掉压缩**（软失败，只落 warning） |
| `llm.total_budget` | 100s | 挂钟硬顶，含全部重试。防"对端 SSE 心跳不断重置 read 计时器 → timeout 永不触发"。**只约束流式**（压缩不受限，否则大摘要永远做不完） |
| `llm.retries` | 1 | 不变。总尝试 = retries+1 |

配套：`_is_retryable` 扩到 `httpx.TransportError`（guarded import）+ `openai.APIConnectionError`；
`_build_client` **无条件** `max_retries=0`（SDK 隐式重试关闭，"最坏等待"才可算）；
`_drain` 加 `finally: stream.close()`（被硬顶放弃的线程仍在跑，不 close 会拖到 GC 才还连接）；
失败点**无条件**打出 `已推送 token=N 已耗时=Xs 可重试=?`（旧实现"不可重试"分支排在
"已推送 token"之前就 `raise`，导致 token 数永远看不到 —— 而它正是决定能否重试的变量）。

**效果**：本事故最坏等待 **365.6s → 90.5s**，且中间有一次真实的重试机会。
压缩路径最坏 1800s（600×3）→ 360.5s。

**必须保留的例外**（用户拍板确认）：**已经吐出过内容**的流式中断不重试 ——
重发会让玩家把同一段话看两遍。所以规则是"一个字没吐就超时就重试；吐了一半才超时就不重试"。

**新增测试 10 条**（`tests/test_llm_retry.py` 9 条 + `tests/test_config_loader.py` 1 条），
含线上事故的端到端回归（`RemoteProtocolError` → 重试后成功）。全量 428 passed。

**未做**：「主动传音」回合失败仍然**静默丢弃**（`ws_channel.py` 的
`log.exception("主动开口回合异常（静默丢弃，服务继续）")` 只落日志、不回执 UI），
玩家要重开窗才从账本回放里看到。它跟代码里"NPC 没主动说话就是最好的失败"那句注释冲突，
也与 `_notify_consented_dropped`（"玩家在等回应时必须给回音"）立场矛盾 —— **待拍板**。

### 5.2.3 AI 时间与重试四个参数进 F11 面板（09-14 晚）—— 走 AB 资产固化

用户要求："记得加到配置UI里面，这些参数，超时时长，重试次数之类的"，
并拍板：**删掉 ui_preview；AB 用户自己打；不做运行期兜底，走 AB 资产固化**。

**四个键 + 面板行**（都在【高级】组，紧跟「RPC 超时秒」—— 全是"等多久/试几次"）：

| UI 行 | 键 | 默认 | Python 侧区间（夹取） |
|---|---|---|---|
| AI 单次超时秒·流式 | `llm.timeout` | 45 | (0, 600] |
| AI 单次超时秒·压缩 | `llm.timeout_nonstream` | 180 | (0, 1800] |
| AI 总等待上限秒 | `llm.total_budget` | 100 | (0, 3600] |
| AI 失败重试次数 | `llm.retries` | 1 | [0, 10]（面板另限 0-5） |

**空 / 0 = 不提交该键**（保持文件原值），与既有 `hasRetain`/`hasHeaders` 同惯例。
为什么不把 0 当"非法"拒收：0 在 Python 侧是合法的「关掉这个上限」语义（→ 落 SDK 600s）。
面板拒收 → 手编过 0 的用户连别项都存不了；面板当 45 提交 → **静默改掉用户配置**。两者都糟，
所以学 headers：没有明确的新值就不碰这个键。

**落地清单**（Python 侧零结构改动，只扩白名单）：

| 文件 | 改动 |
|---|---|
| `config_store.py` | 四个键进 `UI_WHITELIST["llm"]` + `_FLOAT_KEYS`/`_INT_KEYS` + `_RANGE_LIMITS` |
| `ConfigPanelRefs.cs` | 4 个 `InputField` 字段 |
| `ConfigUiBuilder.cs` | 4 行 `MakeFormRow`（代码版树用） |
| `ConfigPresenter.cs` | 读取回填 / 校验 / 提交 / 4 条 `RegisterRow` 悬停说明 |
| `AbConfigPanel.cs` | `CollectRefs` 4 条路径 + 诊断行加 `LlmTimeouts=` |
| `prefab_patch_config_groups.py` | `NEW_ROWS` + `ORDER` 各加 4 项（见下） |

#### 卡住过的地方：补丁脚本跑不动，真因不是"预制件坏了"

第一次拿 AB 工程那份 prefab 跑补丁，自检报 `HeadersRow Placeholder 文本不对`。查清了，**跟预制件结构无关**：

* 19 个"行"里有 **3 个是 Toggle 行**（`InitiativeEnabledRow`/`CompactionEnabledRow`/`PortraitRow`），
  它们只有 `ToggleBg` 没有 `Input` —— 所以"19 行只有 16 个 Placeholder"是**正常的**，不是缺节点。
* 真正的问题只有一个：`HeadersRow` 的 Placeholder，AB 工程那份是 `''`，而脚本声明的是
  `x-opencode-session: agent-loop`（ui_preview 那份是对的）。**纯文案差异，无功能影响**（Label 是对的，
  占位符只是灰字提示）。

**为什么会对不上**：补丁的"修正分支"只在**节点不存在**时才克隆并写文本，
**已存在节点的 Label/Placeholder 永远不被重写**。于是只要声明表里的文案改过一次，
重跑就会撞死局 —— 补丁认为自己做完了，`verify()` 却断言文本不对，脚本从此再也跑不动。
这是**脚本自身的缺陷，先于本次改动就存在**，只是恰好被这四行新配置引爆。

**修法**：加一步 **2c 文本归一** —— 对 `ORDER` 里所有在 `ROW_SPEC`/`HEADER_TEXT`/`BUTTON_TEXT`
声明表内的节点，无条件把 Label/Placeholder 重写成声明值（原有节点如 `BaseUrlRow`/`SaveLlmBtn`
不在声明表里，跳过，本脚本无权改写它们的文案）。对刚克隆的节点是幂等重写，
对早就在那儿的节点才是真正的修复。**只动文本不碰结构。**

#### 结果

* `Content` 子节点 **25 → 29**，总高 **1114.03 → 1254.93**
* 四行落在高级组、`TimeoutRow` 之后；校验类型经脚本自检确认：
  三个小数行 `m_ContentType=3 / m_CharacterValidation=2`，`LlmRetriesRow` 为 `2 / 1`（整数）
* **幂等已验证**：连跑两遍，第 2、3 次输出**逐字节相同**，`克隆节点: 无`
* 真件 352681 → 404300 字节，备份 `UIConfigAi.prefab.bak_cfggroups`

**下一步（用户）**：Unity 重打 AB（`ABBuildRunner.Run`）→ 拷 `ab/ui/*.ab` 到
`Mod_Jgmg5L\ModRes\AssetBundle\ab\ui\` → **重启游戏**（`ModAbRes.PreloadAll()` 一次性装进内存，
不重启不换）。核对口径 = 两端同名文件比 md5。

⚠ 本机 Unity 批处理当前**许可证无效**（`BatchMode: Unity has not been activated with a valid License.`，
`C:\ProgramData\Unity` 为空）—— 16:33 那次还成功过，之后状态变了，需重新登录 Unity。

#### 顺带：ui_preview 的隐患随删除一起消失

`ui_preview/Assets/MUD_UI/ConfigUiBuilder.cs` 比 `csharp/` 那份**落后**（缺 `HeadersRow`、
缺 `TestLlmBtn`、占位符还是开发机的 `127.0.0.1:8123`）。也就是说，**谁哪天点一下
「生成配置面板预制件」，就会把这些 09-14 的改动全冲掉**（生成器头注释自己写着"会覆盖手改"）。
工程已按用户要求在 09-14 删除，这个隐患不复存在；相关脚本的硬编码路径已改为跳过不存在目标。
`docs/APPENDIX.md` §E.2 的"重新生成预制件"一节已标注作废。

### 5.2.4 压缩会吃掉 L1 运行时上下文（09-14 夜，已修）

用户读代码时问「L1 伪装成 user 但 kind 不同，压缩只筛 message 吧？」——
**意图是那样，实现没做到**。仓库里三条路都碰到过 L1（`role:user` + `source.kind:plugin`），
只有两条认得出：

| 路径 | 读 `source.kind`？ | 结果 |
|---|---|---|
| UI 历史投影 `history.py:82` | ✅ | L1 不进聊天窗 |
| 删回合 `history_ops.py:167`（`:18` 写着"绝对豁免"） | ✅ | L1 不被删 |
| **压缩 `compaction/compress.py`** | ❌ **全文没有一处读它** | L1 被当材料 + 被折走 |

**两个后果（都实测）**：

1. **张冠李戴**。`_render_transcript` 的 `speaker = player_name if role=="user" else npc_name`
   只读 `role` → L1 段渲染成 `玩家：Current runtime context —— 自身：你是林婉清…`，
   读起来是"玩家宣称自己是林婉清"。纪要是**永久节点**，下次压缩还把材料喂回来 → 越传越歪。
   实测：第 2 次压缩材料出现 3 行、第 3 次 4 行，纪要层层残留。
2. **段被折走 + 基线不作废 = 永久不再发**。压缩的 replace 区间是在
   `session.surface.nodes` 上**连续**取的（`start_seq, end_seq = nodes[0], nodes[cut_idx-1]`），
   L1 段夹在对话中间 → 一起折进纪要、从 surface 消失；而差分 B 的基线 `_retained_ctx[段]`
   还留着旧文本 → 判"这段没变，不用发" → **模型静默失去境界/心境/气运/好感/日期**。
   实测：`ctx_window=1200` 每轮压缩 → 第 3 轮 surface 里的段变成 `[]`。

**为什么一直没暴露**：① 重开窗/读档会自愈（恢复路径扫 surface 扫不到 → 基线留 None → 重发）；
② L1 频繁变化也掩盖它（一变就重发，只有长期不变的段如境界/宗门/道侣才真丢）；
③ 既有 `test_auto_compact_keeps_fresh_l1_for_next_turn` 的 seed 让 L1 **恰好变了**，靠"变化"过关。

**已实施（净改动 ≈ 20 行）**：
* `_render_transcript` 入口加 `if source.kind == "plugin": continue`
  —— **入口拦人**，不去改 `speaker` 判据（它对真消息永远是对的；入口拦还让以后新增的
  plugin 生产者自动被拦）。只拦 `plugin`：工具结果是 `kind=="tool"`、主动开场指令是
  `"initiative"`，照旧进材料。
* 把 `__init__` 里的"从 surface 恢复基线"抽成 `_resync_runtime_ctx_baselines()`，
  并补第二件事：**不在 surface 里的段 → 基线置 None**（分段核对，不是无脑全作废 ——
  `replace` 只折 `nodes[0..cut-1]`，保留尾里可能还有某段的旧消息）。
  **两个入口都挂**：`_turn`（自动）与 `Agent.compact_now`（手动 `/compact`、面板按钮）。

**实测效果**（每轮都压缩、全程不改 L1）：

| 轮次 | surface 里的 L1 段 | 摘要材料含 L1 行数 |
|---|---|---|
| 1–4 | `自身 / 玩家 / 近况`（一直不丢） | **0** |

修复前：第 3 轮段变 `[]`；材料累计 3~4 行 `玩家：Current runtime context —— …`。

**回归测试**：`tests/test_compaction_ctx_safety.py`（5 条；先写测试确认 3 条会失败再改）。
全量 **442 passed**。完整分析见 `docs/context-projection.md`（§6 前缀缓存权衡、§7 本条）。

⚠ 仍未做：多行内容（工具返回 / 纪要块）的**后续行没有标签**，摘要 LLM 看到的是裸文本 ——
独立的既有小瑕疵，与本次无关。

### 5.3 未实证

- [ ] **创意工坊上传**（完全没做过；订阅后的加载路径靠推理）
- [ ] 干净机器（无 Python、无 VS）上的完整安装验证
- [ ] 杀软误报实测（只做过 VirusTotal 数量级调研：PyInstaller ~4/71；
      Nuitka onefile 据调研 ~19/71，**本机未实测**）
- [x] ~~Nuitka 打包能否成功~~ → **2026-09-14 已跑通**，见 `docs/PACKAGING.md` §6
      （18.2 MB / 14.4 分钟 / 1263 个模块编译成 C；`--selftest` 通过、真起服务握手成功、
      载荷内 `.py` 与 `.pyc` 均为 0。工具：`scripts/build_exe_nuitka.py`）
      **决策：先不切**——发行档仍用 PyInstaller，Nuitka 留作已验证的备用档，
      待创意工坊链路验通 + 杀软误报实测后再评估切换。**切换不动 C#**（判据是文件名）。

---

## 六、命令速查

```bash
# 构建 C#（Release = 发包用，唯一不把 PDB 绝对路径写进 PE 的配置）
"/mnt/c/Program Files/dotnet/dotnet.exe" build csharp/AgentLoopBridge.csproj -c Release

# 打包全套：重打 exe + 铺进编辑器工程（27 秒）—— 之后点「导出模组」
python3 scripts/pack_release.py --build-exe --install-to-project

# 吃编辑器导出的产物：核对 + 装进游戏（改完 UI 或发布前用）
python3 scripts/pack_release.py --from-export "F:\modtest" --check-only   # 只看
python3 scripts/pack_release.py --from-export "F:\modtest"                # 核对并安装

# 日常迭代（改 Python 想秒级验证）：游戏跑**活源码**而不是随包 exe
#   在 <Mod根>\ModAssets\ 放 _dev_root.txt，内容一行 = F:\agent_loop
#   哨兵生效时会**跳过**自包含 exe 候选（作者要的是活源码，不能被产物盖掉）

# 单独验 exe
<Mod根>\ModAssets\AgentLoopServer.exe --selftest

# Python 测试
python3 -m pytest tests/ -q      # 375 passed
```

排障入口：`<Mod根>\ModAssets\logs\agent_loop.log`、MelonLoader 控制台里 `[Launcher]` 那一行、
Steam 的 `logs\gameprocess_log.txt`（查"谁被当成游戏进程跟踪"）。

---

## 七、环境事实（换机器要改）

| 项 | 值 |
|---|---|
| 游戏 | `E:\SteamLibrary\steamapps\common\鬼谷八荒`（appid 1468810） |
| 部署目标 | `<游戏>\ModExportData\Mod_Jgmg5L\` |
| 编辑器工程 | `F:\mod\ModProject_Jgmg5L\ModProject` |
| 构建缓存（**在仓库外**，见 PACKAGING.md） | `F:\.agent_loop_build\` |
| exe 构建 venv | `F:\.agent_loop_build\pybuild\venv`（`py -3.12`） |
| 官方权威文档 | `<游戏>\Mod\modFQA\代码编写教程\代码编写教程.docx`、`资源修改教程\资源修改教程.docx` |
| 玩家日志 | `C:\Users\iu\AppData\LocalLow\guigugame\guigubahuang\Player.log`（`grep -a`） |
