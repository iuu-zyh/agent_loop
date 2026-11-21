/// <summary>
/// RelationTool —— **动作域·关系**：`social_relation`（好感/结缘/婚姻/师徒/结义/捎话）+ 引擎裁决码表。
/// （11-19 由 6711 行的 `ToolExecutor.cs` 拆出，11-20 按 Python 的域归并；成员逐字搬移。）
/// 跨类调用一律 `<类名>.<方法>`；`internal` = 仅本程序集可见。
/// 【Python 对应】动作域·关系（`social_relation`） —— 划分以 Python 侧为准，别在这里另立一套分类。
/// </summary>
using System;
using System.Collections.Generic;
using Newtonsoft.Json.Linq;
using static AgentLoopBridge.ToolResult;

namespace AgentLoopBridge
{
    internal static class RelationTool
    {
        // ---------- 动作类（需二阶段校验） ----------

        internal static JObject SocialRelation(JObject args)
        {
            string op = (string)args["op"] ?? "";
            // 发起方恒为 harness 注入的 initiator（dialogue_agent._execute_tool_calls）。
            // 删掉那个旧版 `target` 兼容别名：schema 里从来没有 target、
            // 也没人写它 —— 留着只会让"actor 从哪来"这件事看起来有两个来源。
            var wub = ToolArgs.BindActor(args, out string initiator, out JObject bindFail);
            if (bindFail != null) return bindFail;
            // `target`（09-16）= **对手方**：缺省 = 玩家（老路径逐字节不变），填了才是其他 NPC。
            // 非玩家时**不开任何确认窗** —— NPC 之间没有"玩家点同意"这一步（见 DirectEstablish/DirectBreak）。
            // 变量 `player` 即"对手方"——下面所有判重/直写/建窗代码因此原样复用。
            var player = ToolArgs.ResolveCounterparty(args, out string cpName, out JObject cpFail);
            if (cpFail != null)
                return cpFail;
            if (player == null)
                return Fail("玩家单位为空");
            bool otherIsPlayer = string.IsNullOrEmpty(cpName);
            // target = **发起方自己**的名字（既有约定：渲染层把它当"那位 NPC"，非玩家对手方时另发 counterparty）
            string target = "";
            target = UnitIdentity.Name(wub, initiator);   // 取不到就用 harness 给的字符串
            int intim = ToolArgs.GetIntimBetween(wub, player);
            var rel = wub.data.unitData.relationData;
            // 模型台词（确认窗正文，可选）：trim + 500 字截断；仅关系类 op 消费，好感直写忽略
            string askText = ((string)args["ask_text"] ?? "").Trim();
            if (askText.Length > 500) askText = askText.Substring(0, 500);

            switch (op)
            {
                case "add_intim":   // 加好感：直写（走写入层 —— 只传请求值，换算/衰减由引擎决定，见 IntimService.ApplyIntim）
                {
                    // 10-12 放开量级：1..10 → 1..120（面板半心=30 / 一星=60 / 满值=300；默认 15＝配表基准）。
                    // 旧上限 10 在原版刻度里小到看不见（一次闲聊/一份薄礼的量级就是 15）。
                    int delta = ToolArgs.SafeInt(args["value"], IntimService.IntimStepDefault);
                    if (delta < 1) delta = 1;
                    if (delta > IntimService.IntimStepMax) delta = IntimService.IntimStepMax;
                    int before = intim;
                    IntimService.ApplyIntim(wub, player, delta, "add_intim");
                    int after = ToolArgs.GetIntimBetween(wub, player);
                    int actual = after - before;
                    return ToolArgs.WithCounterparty(Ok(new JObject
                    {
                        ["op"] = op, ["target"] = target,
                        ["requested"] = delta, ["actual_delta"] = actual,
                        ["intim_before"] = before, ["current_intim"] = after,
                    }), cpName);
                }
                case "reduce_intim": // 减好感：直写（同上；负向不封底——仇怨是游戏内既有的负值域）
                {
                    int delta = ToolArgs.SafeInt(args["value"], IntimService.IntimStepDefault);
                    if (delta < 1) delta = 1;
                    if (delta > IntimService.IntimStepMax) delta = IntimService.IntimStepMax;
                    int before = intim;
                    IntimService.ApplyIntim(wub, player, -delta, "reduce_intim");
                    int after = ToolArgs.GetIntimBetween(wub, player);
                    int actual = after - before;
                    return ToolArgs.WithCounterparty(Ok(new JObject
                    {
                        ["op"] = op, ["target"] = target,
                        ["requested"] = delta, ["actual_delta"] = actual,
                        ["intim_before"] = before, ["current_intim"] = after,
                    }), cpName);
                }

                case "jie_yuan":     // 结缘 → 道侣（Lover）——已移除C端好感校验（由Python侧/模型decision）
                {
                    if (rel.IsRelation(player, UnitRelationType.Lover))
                        return Fail("你与" + target + "已是道侣");
                    if (!otherIsPlayer)
                        return DirectEstablish(wub, player, UnitRelationType.Lover, op, cpName);
                    return StartRelationAction(wub, player, UnitRelationType.Lover, op, askText);
                }
                case "jie_chu_jie_yuan":
                {
                    if (!rel.IsRelation(player, UnitRelationType.Lover))
                        return Fail("你与" + target + "并非道侣");
                    if (!otherIsPlayer)
                        return DirectBreak(wub, player, op, cpName);
                    return StartBreakWith(wub, player, op, askText);
                }

                case "marry":        // 结婚：须先结缘 + 同格。确认窗 → 玩家同意 → 神识传音实证路径
                                     // （_ss_modmain.cs:4030 action5）：不走 UnitActionRoleMarry（真机 NRE），
                                     // 直接互写 married + 从 lover 移除，再 OpenDrama(22201) 弹游戏自带成婚剧情。
                {
                    if (!rel.IsRelation(player, UnitRelationType.Lover))
                        return Fail("需先与" + target + "结缘（道侣）后才能求婚");
                    if (rel.IsRelation(player, UnitRelationType.Married))
                        return Fail("你与" + target + "已是夫妻");
                    if (!ToolArgs.SameGrid(wub, player))
                        return Fail("求婚需同处一地，当前异地");
                    string npcId = (string)wub.data.unitData.unitID;
                    string playerId = (string)player.data.unitData.unitID;
                    string marryText = string.IsNullOrWhiteSpace(askText)
                        ? $"{target}向你正式求婚：愿与我结为夫妻，共证大道吗？"
                        : $"{target}向你求婚：\n{askText.Trim()}";
                    if (!otherIsPlayer)
                        return DirectMarry(wub, player, op, cpName);
                    return NativeActionService.Claimed(wub, () => ShowDramaService.ShowConfirm(
                        ModIds.DramaSocialRelationBase,
                        player, wub,
                        marryText,
                        "我愿意", "再想想",
                        onOk: () =>
                        {
                            // 现状快照（09-17 与其他关系 op 对齐）：结婚走的是**字段直写**（没有引擎动作，
                            // 故无 precheck/create_ret），至少让"落刀前后那一对关系是什么"可查。
                            string relBeforeMarry = RelationOf(wub, player);
                            try
                            {
                                player.data.unitData.relationData.married = npcId;
                                wub.data.unitData.relationData.married = playerId;
                            }
                            catch (Exception e) { return Fail("married 直写失败: " + e.Message); }
                            // 结婚后不再是道侣（神识传音同款：lover 互删），失败不影响成婚事实
                            string loverNote = "lover 未动";
                            try
                            {
                                player.data.unitData.relationData.lover.Remove(npcId);
                                wub.data.unitData.relationData.lover.Remove(playerId);
                                loverNote = "lover 已互删";
                            }
                            catch (Exception e) { loverNote = "lover 互删失败: " + e.Message; }
                            // 弹游戏自带成婚剧情（神识传音同款 ConfDrama 22201）
                            string dramaNote;
                            try
                            {
                                DramaTool.OpenDrama(22201, new DramaData { unitLeft = player, unitRight = wub });
                                dramaNote = "已弹原版成婚剧情(22201)";
                            }
                            catch (Exception e) { dramaNote = "成婚剧情 22201 弹出失败: " + e.Message; }
                            return Ok(new JObject
                            {
                                ["op"] = op, ["target"] = target, ["relation"] = "夫妻",
                                // lover/dramaNote 仍只进日志与 okTip（不进 data）—— 保持既有契约面
                                ["relation_before"] = relBeforeMarry,
                                ["relation_after"] = RelationOf(wub, player),
                                ["diag"] = RelationDiag(wub, player),
                            });
                        },
                        okTip: "礼成！",
                        noTip: "玩家婉拒了求婚"));
                }
                case "divorce":
                {
                    if (!rel.IsRelation(player, UnitRelationType.Married))
                        return Fail("你与" + target + "并非夫妻");
                    if (!otherIsPlayer)
                        return DirectBreak(wub, player, op, cpName);
                    return StartBreakWith(wub, player, op, askText);
                }

                case "bai_shi":      // 拜师：NPC 认玩家做师傅 → player 是 NPC 的 Master
                {
                    if (rel.IsRelation(player, UnitRelationType.Master))
                        return Fail("你已是" + target + "的师傅");
                    if (!otherIsPlayer)
                        return DirectEstablish(wub, player, UnitRelationType.Master, op, cpName);
                    return StartRelationAction(wub, player, UnitRelationType.Master, op, askText);
                }
                case "jie_chu_bai_shi":
                {
                    if (!rel.IsRelation(player, UnitRelationType.Master))
                        return Fail("你并非" + target + "的师傅");
                    if (!otherIsPlayer)
                        return DirectBreak(wub, player, op, cpName);
                    return StartBreakWith(wub, player, op, askText);
                }
                case "shou_tu":      // 收徒：NPC 收玩家做徒弟 → player 是 NPC 的 Student
                {
                    if (rel.IsRelation(player, UnitRelationType.Student))
                        return Fail(target + "已是你的徒弟");
                    if (!otherIsPlayer)
                        return DirectEstablish(wub, player, UnitRelationType.Student, op, cpName);
                    return StartRelationAction(wub, player, UnitRelationType.Student, op, askText);
                }
                case "jie_chu_shou_tu":
                {
                    if (!rel.IsRelation(player, UnitRelationType.Student))
                        return Fail(target + "并非你的徒弟");
                    if (!otherIsPlayer)
                        return DirectBreak(wub, player, op, cpName);
                    return StartBreakWith(wub, player, op, askText);
                }

                case "ren_yi_fu_mu": // 认义父母：NPC 认玩家做义父母 → ParentBack
                {
                    if (rel.IsRelation(player, UnitRelationType.ParentBack))
                        return Fail("你已是" + target + "的义父母");
                    if (!otherIsPlayer)
                        return DirectEstablish(wub, player, UnitRelationType.ParentBack, op, cpName);
                    return StartRelationAction(wub, player, UnitRelationType.ParentBack, op, askText);
                }
                case "jie_yi":       // 结义：NPC 与玩家结义 → BrotherBack（亲测 BrotherBack 单向写入不可靠，改双写兜底）
                {
                    if (rel.IsRelation(player, UnitRelationType.BrotherBack) || rel.IsRelation(player, UnitRelationType.Brother))
                        return Fail("你与" + target + "已是结义");
                    if (!otherIsPlayer)
                        return DirectEstablish(wub, player, UnitRelationType.BrotherBack, op, cpName);
                    return StartRelationAction(wub, player, UnitRelationType.BrotherBack, op, askText);
                }
                case "jie_chu_jie_yi":
                {
                    if (!rel.IsRelation(player, UnitRelationType.BrotherBack) && !rel.IsRelation(player, UnitRelationType.Brother))
                        return Fail("你与" + target + "并非结义");
                    if (!otherIsPlayer)
                        return DirectBreak(wub, player, op, cpName);
                    return StartBreakWith(wub, player, op, askText);
                }

                case "chuan_hua":    // 捎话（11-12）：把一句话投进另一个 NPC 的信箱，**不立即互动**
                {
                    // ★ 本分支**不落任何刀**，只做两件事：解析收信人 + 把台词原样带回给 Python。
                    //   真正的投递在 Python 侧（`agent/mailbox.deliver`）—— 因为"另一个 NPC 的
                    //   账本"是 Python 的物件（活体/冻结舱/磁盘/懒创建），C# 看不见它。
                    //   为什么仍然要在 C# 走一遍：收信人必须按**精确身份**解析（`ResolveCounterparty`
                    //   吃 `target_id`，重名时不会落到别人身上），而重名判据只有 C# 侧有。
                    if (otherIsPlayer)
                        return Fail("捎话的对象要是另一个 NPC（target 填他的真名）—— "
                                    + "玩家就在你面前，有话直接说即可。");
                    string words = ((string)args["message"] ?? "").Trim();
                    if (words.Length == 0)
                        return Fail("捎话要写清捎什么（message 是空的）。");
                    if (words.Length > MailMaxChars) words = words.Substring(0, MailMaxChars);
                    // `delivered` 由 Python 投递成功后补写（这里是"解析成功"，不是"已送到"）——
                    // 分开的理由见 agent/mailbox.py 文件头：没送到却说送到，模型会据此编剧情。
                    return Ok(new JObject
                    {
                        ["op"] = op,
                        ["target"] = target,
                        ["counterparty"] = cpName,
                        ["message"] = words,
                    });
                }

                default:
                    return Fail("unsupported social_relation op: " + op);
            }
        }

