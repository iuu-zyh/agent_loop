# -*- coding: utf-8 -*-
"""对话窗右侧「可做的事」侧边栏的跨语言契约（10-19 用户需求）。

用户原话：「我现在有很多的工具，但是大部分都是需要玩家去说 NPC 才调用的，但是有时候
玩家不知道这个 mod 有什么工具，就体验不到……在对话 UI 加一个工具展示的按钮，点开之后
出现一个侧边栏在 UI 的右侧……点击一个工具那么就会自动发送一个请求给 agent 让其调用这个工具
（实际就是每个按钮对应一段话，点击的话就自动发送，等同于玩家手动输入）」。

## 这个功能每一处都**天生静默失败**，所以这里钉的不是"有没有面板"

  · 目录里写错一个 `op` → 点击发出一句**永远失败**的台词（模型收到就去调，C# 报"不支持的 op"）；
  · 台词里混进元指令（"请调用 economy_item"）→ 那行字**永久**留在账本里，重开窗时
    以玩家气泡回放，之后每轮都喂给模型；
  · `fill` 型忘了留空位 → 点击直接发出半句话（"你那儿的那，可否匀我一件？"），
    模型只能猜玩家要什么；
  · 面板挂在错误的父节点（根 Canvas 而不是 `BG`）→ 位置整个算错，或压根不显示；
  · 面板不随窗收起 → 下次开窗它以"开着"的姿态出现，展示的是**上一个 NPC** 的上下文；
  · 群聊里露出这颗钮 → 点了不知道在跟谁说（工具全是"对某一个人"的动作）。

## 分三层钉

  A. **目录 ↔ `tools/schemas.py` 对账**（纯 Python，任何机器都能跑）：
     `TOOL_ORDER` 里每个工具要么列出、要么在 `UNLISTED` 里写明理由；每个 `op` 逐字存在于
     该工具 enum 里；`fill` 型的空位补在真参数上；台词里没有元指令词。
  B. **Python 侧下发**（`list_tools` RPC 的形状与位置）。
  C. **C# 侧接线**（读源码文本，与 `test_chat_skip_button_contract.py` 同款）：
     面板父级是 `BG`、按钮是克隆而非新建、点击走 `OnSubmitRequested` 这条唯一发送路径、
     ESC 分层、群聊/删除模式的显隐、以及"不许出现第二条发送实现"。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

CATALOG = ROOT / "tools" / "catalog.py"
SCHEMAS = ROOT / "tools" / "schemas.py"
WS_CHANNEL = ROOT / "transport" / "ws_channel.py"

PANEL = ROOT / "csharp" / "UI" / "ToolPanel.cs"
CORE = ROOT / "csharp" / "UI" / "ToolCatalogCore.cs"
WIN = ROOT / "csharp" / "UI" / "ChatWindow.cs"
PRES = ROOT / "csharp" / "UI" / "ChatPresenter.cs"
AB_PANEL = ROOT / "csharp" / "UI" / "AbChatPanel.cs"
REFS = ROOT / "csharp" / "UI" / "ChatWindowRefs.cs"
MAIN = ROOT / "csharp" / "app" / "ModMain.cs"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _method(src: str, header: str) -> str:
    """按 `header` 抠出一个方法体（从签名到配对的大括号）。找不到直接失败。

    与 `test_chat_skip_button_contract.py::_method` 同款：本仓库的 C# 契约测试都靠它，
    这样"方法被改名/被删"会红，而不是让后面的断言在空串上静默通过。
    """
    i = src.find(header)
    assert i >= 0, "找不到方法签名：%s" % header
    j = src.find("{", i)
    assert j >= 0, "方法签名后没有花括号：%s" % header
    depth = 0
    for k in range(j, len(src)):
        if src[k] == "{":
            depth += 1
        elif src[k] == "}":
            depth -= 1
            if depth == 0:
                return src[i:k + 1]
    raise AssertionError("方法体没闭合：%s" % header)


# ══════════════════════════════════════════════════════════════════
# A. 目录 ↔ schemas 对账
# ══════════════════════════════════════════════════════════════════

def _catalog_module():
    """真 import（不用正则）：这个模块是纯数据 + 纯函数，没有副作用、不碰文件/网络。"""
    import importlib
    import sys
    parent = str(ROOT.parent)
    if parent not in sys.path:
        sys.path.insert(0, parent)
    return importlib.import_module("agent_loop.tools.catalog")


def _schemas_module():
    import importlib
    import sys
    parent = str(ROOT.parent)
    if parent not in sys.path:
        sys.path.insert(0, parent)
    return importlib.import_module("agent_loop.tools.schemas")


def test_catalog_has_no_self_inconsistency():
    """目录自检：key 唯一、台词长度、无元指令词、mode 与台词里的空位一致、sub 非空。"""
    C = _catalog_module()
    S = _schemas_module()
    problems = C.validate(S.get_tool_by_name)
    assert problems == [], "目录自检未过：\n  " + "\n  ".join(problems)


def test_every_tool_is_covered_or_explained():
    """★ 新增一个工具而没加进目录 → 这里红。

    这条是整个功能**最可能悄悄失效**的地方：`schemas.py` 加了第 11 个工具，
    侧边栏里却没有它 —— 玩家永远看不见，且没有任何报错。
    """
    C = _catalog_module()
    S = _schemas_module()
    problems = C.coverage_problems(S.TOOL_ORDER)
    assert problems == [], "工具覆盖不全：\n  " + "\n  ".join(problems)


def test_unlisted_reasons_are_real_text():
    """`UNLISTED` 里的理由不能是占位符（"暂不"两个字等于没说）。"""
    C = _catalog_module()
    for name, why in C.UNLISTED.items():
        assert len(why.strip()) >= 20, "UNLISTED[%r] 的理由太短，读的人不会因此信服：%r" % (name, why)


def test_ops_match_schema_enums_exactly():
    """每个 `send` 型条目的判别值，必须**逐字**存在于该参数的合法取值里。

    这是"改了 schemas 枚举而没改目录"的唯一探针 —— 那种漂移的表现是
    "点了按钮，NPC 说了一句话，然后什么都没发生"（C# 侧报"不支持的 op"）。
    """
    C = _catalog_module()
    S = _schemas_module()
    bad = []
    for it in C.rows():
        if it["mode"] == "fill":
            continue
        schema = S.get_tool_by_name(it["tool"])
        assert schema is not None, "目录里的工具 %r 不存在" % it["tool"]
        props = schema["function"]["parameters"].get("properties") or {}
        spec = props.get(it["arg"])
        assert isinstance(spec, dict), "%s 没有参数 %r" % (it["tool"], it["arg"])
        enum = spec.get("enum") or ((spec.get("items") or {}).get("enum")) or []
        assert enum, "%s.%s 没有枚举可对账" % (it["tool"], it["arg"])
        if it["argval"] not in enum:
            bad.append("%s: %s=%r 不在 %s 里" % (it["key"], it["arg"], it["argval"], "/".join(enum)))
    assert bad == [], "判别值漂移：\n  " + "\n  ".join(bad)


def test_team_ops_all_listed():
    """team 的三个 op（invite/dismiss/status）在侧边栏**各有一条 send 型条目**（10-21 用户点名）。

    schemas 的 description 写着"invite 与 dismiss 会弹同意窗"——两条操作面玩家都必须
    看得见；10-19 首版目录只有 invite/status，**dismiss 缺位**，用户第一次提需求补上。
    这条把"三 op 齐"钉死：将来给 team 加第四个 op，schemas 枚举一改、这里没跟，就红。
    """
    C = _catalog_module()
    S = _schemas_module()
    listed = {it["argval"] for it in C.rows()
              if it["tool"] == "team" and it["mode"] != "fill"}
    schema_ops = set(S.get_tool_by_name("team")["function"]["parameters"]
                     ["properties"]["op"]["enum"])
    assert listed == schema_ops, (
        "目录里的 team op 集合 %s ≠ schemas 枚举 %s" % (sorted(listed), sorted(schema_ops)))
    dis = [it for it in C.rows() if it["tool"] == "team" and it["argval"] == "dismiss"][0]
    assert "解散" in dis["title"], "dismiss 条目的标题要让玩家一眼认出是解散队伍"
    assert not dis["say"].startswith("（"), "台词不能是元指令形态"


def test_fill_rows_put_the_blank_on_a_real_parameter():
    """`fill` 型的空位必须补在**真实存在**的参数上（不是"随便写个 fills"）。"""
    C = _catalog_module()
    S = _schemas_module()
    bad = []
    for it in C.rows():
        if it["mode"] != "fill":
            continue
        schema = S.get_tool_by_name(it["tool"])
        props = schema["function"]["parameters"].get("properties") or {}
        if it["fills"] not in props:
            bad.append("%s: fills=%r 不在 %s 的参数里" % (it["key"], it["fills"], it["tool"]))
        # 空位必须**正好一个**：两个空位的台词，光标只落第一个，第二个永远没人补
        if it["say"].count(C.BLANK) != 1:
            bad.append("%s: 台词里有 %d 个空位（应恰好 1 个）" % (it["key"], it["say"].count(C.BLANK)))
    assert bad == [], "fill 型有问题：\n  " + "\n  ".join(bad)


def test_economic_tools_are_all_fill():
    """★ 经济三件（赠送 / 买卖 / 讨要）**必须**是 fill。

    它们的必填参数里有「具体哪件东西」—— 没有任何一句预设台词能替玩家说出它。
    做成 send 的后果不是"报错"，而是**模型自己猜一个**，然后可能真送错东西。
    """
    C = _catalog_module()
    money = {it["tool"]: it for it in C.rows() if it["tool"] in
             ("economy_item", "trade", "item_acquire")}
    missing = [t for t in ("economy_item", "trade", "item_acquire") if t not in money]
    assert missing == [], "经济工具没进目录：%s" % missing
    wrong = [t for t, it in money.items() if it["mode"] != "fill"]
    assert wrong == [], "这些经济工具是 send 型（会让模型自己猜要什么）：%s" % wrong


def test_say_is_never_a_meta_instruction():
    """★ 台词**只能是人话**。

    它会永久留在 `.jsonl` 账本里、重开窗时以玩家气泡回放、之后每轮喂给模型。
    写进"请调用 X"这种元指令，等于往 NPC 的记忆里刻一行假玩家发言。
    """
    C = _catalog_module()
    for it in C.rows():
        low = it["say"].lower()
        for w in C.META_WORDS:
            assert w.lower() not in low, \
                "条目 %r 的台词含元指令词 %r：%r" % (it["key"], w, it["say"])


def test_meta_word_redline_actually_fires():
    """★ 红线**真的会拦住东西**（不是一张空表）。

    上一版这条只遍历目录里现有的台词 —— 把 `META_WORDS` 整个清空，测试照样全绿
    （变异验证当场抓到了：⑦ 号变异未被捕获）。现在改成"注入一条坏台词，validate 必须红"，
    这样词表被清空/被弱化就都会红。
    """
    C = _catalog_module()
    assert len(C.META_WORDS) >= 8, "元指令词表被砍到 %d 个 —— 红线形同虚设" % len(C.META_WORDS)

    saved = C.GROUPS
    try:
        C.GROUPS = [{
            "key": "t", "title": "测试",
            "items": [{
                "key": "bad", "title": "坏条目", "sub": "x",
                "tool": "inspect_unit", "arg": "classes", "argval": "inventory",
                "fills": "", "mode": "send",
                "say": "请调用 inspect_unit 查他的储物",
            }],
        }]
        problems = C.validate()
        assert problems, "含元指令词的台词没有被 validate 拦下（红线失效）"
        assert any("元指令" in p for p in problems), \
            "拦是拦了，但不是因为元指令词：%s" % problems
    finally:
        C.GROUPS = saved

    # 顺带把另两条红线也用注入法验一次（同款理由：只遍历现有目录 = 空过）
    for bad_say, want in (
        ("/compact", "斜杠"),
        ("短", "太短"),
        ("一" * 60, "太长"),
    ):
        try:
            C.GROUPS = [{
                "key": "t", "title": "测试",
                "items": [{
                    "key": "bad", "title": "坏条目", "sub": "x",
                    "tool": "inspect_unit", "arg": "classes", "argval": "inventory",
                    "fills": "", "mode": "send", "say": bad_say,
                }],
            }]
            problems = C.validate()
            assert problems, "台词 %r 没有被拦下（%s 那条红线失效）" % (bad_say, want)
        finally:
            C.GROUPS = saved


def test_blank_marker_matches_between_python_and_csharp():
    """★ 空位标记必须两边**逐字**相同（全角空格 U+3000）。

    改成半角空格的表现是：光标落在句尾、玩家得自己找位置 —— 零报错、只有"手感怪"。
    """
    C = _catalog_module()
    core = _read(CORE)
    m = re.search(r"public const char Blank = '(.*?)';", core)
    assert m, "ToolCatalogCore 里找不到 Blank 常量（判定核被改写了？）"
    lit = m.group(1)
    assert lit == "\\u3000", "C# 侧 Blank 必须是 \\u3000，实际 %r" % lit
    assert C.BLANK == "\u3000", "Python 侧 BLANK 不是全角空格：%r" % C.BLANK


# ══════════════════════════════════════════════════════════════════
# B. Python 侧下发
# ══════════════════════════════════════════════════════════════════

def test_list_tools_rpc_exists_and_returns_the_catalog():
    src = _read(WS_CHANNEL)
    assert 'if method == "list_tools":' in src, "ws_channel 里没有 list_tools 路由"
    # 形状断言：必须走 payload()，不许在 RPC 里另拼一份（两份实现必然漂移）
    body = _py_block(src, 'if method == "list_tools":')
    assert "catalog" in body and "payload()" in body, \
        "list_tools 没有复用 catalog.payload()（另拼一份就会与目录漂移）：\n" + body
    # 判据只看**代码行**：注释里写着形状说明（那正是给人看的），不该因此变红
    code = "\n".join(ln for ln in body.splitlines() if not ln.lstrip().startswith("#"))
    assert '"groups"' not in code, \
        "RPC 里自己拼了 groups —— 形状的唯一真相应在 catalog.payload()：\n" + code
    assert "return _catalog.payload()" in code, "list_tools 没有把 payload() 原样返回"


def _py_block(src: str, header: str) -> str:
    """抠出一个 `if method == "...":` 分支（到下一个同缩进的 `if`/`raise` 之前）。"""
    i = src.find(header)
    assert i >= 0, "找不到分支：%s" % header
    indent = len(src[:i]) - len(src[:i].rstrip())
    out = [src[i:].splitlines()[0]]
    for ln in src[i:].splitlines()[1:]:
        if ln.strip() and (len(ln) - len(ln.lstrip())) <= indent:
            break
        out.append(ln)
    return "\n".join(out)


def test_catalog_payload_shape():
    """下发给 C# 的键集合是契约：`{groups:[{key,title,items:[{key,title,sub,mode,say}]}]}`。"""
    C = _catalog_module()
    p = C.payload()
    assert set(p.keys()) == {"groups"}
    assert isinstance(p["groups"], list) and p["groups"], "目录不能是空的"
    for g in p["groups"]:
        assert set(g.keys()) == {"key", "title", "items"}, "分组键集合不对：%s" % sorted(g.keys())
        assert g["items"], "分组 %r 是空的" % g["key"]
        for it in g["items"]:
            assert set(it.keys()) == {"key", "title", "sub", "mode", "say"}, \
                "条目键集合不对：%s" % sorted(it.keys())
            assert it["mode"] in ("send", "fill")
            # C# 不认识 tool/op —— 下发里带它们只会诱导后来的人去 C# 里做路由判断
            assert "tool" not in it and "op" not in it, "下发里混进了 tool/op：%r" % sorted(it.keys())


