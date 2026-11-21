/// <summary>
/// TriggerSelfCheck —— **自己调用引擎的判定函数**，回答"我们那条 DungeonTrigger 到底会不会执行"。
///
/// ## 为什么是这个方案（10-18 的两次教训）
/// 前一版 `DungeonTriggerWatch` 用 Harmony 给 `ConfDungeonTrigger.GetItem` / `IsTrigger` 挂补丁，
/// **直接导致游戏启动闪退**：`Player.log` 停在自己那些补丁之后，一行输出都没来得及写。
/// 原因是我把"全程序集反射扫描 + 在配置加载高频路径上挂 postfix + 每次调用写文件"三件事
/// 一次全做了。
///
/// 本模块换一条本质不同的路：
/// **不挂任何补丁。** 这两个方法在元数据里都是 `Public`
/// （`NativeMethodInfoPtr_GetItem_Public_List_1_ConfDungeonTriggerItem_ConfDungeonBaseItem_0`
/// / `NativeMethodInfoPtr_IsTrigger_Private_Static_Boolean_ConfDungeonTriggerItem_0`），
/// 所以我们可以**像游戏自己那样直接调用它**，拿到的就是权威答案：
///   · 引擎认为"这个副本应该跑哪些触发器" → `GetItem(dungeonBaseItem)`
///   · 引擎认为"这一条过不过" → `IsTrigger(item)`
///
/// 好处：
///   ① 绝不改游戏行为，也**不进入任何游戏调用路径**——只在探针自己的心跳里跑；
///   ② 不挂补丁 ⇒ 不可能影响启动/配置加载（上次闪退的根因被彻底移除）；
///   ③ 依旧能给出确定性结论，而不是靠猜。
///
/// 风险仍然兜住：整体 try/catch、**每局只跑一次**、结果只落盘。
/// 万一 `IsTrigger` 反射调用失败（私有方法/IL2CPP 限制），会**明确写出"判定不可用"**，
/// 而绝不把"没测到"写成"没执行"。
/// </summary>
using System;
using System.Collections.Generic;
using System.Reflection;

namespace AgentLoopBridge
{
    internal static class TriggerSelfCheck
    {
        private static bool _done;

        /// <summary>每局只跑一次。由 `TeamMultiProbe` 在"已确认进副本"时调用。</summary>
        internal static void RunOnce(object dungeonBaseItem, Action<string> outFn)
        {
            if (_done) return;
            if (dungeonBaseItem == null) return;
            _done = true;

            try
            {
                outFn("========== 触发器自检（自己调引擎函数，不挂补丁）==========");
                DumpOurRows(outFn);
                CheckCandidates(dungeonBaseItem, outFn);
                outFn("==========================================================");
            }
            catch (Exception e)
            {
                outFn("触发器自检异常（本轮无结论）: " + e.Message);
            }
        }

        private static void Reset() { _done = false; }
        /// <summary>读一条 conf 行上的字符串字段（属性优先、字段兜底，含基类链）。</summary>
        private static string GetStr(object o, string name)
        {
            if (o == null) return "?";
            try
            {
                for (var t = o.GetType(); t != null; t = t.BaseType)
                {
                    const BindingFlags BF = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance;
                    var pi = t.GetProperty(name, BF);
                    if (pi != null && pi.CanRead)
                    {
                        object v = pi.GetValue(o, null);
                        return v == null ? "null" : v.ToString();
                    }
                    var fi = t.GetField(name, BF);
                    if (fi != null)
                    {
                        object v = fi.GetValue(o);
                        return v == null ? "null" : v.ToString();
                    }
                }
            }
            catch { }
            return "?";
        }

        private static int GetInt(object o, string name)
        {
            string s = GetStr(o, name);
            int v;
            return int.TryParse(s, out v) ? v : int.MinValue;
        }

        /// <summary>把我们自己那几条触发器在**运行时表**里的真实内容打出来。</summary>
        private static void DumpOurRows(Action<string> outFn)
        {
            int[] offs = { 1, 2, 3 };
            string[] names = { "拉队友", "挂重伤", "结束清理" };
            for (int k = 0; k < offs.Length; k++)
            {
                int id = ModIds.Mod + offs[k];
                object it = null;
                try { it = g.conf.dungeonTrigger.GetItem(id); } catch { }
                if (it == null) { outFn("  [我们] " + names[k] + " id=" + id + " ⇒ ❌ 运行时表里取不到"); continue; }
                outFn("  [我们] " + names[k] + " id=" + id + " ⇒ ✅ 有"
                      + "  tt=" + GetStr(it, "triggerType")
                      + " tv=" + GetStr(it, "triggerValue")
                      + " dType=" + GetStr(it, "dungeonType")
                      + " dID=" + GetStr(it, "dungeonID")
                      + " setNPCA=" + GetStr(it, "setNPCA")
                      + " prob=" + GetStr(it, "probability")
                      + " wt=" + GetStr(it, "weight"));
                outFn("        bf=" + GetStr(it, "battleFunction"));
            }
        }

