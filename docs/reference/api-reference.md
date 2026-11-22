# 官方 API 与反编速查

> 数据源：`docs/reference/csharp-iron-laws.md` §H 拆出（2026-11-20）。
> 找 API 签名 / 查本机路径 / 反编方法。工具链细节见 [`il2cpp-toolchain.md`](il2cpp-toolchain.md)。

---


### 1 本机关键位置

| 对象 | 位置 |
|---|---|
| 游戏本体 | `E:\SteamLibrary\steamapps\common\鬼谷八荒`（`GameAssembly.dll` 在根，IL2CPP 元数据 `guigubahuang_Data\il2cpp_data\Metadata\global-metadata.dat`） |
| 官方 Mod SDK | `<游戏根>\Mod\modFQA\`：`代码编写教程\`（`GGBH_API.chm` 官方 API 帮助 + `ModMain\` 官方模板）+ `配置修改教程\`（配置表 `xlsx` 对照 + **`配置（只读）Json格式\` 全量配置导出**）+ **`资源修改教程\`**（`ResBuildABProject\` 官方 AB 打包工程 + `资源修改教程.docx`）+ `MOD模板例子\` |
| 神识传音 Mod 逆向 | `f:\ggbh_mod_analysis\神识传音Mod逆向分析.md`（原式实现与 API 用法的最初来源） |
| 游戏内 UI 源码 | `csharp/UI/`（三面板与剧情窗注入源码） |
| 反编产物 | `F:\DecompDump\dump\*.txt`（ILSpy 小工具产物，见 H.3） |

### 2 官方 API 按层级

```
g.*（进程单例，游戏一切入口）
├── g.world        世界实例：unit(取单位)/playerUnit(玩家)/battle/mapEvent(格子事件)
├── g.timer        主线程帧回调（g.timer.Frame=注册每帧；C# 壳用它做主线程调度）
├── g.events       事件广播（On/Off 订阅；近况/天下大事靠它攒）
├── g.conf         配置表（roleGrade.GetGradeItem/GetGradeName 等）
├── g.data         存档数据（world.playerUnitID 等）
├── g.root         场景根（AddComponent）
└── g.ui / g.res / g.sounds   UI / 资源 / 音效

WorldUnitBase（单位）
└── .data.unitData（DataUnit.UnitInfoData）公有字段：
    propertyData(属性) / relationData(关系) / pointX,pointY(位置) / schoolID(宗门ID)
    props,propData(道具) / equips(装备) / abilitys(心法),skillLeft(武技),skillRight(绝技),step(身法),ultimate(神通)
    allTask(任务) / memoryData(记忆) / appellationTitle(道号) / heart(道心) ...
    方法：CreateAction(new UnitAction*…) 发起行为（双人行为/移动/加气运…）

DataUnit.RelationData（关系引擎，挂在 unitData.relationData）
    读：GetIntim / GetRelation / GetAllRelation / GetIntimUnitData(好友) / GetHumanValue(人情)
    写：AddIntim / AddHate / SetIntim / AddHumanValue（关系切换字段 lover/married/master/student…）
    字段：intimToUnit / intimToPlayerUnit / intimToSchool

行为与剧情命令
    CreateAction(new UnitActionRoleTrains(playerUnit))  双人行为（聊天/双修/切磋…）
    new WorldUnitAIAction*{...}.ActionStart()          世界 AI 行为（双修/论道/邀约…）
    DramaFunctionTool.OptionsFunction(string, DramaFunctionData=null)  剧情/宗门/UI 命令
    DramaTool.OpenDrama(...)                            打开剧情
