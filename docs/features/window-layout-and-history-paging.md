# 对话窗版式与历史分页 —— 实现现状

> **一句话**：两件独立的事共用一个窗口 —— **版式**（窗口位置跨局记住、拖动不卡）与
> **历史**（开窗只取最近一段，往上滚按游标取更早的）。
> 判据都拆成了纯算术核，能脱离 Unity 单测。

> 这一轮踩的四个实机 bug（入口不出现 / 连环加载 / 位置不锚定 / 日期堆末尾）
> 与"Python 改了没打包"那次事故的过程，见
> [`docs/archive/journal/2026-11-21-对话窗拖动与版式/README.md`](../archive/journal/2026-11-21-对话窗拖动与版式/README.md)。
> **日常开发不需要读**。

---

## 0. 它是什么 / 不是什么

| 是 | 不是 |
|---|---|
| 窗口位置存 `ModAssets/window_layout.json` | 存 `config.json`（那个归 Python 独占解释） |
| 拖动时只更新子 Canvas 的变换矩阵 | 每帧重算整棵子树顶点 |
| 服务端按游标分页 | 客户端拿全量再切片 |
| 上划加载时视口**停在原处** | 加载完跳到顶 / 跳到底 |

**只做拖动，不做缩放**：缩放（边缘/角拖拽）是 B 批，未实现。

---

## 1. 版式：三个纯核

| 核 | 回答什么 | 单测 |
|---|---|---|
| `DragCore` | 屏幕像素 → 画布单位；按锚点类型钳制在视口内 | `DragCoreTests` |
| `WindowLayoutCore` | 盘上的值能不能用（NaN / 尺寸下限 / 是否变了） | `WindowLayoutCoreTests` |
| `WindowLayoutStore` | 读写 `window_layout.json`（`.tmp` + 替换） | — |

**钳制必须按锚点分派**：AB 版 `BG` 是**居中点锚**，用右中锚公式在 1920 视口下
会算出 `x ∈ [-720, 0]`（正确 `±360`）⇒ 只能往左拖。代码版窗口是右中锚，两条公式都在。

**屏幕坐标换算必须带画布相机**：`RectangleContainsScreenPoint(w, mp, null)` 的
`null` 相机**只对 Overlay 成立**。本面板根 Canvas 是 `ScreenSpaceCamera` ⇒ 传 null 恒不命中
（表现为"一次都拖不起来"）。`UiKit.CanvasCamera` 负责取相机。

---

## 2. 版式：为什么拖动要挂独立子 Canvas

UGUI 的**重算单位是 Canvas，不是元素**：改任何一个元素的 transform，整个 Canvas 的顶点重算。
`BG` 底下有 N 行消息 + 8 颗按钮 + 侧边栏，拖动每帧写 `anchoredPosition` = 每帧重算上千顶点
（实测 184fps → **28fps**，而 `TickProfiler` 显示我们自己的回调 < 1ms ⇒ 瓶颈在渲染层）。

给 `BG` 挂子 Canvas 后，内部顶点坐标相对它自己，移动只更新变换矩阵。

### 两个必须做对的地方（都踩过）

| 项 | 结论 | 踩错的表现 |
|---|---|---|
| `GraphicRaycaster` | **必须补一个** | 不补 ⇒ **整个窗按钮、输入框全点不动** |
| `overrideSorting` | **保持默认（不许设）** | 设 true ⇒ 子 Canvas 脱离父级上下文，内容可能被压到背景之下 |

**射线按 Canvas 分表**：`GraphicRegistry` 按**最近的 Canvas 祖先**注册，
`GraphicRaycaster` 只查自己所属 Canvas 那张表。挂子 Canvas = 新开一张表，
父级 raycaster 查不到 ⇒ **零拾取**（不是"双倍拾取"）。

**`overrideSorting` 不能照抄 `UnreadBanner`**：那个是**自建根 Canvas**（挂 `g.root` 下），
必须自己定 order 才能盖住东西；我们是**子 Canvas**，继承父级才对。
运行时实测：游戏 UI 层 `ScreenSpaceCamera order=0`、实例根 `WorldSpace order=20`，两套渲染模式。

