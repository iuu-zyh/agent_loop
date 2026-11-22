# 功能实现要点（自主交互 / 工具文档分层 / 剧情窗按钮）

> 数据源：原 `docs/reference/APPENDIX.md` §G 拆出并**去重**（2026-11-20）。
>
> 本文只留**别处没有**的内容。已挪走的：
>
> | 原小节 | 现位置 |
> |---|---|
> | G.1 剧情窗「AI 对话」按钮 | 本文 §1（**独有**，UI 架构另见 [`ui-interception-spec.md`](ui-interception-spec.md)） |
> | G.2 主动传音 / G.3 同格确认窗 | 本文 §2（判据细节独有；UI 层的三分流图见 [`ui-interception-spec.md`](ui-interception-spec.md) §7.5） |
> | G.4 travel / places / 背包 | 本文 §3（工具层要点） |
> | G.5 自主交互怎么测（10 道闸） | 本文 §4（**独有**，排查「为什么没触发」必看） |
> | G.5.4 工具文档两层分工 | 本文 §5（**独有**，改提示词前必看） |

---


## 1. 原生剧情窗「AI 对话」按钮（`csharp/UI/DramaAiOption.cs`，已实装）

| 项 | 结论 |
|---|---|
| 挂点与捕获 | Harmony **Postfix `UIDramaBase.InitData(int, DramaData)`**——覆盖全部剧情窗变体；剧情推进多页会反复 `InitData`，故**先销毁同名旧钮再注入**（幂等）。`DramaData.unitLeft`/`unitRight`/`unit` 里**非玩家**者即说话 NPC（**无 NPC 的系统公告类不注入**）。原文 = **`DramaTextCapture.LastUiText`**（`UIDramaBase.GetDialogueText` 的返回值 = 屏幕上的成品句，零推断）；兜底才用 `DramaTool.lastOpenDramaDialogueText`（**已证伪的模板路**——`dialogRoleChat1` 的 1050 条变体里 649 条带 `{call\|B\|A}` 之类占位符）；都取不到用空串——**不阻断**。每个剧情页打一行 `注入诊断`（模板 / 宿主 / 克隆 active / 世界坐标 / 屏坐标 / 屏内 / 尺寸），"看不到按钮"时它就是判据 |
| 注入范式 | **① typed 属性直取（09-13 第五轮，首选）→ ② 按名深搜 → ③ 第一个「可用」Button**。<br>**① typed**：游戏把这几枚按钮作为 **typed 属性**暴露在剧情窗基类上（互操作程序集实证）——`UIDramaDialogueBase` 的 `[p] Button btnRightLook / btnLeftLook / btnNext / btnSkip`、`[p] Text textRightName / textLeftName`；大图窗是**另一个平级家族** `UIDramaBigTexture : UIDramaBigTextureBase : UIDramaBase`（**没有** `btnRightLook`/`textRightName`，只有 typed `btnNext`/`textNextTip`）。**用例** `TypedTemplate`/`TypedNameRect`：`ui.TryCast<UIDramaDialogueBase>()` / `TryCast<UIDramaBigTextureBase>()` → 直读引用。**为什么用 `TryCast` 而不是 `as`**：IL2CPP 下 `as` 对返回的包装体**静默给 null**（`UiRects.Of` 已踩过）。**收益 = 消灭一整类脆弱**：不必容忍 `G:` 前缀、预制件改名也不受影响。**全程只读**，任何一步落空只返回 null → 退回 ②，不改变行为。<br>**② 按名深搜**：同名四个候选，**容忍 `G:` 前缀**（`NameIs`：真名 `G:btnRightLook` 与字段名都认）。**③ 兜底**「第一个**可用**的 Button」（`FindBestTemplate`）。<br>**`IsUsableTemplate` 四条**（typed 引用也要过）：`activeInHierarchy`（**不是** `activeSelf`，父链可能整条关着）+ `RectTransform` 有尺寸 + **有可改的 `Text`/TMP** + 有 `Image` 时须 enabled 且 `alpha>0.05`。<br>**为什么必须 typed 优先**：09-13 事故 = 名字路四个候选全 miss → 退兜底 → 捡到 `G:btnNext`（翻页指示图标，**无文本组件**）→ 日志报"已注入"、玩家屏幕上**什么都没有**。<br>→ **`clone.SetActive(true)`** → **先定尺寸再定位**（宽度决定右缘；**先定位后加宽**会让矩形以 pivot 为中心向两侧长回去、压住左邻 —— 09-13 真机「AI 对话」同时压住「查看」和 NPC 名签就是这个成因）→ **落点 = NPC 名签的正左侧**（名签同样 typed 优先：`textRightName`/`textLeftName`，按侧别取、落空退另一侧、再落空才按名深搜）→ 再退「模板左侧」→ 再退剧情根 + 屏幕右下角 → `onClick = new ButtonClickedEvent()` **整体换新** + `NpcPanelButton.StripOperationItemPublic` 清组机制组件 → 文案「AI 对话」（**`Text` + `TextMeshProUGUI` 双支持**）→ `ClickUtils.Attach` 三步写法；按钮名 `AgentLoopAiOptionButton`；该 NPC 回合进行中点击 → 静默丢弃。**禁用 `GetComponentInChildren<Button>(true)`**（同 D.3 铁律）。<br>**诊断**：每页 `注入诊断` 首字段即 **`模板来源=`**（`typed btnRightLook` / `typed(BigTexture) btnNext` / `按名深搜 X` / `兜底(首个可用)`），落点里带**名签来源**（`typed textLeftName` / `按名深搜 G:…` / `名签两路全 miss`）——**一眼看出走的哪条路**，不必再靠猜。typed 与按名都落空时额外打一行 `剧情窗 Button 清单`（全窗 Button 的真名 + 自活/链活 + 有字/无字 + alpha + 尺寸，上限 40 条） |
| 落点：NPC 名签左侧（09-13 用户指定） | `FindNpcNameRect`：**侧别判据 = 名签文本**（09-13 第七轮改）——哪一侧名签写着 NPC 的名字，NPC 就在哪一侧；名签必须「链活 + 有字 + 精确等于 NPC 名」才采信，判不出才退回**已证伪**的指针比对（并在 `落点=` 里标明 `侧别退指针兜底(已证伪，恒右)`，免得哪天又被当成可信判据）。**⚠ 旧判据（`DramaData.unitLeft/unitRight` 与 npc 比 native 指针）已废**：真机证这两个属性**返回同一个单位**，于是 `r.Pointer == npc.Pointer` **恒为 true**、`onRight` 恒真——真机这些剧情 NPC 恰好在右所以**结果一直碰巧对**，NPC 一旦出现在左侧就会锚错边。取矩形优先 typed `textRightName`/`textLeftName`（`Graphic.rectTransform`），落空退按名深搜（`NameIs` 容忍 `G:` 前缀），该侧没有退另一侧。`PlaceLeftOf`：**克隆体右缘 = 锚点左缘 − 8px**，**用世界坐标算**（`右缘 = position.x + (1−pivot.x)·rect.width·lossyScale.x`）——名签与克隆体常常不在同一父节点、锚点/轴心也未必相同，拿两个 `anchoredPosition` 互相加减会错位；只改 **x**、保留模板的 **y**（名签与「查看」同一行）。诊断行 `落点=` 现在同时给出**矩形来源与侧别来源**，形如 `名签左(G:textRightName w=200；typed textRightName；名签右=司空雨珍)`；其它取值：`模板左(无名签)` / `模板左(名签定位失败)` / `旧兜底(右下角)` |
| 模板两条真机教训（**都是同一个坑**） | **① 名字必须容忍 `G:` 前缀**：本作 UI 节点**真名带前缀**，字段名不带 —— 09-07 在 HUD 圆钮上踩过一次（`G:btnPlayer`/`G:btnEmail`，prefer 列表全 miss），09-13 在剧情窗**又踩第二次**：四个候选全 miss → 退兜底 → 捡到 `G:btnNext`（翻页指示图标，**无文本组件**）→ 日志报"AI 对话按钮已注入"、`克隆active=True/True 屏内=是`，**玩家屏幕上什么都没有**。**② 模板必须有可改文本**：否则克隆出"没字的空热区"，`SetLabel` 只会打一行 `警告：克隆体无 Text/TMP 组件` 就过去。教训：**"已注入"日志 ≠ 玩家看得见**——判据要看 `注入诊断` 那行的**模板真名 + 克隆 active + 屏坐标**三件套 |
| 点击后 | `ChatLauncher.OpenForUnit(npc)` 开面板 + `WsClient.SendGameDrama(npcId, text, dramaId, speaker)` → `npc_initiative {intent:"game_drama", text:原文, drama_id, speaker}` → Python `handle_initiative` **跳过全局节流**（玩家主动点击）→ `format_game_drama_message(text, speaker)` 包装舞台指令 → 正常 turn → `npc_reply` 流进已开面板 |
| **引文归属：屏幕句是谁说的（09-13 第七轮定案，修"模型猜反"）** | **症状**：NPC 主动赠送那一页（屏幕句 `我这里有一个青须藤*48准备赠于你，你需要此物吗？`，选项「我收下了/我不需要这个」）点「AI 对话」，NPC 却开口道谢——把这句当成了**玩家说的**（真机日志 `AI 对话已发起：惠都 dramaId=21701 text=我这里有一个@w_…@准备赠于你…`）。**根因**：屏幕成品句**本身不含说话人**，而早先的舞台指令刻意中性（不点名），等于把归属权丢给模型猜；同一句两头都可能说，猜错就是喂一个**反的事实**。<br>**★ 判据 = 配置列给"哪一侧在说" + 名签文本给"那一侧是谁" ★**（两步拼起来，缺一不可）。<br>**① 侧别来自配置列** `ConfDramaDialogueItem.speaker`：语义出自游戏自带表头 `Mod/modFQA/配置修改教程/配置表头/DramaDialogue.xlsx` 第 7 列「说话者」，紧邻注释 **`1-左` / `2-右`**；全量 21926 行佐证（分布 `2:13825 / 0:4589 / 1:3476 / -1:34 / 12:2`；**115 行 `npcLeft=0 且 npcRight≠0 → speaker=2`**；2602 行两侧无人 → `speaker=0`；`uiType=2` 奇遇界面多为 `-1`）。`-1`/`12` = 无说话者/脏数据 → 走兜底。真机与立绘亮侧**同答 6/6**（speaker=1↔左亮 ×2；speaker=2↔右亮 ×4）。<br>**② 侧别 → 人** 用**名签文本**：`UIDramaDialogueBase` 的 typed `Text` `textLeftName`/`textRightName`——哪一侧名签写着玩家名，那一侧就是玩家。**真机实证（09-13 第七轮）**：`名签 左="唐炎"[活] 右="司空雨珍"[活] 玩家名="唐炎" NPC名="司空雨珍"` —— 名签有字、链活、与玩家名/NPC名**精确相等**。**这一步同时把"左=玩家"从假设变成实测事实**（此前只有三条间接依据：用户判断 / `ShowDramaService` 构造 `unitLeft = playerUnit` / 唐炎恒在 `x=-3`）。<br>**③ 采信门槛（硬）**：名签必须「**整条父链活着**（`activeInHierarchy`，不是 `activeSelf`）+ 有字 + 精确等于玩家名或 NPC 名」三者齐备才用；**任何一步含糊 → 立刻退立绘兜底**，绝不含糊时硬给答案。名签路与立绘**无条件交叉校验**，不同答就打 `⚠说话人判据冲突`。<br>**④ 取值时机 = 与文本同源同行**：`DramaTextHook` 本就 patch 了 `UIDramaBase.GetDialogueText(ConfDramaDialogueItem item, DramaData dramaData)`——那个 `item` **就是本页那一行配置**，顺带读 `item.speaker` 即可，"谁在说"与"说了什么"同一行同一次调用，不可能串页。`DramaTextCapture.NoteUiText` 里侧别与句子**同进同出**（句子非空才更新侧别）。<br>**⚠ 四条已被真机否掉的路（勿回退）**：<br>**(a)** `imgBgPlayerDark`/`imgBgOtherDark` 具名压暗遮罩——按反编 `#Strings` 堆相邻性推断，真机探针打出 `0处 0处` + `暗色候选=(无)`。教训：**堆里相邻 ≠ 同类**。<br>**(b)** `UIDramaDialogue.Play(WorldUnitBase, Action)` 的 `unit` 参数——方法确实存在、钩子也确实挂上了（`[剧情说话人] 钩子已挂：UIDramaDialogue.Play（2 参数）`），但**从头到尾没被调用过**（全程无 `Play 首次命中`）。教训：**"方法存在" ≠ "方法被调用"**，与 (a) 同类。<br>**(c) ★ 用 `DramaData.unitLeft`/`unitRight` 做"侧别→人"—— 09-13 第六轮真机证伪，代价最大的一条 ★**：`speaker=1 → data.unitLeft`、`speaker=2 → data.unitRight` 再问 `IsPlayerUnit`。真机打脸：**这两个属性返回的是同一个单位**（`｜左=许其[NPC] 右=许其[NPC]`、`｜左=蒋博明 右=蒋博明`、`｜左=魏盼香 右=魏盼香`，三例一致），于是 `speaker=1` 与 `=2` 必然给出**同一答案**——`speaker=2` 页碰巧对（NPC 恰在右且在说），**`speaker=1` 页稳定错**（玩家在说却输出 `npc`）。是第六轮加的交叉校验当场抓出（`⚠配置级=npc 立绘=player（配置 speaker=1）`）。**教训（比"要查数据源"更狠一层）：用错的数据源比启发式更危险** —— 启发式判不出会闭嘴（返回 `""` → 中性文案），错的数据源会**自信地**给出反事实，日志还长得像"配置级，零推断"那样可信。<br>**(d)** 立绘亮度——**降级为兜底 + 交叉校验**（每剧情页两次，不是每帧）。它**只独立测出"哪一侧亮"**（这一项真机 6/6）；"亮的是谁"另算：**亮侧(实测) + 玩家在哪一侧(名签实测) → 相同则玩家在说**。**⚠ 09-13 第七轮之前这里是 `return leftIsSpeaker ? "player" : "npc"`，把"左=玩家"焊死在代码里**——于是"侧别→人"这一步**从来没被测过**，日志却长得跟实测一样；现已在 `diag` 里**明说**依据是实测还是假设（`名签实测玩家侧=左` / `名签判不出玩家侧→按「左=玩家」假设`，后者只在非对话窗家族没有名签时出现）。（保留细节：立绘节点名 = 角色名而非 `rimgLeft`/`rimgRight`，后两者是父链未激活的空占位 `亮=0.000`；左右按世界坐标 `x` 符号分，玩家 `x=-3`、NPC `x=+3`；黑滤镜真名 `G:imgBlackFilter`。）<br>**探针与自校验**：每页一行 `说话人判定`，内含 **① 两路结论 ② 两侧单位（坏源，留作对照）③ 两侧名签文本+链活 ④ 玩家名/NPC名 ⑤ 交叉校验结果**。成功形如 `配置侧+名签定玩家侧(左) → player（配置 speaker=1）｜左=司空雨珍[NPC] 右=司空雨珍[NPC]｜名签 左="唐炎"[活] 右="司空雨珍"[活] 玩家名="唐炎" NPC名="司空雨珍"｜交叉校验 立绘=player`。**这一行同时是判据、校准台与回归证据**。<br>**另**：`注入诊断` 的 `屏坐标=` 曾长期不可信——它用 `ui.canvas.renderMode` 判相机，踩了**嵌套 Canvas 的序列化残留**（与 `HoverTip` 那个"气泡一个都不出"的真机事故同一坑）。已改为走 `rootCanvas`（`DiagCamera`），并加 `相机=` 字段让坐标自证。**只影响日志，不影响摆放**（`PlaceLeftOf` 走世界坐标，不经相机） |
| **@ 标记必须解成人话（09-13 收口，与上条同一条消息的另一半）** | `UIDramaBase.GetDialogueText` 返回的是**带标记的编码文本**，真机原样：`我这里有一个@w_rIsZH3\|1\|5031101\|48\|@准备赠于你`、`（看起来@q_郦安\|Lepxop@想和我说些什么。）`。直接发给模型，它读到的是 hash 乱码——**不知道送的是青须藤、也认不出是谁**。修法 = `DramaAiOption.ExtractText()` 的返回值过一遍 **`UnitSnapshot.CleanLogText`**（世界日志那条路已有的解码器，当初**就是拿这句剧情原文当样本写的**，只是从未挂到剧情这条路；本次把它的可见性由 `private` 改为 `internal`）。规则：正则 `@([a-zA-Z])_([^@]*)@` → 逐字段试解（纯数字字段查 `ItemProps` 表、过 `GameTool.LS` 取中文名；字段0 当 unitID 查单位名；都不行退回字段0 并留一行 `@标记未解`）。**判据不写死下标**靠的是 `ItemProps` id 从 10001 起，`1`/`48` 这类序号字段查不到行、不会误命中 |
| 舞台指令语义（**归引用、不归主动**，09-13 修） | `speaker="npc"` → `（游戏内交互：你刚对玩家说了这样一句——「{原文}」）…`；`speaker="player"` → `（游戏内交互：玩家刚对你说了这样一句——「{原文}」）…`；**未知 → 旧中性文案逐字不变** `（游戏内交互：你和玩家之间刚经过了这样一幕——「{原文}」）…`（三种共用收尾 `（请以你的人设与当下心境自然地接住这个话头，不要复读括号内容。）`）。**只陈述"这句出自谁口"，绝不说谁主动**——所以玩家点「闲聊」（NPC 回的那句问候归 NPC）与 NPC 主动找上门，两种场景都不会喂出反的事实。UI 分隔条标签 `剧情应对` → `游戏交互` |
| 设计定位（关键决策） | **旁路选项版 = 加法不是接管**：不 `return false` 抑制原 UI、不碰 `onDramaEndCall`/`onOptionsClickCall` 结算回调 → 原生选项**机制完整保留**；LLM 只做**表演层**开口，不替玩家做选择 |

