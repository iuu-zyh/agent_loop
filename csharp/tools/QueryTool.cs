/// <summary>
/// QueryTool —— **查询域**：`inspect_unit`（档案 + 经历分页）/ `search_units` / `query_world`（places/regions/events/rankings）+ 查询侧文案（本地化串/地名/气运名）。
/// （11-19 由 6711 行的 `ToolExecutor.cs` 拆出，11-20 按 Python 的域归并；成员逐字搬移。）
/// 跨类调用一律 `<类名>.<方法>`；`internal` = 仅本程序集可见。
/// 【Python 对应】查询域（`tools/schemas.py` 的「查询域」一段；`READONLY_TOOLS` 三件） —— 划分以 Python 侧为准，别在这里另立一套分类。
/// </summary>
using System;
using System.Collections.Generic;
using Newtonsoft.Json.Linq;
using static AgentLoopBridge.ToolResult;

namespace AgentLoopBridge
{
    internal static class QueryTool
    {
        // ---------- 查询类（只读） ----------

        internal static JObject InspectUnit(JObject args)
        {
            string target = (string)args["target"] ?? "";
            string unitId = (string)args["unit_id"] ?? "";
            if (string.IsNullOrEmpty(target) && string.IsNullOrEmpty(unitId))
                return Fail("请给 target（人物真名）。同名者请照抄结果里给的唯一名（形如 益婉容(化神殿·结晶后期)）");
            // 精确优先：有 unit_id 时**只认它**（UnitLookup 内部不回退按名 —— 回退就等于又猜一次）
            List<UnitLookup.Candidate> cands;
            var wub = UnitLookup.ResolveEx(target, string.IsNullOrEmpty(unitId) ? null : unitId, out cands);
            if (wub == null)
            {
                // —— 死者回退（10-15）：活单位容器对死者不可解析（探针实锤 GetUnit=null）——
                // 按名查死亡账本（unit_id 已随 args 注入时 O(1) 精准，没学过按名遍历兜底）。
                // 命中 = 结果带 dead=true + unit_id 无条件回传（_harvest_ids 学进映射表，之后全程精准）。
                object deadData = null, deadInfo = null; string deadId = "";
                bool isDead = UnitSnapshot.FindDead(
                    string.IsNullOrEmpty(unitId) ? "" : unitId,
                    string.IsNullOrEmpty(target) ? "" : target.Trim(),
                    out deadData, out deadInfo, out deadId);
                if (isDead)
                {
                    var deadClasses = args["classes"] as JArray;   // classes 在后面才定义，这里自行解析
                    if (deadClasses == null) deadClasses = new JArray("brief");
                    var dead = UnitSnapshot.BuildDead(deadData, deadInfo, deadId, deadClasses);
                    if (dead.Count > 0)
                    {
                        // 死者拿不到的块：按请求的 classes 给通用语句（渲染层拼「无从查考」）。
                        // DieData 无这些字段（UnitInfoData 快照 stub 无成员）——不是忘了采。
                        var unavail = new JArray();
                        foreach (var c in deadClasses)
                        {
                            string cs = (string)c;
                            if ((cs == "stats" || cs == "abilities" || cs == "inventory") && dead[cs] == null)
                                unavail.Add(cs);
                        }
                        if (unavail.Count > 0) dead["dead_unavailable"] = unavail;
                        // logs 同活人参数与产出形态（PageLogs 吃 {vital,regular} 出 {items,...}，
                        // 渲染层 _logs 只认后者——直接给桶形态会渲染成"经历：无记录"）
                        if (dead["logs"] != null)
                        {
                            string dfilter = (string)args["log_filter"] ?? "all";
                            int dpage = ToolArgs.SafeInt(args["log_page"], 1);
                            if (dpage < 1) dpage = 1;
                            int dsince = ToolArgs.SafeInt(args["log_since_month"], 0);
                            int duntil = ToolArgs.SafeInt(args["log_until_month"], 0);
                            dead["logs"] = PageLogs(dead, dfilter, dpage, dsince, duntil);
                        }
                        ModMain.P("[Inspect] ★死者档案★ " + (string)dead["name"] + " id=" + deadId);
                        return Ok(dead);
                    }
                }
                // ★ 报错文案里**不许出现 unit_id**（09-17）：`unit_id` 是 harness 注入的
                //   （`tools/unit_refs.PERSON_ARGS`），模型从来看不到它；而错误帧不过渲染层、
                //   原样进模型上下文（`dialogue_agent` 的 `render(...) or json.dumps(res)`）。
                //   原来那句把 id 打了出来，等于亲手教模型"有这么一串东西"，它会照念甚至编造。
                //   失效原因（换存档/人已离场）照旧如实说明，只是不给那串 id。
                return Fail(string.IsNullOrEmpty(unitId)
                    ? "未找到「" + target + "」（用 search_units 查一下真名，或确认他还在世上）"
                    : "未找到「" + target + "」的精确身份记录（他可能已离世/离场，或这局换过存档）。"
                      + "请用 search_units 重新查一次，再照抄结果里的名字");
            }
            ModMain.P("[Inspect] args=" + args.ToString(Newtonsoft.Json.Formatting.None));

            JArray classes = args["classes"] as JArray;
            if (classes == null)
                classes = new JArray("brief");

            bool Has(string c)
            {
                foreach (var x in classes)
                    if ((string)x == c) return true;
                return false;
            }

            // 分类背包裁剪参数：inventory_top=每类 top-N（默认3），inventory_all=true 全量不裁
            // 上限与 schema 的 maximum:50 对齐：原先只做裸转型、不钳制，
            // 传 99999 就等价于 inventory_all（不报错、静默全量）—— 与 top/count 都做了钳制的口径不一致。
            int invTop = 3;
            try { invTop = (int)args["inventory_top"]; } catch { }
            if (invTop > 50) invTop = 50;
            try { if ((bool)args["inventory_all"]) invTop = 0; } catch { }
            if (invTop == 0) invTop = -1;   // 0 会与默认冲突，<=0 语义为全量

            // 分类定向（11-10）：inventory_cat=只看点名的类别（该类全量不裁），inventory_find=按名模糊查。
            // 为什么加：原先只有"五类各 top-3"和"全量"两档，中间没有台阶 —— 玩家问「他有没有化瘀丹」时，
            // 化瘀丹单价低、按 worth 降序必然落在 top-3 之外，模型只剩 inventory_all 一条路（五类全出）。
            // 现在"点名一类"≈全量的 1/5，"按名查"≈几十字节，代价与问题规模终于对得上。
            // 解析口径：非字符串/空串一律当没传（模型常见 `"inventory_find": ""` 的空填）。
            var invCats = new List<string>();
            try
            {
                var catsArr = args["inventory_cat"] as JArray;
                if (catsArr != null)
                    foreach (var c in catsArr)
                    {
                        string cs = (c == null ? null : c.ToString()).Trim();
                        if (cs.Length > 0) invCats.Add(cs);
                    }
            }
            catch { }
            string invFind = null;
            try
            {
                var fv = args["inventory_find"];
                if (fv != null && fv.Type == JTokenType.String) invFind = fv.ToString().Trim();
            }
            catch { }
            if (string.IsNullOrEmpty(invFind)) invFind = null;

            // 与 L1 同源：UnitSnapshot 按 classes 按需采集（只采请求的块，不读多余的 logs 等重数据）
            var snap = UnitSnapshot.Build(wub, classes, invTop, invCats, invFind);

            JObject data = new JObject { ["name"] = (string)snap["name"] ?? target };
            // 被查者自己的 unit_id：**无条件回传**，模型据此把这个人钉死 ——
            // 同名时下一次查询可以直接用 unit_id，不必再按名猜。
            try { data["unit_id"] = wub.data.unitData.unitID; } catch { }
            // 同名歧义（重名事故）：按名解析命中多人时**如实上报候选**，绝不静默选一个。
            // 本次返回的是 candidates[0]（= 遍历序第一个），模型可据此改用 unit_id 精确指定。
            if (cands != null && cands.Count > 1)
            {
                var arr = new JArray();
                foreach (var c in cands)
                {
                    arr.Add(new JObject
                    {
                        ["unit_id"] = c.unitId,
                        ["name"] = c.name,
                        ["realm"] = c.realm,
                        ["sect"] = c.sect,
                        ["reputation"] = c.reputation,
                        ["point"] = new JObject { ["x"] = c.x, ["y"] = c.y },
                    });
                }
                data["ambiguous"] = new JObject
                {
                    ["target"] = target,
                    ["matched"] = cands.Count,
                    ["used"] = (string)data["unit_id"] ?? "",
                    ["candidates"] = arr,
                };
                ModMain.P("[Inspect] ★同名歧义★ '" + target + "' 全图 " + cands.Count
                          + " 人，本次用 " + (string)data["unit_id"]);
            }
            if (Has("brief"))
            {
                // brief = NPC 基本情况：名字/性别/境界/宗门/种族/魅力/兴趣/声望/身份/性格/气运
                //          + 关系·好感·同格（对手方是发起者，不是玩家 —— 见下面那段）
                data["sex"] = snap["sex"];
                data["realm"] = snap["realm"];
                // 境界原始数字（gradeID/curGrade/命中行）随 data 回传：真机日志能直接看出走了哪条路
                // （渲染层不认识这个键，不会进模型上下文；用途是"取值错没错"的现场证据）
                if (snap["realm_diag"] != null) data["realm_diag"] = snap["realm_diag"];
                data["sect"] = snap["sect"];        // [待真机]宗门名
                data["sect_id"] = snap["sect_id"];
                data["race"] = snap["race"];
                data["beauty"] = snap["beauty"];    // 原始数值（如需精确比较）；面板中文见 beauty_label
                data["hobby"] = snap["hobby"];      // [待真机]爱好
                data["reputation"] = snap["reputation"];
                data["title"] = snap["title"];      // [待真机]道号名
                data["personality"] = snap["personality"];  // 性格：{inner, outer[]}，与 NPC 面板一致（查表 LS 中文）
                // —— 关系三项（relation/intim/same_grid）是**相对量**，必须按发起者算 ——
                // `UnitSnapshot.Build` 把对手方**写死成玩家**（那是 L1 的口径：NPC 的档案只关心他与玩家这一对）。
                // 查询的发起者却是当前 NPC：沿用 Build 的值，在**查玩家**时就变成"玩家的关系簿查玩家自己"
                // → 恒 陌生 / 好感0 / 同处一地（09-21 真机：模型据此否掉了上下文里写着的道侣之谊，
                //   并顺手把"同处一地"当成事实，与玩家段的"异地相隔"当面打架）。
                // 所以这里用 `RelationTo(被查者, 发起者)` 重算，并把"对谁"随数据回传（`ref`）——
                // 渲染层不再自己写「玩家」二字。
                var actor = ToolArgs.ResolveActor(args);
                string actorName = actor == null ? "" : UnitIdentity.Name(actor, "");
                bool selfQuery = false;
                if (actor != null)
                {
                    try { selfQuery = actor.Pointer == wub.Pointer; } catch { }
                }
                if (actor != null && !selfQuery)
                {
                    var relSnap = UnitSnapshot.RelationTo(wub, actor);   // 被查者 → 发起者
                    data["relation"] = relSnap["relation"];
                    data["intim"] = relSnap["intim"];
                    data["same_grid"] = relSnap["same_grid"];
                    if (!string.IsNullOrEmpty(actorName)) data["ref"] = actorName;
                }
                else if (actor == null)
                {
                    // 发起者解析不到（老调用方/没有 initiator 的探针）→ 整段回落成改动前的形状，
                    // 不给 `ref`，渲染层按「玩家」措辞。
                    data["relation"] = snap["relation"];
                    data["intim"] = snap["intim"];
                    data["same_grid"] = snap["same_grid"];
                }
                // selfQuery（NPC 查自己）：三项都不给 —— 自己对自己的关系簿没有意义，宁缺不假。
                data["point"] = snap["point"];
                data["luck"] = snap["luck"];        // 气运（brief 块）：{born,added,all}，id+中文名+desc
                // 魅力/声望面板中文档位词（与游戏 NPC 面板同款查表：roleBeauty/roleReputation.GetItemInX）
                string bl = UnitSnapshot.BeautyLabel(wub);
                if (!string.IsNullOrEmpty(bl)) data["beauty_label"] = bl;
                string rl = UnitSnapshot.ReputationLabel(wub);
                if (!string.IsNullOrEmpty(rl)) data["reputation_label"] = rl;
            }
            if (Has("stats"))
                data["stats"] = snap["stats"];
            if (Has("abilities"))
                data["abilities"] = snap["abilities"];
            if (Has("inventory"))
                data["inventory"] = snap["inventory"];
            if (Has("relationships"))
                data["relationships"] = snap["relationships"];
            if (Has("logs"))
            {
                // 默认 all（2026-09-12 真机反馈：查玩家经历返回空——重要层 1 年初常为空，
                // 论道/双修/赠礼等日常互动都在常规层；查经历的自然语义=全量，要纯重要再显式传）
                string filter = (string)args["log_filter"] ?? "all";
                int page = ToolArgs.SafeInt(args["log_page"], 1);
                if (page < 1) page = 1;
                // 时间范围（可选，账面月：1年1月=1、1年12月=12、2年1月=13；范围过滤后再分页）
                int since = ToolArgs.SafeInt(args["log_since_month"], 0);
                int until = ToolArgs.SafeInt(args["log_until_month"], 0);
                data["logs"] = PageLogs(snap, filter, page, since, until);
                var _pl = data["logs"] as JObject;
                ModMain.P("[Inspect] PageLogs 输出=" + _pl.ToString(Newtonsoft.Json.Formatting.None));
            }
            return Ok(data);
        }

