# 上下文投影：实现细节与复现

> 查表用。**结论与模型**见 [`../features/context-projection.md`](../features/context-projection.md)。
> 这里保留逐段链路、基线实例、前缀缓存论证、压缩护栏与复现方法。
> **实现以代码为准**：`session/session.py`、`compaction/`。

## 4. 逐段差分的完整链路（一轮之内）

```
_turn()                                     ← 新一轮开始
 ├─ session.append("turn/start")            记账，不进上下文
 ├─ self._turn_ctx = None                    ★清 L1 缓存 → 本轮首步必然重新取数
 ├─ self._turn_ctx_dirty = False
 └─ compactor.maybe(...)                    压缩：可能 emit surfaceOp=replace
    │
    └─ while True:  ← 步循环（工具循环）
       └─ _pre_step(target, turn, step)
          ├─ 1. inbox.claim()               领信（先吃 next-step 全量，再吃 next-turn 一条）
          ├─ 2. if _turn_ctx is None or dirty:
          │        raw = bridge.get_context(id)      ★每轮一次
          │        _turn_ctx = raw
          │     segs = format_l1_context(id, raw)    四段成文
          │     for 段 in _CTX_SEGMENTS:             ── 差分 A（写屉）
          │         if 新文本 != _last_l1[段]: remove_context + context
          ├─ 3. assembly = system_prompt.assemble(scope=id)   ← 每步现拼
          ├─ 4. for 段 in _CTX_SEGMENTS:             ── 差分 B（发消息）
          │         desired = render_context_segment(段, txt)
          │         if desired != _retained_ctx[段]:
          │             append user/message(id=ctx-{段}-{轮}-{步})
          └─ return {messages: 领到的信 + 变化的段, assembly}
       └─ _step(assembly)
          ├─ sections 逐段比 _last_sys_parts      ── 差分 C
          ├─ header_hash 比 _last_header_hash     ── 差分 D
          ├─ messages = _apply_speaker_labels(_inline_image_refs(derive_messages()))
          │     图片：账本里是 ref，这里投影成 data URI（浅拷贝；10-18 起图片进上下文）
          └─ llm.generate(system, messages, tools)
```

**关键：`assembly` 每步都重新组装**，但**只有变化的部分才落账 / 才进消息流**。
system 本体是每步新拼的字符串，通过 `generate(system=...)` 直接传，**不入账本**；
账本里那份 `request/header` 只是审计 + 差分用。

### 恢复基线（重进游戏 / 重开历史会话）

三处基线都要能从账本回填，否则"未变也重发"：

| 基线 | 回填来源 | 位置 |
|---|---|---|
| `_retained_ctx[段]` + `_last_l1[段]` | 倒序扫账面里 `plugin == "@python-harness/system-prompt"` 的 user/message，按前缀反推段名，**每段只取最新一条** | `:144`、`:162` |
| `_last_sys_parts` | 倒序扫最后一条 `dev/sys-snapshot` | `:120` |

两处注释都记着修过的 bug：

* `:140` —— 原先倒序扫到每条都回填、**被最旧覆盖**，导致重进游戏首轮 diff 基准对不上 → 未变也重发。
* `:142`、`:179` —— 还要把恢复的段**回填 SystemPrompt 容器**，否则容器为空 → `txt` 空 →
  被判成 `remove` → UI 误显示"已移除"。
* `:299`、`:344` —— 空段存 `""` 而不是 `None`：否则 `"" != None` 永远不等 → 永远 emit remove。

---

## 5. 「一个字变了会怎样」——精确回答

**不是"全部重载"。** 实际行为：

```
第 N 轮：四段全发（首次无基线）        → 4 条 ctx 消息
第 N+1 轮：只有"玩家"段变了
          → 只发 1 条新的玩家段
          → 其余三段一个字都不发（它们的消息留在原处不动）
```

