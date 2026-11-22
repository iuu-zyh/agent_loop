# C# ↔ Python 桥：帧格式与语义

> 数据源：原 `docs/reference/APPENDIX.md` §A 拆出并**去重**（2026-11-20）。
>
> **RPC 方法表与事件表不在这里** —— 它们在
> [`ui-interception-spec.md`](ui-interception-spec.md) §3.2 / §3.3（那是唯一一份全表）。
> 本文只留**帧格式、超时、幂等、热生效、落盘时机、历史回放**这些独有内容。

---

## 0. 一句话

**单条 WebSocket 全双工**：Python 作 `WsServer` 监听 `127.0.0.1:8766`，C# 作客户端连入，**不共享内存**。
Python 是大脑（管 `messages[]/tools/模型`），C# 是双手（管 `g.world/g.conf` 与主线程调度）。

---

## 1. 启动方式

| 方式 | 步骤 |
|---|---|
| A) 手动 | 先 `python scripts/server.py`（起 WsServer `:8766` + AgentLoop），再进游戏，C# 插件 `Init` 起 `WsClient` 连入 |
| B) 自拉 | C# `Launcher.LaunchPythonOnce()` → `Process.Start("python", "…server.py")`，Python 起监听后 C# 重连进入 |

两种方式协议完全一致；`WsClient` 带断线自动重连（常规每 2s 一轮；Python 重启期间缩到 0.3s）。

---

## 2. 帧格式

**请求 / 应答**：Python 回合内取快照或执行工具 → C# `request`；C# 按 `req_id` 回 `response`。

```json
{"type":"request","req_id":"1","method":"get_context","params":{"npc_id":"林婉清"}}
{"type":"request","req_id":"1","method":"call_tool","params":{"name":"inspect_unit","arguments":{"target":"张三"}}}
{"type":"response","req_id":"1","ok":true,"data":{"text":"自身：林婉清 位置(100,200)…","raw":{…}}}
{"type":"response","req_id":"1","ok":false,"error":"好感不足，需≥180，当前135","data":null}
```

**事件推送**（双向）：

```json
{"type":"event","event":"player_message","npc_id":"林婉清","text":"[1年1月3日] 你好"}            // C#→Python（带游戏时间戳）
{"type":"event","event":"player_message","npc_id":"林婉清","text":"[1年1月3日] 这张图是谁\n[图片：a.png]",
 "images":[{"name":"a.png","url":"data:image/png;base64,…","w":1024,"h":576,"kb":220}]}      // C#→Python 带图
{"type":"event","event":"npc_initiative","npc_id":"林婉清","intent":"missing","reason":"…"}  // C#→Python 主动开口
{"type":"event","event":"step","npc_id":"林婉清","kind":"tool_call","name":"inspect_unit"}   // Python→C# 回合中
{"type":"event","event":"text_delta","npc_id":"林婉清","text":"张"}                           // Python→C# 逐字流式
{"type":"event","event":"npc_reply","npc_id":"林婉清","text":"……","initiative":true}         // Python→C# 收尾帧
```

* `step` 每完成一步推一次（`tool_call`/`tool_result`/`text`）供 UI 显示「正在做什么」；
  `text_delta` 供打字机；`npc_reply` 携最终完整回复。
* **群聊帧（`room_*`）的 phase 语义**见 [`../features/group-chat.md`](../features/group-chat.md) §5。

### 已知字段漂移（当前无害，勿照抄）

`tool_result` 帧的工具名键是 **`tool`**（`dialogue_agent._emit_step`，契约测试钉着），
而 `ChatFrameTee.Decode` 读的是 **`name`**（那是 `tool_call` 的键）⇒ 缓冲下来的
`tool_result` 帧 `Name` 恒为空串。今天没有影响（重放只用 `text` / `isError`），
**将来要用这个字段时先补解构**。

### 玩家消息时间戳契约

`text` 以 **`[N年M月D日] `** 开头（含日：同月内 1 号与 28 号必须可区分，否则「你三天前才来过」
这类话说不出），由 **C# 在玩家消息进 WS 之前**拼好（`ChatPresenter.OnSubmitRequested`），
Python **只识别、不重写**。用户 09-13 拍板「NPC 必须感知时间」。

