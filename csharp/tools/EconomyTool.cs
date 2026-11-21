/// <summary>
/// EconomyTool —— economy_item（赠予）：灵石直写通道 + 道具原生窗 + 多件赠予。
/// （11-19 由 6711 行的 `ToolExecutor.cs` 拆出，11-20 按 Python 的域归并；成员逐字搬移。）
/// 跨类调用一律 `<类名>.<方法>`；`internal` = 仅本程序集可见。
/// 【Python 对应】动作域·财物：赠送 → 买卖（`economy_item` / `trade`） —— 划分以 Python 侧为准，别在这里另立一套分类。
/// </summary>
using System;
using System.Collections.Generic;
using Newtonsoft.Json.Linq;
using static AgentLoopBridge.ToolResult;

namespace AgentLoopBridge
{
    internal static class EconomyTool
    {
        internal static JObject EconomyItem(JObject args)
        {
            // 发起方恒为 harness 注入的 initiator（dialogue_agent._execute_tool_calls）。
            // `target`（09-16）= **接收方**：缺省 = 玩家（老路径逐字节不变），填了才是其他 NPC。
            // 变量 `player` 即"接收方"——下面所有拆栈/转账/入包代码因此原样复用。
            var wub = ToolArgs.BindActor(args, out string initiator, out JObject bindFail);
            if (bindFail != null) return bindFail;
            var player = ToolArgs.ResolveCounterparty(args, out string cpName, out JObject cpFail);
            if (cpFail != null)
                return cpFail;
            if (player == null)
                return Fail("玩家单位不可用");
            bool toNpc = !string.IsNullOrEmpty(cpName);
            string target = "";
            try { target = player.data.unitData.propertyData.GetName(); }
            catch { target = toNpc ? cpName : "玩家"; }
            string via = (string)args["via"] ?? "direct";

            // 解析赠送清单：`items: [{item_name, count}]`（**唯一形态**）。
            // 删掉顶层单件形态（旧协议把 `item`/`item_name` + `count` 直接铺在顶层）：
            // schema 里只有 items 数组，那条路不可达（上一轮全量核对已确认），
            // 留着只会让"参数到底有几种写法"无从判断。
            var items = args["items"] as JArray;
            if (items == null || items.Count == 0)
                return Fail("缺少 items（[{item_name, count}, ...]），可先 inspect_unit(classes=inventory) 查看背包");

            // 预扫描分类：纯灵石 vs 含道具。灵石 2026-09-09 起也弹自制确认窗（与结缘/结义同款
            // ShowConfirm 机制，玩家"我收下了/我不需要"后转账），但两者挂起通道不同：
            // 道具=UnitActionPending（AiActionGate 忙键）；纯灵石=ShowConfirm 占 economy_item
            // 专属窗坑（DramaEconomyItemBase=Mod+100，登记预留首用）——互为不同 key 不冲突。
            bool hasProps = false;
            foreach (var itTok in items)
            {
                var it0 = itTok as JObject;
                if (it0 == null) continue;
                string n0 = (string)it0["item_name"] ?? "";
                if (!string.IsNullOrEmpty(n0) && !n0.Contains("灵石")) { hasProps = true; break; }
            }

            // 赠送道具走原生剧情窗（真机截图实证："我收下了 / 我不需要这个"双选项），工具结果
            // 必须等玩家选择后返回。先占挂起坑（busy 直接失败）——避免拆完背包才发现占不到坑再回滚。
            // AutoConfirm（verify 工程）不占坑，走旧立即路径。纯灵石不占此坑（ShowConfirm 内部自占窗坑）。
            int giveSlot = -1;
            if (hasProps && !toNpc && !ShowDramaService.AutoConfirm && !DramaGate.TryDefer(ModIds.AiActionGate, out giveSlot))
                // 被拒有两种：撞窗 busy（用既有措辞）/ **链在收尾**（用专属措辞，见 `DramaGate.RejectText`）
                return Fail(DramaGate.RejectText("已有一个行动在等待玩家响应（剧情/论道界面未关闭），请先完成当前交互再赠送"));

            // 入口留痕：这条路径此前**除结果外一条日志都没有** —— 真机上"没弹窗"时
            // 无法区分「没走到这里 / 占不到坑 / 动作建不起来 / 动作建了但游戏没播剧情」。
            // 配合下面的 `CreateAction 已返回` 与 `[Give] 预检`，四段一次分开。
            try
            {
                ModMain.P("[Give] 进入：目标=" + target + " 项数=" + items.Count
                          + " 含道具=" + hasProps + " 槽位=" + giveSlot
                          + " 自动确认=" + ShowDramaService.AutoConfirm + " 同格=" + ToolArgs.SameGrid(wub, player));
            }
            catch { }

            // —— 纯灵石：对玩家走自制确认窗；对 NPC 免窗直转（TransferLingshi 本就是"任意两人"的直写） ——
            if (!hasProps)
            {
                if (!toNpc)
                    return GiveLingshiOnly(wub, player, items, via, target);
                var mres = new JArray();
                bool anyMoney = false;
                foreach (var itTok in items)
                {
                    var it = itTok as JObject;
                    if (it == null) continue;
                    string n1 = (string)it["item_name"] ?? "";
                    if (string.IsNullOrEmpty(n1)) continue;
                    int c1 = ToolArgs.SafeInt(it["count"], 1); if (c1 < 1) c1 = 1;
                    string terr1;
                    int actual1 = PropsService.TransferLingshi(wub, player, c1, out terr1);
                    if (actual1 <= 0)
                    {
                        mres.Add(new JObject { ["item"] = n1, ["requested"] = c1, ["actual"] = 0, ["error"] = terr1 });
                        continue;
                    }
                    anyMoney = true;
                    mres.Add(new JObject { ["item"] = n1, ["requested"] = c1, ["actual"] = actual1, ["mode"] = "money", ["from"] = "npc->npc" });
                }
                if (!anyMoney)
                    return Fail("所有赠送项均未达成（详见 items.error）", new JObject { ["items"] = mres });
                // 好感（10-12 接线）：纯灵石免窗直转，同"收礼方对送礼方"（配表 RoleGive）
                int intimMoneyNpc = IntimService.ApplyGiftIntim(player, wub, "灵石·NPC直转");
                var okMoneyNpc = new JObject
                {
                    ["op"] = "give_item", ["target"] = target, ["items"] = mres, ["via"] = via,
                    ["accepted"] = true, ["same_grid"] = ToolArgs.SameGrid(wub, player)
                };
                IntimService.PutIntim(okMoneyNpc, "intim", intimMoneyNpc);
                return ToolArgs.WithCounterparty(Ok(okMoneyNpc), cpName);
            }

            // 分类处理：灵石=货币字段直写（不走 UI）；普通道具=收集到 giveList 一次 UnitActionRoleGive（原生支持 List 多道具）
            var results = new JArray();
            var giveList = new Il2CppSystem.Collections.Generic.List<DataProps.PropsData>();
            int totalMoney = 0;
            bool anyGave = false;

            foreach (var itTok in items)
            {
                var it = itTok as JObject;
                if (it == null) continue;
                string itemName = (string)it["item_name"] ?? "";
                if (string.IsNullOrEmpty(itemName)) continue;
                int count = ToolArgs.SafeInt(it["count"], 1);
                if (count < 1) count = 1;

                // —— 灵石（混合场景）：货币直写即时转账（道具窗结局文案注明"另灵石×N已即时赠达"）
                if (itemName.Contains("灵石"))
                {
                    string terr;
                    int actualMoney = PropsService.TransferLingshi(wub, player, count, out terr);
                    if (actualMoney <= 0)
                    {
                        results.Add(new JObject { ["item"] = itemName, ["requested"] = count, ["actual"] = 0, ["error"] = terr });
                        continue;
                    }
                    totalMoney += actualMoney;
                    anyGave = true;
                    results.Add(new JObject { ["item"] = itemName, ["requested"] = count, ["actual"] = actualMoney, ["mode"] = "money", ["from"] = toNpc ? "npc->npc" : "npc->player" });
                    continue;
                }

                // —— 普通道具：从发起方 NPC 背包按中文名拆栈（定死 NPC→玩家） ——
                string takeErr = null;
                int took = 0;
                try { took = PropsService.TakePropsFromBag(wub, player, itemName, count, giveList, out takeErr); }
                catch (Exception e) { takeErr = e.Message; }
                if (took <= 0)
                    results.Add(new JObject { ["item"] = itemName, ["requested"] = count, ["actual"] = 0, ["error"] = takeErr ?? "背包中没有，可先 inspect_unit(classes=inventory)" });
                else
                {
                    anyGave = true;
                    results.Add(new JObject { ["item"] = itemName, ["requested"] = count, ["actual"] = took, ["mode"] = "props" });
                }
            }

            if (!anyGave)
            {
                if (giveSlot >= 0) DramaGate.Cancel(giveSlot);   // 没发动作：释放占坑
                // 把**每件的具体原因**并进 error 文案：原先只给一句"所有赠送项均未达成"，
                // 模型看不到到底是"背包里没有"还是"这件不可赠送"，只能瞎猜 —— 真机上模型据此
                // 编出了"许是贫道记错了，那枚丹药已不在身上"。字段缺失≠否定，但这里是我们自己
                // 把已知事实丢了。
                var errs = new List<string>();
                foreach (var r in results)
                {
                    var o = r as JObject;
                    if (o == null) continue;
                    string en = (string)o["item"] ?? "?";
                    string ee = (string)o["error"] ?? "";
                    if (ee.Length > 0) errs.Add(en + "：" + ee);
                }
                string detail = errs.Count > 0 ? "（" + string.Join("；", errs.ToArray()) + "）" : "";
                return Fail("所有赠送项均未达成" + detail, new JObject { ["items"] = results });
            }

            // 灵石已在循环内用 CostPropItem(10001)/RewardPropMoney 即时转账（游戏原生 API）。
            // 结果只给原始事实（refused_count/same_grid），收下/拒收叙述由 Python 润色层组装。
            if (giveList.Count > 0)
            {
                // 非玩家接收方（09-16）：**免窗直写** —— 道具在拆栈那一步已经取出（giveList），
                // 这里直接入包并核对；失败原样退回发起方。不建 UnitActionRoleGive：
                // 它的"我收下了/我不需要这个"是给玩家点的，NPC 之间没有这一环。
                // （落刀骨架与 trade 的 ③④ 步同源：AddProps 入包 + 失败回滚。）
                if (toNpc)
                {
                    var dst = player.data.unitData.propData as DataProps;
                    if (dst == null)
                    {
                        PropsService.RollbackGive(wub, giveList);
                        return Fail(cpName + "的背包不可用，道具已原样放回", new JObject { ["items"] = results });
                    }
                    try
                    {
                        for (int i = 0; i < giveList.Count; i++) dst.AddProps(giveList[i]);
                    }
                    catch (Exception e)
                    {
                        PropsService.RollbackGive(wub, giveList);
                        return Fail("道具已从背包取出但送给" + cpName + "失败，已原样放回：" + e.Message,
                                    new JObject { ["items"] = results });
                    }
                    // 好感（10-12 接线）：免窗直写没有原生 UnitActionRoleGive ⇒ 副作用我们自己补。
                    // 方向同原生：**收礼方（对手方）对送礼方（你）** +（配表 RoleGive.closeParaA）。
                    int intimToNpc = IntimService.ApplyGiftIntim(player, wub, "道具·NPC直转");
                    var okToNpc = new JObject
                    {
                        ["op"] = "give_item", ["target"] = target, ["items"] = results, ["via"] = via,
                        ["accepted"] = true, ["refused_count"] = 0, ["same_grid"] = ToolArgs.SameGrid(wub, player)
                    };
                    IntimService.PutIntim(okToNpc, "intim", intimToNpc);
                    return ToolArgs.WithCounterparty(Ok(okToNpc), cpName);
                }

                // ── 多件道具（≥2）：**原生 UnitActionRoleGive 一次只处理一件** ⇒ 走自制确认窗 + 直写 ──
                //
                // 真机实锤（09-17，用户报"只有第一件生效"）：
                //   · 两次赠送的引擎自证计数：`giveProps=4 → receiveProps=1 refuseProps=0`、
                //     `giveProps=3 → receiveProps=1 refuseProps=0`（只认下**一件**）；
                //   · 签名里那个**单数**字段 `UnitActionRoleGive.propsData`（列表只是账本：
                //     `OnEnd` 里两个闭包 `__OnEnd_b__11_0/1(Int32)` 都是在找"这一件"）；
                //   · 原生剧情 21701 的文案本来就是单数（配表原文）：
                //     105675「我这里有一个{0}准备赠于你，你需要此物吗？」+ 选项「我收下了 / 我不需要这个」；
                //   · 没送出去的那些由原生私有 `FeedbackAllProps()` 退回发起方（不丢，但也没送到）。
                // ⇒ 多件只能走我们自己的窗：正文列全清单、点一次收下**全部**到账；
                //   副作用按配表复刻（好感 + 角色日志，见 GivePropsByWindow），并各自留探针行。
                // 1 件仍走原生（零风险、原生立绘/文案都在，副作用天然精确）。
                if (giveList.Count > 1)
                {
                    if (ShowDramaService.AutoConfirm)
                        return GivePropsByWindow(wub, player, giveList, results, target, via, ToolArgs.GetIntimBetween(player, wub));
                    // 交互坑交给窗自己的 key（DramaEconomyItemBase）——与纯灵石那条路同款，
                    // 不占 AiActionGate（那是"AI 动作挂起"的键，这里没有 AI 动作）。
                    if (giveSlot >= 0) { DramaGate.Cancel(giveSlot); giveSlot = -1; }
                    string giverName = UnitIdentity.Name(wub, "对方");
                    string body = $"{giverName}愿赠你：{GiveItemsText(giveList)}"
                                  + $"（{GiveViaNote(wub, player, via)}），是否一并收下？";
                    int intimBeforeMulti = ToolArgs.GetIntimBetween(player, wub);
                    JObject opened = NativeActionService.Claimed(wub, () => ShowDramaService.ShowConfirm(
                        ModIds.DramaEconomyItemBase,
                        player, wub,
                        body,
                        "我收下了", "我不需要这个",
                        onOk: () => GivePropsByWindow(wub, player, giveList, results, target, via, intimBeforeMulti),
                        okTip: null,
                        noTip: "玩家没有收下赠礼",
                        // ★ 方案 A（09-24）：本分支是**先拆栈、再问**（要列清单就得先把道具挪到手上），
                        // 所以"玩家不收"这条出口**必须有人放回** —— 否则那批东西凭空消失（原本的洞：
                        // `ShowConfirm` 只有 onOk，拒绝路径只回一句 error，没人还原；单件原生那条不受影响，
                        // 它的退还是引擎自己的 `FeedbackAllProps()`）。
                        // `RollbackGive` 幂等（放完会 `Clear()`），只会在"确实拆出来过"时才会被执行。
                        onNo: () => PropsService.RollbackGive(wub, giveList)));
                    if (opened == null || opened["__pending__"]?.Value<bool>() != true)
                    {
                        // 窗没开起来（撞窗/异常）：道具还在我们手里，**原样放回**，绝不静默丢
                        PropsService.RollbackGive(wub, giveList);
                        return Fail("多件赠送的确认窗没能打开，道具已原样放回背包",
                                    new JObject { ["items"] = results });
                    }
                    return opened;
                }

                var giveAction = new UnitActionRoleGive(player, giveList);

                // 根因修复（真机探针定案）
                //   `isCheckUnitProps` 默认为 **true** = 动作创建时按 `soleID` **回查发起方背包**
                //   是否仍持有这些道具。而本方法是**先 DelProps 拆栈、再建动作**，于是：
                //     · 送**整栈**（如筑基丹×1，全拿走）→ `soleID` 已从背包消失 → 回查得 null →
                //       `IsCreate` 内部 `NullReferenceException` → **动作建不起来 → 游戏不播剧情窗**
                //       → 工具挂 120s 被 Python 判超时，而道具已不在背包里 → **静默丢道具**；
                //     · 送**部分**（如蓄力丹 9 送 1）→ `soleID` 还在（剩 8）→ 回查通过 → 窗正常弹。
                //   这就是真机上「送丹药可以、送筑基丹不行」的全部原因 —— 差别只在是不是整栈。
                //   真机证据（探针行，15:00）：
                //     `[Give] 预检 拆出道具数=1 giveProps=1 isCheckUnitProps=True IsCreate=抛异常`
                //     `[Give] IsCreate 预检抛异常: System.NullReferenceException`
                //   置 false 是**该字段存在的意义**：调用方已自行拆栈时，就不该再回查背包。
                //   安全性：我方在拆栈**之前**已用 `PropsItemCanGive` 校验过每一件，且实物就在
                //   `giveProps` 手里（探针证实 giveProps=1）—— 跳过这次重复回查不会放过非法赠送。
                giveAction.isCheckUnitProps = false;

                // ★ 09-24 定性结论：**免检已删除**（曾一度设 `isCreateCall = true` 绕过引擎那道门）★
                // 两轮真机对照（开关 `_diag_give_recheck.txt` 的开关实验，探针已退役）：
                //   · 默认轮（设免检）：`CreateAction 返回码=0` → 原生窗弹出 → `receiveProps=1` ✓
                //   · recheck 轮（**不设免检**，让引擎自己过门）：`实例.unit=(null) isCreateCall=False`，
                //     整栈 6 枚全给，`CreateAction 返回码=0` → 窗照样弹出 → `receiveProps=1` ✓
                // ⇒ **引擎的门是好的**：它在**自己的时机**（`CreateAction` 内部）调 `IsCreate`，
                //   那时发起方已绑；而我们手动调 `giveAction.IsCreate(false)` 只能拿到
                //   `NullReferenceException`（实例 `unit=(null)`，依赖 OnCreate 期才就绪的瞬态字段）
                //   —— 纯噪声，问得太早。
                // ⇒ 于是两件一起删：① 不再设 `isCreateCall`（免检 = 主动放弃引擎校验，且让返回码**永远为 0**
                //      ⇒ "门说了什么"我们永远听不到）；② 不再自己调 `IsCreate` 预检（只会 NRE，还可能
                //      顺手把 `isCreateCall` 置真、把免检偷偷打开 —— 09-17 关系强拆事故的同一形状）。
                // 现在**唯一判据 = `CreateAction` 的返回码**（引擎在正确时机给出的真裁决）：
                //   0 = 已创建（窗会弹）；非 0 = 拒绝且什么都没做 → 回滚 + 如实报（见下方 giveRet 分支）。
                // 我方在拆栈**之前**已逐件 `PropsItemCanGive` 校验过、实物就在 `giveProps` 手里，
                // 所以让引擎再过一次门不会放过非法赠送（`isCheckUnitProps = false` 已处理
                // "整栈被拆走后按 soleID 回查不到"那条老 NRE）。

                if (giveSlot >= 0)
                {
                    // 好感探针（09-17）：原生 OnEnd 的公式在 native 体内看不到，而多件直写那条路
                    // 必须自己补好感 —— 所以在这里量：收礼方对送礼方的前后值。真机送一次单件即可核对
                    // `GiftIntimPerItem`（配表 RoleGive.closeParaA=15）对不对。
                    int intimBeforeNative = IntimService.IntimBetween(player, wub);
                    // 完成挂起：结果由 Harmony postfix 在原生 OnEnd() 后读 receive/refuseProps 落定字段
                    UnitActionPending.Register(giveAction.Pointer, giveSlot, "give_item", DramaNativeClaim.UidOf(wub), () =>
                    {
                        int recv = -1, refused = -1;
                        try { recv = giveAction.receiveProps != null ? giveAction.receiveProps.Count : -1; } catch { }
                        try { refused = giveAction.refuseProps != null ? giveAction.refuseProps.Count : -1; } catch { }
                        try { UnityEngine.Debug.Log("[UnitAction] give_item receiveProps=" + recv + " refuseProps=" + refused + " slot=" + giveSlot); } catch { }
                        try
                        {
                            // 10-15：**不再"补差"**（写入语义改为"引擎换算、我们只回读"）——这里只如实量出
                            // 原生实得（含 RoleClose 系数衰减），供真机核对配表基准值。
                            int want = IntimService.GiftIntimValue();
                            int after = IntimService.IntimBetween(player, wub);
                            int got = intimBeforeNative >= 0 ? after - intimBeforeNative : int.MinValue;
                            ModMain.P("[Give] 好感探针 原生赠礼 收礼方对送礼方 " + intimBeforeNative + "→" + after
                                      + "（Δ" + got + "；配表 closeParaA=" + want
                                      + (recv > 0 && got != int.MinValue && got != want ? "，差额由 RoleClose 系数换算" : "")
                                      + "）"
                                      + " giveProps=" + giveList.Count + " receiveProps=" + recv);
                        }
                        catch { }
                        bool accepted = refused <= 0;
                        return Ok(new JObject { ["op"] = "give_item", ["target"] = target, ["items"] = results, ["via"] = via, ["accepted"] = accepted, ["accepted_count"] = Math.Max(recv, 0), ["refused_count"] = Math.Max(refused, 0), ["same_grid"] = ToolArgs.SameGrid(wub, player) });
                    });
                }
                // 返回码 = **引擎的唯一真裁决**（免检与自建预检都已删，见上）：非 0 = 拒绝且什么都没做。
                int giveRet;
                try { giveRet = wub.CreateAction(giveAction, true); }
                catch (Exception e)
                {
                    // 栈首行留证：09-17 那次只记了 Message、栈丢了，导致"门到底卡在哪"拖到今天才定性。
                    try { ModMain.P("[Give] CreateAction 抛异常: " + e.Message + "｜栈首行=" + NativeActionService.ExcStack1(e)); } catch { }
                    if (giveSlot >= 0) { UnitActionPending.Remove(giveAction.Pointer); DramaGate.Cancel(giveSlot); }
                    PropsService.RollbackGive(wub, giveList); return Fail("发起赠送失败，已回滚背包: " + e.Message, new JObject { ["items"] = results });
                }
                if (giveRet != 0)
                {
                    if (giveSlot >= 0) { UnitActionPending.Remove(giveAction.Pointer); DramaGate.Cancel(giveSlot); }
                    PropsService.RollbackGive(wub, giveList);
                    try { ModMain.P("[UnitAction] give_item 被引擎拒绝 code=" + giveRet + "：" + NativeActionService.NativeRefuseWhy(wub, giveAction, "赠予")); } catch { }
                    return Fail(NativeActionService.Refused("赠送", "赠予", wub, giveAction, giveRet), new JObject { ["items"] = results });
                }
                // 留痕：返回码 0 **≠ 窗一定弹了**。真机两轮（09-24）都是 0 且窗都弹了，但语义上仍要分开：
                // 这一行之后若无原生窗日志、也无 OnEnd，就是"动作建了但链没起来"（120s 后 Python 判超时）。
                try { ModMain.P("[Give] CreateAction 返回码=0，等原生窗…"); } catch { }
                if (giveSlot >= 0) return DramaGate.MakePendingMarker(giveSlot);   // 挂起：等玩家"收下/拒绝"后按结局收口
            }
            // AutoConfirm（verify 工程）旧立即路径：直写已完成，返回纯数据。
            // 10-12：混合赠送里"道具全军覆没、只有灵石转成功"也会走到这里（giveList 为空）——
            // 灵石也是礼 ⇒ 补一次赠礼好感；有道具的路径（原生动作/多件窗）各自已处理，不在这里重复加。
            var doneObj = new JObject
            {
                ["op"] = "give_item", ["target"] = target, ["items"] = results, ["via"] = via,
                ["accepted"] = true, ["same_grid"] = ToolArgs.SameGrid(wub, player)
            };
            if (giveList.Count == 0)
                IntimService.PutIntimWithAfter(doneObj, "intim", IntimService.ApplyGiftIntim(player, wub, "灵石·混合"), player, wub);
            return Ok(doneObj);
        }

