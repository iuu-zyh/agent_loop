# 对话窗拖动修复 + 位置跨局记住（11-21）

用户原话：

> 当前对话框是固定的，能否将其变为可以拖拽的，甚至缩放？你分析一下

> 先做A，我测试完之后你再做B

> 位置和尺寸跨局记住，这就和配置里面的设置一样的啊
> 缩放主要是增大对话框的尺寸，但是字体大小，气泡大小，头像大小不变，你明白吧
> 你的拖拽区我认可

## 一、结论先说：拖动**代码一直在**，但三个 bug 让它像"没做"

`ChatWindow.TickDrag()` 是 10-22 那批写的，挂在 `Update()` 上，逻辑齐备
（标题带几何 + EventSystem 控件避让 + `DragCore` 换算钳制）。
但实机上它**一次都拖不起来**，而且即使拖起来了也**只能往左**，**关窗就归零**。

## 二、三个根因

### ① `RectTransformUtility` 传了 `null` 相机

```csharp
// 修前（ChatWindow.cs）
RectTransformUtility.RectangleContainsScreenPoint(w, mousePos, null)
RectTransformUtility.ScreenPointToLocalPointInRectangle(w, mousePos, null, out var lp)
```

`null` 相机**只对 Overlay 画布成立**。而 AB 面板的根 Canvas 是 `ScreenSpaceCamera`
—— 这不是推测，`Player.log` 里写得很清楚：

```
[AbChatPanel] 挂载链(实例→根): UIChatAi[OFF](Canvas mode=WorldSpace ...)
  <- UI[on](Canvas mode=ScreenSpaceCamera order=0) <- Root[on] <- Canvas[on](mode=ScreenSpaceCamera)
```

传错的后果不是报错，而是**恒不命中** ⇒ `InTitleBand()` 永远返回 false
⇒ `_dragging` 永远 false ⇒ **一次都拖不起来，且日志里一个字都没有**。

> 同一个坑在本仓已经踩过两次，两处文件头都写着这条教训：
> · `HoverTip` —— 悬停气泡"登记全在、一个都不出"
> · `DramaAiOption` —— 屏坐标整个算错，"屏内=否"
>
> 判模式必须看**根 Canvas**（Unity 规定嵌套 Canvas 的 `renderMode` 不生效，
> 子节点上那个只是序列化残留）。现收成 `UiKit.CanvasCamera(Transform)` 一处。

**关于 `PressOnSelectable` 里的 `RaycastAll`**：它**不需要**也不能补相机。
`GraphicRaycaster.Raycast` 内部是 `var cam = eventData.pressEventCamera ?? canvas.worldCamera;`
—— 它本来就用自己画布的相机。而 `PointerEventData.pressEventCamera` 在本工程引用的
uGUI 版本里是**只读属性**（`=> pointerPressEventCamera`），赋值直接 `CS0200` 编译不过。
（这次真的撞上了，编译报错抓的。）两者不是同一个问题，别照搬。

### ② 钳制用错了锚点公式

`DragCore.ClampAnchored` 的注释写的是"窗口 anchor/pivot = (1, 0.5) 右中" ——
那是**代码版构建器**（`ChatUiBuilder` 的 `Window`，`anchoredPosition=(-24,0)` 右贴边）。
而 AB 预制体里 `BG` 是**居中点锚**：

```
BG  m_AnchorMin={x:0.5,y:0.5}  m_AnchorMax={x:0.5,y:0.5}
    m_Pivot={x:0.5,y:0.5}      m_SizeDelta={x:1200,y:800}
```

拿右中公式钳居中锚，1920×1080 视口下：

| 轴 | 旧公式给的范围 | 正确范围 | 后果 |
|---|---|---|---|
| x | `[1200-1920, 0]` = **[-720, 0]** | `±360` | **只能往左拖，往右一格都拖不动** |
| y | `±140` | `±140` | 恰好相同（所以更难发现） |

修法：`ClampWindowToViewport` 按 `anchorMin==anchorMax` 分派 ——
点锚走新的通用公式 `DragCore.ClampToViewport`，右中/拉伸锚保留 `ClampAnchored`。

通用公式（父级 pivot=(0.5,0.5) 时）：

