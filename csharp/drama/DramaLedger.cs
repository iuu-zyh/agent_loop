/// <summary>
/// DramaLedger —— 「剧情流水账」的**接线层**（内核是 `DramaLedgerCore`，纯逻辑可单测）。
///
/// ## 一句话
/// 每出现一页剧情 / 一次选项，**同时**做两件事：进段（原有行为，一字不改）+ 进账本（新增）。
/// 段负责「该不该投给模型」，账本负责「发生过什么、事后能回看」。
///
/// ## 为什么必须流式抄一遍，而不是"打完再读段"（真机证据）
/// 段是**有损**的视图：收口后只活 600 秒、只留 4 段、判不出归属就整页丢弃。
/// 10-24 副本 4901（16 分钟）的实测时间线：
/// ```
/// 18:31:24  ★直塞完成★ + BattleStart          ← 开窗
/// 18:31:31 ~ 18:36:17  剧情 24004…1450102      12 条
/// 18:36:44  段收口 ⇒ 起 600s 倒计时
/// 18:46:44  可投段过期未投出 ⇒ 丢弃（4TTqqu，共 12 条）  ← 内容消失
/// 18:47:17  BattleEndFront                     ← 关窗（比内容消失晚 33 秒）
/// ```
/// 「打完再读段」在这场副本里**必然拿到空**，而且日志上一片平静。
/// 账本没有 TTL，所以关窗那一刻内容是齐的。
///
/// ## 落盘（内存为权威，盘只为回看/排障）
///   · **读永远走内存**：抽取发生在 `BattleExit` 那一拍，不能让磁盘抖动/invoke 失败进入判定路径。
///   · 盘上那份是**镜像**：批量追加（攒够 `FlushEveryEntries` 条或 `FlushEverySeconds` 秒），
///     写失败只打一行日志、**绝不影响游戏**（与 `UnitIdRegistry` / `NoticeStore` 同纪律）。
///   · 崩溃最多丢"最后一批"（约 10 秒 / 20 条）—— 换来的是一帧都不掉。
///   · 按存档隔离（文件里记 `world` = 玩家 unitID）：对不上整份作废。
///     理由同 `UnitIdRegistry`：`unitID` 换档即失效，混起来最坏是**静默错人**。
///
/// ## 边界（刻意的）
///   · **账本绝不反向喂给段**：段的「判不出 ⇒ 丢弃」「换人 ⇒ 收口」是它正确性的骨架，
///     一旦让它从账本里捞别人的内容，那些判据就废了（会把别人的话投给当前对话对象，出过事故）。
///   · 不记玩家输入的聊天内容、不记工具结果 —— 只记**游戏剧情 UI 上的原文**。
///
/// 线程：全部主线程。
/// </summary>
using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
using Newtonsoft.Json.Linq;

namespace AgentLoopBridge
{
    internal static class DramaLedger
    {
        private const string Tag = "[Ledger] ";
        private const string FileName = "drama_ledger.jsonl";

        /// <summary>攒够这么多条就落盘。</summary>
        private const int FlushEveryEntries = 20;
        /// <summary>或者距上次落盘超过这么多秒就落盘（战斗中每页都写会掉帧）。</summary>
        private const float FlushEverySeconds = 10f;
        /// <summary>单文件上限（字节）。超了滚动到 `.1`，只保留最近 2 份 —— 够翻几天。</summary>
        private const long MaxFileBytes = 8L * 1024 * 1024;
        /// <summary>保留的历史文件数（不含当前那份）。</summary>
        private const int KeepArchives = 2;

        private static readonly DramaLedgerCore Core = new DramaLedgerCore();

        private static bool _loaded;
        private static string _world = "";
        private static bool _foreign;                 // 盘上那份属于别的存档 ⇒ 不覆盖
        private static readonly List<DramaLedgerEntry> _pending = new List<DramaLedgerEntry>();
        private static float _lastFlushAt = -1f;
        private static long _fileBytes = -1;

        // ==================================================================== 对外 API

        /// <summary>记一页（`DramaSegmentRecorder.NotePage` 里与段并行调）。
        /// **判不出归属也照记**（`unitId` 空串照存）—— 这正是它比段多出来的覆盖：
        /// 真机上「进入副本」那一页（`610911`，选项「深入探索」）就是被段以"没有归属"丢掉的。</summary>
        internal static void NotePage(string unitId, int dramaId, string speaker, string name, string text)
        {
            try
            {
                EnsureLoaded();
                float now = Now();
                bool priv = IsPrivate(dramaId);
                DramaLedgerEntry e = Core.Add("page", unitId, speaker, name, text, dramaId, priv, now);
                if (e != null) Queue(e, now);
            }
            catch (Exception ex) { SafeLog("记页异常: " + ex.Message); }
        }

