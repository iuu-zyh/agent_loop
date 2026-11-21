/// <summary>
/// 须知面板的**水位落盘**（`notice_seen.json`）与两个诊断哨兵。
///
/// ── 文件在哪、为什么 ────────────────────────────────────────────────────
/// `<数据根>/notice_seen.json`（发行版 = `ModAssets\notice_seen.json`，与 `config.json`、
/// `logs\` 同层）。**刻意不按存档隔离**：须知是"这台机器装了这个 mod、这个版本"的信息，
/// 不是某个存档的剧情进度 —— 换存档不该让人重看一遍"怎么填 Key"。
///
/// ── 写法 ────────────────────────────────────────────────────────────────
/// 沿用 `UnitIdRegistry` 那套**临时文件 + `File.Replace`** 的原子写（同目录 Move 仍原子），
/// 避免游戏崩溃/断电时留下半截 JSON。全程 try/catch：写失败**只打日志**，本次照常显示，
/// 下次进游戏会再弹一次 —— 这个失败模式比"静默不再弹"好得多。
///
/// ── 两个诊断哨兵（与仓库既有 `_diag_*` 同族，增删即时生效、不改代码） ─────────
///   `_diag_no_notice.txt`     → 永不自动弹（觉得烦就放一个；手动入口照常）
///   `_diag_notice_always.txt` → 每次进世界都弹（调版面/截图用）
///
/// ── 缓存的代价值得说一句 ────────────────────────────────────────────────
/// `EnsureLoaded` 只读一次盘（`_loaded` 位）。但**诊断哨兵每 120 帧复查一次** —— 那个复查
/// 只 `File.Exists` 两个小文件（与 `DiagSwitches` 同节拍、同代价），不重读 json；
/// 而 `ShouldAutoShow` 每帧都会被问，它只比较已缓存的两个 int。
/// </summary>
using System;
using System.IO;
using Newtonsoft.Json.Linq;
using UnityEngine;

namespace AgentLoopBridge
{
    internal static class NoticeStore
    {
        private static bool _loaded;
        private static NoticeSeen _seen = NoticeSeen.None;

        // ---- 诊断哨兵（每 120 帧复查，与 DiagSwitches 同节拍）----
        private const int DiagEveryFrames = 120;
        private static int _diagFrame = -1;
        private static bool _diagNoNotice;
        private static bool _diagAlways;

        /// <summary>水位文件路径；解析不到数据根返回 null（调用方降级：当成"没看过"）。</summary>
        internal static string FilePath()
        {
            try
            {
                string d = ModPaths.DataRoot;
                return string.IsNullOrEmpty(d) ? null : Path.Combine(d, NoticeCore.FileName);
            }
            catch { return null; }
        }

        /// <summary>当前水位（首次调用读盘，之后全走内存）。读盘失败 = "没看过"（宁可多弹一次）。</summary>
        internal static NoticeSeen Seen
        {
            get { EnsureLoaded(); return _seen; }
        }

        private static void EnsureLoaded()
        {
            if (_loaded) return;
            _loaded = true;                 // 先置位：加载途中绝不重入
            string path = FilePath();
            if (string.IsNullOrEmpty(path)) return;
            try
            {
                if (!File.Exists(path)) return;         // 首次安装：_seen 保持 None
                string txt = File.ReadAllText(path);
                if (string.IsNullOrWhiteSpace(txt)) return;
                var o = JObject.Parse(txt);
                int seq = 0;
                try { seq = o["seq"] != null ? o["seq"].Value<int>() : 0; } catch { }
                if (seq < 0) seq = 0;
                _seen = new NoticeSeen
                {
                    Seq = seq,
                    Version = (string)o["version"] ?? "",
                    At = (string)o["at"] ?? "",
                    HasFile = true,
                };
                ModMain.P("[Notice] 已读水位：seq=" + _seen.Seq
                          + " version=" + (_seen.Version.Length > 0 ? _seen.Version : "?")
                          + "（当前最新 " + NoticeContentCore.MaxSeq + " / "
                          + NoticeContentCore.LatestVersion + "）");
            }
            catch (Exception e)
            {
                // 文件坏了：当没看过（会再弹一次）。比"静默不再弹"安全 —— 公告不该因为一次写坏就永远消失。
                ModMain.P("[Notice] 水位文件读取失败，按「没看过」处理（下次会自动弹一次）：" + e.Message);
                _seen = NoticeSeen.None;
            }
        }

        /// <summary>
        /// 推进水位（**调用点只有一处**：面板真正显示出来的时候，见 `NoticePanel.Show`）。
        /// 只问"有没有前进"，没前进就一个字节都不写。
        /// 返回是否真的写了盘（诊断/日志用）。
        /// </summary>
        internal static bool MarkSeen()
        {
            EnsureLoaded();
            int max = NoticeContentCore.MaxSeq;
            if (!NoticeCore.NeedsWrite(_seen.Seq, max)) return false;
            int next = NoticeCore.NextSeen(_seen.Seq, max);
            string ver = NoticeContentCore.LatestVersion;
            string at = DateTime.Now.ToString("yyyy-MM-ddTHH:mm:sszzz");
            if (!Write(next, ver, at))
                return false;            // 盘没写成：内存**不动** —— 下次进游戏会再弹一次（宁可多弹）
            _seen = new NoticeSeen { Seq = next, Version = ver, At = at, HasFile = true };
            ModMain.P("[Notice] 水位已前进 → seq=" + next + "（version=" + ver + "）");
            return true;
        }

        private static bool Write(int seq, string version, string at)
        {
            string path = FilePath();
            if (string.IsNullOrEmpty(path))
            {
                ModMain.P("[Notice] 水位未落盘：数据根解析不到");
                return false;
            }
            try
            {
                var o = new JObject
                {
                    ["seq"] = seq,
                    ["version"] = version ?? "",
                    ["at"] = at ?? "",
                };
                string tmp = path + ".tmp";
                File.WriteAllText(tmp, o.ToString(Newtonsoft.Json.Formatting.Indented));
                if (File.Exists(path))
                {
                    try { File.Replace(tmp, path, null); }
                    catch { File.Delete(path); File.Move(tmp, path); }
                }
                else File.Move(tmp, path);
                return true;
            }
            catch (Exception e)
            {
                ModMain.P("[Notice] 水位写盘失败（本次仍会显示，下次进游戏再弹一次）：" + e.Message);
                return false;
            }
        }

        // ------------------------------------------------------------------
        // 诊断哨兵
        // ------------------------------------------------------------------

        /// <summary>诊断：存在 `_diag_no_notice.txt` ⇒ 永不自动弹（手动入口不受影响）。</summary>
        internal static bool NoNoticeFile { get { TickDiag(); return _diagNoNotice; } }

        /// <summary>诊断：存在 `_diag_notice_always.txt` ⇒ 每次进世界都弹（调版面/截图用）。</summary>
        internal static bool AlwaysFile { get { TickDiag(); return _diagAlways; } }

        private static void TickDiag()
        {
            int f = 0;
            try { f = Time.frameCount; } catch { }
            if (f == _diagFrame) return;                       // 同帧多次问只查一次
            if (_diagFrame >= 0 && f - _diagFrame < DiagEveryFrames) return;
            _diagFrame = f;
            _diagNoNotice = Exists("_diag_no_notice.txt");
            _diagAlways = Exists("_diag_notice_always.txt");
        }

        private static bool Exists(string name)
        {
            try
            {
                string d = ModPaths.DiagDir;
                if (string.IsNullOrEmpty(d)) return false;
                return File.Exists(Path.Combine(d, name));
            }
            catch { return false; }
        }
    }
}
