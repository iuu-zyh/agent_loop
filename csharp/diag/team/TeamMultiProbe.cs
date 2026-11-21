/// <summary>
/// TeamMultiProbe —— **多人组队**验证探针（只观测 + 批量挂气运）。
///
/// ★ 10-21 起这是**唯一保留**的组队探针。同类另两个已删除：
///   `TeamProbe`（可行性探针，结论"气运才是驱动源"已并入产品代码）、
///   `TeamBattleEventProbe`（事件探针，结论"triggerType=6 = BattleEndFront"
///   已被 `tools/TeamBattleFollowup.cs` 直接实现）。
///   本文提到它们的地方都是**历史出处说明**，不是依赖。
///
/// 为什么要有它（10-17 决策）：
///   TeamProbe 已经证明**单人组队成立**（给 NPC 挂「结伴而行」→ 进副本 → 队友上场）。
///   但组队 mod 的 `DungeonTrigger MID&001` 里只写了一行
///   `addBattleNPCData_MID&003_0`（索引 **0**），因此**只会上场一个人**。
///   用户要做的是「**多人**组队」，所以必须先搞清楚：
///     ① `NpcCondition MID&003` 一次到底抓到几个 NPC？
///     ② `addBattleNPCData` 的索引 `0` 是否真的只取第一个？改成 `1`/`2` 会不会取到别人？
///     ③ `addBattleUnit_MID&003_...`（无索引）在抓取到多人时，会生成几个单位？
///     ④ 引擎的「战斗NPC容器」到底有几个槽位可用（官方文档说 1-5）？
///
///   **这四个问题静态分析（反编译只有签名、没有方法体）无法回答**，只能实测。
///
/// 设计要点（沿用 TeamProbe 已验证的经验，全部踩过坑）：
///   · 心跳循环，**没有一次性闸**（用户明确要求："探针要心跳式的，不能只跑一遍"）
///   · 自带帧节流（DiagSwitches.FrameTick 是**每帧**调用，不节流会写出 5MB 日志）
///   · 每个动作用**幂等**保证循环安全，而不是靠"只跑一次"
///   · 独立落盘 `logs/team_multi.log` + `logs/team_multi_summary.txt`（游戏一开就没网，必须离线可读）
///   · **绝不硬编码** mod 的气运 ID —— 全部按名字反查 / 从磁盘读 excelMID 推算
///
/// 开关：`&lt;Mod根&gt;\_diag_team_multi.txt`
///   action=scan        只读：枚举全图所有 NPC + 谁身上有「结伴而行」/「对话对象」
///   action=give        给 name 里列的**每个** NPC 挂「结伴而行」（幂等，逗号分隔）
///   action=clear       摘掉这些 NPC 身上的「结伴而行」
///   action=dumpbattle  ★核心★ 打印副本内战斗单位清单 + 战斗容器 + 相关 key
///   names=江宇文,姜萌,张三   目标名单（逗号/中文逗号分隔）
///   max=3             最多处理几个
///
/// 「进副本前」用 give 挂多人；进入副本后切到 dumpbattle，看**实际生成几个战斗单位**。
/// </summary>
#if AB_UI
using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text;
using UnityEngine;

namespace AgentLoopBridge
{
    internal static class TeamMultiProbe
    {
        // ───────────────────────── 开关 ─────────────────────────

        private static string ProbePath
        {
            get { return Path.Combine(ModPaths.DiagDir, "_diag_team_multi.txt"); }
        }

        internal static bool Present { get { return true; } }

        internal static bool SwitchExists
        {
            get { try { return File.Exists(ProbePath); } catch { return false; } }
        }

        internal static string SwitchPath { get { return ProbePath; } }

        /// <summary>
        /// Init 期自检（不依赖帧回调、不依赖是否进世界）。
        ///
        /// 为什么必须放在 Init：TeamProbe 上线时吃过"空有 DLL、日志一条不出"的亏
        /// （部署晚于游戏启动 / 没进世界 ⇒ FrameTick 根本不跑）。
        /// 有这一行，以后"新探针到底进没进游戏"不必再靠猜。
        /// </summary>
        internal static void SelfCheck()
        {
            try
            {
                bool has = SwitchExists;
                string msg = "[TeamMulti] 自检 present=1 switch=" + (has ? "有" : "无")
                           + " path=" + ProbePath
                           + (has ? "（开关存在，进世界后按 action 执行）" : "（无开关，探针待命）");
                try { ModMain.P(msg); } catch { }

                // ★ 10-19 用户要求：日志可以"每会话覆盖"，但**每局必须先归档**。★
                //   旧口径只有覆盖 ⇒ 一局跑完就没了，无法回看"那一局到底挂了几个人、
                //   用了什么开关、配置是什么"。现在覆盖之前先把上一局的
                //   日志原文 / 配置快照 / 结论 / Player.log 整体搬进 runs/<时间>/。
                string archived = ArchivePreviousRun();
                try { if (!string.IsNullOrEmpty(archived)) ModMain.P("[TeamMulti] [归档] " + archived); } catch { }

                try
                {
                    string f = LogFile;
                    if (!string.IsNullOrEmpty(f))
                    {
                        // ★ 10-19 采纳用户要求：日志**每会话覆盖**，绝不跨天累计。★
                        //   此前的教训：315KB 的 team_multi.log 里混着 10-17 前夜到当天中午多个会话，
                        //   排查时差点把10-17 的旧行（旧 dungeonType 14 类那种）当成本轮证据读。
                        //   所以：会话开头整文件重写并盖时间戳；会话内继续 append。
                        //   （运行时表验证的快照文件本来就是覆盖写，两者口径现一致。）
                        string head = "===== 新会话 " + DateTime.Now.ToString("MM-dd HH:mm:ss")
                                    + "（本局日志从此处覆盖重写，旧内容已归档到 runs/）=====" + Environment.NewLine;
                        File.WriteAllText(f, head, new UTF8Encoding(false));
                        File.AppendAllText(f,
                            DateTime.Now.ToString("HH:mm:ss.fff") + "  " + msg + Environment.NewLine,
                            new UTF8Encoding(false));
                        if (!string.IsNullOrEmpty(archived))
                            File.AppendAllText(f,
                                DateTime.Now.ToString("HH:mm:ss.fff") + "  [归档] " + archived + Environment.NewLine,
                                new UTF8Encoding(false));
                    }
                }
                catch { }
            }
            catch { }
        }

        // ───────────────── 每局运行归档（10-19 用户要求）─────────────────
        //
        // 「日志现在是覆盖的，我需要备份，每次都要备份，和组队测试那样留存记录下来，
        //   记录每次的运行的结构和配置，日志结果，日志原文。」
        //
        // 口径：
        //   · 活动文件仍然**每会话覆盖**（防止跨局旧行被当成本局证据 —— 10-19 前的教训）；
        //   · 覆盖之前，先把上一局的四个文件整体搬进 `logs/runs/<会话开始时间>/`：
        //       team_multi.log        日志原文（[TeamMulti] 全部输出）
        //       config_snapshot.txt   运行时配置快照（气运 / 条件组 / 触发器真值）
        //       summary.txt           日志结果（结论摘要）
        //       Player.log            引擎原文（Unity 上一局轮转出来的 Player-prev.log）
        //       run.txt               本次运行的开关 / 队伍结构 / 时间边界
        //   · 一局一档、不覆盖、不清理；时间戳取**上一局会话头**，不是归档时刻。
        //
        // 为什么在 SelfCheck：这是唯一"游戏一启动必执行、且早于覆盖日志"的时机。
        // 即使上一局是崩溃退出的，下次启动照样能把已落盘的内容归档。

        private static string RunsDir
        {
            get { string d = LogDir; return string.IsNullOrEmpty(d) ? null : Path.Combine(d, "runs"); }
        }