```
left  = (a.x - 0.5)·viewW + pos.x - p.x·winW
right = left + winW
⇒ pos.x ∈ [ -viewW/2 - (a.x-0.5)·viewW + p.x·winW ,
             viewW/2 - (a.x-0.5)·viewW + p.x·winW - winW ]
```

窗口比视口还大时区间反转（`lo > hi`）⇒ 取中点（居中），
因为"两边都露一点"比"贴死一侧、另一侧完全看不见"更容易再抓住。

### ③ 关窗即重建，位置归零

`Player.log` 里 `g.ui.OpenUI("UIChatAi") 创建宿主...` 出现 **23 次** ——
游戏 `CloseUI` 会**销毁**宿主，下次开窗是从预制体重新实例化，
`BG.anchoredPosition` 回到 `(0,0)`。位置没有任何持久化。

## 三、位置跨局记住（用户定案）

用户明确：「位置和尺寸跨局记住，这就和配置里面的设置一样的啊」。

### 为什么不写进 config.json

`config.json` 归 Python 的 `config_loader` **独占解释**，C# 侧 `ModConfigFile` 文件头写着
"只读，绝不写回"。两边同时写一个 JSON 必然互相覆盖，且 Python 整份重写时会把我们塞的键丢掉。

### 落盘方案

`<数据根>\window_layout.json`（= `ModAssets\window_layout.json`，与 `config.json` 同层）：

```json
{ "x": -120.0, "y": 0.0, "w": 1200.0, "h": 800.0 }
```

与 `notice_seen.json` 同款：首次访问读一次盘、之后全走内存；写盘先写 `.tmp` 再替换；
失败只记一行日志、绝不影响开窗（**记不住位置是小事，打不开窗是大事**）。

### 为什么不存像素

存的是**画布单位**（`BG.anchoredPosition`）。换分辨率后 CanvasScaler 的 scaleFactor 变了，
但"窗口中心相对屏幕中心偏了多少画布单位"这个语义不变，所以同一份数值在新分辨率下
仍落在合理位置，应用时再按当前视口夹一次即可。

### 什么时候写、什么时候读

| 时机 | 动作 | 理由 |
|---|---|---|
| 松手（`GetMouseButtonUp`）且本次真挪过 | `SaveWindowPos()` | 每帧写盘会卡主线程；`_dragMoved` 保证一次拖动只写一次 |
| 钳在边界上原地不动 | **不**算"挪过" | 否则在屏幕边缘点一下也触发写盘 |
| 开窗后**第 2 帧** | `TickRestoreLayout()` | `Open()` 当帧父级 rect 还是 0，这时候算钳制域会拿到 0×0 视口把窗钳到 (0,0) |
| 每次 `Open()` | 重置 `_layoutRestored/_layoutRestoreWait` | 宿主每次都是新实例，不重置就"只有第一次开窗在正确位置" |

### 恢复也要过钳制

换了分辨率/窗口模式后，上次存的位置可能已经在屏外，直接写回去就是"窗口看不见了"。
所以恢复路径同样走 `ClampWindowToViewport`。

## 四、判定核（可单测的部分）

`WindowLayoutCore.cs` —— 零 Unity、零 Newtonsoft、零文件 IO，
`csharp/tests/WindowLayoutCoreTests` 直接链接源码跑 **51 项断言**：

| 判据 | 挡住的错误 |
|---|---|
| `Finite` | NaN/Inf 会把 UGUI 布局整个算崩 |
| `SanitizeSize` | 小于下限**判废**（不是夹到下限 —— 夹会得到"宽是新的、高是旧的"半吊子版式） |
| `SanitizeSize` 的 `maxW/maxH` | 传 0/NaN = **未知，不夹上限**（否则 4K 屏上存的 2000 宽会被出厂值 1200 砍掉） |
| `SanitizePos` | 只判数值可用；夹视口要 Unity 的 rect，在 `DragCore` |
| `PosChanged/SizeChanged` | 去抖（差 < 0.5 画布单位不写盘） |

`DragCore.ClampToViewport` 另有 **28 项断言**（`DragCoreTests`），
其中"居中锚往右 200 放行（旧公式会钳回 0）"就是 ② 的回归测试。

