"""UIContactAi.prefab 的「群聊页」节点契约（P3 第 2 步）。

通讯录在 AB_UI 构建下由**预制件**装配（`UiComposer` / `ContactUiBuilder` 那条代码链
全工程零调用方），所以「群聊 Tab / 行内复选框 / 底部操作条」这些新节点必须真的长在
`UIContactAi.prefab` 里 —— 落在 `ContactUiBuilder.cs` 里不会生效。

这里钉三件事：

  ① **节点路径**：C# 的 `AbContactPanel.CollectRefs` 是按路径 `Find` 的，
     路径写错不会报错、只会静默拿到 null ⇒ 功能缺失。所以路径是**跨语言契约**，
     第 3 步会在本文件里加"从 C# 源码现读路径字符串"的一半（照 `test_r9b` /
     `test_prompt_reset_ui_contract.py` 的范式）。
  ② **版式数值**：锚点错一位在实机上只是"按钮挤在一起 / 复选框压到名字上"，
     没有异常日志可查，所以在这里把"首尾相接、不重叠、等宽"算一遍。
  ③ **补丁幂等**：这类文本手术脚本最容易悄悄坏掉的地方 —— 第二次跑必须逐字节不变。

`verify()` 由 `scripts/dev/prefab_patch_contact_groups.py` 提供（补丁自己带的独立复核），
本文件再叠一层"这些节点**叫什么、在哪**"的断言。

预制件真源不在本仓库（在 AB 打包工程里），所以依赖它的用例整文件 skipif。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest
import _cs_tools  # 工具层多文件聚合（见 tests/_cs_tools.py）

ROOT = Path(__file__).resolve().parent.parent
PATCH_SCRIPT = ROOT / "scripts" / "dev" / "prefab_patch_contact_groups.py"
PREFAB = Path("/mnt/e/SteamLibrary/steamapps/common/鬼谷八荒/Mod/modFQA/"
              "资源修改教程/ResBuildABProject/Assets/Resources/UI/UIContactAi.prefab")

# 第 3 步 `AbContactPanel.CollectRefs` 必须逐条 Find 这些路径（顺序即父子序）。
# 这是**跨语言契约**：预制件里改了名，这里和 C# 会一起红。
EXPECTED_PATHS = [
    "/UIContactAi/BG/Tabs/TabGroupBtn",
    "/UIContactAi/BG/Tabs/TabGroupBtn/Label",
    "/UIContactAi/BG/ContactItem/CheckBox",
    "/UIContactAi/BG/ContactItem/CheckBox/Check",
    "/UIContactAi/BG/BottomBar",
    "/UIContactAi/BG/BottomBar/NewGroupBtn",
    "/UIContactAi/BG/BottomBar/NewGroupBtn/Label",
    "/UIContactAi/BG/BottomBar/DelGroupBtn",
    "/UIContactAi/BG/BottomBar/DelGroupBtn/Label",
    "/UIContactAi/BG/BottomBar/SelectBar",
    "/UIContactAi/BG/BottomBar/SelectBar/SelectAllBtn",
    "/UIContactAi/BG/BottomBar/SelectBar/CountLabel",
    "/UIContactAi/BG/BottomBar/SelectBar/CancelBtn",
    "/UIContactAi/BG/BottomBar/SelectBar/ConfirmBtn",
]


def _mod():
    if str(ROOT / "scripts" / "dev") not in sys.path:
        sys.path.insert(0, str(ROOT / "scripts" / "dev"))
    import prefab_patch_contact_groups as M
    return M


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _go_at(pf, path: str):
    """按**绝对路径**取节点。

    不能用 `pf.find_go(leaf)`：预制件里有 6 个叫 `Label` 的节点，find_go 返回的是
    文档顺序里第一个（`SearchIconBtn/Label`），不是你要的那个 —— 这个坑本文件的
    第一版就踩了，被 `test_patched_prefab_has_every_expected_path` 当场抓出来。
    """
    parts = [s for s in path.strip("/").split("/") if s]
    go = pf.find_go(parts[0])
    assert go, "找不到根节点 %s" % parts[0]
    for seg in parts[1:]:
        go = pf.child_go_named(go, seg)
        assert go, "路径 %s 在 %s 处断了" % (path, seg)
    return go


# ----------------------------------------------------------------------
# ① 不依赖资产的静态检查（任何机器都能跑）
# ----------------------------------------------------------------------

def test_patch_script_exists_and_is_importable():
    assert PATCH_SCRIPT.is_file(), "补丁脚本不见了"
    M = _mod()
    assert M.TAB_NEW == "TabGroupBtn"
    assert M.BOTTOM_BAR == "BottomBar"
    assert M.DEL_GROUP_BTN == "DelGroupBtn"


def test_select_cells_are_contiguous_and_cover_the_whole_bar():
    """选人模式四格必须**首尾相接、不重叠、不留缝、铺满整条**。

    这是纯常量运算，不需要预制件。锚点写错（0.56 写成 0.65）在实机上只是
    "确认键和取消键中间空一块"，肉眼未必立刻发现。
    """
    M = _mod()
    cells = [M.SEL_ALL_A, M.SEL_COUNT_A, M.SEL_CANCEL_A, M.SEL_CONFIRM_A]
    assert float(cells[0][0]) == 0.0, "第一格应从最左开始"
    assert float(cells[-1][1]) == 1.0, "最后一格应顶到最右"
    for i in range(1, len(cells)):
        assert float(cells[i - 1][1]) == float(cells[i][0]), \
            "第 %d 格与第 %d 格之间有缝/重叠" % (i, i + 1)
    for lo, hi in cells:
        assert float(hi) > float(lo), "格宽必须为正：%s..%s" % (lo, hi)


def test_bottom_bar_halves_are_contiguous():
    """底栏两个按钮必须**恰好各占一半、首尾相接、不留缝不重叠**（纯常量，不需要资产）。

    ★ 不切开的后果是**两个按钮完全重叠** ⇒ 后加的那个永远在上面 ⇒「新建群聊」
    再也点不到，而且零报错（uGUI 拾取只给最上层 raycast target）。
    所以这条不是"版式美观"，是"按钮还能不能用"。
    """
    M = _mod()
    l_lo, l_hi = (float(x) for x in M.BAR_HALF_L)
    r_lo, r_hi = (float(x) for x in M.BAR_HALF_R)
    assert l_lo == 0.0, "左半应从最左开始"
    assert r_hi == 1.0, "右半应顶到最右"
    assert abs(l_hi - r_lo) < 1e-9, "两半之间必须首尾相接（有缝=中间点不到，重叠=下面那个点不到）"
    assert l_hi > l_lo and r_hi > r_lo, "两个半格的宽度都必须为正"


def test_tab_anchors_are_exact_thirds():
    """三个 Tab 的锚点常量必须**恰好**是 0 / 1/3 / 2/3 / 1（不能有漂移）。"""
    M = _mod()
    vals = [M.A_0, M.A_1_3, M.A_2_3, M.A_1]
    nums = [float(v) for v in vals]
    assert nums[0] == 0.0 and nums[-1] == 1.0
    assert abs(nums[1] - 1.0 / 3.0) < 1e-6, "1/3 写成了 %s" % vals[1]
    assert abs(nums[2] - 2.0 / 3.0) < 1e-6, "2/3 写成了 %s" % vals[2]


# ----------------------------------------------------------------------
# ② 真预制件（AB 打包工程不在本机时整段跳过）
# ----------------------------------------------------------------------

@pytest.mark.skipif(not PREFAB.is_file(), reason="AB 打包工程不在这台机器上")
def test_patched_prefab_passes_its_own_verify():
    """补丁自带的独立复核必须过 —— 这是"重打 AB 之后节点会出现"的第一道凭据。"""
    M = _mod()
    assert M.verify(_read(PREFAB)) is True


@pytest.mark.skipif(not PREFAB.is_file(), reason="AB 打包工程不在这台机器上")
def test_patched_prefab_has_every_expected_path():
    """★ 跨语言路径契约：每个新节点的**绝对路径**必须与 EXPECTED_PATHS 逐条一致。"""
    M = _mod()
    pf = M.Prefab(_read(PREFAB))
    for p in EXPECTED_PATHS:
        go = _go_at(pf, p)          # 路径断了会在这里抛，带出是哪一段
        assert pf.path_of(go) == p, "节点路径不符：%s != %s" % (pf.path_of(go), p)


@pytest.mark.skipif(not PREFAB.is_file(), reason="AB 打包工程不在这台机器上")
def test_patched_prefab_is_idempotent():
    """再补一次必须一个字节都不变。

    （补丁以「Tabs 下有没有 TabGroupBtn」作分支开关，命中就走校验分支。）
    """
    M = _mod()
    text = _read(PREFAB)
    assert M.patch(text, do_log=False) == text, "补丁不幂等 —— 重跑会改坏预制件"


@pytest.mark.skipif(not PREFAB.is_file(), reason="AB 打包工程不在这台机器上")
def test_default_state_is_the_todays_look():
    """★ 默认状态必须 = **改造前的观感**（只多一条空底栏）。

    `NewGroupBtn` 与 `SelectBar` 默认都隐藏 ⇒ 即使 C# 的渲染一次都没跑，
    通讯录看起来也和今天一样，不会冒出"选人确认"这种突兀的按钮。
    这是 fail-safe：宁可少一个按钮，也不要多一个。

    ⚠ `CheckBox` **本身是显示的**（只有里面的 `Check` 隐藏）—— 上一轮这里的注释
    写成"全都隐藏"，是过头话，现按实况改正。为什么显示也无害：它的父节点 `BG`
    在预制件里就是 `m_IsActive=0`，而列表行全部由 C# 在 `RebuildRows` 里
    `Instantiate` 后显式 `SetActive` —— 预制件里那份模板**永远不上屏**。
    代价是 C# 必须每行都显式设置它的显隐（不能指望出厂默认），
    见 `test_row_checkbox_sits_above_chatbtn`。
    """
    M = _mod()
    pf = M.Prefab(_read(PREFAB))
    assert pf.field(_go_at(pf, "/UIContactAi/BG/BottomBar/NewGroupBtn"), "m_IsActive") == "0", \
        "NewGroupBtn 默认应隐藏"
    assert pf.field(_go_at(pf, "/UIContactAi/BG/BottomBar/SelectBar"), "m_IsActive") == "0", \
        "SelectBar 默认应隐藏"
    assert pf.field(_go_at(pf, "/UIContactAi/BG/BottomBar/DelGroupBtn"), "m_IsActive") == "0", \
        "DelGroupBtn 默认应隐藏（它与 NewGroupBtn 同显隐，由 C# 在群聊页点亮）"
    assert pf.field(_go_at(pf, "/UIContactAi/BG/ContactItem/CheckBox/Check"), "m_IsActive") == "0", \
        "行内 ✓ 默认应隐藏（未选中）"
    # 前提：模板整体不上屏（父节点隐藏）—— 这条一旦被改动，上面的"无害"结论就不成立了
    assert pf.field(_go_at(pf, "/UIContactAi/BG"), "m_IsActive") == "0", \
        "BG 出厂必须是隐藏的（模板 CheckBox 可见这件事靠它兜底）"
    assert pf.field(_go_at(pf, "/UIContactAi/BG/ContactItem"), "m_IsActive") == "0", \
        "行模板自身也应是隐藏的"


@pytest.mark.skipif(not PREFAB.is_file(), reason="AB 打包工程不在这台机器上")
def test_row_checkbox_sits_above_chatbtn():
    """★ `CheckBox` 是行内**最后一个**子节点 ⇒ 它在 `ChatBtn`（✓）上层，且两者同格。

    uGUI 的拾取取**最上层**的 raycast target。所以只要 CheckBox 处于显示态，
    它就会**吃掉** ✓ 按钮的点击 —— 表现是"复选框能勾，但点不到传音键"，
    而且不报任何错。这条约束只能靠 C# 的互斥显隐来满足（见
    `test_csharp_toggles_checkbox_and_chatbtn_exclusively`），这里先把
    "为什么必须互斥"的事实钉住：**顺序一旦被后人调换，那条互斥就成了可选项**。
    """
    M = _mod()
    pf = M.Prefab(_read(PREFAB))
    item = _go_at(pf, "/UIContactAi/BG/ContactItem")
    order = []
    for c in pf.children(pf.rt_of_go(item)):
        m = re.search(r"fileID: (\d+)", pf.field(c, "m_GameObject") or "")
        order.append(pf.go_name(m.group(1)) if m else "?")
    assert "ChatBtn" in order and "CheckBox" in order, "行模板结构变了：%s" % order
    assert order.index("CheckBox") > order.index("ChatBtn"), \
        "CheckBox 不再位于 ChatBtn 之上 —— 若确实调到了下层，可放宽 C# 的互斥要求：%s" % order


@pytest.mark.skipif(not PREFAB.is_file(), reason="AB 打包工程不在这台机器上")
def test_backup_is_written_once_and_never_clobbered(tmp_path):
    """★ 备份必须留住的**是原始件**，而"幂等可重跑"与"每次 copy2"是冲突的。

    这个脚本第二次跑会走校验分支、一个字节都不改；但若每次运行都无条件 `copy2`，
    第二次就会把**已打补丁**的版本盖到备份上 —— 原始件当场丢失，
    而备份的全部意义就是留住它。

    （这不是假想：本脚本第一版就是无条件 copy2，一次幂等复跑真把
      `UIContactAi.prefab` 的原始件丢了。同族的 `prefab_patch_prompt_reset.py`
      等脚本目前仍是那个写法。）
    """
    M = _mod()
    src = tmp_path / "X.prefab"
    src.write_text("原始件", encoding="utf-8")
    bak = str(src) + ".bak_contactgroups"

    assert M.ensure_backup(str(src), bak) is True, "第一次应写备份"
    assert Path(bak).read_text(encoding="utf-8") == "原始件"

    src.write_text("已打补丁件", encoding="utf-8")           # 模拟打补丁
    assert M.ensure_backup(str(src), bak) is False, "第二次不该再写"
    assert Path(bak).read_text(encoding="utf-8") == "原始件", \
        "★ 备份被覆盖了 —— 它应该永远是原始件"


@pytest.mark.skipif(not PREFAB.is_file(), reason="AB 打包工程不在这台机器上")
def test_labels_and_geometry_match_patch_constants():
    """文案与几何必须与脚本常量一致（防止"改了常量忘了重跑补丁"）。

    ★ 本函数曾经**没有 `def`**：上一轮编辑时它的 docstring 与断言体被并进了
    `test_backup_is_written_once_and_never_clobbered`。断言仍在跑（所以没报错），
    但那是"搭车"——备份用例一旦在前面 assert 早退，这里的校验就整段不跑，
    而 `--collect-only` 会显示 8 个用例而不是 9 个。补上定义。
    """
    M = _mod()
    pf = M.Prefab(_read(PREFAB))

    def label_text(path):
        cg = _go_at(pf, path)
        assert cg, "%s 不存在" % path
        return pf.field(M.find_text_comp(pf, cg), "m_Text")

    assert label_text("/UIContactAi/BG/Tabs/TabGroupBtn/Label") == M.yaml_str(M.TAB_NEW_TEXT)
    assert label_text("/UIContactAi/BG/BottomBar/NewGroupBtn/Label") == M.yaml_str(M.NEW_GROUP_TEXT)
    assert label_text("/UIContactAi/BG/BottomBar/DelGroupBtn/Label") == M.yaml_str(M.DEL_GROUP_TEXT)
    assert label_text("/UIContactAi/BG/BottomBar/SelectBar/SelectAllBtn/Label") == M.yaml_str(M.SEL_ALL_TEXT)
    assert label_text("/UIContactAi/BG/BottomBar/SelectBar/CancelBtn/Label") == M.yaml_str(M.SEL_CANCEL_TEXT)
    assert label_text("/UIContactAi/BG/BottomBar/SelectBar/ConfirmBtn/Label") == M.yaml_str(M.SEL_CONFIRM_TEXT)
    cnt = M.find_text_comp(pf, _go_at(pf, "/UIContactAi/BG/BottomBar/SelectBar/CountLabel"))
    assert pf.field(cnt, "m_Text") == M.yaml_str(M.SEL_COUNT_TEXT)

    # 列表区让位：底栏那条 48px 不能白留
    scroll_rt = pf.rt_of_go(pf.find_go("ContactScroll"))
    assert pf.field(scroll_rt, "m_AnchoredPosition") == M.SCROLL_POS
    assert pf.field(scroll_rt, "m_SizeDelta") == M.SCROLL_SIZE
    tabs_rt = pf.rt_of_go(pf.find_go("Tabs"))
    assert pf.field(tabs_rt, "m_SizeDelta") == M.TABS_SIZE, "Tabs 没加宽到 300"


# ----------------------------------------------------------------------
# ③ C# 源码半段（不需要预制件，任何机器都能跑）
#
# 上面那批只证明"预制件里有这些节点"。C# 是按**路径字符串** Find 的，
# 路径写错不报错、只静默拿到 null ⇒ 功能缺失。所以契约必须两边同时钉。
# 范式与 test_prompt_reset_ui_contract.py 一致（从源码现读，不复制粘贴常量）。
# ----------------------------------------------------------------------

AB_PANEL = ROOT / "csharp" / "UI" / "AbContactPanel.cs"
AB_CHAT_PANEL = ROOT / "csharp" / "UI" / "AbChatPanel.cs"
PRESENTER = ROOT / "csharp" / "UI" / "ContactPresenter.cs"
CHAT_PRESENTER = ROOT / "csharp" / "UI" / "ChatPresenter.cs"
REFS = ROOT / "csharp" / "UI" / "ContactPanelRefs.cs"

#: 第 3 步新接的路径 —— 只校验这批（老节点的路径另行治理，不在此文件的职责内）。
NEW_PATHS = [p for p in EXPECTED_PATHS if ("BottomBar" in p or "TabGroupBtn" in p or "CheckBox" in p)]

#: 契约里有、但 C# **刻意不 Find** 的节点。两类，各自有理由：
#:   · `/Label` 叶子：文案是预制件自己的事，C# 不碰（Tab 文案走
#:     `SetTabSelected` 的 `GetComponentInChildren<Text>(true)`，底栏按钮文案 C# 完全不读）。
#:     文案正确性由 `test_labels_and_geometry_match_patch_constants` 负责。
#:   · `BG/BottomBar`：纯容器（无 Image），C# 操作的是它的两个子节点，容器本身不需要引用。
#: 契约要查的是"**C# 要用的节点**在不在"，不是"预制件里每个节点都得被 Find"。
_NOT_FOUND_BY_CSHARP = {
    "/UIContactAi/BG/BottomBar",
}
#: C# 必须逐条 Find 的实际集合
MUST_FIND = [p for p in NEW_PATHS
             if not p.endswith("/Label") and p not in _NOT_FOUND_BY_CSHARP]
assert set(_NOT_FOUND_BY_CSHARP) <= set(NEW_PATHS), \
    "豁免名单里出现了契约中不存在的路径 —— 名单本身已经烂了：%s" % (_NOT_FOUND_BY_CSHARP - set(NEW_PATHS))


def _strip_comments(src: str) -> str:
    """剥 C# 注释：注释里会**引用**被禁止/被要求的写法，不能当成代码。"""
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"//[^\n]*", "", src)


