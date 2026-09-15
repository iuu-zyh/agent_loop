/// <summary>
/// L1 易变快照 —— request: get_context
///
/// 用官方 GGBH_API 真实读取单位数据，替代旧骨架的硬编码假值：
///   g.world.unit.GetUnit(id) → WorldUnitBase
///   wub.data.unitData（DataUnit.UnitInfoData）→ propertyData / pointX,pointY / relationData
///   relationData.GetIntim(WorldUnitBase) / GetRelation(WorldUnitBase) → 对本单位好感与关系
///
/// 遵守主线程硬约束：本方法仅由 MainThreadDispatcher 在主线程调用，不得在后台线程碰 g.world。
///
/// 传音模式：raw.relations.player.same_grid = NPC 与玩家是否同格（pointX/pointY 比较），
/// Python 端 format_l1_context 据此成文"面对面交谈 / 神识传音"，并约束面对面动作。
///
/// 待真机运行时核对/补齐（官方文档缺公开 entry）：
///   - 境界文案（realm 枚举→中文名）、宗门名（schoolId→名字）：需读 propertyData 与 conf 映射
///   - 近况经历：当前无直接"经历列表"公开读口，先留空占位，后续用 g.events 或 data 字段补
/// </summary>
using Newtonsoft.Json.Linq;

