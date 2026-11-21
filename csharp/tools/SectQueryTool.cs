/// <summary>
/// SectQueryTool —— **查询域·宗门**：`query_world(topic=sects)` 的实现（名册 / 人事树 / 职位 / 分部）。
/// （11-19 由 6711 行的 `ToolExecutor.cs` 拆出，11-20 按 Python 的域归并；成员逐字搬移。）
/// 跨类调用一律 `<类名>.<方法>`；`internal` = 仅本程序集可见。
/// </summary>
using System;
using System.Collections.Generic;
using Newtonsoft.Json.Linq;
using static AgentLoopBridge.ToolResult;

namespace AgentLoopBridge
{
    internal static class SectQueryTool
    {
        /// <summary>
        /// 宗门概览（topic=sects）：
        /// ①名号组成（mainName/branchName/nameOrigin；name1/name2 组成词 已按用户裁剪）
        /// ②存亡/被占状态（isBeHold + curHoldSchoolNameID 反查持有宗名——字段语义是"当前由谁掌控"，
        ///   分宗可能恒处依附态，"被占=已灭"的强结论留真机校准，故只报事实不编结论）
        /// ③宗门层级（IsTopSchool/GetTopSchool 两遍分组：主宗名下挂子宗名列表）
        /// ④宗旨/气运（slogan 四槽位 LS 去重取文案；fateItem.fateFeature → g.conf.fateFeature.GetItem(id)
        ///   → name/desc；type/stand 立场值 2026-09-11 已按用户裁剪移除）
        /// 顺手带：宗主/弟子数/声望/敌对宗门（buildData=DataBuildSchool.SchoolData 反编实锤）。
        ///
        /// 【查询粒度 2026-10-29】region 不再被 name 短路（旧写法 `if (!byName &amp;&amp; !RegionOk(...))`
        /// 让"name+region"组合整个失效）。三档：
        ///   name 空            → 按 region 过滤（给了就收窄）
        ///   name 给 / region 空 → 族系（主宗 + 全部分舵，跨州收全）—— 与旧行为一致
        ///   name + region       → 只留该州内的族系成员，**不跨州展开**
        /// 另一条：name 精确命中**分舵全名**时只回那一条（旧写法会把锚点换成主宗再收回全族）。
        /// 边界：名字里的州名是**生成时快照**（get_branchName 用 ConfWorldAreaBase::GetAreaName 拼），
        /// 而 SchoolRelocate 搬迁不改名 ⇒ "在某州"一律以 gridData.areaBaseID 为准，绝不解析名字。
        ///
        /// 【detail 分层】info=功法偏好/人数细分/经济/战力/名额/战绩/交情；people=人事树；all=两者。
        /// 不给 detail 时输与旧版逐字节一致（老测试锁着）。
        /// 数据源与 RVA 见 docs/plan-sect.md；本函数不猜字段，读不到就不写。
        /// </summary>
        internal static JObject QueryWorldSects(JObject args, string region = null, string name = null)
        {
            string detail = "";
            try { detail = (string)args["detail"]; } catch { }
            detail = (detail ?? "").Trim().ToLowerInvariant();
            bool wantInfo = detail == "info" || detail == "all";
            bool wantPeople = detail == "people" || detail == "all";

            bool byName = !string.IsNullOrEmpty(name);
            bool byRegion = !string.IsNullOrEmpty(region);

            // 全量（族扩展必须在全量上做：主宗与分舵可能分属不同州，如丹阳谷主宗在永宁州、
            // 分舵在水宁州 —— 先按 region 筛会把外州成员整片丢掉）
            var all = new List<KeyValuePair<QueryTool.BuildInfo, MapBuildSchool>>();
            foreach (var bi in QueryTool.CollectNamedBuilds())
            {
                MapBuildSchool ms0 = null;
                try { ms0 = bi.Build as MapBuildSchool; } catch { }
                if (ms0 != null) all.Add(new KeyValuePair<QueryTool.BuildInfo, MapBuildSchool>(bi, ms0));
            }

            // 全局主宗映射（族系判定用）
            var topOfAll = new Dictionary<IntPtr, MapBuildSchool>();
            foreach (var kv in all)
            {
                MapBuildSchool t = null;
                try { t = kv.Value.IsTopSchool() ? kv.Value : kv.Value.GetTopSchool(); } catch { }
                topOfAll[kv.Value.Pointer] = t ?? kv.Value;
            }

            // 候选集：region 参与收窄（不再因 byName 跳过）
            var schools = new List<KeyValuePair<QueryTool.BuildInfo, MapBuildSchool>>();
            foreach (var kv in all)
                if (!byRegion || QueryTool.RegionOk(kv.Key, region)) schools.Add(kv);

            string scope = byRegion ? "region" : "all";
            MapBuildSchool familyRoot = null;

            if (byName)
            {
                QueryTool.BuildInfo hitBi = null;
                MapBuildSchool hitMs = null;
                bool hitFullName = false;

                // ① 先按建物全名精确匹配（分舵全名可唯一定位一支）
                foreach (var kv in schools)
                {
                    bool eq = false;
                    try { eq = kv.Key.Name == name; } catch { }
                    if (eq) { hitBi = kv.Key; hitMs = kv.Value; hitFullName = true; break; }
                }
                // ② 再按族名（主名 / 源名）精确匹配 —— 族系查询
                if (hitMs == null)
                {
                    foreach (var kv in schools)
                    {
                        bool eq = false;
                        try { eq = QueryTool.LSOrRaw(kv.Value.mainName) == name || QueryTool.LSOrRaw(kv.Value.nameOrigin) == name; } catch { }
                        if (eq) { hitBi = kv.Key; hitMs = kv.Value; break; }
                    }
                }
                if (hitMs == null)
                {
                    var empty = new JObject { ["sects"] = new JArray(), ["total"] = 0, ["name"] = name };
                    if (byRegion) empty["region"] = region;
                    empty["note"] = byRegion
                        ? "「" + region + "」内未找到名为「" + name + "」的宗门（该州无此宗，或名号写法不同）"
                        : "未找到名为「" + name + "」的宗门（天下未闻此名，或已灭除籍）";
                    return Ok(empty);
                }

                MapBuildSchool root;
                if (!topOfAll.TryGetValue(hitMs.Pointer, out root) || root == null) root = hitMs;
                familyRoot = root;

                if (byRegion)
                {
                    // 州内：只留该州的族系成员，不跨州展开
                    var scoped = new List<KeyValuePair<QueryTool.BuildInfo, MapBuildSchool>>();
                    foreach (var kv in schools)
                    {
                        MapBuildSchool t;
                        MapBuildSchool tk = topOfAll.TryGetValue(kv.Value.Pointer, out t) && t != null ? t : kv.Value;
                        if (tk.Pointer == familyRoot.Pointer) scoped.Add(kv);
                    }
                    schools = scoped;
                    scope = "state";
                }
                else if (hitFullName && familyRoot.Pointer != hitMs.Pointer)
                {
                    // 传的是分舵全名 → 只这一条，不扩族
                    schools = new List<KeyValuePair<QueryTool.BuildInfo, MapBuildSchool>>();
                    schools.Add(new KeyValuePair<QueryTool.BuildInfo, MapBuildSchool>(hitBi, hitMs));
                    scope = "self";
                }
                else
                {
                    var family = new List<KeyValuePair<QueryTool.BuildInfo, MapBuildSchool>>();
                    foreach (var kv in all)
                    {
                        MapBuildSchool t;
                        MapBuildSchool tk = topOfAll.TryGetValue(kv.Value.Pointer, out t) && t != null ? t : kv.Value;
                        if (tk.Pointer == familyRoot.Pointer) family.Add(kv);
                    }
                    schools = family;
                    scope = "family";
                }
            }

            // ③层级两遍分组：只在**结果集**上算「下辖」——这样"下辖N宗"永远与被返回的条目一致
            var subSchools = new Dictionary<IntPtr, List<string>>();
            foreach (var kv in schools)
            {
                MapBuildSchool t;
                MapBuildSchool tk = topOfAll.TryGetValue(kv.Value.Pointer, out t) && t != null ? t : kv.Value;
                if (tk.Pointer == kv.Value.Pointer) continue;
                string nm = null;
                try { nm = kv.Value.name; } catch { }
                List<string> lst;
                if (!subSchools.TryGetValue(tk.Pointer, out lst)) { lst = new List<string>(); subSchools[tk.Pointer] = lst; }
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
                try { if (!string.IsNullOrEmpty(ms.mainName)) o["main_name"] = QueryTool.LSOrRaw(ms.mainName); } catch { }
                try { if (!string.IsNullOrEmpty(ms.branchName)) o["branch_name"] = QueryTool.LSOrRaw(ms.branchName); } catch { }
                try { if (!string.IsNullOrEmpty(ms.nameOrigin) && ms.nameOrigin != ms.name) o["name_origin"] = QueryTool.LSOrRaw(ms.nameOrigin); } catch { }

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
                        o["hold_by"] = string.IsNullOrEmpty(holderName) ? holderId : QueryTool.LSOrRaw(holderName);
                    }
                }

