/// <summary>
/// AB 面板的存活判定 —— **唯一一份**。
///
/// 【为什么要单独一件】IL2CPP 里"对象还在不在"不能直接判：
///   · `Destroy` 之后拿到的托管代理是**假 null**（`== null` 为 true、但字段访问才炸）；
///   · GC 已经把原生对象收走时，**连判空都会抛**（`MissingReferenceException` / `AccessViolation` 家族）。
/// 三个面板原来各写一份：两个用 `IsAlive` 属性、一个用手写 try/catch。同一个坑，三种写法。
///
/// 【口径】僵尸一律返回 false **并把字段清空**（所以参数是 `ref`）——
/// 不清空的话下一次调用还会拿到同一个僵尸，日志里就是"每帧都异常、永远不自愈"。
/// </summary>
using System;
using UnityEngine;

namespace AgentLoopBridge
{
    internal static class AbPanelLifecycle
    {
        /// <summary>引用还活着吗？false = 没建 / 已 Destroy / 僵尸引用（顺带把 <paramref name="resident"/> 清空）。</summary>
        public static bool Alive<T>(ref T resident) where T : Component
        {
            T r = resident;
            if (r == null) return false;                     // 未创建 / 上次已清理
            try
            {
                if (r.gameObject == null)
                {
                    resident = null;                          // Destroy 后的 fake-null
                    // 11-20：这一步原来是**静默**的。"面板忽然再也开不出来"的第一现场就在这儿，
                    // 而旧实现什么都不说 ⇒ 只能靠猜。一行日志，代价可忽略（每份僵尸只打一次，
                    // 因为清空后下一帧起走上面那行 return false）。
                    try { ModMain.P("[AbPanelLifecycle] " + typeof(T).Name + " 宿主已 Destroy（fake-null）→ 丢弃重建"); }
                    catch { }
                    return false;
                }
                return true;
            }
            catch
            {
                resident = null;                             // GC 僵尸：判空即抛 ⇒ 丢弃重建
                try { ModMain.P("[AbPanelLifecycle] " + typeof(T).Name + " 宿主是 GC 僵尸（判空即抛）→ 丢弃重建"); }
                catch { }
                return false;
            }
        }
    }
}