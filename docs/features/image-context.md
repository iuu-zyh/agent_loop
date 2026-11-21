# 图片进上下文 —— 实现现状

> **一句话**：图片从 10-18 起是**会话内容的一部分** —— 字节落存档内的图片仓、
> 账本只记 `ref`、每一轮重新挂给模型；被「删回合 / 删消息 / 压缩折走」之后才回收。
> 09-13 那条「图片只进口当回合、绝不进历史」已作废。
>
> 过程与两次复核在 [`docs/archive/2026-10-18-图片进上下文.md`](../archive/2026-10-18-图片进上下文.md)，
> **日常开发不需要读**。

---

## 1. 它是什么 / 不是什么

| 是 | 不是 |
|---|---|
| 消息 content 里的一个 `image` 块（与 `text` 块平级） | 独立的事件类型 |
| 账本里只有 `ref` + `tokens`，**没有字节** | base64 内联进 `session.log` |
| 每轮拼请求时**重新投影**成 data URI | 一次挂上、之后一直带着 |
| 图片仓里按内容哈希命名的文件，**可回收** | 只增不减的缓存 |

**为什么不能内联 base64**（这条推翻了「最省事」的直觉）：账本**只追加**
（删除 = 追一条 `history/delete` 注记，压缩 = 追一条 `replace`），写进 log 的字节
**物理上永远回收不了**；而 `save_session` 是**全量重写**，图越多每次固化越慢。

---

## 2. 五个决定

| # | 决定 | 落点 |
|---|---|---|
| ① | 字节落 `<sessions_root>/images/<scope_dir>/<sha1>.<ext>`，账本只记 `ref` | `storage/images.py::ImageStore.put` |
| ② | 账本里 image 块**就是消息的一部分** | `agent/dialogue_agent.py::_ingest_images` |
| ③ | 发前投影：`ref` → data URI（浅拷贝，绝不原地改账本） | `DialogueAgent._inline_image_refs` |
| ④ | token 在 ingest 时算好写进块里，`meter` 读它 | `compaction/meter.py::estimate_image_tokens` |
| ⑤ | 回收 =「surface ∪ 待投递筐」之外的全删 | `storage/images.py::gc_session` |

**② 的关键**：删除与压缩都只作用于 `surface`（投影），而 image 块在账本里，
所以「删掉一个回合」= 把这些 seq 从 surface 剔除，图自然不再投影，文件随后回收。

**③ 的关键**：投影必须**浅拷贝 + 新 content 列表**。`derive_messages()` 返回的对象
**就是账本事件 data 本体**，原地改它 = 把 base64 永久写进账本。

---

## 3. 三条不变量（改代码前必看）

1. **权威方向是账本 → 文件**。账本里的 `ref` 是权威，文件缺失就降级（丢图 + WARNING，**绝不崩**）。
   反过来（以文件为准去改账本）会让一次误删变成「历史被改写」。
2. **GC 的 keep-set 必须含待投递筐**。压缩发生在回合边界，此刻玩家刚发的带图消息
   还躺在 `inbox`（`agent/inbox/spliced`，尚未落 surface）。只看 surface 会把它的图删掉 ——
   图片静默丢失。`live_refs()` 直接用 `Inbox` 自己的重放逻辑算筐，**不另写一份**。
3. **`scope_dir` 必须定长安全**。可读前缀（百分号编码后截断 40 字符）+ sha1 前 8 位，
   长度恒 ≤49。**不要退回「直接 quote(id)」**：一个汉字编码后占 9 个字符，
   8 个汉字的名字就撑破 ref 的 64 字符上限 ⇒ 该 NPC 的图**每一张都落不了盘**（10-18 复核抓到的真 bug）。

---

## 4. 现状与缺口

| 项 | 状态 |
|---|---|
| 收图 / 账本 / 投影 / 记账 / 回收 | ✅ 全部落地，19 + 9 条测试钉住 |
| 纯文本模型（`supports_image is False`） | ✅ 投影时丢块，不报错 |
| `ref` 形状校验（禁止 `../` 穿越） | ✅ |
| 未声明的 `supports_image` | 当作**支持**处理（保守方向：宁可发，不可静默丢） |
| 图片被压缩折进纪要 | ✅ 折走的原图字节回收 |

---

## 5. 相关代码

| 关注点 | 文件 |
|---|---|
| 图片仓（put / data_uri / collect / GC） | `storage/images.py` |
| 收图与发前投影 | `agent/dialogue_agent.py`（`_ingest_images` / `_inline_image_refs`） |
| token 估算 | `compaction/meter.py`（`estimate_image_tokens` / `estimate_blocks`） |
| 删除与压缩触发回收 | `session/history_ops.py`、`compaction/compress.py` |
| C# 侧收图上限（一条消息最多 3 张） | `csharp/UI/ChatPresenter.cs` |
| 测试 | `tests/test_image_context.py`（19）、`tests/test_image_attachment.py`（9） |
