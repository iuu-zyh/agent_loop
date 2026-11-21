# -*- coding: utf-8 -*-
"""UIChatAi.prefab —— 输入区补一个「跳过」按钮（群聊：玩家这一轮不说话，NPC 自己接下去）。

改造前（`BG/Input` 直下 2 个节点，输入面板高 110、底边锚定）：

    BG (1200×800)
    └─ Input                      a(0,0)..(1,0)  piv(0.5,0)  pos(0,20)  size(0,110)
       ├─ ChatInput               a(0,0)..(1,0)  piv(0.5,0.5) pos(0,55)  size(0,110)   输入框
       │   ├─ Placeholder         a(0,1) piv(0.5,0.5) pos(398.1,-43) size(780,70)  ← 文字带 x 8..788
       │   └─ Text
       └─ SendBtn                 a(1,0) piv(0.5,0.5) pos(-20,15)  size(40,30)  「发送」✕

改造后（**只追加**一个节点，既有节点一个字节都不动）：

       └─ SkipBtn  ★ a(1,0) piv(0.5,0.5) pos(-20,53) size(40,30) 「跳过」 默认隐藏
          └─ Text

   右下角按钮列（自下而上）：发送(y 0..30) → 8px → 跳过(y 38..68)

★ 为什么克隆 **SendBtn** 而不是顶栏那批用的 `CompactBtn`：
  顶栏几个按钮（压缩/拉人/踢人）克隆 `CompactBtn` 是对的 —— 它们与它同排。
  这一颗与它**正下方那颗**是同一列、同一功能组（都属于"发出去"这个动作），
  字号/颜色/Image 精灵/Button 过渡必须与 `SendBtn` 逐字相同。克隆源选错的表现是
  "这颗按钮的字比下面那颗大一号/描边不同"，而且**不报错**。

★ 为什么锚在右下角、而不是左对齐往后排：这一列要跟着输入框右下角走（输入框是
  `a(0,0)..(1,0)` 满宽锚定）。用右锚点 ⇒ 窗口变宽时按钮跟着右边缘，与 `SendBtn` 永远对齐。

★ 为什么默认 `m_IsActive=0`（隐藏）：跳过**只在群聊里有意义**（单聊的一轮就是
  "玩家问一句、NPC 答一句"，没有"让 NPC 自己接"这回事），而预制件不知道当前是不是群聊。
  默认隐藏 ⇒ 即使 C# 的显隐逻辑一次都没跑（节点缺失/装配失败/旧 DLL），单聊窗看起来也和
  今天一模一样 —— 宁可少一个按钮，也不要多一个"点了没反应"的按钮
  （与 `InviteBtn`/`KickBtn`/`UIContactAi` 的 NewGroupBtn 同一条 fail-safe 约定）。
  代价：C# 必须**每次开窗都显式设置**它的显隐，不能指望出厂默认。

> 文案为什么是「跳过」：用户原话。语义 = 这一轮玩家不置一词、让 NPC 按轮转顺序自己接。

为什么要脚本而不是手改 YAML：新增一个按钮 = 9 个对象（GO + RectTransform + CanvasRenderer +
Image + Button + 子 GO + 子 RT + 子 CR + 子 Text）、fileID 全部要新分配、父子引用要成对挂好
（**RectTransform 的 m_Children / m_Father** + GameObject 的 m_Component + `m_RootOrder`），
手改极易漏一处而**静默**变成"按钮不出现"或"点了没反应"。本脚本复用
`prefab_patch_contact_groups.py` 的**文本克隆**手术台：把 `SendBtn` 子树整段抄一份、只把旧
fileID 全局替换成新号（单趟正则、长号优先），再覆盖少数几个字段（名字/锚点/几何/文案/显隐/
父子/根序）—— 字段集合与 GUID 引用因此与原件逐字相同。

用法:
    python3 scripts/dev/prefab_patch_chat_skip_button.py \\
        --in  "<AB工程>/Assets/Resources/UI/UIChatAi.prefab" \\
        --out "<同上>" --allow-protected-out
    --dry-run 只算不写；原地改默认先写 .bak_chatskipbtn 备份（**只在备份不存在时**）。
    `--in/--out` 指向真实游戏工程时**默认拒写**（防手滑），必须显式加 --allow-protected-out。

幂等:
    以「`BG/Input` 有没有名为 SkipBtn 的子节点」为分支开关 ——
      没有 → 插入分支：克隆 SendBtn + 新 fileID + 改锚点/文案/显隐 + 挂父子；
      已有 → 校验分支：只核对字段（锚点/几何/文案/父子/ID 唯一性），一个字节都不改。
    重复跑第二次，对象数与字节数完全不变。

★ 序号段：本文件已用 880/882/883/885（气泡）、886（通讯录）、887（顶栏群按钮）。
  本条补丁占 **888**。段里只要出现任何一个号就报错 —— 两个补丁共用基址正是
  `prefab_patch_contact_groups` 踩过的那次撞号事故。
"""
import argparse
import io
import os
import sys

