/// <summary>
/// npc_id 统一解析口 —— 全工程「字符串 → WorldUnitBase」的唯一入口。
///
/// 背景（实锤见 README 附录 §4）：
///   游戏 g.world.unit.GetUnit(string unitID) 按内部 ID 查找（官方示例
///   GetUnit("NPCID_XXXXXX")，参数名即 unitID），unitID 不是中文名；
///   而本项目 Python 侧的会话/人设一律以中文名为 npc_id（UI 手输、
///   chat_cli --npc、NPC 主动开口 GetName()、NpcPanelButton 同步为 GetName()）。
///
/// 重名事故（本文件被重写的原因）
///   真机：`inspect_unit(target="益婉容")` 返回了**另一个益婉容** ——
///   `[UnitLookup] Resolve('益婉容') → ③全图按名#199 unitID=Xs6JDI`。
///   玩家面板上那个益婉容是结晶后期/声望3379，返回的却是登仙境/声望3129。
///   旧实现的原话是「重名时取第一个匹配（真机随机 NPC 可能重名，**概率低**；需消歧再扩展）」
///   —— **"概率低"是错的**：这游戏全图上千个单位、名字用字池有限，重名是常态不是例外。
///   更糟的是它**完全静默**：不报错、不缺字段，只是把另一个人的档案端给模型。
///
/// 现约定（三条，缺一不可）：
///   ① **能精确就精确**：调用方拿得到 unitID 时（关系簿/搜索结果都会带上）一律传 `unitId`；
///   ② **按名解析必须消歧并上报**：全图扫出**所有**同名者，返回第一个的同时把候选列表交给
///      调用方（`out candidates`），由工具层如实告诉模型"有 N 个同名"，绝不静默选一个；
///   ③ **不轻信 GetUnit**：游戏 `GetUnit` 内部对中文名会**按名兜底**（见下），故非 ASCII 的
///      中文名**不走** `GetUnit`（否则它替我们随机选一个，我们连数都没得数）；走了的必须
///      **回验 `unitID` 相等**，不等就当作没命中。
///
/// 硬约束：触 g.world，仅主线程调用（MainThreadDispatcher 侧保证）。
/// </summary>
using System;
using System.Collections.Generic;

namespace AgentLoopBridge
{
    internal static class UnitLookup
    {
        /// <summary>同名候选（仅在按名解析命中多个时非空）。字段供工具层原样转结构化 data。</summary>
        internal sealed class Candidate
        {
            public string unitId = "";
            public string name = "";
            public string realm = "";
            public string sect = "";
            public int reputation;
            public int x, y;
        }

        /// <summary>兼容旧签名：按名/ID 解析，忽略歧义详情（内部自用/非查询类调用点）。</summary>
        public static WorldUnitBase Resolve(string npcId)
        {
            List<Candidate> _;
            return ResolveEx(npcId, null, out _);
        }

        // ---------- 「这个名字就是这个人」的权威登记----------
        //
        // 为什么需要它：`npc_id` 全链路是**中文名**（Python 侧会话/人设/存档路径都以名为键，
        // 改成 unitID 是伤筋动骨的迁移）。但**打开对话 UI 的那一刻，C# 手里是真的握着
        // WorldUnitBase 的**（NPC 面板按钮 / 通讯录行点击 / 剧情窗按钮 → `ChatLauncher.OpenForUnit`）。
        // 拿着真身却把它丢掉、后面再按名字全图猜 —— L1 自身段因此可能挂在另一个同名者身上
        // （用户实机反馈"自身部分内容也是错的，太离谱了"）。故：**握住真身的那一刻把名字钉到 unitID**，
        // 之后所有按名解析先走这份登记，零歧义。
        //
        // 只存内存、不落盘：重进游戏/换存档会重新登记；登记的 id 对不上就摘掉并回落（见 ResolveInner ⓪）。
        private static readonly Dictionary<string, string> _pinned = new Dictionary<string, string>();

        /// <summary>
        /// 登记 name → unitID。**调用方必须真的握着那个 WorldUnitBase**（不是按名查出来的），
        /// 否则就是把"猜"的结果固化下来，比不登记更糟。
        /// </summary>
        internal static void Pin(string name, string unitId)
        {
            if (string.IsNullOrEmpty(name) || string.IsNullOrEmpty(unitId)) return;
            string old;
            bool changed = !_pinned.TryGetValue(name, out old) || old != unitId;
            _pinned[name] = unitId;
            if (changed)
                ModMain.P("[UnitLookup] Pin：'" + name + "' → " + unitId
                          + "（打开对话 UI 时握有真身，后续按名解析优先用它）");
        }

