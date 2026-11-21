# -*- coding: utf-8 -*-
"""气泡**文字版式**（UIChatAi.prefab 的 NpcBubble / UserBubble）契约。

这次改动天生"改一半也能跑"：两张气泡是**镜像的两套**，只改一边 = 另一个气泡照样出框；
对齐只改一处 = 一侧仍然反向对齐。所以这里钉的不是"有没有改过"，而是四条**语义**：

  ① 两个气泡的文字对齐必须是**相反**的一对（NPC 左对齐 / 玩家右对齐）——
     相同 = 又装反了（改造前就是这样，症状：`)` 单独一行却贴最右边）；
  ② **尾巴那一侧**的 padding 必须真的大于**图里 body 的内缩**：内缩是从真实 PNG 的 alpha
     量出来的（不是抄常量），量不到 Pillow 才退回常量。小于内缩 ⇒ 文字又画到白底外，
     而这件事**完全不报错**（用户 09-17 截图里长行首字压在尾巴上，就是这么来的）；
  ③ 高度保底靠 `LayoutElement` 与 VLG **同优先级取大者**（`m_LayoutPriority: 0`）；
     改成 1 会把气泡高度钉死在 76，长文本被 `vOverflow=Truncate` **静默截掉**；
  ④ 文字区宽度要落在"每行 16~24 个汉字"的合理区间（padding 调过头 = 每行只剩几个字）。

预制件真源不在本仓库（在 AB 打包工程里），依赖它的用例整文件 skipif；补丁脚本常量那一半
任何机器都能跑。

配对：
  · 补丁脚本 `scripts/dev/prefab_patch_chat_bubble_style.py`（真源 = 它改出来的 prefab）
  · 显示层的空行折叠在 `tests/test_bubble_text_contract.py`（另一半，互不依赖）
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PATCH_SCRIPT = ROOT / "scripts" / "dev" / "prefab_patch_chat_bubble_style.py"

AB = Path("/mnt/e/SteamLibrary/steamapps/common/鬼谷八荒/Mod/modFQA/资源修改教程/ResBuildABProject")
PREFAB = AB / "Assets" / "Resources" / "UI" / "UIChatAi.prefab"
#: 尾巴在图片包围盒里的那一侧（NPC 左 / 玩家右）—— 见补丁脚本文档头
NPC_PNG = AB / "Assets" / "art" / "npc气泡.png"
USER_PNG = AB / "Assets" / "art" / "玩家气泡.png"


def _mod():
    if str(ROOT / "scripts" / "dev") not in sys.path:
        sys.path.insert(0, str(ROOT / "scripts" / "dev"))
    import prefab_patch_chat_bubble_style as M
    return M


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


# ----------------------------------------------------------------------
# ① 常量自洽（不需要资产，任何机器都跑）
# ----------------------------------------------------------------------

def test_patch_script_exists_and_is_importable():
    assert PATCH_SCRIPT.is_file(), "补丁脚本不见了"
    M = _mod()
    assert M.NPC_BUBBLE == "NpcBubble" and M.USER_BUBBLE == "UserBubble"
    # ★ 两侧正文都**左对齐 + 顶部**（09-17 用户实机纠正过一版）。
    #   气泡整体靠左还是靠右由**行模板的 HorizontalLayoutGroup**决定，与文字对齐无关 ——
    #   第一版把玩家侧改成 UpperRight(2) + MiddleRight(5)，用户截图打回："应该左对齐+顶部"。
    assert M.NPC_ALIGN == "0" and M.USER_ALIGN == "0", \
        "两侧正文都必须 UpperLeft；右对齐会让短句浮在气泡中间偏右"
    assert M.NPC_CHILD_ALIGN == "0" and M.USER_CHILD_ALIGN == "0", \
        "子项对齐必须 UpperLeft（文字贴顶）；Middle* 会让单行泡文字垂直居中"


def test_tail_side_padding_clears_the_art_inset_by_constants():
    """尾巴侧 padding > 图里 body 内缩 + 余量（纯常量层的第一道闸）。"""
    M = _mod()
    assert M.NPC_PAD["m_Left"] > M.NPC_TAIL_INSET + M.TAIL_MARGIN, \
        "NPC 尾巴在左：左 padding 必须让开图里 body 的内缩"
    assert M.USER_PAD["m_Right"] > M.USER_TAIL_INSET + M.TAIL_MARGIN, \
        "玩家尾巴在右：右 padding 必须让开图里 body 的内缩"
    # 非尾巴侧不得比改造前更紧（原来是 12）
    assert M.NPC_PAD["m_Right"] >= M.MIN_FAR_PAD
    assert M.USER_PAD["m_Left"] >= M.MIN_FAR_PAD
    # 尾巴侧必须严格大于非尾巴侧（镜像两边；相等 = padding 又写回对称的了）
    assert M.NPC_PAD["m_Left"] > M.NPC_PAD["m_Right"]
    assert M.USER_PAD["m_Right"] > M.USER_PAD["m_Left"]


def test_bubble_width_is_frozen():
    """宽度由用户拍板（保持 450）—— 补丁不许顺手改它（改了每行字数全变）。"""
    M = _mod()
    assert M.BUBBLE_SIZE == "{x: 450, y: 76}"


def test_min_height_is_one_line_not_two():
    """★ 保底高度必须是**一行**，不是两行（11-20 用户："最小高度是两行文字，很别扭"）★

    算式（字号 19 × 行距 1.35，气泡上下内边距 12+12）：

        单行 = 25.65 + 24 = 49.65
        两行 = 51.30 + 24 = 75.30

    旧值 76 ≈ 两行 ⇒ 一条「嗯」「好」也硬撑两行高。现值 50 = 单行向上取整。

    为什么这条值得单独钉：它**不是宽度**，改错了不会影响任何行的字符数（所以
    `test_bubble_width_is_frozen` 抓不到），只会让每条短消息都多出半个空泡。
    而 `m_LayoutPriority` 必须保持 0，长文本才撑得高 —— 两条必须一起成立。
    """
    M = _mod()
    assert M.LAYOUT_PREF_HEIGHT == "50", (
        "保底高度不是 50（单行）—— 改回 76 等于两行，短消息会凭空多一行空白"
    )
    assert M.LAYOUT_PRIORITY == "0", (
        "LayoutElement 的优先级必须是 0（与 VLG 取大者）；改成 1 会把高度钉死、长文本被截断"
    )
    pref = float(M.LAYOUT_PREF_HEIGHT)
    assert not (70.0 <= pref <= 80.0), (
        f"保底高度 {pref} 落在「两行」区间（75.30 附近）—— 这正是 11-20 修掉的别扭观感"
    )


# ----------------------------------------------------------------------
# ② 真预制件（AB 打包工程不在本机时整段跳过）
# ----------------------------------------------------------------------

@pytest.mark.skipif(not PREFAB.is_file(), reason="AB 打包工程不在这台机器上")
def test_patched_prefab_passes_its_own_verify():
    M = _mod()
    assert M.verify(_read(PREFAB)) is True


@pytest.mark.skipif(not PREFAB.is_file(), reason="AB 打包工程不在这台机器上")
def test_patched_prefab_is_idempotent():
    """幂等 = 重跑不会把文件改坏（用户会反复跑；Unity 侧改过之后再跑也不能翻车）。"""
    M = _mod()
    text = _read(PREFAB)
    assert M.patch(text, do_log=False) == text, "补丁不幂等 —— 重跑会改坏预制件"


@pytest.mark.skipif(not PREFAB.is_file(), reason="AB 打包工程不在这台机器上")
def test_prefab_reads_back_the_expected_values():
    """从真文件里**独立读回**（不信任 verify 的实现）：字段真的落盘了。"""
    M = _mod()
    pf = M.Prefab(_read(PREFAB))
    for name, align, child_align, pad in (
            (M.NPC_BUBBLE, M.NPC_ALIGN, M.NPC_CHILD_ALIGN, M.NPC_PAD),
            (M.USER_BUBBLE, M.USER_ALIGN, M.USER_CHILD_ALIGN, M.USER_PAD)):
        go = pf.find_go(name)
        assert go, "预制件里没有 %s" % name
        txt, vlg = M.text_comp(pf, go), M.comp_with_guid(pf, go, M.VLG_GUID)
        assert M.read_align(pf, txt) == align, "%s/Text 对齐" % name
        assert pf.field(vlg, "m_ChildAlignment") == child_align, "%s childAlignment" % name
        got = M.read_pad(pf, vlg)
        assert got == {k: str(v) for k, v in pad.items()}, "%s padding=%s" % (name, got)


@pytest.mark.skipif(not PREFAB.is_file(), reason="AB 打包工程不在这台机器上")
def test_text_box_width_fits_a_sane_line_length():
    """文字区宽度（气泡宽 - 左右 padding）要能放下 16~24 个汉字。

    padding 从 12 提到 40 会吃掉 28 单位；这条防止"为了不出框把文字区挤到每行只剩几个字"
    （每行字数 = 文字区宽 / fontSize，fontSize 从预制件里真读）。
    """
    M = _mod()
    pf = M.Prefab(_read(PREFAB))
    rect_w = 450.0   # 与 BUBBLE_SIZE 一致（test_bubble_width_is_frozen 钉着它）
    for name, pad in ((M.NPC_BUBBLE, M.NPC_PAD), (M.USER_BUBBLE, M.USER_PAD)):
        go = pf.find_go(name)
        txt = M.text_comp(pf, go)
        size = float(M._nested(pf, txt, "m_FontSize"))
        assert size > 0, "%s/Text 没读到 fontSize" % name
        box = rect_w - pad["m_Left"] - pad["m_Right"]
        per_line = box / size
        assert 16.0 <= per_line <= 24.0, \
            "%s 每行 %.1f 个汉字（文字区 %.0f / 字号 %.0f）—— 超出 16~24 的合理区间" \
            % (name, per_line, box, size)


# ----------------------------------------------------------------------
# ③ 深度检查：内缩从**真实 PNG** 量（需要 Pillow，装不上就跳过）
# ----------------------------------------------------------------------

def _body_inset_px(png: Path):
    """从 PNG 的 alpha 量出 body（白底/绿底）相对图片包围盒的内缩（像素）。

    做法：逐行取最左/最右不透明像素，再各取**中位数** —— 尾巴只占三成左右的行走，
    中位数就是"没有尾巴的那些行"的 body 边缘。返回 (left, right, width, height)。
    """
    pytest.importorskip("PIL")
    from PIL import Image
    im = Image.open(png).convert("RGBA")
    px = im.load()
    W, H = im.size
    lefts, rights = [], []
    for y in range(H):
        xs = [x for x in range(W) if px[x, y][3] > 40]
        if xs:
            lefts.append(min(xs))
            rights.append(max(xs))
    assert lefts, "PNG 没有不透明像素: %s" % png
    lefts.sort()
    rights.sort()
    return lefts[len(lefts) // 2], rights[len(rights) // 2], W, H


@pytest.mark.skipif(not (PREFAB.is_file() and NPC_PNG.is_file() and USER_PNG.is_file()),
                    reason="AB 打包工程 / 气泡美术不在本机器上")
def test_padding_clears_the_png_body_inset_measured_from_the_art():
    """★ 核心语义：**文字不许画到白底外面** —— 比的是**真 prefab 里的 padding** vs **真 PNG 量的内缩**。

    图片是**整张**铺满 450 宽的 rect（`m_Type: Sliced` 但上下边=0、PPU 100 = canvas 参考 100
    ⇒ 1 sprite px = 1 UI 单位），所以图里 body 的内缩会**原样**变成 rect 里可见白底的内缩。
    实测（09-17 截图）：NPC 气泡 rect x95..540、白底 x129..540 ⇒ 内缩 33.5 单位，而老 padding
    只有 12 ⇒ 22 字长行左端落在白底外 15~16px。这条断言就是那次事故的护栏。

    ★ 必须读**资产**而不是读补丁脚本的常量：只比常量的话，"有人手改了 prefab / Unity 侧又存了一次"
      这条最危险的漂移就完全查不出来（第一版就是这么写的，变异验证第①②项当场漏掉）。
    """
    M = _mod()
    pf = M.Prefab(_read(PREFAB))
    rect_w = 450.0  # 与 BUBBLE_SIZE 一致（test_bubble_width_is_frozen 钉着它）
    for png, bubble, tail_side, inset_side in (
            (NPC_PNG, M.NPC_BUBBLE, "m_Left", "left"),
            (USER_PNG, M.USER_BUBBLE, "m_Right", "right")):
        go = pf.find_go(bubble)
        assert go, "预制件里没有 %s" % bubble
        pad = M.read_pad(pf, M.comp_with_guid(pf, go, M.VLG_GUID))
        left, right, W, H = _body_inset_px(png)
        inset_px = left if inset_side == "left" else (W - 1 - right)
        inset_units = inset_px * rect_w / W          # 整图铺满 rect ⇒ 线性换算
        assert inset_units > M.MIN_FAR_PAD, \
            "前提不成立：%s 的 body 内缩 %.1f 单位应当大于老 padding %d（否则这次改动没有意义）" \
            % (png.name, inset_units, M.MIN_FAR_PAD)
        assert float(pad[tail_side]) >= inset_units + M.TAIL_MARGIN - 1e-6, \
            "★ %s（%s）的 %s padding=%s 没让开图里 body 的内缩 %.1f 单位（+余量 %.0f）" \
            " ⇒ 文字会画到白底外" % (png.name, bubble, tail_side, pad[tail_side], inset_units, M.TAIL_MARGIN)


# ----------------------------------------------------------------------
# ④ C# 运行期兜底（"两条腿"的第一条）：与预制件常量不许漂移
# ----------------------------------------------------------------------

CHATWIN = ROOT / "csharp" / "UI" / "ChatWindow.cs"


def _const(src: str, name: str) -> int:
    m = re.search(r"const int %s = (\d+);" % re.escape(name), src)
    assert m, "找不到常量 %s（C# 兜底那条腿被删了？）" % name
    return int(m.group(1))


def test_csharp_runtime_leg_matches_the_prefab_constants():
    """C# 里 `ApplyBubbleTextLayoutFloor` 的下限必须与补丁脚本的 padding **逐项相等**。

    两条腿说的是同一件事（"文字贴左上 + 尾巴侧让开 40"）：腿之间一旦漂移，就会出现
    "重打 AB 之前 / 之后观感不一样"这种最难查的 bug（预制件那条腿改了、C# 那条没改，
    于是老 AB 与新 AB 表现不同）。
    """
    M = _mod()
    src = _read(CHATWIN)
    assert _const(src, "BUBBLE_TAIL_PAD") == M.NPC_PAD["m_Left"], "尾巴侧下限与补丁常量不一致"
    assert _const(src, "BUBBLE_TAIL_PAD") == M.USER_PAD["m_Right"], "两侧尾巴侧下限应当一致（都让开各自内缩）"
    assert _const(src, "BUBBLE_FAR_PAD") == M.NPC_PAD["m_Right"], "非尾巴侧下限与补丁常量不一致"
    assert _const(src, "BUBBLE_FAR_PAD") == M.USER_PAD["m_Left"], "两侧非尾巴侧下限应当一致"
    assert _const(src, "BUBBLE_PAD_V") == M.NPC_PAD["m_Top"] == M.USER_PAD["m_Top"], "上下限与补丁常量不一致"


def test_csharp_runtime_leg_is_wired_and_keeps_upper_left():
    """兜底必须**在 Init 里真的调用**，而且纠正方向是 UpperLeft（不是反向）。"""
    src = _read(CHATWIN)
    assert "ApplyBubbleTextLayoutFloor();" in src, "兜底函数没被调用 = 老 AB 不会自动修好"
    assert "private void ApplyBubbleTextLayoutFloor()" in src
    assert "private static bool FixBubbleLayout(" in src
    # 两个模板都要兜（漏一个 = 那一侧仍然错）
    for name in ("NpcBubble", "UserBubble"):
        assert '"%s"' % name in src, "兜底没覆盖 %s" % name
    assert "text.alignment = TextAnchor.UpperLeft" in src, "正文对齐的纠正方向必须是 UpperLeft"
    assert "vlg.childAlignment = TextAnchor.UpperLeft" in src, "子项对齐的纠正方向必须是 UpperLeft（贴顶）"
    # 语义是"下限/纠正"，不是无条件覆盖（预制件配得更大就听预制件的）
    assert "Mathf.Max(" in src, "padding 必须是下限语义（只抬高不缩小）"


# ----------------------------------------------------------------------
# ⑤ 九宫格 border：气泡变高时圆角不许被拉成椭圆（10-31）
# ----------------------------------------------------------------------

AB_ART = AB / "Assets" / "art"


def _cs_border(src: str, name: str) -> int:
    # 名字与 "=" 之间允许对齐用的多空格（`BUBBLE_BORDER_NPC_L  = 38;`）
    m = re.search(r"const int %s\s+=\s*(\d+);" % re.escape(name), src)
    assert m, "ChatWindow 里找不到常量 %s（运行期兜底那条腿被删了？）" % name
    return int(m.group(1))


def test_sprite_border_matches_between_meta_and_csharp():
    """★ 两条腿说的是同一件事：`.png.meta` 的 `spriteBorder` 与 C# 的常量必须逐项相等。

    border = (x, y, z, w) = (left, bottom, right, top)，单位 = 图片像素。

    **症状（10-31 实测）**：上下 border 是 0 时，`Image.Type.Sliced` 会把气泡**纵向整段拉伸**
    ⇒ 长气泡的两端圆角变成椭圆、尾巴拉成一根尖刺。短气泡看不出来，**文本一长就变形**。
    而"文本长"恰恰是这轮要修的场景。
    """
    src = CHATWIN.read_text(encoding="utf-8")
    cases = (
        ("npc气泡.png", "BUBBLE_BORDER_NPC"),
        ("玩家气泡.png", "BUBBLE_BORDER_USER"),
    )
    for png, prefix in cases:
        meta = AB_ART / (png + ".meta")
        if not meta.is_file():
            pytest.skip("AB 工程的 art 不在这台机器上：%s" % png)
        text = meta.read_text(encoding="utf-8")
        m = re.search(r"spriteBorder: \{x: (\d+), y: (\d+), z: (\d+), w: (\d+)\}", text)
        assert m, "%s 的 meta 里没有 spriteBorder" % png
        want = tuple(int(x) for x in m.groups())          # L, B, R, T
        got = (_cs_border(src, prefix + "_L"), _cs_border(src, prefix + "_B"),
               _cs_border(src, prefix + "_R"), _cs_border(src, prefix + "_T"))
        assert got == want, (
            "%s 的 border 两条腿不一致：\n  meta   = %s（L,B,R,T）\n  ChatWindow = %s"
            % (png, want, got))


def test_sprite_border_has_all_four_sides_set():
    """四个方向都必须是**非零**（上下为 0 = 纵向整段拉伸 = 椭圆）。

    左右可以只有一个"圆角量"（比如玩家气泡左边圆角只有 10px），但**不能是 0**：
    0 意味着那一侧没有固定区，圆角会被横向拉变形。
    """
    src = CHATWIN.read_text(encoding="utf-8")
    for prefix in ("BUBBLE_BORDER_NPC", "BUBBLE_BORDER_USER"):
        for side in ("_L", "_B", "_R", "_T"):
            v = _cs_border(src, prefix + side)
            assert v > 0, (
                "%s%s = 0 —— 该方向没有固定区，九宫格会把圆角拉变形"
                "（10-31 的 bug 就是上下为 0）" % (prefix, side))


def test_runtime_leg_actually_rebuilds_the_sprite():
    """运行期兜底必须真的**造一份新 Sprite** 再赋回去。

    `Sprite.border` 是**只读**属性 —— 想改只能 `Sprite.Create(...)` 造新的。
    这条防止有人写成 `img.sprite.border = ...`（编译就不过）或干脆只打日志不动作
    （那样老 AB 上永远是椭圆角）。
    """
    src = CHATWIN.read_text(encoding="utf-8")
    i = src.index("private static bool FixBubbleLayout(")
    body = src[i: i + 8000]
    assert "Sprite.Create(" in body, "运行期兜底没造新 Sprite（Sprite.border 是只读的，改不了）"
    # ★ 必须是**无条件**赋值。第一版只断言 `"img.sprite = "` 出现过，于是把外层条件
    #   改成 `if (false)` 仍然通过（假绿）—— 那等于兜底只造对象不生效。
    #   这里要求赋值**不在任何 if 里**：从 Sprite.Create 到赋值之间不能有 `if (false` 之类。
    assert re.search(r"\n\s+if \(fixedSpr != null\) \{ img\.sprite = fixedSpr; changed = true; \}", body), \
        "运行期兜底没把新 Sprite 无条件赋回 Image（造了新 Sprite 却不赋回 = 白做）"
    assert "SpriteMeshType.FullRect" in body, (
        "Sprite.Create 必须传 FullRect —— 九宫格拉伸要求整张矩形网格"
    )


# ----------------------------------------------------------------------
# ⑥ 运行期兜底能不能在**部署的那份 .ab** 上命中（不需要 Unity 也能验）
# ----------------------------------------------------------------------

def test_ab_fallback_checker_covers_every_thing_the_fallbacks_touch():
    """兜底脚本必须覆盖 `FixBubbleLayout` 的**每一处**改动。

    这个检查存在的理由：AB 已经一个半月没重打（包 09-17 / prefab 10-31），
    玩家实际只跑运行期那条腿。而一旦 `.ab` 里缺某个节点/组件，
    `FixBubbleLayout` 会在 `if (b == null) return false;` 处**静默跳过** ——
    代码全对、测试全绿、玩家什么都没看到。

    所以 `scripts/dev/check_ab_fallback.py` 的 CHECKS 必须与 C# 的四条改动一一对应：
    ① 对齐（m_Alignment）② 内边距（m_Padding / m_ChildAlignment）
    ③ 行距 + 富文本（m_LineSpacing / m_RichText）④ 九宫格（m_Sprite）。
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "check_ab_fallback", ROOT / "scripts" / "dev" / "check_ab_fallback.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    keys = {k for (_use, _what, k) in mod.CHECKS}
    required = {
        "NpcRow", "UserRow", "NpcBubble", "UserBubble",   # 找得到模板与子节点
        "m_Alignment",                                     # ① 对齐
        "m_Padding", "m_ChildAlignment",                   # ② 内边距
        "m_LineSpacing", "m_RichText",                     # ③ 行距 + 富文本
        "m_Sprite",                                        # ④ 九宫格 border
    }
    missing = required - keys
    assert not missing, "check_ab_fallback.py 漏了这些结构（对应某条兜底会静默跳过）：%s" % sorted(missing)