        // ═══════════════════════════════════════════════════════════════════════
        // ★ 引擎关系裁决码表（`UnitActionRelationSet.IsCreate(bool)` 的返回码）★
        //
        //   **0 = 引擎同意（可创建）；非 0 = 引擎拒绝，数字就是原因码。**
        //
        //   证据 = GameAssembly.dll 反汇编（RVA 由 Cpp2IL 转储给出，机器码逐条核对）：
        //     · `WorldUnitBase.CreateAction(action,isTip)`      @0x32B8D40
        //         if (!action.isCreateCall) { code = action.IsCreate(isTip);
        //                                     if (code != 0) return code; }   // 拒绝：什么都不做
        //         action.Create(); return code;                               // 只有 0 才跑 OnCreate
        //     · `UnitActionBase.IsCreate(isTip)`                @0x35083B0
        //         this.isTip = isTip; if (IsDayCost(day) == 0) this.isCreateCall = true;   // ★ 副作用
        //     · `WorldUnitBase.IsCreateAction(action,isTip)`    @0x32BDAC0  = `action.IsCreate(isTip)`
        //         —— 与落刀**同源**：所谓"预检"没有独立语义，且会写同一个实例。
        //     · `UnitActionRelationSet.IsCreate`                @0x36FC740 逐条门槛：
        //         2  目标是自己（unitID 相同）
        //         3  两人之间**已经有一条关系**（判据 `relationData.IsRelation(对方, None)`；
        //            type ∉ {None, Married} 全走这条 —— 结缘/拜师/收徒/认义父母/结义都会撞）
        //         4  认义父母（ParentBack）另有前置（GetParentUnits 命中）
        //         5  结婚/道侣（Married/Lover）同性
        //         6  结义（BrotherBack）异性
        //         7  已婚（Married：任一方 married 已占用）
        //         8  结婚（Married）但两人还不是道侣
        //         9  type=None 的解除路径（会 AddTip）
        //         （5/6 的判据是两处 0x46436C0 取值比较，按"结婚要异性、结义要同性"读作性别，属推断）
        //     · `UnitActionRelationSet.OnCreate`                @0x36FCF10
        //         `GetRelation(对方)` 读旧关系 → `ClearRelation(unit,toUnit)` **双向各一次**（@0x36FC370）
        //         → 写新关系 + AddIntim。
        //       ⇒ **一对人在引擎里只记一条关系**：能落刀 = 旧关系必然被删（**覆盖，不是叠加**）。
        //       ⇒ `RelationData.GetRelation` 是"先命中先返回的单个枚举"（@0x3CF2390），优先级
        //          父母 > 子女 > 私生 > 兄弟 > 义父母 > 义子女 > 义兄弟 > 夫妻 > 道侣 > 师父 > 徒弟
        //          —— 所以"道侣 + 师徒共存"在引擎层不存在，只能覆盖。
        //
        //   ★ 踩过的坑（09-17 收徒事故，用户关系被强拆）★
        //     预检与落刀**共用同一个 act 实例**时：`IsCreate` 先把该实例的 `isCreateCall` 置 true，
        //     紧接着的 `CreateAction` 见到这个标记就**跳过全部校验直接 `Create()/OnCreate()`**。
        //     于是"引擎说不能建（码 3：两人已是夫妻）"被我们撬成了"强行落刀 → ClearRelation 清掉
        //     夫妻 → 改写成师徒"，而返回码 3 还被当成"可以建"。**铁律两条**：
        //       ① 预检一律用**一次性实例**（用完即弃），绝不复用给落刀；
        //       ② 落刀只调**一次** `CreateAction`，它的返回码就是最终裁决（0 = 已执行）。
        // ═══════════════════════════════════════════════════════════════════════

