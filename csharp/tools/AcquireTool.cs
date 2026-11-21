/// <summary>
/// AcquireTool —— **动作域·财物**：`item_acquire`（偷窃/讨要/收下）。
/// （11-19 由 6711 行的 `ToolExecutor.cs` 拆出，11-20 按 Python 的域归并；成员逐字搬移。）
/// 跨类调用一律 `<类名>.<方法>`；`internal` = 仅本程序集可见。
/// 【Python 对应】动作域·财物：讨要（`item_acquire`） —— 划分以 Python 侧为准，别在这里另立一套分类。
/// </summary>
using System;
using System.Collections.Generic;
using Newtonsoft.Json.Linq;
using static AgentLoopBridge.ToolResult;

namespace AgentLoopBridge
{
    internal static class AcquireTool
    {
        /// <summary>item_acquire：NPC 取得东西 —— 偷窃/讨要，**也包括收下玩家主动要送的**
        /// （`accepting_gift`，09-21：玩家说"我送你 X"，NPC 这边唯一能落地的调用就是它）。</summary>
        internal static JObject ItemAcquire(JObject args)
        {
            string op = (string)args["op"] ?? "";
            // 发起方恒为 harness 注入的 initiator（dialogue_agent._execute_tool_calls）。
            // 删掉那个旧版 `target` 兼容别名：schema 里从来没有 target、
            // 也没人写它 —— 留着只会让"actor 从哪来"这件事看起来有两个来源。
            var wub = ToolArgs.BindActor(args, out string initiator, out JObject bindFail);
            if (bindFail != null) return bindFail;
            string target = "";
            target = UnitIdentity.Name(wub, initiator);   // 取不到就用 harness 给的字符串
            string itemName = (string)args["item_name"] ?? "";
            int count = ToolArgs.SafeInt(args["count"], 1);
            if (count < 1) count = 1;
            // ★ `accepting_gift`（09-21）：玩家**主动说要送**此物，NPC 用同一条 ask_for 把它收下。
            //   两条路都吃这个标记，且都必须走**我方自制窗**：原生 Askfor 的句式是引擎写死的
            //   （"XX想向你讨要…"），玩家刚说"我送你"就看到"讨要"，等于以为被抢。
            //   真机上更早的那道坑是模型想不通"我是收东西的一方，哪来的讨要工具"，于是
            //   整轮不调、只用台词收场（那次思考写了 1400 字）。
            bool receiving = false;
            try { var rg = args["accepting_gift"]; receiving = rg != null && (bool)rg; } catch { }
            // `target`（09-16）= **被偷/被讨的人**：缺省 = 玩家（老路径逐字节不变），填了才是其他 NPC。
            // 变量 `player` 就是"受害方"——下面所有取背包/建动作的代码因此原样复用。
            var player = ToolArgs.ResolveCounterparty(args, out string cpName, out JObject cpFail);
            if (cpFail != null)
                return cpFail;
            if (player == null)
                return Fail("玩家单位不可用");
            bool toNpc = !string.IsNullOrEmpty(cpName);
            string victimName = toNpc ? cpName : "玩家";
            // ★ 10-15：多件请求（`items:[{item_name,count}]`）★
            //   原生 `UnitActionRoleAskfor(WorldUnitBase, String propsSoleID, Int32 propsCount)` **一次只吃一个栈**
            //   （签名实证，无 List 重载）⇒ "一次讨多样"只能走**自制确认窗**。这里先解出请求项：
            //     · ≥2 项 ⇒ `AskMultiple`（自制窗；NPC↔NPC 免窗直转；好感按 `accepting_gift` 分流）
            //     · 1 项 ⇒ 折回下面的单件老路径（**单件道具对玩家仍走原生窗**，原生立绘/文案/结算都在）
            var wants = ParseAcquireItems(args);
            if (wants.Count > 1)
            {
                if (op != "ask_for")
                    return Fail("偷窃一次只取一栈：多件请分次调用（或改用 item_name+count 指定单件）",
                                new JObject { ["requested"] = wants.Count });
                return AskMultiple(wub, player, wants, receiving, toNpc, victimName, target, cpName);
            }
            if (wants.Count == 1)
            {
                itemName = wants[0].Name;
                count = wants[0].Count;
            }
            if (string.IsNullOrEmpty(itemName))
                return Fail("缺少 item_name（道具中文名）或 items（多件讨要），可先 inspect_unit(classes=inventory)");
            // 灵石是货币（GetPropsNum 10001），不是道具实例，而官方 StealItem/Askfor 只接受 propsSoleID；
            // 早前用灵石道具 ID「10001」占位曾在真机触发 NRE（10001 并非有效 soleID）。
            // 偷窃照旧拒绝；讨要两条路都通：
            //   · NPC→NPC（09-17 提案制）：免窗直转 —— 它同时是"对方收下灵石赠礼"的落刀路径，
            //     走 TransferLingshi（任意两人的货币直写，自带补偿回滚）；
            //   · NPC→玩家（2026-09-21）：原生 Askfor 窗用不了（soleID 限制），复用纯灵石赠送同款
            //     自制确认窗（ShowConfirm），占 item_acquire 专属段 DramaItemAcquireBase
            //     （Mod+110，登记预留首用，本次启用）。玩家点「给他」才转账，拒绝不出灵石。
            if (itemName.Contains("灵石"))
            {
                if (op != "ask_for")
                    return Fail($"「{itemName}」是货币不是道具，无法偷窃（原生接口只接受背包道具实例的 soleID）；灵石往来请改用 economy_item",
                                new JObject { ["item_name"] = itemName, ["requested"] = count });
                // 足额才讨（与道具 ask_for 同口径：短缺整笔作废，不支持部分）
                int haveLingshi = PropsService.ReadLingshiHeld(player);
                string holderCn = toNpc ? victimName + "的" : "你的";
                if (haveLingshi < 0)
                    return Fail($"{holderCn}灵石持有数读取失败，已中止（不拿另一个账户兜底）");
                if (haveLingshi < count)
                    return Fail($"{holderCn}灵石仅 {haveLingshi}，不足 {count}（讨要按整笔计，不支持部分）",
                                new JObject { ["item_name"] = itemName, ["requested"] = count });

                if (toNpc)
                {
                    // NPC→NPC：免窗直转
                    string lsErr;
                    int lsGot = 0;
                    try { lsGot = PropsService.TransferLingshi(player, wub, count, out lsErr); }
                    catch (Exception e) { return Fail("灵石转移异常: " + e.Message); }
                    if (lsGot < count)
                    {
                        // 理论上到不了（TransferLingshi 自带"扣了没到账就退回"）；到了也如实报并按差额退回
                        if (lsGot > 0) { string _; try { PropsService.TransferLingshi(wub, player, lsGot, out _); } catch { } }
                        return Fail($"灵石转移未完成（应付 {count} 实收 {lsGot}），已按原额退回");
                    }
                    // 好感（10-15）：讨要达成 ⇒ **双边**（NPC↔NPC 同为讨要语义）：取得方 + / 交付方 −。
                    var joNpcLs = new JObject
                    {
                        ["op"] = op, ["target"] = target, ["item_name"] = itemName,
                        ["count"] = count, ["accepted"] = true
                    };
                    IntimService.PutIntimWithAfter(joNpcLs, "intim", IntimService.ApplyAskforIntim(wub, player, "灵石·NPC直转"), wub, player);
                    IntimService.PutIntimWithAfter(joNpcLs, "intim_upset", IntimService.ApplyAskforUpset(player, wub, "灵石·NPC直转"), player, wub);
                    return ToolArgs.WithCounterparty(Ok(joNpcLs), cpName);
                }

                // NPC→玩家：自制确认窗（left=玩家/right=NPC，立绘位 09-12 教训）。
                // 点选后按当时余额重新结算；TransferLingshi 自带"扣了没到账就退回"补偿。
                // ★ 措辞按**方向**分叉（09-21）：见上面 `receiving` 那段。
                string askText = receiving
                    ? $"{target}收下你赠的灵石×{count}，可好？"
                    : $"{target}想向你讨要灵石×{count}，是否给他？";
                return NativeActionService.Claimed(wub, () => ShowDramaService.ShowConfirm(
                    ModIds.DramaItemAcquireBase,
                    player, wub,
                    askText,
                    receiving ? "收下" : "给他", receiving ? "不用了" : "不给",
                    onOk: () =>
                    {
                        string terr;
                        int got = PropsService.TransferLingshi(player, wub, count, out terr);
                        if (got < count)
                            return Fail($"灵石转移未完成（应付 {count} 实收 {got}）" + (string.IsNullOrEmpty(terr) ? "" : "：" + terr));
                        try { UITipItem.AddTip("已给出灵石×" + got, 2f); } catch { }
                        // 好感（10-15 定案：只传配表请求值、换算由引擎；**按 `accepting_gift` 分流**）：
                        //   · 收下赠礼 ⇒ **单边**：收礼方（你）对送礼方（玩家）+ RoleGive.closeParaA；
                        //   · 讨要达成 ⇒ **双边**：取得方（你）+ RoleAskfor.closeParaA、
                        //                        交付方（玩家）− RoleAskfor.closeParaB（原值 -8）。
                        var joLs = new JObject
                        {
                            ["op"] = op, ["target"] = target, ["item_name"] = itemName,
                            ["count"] = count, ["accepted"] = true,
                            ["receiving"] = receiving
                        };
                        if (receiving)
                            IntimService.PutIntimWithAfter(joLs, "intim", IntimService.ApplyGiftIntim(wub, player, "灵石·收下"), wub, player);
                        else
                        {
                            IntimService.PutIntimWithAfter(joLs, "intim", IntimService.ApplyAskforIntim(wub, player, "灵石窗"), wub, player);
                            IntimService.PutIntimWithAfter(joLs, "intim_upset", IntimService.ApplyAskforUpset(player, wub, "灵石窗"), player, wub);
                        }
                        return Ok(joLs);
                    },
                    okTip: null,
                    noTip: receiving ? $"{target}没有收下你的灵石" : $"你没有把灵石给{target}"));
            }

            // 普通道具：按中文名挑栈。ask_for 按 count 挑（精确→最小够装→首个）；
            // steal_item 无数量概念（产品决策 ：删 count 语义，整栈偷走），want=0 即取首个同名栈
            int want = op == "ask_for" ? count : 0;
            var stack = FindPropsStack(player, itemName, want);
            if (stack == null)
                return Fail($"{victimName}背包中没有「{itemName}」，可先 inspect_unit(classes=inventory) 确认");

            switch (op)
            {
                case "steal_item":
                    // 偷窃无窗：`UnitActionRoleStealItem(受害者, soleID)` 的受害者本就是构造参数，
                    // 玩家/ NPC 同一行代码（察觉与好感惩罚由引擎动作自己处理）。
                    // 偷取**没有原生窗**（察觉与好感惩罚由引擎动作自己处理），所以丢了返回码 = **谎报成功**：
                    // 引擎拒了（精力/状态不满足）我们照样回 Ok，模型于是以为东西到手、还可能接着演。
                    var stealAct = new UnitActionRoleStealItem(player, stack.SoleID);
                    int stealRet = wub.CreateAction(stealAct, true);
                    if (stealRet != 0)
                    {
                        try { ModMain.P("[UnitAction] steal_item 被引擎拒绝 code=" + stealRet + "：" + NativeActionService.NativeRefuseWhy(wub, stealAct, "偷取")); } catch { }
                        return Fail(NativeActionService.Refused("偷取", "偷取", wub, stealAct, stealRet));
                    }
                    return ToolArgs.WithCounterparty(Ok(new JObject
                    {
                        ["op"] = op, ["target"] = target, ["item_name"] = itemName, ["stack_count"] = stack.Count
                    }), cpName);

                case "ask_for":
                {
                    if (stack.Count < count)
                        return Fail($"{victimName}的「{itemName}」单栈仅 {stack.Count} 个，不足 {count} 个（讨要按单个栈计数，不支持跨栈合并）",
                                    new JObject { ["item_name"] = itemName, ["requested"] = count, ["stack_count"] = stack.Count });

                    // 玩家主动赠道具 → **不走原生 Askfor**，改走与纯灵石同款的自制确认窗＋直接搬包
                    // （09-21）。原生那句式是引擎写死的"想向你讨要"，玩家刚说"我送你"就看到它，
                    // 等于以为被抢；搬包三件套（拆栈/入包/回滚）与下面 NPC 免窗路径同一份实现。
                    // 代价（知情）：不经引擎动作 → 引擎自己那套"讨要达成"的角色日志/好感副作用
                    //   不会发生，与纯灵石赠礼同款（灵石本来也是货币直写，不走引擎动作）。
                    if (receiving)
                    {
                        return NativeActionService.Claimed(wub, () => ShowDramaService.ShowConfirm(
                            ModIds.DramaItemAcquireBase,
                            player, wub,
                            $"{target}收下你赠的「{itemName}」×{count}，可好？",
                            "收下", "不用了",
                            onOk: () =>
                            {
                                var taken = new Il2CppSystem.Collections.Generic.List<DataProps.PropsData>();
                                string perr = null;
                                int got = 0;
                                try { got = PropsService.TakePropsFromBag(player, wub, itemName, count, taken, out perr); }
                                catch (Exception e) { perr = e.Message; }
                                if (got < count)
                                {
                                    PropsService.RollbackGive(player, taken);
                                    return Fail($"「{itemName}」只取出 {got} 个（要 {count} 个），本次作废（道具未动）"
                                                + (string.IsNullOrEmpty(perr) ? "" : "：" + perr));
                                }
                                try
                                {
                                    var dst = wub.data.unitData.propData as DataProps;
                                    if (dst == null) throw new Exception("发起方背包不可用");
                                    for (int i = 0; i < taken.Count; i++) dst.AddProps(taken[i]);
                                }
                                catch (Exception e)
                                {
                                    PropsService.RollbackGive(player, taken);
                                    return Fail($"道具已从玩家背包取出但入包失败，已原样退回：{e.Message}");
                                }
                                try { UITipItem.AddTip($"已送出「{itemName}」×{count}", 2f); } catch { }
                                // 好感（10-12 接线）：收下玩家赠礼 ⇒ **收礼方（你）对送礼方（玩家）** +（配表 RoleGive）。
                                // 这条路原注释已标明"不经引擎动作 ⇒ 引擎那套好感副作用不会发生"——现在由我们自己补。
                                int intimRcv = IntimService.ApplyGiftIntim(wub, player, "收下赠礼");
                                var okRcv = new JObject
                                {
                                    ["op"] = op, ["target"] = target, ["item_name"] = itemName,
                                    ["count"] = count, ["accepted"] = true, ["receiving"] = true
                                };
                                IntimService.PutIntim(okRcv, "intim", intimRcv);
                                return Ok(okRcv);
                            },
                            okTip: null,
                            noTip: $"{target}没有收下你的「{itemName}」"));
                    }

                    // 非玩家：**免窗直转**（09-16）。原生 Askfor 弹的是"玩家点同意/拒绝"的窗，
                    // NPC 之间没有这一环 → 走与赠送同款的"拆栈 → 入包"两张式（失败双向回滚）。
                    // 语义 = 对方默许；将来接提案制时把这里换成"挂提案"即可（工具结果形状不变）。
                    if (toNpc)
                    {
                        var taken = new Il2CppSystem.Collections.Generic.List<DataProps.PropsData>();
                        string takeErr = null;
                        int got = 0;
                        try { got = PropsService.TakePropsFromBag(player, wub, itemName, count, taken, out takeErr); }
                        catch (Exception e) { takeErr = e.Message; }
                        if (got < count)
                        {
                            PropsService.RollbackGive(player, taken);
                            return Fail($"{cpName}的「{itemName}」只取出 {got} 个（要 {count} 个），本次作废（道具未动）"
                                        + (string.IsNullOrEmpty(takeErr) ? "" : "：" + takeErr));
                        }
                        try
                        {
                            var dst = wub.data.unitData.propData as DataProps;
                            if (dst == null) throw new Exception("发起方背包不可用");
                            for (int i = 0; i < taken.Count; i++) dst.AddProps(taken[i]);
                        }
                        catch (Exception e)
                        {
                            PropsService.RollbackGive(player, taken);
                            return Fail($"道具已从{cpName}取出但入包失败，已原样退回：{e.Message}");
                        }
                        // 好感（10-15，**按 `accepting_gift` 分流**；NPC↔NPC 与对玩家同一套语义）：
                        //   · `receiving=true`（收下对方赠予）⇒ **单边**：收礼方（你）对送礼方 + RoleGive；
                        //   · 缺省（讨要达成）            ⇒ **双边**：取得方（你）+ / 交付方（对方）− RoleAskfor。
                        var joNpcItem = new JObject
                        {
                            ["op"] = op, ["target"] = target, ["item_name"] = itemName,
                            ["count"] = count, ["accepted"] = true, ["receiving"] = receiving
                        };
                        if (receiving)
                            IntimService.PutIntimWithAfter(joNpcItem, "intim", IntimService.ApplyGiftIntim(wub, player, "NPC收下"), wub, player);
                        else
                        {
                            IntimService.PutIntimWithAfter(joNpcItem, "intim", IntimService.ApplyAskforIntim(wub, player, "NPC取得"), wub, player);
                            IntimService.PutIntimWithAfter(joNpcItem, "intim_upset", IntimService.ApplyAskforUpset(player, wub, "NPC取得"), player, wub);
                        }
                        return ToolArgs.WithCounterparty(Ok(joNpcItem), cpName);
                    }
                    // 完成挂起：原生讨要剧情带"同意/拒绝"选项（Askfor.customNPCToPlayerDrama+Agree/RefuseOption
                    // 反编三件套实锤），玩家拒绝时还有 isNPCReduceIntim 不满标记——工具等选择后按结局返回。
                    var ask = new UnitActionRoleAskfor(player, stack.SoleID, count);
                    var slot = NativeActionService.DeferNativeUnitAction(ask, "ask_for", DramaNativeClaim.UidOf(wub), () =>
                    {
                        bool done = false, upset = false;
                        try { done = ask.isAskforComplete; } catch { }
                        try { upset = ask.isNPCReduceIntim; } catch { }
                        try { UnityEngine.Debug.Log("[UnitAction] ask_for isAskforComplete=" + done + " isNPCReduceIntim=" + upset); } catch { }
                        if (done)
                            return Ok(new JObject { ["op"] = "ask_for", ["target"] = target, ["item_name"] = itemName, ["count"] = count, ["accepted"] = true });
                        return Ok(new JObject { ["op"] = "ask_for", ["target"] = target, ["item_name"] = itemName, ["count"] = count, ["accepted"] = false, ["upset"] = upset });
                    }, out var busyFail);
                    if (busyFail != null) return busyFail;
                    // 返回码必须消费（同 attack 的长注释）。讨要的原生窗（收下/不给）也靠 CreateAction 触发。
                    int askRet;
                    try { askRet = wub.CreateAction(ask, true); }
                    catch (Exception e)
                    {
                        if (slot != null) { UnitActionPending.Remove(ask.Pointer); DramaGate.Cancel(slot.Value); }
                        return Fail("发起讨要失败: " + e.Message);
                    }
                    if (askRet != 0)
                    {
                        if (slot != null) { UnitActionPending.Remove(ask.Pointer); DramaGate.Cancel(slot.Value); }
                        try { ModMain.P("[UnitAction] ask_for 被引擎拒绝 code=" + askRet + "：" + NativeActionService.NativeRefuseWhy(wub, ask, "讨要")); } catch { }
                        return Fail(NativeActionService.Refused("发起讨要", "讨要", wub, ask, askRet));
                    }
                    if (slot != null) return DramaGate.MakePendingMarker(slot.Value);
                    // AutoConfirm（verify 工程）旧立即路径：
                    return Ok(new JObject { ["op"] = op, ["target"] = target, ["item_name"] = itemName, ["count"] = count, ["pending"] = true });
                }

                default:
                    return Fail("unsupported item_acquire op: " + op);
            }
        }

