// DragCore 单测：屏幕像素→画布单位换算 + 右中锚窗口的合法域钳制。
// 症状背景见 DragCore.cs 文件头（编辑器 1.0 缩放大屏全不显形，装机才炸）。

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

        private static void Feq(string name, float got, float want) =>
            Check(name, Math.Abs(got - want) < 1e-4f, "实际=" + got + " 期望=" + want);

        private static int Main()
        {
            Console.WriteLine("=== DragCore 单元测试 ===");

            // ① 缩放换算
            Feq("2x 缩放：100px → 50 单位", DragCore.ScreenToCanvas(100f, 2f), 50f);
            Feq("1x 直通", DragCore.ScreenToCanvas(100f, 1f), 100f);
            Feq("0.5x：100px → 200 单位", DragCore.ScreenToCanvas(100f, 0.5f), 200f);
            Feq("scaleFactor 坏（0）→ 原样不吞拖动", DragCore.ScreenToCanvas(100f, 0f), 100f);
            Feq("负位移同样换算", DragCore.ScreenToCanvas(-60f, 2f), -30f);

            // ② 钳制域（视口 1920×1080，窗 520×760，右中锚）
            float ox, oy;
            DragCore.ClampAnchored(-24f, 0f, 520f, 760f, 1920f, 1080f, out ox, out oy);
            Feq("默认位不动 x", ox, -24f); Feq("默认位不动 y", oy, 0f);

            DragCore.ClampAnchored(10f, 0f, 520f, 760f, 1920f, 1080f, out ox, out oy);
            Feq("右缘不许出屏右", ox, 0f);

            DragCore.ClampAnchored(-1600f, 0f, 520f, 760f, 1920f, 1080f, out ox, out oy);
            Feq("左缘不许出屏左（minX=520-1920）", ox, -1400f);

            DragCore.ClampAnchored(-24f, 500f, 520f, 760f, 1920f, 1080f, out ox, out oy);
            Feq("上缘贴屏顶（hi=(1080-760)/2）", oy, 160f);
            DragCore.ClampAnchored(-24f, -500f, 520f, 760f, 1920f, 1080f, out ox, out oy);
            Feq("下缘贴屏底", oy, -160f);

            // ③ 极端：窗口比视口大 → 贴边/居中锁死（宁可居中，不许漂出可抓区）
            DragCore.ClampAnchored(-9999f, 0f, 2000f, 760f, 1920f, 1080f, out ox, out oy);
            Feq("超宽窗贴右", ox, 0f);
            DragCore.ClampAnchored(0f, 777f, 520f, 2000f, 1920f, 1080f, out ox, out oy);
            Feq("超高窗锁 y=0", oy, 0f);

            // ④ 幂等：钳完再钳不许动
            DragCore.ClampAnchored(-800f, 90f, 520f, 760f, 1920f, 1080f, out ox, out oy);
            float ax = ox, ay = oy;
            DragCore.ClampAnchored(ox, oy, 520f, 760f, 1920f, 1080f, out ox, out oy);
            Check("幂等", Math.Abs(ox - ax) < 1e-6f && Math.Abs(oy - ay) < 1e-6f, ox + "," + oy);

            // ============================================================
            // ⑤ 居中点锚（AB 预制体版 BG）—— 11-21 实机 bug 的正主
            //
            //    症状：拿右中锚公式 `ClampAnchored` 去钳居中锚的 BG（1200×800 @ 1920×1080），
            //    算出 x ∈ [-720, 0]，正确是 ±360 ⇒ **只能往左拖，往右一格都拖不动**。
            //    下面这一组就是"正确公式应该给出什么"，每一条都对着症状。
            // ============================================================
            const float VW = 1920f, VH = 1080f;
            const float WW = 1200f, WH = 800f;

            // 出厂位（居中，(0,0)）必须原样放行 —— 旧公式下 (0,0) 恰好在上界，看不出错
            DragCore.ClampToViewport(0f, 0f, WW, WH, VW, VH, 0.5f, 0.5f, 0.5f, 0.5f, out ox, out oy);
            Feq("居中锚出厂位 x 不动", ox, 0f);
            Feq("居中锚出厂位 y 不动", oy, 0f);

            // ★ 核心回归：往右拖必须能拖 ★ 旧公式在这里会钳回 0
            DragCore.ClampToViewport(200f, 0f, WW, WH, VW, VH, 0.5f, 0.5f, 0.5f, 0.5f, out ox, out oy);
            Feq("居中锚往右 200 放行（旧公式会钳回 0）", ox, 200f);

            // 右缘贴屏右：右缘 = 200 + 600 = 800，屏右 = 960 ⇒ 还能再往右
            DragCore.ClampToViewport(360f, 0f, WW, WH, VW, VH, 0.5f, 0.5f, 0.5f, 0.5f, out ox, out oy);
            Feq("居中锚右缘恰好贴屏右（(1920-1200)/2）", ox, 360f);
            DragCore.ClampToViewport(999f, 0f, WW, WH, VW, VH, 0.5f, 0.5f, 0.5f, 0.5f, out ox, out oy);
            Feq("居中锚右缘不许出屏右", ox, 360f);

            // 左缘贴屏左：对称
            DragCore.ClampToViewport(-360f, 0f, WW, WH, VW, VH, 0.5f, 0.5f, 0.5f, 0.5f, out ox, out oy);
            Feq("居中锚左缘恰好贴屏左", ox, -360f);
            DragCore.ClampToViewport(-999f, 0f, WW, WH, VW, VH, 0.5f, 0.5f, 0.5f, 0.5f, out ox, out oy);
            Feq("居中锚左缘不许出屏左", ox, -360f);

            // 纵向同理：(1080-800)/2 = 140
            DragCore.ClampToViewport(0f, 140f, WW, WH, VW, VH, 0.5f, 0.5f, 0.5f, 0.5f, out ox, out oy);
            Feq("居中锚上缘贴屏顶", oy, 140f);
            DragCore.ClampToViewport(0f, -500f, WW, WH, VW, VH, 0.5f, 0.5f, 0.5f, 0.5f, out ox, out oy);
            Feq("居中锚下缘贴屏底", oy, -140f);

            // ⑥ 幂等（居中锚）：钳完再钳不许动，且必须落在界内
            DragCore.ClampToViewport(-5000f, 5000f, WW, WH, VW, VH, 0.5f, 0.5f, 0.5f, 0.5f, out ox, out oy);
            float bx = ox, by = oy;
            Check("居中锚极端位被夹进界内", Math.Abs(ox) <= 360f + 1e-3f && Math.Abs(oy) <= 140f + 1e-3f, ox + "," + oy);
            DragCore.ClampToViewport(ox, oy, WW, WH, VW, VH, 0.5f, 0.5f, 0.5f, 0.5f, out ox, out oy);
            Check("居中锚幂等", Math.Abs(ox - bx) < 1e-6f && Math.Abs(oy - by) < 1e-6f, ox + "," + oy);

            // ⑦ 窗口比视口大 → 取中点（居中），而不是贴死某一侧
            //    1920 视口里塞 2400 宽的窗：(1920-2400)/2 = -240 ⇒ 居中时 pos = 0
            DragCore.ClampToViewport(500f, 0f, 2400f, WH, VW, VH, 0.5f, 0.5f, 0.5f, 0.5f, out ox, out oy);
            Feq("超宽窗居中（不贴边）", ox, 0f);

            // ⑧ 非居中锚：贴右锚（anchor=1）时 pos 的界整体左移半个屏
            //    右锚 pos=-24（代码版 Window 那种）⇒ 右缘 = 960-24+... 这里只验"界跟着锚走"
            DragCore.ClampToViewport(-24f, 0f, WW, WH, VW, VH, 1f, 0.5f, 1f, 0.5f, out ox, out oy);
            Feq("右锚 pos=-24 放行（右缘正好在屏内）", ox, -24f);
            DragCore.ClampToViewport(500f, 0f, WW, WH, VW, VH, 1f, 0.5f, 1f, 0.5f, out ox, out oy);
            Feq("右锚不许把右缘推出屏右", ox, 0f);

            Console.WriteLine();
            if (_fails.Count == 0)
            {
                Console.WriteLine("全部通过：" + _pass + " 项断言");
                return 0;
            }
            Console.WriteLine("失败 " + _fails.Count + " 项 / 通过 " + _pass + " 项");
            return 1;
        }
    }
}
