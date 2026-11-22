# C# 侧铁律（IL2CPP / 线程 / 数据源 / 本地化）

> 数据源：`docs/reference/csharp-iron-laws.md` §D.1~D.7 拆出（2026-11-20）。
> **写 C# 之前必读。** 这些是「踩过之后不会再踩」的硬约束，不是建议。
>
> 带日期的排查叙事不在这里 —— 那些在 `docs/archive/`。这里只留**至今仍成立的约束**。

---


## 一、IL2CPP 线程与类型铁律

* **★ 对 Il2Cpp 返回值做向下转型（`is 子类` / `as 子类` / `(子类)x`）恒失败 ★**（**全项目铁律**）。机理：本机 Unhollower（MelonLoader 0.5.x；`UnhollowerBaseLib` 里**没有** `Il2CppObjectPool`）生成的属性/方法 getter 一律「`newobj 声明类型(ptr)`」，**不做运行时类型解析**，返回值 CLR 类型恒为**声明类型**。三档后果：`is 子类` 恒 false（**静默走错分支**）、`as 子类` 恒 null（**静默**）、`(子类)x` 抛 `InvalidCastException`「Specified cast is not valid.」。
  **泛型返回值不受影响**——`GetComponent<T>` / `GetBuilds<T>()` / `List<T>[i]` 的声明类型就是 `T`，具体类型保真（故 `GetChild` 是坑而 `GetComponent` 不是）。
  通解：只调**声明类型上就有**的成员（如用 `Graphics.Blit(Texture, RenderTexture)` 而非先转 `RenderTexture`），或 `IL2CPP.il2cpp_object_get_class(x.Pointer)` + `il2cpp_class_get_name` 取真类再决定。
  **踩中案例**：09-13 通讯录头像恒占位——`PortraitCache.SafeCapture` 原按 `is RenderTexture` / `(Texture2D)` 分派取像素，每次捕获都抛该异常，`portrait_cache\` 一张 PNG 都没落盘。已改为 `Graphics.Blit` 单路径（缩放交 GPU，CPU 只做圆形 alpha 烘焙）。
  **对照组**：`GetChild` 声明返回 `Transform` → `GetChild(i) as RectTransform` 恒 null，一律 `GetComponent` 转。
* **★★ 裸 `const char*` 型 il2cpp API 绝不能用 `Il2CppStringToManaged` 解码——会硬崩且托管 catch 抓不到 ★★**（09-13 实机崩溃根因）。
  `UnhollowerBaseLib.IL2CPP` 里两类指针在 C# 签名上**都是 `System.IntPtr`，编译器无法区分**：`Il2CppStringToManaged(IntPtr)` 要的是 **`Il2CppString*`（托管字符串对象）**，其 IL 是 `il2cpp_string_length(ptr)` → `il2cpp_string_chars(ptr)` → `new string(...)`，即**按对象头读 length 字段**；而 `il2cpp_class_get_name` / `il2cpp_class_get_namespace` 返回的是**裸 `const char*`**。把 `char*` 喂进去 → 读到的「长度」其实是 C 串前 4 字节（`"Rend"` = `0x646E6552` ≈ 16.8 亿）→ **越界读数 GB → 访问违例硬崩**，日志只留一行 `Crash!!!`，无任何托管异常。
  **正确写法**：`Marshal.PtrToStringAnsi(IL2CPP.il2cpp_class_get_name(cls))`（逐字节读到 NUL，无越界可能；类名是 ASCII，ANSI 解码无损。.NET Framework 4.7.2 无 `PtrToStringUTF8`）。
  **诊断手法**：这类崩溃「无异常、无日志、断点前最后一条日志之后」，靠**副作用反推**——当时 `portrait_cache\MTpQXJ.png` 已成功落盘（62KB/256² 合法 PNG），而 `MTpQXJ` 是 `PumpCapture` 倒序遍历的第一个任务、`RealClassName` 又是 `SafeCapture` 里 `WriteAllBytes` 之后唯一还没跑的语句 → 一眼锁定。**别信"native GPU 操作崩溃"的直觉**：产物在，就说明 GPU 段无恙。
* **★★ 异常绝不许丢进「没人 await 的 Task」——那等于永久静默 ★★**（09-13 实机教训）。`MainThreadDispatcher.OnUpdate` 原本只有 `catch (e) { job.TCS.TrySetException(e); }`，而调用方 `WsClient` 派发 UI 事件时**丢掉了 `Enqueue(...)` 返回的 Task** → 未观察的 Task 异常被 .NET 静默忽略：**不崩、不进日志、什么都不留**。
  后果：**UI 事件处理链（`OnUiEvent` → `OnStepEvent`/`OnReplyEvent` …）里任何异常都永久无声**。09-13「NPC 回应中…」卡死的排查就卡在这——`ChatPresenter.OnReplyEvent` 渲染半途抛异常 → 后面的 `SetBusy(false)` 被跳过，而日志里一条异常都没有，只能靠读代码反推。**已在 catch 里补 `ModMain.P`（异常照旧回填 TCS，语义不变）。**
  **通用规矩**：① 任何 `catch` 至少要留一行日志；② `Enqueue`/`Task` 式 API 的返回值要么 await、要么在内部 catch 里留痕；③ **收口/复位类操作（清忙标、关 loading、解挂起）必须放 `finally`** —— 它们的语义是"这一帧结束了"，绝不能依赖前面渲染成功。
* **★★ 同一份数据有「原始层 / 解码层」两套字段时，类型可能完全不同——取错层会静默全空 ★★**（09-13 实机教训）。
  `DataUnitLog.LogData` 上四个成员名字像一家人，类型却分两族（`MelonLoader\Managed\Assembly-CSharp.dll` 实证）：
  `allLog`/`allVitalLog` = `List<Il2CppStringArray>`（**一行 = 编码串切段，元素是 `string[]`**）；`allLogData`/`allVitalLogData`（属性 getter）= `List<LogItemData>`；`_allLogData`/`_allVitalLogData` = 解码缓存本体。
  取数函数只认 `LogItemData`（读 `month`/`logs`/`subLogs`/`DataToString()`），一旦拿到原始层：**三处取值全抛异常，而三处都裹在 `catch { }` 里** → 每条静默退化成 `"(无文本)"`，日志一条错都没有。
  **判据**：输出条目里**连 `month` 字段都缺失**（`{"text":"(无文本)"}` 而不是 `{"month":0,"text":"(无文本)"}`）——只有"赋值抛异常"才会整个字段缺失。
  **通用规矩**：① 有两套同义数据时，先确认哪套是**消费者（游戏自己的 UI/逻辑）实际用的那套**（此处 `UINPCInfoLog.logDatas` = `List<LogItemData>`，即解码层），跟消费者对齐；② 兜底分支别信"名字更底层所以更新鲜"的直觉，先实证元素类型；③ **兜底层返回前必须判 `Count > 0`**，否则空的高优先级层会把低优先级兜底整个短路（原写法 `if (v != null) return v;` 就有这个毛病）；④ 兜底解码结果加**内容校验**（此处只认含汉字的结果），防止编码残渣混进模型上下文。
  **防回归**：`tests/test_log_layer_contract.py` 钉住源码顺序契约（解码层必须先于原始层被尝试），倒回事故写法立刻红。
* **★ 跨层顺序约定：取数处统一成「头 = 最新」，别让每个消费者各自猜 ★**（09-13 实机教训）。
  游戏 `LogData` 的 `allLog/allVitalLog` 是**升序（旧→新）**——真机实证：`regular` 桶里「初入八荒。」（创角）排在「与云含交谈…」**之前**，后者逻辑上必然更晚，而全链路此前没有任何排序。
  但两个消费者都是按「头 = 最新」写的：`ToolExecutor.PageLogs` 按 `(page-1)*5` 从**头**切片且 `log_page` 默认 1；`RecentTexts` 取 `arr[0..2]`/`arr[0..1]` 却喂给 L1 的**「近况」**段。结果：默认第 1 页永远是开局那几条，「近况」段内容是「(1年1月) 初入八荒。」。
  **判据**：同一个列表被两个以上消费者按"前 N 条"消费、而它们的语义分别是"最新"和"最早"时，就说明顺序没在取数处定死。
  **通解**：在**取数处一处**反转（`UnitSnapshot.LogsArr` 倒序遍历），让"头 = 最新"成为全链路真命题（与 `QueryWorldEvents` 既有的 `倒序 = 最近在前` 约定一致）；并加 **方向自检日志**（`OrderInfo` 期望打出 `降`，异常标 `升!`/`乱序!`）——游戏若改了写入方向会立刻显形，而不是靠用户发现"近况怎么是陈年旧事"。翻页文案也要跟着改（`has_more` 在新→旧下指"还有**更早**的"）。
* **★ 确认窗立绘位：左位恒为玩家、右位为对方 —— 传反了不报错，玩家只会看到"两个自己" ★★**（09-12 / 09-13 各踩一次）。
  `ShowDramaService.ShowConfirm/ShowConfirmSimple(ModIds.XxxBase, left, right, …)`：`left` = 屏幕左位 = **玩家**，`right` = 屏幕右位 = **对方**（09-08 用户拍板，反编依据 `DramaData{ unitLeft = g.world.playerUnit, unitRight = unit }`，对齐游戏原版神识传音窗方向）。
  **为什么难发现**：编译器管不到、传反不抛异常、日志也正常（正文与选项都对），只有玩家在屏幕上看见两个自己的立绘与名字。判据就一句：**实机"右侧立绘是玩家" = 左右传反了**。
  两次实例：① 09-12 自主互动确认窗把 `wub`(NPC) 放左位（修正留痕在 `NpcInitiativeMonitor.cs:352`）；② 09-13 `trade` 初版把 `(seller, buyer)` 直接当立绘位传 —— 卖家是 NPC、买家是玩家时右位落成玩家。
  **通用规矩**：两位当事人**都不固定是玩家**的新窗（如买卖、传功、中介），必须按"谁是玩家"推导：玩家在左、另一方在右；双方皆非玩家时退化成"本人左 / 对方右"。
  **防回归**：`tests/test_drama_slots_contract.py` 全仓扫描所有 `ShowDramaService.ShowConfirm*` 调用点，左位实参不得是 NPC 侧标识符（npc/wub/seller/buyer）——两次事故的写法都会被打红。
* **不许污染核心业务层**（用户红线）：业务文件（`ChatPresenter`/`ChatWindow`/`ConfigPresenter`/`ContactPresenter`/`ToolExecutor`/`DramaGate`/`ShowDramaService`/`UnreadStore`/`PortraitService` 等）除诊断开关外零改动；改任何业务文件前先说明理由。
* **自定义 MonoBehaviour 必须有 `IntPtr` 构造**：`public X(IntPtr ptr) : base(ptr) { }`，否则 `RegisterTypeInIl2Cpp` NRE，且**半途注入会毒化 il2cpp 类型系统**。官方范式：壳由 `g.ui.OpenUI` 托管游戏自带 `UIBase`，mod 类只做逻辑组件（基类用普通 `MonoBehaviour`）。
* **`ClientWebSocket` 在本环境不可用，勿再引入**：.NET Framework 版在 Windows 上委托**原生 `WebSocketProtocolComponent`**，在 IL2CPP + MelonLoader 的 mono 宿主里首次 `ConnectAsync` 即卡死，随后原生崩溃。**现役 `csharp/WsTransport.cs`** = 纯托管 RFC6455（`TcpClient` + 手写握手/帧：客户端掩码、Ping→Pong（Python `websockets` 库 20s 保活）、Close、分片聚合、内部写锁）。
* **后台线程绝不碰 UnityEngine——`Debug.Log` 也不许**：IL2CPP 非主线程 interop 会崩。**现役 `ModMain.P()` 线程感知**：主线程走 `Debug.Log`（进 `Player.log`），后台线程 / Init 前走 `Console.WriteLine` + `MelonLoader/Logs/AgentLoopBridge-thread.log`（带时间戳，可 grep）。
* **IL2CPP 集合不能 `foreach`**（用 `Count`/`Length` + 索引器，或 `dynamic` 遍历规避泛型参数被抹掉；`dynamic` 绑定需 csproj 引 `Microsoft.CSharp`）。遍历子节点用索引循环（`childCount + GetChild`），`foreach(Transform)` 抛 `InvalidCastException`。
* **泛型复数 `GetComponentsInChildren<T>` 在此包疑似返回空**（凡用它的搜索一次没中过）→ 搜索一律改**递归索引 + 单数 `GetComponent`**。
* **UI 事件/委托桥**：`UnityAction` 委托桥（`op_Implicit` = `DelegateSupport.ConvertDelegate`）一直存在，缺的只是写法——直接 `AddListener(() => {})` 报 CS1660，须「三步写法」（`System.Action → UnityAction → AddListener`，封装 `ClickUtils.Attach`）；UnityEvent 家族（`onValueChanged`/`onEndEdit`）在 IL2CPP 下不可靠，先转存 `System.Action<string>` 再 AddListener；`InputField` 的 `onSubmit` 不可靠，在 `Update` 里轮询 `isFocused + Enter` 提交；`MakeSlicedSprite` 已钳制 `border < size/2`（越界 → 切角覆盖整张纹理 → 精灵全透明/"十字"化），圆形一律 `MakeCircleSprite`（逐像素抗锯齿真圆）。

## 二、工程 / 构建 / 部署链

* **老式工程**：`csharp/AgentLoopBridge.csproj` 新增 `.cs` **必须登记 `<Compile Include>`**（`UI\*.cs` 是通配）；`GameManaged`/`GameRoot` 需指向本机游戏安装路径；`.NET Framework 4.7.2` 对齐 MelonLoader 0.5.4（本机仅装 4.8.1 目标包，故以 `v4.8.1` 验证）。
* 构建：`cd F:/agent_loop/csharp && dotnet build AgentLoopBridge.csproj -c Debug -v minimal`；`Debug` 与 `Release` 的 DefineConstants 均含 `MELONLOADER;AB_UI`。
* 部署：`bin\Debug\MOD_Jgmg5L.dll` → `E:\SteamLibrary\steamapps\common\鬼谷八荒\ModExportData\Mod_Jgmg5L\ModCode\dll\MOD_Jgmg5L.dll`，**部署后必须 MD5 两端比对**（历史备份命名 `.bak_MMDD_*`）。
* 并行修改提醒：若另有工具/会话同时改 `F:\agent_loop`，构建会互相覆盖——**以 `bin\Debug` 时间戳 + 部署 MD5 为准**。
* 官方渠道：模组走游戏「模组编辑器」导出的 `ModCode\dll\{namespace}.dll`（本模组命名空间/程序集名 `MOD_Jgmg5L`），由官方桥 `GGBH_MOD`（MelonLoader 日志里的 "ModName v1.0.0.0 by GGBH_MOD" 就是它，**不是我们的 mod**）反射调用 `{namespace}.ModMain.Init()/Destroy()`；**命名空间、程序集名、导出路径三者必须一致**，否则 DLL 加载失败（`ModMainEntry.cs` 是转发包装，缺此层 `Init` 静默失效）。
* **Mod MID 与官方编辑器配置管线**（09-11 实证，立绘剧情窗的数据来源）：
  * **MID** = 编辑器为本模组随机分配的 int 身份号（interop 字段 `ModData.excelMID`）。本模组 **-803158451**。**「重置ID」按钮永远不要点**——所有引用作废。
  * **配置表**：xlsx 放编辑器工程配置目录（`F://mod//ModProject_Jgmg5L//ModProject//{ModExcel,Excel}//`），**ID 字段写 `MID&偏移`**（如 `MID&100`），导出时真 ID = MID+偏移；表头三行（中文名/字段名/类型）从 `modFQA\配置修改教程\配置表头\*.xlsx` 抄。已配三张：DramaDialogue 8 窗 / DramaOptions 16 选项 / LocalText 24 键。C# `ModIds.Mod` 必须与工程 xlsx 的 MID 同源同步。
  * **导出**：编辑器"导出模组"→ 生成 `ModExcel/*.json`（**`1b8bg-` 加密**）。**必须由编辑器导出**——手写明文放 mod 目录会让 `ModImportTool` 解析炸、阻断启动（09-11 事故）。
  * **加载**：`ModMgr.LoadAllMod` → `ModImportTool` 解密合并进全局配置表 → `UICustomDramaDyn(窗ID)` 查表命中才渲染（**不命中 = 静默不开窗**）。

