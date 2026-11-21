# 决斗（`world_ai_action:attack`）—— 实现现状

> **一句话**：`attack` 现在有三种形态，判据是"**谁算这一场**"，不是"打不打"：
> 打玩家（`target` 留空）、`solo`（A 打 B，玩家旁观）、`together`（玩家当发起方，队友含 A 一起上）。
>
> 本文只记**现在是什么样**。反编证据、被推翻的中间结论、逐条真机时间线在
> [`docs/archive/2026-10-25-NPC对NPC战斗/`](../archive/2026-10-25-NPC对NPC战斗/README.md) 与
> [`docs/archive/2026-10-25-队友进决斗/`](../archive/2026-10-25-队友进决斗/README.md)，
> 需要追溯"这个结论怎么来的"时才去翻。

---

## 1. 三种形态

| 形态 | 触发 | 谁动手 | 玩家 | 走哪条链 |
|---|---|---|---|---|
| **打玩家** | `target` 留空 / 填玩家名 | 说话的那个 NPC | 应战方 | 既有 `switch (op)`；`NPCToPlayerAction` |
| **`solo`** | `target`=NPC，`mode` 留空或 `solo` | A 打 B | **旁观**，不进战斗 | `NpcDuel.Attack` → `dramaUnitBattle` → 观战副本 4001 |
| **`together`** | `target`=NPC，`mode=together` | 玩家当发起方，队友（含 A）参战 | 参战方 | `JoinPlayerAttack` → `player.CreateAction(new UnitActionRoleAttack(B))` |

判据的落点是纯逻辑核 `csharp/state/AiActionTargetCore.cs`（零 Unity / 零 Newtonsoft）：

```csharp
Route Decode(op, modeRaw, hasNpcTarget)     // → ToPlayer / Solo / Together / Reject
```

**`spar` 对 NPC 一律拒绝**，不是"没做"：`spar` 的语义是 `UnitActionRoleDrill`（不落重伤、不致死），
而 `NpcDuel` 走的是真战斗（重伤 / 击杀 / 缴物）。把真战斗标成切磋就是骗玩家。

旧名 `watch` / `join` 仍认（10-27 下午的开发名），归一化后在结果里只出现 `solo` / `together`。

---

## 2. `solo`：A 与 B 单挑，玩家旁观

```
ToolExecutor.WorldAiAction
  → AiActionTargetCore.Decode                 → Route.Solo
  → NpcDuel.Attack(caller=A, target=B)
      DramaGate.TryDefer(ModIds.DramaNpcDuelWatch = -803158241, 180s)
      DramaFunctionTool.OptionsFunction("dramaUnitBattle_1_2", {unitLeft=A, unitRight=B})
        → DramaFunction::DramaUnitBattle        (0x224F110)
        → WorldBattleData.npcFightUnits[0xD8] = (A, B)
      → g.world.battle.IntoBattle(toUnit:null, dungeonID:4001)   (0x27FADA0)
  → SpecialBattle79 场景，玩家旁观
```

- **观战副本是 4001**（`type=79`、`sceneBaseID=14`）。引擎对它显式置 `isRealBattle=false`
  ⇒ 这场的单位**永远不会死**、世界 hp 不回写、引擎也不会自己弹处置页 —— 见 §4。
- **不需要改配表**：4001 是原版副本，`DramaDialogue` 的观战入口也是引擎自带的
  `dramaUnitBattle_*` 剧情函数。
- **同格是我们加的语义约束**（不是引擎要求）：`NpcDuel.AttackCore` 里
  `if (!DiagSwitches.NoDuelSameGrid && !SameGrid(actor, target)) return Fail("你与…不同格…")`。
  反编已确认 `UnitActionRoleToUnitBase::IsCreate`@0x37B8430 与 `UnitActionRoleAttack::IsCreate`
  @0x3702150 都**不判坐标**；`dramaUnitBattle_*` 没有距离条件，战场内靠
  `BattleMapMgr::PointToWorld` 重新摆位。加这条是因为"人不在同一处还打起来"读着出戏，
  关掉它用 `_diag_no_duel_samegrid.txt`（放一个空文件即生效，免重启）。

## 3. `together`：A 请玩家一起上

**整条技术内容就是一行**（接收者必须是玩家）：

```csharp
player.CreateAction(new UnitActionRoleAttack(B), true);   // 0x32B8D40
```

