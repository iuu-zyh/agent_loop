# 上下文投影与逐段差分 —— 实现现状

> **一句话**：发给 LLM 的不是账本，是账本上的一层**投影**（`surface.nodes` 的 seq 列表）。
> L1 分**四段**，每步跑**四个互相独立的差分**，粒度各不相同；段是原子单位 ——
> 段内改一个字就整段重发，但**只发变了的那段**。
>
> 逐段链路、三个基线的实例、前缀缓存的量化论证在
> [`docs/reference/context-projection-details.md`](../reference/context-projection-details.md)。
> **日常开发不需要读**。

---

## 0. 结论速览

| 问题 | 答案 |
|---|---|
| context 是"每轮追加"吗？ | 账本 append-only；**上下文是账本上的一层投影**，不等于账本 |
| L1 是几段？ | **四段**：`time / self / player / recent`（权威 = `SystemPrompt._CTX_SEGMENTS`） |
| 重建时先提取最新吗？ | 是。每轮首步向桥取一次 raw，**轮内各步复用**（工具循环里不重复取） |
| 四段会分别取四个最新吗？ | 是。四段**各自独立**差分、独立写屉、独立发送 |
| 每步都对比吗？ | 是，**每步跑四个独立差分**，粒度各不相同（§3） |
| 一个字变了就全部重载？ | **不是。恰恰相反** —— 逐段比较，只发变化的那一段。但**段是原子单位**：段内改一个字 → **整段**重发（§5） |
| 三个基线是什么？ | `_last_l1`（上次写进屉的**正文**）/ `_retained_ctx`（上次**发给 LLM 的带包装文本**）/ `_last_sys_parts`（上次的 **section 正文**）—— 实例见 §5.5 |
| 变化的段为什么不删旧的？ | **为前缀缓存。** append-only 让「上一轮的整份请求」逐字节成为下一轮的前缀 → 实测 **100% 可复用**；改成原地替换会掉到 **25% 并逐轮衰减**（§6）|
| 那有坑吗？ | **有过，已修**：**压缩全文没有一处读 `source.kind`** → L1 段既被当【对话材料】喂给摘要（还张冠李戴成「玩家：…」），又被 replace 区间折走 → 段永久不再发。UI 投影与删回合两条路都读 kind 并豁免它，只有压缩漏了。**已按同口径补上两处护栏**（§7）|

---

## 1. 账本 vs 投影

```
Session.log      事件流水账。seq = len(log) 单调递增。**append-only，永不改写**
Session.surface  SurfaceManager.nodes，一个 **seq 列表** = "哪些 seq 算进上下文"
```

发给 LLM 的消息 = 遍历 `surface.nodes`，不是遍历 `log`：

```python
# session.py:188
def derive_messages(self):
    msgs = []
    for seq in self.surface.nodes:          # ← 投影，不是账本
        msg = derive_event_message(self.log[seq])
        if msg is not None: msgs.append(msg)
    return msgs
```

`SurfaceManager` 支持三种算子（`session.py:93`）：

| 算子 | 语义 | 谁用 |
|---|---|---|
| `surfaceOp: "append"` | push 一个 seq 进 nodes | 对话消息、L1 段、工具结果 |
| `surfaceOp: {op:"replace", start, end}` | 把 `[start,end]` 这段 seq 折成**一个** | 压缩（`compress.py:372`）、工具结果剪枝（`pruner.py:220`） |
| `history/delete` | **机械剔除**（`ranges ∪ drop_seqs − keep_seqs`） | UI 删回合（`history_ops.py:212`） |

没有 `surfaceOp` 的事件（`turn/start`、`step/start`、`tool/call`、`agent/inbox/spliced` …）
**进账本但不进上下文**：

```python
# session.py:101
if t not in SURFACE_TYPES or surface_op is None:
    self._last_seq = seq
    return                      # ← 只更新游标，不进 nodes
```

> 这条解释了为什么你日志里"历史回放 40 项"和账本事件数对不上：**回放看的是投影，账本里还有一堆记账事件。**

---

## 2. L1 四段

```python
# system_prompt.py:304
_CTX_SEGMENTS = ("time", "self", "player", "recent")
_CTX_LABELS   = {"time": "当前时间", "self": "自身", "player": "玩家", "recent": "近况"}
```

