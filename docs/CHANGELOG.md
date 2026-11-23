# 开发日志

> **追加写**，最新的在最上面。每次开发做完追加一条。
>
> 这里只记"**做了什么、为什么**"。详细过程在 [`journal/`](journal/)（进行中）
> 与 [`archive/`](archive/)（已归档）。
>
> 格式：日期 + 一句话标题 + 要点 + 指向 journal/features 的链接。

---

## 2026-11-23 · 原生剧情窗第二句两侧立绘/名签变成同一人

**症状**：同一段剧情翻到第二页（23205 → 23206，配表两侧都是「运行时取人」），左右两个立绘位画成同一个人，
名签也同名。第一页正常。装本模组才出现。

**前两次修复都没压住**（记录在案，别回退成它们）：

- 在 `InitData` 之前把 `DramaData.unitLeftID/unitRightID` 归零 —— 日志证明那时它们本来就是 0，脏值是在
  `InitData` 内部写进去的（克隆出来的「对话」按钮带着预制件组件被 `SetActive(true)`，是头号嫌疑，未逐一定位）。
- 结果级纠偏「右名签 ≠ unitRight 名 → 归零 ID + `UpdateUI()`」—— 命中了但画面没有任何变化，说明
  `UpdateUI` 重走的是同一条被写脏的解析。

**定案与修法**：`UpdateUnit(..., int id, bool isLeft)` 的 `id` 决定画谁（1=玩家、0=运行时按侧取、11=unitLeft、
12=unitRight，见 `docs/archive/2026-10-19-队友重伤与死亡链路.md` §11）。新增第二个 Harmony Prefix
`TeamContainerInject.DramaUpdateUnitSlotPrefix`：只在这侧 `id==0` 时按侧别改写成 11/12，两侧单位为空或本来
是同一人（单人页、独白页、190）不动；侧别用实参名签 `Text` 与 `textLeftName/textRightName` 比指针判定，
不依赖游戏传的 `isLeft`。与 190 那条前缀**分开挂载**，参数绑定失败不会牵连 190。

**真机验证（14:59）**：启动行出现 `0/0 立绘位=已挂(Prefix)`；23205 与 23206 各出现左 `id 0→11` / 右 `id 0→12`；
两页的说话人判定都是 `名签 左="濮阳冷亦" 右="计天明"`，旧的 `立绘位纠偏` 不再出现。

详见 [`journal/2026-11-23-剧情立绘位错人/`](journal/2026-11-23-剧情立绘位错人/README.md)。

---

## 2026-11-23 · 断连判死从未生效 + 压缩冷却单位写错

**断连判死**：`transport/ws_channel.py` 的断连出口把 `self._drop_pending_on_disconnect()`
写成了裸调用。它在 `finally` 里，于是每次 C# 断连都抛 `NameError`：挂起的 RPC 不会被判死
（11-20 的修复从未生效，每条空转 90 秒），下一行「C# 连接已断开」日志也被一起跳过 ——
现场看不到断连。`tests/test_disconnect_contract.py` 只 grep 方法名，10 条全绿却拦不住；
现改为断言接收者（`self.`），并新增一条真跑清理的行为用例（future 必须变
`ConnectionError` 且从表里摘掉）。变异验证：改回裸调用 → 契约红。

**压缩冷却**：`compaction.cool_down` 默认值 `20000.0` 被当秒用（`compress.py` 是
`monotonic() + cool_down`），≈5.5 小时，等于一次会话只自动压一次。按毫秒误写处理，
`config_loader` / `compress` / `server` 三处默认值统一改为 `20.0` 秒。
`tests/test_config_hot.py` 原来那条「cool_down 不能在重建时丢失」因默认值同值而空转
（临时 config 里根本没写这个键），已改成真从配置文件读回。

**顺手补**：`ws_channel.py` 的 `List`、`tools/unit_refs.py` 的 `Set` 补上 typing 导入
（此前靠 `from __future__ import annotations` 压着）。

**未验证**：没有正在运行的 C# 端，断连行为未实机复现；压缩冷却只验到单测与配置读取。
详见 [`archive/journal/2026-11-23-断连清理与压缩冷却修正/`](archive/journal/2026-11-23-断连清理与压缩冷却修正/README.md)。

