# IL2CPP 反编译/反汇编工具链 —— **缓存优先，别每次重跑**

> 目的：查过一次的方法，第二次直接读文件；不要再重复 `objdump` / ILSpy，
> 既慢又浪费上下文（2026-10-19 用户明确要求）。

---

## 一、地址约定

```
IL2CPP VA = 0x180000000 + RVA
GameAssembly.dll = <游戏根>\GameAssembly.dll
游戏根默认：E:\SteamLibrary\steamapps\common\鬼谷八荒
```

- `F:\_dt_dump\rvamap_all.txt`：13 万条 **RVA → 方法名**（例 `0x3D7DE10  WorldUnitLogMgr::UpdateLogData`）。
- `F:\_il2cpp_ref\Assembly-CSharp.dll`：Cpp2IL 产物，**只有签名/字段偏移/RVA，方法体是 `return null` 桩**。
  ⇒ 字段布局、方法签名、RVA 从它拿；**方法体必须 raw 反汇编**。

---

## 二、raw 方法反汇编（缓存优先）

```bash
# 首次：反汇编 + 自动给 call 加符号注释 + 落盘
python scripts/dev/il2cpp_disasm.py 0x3D7DE10 0x640 WorldUnitLogMgr.UpdateLogData

# 之后：命中缓存，只打印路径（--print 才输出内容）
python scripts/dev/il2cpp_disasm.py 0x3D7DE10 0x640 WorldUnitLogMgr.UpdateLogData

# 不写长度：自动取 rvamap 下一个方法作边界
python scripts/dev/il2cpp_disasm.py 0x3D7DE10 - WorldUnitLogMgr.UpdateLogData

# 看已缓存
python scripts/dev/il2cpp_disasm.py --list
```

缓存目录：`F:\_dt_dump\asm\<方法名>_<rva>_<size>.txt`（**带 `; 方法名` 注释**）。

脚本实现：`scripts/dev/il2cpp_disasm.py`
- 通过 WSL 调 GNU objdump（Ubuntu 自带，不需要装 capstone）；
- 用 `rvamap_all.txt` 给每个 `call 0x...` 补 `; 方法名`；
- `GGBH_GAME_ROOT` / `IL2CPP_DUMP_DIR` 可覆盖路径。

---

## 三、类型/字段 dump（ILSpy，缓存优先）

工程：`F:\_dt_dump\dt.csproj` + `F:\_dt_dump\Prog.cs`

```powershell
cd F:\_dt_dump
# 不带参数：dump 默认清单
& 'C:\Program Files\dotnet\dotnet.exe' run --project 'F:\_dt_dump\dt.csproj' -c Release

# 带参数：只 dump 指定类型；已存在会自动跳过
& 'C:\Program Files\dotnet\dotnet.exe' run --project 'F:\_dt_dump\dt.csproj' -c Release -- DataUnitDie WorldUnitBase
```

- 产物：`F:\_dt_dump\types\<Type>.txt`（**一类型一文件**）；
- 已存在则输出 `[cache] ...` 并跳过 —— 不再重复反编译；
- 要加新类型，直接把类型名加在命令行末尾。