**但段是原子单位**：段内改一个字 → **整段重发**。
比如"近况"里有 8 条日志，第 8 条变了，前 7 条虽没变也跟着整段重发。
这是**刻意的取舍**（`:115` 注释：`全文比较太粗`），更细的粒度（按条）没做。

**所以更准的说法是**：
> 逐段比较、只发变化段；段内任何改动都会导致**那一段**整体重发，其余段纹丝不动。

`sys`（差分 C）和 `header`（差分 D）同理，且更粗 —— C 按 section、D 按整份 header 的哈希。

---


---

## 5.5 三个基线到底是什么（实例）

三个都是"上次发过什么"的**记忆**，用来避免重复发送。它们**各管一段不同的流水**：

| 基线 | 类型 | 一句话 | 谁用它 |
|---|---|---|---|
| `_last_l1[段]` | `Dict[段, str]` | 上次**写进屉（SystemPrompt 容器）**的段**正文** | 差分 A（`_preStep`） |
| `_retained_ctx[段]` | `Dict[段, str]` | 上次**发给 LLM** 的段文本，**带包装前缀** | 差分 B（`_preStep`） |
| `_last_sys_parts[section]` | `Dict[section, str]` | 上次的 **section 正文**（system prompt 的组成段） | 差分 C（`_step`） |

### 实例（真机桩数据，跑完第 1 轮后）

```
── _last_l1  （段 → 上次写进屉的正文）
   time    = ''
   self    = '你是林婉清。栖居于永宁州·白帝城，隶属化神殿一脉，如今已是金丹修为。心境平和…'
   player  = '此刻与你言语相对的是韩立，筑基修为，一介散修游历于八荒，正与你同处一地…'
   recent  = '1. 上月与韩立结为道侣'

── _retained_ctx  （段 → 上次发给 LLM 的带包装文本）
   time    = ''
   self    = 'Current runtime context —— 自身：你是林婉清。栖居于永宁州·白帝城…'
   player  = 'Current runtime context —— 玩家：此刻与你言语相对的是韩立，筑基修为…'
   recent  = 'Current runtime context —— 近况：1. 上月与韩立结为道侣'

── _last_sys_parts  （section 名 → 上次的正文）
   harness:identity     =  478 字  '你是鬼谷八荒世界的NPC对话Agent，运行于Python harn…'
   world:basis          =  705 字  '[世界背景设定]\r\n鬼谷八荒，一方以山海经传说为底蕴…'
   world:persona_rules  =  927 字  '[资质与评品]\r\n修士以灵根（火/水/雷/风/土/木）…'
   deployment:persona   =  521 字  '你是林婉清，鬼谷八荒中的一名修士。\n\n[你的来处]…'
   tool:usage           =  747 字  '[工具使用规则]\n参数名、取值范围与「何时必传」以工具定义为准…'
```

**第 2 轮只改了玩家段（`筑基` → `筑基2层`）之后**：

```
_last_l1     : 只有 player 变（'筑基修为' → '筑基2层修为'），self/recent 一字未动
_retained_ctx: 只有 player 变（同理），其余三个基线原样
_last_sys_parts: **五个 section 全部未变**（长度与内容都一样）
```

于是第 2 轮实际发出去的东西是：

```
['第1问', 'ctx·自身', 'ctx·玩家', 'ctx·近况', '好的', '第2问', 'ctx·玩家(新)']
                                              ↑ 只有这一条是新的
```

### 为什么要有两组 L1 基线而不是一组

因为它们量的是**两个不同阶段**的同一个值：

```
format_l1_context() 产出正文 ──[差分A]──> 写进屉 ──assemble()──> render_context_segment()
                                                                      │
                                                              [差分B] ──> 发成 user 消息
```

A 的基线存**裸正文**（`'此刻与你言语相对的是韩立…'`），
B 的基线存**带包装的文本**（`'Current runtime context —— 玩家：此刻与你…'`）。

