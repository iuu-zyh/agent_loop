/// <summary>
/// ToolArgs —— 参数 → 单位：发起方/对手方解析、JSON 取值、同格判定。
/// （11-19 由 6711 行的 `ToolExecutor.cs` 拆出，11-20 按 Python 的域归并；成员逐字搬移。）
/// 跨类调用一律 `<类名>.<方法>`；`internal` = 仅本程序集可见。
/// </summary>
using System;
using System.Collections.Generic;
using Newtonsoft.Json.Linq;
using static AgentLoopBridge.ToolResult;

namespace AgentLoopBridge
{
    internal static class ToolArgs
    {
        internal static long LongOf(JToken t)
        {
            try { return t == null ? 0 : (long)t; } catch { return 0; }
        }

        internal static bool IsPlayerUnit(WorldUnitBase u)
        {
            try { return u != null && g.world.playerUnit != null && u.Pointer == g.world.playerUnit.Pointer; }
            catch { return false; }
        }

        // ══════════════════════════════════════════════════════════════
        // 指人解析：**先精确 id，后按名**（09-17）
        //
        // 背景：C# 原先只有名字可用，一律 `UnitLookup.Resolve(名字)` = **全图按名扫**，
        // 重名时命中第一个 —— **不报错、不缺字段**，只是把动作落到另一个人身上。
        // 而"这个名字是哪个 unitID"在 Python 侧本来就知道（群聊 = `Room.member_unit_ids`，
        // 单聊 = 开窗时 C# 自己交上去的），没有理由让 C# 再猜一次。
        //
        // ★ 为什么这里的"id 失效回退按名"与 `inspect_unit` 的"给了 id 就只认它"不矛盾：
        //   区别在**谁给的 id**。
        //     · `inspect_unit.unit_id` 是**模型**填的 —— 它明确要求"精确查这一个"，
        //       回退按名就是把"明确失败"降级成"一次静默猜测"，所以 `ResolveEx` 契约拒绝回退。
        //     · `*_id` 是**harness（Python）填的**（模型看不到这些键）—— 它是一条**提示**，
        //       陈旧时（换存档 / 单位消失）回退按名正是今天的行为，不比现状差。
        //   两者的共同底线：**绝不静默落回玩家**（见 ResolveCounterparty）。
        // ══════════════════════════════════════════════════════════════

        /// <summary>解析 `<paramref name="key"/>`（+ 同名 `_id` 提示）指向的单位。
        /// 有 id 就走 ②unitID精确；id 失效或未给 ⇒ 回落按名（登记表 ⓪ → 全图 ④）。</summary>
        internal static WorldUnitBase ResolveArgExact(JObject args, string key, string name)
        {
            string nm = (name ?? "").Trim();
            if (nm.Length == 0) return null;
            string id = ((string)args[key + "_id"] ?? "").Trim();
            if (id.Length > 0)
            {
                System.Collections.Generic.List<UnitLookup.Candidate> _;
                var byId = UnitLookup.ResolveEx(nm, id, out _);
                if (byId != null) return byId;
                // id 失效（换存档/单位消失）→ 回落按名。如实留痕：这条回落意味着
                // "精确身份没用了"，将来若频繁出现就是存档/身份同步出了问题。
                ModMain.P("[ToolExecutor] " + key + "_id 失效（" + nm + " → " + id + "），回落按名解析");
            }
            return UnitLookup.Resolve(nm);
        }

        // ---------------------------------------------------------------- 动作工具的共同开场

        /// <summary>
        /// 动作工具的共同开场之一：取发起方（`initiator` 名字 + `initiator_id` 精确 id）。
        /// 失败帧放进 <paramref name="fail"/> —— 调用方一句 `if (bf != null) return bf;` 即可。
        ///
        /// 【为什么收在这里】六个动作工具（关系/位移/互动/赠予/取得/组队）开头这七行**逐字相同**
        /// （11-20 归并时对着六份比对过）：取发起方 → 已故就带死亡事实报错 → 否则报"未找到发起方 X"。
        /// 以前改其中一处的文案，另外五处不会跟着改；而这句话是**给模型看的**，措辞不一致它就会
        /// 按不同的错去重试（"名字打错"和"人已死"是两种完全不同的自纠路径）。
        /// </summary>
        internal static WorldUnitBase BindActor(JObject args, out string initiator, out JObject fail)
        {
            initiator = (string)args["initiator"] ?? "";
            var wub = ResolveActor(args);
            if (wub != null)
            {
                fail = null;
                // 11-20：这一行回答"发起方是靠 id 精确认出来的，还是又回落按名扫了"。
                //   两者在**没出问题时**长得一模一样，出事时（同名/换存档）才分得出责任 ——
                //   而那时日志已经写完了。所以现在打。
                try
                {
                    string aid = ((string)args["initiator_id"] ?? "").Trim();
                    ModMain.P("[ToolArgs] 发起方=" + initiator + (aid.Length > 0 ? "（带 id 精确命中）" : "（按名解析）"));
                }
                catch { }
                return wub;
            }
            // 死者兜底（10-15 待办②）：发起方已故 ⇒ 带死亡事实报错，别让模型当成名字打错
            string dn = UnitSnapshot.DeadNotice((string)args["initiator_id"] ?? "", initiator);
            fail = dn != null ? Fail(dn) : Fail("未找到发起方 " + initiator);
            try
            {
                ModMain.P("[ToolArgs] 发起方解析失败：initiator=" + initiator
                          + (dn != null ? "（已身故，已给出死亡事实）" : "（查无此人）"));
            }
            catch { }
            return null;
        }