        /// <summary>
        /// 缔结类"只问不落刀"的预检：**每次新建一次性实例**（理由见上面裁决码表的踩坑记录）。
        /// 返回 0 = 引擎同意；非 0 = 拒绝原因码；-2 = 没问出来（异常，**不等于**不能建）。
        /// </summary>
        internal static int ProbeRelationSet(WorldUnitBase actor, WorldUnitBase other, UnitRelationType type, int addClose)
        {
            try { return actor.IsCreateAction(new UnitActionRelationSet(other, type, addClose), false); }
            catch (Exception e)
            {
                try { ModMain.P("[Relation] 预检抛异常（按'没问出来'处理，落刀那次会再判）: " + e.Message); } catch { }
                return -2;
            }
        }

        /// <summary>
        /// 发起建立关系（动作挂 NPC，toUnit=玩家）。
        /// 真机实证：原版 UnitActionRoleRelation 剧情窗"正文空白、选项有字"——
        /// 选项文字取自 confItem.acceptText/rejectText（直接字段），正文走按 ID 查配置表的渲染链，
        /// dummy id（9000+type）永不命中 → 正文空。该渲染链不可控，故弃用原生剧情路径。
        /// 现路径：ShowDramaService 自制确认窗（正文/选项全可控）+ DramaGate 延迟 response——
        /// 本方法返回 __pending__ 标记，WsClient 憋住 response，玩家点选后才把真实结果回给 LLM。
        /// 确认后经 UnitActionRelationSet 直改关系簿（神识传音 NPC 自主关系同款 API）。
        /// 正文兜底链：askOverride（模型台词）> 真配置 askText > dummy 文案。
        ///
        /// **裁决（09-17 二次修正）**：引擎码语义 = **0 同意 / 非 0 拒绝**（表见上方注释块）。
        /// 预检放在**弹窗之前**（引擎说不能建就不该让玩家白点一次"同意"），落刀在 onOk 里用
        /// **另一个新实例**只调一次；`create_ret` 回传最终裁决，`create_ret == -1` = 预检就拒、未落刀。
        /// 现状快照 `relation_before/after` + `diag`（境界 + 师徒位占用，只报事实）供对账。
        /// </summary>
        internal static JObject StartRelationAction(WorldUnitBase npc, WorldUnitBase player, UnitRelationType type, string op, string askOverride = null)
        {
            var item = RoleRelationItemOfType(type) ?? CreateDummyRelationItem(type);
            if (item == null)
            {
                // 文案构建全失败的最后一条路：直连引擎（新实例、只调一次；语义同上面裁决码表）
                int fbRet = -1;
                bool fbVerified = false;
                string fbName = type.ToString();
                try { fbName = UnitSnapshot.RelationCn(type.ToString()); } catch { }
                try
                {
                    fbRet = npc.CreateAction(new UnitActionRelationSet(player, type, 60), true);
                    try { fbVerified = npc.data.unitData.relationData.IsRelation(player, type); } catch { }
                }
                catch (Exception e2) { return Fail($"[{op}] 关系文案构建失败且直写失败: {e2.Message}"); }
                return Ok(new JObject
                {
                    ["op"] = op,
                    ["target"] = (string)npc.data.unitData.propertyData.GetName(),
                    ["relation"] = fbName,
                    ["verified"] = fbVerified,
                    ["create_ret"] = fbRet,
                    ["note"] = $"已直写关系(UnitActionRelationSet/{type} 60)",
                });
            }
            string npcName = "", playerName = "";
            try { npcName = (string)npc.data.unitData.propertyData.GetName(); } catch { npcName = "对方"; }
            try { playerName = (string)player.data.unitData.propertyData.GetName(); } catch { playerName = "你"; }
            string relName; string ask, accept, reject; int addClose;
            try { relName = string.IsNullOrEmpty(item.relation) ? type.ToString() : item.relation; } catch { relName = type.ToString(); }
            try { ask = string.IsNullOrEmpty(item.askText) ? $"{npcName}愿与{playerName}缔结{relName}。" : item.askText; } catch { ask = $"{npcName}愿与{playerName}缔结{relName}。"; }
            if (!string.IsNullOrWhiteSpace(askOverride)) ask = askOverride.Trim();
            try { accept = string.IsNullOrEmpty(item.acceptText) ? "愿意" : item.acceptText; } catch { accept = "愿意"; }
            try { reject = string.IsNullOrEmpty(item.rejectText) ? "拒绝" : item.rejectText; } catch { reject = "拒绝"; }
            try { addClose = item.addClose > 0 ? item.addClose : 20; } catch { addClose = 20; }

            // ── 预检（一次性实例·只问不落刀）放在**弹窗之前** ──
            // 引擎说不能建时，让玩家先白点一次"同意"再告诉他"其实不行"是坏体验；此刻关系簿一字未动，
            // 回报里也就不含任何既成事实。0 = 同意；非 0 = 拒绝原因码（表见上方注释块）；-2 = 没问出来。
            string relBefore = RelationOf(npc, player);
            int precheck = ProbeRelationSet(npc, player, type, addClose);
            if (precheck > 0)
            {
                try { ModMain.P("[Relation] 预检拒绝 引擎码=" + precheck + " type=" + type + " op=" + op + " 未落刀"); } catch { }
                return Ok(new JObject
                {
                    ["op"] = op, ["target"] = npcName, ["relation"] = relName,
                    ["verified"] = false,
                    ["precheck"] = precheck,                    // 非 0 = 引擎拒绝的原因码（表见上方注释块）
                    ["create_ret"] = -1,                        // -1 = 预检就拒，没落刀
                    ["relation_before"] = relBefore,
                    ["relation_after"] = RelationOf(npc, player),   // 应与 before 相同（未动）
                    ["diag"] = RelationDiag(npc, player),
                });
            }

            return NativeActionService.Claimed(npc, () => ShowDramaService.ShowConfirm(
                ModIds.DramaSocialRelationBase,
                player, npc,
                $"{npcName}向{playerName}提出<{relName}>：\n{ask}",
                accept, reject,
                onOk: () =>
                {
                    // 落刀：**新实例**、只调一次；返回码就是引擎的最终裁决（0 = 已执行 OnCreate；
                    // 非 0 = 拒绝且什么都没做）。绝不把预检那个实例递进来 —— 它已被 `isCreateCall`
                    // 标记成"免检"，会让引擎跳过全部校验直接 Create()（09-17 强拆事故的成因）。
                    // 落刀前的现状快照再取一次：弹窗期间的状态变化不该被 before/after 掩盖。
                    string relBeforeKnife = RelationOf(npc, player);
                    int createRet;
                    try { createRet = npc.CreateAction(new UnitActionRelationSet(player, type, addClose), true); }
                    catch (Exception e) { return Fail($"RelationSet 抛异常: {e.Message}"); }
                    bool verified = false;
                    try { verified = npc.data.unitData.relationData.IsRelation(player, type); } catch { }
                    if (!verified && type == UnitRelationType.BrotherBack) try { verified = npc.data.unitData.relationData.IsRelation(player, UnitRelationType.Brother); } catch { }
                    if (!verified && type == UnitRelationType.BrotherBack)
                    {
                        try
                        {
                            string npcId = (string)npc.data.unitData.unitID; string playerId = (string)player.data.unitData.unitID;
                            var nr = npc.data.unitData.relationData; var pr = player.data.unitData.relationData;
                            if (!nr.brotherBack.Contains(playerId)) nr.brotherBack.Add(playerId);
                            if (!pr.brotherBack.Contains(npcId)) pr.brotherBack.Add(npcId);
                            try { verified = nr.IsRelation(player, UnitRelationType.BrotherBack); } catch { }
                            if (!verified) { if (!nr.brother.Contains(playerId)) nr.brother.Add(playerId); if (!pr.brother.Contains(npcId)) pr.brother.Add(npcId); verified = true; }
                        }
                        catch (Exception e2) { return Fail($"RelationSet 后兜底失败: {e2.Message}"); }
                    }
                    return Ok(new JObject
                    {
                        ["op"] = op, ["target"] = npcName, ["relation"] = relName,
                        ["verified"] = verified,
                        ["precheck"] = precheck,                    // 0 = 引擎同意；-2 = 没问出来
                        ["create_ret"] = createRet,                 // 0 = 引擎已执行；非 0 = 拒绝原因码
                        ["relation_before"] = relBeforeKnife,       // 落刀前那一对关系（中文，无则"陌生"）
                        ["relation_after"] = RelationOf(npc, player),
                        ["diag"] = RelationDiag(npc, player),       // 境界 + 师徒位占用（只报事实）
                    });
                },
                okTip: $"已与{npcName}缔结{relName}",
                noTip: $"玩家婉拒了缔结{relName}的请求"));
        }

