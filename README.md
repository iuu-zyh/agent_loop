# agent_loop — NPC 对话专用 AgentLoop 框架

> **一句话定位**：为《鬼谷八荒》类 NPC 一对一对话复刻 `DSH` 的 `Agent` 模式（`LlmRuntime` + `Tools` 分家）的最小可跑实现。**Python 是大脑，C# 是双手**：Python 管对话/工具决策，C# `MelonLoader` 壳管 `g.world/g.conf` 与 Unity 主线程调度，二者以**单条 WebSocket 全双工连接**交换 JSON（Python 作 `WsServer :8766`，C# 作客户端连入）。事件溯源存储、`SystemPrompt` 单例分层、`LlmClient` 单口适配、`GameBridge` 游戏侧抽象、`AgentLoop` 统一管生管毁——C 端骨架已初步搭建。

**技术栈**：`Python 3.13 / 事件溯源 Session / Surface 投影 / Inbox 双筐 / SystemPrompt ScopedLayers / LlmClient(openai·stub·echo·流式) / GameBridge(Stub·Ws) / 9 工具 OpenAItool_calls / 压缩(中文计量·摘要替换) / jsonl 落盘 / 全局日志 log_setup（文件落点+回合上下文+阶段慢告警+异常兜底）/ 单 WS 双向通道 + step·text_delta 流式 / UI 历史投影(get_history) / agent 生命周期 open_chat·dispose_agent（开窗激活·关窗冻结·存档固化 save_happened/进世界丢弃 load_happened·空账懒落盘）` + `C# MelonLoader(IL2CPP) 插件壳（已编译通过）：WS 客户端 + Unity 主线程调度 + 9 工具 16 动作落地（含战斗 spar/attack，神识传音 IL 实证）+ NPC 主动开口监测 + 游戏内三面板 UI（对话 UIChatAi · 配置 UIConfigAi · 通讯录 UIContactAi——F9/F11/F10；双轨制：代码构建器 ↔ AB 预制体，Presenter 业务单份、`*PanelRefs` 路径契约解耦；互跳/上下文传递/未读联动 + 工具确认窗 DramaGate（立绘剧情窗，ID 走官方编辑器 MID 配置表）；原生剧情「AI 应对」DramaAiOption——剧情窗注入选项、原文喂 agent 润色开场。**全部细节见 `docs/ui-interception-spec.md`**）`

---

## ⚠️ 授权与声明（**先读这段**）