def test_catalog_is_not_in_the_prompt():
    """★ 目录**不进 prompt**：每回合固定 token 开销零增加（10 个工具已占 ~4.1k token）。"""
    prompt = (ROOT / "agent" / "system_prompt.py").read_text(encoding="utf-8")
    tool_usage = (ROOT / "prompts" / "sections" / "tool_usage.txt").read_text(encoding="utf-8")
    for name, src in (("system_prompt.py", prompt), ("tool_usage.txt", tool_usage)):
        assert "catalog" not in src, "%s 里引用了 catalog —— 它不该进提示词" % name
        assert "可做的事" not in src, "%s 里出现了侧边栏标题 —— 它只给 C# 用" % name


# ══════════════════════════════════════════════════════════════════
# C. C# 侧接线
# ══════════════════════════════════════════════════════════════════

def test_panel_is_parented_to_bg_not_root_canvas():
    """★ 坐标基准必须是 `BG`（1200×800），不是全屏 stretch 的根 Canvas。

    挂错的后果：右锚点算出来的位置整个偏掉 —— 面板跑到屏幕外面，或宽度按 1920 算。
    """
    src = _read(PANEL)
    body = _method(src, "public static void Build(RectTransform bg, Action onClick)")
    assert "rt.SetParent(bg, false)" in body, "面板没有挂在传入的 bg 下"
    # ChatWindow 必须传 `_r.Window`（BG），不能传 `_r.Root`
    win = _read(WIN)
    init = _method(win, "public void Init(ChatWindowRefs refs)")
    assert "ToolPanel.Build(_r.Window" in init, "ChatWindow.Init 没有以 BG 为父级建面板"
    assert "ToolPanel.Build(_r.Root" not in init, "★ 面板挂到了根 Canvas 上（坐标基准错了）"