        /// <summary>
        /// 关系动作的**非玩家对手方**分支（09-16）：免窗、字段直写。
        ///
        /// 为什么必须免窗：确认窗（ShowConfirm + DramaGate）存在的唯一理由是"取玩家的一次点击"；
        /// NPC 之间没有这一环 —— 弹了没人点，只会把整轮群聊挂到 120s 超时。
        /// 落刀与玩家路径**同源**：`UnitActionRelationSet` 是引擎 API（"神识传音 NPC 自主关系"同款，
        /// 见 `StartRelationAction` 的注释），与确认窗无关；校验/双写兜底与它的 onOk 逐条对齐。
        /// 裁决码语义与"预检必须一次性实例"同玩家路径（表见上方注释块）。
        /// </summary>
        internal static JObject DirectEstablish(WorldUnitBase npc, WorldUnitBase other, UnitRelationType type, string op, string cpName)
        {
            string npcName = UnitIdentity.Name(npc, "对方");
            string relName = type.ToString();
            int addClose = 20;
            try
            {
                var item = CreateDummyRelationItem(type);
                if (item != null)
                {
                    if (!string.IsNullOrEmpty(item.relation)) relName = item.relation;
                    if (item.addClose > 0) addClose = item.addClose;
                }
            }
            catch { }
            // 免窗路径同理要留裁决码（09-17）：isTip 保持 false —— 这条路上没有玩家在等确认，
            // 两个 NPC 之间弹一条提示只会变成噪声；但"有没有真的落刀"必须回传。
            string relBefore = RelationOf(npc, other);

            // 预检（与玩家路径同源，只把 isTip 关掉）：**一次性实例**（理由见上方裁决码表的踩坑记录：
            // 预检与落刀共用实例会让 `isCreateCall` 被置位 → 落刀跳过全部校验直接 Create()）。
            // 返回 0 = 引擎同意；非 0 = 拒绝原因码 ⇒ **不落刀**，如实收口；-2 = 没问出来 ⇒ 照旧落刀。
            int precheck = ProbeRelationSet(npc, other, type, addClose);
            if (precheck > 0)
            {
                try { ModMain.P("[Relation] 免窗预检拒绝 引擎码=" + precheck + " type=" + type + " op=" + op + " 未落刀"); } catch { }
                return ToolArgs.WithCounterparty(Ok(new JObject
                {
                    ["op"] = op, ["target"] = npcName, ["relation"] = relName, ["verified"] = false,
                    ["precheck"] = precheck, ["create_ret"] = -1,
                    ["relation_before"] = relBefore, ["relation_after"] = RelationOf(npc, other),
                    ["diag"] = RelationDiag(npc, other),
                }), cpName);
            }

            int createRet = -1;
            try { createRet = npc.CreateAction(new UnitActionRelationSet(other, type, addClose), false); }
            catch (Exception e) { return Fail($"[{op}] 关系直写失败: {e.Message}"); }
            bool verified = false;
            try { verified = npc.data.unitData.relationData.IsRelation(other, type); } catch { }
            if (!verified && type == UnitRelationType.BrotherBack)
                try { verified = npc.data.unitData.relationData.IsRelation(other, UnitRelationType.Brother); } catch { }
            if (!verified && type == UnitRelationType.BrotherBack)
            {
                // 结义单向写入不可靠（真机样本）→ 双方关系簿双写兜底（与 StartRelationAction.onOk 同款）
                try
                {
                    string npcId = (string)npc.data.unitData.unitID;
                    string otherId = (string)other.data.unitData.unitID;
                    var nr = npc.data.unitData.relationData;
                    var or = other.data.unitData.relationData;
                    if (!nr.brotherBack.Contains(otherId)) nr.brotherBack.Add(otherId);
                    if (!or.brotherBack.Contains(npcId)) or.brotherBack.Add(npcId);
                    try { verified = nr.IsRelation(other, UnitRelationType.BrotherBack); } catch { }
                    if (!verified)
                    {
                        if (!nr.brother.Contains(otherId)) nr.brother.Add(otherId);
                        if (!or.brother.Contains(npcId)) or.brother.Add(npcId);
                        verified = true;
                    }
                }
                catch (Exception e2) { return Fail($"[{op}] 关系直写后兜底失败: {e2.Message}"); }
            }
            return ToolArgs.WithCounterparty(Ok(new JObject
            {
                ["op"] = op, ["target"] = npcName, ["relation"] = relName, ["verified"] = verified,
                ["precheck"] = precheck,
                ["create_ret"] = createRet,
                ["relation_before"] = relBefore,
                ["relation_after"] = RelationOf(npc, other),
                ["diag"] = RelationDiag(npc, other),
            }), cpName);
        }