---

## 3. 超时、错误与幂等

* `request` 超时走 `WsServer.request_timeout`（config `network.request_timeout`，现值 **120.0**；
  早期契约文本记「默认 5s」—— **以 config 为准**）。C# 未连接 / 超时 → 返回兜底文本，对话不断。
* 任一方断开：C# `WsClient` 每 2s 重连；**连接只保留最新一条**（游戏重进替换旧连接）。
  回合内断开，Python 侧照常落账。
* `search_units` 限 10 条、`inspect_unit(logs)` 分页（约定保留）。
* 确认窗 / 挂起动作由 `DramaGate` **120s 超时自愈**（"玩家长时间未响应"），
  与 Python `request_timeout=120` 同步兜底；迟到补发**幂等丢弃**。
* 挂起 pending 期间 `DramaGate.HasPending=true` → initiative 主动开口被状态闸抑制。

---

## 4. 配置保存 → 重启编排 / 热生效

**「保存 → 自动重启 Python → 自动重连」编排**：`set_config` 应答 `effective=restart/mixed`
（或 llm 热换失败）时走 `shutdown` RPC（应答即证明 Python 已排定 0.6s 后 flush + `os._exit`）
→ 等旧进程让位 → `Launcher.RelaunchPython()` → 等自动重连。

* **让位判据只有一个：连接已断**。状态机另记「本轮曾断过」（`_sawDisconnect`），
  **只有「断过 + 现在又连上」才算成功** —— 旧进程僵死、socket 还活着时绝不谎报「配置已生效」。
* **不做端口探测（勿回退）**。`Launcher.ProbePortFree` 是 09-12 事故根因：本机对「无监听端口」的
  connect 不立刻拒绝，**约 2s** 后才回 `ConnectionRefused`（旁证：`WsClient` 每轮失败重连 ~4s
  = 2s connect + 2s sleep），400ms 窗口必然超时 → 恒判「占用」→ 状态机永远停在「等端口释放」。
  **该方法已删除。**
* **有界兜底**（全部收口，绝不停在半路）：2s 未断且进程是自拉的 → `KillOwned()`；
  6s 仍未断（非自拉、无法强杀）→ 硬拉起一次；拉起后 4s 未重连 → 补拉一次（最多 2 次）；
  18s 未重连 → 中止并如实报错（区分「旧 Python 未响应关停，请手动关控制台」与
  「重启后未连上，看 `server.py` 报错」）。
* 实测预算：退出 0.6s → 拉起 0.1s → 监听就绪 0.7s → 重连 ≤2.3s ≈ **2~4s**（正常路径 ~1.7s）。
* 耗时反馈：状态条每 0.25s 刷新（`Time.unscaledDeltaTime` 计时，时间缩放拖不住它）。

### 哪些改动需要重启（按键判定）

`config_store.set_config` 按**键**分类，返回 `effective`（none/hot/restart/mixed）+ `hot_keys`：

| 键 | 生效方式 |
|---|---|
| `llm.*` | **热生效** —— `LlmRouter.swap` 换芯 |
| `ui.*` | **热生效** —— 只是面板显隐 |
| `network.request_timeout` | **热生效** —— 直接改 `WsServer.request_timeout`（`request()` 每次现读该属性） |
| `compaction.ctx_window` | **热生效** —— `_build_compactor()` 按最新 config 重建 → `AgentLoop.rebind_compactor()` 热挂回（**含冻结舱**里的每个 agent，漏推会留旧压缩器；新压缩器继承旧冷却表，避免热换后立刻误触发一次自动压缩） |
| `initiative.*` / `ui.portraits_enabled` | **热生效**（09-13 起）—— C# 在 `set_config` 回包且未走重启时立刻重拉一次 `get_config` |
| `network.port` | **重启**（且还要重启游戏，C# 连的是旧端口） |

`HOT_BLOCKS = (llm, ui, initiative, compaction)`，**面板上唯一还需重启的是 `network.port`**。

