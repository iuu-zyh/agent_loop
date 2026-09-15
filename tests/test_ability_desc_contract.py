"""功法「技能说明」契约（09-13）：`inspect_unit(classes=["abilities"])` 原先只有名字+id。

**要解决的问题**：模型挑功法（传功 / 学习 / 施法）时**看不到这门功法干什么**，只能照名字猜
——与道具原先缺 `desc` 是同一类缺口（附录 D.6「道具介绍」那次）。

**入口只有一个**：`UIMartialInfoTool.GetDesc(MartialData)`，反编实证
（`UIMartialInfoTool` 是 `abstract+sealed` 静态类，`public static string GetDesc(DataProps.MartialData)`）。
同一族的 `GetDescRichText(...)` 才是带图标/染色数字的富文本版 ⇒ `GetDesc` 是纯文本版。
说明文字由「前缀 `ConfBattleSkillPrefixValueItem.desc` 模板 + `ConfBattleSkillValueItem` 数值」
在游戏内组装 —— **自己拼必然漏占位符**，所以只走这一个入口，不自己查表拼。

本文件锁住四条静默失效：
  1. 说明必须挂在**同一个** `MartialData` 上（名字走主路径、说明走兜底路径就会拿到别的东西）；
  2. 富文本标签必须剥掉（`<color=…>`/`<sprite name=…>` 直接喂模型是噪音）；
  3. 取不到就**不落 `desc` 键**（字段缺失 ≠ 没有说明），且**必须留痕**（绝不静默）；
  4. 渲染层要把说明带出来（C# 给了、Python 不读 = 模型还是看不到，附录 D.1 那条已踩四次）。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CS = ROOT / "csharp"


def _read(rel: str) -> str:
    return (CS / rel).read_text(encoding="utf-8")


def _martial_slot_body() -> str:
    src = _read("UnitSnapshot.cs")
    body = src[src.index("private static JObject MartialSlot("):]
    return body[: body.index("\n        /// <summary>首个技能说明取到的留痕")]


# ---------------------------------------------------------------- C# 侧

def test_getdesc_is_the_documented_panel_entry():
    """必须调 `UIMartialInfoTool.GetDesc`（面板同一入口），不自己查 conf 拼说明。"""
    body = _martial_slot_body()
    assert "UIMartialInfoTool.GetDesc(mdOut)" in body, \
        "技能说明必须走 UIMartialInfoTool.GetDesc（面板「技能说明」同一入口）"
    # 不许自己去读 conf 前缀表拼（会漏占位符替换）
    assert "prefixValueItem.desc" not in body and "ConfBattleSkillPrefixValue" not in body, \
        "不许自己按前缀表拼说明（模板占位符要靠游戏侧替换）"


def test_desc_uses_the_same_martialdata_as_the_name():
    """`mdOut` 必须就是主路径那个 `md` —— 名字与说明来自同一份数据。"""
    body = _martial_slot_body()
    assert "mdOut = md;" in body, "主路径取到 md 后必须存进 mdOut（供 GetDesc 用同一个对象）"
    i_assign = body.index("mdOut = md;")
    i_get = body.index("UIMartialInfoTool.GetDesc(mdOut)")
    assert i_assign < i_get, "mdOut 要先于 GetDesc 赋值"
    assert "DataProps.MartialData mdOut = null;" in body, "mdOut 必须提到 try 外层才能跨块使用"


def test_desc_is_sanitized_of_rich_text():
    """说明必须过 `StripRichTags`：面板用富文本染色/插图，模型要纯文本。"""
    body = _martial_slot_body()
    assert "StripRichTags(d)" in body, \
        "说明必须过 StripRichTags（游戏的 <y>…</y> 配色标签与 Unity 富文本都要剥掉）"
    src = _read("UnitSnapshot.cs")
    fn = src[src.index("private static string StripRichTags(string s)"):]
    fn = fn[: fn.index("\n        }")]
    assert "if (c == '<') { inTag = true; continue; }" in fn and \
           "if (c == '>') { inTag = false; continue; }" in fn, "必须成对剥掉 <…> 标签"
    # 只剥标签 + 折叠空白，绝不截断/改写（忠实原则）
    assert "Substring" not in fn and "…" not in fn, "清洗只许剥标签+折叠空白，不得截断"


def test_missing_desc_omits_key_and_never_fails_silently():
    """取不到 → 不落 desc 键 + 留痕；抛异常 → 也留痕（两者都不许静默）。"""
    body = _martial_slot_body()
    assert 'if (d.Length > 0)' in body and 'jo["desc"] = d;' in body, \
        "只有非空才落 desc 键（字段缺失 ≠ 没有说明）"
    assert 'ModMain.P("[Abilities] GetDesc 返回空' in body, "取到空必须留痕"
    assert 'ModMain.P("[Abilities] GetDesc 抛异常' in body, "抛异常必须留痕（不吞）"
    assert "catch (System.Exception e)" in body, "必须是捕获具体异常并记录，而不是裸 catch {}"


# ---------------------------------------------------------------- 渲染层

def test_martial_renders_desc():
    from agent_loop.tools.text_render import _martial
    o = {"type": "神通", "id": "U1", "name": "冲蓬骤天剑",
         "desc": "在使用身法的持续时间内，每 5秒召唤一道剑影。"}
    t = _martial(o)
    assert t.startswith("神通「冲蓬骤天剑」(id=U1)——"), t
    assert "每 5秒召唤一道剑影。" in t, t


def test_martial_without_desc_unchanged():
    """旧 C# 载荷没有 desc → 渲染结果与改动前完全一致（不出现空破折号）。"""
    from agent_loop.tools.text_render import _martial
    assert _martial({"type": "灵技", "id": "M1", "name": "青木箭"}) == "灵技「青木箭」(id=M1)"
    assert _martial({"type": "灵技", "id": "M1", "name": "青木箭", "desc": "  "}) == "灵技「青木箭」(id=M1)"


