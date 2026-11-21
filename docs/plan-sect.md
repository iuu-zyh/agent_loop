# 宗门（sect）功能计划

> 本文是**宗门这一整块**的计划底稿：第 1 章沉淀数据源（后续宗门功能共用），
> 第 2 章是本期要做的「信息查询」，第 3 章留后续条目占位。
> 现状文档见 `docs/features/`；本文只写**要做什么、依据是什么、怎么算做对了**。

> **状态（10-29 / 10-31 记）**
>
> | 章 | 状态 | 验证 |
> |---|---|---|
> | 1 数据源（实证清单） | 完成 | 反编 + 配置表 + 真机旧样本 |
> | 2 信息查询（粒度 / detail 分层） | **已实现** | C# 0 警 0 错；`test_sect_query_contract.py` 15 项 + 相关 4 文件共 60 项绿 |
> | 6 人事：入宗 / 退宗 | **已实现** | C# 0 警 0 错；`test_sect_membership_contract.py` 15 项；相关集共 256 项绿 |
> | 3 后续（晋升 / 招人 / 任务 / 战功 / 搬迁） | 未开始 | — |
>
> ⚠ **两章都还没真机验证、还没部署**。改动的文件见 §5 / §6.8。
> 首次真跑要盯的点：§2.7 的 4 条（查询侧）+ §6.7 的 4 条（人事侧）。

## 0 本期范围

| 项 | 本期 | 说明 |
|---|---|---|
| `query_world topic=sects` 的**查询粒度** | ✅ 做 | `name` + `region` 同时给 → 收窄到该州；传分舵全名 → 只回那一条 |
| **功法偏好**（12 项） | ✅ 做 | 剑/枪/刀/拳/掌/指 + 火/水/雷/风/土/木 |
| **人事树**（宗主 / 大长老 / 长老 / 真传弟子） | ✅ 做 | 带部门、代理、名额上限 |
| 宗门经济/战力/战绩/交情 | ✅ 做 | 顺带，成本近零 |
| **NPC 自主入宗 / 退宗**（外门 / 内门 / 某长老的真传） | ✅ 做 | 见第 6 章；门槛交给 NPC 判断 |
| 内门 / 外门**名单** | ❌ 不做 | 只给人数；名单是几百号人，模型问不到这个粒度 |
| 晋升 / 任务 / 战功 / 搬迁 | ⏸ 占位 | 见第 3 章 |

## 1 数据源（已实证）

### 1.1 入口对象

```
g.world.build.GetBuilds<MapBuildSchool>()      // 全图宗门，含各州分舵
  └ MapBuildSchool
      ├ .buildData : DataBuildSchool.SchoolData      ← getter RVA 0x35F7AC0
      ├ .schoolData : MapBuildSchoolData             ← 字段偏移 0x48
      ├ .schoolNameID / .mainName / .branchName / .nameOrigin / .isBeHold
      └ .IsTopSchool() / .GetTopSchool() / .GetSchoolPower()
```

`CollectTypedBuilds<T>` 已经按泛型重载枚举（无参 `GetBuilds()` 返回无类型 List，
元素转型全抛异常被吞 ⇒ 结果恒空，2026-09-10 真机事故），宗门一律走泛型重载。

### 1.2 人事结构：`SchoolData.postData`（带关系的那一份）

```
SchoolData.postData : DataBuildSchool.PostData      // 偏移 0xD8
├ postMain        : PostMain      { unitData, manageElders }         宗主（+代理）+ 他直管的长老
├ postBigElders   : List<PostBigElders>                             大长老
│                    { unitData, departmentTypes: List<SchoolDepartmentType> }
└ postElders      : Dictionary<SchoolDepartmentType, PostElders>     长老（按部门）
                     { unitData, manageInherit, manageIn, manageOut, lastAllotMonth… }
                        ↑ 真传弟子     ↑ 内门    ↑ 外门
```

`UnitData` = `{ unitID, agentUnitID }` —— `agentUnitID` 非空即“代理”（代理宗主 / 代大长老）。
配套方法：`GetManagerUnit()` / `IsAgent()`。

**不要遍历那个字典**。`PostData.GetElders(dept)`（RVA `0x2A45D30`）反编就是
`postElders` 的 `TryGetValue` 包装：5 个部门各调一次即可，既不碰泛型字典遍历，
也不需要知道 Il2CppInterop 对 `Dictionary<枚举, 类>` 的行为。

### 1.3 扁平名单：`SchoolData` 上的 ID 表

| 字段 | 偏移 | 含义 |
|---|---|---|
| `npcSchoolMain` | `0x70` | 宗主 unitID（字符串） |
| `npcBigElders` | `0x78` | 大长老 ID 列表 |
| `npcElders` | `0x80` | 长老 ID 列表 |
| `npcInherit` | `0x88` | 真传弟子 ID 列表 |
| `npcIn` / `npcOut` | `0x90` / `0x98` | 内门 / 外门 |
| `totalMember` | `0xA0` | 总人数 |
| `money` / `faithMoney` / `reputation` | `0x60` / `0xA8` / `0x68` | 宗门灵石 / 信仰 / 声望 |
| `enemySchoolID` | `0xB0` | 敌对宗门 |
| `agentSchoolMain` / `agentBigElder` | — | 代理宗主 / 代大长老 |
| `dieData` | `0xE0` | 灭宗数据 |
| `unitDieMark` / `unitExitMark` | — | 已故 / 已离宗标记 |
| `unitIDToSchoolPostType` | `0x1B0` | unitID → 职位枚举（**派生缓存**） |

`SchoolData.UpdateUnitIDToSchoolPostTytpe()`（RVA `0x2A4B2E0`）反编显示：它遍历
`npcBigElders / npcElders / npcInherit / npcIn / npcOut` 这几个 List 重建
`unitIDToSchoolPostType` —— 即**扁平表是持久化源**，`postData` 是同一份数据的结构化副本。
两者都在，正好互为兜底（见 2.5）。

### 1.4 功法偏号：`basTypes`

- `MapBuildSchoolData.basTypes`（`schoolData.basTypes`）：12 项，元素是**字符串键**
- `MapBuildSchoolData.buildData` 里的 `BuildSchoolData.basTypes`：同源，`AddBasType(String)` 灌入
- 生成入口：`ConfSchoolInitScale.GetSchoolBasTypes(...)`（RVA `0x4C01740`）
- 面板怎么用：`UISchool.UpdateBasType`（RVA `0x37E4970`）与
  `UISchoolLibrary.UpdateBasTypeUI`（RVA `0x35DB2D0`）都是**循环 12 次画图标**，
  取图标走 `ConfRoleAttributeDesc::GetIcon(string key)` —— 因此元素必然是
  `ConfRoleAttributeDescItem.attribute` 那套键（`basSword`…），不是中文。

