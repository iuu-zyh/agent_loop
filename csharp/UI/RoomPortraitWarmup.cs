/// <summary>
/// 群成员立绘**逐个预热** —— 开群时把每个成员的立绘走一次游戏原生立绘 API，
/// 渲进对话窗那个**既有的**模板头像槽位，落盘进 `PortraitCache`，此后他所有行都复用这一张。
///
/// ## 为什么需要它
/// `PortraitCache` 的 PNG 只在**单聊开窗**时产生（`AbChatPanel.FillPortraits` → `RequestCapture`）。
/// 群聊里如果只读缓存，那么"从没单聊过的成员"就没有任何脸可贴 —— 本机实测磁盘上只有 2 张
/// （玩家 + 一个 NPC），也就是说这个功能上线后**基本看不见**。用户的需求是"每个 NPC 都要有
/// 对应的立绘"，所以开群时要主动把缺的补上。
///
/// ## 为什么不是"每行直接调 API"
/// `PortraitModel.CreateTextureInModelData` 是**真实渲染**（按槽位尺寸建 RenderTexture、渲模型、
/// 回读像素）。每条消息渲一次、回放 100 行就渲 100 次 —— 同一个人的脸被重复渲几十次。
/// 所以：**每个说话人渲一次**，渲完落进像素缓存，之后所有行都贴缓存里那一张。
/// 本质上仍然是"通过立绘 API 获取"，只是加了"同一个人只渲一次"的复用。
///
/// ## 串行化（这段是硬要求，不是优化）
/// 预热渲进的是**行模板的那个槽位**（与单聊共用），所以同一时刻只能有一个人在飞：
/// 渲 A → 等 A 的捕获任务**结束**（落盘 / 放弃 / 超时）→ 才渲 B。
/// 不等就渲 B 的话，A 的捕获任务到期时读到的是 B 的渲染结果，然后**存成 A 的名字** ——
/// 错脸是静默的（PNG 打开看着完全正常），只会表现为"这个人怎么长着别人的脸"，且从此固化在盘上。
///
/// ## 失败面（诚实说明）
/// · `PortraitService.Fill` 对**不在场**的 NPC 可能拿不到 `modelData` 而返回 false
///   （`PortraitService.cs` 里那条 `if (modelData == null) return false`）——
///   那种成员就是没有立绘，不会崩，日志里打 `预热放弃`。**离线无法验证**，要实机确认。
/// · 玩家侧逃生门沿用既有开关：`ui.portraits_enabled=false` 时 `Fill` 直接返回 false，
///   预热逐个跳过；`DiagSwitches.NoPortraits` 则完全不触碰原生渲染 API。
///
/// ## 谁是"宿主"
/// `ChatWindow.Update` 每帧调 `Pump()`（与 `PortraitCache.PumpCapture` 并列）。
/// 入队 = `ChatWindow.OpenForRoom`（成员到齐之后）；出队 = 单聊开窗 / 关窗 / 换群。
/// </summary>
using System.Collections.Generic;
using UnityEngine;
using UnityEngine.UI;

namespace AgentLoopBridge
{
    internal static class RoomPortraitWarmup
    {
        /// <summary>单次"渲染 → 等落盘"的上限（秒）。超时就撤销该捕获任务换下一个 ——
        /// 不设上限的话，一个永远不落盘的成员会把整条队伍卡死（后面的人永远没有脸）。</summary>
        private const float PerShotSeconds = 1.5f;

        private static readonly List<string> _queue = new List<string>();
        private static string _curName;        // 正在处理的成员名（null = 空闲）
        private static string _curId;          // 他的缓存键（渲染后才知道）
        private static bool _waiting;          // 已渲染、正在等落盘
        private static float _deadline;
        private static RawImage _scratch;      // 与单聊共用的模板头像槽位
        private static int _done;              // 本次预热**已处理**人数（含"已有快照，跳过"的）
        private static int _shot;              // 其中**真的调了立绘 API** 的人数（日志要说实话）

        /// <summary>是否正在预热（诊断/日志用）。</summary>
        public static bool Active { get { return _curName != null || _queue.Count > 0; } }

        /// <summary>
        /// 排队预热一批名字（可含玩家）。重复调用会**重新开始**：撤掉在飞的捕获任务、
        /// 重建队列 —— `OpenForRoom` 在一个开群流程里会被调两次（占位一次、回执校准一次），
        /// 成员集合以第二次为准，第一次排下的队必须作废。
        ///
        /// 已在内存或已在盘上的名字**直接跳过**（不占一轮渲染）。
        /// </summary>
        public static void Start(List<string> names, RawImage scratch)
        {
            Stop(silent: true);
            _scratch = scratch;
            Enqueue(names);
        }