        /// <summary>经历日志分页：log_filter 选 重要(vital)/常规(regular)/all/与我相关(with_me)，log_page 每页 5 条；
        /// sinceMonth/untilMonth&gt;0 时按账面月闭区间过滤（过滤先于分页，total=过滤后条数）。
        /// with_me 是**条目级**筛选（跨两桶，只留 with_player=true 的条目），与按桶的 important/regular 不同层。</summary>
        internal static JObject PageLogs(JObject snap, string filter, int page, int sinceMonth = 0, int untilMonth = 0)
        {
            var logs = snap["logs"] as JObject;
            var outObj = new JObject { ["filter"] = filter, ["page"] = page };
            if (sinceMonth > 0) outObj["since_month"] = sinceMonth;
            if (untilMonth > 0) outObj["until_month"] = untilMonth;
            var items = new JArray();
            var raw = logs == null ? new JObject() : logs;

            JArray GetArr(string key)
            {
                var v = raw[key] as JArray;
                return v ?? new JArray();
            }

            bool filterHit(string f)
            {
                // f = 桶名（"vital"/"regular"），filter = 调用方传来的筛选值（"all"/"important"/"regular"/"with_me"）。
                // 修 原写 `if(f == "all")return true;` 用错了变量——"vital"/"regular" 永不等于 "all"，
                // 于是 filter=="all"（**默认值**）时两个桶全被排除 → items 恒空 → 渲染层判「经历：无记录」。
                // 真机证据：`[Logs] BuildLogs 命中主路 unitID=FJLlLl vital=1 regular=1` 后紧接
                //          `[Inspect] PageLogs 输出={"filter":"all","items":[],"total":0}`（数据在、被这里滤光）。
                // 只有 all 会踩中：important/regular 两分支本来就正确用 filter 比较，所以那两种筛选一直是对的。
                if (filter == "all") return true;
                if (filter == "important" && f == "vital") return true;
                if (filter == "regular" && f == "regular") return true;
                // with_me = 「与我相关」（游戏 NPC 面板·经历页同款页签，LocalText npcInfo_yuwoxiangguan）。
                // 它是**条目级**筛选，不看桶名 —— 两桶都放行，去留由下面的 PlayerHit 决定。
                if (filter == "with_me") return true;
                return false;
            }

            // with_me 的条目级判据：只认取数处（UnitSnapshot.LogsArr，在 CleanLogText 之前）打下的
            // with_player 键 —— 键缺了就是 false。**绝不在这里按玩家真名回捞**（同名 NPC 会张冠李戴）。
            bool PlayerHit(JToken it)
            {
                if (filter != "with_me") return true;
                try { return (bool)((JObject)it)["with_player"] == true; } catch { return false; }
            }

            bool MonthHit(JToken it)
            {
                if (sinceMonth <= 0 && untilMonth <= 0) return true;
                int m = 0;
                try { m = (int)((JObject)it)["month"]; } catch { return false; }   // 无月份的时间过滤下不可信，剔除
                if (sinceMonth > 0 && m < sinceMonth) return false;
                if (untilMonth > 0 && m > untilMonth) return false;
                return true;
            }

            if (filterHit("vital"))
                foreach (var it in GetArr("vital")) if (MonthHit(it) && PlayerHit(it)) { TagTier(it, "important"); items.Add(it); }
            if (filterHit("regular"))
                foreach (var it in GetArr("regular")) if (MonthHit(it) && PlayerHit(it)) { TagTier(it, "regular"); items.Add(it); }

            int total = items.Count;
            int start = (page - 1) * 5;
            var pageItems = new JArray();
            for (int i = start; i < total && i < start + 5; i++)
                pageItems.Add(items[i]);

            outObj["items"] = pageItems;
            outObj["total"] = total;
            outObj["has_more"] = start + 5 < total;
            return outObj;
        }