12 个键与中文（`LocalText.json` 的 `modEffectType1Name_<键>`，权威）：

| 键 | 中文 | 键 | 中文 |
|---|---|---|---|
| basSword | 剑法资质 | basFire | 火灵根 |
| basSpear | 枪法资质 | basFroze | **水灵根** |
| basBlade | 刀法资质 | basThunder | 雷灵根 |
| basFist | 拳法资质 | basWind | 风灵根 |
| basPalm | 掌法资质 | basEarth | 土灵根 |
| basFinger | 指法资质 | basWood | 木灵根 |

> `basFroze` 是**水**灵根，不是冰。这一条起初按字面猜成“冰”，查 `LocalText` 才定下来。
> 运行期优先走 `ConfRoleEffect.GetValueName(key)`（RVA `0x62442B0` = 静态字典查表 +
> `GameTool.LS`），查不到再落上表。

### 1.5 职位 / 部门 / 名额

**职位枚举** `SchoolPostType`（反射实证）：`None / SchoolMain / BigElders / Elders / Inherit / In / Out`

**部门枚举** `SchoolDepartmentType`：`None / Personnel / Security / Diplomacy / Constructor / Train`

**职位中文名**（`LocalText.json` 的 `schoolPostNameN`，`SchoolPost.json` 的 `post` 列对应）：

| post | 中文 | numMax（area=2 永宁州） |
|---|---|---|
| 1 | 宗主 | 1 |
| 2 | 大长老 | 2 |
| 3 | 长老 | 5 |
| 4 | **真传弟子** | 2 |
| 5 | 内门弟子 | — |
| 6 | 外门弟子 | — |
| 7 | 入门弟子 | — |

> 游戏用词是**真传弟子**。用户口语说“亲传”，输出用游戏的词，不自己造。

**部门中文名**（`LocalText.json` 的 `SchoolDepartmentNameN`）：

| 枚举 | 中文 |
|---|---|
| Personnel | 招贤堂 |
| Security | 巡查堂 |
| Diplomacy | 外务堂 |
| Constructor | 修筑堂 |
| Train | 贮宝堂 |

`SchoolPost.json` 是**按 area（州）分表**的（`ConfSchoolPost.itemsPostArea`），
所以不同州同名职位的名额、俸禄、境界要求可以不同 —— 读的时候必须带上本宗所在州。

**名额上限**：`ConfSchoolInitScaleItem` 的
`schoolMain / bigElders / elders / inherit / inner / outer`（例：`1/2/5/10/12/25`）。

### 1.6 现成 API 一览（不用自己拼字符串）

| 需求 | API | RVA |
|---|---|---|
| 某人在本宗是什么职位（中文） | `MapBuildSchoolData.GetPostTypeName(unitID)` | `0x35E8C10` |
| 某职位的中文 | `ConfSchoolPost.GetPostName(SchoolPostType)` | — |
| 部门中文 | `ConfSchoolDepartment.GetDepartmentName(int)` | — |
| 功法偏好中文 | `ConfRoleEffect.GetValueName(basType)` | `0x62442B0` |
| 某人职位枚举 | `SchoolData.GetPostType(unitID)` | `0x2A49180` |
| 某职位的全部在任者 | `SchoolData.GetPostUnit(SchoolPostType)` | `0x2A49300` |
| 本宗某部门的长老 | `PostData.GetElders(SchoolDepartmentType)` | `0x2A45D30` |
| 某人是哪个大长老的手下 | `PostData.GetBigElders(unitID)` | `0x2A45C40` |
| 宗门战力 | `SchoolData.GetSchoolPower()` | — |
| 与玩家交情 / 与他宗交情 | `MapBuildSchoolData.GetSchoolIntim(unit)` / `intimToSchool` | — |

`GetPostTypeName` 的反编链条（`0x35E8C10`）：

```
this.school.buildData.GetPostType(unitID)   → SchoolPostType
  → 0 = None → 返回空
  → g.conf.schoolPost.GetItem(post).postName → GameTool.LS(...)  → 中文
```

### 1.7 分舵命名与“州”

`MapBuildSchool.get_name`（RVA `0x35F7FD0`）不是返回字段，而是现拼：

```
rdi = nameOrigin
if (GetTopSchool() != this) rdi = 主宗的 name
return String.Concat(rdi, branchName)
```

`get_branchName`（RVA `0x35F7830`）在分舵分支上走
`GameTool.StrPar(格式串, buildData.propertyData 里的串)`，兜底取
`ConfWorldAreaBase::GetAreaName(gridData.areaBaseID)` —— 即**分舵名里带生成时所在的州名**。

真机样本（`docs/archive/journal/2026-09-10.md`）：

```
areaName2·丹阳谷永宁州分舵（谷）—隶属丹阳谷，宗主濮阳坻，弟子69人，声望711
```

⇒ 名字里的“永宁州”是**生成时快照**。而 `SchoolRelocate` / `MapBuildSchoolMainHall.schoolRelocate`
是实装功能（整宗搬迁不改名）。**所以“在某州”一律以 `gridData.areaBaseID` 解析出的
实际位置为准，绝不解析名字里的州名。**

## 2 信息查询（本期实现）

### 2.1 现状与三处问题

`QueryWorldSects`（`csharp/tools/ToolExecutor.cs:1107`）现有行为：

1. `:1115` `if (!byName && !RegionOk(bi, region)) continue;` —— **给了 `name` 就整条跳过 region 过滤**
2. `:1157` `anchor = topOf[...]` —— 命中项一律换成主宗
3. `:1169` 起按 `top == anchor` 收回全族

后果：`name=丹阳谷` → 全族（这是想要的）；`name=丹阳谷永宁州分舵` → **还是全族**（这是 bug）；
`name=丹阳谷 region=永宁州` → region 被无视，还是全族。

同时人事只取了 `totalMember` 与 `npcSchoolMain`，`npcBigElders / npcElders / npcInherit /
postData / basTypes` 一个都没读 —— 所以只有“宗主 + 总人数”。

### 2.2 查询粒度（`region` 参与匹配）

**不新增参数**：`region` 已经存在，只是被短路了。三档语义：

| 传参 | 语义 | 与现状 |
|---|---|---|
| `name` 空 | 按 region 过滤（给了就收窄） | 不变 |
| `name` 给、`region` 空 | **族系**：主宗 + 全部跨州分舵 | 不变 |
| `name` + `region` 都给 | **该州内的命中项**，不跨州展开 | 新增 |
| `name` 是分舵**全名** | 只这一条，不扩族 | 新增（修掉换锚点） |