        /// <summary>
        /// 解除关系的**非玩家对手方**分支（09-16）：免窗、字段直写。
        /// 解除本就是单方面行为（`BreakFieldsDirect` 双向写字段），不需要对方同意 —— 对 NPC 同样成立。
        /// </summary>
        internal static JObject DirectBreak(WorldUnitBase npc, WorldUnitBase other, string op, string cpName)
        {
            string npcName = UnitIdentity.Name(npc, "对方");
            string relName = BreakRelName(op);
            try { BreakFieldsDirect(op, npc, other); }
            catch (Exception e) { return Fail($"[{op}] 解除关系直写失败: {e.Message}"); }
            bool still = true;
            try { still = BreakStillRelated(op, npc, other); } catch { }
            return ToolArgs.WithCounterparty(Ok(new JObject
            {
                ["op"] = op, ["target"] = npcName, ["relation"] = relName, ["readback_still_related"] = still,
            }), cpName);
        }

        /// <summary>
        /// 成婚的**非玩家对手方**分支（09-16）：免窗、字段直写（married 互写 + lover 互删）。
        /// **不弹原版成婚剧情 22201** —— 那是给玩家看的过场（unitLeft/unitRight 也按"左=玩家"的立绘位约定）。
        /// </summary>
        internal static JObject DirectMarry(WorldUnitBase npc, WorldUnitBase other, string op, string cpName)
        {
            string npcName = UnitIdentity.Name(npc, "对方");
            string npcId = null, otherId = null;
            try { npcId = (string)npc.data.unitData.unitID; } catch { }
            try { otherId = (string)other.data.unitData.unitID; } catch { }
            if (string.IsNullOrEmpty(npcId) || string.IsNullOrEmpty(otherId))
                return Fail("读不到双方的单位 ID，无法成婚");
            string loverNote = "lover 未动";
            try
            {
                npc.data.unitData.relationData.married = otherId;
                other.data.unitData.relationData.married = npcId;
                try
                {
                    npc.data.unitData.relationData.lover.Remove(otherId);
                    other.data.unitData.relationData.lover.Remove(npcId);
                    loverNote = "lover 已互删";
                }
                catch { loverNote = "lover 互删失败（不影响成婚事实）"; }
            }
            catch (Exception e) { return Fail("成婚直写失败: " + e.Message); }
            return ToolArgs.WithCounterparty(Ok(new JObject
            {
                ["op"] = op, ["target"] = npcName, ["relation"] = "夫妻", ["lover"] = loverNote,
            }), cpName);
        }