@pytest.mark.skipif(not AB.is_dir(), reason="AB 工程 / 游戏不在这台机器上")
def test_deployed_ab_still_supports_the_runtime_fallbacks():
    """直接跑一遍检查器（游戏在这台机器上时）。

    这条会在"有人换了份更老的 .ab"时立刻变红 —— 那正是最危险的情况：
    代码没动、测试没动，玩家那边的修复却凭空消失了。
    """
    import subprocess
    ab = Path("/mnt/e/SteamLibrary/steamapps/common/鬼谷八荒/ModExportData/Mod_Jgmg5L"
              "/ModRes/AssetBundle/ab/ui/uichatai.ab")
    if not ab.is_file():
        pytest.skip("部署的 .ab 不在")
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "dev" / "check_ab_fallback.py")],
                       capture_output=True, text=True)
    assert r.returncode == 0, (
        "部署的 .ab 缺结构 ⇒ 运行期兜底会静默跳过：\n" + r.stdout + r.stderr)


# ----------------------------------------------------------------------
# ⑦ 重打 AB 的前置：两张气泡图必须被 ABBuildRunner 登记（否则打完变白块）
# ----------------------------------------------------------------------

AB_RUNNER = AB / "Assets" / "Scripts" / "ABBuild" / "Editor" / "ABBuildRunner.cs"


