# 事故复盘：「进游戏无法输入」（世界输入被门控）

- **日期**：2026-09-10（发现 ~10:52）～ 2026-09-11（复盘）
- **影响构建**：4415e0ec 起，至 3363f8af 修复
- **修复版本**：`MOD_Jgmg5L.dll` MD5 `3363f8af09b5c22de20abd7262e356fc`
- **状态**：修复已实施并部署，待游戏内验证（验证清单见 §7）
- **关联文档**：`README.md` 附录二 G（功能设计底稿）（未读三分流设计）

---

## 1. 结论速览

**双根因，同树不同果，互不为因果：**

| # | 根因 | 症状贡献 |
|---|---|---|
| ① | **UI 生命周期违约**：面板经 `g.ui.OpenUI` 登记进游戏 UIMgr 的"打开中"集合后，我们用 `SetActive(false)`/`Destroy` 关闭，绕过了游戏的关闭流程（动画 → `EGameTypeData.CloseUIEnd` 事件 → 从集合摘除），登记项**永久泄漏** → 游戏认为"一直有界面开着" → **世界输入（键盘/ESC/地图点击）被门控** | 不能动、ESC 无效、地图点不动 |
| ② | **立绘 API 误用**：对"3D 模型未加载进场景"的 NPC 调 `PortraitModel.CreateTextureInModelData` → 游戏内部创建 0 尺寸 RenderTexture 失败并打坏立绘状态 → 每帧重试失败、日志刷十几万行 → 主线程被 I/O 淹没（加重卡顿） | `RenderTexture.Create failed` 刷屏 |

两者**互不为因果**：存在"0 次刷屏但照样卡"的会话，也存在"刷屏但面板从未创建成功"的会话。

**关键判别特征**：HUD 自带按钮（背包/小地图/注入的「传」）**仍可点**——它们走各自独立回调，不经过世界输入门控；而键盘、ESC、地图点击全部失效。

---

## 2. 症状

- 读取存档后，角色不能移动；ESC 无效；点击地面/NPC 无反应；
- 游戏 HUD 按钮（背包、小地图、我们注入的「传」）仍可点击；
- Alt+Tab 切出再切回无效（排除窗口失焦）；
- `timeScale = 1`（非暂停）；`EventSystem.currentSelectedGameObject` 正常；
- 伴生：Player.log 每帧刷 `RenderTexture.Create failed: width & height must be larger than 0`（单次会话最多 17 万+ 行）；
- **只影响旧流程会话**：从启动就"从不创建面板"的会话一切正常。

---

## 3. 排查时间线

排查手段：**只读诊断 + 文件哨兵开关**（`_diag_*.txt`，`DiagSwitches` 每 120 帧复查，免重启切换）做 A/B 对照，而不是读栈推理。

| # | 实验（开关） | 结果 | 结论 |
|---|---|---|---|
| 0 | 初期推理 7 个假设（实例 Canvas 挡射线 / OpenUI 时机 / 面板根 active / RT 刷屏本身 / 层顶 UI 被占 / 隐形公告弹窗 / UIStartGameTip 引导） | 全部证伪 | 放弃读栈推理，转对照实验 |
| 1 | `_diag_no_mod`（mod 整体停用：`Harmony.UnpatchSelf()` + 销毁常驻 UI + 停帧回调） | **能走** | 是本 mod 引起 |
| 2 | `_diag_no_patches`（仅撤全部 Harmony 补丁，保留 UI/回调） | 不能走 | **排除 Harmony 补丁** |
| 3 | `_diag_no_panels`（从启动起禁用面板） | **能走** | 锁定"面板的创建/关闭动作" |
| 4 | `_diag_lazy_panels`（按需创建）后按 F10 开通讯录→关闭 | 关掉后坏 | 排除"创建时机"，锁定"开/关动作本身" |
| 5 | `_diag_no_portraits`（完全不调游戏立绘 API） | 还是坏 | 排除立绘导致门控 |
| 6 | 修注入丢失（见根因 ③）后重按 F10 | 面板能弹出，关闭后仍坏 | 注入是**另一个独立 bug**（已修），非本症状主因 |
| 7 | 销毁面板后再按 F10 | `OpenUI 返回 null` | **登记表仍挂着已销毁的死实例**——机制实锤 |
| 8 | 层顶监测快照（每 240 帧：层顶 UI/order/timeScale/UI 焦点/playerUnit/MapMain/我方面板开合） | 0 刷屏也卡过 | 刷屏与门控**无因果** |
| 9 | 反编考古（`types36_fulldll.txt` 完整源码 + UIMgr 签名表） | 契约实锤 | 见 §4 |