**工具唤出的原生剧情窗必须排除 —— 两道闸（09-13 重构）**：`world_ai_action` 五 op（1031 双修 / 1034 疗伤 / 1037 论道 / 1041 提升心情 / 1044 邀约）与 marry 婚礼 22201 走**原生** `DramaTool.OpenDrama` → `UIDramaDialogue` 家族 → 同样继承 `UIDramaBase`，会被注入——危害是**语义错位**，不是崩溃。

* **闸 1 · 同步窗**：`ToolExecutor.Execute` 的 `try/finally` 置 `DramaGate.SuppressAiOption`。原理 = **单线程 + 同步调用**：`Execute` 跑在主线程（`MainThreadDispatcher` ← `g.timer.Frame`，且一次只取一个任务），而游戏的 `OpenUI → InitData` 在同一调用栈内完成 ⇒ 执行期内弹的窗**必然**是这次工具调用的下游。`finally` 是正确性要求（不清标志会让此后所有窗都不注入，且零报错）。
* **闸 2 · 异步窗（闸 1.5，`DramaNativeClaim.cs`）**：`Execute` **返回之后**才弹的窗闸 1 够不着 —— 实证：①邀约 81002 随 `CreateAction` 同步弹（闸 1 挡得住），玩家点「接受」后游戏的 lambda 才弹 **81012**；②求婚 `ShowConfirm` 的 `onOkCb` 是「`onOk()` 之后**紧接着** `DramaGate.Resolve`」，而 22201 就在 `onOk` **里面**开 —— **22201 还开着动作就算结束了**。
  **凭证认人不认 ID**：工具发起动作时 `ClaimFor(目标 NPC unitID)`，窗开时 `TryClaim(unitID, ui)` 命中即跳过注入。凭证活到 **`UnitActionPending.Complete/Remove`（原生 `OnEnd`：邀约/传功/讨要/赠予原生路）**、**`DramaGate` 槽位 `Resolve`（点确定/拒绝、或 120s 超时：marry/结拜/解除关系/赠予自制窗）**、或 `ClaimTTL=120s` 兜底。
  **`Resolve` 那条腿怎么接的**：`ShowConfirm` 类工具不走 `UnitActionPending`，只能靠槽位。接法是**订阅 `DramaGate` 已有的公开事件 `OnResolved`**（`DramaGate.cs:42`）——**`DramaGate` 零改动**。`Claimed()` 拿到 pending 标记后读 `res["__slot__"]` 调 `BindSlot`。时序正好：`onOkCb` 里 `onOk()`（marry 在其中**同步** `OpenDrama(22201)`）**先**跑完并把会话建好，`DramaGate.Resolve` **后**跑 —— 释放凭证时 22201 已被会话接管，翻页照样挡得住。
  **`ReleaseUnlessPending` 的例外**：同一 NPC 的第二个工具调用撞窗失败时也会走到它。此时表里那张券属于**上一个真挂起中**的动作，替它收回就等于把它的后续窗（如 22201）放出来。判据 = 券绑了**未 Resolve** 的槽位（`Slot != 0`）→ 不收回。
  **为什么不是"命中即消费"**（用户 09-13 拍板）：一个动作可能牵出**多重**剧情，消费掉第二重就漏了。**多翻页**另靠一条**会话指针**（`ui.Pointer` native 指针，**不是**托管引用 —— Unhollower 不保证同一 native 对象每次给出同一代理）+ `SessionTTL` 兜底：同实例的后续翻页直接算我方，**与凭证生死无关**（否则 marry 的 22201 第二页必漏）。
  **为什么不用 `ReferenceEquals`**：见 D.1 —— 代理实例不保证同一。
  **旧实现（已删）**：按 dramaId 黑名单 `{1031,1034,1037,1041,1044,22201}` —— 分不清"谁发起的"，**游戏本体自己**发起的同名剧情（NPC 用游戏 AI 找你双修、过月事件触发论道）也被一起误挡。
  **已知边界**（09-13 补 `Resolve` 吊销后收窄）：4 个 `ShowConfirm` 类工具（marry/结拜/解除关系/赠予自制窗）原先**没有任何代码吊销凭证**，只能干等 `ClaimTTL=120s` 自然过期 —— 表现为"用完工具后 120s 内自己找同一 NPC 开窗，那一窗没按钮"（用户实测连招：送完东西马上接着聊）。现已由 `OnResolved` 订阅修掉，**marry 的该边界归零**。残留仅两条，都极小：①窗实例 native 指针在 `SessionTTL=120s` 内被 Unity 复用给同 NPC 的新窗 → 误认领（理论风险，靠 TTL 自限）；②`ClaimTTL` 兜底路径（窗开了玩家一直不点、槽位始终不 Resolve）仍会留券到 120s。