                // —— ③层级 ——
                bool isTop = false;
                try { isTop = ms.IsTopSchool(); } catch { }
                o["is_top"] = isTop;
                MapBuildSchool topSchool;
                if (topOfAll.TryGetValue(ms.Pointer, out topSchool) && topSchool != null && topSchool.Pointer != ms.Pointer)
                {
                    string tn = null;
                    try { tn = topSchool.name; } catch { }
                    if (!string.IsNullOrEmpty(tn)) o["top_school"] = QueryTool.LSOrRaw(tn);
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
                                try { s = QueryTool.LSOrRaw(si.slogan); } catch { }
                                if (string.IsNullOrEmpty(s)) try { s = QueryTool.LSOrRaw(si.desc); } catch { }
                                if (string.IsNullOrEmpty(s) || !seen.Add(s)) continue;
                                var so = new JObject { ["slogan"] = s };
                                try { if (!string.IsNullOrEmpty(si.desc) && si.desc != si.slogan) so["desc"] = QueryTool.LSOrRaw(si.desc); } catch { }
                                slogans.Add(so);
                                if (slogans.Count >= 3) break;
                            }
                        if (slogans.Count > 0) o["slogans"] = slogans;

                        try
                        {
                            var fi = sd.fateItem;
                            if (fi != null)
                            {
                                string ft = QueryTool.FateText(fi.fateFeature);
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
                            string mn = UnitIdentity.NameOfId(mid);
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
                                o["enemy"] = string.IsNullOrEmpty(en) ? eid : QueryTool.LSOrRaw(en);
                            }
                        }
                        catch { }
                    }
                }
                catch { }

                // —— detail=info：功法偏好 / 人数细分 / 经济 / 战力 / 名额 / 战绩 / 交情 ——
                if (wantInfo) AppendSectInfo(o, ms, all);

                // —— detail=people：人事树（postData 优先，读不到退扁平名单） ——
                if (wantPeople)
                {
                    string degrade;
                    var ppl = SectPeopleTree(ms, out degrade);
                    if (ppl != null) o["people"] = ppl;
                    if (!string.IsNullOrEmpty(degrade)) o["people_note"] = degrade;
                }

                items.Add(o);
            }

            var result = new JObject { ["sects"] = items, ["total"] = items.Count, ["scope"] = scope };
            if (byName) result["name"] = name;
            if (byRegion) result["region"] = region;
            // 11-20：宗门查询从 `QueryTool` 分出来独立成文件，这一行是"分家真的生效了"的证据 ——
            //   旧实现里它跟 inspect/search 在同一个 switch 分支里，日志分不出来。
            //   只打要素（范围/条数/detail），不打整份名册（那会把日志撑爆）。
            try
            {
                ModMain.P("[Sect] query_world(sects) scope=" + scope + " total=" + items.Count
                          + " detail=" + (detail.Length == 0 ? "(默认)" : detail)
                          + (byName ? " name=" + name : "") + (byRegion ? " region=" + region : ""));
            }
            catch { }

            // 收窄说明：让模型知道"被砍掉了什么、还能怎么放宽"，不用再猜
            if (familyRoot != null)
            {
                int famTotal = 0;
                var others = new JArray();
                foreach (var kv in all)
                {
                    MapBuildSchool t;
                    MapBuildSchool tk = topOfAll.TryGetValue(kv.Value.Pointer, out t) && t != null ? t : kv.Value;
                    if (tk.Pointer != familyRoot.Pointer) continue;
                    famTotal++;
                    bool inResult = false;
                    foreach (var kv2 in schools) { if (kv2.Value.Pointer == kv.Value.Pointer) { inResult = true; break; } }
                    if (!inResult) others.Add(kv.Key.Name);
                }
                result["family_total"] = famTotal;
                if (others.Count > 0)
                {
                    result["family_others"] = others;
                    result["note"] = (byRegion
                        ? "已按 region=「" + region + "」收窄到 " + items.Count + " 处"
                        : "已按分舵全名收窄到 " + items.Count + " 处")
                        + "；同族另有 " + others.Count + " 处";
                }
            }
            return Ok(result);
        }

        /// <summary>
        /// 读 Il2Cpp/普通 List&lt;string&gt;：Count + 索引器（IL2CPP 禁 foreach）。任何异常给空表。
        /// 用于 npcBigElders/npcElders/npcInherit/npcIn/npcOut/manageInherit 这一批同构字段。
        /// </summary>
        internal static List<string> ReadStrList(object lst)
        {
            var res = new List<string>();
            if (lst == null) return res;
            try
            {
                dynamic d = lst;
                int n = (int)d.Count;
                for (int i = 0; i < n; i++)
                {
                    string s = null;
                    try { s = (string)d[i]; } catch { }
                    if (!string.IsNullOrEmpty(s)) res.Add(s);
                }
            }
            catch { }
            return res;
        }

        /// <summary>读 List&lt;SchoolDepartmentType&gt;（PostBigElders.departmentTypes 只能走 dynamic）。
        /// 元素是枚举 → Convert.ToInt32 取底层值；&lt;=0（None）丢弃。</summary>
        internal static List<int> ReadEnumList(object lst)
        {
            var res = new List<int>();
            if (lst == null) return res;
            try
            {
                dynamic d = lst;
                int n = (int)d.Count;
                for (int i = 0; i < n; i++)
                {
                    int v = -1;
                    try { v = Convert.ToInt32((object)d[i]); } catch { }
                    if (v > 0) res.Add(v);
                }
            }
            catch { }
            return res;
        }

        /// <summary>12 个功法偏好键 → 中文（**备用表**）。
        /// 权威来源：游戏本体 `LocalText.json` 的 `modEffectType1Name_&lt;key&gt;` 条目。
        /// 注意 basFroze = 水灵根（不是冰）；运行期优先问 g.conf.roleEffect.GetValueName(key)。</summary>
        internal static readonly Dictionary<string, string> SectBasTypeCn = new Dictionary<string, string>
        {
            ["basSword"] = "剑法资质", ["basSpear"] = "枪法资质", ["basBlade"] = "刀法资质",
            ["basFist"] = "拳法资质", ["basPalm"] = "掌法资质", ["basFinger"] = "指法资质",
            ["basFire"] = "火灵根", ["basFroze"] = "水灵根", ["basThunder"] = "雷灵根",
            ["basWind"] = "风灵根", ["basEarth"] = "土灵根", ["basWood"] = "木灵根",
        };

        /// <summary>单个功法偏好键 → 中文：先问游戏（ConfRoleEffect.GetValueName，反编 0x62442B0 =
        /// 静态字典 + GameTool.LS），再查备用表，最后原样返回（元素若已是中文，原样即为正解）。</summary>
        internal static string SectBasTypeCnOf(string key)
        {
            string s = null;
            try { s = g.conf.roleEffect.GetValueName(key); } catch { }
            if (!string.IsNullOrEmpty(s)) return s;
            string cn;
            if (SectBasTypeCn.TryGetValue(key, out cn)) return cn;
            return key;
        }

        /// <summary>宗门功法偏好（12 项）：schoolData.basTypes（getter 现算）→ 空则
        /// buildSchoolData.basTypes（持久化那份）→ 仍空返回 null（整段不出现）。</summary>
        internal static JArray SectBasTypesOf(MapBuildSchool ms)
        {
            var raw = new List<string>();
            try
            {
                var sd = ms.schoolData;
                if (sd != null) raw = ReadStrList((object)sd.basTypes);
            }
            catch { }
            if (raw.Count == 0)
            {
                try
                {
                    var sd = ms.schoolData;
                    if (sd != null)
                    {
                        var bsd = sd.buildSchoolData;
                        if (bsd != null) raw = ReadStrList((object)bsd.basTypes);
                    }
                }
                catch { }
            }
            if (raw.Count == 0) return null;
            var arr = new JArray();
            var seen = new HashSet<string>();
            foreach (var k in raw)
            {
                if (string.IsNullOrEmpty(k) || !seen.Add(k)) continue;
                arr.Add(SectBasTypeCnOf(k));
            }
            return arr.Count > 0 ? arr : null;
        }

        /// <summary>部门 ID → 中文（g.conf.schoolDepartment.GetDepartmentName，失败落静态表）。
        /// 静态表来自 LocalText.json 的 SchoolDepartmentName1~5。</summary>
        internal static string SectDeptName(int dept)
        {
            string s = null;
            try { s = g.conf.schoolDepartment.GetDepartmentName(dept); } catch { }
            if (!string.IsNullOrEmpty(s)) return s;
            switch (dept)
            {
                case 1: return "招贤堂";
                case 2: return "巡查堂";
                case 3: return "外务堂";
                case 4: return "修筑堂";
                case 5: return "贮宝堂";
                default: return "部门" + dept;
            }
        }

        /// <summary>职位枚举 → 中文（备用）：LocalText.json 的 schoolPostName1~7。
        /// 游戏用的是"真传弟子"（不是"亲传"）。</summary>
        internal static string SectPostCn(int post)
        {
            switch (post)
            {
                case 1: return "宗主";
                case 2: return "大长老";
                case 3: return "长老";
                case 4: return "真传弟子";
                case 5: return "内门弟子";
                case 6: return "外门弟子";
                case 7: return "入门弟子";
                default: return "";
            }
        }

        /// <summary>人事树里的一行：名字 / 境界 / 代理 / 状态。
        /// 活人走 g.world.unit.GetUnit + UnitSnapshot.RealmText（面板同口径）；
        /// 查不到活人再翻死亡账本（已故 + 殁于X年X月），仍无则标"未在世"。
        /// ★不输出 unitID（先例：错误帧/文案里不出现内部 ID）★</summary>
        internal static void SectPersonInto(JObject target, string unitId, string agentUnitId)
        {
            if (target == null || string.IsNullOrEmpty(unitId)) return;
            WorldUnitBase u = null;
            try { u = g.world.unit.GetUnit(unitId); } catch { }
            if (u != null)
            {
                try
                {
                    string nm = u.data.unitData.propertyData.GetName();
                    if (!string.IsNullOrEmpty(nm)) target["name"] = nm;
                }
                catch { }
                try
                {
                    string r = UnitSnapshot.RealmText(u);
                    if (!string.IsNullOrEmpty(r)) target["realm"] = r;
                }
                catch { }
            }
            else
            {
                string deadNm = null;
                bool dead = false;
                try
                {
                    object dd, udd; string fid;
                    if (UnitSnapshot.FindDead(unitId, null, out dd, out udd, out fid))
                    {
                        dead = true;
                        try { deadNm = UnitSnapshot.DeadName(dd); } catch { }
                        try
                        {
                            string lbl = UnitSnapshot.DeadMonthLabel(unitId);
                            if (!string.IsNullOrEmpty(lbl)) target["dead_month"] = lbl;
                        }
                        catch { }
                    }
                }
                catch { }
                target["status"] = dead ? "已故" : "未在世";
                if (!string.IsNullOrEmpty(deadNm)) target["name"] = deadNm;
            }
            if (!string.IsNullOrEmpty(agentUnitId))
            {
                string an = UnitIdentity.NameOfId(agentUnitId);
                target["agent"] = string.IsNullOrEmpty(an) ? "在任（代理）" : an;
            }
        }

        internal static JObject SectPerson(string unitId, string agentUnitId)
        {
            var o = new JObject();
            SectPersonInto(o, unitId, agentUnitId);
            return o;
        }

        /// <summary>
        /// detail=info：把"这宗门怎么样"补齐。每段独立 try/catch，读不到就不写该键。
        /// 数据源：SchoolData（人数细分/灵石/信仰/战力/战绩/交情）+ MapBuildSchoolData
        /// （功法偏好/规模/名额上限）。字段清单见 docs/plan-sect.md §2.4。
        /// </summary>
        internal static void AppendSectInfo(JObject o, MapBuildSchool ms, List<KeyValuePair<QueryTool.BuildInfo, MapBuildSchool>> all)
        {
            if (o == null || ms == null) return;

            var bt = SectBasTypesOf(ms);
            if (bt != null) o["bas_types"] = bt;

            DataBuildSchool.SchoolData bd = null;
            try { bd = ms.buildData; } catch { }
            if (bd != null)
            {
                var mem = new JObject();
                try { mem["total"] = bd.totalMember; } catch { }
                try { mem["big_elders"] = ReadStrList(bd.npcBigElders).Count; } catch { }
                try { mem["elders"] = ReadStrList(bd.npcElders).Count; } catch { }
                try { mem["inherit"] = ReadStrList(bd.npcInherit).Count; } catch { }
                try { mem["inner"] = ReadStrList(bd.npcIn).Count; } catch { }
                try { mem["outer"] = ReadStrList(bd.npcOut).Count; } catch { }
                if (mem.Count > 0) o["member"] = mem;

                try { o["money"] = bd.money; } catch { }
                try { o["faith_money"] = bd.faithMoney; } catch { }
                try { o["power"] = bd.GetSchoolPower(); } catch { }

                try
                {
                    var hd = bd.honorData;
                    if (hd != null)
                    {
                        var h = new JObject();
                        try { if (hd.schoolScore > 0) h["score"] = hd.schoolScore; } catch { }
                        try { if (hd.rank > 0) h["rank"] = hd.rank; } catch { }
                        if (h.Count > 0) o["honor"] = h;
                    }
                }
                catch { }

                // 交情：只列非零（GetIntimSchool(string) 直查，不遍历字典），按绝对值取前 5
                try
                {
                    var pairs = new List<KeyValuePair<int, string>>();
                    foreach (var kv in all)
                    {
                        if (kv.Value.Pointer == ms.Pointer) continue;
                        int v = 0;
                        string sid = null;
                        try { sid = kv.Value.schoolNameID; } catch { }
                        if (string.IsNullOrEmpty(sid)) continue;
                        try { v = bd.GetIntimSchool(sid); } catch { continue; }
                        if (v == 0) continue;
                        pairs.Add(new KeyValuePair<int, string>(v, kv.Key.Name));
                    }
                    if (pairs.Count > 0)
                    {
                        pairs.Sort((a, b) => System.Math.Abs(b.Key).CompareTo(System.Math.Abs(a.Key)));
                        var arr = new JArray();
                        for (int i = 0; i < pairs.Count && i < 5; i++)
                            arr.Add(new JObject { ["name"] = pairs[i].Value, ["value"] = pairs[i].Key });
                        o["intim"] = arr;
                    }
                }
                catch { }
            }

            MapBuildSchoolData sd2 = null;
            try { sd2 = ms.schoolData; } catch { }
            if (sd2 != null)
            {
                try { o["is_small"] = sd2.IsSchoolSmall(); } catch { }
                try
                {
                    var si = sd2.initScaleItem;
                    if (si != null)
                    {
                        var cap = new JObject();
                        try { if (si.schoolMain > 0) cap["main"] = si.schoolMain; } catch { }
                        try { if (si.bigElders > 0) cap["big_elders"] = si.bigElders; } catch { }
                        try { if (si.elders > 0) cap["elders"] = si.elders; } catch { }
                        try { if (si.inherit > 0) cap["inherit"] = si.inherit; } catch { }
                        try { if (si.inner > 0) cap["inner"] = si.inner; } catch { }
                        try { if (si.outer > 0) cap["outer"] = si.outer; } catch { }
                        if (cap.Count > 0) o["capacity"] = cap;
                    }
                }
                catch { }
            }
        }

        /// <summary>
        /// detail=people：人事树。宗主 → 大长老（辖哪些堂口）→ 各部门长老 → 各自真传弟子。
        /// 优先读 SchoolData.postData（唯一带部门/师徒归属的那一份，反编依据：
        /// UISchoolNewAdmin.GetStudentMax 的形参就是 Dictionary&lt;SchoolDepartmentType, PostData.PostElders&gt;）；
        /// postData 读不到再退扁平 npcBigElders/npcElders/npcInherit，并在 degradeNote 里说明走了降级。
        /// 内门/外门只给人数（几百号人，模型问不到这个粒度）。
        /// </summary>
        internal static JObject SectPeopleTree(MapBuildSchool ms, out string degradeNote)
        {
            degradeNote = null;
            if (ms == null) return null;

            DataBuildSchool.SchoolData bd = null;
            try { bd = ms.buildData; } catch { }
            if (bd == null) { degradeNote = "读不到宗门数据"; return null; }

            var o = new JObject();
            DataBuildSchool.PostData pd = null;
            try { pd = bd.postData; } catch { }

            bool gotMain = false, gotBE = false, gotElders = false;

            if (pd != null)
            {
                try
                {
                    var pm = pd.postMain;
                    if (pm != null && pm.unitData != null && !string.IsNullOrEmpty(pm.unitData.unitID))
                    {
                        o["main"] = SectPerson(pm.unitData.unitID, pm.unitData.agentUnitID);
                        gotMain = true;
                    }
                }
                catch { }

                try
                {
                    var be = pd.postBigElders;
                    if (be != null && be.Count > 0)
                    {
                        var arr = new JArray();
                        for (int i = 0; i < be.Count; i++)
                        {
                            DataBuildSchool.PostData.PostBigElders it = null;
                            try { it = be[i]; } catch { continue; }
                            if (it == null || it.unitData == null || string.IsNullOrEmpty(it.unitData.unitID)) continue;
                            var po = new JObject();
                            SectPersonInto(po, it.unitData.unitID, it.unitData.agentUnitID);
                            var depts = ReadEnumList((object)it.departmentTypes);
                            if (depts.Count > 0)
                            {
                                var a = new JArray();
                                foreach (var d in depts) a.Add(SectDeptName(d));
                                po["depts"] = a;
                            }
                            arr.Add(po);
                        }
                        if (arr.Count > 0) { o["big_elders"] = arr; gotBE = true; }
                    }
                }
                catch { }

                try
                {
                    var arr = new JArray();
                    for (int d = 1; d <= 5; d++)
                    {
                        DataBuildSchool.PostData.PostElders pe = null;
                        try { pe = pd.GetElders((SchoolDepartmentType)d); } catch { }
                        if (pe == null) continue;
                        var po = new JObject { ["dept"] = SectDeptName(d) };
                        string uid = null, aid = null;
                        try { if (pe.unitData != null) uid = pe.unitData.unitID; } catch { }
                        try { if (pe.unitData != null) aid = pe.unitData.agentUnitID; } catch { }
                        if (string.IsNullOrEmpty(uid)) po["vacant"] = true;
                        else SectPersonInto(po, uid, aid);

                        var ih = ReadStrList((object)pe.manageInherit);
                        if (ih.Count > 0)
                        {
                            var ia = new JArray();
                            foreach (var id in ih) ia.Add(SectPerson(id, null));
                            po["inherit"] = ia;
                        }
                        int innerN = ReadStrList((object)pe.manageIn).Count;
                        int outerN = ReadStrList((object)pe.manageOut).Count;
                        if (innerN > 0) po["inner_count"] = innerN;
                        if (outerN > 0) po["outer_count"] = outerN;
                        arr.Add(po);
                        gotElders = true;
                    }
                    if (arr.Count > 0) o["elders"] = arr;
                }
                catch { }
            }

            if (!gotMain && !gotBE && !gotElders)
            {
                // 降级：postData 不可用 → 扁平名单（没有部门与师徒归属，但"谁是长老/真传"照样对）
                try
                {
                    string mainId = bd.npcSchoolMain;
                    if (!string.IsNullOrEmpty(mainId)) o["main"] = SectPerson(mainId, null);
                }
                catch { }
                try
                {
                    var ids = ReadStrList(bd.npcBigElders);
                    if (ids.Count > 0)
                    {
                        var a = new JArray();
                        foreach (var id in ids) a.Add(SectPerson(id, null));
                        o["big_elders"] = a;
                    }
                }
                catch { }
                try
                {
                    var ids = ReadStrList(bd.npcElders);
                    if (ids.Count > 0)
                    {
                        var a = new JArray();
                        foreach (var id in ids) a.Add(SectPerson(id, null));
                        o["elders_flat"] = a;
                    }
                }
                catch { }
                try
                {
                    var ids = ReadStrList(bd.npcInherit);
                    if (ids.Count > 0)
                    {
                        var a = new JArray();
                        foreach (var id in ids) a.Add(SectPerson(id, null));
                        o["inherit_flat"] = a;
                    }
                }
                catch { }
                degradeNote = "postData 不可读，已退回扁平名单（缺部门与师徒归属）";
            }

            if (o.Count == 0)
            {
                degradeNote = degradeNote ?? "宗门人事为空";
                return null;
            }
            return o;
        }

        /// <summary>按名找宗门：全名精确 → 族名（主名/源名）精确；同名多支时用 region 消歧，
        /// 仍不唯一就把候选列出来（不猜）。与 query_world topic=sects 同一套匹配口径。</summary>
        internal static MapBuildSchool ResolveSchoolByName(string name, string region, out string fail)
        {
            fail = null;
            var all = new List<KeyValuePair<QueryTool.BuildInfo, MapBuildSchool>>();
            foreach (var bi in QueryTool.CollectNamedBuilds())
            {
                MapBuildSchool ms0 = null;
                try { ms0 = bi.Build as MapBuildSchool; } catch { }
                if (ms0 != null) all.Add(new KeyValuePair<QueryTool.BuildInfo, MapBuildSchool>(bi, ms0));
            }
            bool byRegion = !string.IsNullOrEmpty(region);
            var cand = new List<KeyValuePair<QueryTool.BuildInfo, MapBuildSchool>>();
            foreach (var kv in all)
                if (!byRegion || QueryTool.RegionOk(kv.Key, region)) cand.Add(kv);
            if (cand.Count == 0)
            {
                fail = byRegion ? "「" + region + "」内没有宗门" : "这世上没有宗门";
                return null;
            }

            // ① 全名精确
            foreach (var kv in cand)
            {
                bool eq = false;
                try { eq = kv.Key.Name == name; } catch { }
                if (eq) return kv.Value;
            }
            // ② 族名精确
            var fam = new List<KeyValuePair<QueryTool.BuildInfo, MapBuildSchool>>();
            foreach (var kv in cand)
            {
                bool eq = false;
                try { eq = QueryTool.LSOrRaw(kv.Value.mainName) == name || QueryTool.LSOrRaw(kv.Value.nameOrigin) == name; } catch { }
                if (eq) fam.Add(kv);
            }
            if (fam.Count == 1) return fam[0].Value;
            if (fam.Count > 1)
            {
                var names = new List<string>();
                foreach (var kv in fam)
                    names.Add(kv.Key.Region + "·" + kv.Key.Name);
                fail = "「" + name + "」有多处：" + string.Join("、", names.ToArray())
                     + "。请用 region 指定哪一州（或直接传全名）";
                return null;
            }
            fail = "未找到名为「" + name + "」的宗门（可分舵全名或 region 消歧）";
            return null;
        }

        /// <summary>宗门在任名册（只出**人数**，不出上限）：上限有两处候选数字
        /// （`ConfSchoolPost.numMax` 与 `SchoolInitScale.inherit/inner/outer`），语义未定，
        /// 而 `UISchoolNewAdmin.GetStudentMax` 反编实为"当前人数"（读 `manageIn/manageOut.Count`）
        /// ⇒ 我们**不显示上限、也不按上限预拦**，满没满交给引擎的 `SetPostType.IsCreate` 裁决。</summary>
        internal static JObject SectRoster(MapBuildSchool ms)
        {
            var o = new JObject();
            DataBuildSchool.SchoolData bd = null;
            try { bd = ms.buildData; } catch { }
            if (bd == null) return o;

            try { o["out"] = ReadStrList(bd.npcOut).Count; } catch { }
            try { o["in"] = ReadStrList(bd.npcIn).Count; } catch { }
            try { o["inherit"] = ReadStrList(bd.npcInherit).Count; } catch { }

            var elders = new JArray();
            DataBuildSchool.PostData pd = null;
            try { pd = bd.postData; } catch { }
            if (pd != null)
            {
                for (int d = 1; d <= 5; d++)
                {
                    DataBuildSchool.PostData.PostElders pe = null;
                    try { pe = pd.GetElders((SchoolDepartmentType)d); } catch { }
                    if (pe == null) continue;
                    string uid = null;
                    try { if (pe.unitData != null) uid = pe.unitData.unitID; } catch { }
                    string nm = UnitIdentity.NameOfId(uid);
                    var eo = new JObject { ["dept"] = SectDeptName(d) };
                    if (!string.IsNullOrEmpty(nm)) eo["name"] = nm;
                    else eo["vacant"] = true;
                    try { eo["inherit"] = ReadStrList(pe.manageInherit).Count; } catch { }
                    elders.Add(eo);
                }
            }
            if (elders.Count > 0) o["elders"] = elders;
            return o;
        }

        /// <summary>把 schema 的 post 词归一成引擎枚举：out/in/inherit（也认中文）。0 = 不认识。</summary>
        internal static int SectPostOf(string raw)
        {
            string s = (raw ?? "").Trim().ToLowerInvariant();
            switch (s)
            {
                case "out": case "外门": case "外门弟子": return 6;
                case "in": case "内门": case "内门弟子": return 5;
                case "inherit": case "真传": case "真传弟子": case "亲传": return 4;
                default: return 0;
            }
        }
    }
}
