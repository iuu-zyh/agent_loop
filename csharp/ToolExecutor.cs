/// <summary>
/// 工具执行器 —— request: call_tool
///
/// 用官方 GGBH_API 实现：真实读取单位/好感/位置，真实发起行为与剧情命令，
/// 替代旧骨架的硬编码桩（thisId 恒"林婉清"、GetIntim 恒135、SameGrid 恒同格）。
///
/// 二阶段校验（不信任模型参数，必须在此做）：
///   - 同格校验（combat_duel 等面对面行为需同格）
///   - 好感门槛（social_relation.jie_yuan 等）
///   - 性别限制 / 战力比较等（由对应动作在此拦截）
///
/// 仍待真机运行时补齐（官方文档缺公开 entry，见各 TODO）：
///   - 按条件搜索单位（需 WorldUnitMgr 遍历/conf 过滤）
///   - 结缘等关系"写入"的确凿函数、背包物品转移、秘境/宗门命令的下发方式
/// </summary>
using System;
using System.Collections.Generic;
using Newtonsoft.Json.Linq;

namespace AgentLoopBridge
{
    public class ToolExecutor
    {
        /// <summary>主线程执行工具，返回 {"success":bool, "data":..., "error":...}
        /// （执行期间置 SuppressAiOption：工具自己发起的原生剧情窗不注入「AI 对话」——
        /// 那些剧情是模型刚用工具发起的，回喂会语义错位，见 README.md 附录二 G（功能设计底稿））</summary>
        public JObject Execute(string name, JObject args)
        {
            DramaGate.SuppressAiOption = true;
            try
            {
                switch (name)
                {
                    case "inspect_unit":      return InspectUnit(args);
                    case "search_units":      return SearchUnits(args);
                    case "query_world":       return QueryWorld(args);
                    case "social_relation":   return SocialRelation(args);
                    case "movement":          return Movement(args);
                    case "world_ai_action":   return WorldAiAction(args);
                    case "economy_item":      return EconomyItem(args);
                    case "trade":             return Trade(args);
                    case "item_acquire":      return ItemAcquire(args);
                    default:                  return Fail("unknown tool: " + name);
                }
            }
            catch (Exception e)
            {
                return Fail(e.Message);
            }
            finally
            {
                DramaGate.SuppressAiOption = false;
            }
        }

        // ---------- 查询类（只读） ----------

        private JObject InspectUnit(JObject args)
        {
            string target = (string)args["target"] ?? "";
            string unitId = (string)args["unit_id"] ?? "";
            if (string.IsNullOrEmpty(target) && string.IsNullOrEmpty(unitId))
                return Fail("请给 target（人物真名）或 unit_id（从 relationships / search_units 拿到的精确 ID）");
            // 精确优先：有 unit_id 时**只认它**（UnitLookup 内部不回退按名 —— 回退就等于又猜一次）
            List<UnitLookup.Candidate> cands;
            var wub = UnitLookup.ResolveEx(target, string.IsNullOrEmpty(unitId) ? null : unitId, out cands);
            if (wub == null)
                return Fail(string.IsNullOrEmpty(unitId)
                    ? "未找到 " + target
                    : "unit_id=" + unitId + " 不存在（注意：不是人物名，是 relationships/search_units 里的 unit_id）");
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

            // 与 L1 同源：UnitSnapshot 按 classes 按需采集（只采请求的块，不读多余的 logs 等重数据）
            var snap = UnitSnapshot.Build(wub, classes, invTop);

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
                //          + 与玩家关系·好感·同格
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
                data["relation"] = snap["relation"];
                data["intim"] = snap["intim"];
                data["same_grid"] = snap["same_grid"];
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
                int page = SafeInt(args["log_page"], 1);
                if (page < 1) page = 1;
                // 时间范围（可选，账面月：1年1月=1、1年12月=12、2年1月=13；范围过滤后再分页）
                int since = SafeInt(args["log_since_month"], 0);
                int until = SafeInt(args["log_until_month"], 0);
                data["logs"] = PageLogs(snap, filter, page, since, until);
                var _pl = data["logs"] as JObject;
                ModMain.P("[Inspect] PageLogs 输出=" + _pl.ToString(Newtonsoft.Json.Formatting.None));
            }
            return Ok(data);
        }

        /// <summary>经历日志分页：log_filter 选 重要(vital)/常规(regular)/all，log_page 每页 5 条；
        /// sinceMonth/untilMonth&gt;0 时按账面月闭区间过滤（过滤先于分页，total=过滤后条数）。</summary>
        private static JObject PageLogs(JObject snap, string filter, int page, int sinceMonth = 0, int untilMonth = 0)
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
                // f = 桶名（"vital"/"regular"），filter = 调用方传来的筛选值（"all"/"important"/"regular"）。
                // 修 原写 `if(f == "all")return true;` 用错了变量——"vital"/"regular" 永不等于 "all"，
                // 于是 filter=="all"（**默认值**）时两个桶全被排除 → items 恒空 → 渲染层判「经历：无记录」。
                // 真机证据：`[Logs] BuildLogs 命中主路 unitID=FJLlLl vital=1 regular=1` 后紧接
                //          `[Inspect] PageLogs 输出={"filter":"all","items":[],"total":0}`（数据在、被这里滤光）。
                // 只有 all 会踩中：important/regular 两分支本来就正确用 filter 比较，所以那两种筛选一直是对的。
                if (filter == "all") return true;
                if (filter == "important" && f == "vital") return true;
                if (filter == "regular" && f == "regular") return true;
                return false;
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
                foreach (var it in GetArr("vital")) if (MonthHit(it)) { TagTier(it, "important"); items.Add(it); }
            if (filterHit("regular"))
                foreach (var it in GetArr("regular")) if (MonthHit(it)) { TagTier(it, "regular"); items.Add(it); }

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
        private static void TagTier(JToken it, string tier)
        {
            try { if (it is JObject jo) jo["tier"] = tier; } catch { }
        }