def _method(src: str, signature: str) -> str:
    """按大括号配对取一个 C# 成员正文（含花括号）。签名必须唯一。"""
    src = _strip_comments(src)
    assert src.count(signature) == 1, "签名不唯一（%d 次）：%s" % (src.count(signature), signature)
    i = src.index(signature)
    j = src.index("{", i)
    depth = 0
    for k in range(j, len(src)):
        if src[k] == "{":
            depth += 1
        elif src[k] == "}":
            depth -= 1
            if depth == 0:
                return src[j:k + 1]
    raise AssertionError("大括号不配对：" + signature)


def test_csharp_collectrefs_paths_match_the_prefab_contract():
    """★ 跨语言路径契约的**另一边**：C# CollectRefs 里 Find 的路径必须与 EXPECTED_PATHS 一致。

    两个方向都查：
      ① 契约里的每个新路径，C# 必须真的 Find 它（漏了 ⇒ 节点白加，功能静默缺失）；
      ② C# 里每个"新节点族"的路径，必须在契约里（多出来的路径说明有人改了名而没更新契约，
         那种情况下预制件与 C# 已经对不上了，而**运行时只会静默 null**）。
    """
    src = _read(AB_PANEL)
    body = _method(src, "private static ContactPanelRefs CollectRefs(Transform root)")
    found = set(re.findall(r'Find(?:Go|Rt|Comp<[^>]+>)\(\s*root\s*,\s*"([^"]+)"\s*\)', body))
    assert found, "CollectRefs 里一个路径都没抽到 —— 解析正则该更新了"

    for p in MUST_FIND:
        # 预制件里是全路径（/UIContactAi/…），C# 里是相对根节点的路径（BG/…）
        rel = p.split("/UIContactAi/", 1)[1]
        assert rel in found, "CollectRefs 没有 Find(%r) —— 预制件里有这个节点也白搭" % rel

    extra = {p for p in found if ("BottomBar" in p or "TabGroupBtn" in p or "CheckBox" in p)} \
        - {p.split("/UIContactAi/", 1)[1] for p in MUST_FIND}
    assert not extra, "CollectRefs 里有多出来的群聊节点路径（契约没跟上）：%s" % sorted(extra)