判定顺序：

```
候选集 = region 非空 ? 所有该州建物 : 所有建物
命中   = Name 精确相等                      → self（只这一条）
       → 否则 mainName / nameOrigin 精确相等 → family（锚定主宗，收回全族）
       → 都未中                            → 空表 + 说明
region 非空时：命中后**不再扩族**，只留候选集内那些（= 该州的族系成员）
```

`region` 匹配沿用 `RegionOk`（`Region.Contains`，与 `places`、`ResolveBuild` 同一口径），
所以“永宁”也能中“永宁州”。

结果里补三个字段，让模型知道被收窄了、还能放宽：

```json
{ "scope": "state", "family_total": 3, "note": "已按 region 收窄到1处；同族另有2处（水宁州、云陌州）" }
```

`scope` 取值：`all` / `region` / `family` / `self` / `state`。

**`topic=places` 不动**：它的 `name` 忽略 region 是**有用**的（查“丹阳谷”要把各州分舵
全列出来当 travel 目标）。只有 `sects` 收窄。

### 2.3 `detail` 分层

不给 `detail` = 现状（一行一宗，24 宗也不炸上下文）。

| `detail` | 追加内容 | 建议用法 |
|---|---|---|
| `info` | 功法偏好、人数细分、灵石/信仰/声望、战力、名额、战绩、交情 | 问“这宗门怎么样” |
| `people` | 人事树（宗主→大长老→长老→真传） | 问“谁是谁/谁是长老/谁的真传” |
| `all` | 两者合并 | 只查 1~2 宗时 |

`detail` 对多宗也接受，但 24 宗 × 人事会很长 —— schema 描述里点明“只对单宗/少数宗用”。

### 2.4 字段清单

**`detail=info` 追加**

| JSON | 来源 | 缺失时 |
|---|---|---|
| `bas_types: ["剑法资质", …]` | `schoolData.basTypes` → `GetValueName` | 退 `buildSchoolData.basTypes`；再退固定 12 表 |
| `member: {total, big_elders, elders, inherit, inner, outer}` | `totalMember` + 各 List.Count | 缺项不写 |
| `money` / `faith_money` | `money` / `faithMoney` | 不写 |

| `power` | `GetSchoolPower()` | 不写 |
| `capacity: {main, big_elders, elders, inherit, inner, outer}` | `initScaleItem` | 不写 |
| `is_small` | `IsSchoolSmall()` | 不写 |
| `honor: {score, rank}` | `honorData.schoolScore / rank` | 不写 |
| `intim: [{name, value}]` | `intimToSchool`（只列非零，最多 5 条） | 不写 |

**`detail=people` 追加**

```json
"people": {
  "main":  {"name":"濮阳坻","realm":"金丹后期"},                       // 代理时多一个 "agent"
  "big_elders": [{"name":"玄阳子","realm":"元婴初期","depts":["招贤堂","巡查堂"]}],
  "elders": [
    {"dept":"招贤堂","name":"林清","realm":"金丹中期",
     "inherit":[{"name":"苏小满","realm":"筑基后期"}],
     "inner_count":12,"outer_count":25},
    {"dept":"巡查堂","vacant":true}                                    // 该堂无长老
  ],
  "elders_flat":  [...],   // 仅降级时出现（无部门归属）
  "inherit_flat": [...]    // 仅降级时出现（无师徒归属）
}
```

降级说明写在**宗门那一层**的 `people_note`（不是 people 内部），名额上限写在
宗门那一层的 `capacity`（`detail=info` 与 `people` 都会带）。

人名解析：`g.world.unit.GetUnit(id)` → `unitData.propertyData.GetName()`；
查不到活人 → `name` 留空、`status` 写 `已故`（`unitDieMark`）/`离宗`（`unitExitMark`）/`未在世`。
境界走 `UnitSnapshot.RealmText(u)`（与面板同口径，勿回退 `GetGradeName(gradeID)`）。

### 2.5 降级策略（这是“不做探针”的兑现方式）

每一层独立 `try/catch`，读不到就**不渲染该段**，绝不整条崩：

1. **人事**：先读 `postData`（有部门/师徒关系）；`postData` 空或异常 → 退扁平
   `npcSchoolMain / npcBigElders / npcElders / npcInherit`（没有部门与师徒连线，但“谁是
   大长老/长老/真传”照样对），并在 `people.note` 注明走了降级。
2. **功法偏好**：`schoolData.basTypes` → 空则 `schoolData.buildSchoolData.basTypes` →
   仍空则整段不出现。元素转中文：`GetValueName` 命中用之；未命中就把元素原样输出 ——
   万一游戏哪天直接存了中文，原样输出也是对的（自愈）。
3. **职位名**：`GetPostTypeName(unitID)` 优先；异常退 `ConfSchoolPost.GetPostName(post)`;
   再退枚举硬映射（宗主/大长老/长老/真传弟子）。
4. **人数细分**：各 `List.Count` 单列读，任一失败只少一个数。

### 2.6 渲染

`tools/text_render.py::_qw_sects`。不给 `detail` 时**逐字节保持现有格式**（现有测试锁着）。

`detail=info`：在现有那一行之后追加子行：

```
· 永宁州·丹阳谷永宁州分舵（谷）｜层级：分宗，隶属丹阳谷｜状况：被占据（持有方天魔宗）｜宗主濮阳坻，弟子69人，声望711
  功法偏好：剑法资质、枪法资质、火灵根、水灵根
  人数：大长老2/2、长老5/5、真传2/10、内门12、外门25，共69人
  经济：灵石12000，信仰3500，声望711｜战力8600
  战绩：宗门大比 3 分，第 17 名
  交情：与本宗25、天魔宗-40
```

`detail=people`：

```
  宗主：濮阳坻（金丹后期）
  大长老（2）：
    · 玄阳子（元婴初期）｜辖招贤堂、巡查堂
    · 白芷（金丹后期）
  长老（2）：
    · 招贤堂 林清（金丹中期）｜真传：苏小满（筑基后期）；内门12人；外门25人
    · 巡查堂：空缺
```

`capacity` 有就写成 `在任/上限`（`长老2/2`），没有就只有 `在任`。
`detail=all` 时 `info` 段在前、`people` 段在后，实机样例见
`docs/features/tool-result-contract.md` §3.4。

### 2.7 验收方式（为什么不开探针）

上一版计划把 4 件事列为“必须真机探针”，复核后**全部可以离线定**，因此本期不开探针，
用**正常玩一次 + 看一次工具结果**当验收：

