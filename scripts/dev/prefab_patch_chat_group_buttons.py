# -*- coding: utf-8 -*-
"""UIChatAi.prefab —— 对话窗顶栏补「拉人 / 踢人」两个按钮（P3 第 4 步，幂等 / 可重跑）。

改造前（BG 直下 12 个节点，顶栏右侧四个按钮从右往左排）：

    BG (1200×800)
    ├─ NpcInput      a(0,1)  piv(0,0.5)  pos(0,-20)      size(190,40)   左对齐名牌
    ├─ DeleteBtn     a(1,1)  piv(1,0.5)  pos(-105.8,-20) size(64,40)     🗑
    ├─ ConfigBtn     a(1,1)  piv(1,0.5)  pos(-39.8,-20)  size(60,40)     ⚙
    ├─ CloseBtn      a(1,1)  piv(1,0.5)  pos(0,-20)      size(40,40)     ✕
    ├─ …
    ├─ DeleteBar     a(0,0)..a(1,0)  选择模式操作条（默认隐藏）
    └─ CompactBtn    a(1,1)  piv(1,0.5)  pos(-177.8,-20) size(64,40)     「压缩」

改造后（**只追加**两个节点，既有节点一个字节都不动）：

    ├─ InviteBtn  ★ a(1,1) piv(1,0.5) pos(-249.8,-20) size(64,40) 「拉人」 默认隐藏
    ├─ KickBtn    ★ a(1,1) piv(1,0.5) pos(-321.8,-20) size(64,40) 「踢人」 默认隐藏
    └─ CompactBtn   a(1,1) piv(1,0.5) pos(-177.8,-20) size(64,40) 「压缩」

    右往左：✕(0) ⚙(-39.8) 🗑(-105.8) 压缩(-177.8) 拉人(-249.8) 踢人(-321.8)
    每个按钮宽 64（✕/⚙ 是 40/60），右边缘间距恒为 **8px** —— 这是既有四个按钮的既有间距，
    新按钮沿用同一节奏，不另立一套。

★ 为什么沿用「右锚点 + 负 x + piv(1,0.5)」而不是左锚点往后排：
   这一排**全部**是右锚定的，右侧还有 ✕/⚙/🗑 三个固定装置。改成左锚定就等于把
   "窗口变宽时按钮往哪边跑"的答案改了（它们会跟着左边跑，撞上 NpcInput 名牌）。
   沿用右锚点则天然跟随右边缘。

★ 为什么默认 `m_IsActive=0`（隐藏）：这两个动作**只在群聊里有意义**，而预制件不知道
   当前是不是群聊。默认隐藏 ⇒ 即使 C# 的显隐逻辑一次都没跑（节点缺失/装配失败），
   单聊窗看起来也和今天一模一样 —— 宁可少两个按钮，也不要多两个点了没反应的按钮
   （与 `UIContactAi` 的 NewGroupBtn/DelGroupBtn 同一条 fail-safe 约定）。
   代价：C# 必须**每次开窗都显式设置**两者的显隐，不能指望出厂默认。

> 文案为什么是「拉人 / 踢人」而不是「邀请 / 移出」：与用户需求原话一致（"拉人/踢人"），
>   且两字按钮在 64px 宽里排得开。

为什么要脚本而不是手改 YAML：新增一个按钮 = 9 个对象（GO + RectTransform + CanvasRenderer +
Image + Button + 子 GO + 子 RT + 子 CR + 子 Text）、fileID 全部要新分配、父子引用要成对挂好
（**RectTransform 的 m_Children / m_Father** + GameObject 的 m_Component + `m_RootOrder`），
手改极易漏一处而**静默**变成"按钮不出现"或"点了没反应"。本脚本复用
`prefab_patch_contact_groups.py` 的**文本克隆**手术台：把既有按钮子树整段抄一份、只把旧 fileID
全局替换成新号（单趟正则、长号优先），再覆盖少数几个字段（名字/锚点/文案/显隐/父子/根序）——
字段集合与 GUID 引用因此与原件逐字相同，不会出现"这个按钮的字比别的大一号"。

用法:
    python3 scripts/dev/prefab_patch_chat_group_buttons.py \\
        --in  "<AB工程>/Assets/Resources/UI/UIChatAi.prefab" \\
        --out "<同上>" --allow-protected-out
    --dry-run 只算不写；原地改默认先写 .bak_chatgroupbtns 备份（**只在备份不存在时**）。
    `--in/--out` 指向真实游戏工程时**默认拒写**（防手滑），必须显式加 --allow-protected-out。

幂等:
    以「BG 直下有没有名为 InviteBtn 的子节点」为分支开关 ——
      没有 → 插入分支：克隆 CompactBtn + 新 fileID + 改锚点/文案/显隐 + 挂父子；
      已有 → 校验分支：只核对字段（锚点/几何/文案/父子/ID 唯一性），一个字节都不改。
    重复跑第二次，对象数与字节数完全不变。
"""
import argparse
import io
import os
import re
import sys

