/// <summary>
/// ContactStore —— 通讯录数据中转层（**纯静态、不持有任何 Unity 对象引用**）。
///
/// 背景（世界输入失效事故）：通讯录/配置面板改为"按需创建"（方案 A）后，
/// 面板实例在首次打开前不存在、且游戏关闭面板时可能销毁实例——因此所有
/// "面板不在场也必须可用"的数据必须搬到与实例无关的静态层：
///   ① 手动好友镜像（真相在 Python contacts.json，此处只是 C# 侧缓存）
///   ② 会话文件 mtime 索引（npc_id → 秒）
///   ③ 本地互动时间戳（npc_reply 到达时刻）
/// 消费方：NpcInitiativeMonitor（候选/冷却）、NpcPanelAddContact（按钮状态）、
///         ContactPresenter（渲染时读取并应用）。
///
/// 铁律：**只存 id/名字/时间戳等纯托管数据，绝不缓存 WorldUnitBase/GameObject 等
/// IL2CPP 引用**（僵尸引用风险）；单位引用一律在使用现场经 UnitLookup.Resolve 现查。
/// 线程约定：全部在 Unity 主线程调用（RPC 回调经 WsClient 主线程派发）。
/// </summary>
using System;
using System.Collections.Generic;

namespace AgentLoopBridge
{
    internal static class ContactStore
    {
        private static readonly HashSet<string> _manualNames = new HashSet<string>();
        private static readonly Dictionary<string, long> _sessionsLast = new Dictionary<string, long>();
        private static readonly Dictionary<string, long> _localLast = new Dictionary<string, long>();
        private static bool _manualLoaded;

        /// <summary>数据变更通知（视图层订阅：面板开着时重渲行；未开则忽略）。</summary>
        public static event Action Changed;
        private static void Fire() { try { Changed?.Invoke(); } catch { } }

        /// <summary>手动好友镜像是否已从 Python 拉到过（避免"未拉取"与"空列表"混淆）。</summary>
        public static bool ManualLoaded => _manualLoaded;

        /// <summary>手动好友名集合（只读视图；NPC 面板按钮状态 / 主动传音候选用）。</summary>
        public static IEnumerable<string> ManualContactNames => _manualNames;

        public static bool IsManualContact(string name)
        {
            return !string.IsNullOrEmpty(name) && _manualNames.Contains(name);
        }

        /// <summary>RPC 回包：整体替换手动好友镜像。</summary>
        public static void SetManualNames(IEnumerable<string> names)
        {
            _manualNames.Clear();
            if (names != null)
            {
                foreach (var n in names)
                {
                    if (!string.IsNullOrEmpty(n)) _manualNames.Add(n);
                }
            }
            _manualLoaded = true;
            Fire();
        }

        /// <summary>加/移除一个手动好友（本地即时生效；真相仍以 Python 回包校准为准）。</summary>
        public static void SetManualContact(string name, bool add)
        {
            if (string.IsNullOrEmpty(name)) return;
            bool changed = add ? _manualNames.Add(name) : _manualNames.Remove(name);
            if (changed) Fire();
        }

        /// <summary>RPC 回包：整体替换会话 mtime 索引（npc_id → 秒）。</summary>
        public static void SetSessionMtimes(IEnumerable<KeyValuePair<string, long>> items)
        {
            _sessionsLast.Clear();
            if (items != null)
            {
                foreach (var kv in items)
                {
                    if (!string.IsNullOrEmpty(kv.Key) && kv.Value > 0) _sessionsLast[kv.Key] = kv.Value;
                }
            }
            Fire();
        }

        /// <summary>本地互动时间戳（npc_reply 到达即记，跨面板生命周期）。</summary>
        public static void MarkLocal(string npc, long unixSeconds)
        {
            if (string.IsNullOrEmpty(npc) || unixSeconds <= 0) return;
            _localLast[npc] = unixSeconds;
        }

        /// <summary>最近互动时间：会话 mtime ∪ 本地时间戳（取大）；无记录返回 0。</summary>
        public static long LastInteract(string npc)
        {
            long last = 0;
            if (npc != null)
            {
                if (_sessionsLast.TryGetValue(npc, out var m) && m > last) last = m;
                if (_localLast.TryGetValue(npc, out var l) && l > last) last = l;
            }
            return last;
        }
    }
}