        /// <summary>
        /// 往队列**追加**（不动在飞的那个、也不清已有队列）。
        ///
        /// 为什么单独开一个口子：拉人成功后要给新人预热，而那时队里可能还排着开群时的
        /// 其他成员 —— 走 `Start` 会把他们的预热整队撤掉（他们的脸就再也不会补上，
        /// 直到下次开群）。「换一个群」用 `Start`，「群里多了一个人」用这个。
        /// </summary>
        public static void Enqueue(List<string> names)
        {
            if (names == null || names.Count == 0) return;
            int added = 0;
            for (int i = 0; i < names.Count; i++)
            {
                string n = names[i];
                if (string.IsNullOrEmpty(n)) continue;
                if (_queue.Contains(n) || n == _curName) continue;
                _queue.Add(n);
                added++;
            }
            if (added == 0) return;
            try
            {
                ModMain.P("[RoomPortraitWarmup] 排队预热 " + added + " 人（队列 " + _queue.Count
                          + "）：" + string.Join("、", _queue.ToArray())
                          + "（已在缓存/盘上的会当场跳过；逐个走立绘 API，串行）");
            }
            catch { }
        }

        /// <summary>停掉预热并撤销在飞的捕获任务（单聊开窗 / 关窗 / 换群）。
        /// ★ 必须撤销：它渲的槽位与单聊 `FillPortraits` 是**同一个**，不撤就会把单聊刚渲的
        /// 那张脸存成群成员的 id。</summary>
        public static void Stop(bool silent = false)
        {
            if (_curId != null) PortraitCache.CancelCapture(_curId);
            int n = _queue.Count + (_curName != null ? 1 : 0);
            _queue.Clear();
            _curName = null;
            _curId = null;
            _waiting = false;
            _deadline = 0f;
            _done = 0;
            _shot = 0;
            if (!silent && n > 0)
            {
                try { ModMain.P("[RoomPortraitWarmup] 停止预热（剩 " + n + " 人未处理）"); } catch { }
            }
        }

        /// <summary>每帧推进（`ChatWindow.Update` 调）。一帧最多推进"一个成员的一步"。</summary>
        public static void Pump()
        {
            if (_curName == null)
            {
                if (_queue.Count == 0) return;
                _curName = _queue[0];
                _queue.RemoveAt(0);
                _curId = null;
                _waiting = false;
            }

            if (!_waiting)
            {
                Step();
                return;
            }

            // 等落盘：任务结束（成功/放弃）或超时 → 换下一个
            if (!PortraitCache.HasPendingCapture(_curId)) { Finish(null); return; }
            if (Time.unscaledTime > _deadline)
            {
                PortraitCache.CancelCapture(_curId);
                Finish("落盘超时 " + PerShotSeconds + "s");
            }
        }

        /// <summary>处理当前成员的"渲染"这一步：能跳过就跳过，要渲就渲并转入等待。</summary>
        private static void Step()
        {
            if (_scratch == null) { Stop(); return; }

            WorldUnitBase unit = null;
            try { unit = UnitLookup.Resolve(_curName); } catch { }
            if (unit == null) { Finish("解析不到单位（还没进场？）"); return; }

            string id = PortraitCache.IdOf(unit);
            if (string.IsNullOrEmpty(id)) { Finish("读不到 unitID"); return; }
            _curId = id;

            Texture2D have;
            if (PortraitCache.TryGet(id, out have)) { Finish(null); return; }   // 内存已有
            if (PortraitCache.HasOnDisk(id))
            {
                // 盘上有：不必再渲一次，读回来就行（贴图由 RowAvatars 的待贴表完成）
                PortraitCache.RequestLoad(id);
                Finish(null);
                return;
            }

            bool ok;
            try { ok = PortraitService.Fill(unit, _scratch); }
            catch { ok = false; }
            if (!ok) { Finish("立绘渲染失败（模型数据未就绪 / 立绘开关已关）"); return; }

            PortraitCache.RequestCapture(id, _scratch);
            _shot++;
            _waiting = true;
            _deadline = Time.unscaledTime + PerShotSeconds;
        }

        /// <summary>结束当前成员：记日志、清状态，下一帧自动进下一个。</summary>
        private static void Finish(string why)
        {
            if (why != null)
            {
                try { ModMain.P("[RoomPortraitWarmup] " + _curName + "：" + why); } catch { }
            }
            else if (_curId != null)
            {
                _done++;
            }
            _curName = null;
            _curId = null;
            _waiting = false;
            if (_queue.Count == 0 && _done > 0)
            {
                // 说清"处理"与"真的渲了"的差别：盘上已有快照的人只走读回，不碰立绘 API。
                // 日志里这两个数分开，才能一眼看出"预热到底跑了几次原生渲染"。
                try
                {
                    ModMain.P("[RoomPortraitWarmup] 预热结束：处理 " + _done + " 人，其中走立绘 API 渲染 "
                              + _shot + " 人（其余已有快照，直接读回）");
                }
                catch { }
                _done = 0;
                _shot = 0;
            }
        }
    }
}