# 复用同目录的手术台（`Prefab` / 克隆 / 文案转义 / 备份 / 几何实算 / 按钮判据）。
# 插 sys.path 是为了"被测试 import"时也能找到 —— 直接 `python3 scripts/dev/xxx.py` 时
# sys.path[0] 本来就是本目录，这里只是把另一条路径补齐。
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from prefab_patch_contact_groups import (          # noqa: E402
    Prefab, clone_subtree, find_text_comp, yaml_str, ensure_backup,
    rect_in_parent, RE_COMPONENT_REF, RE_GO_REF, _fix_console,
)
from prefab_patch_chat_group_buttons import (       # noqa: E402
    _has_button, _comp_signature,
)


# ══════════════════════════════════════════════════════════════════
# 版式常量（必须与 AbChatPanel.CollectRefs 的路径契约一致）
# ══════════════════════════════════════════════════════════════════

SKIP_BTN = "SkipBtn"
SKIP_TEXT = "跳过"

#: 克隆源 = **正下方那颗发送键**（字号/颜色/Image 精灵/Button 过渡与它逐字相同）。
CLONE_SRC = "SendBtn"
#: 父节点 = 输入面板（不是 BG 直下 —— 那样它不会跟着输入框走）。
PARENT = "Input"

BTN_ANCHOR = "{x: 1, y: 0}"
BTN_PIVOT = "{x: 0.5, y: 0.5}"
BTN_SIZE = "{x: 40, y: 30}"

#: SendBtn 的既有几何（**从预制件实测**，见文件头）。跳过键 = 同 x、同尺寸，y 抬到它上方 8px。
SEND_POS_X, SEND_POS_Y = -20.0, 15.0
SEND_W, SEND_H = 40.0, 30.0
#: 与下方发送键之间的间距（顶栏那一排彼此也是 8px，两处沿用同一节奏）。
GAP = 8.0
#: 由上面几项**算出**（而不是直接写 53）：几何关系一旦改动，这里跟着动、下面的
#: `assert` 又会挡住"算出来的和写进预制件的不一致"。
SKIP_POS_X = SEND_POS_X
SKIP_POS_Y = SEND_POS_Y + SEND_H / 2.0 + GAP + SEND_H / 2.0          # = 53.0
SKIP_POS = "{x: %g, y: %g}" % (SKIP_POS_X, SKIP_POS_Y)

#: `BG/Input` 的尺寸（RectTransform 实算用；改版式必须同步改这里，否则几何复核会红）。
INPUT_W, INPUT_H = 1200.0, 110.0
#: 输入框文字带（`ChatInput/Placeholder` 的矩形，**相对 Input**）：跳过键不许压到它。
#: 由 `rect_in_parent(placeholder, 1200, 110)` 实测：x 8.1..788.1、y 32..102。
TEXT_BAND = (8.1, 788.1, 32.0, 102.0)

#: 新 fileID 段。UIChatAi 的 88x 段已被 880/882/883/885（气泡）与 887（顶栏群按钮）占用，
#: 本条补丁**显式占 888 段并先验空**（见 `_alloc_for`）。
NEW_ID_BASE = 8880000000000000000

BACKUP_SUFFIX = ".bak_chatskipbtn"


def log(*a):
    print("[chat-skip-btn]", *a)


def rects_overlap(a, b):
    """两个矩形 `((x0,x1),(y0,y1))` 是否**相交**（边界相接不算）。

    抽成模块级函数是为了它能被单测钉住：内联在 `verify()` 里的四则布尔值没法证明
    自己写对了 —— 而它守的是"按钮盖住玩家正在打的字"这件只在实机上看得见的事。
    """
    (ax0, ax1), (ay0, ay1) = a
    (bx0, bx1), (by0, by1) = b
    return (ax0 < bx1) and (ax1 > bx0) and (ay0 < by1) and (ay1 > by0)