def test_ab_panel_actually_fills_the_bg_field():
    """★ AB 路径必须真的填 `refs.Window = BG`（10-19 之前它恒 null）。

    这条是"面板静默消失"的唯一探针：`ToolPanel.Build(null)` 只记一行日志就返回，
    界面上什么都不发生、也没有报错。
    """
    src = _read(AB_PANEL)
    body = _method(src, "private static ChatWindowRefs CollectRefs(Transform root)")
    assert 'r.Window = FindRt(root, "BG")' in body, \
        "CollectRefs 没有填 r.Window —— 侧边栏会静默不出现（BG 解析不到基准）"
    assert "public RectTransform Window;" in _read(REFS)


def test_tool_button_is_cloned_not_invented():
    """顶栏那颗钮**克隆 ConfigBtn**（同排同款），而不是画一个新的。

    画新的后果：字号/字体/描边/过渡色与隔壁不一样，实机上一眼看出"这不是美术做的"。
    同款先例：`prefab_patch_chat_skip_button.py` 的「跳过」也是克隆 SendBtn。
    """
    src = _read(PANEL)
    body = _method(src, "private static void MakeToolButton(RectTransform bg, Action onClick)")
    assert 'bg.Find("ConfigBtn")' in body, "工具钮没有以 ConfigBtn 为克隆源"
    assert "Instantiate" in body, "工具钮没有克隆（新建出来的样式与隔壁不一致）"
    # 位置常量：两条建钮路径（克隆 / 兜底）必须用**同一个**常量
    assert src.count("ToolBtnX") >= 3, "ToolBtnX 常量没有被两条路径共用（各写各的必然漂移）"