**本作品完全免费。** 创意工坊页面：[《八荒智能体》](https://steamcommunity.com/sharedfiles/filedetails/?id=3801664332)

| | |
|---|---|
| ✅ **允许** | 个人学习、研究、阅读源码、自己修改、在自己电脑上随便折腾 |
| ❌ **禁止** | **任何形式的商业用途** —— 包括但不限于**收费群、付费下载、打包进付费整合包、挂在收费网站上、以任何名义向他人收取费用** |
| ❌ **禁止** | **二次打包发布** —— 改个名字 / 换个封面重新上传到创意工坊或任何平台 |
| ❌ **禁止** | 移除或篡改作者署名、本仓库链接、以及程序内嵌的署名信息 |
| 📌 **要求** | 转载、引用、基于本作品做衍生时，**保留作者署名与本仓库链接** |

完整法律条款见 [`LICENSE`](LICENSE)（[PolyForm Noncommercial 1.0.0](https://polyformproject.org/licenses/noncommercial/1.0.0)）。
注意：因含非商业限制，本作品属于 **source-available（源码可见）**，而非 OSI 定义的开源软件。

### 💰 如果你是花钱买到这个 mod 的 —— 你被骗了

本 mod 在 Steam 创意工坊**完全免费**。

任何向你收费的人**都不是作者**，他们只是把免费的东西搬去卖。请立刻**申请退款**，并向平台**举报**。
你不需要为这个 mod 付任何一分钱。

### 如果你是搬运工 / 整合包作者

欢迎搬运，前提是**注明作者与本仓库链接**、**不收费**、**不移除署名**。
做不到这三条，请勿搬运。

---

## ⚡ 当前状态（2026-09-13）

**双进程全链路已跑通，功能面全部实装；当前重心 = 真机核验与体验打磨。**

| 面 | 现状 |
|---|---|
| **Python 大脑** | 事件溯源 `Session` + `Surface` 投影、`SystemPrompt` 分层（sections/personas/traits + L1 四段**逐段差分**注入）、9 工具 `tool_calls`、中文计量压缩、`WsServer :8766`、`log_setup` 全局日志（含排障手册）。 |
| **C# 双手** | `WsTransport` 纯托管 RFC6455 + 主线程调度 + 9 工具 16 动作落地（含战斗 spar/attack、传功、邀约）+ **二阶段校验**（同格·好感·性别·战力，不信模型参数）。 |
| **游戏内 UI** | 三面板全部 **AB 预制体化**并部署：对话 `UIChatAi` / 配置 `UIConfigAi` / 通讯录 `UIContactAi`（F9 / F11 / F10）；生命周期走**方案 A**（按需创建 + 关闭 `CloseViaManager` 交游戏 → 登记表不泄漏 → 世界输入不被门控）。 |
| **剧情层** | 原生剧情窗「AI 对话」按钮旁路注入（不接管原选项；点它 = 开对话面板 + 把该页成品句交给 NPC）；邀约第二层地点**原句捕获**（`DramaTextCapture`）；工具确认窗走官方模组编辑器 MID 配置管线。 |
| **自主交互** | NPC 主动传音全链（10 道闸 → 意图派发 → 伪 user 意图驱动正常 turn）→ 收尾**三分流**：同格+空闲**当面开窗吃流式**、异地/忙 → **未读红点 + HUD 横幅**；同格先弹「同意/不同意」确认窗。 |
| **持久化** | 开窗激活 / 关窗**冻结**（不写盘）/ 存档 `flush_all()` 固化 / 读档 `discard_all()` 回滚；通讯录·对话历史·最近索引按 **`worlds/<world_id>/`** 存档隔离。 |
| **立绘** | 对话窗走 `PortraitService` 原生管线（**不得对 `RawImage.texture` 向下转型**，见附录 D.1）→ `PortraitCache` 256² 圆形烘焙快照；通讯录头像**读同一快照**（命中即贴，未命中占位圆 + 懒读盘）。缓存是**懒填充**：只有开过对话的 NPC 才有立绘。 |
| **图片输入** | 走**文件路径**（三来源归一）：手打/粘贴路径、输入框 `Ctrl+V`（剪贴板 `CF_HDROP` / 注册格式 `"PNG"` / `CF_DIB`）、**输入框上方缩略图预览条**。`csharp/UI/ImageInput.cs` 读文件 → 长边 ≤1024 降采样 → base64 data URL → 随 `player_message` 的 `images` 字段发出；**图片只进当回合、不进历史**（账本只留 `[图片：xx]` 占位），**允许只发图不发字**。契约与 `CF_DIB` 的两个坑见 [附录 A.2](docs/APPENDIX.md)；路径解析回归测试 `bash scripts/dev/imgparse_test/run.sh`。 |
| **面板口径对齐** | **境界**：`gradeID` 是 RoleGrade 的**行号**（44 行=大境界×期×品质），而 `GetGradeName(grade)` 要的是**大境界号**（1..10）—— 直接喂会越界钳到「登仙境」（真机事故）。现按行号定位 + 用 `dynUnitData.curGrade` 自洽校验，取 `gradeName+phaseName` = 「结晶后期」。**数值**：brief 的 魅力/声望/资质/心情/年龄/寿命 一律 **DynInt 优先**（面板同口径，裸字段不含气运加成，实测声望差 250）。**身份**：打开对话 UI 时 `UnitLookup.Pin(name, unitID)`，按名解析先查登记。测试 `tests/test_realm_and_identity_contract.py`。 |
| **指人靠 id** | 全图 NPC **重名是常态**（真机 `Resolve('益婉容')` 查成了另一个益婉容：结晶后期/声望3379 → 登仙境/声望3129）。现三条防线：① `inspect_unit(unit_id=…)` 精确指人，关系簿与 `search_units` **每人都回传 `unit_id`**；② 给了 `unit_id` 只认它、查不到不回退按名；③ 按名解析**扫完全部同名者**并如实上报 `ambiguous`，渲染成正文最前面的 `⚠️同名提醒`。坑点：`GetUnit` 对中文名会**按名兜底**，故中文名不走它、走了的必须回验 unitID。见 [附录 D.1](docs/APPENDIX.md)，测试 `tests/test_unit_id_contract.py`。 |
| **经历日志** | `inspect_unit(classes=["logs"])` 读 `LogData` **解码层** `allLogData`/`allVitalLogData`（`List<LogItemData>`，逐条 `logs[]/subLogs[]` 调 `Data.GetLogString()` 出人话），**按新→旧排列（第 1 页 = 最新）**。**原始层 `allLog`/`allVitalLog` 是 `List<string[]>`，照 `LogItemData` 读会静默全空成 `(无文本)`**——层序铁律见 [附录 D.1](docs/APPENDIX.md#d1-il2cpp-线程与类型铁律)。文本里嵌的 `@w_<soleID>|<n>|<propsID>|<n>|@` **引用标记会换成道具中文名**（`ConfItemProps.GetItem(propsID).name` → `GameTool.LS`；旧实现只取字段0 → 模型读到 `购买了zk8xkN` 而游戏面板显示 `五品培元丹`），`DataToString()` 的 `0&A&A` 编码残渣丢弃。L1「近况」从**按桶配额（vital 3 + regular 2，真机 vital 恒 0 → 实际恒 2 条）改为合并两桶取最近 `RecentCap`=6 条**。契约测试 `tests/test_log_layer_contract.py`。 |
| **游戏时间** | **双通道让 NPC 感知时间**：① 玩家消息在**进 WS 之前**由 C# 拼上日期戳（`[1年1月3日] 你好`），落进账本长期留存 ⇒ 能说出"上次见你是三月前"；② L1 增发 `raw.now`，成文为**独立的「当前时间」段**（`_CTX_SEGMENTS` 首位）⇒ 纯 NPC 主动开口回合也有"现在"，且近况为空时不会连日期一起消失。账面月 = `g.world.run.roundMonth + 1`、日 = `roundDay + 1`（**均 0 起**；`WorldRunMgr` **没有 `roundYear`**），换算唯一落点 `UnitSnapshot.SplitAccountMonth`（`CnYearMonth`/`CnDate`/`now` 共用，消息戳与 L1 段**逐字节同串**）。拼在**本地回显之前** ⇒ 气泡/账本/模型同串；日历读不到 → 空标签/空段 → **原样照发、不编日期**。模型侧的读法写在 `harness_identity.txt` 的 `[消息读法]`。契约见 [附录 A](docs/APPENDIX.md)，测试 `tests/test_game_time_stamp.py`。 |

**工具文档去重（09-13）**：`tool_usage.txt` 曾把 9 个工具的参数名/枚举/取值范围逐个复述一遍，而 `schemas.py` 里那份**已经作为 `tools` JSON 传给模型**——每次请求把同一份契约付两遍钱（system 段 4,875 字里 `tool:usage` 占 55%，合计固定开销 14,174 字 ≈ 5,000 token，而真机 `in=` 才 8,600~11,800）。按判据「删掉后**不知道该传什么** → schemas；**不知道该干什么** → tool_usage」重切后：`tool_usage` 2,704 → **748 字（−72%）**、`schemas` 9,299 → **7,987 字（−14%）**、合计 **−27%**。**刻意保留四条的重复**（认 unit_id 不认名 / 陌生≠认识 / 面对面类异地不可行 / error 处理）——它们是行为规则不是契约，纯去重会掉正确率。护栏 `tests/test_tool_usage_contract.py`（长度预算 + 禁复述 + 承重墙清单）。详见 [附录 G.5.4](docs/APPENDIX.md)。

**切磋结果按玩家的选择走（09-14）**：原生 21204 剧情窗弹「好，就让我和你切磋一下 / 我现在没有空」，而 `case "spar"` 原本是 `CreateAction` 完就 `return Ok(...)` —— **两个选项给模型同一句话**，玩家婉拒了 NPC 还照着"双方已开始切磋"演。现在 C# 挂起并在 `UIDramaBase.ClickOption` 上认出这一对选项，**选项一落定就定案**（`accepted: true/false`），Python 渲染成三句不同的话。判据取**选项 id**而非动作结束后的 `isDrillComplete`（后者无语义样本，同类字段 `isInviteComplete` 曾把"接受"误判成"拒绝"）。`attack` 保持立即返回（`UnitActionRoleAttack` 连 `OnEnd` 都没有）。护栏 `tests/test_spar_choice_contract.py`（含"硬编码 id ↔ 游戏配表逐字对齐"）。详见 [附录 D.4](docs/APPENDIX.md)。

**工具契约一致性（09-13 全量核对）**：`tools/schemas.py`（模型看到的参数/枚举）↔ `csharp/ToolExecutor.cs`（真正读的参数 / 真正发的 data 键）↔ `tools/text_render.py`（真正进上下文的键）三面已逐条对齐，并固化成可重复的机械检查 `tests/test_tool_contract_consistency.py`（8 条，全部变异验证过）：分发与 `TOOL_ORDER` 一致 · 无死参数 · 无未声明的隐藏参数 · 枚举值全覆盖 · **渲染层读的每个键 C# 都真的发（本项目已踩四次的形态）** · `initiator` 由 harness 注入（5 个动作工具的 actor 全靠它，漏了要到真机才炸）· `inventory_top` 上限与 schema 一致 · schema 描述不引用已删除的字段名。"允许的不一致"只剩一条并用白名单钉住：`initiator`（harness 注入，刻意不给模型 —— `dialogue_agent._execute_tool_calls` 是 actor 身份的唯一来源，删了 5 个动作工具全哑且无编译期信号）。旧别名 `target` 与 `economy_item` 顶层单件形态已于 09-13 兜底清理中删除。

**构建与部署**：日常迭代 `dotnet build csharp/AgentLoopBridge.csproj -c Debug` → `bin\Debug\MOD_Jgmg5L.dll` → `ModExportData\Mod_Jgmg5L\ModCode\dll\`，**部署后必须两端 MD5 比对**；**发包走 `-c Release` + `python3 scripts/pack_release.py --build-exe --install-to-project`**（Python 侧现在是**一个 15 MB 的自包含 `AgentLoopServer.exe`**，放进 `ModAssets/`，由编辑器「导出模组」带走；改了 Python 代码要带 `--build-exe`）（Release 的 `DebugType=none` 是刻意的：Debug/pdbonly 会把 PDB 绝对路径写进 PE 的 RSDS 目录，跟着 DLL 发给玩家）——**进度与待办见 [docs/RELEASE_STATUS.md](docs/RELEASE_STATUS.md)**，完整打包/保护/许可说明见 **[docs/PACKAGING.md](docs/PACKAGING.md)**。若另有会话并行改同一仓库，构建会互相覆盖——**以 `bin\Debug` 时间戳 + 部署 MD5 为准**（完整链路见 [附录 D.2](docs/APPENDIX.md#d2-工程--构建--部署链)）。

**已知边界（不影响使用）**：读**更早**存档不严格回滚账本（v2 快照方案预留）；历史回放未做上拉分页/虚拟列表（真到上千回合才需要）；`llm.timeout` 默认 `null` = 保持 SDK 默认 600s，建议显式收紧到 60–120s；C# 侧 request/response 帧 trace 镜像未做；**打开我们的面板期间屏蔽游戏原生快捷键**（`UI/FastKeyGate.cs` 拦 `MapWorldMgr.FastKey()`，判据在 `UI/HotkeyGate.cs`；ESC/Enter/鼠标点击照常）——09-15 真机实测通过（`FastKey=820/跳过820`，100% 拦下）。

**文档地图**

| 文档 | 内容 |
|---|---|
| 本文档 | 项目思想与架构 / 实现流程详解 / 特性速览与未来目标 |
| [`docs/RELEASE_STATUS.md`](docs/RELEASE_STATUS.md) | **发行进度与待办（先看这个）**：目标与交付形态 · **已实证的事实**（别再重复验证）· 本轮修掉的六个发行级问题及其证据 · 当前各位置的版本号 · 待办（谁卡谁）· 命令速查 · 环境事实 |
| [`docs/PACKAGING.md`](docs/PACKAGING.md) | **打包与分发**：一条命令出包 · 产物布局与理由 · C#↔Python 环境变量契约 · 中文 Windows 编码三件套 · 泄漏闸 · **Python 源码保护能到什么程度**（当前 PyInstaller 只防手滑；Nuitka 档**已实测跑通**，含四个专属坑与实测数据）· 许可与发布前检查单 |
| [`docs/APPENDIX.md`](docs/APPENDIX.md) | 附录 **A** 桥契约 · **B** 日志规约与排障手册 · **D** 开发铁律与高频踩坑 · **E** UI 工作流 · **G** 功能设计底稿 · **H** 官方 API 与反编速查 |
| [`docs/context-projection.md`](docs/context-projection.md) | **上下文投影与逐段差分**：账本 vs Surface 投影（三种算子）· **L1 四段**（time/self/player/recent）与成文 · **每步四个差分**及其各自粒度 · 「一个字变了会不会全部重载」的精确回答 · **为什么只 append 不 replace = 前缀缓存（实测 100% vs 25%）** · **压缩读 `source.kind` 豁免 L1 + 折走的段作废基线（均含回归测试）** |
| [`docs/brain-liveness-design.md`](docs/brain-liveness-design.md) | **Python 侧存活检测的状态模型梳理**：一条故障链三个环节 · 四份互不相干的状态位 · 为什么「用 WS 状态推断进程死活」是错的（真值表）· 六个坑 · 建议的分层模型与落地顺序 |
| [`docs/ui-skin-prompts.md`](docs/ui-skin-prompts.md) | 原附录 C：UI 换肤素材清单与生图提示词（施工中） |
| [`docs/ui-interception-spec.md`](docs/ui-interception-spec.md) | UI 拦截/注入全量细节（面板架构、克隆与注入、动作映射表） |
| [`docs/tool-result-contract.md`](docs/tool-result-contract.md) | 工具结果契约（C# 只回 raw、Python 成文、忠实原则） |
| [`docs/2026-09-10-world-input-freeze-postmortem.md`](docs/2026-09-10-world-input-freeze-postmortem.md) | 「世界输入失效」事故复盘 |

---

## 一、项目思想与架构

### 1. 项目概况

* **做什么**：提供 **一 NPC 一 Agent** 的对话底座。`AgentLoop` 管 **创建/查询/销毁/自动续聊**，`DialogueAgent` 管 **turn/step 循环**，`Session` 管 **流水账**，`SystemPrompt` 管 **人设分层**，`LlmClient` 管 **模型接入三路**，`GameBridge` 管 **游戏侧快照与工具执行**；与游戏 `UI` 解耦，可脱离游戏单独测（`StubGameBridge` + `StubLlmClient`/`EchoLlmClient`）。
* **为什么抄 DSH**：`神识传音`（见 `../ggbh_mod_analysis/神识传音Mod逆向分析.md`）把全历史展平进单条 `system`、把 40 行动清单写进 `system` 文本令模型吐 `XML` 再正则解析，直给但脆弱、不可缓存、非流式。`DSH` 用 **账本与视图分离、制度与便签分离、工具结构化、LLM 与 Tools 分家** 的标准 `Agent` 模式，恰好治前者的痛点，且与"多 NPC 各有人设"的需求天然契合。

### 2. 双进程架构（C 端已初步搭建）

**分层分权**：Python 是大脑（管 `messages[]/tools/模型/对话编排`），C# 是双手（管 `g.world/g.conf` 与 Unity 主线程调度）。二者只以**单条 WebSocket 全双工连接**交换 JSON（Python 作 `WsServer` 监听 `127.0.0.1:8766`，C# 作客户端连入），**不共享内存**。C 端 = `MelonLoader` 插件（`csharp/`）+ Python 侧常驻平台（`scripts/server.py`）+ 命令行对话壳（`scripts/chat_cli.py`）。

```
Game.exe (Unity IL2CPP + MelonLoader + 本插件 AgentLoopBridge)
 ├─ 主线程：所有 g.world / WorldUnitBase / RelationData / DramaFunctionTool 必须在此调
 │    └─ NpcInitiativeMonitor 帧节流检测（冷却表×候选关系网）→ 产出 NPC 主动开口事件
 └─ 后台线程：WsClient 连 Python 的 WsServer(127.0.0.1:8766) 收发 JSON 帧 → Enqueue 到主线程
        ↑ request {get_context, call_tool} ← Python 回合内调用   （WsServer → C# 客户端）
        ↑ event  {step, text_delta, npc_reply} ← Python 回合中推流    （C# 客户端接收）
        ↓ event  {player_message} ← 玩家消息（游戏内对话 UI 输入）   （C# 客户端发送）
        ↓ event  {npc_initiative} ← NPC 主动开口（NpcInitiativeMonitor 触发） （C# 客户端发送）
Python 进程 (agent_loop)：server.py 常驻（WsServer + ChatHub），chat_cli.py 喂话/看回
```

C 端职责（对照 [附录 A](docs/APPENDIX.md#a-cpython-桥契约单-websocket-全双工) 桥契约，当前为**可跑通骨架**，游戏 API 签名按 `Assembly-CSharp.dll` 实际对照）：
* `ModMain.cs`：插件入口，`Init` 启动调度器 + WS 客户端 + 主动互动监测 + 拉起 `server.py`；`Destroy` 停机
* `WsClient.cs`：后台线程连 Python `WsServer`，收 `request`/`event`、发 `player_message`/`npc_initiative`，断线重连
* `MainThreadDispatcher.cs`：挂 `g.timer.Frame` 每帧，在 Unity 主线程执行入队的游戏调用
* `GameContext.cs`：`request: get_context` 拼 ≤200 字 L1 快照（`自身/玩家/近况` 三行，自身段带气运「名（desc截60）」；raw 含 `same_grid` 供 Python 判定传音模式 + `luck` born/added 条目{name,desc}）；L1 由 Python **每轮取一次**（`DialogueAgent` turn 首步取缓存轮内复用，movement 成功后置脏同轮强制刷新），不再每 step 重取
* `NpcInitiativeMonitor.cs`：NPC 主动开口触发检测（**混合日节拍**：`g.events.On(WorldAddDay)` 每游戏日一试 + `Frame`兜底；**状态闸** 战斗/确认窗不打扰；**0-100日概率** `daily_chance` + 低好感`<阈值减半` 对齐神识传音；**双冷却** 现实`npc_cooldown_real_s`+游戏`npc_cooldown_days`；**社交关系候选** 复用`UnitSnapshot`全集`parent/children/brother/brotherBack/lover/master/student/married+friendUnits`去重排序，按亲密度派发`greet/missing`等11意图，参数经 `Configure` 热更（Init 后延迟从 Python `get_config` 拉取：每日概率/双冷却/低好感阈值+减半），经`WsClient.SendInitiative`发`npc_initiative`事件；**同格当面先弹同意确认窗**（立绘剧情窗 `UICustomDramaDyn`，ID=MID+170 与 ModExcel 配置表同源；婉拒亦进冷却，consented 豁免 Python 节流），异地直发传音）
* `ToolExecutor.cs`：`request: call_tool` 统一入口，9 工具分发 + **二阶段校验**（同格/好感/性别/战力，不信任模型参数）+ **自然语言 `text` 双轨**（每工具 `data.text` 人话一句供模型直引，结构 `data` 保留机读；`married` 已译名、`relation:None→陌生`、`@q_名|id@` 已清）；**`world_ai_action` 8 op 已落地**（战斗 spar/attack + 双修/论道/邀约/疗伤/提升心情按神识传音 IL 实证 1031/1037/1044/1034/1041，传功=`UnitActionRoleTeachSkill` 带 `SkillCanTeach` 校验 + `TeachType.Teach`）
* `Launcher.cs`：探测 Python 并 `Process.Start` 拉起 `scripts/server.py`（状态锁只拉一次）
* `UI/DramaAiOption.cs`：**原生剧情窗「AI 应对」注入**（Harmony Postfix `UIDramaBase.InitData`）——游戏本体 NPC 自主互动（过月仇人寻仇等）弹剧情窗时，捕获 `DramaData` 单位与 `DramaTool.lastOpenDramaDialogueText` 原文，弹窗右下角注入「AI 应对」按钮；点击 → `ChatLauncher.OpenForUnit` 开面板 + `SendGameDrama`（npc_initiative intent=game_drama 带 text）→ Python 跳节流包装舞台指令驱动 NPC 顺剧情润色开场。旁路选项版：不抑制原 UI、不碰结算回调（设计底稿要点见 [附录 G](docs/APPENDIX.md#g-功能设计底稿要点)）

之所以把 C 端先做成 **WS 桥 + CLI 证明链路**，而不是一上来就做复杂游戏内 UI：先打通「对话→工具→LLM→回话」的**全链路可运行**，桥不返工。当前已可：`python scripts/chat_cli.py --ws --npc 林婉清` 以模拟 C# 客户端连入与 NPC 对话（实时看 step / 逐字 text_delta / npc_reply）；**游戏内三面板 UI（对话/配置/通讯录）均已实装**（双轨制 + 入口互跳 + 未读联动，详见 `docs/ui-interception-spec.md`）。

### 3. 核心设计思路

**原式痛点（神识传音 §4–§5）**

* 历史形态：`Messages[unitID]` 全量 `"{名字}:{内容}\n"` 展平后塞单条 `system` 的 `[历史对话记录]`，真 `user` 仅末条一条。模型靠名字区分说话人，丢失原生多轮结构。
* 块顺序：`[系统补充设定/RAG]` 与 `[你的身份信息]` 等易变块位于历史之前，前缀缓存命中率趋零。
* 工具传递：行动清单写进 `system`，要求模型按 `<actionNumber>` 等 8 标签 + 4 `Option` 的 `XML` 输出，本地 `XmlTagParser→UniversalDataParser` 正则兜底，非流式且有格式失败率。

**欣赏的 DSH 解法**

* **Session 事件溯源**：`log` 只追加，`Surface` 投影抄成 `messages`，`Inbox` 双筐排队，账本即存档可重放。
* **SystemPrompt 单例分层**：全局屉放世界观/规则，每 `NPC` 一屉放 `deployment:persona`，`assemble` 合并后者覆盖，`order` 排序拼 `system`，稳区可缓存。
* **LlmRuntime 与 Tools 分家**：`LlmClient`（单个 `generate` 口）管模型接入，`Tools/Bridge` 管工具执行——LLM 是无副作用的纯生成，模型只产原生 `tool_calls`。
* **工具结构化**：`tools` 独立数组不进 `system` 文本，模型走原生 `tool_calls`，本地 `tool/call→tool/result` 配对落盘。

**我们对齐的现实**：抄 `DSH` 的骨架（`Session/Surface/Inbox/SystemPrompt/DialogueAgent/AgentLoop`）+ `LlmClient`（三路） + `GameBridge`（Stub/Http） + 9 工具，弃原式的全量展平与 `XML` 模拟 `function calling`。重点改造：a) 把 `llm_client/llm_stub` 双字段 + `_step` 三路分支下沉为**单一 `LlmClient.generate`**；b) 工具执行留在 Agent/Bridge，**不塞进 LLM**；c) 回显是 `factory` 的 fallback 而非循环机逻辑。

### 4. 关键子系统设计

| 子系统 | 职责 | 与他者关系 |
|---|---|---|
| `Session` | 流水账原件 `log` + 双投影（`Surface` 聊天目录、`Header` 当前 `system+tools`），**只存 Canonical，不知 provider** | `Inbox.spliced` 账、`DialogueAgent.derive_messages` 皆读它 |
| `Inbox` | `next-turn/next-step` 双筐排队，`splice/claim` 均记 `spliced` 账 | 借 `Session.log` 持久化，重放还原 |
| `SystemPrompt` | 进程单例 + 每 `NPC` 一屉 `ScopedLayers`，`sections/contexts/variables/tools` 四格，`section` 分层、`assemble` 每步自检文件+懒建 | 屉是 `SystemPrompt` 私产，`DialogueAgent.preStep` 调 `assemble` 出 `system/tools` |
| `llm/`（LlmClient×3 + factory + router） | 唯一 `Canonical↔wire` 互译口：`openai/stub/echo` 统一 `generate()`；`factory` 读 env/config 选实现；`LlmRouter` 门面管热换内芯（swap 原子，旧芯收尾） | `DialogueAgent._step` 只调 `self.llm.generate` 一行 |
| `config_store` | config.json **唯一写侧**（读侧归 config_loader）：UI 白名单校验 fail-closed + 差分触碰（值未变不计入 effective）+ 坏 JSON 拒写 + **逐键**生效判定（`HOT_BLOCKS` 整块热 / `HOT_KEYS` 单键热，如 `network.request_timeout`） | 装配层 set_config 后按 `hot_keys` 逐键落地（llm 换芯 / RPC 超时改活），其余交 C# 重启 |
| `prompt_files` | prompts/ 目录服务：枚举/读/写/新建 NPC 人设；sections 只许改 `_SECTION_MAP` 已有文件，防路径穿越 | ChatHub 只路由，装配层注入 |
| `llm_adapter` | 底层形态工具：`canonical_to_openai` / `parse_openai_response` / `create_canonical_*` | 被 `OpenAILlmClient` 与 `DialogueAgent`（落盘调度）调用 |
| `tools/` | 9 工具 JSON Schema（查询 3 + 动作 6），`TOOL_ORDER` 排序在 `assemble` 内做 | `SystemPrompt` 全局屉持有 schema，`assemble` 排序进 header |
| `GameBridge` | 游戏侧抽象：`get_context`(L1 快照) + `call_tool`(执行)；`Stub`(内存桩) / `WsGameBridge`(真机 WS 单连接) | 唯一认识 `g.world` 的口子；`DialogueAgent` 每轮取 L1（轮内缓存+差分）、下发 tool |
| `DialogueAgent` | `turn→preStep→step` 循环机，一 `NPC` 一实例，**只编排不解释** | 持 `Session/Inbox/SystemPrompt/LlmClient/GameBridge`，`_execute_tool_calls` 走 bridge；turn 边界触发 `compactor.maybe`、暴露 `compact_now` |
| `compaction/` | 压缩：`meter` 量尺 + `pruner` 剪工具结果 + `compress` 摘要替换旧历史 | 复用 `LlmClient` 摘要；`DialogueAgent` turn 边界自动触发 / 手动 `compact_now`；不碰 system/context/inbox/L1 |
| `AgentLoop` | 管家：名册 `Map[id,Agent]`、`with lock` 保一 `id` 一活体、自动 `resume`、半截 `turn` 补 `interrupted`、`bridge/llm/compactor` 透传 | 唯一入口 `create/get/list/dispose`；`llm` 未注入时经 `factory` 兜底 |

---

## 二、实现流程详解

### 5. 总体流程

一句话：`用户发话进筐→preStep 领信+取 L1+装配 system/tools→开 turn/step 存历史→调 LlmClient.generate 单口→落盘 assistant→有 tool_calls 则走 bridge 执行回灌→循环至无工具纯文本收口`。

```mermaid
flowchart TD
    A[UI send 你好] --> B[Inbox.splice next-turn 记 spliced]
    B --> C[DialogueAgent._preStep: claim + bridge.get_context 取 L1 + assemble]
    C --> D[turn: append turn/start + user/message]
    D --> E[step: render_prompt + derive_messages + header 差分]
    E --> F[self.llm.generate system/messages/tools 一行]
    F -->|tool_calls| G[落盘 assistant(tool-call 块) + _execute_tool_calls → bridge.call_tool → tool/result 回灌]
    G --> E
    F -->|text| H[落盘 assistant(纯文本)]
    H --> I[append step/end + turn/end:completed]
```

对照：`llm_client/llm_stub` 双字段 + `if/elif/else` 三路已下沉到 `llm/` 各实现，`_step` 不再 `hasattr(client,"chat")` 猜后端，`echo` 由 `factory` 兜底——这正是 DSH `LlmRuntime`（管 adapter）与 Agent 编排分家的对齐。

### 6. 目录结构

```
agent_loop/
├── __init__.py          # 包入口：导出 AgentLoop/Session/Inbox/SystemPrompt + bridge/tools/llm
├── session.py           # Session 账本 + SurfaceManager 双投影
├── inbox.py             # Inbox 双筐 + spliced 账单持久化
├── system_prompt.py     # SystemPrompt 单例分层（sections/contexts/variables/tools + 文件化自检）
├── dialogue_agent.py    # DialogueAgent 循环机（turn/preStep/step，单口 llm.generate）
├── agent_loop.py        # AgentLoop 管家（名册/锁/续档/bridge·llm 透传）
├── llm_adapter.py       # Canonical ↔ OpenAI wire 底层形态工具
├── initiative.py        # NPC 主动开口：意图目录（正向7+负向4）+ 伪 user 文案包装
├── config_loader.py     # 分块 config：默认值 + 深合并 + llm 旧键兼容（唯一碰 config.json 的模块）
├── config_store.py      # config.json 唯一写侧（UI 白名单 + 差分触碰 + effective 判定；读侧归 config_loader）
├── log_setup.py         # 全局日志装配（唯一装管道者：文件/滚动/上下文/异常钩子/阶段慢告警；各模块只 get_logger）
├── contacts_store.py    # 通讯录后端：contacts.json 唯一写侧（手动好友幂等 add/remove）+ 会话索引（scandir 只取 mtime·TTL 缓存）
├── prompt_files.py      # prompts/ 目录服务：枚举/读/写/新建 NPC 人设（白名单 + 防穿越）
├── bridge.py            # GameBridge 协议 + StubGameBridge(WsGameBridge) 两实现
├── ws_channel.py        # 单 WS 全双工通道：WsServer(传输) + ChatHub(玩家消息/NPC主动开口编排)
├── history.py           # UI 历史投影：session 账本 → 对话 UI 时间线（只读纯函数，get_history 数据源）
├── llm/                 # LLM 适配层：base(协议+LlmResult) / openai / stub / echo / factory / router
│   ├── base.py          #   LlmClient.generate(..., on_token) 协议（流式可选）
│   ├── openai_client.py #   支持 on_token 流式（stream=True 逐 delta 回调）
│   ├── stub_client.py
│   ├── echo_client.py
│   ├── factory.py
│   └── router.py        #   LlmRouter 门面（swap 热换内芯；llm 块改动即时生效）
├── tools/               # 9 工具 JSON Schema + TOOL_ORDER
│   └── schemas.py       #   查询 3 + 动作 6，TOOL_ORDER
├── compaction/          # 压缩：meter(估算)/pruner(工具结果)/compress(历史摘要)
│   ├── meter.py         #   token 估算（CJK 中文计量）
│   ├── pruner.py        #   工具结果压缩（LLM 摘要优先 + head/tail 兜底）
│   └── compress.py      #   LLM 摘要压缩（配对平衡/决策/replace）
├── prompts/             # 文件化提示词（SystemPrompt 每步自检，改文件下把 turn 生效）
│   ├── sections/      #   harness_identity / world_basis / world_persona_rules / tool_usage
│   ├── personas/      #   default.txt / _suffix.txt / {npc_id}.txt（每 NPC 一份）
│   ├── traits/        #   {npc_id}.json（可选静态标签变量）
│   └── compaction/    #   压缩指令模板 default.md / {npc_id}.md（可热改）
├── persistence.py       # jsonl 读写 + 半截 turn 补 turn/end:interrupted
├── scripts/             # Python 端可运行入口
│   ├── server.py        #   平台常驻（C# 拉起目标；WsServer + WsGameBridge + ChatHub）
│   └── chat_cli.py      #   命令行对话 I/O（--ws 模拟 C# 客户端验证全链路；默认 Stub 离线）
├── csharp/              # C# MelonLoader 插件壳（C 端）
│   ├── AgentLoopBridge.csproj # .NET 4.7.2 库，引用游戏 MelonLoader\Managed 程序集
│   ├── ModMain.cs       #   插件入口（Init!/Destroy，Harmony + 调度 + WS 客户端 + 主动互动监测 + 拉起 Python）
│   ├── WsClient.cs      #   后台线程连 Python WsServer(:8766)，收 request/event、发 player_message/npc_initiative；
│   │                    #   另有三个 UI 传输口：UiEvent 事件出口 + SendRequest(C#→Python 读请求 RPC) + 确认窗 pending response 延迟补发（DramaGate）
│   ├── MainThreadDispatcher.cs # g.timer.Frame 每帧主线程调度
│   ├── GameContext.cs   #   request: get_context → L1 快照（含 same_grid 传音判定）
│   ├── UnitSnapshot.cs  #   单位档案统一采集（L1/brief 同源，按需采，实锤签名见 [附录 H.3](docs/APPENDIX.md#h3-反编方法工具链与实锤签名表)）
│   ├── NpcInitiativeMonitor.cs # NPC 主动开口触发检测（帧节流：冷却 + 关系网候选 + 意图派发）
│   ├── ToolExecutor.cs  #   request: call_tool → 9 工具分发 + 二阶段校验（social_relation 13 op / movement summon·teleport / world_ai_action 8 op / economy_item / trade / item_acquire）
│   ├── DramaGate.cs     #   确认窗延迟回灌闸门（TryDefer 登记槽位 / TryClaim 原子领票防迟到点击 / Resolve 补发 response + 超时自愈）
│   ├── ModIds.cs        #   本 Mod 私有 ID 段基址唯一事实源 = 编辑器分配的 MID（-803158451）+偏移；UICustomDramaDyn 窗 ID/选项 ID/DramaGate 撞窗 key 一体三用；与编辑器工程 ModExcel 三张配置表同源（改基址必须同步表）
│   ├── ModMainEntry.cs  #   官方桥入口包装（namespace MOD_Jgmg5L 反射入口，委托转发 AgentLoopBridge.ModMain；缺此层 Init 静默失效）
│   ├── UnitLookup.cs    #   npc_id 统一解析口（字符串 → WorldUnitBase 唯一入口：unitID 直查失败再全图按名匹配）
│   ├── RelationNetwork.cs #   玩家关系记录全集采集（十容器+好友簿+**仇人簿**+GetAllGoodRelationUnitID 含敌 + 同格单位；通讯录好友层①与主动开口候选同源）
│   ├── UnreadStore.cs   #   未读传音登记表（npc→最新文本/计数；当前对话对象跟踪；消息本体在 session，本表只是客户端标记）
│   ├── ChatLauncher.cs  #   统一「打开对话 UI」入口（NPC 面板按钮与通讯录行共用；AB/代码版分流，代码版顺带填立绘）
│   ├── ContactStore.cs  #   通讯录静态数据层（好友镜像/会话 mtime 索引/本地互动时间戳 + Changed 事件；与面板实例解耦）
│   ├── ContactDuty.cs   #   通讯录常驻行为层（订阅 UiEvent 分流 npc_reply：同格当面弹窗/异格未读+HUD 红点；list_contacts·list_sessions·add·remove_contact RPC→写 Store）
│   ├── DiagSwitches.cs  #   诊断哨兵开关族（F://agent_loop//_diag_*.txt 每 120 帧复查免重启：noPanels/noHudButton/noMonitor/lazyPanels/noPortraits/createOnly/noPatches/noPatchDrama/noMod/initiativeForce）
│   ├── ModAbRes.cs      #   AB 资源装载（直载 ab/ui 包 + 剥预制体自带 Canvas 三件套 + 注入 g.res.allRes 双 key；EnsureInjected 校验/补注——读档会重建资源表）
│   ├── DramaActivityHook.cs #   Harmony Postfix WorldSystemMgr.OpenMapDrama（剧情窗展示期旗标，供面板创建闸门避让）
│   ├── UnitActionHooks.cs   #   Harmony Postfix UnitActionRole{Give,Askfor,Invite,TeachSkill}.OnEnd（动作完成→UnitActionPending 收口，异常吞噬）
│   ├── UnitActionPending.cs #   原生 UnitAction 挂起登记表（实例指针→槽位+结果组装委托；赠送/讨要/邀约/传功真实结局回灌 DramaGate）
│   ├── UiComposer.cs    #   UI 组合公共件
│   ├── WsTransport.cs   #   WS 传输底层（收发帧编解码，与 WsClient 编排分层）
│   ├── PortraitService.cs#   立绘加载（游戏 PortraitModel 原生管线：单位 modelData 直渲，特殊剧情 NPC 走 dramaNpc 兜底）
│   ├── Launcher.cs      #   探测并拉起 scripts/server.py（状态锁只拉一次）
│   └── UI/              #   对话+配置+通讯录三面板与确认窗（纯 Unity 依赖，可被预览工程链接；逐文件职责/契约路径/数据流全量细节见 docs/ui-interception-spec.md）
│       ├── 对话：ChatWindowRefs / ChatUiBuilder(+ClickUtils) / ChatItemViews / ChatWindow / ChatPresenter / AbChatPanel / NpcPanelButton
│       ├── 图片输入：ImageInput（路径识别 + 剪贴板三来源 CF_HDROP/"PNG"/CF_DIB + 降采样 + base64；纯 CPU 行拷贝缩放，不走 Blit）/ ImageAttachPreview（输入框上方缩略图预览条）
│       ├── 配置：ConfigPanelRefs / ConfigUiBuilder / ConfigPresenter / AbConfigPanel / ConfigPanelOpener
│       └── 通讯录+公共：ContactPanelRefs / ContactUiBuilder / ContactPresenter / ContactPanelOpener / AbContactPanel（AB 宿主·方案 A 生命周期）/ NpcPanelAddContact / MapMainContactButton（HUD 传音簿按钮+未读红点）/ ShowDramaService（确认窗；DramaGate 在根目录）
├── UI 预览工程 / 开发工具
│   #  （原 ui_preview/ Unity 预览工程已于 2026-09-14 删除：确定走 **AB 资产固化**，
│   #    不再用编辑器预览。预制件的唯一来源与维护入口 = AB 工程
│   #    `…\鬼谷八荒\Mod\modFQA\资源修改教程\ResBuildABProject\Assets\Resources\UI\UIConfigAi.prefab`，
│   #    由 `scripts/dev/prefab_patch_config_groups.py` 幂等维护；改完重打 AB 见 docs/APPENDIX.md §E.2。）
│   └── scripts/dev/     #   开发期工具（**不进运行时**，删掉不影响 mod）：prefab_dump/prefab_inspect（预制件结构）、ab_ref_audit/ab_probe/ab_patch_priority（AB 覆盖审计 / **AB 内容探针** / UnityPy 二进制补丁）、prefab_patch_*（幂等预制件手术，含**滚动命中层**）、probe_gateway/verify_think_stream/derive_replay（网关与账本排障）；用法见 scripts/dev/README.md
├── config.json          # 分块配置：network / concurrency / initiative / compaction / storage / llm
├── docs/                # 权威文档（README 正文之外的全部）
│   ├── APPENDIX.md      # **附录全文**：A 桥契约 / B 日志规约与排障手册 / D 开发铁律与踩坑 / E UI 工作流 / G 功能设计底稿 / H 官方 API 与反编速查
│   ├── ui-interception-spec.md # **UI 唯一详细文档**：三面板全景/相互关系/游戏与 Python 关系/工具联动/确认窗/拦截/委托桥
│   ├── ui-skin-prompts.md # UI 换肤素材清单与生图提示词（施工中，原附录 C）
│   ├── 2026-09-10-world-input-freeze-postmortem.md # **「进游戏无法输入」事故复盘**：双根因（UI 登记泄漏+立绘 API 误用）/9 步对照实验/方案 A 修复/待验证清单
│   └── tool-result-contract.md # 工具结果契约（C# 只回结构化 data，叙述归 Python text_render）
└── tests/
    ├── test_close_reopen.py   # 并发锁/关窗留账续聊/半截 turn/分层隔离/per-NPC 文件懒建
    ├── test_bridge_tools.py   # 9 工具排序/L1 Context 差分/工具往返闭环/动作阈值
    ├── test_functional_flow.py # 全链路（stub）回归：新建/纯消息/工具多 step/重建续档
    ├── test_compaction_stage{1,2,3}.py # 压缩：计量/手动/自动触发
    ├── test_image_input.py    # 图片序列化 + 图片能力判定
    ├── test_llm_retry.py      # LLM 重试策略（可重试错/不可重试/零产出/已产 token 不重试）
    ├── test_ui_history.py     # project_ui_history 投影规则 + get_history request/response 往返
    ├── test_ws_channel.py     # WS 通道全链路（player_message→step/text_delta/npc_reply；request 双向）
    ├── test_contacts_store.py # contacts.json 读写往返 / 幂等 add·remove / 坏 JSON 兜底 / 原子写 / 会话索引
    ├── test_contacts_rpc.py   # 通讯录 RPC 路由（list/add/remove/sessions）+ 相识注入（首回合一次、resume 不重注）
    ├── test_lifecycle_rpc.py  # 生命周期 RPC：open_chat 激活+回放 / dispose_agent 冻结不落盘 / save_happened 固化 / load_happened 丢弃 / 懒落盘零残留
    ├── test_realm_and_identity_contract.py # 境界取行号+curGrade 自洽校验 / brief 走 DynInt / 开对话窗钉 unitID
    ├── test_unit_id_contract.py # 重名消歧：按名全扫+计数 / unit_id 回验身份且不回退 / 关系簿·搜索带 id / 歧义提醒置顶
    ├── test_ability_desc_contract.py # 功法技能说明（UIMartialInfoTool.GetDesc 单一入口/剥富文本/缺失不落键/逐槽一行）
    ├── test_log_setup.py      # 日志装配：幂等/落盘+上下文/热重配/慢告警封顶/异常钩子/职责哨兵
    ├── test_tool_contract_consistency.py # 工具契约三面一致性：schema 参数/枚举/上限 ↔ C# 实读实发 ↔ 渲染层读取
    ├── test_spar_choice_contract.py # 切磋确认窗：C# 走挂起/选项 id 与游戏配表对齐/渲染三态/attack 不受影响
    ├── test_tool_usage_contract.py # 工具文档去重护栏：长度预算/禁复述 schema 契约/承重墙政策清单/查询前置不被破坏
    ├── test_game_time_stamp.py # 玩家消息游戏时间戳：账面月标度(+1)/换算单一来源/拼在回显前/读不到日历不阻断/命令判据剥前缀
    └── test_initiative.py     # NPC 主动开口意图/守卫/节流
```

### 7. 文件级详解

**`session.py` — 职责：只追加的流水账（存 Canonical，不知 provider）**

* 常量：`SURFACE_TYPES={"user/message","assistant/message","tool/result"}`
* 格式：`SessionEvent{seq,time,type,data,surfaceOp?,sourceEventSeqs?}`
* 核心函数：`append(type,data,opts)` 排号入 `log` 并推 `SurfaceManager` 校验；`derive_messages()` 按 `surface.nodes` 原封返回消息对象；`has_open_turn()` 判尾部是否开转；`request_header()` 折叠最后一条 `request/header`
* 约定：`seq` 严格递增，`surfaceOp` 仅三类可带，`tool/result` 的 `sourceEventSeqs` 必须含对应 `tool/call` 的 `seq`

**`inbox.py` — 职责：排队筐**

* 格式：`state={"next-turn":[], "next-step":[]}`，`spliced{target,start,removedCount?,inserted,outcome?}`
* 核心函数：`splice/claim/clear/prepend/remove/replace` 均经 `session.append("agent/inbox/spliced")` 盖章后改内存筐；`claim(target,turn)` 先吃 `next-step` 全量再按需吃 `next-turn` 一条
* 约定：跨两筐 `id` 全局唯一，同筐重名抛错；重建时重放 `spliced` 还原筐

**`system_prompt.py` — 职责：单例分层（屉是私产）**

* 单例：`SystemPrompt.instance()` 全进程一个，`global_layer + scoped: Dict[scope, PromptLayer]`
* 一屉四格：`sections`(稳区) / `contexts`(易变 L1) / `variables`(插值 {var}/{{var}}) / `tools`(OpenAI schema)
* 核心函数：`assemble(scope)` **每步先 `_refresh_global_sections_if_changed()` 比文件 mtime/内容**、`scope` 未建则 `ensure_agent_layer` **懒建**，合并 global+scoped 后者覆盖，按 `order` 排 `sections`、按 `TOOL_ORDER` 排 `tools`；`render_prompt` 插值拼 `system`；`format_l1_context(raw)` 把桥的 raw 拼成**四段连贯中文（当前时间/自身/玩家/近况）**，近况为编号列表 `1. (N年M月) …；2. …`（≤10 条，C# 侧已限 6），其中**玩家段按 `same_grid` 成文传音模式**（同格=面对面交谈，异格=异地相隔神识传音），**自身段带气运**（`luck` born/added 只名，desc 含 UI 元文本不入上下文），`render_context_segment(name,text)` 单段成文供逐段差分发送
* 人设：`personas/{npc_id}.txt` 存在用，否则 `default.txt`+`_suffix.txt`；热改文件下一 `turn` 立刻生效（`_persona_file_cache` 保证只热更文件源、不覆盖手动屉）
* 约定：`ensure_agent_layer` 为唯一出口，`AgentLoop` 只调这一行；工具在 `__init__` 一次性注册进全局屉，`assemble` 合并排序

**`llm/` — 职责：唯一模型适配口（三路下沉）**

* `base.py`：`LlmClient(Protocol).generate(system,messages,tools,on_token=None) -> LlmResult`；`LlmResult{text, tool_calls:[{id,name,arguments:dict}], raw, usage?, provider?, model?}`；`on_token` 为可选逐 token 回调（供 WS text_delta 流式）
* `openai_client.py`：持 `base_url/api_key/model`，内部 `canonical_to_openai` → `to_thread` 调 `chat.completions.create`/低层 → `parse_openai_response`；`on_token` 非空时走 `stream=True`，后台线程迭代流、逐 delta 回调并累积 text/tool_calls（tool_calls 分片拼接后归一）；**可靠性**：网络/瞬时错误（连接层 + HTTP 408/429/5xx）自动指数退避重试（`retries` 次，config `llm.retries` 可配，0 关闭），认证/格式类错误立即抛；**流式已推送过 token 则不重试**（防玩家看到重复文本），开流即断（零产出）可重试；客户端可传入现成 openai client 或懒造；持 `supports_image`（三态：`None`/`True`/`False`），`False` 且有图时在 `generate` 明确报错而非静默丢图
* `stub_client.py`：注入桩 `fn(request)->result` 归一为 `LlmResult`；arguments 兼容 `str(JSON)/dict`
* `echo_client.py`：无 LLM 时把末条 `user` 回显，保证 turn 收口（final fallback）
* `factory.py`：`create_llm_client()` 显式参（含 `supports_image`） → env(`OPENAI_BASE_URL/API_KEY/MODEL`) → config.json(`base_url/api_key/model/image.enabled`) → Echo 兜底；图片能力按「显式 `supports_image` > config `image.enabled` > 内置 `MODEL_MODALITY` 白名单」解析，仍未知放行
* `router.py`：`LlmRouter` 门面（实现同一 `generate` 协议）——`swap(new_client)` 原子换芯，`generate` 入口瞬间绑定当前内芯引用（swap 后新调用走新芯、进行中调用旧芯收尾）；装配层建 router 注入全部消费者，llm 块配置改动只换芯，Agent/Compressor 零感知

**`llm_adapter.py` — 职责：Canonical ↔ OpenAI wire 底层工具**

* `canonical_to_openai`：system 首条、tool-result 拆多条 `role:tool`、assistant 拼 `tool_calls`；user 含 `image` 块时转为 OpenAI 兼容 `image_url` 多-part（`image` 不泄入 `string` content）
* `create_canonical_image_message(src)` / `content_has_image(content)`：图片输入建 Canonical 块 + 递归检测任意内容是否含图。`src` 接受**任意来源**（http 链接 / data URI / 文件路径 / bytes / 字节流 / PIL 截图），内部统一归一
* `image_to_data_uri(src, *, mime=None)`：**统一图片入口门面**——http/https 链接与 data URI 原样透传，文件路径读盘、bytes/流/`bytearray` 由 magic bytes 探 mime 后 base64、PIL 对象按 fmt 编码；返回可直接填 `image_url.url` 的字符串
* `bytes_to_data_uri` / `image_file_to_data_uri` / `pil_image_to_data_uri` / `_sniff_mime`：上述门面的底层转换与格式探测（Pillow 为可选依赖）
* `parse_openai_response` / `normalize_openai_tool_calls`：兼容 dict / object 两种 SDK 形状
* `create_canonical_assistant_message(text, tool_calls)` / `create_canonical_tool_result_message(call_id, text, is_error)`

**`tools/` — 职责：9 工具（查询 3 + 动作 6）**

* 查询域（只读，**均带 `text` 人话**）：`inspect_unit`(classes 多选：brief/stats/abilities/inventory/relationships/logs，无笼统 full；`text` 如 `姜萌 女 筑基/散修 魅力490…与你同处 关系道侣 好感200；背包灵石381…近况：邀唐炎论道被拒`，`relationships.married` 已译名) / `search_units`(filters 多条件找人，`text` 如 `找到1565人 展示前3：姜萌(道侣200 筑基/散修)…`) / `query_world`(events `text` 前5条月志或 `近来天下无事` + rankings `text` 如 `战力榜前5：1.蓟敏智 387w…`；sects/region 暂占位)
* 动作域（可写，**均带 `text` 人话**，需 L1/二阶段校验）：`social_relation`(13 op：好感直写 `好感提升1点(200→201)`；建/解除经 `ShowDramaService+DramaGate`，`text` 如 `已与姜萌结义`/`已完婚`，`BrotherBack` 双写兜底) / `movement`(`text` 如 `已将你召唤至姜萌身边`) / `world_ai_action`(8 op，`text` 如 `姜萌邀请你论道`，无 `1037` 编号) / `economy_item`(`text` 如 `已赠与你 灵石×1`) / `trade`(买卖，双方由模型指定，`text` 如 `云含把「青木箭」×2卖给了缪嘉歆，价 3000 灵石（云含灵石 0→3000，缪嘉歆灵石 5000→2000），当面交割。`) / `item_acquire`(`text` 如 `姜萌偷取了你的化瘀丹×14`，整栈语义)
* `TOOL_ORDER` 决定 header 内排序；工具注册在 `SystemPrompt.__init__` 一次性写入全局屉，`assemble` 只读合并

**`compaction/` — 职责：压缩（量尺 / 工具结果 / 历史摘要，各司其职）**

* `meter.py`：纯函数 token 估算，**按 CJK 中文计量**（中文 1 字≈1 token，非中文 4 字≈1 token）——对话全中文，直接套 ASCII 启发式会低估导致从不触发
* `pruner.py`：`ToolResultPruner` 压缩超预算 `tool/result`——**LLM 语义摘要优先**（复用注入 `LlmClient`，对话场景信息分布中段故不用纯 head/tail），**LLM 缺失/失败退确定性 head/marker/tail 折叠**兜底；`surfaceOp: replace` + 前插 `compaction/prune` 记账，重放安全
* `compress.py`：`Compressor` LLM 摘要压缩——先编排调用 **pruner** 剪冗余工具结果（可选注入，默认基于同 `LlmClient` 自动建）→ 配对平衡选段（不拆 open `tool-call`/`tool/result`）→ 保留尾原样 → 复用 `LlmClient` 摘要 → `replace` 为 checkpoint 节点 + `compaction/summary` 记账；fail-closed（摘要失败不改 session）；`cool_down` 防抖
* 压缩指令分层（09-12 定）：**历史摘要**可自定义——`prompts/compaction/{default,npc_id}.md`，作为 **system 整体下发**，`{npc_name}/{player_name}` 插值，改文件下把压缩即按新模板；**工具结果摘要不可自定义**——指令取自 `pruner.TOOL_PRUNE_INSTRUCTION` 内置常量（统一、不读 prompts/，UI 不暴露），用户消息只放 `<tool-result-raw>` 包裹的原文
* 历史摘要请求形状（09-11 修）：`system=压缩指令` + 单条 user 消息装 `<transcript>…</transcript>`（历史渲染成 `玩家：…`/`{npc}：…`/`[调用工具 …]` 行文）——此前 system 为空且历史原样当"活消息"发，模型会把历史末尾那条未执行的玩家请求当待办执行、纪要里吐 tool_call
* 触发：`DialogueAgent._turn` 边界自动 `compactor.maybe(session, request_header())`（此刻用户新消息在 inbox 未落面，只动上轮已落历史）；手动 `compact_now` 仅 `idle` 可调

**`bridge.py` — 职责：游戏侧抽象（唯一认识 g.world）**

* `GameBridge` 协议：`get_context(npc_id) -> {"text","raw"}`、`call_tool(name,args) -> {"success","data","error"}`
* `StubGameBridge`：内存微型世界，`seed_unit` 预置档案，脱离 `Game.exe` 跑通全链路；`_action_stub` 含二阶段校验——好感阈值 + **传音约束**（`world_ai_action` 的 spar/attack/双修/传功需目标 `same_grid`；异地则拒，combat_duel 已并入）
* `WsGameBridge`：真机桥，经 `WsServer.request`（get_context/call_tool）与 C# 单连接互通；C# 未连接/失败 → 兜底文本，对话不断

**`log_setup.py` — 职责：全局日志装配（唯一「装管道」者，只依赖标准库）**

* **唯一职责**：日志往哪写、长什么样、什么级别——`setup_logging/reconfigure`（建 handler + 按日滚动 + formatter）、`get_logger`（各模块唯一入口，名字归入 `agent_loop.*`）、`turn_scope`（contextvar 注入 npc/turn/step，并发回合不串台）、`stage`（阶段耗时打点 + **有界**慢告警）、`install_process_hooks/install_asyncio_hooks`（异常兜底）、`shutdown_logging`（收尾 flush）。
* **不做什么**：不认识任何业务模块、不记录业务事实、不做任何恢复动作；`stage()`/formatter 内部全 try 包死——**日志坏了也不拖垮回合**。
* **消费方规约**：业务模块只 `log = log_setup.get_logger(__name__)`；**绝不** `basicConfig`/`addHandler`/`setLevel`（哨兵测试强制）。
* **装配点**：`scripts/server.py`（启动即装）与 `scripts/chat_cli.py`（入口即装）；写回配置即 `reconfigure` 热重配（改 level 不必重启，且**不丢启动时的开关**）。
* **总闸（测试开/部署关）**：`--log off|on|debug|<LEVEL>` / `--no-log` / 环境变量 `AGENT_LOOP_LOG`；优先级 CLI > env > config；关闭 = 不挂 handler + 静默级 → 近似零开销。
* **平台坑**：相对路径**锚包根**而非 cwd（C# `Launcher` 以 `scripts/` 为 cwd）；C# 拉起时 stderr 不重定向 → 浮动控制台不是持久证据，文件才是。全量规约见 [附录 B](docs/APPENDIX.md#b-日志规约log_setup)。

**`ws_channel.py` — 职责：单 WebSocket 全双工通道（Python 作服务器）**

* `WsServer`（传输层）：`websockets.serve` 监听 `127.0.0.1:8766`（host/port/request_timeout 可配），接收循环常驻（回合中 C# 的 response 及时解 future）；`request()` 异步 RPC（req_id 配对，超时用 `request_timeout`）、`send_event()` 事件出口；player_message / npc_initiative 异步转推（不阻塞接收循环）
* `ChatHub`（编排层）：收到 `player_message` → `loop.get/create` → 注入 `on_step`/`on_token` 观察者 → `send` + `run_until_idle` → 收尾 `npc_reply`；收到 `npc_initiative` → `handle_initiative` 以伪 user 意图驱动 NPC 主动开口 → 收尾带 `initiative=true` 的 `npc_reply`。**可靠性兜底**：回合异常 → 推带 `error=True` 的兜底 `npc_reply`（友好文案只进 event 帧，**session 账本不落 assistant**，模型下轮看的是 `turn/end:error` 事实账）。**并发控制（调度职责在编排层）**：per-agent 锁（同 NPC 串行、不同 NPC 并行）+ 全局并发闸 `_turn_slots`（`max_concurrent_turns` 上限，防 LLM 并发爆炸），顺序恒为「先全局闸、再 per-agent 锁」，无死锁路径；initiative 快速失败（该 NPC 忙时锁外 `phase` 检查直接丢弃，不排队不积压）+ 全局节流 `min_initiative_interval`；失败兜底不崩
* 协议：`request/response`（**双向**：Python→C# 的 get_context/call_tool；C#→Python 的读请求经 `ChatHub.handle_request` 应答——get_history 等只查不建活体，`open_chat`/`dispose_agent` 两个生命周期 RPC 见下）+ `event`（player_message / npc_initiative / step / text_delta / npc_reply），一条连接全双工
* **开窗激活 / 关窗冻结（agent 生命周期挂在对话窗上，09-11 存档语义）**：`open_chat` = `loop.get or create` 激活（**恢复优先级：冻结舱 > 磁盘账本 > 全新**）+ 返回历史投影（唯一会建活体的请求路径），顺带撤销待销毁标记（关窗→立刻重开竞态）；`dispose_agent` = idle 即**冻结**（转 `_frozen` 内存舱，**不写盘、不清屉**），回合在跑只记 `_dispose_pending` 立即返回（不等待不占接收循环），`handle_message`/`handle_initiative` 的 finally 收尾统一冻结；**固化跟随游戏存档**：C# `EGameType.SaveData` 钩子 → `save_happened` RPC → `flush_all()` 全量固化进 jsonl（空账跳过）；C# `EGameType.IntoWorld` 钩子 → `load_happened` RPC → `discard_all()` 丢弃未固化增量并清空全部活/冻体（读档=回到存档时刻；关游戏不保存=增量随进程消失）；Python `shutdown` 前自动 flush（重启=世界延续）——详见 [附录 A](docs/APPENDIX.md#a-cpython-桥契约单-websocket-全双工)
* **相识注入**：`_inject_acquaintance_if_new`——全新 session（账本无 turn/start）首回合前补一条 `plugin` 源内部事件（"这是你们之间的第一段传音"），进账本模型可见、UI 投影滤除、不占 next-turn；resume 的会话不重注

**`contacts_store.py` — 职责：通讯录后端（contacts.json 唯一写侧 + 会话索引）**

* `ContactsService`：`list_contacts` / `add_contact`（幂等，已存在不改 added_at）/ `remove_contact`（幂等）；`storage_root/contacts.json`，tmp+`os.replace` 原子写，坏 JSON/异形 → 空表兜底；空/超长 npc_id 抛错（RPC 层转 ok:false）
* `list_sessions()`：scandir `storage_root` 只取 `*.jsonl` 文件名（unquote 还原中文名）与 mtime——**绝不读文件内容**；5s TTL 缓存；供通讯录「最近」Tab 排序
* 以中文名为键、跨周目持久——与 session/人设模型一致；server.py 以 `ContactsService(loop.storage_root)` 注入 ChatHub `contact_service`

**`csharp/` — 职责：C 端双手（MelonLoader 插件壳，初步搭建）**

* `AgentLoopBridge.csproj`：`.NET Framework 4.7.2` 库，`OutputType=Library`；引用 `MelonLoader\Managed` 下的 `Assembly-CSharp` / `Il2Cpp*` / `UnityEngine*` / `Newtonsoft.Json` 等（编译期路径：设环境变量 `GGBH_GAME_ROOT` 覆盖，或直接改 csproj 里的 `GameRoot` 默认值；**运行时**路径由 `ModPaths` 从 DLL 位置动态解析，与编译期无关）。真实游戏路径示例 `E:\SteamLibrary\steamapps\common\鬼谷八荒`
* `ModMain.cs`：插件入口。`Init`：`Harmony.PatchAll` → 起 `MainThreadDispatcher`（挂 `g.timer.Frame` 每帧）→ 起 `WsClient`（连 Python `WsServer`）→ 起 `NpcInitiativeMonitor`（同帧挂载；随后延迟 2s `get_config` 回填 initiative 参数经 `Configure` 热更）→ `Launcher.LaunchPythonOnce` 拉起 Python → **构建 UI**（`AB_UI` 模式：只建传音簿宿主 + 注册 `AbChatPanel`/`AbConfigPanel` 类型，对话与配置均由 AB 预制体承担（「UIChatAi」/「UIConfigAi」），启动不建代码版对话窗与代码版配置树——代码版 presenter 常驻会与 AB 面板 presenter 同帧双双轮询热键（GetKeyDown 对同帧所有 Update 为真），两次 toggle 互相抵消致 F11 关不上面板；代码版模式：`ChatWindow`+`ChatPresenter` 挂 `g.root`。均注册 IL2CPP 类型、优雅失败只记日志不拖垮对话链路）；`Destroy`：停 WS、停调度。`OnIntoWorld` 由游戏侧按官方约定调 `Init`
* `WsClient.cs`：后台线程 `ClientWebSocket` 连 `ws://127.0.0.1:8766`（断线重连）；收 `request`（get_context/call_tool → `Dispatcher.Enqueue` 主线程 → `GameContext`/`ToolExecutor` → 回 response）与 `event`（step/text_delta/npc_reply → 主线程）；`SendPlayerMessage` 发玩家消息、`SendInitiative` 发 NPC 主动开口事件。**UI 传输双口（只加不改业务）**：① `event Action<JObject> UiEvent` —— event 帧经 `Dispatcher` 主线程回调后对外广播，Presenter 订阅即得 step/text_delta/npc_reply（本类不依赖任何 UI 类型）；② `SendRequest(method, parameters, onResponse, timeoutMs)` —— C#→Python 方向 RPC 读请求（get_history 等），req_id 配对 + 超时合成 `{"ok":false}` 错误帧，回调恒走主线程
* `MainThreadDispatcher.cs`：主线程调度器，`g.timer.Frame(...)` 每帧 `OnUpdate` 消费队列——**硬约束：任何触及 `WorldUnitData/UnitInfoData` 的调用必须在主线程**，后台线程只入队
* `GameContext.cs`：`GetL1(npcId)` 用官方 `GGBH_API` 真实读取，回结构化 `raw` 供 Python `format_l1_context` 成文；**`raw.relations.player.same_grid` = 与玩家是否同格**（传音模式判定）；游戏侧失败回兜底文本
* `UnitSnapshot.cs`：**单位档案统一采集（L1/brief 同源，按 `classes` 按需采）**：基础字段（无条件，L1 用）+ **气运（`brief` 门控：`Has("brief")` 才采，inventory 不再混入）** + abilities/**inventory（分类背包：按 `PropsType` 枚举分 丹符/书籍/装备/材料/其他，类内按单价 `worth`（官方悬停出售价）降序，小背包≤12种全显、大背包每类 top-N+杂项聚合 misc，灵石走 money 不再双列）**/relationships/logs/stats；`logs` 合 `logs+subLogs` 双段 `GetLogString()` 并清 `@q_名|id@`/`&` 编码，`relationships.married` 已译名，关系簿与 `RelationNetwork` 候选范围对齐（十容器含 `brotherBack/parentBack/childrenBack` + 好友簿 `friend_units` + 仇人簿 `enemy_units`；`human_value` 传玩家 unitID 查 NPC 对玩家人情——原传自身 ID 恒 0），`UnitNames` 跳过关系簿空串占位条目（真机曾出 `["",""]`）；全部签名反编实锤，单块 try/catch 兜底不崩
* `NpcInitiativeMonitor.cs`：NPC 主动互动触发检测（感知侧，只在主线程跑）。日节拍（WorldAddDay + Frame 兜底）+ 状态闸（战斗/确认窗）、每 NPC 双冷却、一轮只触发一个；**候选 = `RelationNetwork` 关系记录全集（十容器+好友簿+仇人簿+GetAllGoodRelationUnitID 含敌）+ 手动通讯录好友 + 同格所有人（陌生人当面可搭话）**，门槛：非通讯录成员仅同格可发起；意图按亲密度派发（负值含仇人走负向意图），经 `SendInitiative` 发出；参数经 `Configure` 热更（ModMain Init 后从 Python `get_config` 拉取每日概率/双冷却/低好感阈值）。
  **诊断强制触发（09-12，观测加速器）**：`_diag_initiative_force.txt` 存在即进入强制模式（`DiagSwitches` 每 120 帧复查，增删文件即时生效，不必重启游戏/Python），**只放宽"时间"四闸**（日节拍/同日夜守卫/每日概率/单人双冷却）→ 改为按现实秒节拍（`Time.unscaledDeltaTime` 累计，默认 20s）持续触发，不必推进游戏日；候选集、通讯录/同格门槛、意图派发、当面确认窗全部仍走真实链路（`FireOne` 与日节拍路径共用，防诊断路径悄悄偏离真路径）。文件内容可选：`interval=20`（节拍秒）/ `name=林婉清`（只对该 NPC）/ `intent=malice`（指定意图，非法值回落真实派发）/ `#` 注释。强制模式**不写冷却表**（关开关不留痕），事件帧带 `debug:true` → Python `handle_initiative` 据此豁免 `min_interval` 全局节流（否则默认 300s 会把强制节拍削成"看着像没生效"），source 落账带 `debug` 标记可审计。测试预设与该模式的完整用法见 README「自主交互系统怎么测」一节
* `RelationNetwork.cs`：玩家关系记录全集统一采集（通讯录好友层①与主动开口候选同源）——①关系十容器 ②好友簿 `GetIntimUnitData(false,false).friendUnits` ③**仇人簿同源 `.enemyUnits`**（types2.txt:82-84 实锤）④关系记录簿兜底 `GetAllGoodRelationUnitID(true,true)`（第二参即"含仇人"，types27_relation.txt:153）⑤`CollectSameGridUnits` 同格所有人（O(N) pointX/pointY，游戏同款判定）；按 unitID 去重、排玩家排空名，单字段 try/catch 不拖垮全候选
* `UnreadStore.cs`：未读传音登记表（静态，内存态）——`npc → {最新文本/时间/计数}` + `ActiveChatNpc` 当前对话对象跟踪；`NotifyChatOpen/Closed`（代码版 ChatPresenter 与 AB 版 AbChatPanel 挂钩）打开即清未读；消息本体在 session 账本（主动开口回合正常落账），本表只是客户端"没看过"标记，不持久化
* `ChatLauncher.cs`：统一「打开对话 UI」入口——NPC 面板「AI 对话」按钮与通讯录行点击共用；AB 模式 `g.ui.OpenUI("UIChatAi")+AbChatPanel.InitData`（不回退代码版），代码模式 `PresenterInstance.OpenForNpc` 并顺带填 `ChatWindow` 左右立绘（`PortraitService`）
* `ToolExecutor.cs`：`Execute(name,args)` 主线程分发 9 工具，返回 `{"success","data","error"}`；**二阶段校验**（同格 `pointX/Y`、好感 `GetIntim`）。落地明细：`world_ai_action` 8 op（战斗 spar/attack 实锤、双修/论道/邀约/疗伤/提升心情 = `WorldUnitAIAction1031/1037/1044/1034/1041`、传功 = `UnitActionRoleTeachSkill` 带 `SkillCanTeach` 校验 + `TeachType.Teach`）；`social_relation` 13 op（好感直写 `AddIntim/AddHate`；建关系/求婚/解除经 `ShowDramaService` 弹原生确认窗——玩家点选才执行真动作，结果经 `DramaGate` 延迟 response 回灌；求婚实证 `UnitActionRoleMarry` 真机 NRE，改走互写 married + `OpenDrama(22201)` 弹游戏自带成婚剧情）；`inspect_unit` 七块（`UnitSnapshot`）、`search_units` filters 全图找人、`query_world` events/rankings 四榜 + places（全图城镇/宗门：`GetBuilds()` 扫 `MapBuildTown/MapBuildSchool`，name/区域 `gridData.areaBaseID`/坐标 `GetOrigiPoint()`）；`economy_item`（灵石 `RewardPropMoney/CostPropItem(10001)` 转账 + 普通道具 `UnitActionRoleGive` 多道具 List）；`item_acquire`（偷窃/讨要 `UnitActionRoleStealItem/Askfor`）；`movement`（召唤/传送/travel——NPC 自主前往指定城镇宗门 `UnitActionMoveNPC(任意坐标)`，目标名 `ResolveBuild` 精确>包含+region 消歧，神识传音 IL 实证）
* `Launcher.cs`：`LaunchPythonOnce` 探测 `python/python3/py`，`Process.Start` 拉起 `scripts/server.py`；`ServerCandidatePaths` 含回退路径，状态锁保证只拉起一次

**`csharp/UI/` — 职责：三面板视图与控制器（概览；逐文件职责、契约路径、数据流、游戏/Python 关系的全量细节见 `docs/ui-interception-spec.md` §5–§7）**

* **对话 UIChatAi（F9 / NPC 面板按钮 / 通讯录行）**：`ChatPresenter`（唯一认 WS 对话协议：UiEvent 事件翻译、open_chat 激活+历史回放、dispose_agent 冻结——固化由存档事件驱动）+ `ChatWindow/ChatItemViews`（流式气泡·回合折叠组·内心思量区·立绘槽位；delta 逐帧合并/行上限 200/智能滚底）+ `AbChatPanel`（AB 宿主，已部署、按需求无代码回退）+ `NpcPanelButton`（Harmony 注入面板按钮）
* **配置 UIConfigAi（F11 / 对话窗 ⚙ 带 npcId）**：`ConfigPresenter`（get/set_config + list_prompts(desc)/read_prompt/write_prompts/create_persona + 数字严格校验 + effective 提示 + HoverTip 悬停气泡：文件说明 Python 下发、参数说明内置）+ `AbConfigPanel`（AB-only：失败报错不回退）+ `ConfigPanelOpener`（统一路由，编译期分流）
* **通讯录 UIContactAi（F10 / HUD「传」字圆钮）**：`ContactPresenter`（好友=关系全集∪手动 RPC、最近=互动时间序、本地搜索、行点击→ChatLauncher 先藏自己、主动传音三分流：直播已读/同格当面弹出/未读登记）+ `AbContactPanel`（AB 宿主：**按需创建**，`GetUI` 复用优先，关闭走 `CloseViaManager`=`g.ui.CloseUI` 交游戏——09-10 方案 A；好友镜像/未读分流在 `ContactStore`/`ContactDuty` 常驻层）+ `ContactPanelOpener`（路由）+ `NpcPanelAddContact`（加好友按钮）+ `MapMainContactButton`（HUD 头像区按钮注入）
* **图片输入 `ImageInput`（09-13）**：两个取路径入口 —— ① 输入框文本里的图片路径（整行 / 行内盘符尾段 / 「复制文件地址」的带引号形式，路径**可含空格**故按行判定、绝不按空白切词）；② 资源管理器 `Ctrl+C` 图片后在输入框 `Ctrl+V`（`ChatPresenter.Update` 读剪贴板 `CF_HDROP`，非图片文件时原样放行给 InputField 走普通文本粘贴）。编码：`LoadImage` → 长边 ≤1024 降采样 → PNG（超 1.5MB 退 JPG q85）→ base64 data URL；尺寸与体积都在预算内时**原字节直发**。降采样刻意用**纯 CPU 行拷贝**而非 `Graphics.Blit`：发给模型的图用户在 UI 上看不到，Blit 的方向依赖 `_MainTex_TexelSize.y` 约定，翻转了就是静默错误。
* **公共件**：`ClickUtils` 三步写法（各面板共用）、`MakeSlicedSprite/MakeCircleSprite` 精灵生成器（防呆钳制）、`ShowDramaService`+`DramaGate` 工具确认窗（根目录）、`ChatLauncher` 统一对话入口（根目录）、`RelationNetwork`/`UnreadStore`（根目录数据与缓存件）
* 三面板互跳与未读联动：通讯录行/横幅 → ChatLauncher → 对话；对话 ⚙ 携带 npcId → 配置；npc_reply(initiative) 由通讯录分流（关系图见 spec §1.2）

**`scripts/` — 职责：Python 端可运行入口**

* `server.py`：平台常驻。`get_loop()` 持全局 `AgentLoop` 单例（`bridge=WsGameBridge(WsServer)`，WS 端口可用环境变量 `AGENT_LOOP_WS` 覆盖，默认 `8766`；`llm` 走 `factory`：env→OpenAI，否则 Echo 兜底）。`_idle()` 启动 WS 通道（注册 `ChatHub`）并常驻，不内置对话驱动（驱动在各 I/O 端）；**这是 C# 端 `Process.Start` 拉起的脚本**
* `chat_cli.py`：命令行对话 I/O。`--ws` 模式：以**模拟 C# 客户端**连 server.py 的 WS 通道，发 `player_message`、实时打印 step / 逐字 text_delta / npc_reply（验证「Python↔C# 全双工 + 流式」）；默认模式用 `StubGameBridge` + `StubLlmClient` 离线自闭环。**同时是游戏内对话 UI 的对照验证端**（同样的 player_message/step/npc_reply 协议，UI 端 `csharp/UI/` 已实装）

> 路径约定：开发机布局里**项目根即 `agent_loop` 包自身**（根下的 `*.py` 就是包模块），`scripts/*.py` 因此向 `sys.path` 插入其**父目录**，使 `agent_loop` 作为包名解析（而非根下的 `agent_loop.py` 文件）。**发行版布局不同**（代码进了自包含 exe，`<Mod根>/ModAssets/AgentLoopServer.exe`），故两处入口的引导改为「向上找含 `agent_loop` 包的那一层」而非写死层级——判据同时认 `.py` 与 `.pyc`。数据文件（config.json / prompts / logs）的位置统一由 `paths.py` 回答，见 [docs/PACKAGING.md §3](docs/PACKAGING.md)。

**`dialogue_agent.py` — 职责：循环机（只编排不解释）**

* 收信：`send(text, source=None, images=None)` 进筐（`source` 为可选落账标记，如主动开口的 `{kind:"initiative",intent,reason}`）；`run_until_idle()` 跑到无 pending 为止——**异常时 phase 自愈复位 idle**（账本已由 `_turn` 闭合为 `turn/end:error`），异常照抛让编排层决定兜底，避免残留 running 误丢 NPC 主动开口
* **图片附件（09-13）**：`images` 只登记进**内存侧表** `_turn_images{消息id: [dataURL]}`，账本 content 里只有 `text`（C# 已拼好 `[图片：xx]` 占位）。`_step` 用 `_attach_turn_images(derive_messages())` **浅拷贝**挂图 —— `derive_messages()` 返回的是账本事件 data **本体**，原地改就等于把 base64 永久写进 `session.log`。收口按「本回合实际领到的 id」逐个 `pop`（**不能 `clear()`**：一次 `_pre_step` 只吃一条 `next-turn`，连发两条带图消息时第二条还在筐里等下一回合，全清会静默丢图）。不变量由 `tests/test_image_attachment.py` 钉住（含变异测试）。
* `_pre_step`：领信 → `bridge.get_context` 拿 raw → `format_l1_context` 成**四段（当前时间/自身/玩家/近况）** → **逐段独立差分**写 `runtime:time/self/player/recent`（仅变化的段才 replace）→ `assemble` → **逐段差分发送**（对标 DSH `RuntimeContextProjection`，仅变化的段各自追加 user 消息，保留 `_retained_ctx`）
* `_step`：`render_prompt` + `derive_messages` + **header 差分**（文本没变不落盘）→ **`self.llm.generate(system,messages,tools,on_token=...)` 一行**（`on_token` 可选：LLM 流式产 token 时回调，供 text_delta 转发）→ `create_canonical_assistant_message` 落盘（text+tool-call 同块）→ 有 `tool_calls` 则 `_execute_tool_calls`
* `_execute_tool_calls`：`tool/call → bridge.call_tool → tool/result`，`asyncio.Semaphore` 限并发（对标 `maxParallelToolCalls`），`sourceEventSeqs` 关联 tool/call
* 观察者钩子（DI 注入，可选，不传零影响）：`on_step` 每完成一步吐事实（`tool_call`/`tool_result`/`text`，Agent 不解释不渲染）；`on_token` 透传给 LlmClient 逐 token 回调
* 生命周期：`cancel` / `dispose`（清筐 + 清自身屉），`phase: idle/running`

**`agent_loop.py` — 职责：管家（只管名册+锁+续档）**

* `create(npc_id, cwd, provider, model, bridge, llm)` 内 `with _op_lock` 查重 → `load_session` 存在则 `resume` 否则新建 → `_publish`（`ensure_agent_layer` + 建 `DialogueAgent` + 进名册）
* `bridge`/`llm` 由管家透传，未传用主机默认；`llm` 未显式注入走 `factory`（env→OpenAI，否则 Echo）
* `get/list/dispose`；`dispose` 先 `save_session`（空账本跳过）再 `agent.dispose()` 毁活体留账；`create`/`dispose` 对空账本均不写盘（懒首落，配合 open_chat 开窗激活的浏览零残留）
* 约定：`npc_id` 中文经 `urllib.parse.quote` 安全化为文件名，一 `id` 一活体，重复 `create` 抛 `already registered`；屉的生命周期归 `SystemPrompt`

**`persistence.py` — 职责：落盘与补**

* 格式：首行 `{"session":{"id":..., "header":...}}`，余行一事件一行 `jsonl`，路径 `~/.sessions/{quote(npc_id)}.jsonl`
* 核心函数：`save_session/load_session`；`_apply_interrupted_closers` 若尾部 `turn/start` 无 `turn/end` 则补 `step/end? + turn/end:interrupted`
* 约定：`load` 即重放，重放前先补，保证 `has_open_turn` 不卡死

### 8. 数据流串联

**脚本内（脱离游戏，stub 全链路）**：

```
中文 id "林婉清" → quote → %E6%9E%97%E5%A9%89%E6%B8%85.jsonl
send("你好") → spliced{target:next-turn, start:0, inserted:[userMsg]} → log seq0
_preStep claim → bridge.get_context 拿 raw → format_l1_context 成 当前时间/自身/玩家/近况 四段 → 逐段写 runtime:time/self/player/recent → assemble → 逐段差分发 user 消息
step: header 差分 + derive_messages → self.llm.generate(...)（openai/stub/echo 之一）
  工具路径：落盘 assistant(tool-call) → _execute_tool_calls → bridge.call_tool(inspect_unit) → tool/result 回灌 → 下一 step
  文本路径：落盘 assistant(text) → turn 收口
```

**真机（Python↔C# 双进程，桥契约见 [附录 A](docs/APPENDIX.md#a-cpython-桥契约单-websocket-全双工)）**：

```
玩家消息（游戏 UI / chat_cli --ws）→ WS event player_message → Python ChatHub
  ├→ loop.get/create("林婉清") → agent.send → run_until_idle
  │    ├→ 回合内 request get_context  → C# WsClient → 主线程 GameContext.GetL1 → response {"text","raw"}
  │    ├→ （模型产 tool_calls）request call_tool → C# 主线程 ToolExecutor.Execute（二阶段校验）→ response
  │    └→ 回合中推流：event step（tool_call/tool_result/text）+ text_delta（逐字）→ C# 主线程对话 UI
  └→ 收尾 event npc_reply → C# 主线程显示最终回复
```

### 9. 关键配置与测试

* 配置：**分块 config.json**（`config_loader` 读取，缺键用代码内默认）——`network`(host/port/request_timeout) / `concurrency`(max_concurrent_turns 全局回合并发上限 / max_parallel_tools 工具并发) / `initiative`(min_interval 主动开口节流) / `compaction`(压缩阈值/保留尾/窗口/防抖/重试) / `storage`(storage_root 默认 `~/.sessions`) / `llm`(base_url/api_key/model/image.enabled/retries 重试次数/timeout 单次请求超时秒，null=SDK 默认) / `logging`(**不入 UI 白名单**，手改文件：level/file/rotation/backup_days/console/console_level/slow_ms/slow_escalations/trace_frames/capture_root/modules/quiet_libs——写回任意配置项即热重配，改 level 免重启)；**配置 UI 写侧**：`config_store` 白名单写回（llm/network/initiative/compaction 四块白名单键），`set_config` 返回 `effective`（none/hot/restart/mixed），llm 块经 `LlmRouter.swap` 热切换即时生效，network 等重启生效；**提示词写侧**：`prompt_files` 枚举/读写/新建 NPC 人设（`ChatHub.handle_request` 路由 7 类 method：get_config/set_config/list_prompts/read_prompt/write_prompt 单条兼容保留/write_prompts 批量`{files:{path:text}}`（配置 UI 实际走它）/create_persona，游戏内 F11 面板即走这些 RPC）；环境变量优先于 config（`AGENT_LOOP_WS` 覆盖 `network.port`、`OPENAI_*` 覆盖 `llm`）；`prompts/` 文件即真相，改文件下一 `turn` 生效；**日志总闸**另可经 CLI `--log/--no-log/--log-level` 或环境变量 `AGENT_LOOP_LOG` 覆盖（优先级 CLI > env > config，用于"测试开日志/部署关日志"）
* 运行入口：脱离游戏用 `StubGameBridge`（测试里注入）；真机三步——① 进游戏让 C# 插件 `Init`（或手动先 `python scripts/server.py`，起 WsServer 于 config `network`），② C# `WsClient` 连入，③ `python scripts/chat_cli.py --ws --npc 林婉清` 以模拟 C# 客户端验证全链路。依赖：`pip install -r requirements.txt`（`websockets`）。`csharp/AgentLoopBridge.csproj` 编译前需把 `GameManaged`/`GameRoot` 改成你本机 `鬼谷八荒` 安装路径；C# 端连接地址暂时与 Python `network` 手动保持一致（下一阶段再接入 config 读取）
* C# 编译状态：**已首次编译通过**（`AgentLoopBridge.csproj` → `bin\Debug\AgentLoopBridge.dll`；本机 VS BuildTools + 游戏 `MelonLoader\Managed` 程序集，`GameManaged`/`GameRoot` 已指向 `E:\SteamLibrary\steamapps\common\鬼谷八荒`）。曾修复的构建阻塞：① csproj 缺 `NpcInitiativeMonitor.cs` 编译项；② `0Harmony` 引用路径应为 `$(GameRoot)\MelonLoader\0Harmony.dll`（非游戏根）；③ `ModMain._pythonStarted` 改 `internal`（Launcher 同程序集访问）；④ `Debug` 命名歧义（`System.Diagnostics` vs `UnityEngine`，改全限定 `UnityEngine.Debug`）；⑤ `GetRelation() ?? ""` 报错（返回枚举，改 `.ToString()`）；⑥ Launcher 的 C#8 `using` 声明（补 `LangVersion=latest`）。注意：csproj 声明 `.NET Framework 4.7.2` 对齐 MelonLoader 0.5.4，本机仅装 4.8.1 目标包故以 `v4.8.1` 验证，**正式部署前需确认 4.7.2/4.8.1 兼容性**
* 图片输入：`config.json` 加 `"image": {"enabled": false}`（或显式 `supports_image`）声明是否支持多模态；请求带图时 `canonical_to_openai` 自动转为 `image_url`，若模型明确不支持（`supports_image=False`）则报清晰错误；`tests/test_image_input.py` 覆盖序列化与能力判定
* 脱离游戏模拟 `tests/`：
  | 用例 | 断言 |
  |---|---|
  | 同号并发建 | `with lock` 只成一个，另一抛 `already registered` |
  | 关窗冻结再开续聊 | `dispose` 后 `get` 为空、磁盘无文件（未固化）、再 `create` 从冻结舱复活历史仍在 |
  | 半截 `turn` 崩后补 | 手造 `turn/start` 无 `turn/end` 的 `log`，`load` 补 `interrupted` |
  | 分层隔离 | `林婉清` 与 `张三` 各屉 `persona` 互不串，热改一方不影响另一方 |
  | per-NPC 文件懒建 | `personas/{npc_id}.txt` + `traits/{npc_id}.json` 变量插值，`assemble` 不经 create 也能长屉 |
  | 9 工具注册排序 | `assemble` 的 `tools` 与 `TOOL_ORDER` 一致 |
  | L1 四段式 | raw → 当前时间/自身/玩家/近况 四段**连贯成句**，各自独立 `runtime:*` context |
  | L1 逐段差分 | 只改玩家段时自身段屉不重写、已发段保留（`_retained_ctx` 逐段） |
  | inspect 分页 + search 过滤 | `log_page` 分页、`search_units` 条件过滤正确 |
  | tool_calls 往返闭环 | 桩模型调 `inspect_unit` → bridge → `tool/result` 回灌 → 纯文本收口 |
  | 动作阈值二次校验 | 好感 180 结缘通过 / 120 拒绝 |
  | 压缩：meter 中文计量 | 中文 1 字/token，ASCII 4 字/token，不低估中文对话 |
  | 压缩：pruner 修剪 | 超预算 `tool/result` 剪头尾 + `save→load` 后 surface 一致 |
  | 压缩：手动压缩 | `compact_now` 摘要替换旧历史 + 保留尾 + checkpoint 节点 + 重放一致 |
  | 压缩：配对平衡 | 悬空 `tool-call` 跨切割线拒绝，session 不变 |
  | 压缩：自动触发 | turn 边界超阈触发；压缩后新回合自动带出最新 L1 context；`cool_down` 防抖 |
  | 图片：序列化 | `canonical_to_openai` 把 `image` 块转 `image_url` 多-part，不泄入文本 string |
  | 图片：能力判定 | `supports_image=False` + 带图 → 报错且不发请求；纯文本放行；`None` 放行 |
  | WS：通道+流式 | `ChatHub` 收 `player_message` → `step(tool_call/tool_result)` + `text_delta` + `npc_reply` 全链路 |
  | WS：桥往返 | `WsGameBridge` 经 `request/response` 取 `get_context`/`call_tool`；C# 不应答时超时兜底不抛 |
  | WS：主动开口 | `npc_initiative` 事件 → `handle_initiative` 伪 user 意图驱动 → 带 `initiative=true` 的 `npc_reply` |
  | 主动开口：意图+回合 | 意图目录 11 种 + 文案包装；伪 user 消息带 `source=initiative` 落账、开场白走正常 assistant 路径 |
  | 主动开口：守卫 | running 时静默丢弃；全局节流（30s）内二次开口丢弃 |
  | 传音：Context 成文 | `same_grid=False` → 玩家段"神识传音"；`True` → "面对面" |
  | 传音：动作约束 | 异地拒切磋/双修/传功（二阶段校验），放行论道；同格放行 |
  | 并发：per-agent | 不同 NPC 回合并行（耗时差验证）；同 NPC 串行（不重叠） |
  | 并发：全局上限 | `max_concurrent_turns=1` → 峰值并发恒 1 |
  | 主动开口：快速失败 | 同 NPC 回合进行中 → initiative 直接丢弃不投信 |
  | config：加载 | 深合并（部分覆盖）/ 缺文件·坏 json → 默认兜底 / llm 旧扁平键归块 / 模板不污染 |
  | 日志：log_setup 装配 | 幂等不重复挂 handler / 落盘含 `[npc t{turn} s{step} stage]` 上下文栏 / 级别热重配 / `enabled=false` 不建文件 / 相对路径锚包根（不锚 cwd）/ 慢告警按 1x·2x 触发且**次数封顶** / 未捕获异常留痕 / **职责哨兵：无模块自行配置 logging** |
  | LLM：重试 | 可重试错后成功（2 次调用）/ HTTP 400 立即抛 / retries=0 只 1 次 / 耗尽抛最后错 / 流式零产出可重试 / **流式已产 token 不重试** |
  | 可靠性：兜底 | 回合失败 → `npc_reply` 带 `error=True`（友好文案只进 event）；账本不落 assistant（`turn/end:error` 闭合） |
  | 可靠性：自愈 | 失败后 `phase` 复位 idle；下一次 NPC 主动开口不被误丢；账本只含成功回合 |
  | UI 历史回放 | `get_history` 投影：plugin 源（L1 context/压缩 checkpoint）滤除、initiative 伪 user 转分隔条、半截 `turn` 截断、`max_turns` 取尾、`turn_error` 项；WS request/response 往返（未知 NPC 空 / 未知 method `ok:false`）+ 回合完成后同连接取回 |
  | LlmRouter 热换 | swap 后新调用走新芯 / on_token 透传 / 进行中调用旧芯收尾 / None 拒绝 |
  | config_store 写回 | set/get 往返 / 白名单外键与块拒绝 / 坏 JSON 拒写 / effective hot·restart·mixed·none / 无改动不重写文件 |
  | prompt_files 服务 | 枚举读写往返 / 新建白名单（分组×扩展名）/ 路径穿越拒绝 / create_persona 底稿复制 + 重复与非法 npc_id 拒绝 |
  | ChatHub 配置路由 | 6 method 路由（set_config 兼容 `{config:{...}}` 与平铺）/ 未注入服务明确报错 |
  | 通讯录：contacts_store | add/list/remove 往返 + 幂等（重复 add 不改 added_at）/ 空·超长 npc_id 拒绝 / 坏 JSON·异形兜底 / tmp+replace 原子写无残留 |
  | 通讯录：RPC 路由 | list_contacts/add_contact/remove_contact/list_sessions 往返；缺 npc_id 与未注入服务明确报错；list_sessions 只认 *.jsonl + unquote 中文名 + TTL 缓存 |
  | 通讯录：相识注入 | 全新 session 首回合前 plugin 事件恰好一条（surfaceOp=append 进账本模型可见、UI 投影滤除、不占 turn）；resume 会话不重注 |
  | 生命周期：open/dispose | `open_chat` 未知 NPC 建活体（空账不落盘）+ 幂等复用 + 撤销待销；`dispose_agent` idle 即**冻结**（不落盘）、busy 记名回合毕自动冻结、unknown 拒绝；重开从冻结舱/账本恢复、历史完整回放；`get_history` 不建活体；空账开关窗零残留；`save_happened` 固化 / `load_happened` 丢弃 |

#### 9.1 上线前真机自测清单（09-13）

**用法**：按 P0 → P1 → P2 顺序打勾；P0 任一失败就停下先修，别往下测。已单独验过的（自主交互、主动开口回合、多数工具）只留 P0 的回归项，不重复展开。括号里的 `日志行`/`日志锚点` 是出问题时该去 `Player.log`（C# 侧）或 `logs/agent_loop.log`（Python 侧）抄给我的证据行。

**P0 · 冒烟（≈10 分钟，先跑这条）**

- [ ] 启动：`Player.log` 首行 `[Build] 0913-1045 …` + `[ContactDuty] 已附着`，全程无 `Exception`/`Crash`
- [ ] 进世界：`load_happened：存档命名空间 → <玩家名>/<unitID>`；`[DiagSwitches] … initiativeForce=False …`（确认诊断哨兵已下）
- [ ] WS 通：Python 日志 `C# 已连接（本地 :8766）`；F11 面板能读出当前配置（说明 RPC 通）
- [ ] **F9 开关对话窗 ×3**：每次都出现、位置尺寸正常；**关窗后人物能走动/能交互**（09-10 世界输入事故回归点）
- [ ] HUD「传」钮在（`HUD 传音簿按钮已注入…尺寸=(40×42)` + `未读角标已建：真圆+白描边…`）
- [ ] 存档一次 → Python 日志 `save_happened`；退出游戏 → 日志无 ERROR
- [ ] 回归（已测过，确认没被本轮改动影响）：同格主动互动 **点同意 → 窗口当场打开**（`同意后已自动开窗…（第 1 次尝试成功）`）；异地动作完成 → `[ActionWatcher] 动作完成 → 异地…自动开窗`

**P1 · 逐面板**

*A 对话窗（UIChatAi）*
- [ ] 三条入口都能开且绑定正确 NPC：F9 / NPC 面板「AI 对话」/ 通讯录行点击
- [ ] 连开 A → B → 回 A：历史不串台，标题与立绘跟着换
- [ ] 流式：发送 →「对方正在斟酌…」→ 首字出现 → 逐字增长 → 收口停（ttft 实测 3.5~25s，慢≠坏）
- [ ] 工具步骤行与正文各就各位；回合收口后正文**只出现一遍**（09-13 去重回归点）
- [ ] 历史回放：关窗再开，本次对话完整回来（现拉全部历史，不再只 10 轮）
- [ ] 删除模式：🗑 → 选择 → `已选 N 回合` 与气泡数一致 → 删除后重放正确、摘要提示含"折叠记忆"
- [ ] 压缩：点标题栏压缩按钮（或输入 `/compact`）→ 按钮**立刻**变「压缩中…」并置灰 + 提示行带秒表（`已 Ns，摘要调用较慢，请勿重复点击`）→ 结果原位更新为成功/无内容/失败并还原按钮。**慢≠坏**：摘要 LLM 实测跑过 105s（provider 慢时），期间重复点会被 Python 重入闸拒绝并回"正在压缩中，请稍候"；C# 侧留 `压缩请求已发` / `压缩结果：ok=… 耗时=…ms` 两行日志
- [ ] 统计条（AB 预制体有该节点时）回合结束后有 token/时延数字
- [x] 手感：Enter 发送、Esc 关窗；**面板开着时游戏原生快捷键一律不误触（ESC 除外）**——对话窗 / 配置面板 / 传音簿各试一遍：按 `X`(技能) `I`(人物属性) `B`(背包) `M`(小地图) `Z`(跳过本月) 都**不该**有反应，关掉面板后这些键应**立刻恢复**。**09-15 实测通过**。日志判据：`[FastKeyGate] 已拦下大地图快捷键派发 MapWorldMgr.FastKey（第 N 次…）`
- [ ] 图片三条来源各试一次：手打路径 / `Ctrl+V` 路径 / `Ctrl+V` 剪贴板位图 → 缩略图条出现；**只发图不发字**可发送；下一回合问它，历史里只剩 `[图片：xx]`

*B 通讯录（UIContactAi）*
- [ ] F10 开关；`好友 / 最近` 切换 + 搜索框本地过滤
- [ ] 好友列表 = 关系网全集 ∪ 手动好友；行点击开对话窗并绑定该 NPC
- [ ] NPC 面板「加好友」→ 标签变「移除好友」→ `worlds/<world_id>/contacts.json` 落盘 → 重启后仍在
- [ ] 「最近」按互动时间倒序（发一条消息后该 NPC 置顶）
- [ ] 未读：异地传音 → HUD 红点亮 + 顶部横幅；打开该 NPC 对话 → 红点灭；面板行小圆点同步
- [ ] HUD 角标视觉：真圆 + 白描边 + 轻微呼吸，不遮别的 HUD
- [ ] 边角：空列表 / 单条 / 超长名 NPC 排版不炸

*C 配置（UIConfigAi）*
- [ ] F11 开关；对话窗 ⚙ 也能开（带当前 NPC 过滤）
- [ ] **分组与滚动（09-13 改版）**：4 组标题条（模型与生成 / 主动互动 / 记忆与压缩 / 高级）+ 18 行常显；滚轮能一页滚到底（灵敏度 20），切 Tab 回来回到顶部
- [ ] **新增开关逐项生效**：主动互动总开关（关掉后日志 `主动互动总开关已关闭`，NPC 不再主动开口、你主动找他照常）；自动压缩开关（关掉后自动压缩停、标题栏压缩按钮照常）；显示立绘（关掉后 `PortraitService` 打 `立绘开关已关`、立绘槽位隐藏）
- [ ] **热生效验证**：改「每日概率 / 两个冷却 / 保留原文比例」→ 保存 → 状态条显示「已保存：设置已即时生效」，日志出现 `[NpcInitiativeMonitor] config: … enabled=…`（C# 重拉）与 Python 侧压缩器重建日志；**没有** Python 重启
- [ ] **悬停气泡**：鼠标停在任一参数行 0.3s → 右下浮出解释文字（`HoverTip`，18 处登记）；移开即收。日志锚点：`[HoverTip] 登记就绪：N 条 画布=…` + `[HoverTip] 首次命中 区域#0 …`
- [ ] 读：6 个配置块显示当前值（llm / network / initiative / compaction / storage / prompts）
- [ ] 热更：改 `model` / `base_url` → 保存 → 下一句就走新模型（Python 日志可见）
- [ ] 重启项：改 `network.port` → 提示需重启 → 点重启 → Python 重启并自动重连（`C# 已连接`）
- [ ] 提示词：读写 `prompts/personas/<npc>.txt` → 下一回合生效；新建人设合法通过、非法 id/路径穿越被拒
- [ ] 白名单外的键改不动（`config.json` 不被写坏）；越界值被夹取并留 WARNING（如 `retain_ratio=0.9` → 夹到 0.6）

*D 剧情层*
- [ ] 原生剧情窗出现「AI 对话」按钮（与原选项并存、不接管）→ 点它开对话窗且 NPC 顺着该页剧情开口
- [ ] 邀约流程走到第二层地点选择 → NPC 拿得到地点原句（`DramaTextCapture`）
- [ ] 工具确认窗：赠灵石 / 建关系 / 求婚 / 解除 → 立绘左右正确（左玩家、右 NPC）→ **确定真的到账**、**取消 = 婉拒**且模型收到真实失败结果

*E 持久化语义（上线前必过）*
- [ ] 聊两句**不存档**直接退游戏 → 重进：NPC 不记得（增量天然作废）
- [ ] 聊两句**存档后**退游戏 → 重进：NPC 记得
- [ ] 存档 A 聊 → 换存档 B 聊 → 读回 A：历史/通讯录回到 A 时刻（`worlds/<id>` 隔离，不串号）
- [ ] 关窗（冻结）不写盘：关窗后 `worlds/<id>/<npc>.jsonl` 的 mtime 不变，存档后才更新

*F 稳定性*
- [ ] 连续开关面板 ≥20 次 + 连续 10 回合对话：不卡、不残留、无异常
- [ ] 两个 NPC 同时说话（两个窗口快速各发一条）→ 各自归位不串台
- [ ] 战斗/剧情窗中触发传音 → 只横幅+红点，不弹窗不动作
- [ ] 挂机 10 分钟不操作：无崩溃、无 ERROR 刷屏

**P2 · 已知边界（不是 bug，别花时间）**

- 历史**没有**上拉加载更早（`MAX_ROWS=1000` 上限，超出时列表顶部提示"更早的 N 条历史未渲染"）
- 读**更早**的存档不严格回滚账本；`complete_turns` C# 未用；`npc_reply` 不带 `turn` 字段（内部约定）
- 通讯录头像懒填充：只有开过对话的 NPC 才有立绘，其余为占位圆
- `llm.timeout` 默认 `null`（= SDK 600s）——**上线前建议显式收紧到 60~120s**
- C# 侧 request/response 帧 trace 镜像未做（跨端时间线目前只能看 Python 单侧）

**上线收尾**

- [ ] `cd /mnt/f && python3 -m pytest agent_loop/tests -q` → 244 passed
- [ ] `dotnet build csharp/AgentLoopBridge.csproj -c Debug -t:Rebuild` → 0 error（4 个 CS1668 无害）
- [ ] 构建产物与部署 DLL **两端 md5 一致**，且 DLL 时间戳晚于最后一次源码改动
- [ ] 根目录无生效的 `_diag_*.txt` 哨兵（要重测再改名恢复）
- [ ] 复核 `config.json`：`initiative`（频率/冷却）、`llm.timeout`、`logging.level`（部署建议 INFO）、`image.enabled`
- [ ] 备份：当前 DLL、`contacts.json`、`worlds/` 目录

---

## 三、特性总结与未来

### 10. 特性速览

| 特性 | 做法 | 效果 |
|---|---|---|
| 一 `NPC` 一活体 | `AgentLoop` 名册 + `with lock` | 多窗口不分身 |
| 账本可重放 | `Session.log` 事件溯源 + `Surface` 增量 | 崩后精准恢复，脱离游戏可测 |
| 人设分层 | `SystemPrompt` 单例 + 每 `NPC` 一屉 `deployment:persona` | `assemble` 自动带人设，热改不串台 |
| 文件即真相 | `prompts/sections\|personas` 每步自检 + 懒建屉 | 改提示词文件下把 `turn` 生效，无需重启 |
| LLM 三路上沉 | `LlmClient` 单口 `generate`，openai/stub/echo 三实现 + factory | 循环机不猜后端，`echo` 兜底离线可跑 |
| LLM ⇄ Tools 分家 | 适配与互译归 `llm/`，工具执行归 `bridge`/Agent | LLM 无副作用纯生成，可单测 |
| `tool_calls` 结构化 | 9 工具 schema + `tool/call→tool/result` 配对落盘 | 弃 XML 模拟，格式零失败率、可靠缓存 |
| 关窗冻结·固化跟存档 | 生命周期挂在对话窗上：开窗=激活（`open_chat` 恢复优先级 冻结舱 > 磁盘账本 > 全新 + 历史回放），关窗=**冻结**（`dispose_agent` 转 `_frozen` 内存舱，**不写盘不拆屉**，回合在跑只记名延至回合毕）；固化由 C# 存档钩子驱动（`SaveData`→`save_happened`→`flush_all()`；`IntoWorld`→`load_happened`→`discard_all()`），空账本不落盘 | 内存有界（稳态 ≈ 开着的窗口数）、**读档=回到存档时刻**、关游戏不保存=增量天然作废、浏览式开关窗零残留；`get_history` 保持只读 |
| 半截 `turn` 自愈 | `interruptedTurnClosers` 补 `turn/end:interrupted` | 开转不卡死 |
| 手动/自动压缩 | `Compressor`（meter+pruner+compress）turn 边界触发 / `compact_now` 手动；**全程留痕**（收到/选段/摘要/完成或放弃，摘要 LLM 失败不再静默） | 只压消息不压 system，保留尾原样，中文计量，防抖 + fail-closed；"卡在压缩中"可定位到具体一段（排障见 [附录 B](docs/APPENDIX.md#b-日志规约log_setup)） |
| C 端初搭 | `MelonLoader` 插件（WS 全双工客户端 + 主线程调度 + 二阶段校验）拉起 `server.py`，`chat_cli.py --ws` 对话；**C# 已首次编译通过**（`AgentLoopBridge.dll`，修复 csproj 编译项/0Harmony 路径/字段可见性/Debug 歧义/枚举 `??`/LangVersion，本机 v4.8.1 目标包验证） | 双进程「对话→工具→LLM→回话」全链路可跑通，含 step/text_delta 流式，协议固定、桥不返工 |
| AI 行为落地 | `world_ai_action` 8 op 已落地（含战斗 spar/attack——combat_duel 已并入、attack 触发战斗 UI 战后自然结算；双修/论道/邀约/疗伤/提升心情 → `WorldUnitAIAction1031/1037/1044/1034/1041`，`toUnit`/AI 载体模式），传功落点 `UnitActionRoleTeachSkill` 待核 | agent 调用即真实触发游戏原生动作、游戏自弹原版剧情/战斗 UI（联动自动发生），AI 可经 tool/result 得知结果；映射表与 IL 证据见 `docs/ui-interception-spec.md` §7.4 |
| 图片输入 | `image` 块 → `image_url` 多-part；`supports_image` 三态判定 | 支持多模态输入，非多模态模型带图报清晰错误 |
| NPC 主动开口 | C# `NpcInitiativeMonitor` 帧节流触发（冷却+候选+意图派发；候选=关系记录全集（十容器+好友簿+**仇人簿**+GetAllGoodRelationUnitID 含敌）+手动通讯录好友+**同格陌生人**；门槛：非通讯录成员仅同格可发起）→ `npc_initiative` 事件 → Python 伪 user 意图驱动正常 turn → 收尾 C# 分流：**同格且空闲=当面弹出对话 UI，异地/忙=未读**（HUD「传」按钮红点，点开即回放 session 历史） | 无需玩家输入 NPC 即可主动开口（对齐神识传音 §3.4 十一意图）；与游戏本体 AI（RunNPCAI 月度动作/仇恨寻仇原生 UI）并行正交；消息不再丢失（此前 AB 模式未开窗即丢） |
| 传音模式 | L1 玩家段按 `same_grid` 成文（面对面/神识传音）+ 面对面动作二阶段校验 | 异地对话语义明确，切磋/双修/传功不误触发，模型不幻觉远距离动作 |
| 回合并发 | per-agent 锁（同 NPC 串行，不同 NPC 并行）+ 全局并发闸 `max_concurrent_turns` | 多 NPC 同时活跃互不阻塞；LLM 并发有天花板，不爆炸 |
| 分块配置 | `config_loader` 读 `config.json`（network/concurrency/initiative/compaction/storage/llm/logging），缺键默认值深合并 | 一处改配置重启生效；旧扁平 llm 键平滑迁移 |
| 全局日志（log_setup） | 唯一「装管道」者：按日滚动文件 + `[npc t{turn} s{step} stage]` 上下文栏（contextvar，并发回合不串台）+ 进程/线程/asyncio 异常钩子 + `stage()` 阶段打点（超阈按 1x/2x/4x 有界告警，**不常驻巡检**）；各模块只 `get_logger(__name__)`「用管道」，写回配置即热重配。规约见 [附录 B](docs/APPENDIX.md#b-日志规约log_setup) | 慢/卡死/无回复/进程消失全部可回溯；三个静默黑洞（LLM 无超时、桥 RPC 120s 静默、并发闸静默排队）当场可见 |
| 配置管理 UI（UIConfigAi） | AB 预制体版（`AbConfigPanel`）**唯一路径**（`AB_UI` 构建不建代码版配置树、`ConfigPanelOpener` 失败只报错不回退——修复双 presenter 同帧双轮询 F11 互抵致关不上面板的 bug），`ConfigPanelOpener` 统一 F11/⚙ 开关；`ConfigPresenter` 经 `WsClient.SendRequest` 走 6 个配置 RPC；llm 块 `LlmRouter` 热插拔。**09-13 分组改版**：大模型页 = **4 组标题条（模型与生成 / 主动互动 / 记忆与压缩 / 高级）+ 18 行常显 + 整页滚动**（`BG/PageLlm/Scroll/Viewport/Content`，内容 1014px vs 视口 504px），废除 `ui.show_advanced` 整组隐藏；新增 `initiative.enabled`（主动互动总开关）/`compaction.enabled`（自动压缩开关，关后手动压缩照常）/`compaction.retain_ratio`/`compaction.threshold_ratio`/`ui.portraits_enabled`（立绘逃生门）；**除 `network.port` 外全部即时生效**（C# 侧参数保存后重拉 `get_config`，Python 侧 `min_interval` 直改属性、整块 `compaction` 重建 Compressor） | 游戏内 F11 改模型/提示词/主动互动频率/压缩与记忆策略；只重启 Python、不再需要重启游戏（改端口除外） |
| 工具确认窗 | `ShowDramaService` 弹 `UICustomDramaDyn` **立绘剧情窗**（NPC 立绘+自定义按钮文案；窗 ID/选项 ID=编辑器 MID+偏移，与 ModExcel 配置表同源）+ `DramaGate` 延迟 response；官方管线要求：配置条目必须经模组编辑器导出（`1b8bg-` 加密），手写明文/自造 ID 均不可行（09-11 两事故） | 建关系/求婚/解除/赠送由玩家当面点选确认，LLM 等真实结果收口；迟到点击/超时均安全 |
| UI 生命周期（方案 A） | 面板**按需创建**（自动创建废除）：`EnsureInjected` 校验/补注 → `GetUI` 复用优先 → `OpenUI` → 保持显示；关闭 = `g.ui.CloseUI(UITypeBase, false)` 交游戏（`CloseViaManager`）；**绝不自己 `SetActive(false)`/`Destroy`、绝不动 UI 层兄弟位次** | 游戏登记表不泄漏 → 世界输入不被门控（09-10「进游戏无法输入」事故修复） |
| 常驻职责分离 | 未读/好友镜像/会话索引/同格自动弹窗放 `ContactStore`+`ContactDuty` 静态层，面板只是视图（打开时读 Store 渲染、订阅 Changed 即时刷新） | 面板不在场功能照常；面板实例可被游戏随时销毁而数据无损 |
| 诊断工具族 | `DiagSwitches` 文件哨兵（`_diag_*.txt` 免重启 A/B）+ 层顶监测/输入快照（`WorldInputSnapshot`）+ 热键 F2 快照/F4 DumpUiTree/F5 点确认按钮/F6 逐层+0 尺寸/F7 CloseAllUI 应急 | 「游戏整体行为异常」类问题的定位效率数量级提升（本事故 9 步对照实验全靠它） |
| 通讯录（传音簿）UI | `ContactPresenter`（F10 开关，`ContactPanelOpener` 统一）：好友 = 玩家关系记录全集（`RelationNetwork`：十容器+好友簿+仇人簿+GetAllGoodRelation 含敌）∪ 手动好友（`contacts.json` 经 list/add/remove_contact RPC，NPC 面板「加好友」按钮注入）；最近 = 通讯录 ∩ 互动记录（会话 mtime∪本地时间戳）倒序；搜索本地子串过滤；行点击/✓ → `ChatLauncher` 开对话；未读：主动传音分流（同格当面弹出 / 异地 HUD「传」按钮红点） | 微信式好友名录（双向语义：关系簿+手动添加，非全图筛选）；agent 与加好友解耦（首条消息惰性建，全新 session 首回合注入相识事件）；换肤素材清单见 [`docs/ui-skin-prompts.md`](docs/ui-skin-prompts.md)。**方案 A 生命周期（09-10 事故修复）**：按需创建 + 关闭走 `g.ui.CloseUI` 交游戏（登记项泄漏曾致世界输入被门控，复盘见 `docs/2026-09-10-world-input-freeze-postmortem.md`）；通讯录立绘已移除（09-11，头像=占位圆）；好友镜像/未读/会话索引在 `ContactStore`+`ContactDuty` 常驻层（面板不在场功能照常）；未读提醒=HUD「传」按钮红点。`uicontactai.ab` 已部署 |

### 未来目标

* 中期/长期记忆便签化：`短期`（历史压缩为纪要）已落地，`中期/长期` 分层蒸馏 + `zstd` 压（参照神识传音三层记忆漏斗）
* 压缩的 `summary_llm` 与主 LLM 分流、`ctx_window` 由 `OpenAILlmClient.context_window` 动态提供
* RAG 便签与 `AGENTS.md` 瀑布接入（`assemble` 已留 `contexts` 瀑布插槽）
* **每 NPC 气运/性格 RAG 段**：`world:persona_rules` 已补先天气运/性格通识；未来用 RAG 按 `traits/{npc_id}.json` 检索各 NPC 的具体气运/性格，动态注入为 scoped section 段（插槽已规划，本次未实现）
* **C 端补全游戏 API 实装（9 工具已全部落地，剩真机核验）**：`GameContext.cs`/`ToolExecutor.cs` 已用官方 `GGBH_API` 真实读取并真实发起行为——`world_ai_action` 8 op（战斗 spar/attack + 双修/论道/邀约/疗伤/提升心情 `WorldUnitAIAction1031/1037/1044/1034/1041`，**传功 `UnitActionRoleTeachSkill` 已补全**：`SkillCanTeach` 校验 + `TeachType.Teach` + `ResolveMartial` 按 ID/中文名解析功法，见 `ToolExecutor.ResolveMartial`）；`inspect_unit` 七块、`search_units` filters 全图找人、`query_world` events/rankings 四榜、`social_relation` 13 op（AddIntim/AddHate 直写 + `UnitActionRoleRelation/BreakWith/Marry` 弹原版剧情）、`economy_item`（`UnitActionRoleGive` 多道具 + 灵石 `RewardPropMoney/CostPropItem(10001)` 转账）、`movement`（召唤/传送 `UnitActionMovePlayer/MoveNPC`）、`trade`（**买卖**：双方由模型指定，卖方背包 `DelProps`→买方 `AddProps`、买方灵石 `CostPropItem(10001)`→卖方 `RewardPropMoney`，确认窗复用 economy 段，见 `docs/tool-result-contract.md` §8）、`item_acquire`（偷窃/讨要 `UnitActionRoleStealItem/Askfor`，`quest_faction` 已删除）。剩真机核验：世界月志 `month` 年月换算/`DataToString()` 输出形态、灵石转账是否被存档正确消费、`UnitActionRoleRelation` 是否弹确认剧情/`ConfRoleRelationItem.type` 与枚举对应、`BreakWith` 能否精确定位、`Give` 是否弹原版 UI 且多道具生效、`Attack` 是否触发战斗 UI 且战后自然结算、`MovePlayer/MoveNPC` 是否走移动系统、各动作 `ActionStart(null)` 回调容忍度
* **游戏内三面板 UI**：对话（AB 已部署）、配置（AB 部署 + 悬停气泡）、通讯录（AB 接线：`AbContactPanel` 常驻宿主，AB 构建已停建代码版树）均已实装——**全量细节与剩余待办见 `docs/ui-interception-spec.md`**；换肤按 [`docs/ui-skin-prompts.md`](docs/ui-skin-prompts.md) 出图替换预制体素材；通讯录 AB 已部署生效（原待办完成）；对话窗生命周期迁移方案 A **已完成（09-11）**；立绘确认窗官方配置管线**已落地（09-11：MID=-803158451 同源+加密表导出合并）**；当前最高优先=游戏内验证：①立绘确认窗（赠灵石弹窗/按钮文案/确定到账/取消婉拒）②对话持久化存档语义（不存档退出 NPC 不记得/存档后记得/读档回到存档时刻）③对话窗开关与世界输入/历史回放
* 日志：C# 侧 request/response 帧 trace 镜像（跨端时间线目前只能从 Python 单侧看）；`llm.timeout` 建议显式收紧到 60–120s（现默认 `null`=保持 SDK 默认 600s）
* `_execute_tool_calls` 视需要独立为 `ToolExecutor` 服务，与 `LlmClient` 对称

---

## 附录

附录全文已抽出为独立文件（2026-09-13 精简）：

| 文件 | 内容 |
|---|---|
| [`docs/APPENDIX.md`](docs/APPENDIX.md) | **A** C#↔Python 桥契约 · **B** 日志规约与排障手册 · **D** 开发铁律与高频踩坑 · **E** UI 工作流 · **G** 功能设计底稿 · **H** 官方 API 与反编速查 |
| [`docs/ui-skin-prompts.md`](docs/ui-skin-prompts.md) | 原附录 C：UI 换肤素材与生图提示词 |

> **字母编号沿用原稿**，正文里的「附录 A / B / D / E / G」引用继续有效；原 F（Unity 中文界面与文本处理）已并入 APPENDIX §E.5。

---

*本文结构/字段名均来自 `agent_loop/` 实码，无编造。DSH 核心包为闭源运行时，参考点为代码注释与 `docs/` 契约中对齐的理念，非逐行搬运。*