        /// <summary>发起方（`initiator`）。恒由 harness 注入，模型**看不到**这个键，
        /// 所以它带的 `initiator_id` 是纯 Python 侧信息 —— "模型只看到中文名"完全不受影响。</summary>
        internal static WorldUnitBase ResolveActor(JObject args)
        {
            string initiator = (string)args["initiator"] ?? "";
            return ResolveArgExact(args, "initiator", initiator);
        }

        internal static WorldUnitBase ResolveCounterparty(JObject args, out string counterparty, out JObject fail)
        {
            counterparty = "";
            fail = null;
            string targetName = ((string)args["target"] ?? "").Trim();
            if (string.IsNullOrEmpty(targetName))
                return g.world.playerUnit;
            var u = ResolveArgExact(args, "target", targetName);
            if (u == null)
            {
                // 删掉 `ResolveInner` 的 ③ 分支之后，"模型把 unitID 填进 target"不再碰巧能用。
                // 这里给一句**能自纠**的话：查一下反查表就知道那串是不是本存档的 unitID。
                string looksLikeId = "";
                try { looksLikeId = UnitIdRegistry.NameOf(targetName); } catch { }
                if (!string.IsNullOrEmpty(looksLikeId))
                    fail = Fail("「" + targetName + "」是 unitID（属于「" + looksLikeId
                                + "」），不是人物真名。target 只填真名 —— 精确身份由系统自动携带，"
                                + "不要填进 target。");
                else
                {
                    // 死者兜底（10-15 待办②）：对手方已故 ⇒ 带死亡事实报错（未命中落回原报错）
                    string dn = UnitSnapshot.DeadNotice((string)args["target_id"] ?? "", targetName);
                    fail = dn != null
                        ? Fail(dn)
                        : Fail("未找到「" + targetName + "」（target 要填对方真名；在场名单或 search_units 可查）");
                }
                return null;
            }
            if (IsPlayerUnit(u))
                return u;
            counterparty = UnitIdentity.Name(u, targetName);
            return u;
        }

        /// <summary>给成功结果的 data 挂上非玩家对手方名（对手方是玩家时什么都不做）。</summary>
        internal static JObject WithCounterparty(JObject res, string counterparty)
        {
            if (string.IsNullOrEmpty(counterparty) || res == null) return res;
            var d = res["data"] as JObject;
            if (d != null) d["counterparty"] = counterparty;
            return res;
        }

        /// <summary>发起方与**任意对手方**的好感：两个对象都在手里，直接读 ——
        /// **不许**再拿名字回去解析（那正是同名时报错数的那条路，见 SocialRelation 里的说明）。</summary>
        internal static int GetIntimBetween(WorldUnitBase a, WorldUnitBase b)
        {
            try { return SafeInt(a.data.unitData.relationData.GetIntim(b), 0); } catch { return 0; }
        }

        internal static bool SameGrid(WorldUnitBase a, WorldUnitBase b)
        {
            if (a == null || b == null) return false;
            var da = a.data.unitData;
            var db = b.data.unitData;
            return da.pointX == db.pointX && da.pointY == db.pointY;
        }

        // ★ 原先这里有个 `GetIntimWith(string target)`：`UnitLookup.Resolve(名字)` → 读他与玩家的好感。
        //   它被 `add_intim` / `reduce_intim` 用来算 before/after，但**发起方的对象就在手上**
        //   （`wub`）—— 按名重解析一遍是同名时的静默错误：动作落在 A 身上、数字读的是 B，
        //   两次都解析到同一个 B ⇒ `actual_delta` **恒为 0**，模型读成"没生效"而重复落刀。
        //   现在三处一律 `GetIntimBetween(wub, player)`（对手方是玩家时 `player` 就是玩家对象，
        //   两个分支本就是同一个表达式），这个辅助函数不再需要。

        internal static int SafeInt(JToken tok, int def)
        {
            try { return tok == null ? def : (int)tok; }
            catch { return def; }
        }
    }
}