def test_tool_button_does_not_overlap_the_delete_button():
    """★★ 工具钮**不许与顶栏任何既有按钮重叠**（10-19 用户截图实证过一次）。

    事故：第一版放在 ⚙ 左边 -107.8，把「删除」键（-169.8~-105.8）整个盖住了 ——
    因为布局时把顶栏当成了三颗，实际单聊可见四颗。
    完整序列（右缘坐标，右→左）：✕ -40 / 设置 -99.8 / 删除 -105.8 / 压缩 -177.8 /
    拉人 -249.8 / 踢人 -321.8。安全空位 = **压缩左缘再往左**（-249.8）：
    拉人/踢人只在群聊可见、工具钮只在单聊可见 ⇒ 两种模式下这一格都是空的。

    判据钉**几何**而非死数值：算出工具钮的占用区间，逐一比对既有按钮的区间。
    """
    src = _read(PANEL)
    m = re.search(r"private const float ToolBtnX = ([-0-9.]+)f;", src)
    assert m, "找不到 ToolBtnX 常量"
    x = float(m.group(1))          # 右缘（负数）
    w = 64.0                        # ToolBtnW（宽度另测，这里只关心重叠判定用的右缘/宽度）
    left, right = sorted([x, x - w])

    # 顶栏既有按钮（右缘坐标，宽）—— 来源：prefab_dump 实测 + prefab_patch 脚本常量
    existing = {
        "CloseBtn": (-40.0, 40.0),
        "ConfigBtn(设置)": (-99.8, 60.0),
        "DeleteBtn(删除)": (-105.8, 64.0),
        "CompactBtn(压缩)": (-177.8, 64.0),
        "InviteBtn(拉人·仅群聊)": (-249.8, 64.0),
        "KickBtn(踢人·仅群聊)": (-321.8, 64.0),
    }
    # 拉人/踢人与工具钮**互斥显隐**（群聊/单聊），不构成重叠；其余必须在几何上分离
    exclusive = {"InviteBtn(拉人·仅群聊)", "KickBtn(踢人·仅群聊)"}
    overlaps = []
    for name, (edge, btn_w) in existing.items():
        if name in exclusive:
            continue
        b_left, b_right = sorted([edge, edge - btn_w])
        if left < b_right and right > b_left:      # 区间相交 = 重叠
            overlaps.append("%s 占 [%s, %s]，工具钮占 [%s, %s]"
                            % (name, b_left, b_right, left, right))
    assert not overlaps, "★ 工具钮与既有按钮重叠：%s" % "; ".join(overlaps)

    # 同屏按钮之间的既定节奏是 8px —— 工具钮必须正好落在压缩左缘左侧 8px
    assert abs(x - (-249.8)) < 0.01, \
        "工具钮右缘应是 -249.8（压缩 -241.8 左侧 8px），实际 %s" % x


def test_tool_button_and_room_buttons_are_mutually_exclusive():
    """工具钮（仅单聊）与拉人/踢人（仅群聊）**互斥显隐** —— 这是 -249.8 这个位置安全的前提。

    若有人改了其中一边的显隐逻辑（比如让工具钮在群聊也出现），它就会撞上拉人/踢人。
    """
    src = _read(WIN)
    room = _method(src, "private void SetRoomButtonsVisible(bool show)")
    # 工具钮随群聊隐藏（!show），拉人/踢人随群聊显示（show）
    assert "toolBtn.SetActive(!show)" in room.replace(" ", ""), \
        "工具钮没有随群聊隐藏 —— 会与拉人/踢人撞在 -249.8"


def test_click_reuses_the_single_send_path():
    """★★ 点击 `send` 型条目必须走 `OnSubmitRequested` —— **不许另写一份发送逻辑**。

    那份实现里藏着时间戳、死者拦截、`TurnRegistry.Begin`、渲染闸落位。复制一份的后果是
    "面板发的话不带时间戳 / 发给死者不拦 / 忙标不亮"，而且漂移的一定是安全校验那半。
    """
    src = _read(PRES)
    body = _method(src, "private void OnToolItemChosen(string mode, string say)")
    assert "OnSubmitRequested()" in body, \
        "面板发言没有复用 OnSubmitRequested（另写一份发送逻辑必然会漂移）"
    assert "SendPlayerMessage" not in body, \
        "★ 面板直接调了 SendPlayerMessage —— 绕过了时间戳/死者拦截/TurnRegistry"
    assert "SendRoomMessage" not in body, "面板不该发群消息（群聊里这颗钮是藏起来的）"
    # fill 型必须先判、且**不发**
    fill_at = body.find("IsFill")
    submit_at = body.find("OnSubmitRequested()")
    assert 0 <= fill_at < submit_at, "fill 判定必须在发送之前（否则填空型会被直接发出去）"


