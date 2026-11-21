// 反射门面（Refl）行为断言。跑法：
//   python3 scripts/dev/run_csharp_tests.py --only ReflTests
//
// 每条断言都对应一个**静默失败面**：错了不会抛，只会在真机上表现为"某字段读不到"。
using System;
using System.Collections;
using System.Collections.Generic;
using System.Reflection;
using AgentLoopBridge;

namespace AgentLoopBridge.Tests
{
    // ---- 假对象：模拟 Il2Cpp 代理的三种形态 ----
    internal class Props
    {
        public string Name { get; set; }
        public int Hp { get; set; }
        public Props() { Name = "内"; Hp = 7; }
    }

    internal class BaseWithData
    {
        public string Data { get; set; }
        private int secret = 42;
        public int SecretOf(object _) { return secret; }      // 只为让字段"被使用"，避免警告
        public BaseWithData() { Data = "base"; }
    }

    internal class DerivedData : BaseWithData
    {
        public new string Data { get; set; }                   // new 藏基类同名属性
        public DerivedData() { Data = "derived"; }
    }

    internal class FieldsOnly
    {
        private int mIsDie = 1;
        public int Peek() { return mIsDie; }
    }

    internal static class Program
    {
        private static int _pass, _fail;

        private static void Check(bool ok, string what)
        {
            if (ok) { _pass++; return; }
            _fail++;
            Console.WriteLine("  ✗ " + what);
        }

        private static int Failures => _fail;

        public static int Main()
        {
            Console.WriteLine("ReflTests：反射门面的行为断言");

            // ---------- ① 读属性：本类型 / 继承 / new 隐藏 ----------
            var p = new Props();
            Check(Equals(Refl.Get(p, "Name"), "内"), "本类型属性读不到");
            Check(Equals(Refl.Get(p, "Hp"), 7), "本类型 int 属性读不到");
            Check(Equals(Refl.Get(new BaseWithData(), "Data"), "base"), "基类属性沿链读不到");
            // `new` 藏基类同名：必须取**最派生**那个，且不能抛（旧的 field/property 二档会二义）
            Check(Equals(Refl.Get(new DerivedData(), "Data"), "derived"), "new 隐藏成员没有取最派生那个");

            // ---------- ② 属性档看不见字段（这是设计，不是 bug） ----------
            ModMain.Clear();
            Check(Refl.Get(new FieldsOnly(), "mIsDie") == null, "属性档居然读到了私有字段");
            Check(ModMain.Lines.Count == 1 && ModMain.Lines[0].Contains("mIsDie"),
                  "属性档读不到字段时没有打日志");

            // ---------- ③ GetField：本类型私有字段 + 基类私有字段（mIsDie 的实际形态） ----------
            Check(Equals(Refl.GetField(new FieldsOnly(), "mIsDie"), 1), "GetField 读不到本类型私有字段");
            Check(Equals(Refl.GetField(new DerivedData(), "secret"), 42), "GetField 没有沿基类找到私有字段");
            // 反向：GetField 对属性应当返回 null（只给真正的字段用）
            Check(Refl.GetField(new Props(), "Name") == null, "GetField 居然读到了属性");

            // ---------- ④ 读不到：只记一次（同一 类型.成员） ----------
            ModMain.Clear();
            for (int i = 0; i < 3; i++) Refl.Get(p, "NoSuchMember");
            int missLines = CountLines("NoSuchMember");
            Check(missLines == 1, "同一成员重复读不到被记了 " + missLines + " 次（应为 1）");

            // ---------- ⑤ 值转换 ----------
            Check(Refl.Str(p, "NoSuch") == "", "Str 读不到时不是空串");
            Check(Refl.Str(p, "NoSuch", "?") == "?", "Str 丢了调用方兜底");
            Check(Refl.Str(p, "Hp") == "7", "Str 对非字符串属性没有转成字符串");
            Check(Refl.Bool(null, true), "Bool(null) 没有用兜底");
            Check(Refl.Bool(1), "Bool(1) 应为 true");
            Check(!Refl.Bool(0), "Bool(0) 应为 false");
            Check(Refl.Int(null) == -1, "Int(null) 兜底应为 -1");
            Check(Refl.Int("12") == 12, "Int(\"12\") 应能转");
            Check(Refl.Int(true) == 1, "Int(true) 应为 1");
            Check(Refl.Int(new object(), 9) == 9, "Int(不可转) 没有退到兜底");

            // ---------- ⑥ 缓存：同一 (类型,成员) 只解析一次 ----------
            int before = CacheCount();
            var q = new Props();
            for (int i = 0; i < 5; i++) Refl.Get(q, "Name");
            Check(CacheCount() - before <= 1, "同一成员被反复解析（缓存没生效）");

            // "没找到"也必须缓存 —— 否则每次调用都要重走一遍基类链
            int beforeMiss = CacheCount();
            Refl.Get(q, "NeverThere");
            Refl.Get(q, "NeverThere");
            Check(CacheCount() - beforeMiss == 1, "没找到的成员没有被缓存（每次都要重找）");

            // ---------- ⑦ 日志封顶（跑最后：会吃掉日志配额） ----------
            ModMain.Clear();
            for (int i = 0; i < 60; i++) Refl.Get(p, "Bulk" + i);
            int bulk = CountLines("[Refl] 读不到");
            Check(bulk <= 41, "日志封顶失效：打印了 " + bulk + " 行");
            Check(CountLines("上限") == 1, "到上限时没有那条一次性提示");

            Console.WriteLine("  " + (_fail == 0 ? "✅" : "❌") + " 通过 " + _pass + " 条，失败 " + _fail + " 条");
            return _fail == 0 ? 0 : 1;
        }

        private static int CountLines(string needle)
        {
            int n = 0;
            foreach (var l in ModMain.Lines) if (l.Contains(needle)) n++;
            return n;
        }

        /// <summary>读 Refl 的私有缓存字典大小（只读，不扰动用例）。</summary>
        private static int CacheCount()
        {
            var f = typeof(Refl).GetField("_cache",
                BindingFlags.Static | BindingFlags.NonPublic);
            var d = f != null ? f.GetValue(null) as IDictionary : null;
            return d == null ? -1 : d.Count;
        }
    }
}
