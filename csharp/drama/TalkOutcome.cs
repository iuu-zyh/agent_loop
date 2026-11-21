/// <summary>
/// TalkOutcome —— 言语类挂起动作（`world_ai_action` 的 lun_dao / shuang_xiu，走
/// `NativeActionService.StartToUnitAiActionDeferred`）的**结局读数**。
///
/// ## 09-23 用户定：内容不再由本类收集
/// 段（`DramaSegmentCore`）是**唯一的内容流**（页 + 选项，追加序 = 时序）。本类只剩三件事：
///   ① `Arm(slot, npcName, op, unitID)` —— 记住"这场交互从此刻开始"（锚点）与目标；
///   ② `FillResult(data)` 在完成回调里**读段的区间**（`ReadRange`，读走即标消费）写进结果；
///   ③ `OnSlotResolved` 槽位结束 → 清状态。
///
/// 之前的 `NoteLine / NoteChoice / NoteNpc` 与 `_lastPage` / `after_drama_id` 已整体删除 ——
/// 那套"台词一个数组、选择另一个数组、事后按页号拼序"的机械，正是切磋结果**顺序出错**的根源
/// （09-23 真机：玩家战前点的「接受切磋」被排到战后台词之后）。现在两边共用一个追加序，
/// 顺序天然正确，渲染层不需要再做任何交错。
///
/// 视图留给 Python：`lines` 里每行带 `kind`（page/option）/`who`/`name`/`text`/`drama_id`，
/// 按顺序出话（"X说：…" / "X选择了：「…」"）。`player` = 玩家显示名（渲染层要用）。
///
/// ★锚点必须早于动作启动★：`ActionStart` 会同步弹首窗（论道开窗页），锚点晚于它就漏第一页 ——
/// 调用位置见 `NativeActionService.StartToUnitAiActionDeferred`（`ActionStart` 之前 Arm）。
///
/// 线程：全部 Unity 主线程（工具执行 / 完成回调 / OnResolved）。
/// </summary>
using System;
using System.Collections.Generic;
using Newtonsoft.Json.Linq;
using UnityEngine;

namespace AgentLoopBridge
{
    internal static class TalkOutcome
    {
        private static int _slot = -1;
        private static string _op = "";
        private static string _npc = "";
        private static string _uid = "";
        /// <summary>区间起点（`Time.unscaledTime`）——本场交互的第一页不会早于它。</summary>
        private static float _anchor;
        private static bool _hooked;

        /// <summary>是否在跟踪一场言语交互（供诊断/保护用）。</summary>
        public static bool Active { get { return _slot > 0; } }

        /// <summary>开始跟踪。`slot` = 已挂起的 DramaGate 槽位（完成/超时 Resolve 时收口）；
        /// `unitID` = 目标 NPC 的精确编号（区间读数的键）；锚点取"此刻"。</summary>
        public static void Arm(int slot, string npcName, string op, string unitID)
        {
            Reset();
            if (slot <= 0) return;
            _slot = slot;
            _op = op ?? "";
            _npc = npcName ?? "";
            _uid = unitID ?? "";
            _anchor = Now();
            EnsureHooked();
            try
            {
                ModMain.P("[TalkOutcome] 开始跟踪 slot=" + slot + " op=" + _op + " npc=" + _npc
                          + " uid=" + _uid + " 锚点=" + _anchor.ToString("0.0"));
            }
            catch { }
        }

        /// <summary>动作启动失败 / 取消：撤销跟踪（不发结果，只清状态）。</summary>
        public static void Disarm()
        {
            Reset();
        }

        /// <summary>
        /// 完成回调组结果时调用：**读段**（本场交互的页 + 选择，按时序）写进 `data["lines"]`。
        /// **必须在 `DramaGate.Resolve` 之前**调用：`OnResolved` 按订阅顺序跑，本类订阅晚于
        /// WsClient，事件里补的字段进不了已发出的 response。
        /// 读走即标消费（`consume:true`）⇒ 玩家之后点「对话」不会把这批内容再投一遍。
        /// 没读到内容就不写该字段（字段缺失 ≠ 否定）。
        /// </summary>
        public static void FillResult(JObject data)
        {
            if (_slot <= 0 || data == null) return;
            List<SegmentLine> lines;
            // 锚点取凭证首次登记时刻（早于 ActionStart ⇒ 覆盖首窗）；凭证没了回落本类锚点。
            bool ok = DramaSegmentRecorder.TryReadRangeFromChain(_uid, _anchor, true, out lines);
            if (!ok || lines == null || lines.Count == 0)
            {
                try { ModMain.P("[TalkOutcome] 区间无内容（对方=" + _uid + "），结果不带 lines"); } catch { }
                return;
            }
            string pn = DramaSegmentRecorder.PlayerDisplayName();
            data["lines"] = DramaSegmentRecorder.LinesToJson(lines, pn);
            // 玩家显示名：Python 要把它落成人话（"X说：/ X选择了：「…」"）。缺失才补。
            if (data["player"] == null && !string.IsNullOrEmpty(pn)) data["player"] = pn;
            try
            {
                ModMain.P("[TalkOutcome] 结果带上交互内容 " + lines.Count + " 行（含玩家选择；"
                          + "op=" + _op + "，读走即标消费）");
            }
            catch { }
        }

        /// <summary>槽位结束（完成回调已消费 / 90s 超时）→ 清状态。订阅 `DramaGate.OnResolved`。</summary>
        public static void OnSlotResolved(int slot, JObject result)
        {
            if (_slot != slot) return;
            try { ModMain.P("[TalkOutcome] 槽位结束（op=" + _op + "）→ 清状态"); } catch { }
            Reset();
        }

        /// <summary>订阅 `DramaGate.OnResolved`（幂等；失败只影响超时清理，正常路径不受影响）。</summary>
        private static void EnsureHooked()
        {
            if (_hooked) return;
            _hooked = true;
            try { DramaGate.OnResolved += OnSlotResolved; }
            catch (Exception e)
            {
                _hooked = false;
                try { ModMain.P("[TalkOutcome] 订阅 OnResolved 失败（超时后将残留状态，正常路径不受影响）: " + e.Message); } catch { }
            }
        }

        private static void Reset()
        {
            _slot = -1;
            _op = "";
            _npc = "";
            _uid = "";
            _anchor = 0f;
        }

        private static float Now()
        {
            try { return Time.unscaledTime; } catch { return 0f; }
        }
    }
}