        /// <summary>诊断用：登记表当前条目数。</summary>
        internal static int PinnedCount { get { return _pinned.Count; } }

        /// <summary>
        /// 精确解析：`unitId` 非空时**只认这个 ID**（不回退按名 —— 回退就等于又猜一次）；
        /// 否则按 `npcId` 解析。`candidates` 非空 = 该名字全图有多人，返回的是其中第一个。
        /// 任一情况都打一行 `<c>Resolve</c>` 追踪（命中分支 + unitID + 同名计数）。
        /// </summary>
        public static WorldUnitBase ResolveEx(string npcId, string unitId, out List<Candidate> candidates)
        {
            candidates = new List<Candidate>();
            var hit = ResolveInner(npcId, unitId, ref candidates);
            try
            {
                string rid = null;
                try { rid = hit?.data.unitData.unitID; } catch { }
                string amb = candidates.Count > 1 ? " 同名=" + candidates.Count + "人(用 unit_id 精确指定)" : "";
                ModMain.P("[UnitLookup] Resolve('" + npcId + "'"
                          + (string.IsNullOrEmpty(unitId) ? "" : ", unit_id=" + unitId) + ") → "
                          + _lastBranch + " unitID=" + (rid ?? "null") + amb + _lastPnInfo);
            }
            catch { }
            if (candidates.Count <= 1) candidates = new List<Candidate>();   // 1 个 = 无歧义
            return hit;
        }

        /// <summary>最近一次解析的分支（诊断串）。</summary>
        private static string _lastBranch = "?";
        private static string _lastPnInfo = "";

        /// <summary>码点串（隐蔽字符/全半角差异一枪定位，如「唐炎」=21776 28814）。</summary>
        private static string Codes(string s)
        {
            if (s == null) return "null";
            var sb = new System.Text.StringBuilder();
            for (int i = 0; i < s.Length; i++) { if (i > 0) sb.Append(' '); sb.Append((int)s[i]); }
            return sb.ToString();
        }

        /// <summary>含 CJK 即视为"中文名" —— 这类输入**不许**交给 `g.world.unit.GetUnit`（它会按名兜底选人）。</summary>
        private static bool HasCjk(string s)
        {
            if (string.IsNullOrEmpty(s)) return false;
            for (int i = 0; i < s.Length; i++)
            {
                char c = s[i];
                if (c >= 0x4E00 && c <= 0x9FFF) return true;
                if (c >= 0x3400 && c <= 0x4DBF) return true;
            }
            return false;
        }

        /// <summary>读一个单位的 (unitID, realm, sect, reputation, x, y) —— 消歧候选与日志共用。全字段容错。</summary>
        private static Candidate Describe(WorldUnitBase u)
        {
            var c = new Candidate();
            if (u == null) return c;
            try { c.unitId = u.data.unitData.unitID ?? ""; } catch { }
            try { c.name = u.data.unitData.propertyData.GetName() ?? ""; } catch { }
            try { c.realm = g.conf.roleGrade.GetGradeName(u.data.unitData.propertyData.gradeID) ?? ""; } catch { }
            try { c.sect = UnitSnapshot.SectName(u.data.unitData.schoolID) ?? ""; } catch { }
            try { c.reputation = u.data.unitData.propertyData.reputation; } catch { }
            try { c.x = u.data.unitData.pointX; c.y = u.data.unitData.pointY; } catch { }
            return c;
        }