## 五、变异验证

新断言全部过变异（不验证等于没写）：

| 变异 | 结果 |
|---|---|
| 相机传回 `null`（复现原 bug） | 抓到 |
| 钳制不判锚点，全走右中公式 | 抓到 |
| 每帧落盘（不判动没动） | 抓到 |
| 开窗不重置恢复标记 | 抓到 |
| 恢复不等两帧 | 抓到 |
| 存储层写 config.json | 抓到 |
| 居中锚公式漏掉 pivot 项 | 抓到 |
| 居中锚公式漏掉锚点项 | 抓到 |
| 区间反转时不取中点 | 抓到 |
| 未知上限也夹 | 抓到 |
| 小于下限时夹到下限而非判废 | 抓到 |
| NaN 不挡 | 抓到 |
| 去抖 epsilon 放大到 100 | 抓到 |

## 六、部署与验证

- DLL `cdcd133128f733a59d984fec7a5ba802`，四处一致
  （游戏 `ModExportData`、mod `debug`、mod `ModProject`、`ModMain/bin/Release`）
- 构建戳 `1121-dragfix`（`.NET` 字符串是 UTF-16，`grep` 查不到，要用 `'...'.encode('utf-16-le')`）
- 新符号确认在 DLL 里：`ClampToViewport` / `CanvasCamera` / `TickRestoreLayout` /
  `SaveWindowPos` / `WindowLayoutStore` / `WindowLayoutCore`
- 全量测试：**2409 passed / 0 failed**（`test_room.py` 那 5 个既有 flaky 本轮未复现，已确认与改动无关：回退改动后同样失败且多一个）
- C# 单测：**31 个工程全部通过**

### 重启后怎么验

1. 开对话窗，按住**标题栏**（窗口顶 48px 带）拖动 —— 现在**四个方向都能拖**。
2. 拖到左下角，关窗，**重开** —— 应该还在左下角（不再回中间）。
3. 日志里搜：
   ```
   [Chat] 窗口拖动钳制域：锚=点锚(0.5,0.5) pivot=(0.5,0.5) 窗=1200x800 视口=1920x1080 请求(...) → 钳后(...)
   [WindowLayout] 已读取：位置 ... 尺寸 ...
   [WindowLayout] 已落盘：位置 ...
   [Chat] 窗口版式已恢复：位置 ... 尺寸 ...
   ```
   `钳制域` 那行是排障关键：若出现"某个方向拖不动"，看 `锚=` 是不是 `点锚`
   以及 `视口=` 是不是真实画布尺寸（读到 0×0 就会把窗钳到中心）。
4. 点 ✕ / ⚙ / 删除 / 压缩 —— **不许**把窗拖走（控件避让仍靠 EventSystem 射线）。

## 七、下一步（B 阶段，等用户测完 A）

用户已认可拖拽区方案（右边缘 + 下边缘 6px 细条 + 右下角热区），
并明确缩放语义：**增大对话框尺寸，但字体/气泡/头像大小不变**
（即改 `BG.sizeDelta` 与各锚点驱动的子节点，不是改 `localScale` —— 后者会把字放大变糊）。

B 阶段要处理的已知问题（本次勘察时已定位，尚未动）：

1. **气泡宽度是缓存的**：`ChatBubble._width` + `ShouldRewrite` 去抖 1.5 ⇒
   改尺寸后屏幕上已有气泡宽度全不变，只有新气泡按新宽算。需要批量重测。
2. `CompactEditPanel` 在 `Build` 时缓存 `_bgW/_bgH`（真机证明 Show 时现量会读错），
   改尺寸后是陈旧值，面板高度会算错。
3. `ToolPanel.PanelW = 300` 是常量，不随窗口宽变（是否要改成比例，待用户定）。
4. `ChatInput` 正文 `sizeDelta.x = 780` 是固定值，窗口拉宽后右边会空一块。

尺寸下限：`WindowLayoutCore.MinW = 360 / MinH = 320`（已写进判定核，B 阶段直接复用）。


## 八、交付前复核（同轮自查，改了四处）

用户要求"顺便复核一遍"。复核不是复读，下面是**真的找到问题**的四处：

