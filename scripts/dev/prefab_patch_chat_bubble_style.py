# -*- coding: utf-8 -*-
"""UIChatAi.prefab —— 气泡文字版式三件：**左右对齐互换** + **内边距让开尾巴** + **子项垂直居中**（幂等）。

═══ 症状（用户 09-17 实机截图） ═══

    NPC 气泡（左侧）第一行/第二行**头一个字跑到白底外面**去，压在透明的尾巴区上；
    `)` 单独一行却贴到气泡**最右边**、`见过道友。` 也靠右 —— 左边气泡用了右边那套对齐。

═══ 三个根因（都是预制件字段，逐条实测过） ═══

① **NPC 侧对齐错了**（`/NpcBubble/Text` 的 `m_FontData.m_Alignment`）：

       NpcBubble/Text   m_Alignment: 2  = TextAnchor.UpperRight  → NPC 文字右对齐  ✗
       UserBubble/Text  m_Alignment: 0  = TextAnchor.UpperLeft   → 玩家文字左对齐  ✓（本来就对）

   后果：NPC 的长行右对齐后**左端会伸到文本框之外**（下一帧再由 wrap 收回来），短行（`)`、
   `见过道友。`）则贴右边缘 —— 看着就是"格式不对"。

   ⚠ 第一版把这里写成"两边装反了"、顺手把玩家侧改成右对齐 + 垂直居中，**是错的**：
     气泡在窗口里的左右位置是**行模板 `HorizontalLayoutGroup`（childAlignment 3 / 5）**决定的，
     与文字对齐无关。玩家侧当年实机截图纠正为"文字同样贴左上"。

② **内边距没算气泡图的尾巴内缩**。两张图把尾巴画在**图片包围盒里面**：

       npc气泡.png   470×125   body 从 x=35 开始（尾巴 x1..35 在左） → 左边缩进 7.4%
       玩家气泡.png  468×139   body 到 x=435 为止（尾巴 435..466 在右）→ 右边缩进 7.0%

   而两个气泡的 `VerticalLayoutGroup.m_Padding` 都是 `(L12, R12, T8, B8)`。Image 是**整张图**
   铺满 450 宽的 rect（`m_Type: 1` + `m_PixelsPerUnitMultiplier: 1`，sprite PPU 100 = canvas
   参考 PPU 100 ⇒ 1 sprite px = 1 单位），所以可见白底只占 rect 的 x∈[33.5, 448]：
   **12 < 33.5** ⇒ 靠尾巴那一侧的文字有 ~20 单位画在白底外面。

   实测（截图按 1:1、canvas scale=1）：NPC 气泡 rect x95..540、可见白底 x129..540；
   右对齐的 22 字长行左端落在 x113~114 ⇒ 比白底左沿还左 **15~16px** ✔ 与推导一致。
   玩家气泡同理镜像（长行会右溢 ~19px 进尾巴区）。

   本补丁把**尾巴那一侧**的 padding 提到 40（> 35/33 的内缩，留 5~7 单位余量），另一侧 14。

③ **子项对齐**：两个气泡都是 `m_ChildAlignment: 0`（UpperLeft）—— 这是**对的**，保持 0。
   （第一版曾改成 3/5 想"单行泡垂直居中"，用户实机纠正：要**顶部**。见 ① 的说明。）

═══ 为什么 padding 是 40/14 而不是别的数 ═══

    尾巴侧必须 > 图里 body 的内缩（35 / 33 单位）+ 一点余量 ⇒ 40。
    另一侧只要 ≥ 原有的 12 即可（图那一侧 body 贴在 rect 边缘，内缩 1~2 单位）⇒ 留 14。
    上下从 8 提到 12：实测首行墨迹离气泡顶沿只剩 **4px**（字形的 ascent 吃掉了一部分 padding），
    提到 12 后约 8px，与左右观感一致。

    ⚠ 气泡宽度**保持 450 不变**（用户 09-17 明确选择）。代价是文字区从 426 收窄到 396，
      每行从 22 字变 20 字 —— 换行会多一点，但每个字都在白底里。

═══ 用了脚本而不是手改的理由 ═══

    `m_Alignment` 与 `m_Padding` 都是**嵌套字段**（4 空格缩进），`Prefab.field/set_field`
    的正则只认 2 空格 ⇒ 手改一次很容易漏掉两边里的一边（漏一边 = 一个气泡仍然出框），
    而本脚本两边都改、改完再独立复核一遍。幂等：重复跑第二次字节数完全不变。

═══ 与 C# 的分工 ═══

    本补丁只解决**几何**。"正文里的空行导致行距翻倍"是**显示层**的事，在
    `csharp/UI/BubbleTextCore.cs`（`Tighten`）+ `ChatWindow`/`ChatItemViews` 的写泡点做
    （见 tests/test_bubble_text_contract.py）。两者互不依赖，可以分别生效。

用法:
    python3 scripts/dev/prefab_patch_chat_bubble_style.py \\
        --in  "<AB工程>/Assets/Resources/UI/UIChatAi.prefab" \\
        --out "<同上>" --allow-protected-out
    --dry-run 只算不写；原地改默认先写 .bak_bubblelayout 备份（**只在备份不存在时**）。
"""
import argparse
import io
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from prefab_patch_contact_groups import (          # noqa: E402
    Prefab, RE_COMPONENT_REF, _fix_console,
)

