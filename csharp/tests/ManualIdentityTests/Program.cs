// ContactStore 手动好友「精确身份」镜像的单元测试。
//
// 被测的是什么：`ContactStore` 里那一对平行的 name 集合 / name→unitID 字典，以及
// `SetManualContact(name, add, unitId)` 对它们的增删规则。
//
// 为什么值得单独钉：这一层**没有任何界面**，出错的症状全是静默的 ——
//   · 该记住的 unitID 没记住 → 消费方退回按名全图猜，重名时把**别人的档案**端给模型
//     （不报错、不缺字段，`UnitLookup` 文件头记的就是这次真机事故）；
//   · 不该清的时候清了 → 一次不带 ID 的调用就把精确身份抹掉，而名字还在、按钮还亮，
//     界面上完全看不出来。
//
// ⚠ 诚实边界：这里只测**镜像自身的规则**。"谁在什么时候把真身的 unitID 交给它"（接线）
//   依赖 Unity/UI，WSL 里跑不起来 —— 那部分由 Python 侧源码契约测试
//   `tests/test_contact_ui_contract.py` 锁在真实 `ContactDuty.cs` / `ContactStore.cs` 上。
//   两边合起来才完整。

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
            Console.WriteLine("=== ContactStore 手动好友身份镜像 单元测试 ===");
            Section("回包整体替换", ReplaceAll);
            Section("精确 ID 的读写", UnitIdReadWrite);
            Section("不传 ID 不清旧值", NoIdDoesNotClobber);
            Section("移除好友同步清 ID", RemoveClears);
            Section("变更通知", ChangedEvent);
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
                // 一节里抛了也要如实记成"这一节失败"并继续跑后面的小节 —— 崩在第一行会让
                // 后面全都没跑，看起来像"测试通过"（ToolFoldPolicyTests 踩过这个坑）
                Check("★ 小节抛异常：" + name, false, e.GetType().Name + ": " + e.Message);
            }
        }

        /// <summary>用例间复位：`SetManualNames` 是唯一的整体替换口，空表即清空两个集合。</summary>
        private static void Reset()
        {
            ContactStore.SetManualNames(new List<KeyValuePair<string, string>>());
        }

        private static List<KeyValuePair<string, string>> Pairs(params string[] nameIdPairs)
        {
            var list = new List<KeyValuePair<string, string>>();
            for (int i = 0; i + 1 < nameIdPairs.Length; i += 2)
                list.Add(new KeyValuePair<string, string>(nameIdPairs[i], nameIdPairs[i + 1]));
            return list;
        }

        // ------------------------------------------------------------------
        // 回包整体替换（list_contacts → SetManualNames）
        // ------------------------------------------------------------------

        private static void ReplaceAll()
        {
            Reset();
            Eq("空表：不是好友", ContactStore.IsManualContact("甲"), false);
            Eq("空表：无精确 ID", ContactStore.ManualUnitId("甲"), null);

            // 一条有 unitID（新记录）、一条没有（本功能上线前加的老记录）
            ContactStore.SetManualNames(Pairs("甲", "AAA", "乙", ""));
            Eq("新记录是好友", ContactStore.IsManualContact("甲"), true);
            Eq("新记录带精确 ID", ContactStore.ManualUnitId("甲"), "AAA");
            Eq("老记录**也**是好友", ContactStore.IsManualContact("乙"), true);
            Eq("老记录没有精确 ID（不是空串、是 null）", ContactStore.ManualUnitId("乙"), null);

            // 整体替换语义：旧条目连同它的 ID 一起消失
            ContactStore.SetManualNames(Pairs("丙", "CCC"));
            Eq("被替换掉的 甲 不再是好友", ContactStore.IsManualContact("甲"), false);
            Eq("被替换掉的 甲 的 ID 也清掉了", ContactStore.ManualUnitId("甲"), null);
            Eq("被替换掉的 乙 不再是好友", ContactStore.IsManualContact("乙"), false);
            Eq("新来的 丙 是好友", ContactStore.IsManualContact("丙"), true);
            Eq("新来的 丙 带 ID", ContactStore.ManualUnitId("丙"), "CCC");
        }

        // ------------------------------------------------------------------
        // 精确 ID 的读写（点击加好友时的本地即时生效）
        // ------------------------------------------------------------------

        private static void UnitIdReadWrite()
        {
            Reset();
            ContactStore.SetManualContact("甲", true, "AAA");
            Eq("带 ID 加好友 → 是好友", ContactStore.IsManualContact("甲"), true);
            Eq("带 ID 加好友 → ID 记住了", ContactStore.ManualUnitId("甲"), "AAA");

            // 换成新 ID（存档变动后纠正失效的旧 id）：以最后一次为准
            ContactStore.SetManualContact("甲", true, "BBB");
            Eq("再带一个新 ID → 覆盖旧值", ContactStore.ManualUnitId("甲"), "BBB");

            // 边界：不该抛，一律 null
            Eq("未记录的名字 → null", ContactStore.ManualUnitId("查无此人"), null);
            Eq("null 名字 → null（不抛）", ContactStore.ManualUnitId(null), null);
            Eq("空串名字 → null（不抛）", ContactStore.ManualUnitId(""), null);

            // 不带 ID 加好友：是好友，但没有精确身份（消费方只能按名解析）
            Reset();
            ContactStore.SetManualContact("乙", true);
            Eq("不带 ID 加好友 → 仍是好友", ContactStore.IsManualContact("乙"), true);
            Eq("不带 ID 加好友 → 没有精确身份", ContactStore.ManualUnitId("乙"), null);
        }

        // ------------------------------------------------------------------
        // 不传 ID 不清旧值 —— 这条最容易被"顺手写的赋值"破坏，且症状完全静默
        // ------------------------------------------------------------------

        private static void NoIdDoesNotClobber()
        {
            Reset();
            ContactStore.SetManualContact("甲", true, "AAA");

            ContactStore.SetManualContact("甲", true);
            Eq("显式不传 ID（默认参数）→ 旧 ID 保留", ContactStore.ManualUnitId("甲"), "AAA");

            ContactStore.SetManualContact("甲", true, null);
            Eq("显式传 null → 旧 ID 保留", ContactStore.ManualUnitId("甲"), "AAA");

            ContactStore.SetManualContact("甲", true, "");
            Eq("显式传空串 → 旧 ID 保留", ContactStore.ManualUnitId("甲"), "AAA");

            // 整体替换时"空 ID"同样不许覆盖 —— 但这条由 SetManualNames 的语义决定：
            // 它是**整体替换**，回包说没有就是没有（真相在 Python，不由 C# 缓存兜底）。
            ContactStore.SetManualNames(Pairs("甲", ""));
            Eq("整体替换回包说没有 → 就该没有（不拿缓存硬撑）",
               ContactStore.ManualUnitId("甲"), null);
        }

        // ------------------------------------------------------------------
        // 移除好友
        // ------------------------------------------------------------------

        private static void RemoveClears()
        {
            Reset();
            ContactStore.SetManualContact("甲", true, "AAA");
            ContactStore.SetManualContact("甲", false);
            Eq("移除后不再是好友", ContactStore.IsManualContact("甲"), false);
            Eq("移除后 ID 同步清掉（防「幽灵身份」）", ContactStore.ManualUnitId("甲"), null);

            // 幂等：移除不存在的名字不该抛
            ContactStore.SetManualContact("查无此人", false);
            Check("移除不存在的名字不抛异常", true);

            // 移除后再加回来：ID 要按新的来，不能记着旧的
            ContactStore.SetManualContact("甲", true, "ZZZ");
            Eq("移除后再加 → 用新 ID", ContactStore.ManualUnitId("甲"), "ZZZ");
        }

        // ------------------------------------------------------------------
        // 变更通知：视图层（通讯录行 / NPC 面板按钮文案）靠它重渲
        // ------------------------------------------------------------------

        private static int _changed;
        private static void OnChanged() { _changed++; }

        private static void ChangedEvent()
        {
            Reset();
            _changed = 0;
            ContactStore.Changed += OnChanged;
            try
            {
                ContactStore.SetManualContact("甲", true, "AAA");
                Eq("新增（带 ID）→ 通知一次", _changed, 1);

                ContactStore.SetManualContact("甲", true, "AAA");
                Eq("同名同 ID 重复加 → 纯 no-op，不通知", _changed, 1);

                ContactStore.SetManualContact("甲", true, "BBB");
                Eq("同名但 ID 变了 → 必须通知（界面上看不出差别，但身份变了）", _changed, 2);

                ContactStore.SetManualContact("查无此人", false);
                Eq("移除不存在的名字 → 不通知", _changed, 2);

                ContactStore.SetManualContact("甲", false);
                Eq("移除存在的名字 → 通知一次", _changed, 3);

                ContactStore.SetManualNames(Pairs("丙", "CCC"));
                Eq("整体替换 → 通知一次", _changed, 4);
            }
            finally
            {
                ContactStore.Changed -= OnChanged;   // 静态事件：不摘会跨用例累计
            }
        }
    }
}
