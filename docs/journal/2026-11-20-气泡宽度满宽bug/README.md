# 气泡「文字没到上限却撑满上限」

## 现象（11-20 用户截图）

一段约 30 字的回话（`*眉眼一挑*` 那几句），气泡横向撑到天花板，
右边大段空白；而**关窗再打开**（回放）就完全正常。

## 根因：不是算式错，是**测量时机**

`BubbleWidthCore` 的算式一直是对的（36 项单测全绿）。错在接线：

```csharp
w = need > 0f ? Fit(need, cap) : cap;      // ← 量不出就按上限给
```

`measured <= 0` 的真实含义是「**布局还没结算**，`Text.preferredWidth` 读到 0」，
不是「这段文字不需要宽度」。旧代码把它当成后者，直接撑满上限。

为什么错一次就永久错：**非流式气泡只有一次测量机会**。
`ChatBubble` 构造时 `SetText → ApplyBubbleWidth` 走一遍，之后没有第二次
（流式气泡靠每帧 `_deltaDirty` 刷新才会重算）。于是那一帧的读 0 被固化。

这也解释了两个观察：

| 观察 | 解释 |
|---|---|
| 只有长段回复出问题 | 短句 `Need` 本来也小于 `MinWidth`，撑满与否看不出来 |
| 关窗重开就正常 | 走 `ReplaceHistory` 回放，那时布局早已结算，量得准 |
| 日志里 `step(delta)` 是 0 | 本仓走 `step(text)` 整段到达，**从不走流式** ⇒ 没有"每帧重算"兜底 |

真机日志佐证：整场 `step(delta)` 计数 **0**，10 次 `step(text)` 全是整段。

## 修法

判据归核、接线归层（本仓一贯做法）：

1. `BubbleWidthCore.ShouldWrite(measured, newWidth)` —— **量不出就不写**。
   宁可让气泡停在预制体原宽一帧，也不写一个凭空的满宽。
2. `BubbleWidthPump` —— 一条 `FrameTicker` 常驻回调（键 `KBubbleWidth=20`），
   把量不出的气泡排进队列下一帧重测。队列空时自动 `Stop`，零常驻开销。
   为什么不用 `MonoBehaviour.Update`：`ChatBubble` 是纯 C# 类，挂不上组件；
   而且需要重测的只在开窗头几帧是个位数，每条气泡都挂一个纯属浪费。
3. 重测**次数封顶** `MaxRemeasureTries = 3` —— 否则「永远量不出」（字体缺失、行已销毁）
   会变成每帧一次的循环。

## 这一轮真正的教训

**单测全绿不等于功能对。** 36 项 `BubbleWidthCore` 单测覆盖了每一条算式，
但它们测的是「给定输入算得对不对」，测不到「接线有没有绕过判据」。

所以补了 `tests/test_chat_bubble_width_wiring_contract.py`（7 项源码契约），
守接线而不是守算式。变异验证 5 组，其中 **3 组第一次都没抓住**：

| 变异 | 第一次 | 原因 | 改法 |
|---|---|---|---|
| 接线绕过 `ShouldWrite` | ✅ 抓住 | — | — |
| 恢复 `: cap` 兜底 | ✅ 抓住 | — | — |
| 重测上限改成 `if (true)` | ❌ **漏网** | 只断言标识符在场，改条件后它仍在 | 钉 `if (...)` 的**条件表达式**本身 |
| 删掉 `RequestRemeasure()` | ✅ 抓住 | — | — |
| 删掉「队列空就 Stop」 | ❌ **漏网两次** | ① Pump 里有第二处 `Stop`（catch 兜底）② 用 `any()` 遍历所有判空处，变异体与正常收摊并存时只要有一个满足就过 | 限定**第一处**判空必须带 Stop |

第 5 组值得单独记：`if (_queue.Count == 0) { return; }` 和
`if (_queue.Count == 0) FrameTicker.Stop(Key);` 同时存在时，
「有任一判空处带 Stop」永远为真 —— 断言看着很合理，实际什么都没守住。

## 相关文件

- `csharp/UI/BubbleWidthCore.cs` —— `ShouldWrite` / `MaxRemeasureTries`
- `csharp/UI/ChatItemViews.cs` —— 接线 + `BubbleWidthPump`
- `csharp/app/FrameTicker.cs` —— `KBubbleWidth = 20`
- `csharp/tests/BubbleWidthCoreTests/Program.cs` —— 36 项（新增 8）
- `tests/test_chat_bubble_width_wiring_contract.py` —— 7 项（新）