# ══════════════════════════════════════════════════════════════════
# 版式常量（测试 tests/test_chat_bubble_layout_contract.py 直接 import 这些名字）
# ══════════════════════════════════════════════════════════════════

NPC_BUBBLE = "NpcBubble"
USER_BUBBLE = "UserBubble"

#: UnityEngine.UI.Text / VerticalLayoutGroup 的内置脚本 guid（与 prefab 里既有的一致）
TEXT_GUID = "5f7201a12d95ffc409449d95f23cf332"
VLG_GUID = "59f8146938fff824cb5fd77236b75775"
LAYOUT_ELEMENT_GUID = "306cc8c2b49d7114eaa3623786fc2126"

#: TextAnchor：0=UpperLeft 1=UpperCenter 2=UpperRight 3=MiddleLeft 5=MiddleRight
#:
#: ★ 2026-09-17 实机纠正：**两侧正文都是「左对齐 + 顶部」**（0 / 0）。
#:   第一版把玩家侧改成右对齐(2)+垂直居中(5) —— 那是把"气泡靠屏幕右边"（由**行模板的
#:   HorizontalLayoutGroup** 决定，与文字无关）错当成了"文字该右对齐"。用户截图纠正：
#:   玩家气泡里文字要贴**左上**，和 NPC 侧一样。`2/5` 那套只适合"文字宽度 == 气泡宽度"的情形，
#:   而本工程气泡是**固定 450 宽**、文字只占其中一段，右对齐会让短句浮在气泡中间偏右。
NPC_ALIGN = "0"          # NPC 气泡：左对齐
USER_ALIGN = "0"         # 玩家气泡：同样左对齐（气泡整体靠右是 Row 的事）
NPC_CHILD_ALIGN = "0"    # UpperLeft：单行泡文字贴顶（用户要"顶部"）
USER_CHILD_ALIGN = "0"   # 同上

#: VerticalLayoutGroup.m_Padding（L/R/T/B，单位 = UI 单位）
NPC_PAD = {"m_Left": 40, "m_Right": 14, "m_Top": 12, "m_Bottom": 12}
USER_PAD = {"m_Left": 14, "m_Right": 40, "m_Top": 12, "m_Bottom": 12}

#: 气泡图上 body 相对图片包围盒的内缩（单位，PPU 100 = canvas 参考 PPU 100 ⇒ 1px = 1 单位）。
#: npc气泡.png：body 从 x=35/470 开始；玩家气泡.png：body 到 x=435/468 为止。
#: 尾巴那一侧的 padding 必须 > 这个数，否则文字又会画到白底外 —— verify 里会实算卡这条。
NPC_TAIL_INSET = 35.0
USER_TAIL_INSET = 33.0
#: 尾巴侧 padding 相对内缩要留的余量（宁可多留几单位，也不要"刚好贴边"）
TAIL_MARGIN = 4.0
#: 非尾巴侧的最小 padding（= 改造前的 12，不许更小）
MIN_FAR_PAD = 12