| 曾经的“未知” | 离线结论 | 依据 |
|---|---|---|
| `postData` 是否为空 | 构造即建，非空有保证 | `SchoolData..ctor`（`0x2A4B7B0`）显式 `new PostData.PostMain()` |
| 泛型字典能不能遍历 | 不需要遍历 | `PostData.GetElders` = `TryGetValue` 包装 |
| 面板读哪一份数据 | 与我们要读的是同一份 | `UISchoolNewAdmin.GetStudentMax(Dictionary<SchoolDepartmentType, PostData.PostElders>, …)` 形参即 `postData.postElders` |
| `basTypes` 存键还是中文 | 存键 | `GetIcon(string key)` 被直接喂元素；键集 = `RoleAttributeDesc` 的 12 条 |
| 12 项中文名 | 已定 | `LocalText.json` → `modEffectType1Name_bas*` |
| 职位 / 部门中文名 | 已定 | `LocalText.json` → `schoolPostName1~7` / `SchoolDepartmentName1~5` |
| 分舵名是否带州名 | 带，且是生成时快照 | `get_name` / `get_branchName` 反编 + 真机样本 |
| 同族同州会不会两支 | 结构上是“各州一舵” | `GetSameMyNameSchoolFromNextArea()` 的存在；真遇到就返回多条，靠全名区分 |

**首跑核对清单**（真出问题按这个定位，不再单独跑探针）：

1. `[Sect]` 日志是否有 `postData` 读取失败的行；
2. 功法偏好是否出现 12 项之外的怪值；
3. 人事是否走了降级（宗门那层的 `people_note` 会写）；
4. `region` 收窄后的条数是否等于该州实际分舵数。

## 3 后续（占位，不在本期）

| 条目 | 状态 |
|---|---|
| **职位晋升 / 降级**（外门→内门→真传→长老→大长老） | **已预研，设计见 §7**（未实现） |
| 长老/大长老的擂台战（`SpecialBattle` battle 24 / 25） | 未开始（建议与晋升分开做） |
| 玩家自己当宗主的宗门招人（`UnitActionRoleRoleSchoolRecruit` 的 PlayerToNPCAction 支） | 未开始 |
| 宗门任务（`SchoolTask*`）与战功（`SchoolFight*`） | 未开始 |
| 宗门搬迁（`SchoolRelocate`）现状查询 | 未开始 |
| 宗门资产明细（药园/矿脉/库房） | 未开始 |
| `capacity` 补齐（用 `ConfSchoolPost.GetPostNumMax`，见 §7.3） | 未开始（约 10 行） |

## 4 附录：实证索引