def test_abilities_is_one_line_per_slot():
    """说明很长，必须逐槽一行（全挤进一句会糊成一片），且 id 一个都不能少。"""
    from agent_loop.tools.text_render import _abilities
    ab = {
        "skill_left": {"type": "灵技", "id": "M1", "name": "青木箭", "desc": "射出一支木箭。"},
        "ultimate": {"type": "神通", "id": "U1", "name": "冲蓬骤天剑", "desc": "降下巨剑。"},
        "abilitys": [{"type": "心法", "id": "X1", "name": "青元诀", "desc": "提升灵力上限。"}],
    }
    t = _abilities(ab)
    lines = t.split("\n")
    assert lines[0] == "功法：", lines
    assert len(lines) == 4, f"三个槽应各占一行（+标题）：{lines}"
    assert all(l.startswith("· ") for l in lines[1:]), lines
    assert "id=M1" in t and "id=U1" in t and "id=X1" in t, "id 是引用键，一个都不能丢"
    assert "射出一支木箭。" in t and "降下巨剑。" in t and "提升灵力上限。" in t
    # 每个槽的类型/名称/说明同处一行（读起来是一体的）
    assert any("神通「冲蓬骤天剑」(id=U1)——降下巨剑。" in l for l in lines)


def test_abilities_empty_slots_are_skipped():
    from agent_loop.tools.text_render import _abilities
    assert _abilities({}) == "", "全空不该渲染出孤零零的「功法：」"
    assert _abilities({"skill_left": {"type": "灵技", "id": "", "name": ""}}) == ""


# ---------------------------------------------------------------- 占位符替换（第二次）

def _sigil_body() -> str:
    src = _read("UnitSnapshot.cs")
    return src[src.index("private static string ResolveSkillSigils("):
               src.index("private static int CountSigils(")]


def test_sigils_resolved_before_stripping_tags():
    """顺序：取模板 → 替换占位符 → 剥配色标签。反了会把 `$`/`&` 之外的记号留下。"""
    body = _martial_slot_body()
    i_tpl = body.index("UIMartialInfoTool.GetDesc(mdOut)")
    i_res = body.index("ResolveSkillSigils(tpl, mdOut)")
    i_str = body.index("StripRichTags(d)")
    assert i_tpl < i_res < i_str, "必须 取模板 → ResolveSkillSigils → StripRichTags"


