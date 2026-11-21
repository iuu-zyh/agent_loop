/// <summary>
/// 工具执行器 —— request: call_tool 的**分发**（11-19 由 6711 行拆出，11-20 按 Python 的域归并）。
///
/// 本文件只剩三件事：两个 `Execute` 重载（直发 / 带 npc_id 的排队闸）、`IsOk`。
/// 具体工具在哪个文件，按 `name` 一眼可查：
///
///   inspect_unit      → `tools/QueryTool.cs`       （含 `PageLogs` 分页）
///   search_units      → `tools/QueryTool.cs`
///   query_world       → `tools/QueryTool.cs`    （places/regions/events/rankings）
///   query_world.sects → `tools/SectQueryTool.cs`     （宗门人事/名册/人物树）
///   宗门入/退         → `tools/ActionTool.cs`
///   social_relation   → `tools/RelationTool.cs`
///   movement          → `tools/MovementTool.cs`
///   world_ai_action   → `tools/ActionTool.cs`
///   economy_item      → `tools/EconomyTool.cs`
///   trade             → `tools/EconomyTool.cs`
///   item_acquire      → `tools/AcquireTool.cs`
///   team              → `tools/TeamTool.cs`      （入口 + 领域都在这里）
///
/// 共用的判据各有唯一一份，别再往工具类里抄：
///   `ToolArgs`（发起方/对手方解析、JSON 取值）、本文件的 `IsActionTool`/`DescribeAction`
///   （"是不是动作工具"+一句人话）、`IntimService`（好感读写与步长）、
///   `PropsService`（灵石/背包转移）、`NativeActionService`（原生动作下发 + 拒绝判据）、
///   `QueryTool` 里的文案（本地化串/地名/气运名）、`ActionTool` 里的功法解析。
///
/// 【分发形状不能改】`switch` 与全部执行链曾经都在这里，Python 契约测试
///   `test_dispatch_matches_tool_order` 锁的就是这个 `case` 顺序与集合。
/// </summary>
using System;
using System.Collections.Generic;
using System.Text;
using Newtonsoft.Json.Linq;
using static AgentLoopBridge.ToolResult;

namespace AgentLoopBridge
{
    public class ToolExecutor
    {
        // ══════════════════════════════════════════════════════════════
        // ★11-20：分层生效的**现场证据**（用户要求"加了日志，方便判断改动是否生效"）★
        //
        // 拆成 13 个类之后，"新 DLL 到底进没进游戏"不能靠猜：
        //   · `[ToolLayer]` 一行在 `ModMain.Init` 打，用**反射查类型**证明 13 个类都在
        //     加载进来的程序集里 —— 少一个就说明跑的还是旧 DLL（或编译没带上）；
        //   · `[Tool]` 每次工具调用打一行，带**落到哪个文件**。看到 `→ QueryTool.cs`
        //     就知道 `inspect_unit` 真的走的是新分层，而不是老的单文件实现。
        //
        // 这两行都刻意做成"不看代码也能判"的形态：文件名 + 成败 + 耗时 + 失败原文。
        // 以前这类"改了没生效"的排查全靠比对 DLL 的 mtime 与进程启动时刻（见 ModMain 的
        // `[Build] 磁盘上的程序集`），那是**间接**证据；这两行是**直接**证据。
        // ══════════════════════════════════════════════════════════════

        /// <summary>工具名 → 承载它的文件（**与文件头的表同源**，两边必须一起改）。
        /// 用途：`[Tool]` 日志里那一段 `→ XxxTool.cs`，让人一眼看出落到哪个类。</summary>
        private static readonly Dictionary<string, string> _ToolHome = new Dictionary<string, string>
        {
            { "inspect_unit", "QueryTool.cs" },
            { "search_units", "QueryTool.cs" },
            { "query_world", "QueryTool.cs" },          // topic=sects 时转 SectQueryTool
            { "social_relation", "RelationTool.cs" },
            { "movement", "MovementTool.cs" },
            { "world_ai_action", "ActionTool.cs" },
            { "economy_item", "EconomyTool.cs" },
            { "trade", "EconomyTool.cs" },
            { "item_acquire", "AcquireTool.cs" },
            { "team", "TeamTool.cs" },
        };