namespace AgentLoopBridge
{
    public class GameContext
    {
        /// <summary>
        /// 取 NPC 的 L1 快照。返回 {"text": "...", "raw": {...}}。
        /// 自身段字段与 brief 共用 UnitSnapshot（保持一致）；待真机项输出原始值 + [待真机]。
        /// 若单位不存在或桥异常，仍返回兜底文本 + raw.error。
        /// </summary>
        public JObject GetL1(string npcId)
        {
            string text;
            JObject raw;
            try
            {
                // npc_id 兼容 unitID 与中文名（Python 侧统一用中文名，见 UnitLookup 注释）
                var wub = UnitLookup.Resolve(npcId);
                if (wub == null)
                {
                    text = $"自身：{npcId}（单位不存在）";
                    raw = new JObject { ["npc_id"] = npcId, ["error"] = "unit not found" };
                    return new JObject { ["text"] = text, ["raw"] = raw };
                }

                // 与 brief 同源：基础字段无条件 + 气运（brief 门控块）；abilities/logs 等重块仍不读，L1 保持轻量。
                // 气运（尤其后天气运）会随逆天改命/奇遇动态变化，属 NPC 对自身命格的认知，L1 必须带上；
                // 频率由 Python 侧控制（每轮取一次，非每 step）
                var snap = UnitSnapshot.Build(wub, new JArray("brief"));
                string name = (string)snap["name"] ?? npcId;
                var pd = wub.data.unitData.propertyData;
                var ud = wub.data.unitData;
                int px = ud.pointX, py = ud.pointY;

                // 与玩家的位置关系（same_grid 与 brief 同源：UnitSnapshot.IsSameGrid）
                var player = g.world.playerUnit;
                int ppx = 0, ppy = 0;
                bool sameGrid = false;
                try
                {
                    var pud = player.data.unitData;
                    ppx = pud.pointX;
                    ppy = pud.pointY;
                    sameGrid = UnitSnapshot.IsSameGrid(wub, player);
                }
                catch { }   // 玩家单位异常时按异地处理（safe 降级）

                string playerName = "";
                string prealm = "", psect = "";
                string psex = "";   // 玩家性别（UnitSexType → 男/女）；L1 玩家段成文要按性别选代词
                JToken pbeauty = null, prepu = null, pmood = null;
                try
                {
                    playerName = player.data.unitData.propertyData.GetName();
                }
                catch { }
                try
                {
                    // 玩家快照同源（UnitSnapshot 基础面：realm/sect/mood 在 brief 门控内）。
                    // 魅力/声望取面板同款中文档位词（如「出众」「默默无闻」）——查表路径/入参
                    // 与游戏 NPC 面板一致（UnitSnapshot.BeautyLabel/ReputationLabel），LLM 才看得到
                    // 与玩家在面板里一致的说法，而不是冷冰冰数值。
                    var psnap = UnitSnapshot.Build(player, new JArray("brief"));
                    prealm = (string)psnap["realm"] ?? "";
                    psect = (string)psnap["sect"] ?? "";
                    psex = (string)psnap["sex"] ?? "";   // ★09-13：原先没取——raw.self.sex 一直有，玩家段却恒缺性别
                    pmood = psnap["mood"];
                    string bl = UnitSnapshot.BeautyLabel(player);
                    string rl = UnitSnapshot.ReputationLabel(player);
                    if (!string.IsNullOrEmpty(bl)) pbeauty = bl;
                    if (!string.IsNullOrEmpty(rl)) prepu = rl;
                }
                catch { }   // 玩家快照失败只缺修为段，不拖垮 L1

                string realm = (string)snap["realm"] ?? "";
                string sect = (string)snap["sect"] ?? "";
                string race = (string)snap["race"] ?? "";
                string sexCn = (string)snap["sex"] ?? "";   // 自身性别（text 成文用；raw 侧本来就带）
                string beauty = snap["beauty"] == null ? "?" : snap["beauty"].ToString();          // 仅供 text 成文
                string reputation = snap["reputation"] == null ? "?" : snap["reputation"].ToString(); // 仅供 text 成文

                // 性格（内 1 + 外 0~2，与 NPC 面板展示一致；查表路径见 UnitSnapshot.PersonalityOf）
                string perInner = "";
                var perOuterList = new System.Collections.Generic.List<string>();
                try
                {
                    var per = snap["personality"] as JObject;
                    if (per != null)
                    {
                        perInner = per["inner"]?.ToString() ?? "";
                        var outer = per["outer"] as JArray;
                        if (outer != null) foreach (var t in outer) { var s = t?.ToString(); if (!string.IsNullOrEmpty(s)) perOuterList.Add(s); }
                    }
                }
                catch { }
                string perSeg = "";
                if (!string.IsNullOrEmpty(perInner) || perOuterList.Count > 0)
                {
                    var tags = new System.Collections.Generic.List<string>();
                    if (!string.IsNullOrEmpty(perInner)) tags.Add("内" + perInner);
                    foreach (var o in perOuterList) tags.Add("外" + o);
                    perSeg = " 性格" + string.Join("·", tags);
                }

                // 气运短语（名+desc 截断；desc 换行已折叠， L1 要带 desc——后天气运 desc 动态且重要）
                string luckBorn = UnitSnapshot.LuckNamesText(snap["luck"], "born", true);
                string luckAdded = UnitSnapshot.LuckNamesText(snap["luck"], "added", true);
                string luckSeg = "";
                if (luckBorn != "" || luckAdded != "")
                {
                    luckSeg = " 气运" + (luckBorn != "" ? "先天" + luckBorn : "");
                    if (luckAdded != "") luckSeg += (luckBorn != "" ? "；后天" : "后天") + luckAdded;
                }

                text =
                    $"自身：{name} 性别{sexCn} 位置({px},{py}) 境界{realm} 宗门{sect} 种族{race}{perSeg} 魅力{beauty} 声望{reputation}{luckSeg}\n" +
                    $"玩家：{playerName} 性别{psex} 境界{prealm} 宗门{psect} 魅力{pbeauty} 声望{prepu} 位置({ppx},{ppy}) 同格={sameGrid} 关系：{(string)snap["relation"]} 好感{snap["intim"]}\n" +
                    "近况：待补充";

                raw = new JObject
                {
                    ["npc_id"] = npcId,
                    ["name"] = name,
                    ["sex"] = snap["sex"],
                    ["race"] = race,
                    ["realm"] = realm,
                    ["sect"] = sect,
                    ["sect_id"] = snap["sect_id"],
                    ["beauty"] = snap["beauty"],
                    // 容貌/声名的**档位词**：与下方玩家段的 beauty/reputation 用同一套
                    // （UnitSnapshot.BeautyLabel / ReputationLabel → 游戏 NPC 面板那张表 + GameTool.LS）。
                    // 为什么成对给：裸数值是面板口径（「魅力3129」），写进上下文会让模型拿数字当台词；
                    // 而玩家段从 09-09 起就一直用档位词、NPC 自己这侧却只有裸值 —— 同一件事两套话术。
                    // 裸值保留（inspect/brief 等别的消费方在用），档位词另开键，互不影响。
                    ["beauty_label"] = UnitSnapshot.BeautyLabel(wub),
                    // 数值字段保持原生类型（int/long），绝不 .ToString()——Python 侧 _fmt_mood 等只认数值，
                    // 传字符串会静默降级成"心境如常"（真机实证：mood="100" → 心情文案恒定）
                    ["reputation"] = snap["reputation"],
                    ["reputation_label"] = UnitSnapshot.ReputationLabel(wub),
                    ["talent"] = snap["talent"],
                    ["age"] = snap["age"],
                    ["life"] = snap["life"],
                    ["mood"] = snap["mood"],
                    ["power"] = snap["power"],
                    // 体魄：UnitSnapshot 不采 hp（只在 stats 块里），此处直接读 propertyData（反编实锤 pd.hp/hpMax）
                    ["health"] = HealthOf(pd),
                    ["health_max"] = HealthMaxOf(pd),
                    ["hobby"] = snap["hobby"],
                    ["title"] = snap["title"],
                    // 性格（与面板展示一致：内 1 + 外 0~2；查表已 LS 成中文名）
                    ["personality"] = snap["personality"],
                    // 气运条目 {name, desc}（后天气运 desc 动态且重要，L1 必须带上；截断由 Python 成文侧兜底）
                    ["luck"] = new JObject
                    {
                        ["born"] = LuckEntries(snap["luck"], "born"),
                        ["added"] = LuckEntries(snap["luck"], "added"),
                    },
                    ["point"] = new JObject { ["x"] = px, ["y"] = py },
                    // 近况经历（改为合并两桶取最近 RecentCap=6 条）：人类可读文本，
                    // Python format_l1_context 消费成「近况」段；空则整段不渲染（差分只发变化段）
                    ["recent"] = UnitSnapshot.RecentTexts(ud.unitID),
                    ["relations"] = new JObject
                    {
                        ["player"] = new JObject
                        {
                            // 玩家真名：Python 成文侧 format_l1_context 取
                            // relations.player.name 成文，缺了只能兜底「玩家」——模型上下文里
                            // 玩家恒为占位符，inspect_unit 按名查玩家必败（真机实证）。
                            ["name"] = string.IsNullOrEmpty(playerName) ? "玩家" : playerName,
                            // 玩家修为/宗门/魅力/声望/心情：NPC 对话需要感知玩家
                            // 境界高下（称呼/敬畏）、身份阵营、外观与名声；mood 供神色措辞。
                            ["realm"] = string.IsNullOrEmpty(prealm) ? null : prealm,
                            ["sect"] = string.IsNullOrEmpty(psect) ? null : psect,
                            // 玩家性别：缺了它 Python 玩家段只能拿「他」当通用代词，
                            // 女玩家会被写成"你与他结着道侣之谊"。
                            ["sex"] = string.IsNullOrEmpty(psex) ? null : psex,
                            ["beauty"] = pbeauty,
                            ["reputation"] = prepu,
                            ["mood"] = pmood,
                            ["relation"] = (string)snap["relation"],
                            ["intim"] = snap["intim"],
                            ["point"] = new JObject { ["x"] = ppx, ["y"] = ppy },
                            ["same_grid"] = sameGrid,
                        }
                    },
                    // 当前游戏时间：NPC 感知不到时间，L1 必须每轮报"今天几号"——
                    // 玩家消息前缀只能告诉它"这句话是哪天说的"，说不出"现在是什么时候"
                    // （纯 NPC 主动开口回合更是一条玩家消息都没有）。Python 成文成独立的
                    // 「当前时间」段（`_CTX_SEGMENTS` 首位），逐段差分 ⇒ 每游戏日只重发一次。
                    ["now"] = NowBlock(),
                };
            }
            catch (System.Exception e)
            {
                text = $"自身：{npcId}（取快照失败：{e.Message}）";
                // 快照失败 ≠ 不知道几号：时钟独立于单位数据，照给（Python 段侧空则整段不渲染）
                raw = new JObject { ["error"] = e.Message, ["now"] = NowBlock() };
            }

            return new JObject { ["text"] = text, ["raw"] = raw };
        }

