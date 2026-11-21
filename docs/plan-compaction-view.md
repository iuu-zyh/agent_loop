# 压缩纪要：展示与修改（计划 + 实施记录）

> **一句话**：压缩产出的纪要现在只躺在账本里，玩家看不见。本功能给它一栏默认收起的
> 折叠行（仿「内心思量」），展开可读全文；并且允许在**压缩刚完成、还没开启新一轮对话**
> 的窗口内就地改它 —— 改完回传 Python、追加进账本，随存档一起固化。

| 阶段 | 内容 | 状态 |
|---|---|---|
| P1 | Python：纪要读取 / 可编辑判据 / 改账（纯函数 + 账本事件） | ✅ 已完成 |
| P2 | Python：RPC `edit_compact_summary` + 推送 `compact_summary` + 历史投影 | ✅ 已完成 |
| P3 | C#：折叠行（默认收起、展开看全文、带「修改」钮） | ✅ 已完成 |
| P4 | C#：左侧编辑面板（半宽、大文本域、发送钮、成功提示） | ✅ 已完成 |
| P5 | 联动：直播事件 / 回放 / 锁定时机 / 群聊与换人的边界 | ✅ 已完成 |
| P6 | 测试 + 复核（模拟一遍全流程）+ 文档收尾 | ✅ 已完成 |

> 最后更新：10-31 12:30。**已部署**（10-31 10:31 的 DLL / 10:16 的 exe，产物与仓库构建
> 逐字节一致、均含本功能 —— 见 §8.0），**真机验收待做**（八步操作清单见 §8.1；
> 这两件事不是同一件：部署到位 ≠ 在游戏里点过）。
> `plan-sect.md` 的 §2/§6 与本功能同批开发，但它那部分**仍未部署**。

---

## 0. 需求原文（用户口述，逐条对应到设计）

1. 「当前是有压缩功能的，但是压缩后产出的总结是不展示的，我需要可以选进行展示」→ §2 / §3.1
2. 「UI 形式可以参考内心思量那样的，默认不是展开形式，点击可以展开观察到压缩后的内容」→ §3.1
3. 「玩家可以选择对压缩后的上下文进行修改」→ §2.3 / §3.2
4. 「仅限压缩完成后还未开启新一轮对话的时候可以去修改……一旦对话了，就不再可以修改」→ §2.2
5. 「同样考虑存档才落账」→ §2.4
6. 「优先改好 python 侧的逻辑和 C 侧的逻辑，再去做 UI」→ 实施顺序 P1→P2→P3→P4
7. 「打开之后……新增一个按钮，就是修改压缩后的信息的按钮，点击之后需要在对话 UI 进行展开，
   这里需要左侧出现新的区域，可以占据半个 UI 的宽……一个大型输入框……内置发送按钮……
   发送成功有提示（指修改后的压缩信息传回到了 python 侧）」→ §3.2

---

## 1. 现状核实（全部有出处，不猜）

### 1.1 压缩产物长什么样

`Compressor._replace`（`compaction/compress.py:454`）在压缩成功时**追加两条事件**：

```python
# ① 审计（给删除逻辑算覆盖闭包用）
session.append("compaction/summary",
               {"shadowedRange": {...}, "shadowedSeqs": [...], "compactionId": cid})
# ② 真正进 surface 的那条消息
session.append("user/message", {
    "role": "user",
    "content": [{"type": "text",
                 "text": f"{SUMMARY_OPEN}\n{summary}\n{SUMMARY_CLOSE}"}],   # :466
    "source": {"kind": "plugin", "plugin": "compact", "compactionId": cid},
}, {"surfaceOp": {"op": "replace", "start": start_seq, "end": end_seq}})
```

- 标记常量：`SUMMARY_OPEN = "<compacted-summary>"` / `SUMMARY_CLOSE`（`compress.py:24-25`）。
- **纪要节点是 `user/message` + `surfaceOp: replace`**：它在 surface 里占了被折叠那一段的位置，
  是模型可见的普通 user 消息（`Session.derive_messages` 直接返回它）。
- `compactionId` 是这条纪要的稳定标识：同一条纪要无论后面改几次，id 不变（§2.3 依赖这点）。

### 1.2 为什么 UI 现在看不到

两条独立的过滤，**都作用于同一条事件**（`session/history.py`）：

| 位置 | 判据 | 结果 |
|---|---|---|
| `history.py:95-97` | 任何 `surfaceOp: {op:"replace"}` 的节点都不进 UI 投影 | 替换节点整体跳过 |
| `history.py:117-118` | `user/message` 且 `source.kind == "plugin"` → `continue` | 压缩纪要按"内部物"滤除 |

两个判据本身都是**对的**（替换节点复述的是账本里已有的内容，投影它必然多一条；
L1 运行时上下文也确实不该进 UI）。所以修法**不是**放宽这两条，而是给纪要**单开一种条目**
（§2.5 的 `kind: "compaction"`），只在"活着的那条纪要"上出现。

### 1.3 修改是可行的：账本只追加

- `docs/features/context-projection.md:28`：「`Session.log` 事件流水账。seq = len(log) 单调递增。
  **append-only，永不改写**」。
- 所以"修改纪要"**不能原地改 `data.content.text`**（那会破坏回放/前缀缓存的立足点，
  且存档里已经写过的那份会与内存不一致）。正确写法是**再追加一条 replace**：
  `surfaceOp: {op:"replace", start: 旧纪要seq, end: 旧纪要seq}` —— `SurfaceManager._apply`
  （`session/session.py:106-117`）对 `start == end` 的处理就是"这个位置换成新节点"，
  与压缩折叠一段区间是同一个算子。
- 这也正好是用户的原话：「压缩上下文本质是在账本追加一条消息，修改的话就是对追加的内容进行替换即可」。

### 1.4 三个入口的时序事实（决定"可编辑窗口"怎么判）

| 入口 | 代码位置 | 压缩发生在 | 压缩后立刻发生什么 |
|---|---|---|---|
| 手动 `/compact` / 标题栏「压缩」 | `transport/ws_channel.py:1043` `handle_compact` → `_compact_agent`（`:1098`） | **回合外**（`agent.phase.kind == "idle"` 才允许） | 回推 `compact_result`；**没有**新回合 |
| 自动（超阈值） | `agent/dialogue_agent.py:1062-1067`，在 `_turn()` 里 | **`turn/start` 之后**（`:1057` 先落 turn/start） | 同一回合马上发 LLM 请求 |
| 旧客户端兜底 `/compact` | `ws_channel.py:1066` `_handle_compact_fallback` | 同手动 | 回推 `npc_reply(error=true)` |