**判定时机 = 回合收尾**（C# 收到 `npc_reply(initiative=true)`），**不在触发时**；但**"能不能当面弹出"以「触发那一刻」的判定为准**（用户拍板）——否则异地传音的 NPC 在你等回复的几十秒里走进同一格，就变成"我没点同意，怎么就当面开窗了"。判定信息（`UnitLookup` + `pointX/Y`）C# 本地就有，**Python 侧零协议改动**。

**三分流**（`ContactDuty.OnUiEvent`，主线程）——每次 `npc_reply` 都打一行决策现场：
`[ContactDuty] 主动开口分流：①/②/③ ｜ npc=… 触发时可当面=… 现在同格=… 该npc窗=… 别的窗=… 忙=…（原因）正文=…`

| 分流 | 条件 | 行为 |
|---|---|---|
| ① 直播已读 | 对话窗正开着且就是该 NPC（`UnreadStore.IsChatOpenFor`） | 不登记未读 |
| ② 当面开口 | **触发时可当面**（`CanPopAtTrigger`：该 NPC 最近一次触发时 `sameGrid && !busy`）&& 回复时仍同格 && 未开任何对话窗 && 玩家不忙 | `ChatLauncher.OpenForUnit(unit)` 弹 UI |
| ③ 未读 | 其余（异地/忙/已开着别的 NPC 的窗） | `UnreadStore.Mark(npc, text)` + 红点 + HUD 级横幅 |

