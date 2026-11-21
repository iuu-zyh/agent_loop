// UnitIdRegistryCore（名字→unitID 全局登记表的纯逻辑核心）单元测试。
//
// 被测的是什么：两张表（name→unitID / unitID→name）+ 跨存档校验 + 增删规则。
//
// 为什么值得单独钉：这一层**没有任何界面**，出错的症状全是静默的 ——
//   · 跨档污染：旧档的 unitID 被当成本档的权威。最坏情形不是"查不到"（那会自愈），
//     而是那个 id 在新档里**恰好是另一个人** ⇒ 静默错人（本工程最怕的形态）；
//   · 覆盖写不留痕：同一个名字在两个人之间来回翻，谁也没法察觉；
//   · 自愈误删：`Forget` 不校验旧值，会把**刚登记的正确值**删掉，之后一路按名猜；
//   · 两方向漂移：反查给出过期答案，"模型把 unitID 填进 target"时提示指向**上一个人**。
//
// ⚠ 诚实边界：这里只测核心。**文件读写 / 原子替换 / Mod 根解析**要碰 Unity 与真实磁盘，
//   WSL 里跑不起来 —— 那部分由 `scripts/dev/mutate_check_manual_identity.py` 的 `wide` 层
//   （源码契约）兜住。

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

        private static void Eq<T>(string name, T got, T want) =>
            Check(name, EqualityComparer<T>.Default.Equals(got, want),
                  "实际=" + got + " 期望=" + want);

        private static int Main()
        {
            Console.WriteLine("=== UnitIdRegistryCore（全局登记表纯逻辑）单元测试 ===");
            Section("空表语义", EmptySemantics);
            Section("登记与幂等", RememberIsIdempotent);
            Section("反查方向同步", ReverseIndexStaysInSync);
            Section("覆盖登记保留新值", OverwriteKeepsTheNewOne);
            Section("★查询顺带登记不覆盖", ObservedNeverOverwrites);
            Section("自愈只摘自己那条", ForgetOnlyRemovesItsOwnEntry);
            Section("★跨存档校验", CrossSaveIsRejected);
            Section("换存档重置", ResetClearsEverything);
            Section("脏输入容错", GarbageTolerated);
            Console.WriteLine();
            if (_fails.Count == 0)
            {
                Console.WriteLine("全部通过：" + _pass + " 项断言");
                return 0;
            }
            Console.WriteLine("失败 " + _fails.Count + " 项 / 通过 " + _pass + " 项");
            return 1;
        }

        private static void Section(string name, Action body)
        {
            try { body(); }
            catch (Exception e)
            {
                Check("[" + name + "] 抛异常", false, e.GetType().Name + ": " + e.Message);
            }
        }

        private static Dictionary<string, string> Map(params string[] kv)
        {
            var d = new Dictionary<string, string>();
            for (int i = 0; i + 1 < kv.Length; i += 2) d[kv[i]] = kv[i + 1];
            return d;
        }

        // ---------------------------------------------------------------- 用例

        private static void EmptySemantics()
        {
            var c = new UnitIdRegistryCore();
            Eq("未登记的名字 → 空串（不是 null/占位）", c.IdOf("张三"), "");
            Eq("未登记的 id → 空串", c.NameOf("ABC123"), "");
            Eq("空表 Count = 0", c.Count, 0);
            Eq("未加载时 World 为空", c.World, "");
            // 空参不许写进去："不知道"不许变成"空"
            Check("空名不登记", !c.Remember("", "ABC123"));
            Check("空 id 不登记", !c.Remember("张三", ""));
            Check("全空白不登记", !c.Remember("   ", "   "));
            Eq("空参调用后表仍为空", c.Count, 0);
            // 存进去的名字/id 必须**去空白**（否则"张三 "与"张三"会各占一条）
            Check("带空白的登记被接受", c.Remember("  张三  ", "  ABC123  "));
            Eq("名字去空白后能查到", c.IdOf("张三"), "ABC123");
            Eq("带空白也能查到", c.IdOf("  张三 "), "ABC123");
        }

        private static void RememberIsIdempotent()
        {
            var c = new UnitIdRegistryCore();
            Check("首次登记 → true", c.Remember("张三", "AAA"));
            Check("同值重复 → false（调用方据此不写盘）", !c.Remember("张三", "AAA"));
            Check("同值带空白重复 → 仍 false", !c.Remember(" 张三 ", "AAA"));
            Eq("表里仍只有一条", c.Count, 0 + 1);
        }

        private static void ReverseIndexStaysInSync()
        {
            var c = new UnitIdRegistryCore();
            c.Remember("张三", "AAA");
            c.Remember("李四", "BBB");
            Eq("反查张三", c.NameOf("AAA"), "张三");
            Eq("反查李四", c.NameOf("BBB"), "李四");
            Check("两方向自洽", c.IsConsistent());
            // 名字换 id：旧 id 的反查必须消失（那个 id 已经不是他了）
            c.Remember("张三", "CCC");
            Eq("换 id 后新 id 反查得到", c.NameOf("CCC"), "张三");
            Eq("★旧 id 的反查必须摘掉（否则提示会指向上一个人）", c.NameOf("AAA"), "");
            Check("换 id 后仍自洽", c.IsConsistent());
            Eq("Count 仍是 2（换 id 不是新增）", c.Count, 2);
        }

        private static void OverwriteKeepsTheNewOne()
        {
            var c = new UnitIdRegistryCore();
            c.Remember("益婉容", "AAA");
            Check("覆盖登记 → 返回 true（调用方据此打醒目日志）", c.Remember("益婉容", "BBB"));
            Eq("保留的是**后到**的那个", c.IdOf("益婉容"), "BBB");
            Eq("旧 id 反查已摘", c.NameOf("AAA"), "");
            Check("覆盖后仍自洽", c.IsConsistent());
        }

        /// <summary>★ 09-17 新增：查询来源的登记**绝不覆盖**已有值（`RememberIfAbsent`）。</summary>
        private static void ObservedNeverOverwrites()
        {
            var c = new UnitIdRegistryCore();
            Check("空表 → 顺带登记写进去了", c.RememberIfAbsent("李四", "AAA"));
            Eq("值与反查都对", c.IdOf("李四"), "AAA");
            Eq("反查同步", c.NameOf("AAA"), "李四");
            Check("同值再来 → false（幂等，一个字节都不动）", !c.RememberIfAbsent("李四", "AAA"));
            // ★ 核心：手握真身登记过之后，查询顺带看到的**不同** id 不许覆盖它
            c.Remember("李四", "BBB");                       // 真正的握手（后到者可信）
            Check("顺带登记不许覆盖", !c.RememberIfAbsent("李四", "AAA"));
            Eq("★保住的是握手那条", c.IdOf("李四"), "BBB");
            Check("反查没被写乱", c.NameOf("AAA") == "" && c.NameOf("BBB") == "李四");
            // 反向：先顺带登记，再来一次真握手 → 握手可以覆盖（那是它的语义）
            var c2 = new UnitIdRegistryCore();
            c2.RememberIfAbsent("王五", "AAA");
            Check("真握手可以覆盖顺带登记的那条", c2.Remember("王五", "CCC"));
            Eq("覆盖后是新值", c2.IdOf("王五"), "CCC");
            // 空值一律忽略（"不知道"不许变成"空"）
            Check("空名 → false", !c2.RememberIfAbsent("", "AAA"));
            Check("空 id → false", !c2.RememberIfAbsent("赵六", ""));
            Eq("都没写进去", c2.IdOf("赵六"), "");
            Check("顺带登记后仍自洽", c.IsConsistent() && c2.IsConsistent());
        }

        private static void ForgetOnlyRemovesItsOwnEntry()
        {
            var c = new UnitIdRegistryCore();
            c.Remember("张三", "AAA");
            Check("旧值对不上 → 不摘（防误删刚登记的正确值）", !c.Forget("张三", "ZZZ"));
            Eq("那条仍在", c.IdOf("张三"), "AAA");
            Check("旧值对上 → 摘掉", c.Forget("张三", "AAA"));
            Eq("摘掉后查不到", c.IdOf("张三"), "");
            Eq("反查也摘了", c.NameOf("AAA"), "");
            Check("重复摘 → false（幂等）", !c.Forget("张三", "AAA"));
            // 空 unitId = "不管当前值，摘掉这个名字"
            c.Remember("李四", "BBB");
            Check("空 unitId 明确要求摘除 → true", c.Forget("李四", ""));
            Eq("确实摘了", c.IdOf("李四"), "");
            // 摘一个不存在的 → false
            Check("摘不存在的 → false", !c.Forget("王五", "CCC"));
            Check("自愈后仍自洽", c.IsConsistent());
        }

        private static void CrossSaveIsRejected()
        {
            var c = new UnitIdRegistryCore();
            // 正常：文件里的存档 == 当前存档 ⇒ 接受
            Check("同存档 → 接受", c.Load("BOlQu6", Map("张三", "AAA"), "BOlQu6"));
            Eq("World 记下当前存档", c.World, "BOlQu6");
            Eq("条目读进来了", c.IdOf("张三"), "AAA");

            // ★ 核心防线：文件属于**别的**存档 ⇒ 整表作废，一条都不认
            var c2 = new UnitIdRegistryCore();
            Check("★跨存档 → 拒绝", !c2.Load("BOlQu6", Map("张三", "AAA"), "FJLlLl"));
            Eq("★拒绝后一条都没有（不许逐条挑：'哪几条还成立'没有判据）", c2.Count, 0);
            Eq("拒绝后 World 为空", c2.World, "");
            Eq("拒绝后按名解析回落（查不到）", c2.IdOf("张三"), "");

            // 文件里没写 world（老文件/手改）⇒ 同样拒绝：宁可空表，不可猜归属
            var c3 = new UnitIdRegistryCore();
            Check("文件 world 为空 → 拒绝", !c3.Load("", Map("张三", "AAA"), "BOlQu6"));
            Eq("拒绝后为空", c3.Count, 0);

            // 还没进档（nowWorld 为空）⇒ 拒绝：此时无法判定归属
            var c4 = new UnitIdRegistryCore();
            Check("当前存档未知 → 拒绝", !c4.Load("BOlQu6", Map("张三", "AAA"), ""));
            Eq("拒绝后为空", c4.Count, 0);

            // 接受后再被拒绝：旧内容必须整份清掉（不许残留上一个存档的条目）
            var c5 = new UnitIdRegistryCore();
            c5.Load("BOlQu6", Map("张三", "AAA"), "BOlQu6");
            Check("★切到另一档 → 拒绝", !c5.Load("BOlQu6", Map("张三", "AAA"), "FJLlLl"));
            Eq("★上一个存档的条目不许残留", c5.Count, 0);

            // units 缺失（空文件 / 只有 world）⇒ 接受但为空表（不是错误）
            var c6 = new UnitIdRegistryCore();
            Check("units 缺失 → 接受且为空", c6.Load("BOlQu6", null, "BOlQu6"));
            Eq("World 仍是当前档", c6.World, "BOlQu6");
            Eq("表为空", c6.Count, 0);
        }

        private static void ResetClearsEverything()
        {
            var c = new UnitIdRegistryCore();
            c.Load("BOlQu6", Map("张三", "AAA", "李四", "BBB"), "BOlQu6");
            c.Reset();
            Eq("Count 归零", c.Count, 0);
            Eq("World 归零", c.World, "");
            Eq("名字查不到", c.IdOf("张三"), "");
            Eq("反查也查不到", c.NameOf("AAA"), "");
            Check("重置后仍自洽", c.IsConsistent());
            // 重置后可以重新加载（换存档的路径就是 Reset + Load）
            Check("重置后可再加载", c.Load("FJLlLl", Map("云含", "YYY"), "FJLlLl"));
            Eq("新档条目可用", c.IdOf("云含"), "YYY");
        }

        private static void GarbageTolerated()
        {
            var c = new UnitIdRegistryCore();
            // 空白键值静默丢弃（不写占位、不抛）
            Check("脏项不影响加载", c.Load("BOlQu6", Map("  ", "AAA", "张三", "  ", "李四", "BBB"), "BOlQu6"));
            Eq("只有干净的一条进来", c.Count, 1);
            Eq("干净那条可用", c.IdOf("李四"), "BBB");
            // null 值不许炸（`Dictionary` 允许 null value；null **键**在 .NET 里根本放不进去，
            // 所以那个情形不存在，不测）
            var dirty = new Dictionary<string, string>();
            dirty["张三"] = null;
            var c2 = new UnitIdRegistryCore();
            Check("null 值不抛", c2.Load("BOlQu6", dirty, "BOlQu6"));
            Eq("null 值不进来", c2.Count, 0);
            // Snapshot 是**副本**：改它不许影响原表
            var c3 = new UnitIdRegistryCore();
            c3.Remember("张三", "AAA");
            var snap = c3.Snapshot();
            snap["张三"] = "HACKED";
            snap["新人"] = "NEW";
            Eq("Snapshot 是副本，改它不影响原表", c3.IdOf("张三"), "AAA");
            Eq("Snapshot 里加项不影响原表", c3.IdOf("新人"), "");
        }
    }
}