        /// <summary>给经历条目打来源层标记。两层是**独立的流**（`WorldUnitLogMgr` 分开两个暂存字典
        /// `allAddLogData`/`allAddVitalLogData`，最终 `MerageAddLog(..., isBig)` 分落 `allLog`/`allVitalLog`），
        /// 所以 `log_filter=all` 取并集时"哪条是重要"这个信息只在取数处才知道，必须在这里落下，
        /// Python 渲染层才可能区分（否则两桶混在一起，同一条大事看起来跟日常没差别）。
        /// 条目对象归属本次调用刚采集的 snapshot，改它无副作用。</summary>
        internal static void TagTier(JToken it, string tier)
        {
            try { if (it is JObject jo) jo["tier"] = tier; } catch { }
        }

        internal static JObject SearchUnits(JObject args)
        {
            // 全局查询匹配：全图单位按 filters 字典（名字/关系/境界/宗门/地区/种族/性别）过滤。
            // filters 只含模型有把握的键（缺失/空值不过滤），好感降序前 10。
            var filters = args["filters"] as JObject ?? new JObject();
            string F(string k) { var t = filters[k]; return t == null ? "" : t.ToString(); }
            string keyword = F("keyword");
            string relation = F("relation");
            string realm = F("realm");
            string sect = F("sect");
            string region = F("region");
            string race = F("race");
            string sex = F("sex");

            var player = g.world.playerUnit;
            var hits = new List<JObject>();
            try
            {
                var units = g.world.unit.GetUnits(true);
                var dyn = (dynamic)units;
                int cnt = (int)dyn.Count;
                for (int i = 0; i < cnt; i++)
                {
                    var u = dyn[i];
                    if (u == null) continue;
                    var ud = u.data.unitData;
                    var pd = ud.propertyData;
                    string name = "";
                    try { name = pd.GetName(); } catch { }
                    if (string.IsNullOrEmpty(name)) continue;
                    // 跳过玩家自己
                    if (player != null)
                    {
                        string pn = "";
                        try { pn = player.data.unitData.propertyData.GetName(); } catch { }
                        if (name == pn) continue;
                    }

                    // —— filters 逐键过滤（有值才过滤）——
                    if (!string.IsNullOrEmpty(keyword) && name.IndexOf(keyword, StringComparison.Ordinal) < 0) continue;

                    string relEn = "";
                    int intim = 0;
                    try { intim = ud.relationData.GetIntim(player); } catch { }
                    try { relEn = ud.relationData.GetRelation(player).ToString(); } catch { }
                    string relCn = UnitSnapshot.RelationCn(relEn);
                    if (!string.IsNullOrEmpty(relation) && !RelationMatch(relCn, relation)) continue;

                    if (!string.IsNullOrEmpty(realm))
                    {
                        // 口径与面板一致：RealmText = gradeName+phaseName（自洽校验过的行号读法）。
                        // 旧写法 GetGradeName(gradeID) 是行号当大境界号用，过滤"结晶"会命中错人。
                        string r = "";
                        try { r = UnitSnapshot.RealmText(u); } catch { }
                        if (r.IndexOf(realm, StringComparison.Ordinal) < 0) continue;
                    }
                    if (!string.IsNullOrEmpty(sect))
                    {
                        string sc = UnitSnapshot.SectName(ud.schoolID);
                        if (sc.IndexOf(sect, StringComparison.Ordinal) < 0) continue;
                    }
                    if (!string.IsNullOrEmpty(region))
                    {
                        string ar = QueryTool.AreaName(u);
                        if (ar.IndexOf(region, StringComparison.Ordinal) < 0) continue;
                    }
                    if (!string.IsNullOrEmpty(race))
                    {
                        string rc = UnitSnapshot.RaceCn(pd.race.ToString());
                        if (rc != race) continue;
                    }
                    if (!string.IsNullOrEmpty(sex))
                    {
                        string sx = UnitSnapshot.SexCn(pd.sex.ToString());
                        if (sx != sex) continue;
                    }

                    var item = new JObject
                    {
                        ["name"] = name,
                        ["relation"] = relCn,
                        ["intim"] = intim
                    };
                    // unit_id（重名事故）：没有它，模型拿到一串名字后**没有第二条路**
                    // 精确指人，只能拿名字回去按名猜（真机就查成了同名的另一个人）。
                    try { item["unit_id"] = ud.unitID; } catch { }
                    try { item["realm"] = UnitSnapshot.RealmText(u); } catch { }
                    try { item["sect"] = UnitSnapshot.SectName(ud.schoolID); } catch { }
                    try { item["region"] = QueryTool.AreaName(u); } catch { }
                    // 性别：filters 早就支持按 sex 筛选，结果行却不带性别 ——
                    // 模型筛完「找女修」拿到一串名字，仍不知道谁是谁。
                    try { item["sex"] = UnitSnapshot.SexCn(pd.sex.ToString()); } catch { }
                    hits.Add(item);
                }
            }
            catch { }

            // 好感降序，取前 top 条（1-50，默认 10）：取多少归模型 —— 与 rankings 的 top /
            // events 的 count 同一设计语言。解决"total=15 却永远拿不到第 11 名之后"的截断。
            int top = 10;
            try { top = (int)args["top"]; } catch { }
            if (top < 1) top = 1;
            if (top > 50) top = 50;
            hits.Sort((a, b) => ToolArgs.SafeInt(b["intim"], 0).CompareTo(ToolArgs.SafeInt(a["intim"], 0)));
            var items = new JArray();
            for (int i = 0; i < hits.Count && i < top; i++) items.Add(hits[i]);
            var searchData = new JObject { ["filters"] = filters, ["items"] = items, ["total"] = hits.Count, ["top"] = top };
            return Ok(searchData);
        }