* `error=true` 的失败兜底帧**不进未读**；同格但战斗中/模态窗中或已开着别人的窗 → 降级未读（不抢窗口）。
* **动作完成 → 自动开窗**（`csharp/ActionWatcher.cs`）：**动作工具真的执行成功那一刻**（挂起信息存 `_deferredAction`，由 `OnDramaResolved` 补发）四条全中 → `ChatLauncher.OpenForUnit`：① 这是**自主开口**回合（`NoteInitiativeTurn` 标记；玩家自己发起的回合窗本来就在，`SendPlayerMessage` 撤销标记；TTL 900s）② **本回合没有因"当面确认窗 + 点同意"开过窗** ③ 此刻没有任何会打断玩家的界面（与"能弹确认窗"**同一把尺子** `BusyReasonText()`）④ 该工具属**动作类**（只读三件套不算）。
  **用户第三版拍板（现行）**：同格互动无论对话还是动作都**立即弹确认窗**，点确认后立即弹对话 UI，**就不再需要"根据工具结果立即打开对话 UI"**。所以判据是**"这一回合有没有因为确认而已经开过窗"**，不是"同格/异地"四个字。**同格+忙是空集**（忙 → Python `BUSY_NOTE` + 执行层忙碌闸拦下全部 6 个动作工具，根本不会发 `call_tool` 帧过来）。
  **★ 10-16 收窄（异地动作也走确认窗了）★**：确认窗判据改为 `needConsent = !busy && (同格 || 动作意图)` （用户拍板的矩阵，见 `NpcInitiativeMonitor.FireOne`）。此前「异地动作」不弹确认窗、靠本类事后补开窗；**现在它也弹确认窗并预开对话窗**（动作会弹原生界面，无预警地弹才是真打扰）⇒ `windowOpenedByConsent=true` ⇒ 本类**同样不再补开**。收窄后本类实际覆盖的只剩：**异地 + 言语向意图 + 空闲**的回合里模型自发调了动作工具（意图只给倾向，模型仍可能动手），以及 `_diag_initiative_force.txt` 的 `forceRemote=ON` 强制路径。这层兜底**刻意保留**——它挡的正是下面那起真机事故的同类。
  **为什么需要**：真机 00:21 云含 `intent=recent` —— 触发时异地 → 不弹确认窗；但动作闸对"异地+空闲"放行，模型自己推理出"既然异地，可以用 `world_ai_action` 远程"真的执行了 23.6s，而玩家屏幕上什么都没发生。协议：Python `call_tool` 帧新增 `npc_id`（跨语言契约测试 `test_ws_bridge_call_tool_carries_npc_id`）。**异地是否允许动作维持现状（允许，可远程）**——本机制只补"看得见"，不改征求同意。
* **未读本体与红点**：消息本体**在该 NPC 的 session 账本**，打开对话 → `get_history` 回放可见；`UnreadStore` 只是"没看过"标记（`Mark/Clear/Has/Peek` + `NotifyChatOpen/NotifyChatClosed` + `ActiveChatNpc`/`AnyChatOpen`/`TotalCount`/`Latest`），**不持久化**（重启红点消失、历史仍可回放）。
* **HUD 未读角标**（`MapMainContactButton.BuildBadge()`）：**抗锯齿真圆 + 暖白描边**（`UnreadStore.UnreadRing` / `UnreadRed` = #FF3B30，与通讯录行红点/横幅**同源同色**），圆心落在**可见圆钮 45° 环线上**（半个角标压住按钮），直径 = 可见圆钮直径 × 0.30（钳 11~20px）；可见直径按"子孙 ≤2 层里带 sprite 的最大层"实算。动效 = 1.2s 呼吸（`localScale` 1.00↔1.12 绕 pivot + alpha 0.88↔1.00），走 `g.timer.Frame(..., repeat:true)`，**不新增 MonoBehaviour**。显隐由帧回调**轮询** `UnreadStore.Any`（旧写法只在"注入时/登记未读时"写 → 「读完不灭」）。**★ 取宿主必须 `GetComponent<RectTransform>()`**：`transform as RectTransform` 在 IL2CPP 下静默返回 null（见 D.1），角标会永不创建且日志一行不打；该路径上**已取消所有静默 return**（统一 `LogOnce` 去重）。选型示意见 [`mockups/hud-unread-dot-options.png`](../mockups/hud-unread-dot-options.png)（可复现：`scripts/dev/mock_hud_dot.py`）。
* **未读横幅**（`csharp/UI/UnreadBanner.cs`）：HUD 级**独立常驻 Canvas**（挂 `g.root`，非 Canvas 根 → 免嵌套画布被父级 renderMode/scale 吃掉），`sortingOrder=2400`（高于 HUD 20、低于三个面板）、顶部居中 360×46、`TopOffset=-56`、8s 自动隐藏。**纯提示不可点**（用户拍板）：不建 Button、**不挂 GraphicRaycaster** → 完全不参与输入拾取。文案 `「姜萌」传音：唐郎，可还记得我？…`（单行截断 28 字、多条标「共 N 条」、去非 BMP 字符）。懒建于首次未读；宿主随场景销毁后下次 Show 自动重建。
* **候选五源**（`RelationNetwork.CollectKnownUnits`）：`friendUnits`（好友簿）· **`IntimUnitData.enemyUnits`（仇人簿）** · **`GetAllGoodRelationUnitID(true, true)`（关系记录簿兜底，含敌）** · 手动通讯录镜像名 → `UnitLookup.Resolve` · **同格**：一次 O(N) `GetUnits(true)` 按 `pointX/pointY` 相等筛选（陌生人当面可搭话）。按 `unitID` 去重、排除玩家与空名；**非通讯录成员仅同格可发起**。
* **玩家自身必须全程剔除**（曾"自己给自己传音"）：`TryAdd` 的守卫 `ReferenceEquals(u, player)` 在 IL2CPP 下**静默失效**（同一原生对象经不同调用点取到的托管包装器不保证引用同一）。玩家判据统一到 **`UnitSnapshot.IsPlayerUnit`**：① 引用相等 ② **unitID 相等 = 主判据**（世界唯一、与包装器无关）③ 真名兜底（**仅当两侧 unitID 都取不到时用**——本作**存在与玩家同名的 NPC**，名字不能当主判据）。落点：`RelationNetwork.TryAdd` / `NpcInitiativeMonitor.BuildCandidates` 末端统一剔除 + `FireOne` 末道断言 / `DramaAiOption.FindNpcOf`（同一根因第二现场：主角自己的剧情窗被当成 NPC 窗）。

## 2. 主动传音与同格确认窗（已实装）

同格 NPC 主动互动改为**先弹确认窗**（立绘照常 + "XX 希望向你发出互动" + 同意/不同意）；异地（传音）不弹窗、直接发：