| 段 | 中文标 | 内容 | 数据来源（C# raw） |
|---|---|---|---|
| `time` | 当前时间 | 游戏内年月日 | `raw.now.{year,month,day,text}` |
| `self` | 自身 | 姓名/性别/年龄/寿元/道号/境界/宗门/位置/心情/战力/气血/气运/爱好/魅力/声望/资质/种族 | `raw.self.*` |
| `player` | 玩家 | 玩家名/境界/关系/好感/是否同处一地 | `raw.player.*`（+ `raw.relations.player`） |
| `recent` | 近况 | 最近的经历日志条目 | `raw.logs` / `raw.recent` |

* 成文函数：`SystemPrompt.format_l1_context(npc_id, raw) -> Dict[段名, 文本]`（`system_prompt.py:421`）
* 单段渲染：`render_context_segment(name, text)` → `"Current runtime context —— {中文标}：{正文}"`（`:655`）
* **`_CTX_SEGMENTS` 是唯一权威清单**。`dialogue_agent.py:105` 记着一次踩坑：
  09-13 新增「当前时间」段时，因为段名在别处硬编码成 `{"self","player","recent"}`，
  新段在"是否找齐"的判据里被静默漏掉（`.get()` 不报错）。现在一律从 `_CTX_SEGMENTS` 派生。

---

## 3. 每步的四个差分（粒度全不一样，别混）

| # | 差分 | 位置 | 比较对象 | **粒度** | 命中后的动作 |
|---|---|---|---|---|---|
| A | **写屉差分** | `dialogue_agent.py:290` | 新成文段文本 vs `_last_l1[段]` | 段 | `remove_context` + `context` 重写该段容器 |
| B | **发消息差分** | `:317` | `render_context_segment(...)` vs `_retained_ctx[段]` | 段 | 追加一条 `user/message`（id=`ctx-{段}-{轮}-{步}`） |
| C | **sys 段差分** | `:449` | `section["text"]` vs `_last_sys_parts[段名]` | section | 只把变化段推给 UI（`step.sys.parts`）；落 `dev/sys-snapshot` |
| D | **header 差分** | `:467` | `sha256(system + \\n + json(tools, sort_keys))` | 整份 header | 落 `request/header` |

两个基线**不是一回事**，很容易看错：

* `_last_l1[段]` = 上次**写进屉（容器）**的段正文
* `_retained_ctx[段]` = 上次**发给 LLM**的段文本（含 `Current runtime context —— 标：` 包装）

`_last_l1_text` 是给测试/审计看的合并字段；恢复时会置成哨兵 `"__restored__"`（`:156`）。

**C 和 D 存在的原因是"保 log 瘦 + 前缀稳定"**（`:103`、`:115`）：
L1 段每回合微变会让整份 system 变化，若按"整份比较"就会每回合全段重发。
D 的注释写得很直白：`header 差分：文本没变不落盘（保 log 瘦）`。

---


---

## 4. 实现细节去哪了

逐段差分的完整链路、三个基线的实例、前缀缓存的量化论证、压缩漏读 `source.kind` 那次事故、
复现方法 —— 全部在 [`../reference/context-projection-details.md`](../reference/context-projection-details.md)。

**实现以代码为准**：`session/session.py`（账本与投影）、`compaction/`（压缩与剪枝）。

---

## 5. 相关代码

| 关注点 | 文件 |
|---|---|
| 账本与投影（`surface.nodes` / 三种算子） | `session/session.py`（`derive_messages` / `SurfaceManager`） |
| L1 四段成文 | `agent/system_prompt.py`（`_CTX_SEGMENTS` / `format_l1_context` / `render_context_segment`） |
| 四个差分 | `agent/dialogue_agent.py`（写屉 / 发消息 / sys 段 / header） |
| 压缩与剪枝（`replace` 算子） | `compaction/compress.py`、`compaction/pruner.py` |
| 历史投影 | `session/history.py`（`project_ui_history`） |

> 原 `APPENDIX.md` 里那篇带日期的投影排查记录已拆进 `docs/archive/`，
> 追「这个结论怎么来的」时用 `grep` 找关键词。
