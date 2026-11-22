# UI 工作流（预制件 → AB → 部署 → 验证）

> 数据源：`docs/reference/csharp-iron-laws.md` §E 拆出（2026-11-20）。
> 改面板样式 / 重打 AB / 面板看不见时看这里。
> UI 的**架构与数据流**不在这里，见 [`ui-interception-spec.md`](ui-interception-spec.md)。

---

## 一、链路

### 1.1 只有一份预制件真相（历史：曾经有两个工程）

链路：`csharp/UI/*.cs`（视图源码）─改─► `ResBuildABProject/Assets/Resources/UI/UI*.prefab`（**进游戏唯一真相**，用 `scripts/dev/prefab_patch_*.py` 改）─菜单打包─► `.ab` ─► 部署目录 ─► 游戏 `g.res.Load` 装配。

> **`ui_preview/` 预览工程已于 2026-09-14 删除**（用户拍板走 AB 资产固化）。下文凡是提到它的段落都是历史记录，**现在不存在那个目录，也没有「两份预制件」了**。

| 角色 | 路径 | 真相 / 同步 |
|---|---|---|
| 打包工程 | `E:\SteamLibrary\steamapps\common\鬼谷八荒\Mod\modFQA\资源修改教程\ResBuildABProject` | **进游戏唯一真相**；`Assets/Scripts/ABBuild/Editor/ABBuildRunner.cs` 在此 |
| 部署目录 | `E:\SteamLibrary\steamapps\common\鬼谷八荒\ModExportData\Mod_Jgmg5L\` | 游戏实际加载 `ModRes\AssetBundle\ab\ui\` 下的 `.ab` |

* **双份源码的教训（历史）**：曾用 NTFS 硬链接同步 `csharp/UI` 与预览工程，**编辑器保存是原子替换 inode，硬链必断**。现在没有第二份源码 —— 视图源码只有 `csharp/UI/` 一处。
* 视图件按依赖分两类：纯 Unity 的进预制件；`ConfigPresenter`/`AbXxxPanel` 依赖 `WsClient`/`g.ui`，**不进预制件**，由代码装配。

## 二、打包

### 2.1 官方工程 + 自包含 bundle

现役入口 = 菜单 **「游戏工具 → 更新 → 更新AB(自包含)」**（`ABBuildRunner.RunFromMenu` → `Run()`）。三个 bundle：`ab/UI/UIChatAi.ab`（prefab + `Assets/art/panel_bg.png.png`）、`ab/UI/UIConfigAi.ab`（纯 prefab）、`ab/UI/UIContactAi.ab`（prefab + 圆点/红圆点）。

* **必须"自包含"（已踩坑）**：官方教程说「背景图放 `Assets` 非 `Resources`，防止多余 AB」——**实测是反的**：贴图被拆进独立依赖包，加载器只拉主包 → **引用断裂、面板变白块**。正解 = 预制件与它引用的贴图**同名 bundle、一次成包**（根 manifest 该条 `Dependencies: {}`）。官方「更新AB」拆依赖 → 白板，**别用**。
* **新增贴图必须登记进 ABBuildRunner**：`BuildPipeline` 只打**已分配 bundle 名**的资源（通讯录曾因 bundle 名从未赋值 → 改了也永远打不出来）。
* 路径可含中文，**产物名必须全英文**（`UIChatAi`/`UI`）。
* 批处理构建（⚠️ 含中文路径的脚本/`.cs` 须 **UTF-8 带 BOM**，否则 PowerShell 按 GBK 解析乱码）；判据 = 日志出现 `[ABBuildRunner] self-contained AB build done -> …`，产物在 `Assets\AssetBundle\ab\ui\`（**产物名全小写**）：

```
"C:\Program Files\Unity\Hub\Editor\2020.3.9f1\Editor\Unity.exe" -batchmode -quit \
  -projectPath "E:\SteamLibrary\steamapps\common\鬼谷八荒\Mod\modFQA\资源修改教程\ResBuildABProject" \
  -executeMethod ABBuildRunner.Run -logFile "F:\agent_loop\_ab_build_log3.txt"