```
触发（概率/冷却/状态闸照旧）→ 冷却先落（_cooldownReal/_cooldownDay 紧跟触发）→ 分支：
  异地 → SendInitiative(npc, intent, reason)
  同格 → ShowConfirmSimple(+170, left=玩家, right=NPC, "XX 希望向你发出互动", "同意", "不同意")
          同意 → SendInitiative(..., consented: true) → Python 回合 → npc_reply → 三分流 → 当面弹对话 UI
                 同时置 _pendingOpenUnit/_pendingOpenFrames=3：**3 帧后就把对话 UI 打开**（吃流式）
          不同意 → 仅日志（declined by player），冷却已前置即完成
```

* **同格 = 窗口先开、话后到**：点同意那一瞬就发事件让回合尽早开跑，3 帧后 `ChatLauncher.OpenForUnit` 显形 + `SetThinking(true)`「对方正在斟酌…」→ 首 token 落地时直接吃流式。**同格不再依赖"工具结果"才开窗**。
* **★ 开窗重试（同格实感延迟的真凶）★**：旧版 `TickPendingOpen` 是"3 帧后**只试一次**"，失败就退化成"回复到达时才弹窗"。失败原因恰恰是那把 09-10 铁律闸：`AbPanelProber.CanCreateNow()` 里 `DramaUiName = "UICustomDramaDyn"` **就是我们自己这个确认窗的名字**，判据是 `g.ui.GetUI(...) != null`（**存在**，不看可见）→ 本局第一次同格互动的"确认窗刚关、实例仍在"必然撞上。现改为**3 帧后每帧重试、成功即止、5s 超时仍回落旧路径**（成功日志带"第 N 次尝试成功"）；`AbChatPanel` 那条"当前不宜创建"日志按 1s 节流并打出 `确认窗 UICustomDramaDyn 实例仍存在=…`。**零回归**：3 帧时闸门本就通过的话第 1 次就成功，行为与旧版逐字一致。
* **冷却写点在触发瞬间**（前移到分支之前）：婉拒也算一次自主交互（冷却期现实 600s / 游戏 3 日）。
* `SendInitiative(..., consented=true)` 带 `consented` 字段：Python `handle_initiative` 据此**豁免 300s 全局防轰炸节流**——否则玩家点了同意 NPC 也永不开口。**先同意再生成**，避免"拒绝了但话已说出口"的账本残留。
* 用 `ShowConfirmSimple` 而非 `ShowConfirm`：轻量变体，**无 `DramaGate` 槽位**、回调直接执行；同款立绘窗 `UICustomDramaDyn`（`unitLeft` 空会 NRE → 兜底玩家）；弹窗失败按婉拒处理（走 `onNo`）。
* 实现约束：该类被 verify 工程链接编译 → **不得引用 `ModMain`**（`CS0103`）；`lambda → Il2CppSystem.Action` 必须**三步写法**；自制确认窗继承 `UICustomDramaBase`（非 `UIDramaBase` 子类）→ `DramaAiOption` Postfix **不会**在其上触发；`IsBlockedByGameState` 已检查 `UICustomDramaDyn`。
* **左侧立绘必须是玩家**：五个确认窗里只有同格这一个曾传反。方向依据：① 游戏自身代码即 `unitLeft = g.world.playerUnit`（邀请/婚礼剧情）；② 用户拍板"玩家在左、NPC 在右"。`ShowDramaService.ShowConfirmSimple` 注释已把 **left=屏幕左位=玩家 / right=屏幕右位=对方 NPC** 钉死。
* 已知边界（v1 接受）：确认窗**无编程关闭 API**——玩家晾着不点 = 窗常驻 + 自主互动被状态闸暂停；同意到 `npc_reply` 间的竞争（跑开/进战斗）→ 落未读红点兜底。

**窗口 ID 段**：自主互动当面确认窗取 `ModIds.DramaInitiativeBase = Mod + 170`（+0 窗 / +1 同意 / +2 婉拒 / +3~9 预留）；`ModIds.cs` 分段总表另有 +100 `economy_item`（**`trade` 买卖也复用这一段**） / +110 `item_acquire` / +120 `social_relation` / +130 `movement` / +140 `world_ai_action`（`+140+OffReserve` = `AiActionGate` 完成挂起忙键，不开窗）/ +150 查询信息展示窗 / +160 对话 AI 追问多选窗 / +180~+989 预留。**新增窗必须登记，ID = MID + 偏移**。

## 3. travel / places / 分类背包（已实装）

**`movement` 新增 `travel` op**（支持"先约、再赴约"）：原 `teleport` 只能把 NPC 传到**玩家坐标**，而 `UnitActionMoveNPC` 构造函数实际接受**任意 `Vector2Int`**。

* 入参 `destination`（城镇/宗门名）+ 可选 `region`（同名分宗消歧）；`ResolveBuild(dest, region)` 在 `CollectNamedBuilds()` 上**精确匹配 > 包含匹配**，region 命中者优先；`wub.CreateAction(new UnitActionMoveNPC(new Vector2Int(b.X, b.Y)), true)`，已在当地返回 `already_there: true`。传送是否受限由游戏侧 `IsCreate` 裁决——工具返回 success 但动作可能被拒绝，真机观察。
* 返回字段（**Python 渲染依赖的契约**）：`op/target/destination/cat/region/point{x,y}/moved`（或 `already_there`）；文案在 Python `tools/text_render.py`：`"{target}已动身前往{where}，并在此地等候。"` / `"此刻就在{where}"`。
* 数据源：`MapBuildTown`/`MapBuildSchool` 继承 `MapBuildBase`——`name` + `GetOrigiPoint()` + `gridData.areaBaseID`；**农场/兽栏等玩法建筑不进地点表**。枚举必须用**泛型重载**（见 D.6）。

**`query_world` 新增 `topic=places`**：返回 `{places:[{name, cat, region, point{x,y}}], total}`（`cat` = 城镇/宗门），定位 = travel 目标目录 + 邀约碰面地点查询；`region` 非空按地区过滤。配套 `topic=region` 返回当前世界实际存在的州名（命名建物收集去重，无建物退回静态州表）。Python 渲染在 `tools/text_render.py::_qw_places`；Stub 侧 `bridge.py` 的 `_DEFAULT_PLACES` 与 travel 共用同一份口径。

**背包按价值筛选（分类版）**：`inventory.props` 按 `PropsType` 分组 `[{cat, items:[{name,count,worth,total}], misc}]`（`0Money/1Pill/2Martial/3Equip/4Material/5Other` → 丹符/书籍/装备/材料/其他），类内按**单价 `worth` 降序**——`ConfItemPropsItem.worth` = **悬停出售价**。**总种类 ≤12 全显**，大背包每类 top-`inventory_top`（默认 3）+ 杂项聚合 `misc{kinds,pieces,worth}`；`inventory_all=true` 为全量逃生口；`type=0`（灵石）**不进 `props`**（走 `money`）；`equips` 逐件带 `worth`；**执行层（赠送/偷窃）按名扫全量，不受筛选影响**。

## 4. 自主交互系统怎么测

**先记清触发链的 10 道闸**（"没触发"必是其中之一，按序排查）：