def _are_mutually_exclusive(a: str, b: str) -> bool:
    """两个 `SetActive` 实参是否**保证不会同时为真**。

    ★ 只查"两个表达式字符串不同"是**不够的** —— 这是本用例第一版的漏洞，
    变异检查当场抓出来：把 `checkBox.SetActive(sel)` 改成 `SetActive(true)`
    之后，`"true" != "!sel"` 成立，用例照样绿，而复选框从此永远盖住 ✓。
    所以判据收紧成"必须互补"：
      · 同一变量的正反（`sel` / `!sel`）——两条重建路径都用这个形态；
      · 常量 `true` / `false` —— 群列表那条路径用这个形态。
    更复杂的条件（如 `sel && !x` 对 `!sel || x`）会被判为不合规：**这是故意的**，
    它逼人回来把"为什么这两者互斥"讲清楚，而不是让它悄悄滑过去。
    """
    a, b = a.strip(), b.strip()
    if a == "!" + b or b == "!" + a:
        return True
    return {a, b} == {"true", "false"}


def test_csharp_never_lets_checkbox_and_chatbtn_show_together():
    """★ 复选框与 ✓ 同格、复选框在上层 ⇒ **任何一条分支都不得让两者同时可见**。

    否则复选框会吃掉 ✓ 的点击（uGUI 只给最上层的 raycast target），
    表现是"看得见却点不动"，零报错。预制件侧的 z-order 由
    `test_row_checkbox_sits_above_chatbtn` 钉住。
    """
    src = _read(PRESENTER)
    for sig in ("private void RebuildRows(List<ContactEntry> rows)",
                "private void RebuildRoomRows(List<RoomEntry> rows)"):
        body = _method(src, sig)
        mc = re.search(r"checkBox\.SetActive\(([^)]*)\)", body)
        mchat = re.search(r"chatBtnGo\.SetActive\(([^)]*)\)", body)
        assert mc, "%s 没有显式设置 CheckBox 显隐 —— 它出厂是可见的，会吃掉 ✓ 的点击" % sig
        assert mchat, "%s 没有显式设置 ChatBtn 显隐" % sig
        assert _are_mutually_exclusive(mc.group(1), mchat.group(1)), (
            "%s 里 CheckBox=%r 与 ChatBtn=%r **可能同时为真** —— 两者必须互斥"
            % (sig, mc.group(1).strip(), mchat.group(1).strip()))


