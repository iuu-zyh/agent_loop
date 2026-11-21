// WindowLayoutCore 单测：盘上读出来的窗口版式还能不能用 / 夹进视口 / 变没变。
// 症状背景见 WindowLayoutCore.cs 文件头（"位置跨局记住"是 11-21 用户定案）。

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
            Console.WriteLine("=== WindowLayoutCore 单元测试 ===");

            // ---------------------------------------------------------------
            // ① Finite：NaN/Inf 会把 UGUI 布局整个算崩，必须挡在门口
            // ---------------------------------------------------------------
            Check("普通数可用", WindowLayoutCore.Finite(1.5f));
            Check("0 可用", WindowLayoutCore.Finite(0f));
            Check("负数可用（位置本来就可能是负的）", WindowLayoutCore.Finite(-800f));
            Check("NaN 不可用", !WindowLayoutCore.Finite(float.NaN));
            Check("+Inf 不可用", !WindowLayoutCore.Finite(float.PositiveInfinity));
            Check("-Inf 不可用", !WindowLayoutCore.Finite(float.NegativeInfinity));

            // ---------------------------------------------------------------
            // ② SanitizeSize：下限挡坏值，上限夹视口
            // ---------------------------------------------------------------
            float ow, oh;

            Check("出厂尺寸可用", WindowLayoutCore.SanitizeSize(1200f, 800f, 1920f, 1080f, out ow, out oh));
            Feq("出厂宽原样", ow, 1200f);
            Feq("出厂高原样", oh, 800f);

            // 小于下限 = 写坏了，整份作废（不是夹到下限 —— 夹会得到"半吊子"版式）
            Check("宽小于下限判废", !WindowLayoutCore.SanitizeSize(100f, 800f, 1920f, 1080f, out ow, out oh));
            Check("高小于下限判废", !WindowLayoutCore.SanitizeSize(1200f, 50f, 1920f, 1080f, out ow, out oh));
            Feq("判废时宽回出厂值", ow, WindowLayoutCore.DefaultW);
            Feq("判废时高回出厂值", oh, WindowLayoutCore.DefaultH);

            Check("NaN 宽判废", !WindowLayoutCore.SanitizeSize(float.NaN, 800f, 1920f, 1080f, out ow, out oh));
            Check("Inf 高判废", !WindowLayoutCore.SanitizeSize(1200f, float.PositiveInfinity, 1920f, 1080f, out ow, out oh));

            // 上限夹视口：1920 屏上 3000 宽的窗 = 看不见，必须夹
            Check("超视口宽可用（会被夹）", WindowLayoutCore.SanitizeSize(3000f, 800f, 1920f, 1080f, out ow, out oh));
            Feq("超视口宽夹到视口宽", ow, 1920f);
            Check("超视口高可用（会被夹）", WindowLayoutCore.SanitizeSize(1200f, 5000f, 1920f, 1080f, out ow, out oh));
            Feq("超视口高夹到视口高", oh, 1080f);

            // ★ 上限未知（读盘那一刻还没有画布尺寸）时**不许**夹 ★
            //   否则 4K 屏上存的 2000 宽会被出厂值 1200 砍掉，玩家每次开窗都被"缩回中号"。
            Check("上限传 0 = 未知，尺寸原样保留", WindowLayoutCore.SanitizeSize(2000f, 1400f, 0f, 0f, out ow, out oh));
            Feq("未知上限时 2000 宽不被砍", ow, 2000f);
            Feq("未知上限时 1400 高不被砍", oh, 1400f);
            Check("上限传 NaN 也当未知", WindowLayoutCore.SanitizeSize(2000f, 1400f, float.NaN, float.NaN, out ow, out oh));
            Feq("NaN 上限时 2000 宽不被砍", ow, 2000f);

            // 视口比下限还小（极端分辨率）：退回出厂上限，别夹出一个比下限还小的值
            Check("视口小于下限时不夹", WindowLayoutCore.SanitizeSize(1200f, 800f, 200f, 200f, out ow, out oh));
            Feq("极小视口下宽保持", ow, 1200f);

            // ---------------------------------------------------------------
            // ③ SanitizePos：只判数值可用（夹进视口要 Unity 的 rect，在 DragCore）
            // ---------------------------------------------------------------
            float px, py;
            Check("正常位置可用", WindowLayoutCore.SanitizePos(-120f, 35f, out px, out py));
            Feq("x 原样", px, -120f);
            Feq("y 原样", py, 35f);

            Check("NaN x 判废", !WindowLayoutCore.SanitizePos(float.NaN, 0f, out px, out py));
            Feq("判废时 x 归零", px, 0f);
            Feq("判废时 y 归零", py, 0f);
            Check("Inf y 判废", !WindowLayoutCore.SanitizePos(0f, float.PositiveInfinity, out px, out py));

            // 位置**不做**上下限判断：那取决于视口与窗口尺寸，纯算术层拿不到
            Check("离谱但有限的位置放行（由 DragCore 再钳）",
                  WindowLayoutCore.SanitizePos(-99999f, 99999f, out px, out py));
            Feq("离谱 x 原样透传", px, -99999f);

            // ---------------------------------------------------------------
            // ④ PosChanged / SizeChanged：去抖（没动就别写盘）
            // ---------------------------------------------------------------
            const float EPS = WindowLayoutCore.SameEpsilon;

            Check("完全相同 = 没变", !WindowLayoutCore.PosChanged(10f, 20f, 10f, 20f));
            Check("x 差小于 epsilon = 没变", !WindowLayoutCore.PosChanged(10f, 20f, 10f + EPS * 0.5f, 20f));
            Check("y 差小于 epsilon = 没变", !WindowLayoutCore.PosChanged(10f, 20f, 10f, 20f - EPS * 0.5f));
            Check("x 差大于 epsilon = 变了", WindowLayoutCore.PosChanged(10f, 20f, 10f + EPS * 2f, 20f));
            Check("y 差大于 epsilon = 变了", WindowLayoutCore.PosChanged(10f, 20f, 10f, 20f + EPS * 2f));
            Check("只差一点点的负方向也算没变", !WindowLayoutCore.PosChanged(10f, 20f, 10f - EPS * 0.5f, 20f));

            Check("尺寸相同 = 没变", !WindowLayoutCore.SizeChanged(1200f, 800f, 1200f, 800f));
            Check("宽变了 = 变了", WindowLayoutCore.SizeChanged(1200f, 800f, 1201f, 800f));
            Check("高变了 = 变了", WindowLayoutCore.SizeChanged(1200f, 800f, 1200f, 801f));

            // ---------------------------------------------------------------
            // ⑤ 常量口径（与预制体/其它模块的一致性）
            // ---------------------------------------------------------------
            Check("出厂宽 = 预制体 BG 的 1200", Math.Abs(WindowLayoutCore.DefaultW - 1200f) < 1e-3f);
            Check("出厂高 = 预制体 BG 的 800", Math.Abs(WindowLayoutCore.DefaultH - 800f) < 1e-3f);
            Check("最小宽 > 气泡最小宽 + 头像 + 安全边",
                  WindowLayoutCore.MinW > 80f + 6f + 96f + 24f * 2f,
                  "MinW=" + WindowLayoutCore.MinW);
            Check("最小高留得下顶栏+输入区", WindowLayoutCore.MinH > 60f + 110f + 100f);
            Check("去抖 epsilon 是小量（不该吞掉真实拖动）", WindowLayoutCore.SameEpsilon <= 2f);
            Check("文件名是 json", WindowLayoutCore.FileName.EndsWith(".json"));
            Check("文件名不与 config.json 撞名（那个归 Python 独占）",
                  WindowLayoutCore.FileName != "config.json");

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