## 三、UI 生命周期与注入铁律（方案 A 的操作级细节）

* **生命周期 = 方案 A**：面板**按需创建**（自动创建废除）→ `ModAbRes.EnsureInjected` 校验/补注 → `g.ui.GetUI` 复用优先 → `OpenUI`；关闭一律 `g.ui.CloseUI(UITypeBase, false)` 交游戏（`AbXxxPanel.CloseViaManager()`）。**绝不自己 `SetActive(false)`/`Destroy`、绝不动 UI 层兄弟位次**——否则游戏"打开中"登记项永久泄漏、世界输入被门控（09-10 事故）。
* **绝不用"组内/树内任意首 Button"当克隆模板**：NPC 面板树第一个 Button 是 `G:btnClose`——旧代码 `GetComponentInChildren<Button>(true)` 取到它，克隆的 × 钉在真 × 上（真 × 收不到点击、克隆体无文本子节点、点"×"实为静默加好友）。**模板只允许 = 操作行（带文字）或明确按名节点**；找不到就安静返回 false 等重试。
* **按名找节点必须容忍 `G:` 前缀**：本作 UI 节点**真名带前缀**（`G:btnEmail`/`G:goGrid1`/`G:btnFateFeature` 均已实证），反射拿到的**字段名不带**。只按字段名找 → **静默全 miss** → 退到兜底 → 捡一个错的节点（09-07 HUD 圆钮、09-13 剧情窗按钮，**同一个坑踩了两次**）。判据：`NameIs(actual, want)` 同时认 `want` 与 `G:want`。
* **克隆模板必须校验"可用"而非"激活"**：`activeSelf` 为真不代表玩家看得见（父链可能整条关着、`Image` 可能 alpha=0、节点可能 0 尺寸）；模板还必须**自带可改文本**，否则克隆出来是个"写不上字的空热区"。**"已注入"日志 ≠ 玩家看得见**——排查一律先看注入诊断打出的**模板真名 + 克隆 `activeSelf`/`activeInHierarchy` + 屏坐标**三件套。
* **任何克隆行/按钮不得放进受管操作网格**：游戏 `UIOperationGroup.UpdateHandleInput` 逐帧按 `operationItems` 处理网格区输入，操作行是"组的行"，点击不靠 `Button.onClick` 而靠组按条目映射——克隆行放进 `G:goGrid1` 首位会**可见但点不响**。现役做法＝按钮移出网格，挂面板体 × 左侧空位（该层无组机制）。
* **注入去重登记必须在成功之后**：`Injected.Add(instance)` 放在注入尝试之前 → 轮询回调先查 `Injected.Contains` 恒真直接 return，四档轮询永不补注。轮询 guard 与登记必须**同一集合、同一时序**。
* **切页签自愈（v2）**：操作列 `Image/Group:UnitInfo/LanguageGroup/G:goGrid1` 是**跨页签常驻的同一列**，切页签重建的是它的**行子节点**。页签是 `UINPCInfoBase.tglTitle1..7` 七个 Toggle，切换走其 `onValueChanged`（旧假设 `UINPCInfo.Method_Private_Void_Toggle_0` postfix 已被两个完整会话的 `Player.log` 推翻）。**现役**：`WireTabToggles` 直接监听七个页签 → `OnPageSwitched` 幂等补注 + `0.4/1.0/2.0s` **有界**验证。**无常驻轮询**（用户定调"不预支开销，全由真实事件驱动"）；0.8s 常驻巡检方案**已否决，勿回退**。
* **打开链路全通但画面不可见** = 同层多个 ScreenSpaceOverlay Canvas 按 sortingOrder 排被压到游戏 UI 之下 → 显形后 `Canvas.sortingOrder=30000` + `SetAsLastSibling()`；仍不可见再查宿主父链 `activeInHierarchy`、根 RectTransform 尺寸/锚点、`CanvasGroup alpha=0`。
* **UI 让位 hook —— 已退役（10-16 更正）**：本条描述的 `DramaGate.SendChatBehindHook/SendChatBehind()`（对话窗降到 100 让原生弹窗盖上来、重开自愈回 30000）**代码里已经全部删除**（见 `docs/archive/journal/2026-09-09.md`「DramaGate 退役…全删」）。现行做法是**架构层面的**：`AbChatPanel` 从"自建 Overlay Canvas sortingOrder 30000"改为走 `g.ui.OpenUI + (UILayer)0`，根 Canvas 的 sortingOrder 归 0，**后开的原生窗天然盖在本窗之上**（`AbChatPanel.cs` 文件头迁移史 + `AbChatPanel` 内注释「确认窗/论道弹窗后开自然盖住本窗」）。⇒ **不要再引入任何"让位"机制**：没有 `sortingOrder` 要调，也不需要恢复 hook。
* **`g.res.Load` 失败会写 `null` 缓存**，同会话永久命中 `null`，必须重启游戏重测。**`g.ui.OpenUI` 查的是游戏 UI 管理器另一本注册表**（`allAB=0`），`ModAbRes` 只修了 `allRes` 那本——所以对话/配置/通讯录改走 `g.res.Load + Instantiate` 的常驻宿主（`TryCreateResident`），`OpenUI` 路径废弃。
* **`HotkeyPoll` 已删除**（注册 + 方法）：配置常驻后 `ConfigPresenter.Update` 自带 F11 轮询，并存会同帧双 toggle 互抵（`GetKeyDown` 对同帧所有 `Update` 为真）。
* **Unity 预制件 YAML 原位手改会被"生成菜单"覆盖**——手改后**勿再跑生成菜单**（`BuildConfigPrefab`/`BuildContactPrefab` 会从 builder 重新生成）；追加类菜单（`AddConfigTooltip`/`AddChatPortraitSlots`）幂等不覆盖。

## 四、UnitAction / world_ai_action 完成回调判据（不许再用"立即返回"）

机制：工具发出动作后返回 `DramaGate` pending 标记（`WsClient` 憋住 response，Python future 挂起 → LLM 不说话）；玩家在**游戏原生界面**操作完 → 引擎回调 → `Resolve(slot, 真实结局)` 补发 → LLM 按真实结局收口。撞窗忙键共用 `ModIds.AiActionGate`；`DramaGate` 120s 自动 Resolve"玩家长时间未响应"。