        /// <summary>分层自检：13 个类**是不是真的在这个程序集里**。
        ///
        /// 为什么用反射而不是直接引用：直接引用会被编译器优化成"反正都能编过"，
        /// 而这里要回答的是"运行时加载进来的那份 DLL 里有没有它"。名字拼错/类被删/
        /// 部署了旧 DLL —— 三种情况的共同现象都是 `GetType` 返回 null，一行就能看见。</summary>
        internal static string DescribeLayout()
        {
            // 顺序 = 文件头那张表（分发器在前，共用件在后）
            string[] names =
            {
                "ToolExecutor", "ToolArgs", "QueryTool", "SectQueryTool", "RelationTool",
                "MovementTool", "ActionTool", "EconomyTool", "AcquireTool", "TeamTool",
                "IntimService", "PropsService", "NativeActionService",
            };
            var asm = typeof(ToolExecutor).Assembly;
            var ok = new List<string>();
            var miss = new List<string>();
            foreach (var n in names)
            {
                Type t = null;
                try { t = asm.GetType("AgentLoopBridge." + n, false); } catch { }
                (t != null ? ok : miss).Add(n);
            }

            var sb = new StringBuilder();
            sb.Append("工具层 13 类（").Append(ok.Count).Append(" 在册");
            if (miss.Count > 0) sb.Append("，缺 ").Append(miss.Count).Append(" 个 ★旧 DLL 或编译没带上★");
            sb.Append("）：").Append(string.Join(" ", ok.ToArray()));
            if (miss.Count > 0) sb.Append("｜缺失：").Append(string.Join(" ", miss.ToArray()));
            sb.Append("｜分发 ").Append(_ToolHome.Count).Append(" 个工具名");
            return sb.ToString();
        }

        /// <summary>一次工具调用的落点行（成败/耗时/落到哪个文件）。失败原文截断，
        /// 免得一条长报错把日志撑成十行。
        ///
        /// 第三种状态 `pending`：模态确认窗**已弹出、等玩家点选**（`{__pending__,__slot__}` 帧）。
        /// 它既不是"办完了"也不是"失败了" —— 混进 `ok` 会让"玩家点了取消"那类问题在日志里
        /// 看不出是主动拒绝还是工具自己报错。</summary>
        private static void LogDispatch(string name, JObject res, string thrown, long ms)
        {
            try
            {
                string home;
                if (!_ToolHome.TryGetValue(name, out home)) home = "(未登记)";

                bool pending = false;
                try { pending = res != null && res["__pending__"] != null && res["__pending__"].Value<bool>(); }
                catch { }

                bool ok = thrown == null && IsOk(res);
                string why = thrown;
                if (!ok && string.IsNullOrEmpty(why) && res != null)
                {
                    try { why = (string)res["error"]; } catch { }
                }
                if (why != null && why.Length > 120) why = why.Substring(0, 120) + "…";

                string verdict = pending ? "pending（等玩家点选）" : (ok ? "ok" : "FAIL");
                ModMain.P("[Tool] " + name + " " + ms + "ms " + verdict
                          + (ok || pending ? "" : "「" + (why ?? "") + "」") + " → " + home);
            }
            catch { }
        }

        /// <summary>主线程执行工具，返回 {"success":bool, "data":..., "error":...}
        /// （执行期间置 SuppressAiOption：工具自己发起的原生剧情窗不注入「AI 对话」——
        /// 那些剧情是模型刚用工具发起的，回喂会语义错位，见 README.md 附录二 G（功能设计底稿））
        ///
        /// ★ 本方法 = **分发 + 直发**（`switch` 与全部执行链都在这里；契约测试
        ///   `test_dispatch_matches_tool_order` 锁的就是这个形状）。不带 npc_id 的调用
        ///   （外部自测工程 / 排队器回放）一律走这条路，行为与本改造前**逐字一致**；
        ///   带 npc_id 的真机入口是下面那个重载（动作工具"此刻发不出去"时排队）。</summary>
        public JObject Execute(string name, JObject args)
        {
            DramaGate.SuppressAiOption = true;
            // ★11-20 落点计时★：`[Tool]` 那行要回答"这次调用落到哪个文件、花了多久、成没成"。
            //   用 Stopwatch 而不是 DateTime：主线程上被调到几百次，取时间戳的代价要可忽略。
            var sw = System.Diagnostics.Stopwatch.StartNew();
            JObject res = null;
            string thrown = null;
            try
            {
                switch (name)
                {
                    case "inspect_unit":      res = QueryTool.InspectUnit(args); break;
                    case "search_units":      res = QueryTool.SearchUnits(args); break;
                    case "query_world":       res = QueryTool.QueryWorld(args); break;
                    case "social_relation":   res = RelationTool.SocialRelation(args); break;
                    case "movement":          res = MovementTool.Movement(args); break;
                    case "world_ai_action":   res = ActionTool.WorldAiAction(args); break;
                    case "economy_item":      res = EconomyTool.EconomyItem(args); break;
                    case "trade":             res = EconomyTool.Trade(args); break;
                    case "item_acquire":      res = AcquireTool.ItemAcquire(args); break;
                    case "team":              res = TeamTool.Team(args); break;
                    default:                  res = Fail("unknown tool: " + name); break;
                }
                // ★ 查询类结果的**顺带登记**（09-17）：结果里带着 `{name, unit_id}`（从对象上读的
                //   真身身份），记进全局表后，后续的按名解析（自身段/立绘/工具兜底）就能直接命中。
                //   只对**只读查询**做：动作类的结果里没有"人的引用"，而且动作工具可能开确认窗，
                //   不该在它返回时顺手动登记表。规则（不覆盖 / 同名不登记）见被调方。
                if ((name == "inspect_unit" || name == "search_units")
                    && res != null && res["data"] != null && IsOk(res))
                {
                    try { UnitIdRegistry.RememberObservedFromPayload(res["data"], name); }
                    catch (Exception re) { ModMain.P("[UnitIdRegistry] 查询顺带登记失败: " + re.Message); }
                }
                return res;
            }
            catch (Exception e)
            {
                thrown = e.Message;
                res = Fail(e.Message);
                return res;
            }
            finally
            {
                DramaGate.SuppressAiOption = false;
                // 落点行放在 finally：正常返回、异常返回、`__pending__` 模态挂起（也是 return res）
                // 三条路都会走到，不会漏记。
                LogDispatch(name, res, thrown, sw.ElapsedMilliseconds);
            }
        }