        // ---------- item_acquire：多件（自制窗）----------

        /// <summary>item_acquire 的一项请求（道具中文名或「灵石」+ 数量）。</summary>
        internal sealed class AcquireItem
        {
            public string Name;
            public int Count;
        }

        /// <summary>
        /// 从 `items:[{item_name,count}]`（10-15 新增，多件）或旧的 `item_name`+`count` 解出请求项。
        /// `items` 优先；两者都给时以 `items` 为准（与 economy_item 同口径）。
        /// </summary>
        internal static List<AcquireItem> ParseAcquireItems(JObject args)
        {
            var list = new List<AcquireItem>();
            try
            {
                var arr = args["items"] as JArray;
                if (arr != null)
                {
                    foreach (var tok in arr)
                    {
                        var o = tok as JObject;
                        if (o == null) continue;
                        string n = (string)o["item_name"] ?? "";
                        if (string.IsNullOrEmpty(n)) continue;
                        int c = ToolArgs.SafeInt(o["count"], 1);
                        if (c < 1) c = 1;
                        list.Add(new AcquireItem { Name = n, Count = c });
                    }
                }
            }
            catch { }
            if (list.Count == 0)
            {
                string n0 = (string)args["item_name"] ?? "";
                if (!string.IsNullOrEmpty(n0))
                {
                    int c0 = ToolArgs.SafeInt(args["count"], 1);
                    if (c0 < 1) c0 = 1;
                    list.Add(new AcquireItem { Name = n0, Count = c0 });
                }
            }
            return list;
        }