---

## 2026-11-23 · 兼容严格 schema 网关（`tools: null` 导致 400）

**问题**：接某家严格 schema 的 OpenAI 兼容网关（`https://<gateway>/provider/v1`）时，NPC 只回兜底台词，
日志里一行 `400 - {'error': {'message': 'Invalid input: expected array, received null',
'param': 'tools'}}`。玩家先怀疑是缺 HTTP 头，试过 `x-cli-environment` / `x-cmd-zdr` /
`x-command-code-version` / `x-project-slug` / `x-taste-learning` 全部无效 —— 因为根本不是头的问题。

**定案**：OpenAI SDK 对 `tools=None` 的处理是**序列化成 `"tools": null`**（不是省略键），
严格 schema 网关直接 400（`tool_choice: null` 同样被拒）。而"本轮没有工具"是正常情形
（探针 `tools=[]`、纯聊天回合、压缩摘要都走这条），旧行为在这些网关上必然失败。
`llm/openai_client.py` 新增 `_tool_kwargs()`：空工具列表返回 `{}`（两个键整对省略），
非空返回 `{"tools": [...], "tool_choice": "auto"}`；`_call` / `_open_stream`
（含 `stream_options` 的 `TypeError` 回退分支）/ `chat_completions_create` 共 5 处改用它。

**范围**：只动 `llm/openai_client.py`。另外三条协议早就对：`anthropic_client.py` 与
`responses_client.py` 的 `_body` 都是 `if wire_tools: body["tools"]`，空列表不进请求体。
配置侧同一批"看起来像配错"的坑一并写进 features：`base_url` 填到 `/v1` 那一层
（再深会变成 `.../chat/completions/chat/completions` → 404）、模型名必须用 API id
（面板显示名 → 400 `unsupported_model`）、`403 error code: 1010` 是缺 UA 不是鉴权失败。

**验证**：本地假网关修前 3/3 请求带 null、修后 0/3；真网关 10 项字段矩阵全部符合预期
（`tools:null` 400 / `tools:[]` 200 / 不写 200 / 真 tools 200 且 `tool_calls` 正常）；
端到端用本仓客户端打真网关，探针 `ok=True`、流式正文与 `tool_calls` 均正常、非流式压缩路径正常；
新增 `tests/test_llm_no_null_fields_contract.py` 5 条，变异验证
（`_tool_kwargs` 改回发 null）4 红 1 绿；真机跑 `scripts/server.py` + WS 客户端，
`<model-id>` 一轮拿到正文「在的。传音一道我向来不多用，道友寻我，可是有事？」，日志无 400。
**未验证**：游戏内（C# 侧拉起 + 配置面板「测试连接」）未实机跑；
全量 `pytest tests/ -q` 是 `8 failed / 2366 passed / 17 errors`，其中 6 条 failed 与 17 条 errors
在 `git stash` 掉本次改动后**同样全红**（仓库既有红，含 `.depmap.json` 快照缺失），
故本次判据用"新增 0 红"，未为凑全绿去动无关文件。
详见 [`features/llm-gateway-compat.md`](features/llm-gateway-compat.md) 与
[`archive/journal/2026-11-23-兼容严格schema网关/`](archive/journal/2026-11-23-兼容严格schema网关/README.md)。

---

## 2026-11-22 · 解除义父母关系（`jie_chu_ren_yi_fu_mu`）

**问题**：`social_relation` 能 `ren_yi_fu_mu` 认义父母，却没有对应的解除 op ——
一旦认下，工具层无法撤销；而其余 5 类关系都能解。

**定案**：沿现有解除路径补一个 op：原生 `UnitActionRoleBreakWith` 先试，读回仍在则
字段直写兜底。义父母/义子女在引擎里是**互逆的两种容器**
（`parentBack` = 我的义父母、`childrenBack` = 我的义子女），所以兜底同时清
`npc.parentBack` / `target.childrenBack` 及反向组合，读回也查两侧 ——
只清一侧会留下"对方仍把 npc 当义亲"的半解除，且不报错。