        /// <summary>
        /// 带 npc_id 的执行入口（`WsClient` 传当前对话 NPC）：**动作工具在"此刻发不出去"时排队**
        /// （09-24 用户拍板 B 方案，接线在 `DramaGate.DeferredActionQueue`）。
        ///
        /// 判据只写一处：`DeferredActionQueue.ShouldQueue()`（战斗中 / 剧情链收尾 / 已有排队）。
        /// 四条路：
        ///   · 入队成功 → 立即回 `{queued:true, summary, note}`（模型看到"已受理、别重复调"），
        ///     动作在战斗链干净后由队列自动执行，办完发 `npc_initiative(intent=action_done)`；
        ///   · 队列满 → 如实拒绝（**不静默丢**）；
        ///   · 只读三件（inspect/search/query）与未知工具名 → 直通（查询本来就该随时能做）；
        ///   · 未接线（verify / `WaitBlocker == null`）→ `ShouldQueue()` 恒 false → 直通。
        ///
        /// 为什么捕获 `args` 的**冻结副本**：闭包活到几十秒后（跨战斗），期间谁都不该改这份参数；
        /// 而"目标重解析"发生在执行那一刻（闭包调 2 参 `Execute` 现查单位），不持有旧包装对象。
        /// 回放走 2 参重载 = 天然绕开本闸（不会自我重复入队）。
        /// </summary>
        public JObject Execute(string name, JObject args, string npcId)
        {
            // ★ 10-25「战前 / 战中」闸（用户拍板；判据与理由在 `drama/BattleFlowCore.cs`）★
            //
            // 与下面那道排队闸的分工：**先判这一道**，因为"当场拒绝"比"排队"更硬 ——
            // 攻击/切磋被排进队列，等战斗结束后会**再打一次**。真机 10-25 18:51 的现场：
            //   18:51:22.014 NPC 调 attack → 入队（"当前发不出去，等战斗链收场后自动执行"）
            //   18:51:28.123 玩家自己点了那一页上的「战斗」
            //   18:51:28.687 排队那条**落刀**，又开了一次同一页 ⇒ 两场架叠在一起
            //   之后处置页上点「对话」→ 段里只剩「不投」的行 → TakeDelta 返回 0 → 静默无反应
            // 所以这四样必须**当场作废**，不许等会儿补做（补做 = 把已经发生的事再办一遍）。
            //
            // 粒度（用户明确要求，不是一刀切禁全部工具）：
            //   · 拒绝：world_ai_action 的 attack/spar、movement 的 teleport/summon
            //   · 排队：赠予/讨要/传功/邀约/论道/双修/行走（与战斗不冲突，只是弹窗会沉底）
            //   · 照常：只读三件（根本不进这个分支）
            //
            // 为什么闸开在**这里**而不是对话轮次里：轮次闸（`speech_only_turn`）只覆盖
            // "玩家点剧情页对话"那条路 —— `busy` 帧只在那条路上带。玩家**直接在聊天框打字**
            // 说话时没有任何标记，战斗中他要 NPC 打人，旧实现会排队、打完真去打一次。
            // 这里读的是**当下这一刻**，不管这一轮是怎么起来的，一视同仁。
            bool isAction = IsActionTool(name);
            if (isAction)
            {
                string op0 = OpOf(args);
                if (BattleFlowGate.VerdictOf(name, op0) == ToolVerdict.Reject)
                {
                    string txt = BattleFlowGate.RejectTextOf(name, op0);
                    ModMain.P("[ToolExecutor] 战前/战中拒绝：" + DescribeAction(name, args)
                              + "（" + BattleFlowGate.Describe() + "）");
                    return Fail(txt);
                }
            }
            if (isAction && !string.IsNullOrEmpty(npcId) && DeferredActionQueue.ShouldQueue())
            {
                string summary = DescribeAction(name, args);
                var frozen = args == null ? new JObject() : (JObject)args.DeepClone();
                if (DeferredActionQueue.TryEnqueue(npcId, name, summary, () =>
                {
                    // ★ 执行那一刻**复查**：又忙起来了 → 返回 null（队列退回等待，用户拍板：不判失败）；
                    //   否则走与直发完全相同的执行链（二阶段校验/日记账/pending 标记一字不差）。
                    if (DeferredActionQueue.BlockerNow().Length > 0) return null;
                    // ★ 10-25：战前/战中轮到它时**作废**，不许"等战斗结束后补做"★
                    //   排队语义是"等会儿办"，而攻击/切磋/传送/召唤的语义是"把玩家拖去打一场"——
                    //   仗都已经打了，补一刀就是把已经发生的事再办一遍（真机 18:51:28.687 那条落刀）。
                    //   返回 Fail 而不是 null：null 会退回等待、下轮再来，那是"延后执行"。
                    string fop = OpOf(frozen);
                    if (BattleFlowGate.VerdictOf(name, fop) == ToolVerdict.Reject)
                    {
                        string why = BattleFlowGate.RejectTextOf(name, fop);
                        ModMain.P("[DeferQueue] 战前/战中作废（不补做）：" + summary + "（" + BattleFlowGate.Describe() + "）");
                        return Fail(why);
                    }
                    return Execute(name, frozen);
                }))
                {
                    ModMain.P("[ToolExecutor] 此刻发不出去 → 已排队：" + summary);
                    return Ok(new JObject
                    {
                        ["queued"] = true,
                        ["summary"] = summary,
                        // note 是**给模型的**（Python 渲染层会包成一句系统说明，见 text_render.render）：
                        // 说不清楚就会被"再调一次试试"，而那正是要避免的（会排第二个、甚至撞窗）。
                        ["note"] = "此刻发不出去（战斗中/剧情收尾中）——这个动作已受理，"
                                 + "此间事了会自动办理；不要重复调用它；办完后你会收到系统通知。",
                    });
                }
                return Fail("已经有一批动作在排队（" + DeferredActionQueue.Describe() + "），这次先不受理，稍后再说");
            }
            return Execute(name, args);
        }