        private JObject SearchUnits(JObject args)
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
                        string r = "";
                        try { r = g.conf.roleGrade.GetGradeName(pd.gradeID); } catch { }
                        if (r.IndexOf(realm, StringComparison.Ordinal) < 0) continue;
                    }
                    if (!string.IsNullOrEmpty(sect))
                    {
                        string sc = UnitSnapshot.SectName(ud.schoolID);
                        if (sc.IndexOf(sect, StringComparison.Ordinal) < 0) continue;
                    }
                    if (!string.IsNullOrEmpty(region))
                    {
                        string ar = AreaName(u);
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
                    try { item["realm"] = g.conf.roleGrade.GetGradeName(pd.gradeID); } catch { }
                    try { item["sect"] = UnitSnapshot.SectName(ud.schoolID); } catch { }
                    try { item["region"] = AreaName(u); } catch { }
                    // 性别：filters 早就支持按 sex 筛选，结果行却不带性别 ——
                    // 模型筛完「找女修」拿到一串名字，仍不知道谁是谁。
                    try { item["sex"] = UnitSnapshot.SexCn(pd.sex.ToString()); } catch { }
                    hits.Add(item);
                }
            }
            catch { }

            // 好感降序前 10
            hits.Sort((a, b) => IntOf(b["intim"]).CompareTo(IntOf(a["intim"])));
            var items = new JArray();
            for (int i = 0; i < hits.Count && i < 10; i++) items.Add(hits[i]);
            var searchData = new JObject { ["filters"] = filters, ["items"] = items, ["total"] = hits.Count };
            return Ok(searchData);
        }

        /// <summary>单位所在地区名：pointGridData.areaBaseID -> ConfWorldAreaBaseItem.name 本地化（白源区/永宁州...）。</summary>
        private static string AreaName(WorldUnitBase u)
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
        private static readonly Dictionary<string, string> AreaNameFallback = new Dictionary<string, string>
        {
            ["areaName1"] = "白源区", ["areaName2"] = "永宁州", ["areaName3"] = "雷泽", ["areaName4"] = "华封州",
            ["areaName5"] = "十万大山", ["areaName6"] = "云陌州", ["areaName7"] = "永恒冰原", ["areaName8"] = "暮仙州",
            ["areaName9"] = "迷途荒漠", ["areaName10"] = "赤幽州", ["areaName11"] = "天元", ["areaName12"] = "冥山",
            ["areaName13"] = "太行山",
        };

        private static string AreaLabel(string raw)
        {
            if (string.IsNullOrEmpty(raw)) return raw;
            string ls = LSOrRaw(raw);
            if (!string.IsNullOrEmpty(ls) && !ls.StartsWith("areaName", StringComparison.Ordinal))
                return ls;                                  // LS 正常解析（运行时权威）
            string fb;
            if (AreaNameFallback.TryGetValue(raw, out fb)) return fb;
            return string.IsNullOrEmpty(ls) ? raw : ls;
        }

        /// <summary>schema relation（中文）与 与玩家关系（中文）匹配。</summary>
        private static bool RelationMatch(string relCn, string relation)
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

        private static int IntOf(JToken t)
        {
            try { return t == null ? 0 : (int)t; } catch { return 0; }
        }

        private JObject QueryWorld(JObject args)
        {
            string topic = (string)args["topic"] ?? "sects";
            string region = (string)args["region"];   // 可选：places/sects 按地区过滤（州名见 world_basis / topic=region）
            switch (topic)
            {
                case "events":   return QueryWorldEvents(args);
                case "rankings": return QueryWorldRankings(args);
                case "places":   return QueryWorldPlaces(region, (string)args["cat"]);
                case "sects":    return QueryWorldSects(region);
                case "region":   return QueryWorldRegions();
                default:         return Ok(new JObject { ["topic"] = topic, ["note"] = "当前仅实装 events / rankings / places / sects / region" });
            }
        }

        private class BuildInfo
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
        private List<BuildInfo> CollectNamedBuilds()
        {
            var list = new List<BuildInfo>();
            int nSchool = CollectTypedBuilds<MapBuildSchool>(list, "宗门");
            int nTown = CollectTypedBuilds<MapBuildTown>(list, "城镇");
            try { UnityEngine.Debug.Log("[Builds] CollectNamedBuilds：宗门=" + nSchool + " 城镇=" + nTown); } catch { }
            return list;
        }

        /// <summary>按具体类型泛型枚举建物（Count + 索引器，IL2CPP 禁 foreach）。返回新增条数。</summary>
        private int CollectTypedBuilds<T>(List<BuildInfo> list, string cat) where T : MapBuildBase
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
                            string ls = LSOrRaw(nm);          // 名称偶有本地化 key 形态，LS 空则保原文
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
                            region = AreaLabel(areaItem.name);
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
        private BuildInfo ResolveBuild(string dest, string region)
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
        private static bool RegionOk(BuildInfo b, string region)
        {
            return string.IsNullOrEmpty(region) || (b.Region != null && b.Region.Contains(region));
        }

        /// <summary>可去地点：按 cat 分类查询。四类：城镇/宗门（建物链 MapBuildTown/School）、
        /// 突破材料/器灵材料（小地图分类号 4/5：建物 MapBuild10005 按 conf minMapIconType 归类 +
        /// 事件链 g.world.mapEvent 的 MapEventBase 按 eventBaseItem.minMapIconType 归类，双链都收）。
        /// region 过滤：建物走 gridData.areaBaseID；事件点无格子归属，用 GetPoint() 坐标反查
        /// mapCreate.GetGridData(point).areaBaseID（同一张 worldAreaBase 表解析，反编实证）。
        /// cat 省略/"全部"=城镇+宗门（兼容旧语义）。</summary>
        private JObject QueryWorldPlaces(string region, string cat)
        {
            cat = (cat ?? "").Trim();
            var all = new List<BuildInfo>();
            if (cat.Length == 0 || cat == "全部")
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
                if (!RegionOk(b, region)) continue;
                total++;
                items.Add(new JObject { ["name"] = b.Name, ["cat"] = b.Cat, ["region"] = b.Region, ["point"] = new JObject { ["x"] = b.X, ["y"] = b.Y } });
            }
            var o = new JObject { ["places"] = items, ["total"] = total };
            if (cat.Length > 0 && cat != "全部") o["cat"] = cat;
            return Ok(o);
        }

        /// <summary>按小地图分类号采建物（药园/石阵/突破/器灵等 MapBuild10005 族）：buildData.id →
        /// ConfWorldBuilding10005 行 → minMapIconType（**字符串形态**，TryParse）匹配才收。</summary>
        private int CollectIconTypedBuilds<T>(List<BuildInfo> list, string cat, int iconType) where T : MapBuildBase
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
                            string ls = LSOrRaw(nm);
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
        private int CollectIconTypedEvents(List<BuildInfo> list, string cat, int iconType)
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
                    if (string.IsNullOrEmpty(nm) && !string.IsNullOrEmpty(confName)) nm = LSOrRaw(confName);
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
        private static string RegionOfGrid(MapBuildBase b)
        {
            try
            {
                var areaItem = g.conf.worldAreaBase.GetItem(b.gridData.areaBaseID);
                if (areaItem != null && !string.IsNullOrEmpty(areaItem.name))
                    return AreaLabel(areaItem.name);
            }
            catch { }
            return "";
        }

        /// <summary>任意坐标的区域名：mapCreate.GetGridData(point).areaBaseID → worldAreaBase
        /// （事件点无格子数据的兜底链，反编实证 MapCreateGridData.areaBaseID）。</summary>
        private static string RegionOfPoint(UnityEngine.Vector2Int p)
        {
            try
            {
                var gd = g.world.mapCreate.GetGridData(p);
                if (gd == null) return "";
                var areaItem = g.conf.worldAreaBase.GetItem(gd.areaBaseID);
                if (areaItem != null && !string.IsNullOrEmpty(areaItem.name))
                    return AreaLabel(areaItem.name);
            }
            catch { }
            return "";
        }

        /// <summary>topic=region：返回当前世界实际存在的地区（州）名——从所有命名建物收集去重，反映真实存档分布；无任何命名建物时退回静态州表兜底。</summary>
        private JObject QueryWorldRegions()
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
                foreach (var v in AreaNameFallback.Values)
                    if (seen.Add(v)) list.Add(v);
            }
            var arr = new JArray();
            foreach (var r in list) arr.Add(r);
            return Ok(new JObject { ["regions"] = arr, ["total"] = list.Count });
        }

        /// <summary>本地化兜底：conf 文本字段过 GameTool.LS，失败返回原文（面板查表同款惯例）。</summary>
        private static string LSOrRaw(string s)
        {
            if (string.IsNullOrEmpty(s)) return s;
            try { return GameTool.LS(s); } catch { return s; }
        }

        /// <summary>unitID → 中文名（g.world.unit.GetUnit），失败返回 null（UnitSnapshot.UnitNames 同款）。</summary>
        private static string UnitNameById(string id)
        {
            if (string.IsNullOrEmpty(id)) return null;
            try
            {
                var u = g.world.unit.GetUnit(id);
                if (u != null) return u.data.unitData.propertyData.GetName();
            }
            catch { }
            return null;
        }

        /// <summary>气运 featureID → 中文文案（g.conf.fateFeature.GetItem → name 优先 desc 兜底，UnitSnapshot.LuckItemsWithMap 同款回退链）。</summary>
        private static string FateText(int fateFeatureId)
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

        /// <summary>
        /// 宗门概览（topic=sects，实装；region 可选过滤）：
        /// ①名号组成（mainName/branchName/nameOrigin；name1/name2 组成词 已按用户裁剪）
        /// ②存亡/被占状态（isBeHold + curHoldSchoolNameID 反查持有宗名——字段语义是"当前由谁掌控"，
        ///   分宗可能恒处依附态，"被占=已灭"的强结论留真机校准，故只报事实不编结论）
        /// ③宗门层级（IsTopSchool/GetTopSchool 两遍分组：主宗名下挂子宗名列表）
        /// ④宗旨/气运（slogan 四槽位 LS 去重取文案；fateItem.fateFeature → g.conf.fateFeature.GetItem(id)
        ///   → name/desc；type/stand 立场值 2026-09-11 已按用户裁剪移除）
        /// 顺手带：宗主/弟子数/声望/敌对宗门（buildData=DataBuildSchool.SchoolData 反编实锤）。
        /// 反编依据：dump/types48_school_conf.txt + types42_move_map(MapBuildSchool) + types3(MapBuildSchoolData)。
        /// </summary>
        private JObject QueryWorldSects(string region = null)
        {
            var schools = new List<KeyValuePair<BuildInfo, MapBuildSchool>>();
            foreach (var bi in CollectNamedBuilds())
            {
                if (!RegionOk(bi, region)) continue;
                MapBuildSchool ms = null;
                try { ms = bi.Build as MapBuildSchool; } catch { }
                if (ms != null) schools.Add(new KeyValuePair<BuildInfo, MapBuildSchool>(bi, ms));
            }

            // ③层级两遍分组：每宗找 GetTopSchool（自身即主宗时返回自身），主宗名下挂子宗名
            var topOf = new Dictionary<IntPtr, MapBuildSchool>();
            foreach (var kv in schools)
            {
                MapBuildSchool top = null;
                try { top = kv.Value.IsTopSchool() ? kv.Value : kv.Value.GetTopSchool(); } catch { }
                topOf[kv.Value.Pointer] = top ?? kv.Value;
            }
            var subSchools = new Dictionary<IntPtr, List<string>>();
            foreach (var kv in schools)
            {
                MapBuildSchool top;
                if (!topOf.TryGetValue(kv.Value.Pointer, out top) || top == null) continue;
                if (top.Pointer == kv.Value.Pointer) continue;
                string nm = null;
                try { nm = kv.Value.name; } catch { }
                List<string> lst;
                if (!subSchools.TryGetValue(top.Pointer, out lst)) { lst = new List<string>(); subSchools[top.Pointer] = lst; }
                lst.Add(string.IsNullOrEmpty(nm) ? "未名宗门" : nm);
            }

            var items = new JArray();
            foreach (var kv in schools)
            {
                var bi = kv.Key;
                var ms = kv.Value;
                var o = new JObject
                {
                    ["name"] = bi.Name,
                    ["region"] = bi.Region,
                    ["point"] = new JObject { ["x"] = bi.X, ["y"] = bi.Y },
                };

                // —— ①名号组成（只留 主名/支名/源名，去掉 name1/name2 组成词——用户判定冗余） ——
                try { if (!string.IsNullOrEmpty(ms.mainName)) o["main_name"] = LSOrRaw(ms.mainName); } catch { }
                try { if (!string.IsNullOrEmpty(ms.branchName)) o["branch_name"] = LSOrRaw(ms.branchName); } catch { }
                try { if (!string.IsNullOrEmpty(ms.nameOrigin) && ms.nameOrigin != ms.name) o["name_origin"] = LSOrRaw(ms.nameOrigin); } catch { }

                // —— ②存亡/被占 ——
                bool behold = false;
                try { behold = ms.isBeHold; } catch { }
                o["is_hold"] = behold;
                if (behold)
                {
                    string holderId = null;
                    try { holderId = ms.curHoldSchoolNameID; } catch { }
                    if (!string.IsNullOrEmpty(holderId))
                    {
                        string holderName = null;
                        foreach (var kv2 in schools)
                        {
                            string snid = null;
                            try { snid = kv2.Value.schoolNameID; } catch { }
                            if (snid == holderId) { try { holderName = kv2.Value.name; } catch { } break; }
                        }
                        o["hold_by"] = string.IsNullOrEmpty(holderName) ? holderId : LSOrRaw(holderName);
                    }
                }

                // —— ③层级 ——
                bool isTop = false;
                try { isTop = ms.IsTopSchool(); } catch { }
                o["is_top"] = isTop;
                MapBuildSchool topSchool;
                if (topOf.TryGetValue(ms.Pointer, out topSchool) && topSchool != null && topSchool.Pointer != ms.Pointer)
                {
                    string tn = null;
                    try { tn = topSchool.name; } catch { }
                    if (!string.IsNullOrEmpty(tn)) o["top_school"] = LSOrRaw(tn);
                }
                List<string> subs;
                if (subSchools.TryGetValue(ms.Pointer, out subs) && subs.Count > 0)
                    o["sub_schools"] = new JArray(subs);

                // —— ④宗旨/气运（type/stand 已按用户裁剪移除） ——
                try
                {
                    var sd = ms.schoolData;
                    if (sd != null)
                    {
                        var slogans = new JArray();
                        var seen = new HashSet<string>();
                        ConfSchoolSloganItem[] slots = null;
                        try { slots = new[] { sd.schoolSloganItem1Type1, sd.schoolSloganItem1Type2, sd.schoolSloganItem2Type1, sd.schoolSloganItem2Type2 }; } catch { }
                        if (slots != null)
                            foreach (var si in slots)
                            {
                                if (si == null) continue;
                                string s = null;
                                try { s = LSOrRaw(si.slogan); } catch { }
                                if (string.IsNullOrEmpty(s)) try { s = LSOrRaw(si.desc); } catch { }
                                if (string.IsNullOrEmpty(s) || !seen.Add(s)) continue;
                                var so = new JObject { ["slogan"] = s };
                                try { if (!string.IsNullOrEmpty(si.desc) && si.desc != si.slogan) so["desc"] = LSOrRaw(si.desc); } catch { }
                                slogans.Add(so);
                                if (slogans.Count >= 3) break;
                            }
                        if (slogans.Count > 0) o["slogans"] = slogans;

                        try
                        {
                            var fi = sd.fateItem;
                            if (fi != null)
                            {
                                string ft = FateText(fi.fateFeature);
                                if (!string.IsNullOrEmpty(ft)) o["fate"] = ft;
                            }
                        }
                        catch { }
                    }
                }
                catch { }

                // —— 顺手：宗主/弟子数/声望/敌对（buildData=DataBuildSchool.SchoolData） ——
                try
                {
                    dynamic bd = ms.buildData;
                    if (bd != null)
                    {
                        try { int tm = (int)bd.totalMember; if (tm > 0) o["member_count"] = tm; } catch { }
                        try { int rp = (int)bd.reputation; o["reputation"] = rp; } catch { }
                        try
                        {
                            string mid = (string)bd.npcSchoolMain;
                            string mn = UnitNameById(mid);
                            if (!string.IsNullOrEmpty(mn)) o["leader"] = mn;
                        }
                        catch { }
                        try
                        {
                            string eid = (string)bd.enemySchoolID;
                            if (!string.IsNullOrEmpty(eid))
                            {
                                string en = null;
                                try { var eb = g.world.build.GetBuild(eid); if (eb != null) en = eb.name; } catch { }
                                o["enemy"] = string.IsNullOrEmpty(en) ? eid : LSOrRaw(en);
                            }
                        }
                        catch { }
                    }
                }
                catch { }

                items.Add(o);
            }

            var result = new JObject { ["sects"] = items, ["total"] = items.Count };
            return Ok(result);
        }

        /// <summary>近期天下大事：g.data.world.monthLog（世界月志，UIMonthLog 同源），倒序取最近月份，每条 DataToString() 渲染中文。</summary>
        private JObject QueryWorldEvents(JObject args)
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
                int MaxItems = SafeInt(args == null ? null : args["count"], 12);
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
        private JObject QueryWorldRankings(JObject args)
        {
            string board = (string)args["board"] ?? "power";
            int top = SafeInt(args["top"], 10);
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
                    try { item["realm"] = g.conf.roleGrade.GetGradeName(pd.gradeID); } catch { }
                    try { item["sect"] = UnitSnapshot.SectName(ud.schoolID); } catch { }
                    hits.Add(item);
                }
            }
            catch (Exception e) { return Fail("排行榜扫描失败: " + e.Message); }

            hits.Sort((a, b) => LongOf(b["score"]).CompareTo(LongOf(a["score"])));
            var items = new JArray();
            for (int i = 0; i < hits.Count && i < top; i++) items.Add(hits[i]);
            var rankData = new JObject { ["board"] = board, ["items"] = items, ["total"] = hits.Count };
                        return Ok(rankData);
        }

        private static long LongOf(JToken t)
        {
            try { return t == null ? 0 : (long)t; } catch { return 0; }
        }

        // ---------- 动作类（需二阶段校验） ----------

        private JObject SocialRelation(JObject args)
        {
            string op = (string)args["op"] ?? "";
            // 发起方恒为 harness 注入的 initiator（dialogue_agent._execute_tool_calls）。
            // 删掉那个旧版 `target` 兼容别名：schema 里从来没有 target、
            // 也没人写它 —— 留着只会让"actor 从哪来"这件事看起来有两个来源。
            string initiator = (string)args["initiator"] ?? "";
            var wub = UnitLookup.Resolve(initiator);
            if (wub == null)
                return Fail("未找到发起方 " + initiator);
            var player = g.world.playerUnit;
            if (player == null)
                return Fail("玩家单位为空");
            // target 已删，恒为玩家；为兼容保留 target 变量指向玩家名（日志用）
            string target = "";
            try { target = wub.data.unitData.propertyData.GetName(); } catch { target = initiator; }
            int intim = GetIntimWith(initiator);
            var rel = wub.data.unitData.relationData;
            // 模型台词（确认窗正文，可选）：trim + 500 字截断；仅关系类 op 消费，好感直写忽略
            string askText = ((string)args["ask_text"] ?? "").Trim();
            if (askText.Length > 500) askText = askText.Substring(0, 500);

            switch (op)
            {
                case "add_intim":   // 加好感：直写 RelationData.AddIntim（瞬时、无拒绝）
                {
                    int delta = SafeInt(args["value"], 5);
                    if (delta < 1) delta = 1;
                    if (delta > 10) delta = 10;
                    int before = intim;
                    rel.AddIntim((string)player.data.unitData.unitID, delta, 0, "", true);
                    int after = GetIntimWith(target);
                    int actual = after - before;
                    return Ok(new JObject
                    {
                        ["op"] = op, ["target"] = target,
                        ["requested"] = delta, ["actual_delta"] = actual,
                        ["intim_before"] = before, ["current_intim"] = after,
                    });
                }
                case "reduce_intim": // 减好感：直写 RelationData.AddHate
                {
                    int delta = SafeInt(args["value"], 5);
                    if (delta < 1) delta = 1;
                    if (delta > 10) delta = 10;
                    int before = intim;
                    rel.AddHate((string)player.data.unitData.unitID, delta, 0, "", true);
                    int after = GetIntimWith(target);
                    int actual = after - before;
                    return Ok(new JObject
                    {
                        ["op"] = op, ["target"] = target,
                        ["requested"] = delta, ["actual_delta"] = actual,
                        ["intim_before"] = before, ["current_intim"] = after,
                    });
                }

                case "jie_yuan":     // 结缘 → 道侣（Lover）——已移除C端好感校验（由Python侧/模型decision）
                {
                    if (rel.IsRelation(player, UnitRelationType.Lover))
                        return Fail("你与" + target + "已是道侣");
                    return StartRelationAction(wub, player, UnitRelationType.Lover, op, askText);
                }
                case "jie_chu_jie_yuan":
                {
                    if (!rel.IsRelation(player, UnitRelationType.Lover))
                        return Fail("你与" + target + "并非道侣");
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
                    if (!SameGrid(wub, player))
                        return Fail("求婚需同处一地，当前异地");
                    string npcId = (string)wub.data.unitData.unitID;
                    string playerId = (string)player.data.unitData.unitID;
                    string marryText = string.IsNullOrWhiteSpace(askText)
                        ? $"{target}向你正式求婚：愿与我结为夫妻，共证大道吗？"
                        : $"{target}向你求婚：\n{askText.Trim()}";
                    return Claimed(wub, () => ShowDramaService.ShowConfirm(
                        ModIds.DramaSocialRelationBase,
                        player, wub,
                        marryText,
                        "我愿意", "再想想",
                        onOk: () =>
                        {
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
                            });
                        },
                        okTip: "礼成！",
                        noTip: "玩家婉拒了求婚"));
                }
                case "divorce":
                {
                    if (!rel.IsRelation(player, UnitRelationType.Married))
                        return Fail("你与" + target + "并非夫妻");
                    return StartBreakWith(wub, player, op, askText);
                }

                case "bai_shi":      // 拜师：NPC 认玩家做师傅 → player 是 NPC 的 Master
                {
                    if (rel.IsRelation(player, UnitRelationType.Master))
                        return Fail("你已是" + target + "的师傅");
                    return StartRelationAction(wub, player, UnitRelationType.Master, op, askText);
                }
                case "jie_chu_bai_shi":
                {
                    if (!rel.IsRelation(player, UnitRelationType.Master))
                        return Fail("你并非" + target + "的师傅");
                    return StartBreakWith(wub, player, op, askText);
                }
                case "shou_tu":      // 收徒：NPC 收玩家做徒弟 → player 是 NPC 的 Student
                {
                    if (rel.IsRelation(player, UnitRelationType.Student))
                        return Fail(target + "已是你的徒弟");
                    return StartRelationAction(wub, player, UnitRelationType.Student, op, askText);
                }
                case "jie_chu_shou_tu":
                {
                    if (!rel.IsRelation(player, UnitRelationType.Student))
                        return Fail(target + "并非你的徒弟");
                    return StartBreakWith(wub, player, op, askText);
                }

                case "ren_yi_fu_mu": // 认义父母：NPC 认玩家做义父母 → ParentBack
                {
                    if (rel.IsRelation(player, UnitRelationType.ParentBack))
                        return Fail("你已是" + target + "的义父母");
                    return StartRelationAction(wub, player, UnitRelationType.ParentBack, op, askText);
                }
                case "jie_yi":       // 结义：NPC 与玩家结义 → BrotherBack（亲测 BrotherBack 单向写入不可靠，改双写兜底）
                {
                    if (rel.IsRelation(player, UnitRelationType.BrotherBack) || rel.IsRelation(player, UnitRelationType.Brother))
                        return Fail("你与" + target + "已是结义");
                    return StartRelationAction(wub, player, UnitRelationType.BrotherBack, op, askText);
                }
                case "jie_chu_jie_yi":
                {
                    if (!rel.IsRelation(player, UnitRelationType.BrotherBack) && !rel.IsRelation(player, UnitRelationType.Brother))
                        return Fail("你与" + target + "并非结义");
                    return StartBreakWith(wub, player, op, askText);
                }

                default:
                    return Fail("unsupported social_relation op: " + op);
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
        /// </summary>
        private JObject StartRelationAction(WorldUnitBase npc, WorldUnitBase player, UnitRelationType type, string op, string askOverride = null)
        {
            var item = RoleRelationItemOfType(type) ?? CreateDummyRelationItem(type);
            if (item == null)
            {
                try { npc.CreateAction(new UnitActionRelationSet(player, type, 60), true); return Ok(new JObject { ["op"] = op, ["target"] = (string)npc.data.unitData.propertyData.GetName(), ["note"] = $"已直写关系(UnitActionRelationSet/{type} 60)" }); }
                catch (Exception e2) { return Fail($"[{op}] 关系文案构建失败且直写失败: {e2.Message}"); }
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

            return Claimed(npc, () => ShowDramaService.ShowConfirm(
                ModIds.DramaSocialRelationBase,
                player, npc,
                $"{npcName}向{playerName}提出<{relName}>：\n{ask}",
                accept, reject,
                onOk: () =>
                {
                    try { npc.CreateAction(new UnitActionRelationSet(player, type, addClose), false); } catch (Exception e) { return Fail($"RelationSet 抛异常: {e.Message}"); }
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
                    return Ok(new JObject { ["op"] = op, ["target"] = npcName, ["relation"] = relName, ["verified"] = verified });
                },
                okTip: $"已与{npcName}缔结{relName}",
                noTip: $"玩家婉拒了缔结{relName}的请求"));
        }

        private static ConfRoleRelationItem CreateDummyRelationItem(UnitRelationType type)
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
        private JObject StartBreakWith(WorldUnitBase npc, WorldUnitBase target, string op, string askOverride = null)
        {
            string npcName = "", targetName = "";
            try { npcName = (string)npc.data.unitData.propertyData.GetName(); } catch { npcName = "对方"; }
            try { targetName = (string)target.data.unitData.propertyData.GetName(); } catch { targetName = "你"; }
            string relName = BreakRelName(op);
            string text = string.IsNullOrWhiteSpace(askOverride)
                ? $"{npcName}想与你解除{relName}关系，是否同意？"
                : $"{npcName}想与你解除{relName}关系：\n{askOverride.Trim()}";
            return Claimed(npc, () => ShowDramaService.ShowConfirm(
                ModIds.DramaSocialRelationBase,
                target, npc,
                text,
                "同意", "不同意",
                onOk: () =>
                {
                    // 第一段：BreakWith 发起（create_ret 语义见神识传音结婚实证：1=触发，0=创建被拒）
                    int created = -1, state = -1;
                    string bwErr = null;
                    try
                    {
                        var act = new UnitActionRoleBreakWith();
                        ((UnitActionBase)act).Init(target);
                        created = npc.CreateAction(act, false);
                        try { state = (int)((dynamic)act).state; } catch { }   // BreakWith 基类链未实锤含 state，dynamic 试探
                    }
                    catch (Exception e) { bwErr = e.Message; }

                    if (created == 1)
                        return Ok(new JObject
                        {
                            ["op"] = op, ["target"] = npcName, ["relation"] = relName,
                            ["create_ret"] = created, ["state_at_return"] = state,
                        });

                    // 第二段：BreakWith 未触发（创建被拒/异常）→ 字段直写兜底（marry 同款实证路径）
                    try { BreakFieldsDirect(op, npc, target); }
                    catch (Exception e) { return Fail($"[{op}] BreakWith 未触发(create_ret={created}{(bwErr != null ? "，" + bwErr : "")})且直写失败: " + e.Message); }

                    bool still = BreakStillRelated(op, npc, target);
                    return Ok(new JObject
                    {
                        ["op"] = op, ["target"] = npcName, ["relation"] = relName,
                        ["create_ret"] = created, ["state_at_return"] = state, ["bw_err"] = bwErr,
                        ["readback_still_related"] = still,
                    });
                },
                okTip: "已同意解除",
                noTip: $"玩家不同意解除{relName}"));
        }

        /// <summary>解除类字段直写（双向）。married 置空串的合法性依据：神识传音婚配前置 `married != null && GetUnit(married) != null`，空串即视为未婚。</summary>
        private static string BreakFieldsDirect(string op, WorldUnitBase npc, WorldUnitBase target)
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
        private static bool BreakStillRelated(string op, WorldUnitBase npc, WorldUnitBase target)
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
        private static string BreakRelName(string op)
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
        private static ConfRoleRelationItem RoleRelationItemOfType(UnitRelationType type)
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

        private JObject Movement(JObject args)
        {
            string op = (string)args["op"] ?? "";
            // 发起方恒为 harness 注入的 initiator（dialogue_agent._execute_tool_calls）。
            // 删掉那个旧版 `target` 兼容别名：schema 里从来没有 target、
            // 也没人写它 —— 留着只会让"actor 从哪来"这件事看起来有两个来源。
            string initiator = (string)args["initiator"] ?? "";
            var wub = UnitLookup.Resolve(initiator);
            if (wub == null)
                return Fail("未找到发起方 " + initiator);
            var player = g.world.playerUnit;
            if (player == null)
                return Fail("玩家单位不可用");
            string target = "";
            try { target = wub.data.unitData.propertyData.GetName(); } catch { target = initiator; }

            // 神识传音 IL 实证（closures_il b__25_68 召唤 / b__25_70 传送）：
            //   召唤 = 被召唤者(玩家)移动到召唤者(NPC)位置 → player.CreateAction(UnitActionMovePlayer(NPCPoint), true)
            //   传送 = 传送者(NPC)移动到目标位置(玩家) → NPC.CreateAction(UnitActionMoveNPC(playerPoint), true)
            switch (op)
            {
                case "summon": // 召唤玩家到NPC位置
                    if (SameGrid(wub, player))
                        return Ok(new JObject { ["op"] = op, ["target"] = target });
                    player.CreateAction(new UnitActionMovePlayer(wub.data.unitData.GetPoint()), true);
                    return Ok(new JObject { ["op"] = op, ["target"] = target, ["moved"] = true });

                case "teleport": // NPC传送至玩家处
                    if (SameGrid(wub, player))
                        return Ok(new JObject { ["op"] = op, ["target"] = target });
                    wub.CreateAction(new UnitActionMoveNPC(player.data.unitData.GetPoint()), true);
                    return Ok(new JObject { ["op"] = op, ["target"] = target, ["moved"] = true });

                case "travel": // NPC 自主前往指定城镇/宗门（UnitActionMoveNPC 接受任意坐标，types42 实锤），可在当地等候/邀约碰面
                {
                    string dest = (string)args["destination"] ?? "";
                    string region = (string)args["region"] ?? "";
                    if (string.IsNullOrEmpty(dest))
                        return Fail("缺少 destination（城镇/宗门名，可先 query_world topic=places 查看可去地点）");
                    var b = ResolveBuild(dest, region);
                    if (b == null)
                        return Fail($"未找到地点「{dest}」，可先 query_world topic=places 查看可去的城镇/宗门名" + (string.IsNullOrEmpty(region) ? "" : $"（或确认 region={region} 拼写）"));
                    string where = b.Name + (string.IsNullOrEmpty(b.Region) ? "" : $"（{b.Region}）");
                    int curX = 0, curY = 0;
                    try { curX = wub.data.unitData.pointX; curY = wub.data.unitData.pointY; } catch { }
                    if (curX == b.X && curY == b.Y)
                        return Ok(new JObject { ["op"] = op, ["target"] = target, ["destination"] = b.Name, ["region"] = b.Region, ["already_there"] = true });
                    wub.CreateAction(new UnitActionMoveNPC(new UnityEngine.Vector2Int(b.X, b.Y)), true);
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

        private JObject WorldAiAction(JObject args)
        {
            // 注：对话窗已迁移到游戏 UI 管理器体系，层级由游戏自动排——
            // 这里唤起的确认/论道等弹窗后开自然盖住对话窗，无需再手动让位
            string op = (string)args["op"] ?? "";
            // 发起方恒为 harness 注入的 initiator（dialogue_agent._execute_tool_calls）。
            // 删掉那个旧版 `target` 兼容别名：schema 里从来没有 target、
            // 也没人写它 —— 留着只会让"actor 从哪来"这件事看起来有两个来源。
            string initiator = (string)args["initiator"] ?? "";
            var wub = UnitLookup.Resolve(initiator);
            if (wub == null)
                return Fail("未找到发起方 " + initiator);
            var player = g.world.playerUnit;
            string target = "";
            try { target = wub.data.unitData.propertyData.GetName(); } catch { target = initiator; }

            switch (op)
            {
                case "spar":   // 切磋：UnitActionRoleDrill（types36:38182/46297 “双方发起了切磋!”）
                {
                    // ：由「发了就回」改为「等玩家在原生剧情窗里选完」
                    // 原生 21204 剧情窗弹「好，就让我和你切磋一下 | 我现在没有空」两条分支，
                    // 旧实现 `CreateAction` 完立刻 Ok ⇒ 玩家**婉拒了**，Python 侧那句写死的
                    // 「已向你发起切磋，即将进入战斗/切磋界面」照发，NPC 于是继续按"双方已开始切磋"演
                    // （用户报的"都是一样的，不太合适"）。
                    // 判据取**选项本身**（`DrillChoiceGate` 盯 `UIDramaBase.ClickOption`），不取动作结束后的
                    // `isDrillComplete` —— 后者语义没有真机样本（同类字段 isInviteComplete 曾把"接受"误判成
                    // "拒绝"）；且用户拍板"选项一落定就回，不等战斗"，而 OnEnd 在同意路径要等整场打完。
                    if (!SameGrid(wub, player)) return Fail("切磋需同格，当前异地");
                    var drill = new UnitActionRoleDrill(player);
                    try { ((UnitActionRoleToUnitBase)(object)drill).isAutoAddLog = true; } catch { }
                    string drillUid = "";
                    try { drillUid = DramaNativeClaim.UidOf(wub); } catch { }
                    // BuildResult = **OnEnd 兜底**路径专用（选项点击没接住才会跑到：玩家按 ESC/点窗外关窗）。
                    // 刻意不读 drill.isDrillComplete —— 语义未校准就不猜，只如实报"没有明确答复"。
                    // 正常路径由 DrillChoiceGate 在选项落定那一刻直接 Resolve，并先 Remove 掉这条登记，
                    // 所以这份委托在正常路径上根本不会执行。
                    var drillSlot = DeferNativeUnitAction(drill, "spar", drillUid, () =>
                        Ok(new JObject { ["op"] = "spar", ["target"] = target, ["answered"] = false }),
                        out var drillBusyFail);
                    if (drillBusyFail != null) return drillBusyFail;
                    if (drillSlot != null) DrillChoiceGate.Arm(drillSlot.Value, drill.Pointer, target);
                    try { wub.CreateAction(drill, true); }
                    catch (Exception e)
                    {
                        try { UnitActionPending.Remove(drill.Pointer); } catch { }
                        if (drillSlot != null) { DrillChoiceGate.Disarm(drillSlot.Value); DramaGate.Cancel(drillSlot.Value); }
                        return Fail("发起切磋失败: " + e.Message);
                    }
                    try { UITipItem.AddTip($"已向{target}发起切磋", 5f); } catch { }
                    if (drillSlot != null) return DramaGate.MakePendingMarker(drillSlot.Value);
                    return Ok(new JObject { ["op"] = op, ["target"] = target, ["pending"] = true });
                }

                case "attack": // 攻击：触发战斗 UI（同格）；战斗结束后游戏自然结算是否杀死，无需单独 kill
                    if (!SameGrid(wub, player)) return Fail("攻击需同格，当前异地");
                    wub.CreateAction(new UnitActionRoleAttack(player), true);
                    return Ok(new JObject { ["op"] = op, ["target"] = target });

                case "shuang_xiu": // 双修：需同格（已移除C端好感校验）
                    if (!SameGrid(wub, player)) return Fail("双修需同格，当前异地");
                    // 完成挂起：神识传音 case17 实证 1031 挂 Action<bool>，p==true 即"双修完成"
                    return StartToUnitAiActionDeferred(new WorldUnitAIAction1031 { toUnit = player },
                        wub, op, target);

                case "lun_dao":   // 论道：言语往来，可远程（神识传音 IL 实证 = 1037）
                    // 完成挂起：工具结果等玩家在原生论道界面操作完（30s~1min）才回给 LLM——
                    // 消除"UI 刚弹出、玩家还没选，NPC 就已经回话"的时序错位。
                    // 神识传音 case22 实证 1037 挂 Action<bool>，p==true 即"论道完成"。
                    return StartToUnitAiActionDeferred(new WorldUnitAIAction1037 { toUnit = player },
                        wub, op, target);

                case "yao_yue":   // 邀约：必弹原版邀约剧情（UnitActionRoleInvite）
                {
                    // 已移除C端好感校验
                    // 完成挂起：原版邀约剧情玩家可选"同意/拒绝"（Invite.NPCToPlayerAction 带 3 个
                    // 选择回调 + forceAgree 旁路字段反证默认必弹选择），工具等选择后才返回。
                    var invite = new UnitActionRoleInvite(player);
                    // 武装剧情文本捕获：邀约接受后弹的第二层剧情里，{0} = 约定地点（真机实证）。
                    // 必须在 CreateAction 之前武装——第一层 81002 是随 CreateAction 同步弹出的。
                    DramaTextCapture.BeginInvite(wub);
                    var slot = DeferNativeUnitAction(invite, "yao_yue", DramaNativeClaim.UidOf(wub), () =>
                    {
                        bool accepted = false, npcUpset = false;
                        try { accepted = invite.isInviteComplete; } catch { }
                        try { npcUpset = invite.isNPCReduceIntim; } catch { }
                        try { UnityEngine.Debug.Log("[UnitAction] yao_yue isInviteComplete=" + accepted + " isNPCReduceIntim=" + npcUpset); } catch { }
                        // 判定实测校准（两路径真机样本）：接受=(isInviteComplete=True, isNPCReduceIntim=True)、
                        // 拒绝=(False, True) → isInviteComplete 是唯一判据。isNPCReduceIntim 两条路径都为 true
                        //（更像"本次邀约挂了好感惩罚条件"——接受后 NPC 等三个月、爽约才扣，台词"勿要让我久候"佐证），
                        // 不作判据、仅随 data 透出。旧判定 `!accepted || npcUpset` 把接受路径误报成拒绝（用户踩中）。
                        // 取走捕获（收枪）：只在"接受"时透出原句——拒绝路径没有第二层剧情，
                        // 末条会停在第一层的邀请语，带上反而误导模型。
                        // 原句=第二层剧情的成品句（「我先前在新达镇附近发现了一处幽静之地…」），
                        // 地点就在句子里；抓不到就不写该字段（契约：字段缺失 ≠ 否定）。
                        string inviteText;
                        try { DramaTextCapture.TryTakeInvite(out inviteText); }
                        catch { inviteText = null; }
                        // `@` 标记解成人话（与 DramaAiOption.ExtractText 同规约）：
                        // 成品句里可能带 `@q_<unitID>|<hash>@`（人名）或 `@w_<hash>|…|<道具ID>|<数量>|@`
                        // （道具），直接透出模型读到的是 hash 乱码。解不出来会退回字段0 并留痕。
                        try { inviteText = UnitSnapshot.CleanLogText(inviteText); } catch { }
                        var jo = new JObject { ["op"] = "yao_yue", ["target"] = target, ["accepted"] = accepted, ["upset"] = npcUpset };
                        if (accepted && !string.IsNullOrEmpty(inviteText)) jo["invite_text"] = inviteText;
                        return Ok(jo);
                    }, out var busyFail);
                    if (busyFail != null) { DramaTextCapture.Abort(); return busyFail; }
                    try { wub.CreateAction(invite, true); }
                    catch (Exception e)
                    {
                        DramaTextCapture.Abort();
                        if (slot != null) { UnitActionPending.Remove(invite.Pointer); DramaGate.Cancel(slot.Value); }
                        return Fail("发起邀约失败: " + e.Message);
                    }
                    try { UITipItem.AddTip($"已向{target}发起邀约", 5f); } catch { }
                    if (slot != null) return DramaGate.MakePendingMarker(slot.Value);
                    return Ok(new JObject { ["op"] = op, ["target"] = target, ["pending"] = true });
                }

                case "chuan_gong": // 传授功法：需同格；skill 可选——省略时交游戏原生功法面板由玩家自选（09-11 定案）
                    // 落点 UnitActionRoleTeachSkill（原版 case31 反编实锤，types36_fulldll L47009）：
                    //   发起方.CreateAction(new UnitActionRoleTeachSkill(接收方, skill, TeachType.Teach))，
                    //   Teach=0 传授 / Study=1 学习（GGBH_API.TeachType 枚举实证）；校验用官方 SkillCanTeach。
                {
                    string skill = (string)args["skill"] ?? "";
                    if (!SameGrid(wub, player)) return Fail("传功需同格，当前异地");

                    DataUnit.ActionMartialData skillData = null;
                    string seedName = "";
                    if (!string.IsNullOrEmpty(skill))
                    {
                        skillData = ResolveMartial(wub, skill);
                        if (skillData == null)
                            return Fail($"未找到功法「{skill}」，请用功法 ID（inspect_unit abilities 里的 id）或装备中的功法名");
                        if (!UnitActionRoleTeachSkill.SkillCanTeach(wub, skillData))
                            return Fail($"功法「{skill}」不可传授（官方 SkillCanTeach 校验未过）");
                        seedName = skill;
                    }
                    else
                    {
                        // 玩家决定：不给 skill 时仍要一本作为挂载（构造必填），从 NPC 自己功法槽里
                        // 取一本可传的作 seed；游戏原生功法面板照常弹出，由玩家自选实际所学（送 gainSkill）。
                        try { skillData = FirstTeachableSkill(wub); } catch { }
                        if (skillData == null)
                            return Fail("对方暂无已学功法可传");
                        try { string nm = MartialDisplayName(skillData); if (!string.IsNullOrEmpty(nm)) seedName = nm; } catch { }
                    }

                    // 玩家为接收方（toUnit=player），NPC 发起（wub.CreateAction）——与原版 case31 语义一致。
                    // 完成挂起：原生询问剧情（TeachSkill.PlayAskDrama + ConfDramaOptionsItem 选项回调
                    // 反编实锤）玩家自选功法/接受拒绝；接受了才有 gainSkill——实际所学以面板选择为准。
                    var teach = new UnitActionRoleTeachSkill(player, skillData, UnitActionRoleTeachSkill.TeachType.Teach);
                    var slot = DeferNativeUnitAction(teach, "chuan_gong", DramaNativeClaim.UidOf(wub), () =>
                    {
                        bool hasSkill = false;
                        try { hasSkill = teach.gainSkill != null; } catch { }
                        // 实际所学以玩家面板选择为准：优先回读 gainSkill 的中文名，回退 seed。
                        // 校准点：gainSkill 是否=玩家所选（而非构造种子）尚无真机样本——
                        // 诊断行把 seed / teachSkill(构造种子解码) / gainSkill(所学解码) 全部打出，
                        // 一次真实传功（选一本≠种子的功法）即可定案（gainSkillName≠seed 即成立）。
                        string gainedName = "";
                        try { gainedName = MartialDisplayName(teach.gainSkill); } catch { }
                        string teachSkillName = "";
                        try { teachSkillName = MartialDisplayName(teach.teachSkill); } catch { }
                        try { UnityEngine.Debug.Log("[UnitAction] chuan_gong seed=" + seedName
                            + " teachSkill=" + teachSkillName
                            + " gainSkillName=" + (hasSkill ? gainedName : "(null)")); } catch { }
                        bool byGainSkill = hasSkill && !string.IsNullOrEmpty(gainedName);
                        string gained = byGainSkill ? gainedName : seedName;
                        string source = byGainSkill ? "gainSkill" : "seed";
                        if (!hasSkill)
                            return Ok(new JObject { ["op"] = "chuan_gong", ["target"] = target, ["skill"] = gained, ["accepted"] = false, ["skill_source"] = source });
                        return Ok(new JObject { ["op"] = "chuan_gong", ["target"] = target, ["skill"] = gained, ["accepted"] = true, ["skill_source"] = source });
                    }, out var busyFail);
                    if (busyFail != null) return busyFail;
                    try { wub.CreateAction(teach, true); }
                    catch (Exception e)
                    {
                        if (slot != null) { UnitActionPending.Remove(teach.Pointer); DramaGate.Cancel(slot.Value); }
                        return Fail("发起传功失败: " + e.Message);
                    }
                    if (slot != null) return DramaGate.MakePendingMarker(slot.Value);
                    // AutoConfirm（verify 工程）旧立即路径：
                    return Ok(new JObject { ["op"] = op, ["target"] = target, ["skill"] = seedName, ["pending"] = true });
                }

                default:
                    return Fail("unsupported world_ai_action op: " + op);
            }
        }

        private JObject EconomyItem(JObject args)
        {
            // 发起方恒为 harness 注入的 initiator（dialogue_agent._execute_tool_calls）。
            // 删掉那个旧版 `target` 兼容别名：schema 里从来没有 target、
            // 也没人写它 —— 留着只会让"actor 从哪来"这件事看起来有两个来源。
            string initiator = (string)args["initiator"] ?? "";
            var wub = UnitLookup.Resolve(initiator);
            if (wub == null)
                return Fail("未找到发起方 " + initiator);
            var player = g.world.playerUnit;
            string target = "";
            try { target = player.data.unitData.propertyData.GetName(); } catch { target = "玩家"; }
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
            if (hasProps && !ShowDramaService.AutoConfirm && !DramaGate.TryDefer(ModIds.AiActionGate, out giveSlot))
                return Fail("已有一个 AI 行动在等待玩家响应（剧情/论道界面未关闭），请先完成当前交互再赠送");

            // 入口留痕：这条路径此前**除结果外一条日志都没有** —— 真机上"没弹窗"时
            // 无法区分「没走到这里 / 占不到坑 / 动作建不起来 / 动作建了但游戏没播剧情」。
            // 配合下面的 `CreateAction 已返回` 与 `[Give] 预检`，四段一次分开。
            try
            {
                ModMain.P("[Give] 进入：目标=" + target + " 项数=" + items.Count
                          + " 含道具=" + hasProps + " 槽位=" + giveSlot
                          + " 自动确认=" + ShowDramaService.AutoConfirm + " 同格=" + SameGrid(wub, player));
            }
            catch { }

            // —— 纯灵石：自制确认窗路径（玩家"收下/拒收"后才转账；混合场景灵石维持直写） ——
            if (!hasProps) return GiveLingshiOnly(wub, player, items, via, target);

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
                int count = SafeInt(it["count"], 1);
                if (count < 1) count = 1;

                // —— 灵石（混合场景）：货币直写即时转账（道具窗结局文案注明"另灵石×N已即时赠达"）
                if (itemName.Contains("灵石"))
                {
                    string terr;
                    int actualMoney = TransferLingshi(wub, player, count, out terr);
                    if (actualMoney <= 0)
                    {
                        results.Add(new JObject { ["item"] = itemName, ["requested"] = count, ["actual"] = 0, ["error"] = terr });
                        continue;
                    }
                    totalMoney += actualMoney;
                    anyGave = true;
                    results.Add(new JObject { ["item"] = itemName, ["requested"] = count, ["actual"] = actualMoney, ["mode"] = "money", ["from"] = "npc->player" });
                    continue;
                }

                // —— 普通道具：从发起方 NPC 背包按中文名拆栈（定死 NPC→玩家） ——
                string takeErr = null;
                int took = 0;
                try { took = TakePropsFromBag(wub, player, itemName, count, giveList, out takeErr); }
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

                // 建动作前的预检（真机事故）
                //   症状：道具赠送 `CreateAction` **不抛异常**，但动作没跑起来、原生窗也没弹 ——
                //   工具挂起 120s 被 Python 判"请求超时"，而道具**已经被 `TakePropsFromBag` 拆出背包**，
                //   只被这个没跑起来的动作引用着 → **直接丢**。第二次再试就报"所有赠送项均未达成"
                //   （背包里确实已经没有了），模型于是以为是自己记错了。
                //   判据：`UnitActionRoleGive.IsCreate(bool isTip)` 是游戏自己的"能不能创建"判定
                //   （返回 Int32，非 0 = 不能创建；`UnitActionRoleGive : UnitActionRoleToUnitBase`，
                //   同族动作 Marry/TeachSkill 同款签名）。这里先问一次：
                //     · 非 0 → **快速失败 + 原样回滚**，绝不挂起等一个永远不会来的窗口；
                //     · 0    → 照原样 CreateAction（说明卡点在别处，日志里的字段快照是下一轮判据）。
                //   `isTip` 传 false：提示留给游戏内部那次调用，避免双重弹提示。
                //   同时把 `isCheckUnitProps` 打出来 —— 若它为 true 且我方已把道具拆走，
                //   "校验发起方是否仍持有道具"就是本事故的头号嫌疑。
                int createCode = -1;
                bool codeOk = false;
                try { createCode = giveAction.IsCreate(false); codeOk = true; }
                catch (Exception e) { ModMain.P("[Give] IsCreate 预检抛异常: " + e.Message); }
                try
                {
                    int offered = -1;
                    try { offered = giveAction.giveProps != null ? giveAction.giveProps.Count : -1; } catch { }
                    ModMain.P("[Give] 预检 拆出道具数=" + giveList.Count + " giveProps=" + offered
                              + " isCheckUnitProps=" + giveAction.isCheckUnitProps
                              + " IsCreate=" + (codeOk ? createCode.ToString() : "抛异常")
                              + " 同格=" + SameGrid(wub, player));
                }
                catch { }
                if (codeOk && createCode != 0)
                {
                    if (giveSlot >= 0) DramaGate.Cancel(giveSlot);
                    RollbackGive(wub, giveList);
                    return Fail("赠送动作无法创建（游戏 IsCreate=" + createCode
                                + "），道具已原样放回背包；换一件或换个时机再试",
                                new JObject { ["items"] = results, ["is_create"] = createCode });
                }

                if (giveSlot >= 0)
                {
                    // 完成挂起：结果由 Harmony postfix 在原生 OnEnd() 后读 receive/refuseProps 落定字段
                    UnitActionPending.Register(giveAction.Pointer, giveSlot, "give_item", DramaNativeClaim.UidOf(wub), () =>
                    {
                        int recv = -1, refused = -1;
                        try { recv = giveAction.receiveProps != null ? giveAction.receiveProps.Count : -1; } catch { }
                        try { refused = giveAction.refuseProps != null ? giveAction.refuseProps.Count : -1; } catch { }
                        try { UnityEngine.Debug.Log("[UnitAction] give_item receiveProps=" + recv + " refuseProps=" + refused + " slot=" + giveSlot); } catch { }
                        bool accepted = refused <= 0;
                        return Ok(new JObject { ["op"] = "give_item", ["target"] = target, ["items"] = results, ["via"] = via, ["accepted"] = accepted, ["accepted_count"] = Math.Max(recv, 0), ["refused_count"] = Math.Max(refused, 0), ["same_grid"] = SameGrid(wub, player) });
                    });
                }
                try { wub.CreateAction(giveAction, true); }
                catch (Exception e)
                {
                    if (giveSlot >= 0) { UnitActionPending.Remove(giveAction.Pointer); DramaGate.Cancel(giveSlot); }
                    RollbackGive(wub, giveList); return Fail("发起赠送失败，已回滚背包: " + e.Message, new JObject { ["items"] = results });
                }
                // 留痕：CreateAction **没抛异常 ≠ 动作跑起来了**。游戏没播剧情时就是这一行之后
                // 什么都不发生（真机：这里之后再无日志，120s 后 Python 判超时）。
                try { ModMain.P("[Give] CreateAction 已返回（未抛异常），等原生窗…"); } catch { }
                if (giveSlot >= 0) return DramaGate.MakePendingMarker(giveSlot);   // 挂起：等玩家"收下/拒绝"后按结局收口
            }
            // AutoConfirm（verify 工程）旧立即路径：直写已完成，返回纯数据
            return Ok(new JObject { ["op"] = "give_item", ["target"] = target, ["items"] = results, ["via"] = via, ["accepted"] = true, ["same_grid"] = SameGrid(wub, player) });
        }

        /// <summary>
        /// 纯灵石赠送（items 全为灵石项）：弹自制确认窗（ShowConfirm/UICustomDramaDyn，与结缘/结义
        /// 同款机制），玩家点「我收下了」后才执行货币直写转账——2026-09-09 前为无 UI 立即转账。
        /// 占坑走 economy_item 专属段 DramaEconomyItemBase（Mod+100，登记预留首用，不与新工具撞段）；
        /// verify 工程 AutoConfirm 直通转账（结果形态与旧版一致）。弹窗前预检余额（全不足直接
        /// Fail 不弹窗）；点选后按当时余额重新结算（min 兜底+补偿回滚）。
        /// </summary>
        private JObject GiveLingshiOnly(WorldUnitBase npc, WorldUnitBase player, JArray items, string via, string target)
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
                    int count = SafeInt(it["count"], 1); if (count < 1) count = 1;
                    int have = ReadLingshiHeld(npc);
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
                    : (SameGrid(npc, player) ? "当面赠达" : "异地以传讯方式赠达");

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
                        int count = SafeInt(it["count"], 1); if (count < 1) count = 1;
                        string terr;
                        int actual = TransferLingshi(npc, player, count, out terr);
                        if (actual <= 0) { results.Add(new JObject { ["item"] = itemName, ["requested"] = count, ["actual"] = 0, ["error"] = terr }); continue; }
                        got += actual; any = true;
                        results.Add(new JObject { ["item"] = itemName, ["requested"] = count, ["actual"] = actual, ["mode"] = "money", ["from"] = "npc->player" });
                    }
                    if (!any) return Fail("所有赠送项均未达成（详见 items.error）", new JObject { ["items"] = results });
                    return Ok(new JObject { ["op"] = "give_item", ["target"] = target, ["items"] = results, ["via"] = via, ["accepted"] = true, ["same_grid"] = SameGrid(npc, player) });
                }

                // 正常路径：自制确认窗（正文按预检到的实际可赠数描述；点选后按当时余额重新结算）
                string ask = $"{npcName}愿赠你灵石×{totalActual}（{viaNote}），是否收下？";
                return Claimed(npc, () => ShowDramaService.ShowConfirm(
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
                            int count = SafeInt(it["count"], 1); if (count < 1) count = 1;
                            string terr;
                            int actual = TransferLingshi(npc, player, count, out terr);
                            if (actual <= 0) { results.Add(new JObject { ["item"] = itemName, ["requested"] = count, ["actual"] = 0, ["error"] = terr }); continue; }
                            got += actual; any = true;
                            results.Add(new JObject { ["item"] = itemName, ["requested"] = count, ["actual"] = actual, ["mode"] = "money", ["from"] = "npc->player" });
                        }
                        if (!any) return Fail("转账时发起方灵石已不足", new JObject { ["items"] = results });
                        try { UITipItem.AddTip("已收下灵石×" + got, 2f); } catch { }
                        return Ok(new JObject { ["op"] = "give_item", ["target"] = target, ["items"] = results, ["via"] = via, ["accepted"] = true, ["same_grid"] = SameGrid(npc, player) });
                    },
                    okTip: null,
                    noTip: "玩家拒收了灵石"));
            }
            catch (Exception e)
            {
                return Fail("灵石赠送异常: " + e.Message);
            }
        }

        /// <summary>
        /// 读单位**随身**灵石持有数（道具 ID=10001，`DataProps.GetPropsNum`）。
        /// **读不到返回 -1**（调用方必须当"未知"处理，绝不能当成 0）。
        ///
        /// 删掉 `totalSchoolMoney` 兜底（用户拍板"链路确定了就别再兜底"）：
        /// 那是**宗门钱**，不是随身灵石 —— 一个**语义不同的账户**。旧行为在读取失败时
        /// 把一个貌似合理的数字交给模型，模型据此报价/赠送/判断够不够买，实际看的是另一个钱袋，
        /// 而且**没有任何信号**。宁可显式说"读不到"，也不要编一个数。
        /// </summary>
        private static int ReadLingshiHeld(WorldUnitBase u)
        {
            try { return ((DataProps)u.data.unitData.propData).GetPropsNum(10001); }
            catch { return -1; }
        }

        /// <summary>
        /// NPC→玩家 灵石转账（货币字段直写：CostPropItem(10001) 发起方扣 / RewardPropMoney 玩家加）。
        /// 带补偿回滚：NPC 已扣、玩家未收 → 原样加回发起方，防吞币。
        /// 返回实际到账数（≤0 时 err 带失败原因）。混合路径循环与纯灵石确认窗 onOk 共用。
        /// </summary>
        private static int TransferLingshi(WorldUnitBase from, WorldUnitBase player, int count, out string err)
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
        private static int TakePropsFromBag(WorldUnitBase wub, WorldUnitBase player, string itemName, int count,
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
            return actual;
        }

        /// <summary>把已拆出但最终没送成的道具原样加回背包（赠送取消/异常时的兜底，防吞道具）。</summary>
        private static void RollbackGive(WorldUnitBase wub, Il2CppSystem.Collections.Generic.List<DataProps.PropsData> giveList)
        {
            try
            {
                var dp = wub.data.unitData.propData as DataProps;
                if (dp == null) return;
                for (int i = 0; i < giveList.Count; i++)
                {
                    try { dp.AddProps(giveList[i]); } catch { }
                }
                giveList.Clear();
            }
            catch { }
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
        /// 三段式：**预校验（全只读）→ 弹窗 → 带补偿落刀**。
        ///   · 预校验不过就 Fail 且**不弹窗** —— 避免"弹了窗、玩家点了成交、才发现做不了"。
        ///   · 数量必须**整笔满足**（与 economy_item 赠送的"有多少送多少"不同）：买卖是定价交易，
        ///     少给货却收全款不成立；只想要一部分就让模型改小 count 重发。
        ///   · 落刀顺序刻意选成「拆栈 → 灵石 → 入包」：前两步失败时道具都还在手上，
        ///     `RollbackGive` 能无损退回卖方；第三步失败时钱和货的补偿都还是对称可做的。
        ///     反序（先入包）则失败时得从买方"抠回来"，栈可能已被合并、口径不稳。
        /// </summary>
        private JObject Trade(JObject args)
        {
            string sellerId = (string)args["seller"] ?? "";
            string buyerId = (string)args["buyer"] ?? "";
            string itemName = (string)args["item"] ?? "";   // schema 只有 item（旧 item_name 别名 09-13 删）
            int count = SafeInt(args["count"], 1);
            int price = SafeInt(args["price"], 0);
            if (count < 1) count = 1;
            if (price < 0) price = 0;

            if (string.IsNullOrEmpty(sellerId) || string.IsNullOrEmpty(buyerId))
                return Fail("trade 需要 seller（卖方，出货方）与 buyer（买方，付灵石方）");
            if (string.IsNullOrEmpty(itemName))
                return Fail("trade 需要 item（道具中文名，取自 inspect_unit(classes=inventory) 的 props）");
            if (itemName.Contains("灵石"))
                return Fail("灵石是货币不是商品：item 只能填背包道具。单纯给灵石请用 economy_item");

            var seller = UnitLookup.Resolve(sellerId);
            if (seller == null) return Fail("未找到卖方 " + sellerId);
            var buyer = UnitLookup.Resolve(buyerId);
            if (buyer == null) return Fail("未找到买方 " + buyerId);

            string sellerUid = "", buyerUid = "";
            try { sellerUid = DramaNativeClaim.UidOf(seller); } catch { }
            try { buyerUid = DramaNativeClaim.UidOf(buyer); } catch { }
            if (!string.IsNullOrEmpty(sellerUid) && sellerUid == buyerUid)
                return Fail($"卖方与买方是同一人（{sellerId}），无法与自己交易");

            string sellerName = NameOfUnit(seller, sellerId), buyerName = NameOfUnit(buyer, buyerId);

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

            int buyerMoneyBefore = ReadLingshiHeld(buyer);
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

            int sellerMoneyBefore = ReadLingshiHeld(seller);
            int buyerItemBefore = CountOf(buyerDp, propsID);
            string deal = price > 0 ? $"作价 {price} 灵石" : "不取分文";

            // 「AI 应对」原生选项抑制挂在非玩家一方（NPC↔NPC 交易时挂卖方）
            var anchor = IsPlayerUnit(seller) ? buyer : seller;

            // 立绘位约定（ShowDramaService 文档钉死）：`left` = 屏幕左位 = **玩家**，
            // `right` = 屏幕右位 = **对方**。全工程另外 4 处确认窗（建关系/解除/纯灵石/自主互动）
            // 一律传 `(player, npc)`。
            // 事故 本方法初版直接传 `(seller, buyer)` —— 卖家是 NPC、买家是玩家时右位落成
            //   玩家，玩家实机看到**两个自己的立绘与名字**；同一坑 09-12 在自主互动窗已踩过一次
            //   （`NpcInitiativeMonitor.cs` 有同款修正注释）。NPC↔NPC 交易没有玩家可放，
            //   退化成「卖方左 / 买方右」。
            WorldUnitBase dLeft, dRight;
            if (IsPlayerUnit(buyer)) { dLeft = buyer; dRight = seller; }   // 玩家买 → 玩家在左、卖方在右
            else { dLeft = seller; dRight = buyer; }                       // 玩家卖 / 双方皆非玩家

            return Claimed(anchor, () => ShowDramaService.ShowConfirm(
                ModIds.DramaEconomyItemBase,
                dLeft, dRight,
                $"{sellerName}愿将「{itemName}」×{count} 让与{buyerName}，{deal}。",
                "成交", "不成交",
                onOk: () =>
                {
                    // ① 卖方拆栈：复用给赠那条已验过的两段式（先规划后落刀，中途失败自带回滚）
                    var takenList = new Il2CppSystem.Collections.Generic.List<DataProps.PropsData>();
                    string takeErr = null;
                    int got = 0;
                    try { got = TakePropsFromBag(seller, buyer, itemName, count, takenList, out takeErr); }
                    catch (Exception e) { takeErr = e.Message; }
                    if (got <= 0)
                        return Fail($"卖方 {sellerName} 取不出「{itemName}」：{takeErr ?? "背包中没有"}");
                    if (got < count)   // 竞态：预校验后背包变了 → 整笔作废，不留半截交易
                    {
                        RollbackGive(seller, takenList);
                        return Fail($"卖方 {sellerName} 只有「{itemName}」×{got}，不够 ×{count}，交易作废（道具未动）");
                    }

                    // ② 灵石结算（TransferLingshi 自带"扣了没到账就退回买方"的补偿）
                    int paid = 0;
                    if (price > 0)
                    {
                        string terr;
                        try { paid = TransferLingshi(buyer, seller, price, out terr); }
                        catch (Exception e) { paid = 0; terr = e.Message; }
                        if (paid != price)
                        {
                            RollbackGive(seller, takenList);   // 道具还在手上 → 无损退回
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
                        RollbackGive(seller, takenList);
                        if (paid > 0) { string _; try { TransferLingshi(seller, buyer, paid, out _); } catch { } }
                        return Fail(undoWhy + "（已回滚：道具退回卖方、灵石退回买方）");
                    }

                    var okObj = new JObject
                    {
                        ["op"] = "trade",
                        ["seller"] = sellerName, ["buyer"] = buyerName,
                        ["item"] = itemName, ["count"] = count, ["price"] = paid,
                        ["item_moved"] = true,
                        ["buyer_item_before"] = buyerItemBefore, ["buyer_item_after"] = buyerItemAfter,
                        ["same_grid"] = SameGrid(seller, buyer),
                    };
                    // 余额变化：**读得到才写**。ReadLingshiHeld 失败返回 -1 —— 绝不能把 -1 当余额报给模型
                    // （渲染层对缺失字段是容忍的；报 -1 却会被当成真实数字读出来）
                    int sellerAfter = ReadLingshiHeld(seller), buyerAfter = ReadLingshiHeld(buyer);
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
                    return Ok(okObj);
                },
                okTip: price > 0
                    ? $"已成交：「{itemName}」×{count} → {buyerName}，{price} 灵石 → {sellerName}"
                    : $"已赠予：「{itemName}」×{count} → {buyerName}",
                noTip: "玩家叫停了这笔交易"));
        }

        /// <summary>背包里按中文名找**存量最大**的那一栈（原始 propsItem.name 与 GameTool.LS 后两种写法都比对，
        /// 与 `TakePropsFromBag` 同口径）。返回 soleID（找不到返回 null），并给出该道具总存量/道具 ID。</summary>
        private static string FindStackSoleID(DataProps dp, string itemName, out int total, out int propsID)
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

        private static int CountOf(DataProps dp, int propsID)
        {
            try { return dp.GetPropsNum(propsID); } catch { return -1; }
        }

        private static string NameOfUnit(WorldUnitBase u, string fallback)
        {
            try { return u.data.unitData.propertyData.GetName(); } catch { return fallback; }
        }

        private static bool IsPlayerUnit(WorldUnitBase u)
        {
            try { return u != null && g.world.playerUnit != null && u.Pointer == g.world.playerUnit.Pointer; }
            catch { return false; }
        }

        /// <summary>item_acquire：NPC 从玩家身上偷窃/讨要道具（定死 NPC→玩家）。</summary>
        private JObject ItemAcquire(JObject args)
        {
            string op = (string)args["op"] ?? "";
            // 发起方恒为 harness 注入的 initiator（dialogue_agent._execute_tool_calls）。
            // 删掉那个旧版 `target` 兼容别名：schema 里从来没有 target、
            // 也没人写它 —— 留着只会让"actor 从哪来"这件事看起来有两个来源。
            string initiator = (string)args["initiator"] ?? "";
            var wub = UnitLookup.Resolve(initiator);
            if (wub == null)
                return Fail("未找到发起方 " + initiator);
            string target = "";
            try { target = wub.data.unitData.propertyData.GetName(); } catch { target = initiator; }
            string itemName = (string)args["item_name"] ?? "";
            int count = SafeInt(args["count"], 1);
            if (count < 1) count = 1;
            var player = g.world.playerUnit;
            if (player == null)
                return Fail("玩家单位不可用");
            if (string.IsNullOrEmpty(itemName))
                return Fail("缺少 item_name（道具中文名），可先 inspect_unit(classes=inventory)");
            // 灵石是货币（GetPropsNum 10001），不是道具实例，而官方 StealItem/Askfor 只接受 propsSoleID；
            // 早前用灵石道具 ID「10001」占位曾在真机触发 NRE（10001 并非有效 soleID），故这里直接拒绝。
            if (itemName.Contains("灵石"))
                return Fail($"「{itemName}」是货币不是道具，无法偷窃/讨要（原生接口只接受背包道具实例的 soleID）；灵石往来请改用 economy_item",
                            new JObject { ["item_name"] = itemName, ["requested"] = count });

            // 普通道具：按中文名挑栈。ask_for 按 count 挑（精确→最小够装→首个）；
            // steal_item 无数量概念（产品决策 ：删 count 语义，整栈偷走），want=0 即取首个同名栈
            int want = op == "ask_for" ? count : 0;
            var stack = FindPropsStack(player, itemName, want);
            if (stack == null)
                return Fail($"玩家背包中没有「{itemName}」，可先 inspect_unit(classes=inventory) 确认");

            switch (op)
            {
                case "steal_item":
                    wub.CreateAction(new UnitActionRoleStealItem(player, stack.SoleID), true);
                    return Ok(new JObject
                    {
                        ["op"] = op, ["target"] = target, ["item_name"] = itemName, ["stack_count"] = stack.Count
                    });

                case "ask_for":
                {
                    if (stack.Count < count)
                        return Fail($"玩家「{itemName}」单栈仅 {stack.Count} 个，不足 {count} 个（讨要按单个栈计数，不支持跨栈合并）",
                                    new JObject { ["item_name"] = itemName, ["requested"] = count, ["stack_count"] = stack.Count });
                    // 完成挂起：原生讨要剧情带"同意/拒绝"选项（Askfor.customNPCToPlayerDrama+Agree/RefuseOption
                    // 反编三件套实锤），玩家拒绝时还有 isNPCReduceIntim 不满标记——工具等选择后按结局返回。
                    var ask = new UnitActionRoleAskfor(player, stack.SoleID, count);
                    var slot = DeferNativeUnitAction(ask, "ask_for", DramaNativeClaim.UidOf(wub), () =>
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
                    try { wub.CreateAction(ask, true); }
                    catch (Exception e)
                    {
                        if (slot != null) { UnitActionPending.Remove(ask.Pointer); DramaGate.Cancel(slot.Value); }
                        return Fail("发起讨要失败: " + e.Message);
                    }
                    if (slot != null) return DramaGate.MakePendingMarker(slot.Value);
                    // AutoConfirm（verify 工程）旧立即路径：
                    return Ok(new JObject { ["op"] = op, ["target"] = target, ["item_name"] = itemName, ["count"] = count, ["pending"] = true });
                }

                default:
                    return Fail("unsupported item_acquire op: " + op);
            }
        }

        /// <summary>背包里的一个道具栈（soleID = 栈唯一 ID，Count = 该栈有几个）。</summary>
        private class PropsStack
        {
            public string SoleID;
            public int Count;
        }

        /// <summary>
        /// 从单位背包按中文名挑一个道具栈。want &gt; 0 时按数量挑：① 恰好等于 want；② 最小的"够装"栈；③ 退回首个同名栈。
        /// want &lt;= 0 表示无数量诉求（如偷窃整栈语义），直接取首个同名栈。未找到返回 null。
        /// </summary>
        private static PropsStack FindPropsStack(WorldUnitBase unit, string itemName, int want)
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

        // ---------- 辅助：真实校验 ----------

        /// <summary>
        /// 通用：AI 载体挂到 npc，执行"面向玩家"的 WorldUnitAIAction（toUnit 已由调用方在实例上设好）。
        /// 模式来自神识传音 IL 实证：ai.Init(actor) → action.Init(ai, 1, null) → ActionStart(onEndCall)。
        /// ActionStart 回调为 Il2CppSystem.Action<bool>（bool=结算结果）；当前传 null（结算捕获/推
        /// npc_initiative 事件为后续增强）；真机须核验游戏对 null 回调的容忍度，必要时补委托互操作。
        /// </summary>
        private static void StartToUnitAiAction(WorldUnitAIActionBase action, WorldUnitBase npc)
        {
            var ai = new WorldUnitAIBase();
            ai.Init(npc);
            action.Init(ai, 1, null);
            action.ActionStart(NewAiActionCallback());
        }

        /// <summary>
        /// AI 动作「完成挂起」启动（论道/双修专用）：动作发出后工具不立即给结果，而是返回
        /// DramaGate pending 标记 → WsClient 憋住 response → LLM 回合挂起 → 玩家在原生界面
        /// （论道/双修，30s~1min）操作完 → 引擎回调 ActionStart(bool) → Resolve(slot, 真实结果)
        /// → 补发 response → LLM 才按真实结局说话。与 StartRelationAction 的确认窗同一条延迟链。
        ///
        /// 依据：神识传音 case17/22 对 1031/1037 同款挂 Action<bool>，p==true 即"完成"。
        /// 真机观察点：论道完成后 Player.log 应出现 [AiAction] 日志；若始终不出现说明 1037
        /// 完成时不回调（与 "1037 容忍 null"的旧观察一致），届时需改轮询动作状态。
        /// 超时：DramaGate 120s 自动 Resolve"玩家长时间未响应"（与确认窗一致；玩家更慢时
        /// Python 侧 request_timeout 先到，LLM 收到超时错误，迟到补发被幂等丢弃）。
        /// </summary>
        /// <param name="doneNote">完成时给 LLM 的 note（工具结果语义，NPC 视角描述世界）</param>
        private JObject StartToUnitAiActionDeferred(WorldUnitAIActionBase action, WorldUnitBase npc, string op, string npcName)
        {
            if (ShowDramaService.AutoConfirm)
            {
                // verify 直通：无玩家交互，立即启动并返回真实结果形态（pending 无人应答会被判 FAIL）
                StartToUnitAiAction(action, npc);
                return Ok(new JObject { ["op"] = op, ["target"] = npcName, ["completed"] = true });
            }
            if (!DramaGate.TryDefer(ModIds.AiActionGate, out int slot))
                return Fail("已有一个 AI 行动在等待玩家完成（论道/双修界面未关闭），请稍后再试");
            try
            {
                var ai = new WorldUnitAIBase();
                ai.Init(npc);
                action.Init(ai, 1, null);
                System.Action<bool> managed = ok =>
                {
                    JObject result = ok
                        ? Ok(new JObject { ["op"] = op, ["target"] = npcName, ["completed"] = true })
                        : Fail(op + "未完成（玩家婉拒或中途离开）");
                    try { UnityEngine.Debug.Log("[AiAction] " + op + " 完成回调 ok=" + ok + " slot=" + slot); } catch { }
                    DramaGate.Resolve(slot, result);
                };
                action.ActionStart(UnhollowerRuntimeLib.DelegateSupport.ConvertDelegate<Il2CppSystem.Action<bool>>(managed));
            }
            catch (Exception e)
            {
                DramaGate.Cancel(slot);   // 启动失败：静默注销（WsClient 尚未登记该 slot）
                return Fail(op + " 启动失败: " + e.Message);
            }
            return DramaGate.MakePendingMarker(slot);
        }

        /// <summary>
        /// 原生 UnitAction（Give/Askfor/Invite/TeachSkill）「完成挂起」登记。
        /// 返回非 null = 已登记 slot，CreateAction 成功后应返回 MakePendingMarker；
        /// 返回 null 且 busyFail=null = AutoConfirm（verify 工程）→ 调用方走旧立即路径；
        /// busyFail 非空 = 撞窗 busy（已有 AI 行动/确认窗等玩家响应），直接返回给 LLM。
        ///
        /// 结果不用这里给——Harmony postfix（UnitActionHooks，仅主工程）在原生 OnEnd() 后
        /// 调 UnitActionPending.Complete 组真实结局（读 receive/refuseProps、isAskforComplete、
        /// isInviteComplete、gainSkill 等落定字段）。真机截图实证赠送剧情"我收下了/我不需要这个"，
        /// 讨要/邀约/传功同理有选择——结局由玩家决定，工具必须等选择后再返回。
        /// 超时：DramaGate 120s 自动 Resolve"玩家长时间未响应"（与论道/确认窗一致）。
        /// </summary>
        /// <summary>
        /// 闸 1.5 包装：登记归因凭证 → 开确认窗 → 按结果处置。
        /// 凭证让 `DramaAiOption` 在「Execute 返回之后才弹的原生剧情窗」上仍能认出这是我方动作的
        /// 后续（机理见 DramaNativeClaim 头注释）。
        /// **没真挂起**（撞窗 busy / 开窗失败）→ 立刻收回，免得留一张空券在 ClaimTTL 内误挡玩家的窗；
        /// **真挂起** → 把券绑到槽位，槽位一 Resolve（点确定/拒绝、或 120s 超时）就自动吊销。
        /// </summary>
        private JObject Claimed(WorldUnitBase npc, Func<JObject> open)
        {
            string uid = DramaNativeClaim.UidOf(npc);
            DramaNativeClaim.ClaimFor(uid);
            JObject res = open();
            DramaNativeClaim.ReleaseUnlessPending(res, uid);
            // `__slot__` 由 DramaGate.MakePendingMarker 写入，只在真挂起时才有意义
            if (res != null && res["__pending__"]?.Value<bool>() == true)
                DramaNativeClaim.BindSlot(uid, res["__slot__"]?.Value<int>() ?? 0);
            return res;
        }

        private int? DeferNativeUnitAction(UnhollowerBaseLib.Il2CppObjectBase action, string op, string unitID, Func<JObject> buildResult, out JObject busyFail)
        {
            busyFail = null;
            if (ShowDramaService.AutoConfirm) return null;
            if (!DramaGate.TryDefer(ModIds.AiActionGate, out int slot))
            {
                busyFail = Fail("已有一个 AI 行动在等待玩家响应（剧情/论道界面未关闭），请先完成当前交互再发起");
                return null;
            }
            UnitActionPending.Register(action.Pointer, slot, op, unitID, buildResult);
            try { UnityEngine.Debug.Log("[UnitAction] " + op + " 已挂起等待玩家选择 slot=" + slot); } catch { }
            return slot;
        }

        /// <summary>
        /// 通用：AI 载体挂到目标，执行"自身状态类"的 WorldUnitAIAction（疗伤/提升心情，无 toUnit）。
        /// </summary>
        private static void StartSelfAiAction(WorldUnitAIActionBase action, WorldUnitBase npc)
        {
            var ai = new WorldUnitAIBase();
            ai.Init(npc);
            action.Init(ai, 1, null);
            action.ActionStart(NewAiActionCallback());
        }

        /// <summary>
        /// AI 动作回调（空操作，用于无需挂起等待的自身类动作）。真机实证（工具验证工程）：
        /// 1044 邀约/1034 疗伤/1041 提升心情在原生内部会调用 ActionStart 的回调，
        /// 传 null 直接 NRE（1037 论道/1031 双修当时容忍 null；起二者改走
        /// StartToUnitAiActionDeferred 挂真实回调，见该方法注释的真机观察点）。
        /// 原生代码同样传真实回调（types36_fulldll L46555/L46645），此处对齐。
        /// </summary>
        private static Il2CppSystem.Action<bool> NewAiActionCallback()
        {
            return UnhollowerRuntimeLib.DelegateSupport.ConvertDelegate<Il2CppSystem.Action<bool>>(
                new System.Action<bool>(_ => { }));
        }

        /// <summary>
        /// 解析传功目标功法：优先 skill 参数直接按功法 ID 查（UnitInfoData.GetActionMartial(string)，
        /// 反编实锤：返回 DataUnit.ActionMartialData）；失败则按功法中文名在前几槽位（灵技/绝技/身法/神通/心法）
        /// 逐个匹配（名称经 GetActionMartial(id).data -> ConfBattleSkillPrefixName.GetName 组合中文名）。
        /// 返回 null 表示未找到。
        /// </summary>
        private static DataUnit.ActionMartialData ResolveMartial(WorldUnitBase wub, string skill)
        {
            try
            {
                var ud = wub.data.unitData;
                // 1) 按 ID 直查（inspect_unit abilities 里的 id 字段）
                try
                {
                    var byId = ud.GetActionMartial(skill);
                    if (byId != null) return byId;
                }
                catch { }

                // 2) 按中文名匹配已学功法（仅查已知槽位 + 心法，避免遍历整个 allActionMartial 字典）
                var slotIds = new List<string>();
                try { slotIds.Add((string)ud.skillLeft); } catch { }
                try { slotIds.Add((string)ud.skillRight); } catch { }
                try { slotIds.Add((string)ud.step); } catch { }
                try { slotIds.Add((string)ud.ultimate); } catch { }
                try
                {
                    var abilitys = ud.abilitys;   // Il2CppStringArray（多心法）
                    if (abilitys != null)
                        for (int i = 0; i < abilitys.Length; i++)
                        {
                            try { slotIds.Add(abilitys[i]); } catch { }
                        }
                }
                catch { }

                foreach (var sid in slotIds)
                {
                    if (string.IsNullOrEmpty(sid) || sid == skill) continue;
                    try
                    {
                        var am = ud.GetActionMartial(sid);
                        if (am == null) continue;
                        var md = (dynamic)am.data;   // DataProps.MartialData（互操作层运行时绑定，见 UnitSnapshot.MartialSlot）
                        if (md == null) continue;
                        string nm = "";
                        try { nm = g.conf.battleSkillPrefixName.GetName(md); } catch { }
                        if (string.IsNullOrEmpty(nm)) continue;
                        if (nm.IndexOf(skill, StringComparison.Ordinal) >= 0 || skill.IndexOf(nm, StringComparison.Ordinal) >= 0)
                            return am;
                    }
                    catch { }
                }
            }
            catch { }
            return null;
        }

        /// <summary>
        /// 传功不指定 skill 时的 seed：从 NPC(wub) 自己的功法槽（灵技/绝技/身法/神通/心法）里
        /// 挑第一本能通过官方布尔校验（SkillCanTeach(wub, skillData)）的功法作为挂载——
        /// 只满足 UnitActionRoleTeachSkill 构造必填；实际学哪本仍由玩家面板选择（落 gainSkill）。
        /// 不用 SkillCanTeach(wub,player) 二参单值重载：其语义（返回 null）并不可靠，真机一度让
        /// 「身负多门功法的 NPC 也被判无可学」。
        /// </summary>
        private static DataUnit.ActionMartialData FirstTeachableSkill(WorldUnitBase wub)
        {
            var slotIds = new List<string>();
            var ud = wub.data.unitData;
            try { if (!string.IsNullOrEmpty((string)ud.skillLeft)) slotIds.Add(ud.skillLeft); } catch { }
            try { if (!string.IsNullOrEmpty((string)ud.skillRight)) slotIds.Add(ud.skillRight); } catch { }
            try { if (!string.IsNullOrEmpty((string)ud.step)) slotIds.Add(ud.step); } catch { }
            try { if (!string.IsNullOrEmpty((string)ud.ultimate)) slotIds.Add(ud.ultimate); } catch { }
            try
            {
                var abilitys = ud.abilitys;   // Il2CppStringArray（多心法）
                if (abilitys != null)
                    for (int i = 0; i < abilitys.Length; i++)
                    {
                        try { if (!string.IsNullOrEmpty((string)abilitys[i])) slotIds.Add(abilitys[i]); } catch { }
                    }
            }
            catch { }
            foreach (var sid in slotIds)
            {
                try
                {
                    var am = ud.GetActionMartial(sid);
                    if (am == null) continue;
                    if (UnitActionRoleTeachSkill.SkillCanTeach(wub, am)) return am;
                }
                catch { }
            }
            return null;
        }

        /// <summary>
        /// ActionMartialData → 中文名（对齐 UnitSnapshot.MartialSlot 官方路径：
        /// am.data.To&lt;MartialData&gt;().martialInfo.name → GameTool.LS，兜底 battleSkillPrefixName）。
        /// 用于把传功 gainSkill（玩家实际所选）解码成可读名字；拿不到返回 ""。
        /// </summary>
        private static string MartialDisplayName(DataUnit.ActionMartialData am)
        {
            if (am == null) return "";
            try
            {
                var dataObj = am.data;
                if (dataObj == null) return "";
                var md = ((dynamic)dataObj).To<DataProps.MartialData>();
                if (md == null) return "";
                if (md.martialInfo != null)
                {
                    string raw = null;
                    try { raw = (string)md.martialInfo.name; } catch { }
                    if (!string.IsNullOrEmpty(raw))
                    {
                        try { string ls = GameTool.LS(raw); if (!string.IsNullOrEmpty(ls)) return ls; } catch { }
                        return raw;
                    }
                }
                try { var n = g.conf.battleSkillPrefixName.GetName(md); if (!string.IsNullOrEmpty(n)) return n; } catch { }
            }
            catch { }
            return "";
        }

        private static bool SameGrid(WorldUnitBase a, WorldUnitBase b)
        {
            if (a == null || b == null) return false;
            var da = a.data.unitData;
            var db = b.data.unitData;
            return da.pointX == db.pointX && da.pointY == db.pointY;
        }

        private static int GetIntimWith(string target)
        {
            var wub = UnitLookup.Resolve(target);
            if (wub == null) return 0;
            return SafeInt(wub.data.unitData.relationData.GetIntim(g.world.playerUnit), 0);
        }

        private static int SafeInt(JToken tok, int def)
        {
            try { return tok == null ? def : (int)tok; }
            catch { return def; }
        }

        private static JObject Ok(object data)
        {
            return new JObject { ["success"] = true, ["data"] = JToken.FromObject(data) };
        }

        private static JObject Fail(string error, object data = null, int? current_intim = null)
        {
            return new JObject
            {
                ["success"] = false,
                ["error"] = error,
                ["data"] = data == null ? null : JToken.FromObject(data),
                ["current_intim"] = current_intim
            };
        }
    }
}