| 动作 | 判据 |
|---|---|
| `lun_dao` / `shuang_xiu` | `StartToUnitAiActionDeferred`：`WorldUnitAIAction1037`/`1031` 挂 `Action<bool>` 回调，**`p==true` 即"完成"**。真机观察点 `[AiAction] lun_dao 完成回调 ok=True` |
| `spar` | **挂起等玩家选 → 应战之后还要跨过整场战斗**（09-14 修判据；09-22 修两条前提）。原生剧情 **21204** 弹「好，就让我和你切磋一下 \| 我现在没有空」，C# 在 `UIDramaBase.ClickOption` 的 **prefix** 上认出这一对选项（`UI/DramaDrillChoice.cs` 的 `DrillChoiceGate`）→ 婉拒当场定案；应战则把登记 `Take` 走交给 `drama/SparOutcome.cs`，等战斗结果。<br>**09-22 两条被真机证伪的前提**（别改回去）：① 判据原来挂 **postfix** —— 引擎在 `ClickOption` **内部同步**进战斗并跑完 `OnEnd`（`Player.log 4895→4899`），postfix 永远晚于兜底，等它轮到`req_id` 已被 WsClient 摘走（`WsClient.cs:373`），于是**玩家应战并打完了，模型拿到的却是「但你未作答复」**；② 原注释写「`OnEnd` 在同意路径要等整场切磋打完」—— 实测它在**进战斗那一刻**就触发。选项 id 仍是配表事实（`DramaDialogue` id=21204 `options="212042\|212041"`），**免校准**这条不变；`isDrillComplete` 仍**禁止读**（无样本，同类字段 `isInviteComplete` 猜错过的教训在 yao_yue 行）。<br>**结局怎么判胜负**：不判。战后那几行原文（实证：**同一个剧情窗翻两页** —— `dramaId 21401`「厉害，我输了。」说话人=玩家 / `21402`「看来是我赢了！」说话人=NPC，原文出自 `RoleLogLocal` 105981/105982；第二页是玩家点「继续」经 `UIDramaDialogue:OnNextClick → NextDialogue` 出来的，`Player.log 5012/5045/5049/5064`）被 `DramaAiOption` 的**闸 1.5 认领分支**顺手捕获 —— 复用 `DetectSpeaker` 的归属判定（配置侧别 + 名签定玩家侧，立绘亮度交叉校验），把**原话 + 谁在说**一字不改地交给模型读。★不做关键词匹配★：那是个带 `condition/group/{call\|B\|A}` 占位的**语句池**（同池还有「侥幸罢了！…」「我胜了！…你服是不服？！」），匹配迟早撞上既不认输也不直说的那句。归属判不出写「有人」，不猜。<br>**收口口径 = 事件驱动，不靠放大超时**：主 = `BattleEndMgr.BattleEnd`（该类不在任何 dump 里 → `drama/BattleEndHook.cs` 按名反射手动挂，挂不上自动降级）；辅 = `NpcInitiativeMonitor.BattleActive()`（`isBattle` **或** `UIBattleInfo` 可见，两个位对切磋都是零样本，只押一个就是重演 `unitLeft/unitRight` 那次"错数据源自信地给反事实"）；证据到达就 `DramaGate.Renew` + `DramaNativeClaim.ClaimFor` 续期（凭证 `ClaimTTL=120s`，一场战斗很容易超过它）⇒ **打多久都不会被掐**；★主辅两个信号不平等★（复核时按真机栈帧定的）：`BattleEnd` 是玩家在**最后一页**点「继续」时才跑的（`5088-5096`：`ClickOption → NextDialogue → BattleEndMgr.BattleEnd`），天然排在所有台词之后 ⇒ 见到它且已有台词就**立刻收口**（`_battleEndedHard`）；而 `isBattle`/`UIBattleInfo` 翻假可能落在战后窗**之前**（`NpcInitiativeMonitor` 自己的旧注释就写着"结算期 isBattle 可能已落"）⇒ 只认到软信号时必须再等**静默 20s**（已有台词）/ **15s**（一句都没抓到）才收 —— 抓到第一句就收会把第二页**切掉**，并且凭证已吊销 ⇒ 第二页重新长出那颗没用的按钮（正是用户报的原症状）。另：`BattleEnd(Boolean)` 的参数在"玩家输了"那次实测打的是 `False`（游戏自己那行 `12 战斗结束：False`）——**别当胜负位用**。静默 60s / 总上限 10 分钟 / 世界重载 → 收口成"没看到结局"。<br>**为什么两边都要改**：C# 每 30s 发一条 `type:"pending"` 心跳，Python `_await_response` 据此把 RPC deadline 往后推 90s（裸 `wait_for` 超时会**取消** future，故必须 `asyncio.shield`）—— 只放大 `rpc_timeout` 的话，慢的切磋会被 120s 掐成「请求超时」，而几分钟后真结果回来已无人认领；不放大则对端真失联时干等。心跳同时喂对话窗的忙标自愈（`BusyStaleSeconds=240`，战斗期间 Python 一帧都不发，不喂就会 4 分钟后误清成空闲）。<br>★`DramaGate.Resolve(slot, …)` 的 payload 必须是 `Ok(...)`/`Fail(...)` 那个形状（`{success, data}`）★ —— 复核时抓到**婉拒分支递了裸 data**：`WsClient.OnDramaResolved` 按 `result["success"]` 判成败（取不到即 false），`text_render.render` 又按 `res["data"]` 取字段，于是"玩家婉拒"这条最要紧的分支在模型那儿成了 `{success:false, error:"未知错误"}` —— 渲染层单测钉不住它（它绕过 C# 直接喂 data），护栏只能钉 C# 侧（`test_refuse_path_resolves_in_ok_shape`）。同理，Python 侧 `_await_response` 那条必须**跑起来测**：配套的 grep 测试（"函数体里有 `asyncio.shield`"）在函数里引用了一个作用域中根本不存在的 `method` 时照样全绿，而真机上一收到第一条心跳就 NameError（`test_pending_beat_slides_rpc_deadline`）。
**顺带白捡的两件事**：凭证活到收口 ⇒ 战后窗不再长出「AI 对话」按钮（点了它只会得到「世界界面尚未就绪」，实证 5076-5084）；等过战斗的收口不再抢着自动开窗（`WsClient` 认 `crossed_battle`，改走未读+横幅）。<br>**漏选兜底不变**：玩家按 ESC/点窗外关窗不走 `ClickOption` → `UnitActionRoleDrill.OnEnd` postfix → `UnitActionPending` 那份 `answered:false` 结果 → 渲染成「未作答复」。**字段缺失 ≠ 否定**。<br>**群聊**：切磋结果落一条公共叙述行（`tools/room_narrate.py`，`docs/archive/2026-09-17-群聊编排/设计定稿.md` §4.6 设计过但一直没落地的那个文件）——"结果全局可见"只能是发言行，不能共享 `tool/result`（成员的 tool 结果要与他看到的 tool_call 成对，插别人的就是孤儿消息，网关直接拒）。★ 这条行是**旁白**：进 `Room.log`（成员经 `deliver` 读到）也进群窗，但**画「行动记录」行、不画气泡** —— 09-22 用户拍板"不该以 NPC 气泡发，要以工具返回结果形式显示"（实机里它成了发起人的气泡，而旁白里还夹着玩家的原话，挂谁名下都不对）；实现 = 直播帧带 `narration:true` → `ApplyRoomSay` 转 `ChatWindow.AppendActionRow`（工具行同款折叠行），重开窗走 `project_room_history` 的 `tool_result` 条目 ⇒ **重开窗也还在**（这一条很要紧：进战斗是场景切换，群窗被收掉，之前那版重开窗只剩一条气泡）。提案/应下/解除那几条叙述行**不在此列**，仍走气泡。<br>**反编依据**：`UnitActionRoleDrill` own member 里有 `public unsafe void OnEnd()` 与 `public unsafe bool isDrillComplete`，与已挂的 Invite/TeachSkill 同构；另有静态 `OnDrillEndAddValue(unit, toUnit, isDrillComplete, onEndCall)`（CallerCount 3）可作日后校准点。护栏 `tests/test_spar_choice_contract.py` + `tests/test_room_narrate_spar.py` |
| `attack` | **保持立即返回**：`UnitActionRoleAttack` **连 `OnEnd()` 都没有**，也没有 `isDrillComplete` 那种落定字段（只有回调 `onDramaClickCall`/`onDramaAttackTipCall`），接不了选择链。语义上也对——攻击是强加行为，对方没有拒绝权 |
| `economy_item` give | 挂起；结局按 `receive/refuseProps` 分区；give 道具在拆背包前先占坑防回滚浪费。**`giveAction.isCheckUnitProps = false` 是必需项，不是优化**（见下条） |
| `economy_item` give · **整栈丢失事故（09-13 定案）** | **症状**：送某些道具不弹窗、工具挂 120s 被 Python 判超时，而**道具已经从背包消失** → 第二次再试报"所有赠送项均未达成"（模型据此编出"许是贫道记错了"）。诡异点：**送丹药可以、送筑基丹不行**。<br>**根因**：`UnitActionRoleGive.isCheckUnitProps` 默认 **true** = 动作创建时按 `soleID` **回查发起方背包**是否仍持有这些道具。而 `TakePropsFromBag` 是**先 `DelProps` 拆栈、再建动作**：送**整栈**（筑基丹×1 全拿走）→ `soleID` 从背包消失 → 回查得 `null` → `IsCreate` 内部 **`NullReferenceException`** → 动作建不起来 → **游戏不播剧情窗**；送**部分**（蓄力丹 9 送 1）→ `soleID` 还在（剩 8）→ 回查通过 → 窗正常弹。**差别只在是不是整栈**，这就是"换个道具就成/不成"的全部原因。<br>**修法**：`giveAction.isCheckUnitProps = false` —— 该字段存在的意义就是给"调用方已自行拆栈"的场景；我方在拆栈**之前**已用 `PropsItemCanGive` 逐件校验，且实物就在 `giveProps` 手里（探针证实 `giveProps=1`），跳过重复回查不会放过非法赠送。<br>**真机证据**：`[Give] 预检 拆出道具数=1 giveProps=1 isCheckUnitProps=True IsCreate=抛异常` + `IsCreate 预检抛异常: System.NullReferenceException`。<br>**教训**：这条路此前**除结果外零日志**，导致"没弹窗"时无法区分「没走到 / 占不到槽 / 动作建不起来 / 动作建了没播剧情」——补上 `[Give] 进入` / `[Give] 预检` / `[Give] CreateAction 已返回` 三段探针后**一轮定位**。业务路径的入口留痕不是可选项 |
| `trade` | 复用 +100 段（DramaGate 全局只允许一个挂起窗 + 壳条目正文全运行时覆盖 → 不必新占段）；`ShowConfirm` 的 `left/right` = 卖方/买方立绘 |
| `item_acquire` ask_for | 挂起；结局 = `isAskforComplete` + `isNPCReduceIntim` |
| `item_acquire` steal_item | **经用户拍板保持立即返回**（暗中行事无选择 UI，结局 = 概率结算） |
| `world_ai_action` yao_yue | 挂起；**`accepted` 以 `isInviteComplete` 为唯一判据**——实测接受 = `(True,True)`、拒绝 = `(False,True)`；`isNPCReduceIntim` 两条路径**恒 true、无判别力**（配置表 `RoleInvite.intimateDecline=-120` 佐证），仅随 `data` 透出。旧注释"isInviteComplete 有'拒绝/完成'二义"**已证伪**。邀约全程走游戏原生 UI：工具→`UnitActionRoleInvite`→弹 **81002**（选项 `810021`=接受 / `810022`=拒绝，**只有这两个**）→接受走 lambda `b__11_2`→弹 **81012**（6 变体，全含 `{0}`）→`OnEnd`；我方只挂 `OnEnd`，自建窗 `ShowConfirm` **不参与**邀约 |
| `world_ai_action` yao_yue · 原句捕获 | **`DramaTextCapture`（✅ 真机验证通过）**：第二层剧情里含约定地点，抓下来随结果透出。**唯一数据源 = `UIDramaBase.GetDialogueText` 的返回值**（`UI/DramaTextHook.cs` postfix）——游戏填好的**成品句**（`{0}`→"新达镇"），零推断。**两条被实测排除的路（勿回退）**：① `DramaTool.lastOpenDramaDialogueText` 是**未替换的模板**；② `DramaTool.lastOpenDramaDialogueValues` **不含 key 0**（实测 `values0=<no-key-0>`）。配对 = `BeginInvite(npc)` 武装 → 每次 `InitData` 覆盖式记录 → `OnEnd` 取走；仅 `accepted:true` 时透出 `data.invite_text` |
| `world_ai_action` chuan_gong | `UnitActionRoleTeachSkill` 挂起；**`skill` 参数可选**——省略时 `FirstTeachableSkill(wub)` 挑一本作构造种子，原生面板照常弹出由**玩家自选**；`OnEnd` 回读 `teach.gainSkill` 经 `MartialDisplayName` 得**实际所学中文名**写入 `data.skill`（回退种子名），`data.skill_source=gainSkill\|seed` 标注来源。interop 实证该类有 **teachSkill（构造种子）/ gainSkill（所学）/ giveUpSkill** 三独立属性；`gainSkill` 是否 = 玩家所选尚无真机样本 |

落地结构：`UnitActionPending.cs`（实例指针→槽位 + 结果组装委托登记表）+ `UnitActionHooks.cs`（`UnitActionRole{Give,Askfor,Invite,TeachSkill}.OnEnd` 四类**私有**方法的 Harmony postfix，按名 patch）。**OnEnd 四钩子须加 try/catch**（异常会顺游戏动作结束链上炸）。真机观察点 `[UnitAction] <op> OnEnd 挂起命中 slot=N`；若 120s 超时且无日志 = 对应动作不调 `OnEnd`，需改轮询 `isComplete` 方案。

## 五、坐标 / 锚点 / 视觉经验