# 复用同目录的手术台（`Prefab` / 克隆 / 文案转义 / 备份 / 几何实算）。
# 插 sys.path 是为了"被测试 import"时也能找到 —— 直接 `python3 scripts/dev/xxx.py` 时
# sys.path[0] 本来就是本目录，这里只是把另一条路径补齐。
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from prefab_patch_contact_groups import (          # noqa: E402
    Prefab, clone_subtree, find_text_comp, yaml_str, ensure_backup,
    rect_in_parent, RE_COMPONENT_REF, RE_GO_REF, _fix_console,
)


# ══════════════════════════════════════════════════════════════════
# 版式常量（必须与 AbChatPanel.CollectRefs 的路径契约一致）
# ══════════════════════════════════════════════════════════════════

INVITE_BTN = "InviteBtn"
INVITE_TEXT = "拉人"
KICK_BTN = "KickBtn"
KICK_TEXT = "踢人"

#: 克隆源：**与压缩键同源**，所以新按钮的组件集合（Image + Button + Text 的字号/字体/
#: 对齐/RichText）与它逐字相同 —— 顶栏不会出现"这个按钮的字比别的粗/大一号"。
CLONE_SRC = "CompactBtn"

BTN_ANCHOR = "{x: 1, y: 1}"
BTN_PIVOT = "{x: 1, y: 0.5}"
BTN_SIZE = "{x: 64, y: 40}"
INVITE_POS = "{x: -249.8, y: -20}"
KICK_POS = "{x: -321.8, y: -20}"
#: 与压缩键之间的既定间距（既有四个按钮彼此都是 8px，见文件头）
GAP = 8.0
#: 压缩键的右边缘锚点（x 的绝对值）：-177.8 - 64 = -241.8 ⇒ 拉人 -249.8 正好差 8px。
COMPACT_X = -177.8
BTN_W = 64.0
#: BG 的尺寸（RectTransform 实算用；改版式必须同步改这里，否则几何复核会红）
BG_W, BG_H = 1200.0, 800.0
#: NpcInput（左侧名牌）的矩形：a(0,1) piv(0,0.5) pos(0,-20) size(190,40)
NPC_INPUT_W = 190.0

#: 新 fileID 段。UIChatAi.prefab 已用到 880/882/883/885 四个 `88x0000000000000xxx` 段
#: （已核对：**没有任何既有 id 以 887 开头**）。与 UIContactAi 的 886 段分开，
#: 这样看到 887 就知道是"对话窗按钮"，不用回去翻是哪个文件的补丁。
NEW_ID_BASE = 8870000000000000000

BACKUP_SUFFIX = ".bak_chatgroupbtns"


def log(*a):
    print("[chat-group-btns]", *a)


class _Alloc(object):
    """号段分配器（与 contact 脚本同款；基址不同）。"""

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
    """号段分配器：**严格占用 887 段**，并且先证明这一段是空的。

    ★ 为什么不用 `prefab_patch_contact_groups._alloc_for` 那套"按已有 id 的高水位起算"：
    那个写法在本文件会**跑到 9.18e18**（根节点 `UIChatAi` 的 id 是 9188755676859534513，
    比任何 `88x` 补丁号都大），于是新节点拿到 `…534514` 这种既不可读、也说不清出处的号。
    安全性没问题（`take` 的撞号断言仍在），但"看到 887 就知道是哪个补丁"这条可读性约定
    就没了。这里改成**显式占段 + 先验空**，比高水位更严：段里只要出现任何一个号就报错。

    ⚠ 将来若再给本文件加阶段，**换一段**（888…），不要复用 887 —— 两个阶段共用基址正是
    `prefab_patch_contact_groups` 踩过的那次撞号（`AssertionError: 新 fileID 撞号`）。
    """
    span = [int(x) for x in pf.all_ids()
            if str(x).isdigit() and NEW_ID_BASE <= int(x) < NEW_ID_BASE + 1000]
    assert not span, ("887 号段已被占用 —— 要么本补丁已经打过（那应走校验分支，不该走到这里），"
                      "要么有人手改过。占用到的号：%s" % sorted(span))
    return _Alloc(NEW_ID_BASE)