        /// <summary>按中文名统计背包内该道具**总数**（跨栈求和；读不到返回 -1，调用方必须当"未知"）。</summary>
        internal static int CountPropsByName(WorldUnitBase u, string itemName)
        {
            try
            {
                var propData = u.data.unitData.propData;
                object list = null;
                try { list = ((dynamic)propData).allProps; } catch { }
                if (list == null) try { list = ((dynamic)propData).CloneAllProps(); } catch { }
                if (list == null) return -1;
                var dyn = (dynamic)list;
                int n = (int)dyn.Count, total = 0;
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
                }
                return total;
            }
            catch { return -1; }
        }

        /// <summary>
        /// 讨要**多件**（`items` ≥2，道具与灵石可混）—— 原生 `UnitActionRoleAskfor` 一次只吃一个
        /// `propsSoleID`（10-15 签名实证），所以多件只能走**自制确认窗**（与纯灵石那条同款
        /// `ShowConfirm`，占 `DramaItemAcquireBase` 段）。口径与单件完全一致：
        ///   · **整笔**：任一项不足 ⇒ 整笔作废（已取出的一律 `RollbackGive` 原样放回）；
        ///   · 玩家点「给他 / 收下」才真转移；NPC↔NPC 无人在场 ⇒ 免窗直转（对方默许），同一份落刀实现；
        ///   · 好感**按 `accepting_gift` 分流**（10-15 定案）：
        ///       `receiving=true`（收下对方赠予）⇒ **单边** 收礼方→送礼方 +`RoleGive.closeParaA`；
        ///       缺省（讨要达成）              ⇒ **双边** 取得方→交付方 +`RoleAskfor.closeParaA`、
        ///                                            交付方→取得方 −`RoleAskfor.closeParaB`。
        /// </summary>
        internal static JObject AskMultiple(WorldUnitBase wub, WorldUnitBase holder, List<AcquireItem> wants,
                                    bool receiving, bool toNpc, string holderName, string target, string cpName)
        {
            // —— 预检（只读；任一不足直接不弹窗）——
            int totalMoney = 0;
            var detail = new List<string>();
            foreach (var w in wants)
            {
                if (w.Name.Contains("灵石"))
                {
                    int have = PropsService.ReadLingshiHeld(holder);
                    if (have < 0)
                        return Fail($"{holderName}的灵石持有数读取失败，已中止（不拿另一个账户兜底）");
                    if (have < w.Count)
                        return Fail($"{holderName}的灵石仅 {have}，不足 {w.Count}（讨要按整笔计，不支持部分）",
                                    new JObject { ["item_name"] = w.Name, ["requested"] = w.Count });
                    totalMoney += w.Count;
                }
                else
                {
                    int have = CountPropsByName(holder, w.Name);
                    if (have < 0)
                        return Fail($"{holderName}的背包读取失败，已中止");
                    if (have < w.Count)
                        return Fail($"{holderName}的「{w.Name}」共 {have} 个，不足 {w.Count} 个（讨要按整笔计，不支持部分）",
                                    new JObject { ["item_name"] = w.Name, ["requested"] = w.Count, ["have"] = have });
                }
                detail.Add(w.Name + "×" + w.Count);
            }
            string listText = string.Join("、", detail.ToArray());

            // —— 落刀（窗与免窗两条路共用同一份）——
            System.Func<JObject> doTake = () =>
            {
                var taken = new Il2CppSystem.Collections.Generic.List<DataProps.PropsData>();
                var itemsRes = new JArray();
                // ① 道具：跨栈收集；任一项不足 ⇒ 整笔作废
                foreach (var w in wants)
                {
                    if (w.Name.Contains("灵石")) continue;
                    string perr = null;
                    int got = 0;
                    try { got = PropsService.TakePropsFromBag(holder, wub, w.Name, w.Count, taken, out perr); }
                    catch (Exception e) { perr = e.Message; got = 0; }
                    if (got < w.Count)
                    {
                        PropsService.RollbackGive(holder, taken);
                        return Fail($"「{w.Name}」只取出 {got} 个（要 {w.Count} 个），本次作废（道具未动）"
                                    + (string.IsNullOrEmpty(perr) ? "" : "：" + perr),
                                    new JObject { ["items"] = itemsRes });
                    }
                    itemsRes.Add(new JObject { ["item"] = w.Name, ["requested"] = w.Count, ["actual"] = got });
                }
                // ② 灵石（TransferLingshi 自带"扣了没到账就退回"的补偿）
                int paid = 0;
                if (totalMoney > 0)
                {
                    string terr;
                    try { paid = PropsService.TransferLingshi(holder, wub, totalMoney, out terr); }
                    catch (Exception e) { paid = 0; terr = e.Message; }
                    if (paid != totalMoney)
                    {
                        PropsService.RollbackGive(holder, taken);
                        return Fail($"灵石转移未完成（应付 {totalMoney} 实收 {paid}），本次作废（道具未动）"
                                    + (string.IsNullOrEmpty(terr) ? "" : "：" + terr),
                                    new JObject { ["items"] = itemsRes });
                    }
                    itemsRes.Add(new JObject { ["item"] = "灵石", ["requested"] = totalMoney, ["actual"] = paid });
                }
                // ③ 入包（失败 ⇒ 道具原样退回 + 灵石退回）
                try
                {
                    var dst = wub.data.unitData.propData as DataProps;
                    if (dst == null) throw new Exception("发起方背包不可用");
                    for (int i = 0; i < taken.Count; i++) dst.AddProps(taken[i]);
                }
                catch (Exception e)
                {
                    PropsService.RollbackGive(holder, taken);
                    if (paid > 0) { string _; try { PropsService.TransferLingshi(wub, holder, paid, out _); } catch { } }
                    return Fail($"道具已从{holderName}取出但入包失败，已原样退回：{e.Message}",
                                new JObject { ["items"] = itemsRes });
                }
                try { UITipItem.AddTip(receiving ? "已收下" : "已给出", 2f); } catch { }
                // ④ 好感：按 `accepting_gift` 分流（单边赠礼 / 双边讨要）
                var jo = new JObject
                {
                    ["op"] = "ask_for", ["target"] = target, ["items"] = itemsRes,
                    ["count"] = wants.Count, ["accepted"] = true, ["receiving"] = receiving
                };
                if (receiving)
                    IntimService.PutIntimWithAfter(jo, "intim", IntimService.ApplyGiftIntim(wub, holder, "多件·收下"), wub, holder);
                else
                {
                    IntimService.PutIntimWithAfter(jo, "intim", IntimService.ApplyAskforIntim(wub, holder, "多件窗"), wub, holder);
                    IntimService.PutIntimWithAfter(jo, "intim_upset", IntimService.ApplyAskforUpset(holder, wub, "多件窗"), holder, wub);
                }
                return Ok(jo);
            };

            if (toNpc)
                return ToolArgs.WithCounterparty(doTake(), cpName);   // 无人在屏幕前：免窗直转（对方默许）

            string askText = receiving
                ? $"{target}收下你赠的：{listText}，可好？"
                : $"{target}想向你讨要：{listText}，是否给他？";
            return NativeActionService.Claimed(wub, () => ShowDramaService.ShowConfirm(
                ModIds.DramaItemAcquireBase,
                holder, wub,
                askText,
                receiving ? "收下" : "给他", receiving ? "不用了" : "不给",
                onOk: doTake,
                okTip: null,
                noTip: receiving ? $"{target}没有收下你的东西" : $"你没有把东西给{target}"));
        }