⚠ 这张表说明：**"压缩完之后还没对话"这个窗口只对"手动压缩"真实存在**。自动压缩永远发生在
回合内部，压缩完的下一秒模型就读到它了。所以 §2.2 的判据必须能表达"这条纪要落在回合里"，
而不能只看"当前是不是 idle"（后者会把自动压缩的纪要误判成可改）。

**产品结论**（已按此实现）：手动压缩 → 折叠行可改，直到下一回合开始；自动压缩 → 折叠行
**一出现就是锁定的**（只展示、不可改）。这不是退让，是用户规则的直译。

---

## 2. 设计（Python 侧）

### 2.1 数据模型：只认 surface 里"活着"的纪要节点

新增 `compaction/summary.py`（纯函数，不碰 WS / 不碰 agent）：

```python
is_summary(ev)        # user/message + source.plugin == "compact"
summary_text(ev)      # 取出正文并剥掉 <compacted-summary> 标记
wrap(text)            # 反向：正文 → 带标记的 content
latest(session)       # 扫 surface.nodes，取**最后一条**纪要 → info dict；没有则 None
editability(session, seq) -> (bool, reason)
apply_edit(session, compaction_id, text) -> dict   # 校验 + 落账（唯一写入口）
```

`info` 形状（Python 内部 + 协议共用，见 §2.5）：

```python
{"seq": 最新节点 seq, "anchor": 原始位置 seq, "compaction_id": cid,
 "text": "正文（已剥标记）", "chars": 1234,
 "editable": True, "lock_reason": "", "edited": True, "edits": 2}
```

**为什么用 surface 而不是扫 log 找最后一条**：一条纪要可能被改过多次（每次一条 replace），
surface 里只留**最后那条**；而被改掉的旧节点虽然还在 log 里，却已经不在 surface。
取"surface 顺序里的最后一条"天然等于"最新且仍生效的那条"，不需要额外的版本比较。

**`anchor` 的作用**：编辑事件在 log 里的 seq 排在**末尾**，但它占据的**时间线位置**是原纪要
的位置（replace 到同一个位置）。历史投影要按账本顺序把折叠行画在正确的地方，就必须知道
"这条最新节点锚在哪条原始事件上"。所以编辑事件额外带 `source.anchorSeq`，
`latest()` 返回 `anchor` = `source.anchorSeq`（**用 `is None` 判，不用 `or`** ——
seq 0 是合法值，`or` 会把它吞成"自己"）。

### 2.2 可编辑窗口的判据（账本推导，运行时再兜一道）

**账本判据**（`editability`，纯函数、可回放、C# 回放时拿到的就是它算出来的值）：

一条纪要在**同时满足**下面四条时可编辑（语义 =「这条纪要还没被任何一次请求用过」）：

1. 它仍在 surface 里（不是被后来一次压缩折走的旧纪要）；
2. 它**不在一个回合内部** —— 落账那一刻没有未闭合的 `turn/start`；
3. 它之后**没有** `turn/start`（= 没有开启新一轮对话）；
4. 它之后**没有** `assistant/message`（防御性兜底，见下）。

★ 第 2 条是本功能**最容易写错的一格**，也是最关键的一条：自动压缩发生在 `turn/start`
**之后**（`dialogue_agent._turn`），所以第 3 条对它**恒成立**（它的 `turn/start` 在它前面），
只看第 3 条会把自动压缩的纪要判成"可改"。第 2 条把它钉死：**自动压缩产出的纪要永远不可改**
—— 这正是用户那条规则的直译（"压缩完了，如果还没对话，那可以修改；一旦对话了，就不再可以修改"，
而自动压缩本来就是"对话已经开始了"才发生的）。

第 4 条在当前代码里没有现实路径（回合必以 `turn/start` 开头，已被第 2/3 条覆盖），
留着是给"将来若有别的路径直接落 assistant"的保险 —— 它**独立成立**，不靠前两条顺带兜住
（`tests/test_compaction_summary_view.py::test_assistant_message_after_summary_locks_even_outside_turn`
直接钉这一条）。

**运行时兜底**（RPC 里再判一次，不依赖账本推导）：
- `agent.phase.kind != "idle"` → 拒绝（回合在跑，改了也影响不了已在途的请求）；
- `npc_id in self._compacting` → 拒绝（正在压缩，马上会出新纪要）。

**为什么还要运行时那一道**：账本判据能看到"已经发生"的事实，看不到"正在发生"的那半秒。
两道都留，任何一道拦下都回一句人话（§3.4），绝不静默。

### 2.3 修改怎么写账（唯一写入口 `apply_edit`）

前置校验全部 fail-closed（任一条不满足 → 抛 `SummaryEditError`，账本一字不动）：

| 校验 | 不满足时的原因文案（给 UI） |
|---|---|
| 该 npc 有会话且存在纪要 | 这段对话还没有可修改的压缩纪要 |
| `compaction_id` 与当前最新纪要一致 | 这条纪要已被新的压缩取代，不能再改 |
| `editability(...)` 为真 | 已开启新一轮对话，压缩纪要不能再修改 |
| 正文 strip 后非空 | 纪要不能为空 |
| 正文 ≤ 20000 字 | 纪要过长（上限 20000 字） |

落账（两条事件，**先审计后替换**，与压缩的写法对称）：

```python
session.append("compaction/edited", {
    "compactionId": cid, "fromSeq": old_seq,
    "charsBefore": 旧字数, "charsAfter": 新字数, "reason": "user",
})
session.append("user/message", {
    "role": "user",
    "content": wrap(new_text),
    "source": {"kind": "plugin", "plugin": "compact", "compactionId": cid,
               "edited": True, "anchorSeq": anchor, "edits": n},
}, {"surfaceOp": {"op": "replace", "start": old_seq, "end": old_seq}})
```

四个刻意的取舍：

1. **`compactionId` 不变** —— 删除逻辑的覆盖闭包（`history_ops._represented`）按 id 查
   `compaction/summary` 里的 `shadowedSeqs`。换 id 会让改过的纪要**丢失它代表的原文集合**，
   于是"删光原文、纪要该不该一起忘"永远判不出来（保守保留 = 删不掉的记忆）。
2. **`anchorSeq` 只落在 source 里** —— `derive_messages` 只用 role/content，多一个元数据键
   对模型零影响；而历史投影靠它定位（§2.1）。
