"""NPC 对战三条分支的「同格」口径契约（10-28 建）。

## 为什么值得有

「同格」是**我们自己的**语义约束，不是引擎要求 —— 这一点 10-28 反编确认过：

  · `WorldUnitBase::CreateAction`@0x32B8D40 只是 `IsCreate` → `Create` 的调度；
  · `UnitActionRoleAttack::IsCreate`@0x3702150 只调父类 + 判攻击力/禁用；
  · `UnitActionRoleToUnitBase::IsCreate`@0x37B8430（534 行）全是剧情包/行动类型判定，
    **没有任何坐标比较**；
  · 我们走 `dramaUnitBattle_*` 观战副本时，`SpecialBattle79::OneIntoRoomEnd` 是拿
    `BattleMapMgr::PointToWorld` 在**战场上**重新摆位，与地图格子无关。

⇒ 引擎不拦，所以**只能由我们拦**。而拦不拦这件事写反了/漏了一半，在真机上都是静默的：
漏了 = 隔着几个州也能开打（玩家觉得荒谬但没人报错）；写死了 = `solo` 触发率骤降
（NPC 平时各在各的城里），且没有一处日志会说"是这条判据挡的"。

## 三条分支的口径（用户 10-28 定）

| 分支 | 谁跟谁同格 | 理由 |
|---|---|---|
| `solo`（观战） | **A 与 B** | 两个人得在一处才谈得上动手 |
| `together`（一起上） | **玩家与 B**、且 **A 与 B** | A 是请人者，玩家和 B 打、A 不在场就不是"A 请你一起上" |
| 打玩家（target 留空） | 玩家与 A | 老链不变 |

## 开关

`_diag_no_duel_samegrid.txt` 存在 ⇒ 退回 10-26 行为（引擎本就不要求）。
留开关的理由：`solo` 加了这条后触发率会降，万一实测下来功能因此几乎用不上，
放个空文件就能退回，**不必重新编译**。
"""

from __future__ import annotations

import re
from pathlib import Path
import _cs_tools  # 工具层多文件聚合（见 tests/_cs_tools.py）

ROOT = Path(__file__).resolve().parents[1]
CS = ROOT / "csharp"


def _read(rel: str) -> str:
    return (CS / rel).read_text(encoding="utf-8")


def _strip_comments(src: str) -> str:
    """剥掉 `//` 行注释 —— 判据只认真代码，注释里写着 SameGrid 不算数。"""
    out = []
    for ln in src.splitlines():
        i = ln.find("//")
        out.append(ln[:i] if i >= 0 else ln)
    return "\n".join(out)


# ══════════════════════════════════════════════════════════════════
# ① solo：A 与 B 必须同格
# ══════════════════════════════════════════════════════════════════

def test_solo_requires_same_grid():
    """`NpcDuel.AttackCore` 必须有「A 与 B 同格」这一条真判据（不是只写进日志）。"""
    code = _strip_comments(_read("drama/NpcDuel.cs"))
    # 找到 AttackCore 的方法体
    i = code.index("private static JObject AttackCore(")
    j = code.index("\n        }", i)
    body = code[i:j]

    assert "SameGrid(actor, target)" in body, (
        "AttackCore 里没有 `SameGrid(actor, target)` —— solo 不再要求同格了？"
        "这是 10-28 用户明确要加的语义约束（引擎本就不要求，所以只能由我们拦）")
    assert re.search(r"if\s*\([^)]*SameGrid\(actor,\s*target\)[^)]*\)\s*\n?\s*return\s+Fail", body), (
        "同格判据没有接 `return Fail(...)` —— 只算不变布尔值等于没拦")
    # 必须带关闭开关（否则实测下来触发率过低时只能重新编译）
    assert "NoDuelSameGrid" in body, (
        "同格判据没接 `DiagSwitches.NoDuelSameGrid` 开关 —— "
        "实测发现 solo 触发率过低时要能放个空文件退回，而不是重新编译")


