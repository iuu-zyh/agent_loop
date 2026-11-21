# -*- coding: utf-8 -*-
"""NPC 动作"当机/静默"的跨语言契约（10-21 用户报障后建）。

用户原话：「关于时间的问题，比如如果要过月了这时候调用论道，就会静默没有反应……
还有就是我让 npc 传送到我这里也显示失败了，这是为什么呢？」

## 反汇编实证（`scripts/dev/il2cpp_grep.py`，勿删）

`UnitActionBase::IsCreate` @0x1835083B0：
    this.isTip = isTip;
    eax = IsDayCost(day); if (eax != 0) return eax;    // 非 0 原样上抛，什么都不做
    this.isCreateCall = true; return 0;

`UnitActionBase::IsDayCost` @0x1835083E0（按汇编顺序）：
    ① 本动作的成本字段 > day                       → return 1
    ② `WorldRunMgr::get_roundDayResidue() != 0`   → return 1   ← ★ 月结算/换日期间
    ③ day > 0                                      → return 1
    ④ 否则                                          → return 0（放行）

`UnitActionMoveNPC::IsCreate` @0x1838E49C0 第一句就是 `esi = UnitActionBase::IsCreate(...)`，
非 0 直接 `mov eax,esi; ret` ⇒ **`code=1` 的来源就是 `IsDayCost` 拒了**。

## 由此定案的两条（本文件钉住）

  A. **`ok=false` ≠ 玩家拒绝**。`ActionStart` 会**同步**回调 false（真机 10-21：
     `[TalkOutcome] 开始跟踪` 的下一行就是 `完成回调 ok=False`），玩家连窗都没看见。
     旧文案「玩家婉拒或中途离开」是**谎报**，且让模型认定"对方不愿意"从此不再重试。
  B. **`code=1` ≠ 精力不足**。旧 `NativeRefuseWhy` 在 `costEnergy` 读不到时走到兜底句，
     而兜底句里硬编码了"战斗需要消耗精力" —— 那是**假事实**，模型据此让 NPC 去休息补精力，
     做的全是无用功。真因是月结算期间带日耗的动作一律被拒。

## 为什么这类错特别值得钉

两条都是"文案与事实不符"，而**文案直接进模型上下文**（失败帧不走渲染器，
见 `tools/text_render.py` 的 `if not res.get("success"): return None` 回退全量 JSON）。
模型据此做出的下一步全是错的，但日志里看不出任何异常 —— 只能靠源码契约拦。
"""

from __future__ import annotations

import re
from pathlib import Path
import _cs_tools  # 工具层多文件聚合（见 tests/_cs_tools.py）

ROOT = Path(__file__).resolve().parent.parent
TOOL = _cs_tools.TOOL_SRC
GREP_TOOL = ROOT / "scripts" / "dev" / "il2cpp_grep.py"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _method(src: str, header: str) -> str:
    i = src.find(header)
    assert i >= 0, "找不到方法签名：%s" % header
    j = src.find("{", i)
    assert j >= 0, "签名后没有花括号：%s" % header
    depth = 0
    for k in range(j, len(src)):
        if src[k] == "{":
            depth += 1
        elif src[k] == "}":
            depth -= 1
            if depth == 0:
                return src[i:k + 1]
    raise AssertionError("方法体没闭合：%s" % header)


def test_actionstart_false_is_not_reported_as_player_refusal():
    """★ A：`ok=false` 的文案**不许**说"玩家婉拒/中途离开"。

    这条钉的是谎报本身。真机 10-21：论道连点两次都没弹窗，两次都回
    「lun_dao未完成（玩家婉拒或中途离开）」—— 玩家什么都没看见。
    """
    src = _read(TOOL)
    # 判据只看**代码行**：注释里引用旧文案作对照（"旧文案写成「…」是谎报"）是给人看的，
    # 不该让本测试变红 —— 但代码里一个都不许剩。
    code = "\n".join(ln for ln in src.splitlines() if not ln.lstrip().startswith("//"))
    assert "玩家婉拒或中途离开" not in code, \
        "★ 又出现了「玩家婉拒或中途离开」—— `ok=false` 不是玩家拒绝（见文件头反汇编实证）"
    body = _method(src, " JObject StartToUnitAiActionDeferred(")
    body_code = "\n".join(ln for ln in body.splitlines() if not ln.lstrip().startswith("//"))
    # 失败分支必须在：要点明"这次没成"，而不是"对方不肯"。
    # （10-21 措辞从「没能启动」改为「没能生效」：归因从"整月边界"纠正为"没走到位"，
    #   见 test_ok_false_text_mentions_the_real_cause 的说明。）
    assert "没能生效" in body_code or "没能启动" in body_code, \
        "ok=false 的文案没有说明「这次没成」"
    # 且必须给可行动的下一步 + 明确"不是拒绝"
    assert "再试" in body_code or "稍后" in body_code, "文案没给可行动的下一步（模型只会放弃）"
    assert "不代表" in body_code or "不是对方拒绝" in body_code, \
        "没写明「这不代表对方拒绝」——模型会当成玩家的态度"


