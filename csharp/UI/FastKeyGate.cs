/// <summary>
/// FastKeyGate —— **真正的快捷键闸门**。这次不是猜，是元数据里查出来的。
///
/// ============================ 怎么找到它的 ============================
/// 三轮失败后换方法：不再猜"哪个 Update 在轮询"，而是**把整程序集的元数据翻一遍**，
/// 直接找"游戏自己给快捷键起的名字"。
///   · `Assembly-CSharp.dll` 是 IL2CPP 代理程序集（方法体全是桩），但**类型/字段/属性/方法签名齐全**。
///   · 全程序集搜方法名 → **`MapWorldMgr.FastKey()` 是全游戏唯一一个含 "FastKey" 的方法**
///     （`re:Fast` 命中 51 条，只有这一条是快捷键；其余是 FastBlink/FastClose/PathSearchFast）。
///   · `MapWorldMgr` = 大地图世界管理器（`SceneMap.world` 持有它；字段有 `playerPoint` /
///     `PathSearch` / `IsCanMove` / `isStopMove` / `stopKeyMove` / `mapRoot` / `grids`）。
///     快捷键键名恰好全是 `mapPlayXXX` 前缀（大地图族），派发点在这里完全说得通。
///   · `FastKey` 是 **private 实例方法、零参数**，由同类的 `OnUpdate`（private）调用 —— 正是
///     "每帧轮询、内部逐个查键"的形状。它**只做派发**，不参与任何 UI 接线。
///   · 同一类型上还有一个 **`public static bool isEnableMap { get; }`**（getter 名里带 `Static`）
///     —— 一个**静态只读**的"大地图是否可用"闸。静态 = 全局 = 正是剧情/战斗那种"整个场景都不许
///     按快捷键"该用的东西。本类每帧采样它，**翻转时打一行**：那就是"游戏凭什么屏蔽快捷键"的直证。
///
/// ============================ 为什么这次不会再弄坏 UI ============================
/// 前两次翻车的共同点是**挂到了"接线/填充"方法上**：
///   · `UIBase.UpdateHandleInput` 整局只调用 **1 次** → 它是"把输入控件填好、按钮接上回调"的方法。
///     跳过它 ⇒ 面板空白 + 关闭按钮点不动（用户实证）。
///   · `UIMapMain.Update` / `UIOperationGroup.Update` / `InputBase.Update` 跳过无效 ⇒ 根本不是派发点。
/// 而 `FastKey()` 是**纯派发函数**：内部没有任何 UI 装配，跳过它 = 这一帧"没按过快捷键"，
/// 等价于玩家没按键。没有副作用面可破坏。
/// 另外：**ESC 不在键表里**（`DefaultKeys.json` 33 条无 keyID 27/13），所以拦截 FastKey 不会吃掉 ESC。
///
/// ============================ 行为 ============================
///   我们的界面开着（`HotkeyGate.ShouldBlock()`）→ `FastKey()` 被跳过 ⇒ 游戏不响应大地图快捷键。
///   我们的界面没开 → 原样放行 ⇒ 游戏行为一字不变。
/// 真机实测：`FastKey=820 / 跳过 820`，100% 拦下，且与 `UIMapMain.Update`
/// 计数 1:1 ⇒ 它确实每帧被调用一次，就是那个派发点。
/// 逃生开关：&lt;Mod根&gt;\_diag_no_fastkey.txt 存在 ⇒ 退回"只计数不拦截"，**改文件即时生效、不用重启**。
///
/// 【失败姿态】前缀里任何异常 → `return true`（放行）。宁可漏拦，绝不破坏游戏。
/// </summary>
#if AB_UI
using System;
using System.Reflection;
using System.Text;
using HarmonyLib;

namespace AgentLoopBridge
{
    internal static class FastKeyGate
    {
        /// <summary>挂载结果摘要（Init 打一行，也进诊断行）。</summary>
        internal static string InstallReport = "(未执行)";

        /// <summary>我们的界面开着时，`FastKey` 被调用的次数（= 游戏本会响应多少次快捷键）。</summary>
        internal static int Hits;

        /// <summary>其中被真正拦下的次数。</summary>
        internal static int Skips;

        /// <summary>`MapWorldMgr.isEnableMap` 的最新值（"?"=读不到）。</summary>
        internal static string EnableMapText = "?";

        private static bool _installed;
        private static HarmonyLib.Harmony _harmony;

        private static PropertyInfo _isEnableMap;
        private static bool _enableMapSeen;
        private static bool _enableMapLast;
        private static int _sampleTimer;
        private const int SampleEveryFrames = 10;

        /// <summary>拦截日志额度：前 2 次逐条打（当场证明拦对了地方），之后每 <see cref="SkipLogEvery"/>
        /// 次补一行（长期金丝雀，防"补丁悄悄失效"）。常态每局 2~3 行。</summary>
        private static int _skipLogLeft = 2;
        private const int SkipLogEvery = 500;