---

## 补充（同一轮，用户纠正后）

### 我第一版诊断错了一半

第一版我写"流式气泡每帧都重刷，第一帧错了第二帧会纠正，所以这个 bug 只在非流式下出现"。
**用户指出截图就是流式情况**，这个说法是错的。

错在用一个**不记录该事件**的日志去证明它没发生：`WsClient.OnUiEvent` 只给
`npc_reply` / `step` 两类打日志，而流式增量帧的事件名是 `text_delta`
（源码注释：「现已并入 npc_reply 全文，此处不单独输出」）。所以
`step(delta)` 计数恒为 0 **不代表没有流式**。

### 流式下的真实机制（比非流式更糟）

`LateUpdate` 的流式刷新（11-20 改动前）：

```csharp
if (_deltaDirty && _bubbleText != null)
{
    _bubbleText.text = BubbleTextCore.Render(_delta.ToString());
    _deltaDirty = false;            // ← 只写文字，从不重算宽度
}
```

而 `ApplyBubbleWidth` 全仓只有两个调用点：`ChatBubble.SetText()`（构造时一次）
和 `RetryMeasureWidth()`（本轮新加）。`SetText` 又只被构造函数调用。

于是流式时序是：

```
BeginNpcBubble("")        ← 起泡时文字是**空串**
  └ ChatBubble 构造 → SetText("") → ApplyBubbleWidth
        ⇒ 量不出（空文字）⇒ 旧代码 w = cap ⇒ 宽度定成上限 1060
AppendDelta × N           ← 每帧只写 Text.text，**从不碰宽度**
        ⇒ 整段流式期间宽度恒为 1060
```

所以流式不是"错了会纠正"，而是**根本没有第二次测量**。
两条路的病根相同：测量只发生一次，且发生在一个必定失败的时机。

### 补的修法

1. `ChatWindow` 持有 `_deltaView`（`ChatBubble` 引用）—— 光有 `Text` 组件算不了宽度
   （`_bubble` / `_refs` / `_width` 都是 `ChatBubble` 的字段）。
2. `LateUpdate` 刷 Text 后跟一次 `_deltaView.OnStreamTextChanged()`。
   每帧调用安全：`ApplyBubbleWidth` 内部有 `ShouldRewrite` 去抖（差值 < 1.5 不写）。
3. `OverwriteStreaming`（终稿覆盖流式泡）也补一次 —— 终稿长度常与流式最后一帧不同。
4. `ClearStreamingState` 里解引用，防下一回合拿到已销毁的视图。

### 最小高度：用户说的"两行"是精确的

`UIChatAi.prefab` 里两个气泡的 `LayoutElement.m_PreferredHeight = 76`。
而实际行高 = 字号 19 × 行距 1.35 = **25.65**，气泡上下内边距 12+12 = 24：

| 行数 | 文字高 | + 内边距 | 应有高 |
|---|---|---|---|
| 1 行 | 25.65 | 24 | **49.65** |
| 2 行 | 51.30 | 24 | **75.30** |

`76 ≈ 75.30` = **正好两行**。所以一条「嗯」「好」也硬撑两行高。

改法：`m_PreferredHeight` 76 → **50**（单行 49.65 向上取整，留 0.35 防半像素截断）。
`m_LayoutPriority` 保持 **0** 不动 —— 改成 1 会把高度钉死，长文本被
`vOverflow=Truncate` 静默截断（09-17 的教训）。

⚠ 与 `BUBBLE_SIZE = "{x: 450, y: 76}"` 不是同一个东西，别一起改：
前者是 `RectTransform.m_SizeDelta`（布局结算前的初值，会被 `ContentSizeFitter` 覆盖）；
后者是 `LayoutElement.m_PreferredHeight`（VLG 的保底，与自算高度取大者）。
真正决定"一行也占两行高"的是后者。

### 这一轮变异验证（5 组，全抓住）

| 变异 | 结果 |
|---|---|
| 删掉 `LateUpdate` 里的宽度重算（还原原始 bug） | ✅ 红 |
| 删掉 `_deltaView = b;` 赋值 | ✅ 红 |
| 终稿覆盖分支不重算宽度 | ✅ 红 |
| 流式入口自己写 `sizeDelta`（绕过统一判据） | ✅ 红 |
| `LAYOUT_PREF_HEIGHT` 改回 `"76"` | ✅ 红 |
