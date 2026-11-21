using System;
using System.Collections.Generic;
using Newtonsoft.Json.Linq;
using UnhollowerBaseLib;

namespace AgentLoopBridge
{
    /// <summary>
    /// 单位档案统一采集器 —— GameContext(GetL1) 与 ToolExecutor(InspectUnit) 共用同一份数据，
    /// 保证 L1 与 brief 字段/来源完全一致。
    ///
    /// 全部签名来自 Assembly-CSharp.dll 反编实锤（ICSharpCode.Decompiler 导出 types*.txt）：
    ///   - PropertyData: GetName/sex/race/gradeID/beauty/reputation/talent/age/life/mood/attack...
    ///   - UnitInfoData: schoolID/skillLeft/skillRight/ultimate/step/abilitys/props/equips/allTask/appellationTitle...
    ///   - RelationData: GetRelation/GetIntim/GetHumanValue + parent/children/brother/lover/master/student/married
    ///   - g.conf.roleGrade.GetGradeName(grade)  -> 中文境界
    ///   - g.world.unitLog.GetLogDataSync(id) -> LogData.allVitalLogData(重要)/allLogData(常规)
    ///
    /// 待真机项（宗门名/兴趣/技能·道具中文名/战力/道号名）先输出原始 ID + [待真机] 标注，
    /// 单块失败不影响整体，缺失以 null/[待真机] 兜底。
    /// </summary>
    public static class UnitSnapshot
    {
        /// <summary>是否同格（pointX/pointY 比较）；主线程调用。</summary>
        public static bool IsSameGrid(WorldUnitBase a, WorldUnitBase b)
        {
            if (a == null || b == null) return false;
            try
            {
                var da = a.data.unitData;
                var db = b.data.unitData;
                return da.pointX == db.pointX && da.pointY == db.pointY;
            }
            catch { return false; }
        }

        /// <summary>
        /// 是否玩家本人（实锤修复）。
        ///
        /// 起因：自主交互候选集里混进了玩家自己 → "自己给自己传音"（Player.log：
        /// `forced trigger name=缪嘉歆`，而缪嘉歆正是玩家真名；同格=True、好感=0 一路过闸）。
        /// 机制：玩家在游戏里就是一个普通 `WorldUnitBase`，`g.world.unit.GetUnits(true)`
        /// 会把他一并返回（ToolExecutor.search_units / 榜单早已因此在按名跳过玩家自己）。
        /// 而旧判据 `ReferenceEquals(u, player)` 在 IL2CPP 下**静默失效**：同一原生对象经不同
        /// 调用点（`g.world.playerUnit` 与 `GetUnits()` 列表元素、`GetUnit(id)`）取到的托管
        /// 包装器不保证引用同一 → 玩家被当成"同格陌生人"入列。
        ///
        /// 判据顺序（只求准）：
        ///   ① 引用相等（廉价快路，失效也不影响正确性）
        ///   ② unitID 相等（**主判据**：世界唯一、字段可读、与包装器无关）
        ///   ③ 真名兜底（仅当两侧 unitID 都取不到；本作存在与玩家同名的 NPC——见 UnitLookup
        ///      的 09-12 实锤注释，故名字不可当主判据）
        /// 主线程调用。
        /// </summary>
        public static bool IsPlayerUnit(WorldUnitBase u)
        {
            if (u == null) return false;
            WorldUnitBase player = null;
            try { player = g.world.playerUnit; } catch { }
            if (player == null) return false;
            if (ReferenceEquals(u, player)) return true;

            string uid = null, pid = null;
            try { uid = u.data.unitData.unitID; } catch { }
            try { pid = player.data.unitData.unitID; } catch { }
            if (!string.IsNullOrEmpty(uid) && !string.IsNullOrEmpty(pid)) return uid == pid;

            string un = UnitIdentity.Name(u, null), pn = UnitIdentity.Name(player, null);
            return !string.IsNullOrEmpty(un) && un == pn;
        }

        /// <summary>玩家自身 unitID（取不到返回空串；诊断日志用）。主线程调用。</summary>
        public static string PlayerUnitId()
        {
            return UnitIdentity.PlayerId();
        }

        /// <summary>关系枚举名 -> 中文（UnitRelationType）。</summary>
        public static string RelationCn(string en)
        {
            switch (en)
            {
                // 无关系：游戏枚举是 "None"，直通会漏进渲染文本（真机："关系None"）——
                // 与 search_units 的 RelationMatch（"陌生" = None/"") 口径对齐
                case "None": return "陌生";
                case "": return "陌生";
                case "Parent": return "父母";
                case "Children": return "子女";
                case "ChildrenPrivate": return "私生子女";
                case "Brother": return "兄弟姐妹";
                case "ParentBack": return "义父母";
                case "ChildrenBack": return "义子女";
                case "BrotherBack": return "结义";
                case "Married": return "道侣";
                case "Lover": return "情人";
                case "Master": return "师傅";
                case "Student": return "徒弟";
                default: return en;
            }
        }

        /// <summary>
        /// 两个单位之间的**关系快照**（观察者 → 对方），供群聊 `raw.others[]` 用。
        /// 返回 `{relation, intim, same_grid}`，键名与 `raw.relations.player` 的对应三项**同名同义**，
        /// 只是对手方从"玩家"换成了"这一位同席者"。
        ///
        /// ★ 方向（会静默出错的一处）：`RelationData` 是**"我"的关系簿**，
        ///   `viewer.data.unitData.relationData.GetIntim(target)` 的语义是「**viewer 对 target** 的好感」。
        ///   写成 `target...GetIntim(viewer)` 会得到**反向好感**，而且不报任何错。
        ///   验证请用**不对称**数据（A 对 B 好感 80、B 对 A 好感 10）—— 互有好感的数据验证不出方向错误。
        ///
        /// 为什么单列：`Build()` 把对手方**写死成玩家**（`g.world.playerUnit`，见其 :288-296），
        ///   而群聊要的是「当前 NPC ↔ 同席者」这一对。四个原语（`relationData.GetIntim` /
        ///   `GetRelation` / `IsSameGrid` / `RelationCn`）全都是现成的，这里只是换一个对手方。
        /// </summary>
        public static JObject RelationTo(WorldUnitBase viewer, WorldUnitBase target)
        {
            string relEn = "";
            int intim = 0;
            if (viewer != null && target != null)
            {
                try
                {
                    // ★ 必须是**观察者的**关系簿
                    var rel = viewer.data.unitData.relationData;
                    try { intim = rel.GetIntim(target); } catch { }
                    try { relEn = rel.GetRelation(target).ToString(); } catch { }
                }
                catch { }
            }
            return new JObject
            {
                ["relation"] = RelationCn(relEn),
                ["intim"] = intim,
                ["same_grid"] = (viewer != null && target != null) && IsSameGrid(viewer, target),
            };
        }

        /// <summary>同格人物的一个条目（11-11）。名称/关系/好感三项就够写进上下文 ——
        /// 不要在这里塞 unit_id、坐标、门派等：那段文字每轮都发给模型，越短越好。</summary>
        internal sealed class SameGridRow
        {
            internal string Name;
            internal string Relation;
            internal int Intim;
        }

        /// <summary>
        /// 同格人物摘要（11-11 用户需求："NPC 都不知道同一格地区有谁"）。
        ///
        /// ## 排序口径（用户 11-11 定，勿改）
        /// **两个方向各取前 6**：好感最高的 6 个 + 好感最低的 6 个，最多 12 人，其余只报人数。
        /// 理由：最亲的和最恨的才是一个 NPC 真会留意的人；同格一堆陌生人（好感 0）
        /// 既没有信息量，又会把他们在城里的邻居全挤掉。
        ///
        /// ⚠ 游戏里**没有独立的"仇恨度"字段** —— `GetIntim` 本身就是一根可正可负的轴，
        ///   "仇恨"是它的负半段，"好感最低"就等于"最恨"。别再去找什么 hate 值。
        ///
        /// ## 玩家已被排除
        /// 用 `RelationNetwork` 那条链（内部 `TryAdd` → `UnitSnapshot.IsPlayerUnit`）收集，
        /// 玩家天然被剔除。这是有意的：他已在「玩家」段单独写了一份（含同格与否），
        /// 再并列一次纯属浪费 token，也会让模型以为"同格里有个叫某某的陌生人"。
        /// </summary>
        /// <param name="picked">入选的条目（已按好感降序）</param>
        /// <param name="rest">未展示的同格人数</param>
        /// <returns>一行可直接拼进 L1 的文本；无同格者时返回空串。</returns>
        internal static string SameGridText(WorldUnitBase self, List<SameGridRow> picked, out int rest)
        {
            rest = 0;
            if (self == null || picked == null) return "";
            const int TopPerSide = 6;

            var all = new List<SameGridRow>();
            try
            {
                int px = 0, py = 0;
                try { px = self.data.unitData.pointX; py = self.data.unitData.pointY; } catch { return ""; }

                var units = new List<WorldUnitBase>();
                RelationNetwork.CollectSameGridAt(units, new HashSet<string>(), px, py);
                for (int i = 0; i < units.Count; i++)
                {
                    var u = units[i];
                    if (u == null) continue;
                    string un = null;
                    try { un = u.data.unitData.propertyData.GetName(); } catch { }
                    if (string.IsNullOrEmpty(un)) continue;
                    var relObj = RelationTo(self, u);
                    int iv = 0;
                    try { iv = relObj["intim"] != null ? relObj["intim"].Value<int>() : 0; } catch { }
                    all.Add(new SameGridRow
                    {
                        Name = un,
                        Relation = (relObj["relation"] != null ? relObj["relation"].ToString() : null) ?? "陌生",
                        Intim = iv,
                    });
                }
            }
            catch { return ""; }

            if (all.Count == 0) return "";

            // 好感高 → 低
            all.Sort((a, b) => b.Intim.CompareTo(a.Intim));

            // 两个方向各取前 6：先从头取（最亲），再从尾取（最恨），按名字去重
            var byName = new Dictionary<string, SameGridRow>();
            for (int i = 0; i < all.Count && byName.Count < TopPerSide; i++)
                if (!byName.ContainsKey(all[i].Name)) byName[all[i].Name] = all[i];
            for (int i = all.Count - 1; i >= 0 && byName.Count < TopPerSide * 2; i--)
                if (!byName.ContainsKey(all[i].Name)) byName[all[i].Name] = all[i];

            foreach (var r in byName.Values) picked.Add(r);
            picked.Sort((a, b) => b.Intim.CompareTo(a.Intim));   // 展示仍按好感降序

            rest = all.Count - picked.Count;

            var segs = new List<string>(picked.Count);
            for (int i = 0; i < picked.Count; i++)
                segs.Add(picked[i].Name + "（" + picked[i].Relation + "，好感" + picked[i].Intim + "）");

            string s = "同格：" + string.Join("、", segs.ToArray());
            if (rest > 0) s += "，另" + rest + "人";
            return s;
        }

        public static string SexCn(string en)
        {
            switch (en) { case "Man": return "男"; case "Woman": return "女"; default: return en; }
        }

        public static string RaceCn(string en)
        {
            switch (en) { case "Human": return "人族"; case "Demon": return "魔族"; default: return en; }
        }

        /// <summary>
        /// <b>账面月 → "N年M月"</b>（1年1月=1、2年1月=13）。裸数字如 13 直接给模型看会读成"13月"。
        ///
        /// 单一来源 这条换算规则此前在 `RecentTexts` 里内联写过一份；玩家消息时间戳
        /// （`NpcInitiativeMonitor.CurrentTimeLabel`）要用同一条规则 —— 两处各写一份必然漂移，
        /// 表现是"同一个月份，近况里写 2年1月、对话里写 1年13月"。故收敛到这里，两边只许调用它。
        ///
        /// 标度**实证**：`DataUnitLog.LogItemData.month` 与 `g.world.run.roundMonth + 1` 同标度
        /// （真机开局首月载荷为 `"month": 1`；参照 mod 两处独立写法 `roundMonth/12+1` 年 /
        /// `roundMonth%12+1` 月 与 `ConvertToYearsMonths(roundMonth+1)` 互相印证）。
        /// `acctMonth &lt;= 0` 视为"日历不可用"，返回空串 —— 调用方据空串**不打时间戳**。
        /// </summary>
        public static string CnYearMonth(int acctMonth)
        {
            if (acctMonth <= 0) return "";
            int y, m;
            SplitAccountMonth(acctMonth, out y, out m);
            return y + "年" + m + "月";
        }

        /// <summary>
        /// 账面月 → (年, 月)。**算式的唯一落点**：`CnYearMonth` 与结构化输出（`GameContext` 的
        /// `raw.now`）都必须走这里 —— 分开写就是两份会漂移的算术。
        /// </summary>
        public static void SplitAccountMonth(int acctMonth, out int year, out int month)
        {
            year = (acctMonth - 1) / 12 + 1;
            month = (acctMonth - 1) % 12 + 1;
        }

        /// <summary>
        /// 账面月 + 当月日 → "N年M月D日"；日 &lt;= 0（`roundDay` 读不到）时**退化成 "N年M月"**，
        /// 绝不编造一个日出来。月也拿不到（&lt;= 0）返回空串 —— 调用方据此不打时间戳。
        ///
        /// 单一来源 玩家消息时间戳（`NpcInitiativeMonitor.CurrentTimeLabel`）与 L1 的
        /// `raw.now.text` 共用本函数 ⇒ 模型在「当前时间」段看到的串与消息前缀里的**逐字节相同**，
        /// 这个一致性本身就在教模型"方括号里那个就是日期"。
        /// </summary>
        public static string CnDate(int acctMonth, int day)
        {
            string ym = CnYearMonth(acctMonth);
            if (ym.Length == 0) return "";
            return day > 0 ? ym + day + "日" : ym;
        }

        /// <summary>Il2CppStringArray -> string[]。</summary>
        private static string[] Arr(Il2CppStringArray a)
        {
            if (a == null || a.Length == 0) return new string[0];
            var r = new List<string>();
            for (int i = 0; i < a.Length; i++)
            {
                try { r.Add(a[i] ?? ""); } catch { }
            }
            return r.ToArray();
        }

        private static JArray JArr(string[] a)
        {
            var ja = new JArray();
            foreach (var s in a) ja.Add(s);
            return ja;
        }

        /// <summary>Il2Cpp 字符串集合 -> JArray（用 var 遍历，元素统一 ToString，规避泛型参数差异）。</summary>
        private static JArray JArrObjects(object list)
        {
            var ja = new JArray();
            if (list == null) return ja;
            try
            {
                var dyn = (dynamic)list;
                int cnt = (int)dyn.Count;
                for (int i = 0; i < cnt; i++)
                {
                    var item = dyn[i];
                    ja.Add(item == null ? "" : item.ToString());
                }
            }
            catch { }
            return ja;
        }

        /// <summary>
        /// 采集单位档案（主线程调用）。基础字段（名字/境界/宗门/种族/魅力/好感/同格等）总是采集（轻量，
        /// L1 与 brief 都要）；重块（abilities/inventory/relationships/logs/stats）按 classes 按需采集，
        /// 避免平白读 logs 等重数据。classes 为 null 时全采（兼容兜底）。
        /// inventoryTop：分类背包每类 top-N（默认 3；&lt;=0 全量不裁剪）。
        /// invCats / invFind（11-10）：分类背包的定向档。invCats 非空 = 只出点名的类别；
        /// invFind 非空 = 在**全部家当**里按名模糊查（不受类别/裁剪限制）。两者可同时给：
        /// 同时给时先按类别圈定，再在圈内按名查（"丹符里的化瘀丹"与"全部家当里的化瘀丹"结论一致，
        /// 但前者顺带证明"这一类里还有别的"）。见 PropsGroups。
        /// </summary>
        public static JObject Build(WorldUnitBase wub, JArray classes, int inventoryTop = 3,
                                    List<string> invCats = null, string invFind = null)
        {
            var snap = new JObject();
            if (wub == null) { snap["error"] = "unit is null"; return snap; }
            var player = g.world.playerUnit;

            var ud = wub.data.unitData;
            var pd = ud.propertyData;
            var rel = ud.relationData;

            bool Has(string c)
            {
                if (classes == null) return true;
                foreach (var x in classes)
                    if ((string)x == c) return true;
                return false;
            }

            // —— 自身基本面（brief / L1 自身段）——
            // DynInt 动态层（面板同口径）：brief 与 stats 共用一份，提到最前面统一取。
            // 境界/声望取错的根因 面板读的是 dynUnitData 的 DynInt（含气运/装备加成并钳制），
            // 而 brief 这一段原先全读 propertyData 裸字段 —— 两处分层不一致，详见 RealmOf 的说明。
            object dyn = null;
            try { dyn = ((dynamic)wub.data).dynUnitData; } catch { }

            string name = ""; try { name = pd.GetName(); } catch { }
            snap["name"] = name;
            try { snap["sex"] = SexCn(pd.sex.ToString()); } catch { }
            try { snap["race"] = RaceCn(pd.race.ToString()); } catch { }
            {
                var realmDiag = new JObject();
                snap["realm"] = RealmOf(dyn, pd, realmDiag);
                snap["realm_diag"] = realmDiag;     // 原始数字全留档：gradeID / curGrade / 命中的行
            }
            try { snap["sect_id"] = ud.schoolID; } catch { }
            snap["sect"] = SectName(wub);
            // 性格（NPC 面板「内在性格」「外在性格」）：inTrait=内，outTrait1/2=外（面板显示两条）
            // 查表 g.conf.roleCreateCharacter.GetItem(id).sc5asd_sd34 + GameTool.LS（与游戏自用路径一致）
            snap["personality"] = PersonalityOf(pd);
            // 面板数值一律 **DynInt 优先**（与 stats 的 SetAttr 同口径）：裸字段是不含气运/装备加成的底值。
            // 真机实证：某 NPC 声望裸值 3129、面板 3379 —— 差额 250 正好是其气运「赶尸道童+100 / 单身贵族+150」。
            SetAttr(snap, dyn, pd, "beauty", "beauty");
            SetAttr(snap, dyn, pd, "reputation", "reputation");
            SetAttr(snap, dyn, pd, "talent", "talent");
            SetAttr(snap, dyn, pd, "mood", "mood");
            SetAttr(snap, dyn, pd, "age", "age", true);          // 账面月 → 年（面板口径）
            SetAttr(snap, dyn, pd, "life", "life", true);
            try { snap["power"] = FormulaTool.UnitPower.TotalPower(wub.data); } catch { try { snap["power"] = pd.attack; } catch { } } // 战力=属性+技能+道具综合(UnitPower.TotalPower)

            snap["hobby"] = HobbyNames(wub);
            // 道号（身份）：GetAppellationID() 首个 id -> ConfAppellationTitleBase.GetItem(id).name（反编实锤）
            try
            {
                var at = ud.appellationTitle;
                if (at != null)
                {
                    var ids = at.GetAppellationID();
                    if (ids != null && ids.Count > 0)
                    {
                        string tid = ids[0].ToString();
                        snap["title_id"] = tid;
                        string titleName = "";
                        try
                        {
                            int idNum;
                            if (int.TryParse(tid, out idNum))
                            {
                                var item = g.conf.appellationTitle.GetItem(idNum);
                                if (item != null) titleName = item.name;
                                if (!string.IsNullOrEmpty(titleName)) try { titleName = GameTool.LS(titleName); } catch { }
                            }
                        }
                        catch { }
                        snap["title"] = string.IsNullOrEmpty(titleName) ? "[待真机]道号名(id=" + tid + ")" : titleName;
                    }
                }
            }
            catch { }

            // —— 与玩家关系 / 位置 / 同格 ——
            snap["point"] = new JObject { ["x"] = ud.pointX, ["y"] = ud.pointY };
            string relationEn = "";
            int intim = 0;
            try { intim = rel.GetIntim(player); } catch { }
            try { relationEn = rel.GetRelation(player).ToString(); } catch { }
            snap["relation"] = RelationCn(relationEn);
            snap["intim"] = intim;
            snap["same_grid"] = IsSameGrid(wub, player);

            // —— abilities（功法：id + 组合中文名 + 类型；名经 GetActionMartial -> ConfBattleSkillPrefixName.GetName）——
            if (Has("abilities"))
            {
                var ab = new JObject();
                ab["skill_left"] = MartialSlot(ud, ud.skillLeft, "灵技");
                ab["skill_right"] = MartialSlot(ud, ud.skillRight, "绝技");
                ab["step"] = MartialSlot(ud, ud.step, "身法");
                ab["ultimate"] = MartialSlot(ud, ud.ultimate, "神通");
                var abilityArr = new JArray();
                try { foreach (var id in Arr(ud.abilitys)) abilityArr.Add(MartialSlot(ud, id, "心法")); } catch { }
                ab["abilitys"] = abilityArr;
                snap["abilities"] = ab;
            }

            // —— inventory（分类背包：按 PropsType 分五类，类内按单价 worth 降序，小背包全显/大背包每类 top-N
            //     + 杂项聚合 misc；灵石走 money 字段不再进 props；equips 带单价 worth）——
            if (Has("inventory"))
            {
                var inv = new JObject();
                // ★ 先清洗再往下传 ★：回显键（cats/find）与筛选逻辑必须用**同一份**值，
                // 否则会出现 `find:"  "` 时回显了「查找  」、实际却在查全部的自相矛盾。
                // `"".IndexOf("")` 恒为 0 —— 空白串不清掉就等于"查找空串"⇒ 匹配全部道具。
                string f2 = invFind == null ? null : invFind.Trim();
                if (string.IsNullOrEmpty(f2)) f2 = null;
                var c2 = new List<string>();
                if (invCats != null)
                    foreach (var c in invCats)
                    {
                        string t = c == null ? null : c.Trim();
                        if (!string.IsNullOrEmpty(t)) c2.Add(t);
                    }
                if (c2.Count == 0) c2 = null;
                // [{cat,items:[{name,count,worth,total}...],misc:{kinds,pieces,worth}|null}...]
                // 11-10：c2/f2 走定向档，默认（都为空）与旧行为逐字节一致
                inv["props"] = PropsGroups(ud, inventoryTop, c2, f2);
                // 定向档的回显：让模型确认"这次到底按什么口径查的"（也便于回归时一眼看出走没走新路径）。
                // 只在真的用了定向参数时出现，默认路径不新增任何键。
                if (c2 != null) inv["cats"] = new JArray(c2.ToArray());
                if (f2 != null) inv["find"] = f2;
                inv["equips"] = EquipsDetails(ud);            // 身上装备 [{name,worth}...]（唯一实例）
                // 灵石：道具 ID=10001（GetPropsNum），兜底 totalSchoolMoney 字段；economy_item 赠送灵石同源
                int money = -1;
                try { money = ((DataProps)ud.propData).GetPropsNum(10001); } catch { }
                if (money < 0) { try { money = ud.propData.totalSchoolMoney; } catch { } }
                inv["money"] = money < 0 ? 0 : money;
                snap["inventory"] = inv;
            }

            // —— 气运（brief：先天气运 born + 后天气运 added/all，后天随逆天改命/奇遇动态变化）。
            //     门控在 brief：inventory 等按需块不再混入；get_context L1 走 GameContext 传 ["brief"] 取用，
            //     L1 空 classes（每轮一次的自动快照）仍不含，保持最热路径轻量 ——
            if (Has("brief"))
                snap["luck"] = LuckFromWorld(wub, pd.bornLuck, pd.addLuck);  // 优先 wub.allLuck(luckConf.name/tips)，回退 bornLuck/addLuck+查表

            // —— relationships（关系簿：unitID 转中文名；容器与 RelationNetwork 候选范围对齐：
            //     十容器 + 好友簿 friend_units + 仇人簿 enemy_units）——
            if (Has("relationships"))
            {
                var rl = new JObject();
                try { rl["parent"] = UnitNames(rel.parent); } catch { }
                try { rl["children"] = UnitNames(rel.children); } catch { }
                try { rl["brother"] = UnitNames(rel.brother); } catch { }
                try { rl["parent_back"] = UnitNames(rel.parentBack); } catch { }     // 义父母
                try { rl["children_back"] = UnitNames(rel.childrenBack); } catch { } // 义子女
                try { rl["brother_back"] = UnitNames(rel.brotherBack); } catch { }   // 结义
                try { rl["lover"] = UnitNames(rel.lover); } catch { }
                try { rl["master"] = UnitNames(rel.master); } catch { }
                try { rl["student"] = UnitNames(rel.student); } catch { }
                try
                {
                    string mid = rel.married;
                    if (string.IsNullOrEmpty(mid)) rl["married"] = "";
                    else
                    {
                        string mn = "";
                        try { var u = g.world.unit.GetUnit(mid); if (u != null) mn = u.data.unitData.propertyData.GetName(); } catch { }
                        // 同 UnitNames：道侣也带 unit_id（这是最需要精确指人的一个关系）
                        // ★ 取不到名字时 **name 留空，绝不拿 unitID 顶替**（见 `UnitNames` 的说明）
                        rl["married"] = new JObject
                        {
                            ["name"] = mn ?? "",
                            ["unit_id"] = mid,
                        };
                    }
                }
                catch { }
                try { rl["friend_units"] = IntimBookNames(rel, true); } catch { }
                try { rl["enemy_units"] = IntimBookNames(rel, false); } catch { }   // 仇人簿（types2.txt:82-84 与 friendUnits 并列实锤）
                // 人情：GetHumanValue(toUnitID) 语义是"我对 toUnit 的人情"（types.txt:704 参数名实锤）——
                // 必须传玩家 unitID；原传自身 ID 等于查"对自己的人情"，真机恒 0
                try { rl["human_value"] = rel.GetHumanValue(player.data.unitData.unitID); } catch { }
                snap["relationships"] = rl;
            }

            // tasks 已按需求移除，不再返回

            // —— stats（数值汇总）——
            if (Has("stats"))
            {
                var st = new JObject();
                try { st["power"] = pd.attack; } catch { }
                // 动态攻击（含功法/装备/状态加成，面板同口径）；明确键名 attack——
                // power 在 brief 里=UnitPower 综合战力，同名歧义由 attack 键消除（power 键保留兼容）
                // dyn 已在 brief 段取好（DynInt 面板口径），这里复用同一份
                int? atkDyn = DynOf(dyn, "attack");
                if (atkDyn != null) st["attack"] = atkDyn;
                try { st["defense"] = pd.defense; } catch { }
                try { st["hp"] = pd.hp; } catch { }
                try { st["hp_max"] = pd.hpMax; } catch { }
                try { st["energy"] = pd.energy; } catch { }
                try { st["mood"] = pd.mood; } catch { }
                try { st["beauty"] = pd.beauty; } catch { }
                try { st["reputation"] = pd.reputation; } catch { }
                try { st["talent"] = pd.talent; } catch { }
                st["attrs"] = BuildAttrs(dyn, pd);
                BuildHeart(pd, st);
                snap["stats"] = st;
            }

            // —— logs（经历日志：allVitalLogData=重要 / allLogData=常规；重块，仅请求时读）——
            if (Has("logs"))
                snap["logs"] = BuildLogs(ud.unitID);

            return snap;
        }

