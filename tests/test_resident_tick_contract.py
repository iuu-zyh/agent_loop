"""常驻帧回调的**全仓守卫契约**（11-19 立，起因是一次全仓扫描）。

## 这份测试守什么

`tests/test_frame_tick_contract.py` 守的是"`_tickRegistered` 这个具体标志不许回来"。
本文件守的是**形状**：任何"用一个活得比它守的东西更久的布尔，去挡一次每次进世界都该重做的
注册"都必须消失。前者能防住那一个变量名，后者能防住下一个同形状的新变量。

## 真机证据（20:25 那份 Player.log，5 段世界）

体检表每 5 秒一行，含各 tick 的 `n=`（本窗口执行次数）与 `帧=+N`（本窗口帧数）。
把两者的比值按会话拆开：

    | 键            | 会话1 | 会话2 | 会话3 | 会话4 | 会话5 |
    | deferq        | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 |   ← 走 FrameTicker
    | battleflow    | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 |   ← 走 FrameTicker
    | npcmon        | 1.00 | 1.00 | 2.00 | 2.00 | 2.00 |   ← 裸注册
    | hotkeys       | 1.00 | 1.00 | 2.00 | 2.00 | 2.00 |   ← 裸注册
    | diag          | 1.00 | 1.00 | 2.00 | 2.00 | 2.00 |   ← 裸注册
    | prober        | 1.00 | 1.00 | 2.00 | 2.00 | 2.00 |   ← 裸注册

裸注册的键从第 3 段世界起恒为 2.00 次/帧，**再也没回落**；走 FrameTicker 的恒为 1.00。
这就是"每进一次世界多一份、上一份还活着、`Destroy` 一条都撤不掉"的直接量值。

## 为什么要成立

三个代价，全都静默：
  · 每帧多 N 次 `Time.frameCount` + 字典查 + 委托调用（热点路径白烧）；
  · 委托与闭包捕获的对象被游戏那边的计时器表**永久持有** ⇒ 世界重载后旧世界对象不可回收
    （本仓已有两次"静态表活过世界重载 ⇒ 僵尸条目"的事故）；
  · `Destroy` 撤不掉它们 ⇒ "句柄在册 = 确实在跑"这句话不成立，诊断口径被污染。

## 另一个更隐蔽的坑：owner

`ModMain.Init` 每次进世界都 `new MainThreadDispatcher()` / `new NpcInitiativeMonitor(...)`，
帧回调是**实例方法**。若 `Ensure` 只按句柄判活，重跑 `Init` 时会看到"句柄在、还活着"
⇒ 什么都不做 ⇒ 帧回调永远跑在被 `Destroy` 里 `Dispose()` 掉的旧实例上
（`OnUpdate` 第一句就是 `if (_disposed) return`）。后果是**Python 的每次工具调用都超时**
（全部经 `_dispatcher.Enqueue`），而日志显示一切正常 —— 本仓最忌的形态。
所以实例方法的注册必须把实例作为 `owner` 传进去。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CS = ROOT / "csharp"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _code_only(src: str) -> str:
    """去掉注释 —— 断言"不许出现某实现"时，注释里提到它不算。"""
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"//.*", "", src)


def _method_body(lines: list[str], idx: int) -> str:
    """从 idx（1-based，守卫所在行）向上找方法签名、按大括号配平取方法体。"""
    start = idx - 1
    while start > 0 and not re.search(r"\)\s*$", lines[start].rstrip()):
        start -= 1
    depth, opened = 0, False
    for j in range(start, min(len(lines), start + 800)):
        for ch in lines[j]:
            if ch == "{":
                depth += 1
                opened = True
            elif ch == "}":
                depth -= 1
        if opened and depth <= 0:
            return "\n".join(lines[start:j + 1])
    return "\n".join(lines[start:start + 800])


# ---------- ① 常驻注册一律经 FrameTicker ----------


#: (文件, 期望键, 说明) —— 这八条是 11-19 扫描时从裸注册迁进来的
MIGRATED = [
    (CS / "app" / "ModMain.cs", "FrameTicker.KDispatcher", "disp"),
    (CS / "app" / "ModMain.cs", "FrameTicker.KNpcMonitor", "npcmon"),
    (CS / "app" / "ModMain.cs", "FrameTicker.KBrainLink", "brainlink"),
    (CS / "app" / "ModMain.cs", "FrameTicker.KHotkeys", "hotkeys"),
    (CS / "app" / "ModMain.cs", "FrameTicker.KDiag", "diag"),
    (CS / "app" / "ModMain.cs", "FrameTicker.KProber", "prober"),
    (CS / "UI" / "MapMainContactButton.cs", "FrameTicker.KMapBtn", "mapbtn"),
    (CS / "UI" / "UnreadBanner.cs", "FrameTicker.KBanner", "banner"),
]


def test_migrated_ticks_go_through_frame_ticker():
    """扫描时迁进来的八条必须仍然经 `FrameTicker.Ensure`，且日志名没被改。

    日志名是体检表对账口径：日志里 `hotkeys n=2x 帧` 这样的行要靠名字认人，
    改名而不同步会让"这个键消失了"这种排查线索断掉。
    """
    for path, key, name in MIGRATED:
        assert path.exists(), f"{path} 不见了"
        body = _code_only(_read(path))
        assert key in body, f"{path.name} 没有用 {key} 注册帧回调（是不是又改回裸注册了？）"
        assert f'"{name}"' in body, f"{path.name} 的日志名 '{name}' 不见了（改名要同步对账口径）"
        assert "FrameTicker.Ensure(" in body, f"{path.name} 没有经 FrameTicker 注册"


def test_frame_ticker_keys_are_unique_including_new_ones():
    """键值互不相同 —— 重复值等于两个组件共用一个键，后注册的会被 `Once` 永久挡掉。"""
    src = _read(CS / "app" / "FrameTicker.cs")
    pairs = re.findall(r"internal const int (K[A-Za-z]+)\s*=\s*(\d+)\s*;", src)
    assert len(pairs) >= 18, f"键常量少于 18 个（实际 {len(pairs)}）：扫描迁入的那批漏了？"
    vals = [v for _, v in pairs]
    assert len(set(vals)) == len(vals), f"键值重复：{pairs}"
    names = [n for n, _ in pairs]
    assert len(set(names)) == len(names), f"键名重复：{pairs}"


def test_no_frame_ticker_key_is_defined_outside_the_registry():
    """**全仓**不许在 `FrameTicker.cs` 之外自己定义键值。

    ★这条测试是 11-20 补的，起因是一个真实存在的分裂★
    `UiKit` 原先自己写着 `private const int AuditKey = 19;`，注释还是
    "与 app/FrameTicker.cs 的常量段并列"。号段分裂成两处之后，下一个人要加常量时
    自然会想"最后一个是什么？18，那我用 19" —— 撞号是必然的，只是时间问题。

    撞号不会报错，只会静默出三件事（详见 `FrameTicker.KUIAudit` 的注释）：
      ① 日志名被覆盖（显示 A、跑的可能是 B）；
      ② 先挂那条成幽灵（游戏每帧调它、我们永无句柄、`StopAll` 停不掉）；
      ③ 后注册那条永不执行，而 `idle=0` 照旧，日志看不出异常。

    判据：除 `FrameTicker.cs` 外，任何文件里出现"数字字面量"作为键传给
    `FrameTicker.Ensure` / `FrameTicker.Stop`，或形如 `const int XxxKey = <数字>` 的定义，
    都算违规 —— 必须改用 `FrameTicker.K*` 常量。
    """
    ticker = CS / "app" / "FrameTicker.cs"
    offenders = []

    # ① 直接把数字当键传进去
    call_re = re.compile(r"FrameTicker\.(?:Ensure|Stop|Once|FramesSinceAttach)\(\s*(\d+)\s*[,)]")
    # ② 自己定义数字键常量（名字里带 Key/Dkey 之类也算）
    const_re = re.compile(r"const\s+int\s+(\w*[Kk]ey)\s*=\s*(\d+)\s*;")

    for p in sorted(CS.rglob("*.cs")):
        if "tests" in p.parts or p == ticker:
            continue
        code = _code_only(_read(p))
        for m in call_re.finditer(code):
            offenders.append(f"{p.relative_to(ROOT).as_posix()}: 把字面量 {m.group(1)} 当帧回调键")
        for m in const_re.finditer(code):
            # `= FrameTicker.KUIAudit` 这种合法（不是数字），正则只匹配纯数字
            offenders.append(
                f"{p.relative_to(ROOT).as_posix()}: 自定义数字键 {m.group(1)} = {m.group(2)}"
            )

    assert not offenders, (
        "这些地方在 FrameTicker.cs 之外自己造帧回调键：\n  " + "\n  ".join(offenders)
        + "\n号段必须集中在一处（`FrameTicker` 常量段），否则两人挑了同一个号时："
          "\n  名字被覆盖 / 先挂那条成幽灵 / 后注册那条永不执行，而日志一切正常。"
    )


def test_ui_audit_uses_the_registry_constant():
    """`UiKit` 的建树自检必须引用 `FrameTicker.KUIAudit`，不许再自带一个数。

    它是有活才挂、干完自撤的**临时** tick，是最容易被忽视的一条 —— 撞号之后
    症状是"面板自检日志不再出现"，而没人会往"键被抢了"这个方向想。
    """
    kit = _code_only(_read(CS / "UI" / "UiKit.cs"))
    assert "FrameTicker.KUIAudit" in kit, \
        "UiKit 没有引用 FrameTicker.KUIAudit（又自己定义键了？）"
    assert "FrameTicker.Ensure(AuditKey" in kit or "FrameTicker.Ensure(FrameTicker.KUIAudit" in kit, \
        "UiKit 的 Ensure 没有用登记过的键"


# ---------- ② 全仓不许再有裸的常驻注册 ----------


def test_no_bare_repeating_frame_registration_anywhere():
    """**全仓**不许再有 `g.timer.Frame(..., 1, true)` 形式的裸常驻注册。

    例外是**一次性**注册（`..., 1, false`）：那种自己跑完就没了，本来就不会叠。
    这条闸抓的是"每帧重复 + 返回值不接"的组合 —— 那正是叠注册的来源。

    为什么不只查 `ModMain`：11-19 扫描时 `UiComposer.Compose` 里也藏了一条
    （`AbPanelProber.OnFrame`）。它虽然因为 `Compose` 全工程无调用点而从未执行，
    但留在那里会让后来读代码的人以为"prober 已经有人注册了"，而且一旦 `Compose` 被复活，
    就会与 `ModMain` 里那条同键 tick 互相顶掉（`Once` 按帧号去重 ⇒ 后注册的永远不动）。
    只查一个文件挡不住下一个同样的失误。
    """
    offenders = []
    for p in sorted(CS.rglob("*.cs")):
        if "tests" in p.parts or p.name == "FrameTicker.cs":
            continue        # FrameTicker 是唯一允许直接碰 g.timer.Frame 的地方（它存句柄）
        code = _code_only(_read(p))
        lines = code.splitlines()
        for i, line in enumerate(lines, 1):
            if "g.timer.Frame(" not in line:
                continue
            seg = "\n".join(lines[i - 1:i + 14])   # 参数可能跨行
            m = re.search(r"\)\s*,\s*1\s*,\s*(true|false)\s*\)", seg)
            if m and m.group(1) == "true":
                offenders.append(f"{p.relative_to(ROOT).as_posix()}:{i}")
    assert not offenders, (
        "这些位置还有裸的每帧注册（`g.timer.Frame(..., 1, true)` 且返回值不接）："
        + "、".join(offenders)
        + "\n每进一次世界就多一份、上一份还活着、Destroy 撤不掉 ——"
        "\n真机日志量到它们从第 3 段世界起恒为 2.00 次/帧。改用 FrameTicker.Ensure(键, 名, 体)。"
    )


def test_no_self_healing_ticking_flag_left():
    """`_ticking` 这类"只挡帧注册的布尔"必须消失 —— 它的典型故障形态是**再也没复位**。

    `MapMainContactButton` 原样的现场：`_ticking` 全文件没有任何地方设回 false，
    而真机体检表里 `mapbtn` 只在第 1、2 段世界出现过，**第 3 段起彻底消失**。
    症状是收到传音后地图「传」钮的未读红点不再亮，且一条日志都没有。
    """
    offenders = []
    for p in sorted(CS.rglob("*.cs")):
        if "tests" in p.parts:
            continue
        code = _code_only(_read(p))
        if re.search(r"\b_ticking\b", code):
            offenders.append(p.relative_to(ROOT).as_posix())
    assert not offenders, (
        "这些文件里还有 `_ticking`（只挡帧注册的布尔）：" + "、".join(offenders)
        + "\n世界重载清掉帧登记之后它仍然为真 ⇒ 永不重挂，且无日志。改用 FrameTicker。"
    )


# ---------- ③ 实例方法的回调必须传 owner ----------


def test_instance_method_callbacks_pass_owner():
    """`_dispatcher` / `_initMonitor` 的回调是实例方法，必须把实例作为 owner 传进去。

    不传的后果见本文件头"另一个更隐蔽的坑：owner" —— 主线程调度器会静默停摆，
    表现是"Python 的每次工具调用都超时"，而日志一切正常。
    """
    src = _code_only(_read(CS / "app" / "ModMain.cs"))
    m = re.search(r"FrameTicker\.Ensure\(\s*FrameTicker\.KDispatcher\s*,(.*?)\)\s*;", src, re.S)
    assert m, "KDispatcher 的注册不见了"
    assert "_dispatcher" in m.group(1).split(",")[-1], \
        "KDispatcher 没有把 `_dispatcher` 作为 owner 传入 —— 会挂到已被 Dispose 的旧实例上"

    m2 = re.search(r"FrameTicker\.Ensure\(\s*FrameTicker\.KNpcMonitor\s*,(.*?)\}\s*,\s*(\w+)\s*\)\s*;",
                   src, re.S)
    assert m2, "KNpcMonitor 的注册不见了（或是形状变了，请同步本断言）"
    assert m2.group(2) == "_initMonitor", \
        f"KNpcMonitor 的 owner 是 '{m2.group(2)}'，应为 `_initMonitor`"


def test_ensure_supports_owner_and_rebinds():
    """`FrameTicker.Ensure` 必须有 owner 参数，且**引用不等时要撤旧挂新**。

    只加参数不判等于没修：`Ensure` 仍会在第 ① 步"有句柄且活着"直接 return。
    """
    src = _read(CS / "app" / "FrameTicker.cs")
    body = _code_only(src)
    assert "object owner = null" in body, "Ensure 没有 owner 参数"
    assert "RebindNeeded(" in body, "Ensure 没有判 owner 是否换了宿主"
    assert "ReferenceEquals(" in body, "owner 判等必须是**引用相等**（同类型不同实例要能分辨）"
    # 撤旧之后必须让 has=false，否则 Decide 会看到"还活着"直接 return。
    # 用花括号配平取块（正则的非贪婪会停在 `catch { }` 的内层括号上）。
    i = body.find("if (has && RebindNeeded(key, owner))")
    assert i >= 0, "找不到 owner 换绑分支"
    j = body.index("{", i)
    depth, end = 0, -1
    for k in range(j, len(body)):
        if body[k] == "{":
            depth += 1
        elif body[k] == "}":
            depth -= 1
            if depth == 0:
                end = k
                break
    assert end > j, "换绑分支没闭合"
    assert "has = false" in body[j:end], \
        "换绑之后没有把 has 置 false —— 后面 Decide 仍会直接 return"
    # StopAll 要把 owner 表一起清（否则下个世界的 Ensure 会误判"宿主管过"）
    assert "_owner.Clear()" in body, "StopAll 没有清 owner 表"


# ---------- ④ idle 必须有区分度 ----------


def test_describe_distinguishes_just_attached_from_stalled():
    """`Describe()` 的 idle 必须分得开"刚挂上"与"挂了但没跑"。

    老实现一律 `?`，而 `Init` 恰好只在"刚挂上"那一刻打 ⇒ 这一行在最需要它的位置没有信息。
    判据在核里（`FrameTickerCore.IdleLabel`），接线层只搬结论 —— 这里两头都要锁。
    """
    ticker = _code_only(_read(CS / "app" / "FrameTicker.cs"))
    assert "FrameTickerCore.IdleLabel(" in ticker, \
        "Describe 没有用 IdleLabel 判据（自己拼 idle 会与核的口径分叉）"
    assert ": \"?\"))" not in ticker and 'idle="?"' not in ticker, \
        "Describe 里还有写死的 '?' 分支"

    core = _code_only(_read(CS / "app" / "FrameTickerCore.cs"))
    assert "IdleLabel(" in core, "核里没有 IdleLabel"
    assert "AliveGraceFrames" in core, "IdleLabel 没有沿用同一个宽限常量"