分两套的好处：写屉失败/容器被清，不会污染"发没发过"的判断；反之亦然。
代价是要维护两处一致性 —— 恢复路径（`:144`）就是把两套一起回填的。

---

## 6. 为什么只 append、不 replace —— 前缀缓存（★作者 09-14 指出，我一开始判错了★）

### 我原来的判断（错）

我看到"变化的段只 append，旧段永不清理"，把它记成缺陷，并建议改成
`surfaceOp: {op:"replace", start:旧seq, end:旧seq}` 原地替换。

**这个建议会砸掉前缀缓存。** 见下面的实测。

### 现状：严格前缀性质（实测）

抓每轮真正发给 LLM 的消息列表，逐条比对：

| 轮次 | 消息数 | 是下一轮的严格前缀？ | 可复用前缀 |
|---|---|---|---|
| 1→2 | 4→7 | ✅ True | 4/4 = 100% |
| 2→3 | 7→10 | ✅ True | 7/7 = 100% |
| 3→4 | 10→13 | ✅ True | 10/10 = 100% |
| 4→5 | 13→16 | ✅ True | 13/13 = 100% |
| 5→6 | 16→19 | ✅ True | 16/16 = 100% |
| | | **合计** | **50/50 = 100.0%** |

**第 N 轮的整份请求，逐字节是第 N+1 轮请求的前缀。** 这是 provider 侧前缀缓存的
最优形态 —— 每次只有尾部新增的那几条要重新计算。

### 换成原地 replace 会怎样（同场景模拟）

| 轮次 | 可复用前缀 |
|---|---|
| 1→2 | 2/4 = 50.0% |
| 2→3 | 2/6 = 33.3% |
| 3→4 | 2/8 = 25.0% |
| 4→5 | 2/10 = 20.0% |
| 5→6 | 2/12 = 16.7% |
| **合计** | **10/40 = 25.0%** |

**因为替换点在对话中部，它后面的一切（包括所有后续对话）全部失效。**
而且**越聊越糟**：轮次越靠后，被作废的后缀越长。

### 结论：陈旧段是**前缀性质的代价**，不是 bug

```
append-only  ──> 前缀 100% 可比  ──> 缓存命中最大  ──> 代价：旧版本留在上下文里
replace      ──> 前缀 从改动点断裂 ──> 每轮重算大半  ──> 收益：上下文干净
```

**两者不可兼得** —— 旧段是前缀的一部分，删掉它就等于改前缀。
唯一能"删"的时机是压缩（一次**蓄意**的前缀断裂，低频、可控）。

语义上也说得通：这是**状态更新**的常见形态，模型按"越靠后越新"读，
最新的那条在最后，天然覆盖前面的。这与"人看到状态变了"是同一种读法。

> **我曾建议的"修复"是错的，已作废。** 保留 append-only。
> 代价可量化：每有一个段发生变化，永久多付约 94 字符（实测），直到压缩把它折进纪要。
> 若想省这笔钱，唯一不破前缀的办法是**让段少变**（差分 A/B 已经在做的事），而不是删旧段。

### 唯一可以考虑的改进（不动前缀，只改措辞）

多条快照并存时，模型未必确定"哪条为准"。可以把单段渲染从
`Current runtime context —— 玩家：…`
改成
`Current runtime context（最新；覆盖此前同名段）—— 玩家：…`

**代价**：`_restore_segment_from()`（`:162`）按 `"Current runtime context —— "` 前缀反推段名，
改措辞必须同步改它，否则**老账本恢复失败**。要么兼容两种前缀，要么接受一次迁移。
未实施 —— 需要作者拍板。

---

## 7. 压缩这条路上没人读 `source.kind`（★已修 2026-09-14★）

### 问题的精确形状

L1 段是**伪装成 user 的 plugin 消息**：