def test_solo_same_grid_message_is_self_correcting():
    """拒绝文案要能自纠（说清"不同格"且给出下一步），不能只说"失败"。"""
    src = _read("drama/NpcDuel.cs")
    # 文案是拼接的（`"你与" + targetName + "不同格，…"`）⇒ 不能只抓一个字符串字面量，
    # 要抓**整行 Fail 调用**再检查里面有没有那两个要素。
    lines = [ln.strip() for ln in src.splitlines()
             if "Fail(" in ln and "不同格" in ln]
    assert lines, "solo 的同格拒绝文案里没有「不同格」三个字"
    line = lines[0]
    assert "targetName" in line or "对方" in line, (
        f"文案没带对手名（模型不知道是跟谁不同格）：{line}")
    assert "走到" in line, (
        f"文案没说下一步怎么自救：{line}（模型只能照抄这句给玩家，得让它能自纠）")


# ══════════════════════════════════════════════════════════════════
# ② together：玩家与 B、且 A 与 B
# ══════════════════════════════════════════════════════════════════

def test_together_requires_player_and_caller_same_grid():
    """`JoinPlayerAttack` 必须同时判「玩家 vs B」与「A vs B」两对。"""
    code = _strip_comments(_cs_tools.tool_src())
    i = code.index(" JObject JoinPlayerAttack(")
    j = code.index(" int? DeferNativeUnitAction(", i)
    body = code[i:j]

    assert "SameGrid(player, foe)" in body, "together 没判「玩家与 B 同格」"
    assert "SameGrid(caller, foe)" in body, (
        "together 没判「A 与 B 同格」—— 玩家和 B 在一处、A 在几个州外，"
        "那就不是「A 请你一起上」，而是玩家替 A 出头")
    assert body.count("return Fail(") >= 2, "两对同格判据里有一对没接 return Fail"


def test_together_caller_check_uses_the_switch():
    """A vs B 那条也要跟 solo 同一个开关（一处能关、另一处关不掉 = 两个真相）。"""
    code = _strip_comments(_cs_tools.tool_src())
    i = code.index(" JObject JoinPlayerAttack(")
    j = code.index(" int? DeferNativeUnitAction(", i)
    body = code[i:j]
    k = body.index("SameGrid(caller, foe)")
    window = body[max(0, k - 200):k]
    assert "NoDuelSameGrid" in window, (
        "A vs B 那条没有 `DiagSwitches.NoDuelSameGrid` 开关 —— "
        "与 solo 口径不一致，关了一处另一处还拦着")


# ══════════════════════════════════════════════════════════════════
# ③ 开关本身
# ══════════════════════════════════════════════════════════════════

def test_switch_exists_and_defaults_to_enforcing():
    """开关得真的存在，且语义是「**关掉**同格」而不是「打开」——
    文件缺失时必须是**要求同格**（安全默认），否则新装的人拿到的是旧行为。"""
    src = _read("app/DiagSwitches.cs")
    assert "_diag_no_duel_samegrid.txt" in src, "开关文件名不存在"
    assert re.search(r"public static bool NoDuelSameGrid\s*\{\s*get;\s*private set;\s*\}", src), (
        "`NoDuelSameGrid` 属性没有可读的公开访问器")
    # 默认 false（= 要求同格）：判据写成 `!NoDuelSameGrid && !SameGrid(...)`
    duel = _strip_comments(_read("drama/NpcDuel.cs"))
    assert "!DiagSwitches.NoDuelSameGrid && !SameGrid(actor, target)" in duel, (
        "默认方向反了：应当是「没放开关时要求同格」（`!NoDuelSameGrid && !SameGrid`），"
        "否则新装用户拿到的还是旧行为")


# ══════════════════════════════════════════════════════════════════
# ④ 别再声称"引擎要求同格"
# ══════════════════════════════════════════════════════════════════

