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
using System;
using System.Collections.Generic;
using Newtonsoft.Json.Linq;

namespace AgentLoopBridge
{
    public class GameContext
    {
        /// <summary>
        /// 取 NPC 的 L1 快照。返回 {"text": "...", "raw": {...}}。
        /// 自身段字段与 brief 共用 UnitSnapshot（保持一致）；待真机项输出原始值 + [待真机]。
        /// 若单位不存在或桥异常，仍返回兜底文本 + raw.error。
        ///
        /// `roster`（群聊）= **除自己以外的同席者名单**，由 Python 随请求下发（名单的真相在
        /// Python，见 `WsClient.ExecuteRequest` 的 get_context 分支）。非空时 `raw` 里会多出
        /// `others[]`（每个同席者的可观测面 + **观察者视角**的关系/好感/同格）。
        /// 为 null/空 ⇒ **完全不产出这个键** ⇒ 单聊载荷与改动前逐字节相同。
        /// </summary>
        /// `rosterIds` = `{名字 → unitID}`，同席者的**精确身份**（与 `roster` 同一次下发）。
        /// 有了它，`BuildOthers` 走 `ResolveEx(name, id)` 的 ②unitID精确 分支；缺项回落按名。
        ///
        /// `npcUnitId` = **说话者自己**的精确 unitID。空 = 未知 ⇒ 按名解析。
        /// ★ 这一格是真机事故的正面修复：原先只能 `Resolve(npcId)` 全图按名扫，重名时
        ///   **自身段**挂在另一个同名者身上，不报错、不缺字段（用户原话"自身部分内容
        ///   也是错的，太离谱了"）。
        /// </summary>
        public JObject GetL1(string npcId, System.Collections.Generic.List<string> roster = null,
                             System.Collections.Generic.Dictionary<string, string> rosterIds = null,
                             string npcUnitId = "")
        {
            string text;
            JObject raw;
            try
            {
                // npc_id 兼容 unitID 与中文名（Python 侧统一用中文名，见 UnitLookup 注释）。
                // 有精确 id 时**只认它**（②分支）；没有才按名（登记表 ⓪ → 全图 ④）。
                System.Collections.Generic.List<UnitLookup.Candidate> _;
                var wub = string.IsNullOrEmpty(npcUnitId)
                    ? UnitLookup.Resolve(npcId)
                    : UnitLookup.ResolveEx(npcId, npcUnitId, out _);
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
                // 玩家气运（09-26）：从 psnap 的 brief 门控里取出，供 raw.relations.player.luck。
                // 必须提到 try 外声明 —— psnap 是那个块里的局部变量，而 raw 组装在外层作用域。
                JToken pluck = null;
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
                    // 玩家气运（09-26）：brief 门控本就含 luck —— 取出来供 raw.relations.player.luck
                    pluck = psnap["luck"];
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

                // —— 同格有谁（11-11 用户需求）——
                //
                // 起因：玩家反映「NPC 都不知道同一格地区有谁」。此前 L1 只有自己的坐标，
                // 没有任何"这里还站着谁" —— 除非对方恰好在群聊 roster 里。
                // 排序与截断口径见 `UnitSnapshot.SameGridText`（两个方向各取前 6）。
                // 玩家已在该链里被排除：他在下面「玩家」段单独写了一份，并列只是浪费 token。
                var sgPicked = new List<UnitSnapshot.SameGridRow>();
                int sgRest = 0;
                string sgText = "";
                try { sgText = UnitSnapshot.SameGridText(wub, sgPicked, out sgRest); }
                catch (Exception e)
                {
                    try { ModMain.P("[GameContext] 同格段构建失败（不影响其余段）：" + e.Message); } catch { }
                }
                string sgSeg = string.IsNullOrEmpty(sgText) ? "" : sgText + "\n";
                // 结构化那一份（给未来消费者与回归测试用）：只在真有同格者时才产出
                var sgRaw = new JArray();
                foreach (var r in sgPicked)
                    sgRaw.Add(new JObject
                    {
                        ["name"] = r.Name,
                        ["relation"] = r.Relation,
                        ["intim"] = r.Intim,
                    });

                text =
                    $"自身：{name} 性别{sexCn} 位置({px},{py}) 境界{realm} 宗门{sect} 种族{race}{perSeg} 魅力{beauty} 声望{reputation}{luckSeg}\n" +
                    sgSeg +
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
                    // 与玩家相关（游戏 NPC 面板·经历页「与我相关」页签同语义，LocalText
                    // npcInfo_yuwoxiangguan）：**全史**里涉及玩家的条目，取最近 RecentPlayerCap 条。
                    // 独立一段而不是塞进「近况」：①语义（他最近的事 vs 他与玩家之间的事）②差分成本
                    // （他的日常变了不该连累本段重发）③覆盖（久远的往来不会因近期日常太多被挤出上下文）。
                    // 空则整段不渲染 —— 新 NPC / 无往来时不会平白多一段空文本。
                    // 代价：这里会第二次读经历（RecentTexts 各读一次）—— 判定必须发生在
                    // CleanLogText 之前、原始标记还在的时候，独立成一次取数才两边都拿得到原始键。
                    ["recent_with_player"] = UnitSnapshot.RecentWithPlayer(ud.unitID),
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
                            // 玩家气运（先天 born + 后天 added）：09-26 用户拍板 —— 此前玩家气运
                            // 完全没进上下文，而数据**早就在手上**：上面那句
                            // `UnitSnapshot.Build(player, ["brief"])` 的 brief 门控本就含 luck
                            //（与 NPC 侧同一行代码，见 UnitSnapshot 的 brief 段），过去只挑了
                            // realm/sect/sex/mood 就把它丢了。读出来即可、零额外采集成本。
                            // Python 侧成独立的「玩家气运」段（`format_l1_context` 的 player_luck），
                            // 与 NPC 气运段同口径、同样**恒非空**（无气运 ⇒ "身体精神状况正常"）。
                            ["luck"] = new JObject
                            {
                                ["born"] = LuckEntries(pluck, "born"),
                                ["added"] = LuckEntries(pluck, "added"),
                            },
                        }
                    },
                    // 当前游戏时间：NPC 感知不到时间，L1 必须每轮报"今天几号"——
                    // 玩家消息前缀只能告诉它"这句话是哪天说的"，说不出"现在是什么时候"
                    // （纯 NPC 主动开口回合更是一条玩家消息都没有）。Python 成文成独立的
                    // 「当前时间」段（`_CTX_SEGMENTS` 首位），逐段差分 ⇒ 每游戏日只重发一次。
                    ["now"] = NowBlock(),
                };
                // 同席明细（群聊）：**只在真有名单时才写这个键** —— 单聊载荷因此与改动前逐字节相同
                // （单聊 ⇒ roster 空 ⇒ 没有 others 键 ⇒ Python 侧 by_id 为空，行为同改动前）。
                var others = BuildOthers(wub, roster, rosterIds);
                if (others.Count > 0) raw["others"] = others;