| # | 闸 | 位置 | 键 / 条件 | 测试放行值 |
|---|---|---|---|---|
| 1 | 日节拍：每游戏日一次（`WorldAddDay` + `Frame` 兜底）+ `_lastDay` 防同日重复 | C# | — | 推进游戏日 |
| 2 | 状态闸：战斗 / 死亡 / 确认窗（`DramaGate.HasPending`、`UICustomDramaDyn`、`UIBattleInfo`） | C# | — | 非战斗、无模态窗 |
| 3 | 全局日骰 | C# | `initiative.daily_chance` | **100**（`<100` 才掷骰） |
| 4 | 候选集与门槛：关系网全集 + 通讯录 + 同格；**非通讯录成员必须同格** | C# | — | 存档关系网里得有人 |
| 5 | 单人双冷却 `max(现实秒, 游戏日)` | C# | `npc_cooldown_real_s` / `npc_cooldown_days` | **0 / 0** |
| 6 | 低好感减半 | C# | `low_intim_halve` + `low_intim_threshold` | **false** |
| 7 | 一日**只触发一人**（成功即 `return`） | C# | — | 要更多只能多推日 |
| 8 | 同格先弹「同意/不同意」确认窗（婉拒也算一次，冷却已前置） | C# | — | 站远 or 点同意 |
| 9 | Python 全局熔断 `min_interval`（默认 300s） | Python | `initiative.min_interval` | 20（当节流阀用） |
| 10 | Python 忙守卫：该 NPC 有回合在跑 → 丢弃 | Python | — | — |

**路径 A：诊断强制开关（推荐，改判定逻辑也不用重启）**
`F:\agent_loop\_diag_initiative_force.txt` 存在即进入强制模式（`DiagSwitches` 每 120 帧复查 → 增删文件即时生效）。它**只放宽闸 1/3/5/7 的"时间"部分**，闸 2/4/6/8 与 Python 侧闸 9/10 照旧——**测的仍是真系统**。

```
interval=20      # 触发节拍（现实秒，默认 20，最小 1）
name=林婉清      # 只对该 NPC 触发（默认：候选集里好感最高者）
pick=random      # 选人策略：random=候选里随机（不写=好感最高者，可复现）；与 name= 同写时 name 优先
intent=malice    # 指定意图键（默认按亲密度真实派发；非法值回落真实派发）
repeat=1         # 连发模式（默认**一次即停**；写 1 恢复"每 interval 一次"的旧行为）
force_remote=1   # 【诊断】本次强制触发按**异地**判定：不弹确认窗 → 直发 → 收尾必落分流③
no_tools=1       # 【诊断】本回合**一个工具都不调**：帧带 speech_only=true → Python 追加调试约束
                 #   + 执行层拦下**全部**工具（连只读也拦，busy 只拦动作）→ 纯传音形态，回复更快
# 以 # 开头为注释；文件内容每 ~2s 重读，改了即时生效
```

观察横幅/红点的标准姿势：`force_remote=1 + no_tools=1 + name=<某联系人> + intent=smalltalk`，**测试期间不要开该 NPC 的对话窗**（窗开着走分流① 直播已读 → 既不亮红点也不弹横幅）。预期日志链：`[DiagSwitches] … forceRemote=True … noTools=True` → `forced trigger … sameGrid=False …` → `（NPC 主动传音）npc_reply` → `[ContactDuty] 主动开口分流：③未读登记 + 红点/横幅` → `[UnreadBanner] 常驻宿主已建`。

**★ 一次即停（默认，用户要求）★**：只要收口过一次就停表——同格点「同意」/「不同意」、异地（或玩家忙）直发传音，三者任一发生后打 `本次已收口 → 停表`。**重新武装**：把开关文件**改一下并保存**（mtime 变化即视为"再来一次"）；要连续跑就写 `repeat=1`。停表只影响强制模式，**正常日节拍/概率/冷却完全不受影响**。
要点：强制模式**不写冷却表**（删文件后不留痕）；事件帧带 `debug:true` → Python 豁免 `min_interval`；默认选人**不是随机的**（候选按好感降序取第一人，可复现），要随机才写 `pick=random`。

**「触发了但没反应」的三种正常解释**（都会打日志，别误判成链路坏）：
① `forced trigger 跳过：玩家处于战斗/死亡/确认窗`（闸 2）；② `forced trigger 跳过：候选集里没有 name=…`（闸 4，非通讯录成员必须同格）；③ Python 侧 `诊断强制触发被丢弃：该 NPC 有回合在跑`（闸 10，主动开口不排队、错过就丢）。

**路径 B：配置提高概率（保留真实概率链路）** —— 面板里那 6 行**只对开发者可见**：`config.json` 手改 `"ui": {"show_advanced": true}` 后重开配置面板才显形（`get_config` 每次读文件、无缓存，不必重启）。测试预设 `min_interval=20 / daily_chance=100 / npc_cooldown_days=0 / npc_cooldown_real_s=0 / low_intim_threshold=0 / low_intim_halve=false`，**重启 Python 生效**（约 2s，不需要重启游戏）。重连时 `ModMain.OnWsReconnected → RefreshInitiativeFromConfig` 重拉参数，**"配置到底有没有到 C# 监测器"看这一行**：`[NpcInitiativeMonitor] config: dailyChance=100 cooldownDays=0 realSec=0 lowThresh=0 lowHalve=False`。
⚠️ `min_interval` 别设 0：一次闭关/过月可跳 30 日 → 瞬间涌进最多 30 个回合（每次 = 一次真实 LLM 调用）。**测完必须还原默认**。

**路径 C：只测 Python 半边（不起游戏）** —— `ws_channel` 是**单连接语义**，所以**别在游戏运行时**用 `scripts/chat_cli.py --ws` 连 8766（会和 C# 互相踢）。起独立实例（不同端口 + 临时 `storage_root`）再 `/init <npc> <intent>`；`tests/test_initiative.py` 已覆盖 `handle_initiative` 的忙守卫/节流/consented/debug 豁免等分支。

### 4.1 两级闸（用户拍板：闸门管「能不能打扰」，不管「能不能说话」）

| 闸 | 判据 | 命中后果 |
|---|---|---|
| **能说闸** `SpeechBlockReason()` | 未进世界（`playerUnit=null`）、死亡/复活窗（`UIMapDie` 可见） | 本次触发**丢弃**（不排队，下一拍/次日重试） |
| **能做动作闸** `BusyReason()` | 战斗中（`battle.isBattle`）、`DramaGate.HasPending`、我们的确认窗可见、战斗 UI 可见、`Mask`/`Window` 层有可见 UI、`UIChatAi` 可见、`UI(0)` 层顶名含 `Drama` | **不禁言语**：事件帧带 `busy:true` → **尾部追加 `BUSY_NOTE` + 执行层拦下 6 个动作工具**（`ACTION_TOOLS`；工具表本身恒定不动，见 docs/features/brain-liveness.md §4 前缀缓存）；只读三件套照常可用；同格**不弹确认窗**，降级直发传音（未读红点 + 横幅） |

| 玩家状态 | 同格 NPC | 异地通讯录 NPC |
|---|---|---|
| **空闲** | 说话 ✓ 动作 ✓（先弹「同意/不同意」，同意后生成） | 说话 ✓ 动作 ✓（不弹窗：未读红点 + 横幅） |
| **忙碌**（看界面/战斗中/有模态） | 说话 ✓（降级直发）**动作 ✗** | 说话 ✓ **动作 ✗** |
| 未进世界 / 死亡窗 | 丢弃 | 丢弃 |

* 状态闸的历史坑（**已修，勿回退**）：`g.world.battle != null` 判空**恒真**（其类型是 `WorldBattleMgr` 战斗**管理器**，世界常驻）→ 真判据是它自己的字段 `Boolean isBattle`；`data.isDead`/`isInBattle` 是**死代码**（14628 类型全量扫描 hits=0）；`GetUI(...)` 判空**存在≠可见** → 改 `UiVisible()`（`activeInHierarchy + activeSelf + Canvas.enabled`）；玩家死亡 → 判死亡窗 `UIMapDie` 是否可见（fail-open）。
* **模态/窗口层按游戏自己的分层**：`UILayer` 八层（具名成员 `UI/UITop/Guide/Loading/Window/Mask/FullEffect/TempUI`；⚠ **数值顺序不是 0..7**，越界即抛）。判 **`Mask`（全屏输入遮罩）与 `Window`（窗口层）任一有可见层顶 UI** = 玩家被界面占着 → 不打扰。**绝不判 `UITop`**（HUD/地图常驻，判了就永远拦）；**`UI(0)` 层不按层判、按界面名点名判**：`UIChatAi`（我方对话窗）与层顶名含 `Drama` 的剧情窗 = 忙，**其余面板（NPCInfo/人物/任务/先天气运/结算/MinMap）一律放行**。
  依据：① 反编 `UIMgr.GetLayerTopUI(UILayer,int)` 是游戏自己的「层顶是谁」接口；② F12 快照实测（用户开的 8 个页面全在 `UI(0)`，`Window` 层全程为空，`Mask` 只在剧情窗出现 `MaskNotClick`）。