        /// <summary>
        /// L1「近况」段最多取几条。**这是唯一的调节旋钮** —— 想更长/更短改这一个数：
        /// 每条经历约 30~60 字，6 条 ≈ 350 字；该段是逐段差分的，只在**内容变化**时重发，
        /// 但作为上下文消息会一直留在模型上下文里，所以别开太大。
        /// </summary>
        private const int RecentCap = 6;

        /// <summary>
        /// L1 近况段取数：两桶合并后取**最近 <see cref="RecentCap"/> 条**，转 {text}（带月份前缀）；
        /// 空返回空 JArray。供 GameContext.GetL1 每轮填 raw.recent（Python format_l1_context 消费，
        /// 差分机制自动只发变化段）。
        ///
        /// 之前是「vital 前 3 + regular 前 2」的按桶配额，真机上退化成恒 2 条（重要桶恒空）；
        /// 之前顺序也未对齐，「近况」实际喂的是开局那几条（如「初入八荒。」）。
        /// 两处历史坑的细节都留在方法体内。
        /// </summary>
        public static JArray RecentTexts(string unitID)
        {
            var outArr = new JArray();
            try
            {
                var logs = BuildLogs(unitID);
                if (logs == null) return outArr;
                // 改：按桶配额 → **合并后取最近 RecentCap 条**
                //
                // 旧写法 `limits = {3, 2}`（vital 前 3 + regular 前 2）在真机上**退化成永远只有 2 条**：
                // Player.log 的 `分层[...]` 自检行显示所有查过的 NPC 都是 `vital=0, regular=5..15`
                // —— 重要桶恒空，于是 3 那个配额从不生效，只剩 regular 的 2 条。用户直接问
                // "它有很多经历，不是说默认三条吗，怎么只显示了两条"。
                //
                // 现在：两桶合并 → 按账面月降序 → 去重 → 取最近 RecentCap 条。理由
                //   ① 「近况」的语义就是"最近发生了什么"，按时间取比按桶配额更符合直觉；
                //   ② 按桶配额会出现"一条陈旧的要事挤掉三条新的日常"，读起来时间线是断的；
                //   ③ 要事不会丢：它通常本身就是最新的，且 `inspect_unit(logs, log_filter=important)`
                //      才是查要事的正路（那边有分页 + 月份区间过滤）。
                // 去重按 (月,文本)：两桶可能各存一条同月同文的（`LayerOverlap` 在盯这件事）。
                //
                // 顺序：`LogsArr` 已保证两桶都是**新→旧**，这里再按 month 显式降序排一次
                // （不依赖上游顺序 —— 上游一旦改方向，这里不会跟着烂）。
                var merged = new List<JObject>();
                var seen = new HashSet<string>();
                foreach (var key in new[] { "vital", "regular" })
                {
                    if (!(logs[key] is JArray arr)) continue;
                    foreach (var tok in arr)
                    {
                        if (!(tok is JObject it)) continue;
                        string t = (string)it["text"] ?? "";
                        if (t.Length == 0 || t == "(无文本)") continue;
                        int mm = 0;
                        int.TryParse((string)it["month"] ?? "", out mm);
                        if (!seen.Add(mm + "|" + t)) continue;
                        merged.Add(new JObject { ["m"] = mm, ["text"] = t });
                    }
                }
                merged.Sort((a, b) => ((int)b["m"]).CompareTo((int)a["m"]));
                int take = Math.Min(RecentCap, merged.Count);
                for (int i = 0; i < take; i++)
                {
                    var o = merged[i];
                    int mm = (int)o["m"];
                    // 账面月 → N年M月（规则单一来源见 CnYearMonth：玩家消息时间戳共用同一条）
                    string ms = mm > 0 ? CnYearMonth(mm) : "";
                    outArr.Add(new JObject
                    {
                        ["text"] = (ms.Length > 0 ? "(" + ms + ") " : "") + (string)o["text"]
                    });
                }
            }
            catch { }
            return outArr;
        }

        /// <summary>
        /// 经历日志：优先 g.world.unitLog.GetLogDataSync，空则回退 g.data.unitLog.allLog[unitID]（人物故事真实源，dump L33215）。
        /// 真机实证（A06-D）：同会话前两次访问读到空、第三次读到 3 条——LogData 的
        /// allLogData getter 是"原始层+解码缓存+lastUpdateMonth 脏标记"结构（types16_worldlog L4），
        /// 首访问可能命中未就绪的解码缓存。故主路径/回退路径各做最多 2 次读取，空结果立即重读一次。
        /// </summary>
        public static JObject BuildLogs(string unitID)
        {
            // ★ 耗时观测（11-20）★
            // 为什么必须打：此前判断"经历读取到底多贵"只能拿 `disp.job` 的窗口耗时反推，
            // 而那个窗口里还混着 get_context 的其余全部工作 ⇒ 推出来的数字**证实不了**。
            // 这一行把它变成直接测量（真机跑一轮就能定论，不必再猜）。
            // 外层套一层而不是在各 return 处埋点：本方法有 4 个出口（含异常兜底），
            // 逐个埋必然漏一个，而漏的那个恰好会是慢路径。
            long __tp = TickProfiler.Begin();
            try
            {
                var o = BuildLogsCore(unitID);
                if (!string.IsNullOrEmpty(unitID))
                {
                    try
                    {
                        int n = ((o["vital"] as JArray)?.Count ?? 0) + ((o["regular"] as JArray)?.Count ?? 0);
                        ModMain.P("[Logs] BuildLogs 耗时 " + TickProfiler.ElapsedMs(__tp).ToString("F1")
                                  + "ms unitID=" + unitID + " 条数=" + n);
                    }
                    catch { }
                }
                return o;
            }
            finally { TickProfiler.End("logs.build", __tp); }
        }

        private static JObject BuildLogsCore(string unitID)
        {
            var o = new JObject();
            if (string.IsNullOrEmpty(unitID)) { ModMain.P("[Logs] BuildLogs unitID 为空，直接返回"); return o; }
            ModMain.P("[Logs] BuildLogs entry unitID=" + unitID);
            // 主路径：g.world.unitLog，双次读取容脏标记；已稳 4→4，可两轮即返
            for (int attempt = 0; attempt < 2; attempt++)
            {
                try
                {
                    var ld = g.world.unitLog.GetLogDataSync(unitID);
                    if (ld != null)
                    {
                        bool rawV, rawR;
                        var vital = LogsArr(GetLogList(ld, true, out rawV), rawV);
                        var regular = LogsArr(GetLogList(ld, false, out rawR), rawR);
                        if (vital.Count > 0 || regular.Count > 0)
                        {
                            ModMain.P("[Logs] BuildLogs 命中主路 unitID=" + unitID + " vital=" + vital.Count + " regular=" + regular.Count + " attempt=" + attempt + LayerInfo(ld) + LayerOverlap(vital, regular) + OrderInfo(vital, regular));
                            return new JObject { ["vital"] = vital, ["regular"] = regular };
                        }
                    }
                }
                catch { }
            }
            // 回退：g.data.unitLog.allLog（与主路同值，兜底早期档）
            for (int attempt = 0; attempt < 2; attempt++)
            {
                try
                {
                    var dict = (dynamic)g.data.unitLog.allLog;
                    if (dict != null && dict.ContainsKey(unitID))
                    {
                        var ld2 = dict[unitID];
                        if (ld2 != null)
                        {
                            bool rawV2, rawR2;
                            var vital = LogsArr(GetLogList(ld2, true, out rawV2), rawV2);
                            var regular = LogsArr(GetLogList(ld2, false, out rawR2), rawR2);
                            if (vital.Count > 0 || regular.Count > 0)
                            {
                                ModMain.P("[Logs] BuildLogs 命中回退 unitID=" + unitID + " vital=" + vital.Count + " regular=" + regular.Count + " attempt=" + attempt + LayerInfo(ld2) + OrderInfo(vital, regular));
                                return new JObject { ["vital"] = vital, ["regular"] = regular };
                            }
                        }
                    }
                }
                catch { }
            }
            // 第三路（新增）：**暂存层** g.world.unitLog.GetCacheWriteLog(unitID)。
            //
            // 根因链（dump types16_worldlog L733-762 实证）：经历不是直接落库的——
            //   AddLogData/AddVitalLogData(unit,…) → WorldUnitLogMgr.allAddLogData[unitID]
            //     （元素 WorldUnitLogMgr.LogData{unitID, logData:LogItemData, mergeData}，**待写暂存**）
            //   → 私有 UpdateLogData()/SaveLogData() → WriteLogData(…, allAddLogData, …) → MerageAddLog(unitID, logs, month, isBig)
            //   → 才合并进 DataUnitLog.LogData.allLog / allVitalLog（已落库）
            // 而 `GetLogDataSync` 只返回**已落库**的 DataUnitLog.LogData —— 注意两个同名但不同的类型：
            //   GetLogDataSync  → DataUnitLog.LogData（allLog/allVitalLog/_allLogData/…）
            //   GetCacheWriteLog→ List<WorldUnitLogMgr.LogData>（unitID/logData/mergeData）本路
            // 所以"本月刚写、还没过月/存档触发合并"的经历，前两路必然读不到。
            // 真机证据：NPC unitID=MTpQXJ 四层全 0 而 allLog.ContainsKey=True（槽位在、条目没落库）；
            // 同会话玩家 FJLlLl 有 vital=1/regular=1（创角日志已落库）。路径全工程此前从未调用。
            //
            // 只在前两路都空时兜底（此时不存在重复计数）；暂存层拿不到重要/常规分流，统一进 regular。
            try
            {
                var pend = g.world.unitLog.GetCacheWriteLog(unitID);   // List<WorldUnitLogMgr.LogData>
                if (pend != null)
                {
                    int pn = pend.Count;   // IL2CPP 集合禁 foreach，用 Count + 索引器
                    var regularP = new JArray();
                    string pidP = PlayerUnitId();   // 与玩家相关判定：同主路一条判据，不许各写一份
                    for (int i = 0; i < pn; i++)
                    {
                        object item = null;
                        try { var e = pend[i]; if (e != null) item = (object)e.logData; } catch { }
                        if (item == null) continue;
                        var jo = new JObject();
                        try { jo["month"] = ((dynamic)item).month; } catch { }
                        string txt = "";
                        try { txt = LogItemText(item); } catch { }
                        jo["text"] = string.IsNullOrEmpty(txt) ? "(无文本)" : txt;
                        if (MentionsPlayer(item, pidP)) jo["with_player"] = true;
                        regularP.Add(jo);
                    }
                    if (regularP.Count > 0)
                    {
                        ModMain.P("[Logs] BuildLogs 命中暂存层 unitID=" + unitID + " pending=" + regularP.Count + OrderInfo(new JArray(), regularP));
                        return new JObject { ["vital"] = new JArray(), ["regular"] = regularP };
                    }
                }
            }
            catch (Exception ePend) { ModMain.P("[Logs] BuildLogs 暂存层异常 unitID=" + unitID + ": " + ePend.Message); }
            o["vital"] = new JArray();
            o["regular"] = new JArray();
            ModMain.P("[Logs] BuildLogs 空结果 unitID=" + unitID + "（主路+回退+暂存均空）");
            // 诊断（分层证据，真机排查用）：unitID / 主路径对象与两层条数 / 回退字典命中与总键数
            try
            {
                string ev = "[Logs] BuildLogs 空结果 unitID=" + unitID;
                try
                {
                    var ld = g.world.unitLog.GetLogDataSync(unitID);
                    if (ld == null) ev += " GetLogDataSync=null";
                    else
                    {
                        var dyn = (dynamic)ld;
                        int v = 0, r = 0, rv = 0, rr = 0;
                        try { v = (int)((dynamic)dyn.allVitalLogData).Count; } catch { }
                        try { r = (int)((dynamic)dyn.allLogData).Count; } catch { }
                        try { rv = (int)((dynamic)dyn.allVitalLog).Count; } catch { }
                        try { rr = (int)((dynamic)dyn.allLog).Count; } catch { }
                        ev += " GetLogDataSync=obj(解码层vital=" + v + ",regular=" + r + " | 原始层vital=" + rv + ",regular=" + rr + ")";
                    }
                }
                catch (Exception e) { ev += " GetLogDataSync=ERR:" + e.Message; }
                try
                {
                    var dict = (dynamic)g.data.unitLog.allLog;
                    if (dict == null) ev += " allLog=null";
                    else
                    {
                        bool has = false;
                        try { has = dict.ContainsKey(unitID); } catch { }
                        ev += " allLog.ContainsKey=" + has;
                    }
                }
                catch (Exception e) { ev += " allLog=ERR:" + e.Message; }
                // 暂存层条数（第三路的证据行：>0 说明"写了没落库"，=0 说明该单位确实还没有经历）
                try
                {
                    var pend = g.world.unitLog.GetCacheWriteLog(unitID);
                    ev += " GetCacheWriteLog=" + (pend == null ? "null" : ((int)((dynamic)pend).Count).ToString());
                }
                catch (Exception e) { ev += " GetCacheWriteLog=ERR:" + e.Message; }
                // 玩家身份对照：判定解析到的 unitID 是玩家本人还是同名 NPC（玩家 LogData 桶数一并列出）
                try
                {
                    var pu = g.world.playerUnit;
                    if (pu != null)
                    {
                        string pid = pu.data.unitData.unitID;
                        string pname = pu.data.unitData.propertyData.GetName();
                        var pld = g.world.unitLog.GetLogDataSync(pid);
                        int pv = 0, pr = 0;
                        try { pv = (int)((dynamic)pld.allVitalLogData).Count; } catch { }
                        try { pr = (int)((dynamic)pld.allLogData).Count; } catch { }
                        ev += " | player(unitID=" + pid + ",name=" + pname + ",vital桶=" + pv + ",regular桶=" + pr + ")";
                    }
                }
                catch (Exception e) { ev += " player=ERR:" + e.Message; }
                UnityEngine.Debug.Log(ev);
            }
            catch { }
            return o;
        }

        /// <summary>
        /// 取某单位的经历条目列表（解码层优先）。<paramref name="raw"/>=true 表示返回的是**原始层**
        /// （元素 <c>Il2CppStringArray</c>，必须走 <see cref="DecodeRawLogRow"/> 才能出文本）。
        ///
        /// 定案 两层的真实类型（读编译目标 <c>MelonLoader\Managed\Assembly-CSharp.dll</c> 实证）：
        /// <code>
        /// DataUnitLog/LogData:
        ///   P List&lt;Il2CppStringArray&gt; allLog / allVitalLog        ← 原始层：一行 = 编码串切段
        ///   P List&lt;LogItemData&gt;       allLogData / allVitalLogData ← 解码层（只读 getter，属性）
        ///   P List&lt;LogItemData&gt;       _allLogData / _allVitalLogData ← 解码缓存本体
        /// </code>
        /// 写的"原始层优先（allLog 是写入端直接落的 LogItemData）"是**误诊**：原始层元素是
        /// <c>string[]</c>，既没有 <c>month</c> 也没有 <c>logs</c> 更没有 <c>DataToString</c>——
        /// <see cref="LogsArr"/> 里三处取值全抛异常（三处都裹在 catch 里），于是**每一条**都退化成
        /// "(无文本)"。真机铁证：<c>PageLogs 输出={"filter":"all","items":[{"text":"(无文本)"}×3],...}</c>
        /// ——注意条目里连 <c>month</c> 字段都没有，只有"赋值抛异常"才会整个字段缺失。
        /// 解码层才是唯一能出人话的层：实测 <c>Data.GetLogString</c> →
        /// "邀请@q_唐炎|BOlQu6@进行论道，但却被对方拒绝了！"；游戏自己的经历面板
        /// <c>UINPCInfoLog.logDatas</c> 同为 <c>List&lt;LogItemData&gt;</c>（同一层）。
        /// </summary>
        private static object GetLogList(object ld, bool vital, out bool raw)
        {
            raw = false;
            try
            {
                var dyn = (dynamic)ld;
                // 解码层两条路都要求 Count&gt;0 才返回：空解码层必须能落到原始层兜底
                // （09-12 原写法 `if (v != null) return v;` 会把空列表直接返回，兜底永远走不到）。
                if (vital)
                {
                    try { var v = dyn.allVitalLogData; if (v != null && (int)v.Count > 0) return v; } catch { }
                    try { var v = dyn._allVitalLogData; if (v != null && (int)v.Count > 0) return v; } catch { }
                    try { var v = dyn.allVitalLog; if (v != null && (int)v.Count > 0) { raw = true; return v; } } catch { }
                }
                else
                {
                    try { var v = dyn.allLogData; if (v != null && (int)v.Count > 0) return v; } catch { }
                    try { var v = dyn._allLogData; if (v != null && (int)v.Count > 0) return v; } catch { }
                    try { var v = dyn.allLog; if (v != null && (int)v.Count > 0) { raw = true; return v; } } catch { }
                }
            }
            catch { }
            return null;
        }

