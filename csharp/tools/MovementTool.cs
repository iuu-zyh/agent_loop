/// <summary>
/// MovementTool —— **动作域·位移**：`movement`（召唤/传送/行走）。
/// （11-19 由 6711 行的 `ToolExecutor.cs` 拆出，11-20 按 Python 的域归并；成员逐字搬移。）
/// 跨类调用一律 `<类名>.<方法>`；`internal` = 仅本程序集可见。
/// 【Python 对应】动作域·位移（`movement`） —— 划分以 Python 侧为准，别在这里另立一套分类。
/// </summary>
using System;
using System.Collections.Generic;
using Newtonsoft.Json.Linq;
using static AgentLoopBridge.ToolResult;

namespace AgentLoopBridge
{
    internal static class MovementTool
    {
        internal static JObject Movement(JObject args)
        {
            string op = (string)args["op"] ?? "";
            // 发起方恒为 harness 注入的 initiator（dialogue_agent._execute_tool_calls）。
            // `target`（09-16）= **对手方**：缺省 = 玩家（老路径逐字节不变），填了才是其他 NPC。
            // （旧版那个 `initiator ?? target` 兼容别名已删 —— 它那个 target 指的是 actor，同名不同义，勿混。）
            var wub = ToolArgs.BindActor(args, out string initiator, out JObject bindFail);
            if (bindFail != null) return bindFail;
            var player = ToolArgs.ResolveCounterparty(args, out string cpName, out JObject cpFail);
            if (cpFail != null)
                return cpFail;
            if (player == null)
                return Fail("玩家单位不可用");
            string target = "";
            target = UnitIdentity.Name(wub, initiator);   // 取不到就用 harness 给的字符串

            // 神识传音 IL 实证（closures_il b__25_68 召唤 / b__25_70 传送）：
            //   召唤 = 被召唤者(玩家)移动到召唤者(NPC)位置 → player.CreateAction(UnitActionMovePlayer(NPCPoint), true)
            //   传送 = 传送者(NPC)移动到目标位置(玩家) → NPC.CreateAction(UnitActionMoveNPC(playerPoint), true)
            switch (op)
            {
                case "summon": // 把对手方召到 NPC 位置（对手方缺省=玩家；填 target 则召那个 NPC）
                {
                    // ★ 10-22 重写：改用 `UnitActionSetPoint`（引擎的零成本瞬移动作）★
                    //
                    // ## 旧实现为什么错
                    //
                    // 旧代码用 `UnitActionMovePlayer` / `UnitActionMoveNPC`，两者 `Init` 都会算 `day`：
                    //   · `UnitActionMovePlayer::Init` @0x38E52x0 → `MapTool::CalMoveDay(...)`
                    //     → `Mathf::FloorToInt` → `mov [rdi+0x18], eax`
                    //   · `UnitActionMoveNPC::Init` @0x38E4810 → 同族公式，上限是 `unit.residueDay`
                    // 而 `UnitActionBase::Create` @0x3507F50 只看 `day > 0` 就扣：
                    //     mov edx,[rdi+0x18] ; test edx,edx ; jle 跳过 ; call AddResidueDay
                    // ⇒ **即使 `OnCreate` 走瞬移分支（坐标立刻改、`moveComplete=true`），天数照扣。**
                    // 真机代价：江宇文 (55,79)→(46,27) 距离 61 格 ⇒ `day=15` ⇒ `residueDay` 15→0。
                    //
                    // ## 为什么 `UnitActionSetPoint` 是对的
                    //
                    // 反汇编事实（`F:\_dt_dump\types\UnitActionSetPoint.txt` + asm）：
                    //   · 它**没有覆盖 `Init`**（方法表只有 .cctor/.ctor/IsCreate/OnCreate/UpdateGimpedPoint）
                    //     ⇒ `day` 保持 `UnitActionBase` 默认值 **0** ⇒ `Create` 里 `jle` 跳过 ⇒ **不扣天数**；
                    //   · `OnCreate` @0x2B2A310 内 `call UnitInfoData::SetPoint` ⇒ 坐标 + 格子索引正确搬家
                    //     （`Map::DelGridUnit` @0x3CE5F40 / `Map::AddGridUnit` @0x3CE5590，均已核实）；
                    //   · 它**有 `IsCreate`** @0x2B29B20（12 条业务门槛：地形/effect/建筑/障碍/AI目标/气运/格子类型）
                    //     ⇒ 比裸调 `SetPoint` **更安全**：非法落点会被引擎自己挡掉并给出码。
                    //
                    // ⇒ 这就是引擎自己的"瞬移"动作：零成本 + 维护格子表 + 有业务校验。
                    //   `summon` / `teleport` 都该用它。

                    if (ToolArgs.SameGrid(wub, player))
                        return ToolArgs.WithCounterparty(Ok(new JObject { ["op"] = op, ["target"] = target }), cpName);

                    var mover = player;                        // 被召唤者（缺省=玩家；填 target 则那个 NPC）
                    var destPt = wub.data.unitData.GetPoint(); // 移到发起方所在点

                    int residueBefore = -1, residueAfter = -1;
                    try { residueBefore = mover.data.unitData.residueDay; } catch { }

                    var mvSet = new UnitActionSetPoint(destPt);
                    int setRet = mover.CreateAction(mvSet, true);
                    if (setRet != 0)
                    {
                        try { ModMain.P("[UnitAction] summon 被引擎拒绝 code=" + setRet + "：" + NativeActionService.NativeRefuseWhy(mover, mvSet, "召唤")); } catch { }
                        return Fail(NativeActionService.Refused("召唤", "召唤", mover, mvSet, setRet));
                    }

                    try { residueAfter = mover.data.unitData.residueDay; } catch { }
                    int nx = -1, ny = -1;
                    try { nx = mover.data.unitData.pointX; ny = mover.data.unitData.pointY; } catch { }
                    if (nx != destPt.x || ny != destPt.y)
                        return Fail("召唤未生效：目标(" + destPt.x + "," + destPt.y + ") 实际在(" + nx + "," + ny + ")");

                    try
                    {
                        ModMain.P("[UnitAction] summon(SetPoint) 对手方 → (" + nx + "," + ny + ")"
                                  + " residueDay " + residueBefore + "→" + residueAfter
                                  + (residueBefore == residueAfter ? "（未扣天数 ✓）" : "（★ 天数被改了，异常）"));
                    }
                    catch { }

                    return ToolArgs.WithCounterparty(Ok(new JObject
                    {
                        ["op"] = op, ["target"] = target, ["moved"] = true,
                        ["point"] = new JObject { ["x"] = nx, ["y"] = ny },
                        ["day_cost"] = residueBefore >= 0 && residueAfter >= 0
                                        ? residueBefore - residueAfter : 0
                    }), cpName);
                }

                case "teleport": // NPC 传送到对手方处（对手方缺省=玩家；填 target 则去那个 NPC 那里）
                {
                    // ★ 10-22 重写：改用 `UnitInfoData::SetPoint` 真瞬移，不再走 `UnitActionMoveNPC` ★
                    //
                    // ## 旧实现为什么错（真机实证，`Player.log` 2026-10-22 12:04）
                    //
                    // 旧代码是 `new UnitActionMoveNPC(dest)` + `wub.CreateAction(...)`。
                    // `UnitActionMoveNPC::Init` @0x38E4810 会按**距离**算出 `day`：
                    //     day = Clamp(moveDis², 0, timeCostPara) ÷ ... , 上限 = unit.residueDay
                    // 而 `UnitActionBase::Create` @0x3507F50 **只看 `day > 0` 就扣钱**：
                    //     test edx,edx ; jle 跳过 ; call WorldUnitBase::AddResidueDay
                    // ⇒ 即使 `OnCreate` 走的是**瞬移分支**（`moveComplete` 置 true、坐标立刻改），
                    //   `day>0` 照样把 NPC 当月的 `residueDay` 扣光。
                    //
                    // 真机代价：江宇文 (55,79) → 玩家 (46,27)，曼哈顿距离 61 格 ⇒ `day=15`
                    // ⇒ `residueDay` 15→0 ⇒ 紧接着论道 `Discovery.IsCreate` 里
                    //   `IsDayCost(costDay=5)` 拿 0 比 5 ⇒ 返回 1 ⇒ 被拒、无 UI。
                    // **用户实测「人明明已经到身边了，却论道失败」的全部原因就是这个。**
                    //
                    // ## `SetPoint` 为什么是对的
                    //
                    // `DataUnit.UnitInfoData::SetPoint` @0x2FC32E0 只做坐标 + 格子索引搬家：
                    //     Map::DelGridUnit(旧点, unitID)     ; 从旧格摘掉
                    //     mov [rdi+0x28], pointX             ; +0x28
                    //     mov [rdi+0x2C], pointY             ; +0x2C
                    //     mov byte [rdi+0x138], 1            ; 标记已移动
                    //     Map::AddGridUnit(新点, unitID)     ; 挂到新格
                    //     ret                                ; ★ 全程不碰 residueDay，不扣任何天数
                    // `Map::DelGridUnit` @0x3CE5F40 / `Map::AddGridUnit` @0x3CE5590 均已核实为真名。
                    //
                    // ## 关于旧注释里那句「硬写坐标风险未知」
                    //
                    // 那是 10-18 没读反汇编时的**猜测**，写在 `TeamBattleFollowup.cs` 文件头。
                    // 现在有证据了：`SetPoint` 是**游戏自己的公开 API**，格子索引由它自己维护
                    //（旧猜测担心的"绕过格子索引"不成立）；它唯一"少做"的事是
                    // **不推进游戏日、不扣天数 —— 而传送本来就不该花时间（用户明确要求）**。
                    //
                    // ## 副作用边界（已知且接受）
                    //
                    //   · 不走 `UnitActionMoveNPC`，所以没有"赶路动画/移动中状态"——
                    //     这正是我们要的（旧实现会导致地图徽标延迟几天才出现）；
                    //   · 若该 NPC 当前正被 AI 派去别处（`unitAI` 有在跑的动作），
                    //     强改坐标会让那个动作的落点对不上。原实现同样有这个问题
                    //     （`CreateAction` 会顶掉在跑的动作），不算回归。

                    if (ToolArgs.SameGrid(wub, player))
                    {
                        return ToolArgs.WithCounterparty(Ok(new JObject { ["op"] = op, ["target"] = target }), cpName);
                    }

                    var dest = player.data.unitData.GetPoint();
                    int residueBefore = -1, residueAfter = -1;
                    try { residueBefore = wub.data.unitData.residueDay; } catch { }

                    // 用引擎自己的零成本瞬移动作（`day` 恒 0 ⇒ `Create` 不扣），
                    // 且带 `IsCreate` 的 12 条业务校验（非法落点会被挡掉并给出码）。
                    var mvSet = new UnitActionSetPoint(dest);
                    int setRet = wub.CreateAction(mvSet, true);
                    if (setRet != 0)
                    {
                        try { ModMain.P("[UnitAction] teleport 被引擎拒绝 code=" + setRet + "：" + NativeActionService.NativeRefuseWhy(wub, mvSet, "传送")); } catch { }
                        return Fail(NativeActionService.Refused("传送", "传送", wub, mvSet, setRet));
                    }

                    try { residueAfter = wub.data.unitData.residueDay; } catch { }

                    // 回读复核：坐标真的改了吗（不谎报成功）
                    int nx = -1, ny = -1;
                    try { nx = wub.data.unitData.pointX; ny = wub.data.unitData.pointY; } catch { }
                    if (nx != dest.x || ny != dest.y)
                    {
                        return Fail("传送未生效：目标(" + dest.x + "," + dest.y
                                    + ") 实际在(" + nx + "," + ny + ")");
                    }

                    try
                    {
                        ModMain.P("[UnitAction] teleport(SetPoint) " + target + " → (" + nx + "," + ny + ")"
                                  + " residueDay " + residueBefore + "→" + residueAfter
                                  + (residueBefore == residueAfter ? "（未扣天数 ✓）" : "（★ 天数被改了，异常）"));
                    }
                    catch { }

                    return ToolArgs.WithCounterparty(Ok(new JObject
                    {
                        ["op"] = op, ["target"] = target, ["moved"] = true,
                        ["point"] = new JObject { ["x"] = nx, ["y"] = ny },
                        ["day_cost"] = residueBefore >= 0 && residueAfter >= 0
                                        ? residueBefore - residueAfter : 0
                    }), cpName);
                }

                case "travel": // NPC 自主前往指定城镇/宗门（UnitActionMoveNPC 接受任意坐标，types42 实锤），可在当地等候/邀约碰面
                {
                    string dest = (string)args["destination"] ?? "";
                    string region = (string)args["region"] ?? "";
                    if (string.IsNullOrEmpty(dest))
                        return Fail("缺少 destination（城镇/宗门名，可先 query_world topic=places 查看可去地点）");
                    var b = QueryTool.ResolveBuild(dest, region);
                    if (b == null)
                    {
                        // 解析失败 = 连包含都中不了（拼错/不存在）。带上首字相同或同州的候选
                        //（最多 5 个），模型多半能就地纠正 —— 省掉再跑一轮 query_world 整目录。
                        var hints = new List<string>();
                        foreach (var hb in QueryTool.CollectNamedBuilds())
                        {
                            if (string.IsNullOrEmpty(hb.Name) || hb.Name == dest) continue;
                            bool firstSame = hb.Name[0] == dest[0];
                            bool sameRegion = !string.IsNullOrEmpty(region) && hb.Region != null && hb.Region.Contains(region);
                            if (firstSame || sameRegion)
                            {
                                string tag = hb.Name + (string.IsNullOrEmpty(hb.Region) ? "" : $"（{hb.Region}）");
                                if (!hints.Contains(tag)) hints.Add(tag);
                                if (hints.Count >= 5) break;
                            }
                        }
                        string hintTxt = hints.Count > 0 ? "；名字相近或同州的地点：" + string.Join("、", hints) : "";
                        return Fail($"未找到地点「{dest}」，可先 query_world topic=places 查看可去的城镇/宗门名" + (string.IsNullOrEmpty(region) ? "" : $"（或确认 region={region} 拼写）") + hintTxt);
                    }
                    string where = b.Name + (string.IsNullOrEmpty(b.Region) ? "" : $"（{b.Region}）");
                    int curX = 0, curY = 0;
                    try { curX = wub.data.unitData.pointX; curY = wub.data.unitData.pointY; } catch { }
                    if (curX == b.X && curY == b.Y)
                        return Ok(new JObject { ["op"] = op, ["target"] = target, ["destination"] = b.Name, ["region"] = b.Region, ["already_there"] = true });
                    var mvTravel = new UnitActionMoveNPC(new UnityEngine.Vector2Int(b.X, b.Y));
                    int travelRet = wub.CreateAction(mvTravel, true);
                    if (travelRet != 0)
                    {
                        try { ModMain.P("[UnitAction] travel 被引擎拒绝 code=" + travelRet + "：" + NativeActionService.NativeRefuseWhy(wub, mvTravel, "前往")); } catch { }
                        return Fail(NativeActionService.Refused("前往" + where, "前往", wub, mvTravel, travelRet));
                    }
                    return Ok(new JObject
                    {
                        ["op"] = op, ["target"] = target,
                        ["destination"] = b.Name, ["cat"] = b.Cat, ["region"] = b.Region,
                        ["point"] = new JObject { ["x"] = b.X, ["y"] = b.Y },
                        ["moved"] = true
                    });
                }

                default:
                    return Fail("unsupported movement op: " + op);
            }
        }
    }
}