**范围**：对象可以是任意 NPC（`target` 真名），NPC↔NPC 与原来一样走免窗 `DirectBreak`；
对玩家走确认窗。群聊里解除成功仍补一条公开叙述（`REL_BREAK_OPS`）。

**验证**：定向 pytest 190 passed；变异验证（把 `tr.childrenBack.Remove` 改成
`tr.parentBack.Remove`）新契约测试立刻变红，已还原；`dotnet build -c Release` 0 错误。
已部署：PyInstaller 自测档 exe md5 `7add5d46…`，DLL md5 `AE1E3390…`（三处一致）。
**未验证**：真机；原生 BreakWith 是否覆盖 `ParentBack`（代码不依赖它，兜底会接管）。
详见 [`archive/journal/2026-11-22-解除义父母关系/`](archive/journal/2026-11-22-解除义父母关系/README.md)。

---

## 2026-11-22 · 用户数据根外置（v1.2.3 公告）

**问题**：创意工坊整包更新会同步 `ModAssets/`，用户填的 API Key、改过的提示词、自建人设
每次更新都被盖回去；窗口位置、公告水位、剧情账本也可能被清。

**定案**：数据根移到 `%USERPROFILE%\.agent_loop`。优先级
`AGENT_LOOP_DATA` > 用户目录 > 旧 `ModAssets` > Mod 根（源码树 / 手工布局）。
首次启动迁移 **只补缺失、绝不覆盖**：`config.json` / `prompts/` / `window_layout.json` /
`notice_seen.json` / `drama_ledger.jsonl`；`prompts/` 逐文件补缺，作者新增的内置人设能进来，
用户改过的同名文件不动。

**验证**：C# `UserDataMigrationCoreTests` 15 项全过；Python 契约 8 条全过；变异验证
（删「目标已存在」早退 + 把 `File.Copy(..., false)` 改成 `true`）→ C# 3 项红、Python 1 条红。
全量 pytest **2391 绿**、C# **34 个工程全过**、`run_tests_for.py --probe --force` 已重采快照。

**顺带**：修了三处只在 WSL 成立的测试路径假设（`test_big_portrait_contract` /
`test_team_multi_probe_contract` / `test_run_tests_for_contract`），Windows 侧全量不再假红。

详见 [`features/user-data-root.md`](features/user-data-root.md)。

---

## 2026-11-21 · 对话窗拖动 + 历史分页（v1.2.2 公告）

两件用户可见的事：窗口能拖了、开窗不再一次拉全部历史。中间踩了四次"一次没做完整"。

**① 对话窗拖动（位置跨局记住）**

用户 11-21 要"能拖拽、甚至缩放"。定案分两批：A = 拖动（本次），B = 缩放（未做）。

判据拆到 `DragCore`（纯算术）+ `WindowLayoutCore`（窗口切在哪）+ `WindowLayoutStore`（落盘），
都能脱离 Unity 单测。位置存 `ModAssets/window_layout.json`，**不写 config.json**
（那个归 Python 独占解释，两边同写必然互相覆盖）。

三个实机 bug：
- **拖不起来**：`RectangleContainsScreenPoint(w, mp, null)` 的 null 相机只对 Overlay 成立，
  而本面板根 Canvas 是 `ScreenSpaceCamera` ⇒ 恒不命中。同一个坑 `HoverTip`/`DramaAiOption` 各踩过一次。
- **只能往左拖**：钳制用错了锚点公式。AB 版 `BG` 是**居中点锚**，拿右中锚公式钳
  1920 视口算出 `x ∈ [-720, 0]`（正确 `±360`）。现在按锚点分派。
- **一次拖动写了 4 次盘**：说明"松手"在一次连续拖动里被识别了 4 次。改成**关窗才落盘**。

**② 拖动卡（28fps）→ BG 挂独立子 Canvas**

`TickProfiler` 显示我们所有回调 < 1ms ⇒ 卡在渲染层。根因：**UGUI 的重算单位是 Canvas 不是元素**，
改 `BG.anchoredPosition` 会重算整棵子树（N 行消息 + 8 颗按钮 + 侧边栏）。
挂子 Canvas 后拖动只更新变换矩阵。另加写位置降频（`DragStepEpsilon=2`，松手补最后一笔）。