        /// <summary>原始层兜底：一行 <c>Il2CppStringArray</c> = 编码串按 '&' 切段，拼回后交游戏自己的
        /// <c>LogItemData.StringToData</c> 还原成 {month,logs,subLogs}，再走与解码层同一条渲染路径。
        /// 还原不出文本时返回 ""（同时打一行原始内容，供下一轮定位格式）。</summary>
        private static string DecodeRawLogRow(object row, JObject jo)
        {
            var segs = new List<string>();
            try
            {
                var d = (dynamic)row;
                int n = 0;
                try { n = (int)d.Length; } catch { try { n = (int)d.Count; } catch { } }   // Il2CppStringArray 两者皆有
                for (int i = 0; i < n; i++) { string s = (string)d[i]; if (s != null) segs.Add(s); }
            }
            catch { }
            if (segs.Count == 0) return "";
            string joined = string.Join("&", segs);
            try
            {
                var li = new DataUnitLog.LogData.LogItemData();   // 无参 ctor 已由 Unhollower 生成
                li.StringToData(joined);
                try { jo["month"] = li.month; } catch { }
                string t = LogItemText(li);
                // 中文兜底校验：拼错分隔符时 StringToData 可能"成功"吐出编码残渣，
                // 那种东西进上下文比"(无文本)"更糟，故只认含汉字的还原结果。
                if (!string.IsNullOrEmpty(t) && HasCjk(t)) return t;
                ModMain.P("[Logs] 原始层行还原可疑（无可读汉字）raw=" + Short(joined, 160) + " → " + Short(t, 80));
            }
            catch (Exception e) { ModMain.P("[Logs] 原始层行还原异常: " + e.Message + " raw=" + Short(joined, 160)); }
            return "";
        }

        private static bool HasCjk(string s)
        {
            for (int i = 0; i < s.Length; i++) if (s[i] >= 0x4E00 && s[i] <= 0x9FFF) return true;
            return false;
        }

        /// <summary>分层证据行：解码层/原始层各自条数。两层不相等 = 解码缓存落后于原始层
        /// （AddLog 写了 allLog 但脏标记没触发重建）——这一行直接回答"有没有条目被吞掉"。</summary>
        private static string LayerInfo(object ld)
        {
            string s = "";
            try
            {
                var d = (dynamic)ld;
                int dv = -1, dr = -1, rv = -1, rr = -1;
                try { dv = (int)((dynamic)d.allVitalLogData).Count; } catch { }
                try { dr = (int)((dynamic)d.allLogData).Count; } catch { }
                try { rv = (int)((dynamic)d.allVitalLog).Count; } catch { }
                try { rr = (int)((dynamic)d.allLog).Count; } catch { }
                s = " 分层[解码 vital=" + dv + ",regular=" + dr + " | 原始 vital=" + rv + ",regular=" + rr + "]";
                if (dv >= 0 && rv >= 0 && dr >= 0 && rr >= 0 && (dv != rv || dr != rr)) s += " ★两层不等";
            }
            catch { }
            return s;
        }

        private static string Short(string s, int n)
        {
            if (string.IsNullOrEmpty(s)) return "";
            return s.Length <= n ? s : s.Substring(0, n) + "…(" + s.Length + ")";
        }

        /// <summary>
        /// 分层**包含关系**判据：vital 的 (month,text) 是否全部也出现在 regular 里。
        ///
        /// 为什么需要这一行——`LogData` 的两层由两个独立暂存字典写入
        /// （`WorldUnitLogMgr.allAddLogData` / `allAddVitalLogData` → `MerageAddLog(unitID, logs, month, isBig)`），
        /// 于是"一件大事"有两种可能的落库方式：
        ///   (A) 只进 `allVitalLog`（两层互斥）→ `log_filter=all` 取并集正确；
        ///   (B) 同时进 `allLog` 和 `allVitalLog`（`allLog` 是**含大事的全量时间线**，游戏面板
        ///       「全部」页签直接用它）→ 并集会把每条大事列两遍。
        /// 真机现象（玩家 缪嘉歆，开局首月）：「初入八荒。」在两桶各有一条，`all` 输出里出现两次。
        /// 仅凭"想当然"无法区分 A/B——**这一行就是判据**：若恒为 `vital⊆regular`，则 (B) 成立，
        /// `all` 应只取 regular；只要出现一次不包含，就是 (A)，并集正确。
        /// </summary>
        private static string LayerOverlap(JArray vital, JArray regular)
        {
            try
            {
                if (vital == null || regular == null || vital.Count == 0 || regular.Count == 0) return "";
                var set = new HashSet<string>();
                foreach (var it in regular) set.Add(ItemKey(it));
                int hit = 0;
                foreach (var it in vital) if (set.Contains(ItemKey(it))) hit++;
                string s = " 重叠=" + hit + "/" + vital.Count;
                if (hit == vital.Count) s += "（vital⊆regular → allLog 可能含大事，all 会重复列出）";
                return s;
            }
            catch { return ""; }
        }

        private static string ItemKey(JToken it)
        {
            string t = "", m = "";
            try { t = (string)((JObject)it)["text"] ?? ""; } catch { }
            try { m = ((JObject)it)["month"] == null ? "" : ((JObject)it)["month"].ToString(); } catch { }
            return m + "|" + t;
        }

        /// <summary>
        /// 条目列表 → JArray，**倒序输出（新 → 旧）**。
        ///
        /// 顺序对齐 游戏这份列表是**升序（旧→新）**，真机实证：`regular` 桶里
        /// 「初入八荒。」（创角）排在「与云含进行交谈…」**之前**，而后者逻辑上必然更晚；
        /// 且全链路（本函数 / `PageLogs` / 渲染层）此前没有任何排序，顺序就是库里的顺序。
        /// 但下游两个消费者都是按【头 = 最新】写的：
        ///   · `RecentTexts` 取 `arr[0..2]`/`arr[0..1]` 喂 L1 上下文的**「近况」**段 ——
        ///     顺序不对时模型每轮看到的是「近况：(1年1月) 初入八荒。」这种陈年旧事；
        ///   · `QueryTool.PageLogs` 按 `(page-1)*5` 从**头**切片，而 `log_page` 默认 1 ——
        ///     顺序不对时第 1 页永远是开局那几条，最近的经历得翻到最后一页。
        /// 所以在**这一处**反转，让"头 = 最新"成为全链路真命题（与 `QueryWorldEvents` 既有的
        /// `倒序 = 最近在前` 约定一致）。要按时间正序读完整生平，用 `log_since_month` 圈范围。
        /// 方向由 `OrderInfo` 每次命中时自检打印（期望 `降`）——游戏若改了写入方向会立刻显形。
        /// </summary>
        private static JArray LogsArr(object list, bool raw = false)
        {
            var ja = new JArray();
            if (list == null) return ja;
            try
            {
                var dyn = (dynamic)list;
                int cnt = (int)dyn.Count;
                // 「与玩家相关」判定要在**此处**取玩家身份：取不到（未进世界）就一律不打标，
                // 宁缺勿错 —— 绝不许退回按玩家真名匹配（本作有同名 NPC，会张冠李戴）。
                string pid = PlayerUnitId();
                for (int i = cnt - 1; i >= 0; i--)   // ← 倒序：库里旧→新，输出新→旧
                {
                    var item = dyn[i];
                    if (item == null) continue;
                    var jo = new JObject();
                    if (raw)
                    {
                        string t0 = DecodeRawLogRow(item, jo);
                        jo["text"] = string.IsNullOrEmpty(t0) ? "(无文本)" : t0;
                        if (RawRowHasPid(item, pid)) jo["with_player"] = true;
                        ja.Add(jo);
                        continue;
                    }
                    try { jo["month"] = item.month; } catch { }
                    string txt = "";
                    try { txt = LogItemText(item); } catch { try { txt = CleanLogText((string)((dynamic)item).DataToString()); } catch { } }
                    jo["text"] = string.IsNullOrEmpty(txt) ? "(无文本)" : txt;
                    // 与玩家相关（游戏 NPC 面板·经历页「与我相关」页签同语义）。判定必须发生在
                    // CleanLogText **之前** —— LogItemText 返回的是已渲染文本，`@q_唐炎|BOlQu6@`
                    // 里的 unitID 已被 RenderAtTag 换成人话，渲染后再判就只剩名字了。
                    // 不命中不写键（缺键 = false），48 条条目不必每条都背一个布尔键。
                    if (MentionsPlayer(item, pid)) jo["with_player"] = true;
                    ja.Add(jo);
                }
            }
            catch { }
            return ja;
        }

        // ------------------------------------------------------------------
        // 「与玩家相关」判定（游戏 NPC 面板·经历页「与我相关」页签同语义）
        //
        // 游戏侧锚点：LocalText id=23183 `key=npcInfo_yuwoxiangguan` =「与我相关」；
        // 实现是 UINPCInfoLog 的私有方法 `GetLogWithMe(List<LogItemData>)` + 谓词
        // `_GetLogWithMe_b__22_0`。谓词挂在 `<>c` 的静态缓存委托 `<>9__22_0` 上、
        // 没有对应 DisplayClass ⇒ **不捕获任何外部状态**，只能看条目自身 + 全局玩家单位。
        // 该实现体没有反编出来（types_UINPCInfo.txt / types49_uinpcinfo.txt 都只有签名），
        // 所以这里**不照抄它**，而是按下面这条可直接验证的判据自判。
        //
        // ★ 判据（全仓唯一，勿在别处再写一份）★
        //   在 **CleanLogText 之前**的原始文本里，某个 `@` 标记的某个**竖线字段精确等于玩家 unitID**。
        //   真机铁证：`@q_唐炎|BOlQu6@` —— `q` 类标记 221/221 都是「字段0=显示名(关系)、字段1=unitID」，
        //   而 `BOlQu6` 即玩家（load_happened world=BOlQu6 / portrait_cache\BOlQu6.png /
        //   玩家名="唐炎" 三处互证）。
        //
        // ★ 为什么必须用 unitID、绝不许按玩家真名匹配 ★
        //   本作存在与玩家同名的 NPC（UnitLookup 09-12 实锤），按名匹配会把别人的经历算到玩家头上。
        //
        // ★ 为什么必须在这一层判 ★
        //   `LogItemText` → `TryDataString` → `CleanLogText` 会把 `@q_唐炎|BOlQu6@` 渲染成 `唐炎`，
        //   unitID 在那一刻蒸发 —— 渲染之后再判就只剩名字了，链路上没有回头路。
        // ------------------------------------------------------------------

        /// <summary>这条经历是否涉及玩家。<paramref name="pid"/> 取不到一律 false —— **不猜**，宁缺勿错。</summary>
        private static bool MentionsPlayer(object item, string pid)
        {
            if (string.IsNullOrEmpty(pid) || item == null) return false;
            try
            {
                var dynItem = (dynamic)item;
                if (DataListHasPid(dynItem.logs, pid)) return true;
                if (DataListHasPid(dynItem.subLogs, pid)) return true;
            }
            catch { }
            return false;
        }

        /// <summary>遍历 `logs[]` / `subLogs[]`，任一 `Data` 命中即真。IL2CPP 集合禁 foreach，用 Count + 索引器。</summary>
        private static bool DataListHasPid(object list, string pid)
        {
            if (list == null || string.IsNullOrEmpty(pid)) return false;
            try
            {
                var dl = (dynamic)list;
                int n = 0;
                try { n = (int)dl.Count; } catch { }
                for (int i = 0; i < n; i++)
                {
                    object d = null;
                    try { d = dl[i]; } catch { }
                    if (d == null) continue;
                    if (FieldHasPid(RawDataText(d), pid)) return true;   // 渲染文本（仍带 @ 标记）
                    if (ValuesHasPid(d, pid)) return true;               // 原始 values 数组（更便宜的一条路）
                }
            }
            catch { }
            return false;
        }

        /// <summary>未清洗原文：`GetLogString()`（仍带 @ 标记）优先，`DataToString()`（序列化）兜底。</summary>
        private static string RawDataText(object d)
        {
            try { string s = ((dynamic)d).GetLogString(); if (!string.IsNullOrEmpty(s)) return s; } catch { }
            try { string v = ((dynamic)d).DataToString(); if (!string.IsNullOrEmpty(v)) return v; } catch { }
            return "";
        }

        /// <summary>`Data.values` 直查：某段**精确等于** pid。注意是相等不是包含。</summary>
        private static bool ValuesHasPid(object d, string pid)
        {
            try
            {
                var vals = (dynamic)((dynamic)d).values;
                if (vals == null) return false;
                int n = 0;
                try { n = (int)vals.Length; } catch { try { n = (int)vals.Count; } catch { } }
                for (int i = 0; i < n; i++)
                {
                    string s = null;
                    try { s = (string)vals[i]; } catch { }
                    if (s == pid) return true;
                }
            }
            catch { }
            return false;
        }

        /// <summary>
        /// 逐个 `@…@` 标记、逐个竖线字段做**精确相等**比较。
        /// 不用 `Contains` 子串：unitID 是 6 位 base62，子串误伤率虽低，但字段级相等是零误伤的判法，
        /// 也天然兼容 5 字段（`@w_zcW8xn|1|1011111|13|@`）与 19 字段（`@w_gdZZA1|2|…|@`）两种形态。
        /// </summary>
        private static bool FieldHasPid(string s, string pid)
        {
            if (string.IsNullOrEmpty(s) || string.IsNullOrEmpty(pid)) return false;
            int at = 0;
            while (true)
            {
                int a = s.IndexOf('@', at);
                if (a < 0) break;
                int b = s.IndexOf('@', a + 1);
                if (b < 0) break;
                string chunk = s.Substring(a + 1, b - a - 1);
                int p = 0;
                while (p <= chunk.Length)
                {
                    int bar = chunk.IndexOf('|', p);
                    string field = bar < 0 ? chunk.Substring(p) : chunk.Substring(p, bar - p);
                    if (field == pid) return true;
                    if (bar < 0) break;
                    p = bar + 1;
                }
                at = b + 1;
            }
            return false;
        }

        /// <summary>原始层一行（Il2CppStringArray）：编码串切段后判玩家引用。与 <see cref="DecodeRawLogRow"/> 同一条切段逻辑，但不落文本、只做判定。</summary>
        private static bool RawRowHasPid(object row, string pid)
        {
            if (row == null || string.IsNullOrEmpty(pid)) return false;
            try
            {
                var d = (dynamic)row;
                int n = 0;
                try { n = (int)d.Length; } catch { try { n = (int)d.Count; } catch { } }
                var segs = new List<string>();
                for (int i = 0; i < n; i++)
                {
                    string s = null;
                    try { s = (string)d[i]; } catch { }
                    if (s != null) segs.Add(s);
                }
                if (FieldHasPid(string.Join("&", segs), pid)) return true;
                foreach (var s in segs) if (s == pid) return true;
            }
            catch { }
            return false;
        }

        /// <summary>
        /// L1「与玩家相关」段最多取几条。**独立旋钮**，与 <see cref="RecentCap"/> 互不牵连：
        /// 每条经历约 30~60 字，6 条 ≈ 350 字；该段随上下文常驻，别开太大。
        /// </summary>
        private const int RecentPlayerCap = 6;

        /// <summary>
        /// L1「与玩家相关」段取数：两桶合并 → **只留 <c>with_player</c> 条目** → 按账面月降序 → 去重
        /// → 取最近 <see cref="RecentPlayerCap"/> 条。供 GameContext.GetL1 填 raw.recent_with_player
        /// （Python format_l1_context 消费成「与玩家相关」段；空则整段不渲染，差分只发变化段）。
        ///
        /// 为什么是**独立一段**而不是塞进「近况」（与「当前时间」同款三段论）：
        ///   ① 语义：「近况」= 他最近经历了什么；本段 = **他与玩家之间发生过什么**。两码事，混装难归因。
        ///   ② 成本：段是逐段差分的 —— 他的日常变了不该连累本段重发，反之亦然。
        ///   ③ 覆盖：本段筛的是**全史**里的玩家相关条目（不只最近 N 条），久远的往来不会因为
        ///      近期日常太多而被挤出上下文 —— 这正是游戏「与我相关」页签存在的意义。
        ///
        /// 与「近况」**允许重复**：同一条既新又涉及玩家时两段都出现，信息不丢；
        /// 刻意不互斥 —— 互斥会把「近况」的语义悄悄改写成"与玩家无关的近况"，且条数变相缩水。
        /// </summary>
        public static JArray RecentWithPlayer(string unitID)
        {
            var outArr = new JArray();
            try
            {
                var logs = BuildLogs(unitID);
                if (logs == null) return outArr;
                var merged = new List<JObject>();
                var seen = new HashSet<string>();
                foreach (var key in new[] { "vital", "regular" })
                {
                    if (!(logs[key] is JArray arr)) continue;
                    foreach (var tok in arr)
                    {
                        if (!(tok is JObject it)) continue;
                        bool wp = false;
                        try { wp = (bool)it["with_player"] == true; } catch { }
                        if (!wp) continue;
                        string t = (string)it["text"] ?? "";
                        if (t.Length == 0 || t == "(无文本)") continue;
                        int mm = 0;
                        int.TryParse((string)it["month"] ?? "", out mm);
                        if (!seen.Add(mm + "|" + t)) continue;
                        merged.Add(new JObject { ["m"] = mm, ["text"] = t });
                    }
                }
                merged.Sort((a, b) => ((int)b["m"]).CompareTo((int)a["m"]));
                int take = Math.Min(RecentPlayerCap, merged.Count);
                for (int i = 0; i < take; i++)
                {
                    var o = merged[i];
                    int mm = (int)o["m"];
                    // 账面月 → N年M月（单一来源见 CnYearMonth：与「近况」前缀、玩家消息时间戳共用同一条）
                    string ms = mm > 0 ? CnYearMonth(mm) : "";
                    outArr.Add(new JObject
                    {
                        ["text"] = (ms.Length > 0 ? "(" + ms + ") " : "") + (string)o["text"]
                    });
                }
            }
            catch { }
            return outArr;
        }

        /// <summary>桶内月份方向自检。输出**应为「降」（新→旧）**；打出 `升!` 说明游戏改了写入方向，
        /// 那时 `LogsArr` 的倒序必须撤掉，否则工具第 1 页会退回陈年旧事、L1「近况」段跟着错。
        /// 「同月」= 样本全在同一个月（开局常见，信息量为零，不算异常）。</summary>
        private static string OrderInfo(JArray vital, JArray regular)
        {
            return " 顺序[vital=" + Dir(vital) + " regular=" + Dir(regular) + "]";
        }

        private static string Dir(JArray arr)
        {
            try
            {
                if (arr == null || arr.Count < 2) return "样本不足";
                int up = 0, down = 0;
                bool has = false;
                int prev = 0;
                for (int i = 0; i < arr.Count; i++)
                {
                    int m;
                    try { m = (int)((JObject)arr[i])["month"]; } catch { continue; }   // 无月份的条目不参与
                    if (has)
                    {
                        if (m > prev) up++;
                        else if (m < prev) down++;
                    }
                    prev = m; has = true;
                }
                if (!has || (up == 0 && down == 0)) return "同月";
                if (up > 0 && down > 0) return "乱序!";
                return up > 0 ? "升!" : "降";
            }
            catch { return "?"; }
        }