        /// <summary>schema relation（中文）与 与玩家关系（中文）匹配。</summary>
        internal static bool RelationMatch(string relCn, string relation)
        {
            switch (relation)
            {
                case "亲族": return relCn == "父母" || relCn == "子女" || relCn == "兄弟姐妹" || relCn == "义父母" || relCn == "义子女" || relCn == "私生子女";
                case "道侣": return relCn == "道侣" || relCn == "情人";
                case "师徒": return relCn == "师傅" || relCn == "徒弟";
                case "结义": return relCn == "结义";
                case "好友":
                case "好感": return true;          // 不按关系过滤（好感排序由外层统一做）
                case "陌生": return relCn == "None" || relCn == "";
                default: return true;
            }
        }

        internal static JObject QueryWorld(JObject args)
        {
            string topic = (string)args["topic"] ?? "sects";
            string region = (string)args["region"];   // 可选：places/sects 按地区过滤（州名见 world_basis / topic=region）
            switch (topic)
            {
                case "events":   return QueryWorldEvents(args);
                case "rankings": return QueryWorldRankings(args);
                case "places":   return QueryWorldPlaces(region, (string)args["cat"], (string)args["name"]);
                case "sects":    return SectQueryTool.QueryWorldSects(args, region, (string)args["name"]);
                case "region":   return QueryWorldRegions();
                default:         return Ok(new JObject { ["topic"] = topic, ["note"] = "当前仅实装 events / rankings / places / sects / region" });
            }
        }

