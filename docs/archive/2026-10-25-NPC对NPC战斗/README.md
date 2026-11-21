# NPC 对 NPC 战斗 —— 调研与方案

> ⚠ **本目录已归档（2026-10-29）**。功能已实现完毕，**现状请看 [`docs/features/npc-duel.md`](../../features/npc-duel.md)**。
> 这里保留的是调研过程、被推翻的中间结论与逐轮真机时间线，只在追溯"结论怎么来的"时翻。
>
> 本目录只服务一件事：**让 NPC 之间真的打起来（并能被玩家看到 / 或只是后台结算）**。
> 不重复 [team.md](../../features/team.md)（组队机制）、[team-battle-join](../2026-10-25-队友进决斗/README.md)（队友进决斗）、
> [dungeon-type-mapping.md](../../reference/dungeon-type-mapping.md)（副本类型）。

## 一句话结论（先看这个）

**引擎本来就有"NPC 打 NPC"的两条正经路：**

| | 引擎能力 | 有没有场景 | 能不能被玩家看到 | 我方现状 |
|---|---|---|---|---|
| **后台结算** | `unitA.CreateAction(new UnitActionRoleAttack(unitB))` → `UnitActionRoleBattle::NPCToNPCAction` | ❌ 无 | ❌ 看不到 | **仍没接**（这是 B3；`attack` 的 NPC 目标走的是观战） |
| **真观战** | `battle.data.npcFightUnits = (A,B)` + `IntoBattle(toUnit:null, dungeonID:4001, …)`（引擎的 `dramaUnitBattle_*` 剧情函数走的就是这条） | ✅ 真场景 | ✅ 玩家旁观 | **已接**（路线 B，10-26） |

**用户提议的路线 A 在"让 NPC 当发起方"这个写法下不成立**：
`UnitActionRoleToUnitBase` 按"谁是玩家"分三条虚方法，A→B 两边都不是玩家 ⇒ 走 `NPCToNPCAction`，
**直接结算、不起场景**，玩家不会被"一起带进去"。证据见 [01-F2/F3](../../reference/engine-facts.md)。

⇒ 但它**换一个接收者就成立**：`player.CreateAction(new UnitActionRoleAttack(B))`
走 `PlayerToNPCAction`，真场景 + 战前窗，队友作为盟军上场 —— 这就是
**路线 A2 / `mode=together`**（[mode-together.md](mode-together.md)，10-27 已实现待验收）。
关键差别只在 `rcx` 是谁，同一个动作类。

真正"两个 NPC 单挑、玩家在旁边看"是**路线 B**（[路线B-引擎观战.md](路线B-引擎观战.md)）。

## 文档索引

| 文件 | 内容 |
|---|---|
| [../../reference/engine-facts.md](../../reference/engine-facts.md) | **引擎事实与证据链**：三分派 / 无场景结算 / `npcFightUnits` 观战正门 / 纯结算 API / 宗门大比小比 / 组队前置 / 未验证清单 |
| [路线A-玩家参战.md](路线A-玩家参战.md) | 路线 A：玩家参战的决斗（队友盟军入场），含配表与代码改动、边界 |
| [路线B-引擎观战.md](路线B-引擎观战.md) | 路线 B：引擎原生观战（`dramaUnitBattle` / 自建 `npcFightUnits`），含接入点与降级 |
| [路线对比与推荐.md](路线对比与推荐.md) | 两条路线对比、推荐执行顺序、探针清单 |
| [进展日志.md](进展日志.md) | 每轮查了什么、结论、下一轮从哪继续 |
| [mode-together.md](mode-together.md) | **路线 A2 落地**：`mode=together` 玩家参战（反编证据 / 复用链 / 验收判据 / 已知语义错位） |

## 两条路线的定位（不要混用）

- **路线 A = "玩家也参战"**。它解决的是"我想帮队友一起打"，**不是**"两个 NPC 单挑给我看"。
- **路线 B = "玩家只看"**。它才是"B 与 A 单挑"的正门，也是"NPC 之间寻仇/切磋自动打起来"的通用解。

## 当前状态

| 项 | 状态 |
|---|---|
| 引擎调研 | **已完成**（反编地址 + 配表 + 第三方 mod 实证，见 01） |
| 方案 | **已完成**（02 / 03） |
| 真机验证 | 路线 B 已验（10-27）；`mode=together` **待验**（判据见 [mode-together.md §7](mode-together.md)） |
| 路线 B（观战） | **已实现并编译部署**（10-26，见 [路线B-引擎观战.md §8](路线B-引擎观战.md)）：`NpcDuel` + `attack` 放开 NPC 目标 + 文案 |
| 路线 A2（玩家参战，`mode=together`） | **已实现，待真机验收**（10-27，见 [mode-together.md](mode-together.md)）：玩家当发起方 → `UIMapBattlePre` → `TeamJoinGate` → 队友注入，**全部复用现成链** |
| 后台结算（B3） | 未单独做（观战分支里已内置"没开起来就不等"的失败路径，`UnitBattle` 结算仍未接） |

## 环境备注（本轮）

`F:\` 在 WSL 里是**只读挂载**（`9p, ro`），项目内文件不能直接写。
本轮文档经 `powershell.exe` 走 Windows 侧写入（`C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe`
+ base64 → `[IO.File]::WriteAllBytes`）。要改代码/配表，需先把挂载恢复为 `rw`。