| 项 | 位置 |
|---|---|
| 类型/字段清单 | `F:\DecompDump\dump\sect\`（ICSharpCode.Decompiler 批量导出，脚本 `F:\DecompDump\Program.cs`） |
| 符号表 | `/home/zyh/work/dt/rvamap_all.txt` |
| 反汇编 | `/home/zyh/work/rev/one.py <RVA>` |
| 反射（枚举/签名） | `reference/il2cpp/dump_types.ps1` |
| 配置表（离线权威） | `鬼谷八荒/Mod/modFQA/配置修改教程/配置（只读）Json格式/` |
| 中文表 | 同目录 `LocalText.json`（54991 条 `key→ch`） |
| 旧真机样本 | `docs/archive/journal/2026-09-10.md` §sects 真机数据 |

## 5 实现落点（10-29）

| 文件 | 改动 |
|---|---|
| `csharp/tools/ToolExecutor.cs` | `QueryWorldSects(args, region, name)` 重写：region 参与匹配、三档 `scope`、`detail` 分层；新增 `ReadStrList` / `ReadEnumList` / `SectBasTypesOf` / `SectDeptName` / `SectPostCn` / `SectPersonInto` / `AppendSectInfo` / `SectPeopleTree` |
| `tools/schemas.py` | 新增 `detail`（enum info/people/all）；改写 `region` / `name` / `topic` 的语义描述 |
| `tools/text_render.py` | `_qw_sects` 收尾接 `_sect_detail_lines`；新增 `_person` / `_sect_people_lines` / `_sect_detail_lines` |
| `tests/test_sect_query_contract.py` | 新增 15 项契约（三档粒度 / 12 条功法偏好含 basFroze=水灵根 / postData+降级 / 禁 Il2Cpp foreach / 渲染格式） |
| `tests/test_tool_contract_consistency.py` | `CS_METHODS` 里 `QueryWorldSects` 的签名同步（`args` 提到第一位） |
| `docs/features/tool-result-contract.md` | §3.4 补查询粒度 / detail 分层 / 两个渲染样例 |

**没做**：`topic=places` 的 `name` 仍然忽略 `region`（目录用途，按名跨州全列是刻意的）；
内门/外门名单；入宗/晋升/任务/战功（§3）。

## 6 宗门人事：NPC 自主入宗 / 退宗（10-29 实现）

### 6.0 需求原话

> 「我需要 npc 可以实现自由的加宗门和退出宗门，当然是需要门槛的，但是我不想要门槛设置的是死的，
> 所以决定权在 npc 上面，npc 自由决定是否加宗门还是退出，就是需要一个参数，作为加入或者退出
> 宗门的理由，然后你在这个参数描述的时候需要让 npc 自己考虑是否加入还是退出」
>
> 「不是简单的加入宗门变为外门弟子就行了，还需要可以成为某某的真传弟子这样的功能，以及成为内门
> 弟子，当玩家要求 npc 加入某个宗门的时候，应该反问玩家是成为外门弟子还是内门还是谁的真传弟子」

⇒ 三条硬要求：**① 门槛不写死；② 一个"理由"参数，且它的描述要让 NPC 自己权衡；③ 没定身份时反问。**

### 6.1 引擎侧事实（全部反编实证）

| 事项 | 结论 | 出处 |
|---|---|---|
| 加入动作 | `new UnitActionSchoolJoin(string schoolID, SchoolPostType type, SchoolDepartmentType dept = None)` | `0x37C9B30` |
| **能不能直接以任意职位入** | **能**。33 个构造点的 `type` 实参覆盖 1~6 全部（宗主/大长老/长老/**真传**/**内门**/外门）；其中外门 21 处、内门 2 处、真传 1 处 | `scan4.py 37C9B30` |
| ~~`schoolID` 用什么~~ → **用 `buildData.id`** | ~~`schoolNameID`~~ **已证伪（11-10 真机 + 字段级反编）**：`schoolNameID` 是**算出来的宗名族 id**（getter 读 `schoolData` → `ConfSchoolNameBase.GetItem` → `Int32.ToString()` 拼串），不是 build 的键；传它进去 `WorldBuildMgr.GetBuild` 返回 null ⇒ 裸 `NullReferenceException`。`BuildDataBase` 的字段表写明 `0x18 = public string id`；33 处构造点中 32 处是 `mov rXX,[buildData+0x18]`，余 1 处是 Lua 桥（脚本给 id），**没有一处用 schoolNameID** | 字段表：`F:\DecompDump\dump\builddata\`；`0x37CA800`（InitData）+ `0x47AD9B0`（GetBuild）+ 全 .text 33 处构造点扫描 |
| 加入的门槛 | ①`UnitActionBase::IsCreate`（月结算/日耗）②`GetGrade() > ConfWorldAreaBaseItem.gradeMax`（偏移 `0x28`，同结构 `gradeMin@0x24`）③宗门交情 `GetSchoolIntim` ④`IsHerdNPC` / `GetFiveFlowerDramaNpcID`（返回码 12） | `0x37C8250` |
| 加入的**副作用** | `CostPropItem`（扣引荐之物）→ `AddIntim`（加好感）→ `GetPostUnit`+`List::Remove`（离旧职）→ 内建 `UnitActionSchoolSetPostType` 落职位 → 写宗门日志/剧情 | `0x37C9100` |
| 职位是 (post, dept) 二元组 | `UnitActionSchoolSetPostType(schoolID, post, dept)` + 6 个专用重载；**`InheritData` 只带 `departmentType`** ⇒ 真传是"挂在某堂长老名下"，不能随手指一个非长老的人 | `0x37CCFA0` 等 |
| 退宗动作 | `new UnitActionSchoolExit()` **无参**，`Init` 自己从 unit 推出 school/post/dept | `0x37C6480` |
| 退宗门槛 | **没有**。`IsCreate` 通篇只调 `UnitActionBase::IsCreate`；`OnCreate` 只 `ClearUnitExitMark/DieMark` + 写日志 | `0x37C6560` / `0x37C65C0` |
| 游戏自己也在让 NPC 自己决定 | `SchoolPostParam`：`outExitSchoolWeight 65` / `outDieSchoolWeight 35`、`outExitSchoolMinMonth 1` / `MaxMonth 12`、`outNPCAddRate1/2/3`、`inheritMaxUnitCount 7`、`inheritNotAgreeJoin 6373601` | 配置表 |

> **为什么绝不自己改 `postData`**：上面那串副作用（扣道具/加好感/离旧职/日志/剧情/缓存
> `unitIDToSchoolPostType`）全在 `OnCreate` 里。直接塞 ID 会漏掉整套 ⇒ 名单里有他、但没人当他是弟子。

### 6.2 门槛怎么"交给 NPC"（不写死任何数字）

| 层 | 做法 |
|---|---|
| 事前 | 只给**可查事实**：该宗在任人数（外门/内门/真传）、五堂长老名单与各自名下真传数；身份没定就把这些当"问玩家的素材" |
| 事中 | **不预拦**（除已在本宗、目标宗门不存在、人已死）。真调 `CreateAction`，引擎拒就照实回报 |
| 拒绝码 | 已全部逐条反编成码表：见 §6.9 ④（`1` 日耗、`2` 引擎认为他还有宗门、`3` `IsHerdNPC`、`4` 内层职位/名额被折叠、`7` 退宗冷却、`8` 他**自己的行事安排**未到期、`11` 动作没初始化、`12` 五花生灵）。真机已证 `7` |
| 事后 | **读回验证**：`npc.data.school` + `buildData.GetPostType(unitId)`。没落地就报 `pending`（"引擎已受理，名册下一拍才更新"），**绝不当失败说** |

> **名额上限没有展示，也没有预拦** —— 这是刻意的。候选数字有两处
> （`ConfSchoolPost.numMax` 与 `SchoolInitScale.inherit/inner/outer`，永宁州真传一处写 2、一处写 10），
> 而 `UISchoolNewAdmin.GetStudentMax` 反编后实际返回的是**当前人数**（读 `manageIn.Count` / `manageOut.Count`），
> 不是上限。数字定不下来就不写 —— 满没满交给引擎的 `SetPostType.IsCreate` 裁决。

### 6.3 参数（`world_ai_action` 扩两个 op）

| 参数 | 适用 | 说明 |
|---|---|---|
| `op` | — | 新增 `join_sect`（你自己入宗门）、`leave_sect`（你自己退出宗门） |
| `sect` | join | 宗门名；同名分宗用 `region` 消歧 |
| `region` | join 可选 | 州名，仅用于消歧 |
| `post` | join | `out` 外门 / `in` 内门 / `inherit` 真传（**留空 ⇒ 反问**） |
| `master` | join + inherit | 拜哪一堂的长老（填真名），内部映射到该长老所在部门 |
| `reason` | **join / leave 必填** | 交给 NPC 自己权衡的动机；描述里写明"玩家只是提议，去不去、走不走由你定" |

`target` **不参与**这两条 op（actor 恒为当前对话 NPC）——schema 里已写清。

### 6.4 反问的两种形态（都不动手）

1. **没给 `post`** → 回 `{ask:true, roster:{out,in,inherit,elders[]}, question:"…要先问清玩家：是入外门、入内门，还是拜在哪一堂的长老门下做真传弟子？"}`
2. **给了 `inherit` 却没给 `master`** → 回 `{ask:true, roster:…, question:"…要先问清玩家：拜在哪一堂的长老门下？"}`

渲染成一句"（系统：这一调没有动手 —— …）" + 该宗在任人数 + 可选师父清单，NPC 照着问玩家，
拿到答复再调一次。**不靠提示词约束"记得先问"** —— 模型十次里有九次会直接默认填外门。

### 6.5 我们拦的两种情况（都会造成乱子）

| 情况 | 处理 | 理由 |
|---|---|---|
| 已有宗门，想入别家 | 拒绝，提示"先退宗（退不退也由他自己判断）" | 一个人只有一个 `schoolID` |
| 宗主 / 大长老退宗 | 拒绝，说明"一退就空位、要牵继承" | `heritData` / `mainUpGrade` 是另一套流程，本期不碰 |

长老（post 3）及以下**可以退**，引擎本来就允许。

### 6.6 未做 / 风险

- **真传"师父同不同意"没有模拟**：引擎 `InheritData` 只按部门指派；游戏自己的月度流程里有
  `inheritNotAgreeJoin`（长老拒收）的剧情。为避免自造门槛，本期直接由门派归属决定师父，
  只在反问素材里给出"该长老门下已有 N 人"让 NPC 自己掂量。
- **引荐之物**（`GetJoinProps`）没有显示：扣不扣、够不够由引擎 `CostPropItem` 决定，
  我们不预判、也不替 NPC 备货。
- **玩家当宗主的宗门招人**（`UnitActionRoleRoleSchoolRecruit`）没接。
- 真机未验：首次真跑要看 `Player.log` 里 `CreateAction` 的返回码分布，以及读回是否立刻可见。

### 6.7 真机首跑核对清单（还没跑过，先记在这里）

| # | 要看什么 | 怎么判 |
|---|---|---|
| 1 | **`schoolNameID` 能不能当 `schoolID`** | 这是全链唯一"靠交叉引用推断、没直接跑过"的点（`curHoldSchoolNameID`/`enemySchoolID` 都用它）。若引擎不认，`CreateAction` 会返回非 0 或动作静默不动 —— 换 `GetBuild` 的建物 id 再试 |
| 2 | `CreateAction` 返回码分布 | `1`=通用闸（多半月结算）`5`=州境界/交情 `12`=特殊剧情角色。若大量 `5`，说明门槛比预想严，考虑在反问素材里补该州 `gradeMax` |
| 3 | `joined:true` 与 `pending` 的比例 | 全 `pending` ⇒ 引擎是下一拍才改名单，文案要改成"稍后确认"；全 `true` ⇒ 立刻可见 |
| 4 | 入完 `GetPostType` 是不是 4/5/6 | 若内门/真传落成外门，说明 `dept=0` 那条分支没落职位（`Join.Init` 里 `dept==0 && post==3` 是特殊分支，4/5/6 会内建 `SetPostType`） |

### 6.8 实现落点

| 文件 | 改动 |
|---|---|
| `csharp/tools/ToolExecutor.cs` | `WorldAiAction` 在 `ResolveCounterparty` **之前**分流两个 op；新增 `SectJoin` / `SectLeave` / `SectRoster` / `ResolveSchoolByName` / `SectResolveMasterDept` / `SectPostOf` / `SectActionRefuseText` |
| `tools/schemas.py` | `op` 加两个值；新增 `sect` / `region` / `post` / `master` / `reason`；改 `target`、`op` 的描述 |
| `tools/text_render.py` | 新增 `_render_sect_membership`（ask / 已落地 / 已受理 三形态），`render_world_ai` 前置分流 |
| `tests/test_sect_membership_contract.py` | 新增 15 项（分发位置 / 引擎三件事 / 不改 postData / 三档身份 / 反问 / 拒因不夸大 / 退宗拦截 / 渲染三形态） |
| `tests/test_tool_contract_consistency.py` | `CS_METHODS["world_ai_action"]` 补两个新方法签名 |
| `tests/test_tool_usage_contract.py` | 散文水位 5340 → 6300（用户拍板：工具增多，散文必然增加） |

### 6.9 真机首跑记录（11-10，两起失败 + 一处纠正）

#### ① 论道（`lun_dao`）"莫名其妙失败"——**不是 bug，是引擎的余暇闸**

```
15:26:25 [AiAction] lun_dao 完成回调 ok=False slot=1 同步=True
15:26:25 RPC call_tool 返回失败：lun_dao没能生效：引擎没有接受这次互动动作…
```

同步回调 `ok=false` 就是「动作创建被引擎拒」。反编早定过案（10-23，`UnitActionRoleDiscovery::
IsCreate`）：门槛是 `IsDayCost(conf.roleDiscovery.costDay = 5)`，比的是**这个 NPC** 的
`unit.data.unitData.residueDay`。当时姜萌的余暇被传送/日常消耗扣到 < 5，所以拒 —— 与"她不肯"无关。

**本轮改进**：失败文案里补上**真实余暇数字**（`他此刻的当月行动余暇：N 天`），
玩家不用再对着"常见原因"猜。（`op == lun_dao` 才加，别的挂起动作不加。）

#### ② 入宗（`join_sect`）连续 `NullReferenceException` —— **我们传错了 id**

```
15:47:34 tool_result: {"success": false, "error": "发起入门失败: System.NullReferenceException…"}
15:47:42 同上（第二次）
```

对照组：**同一晚 `leave_sect` 成功**（`你已退出「锦霄派永宁州分舵」，如今是散修。`）⇒
`CreateAction` 链路本身没问题，问题只在 `UnitActionSchoolJoin`。

反编追到底：`Init` @0x37C7FE0 建 `UnitActionSchoolSetPostType` → `InitData` @0x37CA800 →
`WorldBuildMgr::GetBuild(string)` @0x47AD9B0。`InitData` 里紧跟其后的一串
`test rax,rax / je <NRE>` 就是空引用点 —— **`GetBuild` 拿不到 build 就抛裸 NRE**（无文案）。

**字段级证据（11-10 补做，不是推断；用 `F:\DecompDump` 的 ICSharpCode 反编出字段表）**：

```
BuildDataBase（0x200121E）             DataBuildBase.BuildData（0x2002753）
  0x10  string[] points                  0x50  Dictionary<string,BuildSubData> allBuildSub
  0x18  string id      ← 就是它          0x58  DataObjectData objData
  0x20  string[] values                （自己的字段从 0x50 起 ⇒ 0x18 属于基类 BuildDataBase 的 `id`）