> **这里我犯了一个错，值得单记**：第一版按"父级 raycaster 已覆盖子 Canvas、再加会双倍拾取"
> **不加** `GraphicRaycaster` ⇒ **整个窗的按钮、输入框全点不动**。
> 真实机制：`GraphicRegistry` 按**最近的 Canvas 祖先**分表，raycaster 只查自己那张表 ⇒
> 挂子 Canvas = 新开一张表，不加就是**零拾取**（不是双倍）。
> 更糟的是我把这个错误理解**写进了契约测试**：`assert "GraphicRaycaster" not in code`。
> **错误的设计 + 忠于错误设计的测试 = 全绿**，变异验证也"通过"了（变异方向也是错的）。
>
> 教训：**测试只能验证"代码符合我的意图"，不能验证"我的意图是对的"。**
> 对策是回到运行时事实（Player.log 的 `Canvas mode=... order=...`），不是在源码里找自洽。

同轮还纠正了 `overrideSorting`：第一版写 `true`，理由是"保证先后关系"——
也是推的。日志实测：游戏 UI 层 `ScreenSpaceCamera order=0`、实例根 `WorldSpace order=20`，
**两套渲染模式**。开 `overrideSorting` 会让子 Canvas 变成分离排序条目、脱离父级上下文。
改成保持默认（继承）。对照 `UnreadBanner` 用 `overrideSorting=true` 是**对的** ——
但那是**自建根 Canvas**。同样是 Canvas，根和子结论相反，不能照抄。

**③ 历史分页：从"本地切片"改到"服务端游标"**

第一版做了本地切片（`open_chat` 照旧拿全量、本地只渲染最近 40 条）。用户否决：

> 最终的目标还是新协议，新请求，因为每次都拿全部的消息到客户端这也要等待很久

**他是对的，这是设计缺陷**：本地切片只砍"渲染"，传输+解析一点没省。

真机数据（姜萌账本 43 回合）：`limit=0` → 169 条 / 42.1KB；`limit=20` → 69 条 / 13.5KB。
定案：单聊 20 **回合**、群聊 60 **条**（单位不同不是笔误：群发言没有回合概念）。

**用 `seq` 游标而非 offset**：账本有 `history/delete` 删除注记，删一条其后下标全前移 ⇒ offset 会重复/跳过。

**必须双锚 `(before_seq, before_turn)`**：实测只用一个时翻页出现
`第1页[38..58] / 第2页[18..42]` —— 38、42 在两页都出现。原因是姜萌 43 回合里有 **2 个回合完全没有 seq**
（`initiative` / `turn_error` 这类合成内容），只按 seq 判会把它们当"永远更早"⇒ 每页重复。群聊不需要双锚。

**验证方式是逐条重建**（不是只测条数）：页长 5/10/20/40/58/100 下翻页累积都能
**逐条精确重建全部 169 条**、顺序一致、不重不漏。漏一条就是"某句话凭空消失"，比卡顿严重。

**④ 上划加载的四个实机 bug（用户逐个实测暴露）**

| 症状 | 根因 |
|---|---|
| 入口行不出现 | `RememberHistoryCursor` 只写了 Presenter 的 `_histHasMore`，**从没调 `SetEarlierState`** ⇒ 窗口那边永远是 false ⇒ 连行都不建 |
| 4 秒翻 2 页（"全送过来了"） | 插入后当帧读 `rect.height`（布局未结算）⇒ `added≈0` ⇒ 视口算成 `v=1`（顶部）⇒ 手指还在滚轮上，下一格又满足 `AtTop()` |
| 位置不在"刚上划处" | 同上；改成**延迟一帧锚定** + 锚定期间拦截自动触发 |
| 日期分隔行堆在末尾 | 一个 item 可能新增**两个**节点（分隔行 + 气泡），旧代码只收 `GetChild(after-1)`（气泡），分隔行留在原地 |

另有日期**乱序**（截图：`2年8月3日→3年7月4日→8月19日→2年8月3日`）：`_sepHas/_sepY/_sepM/_sepD`
是跨行累进状态，从新到旧插入时它属于更晚的历史 ⇒ 每条都判成"日期变了"。
修法：插入前清空、插入后复原。