# ----------------------------------------------------------------------
# ③b 克隆行的"第二套路径"
#
# 行不是 `CollectRefs` 手里的那个模板，而是 `Instantiate` 出来的克隆 ——
# 所以两个 `Rebuild*Rows` 只能拿着 clone 再按名/按路径找一遍。于是**同一个节点
# 有了第二套路径**，而上面那批契约只钉了 `CollectRefs` 那一套。
# ----------------------------------------------------------------------

#: 对**克隆行**的每次查找：`FindChild(clone.transform, "X")` 与 `clone.transform.Find("X")`。
#: 两种写法语义完全相同（都是 `Transform.Find`，**只认直接子节点**），所以一起抽。
_CLONE_FIND_RE = re.compile(
    r'(?:FindChild\(\s*clone\.transform\s*,|clone\.transform\.Find\()\s*"([^"]+)"\s*\)')

#: 两个"从同一份行模板造行"的方法。两处必须同时保有正确的相对路径 ——
#: 只修一处就是"建群能勾、解散群不能勾"这种只在一半路径上发作的缺陷。
_ROW_BUILDERS = ("private void RebuildRows(List<ContactEntry> rows)",
                 "private void RebuildRoomRows(List<RoomEntry> rows)")


def _clone_find_paths(src: str, signature: str):
    """抽一个行构建方法里对克隆行的所有查找路径（去重、有序）。"""
    return sorted(set(_CLONE_FIND_RE.findall(_method(src, signature))))