        /// <summary>
        /// 当前游戏时间块：`{year, month, day, text}`，`text` 形如 `1年1月3日`。
        /// 结构化字段供 Python/测试取用，`text` 是**成文唯一来源**（`UnitSnapshot.CnDate`，
        /// 与玩家消息时间戳 `[1年1月3日]` 同函数 ⇒ 逐字节相同）。
        /// 日历取不到时返回空对象 `{}` —— Python 侧据此让整段不渲染（绝不编造日期）。
        /// </summary>
        private static JObject NowBlock()
        {
            var o = new JObject();
            int acct = NpcInitiativeMonitor.CurrentAccountMonth();   // 账面月
            int day = NpcInitiativeMonitor.CurrentRoundDay();
            if (acct <= 0) return o;
            int y, m;
            UnitSnapshot.SplitAccountMonth(acct, out y, out m);
            o["year"] = y;
            o["month"] = m;
            if (day > 0) o["day"] = day;
            string t = UnitSnapshot.CnDate(acct, day);
            if (t.Length > 0) o["text"] = t;
            return o;
        }

        /// <summary>气运条目数组（{name,desc} 原样透传，不截断——Python 成文侧按需取舍）。</summary>
        private static JArray LuckEntries(JToken luck, string key)
        {
            var arr = new JArray();
            if (luck != null && luck[key] is JArray ja)
                foreach (var it in ja)
                {
                    if (!(it is JObject o)) continue;
                    string n = null, d = null;
                    try { n = o["name"]?.ToString(); } catch { }
                    try { d = o["desc"]?.ToString(); } catch { }
                    if (string.IsNullOrEmpty(n)) continue;
                    var jo = new JObject { ["name"] = n };
                    if (!string.IsNullOrEmpty(d)) jo["desc"] = d;
                    arr.Add(jo);
                }
            return arr;
        }

        /// <summary>当前体魄（pd.hp；UnitSnapshot 不在基础面采集 hp，这里直读补齐，失败返回 null 不拖垮快照）。</summary>
        private static JToken HealthOf(DataUnit.PropertyData pd)
        {
            try { return pd.hp; } catch { return null; }
        }

        /// <summary>体魄上限（pd.hpMax；失败返回 null）。</summary>
        private static JToken HealthMaxOf(DataUnit.PropertyData pd)
        {
            try { return pd.hpMax; } catch { return null; }
        }
    }
}