        /// <summary>背包里的一个道具栈（soleID = 栈唯一 ID，Count = 该栈有几个）。</summary>
        internal sealed class PropsStack
        {
            public string SoleID;
            public int Count;
        }

        /// <summary>
        /// 从单位背包按中文名挑一个道具栈。want &gt; 0 时按数量挑：① 恰好等于 want；② 最小的"够装"栈；③ 退回首个同名栈。
        /// want &lt;= 0 表示无数量诉求（如偷窃整栈语义），直接取首个同名栈。未找到返回 null。
        /// </summary>
        internal static PropsStack FindPropsStack(WorldUnitBase unit, string itemName, int want)
        {
            var all = new List<PropsStack>();
            try
            {
                var propData = unit.data.unitData.propData;
                object list = null;
                try { list = ((dynamic)propData).allProps; } catch { }
                if (list == null) try { list = ((dynamic)propData).CloneAllProps(); } catch { }
                if (list == null) return null;
                var dyn = (dynamic)list;
                int n = (int)dyn.Count;
                for (int i = 0; i < n; i++)
                {
                    var p = (dynamic)dyn[i];
                    if (p == null) continue;
                    string raw = "";
                    string ls = "";
                    try { raw = p.propsItem.name; } catch { }
                    try { ls = GameTool.LS(raw); } catch { ls = raw; }
                    if (raw != itemName && ls != itemName) continue;
                    string sid = "";
                    try { sid = (string)p.soleID; } catch { }
                    if (string.IsNullOrEmpty(sid)) continue;
                    int c = 1;
                    try { c = (int)p.propsCount; } catch { }
                    if (c < 1) c = 1;
                    all.Add(new PropsStack { SoleID = sid, Count = c });
                }
            }
            catch { }
            if (all.Count == 0) return null;
            if (want <= 0) return all[0];                                       // 无数量诉求：首个同名栈

            foreach (var s in all)
                if (s.Count == want) return s;                                  // ① 精确匹配
            PropsStack best = null;
            foreach (var s in all)
                if (s.Count >= want && (best == null || s.Count < best.Count))   // ② 最小够装
                    best = s;
            return best ?? all[0];                                              // ③ 退回首个同名栈
        }
    }
}