* 要临时绕过状态闸测链路：开关文件里写 `ignore_state=1`（热生效，放行时仍会打命中原因）。
* **Python 侧落地（`busy` 变体）**：`ws_channel.handle_initiative(busy=)` → ① **在本回合尾部追加一条 plugin 消息**（`_run_turn(post_note=)` → `_append_plugin_note`，形状同 `_inject_acquaintance_if_new`：`surfaceOp=append` 进 Surface 模型可见、`source.kind=plugin` 被 `project_ui_history` 滤除 UI 不可见、不进筐不触发新回合），内容 = `ChatHub.BUSY_NOTE`；② 执行层兜底 `DialogueAgent.speech_only_turn`（历史残留/幻觉硬调动作 → 回 canonical 拒绝结果，落账形状与正常路径**逐字同构**）。只读/动作分组在 `tools/schemas.py`：`READONLY_TOOLS`（inspect_unit / search_units / query_world）与 `ACTION_TOOLS`（其余 5 个）。
* **为什么是"追加消息"而不是"删工具"（曾实现后撤回）**：① 与自主交互本质一致 —— 它本就是"模拟用户发一条消息"；② **工具表恒定**：`tools` 在 LLM 请求的**前缀**里（`llm/openai_client.py: tools=oai_tools`），忙碌回合删、下回合恢复会让 `request/header` 的 `header_hash` 每回合变化 → **上游 prefix cache 失效**（上下文常达 ~25k tokens，代价可观）；③ 账本可回放"当时为什么只说话"。**代价**：约束是软性的，故保留执行层兜底作为硬防线。
* **排队语义**：**玩家消息排队** —— 进 `inbox` 的 `next-turn` 筐，每回合领一条（`next-step` 筐全量插队先吃）；**NPC 主动开口不排队** —— 该 NPC 有回合在跑即丢弃；全局并发 `_turn_slots`(2) 只让**不同** NPC 等空位（限流等待，不是队列）。

### 4.2 ~~F12 = UI / 闸门快照~~（**2026-09-15 已退役并删除**）

> **本节描述的工具已不存在**：F12/F1 热键在 09-14「删除全部 F 键」时就被摘掉了，但
> `csharp/UI/UiGateDump.cs`（418 行）、它的探针口 `NpcInitiativeMonitor.GateReport()/GateProbes()`
> （89 行）当时**没跟着删**，成了无入口的死代码，09-15 探针清理时一并删除。
> 下面保留的是**结论**（判据口径仍有效，见 §G.5.1）与那条 `UILayer` 的坑。

原工具：打开任意页面 → 按 F12（别名 F1）→ 整份记进 `Player.log`，每行带 `[UiGateDump]` 前缀。三段输出：
① 判定结论（`能说闸=…｜能做动作闸=…｜IsPlayerBusy=…` + 逐条探针）
② `UILayer 0~7` 逐层清单（每层标参不参与判定 + 该层每个已打开 UI 的名字/类名/可见性/尺寸/order）
③ 可见 UI 结构树（inactive 只报一行不下钻，900 行封顶）。

**排查结论（这才是要留的东西）**：只有 `Mask(5)`/`Window(4)` 的**层顶可见**算"玩家忙"；
`UI(0)`（我们三个 AB 面板所在层）与 `UITop(1)`（HUD 常驻）永不判；
`UI(0)` 层内**按界面名点名判**（`UIChatAi` 可见 / 层顶名含 `Drama` → 忙），其余 `UI(0)` 面板一律放行。

> **⚠ 遍历必须用具名成员，不能用 `(UILayer)i` 数值转换**：`UILayer` 的**真实数值不是 0..7 顺序**（层数组按枚举成员数定长，越界即抛 `IndexOutOfRangeException`）。同一根因也解释了历史日志里所有 `UITop=(err) … TempUI=(err)`；闸门本体一直用具名成员所以从未受影响。

### 4.3 「主动开口」回合喂给模型的完整提示词

一键复现：`python3 scripts/dev/dump_initiative_prompt.py [npc] [intent] [缘由]`（真实 SystemPrompt/prompts 目录 + Stub 桥，不连游戏、不调真 LLM）。**自主交互没有独立提示词模板**——它是"被动对话全套提示词 + 一条伪 user 舞台指令"，实际请求 = ① `role:system`（`SystemPrompt.assemble`）② 账本投影 messages ③ 恒定 9 工具：