def _make_button(pf, alloc, name, text, pos):
    """克隆 CompactBtn → 改名 / 改锚点几何 / 改文案 / 默认隐藏。返回 (go, rt)。"""
    src = pf.find_go(CLONE_SRC)
    assert src, "找不到克隆源 %s（顶栏结构变了？）" % CLONE_SRC
    go, rt, _ = clone_subtree(pf, src, alloc)
    pf.set_field(go, "m_Name", name)
    pf.set_field(go, "m_IsActive", "0")        # ★ fail-safe：出厂隐藏，由 C# 在群聊模式点亮
    pf.set_field(rt, "m_AnchorMin", BTN_ANCHOR)
    pf.set_field(rt, "m_AnchorMax", BTN_ANCHOR)
    pf.set_field(rt, "m_AnchoredPosition", pos)
    pf.set_field(rt, "m_SizeDelta", BTN_SIZE)
    pf.set_field(rt, "m_Pivot", BTN_PIVOT)
    # 文案子节点：克隆源自带的那个 Text
    label = pf.child_go_named(go, "Text")
    assert label, "%s 克隆后找不到 Text 子节点" % name
    txt = find_text_comp(pf, label)
    assert txt, "%s/Text 上找不到 Text 组件" % name
    pf.set_field(txt, "m_Text", yaml_str(text))
    return go, rt


def patch(text, do_log=True):
    """打补丁。已打过就走校验分支（一字不改）。"""
    pf = Prefab(text)
    # 最低成本的安全网：拆分/拼回必须逐字节还原（不然这次手术会顺手改坏别处）
    assert pf.text() == text, "Prefab 拆分-拼回不一致（脚本自身的问题，先别改真文件）"
    before = len(pf.order)

    if pf.find_go(INVITE_BTN):
        if do_log:
            log("已存在 %s → 跳过插入，只做校验" % INVITE_BTN)
    else:
        _stage_buttons(pf, do_log)

    out = pf.text()
    if do_log and len(pf.order) != before:
        log("对象数 %d → %d（+%d）" % (before, len(pf.order), len(pf.order) - before))
    verify(out)
    return out


def _stage_buttons(pf, do_log=True):
    """唯一阶段：往 BG 追加 InviteBtn / KickBtn（顺序 = 拉人、踢人，都在压缩键左侧）。"""
    bg = pf.find_go("BG")
    assert bg, "预制件里找不到 BG（路径契约变了？）"
    bg_rt = pf.rt_of_go(bg)
    compact = pf.find_go(CLONE_SRC)
    assert compact, "找不到 %s（新按钮的克隆源）" % CLONE_SRC

    alloc = _alloc_for(pf)
    kids = pf.children(bg_rt)
    base_order = len(kids)

    invite, invite_rt = _make_button(pf, alloc, INVITE_BTN, INVITE_TEXT, INVITE_POS)
    pf.set_father(invite_rt, bg_rt)
    pf.set_field(invite_rt, "m_RootOrder", str(base_order))

    kick, kick_rt = _make_button(pf, alloc, KICK_BTN, KICK_TEXT, KICK_POS)
    pf.set_father(kick_rt, bg_rt)
    pf.set_field(kick_rt, "m_RootOrder", str(base_order + 1))

    assert invite_rt not in kids and kick_rt not in kids
    pf.set_children(bg_rt, kids + [invite_rt, kick_rt])

    if do_log:
        log("追加 %s(pos %s) 与 %s(pos %s)，均为右锚定 64×40、默认隐藏"
            % (INVITE_BTN, INVITE_POS, KICK_BTN, KICK_POS))
        log("顶栏右起：✕ ⚙ 🗑 压缩 拉人 踢人（间距各 8px）")


def _nums(s):
    return [float(x) for x in re.findall(r"[-+]?[\d.]+(?:[eE][-+]?\d+)?", s or "")]