        internal static void EnsureInstalled()
        {
            if (_installed) return;
            _installed = true;

            var note = new StringBuilder();
            try
            {
                _harmony = new HarmonyLib.Harmony("AgentLoopBridge.FastKeyGate");

                var t = AccessTools.TypeByName("MapWorldMgr");
                if (t == null)
                {
                    InstallReport = "MapWorldMgr 类型未找到（本版无法拦截）";
                    ModMain.P("[FastKeyGate] " + InstallReport);
                    return;
                }
                note.Append("MapWorldMgr=有;");

                // ---- 只读探针：静态属性 isEnableMap ----
                try
                {
                    _isEnableMap = t.GetProperty("isEnableMap",
                        BindingFlags.Public | BindingFlags.Static);
                    note.Append(_isEnableMap != null ? "isEnableMap=可读;" : "isEnableMap=未找到;");
                }
                catch (Exception e) { note.Append("isEnableMap探测失败:").Append(Short(e.Message)).Append(';'); }

                // ---- 主目标：private 实例方法 FastKey() ----
                MethodInfo m = null;
                try
                {
                    m = t.GetMethod("FastKey",
                        BindingFlags.Public | BindingFlags.NonPublic |
                        BindingFlags.Instance | BindingFlags.DeclaredOnly);
                }
                catch (Exception e) { note.Append("FastKey查找失败:").Append(Short(e.Message)).Append(';'); }

                if (m == null)
                {
                    InstallReport = note + "FastKey 未找到（本版无法拦截）";
                    ModMain.P("[FastKeyGate] " + InstallReport);
                    return;
                }
                if (m.GetParameters().Length != 0)
                {
                    InstallReport = note + "FastKey 形参非 0（不敢拦）";
                    ModMain.P("[FastKeyGate] " + InstallReport);
                    return;
                }

                var pre = typeof(FastKeyGate).GetMethod(nameof(PrefixFastKey),
                    BindingFlags.Static | BindingFlags.NonPublic);
                _harmony.Patch(m, prefix: new HarmonyMethod(pre));
                note.Append("FastKey 前缀=已挂;");

                InstallReport = note + "★我们的界面开着时跳过 FastKey（大地图快捷键派发点）★"
                                + " 逃生开关=_diag_no_fastkey.txt";
                ModMain.P("[FastKeyGate] " + InstallReport);
            }
            catch (Exception e)
            {
                InstallReport = "整体失败: " + e.Message;
                ModMain.P("[FastKeyGate] 挂载失败（不影响其它补丁）: " + e);
            }
        }

        /// <summary>
        /// `MapWorldMgr.FastKey()` 的前缀。
        /// 我们界面开着 → 跳过（游戏这一帧当作"没按快捷键"）；否则原样走。
        /// </summary>
        private static bool PrefixFastKey()
        {
            try
            {
                if (!HotkeyGate.ShouldBlock()) return true;   // 我们没在用界面 → 一个字都不改

                Hits++;
                if (DiagSwitches.NoFastKeyGate)
                {
                    // 逃生开关开着：只观察。第一次命中打一行 —— 证明"就算不拦，这条接缝
                    // 也确实在被调用"，否则用户无法区分"开关生效了"和"接缝没接上"。
                    if (Hits == 1)
                        ModMain.P("[FastKeyGate] （_diag_no_fastkey.txt 开着：只观察不拦截）"
                                  + "FastKey 仍被调用，本次本可拦下；isEnableMap=" + EnableMapText);
                    return true;
                }

                Skips++;
                if (_skipLogLeft > 0)
                {
                    _skipLogLeft--;
                    ModMain.P("[FastKeyGate] 已拦下大地图快捷键派发 MapWorldMgr.FastKey（第 "
                              + Skips + " 次；isEnableMap=" + EnableMapText + "）");
                }
                else if (Skips % SkipLogEvery == 0)
                {
                    ModMain.P("[FastKeyGate] 累计已拦下 " + Skips + " 次大地图快捷键（isEnableMap="
                              + EnableMapText + "）");
                }
                return false;
            }
            catch { return true; }   // 宁可漏拦，绝不破坏游戏
        }

        /// <summary>
        /// 每帧采样 `isEnableMap`（内部节流）。**状态翻转时打一行**——这是"游戏自己凭什么
        /// 在剧情/战斗里屏蔽快捷键"的直接证据：玩家过一次剧情，这里就该出现一次 True→False。
        /// </summary>
        internal static void Sample()
        {
            if (_isEnableMap == null) return;
            if (--_sampleTimer > 0) return;
            _sampleTimer = SampleEveryFrames;
            try
            {
                object v = _isEnableMap.GetValue(null);
                bool b = v is bool && (bool)v;
                EnableMapText = b ? "True" : "False";
                if (!_enableMapSeen)
                {
                    _enableMapSeen = true;
                    _enableMapLast = b;
                    ModMain.P("[FastKeyGate] MapWorldMgr.isEnableMap 初值=" + EnableMapText);
                }
                else if (b != _enableMapLast)
                {
                    _enableMapLast = b;
                    ModMain.P("[FastKeyGate] ★isEnableMap " + (b ? "False→True" : "True→False")
                              + "★（游戏自己开关大地图快捷键的那道闸；对照剧情/战斗/开界面三种时机）");
                }
            }
            catch (Exception e)
            {
                _isEnableMap = null;   // 读不了就不再每帧试
                EnableMapText = "读取失败";
                ModMain.P("[FastKeyGate] isEnableMap 读取失败（已停采样）: " + Short(e.Message));
            }
        }

        private static string Short(string s)
        {
            if (string.IsNullOrEmpty(s)) return "";
            s = s.Replace('\r', ' ').Replace('\n', ' ');
            return s.Length > 120 ? s.Substring(0, 120) + "…" : s;
        }
    }
}
#endif // AB_UI