        /// <summary>
        /// `@&lt;类型字母&gt;_&lt;字段…&gt;@` → 人话。
        ///
        /// **类型字母决定字段含义，不能一律按"字段0 是 ID"处理**（全量实测，见下）：
        ///   · **`q` = 人物引用**，形如 `@q_寇炫明(好友)|cPoLMG@` —— **字段0 就是显示名**，
        ///     字段1 才是 unitID。实机 **221/221 全是这个形态**。所以对 `q` 直接取字段0，
        ///     既正确又是游戏面板的样子（旧的"退回字段0"其实一直是对的，只是日志把它打成了"未解"）。
        ///   · **`w` = 物品/能力引用**，字段0 是 soleID（**对模型是纯噪音**），真名要从表里查：
        ///     - 短形态 `@w_zcW8xn|1|1011111|13|@`（5 字段）→ 数字字段命中 `ItemProps` → `六品培元丹`
        ///     - 长形态 `@w_gdZZA1|2|0|0|4|88003|…|26|@`（19 字段）**没有任何 propsID**，
        ///       但 `88003` 命中 `BattleAbilityBase`（`ability_name_last8` → `大法`）—— 战斗能力引用
        ///   · 其他字母 → 逐字段试两张表，再退字段0（旧行为）。
        ///
        /// **为什么"逐字段试"安全**：`ItemProps` id 从 **10001** 起、`BattleAbilityBase` 从 **101** 起，
        /// 都远大于标记里的类型/序号字段（`1`/`2`/`4`/`48` 之类一律查不到），不会误命中。
        ///
        /// **查不到时不再回裸 soleID**：`gdZZA1` 这种串对模型毫无意义，
        /// 只会污染上下文（它曾经漏进 L1 近况）。改为给明确占位，原样留在日志里备查。
        /// </summary>
        private static string RenderAtTag(System.Text.RegularExpressions.Match m)
        {
            string letter = m.Groups[1].Value;
            string payload = m.Groups[2].Value;
            string field0 = payload;
            int bar = field0.IndexOf('|');
            if (bar >= 0) field0 = field0.Substring(0, bar);
            try
            {
                // ① 人物引用：字段0 本身就是中文显示名（含 (关系) 后缀）
                if (letter == "q")
                {
                    if (!string.IsNullOrEmpty(field0)) { NoteTag(m.Value, field0, "人物"); return field0; }
                }
                string[] fields = payload.Split('|');
                // ② 逐字段试解两张表（道具 → 战斗能力）
                for (int i = 0; i < fields.Length; i++)
                {
                    int id;
                    if (!int.TryParse(fields[i], out id) || id <= 0) continue;
                    string pn = PropsName(id);
                    if (pn.Length > 0) { NoteTag(m.Value, pn, "道具"); return pn; }
                }
                for (int i = 0; i < fields.Length; i++)
                {
                    int id;
                    if (!int.TryParse(fields[i], out id) || id <= 0) continue;
                    string an = AbilityName(id);
                    if (an.Length > 0) { NoteTag(m.Value, an, "能力"); return an; }
                }
                // ③ 字段0 能当 unitID 查到单位（覆盖非 q 的人物型引用）
                if (fields.Length > 0 && !string.IsNullOrEmpty(fields[0]))
                {
                    string un = UnitIdentity.NameOfId(fields[0]);
                    if (un.Length > 0) { NoteTag(m.Value, un, "人物"); return un; }
                }
                // ④ 解不出：物品类**不回裸 soleID**（纯噪音），给明确占位；其他类型保持旧行为
                if (_tagMiss.Count < 40 && _tagMiss.Add(m.Value))
                    ModMain.P("[Logs] @标记未解 " + m.Value + "（类型 " + letter + "，字段 "
                              + fields.Length + " 个，字段0「" + field0 + "」）");
                if (letter == "w") return "（未知道具）";
            }
            catch { }
            return field0;
        }

        /// <summary>
        /// 战斗能力 id → 中文名。`ConfBattleAbilityBaseItem.name`（如 `ability_name_last8`）是
        /// 本地化 key，须过 `GameTool.LS`（→ `大法`）。用途见 <see cref="RenderAtTag"/> 的长形态 `@w_`。
        /// </summary>
        private static string AbilityName(int abilityId)
        {
            if (abilityId <= 0) return "";
            try
            {
                var item = g.conf.battleAbilityBase.GetItem(abilityId);
                if (item == null) return "";
                string n = null;
                try { n = ((dynamic)item).name; } catch { }
                if (string.IsNullOrEmpty(n)) return "";
                try { n = GameTool.LS(n); } catch { }
                return n ?? "";
            }
            catch { return ""; }
        }

        /// <summary>propsID → 中文道具名。`ConfItemProps.name` 是本地化 key（如 `item_name5031101`），须 LS。</summary>
        private static string PropsName(int propsId)
        {
            if (propsId <= 0) return "";
            try
            {
                var item = g.conf.itemProps.GetItem(propsId);
                if (item == null) return "";
                string n = null;
                try { n = ((dynamic)item).name; } catch { }
                if (string.IsNullOrEmpty(n)) return "";
                try { n = GameTool.LS(n); } catch { }
                return n ?? "";
            }
            catch { return ""; }
        }

        // @ 标记样本留痕（去重 + 封顶）：只为积累"游戏到底有哪几种标记"的证据，不参与逻辑。
        private static readonly HashSet<string> _tagSeen = new HashSet<string>();

        /// <summary>解不出的标记去重（同 `_tagSeen` 规约：40 条封顶，防每页刷屏）</summary>
        private static readonly HashSet<string> _tagMiss = new HashSet<string>();
        private static void NoteTag(string raw, string rendered, string kind)
        {
            try
            {
                if (_tagSeen.Count >= 40 || !_tagSeen.Add(raw)) return;
                ModMain.P("[Logs] @标记[" + kind + "] " + raw + " → " + rendered);
            }
            catch { }
        }

        private static string TryDataString(dynamic d)
        {
            try { string s = d.GetLogString(); if (!string.IsNullOrEmpty(s)) return CleanLogText(s); } catch { }
            try { string v = d.DataToString(); if (!string.IsNullOrEmpty(v)) return CleanLogText(v); } catch { }
            return null;
        }

        /// <summary>
        /// 人类可读：`logs[]` + `subLogs[]` 各取 `GetLogString()`，再清 @ 引用标记。
        ///
        /// 两段的连接方式不一样，别顺手统一：
        ///   · **`logs[]` 是同一句话的片段** —— 原文自带标点（`…需求，` / `认为…丹药。` / `在…购买了X。`），
        ///     所以**直接相连**才是游戏面板的显示。原来的 `string.Join("；", parts)` 会插出
        ///     `基于自我成长的需求，；认为…` 这种顿挫，与面板逐字不一致（用户看出来的）。
        ///     铁证：游戏经历面板同一句显示为 `基于自我成长的需求，认为当前的丹药不足够，想获得更多的丹药。在远望镇的坊市中购买了五品培元丹。`
        ///     —— 片段之间**没有任何分隔符**。
        ///   · **`subLogs[]` 是附加明细**（不是主句的一部分），跟在主句后面用 `；` 分隔。
        ///
        /// 删掉了 `DataToString` 那段兜底（用户拍板"链路确定了就别再兜底"）：
        /// 它是**序列化格式**（`id&amp;values&amp;conditionValues` 用 `&amp;` 连接，`StringToData` 是它的逆），
        /// 不是人话 —— 它唯一产出过的东西就是 L1 近况里那条 `0&amp;A&amp;A`。
        /// `GetLogString` 已是实证主路（`命中主路`），片段全空时**返回空串**（如实"这条读不出"），
        /// 绝不再拿序列化残渣冒充正文。
        /// </summary>
        private static string LogItemText(object item)
        {
            var main = new List<string>();
            var sub = new List<string>();
            try
            {
                var dynItem = (dynamic)item;
                try { var dl = (dynamic)dynItem.logs; int n = dl == null ? 0 : (int)dl.Count; for (int i = 0; i < n; i++) { var t = TryDataString(dl[i]); if (KeepLogPart(t)) main.Add(t); } } catch { }
                try { var sl = (dynamic)dynItem.subLogs; int n = sl == null ? 0 : (int)sl.Count; for (int i = 0; i < n; i++) { var t = TryDataString(sl[i]); if (KeepLogPart(t)) sub.Add(t); } } catch { }
            }
            catch { }
            string s = string.Concat(main);                       // 句内片段：直连（原文自带标点）
            if (sub.Count > 0)
            {
                string tail = string.Join("；", sub);
                s = s.Length > 0 ? s + "；" + tail : tail;
            }
            return s;
        }

        /// <summary>
        /// 该段是否值得显示。**专治 `DataToString` 的 `&amp;` 编码残渣**（实机）：
        /// 真机 L1 近况里出现 `0&amp;A&amp;A` 这种尾巴 —— 某个 <c>LogData.Data</c> 元素的
        /// `GetLogString()` 为空，于是回退到 `DataToString()`，而后者是**序列化格式**
        /// （`id&amp;values&amp;conditionValues` 用 `&amp;` 连接，`StringToData` 是它的逆），不是人话。
        /// 游戏自己的经历面板**不显示它**，我们也不该显示。
        ///
        /// 判据刻意收窄：**无中日韩字符 且 含 `&amp;`** 才丢 ——
        /// 纯数字/纯符号的 subLog（如 "+58"）没有 `&amp;`，照常保留。
        /// </summary>
        private static bool KeepLogPart(string s)
        {
            if (string.IsNullOrEmpty(s)) return false;
            if (s.IndexOf('&') < 0) return true;
            return HasCjk(s);
        }

        public static string CleanLogTextPublic(string s) => CleanLogText(s);

        /// <summary>
        /// 清 @ 引用标记 + 剥 DataToString 的 &amp; 编码尾段。
        ///
        /// @ 标记的真实格式（真机实证）：`@w_rIsZH3|1|5031101|48|@`
        /// —— `@&lt;类型字母&gt;_&lt;字段0&gt;|&lt;字段1&gt;|…|@`，**物品标签里含 propsID**。
        /// 该样本取自真机剧情文本 `我这里有一个@w_rIsZH3|1|5031101|48|@准备赠于你`：
        /// 字段2 `5031101` 在 `ItemProps.json` 里存在（`name = item_name5031101`
        /// → LocalText `青须藤`），即**中间的数字字段是道具表 id**。
        ///
        /// 旧实现 `Replace(s, "@[a-zA-Z]_([^|@]*)(?:\|[^@]*)?@", "$1")` 只取**字段0**
        /// （道具的 soleID，如 `zk8xkN`）—— 于是模型读到的是 `在溪望镇的坊市中购买了zk8xkN。`，
        /// 而游戏面板显示的是绿色的 `五品培元丹`。本方法改为**查表换中文名**。
        /// </summary>
        internal static string CleanLogText(string s)
        {
            if (string.IsNullOrEmpty(s)) return s;
            try
            {
                s = System.Text.RegularExpressions.Regex.Replace(s, "@([a-zA-Z])_([^@]*)@", RenderAtTag);
                if (s.IndexOf('&') >= 0 && s.IndexOf("，") < 0 && s.IndexOf("。") < 0 && s.IndexOf("！") < 0 && s.IndexOf("!") < 0)
                {
                    var m = System.Text.RegularExpressions.Regex.Match(s, @"[^&]+$");
                    if (m.Success && !string.IsNullOrWhiteSpace(m.Value))
                    {
                        string tail = m.Value.Trim();
                        tail = System.Text.RegularExpressions.Regex.Replace(tail, @"^[0-9QA&; ]+", "");
                        if (!string.IsNullOrEmpty(tail)) s = tail;
                    }
                }
                // ★ 剥 Unity 富文本标签（10-25 补）★
                //   真机实测：**61 处** `<color=#004FCA>` + 66 处 `</color>` 原样漏进了 L1
                //   「近况」段（样本：`完成了宗门任务<color=#004FCA>妖物肆虐灵矿</color>。`）。
                //   模型会把标签当正文读 —— 纯噪声，而且它还可能照着标签风格输出。
                //
                //   ★ 为什么之前没漏在这里 ★：`StripRichTags` 早在技能说明那条路上就有，
                //   但 `CleanLogText` **从没调用过它**。而经历文本走的正是这条
                //   （`LogItemText` → `CleanLogText`），所以标签一路到模型。
                //
                //   ★ 为什么用 `StripRichTags` 而不是再写一个正则 ★
                //   它是**通用括号机**（进 `<` 出 `>` 之间的字符全丢），对游戏自带的
                //   `<g>`/`<y>`/`<size=…>`/`<sprite …>` 一视同仁，不用维护标签白名单
                //   （白名单必然漏掉下一种标签）。代价：正文里真出现裸 `<…>` 也会被吃掉 ——
                //   实测三个存档 20 个会话**一个都没有**（0/313 段），所以这个代价是零。
                s = StripRichTags(s);
                s = s.Trim();
                s = System.Text.RegularExpressions.Regex.Replace(s, @"\s+", " ");
                s = System.Text.RegularExpressions.Regex.Replace(s, @"[；;]+", "；");
                return s;
            }
            catch { return s; }
        }

        /// <summary>unitID 列表 -> 中文名数组（GetUnit(id).GetName()），解析失败回退原 ID。</summary>
        /// <summary>
        /// 关系 ID 列表 → `[{name, unit_id}]`。
        ///
        /// 重名事故后改 这里**一直握着 unitID**（入参就是 ID 列表），旧实现却在
        /// `GetUnit(idStr)` 取到名字后把 ID **丢掉**、只回中文名 —— 于是模型拿到「益婉容」
        /// 三个字，再 `inspect_unit(target="益婉容")` 时只能全图按名猜，真机上就查成了**另一个
        /// 同名的人**（`Resolve('益婉容') → ④全图按名#199 unitID=Xs6JDI`，与玩家面板上那个
        /// 结晶后期/声望3379 的益婉容不是同一人）。带上 unit_id 后这条链变成**可精确往返**：
        /// 关系簿 → unit_id → `inspect_unit(unit_id=…)`，全程不经过名字。
        ///
        /// ★ 09-17：单位已不在世上（死亡/离场）而取不到名字时，**name 留空**，
        ///   **绝不拿 `idStr` 顶替**。原来那句 `string.IsNullOrEmpty(nm) ? idStr : nm`
        ///   会把一串 unitID 塞进 `name` 字段 —— 而 `name` 是**全工程"人物名"的载体**：
        ///   Python 侧 `unit_refs._pair` 收下 `{name, unit_id}` 就按它登记映射表，
        ///   渲染层再把 `name` 印给模型看 ⇒ 模型会把那串 id 当人名念出来（本工程
        ///   最忌讳的形态）。留空之后 `_pair` 只认"同时有 name 与 unit_id"的条目，
        ///   这一条自然被跳过：查不到名字的人**不显示、也不登记**（他本来也指不到）。
        /// </summary>
        private static JArray UnitNames(object list)
        {
            var arr = new JArray();
            if (list == null) return arr;
            try
            {
                var dyn = (dynamic)list;
                int cnt = 0;
                try { cnt = (int)dyn.Length; } catch { try { cnt = (int)dyn.Count; } catch { } }
                for (int i = 0; i < cnt; i++)
                {
                    var id = dyn[i];
                    if (id == null) continue;
                    string idStr = id.ToString();
                    // 关系簿存在空串占位条目（真机实证：姜萌 parent 出 ["",""]），直接跳过不输出噪音
                    if (string.IsNullOrEmpty(idStr)) continue;
                    string nm = "";
                    try
                    {
                        var u = g.world.unit.GetUnit(idStr);
                        if (u != null) nm = u.data.unitData.propertyData.GetName();
                    }
                    catch { }
                    arr.Add(new JObject
                    {
                        ["name"] = nm ?? "",
                        ["unit_id"] = idStr,
                    });
                }
            }
            catch { }
            return arr;
        }

        /// <summary>好友/仇人簿：GetIntimUnitData(...).{friendUnits|enemyUnits} -> `[{name, unit_id}]`
        /// （types2.txt:82-84 并列实锤）。同 <see cref="UnitNames"/>：ID 必须带出去，否则只能按名猜。</summary>
        private static JArray IntimBookNames(object relObj, bool friend)
        {
            var arr = new JArray();
            if (relObj == null) return arr;
            try
            {
                var dynRel = (dynamic)relObj;
                var iud = dynRel.GetIntimUnitData(true, false);
                if (iud == null) return arr;
                object book = null;
                try { book = friend ? ((dynamic)iud).friendUnits : ((dynamic)iud).enemyUnits; } catch { return arr; }
                if (book == null) return arr;
                var dyn = (dynamic)book;
                int cnt = 0;
                try { cnt = (int)dyn.Count; } catch { try { cnt = (int)dyn.Length; } catch { return arr; } }
                for (int i = 0; i < cnt; i++)
                {
                    var u = dyn[i];
                    if (u == null) continue;
                    var jo = new JObject();
                    try { jo["name"] = u.data.unitData.propertyData.GetName(); } catch { }
                    try { jo["unit_id"] = u.data.unitData.unitID; } catch { }
                    if (jo["name"] != null || jo["unit_id"] != null) arr.Add(jo);
                }
            }
            catch { }
            return arr;
        }

        /// <summary>气运：优先 wub.allLuck -&gt; luckConf.name/tips（types36 实机），回退 bornLuck/addLuck 查表。</summary>
        private static JObject LuckFromWorld(WorldUnitBase wub, object born, object added)
        {
            var o = new JObject();
            // 1) 建 id-&gt;(name,desc) 映射表，来自 wub.allLuck (WorldUnitLuckBase.luckConf/luckData)
            var map = new Dictionary<int, JObject>();
            try
            {
                var all = (dynamic)wub.allLuck;
                if (all != null)
                {
                    int cnt = 0;
                    try { cnt = (int)all.Count; } catch { }
                    for (int i = 0; i < cnt; i++)
                    {
                        var wb = all[i];
                        if (wb == null) continue;
                        try
                        {
                            int id = 0;
                            try { id = (int)wb.luckData.id; } catch { try { id = (int)((dynamic)wb.luckData).id; } catch { continue; } }
                            string name = null, tips = null;
                            try { name = wb.luckConf.name; } catch { try { name = ((dynamic)wb.luckConf).name; } catch { } }
                            try { tips = wb.luckConf.tips; } catch { try { tips = ((dynamic)wb.luckConf).tips; } catch { } }
                            // ConfFateFeatureItem 无 name 时 desc 即 name，已在 luckConf.name 中体现
                            if (string.IsNullOrEmpty(name)) try { name = ((dynamic)wb.luckConf).desc; } catch { }
                            if (!string.IsNullOrEmpty(name)) try { name = GameTool.LS(name); } catch { }
                            if (!string.IsNullOrEmpty(tips)) try { tips = GameTool.LS(tips); } catch { }
                            var jo = new JObject { ["id"] = id };
                            if (!string.IsNullOrEmpty(name)) jo["name"] = name;
                            if (!string.IsNullOrEmpty(tips)) jo["desc"] = tips;
                            else if (!string.IsNullOrEmpty(name)) jo["desc"] = name;
                            map[id] = jo;
                        }
                        catch { }
                    }
                }
            }
            catch { }

            // 2) born / added 仍按原 LubData.id 列表输出，但 name/desc 优先用 allLuck 映射回填
            o["born"] = LuckItemsWithMap(born, map);
            o["added"] = LuckItemsWithMap(added, map);
            // 3) 额外全量 all（便于模型直接取所有气运中文）
            try
            {
                var allArr = new JArray();
                foreach (var kv in map) allArr.Add(kv.Value);
                // 若 allLuck 为空（极早期档），回退 born/added 去重
                if (allArr.Count == 0)
                {
                    var seen = new HashSet<int>();
                    foreach (var a in (JArray)o["born"]) { int id = (int)a["id"]; if (seen.Add(id)) allArr.Add(a); }
                    foreach (var a in (JArray)o["added"]) { int id = (int)a["id"]; if (seen.Add(id)) allArr.Add(a); }
                }
                o["all"] = allArr;
            }
            catch { }
            return o;
        }

        private static JArray LuckItemsWithMap(object list, Dictionary<int, JObject> map)
        {
            var arr = new JArray();
            if (list == null) return arr;
            try
            {
                var dyn = (dynamic)list;
                int cnt = 0;
                try { cnt = (int)dyn.Length; } catch { try { cnt = (int)dyn.Count; } catch { } }
                for (int i = 0; i < cnt; i++)
                {
                    var ld = dyn[i];
                    if (ld == null) continue;
                    int idNum = 0;
                    try { idNum = (int)ld.id; } catch { try { idNum = (int)((dynamic)ld).id; } catch { continue; } }
                    JObject jo;
                    if (map != null && map.TryGetValue(idNum, out jo))
                    {
                        arr.Add(new JObject(jo)); // clone 已含 name/desc
                        continue;
                    }
                    // 回退：单查 g.conf.fateFeature / 直接 LS desc
                    jo = new JObject { ["id"] = idNum };
                    try
                    {
                        var item = g.conf.fateFeature.GetItem(idNum);
                        if (item != null)
                        {
                            string name = null, desc = null;
                            try { name = ((dynamic)item).name; } catch { }
                            if (string.IsNullOrEmpty(name)) try { name = ((dynamic)item).desc; } catch { }
                            try { desc = ((dynamic)item).desc; } catch { }
                            if (!string.IsNullOrEmpty(name)) { try { name = GameTool.LS(name); } catch { } jo["name"] = name; }
                            if (!string.IsNullOrEmpty(desc)) { try { desc = GameTool.LS(desc); } catch { } jo["desc"] = desc; }
                            if (jo["name"] == null && jo["desc"] != null) jo["name"] = jo["desc"];
                        }
                    }
                    catch { }
                    if (jo["name"] == null) try { jo["name"] = ""; } catch { }
                    arr.Add(jo);
                }
            }
            catch { }
            return arr;
        }

        /// <summary>
        /// 气运名串（每侧截前 5 防撑爆）。withDesc=true 时成"名（desc截60）"，desc 换行折叠——
        /// L1 自身段用（后天气运 desc 动态且重要，用户拍板要带）；inspect text 用默认只名（结构数据里已有全量 desc）。
        /// 供 GameContext 与 ToolExecutor 共用。key = "born" / "added"。
        /// </summary>
        public static string LuckNamesText(JToken luck, string key, bool withDesc = false)
        {
            var names = new List<string>();
            if (luck != null && luck[key] is JArray ja)
                foreach (var it in ja)
                {
                    string n = null, d = null;
                    try { n = it?["name"]?.ToString(); } catch { }
                    if (withDesc)
                    {
                        try { d = it?["desc"]?.ToString(); } catch { }
                        if (!string.IsNullOrEmpty(d))
                        {
                            d = d.Replace("\r", "").Replace("\n", " ");
                            if (d.Length > 60) d = d.Substring(0, 60) + "…";
                        }
                    }
                    if (string.IsNullOrEmpty(n)) continue;
                    names.Add(string.IsNullOrEmpty(d) ? n : n + "（" + d + "）");
                    if (names.Count >= 5) break;
                }
            return names.Count == 0 ? "" : string.Join(withDesc ? "；" : "、", names);
        }

