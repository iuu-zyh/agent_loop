// 气泡终稿去重（BubbleCommitPolicy）单元测试。
//
// 为什么要有这个工程：这个 bug（同一段正文在屏幕上出现两遍）在实机上表现为"偶发 + 无日志"
// —— C# 的 UI 日志在 Player.log 里，游戏一重启就轮转掉了（09-16 排查时只剩 6 秒的会话日志）。
// 所以判定规则必须能在开发机上跑：这里把真正的策略源码链接进来，喂帧序列，断言"屏幕上几条泡"。
//
// 覆盖三类真实成因（都来自实机/账本证据）：
//   ① 回合已 turn/end、npc_reply 未到的空档重开窗 → 回放画的正文没登记 → npc_reply 再画一遍
//      （09-16 实机截图：两条像素级全等的气泡）
//   ② 模型在工具返回后复述自己的旁白（账本 BOlQu6/赵勤.jsonl 第 1 回合 step3/step4 逐字节
//      相同 116 字）→ 流式门建的"重绘泡"没被撤掉
//   ③ npc_reply 的规范化文本与 step(text) 原文不同（_collect_reply 剥 <think>/剥自名前缀/
//      strip）→ 旧逻辑为这点差异新建了一条泡
//
// ⚠ 模拟器的诚实边界：真实的 ChatWindow 依赖 Unity（GameObject/Text），WSL 里跑不起来。
//   所以这里用一个"记账规则模拟器"复现调用方契约（Skip→撤重绘泡、OverwriteStreaming→写流式泡、
//   OverwriteLastRow→写末行、CreateNew→加一行）。**契约本身由 Python 侧源码契约测试
//   tests/test_bubble_dedup_contract.py 锁在真实 ChatWindow.cs 上** —— 两边合起来才完整：
//   这里测"规则对不对"，那边测"接线有没有被改回去"。

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
                  "实际=" + Show(got) + " 期望=" + Show(want));

        private static string Show<T>(T v) => v == null ? "<null>" : "\"" + v + "\"";

        private static int Main()
        {
            Console.WriteLine("=== BubbleCommitPolicy 单元测试 ===");
            PolicyTable();
            Scenarios();
            Console.WriteLine();
            if (_fails.Count == 0)
            {
                Console.WriteLine("全部通过：" + _pass + " 项断言");
                return 0;
            }
            Console.WriteLine("失败 " + _fails.Count + " 项 / 通过 " + _pass + " 项");
            return 1;
        }

        // ------------------------------------------------------------------
        // 一、判定表（纯函数，直接喂状态）
        // ------------------------------------------------------------------
        private static void PolicyTable()
        {
            Console.WriteLine("[判定表]");
            const string T = "道友过奖了。";

            // 正常回合：step(text) 定稿后，末行活着 → npc_reply 同文本必须跳过
            Eq("同回合同文本+末行活着 → Skip",
               BubbleCommitPolicy.Decide(T, T, 1, 1, false, true), BubbleCommit.Skip);

            // ★ 关键：末行已不在屏幕上时**不能**跳过（否则回复被整条吃掉）
            Eq("同回合同文本但末行已销毁 → CreateNew（宁重勿丢）",
               BubbleCommitPolicy.Decide(T, T, 1, 1, false, false), BubbleCommit.CreateNew);

            // 有流式泡 → 覆盖它
            Eq("有活跃流式泡 → OverwriteStreaming",
               BubbleCommitPolicy.Decide(T, null, 1, -1, true, false), BubbleCommit.OverwriteStreaming);

            // 同回合、文本不同、末行活着 → 覆盖末行（规范化差异，绝不新建）
            Eq("同回合文本不同+末行活着 → OverwriteLastRow",
               BubbleCommitPolicy.Decide("正文", "瞿天心：正文", 1, 1, false, true),
               BubbleCommit.OverwriteLastRow);

            // 跨回合同文本（模型复读）→ 绝不跳过
            Eq("跨回合同文本 → 不 Skip（有流式泡走覆盖）",
               BubbleCommitPolicy.Decide(T, T, 2, 1, true, true), BubbleCommit.OverwriteStreaming);
            Eq("跨回合同文本 → 不 Skip（无流式泡走新建）",
               BubbleCommitPolicy.Decide(T, T, 2, 1, false, true), BubbleCommit.CreateNew);

            // 回合号未知（step 帧没到 / 被丢）→ 无从判断"同一份"，只能画
            Eq("回合号未知(curTurn<=0) → 从不 Skip",
               BubbleCommitPolicy.Decide(T, T, -1, -1, false, false), BubbleCommit.CreateNew);

            // 空正文 → 不参与判重
            Check("空正文 → 不 Skip",
                  BubbleCommitPolicy.Decide("", "", 1, 1, false, true) != BubbleCommit.Skip);
            Check("null 正文 → 不 Skip",
                  BubbleCommitPolicy.Decide(null, null, 1, 1, false, true) != BubbleCommit.Skip);
        }

        // ------------------------------------------------------------------
        // 二、帧序列场景（断言"屏幕上剩几条泡"）
        // ------------------------------------------------------------------
        private static void Scenarios()
        {
            Console.WriteLine("[场景]");
            const string T = "瞿天心微微歪头，目光在风剑流身上停留片刻。";

            // ① 正常单步回合：流式 → step(text) 定稿 → npc_reply
            {
                var w = new WindowSim();
                w.NoteTurn(1);
                w.Delta("瞿天心微微"); w.Delta("歪头，目光在风剑流身上停留片刻。");
                w.Finish(T);        // step(text)
                w.Finish(T);        // npc_reply
                Eq("S1 正常单步回合 → 1 条泡", w.LiveCount(), 1);
                Eq("S1 文本正确", w.LiveTexts()[0], T);
            }

            // ② 窗口 C：turn/end 之后、npc_reply 之前重开窗（回放**包含**本回合）
            //    —— 09-16 实机截图那条
            {
                var w = new WindowSim();
                w.NoteTurn(1);
                w.Delta(T); w.Finish(T);        // 流式 + step(text) 定稿
                w.Reset();                      // ★ 玩家重开窗 → ResetContent（销毁全部行 + 清记账）
                w.Replay(T, 1);                 // ★ 回放（回合已闭合 → 投影含它）→ 画出 T
                w.Finish(T);                    // npc_reply
                Eq("S2 窗口C（重放含本回合）→ 1 条泡", w.LiveCount(), 1);
                Eq("S2 文本正确", w.LiveTexts()[0], T);
            }

            // ③ 窗口 B：重开窗发生在 turn/end **之前**（回放不含本回合）
            //    —— 不能破：终稿必须照常成泡（这正是原先"清零"想保的场景）
            {
                var w = new WindowSim();
                w.NoteTurn(1);
                w.Delta(T); w.Finish(T);
                w.Reset();
                w.Replay("上一回合的正文", 0);   // ★ 未闭合回合不进投影 → 只画到上一回合
                w.Finish(T);                    // npc_reply → 必须画出来
                Eq("S3 窗口B（重放不含本回合）→ 本回合正文恰好 1 条",
                   w.CountText(T), 1);
            }

            // ④ 模型复述：同一步的正文被重流一遍（账本 赵勤 那例）
            {
                var w = new WindowSim();
                w.NoteTurn(1);
                w.Delta(T); w.Finish(T);        // 第一步定稿
                w.Delta(T);                     // ★ 下一步把同一段话又流了一遍 → 新建重绘泡
                Eq("S4 重绘泡确实被建出来了（前置条件）", w.LiveCount(), 2);
                w.Finish(T);                    // 该步 step(text) → 判重命中 → 应撤掉重绘泡
                w.Finish(T);                    // npc_reply
                Eq("S4 复述同段 → 1 条泡", w.LiveCount(), 1);
                Eq("S4 文本正确", w.LiveTexts()[0], T);
            }

            // ⑤ 规范化差异：npc_reply 剥掉自名前缀 → 覆盖，不新建
            {
                var w = new WindowSim();
                w.NoteTurn(1);
                w.Delta("瞿天心：" + T); w.Finish("瞿天心：" + T);   // step(text) 发原文
                w.Finish(T);                                       // npc_reply 剥了前缀
                Eq("S5 规范化差异 → 1 条泡", w.LiveCount(), 1);
                Eq("S5 内容为规范化后的终稿", w.LiveTexts()[0], T);
            }

            // ⑥ 末行被销毁（防御）：宁可重画，绝不静默丢失
            {
                var w = new WindowSim();
                w.NoteTurn(1);
                w.Delta(T); w.Finish(T);
                w.KillLastRow();                // 行没了（记账还在）
                w.Finish(T);                    // npc_reply
                Eq("S6 末行已销毁 → 正文仍然可见", w.CountText(T), 1);
            }

            // ⑦ 跨回合复读：两个回合都说了同一句 → **两条**泡（合法，不能误判成重复）
            {
                var w = new WindowSim();
                w.NoteTurn(1); w.Delta(T); w.Finish(T); w.Finish(T);
                w.NoteTurn(2); w.Delta(T); w.Finish(T); w.Finish(T);
                Eq("S7 跨回合复读 → 2 条泡（合法）", w.CountText(T), 2);
            }

            // ⑧ 非流式后端整段正文：一条 delta 就是全文，随后 step(text) + npc_reply
            {
                var w = new WindowSim();
                w.NoteTurn(1);
                w.Delta(T);                     // 整段一次回调
                w.Finish(T); w.Finish(T);
                Eq("S8 非流式整段 → 1 条泡", w.LiveCount(), 1);
            }

            // ⑨ 二步回合、两步正文不同 → 两条泡（多步回合的多条泡是正当的，别被去重吃掉）
            {
                var w = new WindowSim();
                w.NoteTurn(1);
                w.Delta("先看看你的根骨。"); w.Finish("先看看你的根骨。");
                w.Delta(T);                   w.Finish(T);
                w.Finish(T);
                Eq("S9 两步不同正文 → 2 条泡（合法）", w.LiveCount(), 2);
            }

            // ⑩ 已知缺口（记录现状，不假装已修）：多步回合 + 窗口 B
            //    重开窗销毁了本回合已渲染的行，而回放按设计不含未闭合回合 → 中间那段正文
            //    谁也补不回来（npc_reply 只带最后一步的正文，且它与最后一条泡逐字相同 → 跳过）。
            //    修它要动"投影是否包含未闭合回合"或"历史区/直播区分离"，属结构性改动，暂不做。
            {
                var w = new WindowSim();
                w.NoteTurn(1);
                w.Delta("第一段旁白。"); w.Finish("第一段旁白。");   // 第一步正文
                w.Reset();                                          // ★ 此刻重开窗
                w.Replay("上一回合的正文", 0);
                w.Delta(T); w.Finish(T);                            // 后续 step 的流式 + 定稿
                w.Finish(T);                                        // npc_reply
                Eq("S10【已知缺口】多步+窗口B → 第一段旁白丢失（现状如此）",
                   w.CountText("第一段旁白。"), 0);
                Eq("S10 最后一段正文仍在", w.CountText(T), 1);
            }
        }

        // ------------------------------------------------------------------
        // 屏幕记账模拟器：照搬 ChatWindow 的调用方契约
        // ------------------------------------------------------------------
        private sealed class Row
        {
            public string Text;
            public bool Alive = true;
        }

        private sealed class WindowSim
        {
            private readonly List<Row> _rows = new List<Row>();
            private int _lastRow = -1;        // _lastRenderRow（-1 = 未登记）
            private string _lastText;         // _lastRenderText
            private int _lastTurn = -1;       // _lastRenderTurn
            private int _curTurn = -1;        // _curTurn
            private int _stream = -1;         // _deltaBubble（-1 = 无活跃流式泡）
            private string _buf = "";         // _delta

            public void NoteTurn(int t) { if (t > 0) _curTurn = t; }

            /// <summary>流式门：OnDeltaEvent + AppendDelta（这一路**没有**去重）</summary>
            public void Delta(string tok)
            {
                if (_stream < 0) { _rows.Add(new Row()); _stream = _rows.Count - 1; _buf = ""; }
                _buf += tok;
                _rows[_stream].Text = _buf;   // LateUpdate 每帧把缓冲刷进 Text
            }

            /// <summary>FinishNpcBubble（step(text) 与 npc_reply 共用）</summary>
            public void Finish(string fullText)
            {
                bool hasStream = _stream >= 0;
                bool lastAlive = _lastRow >= 0 && _rows[_lastRow].Alive;
                var action = BubbleCommitPolicy.Decide(
                    fullText, _lastText, _curTurn, _lastTurn, hasStream, lastAlive);

                switch (action)
                {
                    case BubbleCommit.Skip:
                        // 撤掉多余的重绘泡（真实实现：DestroyActiveStreamingBubble）
                        if (hasStream) { _rows[_stream].Alive = false; _stream = -1; _buf = ""; }
                        return;                       // 记账不变；finally 里 ClearStreamingState
                    case BubbleCommit.OverwriteStreaming:
                        _rows[_stream].Text = fullText;
                        _lastRow = _stream;
                        break;
                    case BubbleCommit.OverwriteLastRow:
                        if (!string.IsNullOrEmpty(fullText)) _rows[_lastRow].Text = fullText;
                        break;
                    default:                          // CreateNew
                        _rows.Add(new Row { Text = fullText });
                        _lastRow = _rows.Count - 1;
                        break;
                }
                _lastTurn = _curTurn;
                _lastText = fullText;
                _stream = -1;                         // finally: ClearStreamingState()
                _buf = "";
            }

            /// <summary>ReplaceHistory 的 assistant 分支（回放参与记账）</summary>
            public void Replay(string body, int turn)
            {
                _rows.Add(new Row { Text = body });
                _lastRow = _rows.Count - 1;
                if (!string.IsNullOrEmpty(body)) { _lastText = body; _lastTurn = turn; }
            }

            /// <summary>ResetContent（开窗/换 NPC/重放前）—— 屏幕真空，记账清零，_curTurn 保留</summary>
            public void Reset()
            {
                _rows.Clear();
                _lastRow = -1; _lastText = null; _lastTurn = -1;
                _stream = -1; _buf = "";
            }

            /// <summary>模拟"末行被销毁但记账还指着它"（Unity 里 == null 为真 ⇒ lastAlive=false）</summary>
            public void KillLastRow()
            {
                if (_lastRow >= 0) _rows[_lastRow].Alive = false;
            }

            public int LiveCount()
            {
                int n = 0;
                foreach (var r in _rows) if (r.Alive) n++;
                return n;
            }

            public int CountText(string t)
            {
                int n = 0;
                foreach (var r in _rows) if (r.Alive && r.Text == t) n++;
                return n;
            }

            public List<string> LiveTexts()
            {
                var o = new List<string>();
                foreach (var r in _rows) if (r.Alive) o.Add(r.Text);
                return o;
            }
        }
    }
}
