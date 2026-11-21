# 群聊（Room 编排层）—— 实现现状

> **一句话**：一个 NPC 仍然是一个独立 agent（各有各的 session、各有各的 LLM 回合），
> Room 只做**调度与记账**：谁先说、说什么投给谁、群窗显示什么。
> `Room.log` 是群的流水，但**模型从不读它** —— 每个成员读的是自己 session 里被投递进去的那几条。
>
> 设计与施工全过程（2965 行）在 [`docs/archive/2026-09-17-群聊编排/设计定稿.md`](../archive/2026-09-17-群聊编排/设计定稿.md)；
> 三份前置计划在同一个目录。**日常开发不需要读它们**，本文是现状。

---

## 0. 它是什么、不是什么

**Room = 一个「交谈身份」+ 一个「接力调度器」。** 它持有这个群是谁、有谁、说过什么，
并按顺序把发言投递给各个 NPC 的 agent。

三条否定（都是踩过的）：

| ✘ 不是 | 为什么 |
|---|---|
| **不是地点** | 坐标不唯一（同一格可能有好几场聚谈）；地名会撞车。「同一酒楼里先跟甲单聊、再把乙叫过来」本来就是**两个**场合 |
| **不是另一个 agent** | 不跑 LLM、没有 session、没有回合。只做调度与记账 |
| **不是记忆载体** | 模型**从不读** `Room.log`。NPC 的记忆在各自的 `session` 里（见 §6 不变量 I2） |

**为什么必须有个 Room**——三个消费者没人能替代它：

| 消费者 | 它要的 | 只有 Room 能给的 |
|---|---|---|
| 群聊窗（UI 回放） | 「这个群聊过什么」 | 玩家自己没有 session；成员的 session 可能被压缩、被删、或是后加入的 |
| 投递（增量） | 「谁还没听到哪条」 | 需要一个**跨成员共享的发言序号** |
| 调度 | 「这轮该谁说话」 | 轮转指针 + 本轮已发言计数，是群级状态 |

> 逐条论证与踩坑现场：`archive/2026-09-17-群聊编排/设计定稿.md` §一、§二、§4.1。
> **日常不需要读**——本文给的是结论，具体实现以代码为准（`room/room.py`、`room/room_flow.py`）。

---

## 1. 形态：路线 C

当时的三个候选（[设计草案](../archive/2026-09-17-群聊编排/设计草案.md) §2）：

| 路线 | 做法 | 结论 |
|---|---|---|
| A | 一个房间一个 agent，NPC 是它的"角色" | 否 —— 人设、记忆、工具身份全部要重做 |
| B | 一 NPC 一 agent + 全员共享完整 transcript | 否 —— 谁都看得见别人私有查询的结果 |
| **C** | **一 NPC 一 agent + 增量投递 + 私域工具** | **采纳** |

关键分工：

| 谁 | 负责 | 不是什么 |
|---|---|---|
| `Room`（`room/room.py`） | 纯数据：成员、流水、墓标。零 IO、零 LLM | 不跑回合、不持 agent、不持游标 |
| `RoomFlow`（`room/room_flow.py`） | 编排：投递、排轮、跑轮、广播、进出群、群窗删除 | 不实现对话 |
| `DialogueAgent`（`agent/dialogue_agent.py`） | 每个 NPC 自己的回合 | 不知道"群里还有谁在等" |

## 2. 增量投递：走 inbox 的 `next-step`

```python
agent.send(text, target="next-step", wake=False,
           source={"kind": "peer", "speaker": "李四", "uid": "g3#7"})
```

- `next-step` = 落进账本、**不唤醒**；什么时候跑回合由 Room 决定。
- 玩家在群里的发言同样走这条（`kind="room"`, `speaker="player"`）。
- 催话（`next-turn`）是另一条：只有它才触发一个回合。

**为什么不能在回合外直接 `session.append(..., surfaceOp="append")`**：实测三个破口 ——
投影归属与删除跨度不一致（假删除）、首个 turn 之前的投递被静默丢弃、顺序错位。
而且**跑模拟看不出来**，只有真账本才会暴露。详见
[`plan-group-history.md`](../archive/2026-09-17-群聊编排/计划-下游消费方.md) §2。

**增量规则一句话**：把"比我的游标新、且**不是我说的**"的发言按序投给我。
自己的话已经在 assistant 历史里，所以游标可以跳着走。

**没被点名的人**：增量留在筐里，账本不涨（不建回合、不调 LLM）。

## 3. 游标与落盘

```
<storage_root>/worlds/<world_id>/
├── <npc_id>.jsonl          ← 每个 NPC 的 session（含 room/cursor 事件）
└── rooms/
    ├── index.json          ← 名录：有哪些群（C# 列群只读这一个文件）
    └── <room_id>.jsonl      ← 首行 header（Room 元数据）+ 每行一条发言
```