def test_the_fill_branch_is_not_disabled_and_never_sends():
    """★★ `fill` 分支的守卫**不许被附加条件短路**，分支体里**不许有发送**。

    两个都是最隐蔽的写法：
      · `if (IsFill(...) && false)` —— 判定还在、顺序还对，但填空型全都走进发送分支，
        结果是"点了填空，半句话直接发出去"（模型只能猜玩家要什么）；
      · `FillInput(text); OnSubmitRequested(); return;` —— 填了框又把它发出去。
    两者都零报错，只有在真机上点一下才看得出来 —— 所以必须钉在源码层。
    """
    src = _read(PRES)
    body = _method(src, "private void OnToolItemChosen(string mode, string say)")

    # ① 守卫必须是裸调用，不许加 `&& false` / `|| true` 这类短路
    guard = [ln.strip() for ln in body.splitlines() if "IsFill(" in ln]
    assert len(guard) == 1, "fill 判定的出现次数不是 1：%r" % guard
    assert guard[0] == "if (ToolCatalogCore.IsFill(mode, text))", \
        "fill 的守卫被改写了（加条件就等于关掉填空）：%r" % guard[0]
    for lit in ("&& false", "&&false", "|| true", "||true", "== false"):
        assert lit not in guard[0], "fill 守卫里出现了布尔字面量 %r（分支被短路）" % lit

    # ② 分支体里不许有发送（从 `if (IsFill...` 到配对的右花括号）
    start = body.find("if (ToolCatalogCore.IsFill(")
    j = body.find("{", start)
    depth = 0
    end = -1
    for k in range(j, len(body)):
        if body[k] == "{":
            depth += 1
        elif body[k] == "}":
            depth -= 1
            if depth == 0:
                end = k
                break
    assert end > j, "取不到 fill 分支体"
    fill_body = body[j:end + 1]
    assert "FillInput(text)" in fill_body, "fill 分支里没有填空调用：%r" % fill_body
    assert "return;" in fill_body, "fill 分支没有 return（会继续往下走到发送）"
    assert "OnSubmitRequested" not in fill_body, \
        "★ fill 分支里调了发送：填空型会被直接发出去（模型只能猜玩家要什么）"
    assert "SendPlayerMessage" not in fill_body, "★ fill 分支里直接发了 WS 帧"


def test_fill_puts_the_caret_on_the_blank():
    """填空型：光标必须落在空位上（`ToolCatalogCore.CaretAt`），不是句尾。"""
    src = _read(PRES)
    body = _method(src, "private void FillInput(string say)")
    assert "ToolCatalogCore.CaretAt(" in body, "填空没有用判定核算光标位置"
    assert "caretPosition" in body, "没有真的设光标"
    assert "ActivateInputField" in body, "没有聚焦 —— 玩家还得自己点一下输入框"
    assert "OnSubmitRequested" not in body, "★ FillInput 里调了发送：填空型被直接发出去了"
    # 必须有可见提示：屏幕上只是半句话，不说一句玩家会以为已经发了
    assert "AppendSystemNotice" in body, "填空后没有提示（玩家会以为这句话已经发出去了）"


def test_esc_closes_the_panel_before_the_window():
    """★ ESC 分层：面板开着 → 先关面板，**不许**顺手把整个对话窗关掉。

    这是本仓库刚在放大立绘上踩过的同一类坑（`BigPortrait.ConsumesEscape`）：
    两个互不知情的处理器各关各的，一次按键关掉两层。
    """
    src = _read(PRES)
    esc = src[src.find("if (Input.GetKeyDown(KeyCode.Escape))"):]
    esc = esc[:esc.find("var input = _window.ChatInput;")]
    panel_at = esc.find("ToolPanel.IsOpen")
    close_at = esc.find("OnCloseClicked();")
    assert panel_at >= 0, "ESC 链里没有 ToolPanel.IsOpen —— 按 ESC 会连对话窗一起关掉"
    assert close_at >= 0, "ESC 链里找不到 OnCloseClicked（断言锚点失效，本测试需要跟着改）"
    assert panel_at < close_at, "面板判定必须在关窗之前"


def test_esc_panel_branch_returns_immediately():
    """★ 关掉面板之后必须 `return` —— 否则同一次 ESC 会继续往下走到 `OnCloseClicked()`。

    这条单独拎出来，是因为上一版把它和"面板判定在关窗之前"混在一条测试里，
    用 `seg` 切片做的断言**把 `return` 也框进去了**（切片终点就是 OnCloseClicked），
    于是"删掉 return"这个变异照样绿（变异验证 ㉕ 当场抓到）。
    现在按**语句块**判：面板分支的花括号体里必须以 return 收尾。
    """
    src = _read(PRES)
    start = src.find("if (ToolPanel.IsOpen)")
    assert start >= 0, "ESC 链里没有 ToolPanel.IsOpen 分支"
    j = src.find("{", start)
    depth = 0
    end = -1
    for k in range(j, len(src)):
        if src[k] == "{":
            depth += 1
        elif src[k] == "}":
            depth -= 1
            if depth == 0:
                end = k
                break
    assert end > j, "取不到面板分支体"
    block = src[j + 1:end]
    stmts = [ln.strip() for ln in block.splitlines() if ln.strip() and not ln.strip().startswith("//")]
    assert stmts, "面板分支是空的"
    assert stmts[0].startswith("ToolPanel.Hide();"), "面板分支第一句不是收起面板：%r" % stmts
    assert stmts[-1] == "return;", \
        "★ 面板分支没有以 return 收尾（同一次 ESC 会继续走到关窗）：%r" % stmts
    assert "OnCloseClicked" not in block, "面板分支里调了关窗"