        /// <summary>
        /// 一对人"那一条"关系（中文）—— **引擎口径：一对人只记一条**。
        /// 依据：`RelationData.GetRelation(toUnit)` 返回**单个枚举**（不是类型集合），
        /// `UnitActionRelationSet` 内部还带 `ClearRelation(unit,toUnit)` + `origiType`
        /// —— 设新关系是**覆盖**语义。取不到给空串（渲染层不报这一项），绝不冒充"陌生"。
        /// </summary>
        internal static string RelationOf(WorldUnitBase a, WorldUnitBase b)
        {
            try { return UnitSnapshot.RelationCn(a.data.unitData.relationData.GetRelation(b).ToString()); }
            catch { return ""; }
        }

        /// <summary>
        /// 关系缔结失败的现状快照（09-17）：只报**事实**，不猜原因 ——
        /// 境界（收徒/拜师的门槛，游戏自己的更新日志："优化了收徒或拜师时，
        /// NPC 对于双方的境界判定过于严格的问题"）＋师徒位占用（另一类门槛：
        /// 徒弟位为空 / 师傅徒弟上限 3；判据同神识传音 types36_fulldll 的收徒/拜师前置）。
        /// 取不到的项**不写键**（渲染层按缺键跳过），绝不写 0 冒充"没有"。
        /// </summary>
        internal static JObject RelationDiag(WorldUnitBase npc, WorldUnitBase target)
        {
            var d = new JObject();
            try { d["npc_realm"] = UnitSnapshot.RealmText(npc); } catch { }
            try { d["player_realm"] = UnitSnapshot.RealmText(target); } catch { }
            try { d["npc_students"] = RelCount(npc.data.unitData.relationData.student); } catch { }
            try { d["npc_masters"] = RelCount(npc.data.unitData.relationData.master); } catch { }
            try { d["player_students"] = RelCount(target.data.unitData.relationData.student); } catch { }
            try { d["player_masters"] = RelCount(target.data.unitData.relationData.master); } catch { }
            return d;
        }