def test_ok_false_text_mentions_the_real_cause():
    """A2：文案要点出**真实机理**，否则模型只能瞎猜。

    ⚠️ 这条断言**改过三次**，每次都是因为我先把未核实的猜测钉成了护栏：

      ① 第一版找"月结算" —— 我**读反了跳转方向**（`0x183508486` 实际是 `xor eax,eax`
         = 放行，不是拒绝）；
      ② 第二版找"整月边界"（`roundDayResidue==0`）—— 套用了 `CreateAction` 系动作
         （有返回码）的解释，而这条路根本不是那条链；
      ③ 第三版找"没走到你跟前"（`MovePoint` 未到位）—— 而真机那一刻 `MovePoint`
         返回的是 **true**，人就在面前。

    现在的判据来自 2026-10-23 的**逐条反汇编 + 真机日志双重验证**
    （`docs/archive/2026-10-22-论道静默失败/修法与清理.md` §2/§4）：`ActionStart` 把 `CreateAction`
    的返回码**直接丢弃**；而 `Discovery.IsCreate` 的闸②是 `IsDayCost(配置常数 costDay=5)`
    拿 `residueDay` 去比 —— 余额不够就返回 1、无 UI、无提示。
    故文案必须说"引擎没接受"+ 可行动的原因（行动余暇/天数），**不许**再说"没走到"。

    ★ 教训（留给下一个人）：**护栏断言只能在机理被证实之后再写**。
      三次都是"测试替错误背书"，而且每次都得靠真机日志才发现。
    """
    src = _read(TOOL)
    body = _method(src, " JObject StartToUnitAiActionDeferred(")
    body_code = "\n".join(ln for ln in body.splitlines() if not ln.lstrip().startswith("//"))
    # 必须点出"引擎没接受"这个事实（而不是"对方如何如何"）
    assert "引擎没有接受" in body_code, (
        "ok=false 的文案没有点出真实机理（引擎丢弃了 `CreateAction` 的返回码 = 动作没建起来）"
    )
    # 必须给可行动的原因（行动余暇/天数）—— 这是模型唯一能做的事
    assert "余暇" in body_code or "天数" in body_code, \
        "文案没点出可行动的原因（他当月的行动余暇不够），模型只会反复重试同一个动作"
    # ★ 反向断言：**已证伪的归因一个都不许写回代码**
    for dead, why in (
        ("整月边界", "套用 `CreateAction` 系动作（有返回码）的解释；这条路与月末无关"),
        ("跨日", "论道/双修不需要跨日，那是移动/召唤那条链的约束"),
        ("走到你跟前", "真机那一刻 `MovePoint` 返回 true，人已经站在面前"),
        ("没走到", "同上：`MovePoint` 未到位这个前提不成立"),
    ):
        assert dead not in body_code, f"★ 文案又出现了「{dead}」—— 该归因已证伪：{why}"


def test_native_refuse_why_does_not_claim_energy_without_evidence():
    """★ B：`NativeRefuseWhy` 兜底句**不许**断言"精力不足"。

    旧兜底句硬编码了「游戏规则：战斗需要消耗精力，精力不足则无法进行」——
    而 `code=1` 的真因是 `IsDayCost`（月结算），与精力无关。
    """
    src = _read(TOOL)
    body = _method(src, " string NativeRefuseWhy(")
    # 取出所有 return 语句
    returns = re.findall(r"return\s+[^;]+;", body)
    assert returns, "NativeRefuseWhy 里没有 return 语句？"
    fallback = [r for r in returns if "引擎拒绝了这次动作" in r]
    assert fallback, "找不到兜底 return（断言锚点失效，本测试需要跟着改）"
    joined = " ".join(fallback)
    assert "精力不足" not in joined, \
        "★ 兜底句又断言了「精力不足」—— code=1 的真因是 IsDayCost 的整月边界，不是精力"