        /// <summary>功法槽：实机路径 am.data.To&lt;MartialData&gt;().martialInfo.name（types36:13791），兜底 battleSkillPrefixName。
        /// 增补 <c>desc</c>（技能说明，面板「技能说明」同源）。</summary>
        private static JObject MartialSlot(object udObj, string skillId, string typeCn)
        {
            var jo = new JObject { ["type"] = typeCn, ["id"] = skillId ?? "" };
            string name = "";
            DataProps.MartialData mdOut = null;      // 提到外层：技能说明要用同一个 MartialData
            if (!string.IsNullOrEmpty(skillId))
            {
                try
                {
                    var dynUd = (dynamic)udObj;
                    var am = dynUd.GetActionMartial(skillId);
                    if (am != null)
                    {
                        // 主路径：MartialData md = am.data.To<MartialData>(); md.martialInfo.name
                        try
                        {
                            var dataObj = am.data;
                            if (dataObj != null)
                            {
                                // dynamic 调用泛型 To<MartialData>
                                var md = ((dynamic)dataObj).To<DataProps.MartialData>();
                                mdOut = md;
                                if (md != null && md.martialInfo != null)
                                {
                                    string raw = null;
                                    try { raw = md.martialInfo.name; } catch { }
                                    if (!string.IsNullOrEmpty(raw))
                                    {
                                        try { name = GameTool.LS(raw); } catch { name = raw; }
                                        if (string.IsNullOrEmpty(name)) name = raw;
                                    }
                                }
                                // 兜底：若未取到，用前缀表拼名
                                if (string.IsNullOrEmpty(name) && md != null)
                                {
                                    try
                                    {
                                        var n = g.conf.battleSkillPrefixName.GetName(md);
                                        if (!string.IsNullOrEmpty(n)) { try { name = GameTool.LS(n); } catch { name = n; } }
                                    }
                                    catch { }
                                }
                            }
                        }
                        catch { }
                        // 兼容旧动态路径（极端档）
                        if (string.IsNullOrEmpty(name))
                        {
                            try
                            {
                                var mi = am.martialInfo;
                                if (mi == null) try { mi = am.data?.martialInfo; } catch { }
                                if (mi != null)
                                {
                                    string raw = mi.name;
                                    if (!string.IsNullOrEmpty(raw))
                                    {
                                        try { name = GameTool.LS(raw); } catch { name = raw; }
                                        if (string.IsNullOrEmpty(name)) name = raw;
                                    }
                                }
                            }
                            catch { }
                        }
                    }
                }
                catch { }
            }
            if (string.IsNullOrEmpty(skillId)) jo["name"] = "";
            else jo["name"] = string.IsNullOrEmpty(name) ? "[待真机]功法名(id=" + skillId + ")" : name;

            // —— 技能说明——
            // `UIMartialInfoTool.GetDesc(md)` 是**面板「技能说明」同一个入口**（反编实证：
            // `UIMartialInfoTool` 为 abstract+sealed 静态类，`public static string GetDesc(DataProps.MartialData)`；
            // 同一族的 `GetDescRichText` 才是带图标/染色数字的富文本版，说明 `GetDesc` 是纯文本版）。
            // 说明文字由「前缀 desc 模板 + ConfBattleSkillValue 数值」在游戏内组装，自己拼必然漏占位符，
            // 所以只走这一个入口。取不到就**不落 desc 键**（字段缺失 ≠ 没有说明），并留痕绝不静默。
            if (mdOut != null)
            {
                try
                {
                    // ① 取模板（`GetDesc` 返回的是**未替换的原文**：`&22111_range&` / `$s_dao$` / `<y>…</y>`）
                    string tpl = UIMartialInfoTool.GetDesc(mdOut);
                    // ② 替换两类占位符（见 ResolveSkillSigils 的语法说明）
                    string d = ResolveSkillSigils(tpl, mdOut);
                    // ③ 剥掉游戏自己的配色标签（`<y>`/`<g>`/Unity 富文本），折叠空白
                    d = StripRichTags(d);
                    if (d.Length > 0)
                    {
                        jo["desc"] = d;
                        // 残留占位符必须留痕（不静默）：本轮若还有，日志里能直接看到是哪种 token。
                        int residual = CountSigils(d);
                        if (residual > 0)
                            ModMain.P("[Abilities] ★技能说明仍残留 " + residual + " 个占位符 slot=" + typeCn
                                      + " id=" + skillId + " 片段=" + Short(d, 160));
                        if (!_descLogged)
                        {
                            _descLogged = true;
                            ModMain.P("[Abilities] 技能说明已取到（首个：" + typeCn + "「" + name + "」len=" + d.Length
                                      + " 残留=" + residual + "）");
                        }
                    }
                    else ModMain.P("[Abilities] GetDesc 返回空 slot=" + typeCn + " id=" + skillId);
                }
                catch (System.Exception e)
                {
                    ModMain.P("[Abilities] GetDesc 抛异常 slot=" + typeCn + " id=" + skillId + "：" + e.Message);
                }
            }
            return jo;
        }

        /// <summary>首个技能说明取到的留痕（只打一次，不刷屏）。</summary>
        private static bool _descLogged;

        /// <summary>
        /// 技能说明占位符替换 —— 面板上那串人话就是这一步的产物。
        ///
        /// `UIMartialInfoTool.GetDesc(md)` 给的是**模板原文**，里面有三类记号，
        /// 语法是从游戏自带的配置表里实证出来的（`Mod/modFQA/…/Json格式/`）：
        ///
        ///   ① `&lt;y&gt;…&lt;/y&gt;` / `&lt;g&gt;…&lt;/g&gt;` —— 游戏自带的配色标签（不是 Unity 富文本），
        ///      由 `StripRichTags` 剥掉（它剥的是通用 `&lt;…&gt;`，两类通吃）。
        ///   ② `&lt;expr&gt;` 形如 `&amp;22111_range&amp;` —— **数值**，查 `ConfBattleSkillValue`。
        ///      该表的主键**自带前导 `&amp;`**（实证 key = `&amp;22111_range`，value1..value10 为各等级值，
        ///      `valueScale = "x1|f0"` 是格式说明）。
        ///      expr 里含 `|` 的是**算式**（如 `22111_xzsh|x22111_dmg|/100|f0` = 两个值相乘再除 100、
        ///      保留 0 位小数）—— 那不是表里的行（`&amp;…|…&amp;` 在表中查无此键），要走 `ValueMathf`。
        ///   ③ `$key$` 形如 `$s_dao$` —— **本地化文本**引用（`s_dao` → 刀法、`s_xueren` → 血刃），
        ///      查 `GameTool.LS`（`LocalText.json` 的 key）。
        ///
        /// 真机原样（`LocalText.skill_attack_desc22111`，替换前的模板）：
        ///   `对范围&amp;22111_range&amp;内的敌人造成威力&amp;22111_dmg&amp;的$s_dao$伤害…`
        /// 不替换就直接喂模型 = 满屏 `&amp;22111_dmg&amp;`（用户实机投诉"夹杂太多占位"）。
        ///
        /// 替换不到的记号**原样保留**（不猜、不删），由调用方统计残留并留痕。
        /// </summary>
        private static string ResolveSkillSigils(string s, DataProps.MartialData md)
        {
            BattleSkillValueData vd = null;
            try { vd = new BattleSkillValueData(md); } catch { }
            if (vd == null) return s;
            // 扫描/残留计数与物品介绍**共用** PropsSigilCore.Scan；这里只提供"数值怎么算"
            return PropsSigilCore.Scan(s, expr => TryResolveValue(expr, vd), LsOrNull).Text;
        }

        /// <summary>`GameTool.LS` 的安全包装：抛异常/未命中都返回 null（=不替换），交扫描器原样保留。</summary>
        private static string LsOrNull(string key)
        {
            try { return GameTool.LS(key); } catch { return null; }
        }

        /// <summary>
        /// 单个 `&amp;expr&amp;` 的数值解析。
        ///
        /// 必须自己套 `valueScale`（第三次修）
        /// 真机症状：`2秒` 显示成 `2000秒`、`8秒` 显示成 `8000秒`、`50%` 显示成 `5000%`。
        /// 根因：`ConfBattleSkillValue.GetValue(key, vd)` 返回的是**未缩放的原始值**
        /// （实证 `&amp;510011_cxsj`：表里 value1=**2000**，而面板是 **2 秒**）——
        /// 缩放系数在**同一行的 `valueScale` 列**：`x&amp;lt;系数&amp;gt;|f&amp;lt;小数位&amp;gt;`。
        /// 全表实证：系数只有 `1 / 0.1 / 0.01 / 0.001 / 0.0001`，小数位 0~3（有一条大写 `F1`）。
        /// 不套这一列 ⇒ 所有 0.001/0.01 系数的数值**整体放大 100~1000 倍**。
        ///
        /// **两种键写法都试**：表主键自带前导 `&amp;`（`&amp;22111_range`），而 API 形参名是 `key`，
        /// 内部是否补 `&amp;` 无法离线确认 —— 同键的两种写法，不算"猜值"。
        ///
        /// 算式（含 `|`）走 `EvalMath`：**每个操作数各自套自己的 valueScale**，
        /// 因为 `ValueMathf` 是否替操作数缩放同样无法确认（已知 `GetValue` 不缩）。
        /// 算不出才退回 `ValueMathf`；**算式失败绝不退回单值**（只取一个因子是错的数，比留占位符更糟）。
        /// </summary>
        private static string TryResolveValue(string inner, BattleSkillValueData vd)
        {
            if (string.IsNullOrEmpty(inner) || vd == null) return null;
            if (inner.IndexOf('|') >= 0)
            {
                string mine = EvalMath(inner, vd);
                // 留痕对照（只打第一次）：自算 vs 游戏 ValueMathf。
                // 自算是主路径（操作数逐个套 valueScale —— 已知 GetValue 不缩）；
                // ValueMathf 只在自算失败时兜底。两者若有分歧，这行日志就是判据。
                if (!_mathCompared)
                {
                    _mathCompared = true;
                    string theirs = null;
                    try { theirs = g.conf.battleSkillValue.ValueMathf(inner, vd); } catch { }
                    ModMain.P("[Abilities] 算式自算=" + (mine ?? "null") + " 游戏ValueMathf=" + (theirs ?? "null")
                              + " 表达式=" + inner);
                }
                if (mine != null) return mine;
                try { return g.conf.battleSkillValue.ValueMathf(inner, vd); } catch { }
                return null;
            }
            return ScaledValue(inner, vd);
        }

        /// <summary>算式"自算 vs 游戏 ValueMathf"对照日志只打一次。</summary>
        private static bool _mathCompared;

        /// <summary>单值：`GetValue`（负责按等级选列）→ 再套该行的 `valueScale`。</summary>
        private static string ScaledValue(string key, BattleSkillValueData vd)
        {
            string raw = null, form = null;
            string[] forms = { key, "&" + key };
            for (int i = 0; i < forms.Length; i++)
            {
                try { raw = g.conf.battleSkillValue.GetValue(forms[i], vd); } catch { }
                if (!string.IsNullOrEmpty(raw)) { form = forms[i]; break; }
            }
            if (string.IsNullOrEmpty(raw)) return null;
            return ApplyValueScale(raw, ValueScaleOf(form));
        }

        /// <summary>取该键所在行的 `valueScale`（`x&lt;系数&gt;|f&lt;小数位&gt;`）；取不到返回 null（=不缩放）。</summary>
        private static string ValueScaleOf(string key)
        {
            if (string.IsNullOrEmpty(key)) return null;
            string[] forms = { key, "&" + key };
            for (int i = 0; i < forms.Length; i++)
            {
                try
                {
                    var it = g.conf.battleSkillValue.GetItem(forms[i]);
                    if (it == null) continue;
                    string sc = null;
                    try { sc = it.valueScale; } catch { }
                    if (!string.IsNullOrEmpty(sc)) return sc;
                }
                catch { }
            }
            return null;
        }

        /// <summary>
        /// `x&lt;系数&gt;|f&lt;小数位&gt;` → 缩放 + 格式化。**去掉多余尾零**（`2.00` → `2`、`7.50` → `7.5`）：
        /// 中文句子里"2秒 / 7%吸血"才是人话，"2.00秒"不是。系数/小数位缺省即不缩放/不限定。
        /// </summary>
        private static string ApplyValueScale(string raw, string spec)
        {
            if (string.IsNullOrEmpty(raw)) return raw;
            if (string.IsNullOrEmpty(spec)) return raw;
            double v;
            if (!double.TryParse(raw, System.Globalization.NumberStyles.Any,
                                 System.Globalization.CultureInfo.InvariantCulture, out v)) return raw;
            double mul = 1; int dec = -1;
            string[] segs = spec.Split('|');
            for (int i = 0; i < segs.Length; i++)
            {
                string seg = segs[i];
                if (seg == null || seg.Length < 2) continue;
                char c = char.ToLowerInvariant(seg[0]);
                if (c == 'x')
                {
                    double d;
                    if (double.TryParse(seg.Substring(1), System.Globalization.NumberStyles.Any,
                                        System.Globalization.CultureInfo.InvariantCulture, out d)) mul = d;
                }
                else if (c == 'f')
                {
                    int n;
                    if (int.TryParse(seg.Substring(1), out n) && n >= 0 && n <= 8) dec = n;
                }
            }
            double r = v * mul;
            string s = dec >= 0
                ? r.ToString("F" + dec, System.Globalization.CultureInfo.InvariantCulture)
                : r.ToString(System.Globalization.CultureInfo.InvariantCulture);
            if (s.IndexOf('.') >= 0) s = s.TrimEnd('0').TrimEnd('.');
            return s;
        }

        /// <summary>
        /// 算式求值：`22111_xzsh|x22111_dmg|/100|f0` = 值(第一个键) × 值(第二个键) ÷ 100，保留 0 位小数。
        /// 段语法（真机 desc 实证）：裸段 = 键（首段为基准）、`x&lt;key&gt;` 乘、`/&lt;数&gt;` 除、
        /// `+/-&lt;数&gt;` 加减、`f&lt;n&gt;` 小数位。
        /// **每个操作数各自套自己的 `valueScale`**（与 <see cref="ScaledValue"/> 同一套规则）。
        /// 算不出返回 null，由调用方退回 `ValueMathf`。
        /// </summary>
        private static string EvalMath(string expr, BattleSkillValueData vd)
        {
            string[] segs = expr.Split('|');
            double? acc = null; double div = 1; int dec = -1;
            for (int i = 0; i < segs.Length; i++)
            {
                string seg = segs[i];
                if (string.IsNullOrEmpty(seg)) continue;
                char c = seg[0];
                if ((c == 'x' || c == 'X') && seg.Length > 1)
                {
                    double? v = NumOf(seg.Substring(1), vd);
                    if (v != null) acc = (acc ?? 1) * v.Value;
                }
                else if (c == '/' && seg.Length > 1)
                {
                    double d;
                    if (double.TryParse(seg.Substring(1), System.Globalization.NumberStyles.Any,
                                        System.Globalization.CultureInfo.InvariantCulture, out d) && d != 0) div *= d;
                }
                else if ((c == 'f' || c == 'F') && seg.Length > 1)
                {
                    int n;
                    if (int.TryParse(seg.Substring(1), out n) && n >= 0 && n <= 8) dec = n;
                }
                else if (c == '+' || c == '-')
                {
                    double d;
                    if (double.TryParse(seg, System.Globalization.NumberStyles.Any,
                                        System.Globalization.CultureInfo.InvariantCulture, out d))
                        acc = (acc ?? 0) + d;
                    else { double? v = NumOf(seg, vd); if (v != null) acc = (acc ?? 0) + v.Value; }
                }
                else
                {
                    double? v = NumOf(seg, vd);
                    if (v == null) return null;
                    acc = acc == null ? v.Value : acc.Value * v.Value;
                }
            }
            if (acc == null) return null;
            double r = acc.Value / div;
            string s = dec >= 0
                ? r.ToString("F" + dec, System.Globalization.CultureInfo.InvariantCulture)
                : r.ToString(System.Globalization.CultureInfo.InvariantCulture);
            if (s.IndexOf('.') >= 0) s = s.TrimEnd('0').TrimEnd('.');
            return s;
        }

        /// <summary>键 → double（`GetValue` 选列 + 自身 `valueScale` 缩放）；取不到返回 null。</summary>
        private static double? NumOf(string key, BattleSkillValueData vd)
        {
            string s = ScaledValue(key, vd);
            if (string.IsNullOrEmpty(s)) return null;
            double d;
            if (!double.TryParse(s, System.Globalization.NumberStyles.Any,
                                 System.Globalization.CultureInfo.InvariantCulture, out d)) return null;
            return d;
        }

        /// <summary>残留占位符计数（`&amp;`/`$` 成对算一个）—— 只用于"替换干净没有"的留痕。实现在共享核里。</summary>
        private static int CountSigils(string s)
        {
            return PropsSigilCore.CountSigils(s);
        }

        // ══════════════════════════════════════════════════════════════
        // 物品/装备介绍的占位符（09-17）
        //
        // 缺口：`PropsDetails` / `EquipsDetails` 原先只做 `GameTool.LS`，占位符原样喂给模型
        //   （原版「残·灵盾符」：`吸收使用者当前&608211_kcll&%的灵力…护盾持续&608211_cxsj&秒。`）。
        // 面板上它是 `30%` / `60秒`：`ConfBattleSkillValue.&608211_kcll` 十列皆 3000、`valueScale=x0.01|f0`。
        //
        // 与技能说明**共用**同一个扫描器（`PropsSigilCore.Scan`）+ 同一个缩放格式化
        // （`ApplyValueScale`）；差别只在"第几列怎么定"：
        //   · 技能：`new BattleSkillValueData(md)` → 游戏自己的 `GetValue(key, vd)` 选列；
        //   · 物品：**列自己定**（`PropsSigilCore.PickColumn`）—— 因为 `DataProps.PropsData`
        //     上没有 `grade`（grade 只在 `PropsArtifact`/`MartialData` 上，代理反射实证），
        //     境界只能来自 运行时 vd / 配置表 `ItemPill.grade`（表头写明「道具境界」）。
        //   列定不下来且十列不一致时**不猜**，留占位符 + ★留痕。
        // ══════════════════════════════════════════════════════════════

        /// <summary>物品/装备介绍里单个 `&amp;expr&amp;` 的取值（未缩放原值），失败返回 null。</summary>
        private static PropsSigilCore.Pick PropsColumn(string key, int vdGrade, int confGrade)
        {
            var none = new PropsSigilCore.Pick { Kind = PropsSigilCore.PickKind.NoRow };
            if (string.IsNullOrEmpty(key)) return none;
            ConfBattleSkillValueItem row = null;
            // 表主键自带前导 `&`（实证 `&22111_range`），API 形参名却叫 key —— 两种写法都试
            string[] forms = { key, "&" + key };
            for (int i = 0; i < forms.Length && row == null; i++)
            {
                try { row = g.conf.battleSkillValue.GetItem(forms[i]); } catch { }
            }
            if (row == null) return none;                       // 本表没有这条（可能属于别的表）→ 原样保留
            return PropsSigilCore.PickColumn(RowValues(row), vdGrade, confGrade);
        }

        /// <summary>`ConfBattleSkillValueItem.value1..value10` → `string[10]`（IL2CPP 无字符串索引器，只能逐个取）。</summary>
        private static string[] RowValues(ConfBattleSkillValueItem it)
        {
            var v = new string[PropsSigilCore.GradeMax];
            var d = (dynamic)it;
            try { v[0] = d.value1; } catch { }
            try { v[1] = d.value2; } catch { }
            try { v[2] = d.value3; } catch { }
            try { v[3] = d.value4; } catch { }
            try { v[4] = d.value5; } catch { }
            try { v[5] = d.value6; } catch { }
            try { v[6] = d.value7; } catch { }
            try { v[7] = d.value8; } catch { }
            try { v[8] = d.value9; } catch { }
            try { v[9] = d.value10; } catch { }
            return v;
        }

        /// <summary>
        /// 物品介绍里的 `&amp;expr&amp;` → 显示文本。单值走 `PickColumn`（列自己定、绝不猜）；
        /// 含 `|` 的算式走 `EvalMath`（与技能路径同一套；物品 desc 实测 0 处算式，这条是兜底）。
        /// 上下文（vd / 两个境界）**按件闭包传入**，不用静态字段 —— 免得两件道具互相串。
        /// </summary>
        private static string PropsScaledValue(string key, BattleSkillValueData vd, int vdGrade, int confGrade)
        {
            if (string.IsNullOrEmpty(key)) return null;
            if (key.IndexOf('|') >= 0)
            {
                if (vd == null) return null;
                string mine = EvalMath(key, vd);
                if (mine != null) return mine;
                try { return g.conf.battleSkillValue.ValueMathf(key, vd); } catch { }
                return null;
            }
            var pick = PropsColumn(key, vdGrade, confGrade);
            if (pick.Kind == PropsSigilCore.PickKind.Ambiguous)
            {
                // 境界未知且十列不一致 —— 宁可留占位符，也绝不猜一个数；留痕（去重封顶）
                PropSigilMiss(key, vdGrade, confGrade);
                return null;
            }
            if (pick.Kind != PropsSigilCore.PickKind.Ok) return null;
            return ApplyValueScale(pick.Value, ValueScaleOf(key));
        }

