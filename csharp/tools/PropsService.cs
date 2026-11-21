/// <summary>
/// PropsService —— 背包与灵石转移（含补偿回滚）。
/// （11-19 由 6711 行的 `ToolExecutor.cs` 拆出，11-20 按 Python 的域归并；成员逐字搬移。）
/// 跨类调用一律 `<类名>.<方法>`；`internal` = 仅本程序集可见。
/// </summary>
using System;
using System.Collections.Generic;
using Newtonsoft.Json.Linq;
using static AgentLoopBridge.ToolResult;

namespace AgentLoopBridge
{
    internal static class PropsService
    {
        /// <summary>
        /// 读单位**随身**灵石持有数（道具 ID=10001，`DataProps.GetPropsNum`）。
        /// **读不到返回 -1**（调用方必须当"未知"处理，绝不能当成 0）。
        ///
        /// 删掉 `totalSchoolMoney` 兜底（用户拍板"链路确定了就别再兜底"）：
        /// 那是**宗门钱**，不是随身灵石 —— 一个**语义不同的账户**。旧行为在读取失败时
        /// 把一个貌似合理的数字交给模型，模型据此报价/赠送/判断够不够买，实际看的是另一个钱袋，
        /// 而且**没有任何信号**。宁可显式说"读不到"，也不要编一个数。
        /// </summary>
        internal static int ReadLingshiHeld(WorldUnitBase u)
        {
            try { return ((DataProps)u.data.unitData.propData).GetPropsNum(10001); }
            catch { return -1; }
        }

        /// <summary>
        /// NPC→玩家 灵石转账（货币字段直写：CostPropItem(10001) 发起方扣 / RewardPropMoney 玩家加）。
        /// 带补偿回滚：NPC 已扣、玩家未收 → 原样加回发起方，防吞币。
        /// 返回实际到账数（≤0 时 err 带失败原因）。混合路径循环与纯灵石确认窗 onOk 共用。
        /// </summary>
        internal static int TransferLingshi(WorldUnitBase from, WorldUnitBase player, int count, out string err)
        {
            err = null;
            int haveMoney = ReadLingshiHeld(from);
            if (haveMoney < 0) { err = "读不到发起方的灵石持有数（GetPropsNum 失败），已中止转账"; return 0; }
            int actual = Math.Min(count, haveMoney);
            if (actual <= 0) { err = $"发起方灵石不足（持有 {haveMoney}）"; return 0; }
            try
            {
                from.data.CostPropItem(10001, actual, false);
                try { player.data.RewardPropMoney(actual); }
                catch (Exception e2)
                {
                    try { from.data.RewardPropMoney(actual); } catch { }
                    throw e2;
                }
            }
            catch (Exception e) { err = "灵石转账失败: " + e.Message; return 0; }
            // 11-20：灵石转移是"钱袋"这条链上唯一会真的动数值的地方 —— 打一行，
            // 出事时不必再靠余额差反推（旧实现在成功路径上完全静默）。
            try
            {
                ModMain.P("[Props] 灵石转移 " + (actual < count ? "★部分★" : "")
                          + actual + "/" + count + "（发起方持有 " + haveMoney + "）");
            }
            catch { }
            return actual;
        }