3. **不改 `shadowedRange`/`shadowedSeqs`** —— 改的是措辞，不是"它代表哪段原文"。
4. **不搬动 L1 基线** —— 压缩折走 L1 段时 `compact_now` 会 `_resync_runtime_ctx_baselines`；
   改纪要不动 surface 的成员集合，故不需要。

### 2.4 存档语义：内存为真，存档落账

与删除、与所有对话增量**同一套规则**（`agent_loop.py:273` `flush_all`）：`apply_edit` 只往
内存账本追加事件，**不写盘**；C# 存档钩子 → `save_happened` → `flush_all` 才固化。
不存档就退游戏 = 这次修改没发生（与"这段话没说过"同语义）。
不需要为它写任何额外代码 —— 这是既有机制，本文只是**明确它同样适用**。

### 2.5 协议：一个 RPC + 一个推送事件 + 一种历史条目

**(a) RPC `edit_compact_summary`**（C# → Python，UI 发起的唯一写操作）：

```
params: {npc_id, compaction_id, text}
resp  : {ok: true, compaction_id, chars, text}          # text = Python 侧规范化后的正文
      | {ok: false, reason: "人话原因"}                  # 预期内的拒绝，不抛异常
```

`ok=false` 走"返回体"而不是抛异常：这些都是**预期内**的拒绝（窗口关了、被新压缩取代、
回合跑起来了），C# 要拿 reason 原文显示给玩家 —— 抛异常会变成 `ok:false` + `error`
（另一条渲染路径），两条路都要维护。缺 `npc_id` 仍然抛（那是调用方 bug）。

**(b) 推送事件 `compact_summary`**（Python → C#，压缩成功后立即发，手动/自动都发）：

```json
{"event": "compact_summary", "npc_id": "…", "compaction_id": "…",
 "text": "正文", "chars": 1234, "editable": true, "lock_reason": "",
 "source": "manual|auto", "turn": 7, "room_id": "g1"?}
```

- 手动路径在 `handle_compact` 里发（与 `compact_result` 并列，顺序：先 `compact_summary`
  后 `compact_result`，这样提示行收尾时折叠行已经在了）；
- 自动路径由 `DialogueAgent` 在压缩成功后经 `_emit_step({"kind":"compact", ...})` 抛出，
  `ws_channel._on_step` 把 `kind == "compact"` **转成** `compact_summary` 事件
  （agent 不认识 wire 事件名，保持分层）。
- 群聊回合里产生的自动压缩：事件带 `room_id`，C# **单聊窗丢弃**（群窗不展示某个成员的记忆，
  与"群聊不提供手动压缩"同一口径）；该纪要在 1:1 窗重开时由历史投影补上。

**(c) 历史投影 `kind: "compaction"`**（`project_ui_history` 内新增，回放用）：

```json
{"kind": "compaction", "text": "正文", "compaction_id": "…", "chars": 1234,
 "editable": true, "lock_reason": "", "seq": 最新节点seq, "turn": 0}
```

- **位置**：循环扫到 `anchor` 那条事件时吐这一项 → 与当时屏幕上的顺序一致
  （手动压缩之后它在末尾；自动压缩之后它在该回合的最前面）。
- **`turn` 恒 0**：它不是任何回合的内容，不进删除模式的可选单位（`BindKey` 见 `Turn > 0`
  才绑回合号；`Turn == 0` 且无 `uid` ⇒ 不可选）。它自己的去向由删除算法按覆盖闭包决定。
- **`scene` 恒 `""`**：纪要是某个 NPC 的记忆，不是某一场对话的内容，所以条目本身按单聊标。
  ⚠ 但**投影的场合过滤是按回合做的**，所以自动压缩若发生在群聊回合里，那一行仍会跟着
  那个回合一起被滤掉（1:1 窗回放看不到它）。这是**已知限制**，不是本设计没考虑：
  修它要把投影改成"按条目过滤 + 截断仍按回合"，会动到删除/截断两条被真机打磨过的路径，
  收益只有一个边角场景 —— 取舍见 §7「已知限制」。
- 被后一次压缩折走的旧纪要（不在 surface）：不吐（`latest` 只看 surface）。

---

## 3. UI 设计（C#）

### 3.1 折叠行（`SummaryGroup`，仿 `ThinkGroup`）

```
▸ 压缩纪要 · 1234 字                      [修改]
（展开后）
▾ 压缩纪要 · 1234 字                      [修改]
  你与韩立在永宁州相识……（全文，可滚动区外自然撑高）
```

- 默认**收起**：只显示标题行；点标题行展开/收起（与「内心思量」逐字同款手感）。
- 标题里的字数 = 正文字数，展开前后都显示（收着也知道长短）。
- 右侧「修改」钮：
  - `editable == true` → 亮着，点击打开左侧编辑面板；
  - `editable == false` → 置灰不可点，标题尾部附「（已锁定）」；展开后的正文底部再补一行
    灰字说明原因（`lock_reason`，如「已开启新一轮对话，不能再修改」）。
- 实现要点：**按钮不能挂在整行的 Button 上**。uGUI 的点击会沿层级冒泡，子按钮点一下会连
  父按钮的展开一起触发。所以根节点不挂 Button，只有左侧标题文字那块挂 Button，「修改」钮
  是它的兄弟节点。
- 行注册进 `ChatWindow._rows`（`RegisterRow`），与其它行一样受 `MAX_ROWS` 裁剪；
  但与删除模式无关（`Turn == 0` 不可选）。

### 3.2 左侧编辑面板（`CompactEditPanel`）

版式（坐标沿用 BG 的 1200×800，与 `ToolPanel` 同一套常量口径）：

```
 BG 1200×800
┌────────────────────────────┬──────────────────────────┐
│ 修改压缩纪要            ✕  │                          │
│ ┌────────────────────────┐ │      消息区（被盖住）      │
│ │                        │ │                          │
│ │  大文本域（多行）        │ │                          │
│ │  预填当前纪要全文        │ │                          │
│ │                        │ │                          │
│ └────────────────────────┘ │                          │
│ 1234 字 · 上限 20000       │                          │
│                  [ 发送 ]  │                          │
└────────────────────────────┴──────────────────────────┘
└──────── 600 宽（BG 的一半）────────┘
```

- 位置：左锚点（`anchorMin/Max = (0,0)/(0,1)`），宽 = 600（BG 宽的一半），
  顶边同样停在 760（不压顶栏那排窗口级按钮 —— ✕ 必须始终可点，`ToolPanel` 踩过这个坑）。