```

* 重新生成预制件：
  （用户拍板走 AB 资产固化，不再用编辑器预览/生成）。预制件现在只有一个维护入口：
  `scripts/dev/prefab_patch_config_groups.py`（幂等、可重跑、纯文本手术）直接改 AB 工程那份。
  ⚠ 历史坑留个记性：当年 `ui_preview/Assets/MUD_UI/ConfigUiBuilder.cs` 与 `csharp/` 那份
  **会不同步**，而「生成配置面板预制件」会**覆盖手改** —— 删掉预览工程顺带消灭了这个隐患，
  所以现在**只能用 `prefab_patch_*.py` 改，不要去找「生成预制件」的菜单**。

## 三、部署与核对

### 3.1 两端核对口径

* AB：`ResBuildABProject\Assets\AssetBundle\ab\ui\*.ab` → `…\Mod_Jgmg5L\ModRes\AssetBundle\ab\ui\`（**小写 `ab/ui`**）；**只拷 `ab/`——两侧 `assets/` 都是首次构建的依赖残留，部署前删掉**。
* dll：`csharp\bin\Debug\MOD_Jgmg5L.dll` → `…\ModCode\dll\MOD_Jgmg5L.dll`（见 D.2）。
* **核对口径 = 两端同名文件比大小/MD5**。历史教训：`uiconfigai.ab` 打包产物 197991 B vs 部署 206839 B → 配置包没随最近一次构建部署；**改过面板样式后务必重拷**。

## 四、进游戏验证

### 4.1 重启与入口排查

1. **重启游戏**：`g.res.Load` 失败会写 **null 缓存**、同会话永久命中，必须重启重测（D.3）。
2. 入口：NPC 面板「AI 对话」/ F10 通讯录 / F11 配置。面板不可见时依次查宿主父链 `activeInHierarchy` → 根 `RectTransform` 尺寸/锚点 → `CanvasGroup alpha` → 同层多 Canvas 被 `sortingOrder` 压低（D.3）。日志落 `Player.log`，**必须 `grep -a`**。

## 五、气泡正文：行距与富文本

玩家反馈两条：「文字多的时候密密麻麻」「希望模型能输出富文本，用不同颜色」。两件事都作用在**同一段正文**上，但机制完全不同，改一处不会带动另一处。

| 关注点 | 载体 | 生效条件 | 护栏 |
|---|---|---|---|
| 行距 | `Text.lineSpacing`（预制件 `m_FontData.m_LineSpacing`） | 预制件改完**必须重打 AB**；C# 运行期兜底则重启即生效 | `test_line_spacing_matches_the_prefab_patch_script`（两条腿逐值相等） |
| 富文本 | `Text.supportRichText` + 正文里的标签 | 同上；清洗在 C#，**不需要** AB 配合 | `test_rich_text_bubble_contract.py`（14 条） |

**行距 1.35 的来处**：中文字形是满框的（不像拉丁字母自带留白），`lineSpacing=1` 时两行墨迹几乎正好相接 ⇒ 一整段读下来是一坨。1.35 与 `NoticePanel` 正文同款。

量化过（20px 字号、19 字/行、6 行的一段台词，按 uGUI 的 `行高 = fontSize × lineSpacing` 模拟后量墨迹）：

| 行距 | 气泡总高 | 相邻行墨迹空隙 | 相对 1.0 增高 |
|---|---|---|---|
| 1.0（改前） | 148 px | **0 px**（墨迹真的相接） | — |
| 1.2 | 172 px | 5 px | +16% |
| **1.35（定稿）** | **190 px** | **9 px** | **+28%** |
| 1.5 | 209 px | 13 px | +41% |

1.0 的「墨迹空隙 = 0」就是玩家说的「没有行间距」，不是感觉问题。1.35 的代价是气泡高 28%、
长对话要滚更多 —— 这是真实的取舍，不是白拿。选 1.35 是因为它让「六行」从一坨变成一段，
再往上（1.5+）增高收益递减、滚动负担递增。

**富文本为什么不能直通**：把模型输出原样塞进 `Text.text` 有两个**真实**风险 ——
① 标签不合法（拼错/不在 Unity 支持集）⇒ 上屏一堆尖括号，比不富文本更难看；
② 颜色选浅了（`#FFFF00`）⇒ 浅灰/浅绿气泡底上**等于隐形**，玩家只会觉得"字没了"。