```

**工具与 API 的对应**：`inspect_unit` = `UnitSnapshot` 按 `classes` 按需采集；`search_units` = `g.world.unit.GetUnits` 全图遍历 + `filters` 多条件过滤；`query_world` = `g.conf`/`g.events`；动作类 = `CreateAction`/`WorldUnitAIAction`/`OptionsFunction` ＋二阶段校验。

### 3 反编方法：工具链与实锤签名表

**工具链**：反编 `MelonLoader\Managed\Assembly-CSharp.dll`（IL2CPP 互操作程序集）+ `ICSharpCode.Decompiler`（写小工具 `F:\DecompDump\` 反编，产物 `F:\DecompDump\dump\*.txt`；`ilspycmd` 因包缺 `DotnetToolSettings.xml` 装不上，弃用）。**Mono.Cecil** 遍历全类型找"唯一持有某字段组合的类型"是定接缝的利器（快、无需运行时）。
> **局限**：互操作程序集**只有成员签名，方法体是 `extern`**；要看方法体逻辑需 `Il2CppDumper` 解 `GameAssembly.dll + global-metadata.dat`。属性"实际装什么值"只能真机核对。

**关键签名（均已编译验证）**：

| 数据 | 实锤签名 | 备注 |
|---|---|---|
| 姓名/性别/种族 | `propertyData.GetName()` / `.sex`(Man/Woman) / `.race`(Human/Demon) | 枚举转中文（`UnitSnapshot.SexCn/RaceCn`） |
| 境界 | `propertyData.gradeID`(int) → `g.conf.roleGrade.GetGradeName(int)` | 中文境界名 |
| 魅力/声望/资质/年龄/心情 | `propertyData.beauty/reputation/talent/age/life/mood` | int |
| 战力 | `FormulaTool.UnitPower.TotalPower(WorldUnitData)` | 属性+技能+道具综合 |
| 气运(先/后) | `propertyData.bornLuck`/`addLuck`（元素 `LuckData.id`）→ `g.conf.fateFeature.GetItem(id).desc` | |
| 宗门名 | `unitInfo.schoolID`(string) → `g.world.build.GetBuild(id)` → `MapBuildSchool.name` | 每局随机组合名，无 conf 固定表 |
| 地区 | `unitInfo.pointGridData.areaBaseID` → `g.conf.worldAreaBase.GetItem(id).name` | 须过 `GameTool.LS()`（见 D.6） |
| 功法(技能) | `unitInfo.skillLeft/Right/step/ultimate/abilitys`(ID) → `GetActionMartial(id).data`(MartialData) → `g.conf.battleSkillPrefixName.GetName(MartialData)` | 组合中文名如"煞云飞花术" |
| 道具/装备 | `unitInfo.propData.GetEquipProps(bool,bool)` → `PropsData.propsItem.name` | 中文名现成 |
| 与玩家关系/好感 | `relationData.GetRelation(player)`(枚举) / `GetIntim(player)` / `GetHumanValue(unitID)` | 枚举→中文（`RelationCn`） |
| 好感写入（10-12 建 / **10-15 改口径**） | `relationData.AddIntim(string unitID, float value, int clampNewIntim=0, string uiTip="", bool isTip=true)` / `AddHate(...)` 同签名 / `GetIntim(...)` / `ClearIntim(string)`；`SetIntim(string,float)` 未用（10-15 定案：换算交引擎，不写绝对值） | 走写入层 `ApplyIntim`（只传请求值 + 回读实得，无校正/无补差；实得被 `RoleClose` 系数衰减属预期）；配表 `g.conf.roleGive.closeParaA/closeOnceAddMax`、`g.conf.roleAskfor.closeParaA/closeAddMax`（详见 D.26） |
| 关系簿 | `relationData.parent/children/brother/lover/master/student/married`（unitID）→ `GetUnit(id).GetName()`；好友 `GetIntimUnitData(true,false).friendUnits` | 中文名 |
| 经历日志 | `g.world.unitLog.GetLogDataSync(unitID)` → **解码层** `LogData.allVitalLogData`(重要)/`allLogData`(常规) : `List<LogItemData>` → `LogItemData.month` + 逐 `logs[]/subLogs[]` 调 `Data.GetLogString()`（**只有这层出人话**） | 同类型还有**原始层** `allLog`/`allVitalLog` : `List<Il2CppStringArray>`（元素是 `string[]`，**不是** `LogItemData`，照 `LogItemData` 读会静默全空，见 D.1）；未固化的新条目在暂存层 `GetCacheWriteLog(unitID)` |
| 道号 | `unitInfo.appellationTitle.GetAppellationID()` → `g.conf.appellationTitle.GetItem(int).name` | |
| 全图单位 | `g.world.unit.GetUnits(bool)` | `search_units` 用，跳过玩家自己 |
| 单位唯一键 | `unitID`（官方示例实证）**≠ 中文名**；中文名用 `propertyData.GetName()` | npc_id 统一为中文名：`UnitLookup.Resolve` 先按 unitID 直查、失败全图按名匹配 |
| 枚举 | `UnitRelationType`(Parent/Children/Married/Master/Student/…)、`MartialType`(SkillLeft/Right/Step/Ability/Ultimate=灵技/绝技/身法/心法/神通) | 中文映射在 `UnitSnapshot` |

**反编经验**：
* `g.*` 是全局门面（`g.world`=WorldMgr / `g.conf`=**ConfMgr** / `g.timer` / `g.data` / `g.events`）。
* conf 表结构固定：管理类 `ConfXxx`（`g.conf.xxx`）+ 条目 `ConfXxxItem`（字段 `name/desc/id`），查表多为**基类 `ConfXxxBase.GetItem(int id)`**。
* Il2Cpp 集合不能 `foreach` / 不能向下转型（见 D.1）。
* `dynamic` 绑定需 csproj 引 `Microsoft.CSharp`。
* **查一个游戏类型的成员/泛型实参，必须读编译目标 `MelonLoader\Managed\Assembly-CSharp.dll`**（45MB，Unhollower 真实产物：字段已暴露成属性、`string[]` 写成 `Il2CppStringArray`），**不要**读
  `MelonLoader\Dependencies\Il2CppAssemblyGenerator\Cpp2IL\cpp2il_out\Assembly-CSharp.dll`（16MB，方法体全是 `ldnull; ret` 的 stub）。
  两者 md5 不同、`Managed` 才是 `csc` 实际引用的那个。用 Mono.Cecil 打印时**一定要打 `FieldType.FullName`**——
  只打 `.Name` 会把 `List<string[]>`、`List<LogItemData>`、`List<int>` 统统显示成 `List\`1`，据此判断层结构必错。
* 找到"能同时喂饱 A 和 B 两种字段的唯一消费者"是定位接缝的通用手法（如 `UIFastClick` = 全程序集唯一同时持有可重绑定 `KeyCode` 与 `InputButton` 的类型）。

### 4 官方模组加载链

游戏「模组编辑器」新建模组会分配**命名空间** `MOD_xxxxx`（记录于 `ModData.cache` 的 `modNamespace` 与 `ModMain.cs` 的 `namespace`），导出得到 `ModCode/dll/` 等结构；主菜单「本地模组」导入勾选后，官方桥 `GGBH_MOD`（MelonLoader 0.5）加载 dll 并**反射调用 `{namespace}.ModMain.Init()/Destroy()`**。**命名空间、程序集名、导出路径 `ModCode\dll\{namespace}.dll` 三者必须一致**，否则 DLL 加载失败。MID / 配置表导出 / 加密约束见 D.2。