```python
# dialogue_agent.py:325
context_msgs.append({
    "role": "user",                                   # ← 伪装成 user（模型才看得到）
    "content": [{"type": "text", "text": desired}],
    "id": f"ctx-{name}-{turn}-{step}",
    "source": {"kind": "plugin", "plugin": "@python-harness/system-prompt"},   # ← 真实身份
})
```

仓库里**三条路**都遇到过这类消息，只有两条认得出它：

| 路径 | 读 `source.kind`？ | 代码 | 结果 |
|---|---|---|---|
| UI 历史投影 | ✅ | `history.py:82` `if kind == "plugin": continue` | L1 不进聊天窗 |
| 删回合 | ✅ | `history_ops.py:167` `is_plugin_user = t=="user/message" and src.get("kind")=="plugin"` | L1 **绝对豁免**（`:18`） |
| **压缩** | ❌ | `compaction/compress.py` **全文没有一处读它** | **L1 被当对话材料 + 被区间折走** |

`compress.py` 里唯一的 `"plugin"` 在第 359 行 —— 那是**写**（给纪要节点自己打标记），不是读：

```python
# compaction/compress.py:359
"source": {"kind": "plugin", "plugin": COMPACT_PLUGIN, "compactionId": cid},
```

而它选段的范围是：

```python
# compaction/compress.py:147,181
def _find_cut(self, session, msgs):
    nodes = session.surface.nodes        # ★ 含 L1 段（它们也是 surface 节点）
    ...
    start_seq, end_seq = nodes[0], nodes[cut_idx - 1]
```

**"伪装成 user" 恰恰是它被卷进去的原因** —— 范围在 surface 上连续取，
材料渲染读的是 `role`，两处都不看 `source.kind`。

### 实测证据（两条）

**证据一：L1 段被区间折走**

`ctx_window=1200` / `threshold_ratio=0.05` / ctx 段落在 seq 11/12/13：

| 轮次 | 压缩日志 | surface 里的 ctx 段 |
|---|---|---|
| 1 | （未压缩） | `[11, 12, 13]` |
| 2 | `压缩选段：seq 0..17` | 逐步减少 → 最终 `[]` |

**证据二（更硬）：L1 原文出现在摘要 LLM 收到的【对话材料】里**

抓 `_summarize` 实际发出去的那条 user 消息：

```
<transcript>
[既往纪要] <compacted-summary>
  ## 角色
- 林婉清
</compacted-summary>
林婉清：林婉清回1炼气期吐纳闭关一口真气上九霄
玩家：第一问
玩家：Current runtime context —— 自身：你是林婉清。栖居于永宁州，隶属化神殿一脉，如今已是金丹修为。一身战力约8900。
玩家：Current runtime context —— 玩家：此刻与你言语相对的是韩立，筑基修为，一介散修游历于八荒，正与你同处一地…
玩家：Current runtime context —— 近况：1. 今日刚与韩立共游白帝城
林婉清：[回显] 第一问
</transcript>
```

所以压缩**既吃掉它、又读过它**。"压缩只筛选 message"是设计意图（`compress.py:105` 注释），
但实现没有按意图筛。

### 连带的第二个问题：L1 被张冠李戴成"玩家说的"

```python
# compaction/compress.py:315
speaker = player_name if msg.get("role") == "user" else npc_name
```

L1 段的 `role` 是 `"user"` → 渲染成 `玩家：Current runtime context —— 自身：你是林婉清…`。

于是**摘要里会出现一行"玩家宣称自己是林婉清、金丹修为"**。这比"段被折走"更糟：
段被折走还能靠"L1 变了就重发"自愈，而**被写歪的纪要会一直留在上下文里**。

### 修复（2026-09-14 已实施，净改动 ≈ 20 行）

**① `_render_transcript` 入口拦 plugin**（`compaction/compress.py`）

```python
for msg in self._surface_messages(session):
    if not isinstance(msg, dict):
        continue
    if (msg.get("source") or {}).get("kind") == "plugin":
        continue          # L1 是状态注入，不是对话材料
    content = msg.get("content")
    ...
```