def _has_button(pf, go_fid):
    """这个 GO 上有没有 uGUI Button。

    ★ 判据不能写 `pf.field(c, "m_OnClick")` —— `Prefab.field` 的正则是
    `^  key: (.*)$`（冒号后**必须**有空格），而 Button 序列化出来的那行是
    `  m_OnClick:`（**没有值、也没有尾空格**）⇒ 恒返回 None，于是这个检查会
    在按钮明明好好的时候报"没有 Button"。第一版就是这么红的。
    改成行锚定、不要求有值。
    """
    for c in RE_COMPONENT_REF.findall(pf.doc[go_fid]):
        if pf.cls(c) == "114" and re.search(r"(?m)^  m_OnClick:\s*$", pf.doc[c]):
            return True
    return False


def _comp_signature(pf, go_fid):
    """组件指纹：[(class, 脚本 guid)]。

    克隆保真的**强断言**：新按钮与克隆源的组件集合（含每个 MonoBehaviour 挂的是哪个
    脚本 guid）必须逐项相同 —— 比"有没有 Button"更严，顺手证明 Image/Button 两个
    脚本 guid 也抄对了（guid 抄错的表现是运行时 `AddComponent` 抛异常或按钮无外观）。
    """
    out = []
    for c in RE_COMPONENT_REF.findall(pf.doc[go_fid]):
        m = re.search(r"guid: ([0-9a-f]{32})", pf.doc[c])
        out.append((pf.cls(c), m.group(1) if m else ""))
    return sorted(out)


