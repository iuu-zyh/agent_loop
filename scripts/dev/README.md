# scripts/dev —— 开发期工具（不参与运行时）

**定位**：一次性/可复用的排查与预制件手术工具。运行时只跑 `scripts/server.py` 与 `scripts/chat_cli.py`，
本目录**不被任何生产代码 import**，删掉不影响 mod 运行。

**来历**：2026-09-12 目录整理时，从项目根目录的 `_tmp_*.py` 迁入并重命名（根目录不再堆临时脚本）。
对照表：

| 原根目录名 | 现名 |
|---|---|
| `_tmp_prefab_dump.py` | `prefab_dump.py` |
| `_tmp_prefab_inspect.py` | `prefab_inspect.py` |
| `_tmp_ab_ref_audit.py` | `ab_ref_audit.py` |
| `_tmp_patch_ab_priority.py` | `ab_patch_priority.py` |
| `_tmp_chat_bubble_patch.py` | `prefab_patch_chat_bubble.py` |
| `_tmp_busylabel_patch.py` | `prefab_patch_busylabel.py` |
| `_tmp_promptinput_scroll.py` | `prefab_patch_promptinput_scroll.py` |
| `_tmp_prefab_patch.py` | `prefab_patch_uiconfig.py` |
| `_tmp_probe_gateway.py` | `probe_gateway.py` |
| `_tmp_verify_think_stream.py` | `verify_think_stream.py` |
| `_tmp_derive_replay.py` | `derive_replay.py` |

## 只读工具（安全，随时可用）

| 脚本 | 用途 | 依赖 |
|---|---|---|
| `prefab_dump.py` | 解析 Unity prefab YAML → 节点树 + RectTransform 锚点/坐标 + 组件颜色 | 标准库 |
| `prefab_inspect.py` | prefab 组件级检查（class-id → 组件名映射，查 MonoBehaviour/RT/Canvas 等） | 标准库 |
| `derive_replay.py` | 复刻 session surface 投影 + `derive_messages`，看某个 seq 时刻实际发出去的请求组成 | 标准库（读 `~/.sessions/*.jsonl`） |
| `ab_ref_audit.py` | 打包后体检：三个 UI prefab 引用的工程内资源是否都被 AB 覆盖（并包/旧包兜底判定） | 标准库 |
| `ab_probe.py` | **AB 内容探针**：解 UnityFS 包 → 抽出全部字符串，核对"这个 `.ab` 到底是哪次构建"（节点名/中文文案在不在）+ 包内资源与数据块尺寸。**判断 AB 内容别用 `grep`**（整包 LZMA，恒 0 命中），也别只看文件大小变大就下结论 | 标准库 + `lz4` |
| `probe_gateway.py` | 探测 LLM 网关流式 chunk 结构（`reasoning` 字段名、content 里有无 `<think>`）；换网关/换端点时先跑它 | 标准库 + 网络 |
| `verify_think_stream.py` | 端到端验证 think/body 分流（直连网关跑一次流式生成，确认 `reasoning` 回调到了） | 项目包 + 网络 |

## 预制件手术脚本（幂等，可重跑；跑前先备份 prefab）