        private static WorldUnitBase ResolveInner(string npcId, string unitId, ref List<Candidate> candidates)
        {
            _lastPnInfo = "";
            bool hasId = !string.IsNullOrEmpty(unitId);
            if (!hasId && string.IsNullOrEmpty(npcId)) { _lastBranch = "空参"; return null; }

            // ① 玩家直达（最高优先）：玩家真名匹配 / 上下文占位「玩家」/ 常见别名。
            // 真名匹配是零歧义目标（世界唯一），必须压过一切按名查询——
            // 实锤：游戏 g.world.unit.GetUnit("唐炎")内部竟会按名字兜底，先命中了
            // 同名 NPC（Lepxop）→「查玩家经历」恒空（玩家 BOlQu6 有 5 桶数据躺在旁边）。
            var player = g.world.playerUnit;
            if (player != null)
            {
                string pn = null;
                try { pn = player.data.unitData.propertyData.GetName(); } catch { }
                string pid = null;
                try { pid = player.data.unitData.unitID; } catch { }
                bool hitPlayer = (hasId && !string.IsNullOrEmpty(pid) && pid == unitId)
                    || (!hasId && ((!string.IsNullOrEmpty(pn) && pn == npcId)
                                   || npcId == "玩家" || npcId == "player" || npcId == "我"));
                _lastPnInfo = " pn='" + pn + "'(码点 " + Codes(pn) + ") 目标码点 " + Codes(hasId ? unitId : npcId);
                if (hitPlayer)
                {
                    _lastBranch = hasId ? "①玩家直达(unitID)" : "①玩家直达";
                    candidates.Add(Describe(player));
                    return player;
                }
            }
            else _lastPnInfo = " playerUnit=null";

            // ⓪ 权威登记（打开对话 UI 时钉下的 unitID）：**零歧义**，压过全图按名扫描。
            //    排在玩家直达之后：那条"玩家真名必须最高优先"的结论继续成立。
            //    登记失效（换存档/单位消失）→ 摘掉登记并回落常规路径，绝不拿旧 id 硬套。
            if (!hasId)
            {
                string pinned;
                if (_pinned.TryGetValue(npcId, out pinned) && !string.IsNullOrEmpty(pinned))
                {
                    WorldUnitBase pu = null;
                    string got = null;
                    try { pu = g.world.unit.GetUnit(pinned); } catch { }
                    try { got = pu?.data.unitData.unitID; } catch { }
                    if (pu != null && got == pinned)
                    {
                        _lastBranch = "⓪已登记unitID";
                        candidates.Add(Describe(pu));
                        return pu;
                    }
                    _pinned.Remove(npcId);
                    ModMain.P("[UnitLookup] 登记失效（'" + npcId + "' → " + pinned
                              + " 在当前存档查不到），已摘除，回落按名");
                }
            }

            // ② unitID 精确（唯一权威路径）：非空时**只认它**，查不到就返回 null。
            //    必须回验 unitID 相等 —— `GetUnit` 对查不到的串会**按名兜底**，
            //    不回验就会把"随便一个同名的人"当成精确命中，正是本次事故的形态。
            if (hasId)
            {
                try
                {
                    var byId = g.world.unit.GetUnit(unitId);
                    if (byId != null)
                    {
                        string got = null;
                        try { got = byId.data.unitData.unitID; } catch { }
                        if (!string.IsNullOrEmpty(got) && got == unitId)
                        {
                            _lastBranch = "②unitID精确";
                            candidates.Add(Describe(byId));
                            return byId;
                        }
                        _lastBranch = "②unitID不匹配(GetUnit按名兜底返回了 '" + got + "')";
                        return null;     // ★不回退按名：回退 = 再猜一次
                    }
                }
                catch { }
                _lastBranch = "②unitID未命中";
                return null;
            }

            // ③ 非中文输入：可能是 unitID，先试 GetUnit（**回验 + 只记一个**，不算歧义：
            //    ID 是唯一的，命中即是它）。中文输入刻意跳过这一步，理由见文件头 ③。
            if (!HasCjk(npcId))
            {
                try
                {
                    var direct = g.world.unit.GetUnit(npcId);
                    if (direct != null)
                    {
                        string got = null;
                        try { got = direct.data.unitData.unitID; } catch { }
                        if (!string.IsNullOrEmpty(got) && got == npcId)
                        {
                            _lastBranch = "③GetUnit直查(unitID)";
                            candidates.Add(Describe(direct));
                            return direct;
                        }
                    }
                }
                catch { }
            }

            // ④ 中文名兜底：全图按 GetName() 精确匹配，**扫完全部**收集同名者。
            //    以前这里命中即 return —— 那是静默选人；现在继续扫完，把候选交给调用方上报。
            try
            {
                var units = g.world.unit.GetUnits(true);
                int count = units.Count;   // IL2CPP 集合禁 foreach，用 Count + 索引器
                int first = -1;
                WorldUnitBase firstUnit = null;
                for (int i = 0; i < count; i++)
                {
                    var u = units[i];
                    if (u == null) continue;
                    string nm = null;
                    try { nm = u.data.unitData.propertyData.GetName(); } catch { }
                    if (string.IsNullOrEmpty(nm) || nm != npcId) continue;
                    candidates.Add(Describe(u));
                    if (first < 0) { first = i; firstUnit = u; }
                }
                if (firstUnit != null)
                {
                    _lastBranch = "④全图按名#" + first + (candidates.Count > 1 ? "（共" + candidates.Count + "人同名）" : "");
                    return firstUnit;
                }
            }
            catch { }

            _lastBranch = "未命中";
            return null;
        }
    }
}