### ① 视口读不到时会照钳 ⇒ 窗被钉到 (0,0)

`ClampWindowToViewport` 原来无条件 `Screen.width / sf` 兜底，但**兜底值也可能不可用**
（开窗首帧、`Screen` 异常）。0×0 视口会让 `ClampToViewport` 的合法域反转，
取中点正好是 0 ⇒ 玩家视角="拖一下窗就跳回正中"，**且没有任何报错**。

改成 `readOk` 两段判定，两个来源都读不到时**直接不钳**（这一帧原样返回，
下一帧正常读数自然纠正）。宁可这一帧不钳，也不写一个错值进去。

### ② 恢复时视口没就绪会永久放弃

`TickRestoreLayout` 原来第 2 帧就把 `_layoutRestored = true`，然后才读视口。
若那一帧视口还是 0×0，就会拿"按旧视口算的原始位置"直接写下去（换分辨率就在屏外），
而且**从此不再重试** ⇒ 每次开窗都在屏外。

改成：视口没就绪就 `return`（不置位），有界重试 `LayoutRestoreMaxWait = 3` 帧，
超时才放弃并打一行日志。异常路径也要置位（否则每帧重试刷日志）。

### ③ 关窗不清拖动状态

拖到一半按 F9/ESC 关窗，`_dragging` 还是 true。下次开窗时鼠标可能正好按着，
而 `GetMouseButtonUp` 那一帧已经错过 ⇒ 窗会**跟着鼠标跑**；
`_dragMoved` 残留还会让**没动过也落一次盘**。

`Close()` 里清两个位，且**不落盘**（拖到一半的中间位置不是玩家想要的）。

### ④ 拖动与恢复的钳制域日志会互相挤占

`_dragDiagLogged` 是 static，但恢复发生在开窗第 2 帧、**早于任何拖动**
⇒ 那行日志永远是"恢复"那次的值，而排障要看的正是拖动那次
（用户报"某个方向拖不动"时，只有拖动那行有意义）。

拆成 `_dragDiagLogged` / `_restoreDiagLogged` 两个标志，日志行带来源标签：
`[Chat] 窗口钳制域(拖动)：...` / `[Chat] 窗口钳制域(恢复)：...`

### 复核的变异验证（8 组全抓）

| 变异 | 结果 |
|---|---|
| 视口读不到时照钳（复现 0,0 跳回） | 抓到 |
| 未就绪分支一进去就置位（永不重试） | 抓到 |
| 未就绪分支不 return（掉进应用路径） | 抓到 |
| 关窗不清 `_dragMoved` | 抓到 |
| 关窗顺手落盘 | 抓到 |
| 拖动与恢复共用同一标志 | 抓到（**第一版漏了**，见下） |
| 日志行不带来源 | 抓到 |
| 恢复路径不传来源 | 抓到 |

> **第一版漏掉的那条值得记一笔**：断言原本写 `assert '_restoreDiagLogged' in body`，
> 变异"共用标志"时那个符号**仍然出现在赋值行里** ⇒ 断言照样通过。
> 改成盯住"判据那一行"（`bool logged` 所在行必须同时出现两个标志）才抓得到。
> 教训：**符号存在 ≠ 参与判据**，断言要盯"这一行在算什么"，不是"这个词出现过没有"。


## 九、拖动卡顿（用户实测后追加）

用户：「拖动确实可以了，只是拖动明显能感觉很卡，这怎么回事，可以优化吗？」

### 证据

`Player.log` 里拖动那 10 秒的帧率（`TickProfiler` 的 `5.0s 帧=+N`）：

```
185 → 184 → 23 → 78 → 53 → 38 → 28 → 62 → 44 → 77 → 117 → 146 → 184 fps
              └──────────── 拖动期间 ────────────┘
```

同一份日志里我们所有常驻回调合计 **< 1ms**：

```
diag n=193 avg=0.01ms max=0.95ms
teamjoin n=193 avg=0.02ms max=0.47ms
hotkeys n=193 avg=0.01ms max=0.09ms
```

⇒ **卡顿不在我们的 C# 代码里。**

### 根因：UGUI 的重算单位是「Canvas」不是「元素」

