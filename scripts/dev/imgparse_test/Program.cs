using System;
using System.Collections.Generic;
using System.IO;

class Program
{
    static int fail = 0, pass = 0;
    static string dir;

    static void Case(string name, string input, string wantClean, string[] wantPaths)
    {
        string clean; List<string> paths;
        P.SplitPaths(input, out clean, out paths);
        bool ok = clean == wantClean && paths.Count == wantPaths.Length;
        if (ok) for (int i = 0; i < wantPaths.Length; i++) if (paths[i] != wantPaths[i]) ok = false;
        if (ok) { pass++; Console.WriteLine("  ok   " + name); }
        else
        {
            fail++;
            Console.WriteLine("  FAIL " + name);
            Console.WriteLine("       clean = [" + clean + "]  期望 [" + wantClean + "]");
            Console.WriteLine("       paths = [" + string.Join(" | ", paths) + "]");
            Console.WriteLine("       期望  = [" + string.Join(" | ", wantPaths) + "]");
        }
    }

    static void Main()
    {
        dir = Path.Combine(Path.GetTempPath(), "agent_loop_parsetest");
        Directory.CreateDirectory(dir);
        // 造真实文件：带空格的中文名（模拟"屏幕截图 2026-09-13 002402.png"）
        string a = Path.Combine(dir, "屏幕截图 2026-09-13 002402.png");
        string b = Path.Combine(dir, "第二张 图.jpg");
        string c = Path.Combine(dir, "no_space.png");
        string notimg = Path.Combine(dir, "readme.txt");
        File.WriteAllBytes(a, new byte[] { 1 }); File.WriteAllBytes(b, new byte[] { 1 });
        File.WriteAllBytes(c, new byte[] { 1 }); File.WriteAllBytes(notimg, new byte[] { 1 });
        string ghost = Path.Combine(dir, "不存在.png");

        Console.WriteLine("== 入口①：路径文本 ==");
        Case("整行裸路径", c, "", new[] { c });
        Case("整行带引号路径", "\"" + a + "\"", "", new[] { a });
        Case("整行中文引号路径", "\u201c" + a + "\u201d", "", new[] { a });
        Case("含空格路径", a, "", new[] { a });
        Case("正文+盘符尾段", "这张图是谁 " + a, "这张图是谁", new[] { a });
        Case("正文+引号路径", "看看这个 \"" + a + "\"", "看看这个", new[] { a });
        Case("文件不存在", ghost, ghost, new string[0]);
        Case("非图片后缀", notimg, notimg, new string[0]);

        Console.WriteLine("== 入口②：Ctrl+V 多张（引号+空格，单行也成立）==");
        Case("两张引号", P.QuotePath(a) + " " + P.QuotePath(b), "", new[] { a, b });
        Case("正文夹两张", "对比一下 " + P.QuotePath(a) + " 和 " + P.QuotePath(b), "对比一下  和", new[] { a, b });
        Case("引号内非图不误吞", "他说 \"你好\" 然后 " + P.QuotePath(c), "他说 \"你好\" 然后", new[] { c });

        Console.WriteLine("== 边界 ==");
        Case("空串", "", "", new string[0]);
        Case("只有空格", "   ", "", new string[0]);
        Case("落单引号", "半个 \" 引号 " + c, "半个 \" 引号", new[] { c });
        Case("多行混合", "第一行\n" + a + "\n第三行 " + c, "第一行\n第三行", new[] { a, c });
        Case("纯正文不误判", "今天天气不错，我们去打猎吧", "今天天气不错，我们去打猎吧", new string[0]);
        Case("数字开头不像盘符", "3:30 出发", "3:30 出发", new string[0]);

        Console.WriteLine();
        Console.WriteLine(fail == 0 ? $"全部通过 {pass}/{pass}" : $"失败 {fail}，通过 {pass}");
        Environment.Exit(fail == 0 ? 0 : 1);
    }
}