        /// <summary>记一枚玩家点的选项。同样**不看归属**（没有活动段的选择也要留痕）。</summary>
        internal static void NoteOption(string unitId, int dramaId, string text, string name)
        {
            try
            {
                EnsureLoaded();
                float now = Now();
                bool priv = IsPrivate(dramaId);
                DramaLedgerEntry e = Core.Add("option", unitId, "choice", name, text, dramaId, priv, now);
                if (e != null) Queue(e, now);
            }
            catch (Exception ex) { SafeLog("记选项异常: " + ex.Message); }
        }

        /// <summary>
        /// 插一根事件钉子。**唯一合法的事件名见下方常量**。
        ///
        /// 为什么需要事件：从账本里抽「某场战斗的剧情」需要一个**边界**。用固定时间窗猜
        /// （"往前 60 秒"）在进副本这一幕上就判错（`610911` 比队友进场早 95 秒）。
        /// 让事件自己插钉子，边界由**发生的事实**给出，不靠猜时长。
        /// </summary>
        internal const string EventBattleBegin = DramaLedgerCore.EventBattleBegin;
        internal const string EventBattleEnd = DramaLedgerCore.EventBattleEnd;

        internal static void Mark(string eventName, string unitId)
        {
            try
            {
                EnsureLoaded();
                float now = Now();
                DramaLedgerEntry e = Core.Mark(eventName, unitId, now);
                if (e != null)
                {
                    Queue(e, now);
                    SafeLog("事件钉子 " + eventName + (string.IsNullOrEmpty(unitId) ? "" : "（" + unitId + "）")
                            + "｜" + Core.Brief());
                }
            }
            catch (Exception ex) { SafeLog("插事件钉子异常: " + ex.Message); }
        }

        /// <summary>
        /// 开战斗窗（`★直塞完成★` 调）。**幂等**：已经开着就返回 false 不重复插钉子。
        /// 返回 true 表示"这次真的开了"（日志据此只说一次）。
        /// </summary>
        internal static bool BeginWindow(string unitId)
        {
            try
            {
                EnsureLoaded();
                float now = Now();
                if (!Core.BeginWindow(unitId, now)) return false;
                Queue(Core.Last, now);      // 刚插的那根
                SafeLog("★战斗窗开★ 起点已插钉子" + (string.IsNullOrEmpty(unitId) ? "" : "（" + unitId + "）")
                        + "｜" + Core.Brief());
                return true;
            }
            catch (Exception ex) { SafeLog("开战斗窗异常: " + ex.Message); return false; }
        }

        /// <summary>
        /// 关战斗窗并插 `end` 钉子（`BattleExit` 调）。**没开过就什么都不做** ——
        /// 单人战斗也会走到 `BattleExit`，那时插一根孤儿 `end` 会把真正那场战斗的配对堵死
        /// （抽取是"从最后一个 end 往前找 begin"，孤儿 end 正好卡在中间）。
        /// 返回 true 表示"这次真的关了"（调用方据此决定要不要去取区间）。
        /// </summary>
        internal static bool EndWindow(string unitId)
        {
            try
            {
                EnsureLoaded();
                float now = Now();
                if (!Core.EndWindow(unitId, now)) return false;
                Queue(Core.Last, now);
                SafeLog("★战斗窗关★ 终点已插钉子｜" + Core.Brief());
                return true;
            }
            catch (Exception ex) { SafeLog("关战斗窗异常: " + ex.Message); return false; }
        }

        /// <summary>
        /// 取「最近一对 begin…end 之间」的内容（战斗履历用）。调用方**通常刚刚插完 end**。
        /// 读的是内存（权威），不等落盘。
        /// </summary>
        internal static DramaLedgerWindow TakeBattleWindow()
        {
            try
            {
                EnsureLoaded();
                return Core.TakeWindow(EventBattleBegin, EventBattleEnd, false);
            }
            catch (Exception ex)
            {
                SafeLog("取战斗区间异常: " + ex.Message);
                return new DramaLedgerWindow();
            }
        }

        /// <summary>换世界：整账作废并落一份空的（`unitID` 换档即失效，理由见文件头）。</summary>
        internal static void OnWorldChanged()
        {
            try
            {
                Core.Reset();
                _pending.Clear();
                _loaded = false;         // 下次用到时按新存档重新加载
                _world = "";
                _foreign = false;
                _fileBytes = -1;
                _lastFlushAt = -1f;
            }
            catch (Exception e) { SafeLog("换世界清理失败: " + e.Message); }
        }