#: 气泡尺寸：本补丁**不动**宽度（用户选择保持 450），只改文字区
BUBBLE_SIZE = "{x: 450, y: 76}"
#: 高度保底：LayoutElement 必须与 VLG **同优先级**（0）才能"取大者"。
#: 若有人把 m_LayoutPriority 改回 1，LayoutElement 会独占 preferredHeight ⇒
#: 长文本气泡**永远这个高**、文字被 `vOverflow=Truncate` 截掉（且不报错）。verify 卡它。
LAYOUT_PRIORITY = "0"
#: 气泡**保底高度**（LayoutElement.m_PreferredHeight）。
#:
#: ★ 11-20：76 → 50 ★（用户："最小高度是两行文字，很别扭"）
#:   76 恰好是**两行**：字号 19 × 行距 1.35 = 25.65/行，加气泡上下内边距 12+12 = 24
#:   ⇒ 单行 49.65、两行 75.30。所以旧值 76 让一条"嗯""好"也硬撑两行高。
#:   50 = 单行（49.65）向上取整，留 0.35 余量防半像素截断。
#:
#: ⚠ 与 `BUBBLE_SIZE` 的 y 不是同一个东西，别一起改：
#:   · `BUBBLE_SIZE` = RectTransform.m_SizeDelta（布局结算**前**的初值，会被
#:     ContentSizeFitter 的 `m_VerticalFit=PreferredSize` 覆盖掉）；
#:   · 本常量 = LayoutElement.m_PreferredHeight（VLG 的**保底**，与自算高度取大者）。
#:   真正决定"一行也占两行高"的是后者 —— 所以只改这一个。
LAYOUT_PREF_HEIGHT = "50"

#: 气泡正文的行距（uGUI `Text.m_FontData` 里的 `m_LineSpacing`，1 = 紧贴）。
#:
#: ★ 2026-10-31（用户实机反馈"文字多的时候密密麻麻"）★
#:   默认 1 时中文两行墨迹几乎正好相接（中文字形满框，不像拉丁字母自带留白），
#:   一整段读下来没有呼吸。1.35 与 `NoticePanel` 正文同款，实测好读。
#:
#: ⚠ 这是**两条腿**里的预制件那条：`csharp/UI/BubbleTextCore.cs` 的
#:   `BubbleLineSpacing` 是另一条（运行期兜底，老 AB 一重启就对）。
#:   两处数字必须逐值相等，否则会出现"重打 AB 前后观感不同"——
#:   `tests/test_chat_bubble_layout_contract.py` 会比对。
LINE_SPACING = "1.35"

#: 富文本开关（`Text.m_FontData.m_RichText`）。1 = 开。
#: 气泡正文的模型输出要先过 `BubbleTextCore.Render()` 的白名单，
#: 但**渲染开关必须是开的**，否则 `<color=…>` 会原样上屏。
RICH_TEXT = "1"

BACKUP_SUFFIX = ".bak_bubblelayout"

PAD_KEYS = ("m_Left", "m_Right", "m_Top", "m_Bottom")


def log(*a):
    print("[chat-bubble-style]", *a)


# ----------------------------------------------------------------------
# 嵌套字段读写（4 空格缩进 —— Prefab.field/set_field 只认 2 空格，不能用）
# ----------------------------------------------------------------------

def _comps_of(pf, go_fid):
    return RE_COMPONENT_REF.findall(pf.doc[go_fid])


def _script_guid(pf, comp_fid):
    m = re.search(r"guid: ([0-9a-f]{32})", pf.doc[comp_fid])
    return m.group(1) if m else ""


def comp_with_guid(pf, go_fid, guid):
    """这个 GO 上挂着指定脚本 guid 的组件（找不到返回 None）。"""
    for c in _comps_of(pf, go_fid):
        if pf.cls(c) == "114" and _script_guid(pf, c) == guid:
            return c
    return None


def text_comp(pf, bubble_go):
    """气泡的正文 Text —— 它挂在**子节点 `Text`** 上（气泡本体只有
    Image/VLG/CSF/LayoutElement）。子节点名是 C# 取文本那条路的字符串契约
    （`ChatBubble` 用 `GetComponentInChildren<Text>`，不认名字），所以这里按名字找。"""
    child = pf.child_go_named(bubble_go, "Text")
    assert child, "%s 下没有名为 Text 的子节点" % pf.go_name(bubble_go)
    return comp_with_guid(pf, child, TEXT_GUID)


