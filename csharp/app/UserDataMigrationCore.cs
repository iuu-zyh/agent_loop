/// <summary>
/// 用户数据根迁移的**判据核**（纯 <see cref="System.IO"/>，零 Unity、零全局状态，可单测）。
///
/// 为什么需要它：创意工坊整包更新会同步 MOD 目录，而 `config.json` / `prompts/` 等用户数据
/// 原本就住在 `&lt;Mod根&gt;\ModAssets\` 里 —— 更新一次，API Key、改过的提示词、自建人设全被盖掉。
/// 数据根外置到 `%USERPROFILE%\.agent_loop` 之后，工坊只动 MOD 目录，碰不到用户数据。
///
/// 铁律：**只补缺失，绝不覆盖**。迁移会重复执行（每次启动都会走一遍），所以必须幂等；
/// 同名文件一律以用户目录那份为准 —— 一旦反过来，用户改过的人设会在一次更新后悄悄回退。
/// </summary>
using System;
using System.Collections.Generic;
using System.IO;

namespace AgentLoopBridge
{
    /// <summary>一次迁移的结果（只用于日志与单测，不参与业务判定）。</summary>
    internal sealed class UserDataMigrationReport
    {
        internal readonly List<string> Copied = new List<string>();
        internal readonly List<string> Failed = new List<string>();

        internal bool Changed { get { return Copied.Count > 0; } }
    }

    internal static class UserDataMigrationCore
    {
        /// <summary>用户数据目录名（与 <c>.sessions</c> 并列，见 README）。</summary>
        internal const string UserDataDirName = ".agent_loop";

        /// <summary>除 config / prompts 外，需要跟着搬的用户状态文件。</summary>
        internal static readonly string[] StateFiles =
        {
            "window_layout.json",
            "notice_seen.json",
            "drama_ledger.jsonl",
        };

        /// <summary>`%USERPROFILE%` → `...\.agent_loop`；取不到用户目录返回 null（调用方回退旧布局）。</summary>
        internal static string DefaultUserRoot(string userProfile)
        {
            if (string.IsNullOrEmpty(userProfile)) return null;
            try { return Path.Combine(userProfile, UserDataDirName); }
            catch { return null; }
        }

        /// <summary>
        /// 把旧数据根里的用户数据迁到新根。
        ///
        /// 行为：
        ///   · config.json、三个状态文件：目标不存在才复制
        ///   · prompts/：目标不存在就整棵复制；已存在则逐文件补缺失（新内置人设能进来，
        ///     用户改过的同名文件不动）
        ///   · 单文件失败只记进 <see cref="UserDataMigrationReport.Failed"/>，不抛
        /// </summary>
        internal static UserDataMigrationReport Migrate(string legacyRoot, string userRoot)
        {
            var report = new UserDataMigrationReport();
            if (string.IsNullOrEmpty(legacyRoot) || string.IsNullOrEmpty(userRoot)) return report;
            if (SamePath(legacyRoot, userRoot)) return report;

            try { Directory.CreateDirectory(userRoot); }
            catch (Exception e)
            {
                report.Failed.Add("create " + userRoot + "：" + e.Message);
                return report;
            }

            CopyFileIfMissing(Path.Combine(legacyRoot, "config.json"),
                              Path.Combine(userRoot, "config.json"), report);
            CopyTreeIfMissing(Path.Combine(legacyRoot, "prompts"),
                              Path.Combine(userRoot, "prompts"), report);
            for (int i = 0; i < StateFiles.Length; i++)
            {
                string name = StateFiles[i];
                CopyFileIfMissing(Path.Combine(legacyRoot, name),
                                  Path.Combine(userRoot, name), report);
            }
            return report;
        }

        /// <summary>目标已存在即跳过 —— 这一行就是"绝不覆盖"的判据，删掉它等于让用户改动静默回退。</summary>
        internal static void CopyFileIfMissing(string src, string dst, UserDataMigrationReport report)
        {
            try
            {
                if (!File.Exists(src)) return;
                if (File.Exists(dst)) return;
                string dir = Path.GetDirectoryName(dst);
                if (!string.IsNullOrEmpty(dir)) Directory.CreateDirectory(dir);
                File.Copy(src, dst, false);
                report.Copied.Add(dst);
            }
            catch (Exception e)
            {
                report.Failed.Add(dst + "：" + e.Message);
            }
        }

        /// <summary>递归补齐 <paramref name="srcDir"/> 下缺失的文件；已存在的一个都不碰。</summary>
        internal static void CopyTreeIfMissing(string srcDir, string dstDir, UserDataMigrationReport report)
        {
            try
            {
                if (!Directory.Exists(srcDir)) return;
                string[] files = Directory.GetFiles(srcDir, "*", SearchOption.AllDirectories);
                for (int i = 0; i < files.Length; i++)
                {
                    string src = files[i];
                    // 运行时不可能有这些，但迁移不该把缓存/字节码带进用户目录
                    if (src.IndexOf("__pycache__", StringComparison.OrdinalIgnoreCase) >= 0) continue;
                    if (src.EndsWith(".pyc", StringComparison.OrdinalIgnoreCase)) continue;
                    string rel = src.Substring(srcDir.Length)
                                    .TrimStart(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
                    CopyFileIfMissing(src, Path.Combine(dstDir, rel), report);
                }
            }
            catch (Exception e)
            {
                report.Failed.Add(dstDir + "：" + e.Message);
            }
        }

        /// <summary>同一个目录不做迁移（否则读自己、写自己，日志还会刷屏）。</summary>
        internal static bool SamePath(string a, string b)
        {
            try
            {
                string na = Path.GetFullPath(a).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
                string nb = Path.GetFullPath(b).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
                return string.Equals(na, nb, StringComparison.OrdinalIgnoreCase);
            }
            catch { return false; }
        }
    }
}
