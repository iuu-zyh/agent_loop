# 解除义父母关系（`jie_chu_ren_yi_fu_mu`）

## 目标

`social_relation` 已有 5 个缔结类 op（道侣/夫妻/师徒×2/结义/义父母）与 5 个解除 op，
但 **`ren_yi_fu_mu` 没有对应的解除 op** —— 认了义父母之后无法在工具层撤销。
本次补上 `jie_chu_ren_yi_fu_mu`，并保持"对象可以是任意 NPC，不限于玩家"。

## 引擎事实（开工前查证）

| 事实 | 出处 |
|---|---|
| `RelationData.parentBack` = 我的**义父母**；`childrenBack` = 我的**义子女** | `F:\DecompDump\dump\types36_fulldll.txt` L13701-13730（关系面板逐字读出）；`csharp/game/UnitSnapshot.cs` L482-483 同口径 |
| `UnitActionRoleBreakWith` **不带关系类型**，只有 `Init(对方)` + `OnCreate()` | `reference/decomp_tmp/unit_actions.txt` L7672-7803；`F:\DecompDump\dump\types26_social.txt` L81-89 |
| 引擎的关系读写按"一对人一条关系"：`GetRelation` 返回单个枚举，`UnitActionRelationSet.OnCreate` 双向 `ClearRelation` 后写新关系 | `csharp/tools/RelationTool.cs` 顶部裁决码表；`docs/archive/2026-09-17-关系预检误判.md` |
| 神识传音的直写兜底对 `ParentBack` 写的是 `parentBack`（两侧都写） | `F:\DecompDump\dump\types36_fulldll.txt` L46460-46466 |

**结论**：解除路径与现有 5 个解除 op 完全同源 —— 先尝试原生 `UnitActionRoleBreakWith`，
读回仍在则字段直写兜底。字段兜底必须**同时清理两侧的 `parentBack` / `childrenBack`**：
只清 `npc.parentBack` 会留下 `target` 仍把 `npc` 当义亲的半解除状态，而且不会报错。
原生 `BreakWith` 是否真的覆盖 `ParentBack` 没有反编证据（方法体在 GameAssembly.dll），
所以这次不依赖它 —— 读回判据覆盖两侧两种容器，未清干净就落直写。

## 改动面

- C#：`RelationTool.cs`（新 case、`BreakFieldsDirect`、`BreakStillRelated`、`BreakRelName`）
- Python：`tools/schemas.py`（enum + 释义）、`room/room_flow.py`（解除类公开叙述）、
  `tools/text_render.py`（解除文案）、`transport/bridge.py`（离线 stub）
- 契约测试：`tests/test_relation_diag_contract.py`

## 验证状态

- [x] 定向 pytest：`test_relation_diag_contract` / `test_tool_contract_consistency` /
      `test_prompt_rules_contract` / `test_room` / `test_text_render` / `test_bridge_tools`
      —— **190 passed**
- [x] 变异验证：把 `tr.childrenBack.Remove(npcId)` 改成 `tr.parentBack.Remove(npcId)`，
      `test_ren_yi_fu_mu_can_be_dissolved` 立刻变红（提示"字段兜底缺 …（会留下半解除状态）"），
      已还原
- [x] `dotnet build -c Release`：0 错误（4 条 CSC 的 LIB 路径警告，与本次无关）
- [x] 部署（12:54）：PyInstaller 自测档 `AgentLoopServer.exe` md5 `7add5d4644c9f04ed3f745a9fc177cf5`；
      DLL md5 `AE1E339033F7C840C11EEC91FF4E26ED`，游戏 + 编辑器两处共三处一致；旧件均已备份。
- [ ] 真机：认一次义父母 → `jie_chu_ren_yi_fu_mu` → 双方关系面板都不再显示义亲
- [ ] 未验证：原生 `UnitActionRoleBreakWith` 是否覆盖 `ParentBack`。代码不依赖它 ——
      读回查两侧两种容器，未清干净一律走字段兜底