        /// <summary>结果是否成功（`{"success":bool,...}` 的既有形状；缺键当成功 —— 老形状兼容）。</summary>
        private static bool IsOk(JObject res)
        {
            try
            {
                var t = res["success"];
                return t == null || t.Type != JTokenType.Boolean || (bool)t;
            }
            catch { return true; }
        }

        // ---------- 「此刻发不出去」的动作排队（09-24 B 方案；判据/状态机在 DeferredActionCore） ----------

        /// <summary>六个动作工具（会改状态或弹原生窗）：此刻发不出去时**排队**，而不是硬发
        /// （原生窗会沉进游戏系统队列不出队 → 90 秒静默超时）或失败（用户否掉了"让模型记住稍后重试"）。
        /// 名单与 Python `tools/schemas.ACTION_TOOLS` 同集合；这里写成白名单是**故意**的：
        /// 将来新增工具默认走直通（旧行为），不会被静默塞进排队。</summary>
        private static bool IsActionTool(string name)
        {
            return name == "social_relation" || name == "movement" || name == "world_ai_action"
                || name == "economy_item" || name == "trade" || name == "item_acquire";
        }

        /// <summary>读 `op` 参数（缺项 / null / 非字符串一律给空串）。
        ///
        /// 为什么不用 `(string)args["op"]`（工具层其余地方的老写法）：那道「战前/战中」闸
        /// 跑在 2 参重载的 `try/catch` **之外**（它在 3 参重载里、分发之前），
        /// 而 Newtonsoft 把整数/布尔 token 显式转 string 会抛 —— 一条本该"照常执行"的调用
        /// 会变成"工具执行异常（C# 侧）"。这里只求不新增一个抛出点：读不到就当没给 op。
        /// （`op` 在 `tools/schemas.py` 里是 `type: string` + `enum`，正常恒为字符串。）</summary>
        private static string OpOf(JObject args)
        {
            try
            {
                if (args == null) return "";
                var t = args["op"];
                if (t == null) return "";
                if (t.Type == Newtonsoft.Json.Linq.JTokenType.Null) return "";
                return t.ToString();
            }
            catch { return ""; }
        }