### 3.1 决定性日志证据

```
1372: [ModAbRes] 资源表丢失，重新注入 UIContactAi: 成功
1376: [AbContactPanel] g.ui.OpenUI("UIContactAi") 创建宿主...
1384: [AbContactPanel] 常驻宿主经 OpenUI 创建完成（游戏 UI 层…）
1388: [PortraitService] Fill #1 slot=.../ContactItem(Clone)/Avatar/Portrait unit=姜萌
1392: RenderTexture.Create failed: width & height must be larger than 0     ← 刷屏从这一刻开始
```

```
[AbContactPanel] OpenUI 返回 null                                            ← 销毁后同名 OpenUI 失败
[ContactPanelOpener] AB 常驻宿主不可用（OpenUI 失败），F10 本次不响应
```

```
[AbPanelProber] 层顶监测#N：层顶=MapMain(order=20) ｜ timeScale=1 ｜ UI焦点=(无)
             ｜ playerUnit=True MapMain=True ｜ 对话窗=未创建 通讯录=关着 配置=关着   ← UI 侧完全干净
```

---

## 4. 根因机制

### 4.1 根因 ①：UI 生命周期违约（不能动的真因）

游戏的 UI 管理契约是**异步事件驱动**的：

```
g.ui.OpenUI(type)      → 创建实例 + 登记"打开中" + 开启动画
g.ui.CloseUI(type, f)  → 启动关闭 → 关闭动画播完 → 触发 EGameTypeData.CloseUIEnd 事件
                         → UIMgr 从"打开中"集合摘除该 UI
                         → UIMapMain.OnCloseUIEnd(ETypeData) 恢复世界输入
```

反编证据：`EGameTypeData.CloseUIEnd` / `OneCloseUIEnd` 事件存在；`UIMapMain` 挂有 `OnCloseUIEnd(ETypeData)` 处理器；**神识传音（MOD_SSCYAI）全站使用 `g.ui.OpenUI` + 关闭一律 `g.ui.CloseUI(new UITypeBase(名字), false)`，从不自己 Destroy/SetActive**。

我们的三处违约：

1. `ContactPresenter.Hide()` / `ConfigPresenter.Hide()`：只 `SetActive(false)`，管理器根本不知道我们"关了"；
2. `ChatWindow.Close()`：同上（root-OFF）；
3. `DestroyResident()`：调了 `CloseUI` 但**紧接着 `Object.Destroy(gameObject)`**——把关闭动画连同协程杀掉，`CloseUIEnd` 永不触发 → 登记项泄漏 → 之后同名 `OpenUI` 返回 null（登记表撞死实例）。

登记项泄漏 → 游戏认为"有 UI 开着" → 世界输入被门控。`CloseAllUI(保留 MapMain)` 强清整个集合，所以 **F7 每次都能立刻恢复输入**；重新读档会重建集合，所以"从不创建"的会话正常。

### 4.2 根因 ②：立绘 API 误用（刷屏来源）

`PortraitModel.CreateTextureInModelData(modelData, rawImage, …)` 需要目标 NPC 的 **3D 模型已在场景中加载**（RenderTexture 尺寸按场景模型计算）。通讯录行渲染会给"不在当前地图"的好友调用它 → 内部尺寸算出 0 → RT 创建失败 → **打坏游戏立绘状态** → 游戏此后每帧重试失败 → 日志 I/O 淹没主线程。

对照：**对话窗立绘从未出过问题**——对话只在"点击了 NPC / NPC 同格走来"时打开，对象必然在场景中，且槽位是静态窗口里已布局完成的元素。

### 4.3 附属问题（排查中顺带发现并修复）

- **注入丢失**：游戏读档/进世界会重建 `g.res.allRes`，ModAbRes 在 Init 时注入的三个预制体条目会丢 → 之后 `OpenUI` 内部 `Instantiate(null)` 抛 `ArgumentException: The Object you want to instantiate is null`（从游戏 UIMgr 内部炸出，打断游戏 UI 流程）；
- **UnitAction OnEnd 钩子裸奔**：4 个 Harmony Postfix 无 try/catch，一旦抛异常会顺着游戏自身的"动作结束"链上炸，导致动作"结束不掉"、游戏停在"等待行动"。

---

## 5. 修复内容

### 5.1 方案 A：UI 生命周期遵守游戏契约（通讯录/配置）