所以正文统一过 `BubbleTextCore.Render()` = `Rich(Tighten(s))`：

* **白名单**只有 `color`（8 个命名色 / `#RRGGBB[AA]`）、`b`、`i`、`size(8..30)`；
  其余（含游戏自带 `<g>/<y>/<sprite>`）**连标签带尖括号剥掉、内容保留**；
* **亮度压制**：颜色一律压到感知亮度 ≤ `LumCap=0.50`（保色相只压暗）。两种气泡底亮度
  0.91 / 0.76，压完任何色相都还能读；调色板里每个值本身就在上限以下，正常路径不会被改动；
* **配平**：缺闭合自动补、孤立闭合丢弃、同类重复开先收前一个。流式期间开闭标签**分两帧到**，
  不配平的话半句话会染色闪一下；孤立 `</color>` 更会让**后面整段错色**；
* **流式半截标签**（这一帧断在 `<color=#C6`）整段丢弃。判据保守：后面出现中文/标点就当正文，
  所以 `甲<乙` 不会被吃掉。

**四个写泡点必须全走 `Render`**（流式刷新 / 终稿覆盖 / 行覆盖 / 新建气泡）——漏一处就是
"同一段话露不露裸标签取决于走哪条判重分支"，而那四条路**肉眼分不出来**。

**提示词那半边**：`prompts/sections/world_rich_text.txt`（登记在 `_SECTION_MAP`，
order=**-75**，必须 < 0 —— 排在 `deployment:persona(0)` 之后会废掉 provider 的前缀缓存）。
颜色名与 C# 调色板**双向**比对：模型写对名字而 C# 不认 = 静默无色。**刻意不教 `<size>`**，
它能被用来撑破版式。

**⚠ AB 重打的拦路虎：Unity 许可证过期**（10-31 实测）。
batchmode 报 `BatchMode: Unity has not been activated with a valid License`，
`-force-free` 也无效（Personal 许可证需要联网续期）。此时 prefab 已经改好但**打不出 `.ab`**，
表现是"预制品那条腿不生效"，**而 C# 运行期兜底覆盖同一效果** —— 所以功能不会缺，只是没固化进资产。
解法：打开 Unity Hub → 账号重新登录 → 启动一次编辑器 → 再跑 batch 命令。

### 富文本的**提示词那一半**：实测命中率与畸形标签

C# 清洗器只保证"写对了能显示、写错了不出洋相"，**模型会不会写**完全是提示词的事。
10-31 用本地网关（`127.0.0.1:8788`，配置面板那个 "测试连接" 用的同一条路）+ 真 system + 6 个典型场景
做了 A/B，数据如下（**改提示词前先看这张表**）：

| 版本 | 命中 | 副作用 |
|---|---|---|
| 只列语法（"可以用这些标签"） | 0~1/6 | — |
| 场景清单（威胁标红、数目标黄…） | 3/6 | 颜色常标在"名词"上而不是"重点"上 |
| 强制"每条回复都要标" | **5/6** | ★ 模型开始**写崩**：`<>`、`</>`、`<color:黄>`，且把颜色标到闲聊 |
| ★ **定稿**：正面要求 + 挑哪处 + **保留白字出口** | 3/6 | 零畸形 |

**定稿刻意没选 5/6 那一版**：多出来的命中落在"闲聊/应答"上（给「今天天气不错」标个颜色），
那不是玩家要的"更多样"，是噪音；代价还是模型被逼出畸形语法。**规律很清楚：有冲突或有硬信息的
场景会标，纯寒暄和短应答不标** —— 正是想要的形状。契约 `test_prompt_has_the_measured_effective_shape`
把这三条钉住了（正面要求 / 挑哪处的判据 / 白字出口）。

**模型实测会写的畸形标签**（都已进 `BubbleTextTests` 判定表，`Render` 必须清干净）：

| 写法 | 原因 | 不清会怎样 |
|---|---|---|
| `<color>黄>字</color>` | 漏等号，颜色名跑到尖括号外 | 上屏 `黄>字` |
| `<color:黄>字</color>` | 用冒号不用等号 | 标签被丢、字留下（可接受） |
| `<>字</>` | 空标签（被"必须标"逼出来的） | 上屏 `<>字</>` |
| `<color=浅黄>` | 颜色名不在表里 | 标签被丢、字留下 |