        /// <summary>`★物品介绍境界未知★` 留痕（键 → 已报过），封顶 40 条防刷屏。</summary>
        private static readonly System.Collections.Generic.HashSet<string> _propSigilMiss =
            new System.Collections.Generic.HashSet<string>();

        private static void PropSigilMiss(string key, int vdGrade, int confGrade)
        {
            try
            {
                if (_propSigilMiss.Contains(key)) return;
                if (_propSigilMiss.Count >= 40) return;
                _propSigilMiss.Add(key);
                ModMain.P("[Props] ★介绍占位符境界未知★ key=" + key
                          + " vdGrade=" + vdGrade + " confGrade=" + confGrade
                          + "（十列不一致，原样保留占位符，绝不猜）");
            }
            catch { }
        }

        /// <summary>
        /// 配置表里的「道具境界」：`ItemPill.grade`（丹符）/ `ItemSkill.grade`（秘籍）/
        /// `ItemRarityMaterials.grade`（材料）—— 三者表头都写明该列是境界/品阶。
        /// 拿不到返回 0（未知），不影响别的类型（装备类没有境界表，靠运行时 vd + 十列全同兜底）。
        /// </summary>
        private static int ConfPropsGrade(object propsItem, int propsID)
        {
            int type = 0;
            try { type = (int)((dynamic)propsItem).type; } catch { return 0; }
            try
            {
                object it = null;
                if (type == 1) it = g.conf.itemPill.GetItem(propsID);
                else if (type == 2) it = g.conf.itemSkill.GetItem(propsID);
                else if (type == 4) it = g.conf.itemRarityMaterials.GetItem(propsID);
                if (it == null) return 0;
                return PropsSigilCore.NormGrade((int)((dynamic)it).grade);
            }
            catch { }
            return 0;
        }

        /// <summary>
        /// 物品/装备介绍的统一入口：`LS(desc)` → 占位符解析 → 剥富文本标签。
        /// 与原实现相比只多了后两步；`propsData` 是 `DataProps.PropsData`（`allProps` 的元素）。
        /// </summary>
        private static string PropsDescText(DataProps.PropsData propsData, object propsItem, string rawDesc, string slotTag)
        {
            string d = rawDesc;
            try { d = GameTool.LS(rawDesc); } catch { d = rawDesc; }
            if (string.IsNullOrEmpty(d)) d = rawDesc;
            if (string.IsNullOrEmpty(d)) return null;

            // 本件道具的取值上下文：运行时 vd（游戏口径）+ 配置表境界（可审计）
            BattleSkillValueData vd = null;
            int vdGrade = 0, confGrade = 0, propsID = 0;
            try { propsID = propsData.propsID; } catch { }
            try { vd = new BattleSkillValueData(propsData); } catch { }
            if (vd != null) { try { vdGrade = vd.grade; } catch { } }
            confGrade = ConfPropsGrade(propsItem, propsID);

            string outText = d;
            try
            {
                if (d.IndexOf('&') >= 0)
                {
                    // 闭包捕获本件的 vd/境界 —— 扫描器与技能说明共用同一段（PropsSigilCore.Scan）
                    var scan = PropsSigilCore.Scan(d,
                        key => PropsScaledValue(key, vd, vdGrade, confGrade), LsOrNull);
                    outText = scan.Text;
                    // 一次性对照留痕（前 8 件带占位符的道具）：真机拿悬浮窗一比就知道列选得对不对
                    if (_propsDescLogged < 8 && scan.Replaced > 0)
                    {
                        _propsDescLogged++;
                        ModMain.P("[Props] 介绍占位符 slot=" + slotTag + " id=" + propsID
                                  + " type=" + PropTypeSafe(propsItem)
                                  + " vdGrade=" + vdGrade + " confGrade=" + confGrade
                                  + " 替换=" + scan.Replaced + " 残留=" + scan.Residual
                                  + " 片段=" + Short(outText, 90));
                    }
                    else if (scan.Residual > 0 && _propsResidualLogged < 12)
                    {
                        _propsResidualLogged++;
                        ModMain.P("[Props] ★介绍占位符仍残留 " + scan.Residual + " 个 slot=" + slotTag
                                  + " id=" + propsID + " 片段=" + Short(outText, 120));
                    }
                }
            }
            catch (System.Exception e)
            {
                ModMain.P("[Props] 介绍占位符解析抛异常 slot=" + slotTag + " id=" + propsID + "：" + e.Message);
                outText = d;      // 失败退回"只做了 LS"的原文，绝不落空
            }

            // 富文本清洗：565 条物品 desc 带 <g>/<color=#057600>/<size=12> 标签（面板要，模型不要）
            string clean = StripRichTags(outText);
            return string.IsNullOrEmpty(clean) ? null : clean;
        }

        /// <summary>对照日志只打前 8 件（不刷屏）。</summary>
        private static int _propsDescLogged;

        /// <summary>残留 ★ 日志封顶 12 条（同一件道具在多次 inspect 里会重复走到这里）。</summary>
        private static int _propsResidualLogged;

        private static int PropTypeSafe(object propsItem)
        {
            try { return (int)((dynamic)propsItem).type; } catch { return -1; }
        }

        /// <summary>
        /// 技能说明的富文本清洗：`GetDesc` 若返回 Unity 富文本（`&lt;color=…&gt;` / `&lt;sprite name=…&gt;` / `&lt;b&gt;`），
        /// 直接喂模型就是噪音。**只剥标签、不改字**（忠实原则）：空白折叠成单空格、首尾去净，
        /// 其余一字不动（不截断、不改写、不省略）。
        /// </summary>
        private static string StripRichTags(string s)
        {
            if (string.IsNullOrEmpty(s)) return "";
            var sb = new System.Text.StringBuilder(s.Length);
            bool inTag = false;
            for (int i = 0; i < s.Length; i++)
            {
                char c = s[i];
                if (c == '<') { inTag = true; continue; }
                if (c == '>') { inTag = false; continue; }
                if (!inTag) sb.Append(c);
            }
            var o = new System.Text.StringBuilder(sb.Length);
            bool sp = false;
            for (int i = 0; i < sb.Length; i++)
            {
                char c = sb[i];
                if (c == ' ' || c == '\t' || c == '\r' || c == '\n' || c == '\u3000')
                {
                    if (!sp && o.Length > 0) { o.Append(' '); sp = true; }
                    continue;
                }
                sp = false;
                o.Append(c);
            }
            return o.ToString().Trim();
        }

        /// <summary>
        /// stats.attrs 全量属性子块：与 NPC 面板「个人属性/战斗属性/资质」三组同口径
        /// （反编实锤 DataUnit.PropertyData / WorldUnitDynData 全字段，types49_uinpcinfo 面板同源）。
        /// 取值优先 WorldUnitDynData 的 DynInt 动态值（Value(null,true,true)=含功法/装备/状态加成并做
        /// 上下限钳制，与面板显示一致），拿不到回退 propertyData 裸字段。age/life 账面单位是月，
        /// ÷12 取整为年（面板口径，UIUnitSIM 同款换算）。
        /// 键名说明：水灵根的字段名是 basisFroze（游戏原名）；功法抗性=phycicalFree（游戏拼写），
        /// 灵根抗性=magicFree——输出键修正为 physical_free/magic_free。
        /// </summary>
        private static JObject BuildAttrs(object dyn, object pd)
        {
            var personal = new JObject();
            SetAttr(personal, dyn, pd, "age", "age", true);          // 月→年
            SetAttr(personal, dyn, pd, "life", "life", true);        // 寿命上限，月→年
            SetAttr(personal, dyn, pd, "mood", "mood");
            SetAttr(personal, dyn, pd, "mood_max", "moodMax");
            SetAttr(personal, dyn, pd, "health", "health");
            SetAttr(personal, dyn, pd, "health_max", "healthMax");
            SetAttr(personal, dyn, pd, "energy", "energy");
            SetAttr(personal, dyn, pd, "energy_max", "energyMax");
            SetAttr(personal, dyn, pd, "hp", "hp");
            SetAttr(personal, dyn, pd, "hp_max", "hpMax");
            SetAttr(personal, dyn, pd, "mp", "mp");                  // 灵力
            SetAttr(personal, dyn, pd, "mp_max", "mpMax");
            SetAttr(personal, dyn, pd, "sp", "sp");                  // 念力
            SetAttr(personal, dyn, pd, "sp_max", "spMax");
            SetAttr(personal, dyn, pd, "luck", "luck");
            SetAttr(personal, dyn, pd, "talent", "talent");          // 悟性

            var combat = new JObject();
            SetAttr(combat, dyn, pd, "attack", "attack");
            SetAttr(combat, dyn, pd, "defense", "defense");
            SetAttr(combat, dyn, pd, "foot_speed", "footSpeed");     // 脚力（≠移速）
            SetAttr(combat, dyn, pd, "move_speed", "moveSpeed");
            SetAttr(combat, dyn, pd, "physical_free", "phycicalFree"); // 功法抗性（游戏拼写 phycical）
            SetAttr(combat, dyn, pd, "magic_free", "magicFree");     // 灵根抗性
            SetAttr(combat, dyn, pd, "crit", "crit");                // 会心
            SetAttr(combat, dyn, pd, "crit_value", "critValue");     // 暴击倍数
            SetAttr(combat, dyn, pd, "guard", "guard");              // 护心
            SetAttr(combat, dyn, pd, "guard_value", "guardValue");   // 抗暴倍数

            var apt = new JObject();
            // 功法资质六项
            SetAttr(apt, dyn, pd, "blade", "basisBlade");
            SetAttr(apt, dyn, pd, "spear", "basisSpear");
            SetAttr(apt, dyn, pd, "sword", "basisSword");
            SetAttr(apt, dyn, pd, "fist", "basisFist");
            SetAttr(apt, dyn, pd, "palm", "basisPalm");
            SetAttr(apt, dyn, pd, "finger", "basisFinger");
            // 灵根资质六项（水=basisFroze，游戏原名字段）
            SetAttr(apt, dyn, pd, "fire", "basisFire");
            SetAttr(apt, dyn, pd, "froze", "basisFroze");
            SetAttr(apt, dyn, pd, "thunder", "basisThunder");
            SetAttr(apt, dyn, pd, "wind", "basisWind");
            SetAttr(apt, dyn, pd, "earth", "basisEarth");
            SetAttr(apt, dyn, pd, "wood", "basisWood");
            // 技艺资质六项
            SetAttr(apt, dyn, pd, "refine_elixir", "refineElixir");
            SetAttr(apt, dyn, pd, "refine_weapon", "refineWeapon");
            SetAttr(apt, dyn, pd, "geomancy", "geomancy");
            SetAttr(apt, dyn, pd, "symbol", "symbol");
            SetAttr(apt, dyn, pd, "herbal", "herbal");
            SetAttr(apt, dyn, pd, "mine", "mine");

            return new JObject { ["personal"] = personal, ["combat"] = combat, ["aptitudes"] = apt };
        }

        /// <summary>
        /// 境界（面板口径）—— 09-13 真机"父亲显示成登仙境"事故的修复处
        ///
        /// **症状**：`inspect_unit(unit_id=ECYyXU)`（**精确 ID，不是重名**）返回 `登仙境`，
        /// 而 NPC 面板上此人明明是**结晶后期**。
        ///
        /// **根因**：旧写法 `g.conf.roleGrade.GetGradeName(pd.gradeID)` 把两个不同的东西对上了：
        ///   · `RoleGrade` 表的**主键是「行」**（`id` 1..44 = 大境界×期×品质；44 行 / 10 个大境界 / 3 期）；
        ///   · 而 `GetGradeName(Int32 grade)` 的形参名是 **`grade`（大境界号 1..10）**
        ///     —— 同族 `GetNextGradeItem(Int32 gradeId)` 的形参名才是 `gradeId`（行号）。
        ///   于是 `gradeID`（行号）被当成大境界号喂进去：结晶后期的行号 ≈11 > 10，
        ///   查表越界被**钳到最大档 → 登仙境**。低阶 NPC（行号 1~3）恰好落回炼气/筑基，看起来"有时对"。
        ///
        /// **修法（不猜——自洽校验）**：把 `gradeID` 当行号，在 `allConfList` 里定位该行，
        /// 用**行内的 `grade` 必须与 `dynUnitData.curGrade` 对得上**做判据（`curGrade` 是面板同源的
        /// 大境界号，参照 mod `GetGradeItemMaxQuality(unit.data.dynUnitData.curGrade + 1, 1)` 实证其语义）。
        /// 对得上 ⇒ 行号假设成立，取该行的 `gradeName + phaseName`（LS 后即 **"结晶后期"**，与面板逐字一致）；
        /// 对不上 ⇒ **不采信**，退回 `GetGradeName(curGrade)`（丢"期"，但至少不是别人）。
        ///
        /// 下标 1 基/0 基两种读法都试（配置文件 id 从 1 起、列表从 0 起，差一位在这里是致命的）。
        /// 原始数字全部写进 `diag` 并随 brief 回传 —— 真机日志/工具结果里能直接看到走了哪条路。
        /// </summary>
        /// <summary>
        /// 单单位境界（面板口径）—— 供 brief 之外的场景复用**同一套读数**
        /// （09-17 关系诊断 RelationDiag 用它；另起一套写法必然与 brief 漂移）。
        /// 取不到给空串（调用方按"缺键/空值"跳过），绝不猜、也不写"未知"混淆真值。
        /// </summary>
        public static string RealmText(WorldUnitBase u)
        {
            if (u == null) return "";
            try { return RealmOf(((dynamic)u.data).dynUnitData, u.data.unitData.propertyData, null); }
            catch { return ""; }
        }

        /// <summary>
        /// 境界文本（按 gradeID 行号）：供**死者档案**用（DieData.gradeID 与活人 pd.gradeID 同源，
        /// 1 基读法与 <see cref="RealmOf"/> ① 一致；无 curGrade 做自洽校验，故跳过校验直接行号读，
        /// 读不到退 GetGradeName）。取不到给空串，调用方按缺键跳过。
        /// </summary>
        public static string RealmText(int gradeId)
        {
            if (gradeId <= 0) return "";
            try
            {
                var list = (dynamic)g.conf.roleGrade.allConfList;
                int cnt = (int)list.Count;
                int idx = gradeId - 1;                          // 1 基（同 RealmOf ①）
                if (idx >= 0 && idx < cnt)
                {
                    var it = list[idx];
                    if (it != null)
                    {
                        string gn = "", pn = "";
                        try { gn = GameTool.LS(it.gradeName); } catch { }
                        try { pn = GameTool.LS(it.phaseName); } catch { }
                        if (!string.IsNullOrEmpty(gn)) return gn + pn;   // "结晶" + "后期"
                    }
                }
            }
            catch { }
            try { string r = g.conf.roleGrade.GetGradeName(gradeId); if (!string.IsNullOrEmpty(r)) return r; } catch { }
            return "";
        }

        // ════════════════════ 死者档案（10-15：inspect_unit 的死亡账本回退）════════════════════
        //
        // 数据源与访问入口全部真机探针实锤（DeathProbe，本日）：
        //   · 账本入口 g.data.unitDie；allUnitDie=Dictionary<string,DieData> 75 条全可读；
        //   · DieData 字段齐全：unitID/name[姓,名]/sex/race/schoolID/gradeID/age/life/reputation/
        //     modelData/point(死亡坐标)/killUnitID/objData/relationData(死者关系簿快照)；
        //   · UnitDieData：dieMonth + logData(经历快照，与 DataUnitLog.LogData 同款可走解码层)；
        //   · **GetUnit(死者ID)=null**（三重载+全量遍历 1548 均不含）→ 活单位容器对死者不可解析，
        //     一切死者数据只能走本账本；
        //   · 快照 relationData 的 GetIntim(玩家)/GetRelation(player) 可调（冻结值）。
        // ═══════════════════════════════════════════════════════════════════════════════════

        /// <summary>死亡账本入口（双路兜底：unitDie 逻辑类优先，dataUnitDie 数据类次之）。</summary>
        public static object DeadLedger()
        {
            try { var o = (object)g.data.unitDie; if (o != null) return o; } catch { }
            try { return (object)g.data.dataUnitDie; } catch { return null; }
        }

        /// <summary>DieData.name（[姓,名] 数组）→ 全名（如 "宁凯旋"）。
        /// 双路：反射 GetProperty 优先（DeathProbe.ReadName 真机同款），失败退 dynamic 直取。</summary>
        public static string DeadName(object dieData)
        {
            if (dieData == null) return "";
            object arr = null;
            try { arr = dieData.GetType().GetProperty("name").GetValue(dieData, null); } catch { }
            if (arr == null) { try { arr = (object)((dynamic)dieData).name; } catch { } }
            if (arr == null) return "";
            try
            {
                var dyn = (dynamic)arr;
                int n = (int)dyn.Count;
                var sb = new System.Text.StringBuilder();
                for (int i = 0; i < n; i++)
                {
                    string s = null; try { s = dyn[i]?.ToString(); } catch { }
                    sb.Append(s);
                }
                return sb.ToString();
            }
            catch { return ""; }
        }

        /// <summary>
        /// allUnitDie[id] 取值（**双路降级**，与 DeathProbe 主段同款——10-16 ⑦ 探针实测：
        /// 单路 `(dynamic)all[id]` 索引器在真机可能抛/返 null，`get_Item(id)` 是跑通过的路）。
        /// </summary>
        public static object GetDieData(object allUnitDie, string id)
        {
            if (allUnitDie == null || string.IsNullOrEmpty(id)) return null;
            try { return (object)((dynamic)allUnitDie)[id]; } catch { }
            try { return (object)((dynamic)allUnitDie).get_Item(id); } catch { }
            return null;
        }

        /// <summary>
        /// 账本全部死者 ID（**三分列表优先**——persistenDieData 的 List&lt;string&gt; Count+索引器
        /// 真机跑通过 75 位详探；Keys 快照索引遍历在 ⑦ 探针实测会静默返 null 导致"账本无死者"
        /// 假象，只作列表不可读时的降级）。产出顺序稳定，供按名遍历与工具模拟共用。
        /// </summary>
        public static System.Collections.Generic.List<string> DeadIds()
        {
            var ids = new System.Collections.Generic.List<string>();
            var ledger = DeadLedger();
            if (ledger == null) return ids;
            object all = null;
            try { all = (object)((dynamic)ledger).allUnitDie; } catch { }
            if (all == null) return ids;
            // ① 三分列表（intim/hate/normal）：与 DeathProbe 主段同款
            try
            {
                var pers = (dynamic)((dynamic)ledger).persistenDieData;
                foreach (var kind in new[] { "intimDieUnitIds", "hateDieUnitIds", "normalDieUnitIds" })
                {
                    object list = null;
                    try { list = pers.GetType().GetProperty(kind).GetValue(pers, null); }
                    catch { try { list = pers.GetType().GetField(kind).GetValue(pers); } catch { } }
                    if (list == null) continue;
                    var dyn = (dynamic)list;
                    int n = 0; try { n = (int)dyn.Count; } catch { }
                    for (int i = 0; i < n; i++)
                    {
                        string id = null; try { id = dyn[i]?.ToString(); } catch { }
                        if (!string.IsNullOrEmpty(id) && !ids.Contains(id)) ids.Add(id);
                    }
                }
            }
            catch { }
            // ② 降级：Keys 快照 + 索引器（⑦ 实测可能静默返 null——仅兜底，逐个验非空）
            if (ids.Count == 0)
            {
                try
                {
                    var keys = (dynamic)((dynamic)all).Keys;
                    int kc = (int)keys.Count;
                    for (int i = 0; i < kc; i++)
                    {
                        string id = null; try { id = keys[i]?.ToString(); } catch { }
                        if (!string.IsNullOrEmpty(id)) ids.Add(id);
                    }
                }
                catch { }
            }
            return ids;
        }

        /// <summary>
        /// 死者定位：`unitId` 非空 → `allUnitDie[id]` **O(1) 精准**（Python harness 会注入
        /// `unit_id`——名字→ID 映射表学过的死者）；否则按 `name` 遍历（账本条数=死者数，
        /// 远小于活人全图扫；死者被 ClearDieUnit 清出关系簿 ⇒ 关系簿反查不出 ID）。
        /// 命中回传三件套：dieData / unitDieData（含 dieMonth/logData，可空）/ foundId。
        /// </summary>
        public static bool FindDead(string unitId, string name, out object dieData, out object unitDieData, out string foundId)
        {
            dieData = unitDieData = null; foundId = "";
            var ledger = DeadLedger();
            if (ledger == null) return false;
            object all = null, allInfo = null;
            try { all = (object)((dynamic)ledger).allUnitDie; } catch { }
            try { allInfo = (object)((dynamic)ledger).allUnitDieInfoData; } catch { }
            if (all == null) return false;
            try
            {
                if (!string.IsNullOrEmpty(unitId))
                {
                    // ID 精准（O(1)）：GetDieData 双路降级（[id] 索引器真机可能炸，get_Item 是验证过的路）
                    object d = GetDieData(all, unitId);
                    if (d != null) { dieData = d; foundId = unitId; }
                }
                if (dieData == null && !string.IsNullOrEmpty(name))
                {
                    // 按名遍历：ID 走 DeadIds（三分列表优先，真机验证；Keys 索引只作降级），
                    // 取值走 GetDieData 双路（10-16 ⑦ 探针：Keys 索引静默返 null 曾致"账本无死者"假象）
                    foreach (var id in DeadIds())
                    {
                        object d = GetDieData(all, id);
                        string nm = DeadName(d);
                        if (!string.IsNullOrEmpty(nm) && nm == name) { dieData = d; foundId = id; break; }
                    }
                }
                if (dieData != null && allInfo != null && !string.IsNullOrEmpty(foundId))
                {
                    try { unitDieData = GetDieData(allInfo, foundId); } catch { }
                }
                return dieData != null;
            }
            catch { return false; }
        }