引擎按"谁是玩家"分派：`player` 当发起方 ⇒ `PlayerToNPCAction`@0x18370C820 ⇒ 真场景 + 战前窗。
同一行换成 `A.CreateAction(...)` 就成了 `NPCToNPCAction`@0x183709480 —— **直接结算、不起场景**。
差别只在 `rcx` 是谁。

```csharp
// ToolExecutor.JoinPlayerAttack(caller, foeName, foe)
1. 守卫：foe/caller/player 非空、三者互不相同、caller 不是玩家自己
2. 算 callerIsMate / teamSize（决定结果里能不能说"我跟你一起打了"）
3. 同格：玩家↔B 必须同格；A↔B 同格受同一个开关控制
4. DeferNativeUnitAction 登记 → UnitActionPending.Take 取槽位
5. BattleOutcome.Arm(slot, foeName=?, ..., op="attack", mode="together")
6. player.CreateAction(atk, true)；返回码非 0 ⇒ 整套回滚 + Refused(...)
```

外侧三步全部复用现成链，一个字没改：

```
player.CreateAction
  → WorldBattleMgr::IntoBattle@0x27FAAA0   建数据 + UIMgr::OpenUI + UIMapBattlePre::InitData@0x27FAD59
                                            （★ 战前窗是一个独立阶段，不进场景 ★）
  → 玩家点「应战」→ UIMapBattlePre::<UpdateUI>b__10_3
  → WorldBattleMgr::IntoBattle@0x27FADA0   真正进场景（dungeonID=11、type=40）
  → DungeonTrigger(MID&004) → TeamContainerInject 直塞队友
  → 战后 TeamBattleFollowup 写"并肩应战"那套经历
```

> 早先我判"`CreateAction` 是同步链，中间没机会等确认窗"——**错**，反编两阶段推翻了它。
> 详见 `docs/archive/2026-10-25-NPC对NPC战斗/mode-together.md §2`。

**已知语义错位**：确认窗的文案取 `DramaNativeClaim.CurrentChainUid()`（= B），
所以显示的是「**B** 来势不善」，而不是 A 的口气。功能不受影响，措辞待改。

## 4. 战后处置页（`solo` 专用）

观战副本 79 的单位不会死、引擎也不弹处置页，所以"打赢了怎么处置败者"必须我们拦：

- 拦截点：`SpecialBattle79::BattleEnd`@0x2BFDCD0 尾跳到 `BattleEndMgr::BattleEnd(Boolean)`@0x28FA0D0，
  `BattleEndHook.Prefix` 返回 `false` = 干净拦住整场（没有半结束中间态）。
- 弹的是**原版** `DramaDialogue 20403`，真身 `UICustomDramaBattleNPCFailed2`；
  立绘位 `unitLeft=玩家 / unitRight=败者`。
- 五个选项全部改走**原生回调**（`csharp/drama/NpcDuel.cs` 的 `NativeKill/NativeRelease/NativeInsult`）：
  `204033` 击杀 → `UnitActionRoleKill`（走 Dying，不是瞬删，所以 `isDie` 仍 false 是正常的）；
  `204032/204034` 放人 → `UnitActionRoleRelease`；`204035` 羞辱 → 原生 `InsultUnit`；
  `204036` 选物不干预，只记 `outcome=pill`。
- 仍由我们补两样：`abuseAddFeature1/2` 气运、战败重伤 `hurtFeatureID`（原生那条挂在真实攻击战斗上，79 没有）。
- **玩家不选就无限等**（用户 10-26 拍板，不设超时），复用既有 0.25s 采样，连续两拍看不到窗才算关窗。
- 存活判据三级：`dd.onEndCall` 事件 → 按名字取 `UIDramaDialogue` 判可见 → 仅在名字取不到时退回层顶。
  第三级是**代理量**，单独用会误判（曾提前 46 秒放行，把玩家还没点的页当成关了）。

## 5. 结果字段（C# → Python）

外层 `{"success": true, "data": {...}}`。渲染层只翻译事实，不在 Python 判胜负。

| 键 | 语义 | 缺时 |
|---|---|---|
| `op` / `target` | 恒 `attack` / 对手名 | — |
| `mode` | `solo` / `together`（打玩家时无此键） | 无 = 打玩家 |
| `battle_seen` / `battle_finished` | 是否真进了战斗 / 是否打完 | — |
| `spectator` | 旁观的玩家名 | 读不到就不给，文案不提"谁在看着" |
| `left` / `right` | `{name, unit_id, alive, hp, hp_max}`；hp 一律取**世界单位** | 两处都读不到则整块不给 |
| `winner` | 胜者名 | 只在"两方都读到且恰好一方倒下"时出现；否则不给 |
| `judgement` | `{loser, outcome, label}`；`outcome ∈ kill/spare_took_items/insult/spare/pill/closed` | 没走到处置则整块不给，文案一个字都不加 |
| `caller_is_mate` / `team_size` | `together` 专用 | `caller_is_mate=false` 时不许对玩家说"我跟你一起打了" |