| 脚本 | 做什么 |
|---|---|
| `prefab_patch_chat_bubble.py` | 给 `UIChatAi.prefab` 的 NpcBubble/UserBubble 模板 Root 挂 `LayoutElement(prefH=76)`（行高保底 76、超长再拉伸）；AB 工程 + ui_preview 双份 |
| `prefab_patch_busylabel.py` | `BusyLabel` 从父节点正中改回右上角（锚(1,1)/pos(-16,-58)/size(300,24)，Text 对齐 0→2） |
| `prefab_patch_promptinput_scroll.py` | `UIConfigAi.prefab` 的 PromptInput 外挂 ScrollRect/Viewport/Content（多行输入可滚动） |
| `prefab_patch_config_groups.py` | `UIConfigAi.prefab` 的 `BG/PageLlm` 改「4 组标题 + 整页纵向滚动」：新增 Scroll/Viewport/Content，原 14 个行节点搬入 Content，新增 4 组标题 + 5 个参数行；**支持 `--in/--out`**（不硬编码路径），真实工程路径默认拒写；幂等可重跑 |
| `prefab_patch_chat_scroll.py` | `UIChatAi.prefab` 的 `BG/Scroll` 滚轮灵敏度 `m_ScrollSensitivity` 30 → **90**（一格 ≈ 4.5 行正文，用户拍板；聊天余量 2000~4000px，30/格要 70~130 格才从底到顶）。幂等；真件需 `--allow-protected-out`，写前留 `.bak_chatscroll`；**两条腿**：C# 侧 `ChatWindow.ApplyScrollSensitivityFloor()` 在 `Init` 里抬到**下限 90** ⇒ **不重打 AB 也立刻生效**，重打只是把修复固化进资产（照抄 `prefab_patch_scroll_hit.py` + `EnsureScrollInputTarget` 的约定）。同脚本顺带报告 Viewport 退化 0×0（本工程唯一一个，另两个滚动区都是 stretch；线上能用故不改）并在文件头记下「向下一下就到底」的真因在 `ChatWindow.cs` 的 `_followingBottom` 方向闩锁（该 C# 侧已于 09-14 17:18 一并修复并部署 `7b4f7c4d884024b23ca8c05439081325`） |
| `prefab_patch_scroll_hit.py` | 给 `BG/PageLlm/Scroll` 补**射线命中层**（全透明 `Image(raycastTarget=1, a=0)`）：滚动区是纯容器时滚轮/拖动**一点都进不来**（UGUI 事件必须先命中 Graphic 再冒泡到 ScrollRect）——分组改版漏抄了 `FileScroll` 那张图，实机表现为"面板能开、节点全 True、就是滚不动"。幂等；AB 工程 + ui_preview 双份（真件需 `--allow-protected-out`，写前留 `.bak_scrollhit`） |
| `prefab_patch_uiconfig.py` | `UIConfigAi.prefab` 两处全拉伸文本条 anchoredPosition 错位修正 |
| `ab_patch_priority.py` | **UnityPy 二进制补丁**：直接改 `uichatai.ab` 里的 `LayoutElement.m_LayoutPriority`（Unity 批处理被许可证挡住时的替代路线）；产物落 `F:/agent_loop/_tmp_ab_out/`，自校验后需手工替换部署 |

## 注意

- 脚本内路径多为**硬编码**（`F:\agent_loop\ui_preview\Assets\Resources\UI\*.prefab`、`backup\*.bak_*`）——
  沿用本机目录结构；换机器/换目录要改路径。
- 手术脚本同时改 **AB 工程**与 **ui_preview 工程**两份 prefab，改完必须**重打 AB 并部署**才在游戏里生效。
- **AB 换新后必须重启游戏**：`ModAbRes.PreloadAll()` 一次性把 bundle 装进内存 `_bundles`，`EnsureInjected`
  只复用内存里的那份（不重读磁盘）⇒ 运行期替换 `.ab` 再重开面板也还是旧内容。
- **UnityFS 包的格式坑（`ab_probe.py` 已处理）**：归档 flags 报的压缩方式（本工程 `0x43`=LZ4HC）只作用于
  blocksInfo，**数据块以块自身 flags 低位为准**（本工程 `0x41` → LZMA）；且 Unity 的 LZMA 块是
  「5 字节 props + 裸 LZMA1 流」，**没有** 8 字节长度字段，得用 `FORMAT_RAW` + 从 props 反解 lc/lp/pb/dict
  来解（直接 `FORMAT_ALONE` 会报 `Corrupt input data`）。
- `ab_patch_priority.py` 需 `pip install UnityPy`。
- 一次性脚本（已烧结进 prefab、不再复用）已在 09-12 整理中删除，其过程记录留在
  `.workbuddy/memory/2026-09-0*.md` 与 `README.md` 附录。