        internal sealed class BuildInfo
        {
            public MapBuildBase Build;
            public string Name, Cat, Region;
            public int X, Y;
        }

        /// <summary>
        /// 全图命名地点（城镇 MapBuildTown + 宗门 MapBuildSchool）：name / GetOrigiPoint() 坐标 /
        /// gridData.areaBaseID → conf.worldAreaBase 区域名。
        /// **必须用泛型重载 `GetBuilds&lt;T&gt;()`**（反编实证：神识传音 types36 L7791
        /// `g.world.build.GetBuilds&lt;MapBuildSchool&gt;()`）——无参 `GetBuilds()` 返回无类型 List，
        /// 元素转 MapBuildBase 逐个抛异常被静默跳过 → 结果恒为空表（2026-09-10 真机事故：
        /// query_world places/sects 双双 total=0）。农场/兽栏等玩法建筑不进地点表。仅主线程调用。
        /// </summary>
        internal static List<BuildInfo> CollectNamedBuilds()
        {
            var list = new List<BuildInfo>();
            int nSchool = CollectTypedBuilds<MapBuildSchool>(list, "宗门");
            int nTown = CollectTypedBuilds<MapBuildTown>(list, "城镇");
            try { UnityEngine.Debug.Log("[Builds] CollectNamedBuilds：宗门=" + nSchool + " 城镇=" + nTown); } catch { }
            return list;
        }

        /// <summary>按具体类型泛型枚举建物（Count + 索引器，IL2CPP 禁 foreach）。返回新增条数。</summary>
        internal static int CollectTypedBuilds<T>(List<BuildInfo> list, string cat) where T : MapBuildBase
        {
            int added = 0;
            try
            {
                var builds = g.world.build.GetBuilds<T>();
                if (builds == null) return 0;
                int cnt = builds.Count;
                for (int i = 0; i < cnt; i++)
                {
                    MapBuildBase b = null;
                    try { b = builds[i]; } catch { continue; }
                    if (b == null) continue;
                    string nm = null;
                    try
                    {
                        nm = b.name;
                        if (!string.IsNullOrEmpty(nm))
                        {
                            string ls = QueryTool.LSOrRaw(nm);          // 名称偶有本地化 key 形态，LS 空则保原文
                            if (!string.IsNullOrEmpty(ls)) nm = ls;
                        }
                    }
                    catch { }
                    if (string.IsNullOrEmpty(nm)) continue;
                    string region = "";
                    try
                    {
                        var areaItem = g.conf.worldAreaBase.GetItem(b.gridData.areaBaseID);
                        // 区域名是本地化 key（areaName9 之类）→ AreaLabel：LS 解析 + 静态兜底表
                        if (areaItem != null && !string.IsNullOrEmpty(areaItem.name))
                            region = QueryTool.AreaLabel(areaItem.name);
                    }
                    catch { }
                    int px = 0, py = 0;
                    try { var p = b.GetOrigiPoint(); px = p.x; py = p.y; } catch { }
                    list.Add(new BuildInfo { Build = b, Name = nm, Cat = cat, Region = region, X = px, Y = py });
                    added++;
                }
            }
            catch (Exception e)
            {
                try { UnityEngine.Debug.Log("[Builds] GetBuilds<" + typeof(T).Name + ">() 失败: " + e.Message); } catch { }
            }
            return added;
        }

        /// <summary>按名找地点：精确命中优先，其次包含；region 给定时同区域者优先（不同区域可有同名分宗）。</summary>
        internal static BuildInfo ResolveBuild(string dest, string region)
        {
            BuildInfo exact = null, contains = null, exactR = null, containsR = null;
            foreach (var b in CollectNamedBuilds())
            {
                bool regionOk = string.IsNullOrEmpty(region) || (b.Region != null && b.Region.Contains(region));
                if (b.Name == dest)
                {
                    if (regionOk) { if (exactR == null) exactR = b; }
                    else if (exact == null) exact = b;
                }
                else if (b.Name.Contains(dest) || dest.Contains(b.Name))
                {
                    if (regionOk) { if (containsR == null) containsR = b; }
                    else if (contains == null) contains = b;
                }
            }
            return exactR ?? exact ?? containsR ?? contains;
        }

        /// <summary>区域匹配：region 为空放行；否则建物区域包含 region（与 ResolveBuild 同规则）。</summary>
        internal static bool RegionOk(BuildInfo b, string region)
        {
            return string.IsNullOrEmpty(region) || (b.Region != null && b.Region.Contains(region));
        }