前两种靠 `SkipTagTail`（丢掉标签后顺带吃掉紧邻的写崩尾巴），第三种靠
`LooksLikeTagBody` 把"清干净后什么都不剩"也判成标签。**这三条都是真实模型跑出来的样本，不是编的**
—— 提示词一加强，写法就会千奇百怪，这是必然代价。

**怎么复现这套测法**（改提示词后建议重跑）：把 `config.json` 里 `llm` 那一组读出来，
用 `SystemPrompt.render_prompt(assemble(npc))` 拿真 system，对 `/chat/completions` 发几个
"玩家说了 X"的舞台提示，看回复里有没有 `<color=`。**别只跑一个场景** —— 单场景的结论会被偶然性带偏
（我第一版只跑 3 个，结论是 1/3，看起来"能用"，实际是 1/6）。

### 运行期兜底能不能命中 `.ab` —— 不用 Unity 也能验

10-31 那一批 UI 修复（行距 / 富文本 / 九宫格 border / 内边距 / 对齐）全都走"两条腿"：
预制件改资产（**要重打 AB**）+ 运行期 `ApplyBubbleTextLayoutFloor` 直接改组件。
而 **AB 是 09-17 的包、prefab 是 10-31 的**，玩家实际只跑运行期那条腿。

"兜底能命中"过去一直是**读代码 + 读 prefab 推断**出来的 —— 但 prefab 是新版、`.ab` 是旧版，
两者结构未必一致。代码再对，只要 `.ab` 里没有那个节点/组件，`FixBubbleLayout` 就在
`if (b == null) return false;` 处**静默跳过**：测试全绿、玩家什么都没看到。

现在有一个不用 Unity 的核对：

```bash
python3 scripts/dev/check_ab_fallback.py            # 默认查部署的那份
python3 scripts/dev/check_ab_fallback.py <x.ab>
```

它解包 `.ab`（复用 `ab_probe.py`）后逐条核对 11 个前置结构，与 C# 的四条改动一一对应：

| 兜底 | 要核对的结构 |
|---|---|
| 前提 | `NpcRow` / `UserRow` / `NpcBubble` / `UserBubble` / `Templates` |
| ① 对齐 | `m_Alignment` |
| ② 内边距 | `m_Padding` / `m_ChildAlignment` |
| ③ 行距 + 富文本 | `m_LineSpacing` / `m_RichText` |
| ④ 九宫格 border | `m_Sprite` |

退出码 0 = 齐备。10-31 实测：**11/11 全在**，四条兜底都能命中（所以"不重打 AB 也能生效"这个
结论终于有证据了，之前只是推断）。

**局限（诚实边界）**：看的是字符串表（类型树字段名 + 节点名），只能证明**结构存在**，
证明不了**数值对**。数值那条腿由 `tests/test_chat_bubble_layout_contract.py` 钉在常量层，
运行时实际值只能上机看日志。

护栏：`test_ab_fallback_checker_covers_every_thing_the_fallbacks_touch`（检查项必须覆盖
C# 的每一处改动，三处变异全部被抓）+ `test_deployed_ab_still_supports_the_runtime_fallbacks`
（有人换了份更老的 `.ab` 时立刻变红）。

### 行距是**乘数**，乘的是**字体基础行高** —— 观感随字体差一倍

查了 Unity 2020.3.9f1 自带的 UGUI 源码（`BuiltInPackages/com.unity.ugui`）与
`UnityEngine.TextRenderingModule.xml`，`lineSpacing` 的确切语义是：

> `TextGenerationSettings.lineSpacing` —— **"The line spacing multiplier."**

它是**乘数**（1.0 = 默认），乘的是**字体基础行高 = ascent + descent**，
而基础行高**不等于 `fontSize`**。实测（fontSize=19，直接读字体度量）：

| 字体 | 基础行高 | 比值 | ls=1.35 时行盒 | 墨迹空隙占字号 |
|---|---|---|---|---|
| SimHei | 20 | 1.05× | 27.0 | **53%** |
| MSYaHei | 26 | 1.37× | 35.1 | **95%** |