def test_two_sigil_families_have_distinct_sources():
    """`&expr&` = 数值（ConfBattleSkillValue），`$key$` = 本地化文本（GameTool.LS）——
    两族来源不同，混用会得到"查不到就原样返回"的静默失败。"""
    body = _sigil_body()
    assert "c == '$'" in body and "GameTool.LS(inner)" in body, "$key$ 必须走 GameTool.LS"
    assert "TryResolveValue(inner, vd)" in body, "&expr& 必须走 ConfBattleSkillValue"
    # LS 未命中通常原样返回 key —— 那不算替换成功，必须判掉
    assert "rep == inner" in body, "LS 返回原 key 时要判定为未命中（否则会把 key 当正文）"


def test_value_lookup_tries_both_key_forms():
    """配置表主键自带前导 `&`（实证 key=`&22111_range`），但 API 形参叫 key —— 两种都试。"""
    src = _read("UnitSnapshot.cs")
    body = src[src.index("private static string ScaledValue("):
               src.index("private static string EvalMath(")]
    assert 'string[] forms = { key, "&" + key };' in body, "取值两种键写法都要试"
    assert "GetValue(forms[i], vd)" in body, "单值走 GetValue"
    assert "GetItem(forms[i])" in body, "缩放系数要用同一套键写法去取 GetItem"


def _brace_block(src: str, start_idx: int) -> str:
    """取出 start_idx 之后第一对配平花括号的**内部**（不含外层括号）。"""
    i = src.index("{", start_idx)
    depth, j = 0, i
    while j < len(src):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[i + 1: j]
        j += 1
    raise AssertionError("花括号不配平")


def test_math_failure_does_not_fall_back_to_a_single_factor():
    """算式解析失败**不许**退回单值：只取一个因子会给出错误的数，比留个可见占位符更糟。"""
    src = _read("UnitSnapshot.cs")
    body = src[src.index("private static string EvalMath("):
               src.index("private static double? NumOf(")]
    # 算式分支失败一律走 null（由 TryResolveValue 决定是否退 ValueMathf），
    # 绝不在 EvalMath 内部把表达式当单值查一次
    assert "GetValue(" not in body, "EvalMath 里不许出现 GetValue 单值兜底"
    assert re.search(r"if \(acc == null\) return null;", body), "算不出就 return null"
    # 调用侧：算式失败只退 ValueMathf（游戏实现），不退单值
    top = src[src.index("private static string TryResolveValue("):
              src.index("private static bool _mathCompared;")]
    assert "ScaledValue(inner, vd)" in top, "单值路径只在没有 `|` 时走"
    assert top.count("ScaledValue(inner, vd)") == 1, "算式分支里不许再出现单值兜底"


def test_battle_skill_value_data_comes_from_the_same_martialdata():
    body = _sigil_body()
    assert "new BattleSkillValueData(md)" in body, "值域数据必须由同一个 MartialData 构造"


def test_residuals_are_counted_and_logged():
    """替换不掉的记号必须留痕（绝不静默）—— 否则又回到"模型看到一串 &xxx&"的投诉。"""
    body = _martial_slot_body()
    assert "CountSigils(d)" in body, "要统计残留"
    assert "★技能说明仍残留" in body, "有残留必须打日志（带片段，便于定位是哪种 token）"
    src = _read("UnitSnapshot.cs")
    assert "private static int CountSigils(string s)" in src


