# 论道静默失败（`lun_dao` + `movement`）—— 结论与现状

> **一句话**：不是论道坏了，是**传送把 NPC 当月的行动预算扣光了**，他付不起论道要的 5 天；
> 而 `ActionStart` 把 `CreateAction` 的返回码**丢掉了**，所以既没界面也没提示。
>
> 完整排查过程（含七次误判）封档在
> [`docs/archive/2026-10-22-论道静默失败/排查过程.md`](../archive/2026-10-22-论道静默失败/排查过程.md)，
> 根因+修法原文在 [`2026-10-23-lun-dao-bug-fixed.md`](../archive/2026-10-22-论道静默失败/修法与清理.md)。
> **不建议先读那两份**，读本文就够。

---

## 1. 三层因果

| # | 环节 | 事实 |
|---|---|---|
| ① | 传送用错了 API | `movement:teleport` / `summon` 走的是 `UnitActionMoveNPC` / `UnitActionMovePlayer`——**走路**动作 |
| ② | 走路按距离收天数，瞬移分支也不免 | `Init` 按两点距离算 `day`（61 格 ⇒ **15 天**）；`Create` **只看 `day > 0` 就扣**，不管 `OnCreate` 走的是瞬移还是真赶路 |
| ③ | 论道准入看天数 | `Discovery.IsCreate` → `IsDayCost(costDay=5)` 比 `residueDay`；被扣成 0 ⇒ 返回 `1` ⇒ 拒绝 |

**静默的来源是第 ③ 步之外的第四件事**：`WorldUnitAIAction1037::ActionStart`@0x45E2210 调完
`npc.CreateAction(discovery)` **直接 ret，返回码完全不看** —— 只有我们挂的 `onEndCall` 被调成 `false`。

## 2. 为什么"手动点能成功、工具调用失败"

两条路都调 `CreateAction`，但 `IsDayCost` 读的是 **`action.unit`**（由 `Init(unit)` 写入），不是接收者：

| 路径 | 入口 | `action.unit` | 查谁的天数 |
|---|---|---|---|
| 面板手动 | `UINPCInfoUnitInfo.<InitMeum>b__19`@0x33601DE | **玩家** | 玩家的（够）⇒ 放行 |
| 工具调用 | `ActionStart`@0x1845e22ee | **NPC 自己** | 江宇文的（0）⇒ 拒 |

用户那句「同一个 NPC、同一个位置，手动点成功、工具失败」就是这条差异的全部。

## 3. 修法与四道保险

`teleport` / `summon` 改走 `UnitActionSetPoint`（`day` 恒 0、`OnCreate` 里
`UnitInfoData::SetPoint` 维护格子索引、自带 12 条校验）；`travel` **保持不动**（真赶路扣天数合理）。

配套四道：

1. `residueDay` 前后对比；
2. 回读 `pointX/pointY` 复核，不到位就 `Fail`；
3. 日志自证 `residueDay 15→15（未扣天数 ✓）` / `（★ 天数被改了，异常）`；
4. 返回值带 `day_cost = before - after`。

## 4. 现状

**已修复并真机验证通过**：`residueDay 15→15`、`code=0`、三枚选项按钮、
页 `22821→22834→22841→22833` 全跑通、`ok=True`。

**护栏**（改这里之前先看这几条断言）：

| 断言 | 钉的是什么 |
|---|---|
| `test_actionstart_false_is_not_reported_as_player_refusal` | 不许把 `CreateAction` 失败说成"玩家婉拒或中途离开" |
| `test_ok_false_text_mentions_the_real_cause` | 话术必须含「引擎没有接受」+（余暇/天数），四条已证伪的归因**一个都不许写回**（整月边界 / 跨日 / 走到你跟前 / 没走到） |
| `test_native_refuse_why_probes_round_day_residue` | 兜底诊断必须探 `residueDay` |
| `test_native_refuse_why_does_not_claim_energy_without_evidence` | 没有两个数就不许说"精力不足" |
| `test_tool_contract_consistency` 的 `"movement": [..., "UnitActionSetPoint"]` | 传送不许退回走路动作 |

**一条通用纪律**（从这里学到的，已写进测试注释）：
**文案会直接进模型上下文**。"他还没能站到你面前"这类话模型会当真，随后自己编解释。
所以话术里的每一句归因都要有证据，没有就只说"引擎没有接受"。

## 5. 探针去留

这次排查临时装的 6 个探针类（`IsCreateProbe` / `GridProbe` / `AiActionMove` / `AiActionProbe` /
`MovePointProbe` / `DiscoveryIsCreateProbe` / `CreateActionProbe` / `MoveNpcOnCreateProbe` /
`SetPointIsCreateProbe` / `MoveNpcIsCreateProbe`，共 421 行）**已整体删除**，
连 `ModMain` 的六项补丁自检块与 `ToolExecutor` 的三处探针调用一起清掉，
二进制里按类名搜（UTF-8 + UTF-16-LE）全部 0 命中。

反面钉子：`test_move_probe_is_gone` 盯着 `LogAiActionMoveProbe(` / `LogAiActionEnergyProbe(` /
`LogGridProbe(` 不许再出现在执行路径里。**要重新加探针，先改那条文档和那个断言。**

---

## 6. 相关代码

| 关注点 | 文件 |
|---|---|
| 修法落点（改走 `UnitActionSetPoint`） | `csharp/tools/NativeActionService.cs` |
| 返回码不再被丢弃 | `csharp/app/ModMain.cs`（`ActionStart` 附近） |
| 四道保险 | 见本文 §3；断言在 `tests/test_action_refusal_contract.py` |
| 探针（已清理） | 反面钉子 `test_move_probe_is_gone` |

> 完整排查（七次误判 + 反编证据）在
> [`docs/archive/2026-10-22-论道静默失败/排查过程.md`](../archive/2026-10-22-论道静默失败/排查过程.md)，
> 根因与修法原文在同目录 `修法与清理.md`。