**同一个 1.35，在 MSYaHei 上比 SimHei 上松一倍。**
而字体来自三级兜底链（① 捞游戏现成 `Text` 的字体 ② `Font.CreateDynamicFontFromOSFont("SimHei", 20)`
③ 内置 `LegacyRuntime.ttf`）—— **每台机器拿到的可能不是同一款**。

所以 `ResolveSharedFont` 现在会**打一行日志**记下实际字体名、`dynamic`、度量、行盒估算：

```
[ChatUiBuilder] 共享字体 = 「SimHei」 dynamic=True 字形框高=17 行距=1.35 → 行盒 ≈ 23.0
```

这一行是**唯一**能判断"玩家那边到底多松"的依据。觉得行距不对时先 `grep -a "共享字体"`，
再决定调不调 `BubbleLineSpacing` —— 否则就是盲调（在 SimHei 上合适，在 MSYaHei 上可能已经太散）。

⚠ 顺带记一条边界：`TextGenerator` 是 **Unity 原生实现**（`UnityEngine.TextRenderingModule.dll`），
源码不在 UGUI 包里，所以我们只能拿官方文档 + 字体度量 + 实机日志三者对齐，
**没法读它的逐行实现**。上面那张表是"按文档语义 + 真实字体度量"推出来的，不是从实现里抠的。

### 九宫格 border：长气泡的圆角会被拉成椭圆（10-31）

`spriteBorder` 原来只有**尾巴那一侧**有值，上下全是 0：

| 图 | 原 border (L,B,R,T) | 修后 |
|---|---|---|
| `npc气泡.png` | `38, 0, 0, 0` | `38, 17, 35, 17` |
| `玩家气泡.png` | `0, 0, 33, 0` | `10, 19, 40, 19` |

`Image.Type.Sliced` 会把**没设 border 的那一维整段拉伸** ⇒ 气泡随文本变高时：

* 两端圆角（占图高 **27%**）被纵向拉成**椭圆**；
* 尾巴被拉成一根**尖刺**。

短气泡看不出来，**文本一长就变形** —— 而"文本长"恰恰是这轮要修的场景。

**数值是量出来的，不是拍的**：取**没有尾巴那一侧**的边缘，看它从哪一行开始稳定，
那就是圆角结束、可以安全拉伸的起点。两图圆角都占高度的 27%（17/125、19/139）。

**同一件事两条腿**（同 ①②③ 的约定）：
* 预制件：改 `.png.meta` 的 `spriteBorder` —— **要重打 AB**；
* 运行期：`FixBubbleLayout` ④ 用 `Sprite.Create(...)` 造一份带正确 border 的新 Sprite 赋回
  （`Sprite.border` 是**只读**的，只能造新的；纹理共享，不多占显存）。

护栏：`test_sprite_border_matches_between_meta_and_csharp`（两条腿逐项相等）、
`test_sprite_border_has_all_four_sides_set`（四边都非 0）、
`test_runtime_leg_actually_rebuilds_the_sprite`（真的造+真的赋回，且赋值**无条件**）。

⚠ 最后那条踩过假绿：第一版只断言 `"img.sprite = "` 出现过，把外层条件改成 `if (false)`
仍然通过 —— 那等于"造了新 Sprite 却不赋回"，兜底完全无效。现在断言完整的无条件赋值语句。

### 压暗判据从「亮度阈值」改成「WCAG 对比度」

第一版用「感知亮度 ≤ `LumCap(0.50)`」判断"这个颜色够不够暗、能不能直接上屏"。
**扫了整个色空间后发现有 34% 的过判颜色对比度不足**：

| 颜色 | 感知亮度 | 过 LumCap? | 灰底对比度 | **绿底对比度** |
|---|---|---|---|---|
| `#7F7F7F` | 0.498 | ✓ 过判 | 3.30 | **2.60** ← 隐形 |
| `#6E6E6E` | 0.431 | ✓ 过判 | 4.20 | **3.31** ← 不够 |
| `#909090` | 0.565 | ✗ 被压暗 | — | — |

灰阶 `#5C5C5C ~ #7C7C7C` 整段落在盲区里。根因：**亮度判的是"够不够暗"，
而"读不读得出来"是两个颜色之间的关系。**