        /// <summary>
        /// 死者判定（布尔轻封装）：unit_id 精准 O(1) / 按名遍历兜底（同 <see cref="FindDead"/>）。
        /// 消费方：传音不回复（ChatPresenter 收件人判定）、自主交互剔除（NpcInitiativeMonitor）。
        /// 活人绝不命中（账本键是死者 ID 集合）——高频路径（候选剔除）成本可忽略。
        /// </summary>
        public static bool IsDead(string unitId, string name)
        {
            object dieData, unitDieData; string foundId;
            return FindDead(unitId, name, out dieData, out unitDieData, out foundId);
        }

        /// <summary>死者「殁于X年X月」短语；非死者 / 取不到时间 → 空串。
        ///
        /// 通讯录「已故」行的副标题用它。**必须走 `unit_id`**（`allUnitDie[id]` 字典直查，
        /// O(1)）：按名兜底是遍历整个账本，渲染层逐行调会把面板打开变成 O(行数×死者数)。
        /// 月份换算复用 `CnYearMonth`（全仓唯一一份）—— 另写一份必然与「近况」/消息时间戳漂移。
        /// </summary>
        public static string DeadMonthLabel(string unitId, string name = null)
        {
            object dieData, unitDieData; string foundId;
            if (!FindDead(unitId, name, out dieData, out unitDieData, out foundId)) return "";
            try
            {
                object raw = (object)((dynamic)unitDieData).dieMonth;
                if (raw == null) return "";
                string lbl = CnYearMonth(Convert.ToInt32(raw));
                return string.IsNullOrEmpty(lbl) ? "" : "殁于" + lbl;
            }
            catch { return ""; }
        }

        /// <summary>
        /// 交互工具对死者的报错文案（10-15 待办②）：活人解析失败后、落"未找到"之前查死亡账本——
        /// 人死了要告诉模型**为什么**（已身故/殁于何时/死于谁手），不然它会当成名字打错了去重试
        /// search_units（活人工具，永远查不到）→ 死循环。复用 <see cref="FindDead"/> 定位
        /// （unit_id O(1) 精准 / 按名遍历兜底）；未命中返回 null（调用方落回原报错，逐字不变）。
        /// ★文案里绝不出现 unit_id（09-17 先例：错误帧原样进模型上下文）★。
        /// 三个细节（殁于/卒于/凶手）各自容错，缺哪个省哪个——最差也有"已身故"三个字。
        /// </summary>
        public static string DeadNotice(string unitId, string name)
        {
            object dieData, unitDieData; string foundId;
            if (!FindDead(unitId, name, out dieData, out unitDieData, out foundId))
                return null;
            var dyn = (dynamic)dieData;
            string nm = DeadName(dieData);
            if (string.IsNullOrEmpty(nm)) nm = (name ?? "").Trim();
            var parts = new List<string>();
            try
            {
                // dieMonth 非 int（⑦ 探针实证），Convert 宽容转换（同 BuildDead）
                object raw = (object)((dynamic)unitDieData).dieMonth;
                if (raw != null)
                {
                    string lbl = CnYearMonth(Convert.ToInt32(raw));   // 月份换算单一来源（SplitAccountMonth）
                    if (!string.IsNullOrEmpty(lbl)) parts.Add("殁于" + lbl);
                }
            }
            catch { }
            try { string dp = (string)dyn.point; if (!string.IsNullOrEmpty(dp)) parts.Add("卒于（" + dp + "）"); } catch { }
            try
            {
                string kid = (string)dyn.killUnitID;
                if (!string.IsNullOrEmpty(kid))
                {
                    string kn = "";
                    try { var u = g.world.unit.GetUnit(kid); if (u != null) kn = u.data.unitData.propertyData.GetName(); } catch { }
                    if (string.IsNullOrEmpty(kn))
                    {
                        object d2 = null;
                        try { d2 = (object)((dynamic)DeadLedger()).allUnitDie[kid]; } catch { }
                        kn = DeadName(d2);
                    }
                    if (!string.IsNullOrEmpty(kn)) parts.Add("死于" + kn + "之手");
                }
            }
            catch { }
            return "「" + nm + "」已身故" + (parts.Count > 0 ? "（" + string.Join("，", parts.ToArray()) + "）" : "")
                   + "。无法与逝者进行这一动作；他的生前事迹可用 inspect_unit 查阅。";
        }

        /// <summary><see cref="UnitNames"/> 的死者版：关系人 GetUnit 不到（对方也已死）→
        /// 死亡账本按 ID 精准拿名（O(1)）。同款产出 `[{name, unit_id}]`，ID 必带（可精确往返）。</summary>
        private static JArray DeadUnitNames(object list)
        {
            var arr = new JArray();
            if (list == null) return arr;
            try
            {
                var dyn = (dynamic)list;
                int cnt = 0;
                try { cnt = (int)dyn.Length; } catch { try { cnt = (int)dyn.Count; } catch { } }
                for (int i = 0; i < cnt; i++)
                {
                    var id = dyn[i];
                    if (id == null) continue;
                    string idStr = id.ToString();
                    if (string.IsNullOrEmpty(idStr)) continue;    // 空串占位条目（同 UnitNames 口径）
                    string nm = "";
                    try { var u = g.world.unit.GetUnit(idStr); if (u != null) nm = u.data.unitData.propertyData.GetName(); } catch { }
                    if (string.IsNullOrEmpty(nm))
                    {
                        object d = null;
                        try { d = (object)((dynamic)DeadLedger()).allUnitDie[idStr]; } catch { }
                        nm = DeadName(d);
                    }
                    arr.Add(new JObject { ["name"] = nm ?? "", ["unit_id"] = idStr });
                }
            }
            catch { }
            return arr;
        }

        /// <summary>
        /// 死者档案采集：与 <see cref="Build"/> 同形（部分）。brief 静态字段 + 关系三项（**冻结值**，
        /// 死者档案关系簿快照对玩家）+ relationships（快照十容器，关系人名字活死两路解析）+ logs
        /// （死亡瞬间的经历快照，走同款解码层）；**stats/abilities/inventory/luck 不采**
        /// （DieData 无字段）——调用方按请求的 classes 放通用语句。
        /// `dead=true` 是渲染层与 Python 侧判定"亡者档案"的总开关；`unit_id` 无条件回传
        /// （与活人 inspect 同规：`_harvest_ids` 学进映射表，之后全程精准）。
        /// </summary>
        public static JObject BuildDead(object dieData, object unitDieData, string foundId, JArray classes)
        {
            var data = new JObject();
            if (dieData == null) return data;
            bool Has(string c)
            {
                if (classes == null) return c == "brief";
                foreach (var x in classes) if ((string)x == c) return true;
                return false;
            }
            var dyn = (dynamic)dieData;

            try { data["unit_id"] = (string)(foundId ?? (object)((string)dyn.unitID)); } catch { }
            data["dead"] = true;
            data["name"] = DeadName(dieData);
            try { data["sex"] = SexCn(dyn.sex.ToString()); } catch { }
            try { data["race"] = RaceCn(dyn.race.ToString()); } catch { }
            int gradeId = 0; try { gradeId = (int)dyn.gradeID; } catch { }
            string realm = RealmText(gradeId);
            data["realm"] = string.IsNullOrEmpty(realm) ? "境界#" + gradeId : realm;
            try { data["sect"] = SectName((string)dyn.schoolID); } catch { }
            try { data["age"] = (int)dyn.age; } catch { }
            try { data["life"] = (int)dyn.life; } catch { }             // 寿元（对照 age 可讲"寿元未尽/将尽"）
            try { data["reputation"] = (int)dyn.reputation; } catch { }

            // —— 死亡事实（渲染层拼「殁于X年X月·卒于(x,y)·死于谁手」）——
            // dieMonth 真实类型非 int（⑦ 探针 10-16 实测 (int)dynamic 抛异常 → 字段静默缺失），
            // 改 Convert.ToInt32 宽容转换（uint/long/int 通吃）。
            try
            {
                object raw = (object)((dynamic)unitDieData).dieMonth;
                if (raw != null) data["died_month"] = Convert.ToInt32(raw);
            }
            catch { }
            try { data["die_point"] = (string)dyn.point; } catch { }
            try
            {
                string kid = (string)dyn.killUnitID;
                if (!string.IsNullOrEmpty(kid))
                {
                    string kn = "";
                    try { var u = g.world.unit.GetUnit(kid); if (u != null) kn = u.data.unitData.propertyData.GetName(); } catch { }
                    if (string.IsNullOrEmpty(kn))
                    {
                        object d2 = null;
                        try { d2 = (object)((dynamic)DeadLedger()).allUnitDie[kid]; } catch { }
                        kn = DeadName(d2);
                    }
                    if (!string.IsNullOrEmpty(kn)) data["killer"] = kn;
                }
            }
            catch { }

            if (Has("brief"))
            {
                // 关系三项（冻结值）：死者档案的关系簿快照对**玩家**（探针实锤 GetIntim/GetRelation 可调）。
                // ref 不给 —— 渲染层对缺 ref 的 relation 按「你」（玩家）措辞，语义即"他生前对你的…".
                try
                {
                    var rel = (dynamic)((dynamic)dieData).relationData;
                    string pid = PlayerUnitId();
                    try { data["intim"] = (int)rel.GetIntim(pid); } catch { }
                    try { data["relation"] = RelationCn(rel.GetRelation(g.world.playerUnit).ToString()); } catch { }
                }
                catch { }
            }
            if (Has("relationships"))
            {
                try
                {
                    var rel = (dynamic)((dynamic)dieData).relationData;
                    var rl = new JObject();
                    try { rl["parent"] = DeadUnitNames(rel.parent); } catch { }
                    try { rl["children"] = DeadUnitNames(rel.children); } catch { }
                    try { rl["brother"] = DeadUnitNames(rel.brother); } catch { }
                    try { rl["parent_back"] = DeadUnitNames(rel.parentBack); } catch { }
                    try { rl["children_back"] = DeadUnitNames(rel.childrenBack); } catch { }
                    try { rl["brother_back"] = DeadUnitNames(rel.brotherBack); } catch { }
                    try { rl["lover"] = DeadUnitNames(rel.lover); } catch { }
                    try { rl["master"] = DeadUnitNames(rel.master); } catch { }
                    try { rl["student"] = DeadUnitNames(rel.student); } catch { }
                    data["relationships"] = rl;
                }
                catch { }
            }
            if (Has("logs"))
            {
                try
                {
                    var ld = (object)((dynamic)unitDieData).logData;   // 死亡瞬间的经历快照（同款解码层）
                    bool rawV, rawR;
                    var vital = LogsArr(GetLogList(ld, true, out rawV), rawV);
                    var regular = LogsArr(GetLogList(ld, false, out rawR), rawR);
                    data["logs"] = new JObject { ["vital"] = vital, ["regular"] = regular };
                }
                catch { }
            }
            return data;
        }

        private static string RealmOf(object dyn, object pd, JObject diag)
        {
            int rowId = -1, curGrade = -1;
            int? dynId = DynOf(dyn, "gradeID");          // 面板同源（DynInt 层）
            int rawId = -1;
            try { rawId = (int)((dynamic)pd).gradeID; } catch { }
            rowId = dynId ?? rawId;
            try { curGrade = (int)((dynamic)dyn).curGrade; } catch { }
            if (diag != null)
            {
                diag["grade_id"] = rowId;
                if (rawId != rowId) diag["grade_id_raw"] = rawId;
                diag["cur_grade"] = curGrade;
            }

            // ① 行号假设 + 自洽校验
            if (rowId > 0 && curGrade > 0)
            {
                try
                {
                    // IL2CPP 集合禁 foreach：走 dynamic 的 Count + 索引器（同仓既有写法）
                    var list = (dynamic)g.conf.roleGrade.allConfList;
                    if (list == null) throw new System.Exception("allConfList 为空");
                    int cnt = (int)list.Count;
                    for (int off = 1; off >= 0; off--)          // 1 基优先，再试 0 基
                    {
                        int idx = rowId - off;
                        if (idx < 0 || idx >= cnt) continue;
                        var it = list[idx];
                        if (it == null) continue;
                        int g = (int)it.grade;
                        // 自洽判据：行内大境界号必须与 curGrade 对上（容忍 curGrade 0 基）
                        if (g != curGrade && g != curGrade + 1) continue;
                        string gn = "", pn = "";
                        try { gn = GameTool.LS(it.gradeName); } catch { }
                        try { pn = GameTool.LS(it.phaseName); } catch { }
                        if (string.IsNullOrEmpty(gn)) continue;
                        if (diag != null)
                        {
                            diag["realm_source"] = off == 1 ? "行号命中(1基)" : "行号命中(0基)";
                            diag["grade"] = g;
                            diag["phase"] = (int)it.phase;
                        }
                        if (!_realmLogged)
                        {
                            _realmLogged = true;
                            ModMain.P("[Realm] gradeID=" + rowId + " curGrade=" + curGrade
                                      + " → 行#" + idx + " grade=" + g + " phase=" + (int)it.phase
                                      + " ⇒ " + gn + pn);
                        }
                        return gn + pn;                          // "结晶" + "后期"
                    }
                }
                catch { }
            }

            // ② 退回：curGrade 当大境界号（丢"期"）
            if (curGrade > 0)
            {
                try
                {
                    string r = g.conf.roleGrade.GetGradeName(curGrade);
                    if (!string.IsNullOrEmpty(r))
                    {
                        if (diag != null) diag["realm_source"] = "curGrade退(丢期)";
                        if (!_realmLogged)
                        {
                            _realmLogged = true;
                            ModMain.P("[Realm] ★行号自洽校验未过★ gradeID=" + rowId + " curGrade=" + curGrade
                                      + " → 退回 curGrade ⇒ " + r + "（若面板带「期」，说明行号读法要调）");
                        }
                        return r;
                    }
                }
                catch { }
            }

            // ③ 最后：老写法（已知会越界钳到最高档），留痕标明可疑
            try
            {
                string r = g.conf.roleGrade.GetGradeName(rowId);
                if (diag != null) diag["realm_source"] = "★旧写法(可能越界钳制)";
                if (!string.IsNullOrEmpty(r)) return r;
            }
            catch { }
            if (diag != null) diag["realm_source"] = "未取到";
            return "";
        }

        /// <summary>境界自检日志只打一次（换人不再刷屏，真要多个样本可临时去掉这个闸）。</summary>
        private static bool _realmLogged;

        /// <summary>
        /// 单属性采集：**只认动态层 `DynInt`**（面板口径：含加成 + 上下限钳制）；拿不到就**不落键**。
        /// toYear=账面月 ÷12 取整为年。
        ///
        /// 删掉裸字段兜底（用户拍板"链路确定了就别再兜底"）：原来 `DynOf` 为 null 时退
        /// `PdOf`（`propertyData` 裸字段）—— 那个值**未加成、未钳制**，与游戏面板显示的不是同一个数。
        /// 这正是 09-13「境界/声望取错」那条 bug 的**同一形态**（当时是整段读了裸字段），
        /// 只是被降级成了兜底：一旦命中，模型看到的属性与面板不一致，**而且没有任何信号**。
        ///
        /// 现在：缺失即缺失（`字段缺失 ≠ 否定`，模型不会拿一个错数去推理），并把**属性名**记进
        /// `DynInt 缺失` 日志（去重封顶）—— 若真机上出现某属性恒定缺失，那行日志会立刻指出来，
        /// 到时再**有针对性地**补读取路径，而不是靠一个会编数的兜底蒙混过去。
        /// </summary>
        private static void SetAttr(JObject t, object dyn, object pd, string key, string prop, bool toYear = false)
        {
            int? v = DynOf(dyn, prop);
            if (v == null)
            {
                if (_dynMiss.Count < 60 && _dynMiss.Add(prop))
                    ModMain.P("[Attrs] ★DynInt 缺失★ prop=" + prop + "（key=" + key
                              + "）—— 该属性本次不落键（旧行为是退裸字段，值与面板不一致，已删）");
                return;
            }
            t[key] = toYear ? v.Value / 12 : v.Value;
        }

        /// <summary>`DynInt 缺失` 日志去重（属性名 → 已报过），封顶 60 条防刷屏。</summary>
        private static readonly HashSet<string> _dynMiss = new HashSet<string>();

        /// <summary>WorldUnitDynData.&lt;prop&gt;（DynInt interop 包装）动态取值，反射走（编译期不可直引该类型）：
        /// 优先 Value(null,true,true) 三参重载（含加成+上下限钳制，面板同款），失败回退 .value。</summary>
        private static int? DynOf(object dyn, string prop)
        {
            if (dyn == null || string.IsNullOrEmpty(prop)) return null;
            try
            {
                var p = dyn.GetType().GetProperty(prop);
                if (p == null) return null;
                var box = p.GetValue(dyn, null);
                if (box == null) return null;
                var ty = box.GetType();
                foreach (var m in ty.GetMethods())
                {
                    if (m.Name != "Value") continue;
                    var ps = m.GetParameters();
                    if (ps.Length != 3) continue;   // 只要 (IList<string>,bool,bool) 重载；单参 Value(tag) 不做钳制
                    try { return Convert.ToInt32(m.Invoke(box, new object[] { null, true, true })); } catch { }
                }
                var vp = ty.GetProperty("value");
                if (vp != null) { try { return Convert.ToInt32(vp.GetValue(box, null)); } catch { } }
            }
            catch { }
            return null;
        }

        /// <summary>PropertyData 裸字段兜底读取（interop 以同名属性暴露；兼容字段形态），失败返回 null 不落键。</summary>
        private static int? PdOf(object pd, string prop)
        {
            if (pd == null || string.IsNullOrEmpty(prop)) return null;
            try
            {
                var p = pd.GetType().GetProperty(prop);
                if (p != null) return Convert.ToInt32(p.GetValue(pd, null));
                var f = pd.GetType().GetField(prop);
                if (f != null) return Convert.ToInt32(f.GetValue(pd));
            }
            catch { }
            return null;
        }

        /// <summary>道心：TaoistHeart.HeartConf()/SeedConf() → ConfTaoistHeartItem/ConfTaoistSeedItem.name（无则“无”）。</summary>
        private static void BuildHeart(object propertyData, JObject st)
        {
            try
            {
                var pd = (dynamic)propertyData;
                var heart = pd.heart;
                if (heart == null) { st["heart"] = "无"; return; }
                int cid = 0;
                try { cid = (int)heart.confID; } catch { }
                string state = "";
                try { state = heart.state.ToString(); } catch { }
                if (cid == 0)
                {
                    // 种子期或空
                    try
                    {
                        var seed = heart.SeedConf();
                        if (seed != null)
                        {
                            string sname = null;
                            try { sname = seed.name; } catch { try { sname = ((dynamic)seed).name; } catch { } }
                            if (!string.IsNullOrEmpty(sname)) try { sname = GameTool.LS(sname); } catch { }
                            st["heart"] = string.IsNullOrEmpty(sname) ? "道种" : sname;
                            st["heart_confID"] = cid;
                            if (!string.IsNullOrEmpty(state)) st["heart_state"] = state;
                            return;
                        }
                    }
                    catch { }
                    st["heart"] = "无";
                    if (!string.IsNullOrEmpty(state)) st["heart_state"] = state;
                    return;
                }
                object conf = null;
                try { conf = heart.HeartConf(); } catch { }
                if (conf == null) try { conf = g.conf.taoistHeart.GetItem(cid); } catch { }
                string hname = null, hdesc = null;
                if (conf != null)
                {
                    try { hname = ((dynamic)conf).name; } catch { }
                    try { hdesc = ((dynamic)conf).desc; } catch { }
                    // 有些版本字段为 title / tips
                    if (string.IsNullOrEmpty(hname)) try { hname = ((dynamic)conf).title; } catch { }
                    if (string.IsNullOrEmpty(hname)) try { hname = ((dynamic)conf).tips; } catch { }
                }
                if (!string.IsNullOrEmpty(hname)) try { hname = GameTool.LS(hname); } catch { }
                if (!string.IsNullOrEmpty(hdesc)) try { hdesc = GameTool.LS(hdesc); } catch { }
                st["heart"] = string.IsNullOrEmpty(hname) ? ("道心#" + cid) : hname;
                st["heart_confID"] = cid;
                if (!string.IsNullOrEmpty(hdesc)) st["heart_desc"] = hdesc;
                if (!string.IsNullOrEmpty(state)) st["heart_state"] = state;
            }
            catch
            {
                try { st["heart"] = "[待真机]道心(heart.confID 文案待核)"; } catch { }
            }
        }