class _Alloc(object):
    """号段分配器（与同族脚本同款；基址不同）。"""

    def __init__(self, base):
        self.n = base

    def take(self, count, pf):
        out = []
        for _ in range(count):
            self.n += 1
            fid = str(self.n)
            assert fid not in pf.doc, "新 fileID 撞号: " + fid
            out.append(fid)
        return out


def _alloc_for(pf):
    """号段分配器：**严格占用 888 段**，并且先证明这一段是空的。

    ★ 为什么不用"按已有 id 的高水位起算"：本文件的根节点 `UIChatAi` 的 id 是
    9188755676859534513，比任何 `88x` 补丁号都大 ⇒ 新高水位会跑到 9.18e18，
    新节点拿到既不可读、也说不清出处的号。显式占段更严：段里只要出现任何一个号就报错。
    """
    span = [int(x) for x in pf.all_ids()
            if str(x).isdigit() and NEW_ID_BASE <= int(x) < NEW_ID_BASE + 1000]
    assert not span, ("888 号段已被占用 —— 要么本补丁已经打过（那应走校验分支），"
                      "要么有人手改过，要么有人复用了这段基址。占用到的号：%s" % sorted(span))
    return _Alloc(NEW_ID_BASE)


def _stage_button(pf, do_log=True):
    """唯一阶段：往 `BG/Input` 追加 SkipBtn（在 SendBtn 上方的同一列）。"""
    inp = pf.find_go(PARENT)
    assert inp, "预制件里找不到 %s（路径契约变了？）" % PARENT
    inp_rt = pf.rt_of_go(inp)
    src = pf.find_go(CLONE_SRC)
    assert src, "找不到克隆源 %s" % CLONE_SRC
    assert pf.father_of(pf.rt_of_go(src)) == inp_rt, \
        "%s 不再是 %s 的直接子节点（版式变了？那条几何假设要重新核）" % (CLONE_SRC, PARENT)

    alloc = _alloc_for(pf)
    kids = pf.children(inp_rt)

    go, rt, _ = clone_subtree(pf, src, alloc)
    pf.set_field(go, "m_Name", SKIP_BTN)
    pf.set_field(go, "m_IsActive", "0")        # ★ fail-safe：出厂隐藏，由 C# 在群聊模式点亮
    pf.set_field(rt, "m_AnchorMin", BTN_ANCHOR)
    pf.set_field(rt, "m_AnchorMax", BTN_ANCHOR)
    pf.set_field(rt, "m_AnchoredPosition", SKIP_POS)
    pf.set_field(rt, "m_SizeDelta", BTN_SIZE)
    pf.set_field(rt, "m_Pivot", BTN_PIVOT)
    label = pf.child_go_named(go, "Text")
    assert label, "%s 克隆后找不到 Text 子节点" % SKIP_BTN
    txt = find_text_comp(pf, label)
    assert txt, "%s/Text 上找不到 Text 组件" % SKIP_BTN
    pf.set_field(txt, "m_Text", yaml_str(SKIP_TEXT))

    pf.set_father(rt, inp_rt)
    pf.set_field(rt, "m_RootOrder", str(len(kids)))
    assert rt not in kids
    pf.set_children(inp_rt, kids + [rt])

    if do_log:
        log("追加 %s(pos %s) 于 %s 直下，右锚定 40×30、默认隐藏"
            % (SKIP_BTN, SKIP_POS, PARENT))
        log("输入区右下按钮列（自下而上）：发送 → %.0fpx → 跳过" % GAP)


def patch(text, do_log=True):
    """打补丁。已打过就走校验分支（一字不改）。"""
    pf = Prefab(text)
    # 最低成本的安全网：拆分/拼回必须逐字节还原（不然这次手术会顺手改坏别处）
    assert pf.text() == text, "Prefab 拆分-拼回不一致（脚本自身的问题，先别改真文件）"
    before = len(pf.order)

    if pf.find_go(SKIP_BTN):
        if do_log:
            log("已存在 %s → 跳过插入，只做校验" % SKIP_BTN)
    else:
        _stage_button(pf, do_log)

    out = pf.text()
    if do_log and len(pf.order) != before:
        log("对象数 %d → %d（+%d）" % (before, len(pf.order), len(pf.order) - before))
    verify(out)
    return out