def test_clone_finds_agree_with_collectrefs_paths():
    """★ `CollectRefs` 与两个 `Rebuild*Rows` 对**同一个节点**必须用同一条相对路径。

    真实事故（09-17 实机，用户截图）：建群选人点了几个人，只有底部"已选 2 人"在动，
    行内没有任何勾选反馈。根因是 `FindChild(clone.transform, "Check")` ——
    `Check` 是 `CheckBox` 的**子节点**（`ContactItem/CheckBox/Check`），
    而 `Transform.Find(name)` **只认直接子节点** ⇒ 静默 null ⇒
    `check.SetActive(isChecked)` 永不执行 ⇒ 绿勾永远不亮。零报错、零日志，
    四个选择模式（建群 / 拉人 / 踢人 / 解散群）全中招。

    为什么上面那批契约没拦住：`CollectRefs` 用的是**全路径**（`BG/ContactItem/CheckBox/Check`），
    是对的；真正上屏的克隆行走的却是另一套裸名写法，没人钉。

    本条不需要预制件（纯源码互校）：`CollectRefs` 里 `ContactItem` 下的每个节点，
    两个行构建方法都必须用**同一条后缀路径**去找。两边从此锁死 ——
    改层级、改写法、只改一处，都会在这里红。
    """
    panel = _method(_read(AB_PANEL), "private static ContactPanelRefs CollectRefs(Transform root)")
    ref_suffixes = {p.split("BG/ContactItem/", 1)[1]
                    for p in re.findall(r'FindGo\(\s*root\s*,\s*"([^"]+)"\s*\)', panel)
                    if p.startswith("BG/ContactItem/")}
    assert ref_suffixes, "CollectRefs 里抽不到 ContactItem 下的路径 —— 解析正则该更新了"

    src = _read(PRESENTER)
    for sig in _ROW_BUILDERS:
        used = set(_clone_find_paths(src, sig))
        assert used, "%s 里一条 clone 查找都没抽到 —— 解析正则该更新了" % sig
        missing = ref_suffixes - used
        assert not missing, (
            "%s 里找不到这些路径：%s —— CollectRefs 用的是相对路径（%s），"
            "克隆行上却写成别的（很可能是裸名，`Transform.Find` 只认直接子节点 ⇒ 静默 null）"
            % (sig, sorted(missing), sorted(ref_suffixes)))


@pytest.mark.skipif(not PREFAB.is_file(), reason="AB 打包工程不在这台机器上")
def test_every_clone_find_path_resolves_in_the_real_prefab():
    """★★ 上面那条只保证"C# 两边写法一致"，这条保证"在那个写法真的能在预制件里解析出来"。

    两条合起来才是闭环：光互校的话，两边**一起写错**（比如都写成裸名 `Check`）仍然绿。
    真实事故里 C# 两边恰好不一致才侥幸被上一条抓到；这条直接把"路径必须落在真节点上"
    钉死 —— 裸名写错、层级改了没跟上、相对路径漏写，三种都会红。
    """
    M = _mod()
    pf = M.Prefab(_read(PREFAB))
    src = _read(PRESENTER)
    checked = 0
    for sig in _ROW_BUILDERS:
        for rel in _clone_find_paths(src, sig):
            full = "/UIContactAi/BG/ContactItem/" + rel
            try:
                _go_at(pf, full)
            except AssertionError as e:
                raise AssertionError(
                    "%s 里 Find(%r) 在预制件里解析不到：%s\n"
                    "（`Transform.Find` 只认直接子节点；孙节点要写相对路径，"
                    "如 \"CheckBox/Check\"）" % (sig, rel, e))
            checked += 1
    assert checked >= 8, "只抽到 %d 条 clone 查找 —— 解析正则该更新了" % checked


def test_there_is_exactly_one_open_room_path():
    """★ 群窗只能有**一条**开启路径。

    两个入口（群列表点一行 / 选人确认建群）都汇到 `ChatPresenter.OpenRoomById`。
    复制一份回执处理就是第二个真相源 —— 本工程已经因为跨入口重复实现栽过（§12.3）。
    """
    src = _read(CHAT_PRESENTER)
    code = _strip_comments(src)
    assert code.count('SendRequest("open_room"') == 1, \
        "open_room 请求有 %d 处发送点，应当只有 1 处" % code.count('SendRequest("open_room"')
    assert code.count('_window.ReplaceHistory(data["items"]') == 1, \
        "群窗历史填充点不止一处（应只在 ApplyOpenRoom 里）"


def test_confirm_create_never_opens_the_chat_window():
    """★ **建群 ≠ 开窗**：`ConfirmCreate` 必须发 `create_room`，且不得开窗、不得关面板。

    09-17 实机（用户截图）：建群走的是 `open_room` + `ChatLauncher.OpenForRoom`，
    后果是通讯录被 `Hide()`、群窗**先占位打开**（标题为空 ⇒ InputField 显示占位符
    「npc 名称」），回执一失败就留下「空群窗 + 一行开群失败」——
    而**群其实已经建成了**（Python 日志里 `建群：g1` 就打在报错前一行）。
    "以为失败、实际成功"比彻底失败更糟：玩家会重复建，或者以为这个群不存在。

    正确流程与「点好友开单聊」对称：**建完留在通讯录**、退回群聊页，新群出现在列表里，
    玩家点那一行才开窗（`OpenRoomFor` → `open_room`，那条唯一入口不动）。
    """
    src = _read(PRESENTER)
    body = _method(src, "private void ConfirmCreate()")
    assert 'SendRequest("create_room"' in body, \
        "ConfirmCreate 必须发 create_room（建群是改数据，开窗是开窗）"
    assert 'SendRequest("open_room"' not in body, \
        "ConfirmCreate 不得发 open_room —— 那是群窗的唯一入口，通讯录再发一个就是第二个真相源"
    assert "ChatLauncher.OpenForRoom" not in body, \
        "ConfirmCreate 不得开群窗 —— 建完要**留在通讯录**（点那一行才开）"
    assert not re.search(r"(?<![\w.])Hide\(\)", body), \
        "ConfirmCreate 不得关掉通讯录 —— 那正是「点了新建群聊却被踢出面板」的来源"