        /// <summary>可去地点：按 cat 分类查询。四类：城镇/宗门（建物链 MapBuildTown/School）、
        /// 突破材料/器灵材料（小地图分类号 4/5：建物 MapBuild10005 按 conf minMapIconType 归类 +
        /// 事件链 g.world.mapEvent 的 MapEventBase 按 eventBaseItem.minMapIconType 归类，双链都收）。
        /// region 过滤：建物走 gridData.areaBaseID；事件点无格子归属，用 GetPoint() 坐标反查
        /// mapCreate.GetGridData(point).areaBaseID（同一张 worldAreaBase 表解析，反编实证）。
        /// cat 省略/"全部"=城镇+宗门（兼容旧语义）。</summary>
        internal static JObject QueryWorldPlaces(string region, string cat, string name = null)
        {
            // name（按名包含匹配）优先于 region/cat：查"丹阳谷"要主宗+各州分舵全出来
            // （分舵建物名形如"丹阳谷永宁州分舵"，全等会漏），且模型可能记错州/类。
            // 包含匹配在**四类全量**上做（材料点也能按名搜），命中通常只有几条 —— 仍是过滤不是目录。
            bool byName = !string.IsNullOrEmpty(name);
            if (byName) cat = "";
            var all = new List<BuildInfo>();
            if (byName)
            {
                CollectTypedBuilds<MapBuildTown>(all, "城镇");
                CollectTypedBuilds<MapBuildSchool>(all, "宗门");
                CollectIconTypedBuilds<MapBuild10005>(all, "突破材料", 4);
                CollectIconTypedEvents(all, "突破材料", 4);
                CollectIconTypedBuilds<MapBuild10005>(all, "器灵材料", 5);
                CollectIconTypedEvents(all, "器灵材料", 5);
            }
            else if (cat.Length == 0 || cat == "全部")
            {
                CollectTypedBuilds<MapBuildTown>(all, "城镇");
                CollectTypedBuilds<MapBuildSchool>(all, "宗门");
            }
            else if (cat == "城镇" || cat == "宗门")
            {
                if (cat == "城镇") CollectTypedBuilds<MapBuildTown>(all, "城镇");
                else CollectTypedBuilds<MapBuildSchool>(all, "宗门");
            }
            else if (cat == "突破材料" || cat == "器灵材料")
            {
                int icon = cat == "突破材料" ? 4 : 5;   // 小地图分类号（配置互证：4=突破材料 5=器灵材料）
                CollectIconTypedBuilds<MapBuild10005>(all, cat, icon);
                CollectIconTypedEvents(all, cat, icon);
            }
            else
            {
                return Fail("未知 cat=" + cat + "（可选：城镇/宗门/突破材料/器灵材料）");
            }

            var items = new JArray();
            int total = 0;
            foreach (var b in all)
            {
                if (!byName && !RegionOk(b, region)) continue;   // name 查询跨州全搜
                if (byName)
                {
                    if (string.IsNullOrEmpty(b.Name) || b.Name.IndexOf(name, StringComparison.Ordinal) < 0) continue;
                }
                total++;
                items.Add(new JObject { ["name"] = b.Name, ["cat"] = b.Cat, ["region"] = b.Region, ["point"] = new JObject { ["x"] = b.X, ["y"] = b.Y } });
            }
            var o = new JObject { ["places"] = items, ["total"] = total };
            if (cat.Length > 0 && cat != "全部") o["cat"] = cat;
            if (byName)
            {
                o["name"] = name;
                if (total == 0)
                    o["note"] = "未找到名字含「" + name + "」的地点（可先 topic=region 看州名，或不带 name 拉目录）";
            }
            return Ok(o);
        }

        /// <summary>按小地图分类号采建物（药园/石阵/突破/器灵等 MapBuild10005 族）：buildData.id →
        /// ConfWorldBuilding10005 行 → minMapIconType（**字符串形态**，TryParse）匹配才收。</summary>
        internal static int CollectIconTypedBuilds<T>(List<BuildInfo> list, string cat, int iconType) where T : MapBuildBase
        {
            int added = 0;
            try
            {
                var builds = g.world.build.GetBuilds<T>();
                if (builds == null) return 0;
                int cnt = builds.Count;
                for (int i = 0; i < cnt; i++)
                {
                    MapBuildBase b = null;
                    try { b = builds[i]; } catch { continue; }
                    if (b == null) continue;
                    int rowIcon = 0;
                    try
                    {
                        string idStr = b.buildData.id;   // DataBuildBase.BuildDataBase.id（conf 行 id）
                        if (!int.TryParse(idStr, out int rowId)) continue;
                        var row = g.conf.worldBuilding10005.GetItem(rowId);
                        if (row == null) continue;
                        if (!int.TryParse(row.minMapIconType, out rowIcon)) continue;
                    }
                    catch { continue; }
                    if (rowIcon != iconType) continue;
                    string nm = null;
                    try
                    {
                        nm = b.name;
                        if (!string.IsNullOrEmpty(nm))
                        {
                            string ls = QueryTool.LSOrRaw(nm);
                            if (!string.IsNullOrEmpty(ls)) nm = ls;
                        }
                    }
                    catch { }
                    if (string.IsNullOrEmpty(nm)) continue;
                    int px = 0, py = 0;
                    try { var p = b.GetOrigiPoint(); px = p.x; py = p.y; } catch { }
                    list.Add(new BuildInfo { Build = b, Name = nm, Cat = cat, Region = RegionOfGrid(b), X = px, Y = py });
                    added++;
                }
            }
            catch (Exception e)
            {
                try { UnityEngine.Debug.Log("[Builds] CollectIconTypedBuilds<" + typeof(T).Name + "> 失败: " + e.Message); } catch { }
            }
            return added;
        }

        /// <summary>按小地图分类号采地图事件点（突破/器灵材料的另一条链）：GetAllGridEvent() →
        /// MapEventBase.eventBaseItem.minMapIconType 匹配；isHide=true（未发现的隐藏点）不列。
        /// 事件无格子归属，区域用 GetPoint() 坐标反查。</summary>
        internal static int CollectIconTypedEvents(List<BuildInfo> list, string cat, int iconType)
        {
            int added = 0;
            try
            {
                var evs = g.world.mapEvent.GetAllGridEvent();
                if (evs == null) return 0;
                int cnt = evs.Count;
                for (int i = 0; i < cnt; i++)
                {
                    MapEventBase ev = null;
                    try { ev = evs[i]; } catch { continue; }
                    if (ev == null) continue;
                    try { if (ev.isHide) continue; } catch { }   // 隐藏机缘点不进目录
                    int rowIcon = 0;
                    string confName = null;
                    try
                    {
                        var item = ev.eventBaseItem;
                        if (item == null) continue;
                        if (!int.TryParse(item.minMapIconType, out rowIcon)) continue;
                        confName = item.name;
                    }
                    catch { continue; }
                    if (rowIcon != iconType) continue;
                    string nm = null;
                    try { nm = ev.name; } catch { }
                    if (string.IsNullOrEmpty(nm) && !string.IsNullOrEmpty(confName)) nm = QueryTool.LSOrRaw(confName);
                    if (string.IsNullOrEmpty(nm)) continue;
                    int px = 0, py = 0;
                    string region = "";
                    try { var p = ev.GetPoint(); px = p.x; py = p.y; region = RegionOfPoint(p); } catch { }
                    list.Add(new BuildInfo { Build = null, Name = nm, Cat = cat, Region = region, X = px, Y = py });
                    added++;
                }
            }
            catch (Exception e)
            {
                try { UnityEngine.Debug.Log("[Builds] CollectIconTypedEvents 失败: " + e.Message); } catch { }
            }
            return added;
        }

