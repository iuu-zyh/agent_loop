/// <summary>
/// AiActionTargetCore —— 「`world_ai_action` 这一调该走哪条链」的**判定核**（10-27 立；
/// 零 Unity / 零 Newtonsoft，与 `TeamJoinCore` / `BattleFlowCore` 同一套路）。
///
/// ## 它回答的唯一问题
/// 给定 `op` / `mode` / `target` 是不是 NPC，这一调该：
///   ① 走"打玩家"（对象留空，本来带着队友确认窗）；
///   ② 走 `solo`（A 与 B 单挑，玩家旁观）；
///   ③ 走 `together`（玩家当发起方，队友含 A 一起上）；
///   ④ 直接拒绝（并给出**能自纠**的话）。
///
/// ## 为什么值得单独一个核
/// 这几条判据错在真机上**全是静默的**：
///   · `mode` 拼错（或留给旧名）⇒ 静默走 solo，模型以为切到了 together；
///   · `spar` 配 NPC ⇒ 若落到 `NpcDuel`，那是**真战斗**（重伤/击杀/缴物），
///     却按"切磋"回话 ⇒ 玩家以为只是过招，可能已经有人被打死；
///   · `mode` 配空 target ⇒ 静默忽略，模型以为"请玩家一起上"，实际只有 NPC 自己动手。
/// 这三条都只能靠表驱动测出来，没法靠"进游戏打一次"稳定复现。
///
/// ## 一条刻意的口径
/// **`spar` 对 NPC 一律拒绝**（10-27 用户定）。不是没做，是做了会说谎：
/// `spar` 的语义是 `UnitActionRoleDrill`（不落重伤、不致死），
/// 而 `NpcDuel` 走的是 `npcFightUnits` + `IntoBattle(4001)` 的真战斗。
/// </summary>
internal static class AiActionTargetCore
{
    /// <summary>这一调该走哪条链。</summary>
    internal enum Route
    {
        /// <summary>原样走 `switch (op)` 的老路（对象是玩家）。</summary>
        ToPlayer = 0,
        /// <summary>A 与 B 单挑，玩家旁观（`NpcDuel`）。</summary>
        Solo = 1,
        /// <summary>玩家当发起方，队友含 A 一起上（`JoinPlayerAttack`）。</summary>
        Together = 2,
        /// <summary>拒绝，理由见 `Fail`。</summary>
        Reject = 3,
    }

    internal struct Verdict
    {
        internal Route Route;
        /// <summary>规范化后的 mode（`""` / `"solo"` / `"together"`）。</summary>
        internal string Mode;
        /// <summary>`Route == Reject` 时的**人话**原因（能自纠，别写"参数错误"）。</summary>
        internal string Fail;
    }

    /// <summary>把调用方给的 `mode` 规范化。旧名 `watch`/`join`（10-27 下午的开发名）一并认。</summary>
    /// <returns>规范化后的值；`null` = 不认识。</returns>
    internal static string NormalizeMode(string raw)
    {
        string m = (raw ?? "").Trim().ToLowerInvariant();
        if (m.Length == 0) return "";
        if (m == "watch") return "solo";
        if (m == "join") return "together";
        if (m == "solo" || m == "together") return m;
        return null;
    }

    /// <summary>
    /// 判定。`hasNpcTarget` = `target` 解析出了一个**非玩家**的对手方
    /// （接线层用 `ResolveCounterparty` 的结果判 —— 留空/填玩家名都是 false）。
    /// </summary>
    internal static Verdict Decode(string op, string modeRaw, bool hasNpcTarget)
    {
        var v = new Verdict();
        string m = NormalizeMode(modeRaw);
        if (m == null)
        {
            v.Route = Route.Reject;
            v.Mode = "";
            v.Fail = "mode 只能是 solo（单独应战，玩家旁观）或 together（一起上，玩家与队友参战）；留空 = solo";
            return v;
        }
        v.Mode = m;

        if (hasNpcTarget)
        {
            // `spar` 以 NPC 为目标一律拒绝 —— 理由见类注释（拿真战斗冒充切磋 = 说谎）
            if (op != "attack")
            {
                v.Route = Route.Reject;
                v.Fail = "world_ai_action(" + op + ") 的对象只能是玩家：暂不支持对该 NPC 动手（可先用言语相商）";
                return v;
            }
            // ★ 留空要归一成字面 ``solo``，不能原样返回空串 ★
            //   下游（`NpcDuel` 的结果、日志、`BattleOutcome` 的 data）都按字符串分流；
            //   空串会让"默认 solo"和"没给 mode"分不开，日志里也看不出走的是哪条。
            v.Mode = (m == "together") ? "together" : "solo";
            v.Route = (m == "together") ? Route.Together : Route.Solo;
            return v;
        }

        // 对象是玩家 ⇒ `mode` 没有意义。静默忽略会让模型以为切到了 together，
        // 失败形态是"NPC 说我们一起上、结果只有他自己动手"。明确拒。
        if (m.Length > 0)
        {
            v.Route = Route.Reject;
            v.Fail = "mode 只用于 target 填了另一个 NPC 时（solo=单独应战、玩家旁观；"
                   + "together=一起上、玩家与队友参战）；target 留空就是打玩家，本来就带着队友确认窗，不用 mode";
            return v;
        }
        v.Route = Route.ToPlayer;
        return v;
    }
}