        /// <summary>宗门名：优先 wub.data.school.name / GetName(true)（dump 13507），空schoolID即散修。</summary>
        public static string SectName(WorldUnitBase wub)
        {
            try
            {
                if (wub != null)
                {
                    var s = wub.data?.school;
                    if (s != null)
                    {
                        string n = null;
                        try { n = s.name; } catch { }
                        if (string.IsNullOrEmpty(n)) try { n = s.GetName(true); } catch { }
                        if (!string.IsNullOrEmpty(n))
                        {
                            try { return GameTool.LS(n); } catch { return n; }
                        }
                    }
                    string sid = null;
                    try { sid = wub.data?.unitData?.schoolID; } catch { }
                    if (string.IsNullOrEmpty(sid)) return "散修";
                    return SectName(sid);
                }
            }
            catch { }
            return "散修";
        }

        /// <summary>宗门名（按schoolID查）：兼容 rankings/search 仅有ID的场景，空即散修。</summary>
        public static string SectName(string schoolID)
        {
            if (string.IsNullOrEmpty(schoolID)) return "散修";
            try
            {
                var build = g.world.build.GetBuild(schoolID);
                if (build != null)
                {
                    var school = build as MapBuildSchool;
                    if (school != null && !string.IsNullOrEmpty(school.name))
                    {
                        try { return GameTool.LS(school.name); } catch { return school.name; }
                    }
                }
                // 遍历所有 MapBuildSchool 按 buildData.id 匹配（schoolID 可能是 conf id）
                try
                {
                    var schools = g.world.build.GetBuilds<MapBuildSchool>();
                    var dyn = (dynamic)schools;
                    int cnt = (int)dyn.Count;
                    for (int i = 0; i < cnt; i++)
                    {
                        var s = dyn[i] as MapBuildSchool;
                        if (s == null || s.buildData == null) continue;
                        if (s.buildData.id == schoolID && !string.IsNullOrEmpty(s.name))
                        {
                            try { return GameTool.LS(s.name); } catch { return s.name; }
                        }
                    }
                }
                catch { }
                return "[待真机]宗门名(id=" + schoolID + ")";
            }
            catch { return "[待真机]宗门名(id=" + schoolID + ")"; }
        }

        /// <summary>爱好：优先 wub.data.GetHobbyItems()（dump 13517，ConfRoleCreateHobbyItem.name），回退 pd.hobby。</summary>
        private static JArray HobbyNames(WorldUnitBase wub)
        {
            var names = new JArray();
            if (wub == null) return names;
            // 主路径：GetHobbyItems() 直接返 ConfRoleCreateHobbyItem 数组
            try
            {
                var list = wub.data.GetHobbyItems();
                if (list != null)
                {
                    var dyn = (dynamic)list;
                    int cnt = 0;
                    try { cnt = (int)dyn.Length; } catch { try { cnt = (int)dyn.Count; } catch { } }
                    for (int i = 0; i < cnt; i++)
                    {
                        var hi = dyn[i];
                        if (hi == null) continue;
                        try
                        {
                            // hi 可能是 ConfRoleCreateHobbyItem 本身
                            string n = null;
                            try { n = hi.name; } catch { try { n = hi.hobbyItem.name; } catch { } }
                            if (!string.IsNullOrEmpty(n))
                            {
                                try { n = GameTool.LS(n); } catch { }
                                names.Add(n);
                            }
                        }
                        catch { }
                    }
                    if (names.Count > 0) return names;
                }
            }
            catch { }
            // 回退：pd.hobby -> WorldUnitHobbyBase.hobbyItem.name
            try
            {
                var pd = wub.data?.unitData?.propertyData;
                var hobbyArr = pd?.hobby;
                if (hobbyArr == null) return names;
                var dyn = (dynamic)hobbyArr;
                int cnt = 0;
                try { cnt = (int)dyn.Length; } catch { try { cnt = (int)dyn.Count; } catch { } }
                for (int i = 0; i < cnt; i++)
                {
                    var h = dyn[i];
                    if (h == null) continue;
                    try
                    {
                        var hi = h.hobbyItem;
                        if (hi != null && hi.name != null)
                        {
                            string n = hi.name;
                            try { n = GameTool.LS(n); } catch { }
                            names.Add(n);
                        }
                    }
                    catch { }
                }
            }
            catch { }
            return names;
        }

        /// <summary>爱好（兼容旧调）：propertyData.hobby 数组元素 = WorldUnitHobbyBase。</summary>
        private static JArray HobbyNames(object hobbyArr)
        {
            var names = new JArray();
            if (hobbyArr == null) return names;
            try
            {
                var dyn = (dynamic)hobbyArr;
                int cnt = 0;
                try { cnt = (int)dyn.Length; } catch { try { cnt = (int)dyn.Count; } catch { } }
                for (int i = 0; i < cnt; i++)
                {
                    var h = dyn[i];
                    if (h == null) continue;
                    try
                    {
                        var hi = h.hobbyItem;
                        if (hi != null && hi.name != null)
                        {
                            string n = hi.name;
                            try { n = GameTool.LS(n); } catch { }
                            names.Add(n);
                        }
                    }
                    catch { }
                }
            }
            catch { }
            return names;
        }

        /// <summary>
        /// 分类背包（主输出）：按 PropsType 分五类（1丹符/2书籍/3装备/4材料/5其他），类内按单价 worth 降序。
        /// worth=ConfItemPropsItem.worth（官方悬停出售价实锤 types36:22837）；total=栈总值 worth×count。
        /// 裁剪：各类合计种类 ≤ SmallBagBudget(12) 全显不裁；否则每类取 top inventoryTop，其余聚合进
        /// misc{kinds,pieces,worth}。type=0(Money，灵石) 不进 props——已有 money 字段，避免双列
        /// （真机实证 A05：props[0] 灵石×381 与 money:381 重复）。执行层赠送/偷窃按名扫全量 allProps，
        /// 此处裁剪只影响展示。inventoryTop<=0 全量不裁剪。
        /// </summary>
        private const int InventorySmallBagBudget = 12;

        /// <summary>性格（NPC 面板「内在性格」「外在性格」）：inTrait=内（1 个），outTrait1/2=外（最多 2 个，面板显示两条）。
        /// 输出名字 + 面板描述：inner_desc/outer_desc 与名字平行，取 conf 行
        /// xdash_54sd（LS key=role_character_descN，反编+LocalText ，如 邪恶→“唯已所欲，从来不管其它人的感受。”），
        /// 查表 g.conf.roleCreateCharacter.GetItem(id) + GameTool.LS（与名字同链）。
        /// 返回 {inner, inner_desc?, outer[], outer_desc?}；未设置/读取失败项不出现而非空串。</summary>
        private static JObject PersonalityOf(DataUnit.PropertyData pd)
        {
            var o = new JObject();
            var outer = new JArray();
            var outerDesc = new JArray();
            int inId = SafeIntTrait(pd, "inTrait");
            o["inner"] = TraitCn(inId);
            string inDesc = TraitDesc(inId);
            if (!string.IsNullOrEmpty(inDesc)) o["inner_desc"] = inDesc;
            foreach (var f in new[] { "outTrait1", "outTrait2" })
            {
                int id = SafeIntTrait(pd, f);
                string nm = TraitCn(id);
                if (string.IsNullOrEmpty(nm)) continue;
                outer.Add(nm);
                outerDesc.Add(TraitDesc(id) ?? "");   // 与 outer 按下标对齐，缺描述补空串
            }
            o["outer"] = outer;
            if (outer.Count > 0) o["outer_desc"] = outerDesc;
            return o;
        }

        private static int SafeIntTrait(DataUnit.PropertyData pd, string field)
        {
            if (pd == null) return 0;
            try
            {
                var d = (dynamic)pd;
                switch (field)
                {
                    case "inTrait": return (int)d.inTrait;
                    case "outTrait1": return (int)d.outTrait1;
                    case "outTrait2": return (int)d.outTrait2;
                }
            }
            catch { }
            return 0;
        }

        private static string TraitCn(int id)
        {
            if (id <= 0) return "";
            try
            {
                var item = g.conf.roleCreateCharacter.GetItem(id);
                if (item != null)
                {
                    string n = null;
                    try { n = ((dynamic)item).sc5asd_sd34; } catch { }
                    if (!string.IsNullOrEmpty(n)) { try { n = GameTool.LS(n); } catch { } return n; }
                }
            }
            catch { }
            return "";
        }

        /// <summary>性格描述（面板「内在性格：邪恶，唯已所欲…」冒号后整句）：conf 行 xdash_54sd
        /// （LS key=role_character_descN）→ GameTool.LS；失败返回 ""（调用方省略，不占键）。</summary>
        private static string TraitDesc(int id)
        {
            if (id <= 0) return "";
            try
            {
                var item = g.conf.roleCreateCharacter.GetItem(id);
                if (item != null)
                {
                    string d = null;
                    try { d = ((dynamic)item).xdash_54sd; } catch { }
                    if (!string.IsNullOrEmpty(d)) { try { d = GameTool.LS(d); } catch { } return d; }
                }
            }
            catch { }
            return "";
        }

        // ---------- 面板档位词（魅力/声望，与游戏 NPC 面板一致的中文标签） ----------
        // 游戏侧：g.conf.roleBeauty.GetItemInBeauty(value).text / roleReputation.GetItemInReputation(value).text，
        // 入参取 dynUnitData.beauty/reputation.value（反编实锤 types36:14420/14426），LS 后即面板中文
        // （如 魅力：出众、声望：默默无闻）。供 L1/Player 段用；失败返回 ""（Python 端省略）。

        /// <summary>dynUnitData 当前值（优先）；读不到退 propertyData。</summary>
        private static int DynOrPropValue(WorldUnitBase wub, string which)
        {
            try
            {
                var d = (dynamic)wub.data.dynUnitData;
                if (which == "beauty") return (int)d.beauty.value;
                return (int)d.reputation.value;
            }
            catch { }
            try
            {
                var pd = wub.data.unitData.propertyData;
                if (pd == null) return -1;
                return which == "beauty" ? (int)pd.beauty : (int)pd.reputation;
            }
            catch { }
            return -1;
        }

        public static string BeautyLabel(WorldUnitBase wub)
        {
            int v = DynOrPropValue(wub, "beauty");
            if (v < 0) return "";
            try
            {
                var it = g.conf.roleBeauty.GetItemInBeauty(v);
                if (it != null)
                {
                    string s = null;
                    try { s = ((dynamic)it).text; } catch { }
                    if (!string.IsNullOrEmpty(s)) { try { s = GameTool.LS(s); } catch { } return s; }
                }
            }
            catch { }
            return "";
        }

        public static string ReputationLabel(WorldUnitBase wub)
        {
            int v = DynOrPropValue(wub, "reputation");
            if (v < 0) return "";
            try
            {
                var it = g.conf.roleReputation.GetItemInReputation(v);
                if (it != null)
                {
                    string s = null;
                    try { s = ((dynamic)it).text; } catch { }
                    if (!string.IsNullOrEmpty(s)) { try { s = GameTool.LS(s); } catch { } return s; }
                }
            }
            catch { }
            return "";
        }

        /// <summary>
        /// 分类背包一栈的中间态。为什么要单独存一笔：11-10 之前 desc 是**采集时**就算的，
        /// 而采集发生在裁剪之前 —— 一个 200 件的大背包，`inventory_top=3` 只会展示 15 件，
        /// 剩下 185 件的 `PropsDescText`（每件 new BattleSkillValueData + 占位符扫描）纯属白烧。
        /// 现在只给**最终真的会输出**的那些栈算 desc。misc 聚合不展示 desc，故不受影响。
        /// </summary>
        private sealed class BagStack
        {
            public int Type;
            public JObject Item;
            /// <summary>原始数据栈（`DataProps.PropsData`）。只在真正输出时用一次，
            /// 拿它调 `PropsDescText` 解占位符。</summary>
            public object Stack;
            public string RawDesc;
            public string RawName;
        }

        /// <param name="invCats">非空 = 只出点名的类别（该类**不裁剪**，全量列出）。</param>
        /// <param name="invFind">非空 = 按名字模糊查找（不受类别与 top-N 限制）。</param>
        private static JArray PropsGroups(object udObj, int inventoryTop,
                                          List<string> invCats = null, string invFind = null)
        {
            var arr = new JArray();
            if (udObj == null) return arr;
            bool targetedCat = invCats != null && invCats.Count > 0;
            // ★ 必须 Trim 后再判空 ★：`"".IndexOf("")` 恒为 0 —— 若模型传了 `"inventory_find": "  "`，
            // 不 Trim 就会变成"查找空串"⇒ 匹配**全部**道具 ⇒ 静默退化成 inventory_all。
            // 同理 invCats 里的空白项也要剔掉，否则 wantTypes 为空集、整个背包被筛没。
            if (invFind != null) { invFind = invFind.Trim(); if (invFind.Length == 0) invFind = null; }
            if (invCats != null)
            {
                var cleaned = new List<string>();
                foreach (var c in invCats)
                {
                    string t = c == null ? null : c.Trim();
                    if (!string.IsNullOrEmpty(t)) cleaned.Add(t);
                }
                invCats = cleaned.Count > 0 ? cleaned : null;
                targetedCat = invCats != null;
            }
            bool findMode = !string.IsNullOrEmpty(invFind);
            // 点名的类别 → type 集合。认不出的名字直接忽略（schema 已限枚举，这里只是不炸）。
            var wantTypes = new HashSet<int>();
            if (targetedCat)
                foreach (var c in invCats)
                {
                    int t = PropsCatType(c);
                    if (t > 0) wantTypes.Add(t);
                }
            try
            {
                var propData = ((dynamic)udObj).propData;
                if (propData == null) return arr;
                object list = null;
                try { list = ((dynamic)propData).allProps; } catch { }
                if (list == null) try { list = ((dynamic)propData).CloneAllProps(); } catch { }
                if (list == null) return arr;
                var dyn = (dynamic)list;
                int cnt = 0;
                try { cnt = (int)dyn.Count; } catch { return arr; }

                // 1) 全栈采集并按 PropsType 分组（未知 type 归"其他"）。此步**不读 desc**。
                var byCat = new SortedDictionary<int, List<BagStack>>();
                int totalKinds = 0;
                for (int i = 0; i < cnt; i++)
                {
                    var p = dyn[i];
                    if (p == null) continue;
                    string n = null;
                    try { n = p.propsItem.name; } catch { }
                    if (string.IsNullOrEmpty(n)) continue;
                    string ln = n;
                    try { ln = GameTool.LS(n); } catch { ln = n; }
                    if (string.IsNullOrEmpty(ln)) ln = n;
                    int type = 5;
                    try { type = (int)p.propsItem.type; } catch { }
                    if (type <= 0 || type > 5) type = 5;   // 0=Money 货币不进背包分类；越界归"其他"

                    // —— 定向档的筛选放在**算 desc 之前**，筛掉的栈一分钱不花 ——
                    if (targetedCat && !wantTypes.Contains(type)) continue;
                    if (findMode)
                    {
                        bool hit = ln.IndexOf(invFind, StringComparison.OrdinalIgnoreCase) >= 0
                                   || (n != null && n.IndexOf(invFind, StringComparison.OrdinalIgnoreCase) >= 0);
                        if (!hit) continue;
                    }

                    int count = 1;
                    try { count = (int)p.propsCount; } catch { }
                    int worth = 0;
                    try { worth = (int)p.propsItem.worth; } catch { }
                    string rawDesc = null;
                    try { rawDesc = p.propsItem.desc; } catch { }
                    var bs = new BagStack
                    {
                        Type = type,
                        Item = new JObject { ["name"] = ln, ["count"] = count, ["worth"] = worth, ["total"] = worth * count },
                        Stack = (object)p,
                        RawDesc = rawDesc,
                        RawName = n,
                    };
                    if (!byCat.TryGetValue(type, out var lst)) { lst = new List<BagStack>(); byCat[type] = lst; }
                    lst.Add(bs);
                    totalKinds++;
                }

                // 2) 裁剪与出组（固定枚举序：丹符→书籍→装备→材料→其他）
                //    ★ 定向档一律不裁 ★ 点名一类 = 已经是全量的 1/5，再裁就回到"看得见列表、
                //    找不到东西"的老毛病上；按名查更不该裁（它本来就是奔着某一件去的）。
                //    只有**默认档**（invCats/invFind 都没给）才沿用「小背包全显 / 大背包每类 top-N」。
                bool truncate = !targetedCat && !findMode
                                && inventoryTop > 0 && totalKinds > InventorySmallBagBudget;
                foreach (var kv in byCat)
                {
                    var items = kv.Value;
                    items.Sort((a, b) => ((int)b.Item["worth"]).CompareTo((int)a.Item["worth"]));
                    var g = new JObject { ["cat"] = PropsCatName(kv.Key) };
                    // 只按名查时，空类别跳过 —— 那条问题是"有没有这件东西"，不是"各类都有什么"
                    if (findMode && !targetedCat && items.Count == 0) continue;
                    if (truncate && items.Count > inventoryTop)
                    {
                        var top = new JArray();
                        int mk = 0, mp = 0, mw = 0;
                        for (int i = 0; i < items.Count; i++)
                        {
                            if (i < inventoryTop) { FillDesc(items[i]); top.Add(items[i].Item); }
                            else { mk++; mp += (int)items[i].Item["count"]; mw += (int)items[i].Item["total"]; }
                        }
                        g["items"] = top;
                        g["misc"] = new JObject { ["kinds"] = mk, ["pieces"] = mp, ["worth"] = mw };
                    }
                    else
                    {
                        var top = new JArray();
                        foreach (var bs in items) { FillDesc(bs); top.Add(bs.Item); }
                        g["items"] = top;
                    }
                    arr.Add(g);
                }
            }
            catch { }
            return arr;
        }

        /// <summary>
        /// 给一栈补 desc（悬浮窗悬停文案同源：ConfItemPropsItem.desc → LS + 占位符解析 + 剥标签）。
        /// 只在确定要输出这一栈时调用；失败静默（desc 是加分项，缺了不影响其余字段）。
        /// </summary>
        private static void FillDesc(BagStack bs)
        {
            if (bs == null || bs.Item == null || bs.Stack == null) return;
            var jo = bs.Item;
            if (!string.IsNullOrEmpty((string)jo["desc"])) return;   // 已算过
            if (string.IsNullOrEmpty(bs.RawDesc)) return;
            try
            {
                // 形态与旧版逐字一致（`p`/`d`/`jo` 同名）：这样"介绍必须走 PropsDescText"
                // 那条契约测试（test_item_desc_sigil_contract）仍然盯得住这个落点。
                var p = (dynamic)bs.Stack;
                string d = bs.RawDesc;
                string td = PropsDescText((DataProps.PropsData)p, (object)p.propsItem, d, "inventory");
                if (!string.IsNullOrEmpty(td)) jo["desc"] = td;
            }
            catch { }
        }

        /// <summary>类别中文名 → PropsType（PropsCatName 的反函数；认不出返回 0）。</summary>
        private static int PropsCatType(string cat)
        {
            switch ((cat ?? "").Trim())
            {
                case "丹符": return 1;
                case "书籍": return 2;
                case "装备": return 3;
                case "材料": return 4;
                case "其他": return 5;
                default: return 0;
            }
        }

        /// <summary>PropsType 枚举（0Money/1Pill/2Martial/3Equip/4Material/5Other）→ UI 页签中文名（types41_propstype.txt）。</summary>
        private static string PropsCatName(int type)
        {
            switch (type)
            {
                case 1: return "丹符";
                case 2: return "书籍";
                case 3: return "装备";
                case 4: return "材料";
                default: return "其他";
            }
        }

        /// <summary>身上装备（unitData.equips soleID 列表 → DataProps.GetProps(soleID)）：[{name,worth}...]（唯一实例，worth=单价出售价）。</summary>
        private static JArray EquipsDetails(object udObj)
        {
            var arr = new JArray();
            if (udObj == null) return arr;
            try
            {
                var dynUd = (dynamic)udObj;
                var equips = dynUd.equips;
                if (equips == null) return arr;
                var dynEq = (dynamic)equips;
                int cnt = 0;
                try { cnt = (int)dynEq.Count; } catch { try { cnt = (int)dynEq.Length; } catch { return arr; } }
                var propData = dynUd.propData;
                for (int i = 0; i < cnt; i++)
                {
                    string sid = null;
                    try { sid = (string)dynEq[i]; } catch { try { sid = dynEq[i].ToString(); } catch { } }
                    if (string.IsNullOrEmpty(sid)) continue;
                    try
                    {
                        var p = ((dynamic)propData).GetProps(sid);
                        if (p == null) continue;
                        string n = null;
                        try { n = p.propsItem.name; } catch { }
                        if (string.IsNullOrEmpty(n)) continue;
                        try { n = GameTool.LS(n); } catch { }
                        int worth = 0;
                        try { worth = (int)p.propsItem.worth; } catch { }
                        var ej = new JObject { ["name"] = n, ["worth"] = worth };
                        try
                        {
                            string d = p.propsItem.desc;   // 装备介绍同源（ConfItemPropsItem.desc）
                            if (!string.IsNullOrEmpty(d))
                            {
                                string td = PropsDescText((DataProps.PropsData)p, (object)p.propsItem, d, "equips");
                                if (!string.IsNullOrEmpty(td)) ej["desc"] = td;
                            }
                        }
                        catch { }
                        arr.Add(ej);
                    }
                    catch { }
                }
            }
            catch { }
            return arr;
        }
    }
}