        /// <summary>
        /// 调引擎的 `GetItem(dungeonBaseItem)` 拿候选清单，再看我们在不在、`IsTrigger` 过不过。
        /// 这是"触发器会不会执行"的**唯一权威答案**。
        /// </summary>
        private static void CheckCandidates(object dungeonBaseItem, Action<string> outFn)
        {
            // ① 候选清单（只走**反射**，不在编译期强转 —— 强转会引入类型初始化风险）
            object listObj = null;
            string viaErr = null;
            try
            {
                var conf = g.conf.dungeonTrigger;
                var mi = conf.GetType().GetMethod(
                    "GetItem", BindingFlags.Public | BindingFlags.Instance,
                    null, new[] { dungeonBaseItem.GetType() }, null);
                if (mi == null)
                {
                    // 参数类型可能被包装过，退一步：全表找同名且只收 1 个参数的 GetItem
                    foreach (var m in conf.GetType().GetMethods(
                                 BindingFlags.Public | BindingFlags.Instance))
                    {
                        if (m.Name != "GetItem") continue;
                        var ps = m.GetParameters();
                        if (ps.Length == 1 && ps[0].ParameterType.IsInstanceOfType(dungeonBaseItem))
                        { mi = m; break; }
                    }
                }
                if (mi != null) listObj = mi.Invoke(conf, new[] { dungeonBaseItem });
                else viaErr = "找不到接受 " + dungeonBaseItem.GetType().Name + " 的 GetItem";
            }
            catch (Exception e) { viaErr = e.GetType().Name + ": " + e.Message; }

            if (listObj == null)
            {
                outFn("  [候选清单] 拿不到（" + (viaErr ?? "返回 null") + "）");
                outFn("  ⚠ 这只是**本模块没拿到**，不代表引擎没有 —— 不要读成「触发器没跑」。");
                return;
            }

            // ② 读清单（Count + 索引器；Il2Cpp 枚举器不可靠）
            var rows = new List<object>();
            try
            {
                var t = listObj.GetType();
                const BindingFlags BF = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance;
                var piCount = t.GetProperty("Count", BF);
                var miItem = t.GetMethod("get_Item", BF);
                if (piCount == null || miItem == null)
                {
                    outFn("  [候选清单] 容器没有 Count/get_Item，无法读取（不当作空）");
                    return;
                }
                object cv = piCount.GetValue(listObj, null);
                int n = cv is int i0 ? i0 : (cv == null ? -1 : Convert.ToInt32(cv));
                for (int k = 0; k < n; k++)
                {
                    try { rows.Add(miItem.Invoke(listObj, new object[] { k })); } catch { }
                }
                outFn("  [候选清单] 引擎给的候选共 " + n + " 条：" + DescribeIds(rows));
            }
            catch (Exception e)
            {
                outFn("  [候选清单] 读取异常：" + e.Message);
                return;
            }

            // ③ 我们在不在清单里 + IsTrigger 判定
            int[] offs = { 1, 2, 3 };
            string[] names = { "拉队友", "挂重伤", "结束清理" };
            for (int k = 0; k < offs.Length; k++)
            {
                int id = ModIds.Mod + offs[k];
                object hit = null;
                foreach (var r in rows) { if (GetInt(r, "id") == id) { hit = r; break; } }

                if (hit == null)
                {
                    outFn("  ⇒ " + names[k] + " id=" + id + " **不在候选清单** ⇒"
                          + " ① 被 dungeonType/dungeonID 过滤掉了（条件都没轮到判）");
                    continue;
                }

                string verdict = Judge(hit);
                outFn("  ⇒ " + names[k] + " id=" + id + " **在候选清单** ✓；IsTrigger=" + verdict);
                if (verdict == "TRUE")
                    outFn("     ★ 引擎判定它会执行 ⇒ 问题在 setNPCA / addBattleUnit 生成环节 ★");
                else if (verdict == "FALSE")
                    outFn("     ⚠ 引擎判定它不执行 ⇒ 问题在触发条件/时机（tt/tv/condition）");
            }
        }

        /// <summary>调 `IsTrigger(item)`。拿不到就**明确说拿不到**，绝不默认成 false。</summary>
        private static string Judge(object item)
        {
            try
            {
                var t = g.conf.dungeonTrigger.GetType();
                var mi = t.GetMethod("IsTrigger",
                    BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static,
                    null, new[] { item.GetType() }, null);
                if (mi == null) return "?(找不到 IsTrigger(" + item.GetType().Name + ")，不可用)";
                object r = mi.Invoke(null, new[] { item });
                return (r is bool b) ? (b ? "TRUE" : "FALSE") : "?(" + r + ")";
            }
            catch (Exception e)
            {
                return "?(调用失败：" + e.GetType().Name + ")";
            }
        }

        private static string DescribeIds(List<object> rows)
        {
            var parts = new List<string>();
            int shown = 0;
            foreach (var r in rows)
            {
                if (shown++ >= 20) { parts.Add("…"); break; }
                int id = GetInt(r, "id");
                parts.Add(id == int.MinValue ? "?" : id.ToString());
            }
            return "[" + string.Join(",", parts.ToArray()) + "]";
        }
    }
}