def verify(text):
    """独立复核（重新解析产物，不信任 patch 的中间状态）。失败即抛 AssertionError。"""
    pf = Prefab(text)
    inp = pf.find_go(PARENT)
    assert inp, "复核失败：%s 不见了" % PARENT
    inp_rt = pf.rt_of_go(inp)
    send = pf.find_go(CLONE_SRC)
    assert send, "复核失败：%s 不见了（几何基准）" % CLONE_SRC

    go = pf.find_go(SKIP_BTN)
    assert go, "复核失败：%s 不存在" % SKIP_BTN
    rt = pf.rt_of_go(go)
    assert pf.father_of(rt) == inp_rt, "复核失败：%s 的父节点不是 %s" % (SKIP_BTN, PARENT)
    assert pf.children(inp_rt).count(rt) == 1, \
        "复核失败：%s.m_Children 里 %s 应恰好一次" % (PARENT, SKIP_BTN)
    assert pf.field(rt, "m_AnchorMin") == BTN_ANCHOR, pf.field(rt, "m_AnchorMin")
    assert pf.field(rt, "m_AnchorMax") == BTN_ANCHOR, pf.field(rt, "m_AnchorMax")
    assert pf.field(rt, "m_AnchoredPosition") == SKIP_POS, \
        "复核失败：pos 应为 %s，实际 %s" % (SKIP_POS, pf.field(rt, "m_AnchoredPosition"))
    assert pf.field(rt, "m_SizeDelta") == BTN_SIZE, pf.field(rt, "m_SizeDelta")
    assert pf.field(rt, "m_Pivot") == BTN_PIVOT, \
        "复核失败：pivot 必须是 %s（与发送键同基准），否则几何整体偏移" % BTN_PIVOT
    assert abs(SKIP_POS_Y - 53.0) < 1e-9, \
        "复核失败：SKIP_POS_Y 算出来是 %.3f，与预制件里写的 53 不符" % SKIP_POS_Y
    # ★ 默认隐藏：出厂可见 = 单聊窗里冒出一个点了没反应的按钮
    assert pf.field(go, "m_IsActive") == "0", \
        "复核失败：%s 默认应隐藏（由 C# 在群聊模式点亮）" % SKIP_BTN
    # 文案
    label = pf.child_go_named(go, "Text")
    assert label, "复核失败：%s 下没有 Text 子节点" % SKIP_BTN
    got = pf.field(find_text_comp(pf, label), "m_Text")
    assert got == yaml_str(SKIP_TEXT), "文案应为 %s，实际 %s" % (yaml_str(SKIP_TEXT), got)
    # 组件完整性：1 RT + 1 CR + 2 MB（Image + Button），且都指向自己
    comps = RE_COMPONENT_REF.findall(pf.doc[go])
    kinds = [pf.cls(c) for c in comps]
    assert kinds.count("224") == 1, "%s 应恰好一个 RectTransform" % SKIP_BTN
    assert kinds.count("222") == 1, "%s 应恰好一个 CanvasRenderer" % SKIP_BTN
    assert kinds.count("114") == 2, "%s 应恰好两个 MonoBehaviour（Image + Button）" % SKIP_BTN
    for c in comps:
        assert RE_GO_REF.search(pf.doc[c]).group(1) == go, \
            "%s 的组件 m_GameObject 没指向自己" % SKIP_BTN
    assert _has_button(pf, go), "%s 上没有 Button 组件（点了不会有反应）" % SKIP_BTN
    # 克隆保真：组件集合（class + 脚本 guid）必须与克隆源逐项相同
    assert _comp_signature(pf, go) == _comp_signature(pf, send), \
        "%s 的组件集合与 %s 不一致（克隆抄坏了？）" % (SKIP_BTN, CLONE_SRC)
    # m_RootOrder 必须与它在父节点 m_Children 里的位次一致（本文件既有节点全部一致）
    idx = pf.children(inp_rt).index(rt)
    assert pf.field(rt, "m_RootOrder") == str(idx), \
        "复核失败：m_RootOrder=%s 与它在 m_Children 的位次 %d 不符" % (pf.field(rt, "m_RootOrder"), idx)

    ids = list(pf.order)
    assert len(ids) == len(set(ids)), "fileID 有重复"

    # ---- 数值几何（按 Unity 公式实算，不只比锚点字符串）----
    TOL = 1e-3
    (sx0, sx1), (sy0, sy1) = rect_in_parent(pf, pf.rt_of_go(send), INPUT_W, INPUT_H)
    (kx0, kx1), (ky0, ky1) = rect_in_parent(pf, rt, INPUT_W, INPUT_H)

    assert abs((kx1 - kx0) - SEND_W) < TOL, \
        "跳过键宽应为 %.0f，实际 %.4f" % (SEND_W, kx1 - kx0)
    assert abs((ky1 - ky0) - SEND_H) < TOL, \
        "跳过键高应为 %.0f，实际 %.4f" % (SEND_H, ky1 - ky0)
    # 同一列：x 区间必须逐字对齐（错开 1px 肉眼就是"歪的"）
    assert abs(kx0 - sx0) < TOL and abs(kx1 - sx1) < TOL, \
        "★ 跳过键与发送键不在同一列：跳过 x[%.1f,%.1f] vs 发送 x[%.1f,%.1f]" % (kx0, kx1, sx0, sx1)
    # 上下的间距恒为 GAP
    gap = ky0 - sy1
    assert abs(gap - GAP) < TOL, \
        "★ 跳过键与发送键的间距应为 %.0fpx，实际 %.4f（<=0 就是压住了，下面那个点不到）" % (GAP, gap)
    assert ky0 >= sy1 - TOL, "★ 跳过键压到发送键上了"
    # 都在输入面板内
    assert kx0 >= 0.0 and kx1 <= INPUT_W and ky0 >= 0.0 and ky1 <= INPUT_H, \
        "复核失败：跳过键超出输入面板：[%.1f,%.1f]×[%.1f,%.1f]" % (kx0, kx1, ky0, ky1)
    # ★ 不许压住玩家正在打的字：与 ChatInput 的文字带（实测矩形）不得相交
    bx0, bx1, by0, by1 = TEXT_BAND
    if rects_overlap(((kx0, kx1), (ky0, ky1)), ((bx0, bx1), (by0, by1))):
        raise AssertionError(
            "★ 跳过键压在输入文字带上了（x[%.1f,%.1f] y[%.1f,%.1f] vs 文字带 x[%.1f,%.1f] y[%.1f,%.1f]）"
            % (kx0, kx1, ky0, ky1, bx0, bx1, by0, by1))
    # 文字带的实测值与常量必须一致（常量漂了，上面那条判断就是假的）
    # ★ 这里必须逐层 `child_go_named`：`find_go` 是**按名字全表找第一个**，
    #   而本文件里有多个 `Placeholder`（`NpcInput/Placeholder` 在前），
    #   用名字找会拿错那一个 —— 且几何复核照样"通过"，只是基准换成了别人的矩形。
    chat_input = pf.child_go_named(inp, "ChatInput")
    assert chat_input, "找不到 %s/ChatInput" % PARENT
    ph = pf.child_go_named(chat_input, "Placeholder")
    assert ph, "找不到 %s/ChatInput/Placeholder（文字带的实测基准）" % PARENT
    (px0, px1), (py0, py1) = rect_in_parent(pf, pf.rt_of_go(ph), INPUT_W, INPUT_H)
    for got_v, want_v, nm in ((px0, bx0, "文字带左"), (px1, bx1, "文字带右"),
                              (py0, by0, "文字带下"), (py1, by1, "文字带上")):
        assert abs(got_v - want_v) < 0.2, \
            "%s 实测 %.1f 与常量 %.1f 不符（改版式要同步改 TEXT_BAND）" % (nm, got_v, want_v)
    return True


def main(argv=None):
    _fix_console()
    ap = argparse.ArgumentParser(description="UIChatAi.prefab 补群聊「跳过」按钮（幂等）")
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
        # 与同族脚本同一份保护名单（真源在 AB 打包工程里）
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
    if a.dry_run:
        log("== dry-run：未写盘（输出将 %d 字节）" % len(out.encode("utf-8")))
        return 0
    if inplace and not a.no_backup:
        bak = outp + BACKUP_SUFFIX
        if ensure_backup(outp, bak):
            log("== 备份 %s" % bak)
        else:
            log("== 备份已存在，不覆盖（保留最早的原始件）：%s" % bak)
    with io.open(outp, "w", encoding="utf-8", newline="") as f:
        f.write(out)
    log("== 输出 %s (%d 字节)" % (outp, len(out.encode("utf-8"))))
    log("== 自校验通过（锚点/几何/文案/父子/组件/fileID 唯一/不压文字带）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