**不加 `CanvasScaler`**：缩放由父级统一决定，子 Canvas 再挂一个会缩两次（窗口变四分之一）。

---

## 3. 历史：服务端游标分页

```
open_chat(limit=20)                        → 最近 20 回合 + (next_before_seq, next_before_turn)
get_history(before_seq=X, before_turn=Y)   → X/Y 之前 20 回合 + 新锚
0, 0 = 没有更早的了
```

| 项 | 单聊 | 群聊 |
|---|---|---|
| `limit` 单位 | **回合** | **条** |
| 游标 | **双锚** `(seq, turn)` | 单锚 `seq` |

单位不同不是笔误：群发言没有回合概念（`project_room_history` 刻意不返回 turn）。

### 为什么用 `seq` 而不是 offset

账本里有 `history/delete` 删除注记与压缩纪要替换。**删掉一条，其后所有下标前移**
⇒ offset 翻页会重复或跳过。`seq` 单调递增、永不复用，游标不漂移。

### 为什么单聊必须双锚

账本里有**整个回合没有 seq** 的合成内容（`initiative` / `turn_error`；真机 43 回合里有 2 个）。
只按 seq 判会把它们当成"永远更早"⇒ **每页重复返回**（实测 `第1页[38..58]` / `第2页[18..42]`）。
判据：优先用回合号，无 seq 时退回"该回合**最大** seq < 游标"（用最大是因为用最小会让跨越游标的回合每页重复）。

群聊不需要：群发言平铺，每条都带 seq。

### 加载更早的不变量

| 不变量 | 违反的后果 |
|---|---|
| 插入后**延迟一帧**再补偿滚动位置 | 布局未结算 ⇒ `added≈0` ⇒ 视口算成顶部 ⇒ **连环加载**（实测 4 秒翻 2 页） |
| 锚定未落位时**不许**再触发自动加载 | 同上 |
| 收集**整段**新增节点（`[before,after)`） | 只取最后一个 ⇒ **日期分隔行堆在末尾** |
| 挪到最前必须**倒序** | 正序 ⇒ 历史倒着排 |
| 插入前清空、插入后复原日期累进状态 | 不清 ⇒ 日期乱序（年份倒退） |
| 翻到头两个锚**都**归 0 | 入口永远显示，点了返回空列表 |

`ScrollAfterPrepend` 的参数语义：`prevScrollPx` 是**从顶部算的已滚距离**
（`(1-verticalNormalizedPosition)*range`），不是"离底部距离"。

---

## 4. 相关代码

| 路径 | 角色 |
|---|---|
| `csharp/UI/DragCore.cs` | 拖动算术（纯） |
| `csharp/UI/WindowLayoutCore.cs` | 版式判据（纯） |
| `csharp/UI/WindowLayoutStore.cs` | `window_layout.json` 读写 |
| `csharp/UI/HistoryWindowCore.cs` | 分页窗口/滚动补偿（纯） |
| `csharp/UI/ChatWindow.cs` | `TickDrag` / `EnsureOwnCanvas` / `InsertEarlier` / `TickEarlierAnchor` |
| `csharp/UI/ChatPresenter.cs` | `HistoryPageTurns` / `RememberHistoryCursor` / `OnEarlierRequested` |
| `session/history.py` | `project_ui_history`（`before_seq` / `before_turn`） |
| `room/room_history.py` | `project_room_history`（单锚） |
| `transport/ws_channel.py` | `_parse_cursor` + 三处 `_history_out` 透传 |

---

## 5. 已知边界

- **缩放未实现**（B 批）。预制体里 `ChatInput` 的 `Text`/`Placeholder` 是固定 780 宽，
  一旦支持改宽度会先坏在这里。
- **崩溃/强杀时窗口位置丢失**：落盘在关窗时，代价是崩溃那次的位置回到上次成功保存的值。
- **不报"还有多少条"**：服务端只说"还有没有更早的"，没给总数（要扫一遍账本，反而变慢）。
  入口行文案因此不报数字，不编造。
