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

            string un = null, pn = null;
            try { un = u.data.unitData.propertyData.GetName(); } catch { }
            try { pn = player.data.unitData.propertyData.GetName(); } catch { }
            return !string.IsNullOrEmpty(un) && un == pn;
        }

        /// <summary>玩家自身 unitID（取不到返回空串；诊断日志用）。主线程调用。</summary>
        public static string PlayerUnitId()
        {
            try
            {
                var p = g.world.playerUnit;
                if (p == null) return "";
                return p.data.unitData.unitID ?? "";
            }
            catch { return ""; }
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
        /// </summary>
        public static JObject Build(WorldUnitBase wub, JArray classes, int inventoryTop = 3)
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
                inv["props"] = PropsGroups(ud, inventoryTop); // [{cat,items:[{name,count,worth,total}...],misc:{kinds,pieces,worth}|null}...]
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
                        rl["married"] = new JObject
                        {
                            ["name"] = string.IsNullOrEmpty(mn) ? mid : mn,
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
        ///   · `ToolExecutor.PageLogs` 按 `(page-1)*5` 从**头**切片，而 `log_page` 默认 1 ——
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
                for (int i = cnt - 1; i >= 0; i--)   // ← 倒序：库里旧→新，输出新→旧
                {
                    var item = dyn[i];
                    if (item == null) continue;
                    var jo = new JObject();
                    if (raw)
                    {
                        string t0 = DecodeRawLogRow(item, jo);
                        jo["text"] = string.IsNullOrEmpty(t0) ? "(无文本)" : t0;
                        ja.Add(jo);
                        continue;
                    }
                    try { jo["month"] = item.month; } catch { }
                    string txt = "";
                    try { txt = LogItemText(item); } catch { try { txt = CleanLogText((string)((dynamic)item).DataToString()); } catch { } }
                    jo["text"] = string.IsNullOrEmpty(txt) ? "(无文本)" : txt;
                    ja.Add(jo);
                }
            }
            catch { }
            return ja;
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
                    string un = UnitName(fields[0]);
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

        private static string UnitName(string unitId)
        {
            if (string.IsNullOrEmpty(unitId)) return "";
            try
            {
                var u = g.world.unit.GetUnit(unitId);
                if (u == null) return "";
                return u.data.unitData.propertyData.GetName() ?? "";
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
                        ["name"] = string.IsNullOrEmpty(nm) ? idStr : nm,
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
            if (string.IsNullOrEmpty(s)) return s;
            if (s.IndexOf('&') < 0 && s.IndexOf('$') < 0) return s;
            BattleSkillValueData vd = null;
            try { vd = new BattleSkillValueData(md); } catch { }
            var o = new System.Text.StringBuilder(s.Length + 32);
            int i = 0;
            while (i < s.Length)
            {
                char c = s[i];
                if (c == '&' || c == '$')
                {
                    int close = s.IndexOf(c, i + 1);
                    if (close > i + 1)
                    {
                        string inner = s.Substring(i + 1, close - i - 1);
                        string rep = null;
                        if (c == '$')
                        {
                            try { rep = GameTool.LS(inner); } catch { }
                            // LS 未命中通常原样返回 key —— 那不算替换成功
                            if (!string.IsNullOrEmpty(rep) && rep == inner) rep = null;
                        }
                        else if (vd != null)
                        {
                            rep = TryResolveValue(inner, vd);
                        }
                        if (!string.IsNullOrEmpty(rep)) { o.Append(rep); i = close + 1; continue; }
                    }
                }
                o.Append(c);
                i++;
            }
            return o.ToString();
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

        /// <summary>残留占位符计数（`&amp;`/`$` 成对算一个）—— 只用于"替换干净没有"的留痕。</summary>
        private static int CountSigils(string s)
        {
            if (string.IsNullOrEmpty(s)) return 0;
            int n = 0;
            for (int i = 0; i < s.Length; i++)
            {
                if (s[i] != '&' && s[i] != '$') continue;
                if (s.IndexOf(s[i], i + 1) > i) { n++; i = s.IndexOf(s[i], i + 1); }
            }
            return n;
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

        private static JArray PropsGroups(object udObj, int inventoryTop)
        {
            var arr = new JArray();
            if (udObj == null) return arr;
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

                // 1) 全栈采集并按 PropsType 分组（未知 type 归"其他"）
                var byCat = new SortedDictionary<int, List<JObject>>();
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
                    int count = 1;
                    try { count = (int)p.propsCount; } catch { }
                    int worth = 0;
                    try { worth = (int)p.propsItem.worth; } catch { }
                    var jo = new JObject { ["name"] = ln, ["count"] = count, ["worth"] = worth, ["total"] = worth * count };
                    try
                    {
                        // 道具介绍（悬浮窗悬停文案同源：ConfItemPropsItem.desc，本地化 key 需 LS）
                        string d = p.propsItem.desc;
                        if (!string.IsNullOrEmpty(d))
                        {
                            string ld = null;
                            try { ld = GameTool.LS(d); } catch { ld = d; }
                            jo["desc"] = string.IsNullOrEmpty(ld) ? d : ld;
                        }
                    }
                    catch { }
                    if (!byCat.TryGetValue(type, out var lst)) { lst = new List<JObject>(); byCat[type] = lst; }
                    lst.Add(jo);
                    totalKinds++;
                }

                // 2) 裁剪与出组（固定枚举序：丹符→书籍→装备→材料→其他）
                bool truncate = inventoryTop > 0 && totalKinds > InventorySmallBagBudget;
                foreach (var kv in byCat)
                {
                    var items = kv.Value;
                    items.Sort((a, b) => ((int)b["worth"]).CompareTo((int)a["worth"]));
                    var g = new JObject { ["cat"] = PropsCatName(kv.Key) };
                    if (truncate && items.Count > inventoryTop)
                    {
                        var top = new JArray();
                        int mk = 0, mp = 0, mw = 0;
                        for (int i = 0; i < items.Count; i++)
                        {
                            if (i < inventoryTop) top.Add(items[i]);
                            else { mk++; mp += (int)items[i]["count"]; mw += (int)items[i]["total"]; }
                        }
                        g["items"] = top;
                        g["misc"] = new JObject { ["kinds"] = mk, ["pieces"] = mp, ["worth"] = mw };
                    }
                    else
                    {
                        var top = new JArray();
                        foreach (var it in items) top.Add(it);
                        g["items"] = top;
                    }
                    arr.Add(g);
                }
            }
            catch { }
            return arr;
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
                                string ld = null;
                                try { ld = GameTool.LS(d); } catch { ld = d; }
                                ej["desc"] = string.IsNullOrEmpty(ld) ? d : ld;
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