**"字段缺失 ≠ 否定"** 是这里的硬口径：读不到就不给键，渲染层换一种说法，不猜。

## 6. 收口判据

三条证据**先到先算**，没有新定时器：

1. `BattleEndFront` —— 抓世界单位快照（那时人还在）；
2. `BattleExit` —— 场景关闭；
3. `isBattle` 真→假（`BattleFlowGate` 每 0.25s 采样的**边沿**）。

兜底只有 `DramaGate` 的 180s 槽位过期（"玩家永远不回应"的通用底线），
外加世界重载 `ForceResolveAll`。长战斗靠 `OnSlotBeat → Renew` 续期，不是猜时长。

**为什么不等 N 秒再判**：`isBattle` 在 `IntoBattleInit`@0x27FA86F 一句话就置真，
"到底开没开打"是**同步可读的事实**。

## 7. 开关

| 文件 | 作用 |
|---|---|
| `_diag_npc_duel.txt` | 不用等模型出手，直接触发一次观战（内容 `left=江宇文` / `right=姜萌`） |
| `_diag_npc_duel_report.txt` | 上面那次触发的完整时间轴报告（每次触发先清空） |
| `_diag_no_duel_samegrid.txt` | 关闭同格硬闸，回到 10-26 行为 |
| `_diag_no_relaunch.txt` | 与决斗无关，但同族（BrainLink 自动拉起），见 `brain-liveness-design.md` |

诊断触发必须在 `ModMain.WorldEntered` 之后（读档屏里触发会被 `ForceResolveAll` 收票）。

## 8. 现状与缺口

**已真机验证**

- `solo`：观战场景、战场单位读取、原生处置页点击回调、`props` 由引擎算、羞辱第二页由引擎自己开。
- `together` 的前置：`UnitActionRoleAttack` 这条链**确实会弹应战窗**；`type=40` 里队友**确实进场并会倒下**。

**未验证 / 已知缺口**

| 项 | 说明 |
|---|---|
| `together` 全链 | **没有任何真机样本**。要看到：`[UnitAction][together]` → 弹进战斗预告窗 → `[TeamJoin] 弹出确认窗` → `★直塞完成★` |
| 逃跑（对方没应战） | 应当走 `dramaId=20102` 反馈页**当场收口**，不再等 60s；同样没有真机样本 |
| `solo` 同格 | 新加的约束，之前跨格的样本现在会直接「不同格」失败 |
| 后台结算（只要结果不要场面） | **没接**。引擎有 `UnitActionRoleBattle::UnitBattle`（纯随机算术、不写经历），但产品上没入口 |
| `204033` 击杀分支 | 代码已改走原生 `UnitActionRoleKill`，真机只跑过 `<Init>b__8_1` 那条路径 |
| 观战画面 | 玩家在观战场景里看到什么（镜头、能否操作）没有记录 |
| 引擎自己的 `SpecialBattle79winUnit` | 仍未接，胜负只报能读到的 `isDie` |
| 确认窗文案 | 显示 B 的名字而不是 A 的口气（见 §3 末） |

## 9. 相关代码

| 文件 | 职责 |
|---|---|
| `csharp/state/AiActionTargetCore.cs` | 三形态判定核（纯逻辑，`AiActionTargetCoreTests` 28 项） |
| `csharp/drama/NpcDuel.cs` | `solo` 全链 + 处置页拦截 |
| `csharp/tools/ToolExecutor.cs` | `JoinPlayerAttack`（`together`）、`case "attack"`（打玩家） |
| `csharp/drama/BattleOutcome.cs` | 结果锚点（`Arm` / `Resolve` / 反馈页 20102 早收口） |
| `csharp/tools/TeamContainerInject.cs` | 决斗三分支（`isDuel` → 同意/不同意/已决定） |
| `csharp/tools/TeamJoinGate.cs` | 应战窗上的「一起应战 / 我一个人」 |
| `tools/text_render.py` | `_render_npc_watch` / `_render_battle_result`，按 `mode` 分话术 |
| `tests/test_duel_samegrid_contract.py` | 同格、反馈页、`mode` 捕获时机等 9 条契约 |
| `tests/test_text_render.py` | 三种形态的话术逐字基线 |
