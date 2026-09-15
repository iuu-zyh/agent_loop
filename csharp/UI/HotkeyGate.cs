/// <summary>
/// HotkeyGate —— 「玩家此刻在我们自己的界面里吗」的**唯一判据层**。
///
/// 本文件只回答一个问题：**该不该屏蔽游戏原生快捷键**。真正动手屏蔽的是
/// `UI/FastKeyGate.cs`（给 `MapWorldMgr.FastKey()` 挂前缀）；这里提供判据。
///
/// ============================ 判据 ============================
/// 「**我们的任一界面开着** OR 输入框聚焦」。
///   · 面板开着的判据见 `OwnUiProbe`（三个面板各写各的 `IsShowing`，**都不看"宿主引用非空"**
///     —— 关窗只把 BG 灰掉/把根 deactivate，引用会一直是非空的，那是踩过的坑）；
///   · 输入框聚焦：uGUI `InputField` 与 `TMPro.TMP_InputField` 两边都认，且只认 `isFocused`、
///     **不**认"仅被选中"（单行 InputField 按 Enter 提交后会 Deactivate 但保持 selected，
///     那种状态下按键不再进输入框，继续屏蔽反而是错的）；
///   · `ESC` 不在任何一张绑定表里（`DefaultKeys.json` 全文 33 条无 keyID 27；Enter=13 同理），
///     所以「Esc 退出 / Enter 发送」天然不受影响，**无需开白名单**；
///   · 宿主自己的 F 键已全部删除（见 `AbHotkeys` 文件头），不存在"掐到自己人"。
///
/// ============================ 覆盖面 ============================
/// `DefaultKeys.json` 大地图组（id 204-215）共 12 个键，与 `Key` 表属性一一对应：
///     204 I=人物属性  205 X=技能    206 B=背包    207 L=任务    208 H=信件    209 N=大事件
///     210 M=小地图    211 O=技艺    212 Z=跳过本月 213 J=逆天改命 214 F=神器    215 T=仙法
/// 这 12 个键全部由 `MapWorldMgr.FastKey()` 一处派发，故拦一处即全覆盖（真机实测
/// `FastKey=820/跳过820`，100% 命中）。
///
/// ============================ 排查史：这些接缝**已实证不是派发点，别再试** ============================
/// 全部来自真机诊断行，不是推断。踩过的坑按时间顺序：
///   · `UIFastClick.Update`      —— 场景内 **0 个**实例（大地图 HUD `G:btnEmail` 全套组件里
///                                  根本没有它），那条补丁**从头到尾一次没被调用过**。
///   · `InputBase.Update`        —— 70 万次命中且都被跳过，快捷键照旧 ⇒ 管的是手柄/输入模式。
///   · `InputHandShank.Update`   —— 同上。
///   · `UIOperationGroup.Update` —— 1.3 万次命中且都被跳过，无效。
///   · `UIMapMain.Update`        —— 4374 次命中且都被跳过，无效。
///   · `UpdateHandleInput`       —— **整局只被调用 1 次**！它是"把输入控件填充好、按钮接上
///                                  回调"的**接线方法**，不是轮询。跳过它 ⇒ 面板永远填不上内容、
///                                  按钮永远没接上回调 → **面板空白 + 关闭按钮点不动**（用户实证）。
///                                  这是最贵的一课：动前缀之前先确认那方法是"派发"还是"接线"。
///   · `IsCanOperation`/`IsTopUI`—— 命中 **0 次**，游戏压根不调用。
///   · **UI 层级**               —— 面板开着时我们本来就是层顶（`层顶=UIConfigAi order=40`），
///                                  游戏照样响应快捷键 ⇒ 热键路径**根本不查 UI 层级**。
///   · `Key` 表 getter           —— 挂载 67 个全失败（`Key` 是纯数据类，原生侧多半直接内联字段读）。
///   · **调用栈追踪**            —— 挂在 `UIMgr.OpenUI` postfix 上追"谁派发的"，结果三次抓到的
///                                  全是**我们自己**开面板的调用（`AbChatPanel.EnsureResident` /
///                                  `AbConfigPanel.HandleToggle`），预算被自己的噪声吃光。
///                                  教训：追踪器必须先排除自身帧，否则等于没装。
///
/// **正解怎么找到的**：不再猜"哪个 Update 在轮询"，改为**直接读 IL2CPP 元数据、按游戏自己
/// 起的名字找** —— `MapWorldMgr.FastKey()` 是全程序集唯一含 "FastKey" 的方法。
/// 完整证据链与修法见 `UI/FastKeyGate.cs` 文件头。
///
/// 【失败姿态】判据里任何异常 → 返回 false（不屏蔽）。宁可快捷键漏一次，
/// 也绝不因为本判据把游戏按死。
/// </summary>
using UnityEngine;
using UnityEngine.EventSystems;
using UnityEngine.UI;

namespace AgentLoopBridge
{
    /// <summary>「玩家此刻在我们自己的界面里吗」——屏蔽游戏快捷键的唯一判据。</summary>
    internal static class HotkeyGate
    {
        /// <summary>应急总开关（false = 完全退回原版行为，不屏蔽任何快捷键）。</summary>
        internal static bool Enabled = true;

        /// <summary>
        /// 逐帧缓存。`FastKey` 每帧只问一次，但缓存让判据可以随时被别的调用方复用而不变贵
        /// （`EventSystem` / `GetComponentInParent` 都不便宜）。
        /// </summary>
        private static int _cachedFrame = -1;
        private static bool _cachedBlock;

        /// <summary>本帧是否应当屏蔽游戏原生快捷键。</summary>
        internal static bool ShouldBlock()
        {
            if (!Enabled) return false;

            int f = Time.frameCount;
            if (f == _cachedFrame) return _cachedBlock;

            _cachedFrame = f;
            _cachedBlock = Compute();
            return _cachedBlock;
        }

        private static bool Compute()
        {
#if AB_UI
            try
            {
                if (OwnUiProbe.OpenPanelName() != null) return true;
            }
            catch { }
#endif
            try
            {
                if (AnyInputFocused()) return true;
            }
            catch { }
            return false;
        }

        /// <summary>
        /// 有没有输入框正被编辑。用 `GetComponentInParent` 而非 `GetComponent`：uGUI/TMP 的
        /// InputField 都是把焦点设在**自身 GameObject** 上，但父级查找对子物体抢焦点的情况也成立。
        /// </summary>
        private static bool AnyInputFocused()
        {
            try
            {
                var es = EventSystem.current;
                if (es == null) return false;
                var go = es.currentSelectedGameObject;
                if (go == null) return false;

                var field = go.GetComponentInParent<InputField>();
                if (field != null && field.isFocused) return true;

                // 游戏本体用的是 TMP_InputField（InputSDK.SelectInput 有 TMP 重载）；
                // 本 mod 只建 uGUI InputField。两边都盖住，代价只是一次 GetComponentInParent。
                try
                {
                    var tmp = go.GetComponentInParent<TMPro.TMP_InputField>();
                    if (tmp != null && tmp.isFocused) return true;
                }
                catch { }
                return false;
            }
            catch { return false; }
        }
    }
}