def test_slash_group_commands_are_gone():
    """★ P3：`/group` 与 `/ungroup` 已删除，不许回来。

    它们是 P1b 的临时入口（那时还没有通讯录群聊页）。通讯录做出来之后它们就是
    **重复入口**，而且斜杠命令会**吃掉玩家正常的正文**（`/group` 本身也是合法输入）。
    离群由群窗右上角 ✕ 承担（`OnCloseClicked` 在群模式下走 `CloseRoomCommand`）。
    """
    code = _strip_comments(_read(CHAT_PRESENTER))
    assert "OpenRoomCommand" not in code, "旧的 /group 命令入口又回来了"
    assert "IsCommand" not in code, "斜杠命令的词边界助手还在 —— 说明命令入口没删干净"
    assert '"/group"' not in code, "还在比较 /group"
    assert '"/ungroup"' not in code, "还在比较 /ungroup"
    # 离群本身必须留着（✕ 要它）。★ 判据不能用 `"CloseRoomCommand" in code`：
    # 把方法改名成 `CloseRoomCommandRenamed` 也**包含**这个子串，用例照样绿
    # （变异检查当场抓出来的）—— 所以这里钉"声明"与"调用点"两处精确形态。
    assert "private void CloseRoomCommand()" in code, "离群路径被误删了（✕ 在群模式下调它）"
    assert "CloseRoomCommand()" in _method(_read(CHAT_PRESENTER), "private void OnCloseClicked()"), \
        "✕ 在群模式下没有走 CloseRoomCommand —— 离群的唯一入口断了"


def test_dissolve_has_exactly_one_send_site_and_is_two_step():
    """★ 解散群聊：单一发送点 + **两段确认** + 通知对话窗。

    三条都是"静默出事"的守卫：
      · 多个发送点 ⇒ 两份处理迟早走偏（同 open_room 那条）；
      · 没有两段确认 ⇒ 手滑一下就把群流水 unlink 掉，**不可恢复**；
      · 不通知对话窗 ⇒ 窗口继续显示一个已不存在的群，之后发言永远没有回复。
    """
    cp = _read(PRESENTER)
    code = _strip_comments(cp)
    assert code.count('SendRequest("dissolve_room"') == 1, \
        "dissolve_room 有 %d 处发送点，应当只有 1 处" % code.count('SendRequest("dissolve_room"')

    body = _method(cp, "private void ConfirmDelete()")
    assert "TwoStepConfirm" in cp, "整个文件都没引用 TwoStepConfirm —— 两段确认没接上"
    assert "_delConfirm.Click(" in body, "确认删除没有走两段确认"
    assert "Step.First" in body and "Step.Second" not in body, \
        "确认删除应当只显式处理 First/Expired，Second 落到 ExecuteDissolve（见下）"
    assert "ExecuteDissolve()" in body, "Second 那一步没有落到真正的执行"

    # 真正执行的那一段：必须通知对话窗
    ex = _method(cp, "private void ExecuteDissolve()")
    assert "NotifyRoomDissolvedStatic" in ex, \
        "解散后没有通知对话窗 —— 正开着这个群的窗口会继续显示它，之后发言永远没回复"
    assert "RequestRooms()" in ex, "解散后没有重拉名录校准"
    # ★ 失败必须让玩家看见（09-17 复核修）：列表是**乐观删除**的（点击立刻有反馈），
    #   只在日志里写一行的话，表现就是"群先是没了、几秒后又自己回来"且**零解释**。
    assert "if (!ok)" in ex and "SetStatus(" in ex.split("if (!ok)", 1)[1][:400], \
        "解散失败只写日志、不告诉玩家（群会'自己回来'且没有原因）"


def test_room_dissolve_notification_exists_on_both_sides():
    """`AbChatPanel.NotifyRoomDissolvedStatic` ↔ `ChatPresenter.OnRoomDissolved` 必须**真的接上**。

    ★ 只断言"符号存在"是不够的（变异检查抓出来的）：把 `p.OnRoomDissolved(...)` 换成空语句，
    符号照样在、用例照样绿，而解散后对话窗会继续显示一个已不存在的群。
    所以这里钉的是**调用**，不是**名字**。
    """
    abchat = _read(AB_CHAT_PANEL)
    assert "NotifyRoomDissolvedStatic" in _strip_comments(abchat)
    fwd = _method(abchat, "public static void NotifyRoomDissolvedStatic(string roomId)")
    assert "OnRoomDissolved(" in fwd, "静态入口没有真的转调 OnRoomDissolved（空实现？）"

    cp = _read(CHAT_PRESENTER)
    assert "public void OnRoomDissolved(string roomId)" in _strip_comments(cp)
    body = _method(cp, "public void OnRoomDissolved(string roomId)")
    assert "AcceptsRoomFrame" in body, \
        "OnRoomDissolved 必须只对**当前这个群**生效（否则删别的群会把当前群窗也关掉）"
    assert 'OpenFor("")' in body, "OnRoomDissolved 没把窗口切回单聊外观"


def test_open_room_request_asks_for_full_history():
    """`open_room` 必须显式带 `limit = 0`。

    Python 的 `_parse_limit` 缺省是 **10**（为不传参的老调用方保留）⇒ 不传的话群窗
    只回放最后 10 条，而且是**静默**的：界面看不出截断，玩家只会觉得"这个群少了一段"。
    """
    src = _read(CHAT_PRESENTER)
    # 签名只钉**参数前缀**：后面还挂着可选的 `JObject memberUnitIds = null`
    # （群成员的精确身份，见 test_manual_identity_contract.py）。钉死整串会在
    # 加一个可选参数时误红 —— 而这条测试要守的是 limit，不是签名宽度。
    body = _method(src, "public void OpenRoomById(string roomId, List<string> members, string titleHint")
    # ★ 11-21：值从 `0`（全量）改成具名常量 `RoomHistoryPageRows`（分页首屏）★
    #   这条测试**原本的意图仍成立且更重要**：必须**显式传** limit ——
    #   不传的话 Python `_parse_limit` 缺省给 10，群窗会**静默**只剩 10 条。
    #   只是不再允许传 0（那会把全部历史一次灌给客户端，正是用户要修的问题）。
    assert re.search(r'p\["limit"\]\s*=\s*(?!0\s*;)\w+\s*;', body), \
        "OpenRoomById 必须显式传 limit（且不许是 0 = 全量）"
    assert "RoomHistoryPageRows" in body, \
        "群窗首屏条数必须用具名常量 RoomHistoryPageRows（不是魔法数字）"