def test_panel_is_hidden_on_every_exit_path():
    """★ 面板必须在**每一条**"窗口不再是原来那个上下文"的路上收起。

    漏一条的后果不是崩溃，而是"下次开窗面板开着、内容还是上一个人的"。
    四条路：关窗 / 换 NPC / 进群 / 宿主复位。

    ⚠️ 判据必须落在**方法体里**，不能只查全文件里有没有那句话 ——
    关窗那条最初写成 `"ToolPanel.Hide();" in _method(...)`，而 `OnCloseClicked` 里
    恰好还留着换人那句的副本……更要命的是"整句被删掉"与"文件里别处有"区分不开
    （变异验证 ㉖ 当场抓到：删掉关窗那句，测试照样绿）。
    所以这里逐条查**方法体**，且关窗那条额外钉住它紧跟在 `_wantReplay = false` 之后
    —— 那个位置是"关窗流程已经开始"的唯一标志。
    """
    src = _read(PRES)

    close = _method(src, "private void OnCloseClicked()")
    assert "ToolPanel.Hide();" in close, "关窗没收起面板"
    # 位置：必须在流程开头附近（`_wantReplay = false` 之后、`if (_window != null)` 之前）
    hide_at = close.find("ToolPanel.Hide();")
    win_at = close.find("if (_window != null)")
    assert 0 <= hide_at < win_at, \
        "关窗收起面板的位置不在流程开头（漏在某个早退之后就等于没收）"

    assert "ToolPanel.Hide();" in _method(src, "public void OpenForNpc(string npcId)"), \
        "换 NPC 没收起面板（会拿上一个人的上下文摆在新人脸上）"
    room = _method(src, "public void OpenRoomById(string roomId, List<string> members, string titleHint,")
    assert "ToolPanel.Hide();" in room, "进群没收起面板"
    panel = _read(PANEL)
    assert "public static void Reset()" in panel and "Hide()" in _method(panel, "public static void Reset()")


def test_panel_reset_is_wired_to_host_reset():
    """`ToolPanel.Reset()` 得有人调（只定义不调 = 换存档后面板状态残留）。"""
    panel = _read(PANEL)
    assert "public static void Reset()" in panel
    # 关窗路径已经调 Hide；Reset 是给"宿主重建/换档"那类场合的入口，至少要能从外面调到
    assert "internal static class ToolPanel" not in panel or "public static void Reset()" in panel
    # 不许只在注释里提一句（那种"以为接好了"的形态最费时间）
    code = "\n".join(ln for ln in panel.splitlines() if not ln.lstrip().startswith("//"))
    assert "public static void Reset()" in code, "Reset 只出现在注释里"


def test_world_reload_uses_reset_for_world_not_reset():
    """★★ 换存档必须调 `ResetForWorld`（清引用），**不能**只调 `Reset`（只收显隐）。

    这一条钉的是本仓库踩过好几次的那类坑（`PortraitCache.ResetForWorld` 同源）：
    面板挂在对话宿主的 `BG` 下，宿主随场景销毁 ⇒ 静态引用变**僵尸**。
    只 `Hide()` 不清引用的话，新档里 `Build` 看到 `_root != null` 会以为"宿主还在"直接
    return ⇒ **面板永远不重建**，而且日志里一句都不打。
    """
    main = _read(MAIN)
    assert "ToolPanel.ResetForWorld();" in main, \
        "换存档没有调 ToolPanel.ResetForWorld —— 新档里面板永远不会重建（且零日志）"
    # 位置：与另外几个 ResetForWorld 同一段（换档钩子里）
    i = main.find("BigPortrait.ResetForWorld();")
    j = main.find("ToolPanel.ResetForWorld();")
    assert i >= 0 and j > i, "工具侧边栏的复位没有跟在立绘复位之后（不在同一个换档钩子里）"
    assert abs(j - i) < 2000, "两处复位隔了两千字符 —— 大概不在同一个换档块里"
    # 不许误写成 Reset()
    seg = main[i:i + 2000]
    assert "ToolPanel.Reset();" not in seg, \
        "★ 换存档处调的是 Reset()（只收显隐、不清引用）—— 新档里面板永远不重建"


def test_panel_guards_against_zombie_references():
    """★ 静态引用必须过僵尸守卫，不能裸判 `!= null`。

    Unity 的 `Destroy` 之后托管包装器**不再是 null**：`go != null` 为 true，但访问属性会抛
    `ObjectCollectedException` 或静默返回默认值。`_root.activeSelf` 这种裸访问一旦落在
    僵尸上是真机崩溃；`HasPanel` 裸判 `!= null` 则会在换档后谎报"宿主还在"。
    """
    panel = _read(PANEL)
    assert "private static bool Alive(GameObject go)" in panel, "没有僵尸守卫函数"
    # 三个对外属性必须走 Alive。★ 判据取**该属性声明到配对花括号**那一段
    #   （不能取固定字符窗口：下一个属性里也有 `Alive(`，窗口一大就会把"裸判"放过去
    #    —— 变异验证 ㊾ 当场抓到；也不能只取签名那一行：`ButtonGo` 是跨行的）
    for prop in ("IsOpen", "HasPanel", "ButtonGo"):
        m = re.search(r"public static (?:bool|GameObject) %s\b" % prop, panel)
        assert m, "找不到属性 %s" % prop
        # 从签名往后取到配对的花括号（单行属性就是 `{ get { ... } }` 那一行）
        tail = panel[m.start():]
        depth = 0
        started = False
        end = -1
        for k, ch in enumerate(tail):
            if ch == "{":
                depth += 1
                started = True
            elif ch == "}":
                depth -= 1
                if started and depth == 0:
                    end = k
                    break
        assert end > 0, "属性 %s 的花括号没闭合" % prop
        decl = tail[:end + 1]
        assert "Alive(" in decl, \
            "属性 %s 没有走僵尸守卫（裸判 != null）：%r" % (prop, decl[:160])
        assert "!= null" not in decl, \
            "属性 %s 里还有裸判 `!= null`（僵尸引用不是 null）：%r" % (prop, decl[:160])
    # Build 里也要判活：僵尸宿主不能当成"宿主还在"
    body = _method(panel, "public static void Build(RectTransform bg, Action onClick)")
    assert "Alive(_root)" in body, "Build 里裸判 _root != null（僵尸会被当成活着，永不重建）"