| 环节 | 旧行为（违约） | 新行为（契约） |
|---|---|---|
| 创建 | 进世界自动创建（五重闸门）+ `SetActive(false)` 隐藏 | **按需创建**（自动创建废除）：`EnsureInjected` 校验/补注 → `GetUI` 复用优先 → `OpenUI` → `AddComponent` → `NormalizeLayout` → **保持显示** |
| 关闭 | `SetActive(false)` + 自己 `Destroy` | `AbXxxPanel.CloseViaManager()` = `g.ui.CloseUI(new UITypeBase(key,(UILayer)0), false)`，动画/事件/登记摘除全归管理器；`_resident`/`ContactPresenterInstance` 置空 |
| 销毁 | `CloseUI` 后立即 `Destroy` | **绝不自己 Destroy**；实例复用或由管理器处置 |
| 兄弟位次 | `SetAsFirstSibling`/`SetAsLastSibling` | **全部撤销**（改动层级顺序本身就是风险点） |

`AbPanelProber` 自动创建 machinery（TryCreate/五重闸门/巡检/冷却）废除；Frame 回调只保留只读诊断。`CanCreateNow()` 保留，作为 `EnsureResident` 的前置闸（世界未就绪/剧情窗展示中绝不 OpenUI）。

### 5.2 职责与面板分离（保住"面板不在场"时的功能）

```
【常驻层 · 静态、无 GameObject、与面板实例无关】
  ContactStore —— 纯数据：手动好友镜像 / 会话 mtime 索引 / 本地互动时间戳
                  （铁律：只存 id/名字/时间戳，绝不缓存 IL2CPP 引用）
  ContactDuty  —— 行为：Attach(ws) 订阅 UiEvent；
                  npc_reply 分流 = 同格→ChatLauncher.OpenForUnit（对话窗按需建）
                                  不同格→UnreadStore.Mark + HUD「传」按钮红点；
                  list_contacts / list_sessions / add_contact / remove_contact RPC → 写 Store；
                  Changed 事件 → 视图订阅刷新

【视图层 · 按需创建】
  通讯录面板 = 纯视图：打开时读 Store + 发 RPC 同步 → 渲染行/应用未读
  配置面板   = 纯视图（Python 重启中禁止关闭——状态机在实例上，中途销毁会丢流程）

【HUD】
  「传」按钮红点 = 未读提醒唯一载体（旧横幅是面板子节点，关闭态显示不出来）
```

### 5.3 立绘守卫

- `UnitPortrait.Render`：只渲染"与玩家同格（模型必然已加载）"的单位；其余记入 `_failed` 集合**永不重试**；
- `PortraitService.Fill`：`DiagSwitches.NoPortraits` 开关保留（诊断用）；前 5 次调用打日志（槽位路径 + 单位名）便于时序定位；
- **对话窗立绘不受影响**（直连 `Fill`，对象必在场景中）。

### 5.4 附带加固

| 项 | 内容 |
|---|---|
| `ModAbRes.EnsureInjected(uiName)` | OpenUI 前校验双 key，丢失即按原样重注入（**任何往游戏全局表注入的东西都要假设会失效**） |
| `UnitActionHooks` | 4 个 `OnEnd` Postfix 全部加 try/catch（异常会顺游戏动作结束链上炸 → 动作结束不掉 → 停在"等行动"） |
| `NpcPanelAddContact` | 加好友不再要求通讯录面板在场（走 Store/Duty） |
| `NpcInitiativeMonitor.BuildCandidates` | 候选④手动好友改读 `ContactStore` |
| `ConfigPresenter.Hide` | Python 重启进行中禁止关闭（防状态机丢失） |

### 5.5 文件清单

- **新增**：`csharp/ContactStore.cs`、`csharp/ContactDuty.cs`
- **修改**：`UI/ContactPresenter.cs`（瘦身为纯视图）、`UI/AbContactPanel.cs`、`UI/AbConfigPanel.cs`、`UI/ConfigPresenter.cs`、`UI/AbPanelProber.cs`、`UI/NpcPanelAddContact.cs`、`UI/MapMainContactButton.cs`（红点）、`UI/ChatWindow.cs`（撤兄弟位次；**生命周期未改**，见 §7）、`NpcInitiativeMonitor.cs`、`ModMain.cs`（Duty.Attach）、`UnitPortrait.cs`、`PortraitService.cs`、`DiagSwitches.cs`（开关族）、`UI/AbHotkeys.cs`（F2/F4/F5/F6/F7 诊断工具）
- **保留的诊断工具**：`DiagSwitches` 文件哨兵（noPanels/noHudButton/noMonitor/lazyPanels/noPortraits/createOnly/noPatches/noPatchDrama/noMod，120 帧复查免重启）；层顶监测/输入快照（`WorldInputSnapshot`）；`DumpZeroSizeRawImages`；热键 F2 快照 / F4 DumpUiTree / F5 点确认按钮 / F6 逐层+0 尺寸 / F7 CloseAllUI（应急）