def test_csharp_never_mints_a_room_id():
    """★ `room_id` 的所有权在 Python。

    `new_room_id` 需要"权威的已用 id 集合"才能不撞号，C# 看不到 `index.json`；
    两个生成器必然发散。所以 C# 只能**回声**调用方给的 id，或**接收**回执里的 id。
    这里查的是最容易犯的那一种：在 C# 里拼 `"g" + n` 这类字面量。
    """
    bad = re.compile(r'"g"\s*\+|String\.Format\(\s*"g\{|room_id"?\]\s*=\s*"g')
    for f in (PRESENTER, CHAT_PRESENTER, REFS):
        src = _strip_comments(_read(f))
        m = bad.search(src)
        assert not m, "%s 里出现了自己造 room_id 的痕迹：%r" % (f.name, m.group(0))


# ----------------------------------------------------------------------
# 09-26 用户拍板三项：打开面板全清红点 / 行内 ✕ 删好友 / 境界口径收口
#
# 三条都属"静默出事"一类：没有异常、没有日志，只有玩家看到"红点点不下去"、
# "删不掉好友"、"所有人都是登仙境"。所以全部用源码断言钉死。
# ----------------------------------------------------------------------

UNREAD_STORE = ROOT / "csharp" / "contacts" / "UnreadStore.cs"
UI_BUILDER = ROOT / "csharp" / "UI" / "ContactUiBuilder.cs"


def test_opening_panel_clears_all_unread():
    """★ 打开传音簿 = **全部已读**（09-26 用户拍板）。

    现象：HUD「传」钮角标由 `UnreadStore.Any`（聚合）驱动，而旧语义要求逐个点开 NPC
    对话才清 —— 多人同时有未读时"点开 A、角标照旧不灭"，玩家读作"红点点不下去"。
    契约：`Show()` 里先 `ClearAll()` 再渲染（顺序反了，这一次打开仍会看到红点）。
    """
    store = _strip_comments(_read(UNREAD_STORE))
    assert "public static void ClearAll()" in store, "UnreadStore 没有 ClearAll 入口"
    body = _method(_read(UNREAD_STORE), "public static void ClearAll()")
    assert "_unread.Clear()" in body, "ClearAll 没有真的清登记表"

    show = _method(_read(PRESENTER), "public void Show()")
    assert "UnreadStore.ClearAll()" in show, "打开面板没有清未读 —— 红点会继续亮"
    assert show.index("UnreadStore.ClearAll()") < show.index("RenderCurrent()"), \
        "ClearAll 必须在渲染之前（否则这一次打开仍显示红点）"


def test_row_remove_button_contract():
    """★ 行内 ✕「删除好友」：只在好友行、与选择模式互斥、两段确认、走既有移除链路。

    四件事漏任何一件都是静默出错：
      · 选择模式里 ✕ 若仍亮着 —— 玩家在勾人时点到的是"删除好友"；
      · 纯关系层行（道侣/子女/仇人）点亮 ✕ = 给一个删不动的东西；
      · 没有两段确认 = 手滑一下好友就没了（不可恢复）；
      · 不走 `ContactDuty.ToggleManualContact(add:false)` 而自己发 RPC = 绕过镜像校准。
    """
    fb_sig = "public static GameObject BuildRowRemoveButton(Transform row, RectTransform chatBtn, out Text icon)"
    fb = _method(_read(UI_BUILDER), fb_sig)
    assert '"RemoveBtn"' in fb, "✕ 节点名不是 RemoveBtn（后续查找/让位都靠它）"
    assert "chatBtn.anchoredPosition.x - (w + Gap)" in fb, "✕ 没有落在 ✓ 的左侧"
    assert "SetActive(false)" in fb, "✕ 出厂必须默认隐藏（由 Presenter 按行点亮）"

    rows = _method(_read(PRESENTER), "private void RebuildRows(List<ContactEntry> rows)")
    assert "ContactUiBuilder.BuildRowRemoveButton(" in rows, "RebuildRows 没有建 ✕"
    m = re.search(r"bool canRemove = ([^;]+);", rows)
    assert m, "RebuildRows 没有 canRemove 判据"
    assert "pickMode" in m.group(1), "✕ 在选人/踢人模式里也可能亮着：%r" % m.group(1)
    assert "Manual" not in m.group(1), \
        "✕ 又只对好友行亮了 —— 09-26 用户拍板：关系层行也要有（否则那些行删不掉）"
    assert "DisarmUnfriend()" in rows, "重渲没有作废未完成的确认（会留下看不见的确认态）"
    assert "DisarmUnfriend()" in _method(_read(PRESENTER), "private void ClearPick()"), \
        "关面板/切 Tab 没有作废未完成的确认"
    # ★ 无超时（09-26 用户拍板："只要用户点了确认就行"）—— 不许再用带窗口的 TwoStepConfirm
    assert "_unfriendConfirm" not in _strip_comments(_read(PRESENTER)), \
        "行内 ✕ 用回了带 3s 窗口的 TwoStepConfirm —— 用户明确要求去掉超时"

    rm = _method(_read(PRESENTER), "private void OnRemoveRow(ContactEntry e, Text icon)")
    assert "_unfriendArmName != e.Name" in rm and "_unfriendArmName = e.Name" in rm, \
        "首击武装的分支不见了（两段确认只剩一段？）"
    assert "DisarmUnfriend()" in rm, "执行/换行时没有解除确认态"
    assert "SetRemoveIcon(" in rm, "没有确认态视觉切换（玩家看不到自己在确认什么）"
    # ★ 双语义（09-26 用户拍板）：好友行 = 删好友（+顺带隐藏仍有关系的部分），纯关系行 = 隐藏
    assert re.search(r"ContactDuty\.ToggleManualContact\([^)]*false", rm), \
        "好友行没有走 ContactDuty（add:false）—— 镜像会与 contacts.json 发散"
    assert "ContactDuty.SetHidden(" in rm, "没有隐藏分支（纯关系行点 ✕ 会什么都不发生）"
    assert "e.ManualOnly" in rm, "没有按 ManualOnly 判定『关系层也有他』（回执与联动会错）"