MapBuildBase（0x200195D）              MapBuildSchool：`new buildData` getter = 0x35F7AC0
  0x10  Vector2Int[] points             → 读 [this+0x20] 后强转 SchoolData
  0x18  DataGrid.GridData gridData
  0x20  BuildData buildData   ← 就是它
```

⇒ 全 .text 穷扫 `call UnitActionSchoolJoin::.ctor`（rel32）共 **33 处**（与早前 `scan4.py` 的
计数一致）。其中 **32 处**是
`call 0x35F7AC0(buildData) → mov rXX,[rax+0x18] → rdx`（`rbx`/`r14`/`r15`/`rsi` 中转），
即实参 `schoolID = buildData.id`；剩下 1 处（0x26C59CE）是 **Lua 桥**（
`lua_tostring(index 1)` 的返回值当实参，由脚本给 id）。
**没有一处用 `schoolNameID`。**

而 `MapBuildSchool.schoolNameID`（getter @0x35F80C0）读的是 `[this+0x48]`= `schoolData` →
`ConfSchoolNameBase::GetItem` → **`Int32.ToString()`** 把两个 `ConfSchoolName` 数字 id 拼成字符串，
是**宗名族 id**，与 build 的 `id` 不同命名空间 —— 拿它去 `GetBuild` 只能得到 null。

**修法（`ResolveJoinSchoolId`）**：候选 `buildData.id` → `schoolNameID` → `curHoldSchoolNameID`
逐个用**同一把** `world.build.GetBuild(id)` 验，**指针等于本对象**才算命中；一个都不中就交第一条
出去（让引擎裁决）并把候选写进日志。发起的动作有异常时，**完整栈进 `Player.log`**
（`[Sect] 发起入门异常（schoolID=… 宗门=…）：…`）——上次只回了 `e.Message`，白猜一轮。

#### ③ 部署与复测

| | |
|---|---|
| DLL | 11-10 **15:54**，1105408 B，md5 `4cc70ad7…`，备份 `MOD_Jgmg5L.dll.bak_1110_1556_before_schoolid` |
| 测试 | 24 个 C# 测试工程全过；宗门/工具契约 42 项全过 |

复测时看这三行（`Player.log`）：

```
[Sect] 入宗 schoolID 命中 buildData.id=…
[Sect] 入宗发起：宗门=… schoolID=… post=外门弟子 dept=0
[Sect] 发起入门异常（…）：…（只有失败才出现，且带栈）
```

若 `命中` 那行显示的是 `schoolNameID` 或"候选都没通过"，说明该 build 的键不是这两种，
把这两行发我即可定位。

#### ④ 首跑结果（17:00，真机）：id 修复成立，**返回码 7 = 退宗冷却未满**

```
[Sect] 入宗 schoolID 命中 buildData.id=SRL471          ← §6.1 的结论被真机证实
[Sect] 入宗发起：宗门=锦霄派永宁州分舵 schoolID=SRL471 post=外门弟子 dept=0
→ 引擎拒绝了入门（返回码 7）           （同一分钟第二次重试，还是 7）