        /// <summary>
        /// 纯灵石赠送（items 全为灵石项）：弹自制确认窗（ShowConfirm/UICustomDramaDyn，与结缘/结义
        /// 同款机制），玩家点「我收下了」后才执行货币直写转账——2026-09-09 前为无 UI 立即转账。
        /// 占坑走 economy_item 专属段 DramaEconomyItemBase（Mod+100，登记预留首用，不与新工具撞段）；
        /// verify 工程 AutoConfirm 直通转账（结果形态与旧版一致）。弹窗前预检余额（全不足直接
        /// Fail 不弹窗）；点选后按当时余额重新结算（min 兜底+补偿回滚）。
        /// </summary>
        internal static JObject GiveLingshiOnly(WorldUnitBase npc, WorldUnitBase player, JArray items, string via, string target)
        {
            try
            {
                // 预检：只读余额不转账（弹窗前发现全不足，避免玩家点"收下"才失败）
                int totalActual = 0;
                var preview = new JArray();
                foreach (var itTok in items)
                {
                    var it = itTok as JObject;
                    if (it == null) continue;
                    string itemName = (string)it["item_name"] ?? "";
                    if (string.IsNullOrEmpty(itemName)) continue;
                    int count = ToolArgs.SafeInt(it["count"], 1); if (count < 1) count = 1;
                    int have = PropsService.ReadLingshiHeld(npc);
                    if (have < 0)
                    {
                        preview.Add(new JObject { ["item"] = itemName, ["requested"] = count, ["actual"] = 0, ["error"] = "读不到发起方的灵石持有数（GetPropsNum 失败），该项未赠送" });
                        continue;
                    }
                    int actual = Math.Min(count, have);
                    if (actual <= 0)
                    {
                        preview.Add(new JObject { ["item"] = itemName, ["requested"] = count, ["actual"] = 0, ["error"] = $"发起方灵石不足（持有 {have}）" });
                        continue;
                    }
                    totalActual += actual;
                    preview.Add(new JObject { ["item"] = itemName, ["requested"] = count, ["actual"] = actual, ["mode"] = "money", ["from"] = "npc->player" });
                }
                if (totalActual <= 0)
                    return Fail("所有赠送项均未达成（详见 items.error）", new JObject { ["items"] = preview });

                string npcName = "对方";
                try { npcName = (string)npc.data.unitData.propertyData.GetName(); } catch { }
                string viaNote = via == "letter"
                    ? "以信件方式赠达"
                    : (ToolArgs.SameGrid(npc, player) ? "当面赠达" : "异地以传讯方式赠达");

                if (ShowDramaService.AutoConfirm)
                {
                    // verify 直通：立即转账，结果形态与旧版纯灵石一致（无 op/accepted 字段）
                    var results = new JArray(); int got = 0; bool any = false;
                    foreach (var itTok in items)
                    {
                        var it = itTok as JObject;
                        if (it == null) continue;
                        string itemName = (string)it["item_name"] ?? "";
                        if (string.IsNullOrEmpty(itemName)) continue;
                        int count = ToolArgs.SafeInt(it["count"], 1); if (count < 1) count = 1;
                        string terr;
                        int actual = PropsService.TransferLingshi(npc, player, count, out terr);
                        if (actual <= 0) { results.Add(new JObject { ["item"] = itemName, ["requested"] = count, ["actual"] = 0, ["error"] = terr }); continue; }
                        got += actual; any = true;
                        results.Add(new JObject { ["item"] = itemName, ["requested"] = count, ["actual"] = actual, ["mode"] = "money", ["from"] = "npc->player" });
                    }
                    if (!any) return Fail("所有赠送项均未达成（详见 items.error）", new JObject { ["items"] = results });
                    // 好感（10-12 接线）：灵石也是礼 ⇒ 收礼方（玩家）对送礼方 +（配表 RoleGive）
                    int intimDirect = IntimService.ApplyGiftIntim(player, npc, "灵石直通");
                    var okDirect = new JObject { ["op"] = "give_item", ["target"] = target, ["items"] = results, ["via"] = via, ["accepted"] = true, ["same_grid"] = ToolArgs.SameGrid(npc, player) };
                    IntimService.PutIntimWithAfter(okDirect, "intim", intimDirect, player, npc);
                    return Ok(okDirect);
                }

                // 正常路径：自制确认窗（正文按预检到的实际可赠数描述；点选后按当时余额重新结算）
                string ask = $"{npcName}愿赠你灵石×{totalActual}（{viaNote}），是否收下？";
                return NativeActionService.Claimed(npc, () => ShowDramaService.ShowConfirm(
                    ModIds.DramaEconomyItemBase,
                    player, npc,
                    ask,
                    "我收下了", "我不需要",
                    onOk: () =>
                    {
                        var results = new JArray(); int got = 0; bool any = false;
                        foreach (var itTok in items)
                        {
                            var it = itTok as JObject;
                            if (it == null) continue;
                            string itemName = (string)it["item_name"] ?? "";
                            if (string.IsNullOrEmpty(itemName)) continue;
                            int count = ToolArgs.SafeInt(it["count"], 1); if (count < 1) count = 1;
                            string terr;
                            int actual = PropsService.TransferLingshi(npc, player, count, out terr);
                            if (actual <= 0) { results.Add(new JObject { ["item"] = itemName, ["requested"] = count, ["actual"] = 0, ["error"] = terr }); continue; }
                            got += actual; any = true;
                            results.Add(new JObject { ["item"] = itemName, ["requested"] = count, ["actual"] = actual, ["mode"] = "money", ["from"] = "npc->player" });
                        }
                        if (!any) return Fail("转账时发起方灵石已不足", new JObject { ["items"] = results });
                        try { UITipItem.AddTip("已收下灵石×" + got, 2f); } catch { }
                        // 好感（10-12 接线）：收下灵石＝收礼 ⇒ 收礼方（玩家）对送礼方 +（配表 RoleGive）
                        int intimWindow = IntimService.ApplyGiftIntim(player, npc, "灵石窗");
                        var okWindow = new JObject { ["op"] = "give_item", ["target"] = target, ["items"] = results, ["via"] = via, ["accepted"] = true, ["same_grid"] = ToolArgs.SameGrid(npc, player) };
                        IntimService.PutIntimWithAfter(okWindow, "intim", intimWindow, player, npc);
                        return Ok(okWindow);
                    },
                    okTip: null,
                    noTip: "玩家拒收了灵石"));
            }
            catch (Exception e)
            {
                return Fail("灵石赠送异常: " + e.Message);
            }
        }