**⑤ 部署这个坑（用户一句话点破）**

用户问「你 python 有打包部署吗」—— **没有**。我改了 Python 源码，但游戏跑的是
`ModAssets/AgentLoopServer.exe`（打包产物）。`DEV_RULES.md` §四 只写了 C# 部署，**完全没写 Python 侧**。

最误导的地方：我当时拿 `limit=20 → 返回 69 条` 当"新代码在跑"的证据。
但 `limit` 是**旧参数**，旧 exe 一样返回 69 条，只是**不返回新增的 `next_before_seq`**。

> **判据必须是"新增的东西出现了"，不是"旧行为还在"。改协议时盯新字段，不能盯新参数。**

已补进 `DEV_RULES.md` §四「Python 侧」：两档打包（开发 PyInstaller 16 秒 / 发布 Nuitka）+ 三个必踩坑
（`--deploy` 会用 stage 里的旧包、exe 被占用会 PermissionError、`[Build]` 那行只验证 DLL）。
§十一 排查顺序第 1 条也拆成 C#/Python 分别判。

**⑥ 须知与版本**

`1.2.1` → `1.2.2`（`About.cs` 与 `__init__.py` 同步）。公告 `Seq=7`，9 条，含并行会话的
群聊 @ / 气泡宽度 / 读档后工具调用自愈。`Guide` 加一节「历史消息往上滚才会出来」
（玩家可能误以为历史丢了；拖动是可发现的自明操作，不单列）。

**验证**：全量 pytest **2419 绿**、C# **33 工程全过**、定向 `--changed` 全绿。
变异验证本功能累计 **40 组**。其中 **4 次第一版没抓住**，全是同一个模式
（断言写成了"符号存在"而注释/别处也有那个符号）：`_restoreDiagLogged` / `_dragAppliedPos` /
`EnsureOwnRaycaster` / `before_turn`。已固化成四条检查清单写进 journal。

**注意**：`room_flow.py` / `ChatFrameQueue.cs` / `ChatFrameTee.cs` / `test_room.py` 是 CRLF 文件，
用 Python 脚本改会把整文件转 LF（假 diff 上千行）。本轮又踩了一次。

---

## 2026-11-21 · 群聊 @ 补全：回车抢键 / 回声去重 / 部署后不验证

三条独立的事，都记在这里。

**① 回车选了候选却把 `@` 发出去（11-21 实机）**

根因是**顺序**：`PollMentionPick` 先重算候选、再看 `IsOpen` 决定吃不吃键。
而单行 `InputField` 按回车会**先 Submit 并自动失焦**（本文件头早有实测记录），
失焦挪走 `caretPosition` ⇒ 同一帧重算算出"光标不在 @ 段里" ⇒ `Hide()`
⇒ `IsOpen` 变假 ⇒ 回车落到发送分支。

实机证据两条互相印证：Python 侧 `@ 解析失败（unknown=[]）`（@ 后为空）、
`Player.log` 里 `@ 补全选中` **零次**（回调没跑过）。

改法：键盘处理挪到重算**之前**；抢键判据从「浮层开着」换成「文本里有一个
还没确定的 @ 段」（`_pickCtx`，只随重算更新，与这一刻的光标无关）。
替换也用 `_pickCtx` 而不是重新 `Analyze` 实时光标。

**② 同一条群消息显示两遍（11-21 用户截图）**

C# 本地气泡画的是玩家**原文**（含 `@名字`），Python 回声里 `@名字` 已被
`strip_mentions` 剥掉，而销账判据是**文本相等** ⇒ 永远不等 ⇒ 画两遍。

这份去重是 09-17 加的，当时判据成立（Python 原样透传）；@ 功能上线后
`run_message` 开始改写正文，接口契约变了而判据没跟上。

改法：Python 在回声帧上额外交 `echo_text`（原文），C# 优先用它销账。
**没有让 C# 复刻剥离规则** —— `strip_mentions` 会吃标点再吃空格再 Trim，
两边各写一套必然漂移且漂移后静默。`ChatFrame` 同步加 `EchoText` 字段，
否则关窗缓冲的消息重放时同一个 bug 会从另一个入口再来一次。