一个 Canvas 下所有 Graphic 共享批次；**改其中任何一个元素的 transform，
整个 Canvas 的顶点都要重算**。而 `BG` 底下挂着：

```
Scroll → Viewport → Content → N 行消息（每行头像 RawImage + 气泡 Image + Text）
Input / ChatInput / SendBtn / SkipBtn
8 颗顶栏按钮 + StatsBar + BusyLabel
ToolSidePanel / CompactEditPanel（各含 Scroll + 若干行）
```

拖动每帧写一次 `BG.anchoredPosition` ⇒ 每帧重算上千顶点。消息越多越卡 ——
正好对上"帧率是**逐渐**掉下去的"这个观察（不是一开始就卡死）。

### 解法一：给 BG 挂独立子 Canvas

子 Canvas 内部的顶点坐标**相对于它自己**；移动子 Canvas 只更新变换矩阵，
内部 N 个 Graphic 的顶点**一个都不用重算**。

类比：现在是把整个房间的家具挪一遍再拍照；挂子 Canvas 后是"拍一张照片，然后移动相框"。

三个必须注意的点（`EnsureOwnCanvas` 里逐条写着）：

| 点 | 为什么 |
|---|---|
| **不加 `GraphicRaycaster`** | 父级（游戏 UIMgr 挂的）已覆盖子 Canvas 内容；再加会让同一个点被拾取两次 |
| **`overrideSorting` 必须开** | 子 Canvas 默认 order=0 会按层级插队；开了才能保证整块 BG 与其它 UI 的先后关系不变 |
| **幂等** | `Init` 会因宿主重建重跑，已有 Canvas 不再挂第二个 |

还**不加 `CanvasScaler`** —— 缩放由父级统一决定，子 Canvas 再挂一个会缩两次（窗口变四分之一）。

### 解法二：写位置降频

`DragStepEpsilon = 2f`（画布单位，≈2 像素）：位移攒够 2 单位才写一次。
慢速微调时省掉大量"不到 2 单位"的写入；快速拖动时每帧位移远超 2，等于不降频（不拖尾）。

**基准必须是 `_dragAppliedPos`（上次真正写入的值），不能是 `anchoredPosition`**
——后者已被我们自己改过，拿它当基准等于阈值恒成立（变异验证专门钉了这条）。

松手时 `FlushDragPosition()` 补最后一笔，否则窗会停在前一格（"松手弹回去一点点"）。

### 解法三：落盘从「松手就写」改成「关窗才写」

日志实证一次拖动写了 **4 次**盘：

```
[WindowLayout] 已落盘：位置 -74,5
[WindowLayout] 已落盘：位置 -214,14
[WindowLayout] 已落盘：位置 245,113
[WindowLayout] 已落盘：位置 5,-28
```

说明"松手"在一次连续拖动里被识别了 4 次，而每次落盘都是**同步文件 IO**，直接阻塞主线程。
关窗只发生一次，代价摊薄到"关窗那一下"，而位置本来就只需要记住最后一次。

代价（用户已确认接受）：游戏崩溃/强杀时位置丢失，回到上次成功保存的位置。

### 本轮的变异验证（7 组全抓）

| 变异 | 结果 |
|---|---|
| 不挂子 Canvas（拖动仍卡） | 抓到（**第一版漏了**，见下） |
| 子 Canvas 加 `GraphicRaycaster` | 抓到 |
| 子 Canvas 不开 `overrideSorting` | 抓到 |
| 降频基准用 `anchoredPosition` | 抓到（**第一版漏了**，见下） |
| 松手不补最后一笔 | 抓到 |
| 关窗不落盘 | 抓到 |
| 松手又落盘（回到 4 次写） | 抓到 |

> **两条第一版漏掉的，是同一个错误犯了两遍**：
> 断言写 `assert "EnsureOwnCanvas();" in init`，而**注释里也写着这串**
> ⇒ 变异"把调用注释掉"时断言照样通过。
> 降频那条同理：`assert "_dragAppliedPos" in drag`，而它在**赋值行**也出现
> ⇒ 变异"判据改用 `anchoredPosition`"时照样通过。
>
> 修法都是同一条：**只看代码行（`ln.split("//")[0]`），并且盯"判据那一行"**，
> 不是盯"这个词出现过没有"。这与第八节 `_restoreDiagLogged` 那次是同一个教训，
> 本轮又犯了两次 —— 说明这个模式值得写成检查清单。


