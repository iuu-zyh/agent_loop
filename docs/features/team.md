# 组队（带 NPC 队友下副本）—— 实现现状

> **一句话**：让 agent 能邀请最多 **3 名** NPC 一起下副本。给 NPC 挂「结伴而行」气运 →
> 配置表条件组按气运筛人 → Harmony 钩子把筛出的人塞进引擎战斗容器 `npcUnits` →
> 引擎的 `addBattleUnit_3/4/5` 按位置取人上场。
>
> **不依赖任何第三方组队 mod。** 状态：组队 / 容器直塞 / 重伤 / 离队 / 写经历均已真机跑通（10-21）。

**本文是导读**。想改代码请直接看 `csharp/tools/Team*.cs`；
当时的调研与逐轮实验在 [`../archive/`](../archive)。

---

## 1. 关键分工：谁负责什么

这条决定了绝大多数设计取舍，**先记住它再改代码**：

| 环节 | 谁负责 | 为什么 |
|---|---|---|
| 剧情窗出现 / 正文 / 选项 | **配置表** | 表里没这行 ⇒ `GetItem` 返回 null ⇒ 静默不开窗 |
| 挂「内外重伤」 | **配置表** | `addNPCLuck_1_MID&52`；代码只补漏 |
| 谁上场 | **代码**（容器直塞） | 见 §二 |
| 认谁倒下了 | **代码** | 配置表对"谁死了"完全不知情 |
| 立绘显示谁 | **代码**（只填一侧槽） | 填两侧会与原生布局打架 |
| 摘号位让人离队 | **代码** | `addNPCLuck` 系列**没有**"摘除"指令 |

## 2. 唯一改动引擎的地方：容器直塞

条件组选人链在 `type=2` 后天气运上必抛（`GetLuck` 遇 `luckData=null` 直接 throw）。
所以我们的做法是：

```
BattleFunction.AddBattleNPCData(string[])  ← 前缀拦下
    把队伍成员按号位直接写进 battle.data.npcUnits
                                         ↓
    addBattleUnit_3/4/5 照常从容器按位取人
```

没组队时不拦截，普通战斗完全不受影响。**这是全项目唯一"绕过引擎选人"的点**，
其余全部走官方链路。挂载细节见 `TeamContainerInject.cs` 文件头。

## 3. 五个号位 vs 三个槽位（别混）

- **号位**（1~5）：气运编号 `MID&50~52` 之类，决定"这个人排在队伍第几位"
- **槽位**：引擎 `npcUnits` 的容量，`addBattleUnit_3/4/5` 对应 3/4/5 号位

概念混了会导致"明明邀请了 3 个人，只上场 2 个"。具体映射见 `TeamTool.cs` 的号位常量。

## 4. 配置表

六张表 + `DungeonTrigger` 三行 + `RoleLogLocal` 三条经历模板。
**改表之前先读** [`../reference/config-table-guide.md`](../reference/config-table-guide.md)。

| 表 | 作用 |
|---|---|
| `RoleLuck` 系 | 「结伴而行」气运本体 |
| `NpcCondition` | 条件组：按气运筛队友 |
| `DungeonTrigger` | 副本触发器（`dungeonType` 白名单） |
| `RoleLogLocal` | 战后经历模板 |
| `BattleFunction` 相关 | 战斗容器注入点 |
| `LocalText` | 文案（4 语言，`TeamDescService` 运行时改写） |

## 5. 引擎 API 的坑（改代码前必看）

这几条都是**踩过并定案**的，照着写会省一次事故：

| 坑 | 真相 |
|---|---|
| `OnUnitDie` ≠ 死亡 | 它表示"这一击打死了"，战场复位时也会触发 |
| 死亡钩子拿到的是 `UnitDataBase` | 不是 `UnitDataHuman`，强转会炸 |
| 不要用 `WorldUnitBase` 引用判等 | IL2CPP 包装对象每次取都是新实例 |
| `GetLuck` 遇 `luckData == null` **抛异常** | 不是返回 null。条件组在 `type=2` 上必踩 |
| 剧情立绘只填一侧 | 两侧都填会与原生布局打架 |
| 挂气运必须复刻引擎原生序列 | 顺序错了不生效且无提示 |

> 反编证据与字段偏移见 [`../reference/engine-facts.md`](../reference/engine-facts.md)。

## 6. 别回退的三件事

1. **不要为了"简单"去掉容器直塞** —— 条件组在 `type=2` 上必抛，去掉就等于组队不可用
2. **不要用引用判等判断"是不是同一个单位"** —— 必须用 unitID
3. **探针与产品代码必须隔离** —— `tools/` 不引用 `diag/`（唯一例外是 `ModMain` 的两个调用点）

## 7. 已知边界与未验证

| 项 | 状态 |
|---|---|
| 最多 3 名队友 | 引擎容量限制，不是我们设的 |
| 重伤 4 个月 | 配置表定的 |
| 重伤未愈不能重新入队 | 已实现，真机验证过 |
| 多人组队（>3） | **未验证** —— 探针 `TeamMultiProbe` 就是为这个留的 |
| 战斗中动态换人 | **不支持** |


## 7.1 日志在哪（排查第一站）

| 日志 | 位置 | 装什么 |
|---|---|---|
| **`Player.log`** | `C:\Users\<u>\AppData\LocalLow\guigugame\guigubahuang\Player.log` | **`P()` 的唯一落点**：`[TeamFollowup]` / `[TeamInject]` / `[KillStats]` / `[death]` |
| 探针日志 | `<ModAssets>\logs\team_multi.log` | 只装 `[TeamMulti]`，每会话覆盖 |
| 每局归档 | `<ModAssets>\logs\runs\<上一局时间>\` | 覆盖前自动搬入：探针日志 / 配置快照 / summary / Player.log，**一局一档** |
| MelonLoader 日志 | `<游戏>\MelonLoader\Logs\` | ⚠ **我们的 `P()` 永远不出现在这里**，别再往这翻 |

> 排排除问题先看 `runs/` 归档 —— 一局一档，比翻活动文件可靠。

## 8. 代码在哪

| 文件 | 干什么 |
|---|---|
| `csharp/tools/TeamTool.cs` | `team` 工具执行体（invite / dismiss / status）、号位气运读写 |
| `csharp/tools/TeamContainerInject.cs` | 容器直塞钩子、死亡钩子、立绘修正 |
| `csharp/tools/TeamBattleFollowup.cs` | 战后收尾：归队、重伤判定、离队、写经历 |
| `csharp/tools/BattleKillStats.cs` | 击杀统计 |
| `csharp/tools/TeamDescService.cs` | 运行时改写气运描述（4 语言） |
| `csharp/diag/team/TeamMultiProbe.cs` | 多人验证探针（唯一保留的一个） |
| `tools/team.py`（Python） | 工具 schema 与文案 |

## 9. 历史

历史文档全部在 **`docs/archive/`**（10-17 ～ 10-19 那批，共 7 份，过程流水已精简）：

- 逐轮实验记录：`docs/archive/2026-10-18-组队实验记录表.md`（已精简为结论索引）
- 交接档：`docs/archive/2026-10-19-组队交接档.md`（已精简）
- 重伤/死亡专题：`docs/archive/2026-10-19-队友重伤与死亡链路.md`（技术查证，保留原文）
- 配置表细节：`docs/archive/2026-10-17-组队配置表与部署.md`