**已经 dump 过的类型不要再跑**，直接读 `F:\_dt_dump\types\`：

| 文件 | 内容 |
|---|---|
| `WorldUnitLogMgr.txt` / `DataUnitLog.txt` | 经历系统全部字段/方法签名（含 `LogItemData` / `Data`） |
| `ConfRoleLogLocal.txt` | 日志文案表类（`GetItem(int)`、`LS(key, cond)`） |
| `DataUnitDie.txt` | 世界死亡档案 `DieData / UnitDieData / UnitDie` |
| `WorldUnitBase.txt` / `WorldUnitData.txt` | 世界单位字段、`isDie`、`onDieCall`、`Die()` 签名 |
| `WorldBattleData.txt` / `WorldBattleMgr.txt` | 战斗数据/管理器字段 |
| `BattleUnitMgr.txt` / `BattleUnitBase.txt` | 战场单位列表，`GetAllUnitIncludeDie` |
| `UnitCtrlBase.txt` / `UnitCtrlHuman.txt` / `UnitCtrlPlayer.txt` | 战场单位 `mIsDie/onDieCall/data` |
| `UnitDataBase.txt` / `UnitDataHuman.txt` | 战场 HP、`worldUnitData` 映射 |
| `EffectBase.txt` / `Effect6009.txt` | 死亡回调效果 |
| `ConfBattleEffectItem.txt` / `UnitActionRoleConvoy.txt` / `WorldUnitEffectBase.txt` | 效果/搬运重伤/世界效果 |
| `ConfDungeonBaseItem.txt` | 副本配置，`winFunction` +0xB8 / `loseFunction` +0xC0 |
| `SceneType.txt` / `SceneBattle` | `SceneType.battle` 静态属性 → `SceneBattle.unit`（BattleUnitMgr） |

> 约定：以后要把某个类型存进 `types/`，**先看有没有**；没有才跑一次上面的命令。

---

## 四、已缓存的日志管线方法（2026-10-19）

| 方法 | RVA | 缓存文件（`F:\_dt_dump\asm\`） |
|---|---|---|
| `WorldUnitLogMgr::UpdateLogData` | `0x3D7DE10` | `WorldUnitLogMgr.UpdateLogData_3d7de10_640.txt` |
| `WorldUnitLogMgr::WriteLogData` | `0x3D7E450` | `WorldUnitLogMgr.WriteLogData_3d7e450_510.txt` |
| `WorldUnitLogMgr::MerageAddLog` | `0x3D7D4C0` | `WorldUnitLogMgr.MerageAddLog_3d7d4c0_630.txt` |
| `WorldUnitLogMgr::SaveLogData` | `0x3D7DA30` | `WorldUnitLogMgr.SaveLogData_3d7da30_3e0.txt` |
| `DataUnitLog.LogData::AddLog` | `0x2FB0040` | `DataUnitLog.LogData.AddLog_2fb0040_1a0.txt` |
| `DataUnitLog.LogData::AddVitalLog` | `0x2FB01E0` | `DataUnitLog.LogData.AddVitalLog_2fb01e0_1a0.txt` |
| `DataUnitLog.LogData::ClearOverMonthLog` | `0x2FB0380` | （未缓存，需要时跑脚本） |

### 已确认的管线（重要）

```
AddLogData(unit, keyID, values, cond)
    → WorldUnitLogMgr.allAddLogData[unitID].Add(LogData)
    → **暂存层**（面板此时看不到）

WriteLogData(allUnit, allAddLogData, allAddVitalLogData, month)
    → foreach unit: MerageAddLog(unitID, logs, month, isBig=false)
    → DataUnitLog.LogData::AddLog  → 追加到 DataUnitLog.LogData.allLog（原始行）
    → 最后 Dictionary::Clear 两个暂存字典

SaveLogData()
    → UpdateLogData()（给所有单位补 DataUnitLog.LogData 容器）
    → 构造 allUnit 字典
    → WriteLogData(...)（合并 + 清暂存）
    → 落盘