        /// <summary>游戏存档那一刻强制落盘（与 `UnitIdRegistry.Flush` 同一个钩子、同一个语义）。
        /// 玩家不存档就退游戏 ⇒ 这份账本也就是"这一局的草稿"，与对话增量同寿命。</summary>
        internal static void Flush()
        {
            try { EnsureLoaded(); FlushNow("存档"); }
            catch (Exception e) { SafeLog("存档落盘失败: " + e.Message); }
        }

        /// <summary>诊断一行（进世界/排障时打）。</summary>
        internal static string Report()
        {
            try
            {
                EnsureLoaded();
                string path = FilePath();
                return Core.Brief() + " ｜ world=" + (_world.Length > 0 ? _world : "(未进档)")
                     + (_pending.Count > 0 ? " ｜ 待落盘 " + _pending.Count + " 条" : "")
                     + (_foreign ? " ｜ ★盘上那份属于别的存档（下次存档时覆盖）" : "")
                     + " ｜ 文件=" + (path ?? "(无法解析 Mod 根)");
            }
            catch { return Core.Brief(); }
        }

        // ==================================================================== 落盘

        private static void Queue(DramaLedgerEntry e, float now)
        {
            if (e == null) return;
            _pending.Add(e);
            bool byCount = _pending.Count >= FlushEveryEntries;
            bool byTime = _lastFlushAt >= 0f && now - _lastFlushAt >= FlushEverySeconds;
            if (byCount || byTime) FlushNow(byCount ? "攒满" + FlushEveryEntries + "条" : "超过" + FlushEverySeconds + "s");
        }

        /// <summary>
        /// 追加落盘。**只追加**（不重写整个文件）：重写要序列化全部内容，越长越卡，
        /// 而战斗中每一页都来一次。
        /// </summary>
        private static void FlushNow(string why)
        {
            if (_pending.Count == 0) return;
            string path = FilePath();
            if (string.IsNullOrEmpty(path)) { _pending.Clear(); return; }   // 解析不到根：降级纯内存

            string world = WorldNow();
            if (_foreign && world.Length > 0 && world != _world)
            {
                // 盘上那份属于别的存档 ⇒ 整份换成本档的（不追加到别人后面）
                try { if (File.Exists(path)) File.Delete(path); } catch { }
                _fileBytes = -1;
            }
            if (world.Length > 0) { _world = world; _foreign = false; }

            try
            {
                if (_fileBytes < 0)
                {
                    try { _fileBytes = File.Exists(path) ? new FileInfo(path).Length : 0; } catch { _fileBytes = 0; }
                }
                RotateIfNeeded(path);

                var sb = new StringBuilder(_pending.Count * 96);
                for (int i = 0; i < _pending.Count; i++) sb.Append(Line(_pending[i])).Append('\n');
                string chunk = sb.ToString();
                File.AppendAllText(path, chunk, new UTF8Encoding(false));
                _fileBytes += Encoding.UTF8.GetByteCount(chunk);
                _pending.Clear();
                _lastFlushAt = Now();
            }
            catch (Exception ex)
            {
                // 写失败：**留着**待写内容下次再试；实在写不了就丢掉（内存仍是权威，游戏不受影响）
                SafeLog("落盘失败（" + why + "，本次不影响游戏，下次再试）：" + ex.Message);
                if (_pending.Count > DramaLedgerCore.MaxEntries) _pending.Clear();
            }
        }

        /// <summary>滚动：到上限就把当前文件挪成 `.1`，删掉最老的归档。失败一律忽略（只是回看用）。</summary>
        private static void RotateIfNeeded(string path)
        {
            try
            {
                if (_fileBytes < MaxFileBytes) return;
                if (File.Exists(path))
                {
                    for (int i = KeepArchives; i >= 2; i--)
                    {
                        string older = path + "." + i, prev = path + "." + (i - 1);
                        try { if (File.Exists(older)) File.Delete(older); } catch { }
                        try { if (File.Exists(prev)) File.Move(prev, older); } catch { }
                    }
                    try { File.Move(path, path + ".1"); } catch { try { File.Delete(path); } catch { } }
                }
                _fileBytes = 0;
                SafeLog("账本文件滚动（上限 " + (MaxFileBytes / 1024 / 1024) + "MB），保留最近 " + (KeepArchives + 1) + " 份");
            }
            catch { _fileBytes = 0; }
        }

