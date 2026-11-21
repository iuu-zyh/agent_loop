# 工具结果契约 —— 实现现状

> **一句话**：C# 只回**纯原始数据**（`Ok(data)`，不带任何叙述），
> 自然语言由 Python 的 `tools/text_render.py` 组句 —— 一份数据，两种消费者（模型 + 玩家）
> 读**同一个字符串**。
>
> 适用：`csharp/tools/*.cs`（工具层） ↔ `tools/text_render.py`（渲染层）。
> 逐工具字段清单在 [`docs/reference/tool-result-fields.md`](../reference/tool-result-fields.md)。
>
> 信封自 11-19 起只有**一处实现**：`csharp/game/ToolResult.cs`（`Ok` / `Fail` / `FailWith`）。
> 原先 8 个文件各写一份、形状漂了三种。

## 0. 分工与原则

```
C# ToolExecutor.Execute(name,args) → {success, data}     ← 只给原始数据，不组装叙述
        │  （WsClient 转发 / DramaGate 挂起后补发）
        ▼
Python dialogue_agent._execute_tool_calls
        text = text_render.render(name, args, res) or json.dumps(res)
        │  （渲染器缺失 / 形态不认识 / 抛异常 → 回退全量 JSON）
        ├──→ LLM 的 tool_result
        └──→ UI 行动记录行（同一字符串）
```

**原则（用户定调）**

1. **忠实优先于简短**：data 里的语义字段一律呈现——不截断列表、不隐藏失败项、不省略数值；
   省 token 只靠"去掉 JSON 结构包装"。
2. **字段缺失 ≠ 否定**：`completed`/`accepted` 缺失（非挂起形态）不得说成"未完成/被拒绝"。
3. **本地化归 C#**：conf 查表中文字段（性格/魅力档位/关系中文/区域名）在 C# 完成；Python 只组句。
4. **人称**：「你」=玩家；NPC 一律用真名。列表类结果换行分条。
5. **边界铁律（2026-09-10 用户定调）：渲染层禁止任何截断/上限/丢弃——N 条进必须 N 条出。**
   "取多少/留几条"全部由 C# 决定：`query_world.count`（events 条数）、`query_world.top`（榜单名次）、
   `search_units` 前 10、`inspect_unit.log_page`（经历每页 5 条）、`movement/…` 等工具的语义校验。
   Python 不替 C# 做取舍；**防回退哨兵** = `tests/test_text_render.py::test_no_truncation_anywhere`
   （events/places/sects/logs/search/inventory/关系簿/rankings 各喂 N 条断言 N 条出）。

### 0.1 机器字段例外清单（渲染层省略，data 中完整保留）

省略只影响 text 呈现，**C# 给的数据没有任何丢失**（模型看不到而已）。登记在此以便审查：

| 工具 | 省略字段 | 理由 |
|---|---|---|
| inspect_unit | `sect_id`、`stats.heart_confID`、`luck.all` | 内部 id / 冗余（`luck.all` = born+added 去重合并） |
| economy_item | `items[].from`（"npc->player"） | 方向已由标题「X 赠予 Y」表达 |
| social_relation | `create_ret`（仅**解除类**分支）、`state_at_return`、`precheck` | 解除类的引擎诊断值（`bw_err` 有异常才渲染）；**缔结类**的 `create_ret`/`precheck` 09-17 起**必须渲染**（见 §4） |

> 如后续需要这些字段进 text（例如要让模型用 `sect_id` 交叉引用宗门），在此清单里划掉并补渲染器即可。

### 0.2 工具结果离开渲染层之后的两个下游

渲染层产出的那个 `text` 串有**两个下游**，两边口径必须分清（混起来就是"同一件事两种说法"）：

| 下游 | 做什么 | 口径 |
|---|---|---|
| **喂模型**（`dialogue_agent._execute_tool_calls` 的 `tool_result`） | 超预算结果被 `compaction/pruner.py` **摘要**成要点（LLM 语义摘要优先，失败退确定性 head + marker + tail 折叠） | **允许有损**：缺省阈值 8192 字 / head 4096 / tail 1024（`ToolResultPruner` 的构造缺省；生产由 `Compressor(llm=…)` 自动建，`compaction.enabled` 只管自动压缩、不影响手动）。替换走 `surfaceOp: {op:"replace"}`，**原文仍在账本里** |
| **喂玩家**（聊天窗「行动记录」那一行） | 结果行默认折成 `▸ ✓ 摘要…（共 N 字，点击展开）`，点开是**原文**（见 `docs/reference/csharp-iron-laws.md` 2026-09-16-工具结果折叠.md）。**群聊**里整块标题带说话人（`姜萌 · 行动记录 (3)`，见 2026-09-17-群聊四件事.md） | **一个字都不许动**：摘要是"预告"不是替代品（§0 原则 1）。剪枝**不改 UI** —— UI 投影只读账本原文 |

两条**静默失效**（都实测过、都已修；改这块之前先读）：