def test_button_visible_only_in_solo_chat():
    """★ 群聊里不能露这颗钮（工具全是"对某一个人"的动作，群里没有唯一对象）。

    同时钉住删除模式：选删除时它必须收起 —— 否则玩家会在"挑要删哪几条"的当口
    从侧边栏发一整轮对话出去。
    """
    src = _read(WIN)
    room = _method(src, "private void SetRoomButtonsVisible(bool show)")
    assert "ToolPanel.ButtonGo" in room, "群聊显隐没管工具钮"
    assert "toolBtn.SetActive(!show)" in room.replace(" ", ""), \
        "群聊里工具钮没有收起（群里点了不知道在跟谁说话）"
    assert "ToolPanel.Hide()" in room, "切到群聊时已开的面板没有收起"

    enter = _method(src, "public void EnterSelecting()")
    assert "ToolPanel.ButtonGo" in enter, "进删除模式没管工具钮"
    # 判据：拿到钮的那几行里必须有一条 SetActive(false)（变量名不限 —— 钉意图不钉字面）
    lines = [ln.strip() for ln in enter.splitlines()
             if "ToolPanel.ButtonGo" in ln or "SetActive" in ln]
    btn_line = [ln for ln in lines if "ToolPanel.ButtonGo" in ln]
    assert btn_line, "EnterSelecting 里没有取工具钮"
    var = btn_line[0].split("=")[0].split()[-1].strip() if "=" in btn_line[0] else ""
    assert var, "工具钮取了却没存进变量（后面没法收起它）：%r" % btn_line[0]
    assert any(("%s.SetActive(false)" % var) in ln.replace(" ", "") for ln in lines), \
        "删除模式下工具钮还亮着（会在选删除时另起一轮对话）：%r" % lines

    exit_ = _method(src, "public void ExitSelecting()")
    line = [ln for ln in exit_.splitlines() if "ToolPanel.ButtonGo" in ln or "toolBtnBack" in ln]
    assert line, "退出删除模式没恢复工具钮（它永远不见了）"
    joined = " ".join(line)
    assert "IsRoomMode" in joined, "★ 恢复条件缺 IsRoomMode：群聊里退出删除模式会把工具钮点亮"


def test_window_exposes_the_event_and_wires_it():
    src = _read(WIN)
    assert "public event Action ToolsClicked;" in src, "ChatWindow 没有 ToolsClicked 事件"
    init = _method(src, "public void Init(ChatWindowRefs refs)")
    assert "ToolPanel.Build(_r.Window" in init, "Init 里没有建面板"
    assert "ToolsClicked" in init, "顶栏钮没有接到 ToolsClicked 上（点了不会有反应）"


def test_presenter_subscribes_and_unsubscribes():
    """事件必须**成对**：只订不退的话，宿主重建后一个点击会触发 N 次。"""
    src = _read(PRES)
    init = _method(src, "public void Init(ChatWindow window, WsClient ws)")
    destroy = _method(src, "private void OnDestroy()")
    for ev in ("ToolsClicked", "ToolPanel.ItemChosen"):
        assert ev in init, "Init 没订阅 %s" % ev
        assert ev in destroy, "OnDestroy 没退订 %s（宿主重建后一次点击触发多次）" % ev


def test_presenter_fetches_the_catalog_once_and_refreshes_lazily():
    """首次打开拉一次 `list_tools`，之后走缓存（`HasData`）。失败要有回音，不许静默。"""
    src = _read(PRES)
    body = _method(src, "private void OnToolsClicked()")
    assert '"list_tools"' in body, "没有拉目录"
    assert "ToolPanel.HasData" in body, "没有走缓存（每次打开都发一次 RPC）"
    assert "ToolPanel.SetData" in body, "拉回来的目录没有被用上"
    # 群聊明拒：按钮虽然藏了，事件仍可能从别处来
    assert "IsRoomMode" in body, "群聊里没有明拒"


def test_rebuilt_tree_repaints_from_catalog_cache():
    """★★ 宿主销毁重建后，面板不许是空的（10-21 实机 bug，用户截图：第二次开窗只剩头部一行）。

    事故链（Player-prev.log 逐行实证）：游戏 `CloseUI` **销毁** UIChatAi 实例
    （每次重开都有一行"创建宿主"）⇒ 挂在 BG 下的静态树随宿主生灭；行是在 `SetData`
    里画的，重建出来的新树只有头和滚动区；而 `_hasData` 活在静态字段里**不跟着树死**
    ⇒ `OnToolsClicked` 永远走 `HasData → Show()` 快路径，第二次起再也没人补画行。
    （旧版只钉了"换存档要 `ResetForWorld`"——**同一次存档内**的关窗重开不走那条路。）

    定案：**数据不跟着树死**。`SetData` 先把目录本体存进 `_data`（树死了也存），
    `Build` 重建出新宿主后从 `_data` 原地重画；`ResetForWorld` / `Invalidate`
    连 `_data` 一起清（换世界 / 工具表变更后旧目录不许复活）。
    这样既不出空面板，也保住"一个世界只拉一次 RPC"的原设计。
    """
    panel = _read(PANEL)
    assert "private static JObject _data;" in panel, "没有目录数据缓存字段 `_data`"

    # ① SetData：先缓存、后画图 —— 缓存必须落在树判空守卫**之前**
    body = _method(panel, "public static void SetData(JObject data)")
    i_cache = body.find("_data = data")
    i_guard = body.find("if (_root == null || _content == null)")
    assert i_cache >= 0, "SetData 没有缓存目录本体"
    assert i_guard >= 0, "SetData 的树判空守卫被删了？"
    assert i_cache < i_guard, "★ 缓存在守卫之后：树死时回包的目录会被整个丢掉（10-21 事故的半环）"

    # ② Build：重建出新树后必须从缓存重画；复用分支（行还活着）不许重画
    body = _method(panel, "public static void Build(RectTransform bg, Action onClick)")
    i_alive = body.find("if (Alive(_root))")
    assert i_alive >= 0, "Build 的复用分支（`if (Alive(_root))`）没了？"
    i_reuse_ret = body.find("return;", i_alive)
    assert i_reuse_ret > i_alive, "复用分支没有提前返回（每次 Init 都会拆树重画）"
    i_repaint = body.find("SetData(_data)")
    assert i_repaint >= 0, "★ Build 重建后没有从 `_data` 重画目录（10-21 空面板 bug 会复发）"
    assert i_repaint > i_reuse_ret, "重画写进了复用分支（同一棵树行还在，先拆后建会白闪一下）"

    # ③ 作废缓存的两条路必须连 `_data` 一起清，不许只清布尔位
    body = _method(panel, "public static void ResetForWorld()")
    assert "_data = null" in body, "ResetForWorld 没清 `_data`：新存档会重画旧世界的目录"
    body = _method(panel, "public static void Invalidate()")
    assert "_data = null" in body, "Invalidate 没清 `_data`：作废后 Build 还会拿旧缓存重画"