- 文本域：`InputField` + `lineType = MultiLineNewline`，左上对齐，多行换行。
  **照抄 `ConfigUiBuilder.MakeMultilineInput` 的建法**（那是本工程已验证的多行编辑框），
  但背景与字体取对话窗自己的（`ChatUiBuilder.ResolveSharedFont`），保证观感一致。
- 「发送」钮：读 `input.text` → 回调 Presenter → `edit_compact_summary` RPC。
  点发送后按钮变「发送中…」并禁用，回执到达再恢复（防连点发两遍）。
- 成功：面板收起 + 对话窗补一行系统提示「压缩纪要已更新」+ 折叠行正文与字数就地刷新。
- 失败：面板**不关**（玩家改的文字还在），底部红字显示 `reason`，按钮恢复可点。
- 关闭：右上 ✕；打开时自动聚焦文本域、光标置末尾。

### 3.3 交互时序（四条路，逐条落点）

| 场景 | 屏幕上的顺序 |
|---|---|
| 手动压缩 | 点「压缩」→ 提示行「正在压缩中…」→ `compact_summary` 到达 → **末尾出现折叠行**（可改）→ `compact_result` 把提示行改成结果文案 |
| 自动压缩 | 玩家发消息 → 回合开始 → `compact_summary` 到达 → 折叠行出现在**本回合内容之前**（已锁定）→ 之后才是本回合的系统组装/思考/气泡 |
| 重开窗 | `open_chat` 回执 → `ReplaceHistory` 按 `anchor` 的位置画折叠行（可编辑状态由 Python 算好） |
| 改完再改 | 成功后面板收起，折叠行刷新；只要还没开新回合，「修改」钮仍然可点（可以连续改） |

**锁定时机**（C# 本地，少一次跨进程往返）：玩家发消息（`OnSubmitRequested` 成功发帧）
或 `step` 帧带来新回合号时，`ChatWindow.LockSummaryEdits()` 把所有还亮着的「修改」钮置灰。
Python 侧仍然会拒（§2.2），C# 只是让按钮状态立刻跟上事实。

### 3.4 边界与失败姿态

| 情况 | 行为 |
|---|---|
| 没有压缩过 | 历史里没有 `compaction` 条目；面板按钮不会出现（不是禁用，是根本没有） |
| 群聊窗口 | 不展示折叠行（`project_room_history` 不投影它）；自动压缩的 `compact_summary` 带 `room_id`，被单聊窗丢弃 |
| 群聊回合里发生的自动压缩（1:1 窗） | **已知盲区**：直播事件因带 `room_id` 被丢；重开时又因"该回合属于群聊场合"被整段过滤 ⇒ 这一行在 1:1 窗看不到，直到下一次压缩。数据/记忆本身正常（见 §7 已知限制） |
| 换 NPC / 关窗 | 面板随宿主销毁（挂在 BG 下）；未发送的文本直接丢（与输入框同语义） |
| 发送时回合已经开跑 | Python 返回 `ok:false` + 「已开启新一轮对话…」，面板保留文本、红字提示 |
| Python 未连接 | 发送前就地拒绝：「未连接 Python」，不发帧（同 `OnSubmitRequested` 的判据） |
| 纪要被新的压缩取代（面板开着时） | RPC 返回「这条纪要已被新的压缩取代」，面板保留文本；折叠行本身由新的 `compact_summary` 顶掉 |
| 纪要为空/超长 | 就地拒绝（C# 先判一次给即时反馈，Python 再判一次兜底） |

---

## 4. 落点清单

**Python**

| 文件 | 改动 |
|---|---|
| `compaction/summary.py` | **新增**：`is_summary` / `summary_text` / `wrap` / `latest` / `editability` / `apply_edit` / `SummaryEditError` |
| `compaction/compress.py` | `_compact` 返回值补 `summary`（正文）与 `summarySeq`（新节点 seq），供推送与 RPC 使用 |
| `session/history.py` | `project_ui_history`：`anchor` 处吐 `kind:"compaction"`，`scene` 固定 `""`、`turn` 固定 0 |
| `agent/dialogue_agent.py` | 自动压缩成功后 `_emit_step({"kind":"compact", ...})`（走既定观察者出口） |
| `transport/ws_channel.py` | ① `_on_step` 把 `kind=="compact"` 转 `compact_summary` 事件；② `handle_compact` 成功后发 `compact_summary`；③ 新 RPC `edit_compact_summary` |

**C#**

| 文件 | 改动 |
|---|---|
| `csharp/UI/ChatItemViews.cs` | **新增** `SummaryGroup`（折叠行 + 「修改」钮，根节点不挂 Button） |
| `csharp/UI/CompactEditPanel.cs` | **新增**：左侧半宽面板（大文本域 + 发送 + 状态行） |
| `csharp/UI/ChatWindow.cs` | `AppendSummaryRow(...)` / `RefreshSummaryRow(...)` / `LockSummaryEdits()` / 回放 `case "compaction"` / 面板开关入口 |
| `csharp/UI/ChatPresenter.cs` | `case "compact_summary"`；`OnSubmitRequested` 与 `OnStepEvent` 的锁定调用；发送回调 → `edit_compact_summary`；成功提示 |
| `csharp/bridge/WsClient.cs` | 事件白名单加 `compact_summary`（漏了 = 帧在传输层被静默丢） |

---

## 5. 测试计划

| 文件 | 覆盖 |
|---|---|
| `tests/test_compaction_summary_view.py`（新） | `latest` 取最后一条 / 锚点 / 剥标记；四条可编辑判据（无后续 turn、无后续 assistant、被新压缩取代、被编辑过仍可编辑）；`apply_edit` 的 5 条拒绝 + 落账形状（replace start==end、compactionId 不变、anchorSeq 正确）+ 改完 `derive_messages` 真的变了 + 删除覆盖闭包仍认这条纪要 |
| 同上（投影段） | `project_ui_history` 吐 `kind:"compaction"`、位置在 anchor、`turn==0`、`scene==""`、被折走的旧纪要不出现、编辑后只吐一条且正文是新的 |
| `tests/test_compaction_edit_rpc.py`（新） | RPC 成功/四类拒绝；`compact_summary` 推送（手动 + 自动）；`_on_step` 转事件；群聊带 `room_id` |
| `tests/test_compaction_ui_contract.py`（新） | C# 源码契约：折叠行默认收起、根节点不挂 Button、面板左锚点半宽、多行 `MultiLineNewline`、发送钮存在、白名单含 `compact_summary`、Presenter 有 `case "compact_summary"` |
| 既有 | `tests/test_ui_history.py` / `test_history_delete.py` / `test_compact_command.py` / `test_compaction_*.py` 必须全绿（投影新增一种 kind 不能改旧条目） |