        /// <summary>一条 → 一行 JSON。**手写**而不是 `JObject`：战斗中每页都要走，省掉反射与分配。</summary>
        private static string Line(DramaLedgerEntry e)
        {
            var sb = new StringBuilder(128);
            sb.Append('{');
            sb.Append("\"seq\":").Append(e.Seq);
            sb.Append(",\"t\":").Append(e.At.ToString("0.000", System.Globalization.CultureInfo.InvariantCulture));
            sb.Append(",\"kind\":\"").Append(Esc(e.Kind)).Append('"');
            sb.Append(",\"uid\":\"").Append(Esc(e.UnitId)).Append('"');
            sb.Append(",\"who\":\"").Append(Esc(e.Speaker)).Append('"');
            sb.Append(",\"name\":\"").Append(Esc(e.Name)).Append('"');
            sb.Append(",\"did\":").Append(e.DramaId);
            if (e.Private) sb.Append(",\"priv\":1");
            sb.Append(",\"text\":\"").Append(Esc(e.Text)).Append('"');
            sb.Append('}');
            return sb.ToString();
        }

        private static string Esc(string s)
        {
            if (string.IsNullOrEmpty(s)) return "";
            var sb = new StringBuilder(s.Length + 8);
            for (int i = 0; i < s.Length; i++)
            {
                char c = s[i];
                switch (c)
                {
                    case '"': sb.Append("\\\""); break;
                    case '\\': sb.Append("\\\\"); break;
                    case '\n': sb.Append("\\n"); break;
                    case '\r': sb.Append("\\r"); break;
                    case '\t': sb.Append("\\t"); break;
                    default:
                        if (c < 0x20) sb.Append("\\u").Append(((int)c).ToString("x4"));
                        else sb.Append(c);
                        break;
                }
            }
            return sb.ToString();
        }

        // ==================================================================== 加载

        /// <summary>
        /// 首次用到时把盘上那份读进来（只为"上次会话的剧情还能翻"，**不参与本局判定**）。
        /// 存档对不上 ⇒ 整份作废并标记 `_foreign`（不覆盖别人的文件）。
        /// 读失败一律当空账（绝不影响游戏）。
        /// </summary>
        private static void EnsureLoaded()
        {
            if (_loaded) return;
            _loaded = true;
            string path = FilePath();
            if (string.IsNullOrEmpty(path)) return;
            try
            {
                if (!File.Exists(path)) return;
                string world = WorldNow();
                int loaded = 0, skipped = 0;
                using (var fr = new StreamReader(path, Encoding.UTF8))
                {
                    string ln;
                    while ((ln = fr.ReadLine()) != null)
                    {
                        if (ln.Length == 0) continue;
                        JObject o;
                        try { o = JObject.Parse(ln); } catch { continue; }
                        string fw = (string)o["world"];
                        if (fw != null)
                        {
                            // 首行式头（旧版本兼容位，目前不写）；对不上就停
                            if (world.Length > 0 && fw != world) { skipped = -1; break; }
                            continue;
                        }
                        DramaLedgerEntry e = new DramaLedgerEntry();
                        e.Seq = (int?)o["seq"] ?? 0;
                        e.At = (float?)o["t"] ?? 0f;
                        e.Kind = (string)o["kind"] ?? "page";
                        e.UnitId = (string)o["uid"] ?? "";
                        e.Speaker = (string)o["who"] ?? "";
                        e.Name = (string)o["name"] ?? "";
                        e.DramaId = (int?)o["did"] ?? 0;
                        e.Private = ((int?)o["priv"] ?? 0) != 0;
                        e.Text = (string)o["text"] ?? "";
                        if (e.Text.Length == 0) continue;
                        Core.Restore(e);
                        loaded++;
                    }
                }
                if (skipped < 0) { Core.Reset(); _foreign = true; return; }
                Core.TrimToCap();
                _world = world;
                SafeLog("已加载盘上账本 " + loaded + " 条（world=" + (_world.Length > 0 ? _world : "(未进档)") + "）");
            }
            catch (Exception e)
            {
                Core.Reset();
                SafeLog("加载盘上账本失败（已当空账）：" + e.Message);
            }
        }

        // ==================================================================== 小工具

        private static string FilePath()
        {
            try
            {
                string d = ModPaths.DataRoot;
                return string.IsNullOrEmpty(d) ? null : Path.Combine(d, FileName);
            }
            catch { return null; }
        }

        private static string WorldNow()
        {
            try { return UnitSnapshot.PlayerUnitId() ?? ""; } catch { return ""; }
        }

        /// <summary>是不是我方私有窗（`ModIds.Mod + 0..999`）。与段用同一判据 —— 但**照记**，
        /// 私有标记只是给读者过滤用的（我们自己弹的窗不该出现在"剧情见闻"里）。</summary>
        private static bool IsPrivate(int dramaId)
        {
            return dramaId >= ModIds.Mod && dramaId <= ModIds.Mod + 999;
        }

        private static float Now()
        {
            try { return UnityEngine.Time.unscaledTime; } catch { return 0f; }
        }

        private static void SafeLog(string s)
        {
            try { ModMain.P(Tag + s); } catch { }
        }
    }
}