        /// <summary>IL2CPP 关系容器计数（禁 foreach：走 dynamic 的 Count）。
        /// null / 取不到一律 **-1 = 未知**——"读不到"与"没有"在这条链上是两件事。</summary>
        internal static int RelCount(object list)
        {
            try { return list == null ? -1 : (int)((dynamic)list).Count; } catch { return -1; }
        }

        internal static ConfRoleRelationItem CreateDummyRelationItem(UnitRelationType type)
        {
            try
            {
                string ask = "", accept = "愿意", reject = "拒绝", rel = type.ToString();
                int addClose = 20;
                switch (type)
                {
                    case UnitRelationType.Lover: rel = "道侣"; ask = "愿与君结为道侣，共参大道？"; addClose = 30; break;
                    case UnitRelationType.BrotherBack: rel = "结义"; ask = "愿与君结义为兄弟，同生共死？"; addClose = 20; break;
                    case UnitRelationType.Master: rel = "师徒"; ask = "愿拜君为师，聆听教诲？"; addClose = 15; break;
                    case UnitRelationType.Student: rel = "师徒"; ask = "愿收君为徒，传道授业？"; addClose = 15; break;
                    case UnitRelationType.ParentBack: rel = "义父母"; ask = "愿认君为义父母，承欢膝下？"; addClose = 15; break;
                    default: ask = $"愿与君缔结{rel}？"; break;
                }
                // id=9000+type, group=0, gender/age 0=不限, relationRequire 0
                return new ConfRoleRelationItem(9000 + (int)type, 0, (int)type, 0, 0, 0, 0, 0, rel, "", addClose, addClose, 5, ask, accept, reject, $"{rel}成功", $"{rel}失败", $"{rel}成功", $"{rel}失败", $"大事：{rel}", $"大事：{rel}");
            }
            catch { return null; }
        }

        /// <summary>
        /// 发起解除关系（自制确认窗 + DramaGate 延迟 response，玩家同意后才执行）。
        /// 神识传音 NPC-AI 实证（types36_fulldll L38026/38040/38076 等 case 21/22/23）：
        /// BreakWith 必须先 Init(对方单位) 才知道解除与谁的关系——
        ///   var act = new UnitActionRoleBreakWith(); ((UnitActionBase)act).Init(对方); npc.CreateAction(act, false);
        /// 旧版无 Init（真机待验是否就是"解除定位不到"的原因）；CreateAction bool 参照神识传音用 false。
        /// 注意：BreakWith.OnCreate 是黑盒，若其内部再弹原版确认造成双窗，真机确认后降级为字段直写。
        /// </summary>
        internal static JObject StartBreakWith(WorldUnitBase npc, WorldUnitBase target, string op, string askOverride = null)
        {
            string npcName = "", targetName = "";
            try { npcName = (string)npc.data.unitData.propertyData.GetName(); } catch { npcName = "对方"; }
            try { targetName = (string)target.data.unitData.propertyData.GetName(); } catch { targetName = "你"; }
            string relName = BreakRelName(op);
            string text = string.IsNullOrWhiteSpace(askOverride)
                ? $"{npcName}想与你解除{relName}关系，是否同意？"
                : $"{npcName}想与你解除{relName}关系：\n{askOverride.Trim()}";
            return NativeActionService.Claimed(npc, () => ShowDramaService.ShowConfirm(
                ModIds.DramaSocialRelationBase,
                target, npc,
                text,
                "同意", "不同意",
                onOk: () =>
                {
                    // 第一段：BreakWith 发起。`create_ret` = 引擎码，**0 = 原生解除动作已执行**
                    // （09-17 二次修正：旧注释"1=触发/0=创建被拒"是反的，见上方关系裁决码表）。
                    int created = -1, state = -1;
                    string bwErr = null;
                    int bwPre = -2;                              // 引擎码：0 = 同意；非 0 = 拒绝原因码
                    string relBeforeBw = RelationOf(npc, target);
                    try
                    {
                        // 预检用**一次性实例**（理由见上方裁决码表的踩坑记录）。**只报不拦** ——
                        // 这条路下面还有"字段直写兜底"（解除是单方面行为，直写就能成），
                        // 原生动作建不起来不等于解除失败 ⇒ 绝不能据此提前 return。
                        try
                        {
                            var probe = new UnitActionRoleBreakWith();
                            ((UnitActionBase)probe).Init(target);
                            bwPre = npc.IsCreateAction(probe, false);
                        }
                        catch (Exception e) { try { ModMain.P("[Relation] 解除预检抛异常（按未知处理）: " + e.Message); } catch { } }

                        // 落刀用**另一个新实例**：绝不复用上面那个（isCreateCall 免检陷阱）。
                        var act = new UnitActionRoleBreakWith();
                        ((UnitActionBase)act).Init(target);
                        created = npc.CreateAction(act, false);
                        try { state = (int)((dynamic)act).state; } catch { }   // BreakWith 基类链未实锤含 state，dynamic 试探
                    }
                    catch (Exception e) { bwErr = e.Message; }

                    // 读回一次：原生动作可能分帧跑（state 机），当场读回"关系仍在"不等于失败。
                    bool stillAfterNative = BreakStillRelated(op, npc, target);
                    if (created == 0 && !stillAfterNative)
                        return Ok(new JObject
                        {
                            ["op"] = op, ["target"] = npcName, ["relation"] = relName,
                            ["precheck"] = bwPre,
                            ["relation_before"] = relBeforeBw, ["relation_after"] = RelationOf(npc, target),
                            ["create_ret"] = created, ["state_at_return"] = state,
                            ["readback_still_related"] = false,
                        });

                    // 第二段：原生动作没跑起来（引擎码非 0 / 抛异常）或跑完关系仍在 → 字段直写兜底
                    // （marry 同款实证路径；`Remove` 幂等，重复执行不会写坏关系簿）。
                    try { BreakFieldsDirect(op, npc, target); }
                    catch (Exception e) { return Fail($"[{op}] BreakWith 未生效(create_ret={created}{(bwErr != null ? "，" + bwErr : "")})且直写失败: " + e.Message); }

                    bool still = BreakStillRelated(op, npc, target);
                    return Ok(new JObject
                    {
                        ["op"] = op, ["target"] = npcName, ["relation"] = relName,
                        ["precheck"] = bwPre,
                        ["relation_before"] = relBeforeBw, ["relation_after"] = RelationOf(npc, target),
                        ["create_ret"] = created, ["state_at_return"] = state, ["bw_err"] = bwErr,
                        ["readback_still_related"] = still,
                    });
                },
                okTip: "已同意解除",
                noTip: $"玩家不同意解除{relName}"));
        }