现在 `CapLum` 先查**最差的那个气泡底**的 WCAG 对比度（相对亮度比），不过就等比压暗
（保色相），直到过 `MinContrast = 4.5`。本来就够清楚的颜色**一个字节都不动**。

**全色空间验证**：4096 个输入色跑一遍 `Render`，不合格 **0 个**，最差对比度恰好 4.50
（改造前会有约 1/3 的颜色漏出去）。

护栏：`test_cap_uses_wcag_contrast_not_a_luminance_threshold`（禁止退回亮度判据、
要求两种底都查）+ `test_cap_preserves_hue_by_scaling_proportionally`（必须等比缩放，
不能压成灰）。四类变异全部被抓。

### `<b>` / `<i>` 已删除（实测依据：模型不用）

10-31 用真实模型跑 8 个场景，统计标签使用：

| 标签 | 使用次数 | 结论 |
|---|---|---|
| `<color>` | 6/8 | 主线，保留 |
| `<b>` | **0/8** | 删 |
| `<i>` | **0/8** | 删 |

再加一句「每次回复都必须用一次 `<b>` 把最有力的那个词加粗」，模型**照样一次都不用**
（那一轮它改用 `<color=gray>` 了）。技术上是能用的 —— uGUI 的伪粗体实测墨迹 +77~80%
（`SimHei` 1125→1992 像素、`MSYaHei` 1275→2289）—— 但**模型不用**。

留着它们的代价：提示词里两条永不触发的语法噪音，还占掉模型对 `<color>` 的注意力。
所以 **C# 白名单与提示词同步删掉**（两边必须一致：只删一边会出现"说不认却会变粗"）。

契约 `test_csharp_whitelist_has_no_bold_or_italic` + `test_prompt_lists_the_whitelisted_tags_only`
双向钉住，四类变异（把 b/i 加回 C#、加回 `StartsLikeTagName`、加回提示词）全部被抓。

**顺带一个发现**：模型很爱用**英文颜色名**（实测出现过 `<color=red>` / `<color=yellow>` /
`<color=gray>`）。所以英文名不是"备用通道"而是同等主要的一条路 ——
`test_english_and_chinese_names_resolve_to_the_same_color` 钉住"中英同名必须同值"，
否则同一个「红」会时深时浅。

### 调色板是**按两种气泡底算出来的**，不是看着挑的

两种气泡底都不是白色：**NPC `#E9E9E9`（灰）**、**玩家 `#9BE375`（浅绿）**。
第一版按灰底挑了 8 个明快的色，在 NPC 气泡上勉强及格，但**玩家气泡是绿底** ——
橙 `#C05621` 在上面对比度只有 **2.97**（WCAG 正文要求 4.5），绿底橙字等于没标。
**而这件事完全不报错**，只有真机才看得出来。

重做方式：对每个色相求"在两种底上都 ≥4.5"的**最大亮度**，得到一组整体偏深的颜色。
看得见比鲜艳重要。

| 色 | 旧值 | 新值 | vs NPC 灰底 | vs 玩家绿底 |
|---|---|---|---|---|
| 红 | `#C62828` | `#B2121D` | 5.77 | 4.55 |
| 黄 | `#8A6D00` | `#705600` | 5.73 | 4.52 |
| 绿 | `#2E7D32` | `#0A681D` | 5.75 | 4.53 |
| 青 | `#00706B` | `#0B6369` | 5.76 | 4.54 |
| 蓝 | `#1565C0` | `#1257B1` | 5.72 | 4.51 |
| 紫 | `#6A3FA0` | `#8423C5` | 5.71 | 4.50 |
| 灰 | `#5A5A5A` | `#595959` | 5.77 | 4.55 |
| ~~橙~~ | `#C05621` | **删除** | 3.76 | **2.97** ✗ |

**「橙」是刻意删掉的，不是漏了**：深色区里红/橙/黄的色相距离只有 45~50，
而 <55 人眼就分不清 ⇒ **物理上塞不下三个可区分的暖色**。留红/黄两个，
模型写「橙」会静默降级成白字（不报错，但那句话就是没标）。提示词也只教这 7 个，两边一致。