| 层 | 内容 | 来源 |
|---|---|---|
| system · 消息读法 | `[消息读法]`：日期戳/舞台提示/图片占位该怎么读 | `prompts/sections/harness_identity.txt` |
| system · 世界设定 | `world_basis.txt` 6 段（世界背景设定/境界体系/大地舆图/研修与技艺/势力与正邪/**边界情况**）+ `world_persona_rules.txt` 8 段（资质与品评/先天气运/性格与处世/体貌与魅力/名声与道行/灵体与道途/种族与他族/天骄），共 14 段 | `prompts/sections/*.txt` |
| system · 人设 | `你是郦安，是一个好人` + `[输出底线] …禁止透露你是 AI、模型或程序 …` | `prompts/personas/<npc>.txt` + `_suffix.txt` |
| system · 工具规则 | `[工具使用规则]` **~870 字**：场景→工具路由表 + 结果解读 / 语气 / 错误处理 —— 参数名·枚举·取值范围**不在此处复述**，归 `tools/schemas.py`（见 §G.5.4） | `prompts/sections/tool_usage.txt` |
| messages · L1 四段 | `Current runtime context —— 当前时间 / 自身 / 玩家 / 近况`（逐段差分注入；**同格/异地措辞就在「玩家」段**） | `system_prompt.format_l1_context` |
| messages · 首见注记 | `（系统：这是你们之间的第一段传音，此前你们并无对话往来。）` | `ChatHub.ACQUAINTANCE_NOTE`（`plugin` 源，UI 不显示） |
| messages · **伪 user 意图** | `（NPC主动传音：向玩家表达思念）（缘由：…）` | `initiative.format_initiative_message`（`initiative` 源） |
| messages · 忙碌约束（仅 busy） | `（系统：玩家此刻正忙——…**不要发起任何行动**…）` | `ChatHub.BUSY_NOTE`（尾部追加，`plugin` 源） |

**UI 不会看到那条"模拟用户消息"**：伪 user 意图是 `user/message + source.kind="initiative"`，`history.project_ui_history` **不取正文**，只投标记项（`intent`/`intent_text`/`reason`），C# 渲染成一行分隔条 `—— 主动传音 · 表达思念 ——`；`kind="plugin"` 的 L1/忙碌约束整条跳过。**模型看得见、UI 不存在。**

**意图键（intent key）** 是 **C# 触发期决定的"派发意图"**（不是模型生成、不是玩家输入），C# 只发英文键 + 缘由，Python 映射成中文舞台指令：

```csharp
private static string PickIntent(int intim) {                     // NpcInitiativeMonitor.cs
    var rnd = UnityEngine.Random.value;
    if (intim < 0)   return RandomOf(NegativeIntents);                      // 4 负向：malice/vent/provocation/disdain
    if (intim >= 120 && rnd < 0.4f) return RandomOf(PositiveCloseIntents);  // missing/affection（40%）
    return RandomOf(PositiveIntents);                                       // 5 正向：greet/smalltalk/courteous/life/recent
}
private static string BuildReason(int intim) {                    // 缘由＝好感阈值阶梯给的"开口抓手"
    if (intim >= 120) return "念及与你多日情谊，特来以神识传音寻你";
    if (intim >= 60)  return "想起你，想与你聊上几句";
    if (intim < 0)    return "心中不忿，寻你理论";
    return "";
}
```

即：**先按好感选"情绪档"，再在档内随机抽一条**。选人同理：候选按好感降序（日节拍同好感随机打散、诊断强制不打散），成功即 `return`（一日只一人）。**英文键不上线**：`llm_adapter.canonical_to_openai` 只保留 `role/content/tool_calls`——模型看到的只有中文舞台指令；键只落账本（审计）与 UI 分隔条短标签。**意图的语义是"舞台指令"不是"命令"**：它给开场定方向 + 一个事实抓手，具体措辞仍由人设决定。

## 5. 工具文档的两层分工：契约 vs 决策（勿回退）

**问题**：`tool_usage.txt` 与 `schemas.py` 曾把同一份契约各写一遍。system 段 4,875 字里 `tool:usage` 占 **2,703（55%）**，套上 `tools` JSON 9,299 字，**每次请求固定 14,174 字 ≈ 5,000 token**；而真机 `in=` 只有 8,600~11,800 ⇒ **42%~58% 的输入是工具文档**，且上游 25/25 次 `cache_hit=-`（不能指望前缀缓存兜底）。

**分工判据**（判一条内容该住哪）：

> 删掉后模型「**不知道该传什么**」→ 归 `tools/schemas.py`
> 删掉后模型「**不知道该干什么**」→ 归 `prompts/sections/tool_usage.txt`

- **schemas = 契约**：参数名、枚举取值、类型范围、`required`、示例值、bounds。模型**填参那一刻视线所在**的地方，且是机器可校验的。
- **tool_usage = 决策**：跨工具路由、先做什么后做什么、结果怎么解读、语气约束、错误处理。这些 schema 表达不了。

**结果**：`tool_usage` 2,704 → **748 字（−72%）**；`schemas` 9,299 → **7,987 字（−14%）**；合计 **−27%**（≈5,006 → 3,392 token）。

**09-21 反向修正（勿再删）**：压到 748 字之后发现「何时必须调」被一起挤没了 —— 同一模型关掉思考，39 个场景只有 12 个真发生调用，标为「该调」的回合 **8/12 一次都没调**（量法见 `scripts/dev/prompt_lab`）。于是：

- `tool_usage` 改成**场景→工具路由表**（宗门/大势/排行/地点 → `query_world`；报人名问认不认得 → 先 `search_units`；财物数目 → `inspect_unit`；送/要/买卖/关系/碰面各自点名），并加一条「只在台词里应承等于什么都没做」。水位 748 → **869 字**。
- `schemas` 给每件工具补一条**「玩家这样说就必须调我」的触发句**（散文 4,183 → **4,381**），同时删掉三处重复的「先 inspect_unit 查背包」。

动机与实测数字记在 `tests/test_tool_usage_contract.py` 的预算注释里。

**★ schemas 的精简地板（别白费力气）**：序列化后 **~4,665 字是 JSON 结构本体**（`"type"`/`"properties"`/`"required"`/`"additionalProperties"`）＋**枚举字面量**（`social_relation.op` 13 个值、`search_units.filters` 7 个子键…）——删了就是**改功能**不是瘦身。真正可动的散文只有 ~3.7k，压到 3.3k 已是"每个字都在讲契约"。**想再省只能砍功能。**

**★ 刻意保留重复的四条（不是漏删）**：`认 unit_id 不认名` · `陌生 ≠ 认识` · `面对面类异地不可行` · `error 处理`。它们是**行为规则不是契约**，schema 里没有对应位置；在 system 段再喊一遍换来的是正确率。**纯去重会掉正确率**——这是用户拍板的取舍。

**防回涨护栏** `tests/test_tool_usage_contract.py`（7 条，9 个变异全部验证过）：
- 双向覆盖：`TOOL_ORDER` 每个工具必须被 tool_usage 提到；tool_usage 里的 snake_case 不许是野名字
- **长度预算 900 字**（现 869）：超了不是"写得细"，是契约又被抄回来了
- **禁复述**：不许出现 `xxx=yyy` 式参数赋值签名；历史上抄过的 8 个片段不许回来
- **承重墙清单**：上面那四条政策每条一个断言（防精简删过头）
- 委派句：必须写明「参数以工具定义为准，此处不重复」——否则模型可能**自己编一个参数**
- schema 散文冒烟上限（宽松报警）· `_ALL_MAP` 无孤儿 · `READONLY_TOOLS` 必须是 `TOOL_ORDER` 前三位

> **变异验证教训**：最初写的 `[t["name"] for t in ALL_TOOL_SCHEMAS] == TOOL_ORDER` 是**恒真式**（前者由后者推导），打乱顺序照样通过。真正会静默出事的是「查询前置」——`READONLY_TOOLS` 必须是 `TOOL_ORDER[:3]`，否则玩家忙碌时会静默放行动作工具。**写护栏测试时先变异一次，恒真式的护栏比没有护栏更危险。**



**现象**：换存档打开通讯录，里面还有上一个存档加的好友。根因是**存储没有存档维度**——三层里只有关系层是对的：

| 层 | 来源 | 修前 | 修后 |
|---|---|---|---|
| 关系层 | 活世界 `RelationNetwork` | ✅ 随存档 | 不变 |
| 手动层 | `contacts.json` | ❌ 全局 `storage_root` | `storage_root/worlds/<world_id>/contacts.json` |
| 「最近」层 | 扫 `*.jsonl` mtime | ❌ 全局 | 只扫当前存档目录 |
| 对话历史本体 | `<npc>.jsonl` | ❌ 全局（换存档后同名 NPC **串号**，比通讯录更严重） | `storage_root/worlds/<world_id>/<npc>.jsonl` |

实现：`world_id` = **玩家 unitID**（随存档固化、世界唯一），由 C# 在 `load_happened`（`EGameType.IntoWorld`）与 `save_happened`（`EGameType.SaveData`）帧里携带（`ModMain.FillWorldIdentity`，另附 `player_name` 作人类可读标签——**本作存在与玩家同名的 NPC，名字不可当键**）。Python `persistence.world_root(base, world_id)` 统一算目录，`AgentLoop.set_world()` 与 `ContactsService.set_world()` 同时切（`ws_channel._apply_world`，**先切命名空间再 `discard_all`**，顺序反了会把旧存档增量写进新目录）；切档同时清会话索引缓存，C# 侧 `OnIntoWorldEvent` 立刻重拉 `list_contacts`/`list_sessions`（`ContactStore` 是静态镜像，不重拉会把旧存档好友继续喂给候选集与 NPC 面板按钮）。缺 `world_id`（旧 C#/手工 RPC）→ 沿用现状并 WARNING，**不猜**。未定存档（测试 / `chat_cli` / 尚未进世界）→ 退回扁平 `storage_root` 布局（后向兼容）。

★ **09-18 补齐**：上面这条链原先还差一半 —— 身份**只在 save/load 两条事件里下发**，而它只活在 Python 内存里，于是 Python 一重启（配置面板保存 / BrainLink 拉起 / 崩溃重生）身份就没了，那段窗口的读写会落扁平目录（开窗看不见本档历史，下一次存档还会把短账本**整份覆盖**进真存档）。现在 C# **每次连接**都补发 `world_hello`（只切命名空间、不丢增量；换档才丢弃）+ 断线期间的 save/load 排队补投，Python 侧另有账本**出处对账**与生产形态未定档闸门（`require_world`）。完整来龙去脉见 **2026-09-18-存档身份丢失.md**。

---