```

**关键点**：`DataUnitLog.LogData::AddLog` 只追加 `allLog`（原始行），
**没有**更新解码缓存 `_allLogData` / `lastUpdateLogDataMonth`。
面板读的是 `allLogData` / `allVitalLogData`（解码缓存），
所以“写入成功”≠“面板立刻显示”，通常要等月结/存档触发缓存重建。

### 已实施（10-19 20:3x，`TeamBattleFollowup.ForceMergeToPanel`）

1. 写完经历后反射取 `allAddLogData` / `allAddVitalLogData`；
2. 调 public `WriteLogData(空 allUnit, 两个暂存字典, 账面月)` 立即合并；
3. 把 `DataUnitLog.LogData.lastUpdateLogDataMonth` /
   `lastUpdateVitalLogDataMonth` 置 `-1`，强制 `allLogData` getter 重建；
4. 回读 `GetLogDataSync(unitID).allLogData` 验证，打 `✅已显示` / `❌合并后仍读不到`。

**尚未确证**：若数据已进 `allLogData` 但 UI 面板仍不刷新，
说明 `UINPCInfoLog` 自己还缓存了一份列表，需要找它的刷新入口（或要求重开面板）。
下一场组队战斗的 `面板回读 → … ✅已显示` 是判断分水岭。

---

## 五、战场死亡 / 重伤（2026-10-19 新增）

详细结论见 **`docs/features/team.md`** §5.1 / §5.2（逐条反汇编原文在
`docs/archive/2026-10-19-队友重伤与死亡链路.md`）。一句话：

> 副本 HP 归零只死战场克隆体 `UnitCtrlBase`，不自动杀 `WorldUnitBase`，也不自动挂重伤；
> 组队 mod 的「内外重伤」是 `BattleEffect 6009` 死亡回调 → 剧情 → `addNPCLuck` 做出来的。

| 方法 | RVA | 缓存文件（`F:\_dt_dump\asm\`） |
|---|---|---|
| `Effect6009::Create` | `0x3522B80` | `Effect6009.Create_3522b80_e0.txt` |
| `Effect6009.<Create>b__0_0` | `0x3522C60` | `Effect6009.Create.b__0_0_3522c60_c0.txt` |
| `WorldBattleMgr::BattleEnd` | `0x27F98A0` | `WorldBattleMgr.BattleEnd_27f98a0_e30.txt` |
| `UnitCtrlBase::Die` | `0x2CB1DB0` | `UnitCtrlBase.Die_2cb1db0_590.txt` |
| `UnitCtrlHuman::Die` | `0x2CB6860` | `UnitCtrlHuman.Die_2cb6860_200.txt` |
| `UnitCtrlPlayer::Die` | `0x2CBD600` | `UnitCtrlPlayer.Die_2cbd600_400.txt` |
| `WorldUnitBase::Die` | `0x32BBED0` | `WorldUnitBase.Die_32bbed0_5f0.txt` |
| `BattleUnitMgr::OnUnitDie` | `0x2B064C0` | `BattleUnitMgr.OnUnitDie_2b064c0_40.txt` |
| `BattleUnitMgr::GetAllUnitIncludeDie` | `0x2B05230` | `BattleUnitMgr.GetAllUnitIncludeDie_2b05230_1d0.txt` |
| `DataUnitDie.UnitDie::AddUnitDie` | `0x2FC0040` | `DataUnitDie.UnitDie.AddUnitDie_2fc0040_170.txt` |
| `UnitCondition::IsBattleUnitDie` | `0x25987D0` | `UnitCondition.IsBattleUnitDie_25987d0_150.txt` |
| `BattleFunction::UnitAddEffect` | `0x290AC40` | `BattleFunction.UnitAddEffect_290ac40_1d0.txt` |
| `BattleFunction::GetUnit` | `0x29055A0` | `BattleFunction.GetUnit_29055a0_8e0.txt` |
| `BattleEffectMgr::Create` | `0x28F79B0` | `BattleEffectMgr.Create_28f79b0_60.txt` |
| `BattleEffectMgr::CreateCore` | `0x28F7B50` | `BattleEffectMgr.CreateCore_28f7b50_2c0.txt` |

**关键字段**（`types\UnitCtrlBase.txt` / `UnitDataHuman.txt`）：

```text
UnitCtrlBase:   mIsDie +0x10, onDieCall +0x18, data +0x60
UnitDataBase:   int hp, DynInt maxHP
UnitDataHuman:  WorldUnitData worldUnitData
EffectBase:     data +0x10, unit +0x18, unitEffectSource +0x20
WorldUnitBase:  _isDie +0x78, onDieCall +0x80
```

---

## 六、纪律

1. **先查缓存，再决定要不要反编译**（`asm/` + `types/`）。
2. 反汇编一次就落盘，文件名带方法名+RVA+长度，便于 grep。
3. 不确定的语义**不要猜**：先看 raw 机器码，再看 `rvamap_all.txt` 的调用名。
4. 新增结论写进 `docs/`，不要只留在对话里（上下文会被压缩）。
