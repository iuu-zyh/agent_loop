// UserDataMigrationCore 单测：迁移只补缺失、绝不覆盖。
// 背景：工坊更新会整包同步 MOD 目录，用户数据必须搬到 %USERPROFILE%\.agent_loop；
// 迁移一旦覆盖同名文件，用户改过的人设会在更新后静默回退。

using System;
using System.Collections.Generic;
using System.IO;
using AgentLoopBridge;

namespace AgentLoopBridge.Tests
{
    internal static class Program
    {
        private static int _pass;
        private static readonly List<string> _fails = new List<string>();

        private static void Check(string name, bool ok, string detail = "")
        {
            if (ok) { _pass++; return; }
            _fails.Add(name + (detail.Length > 0 ? "  ｜ " + detail : ""));
            Console.WriteLine("  ✗ " + name + (detail.Length > 0 ? "  ｜ " + detail : ""));
        }

        private static string Read(string p)
        {
            try { return File.ReadAllText(p); } catch { return null; }
        }

        private static void Write(string p, string text)
        {
            string d = Path.GetDirectoryName(p);
            if (!string.IsNullOrEmpty(d)) Directory.CreateDirectory(d);
            File.WriteAllText(p, text);
        }

        private static int Main()
        {
            Console.WriteLine("=== UserDataMigrationCore 单元测试 ===");
            string sandbox = Path.Combine(Path.GetTempPath(), "jp_udm_" + Guid.NewGuid().ToString("N"));
            try
            {
                // -----------------------------------------------------------
                // ① 全新迁移：config / prompts / 三个状态文件都搬过去
                // -----------------------------------------------------------
                string legacy = Path.Combine(sandbox, "ModAssets");
                string user = Path.Combine(sandbox, ".agent_loop");
                Write(Path.Combine(legacy, "config.json"), "KEY=abc");
                Write(Path.Combine(legacy, "prompts", "personas", "default.txt"), "FACTORY");
                Write(Path.Combine(legacy, "prompts", "personas", "郦安.txt"), "NPC");
                Write(Path.Combine(legacy, "prompts", "__pycache__", "junk.pyc"), "JUNK");
                Write(Path.Combine(legacy, "window_layout.json"), "W");
                Write(Path.Combine(legacy, "notice_seen.json"), "N");
                Write(Path.Combine(legacy, "drama_ledger.jsonl"), "L");

                UserDataMigrationReport r1 = UserDataMigrationCore.Migrate(legacy, user);
                Check("全新迁移有复制", r1.Changed, "copied=" + r1.Copied.Count);
                Check("config 已复制", Read(Path.Combine(user, "config.json")) == "KEY=abc");
                Check("嵌套人设已复制", Read(Path.Combine(user, "prompts", "personas", "郦安.txt")) == "NPC");
                Check("状态文件 window 已复制", Read(Path.Combine(user, "window_layout.json")) == "W");
                Check("状态文件 notice 已复制", Read(Path.Combine(user, "notice_seen.json")) == "N");
                Check("状态文件 ledger 已复制", Read(Path.Combine(user, "drama_ledger.jsonl")) == "L");
                Check("字节码不进用户目录", !File.Exists(Path.Combine(user, "prompts", "__pycache__", "junk.pyc")));

                // -----------------------------------------------------------
                // ② 绝不覆盖：用户改过的 config / 同名提示词必须原样保留
                // -----------------------------------------------------------
                Write(Path.Combine(user, "config.json"), "KEY=user");
                Write(Path.Combine(user, "prompts", "personas", "default.txt"), "USER_EDIT");
                Write(Path.Combine(legacy, "config.json"), "KEY=factory-new");
                Write(Path.Combine(legacy, "prompts", "personas", "default.txt"), "FACTORY_NEW");

                UserDataMigrationCore.Migrate(legacy, user);
                Check("用户 config 不被覆盖", Read(Path.Combine(user, "config.json")) == "KEY=user");
                Check("用户改过的同名提示词不被覆盖",
                      Read(Path.Combine(user, "prompts", "personas", "default.txt")) == "USER_EDIT");

                // -----------------------------------------------------------
                // ③ 只补缺失：新内置人设能进来，用户自建人设不受影响
                // -----------------------------------------------------------
                Write(Path.Combine(legacy, "prompts", "personas", "新内置.txt"), "NEW");
                Write(Path.Combine(user, "prompts", "personas", "我自建.txt"), "MINE");

                UserDataMigrationCore.Migrate(legacy, user);
                Check("新内置人设被补进来",
                      Read(Path.Combine(user, "prompts", "personas", "新内置.txt")) == "NEW");
                Check("用户自建人设原样保留",
                      Read(Path.Combine(user, "prompts", "personas", "我自建.txt")) == "MINE");

                // -----------------------------------------------------------
                // ④ 幂等：再跑一次不应再复制任何文件
                // -----------------------------------------------------------
                UserDataMigrationReport r4 = UserDataMigrationCore.Migrate(legacy, user);
                Check("重复迁移零复制", r4.Copied.Count == 0, "copied=" + r4.Copied.Count);

                // -----------------------------------------------------------
                // ⑤ 同路径 / 旧根不存在：不抛、不复制
                // -----------------------------------------------------------
                UserDataMigrationReport r5 = UserDataMigrationCore.Migrate(legacy, legacy);
                Check("同路径不迁移", r5.Copied.Count == 0);

                UserDataMigrationReport r6 = UserDataMigrationCore.Migrate(
                    Path.Combine(sandbox, "not-exist"), Path.Combine(sandbox, "u2"));
                Check("旧根不存在不抛错", r6.Failed.Count == 0, "failed=" + r6.Failed.Count);
                Check("旧根不存在零复制", r6.Copied.Count == 0);
            }
            finally
            {
                try { Directory.Delete(sandbox, true); } catch { }
            }

            Console.WriteLine();
            Console.WriteLine(_fails.Count == 0
                ? "=== 全部通过：" + _pass + " 项 ==="
                : "=== 失败 " + _fails.Count + " 项 / 通过 " + _pass + " 项 ===");
            return _fails.Count == 0 ? 0 : 1;
        }
    }
}