---

## 6. 实施进度

- [x] P1 Python 纯逻辑（`compaction/summary.py`）+ `_compact` 返回值补正文
- [x] P2 协议（RPC / 推送 / 投影）+ 单测
- [x] P3 C# 折叠行 + 回放
- [x] P4 C# 左侧面板
- [x] P5 联动（锁定时机 / 成功提示 / 群聊与换人边界）
- [x] P6 复核（模拟全流程）+ 文档收尾

每完成一个阶段，除了勾这一行，还要在下面 §7 补一段"做了什么、为什么"。

---

## 7. 实施记录（随阶段追加）

### P1：`compaction/summary.py` + 压缩器返回值（已完成）

**新增 `compaction/summary.py`**（纯逻辑，7 个出口：`is_summary` / `summary_text` / `wrap` /
`normalize_text` / `latest` / `editability` / `apply_edit` / `anchors`）。三个取舍：

1. **不 import 压缩实现**（`compress.py`）：`session/history.py` 只读投影，不该被
   `pruner → llm` 那串依赖牵着走。代价是 `SUMMARY_PLUGIN = "compact"` 与
   `COMPACT_PLUGIN` 是两处字面量 —— 用一条断言钉死同值（`test_plugin_constants_match_producer`）。
2. **`latest()` 扫 surface 而不是 log**：改过多次的纪要只在 surface 留最后一版；
   被后一次压缩折走的旧纪要也不该被当成"当前那条"。这一条同时解决了"取最新"和"取仍生效"
   两个问题，不需要版本号比较。
3. **`editability()` 加"是否落在回合内部"**：见 §2.2 的第 2 条。实现是扫 `log[:seq+1]`
   找最后一个未闭合的 `turn/start`（**不是** `session.has_open_turn()` —— 那只描述"现在"，
   而纪要落在回合中是永久事实，回合后来闭合了也不会变）。

**`compaction/compress.py`**：`_compact()` 的返回值补 `summary`（正文）与 `summarySeq`
（新节点 seq），`_replace()` 改为返回该 seq。理由：推送事件与 RPC 都需要"这次压缩产出了什么"，
让调用方再扫一遍账本等于在外面复制选段知识（而且 `latest()` 可能捞到上一次的旧纪要）。

**验证**：`tests/test_compaction_summary_view.py` 13 条全绿，覆盖
"落账形状 / 上下文真的变了 / 连改三次 / 锁定两条路 / 被取代 / 输入校验 / 删除覆盖闭包仍在"。

### P2：协议（已完成）

- **RPC `edit_compact_summary`**（`ws_channel.handle_request`）：运行时先判 `phase`/`_compacting`，
  再交给 `apply_edit`（账本判据）。预期内拒绝一律 `{ok:false, reason}`（不抛）——
  C# 要拿 reason 原样显示；只有缺 `npc_id` 抛（调用方 bug）。成功后调
  `refresh_context_pressure()`（surface 变了，旧实测值作废）。
- **推送 `compact_summary`**：手动路径在 `handle_compact` 里发（**先于** `compact_result`，
  让折叠行在提示行收尾前就位）；自动路径由 `DialogueAgent._emit_compact_summary` 经
   `on_step(kind="compact")` 抛出，`ws_channel._on_step` 翻译成 wire 事件（agent 不认识事件名）。
- **历史投影**：`project_ui_history` 在 `anchors()` 给出的原始位置吐 `kind:"compaction"`，
  只吐当前生效那版；`turn` 恒 0、`scene` 恒 `""`。**没有**放宽原有两条过滤
  （replace 节点 / plugin 消息照旧不进 UI）。
- **验证**：`tests/test_compaction_edit_rpc.py` 5 条全绿，含真实 WS 的自动压缩端到端
  （`compact_summary` 先于第一个 step、`editable=false`）。

### P1/P2 复核时改掉的一个设计错误（留痕）

第一版 `editability` 只写了"之后没有 turn/start、之后没有 assistant"两条。写完推送事件后
发现：自动压缩的事件是在**回合内、模型回复之前**发出的，那一刻"之后没有 assistant"成立
⇒ 推出去的 `editable=true`，而一秒后模型一回复它就该锁了 —— UI 会亮着一个点了必失败的按钮。
修法就是 §2.2 的第 2 条（落在回合内部即锁定）。这条如果只写测试不改实现，测试会通过，
因为测试是照着实现对写的 —— 是"先跑一遍真实流程"抓出来的。

---



### P3：折叠行 `SummaryGroup`（已完成）

- 落在 `csharp/UI/ChatItemViews.cs`（与 `ThinkGroup` 同文件、同族观感：同字号/同字体/同底色深度）。
- **默认收起**：`body.SetActive(false)`，标题行 `▸ 压缩纪要 · N 字`；点标题展开成 `▾`。
- **根节点不挂 Button**：只有左侧标题那一条挂（`Title`）。「修改」钮是它的**兄弟**节点。
  原因不是洁癖：uGUI 的点击会沿层级冒泡，子按钮点一下会连父按钮的 onClick 一起触发 ——
  表现就是"点修改，面板刚冒出来又被收起"。这条写进了契约测试（`go.AddComponent<Button>()`
  在 `SummaryGroup` 块里不许出现）。
- **锁定是一等状态**：置灰「修改」+ 标题后缀 ` · 已锁定` + 正文尾部补一行原因
  （"本轮对话已经开始…"/"已开启新一轮对话…"）。自动压缩产出的纪要来就是锁定的。
- 两个成员名刻意避开 `SetText` / `Toggle`：本仓的 C# 契约测试按"签名唯一"抠方法体，
  撞名会让 `test_bubble_text_contract` / `test_tool_fold_contract` 直接红（第一版就撞了，
  见「P6 复核时发现并修掉的两处」）。改成 `SetSummaryText` / `ToggleExpanded`。

### P4：左侧编辑面板 `CompactEditPanel`（已完成）

- 新增 `csharp/UI/CompactEditPanel.cs`：静态类、运行时搭树、**一个预制体字节都不动**
  （与 `ToolPanel` 同一条路子）。左锚点 + 上下拉伸，`PanelW = 600`（BG 1200 的一半），
  顶边停在 760（不压顶栏那排窗口级按钮）。