**为什么在入口拦、不去修 `speaker` 的判据**：`speaker = player_name if role=="user" else npc_name`
对**真消息**永远是对的；错的是它吃到了伪装者。"入口拦人"还带来一个维护性优势 ——
以后再加 plugin 生产者会被自动拦下，不用记得回来改判据。
⚠ 只拦 `plugin`：工具结果是 `source.kind=="tool"`、主动开口舞台指令是 `"initiative"`，
两者照旧进材料（工具结果另有 pruner 先剪）。

**② 压缩后核对 L1 基线，被折走的段作废**（`dialogue_agent.py`）

把原先写在 `__init__` 里的"从 surface 恢复基线"抽成
`_resync_runtime_ctx_baselines()`，并补上第二件事：**不在 surface 里的段 → 基线置 None**。

```python
lost = []
for name in self._segs:
    if self._retained_ctx.get(name) and name not in seen:
        self._retained_ctx[name] = None
        self._last_l1[name] = None
        lost.append(name)
```

**为什么逐段核对、而不是无脑全作废**：`replace` 只折 `nodes[0..cut-1]`，
**保留尾里可能还留着某段的旧消息** → 全作废会让那段重发一遍、同一段两条并存。
逐段核对"还在不在 surface"更准，而且不只管压缩 —— 任何原因导致段消息消失都能自愈。

**两个调用点都要挂**（漏一个就会出现"自动压缩能自愈、手动压缩后永久丢失"的怪相）：

| 入口 | 位置 |
|---|---|
| 自动（回合边界） | `_turn` → `report = await compactor.maybe(...)` → `if report: resync()` |
| 手动（`/compact`、面板按钮） | `Agent.compact_now()` → `if report: resync()` |

**顺序天然正确**：`_turn` 里 `self._turn_ctx = None` 发生在压缩**之前** →
重发出去的是**最新**状态，不是压缩前抓的旧快照。而且纪要被 replace 插在**头部**、
四段 append 到**尾部** → L1 落在最靠近当前话头的位置。

**实测效果**（`ctx_window=1200` / `threshold_ratio=0.05`，每轮都压缩，全程不改 L1）：

| 轮次 | surface 里的 L1 段 | 摘要材料含 L1 行数 |
|---|---|---|
| 1 | `自身 / 玩家 / 近况` | 0 |
| 2 | `自身 / 玩家 / 近况` | 0 |
| 3 | `自身 / 玩家 / 近况` | 0 |
| 4 | `自身 / 玩家 / 近况` | 0 |

修复前：段在第 3 轮变成 `[]`，材料里累计 3~4 行 `玩家：Current runtime context —— …`。
修复后：**段一段不丢，材料 0 行**。

**回归测试**：`tests/test_compaction_ctx_safety.py`（5 条，先写测试确认 3 条会失败再改）
* 材料不得含 `Current runtime context`
* 材料里对话部分不得被误滤（防"一刀切过头"）
* 压缩后 L1 不变，四段仍须在（★核心回归★，既有 `test_auto_compact_keeps_fresh_l1_for_next_turn` 覆盖不到）
* 未被折走的段基线须保持同步（防"无脑全作废造成重复发送"）
* 手动压缩入口同样要作废基线

### 为什么一直没被发现

1. **重开/读档会自愈**：`create()` 走恢复路径（`:144`）扫 surface，扫不到 ctx 消息
   → `_retained_ctx` 留 `None` → 下一步重发全四段。
2. **L1 频繁变化也会掩盖**：某段一变立刻重发；只有**长期不变的段**（境界/宗门/道侣）才真丢。
3. **既有测试把"L1 变了"当成了前提**：
   `test_compaction_stage3.py::test_auto_compact_keeps_fresh_l1_for_next_turn`
   构造的场景里 L1 恰好变了（seed 的 recent 与压缩前不同）→ 靠"变化"过关。