                // 同格人物（11-11）：[{name, relation, intim}...]，最多 12 人（两个方向各 6）。
                // 与 `others` 同一套纪律：**身边没人就一个键都不写**，载荷与改动前逐字节相同。
                // `same_grid_rest` = 没列出来的还有几个（城镇里人多时给模型一个量级感）。
                if (sgRaw.Count > 0)
                {
                    raw["same_grid_units"] = sgRaw;
                    if (sgRest > 0) raw["same_grid_rest"] = sgRest;
                }
                // ══ 探针（11-15 联调，验完删）══ 同格段的**产出**（不是"扫到几个人"）。
                // 这一段的失败形态是"静默为空"：站在城里却一个同格都没有，和"这功能没做"
                // 看起来一模一样。所以把列表与文本都打出来。grep `[MAIL-PROBE]`
                try
                {
                    ModMain.P("[MAIL-PROBE] 同格段：npc=" + npcId + "｜人数=" + sgPicked.Count
                              + (sgRest > 0 ? "（另有 " + sgRest + " 人未列）" : "")
                              + "｜列表=" + (sgRaw.Count == 0 ? "空" : sgRaw.ToString(Newtonsoft.Json.Formatting.None))
                              + "｜文本=" + (string.IsNullOrEmpty(sgText) ? "空" : sgText));
                }
                catch { }
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
        /// 同席者明细 `raw.others[]`（群聊）。每个元素 = **观察者视角下"抬眼能看到的"**那一面。
        ///
        /// 契约（Python 侧 `SystemPrompt.format_other_segment` 是消费方，逐字段核对过）：
        ///   必需  `id`（中文名）；解析失败时附 `resolved:false`
        ///   静态面 `name` `sex` `race` `realm` `sect` `age` `title` `beauty_label`
        ///          `reputation_label` `personality`
        ///   动态面 `mood` `point`
        ///   ★视角 `relation` `intim` `same_grid` —— **必须是"观察者 ↔ 对方"这一对**，
        ///          不是"对方 ↔ 玩家"（用 `UnitSnapshot.RelationTo`，别在这里手搓）
        ///
        /// **不要往里放**：`luck`（气运）、`logs`/`recent`（生平）、`talent`、`power`（裸值）、
        /// `hobby`、`life`。那些不是"抬眼就能看到的"，而是"查过资料库"——Python 侧会无视它们，
        /// 放了只是白传（还让载荷变重）。模型要细节应当自己调 `inspect_unit`。
        ///
        /// 容错：**单人失败不拖垮整份快照** —— 取数抛异常时降级成"只认得出是谁"，照常成项。
        /// 解析不到单位的（死了/离场）标 `resolved:false`，Python 据此把他从「在场」段剔除。
        ///
        /// ★ `rosterIds`（名字 → unitID，Python 随名单一起下发）：**有就精确、没有才按名**。
        ///   为什么这一格非补不可 —— 这是本函数原先最隐蔽的故障：
        ///   它只调 `Resolve(名字)`（全图按名扫，重名返回第一个），而紧接着把
        ///   `entry["name"]` 用**解析到的那个人**的真名覆盖。重名时那个真名与查询串
        ///   **逐字相同** ⇒ Python 与模型**永远无法察觉**拿到的是另一个人的境界/宗门/
        ///   关系/好感。`inspect_unit` 那次事故至少"境界对不上"还能起疑，这里连线索都没有。
        /// </summary>
        private static JArray BuildOthers(WorldUnitBase viewer, System.Collections.Generic.List<string> roster,
                                          System.Collections.Generic.Dictionary<string, string> rosterIds = null)
        {
            var arr = new JArray();
            if (viewer == null || roster == null || roster.Count == 0) return arr;
            var seen = new System.Collections.Generic.HashSet<string>();
            foreach (var rawId in roster)
            {
                string id = (rawId ?? "").Trim();
                if (id.Length == 0 || !seen.Add(id)) continue;   // 去重：同名重复项只算一次
                var entry = new JObject { ["id"] = id };

                string exactId = null;
                if (rosterIds != null) rosterIds.TryGetValue(id, out exactId);
                WorldUnitBase other = null;
                try
                {
                    if (!string.IsNullOrEmpty(exactId))
                    {
                        System.Collections.Generic.List<UnitLookup.Candidate> _;
                        other = UnitLookup.ResolveEx(id, exactId, out _);   // ②unitID精确（零歧义）
                    }
                    else other = UnitLookup.Resolve(id);                    // 回落按名（登记表 → 全图）
                }
                catch { }
                if (other == null)
                {
                    // 世界里已经没有这个单位 —— 这是 C# 唯一能告诉 Python"人其实不在了"的通道
                    entry["resolved"] = false;
                    arr.Add(entry);
                    continue;
                }

                entry["resolved"] = true;
                entry["name"] = id;
                try
                {
                    var snap = UnitSnapshot.Build(other, new JArray("brief"));
                    string oname = (string)snap["name"];
                    if (!string.IsNullOrEmpty(oname)) entry["name"] = oname;
                    entry["sex"] = snap["sex"];
                    entry["race"] = snap["race"];
                    entry["realm"] = snap["realm"];
                    entry["sect"] = snap["sect"];
                    entry["age"] = snap["age"];
                    // 道号：`Build` 查表失败时会回 `[待真机]道号名(id=…)` 这种**给开发者看的诊断串**，
                    // 它绝不能进上下文（模型会照着念出来）—— 这里**不发这个键**比让 Python 去挡更干净
                    // （Python 侧 `_title_phrase` 也挡了一道，两边都挡是因为契约有两端）。
                    string title = (string)snap["title"];
                    if (!string.IsNullOrEmpty(title) && !title.StartsWith("[待真机]")) entry["title"] = title;
                    // 容貌 / 声名的**档位词**（与自身段、玩家段同一套面板口径；裸数值会让模型当台词说）
                    string bl = UnitSnapshot.BeautyLabel(other);
                    if (!string.IsNullOrEmpty(bl)) entry["beauty_label"] = bl;
                    string rl = UnitSnapshot.ReputationLabel(other);
                    if (!string.IsNullOrEmpty(rl)) entry["reputation_label"] = rl;
                    entry["mood"] = snap["mood"];
                    entry["personality"] = snap["personality"];
                    try
                    {
                        var oud = other.data.unitData;
                        entry["point"] = new JObject { ["x"] = oud.pointX, ["y"] = oud.pointY };
                    }
                    catch { }
                    // ★ 视角相关三项：观察者 ↔ 对方（方向写反会静默给出反向好感）
                    var rela = UnitSnapshot.RelationTo(viewer, other);
                    entry["relation"] = rela["relation"];
                    entry["intim"] = rela["intim"];
                    entry["same_grid"] = rela["same_grid"];
                }
                catch
                {
                    // 单人取数失败：降级成"只认得出是谁"，绝不因此丢掉整份快照的其余人。
                    // 注意**不标 resolved:false** —— 单位是解析到了的，只是明细没取全；
                    // 误标会让 Python 把他从在场名单里抹掉（人明明还在）。
                }
                arr.Add(entry);
            }
            return arr;
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