def test_hidden_list_contract():
    """★ 隐藏名单（09-26 用户拍板）：渲染层过滤 + 好友优先 + 重现出口唯一。

    三件静默事：
      · 过滤没接上 → 隐藏了还在列表里（玩家以为没生效）；
      · 好友优先丢了 → 重新加为好友却看不见（最气人的一种）；
      · 重现出口没有/不止一个 → 隐藏者永远回不来，或"关系一变就自己蹦出来"。
    """
    rc = _method(_read(PRESENTER), "private void RenderCurrent()")
    assert "ExcludeHidden(" in rc, "渲染时没有过滤隐藏者"
    body = _method(_read(PRESENTER),
                   "private static List<ContactEntry> ExcludeHidden(List<ContactEntry> rows)")
    assert "c.Manual" in body and "IsHidden" in body, "好友优先丢了（好友行被隐藏过滤挡住）"
    assert "HasHidden" in body, "没有空名单短路（常路径白遍历一遍）"
    assert "ContactDuty.RequestHidden()" in _method(_read(PRESENTER), "public void Show()"), \
        "打开面板没有拉隐藏名单（隐藏会看起来没生效）"

    duty = _read(ROOT / "csharp" / "contacts" / "ContactDuty.cs")
    ui = _method(duty, "private static void OnUiEvent(JObject payload)")
    assert "UnhideOnInitiative(" in ui, "主动传音没有取消隐藏（隐藏者永远回不来）"
    un = _method(duty, "private static void UnhideOnInitiative(string npc)")
    assert "ContactStore.SetHidden(" in un and "SetHiddenRpc(" in un, \
        "取消隐藏没有双写（本地即时 + 落盘）"
    assert '"list_hidden"' in duty and '"hide_contact"' in duty and '"unhide_contact"' in duty, \
        "三个隐藏 RPC 的名字没接上"


def test_status_bar_sits_at_panel_bottom():
    """★ 状态栏必须钉在**面板底端**（09-26 用户实机反馈：原版在中央、被列表行压住）。

    预制件里 `BG/Status` 是"全幅拉伸 + 居中对齐" ⇒ 文字落在面板中央；列表一长就与行重叠
    （用户截图："再点一次 ✕ 确认…"压在好友行上）。契约：运行时改成"底边拉伸 + 底中 pivot"，
    且群聊页让开底栏（BottomBar 占距底 4~48，状态栏抬到其上沿）。
    """
    body = _method(_read(PRESENTER), "private void LayoutStatusBar()")
    assert "anchorMin = new Vector2(0f, 0f)" in body and "anchorMax = new Vector2(1f, 0f)" in body, \
        "状态栏没有改成底边拉伸（文字还会落在面板中央）"
    assert "StatusBottomGroup" in body, "群聊页没有让开底栏（状态文字会压在底栏按钮上）"
    assert "LayoutStatusBar()" in _method(_read(PRESENTER), "private void RenderCurrent()"), \
        "渲染时没有应用状态栏版式（切 Tab/数据变更后又会回到中央）"


def test_realm_readings_all_go_through_realmtext():
    """★ 境界口径全仓唯一入口 = `UnitSnapshot.RealmText`（09-13 定案，09-26 收口通讯录）。

    `RoleGrade.GetGradeName(grade)` 的形参是**大境界号 1..10**，而 `gradeID` 是表**行号
    1..44**；直接喂行号会越界钳到最高档 ⇒ 高阶 NPC 全显示"登仙境"（低阶偶然对）。
    被这条口径波及过的四个调用点：通讯录副标题 / 重名消歧候选 / find_units 结果与过滤 /
    排行榜结果行 —— 本用例既查"没人再直连 GetGradeName"，也查四类点真的走 RealmText。
    """
    hits = []
    for f in (ROOT / "csharp").rglob("*.cs"):
        if "bin" in f.parts or "obj" in f.parts:
            continue
        if f.name == "UnitSnapshot.cs":
            continue          # 自洽校验失败后的回退分支（注释已说明），是唯一豁免
        if "roleGrade.GetGradeName(" in _strip_comments(_read(f)):
            hits.append(f.relative_to(ROOT).as_posix())
    assert not hits, "这些文件绕过了 UnitSnapshot.RealmText（境界会显示成登仙境）：%s" % hits

    sub = _method(_read(PRESENTER), "private static string SubOf(ContactEntry e)")
    assert "UnitSnapshot.RealmText(" in sub, "通讯录副标题没有走 RealmText"
    for name, cs in (("csharp/tools/*.cs（工具层）", _cs_tools.tool_src()),
                     ("game/UnitLookup.cs", _read(ROOT / "csharp" / "game" / "UnitLookup.cs"))):
        assert "UnitSnapshot.RealmText(" in cs, "%s 没有走 RealmText" % name


HUD_BTN = ROOT / "csharp" / "UI" / "MapMainContactButton.cs"


def test_hud_button_strips_game_new_tip():
    """★ HUD「传」钮不得跟随游戏信件的「新」标记（09-26 真机截图 + Player.log 实证）。

    根因 = **整树克隆**：模板 `G:btnEmail`（信件钮）根上挂着游戏原生 `UINewTip`
    （"有新内容就亮"的标记控制器），`Instantiate` 把它一起复制进克隆体 ⇒ 我们的按钮
    跟着**全局信件未读**状态显示「新」印章（用户截图）。
    修法 = 克隆后剥离该组件，**不是换模板**：同链任务/背包/功法钮同样各挂各的「新」，
    小地图钮既不同父、无 writable 文本、尺寸结构也不同（换过去治不了还会破坏圆环版式）。
    """
    src = _read(HUD_BTN)
    body = _method(src, "private static void StripNativeNewTip(GameObject clone)")
    assert '"UINewTip"' in body, "没有按运行时真名识别 UINewTip"
    assert 'Contains("NewTip")' in body, "没有做 NewTip 兜底匹配"
    assert "GetComponentsInChildren<Component>(true)" in body, "没有扫整棵子树（含 inactive）"
    assert "DestroyImmediate" in body, "没有真的销毁组件（只打日志不删 = 白做）"
    assert body.index("ModMain.P(") < body.index("DestroyImmediate"), \
        "必须先留痕再删（组件销毁后连节点名都取不到）"

    inject = _method(src, "private static bool TryInject(Transform transform, bool finalAttempt)")
    assert "StripNativeNewTip(clone)" in inject, "注入流程没有调用剥离"
    assert inject.index("StripOperationItemPublic(clone)") < inject.index("StripNativeNewTip(clone)"), \
        "剥离须排在组机制剥离之后（同一批克隆体清理）"