* HUD「传」钮：模板改 `G:btnEmail` 优先链（此前克隆 `G:btnPlayer` 头像钮导致"第二个玩家头像"）；全 HUD 搜境界文本（**炼气/筑基/结晶/金丹/具灵/元婴/化神/悟道/羽化/登仙**，`Text` + `TextMeshPro` 双轨）→ 挂容器之父、落**修为容器右缘外**，实测锚点 `(110,2)`、与左邻间隙 36；搜不到退回模板右侧；两种走法都打点。
* 面板操作按钮常量：`CreateBarButton` 的 `w≤120`、`gap=8`（视觉模板取 `goGrid1` 的"交谈"行）；改字同时支持 `TextMeshPro`。
* 立绘管线：对"3D 模型未进场景"的 NPC 调立绘 API → 0 尺寸 RawImage → 游戏每帧刷 `RenderTexture.Create failed: width & height must be larger than 0`（单局 20 万行级）。**通讯录立绘已整体移除**（头像恒为占位圆），对话窗立绘走 `PortraitService` 原生管线不受影响。
* **悬停气泡（`UI/HoverTip.cs`）的两条静默失败（09-13 定案；症状都是"气泡一个都不出、日志无异常"）**：
  * **① 相机必须随画布**。`RectangleContainsScreenPoint` / `ScreenPointToLocalPointInRectangle` 要求 Overlay 传 `null`、`ScreenSpaceCamera`/`WorldSpace` 传**画布相机**。旧代码写死 `null`——这只对**代码自建面板**成立（`ConfigUiBuilder`/`ChatUiBuilder`/`ContactUiBuilder` 都把 canvas 设成 `ScreenSpaceOverlay`）；而 **AB 面板的 Canvas 是游戏 UIMgr 自己挂的**，真机 dump 实证是 `ScreenSpaceCamera`（Player.log：`[Repair] StartGameTip … <Canvas mode=ScreenSpaceCamera order=20>`、`[AbChatPanel] 挂载链 … UI[on](Canvas mode=ScreenSpaceCamera order=0)`）→ 屏幕坐标被当世界坐标 → **恒不命中**：登记全在、`Update` 照跑、气泡永远不出。这正是"同一份 HoverTip，预览工程/代码版有气泡、AB 版一个都没有"，也是配置面板 09-12 换 AB 壳后才暴露的原因。现役 `ResolveCamera()` 运行期解析：本节点 `GetComponent<Canvas>()` → 父链 → Overlay 则 `null`、否则 `canvas.worldCamera` → `rootCanvas.worldCamera` → `Camera.main`；画布晚挂（游戏侧 `AddComponent`）时**不置就绪位、下帧重试**。**唯一正确写法是"取画布相机"**——不要写死 `null`，也不要写死 `Camera.main`（`DramaAiOption.LogInjectDiag` 早就按这条取相机，是 HoverTip 漏了）。
  * **② 锚点必须对齐画布 pivot**。`ScreenPointToLocalPointInRectangle` 返回**以画布 pivot 为原点**的局部坐标，而 `anchoredPosition` 以**自身锚点**为原点——两套坐标只有「锚点 == 画布 pivot」时才同系。旧代码假定画布 pivot 恒为 `(0.5,0.5)`；本作预制件根节点常见 `(0,0)`，写死会让气泡整体偏半个画面 → 定位到屏幕外 = **同样"没有气泡"**。现役在 `Show()` 里 `rt.anchorMin = rt.anchorMax = _canvasRt.pivot`（`NormalizeLayout` 只归一化 scale/锚点/offset，**不动 pivot**，不能指望它）。
  * **③ 成功路径也要留痕**：`HoverTip` 此前**零日志**，"不出气泡"只能靠猜。现役三行——`装配：模板=预制体模板|代码现造 文本=有|**无** 画布=<Overlay(相机=null)|ScreenSpaceCamera(相机=X)> 宿主=`、`登记就绪：N 条 画布=…`、`首次命中 区域#i 相机=… 文本=…`，失败径另有 `浮出失败：`。**排查固定顺序**：`登记就绪` 从未出现 = `Register` 根本没跑到（看文件列表是否拉回、`desc`/兜底文案是否为空）；`画布未挂（下帧重试）` 常驻 = 画布没挂上；`相机=**缺失**` = 画布无 event camera；`首次命中` 从不出现而登记数 > 0 = 相机/坐标系仍不对。

## 六、游戏数据源与本地化（反编/真机实证）

* **★ 兜底清理（09-13 用户拍板"链路确定了就别再兜底"）★**：C# 工具层原有 450 处 `catch`（`ToolExecutor` 217 + `UnitSnapshot` 233），
  其中一批是**分层兜底**（A 路失败试 B 路再试 C 路）。判据不是"看起来多余"，而是**代码里已有的自检日志**：
  `命中主路` 9 / `命中回退` 0 / `空结果` 0；`attempt=0` 9/9（重读循环从未触发）；`两层不等` 0/9。
  **已删（可逆，备份在手）**：
  * `LogItemText` 的 `DataToString()` 兜底 —— 它是**序列化格式**（`StringToData` 的逆），不是人话，
    唯一产出过的东西就是 L1 近况里的 `0&A&A`。现在片段全空即**如实返回空串**。
  * `SetAttr` 的 `PdOf` 裸字段兜底 —— 面板口径是 `DynInt`（含加成 + 上下限钳制），裸字段是未加成值。
    这正是 09-13「境界/声望取错」那条 bug 的**同一形态**，降级成兜底只是把同一个错藏得更深。
    现在**只认 DynInt**，缺失即不落键，并打 `[Attrs] ★DynInt 缺失★ prop=…`（去重封顶 60）——
    真机若恒定缺某属性，那行日志会立刻指出来，再**有针对性**地补读取路径。
  * `ReadLingshiHeld` 的 `totalSchoolMoney` 兜底 —— 那是**宗门钱**，与随身灵石是**两个账户**。
    旧行为在读取失败时把一个貌似合理的数字交给模型，模型据此报价/赠送而实际看的是另一个钱袋。
    现在失败返回 `-1`，三个调用点（trade 预检 / economy_item 预览 / TransferLingshi）**显式失败**；
    trade 的余额字段改成"读得到才写"（绝不把 `-1` 当余额报给模型）。
  * 旧版参数别名：`initiator ?? target` 的 `target`（6 处）、`economy_item` 顶层 `item`/`item_name`/`count`
    单件形态、`trade` 的 `item_name` —— 上一轮全量核对确认**全部不可达**。`bridge.py`（离线桩）同步删除。
  * `dialogue_agent.py` 的 `[排查] inspect_unit res 原始载荷` 调试转储（每次 inspect 写 1500 字日志）。
  **保留（不是兜底）**：`RollbackGive`/`TransferLingshi` 补偿回滚（道具原子性）· IL2CPP per-field `try/catch`（隔离）·
  `UnitLookup` 全图按名匹配（中文名是主键）· `"(无文本)"`（**失败信号**，删了就退回静默）· `jie_yi` 双写 ·
  `AreaNameFallback`（`topic=region` 拿它当州名词表）· `BuildLogs` 的多层读取（见下，用户明确保留）。
  **未动（链路未验证）**：`social_relation` 的 BreakWith→字段直写、`BrotherBack→Brother` 回读 ——
  Python 日志显示这三个工具**一次都没跑过**（只跑过 `economy_item` 24 / `trade` 6 / `world_ai_action` 9），
  这些兜底是**未拆的保险**，删了等于把"没测过"直接暴露给玩家。
* **★ `@` 引用标记按类型字母分流（09-13 全量实测修正）★**：真机两会话统计 ——
  **`@q_` 221 次全部 2 字段**（`@q_寇炫明(好友)|cPoLMG@`，**字段0 就是显示名**，字段1 才是 unitID）；
  **`@w_` 14 次**：12 次 5 字段（`@w_zcW8xn|1|1011111|13|@`，字段2=propsID → 六品培元丹）
  + 2 次 19 字段（`@w_gdZZA1|2|0|0|4|88003|…|@`，**无 propsID**，`88003` 命中 `BattleAbilityBase` → 大法）。
  ⇒ 旧实现一律按"字段0 是 ID"处理，把 `q` 的**正确结果**打成"未解"（**198/221 是误报**），
  又把 `w` 长形态的裸 soleID（`gdZZA1`，对模型纯噪音）漏进上下文。
  现在：`q` → 字段0（并记为"人物"）；`w`/其他 → 逐字段试 `ItemProps` → `BattleAbilityBase` → unitID；
  仍解不出且是 `w` → **`（未知道具）`占位，绝不回裸 ID**（原样留在 `@标记未解` 日志里备查）。
  **为什么"逐字段试"安全**：`ItemProps` id 从 10001 起、`BattleAbilityBase` 从 101 起，都远大于标记里的类型/序号字段。
* **★ 工具契约三面一致性（09-13 全量核对，机械化）★**：这条链上有四个互相独立的"声明面"，任何一处单方面改动**都不会报错**，只是行为悄悄不对 ——
  `schemas.py`（模型据此生成参数）/ C# 真正读的 args / C# 真正发的 data 键 / `text_render.py` 真正读的键（**没被读 = 模型永远看不到**，本项目已踩四次：性格注解、性别、位置、道具 ID→中文名）。
  现固化为 `tests/test_tool_contract_consistency.py`（8 条，全部变异验证过）。本轮核对结论：
  * ✅ 分发与 `TOOL_ORDER` 一致；**无死参数**（schema 声明的每个键 C# 都读）；6 个枚举参数取值全对齐（`social_relation.op` 13 / `movement.op` 3 / `world_ai_action.op` 6 / `item_acquire.op` 2 / `query_world.topic` 5 / `inspect_unit.log_filter` 3 + 嵌套 `cat`/`board`/`relation`/`race`/`sex`）。
  * ✅ 所有数值默认/钳制与 schema 对齐（`log_page` 1、`top` 10/1..200、`count` 12/1..120、`value` 5/1..10）。
  * ✅ 渲染层读的键 C# 全都发（`age`/`life` 是 `SetAttr(t, ..., key, ...)` 动态写出的，已在测试里白名单注明）。
  * **三类"允许的不一致"**（白名单 + 测试头写明理由）：① `initiator` —— **harness 注入**（`dialogue_agent._execute_tool_calls`），刻意不进 schema（发起方恒为当前对话 NPC，不能让模型指定）；**全部 6 个**动作工具的 actor 全靠它（`social_relation`/`movement`/`world_ai_action`/`economy_item`/`trade`/`item_acquire`），**注入被删会全部以「未找到发起方」失败**，故专门一条测试盯着；② `target` —— 旧版兼容别名，现状无人写它；③ `economy_item` 顶层 `item`/`item_name`/`count` —— 旧版单件形态兼容，同样不可达。
  * **本轮修掉的三处真不一致**：① `item_acquire` schema 描述还写着 `props_detail`（**09-03 就被删了**，模型被指引去找不存在的键）；② `inventory_top` 只做裸转型不钳制（传 99999 静默等价于 `inventory_all`，与 `top`/`count` 口径不一致）；③ `economy_item.via` 的 schema 描述暗示它管整体送达方式，实际**只影响纯灵石那条路**（含道具时道具恒走原版赠送 UI），描述已按实情改写。
  * **顺带补上两处"C# 发了、模型看不到"**：`trade.buyer_item_before/after`（到货实证 `0→2`，此前只渲染了 `item_moved` 布尔）与 `world_ai_action.skill_source`（`gainSkill`=玩家在原生功法面板自选 / `seed`=NPC 功法槽播种；不区分会把玩家自选的功法说成"我传你这部"，语气反了）。