**游标写进 session，不写进 Room**。理由：记忆在 session、游标在 Room ⇒ 两次落盘之间出错会
"游标说听过了、记忆里没有" ⇒ 那几条发言**永久丢失**。所以顺序是
**① 先投递 `next-step` → ② 后推进 `room/cursor`**，同一个 log、同一次 flush。

**投递起点**：有游标用游标；没游标退回 `Room.joined`（入群位置），**不是** `log` 末尾 ——
初稿写成"末尾"，后果是一个人**都听不到**玩家说话。

## 4. 排轮

`RoomFlow.plan_round`：全员轮流 + 可弃权 + 每轮上限 `k`（默认 3）+ 轮转指针。

- 轮转指针指向"本轮最后一个实际发言者的下一位"，只在内存（不落盘）。
- 玩家插话（`room_message`）只 append 到 `Room.log`，**不打断本轮**，本轮跑完再开新一轮。
- 一轮里成员**串行**跑（持 `room_lock`）⇒ 同一时刻只有一个人说话 ⇒ C# 单槽流式状态可以原样保留。
- 玩家点名（`speak_first`）协议通了，**UI 没做**（点头像点名仍是待办）。

**弃权**：回 `【沉默】` 或空 ⇒ 本回合弃权。精确匹配（外加几个带标点的写法），不做模糊匹配。
弃权**不写流水、不广播气泡，但游标照常推进**（弃权 ≠ 没听见）。

`k` 已接配置热生效：`room.max_speakers_per_round` → `RoomFlow.set_max_speakers_per_round`。

## 5. 协议

### C# → Python（request）

| 名 | 作用 |
|---|---|
| `open_room` / `create_room` | 开群并开窗 / **只建群不开窗**（两者 09-17 拆开） |
| `close_room` | 关窗：全员 `leave()`、成员 scene 复位，**不销毁群** |
| `list_rooms` | 群列表（通讯录群聊页数据源） |
| `get_room_history` | 增量拉取 / 上拉加载 |
| `add_room_members` / `kick_room_member` | 拉人 / 踢人（新人看不到入群前的话） |
| `dissolve_room` | 解散群聊（删容器 + 名录 + 公共账本，**不删成员记忆**） |
| `preview_room_delete` / `room_delete` | 群窗删一条：预览（每成员报告）+ 执行 |
| `get_context` | 取 L1 raw（带 `roster` / `roster_ids` / `npc_unit_id`） |

### C# → Python（event）

| 名 | 作用 |
|---|---|
| `room_message{room_id,text,speak_first}` | 玩家在群里发言，触发一轮；**立即返回**，接力后台跑 |
| `room_skip{room_id}` | 玩家按「跳过」：这一轮他不置一词，NPC 按轮转自己接下去 |

**归属规则**：「发生了一件事」一律 event，「问一次答一次」才 request。
走 request 会堵死接收循环（连玩家插话都进不来）—— 初稿把 `room_message` 列在 RPC 下是错的。

### Python → C#（event）

| 名 | 作用 |
|---|---|
| `room_say{speaker,text,seq,uid,speaker_id,narration}` | 一条发言。`speaker≠player` ⇒ 定稿/替换气泡；`=player` ⇒ 回声，按 uid 去重不再画 |
| `room_state{phase=speak}` | 点名广播（不预开气泡） |
| `room_state{phase=silent}` | 该成员弃权：撤掉可能存在的泡，**不复位忙碌态** |
| `room_state{phase=error}` | 该成员出错：撤/定稿气泡 + 系统提示，**不复位忙碌态** |
| **`room_state{phase=end}`** | **回合终帧**：复位忙碌态（唯一依据） |
| `text_delta` / `step` / `stats_update` | 既有帧，群聊里必须带 `room_id`，否则被群窗准入判据整帧丢弃 |

**终帧为什么必须有**：弃权不写流水、不广播 ⇒ 一轮里全员回 `【沉默】` 时 C# **一条 `room_say` 都收不到**
⇒ 没有"这轮结束"信号 ⇒ 群窗永远卡在"正在输入"。所以它放在 `finally` 里**无条件发**，
错误帧也必须带 `phase="end"`（否则 `switch(phase)` 两个分支都不命中、忙碌态不复位）。

## 6. 四条不变量

| # | 不变量 | 违反的后果 |
|---|---|---|
| I1 | 每条 `user/message` 都住在某个 `turn/start..turn/end` 里 | 投影归属与删除跨度错位 ⇒ 假删除 |
| I2 | 模型只读自己的 session；`Room.log` 永不进 prompt | 某个 NPC 会知道它没听过的事 |
| I3 | 记忆与游标在同一个文件、同一次 flush | 游标超前 ⇒ 那几条发言永久丢失 |
| I4 | 同席发言落账存**原文**，归属走 `source` | 双重前缀「李四：李四：…」 |

## 7. L1 段的多人化