- 文本域**克隆 `BG/Input/ChatInput`**（`ChatWindow.Init` 把 `_r.ChatInput` 传进来）——
  同字体/同底图/同光标色，只改三件：`lineType=MultiLineNewline`、`characterLimit=20000`、
  正文左上对齐 + 纵向溢出可见。取不到模板则回落"从零建"（`MakeInputFromScratch`）。
  克隆时**清掉 onValueChanged/onEndEdit 的旧监听**——那是对话输入框的回调，挂过来会
  "改纪要顺手发一条消息"。
- 三态：`发送` / `发送中…`（禁用防连点）/ 失败红字（面板**不关**，玩家改的字还在）。
  成功才收起（正文已回到折叠行上，框里留旧值只会误导）。
- 一个 API 事实：本工程的 Unity 版本里 `InputField` **没有 `onSubmit`**（只有 onEndEdit /
  onValueChanged），照 TMP 的写法编译期就炸 —— 第一版踩了，已改成"读 `text` + 按钮回调"。

### P5：联动（已完成）

- **传输层**：`WsClient` 事件白名单加 `compact_summary`（漏了 = 帧在传输层被静默丢，
  面板永远不出现，且两端都零报错）。
- **Presenter**：`case "compact_summary"` → `OnCompactSummary`（按 `npc_id` 过滤、
  带 `room_id` 的直接丢）；`SummaryEditClicked` → 开面板；`SummaryEditSubmitted` →
  `edit_compact_summary` RPC（**不碰** `SendPlayerMessage`）。
- **锁定时机**（三层，缺一层都会出现"按钮亮着但点必失败"）：① 玩家发消息时
  `LockSummaryEdits("玩家发消息")`；② `NoteTurn` 发现回合号变了时锁；③ Python 侧
  `editability` 才是权威，前两层只是让按钮状态立刻跟上。
- **收起时机**：关窗、进群聊、进删除模式、`ResetContent`（内容整块重建）四处都调
  `CompactEditPanel.Hide()`；ESC 链里**排在工具面板之前**（玩家在编辑文本时 ESC 的第一
  意图是收面板，不是关对话窗）。
- **回放**：`ReplaceHistory` 新增 `case "compaction"`，字段与直播事件同名同义；
  **不绑 `BindKey`**（它不属于任何回合，删除模式的选中单位里没有它）。

### P6：测试与复核（已完成）

| 套件 | 结果 |
|---|---|
| 新增 Python：`test_compaction_summary_view.py`（13）/ `test_compaction_edit_rpc.py`（5）/ `test_compaction_ui_contract.py`（8） | 26 全绿 |
| 受影响面（压缩/历史/删除/群聊/用量/UI 契约/工具契约） | 199 + 154 全绿 |
| **全量 Python 套件** | **2015 passed**（两个失败是第一版撞名造成的，已修；见「P6 复核时发现并修掉的两处」） |
| **C# 测试工程** | **24 个工程全部通过** |
| C# 主工程编译（Debug） | 0 错误（4 条 CS1668 是环境里的 LIB 路径告警，改动前就有） |

**模拟全流程**（脚本直跑 Python 侧真实函数，输出见下面 7.8）：手动压缩 → 推事件 →
改 → 回放 → 发消息锁定 → 自动压缩事件自带锁定，五步全部符合设计。

### P6 复核时发现并修掉的两处（留痕）

1. **`anchorSeq` 的假值陷阱**：`int(src.get("anchorSeq") or seq)` —— seq 0 是合法值，
   `or` 会把它吞成"自己"。虽然现实里纪要的 seq 不可能为 0（压缩至少要 2 个节点），
   但这是"看着对、边界错"的典型写法，改成 `is None` 判断。
2. **方法名撞车**：`SummaryGroup.SetText` / `Toggle` 与同文件的 `ChatBubble.SetText` /
   `ThinkGroup.Toggle` 重名，直接让两条既有契约测试（按"签名唯一"抠方法体）红。
   改名 `SetSummaryText` / `ToggleExpanded` 而不是放宽那两个测试 —— 它们的唯一性断言
   是有价值的（方法被改名/删掉时会红，而不是在空串上静默通过）。

### 7.8 模拟全流程的输出（10-31 实跑）

```
=== ① 玩家点「压缩」 ===
  事件: compact_summary {'chars': 14, 'editable': True, 'lock_reason': '', 'source': 'manual'}
  事件: compact_result {}
=== ② 玩家展开、点修改、改完发送 ===
  RPC: {'ok': True, 'chars': 36, 'edits': 1}
  屏幕提示：压缩纪要已更新（36 字，下次对话起生效）
=== ③ 关窗再开（历史投影） ===
  条目: {'kind': 'compaction', ..., 'editable': True, 'turn': 0, 'scene': ''}
=== ④ 玩家发一句话（开新回合） ===
  折叠行：可改= False 原因= 已开启新一轮对话，压缩纪要不能再修改
  再点修改发送 → {'ok': False, 'reason': '已开启新一轮对话，压缩纪要不能再修改'}
=== ⑤ 自动压缩 ===
  事件: {'editable': False, 'lock_reason': '本轮对话已经开始，压缩纪要不能再修改', 'source': 'auto'}
  事件顺序（前 3 个）: ['compact_summary', 'step:ctx', 'step:sys']
```

（④的拒绝文案与⑤的事件顺序是本功能两条最容易写错的不变量：**锁定要在发送时就生效**、
**折叠行必须排在本回合第一个 step 之前**。）

### 7.9 真机修复记录（11-10）

#### ① 压缩纪要的输入框只有一小块（用户截图）

- **现象**：面板正常弹出，标题/✕ 都在，但输入框只占顶部一截，下方是面板底色。
- **证据**：`Player.log` 里 `[CompactEditPanel] 编辑面板已搭好` 正常、克隆路径也走了、**零异常**；
  按截图量像素：面板 600 单位宽、输入框横向 16→582 单位（**宽度是对的**）⇒ 问题只在高度。
- **根因**：面板在 `ChatWindow.Init`（宿主创建期）搭，整棵树还是 **inactive**；输入框是从
  `BG/Input/ChatInput` **克隆**来的，它的 RectTransform 尺寸要等激活后的那次布局才结算。
  旧代码只在 `Build` 里设一次锚点，激活后没再贴 ⇒ 停在克隆体自带的那个小尺寸上。
- **修法**：`Show()` 显形后**再贴一次**，且不再依赖父级拉伸 —— 按 BG 的实时尺寸**显式算**
  面板/文本域/输入框三个矩形（`sizeDelta`），再 `ForceRebuildLayoutImmediate`；
  并打一行 `Show 几何: BG=… 面板=… 域=… 输入=…` 供下次一眼定位。

#### ② 设置→提示词→「自我人设」文字高度没展开（用户反馈，同一类问题）