def test_ab_runner_registers_both_bubble_textures():
    """★ `BuildPipeline` **只打"已分配 bundle 名"的资源**。

    10-31 实测：部署的 `uichatai.ab` 字符串表里只有 `panel_bg.png`，
    **`npc气泡.png` 与 `玩家气泡.png` 都不在** —— 它们一直靠首次构建的依赖残留
    （`assets/art/聊天气泡.png.ab` 之类）解析。

    于是重打时如果没把它们并进来，两个气泡的底图会**变成白块**；而"自包含单包"
    正是 09-06 定下的规矩（那条注释就在 runner 文件头里）。同一类坑 09-08 在通讯录上
    已经踩过一次（圆点没登记 ⇒ 改了也永远打不出来）。
    """
    if not AB_RUNNER.is_file():
        pytest.skip("AB 打包工程不在这台机器上")
    src = AB_RUNNER.read_text(encoding="utf-8")
    assert "Assets/art/npc气泡.png" in src, (
        "ABBuildRunner 没登记 npc气泡.png ⇒ 重打后 NPC 气泡底图会变白块"
    )
    assert "Assets/art/玩家气泡.png" in src, (
        "ABBuildRunner 没登记 玩家气泡.png ⇒ 重打后玩家气泡底图会变白块"
    )
    # 必须真的赋给 chat bundle（只写常量不赋值 = 没登记）
    assert re.search(r"Assign\(NpcBubblePath,\s*ChatBundle\)", src), \
        "npc气泡.png 没赋给 ChatBundle（只声明常量不算登记）"
    assert re.search(r"Assign\(UserBubblePath,\s*ChatBundle\)", src), \
        "玩家气泡.png 没赋给 ChatBundle"


def test_ab_runner_fails_loudly_on_missing_asset():
    """登记路径不存在时必须**抛一条带名字的异常**，不能靠 null 引用崩或者静默跳过。

    `AssetImporter.GetAtPath` 对不存在的路径返回 null；下一行 `.assetBundleName =` 会
    抛 NullReference —— 在 batchmode 下表现为"构建成功但产物里少东西"，
    而 batchmode 的日志很容易没人看。这里要求显式检查。
    """
    if not AB_RUNNER.is_file():
        pytest.skip("AB 打包工程不在这台机器上")
    src = AB_RUNNER.read_text(encoding="utf-8")
    assert "if (imp == null)" in src, "Assign 没检查 GetAtPath 的 null 返回"
    assert "FileNotFoundException" in src, "缺资源时没有明确报错（应抛带路径的异常）"
    # 不允许再出现「直接 GetAtPath(...).assetBundleName =」这种一行式
    assert not re.search(r"AssetImporter\.GetAtPath\([^)]*\)\.assetBundleName", src), \
        "还有一处直接对 GetAtPath 的返回值赋值（null 就会崩）"