        // ============ 多件赠送（09-17）：自制窗 + 直写 + 副作用复刻 ============

        // 赠礼好感值已上收到「好感写入层」（见 IntimBetween 下方；**10-15 口径：引擎换算、我们只回读**）：
        //   `GiftIntimValue()` 读配表 `RoleGive.closeParaA`（回退 15）并以 `closeOnceAddMax` 封顶；
        //   写入走 `ApplyGiftIntim`（只传请求值，实得由 RoleClose 系数决定，我们如实报出）。两侧探针照旧。

        /// <summary>清单文案："六品化瘀丹×10、六品蓄力丹×3"（窗正文用；名字走 LS，与背包同口径）。</summary>
        internal static string GiveItemsText(Il2CppSystem.Collections.Generic.List<DataProps.PropsData> giveList)
        {
            var sb = new System.Text.StringBuilder();
            try
            {
                for (int i = 0; i < giveList.Count; i++)
                {
                    var p = (dynamic)giveList[i];
                    string nm = "";
                    try { nm = (string)p.propsItem.name; } catch { }
                    try { nm = GameTool.LS(nm); } catch { }
                    int cnt = 1;
                    try { cnt = (int)p.propsCount; } catch { }
                    if (i > 0) sb.Append("、");
                    sb.Append(nm).Append("×").Append(cnt);
                }
            }
            catch { }
            return sb.ToString();
        }