- **根因链**（三处证据）：
  1. 预制件里 `PagePrompt.m_IsActive = 0` —— 提示词页**出厂是 inactive**；
  2. 配置面板一打开（默认停在大模型页）→ `get_config` 回来 → `FillPersonaEditor()` 就把
     自我人设灌进编辑框 → `UpdatePromptLayout()` 此刻量到 **视口宽 = 0**；
  3. 旧实现 `if (width <= 0f) return;` **直接放弃**，而 `SwitchTab(false)` 只 `SetActive`
     **不重量** ⇒ 切到提示词页后 Text 永远停在默认一行 —— 就是"没展开"。
- **修法**（`ConfigPresenter`）：
  - `SwitchTab` 切到提示词页时主动量一次；
  - `UpdatePromptLayout` 宽度量不到时**回落到 InputField 自身 rect**，仍为 0 则挂起
    （`_promptLayoutPending`），由 `Update` 下一帧重试（量到即落地）；
  - 成功落地后打一次 `提示词高度：视口宽=… 生成宽=… 文本高=… 内容高=…`，下次能直接
    分清"没量到"还是"量错了"。

#### 部署

两处修复同一个 DLL（11-10 14:53，md5 `61d1afd6…`，备份
`MOD_Jgmg5L.dll.bak_1110_1248_before_geometry`）。**DLL 只在进程启动时加载** ——
日志显示 12:36 那个进程一直没重启，所以这两处要等下一次重启才生效
（**后续**：用户在 15:10 重启过，14:53 这份已加载；同日又出一处同类问题，见 §7.10）。

### 7.10 真机修复记录（11-10 下午）：正文仍然只有四行

#### ① 现象（用户第二次截图，15:25）

面板、白框都对了（宽度 16→582 单位没问题），但**白框里的正文只有四行**，再往下一直空到发送键。
用户原话：「文字高度还是有问题，不应该是占满整个容器到发送那里吗？」

#### ② 证据（两条，都硬）

1. `Player.log`（15:24，跑的是 14:53 那份 DLL）：

   ```
   [CompactEditPanel] Show 几何: BG=600x760 面板=600x720 域=568x568 输入=-1x-1 输入父=?
   ```

   - `输入=-1x-1 输入父=?` ⇒ 那一刻 `_input` 是**空引用**，输入框那三行尺寸**一个都没写进去**
     （旧代码 `_input.transform as RectTransform` 拿到 null 就静默跳过）。
   - `BG=600x760` 也是**错的**：BG 是 1200×800，600×760 恰好是**面板根自己**的 rect ⇒
     `_root.transform.parent as RectTransform` 同样是 null，走了回落分支，面板因此被算矮 40。

2. 预制体（`backup/UIChatAi.prefab.bak_20260908`，`ChatInput/Text`）：

   ```
   m_AnchorMin/Max: (0,1)        // 左上定点，不是拉伸
   m_SizeDelta: (780, 70)
   m_VerticalOverflow: 0         // = Truncate（砍掉超出的行）
   ```

   70 高 ÷ 19 号字 ≈ **4 行** —— 与截图逐行对得上。

> ⇒ 上一轮把**输入框**撑满没错，但**正文是输入框的子节点**，它有自己一副固定尺寸，
> 而且 Truncate 会把多出来的行直接砍掉。**盒子做大了，字没变大。**

#### ③ 修法（`csharp/UI/CompactEditPanel.cs`）

| 项 | 改成 |
|---|---|
| BG 尺寸 | `Build` 时量一次存 `_bgW/_bgH`（那一刻父级是准的），`Show` 不再问父级 |
| `_input` 为空 | 当场从文本域 `GetComponentsInChildren<InputField>` 重新取，并打一行日志 |
| 正文矩形 | `RelayoutText()`：**顶部拉伸**，高度 = 全文高（滚轮可用时）/ 视口高（否则） |
| 超长正文 | 文本域加 `RectMask2D` 裁边；`CompactEditScrollDriver` 轮询 `Input.mouseScrollDelta` 上下翻 |
| 漂移兜底 | `GuardTextLayout()` 每帧只比对锚点/高度，被 InputField 写回去才重设 |
| 光标 | 打开时放到**开头**（正文常一屏放不下，跳末尾会「看着顶部、改着尾部」） |

为什么滚轮用**轮询**而不是 `IScrollHandler`：本工程的 UGUI 接口在 Il2Cpp 程序集里是**类**，
`class X : MonoBehaviour, IScrollHandler` 直接 CS1721 编不过（全工程也没有自研组件实现过事件
接口）；而对话窗自己的滚动就是读 `Input.mouseScrollDelta`（`ChatWindow.Update`）—— 沿用同一条
已在真机跑过的路。

#### ④ 部署

DLL 11-10 **15:33**，md5 `6994b719780b3a6e8d13c4d18608df22`（1103872 B），备份
`MOD_Jgmg5L.dll.bak_1110_1536_before_textheight`。**DLL 只在进程启动时加载，需要再重启一次。**

#### ⑤ 下次怎么一眼判断（看 `Player.log`）

```
[CompactEditPanel] Show 几何: BG=1200x800 面板=600x760 域=568x608 输入=564x604
                                正文=560x<全文高+8> 全文高=<...> 字数=<...> 滚动=开
```

- `BG=1200x800`、`域=568x608` ⇒ BG 尺寸对了（比上一版高 40）；
- `输入=` 不再是 `-1x-1` ⇒ `_input` 有着落；
- `正文=` 的高度 **≫** `域=` 的高度、`滚动=开` ⇒ 正文已按全文铺开，超出部分由遮罩裁掉、滚轮翻；
- 若出现 `滚动=关`，说明遮罩/滚轮没搭上，正文会退回落在一屏内截断（日志里有原因）。

### 已知限制（诚实记录，不是待办）

**群聊回合里发生的自动压缩，在 1:1 窗重开时看不到那一行折叠。**
原因：`project_ui_history` 的场合过滤是**按回合**做的（`closed = [x for x in closed
if x["scene"] == scene]`），而该纪要的账本位置落在那个群聊回合里 —— 回合被整段滤掉，
条目跟着没了。数据/记忆本身不受影响（它仍在 surface 里、模型照常读到），只是 1:1 窗的
回放少了这一行；下一次压缩产出新纪要时会重新出现。