七个决策（原文在 [`plan-l1-multi-npc.md`](../archive/2026-09-17-群聊编排/计划-L1多人化.md) §三）：

| # | 决策 | 一句话 |
|---|---|---|
| D1 | 段清单**推导**，不维护 | 每步从真实数据算 `segs`，与上一步键集求差 ⇒ 离场 = `previous - current`，自动覆盖关窗/死亡/解析失败 |
| D2 | `present` 段**恒非空** | 单聊也有专属文案 ⇒ 进出房间两个方向都是内容变化，不需要给 `other:*` 写撤回 |
| D3 | `other:*` **一人一段** | 一整段任一人变即整段重发（O(N²)）；一人一段是 O(N) |
| D4 | `other:*` 只给**可观测面** | 姓名/性别/种族/境界/宗门/容貌声名/是否同格/好感；不给气运、经历、悟性裸值 |
| D5 | 统一命名走**投影** | 发消息时才加「说话人：」，账本保持原文；否则 UI 显示 `玩家：你好`、压缩材料双重前缀 |
| D6 | 没有 `raw.others` 就**退化单聊** | C# 没改时行为与今天一致 |
| D7 | 段顺序固定 | `time → self → player → present → other:* → recent` |

## 8. 群窗删除一条消息

三处落点**同时**发生（同一次 `flush_all()`）：

1. **群记录**：`Room.deleted_seqs` 加墓标（`Room.log` 仍只增不改，`delta_for` / 投影跳过）；
2. **各成员 session**：fan-out 落 `history/delete {reason:"room_delete"}`；
3. 群窗重开时 `project_room_history` 一样跳过。

**用 `uid`（`g3#7`）定位，不用 turn 号** —— turn 号是会话局部的
（同一条发言在林婉清账本里是 turn 2、在李四账本里是 turn 1）。

**群里不提供手动压缩**：压缩的单位是**某个 NPC 的 session**，`Room.log` 永不压缩。
群窗里的 `/compact` 由 C# 本地拦掉，Python 侧只当没这条命令。

## 9. 现状与缺口

**已实现并跑过**

- 单聊 ⇄ 群聊共用一个 session（进群/出群不切历史）；增量投递、排轮、弃权、跳过、催话；
- 通讯录群聊页：建群 / 解散 / 拉人 / 踢人 / 逐行立绘 / 删一条 / 上拉加载；
- 群窗直播（气泡 + 行动记录行）与重开窗回放；弃权不留空气泡；玩家回声不双画。

**未做 / 未验证**

| 项 | 说明 |
|---|---|
| 群级未读红点 | 未做。`ContactPresenter.RebuildRoomRows` 里 `dot.SetActive(false)` 且有注释标明是 P3 第 5 步 |
| 头条像点名（`speak_first`） | 协议与 Python 侧就绪，**缺 UI** |
| `rename_room` | 不存在 |
| 同名两人同群 | 刻意不做（成员按名去重，一名一 id） |
| `room_narrate` 覆盖 | 只落了战斗类（`spar`/`attack`）一条模板，其余动作类仍是待办 |
| 群聊规则段（`prompts/sections/`） | 未做，目前靠 nudge 正文顶上 |
| 群窗删除的实机首测 | 逻辑与契约都在，真机没跑过 |
| 立绘预热（不在场 NPC） | 离线无法验证，失败面 = 没有立绘、不崩 |
| 房间内单聊 | 技术上允许（`handle_message` 不取 `room_lock`），产品上是否禁入口待定 |

## 10. 相关代码

| 文件 | 职责 |
|---|---|
| `room/room.py` | `Room` / `Utterance` / `new_room_id`（纯数据，无 IO） |
| `room/room_flow.py` | `RoomFlow`：投递、`plan_round`、`_run_round`、提案闸门、群窗删除 |
| `room/room_history.py` | `project_room_history`（重开窗回放投影） |
| `storage/persistence.py` | `rooms_root_for` / `room_path_for` / `save_room` / `load_room` / 名录 |
| `transport/ws_channel.py` | 4 个 RPC + `room_message`/`room_skip` + `handle_room_message`/`handle_room_skip` |
| `tools/room_narrate.py` | 动作落定后的公开叙述行（`narration=True`） |
| `csharp/UI/ChatFrameQueue.cs` | 群聊帧排队（关窗→重开不丢行） |
| `csharp/UI/ChatPresenter.cs` / `ChatWindow.cs` | 群窗准入、`room_say`/`room_state` 翻译、按钮显隐 |
| `csharp/UI/ContactPresenter.cs` | 通讯录群聊页 |
| `tests/test_room.py` 81 / `test_room_member_identity.py` 17 / `test_room_speakers_setting.py` 11 / `test_group_history.py` 18 / `test_l1_multi_npc.py` 30 | 共 157 项（R1–R12 / G1–G17 / T1–T12 与配套契约） |