1. **摘要那次调用也是钱，用量必须交出来。** pruner 对每个超预算结果真调一次 LLM：
   `_summarize_result` 拿到 `result.usage` 就 append 进 `_pending_usage`，位置在
   **"取文本之后、判空之前"**（摘要为空 / 解析失败的 fail-closed 路径同样要记 —— 调用已经发生）；
   链路 `pruner → Compressor.take_pending_usage() → DialogueAgent._record_maintenance_usage()`
   → `stats.add_maintenance_usage` + 账本 `dev/maintenance-usage`。
   ⚠ 这里曾经**只有容器与消费者、没有任何一处 append**（注释还写着"已接上"）⇒
   每次压缩的面板"总 token"都少算工具摘要那一份，**零报错**。回归：`tests/test_compaction_fixes.py`（3 条）。
2. **剪枝的替换节点不许进 UI 投影。** 折完是 **append 一条新的 `tool/result`**（带 replace surfaceOp），
   而 `project_ui_history` 原先只滤 `history/delete` 与 plugin 消息 ⇒ 实测投影出**两条** `tool_result`
   （9000 字原文 + 45 字摘要），玩家每次开窗回放都会在「行动记录」里多看到一行。
   修法 = 投影跳过**替换节点**（它复述的内容账本里本来就有；压缩折出来的那条是 plugin 消息，
   本来就滤）。回归：`tests/test_ui_history.py::test_project_ui_history_skips_replace_nodes`。
   判据不是"哪条更好看"，而是**直播与重放必须逐字一致** —— 屏幕上当时显示的就是原文。

> 另：工具结果行**关窗不丢**。关窗期间到达的 `step(tool_call / tool_result)` 帧由 `ChatFrameQueue`
> 逐条记下（"当前未闭合段"），重开窗时整段重演（含结果行与它的折叠态）。
> 见 `docs/reference/csharp-iron-laws.md` 2026-09-17-单聊关窗不丢.md。

---


---

## 1. 逐工具字段清单

每个工具的 `data` 里有什么、渲染器怎么组句 —— 见
[`../reference/tool-result-fields.md`](../reference/tool-result-fields.md)。
**实现以代码为准**：C# 侧 `csharp/tools/*Tool.cs`，Python 侧 `tools/text_render.py`。

## 2. 参数化改动

### 2.1 rankings：放开 `top` 上限（20 → 200）

**现状**：schema `top` 1~20、C# 钳制 1~20（默认 10）。模型想多看只能一次 20 名，**上限本身没有语义依据**。

**提案**
| 位置 | 改动 |
|---|---|
| `tools/schemas.py` | `top`：`maximum: 20 → 200`；描述补"默认 10，需要更多可自行上调" |
| `csharp/ToolExecutor.QueryWorldRankings` | `if (top > 20) top = 20;` → `if (top > 200) top = 200;` |
| `bridge.py` 同名 mock | 同步 1~200 |
| 测试 | 断言 top 边界（20→200） |

**理由**：榜单是"一次性查询"，条数应由模型按需决定；保留 200 只是防呆（防止模型填 100000 时全图单位 dump 爆 token）。
默认仍 10，不改变常规开销。

### 2.2 events：新增 `count` 参数，默认 12 条（≈最近一年）

**现状**：C# 固定 `const int MaxItems = 24`，无参数。

**提案**
| 位置 | 改动 |
|---|---|
| `tools/schemas.py` | 新增 `count`：`type: integer, minimum: 1, maximum: 120`，描述"仅 topic=events：按月志倒序取最近 N 条，默认 12（约一年）" |
| `csharp/ToolExecutor.QueryWorldEvents` | 读 `args["count"]`（默认 12，钳制 1~120）替换常量 24 |
| `bridge.py` mock | 同步该参数 |
| 渲染层 | **无需改**（已全列 + 计数头） |

**语义说明**：`count` 是**条数**（月志每月条目数不定，通常 1~3 条），"12 条 ≈ 最近一年"是近似。
若要严格"最近 12 个月的全部条目"，需按 `month` 窗口过滤（要用到游戏当前月份字段，未实证）——
本提案先做条数版，简单可靠；确需按月窗口再单独提。

### 2.3 events 上限 120
与 9.2 同批：上限 120 条 ≈ 十年量级，足够"翻旧账"场景，再往上主要是防爆。

---

## 3. 兜底与错误

- **错误帧**：`{success:false, error:"人话原因"(, data:{…})}` → 无 `data.text`，渲染器返回 `None`
  → **回退 `json.dumps(全量)`**（`error` 本身已是人话，模型能直接读）。
- **未实装的 topic**（如 `region`）：同上回退。
- **未来新工具**：没写渲染器 → 自动回退 JSON，行为与旧版一致，不会坏。
- **测试**：`tests/test_text_render.py`（渲染器单测，含兜底路径）+ `tests/test_bridge_tools.py`（mock 与渲染联动）。

---

## 4. 相关代码

| 关注点 | 文件 |
|---|---|
| 信封唯一实现（`Ok` / `Fail` / `FailWith`） | `csharp/game/ToolResult.cs` |
| 工具层（一个工具一个类，只产原始数据） | `csharp/tools/*Tool.cs` |
| 渲染层（组句，`render(name, args, res)`） | `tools/text_render.py` |
| 工具定义与参数契约 | `tools/schemas.py` |
| 结果剪枝（超预算折成摘要） | `compaction/pruner.py` |
| 契约测试 | `tests/test_text_render.py`、`tests/test_tool_contract_consistency.py`、`tests/test_tool_group_consistency.py` |