def _nested(pf, comp_fid, key):
    m = re.search(r"(?m)^    %s: (-?\d+)\s*$" % re.escape(key), pf.doc[comp_fid])
    return m.group(1) if m else None


def _set_nested(pf, comp_fid, key, value):
    pat = re.compile(r"(?m)^    %s: -?\d+\s*$" % re.escape(key))
    new, n = pat.subn(lambda m: "    %s: %s" % (key, value), pf.doc[comp_fid])
    assert n == 1, "嵌套字段写入失败 %s.%s（命中 %d 次）" % (comp_fid, key, n)
    pf.doc[comp_fid] = new


# ----------------------------------------------------------------------
# m_FontData 里的**浮点**字段（m_LineSpacing: 1 是 float，不是 int ——
# `_set_nested` 的 `-?\d+` 匹配不到它，直接复用会静默 0 命中并 assert 掉）
# ----------------------------------------------------------------------

_RE_FLOAT = r"-?\d+(?:\.\d+)?"


def read_line_spacing(pf, comp_fid):
    m = re.search(r"(?m)^    m_LineSpacing: (%s)\s*$" % _RE_FLOAT, pf.doc[comp_fid])
    return m.group(1) if m else None


def read_rich(pf, comp_fid):
    """`m_RichText` 在 m_FontData 里是 int（0/1），但同一文件里别处也有 `m_RichText` 的可能性为零
    （只有 Text 组件带它），所以按 4 空格嵌套位取即可。"""
    m = re.search(r"(?m)^    m_RichText: (\d+)\s*$", pf.doc[comp_fid])
    return m.group(1) if m else None


def _set_font_field(pf, comp_fid, key, value):
    """写 m_FontData 里的字段（浮点/整数都行，按**出现次数恰好 1** 校验）。"""
    pat = re.compile(r"(?m)^    %s: %s\s*$" % (re.escape(key), _RE_FLOAT))
    new, n = pat.subn(lambda m: "    %s: %s" % (key, value), pf.doc[comp_fid])
    assert n == 1, "字体字段写入失败 %s.%s（命中 %d 次 —— m_FontData 结构变了？）" % (comp_fid, key, n)
    pf.doc[comp_fid] = new


def read_pad(pf, vlg_fid):
    return {k: _nested(pf, vlg_fid, k) for k in PAD_KEYS}


def read_align(pf, text_fid):
    """Text 的 m_Alignment 在 m_FontData 块里（4 空格）。"""
    return _nested(pf, text_fid, "m_Alignment")


# ----------------------------------------------------------------------
# 补丁
# ----------------------------------------------------------------------

#: (节点名, Text 对齐, VLG 子项对齐, padding)
PLAN = (
    (NPC_BUBBLE, NPC_ALIGN, NPC_CHILD_ALIGN, NPC_PAD),
    (USER_BUBBLE, USER_ALIGN, USER_CHILD_ALIGN, USER_PAD),
)


def patch(text, do_log=True):
    """打补丁。写的是**目标值**，所以重复跑天然幂等（第二次字节数不变）。"""
    pf = Prefab(text)
    assert pf.text() == text, "Prefab 拆分-拼回不一致（脚本自身的问题，先别改真文件）"

    for name, align, child_align, pad in PLAN:
        go = pf.find_go(name)
        assert go, "找不到节点 %s（预制件结构变了？）" % name
        txt = text_comp(pf, go)
        vlg = comp_with_guid(pf, go, VLG_GUID)
        assert txt, "%s/Text 上找不到 UI.Text 组件" % name
        assert vlg, "%s 上找不到 VerticalLayoutGroup 组件" % name

        old_align, old_pad = read_align(pf, txt), read_pad(pf, vlg)
        old_ls, old_rich = read_line_spacing(pf, txt), read_rich(pf, txt)
        if do_log:
            log("%-10s alignment %s → %s ；padding %s → %s ；childAlign %s → %s"
                " ；lineSpacing %s → %s ；richText %s → %s"
                % (name, old_align, align,
                   tuple(old_pad[k] for k in PAD_KEYS), tuple(str(pad[k]) for k in PAD_KEYS),
                   pf.field(vlg, "m_ChildAlignment"), child_align,
                   old_ls, LINE_SPACING, old_rich, RICH_TEXT))
        _set_nested(pf, txt, "m_Alignment", align)
        pf.set_field(vlg, "m_ChildAlignment", child_align)
        for k in PAD_KEYS:
            _set_nested(pf, vlg, k, str(pad[k]))
        # 10-31：行距与富文本开关（都是 m_FontData 里的嵌套字段，与 m_Alignment 同层）
        _set_font_field(pf, txt, "m_LineSpacing", LINE_SPACING)
        _set_font_field(pf, txt, "m_RichText", RICH_TEXT)

    out = pf.text()
    verify(out)
    return out