def test_no_claim_that_engine_enforces_same_grid():
    """10-28 反编已定：`IsCreate` 里**没有**坐标比较。

    早先几处注释写着「`ToUnitBase::IsCreate` 会判（同格）」——那是错的，
    留着会让下一个人以为"引擎已经拦了，我们不用管"，于是把判据删掉。
    """
    bad = []
    for f in CS.rglob("*.cs"):
        p = f.as_posix()
        if "/obj/" in p or "/bin/" in p or "/tests/" in p:
            continue
        txt = f.read_text(encoding="utf-8", errors="replace")
        for ln in txt.splitlines():
            if "IsCreate" in ln and "同格" in ln and ("会判" in ln or "要求同格" in ln):
                bad.append(f"{f.name}: {ln.strip()[:90]}")
    assert not bad, (
        "还有人声称引擎 `IsCreate` 会判同格 —— 反编 0x37B8430（534 行）里没有坐标比较：\n"
        + "\n".join(bad))


# ══════════════════════════════════════════════════════════════════
# ⑤ `mode` 必须真的到模型手上（10-28 真机 bug）
# ══════════════════════════════════════════════════════════════════

def test_mode_is_captured_before_reset():
    """`BattleOutcome.Resolve` 里 `mode` 必须在 `Reset()` **之前**取出来。

    事故形态（10-28 真机）：`Reset()` 把 `_mode` 清成空串，而 data 是在 Reset **之后**
    才组装的 ⇒ `if (_mode.Length > 0) data["mode"] = _mode` 永远不成立 ⇒
    `mode` 从来没到过模型手上。后果不是"少一个字段"，而是**战报话术整条退回错的那一支**：

        together 里本来该是「你和<玩家>一起向<对手>动了手」，
        实际成了「你向<玩家名>动了手」—— 把玩家自己说成了对手。

    这条在真机上表现为"文案看着别扭"，很容易被当成措辞问题放过，所以钉死。
    """
    src = _read("drama/BattleOutcome.cs")
    i = src.index("private static void Resolve(")
    j = src.index("private static void Reset(", i)
    body = src[i:j]

    assert "mode = _mode" in body, (
        "Resolve 开头没有把 `_mode` 取出来 —— Reset() 会清空它，"
        "data 在 Reset 之后组装，于是 mode 永远是空串")
    # Reset 必须确实在写 data 之前被调用（顺序本身是要紧的）
    k_reset = body.index("Reset();")
    k_write = body.index('data["mode"] = ')
    assert k_reset < k_write, "前提变了：Reset 不再早于写 data，这条契约要重写"
    # 写进 data 的必须是**局部变量**，不能再直接读字段
    assert 'data["mode"] = _mode' not in body, (
        "又直接读 `_mode` 字段了 —— Reset 已经把它清空，必须用前面取出的局部变量")


def test_no_battle_feedback_page_wired():
    """反馈页 20102 必须挂上"当场收口"，否则只能等静默 60s。

    事故形态（10-28 真机）：`CreateAction` 返回 0 只代表**动作建好**，
    引擎可以在 `OnCreate` 里直接判"对方逃了"（`UnitActionFeedback101`），
    这场从没进过战斗 ⇒ `BattleEnd` 不来、战斗位不翻真 ⇒ 只能等静默兜底。
    实测卡了 **67 秒**才回工具结果。
    """
    bo = _read("drama/BattleOutcome.cs")
    assert "IsNoBattleFeedbackPage" in bo and "ResolveAtNoBattlePage" in bo, (
        "BattleOutcome 没有反馈页判据")
    assert "20102" in bo, "反馈页页号（20102）没有落成常量"

    ui = _read("UI/DramaAiOption.cs")
    assert "IsNoBattleFeedbackPage" in ui, (
        "`DramaAiOption` 没调反馈页判据 —— 判据写了不接等于没写")
    assert "ResolveAtNoBattlePage" in ui, (
        "反馈页抓到了但没有当场收口 —— 玩家还得等静默兜底")


def test_no_battle_flag_reaches_result_and_renderer():
    """`no_battle` 要一路传到渲染层，且渲染层据此改口。"""
    bo = _read("drama/BattleOutcome.cs")
    assert 'data["no_battle"] = true' in bo, "结果里没有 `no_battle` 标记"

    tr = (ROOT / "tools" / "text_render.py").read_text(encoding="utf-8")
    assert 'data.get("no_battle")' in tr, (
        "渲染层没读 `no_battle` —— 那模型只知道「没看到战斗」，"
        "分不清「对方逃了」「还没打」「我们在等」")