# ---------------------------------------------------------------- 语法实证（离线用游戏配置表验过）
#
# 下面这段是**用游戏自带配置表演算出来的**真值，作为"替换后应该长什么样"的标准答案留档。
# 数据源（只读，不随仓库分发）：
#   Mod/modFQA/配置修改教程/配置（只读）Json格式/LocalText.json          ← $key$ 的目标
#   Mod/modFQA/配置修改教程/配置（只读）Json格式/BattleSkillValue.json   ← &expr& 的目标（key 自带前导 &）
# 模板 `LocalText.skill_attack_desc22111` 原文：
TPL_22111 = ("向周围斩出一轮横扫刀光，对范围&22111_range&内的敌人造成威力&22111_dmg&的$s_dao$伤害，"
             "并且引爆目标身上的$s_xueren$，每引爆一层，在目标的位置落下一道血斩，"
             "对范围&22111_xzfw&内的敌人造成威力&22111_xzsh|x22111_dmg|/100|f0&的$s_dao$伤害。")
# 用 conf 的 value1（等级 1）演算出的结果（&22111_range=240 / _dmg=480 / _xzfw=100 / _xzsh=10
# → 10×480/100=48；$s_dao$=刀法、$s_xueren$=血刃）
EXPECT_22111 = ("向周围斩出一轮横扫刀光，对范围240内的敌人造成威力480的刀法伤害，"
                "并且引爆目标身上的血刃，每引爆一层，在目标的位置落下一道血斩，"
                "对范围100内的敌人造成威力48的刀法伤害。")


def test_reference_substitution_grammar_is_recorded():
    """把实证过的语法钉成可执行规格（C# 侧的结构由上面几条源码契约锁）。

    这条测的是**规格本身**：三类记号、两族来源、算式语法、`<y>…</y>` 配色标签。
    若哪天有人改了 C# 的语法假设，这里的真值就是对照物。
    """
    import re
    lt = {"s_dao": "刀法", "s_xueren": "血刃"}
    bv = {"&22111_range": "240", "&22111_dmg": "480", "&22111_xzfw": "100", "&22111_xzsh": "10"}

    def _val(expr: str) -> str:
        if "|" in expr:                       # A|xB|/100|f0 → A × B ÷ 100，0 位小数
            parts = expr.split("|")
            nums, mul, div = [], 1.0, 1.0
            for p in parts:
                if p.startswith("x"):
                    mul *= float(_val(p[1:]) or 0)
                elif p.startswith("/"):
                    div *= float(p[1:] or 1)
                elif p and not re.fullmatch(r"f\d+", p):
                    nums.append(float(_val(p) or 0))
            return f"{(nums[0] if nums else 0) * mul / div:.0f}"
        return bv.get("&" + expr, "")

    t = re.sub(r"&([^&]+)&", lambda m: _val(m.group(1)) or m.group(0), TPL_22111)
    t = re.sub(r"\$([^$]+)\$", lambda m: lt.get(m.group(1), m.group(0)), t)
    t = re.sub(r"</?[a-zA-Z]+>", "", t)       # <y>…</y> 游戏自带配色标签
    assert t == EXPECT_22111, t
    assert "&" not in t and "$" not in t, "替换后不许再有任何记号"


# ---------------------------------------------------------------- valueScale（第三次修）

def test_value_scale_is_applied():
    """`GetValue` 返回**未缩放的原始值**，缩放系数在同一行的 `valueScale` 列（`x<系数>|f<小数位>`）。
    不套它 ⇒ 所有 0.001/0.01 系数的数值整体放大 100~1000 倍（真机：2秒→2000秒、50%→5000%）。"""
    src = _read("UnitSnapshot.cs")
    body = src[src.index("private static string ScaledValue("):
               src.index("private static string ValueScaleOf(")]
    assert "ApplyValueScale(raw, ValueScaleOf(form))" in body, \
        "单值路径必须 GetValue → 再套该行的 valueScale"
    assert "ApplyValueScale" in src and "private static string ValueScaleOf(" in src