def test_native_refuse_why_probes_round_day_residue():
    """B2：必须**真的去探** `roundDayResidue`（而不是靠猜），且按**核实过的方向**判。

    ★ 核实过的语义（逐字节对过机器码）：`residue == 0`（整月边界）**且**动作跨日 → 拒绝。
      第一版代码与测试都写成了 `residue != 0`（我读反了 `jne` 的目标），
      那样会在**绝大多数日子**里错误地宣称"月结算"，是更糟的假事实。
    """
    src = _read(TOOL)
    body = _method(src, " string NativeRefuseWhy(")
    assert "roundDayResidue" in body, \
        "没有探 roundDayResidue —— 那就不可能分辨「整月边界拒」与「别的原因拒」"
    # 判据只看**代码行**：注释里留着"不是 residue != 0"这条更正记录（给人看的）
    code = "\n".join(ln for ln in body.splitlines()
                     if not ln.lstrip().startswith("//"))
    compact = code.replace(" ", "")
    assert "residue==0" in compact, \
        "判据不是「residue == 0」（整月边界）—— 方向反了会在平时的日子里乱报"
    assert "residue!=0" not in compact, \
        "★ 又用了「residue != 0」—— 那是读反跳转方向的结论（`jne 0x…486` 实为放行）"
    # 必须同时看 day：整月边界只拒**跨日**动作
    assert "day" in code, "没有看该动作要跨几天 —— 光看 residue 会误伤当天可完成的动作"


def test_energy_branch_still_requires_both_numbers():
    """B3：精力不足那条**必须**同时有 energy 与 cost 才允许说（不许半猜）。

    这是旧代码唯一做对的地方，别在改写时把它弄丢。
    """
    src = _read(TOOL)
    body = _method(src, " string NativeRefuseWhy(")
    # 只找**紧邻**「精力不足」那句的 if（`.*?` 会跨过好几个无关的 if —— 首版就匹配到了
    # `if (wr != null)`，断言对着错的括号在喊）
    m = re.search(r"if\s*\(([^)]*)\)\s*\n\s*return[^;]*精力不足", body)
    assert m, "找不到「精力不足」那一条的判断（锚点失效，本测试需要跟着改）"
    cond = m.group(1)
    assert "energy" in cond and "cost" in cond, \
        "精力不足的判据缺 energy 或 cost（会在读不到数值时乱断言）：%r" % cond
    assert "!= null" in cond, "判据没有排除 null（读不到时会当成 0 比较）：%r" % cond


def test_decompilation_notes_are_in_the_source():
    """反汇编结论必须**写在源码里**（下一次改这里的人不需要重跑反编译器）。

    地址 + 判定顺序是这条结论的唯一凭据；只写在 commit message / 聊天里等于没写。
    """
    src = _read(TOOL)
    body = _method(src, " string NativeRefuseWhy(")
    # 方法体上方的注释块（`_method` 从签名开始，故往前取 4000 字符）
    head = src[max(0, src.find(" string NativeRefuseWhy(") - 4000):
               src.find(" string NativeRefuseWhy(")]
    for token in ("0x1835083B0", "0x1835083E0", "IsDayCost", "roundDayResidue"):
        assert token in head, "反汇编凭据缺 %r（下一个人无从复核）" % token


def test_il2cpp_grep_tool_exists_and_documents_its_traps():
    """查证工具本身要有，且把踩过的坑写下来（否则下次还会踩）。"""
    assert GREP_TOOL.exists(), "scripts/dev/il2cpp_grep.py 不见了（查证入口）"
    src = _read(GREP_TOOL)
    for token in ("tlist", "rawx", "roundDayResidue" if False else "xref", "index"):
        assert token in src, "il2cpp_grep 缺 %r 相关实现/说明" % token
    # 两个已知坑必须写进 docstring（踩过：传方法名给 tlist 会静默空表；raw 会截 ret）
    assert "类型名前缀" in src or "类型名" in src, "没写「tlist 收类型名不是方法名」这个坑"
    assert "rawx" in src and "ret" in src, "没写「raw 会在第一个 ret 截断」这个坑"