## 十、我引入的回归：子 Canvas 让整个窗点不动（11-21 下午，用户实测后）

用户报：「现在可以拖动，但是我尝试点击对话框却没有反应，点击对话UI的按钮也失去反应了？
这怎么回事，你是搞了个层级覆盖挡掉了按钮吗？」

**不是层级覆盖，是射线注册表的问题。而且我在注释里把机制写反了。**

### 真实机制

```csharp
// Unity 内部（Graphic.OnEnable）
GraphicRegistry.RegisterGraphicForCanvas(graphic.canvas, this);
//                                          ↑ 取**最近的** Canvas 祖先

// Unity 内部（GraphicRaycaster.Raycast）
var graphics = GraphicRegistry.GetGraphicsForCanvas(canvas);   // 只查自己所属 Canvas
```

给 `BG` 挂上子 Canvas 后：

1. `BG` 底下所有按钮/输入框的 `graphic.canvas` 变成**子 Canvas**
2. 它们被注册进**子 Canvas 那张表**
3. 父级 `GraphicRaycaster` 挂在 `UIChatAi` 上，查的是 `UIChatAi` 那张表
4. ⇒ **一个都看不到**。结果是**零拾取**，不是双倍拾取。

### 我写错的注释

> ① **不加 `GraphicRaycaster`**：父级（游戏 UIMgr 挂在实例根上的那个）已经覆盖
>    子 Canvas 的内容；再挂一个会让同一个点被拾取两次。

**这句话正好说反了。** 而且我已经把相反的正确结论写进了两条契约测试：

```python
assert "GraphicRaycaster" not in code, \
    "不许加 GraphicRaycaster（父级的已覆盖子 Canvas，再加会双倍拾取）"
```

测试锁住了错误认知，所以变异验证也"通过"了 —— **错误的设计 + 忠于错误设计的测试 = 全绿**。
这是本轮最值得记的一条：**测试只能验证"代码符合我的意图"，不能验证"我的意图是对的"**。

### 为什么拖动仍然可用（这掩盖了问题）

- `InTitleBand` 走 `RectTransformUtility.RectangleContainsScreenPoint` —— **直接算矩形，不经过射线**
- `PressOnSelectable` 走 `EventSystem.RaycastAll` —— **它也失效了**，但表现是"按下去不避让"，
  在按钮本身也点不动的前提下根本看不出来

### 修法

```csharp
private static void EnsureOwnRaycaster(GameObject go)
{
    if (go.GetComponent<GraphicRaycaster>() != null) return;   // 幂等：只能有一个
    go.AddComponent<GraphicRaycaster>();
}
```

两个位置都要调：

| 位置 | 为什么 |
|---|---|
| 挂完 Canvas 的主路径 | 新开的表需要有人查 |
| **幂等早退分支**（已有 Canvas 时） | 否则"第二次 Init 已有 Canvas → 直接 return"会漏掉 raycaster，重开窗后按钮又失灵 |

**必须补一个，且只能有一个** —— 多挂 = 同一点拾取两次 = 按钮回调触发两遍。

### 本轮变异验证（4 组全抓，其中两轮是我自己的断言写错）

| 变异 | 第一版 | 修正后 |
|---|---|---|
| 不补 raycaster（复现按钮全失灵） | **漏** | 抓到 |
| 幂等早退分支漏补 raycaster | 抓到 | 抓到 |
| raycaster 不判已有（会挂两个） | 抓到 | 抓到 |
| raycaster 静默失败（catch 里啥也不干） | **漏** | 抓到 |

两条漏掉的原因还是同一个模式，**这已经是第三次**：

1. `assert any("EnsureOwnRaycaster(" in ln for ln in code)` —— **注释里也有这串**
2. `any()` 跨了两处调用 —— 变异掉主路径那处，早退分支那处仍然通过

修法：**只看代码行（`split("//")[0]`）+ 两处分别断言 + catch 里必须有日志**。

