// `world_ai_action` 分派判定核的单元测试（10-27）。
//
// 覆盖的是"这一调走哪条链"，也就是用户视角的两个分支：
//   solo     = 单独应战，玩家不参与、在旁边看
//   together = 一起上，玩家当发起方，队友（含 A）跟着进场
//
// 十组：
//   ① 不留 target（对象是玩家）⇒ 走老路，mode 留空
//   ② 留 target 但 mode 留空  ⇒ solo（默认）
//   ③ mode=solo + NPC target  ⇒ Solo
//   ④ mode=together + NPC target ⇒ Together
//   ⑤ 旧名 watch/join         ⇒ 归一成 solo/together（不静默走默认）
//   ⑥ 拼错的 mode             ⇒ 拒绝，且话说得能自纠
//   ⑦ spar + NPC target       ⇒ 一律拒绝（拿真战斗冒充切磋 = 说谎）
//   ⑧ 其余 op + NPC target    ⇒ 拒绝
//   ⑨ mode 非空但 target 留空 ⇒ 拒绝（静默忽略会让模型以为切到了 together）
//   ⑩ 大小写/空白             ⇒ 归一

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

        private static AiActionTargetCore.Verdict D(string op, string mode, bool npc)
        {
            return AiActionTargetCore.Decode(op, mode, npc);
        }

        private static void CheckRoute(string name, AiActionTargetCore.Verdict v,
                                       AiActionTargetCore.Route want, string wantMode = null)
        {
            bool ok = v.Route == want && v.Fail == null;
            if (ok && wantMode != null) ok = v.Mode == wantMode;
            Check(name, ok, "route=" + v.Route + " mode=" + (v.Mode ?? "(null)") + " fail=" + (v.Fail ?? "(null)"));
        }

        private static void CheckReject(string name, AiActionTargetCore.Verdict v, string mustContain)
        {
            bool ok = v.Route == AiActionTargetCore.Route.Reject
                      && v.Fail != null && v.Fail.Length > 0
                      && (mustContain == null || v.Fail.Contains(mustContain));
            Check(name, ok, "route=" + v.Route + " fail=" + (v.Fail ?? "(null)"));
        }

        private static int Main(string[] args)
        {
            Console.WriteLine("AiActionTargetCoreTests —— world_ai_action 分派判定");

            // ── ① 对象是玩家（target 留空 / 填玩家名）──
            CheckRoute("① attack, 无 target ⇒ 走老路", D("attack", "", false),
                       AiActionTargetCore.Route.ToPlayer, "");
            CheckRoute("① spar, 无 target ⇒ 走老路", D("spar", "", false),
                       AiActionTargetCore.Route.ToPlayer, "");
            CheckRoute("① lun_dao, 无 target ⇒ 走老路", D("lun_dao", "", false),
                       AiActionTargetCore.Route.ToPlayer, "");
            CheckRoute("① attack, 有 target 但解析成玩家 ⇒ 仍是老路",
                       D("attack", "", false), AiActionTargetCore.Route.ToPlayer, "");

            // ── ② mode 留空 + NPC target ⇒ solo（默认行为，不看也罢但必须钉住）──
            CheckRoute("② attack + NPC target + mode 空 ⇒ Solo", D("attack", "", true),
                       AiActionTargetCore.Route.Solo, "solo");
            CheckRoute("② attack + NPC target + mode=null ⇒ Solo", D("attack", null, true),
                       AiActionTargetCore.Route.Solo, "solo");

            // ── ③④ 两个正门 ──
            CheckRoute("③ mode=solo + NPC ⇒ Solo", D("attack", "solo", true),
                       AiActionTargetCore.Route.Solo, "solo");
            CheckRoute("④ mode=together + NPC ⇒ Together", D("attack", "together", true),
                       AiActionTargetCore.Route.Together, "together");

            // ── ⑤ 旧名归一（只在开发中出现过，但静默走默认就是坑）──
            CheckRoute("⑤ mode=watch ⇒ Solo", D("attack", "watch", true),
                       AiActionTargetCore.Route.Solo, "solo");
            CheckRoute("⑤ mode=join ⇒ Together", D("attack", "join", true),
                       AiActionTargetCore.Route.Together, "together");

            // ── ⑥ 拼错必须拒绝，不能静默走 solo ──
            CheckReject("⑥ mode=joinn ⇒ 拒绝", D("attack", "joinn", true), "mode 只能是");
            CheckReject("⑥ mode=together2 ⇒ 拒绝", D("attack", "together2", true), "mode 只能是");
            CheckReject("⑥ mode=单挑 ⇒ 拒绝", D("attack", "单挑", true), "mode 只能是");

            // ── ⑦ spar 以 NPC 为目标一律拒绝 ──
            CheckReject("⑦ spar + NPC ⇒ 拒绝", D("spar", "", true), "暂不支持");
            CheckReject("⑦ spar + NPC + solo ⇒ 拒绝", D("spar", "solo", true), "暂不支持");
            CheckReject("⑦ spar + NPC + together ⇒ 拒绝", D("spar", "together", true), "暂不支持");

            // ── ⑧ 其余 op 配 NPC 也拒绝 ──
            CheckReject("⑧ yao_yue + NPC ⇒ 拒绝", D("yao_yue", "", true), "暂不支持");
            CheckReject("⑧ shuang_xiu + NPC ⇒ 拒绝", D("shuang_xiu", "", true), "暂不支持");
            CheckReject("⑧ chuan_gong + NPC ⇒ 拒绝", D("chuan_gong", "", true), "暂不支持");

            // ── ⑨ mode 配空 target ⇒ 拒绝（静默忽略 = 模型以为切到了 together）──
            CheckReject("⑨ mode=solo, 无 target ⇒ 拒绝", D("attack", "solo", false), "只用于 target");
            CheckReject("⑨ mode=together, 无 target ⇒ 拒绝", D("attack", "together", false), "只用于 target");
            // 但拼错的 mode 仍先报"mode 只能是"（判据顺序：先认参数，再认语义）
            CheckReject("⑨ mode=xxx, 无 target ⇒ 先报参数错", D("attack", "xxx", false), "mode 只能是");

            // ── ⑩ 大小写 / 空白归一 ──
            CheckRoute("⑩ ' TOGETHER ' ⇒ Together", D("attack", " TOGETHER ", true),
                       AiActionTargetCore.Route.Together, "together");
            CheckRoute("⑩ 'Solo' ⇒ Solo", D("attack", "Solo", true),
                       AiActionTargetCore.Route.Solo, "solo");

            // ── 附加：NormalizeMode 直接测 ──
            Check("N1 NormalizeMode(null) ⇒ \"\"", AiActionTargetCore.NormalizeMode(null) == "");
            Check("N2 NormalizeMode(\"\") ⇒ \"\"", AiActionTargetCore.NormalizeMode("") == "");
            Check("N3 NormalizeMode(\"join\") ⇒ together", AiActionTargetCore.NormalizeMode("join") == "together");
            Check("N4 NormalizeMode(\"bogus\") ⇒ null", AiActionTargetCore.NormalizeMode("bogus") == null);

            Console.WriteLine();
            if (_fails.Count == 0)
            {
                Console.WriteLine("✅ 全过：" + _pass + " 项");
                return 0;
            }
            Console.WriteLine("❌ " + _fails.Count + " 项失败 / 共 " + (_pass + _fails.Count) + " 项");
            foreach (var f in _fails) Console.WriteLine("   · " + f);
            return 1;
        }
    }
}