        /// <summary>送达方式（与纯灵石窗、结果文案同一口径）。</summary>
        internal static string GiveViaNote(WorldUnitBase giver, WorldUnitBase receiver, string via)
        {
            if (via == "letter") return "以信件方式赠达";
            return ToolArgs.SameGrid(giver, receiver) ? "当面赠达" : "异地以传讯方式赠达";
        }

        /// <summary>
        /// 多件赠礼的落刀（自制窗"我收下了"之后）：逐件入包 → 复刻原生副作用 → 报**引擎真值**。
        /// 失败一律 `RollbackGive` 原样放回（这批道具是拆栈拆出来的，丢了就是静默吞道具）。
        /// </summary>
        internal static JObject GivePropsByWindow(
            WorldUnitBase giver, WorldUnitBase receiver,
            Il2CppSystem.Collections.Generic.List<DataProps.PropsData> giveList,
            JArray results, string target, string via, int intimBefore)
        {
            var dst = receiver.data.unitData.propData as DataProps;
            if (dst == null)
            {
                PropsService.RollbackGive(giver, giveList);
                return Fail("接收方的背包不可用，道具已原样放回", new JObject { ["items"] = results });
            }
            try
            {
                for (int i = 0; i < giveList.Count; i++) dst.AddProps(giveList[i]);
            }
            catch (Exception e)
            {
                PropsService.RollbackGive(giver, giveList);
                return Fail("道具已从背包取出但赠送失败，已原样放回：" + e.Message,
                            new JObject { ["items"] = results });
            }

            GiveSideEffects(giver, receiver, giveList, intimBefore);
            try { UITipItem.AddTip("已收下赠礼", 2f); } catch { }
            return Ok(new JObject
            {
                ["op"] = "give_item", ["target"] = target, ["items"] = results, ["via"] = via,
                ["accepted"] = true,
                ["accepted_count"] = giveList.Count,     // 引擎真值口径与原生一致（= 收下的**栈**数）
                ["refused_count"] = 0,
                ["delivered_all"] = true,               // 多件直写：全部到账（原生一次只处理一件）
                ["same_grid"] = ToolArgs.SameGrid(giver, receiver),
            });
        }