[Sect] 入宗 schoolID 命中 buildData.id=6qlZMM
[Sect] 入宗发起：宗门=观莲教 schoolID=6qlZMM post=外门弟子 dept=0
→ 你已入「观莲教」，身份是外门弟子（现属观莲教）    ← **真正落地了**
```

同一人、同一套代码：**刚退出的那个宗吃 7，没退过的宗一次成功**。这就把 7 的含义钉死了。

反编对照（`UnitActionSchoolJoin::IsCreate` @0x37C8BB0 起的循环）：

```
loop:  rax = school.buildData            (MapBuildSchool.buildData @+0x20)
       rax = [rax + 0xD0]                ; SchoolData.exitSchoolUnit（字段表 0xD0，List<string>）
       entry = 该表第 i 条               ; "unitID|离宗月"
       parts = entry.Split('|')
       若 parts[?] == 该 unit 的 id：
           ebp = 当前月 ; r14d = parts[中的月]
           cmp (ebp - r14d), 要求月数
           jl → AddTip(...) ; **mov eax,7 ; ret**     ← 冷却没满就返回 7
```

配套配置：`SchoolPostParam.outExitSchoolMinMonth 1 / MaxMonth 12`。实机时间也对得上 ——
15:28 退宗、17:00 再入，游戏内还没过那个月。

**本轮改动**：把 7 写进 `SectActionRefuseText`（模型以后不会再说"这宗不收我"这种含糊话，而是
直接告诉玩家"刚退过、冷却没过，换个宗门或等过月"）。

`IsCreate` 的完整码表（已逐条落到字段/调用点，**没有一条是猜的**）：

| 码 | 事实（证据） |
|---|---|
| 1 | `UnitActionBase::IsCreate` → `IsDayCost(day)`：换日/月结算/日耗不足 |
| 2 | join：`unitData.schoolID`（`DataUnit.UnitInfoData` **@0x30**）`!= 空` ⇒ 引擎仍当他有宗门（`String::op_Inequality` @0x37C8403）<br>leave：`UnitActionSchoolExit::IsCreate` @0x37C6597 `cmovne ecx,2`，谓词为假即 2 |
| 3 | `BigFightExtensions::IsHerdNPC()` 为真（@0x37C841D → `eax=3`） |
| 4 | 内层 `UnitActionSchoolSetPostType::IsCreate` 返回非 0 ⇒ 折叠成 **4**（@0x37C8445 → `eax=4`） |
| 5 | 只在 Lua 桥那条构造路径（脚本给 id）；我们的链路碰不到 |
| 7 | 目标宗 `buildData.exitSchoolUnit`（`SchoolData` **@0xD0**）里记着此人、离宗月数不够 |
| 8 | `WorldUnitAIMgr::GetUnitAI(unit)` 的字符串与宗 `curHoldSchoolNameID` 对得上且未到期（@0x37C8EF5 循环） |
| 11 | `setPostType` 缺失（`[action+0x48]==0`，@0x37C83C8 → `eax=11`）⇒ 动作没初始化 |
| 12 | `ConfDramaNpc::GetFiveFlowerDramaNpcID` 命中（@0x37C846D → `eax=0xc`） |

内层 `SetPostType.IsCreate` 自己的码（**到我们手上会被折叠成 4，只能用于写说明**）：
`3`、`7`（`bigEldersData` 槽已占）、`8`（`eldersData` 槽已占）、`9`（名额比较）、`11`、`12`。

#### ⑤ 部署（17:36 更新）

| | |
|---|---|
| DLL | 11-10 **17:36**，1106432 B，md5 `e43ddeed…`，备份 `MOD_Jgmg5L.dll.bak_1110_1736_before_codetable` |
| 内容 | ① id 修复（15:54，已真机验证：观莲教入宗成功）；② 码表全部落成人话（1/2/3/4/7/8/11/12） |

码表落地后，模型不会再自己编"这宗不收我"这类话：每种拒绝都会带上原因与下一步该怎么做。

## 7 待开发：宗门职位晋升（10-31 预研，**未实现**）

> 本章只有调研结论与待拍板项，代码一行没动。目标是让"入宗"这条链能往下走：
> 外门 → 内门 → 真传 → 长老 → 大长老。

### 7.1 引擎动作

```csharp
new UnitActionSchoolSetPostType(string schoolID, SchoolPostType post, SchoolDepartmentType dept)
new UnitActionSchoolSetPostType(schoolID, new InData(dept))       // 内门
new UnitActionSchoolSetPostType(schoolID, new InheritData(dept))  // 真传（只吃部门，不吃长老 ID）
new UnitActionSchoolSetPostType(schoolID, new OutData(dept))      // 外门
new UnitActionSchoolSetPostType(schoolID, new EldersData(dept))   // 长老
new UnitActionSchoolSetPostType(schoolID, new BigEldersData(postBigElders))
new UnitActionSchoolSetPostType(schoolID, new SchoolMainData())
```

CTOR RVA `0x37CCFA0` 等 7 个；`InitData@0x37CA800`；`IsCreate@0x37CABD0`；`OnCreate@0x37CBCF0`。

### 7.2 `IsCreate` 几乎没有门槛（重要）

`0x37CABD0` 反编：除 `UnitActionBase::IsCreate`（月结算/日耗）与"特殊剧情角色 → 返回码 12"之外，
**没有任何名额/境界/资质判断**。中间那段只在 `unit.schoolID != 目标 school` 时打一条
`Debug.LogWarning`（警告而已，不阻断）。

⇒ 名额与资质的门槛**不在 IsCreate**，而在：

| 位置 | 干什么 |
|---|---|
| `OnCreate` → `MaxUnitClearUnit@0x37CB8B0` | 超员时**挤人**（已核控制流）：`GetPostNumMax(...)` 取上限 → `cmp [list+0x18](Count), eax` → `jle` 放行；超了循环从名单里取人，走 `UnitActionSchoolSetPostType::InitData` **改掉他的职位**（`GetManagerUnit` 找该部门管事） |
| UI 侧 `UISchoolUpPostSelect` / `UISchoolSelectPostNPC` | 玩家晋升的选人与资格提示 |
| `MapBuildSchoolPostBase.PostBattle(...)` + `MatchFight` | 长老/大长老的**擂台战**（见 7.4） |

★ 结论：**不能无脑调 `SetPostType` 晋升** —— 会挤掉别人。必须事前按 `GetPostNumMax` 拦住。

### 7.3 名额 API 找到了（顺带纠正 §6.2 的"定不下来"）

```csharp
int ConfSchoolPost.GetPostNumMax(MapBuildSchool school, int post)
int ConfSchoolPost.GetPostNumMax(int post, int department)     // ← 弟子类是"每部门"名额
```

对上了：`SchoolPost.json`(area=2) 的 `numMax` × 部门数 = `SchoolInitScale` 的总数

| 职位 | numMax（每部门/每宗） | ×5 部门 | SchoolInitScale / SchoolPostParam |
|---|---|---|---|
| 宗主 | 1 | — | `schoolMain 1` ✔ |
| 大长老 | 2 | — | `bigElders 2` ✔ |
| 长老 | 5 | — | `elders 5` ✔ |
| 真传 | 2 | **10** | `inherit 10` ✔ |
| 内门 | 3 | **15** | `inMaxUnitCount 15` ✔ |
| 外门 | 5 | **25** | `outer 25` ✔ |

> 这也解释了为什么 `PostElders` 的 `manageInherit/manageIn/manageOut` 是**每个部门各一份列表**。

**顺带的既有缺口**：§2 的 `capacity` 当时没写（找不到权威来源）。现在有了
`GetPostNumMax` —— 这是个约 10 行的补丁，可让 `detail=info` 显示"真传 2/10"这样的真实名额。
（`inheritMaxUnitCount 7` 与"每部门 2 人 × 5 = 10"不一致，7 的语义仍未定 —— 若要用，先查。）

### 7.4 两条晋升路径（配置表实证）

`SchoolPostParam.json` 逐职位：

| 职位 | 起始月 | 名额 | 战斗 | 剧情链 |
|---|---|---|---|---|
| 外门 out | 1 | 15（`outNPCMaxCount`） | 无 | `outJoinDrama 6371101` → `outNotBattleDrama 6371201` → `outStartBattleDrama 6371301` → `outJoinCompleteDrama 6371401` / `outJoinFailDrama 6371501`，`outMaxJoinCount 4` |
| 内门 in | 1 | 15 | 无 | `inJionDrama 6372101` … `inMaxJoinCount 1` |
| 真传 inherit | **7** | 7（`inheritMaxUnitCount`） | **无**（`inheritStartBattleDrama 0`） | 6373101…6374201 六段（含 `inheritNotAgreeJoin 6373601`、`inheritAddUnitIntim 120`、`inheritAddHate 180`） |
| 长老 elders | 1 | — | **有**（`eldersBattleID 24`） | 6375101 / 6375201 → 胜 6376301 / 败 6376401 |
| 大长老 bigElders | 1 | — | **有**（`bigEldersBattleID 25`） | 6377101 / 6377201 |

⇒ 弟子三档是"申请 + 剧情"，长老两档要**打一场擂台**（battle 24 / 25，`SpecialBattle*` 一系）。
真传还有"长老不同意收徒"的分支（`inheritNotAgreeJoin`）—— 正是 §6.6 里我们没模拟的那条。

### 7.5 建议方案（待拍板）

**接口**：`world_ai_action` 再加一个 `op=sect_post`（或 `promote_sect`），与入宗同族：

| 参数 | 说明 |
|---|---|
| `post` | `in` 内门 / `inherit` 真传 / `out` 外门（降级） |
| `master` | 仅 `inherit`，长老真名 → 映射部门 |
| `reason` | 必填，同上一条 op 的语义（NPC 自己权衡） |

**长老 / 大长老**：建议**本期不接**。要打擂台（battle 24/25），先得搞清 `SpecialBattle` 怎么开、
玩家要不要在场 —— 那是独立一块（与 §3 的"晋升考试"合并做）。

**名额**：事前按 `GetPostNumMax(school, post)` / `(post, department)` 拦；
超了报"内门已满 15/15，要进得有人先出"，**绝不让引擎去挤人**。

**真传**：沿用入宗那条（部门决定师父），但可以顺手把 `inheritNotAgreeJoin` 的剧情做成
"长老若好感不足会当众拒收" —— 那是游戏原有分支，不算我们造门槛。

### 7.6 待拍板

1. `op` 命名：`sect_post` / `promote_sect` / 复用 `join_sect` 加 `from`？（我倾向 `sect_post`，语义是"改职位"，升降都管）
2. 长老/大长老的擂台战：本期跳过，还是现在就做？
3. 真传要不要接 `inheritNotAgreeJoin`（长老拒收）那一段剧情？
4. `capacity` 的补齐（§7.3 那个 10 行补丁）：现在顺手做，还是并到晋升一起？
5. 降级（真传 → 内门）要不要允许 NPC 自己申请？（`MaxUnitClearUnit` 说明引擎会强制降级，主动降级是另一回事）

### 7.7 开工第一步

不是写代码，是**先验一条**：`MaxUnitClearUnit@0x37CB8B0` 到底按什么选人挤掉
（`GetPostNumMax` 比较 + 谁的 `GetManagerUnit`），以及 `GetPostNumMax` 两个重载的实参语义
（`(MapBuildSchool,int)` 与 `(int post,int department)` 分别在什么场合用）。
这条定了，名额拦截与"挤人"风险的处置才有着落。