def verify(text):
    """独立复核（重新解析产物，不信任 patch 的中间状态）。失败即抛 AssertionError。"""
    pf = Prefab(text)
    pads = {}
    aligns = {}
    for name, align, child_align, pad in PLAN:
        go = pf.find_go(name)
        assert go, "复核失败：%s 不见了" % name
        txt = text_comp(pf, go)
        vlg = comp_with_guid(pf, go, VLG_GUID)
        assert txt and vlg, "复核失败：%s 缺 Text/VLG" % name

        got_align = read_align(pf, txt)
        assert got_align == align, \
            "复核失败：%s/Text 对齐应为 %s，实际 %s" % (name, align, got_align)
        assert pf.field(vlg, "m_ChildAlignment") == child_align, \
            "复核失败：%s 的 childAlignment 应为 %s" % (name, child_align)
        got_pad = read_pad(pf, vlg)
        want_pad = {k: str(v) for k, v in pad.items()}
        assert got_pad == want_pad, \
            "复核失败：%s padding 应为 %s，实际 %s" % (name, want_pad, got_pad)

        # 宽度不许动（用户选择保持 450）
        rt = pf.rt_of_go(go)
        assert pf.field(rt, "m_SizeDelta") == BUBBLE_SIZE, \
            "复核失败：%s 尺寸被改了（%s）—— 本补丁只改文字版式" % (name, pf.field(rt, "m_SizeDelta"))

        # 高度保底机制：LayoutElement 必须与 VLG 同优先级（否则长文本气泡恒 76、文字被截断）
        le = comp_with_guid(pf, go, LAYOUT_ELEMENT_GUID)
        assert le, "复核失败：%s 上没有 LayoutElement" % name
        assert pf.field(le, "m_LayoutPriority") == LAYOUT_PRIORITY, \
            "★ %s 的 LayoutElement.m_LayoutPriority 必须是 %s（=1 会把气泡高度钉死在 %s，" \
            "长文本被 vOverflow=Truncate 静默截掉）" % (name, LAYOUT_PRIORITY, LAYOUT_PREF_HEIGHT)
        assert pf.field(le, "m_PreferredHeight") == LAYOUT_PREF_HEIGHT, \
            "复核失败：%s 的保底高度应为 %s" % (name, LAYOUT_PREF_HEIGHT)

        # 10-31：行距与富文本开关（"密密麻麻"与"上色"两件事的资产那一半）
        got_ls = read_line_spacing(pf, txt)
        assert got_ls == LINE_SPACING, \
            "★ 复核失败：%s/Text 的 m_LineSpacing 应为 %s，实际 %s" \
            "（1 = 两行墨迹紧贴，正是用户说的「密密麻麻」）" % (name, LINE_SPACING, got_ls)
        got_rich = read_rich(pf, txt)
        assert got_rich == RICH_TEXT, \
            "★ 复核失败：%s/Text 的 m_RichText 应为 %s，实际 %s" \
            "（0 = <color> 原样上屏）" % (name, RICH_TEXT, got_rich)

        pads[name] = {k: float(got_pad[k]) for k in PAD_KEYS}
        aligns[name] = got_align

    # ---- 语义复核①：尾巴那一侧的 padding 必须真的让开图片里的尾巴 ----
    npc_tail_side = pads[NPC_BUBBLE]["m_Left"]      # NPC 尾巴在左
    usr_tail_side = pads[USER_BUBBLE]["m_Right"]    # 玩家尾巴在右
    assert npc_tail_side >= NPC_TAIL_INSET + TAIL_MARGIN, \
        "★ NpcBubble 左 padding %.0f ≤ 图里 body 的内缩 %.0f + 余量 %.0f ⇒ 文字会画到白底外" \
        % (npc_tail_side, NPC_TAIL_INSET, TAIL_MARGIN)
    assert usr_tail_side >= USER_TAIL_INSET + TAIL_MARGIN, \
        "★ UserBubble 右 padding %.0f ≤ 图里 body 的内缩 %.0f + 余量 %.0f ⇒ 文字会画到白底外" \
        % (usr_tail_side, USER_TAIL_INSET, TAIL_MARGIN)
    for nm in (NPC_BUBBLE, USER_BUBBLE):
        far_side = pads[nm]["m_Right"] if nm == NPC_BUBBLE else pads[nm]["m_Left"]
        assert far_side >= MIN_FAR_PAD, \
            "复核失败：%s 非尾巴侧 padding %.0f < %d（比改造前还紧）" % (nm, far_side, MIN_FAR_PAD)

    # ---- 语义复核②：两侧正文都必须「左对齐」（气泡整体靠哪边是 Row 的 HLG 的事）----
    for nm in (NPC_BUBBLE, USER_BUBBLE):
        assert aligns[nm] == "0", \
            "★ %s 的正文必须左对齐（TextAnchor.UpperLeft）；现在是 %s。" \
            "气泡在窗口里的左右位置由行模板 HorizontalLayoutGroup 决定，**不能**用文字右对齐去凑。" \
            % (nm, aligns[nm])
    assert pads[NPC_BUBBLE]["m_Left"] > pads[NPC_BUBBLE]["m_Right"], \
        "★ NpcBubble 的尾巴在左边，左 padding 必须大于右 padding"
    assert pads[USER_BUBBLE]["m_Right"] > pads[USER_BUBBLE]["m_Left"], \
        "★ UserBubble 的尾巴在右边，右 padding 必须大于左 padding"
    return True