        /// <summary>
        /// 复刻原生 `UnitActionRoleGive.OnEnd` 的两件副作用（我们自己落刀 ⇒ 必须自己补）：
        ///   ① 好感：`AddIntim`（配表 `closeParaA=15`）——**只加"收礼方对送礼方"**（收礼的语义），
        ///      并留探针行打印两个方向的前后值（原生公式不可见 ⇒ 靠真机核对对齐）；
        ///   ② 角色日志：原生 key `eventLogRoleGive21311`（送礼方视角）/ `21312`（收礼方视角），
        ///      参数 `{itemLink<propsID>}`；`{name|A}`/`{name|B}` 由 `UnitConditionData(送礼方,收礼方)` 提供。
        /// 全 try/catch：副作用失败**不影响**"道具已送到"这个事实，但要留痕。
        /// </summary>
        internal static void GiveSideEffects(
            WorldUnitBase giver, WorldUnitBase receiver,
            Il2CppSystem.Collections.Generic.List<DataProps.PropsData> giveList, int intimBefore)
        {
            // （10-15 复核：原 giverId/recvId 两个局部变量自写入层接管后已无读者，删除。）

            // ① 好感：收礼方对送礼方（配表 RoleGive → 走写入层；实得由引擎换算，探针如实记录）
            try
            {
                int want = IntimService.GiftIntimValue();
                int got = IntimService.ApplyGiftIntim(receiver, giver, "多件窗");
                int rAfter = IntimService.IntimBetween(receiver, giver);
                int gBefore = IntimService.IntimBetween(giver, receiver);
                int rBefore = intimBefore;                 // 探针传进来的是"收礼方对送礼方"的旧值
                try
                {
                    ModMain.P("[Give] 好感探针 多件直写 收礼方对送礼方 " + rBefore + "→" + rAfter
                              + "（请求 +" + want + "＝配表 closeParaA；实得 " + got + "）"
                              + (rBefore >= 0 && got != want ? "｜差额由 RoleClose 系数换算（好感越高实得越少）" : "")
                              + "；送礼方对收礼方 " + gBefore + "（未动）");
                }
                catch { }
            }
            catch (Exception e) { try { ModMain.P("[Give] 好感写入失败（道具已送到，不回收）: " + e.Message); } catch { } }

            // ② 角色日志（原生文案：21311 送礼方 / 21312 收礼方）
            try
            {
                int propsID = 0;
                try { propsID = (int)((dynamic)giveList[0]).propsID; } catch { }
                var vals = new string[] { "{itemLink" + propsID + "}" };
                var cd = new UnitConditionData(giver, receiver);
                g.world.unitLog.AddLogData(giver, "eventLogRoleGive21311", vals, cd);
                g.world.unitLog.AddLogData(receiver, "eventLogRoleGive21312", vals, cd);
            }
            catch (Exception e) { try { ModMain.P("[Give] 角色日志写入失败（道具已送到）: " + e.Message); } catch { } }
        }

