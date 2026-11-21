"""★ 反射只有一份实现（11-19 收拢）★

## 钉什么

11-19 之前，同一个"按名字读游戏对象成员"在 7 个文件里各写了一遍
（`RefMember` / `GetMember` / `GetMemberValue` / `Ref`，三个名字七处），
而且已经分叉：

  · 一份**字段优先**、六份属性优先 —— 同一类型若既有同名字段又有同名属性，取到不同值且不报错；
  · 一份多了 `dynamic` 索引器兜底 —— 代理类型没有索引器，走到必抛必吞，却让那**一处**语义不同。

反编 dump 核对过：这 7 处读的 28 个成员名，凡在 dump 里出现的**一律是属性**（PROP），
零个实例字段；唯一真字段 `mIsDie` 是 protected 原生字段，`GetField` 不搜基类 ⇒ 旧的字段档
对它同样读不到（它一直是被一段专门沿 `BaseType` 走的循环读的）。

## 断言（都是"改回去就会红"的形态）

1. 全仓只有 `game/Refl.cs` 一份实现；
2. 门面里没有 `dynamic` 索引器兜底；
3. 属性档不许退化成字段档（`Get` 走 `Resolve(..., false)`，`GetField` 才是 true）；
4. 失败要有日志（读不到 / 异常），且**有封顶**；
5. 调用点不许再自己包 `try { ... } catch { }`（门面绝不抛，外层再包只会掩盖失败来源）；
6. 六个调用方确实在用门面（不是只建了个文件没人调）。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CS = ROOT / "csharp"
REFL = CS / "game" / "Refl.cs"

#: 11-19 之前各写一份的调用方（现在都必须走门面）
CALLERS = [
    CS / "tools" / "TeamContainerInject.cs",
    CS / "tools" / "TeamJoinGate.cs",
    CS / "tools" / "TeamBattleFollowup.cs",
    CS / "tools" / "BattleKillStats.cs",
    CS / "state" / "BattleSceneFacts.cs",
    CS / "drama" / "NpcDuel.cs",
    CS / "diag" / "team" / "TeamMultiProbe.cs",
]


def _code(text: str) -> str:
    """粗略去注释：契约断言不许在注释上命中（本仓注释里到处在讲这些坑）。"""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"///.*", "", text)
    text = re.sub(r"//.*", "", text)
    return text


def _src(p: Path) -> str:
    return _code(p.read_text(encoding="utf-8"))


def _all_cs() -> list[Path]:
    return [p for p in CS.rglob("*.cs")
            if not ({"tests", "obj", "bin"} & set(p.parts))]


def test_only_one_reflection_implementation_exists():
    assert REFL.is_file(), "唯一实现 game/Refl.cs 不见了"
    offenders = []
    for p in _all_cs():
        if p == REFL:
            continue
        if re.search(r"static\s+object\s+(Ref|RefMember|GetMember|GetMemberValue)\s*\(", _src(p)):
            offenders.append(str(p.relative_to(ROOT)))
    assert not offenders, "又出现了第二份反射实现：%s" % offenders


def test_no_dynamic_indexer_fallback():
    src = _src(REFL)
    assert "dynamic" not in src, "门面里又出现了 dynamic 兜底（代理类型没有索引器，走到必抛必吞）"
    assert "RuntimeBinder" not in src, "门面里出现了动态绑定"


def test_property_path_does_not_fall_back_to_fields():
    """属性档读不到就返回空 —— 不许偷偷再查一次字段（那是每次调用多一次查找的死路）。"""
    src = _src(REFL)
    get_body = src[src.index("public static object Get(object o, string name)"):]
    get_body = get_body[:get_body.index("public static object GetField")]
    assert "Resolve(o.GetType(), name, false)" in get_body, "Get 没有走属性档"
    assert "GetField(" not in get_body and "FieldInfo" not in get_body, \
        "属性档里又出现了字段兜底"


def test_field_path_is_explicit_and_walks_base_types():
    src = _src(REFL)
    assert "public static object GetField(" in src, "显式字段入口不见了（mIsDie 这类只有它能读）"
    assert "t.BaseType" in src, "没有沿 BaseType 上找 —— 基类的私有字段会永远读不到"


def test_failures_are_logged_and_capped():
    src = _src(REFL)
    assert "[Refl] 读不到" in src, "读不到没有日志（又会变成静默）"
    assert "[Refl] 读取异常" in src, "异常没有日志"
    m = re.search(r"LogCap\s*=\s*(\d+)", src)
    assert m, "没有日志封顶常量"
    assert int(m.group(1)) <= 200, "封顶太大，等于没封"


def test_call_sites_do_not_wrap_the_helper_in_try_catch():
    """门面绝不抛，调用点再包 try/catch 只会让"是谁失败"更模糊。"""
    pat = re.compile(r"try\s*\{\s*[^{};]*Refl\.[^{};]*;\s*\}\s*catch\s*\{\s*\}")
    bad = []
    for p in CALLERS:
        if pat.search(_src(p)):
            bad.append(str(p.relative_to(ROOT)))
    assert not bad, "又有调用点给 Refl 包了 try/catch：%s" % bad


def test_every_former_duplicate_now_uses_the_facade():
    for p in CALLERS:
        assert p.is_file(), "调用方文件不见了：%s" % p
        assert "Refl." in _src(p), "%s 没有走统一门面" % p.relative_to(ROOT)