**③ 部署后不再验证（用户 11-21 定规）**

写进 `DEV_RULES.md` §四「部署完就结束」+ `AGENTS.md` 第 4 条。
部署命令跑完、md5 打出来即结束：不解包 exe 查 code object、不 `strings` 查
DLL 符号、不重跑 `--check`、不抽查时间戳。

理由不是省事：md5 判据已经算过一遍，再算一遍不会变准；而"源和目标都是旧代码"
这种情形 md5 挡得住（当年那次误判不是没验 md5，是把"旧参数被接受"当成
"新代码在跑"）。真正证明生效的只有实机跑一次。

同时把 §四 那段解包查 PYZ 的内容**重新定位成排查手册**（只在怀疑"没打进去"时用），
§十一 排查顺序也加了"只在出事了才用"的边界说明 —— 免得下次又把排查手法当常规步骤。

**顺带修掉的既有问题**

- `EarlierRequested` 只订阅、`OnDestroy` 不退订（分页那轮留下的泄漏）。
- 多个 `ChatPresenter` 实例抢同一个静态浮层：非群聊实例每帧
  `Hide()` 会把群聊窗的候选关掉。加 owner 判据（只有建树方能收起）。
- 加 owner 后暴露新漏洞：关窗时 owner 可能是**另一个** presenter，
  `Hide(this)` 直接 return ⇒ 窗口关了浮层还在、`raycastTarget=true` 吃点击且无日志。
  分出 `HideForce()`（越过 owner），用于关窗 / 换 NPC / 退出群聊 / 群解散 5 处。

**测试**：Python 全量 2415 绿、C# 33 工程全过。新增契约测试 10 条，
13 个变异全部转红。三个变异**第一轮没抓住**（断言空转）——
`_pickCtx.HasAt` 在 Esc 分支里也出现，改用正则精确抓 `bool picking = ...;` 才生效。

**注意**：`room_flow.py` / `ChatFrameQueue.cs` / `ChatFrameTee.cs` / `test_room.py`
是 CRLF 文件，用 Python 脚本改完会把整文件转成 LF（假 diff 上千行）。
本轮又踩了一次，已按字节恢复并复核 `lone_LF == 0`。

## 2026-11-20 · APPENDIX 拆解 + archive 扁平化 + features 套模板

**拆掉 `docs/reference/APPENDIX.md`（3264 行）**。它是六个互不相关的文件被拼成一个，用字母编号
硬串起来。拆完删除原文件。

| 原节 | 现在 |
|---|---|
| A 桥契约（196） | `reference/bridge-contract.md`（**去重**：RPC 全表早就在 `ui-interception-spec.md` §3.2） |
| B 日志规约（111） | `reference/logging.md` |
| D.1~D.7 铁律（465） | `reference/csharp-iron-laws.md` |
| D.8~D.27 踩坑（1785） | `archive/` 下 **20 篇**带日期文档 |
| D.28 图片（76） | 删除（已由 `features/image-context.md` 覆盖） |
| E UI 工作流（344） | `reference/ui-workflow.md`（删掉已作废的 `ui_preview` 预览工程段落） |
| G 设计底稿（260） | `reference/design-notes.md`（去重：主动传音/同格确认窗已在 `ui-interception-spec.md` §7.5） |
| H API 速查（88） | `reference/api-reference.md` |

`reference/` 3264 → 1473 行。

**archive 扁平化**：删掉 `bugs/` `audits/` `investigations/` 三个分类目录，
改成"**日期开头，一件事一份 `.md`，多份一个目录**"（与 `journal/` 同一条规则）。
16 个条目 / 28 份文档。新增 `archive/README.md`（索引 + 写作要求）。

**features 套统一模板**：11 篇全部补齐"一句话 + 指路 + 相关代码"，
编号统一为阿拉伯数字（`team.md` 中文数字、`brain-liveness.md` 重复的 `## 8.` 都改掉），
`tool-result-contract.md` 的段号 `0 → 无 → 9 → 10` 重排为连续。
新建 `features/image-context.md`（79 行，补上被 3 篇 features 引用却不存在的那份）。