        // ============================== trade：双向交易 ==============================

        /// <summary>
        /// trade —— 双向买卖：卖方背包的道具 → 买方，买方的灵石 → 卖方。
        ///
        /// 用户拍板：**不驱动游戏原生交易系统**（那要开原版交易 UI、两边都进交易态），
        /// 实质就是改字段：
        ///   · 道具用引擎自己的 `DataProps.DelProps(soleID,n)` / `AddProps(PropsData)` 拆栈入栈
        ///     （**绝不手写 propsCount** —— 那是"把整栈改成 n"不是"取出 n 个"，见 `TakePropsFromBag` 的坑注）；
        ///   · 灵石用 ID=10001 的道具栈承载：`WorldUnitData.CostPropItem(10001,…)` 扣 /
        ///     `RewardPropMoney(…)` 加（与 economy_item 转账同源）。
        ///
        /// 双方都不限于玩家：`seller`/`buyer` 都走 `UnitLookup.Resolve`（玩家真名 /「玩家」/ unitID 均可）。
        /// 确认窗**复用 economy_item 段** `ModIds.DramaEconomyItemBase` —— DramaGate 全局同时只允许一个
        /// 挂起窗，且壳条目的正文/选项文字全部运行时覆盖，故无需新增 ModExcel 段（8 段登记表不动）。
        ///
        /// ★ **NPC↔NPC 免窗直落刀**（09-17 提案制）：双方都不是玩家时跳过确认窗、直接执行 ——
        ///   窗存在的唯一理由是"取玩家的一次点击"，玩家不在交易里就没有这一环（弹了没人点，
        ///   整轮还会挂到 120s 超时）。两边的同意由 Python 提案制取得：提出方报价 →
        ///   对手方**按同条款回发**，回发的这一路才走到这里。
        ///
        /// 三段式：**预校验（全只读）→ 弹窗 → 带补偿落刀**。
        ///   · 预校验不过就 Fail 且**不弹窗** —— 避免"弹了窗、玩家点了成交、才发现做不了"。
        ///   · 数量必须**整笔满足**（与 economy_item 赠送的"有多少送多少"不同）：买卖是定价交易，
        ///     少给货却收全款不成立；只想要一部分就让模型改小 count 重发。
        ///   · 落刀顺序刻意选成「拆栈 → 灵石 → 入包」：前两步失败时道具都还在手上，
        ///     `RollbackGive` 能无损退回卖方；第三步失败时钱和货的补偿都还是对称可做的。
        ///     反序（先入包）则失败时得从买方"抠回来"，栈可能已被合并、口径不稳。
        /// </summary>
        internal static JObject Trade(JObject args)
        {
            string sellerId = (string)args["seller"] ?? "";
            string buyerId = (string)args["buyer"] ?? "";
            string itemName = (string)args["item"] ?? "";   // schema 只有 item（旧 item_name 别名 09-13 删）
            int count = ToolArgs.SafeInt(args["count"], 1);
            int price = ToolArgs.SafeInt(args["price"], 0);
            if (count < 1) count = 1;
            if (price < 0) price = 0;

            if (string.IsNullOrEmpty(sellerId) || string.IsNullOrEmpty(buyerId))
                return Fail("trade 需要 seller（卖方，出货方）与 buyer（买方，付灵石方）");
            if (string.IsNullOrEmpty(itemName))
                return Fail("trade 需要 item（道具中文名，取自 inspect_unit(classes=inventory) 的 props）");
            if (itemName.Contains("灵石"))
                return Fail("灵石是货币不是商品：item 只能填背包道具。单纯给灵石请用 economy_item");

            // ★ 名字**旁边带着精确 id**（`seller_id` / `buyer_id`，Python 侧群聊提案闸门
            //   从房间身份表翻译来的；模型永远看不到这两个键）。有它就不再按名全图扫 ——
            //   重名时按名会命中第一个，**不报错、不缺字段**，只是把货和灵石换到另一个人手上。
            //   缺项（单聊、或极少数没登记的人）⇒ 回落按名，与本功能上线前逐字一致。
            var seller = ToolArgs.ResolveArgExact(args, "seller", sellerId);
            if (seller == null)
            {
                // 死者兜底（10-15 待办②）：卖方已故 ⇒ 带死亡事实报错
                string dsn = UnitSnapshot.DeadNotice((string)args["seller_id"] ?? "", sellerId);
                if (dsn != null) return Fail(dsn);
                return Fail("未找到卖方 " + sellerId);
            }
            var buyer = ToolArgs.ResolveArgExact(args, "buyer", buyerId);
            if (buyer == null)
            {
                string dbn = UnitSnapshot.DeadNotice((string)args["buyer_id"] ?? "", buyerId);
                if (dbn != null) return Fail(dbn);
                return Fail("未找到买方 " + buyerId);
            }

            string sellerUid = "", buyerUid = "";
            try { sellerUid = DramaNativeClaim.UidOf(seller); } catch { }
            try { buyerUid = DramaNativeClaim.UidOf(buyer); } catch { }
            if (!string.IsNullOrEmpty(sellerUid) && sellerUid == buyerUid)
                return Fail($"卖方与买方是同一人（{sellerId}），无法与自己交易");

            // ★ **不许替他人做主**（09-17 补上这道闸）：买卖双方必须有一方是**发起方**。
            //   工具描述里一直写着这条，但代码里从来没有判过 —— 群聊有提案闸门兜着，
            //   单聊没有：模型理论上能让两个第三人擅自成交（把甲的货卖给乙、丙替他们定价）。
            //   判据用 **unitID 字符串**而不是对象引用：IL2CPP 下同一个人的两次 GetUnit
            //   未必是同一个托管包装（同文件既有的"卖方与买方是同一人"也是这么比的）。
            //   拿不到发起方身份（unitID 为空）时**放行** —— 那道情形下判不出来，宁可照旧。
            string actorUid = "";
            try { actorUid = DramaNativeClaim.UidOf(ToolArgs.ResolveActor(args)); } catch { }
            if (!string.IsNullOrEmpty(actorUid)
                && actorUid != sellerUid && actorUid != buyerUid)
                return Fail("买卖必须有一方是你自己（" + ((string)args["initiator"] ?? "")
                            + "）：不许替他人做主。你是卖方就把 buyer 填对方，你是买方就把 seller 填对方");

            string sellerName = UnitIdentity.Name(seller, sellerId), buyerName = UnitIdentity.Name(buyer, buyerId);

            // —— 预校验（全只读，绝不碰背包/货币）——
            var sellerDp = seller.data.unitData.propData as DataProps;
            var buyerDp = buyer.data.unitData.propData as DataProps;
            if (sellerDp == null) return Fail($"卖方 {sellerName} 的背包不可用");
            if (buyerDp == null) return Fail($"买方 {buyerName} 的背包不可用");

            string soleID = FindStackSoleID(sellerDp, itemName, out int have, out int propsID);
            if (string.IsNullOrEmpty(soleID))
                return Fail($"卖方 {sellerName} 背包里没有「{itemName}」（可先 inspect_unit(classes=inventory) 确认）");
            if (have < count)
                return Fail($"卖方 {sellerName} 只有「{itemName}」×{have}，不够 ×{count}；买卖必须整笔满足——改小 count 或换卖方");

            int buyerMoneyBefore = PropsService.ReadLingshiHeld(buyer);
            if (buyerMoneyBefore < 0)
                return Fail($"读不到买方 {buyerName} 的灵石持有数（GetPropsNum 失败）—— 无法确认支付能力，本次不交易");
            if (price > buyerMoneyBefore)
                return Fail($"买方 {buyerName} 灵石不足（持有 {buyerMoneyBefore}，需付 {price}）");

            // 可交易性预检（与拆栈同口径，即 TakePropsFromBag 内部那道 PropsItemCanGive）：
            // 刻意放到**弹窗之前** —— 否则会变成"玩家点了成交、才发现这东西不能转手"。
            // 该 API 抛异常时不拦（按可交易处理），执行期 TakePropsFromBag 还会再判一次。
            try
            {
                var sample = sellerDp.GetProps(soleID);
                if (sample != null && !UnitActionRoleGive.PropsItemCanGive(seller, buyer, sample))
                    return Fail($"「{itemName}」不可交易（游戏判定该道具不可转手：绑定/任务/装备中等）");
            }
            catch { }

            int sellerMoneyBefore = PropsService.ReadLingshiHeld(seller);
            int buyerItemBefore = CountOf(buyerDp, propsID);
            string deal = price > 0 ? $"作价 {price} 灵石" : "不取分文";

            // 落刀：拆栈 → 灵石 → 入包（钱货双向补偿）。定成**局部委托**而不是内联进 onOk ——
            // **NPC↔NPC 免窗直落刀**与窗口路径必须走同一份实现（复制一份 = 两处逐行漂移，
            // 回滚口径迟早对不上）。
            System.Func<JObject> doDeal = () =>
                {
                    // ① 卖方拆栈：复用给赠那条已验过的两段式（先规划后落刀，中途失败自带回滚）
                    var takenList = new Il2CppSystem.Collections.Generic.List<DataProps.PropsData>();
                    string takeErr = null;
                    int got = 0;
                    try { got = PropsService.TakePropsFromBag(seller, buyer, itemName, count, takenList, out takeErr); }
                    catch (Exception e) { takeErr = e.Message; }
                    if (got <= 0)
                        return Fail($"卖方 {sellerName} 取不出「{itemName}」：{takeErr ?? "背包中没有"}");
                    if (got < count)   // 竞态：预校验后背包变了 → 整笔作废，不留半截交易
                    {
                        PropsService.RollbackGive(seller, takenList);
                        return Fail($"卖方 {sellerName} 只有「{itemName}」×{got}，不够 ×{count}，交易作废（道具未动）");
                    }

                    // ② 灵石结算（TransferLingshi 自带"扣了没到账就退回买方"的补偿）
                    int paid = 0;
                    if (price > 0)
                    {
                        string terr;
                        try { paid = PropsService.TransferLingshi(buyer, seller, price, out terr); }
                        catch (Exception e) { paid = 0; terr = e.Message; }
                        if (paid != price)
                        {
                            PropsService.RollbackGive(seller, takenList);   // 道具还在手上 → 无损退回
                            return Fail($"灵石结算未完成（应付 {price} 实付 {paid}{(string.IsNullOrEmpty(terr) ? "" : "：" + terr)}），道具已退回卖方");
                        }
                    }

                    // ③ 买方入包。失败 → 钱与货双向补偿（两者都还对称可做）
                    string undoWhy = null;
                    try
                    {
                        for (int i = 0; i < takenList.Count; i++) buyerDp.AddProps(takenList[i]);
                    }
                    catch (Exception e) { undoWhy = "买方入包失败: " + e.Message; }

                    // ④ 结果核对：买方该道具确实 +count（被背包规则吃掉时立刻显形）
                    int buyerItemAfter = CountOf(buyerDp, propsID);
                    if (undoWhy == null && buyerItemAfter < buyerItemBefore + count)
                        undoWhy = $"买方实收 {buyerItemAfter - buyerItemBefore} 件（应 {count}）";

                    if (undoWhy != null)
                    {
                        try { buyer.data.CostPropItem(propsID, count, false); } catch { }   // 按 propsID 抠回，不依赖栈还在不在
                        PropsService.RollbackGive(seller, takenList);
                        if (paid > 0) { string _; try { PropsService.TransferLingshi(seller, buyer, paid, out _); } catch { } }
                        return Fail(undoWhy + "（已回滚：道具退回卖方、灵石退回买方）");
                    }

                    var okObj = new JObject
                    {
                        ["op"] = "trade",
                        ["seller"] = sellerName, ["buyer"] = buyerName,
                        ["item"] = itemName, ["count"] = count, ["price"] = paid,
                        ["item_moved"] = true,
                        ["buyer_item_before"] = buyerItemBefore, ["buyer_item_after"] = buyerItemAfter,
                        ["same_grid"] = ToolArgs.SameGrid(seller, buyer),
                    };
                    // 余额变化：**读得到才写**。ReadLingshiHeld 失败返回 -1 —— 绝不能把 -1 当余额报给模型
                    // （渲染层对缺失字段是容忍的；报 -1 却会被当成真实数字读出来）
                    int sellerAfter = PropsService.ReadLingshiHeld(seller), buyerAfter = PropsService.ReadLingshiHeld(buyer);
                    if (sellerMoneyBefore >= 0 && sellerAfter >= 0)
                    {
                        okObj["seller_money_before"] = sellerMoneyBefore;
                        okObj["seller_money_after"] = sellerAfter;
                    }
                    if (buyerMoneyBefore >= 0 && buyerAfter >= 0)
                    {
                        okObj["buyer_money_before"] = buyerMoneyBefore;
                        okObj["buyer_money_after"] = buyerAfter;
                    }
                    // 好感（10-12，**本项目规则**：ConfRole* 全枚举无 Trade 类 ⇒ 游戏原生交易不挂好感，
                    // 这里按用户要求补）：
                    //   · 有价成交（paid>0）：双方各 +TradeIntimEach（人情往来，小额、不喧宾夺主）；
                    //   · 无偿赠予（price=0）：按赠礼语义——**收下东西的一方（买方）对给出方（卖方）** +配表值。
                    try
                    {
                        var intimPair = new JObject();
                        if (paid > 0)
                        {
                            IntimService.PutIntimWithAfter(intimPair, "seller_to_buyer",
                                IntimService.ApplyIntim(seller, buyer, IntimService.TradeIntimEach, "交易·卖方对买方"), seller, buyer);
                            IntimService.PutIntimWithAfter(intimPair, "buyer_to_seller",
                                IntimService.ApplyIntim(buyer, seller, IntimService.TradeIntimEach, "交易·买方对卖方"), buyer, seller);
                        }
                        else
                        {
                            IntimService.PutIntimWithAfter(intimPair, "buyer_to_seller",
                                IntimService.ApplyGiftIntim(buyer, seller, "交易赠予"), buyer, seller);
                        }
                        if (intimPair.Count > 0) okObj["intim"] = intimPair;   // 两边都读不到时不放空对象
                    }
                    catch { }
                    return Ok(okObj);
                };

            // ★ NPC↔NPC（双方都不是玩家）：**免窗直落刀**（09-17 提案制）。确认窗存在的唯一理由
            //   是"取玩家的一次点击"；玩家不在交易里就没有这一环 —— 弹了没人点，还会把整轮
            //   挂到 120s 超时。两边的同意由 Python 提案制取得（提出 → 对手方按原条款回发才到这一步）。
            if (!ToolArgs.IsPlayerUnit(seller) && !ToolArgs.IsPlayerUnit(buyer))
                return doDeal();

            // 「AI 应对」原生选项抑制挂在非玩家一方（窗口路径走到这里 ⇒ 玩家必是买卖一方）
            var anchor = ToolArgs.IsPlayerUnit(seller) ? buyer : seller;

            // 立绘位约定（ShowDramaService 文档钉死）：`left` = 屏幕左位 = **玩家**，
            // `right` = 屏幕右位 = **对方**。全工程另外 4 处确认窗（建关系/解除/纯灵石/自主互动）
            // 一律传 `(player, npc)`。
            // 事故 本方法初版直接传 `(seller, buyer)` —— 卖家是 NPC、买家是玩家时右位落成
            //   玩家，玩家实机看到**两个自己的立绘与名字**；同一坑 09-12 在自主互动窗已踩过一次
            //   （`NpcInitiativeMonitor.cs` 有同款修正注释）。
            WorldUnitBase dLeft, dRight;
            if (ToolArgs.IsPlayerUnit(buyer)) { dLeft = buyer; dRight = seller; }   // 玩家买 → 玩家在左、卖方在右
            else { dLeft = seller; dRight = buyer; }                       // 玩家卖

            return NativeActionService.Claimed(anchor, () => ShowDramaService.ShowConfirm(
                ModIds.DramaEconomyItemBase,
                dLeft, dRight,
                $"{sellerName}愿将「{itemName}」×{count} 让与{buyerName}，{deal}。",
                "成交", "不成交",
                onOk: () => doDeal(),
                okTip: price > 0
                    ? $"已成交：「{itemName}」×{count} → {buyerName}，{price} 灵石 → {sellerName}"
                    : $"已赠予：「{itemName}」×{count} → {buyerName}",
                noTip: "玩家叫停了这笔交易"));
        }

