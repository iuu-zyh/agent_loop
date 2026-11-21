/// <summary>
/// WorldFrameOutbox —— 「存档身份帧」的发送队列（纯托管：零 Unity / 零 Newtonsoft）。
///
/// ── 它解决什么（09-18「世界身份丢失」修复的 C# 半边） ─────────────────────────
/// `world_id`（= 玩家 unitID）只活在 Python 进程内存里，而补发身份原先只有 `save_happened`
/// / `load_happened` 两条事件。Python 一重启（配置面板保存 / BrainLink 拉起 / 崩溃重生）
/// 身份就没了，那段窗口里的读写全落**扁平目录**（不属于任何存档）：开窗看不见本存档历史，
/// 下一次存档还会把短账本**整份覆盖**进真存档（静默丢记忆；09-15 02:13 实机已发生过一次
/// Python 中途重启，只差一条消息就踩上）。
///
/// 而**发帧这件事本身也不可靠**：`WsClient.SendRequest` 在未连接时直接回调 `ok:false`
/// 且**不排队**，两个调用点的回调是 `_ => { }`（丢回执）却照样打印"已发送" —— 于是
/// "进世界早于 WS 连上"（实机日志里两者相隔 7~45s）或"Python 正在重启"这类时序会把帧
/// **静默**吞掉，整局都没有存档身份。本类就是补这一环。
///
/// ── 三条规则（每条都对应一种静默故障） ────────────────────────────────────────
///   ① 连着就直发、未连接就排队 —— 不许把帧丢掉；
///   ② 连上后按**原顺序**补投、且**只投一次** —— 顺序反了会让"先读档后存档"变成
///      "先存档后读档"（后者会把刚固化的内容又丢掉）；
///   ③ 积压超上限时丢**最旧**的 —— 陈旧身份比丢帧更糟：拿它去切命名空间 = 指到别的存档。
///      丢最新同样不行（那会让"最后一次读档"永远投不出去）。
///
/// ── 怎么测 ──────────────────────────────────────────────────────────────────
/// 本类是刻意的纯逻辑（发送动作与连接状态都从构造参数注入），所以 WSL 里能直接用
/// net8.0 控制台工程跑断言（见 `csharp/tests/WorldFrameOutboxTests/`）——
/// 这层出错的症状全是"该发的帧没发"，没有界面、没有异常，只能靠单测钉住。
///
/// 线程：只在 Unity 主线程调用（与 `ModMain` 的存档/连接回调同线程）。
/// </summary>
using System;
using System.Collections.Generic;

namespace AgentLoopBridge
{
    /// <typeparam name="T">载荷类型。主工程用 `JObject`；单测用 `string`，从而不依赖 Newtonsoft。</typeparam>
    internal sealed class WorldFrameOutbox<T>
    {
        private readonly List<KeyValuePair<string, T>> _queue = new List<KeyValuePair<string, T>>();
        private readonly Func<bool> _connected;
        private readonly Action<string, T> _send;
        private readonly Action<string> _log;

        /// <summary>排队上限。正常最多同时存在 1~2 条（一次断线期间至多一次读档 + 几次存档）；
        /// 上限只防"连接长期不通 + 反复存档"把内存撑起来。</summary>
        internal int MaxPending = 8;

        /// <summary>
        /// `connected` = 现在能不能发（主工程传 `() => _wsClient != null && _wsClient.IsConnected`）；
        /// `send` = 真正把一帧写出去（主工程传 `_wsClient.SendRequest(..., 10000)`）；
        /// `log` 可为 null（单测多数场合不关心）。
        /// </summary>
        internal WorldFrameOutbox(Func<bool> connected, Action<string, T> send, Action<string> log = null,
                                 int maxPending = 8)
        {
            if (connected == null) throw new ArgumentNullException("connected");
            if (send == null) throw new ArgumentNullException("send");
            _connected = connected;
            _send = send;
            _log = log;
            MaxPending = maxPending < 1 ? 1 : maxPending;
        }

        /// <summary>当前排队条数（诊断/测试用）。</summary>
        internal int Pending { get { return _queue.Count; } }

        /// <summary>
        /// 交一帧出去：连着就直发，否则排队。返回**是否直发成功**（false = 已排队）。
        /// `send` 抛出的异常照抛（调用方自己决定记不记日志）—— 静默吞掉发送失败就等于丢帧。
        /// </summary>
        internal bool Send(string method, T payload)
        {
            if (_connected())
            {
                _send(method, payload);
                return true;
            }
            if (_queue.Count >= MaxPending)
            {
                KeyValuePair<string, T> old = _queue[0];
                _queue.RemoveAt(0);
                if (_log != null) _log("存档身份帧积压超 " + MaxPending + " 条 → 丢弃最旧的 " + old.Key);
            }
            _queue.Add(new KeyValuePair<string, T>(method, payload));
            return false;
        }

        /// <summary>
        /// 连接恢复后按原顺序补投。返回**实际投出去的条数**。
        /// 仍未连接 ⇒ 一条都不动（原样留在队列里，等下一次连接事件）—— 把帧"烧掉"是最坏的结果。
        /// </summary>
        internal int Flush()
        {
            if (_queue.Count == 0) return 0;
            if (!_connected()) return 0;
            var frames = new List<KeyValuePair<string, T>>(_queue);
            _queue.Clear();          // 先摘下来再发：`send` 里若回头调 Send 不会重复入队
            int n = 0;
            foreach (var kv in frames)
            {
                _send(kv.Key, kv.Value);
                n++;
            }
            return n;
        }
    }
}
