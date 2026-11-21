// WorldFrameOutbox（「存档身份帧」发送队列）单元测试。
//
// 被测的是什么：world_hello / load_happened / save_happened 三条帧"现在该发还是该等"，
// 以及连接恢复后的补投顺序与去重。
//
// 为什么值得单独钉：这层的失败全**没有界面** —— 帧丢了就是"NPC 忘了"或"跨档串号"，
// 而日志里那行"已发送"照样会打（旧实现的 `SendRequest` 在未连接时直接回 ok:false
// 且不排队，回调还是 `_ => { }`）。三个最要命的形态各有专门用例：
//   · 未连接把帧丢掉 → 整局没有存档身份；
//   · 补投顺序反了 → "先读档后存档"变成"先存档后读档"（刚固化的内容又被丢）；
//   · 补投时没看连接 → 把帧原地烧掉，之后再也不会补。
//
// ⚠ 诚实边界：这里只测队列。**真正的调用时机**（哪条回调里 Send/Flush）是接线问题，
//   由 `tests/test_world_identity_contract.py`（Python 侧源码契约）+ 实机验收兜。

using System;
using System.Collections.Generic;
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

        private static void Eq<T>(string name, T got, T want) =>
            Check(name, EqualityComparer<T>.Default.Equals(got, want),
                  "实际=" + got + " 期望=" + want);

        /// <summary>测试替身：可切换的连接状态 + 记录所有"发出去"的帧。</summary>
        private sealed class Rig
        {
            internal bool Connected;
            internal readonly List<string> Sent = new List<string>();
            internal readonly List<string> Logs = new List<string>();
            internal WorldFrameOutbox<string> Box;

            internal Rig(int maxPending = 8)
            {
                Box = new WorldFrameOutbox<string>(
                    () => Connected,
                    (m, p) => Sent.Add(m + ":" + p),
                    s => Logs.Add(s),
                    maxPending);
            }

            internal string SentJoined { get { return string.Join("|", Sent); } }
        }

        private static int Main()
        {
            Console.WriteLine("=== WorldFrameOutbox（存档身份帧队列）单元测试 ===");

            // ---------- ① 连着就直发 ----------
            {
                var r = new Rig();
                r.Connected = true;
                bool direct = r.Box.Send("world_hello", "A");
                Eq("① 已连接 → Send 返回 true（直发）", direct, true);
                Eq("① 已连接 → 立刻发出去", r.SentJoined, "world_hello:A");
                Eq("① 已连接 → 不进队列", r.Box.Pending, 0);
            }

            // ---------- ② 未连接就排队（不许丢） ----------
            {
                var r = new Rig();
                r.Connected = false;
                bool direct = r.Box.Send("load_happened", "A");
                Eq("② 未连接 → Send 返回 false（已排队）", direct, false);
                Eq("② 未连接 → 没有发出去", r.SentJoined, "");
                Eq("② 未连接 → 队列里有 1 条", r.Box.Pending, 1);
            }

            // ---------- ③ 补投：按原顺序、只投一次 ----------
            {
                var r = new Rig();
                r.Connected = false;
                r.Box.Send("load_happened", "A");
                r.Box.Send("save_happened", "A");
                r.Box.Send("world_hello", "A");
                Eq("③ 三条都在队列里", r.Box.Pending, 3);

                r.Connected = true;
                Eq("③ 补投条数 = 3", r.Box.Flush(), 3);
                Eq("③ 顺序 = 入队顺序（先读档后存档，反了会丢内容）",
                   r.SentJoined, "load_happened:A|save_happened:A|world_hello:A");
                Eq("③ 投完即清", r.Box.Pending, 0);
                Eq("③ 再 Flush 不重复投", r.Box.Flush(), 0);
                Eq("③ 重复 Flush 后已发集合不变", r.SentJoined,
                   "load_happened:A|save_happened:A|world_hello:A");
            }

            // ---------- ④ 仍未连接时补投：一条都不许烧掉 ----------
            {
                var r = new Rig();
                r.Connected = false;
                r.Box.Send("save_happened", "A");
                Eq("④ 未连接 → Flush 投出 0 条", r.Box.Flush(), 0);
                Eq("④ 未连接 → 帧原样留在队列（等下一次连接）", r.Box.Pending, 1);
                Eq("④ 未连接 → 真的没发", r.SentJoined, "");

                r.Connected = true;
                Eq("④ 之后连上 → 照样能补投", r.Box.Flush(), 1);
                Eq("④ 之后连上 → 内容不丢", r.SentJoined, "save_happened:A");
            }

            // ---------- ⑤ 积压超限：丢最旧，保最新 ----------
            {
                var r = new Rig(maxPending: 3);
                r.Connected = false;
                r.Box.Send("f1", "1");
                r.Box.Send("f2", "2");
                r.Box.Send("f3", "3");
                Eq("⑤ 正好到上限：一条不丢", r.Box.Pending, 3);
                r.Box.Send("f4", "4");
                Eq("⑤ 超限后仍是 3 条", r.Box.Pending, 3);
                r.Connected = true;
                r.Box.Flush();
                Eq("⑤ 丢的是最旧的 f1、保最新（陈旧身份比丢帧更糟）",
                   r.SentJoined, "f2:2|f3:3|f4:4");
                Check("⑤ 丢帧有日志留痕", r.Logs.Count == 1 && r.Logs[0].Contains("f1"),
                      "logs=" + string.Join(";", r.Logs));
            }

            // ---------- ⑥ 上限为 1 / 0 也别炸（配置夹取：0 会让"丢最旧"去取空队列的头） ----------
            {
                var r = new Rig(maxPending: 1);
                r.Connected = false;
                r.Box.Send("f1", "1");
                r.Box.Send("f2", "2");
                r.Connected = true;
                r.Box.Flush();
                Eq("⑥ 上限 1 → 只留最新那条", r.SentJoined, "f2:2");

                var z = new Rig(maxPending: 0);   // 越界值：必须被夹到 ≥1，否则取头越界
                z.Connected = false;
                z.Box.Send("f1", "1");
                z.Box.Send("f2", "2");
                z.Connected = true;
                z.Box.Flush();
                Eq("⑥ 上限 0 → 夹到 1（不越界、只留最新）", z.SentJoined, "f2:2");
            }

            // ---------- ⑦ 直发抛异常照抛（静默吞掉发送失败 = 丢帧） ----------
            {
                bool threw = false;
                var box = new WorldFrameOutbox<string>(() => true,
                    (m, p) => { throw new InvalidOperationException("socket 炸了"); });
                try { box.Send("save_happened", "A"); }
                catch (InvalidOperationException) { threw = true; }
                Check("⑦ 发送异常向上抛（调用方必须看得见）", threw);
            }

            // ---------- ⑧ 构造参数缺失直接拒（防呆） ----------
            {
                bool t1 = false, t2 = false;
                try { new WorldFrameOutbox<string>(null, (m, p) => { }); }
                catch (ArgumentNullException) { t1 = true; }
                try { new WorldFrameOutbox<string>(() => true, null); }
                catch (ArgumentNullException) { t2 = true; }
                Check("⑧ connected=null 直接拒", t1);
                Check("⑧ send=null 直接拒", t2);
            }

            Console.WriteLine();
            if (_fails.Count == 0)
            {
                Console.WriteLine("全部通过：" + _pass + " 项断言");
                return 0;
            }
            Console.WriteLine("失败 " + _fails.Count + " 项（通过 " + _pass + " 项）：");
            foreach (var f in _fails) Console.WriteLine("  - " + f);
            return 1;
        }
    }
}