**顺带修掉两处一直指错的引用**：
- 代码注释里 6 处 `APPENDIX §D.19` 讲超时，D.19 其实是关系决断，超时在 D.21/D.18.1
- `csharp-iron-laws` 原写"Python 不发 `isError` 字段"—— 实际 C# 读的是 `payload["error"]`，不是 `isError`

验证：全量 2222 通过；C# 29 个测试工程全通过；文档 0 断链。

## 2026-11-20 · 仓库清理与文档体系重构

- 删除无关目录：`hw1_jupyterhub/`（17M，**含明文课程账号密码**）、`backup/`（639M）、
  `generated-images/`、`.workbuddy/`（内容先迁出）、`.mine_0913/`、`NUL`、`_unity_probe.log`
- 仓库体积 700M+ → 117M
- 建立五层文档结构：`features/`（现状）/ `reference/`（查表）/ `archive/`（过程）/
  `journal/`（进行中）/ `release/`（发布）
- `docs/` 根目录 23 篇 md 全部归类，根只留 `README.md` + `DEV_RULES.md` + `CHANGELOG.md`
- 删除 `docs/csharp-audit/` 这个错误分层，拆进 `journal/`
- 删除死代码 `pack_release.py` 的 `NEVER_SHIP`（定义了从未被引用）
- 文档精简：`features/team.md` 643→111 行；`tool-result-contract` 541→132 行；
  `context-projection` 602→119 行（细节移入 `reference/`）；
  `archive/2026-09-17-群聊编排/设计定稿.md` 2968→1267 行、`team-experiment-log` 1581→221 行（过程流水精简）
- → [`journal/2026-11-20-仓库清理与文档规范/`](journal/2026-11-20-仓库清理与文档规范/)

## 2026-11-20 · 工具层拆分

- `csharp/tools/ToolExecutor.cs` 6711 行 → 按 Python 的域拆成 10 个文件，主文件只剩 289 行
- 切分依据：跟着 `tools/schemas.py` 的两级划分，不自己发明维度
- → [`journal/2026-11-20-工具层拆分/`](journal/2026-11-20-工具层拆分/)

## 2026-11-19 · 常驻帧回调句柄化 + 事件订阅对称

- **帧回调**：`_tickRegistered` 这类"全进程一次的布尔"全部换成 `FrameTicker` 句柄表。
  真机证据：走 FrameTicker 的键恒 1.00 次/帧，裸注册的从第 3 段世界起恒 **2.00** 次/帧
- 新增 `FrameTickerCore`（纯逻辑 + 单测），`idle` 分三态（`3` / `~3` / `!90`）
- **事件订阅**：`SaveData` / `IntoWorld` / `WorldAddDay` 补上 `Off`（原先只订不退）
- 修 `MapMainContactButton._ticking`（无复位路径，日志里 `mapbtn` 第 3 段世界起消失）
- 新增契约测试：`test_frame_tick_contract.py`、`test_resident_tick_contract.py`、
  `test_game_event_subscription_contract.py`
- → [`journal/2026-11-19-常驻帧回调句柄化/`](journal/2026-11-19-常驻帧回调句柄化/)

## 2026-11-19 · C 端重复实现复核

- 按 10-28 口径复扫 `csharp/`（72,470 行 / 129 个文件）
- **业务判据没有重复**（「判据归核、接线归层」那套在生效）；重复全在基建层
- → [`journal/2026-11-19-C端重复实现复核/`](journal/2026-11-19-C端重复实现复核/)

## 2026-10-28 · C 端代码审查

- 按"各司其职、井井有条"审 `csharp/` 的结构与冗余（67,268 行）
- 判据分三类：A 冗长 / B 混乱 / C 死重
- 核心判断：`state/` 的「事实层 / 纯逻辑核 / 接线层」三分法是全仓最好的结构，但只覆盖 90% 之外的一小块
- → [`journal/2026-10-28-C端代码审查/`](journal/2026-10-28-C端代码审查/)

---

## 更早

`git log` 是完整记录。上面这些之前的工作（09~10 月）没逐条回填 ——
它们的过程详见 [`archive/`](archive/) 里的各功能目录。