4. **摘要被污染这件事完全没有测试**：没有任何测试检查过
   "plugin 消息不该出现在 `<transcript>` 里"。

### 验证方式

```python
# ① 段是否被折走：压缩后断言四段仍在 surface（两轮之间不改 L1）
# ② 材料是否被污染：把摘要 LLM 换成 spy，断言它收到的文本里
#    不含 "Current runtime context"
```

## 8. 复现方法（两个问题各一段）

### 复现"陈旧段累积"（§6，这是**设计代价**不是 bug）

```python
import sys, asyncio; sys.path.insert(0, "/mnt/f")
from agent_loop.tests.test_bridge_tools import reset_with_stub
loop, bridge = reset_with_stub()
agent = loop.create("林婉清")
orig = bridge.get_context
n = {"i": 0}
def patched(npc_id):                      # 每轮改一个必然改文案的字段
    n["i"] += 1; r = orig(npc_id)
    r["raw"]["player"]["realm"] = f"筑基{n['i']}层"
    return r
bridge.get_context = patched
for i in range(12):
    agent.send(f"第{i+1}问"); asyncio.run(agent.run_until_idle())
    msgs = agent.session.derive_messages()
    ctx = [m for m in msgs if "Current runtime context" in str(m.get("content"))]
    print(i+1, "ctx 条数 =", len(ctx), " 玩家段 =",
          sum(1 for m in ctx if "—— 玩家" in str(m["content"][0]["text"])))
```

⚠ **改 `intim += 1` 测不出来** —— `_intim_tier()` 按 180/120/60/20/0 分档，
180→181 同档、段文本不变 → 不触发差分。我第一次就踩了这个坑，误判成"没有累积"。
要改 `realm`/`mood` 这类必然改文案的字段。

### 复现"压缩后段丢失"（§7，**这是真 bug**）

见 `tests/test_compaction_stage3.py` 的 `_seed_history` + `StubSummaryLlm`：
把 `ctx_window` 设成 1200（保证每轮都压缩），**两轮之间不改 L1**，
打印 `derive_messages()` 里的段名与 `_retained_ctx`，即可看到两者背离。

## 9. 压缩 / 删除下的**用量口径**（09-17 定案）

`stats.UsageTracker` 出三块快照（`tokenUsage` / `sessionStats` / `contextPressure`），
它们**不是一套口径**，压缩与删除对它们的答案完全不同。混起来就会出现
"删了没变小 / 压了没用 / 重启后总 token 少一截"这类只在实机上看得见的误导。

### 9.1 两类口径

| | **花费口径** `tokenUsage` / `sessionStats` | **存货口径** `contextPressure` |
|---|---|---|
| 语义 | 这个会话一共花掉多少 | 下一次请求大概多大 |
| 载体 | **整本 log**（`rebuild_from_log` 重放全部事件） | 当前 `surface` + 上一次实测 |
| 压缩 | 只增不减，**且摘要调用要计钱** | 旧实测值必须作废 |
| 删除 | **不退钱**（钱已经花了） | 旧实测值必须作废 |

### 9.2 三条曾经的漏洞（09-17 修）

1. **摘要调用没记账**：`compaction/compress.py` 的 `_summarize` 只取 `result.text`，
   `result.usage` 直接丢；`Compressor` 由 `scripts/server.py:197` 独立构造，跟 agent 的
   stats 没有任何连线。摘要调用是**整前缀**的一次调用（全会话最贵的一次，实测一次
   约 0.8–1.2 万输入 token）⇒ 每压一次就少算一整份上下文。
   现修：`_summarize` 把用量攒进 `_pending_usage`，会话侧
   （`DialogueAgent._record_maintenance_usage`，放在 `finally` 里——fail-closed 也真花过钱）
   调 `stats.add_maintenance_usage()`，并落一条 **`dev/maintenance-usage`** 账本事件供
   重启重放。`Compressor.take_pending_usage()` 会**一并带上 pruner 的工具结果摘要**
   （同属维护开销）—— 这条的**后半截曾经是空的**：`ToolResultPruner._pending_usage` 只有
   容器与消费者，`_summarize_result` 里从来没有 append（注释却写着"已接上"）⇒ 每次压缩
   静默少算工具摘要那一份。09-17 部署前补齐，见 `tests/test_compaction_fixes.py` 三条
   （交接 / 空摘要那条 fail-closed 分支也交接 / 压缩器聚合）。
