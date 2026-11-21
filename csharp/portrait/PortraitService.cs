/// <summary>
/// 立绘加载 —— 游戏原生 PortraitModel 管线（神识传音聊天窗 UpdataModel 同款，反编逐字对照）。
///
/// 证据（types36_fulldll.txt，神识传音传音窗双立绘实录）：
///   PortraitModel.CreateTextureInModelData(unit.data.unitData.propertyData.modelData,
///       rawImage, new Vector2(0f, -4.5f), 1f, false, true,(Action&lt;GameObject&gt)null);
///   特殊剧情 NPC（五朵金花等）改走：
///   g.conf.dramaNpc.CreateTexture(g.conf.dramaNpc.GetFiveFlowerDramaNpcID(unit),
///       rawImage, 同参数,(PortraitModelData)null,(Action&lt;GameObject&gt)null);
///   玩家立绘 = g.world.playerUnit.data.unitData.propertyData.modelData 同款调用。
///
/// 用法：PortraitService.Fill(unit, rawImage) —— 失败返回 false，调用方隐藏槽位。
/// 硬约束：触 unitData / g.conf，仅 Unity 主线程调用。取景不适时只调 ModelOffset/ModelScale。
/// </summary>
using System;
using UnityEngine;
using UnityEngine.UI;

namespace AgentLoopBridge
{
    internal static class PortraitService
    {
        /// <summary>模型垂直偏移/缩放（神识传音同款常量；槽位尺寸不同导致取景不合适时只调这里）</summary>
        public static readonly Vector2 ModelOffset = new Vector2(0f, -4.5f);
        public const float ModelScale = 1f;

        private static int _calls;       // 正常渲染计数（诊断：定位"每帧刷屏"的调用方）
        private static int _degenerate;  // 退化槽位拦截计数

        /// <summary>立绘总开关（config `ui.portraits_enabled`，默认 true）。由
        /// `ModMain.RefreshInitiativeFromConfig` 在 WS 连上/保存配置后写入（热生效，不必重启）。
        /// false 时 `Fill` 直接返回 false、不触碰游戏立绘 API（调用方按 false 隐藏槽位）。</summary>
        public static bool Enabled = true;

        /// <summary>槽位节点的层级路径（诊断日志用，异常安全）。</summary>
        private static string SafePath(RawImage slot)
        {
            try
            {
                var t = slot.transform;
                string path = t != null ? t.name : "?";
                for (int i = 0; i < 4 && t != null && t.parent != null; i++)
                {
                    t = t.parent;
                    path = t.name + "/" + path;
                }
                return path;
            }
            catch { return "?"; }
        }

        /// <summary>单位名字（诊断日志用，异常安全——绝不触发 Unity 主线程外的 API）。</summary>
        private static string SafeUnitName(WorldUnitBase unit)
        {
            try { return unit.data.unitData.propertyData.GetName(); }
            catch { return "?"; }
        }

        /// <summary>把单位立绘画进 rawImage 槽位。unit/slot 为空或渲染失败返回 false。</summary>
        public static bool Fill(WorldUnitBase unit, RawImage slot)
        {
            if (unit == null || slot == null) return false;
            // 玩家开关（config `ui.portraits_enabled`）：关掉后**完全不触碰游戏立绘 API**
            // （槽位由调用方按 false 隐藏）。为什么不复用 DiagSwitches.NoPortraits：那个是文件哨兵
            // 诊断开关（重启前手改文件），这个是给玩家的正式配置项，走 get_config 热生效。
            // 立绘路径出过崩溃/异常（PortraitModel/RenderTexture 那条链），这个开关同时是玩家侧逃生门。
            if (!Enabled)
            {
                _calls++;
                if (_calls <= 3 || _calls % 200 == 0)
                    ModMain.P("[PortraitService] 立绘开关已关（ui.portraits_enabled=false），跳过 Fill #" + _calls);
                return false;
            }
            // 诊断开关：完全不触碰游戏立绘 API（验证"触碰 PortraitModel 是否破坏游戏"）
            if (DiagSwitches.NoPortraits)
            {
                _calls++;
                if (_calls <= 3 || _calls % 200 == 0)
                    ModMain.P("[PortraitService] noPortraits 开关生效，跳过 Fill #" + _calls);
                return false;
            }
            // 诊断：把"我方何时开始调游戏立绘 API"打出来（时间线上定位破坏点）
            _calls++;
            if (_calls <= 5)
                ModMain.P("[PortraitService] Fill #" + _calls + " slot=" + SafePath(slot) +
                          " unit=" + SafeUnitName(unit));
            else if (_calls % 200 == 0)
                ModMain.P("[PortraitService] Fill 计数=" + _calls + "（退化跳过 " + _degenerate + "）");
            // 防护 + 诊断：槽位 rect 为 0（未布局/已隐藏）时创建 RenderTexture 必然失败
            // （引擎刷 "RenderTexture.Create failed: width & height must be larger than 0"，
            //  高频刷屏会拖垮主线程）——这里提前拦掉，行为等价但零引擎开销。
            // 计数器留作 09-10 "每帧刷屏"事故的调用方定位手段（若日志里计数不增长 = 不是我们）。
            try
            {
                var rt = slot.rectTransform;
                float w = rt != null ? rt.rect.width : 0f;
                float h = rt != null ? rt.rect.height : 0f;
                if (w <= 0f || h <= 0f)
                {
                    _degenerate++;
                    if (_degenerate <= 3 || _degenerate % 300 == 0)
                        ModMain.P("[PortraitService] 跳过退化槽位 #" + _degenerate + " rect=" + w + "x" + h +
                                  " slot=" + SafePath(slot));
                    return false;
                }
            }
            catch { }
            try
            {
                // 特殊剧情 NPC：立绘数据在 conf（dramaNpc 表），不在单位身上
                int dramaNpcId = g.conf.dramaNpc.GetFiveFlowerDramaNpcID(unit);
                if (dramaNpcId != 0)
                {
                    g.conf.dramaNpc.CreateTexture(dramaNpcId, slot, ModelOffset, ModelScale,
                                                  (PortraitModelData)null, (Action<GameObject>)null);
                    return true;
                }
                var modelData = unit.data.unitData.propertyData.modelData;
                if (modelData == null) return false;
                PortraitModel.CreateTextureInModelData(modelData, slot, ModelOffset, ModelScale,
                                                       false, true, (Action<GameObject>)null);
                return true;
            }
            catch (Exception e)
            {
                ModMain.P("[PortraitService] fill: " + e.Message);
                return false;
            }
        }
    }
}