def main(argv=None):
    _fix_console()
    ap = argparse.ArgumentParser(description="UIChatAi.prefab 气泡文字版式（对齐/内边距，幂等）")
    ap.add_argument("--in", dest="inp", required=True, help="源 prefab")
    ap.add_argument("--out", dest="out", required=True, help="目标 prefab（可与 --in 相同=原地改）")
    ap.add_argument("--dry-run", action="store_true", help="只算不写")
    ap.add_argument("--no-backup", action="store_true", help="原地改时不写备份")
    ap.add_argument("--allow-protected-out", action="store_true",
                    help="允许写到真实游戏工程（默认拒绝）")
    a = ap.parse_args(argv)

    outp = os.path.abspath(a.out)
    low = outp.replace("/", os.sep).lower()
    if not a.allow_protected_out:
        for p in ("/mnt/e/SteamLibrary/steamapps/common/鬼谷八荒/Mod/modFQA/资源修改教程/",
                  "e:\\steamlibrary\\steamapps\\common\\鬼谷八荒\\mod\\modfqa\\资源修改教程\\"):
            if p.replace("/", os.sep).lower() in low:
                log("拒绝写入受保护路径: %s" % outp)
                log("（确实要写请显式加 --allow-protected-out）")
                return 2

    inplace = os.path.abspath(a.inp) == outp
    with io.open(a.inp, "r", encoding="utf-8") as f:
        text = f.read()
    log("== 输入 %s (%d 字节)" % (a.inp, len(text.encode("utf-8"))))
    out = patch(text)
    if out == text:
        log("== 目标值已就位（幂等：字节数不变 %d）" % len(out.encode("utf-8")))
        return 0
    if a.dry_run:
        log("== dry-run：未写盘（输出将 %d 字节）" % len(out.encode("utf-8")))
        return 0
    if inplace and not a.no_backup:
        from prefab_patch_contact_groups import ensure_backup
        bak = outp + BACKUP_SUFFIX
        if ensure_backup(outp, bak):
            log("== 备份 %s" % bak)
        else:
            log("== 备份已存在，不覆盖（保留最早的原始件）：%s" % bak)
    with io.open(outp, "w", encoding="utf-8", newline="") as f:
        f.write(out)
    log("== 输出 %s (%d 字节)" % (outp, len(out.encode("utf-8"))))
    log("== 自校验通过（对齐/padding/子项对齐/宽度未动/保底优先级/尾巴让位）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