        /// <summary>建物的区域名（gridData.areaBaseID → worldAreaBase；与 CollectTypedBuilds 同链，抽出复用）。</summary>
        internal static string RegionOfGrid(MapBuildBase b)
        {
            try
            {
                var areaItem = g.conf.worldAreaBase.GetItem(b.gridData.areaBaseID);
                if (areaItem != null && !string.IsNullOrEmpty(areaItem.name))
                    return QueryTool.AreaLabel(areaItem.name);
            }
            catch { }
            return "";
        }

        /// <summary>任意坐标的区域名：mapCreate.GetGridData(point).areaBaseID → worldAreaBase
        /// （事件点无格子数据的兜底链，反编实证 MapCreateGridData.areaBaseID）。</summary>
        internal static string RegionOfPoint(UnityEngine.Vector2Int p)
        {
            try
            {
                var gd = g.world.mapCreate.GetGridData(p);
                if (gd == null) return "";
                var areaItem = g.conf.worldAreaBase.GetItem(gd.areaBaseID);
                if (areaItem != null && !string.IsNullOrEmpty(areaItem.name))
                    return QueryTool.AreaLabel(areaItem.name);
            }
            catch { }
            return "";
        }

        /// <summary>topic=region：返回当前世界实际存在的地区（州）名——从所有命名建物收集去重，反映真实存档分布；无任何命名建物时退回静态州表兜底。</summary>
        internal static JObject QueryWorldRegions()
        {
            var list = new List<string>();
            var seen = new HashSet<string>();
            foreach (var b in CollectNamedBuilds())
            {
                if (string.IsNullOrEmpty(b.Region) || !seen.Add(b.Region)) continue;
                list.Add(b.Region);
            }
            if (list.Count == 0)
            {
                foreach (var v in QueryTool.AreaNameFallback.Values)
                    if (seen.Add(v)) list.Add(v);
            }
            var arr = new JArray();
            foreach (var r in list) arr.Add(r);
            return Ok(new JObject { ["regions"] = arr, ["total"] = list.Count });
        }

        /// <summary>近期天下大事：g.data.world.monthLog（世界月志，UIMonthLog 同源），倒序取最近月份，每条 DataToString() 渲染中文。</summary>
        internal static JObject QueryWorldEvents(JObject args)
        {
            var outList = new JArray();
            try
            {
                var monthLog = g.data.world.monthLog;   // List<DataUnitLog.LogData.LogItemData>
                if (monthLog == null)
                {
                    var empty = new JObject { ["events"] = outList, ["count"] = 0, ["note"] = "世界月志为空" };
                    empty["text"] = "近来天下无事。";
                    return Ok(empty);
                }
                var dyn = (dynamic)monthLog;
                int cnt = (int)dyn.Count;
                // 取多少条由模型决定（query_world count）：默认 12 条（≈最近一年），上限 120 仅防呆
                int MaxItems = ToolArgs.SafeInt(args == null ? null : args["count"], 12);
                if (MaxItems < 1) MaxItems = 1;
                if (MaxItems > 120) MaxItems = 120;
                for (int i = cnt - 1; i >= 0 && outList.Count < MaxItems; i--)   // 倒序 = 最近在前
                {
                    var item = dyn[i];
                    if (item == null) continue;
                    int month = 0;
                    try { month = (int)item.month; } catch { }
                    var logs = (dynamic)item.logs;
                    int lc = 0;
                    try { lc = (int)logs.Count; } catch { }
                    for (int j = 0; j < lc && outList.Count < MaxItems; j++)
                    {
                        string txt = "";
                        try { txt = ((dynamic)logs[j]).GetLogString(); if (string.IsNullOrEmpty(txt)) txt = ((dynamic)logs[j]).DataToString(); } catch { try { txt = ((dynamic)logs[j]).DataToString(); } catch { } }
                        if (string.IsNullOrEmpty(txt)) continue;
                        try { txt = UnitSnapshot.CleanLogTextPublic(txt); } catch { }
                        outList.Add(new JObject { ["month"] = month, ["text"] = txt });
                    }
                    var subLogs = (dynamic)item.subLogs;
                    int sc = 0;
                    try { sc = (int)subLogs.Count; } catch { }
                    for (int j = 0; j < sc && outList.Count < MaxItems; j++)
                    {
                        string txt = "";
                        try { txt = ((dynamic)subLogs[j]).GetLogString(); if (string.IsNullOrEmpty(txt)) txt = ((dynamic)subLogs[j]).DataToString(); } catch { try { txt = ((dynamic)subLogs[j]).DataToString(); } catch { } }
                        if (string.IsNullOrEmpty(txt)) continue;
                        try { txt = UnitSnapshot.CleanLogTextPublic(txt); } catch { }
                        outList.Add(new JObject { ["month"] = month, ["text"] = txt });
                    }
                }
            }
            catch (Exception e) { return Fail("读取世界月志失败: " + e.Message); }
            var ret = new JObject { ["events"] = outList, ["count"] = outList.Count };
            return Ok(ret);
        }