热生效失败（换芯/改活/重建抛异常）→ 置 `swap_error`，C# 照旧自动重启兜底（改动已写盘，重启必然生效）。
UI 侧同步：`ConfigPresenter.Init` 用 `RelabelRow` 把行文案改成实际生效方式（预制件里写死「（重启生效）」），
悬停说明与状态条文案随之改为「即时生效」/「热生效未成功，正重启…」。

---

## 5. 删除的落盘语义

用户拍板「与对话增量同一套冷冻规则」：`delete_history` 只做两件事 ——
把一条 `history/delete` 注记**追加进内存账本**，以及回包带上过滤后的新历史投影。
所以 UI 上「删完立刻消失」是**内存投影，不是落盘**。

| 时机 | 是否写 jsonl |
|---|---|
| `preview_delete_history` / `delete_history` | **md5、行数、mtime 全不变** |
| 连接建立 `world_hello` | 不变：只切命名空间 + 补载群名录，**既不写盘也不丢弃**增量（身份真的变了才丢弃内存活体 —— 与读档同语义） |
| 读档 `load_happened`（未存档） | 不变，且**被删回合回来** = 回滚 |
| 游戏存档 `save_happened` → `flush_all()` | 行数 +1（多一条 delete 注记）= 固化 |
| `shutdown`（**仅**配置保存「重启生效」项触发） | 重启前 flush |
| 关游戏不存档（C# 退出**不发** `shutdown`） | 增量随 Python 进程消失 |

**一个有意的例外**（用户确认保持）：配置保存触发的重启会在 `shutdown` 前固化未存档增量
（09-11「重启 = 世界延续」防丢），因此「删了历史 → 立刻改配置」也会把删除一起固化 ——
看起来像"删除立刻落盘"，实则发生在重启那一刻。

---

## 6. 历史回放与渲染上限

* **回放 = 全部回合**：`ChatPresenter` 固定发 `limit=0`；`ChatHub._parse_limit()` 语义 ——
  **缺省/非法 → 10**（老行为，`chat_cli`/测试默认不变）、**0 或负数 → 0 = 不截断**
  （`project_ui_history(max_turns=0|None)` 解释成"全部"）。
  实测量级（26 回合账本）：全部 = 109 项 / **62.7KB** 一帧；`limit=10` = 43 项 / 40.1KB。
* C# 渲染上限 `ChatWindow.MAX_ROWS` = **1000**（≈250 回合），**超限不静默**：
  `ReplaceHistory` 结束时若发生裁剪，列表顶部插一行
  「…更早的 N 条历史未渲染（窗口上限 1000 行）」（该行 `Turn=0`、不可选、不参与删除）。
* **仍未做**：上拉分页 / 虚拟列表（真到上千回合才需要）；`complete_turns` 仍未被 C# 使用。
* **★ 历史回放 ≠ 全部内容**：`open_chat` / `open_room` 的重放只含**已经落账的内容**
  （单聊 = 已收口的回合；群聊 = `Room.log` 里已经说完的那几条）—— "说到一半"的半截正文、
  关窗期间到达的工具行/思考/玩家自己那句话，**都不在快照里**，由 `ChatFrameQueue` 的段
  （单聊：整轮记录；群聊：未闭合轮）经 `DrainSoloFrames` / `DrainRoomFrames` 补上。
  ★ **消费必须在 `ReplaceHistory` 之后**（先消费会被整块重建擦掉），
  且单聊那次要排在 `ClearReplayGate` 与删除模式入口之前。
* **删除模式的计数单位 = 回合**：`ChatWindow.UpdateCount()` 用 `HashSet<int>` 去重
  （旧写法逐**行** +1，而一个回合有 4 行 → 只显示一个回合也报「已选 4 回合」）；
  `全选/取消全选` 判定同步。
* 哨兵测试：`test_project_ui_history_max_turns_zero_means_all` /
  `test_ws_open_chat_limit_zero_returns_all_turns` / `test_parse_limit_semantics`。

---

## 7. Python 侧对接

`WsServer(port=8766)` → `AgentLoop(bridge=WsGameBridge(ws))` → `ChatHub(loop, ws)`，
注册 `hub.handle_message` / `hub.handle_initiative` 后 `await ws.start()` 常驻监听
（脱离游戏测试用 `StubGameBridge` + `StubLlmClient`，见 `tests/test_ws_channel.py`）。