护栏（`tests/test_rich_text_bubble_contract.py`）：
* `test_every_palette_color_is_readable_on_both_bubble_backgrounds` —— 逐色逐底算 WCAG 对比度；
* `test_palette_colors_are_distinguishable_from_each_other` —— 两两 RGB 距离 ≥55；
* `test_orange_is_intentionally_absent` —— 挡住"好心把橙加回来"。

⚠ 改调色板时**必须三种事一起过**：两种底上对比度 ≥4.5、与现有 7 色距离 ≥55、
提示词里的色名集合与 C# 表**逐项相等**。少一样就是"要么看不见、要么分不出、要么模型不会写"。

## 六、预制件编辑铁律

### 6.1 铁律 + Unity 中文速查

* 场景里改结构弹「无法重构预制件实例」→ **右键实例 → 预制件 → 打开预制件**（Prefab Mode）改，改完回场景保存。
* 改了但文件里没变 → 你八成在**播放模式**改的（改动一律不保存）：停止播放 → Prefab Mode 改 → **点右上角保存**。
* 根 Scale 必须 **(1,1,1)**（资产本体；改实例无效）；行/气泡模板**必须全部不激活**（构建后 `SetActive(false)`）。
* 编辑模式挂的回调/refs 进 ▶ 会被场景重新序列化清掉（`UnityEvent.AddListener` 与 C# 运行时字段都不序列化）→ 预览驱动必须在其 `Start` 里按契约**重新组装初始化**。
* **中文界面术语**：层级=Hierarchy｜场景=Scene｜游戏=Game｜项目=Project｜检查器=Inspector｜控制台=Console（红字=错误）｜播放键=▶。
  * **播放键消失**：九成是**双击了「场景」标签把它最大化**——再双击一次还原；加组件要先在**层级**选中对象。
  * 新建 UI 文本：右键菜单**默认只有 TextMeshPro**，要选「**文本（旧版）**」/「**输入字段（旧版）**」（本项目面板全用 legacy UGUI `Text`/`InputField`）；改字若两种文本都可能存在，**同时支持 `Text` + `TextMeshPro`**。
* **字体：三级兜底链（新增文本必须显式挂字体）**：`csharp/UI/ChatUiBuilder.cs` 的 `ResolveSharedFont()`（→ `ResolveCjkFont()`）是全项目唯一字体来源：①**捞游戏现成 Text 的字体**（自带 CJK，最优）→ ②`Font.CreateDynamicFontFromOSFont("SimHei", 20)` → ③`Resources.GetBuiltinResource<Font>("LegacyRuntime.ttf")`（再退 `Arial.ttf`）；只解析一次并缓存。新增文本节点必须显式 `txt.font = ChatUiBuilder.ResolveSharedFont();` 并显式赋 `fontSize`——漏挂就是方块/tofu。

**文本显示问题速查**

| 现象 | 原因与解法 |
|---|---|
| 文字跑到面板框外面 | 静态文本（标题/按钮/输入框）子 Text **锚点四角拉伸**：最小 (0,0)、最大 (1,1)，再配内边距；**气泡/行模板内文字除外**——交给布局组接管，子 Text 保持默认点锚 |
| 模板气泡高度恒 0、不随文字撑开 | 气泡根必须**同时**有「垂直布局组（边距 12/12/8/8）」+「内容大小适配器（垂直=首选大小）」，且父级 Content 布局组开**「控制子高度」**——只挂适配器时文字高度传不到气泡根。编辑模式下 inactive 模板显示 0 属正常；想手动调高先改「不受约束」，**预览完改回「首选大小」** |
| 系统行/分隔条显示不出字 | 必须**根节点自己就是 Text**（代码 `GetComponent<Text>()` 只找自身）；文本组件加到根上、删掉旧子 Text |
| emoji/特殊字符变 tofu 方框 | 游戏字体不含 emoji → **图标一律换图**（先例：🔍 搜索图标必须换成 `icon_search`）；`✕` 同理 |

* **布局结构坑（AB 预制体专有）**：视口挂 `RectMask2D` 才裁剪，滚动条竖向关横条；根 Canvas + `CanvasScaler`「随屏幕大小缩放」1920×1080，层级用 `sortingOrder` 显式压过游戏 UI。九宫格需先装包：**窗口 → 程序包管理器 → Unity 注册表 → 搜 `2D Sprite` → 安装**。

---