* **工具结果分工（勿回退）**：C# 只回**纯原始数据**（`Ok(data)`，叙述字段 `note`/`text` 全退役），自然语言叙述唯一来源在 Python `tools/text_render.py`（`render(name, args, res)`，异常/缺失 → `None` → 回退 `json.dumps(全量)`；UI 行动记录行与 LLM `tool_result` 同一产物）。**忠实原则**（用户定调）：data 里有的语义字段一律呈现——不截断列表、不隐藏失败项、不省略数值；省 token 只靠"去掉 JSON 结构包装"；**字段缺失 ≠ 否定**（`completed`/`accepted` 缺失不能说成"未完成/被拒绝"）。逐工具字段表见 `tools/schemas.py` 与 `tools/text_render.py`。
* **本地化归 C#**：conf 查表中文字段（性格/魅力档位/关系中文/区域名）在 C# 完成，Python 只组句。
* **区域名**：`ConfWorldAreaBaseItem.name` 只存**本地化 key**（形如 `areaName9`），必须过 `GameTool.LS()`。现役 `AreaLabel(raw)` = `GameTool.LS` → 若返回值**仍是 `areaName…` 形态或为空**则查静态兜底表 `AreaNameFallback`（`AreaName()` 与 `search_units` 的 region 过滤共用）。
* **权威本地化表**：`E:/SteamLibrary/steamapps/common/鬼谷八荒/Mod/modFQA/配置修改教程/配置（只读）Json格式/LocalText.json`（**54,991 条**，字段 `id`/`key`/`ch`/`tc`/`en`/`kr`）。`areaName1..13` = 白源区 / 永宁州 / 雷泽 / 华封州 / 十万大山 / 云陌州 / 永恒冰原 / 暮仙州 / 迷途荒漠 / 赤幽州 / 天元 / 冥山 / 太行山（早期按解锁顺序反推的表把 `areaName11` 记作「天元山」且只到 11——**以 LocalText.json 为准**）。
* **建物枚举必须用泛型重载** `g.world.build.GetBuilds<T>()`（`MapBuildSchool` / `MapBuildTown`）：**无参重载**元素转不成 `MapBuildBase`，被 `catch { continue; }` 静默跳过 → 全图建物被逐条吞掉、结果恒空（`places`/`sects` 双双为空即此）。逐条 try/catch 但要**记录失败原因** + 末尾计数日志 `[Builds] CollectNamedBuilds：宗门=N 城镇=M`。
* **道具介绍**：`DataProps.PropsData.propsItem` = `ConfItemPropsItem`，除 `name`/`worth` 外同一行还有 **`desc`**（本地化 key，LS 后即悬浮窗介绍）；装备（`EquipsDetails → GetProps(soleID)`）拿的是同一个 `PropsData`。悬浮窗的结构化效果（体力+75 / 消耗 8 念力 / 冷却 20 秒）**不在此表**，疑似 `propsInfoBase`——二期再挖。 **★ 09-17 起：`desc` 里的 `&…&` 占位符必须解析**（`PropsDescText` = LS → `PropsSigilCore.Scan` → `StripRichTags`；列取「道具境界」= `ItemPill.grade` / 运行时 `BattleSkillValueData(propsData).grade`，**不是** `ItemProps.level` 品质），详见 §D.12。
* **功法（技能）说明**（09-13）：**不要自己查表拼**，走 `UIMartialInfoTool.GetDesc(DataProps.MartialData)` —— 反编实证它是 `abstract+sealed`（静态类）上的 `public static string GetDesc(MartialData)`，**与面板「技能说明」同一入口**。同族的 `GetDescRichText(...)` 才是带图标/染色数字的富文本版，故 `GetDesc` 是纯文本版。
  * 说明文字在游戏内由「前缀 `ConfBattleSkillPrefixValueItem.desc` 模板 + `ConfBattleSkillValueItem{key,value1..10,icon}` 数值」组装 —— **模板里的占位符只有游戏侧会替换**，自己按 `md.prefixs[].prefixValueItem.desc` 拼出来是一串未替换的占位符，所以只留一个入口、不做本地拼装兜底。
  * 拿到后必须过 `UnitSnapshot.StripRichTags`（`<color=…>`/`<sprite name=…>`/`<b>` 是 UI 排版，对模型是纯噪音）：**只剥标签 + 折叠空白，不截断不改写**。
  * 取不到 → **不落 `desc` 键**（字段缺失 ≠ 没有说明）+ `ModMain.P` 留痕（空/异常两条路径都不许静默）。全链路只有 `inspect_unit(classes=["abilities"])` 会带上它（**L1 只要 `brief`**，所以不吃这份体积）。
  * **★ 拿到的 `GetDesc` 是模板原文，必须自己替换占位符 ★**（09-13 第二次修：用户投诉"中间夹杂太多占位"）。
    `UIMartialInfoTool.GetDesc(md)` 返回的**不是**面板上那串人话，而是带记号的模板。三类记号、三个来源，
    语法与来源**全部用游戏自带配置表离线实证**（`Mod/modFQA/配置修改教程/配置（只读）Json格式/`）：
    | 记号 | 含义 | 来源 |
    |---|---|---|
    | `<y>…</y>` / `<g>…</g>` | 游戏自带**配色标签**（不是 Unity 富文本） | `StripRichTags` 剥掉（剥的是通用 `<…>`，两类通吃） |
    | `&expr&` | **数值**，如 `&22111_range&` | `ConfBattleSkillValue`。★该表主键**自带前导 `&`**（实证 key = `&22111_range`，`value1..value10` 为各等级值，`valueScale="x1\|f0"` 是格式）★ |
    | `$key$` | **本地化文本**，如 `$s_dao$`→刀法、`$s_xueren$`→血刃 | `GameTool.LS`（`LocalText.json` 的 key） |
    * `&expr&` 里含 `|` 的是**算式**：`22111_xzsh|x22111_dmg|/100|f0` = 两值相乘 ÷100 保留 0 位小数 —— 它**不是表里的行**（`&…|…&` 查无此键），必须走 `ConfBattleSkillValue.ValueMathf`（单值走 `GetValue`）。
    * **不猜值**：算式解析失败**绝不退回单值** —— 只取一个因子会给出错误的数，比留个可见占位符更糟。查不到的记号**原样保留**并统计残留留痕（`[Abilities] ★技能说明仍残留 N 个占位符 …片段=…`）。
    * **★★ `GetValue` 返回的是未缩放的原始值，必须自己套 `valueScale` ★★**（09-13 第三次修）。
      真机症状：`2秒` 显示成 `2000秒`、`8秒` 显示成 `8000秒`、`50%` 显示成 `5000%`、`7%吸血` 显示成 `700%`。
      根因：缩放系数在**同一行的 `valueScale` 列**，形如 **`x<系数>|f<小数位>`**（全表实证：系数只有 `1 / 0.1 / 0.01 / 0.001 / 0.0001`，小数位 0~3，还有一条大写 `F1`）。
      实证三行：`&510011_cxsj` value1=**2000** × 0.001 = **2**（面板 2 秒）；`&32111_tsgj` 5000 × 0.01 = **50**（面板 50%）；`&22111_dmg` 480 × **1** = 480（系数为 1，所以"有时看着是对的"）。
      现流程：`GetValue`（负责按等级选列）→ `ApplyValueScale`（套该行 `valueScale`，**去掉多余尾零**：`2.00`→`2`）。
      **算式里的每个操作数也各自套自己的 `valueScale`**（`NumOf`），因为 `ValueMathf` 是否替操作数缩放无法离线确认（已知 `GetValue` 不缩）。
      算式自算为主、`ValueMathf` 仅兜底，并打一行**对照日志**（`[Abilities] 算式自算=… 游戏ValueMathf=…`）留判据。
      真机原值→期望值留档在 `tests/test_ability_desc_contract.py` 的 `_GOLDEN`。
    * **键的两种写法都试**（`inner` 与 `"&"+inner`）：表主键带 `&`，但 API 形参名是 `key`，内部是否补 `&` 无法离线确认 —— 同一键的两种写法，不属于"猜值"。
    * 真机模板原文（`LocalText.skill_attack_desc22111`）与替换结果留档在 `tests/test_ability_desc_contract.py`（`TPL_22111` / `EXPECT_22111`），可当回归对照。
  * 渲染层**逐槽一行**（`功法：` 换行后 `· 灵技「名」(id=…)——说明`）：说明动辄上百字，沿用旧的单行 `，` 拼接会糊成一片。契约测试 `tests/test_ability_desc_contract.py`。
* **★★境界（面板口径）：`gradeID` 是「行号」不是「大境界号」★★**（09-13 真机事故："他爸显示成登仙境"）。
  现场（**走的是 unit_id 精确路径，与重名无关**）：`Resolve('', unit_id=ECYyXU) → ②unitID精确` → `"realm": "登仙境"`，而面板上此人是**结晶后期**。
  * **根因**：`RoleGrade` 表**一行 = 大境界 × 期 × 品质**（44 行 / 10 个大境界 / 3 期）；而 `g.conf.roleGrade.GetGradeName(Int32 grade)` 的形参名是 **`grade`（大境界号 1..10）** —— 同族 `GetNextGradeItem(Int32 gradeId)` 的形参名才是 **`gradeId`（行号）**。**参数命名本身就是证据**。于是行号被当大境界号喂进去：结晶后期行号 ≈11 > 10 → 查表**越界钳到最大档 → 登仙境**。低阶 NPC（行号 1~3）恰好落回炼气/筑基，所以"有时看着是对的"——这类 bug 最容易被误判成重名。
  * **修法（不猜，自洽校验）**：把 `gradeID` 当行号在 `allConfList` 里定位，判据是**行内 `grade` 必须与 `dynUnitData.curGrade` 对得上**（`curGrade` 是面板同源的大境界号，参照 mod `GetGradeItemMaxQuality(unit.data.dynUnitData.curGrade + 1, 1)` 实证其语义）。对上 ⇒ 取该行 `LS(gradeName)+LS(phaseName)` = **"结晶后期"（与面板逐字一致）**；对不上 ⇒ **不采信**，退 `GetGradeName(curGrade)`（丢"期"但不错人）。行号 1 基/0 基两种下标读法都试。原始数字写进 `realm_diag` 随 brief 回传，`[Realm]` 日志一行给出结论。
* **★面板数值一律 DynInt 优先 —— brief 曾经不是★**（09-13，与上一条同源）。面板读 `dynUnitData.<X>`（`DynInt.Value(null,true,true)`，含气运/装备加成并钳制），而 `brief` 原先直接读 `propertyData` **裸字段**。
  真机实证：某 NPC 声望裸值 **3129**、面板 **3379** —— 差额 **250** 正好是其气运「赶尸道童 +100 / 单身贵族 +150」。现 brief 的 `beauty/reputation/talent/mood/age/life` 全部走 `SetAttr`（DynInt 优先），与 `stats` 同口径。
* **★自身段身份：打开对话 UI 时把真身钉下来★**（09-13）。`npc_id` 全链路是中文名（Python 侧会话/人设/存档路径都以名为键，改 unitID 是伤筋动骨的迁移），但**打开对话 UI 的那一刻 C# 手里真的握着 `WorldUnitBase`**（NPC 面板按钮 / 通讯录行点击 / 剧情窗按钮 → 全部汇到 `ChatLauncher.OpenForUnit`）。拿着真身却只传名字、后面再按名全图猜 ⇒ 自身段可能挂在同名者身上。现于该入口登记 `name → unitID`，按名解析先查登记（分支 ⓪，排在**玩家直达之后、全图扫描之前**）；登记失效（换存档/单位消失）自动摘除并回落。
  ★ **09-17 更新**：落点从内存字典 `UnitLookup._pinned` 改为**落盘 + 按存档隔离**的 `UnitIdRegistry`（写入点增加「加好友」），并新增第 4 步「身份随帧携带」—— 见本节下方。