        /// <summary>解除类字段直写（双向）。married 置空串的合法性依据：神识传音婚配前置 `married != null && GetUnit(married) != null`，空串即视为未婚。</summary>
        internal static string BreakFieldsDirect(string op, WorldUnitBase npc, WorldUnitBase target)
        {
            string npcId = (string)npc.data.unitData.unitID;
            string targetId = (string)target.data.unitData.unitID;
            var nr = npc.data.unitData.relationData;
            var tr = target.data.unitData.relationData;
            switch (op)
            {
                case "divorce":
                    tr.married = ""; nr.married = "";
                    return "married 双向置空";
                case "jie_chu_jie_yuan":
                    tr.lover.Remove(npcId); nr.lover.Remove(targetId);
                    return "lover 双向 Remove";
                case "jie_chu_bai_shi":      // NPC 拜玩家为师：npc.master 含玩家 / player.student 含 npc
                    nr.master.Remove(targetId); tr.student.Remove(npcId);
                    return "master/student 双向 Remove";
                case "jie_chu_shou_tu":      // NPC 收玩家为徒：npc.student 含玩家 / player.master 含 npc
                    nr.student.Remove(targetId); tr.master.Remove(npcId);
                    return "student/master 双向 Remove";
                case "jie_chu_jie_yi":
                    {
                        bool a = nr.brotherBack.Remove(targetId);
                        bool b = tr.brotherBack.Remove(npcId);
                        bool c = nr.brother.Remove(targetId);
                        bool d = tr.brother.Remove(npcId);
                        return $"brotherBack 双向 Remove({a},{b}) + brother 兼容({c},{d})";
                    }
                default:
                    throw new Exception("unsupported break op: " + op);
            }
        }

        /// <summary>直写后读回：NPC 视角与玩家是否仍处于该关系（best-effort）。</summary>
        internal static bool BreakStillRelated(string op, WorldUnitBase npc, WorldUnitBase target)
        {
            try
            {
                var nr = npc.data.unitData.relationData;
                switch (op)
                {
                    case "divorce": return nr.IsRelation(target, UnitRelationType.Married);
                    case "jie_chu_jie_yuan": return nr.IsRelation(target, UnitRelationType.Lover);
                    case "jie_chu_bai_shi": return nr.IsRelation(target, UnitRelationType.Master);
                    case "jie_chu_shou_tu": return nr.IsRelation(target, UnitRelationType.Student);
                    case "jie_chu_jie_yi": return nr.IsRelation(target, UnitRelationType.BrotherBack) || nr.IsRelation(target, UnitRelationType.Brother);
                    default: return false;
                }
            }
            catch { return false; }
        }

        /// <summary>解除类 op → 中文关系名（确认窗文案用）。</summary>
        internal static string BreakRelName(string op)
        {
            switch (op)
            {
                case "jie_chu_jie_yuan": return "道侣";
                case "divorce": return "夫妻";
                case "jie_chu_bai_shi":
                case "jie_chu_shou_tu": return "师徒";
                case "jie_chu_jie_yi": return "结义";
                default: return "该";
            }
        }

        /// <summary>查 g.conf.roleRelation 里 type == (int)relationType 的配置条目（ConfRoleRelationItem.type 实锤对应枚举）。</summary>
        internal static ConfRoleRelationItem RoleRelationItemOfType(UnitRelationType type)
        {
            try
            {
                var conf = g.conf.roleRelation;   // ConfRoleRelation : ConfRoleRelationBase
                if (conf == null) return null;
                var dyn = (dynamic)conf.allConfList;   // IReadOnlyList<ConfRoleRelationItem>，泛型被 IL2CPP 抹除
                int cnt = (int)dyn.Count;
                for (int i = 0; i < cnt; i++)
                {
                    var item = (dynamic)dyn[i];
                    if (item != null && (int)item.type == (int)type)
                        return item as ConfRoleRelationItem;
                }
            }
            catch { }
            return null;
        }

        /// <summary>捎话（`social_relation op=chuan_hua`）一句话的字数上限。
        /// ★ 与 Python `agent/mailbox.MAIL_MAX_CHARS` **同名同值**：这里是真正生效的那一刀，
        ///   Python 只是给"直调 deliver()"的路径兜同一个值。改一处必须改另一处（有契约测试钉）。</summary>
        internal const int MailMaxChars = 200;
    }
}