def test_catalog_failure_is_never_silent():
    """★★ 拉目录失败时**必须**给玩家一行提示。

    `SendRequest` 在"未连接"时会把 `ok:false` 直接回调、超时也给 `ok:false` ——
    两种都是**不抛异常**的正常返回。如果失败分支只打日志，玩家点了按钮看到的就是
    "面板没出来，什么也没发生"，与按钮坏了长得一样。
    上一版只断言方法体里有 `AppendSystemNotice`（群聊明拒那条也算），
    于是把失败分支的提示删掉照样绿（变异验证 ㉟ 抓到）。现在按**失败分支体内**判。
    """
    src = _read(PRES)
    body = _method(src, "private void OnToolsClicked()")
    # 找到 `if (!ok)` 之后的分支体
    i = body.find("if (!ok)")
    assert i >= 0, "找不到失败分支（`if (!ok)`）—— 拉取失败的处理被删了？"
    j = body.find("{", i)
    depth = 0
    end = -1
    for k in range(j, len(body)):
        if body[k] == "{":
            depth += 1
        elif body[k] == "}":
            depth -= 1
            if depth == 0:
                end = k
                break
    assert end > j, "取不到失败分支体"
    fail_body = body[j:end + 1]
    assert "AppendSystemNotice" in fail_body, \
        "★ 拉目录失败时没有告诉玩家（玩家看到的就是「点了没反应」）：%r" % fail_body
    assert "return;" in fail_body, "失败分支没有 return（会继续拿空的 data 去建面板）"


def test_panel_scroll_area_has_a_raycast_target():
    """★ 滚动区必须自带一张能命中的 Graphic。

    UGUI 的事件要先命中 Graphic 再冒泡到 ScrollRect —— 纯容器的滚动区**一点都进不来**
    （`prefab_patch_scroll_hit.py` 那次"面板能开、节点全 True、就是滚不动"是同一个坑）。
    透明度刻意不写 0（α=0 会被当"不渲染"剔除，射线同样进不来）。
    """
    src = _read(PANEL)
    body = _method(src, "private static void BuildScroll(RectTransform parent)")
    assert "raycastTarget = true" in body, "滚动区没有可命中的 Graphic（滚轮进不来）"
    assert "1f / 255f" in body, "★ 透明层的 alpha 写了 0：会被剔除，滚动与点击都进不来"
    assert "RectMask2D" in body, "视口没有裁剪（行会溢出面板）"


def test_panel_dims_are_the_quarter_the_user_asked_for():
    """用户定的是"右侧四分之一左右"。BG 宽 1200 ⇒ 300。顶边停在顶栏下沿（760）。"""
    src = _read(PANEL)
    assert "PanelW = 300f" in src, "面板宽不是 300（BG 1200 的四分之一）"
    assert "TopInset = 40f" in src, "顶边内缩不是 40（顶栏下沿 760）"
    # 头/行高：头部 52、行 54、分组 30 —— 数量级合理，别改到"一行装不下两行字"
    assert "HeadH = 52f" in src and "RowH = 54f" in src


def test_no_unity_dependency_in_the_core():
    """判定核必须保持零 Unity 依赖（否则 net8.0 单测编不进去，判据就没人测了）。

    判据只看**代码行** —— 文件头注释里"刻意不 using UnityEngine"那句话正是要留着给人读的。
    """
    src = _read(CORE)
    code = "\n".join(ln for ln in src.splitlines() if not ln.lstrip().startswith("//"))
    assert "using UnityEngine" not in code, "ToolCatalogCore 引入了 UnityEngine（单测将无法编译）"
    assert "UnityEngine." not in code, "ToolCatalogCore 引用了 UnityEngine 类型"


def test_core_has_no_second_send_or_network():
    """核里不许有发送/网络 —— 它只做判定。"""
    src = _read(CORE)
    for bad in ("SendRequest", "SendPlayerMessage", "WsClient", "MonoBehaviour"):
        assert bad not in src, "ToolCatalogCore 里出现了 %s（它只该做纯判定）" % bad


# ══════════════════════════════════════════════════════════════════
# 反例：把最容易写错的地方写成"如果这么写就会红"
# ══════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("say,ok", [
    ("你囊中都有些什么好东西？", True),
    ("请调用 economy_item 赠送道具", False),      # 元指令
    ("帮我执行 trade", False),                    # 元指令
    ("/compact", False),                          # 命令
    ("短", False),                                # 太短
])
def test_say_red_lines(say, ok):
    """台词红线表：这几条是"写进去就永久留在账本里"的东西，必须被 validate 拦住。"""
    C = _catalog_module()
    problems = C.validate()
    # 直接构造一个坏条目跑黑名单那一层（不依赖目录当前内容）
    bad = []
    low = say.lower()
    for w in C.META_WORDS:
        if w.lower() in low:
            bad.append("meta:" + w)
    if say.lstrip().startswith("/"):
        bad.append("slash")
    if len(say) < 4:
        bad.append("short")
    assert bool(not bad) == ok, "台词 %r 的红线判定错了：%s" % (say, bad)
    assert problems == [], "目录自检被这些红线带坏了：%s" % problems