        /// <summary>
        /// 从发起方 NPC 背包按中文名拆出 count 个道具，收集到 giveList。返回实际取到数量；error 为失败原因（成功时 null）。
        ///
        /// ⚠ 旧实现的坑（勿回退）：曾直接写 `p.propsCount = take`。那不是"取出 N 个"，
        /// 而是"把整栈数量改成 N"——剩余部分既没有新栈承载、也无法在赠送被拒/校验取消时找回（真丢道具）。
        /// 官方正解是 `DataProps.DelProps(soleID, delCount)`：由引擎拆栈，返回被取出的**独立** PropsData 实例，
        /// 原栈自动保留剩余（have - take）。
        ///
        /// 流程固定两段：先规划（只读 + PropsItemCanGive 校验），全部通过后才落刀拆栈；
        /// 拆栈中途失败则把已取出的全部 AddProps 回滚，保证任何情况下不吞道具。
        /// </summary>
        internal static int TakePropsFromBag(WorldUnitBase wub, WorldUnitBase player, string itemName, int count,
                                           Il2CppSystem.Collections.Generic.List<DataProps.PropsData> giveList,
                                           out string error)
        {
            error = null;
            var dp = wub.data.unitData.propData as DataProps;
            if (dp == null) { error = "背包不可用"; return 0; }

            // —— 第一段：只读规划（绝不碰背包数据）——
            var plan = new List<KeyValuePair<string, int>>();   // soleID -> 该栈取出数量
            int planned = 0;
            try
            {
                object list = null;
                try { list = ((dynamic)dp).allProps; } catch { }
                if (list == null) try { list = ((dynamic)dp).CloneAllProps(); } catch { }
                var dyn = (dynamic)list;
                int n = dyn == null ? 0 : (int)dyn.Count;
                for (int i = 0; i < n && planned < count; i++)
                {
                    var p = (dynamic)dyn[i];
                    if (p == null) continue;
                    string raw = "";
                    try { raw = p.propsItem.name; } catch { }
                    string ls = raw;
                    try { ls = GameTool.LS(raw); } catch { ls = raw; }
                    if (raw != itemName && ls != itemName) continue;

                    string soleID = "";
                    try { soleID = (string)p.soleID; } catch { }
                    if (string.IsNullOrEmpty(soleID)) continue;   // 无 soleID 无法安全拆栈，跳过而非改字段
                    int have = 1;
                    try { have = (int)p.propsCount; } catch { }
                    if (have < 1) have = 1;
                    int take = Math.Min(count - planned, have);
                    if (take <= 0) continue;

                    // 可赠送校验前置到"落刀前"：整单任一不可赠送就取消，避免拆出来了又退不干净
                    try
                    {
                        if (!UnitActionRoleGive.PropsItemCanGive(wub, player, (DataProps.PropsData)p))
                        { error = "道具不可赠送：" + itemName; return 0; }
                    }
                    catch { }

                    plan.Add(new KeyValuePair<string, int>(soleID, take));
                    planned += take;
                }
            }
            catch (Exception e) { error = "背包读取失败: " + e.Message; return 0; }

            if (plan.Count == 0) { error = "背包中没有，可先 inspect_unit(classes=inventory)"; return 0; }

            // —— 第二段：落刀拆栈（任一栈失败即全量回滚）——
            int actual = 0;
            try
            {
                foreach (var kv in plan)
                {
                    var taken = dp.DelProps(kv.Key, kv.Value);
                    if (taken == null) throw new Exception("拆栈失败 soleID=" + kv.Key);
                    giveList.Add(taken);
                    actual += kv.Value;
                }
            }
            catch (Exception e)
            {
                RollbackGive(wub, giveList);
                error = "取出道具失败，已回滚: " + e.Message;
                return 0;
            }
            // 11-20：拆栈是"道具离手"的唯一时刻，成功路径原先一句话都没有 ——
            //   之后不论哪条失败路吞了道具，日志里都看不出"它到底出来没有"。这一行补上。
            try { ModMain.P("[Props] 拆栈 " + itemName + " " + actual + "/" + count + "（" + plan.Count + " 栈）"); }
            catch { }
            return actual;
        }

        /// <summary>把已拆出但最终没送成的道具原样加回背包（赠送取消/异常时的兜底，防吞道具）。</summary>
        internal static void RollbackGive(WorldUnitBase wub, Il2CppSystem.Collections.Generic.List<DataProps.PropsData> giveList)
        {
            try
            {
                var dp = wub.data.unitData.propData as DataProps;
                if (dp == null) return;
                int n = giveList.Count;
                for (int i = 0; i < n; i++)
                {
                    try { dp.AddProps(giveList[i]); } catch { }
                }
                giveList.Clear();
                // 11-20：回滚是"出错善后"，最该留痕的一步 —— 否则"道具到底退回去没有"
                // 只能靠玩家数背包。条数取 Clear 之前的快照。
                if (n > 0)
                {
                    try { ModMain.P("[Props] 回滚 " + n + " 件道具加回背包（防吞道具）"); }
                    catch { }
                }
            }
            catch { }
        }
    }
}