        /// <summary>背包里按中文名找**存量最大**的那一栈（原始 propsItem.name 与 GameTool.LS 后两种写法都比对，
        /// 与 `TakePropsFromBag` 同口径）。返回 soleID（找不到返回 null），并给出该道具总存量/道具 ID。</summary>
        internal static string FindStackSoleID(DataProps dp, string itemName, out int total, out int propsID)
        {
            total = 0; propsID = 0;
            string bestSole = null; int bestCount = 0;
            try
            {
                object list = null;
                try { list = ((dynamic)dp).allProps; } catch { }
                if (list == null) try { list = ((dynamic)dp).CloneAllProps(); } catch { }
                var dyn = (dynamic)list;
                int n = dyn == null ? 0 : (int)dyn.Count;
                for (int i = 0; i < n; i++)
                {
                    var p = (dynamic)dyn[i];
                    if (p == null) continue;
                    string raw = "";
                    try { raw = p.propsItem.name; } catch { }
                    string ls = raw;
                    try { ls = GameTool.LS(raw); } catch { ls = raw; }
                    if (raw != itemName && ls != itemName) continue;
                    int c = 1;
                    try { c = (int)p.propsCount; } catch { }
                    if (c < 1) c = 1;
                    total += c;
                    if (propsID == 0) { try { propsID = (int)p.propsID; } catch { } }
                    string sid = null;
                    try { sid = (string)p.soleID; } catch { }
                    if (!string.IsNullOrEmpty(sid) && c > bestCount) { bestSole = sid; bestCount = c; }
                }
            }
            catch { }
            return bestSole;
        }

        internal static int CountOf(DataProps dp, int propsID)
        {
            try { return dp.GetPropsNum(propsID); } catch { return -1; }
        }
    }
}