        /// <summary>把上一局的运行产物搬进 runs/&lt;时间&gt;/；返回一行可读回执（失败/无内容返回空）。</summary>
        private static string ArchivePreviousRun()
        {
            try
            {
                string log = LogFile, snap = SnapshotFile, sum = SummaryFile;
                bool hasLog = FileExistsSafe(log), hasSnap = FileExistsSafe(snap), hasSum = FileExistsSafe(sum);
                if (!hasLog && !hasSnap && !hasSum) return "";

                DateTime start = hasLog ? ReadSessionStart(log) : DateTime.MinValue;
                DateTime end = LatestWrite(hasLog ? log : null, hasSnap ? snap : null, hasSum ? sum : null);
                if (start == DateTime.MinValue) start = end;
                if (end == DateTime.MinValue) end = start;

                string root = RunsDir;
                if (string.IsNullOrEmpty(root)) return "";
                Directory.CreateDirectory(root);

                string stamp = start.ToString("yyyy-MM-dd_HHmmss");
                string runDir = Path.Combine(root, stamp);
                int dup = 2;
                while (Directory.Exists(runDir))
                    runDir = Path.Combine(root, stamp + "_" + dup++);
                Directory.CreateDirectory(runDir);

                // 元信息必须在搬运**之前**读（搬完路径就变了）
                string swBoot = hasLog ? LastLineContaining(log, "开关生效：") : "";
                string teamLine = hasLog ? LastLineContaining(log, "[态] 周围") : "";
                if (string.IsNullOrEmpty(teamLine) && hasLog)
                    teamLine = LastLineContaining(log, "两人组队就绪");
                if (string.IsNullOrEmpty(teamLine) && hasLog)
                    teamLine = LastLineContaining(log, "[态] 按号位挂载");
                if (string.IsNullOrEmpty(teamLine) && hasSum)
                    teamLine = LastLineContaining(sum, "队伍结构 =");
                if (string.IsNullOrEmpty(teamLine) && hasSum)
                    teamLine = LastLineContaining(sum, "已占号位 =");
                string swNow = "";
                try { if (SwitchExists) swNow = string.Join(" ", File.ReadAllLines(ProbePath, Encoding.UTF8)).Trim(); }
                catch { }

                MoveFileSafe(log, Path.Combine(runDir, "team_multi.log"));
                MoveFileSafe(snap, Path.Combine(runDir, "config_snapshot.txt"));
                MoveFileSafe(sum, Path.Combine(runDir, "summary.txt"));

                // 引擎原文：Unity 每次启动把上一局 Player.log 轮转成 Player-prev.log。
                string prev = PlayerPrevLogPath();
                if (!string.IsNullOrEmpty(prev) && FileExistsSafe(prev))
                {
                    try { File.Copy(prev, Path.Combine(runDir, "Player.log"), true); } catch { }
                }

                var meta = new StringBuilder();
                meta.AppendLine("=== 八荒智能体 · 组队探针 运行归档 ===");
                meta.AppendLine("会话开始: " + (start == DateTime.MinValue ? "?" : start.ToString("yyyy-MM-dd HH:mm:ss")));
                meta.AppendLine("最后写入: " + (end == DateTime.MinValue ? "?" : end.ToString("yyyy-MM-dd HH:mm:ss")));
                meta.AppendLine("归档时刻: " + DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss"));
                meta.AppendLine();
                meta.AppendLine("开关（上一局启动/最后变更）:");
                meta.AppendLine("  " + (string.IsNullOrEmpty(swBoot) ? "(日志里没有『开关生效』行)" : swBoot));
                meta.AppendLine("开关（归档时刻文件内容，属于新一局）:");
                meta.AppendLine("  " + (string.IsNullOrEmpty(swNow) ? "(无开关文件)" : swNow.Replace("\r", "").Replace("\n", " ")));
                meta.AppendLine();
                meta.AppendLine("队伍结构 / 本局最后一次状态行:");
                meta.AppendLine("  " + (string.IsNullOrEmpty(teamLine) ? "(无状态行)" : teamLine));
                meta.AppendLine();
                meta.AppendLine("文件清单:");
                meta.AppendLine("  team_multi.log       日志原文（[TeamMulti] 全部输出，含清运/挂载/引擎答复/战斗诊断）");
                meta.AppendLine("  config_snapshot.txt  配置快照（运行时气运表 / 条件组 / 触发器真实值）");
                meta.AppendLine("  summary.txt          日志结果（探针最后写入的结论摘要）");
                meta.AppendLine("  Player.log           引擎原文（上一局 Player-prev.log，含 [TeamInject]/[TeamFollowup]/[KillStats]/P()）");
                meta.AppendLine();
                meta.AppendLine("注：由下一次启动的 TeamMultiProbe.SelfCheck 自动生成；一局一档，不覆盖。");
                File.WriteAllText(Path.Combine(runDir, "run.txt"), meta.ToString(), new UTF8Encoding(false));

                return "上一局已留存 → " + runDir
                     + "（team_multi.log / config_snapshot.txt / summary.txt / Player.log / run.txt）";
            }
            catch (Exception e)
            {
                return "归档失败（不影响本局）：" + e.Message;
            }
        }

        private static bool FileExistsSafe(string p)
        {
            try { return !string.IsNullOrEmpty(p) && File.Exists(p); } catch { return false; }
        }

        private static void MoveFileSafe(string from, string to)
        {
            if (!FileExistsSafe(from)) return;
            try
            {
                if (File.Exists(to)) File.Delete(to);
                File.Move(from, to);
            }
            catch
            {
                // 移动失败（被占用/跨卷）⇒ 退回复制，至少内容不丢。
                try { File.Copy(from, to, true); } catch { }
            }
        }

        private static DateTime LatestWrite(params string[] paths)
        {
            DateTime max = DateTime.MinValue;
            foreach (string p in paths)
            {
                if (!FileExistsSafe(p)) continue;
                try { var t = File.GetLastWriteTime(p); if (t > max) max = t; } catch { }
            }
            return max;
        }

        /// <summary>从日志头 "===== 新会话 MM-dd HH:mm:ss" 解析上一局的开始时间。</summary>
        private static DateTime ReadSessionStart(string path)
        {
            try
            {
                int n = 0;
                foreach (string raw in File.ReadLines(path, Encoding.UTF8))
                {
                    string line = raw ?? "";
                    int at = line.IndexOf("新会话 ", StringComparison.Ordinal);
                    if (at >= 0)
                    {
                        string s = line.Substring(at + 4).Trim();
                        int cut = s.IndexOf('（');
                        if (cut < 0) cut = s.IndexOf('(');
                        if (cut > 0) s = s.Substring(0, cut).Trim();
                        DateTime t;
                        if (DateTime.TryParseExact(s, "MM-dd HH:mm:ss", CultureInfo.InvariantCulture,
                                                   DateTimeStyles.None, out t))
                            return new DateTime(DateTime.Now.Year, t.Month, t.Day,
                                                t.Hour, t.Minute, t.Second);
                    }
                    if (++n >= 5) break;
                }
            }
            catch { }
            return DateTime.MinValue;
        }

        /// <summary>取文件里**最后一条**包含 token 的行（日志过大时明说跳过，不冒充）。</summary>
        private static string LastLineContaining(string path, string token)
        {
            try
            {
                var fi = new FileInfo(path);
                if (!fi.Exists) return "";
                const long MaxBytes = 4 * 1024 * 1024;
                if (fi.Length > MaxBytes)
                    return "(日志 " + (fi.Length / 1024) + "KB 超过提取上限，未提取；原文已完整归档)";
                string last = "";
                foreach (string raw in File.ReadLines(path, Encoding.UTF8))
                {
                    if (raw != null && raw.IndexOf(token, StringComparison.Ordinal) >= 0)
                        last = raw.Trim();
                }
                return last;
            }
            catch { return ""; }
        }

        /// <summary>上一局的 Player.log（Unity 启动时轮转为 Player-prev.log）。</summary>
        private static string PlayerPrevLogPath()
        {
            try
            {
                string d = Application.persistentDataPath;
                if (!string.IsNullOrEmpty(d)) return Path.Combine(d, "Player-prev.log");
            }
            catch { }
            try
            {
                string low = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
                if (!string.IsNullOrEmpty(low))
                    return Path.Combine(low, "Low", "guigugame", "guigubahuang", "Player-prev.log");
            }
            catch { }
            return null;
        }

        /// <summary>
        /// 日志目录：**直接复用 `ModPaths.LogDir`**（TeamProbe 已验证可用的入口）。
        ///
        /// 为什么不再自己拼：10-17 第一趟实测，自己拼的 `GetDirectoryName(DiagDir)+"/logs"`
        /// 判定条件写错（`Directory.Exists(Path.GetDirectoryName(p))` 恒真），
        /// 结果日志一条都没落盘，只有 MelonLoader 那份。已验证的东西就别再发明一遍。
        /// </summary>
        private static string LogDir { get { try { return ModPaths.LogDir; } catch { return null; } } }

        // ───────────────────── 表验证（不需要进副本！）─────────────────────
        //
        // 存在理由：用户问"是不是没读到配置文件"。这**根本不用进副本就能回答** ——
        // 运行时 `g.conf` 里的表已是解密/展开后的真身，按真 ID 取一次即可。
        // 这一步能把"表没加载 / ID 算错"与"触发器没执行"彻底分开，
        // 避免把两者混成一句"组队没生效"。
        private static bool _verified;

        private static void VerifyTablesOnce()
        {
            if (_verified) return;
            try
            {
                if (g.conf == null) return;
            }
            catch { return; }
            _verified = true;

            Snap("========== 运行时表验证（真 ID = Mod + 偏移）==========");
            Snap("Mod = " + ModIds.Mod);

            VerifyLuck("结伴而行", ModIds.LuckTeam);
            VerifyLuck("内外重伤", ModIds.LuckHurt);
            VerifyLuck("组队中", ModIds.LuckSelf);

            // ★ 10-19 补验：2/3 号位的两枚气运**在运行时配置里存在吗**？★
            //   （只加 MID&55/56 时最可疑的"导出丢行"，导出 json 是加密的、
            //    离线只能靠 md5 —— 运行时 GetItem 是唯一能回答的地方。）
            for (int slot = 1; slot <= 2; slot++)
            {
                int id = TeamTool.SlotLuckId(slot, out string serr);
                if (id == 0 || serr != null)
                { Snap("  [号位" + (slot + 1) + "] ❌ SlotLuckId 取不到：" + serr); continue; }
                try
                {
                    bool has = g.conf.roleCreateFeature.GetItem(id) != null;
                    Snap("  [号位" + (slot + 1) + "] luckId=" + id + " ⇒ "
                        + (has ? "✅ 运行时表里有" : "❌ 运行时表里没有（导出丢行?）"));
                }
                catch (Exception ee)
                { Snap("  [号位" + (slot + 1) + "] GetItem 异常: " + ee.Message); }
            }

            VerifyNpcCondition("队友组", ModIds.Mod + 3);
            ProbeGetUnits();   // ★ 直接问引擎「这个条件组现在能抓到几个人」

            VerifyTrigger("拉队友", ModIds.Mod + 1);
            VerifyTrigger("挂重伤", ModIds.Mod + 2);
            VerifyTrigger("结束清理", ModIds.Mod + 3);
            Snap("====================================================");
            FlushSnapshot();
        }

        private static void VerifyLuck(string tag, int id)
        {
            try
            {
                var it = g.conf.roleCreateFeature.GetItem(id);
                Snap("  [气运] " + tag + " id=" + id + " ⇒ " + (it == null ? "❌ 取不到（表没加载 / ID 算错）" : "✅ 有"));
            }
            catch (Exception e) { Snap("  [气运] " + tag + " id=" + id + " 取值异常: " + e.Message); }
        }

        /// <summary>通用取值：属性优先、字段兜底（IL2CPP 两种都可能）。</summary>
        private static string GetMemberString(object o, string name)
        {
            if (o == null) return "?";
            object v = Refl.Get(o, name);
            return v == null ? "null" : v.ToString();
        }

        private static void VerifyNpcCondition(string tag, int id)
        {
            try
            {
                var it = g.conf.npcCondition.GetItem(id);
                if (it == null) { Snap("  [条件组] " + tag + " id=" + id + " ⇒ ❌ 取不到"); return; }
                int segs = 0;
                var desc = new List<string>();
                for (int k = 1; k <= 5; k++)
                {
                    string s = GetMemberString(it, "condition" + k);
                    if (!string.IsNullOrEmpty(s) && s != "0" && s != "null" && s != "?")
                    { segs++; desc.Add("c" + k + "=" + s); }
                }
                string sv = GetMemberString(it, "save");
                string gp = GetMemberString(it, "group");
                Snap("  [条件组] " + tag + " id=" + id + " ⇒ ✅ 有；段数=" + segs + " save=" + sv + " group=" + gp);
                Snap("      " + string.Join(" ", desc.ToArray()));
                if (segs < 3)
                    Snap("      ⚠ 段数只有 " + segs + " ⇒ 最多只能抓到 " + segs + " 个人（一段一个人）");
                // 10-18 晚：这条提示**已被实测推翻**，改成中性说明，避免下次排查被它带偏。
                //   save=0 + 3 段实测能出 3 个不同队友（world.battle.data.npcUnits Count=3）。
                //   去重逻辑根本不存在（GetUnitsInID 尾部只有 RemoveAll(u => u == null)）。
                Snap("      （save=" + sv + "：只决定是否存进存档复用，不影响一次取几个人；"
                     + "clone 的真因是组 ID 不含 '&' 时走 GetUnit(id) 只取单个）");
            }
            catch (Exception e) { Snap("  [条件组] " + tag + " id=" + id + " 异常: " + e.Message); }
        }

        /// <summary>
        /// 直接问引擎：这个条件组**现在**能抓到几个人、分别是谁。
        ///
        /// 存在理由（10-18）：`&N` 的语义已经靠反汇编确证为 `GetUnits(组)[N]`，
        /// 但"3 段相同条件到底能不能抓到 3 个**不同**的人"这件事，
        /// 静态读表读不出来（要看引擎的去重/排序行为）。这里直接调引擎，
        /// 让它亲口回答 —— 比任何推断都可靠。
        ///
        /// 纯只读：只调 GetUnits(不传 save=false 之外的参数)，不改任何状态。
        /// </summary>
        private static void ProbeGetUnits()
        {
            int id = ModIds.Mod + 3;
            string key = id.ToString();
            try
            {
                var cond = g.conf.npcCondition;
                if (cond == null) { Snap("  [GetUnits] ❌ g.conf.npcCondition 为空"); return; }

                // 先把所有 GetUnits 重载签名打印出来，避免再猜错
                var cands = cond.GetType().GetMethods()
                    .Where(m => m.Name == "GetUnits").ToList();
                Snap("  [GetUnits] 反射到 " + cands.Count + " 个重载：");
                foreach (var m in cands)
                {
                    var ps0 = m.GetParameters();
                    Snap("      " + m.Name + "(" + string.Join(", ",
                        ps0.Select(x => x.ParameterType.Name).ToArray()) + ")");
                }

                // ★ 只找 (String, NpcConditionData) 2 参重载（10-19 与 DoGetUnits 对齐）★
                //   之前这里按 3 参找（`GetUnits(string,bool,bool)`），必然找不到，
                //   印了"反射调用没拿到列表"却又把那条路当"0 人"人证 —— 伪 0 的来源。
                System.Collections.IList list = null;
                string how = "";
                var mi2 = cands.FirstOrDefault(m =>
                {
                    var ps0 = m.GetParameters();
                    return ps0.Length == 2 && ps0[0].ParameterType == typeof(string);
                });
                if (mi2 != null)
                {
                    how = "GetUnits(string,NpcConditionData)";
                    for (int attempt = 1; attempt <= 3 && list == null; attempt++)
                    {
                        try
                        {
                            object rr = mi2.Invoke(cond, new object[] { key, null });
                            list = rr as System.Collections.IList;
                        }
                        catch (Exception e)
                        {
                            Snap("  [GetUnits] string 版第 " + attempt + "/3 次没调到（" + e.GetType().Name + "）—— 不是引擎答复，重跑中…");
                            System.Threading.Thread.Sleep(200);
                        }
                    }
                }
                else
                {
                    Snap("  [GetUnits] ⚠ 没有找到 GetUnits(String, NpcConditionData) 2 参重载");
                }

                if (list == null)
                {
                    Snap("  [GetUnits] ✖ 重跑 3 次仍未调到引擎 ⇒ 探针侧问题，**此拍不判断引擎能选几人**");
                    return;
                }

                int? cntTry = TryCount(list);
                if (cntTry == null)
                {
                    // 10-19 语义对齐：Count 读不出 ⇒ 不下"0 人"结论，如实说读不到。
                    Snap("  [GetUnits] 引擎返回了列表但 Count 读不出 ⇒ 本次不下人数结论");
                    return;
                }
                int n = cntTry.Value;
                Snap("  [GetUnits] ✅ 引擎返回 Count=" + n + "   （入口 " + how + "，key=" + key + "）");

                int shown = 0;
                var elems = SafeElements(list);   // Il2Cpp 枚举器不可靠 → 用 get_Item 逐个取
                foreach (var o in elems)
                {
                    if (shown >= 5) { Snap("      …（只列前 5 个）"); break; }
                    shown++;
                    string nm = "?", uid = "?";
                    try
                    {
                        dynamic u = o;
                        nm = u.data.unitData.name.ToString();
                        uid = u.data.unitData.unitID.ToString();
                    }
                    catch { }
                    Snap("      第" + (shown - 1) + "个: " + nm + " (unitID=" + uid + ")");
                }

                if (n < 3)
                    Snap("      ⚠ 只拿到 " + n + " 人 ⇒ 段数不足或条件太窄；&1/&2 必然越界");
            }
            catch (Exception e) { Snap("  [GetUnits] 外层异常: " + e.GetType().Name + " " + e.Message); }
        }

        private static void VerifyTrigger(string tag, int id)
        {
            try
            {
                var it = g.conf.dungeonTrigger.GetItem(id);
                if (it == null) { Snap("  [触发器] " + tag + " id=" + id + " ⇒ ❌ 取不到"); return; }
                Snap("  [触发器] " + tag + " id=" + id + " ⇒ ✅ 有");
                Snap("      triggerType=" + GetMemberString(it, "triggerType")
                    + " triggerValue=" + GetMemberString(it, "triggerValue")
                    + " dungeonType=" + GetMemberString(it, "dungeonType")
                    + " dungeonID=" + GetMemberString(it, "dungeonID"));
                Snap("      setNPCA=" + GetMemberString(it, "setNPCA")
                    + " setNPCB=" + GetMemberString(it, "setNPCB")
                    + " probability=" + GetMemberString(it, "probability")
                    + " weight=" + GetMemberString(it, "weight"));
                Snap("      battleFunction=" + GetMemberString(it, "battleFunction"));
            }
            catch (Exception e) { Snap("  [触发器] " + tag + " id=" + id + " 异常: " + e.Message); }
        }

        private static string LogFile
        {
            get { string d = LogDir; return string.IsNullOrEmpty(d) ? null : Path.Combine(d, "team_multi.log"); }
        }

        /// <summary>
        /// 实验快照文件：每次进世界**覆盖写一次**当前生效的完整组队配置。
        ///
        /// 存在理由（10-18 用户明确要求）：之前每改一次配置，跑完就没人记得"那一趟到底跑的什么"，
        /// 于是出现过"用户记得成功加进过 1 个人、我却无法确认当时用了什么结构"的情况，
        /// 只能重试 —— 已经烧过的时间又烧一遍。
        /// 现在每次启动都把配置写成一份**可追溯的快照**，配合
        /// `docs/archive/2026-10-18-组队实验记录表.md`（历史实验流水）
        /// 就能做到"一次实验 = 一行记录 + 一份配置存档"。
        /// </summary>
        private static string SnapshotFile
        {
            get { string d = LogDir; return string.IsNullOrEmpty(d) ? null : Path.Combine(d, "team_config_snapshot.txt"); }
        }

        private static readonly StringBuilder _snap = new StringBuilder();

        /// <summary>快照专用输出：同时进日志与快照文件。</summary>
        private static void Snap(string line)
        {
            Out(line);
            _snap.AppendLine(line);
        }

        private static void FlushSnapshot()
        {
            try
            {
                string f = SnapshotFile;
                if (string.IsNullOrEmpty(f)) return;
                var sb = new StringBuilder();
                sb.AppendLine("=== 组队配置快照（每次进世界覆盖写）===");
                sb.AppendLine("时间: " + DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss"));
                sb.AppendLine("Mod = " + ModIds.Mod);
                sb.AppendLine();
                sb.Append(_snap.ToString());
                File.WriteAllText(f, sb.ToString(), new UTF8Encoding(false));
            }
            catch { }
        }

        private static string SummaryFile
        {
            get { string d = LogDir; return string.IsNullOrEmpty(d) ? null : Path.Combine(d, "team_multi_summary.txt"); }
        }

        // ───────────────────────── 输出 ─────────────────────────

        /// <summary>
        /// 全部输出都走这里：一份进 MelonLoader 日志（开发时方便），
        /// 一份进自己的文件（**离线可读**，这是硬需求）。
        ///
        /// ## 同内容去重（10-21 收尾加）
        ///
        /// `pair` 模式每 2 秒跑一拍，而 `[pair] 候选池 1 人（radius=8）：郑博艺(57,28)…`
        /// 这种行在玩家不动时**每拍都一模一样**。真机日志里同一行连续重复 30+ 次
        /// （`[TeamMulti]` 共 259 行，其中 132 行是这类重复）。
        ///
        /// 规则：与上一条**完全相同**就不写，只在第 20/40/60… 次补一条"仍在重复"，
        /// 既能证明它没死，又不刷屏。
        /// </summary>
        private static void Out(string msg)
        {
            if (!string.IsNullOrEmpty(msg))
            {
                if (msg == _lastOutMsg)
                {
                    _lastOutRepeat++;
                    if (_lastOutRepeat % 20 != 0) return;      // 只每 20 次补一条
                    msg = "（上一条已重复 " + _lastOutRepeat + " 次）" + msg;
                }
                else
                {
                    _lastOutMsg = msg;
                    _lastOutRepeat = 0;
                }
            }

            try { ModMain.P("[TeamMulti] " + msg); } catch { }
            try
            {
                string f = LogFile;
                if (!string.IsNullOrEmpty(f))
                    File.AppendAllText(f,
                        DateTime.Now.ToString("HH:mm:ss.fff") + "  " + msg + Environment.NewLine,
                        new UTF8Encoding(false));
            }
            catch { }
        }

        /// <summary>上一条输出内容 + 连续重复次数（供 <see cref="Out"/> 去重用）。</summary>
        private static string _lastOutMsg = null;
        private static int _lastOutRepeat = 0;

        /// <summary>写结论文件（**覆盖**，只保留最新一份，方便关掉游戏后直接读）。</summary>
        private static void Summary(string msg)
        {
            try
            {
                string f = SummaryFile;
                if (!string.IsNullOrEmpty(f))
                    File.WriteAllText(f, msg + Environment.NewLine, new UTF8Encoding(false));
            }
            catch { }
        }

        /// <summary>追加到结论文件。</summary>
        private static void SummaryAppend(string msg)
        {
            try
            {
                string f = SummaryFile;
                if (!string.IsNullOrEmpty(f))
                    File.AppendAllText(f, msg + Environment.NewLine, new UTF8Encoding(false));
            }
            catch { }
        }

        // ───────────────────────── 状态 ─────────────────────────

        private static int _timer;
        private const int RecheckFrames = 120;   // ≈2s @60fps（活跃期：刚开始扫）
        /// <summary>空闲期节拍 ≈10s。挂满了/没东西可做时用这个，别一直按 2 秒跑 ——
        /// 每次 Run() 都要碰 IL2CPP 单位对象，太密会明显拖慢游戏（10-18 用户反馈"很卡"）。</summary>
        private const int IdleFrames = 600;
        /// <summary>连续多少拍没有新挂载 ⇒ 转空闲节拍。</summary>
        private const int IdleAfterBeats = 3;
        private static int _quietBeats;
        /// <summary>本次战斗已过多少拍（auto 模式用；进战斗清零，用于"只 dump 首拍 + 每5拍"）。</summary>
        private static int _battleBeats;

        private static bool _active;
        private static string _action = "scan";
        /// <summary>give/clear 已经执行过（心跳每 ~2s 一拍，不清这个标志会原地反复清空再挂）。</summary>
        private static bool _giveDone;
        private static string _namesRaw = "";

        private static int _max = 3;
        private static int _radius = 1;      // givemap 的搜索半径（格）
        /// <summary>开关文件里 `lowhp=1` 的**记录位**（10-21：功能已删，仅保留显示，
        /// 防止老配置文件里残留该键时报"未知键"）。</summary>
        private static bool _lowHp;
        /// <summary>挂载的气运模式。`slot`（默认）= 第 i 人挂第 i 号位气运（51/55/56）；
        /// `all` = 每人都挂 1 号位那枚（51）—— 10-19 A/B 用：复刻"成功时"的
        /// 「三段全指同一枚 luck」结构，把"三枚不同 luck"这一个变量单独隔离出来。
        /// </summary>
        private static string _luckMatch = "slot";
        private static long _stamp;

        private static string _lastBeat;          // 心跳去重
        private static int _beats;                // 已执行拍数（日志里报给用户看）
        private static string _lastPhase;
        /// <summary>气运反查已尝试次数 / 上限（世界加载后配置合并有延迟，必须允许重试）。</summary>
        private static int _scanTries;
        /// <summary>探测时造出来的气运 id:name（诊断用，最多 80 条）。</summary>
        private static readonly List<string> _probeNames = new List<string>();
        private const int MaxScanTries = 6;
        /// <summary>下一次允许重试的拍号（避免每拍都扫，3010 次 GetItem 不便宜）。</summary>
        private static int _nextScanBeat;
        private static bool _summaryInit;

        // ── 10-19：「不行就重跑」──挂载完成后引擎答复没到手就自动复查 ──────────
        // 挂载成功（_giveDone=true）后，若引擎还没给出**可信**的人数答复，就每 5 拍（~10s）
        // 再问一次，至多 6 次。用户 10-19 明确要求"同一日志位置，如果不行那就重跑"。
        private static int _unitAskTries;        // 已复查次数（含挂载完成时的第 1 问）
        // ★ 用**墙钟**排程而不是拍号：挂载后 _giveDone 闸门让每拍静默放行，_beats 不再递增，
        //   拍号永远碰不到“下一拍” —— 10-19 踩过后改成时间驱动（挂载完成 10s 后第一轮复查）。
        private static DateTime _nextUnitAskAt = DateTime.UtcNow;
        private const int MaxUnitAsks = 6;
        /// <summary>最近一次引擎答复：-1=无答复 / 其他=真实 Count。</summary>
        private static int _lastUnitCount = -1;
        /// <summary>最近一次答复是否**可信**（Count 真读到了，而不是 Explorer 读失败/反射没调到）。</summary>
        private static bool _lastCountKnown;
        /// <summary>是否已经答出 ≥1 人（答出后就停止复查）。</summary>
        private static bool _unitCountOk;

        /// <summary>DiagSwitches.FrameTick 每帧调用 —— 本方法自带 ~2s 节流。</summary>
        internal static void Tick()
        {
            // ★ 节流必须在最前面：FrameTick 是逐帧调用，不节流会写爆日志 ★
            if (--_timer > 0) return;
            // 空闲期降频：连续若干拍没新挂载 ⇒ 10s 一拍（10-18 卡顿治理）
            _timer = (_quietBeats >= IdleAfterBeats) ? IdleFrames : RecheckFrames;

            bool exists = false;
            try { exists = File.Exists(ProbePath); } catch { }
            if (!exists)
            {
                if (_active) { _active = false; Out("开关已移除，多人探针关闭"); }
                return;
            }

            long st = 0L;
            try { st = File.GetLastWriteTimeUtc(ProbePath).Ticks; } catch { }
            if (st != _stamp) { _stamp = st; Parse(); }
            if (!_active) return;

            try { Run(); }
            catch (Exception e) { Out("执行异常：" + e); }
        }

        private static void Parse()
        {
            string action = "scan";
            string names = "";
            int max = 3;
            int radius = 1;
            string luckMode = "slot";
            bool lowHp = false;
            bool noInject = false;
            try
            {
                foreach (string raw in ReadLines(ProbePath))
                {
                    string line = (raw ?? "").Trim();
                    if (line.Length == 0 || line[0] == '#') continue;
                    int eq = line.IndexOf('=');
                    if (eq <= 0) continue;
                    string k = line.Substring(0, eq).Trim().ToLowerInvariant();
                    string v = line.Substring(eq + 1).Trim();
                    if (k == "action") { if (v.Length > 0) action = v.ToLowerInvariant(); }
                    else if (k == "names" || k == "name") { if (v.Length > 0) names = v; }
                    else if (k == "max") { int t; if (int.TryParse(v, out t) && t >= 1 && t <= 5) max = t; }
                    else if (k == "radius") { int t; if (int.TryParse(v, out t) && t >= 0 && t <= 10) radius = t; }
                    else if (k == "luck") { if (v.Length > 0) luckMode = v.ToLowerInvariant(); }
                    else if (k == "lowhp")
                    {
                        string lv = v.ToLowerInvariant();
                        lowHp = (lv == "1" || lv == "true" || lv == "on" || lv == "yes");
                    }
                    else if (k == "noinject")
                    {
                        string nv = v.ToLowerInvariant();
                        noInject = (nv == "1" || nv == "true" || nv == "on" || nv == "yes");
                    }
                }
            }
            catch { }
            _action = action;
            _namesRaw = names;
            _max = max;
            _radius = radius;
            _lowHp = lowHp;      // 仅记录，不再有对应行为（见字段注释）
            TeamContainerInject.NoInject = noInject;
            _luckMatch = (luckMode == "all") ? "all" : "slot";
            if (luckMode != "slot" && luckMode != "all")
                Out("⚠ 未知的 luck 模式 '" + luckMode + "'（只认 slot/all）→ 已回落 slot");
            _active = true;
            _beats = 0;
            _giveDone = false;      // 开关变了 ⇒ 允许再挂一次（心跳模式下不原地反复清/挂）
            // 10-19：一并重置"引擎答人数"复查链 —— 用户要求"不行就重跑"，改开关即重新从第 1 问开始。
            _unitAskTries = 0;
            _nextUnitAskAt = DateTime.UtcNow;
            _lastUnitCount = -1;
            _lastCountKnown = false;
            _unitCountOk = false;
            Out("开关生效：action=" + _action + " names=[" + _namesRaw + "] max=" + _max + " radius=" + _radius
                  + " luck=" + _luckMatch + " lowhp=" + (_lowHp ? 1 : 0)
                  + " noinject=" + (TeamContainerInject.NoInject ? 1 : 0)
                      + "（心跳模式：每 ~2s 复查一次，不会只跑一遍）");
            if (!_summaryInit)
            {
                _summaryInit = true;
                Summary("=== TeamMultiProbe 多人组队探针 ===");
                SummaryAppend("开关: action=" + _action + " names=[" + _namesRaw + "] max=" + _max);
                SummaryAppend("开始时间: " + DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss"));
                SummaryAppend("");
            }
        }

        private static IEnumerable<string> ReadLines(string path)
        {
            string[] lines = null;
            try { lines = File.ReadAllLines(path, Encoding.UTF8); } catch { }
            if (lines == null) { try { lines = File.ReadAllLines(path, Encoding.Default); } catch { } }
            return lines ?? new string[0];
        }

        /// <summary>解析 names= 列表（支持中英文逗号、分号、空格）。</summary>
        private static List<string> TargetNames()
        {
            var list = new List<string>();
            if (string.IsNullOrEmpty(_namesRaw)) return list;
            string s = _namesRaw.Replace('，', ',').Replace('、', ',').Replace(';', ',').Replace('；', ',');
            foreach (string part in s.Split(','))
            {
                string t = (part ?? "").Trim();
                if (t.Length > 0 && !list.Contains(t)) list.Add(t);
                if (list.Count >= _max) break;
            }
            return list;
        }

        /// <summary>
        /// 按名解析单个 NPC，**重名时如实报告**。
        ///
        /// 为什么不能用 `UnitLookup.Resolve` 了事：全图上千个单位、名字用字池有限，
        /// **重名是常态不是例外**（`UnitLookup` 文件头记着真机事故：按名解析拿到了另一个同名者，
        /// 而且完全静默）。探针要验证"挂了几个人、上来几个人"，一旦挂到错的人身上，
        /// 结论就整体失真且不可察觉。
        /// 所以这里走 `ResolveEx` 拿候选列表，重名就**明确打警告**并把坐标带上，
        /// 让人能自己判断是不是要找的那一个。
        /// </summary>
        private static WorldUnitBase ResolveTarget(string nm, out string note)
        {
            note = "";
            WorldUnitBase u = null;
            try
            {
                System.Collections.Generic.List<UnitLookup.Candidate> cands = null;
                u = UnitLookup.ResolveEx(nm, null, out cands);
                if (cands != null && cands.Count > 1)
                {
                    var sb = new System.Text.StringBuilder();
                    sb.Append("⚠「").Append(nm).Append("」全图有 ").Append(cands.Count).Append(" 个同名：");
                    for (int i = 0; i < cands.Count && i < 6; i++)
                    {
                        var c = cands[i];
                        if (i > 0) sb.Append(" | ");
                        sb.Append("#").Append(i).Append("(").Append(c.x).Append(",").Append(c.y).Append(")");
                    }
                    sb.Append("  ← 取的是第 0 个");
                    note = sb.ToString();
                }
            }
            catch (Exception e) { note = "解析「" + nm + "」异常：" + e.Message; }
            return u;
        }

        // ───────────────────────── 主流程 ─────────────────────────

        private static void Run()
        {
            WorldUnitBase player = null;
            try { player = g.world.playerUnit; } catch { }
            if (player == null)
            {
                Beat("no-world", "[态] 世界未加载");
                return;
            }

            // ★ 世界一加载就先把**运行时表**验一遍（不需要进副本）。
            //   这一步回答"配置到底读进来没有"，与"触发器有没有执行"是两件事。
            VerifyTablesOnce();

            // 气运反查：**失败要允许重试**。
            // ★ 10-17 真机教训（两次踩同一个坑）★
            //   反查落地时刻恰好是世界刚加载那一拍，而 `g.conf.fateFeature` 那时**还没把
            //   mod 条目合并进来** ⇒ `GetItem` 全部返回 null ⇒ `scanned=0` ⇒ 反查必然失败。
            //   旧探针 20:37 那次同样 `气运表共 0 条`，只是日志被淹没了没人注意。
            //   所以：① 失败后每 10 拍（≈20s）重试一次，累计最多 6 次；
            //        ② 一旦拿到就永久停止；③ 全部重试失败才放弃。
            if (_luckTeam == 0 && _scanTries < MaxScanTries && _beats >= _nextScanBeat)
            {
                _scanTries++;
                _nextScanBeat = _beats + 10;
                ScanLuckTable();
                if (_luckTeam != 0)
                    Out("✅ 气运反查成功（第 " + _scanTries + " 次尝试）");
                else if (_scanTries >= MaxScanTries)
                    Out("❌ 气运反查 " + MaxScanTries + " 次均失败，本次不再重试");
            }

            // ★ 10-19「不行就重跑」：挂载完成后，引擎若还没给出 **可信** 的 ≥1 答复，
            //   每 5 拍（~10s）自动再问一次 GetUnits，至多 MaxUnitAsks 次。
            //   与挂载完成当拍那次（_unitAskTries=1）共用一套计数。
            //   战斗中不抢拍（auto 分支在战斗内的 dumpbattle 是另一条证据链）。
            if (_giveDone && !_unitCountOk && _unitAskTries < MaxUnitAsks
                && DateTime.UtcNow >= _nextUnitAskAt)
            {
                bool inBattleNow = false;
                try { var wb = g.world.battle; inBattleNow = (wb != null && wb.isBattle); } catch { }
                if (!inBattleNow)
                {
                    _unitAskTries++;
                    _nextUnitAskAt = DateTime.UtcNow.AddSeconds(10);
                    Out("[getunits] 自动复查第 " + _unitAskTries + "/" + MaxUnitAsks + " 次（上次"
                        + (_lastCountKnown ? "答 " + _lastUnitCount + " 人" : "没拿到引擎答复") + "）");
                    DoGetUnits();
                    if (_unitCountOk)
                        Out("[getunits] ✋ 复查已答出 ≥1 人，停止后续自动复查");
                    else if (_unitAskTries >= MaxUnitAsks)
                        Out("[getunits] ⛔ 自动复查用完 " + MaxUnitAsks + " 次仍未答出 ≥1 人 —— 停止重跑，等改开关");
                }
            }

            switch (_action)
            {
                case "scan": DoScan(player); break;
                case "give": DoGive(player); break;
                case "givemap": DoGiveNear(player); break;
                case "clear": DoClear(player); break;
                case "dumpbattle": DoDumpBattle(player); break;
                case "getunits": DoGetUnits(); break;
                case "auto":
                    {
                        // ★ 一个开关管两件事（10-18 加）★
                        //   起因：反复出现"进副本时开关还停在 givemap" ⇒ 副本内零记录，
                        //   只能靠肉眼数人，排查效率极低。
                        //   现在：不在战斗就挂气运（同 givemap），一进战斗自动转诊断（同 dumpbattle）。
                        //   —— 一趟跑完，不用中途改文件。
                        //
                        // ★ 但战斗内**不能每拍都 dump**（10-18 卡顿教训）★
                        //   dump 内部要遍历单位（`GetUnits(true)` ~100ms/1556 个单位），
                        //   每 2 秒跑一次就是持续卡顿。所以：
                        //     · 进战斗的**第一拍**必 dump（最关键的首帧现场）
                        //     · 之后每 5 拍补一次（看有没有变化，成本可接受）
                        bool inBattle = false;
                        try { var wb = g.world.battle; inBattle = (wb != null && wb.isBattle); } catch { }

                        if (inBattle)
                        {
                            _battleBeats++;
                            // ★ 10-21 起默认**不自动 dump**（用户：先不管）★
                            //   实测一次 dump ≈59 行 / 300~360ms（遍历 GetUnits(true)），
                            //   每 5 拍一次 = 持续掉帧。改成只在 `action=dumpbattle` 手动触发。
                            //   要恢复自动：把下面那行注释换成原条件即可。
                            // ★ 不要带拍数（10-21）★
                            //   `Beat` 的去重是**整行文本比较**，而旧文案里含递增的拍数
                            //   ⇒ 每拍都"不一样"⇒ 去重永不命中 ⇒ 一场战斗刷 128 行。
                            //   拍数对读者没有价值（要看"还在跑"看心跳就够）。
                            Beat("battle-wait", "[态] 战斗中（自动 dump 已关，"
                                + "需现场快照请改 action=dumpbattle）");
                        }
                        else
                        {
                            if (_battleBeats > 0)
                            {
                                Out("[战斗结束] 本场共经历 " + _battleBeats + " 拍");
                                _battleBeats = 0;
                            }
                            DoGiveNear(player);
                        }
                        break;
                    }
                default: Out("未知 action=" + _action + "（可选 scan/give/givemap/clear/dumpbattle/getunits/auto）"); break;
            }
        }

        /// <summary>心跳去重：内容不变就不重复写（避免 ~2s 一条刷爆）。</summary>
        private static void Beat(string phase, string line)
        {
            _beats++;
            if (_lastPhase == phase && _lastBeat == line)
            {
                // 内容没变也每 30 拍（≈1 分钟）报一次存活，证明它还在跑
                if (_beats % 30 == 0) Out("[心跳] 第 " + _beats + " 拍，状态未变：" + line);
                return;
            }
            _lastPhase = phase;
            _lastBeat = line;
            Out("[第" + _beats + "拍] " + line);
        }

        // ───────────────────────── 气运 ID 反查（绝不硬编码）─────────────────────────

        private static int _luckTeam;       // 「结伴而行」
        private static int _luckTalk;       // 「对话对象」

        /// <summary>
        /// 按**名字**反查气运 ID（「结伴而行」/「对话对象」）。
        ///
        /// ★ 实现直接沿用 `TeamProbe.FindLuckIdByName` —— 那是**真机跑通过**的代码 ★
        ///
        /// 为什么不再自己写一遍（10-17 教训）：
        ///   我的第一版从 `conf._allConfList` 枚举 + `GetField("id")` 取 id，结果 1372 条 0 命中。
        ///   两个坑都是旧探针文件头**明确警告过**的：
        ///     ① 「不靠"枚举容器"（Il2Cpp 集合不实现 IList<object>，实测恒 null）」
        ///     ② `id` 不在 `ConfRoleCreateFeatureItem` 上，而在基类 `ConfBaseItem` 上，
        ///        `GetField` 默认不搜基类 ⇒ 恒返回 null ⇒ id=0 ⇒ 全被 continue 掉。
        ///   已验证的东西不该重新发明。这里搬迁并保留原实现的注释与取舍。
        /// </summary>
        private static void ScanLuckTable()
        {
            try
            {
                // ── 第 0 步：**已知偏移优先**（10-18 加，别调到后面去）──
                //    工坊里那个组队 mod 的气运也叫「结伴而行」，按名反查命中谁取决于
                //    扫描顺序；真机日志里命中的是工坊那一枚（不是我们的）。探针要测的是
                //    **我们的**配置（`NpcCondition MID&003` 只认我们的 id），
                //    所以先用本 mod 的偏移定位。
                TryKnownLuckIds();

                // ── 主路径：遍历气运表（`roleCreateFeature._allConfList`，1372 行）──
                if (_luckTeam == 0) ScanLuckByList();

                // ── 次路径：按 id 区间 GetItem（主路径失败时才跑）──
                if (_luckTeam == 0)
                {
                    _luckTeam = FindLuckIdByName("结伴而行");
                    _luckTalk = FindLuckIdByName("对话对象");
                    Out("按名反查结果：结伴而行=" + _luckTeam + "  对话对象=" + _luckTalk);
                }

                // ── 兜底：按名反查失败时，用「这个 ID 是不是一枚真实气运」来定位 ──
                // 为什么需要：`g.conf.fateFeature.GetItem` 在世界刚加载那一拍会返回 null
                // （mod 条目还没合并完），而 `WorldFactory.CreateLuckInID` 走的是**气运工厂**，
                // 只要这枚气运真的注册进了游戏就能造出来。用它当探针更直接。
                if (_luckTeam == 0) ProbeLuckByIds();

                Out("最终结果：结伴而行=" + _luckTeam + "  对话对象=" + _luckTalk);
                SummaryAppend("气运反查: 结伴而行=" + (_luckTeam == 0 ? "❌未找到" : _luckTeam.ToString())
                            + "  对话对象=" + (_luckTalk == 0 ? "❌未找到" : _luckTalk.ToString()));
            }
            catch (Exception e) { Out("ScanLuckTable 异常：" + e); }
        }

        /// <summary>
        /// 用**本 mod 的已知偏移**（`ModIds.LuckTeam` / `LuckTalk`）取气运 ID。
        ///
        /// 判据 = `GetItem(id)` 非空（这枚气运真的注册进了游戏）。
        /// 这条优先于任何"按名字找"：同名气运在本机是既有事实
        /// （工坊里那个组队 mod 也叫「结伴而行」，只是 excelMID 不同），按名找必然有歧义。
        /// </summary>
        private static void TryKnownLuckIds()
        {
            try
            {
                if (_luckTeam == 0 && g.conf.roleCreateFeature.GetItem(ModIds.LuckTeam) != null)
                {
                    _luckTeam = ModIds.LuckTeam;
                    Out("✅ 用已知偏移命中「结伴而行」= " + _luckTeam + "（Mod+" + (_luckTeam - ModIds.Mod) + "）");
                }
            }
            catch (Exception e) { Out("已知偏移取「结伴而行」失败：" + e.Message); }
            try
            {
                if (_luckTalk == 0 && g.conf.roleCreateFeature.GetItem(ModIds.LuckTalk) != null)
                {
                    _luckTalk = ModIds.LuckTalk;
                    Out("✅ 用已知偏移命中「对话对象」= " + _luckTalk + "（Mod+" + (_luckTalk - ModIds.Mod) + "）");
                }
            }
            catch (Exception e) { Out("已知偏移取「对话对象」失败：" + e.Message); }
        }

        /// <summary>
        /// 遍历**气运表**（`g.conf.roleCreateFeature._allConfList`）按名找 ID。
        ///
        /// 这是主路径，理由：
        ///   ① 表已确认正确（`ConfRoleCreateFeature`，1372 行，第一趟实测读到过）；
        ///   ② 一次遍历 1372 行，比 `GetItem` 探测 3010 次便宜得多；
        ///   ③ 不依赖 `GetItem` 的内部 id 匹配行为。
        /// 两个坑（都踩过，写在这里防复发）：
        ///   · `id` 在基类 `ConfBaseItem` 上，`GetField` 必须走 `BaseType` 链；
        ///   · `name` 是 LocalText key，且 mod 条目的 key 里带**未展开的 `MID&amp;NNN`**，
        ///     必须过 `ResolveName`（先原样 LS，再按 excelMID 偏移展开后 LS）。
        /// </summary>
        private static void ScanLuckByList()
        {
            try
            {
                var conf = g.conf.roleCreateFeature;
                if (conf == null) { Out("❌ roleCreateFeature 为空"); return; }
                var all = conf._allConfList;
                if (all == null) { Out("❌ _allConfList 为空"); return; }
                int n = all.Count;
                Out("气运表(roleCreateFeature) 共 " + n + " 行，开始按名扫描");

                var samples = new List<string>();
                var teamHits = new List<string>();
                for (int i = 0; i < n; i++)
                {
                    object item = null;
                    try { item = all[i]; } catch { continue; }
                    if (item == null) continue;

                    int id = 0;
                    string rawKey = "";
                    try
                    {
                        // id 在基类 ConfBaseItem 上 → 沿 BaseType 链找
                        for (Type bt = item.GetType(); bt != null && id == 0; bt = bt.BaseType)
                        {
                            var fi = bt.GetField("id", System.Reflection.BindingFlags.Public |
                                                       System.Reflection.BindingFlags.NonPublic |
                                                       System.Reflection.BindingFlags.Instance);
                            if (fi != null) { try { id = (int)fi.GetValue(item); } catch { } }
                        }
                    }
                    catch { }
                    try
                    {
                        for (Type bt = item.GetType(); bt != null && rawKey.Length == 0; bt = bt.BaseType)
                        {
                            var fn = bt.GetField("name", System.Reflection.BindingFlags.Public |
                                                         System.Reflection.BindingFlags.NonPublic |
                                                         System.Reflection.BindingFlags.Instance);
                            if (fn != null) { try { rawKey = (string)fn.GetValue(item) ?? ""; } catch { } }
                        }
                    }
                    catch { }
                    if (id == 0) continue;

                    string nm = ResolveName(rawKey);
                    if (samples.Count < 5) samples.Add("  id=" + id + "  raw=「" + rawKey + "」→「" + nm + "」");

                    bool isTeam = nm.Contains("结伴") || nm.Contains("同行");
                    bool isTalk = nm.Contains("对话对象");
                    if (isTeam || isTalk)
                    {
                        teamHits.Add("  id=" + id + "  「" + nm + "」  raw=「" + rawKey + "」");
                        if (isTeam) _luckTeam = id;
                        if (isTalk) _luckTalk = id;
                    }
                }

                Out("前5行样本（验证 id/name 取法与翻译通路）:");
                foreach (string sm in samples) Out(sm);
                if (teamHits.Count > 0)
                {
                    Out("=== 气运表里命中的组队条目 ===");
                    foreach (string h in teamHits) Out(h);
                }
                else Out("气运表里没有「结伴而行」/「对话对象」（组队 mod 未加载？）");
                Out("表扫描结果：结伴而行=" + _luckTeam + "  对话对象=" + _luckTalk);
            }
            catch (Exception e) { Out("ScanLuckByList 异常：" + e.Message); }
        }

        /// <summary>
        /// 备选定位：在候选 excelMID 段里，用 `CreateLuckInID` 探哪些 ID 是真实气运。
        ///
        /// 不硬编码任何人的私有 ID —— 候选段全部来自磁盘 `ModData.cache` 的 `excelMID`。
        /// 找到之后**用气运自己的 name 字段（过 GameTool.LS）核对名字**，确认是「结伴而行」。
        /// 这条路绕开 `g.conf.fateFeature.GetItem` 的时序问题。
        /// </summary>
        private static void ProbeLuckByIds()
        {
            try
            {
                var mids = InstalledModExcelMids();
                int made = 0;
                foreach (int mid in mids)
                {
                    for (int off = 0; off <= 300; off++)
                    {
                        int id = mid + off;
                        WorldUnitLuckBase lk = null;
                        try { lk = WorldFactory.CreateLuckInID(id); } catch { }
                        if (lk == null) continue;
                        made++;

                        string nm = "";
                        // 名字来源（按可靠性排序，逐个试）：
                        //   ① `lk.luckConf`（WorldUnitLuckBase 上的配置引用）
                        //   ② `roleCreateFeature.GetItem(id)`（气运表按 id 取）
                        // 反射实证：`luckData` 只有 id/duration/createTime/objData，**没有 name**。
                        try
                        {
                            var lc = lk.luckConf;
                            if (lc != null) nm = ResolveName(lc.name);
                        }
                        catch { }
                        if (string.IsNullOrEmpty(nm))
                        {
                            try
                            {
                                var ci = g.conf.roleCreateFeature.GetItem(lk.luckData.id);
                                if (ci != null) nm = ResolveName(ci.name);
                            }
                            catch { }
                        }
                        // ★ 不再因名字为空就跳过记录 ★
                        //   上一版这里 `if (empty) continue;` 导致 made=63 但清单为空，
                        //   日志只剩一句"一枚气运都没造出来"，无法定位。诊断件必须能自证。
                        if (_probeNames.Count < 80) _probeNames.Add(id + ":" + (nm.Length == 0 ? "<名字读不到>" : nm));
                        if (string.IsNullOrEmpty(nm)) continue;

                        // 造出来了就销毁，避免污染（这一步只读探测，不该留痕）
                        try { lk.Destroy(); } catch { }

                        if (nm.Contains("结伴") || nm.Contains("同行")) { _luckTeam = id; Out("  ✔ 探测命中「结伴而行」= " + id); }
                        if (nm.Contains("对话对象")) { _luckTalk = id; Out("  ✔ 探测命中「对话对象」= " + id); }
                    }
                }
                Out("按 ID 探测完成：共造出 " + made + " 枚气运；结伴而行=" + _luckTeam + " 对话对象=" + _luckTalk);
                if (_probeNames.Count > 0)
                {
                    Out("--- 造出来的气运清单（前 " + _probeNames.Count + " 个）---");
                    foreach (string pn in _probeNames) Out("    " + pn);
                    SummaryAppend("气运清单（共 " + _probeNames.Count + "）:");
                    foreach (string pn in _probeNames) SummaryAppend("  " + pn);
                }
                else Out("⚠ 一枚气运都没造出来（CreateLuckInID 全返回 null）");
            }
            catch (Exception e) { Out("ProbeLuckByIds 异常：" + e.Message); }
        }

        /// <summary>本地化 key → 中文（拿不到就原样返回）。</summary>
        private static string Loc(string key)
        {
            if (string.IsNullOrEmpty(key)) return "";
            try
            {
                string s = GameTool.LS(key);
                if (!string.IsNullOrEmpty(s) && s != key) return s;
            }
            catch { }
            return key;
        }

        /// <summary>
        /// 把 key 里的 `MID&amp;NNN` 展开成 `excelMID + NNN`（官方文档的导出规则）。
        ///
        /// 为什么需要：mod 的 xlsx 里 `name` 字段写的是 `role_feature_postnatal_nameMID&amp;001`
        /// —— 那是**编辑期写法**。官方文档："模组在导出数据时，会根据模组编辑器随机的 int 值，
        /// 加上用户自定义的 MID&amp;XXXX 作为偏移，得到一个真正的 ID"。
        /// 所以游戏里真实 key 应该是 `role_feature_postnatal_name` + 展开后的整数，
        /// 直接拿带 `MID&amp;` 的原样去 `GameTool.LS` 必然查不到（这就是 63 枚气运一个都没
        /// 匹配上的原因）。展开结果由 `InstalledModExcelMids()` 从磁盘读出，不写死任何值。
        ///
        /// 候选 excelMID 全部来自磁盘（`ModData.cache`），不硬编码任何私有值。
        /// </summary>
        private static List<string> ExpandMidKey(string key)
        {
            var outs = new List<string>();
            if (string.IsNullOrEmpty(key)) return outs;
            int at = key.IndexOf("MID&", StringComparison.Ordinal);
            if (at < 0) return outs;
            int i = at + 4;
            int start = i;
            while (i < key.Length && key[i] >= '0' && key[i] <= '9') i++;
            if (i == start) return outs;
            int off;
            if (!int.TryParse(key.Substring(start, i - start), out off)) return outs;

            string head = key.Substring(0, at);
            string tail = key.Substring(i);
            foreach (int mid in InstalledModExcelMids())
                outs.Add(head + (mid + off).ToString(CultureInfo.InvariantCulture) + tail);
            return outs;
        }

        /// <summary>名字解析：先原样 LS，再逐个尝试 MID&amp; 展开后的 key。</summary>
        private static string ResolveName(string rawKey)
        {
            if (string.IsNullOrEmpty(rawKey)) return "";
            string direct = Loc(rawKey);
            if (direct != rawKey && !string.IsNullOrEmpty(direct)) return direct;

            foreach (string cand in ExpandMidKey(rawKey))
            {
                string v = Loc(cand);
                if (!string.IsNullOrEmpty(v) && v != cand) return v;
            }
            return rawKey;
        }

        // ─────────── 以下两段搬迁自 TeamProbe（真机验证过的实现，勿随意重写）───────────

private static int FindLuckIdByName(string wantName)
        {
            if (string.IsNullOrEmpty(wantName)) return 0;
            try
            {
                // ★ 10-17 决定性修正：原先（含旧探针）用的是 `g.conf.fateFeature`（ConfFateFeature），
                //   那是**另一张表**；气运表是 `g.conf.roleCreateFeature`（ConfRoleCreateFeature）。
                //   用错表的后果：`GetItem(modId)` 全部返回 null ⇒ `scanned=0` ⇒ 永远查不到，
                //   而日志只说"未命中"，看不出是"查错表了"。反射实证：
                //     ConfMgr.roleCreateFeature : ConfRoleCreateFeature   ← 气运（1372 行）
                //     ConfMgr.fateFeature       : ConfFateFeature         ← 另一张表
                var conf = (object)g.conf.roleCreateFeature;
                if (conf == null) { Out("g.conf.roleCreateFeature 为空"); return 0; }

                // ★ 不靠"枚举容器"（Il2Cpp 集合不实现 IList<object>，实测恒 null）★
                // 改为：**从磁盘读出已安装 mod 的 excelMID**，再在 [excelMID, excelMID+Limit]
                // 区间里用已知可用的 GetItem(int) 逐个取条目、比对名字。
                // 这样既绕开枚举限制，又**不把任何 mod 的 MID 硬编码进代码**（值来自它自己的
                // ModData.cache），作者重导出换 ID 也自动跟随。
                int scanned = 0;
                foreach (var mid in InstalledModExcelMids())
                {
                    for (int off = 0; off <= 300; off++)
                    {
                        int id = mid + off;
                        object it = null;
                        try { it = ((dynamic)conf).GetItem(id); } catch { }
                        if (it == null) continue;
                        scanned++;
                        string nm = null;
                        try { nm = (string)((dynamic)it).name; } catch { }
                        if (!string.IsNullOrEmpty(nm)) { try { nm = GameTool.LS(nm); } catch { } }
                        if (nm == wantName) { Out("按名反查命中：" + wantName + " = " + id); return id; }
                    }
                }
                // 兜底：原版小 ID 区段（万一气运是原版就有的，不属于任何 mod）
                for (int id = 1; id <= 1500; id++)
                {
                    object it = null;
                    try { it = ((dynamic)conf).GetItem(id); } catch { }
                    if (it == null) continue;
                    scanned++;
                    string nm = null;
                    try { nm = (string)((dynamic)it).name; } catch { }
                    if (!string.IsNullOrEmpty(nm)) { try { nm = GameTool.LS(nm); } catch { } }
                    if (nm == wantName) { Out("按名反查命中（原版段）：" + wantName + " = " + id); return id; }
                }
                Out("按名反查「" + wantName + "」未命中（共查 " + scanned + " 条）");
            }
            catch (Exception e) { Out("FindLuckIdByName 异常：" + e.Message); }
            return 0;
        }

private static List<int> InstalledModExcelMids()
        {
            var mids = new List<int>();
            var seen = new HashSet<int>();
            Action<string> take = (path) =>
            {
                try
                {
                    if (!File.Exists(path)) return;
                    string txt = File.ReadAllText(path, Encoding.UTF8);
                    // 不引入正则/JSON 库：手工定位 "excelMID" 后取紧跟的整数。
                    // （转义容易在生成代码时被吃掉，纯字符串查找最稳。）
                    int at = txt.IndexOf("excelMID", StringComparison.Ordinal);
                    if (at < 0) return;
                    int i = at + "excelMID".Length;
                    while (i < txt.Length && (txt[i] == '"' || txt[i] == ' ' || txt[i] == ':')) i++;
                    bool neg = false;
                    if (i < txt.Length && (txt[i] == '-' )) { neg = true; i++; }
                    int start = i;
                    while (i < txt.Length && txt[i] >= '0' && txt[i] <= '9') i++;
                    if (i == start) return;
                    int v;
                    if (!int.TryParse(txt.Substring(start, i - start), out v)) return;
                    if (neg) v = -v;
                    if (v == 0) return;
                    if (seen.Add(v)) mids.Add(v);
                }
                catch { }
            };

            try
            {
                // ① 本模组根下的工程（若作者机上有）
                string root = ModPaths.ModRoot;
                if (!string.IsNullOrEmpty(root))
                {
                    take(Path.Combine(root, "ModData.cache"));
                    take(Path.Combine(root, "ModProject", "ModData.cache"));
                }
            }
            catch { }

            try
            {
                // ② 工坊目录：从游戏根反推
                string gameRoot = null;
                string root = ModPaths.ModRoot;
                // ModRoot 形如 .../common/鬼谷八荒/ModExportData/Mod_Jgmg5L
                if (!string.IsNullOrEmpty(root))
                {
                    var d = new DirectoryInfo(root);
                    for (int i = 0; i < 4 && d != null; i++)
                    {
                        var ws = Path.Combine(d.FullName, "..", "..", "workshop", "content", "1468810");
                        try { if (Directory.Exists(ws)) { gameRoot = Path.GetFullPath(ws); break; } } catch { }
                        d = d.Parent;
                    }
                }
                if (!string.IsNullOrEmpty(gameRoot))
                {
                    foreach (string modDir in Directory.GetDirectories(gameRoot))
                    {
                        take(Path.Combine(modDir, "ModProject", "ModData.cache"));
                        // debug/Mod_XXX 形态（组队 mod 就是这种）
                        try
                        {
                            foreach (string dbg in Directory.GetDirectories(modDir, "Mod_*", SearchOption.AllDirectories))
                                take(Path.Combine(dbg, "ModData.cache"));
                        }
                        catch { }
                    }
                    Out("[反查] 工坊目录 = " + gameRoot + "，读到 " + mids.Count + " 个 excelMID");
                }
            }
            catch (Exception e) { Out("[反查] 扫工坊异常：" + e.Message); }

            // ③ 本模组自己的 MID（ModIds 是唯一事实来源，不是"别人的私有值"）
            if (seen.Add(ModIds.Mod)) mids.Add(ModIds.Mod);

            try
            {
                var sb = new System.Text.StringBuilder();
                foreach (var v in mids) { if (sb.Length > 0) sb.Append(", "); sb.Append(v); }
                Out("[反查] 待探测的 excelMID 列表: [" + sb + "]");
            }
            catch { }
            return mids;
        }

        // ───────────────────────── action=scan ─────────────────────────

        /// <summary>
        /// 只读：枚举全图所有 NPC，标出谁身上有「结伴而行」/「对话对象」。
        ///
        /// 这一步回答的问题：**我到底能给几个 NPC 挂上气运**，
        /// 以及 `NpcCondition MID&003` 未来会抓到谁。
        /// </summary>
        private static void DoScan(WorldUnitBase player)
        {
            try
            {
                Il2CppSystem.Collections.Generic.List<WorldUnitBase> units = null;
                try { units = g.world.unit.GetUnits(true); } catch { }
                if (units == null) { Out("❌ WorldMgr.unit.GetUnits 返回 null"); return; }

                int total = units.Count;
                var withTeam = new List<string>();
                var withTalk = new List<string>();
                var sb = new StringBuilder();

                for (int i = 0; i < total; i++)
                {
                    WorldUnitBase u = null;
                    try { u = units[i]; } catch { continue; }
                    if (u == null) continue;

                    string nm = SafeName(u);
                    if (_luckTeam != 0 && HasLuckAny(u, _luckTeam)) withTeam.Add(nm);
                    if (_luckTalk != 0 && SafeGetLuck(u, _luckTalk) != null) withTalk.Add(nm);
                }

                sb.Append("全图NPC=").Append(total);
                sb.Append("  有「结伴而行」=").Append(withTeam.Count);
                if (withTeam.Count > 0) sb.Append("[").Append(string.Join(",", withTeam.ToArray())).Append("]");
                sb.Append("  有「对话对象」=").Append(withTalk.Count);
                if (withTalk.Count > 0) sb.Append("[").Append(string.Join(",", withTalk.ToArray())).Append("]");

                Beat("scan", "[态] " + sb.ToString());

                // 结论文件：只更新"有组队标记的人"这一行，方便看
                Summary("=== 多人组队探针 状态 ===\n"
                      + "全图NPC总数 = " + total + "\n"
                      + "带「结伴而行」= " + withTeam.Count + " 个"
                      + (withTeam.Count > 0 ? "  → " + string.Join(", ", withTeam.ToArray()) : "") + "\n"
                      + "带「对话对象」= " + withTalk.Count + " 个"
                      + (withTalk.Count > 0 ? "  → " + string.Join(", ", withTalk.ToArray()) : "") + "\n");
            }
            catch (Exception e) { Out("scan 异常：" + e); }
        }

        // ───────────────────────── action=givemap ─────────────────────────

        /// <summary>
        /// 给**玩家周围 radius 格内**的所有 NPC 挂「结伴而行」，不用猜名字。
        ///
        /// 为什么加这个动作（10-17）：
        ///   `action=give` 要人手填 3 个真实 NPC 名，填错就等于这一趟白跑；
        ///   而这游戏**重名是常态**（`UnitLookup` 文件头记着按名解析拿到过另一个同名者的事故）。
        ///   更省事也更可靠的做法：**人站到几个 NPC 旁边**，探针自己把周围的人全挂上。
        ///   这样"挂了谁"是探针报出来的，不是我猜的。
        /// </summary>
        private static void DoGiveNear(WorldUnitBase player)
        {
            if (_giveDone) return;
            try
            {
                // ★ 一槽一枚气运（10-19）★
                //   以前这里给周围每个 NPC 都挂**同一枚**气运 → 条件组三段筛同一条件、
                //   不去重 + 随机排序 ⇒ 会抽到重复的人。现在第 i 个挂第 i 号位，
                //   每段候选集恰好 1 人。`max` 就是本次要凑的人数（不给满编也不会炸）。
                //
                //   为什么这个 action 比 `give` 更重要：`give` 要人手填名字，
                //   而**换个存档名字就不在了**（10-19 真机：'方成和' 在当时存档查不到）。
                //   `givemap` 只按坐标取最近的几个，任何存档都能用。
                if (SlotsReady() == 0) { Out("❌ 号位气运没就绪（RoleCreateFeature MID&51/55/56 导出没有？）"); return; }

                // ★ 只取玩家周围，不拉全图（10-18 卡顿治理）★
                //   `GetUnits(true)` 单次 ~100ms / 1556 个单位（实测数据见
                //   `game/RelationNetwork.cs` 的同格扫描区块）。这个动作每 2 秒跑一次，
                //   等于持续卡顿。而 givemap 本来就只需要"身边的 NPC"。
                //   改用引擎自带的按坐标查询（同 RelationNetwork 已验证的两个入口）。
                int px = 0, py = 0;
                try { px = player.data.unitData.pointX; py = player.data.unitData.pointY; } catch { }
                string pid = "";
                try { pid = player.data.unitData.unitID ?? ""; } catch { }

                int stripped = StripAllSlots();
                if (stripped > 0) Out("[givemap] 先清掉全部旧号位气运 " + stripped + " 枚");

                Il2CppSystem.Collections.Generic.List<WorldUnitBase> units = NearUnits(px, py, _radius);

                // ★ 先把自己身上误挂的气运摘掉（10-17 上一版 bug 的善后）★
                //   上一版 `u == player` 判引用相等没生效，把「结伴而行」挂到了玩家身上；
                //   若玩家当时存过档，这枚脏气运就会留在存档里，进而可能被 NpcCondition
                //   当成"队友候选"（`GetUnits(true)` 的结果里**确实包含玩家**，日志实证）。
                //   这里每拍自查一次，有就摘掉 —— 幂等，且不必让用户手动清。
                if (_luckTeam != 0 && HasLuckAny(player, _luckTeam))
                {
                    bool removed = DelLuck(player, _luckTeam);
                    Out("[自清] 玩家身上误挂了「结伴而行」→ " + (removed ? "已摘除" : "摘除失败"));
                    SummaryAppend("自清: 玩家身上误挂的「结伴而行」" + (removed ? "已摘除" : "摘除失败"));
                }

                var hit = new List<string>();
                var near = new List<string>();
                var skipped = new List<string>();
                int radius = _radius;
                int newly = 0;                      // 本次**新挂上**的个数
                for (int i = 0; i < units.Count; i++)
                {
                    WorldUnitBase u = null;
                    try { u = units[i]; } catch { continue; }
                    if (u == null) continue;

                    // ★ 必须排除玩家自己（10-17 实测踩到）★
                    //   上一版用 `u == player` 判引用相等，**没生效** —— 结果把「结伴而行」
                    //   挂到了玩家身上（日志：`已挂气运=1 [唐炎(15,2)]`，唐炎就是玩家）。
                    //   `UnitSnapshot` 文件头写明：解析同一个人的判据是 **unitID 相等**
                    //   （世界唯一、与包装器无关），引用相等不可靠。
                    string uid = "";
                    try { uid = u.data.unitData.unitID ?? ""; } catch { }
                    if (pid.Length > 0 && uid == pid) continue;
                    if (u == player) continue;      // 双保险


                    int ux = 0, uy = 0;
                    try { ux = u.data.unitData.pointX; uy = u.data.unitData.pointY; } catch { continue; }

                    int dx = ux - px, dy = uy - py;
                    if (Math.Abs(dx) > radius || Math.Abs(dy) > radius) continue;

                    string nm = SafeName(u);
                    near.Add(nm + "(" + ux + "," + uy + ")");

                    // 先清存量重复（旧版判据错时堆进去的），再判"已有"。
                    // 每拍都清一次是**幂等**的：干净了就 0 条，不会反复删。
                    int dup = DedupeLuck(u, _luckTeam);
                    if (dup > 0)
                    {
                        Out("[去重] " + nm + " 身上重复的「结伴而行」清掉 " + dup + " 个");
                        SummaryAppend("去重: " + nm + " 清掉 " + dup + " 个重复气运");
                    }

                    // ★ max 限制的是「新挂上的个数」，不是「扫到的个数」（10-17 实测踩到）★
                    //   上一版写 `if (near.Count > _max) continue;` —— near 是**扫描清单**，
                    //   扫到第 4 个人时 near.Count=4 就把人跳过了。结果
                    //   `[方成和,唐炎,张票,江宇文]` 里**第 4 个 江宇文 永远挂不上**，
                    //   而它恰恰是本次最想测的目标 NPC。
                    if (newly >= _max) { skipped.Add(nm + "(超出max)"); continue; }

                    // luck=slot（默认）：第 i 人挂第 i 号位气运（51/55/56）；
                    // luck=all（10-19 A/B）：每人挂 1 号位那枚 —— 三段同指一枚，复刻成功结构。
                    string slotErr;
                    int slotId = (_luckMatch == "all")
                        ? TeamTool.SlotLuckId(0, out slotErr)
                        : TeamTool.SlotLuckId(newly, out slotErr);
                    if (slotId == 0)
                    {
                        skipped.Add(nm + "(号位缺气运:" + slotErr + ")");
                        continue;
                    }

                    // ★ 10-19 根因修复：改用 TeamTool.AddLuck（与 team.invite 完全同源）★
                    //   探针旧版策略 #2 只写 addLuck（存档/面板层），同会话内 `feature_1_*`
                    //   条件读 allLuck（运行时层）恒看不见 ⇒ GetUnits=0 ⇒ 触发器整条不跑。
                    //   TeamTool.AddLuck 的 #1 策略是 u.CreateLuck —— 直接进运行时层。
                    //   ★ 10-19 追加：AddLuck 本身已改成「两层都进才算成功」（假成功 hoops）
                    //   ⚠ 10-19 下午：用户注意到"面板里带气运的是旧二人，而探针说挂的是另两人"
                    //     ⇒ 诊断口径再收紧：挂完立刻把**分层状态**打出来。
                    string addErr;
                    bool ok = TeamTool.AddLuck(u, slotId, out addErr) && HasLuckAny(u, slotId);
                    if (ok)
                    {
                        Out("[层] " + nm + " luckId=" + slotId + "：" + TeamTool.LuckLayerState(u, slotId)
                            + (addErr == null ? "" : "（残留报错: " + addErr + "）"));
                        hit.Add(nm + "(" + (newly + 1) + "号位@" + ux + "," + uy + ")");
                        newly++;
                    }
                    else skipped.Add(nm + "(挂载失败:" + addErr + ")");
                }

                // 有新增 ⇒ 保持活跃节拍；连续多拍无新增 ⇒ 转 10s 空闲节拍（省 IL2CPP 开销）
                if (newly > 0) _quietBeats = 0; else if (_quietBeats < 999) _quietBeats++;

                string line = "周围" + radius + "格内NPC=" + near.Count
                            + (near.Count > 0 ? " [" + string.Join(",", near.ToArray()) + "]" : "")
                            + "  本拍新挂=" + newly + "  已有=" + (hit.Count - newly)
                            + (hit.Count > 0 ? " [" + string.Join(",", hit.ToArray()) + "]" : "")
                            + (skipped.Count > 0 ? "  超上限跳过=[" + string.Join(",", skipped.ToArray()) + "]" : "");
                Beat("givemap", "[态] " + line);

                Summary("=== 多人组队探针 周围挂载（一槽一枚）===\n"
                      + "玩家位置 = (" + px + "," + py + ")  半径 = " + radius + " 格\n"
                      + "周围NPC = " + near.Count + " 个"
                      + (near.Count > 0 ? "\n  " + string.Join("\n  ", near.ToArray()) : "") + "\n"
                      + "已占号位 = " + newly + " / " + TeamTool.MaxMembers
                      + (hit.Count > 0 ? "\n  " + string.Join("\n  ", hit.ToArray()) : "") + "\n"
                      + (newly < TeamTool.MaxMembers
                           ? "\n★ 本次是「不满编」测试：空号位筛不出人，`addBattleNPCData_<空槽>` 会拿到 null ★\n"
                           : "\n★ 满编：三人应各不相同 ★\n")
                      + "\n★ 下一步：进一个普通副本，打完看 [TeamFollowup] 与 [①npcUnits] ★\n");
                if (newly > 0)
                {
                    _giveDone = true;      // 一次性闸：别每拍清空再挂

                    // ★ 挂完立刻验一次"引擎到底看不看得到"（10-19）★
                    //   进副本没人有两种完全不同的原因，这条一句就能分开：
                    //     · GetUnits 返回 0 ⇒ 号位气运没真正进 `allLuck`（只进了 `addLuck` 暂存）
                    //       ⇒ `feature_1_*` 看不到 ⇒ `npcConditionExamine` 失败 ⇒ **触发器整条不跑**
                    //     · GetUnits ≥1 ⇒ 条件组是好的 ⇒ 问题在触发器的副本类型/其它条件上
                    //   10-19 起：这一问算"自动复查链"的第 1 问 —— 没答出 ≥1 人就每 5 拍重跑（用户要求）。
                    Out("[givemap] 挂载完成 → 立刻问引擎一次条件组能选出几个人");
                    _unitAskTries = 1;
                    _nextUnitAskAt = DateTime.UtcNow.AddSeconds(10);
                    DoGetUnits();
                }
            }
            catch (Exception e) { Out("givemap 异常：" + e); }
        }

        // ───────────────────────── action=give ─────────────────────────

        /// <summary>
        /// 给 names= 里每个 NPC 挂「结伴而行」（**幂等**：已有就跳过）。
        ///
        /// 这是"多人组队"的前置条件 —— 挂上 3 个，进副本看上来几个。
        /// </summary>
        private static void DoGive(WorldUnitBase player)
        {
            if (_giveDone) return;
            try
            {
                List<string> names = TargetNames();
                if (names.Count == 0) { Out("❌ names= 为空，没指定目标"); return; }

                // ★ 一槽一枚气运（10-18 深夜定案）★
                //   以前这里只挂一枚「结伴而行」，`NpcCondition MID&003` 三段筛同一个条件
                //   ⇒ 条件组不去重 + sortType=0 随机 ⇒ 从池子里**有放回**抽三次，会出现
                //   「方成和、温芸、方成和」。改成第 i 个名字挂第 i 号位气运后，
                //   每段候选集恰好 1 人，结构上不可能重复。
                //
                //   只有 2 个名字 = 3 号位空着 —— 这正是要测的「缺槽会不会炸」。
                int slot0 = SlotsReady();
                if (slot0 == 0)
                {
                    Out("❌ 号位气运没就绪（RoleCreateFeature MID&51/55/56 有没有导出？）→ 无法挂载");
                    return;
                }

                // 先清干净：老版本往同一枚气运上堆过 5 个持有者，
                // 不清掉的话 1 号位还是"多人随机抽"，这一趟就白测了。
                int stripped = StripAllSlots();
                if (stripped > 0) Out("[give] 先清掉全部旧号位气运 " + stripped + " 枚");

                var okList = new List<string>();
                var failList = new List<string>();
                var slotLine = new List<string>();

                for (int i = 0; i < names.Count && i < TeamTool.MaxMembers; i++)
                {
                    string nm = names[i];
                    string note = "";
                    WorldUnitBase u = ResolveTarget(nm, out note);
                    if (!string.IsNullOrEmpty(note)) Out("[give] " + note);
                    if (u == null) { failList.Add(nm + "(找不到)"); continue; }

                    int luckId = TeamTool.SlotLuckId(i, out string se);
                    if (luckId == 0) { failList.Add(nm + "(号位" + (i + 1) + "气运缺失：" + se + ")"); continue; }

                    // 10-19：与 givemap 同理，统一走 TeamTool.AddLuck（两层都写）
                    string addErr2;
                    bool ok = HasLuckAny(u, luckId) || (TeamTool.AddLuck(u, luckId, out addErr2) && HasLuckAny(u, luckId));
                    if (ok && HasLuckAny(u, luckId))
                    {
                        okList.Add(nm);
                        slotLine.Add((i + 1) + "号位=" + nm + "(id=" + luckId + ")");
                    }
                    else failList.Add(nm + "(挂载失败)");
                }

                string line = "按号位挂载 " + okList.Count + "/" + names.Count
                            + (slotLine.Count > 0 ? " [" + string.Join(",", slotLine.ToArray()) + "]" : "")
                            + (failList.Count > 0 ? "  失败=[" + string.Join(",", failList.ToArray()) + "]" : "");
                Beat("give", "[态] " + line);

                Summary("=== 多人组队探针 挂载结果（一槽一枚）===\n"
                      + "目标 = " + string.Join(", ", names.ToArray()) + "\n"
                      + "已占号位 = " + okList.Count + " / " + TeamTool.MaxMembers
                      + (slotLine.Count > 0 ? "  → " + string.Join("; ", slotLine.ToArray()) : "") + "\n"
                      + (failList.Count > 0 ? "失败 = " + string.Join(", ", failList.ToArray()) + "\n" : "")
                      + (okList.Count < TeamTool.MaxMembers
                           ? "\n★ 本次是「不满编」测试：空着的号位筛不出人，`addBattleNPCData_<空槽>` 会拿到 null ★\n"
                           : "\n★ 满编：三人应各不相同 ★\n")
                      + "\n★ 下一步：把 action 改成 clear 之外的任意值都行，然后进一个副本 ★\n");
                _giveDone = true;
            }
            catch (Exception e) { Out("give 异常：" + e); }
        }

        /// <summary>1 号位气运 id（0 = 表里没有 ⇒ 号位机制没就绪）。</summary>
        private static int SlotsReady()
        {
            for (int i = 0; i < TeamTool.MaxMembers; i++)
            {
                if (TeamTool.SlotLuckId(i, out string e) == 0)
                {
                    Out("[give] " + (i + 1) + " 号位气运缺失：" + e);
                    return 0;
                }
            }
            return TeamTool.SlotLuckId(0, out _);
        }

        /// <summary>把全图所有人身上的号位气运都摘掉，返回摘掉的枚数。</summary>
        private static int StripAllSlots()
        {
            int n = 0;
            try
            {
                // ★ 单次全图扫描 + 逐人逐槽清到干净（10-19 21:xx 重写）★
                //   上一版为了"复核"打了两遍扫描、外加一遍再复核 = **3 次全图**
                //   （每次 ~100ms/1556 单位），而 `pair` 每拍都可能调它 ⇒ 明显卡顿。
                //
                //   真正需要"反复"的不是**全图**，而是**同一个人身上**：
                //   `PropertyData::DelAddLuck` @0x183CE9150 反汇编实证只删第一条匹配
                //   （cmp/je → List::RemoveAt → 直接 return），历史重复挂载会堆多条。
                //   ⇒ 对每个人、每一槽 while 循环删到他身上读不到为止即可，
                //     不需要为此再扫一遍全图。
                var all = g.world?.unit?.GetUnits(true);
                if (all == null) return 0;
                int cnt = 0;
                try { cnt = all.Count; } catch { return 0; }

                int leftovers = 0;
                var leftNames = new List<string>();
                for (int i = 0; i < cnt; i++)
                {
                    WorldUnitBase u = null;
                    try { u = all[i]; } catch { continue; }
                    if (u == null) continue;
                    bool hadAny = false;
                    for (int s = 0; s < TeamTool.MaxMembers; s++)
                    {
                        int id = TeamTool.SlotLuckId(s, out _);
                        if (id == 0 || !HasLuckAny(u, id)) continue;
                        hadAny = true;
                        try { if (DelLuck(u, id)) n++; } catch { }
                    }
                    if (!hadAny) continue;
                    // 清完就地复核这一个人（O(1)，不涉全图）。
                    // ★ 判据必须用 DelLuck 的返回值，不能用 HasLuckAny ★
                    //   `HasLuckAny` = GetLuck || InAddLuck，**两层都看不见**
                    //   `allLuck` 里 luckData=null 的僵尸对象（见 DelLuck 的 ★段★）。
                    //   而引擎 `UnitCondition::Feature` 是直接遍历 allLuck 的 ⇒
                    //   用 HasLuckAny 复核会漏报"已清空"，实际引擎仍看得见。
                    //   DelLuck 内部已按三层复核并返回真实结果，这里直接采信。
                    for (int s = 0; s < TeamTool.MaxMembers; s++)
                    {
                        int id = TeamTool.SlotLuckId(s, out _);
                        if (id == 0) continue;
                        if (!HasLuckAny(u, id)) continue;      // 两层都干净
                        // 两层仍有 ⇒ 再清一次并复核（DelLuck 内部会报残留原因）
                        bool ok = false;
                        try { ok = DelLuck(u, id); } catch { }
                        if (!ok)
                        {
                            leftovers++;
                            if (leftNames.Count < 8) leftNames.Add(SafeName(u) + "/" + (s + 1) + "号位");
                        }
                    }
                }
                // ★ 清运后必须让 TeamTool 的队员缓存失效（10-20）★
                //   本方法直接操作气运，**绕过了 TeamTool 的增删入口** ⇒ 缓存里
                //   `_memberIds[]` 仍指向已经清掉的人。若不清，下一步
                //   `CachedMembers()`/`Members()` 会读到"幽灵队员"：
                //   真机实证（10-19 21:29:35）——
                //     [清运] 已彻底清空 ✓
                //     [pair] 清运后复核（缓存）：⚠ 仍有 1 人 [1号:方成和]
                //   而那一刻方成和身上的气运确实已被摘掉。
                //   清掉标记后，下一次查询会重建一次（全图扫描仅此一次）。
                try { TeamTool.ResetMemberCache(); } catch { }

                Out("[清运] 清掉 " + n + " 枚；就地复核："
                    + (leftovers == 0 ? "已彻底清空 ✓"
                                      : "⚠ 仍有 " + leftovers + " 处残留 [" + string.Join(",", leftNames.ToArray()) + "]"));
            }
            catch (Exception e) { Out("[give] StripAllSlots 异常：" + e.Message); }
            return n;
        }

        // ───────────────────────── action=getunits ─────────────────────────

        /// <summary>
        /// 直接问引擎：`ConfNpcCondition.GetUnits(<条件组id>)` 现在能选出几个人。
        ///
        /// ★ 为什么这条最值钱（10-19）★
        ///   进副本没人有两种完全不同的原因，这条一句就能分开：
        ///     · **返回 0** ⇒ 条件组选不出人 ⇒ `DungeonTrigger` 的 `npcConditionExamine_*`
        ///       判定失败 ⇒ **整条触发器不跑**（连 battleFunction 都不会尝试）
        ///     · **返回 ≥1** ⇒ 条件组是好的 ⇒ 问题在触发器的**副本类型/其它条件**上
        ///
        /// ★ 签名坑（连踩三轮）★
        ///   `GetUnits` 有 3 个重载，首参分别是 `string[]` / `string` / `int`，
        ///   且都是 **2 参**（第二参 `NpcConditionData`）。之前一直按 `(string,bool,bool)` 3 参找，
        ///   必然找不到 ⇒ 探针每次都只打印"没找到重载"。
        /// </summary>
        private static void DoGetUnits()
        {
            try
            {
                var cond = g.conf?.npcCondition;
                if (cond == null) { Out("[getunits] g.conf.npcCondition 为空"); return; }

                System.Reflection.MethodInfo pick = null;
                foreach (var m in cond.GetType().GetMethods())
                {
                    if (m.Name != "GetUnits") continue;
                    var ps = m.GetParameters();
                    // ★ 10-19 续查二轮 Q8：battle 链 AddBattleNPCData 引擎自己调的是
                    //   GetUnits(int 组id, null)@0x1859937F0 —— 探针必须测**同一个重载**，
                    //   否则"0"测的是剧情触发器家族那条 String 重载（级联清零语义），
                    //   与战斗链无关。
                    if (ps.Length == 2 && ps[0].ParameterType == typeof(int)) { pick = m; break; }
                }
                if (pick == null)
                {
                    // 兜底：int 重载不在（IL2CPP 包装差异）→ 才退 String 版并注明污染源
                    foreach (var m in cond.GetType().GetMethods())
                    {
                        if (m.Name != "GetUnits") continue;
                        var ps = m.GetParameters();
                        if (ps.Length == 2 && ps[0].ParameterType == typeof(string)) { pick = m; break; }
                    }
                    if (pick != null)
                        Out("[getunits] ⚠ int 重载没找到，退到 String 版 —— 该版的 0 含剧情链级联语义，**不能**对等战斗链");
                }
                if (pick == null) { Out("[getunits] ⚠ GetUnits(int/String, NpcConditionData) 都没找到"); return; }

                bool usedInt = pick.GetParameters()[0].ParameterType == typeof(int);
                int condId = ModIds.Mod + 3;        // NpcCondition MID&003
                object r = null;
                string lastErr = "";
                for (int attempt = 1; attempt <= 5; attempt++)
                {
                    try
                    {
                        r = usedInt
                            ? pick.Invoke(cond, new object[] { condId, null })
                            : pick.Invoke(cond, new object[] { condId.ToString(), null });
                        lastErr = "";
                        break;
                    }
                    catch (Exception ex)
                    {
                        var inner = ex.InnerException;
                        lastErr = (inner == null ? ex.Message : inner.Message);
                        Out("[getunits] 反射调用第 " + attempt + "/5 次没调到（"
                            + ex.GetType().Name + " " + lastErr + "）—— 这不是引擎答复，重跑中…");
                        System.Threading.Thread.Sleep(200);
                    }
                }
                if (r == null || lastErr != "")
                {
                    _lastUnitCount = -1; _lastCountKnown = false;
                    Out("[getunits] ✖ 反射跑完 5 次仍未调到引擎 ⇒ 本拍**无法判断**能选出几人"
                        + "（绝对不等于「选出 0 人」）；下拍自动复查将继续重跑");
                    return;
                }

                // IL2CPP 的 List 不实现托管 ICollection ⇒ 走 Count 属性 + 索引器（禁用 foreach）
                int n = 0;
                bool countKnown = true;
                try { n = (int)((dynamic)r).Count; }
                catch (Exception cEx)
                {
                    countKnown = false;
                    Out("[getunits] 引擎返回了对象但 Count 读取失败（" + cEx.GetType().Name + " "
                        + cEx.Message + "）⇒ 与「选出 0 人」是**两回事**，本拍不下结论，下拍重跑");
                }
                _lastUnitCount = countKnown ? n : -1;
                _lastCountKnown = countKnown;
                if (countKnown && n >= 1) _unitCountOk = true;
                if (!countKnown) return;

                var got = new List<string>();
                for (int i = 0; i < n && i < 12; i++)
                {
                    WorldUnitBase u = null;
                    try { u = (WorldUnitBase)((dynamic)r)[i]; } catch { continue; }
                    got.Add(u == null ? "<null>" : SafeName(u));
                }

                string verdict = n == 0
                    ? "⇒ 引擎真答 0（直调走 condData=null）★若触发器也走同参即 100% 不触发★"
                      + "（查号位气运是不是没真正进 allLuck / 条件段）"
                    : "⇒ 条件组正常 ⇒ 问题在触发器的副本类型或其它条件上";

                Beat("getunits", "[态] 条件组 " + condId + " 能选出 " + n + " 人"
                      + (got.Count > 0 ? " [" + string.Join(",", got.ToArray()) + "]" : ""));
                Out("[getunits] " + verdict);
                SummaryAppend("=== 条件组实测 ===\n条件组 id = " + condId + "\n能选出 = " + n + " 人"
                      + (got.Count > 0 ? "\n  " + string.Join(", ", got.ToArray()) : "")
                      + "\n" + verdict + "\n");
            }
            catch (Exception e) { Out("[getunits] 异常：" + e.GetType().Name + " " + e.Message); }
        }

        // ───────────────────────── action=clear ─────────────────────────

        private static void DoClear(WorldUnitBase player)
        {
            try
            {
                // 一槽一枚后，"清干净"= 摘掉全图所有人的号位气运（51/55/56 三枚都要清，
                // 只清 1 号位会留下 2/3 号位的残留，下一趟又变成脏池子）。
                List<string> names = TargetNames();
                if (names.Count == 0)
                {
                    int cnt = StripAllSlots();
                    Beat("clear", "[态] 已清除号位气运=" + cnt + " 枚（全图）");
                    Summary("=== 多人组队探针 清除结果 ===\n已清除号位气运 = " + cnt + " 枚（全图，51/55/56 三枚都扫）\n");
                    return;
                }

                var ok = new List<string>();
                foreach (string nm in names)
                {
                    WorldUnitBase u = null;
                    string note = "";
                    u = ResolveTarget(nm, out note);
                    if (!string.IsNullOrEmpty(note)) Out("[clear] " + note);
                    if (u == null) continue;
                    bool any = false;
                    for (int s = 0; s < TeamTool.MaxMembers; s++)
                    {
                        int id = TeamTool.SlotLuckId(s, out _);
                        if (id == 0 || !HasLuckAny(u, id)) continue;
                        any = true;
                        DelLuck(u, id);
                    }
                    if (!any || !HasAnySlotLuck(u)) ok.Add(nm);
                }
                Beat("clear", "[态] 已清除「结伴而行」=" + ok.Count + "/" + names.Count);
                Summary("=== 多人组队探针 清除结果 ===\n已清除 = " + string.Join(", ", ok.ToArray()) + "\n");
            }
            catch (Exception e) { Out("clear 异常：" + e); }
        }



        // ───────────────────────── action=dumpbattle ★核心★ ─────────────────────────

        /// <summary>
        /// 打印**副本内战斗状态**：所有战斗单位 + 类型 + 是否玩家方 + 战斗数据 key。
        ///
        /// 这是回答"到底上来几个人"的唯一直接证据 ——
        /// 不看配置、不看推测，直接数场上真实存在的 `UnitCtrlBase`。
        /// </summary>
        private static void DoDumpBattle(WorldUnitBase player)
        {
            try
            {
                // 1) 是否在战斗中（真判据：WorldBattleMgr.isBattle —— 仓库里已有先例
                //    NpcInitiativeMonitor.BusyReason()：「判空是恒真的错判据」）
                bool inBattle = false;
                try
                {
                    var wb = g.world.battle;
                    if (wb != null && wb.isBattle) inBattle = true;
                }
                catch { }
                if (!inBattle)
                {
                    Beat("no-battle", "[态] 不在副本/战斗中（world.battle.isBattle = false）");
                    return;
                }

                // 1b) 先把「现在谁身上有结伴而行」列出来（手动挂载也能在这里验证）
                ListTeamLuckHolders();

                // 2) 枚举战斗单位 —— **深度反射诊断**（10-18 重写）
                //
                // 旧实现（已废弃）猜字段名 `SceneBattle.unit` + 「字段是 IEnumerable 就遍历」，
                // 结果恒报「战斗单位总数 = 0」，而玩家肉眼明明看到 3 个人站在场上。
                // **拿不到 ≠ 没有** —— 它把"我反射失败"报成了"场上没人"，是最危险的那种错。
                //
                // 新口径：**不猜字段名**。把可达对象的字段/属性全打出来（名字+类型+值摘要），
                // 由人（或下一次分析）从真实结构里读出正确的访问路径，再固化成代码。
                // 同时补一条**不依赖反射**的旁证：全图单位里有多少个处于"战斗中"坐标区域。
                var all = new List<string>();
                int npcCount = 0;

                object wb0 = null;
                try { wb0 = g.world.battle; } catch { }

                // 2a) `g.world.battle` 的真实类型与成员
                if (wb0 != null)
                {
                    DumpMembers("world.battle", wb0, all, 0);
                }
                else all.Add("  world.battle = null");

                // 2b) 顺着 battle 往下找「集合类」成员，逐层展开
                if (wb0 != null) ExploreBattleTree(wb0, "world.battle", 0, all, ref npcCount);

                var sb2 = new StringBuilder();
                sb2.Append("诊断条目=").Append(all.Count);
                Beat("battle:" + all.Count, "[态] " + sb2.ToString());

                if (all.Count > 0)
                {
                    Out("=== 副本内战斗诊断（" + all.Count + " 条）===");
                    foreach (string s in all) Out(s);
                }

                // 3) 读 battleData 里所有 key（团队标记在这里）
                DumpBattleDataKeys();

                // 4) 结论：只陈述**观测到的事实**，不再给"有没有人"的断言
                //    （旧版在这里断言"没有 NPC 单位 ⇒ 队友没上场"，而它其实是反射失败，
                //      直接误导了 3 轮排查。宁可报"未知"，也不许报错结论。）
                var sum = new StringBuilder();
                sum.Append("=== 副本内战斗诊断 ===\n");
                sum.Append("world.battle 类型 = ").Append(wb0 == null ? "(null)" : wb0.GetType().FullName).Append("\n");
                sum.Append("诊断条目 = ").Append(all.Count).Append("\n");
                sum.Append("\n★ 说明 ★\n");
                sum.Append("本探针**不再断言场上有没有人** —— 旧版靠猜字段名，恒报 0，\n");
                sum.Append("而真人明明看到有人，于是把排查带偏了 3 轮。\n");
                sum.Append("下面这些条目是真实反射结果，用来确定「正确的访问路径」。\n");
                Summary(sum.ToString());

                if (all.Count > 0)
                {
                    SummaryAppend("\n=== 反射明细（前 40 条） ===");
                    int n = 0;
                    foreach (string s in all) { if (n++ >= 40) break; SummaryAppend(s); }
                }
            }
            catch (Exception e) { Out("dumpbattle 异常：" + e); }
        }

        /// <summary>
        /// 列出全图身上带「结伴而行」的 NPC。
        ///
        /// 为什么放在 dumpbattle 里：这一行是**进副本之前的最后一道确认** ——
        /// 如果这里显示 0 个人，那进副本后当然一个队友都不会有，
        /// 就不用去猜是"引擎不支持多人"还是"根本没挂上"。
        /// 手动在游戏里挂的（对话框选「和你组队」）也会在这里体现。
        /// </summary>
        private static void ListTeamLuckHolders()
        {
            try
            {
                if (_luckTeam == 0) { Out("[队员] 结伴而行 ID 未知，跳过队员清单"); return; }
                Il2CppSystem.Collections.Generic.List<WorldUnitBase> units = null;
                try { units = g.world.unit.GetUnits(true); } catch { }
                if (units == null) return;

                var holders = new List<string>();
                var detail = new List<string>();
                for (int i = 0; i < units.Count; i++)
                {
                    WorldUnitBase u = null;
                    try { u = units[i]; } catch { continue; }
                    if (u == null) continue;

                    // ★10-19 五轮后改：三个槽都查（以前只查 1 号位那枚）★
                    //   三槽异气运设计下，只查 SlotLuckId(0) 会漏掉 2/3 号位的人。
                    int slotHit = -1;
                    for (int s = 0; s < TeamTool.MaxMembers; s++)
                    {
                        int sid = TeamTool.SlotLuckId(s, out _);
                        if (sid == 0) continue;
                        if (HasLuckAny(u, sid)) { slotHit = s; break; }
                    }
                    if (slotHit < 0) continue;

                    int ux = 0, uy = 0;
                    try { ux = u.data.unitData.pointX; uy = u.data.unitData.pointY; } catch { }
                    holders.Add(SafeName(u) + "(" + ux + "," + uy + ")");

                    // 逐人打分层状态 —— 这是「面板看得见 / 条件组看不见」分裂的直接证供
                    int sid2 = TeamTool.SlotLuckId(slotHit, out _);
                    string layer = "";
                    try { layer = TeamTool.LuckLayerState(u, sid2); } catch { }
                    detail.Add("  " + SafeName(u) + " " + (slotHit + 1) + "号位 luckId=" + sid2
                             + "：" + layer);
                }

                string line = "[队员] 带「结伴而行」的 NPC = " + holders.Count + " 个"
                            + (holders.Count > 0 ? " [" + string.Join(",", holders.ToArray()) + "]" : "");
                Out(line);
                if (detail.Count > 0)
                {
                    Out("[队员·分层] 逐人两层状态（运行时=GetLuck / 存档=addLuck）：");
                    foreach (string d in detail) Out(d);
                }
                SummaryAppend("\n=== 队友标记（进副本前）===");
                SummaryAppend("带「结伴而行」= " + holders.Count + " 个"
                            + (holders.Count > 0 ? "\n  " + string.Join("\n  ", holders.ToArray()) : ""));
                if (detail.Count > 0) SummaryAppend("\n分层：\n" + string.Join("\n", detail.ToArray()));
            }
            catch (Exception e) { Out("ListTeamLuckHolders 异常：" + e.Message); }
        }

        /// <summary>把一个战斗单位描述成一行文本（拿不到关键信息就返回 null 跳过）。</summary>
        private static string DescribeUnit(object o)
        {
            try
            {
                string typeName = o.GetType().Name;
                // 只关心人类NPC / 玩家
                if (!typeName.Contains("Human") && !typeName.Contains("Player")) return null;

                string name = "";
                string ut = "";
                try
                {
                    var t = o.GetType();
                    var f = t.GetField("unitType") ?? t.GetField("UnitType");
                    if (f != null) { var v = f.GetValue(o); if (v != null) ut = v.ToString(); }
                }
                catch { }
                try
                {
                    var t = o.GetType();
                    var f = t.GetField("unitData") ?? t.GetField("data");
                    if (f != null)
                    {
                        var v = f.GetValue(o);
                        if (v != null)
                        {
                            var nf = v.GetType().GetField("name");
                            if (nf != null) { var nv = nf.GetValue(v); if (nv != null) name = Loc(nv.ToString()); }
                        }
                    }
                }
                catch { }
                if (name.Length == 0) name = "?";
                return "type=" + typeName + "  name=「" + name + "」  unitType=" + ut;
            }
            catch { return null; }
        }

        /// <summary>
        /// ★10-19 五轮定案后的第一观测点★ 读 `SceneBattle.battleTriggerBases`（+0xC0）。
        ///
        /// 为什么是它（反编译实证，不是猜）：
        ///   `SceneBattle::Init` 的触发器准备段 = GetItem 候选集 → 合并 → 排序 →
        ///   逐行 `CommonTool.CreateClass("BattleTrigger"+row.triggerType)` → `Init(row.id)`
        ///   → `List&lt;BattleTriggerBase&gt;::Add([scene+0xC0], 实例)`。
        ///   ⇒ **这个 List 的 Count 就是「引擎为本次副本建了几个触发器实例」**，
        ///      每条实例的 `triggerItem`（BattleTriggerBase+0x10）就是它对应的配置行。
        ///
        /// 判据（一眼定性，不再靠推）：
        ///   · 拿不到 scene      ⇒ 报「未知」，**不许**当成 0
        ///   · Count == 0        ⇒ 候选集为空（GetItem 没返回任何行）⇒ 查 dungeonType/dungeonID
        ///   · Count > 0 且含我方 ⇒ 实例已建 ⇒ 问题在条件门/执行时机
        ///   · Count > 0 但不含我方 ⇒ 被 GetItem 过滤掉了
        /// </summary>
        private static void DumpBattleTriggerBases()
        {
            try
            {
                object scene = null;
                string via = null;
                try
                {
                    var wb = g.world.battle;
                    if (wb != null)
                    {
                        foreach (string nm in new[] { "scene", "sceneBattle", "curScene" })
                        {
                            if (scene != null) break;
                            try
                            {
                                var pi = wb.GetType().GetProperty(nm,
                                    System.Reflection.BindingFlags.Public |
                                    System.Reflection.BindingFlags.NonPublic |
                                    System.Reflection.BindingFlags.Instance);
                                if (pi != null) { scene = pi.GetValue(wb, null); if (scene != null) { via = "属性 " + nm; break; } }
                            }
                            catch { }
                            try
                            {
                                var fi = wb.GetType().GetField(nm,
                                    System.Reflection.BindingFlags.Public |
                                    System.Reflection.BindingFlags.NonPublic |
                                    System.Reflection.BindingFlags.Instance);
                                if (fi != null) { scene = fi.GetValue(wb); if (scene != null) { via = "字段 " + nm; break; } }
                            }
                            catch { }
                        }
                    }
                }
                catch { }
                if (scene == null)
                {
                    Out("[触发器实例] ⚠ 拿不到 SceneBattle（world.battle 上没有 scene/sceneBattle/curScene）"
                        + " —— 这是**没测到**，不是「0 个触发器」");
                    return;
                }

                object list = null;
                foreach (string nm in new[] { "battleTriggerBases", "allTrigger", "triggers" })
                {
                    if (list != null) break;
                    try
                    {
                        var fi = scene.GetType().GetField(nm,
                            System.Reflection.BindingFlags.Public |
                            System.Reflection.BindingFlags.NonPublic |
                            System.Reflection.BindingFlags.Instance);
                        if (fi != null) list = fi.GetValue(scene);
                    }
                    catch { }
                    if (list == null)
                    {
                        try
                        {
                            var pi = scene.GetType().GetProperty(nm,
                                System.Reflection.BindingFlags.Public |
                                System.Reflection.BindingFlags.NonPublic |
                                System.Reflection.BindingFlags.Instance);
                            if (pi != null) list = pi.GetValue(scene, null);
                        }
                        catch { }
                    }
                }
                if (list == null)
                {
                    Out("[触发器实例] ⚠ 拿不到 battleTriggerBases（SceneBattle 类型="
                        + scene.GetType().FullName + "）—— 没测到，不等于 0");
                    return;
                }

                int n = -1;
                try { n = (int)((dynamic)list).Count; } catch { }
                if (n < 0)
                {
                    Out("[触发器实例] ⚠ battleTriggerBases 拿不到 Count —— 没测到，不等于 0");
                    return;
                }

                Out("[触发器实例] SceneBattle.battleTriggerBases.Count = " + n
                    + "（经 " + via + " 拿 scene；每行一个 BattleTriggerBase 实例）");
                if (n == 0)
                {
                    Out("[触发器实例] ⇒ **引擎本次副本一个触发器实例都没建**"
                        + " = GetItem 候选集为空 ⇒ 查 dungeonType/dungeonID/表是否加载");
                    return;
                }

                int[] ours = { ModIds.Mod + 1, ModIds.Mod + 2, ModIds.Mod + 3 };
                var hit = new bool[ours.Length];
                for (int i = 0; i < n && i < 40; i++)
                {
                    object inst = null;
                    try { inst = ((dynamic)list)[i]; } catch { continue; }
                    if (inst == null) continue;

                    object row = null;
                    try { row = ((dynamic)inst).triggerItem; } catch { }
                    int rowId = int.MinValue, tt = int.MinValue;
                    if (row != null)
                    {
                        try { rowId = (int)((dynamic)row).id; } catch { }
                        try { tt = (int)((dynamic)row).triggerType; } catch { }
                    }
                    string mark = "";
                    for (int k = 0; k < ours.Length; k++)
                        if (rowId == ours[k]) { hit[k] = true; mark = "  ★我方★"; }
                    Out("    [" + i + "] " + inst.GetType().Name
                        + "  row.id=" + (rowId == int.MinValue ? "?" : rowId.ToString())
                        + "  triggerType=" + (tt == int.MinValue ? "?" : tt.ToString()) + mark);
                }

                for (int k = 0; k < ours.Length; k++)
                {
                    if (hit[k]) Out("[触发器实例] ✅ 我方 id=" + ours[k] + " 在实例列表里");
                    else Out("[触发器实例] ❌ 我方 id=" + ours[k] + " **不在**实例列表里"
                        + "（要么被 GetItem 过滤，要么表没加载）");
                }
            }
            catch (Exception e) { Out("[触发器实例] 异常（本轮无结论）: " + e.GetType().Name + " " + e.Message); }
        }

        /// <summary>
        /// 读 `BattleDataMgr` / 副本数据里所有 `wvTeamIn` / `team` 相关的 key。
        ///
        /// 这是判断"引擎认不认为队伍已参战"的直接证据。
        /// </summary>
        /// <summary>
        /// 读副本数据（`WorldBattleMgr.data` : `WorldBattleData`）里的所有 key。
        ///
        /// ★ 10-18 真机修正（旧版三处错，全是"猜名字"造成的）★
        ///   ① 旧版用 `FindSceneBattle()` 找 `SceneBattle` —— 真机证明那是**不存在**的路径；
        ///      真实入口是 `g.world.battle`（类型 `WorldBattleMgr`，日志实证）。
        ///   ② 旧版找**字段** `battleData` —— 真机打出来的是**属性** `data`
        ///      （`P data : WorldBattleData`）。字段找不到就 `return`，于是一直只打 "battleData = null"。
        ///   ③ 旧版只收 `Dictionary<string,string>` 且**只留名字带 team 的 key** ——
        ///      我们要找的 `alTeamIn_ok` 确实带 team，但一旦引擎换成别的容器/别的命名就全漏。
        ///      ⇒ 现在**先无条件打全部 key**，再标出 team 相关的。
        /// </summary>
        private static void DumpBattleDataKeys()
        {
            try
            {
                object wb = null;
                try { wb = g.world.battle; } catch { }
                if (wb == null) { Out("[副本数据] g.world.battle = null"); return; }

                // ① 找 WorldBattleData：属性优先，字段兜底（真机是属性 data）
                object bd = null;
                string via = null;
                foreach (string nm in new[] { "data", "worldBattleData", "battleData" })
                {
                    if (bd != null) break;
                    try
                    {
                        var pi = wb.GetType().GetProperty(nm,
                            System.Reflection.BindingFlags.Public |
                            System.Reflection.BindingFlags.NonPublic |
                            System.Reflection.BindingFlags.Instance);
                        if (pi != null) { bd = pi.GetValue(wb, null); if (bd != null) { via = "属性 " + nm; break; } }
                    }
                    catch { }
                    try
                    {
                        var fi = wb.GetType().GetField(nm,
                            System.Reflection.BindingFlags.Public |
                            System.Reflection.BindingFlags.NonPublic |
                            System.Reflection.BindingFlags.Instance);
                        if (fi != null) { bd = fi.GetValue(wb); if (bd != null) { via = "字段 " + nm; break; } }
                    }
                    catch { }
                }
                if (bd == null) { Out("[副本数据] world.battle 上找不到 data/worldBattleData/battleData"); return; }

                var t = bd.GetType();
                Out("[副本数据] 拿到 " + t.FullName + "（经 " + via + "）");

                // ①a ★10-19 五轮定案后的关键观测点：`SceneBattle.battleTriggerBases`★
                //   反编译实证（Cpp2IL [FieldOffset]）：
                //     SceneBattle +0xC0 = private List<BattleTriggerBase> battleTriggerBases
                //   SceneBattle::Init 的触发器准备段逐行 `new BattleTriggerN()` + `Init(row.id)`
                //   塞进这个列表 ⇒ **Count = 引擎为本次副本登记了几个触发器实例**。
                //   判据：
                //     Count==0  ⇒ 引擎连实例都没建（候选集为空 = GetItem 没给行）
                //     Count>0    ⇒ 实例建了；再逐条打 triggerItem 的 id/triggerType，
                //                  看我们 MID&001/002/003 在不在里面
                DumpBattleTriggerBases();

                // ①b 触发器自检：**只读、不挂补丁**，每局只跑一次。
                //     用引擎自己的 GetItem/IsTrigger 回答"我们那条会不会执行"。
                //     放在这里是因为要从 WorldBattleData 上取 dungeonBaseItem。
                try
                {
                    object dbi = null;
                    foreach (var m in Members(bd))
                        if (m.Name == "dungeonBaseItem") { dbi = m.Value; break; }
                    if (dbi != null) TriggerSelfCheck.RunOnce(dbi, Out);
                    else Out("[触发器自检] 拿不到 dungeonBaseItem，本轮跳过（不等于没执行）");
                }
                catch (Exception e) { Out("[触发器自检] 异常（本轮无结论）: " + e.Message); }

                // ② 无条件打全部成员（去重后可比对）
                foreach (var m in Members(bd))
                {
                    string vs = Brief(m.Value);
                    Out("[副本数据]   " + m.Name + " : " + Short(m.Type) + " = " + vs);
                }

                // ③ 集合：**一律不枚举** —— 改成「反射读 Count」+「按已知 key 探字典」。
                //
                // ★ 10-18 第二次真机修正（同一个坑的第二次）★
                //   旧版这里是 `m.Value as System.Collections.IDictionary` 然后 foreach 枚举。
                //   真机日志里所有集合都打成 `<List`1>` / `<Dictionary`2>`（**原始 IL2CPP 类型名**），
                //   说明 `Il2CppSystem.Collections.Generic.List/Dictionary` **不实现**托管侧的
                //   `System.Collections.ICollection` / `IDictionary` ⇒ cast 恒为 null
                //   ⇒ 循环一次都没进 ⇒ 打出 "字典条目合计 = 0"。
                //   **那个 0 是「我读不到」，不是「引擎里没有」** —— 正是本文件开头警告的坑。
                //   本仓库 `drama/DramaConfDump.cs` 早就写下这条结论：
                //   Il2Cpp 字典的枚举器在 Unhollower 下不保证可用，`ContainsKey` + 索引器才稳。
                var team = new List<string>();
                var counts = new List<string>();
                var probeErr = new List<string>();
                foreach (var m in Members(bd))
                {
                    if (m.Value == null) continue;
                    int? n = TryCount(m.Value);
                    if (!n.HasValue) continue;                 // 不是集合
                    string tn = Short(m.Type);
                    counts.Add("  " + m.Name + " : " + tn + "  Count=" + n.Value);
                    Out("[副本数据]   ◆ " + m.Name + " : " + tn + "  Count=" + n.Value);

                    if (!tn.Contains("Dictionary")) continue;

                    // ★ 先尝试**全量读出**（Keys 属性 / 枚举器）—— 这样不管 key 叫什么都能看到。
                    //   只按已知 key 探会漏掉我们没想到的名字（上一次 Count=3 却一条都没探到）。
                    var kvAll = TryDumpDict(m.Value);
                    if (kvAll != null)
                    {
                        Out("[副本数据]     ◆ " + m.Name + " 全量读出 " + kvAll.Count + " 条：");
                        foreach (string kv in kvAll)
                        {
                            Out("[副本数据]        " + kv);
                            bool isT = kv.ToLowerInvariant().Contains("team")
                                       || kv.ToLowerInvariant().Contains("alteam");
                            team.Add("  " + m.Name + "  " + kv + (isT ? "   ★team" : ""));
                        }
                        continue;                    // 已全量读到，不必再按名探
                    }

                    foreach (string k in ProbeKeys)
                    {
                        bool has;
                        string err;
                        string v = TryDictProbe(m.Value, k, out has, out err);
                        if (err != null)
                        {
                            // ★ 探测本身失败也要说出来 —— 绝不允许伪装成"这个 key 不存在"
                            probeErr.Add("  ⚠ " + m.Name + " 探测异常：" + err);
                            continue;
                        }
                        if (!has) continue;
                        string line = "  " + m.Name + "[" + k + "] = " + v;
                        team.Add(line);
                        Out("[副本数据]     ★命中 " + m.Name + "[" + k + "] = " + v);
                    }
                }
                Out("[副本数据] 集合成员 " + counts.Count + " 个；按 key 命中 team 相关 = " + team.Count);

                SummaryAppend("\n=== WorldBattleData 各集合的 Count（Count 属性反射，非枚举）===");
                foreach (string x in counts) SummaryAppend(x);
                SummaryAppend("\n=== 按 key 探测结果（ContainsKey + 索引器，不依赖枚举器）===");
                if (team.Count > 0)
                {
                    foreach (string x in team) SummaryAppend(x);
                    SummaryAppend("★ 命中 team key ⇒ setBattleData 那一步**执行过** ★");
                }
                else if (probeErr.Count > 0)
                {
                    foreach (string x in probeErr) SummaryAppend(x);
                    SummaryAppend("⚠ 探测本身抛异常 ⇒ 本轮**得不出结论**，不要读成「触发器没跑」。");
                }
                else
                {
                    SummaryAppend("  以上 " + ProbeKeys.Length + " 个 key 一个都没命中。");
                    SummaryAppend("  探测走的是 ContainsKey + 索引器（稳），**不是枚举** ——");
                    SummaryAppend("  所以「探不到」这次是可信的 ⇒ DungeonTrigger MID&001 没有执行。");
                }
            }
            catch (Exception e) { Out("DumpBattleDataKeys 异常：" + e.Message); }
        }

        // ───────────────────────── 深度反射诊断（10-18 新增）─────────────────────────

        /// <summary>
        /// 把一个对象的成员全打出来：`名字 : 类型 = 值摘要`。
        ///
        /// 存在理由：旧实现靠**猜**字段名（`SceneBattle.unit`），猜错就静默报 0，
        /// 于是"反射失败"被当成"场上没人"，把排查带偏了好几轮。
        /// 正确做法是先把真实结构打出来，看清了再写死访问路径。
        ///
        /// 只打**浅层摘要**（字符串截断、集合只报 Count），避免把整个战斗场景 dump 成几 MB。
        /// </summary>
        private static void DumpMembers(string label, object o, List<string> sink, int depth)
        {
            if (sink.Count >= MaxDumpLines) return;
            if (o == null) { sink.Add(Pad(depth) + label + " = null"); return; }
            var t = o.GetType();
            sink.Add(Pad(depth) + "── " + label + " : " + t.FullName);
            const System.Reflection.BindingFlags F =
                System.Reflection.BindingFlags.Public |
                System.Reflection.BindingFlags.NonPublic |
                System.Reflection.BindingFlags.Instance;

            System.Reflection.FieldInfo[] fs = null;
            try { fs = t.GetFields(F); } catch { }
            if (fs != null)
                foreach (var f in fs)
                {
                    object v = null;
                    string vs;
                    try { v = f.GetValue(o); vs = Brief(v); }
                    catch (Exception e) { vs = "<取值异常:" + e.GetType().Name + ">"; }
                    sink.Add(Pad(depth) + "  F " + f.Name + " : " + Short(f.FieldType) + " = " + vs);
                }

            System.Reflection.PropertyInfo[] ps = null;
            try { ps = t.GetProperties(F); } catch { }
            if (ps != null)
                foreach (var p in ps)
                {
                    if (p.GetIndexParameters() != null && p.GetIndexParameters().Length > 0) continue;
                    object v = null;
                    string vs;
                    try { v = p.GetValue(o, null); vs = Brief(v); }
                    catch (Exception e) { vs = "<取值异常:" + e.GetType().Name + ">"; }
                    sink.Add(Pad(depth) + "  P " + p.Name + " : " + Short(p.PropertyType) + " = " + vs);
                }
        }

        /// <summary>
        /// 沿 `world.battle` 往下找**集合类**成员，逐层展开 2 层。
        ///
        /// 目的：找出「战斗单位集合」到底挂在哪个字段上。找法不靠名字猜测 ——
        /// 凡是值实现了 IEnumerable 且元素类型名里带 Unit/Human/Player 的，就展开它的元素。
        /// </summary>
        /// <summary>
        /// 沿 `world.battle` 往下找**集合类**成员与子对象，逐层展开。
        ///
        /// ★ 两个已修的坑（10-18 真机）★
        ///   ① **只遍历了字段**：而 `WorldBattleMgr.data`（`WorldBattleData`）是**属性**，
        ///      于是整棵树走不下去，日志里只剩 5 行 `world.battle` 自身的成员。
        ///      ⇒ 现在字段 + 属性**都遍历**。
        ///   ② **命名空间过滤太严**：原来要求 `Namespace != null 且不以 System 开头`。
        ///      IL2CPP 里无命名空间的类型 `Namespace` 是 null，会被整类跳过。
        ///      ⇒ 现在只排除**明确的基础类型/字符串**，其余都往下探。
        /// </summary>
        private static void ExploreBattleTree(object root, string path, int depth,
                                              List<string> sink, ref int npcCount)
        {
            if (root == null || depth > 3) return;
            // 硬上限：战斗中场景对象极多（单位/弹幕/特效），不封顶会写出几十 MB 并明显卡顿
            if (sink.Count >= MaxDumpLines) return;

            foreach (var m in Members(root))
            {
                object v = m.Value;
                if (v == null || v is string) continue;

                var coll = v as System.Collections.IEnumerable;
                int? nCount = TryCount(v);          // ★ 枚举器不可靠，Count 属性兜底
                if (coll != null || nCount.HasValue)
                {
                    int cnt = 0;
                    string elemType = "?";
                    var items = new List<string>();
                    if (coll != null)
                    {
                        try
                        {
                            foreach (object o in coll)
                            {
                                if (o == null) continue;
                                if (cnt == 0) elemType = o.GetType().Name;
                                if (items.Count < 8) items.Add(Brief(o));
                                cnt++;
                            }
                        }
                        catch { }
                    }
                    // ★ 枚举不到 ≠ 空：Count 有值就报 Count，并注明枚举不可用。
                    //   旧版只在 cnt > 0 时才输出 ⇒ Il2Cpp 的 List/Dictionary 枚举不出来时
                    //   整行消失，看起来像"这个成员不存在"，正是"拿不到被读成没有"。
                    int? shown = cnt > 0 ? cnt : nCount;
                    if (shown.HasValue)
                    {
                        bool enumDead = cnt == 0 && shown.Value > 0;
                        sink.Add(Pad(depth) + "  ★集合 " + path + "." + m.Name
                                 + " : " + Short(m.Type) + "  Count=" + shown.Value
                                 + (enumDead ? "（⚠枚举器不可用，Count 来自属性）" : "")
                                 + " 元素=" + elemType);
                        foreach (string it in items) sink.Add(Pad(depth) + "      - " + it);
                        string sn = Short(m.Type);
                        if (elemType.Contains("Unit") || elemType.Contains("Human") || elemType.Contains("Player")
                            || sn.Contains("Unit"))
                            npcCount = Math.Max(npcCount, shown.Value);
                    }
                    continue;
                }

                // 非集合：引擎对象再往下探（排除明确的基础类型）
                var vt = v.GetType();
                if (depth < 3 && !IsPlain(vt))
                {
                    ExploreBattleTree(v, path + "." + m.Name, depth + 1, sink, ref npcCount);
                }
            }
        }

        /// <summary>是不是"不需要再往下探"的平凡类型（基础类型/枚举/字符串/委托）。</summary>
        private static bool IsPlain(Type t)
        {
            if (t == null) return true;
            if (t.IsPrimitive || t.IsEnum || t == typeof(string)) return true;
            if (t.IsArray && t.GetElementType() != null && IsPlain(t.GetElementType())) return true;
            if (typeof(Delegate).IsAssignableFrom(t)) return true;
            string n = t.FullName ?? "";
            // 只排除真正的 BCL 值类型/容器；**不排除** null 命名空间的引擎类型
            return n.StartsWith("System.Numerics") || n.StartsWith("System.DateTime")
                || n.StartsWith("System.TimeSpan") || n.StartsWith("System.Decimal");
        }

        /// <summary>一个成员（字段或属性）的名字/类型/值 —— 反射的统一视图。</summary>
        private struct Member
        {
            public string Name;
            public Type Type;
            public object Value;
        }

        /// <summary>
        /// 枚举对象的**字段 + 属性**（`ExploreBattleTree` 与 `DumpMembers` 共用）。
        /// 取不到值的成员直接跳过（不写 "(异常)" 噪音）。
        /// </summary>
        private static List<Member> Members(object o)
        {
            var list = new List<Member>();
            if (o == null) return list;
            var t = o.GetType();
            const System.Reflection.BindingFlags F =
                System.Reflection.BindingFlags.Public |
                System.Reflection.BindingFlags.NonPublic |
                System.Reflection.BindingFlags.Instance;

            System.Reflection.FieldInfo[] fs = null;
            try { fs = t.GetFields(F); } catch { }
            if (fs != null)
                foreach (var f in fs)
                {
                    object v;
                    try { v = f.GetValue(o); } catch { continue; }
                    list.Add(new Member { Name = f.Name, Type = f.FieldType, Value = v });
                }

            System.Reflection.PropertyInfo[] ps = null;
            try { ps = t.GetProperties(F); } catch { }
            if (ps != null)
                foreach (var p in ps)
                {
                    try { if (p.GetIndexParameters().Length > 0) continue; } catch { continue; }
                    object v;
                    try { v = p.GetValue(o, null); } catch { continue; }
                    list.Add(new Member { Name = p.Name, Type = p.PropertyType, Value = v });
                }
            return list;
        }

        /// <summary>诊断输出的硬上限（条）。超过就截断 —— 宁可少打，不可拖垮游戏。</summary>
        private const int MaxDumpLines = 300;

        /// <summary>
        /// 要**探测**的 key 清单（为什么是探测而不是枚举，见 ③ 处注释）。
        /// 我们自己的表会写的 key + 组队 mod 的 key 都列上；命中即为硬证据。
        /// </summary>
        private static readonly string[] ProbeKeys =
        {
            "alTeamIn_ok", "wvTeamIn_ok", "wvTeamIn_hurt", "wvTeamIn_watch",
            "teamIn_ok", "teamIn", "alTeamIn", "team",
        };

        /// <summary>
        /// 反射读 `Count`。Il2Cpp 容器的**枚举器**不可靠，但 `Count` 属性是稳的 ——
        /// 这样即使读不到元素，也能区分「空集合(Count=0)」和「压根读不到(null)」。
        /// </summary>
        /// <summary>
        /// 安全取出 Il2Cpp List 的元素：枚举器在 Unhollower 下经常不工作，
        /// 改用「Count 属性 + get_Item 索引器」逐项取，这条路径是验证过可行的。
        /// 取不到就返回空列表 —— 空列表 != 引擎真没数据，调用方需自行标注。
        /// </summary>
        private static List<object> SafeElements(object list)
        {
            var res = new List<object>();
            if (list == null) return res;
            int? n = TryCount(list);
            if (n == null || n.Value <= 0) return res;
            var t = list.GetType();
            var getItem = t.GetMethods()
                .FirstOrDefault(m => m.Name == "get_Item" && m.GetParameters().Length == 1);
            if (getItem == null) return res;
            for (int i = 0; i < n.Value && i < 32; i++)
            {
                try { res.Add(getItem.Invoke(list, new object[] { i })); }
                catch { break; }
            }
            return res;
        }

        private static int? TryCount(object o)
        {
            if (o == null) return null;
            try
            {
                var pi = o.GetType().GetProperty("Count",
                    System.Reflection.BindingFlags.Public |
                    System.Reflection.BindingFlags.NonPublic |
                    System.Reflection.BindingFlags.Instance);
                if (pi == null || !pi.CanRead) return null;
                object v = pi.GetValue(o, null);
                if (v is int i) return i;
                return v == null ? (int?)null : Convert.ToInt32(v);
            }
            catch { return null; }
        }

        /// <summary>
        /// 按 key 探一个字典：`ContainsKey(key)` + 索引器，**不枚举**。
        ///
        /// 用 `dynamic` 派发：IL2CPP 字典的键类型是 `Il2CppSystem.String`，
        /// `MethodInfo.Invoke` **不会**套用隐式转换，而 dynamic 绑定器会
        /// （同 `drama/DramaConfDump.ProbeKeys` 已在用的写法）。
        ///
        /// `err != null` ⇒ **探测本身失败**（必须与"没有这个 key"区分开，否则又变成
        /// "拿不到 ⇒ 报没有"）。`err == null && has == false` ⇒ 确定没有这个 key。
        /// </summary>
        private static string TryDictProbe(object dict, string key, out bool has, out string err)
        {
            has = false;
            err = null;
            if (dict == null) { err = "dict 为 null"; return null; }
            try
            {
                has = (bool)((dynamic)dict).ContainsKey(key);
            }
            catch (Exception e)
            {
                err = e.GetType().Name + ": " + e.Message;
                return null;
            }
            if (!has) return null;
            try { return Convert.ToString(((dynamic)dict)[key]); }
            catch (Exception e) { return "(有 key，取值抛 " + e.GetType().Name + ")"; }
        }

        private static string Pad(int d) { return new string(' ', d * 2); }

        /// <summary>
        /// 把一个字典的**全部键值**读出来。
        ///
        /// 为什么不用 `foreach`/`IEnumerable`：`Il2CppSystem.Collections.Generic.Dictionary`
        /// **不实现**托管侧的 `System.Collections.IDictionary`（真机实测：cast 恒为 null），
        /// 且其枚举器在 Unhollower 下不保证可用（`drama/DramaConfDump.cs:28` 已有此结论）。
        ///
        /// 这里走**三条**彼此独立的通路，任一成功即可：
        ///   ① `Keys` 属性（Il2Cpp 字典有）→ 读 `Count` + `get_Item` 索引 KeyCollection；
        ///   ② `CopyTo` 方法 → 拷到数组；
        ///   ③ `GetEnumerator` + `MoveNext`/`get_Current`（最后尝试）。
        /// 返回 null = **三条全失败**（明确"读不到"，不等于字典为空）。
        /// </summary>
        private static List<string> TryDumpDict(object dict)
        {
            if (dict == null) return null;
            const System.Reflection.BindingFlags BF =
                System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic |
                System.Reflection.BindingFlags.Instance;
            var t = dict.GetType();

            // ① Keys 属性
            try
            {
                var piKeys = t.GetProperty("Keys", BF);
                var piCount = t.GetProperty("Count", BF);
                if (piKeys != null && piCount != null)
                {
                    object keys = piKeys.GetValue(dict, null);
                    if (keys != null)
                    {
                        var kt = keys.GetType();
                        var kCount = kt.GetProperty("Count", BF);
                        var kItem = kt.GetMethod("get_Item", BF);
                        var dictItem = t.GetMethod("get_Item", BF);
                        if (kCount != null && kItem != null && dictItem != null)
                        {
                            object cv = kCount.GetValue(keys, null);
                            int n = cv is int i0 ? i0 : (cv == null ? -1 : Convert.ToInt32(cv));
                            if (n < 0) return null;
                            var outp = new List<string>();
                            for (int k = 0; k < n; k++)
                            {
                                object key;
                                try { key = kItem.Invoke(keys, new object[] { k }); }
                                catch { return null; }
                                string val = "?";
                                try { val = Convert.ToString(dictItem.Invoke(dict, new[] { key })); }
                                catch (Exception ee) { val = "(取值抛 " + ee.GetType().Name + ")"; }
                                outp.Add((key == null ? "(null)" : key.ToString()) + " = " + val);
                            }
                            return outp;
                        }
                    }
                }
            }
            catch { }

            // ② GetEnumerator + MoveNext（IL2CPP 枚举器，成败听天由命，但值得一试）
            try
            {
                var miEnum = t.GetMethod("GetEnumerator", BF);
                if (miEnum != null)
                {
                    object en = miEnum.Invoke(dict, null);
                    if (en != null)
                    {
                        var et = en.GetType();
                        var miNext = et.GetMethod("MoveNext", BF);
                        var piCur = et.GetProperty("Current", BF) ?? et.GetProperty("get_Current", BF);
                        var miCur = et.GetMethod("get_Current", BF);
                        if (miNext != null && (piCur != null || miCur != null))
                        {
                            var outp = new List<string>();
                            int guard = 0;
                            while (guard++ < 256)
                            {
                                object mv;
                                try { mv = miNext.Invoke(en, null); } catch { return outp.Count > 0 ? outp : null; }
                                if (!(mv is bool b) || !b) break;
                                object cur = null;
                                try
                                {
                                    cur = piCur != null ? piCur.GetValue(en, null)
                                                        : miCur.Invoke(en, null);
                                }
                                catch { break; }
                                if (cur == null) continue;
                                // KeyValuePair：取 Key / Value
                                string kk = null, vv = null;
                                try
                                {
                                    var ct = cur.GetType();
                                    var pk = ct.GetProperty("Key", BF);
                                    var pv = ct.GetProperty("Value", BF);
                                    if (pk != null) kk = Convert.ToString(pk.GetValue(cur, null));
                                    if (pv != null) vv = Convert.ToString(pv.GetValue(cur, null));
                                }
                                catch { }
                                outp.Add((kk ?? cur.ToString()) + " = " + (vv ?? "?"));
                            }
                            if (outp.Count > 0) return outp;
                        }
                    }
                }
            }
            catch { }

            return null;
        }

        private static string Short(Type t)
        {
            if (t == null) return "?";
            string n = t.Name;
            if (t.IsGenericType)
            {
                var args = t.GetGenericArguments();
                var parts = new string[args.Length];
                for (int i = 0; i < args.Length; i++) parts[i] = Short(args[i]);
                n = n.Substring(0, Math.Max(0, n.IndexOf('`'))) + "<" + string.Join(",", parts) + ">";
            }
            return n;
        }

        /// <summary>值的浅层摘要：字符串截断、集合报 Count、对象报类型名 + 名字（若能取到）。</summary>
        private static string Brief(object v)
        {
            if (v == null) return "null";
            try
            {
                if (v is string s) return "\"" + (s.Length > 40 ? s.Substring(0, 40) + "…" : s) + "\"";
                var t = v.GetType();
                if (t.IsPrimitive || t.IsEnum) return v.ToString();
                var coll = v as System.Collections.ICollection;
                if (coll != null) return "<集合 Count=" + coll.Count + ">";
                var en = v as System.Collections.IEnumerable;
                if (en != null && !(v is string)) return "<可枚举 " + t.Name + ">";

                // 引擎单位：尽量取名字
                string nm = null;
                try
                {
                    var f = t.GetField("unitData",
                        System.Reflection.BindingFlags.Public |
                        System.Reflection.BindingFlags.NonPublic |
                        System.Reflection.BindingFlags.Instance);
                    if (f != null)
                    {
                        var ud = f.GetValue(v);
                        if (ud != null)
                        {
                            var pd = ud.GetType().GetProperty("propertyData");
                            if (pd != null)
                            {
                                var pv = pd.GetValue(ud, null);
                                if (pv != null)
                                {
                                    var gm = pv.GetType().GetMethod("GetName", Type.EmptyTypes);
                                    if (gm != null) nm = gm.Invoke(pv, null) as string;
                                }
                            }
                        }
                    }
                }
                catch { }
                return "<" + t.Name + (string.IsNullOrEmpty(nm) ? "" : " " + nm) + ">";
            }
            catch { return "<" + v.GetType().Name + ">"; }
        }

        // ───────────────────────── 测试专用：队友 HP=1 ─────────────────────────

        // ───────────────────────── SceneBattle 取实例（反射兜底）─────────────────────────

        /// <summary>
        /// 拿 `SceneBattle` 实例。
        ///
        /// 为什么用反射而不是直接写 `SceneType.battle`：
        ///   引擎里 `SceneBattle` **没有任何 static 字段/方法**（反射实证），
        ///   唯一持有它的是 `SceneType.battle` 属性；而 `SceneType` 自身是不是静态单例、
        ///   这台机器上没验证过。写成编译期引用一旦猜错就是**整个 DLL 编译不过**，
        ///   探针是诊断件，绝不能因为一个可选的附加信息就让主程序编不过。
        ///   所以这里反射找：先试 `SceneType` 的静态属性/字段，再退回扫描常见持有者。
        /// 拿不到就返回 null，调用方退化为"只报 isBattle"，不影响主结论。
        /// </summary>
        private static object FindSceneBattle()
        {
            // ① SceneType.battle（静态属性）
            try
            {
                var t = Type.GetType("SceneType, Assembly-CSharp");
                if (t == null)
                {
                    foreach (var asm in AppDomain.CurrentDomain.GetAssemblies())
                    {
                        try { t = asm.GetType("SceneType"); } catch { }
                        if (t != null) break;
                    }
                }
                if (t != null)
                {
                    var p = t.GetProperty("battle",
                        System.Reflection.BindingFlags.Public |
                        System.Reflection.BindingFlags.NonPublic |
                        System.Reflection.BindingFlags.Static |
                        System.Reflection.BindingFlags.Instance);
                    if (p != null)
                    {
                        // ★ 不再用 GetGetMethod(true).IsStatic 先判 ★
                        //   反编译确认 `SceneType.battle` 是静态属性；IL2CPP 下
                        //   `GetGetMethod(true)` 可能返回 null / IsStatic 报 false，
                        //   会把“能读”误判成“拿不到”。直接 GetValue(null)，
                        //   抛异常再退回字段。
                        object inst = null;
                        try { inst = p.GetValue(null); } catch { }
                        if (inst != null) return inst;
                    }

                    var f = t.GetField("battle",
                        System.Reflection.BindingFlags.Public |
                        System.Reflection.BindingFlags.NonPublic |
                        System.Reflection.BindingFlags.Static);
                    if (f != null)
                    {
                        try
                        {
                            object inst = f.GetValue(null);
                            if (inst != null) return inst;
                        }
                        catch { }
                    }
                }
            }
            catch { }
            return null;
        }

        // ───────────────────────── 小工具 ─────────────────────────

        private static string SafeName(WorldUnitBase u)
        {
            return UnitIdentity.Name(u, "?");
        }

        private static WorldUnitLuckBase SafeGetLuck(WorldUnitBase u, int luckId)
        {
            if (u == null || luckId == 0) return null;
            try { return u.GetLuck(luckId); } catch { return null; }
        }

        /// <summary>
        /// 取玩家周围 `range` 格内的单位 —— **不拉全图**。
        ///
        /// 为什么（10-18）：`GetUnits(true)` 单次 ~100ms / 1556 个单位，
        /// 而这个动作每 2 秒跑一次，直接把游戏拖卡。仓库里 `RelationNetwork`
        /// 早已实测过引擎自带按坐标查询可用：
        ///   `WorldUnitMgr.GetUnit(Vector2Int, int range)` / `GetUnitExact(...)`
        ///
        /// 安全策略（与 RelationNetwork 同款）：
        ///   ① 快查结果**逐个复核坐标**（`pointX/Y` 必须在 range 内），不信 API 语义；
        ///   ② 快查抛异常/返回 null → 回退全图并留一行日志（宁可慢一次，不可静默漏人）。
        /// </summary>
        private static Il2CppSystem.Collections.Generic.List<WorldUnitBase> NearUnits(int px, int py, int range)
        {
            // 快查：GetUnit(Vector2Int, range)
            try
            {
                var pt = new UnityEngine.Vector2Int(px, py);
                var list = g.world.unit.GetUnit(pt, range);
                if (list != null && list.Count > 0)
                {
                    var ok = new Il2CppSystem.Collections.Generic.List<WorldUnitBase>();
                    bool anyBad = false;
                    for (int i = 0; i < list.Count; i++)
                    {
                        WorldUnitBase u = null;
                        try { u = list[i]; } catch { continue; }
                        if (u == null) continue;
                        int ux = 0, uy = 0;
                        try { ux = u.data.unitData.pointX; uy = u.data.unitData.pointY; } catch { anyBad = true; continue; }
                        if (Math.Abs(ux - px) > range || Math.Abs(uy - py) > range) { anyBad = true; continue; }
                        ok.Add(u);
                    }
                    if (ok.Count > 0 && !anyBad) return ok;
                    if (ok.Count > 0) return ok;          // 复核过的子集也够用
                }
            }
            catch (Exception e) { Out("[降级] GetUnit 快查失败 → 回退全图：" + e.GetType().Name); }

            // 回退：全图（慢，但不会漏）
            try { return g.world.unit.GetUnits(true); } catch { }
            return null;
        }

        /// <summary>
        /// 「这枚气运在他身上吗」—— **两套存储都查**。
        ///
        /// ★ 10-18 重复挂载事故（必读）★
        ///   引擎有两套存储：`u.GetLuck(id)`（运行时对象）与
        ///   `propertyData.addLuck`（面板/存档读的那份）。旧版幂等判据只查前者，
        ///   而后来的写入改成写后者 ⇒ 判据恒为「没有」⇒ **每 2 秒重复挂一次**。
        ///   真机后果：NPC 面板上出现 **6 个「结伴而行」**（3 个 NPC × 各 2 份），
        ///   而且面板/存档里越堆越多、游戏明显变卡。
        ///
        /// 所以判重必须**两边都看**：任一边有就算「已有」。
        /// </summary>
        /// <summary>该单位身上还有没有任一号位的队友气运。</summary>
        private static bool HasAnySlotLuck(WorldUnitBase u)
        {
            for (int s = 0; s < TeamTool.MaxMembers; s++)
            {
                int id = TeamTool.SlotLuckId(s, out _);
                if (id != 0 && HasLuckAny(u, id)) return true;
            }
            return false;
        }

        private static bool HasLuckAny(WorldUnitBase u, int luckId)
        {
            if (u == null || luckId == 0) return false;
            try { if (u.GetLuck(luckId) != null) return true; } catch { }
            try { if (TeamTool.InAddLuck(u, luckId)) return true; } catch { }
            return false;
        }

        /// <summary>
        /// 把单位身上**重复**的同 id 气运清理掉，只留一枚。
        ///
        /// 为什么需要：上面那次事故已经往存档里堆了重复条目，光修判据不会自动清理
        /// —— 旧存档里的 6 个还在。每拍调用一次（幂等），让存量自己收敛。
        /// 返回清掉的个数。
        /// </summary>
        private static int DedupeLuck(WorldUnitBase u, int luckId)
        {
            if (u == null || luckId == 0) return 0;
            int removed = 0;
            // addLuck 列表：从头扫，见过就记下，再遇到就删
            try
            {
                var arr = u.data.unitData.propertyData.addLuck;
                if (arr != null)
                {
                    bool seen = false;
                    for (int i = arr.Count - 1; i >= 0; i--)
                    {
                        object ld = null;
                        try { ld = arr[i]; } catch { continue; }
                        if (ld == null) continue;
                        int id = 0;
                        try { id = (int)((dynamic)ld).id; } catch { continue; }
                        if (id != luckId) continue;
                        if (!seen) { seen = true; continue; }   // 从后往前，留下最后一个
                        try { arr.RemoveAt(i); removed++; } catch { }
                    }
                }
            }
            catch { }
            return removed;
        }

        /// <summary>哪种挂载策略成功过（0=还没成功过；成功后固定用那条，避免每次试错）。</summary>
        private static int _luckStrategy;

        /// <summary>
        /// 给单位挂气运。**多策略尝试**，并把「哪条路通了」写进日志。
        ///
        /// 为什么搞成多策略（10-17 实测教训）：
        ///   `WorldFactory.CreateLuckInID(id)` 造出来的对象，其 `luckData` 字段是 **null**
        ///   （它只装好了 `luckConf` 配置，没装运行时数据），所以
        ///   `u.CreateLuck(lk.luckData)` 直接 `NullReferenceException`。
        ///   而引擎里能造 `LuckData` 的口子只有：
        ///     · `DataUnit.LuckData` 的**无参构造器**（反射实证：只有一个 `ctor()`）
        ///     · `PropertyData.AddAddLuck(LuckData)` —— 交给属性层去加
        ///   方法体看不见（IL2CPP 只有签名），所以**逐条试、试通了记下来**，
        ///   而不是继续猜哪条对。
        /// </summary>
        private static bool AddLuck(WorldUnitBase u, int luckId)
        {
            if (u == null || luckId == 0) return false;

            // 已经试通过某条 ⇒ 直接用那条
            if (_luckStrategy != 0 && TryStrategy(_luckStrategy, u, luckId)) return true;

            for (int st = 1; st <= 3; st++)
            {
                if (TryStrategy(st, u, luckId))
                {
                    if (_luckStrategy != st)
                    {
                        _luckStrategy = st;
                        Out("★ 挂气运策略 #" + st + " 成功（后续固定用它）：" + StrategyName(st));
                        SummaryAppend("挂气运成功策略 = #" + st + " " + StrategyName(st));
                    }
                    return true;
                }
            }
            return false;
        }

        private static string StrategyName(int st)
        {
            switch (st)
            {
                case 1: return "WorldFactory.CreateLuckInID → u.CreateLuck(lk.luckData)";
                case 2: return "propertyData.AddAddLuck(data) → 复查 addLuck";
                case 3: return "addLuck.Add(data) 直塞 → 复查 addLuck";
                default: return "?";
            }
        }

        /// <summary>试一条策略；成功与否用 `GetLuck(id)` 复查，不靠"没抛异常"当成功。</summary>
        private static bool TryStrategy(int st, WorldUnitBase u, int luckId)
        {
            try
            {
                switch (st)
                {
                    case 1:
                        {
                            var lk = WorldFactory.CreateLuckInID(luckId);
                            if (lk == null) return false;
                            var ld = lk.luckData;
                            if (ld == null) return false;          // 上一版就是死在这
                            var made = u.CreateLuck(ld);
                            return made != null && SafeGetLuck(u, luckId) != null;
                        }
                    case 2:
                        {
                            // 判据必须是 addLuck（面板读的那份），不是 GetLuck —— 见 TeamTool.AddLuck 的 ★
                            var ld = new DataUnit.LuckData();
                            ld.id = luckId;
                            ld.duration = -1;
                            u.data.unitData.propertyData.AddAddLuck(ld);
                            if (TeamTool.InAddLuck(u, luckId)) return true;
                            // 属性层没收就再走引擎入口，最后直塞列表
                            try { u.CreateLuck(ld); } catch { }
                            if (TeamTool.InAddLuck(u, luckId)) return true;
                            try { u.data.unitData.propertyData.addLuck.Add(ld); } catch { }
                            return TeamTool.InAddLuck(u, luckId);
                        }
                    case 3:
                        {
                            var ld = new DataUnit.LuckData();
                            ld.id = luckId;
                            ld.duration = -1;
                            try { u.data.unitData.propertyData.addLuck.Add(ld); } catch { }
                            return TeamTool.InAddLuck(u, luckId);
                        }
                }
            }
            catch (Exception e)
            {
                // 只在"还没成功过"时打第一层异常，避免每 2 秒刷屏
                if (_luckStrategy == 0 && st == 1) Out("策略#" + st + " 异常：" + e.GetType().Name + " " + e.Message);
                return false;
            }
            return false;
        }

        /// <summary>
        /// 摘气运。引擎里是 `DestroyLuck(WorldUnitLuckBase)`，**没有** `RemoveLuck`。
        /// </summary>
        private static bool DelLuck(WorldUnitBase u, int luckId)
        {
            if (u == null || luckId == 0) return false;
            try
            {
                // ★ 三层清，且**按 allLuck 索引直删**（10-20 根因定案）★
                //
                //   ── 为什么旧版清不干净（真机实证，2026-10-19 21:29:35）──
                //   旧版靠 `u.GetLuck(luckId)` 拿对象再 `DestroyLuck`，但
                //   `GetLuck` 是**按内嵌 `luckData.id` 值相等**匹配的；而 type=2 的气运经
                //   `u.CreateLuck` 进 `allLuck` 后 **`luckData=null`**
                //   （`WorldUnitLuckDyn` ctor 不填 +0x20，见 AddLuck 的 ★动态气运陷阱★ 段）。
                //   ⇒ `GetLuck` 永远返回 null ⇒ 拿不到对象 ⇒ `DestroyLuck` 无从调用
                //   ⇒ 那枚僵尸对象**永久留在 allLuck 里**。
                //   而 `InAddLuck` 只看面板层 `addLuck`，也看不见它。
                //   于是 `HasLuckAny` 报"已彻底清空 ✓"，**但引擎的 `UnitCondition::Feature`
                //   是直接遍历 `allLuck` 看对象的** ⇒ 引擎仍认为这个人带着号位气运
                //   ⇒ 该号位候选集变 2 人 ⇒ `SortRandom` 随机抽 ⇒ 每次进副本队友都不一样。
                //   日志铁证：
                //     21:29:35.011 [清运] 清掉 4 枚；就地复核：已彻底清空 ✓
                //     21:29:35.385 [pair] 挂完复核（全图）：1号位=**2人**[方成和,江宇文]
                //
                //   ── 现在怎么做 ──
                //   ① 直接遍历 `allLuck`，凡 `luckData.id == luckId` 的对象，**按索引**
                //      交给 `DestroyLuck`（它收对象引用，能删 luckData=null 的僵尸）；
                //   ② `luckData=null` 的僵尸读不到 id，无法判断归属 ⇒ 用**面板层对账**：
                //      若面板层已无该 id、而 allLuck 里仍有 null-data 对象，说明它是我们
                //      这枚的残骸（本 mod 只往这几个单位挂 type=2 气运），一并清掉；
                //   ③ 面板层 `DelAddLuck` 循环删（它只删第一条匹配）；
                //   ④ 退出条件改成"三层都读不到"，而不是"这一轮没删到东西"。
                for (int round = 0; round < 64; round++)
                {
                    bool did = false;

                    // ① 按 allLuck 索引找同 id 的对象（含 luckData 可读的）
                    object target = null;
                    bool hasNullData = false;
                    try
                    {
                        var all = ((dynamic)u).allLuck;
                        int n = (int)((dynamic)all).Count;
                        for (int i = 0; i < n; i++)
                        {
                            object el = null;
                            try { el = ((dynamic)all)[i]; } catch { continue; }
                            if (el == null) continue;
                            object ld = null;
                            try { ld = ((dynamic)el).luckData; } catch { }
                            if (ld == null) { hasNullData = true; continue; }
                            int id = int.MinValue;
                            try { id = (int)((dynamic)ld).id; } catch { }
                            if (id == luckId) { target = el; break; }
                        }
                    }
                    catch { }

                    if (target != null)
                    {
                        try { u.DestroyLuck((WorldUnitLuckBase)target); did = true; } catch { }
                    }

                    // ② 面板层：DelAddLuck 只删第一条匹配 ⇒ 循环
                    try
                    {
                        if (TeamTool.InAddLuck(u, luckId))
                        {
                            u.data.unitData.propertyData.DelAddLuck(luckId);
                            did = true;
                        }
                    }
                    catch { }

                    // ③ 兜底：GetLuck 还能拿到（luckData 可读但上面漏了的情况）
                    try
                    {
                        var l = u.GetLuck(luckId);
                        if (l != null) { u.DestroyLuck(l); did = true; }
                    }
                    catch { }

                    // ④ 僵尸对象：面板层已经没这枚、allLuck 里仍有 null-data
                    //    ⇒ 视为本枚的残骸，清掉一个（每轮一个，靠循环收敛）
                    if (!did && hasNullData && !TeamTool.InAddLuck(u, luckId))
                    {
                        try
                        {
                            var all = ((dynamic)u).allLuck;
                            int n = (int)((dynamic)all).Count;
                            for (int i = 0; i < n; i++)
                            {
                                object el = null;
                                try { el = ((dynamic)all)[i]; } catch { continue; }
                                if (el == null) continue;
                                object ld = null;
                                try { ld = ((dynamic)el).luckData; } catch { }
                                if (ld != null) continue;          // 只动 null-data
                                try { u.DestroyLuck((WorldUnitLuckBase)el); did = true; } catch { }
                                break;                             // 一轮一个，重新扫描
                            }
                        }
                        catch { }
                    }

                    if (!did) break;
                }

                // ★ 复核三层全干净才算成功（旧版只查两层 ⇒ 僵尸对象漏网）★
                bool runtimeOk = false, panelOk = false, allLuckOk = true;
                try { runtimeOk = u.GetLuck(luckId) != null; } catch { }
                panelOk = TeamTool.InAddLuck(u, luckId);
                try
                {
                    var all = ((dynamic)u).allLuck;
                    int n = (int)((dynamic)all).Count;
                    for (int i = 0; i < n; i++)
                    {
                        object el = null;
                        try { el = ((dynamic)all)[i]; } catch { continue; }
                        if (el == null) continue;
                        object ld = null;
                        try { ld = ((dynamic)el).luckData; } catch { }
                        if (ld == null) { allLuckOk = false; continue; }   // 僵尸
                        int id = int.MinValue;
                        try { id = (int)((dynamic)ld).id; } catch { }
                        if (id == luckId) { allLuckOk = false; break; }
                    }
                }
                catch { }

                if (!allLuckOk && !panelOk && !runtimeOk)
                {
                    // allLuck 里还有读不出 id 的僵尸，但另两层已干净 —— 如实报告
                    Out("[清运] ⚠ " + SafeName(u) + " 的 allLuck 仍残留 luckData=null 对象"
                        + "（读不到 id，无法确认是否本枚；引擎条件段可能仍看得见）");
                    return false;
                }
                return !runtimeOk && !panelOk && allLuckOk;
            }
            catch (Exception e) { Out("DelLuck(" + luckId + ") 异常：" + e.Message); }
            return false;
        }
    }
}
#endif