2. **`set_surface()` 是死接口**：全仓零调用 ⇒ `surface_tokens` 恒 0 ⇒
   `projected = pressure + (surface − 采样时 surface)` 恒等于 `pressure`（推算形同虚设），
   而 UI 的「上下文」直接读 `pressureTokens` ⇒ 压缩/删除后一直显示**重建前**的规模。
   现修：`UsageTracker.set_surface(tokens, turns)` + `invalidate_pressure()`，
   由 `DialogueAgent.refresh_context_pressure()` 在**压缩两处 + 删除成功后**调用。
   作废（显示"—"）比估一个数诚实：估错同样误导，而"—"只表示"还没测"。
3. **累计轮数与现存轮数混用**：`sessionStats.turns` 是**累计**（从 `step/end` 重放），
   删除后与"历史还剩几轮"不相等。现修：快照加 `contextPressure.liveTurns`
   （`history_ops.live_turn_count`），UI 文案改成「累计 N轮/M步（现存 K 轮）」，
   两者相等时不显示后半句。

### 9.3 口径的记账规则（改之前先读）

- **维护用量并入四桶**，不另开互斥桶：`tokenUsage` 的「总 = 输入 + 输出」必须始终成立
  （UI 那行数字要能自己对上），`maintenanceInputTokens/OutputTokens` 与 `compactionCalls`
  只是"其中多少"的分解。`maintenanceInputTokens` 是**整份 prompt**（缓存 + 未缓存），
  与 UI 的「输入」（`billedInputTokens`）同口径。
- **维护调用不动 `pressure_tokens`**：它吃的是"即将被折掉的历史"，不是下一次请求的规模；
  让它更新压力会在压缩后立刻把上下文显示成一个万级大数，正好抹掉压缩的效果。
- **`rebuild_from_log` 重放整本 log，不是 surface**：删除是软删（账本留痕），
  改成按 surface 重放就会静默少算，而且**只有删过历史的会话**对不上账。
- **缓存率是整会话累计**：压缩/删除会把 provider 的前缀缓存整段作废，下一次调用几乎
  全是未缓存输入，这笔钱被累计率永久吸收。要解释这个坑需要"重建事件"留痕
  （§9.4 未做项）。

### 9.4 已知未做（需要动 UI 时一起做）

- **重建事件留痕**：`dev/context-rebuild`（原因 compact/delete + 被折/被删 seq 数 + 时间），
  快照加 `rebuilds` 与 `lastRebuildUncachedTokens`，UI 文案「缓存率 75%（含 2 次重建）」。
- `ChatWindow.BuildStatsLine` 的「累计/现存」已接上 `liveTurns`；重建代价那行还没接。

### 9.5 回归保护

| 文件 | 钉什么 |
|---|---|
| `tests/test_stats.py` | 桶与恒等式、维护用量不动压力、作废与 surface 推算、重放 |
| `tests/test_history_delete.py` | 交接（`take_pending_usage` 取走即清空）、fail-closed 也留样、**删除不退钱**、现存轮数、ws 删除后刷新 |
| `tests/test_usage_accounting.py` | agent 级接线：自动/手动压缩真记账、`dev/maintenance-usage` 真落账、重放一致、删除后作废 |
| `tests/test_compaction_fixes.py` | pruner 侧的交接：`_summarize_result` 真的 append 用量、空摘要（fail-closed）也 append、`Compressor.take_pending_usage()` 聚合两份 |