        /// <summary>动作的一句话说明：玩家提示（`UITipItem`）/ `[DeferQueue]` 日志 / 办完后的系统通知
        /// 都用它。**必须是"人话"** —— 它会经 `action_done` 通知**进模型上下文**（Python 侧包装见
        /// `initiative.format_action_done_message`）：模型看到"邀约姜萌：已完成"才知道是哪件事办成了。
        /// 动词表刻意写细：只给工具名（"执行 world_ai_action"）等于没说清是哪件事。</summary>
        private static string DescribeAction(string name, JObject args)
        {
            if (args == null) args = new JObject();
            string op = (string)args["op"] ?? "";
            string target = (string)args["target"] ?? "";
            string who = string.IsNullOrEmpty(target) ? "玩家" : target;

            // 动词表（键 = "工具:op"，值里 {0} = 对象）。**故意不用 switch/case**：
            // 工具层 `ActionTool` 里的 `case "spar":` 这类分支是**执行**语义，被 Python 侧的源码契约测试
            // （test_spar_choice_contract / test_relation_diag_contract）当锚点抓取；
            // 说明文案里再写一份同名 case，会让那些测试抓到错的段落（真被红过一次）。
            string verb;
            if (_ActionVerb.TryGetValue(name + ":" + op, out verb)) return string.Format(verb, who);

            if (name == "economy_item") return "向" + who + "赠送物品";
            if (name == "item_acquire")
            {
                if (op == "steal_item") return "偷窃" + who + "的物品";
                if (op == "ask_for") return "向" + who + "讨要物品";
                return "向" + who + "取得物品";
            }
            if (name == "trade")
            {
                string seller = (string)args["seller"] ?? "";
                string buyer = (string)args["buyer"] ?? "";
                string item = (string)args["item"] ?? "";
                return "买卖（" + seller + " → " + buyer + (item.Length > 0 ? "：" + item : "") + "）";
            }
            if (name == "movement" && op == "travel")
            {
                string dest = (string)args["destination"] ?? "";
                return "前往" + (dest.Length > 0 ? dest : "别处");
            }
            if (name == "movement") return "位移（" + op + "）";
            if (name == "social_relation") return "与" + who + "处理关系（" + op + "）";
            return "对" + who + "发起动作（" + op + "）";
        }

        /// <summary>动词表（见 `DescribeAction`：**不许写成 switch/case** —— 会撞源码契约测试的锚点）。</summary>
        private static readonly Dictionary<string, string> _ActionVerb = new Dictionary<string, string>
        {
            // world_ai_action
            { "world_ai_action:spar", "与{0}切磋" },
            { "world_ai_action:attack", "攻击{0}" },
            { "world_ai_action:shuang_xiu", "与{0}双修" },
            { "world_ai_action:lun_dao", "与{0}论道" },
            { "world_ai_action:yao_yue", "邀约{0}" },
            { "world_ai_action:chuan_gong", "向{0}传功" },
            // movement
            { "movement:summon", "召唤{0}到身边" },
            { "movement:teleport", "传送到{0}身边" },
            // social_relation
            { "social_relation:add_intim", "与{0}拉近关系" },
            { "social_relation:reduce_intim", "与{0}疏远关系" },
            { "social_relation:jie_yuan", "与{0}结为道侣" },
            { "social_relation:jie_chu_jie_yuan", "解除与{0}的道侣关系" },
            { "social_relation:marry", "与{0}结为夫妻" },
            { "social_relation:divorce", "解除与{0}的夫妻关系" },
            { "social_relation:bai_shi", "向{0}拜师" },
            { "social_relation:jie_chu_bai_shi", "解除与{0}的师徒关系" },
            { "social_relation:shou_tu", "收{0}为徒" },
            { "social_relation:jie_chu_shou_tu", "解除与{0}的师徒关系" },
            { "social_relation:ren_yi_fu_mu", "认{0}为义父母" },
            { "social_relation:jie_yi", "与{0}结义" },
            { "social_relation:jie_chu_jie_yi", "解除与{0}的结义" },
        };
    }
}
