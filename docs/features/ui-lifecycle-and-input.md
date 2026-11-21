# 世界输入冻结（09-10 事故）—— 结论与现行规则

> **一句话**：进存档后人物不能动、ESC 无效、地图点不动，但 HUD 自带按钮（背包/小地图/「传」）**仍可点**。
> 双根因、互不为因果：**UI 生命周期违约**（登记项永久泄漏 ⇒ 世界输入被门控）
> 与**立绘 API 误用**（0 尺寸 RenderTexture 每帧重试 ⇒ 主线程被刷屏淹没）。
>
> 完整复盘（7 个被证伪的假设、逐条 A/B 实验）在
> [`docs/archive/2026-09-10-世界输入冻结.md`](../archive/2026-09-10-世界输入冻结.md)。

---

## 1. 双根因

| # | 根因 | 症状贡献 |
|---|---|---|
| ① | 面板经 `g.ui.OpenUI` 登记进游戏的"打开中"集合后，我们用 `SetActive(false)`/`Destroy` 关闭，绕过了游戏的关闭流程（动画 → `CloseUIEnd` 事件 → 从集合摘除）⇒ 登记项**永久泄漏** ⇒ 游戏认为"一直有界面开着" ⇒ 世界输入被门控 | 不能动、ESC 无效、地图点不动 |
| ② | 对"3D 模型未加载进场景"的 NPC 调 `PortraitModel.CreateTextureInModelData` ⇒ 内部算出 0 尺寸 ⇒ RT 创建失败 ⇒ 打坏立绘状态 ⇒ 每帧重试刷屏（单次会话最多 17 万+ 行） | 主线程被 I/O 淹没，加重卡顿 |

**判别特征**：HUD 自带按钮仍可点 —— 它们走各自独立回调，不经过世界输入门控。

## 2. 现行规则（这两条至今有效）

### R1. 打开走 `OpenUI`，关闭就必须走 `CloseUI`

```csharp
// 开
var ui = g.ui.OpenUI(new UITypeBase("UIContactAi", (UILayer)0));
ui.gameObject.AddComponent<AbContactPanel>();
// 关 ★ 绝不自己 Destroy / SetActive(false)
AbContactPanel.CloseViaManager();   // = g.ui.CloseUI(new UITypeBase(key, 0), false)
```

**"创建走游戏、关闭走自己"是这次事故的原始形态。** 反过来（关闭走 `CloseUI` 却紧接着 `Destroy`）
同样会杀掉关闭协程，也是错的。

护栏：`tests/test_notice_panel_contract.py::test_panel_goes_through_ui_manager_not_self_made_canvas`
（断言 `g.ui.OpenUI(` / `g.ui.CloseUI(` 都在，且 `Hide` 里不许出现 `SetActive(false)`）。

### R2. 立绘只在"模型真的在场景里"时渲染，失败不重试

`UnitPortrait.Render` 只渲染同格单位，其余进 `_failed` 集合**永不重试**。
给第三方 API 传参前先确认它的前提，失败必须**不重试** —— 每帧重试是这次把卡顿放大成灾难的原因。

### R3. 往游戏全局表注入的东西要假设会失效

使用前校验，丢失即补注（`ModAbRes.EnsureInjected`）。
判据：`OpenUI` 返回 null 却没人报错 —— 实测过"登记表里挂着已销毁的死实例"。

## 3. 被删掉的那道闸（容易踩回去）

`AbPanelProber` 原先有一套**自动创建机械**（`TryCreate` / 五重闸门 / 巡检 / 冷却），
已随面板改按需创建而**整体废除**，`OnFrame` 现在只做只读诊断。
`CanCreateNow()` 保留，作为 `EnsureResident` 的前置闸（世界未就绪 / 剧情窗展示中绝不 `OpenUI`）。

其中原本的 ④「剧情窗正显示」**单独删掉了**（09-21）：它声称防的正是 09-10 复盘里
**第 2 号已证伪假说**（"OpenUI 撞上剧情窗展示"，加闸门后仍复现），也就是说它在面板闸门上
从来没有保护过任何东西；而它会把玩家亲手点的「对话」按钮当场拦死。

## 4. 已知遗留

**对话窗（`UIChatAi`）的生命周期没改成方案 A** —— 关闭仍是 root-OFF。
理由：对话历史在实例上，`CloseUI` 可能销毁实例丢历史。
若"对话窗关闭后输入失效"复现，下一阶段处理：关闭走 `CloseUI` + 打开时从 session 账本回放历史。

## 5. 方法论（比结论更值钱）

1. **先做对照实验，不要读栈推理**：这轮 7 个假设**全部**被实验推翻，最后靠一组文件哨兵开关
   （`_diag_*.txt`，每 120 帧复查、免重启 A/B）逐步收敛。
2. **只读诊断优先**：任何会改动游戏/存档状态的动作（清数据、自动清理、强关界面）先讨论 ——
   这轮两次擅自动手被用户叫停，均属正确干预。
3. **"没看到某条日志"不等于"某件事没发生"** —— 这个坑在 10-22 论道排查里又踩了一次。

---

## 6. 相关代码

| 关注点 | 文件 |
|---|---|
| 面板生命周期（`OpenUI` / `CloseUI` 配对） | `csharp/UI/AbChatPanel.cs`、`AbContactPanel.cs`、`AbConfigPanel.cs` |
| 立绘守卫（0 尺寸不重试） | `csharp/portrait/BigPortrait.cs`、`PortraitCache` |
| 注入登记与撤销 | `csharp/UI/UiComposer.cs`、`AbPanelProber.cs` |
| 契约测试 | `tests/test_big_portrait_contract.py`、`tests/test_ui_type_registration_contract.py` |
