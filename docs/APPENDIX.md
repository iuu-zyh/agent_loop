# agent_loop 附录

> README 的附录全文（2026-09-13 从 `README.md` 抽出并精简）。**字母编号沿用原稿**——正文与其它文档里的
> 「附录 A / B / D / E / G」引用继续有效。原 C（UI 换肤素材与生图提示词）是活的施工清单，单独放在
> [`ui-skin-prompts.md`](ui-skin-prompts.md)；原 F（Unity 中文界面术语）已并入本文 E.5。

| 节 | 内容 | 什么时候看 |
|---|---|---|
| [A](#a-cpython-桥契约单-websocket-全双工) | C#↔Python 桥契约 | 改协议 / 加 RPC / 调超时 |
| [B](#b-日志规约log_setup) | 日志规约 + 排障手册 | 「慢 / 卡住 / 没回复 / 进程没了」 |
| [D](#d-开发铁律与高频踩坑) | 开发铁律与高频踩坑 | **写 C# 之前必读** |
| [E](#e-ui-工作流预制件--ab--部署--验证) | UI 工作流（预制件 → AB → 部署 → 验证） | 改面板样式 / 重打 AB |
| [G](#g-功能设计底稿要点) | 功能设计底稿（剧情注入 / 自主交互 / travel / 背包） | 改玩法行为 |
| [H](#h-官方-api-与反编速查c-端联调资料) | 官方 API 与反编速查 | 找 API 签名 / 查游戏数据源 |

---

## A. C#↔Python 桥契约（单 WebSocket 全双工）

**分层分权**：Python 是大脑（管 `messages[]/tools/模型`），C# 是双手（管 `g.world/g.conf` 与主线程调度）；二者只以
**单条 WebSocket 全双工连接**交换 JSON（Python 作 `WsServer` 监听 `127.0.0.1:8766`，C# 作客户端连入），**不共享内存**。

### A.1 启动方式

| 方式 | 步骤 |
|---|---|
| A) 手动 | 先 `python scripts/server.py`（起 WsServer `:8766` + AgentLoop），再进游戏，C# 插件 `Init` 起 `WsClient` 连入 |
| B) 自拉 | C# `Launcher.LaunchPythonOnce()` → `Process.Start("python", "…server.py")`，Python 起监听后 C# 重连进入 |

两种方式协议完全一致；`WsClient` 带断线自动重连（常规每 2s 一轮；Python 重启期间缩到 0.3s）。

### A.2 帧类型与方向

Python 回合内取快照 / 执行工具 → C# `request`；C# 按 `req_id` 回 `response`：

```json
{"type":"request","req_id":"1","method":"get_context","params":{"npc_id":"林婉清"}}
{"type":"request","req_id":"1","method":"call_tool","params":{"name":"inspect_unit","arguments":{"target":"张三"}}}
{"type":"response","req_id":"1","ok":true,"data":{"text":"自身：林婉清 位置(100,200)…","raw":{…}}}
{"type":"response","req_id":"1","ok":false,"error":"好感不足，需≥180，当前135","data":null}
```

`event`（双向推送）：

```json
{"type":"event","event":"player_message","npc_id":"林婉清","text":"[1年1月3日] 你好"}             // C#→Python 玩家消息（带游戏时间戳，09-13）
{"type":"event","event":"player_message","npc_id":"林婉清","text":"[1年1月3日] 这张图是谁\n[图片：a.png]",
 "images":[{"name":"a.png","url":"data:image/png;base64,…","w":1024,"h":576,"kb":220}]}      // C#→Python 带图（09-13）
{"type":"event","event":"npc_initiative","npc_id":"林婉清","intent":"missing","reason":"…"}  // C#→Python NPC 主动开口
{"type":"event","event":"step","npc_id":"林婉清","kind":"tool_call","name":"inspect_unit"}   // Python→C# 回合中
{"type":"event","event":"text_delta","npc_id":"林婉清","text":"张"}                           // Python→C# 逐字流式
{"type":"event","event":"npc_reply","npc_id":"林婉清","text":"……","initiative":true}         // Python→C# 收尾帧
```

* `step` 每完成一步推一次（`tool_call`/`tool_result`/`text`）供 UI 显示「正在做什么」；`text_delta` 供打字机；`npc_reply` 携最终完整回复。
* **玩家消息时间戳契约（09-13，用户拍板"NPC 必须感知时间"）**：`text` 以 **`[N年M月D日] `** 开头（含日：同月内 1 号与 28 号必须可区分，否则「你三天前才来过」这类话说不出），由 **C# 在玩家消息进 WS 之前**拼好（`ChatPresenter.OnSubmitRequested`），Python **只识别、不重写**。
  * **在解决什么**：账本里玩家的话原本没有任何时间信息，模型说不出"上次见你是三月前"；且 `npc_reply` 只回当前回合，跨会话的时间差完全靠账本 —— 所以时间戳必须**落在账本文本里**（长期留存），不能只在系统提示里报一次"现在"。
  * **标度**：账面月 = `g.world.run.roundMonth + 1`（`roundMonth` 是 **0 起总月数**），与 `DataUnitLog.LogItemData.month` **同标度**（1年1月=1、2年1月=13）。换算 `UnitSnapshot.CnYearMonth` 是**全仓唯一一份**，经历「近况」前缀与消息时间戳共用（各写一份必然漂移成"近况 2年1月 / 对话 1年13月"）。反编实证 `WorldRunMgr` 只有 `roundMonth/roundDay/roundDayResidue/roundDayMax` —— **没有 `roundYear`**，年必须除出来。参照 mod 两处独立写法（`roundMonth/12+1` 年 + `roundMonth%12+1` 月；`ConvertToYearsMonths(roundMonth+1)`）互相印证标度。
  * **拼在本地回显之前**（唯一来源，同 `[图片：xx]` 那条）：气泡 / 账本 / 模型后续回合必须同一个串。若改在 `WsClient.SendPlayerMessage` 里拼，实时气泡干净、而重开窗口走 `get_history` 回放（`ChatWindow.cs` 读账本）时气泡**突然长出时间戳** —— 同一条消息两种样子。代价是气泡里也看得见时间戳（参照 mod 同样把 `[N年M月D日]` 显示在聊天条目上）。
  * **故障不阻断**：日历读不到（未进世界 / 切档瞬间）→ 标签为空串 → **原样照发**。时间戳是锦上添花，绝不能把玩家的话卡住。
  * **Python 侧的义务**：命令判据必须先剥前缀（`ws_channel._strip_time_stamp`）。C# 自己拦 `/compact` 时还没有前缀，但兜底通道一旦收到 `"[1年1月3日] /compact"`，裸判据永远匹配不上 → 会被当普通文本喂给模型（玩家以为要压缩、实际烧一个回合）。**正文一律带着前缀进账本**，剥前缀只用于判据。
  * **L1 侧的对应物：「当前时间」段**（09-13 用户要求"L1 也要有日期"）。前缀只能说明"那句话是哪天说的"，说不出"**现在**是什么时候" —— 纯 `npc_initiative` 回合一条玩家消息都没有，模型手里会**完全没有任何日期**。故 `GameContext.GetL1` 增发 `raw.now = {year, month, day, text}`（`text` 形如 `1年1月3日`，与消息前缀**同一函数** `UnitSnapshot.CnDate` ⇒ 逐字节相同，模型在两边看到同一个串）。
    **为什么是独立一段而不是塞进「近况」**（决定性理由，勿回退）：① `render_context_segment` 对空文本返回 `""` ⇒ **「近况」为空时整段不渲染**，而新 NPC / 系统角色恰恰没有经历日志、又最需要知道"今天几号"，塞进去 = 那类 NPC 永远没日期；② 段是逐段差分的，日期每天变 ⇒ 独立段每天只重发 ~20 字，塞进近况则是**整段（最多 10 条经历）每天重发**；③ 段名「近况」装历史，混进"现在"会让"这段为什么变了"无法归因。位置在 `_CTX_SEGMENTS` **首位**（先"现在"，再自身/玩家/近况）。
    快照失败分支**也要给 `now`**（时钟独立于单位数据）；日历读不到 → `now` 为空对象 → 段不渲染（绝不编造日期）。
  * **模型得知道这个方括号是什么**：`prompts/sections/harness_identity.txt` 的 `[消息读法]` 块说明 `[N年M月D日]` 是"那句话的日期戳"、`（NPC主动传音：…）` 是舞台提示、且**两者都不要在台词里复述** —— 光给数据不说读法，模型可能把日期当成玩家说的话念出来。
* **图片附件契约（09-13，用户拍板三条）**：`images` 可选、缺省不写该字段（旧端收到也无感）。
  ① `text` 由 **C# 侧拼好** `[图片：文件名]` 占位，Python **不再改写** —— 本地气泡 / 落盘历史 / 后续回合三者同一串（唯一来源，避免两边各拼一遍）。
  ② **图片只进当回合、绝不进历史**：账本（`session.log` / JSONL / 重放）里只有占位文本，图像 url 存 `DialogueAgent._turn_images` 内存侧表，仅在拼 LLM 请求时**浅拷贝**挂进消息副本（`_attach_turn_images`），回合收口按「本回合实际领到的 id」清除。原地改 `derive_messages()` 返回的对象 = 把 base64 永久写进账本，是本条要防的事故。
  ③ **允许只发图不发字**：`ChatHub.handle_message` 判据放宽为 `not text and not images` 才丢弃。
  取图入口（C#，均在 `csharp/UI/ImageInput.cs`）：**路径文本**（手打/粘贴，含带引号的「复制文件地址」）与 **输入框里 Ctrl+V**（读剪贴板）。编码预算：长边 ≤1024、单张 ≤1.5MB（超了 PNG→JPG q85）、单条 ≤3 张；尺寸体积都在预算内时**原字节直发**（零重编码）。**`websockets.serve` 必须显式 `max_size`**（库默认 1MiB，带图帧必超 → 1009 关连接且无有用报错，现设 8MiB）。
  ④ **剪贴板三条来源，全部归一到「一个文件路径」**（09-13 实机修正）：`CF_HDROP`（资源管理器复制的文件）→ 注册格式 **`"PNG"`**（Win11 截图工具：剪贴板里就是一条完整 PNG 流，实测 940041B 合法签名）→ `CF_DIB`（裸位图，**自己解像素后重新编码成 PNG**）。后两者落 `%TEMP%\agent_loop_clip\` 再返回路径，于是**下游只有「路径」一条代码路径**，剪贴板来源对它透明。
     **原实现只读 `CF_HDROP` 是错的**：`Win+Shift+S` 截图**只写 Bitmap/PNG、不写 CF_HDROP**（实测 `HasFileDrop=False / HasImage=True`）→ 用户 Ctrl+V 后**什么都没发生且无任何日志**，被误判成"上传失败"。现**无论成败都打 `[ImageInput] Ctrl+V 取剪贴板：<how> → N 张`**，绝不静默。
  ⑤ **★★ `CF_DIB` 绝不能落成 .bmp：`ImageConversion.LoadImage` 只支持 PNG/JPG ★★**（09-13 实机教训，第二次踩）：
     最初做法是「补 14B `BITMAPFILEHEADER` 当 .bmp 落盘，让解码器去读」。那个 .bmp **文件完全合法**（离线 PIL 验证、与源图逐像素一致、`bfOff=66 bpp=32 comp=3` 全对），但 Unity 解不出来 —— 实机表现是**预览条渲染成一块"带问号的占位图"**（解码失败的兜底图形，且整块发绿）。
     **教训本体：「我能生成合法文件」≠「目标解码器认这个格式」。** 生成端自测通过之后，**仍必须单独核对消费端的格式支持面** —— 这次就是自查全绿、真机才暴露。
     现改为 `DibToPng`：自己按 DIB 头解像素 → `Texture2D` → `EncodeToPNG`（该 API 已由 PortraitCache 实证可用）→ 落 `.png`。顺带按需**直接降采样解码**（省内存省编码），支持 32/24/16bpp，1/4/8bpp 调色板图明确拒绝并留痕。
     解析段的两个坑（用真实剪贴板 DIB 差分验证才暴露，漏了就是**静默的坏图**）：
      · **`BI_BITFIELDS` 的 12 字节通道掩码**：`biCompression==3` 且 `biSize==40` 时，头后紧跟 3 个 DWORD 掩码，**不是**像素。漏掉 → 像素整体错位（实测修正前 RGB 抽样与参照 PNG **全不符**，修正后 **0/560 不符**；换解析器后复测 **0/3010 不符、导出图与真值 sha256 完全相同**）。
      · **alpha 一律填 `0xFF`**：截图 CF_DIB 的第 4 字节恒为 0（实测抽样全 0），当 A 读会得到**全透明图**。
  ⑥ **多张用「引号 + 空格」分隔，不用换行**：输入框可能是**单行** `InputField`，`\n` 会被 Unity 吃掉 → 多张挤成一行、只有一张被识别。`"p1" "p2"` 单/多行都成立，且与 Windows「复制文件地址」同款、路径含空格不歧义。
  ⑦ **预览条**（`csharp/UI/ImageAttachPreview.cs`）：输入框上方显示缩略图 —— 路径通道对玩家本来是不可观测的（框里只有一行路径），**没有预览就无法判断到底附上了没有**。运行时新建节点、`LayoutElement.ignoreLayout=true` 避开父级布局组，不动 AB 预制体；只在输入文本变化时重建。
     **布局契约（用户明确三条）**：**左对齐**（不居中）· **多图从左到右依次排** · **不染色**。实现：预览容器**与输入框同锚点同宽度** ⇒ 左缘即输入框左缘，"左对齐"不需要任何坐标换算；格子以 `pivot.x=0` 从容器左缘依次排开。垂直位置用一般式 `pos.y = 框pos.y + 框高*(1−pivot.y) + Gap + 条高*pivot.y`（对任意 pivot 成立 —— 首版写成 `+框高+Gap`，pivot=0.5 时会**压住输入框约 16px**）。
     **★ 绘制顺序铁律：Unity UI 里子节点画在父节点之上 ★**。首版把描边做成缩略图 cell 的子节点、`RawImage` 挂在 cell 上 → 那层 85% 不透明的描边**把照片整个盖住**，实机就是"整张图蒙了一层绿"（用户反馈"绿色滤镜很丑"）。`SetAsFirstSibling()` 只在**兄弟之间**排序，独生子排了也白排。正确结构是**两兄弟**：`cell`（纯壳）→ `Border`（先画，每边大 1.5px）+ `Photo`（后画，铺满 cell）⇒ 描边只在照片外沿露出细环。描边颜色也换成中性深色：绿色描边一旦因绘制顺序出问题就变成"绿色滤镜"。
  ⑧ 解析逻辑有回归测试：`bash scripts/dev/imgparse_test/run.sh`（按花括号配平抽取**真实源码**编译运行，17 例，含变异验证思路见该目录 README）。
* `npc_initiative.intent` 由 C# 按亲密度派发（正向 7 + 负向 4，与 Python `initiative.INITIATIVE_INTENTS` 对齐）；`ChatHub` 侧另有 `min_interval` 熔断（防闭关跳日爆量）。

### A.3 方法全表

**Python → C#（`request`）**

| method | C# 处理 | 说明 |
|---|---|---|
| `get_context` | `GameContext.GetL1(npc_id)` | L1 快照，`{"text","raw"}` |
| `call_tool` | `ToolExecutor.Execute(name, arguments)` | 9 工具统一入口（查询 3 + 动作 6），**二阶段校验在此**（同格/好感/性别/战力），不信任模型参数 |

失败亦回 `ok:false` + `error`，Python 侧兜底为文本，**不得静默重试同一参数**。

**C# → Python（读 RPC，UI 查询）**：`WsClient.SendRequest(method, params, onResponse, timeoutMs)`（**req_id 前缀 `u`**，回调恒主线程）。
既有：`get_history` / `get_config` / `set_config` / `list_prompts` / `read_prompt` / `write_prompt` / `write_prompts` / `create_persona`。

通讯录 4 个：

| method | params | data | 说明 |
|---|---|---|---|
| `list_contacts` | `{}` | `{"contacts":[{"npc_id","added_at","source":"manual"}]}` | 手动好友全量（保持添加顺序） |
| `add_contact` | `{"npc_id"}` | `{"added":bool,"count":n}` | 幂等（已存在不重写、不改 `added_at`）；空/超长 → `ok:false` |
| `remove_contact` | `{"npc_id"}` | `{"removed":bool,"count":n}` | 幂等（不存在也算成功） |
| `list_sessions` | `{}` | `{"sessions":[{"npc_id","mtime"}]}` | 只读存档目录下 `*.jsonl` 的**文件名与 mtime**，不读内容；5s TTL 缓存，供「最近」排序 |

> 通讯录数据分层：**好友 = 玩家社会关系（C# 本地 `RelationNetwork`）∪ 手动名单（本组 RPC）**；关系层随存档走不持久化，手动层持久化在 Python 侧（与 session/人设一样以中文名为键）。

**生命周期 RPC（开窗 = 激活 / 关窗 = 冻结，agent 生死挂在对话窗上）**

| method | data | 说明 |
|---|---|---|
| `open_chat` `{npc_id, limit?}` | `project_ui_history` 投影（形状同 `get_history`，另带 `npc_id`） | **开窗激活**：`loop.get or create` —— 活体复用 / 冻结舱 / 磁盘账本 resume（**账本即真相**）；响应直接用于 UI 历史回放；顺带撤销该 NPC 的待销毁标记（关窗→立刻重开竞态） |
| `dispose_agent` `{npc_id}` | `{"disposed":true}` 或 `{"disposed":false,"reason":"busy","pending":true}` | **关窗**：idle 即冻结（转 `_frozen` 内存舱，**不写盘、不拆屉**）；回合在跑只记 `_dispose_pending` 立即返回，收尾（`finally`）统一冻结 |

配套约定：

* `get_history` **保持只读**（只查名册不建活体）。
* **空账本不落盘**（create/dispose 双侧跳过）：浏览式开关窗零残留，`list_sessions` 的「最近」不被空文件污染。
* **回合中关窗的消息不丢**：assistant 落账先于 `npc_reply` 发送；重开窗 resume 后历史完整回放（Python 不感知窗口状态，不做 suppress）。
* **NPC 主动开口随时可复活 agent**：`handle_initiative` 同为 `get or create`。
* **加好友与 agent 解耦**：加好友只写 `contacts.json` 不建活体；开窗才激活，「相识」plugin 事件在首回合前注入一次。
* C# 挂点：开窗发 `open_chat`（代码版 `ChatPresenter`；AB 版 `AbChatPanel.InitData` 经 `OpenForNpc` 统一路由），关窗发 `dispose_agent`（fire-and-forget、幂等）。

### A.4 超时、错误与幂等

* `request` 超时走 `WsServer.request_timeout`（config `network.request_timeout`，现值 **120.0**；早期契约文本记「默认 5s」——**以 config 为准**）。C# 未连接 / 超时 → 返回兜底文本，对话不断。
* 任一方断开：C# `WsClient` 每 2s 重连；**连接只保留最新一条**（游戏重进替换旧连接）。回合内断开，Python 侧照常落账。
* `search_units` 限 10 条、`inspect_unit(logs)` 分页（约定保留）。
* 确认窗 / 挂起动作由 `DramaGate` **120s 超时自愈**（"玩家长时间未响应"），与 Python `request_timeout=120` 同步兜底；迟到补发幂等丢弃。
* 挂起 pending 期间 `DramaGate.HasPending=true` → initiative 主动开口被状态闸抑制。

### A.5 配置保存 → 重启编排 / 热生效 / 删除的落盘语义

**「保存 → 自动重启 Python → 自动重连」编排**：`set_config` 应答 `effective=restart/mixed`（或 llm 热换失败）时走
`shutdown` RPC（应答即证明 Python 已排定 0.6s 后 flush + `os._exit`）→ 等旧进程让位 → `Launcher.RelaunchPython()` → 等自动重连。

* **让位判据只有一个：连接已断**。状态机另记「本轮曾断过」（`_sawDisconnect`），**只有「断过 + 现在又连上」才算成功**——旧进程僵死、socket 还活着时绝不谎报「配置已生效」。
* **不做端口探测（勿回退）**。`Launcher.ProbePortFree` 是 09-12 事故根因：本机对「无监听端口」的 connect 不立刻拒绝，**约 2s** 后才回 `ConnectionRefused`（旁证：`WsClient` 每轮失败重连 ~4s = 2s connect + 2s sleep），400ms 窗口必然超时 → 恒判「占用」→ 状态机永远停在「等端口释放」，既不拉起也不报错。**该方法已删除。**
* **有界兜底**（全部收口，绝不停在半路）：2s 未断且进程是自拉的 → `KillOwned()`；6s 仍未断（非自拉、无法强杀）→ 硬拉起一次；拉起后 4s 未重连 → 补拉一次（最多 2 次）；18s 未重连 → 中止并如实报错（区分「旧 Python 未响应关停，请手动关控制台」与「重启后未连上，看 `server.py` 报错」）。
* 实测预算：退出 0.6s → 拉起 0.1s → 监听就绪 0.7s → 重连 ≤2.3s ≈ **2~4s**（正常路径 ~1.7s）。解释器只解析一次（重启路径不再逐个候选起进程探测）。
* 耗时反馈：状态条每 0.25s 刷新（`Time.unscaledDeltaTime` 计时，时间缩放拖不住它）。

**哪些改动需要重启（按键判定）**：`config_store.set_config` 按**键**分类，返回 `effective`（none/hot/restart/mixed）+ `hot_keys`：

| 键 | 生效方式 |
|---|---|
| `llm.*` | **热生效** —— `LlmRouter.swap` 换芯 |
| `ui.*` | **热生效** —— 只是面板显隐 |
| `network.request_timeout` | **热生效** —— 直接改 `WsServer.request_timeout`（`request()` 每次现读该属性，bridge 不传显式 timeout） |
| `compaction.ctx_window` | **热生效** —— `_build_compactor()` 按最新 config 重建 → `AgentLoop.rebind_compactor()` 热挂回（**含冻结舱**里的每个 agent，漏推会留旧压缩器；新压缩器继承旧冷却表，避免热换后立刻误触发一次自动压缩） |
| `network.port` / `initiative.*` / compaction 其余键 | **重启** |

热生效失败（换芯/改活/重建抛异常）→ 置 `swap_error`，C# 照旧自动重启兜底（改动已写盘，重启必然生效）。
UI 侧同步：`ConfigPresenter.Init` 用 `RelabelRow` 把行文案改成实际生效方式（预制件里写死「（重启生效）」），悬停说明与状态条文案随之改为「即时生效」/「热生效未成功，正重启…」。
**09-13 起**：`initiative.*`（含 `enabled`）与 `ui.portraits_enabled` 也改为热生效——C# 在 `set_config` 回包且未走重启时立刻重拉一次 `get_config` → `NpcInitiativeMonitor.Configure(...)` / `PortraitService.Enabled`；`HOT_BLOCKS = (llm, ui, initiative, compaction)`，**面板上唯一还需重启的是 `network.port`**（改端口还要重启游戏，C# 连的是旧端口）。

**删除的落盘语义（用户拍板「与对话增量同一套冷冻规则」）**：`delete_history` 只做两件事——把一条 `history/delete` 注记**追加进内存账本**，以及回包带上过滤后的新历史投影。所以 UI 上「删完立刻消失」是**内存投影，不是落盘**。

| 时机 | 是否写 jsonl |
|---|---|
| `preview_delete_history` / `delete_history` | **md5、行数、mtime 全不变** |
| 读档 `load_happened`（未存档） | 不变，且**被删回合回来** = 回滚 |
| 游戏存档 `save_happened` → `flush_all()` | 行数 +1（多一条 delete 注记）= 固化 |
| `shutdown`（**仅**配置保存「重启生效」项触发） | 重启前 flush |
| 关游戏不存档（C# 退出**不发** `shutdown`） | 增量随 Python 进程消失 |

一个**有意的例外**（用户确认保持）：配置保存触发的重启会在 `shutdown` 前固化未存档增量（09-11「重启 = 世界延续」防丢），因此「删了历史 → 立刻改配置」也会把删除一起固化——看起来像"删除立刻落盘"，实则发生在重启那一刻。

### A.6 历史回放与渲染上限

* **回放 = 全部回合**：`ChatPresenter` 固定发 `limit=0`；`ChatHub._parse_limit()` 语义 —— **缺省/非法 → 10**（老行为，`chat_cli`/测试默认不变）、**0 或负数 → 0 = 不截断**（`project_ui_history(max_turns=0|None)` 解释成"全部"）。实测量级（26 回合账本）：全部 = 109 项 / **62.7KB** 一帧；`limit=10` = 43 项 / 40.1KB。
* C# 渲染上限 `ChatWindow.MAX_ROWS` = **1000**（≈250 回合），**超限不静默**：`ReplaceHistory` 结束时若发生裁剪，列表顶部插一行「…更早的 N 条历史未渲染（窗口上限 1000 行）」（该行 `Turn=0`、不可选、不参与删除）。
* **仍未做**：上拉分页 / 虚拟列表（真到上千回合才需要）；`complete_turns` 仍未被 C# 使用。
* **删除模式的计数单位 = 回合**：`ChatWindow.UpdateCount()` 用 `HashSet<int>` 去重（旧写法逐**行** +1，而一个回合有 4 行 → 只显示一个回合也报「已选 4 回合」）；`全选/取消全选` 判定同步。
* 哨兵测试：`test_project_ui_history_max_turns_zero_means_all` / `test_ws_open_chat_limit_zero_returns_all_turns` / `test_parse_limit_semantics`。

### A.7 Python 侧对接

`WsServer(port=8766)` → `AgentLoop(bridge=WsGameBridge(ws))` → `ChatHub(loop, ws)`，注册 `hub.handle_message` / `hub.handle_initiative` 后 `await ws.start()` 常驻监听（脱离游戏测试用 `StubGameBridge` + `StubLlmClient`，见 `tests/test_ws_channel.py`）。

---

## B. 日志规约（log_setup）

### B.1 分工与四条原则

```
装配层 scripts/server.py（唯一）
        │  setup_logging(CFG) + install_process_hooks() + install_asyncio_hooks()
        ▼
log_setup.py  ← 唯一「装管道」者：handler / formatter / 滚动 / 上下文 / 异常钩子 / 阶段打点原语
        │  （只依赖标准库，不认识任何业务模块）
        ▼
各业务模块  ← 只「用管道」：log = log_setup.get_logger(__name__)
              turn_scope() 补 npc/turn、stage() 记阶段耗时
```

1. **装管道与用管道分离**：只有装配层能配置全局日志状态；业务模块**绝不** `basicConfig` / `addHandler` / `setLevel`（违规会导致 handler 重复=日志翻倍，或级别被踩=排障时看不到该看的行）。哨兵 `tests/test_log_setup.py::test_no_module_configures_logging_itself` 强制。
2. **日志只做记录，不做恢复**：自愈/重试/兜底归各业务模块；日志不参与控制流，`stage()` / formatter 内部全部 try 包死——**日志坏了也不能拖垮回合**。
3. **事件驱动，不预支轮询开销**：慢告警靠"阶段存续期内挂有界定时器 + 次数封顶"，阶段一结束立刻取消。
4. **记录业务事实的是各模块自己**：log_setup 不写任何业务语义。

> **单位坑（已踩）**：`stage(slow_ms=…)` 单位是**毫秒**，而 `WsServer.request_timeout` 等单位是**秒**。把秒直接传给 `slow_ms` 会让阈值缩到毫秒级 → 超时窗口内连发告警。正确写法 `min(limit * 500.0, 30000.0)`（半个超时，封顶 30s），由 `tests/test_ws_channel.py::test_rpc_slow_warning_threshold_is_milliseconds` 把守。
> `log_setup.flag(name)` 供"高频但默认关"的埋点单独取闸（如 `trace_frames`）。

### B.2 总闸：一个词决定开/关

| 写法 | 效果 |
|---|---|
| `off` / `false` / `0` / `no` / `none` / `silent` | 完全关闭 |
| `on` / `true` / `1` / `yes` | 打开，沿用 config 里的 `level` |
| `debug` / `info` / `warning` / `error` / `critical` | 打开并指定级别 |
| 未设置 / 无法识别 | 沿用 `config.json` 的 `logging` 块 |

```bash
python scripts/server.py                       # 沿用 config.json 的 logging 块
python scripts/server.py --log debug           # 开到 DEBUG（排查）
python scripts/server.py --no-log              # 完全关闭（部署；也可 --log off）
AGENT_LOOP_LOG=info python scripts/server.py   # 环境变量方式
```

**优先级：CLI `--log` / `--no-log` / `--log-level` > 环境变量 `AGENT_LOOP_LOG` > `config.json`**（与 `AGENT_LOOP_WS` 同一规约：env 在「使用处」解释，`config_loader` 只管文件）。

**关闭是真的关闭**：不挂任何 handler、不建日志文件，包 logger 定在「静默」级（高于 CRITICAL）；子 logger 有效级别继承自包 logger，所有 `log.xxx()` 立即返回（参数是惰性 `%s`，不会被格式化），`stage()` 连慢告警定时器都不挂——**近似零开销，可留在正式部署**。启动时往 stderr 打一行 `[server] 日志已关闭：…（需要时用 --log debug）`。
**C# 侧**：`Launcher` 目前不传参数，游戏里想换开关改 `config.json` 的 `logging.enabled` / `level`（由重启或保存任意配置项触发热重配）。

### B.3 配置块（`config.json` → `logging`，不入 UI 白名单，手改文件）

| 字段 | 默认 | 说明 |
|---|---|---|
| `enabled` | `true` | 总开关；false 时不落文件（仅保留 stderr 兜底） |
| `level` | `"INFO"` | 包 logger 级别；排障调 `"DEBUG"` |
| `file` | `"logs/agent_loop.log"` | **相对路径锚包根**（非 cwd）；空串/null = 不落文件 |
| `rotation` | `"daily"` | `daily` / `size` / `none` |
| `backup_days` | `7` | 保留份数（daily=天数；size=文件数） |
| `max_bytes` | `4MB` | `rotation=size` 时单文件上限 |
| `console` | `true` | 是否同时打 stderr（C# 拉起时有浮动控制台可见） |
| `console_level` | `"WARNING"` | 控制台级别（默认只让 WARNING+ 上屏） |
| `slow_ms` | `30000` | 阶段慢告警阈值（**毫秒**）；`0` = 关闭 |
| `slow_escalations` | `3` | 慢告警最多次数（1x/2x/4x…），封顶防长尾开销 |
| `trace_frames` | `false` | 逐帧记 C#↔Python 线上帧（DEBUG 级 + 本开关双闸；异常类帧恒记） |
| `capture_root` | `false` | true = 连第三方库日志一起收进本文件 |
| `modules` | `{}` | 模块级级别覆盖，如 `{"agent_loop.llm": "DEBUG"}` |
| `quiet_libs` | `httpx`/`httpcore`/`openai`/`websockets`/`urllib3`/`asyncio` | 第三方库降噪到 WARNING |

`config_loader.DEFAULT_CONFIG["logging"]` 是给 UI/诊断看的镜像；**真相源是 `log_setup.DEFAULT_LOGGING`**（缺键由后者补齐，两处无需严格同步）。任何一次配置写回都会触发热重配。

### B.4 行格式与平台坑

```
2026-09-11 19:25:37.389 INFO  [-] agent_loop.server: WS 通道已监听 127.0.0.1:8766，等待 C# 连接（日志：F:\agent_loop\logs\agent_loop.log）
%(asctime)s.%(msecs)03d %(levelname)-5s [%(ctx)s] %(name)s: %(message)s
```

上下文栏 `[npc t{turn} s{step} {stage}]`：缺项自动省略，全缺为 `[-]`。由 contextvar 提供（`turn_scope()` 注入），**`asyncio.create_task` 会复制 context**，所以并发 NPC 回合互不串台。副作用：单个 NPC 的时间线可直接 grep：

```
findstr /C:"[林婉清 " logs\agent_loop.log
```

1. **相对路径锚包根，不锚 cwd**：C# `Launcher` 传 `WorkingDirectory = server.py 所在目录`（即 `scripts/`），用 cwd 会把日志写进 `scripts/logs/`。`log_setup._resolve_path` 统一锚 `Path(log_setup.__file__).parent`。
2. **C# 拉起时不重定向 stderr**：控制台 handler 的输出只在一个浮动窗口里，**不是**持久证据；唯一持久证据是文件 handler。
3. **日志只在 `agent_loop.*` 命名空间内生效**：handler 挂在 `agent_loop` logger 上并 `propagate=False`，不劫持真 root（避免第三方库刷屏）。`py.warnings` 被单独收进文件；需要连第三方库一起收时开 `capture_root`。
4. **shutdown 路径**：`WsServer` 收到 `shutdown` RPC 后 0.6s 调 `os._exit(0)`，已在其前插入 `log_setup.shutdown_logging()`，保证尾巴日志不丢。

### B.5 排障手册（grep 清单）

**「很慢」** → grep `LLM 完成`，先看 `ttft` 与 `decode` 谁占 `llm` 大头：

* `ttft ≈ llm`（decode 极小）→ **慢在"发出去到第一个 token"**：网关排队 / 上游 prefill / prompt 太大。此时 `in=`（prompt tokens，同一条日志里）就是关键自变量，拿它和 `ctx_window` 比：接近阈值说明该让它压缩了。实测样本（姜萌回合）：`llm=43102ms ttft=43101ms decode=1ms` 与 `llm=14135ms ttft=13917ms decode=218ms`——ttft 占 98.5%~99.998%，整回合 65.3s = 13.9s 等首字 + 7.9s 工具 + 43.1s 等首字。**结论：瓶颈不在模型出字速度，先查网关与 prompt 规模。**
* `decode` 占大头 → 模型逐字生成慢（模型/配额/负载）。
* 再看 `工具 X 完成（Nms）` 找慢工具；`rpc.call_tool` 阶段行能区分"等 C# 主线程"还是"动作本身耗时"。
* 其次看 `慢告警` 行给出的"卡在哪一段"。

**「卡住不动」** → grep `慢告警`：`阶段 llm 已运行 …` 连发 = LLM 侧挂起（查本地网关 `127.0.0.1:8123`），若未配 `llm.timeout`，最长可静默等 ~600s×重试；`阶段 rpc.<method> 已运行 …` = 等 C# 主线程无应答（模态窗没关？游戏卡帧？）；`排队 N.Ns 才取得 LLM 并发槽` = 别的 NPC 回合占着槽，属"排队"而非"卡死"。

**「回复没出来 / UI 空窗」** → grep `回合收口`（有没有走到收口）、`事件 npc_reply 未送达/发送失败`、`C# 连接已断开`。

**「一直显示正在压缩中」**（C# 提示行只在收到 `compact_result` 时才更新）：

1. grep `/compact 收到` —— **完全没有这行 = 事件没到 Python**：可能是连接闪断把事件丢在死 socket 上（看 `C# 连接已断开`）、`npc_id` 为空（看 `缺少 npc_id` 告警）、或 C# 侧 `ws==null` 根本没发出。
2. 有 `/compact 收到` 但没有 `/compact 结束` → 压缩中途卡住：`compact.manual]` 慢告警连发 = 卡在压缩内部；`压缩重入被拒 … 上一次压缩卡住了` = 占坑未释放，**再按 /compact 只会立刻被拒，需重启 Python**；`压缩等待回合锁 N.Ns` = 被同 NPC 的回合占着锁。
3. 有 `压缩开始` 但无 `压缩成功/压缩未执行` → 卡在摘要 LLM：看 `compact.summarize]` 慢告警与 `摘要 LLM 调用失败`。
4. 有 `/compact 结束` 但 UI 仍显示压缩中 → 回推失败，看 `事件 compact_result 未送达/发送失败`。

**「进程莫名其妙没了」** → grep `CRITICAL`，异常钩子会带完整堆栈（含线程名）。
**「回答变哑 / 答非所问」** → grep `L1 快照` / `L1 上下文取数失败`：模型可能没拿到人物状态。

**哨兵测试族**：`tests/test_log_setup.py`（装配幂等、落盘与上下文栏、级别热重配、`enabled=false`、相对路径锚包根、慢告警有界封顶、未捕获异常留痕、**职责哨兵**）、`tests/test_log_switch.py`（开关词解析、覆盖 config 的 enabled/level、`resolve_log_switch` 优先级、`--no-log` 压过环境变量且不建文件）。
**未做**：C# 侧 request/response 帧 trace 镜像；日志轮转的磁盘总上限；`llm.timeout` 默认仍为 `null`（= 保持 SDK 默认，建议显式设 60–120s —— 配了它之后本层会同时关闭 SDK 隐式重试，使"一次请求最多等 timeout 秒"成立）；结构化日志（JSON）。

---

## D. 开发铁律与高频踩坑

### D.1 IL2CPP 线程与类型铁律

* **★ 对 Il2Cpp 返回值做向下转型（`is 子类` / `as 子类` / `(子类)x`）恒失败 ★**（**全项目铁律**）。机理：本机 Unhollower（MelonLoader 0.5.x；`UnhollowerBaseLib` 里**没有** `Il2CppObjectPool`）生成的属性/方法 getter 一律「`newobj 声明类型(ptr)`」，**不做运行时类型解析**，返回值 CLR 类型恒为**声明类型**。三档后果：`is 子类` 恒 false（**静默走错分支**）、`as 子类` 恒 null（**静默**）、`(子类)x` 抛 `InvalidCastException`「Specified cast is not valid.」。
  **泛型返回值不受影响**——`GetComponent<T>` / `GetBuilds<T>()` / `List<T>[i]` 的声明类型就是 `T`，具体类型保真（故 `GetChild` 是坑而 `GetComponent` 不是）。
  通解：只调**声明类型上就有**的成员（如用 `Graphics.Blit(Texture, RenderTexture)` 而非先转 `RenderTexture`），或 `IL2CPP.il2cpp_object_get_class(x.Pointer)` + `il2cpp_class_get_name` 取真类再决定。
  **踩中案例**：09-13 通讯录头像恒占位——`PortraitCache.SafeCapture` 原按 `is RenderTexture` / `(Texture2D)` 分派取像素，每次捕获都抛该异常，`portrait_cache\` 一张 PNG 都没落盘。已改为 `Graphics.Blit` 单路径（缩放交 GPU，CPU 只做圆形 alpha 烘焙）。
  **对照组**：`GetChild` 声明返回 `Transform` → `GetChild(i) as RectTransform` 恒 null，一律 `GetComponent` 转。
* **★★ 裸 `const char*` 型 il2cpp API 绝不能用 `Il2CppStringToManaged` 解码——会硬崩且托管 catch 抓不到 ★★**（09-13 实机崩溃根因）。
  `UnhollowerBaseLib.IL2CPP` 里两类指针在 C# 签名上**都是 `System.IntPtr`，编译器无法区分**：`Il2CppStringToManaged(IntPtr)` 要的是 **`Il2CppString*`（托管字符串对象）**，其 IL 是 `il2cpp_string_length(ptr)` → `il2cpp_string_chars(ptr)` → `new string(...)`，即**按对象头读 length 字段**；而 `il2cpp_class_get_name` / `il2cpp_class_get_namespace` 返回的是**裸 `const char*`**。把 `char*` 喂进去 → 读到的「长度」其实是 C 串前 4 字节（`"Rend"` = `0x646E6552` ≈ 16.8 亿）→ **越界读数 GB → 访问违例硬崩**，日志只留一行 `Crash!!!`，无任何托管异常。
  **正确写法**：`Marshal.PtrToStringAnsi(IL2CPP.il2cpp_class_get_name(cls))`（逐字节读到 NUL，无越界可能；类名是 ASCII，ANSI 解码无损。.NET Framework 4.7.2 无 `PtrToStringUTF8`）。
  **诊断手法**：这类崩溃「无异常、无日志、断点前最后一条日志之后」，靠**副作用反推**——当时 `portrait_cache\MTpQXJ.png` 已成功落盘（62KB/256² 合法 PNG），而 `MTpQXJ` 是 `PumpCapture` 倒序遍历的第一个任务、`RealClassName` 又是 `SafeCapture` 里 `WriteAllBytes` 之后唯一还没跑的语句 → 一眼锁定。**别信"native GPU 操作崩溃"的直觉**：产物在，就说明 GPU 段无恙。
* **★★ 异常绝不许丢进「没人 await 的 Task」——那等于永久静默 ★★**（09-13 实机教训）。`MainThreadDispatcher.OnUpdate` 原本只有 `catch (e) { job.TCS.TrySetException(e); }`，而调用方 `WsClient` 派发 UI 事件时**丢掉了 `Enqueue(...)` 返回的 Task** → 未观察的 Task 异常被 .NET 静默忽略：**不崩、不进日志、什么都不留**。
  后果：**UI 事件处理链（`OnUiEvent` → `OnStepEvent`/`OnReplyEvent` …）里任何异常都永久无声**。09-13「NPC 回应中…」卡死的排查就卡在这——`ChatPresenter.OnReplyEvent` 渲染半途抛异常 → 后面的 `SetBusy(false)` 被跳过，而日志里一条异常都没有，只能靠读代码反推。**已在 catch 里补 `ModMain.P`（异常照旧回填 TCS，语义不变）。**
  **通用规矩**：① 任何 `catch` 至少要留一行日志；② `Enqueue`/`Task` 式 API 的返回值要么 await、要么在内部 catch 里留痕；③ **收口/复位类操作（清忙标、关 loading、解挂起）必须放 `finally`** —— 它们的语义是"这一帧结束了"，绝不能依赖前面渲染成功。
* **★★ 同一份数据有「原始层 / 解码层」两套字段时，类型可能完全不同——取错层会静默全空 ★★**（09-13 实机教训）。
  `DataUnitLog.LogData` 上四个成员名字像一家人，类型却分两族（`MelonLoader\Managed\Assembly-CSharp.dll` 实证）：
  `allLog`/`allVitalLog` = `List<Il2CppStringArray>`（**一行 = 编码串切段，元素是 `string[]`**）；`allLogData`/`allVitalLogData`（属性 getter）= `List<LogItemData>`；`_allLogData`/`_allVitalLogData` = 解码缓存本体。
  取数函数只认 `LogItemData`（读 `month`/`logs`/`subLogs`/`DataToString()`），一旦拿到原始层：**三处取值全抛异常，而三处都裹在 `catch { }` 里** → 每条静默退化成 `"(无文本)"`，日志一条错都没有。
  **判据**：输出条目里**连 `month` 字段都缺失**（`{"text":"(无文本)"}` 而不是 `{"month":0,"text":"(无文本)"}`）——只有"赋值抛异常"才会整个字段缺失。
  **通用规矩**：① 有两套同义数据时，先确认哪套是**消费者（游戏自己的 UI/逻辑）实际用的那套**（此处 `UINPCInfoLog.logDatas` = `List<LogItemData>`，即解码层），跟消费者对齐；② 兜底分支别信"名字更底层所以更新鲜"的直觉，先实证元素类型；③ **兜底层返回前必须判 `Count > 0`**，否则空的高优先级层会把低优先级兜底整个短路（原写法 `if (v != null) return v;` 就有这个毛病）；④ 兜底解码结果加**内容校验**（此处只认含汉字的结果），防止编码残渣混进模型上下文。
  **防回归**：`tests/test_log_layer_contract.py` 钉住源码顺序契约（解码层必须先于原始层被尝试），倒回事故写法立刻红。
* **★ 跨层顺序约定：取数处统一成「头 = 最新」，别让每个消费者各自猜 ★**（09-13 实机教训）。
  游戏 `LogData` 的 `allLog/allVitalLog` 是**升序（旧→新）**——真机实证：`regular` 桶里「初入八荒。」（创角）排在「与云含交谈…」**之前**，后者逻辑上必然更晚，而全链路此前没有任何排序。
  但两个消费者都是按「头 = 最新」写的：`ToolExecutor.PageLogs` 按 `(page-1)*5` 从**头**切片且 `log_page` 默认 1；`RecentTexts` 取 `arr[0..2]`/`arr[0..1]` 却喂给 L1 的**「近况」**段。结果：默认第 1 页永远是开局那几条，「近况」段内容是「(1年1月) 初入八荒。」。
  **判据**：同一个列表被两个以上消费者按"前 N 条"消费、而它们的语义分别是"最新"和"最早"时，就说明顺序没在取数处定死。
  **通解**：在**取数处一处**反转（`UnitSnapshot.LogsArr` 倒序遍历），让"头 = 最新"成为全链路真命题（与 `QueryWorldEvents` 既有的 `倒序 = 最近在前` 约定一致）；并加 **方向自检日志**（`OrderInfo` 期望打出 `降`，异常标 `升!`/`乱序!`）——游戏若改了写入方向会立刻显形，而不是靠用户发现"近况怎么是陈年旧事"。翻页文案也要跟着改（`has_more` 在新→旧下指"还有**更早**的"）。
* **★ 确认窗立绘位：左位恒为玩家、右位为对方 —— 传反了不报错，玩家只会看到"两个自己" ★★**（09-12 / 09-13 各踩一次）。
  `ShowDramaService.ShowConfirm/ShowConfirmSimple(ModIds.XxxBase, left, right, …)`：`left` = 屏幕左位 = **玩家**，`right` = 屏幕右位 = **对方**（09-08 用户拍板，反编依据 `DramaData{ unitLeft = g.world.playerUnit, unitRight = unit }`，对齐游戏原版神识传音窗方向）。
  **为什么难发现**：编译器管不到、传反不抛异常、日志也正常（正文与选项都对），只有玩家在屏幕上看见两个自己的立绘与名字。判据就一句：**实机"右侧立绘是玩家" = 左右传反了**。
  两次实例：① 09-12 自主互动确认窗把 `wub`(NPC) 放左位（修正留痕在 `NpcInitiativeMonitor.cs:352`）；② 09-13 `trade` 初版把 `(seller, buyer)` 直接当立绘位传 —— 卖家是 NPC、买家是玩家时右位落成玩家。
  **通用规矩**：两位当事人**都不固定是玩家**的新窗（如买卖、传功、中介），必须按"谁是玩家"推导：玩家在左、另一方在右；双方皆非玩家时退化成"本人左 / 对方右"。
  **防回归**：`tests/test_drama_slots_contract.py` 全仓扫描所有 `ShowDramaService.ShowConfirm*` 调用点，左位实参不得是 NPC 侧标识符（npc/wub/seller/buyer）——两次事故的写法都会被打红。
* **不许污染核心业务层**（用户红线）：业务文件（`ChatPresenter`/`ChatWindow`/`ConfigPresenter`/`ContactPresenter`/`ToolExecutor`/`DramaGate`/`ShowDramaService`/`UnreadStore`/`PortraitService` 等）除诊断开关外零改动；改任何业务文件前先说明理由。
* **自定义 MonoBehaviour 必须有 `IntPtr` 构造**：`public X(IntPtr ptr) : base(ptr) { }`，否则 `RegisterTypeInIl2Cpp` NRE，且**半途注入会毒化 il2cpp 类型系统**。官方范式：壳由 `g.ui.OpenUI` 托管游戏自带 `UIBase`，mod 类只做逻辑组件（基类用普通 `MonoBehaviour`）。
* **`ClientWebSocket` 在本环境不可用，勿再引入**：.NET Framework 版在 Windows 上委托**原生 `WebSocketProtocolComponent`**，在 IL2CPP + MelonLoader 的 mono 宿主里首次 `ConnectAsync` 即卡死，随后原生崩溃。**现役 `csharp/WsTransport.cs`** = 纯托管 RFC6455（`TcpClient` + 手写握手/帧：客户端掩码、Ping→Pong（Python `websockets` 库 20s 保活）、Close、分片聚合、内部写锁）。
* **后台线程绝不碰 UnityEngine——`Debug.Log` 也不许**：IL2CPP 非主线程 interop 会崩。**现役 `ModMain.P()` 线程感知**：主线程走 `Debug.Log`（进 `Player.log`），后台线程 / Init 前走 `Console.WriteLine` + `MelonLoader/Logs/AgentLoopBridge-thread.log`（带时间戳，可 grep）。
* **IL2CPP 集合不能 `foreach`**（用 `Count`/`Length` + 索引器，或 `dynamic` 遍历规避泛型参数被抹掉；`dynamic` 绑定需 csproj 引 `Microsoft.CSharp`）。遍历子节点用索引循环（`childCount + GetChild`），`foreach(Transform)` 抛 `InvalidCastException`。
* **泛型复数 `GetComponentsInChildren<T>` 在此包疑似返回空**（凡用它的搜索一次没中过）→ 搜索一律改**递归索引 + 单数 `GetComponent`**。
* **UI 事件/委托桥**：`UnityAction` 委托桥（`op_Implicit` = `DelegateSupport.ConvertDelegate`）一直存在，缺的只是写法——直接 `AddListener(() => {})` 报 CS1660，须「三步写法」（`System.Action → UnityAction → AddListener`，封装 `ClickUtils.Attach`）；UnityEvent 家族（`onValueChanged`/`onEndEdit`）在 IL2CPP 下不可靠，先转存 `System.Action<string>` 再 AddListener；`InputField` 的 `onSubmit` 不可靠，在 `Update` 里轮询 `isFocused + Enter` 提交；`MakeSlicedSprite` 已钳制 `border < size/2`（越界 → 切角覆盖整张纹理 → 精灵全透明/"十字"化），圆形一律 `MakeCircleSprite`（逐像素抗锯齿真圆）。

### D.2 工程 / 构建 / 部署链

* **老式工程**：`csharp/AgentLoopBridge.csproj` 新增 `.cs` **必须登记 `<Compile Include>`**（`UI\*.cs` 是通配）；`GameManaged`/`GameRoot` 需指向本机游戏安装路径；`.NET Framework 4.7.2` 对齐 MelonLoader 0.5.4（本机仅装 4.8.1 目标包，故以 `v4.8.1` 验证）。
* 构建：`cd F:/agent_loop/csharp && dotnet build AgentLoopBridge.csproj -c Debug -v minimal`；`Debug` 与 `Release` 的 DefineConstants 均含 `MELONLOADER;AB_UI`。
* 部署：`bin\Debug\MOD_Jgmg5L.dll` → `E:\SteamLibrary\steamapps\common\鬼谷八荒\ModExportData\Mod_Jgmg5L\ModCode\dll\MOD_Jgmg5L.dll`，**部署后必须 MD5 两端比对**（历史备份命名 `.bak_MMDD_*`）。
* 并行修改提醒：若另有工具/会话同时改 `F:\agent_loop`，构建会互相覆盖——**以 `bin\Debug` 时间戳 + 部署 MD5 为准**。
* 官方渠道：模组走游戏「模组编辑器」导出的 `ModCode\dll\{namespace}.dll`（本模组命名空间/程序集名 `MOD_Jgmg5L`），由官方桥 `GGBH_MOD`（MelonLoader 日志里的 "ModName v1.0.0.0 by GGBH_MOD" 就是它，**不是我们的 mod**）反射调用 `{namespace}.ModMain.Init()/Destroy()`；**命名空间、程序集名、导出路径三者必须一致**，否则 DLL 加载失败（`ModMainEntry.cs` 是转发包装，缺此层 `Init` 静默失效）。
* **Mod MID 与官方编辑器配置管线**（09-11 实证，立绘剧情窗的数据来源）：
  * **MID** = 编辑器为本模组随机分配的 int 身份号（interop 字段 `ModData.excelMID`）。本模组 **-803158451**。**「重置ID」按钮永远不要点**——所有引用作废。
  * **配置表**：xlsx 放编辑器工程配置目录（`F://mod//ModProject_Jgmg5L//ModProject//{ModExcel,Excel}//`），**ID 字段写 `MID&偏移`**（如 `MID&100`），导出时真 ID = MID+偏移；表头三行（中文名/字段名/类型）从 `modFQA\配置修改教程\配置表头\*.xlsx` 抄。已配三张：DramaDialogue 8 窗 / DramaOptions 16 选项 / LocalText 24 键。C# `ModIds.Mod` 必须与工程 xlsx 的 MID 同源同步。
  * **导出**：编辑器"导出模组"→ 生成 `ModExcel/*.json`（**`1b8bg-` 加密**）。**必须由编辑器导出**——手写明文放 mod 目录会让 `ModImportTool` 解析炸、阻断启动（09-11 事故）。
  * **加载**：`ModMgr.LoadAllMod` → `ModImportTool` 解密合并进全局配置表 → `UICustomDramaDyn(窗ID)` 查表命中才渲染（**不命中 = 静默不开窗**）。

### D.3 UI 生命周期与注入铁律（方案 A 的操作级细节）

* **生命周期 = 方案 A**：面板**按需创建**（自动创建废除）→ `ModAbRes.EnsureInjected` 校验/补注 → `g.ui.GetUI` 复用优先 → `OpenUI`；关闭一律 `g.ui.CloseUI(UITypeBase, false)` 交游戏（`AbXxxPanel.CloseViaManager()`）。**绝不自己 `SetActive(false)`/`Destroy`、绝不动 UI 层兄弟位次**——否则游戏"打开中"登记项永久泄漏、世界输入被门控（09-10 事故）。
* **绝不用"组内/树内任意首 Button"当克隆模板**：NPC 面板树第一个 Button 是 `G:btnClose`——旧代码 `GetComponentInChildren<Button>(true)` 取到它，克隆的 × 钉在真 × 上（真 × 收不到点击、克隆体无文本子节点、点"×"实为静默加好友）。**模板只允许 = 操作行（带文字）或明确按名节点**；找不到就安静返回 false 等重试。
* **按名找节点必须容忍 `G:` 前缀**：本作 UI 节点**真名带前缀**（`G:btnEmail`/`G:goGrid1`/`G:btnFateFeature` 均已实证），反射拿到的**字段名不带**。只按字段名找 → **静默全 miss** → 退到兜底 → 捡一个错的节点（09-07 HUD 圆钮、09-13 剧情窗按钮，**同一个坑踩了两次**）。判据：`NameIs(actual, want)` 同时认 `want` 与 `G:want`。
* **克隆模板必须校验"可用"而非"激活"**：`activeSelf` 为真不代表玩家看得见（父链可能整条关着、`Image` 可能 alpha=0、节点可能 0 尺寸）；模板还必须**自带可改文本**，否则克隆出来是个"写不上字的空热区"。**"已注入"日志 ≠ 玩家看得见**——排查一律先看注入诊断打出的**模板真名 + 克隆 `activeSelf`/`activeInHierarchy` + 屏坐标**三件套。
* **任何克隆行/按钮不得放进受管操作网格**：游戏 `UIOperationGroup.UpdateHandleInput` 逐帧按 `operationItems` 处理网格区输入，操作行是"组的行"，点击不靠 `Button.onClick` 而靠组按条目映射——克隆行放进 `G:goGrid1` 首位会**可见但点不响**。现役做法＝按钮移出网格，挂面板体 × 左侧空位（该层无组机制）。
* **注入去重登记必须在成功之后**：`Injected.Add(instance)` 放在注入尝试之前 → 轮询回调先查 `Injected.Contains` 恒真直接 return，四档轮询永不补注。轮询 guard 与登记必须**同一集合、同一时序**。
* **切页签自愈（v2）**：操作列 `Image/Group:UnitInfo/LanguageGroup/G:goGrid1` 是**跨页签常驻的同一列**，切页签重建的是它的**行子节点**。页签是 `UINPCInfoBase.tglTitle1..7` 七个 Toggle，切换走其 `onValueChanged`（旧假设 `UINPCInfo.Method_Private_Void_Toggle_0` postfix 已被两个完整会话的 `Player.log` 推翻）。**现役**：`WireTabToggles` 直接监听七个页签 → `OnPageSwitched` 幂等补注 + `0.4/1.0/2.0s` **有界**验证。**无常驻轮询**（用户定调"不预支开销，全由真实事件驱动"）；0.8s 常驻巡检方案**已否决，勿回退**。
* **打开链路全通但画面不可见** = 同层多个 ScreenSpaceOverlay Canvas 按 sortingOrder 排被压到游戏 UI 之下 → 显形后 `Canvas.sortingOrder=30000` + `SetAsLastSibling()`；仍不可见再查宿主父链 `activeInHierarchy`、根 RectTransform 尺寸/锚点、`CanvasGroup alpha=0`。
* **UI 让位 hook**：`DramaGate.SendChatBehindHook/SendChatBehind()`——`WorldAiAction` 入口触发、`AbChatPanel.OpenForUnit` 注册，对话窗降到 100 让原生弹窗盖上来，重开自愈回 30000。
* **`g.res.Load` 失败会写 `null` 缓存**，同会话永久命中 `null`，必须重启游戏重测。**`g.ui.OpenUI` 查的是游戏 UI 管理器另一本注册表**（`allAB=0`），`ModAbRes` 只修了 `allRes` 那本——所以对话/配置/通讯录改走 `g.res.Load + Instantiate` 的常驻宿主（`TryCreateResident`），`OpenUI` 路径废弃。
* **`HotkeyPoll` 已删除**（注册 + 方法）：配置常驻后 `ConfigPresenter.Update` 自带 F11 轮询，并存会同帧双 toggle 互抵（`GetKeyDown` 对同帧所有 `Update` 为真）。
* **Unity 预制件 YAML 原位手改会被"生成菜单"覆盖**——手改后**勿再跑生成菜单**（`BuildConfigPrefab`/`BuildContactPrefab` 会从 builder 重新生成）；追加类菜单（`AddConfigTooltip`/`AddChatPortraitSlots`）幂等不覆盖。

### D.4 UnitAction / world_ai_action 完成回调判据（不许再用"立即返回"）

机制：工具发出动作后返回 `DramaGate` pending 标记（`WsClient` 憋住 response，Python future 挂起 → LLM 不说话）；玩家在**游戏原生界面**操作完 → 引擎回调 → `Resolve(slot, 真实结局)` 补发 → LLM 按真实结局收口。撞窗忙键共用 `ModIds.AiActionGate`；`DramaGate` 120s 自动 Resolve"玩家长时间未响应"。

| 动作 | 判据 |
|---|---|
| `lun_dao` / `shuang_xiu` | `StartToUnitAiActionDeferred`：`WorldUnitAIAction1037`/`1031` 挂 `Action<bool>` 回调，**`p==true` 即"完成"**。真机观察点 `[AiAction] lun_dao 完成回调 ok=True` |
| `spar` | **挂起等玩家选，但判据是"选项"不是"动作结束"**（09-14 修）。原生剧情 **21204** 弹「好，就让我和你切磋一下 \| 我现在没有空」，C# 在 `UIDramaBase.ClickOption` 的 postfix 上认出这一对选项（`UI/DramaDrillChoice.cs` 的 `DrillChoiceGate`）→ **玩家一点就定案**，`data.accepted` = true/false。真机观察点 `[DrillChoice] 玩家选了「应战/婉拒」npc=… slot=…`。<br>**为什么不用 `isDrillComplete`**：①该字段语义**没有真机样本**（同类字段 `isInviteComplete` 当年就猜错过，见 yao_yue 行）；②用户拍板"**选项一落定就回，不等战斗**"，而 `OnEnd` 在同意路径要等整场切磋打完。选项 id 是配表事实（`DramaDialogue` id=21204 `options="212042\|212041"`），**免校准**。<br>**漏选兜底**：玩家按 ESC/点窗外关窗不走 `ClickOption` → 走新加的 `UnitActionRoleDrill.OnEnd` postfix → `UnitActionPending` 里那份"没有明确答复"结果（`answered:false`）。**刻意不读 `isDrillComplete`**——未校准就不猜，字段缺失 ≠ 否定。正常路径上 `DrillChoiceGate` 会先 `Remove` 掉登记，兜底找不到条目<br>**反编依据（`F:\DecompDump\dump\unit_action_sigs.txt`）**：`UnitActionRoleDrill` 的 own member 里有 `public unsafe void OnEnd()` 与 `public unsafe bool isDrillComplete` —— 与已挂的 Invite/TeachSkill **完全同构**，此前只是漏登记。护栏 `tests/test_spar_choice_contract.py`（9 个变异全验过，含"硬编码 id ↔ 游戏配表逐字对齐"） |
| `attack` | **保持立即返回**：`UnitActionRoleAttack` **连 `OnEnd()` 都没有**，也没有 `isDrillComplete` 那种落定字段（只有回调 `onDramaClickCall`/`onDramaAttackTipCall`），接不了选择链。语义上也对——攻击是强加行为，对方没有拒绝权 |
| `economy_item` give | 挂起；结局按 `receive/refuseProps` 分区；give 道具在拆背包前先占坑防回滚浪费。**`giveAction.isCheckUnitProps = false` 是必需项，不是优化**（见下条） |
| `economy_item` give · **整栈丢失事故（09-13 定案）** | **症状**：送某些道具不弹窗、工具挂 120s 被 Python 判超时，而**道具已经从背包消失** → 第二次再试报"所有赠送项均未达成"（模型据此编出"许是贫道记错了"）。诡异点：**送丹药可以、送筑基丹不行**。<br>**根因**：`UnitActionRoleGive.isCheckUnitProps` 默认 **true** = 动作创建时按 `soleID` **回查发起方背包**是否仍持有这些道具。而 `TakePropsFromBag` 是**先 `DelProps` 拆栈、再建动作**：送**整栈**（筑基丹×1 全拿走）→ `soleID` 从背包消失 → 回查得 `null` → `IsCreate` 内部 **`NullReferenceException`** → 动作建不起来 → **游戏不播剧情窗**；送**部分**（蓄力丹 9 送 1）→ `soleID` 还在（剩 8）→ 回查通过 → 窗正常弹。**差别只在是不是整栈**，这就是"换个道具就成/不成"的全部原因。<br>**修法**：`giveAction.isCheckUnitProps = false` —— 该字段存在的意义就是给"调用方已自行拆栈"的场景；我方在拆栈**之前**已用 `PropsItemCanGive` 逐件校验，且实物就在 `giveProps` 手里（探针证实 `giveProps=1`），跳过重复回查不会放过非法赠送。<br>**真机证据**：`[Give] 预检 拆出道具数=1 giveProps=1 isCheckUnitProps=True IsCreate=抛异常` + `IsCreate 预检抛异常: System.NullReferenceException`。<br>**教训**：这条路此前**除结果外零日志**，导致"没弹窗"时无法区分「没走到 / 占不到槽 / 动作建不起来 / 动作建了没播剧情」——补上 `[Give] 进入` / `[Give] 预检` / `[Give] CreateAction 已返回` 三段探针后**一轮定位**。业务路径的入口留痕不是可选项 |
| `trade` | 复用 +100 段（DramaGate 全局只允许一个挂起窗 + 壳条目正文全运行时覆盖 → 不必新占段）；`ShowConfirm` 的 `left/right` = 卖方/买方立绘 |
| `item_acquire` ask_for | 挂起；结局 = `isAskforComplete` + `isNPCReduceIntim` |
| `item_acquire` steal_item | **经用户拍板保持立即返回**（暗中行事无选择 UI，结局 = 概率结算） |
| `world_ai_action` yao_yue | 挂起；**`accepted` 以 `isInviteComplete` 为唯一判据**——实测接受 = `(True,True)`、拒绝 = `(False,True)`；`isNPCReduceIntim` 两条路径**恒 true、无判别力**（配置表 `RoleInvite.intimateDecline=-120` 佐证），仅随 `data` 透出。旧注释"isInviteComplete 有'拒绝/完成'二义"**已证伪**。邀约全程走游戏原生 UI：工具→`UnitActionRoleInvite`→弹 **81002**（选项 `810021`=接受 / `810022`=拒绝，**只有这两个**）→接受走 lambda `b__11_2`→弹 **81012**（6 变体，全含 `{0}`）→`OnEnd`；我方只挂 `OnEnd`，自建窗 `ShowConfirm` **不参与**邀约 |
| `world_ai_action` yao_yue · 原句捕获 | **`DramaTextCapture`（✅ 真机验证通过）**：第二层剧情里含约定地点，抓下来随结果透出。**唯一数据源 = `UIDramaBase.GetDialogueText` 的返回值**（`UI/DramaTextHook.cs` postfix）——游戏填好的**成品句**（`{0}`→"新达镇"），零推断。**两条被实测排除的路（勿回退）**：① `DramaTool.lastOpenDramaDialogueText` 是**未替换的模板**；② `DramaTool.lastOpenDramaDialogueValues` **不含 key 0**（实测 `values0=<no-key-0>`）。配对 = `BeginInvite(npc)` 武装 → 每次 `InitData` 覆盖式记录 → `OnEnd` 取走；仅 `accepted:true` 时透出 `data.invite_text` |
| `world_ai_action` chuan_gong | `UnitActionRoleTeachSkill` 挂起；**`skill` 参数可选**——省略时 `FirstTeachableSkill(wub)` 挑一本作构造种子，原生面板照常弹出由**玩家自选**；`OnEnd` 回读 `teach.gainSkill` 经 `MartialDisplayName` 得**实际所学中文名**写入 `data.skill`（回退种子名），`data.skill_source=gainSkill\|seed` 标注来源。interop 实证该类有 **teachSkill（构造种子）/ gainSkill（所学）/ giveUpSkill** 三独立属性；`gainSkill` 是否 = 玩家所选尚无真机样本 |

落地结构：`UnitActionPending.cs`（实例指针→槽位 + 结果组装委托登记表）+ `UnitActionHooks.cs`（`UnitActionRole{Give,Askfor,Invite,TeachSkill}.OnEnd` 四类**私有**方法的 Harmony postfix，按名 patch）。**OnEnd 四钩子须加 try/catch**（异常会顺游戏动作结束链上炸）。真机观察点 `[UnitAction] <op> OnEnd 挂起命中 slot=N`；若 120s 超时且无日志 = 对应动作不调 `OnEnd`，需改轮询 `isComplete` 方案。

### D.5 坐标 / 锚点 / 视觉经验

* HUD「传」钮：模板改 `G:btnEmail` 优先链（此前克隆 `G:btnPlayer` 头像钮导致"第二个玩家头像"）；全 HUD 搜境界文本（**炼气/筑基/结晶/金丹/具灵/元婴/化神/悟道/羽化/登仙**，`Text` + `TextMeshPro` 双轨）→ 挂容器之父、落**修为容器右缘外**，实测锚点 `(110,2)`、与左邻间隙 36；搜不到退回模板右侧；两种走法都打点。
* 面板操作按钮常量：`CreateBarButton` 的 `w≤120`、`gap=8`（视觉模板取 `goGrid1` 的"交谈"行）；改字同时支持 `TextMeshPro`。
* 立绘管线：对"3D 模型未进场景"的 NPC 调立绘 API → 0 尺寸 RawImage → 游戏每帧刷 `RenderTexture.Create failed: width & height must be larger than 0`（单局 20 万行级）。**通讯录立绘已整体移除**（头像恒为占位圆），对话窗立绘走 `PortraitService` 原生管线不受影响。
* **悬停气泡（`UI/HoverTip.cs`）的两条静默失败（09-13 定案；症状都是"气泡一个都不出、日志无异常"）**：
  * **① 相机必须随画布**。`RectangleContainsScreenPoint` / `ScreenPointToLocalPointInRectangle` 要求 Overlay 传 `null`、`ScreenSpaceCamera`/`WorldSpace` 传**画布相机**。旧代码写死 `null`——这只对**代码自建面板**成立（`ConfigUiBuilder`/`ChatUiBuilder`/`ContactUiBuilder` 都把 canvas 设成 `ScreenSpaceOverlay`）；而 **AB 面板的 Canvas 是游戏 UIMgr 自己挂的**，真机 dump 实证是 `ScreenSpaceCamera`（Player.log：`[Repair] StartGameTip … <Canvas mode=ScreenSpaceCamera order=20>`、`[AbChatPanel] 挂载链 … UI[on](Canvas mode=ScreenSpaceCamera order=0)`）→ 屏幕坐标被当世界坐标 → **恒不命中**：登记全在、`Update` 照跑、气泡永远不出。这正是"同一份 HoverTip，预览工程/代码版有气泡、AB 版一个都没有"，也是配置面板 09-12 换 AB 壳后才暴露的原因。现役 `ResolveCamera()` 运行期解析：本节点 `GetComponent<Canvas>()` → 父链 → Overlay 则 `null`、否则 `canvas.worldCamera` → `rootCanvas.worldCamera` → `Camera.main`；画布晚挂（游戏侧 `AddComponent`）时**不置就绪位、下帧重试**。**唯一正确写法是"取画布相机"**——不要写死 `null`，也不要写死 `Camera.main`（`DramaAiOption.LogInjectDiag` 早就按这条取相机，是 HoverTip 漏了）。
  * **② 锚点必须对齐画布 pivot**。`ScreenPointToLocalPointInRectangle` 返回**以画布 pivot 为原点**的局部坐标，而 `anchoredPosition` 以**自身锚点**为原点——两套坐标只有「锚点 == 画布 pivot」时才同系。旧代码假定画布 pivot 恒为 `(0.5,0.5)`；本作预制件根节点常见 `(0,0)`，写死会让气泡整体偏半个画面 → 定位到屏幕外 = **同样"没有气泡"**。现役在 `Show()` 里 `rt.anchorMin = rt.anchorMax = _canvasRt.pivot`（`NormalizeLayout` 只归一化 scale/锚点/offset，**不动 pivot**，不能指望它）。
  * **③ 成功路径也要留痕**：`HoverTip` 此前**零日志**，"不出气泡"只能靠猜。现役三行——`装配：模板=预制体模板|代码现造 文本=有|**无** 画布=<Overlay(相机=null)|ScreenSpaceCamera(相机=X)> 宿主=`、`登记就绪：N 条 画布=…`、`首次命中 区域#i 相机=… 文本=…`，失败径另有 `浮出失败：`。**排查固定顺序**：`登记就绪` 从未出现 = `Register` 根本没跑到（看文件列表是否拉回、`desc`/兜底文案是否为空）；`画布未挂（下帧重试）` 常驻 = 画布没挂上；`相机=**缺失**` = 画布无 event camera；`首次命中` 从不出现而登记数 > 0 = 相机/坐标系仍不对。

### D.6 游戏数据源与本地化（反编/真机实证）

* **★ 兜底清理（09-13 用户拍板"链路确定了就别再兜底"）★**：C# 工具层原有 450 处 `catch`（`ToolExecutor` 217 + `UnitSnapshot` 233），
  其中一批是**分层兜底**（A 路失败试 B 路再试 C 路）。判据不是"看起来多余"，而是**代码里已有的自检日志**：
  `命中主路` 9 / `命中回退` 0 / `空结果` 0；`attempt=0` 9/9（重读循环从未触发）；`两层不等` 0/9。
  **已删（可逆，备份在手）**：
  * `LogItemText` 的 `DataToString()` 兜底 —— 它是**序列化格式**（`StringToData` 的逆），不是人话，
    唯一产出过的东西就是 L1 近况里的 `0&A&A`。现在片段全空即**如实返回空串**。
  * `SetAttr` 的 `PdOf` 裸字段兜底 —— 面板口径是 `DynInt`（含加成 + 上下限钳制），裸字段是未加成值。
    这正是 09-13「境界/声望取错」那条 bug 的**同一形态**，降级成兜底只是把同一个错藏得更深。
    现在**只认 DynInt**，缺失即不落键，并打 `[Attrs] ★DynInt 缺失★ prop=…`（去重封顶 60）——
    真机若恒定缺某属性，那行日志会立刻指出来，再**有针对性**地补读取路径。
  * `ReadLingshiHeld` 的 `totalSchoolMoney` 兜底 —— 那是**宗门钱**，与随身灵石是**两个账户**。
    旧行为在读取失败时把一个貌似合理的数字交给模型，模型据此报价/赠送而实际看的是另一个钱袋。
    现在失败返回 `-1`，三个调用点（trade 预检 / economy_item 预览 / TransferLingshi）**显式失败**；
    trade 的余额字段改成"读得到才写"（绝不把 `-1` 当余额报给模型）。
  * 旧版参数别名：`initiator ?? target` 的 `target`（6 处）、`economy_item` 顶层 `item`/`item_name`/`count`
    单件形态、`trade` 的 `item_name` —— 上一轮全量核对确认**全部不可达**。`bridge.py`（离线桩）同步删除。
  * `dialogue_agent.py` 的 `[排查] inspect_unit res 原始载荷` 调试转储（每次 inspect 写 1500 字日志）。
  **保留（不是兜底）**：`RollbackGive`/`TransferLingshi` 补偿回滚（道具原子性）· IL2CPP per-field `try/catch`（隔离）·
  `UnitLookup` 全图按名匹配（中文名是主键）· `"(无文本)"`（**失败信号**，删了就退回静默）· `jie_yi` 双写 ·
  `AreaNameFallback`（`topic=region` 拿它当州名词表）· `BuildLogs` 的多层读取（见下，用户明确保留）。
  **未动（链路未验证）**：`social_relation` 的 BreakWith→字段直写、`BrotherBack→Brother` 回读 ——
  Python 日志显示这三个工具**一次都没跑过**（只跑过 `economy_item` 24 / `trade` 6 / `world_ai_action` 9），
  这些兜底是**未拆的保险**，删了等于把"没测过"直接暴露给玩家。
* **★ `@` 引用标记按类型字母分流（09-13 全量实测修正）★**：真机两会话统计 ——
  **`@q_` 221 次全部 2 字段**（`@q_寇炫明(好友)|cPoLMG@`，**字段0 就是显示名**，字段1 才是 unitID）；
  **`@w_` 14 次**：12 次 5 字段（`@w_zcW8xn|1|1011111|13|@`，字段2=propsID → 六品培元丹）
  + 2 次 19 字段（`@w_gdZZA1|2|0|0|4|88003|…|@`，**无 propsID**，`88003` 命中 `BattleAbilityBase` → 大法）。
  ⇒ 旧实现一律按"字段0 是 ID"处理，把 `q` 的**正确结果**打成"未解"（**198/221 是误报**），
  又把 `w` 长形态的裸 soleID（`gdZZA1`，对模型纯噪音）漏进上下文。
  现在：`q` → 字段0（并记为"人物"）；`w`/其他 → 逐字段试 `ItemProps` → `BattleAbilityBase` → unitID；
  仍解不出且是 `w` → **`（未知道具）`占位，绝不回裸 ID**（原样留在 `@标记未解` 日志里备查）。
  **为什么"逐字段试"安全**：`ItemProps` id 从 10001 起、`BattleAbilityBase` 从 101 起，都远大于标记里的类型/序号字段。
* **★ 工具契约三面一致性（09-13 全量核对，机械化）★**：这条链上有四个互相独立的"声明面"，任何一处单方面改动**都不会报错**，只是行为悄悄不对 ——
  `schemas.py`（模型据此生成参数）/ C# 真正读的 args / C# 真正发的 data 键 / `text_render.py` 真正读的键（**没被读 = 模型永远看不到**，本项目已踩四次：性格注解、性别、位置、道具 ID→中文名）。
  现固化为 `tests/test_tool_contract_consistency.py`（8 条，全部变异验证过）。本轮核对结论：
  * ✅ 分发与 `TOOL_ORDER` 一致；**无死参数**（schema 声明的每个键 C# 都读）；6 个枚举参数取值全对齐（`social_relation.op` 13 / `movement.op` 3 / `world_ai_action.op` 6 / `item_acquire.op` 2 / `query_world.topic` 5 / `inspect_unit.log_filter` 3 + 嵌套 `cat`/`board`/`relation`/`race`/`sex`）。
  * ✅ 所有数值默认/钳制与 schema 对齐（`log_page` 1、`top` 10/1..200、`count` 12/1..120、`value` 5/1..10）。
  * ✅ 渲染层读的键 C# 全都发（`age`/`life` 是 `SetAttr(t, ..., key, ...)` 动态写出的，已在测试里白名单注明）。
  * **三类"允许的不一致"**（白名单 + 测试头写明理由）：① `initiator` —— **harness 注入**（`dialogue_agent._execute_tool_calls`），刻意不进 schema（发起方恒为当前对话 NPC，不能让模型指定）；5 个动作工具的 actor 全靠它，**注入被删会全部以「未找到发起方」失败**，故专门一条测试盯着；② `target` —— 旧版兼容别名，现状无人写它；③ `economy_item` 顶层 `item`/`item_name`/`count` —— 旧版单件形态兼容，同样不可达。
  * **本轮修掉的三处真不一致**：① `item_acquire` schema 描述还写着 `props_detail`（**09-03 就被删了**，模型被指引去找不存在的键）；② `inventory_top` 只做裸转型不钳制（传 99999 静默等价于 `inventory_all`，与 `top`/`count` 口径不一致）；③ `economy_item.via` 的 schema 描述暗示它管整体送达方式，实际**只影响纯灵石那条路**（含道具时道具恒走原版赠送 UI），描述已按实情改写。
  * **顺带补上两处"C# 发了、模型看不到"**：`trade.buyer_item_before/after`（到货实证 `0→2`，此前只渲染了 `item_moved` 布尔）与 `world_ai_action.skill_source`（`gainSkill`=玩家在原生功法面板自选 / `seed`=NPC 功法槽播种；不区分会把玩家自选的功法说成"我传你这部"，语气反了）。
* **工具结果分工（勿回退）**：C# 只回**纯原始数据**（`Ok(data)`，叙述字段 `note`/`text` 全退役），自然语言叙述唯一来源在 Python `tools/text_render.py`（`render(name, args, res)`，异常/缺失 → `None` → 回退 `json.dumps(全量)`；UI 行动记录行与 LLM `tool_result` 同一产物）。**忠实原则**（用户定调）：data 里有的语义字段一律呈现——不截断列表、不隐藏失败项、不省略数值；省 token 只靠"去掉 JSON 结构包装"；**字段缺失 ≠ 否定**（`completed`/`accepted` 缺失不能说成"未完成/被拒绝"）。逐工具字段表见 [`tool-result-contract.md`](tool-result-contract.md)。
* **本地化归 C#**：conf 查表中文字段（性格/魅力档位/关系中文/区域名）在 C# 完成，Python 只组句。
* **区域名**：`ConfWorldAreaBaseItem.name` 只存**本地化 key**（形如 `areaName9`），必须过 `GameTool.LS()`。现役 `AreaLabel(raw)` = `GameTool.LS` → 若返回值**仍是 `areaName…` 形态或为空**则查静态兜底表 `AreaNameFallback`（`AreaName()` 与 `search_units` 的 region 过滤共用）。
* **权威本地化表**：`E:/SteamLibrary/steamapps/common/鬼谷八荒/Mod/modFQA/配置修改教程/配置（只读）Json格式/LocalText.json`（**54,991 条**，字段 `id`/`key`/`ch`/`tc`/`en`/`kr`）。`areaName1..13` = 白源区 / 永宁州 / 雷泽 / 华封州 / 十万大山 / 云陌州 / 永恒冰原 / 暮仙州 / 迷途荒漠 / 赤幽州 / 天元 / 冥山 / 太行山（早期按解锁顺序反推的表把 `areaName11` 记作「天元山」且只到 11——**以 LocalText.json 为准**）。
* **建物枚举必须用泛型重载** `g.world.build.GetBuilds<T>()`（`MapBuildSchool` / `MapBuildTown`）：**无参重载**元素转不成 `MapBuildBase`，被 `catch { continue; }` 静默跳过 → 全图建物被逐条吞掉、结果恒空（`places`/`sects` 双双为空即此）。逐条 try/catch 但要**记录失败原因** + 末尾计数日志 `[Builds] CollectNamedBuilds：宗门=N 城镇=M`。
* **道具介绍**：`DataProps.PropsData.propsItem` = `ConfItemPropsItem`，除 `name`/`worth` 外同一行还有 **`desc`**（本地化 key，LS 后即悬浮窗介绍）；装备（`EquipsDetails → GetProps(soleID)`）拿的是同一个 `PropsData`。悬浮窗的结构化效果（体力+75 / 消耗 8 念力 / 冷却 20 秒）**不在此表**，疑似 `propsInfoBase`——二期再挖。
* **功法（技能）说明**（09-13）：**不要自己查表拼**，走 `UIMartialInfoTool.GetDesc(DataProps.MartialData)` —— 反编实证它是 `abstract+sealed`（静态类）上的 `public static string GetDesc(MartialData)`，**与面板「技能说明」同一入口**。同族的 `GetDescRichText(...)` 才是带图标/染色数字的富文本版，故 `GetDesc` 是纯文本版。
  * 说明文字在游戏内由「前缀 `ConfBattleSkillPrefixValueItem.desc` 模板 + `ConfBattleSkillValueItem{key,value1..10,icon}` 数值」组装 —— **模板里的占位符只有游戏侧会替换**，自己按 `md.prefixs[].prefixValueItem.desc` 拼出来是一串未替换的占位符，所以只留一个入口、不做本地拼装兜底。
  * 拿到后必须过 `UnitSnapshot.StripRichTags`（`<color=…>`/`<sprite name=…>`/`<b>` 是 UI 排版，对模型是纯噪音）：**只剥标签 + 折叠空白，不截断不改写**。
  * 取不到 → **不落 `desc` 键**（字段缺失 ≠ 没有说明）+ `ModMain.P` 留痕（空/异常两条路径都不许静默）。全链路只有 `inspect_unit(classes=["abilities"])` 会带上它（**L1 只要 `brief`**，所以不吃这份体积）。
  * **★ 拿到的 `GetDesc` 是模板原文，必须自己替换占位符 ★**（09-13 第二次修：用户投诉"中间夹杂太多占位"）。
    `UIMartialInfoTool.GetDesc(md)` 返回的**不是**面板上那串人话，而是带记号的模板。三类记号、三个来源，
    语法与来源**全部用游戏自带配置表离线实证**（`Mod/modFQA/配置修改教程/配置（只读）Json格式/`）：
    | 记号 | 含义 | 来源 |
    |---|---|---|
    | `<y>…</y>` / `<g>…</g>` | 游戏自带**配色标签**（不是 Unity 富文本） | `StripRichTags` 剥掉（剥的是通用 `<…>`，两类通吃） |
    | `&expr&` | **数值**，如 `&22111_range&` | `ConfBattleSkillValue`。★该表主键**自带前导 `&`**（实证 key = `&22111_range`，`value1..value10` 为各等级值，`valueScale="x1\|f0"` 是格式）★ |
    | `$key$` | **本地化文本**，如 `$s_dao$`→刀法、`$s_xueren$`→血刃 | `GameTool.LS`（`LocalText.json` 的 key） |
    * `&expr&` 里含 `|` 的是**算式**：`22111_xzsh|x22111_dmg|/100|f0` = 两值相乘 ÷100 保留 0 位小数 —— 它**不是表里的行**（`&…|…&` 查无此键），必须走 `ConfBattleSkillValue.ValueMathf`（单值走 `GetValue`）。
    * **不猜值**：算式解析失败**绝不退回单值** —— 只取一个因子会给出错误的数，比留个可见占位符更糟。查不到的记号**原样保留**并统计残留留痕（`[Abilities] ★技能说明仍残留 N 个占位符 …片段=…`）。
    * **★★ `GetValue` 返回的是未缩放的原始值，必须自己套 `valueScale` ★★**（09-13 第三次修）。
      真机症状：`2秒` 显示成 `2000秒`、`8秒` 显示成 `8000秒`、`50%` 显示成 `5000%`、`7%吸血` 显示成 `700%`。
      根因：缩放系数在**同一行的 `valueScale` 列**，形如 **`x<系数>|f<小数位>`**（全表实证：系数只有 `1 / 0.1 / 0.01 / 0.001 / 0.0001`，小数位 0~3，还有一条大写 `F1`）。
      实证三行：`&510011_cxsj` value1=**2000** × 0.001 = **2**（面板 2 秒）；`&32111_tsgj` 5000 × 0.01 = **50**（面板 50%）；`&22111_dmg` 480 × **1** = 480（系数为 1，所以"有时看着是对的"）。
      现流程：`GetValue`（负责按等级选列）→ `ApplyValueScale`（套该行 `valueScale`，**去掉多余尾零**：`2.00`→`2`）。
      **算式里的每个操作数也各自套自己的 `valueScale`**（`NumOf`），因为 `ValueMathf` 是否替操作数缩放无法离线确认（已知 `GetValue` 不缩）。
      算式自算为主、`ValueMathf` 仅兜底，并打一行**对照日志**（`[Abilities] 算式自算=… 游戏ValueMathf=…`）留判据。
      真机原值→期望值留档在 `tests/test_ability_desc_contract.py` 的 `_GOLDEN`。
    * **键的两种写法都试**（`inner` 与 `"&"+inner`）：表主键带 `&`，但 API 形参名是 `key`，内部是否补 `&` 无法离线确认 —— 同一键的两种写法，不属于"猜值"。
    * 真机模板原文（`LocalText.skill_attack_desc22111`）与替换结果留档在 `tests/test_ability_desc_contract.py`（`TPL_22111` / `EXPECT_22111`），可当回归对照。
  * 渲染层**逐槽一行**（`功法：` 换行后 `· 灵技「名」(id=…)——说明`）：说明动辄上百字，沿用旧的单行 `，` 拼接会糊成一片。契约测试 `tests/test_ability_desc_contract.py`。
* **★★境界（面板口径）：`gradeID` 是「行号」不是「大境界号」★★**（09-13 真机事故："他爸显示成登仙境"）。
  现场（**走的是 unit_id 精确路径，与重名无关**）：`Resolve('', unit_id=ECYyXU) → ②unitID精确` → `"realm": "登仙境"`，而面板上此人是**结晶后期**。
  * **根因**：`RoleGrade` 表**一行 = 大境界 × 期 × 品质**（44 行 / 10 个大境界 / 3 期）；而 `g.conf.roleGrade.GetGradeName(Int32 grade)` 的形参名是 **`grade`（大境界号 1..10）** —— 同族 `GetNextGradeItem(Int32 gradeId)` 的形参名才是 **`gradeId`（行号）**。**参数命名本身就是证据**。于是行号被当大境界号喂进去：结晶后期行号 ≈11 > 10 → 查表**越界钳到最大档 → 登仙境**。低阶 NPC（行号 1~3）恰好落回炼气/筑基，所以"有时看着是对的"——这类 bug 最容易被误判成重名。
  * **修法（不猜，自洽校验）**：把 `gradeID` 当行号在 `allConfList` 里定位，判据是**行内 `grade` 必须与 `dynUnitData.curGrade` 对得上**（`curGrade` 是面板同源的大境界号，参照 mod `GetGradeItemMaxQuality(unit.data.dynUnitData.curGrade + 1, 1)` 实证其语义）。对上 ⇒ 取该行 `LS(gradeName)+LS(phaseName)` = **"结晶后期"（与面板逐字一致）**；对不上 ⇒ **不采信**，退 `GetGradeName(curGrade)`（丢"期"但不错人）。行号 1 基/0 基两种下标读法都试。原始数字写进 `realm_diag` 随 brief 回传，`[Realm]` 日志一行给出结论。
* **★面板数值一律 DynInt 优先 —— brief 曾经不是★**（09-13，与上一条同源）。面板读 `dynUnitData.<X>`（`DynInt.Value(null,true,true)`，含气运/装备加成并钳制），而 `brief` 原先直接读 `propertyData` **裸字段**。
  真机实证：某 NPC 声望裸值 **3129**、面板 **3379** —— 差额 **250** 正好是其气运「赶尸道童 +100 / 单身贵族 +150」。现 brief 的 `beauty/reputation/talent/mood/age/life` 全部走 `SetAttr`（DynInt 优先），与 `stats` 同口径。
* **★自身段身份：打开对话 UI 时把真身钉下来★**（09-13）。`npc_id` 全链路是中文名（Python 侧会话/人设/存档路径都以名为键，改 unitID 是伤筋动骨的迁移），但**打开对话 UI 的那一刻 C# 手里真的握着 `WorldUnitBase`**（NPC 面板按钮 / 通讯录行点击 / 剧情窗按钮 → 全部汇到 `ChatLauncher.OpenForUnit`）。拿着真身却只传名字、后面再按名全图猜 ⇒ 自身段可能挂在同名者身上。现于该入口 `UnitLookup.Pin(name, unitID)`，按名解析先查登记（分支 ⓪，排在**玩家直达之后、全图扫描之前**）；登记失效（换存档/单位消失）自动摘除并回落。

* **查询条数交给模型**（09-10 定）：`rankings` 的 `top` 上限 `20 → 200`；`events` 新增 `count`（`1~120`，默认 12 ≈ 最近一年）。上限只是防呆，默认值不变。
* **关系枚举**：`GetRelation(player)` 返回枚举，`"None"`/`""` 未映射会渲染出"关系None" → C# `UnitSnapshot.RelationCn` 统一 `None/"" → 陌生`（与 `search_units` 的 `RelationMatch` 口径一致）。
* **性格**：`propertyData.inTrait`(内 1) + `outTrait1/2`(外 ≤2) → `g.conf.roleCreateCharacter.GetItem(id)` 的 `sc5asd_sd34`(名) / `xdash_54sd`(面板注解，LS）→ `role_character_nameN` / `role_character_descN`；输出 `{inner, inner_desc?, outer[], outer_desc?}`（两条渲染路径都要带上注解：inspect 的「性格注解」与 L1 自身段的「本性X（注解）」，**只补一条是历史踩过的坑**）。
* **★ 经历文本里的 `@` 引用标记：字段0 是 soleID，道具表 id 在中间字段 ★**（09-13，第四次「静默降级」）。
  真机 L1 近况出现 `在溪望镇的坊市中购买了zk8xkN。`，而游戏经历面板同一句显示绿色的 **五品培元丹**。
  * **标记真实格式**（铁证取自 Player.log 里游戏自己的剧情文本 `我这里有一个@w_rIsZH3|1|5031101|48|@准备赠于你`）：
    `@<类型字母>_<字段0>|<字段1>|…|@`。该样本字段2 `5031101` 在 `ItemProps.json` 里存在
    （`name = item_name5031101` → LocalText **青须藤**）⇒ **中间的数字字段才是道具表 id**，字段0 是道具的 soleID。
  * 旧实现 `Regex.Replace(s, "@[a-zA-Z]_([^|@]*)(?:\|[^@]*)?@", "$1")` 只输出**字段0** ⇒ 模型看到 `zk8xkN`。
    现改为 `MatchEvaluator`（`RenderAtTag`）**逐字段试解**：任一纯数字字段能查到 `ConfItemProps` 行 → 该道具中文名；
    否则字段0 当 unitID 查人名；都不行 → **退回字段0**（= 旧行为，绝不更差）。
  * **"逐字段试"为什么安全**：`ItemProps` 全表 3295 行、id 从 **10001** 起、**无 <1000 的 id**
    ⇒ `1`/`48` 这类序号字段查不到任何行，不会误命中。
  * `DataToString()` 是**序列化格式**（`id&values&conditionValues` 用 `&` 连接，`StringToData` 是其逆），不是人话：
    某元素 `GetLogString()` 为空而回退到它时，就漏出 `0&A&A` 这种残渣（游戏面板也不显示）。
    现由 `KeepLogPart` 丢弃 —— 判据刻意收窄：**无中日韩字符 且 含 `&`** 才丢，纯数字/符号的 subLog 照常保留。
  * **留痕**：每个**新**标记打一行 `[Logs] @标记 <原样> → <渲染结果>`（去重、封顶 40 条）；解不出来额外打
    `[Logs] @标记未解 … → 退回字段0`。下一次真机日志即可核对道具种类全覆盖、并积累其他类型字母的证据。
* **★ L1「近况」为什么从"按桶配额"改成"合并取最近 N 条" ★**（09-13，用户问"不是说默认三条吗，怎么只显示了两条"）。
  旧写法 `limits = {3, 2}`（vital 前 3 + regular 前 2）**在真机上退化成恒 2 条**：Player.log 的 `分层[...]` 自检行显示
  所有查过的 NPC 都是 `vital=0, regular=5..15` —— **重要桶恒空**，于是 3 那个配额从不生效，只剩 regular 的 2 条，
  而 regular 里明明有 14 条可用。现改为**两桶合并 → 按账面月降序 → 去重（月,文本）→ 取最近 `RecentCap`(=6) 条**：
  ① 「近况」的语义就是"最近发生了什么"，按时间取比按桶配额更符合直觉；② 按桶配额会出现"一条陈旧的要事挤掉三条新的日常"，
  时间线是断的；③ 要事不会丢 —— 它通常本身就是最新的，且 `inspect_unit(logs, log_filter=important)` 才是查要事的正路。
  **`RecentCap` 是唯一旋钮**（每条经历 30~60 字，6 条 ≈ 350 字；该段随上下文常驻，别开太大）。
  配套两处措辞修正（09-13，用户指出）：① `LogItemText` 里 **`logs[]` 是同一句的片段，必须直连**
  （`string.Concat`）—— 旧写法 `string.Join("；", parts)` 会插出 `基于自我成长的需求，；认为…`，与游戏面板逐字不一致；
  `subLogs[]` 是附加明细，仍用 `；` 跟在主句后。② Python 侧**去掉正文的「近况经历：」前缀** ——
  段标签已是「近况」，渲染出来是 `Current runtime context —— 近况：近况经历：1. …`，两个同义词叠着纯属噪音，
  现在是 `Current runtime context —— 近况：1. …`。
* **★ C# `raw` 里有字段 ≠ 模型看得到：L1 成文在 Python，漏读是静默的 ★**（09-13 已踩三次：性格注解、性别、位置）。
  模型上下文里的人话由 `system_prompt.format_l1_context(raw)` 生成，C# 只负责把结构化 `raw` 发过来。**Python 没读的字段不会报错、不会缺段，只是模型"不知道"** —— 而模型不知道的表现是"回答变哑/答非所问"，不是崩，极难归因。
  **三次实例**：① `personality.inner_desc/outer_desc` 只进了 inspect 那条渲染路径，L1 自身段一直只有名字；② `raw.self.sex` 由 `GameContext.GetL1` 一直提供，`format_l1_context` 从没读过 → 模型不知道自己是男是女，玩家块更干脆**没有** `sex` 字段 → 玩家段代词写死 `"他"`，女玩家被成文成"你与他结着道侣之谊"；③ **键名不一致也算没读** —— 位置：C# 发 `point{x,y}`，成文侧写的是 `pos = self_raw["pos"]`（等地名），而 `grep '["pos"]' csharp/*.cs` = **0 处** → `栖居于{pos}` 是死分支、自身段位置**恒空**。
  **跨端字段名不一致比忘记读更难查**：两端各自都自洽，跑起来不报错、不缺段、日志干净 —— 只是模型"不知道"。查这类问题的通用手法：**在成文侧反查每个 `raw.get(...)` 的键，去 C# 里 grep 一遍是否存在**（本次 `pos` 就是这样一枪打中的）。
  **通用规矩**：① 增删 `raw` 字段时**两端一起改**（`GameContext`/`UnitSnapshot` ↔ `format_l1_context`），并各加一条断言；② 成文侧对**枚举型取值**做白名单（`if sex in ("男","女")`），既防脏值外泄（不写成"一位None子"），也天然给出缺字段时的旧行为回退；③ 代词这类"中文里绕不开"的东西必须由数据决定，别写死。
* **★★ 重名事故：按名字查人会静默查到另一个人（09-13）★★** —— 查人一律认 `unit_id`，不认名字。
  真机现场（Player.log 原文）：`[UnitLookup] Resolve('益婉容') → ③全图按名#199 unitID=Xs6JDI`。
  玩家面板上那个益婉容是**结晶后期/声望3379**，工具端出来的是**登仙境/声望3129** —— 两个都叫「益婉容」。
  旧实现的原话是「重名时取第一个匹配（真机随机 NPC 可能重名，**概率低**）」：**"概率低"是错的** ——
  全图上千个单位、名字用字池有限，重名是常态；而它**完全静默**：不报错、不缺字段，只是把另一个人的档案交给模型。
  * **三条防线缺一不可**（对应测试 `tests/test_unit_id_contract.py`，全部做过变异验证）：
    ① **能精确就精确**：`inspect_unit(unit_id=…)`；关系簿与 `search_units` **每人都回传 unit_id**（关系簿入参本来就是 unitID，旧实现取到名字后把 ID 丢了 —— 丢掉它才是根因）；
    ② **给了 unit_id 就只认它**：查不到返回 null，**绝不回退按名**（回退 = 又猜一次）。`if (hasId)` 块每条路径必须 return，测试用花括号配平断言"最后一条语句是 `return null;`"；
    ③ **按名解析必须消歧上报**：全图扫出**所有**同名者，返回第一个的同时把候选列表交给工具层，返回体带 `ambiguous{target,matched,used,candidates[]}`，Python 渲染成**正文最前面**的 `⚠️同名提醒`（明说"未必是你要找的人"+ 列出其余同名的 unit_id）。
  * **两个 IL2CPP 陷阱**（都会让"精确查询"变成猜）：
    · `g.world.unit.GetUnit(s)` 对**查不到的串会按名兜底**返回随便一个同名者 —— 所以 unitID 路径**必须回验** `got == unitId`，不等就当作没命中（诊断串里写 `②unitID不匹配(GetUnit按名兜底返回了 '…')`）；
    · 因此**含 CJK 的输入不许交给 `GetUnit`**（`HasCjk` 判据）：它替我们选人，还绕过了同名计数 —— 中文名一律走"全图扫完 + 计数"。
  * **历史背景**：这条坑 09-12 已经踩过一次（`GetUnit("唐炎")` 按名兜底命中同名 NPC →「查玩家经历」恒空），当时的修法只是"把玩家直达提到最前面"，只堵了玩家这一个高频歧义，**其余名字仍然静默选人**。这次才算堵住。
* **★ 游戏时钟：`roundMonth` 是 0 起总月数，年必须除出来 ★**（09-13）。
  `g.world.run` 是 `WorldRunMgr`（**属性**，不是字段 —— `g.world` 是 `WorldMgr.run` 属性），反编成员只有
  `roundMonth` / `roundDay` / `roundDayResidue` / `roundDayMax`：**没有 `roundYear`，也没有直接给"年"的东西**。
  * **账面月 = `roundMonth + 1`**（1年1月=1、2年1月=13；`(账面月-1)/12+1` 年、`(账面月-1)%12+1` 月）。
    漏掉 `+1` → **整体差一个月且完全静默**。两条独立佐证：① 真机开局首月载荷 `DataUnitLog.LogItemData.month = 1`；② 参照 mod 两处写法（`roundMonth/12+1` 年 + `roundMonth%12+1` 月；`ConvertToYearsMonths(roundMonth+1)`）与上式互相印证。
  * `roundDay` 同样是 **0 起**的**月内**日（各处均以 `roundDay+1` 报日）—— 月 0 起 + 日 0 起是同一个约定位，`CurrentGameDay()` 的 `m*30+d` 近似也建立在此。`CurrentRoundDay()` 返回 1 起值，读不到返回 `-1`。
  * **换算只许有一份**：算式的唯一落点是 `UnitSnapshot.SplitAccountMonth(账面月, out 年, out 月)`；`CnYearMonth`（年月）与 `CnDate`（年月日，日 `<=0` 时**退化成"年M月"、绝不编造日**）都走它，`GameContext.NowBlock` 的结构化年月也走它。经历「近况」前缀、玩家消息时间戳、L1「当前时间」段三者共用；测试 `tests/test_game_time_stamp.py::test_year_month_conversion_has_single_source` 全仓扫描 `(x-1)/12+1` 只允许命中一次。
  * **日粒度不能省**：只报年月的话，同一个月里 1 号和 28 号**长得一模一样**，"你三天前才来过"这类话就永远说不出来。
  * **读日历必须容错**：`CurrentAccountMonth()` / `CurrentRoundDay()` 取不到返回 `-1` → `CurrentDateText()` / `CurrentTimeLabel()` 返回**空串** → 调用方原样放行（时间戳绝不阻断玩家发言）。世界未加载/切档瞬间真的会读不到。
  * **标度自检留痕**：`ChatPresenter` 在标签**变化时**打一行 `[ChatPresenter] 玩家消息时间戳 [1年1月3日]（roundMonth=0）`（**换日**才打，不刷屏）。判读办法：同一游戏月内它与经历「近况」条目前的 `(N年M月)` 必须一致；若整月差一（近况 `(1年1月)` 而这里 `[1年2月]`），就是 `+1` 那处标度错了 —— 那是本机制唯一的一处魔法常量。

### D.7 C# 侧证据链与环境速查

| 对象 | 值 |
|---|---|
| 游戏本体 | `E:\SteamLibrary\steamapps\common\鬼谷八荒`（Unity 2020.3.9，IL2CPP，版本 **v1.2.113.259**） |
| 运行时 | MelonLoader **0.5.4** + UnhollowerRuntimeLib **0.4.18** |
| 主证据日志 | `C:\Users\iu\AppData\LocalLow\guigugame\guigubahuang\Player.log`（当前运行；`Player-prev.log` 上一次）。mod 的 `P()` 走 `UnityEngine.Debug.Log` 落在 Player.log（**不是** MelonLoader 日志） |
| 后台线程日志 | `MelonLoader/Logs/AgentLoopBridge-thread.log` |
| MelonLoader 日志 | `<游戏根>\MelonLoader\Logs\`（注册成功的类型会留下 "unsupported parameter" 方法扫描警告，可用于反推注册进度） |
| mod 打点样式 | `[AgentLoopBridge] UI-2 注册完成（含 AB 两面板）` |

* ⚠️ **`Player.log` / MelonLoader 日志必须用 `grep -a`**（文件含特殊字节，普通 grep 静默返回空，已踩坑两次）。
* 崩溃读法：崩溃标志 `Crash!!!` 与 `OUTPUTTING STACK TRACE`；栈里出现 `il2cpp_class_get_static_field_data` = 互操作层访问游戏类静态字段时踩雷（try/catch 拦不住）。
* 遗留观察：Python `tool_result` 事件**不发 `isError` 字段**，C# 读 `payload["isError"]` 恒 false——只影响标红，不影响显示。

---

## E. UI 工作流（预制件 → AB → 部署 → 验证）

### E.1 两个 Unity 工程 + 两份预制件 + 双份源码（本项目最大的坑）

链路：`csharp/UI/*.cs`（视图源码）─cp─► `ui_preview/Assets/MUD_UI/` ─菜单─► `ui_preview/Assets/Resources/UI/UI*.prefab`（预览副本）─手动拷─► `ResBuildABProject/Assets/Resources/UI/UI*.prefab`（**进游戏唯一真相**）─菜单打包─► `.ab` ─► 部署目录 ─► 游戏 `g.res.Load` 装配。

| 角色 | 路径 | 真相 / 同步 |
|---|---|---|
| 预览工程 | `F:\agent_loop\ui_preview`（Unity Hub 打开时**选这一层**，别选上级） | 只预览样式；`Assets/Resources/UI/UI*.prefab` 是**预览副本** |
| 打包工程 | `E:\SteamLibrary\steamapps\common\鬼谷八荒\Mod\modFQA\资源修改教程\ResBuildABProject` | **进游戏唯一真相**；`Assets/Scripts/ABBuild/Editor/ABBuildRunner.cs` 在此 |
| 部署目录 | `E:\SteamLibrary\steamapps\common\鬼谷八荒\ModExportData\Mod_Jgmg5L\` | 游戏实际加载 `ModRes\AssetBundle\ab\ui\` 下的 `.ab` |

* **双份源码曾用 NTFS 硬链接，现已断链**——**编辑器保存是原子替换 inode，硬链必断**，统一**以 `cp` 为准**：改完 `csharp/UI` 立刻拷 `ui_preview/Assets/MUD_UI/`；**拷贝前先 diff**（漂移样本 `ChatWindow.cs` 39738 vs 20484）。
* `MUD_UI` 只收**纯 Unity 依赖**视图件；`ConfigPresenter`/`AbXxxPanel` 依赖 `WsClient`/`g.ui`，**不进预览工程**。
* **两份预制件从不自动同步**（实测 `UIChatAi.prefab` 139264 B vs 102354 B）：谁进游戏就更新打包工程那份，想预览再拷一份。

### E.2 打包：官方工程 + 自包含 bundle

现役入口 = 菜单 **「游戏工具 → 更新 → 更新AB(自包含)」**（`ABBuildRunner.RunFromMenu` → `Run()`）。三个 bundle：`ab/UI/UIChatAi.ab`（prefab + `Assets/art/panel_bg.png.png`）、`ab/UI/UIConfigAi.ab`（纯 prefab）、`ab/UI/UIContactAi.ab`（prefab + 圆点/红圆点）。

* **必须"自包含"（已踩坑）**：官方教程说「背景图放 `Assets` 非 `Resources`，防止多余 AB」——**实测是反的**：贴图被拆进独立依赖包，加载器只拉主包 → **引用断裂、面板变白块**。正解 = 预制件与它引用的贴图**同名 bundle、一次成包**（根 manifest 该条 `Dependencies: {}`）。官方「更新AB」拆依赖 → 白板，**别用**。
* **新增贴图必须登记进 ABBuildRunner**：`BuildPipeline` 只打**已分配 bundle 名**的资源（通讯录曾因 bundle 名从未赋值 → 改了也永远打不出来）。
* 路径可含中文，**产物名必须全英文**（`UIChatAi`/`UI`）。
* 批处理构建（⚠️ 含中文路径的脚本/`.cs` 须 **UTF-8 带 BOM**，否则 PowerShell 按 GBK 解析乱码）；判据 = 日志出现 `[ABBuildRunner] self-contained AB build done -> …`，产物在 `Assets\AssetBundle\ab\ui\`（**产物名全小写**）：

```
"C:\Program Files\Unity\Hub\Editor\2020.3.9f1\Editor\Unity.exe" -batchmode -quit \
  -projectPath "E:\SteamLibrary\steamapps\common\鬼谷八荒\Mod\modFQA\资源修改教程\ResBuildABProject" \
  -executeMethod ABBuildRunner.Run -logFile "F:\agent_loop\_ab_build_log3.txt"
```

* 重新生成预制件：**该路径已作废** —— `ui_preview` Unity 预览工程已于 2026-09-14 删除
  （用户拍板走 AB 资产固化，不再用编辑器预览/生成）。预制件现在只有一个维护入口：
  `scripts/dev/prefab_patch_config_groups.py`（幂等、可重跑、纯文本手术）直接改 AB 工程那份。
  ⚠ 历史坑仍在文档里留个记性：当年 `ui_preview/Assets/MUD_UI/ConfigUiBuilder.cs` 与 `csharp/` 那份
  **会不同步**，而"生成配置面板预制件"会**覆盖手改** —— 删掉预览工程也顺带消灭了这个隐患。

### E.3 部署与核对

* AB：`ResBuildABProject\Assets\AssetBundle\ab\ui\*.ab` → `…\Mod_Jgmg5L\ModRes\AssetBundle\ab\ui\`（**小写 `ab/ui`**）；**只拷 `ab/`——两侧 `assets/` 都是首次构建的依赖残留，部署前删掉**。
* dll：`csharp\bin\Debug\MOD_Jgmg5L.dll` → `…\ModCode\dll\MOD_Jgmg5L.dll`（见 D.2）。
* **核对口径 = 两端同名文件比大小/MD5**。历史教训：`uiconfigai.ab` 打包产物 197991 B vs 部署 206839 B → 配置包没随最近一次构建部署；**改过面板样式后务必重拷**。

### E.4 进游戏验证

1. **重启游戏**：`g.res.Load` 失败会写 **null 缓存**、同会话永久命中，必须重启重测（D.3）。
2. 入口：NPC 面板「AI 对话」/ F10 通讯录 / F11 配置。面板不可见时依次查宿主父链 `activeInHierarchy` → 根 `RectTransform` 尺寸/锚点 → `CanvasGroup alpha` → 同层多 Canvas 被 `sortingOrder` 压低（D.3）。日志落 `Player.log`，**必须 `grep -a`**。

### E.5 预制件编辑铁律 + Unity 中文速查

* 场景里改结构弹「无法重构预制件实例」→ **右键实例 → 预制件 → 打开预制件**（Prefab Mode）改，改完回场景保存。
* 改了但文件里没变 → 你八成在**播放模式**改的（改动一律不保存）：停止播放 → Prefab Mode 改 → **点右上角保存**。
* 根 Scale 必须 **(1,1,1)**（资产本体；改实例无效）；行/气泡模板**必须全部不激活**（构建后 `SetActive(false)`）。
* 编辑模式挂的回调/refs 进 ▶ 会被场景重新序列化清掉（`UnityEvent.AddListener` 与 C# 运行时字段都不序列化）→ 预览驱动必须在其 `Start` 里按契约**重新组装初始化**。
* **中文界面术语**：层级=Hierarchy｜场景=Scene｜游戏=Game｜项目=Project｜检查器=Inspector｜控制台=Console（红字=错误）｜播放键=▶。
  * **播放键消失**：九成是**双击了「场景」标签把它最大化**——再双击一次还原；加组件要先在**层级**选中对象。
  * 新建 UI 文本：右键菜单**默认只有 TextMeshPro**，要选「**文本（旧版）**」/「**输入字段（旧版）**」（本项目面板全用 legacy UGUI `Text`/`InputField`）；改字若两种文本都可能存在，**同时支持 `Text` + `TextMeshPro`**。
* **字体：三级兜底链（新增文本必须显式挂字体）**：`csharp/UI/ChatUiBuilder.cs` 的 `ResolveSharedFont()`（→ `ResolveCjkFont()`）是全项目唯一字体来源：①**捞游戏现成 Text 的字体**（自带 CJK，最优）→ ②`Font.CreateDynamicFontFromOSFont("SimHei", 20)` → ③`Resources.GetBuiltinResource<Font>("LegacyRuntime.ttf")`（再退 `Arial.ttf`）；只解析一次并缓存。新增文本节点必须显式 `txt.font = ChatUiBuilder.ResolveSharedFont();` 并显式赋 `fontSize`——漏挂就是方块/tofu。

**文本显示问题速查**

| 现象 | 原因与解法 |
|---|---|
| 文字跑到面板框外面 | 静态文本（标题/按钮/输入框）子 Text **锚点四角拉伸**：最小 (0,0)、最大 (1,1)，再配内边距；**气泡/行模板内文字除外**——交给布局组接管，子 Text 保持默认点锚 |
| 模板气泡高度恒 0、不随文字撑开 | 气泡根必须**同时**有「垂直布局组（边距 12/12/8/8）」+「内容大小适配器（垂直=首选大小）」，且父级 Content 布局组开**「控制子高度」**——只挂适配器时文字高度传不到气泡根。编辑模式下 inactive 模板显示 0 属正常；想手动调高先改「不受约束」，**预览完改回「首选大小」** |
| 系统行/分隔条显示不出字 | 必须**根节点自己就是 Text**（代码 `GetComponent<Text>()` 只找自身）；文本组件加到根上、删掉旧子 Text |
| emoji/特殊字符变 tofu 方框 | 游戏字体不含 emoji → **图标一律换图**（先例：🔍 搜索图标必须换成 `icon_search`）；`✕` 同理 |

* **布局结构坑（AB 预制体专有）**：视口挂 `RectMask2D` 才裁剪，滚动条竖向关横条；根 Canvas + `CanvasScaler`「随屏幕大小缩放」1920×1080，层级用 `sortingOrder` 显式压过游戏 UI。九宫格需先装包：**窗口 → 程序包管理器 → Unity 注册表 → 搜 `2D Sprite` → 安装**。

---

## G. 功能设计底稿要点

### G.1 原生剧情窗「AI 对话」按钮（`csharp/UI/DramaAiOption.cs`，已实装）

| 项 | 结论 |
|---|---|
| 挂点与捕获 | Harmony **Postfix `UIDramaBase.InitData(int, DramaData)`**——覆盖全部剧情窗变体；剧情推进多页会反复 `InitData`，故**先销毁同名旧钮再注入**（幂等）。`DramaData.unitLeft`/`unitRight`/`unit` 里**非玩家**者即说话 NPC（**无 NPC 的系统公告类不注入**）。原文 = **`DramaTextCapture.LastUiText`**（`UIDramaBase.GetDialogueText` 的返回值 = 屏幕上的成品句，零推断）；兜底才用 `DramaTool.lastOpenDramaDialogueText`（**已证伪的模板路**——`dialogRoleChat1` 的 1050 条变体里 649 条带 `{call\|B\|A}` 之类占位符）；都取不到用空串——**不阻断**。每个剧情页打一行 `注入诊断`（模板 / 宿主 / 克隆 active / 世界坐标 / 屏坐标 / 屏内 / 尺寸），"看不到按钮"时它就是判据 |
| 注入范式 | **① typed 属性直取（09-13 第五轮，首选）→ ② 按名深搜 → ③ 第一个「可用」Button**。<br>**① typed**：游戏把这几枚按钮作为 **typed 属性**暴露在剧情窗基类上（互操作程序集实证）——`UIDramaDialogueBase` 的 `[p] Button btnRightLook / btnLeftLook / btnNext / btnSkip`、`[p] Text textRightName / textLeftName`；大图窗是**另一个平级家族** `UIDramaBigTexture : UIDramaBigTextureBase : UIDramaBase`（**没有** `btnRightLook`/`textRightName`，只有 typed `btnNext`/`textNextTip`）。**用例** `TypedTemplate`/`TypedNameRect`：`ui.TryCast<UIDramaDialogueBase>()` / `TryCast<UIDramaBigTextureBase>()` → 直读引用。**为什么用 `TryCast` 而不是 `as`**：IL2CPP 下 `as` 对返回的包装体**静默给 null**（`UiRects.Of` 已踩过）。**收益 = 消灭一整类脆弱**：不必容忍 `G:` 前缀、预制件改名也不受影响。**全程只读**，任何一步落空只返回 null → 退回 ②，不改变行为。<br>**② 按名深搜**：同名四个候选，**容忍 `G:` 前缀**（`NameIs`：真名 `G:btnRightLook` 与字段名都认）。**③ 兜底**「第一个**可用**的 Button」（`FindBestTemplate`）。<br>**`IsUsableTemplate` 四条**（typed 引用也要过）：`activeInHierarchy`（**不是** `activeSelf`，父链可能整条关着）+ `RectTransform` 有尺寸 + **有可改的 `Text`/TMP** + 有 `Image` 时须 enabled 且 `alpha>0.05`。<br>**为什么必须 typed 优先**：09-13 事故 = 名字路四个候选全 miss → 退兜底 → 捡到 `G:btnNext`（翻页指示图标，**无文本组件**）→ 日志报"已注入"、玩家屏幕上**什么都没有**。<br>→ **`clone.SetActive(true)`** → **先定尺寸再定位**（宽度决定右缘；**先定位后加宽**会让矩形以 pivot 为中心向两侧长回去、压住左邻 —— 09-13 真机「AI 对话」同时压住「查看」和 NPC 名签就是这个成因）→ **落点 = NPC 名签的正左侧**（名签同样 typed 优先：`textRightName`/`textLeftName`，按侧别取、落空退另一侧、再落空才按名深搜）→ 再退「模板左侧」→ 再退剧情根 + 屏幕右下角 → `onClick = new ButtonClickedEvent()` **整体换新** + `NpcPanelButton.StripOperationItemPublic` 清组机制组件 → 文案「AI 对话」（**`Text` + `TextMeshProUGUI` 双支持**）→ `ClickUtils.Attach` 三步写法；按钮名 `AgentLoopAiOptionButton`；该 NPC 回合进行中点击 → 静默丢弃。**禁用 `GetComponentInChildren<Button>(true)`**（同 D.3 铁律）。<br>**诊断**：每页 `注入诊断` 首字段即 **`模板来源=`**（`typed btnRightLook` / `typed(BigTexture) btnNext` / `按名深搜 X` / `兜底(首个可用)`），落点里带**名签来源**（`typed textLeftName` / `按名深搜 G:…` / `名签两路全 miss`）——**一眼看出走的哪条路**，不必再靠猜。typed 与按名都落空时额外打一行 `剧情窗 Button 清单`（全窗 Button 的真名 + 自活/链活 + 有字/无字 + alpha + 尺寸，上限 40 条） |
| 落点：NPC 名签左侧（09-13 用户指定） | `FindNpcNameRect`：**侧别判据 = 名签文本**（09-13 第七轮改）——哪一侧名签写着 NPC 的名字，NPC 就在哪一侧；名签必须「链活 + 有字 + 精确等于 NPC 名」才采信，判不出才退回**已证伪**的指针比对（并在 `落点=` 里标明 `侧别退指针兜底(已证伪，恒右)`，免得哪天又被当成可信判据）。**⚠ 旧判据（`DramaData.unitLeft/unitRight` 与 npc 比 native 指针）已废**：真机证这两个属性**返回同一个单位**，于是 `r.Pointer == npc.Pointer` **恒为 true**、`onRight` 恒真——真机这些剧情 NPC 恰好在右所以**结果一直碰巧对**，NPC 一旦出现在左侧就会锚错边。取矩形优先 typed `textRightName`/`textLeftName`（`Graphic.rectTransform`），落空退按名深搜（`NameIs` 容忍 `G:` 前缀），该侧没有退另一侧。`PlaceLeftOf`：**克隆体右缘 = 锚点左缘 − 8px**，**用世界坐标算**（`右缘 = position.x + (1−pivot.x)·rect.width·lossyScale.x`）——名签与克隆体常常不在同一父节点、锚点/轴心也未必相同，拿两个 `anchoredPosition` 互相加减会错位；只改 **x**、保留模板的 **y**（名签与「查看」同一行）。诊断行 `落点=` 现在同时给出**矩形来源与侧别来源**，形如 `名签左(G:textRightName w=200；typed textRightName；名签右=司空雨珍)`；其它取值：`模板左(无名签)` / `模板左(名签定位失败)` / `旧兜底(右下角)` |
| 模板两条真机教训（**都是同一个坑**） | **① 名字必须容忍 `G:` 前缀**：本作 UI 节点**真名带前缀**，字段名不带 —— 09-07 在 HUD 圆钮上踩过一次（`G:btnPlayer`/`G:btnEmail`，prefer 列表全 miss），09-13 在剧情窗**又踩第二次**：四个候选全 miss → 退兜底 → 捡到 `G:btnNext`（翻页指示图标，**无文本组件**）→ 日志报"AI 对话按钮已注入"、`克隆active=True/True 屏内=是`，**玩家屏幕上什么都没有**。**② 模板必须有可改文本**：否则克隆出"没字的空热区"，`SetLabel` 只会打一行 `警告：克隆体无 Text/TMP 组件` 就过去。教训：**"已注入"日志 ≠ 玩家看得见**——判据要看 `注入诊断` 那行的**模板真名 + 克隆 active + 屏坐标**三件套 |
| 点击后 | `ChatLauncher.OpenForUnit(npc)` 开面板 + `WsClient.SendGameDrama(npcId, text, dramaId, speaker)` → `npc_initiative {intent:"game_drama", text:原文, drama_id, speaker}` → Python `handle_initiative` **跳过全局节流**（玩家主动点击）→ `format_game_drama_message(text, speaker)` 包装舞台指令 → 正常 turn → `npc_reply` 流进已开面板 |
| **引文归属：屏幕句是谁说的（09-13 第七轮定案，修"模型猜反"）** | **症状**：NPC 主动赠送那一页（屏幕句 `我这里有一个青须藤*48准备赠于你，你需要此物吗？`，选项「我收下了/我不需要这个」）点「AI 对话」，NPC 却开口道谢——把这句当成了**玩家说的**（真机日志 `AI 对话已发起：惠都 dramaId=21701 text=我这里有一个@w_…@准备赠于你…`）。**根因**：屏幕成品句**本身不含说话人**，而早先的舞台指令刻意中性（不点名），等于把归属权丢给模型猜；同一句两头都可能说，猜错就是喂一个**反的事实**。<br>**★ 判据 = 配置列给"哪一侧在说" + 名签文本给"那一侧是谁" ★**（两步拼起来，缺一不可）。<br>**① 侧别来自配置列** `ConfDramaDialogueItem.speaker`：语义出自游戏自带表头 `Mod/modFQA/配置修改教程/配置表头/DramaDialogue.xlsx` 第 7 列「说话者」，紧邻注释 **`1-左` / `2-右`**；全量 21926 行佐证（分布 `2:13825 / 0:4589 / 1:3476 / -1:34 / 12:2`；**115 行 `npcLeft=0 且 npcRight≠0 → speaker=2`**；2602 行两侧无人 → `speaker=0`；`uiType=2` 奇遇界面多为 `-1`）。`-1`/`12` = 无说话者/脏数据 → 走兜底。真机与立绘亮侧**同答 6/6**（speaker=1↔左亮 ×2；speaker=2↔右亮 ×4）。<br>**② 侧别 → 人** 用**名签文本**：`UIDramaDialogueBase` 的 typed `Text` `textLeftName`/`textRightName`——哪一侧名签写着玩家名，那一侧就是玩家。**真机实证（09-13 第七轮）**：`名签 左="唐炎"[活] 右="司空雨珍"[活] 玩家名="唐炎" NPC名="司空雨珍"` —— 名签有字、链活、与玩家名/NPC名**精确相等**。**这一步同时把"左=玩家"从假设变成实测事实**（此前只有三条间接依据：用户判断 / `ShowDramaService` 构造 `unitLeft = playerUnit` / 唐炎恒在 `x=-3`）。<br>**③ 采信门槛（硬）**：名签必须「**整条父链活着**（`activeInHierarchy`，不是 `activeSelf`）+ 有字 + 精确等于玩家名或 NPC 名」三者齐备才用；**任何一步含糊 → 立刻退立绘兜底**，绝不含糊时硬给答案。名签路与立绘**无条件交叉校验**，不同答就打 `⚠说话人判据冲突`。<br>**④ 取值时机 = 与文本同源同行**：`DramaTextHook` 本就 patch 了 `UIDramaBase.GetDialogueText(ConfDramaDialogueItem item, DramaData dramaData)`——那个 `item` **就是本页那一行配置**，顺带读 `item.speaker` 即可，"谁在说"与"说了什么"同一行同一次调用，不可能串页。`DramaTextCapture.NoteUiText` 里侧别与句子**同进同出**（句子非空才更新侧别）。<br>**⚠ 四条已被真机否掉的路（勿回退）**：<br>**(a)** `imgBgPlayerDark`/`imgBgOtherDark` 具名压暗遮罩——按反编 `#Strings` 堆相邻性推断，真机探针打出 `0处 0处` + `暗色候选=(无)`。教训：**堆里相邻 ≠ 同类**。<br>**(b)** `UIDramaDialogue.Play(WorldUnitBase, Action)` 的 `unit` 参数——方法确实存在、钩子也确实挂上了（`[剧情说话人] 钩子已挂：UIDramaDialogue.Play（2 参数）`），但**从头到尾没被调用过**（全程无 `Play 首次命中`）。教训：**"方法存在" ≠ "方法被调用"**，与 (a) 同类。<br>**(c) ★ 用 `DramaData.unitLeft`/`unitRight` 做"侧别→人"—— 09-13 第六轮真机证伪，代价最大的一条 ★**：`speaker=1 → data.unitLeft`、`speaker=2 → data.unitRight` 再问 `IsPlayerUnit`。真机打脸：**这两个属性返回的是同一个单位**（`｜左=许其[NPC] 右=许其[NPC]`、`｜左=蒋博明 右=蒋博明`、`｜左=魏盼香 右=魏盼香`，三例一致），于是 `speaker=1` 与 `=2` 必然给出**同一答案**——`speaker=2` 页碰巧对（NPC 恰在右且在说），**`speaker=1` 页稳定错**（玩家在说却输出 `npc`）。是第六轮加的交叉校验当场抓出（`⚠配置级=npc 立绘=player（配置 speaker=1）`）。**教训（比"要查数据源"更狠一层）：用错的数据源比启发式更危险** —— 启发式判不出会闭嘴（返回 `""` → 中性文案），错的数据源会**自信地**给出反事实，日志还长得像"配置级，零推断"那样可信。<br>**(d)** 立绘亮度——**降级为兜底 + 交叉校验**（每剧情页两次，不是每帧）。它**只独立测出"哪一侧亮"**（这一项真机 6/6）；"亮的是谁"另算：**亮侧(实测) + 玩家在哪一侧(名签实测) → 相同则玩家在说**。**⚠ 09-13 第七轮之前这里是 `return leftIsSpeaker ? "player" : "npc"`，把"左=玩家"焊死在代码里**——于是"侧别→人"这一步**从来没被测过**，日志却长得跟实测一样；现已在 `diag` 里**明说**依据是实测还是假设（`名签实测玩家侧=左` / `名签判不出玩家侧→按「左=玩家」假设`，后者只在非对话窗家族没有名签时出现）。（保留细节：立绘节点名 = 角色名而非 `rimgLeft`/`rimgRight`，后两者是父链未激活的空占位 `亮=0.000`；左右按世界坐标 `x` 符号分，玩家 `x=-3`、NPC `x=+3`；黑滤镜真名 `G:imgBlackFilter`。）<br>**探针与自校验**：每页一行 `说话人判定`，内含 **① 两路结论 ② 两侧单位（坏源，留作对照）③ 两侧名签文本+链活 ④ 玩家名/NPC名 ⑤ 交叉校验结果**。成功形如 `配置侧+名签定玩家侧(左) → player（配置 speaker=1）｜左=司空雨珍[NPC] 右=司空雨珍[NPC]｜名签 左="唐炎"[活] 右="司空雨珍"[活] 玩家名="唐炎" NPC名="司空雨珍"｜交叉校验 立绘=player`。**这一行同时是判据、校准台与回归证据**。<br>**另**：`注入诊断` 的 `屏坐标=` 曾长期不可信——它用 `ui.canvas.renderMode` 判相机，踩了**嵌套 Canvas 的序列化残留**（与 `HoverTip` 那个"气泡一个都不出"的真机事故同一坑）。已改为走 `rootCanvas`（`DiagCamera`），并加 `相机=` 字段让坐标自证。**只影响日志，不影响摆放**（`PlaceLeftOf` 走世界坐标，不经相机） |
| **@ 标记必须解成人话（09-13 收口，与上条同一条消息的另一半）** | `UIDramaBase.GetDialogueText` 返回的是**带标记的编码文本**，真机原样：`我这里有一个@w_rIsZH3\|1\|5031101\|48\|@准备赠于你`、`（看起来@q_郦安\|Lepxop@想和我说些什么。）`。直接发给模型，它读到的是 hash 乱码——**不知道送的是青须藤、也认不出是谁**。修法 = `DramaAiOption.ExtractText()` 的返回值过一遍 **`UnitSnapshot.CleanLogText`**（世界日志那条路已有的解码器，当初**就是拿这句剧情原文当样本写的**，只是从未挂到剧情这条路；本次把它的可见性由 `private` 改为 `internal`）。规则：正则 `@([a-zA-Z])_([^@]*)@` → 逐字段试解（纯数字字段查 `ItemProps` 表、过 `GameTool.LS` 取中文名；字段0 当 unitID 查单位名；都不行退回字段0 并留一行 `@标记未解`）。**判据不写死下标**靠的是 `ItemProps` id 从 10001 起，`1`/`48` 这类序号字段查不到行、不会误命中 |
| 舞台指令语义（**归引用、不归主动**，09-13 修） | `speaker="npc"` → `（游戏内交互：你刚对玩家说了这样一句——「{原文}」）…`；`speaker="player"` → `（游戏内交互：玩家刚对你说了这样一句——「{原文}」）…`；**未知 → 旧中性文案逐字不变** `（游戏内交互：你和玩家之间刚经过了这样一幕——「{原文}」）…`（三种共用收尾 `（请以你的人设与当下心境自然地接住这个话头，不要复读括号内容。）`）。**只陈述"这句出自谁口"，绝不说谁主动**——所以玩家点「闲聊」（NPC 回的那句问候归 NPC）与 NPC 主动找上门，两种场景都不会喂出反的事实。UI 分隔条标签 `剧情应对` → `游戏交互` |
| 设计定位（关键决策） | **旁路选项版 = 加法不是接管**：不 `return false` 抑制原 UI、不碰 `onDramaEndCall`/`onOptionsClickCall` 结算回调 → 原生选项**机制完整保留**；LLM 只做**表演层**开口，不替玩家做选择 |

**工具唤出的原生剧情窗必须排除 —— 两道闸（09-13 重构）**：`world_ai_action` 五 op（1031 双修 / 1034 疗伤 / 1037 论道 / 1041 提升心情 / 1044 邀约）与 marry 婚礼 22201 走**原生** `DramaTool.OpenDrama` → `UIDramaDialogue` 家族 → 同样继承 `UIDramaBase`，会被注入——危害是**语义错位**，不是崩溃。

* **闸 1 · 同步窗**：`ToolExecutor.Execute` 的 `try/finally` 置 `DramaGate.SuppressAiOption`。原理 = **单线程 + 同步调用**：`Execute` 跑在主线程（`MainThreadDispatcher` ← `g.timer.Frame`，且一次只取一个任务），而游戏的 `OpenUI → InitData` 在同一调用栈内完成 ⇒ 执行期内弹的窗**必然**是这次工具调用的下游。`finally` 是正确性要求（不清标志会让此后所有窗都不注入，且零报错）。
* **闸 2 · 异步窗（闸 1.5，`DramaNativeClaim.cs`）**：`Execute` **返回之后**才弹的窗闸 1 够不着 —— 实证：①邀约 81002 随 `CreateAction` 同步弹（闸 1 挡得住），玩家点「接受」后游戏的 lambda 才弹 **81012**；②求婚 `ShowConfirm` 的 `onOkCb` 是「`onOk()` 之后**紧接着** `DramaGate.Resolve`」，而 22201 就在 `onOk` **里面**开 —— **22201 还开着动作就算结束了**。
  **凭证认人不认 ID**：工具发起动作时 `ClaimFor(目标 NPC unitID)`，窗开时 `TryClaim(unitID, ui)` 命中即跳过注入。凭证活到 **`UnitActionPending.Complete/Remove`（原生 `OnEnd`：邀约/传功/讨要/赠予原生路）**、**`DramaGate` 槽位 `Resolve`（点确定/拒绝、或 120s 超时：marry/结拜/解除关系/赠予自制窗）**、或 `ClaimTTL=120s` 兜底。
  **`Resolve` 那条腿怎么接的**：`ShowConfirm` 类工具不走 `UnitActionPending`，只能靠槽位。接法是**订阅 `DramaGate` 已有的公开事件 `OnResolved`**（`DramaGate.cs:42`）——**`DramaGate` 零改动**。`Claimed()` 拿到 pending 标记后读 `res["__slot__"]` 调 `BindSlot`。时序正好：`onOkCb` 里 `onOk()`（marry 在其中**同步** `OpenDrama(22201)`）**先**跑完并把会话建好，`DramaGate.Resolve` **后**跑 —— 释放凭证时 22201 已被会话接管，翻页照样挡得住。
  **`ReleaseUnlessPending` 的例外**：同一 NPC 的第二个工具调用撞窗失败时也会走到它。此时表里那张券属于**上一个真挂起中**的动作，替它收回就等于把它的后续窗（如 22201）放出来。判据 = 券绑了**未 Resolve** 的槽位（`Slot != 0`）→ 不收回。
  **为什么不是"命中即消费"**（用户 09-13 拍板）：一个动作可能牵出**多重**剧情，消费掉第二重就漏了。**多翻页**另靠一条**会话指针**（`ui.Pointer` native 指针，**不是**托管引用 —— Unhollower 不保证同一 native 对象每次给出同一代理）+ `SessionTTL` 兜底：同实例的后续翻页直接算我方，**与凭证生死无关**（否则 marry 的 22201 第二页必漏）。
  **为什么不用 `ReferenceEquals`**：见 D.1 —— 代理实例不保证同一。
  **旧实现（已删）**：按 dramaId 黑名单 `{1031,1034,1037,1041,1044,22201}` —— 分不清"谁发起的"，**游戏本体自己**发起的同名剧情（NPC 用游戏 AI 找你双修、过月事件触发论道）也被一起误挡。
  **已知边界**（09-13 补 `Resolve` 吊销后收窄）：4 个 `ShowConfirm` 类工具（marry/结拜/解除关系/赠予自制窗）原先**没有任何代码吊销凭证**，只能干等 `ClaimTTL=120s` 自然过期 —— 表现为"用完工具后 120s 内自己找同一 NPC 开窗，那一窗没按钮"（用户实测连招：送完东西马上接着聊）。现已由 `OnResolved` 订阅修掉，**marry 的该边界归零**。残留仅两条，都极小：①窗实例 native 指针在 `SessionTTL=120s` 内被 Unity 复用给同 NPC 的新窗 → 误认领（理论风险，靠 TTL 自限）；②`ClaimTTL` 兜底路径（窗开了玩家一直不点、槽位始终不 Resolve）仍会留券到 120s。

### G.2 NPC 主动传音三分流 + 未读体系（已实装）

**判定时机 = 回合收尾**（C# 收到 `npc_reply(initiative=true)`），**不在触发时**；但**"能不能当面弹出"以「触发那一刻」的判定为准**（用户拍板）——否则异地传音的 NPC 在你等回复的几十秒里走进同一格，就变成"我没点同意，怎么就当面开窗了"。判定信息（`UnitLookup` + `pointX/Y`）C# 本地就有，**Python 侧零协议改动**。

**三分流**（`ContactDuty.OnUiEvent`，主线程）——每次 `npc_reply` 都打一行决策现场：
`[ContactDuty] 主动开口分流：①/②/③ ｜ npc=… 触发时可当面=… 现在同格=… 该npc窗=… 别的窗=… 忙=…（原因）正文=…`

| 分流 | 条件 | 行为 |
|---|---|---|
| ① 直播已读 | 对话窗正开着且就是该 NPC（`UnreadStore.IsChatOpenFor`） | 不登记未读 |
| ② 当面开口 | **触发时可当面**（`CanPopAtTrigger`：该 NPC 最近一次触发时 `sameGrid && !busy`）&& 回复时仍同格 && 未开任何对话窗 && 玩家不忙 | `ChatLauncher.OpenForUnit(unit)` 弹 UI |
| ③ 未读 | 其余（异地/忙/已开着别的 NPC 的窗） | `UnreadStore.Mark(npc, text)` + 红点 + HUD 级横幅 |

* `error=true` 的失败兜底帧**不进未读**；同格但战斗中/模态窗中或已开着别人的窗 → 降级未读（不抢窗口）。
* **动作完成 → 自动开窗**（`csharp/ActionWatcher.cs`）：**动作工具真的执行成功那一刻**（挂起信息存 `_deferredAction`，由 `OnDramaResolved` 补发）四条全中 → `ChatLauncher.OpenForUnit`：① 这是**自主开口**回合（`NoteInitiativeTurn` 标记；玩家自己发起的回合窗本来就在，`SendPlayerMessage` 撤销标记；TTL 900s）② **本回合没有因"当面确认窗 + 点同意"开过窗** ③ 此刻没有任何会打断玩家的界面（与"能弹确认窗"**同一把尺子** `BusyReasonText()`）④ 该工具属**动作类**（只读三件套不算）。
  **用户第三版拍板（现行）**：同格互动无论对话还是动作都**立即弹确认窗**，点确认后立即弹对话 UI，**就不再需要"根据工具结果立即打开对话 UI"**；异地动作**保留**该机制（且**仍不看远近**）。所以判据是**"这一回合有没有因为确认而已经开过窗"**，不是"同格/异地"四个字。**同格+忙是空集**（忙 → Python `BUSY_NOTE` + 执行层忙碌闸拦下 5 个动作工具，根本不会发 `call_tool` 帧过来）。
  **为什么需要**：真机 00:21 云含 `intent=recent` —— 触发时异地 → 不弹确认窗；但动作闸对"异地+空闲"放行，模型自己推理出"既然异地，可以用 `world_ai_action` 远程"真的执行了 23.6s，而玩家屏幕上什么都没发生。协议：Python `call_tool` 帧新增 `npc_id`（跨语言契约测试 `test_ws_bridge_call_tool_carries_npc_id`）。**异地是否允许动作维持现状（允许，可远程）**——本机制只补"看得见"，不改征求同意。
* **未读本体与红点**：消息本体**在该 NPC 的 session 账本**，打开对话 → `get_history` 回放可见；`UnreadStore` 只是"没看过"标记（`Mark/Clear/Has/Peek` + `NotifyChatOpen/NotifyChatClosed` + `ActiveChatNpc`/`AnyChatOpen`/`TotalCount`/`Latest`），**不持久化**（重启红点消失、历史仍可回放）。
* **HUD 未读角标**（`MapMainContactButton.BuildBadge()`）：**抗锯齿真圆 + 暖白描边**（`UnreadStore.UnreadRing` / `UnreadRed` = #FF3B30，与通讯录行红点/横幅**同源同色**），圆心落在**可见圆钮 45° 环线上**（半个角标压住按钮），直径 = 可见圆钮直径 × 0.30（钳 11~20px）；可见直径按"子孙 ≤2 层里带 sprite 的最大层"实算。动效 = 1.2s 呼吸（`localScale` 1.00↔1.12 绕 pivot + alpha 0.88↔1.00），走 `g.timer.Frame(..., repeat:true)`，**不新增 MonoBehaviour**。显隐由帧回调**轮询** `UnreadStore.Any`（旧写法只在"注入时/登记未读时"写 → 「读完不灭」）。**★ 取宿主必须 `GetComponent<RectTransform>()`**：`transform as RectTransform` 在 IL2CPP 下静默返回 null（见 D.1），角标会永不创建且日志一行不打；该路径上**已取消所有静默 return**（统一 `LogOnce` 去重）。选型示意见 [`mockups/hud-unread-dot-options.png`](mockups/hud-unread-dot-options.png)（可复现：`scripts/dev/mock_hud_dot.py`）。
* **未读横幅**（`csharp/UI/UnreadBanner.cs`）：HUD 级**独立常驻 Canvas**（挂 `g.root`，非 Canvas 根 → 免嵌套画布被父级 renderMode/scale 吃掉），`sortingOrder=2400`（高于 HUD 20、低于三个面板）、顶部居中 360×46、`TopOffset=-56`、8s 自动隐藏。**纯提示不可点**（用户拍板）：不建 Button、**不挂 GraphicRaycaster** → 完全不参与输入拾取。文案 `「姜萌」传音：唐郎，可还记得我？…`（单行截断 28 字、多条标「共 N 条」、去非 BMP 字符）。懒建于首次未读；宿主随场景销毁后下次 Show 自动重建。
* **候选五源**（`RelationNetwork.CollectKnownUnits`）：`friendUnits`（好友簿）· **`IntimUnitData.enemyUnits`（仇人簿）** · **`GetAllGoodRelationUnitID(true, true)`（关系记录簿兜底，含敌）** · 手动通讯录镜像名 → `UnitLookup.Resolve` · **同格**：一次 O(N) `GetUnits(true)` 按 `pointX/pointY` 相等筛选（陌生人当面可搭话）。按 `unitID` 去重、排除玩家与空名；**非通讯录成员仅同格可发起**。
* **玩家自身必须全程剔除**（曾"自己给自己传音"）：`TryAdd` 的守卫 `ReferenceEquals(u, player)` 在 IL2CPP 下**静默失效**（同一原生对象经不同调用点取到的托管包装器不保证引用同一）。玩家判据统一到 **`UnitSnapshot.IsPlayerUnit`**：① 引用相等 ② **unitID 相等 = 主判据**（世界唯一、与包装器无关）③ 真名兜底（**仅当两侧 unitID 都取不到时用**——本作**存在与玩家同名的 NPC**，名字不能当主判据）。落点：`RelationNetwork.TryAdd` / `NpcInitiativeMonitor.BuildCandidates` 末端统一剔除 + `FireOne` 末道断言 / `DramaAiOption.FindNpcOf`（同一根因第二现场：主角自己的剧情窗被当成 NPC 窗）。

### G.3 同格当面先弹同意窗（已实装）

同格 NPC 主动互动改为**先弹确认窗**（立绘照常 + "XX 希望向你发出互动" + 同意/不同意）；异地（传音）不弹窗、直接发：

```
触发（概率/冷却/状态闸照旧）→ 冷却先落（_cooldownReal/_cooldownDay 紧跟触发）→ 分支：
  异地 → SendInitiative(npc, intent, reason)
  同格 → ShowConfirmSimple(+170, left=玩家, right=NPC, "XX 希望向你发出互动", "同意", "不同意")
          同意 → SendInitiative(..., consented: true) → Python 回合 → npc_reply → 三分流 → 当面弹对话 UI
                 同时置 _pendingOpenUnit/_pendingOpenFrames=3：**3 帧后就把对话 UI 打开**（吃流式）
          不同意 → 仅日志（declined by player），冷却已前置即完成
```

* **同格 = 窗口先开、话后到**：点同意那一瞬就发事件让回合尽早开跑，3 帧后 `ChatLauncher.OpenForUnit` 显形 + `SetThinking(true)`「对方正在斟酌…」→ 首 token 落地时直接吃流式。**同格不再依赖"工具结果"才开窗**。
* **★ 开窗重试（同格实感延迟的真凶）★**：旧版 `TickPendingOpen` 是"3 帧后**只试一次**"，失败就退化成"回复到达时才弹窗"。失败原因恰恰是那把 09-10 铁律闸：`AbPanelProber.CanCreateNow()` 里 `DramaUiName = "UICustomDramaDyn"` **就是我们自己这个确认窗的名字**，判据是 `g.ui.GetUI(...) != null`（**存在**，不看可见）→ 本局第一次同格互动的"确认窗刚关、实例仍在"必然撞上。现改为**3 帧后每帧重试、成功即止、5s 超时仍回落旧路径**（成功日志带"第 N 次尝试成功"）；`AbChatPanel` 那条"当前不宜创建"日志按 1s 节流并打出 `确认窗 UICustomDramaDyn 实例仍存在=…`。**零回归**：3 帧时闸门本就通过的话第 1 次就成功，行为与旧版逐字一致。
* **冷却写点在触发瞬间**（前移到分支之前）：婉拒也算一次自主交互（冷却期现实 600s / 游戏 3 日）。
* `SendInitiative(..., consented=true)` 带 `consented` 字段：Python `handle_initiative` 据此**豁免 300s 全局防轰炸节流**——否则玩家点了同意 NPC 也永不开口。**先同意再生成**，避免"拒绝了但话已说出口"的账本残留。
* 用 `ShowConfirmSimple` 而非 `ShowConfirm`：轻量变体，**无 `DramaGate` 槽位**、回调直接执行；同款立绘窗 `UICustomDramaDyn`（`unitLeft` 空会 NRE → 兜底玩家）；弹窗失败按婉拒处理（走 `onNo`）。
* 实现约束：该类被 verify 工程链接编译 → **不得引用 `ModMain`**（`CS0103`）；`lambda → Il2CppSystem.Action` 必须**三步写法**；自制确认窗继承 `UICustomDramaBase`（非 `UIDramaBase` 子类）→ `DramaAiOption` Postfix **不会**在其上触发；`IsBlockedByGameState` 已检查 `UICustomDramaDyn`。
* **左侧立绘必须是玩家**：五个确认窗里只有同格这一个曾传反。方向依据：① 游戏自身代码即 `unitLeft = g.world.playerUnit`（邀请/婚礼剧情）；② 用户拍板"玩家在左、NPC 在右"。`ShowDramaService.ShowConfirmSimple` 注释已把 **left=屏幕左位=玩家 / right=屏幕右位=对方 NPC** 钉死。
* 已知边界（v1 接受）：确认窗**无编程关闭 API**——玩家晾着不点 = 窗常驻 + 自主互动被状态闸暂停；同意到 `npc_reply` 间的竞争（跑开/进战斗）→ 落未读红点兜底。

**窗口 ID 段**：自主互动当面确认窗取 `ModIds.DramaInitiativeBase = Mod + 170`（+0 窗 / +1 同意 / +2 婉拒 / +3~9 预留）；`ModIds.cs` 分段总表另有 +100 `economy_item`（**`trade` 买卖也复用这一段**） / +110 `item_acquire` / +120 `social_relation` / +130 `movement` / +140 `world_ai_action`（`+140+OffReserve` = `AiActionGate` 完成挂起忙键，不开窗）/ +150 查询信息展示窗 / +160 对话 AI 追问多选窗 / +180~+989 预留。**新增窗必须登记，ID = MID + 偏移**。

### G.4 travel / places / 分类背包（已实装）

**`movement` 新增 `travel` op**（支持"先约、再赴约"）：原 `teleport` 只能把 NPC 传到**玩家坐标**，而 `UnitActionMoveNPC` 构造函数实际接受**任意 `Vector2Int`**。

* 入参 `destination`（城镇/宗门名）+ 可选 `region`（同名分宗消歧）；`ResolveBuild(dest, region)` 在 `CollectNamedBuilds()` 上**精确匹配 > 包含匹配**，region 命中者优先；`wub.CreateAction(new UnitActionMoveNPC(new Vector2Int(b.X, b.Y)), true)`，已在当地返回 `already_there: true`。传送是否受限由游戏侧 `IsCreate` 裁决——工具返回 success 但动作可能被拒绝，真机观察。
* 返回字段（**Python 渲染依赖的契约**）：`op/target/destination/cat/region/point{x,y}/moved`（或 `already_there`）；文案在 Python `tools/text_render.py`：`"{target}已动身前往{where}，并在此地等候。"` / `"此刻就在{where}"`。
* 数据源：`MapBuildTown`/`MapBuildSchool` 继承 `MapBuildBase`——`name` + `GetOrigiPoint()` + `gridData.areaBaseID`；**农场/兽栏等玩法建筑不进地点表**。枚举必须用**泛型重载**（见 D.6）。

**`query_world` 新增 `topic=places`**：返回 `{places:[{name, cat, region, point{x,y}}], total}`（`cat` = 城镇/宗门），定位 = travel 目标目录 + 邀约碰面地点查询；`region` 非空按地区过滤。配套 `topic=region` 返回当前世界实际存在的州名（命名建物收集去重，无建物退回静态州表）。Python 渲染在 `tools/text_render.py::_qw_places`；Stub 侧 `bridge.py` 的 `_DEFAULT_PLACES` 与 travel 共用同一份口径。

**背包按价值筛选（分类版）**：`inventory.props` 按 `PropsType` 分组 `[{cat, items:[{name,count,worth,total}], misc}]`（`0Money/1Pill/2Martial/3Equip/4Material/5Other` → 丹符/书籍/装备/材料/其他），类内按**单价 `worth` 降序**——`ConfItemPropsItem.worth` = **悬停出售价**。**总种类 ≤12 全显**，大背包每类 top-`inventory_top`（默认 3）+ 杂项聚合 `misc{kinds,pieces,worth}`；`inventory_all=true` 为全量逃生口；`type=0`（灵石）**不进 `props`**（走 `money`）；`equips` 逐件带 `worth`；**执行层（赠送/偷窃）按名扫全量，不受筛选影响**。

### G.5 自主交互系统怎么测

**先记清触发链的 10 道闸**（"没触发"必是其中之一，按序排查）：

| # | 闸 | 位置 | 键 / 条件 | 测试放行值 |
|---|---|---|---|---|
| 1 | 日节拍：每游戏日一次（`WorldAddDay` + `Frame` 兜底）+ `_lastDay` 防同日重复 | C# | — | 推进游戏日 |
| 2 | 状态闸：战斗 / 死亡 / 确认窗（`DramaGate.HasPending`、`UICustomDramaDyn`、`UIBattleInfo`） | C# | — | 非战斗、无模态窗 |
| 3 | 全局日骰 | C# | `initiative.daily_chance` | **100**（`<100` 才掷骰） |
| 4 | 候选集与门槛：关系网全集 + 通讯录 + 同格；**非通讯录成员必须同格** | C# | — | 存档关系网里得有人 |
| 5 | 单人双冷却 `max(现实秒, 游戏日)` | C# | `npc_cooldown_real_s` / `npc_cooldown_days` | **0 / 0** |
| 6 | 低好感减半 | C# | `low_intim_halve` + `low_intim_threshold` | **false** |
| 7 | 一日**只触发一人**（成功即 `return`） | C# | — | 要更多只能多推日 |
| 8 | 同格先弹「同意/不同意」确认窗（婉拒也算一次，冷却已前置） | C# | — | 站远 or 点同意 |
| 9 | Python 全局熔断 `min_interval`（默认 300s） | Python | `initiative.min_interval` | 20（当节流阀用） |
| 10 | Python 忙守卫：该 NPC 有回合在跑 → 丢弃 | Python | — | — |

**路径 A：诊断强制开关（推荐，改判定逻辑也不用重启）**
`F:\agent_loop\_diag_initiative_force.txt` 存在即进入强制模式（`DiagSwitches` 每 120 帧复查 → 增删文件即时生效）。它**只放宽闸 1/3/5/7 的"时间"部分**，闸 2/4/6/8 与 Python 侧闸 9/10 照旧——**测的仍是真系统**。

```
interval=20      # 触发节拍（现实秒，默认 20，最小 1）
name=林婉清      # 只对该 NPC 触发（默认：候选集里好感最高者）
pick=random      # 选人策略：random=候选里随机（不写=好感最高者，可复现）；与 name= 同写时 name 优先
intent=malice    # 指定意图键（默认按亲密度真实派发；非法值回落真实派发）
repeat=1         # 连发模式（默认**一次即停**；写 1 恢复"每 interval 一次"的旧行为）
force_remote=1   # 【诊断】本次强制触发按**异地**判定：不弹确认窗 → 直发 → 收尾必落分流③
no_tools=1       # 【诊断】本回合**一个工具都不调**：帧带 speech_only=true → Python 追加调试约束
                 #   + 执行层拦下**全部**工具（连只读也拦，busy 只拦动作）→ 纯传音形态，回复更快
# 以 # 开头为注释；文件内容每 ~2s 重读，改了即时生效
```

观察横幅/红点的标准姿势：`force_remote=1 + no_tools=1 + name=<某联系人> + intent=smalltalk`，**测试期间不要开该 NPC 的对话窗**（窗开着走分流① 直播已读 → 既不亮红点也不弹横幅）。预期日志链：`[DiagSwitches] … forceRemote=True … noTools=True` → `forced trigger … sameGrid=False …` → `（NPC 主动传音）npc_reply` → `[ContactDuty] 主动开口分流：③未读登记 + 红点/横幅` → `[UnreadBanner] 常驻宿主已建`。

**★ 一次即停（默认，用户要求）★**：只要收口过一次就停表——同格点「同意」/「不同意」、异地（或玩家忙）直发传音，三者任一发生后打 `本次已收口 → 停表`。**重新武装**：把开关文件**改一下并保存**（mtime 变化即视为"再来一次"）；要连续跑就写 `repeat=1`。停表只影响强制模式，**正常日节拍/概率/冷却完全不受影响**。
要点：强制模式**不写冷却表**（删文件后不留痕）；事件帧带 `debug:true` → Python 豁免 `min_interval`；默认选人**不是随机的**（候选按好感降序取第一人，可复现），要随机才写 `pick=random`。

**「触发了但没反应」的三种正常解释**（都会打日志，别误判成链路坏）：
① `forced trigger 跳过：玩家处于战斗/死亡/确认窗`（闸 2）；② `forced trigger 跳过：候选集里没有 name=…`（闸 4，非通讯录成员必须同格）；③ Python 侧 `诊断强制触发被丢弃：该 NPC 有回合在跑`（闸 10，主动开口不排队、错过就丢）。

**路径 B：配置提高概率（保留真实概率链路）** —— 面板里那 6 行**只对开发者可见**：`config.json` 手改 `"ui": {"show_advanced": true}` 后重开配置面板才显形（`get_config` 每次读文件、无缓存，不必重启）。测试预设 `min_interval=20 / daily_chance=100 / npc_cooldown_days=0 / npc_cooldown_real_s=0 / low_intim_threshold=0 / low_intim_halve=false`，**重启 Python 生效**（约 2s，不需要重启游戏）。重连时 `ModMain.OnWsReconnected → RefreshInitiativeFromConfig` 重拉参数，**"配置到底有没有到 C# 监测器"看这一行**：`[NpcInitiativeMonitor] config: dailyChance=100 cooldownDays=0 realSec=0 lowThresh=0 lowHalve=False`。
⚠️ `min_interval` 别设 0：一次闭关/过月可跳 30 日 → 瞬间涌进最多 30 个回合（每次 = 一次真实 LLM 调用）。**测完必须还原默认**。

**路径 C：只测 Python 半边（不起游戏）** —— `ws_channel` 是**单连接语义**，所以**别在游戏运行时**用 `scripts/chat_cli.py --ws` 连 8766（会和 C# 互相踢）。起独立实例（不同端口 + 临时 `storage_root`）再 `/init <npc> <intent>`；`tests/test_initiative.py` 已覆盖 `handle_initiative` 的忙守卫/节流/consented/debug 豁免等分支。

#### G.5.1 两级闸（用户拍板：闸门管「能不能打扰」，不管「能不能说话」）

| 闸 | 判据 | 命中后果 |
|---|---|---|
| **能说闸** `SpeechBlockReason()` | 未进世界（`playerUnit=null`）、死亡/复活窗（`UIMapDie` 可见） | 本次触发**丢弃**（不排队，下一拍/次日重试） |
| **能做动作闸** `BusyReason()` | 战斗中（`battle.isBattle`）、`DramaGate.HasPending`、我们的确认窗可见、战斗 UI 可见、`Mask`/`Window` 层有可见 UI、`UIChatAi` 可见、`UI(0)` 层顶名含 `Drama` | **不禁言语**：事件帧带 `busy:true` → 本回合工具表剔除 5 个动作工具、只留 3 个只读；同格**不弹确认窗**，降级直发传音（未读红点 + 横幅） |

| 玩家状态 | 同格 NPC | 异地通讯录 NPC |
|---|---|---|
| **空闲** | 说话 ✓ 动作 ✓（先弹「同意/不同意」，同意后生成） | 说话 ✓ 动作 ✓（不弹窗：未读红点 + 横幅） |
| **忙碌**（看界面/战斗中/有模态） | 说话 ✓（降级直发）**动作 ✗** | 说话 ✓ **动作 ✗** |
| 未进世界 / 死亡窗 | 丢弃 | 丢弃 |

* 状态闸的历史坑（**已修，勿回退**）：`g.world.battle != null` 判空**恒真**（其类型是 `WorldBattleMgr` 战斗**管理器**，世界常驻）→ 真判据是它自己的字段 `Boolean isBattle`；`data.isDead`/`isInBattle` 是**死代码**（14628 类型全量扫描 hits=0）；`GetUI(...)` 判空**存在≠可见** → 改 `UiVisible()`（`activeInHierarchy + activeSelf + Canvas.enabled`）；玩家死亡 → 判死亡窗 `UIMapDie` 是否可见（fail-open）。
* **模态/窗口层按游戏自己的分层**：`UILayer` 八层（具名成员 `UI/UITop/Guide/Loading/Window/Mask/FullEffect/TempUI`；⚠ **数值顺序不是 0..7**，越界即抛）。判 **`Mask`（全屏输入遮罩）与 `Window`（窗口层）任一有可见层顶 UI** = 玩家被界面占着 → 不打扰。**绝不判 `UITop`**（HUD/地图常驻，判了就永远拦）；**`UI(0)` 层不按层判、按界面名点名判**：`UIChatAi`（我方对话窗）与层顶名含 `Drama` 的剧情窗 = 忙，**其余面板（NPCInfo/人物/任务/先天气运/结算/MinMap）一律放行**。
  依据：① 反编 `UIMgr.GetLayerTopUI(UILayer,int)` 是游戏自己的「层顶是谁」接口；② F12 快照实测（用户开的 8 个页面全在 `UI(0)`，`Window` 层全程为空，`Mask` 只在剧情窗出现 `MaskNotClick`）。
* 要临时绕过状态闸测链路：开关文件里写 `ignore_state=1`（热生效，放行时仍会打命中原因）。
* **Python 侧落地（`busy` 变体）**：`ws_channel.handle_initiative(busy=)` → ① **在本回合尾部追加一条 plugin 消息**（`_run_turn(post_note=)` → `_append_plugin_note`，形状同 `_inject_acquaintance_if_new`：`surfaceOp=append` 进 Surface 模型可见、`source.kind=plugin` 被 `project_ui_history` 滤除 UI 不可见、不进筐不触发新回合），内容 = `ChatHub.BUSY_NOTE`；② 执行层兜底 `DialogueAgent.speech_only_turn`（历史残留/幻觉硬调动作 → 回 canonical 拒绝结果，落账形状与正常路径**逐字同构**）。只读/动作分组在 `tools/schemas.py`：`READONLY_TOOLS`（inspect_unit / search_units / query_world）与 `ACTION_TOOLS`（其余 5 个）。
* **为什么是"追加消息"而不是"删工具"（曾实现后撤回）**：① 与自主交互本质一致 —— 它本就是"模拟用户发一条消息"；② **工具表恒定**：`tools` 在 LLM 请求的**前缀**里（`llm/openai_client.py: tools=oai_tools`），忙碌回合删、下回合恢复会让 `request/header` 的 `header_hash` 每回合变化 → **上游 prefix cache 失效**（上下文常达 ~25k tokens，代价可观）；③ 账本可回放"当时为什么只说话"。**代价**：约束是软性的，故保留执行层兜底作为硬防线。
* **排队语义**：**玩家消息排队** —— 进 `inbox` 的 `next-turn` 筐，每回合领一条（`next-step` 筐全量插队先吃）；**NPC 主动开口不排队** —— 该 NPC 有回合在跑即丢弃；全局并发 `_turn_slots`(2) 只让**不同** NPC 等空位（限流等待，不是队列）。

#### G.5.2 ~~F12 = UI / 闸门快照~~（**2026-09-15 已退役并删除**）

> **本节描述的工具已不存在**：F12/F1 热键在 09-14「删除全部 F 键」时就被摘掉了，但
> `csharp/UI/UiGateDump.cs`（418 行）、它的探针口 `NpcInitiativeMonitor.GateReport()/GateProbes()`
> （89 行）当时**没跟着删**，成了无入口的死代码，09-15 探针清理时一并删除。
> 下面保留的是**结论**（判据口径仍有效，见 §G.5.1）与那条 `UILayer` 的坑。

原工具：打开任意页面 → 按 F12（别名 F1）→ 整份记进 `Player.log`，每行带 `[UiGateDump]` 前缀。三段输出：
① 判定结论（`能说闸=…｜能做动作闸=…｜IsPlayerBusy=…` + 逐条探针）
② `UILayer 0~7` 逐层清单（每层标参不参与判定 + 该层每个已打开 UI 的名字/类名/可见性/尺寸/order）
③ 可见 UI 结构树（inactive 只报一行不下钻，900 行封顶）。

**排查结论（这才是要留的东西）**：只有 `Mask(5)`/`Window(4)` 的**层顶可见**算"玩家忙"；
`UI(0)`（我们三个 AB 面板所在层）与 `UITop(1)`（HUD 常驻）永不判；
`UI(0)` 层内**按界面名点名判**（`UIChatAi` 可见 / 层顶名含 `Drama` → 忙），其余 `UI(0)` 面板一律放行。

> **⚠ 遍历必须用具名成员，不能用 `(UILayer)i` 数值转换**：`UILayer` 的**真实数值不是 0..7 顺序**（层数组按枚举成员数定长，越界即抛 `IndexOutOfRangeException`）。同一根因也解释了历史日志里所有 `UITop=(err) … TempUI=(err)`；闸门本体一直用具名成员所以从未受影响。

#### G.5.3 「主动开口」回合喂给模型的完整提示词

一键复现：`python3 scripts/dev/dump_initiative_prompt.py [npc] [intent] [缘由]`（真实 SystemPrompt/prompts 目录 + Stub 桥，不连游戏、不调真 LLM）。**自主交互没有独立提示词模板**——它是"被动对话全套提示词 + 一条伪 user 舞台指令"，实际请求 = ① `role:system`（`SystemPrompt.assemble`）② 账本投影 messages ③ 恒定 9 工具：

| 层 | 内容 | 来源 |
|---|---|---|
| system · 消息读法 | `[消息读法]`：日期戳/舞台提示/图片占位该怎么读 | `prompts/sections/harness_identity.txt` |
| system · 世界设定 | `world_basis.txt` 6 段（世界背景设定/境界体系/大地舆图/研修与技艺/势力与正邪/**边界情况**）+ `world_persona_rules.txt` 8 段（资质与品评/先天气运/性格与处世/体貌与魅力/名声与道行/灵体与道途/种族与他族/天骄），共 14 段 | `prompts/sections/*.txt` |
| system · 人设 | `你是郦安，是一个好人` + `[输出底线] …禁止透露你是 AI、模型或程序 …` | `prompts/personas/<npc>.txt` + `_suffix.txt` |
| system · 工具规则 | `[工具使用规则]` **~750 字**：只讲跨工具路由 / 结果解读 / 语气 / 错误处理 —— 参数名·枚举·取值范围**不在此处复述**，归 `tools/schemas.py`（见 §G.5.4） | `prompts/sections/tool_usage.txt` |
| messages · L1 四段 | `Current runtime context —— 当前时间 / 自身 / 玩家 / 近况`（逐段差分注入；**同格/异地措辞就在「玩家」段**） | `system_prompt.format_l1_context` |
| messages · 首见注记 | `（系统：这是你们之间的第一段传音，此前你们并无对话往来。）` | `ChatHub.ACQUAINTANCE_NOTE`（`plugin` 源，UI 不显示） |
| messages · **伪 user 意图** | `（NPC主动传音：向玩家表达思念）（缘由：…）` | `initiative.format_initiative_message`（`initiative` 源） |
| messages · 忙碌约束（仅 busy） | `（系统：玩家此刻正忙——…**不要发起任何行动**…）` | `ChatHub.BUSY_NOTE`（尾部追加，`plugin` 源） |

**UI 不会看到那条"模拟用户消息"**：伪 user 意图是 `user/message + source.kind="initiative"`，`history.project_ui_history` **不取正文**，只投标记项（`intent`/`intent_text`/`reason`），C# 渲染成一行分隔条 `—— 主动传音 · 表达思念 ——`；`kind="plugin"` 的 L1/忙碌约束整条跳过。**模型看得见、UI 不存在。**

**意图键（intent key）** 是 **C# 触发期决定的"派发意图"**（不是模型生成、不是玩家输入），C# 只发英文键 + 缘由，Python 映射成中文舞台指令：

```csharp
private static string PickIntent(int intim) {                     // NpcInitiativeMonitor.cs
    var rnd = UnityEngine.Random.value;
    if (intim < 0)   return RandomOf(NegativeIntents);                      // 4 负向：malice/vent/provocation/disdain
    if (intim >= 120 && rnd < 0.4f) return RandomOf(PositiveCloseIntents);  // missing/affection（40%）
    return RandomOf(PositiveIntents);                                       // 5 正向：greet/smalltalk/courteous/life/recent
}
private static string BuildReason(int intim) {                    // 缘由＝好感阈值阶梯给的"开口抓手"
    if (intim >= 120) return "念及与你多日情谊，特来以神识传音寻你";
    if (intim >= 60)  return "想起你，想与你聊上几句";
    if (intim < 0)    return "心中不忿，寻你理论";
    return "";
}
```

即：**先按好感选"情绪档"，再在档内随机抽一条**。选人同理：候选按好感降序（日节拍同好感随机打散、诊断强制不打散），成功即 `return`（一日只一人）。**英文键不上线**：`llm_adapter.canonical_to_openai` 只保留 `role/content/tool_calls`——模型看到的只有中文舞台指令；键只落账本（审计）与 UI 分隔条短标签。**意图的语义是"舞台指令"不是"命令"**：它给开场定方向 + 一个事实抓手，具体措辞仍由人设决定。

### G.5.4 工具文档的两层分工：契约 vs 决策（09-13 去重，勿回退）

**问题**：`tool_usage.txt` 与 `schemas.py` 曾把同一份契约各写一遍。system 段 4,875 字里 `tool:usage` 占 **2,703（55%）**，套上 `tools` JSON 9,299 字，**每次请求固定 14,174 字 ≈ 5,000 token**；而真机 `in=` 只有 8,600~11,800 ⇒ **42%~58% 的输入是工具文档**，且上游 25/25 次 `cache_hit=-`（不能指望前缀缓存兜底）。

**分工判据**（判一条内容该住哪）：

> 删掉后模型「**不知道该传什么**」→ 归 `tools/schemas.py`
> 删掉后模型「**不知道该干什么**」→ 归 `prompts/sections/tool_usage.txt`

- **schemas = 契约**：参数名、枚举取值、类型范围、`required`、示例值、bounds。模型**填参那一刻视线所在**的地方，且是机器可校验的。
- **tool_usage = 决策**：跨工具路由、先做什么后做什么、结果怎么解读、语气约束、错误处理。这些 schema 表达不了。

**结果**：`tool_usage` 2,704 → **748 字（−72%）**；`schemas` 9,299 → **7,987 字（−14%）**；合计 **−27%**（≈5,006 → 3,392 token）。

**★ schemas 的精简地板（别白费力气）**：序列化后 **~4,665 字是 JSON 结构本体**（`"type"`/`"properties"`/`"required"`/`"additionalProperties"`）＋**枚举字面量**（`social_relation.op` 13 个值、`search_units.filters` 7 个子键…）——删了就是**改功能**不是瘦身。真正可动的散文只有 ~3.7k，压到 3.3k 已是"每个字都在讲契约"。**想再省只能砍功能。**

**★ 刻意保留重复的四条（不是漏删）**：`认 unit_id 不认名` · `陌生 ≠ 认识` · `面对面类异地不可行` · `error 处理`。它们是**行为规则不是契约**，schema 里没有对应位置；在 system 段再喊一遍换来的是正确率。**纯去重会掉正确率**——这是用户拍板的取舍。

**防回涨护栏** `tests/test_tool_usage_contract.py`（7 条，9 个变异全部验证过）：
- 双向覆盖：`TOOL_ORDER` 每个工具必须被 tool_usage 提到；tool_usage 里的 snake_case 不许是野名字
- **长度预算 900 字**（现 748）：超了不是"写得细"，是契约又被抄回来了
- **禁复述**：不许出现 `xxx=yyy` 式参数赋值签名；历史上抄过的 8 个片段不许回来
- **承重墙清单**：上面那四条政策每条一个断言（防精简删过头）
- 委派句：必须写明「参数以工具定义为准，此处不重复」——否则模型可能**自己编一个参数**
- schema 散文冒烟上限（宽松报警）· `_ALL_MAP` 无孤儿 · `READONLY_TOOLS` 必须是 `TOOL_ORDER` 前三位

> **变异验证教训**：最初写的 `[t["name"] for t in ALL_TOOL_SCHEMAS] == TOOL_ORDER` 是**恒真式**（前者由后者推导），打乱顺序照样通过。真正会静默出事的是「查询前置」——`READONLY_TOOLS` 必须是 `TOOL_ORDER[:3]`，否则玩家忙碌时会静默放行动作工具。**写护栏测试时先变异一次，恒真式的护栏比没有护栏更危险。**



**现象**：换存档打开通讯录，里面还有上一个存档加的好友。根因是**存储没有存档维度**——三层里只有关系层是对的：

| 层 | 来源 | 修前 | 修后 |
|---|---|---|---|
| 关系层 | 活世界 `RelationNetwork` | ✅ 随存档 | 不变 |
| 手动层 | `contacts.json` | ❌ 全局 `storage_root` | `storage_root/worlds/<world_id>/contacts.json` |
| 「最近」层 | 扫 `*.jsonl` mtime | ❌ 全局 | 只扫当前存档目录 |
| 对话历史本体 | `<npc>.jsonl` | ❌ 全局（换存档后同名 NPC **串号**，比通讯录更严重） | `storage_root/worlds/<world_id>/<npc>.jsonl` |

实现：`world_id` = **玩家 unitID**（随存档固化、世界唯一），由 C# 在 `load_happened`（`EGameType.IntoWorld`）与 `save_happened`（`EGameType.SaveData`）帧里携带（`ModMain.FillWorldIdentity`，另附 `player_name` 作人类可读标签——**本作存在与玩家同名的 NPC，名字不可当键**）。Python `persistence.world_root(base, world_id)` 统一算目录，`AgentLoop.set_world()` 与 `ContactsService.set_world()` 同时切（`ws_channel._apply_world`，**先切命名空间再 `discard_all`**，顺序反了会把旧存档增量写进新目录）；切档同时清会话索引缓存，C# 侧 `OnIntoWorldEvent` 立刻重拉 `list_contacts`/`list_sessions`（`ContactStore` 是静态镜像，不重拉会把旧存档好友继续喂给候选集与 NPC 面板按钮）。缺 `world_id`（旧 C#/手工 RPC）→ 沿用现状并 WARNING，**不猜**。未定存档（测试 / `chat_cli` / 尚未进世界）→ 退回扁平 `storage_root` 布局（后向兼容）。

---

## H. 官方 API 与反编速查（C 端联调资料）

### H.1 本机关键位置

| 对象 | 位置 |
|---|---|
| 游戏本体 | `E:\SteamLibrary\steamapps\common\鬼谷八荒`（`GameAssembly.dll` 在根，IL2CPP 元数据 `guigubahuang_Data\il2cpp_data\Metadata\global-metadata.dat`） |
| 官方 Mod SDK | `<游戏根>\Mod\modFQA\`：`代码编写教程\`（`GGBH_API.chm` 官方 API 帮助 + `ModMain\` 官方模板）+ `配置修改教程\`（配置表 `xlsx` 对照 + **`配置（只读）Json格式\` 全量配置导出**）+ **`资源修改教程\`**（`ResBuildABProject\` 官方 AB 打包工程 + `资源修改教程.docx`）+ `MOD模板例子\` |
| 神识传音 Mod 逆向 | `f:\ggbh_mod_analysis\神识传音Mod逆向分析.md`（原式实现与 API 用法的最初来源） |
| 游戏内 UI 源码 | `csharp/UI/`（三面板源码；**详细文档 [`ui-interception-spec.md`](ui-interception-spec.md)**） |
| UI 预览工程 | `f:\agent_loop\ui_preview\`（Unity 2020.3.9f1：链接 UI 源码，编辑/播放模式直接看样式，不进游戏） |
| 反编产物 | `F:\DecompDump\dump\*.txt`（ILSpy 小工具产物，见 H.3） |

### H.2 官方 API 按层级

```
g.*（进程单例，游戏一切入口）
├── g.world        世界实例：unit(取单位)/playerUnit(玩家)/battle/mapEvent(格子事件)
├── g.timer        主线程帧回调（g.timer.Frame=注册每帧；C# 壳用它做主线程调度）
├── g.events       事件广播（On/Off 订阅；近况/天下大事靠它攒）
├── g.conf         配置表（roleGrade.GetGradeItem/GetGradeName 等）
├── g.data         存档数据（world.playerUnitID 等）
├── g.root         场景根（AddComponent）
└── g.ui / g.res / g.sounds   UI / 资源 / 音效

WorldUnitBase（单位）
└── .data.unitData（DataUnit.UnitInfoData）公有字段：
    propertyData(属性) / relationData(关系) / pointX,pointY(位置) / schoolID(宗门ID)
    props,propData(道具) / equips(装备) / abilitys(心法),skillLeft(武技),skillRight(绝技),step(身法),ultimate(神通)
    allTask(任务) / memoryData(记忆) / appellationTitle(道号) / heart(道心) ...
    方法：CreateAction(new UnitAction*…) 发起行为（双人行为/移动/加气运…）

DataUnit.RelationData（关系引擎，挂在 unitData.relationData）
    读：GetIntim / GetRelation / GetAllRelation / GetIntimUnitData(好友) / GetHumanValue(人情)
    写：AddIntim / AddHate / SetIntim / AddHumanValue（关系切换字段 lover/married/master/student…）
    字段：intimToUnit / intimToPlayerUnit / intimToSchool

行为与剧情命令
    CreateAction(new UnitActionRoleTrains(playerUnit))  双人行为（聊天/双修/切磋…）
    new WorldUnitAIAction*{...}.ActionStart()          世界 AI 行为（双修/论道/邀约…）
    DramaFunctionTool.OptionsFunction(string, DramaFunctionData=null)  剧情/宗门/UI 命令
    DramaTool.OpenDrama(...)                            打开剧情
```

**工具与 API 的对应**：`inspect_unit` = `UnitSnapshot` 按 `classes` 按需采集；`search_units` = `g.world.unit.GetUnits` 全图遍历 + `filters` 多条件过滤；`query_world` = `g.conf`/`g.events`；动作类 = `CreateAction`/`WorldUnitAIAction`/`OptionsFunction` ＋二阶段校验。

### H.3 反编方法：工具链与实锤签名表

**工具链**：反编 `MelonLoader\Managed\Assembly-CSharp.dll`（IL2CPP 互操作程序集）+ `ICSharpCode.Decompiler`（写小工具 `F:\DecompDump\` 反编，产物 `F:\DecompDump\dump\*.txt`；`ilspycmd` 因包缺 `DotnetToolSettings.xml` 装不上，弃用）。**Mono.Cecil** 遍历全类型找"唯一持有某字段组合的类型"是定接缝的利器（快、无需运行时）。
> **局限**：互操作程序集**只有成员签名，方法体是 `extern`**；要看方法体逻辑需 `Il2CppDumper` 解 `GameAssembly.dll + global-metadata.dat`。属性"实际装什么值"只能真机核对。

**关键签名（均已编译验证）**：

| 数据 | 实锤签名 | 备注 |
|---|---|---|
| 姓名/性别/种族 | `propertyData.GetName()` / `.sex`(Man/Woman) / `.race`(Human/Demon) | 枚举转中文（`UnitSnapshot.SexCn/RaceCn`） |
| 境界 | `propertyData.gradeID`(int) → `g.conf.roleGrade.GetGradeName(int)` | 中文境界名 |
| 魅力/声望/资质/年龄/心情 | `propertyData.beauty/reputation/talent/age/life/mood` | int |
| 战力 | `FormulaTool.UnitPower.TotalPower(WorldUnitData)` | 属性+技能+道具综合 |
| 气运(先/后) | `propertyData.bornLuck`/`addLuck`（元素 `LuckData.id`）→ `g.conf.fateFeature.GetItem(id).desc` | |
| 宗门名 | `unitInfo.schoolID`(string) → `g.world.build.GetBuild(id)` → `MapBuildSchool.name` | 每局随机组合名，无 conf 固定表 |
| 地区 | `unitInfo.pointGridData.areaBaseID` → `g.conf.worldAreaBase.GetItem(id).name` | 须过 `GameTool.LS()`（见 D.6） |
| 功法(技能) | `unitInfo.skillLeft/Right/step/ultimate/abilitys`(ID) → `GetActionMartial(id).data`(MartialData) → `g.conf.battleSkillPrefixName.GetName(MartialData)` | 组合中文名如"煞云飞花术" |
| 道具/装备 | `unitInfo.propData.GetEquipProps(bool,bool)` → `PropsData.propsItem.name` | 中文名现成 |
| 与玩家关系/好感 | `relationData.GetRelation(player)`(枚举) / `GetIntim(player)` / `GetHumanValue(unitID)` | 枚举→中文（`RelationCn`） |
| 关系簿 | `relationData.parent/children/brother/lover/master/student/married`（unitID）→ `GetUnit(id).GetName()`；好友 `GetIntimUnitData(true,false).friendUnits` | 中文名 |
| 经历日志 | `g.world.unitLog.GetLogDataSync(unitID)` → **解码层** `LogData.allVitalLogData`(重要)/`allLogData`(常规) : `List<LogItemData>` → `LogItemData.month` + 逐 `logs[]/subLogs[]` 调 `Data.GetLogString()`（**只有这层出人话**） | 同类型还有**原始层** `allLog`/`allVitalLog` : `List<Il2CppStringArray>`（元素是 `string[]`，**不是** `LogItemData`，照 `LogItemData` 读会静默全空，见 D.1）；未固化的新条目在暂存层 `GetCacheWriteLog(unitID)` |
| 道号 | `unitInfo.appellationTitle.GetAppellationID()` → `g.conf.appellationTitle.GetItem(int).name` | |
| 全图单位 | `g.world.unit.GetUnits(bool)` | `search_units` 用，跳过玩家自己 |
| 单位唯一键 | `unitID`（官方示例实证）**≠ 中文名**；中文名用 `propertyData.GetName()` | npc_id 统一为中文名：`UnitLookup.Resolve` 先按 unitID 直查、失败全图按名匹配 |
| 枚举 | `UnitRelationType`(Parent/Children/Married/Master/Student/…)、`MartialType`(SkillLeft/Right/Step/Ability/Ultimate=灵技/绝技/身法/心法/神通) | 中文映射在 `UnitSnapshot` |

**反编经验**：
* `g.*` 是全局门面（`g.world`=WorldMgr / `g.conf`=**ConfMgr** / `g.timer` / `g.data` / `g.events`）。
* conf 表结构固定：管理类 `ConfXxx`（`g.conf.xxx`）+ 条目 `ConfXxxItem`（字段 `name/desc/id`），查表多为**基类 `ConfXxxBase.GetItem(int id)`**。
* Il2Cpp 集合不能 `foreach` / 不能向下转型（见 D.1）。
* `dynamic` 绑定需 csproj 引 `Microsoft.CSharp`。
* **查一个游戏类型的成员/泛型实参，必须读编译目标 `MelonLoader\Managed\Assembly-CSharp.dll`**（45MB，Unhollower 真实产物：字段已暴露成属性、`string[]` 写成 `Il2CppStringArray`），**不要**读
  `MelonLoader\Dependencies\Il2CppAssemblyGenerator\Cpp2IL\cpp2il_out\Assembly-CSharp.dll`（16MB，方法体全是 `ldnull; ret` 的 stub）。
  两者 md5 不同、`Managed` 才是 `csc` 实际引用的那个。用 Mono.Cecil 打印时**一定要打 `FieldType.FullName`**——
  只打 `.Name` 会把 `List<string[]>`、`List<LogItemData>`、`List<int>` 统统显示成 `List\`1`，据此判断层结构必错。
* 找到"能同时喂饱 A 和 B 两种字段的唯一消费者"是定位接缝的通用手法（如 `UIFastClick` = 全程序集唯一同时持有可重绑定 `KeyCode` 与 `InputButton` 的类型）。

### H.4 官方模组加载链

游戏「模组编辑器」新建模组会分配**命名空间** `MOD_xxxxx`（记录于 `ModData.cache` 的 `modNamespace` 与 `ModMain.cs` 的 `namespace`），导出得到 `ModCode/dll/` 等结构；主菜单「本地模组」导入勾选后，官方桥 `GGBH_MOD`（MelonLoader 0.5）加载 dll 并**反射调用 `{namespace}.ModMain.Init()/Destroy()`**。**命名空间、程序集名、导出路径 `ModCode\dll\{namespace}.dll` 三者必须一致**，否则 DLL 加载失败。MID / 配置表导出 / 加密约束见 D.2。