> 这个模式值得固化成检查清单：
> **① 断言前先 `split("//")[0]`；② 有 N 个调用点就写 N 条断言，别用 `any()` 合并；
> ③ 断"判据那一行"而不是"符号出现过"；④ catch 分支要断"里面有日志"。**


## 十一、同一处的第二次纠正：别碰排序（11-21 第二版）

搭好 raycaster 之后我继续复核，发现 `overrideSorting` 那条判断**也是推的、不是测的**。

### 我第一版写的理由

> ② **`overrideSorting` 必须开**：子 Canvas 默认 sortingOrder=0，会按层级插入父级
>    同级参与排序；开了 overrideSorting 才能保证整块 BG 与其它 UI 的先后关系不变。

### 翻日志才看清的运行时事实

```
Canvas mode=ScreenSpaceCamera order=0   （16 次）← 游戏 UI 层
Canvas mode=WorldSpace order=20         （8 次）← 我们的实例根 UIChatAi
```

**这是两套渲染模式，不是同一套 order 序列上的两个值。**
子 Canvas 一旦 `overrideSorting=true`，它就变成**分离的排序条目**、拿自己那个 order
去参与比较 ⇒ 脱离 `UIChatAi` 的 WorldSpace 上下文 ⇒ 内容可能被压到 BG 背景之下
（窗口在、内容不见）。

而我们本来要修的**只是"重算粒度"，排序根本没坏**。
⇒ 保持 `overrideSorting=false`（默认），让子 Canvas 继承父级上下文。
**只动该动的东西。**

### 为什么 `UnreadBanner` 的写法不能照抄

```csharp
// UnreadBanner.cs —— 这是**对的**
canvas.renderMode = RenderMode.ScreenSpaceOverlay;
canvas.sortingOrder = 2400;
canvas.overrideSorting = true;
```

因为那是**自建根 Canvas**（挂 `g.root` 下、与游戏层级无关），必须自己决定 order 才能盖住东西。
**同样是 Canvas，一个是根、一个是子，结论相反。**

### 顺带加了运行时诊断

`DescribeCanvas()` 把子 Canvas 与它所属根 Canvas 的 `renderMode / sortingOrder /
overrideSorting / name` 打进日志。理由：这类事实**没法从源码推断**，
只能让它在日志里自己说出来 —— 本轮已经因为"靠猜"错了两次。

### 本轮变异验证（3 组全抓）

| 变异 | 结果 |
|---|---|
| 又开 `overrideSorting` | 抓到 |
| 设了 `sortingOrder` | 抓到 |
| 诊断行不报父级 | 抓到（**第一版漏**，因为没为诊断行写断言；补上后抓到） |

### 一个反复出现的模式，值得固化成清单

本轮**同一类错误犯了三次**（`_restoreDiagLogged`、`_dragAppliedPos`、`EnsureOwnRaycaster`），
全是"断言写成了符号存在性检查，而注释/别处也有那个符号"。

**四条检查清单：**

1. 断言前先 `ln.split("//")[0]` —— 注释里往往有同样的字面量
2. 有 N 个调用点就写 N 条断言，**别用 `any()` 合并**（变异掉一处，另一处仍然通过）
3. 断"判据那一行"在算什么，**不是**"符号出现过没有"
4. catch 分支要断"里面有日志"，不能只断"有 catch"（静默失败最难查）

还有一条更根本的（本轮两次都栽在它上面）：

> **测试只能验证"代码符合我的意图"，不能验证"我的意图是对的"。**
> `assert "GraphicRaycaster" not in code` 和 `assert "overrideSorting = true" in code`
> 都是**把错误的设计锁死了**，于是变异验证也全绿 —— 它只证明"代码忠实地实现了错误"。
>
> 唯一有效的对策是**回到运行时事实**（Player.log 里的 Canvas mode/order、
> Unity 的注册表机制），而不是在源码里找自洽。


## 十二、历史分页：从"本地切片"改到"服务端游标"（11-21 下午，用户两次否决后的定案）

### 第一次方案（被否决）：本地切片

我先做的是"`open_chat` 照旧拿全量，本地只渲染最近 40 条"。
用户否决，理由一句话：

> 最终的目标还是新协议，新请求，因为每次都拿全部的消息到客户端这也要等待很久