def verify(text):
    """独立复核（重新解析产物，不信任 patch 的中间状态）。失败即抛 AssertionError。"""
    pf = Prefab(text)
    bg = pf.find_go("BG")
    assert bg, "复核失败：BG 不见了"
    bg_rt = pf.rt_of_go(bg)

    for name, want_pos, want_text in ((INVITE_BTN, INVITE_POS, INVITE_TEXT),
                                      (KICK_BTN, KICK_POS, KICK_TEXT)):
        go = pf.find_go(name)
        assert go, "复核失败：%s 不存在" % name
        rt = pf.rt_of_go(go)
        assert pf.father_of(rt) == bg_rt, "复核失败：%s 的父节点不是 BG" % name
        assert pf.children(bg_rt).count(rt) == 1, \
            "复核失败：BG.m_Children 里 %s 应恰好一次" % name
        assert pf.field(rt, "m_AnchorMin") == BTN_ANCHOR, name
        assert pf.field(rt, "m_AnchorMax") == BTN_ANCHOR, name
        assert pf.field(rt, "m_AnchoredPosition") == want_pos, \
            "复核失败：%s 的 pos 应为 %s，实际 %s" % (name, want_pos, pf.field(rt, "m_AnchoredPosition"))
        assert pf.field(rt, "m_SizeDelta") == BTN_SIZE, name
        assert pf.field(rt, "m_Pivot") == BTN_PIVOT, \
            "复核失败：%s 的 pivot 必须是 %s（右基准），否则几何整体偏 64px" % (name, BTN_PIVOT)
        # ★ 默认隐藏：出厂可见 = 单聊窗里冒出两个点了没反应的按钮
        assert pf.field(go, "m_IsActive") == "0", \
            "复核失败：%s 默认应隐藏（由 C# 在群聊模式点亮）" % name
        # 文案
        label = pf.child_go_named(go, "Text")
        assert label, "复核失败：%s 下没有 Text 子节点" % name
        got = pf.field(find_text_comp(pf, label), "m_Text")
        assert got == yaml_str(want_text), "%s 文案应为 %s，实际 %s" % (name, yaml_str(want_text), got)
        # 组件完整性：1 RT + 1 CR + 2 MB（Image + Button），且都指向自己
        comps = RE_COMPONENT_REF.findall(pf.doc[go])
        kinds = [pf.cls(c) for c in comps]
        assert kinds.count("224") == 1, "%s 应恰好一个 RectTransform" % name
        assert kinds.count("222") == 1, "%s 应恰好一个 CanvasRenderer" % name
        assert kinds.count("114") == 2, "%s 应恰好两个 MonoBehaviour（Image + Button）" % name
        for c in comps:
            assert RE_GO_REF.search(pf.doc[c]).group(1) == go, "%s 的组件 m_GameObject 没指向自己" % name
        assert _has_button(pf, go), "%s 上没有 Button 组件（点了不会有反应）" % name
        # 克隆保真：组件集合（class + 脚本 guid）必须与克隆源逐项相同
        assert _comp_signature(pf, go) == _comp_signature(pf, pf.find_go(CLONE_SRC)), \
            "%s 的组件集合与 %s 不一致（克隆抄坏了？）" % (name, CLONE_SRC)
        # m_RootOrder 必须与它在 BG.m_Children 里的位次一致（本文件既有节点全部一致）
        idx = pf.children(bg_rt).index(rt)
        assert pf.field(rt, "m_RootOrder") == str(idx), \
            "复核失败：%s 的 m_RootOrder=%s 与它在 m_Children 的位次 %d 不符" \
            % (name, pf.field(rt, "m_RootOrder"), idx)

    ids = list(pf.order)
    assert len(ids) == len(set(ids)), "fileID 有重复"

    # ---- 数值几何（按 Unity 公式实算，不只比锚点字符串）----
    TOL = 1e-4
    compact = pf.find_go(CLONE_SRC)
    assert compact, "复核失败：%s 不见了（新按钮的克隆源）" % CLONE_SRC
    (c0, c1), _ = rect_in_parent(pf, pf.rt_of_go(compact), BG_W, BG_H)
    (i0, i1), _ = rect_in_parent(pf, pf.rt_of_go(pf.find_go(INVITE_BTN)), BG_W, BG_H)
    (k0, k1), _ = rect_in_parent(pf, pf.rt_of_go(pf.find_go(KICK_BTN)), BG_W, BG_H)

    for nm, (a, b) in (("InviteBtn", (i0, i1)), ("KickBtn", (k0, k1))):
        assert abs((b - a) - BTN_W) < TOL, "%s 宽应为 %.0f，实际 %.5f" % (nm, BTN_W, b - a)
    # 与压缩键同样的右边缘基准 ⇒ 间距恒为 GAP
    # （CompactBtn 的 x 是**右边缘**锚点，所以它的左边缘 = BG_W + COMPACT_X - BTN_W）
    compact_left = BG_W + COMPACT_X - BTN_W
    assert abs(compact_left - (c1 - BTN_W)) < 1e-3, \
        "几何前置不成立：BG_W(%.0f)+COMPACT_X(%.1f) 与实测的压缩键右边缘 %.1f 对不上" \
        % (BG_W, COMPACT_X, c1)
    gap1 = compact_left - i1
    gap2 = i0 - k1
    assert abs(gap1 - GAP) < 1e-3, \
        "★ 拉人与压缩键的间距应为 %.0fpx，实际 %.4f（重叠=有一个永远点不到）" % (GAP, gap1)
    assert abs(gap2 - GAP) < 1e-3, \
        "★ 踢人与拉人的间距应为 %.0fpx，实际 %.4f（重叠=有一个永远点不到）" % (GAP, gap2)
    assert i1 <= compact_left + TOL, "★ 拉人压到压缩键上了：下面那个永远点不到"
    assert k1 <= i0 + TOL, "★ 踢人压到拉人上了：下面那个永远点不到"
    # 左边界：不许撞上左侧名牌 NpcInput（它左锚定、宽 190，右边缘在 BG 本地 x=190）
    assert k0 > NPC_INPUT_W, \
        "★ 踢人按钮（左边缘 %.1f）撞上了 NpcInput 名牌（右边缘 %.0f）" % (k0, NPC_INPUT_W)
    # 都在 BG 内
    assert k0 >= 0.0 and i1 <= BG_W, \
        "复核失败：新按钮超出 BG：[%.1f, %.1f]" % (k0, i1)
    # 纵向：与既有顶栏按钮同一行（y 与压缩键一致）
    _, (cy0, cy1) = rect_in_parent(pf, pf.rt_of_go(compact), BG_W, BG_H)
    _, (iy0, iy1) = rect_in_parent(pf, pf.rt_of_go(pf.find_go(INVITE_BTN)), BG_W, BG_H)
    _, (ky0, ky1) = rect_in_parent(pf, pf.rt_of_go(pf.find_go(KICK_BTN)), BG_W, BG_H)
    assert abs(iy0 - cy0) < TOL and abs(ky0 - cy0) < TOL, "新按钮与压缩键不在同一行"
    assert abs(iy1 - cy1) < TOL and abs(ky1 - cy1) < TOL, "新按钮与压缩键不在同一行"
    return True


def main(argv=None):
    _fix_console()
    ap = argparse.ArgumentParser(description="UIChatAi.prefab 补拉人/踢人按钮（幂等）")
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
    log("== 自校验通过（锚点/几何/文案/父子/组件/fileID 唯一）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