def test_value_scale_grammar_is_parsed_case_insensitively():
    """全表实证：系数只有 1/0.1/0.01/0.001/0.0001，小数位 0~3，且有一条大写 `F1`。"""
    src = _read("UnitSnapshot.cs")
    body = src[src.index("private static string ApplyValueScale("):
               src.index("private static string EvalMath(")]
    assert "char.ToLowerInvariant(seg[0])" in body, "`F1` 这种大写要能认（真机配置里就有一条）"
    assert "c == 'x'" in body and "c == 'f'" in body, "系数段 x… 与小数位段 f… 都要解析"
    # 尾零裁剪：中文句子里要"2秒"，不是"2.00秒"
    assert "TrimEnd('0').TrimEnd('.')" in body, "要去掉多余尾零"


def test_math_operands_get_their_own_scale():
    """算式里的**每个操作数**也要各自套 valueScale —— 否则 `x0.001` 类系数照样漏。"""
    src = _read("UnitSnapshot.cs")
    body = src[src.index("private static double? NumOf("):]
    body = body[: body.index("\n        }")]
    assert "ScaledValue(key, vd)" in body, "操作数取值必须走同一套（选列 + 缩放）"


def test_math_failure_falls_back_to_gamemathf_with_evidence():
    """自算失败退回 `ValueMathf`，并把两者结果打一行对照日志（有分歧时有判据）。"""
    src = _read("UnitSnapshot.cs")
    body = src[src.index("private static string TryResolveValue("):
               src.index("private static bool _mathCompared;")]
    assert "ValueMathf(inner, vd)" in body, "兜底要留游戏自己的实现"
    assert "算式自算=" in body and "游戏ValueMathf=" in body, "要打对照日志（只打一次）"


# —— 用真机配置表的原值演算，钉住"替换后应该长什么样" ——
# 数据源（只读，不随仓库分发）：BattleSkillValue.json 的 value1 列 + valueScale 列
_GOLDEN = {
    # key: (原值, valueScale, 期望)
    "510011_cxsj": ("2000", "x0.001|f2", "2"),      # 真机症状：显示成 2000 秒
    "32111_bssj": ("8000", "x0.001|f2", "8"),       # 真机症状：显示成 8000 秒
    "32111_tsgj": ("5000", "x0.01|f2", "50"),       # 真机症状：显示成 5000%
    "32111_tsfy": ("-3000", "x0.01|f2", "-30"),     # 真机症状：显示成 -3000%
    "12113_xxbl": ("700", "x0.01|f2", "7"),         # 真机症状：显示成 700% 吸血
    "22111_dmg": ("480", "x1|f0", "480"),           # 系数 1：原样
    "32111_conditionValue": ("50", "x1|f0", "50"),  # 系数 1：真机显示 50%，正确
    "510011_ydsd": ("30", "x1|f0", "30"),
    "22111_xzsh": ("10", "x1|f0", "10"),
}


def _apply_scale(raw: str, spec: str) -> str:
    mul, dec = 1.0, -1
    for seg in spec.split("|"):
        if len(seg) < 2:
            continue
        c, rest = seg[0].lower(), seg[1:]
        if c == "x":
            mul = float(rest)
        elif c == "f":
            dec = int(rest)
    r = float(raw) * mul
    s = f"{r:.{dec}f}" if dec >= 0 else str(r)
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s


def test_golden_value_scale_matches_real_conf():
    """真机配置表原值 → 期望值（这条就是"8秒不该是8000秒"的可执行规格）。"""
    for key, (raw, spec, want) in _GOLDEN.items():
        got = _apply_scale(raw, spec)
        assert got == want, f"{key}: {raw} × {spec} 应为 {want}，实得 {got}"
    # 反例自检：不套 valueScale 会得到什么（正是真机症状）
    assert _apply_scale("8000", "") == "8000", "不缩放就是原值 —— 这正是踩过的坑"


def test_golden_reference_math_expression():
    """算式 `A|xB|/100|f0` 的语义（各操作数先各自缩放，再乘除，最后按 f 格式化）。"""
    def val(k):
        raw, spec, _ = _GOLDEN[k]
        return float(_apply_scale(raw, spec))

    # 真机模板：威力&22111_xzsh|x22111_dmg|/100|f0&  → 10 × 480 / 100 = 48
    assert f"{val('22111_xzsh') * val('22111_dmg') / 100:.0f}" == "48"