**为什么不顺手修**：把投影改成"按条目过滤 + 截断仍按回合"会动到删除/截断的既有语义
（这两个是已经被真机事故打磨过的路径），收益只是"某个成员的某次群聊自动压缩能在 1:1 窗
看到一行"，不值得在本功能里冒险。已在 `tests/test_compaction_summary_view.py::`
`test_projection_place_and_scene_rules` 里把这个事实**钉住**（改成按条目过滤时那条断言会
主动红，提醒同步改文档）。

## 8. 真机验收：逐步操作 + 预期 + 排查

### 8.0 部署现状（**11-10 15:35 最近一次核对**）

> exe 那一行是 10-31 上午那一轮部署带进去的（我只做了核对）；DLL 在 11-10 由我改过三次
> （12:48 输入框几何、14:53 提示词高度、15:33 正文高度）。下表是**当前落在盘上的那一份**。

| 产物 | 路径 | 大小 | 时间 | md5 | 与仓库对照 |
|---|---|---|---|---|---|
| C# DLL | `…/Mod_Jgmg5L/ModCode/dll/MOD_Jgmg5L.dll` | 1103872 | **11-10 15:33** | `6994b719…` | == `csharp/bin/Release/MOD_Jgmg5L.dll`（含 §7.9 / §7.10 三处真机修复） |
| Python exe | `…/Mod_Jgmg5L/ModAssets/AgentLoopServer.exe` | 24162304 | 10-31 10:16 | `4060d5ac…` | == `F:\.agent_loop_build\nuitka\AgentLoopServer.exe` |

**DLL 里能找到本功能的记号**（UTF-16 字符串堆计数）：`compact_summary` ×2、
`edit_compact_summary` ×1、`CompactEditPanel` ×3、`SummaryGroup` ×1、`压缩纪要` ×11、
`已锁定` ×1。**exe 的 Nuitka 构建树里有 `module.agent_loop.compaction.summary.c`**（10:12 编译）。

⇒ 两端都已包含本功能。**唯一的硬前提：DLL 只在进程启动时加载 —— 游戏若一直开着，必须重启一次。**

### 8.1 八步验收（每步：操作 → 预期 → 不符合时先看哪里）

**① 手动压缩 → 折叠行出现（默认收起）**
- 操作：开一个有 10+ 轮对话的 NPC → 点标题栏「压缩」（或输入 `/compact`）。
- 预期：先见「正在压缩对话历史…（已 Ns）」，完成后**末尾**出现一行
  `▸ 压缩纪要 · N 字`（淡蓝底、**收起**）；提示行变成「压缩完成：上下文约 A → B tokens…」。
- 排查：C# 日志 `[ChatPresenter] 压缩纪要上屏：N 字，可改=True，来源=manual`；
  没有这行就是事件没到（看 Python 是否发出 `compact_summary`）。

**② 展开读全文**
- 操作：点标题行。
- 预期：变 `▾ 压缩纪要 · N 字`，正文是七个分节（自身/与玩家/同席/经历/约定/心境/认知边界），
  长文能滚、不被截断。

**③ 修改 → 传回 Python → 有提示**
- 操作：展开后点右上「修改」→ 左侧半屏面板弹出（标题「修改压缩纪要」，输入框预填全文，
  光标在末尾）→ 改几个字 → 点「发送」。
- 预期：按钮变「发送中…」→ 面板收起 → 聊天里多一行系统提示
  「压缩纪要已更新（N 字，下次对话起生效）」→ 再展开折叠行：正文与字数都变了。
- 排查：`[ChatPresenter] 打开纪要编辑面板：…` / `压缩纪要已传回 Python：N 字`；
  Python 日志 `压缩纪要已修改：<cid> seq a→b，x→y 字（第 n 次）`。

**④ 存档才落账**
- 操作 A：改完 → **不存档**直接退游戏 → 重进同一存档。
- 预期 A：正文回到改之前（内存为真、不存档等于没发生）。
- 操作 B：改完 → 游戏内存档 → 重进。
- 预期 B：正文是改后的。

**⑤ 一旦开新回合就锁定**
- 操作：压缩完（还没发消息）→ 发一句话。
- 预期：折叠行标题变 `… · 已锁定`，「修改」置灰；展开后正文底部多一行
  「（已开启新一轮对话，压缩纪要不能再修改）」。
- 加测：面板开着时硬发 → Python 回同一句原因（不落账）。

**⑥ 自动压缩（回合内）**
- 操作：继续聊到上下文超阈值（或把 `compaction.ctx_window` 调小逼它触发）。
- 预期：回合开始时折叠行出现在**本回合内容之前**，且**一出现就是锁定的**
  （原因「本轮对话已经开始，压缩纪要不能再修改」）。

**⑦ 重开窗回放**
- 操作：关窗再开（或换 NPC 再换回）。
- 预期：折叠行还在、位置与关闭前一致；可改状态与关闭前一致
  （手动压缩那条仍亮；期间发过消息则已锁）。

**⑧ 输入法与热键**
- 操作：在左侧面板里打中文 / 按 WASD / 按回车 / 按 ESC。
- 预期：WASD 不让角色走路；回车**换行**不发送；ESC **先收面板**（再按一次才关对话窗）；
  点「发送」才真正提交。

> **群聊相关（已知盲区，不算失败）**：群聊回合里触发的自动压缩，1:1 窗当场不会冒出来
> （事件带 `room_id` 被丢）；之后打开 1:1 窗也**可能看不到那一行**（投影按回合过滤，
> 该回合属于群聊场合）。记忆本身不受影响 —— 见 §7「已知限制」。

### 8.2 一行日志定位表（出问题时先 grep 这些）

| 现象 | 先找这行 | 说明 |
|---|---|---|
| 压缩后没有折叠行 | `[ChatPresenter] 压缩纪要上屏：` | 没有 = 事件没到（Python 侧/白名单）；有 = 渲染失败，再看 `[ChatWindow] 压缩纪要行渲染失败` |
| 点「修改」没反应 | `[ChatPresenter] 打开纪要编辑面板：` | 没有 = 按钮没回调；有 = 面板树不在（`[CompactEditPanel] Show 忽略：面板树不存在`） |
| 点「发送」没反应 | `[ChatPresenter] 压缩纪要已传回 Python：` | 没有就找 `压缩纪要修改失败：<原因>`（Python 的拒绝原因会原样显示在面板红字上） |
| 面板没出现/位置怪 | `[CompactEditPanel] 编辑面板已搭好 宽=600` | 没有 = `Build` 失败（日志里有原因）；宽/位置不对 = 版式常量被动过 |
| 行一直不锁定 | （不该发生） | Python 是权威：`editability` 的"落在回合内/之后有 turn/start"两条 |