**他说得对，而且这是我的设计缺陷**：本地切片只砍掉了"渲染"，
**传输 + 解析那一大段一点没省**。真机 42.1KB / 169 条，每次开窗都要走一遍。

### 真机数据（姜萌账本）

| limit | 条目 | JSON |
|---|---|---|
| 0（全量） | 169 | 42.1 KB |
| 10 | 35 | 6.4 KB |
| **20** | **69** | **13.5 KB** |
| 30 | 109 | 20.9 KB |

账本 58 个 `turn/start`，投影出 **43 个完整回合**（差额是场合过滤：群聊回合不进 1:1 窗）。
定案：**单聊 20 回合 / 群聊 60 条**。

> ⚠ 单聊 `limit` 单位是**回合**，群聊是**条** —— 这不是笔误：
> 群发言没有回合概念（`project_room_history` 刻意不返回 turn 号）。

### 为什么必须用 `seq` 游标而不是 offset

账本里有 `history/delete` 删除注记与压缩纪要替换。**删掉一条，其后所有下标前移**
⇒ offset 翻页会重复或跳过。`seq` 单调递增、永不复用，游标不漂移。

### 为什么必须**两个**锚（这是实测踩出来的）

第一版只用 `before_seq`。实测翻页：

```
第1页 [38..58]   ← turn 38、42 出现了
第2页 [18..42]   ← turn 38、42 又出现了
```

原因：姜萌账本 43 回合里有 **2 个回合完全没有 seq**（`initiative` / `turn_error`
这类合成内容）。只按 seq 判，它们被当成"永远更早"⇒ **每页重复返回**。

修法：游标成对携带 `before_turn`，判据**优先用回合号**。
群聊不需要双锚 —— 群发言平铺，每条都有 seq，没有合成回合。

### 验证方式：逐条重建

不只测"条数对"，而是**逐条比对**：

```
每页  5 回合 → 累积 169 条 | 逐条完全一致: True
每页 20 回合 → 累积 169 条 | 逐条完全一致: True
每页 100回合 → 累积 169 条 | 逐条完全一致: True
```

从 5 到 100 的任意页长，翻页都能精确重建全部 169 条、顺序一致、不重不漏。
这是整个方案的地基 —— 漏一条就是"某句话凭空消失"，比卡顿严重。

### 关键实现点

| 点 | 为什么 |
|---|---|
| `InsertEarlier` 必须先记"已滚距离"→建行→再补偿 | `verticalNormalizedPosition` 是**比例**，内容变高后同一比例对应不同绝对位置 ⇒ 画面跳 |
| `SetAsFirstSibling` 必须**倒序** | 正序会让历史倒着排 |
| 插入时 `_histGroup = null` | 跨批的折叠组不该延续（这一段是独立历史段） |
| 翻到头两个锚**都**归 0 | 否则入口永远显示、点了返回空列表 |
| 失败时保留入口 | 让玩家能重试，不静默吞掉 |

### 变异验证（15 组全抓）

单聊 7 组：
游标用最小 seq / 忽略 before_turn / next_before_seq 用最小 / 翻到头仍报游标 /
不返回 next_before_turn / delete_history 漏传 / get_history 漏解析

群聊 6 组：
游标被忽略 / next_before_seq 恒 0 / next_before 用最后一条 /
get_room_history 漏传 / open_room 漏传 / room_flow 不透传

C# 8 组：
回到 limit=0 / 又做本地切片 / 窗口自己发 RPC / 插入不补偿滚动 /
挪到最前用正序 / Presenter 不订阅 / 群聊走错 RPC / 单聊不传回合锚

> **四条断言又是第一版漏掉后补的**（第四次犯同一个错）：
> - `any("EnsureOwnRaycaster(" in ...)` —— 注释里有同样的串
> - `any()` 跨两处调用 —— 变异掉一处，另一处仍通过
> - `assert len(parses) >= 2` —— 实际有 4 处解析，删一处仍然是 3 ≥ 2
> - `assert "before_turn" in handler` —— 注释里也有，删掉赋值照样通过
>
> 共同修法：**按 RPC 分段查、N 个调用点写 N 条断言、断"写进了请求"而不是"词出现过"**。