        /// <summary>天下排行榜：power/reputation/beauty/money 四榜 Top N。全图扫描单位 + 指标算分排序（神识传音同款做法）。</summary>
        internal static JObject QueryWorldRankings(JObject args)
        {
            string board = (string)args["board"] ?? "power";
            int top = ToolArgs.SafeInt(args["top"], 10);
            if (top < 1) top = 1;
            if (top > 200) top = 200;      // 查多少由模型决定；200 仅防呆（防意外 dump 全图）
            if (board != "power" && board != "reputation" && board != "beauty" && board != "money")
                return Fail("未知榜单 " + board + "，可选 power/reputation/beauty/money");

            var player = g.world.playerUnit;
            var hits = new List<JObject>();
            try
            {
                var units = g.world.unit.GetUnits(true);
                var dyn = (dynamic)units;
                int cnt = (int)dyn.Count;
                for (int i = 0; i < cnt; i++)
                {
                    var u = dyn[i] as WorldUnitBase;
                    if (u == null) continue;
                    var ud = u.data.unitData;
                    var pd = ud.propertyData;
                    string name = "";
                    try { name = pd.GetName(); } catch { }
                    if (string.IsNullOrEmpty(name)) continue;
                    // 跳过玩家自己（与 search_units 一致）
                    if (player != null)
                    {
                        string pn = "";
                        try { pn = player.data.unitData.propertyData.GetName(); } catch { }
                        if (name == pn) continue;
                    }

                    long score = 0;
                    switch (board)
                    {
                        case "power":       try { score = FormulaTool.UnitPower.TotalPower(u.data); } catch { continue; } break;
                        case "reputation":  try { score = pd.reputation; } catch { continue; } break;
                        case "beauty":      try { score = pd.beauty; } catch { continue; } break;
                        case "money":       try { score = ((DataProps)ud.propData).GetPropsNum(10001); } catch { try { score = ud.propData.totalSchoolMoney; } catch { continue; } } break;
                    }
                    var item = new JObject { ["name"] = name, ["score"] = score };
                    try { item["realm"] = UnitSnapshot.RealmText(u); } catch { }   // 口径同面板（勿回退成 GetGradeName(gradeID)）
                    try { item["sect"] = UnitSnapshot.SectName(ud.schoolID); } catch { }
                    hits.Add(item);
                }
            }
            catch (Exception e) { return Fail("排行榜扫描失败: " + e.Message); }

            hits.Sort((a, b) => ToolArgs.LongOf(b["score"]).CompareTo(ToolArgs.LongOf(a["score"])));
            var items = new JArray();
            for (int i = 0; i < hits.Count && i < top; i++) items.Add(hits[i]);
            var rankData = new JObject { ["board"] = board, ["items"] = items, ["total"] = hits.Count };
                        return Ok(rankData);
        }

        /// <summary>单位所在地区名：pointGridData.areaBaseID -> ConfWorldAreaBaseItem.name 本地化（白源区/永宁州...）。</summary>
        internal static string AreaName(WorldUnitBase u)
        {
            try
            {
                var gd = u.data.unitData.pointGridData;
                if (gd != null)
                {
                    var item = g.conf.worldAreaBase.GetItem(gd.areaBaseID);
                    if (item != null && !string.IsNullOrEmpty(item.name))
                        return AreaLabel(item.name);
                }
            }
            catch { }
            return "";
        }

        /// <summary>
        /// 区域名 → 中文。conf.worldAreaBase.name 存的是**本地化 key**（areaName9 之类），
        /// 运行时由 GameTool.LS 解析；万一 LS 未解析（返回值仍是 key 形态/空），用静态兜底表。
        /// 兜底表来源：游戏本地化表 LocalText.json（Mod\modFQA\配置修改教程\配置（只读）Json格式\，
        /// 条目 id 18014+ 的 ch 列），从游戏本体读取实锤。
        /// </summary>
        internal static readonly Dictionary<string, string> AreaNameFallback = new Dictionary<string, string>
        {
            ["areaName1"] = "白源区", ["areaName2"] = "永宁州", ["areaName3"] = "雷泽", ["areaName4"] = "华封州",
            ["areaName5"] = "十万大山", ["areaName6"] = "云陌州", ["areaName7"] = "永恒冰原", ["areaName8"] = "暮仙州",
            ["areaName9"] = "迷途荒漠", ["areaName10"] = "赤幽州", ["areaName11"] = "天元", ["areaName12"] = "冥山",
            ["areaName13"] = "太行山",
        };

        internal static string AreaLabel(string raw)
        {
            if (string.IsNullOrEmpty(raw)) return raw;
            string ls = LSOrRaw(raw);
            if (!string.IsNullOrEmpty(ls) && !ls.StartsWith("areaName", StringComparison.Ordinal))
                return ls;                                  // LS 正常解析（运行时权威）
            string fb;
            if (AreaNameFallback.TryGetValue(raw, out fb)) return fb;
            return string.IsNullOrEmpty(ls) ? raw : ls;
        }

        /// <summary>本地化兜底：conf 文本字段过 GameTool.LS，失败返回原文（面板查表同款惯例）。</summary>
        internal static string LSOrRaw(string s)
        {
            if (string.IsNullOrEmpty(s)) return s;
            try { return GameTool.LS(s); } catch { return s; }
        }

        /// <summary>气运 featureID → 中文文案（g.conf.fateFeature.GetItem → name 优先 desc 兜底，UnitSnapshot.LuckItemsWithMap 同款回退链）。</summary>
        internal static string FateText(int fateFeatureId)
        {
            if (fateFeatureId == 0) return null;
            try
            {
                var item = g.conf.fateFeature.GetItem(fateFeatureId);
                if (item != null)
                {
                    string name = null, desc = null;
                    try { name = ((dynamic)item).name; } catch { }
                    if (string.IsNullOrEmpty(name)) try { name = ((dynamic)item).desc; } catch { }
                    try { desc = ((dynamic)item).desc; } catch { }
                    string s = string.IsNullOrEmpty(name) ? desc : name;
                    return LSOrRaw(s);
                }
            }
            catch { }
            return null;
        }
    }
}
