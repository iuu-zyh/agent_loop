// 测试用桩：`Refl` 只依赖 `ModMain.P` 一个日志口，把它换成"记进列表"就能断言日志行为。
// 主工程的 ModMain.cs 不参与本工程编译（它拖 Unity/IL2CPP 引用），所以这里不冲突。
using System.Collections.Generic;

namespace AgentLoopBridge
{
    internal static class ModMain
    {
        public static readonly List<string> Lines = new List<string>();
        public static void P(string msg) { Lines.Add(msg); }
        public static void Clear() { Lines.Clear(); }
    }
}