* **查询条数交给模型**（09-10 定）：`rankings` 的 `top` 上限 `20 → 200`；`events` 新增 `count`（`1~120`，默认 12 ≈ 最近一年）。上限只是防呆，默认值不变。
* **关系枚举**：`GetRelation(player)` 返回枚举，`"None"`/`""` 未映射会渲染出"关系None" → C# `UnitSnapshot.RelationCn` 统一 `None/"" → 陌生`（与 `search_units` 的 `RelationMatch` 口径一致）。
* **性格**：`propertyData.inTrait`(内 1) + `outTrait1/2`(外 ≤2) → `g.conf.roleCreateCharacter.GetItem(id)` 的 `sc5asd_sd34`(名) / `xdash_54sd`(面板注解，LS）→ `role_character_nameN` / `role_character_descN`；输出 `{inner, inner_desc?, outer[], outer_desc?}`（两条渲染路径都要带上注解：inspect 的「性格注解」与 L1 自身段的「本性X（注解）」，**只补一条是历史踩过的坑**）。
* **★ 经历文本里的 `@` 引用标记：字段0 是 soleID，道具表 id 在中间字段 ★**（09-13，第四次「静默降级」）。
  真机 L1 近况出现 `在溪望镇的坊市中购买了zk8xkN。`，而游戏经历面板同一句显示绿色的 **五品培元丹**。
  * **标记真实格式**（铁证取自 Player.log 里游戏自己的剧情文本 `我这里有一个@w_rIsZH3|1|5031101|48|@准备赠于你`）：
    `@<类型字母>_<字段0>|<字段1>|…|@`。该样本字段2 `5031101` 在 `ItemProps.json` 里存在
    （`name = item_name5031101` → LocalText **青须藤**）⇒ **中间的数字字段才是道具表 id**，字段0 是道具的 soleID。
  * 旧实现 `Regex.Replace(s, "@[a-zA-Z]_([^|@]*)(?:\|[^@]*)?@", "$1")` 只输出**字段0** ⇒ 模型看到 `zk8xkN`。
    现改为 `MatchEvaluator`（`RenderAtTag`）**逐字段试解**：任一纯数字字段能查到 `ConfItemProps` 行 → 该道具中文名；
    否则字段0 当 unitID 查人名；都不行 → **退回字段0**（= 旧行为，绝不更差）。
  * **"逐字段试"为什么安全**：`ItemProps` 全表 3295 行、id 从 **10001** 起、**无 <1000 的 id**
    ⇒ `1`/`48` 这类序号字段查不到任何行，不会误命中。
  * `DataToString()` 是**序列化格式**（`id&values&conditionValues` 用 `&` 连接，`StringToData` 是其逆），不是人话：
    某元素 `GetLogString()` 为空而回退到它时，就漏出 `0&A&A` 这种残渣（游戏面板也不显示）。
    现由 `KeepLogPart` 丢弃 —— 判据刻意收窄：**无中日韩字符 且 含 `&`** 才丢，纯数字/符号的 subLog 照常保留。
  * **留痕**：每个**新**标记打一行 `[Logs] @标记 <原样> → <渲染结果>`（去重、封顶 40 条）；解不出来额外打
    `[Logs] @标记未解 … → 退回字段0`。下一次真机日志即可核对道具种类全覆盖、并积累其他类型字母的证据。
* **★ L1「近况」为什么从"按桶配额"改成"合并取最近 N 条" ★**（09-13，用户问"不是说默认三条吗，怎么只显示了两条"）。
  旧写法 `limits = {3, 2}`（vital 前 3 + regular 前 2）**在真机上退化成恒 2 条**：Player.log 的 `分层[...]` 自检行显示
  所有查过的 NPC 都是 `vital=0, regular=5..15` —— **重要桶恒空**，于是 3 那个配额从不生效，只剩 regular 的 2 条，
  而 regular 里明明有 14 条可用。现改为**两桶合并 → 按账面月降序 → 去重（月,文本）→ 取最近 `RecentCap`(=6) 条**：
  ① 「近况」的语义就是"最近发生了什么"，按时间取比按桶配额更符合直觉；② 按桶配额会出现"一条陈旧的要事挤掉三条新的日常"，
  时间线是断的；③ 要事不会丢 —— 它通常本身就是最新的，且 `inspect_unit(logs, log_filter=important)` 才是查要事的正路。
  **`RecentCap` 是唯一旋钮**（每条经历 30~60 字，6 条 ≈ 350 字；该段随上下文常驻，别开太大）。
  配套两处措辞修正（09-13，用户指出）：① `LogItemText` 里 **`logs[]` 是同一句的片段，必须直连**
  （`string.Concat`）—— 旧写法 `string.Join("；", parts)` 会插出 `基于自我成长的需求，；认为…`，与游戏面板逐字不一致；
  `subLogs[]` 是附加明细，仍用 `；` 跟在主句后。② Python 侧**去掉正文的「近况经历：」前缀** ——
  段标签已是「近况」，渲染出来是 `Current runtime context —— 近况：近况经历：1. …`，两个同义词叠着纯属噪音，
  现在是 `Current runtime context —— 近况：1. …`。
* **★ C# `raw` 里有字段 ≠ 模型看得到：L1 成文在 Python，漏读是静默的 ★**（09-13 已踩三次：性格注解、性别、位置）。
  模型上下文里的人话由 `system_prompt.format_l1_context(raw)` 生成，C# 只负责把结构化 `raw` 发过来。**Python 没读的字段不会报错、不会缺段，只是模型"不知道"** —— 而模型不知道的表现是"回答变哑/答非所问"，不是崩，极难归因。
  **三次实例**：① `personality.inner_desc/outer_desc` 只进了 inspect 那条渲染路径，L1 自身段一直只有名字；② `raw.self.sex` 由 `GameContext.GetL1` 一直提供，`format_l1_context` 从没读过 → 模型不知道自己是男是女，玩家块更干脆**没有** `sex` 字段 → 玩家段代词写死 `"他"`，女玩家被成文成"你与他结着道侣之谊"；③ **键名不一致也算没读** —— 位置：C# 发 `point{x,y}`，成文侧写的是 `pos = self_raw["pos"]`（等地名），而 `grep '["pos"]' csharp/*.cs` = **0 处** → `栖居于{pos}` 是死分支、自身段位置**恒空**。
  **跨端字段名不一致比忘记读更难查**：两端各自都自洽，跑起来不报错、不缺段、日志干净 —— 只是模型"不知道"。查这类问题的通用手法：**在成文侧反查每个 `raw.get(...)` 的键，去 C# 里 grep 一遍是否存在**（本次 `pos` 就是这样一枪打中的）。
  **通用规矩**：① 增删 `raw` 字段时**两端一起改**（`GameContext`/`UnitSnapshot` ↔ `format_l1_context`），并各加一条断言；② 成文侧对**枚举型取值**做白名单（`if sex in ("男","女")`），既防脏值外泄（不写成"一位None子"），也天然给出缺字段时的旧行为回退；③ 代词这类"中文里绕不开"的东西必须由数据决定，别写死。
* **★★ 重名事故：按名字查人会静默查到另一个人（09-13）★★** —— 查人一律认 `unit_id`，不认名字。
  真机现场（Player.log 原文）：`[UnitLookup] Resolve('益婉容') → ③全图按名#199 unitID=Xs6JDI`。
  玩家面板上那个益婉容是**结晶后期/声望3379**，工具端出来的是**登仙境/声望3129** —— 两个都叫「益婉容」。
  旧实现的原话是「重名时取第一个匹配（真机随机 NPC 可能重名，**概率低**）」：**"概率低"是错的** ——
  全图上千个单位、名字用字池有限，重名是常态；而它**完全静默**：不报错、不缺字段，只是把另一个人的档案交给模型。
  * **三条防线缺一不可**（对应测试 `tests/test_unit_id_contract.py`，全部做过变异验证）：
    ① **能精确就精确**：`inspect_unit(unit_id=…)`；关系簿与 `search_units` **每人都回传 unit_id**（关系簿入参本来就是 unitID，旧实现取到名字后把 ID 丢了 —— 丢掉它才是根因）；
    ② **给了 unit_id 就只认它**：查不到返回 null，**绝不回退按名**（回退 = 又猜一次）。`if (hasId)` 块每条路径必须 return，测试用花括号配平断言"最后一条语句是 `return null;`"；
    ③ **按名解析必须消歧上报**：全图扫出**所有**同名者，返回第一个的同时把候选列表交给工具层，返回体带 `ambiguous{target,matched,used,candidates[]}`，Python 渲染成**正文最前面**的 `⚠️同名提醒`（明说"未必是你要找的人"+ 列出其余同名的 unit_id）。
  * **两个 IL2CPP 陷阱**（都会让"精确查询"变成猜）：
    · `g.world.unit.GetUnit(s)` 对**查不到的串会按名兜底**返回随便一个同名者 —— 所以 unitID 路径**必须回验** `got == unitId`，不等就当作没命中（诊断串里写 `②unitID不匹配(GetUnit按名兜底返回了 '…')`）；
    · 因此**含 CJK 的输入不许交给 `GetUnit`**（`HasCjk` 判据）：它替我们选人，还绕过了同名计数 —— 中文名一律走"全图扫完 + 计数"。
  * **历史背景**：这条坑 09-12 已经踩过一次（`GetUnit("唐炎")` 按名兜底命中同名 NPC →「查玩家经历」恒空），当时的修法只是"把玩家直达提到最前面"，只堵了玩家这一个高频歧义，**其余名字仍然静默选人**。这次才算堵住。
* **★ 第四条防线：C# 的 UI 侧也在按名找人 —— 手动好友的精确身份必须落盘（09-16，第 1 步）★**
  上面三条防线全在**工具/查询**那条路上，而**界面自己**找人时一次都没上报过歧义。三处都在按名解析：
  ① 通讯录行条目（`ContactPresenter.MergeManualIntoContacts` ③）、② 通讯录点开单聊
  （`OpenChatFor`）、③ **NPC 主动开口候选集**（`NpcInitiativeMonitor` ④）。
  * **根因还是那一句"拿着真身却把它丢掉"**：`NpcPanelAddContact.UnitNameOf` 里
    `panel.unit` **就是本人对象**，函数却只返回名字（`unit.data.unitData.propertyData.GetName()`）；
    而 `contacts.json` 从头到尾只存 `npc_id`。⇒ 每次读名单都只能全图扫一遍猜一个。
  * **最脏的后果不是"查错了"，是 ③**：候选集按名解析 ⇒ **你没加过的那个同名者会主动来找你说话**，
    而你真正加的那个永远沉默。这条症状极难归因（"我明明只加了甲，怎么乙来了"）。
  * **修法靠"唯一入口法则"**：每个手动好友都**必然经过** NPC 面板「加好友」按钮那一刻
    （`ContactDuty.ToggleManualContact` 全工程只有一个调用点），而那正是手里握着真身的时刻
    ⇒ 把 `unitID` 随 `add_contact` 上行、落进 `contacts.json`。
  * **已落地（第 1 步 = 纯契约层，用户可见行为不变）**：
    `contacts_store.add_contact(npc_id, unit_id=None)` 持久化它（**老记录没有这个键 → 读作"没提供"，
    逐字段兼容**）；同一函数的幂等语义扩展成"**补空 + 纠错**"（`added_at` 不动、`added` 仍为 `False`）；
    `ws_channel.add_contact` 透传；C# `ContactStore` 的镜像里 unit_id 与名字**同生共死**；
    `ContactDuty.BackfillManualUnitId` 提供回填通道（**暂不接线**，接线与消费层一起做）。
  * **★ 写侧铁律**：`unit_id` **只许来自"真实握在手里的对象"**，按名解析（`UnitLookup.Resolve`）
    的结果**绝不许回写** —— 那是把一次猜测固化到磁盘、跨会话生效，而且再也看不出它是猜的
    （`Pin` 注释原话："把'猜'的结果固化下来，比不登记更糟"）。机制上由
    `tests/test_manual_identity_contract.py` 钉住"C# 这层不许出现 `UnitLookup`（代码里都不许）"。
  * **验证**：`tests/test_contacts_store.py` +8、`tests/test_contacts_rpc.py` +3、
    `tests/test_manual_identity_contract.py` +5、C# 真单元测试 `csharp/tests/ManualIdentityTests`
    **33 项断言**（直接链接 `ContactStore.cs` 编成 net8.0 跑，不需要 Unity）；
    **变异检查 16/16 全被抓到**（脚手架先自检、跑完全部逐字节还原）。
  * **第 2 步（消费层，已落地）**：三处消费方改成 **"先精确、后按名"**
    （`ContactPresenter.ResolveEntry` 统一实现，通讯录行条目 / 点开单聊 / `NpcInitiativeMonitor`
    候选集都走它），两个写点也接上了 —— NPC 面板「加好友」把 `panel.unit` 的 unitID 一起上行；
    `ChatLauncher.OpenForUnit` 在**开对话那一刻**（手里有真身）顺手回填老记录。
    ★ 新增一条**开关**：`guessed` 参数。按名猜出来的对象**不许进登记表**（`Pin`）**也不许回填** ——
    `Pin` 的 ⓪ 分支排在全图扫描之前，钉错一次就是"本局所有按名查这个人的地方全挂到别人身上，
    且不报错、不缺字段"。`Pin` 的注释原话："把'猜'的结果固化下来，比不登记更糟" —— 这个参数就是那句话。
  * **第 3 步（群成员身份，已落地）**：`Room.member_unit_ids`（`{npc_id: unitID}`，**可缺项**），
    随 `open_room` / `add_room_members` 上行、随 `history()` 回包下行。**为什么必须有**：
    群成员名单的真相是 Python 的 `Room.members`（中文名），重开群时 C# **手里没有任何对象**
    —— 不落盘就只能按名猜。用 **dict 而不是与 `members` 平行的数组**是刻意的：平行数组两端
    一旦对不齐（过滤空成员、顺序不一致）就会把甲的身份安到乙头上，**不报错、只是画错脸**。
  * **第 4 步（09-17：身份随帧携带，已落地）** —— 用户拍板：**不再让 C# 按名解析**。
    四条通道各带一份精确 id，模型侧一个字都没变（`roster` / 段名 / 标签 / `Room.log.speaker`
    仍是中文名）：
      | 通道 | 载荷 | 消费点 |
      |---|---|---|
      | `room_say` | `speaker` + `speaker_id` | `ApplyRoomSay` → `FinishNpcBubble(…, id)` |
      | `room_state(speak)` | `speaking[]` + `speaking_ids[]` | `ApplyRoomState` → `SetRoomSpeaker(name, id)` |
      | 回放 `items[]` | `speaker` + `speaker_id` | `ReplaceHistory` 的 `peer` 分支 |
      | `get_context` | `roster` + `roster_ids` / `npc_unit_id` | `GameContext.BuildOthers` / `GetL1` |
      | 工具落刀 | `initiator` + `initiator_id`；`target`/`seller`/`buyer` + `*_id` | `ToolExecutor.ResolveActor` / `ResolveCounterparty` / `ResolveArgExact` |
    ★ **删掉了 `ChatPresenter.PinMemberUnitIds`**：它把**房间级**事实灌进**全局**登记表，且
    后写覆盖先写 ⇒ 开一个含「益婉容B」的群会**静默覆盖**单聊里登记过的「益婉容A」，
    之后 A 的自身段/查人/候选人集全挂到 B 身上 —— 与那次真机事故同形，只是换了个后门。
    ★ **删掉了 `ResolveInner` 的 ③ 分支**（"非中文输入先试 GetUnit"）：它是全工程唯一
    「没给 id 却走了 id 路径」的地方（"这串像不像中文"从来不是"这是不是 id"的判据）。
    现在指人只有两条路：显式 `unit_id`（②）或登记表（⓪），**都回验相等**。
    ★ **`UnitIdRegistry` 取代 `UnitLookup._pinned`**：`_pinned` 是进程内字典、只活一局，
    而"点过 AI 对话 / 加过好友"这个事实没有理由只活一局。新表**落盘**
    （`<ModRoot>/unit_ids.json`）+ **按存档隔离**（文件里记 `world`，对不上**整表作废** ——
    unitID 换档即失效，最坏情形是那个 id 在新档里恰好是**另一个人**）。
    写入点仍只有两个、同一个 `!guessed` 守卫：`ChatLauncher.OpenForUnit`（AI 对话）
    与 `NpcPanelAddContact.ToggleContact`（加好友）。纯逻辑切到 `UnitIdRegistryCore.cs`
    （零 Unity / 零 Newtonsoft）以便单测。
    ★ **`*_id` 一律不进 schema**（`CS_ONLY_ALLOWED` 钉住）：它们是 **harness** 填的，
    模型看不到也不该填 —— 一旦进 schema，模型就会开始自己编 id。
  * **验证（第 1~5 步合计）**：`tests/test_contacts_store.py` +8、`tests/test_contacts_rpc.py` +3、
    `tests/test_manual_identity_contract.py` 13、`tests/test_room_member_identity.py` 19、
    `tests/test_realm_and_identity_contract.py` 12（含第 5 步的落盘时机三条）、
    C# 真单元测试 `ManualIdentityTests` **33 项断言** + `UnitIdRegistryTests` **66 项断言**
    （都直接链接被测源码编成 net8.0 跑，不需要 Unity）；
    **变异检查 52/52 全被抓到**（`scripts/dev/mutate_check_manual_identity.py`，脚手架先自检
    **+ 每层基线必须绿**、跑完逐字节还原；`wide` 层 = 身份相关的全部契约文件，只跑 `contract`
    会让随帧那几条**假绿**）。
    全量 pytest **767 passed**。
  * **已知边界**：① **同名两人不能同群**（`Room.members` 按名去重、一个名字只能存一个 id）——
    这是"名字当键"的必然代价，真要支持得把房内键也换成 unitID（那时段名/标签会变成
    `other:Xs6JDI`，模型会开始念 ID）；② **成员立绘预热**（`RoomPortraitWarmup`）仍按名解析，
    错了只是白烘一张图（真正的行用精确 id 取图、缓存按 id 分桶，不会串脸）；
    ③ **闸门只管四类**（rel/ask/give/trade），`movement` 与 `world_ai_action` **刻意不拦**
    （用户拍板的有意为之，理由见 `room_flow.py` 提案段头部）。
  * **第 5 步（09-17 第二轮：登记表改为「存档时落盘」，已落地）** —— 用户拍板：
    **不必每次登记就写盘**，写盘跟游戏存档挂钩即可。改动：`Remember` / `Forget` 只改内存
    并置 `_pending`，写盘只剩 `Flush()`，唯一调用点是 `ModMain.OnSaveDataEvent`
    （与 `save_happened → flush_all` **同一个钩子**）；`ResetForWorld` 连 `_pending` 一起清
    （否则上一个存档的待写内容会写进新档 = 静默错人）。
    **理由**：本表与对话增量是**同一次握手产出的两份数据**，寿命语义必须一致
    （存档=固化，不存档=丢弃）；只固化一份会留下"NPC 不记得这次对话、但登记表里还留着
    这一局的 unitID"这种半状态。**不变式**：待写内容必然属于当前存档。
    **代价（明确认下）**：不存档就退游戏 / 读档 ⇒ 这次登记丢，回落按名解析（与对话增量同命运，
    点开一次对话就重新登记）。**白捡**：切到别的存档、又没在那边登记过任何人时 `Flush`
    直接返回，磁盘上别人的表原样不动。
    钉它的测试：`tests/test_realm_and_identity_contract.py::test_registry_flushes_only_on_game_save`
    （登记/自愈体内不许出现 `Save()`、`Flush` 唯一调用点按**实际调用点**钉、换档清 `_pending`）。

    ★ **本轮最要紧的产出其实是"验收方式"里的两个坑**（都不在功能代码里）：
    ① **判定规则太宽**：变异脚本的判定是"**这一层红了就算抓到**"，于是**与变异无关的红**
       （环境缺 dotnet、并发会话正好在改源码、测试本来就挂着）同样被记成 ✅。实例：
       `登记表换存档不重置` 在当时的断言集下**根本抓不到**（单独复现：去掉新断言后那 6 个
       契约文件全绿 —— 当时**没有任何测试引用 `_loaded`**），却被记成抓到。
    ② **层根本没跑起来**：`run_pytest` 原来用 `cwd=ROOT` 跑 `tests/xxx.py`，而
       `python -m pytest` 会把 **cwd 塞进 `sys.path[0]`** ⇒ `import agent_loop` 命中的是
       **同名外壳文件** `agent_loop.py`（`from .room import Room` 当场炸）而不是包目录 ⇒
       **凡 import 包的测试文件在收集阶段就红**。中招的是 `py` / `room` / `wide` 三层
       （只有**只读源码、不 import** 的 `contract` 层幸免）⇒ 这三层的变异**全部**被记成 ✅，
       **连"脚手架自检"也被骗过**（它注入的 no-op 也是靠收集错误"变红"的）。
       修法：`cwd=ROOT.parent` + 路径带 `agent_loop/` 前缀（与开发者手工命令一致）。
    修好后四层基线全绿重跑，**暴露出 3 条真漏**（此前全是假绿），逐条补断言并单独复现：
      · `补写时顺手刷了 added_at` —— `added_at` 粒度是**秒**，两次调用相隔微秒 ⇒
        "顺手刷一下"写出的值完全相同，`after == before` 退化成**同一秒内的恒真断言**。
        修法：`monkeypatch` 把时钟推后 1000 秒再补写。
      · `room_say 不带 speaker_id` —— `ChatFrameTee` 解出、`ApplyRoomSay` 接住，但
        **`DrainRoomFrames` 那一跳没人钉**（关窗重放路径照样退回按名解析）。
      · `闸门不做边界翻译` —— `translate_member_ids` 从来没被**从 `gate()` 那一侧**走过。
    脚手架另加一道**基线自检**：每个要用到的层先在**未变异**树上跑一遍，不绿就立即中止。
    ★ 顺带记一条操作教训：**变异脚本跑到一半被打断，会把变异留在工作区**
      （09-17 实发：中途 kill 后 `csharp/ChatLauncher.cs` 残留 `if (false) return;`）。
      现在脚本自带基线自检，能立刻发现这种残留 —— 但别在它跑的时候改源码。
  * **第 6 步（09-17 第三轮：模型侧零 id + 人名引用层，已落地）** —— 用户拍板：
    「模型接受的和输出的**不会带有 id**」，所以 Python 要有一个中间层，把模型写的中文名
    翻译成临时映射表里的 id 再交给工具；工具侧仍是 **id 优先、名字兜底**（这一半早已在位）。

    **这一轮反转了 09-13 的修复**（那时把 id 交给模型：`益婉容(Xs6JDI)` +
    `inspect_unit(unit_id=…)`）。反转的理由：模型一旦看得见 id 就会**自己编 id**、
    把 id 当名字念，而"这个名字是谁"本来就该由 harness 回答。三个执行点全部改掉：
    `tools/schemas.py` 删掉 `inspect_unit.unit_id`（进 `CS_ONLY_ALLOWED`：C# 仍读，
    但只有 harness 会填）、`tools/text_render.py` 四处不再输出 id、
    `tool_usage.txt` 改成"**指人只写名字**"。

    **重名怎么办 —— 唯一化显示名**（用户口径：重名是例外，不是常态）：
    `益婉容(化神殿·结晶后期)`（判别字段取不到时退化成 `名字#序号`）。
    它由新模块 `tools/unit_refs.py` 在**渲染之前**就地写进载荷（`display`），
    于是**同一个字符串既是模型看到的那个名字、也是映射表的键** —— 模型照抄即可精确指人，
    而它看到的仍然只是"名字"。触发唯一化的四种情形：同批同名两值 / C# 的 `ambiguous`
    候选块 / 跨批次才发现的重名 / 此前已判定为重名（`ambiguous_names`）。

    **人名映射表** `unit_id_map.py`（世界级、**落盘**，用户追加要求）：四个来源 ——
    自己 / 玩家（C# 新发的 `player_unit_id` **原样值**：`world_id` 是 sanitize 过的目录名，
    不能当 id 用）/ 同席与群成员（`roster_ids`）/ **查询结果**（`_harvest_ids`）。
    **为什么必须落盘**：模型历史里留着显示名，钥匙只在内存的话重进游戏就**再也解不开**
    （退回裸名按名猜，静默错人）。落盘时机与 C# 登记表一致 —— 跟游戏存档
    （`save_happened → AgentLoop.flush_all`），落点 `worlds/<world_id>/unit_ids.json`
    （目录即存档隔离）。**与 C# 那份 `<ModRoot>/unit_ids.json` 是两份东西**（那份是"手握真身"
    的裸名登记，本表是"模型见过什么"），所有者/键空间/失效条件都不同，不合。

    **边界翻译收编**：`room_flow.translate_member_ids` 整体搬到
    `DialogueAgent._inject_person_ids`（群/单聊同一入口）。理由：单聊不挂闸门 ⇒ 那条路
    以前**压根没有** `target_id`；映射表来源也不止房间；两个实现必然漂移。

    **C# 三处修复**（同轮审计出来的）：
      ① `GetIntimWith(string)` 删除 —— 好感数字原先是"拿名字回全图重解析"，同名时
         `before`/`after` 都读到**另一个**人 ⇒ `actual_delta` **恒为 0**、模型读成"没生效"
         而重复落刀（动作其实落对了人）。三处一律 `GetIntimBetween(wub, player)`。
      ② `trade` 补上"发起方必须是买卖一方"的闸（描述里一直写着，代码里没有；
         群聊有提案闸门兜着，单聊没有）。
      ③ 查询结果**顺带登记**进 C# 全局表（`RememberObservedFromPayload`）：不覆盖已有值、
         同名多值整组跳过（这条**顺带覆盖了 `inspect_unit` 的同名歧义**）。

    **验证**：全量 pytest **805 passed**；C# 真单元测试 7 工程全过
    （`UnitIdRegistryTests` **79 项断言**，新增"顺带登记不覆盖"一组）；
    **变异检查 63/63**（新增 11 条，含"接线点被删"两条 —— 它们第一次跑是**漏的**：
    单测都在直接调私有方法，把调用点删掉照样全绿，于是补了一条钉**调用位置**的契约测试）。
    ★ 又一次确认了那条纪律：**"新加的测试"必须靠变异验证才算数**（这两条就是它抓出来的）。

  * **第 7 步（09-17 复核轮：整项目复核 + 三个洞，已落地）** —— 用户要求"整体复核一遍，
    确认没有问题"。复核方式：**自己读**（Python 身份链、群聊编排、群窗删除、排队队列、
    落盘/切档）+ **两个只读子代理**分头审 C#（`ToolExecutor`/`UnitLookup`/`UnitIdRegistry`/
    `ModMain`/`UnitSnapshot` 一条；`ChatFrameQueue`/`ChatPresenter`/`ChatWindow` 一条），
    每条结论都要 file:line 证据、并逐条回读原文核对。

    复核**抓到三处真洞**（都已修 + 都补了"回退必红"的验证）：

    | # | 洞 | 症状 | 修法 |
    |---|---|---|---|
    | ① | `名#序号` 兜底按**出现顺序**发号 | 关系簿引用只有 `{name, unit_id}`（没有宗门/境界），同名者只能发序号；而"谁排前面"由 C# 遍历序决定 ⇒ 换个批次 `李四#1` 就指到另一个人 ⇒ 表里两条键**双双被撤回** ⇒ 模型历史里的显示名**再也解不开**（回落按名 = 同名时静默错人）。实测：批次1 `[甲,乙]` 建好两条，批次2 `[乙,甲]` 两条**全丢** | `_hash_display` 先查"(unitID, 真名) → 已发过的号"复用，取不到才找空号；冲突时记进"已知重名"的改成**裸名**（原来塞的是显示名，既是垃圾又永远匹配不上） |
    | ② | 模型自带的 `*_id` / `initiator*` **不被覆盖** | 这些键不在任何 schema 里，但函数调用协议不强制校验；C# 侧 **id 优先于名字** ⇒ 模型（或被玩家话术带偏）自己写一个 `target_id` 就能**压过它自己写的名字**，失败形态是"落到另一个人身上、`success` 仍为真" | `unit_refs.RESERVED_ARG_KEYS` + `strip_reserved_args`：入口（`_execute_tool_calls`）**先清空再由 harness 重注** |
    | ③ | C# 两处把 unitID 当"名字"用 | (a) `inspect_unit` 失败文案 `"unit_id=" + unitId + " 不存在（…）"` —— 那串是 **harness 注入**的，错误帧不过渲染层、原样进模型上下文 ⇒ 模型读到 id、开始照念/编造；(b) `UnitNames`/`married` 取不到名字时 `name = idStr`，而 `name` 是全工程"人名"的载体（Python 据此登记映射表、渲染层据此印给模型） | (a) 文案改成"未找到「真名」的精确身份记录（可能已离世/离场，或换过存档）"，**不给 id**；(b) `name` 取不到就**留空**（`_pair` 只认"同时有 name 与 unit_id"，渲染侧只印非空名字 ⇒ 这条不显示也不登记） |

    另外补了一条**跨语言契约测试**：`unit_refs.PERSON_ARGS`（Python 注入哪些键）
    必须与 C# 实际读的指人参数**同集合**（`ResolveArgExact(args, "…")` 全扫）——
    此前两边靠 `args[key + "_id"]` 这个约定接上，谁改名字都不会报错，只是**静默失效退回按名**。

    **复核结论**：Python 零 id 面（schema / 渲染 / 提示词 / 事件流）、中间层（注入点位置、
    渲染前收集、映射表四来源、落盘与切档）、群聊编排（锁序、投递游标、指针、提案、删除 fan-out）、
    排队队列（`turn_slot`→`agent_lock` 顺序、`say_lock` 钉帧序）、群窗删除、C# 全局表
    （跨档校验 / 惰性加载 / 存档时落盘 / 不覆盖）**均无逻辑错误**；端到端跑通
    （假桥 + 脚本 LLM：模型侧**每一帧**零 id、显示名 → `target_id` 精确到位、落盘往返正确）。

    **复核发现的 C# 群窗问题 → 用户拍板"确认有问题就直接改"，同日全部落地**：

    | # | 症状 | 修法 |
    |---|---|---|
    | ① | **玩家群消息画两遍**：本地同步 `AppendUserMessage`，Python 的 `room_say` 回声又画一次 —— `_roomEchoSeen` 靠 `uid` 去重，而**本地那条从来没有 uid**（`SendRoomMessage` 是 fire-and-forget 的 event，回执里拿不到 seq） | 发送时把文本压进 `_roomEchoPending`，回声按 FIFO 对号 ⇒ **只销账不再画**；换窗/重放整表作废 |
    | ② | **弃权者留一条空气泡**：`phase="speak"` 无条件开泡写「甲：」，而弃权不写流水 ⇒ 永远等不到 `room_say` 定稿；模型回【沉默】时哨兵还会被流式打进气泡 | Python 弃权时**显式发 `phase="silent"`**（加法：旧 DLL 落空 = 原行为），C# 撤泡且不复位忙碌态；`end` 与下一个 `speak` 各留一道兜底（新判据 `ActiveBubbleHasNoBody`） |
    | ③ | **轮未跑完时重开窗，同一条出现两遍**：未闭合段不受基线约束（有意为之：正在流的那半截要补上），但段内可能已有**落了账**的 `room_say`，而回放读的正是 `Room.log` | 未闭合段内出现 `0 < seq <= baseline` 的 `room_say` ⇒ **整段丢**（只丢 say 会留下永远等不到定稿的半截泡） |
    | ④ | **`dissolve_room` 失败零反馈**：列表乐观删除，失败分支只写日志 ⇒ 群"自己回来"且无解释 | 失败时 `SetStatus("解散失败：…")` + 照旧 `RequestRooms()` 拉回真相 |
    | ⑤ | **群被解散后玩家再发言卡忙碌 240s**：Python 的 `KeyError` 分支不发终帧，而终帧是复位忙碌态的唯一依据 | 补发 `phase="end"` + 文案（Python 侧，先于上面四条落地） |

    **仍在的已知问题**（记在 `docs/archive/2026-09-17-群聊编排/设计定稿.md` §19.7）：`world_id` 双重百分号编码
    （实机 ASCII id 下**零影响**，已实测两种情况路径相同；修它要动全工程每条存档路径，收益为零）；
    `dissolve_room` 回包丢失时 C# 窗仍显示死群（未证实，需真机）；
    WS 重连后群窗不重同步；`stats_update` 在群窗被 room_id 闸丢弃。

    **验证（复核轮）**：全量 pytest **830 passed**；`dotnet build -c Release` **0 错误**；
    C# 单元测试 **8 工程全过**（`ChatFrameQueueTests` 74 → **75 断言**）；
    `mutate_check_chat_frame_queue.py cs` 整跑 **7/7 全捕获**；
    其余 10 条新变异用**逐文件回退**单独验证（改一处 → 对应测试真的变红 → 逐字节还原 md5 校验通过），
    其中一条**第一次没被抓到**（"空气泡判据"只查了成员存在、没钉表达式）⇒ 补钉判据正文后才红。
    ⚠ **没有整跑**两个脚本的 `py`/`wide` 层 —— 它们会临时改
    `UI/ChatWindow.cs` / `UI/ChatPresenter.cs`，而并发会话正在改那两个文件，
    整跑会把对方运行期间的改动一起还原掉（09-17 已实发过一次，见脚本头部告警）。
* **★ 游戏时钟：`roundMonth` 是 0 起总月数，年必须除出来 ★**（09-13）。
  `g.world.run` 是 `WorldRunMgr`（**属性**，不是字段 —— `g.world` 是 `WorldMgr.run` 属性），反编成员只有
  `roundMonth` / `roundDay` / `roundDayResidue` / `roundDayMax`：**没有 `roundYear`，也没有直接给"年"的东西**。
  * **账面月 = `roundMonth + 1`**（1年1月=1、2年1月=13；`(账面月-1)/12+1` 年、`(账面月-1)%12+1` 月）。
    漏掉 `+1` → **整体差一个月且完全静默**。两条独立佐证：① 真机开局首月载荷 `DataUnitLog.LogItemData.month = 1`；② 参照 mod 两处写法（`roundMonth/12+1` 年 + `roundMonth%12+1` 月；`ConvertToYearsMonths(roundMonth+1)`）与上式互相印证。
  * `roundDay` 同样是 **0 起**的**月内**日（各处均以 `roundDay+1` 报日）—— 月 0 起 + 日 0 起是同一个约定位，`CurrentGameDay()` 的 `m*30+d` 近似也建立在此。`CurrentRoundDay()` 返回 1 起值，读不到返回 `-1`。
  * **换算只许有一份**：算式的唯一落点是 `UnitSnapshot.SplitAccountMonth(账面月, out 年, out 月)`；`CnYearMonth`（年月）与 `CnDate`（年月日，日 `<=0` 时**退化成"年M月"、绝不编造日**）都走它，`GameContext.NowBlock` 的结构化年月也走它。经历「近况」前缀、玩家消息时间戳、L1「当前时间」段三者共用；测试 `tests/test_game_time_stamp.py::test_year_month_conversion_has_single_source` 全仓扫描 `(x-1)/12+1` 只允许命中一次。
  * **日粒度不能省**：只报年月的话，同一个月里 1 号和 28 号**长得一模一样**，"你三天前才来过"这类话就永远说不出来。
  * **读日历必须容错**：`CurrentAccountMonth()` / `CurrentRoundDay()` 取不到返回 `-1` → `CurrentDateText()` / `CurrentTimeLabel()` 返回**空串** → 调用方原样放行（时间戳绝不阻断玩家发言）。世界未加载/切档瞬间真的会读不到。
  * **标度自检留痕**：`ChatPresenter` 在标签**变化时**打一行 `[ChatPresenter] 玩家消息时间戳 [1年1月3日]（roundMonth=0）`（**换日**才打，不刷屏）。判读办法：同一游戏月内它与经历「近况」条目前的 `(N年M月)` 必须一致；若整月差一（近况 `(1年1月)` 而这里 `[1年2月]`），就是 `+1` 那处标度错了 —— 那是本机制唯一的一处魔法常量。

## 七、C# 侧证据链与环境速查

| 对象 | 值 |
|---|---|
| 游戏本体 | `E:\SteamLibrary\steamapps\common\鬼谷八荒`（Unity 2020.3.9，IL2CPP，版本 **v1.2.113.259**） |
| 运行时 | MelonLoader **0.5.4** + UnhollowerRuntimeLib **0.4.18** |
| 主证据日志 | `C:\Users\<用户名>\AppData\LocalLow\guigugame\guigubahuang\Player.log`（当前运行；`Player-prev.log` 上一次）。mod 的 `P()` 走 `UnityEngine.Debug.Log` 落在 Player.log（**不是** MelonLoader 日志） |
| 后台线程日志 | `MelonLoader/Logs/AgentLoopBridge-thread.log` |
| MelonLoader 日志 | `<游戏根>\MelonLoader\Logs\`（注册成功的类型会留下 "unsupported parameter" 方法扫描警告，可用于反推注册进度） |
| mod 打点样式 | `[AgentLoopBridge] UI-2 注册完成（含 AB 两面板）` |

* ⚠️ **`Player.log` / MelonLoader 日志必须用 `grep -a`**（文件含特殊字节，普通 grep 静默返回空，已踩坑两次）。
* 崩溃读法：崩溃标志 `Crash!!!` 与 `OUTPUTTING STACK TRACE`；栈里出现 `il2cpp_class_get_static_field_data` = 互操作层访问游戏类静态字段时踩雷（try/catch 拦不住）。
* 崩溃读法补充：`Crash!!!` 之后无任何托管异常 = 原生层；有 `Exception` 行 = 托管层（可 catch）。
* **两条字段漂移（都无害，但别照抄）**：
  ① `tool_result` 帧的工具名键是 `tool`，而 `ChatFrameTee.Decode` 读 `name` ⇒ 缓冲帧 `Name` 恒空
  （详见 [`bridge-contract.md`](bridge-contract.md) §2「已知字段漂移」）；
  ② 失败帧的标志键是 **`error`**（`npc_reply` 上），**不是** `isError` ——
  `ContactDuty` 读 `payload["error"]`，旧文档写的 `isError` 是错的。

---

## 附：本文原来的案例叙事去哪了

这些规则都是从真机事故里逼出来的。**事故的逐条排查过程不在本文** ——
本文只留"至今仍成立的约束"，因为查表的人需要的是**这条线不能碰**，不是当时怎么发现的。

原 `APPENDIX.md` 里的 20 篇带日期的排查记录（D.8~D.27）已拆进
[`docs/archive/`](../archive/)，按日期查。要追某条规则怎么来的：

```bash
grep -rn "<事故关键词>" docs/archive/
```