---

## 6. 被推翻的假设（共 7 个，记录以免重蹈）

1. 实例上的 Canvas 挡射线 → 销毁也失效，证伪；
2. OpenUI 撞上剧情窗展示 → 闸门后仍复现，证伪；
3. 面板根 SetActive(false) 状态问题 → noPanels 下仍卡，证伪；
4. RT 刷屏本身拖垮输入 → 0 刷屏也卡，证伪（刷屏是伴生损害）；
5. 层顶被我们的面板占据 → 层顶=GameMemu 也能走，证伪；
6. 主界面隐形公告弹窗（UIStartGameTip，CanvasGroup alpha=0）挡输入 → 只存在于主界面阶段，证伪；
7. 游戏开局引导未完成导致 → 与所有存档无关、mod 停用即好，证伪。

---

## 7. 待验证清单（游戏内）

1. 进存档 → 能动；
2. F10 开通讯录 → 关闭 → **仍能动**；
3. F11 开配置 → 关闭 → 仍能动；
4. NPC 面板「加好友」→ 不需要通讯录在场即可生效；
5. 不同格 NPC 传音 → HUD「传」按钮亮红点；打开通讯录 → 未读正确显示；
6. 同格 NPC 主动传音 → 对话窗自动弹出；
7. 对话窗开/关 → 仍能动；对话立绘正常显示；
8. 日志 0 次 `RenderTexture.Create failed`。

**已知遗留**：**对话窗（UIChatAi）生命周期未改方案 A**——关闭仍是 root-OFF（对话历史在实例上，`CloseUI` 可能销毁实例丢历史）。若"对话窗关闭后输入失效"复现，下一阶段处理：关闭走 `CloseUI` + 打开时从 session 账本回放历史。

---

## 8. 方法论教训

1. **先做对照实验，不要读栈推理**：本轮 7 个假设全部被实验推翻，最后靠一组文件哨兵开关（免重启 A/B）逐步收敛；
2. **用游戏管理器管 UI，就必须全生命周期走它的契约**：开与关都是异步、事件驱动的流程，不能"创建走游戏、关闭走自己"地混用；
3. **往游戏全局表注入的东西要假设会失效**：使用前校验、丢失即补注（`EnsureInjected`）；
4. **给第三方 API 传参前先确认其前提**（如"模型必须在场景中"），失败必须不重试；
5. **只读诊断优先**：任何会改动游戏/存档状态的动作（清数据、自动清理、强关界面）必须先讨论——本轮两次擅自动手被用户叫停，均属正确干预。

---

## 附录：关键符号速查

| 符号 | 位置 | 说明 |
|---|---|---|
| `ContactStore` | `csharp/ContactStore.cs` | 静态数据层（好友镜像/会话索引/时间戳/Changed 事件） |
| `ContactDuty` | `csharp/ContactDuty.cs` | 静态行为层（UiEvent 分流/RPC/HUD 红点） |
| `AbContactPanel.CloseViaManager()` | `csharp/UI/AbContactPanel.cs` | 方案 A 关闭入口（CloseUI 交游戏） |
| `AbConfigPanel.CloseViaManager()` | `csharp/UI/AbConfigPanel.cs` | 同上（配置面板） |
| `MapMainContactButton.SetUnread(bool)` | `csharp/UI/MapMainContactButton.cs` | HUD 未读红点 |
| `UnitPortrait._failed` / `Render()` | `csharp/UnitPortrait.cs` | 立绘守卫（仅同格渲染+失败不重试） |
| `ModAbRes.EnsureInjected(uiName)` | `csharp/ModAbRes.cs` | 资源表注入校验/补注 |
| `DiagSwitches` | `csharp/DiagSwitches.cs` | 文件哨兵开关族（`F:\agent_loop\_diag_*.txt`） |
| `EGameTypeData.CloseUIEnd` / `OneCloseUIEnd` | 游戏 | UI 关闭完成事件（UIMapMain.OnCloseUIEnd 消费） |
| `UIStartGameTip` / `ConfStartGameTip` | 游戏 | 主界面"公告"弹窗（本事故中曾为误导项：CanvasGroup alpha=0 不可见但存在） |
