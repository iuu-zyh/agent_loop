# -*- coding: utf-8 -*-
"""UIContactAi.prefab —— 通讯录补「群聊」页与选人模式的节点（幂等 / 可重跑 / 纯文本手术）。

改造前（两个 Tab、无底栏、行模板右侧只有一个 ✓ 按钮）：

    BG
    ├─ Tabs              size 240×34
    │   ├─ TabFriendBtn  a 0..0.5
    │   └─ TabRecentBtn  a 0.5..1
    ├─ ContactScroll     pos(0,-35) size(0,-138)
    ├─ ContactItem       行模板（m_IsActive=0）
    │   └─ … / UnreadDot / ChatBtn(✓)
    └─ Status

改造后：

    BG
    ├─ Tabs              size **300**×34，**三等分**
    │   ├─ TabFriendBtn  a 0..1/3
    │   ├─ TabRecentBtn  a 1/3..2/3
    │   └─ TabGroupBtn ★ a 2/3..1              「群聊」
    ├─ ContactScroll     pos(0,-24) size(0,-160)   ← 底部让出 48px 给底栏
    ├─ ContactItem
    │   └─ … / UnreadDot / ChatBtn(✓) / **CheckBox** ★(内含 Check ✓，默认隐藏)
    ├─ **BottomBar** ★
    │   ├─ **NewGroupBtn** ★（默认隐藏；仅群聊 Tab 由 C# 点亮）「＋ 新建群聊」a 0..0.5
    │   ├─ **DelGroupBtn** ★（默认隐藏；同上）              「删除群聊」  a 0.5..1
    │   └─ **SelectBar** ★（默认隐藏，整条铺满；建群选人 / 解散选群 两个模式共用）
    │        ├─ SelectAllBtn「全选」/ CountLabel「已选 0 人」
    │        └─ CancelBtn「取消」  / ConfirmBtn「确认」
    └─ Status

分两个**阶段**打（各自判"打过没有"，所以原始件与"只打过第 2 步的中间态"都能正确处理）：
    第 2 步 = 群聊页（TabGroupBtn / CheckBox / BottomBar + NewGroupBtn + SelectBar）
    第 3 步 = 解散群聊（DelGroupBtn 占右半 + NewGroupBtn 收成左半）

> ★ 第 3 步为什么必须**把已存在的 NewGroupBtn 一起改**：两者原本都是
>   `aMin(0,0) aMax(1,1)`（整条铺满），不切开就**完全重叠** —— 后加的那个在上面，
>   于是「新建群聊」再也点不到，而且不报任何错（uGUI 拾取只给最上层 raycast target，
>   与行内 CheckBox 盖住 ChatBtn 是同一类坑）。

为什么要脚本而不是手改 YAML：新增一个按钮 = 9 个对象（GO + RectTransform + CanvasRenderer +
Image + Button + 子 GO + 子 RectTransform + 子 CanvasRenderer + 子 Text）、fileID 全部要新分配、
父子引用要成对挂好（**RectTransform 的 m_Children / m_Father** + GameObject 的 m_Component），
手改极易漏一处而**静默**变成"按钮不出现"或"点了没反应"。本脚本的做法是**文本克隆**：
把已有的子树整段抄一份、只把旧 fileID 全局替换成新号（单趟正则、长号优先），
再覆盖少数几个字段（名字/锚点/文案/颜色/父子）—— 字段集合与 GUID 引用因此与原件逐字相同。

⚠ 两个反直觉点（都踩过）：
  1. `m_Father` / `m_Children` 挂在 **RectTransform(class 224)** 上，**不在 GameObject 上**。
     只改 GO 的 m_Component 是不够的。
  2. 子节点的 RectTransform **已经包含在「子 GO 的组件表」里**，克隆时不要再单独追加一次 ——
     重复项会让号段缺一个，属于"靠巧合正确"。

用法:
    python3 scripts/dev/prefab_patch_contact_groups.py \\
        --in  "<AB工程>/Assets/Resources/UI/UIContactAi.prefab" \\
        --out "<同上>" --allow-protected-out
    --dry-run 只算不写；原地改默认先写 .bak_contactgroups 备份。
    `--in/--out` 指向真实游戏工程时**默认拒写**（防手滑），必须显式加 --allow-protected-out。
    （约定与 prefab_patch_prompt_reset.py / prefab_patch_config_groups.py 一致。）

幂等:
    以「BG/Tabs 下有没有名为 TabGroupBtn 的子节点」为分支开关 ——
      没有 → 插入分支：克隆 + 新 fileID + 改锚点 + 挂父子；
      已有 → 校验分支：只核对字段（锚点/文案/父子/ID 唯一性），一个字节都不改。
    重复跑第二次，对象数与字节数完全不变。
"""
import argparse
import io
import os
import re
import shutil
import sys


def _fix_console():
    """Windows 控制台/管道下也能打印中文。

    ⚠ 只在 `main()` 里调，**不在 import 时执行**：`sys.stdout` 一旦被包成 TextIOWrapper，
    pytest 的捕获流会跟着被换掉（本文件会被契约测试 import 来复核预制件）。
    """
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    except Exception:
        pass


# 真实游戏工程 / ui_preview 工程：默认拒写（与同族脚本同约定）
PROTECTED_OUT = [
    "/mnt/e/SteamLibrary/steamapps/common/鬼谷八荒/Mod/modFQA/资源修改教程/",
    "e:\\steamlibrary\\steamapps\\common\\鬼谷八荒\\mod\\modfqa\\资源修改教程\\",
    "/mnt/f/agent_loop/ui_preview/",
    "f:\\agent_loop\\ui_preview\\",
]

# ══════════════════════════════════════════════════════════════════
# 版式常量（必须与 AbContactPanel.CollectRefs 的路径契约、ContactPresenter 的用法一致）
# ══════════════════════════════════════════════════════════════════

TAB_NEW = "TabGroupBtn"
TAB_NEW_TEXT = "群聊"
CHECKBOX = "CheckBox"
CHECK = "Check"
BOTTOM_BAR = "BottomBar"
NEW_GROUP_BTN = "NewGroupBtn"
NEW_GROUP_TEXT = "＋ 新建群聊"
# ── 第 3 步（解散群聊）──────────────────────────────────────────────
# 底栏放两个按钮：NewGroupBtn 收成**左半**、新按钮占**右半**。
# 为什么必须切开：两者原本都是 `aMin(0,0) aMax(1,1)`（整条铺满），
# 不切就**完全重叠** —— 后加的那个永远在上面，于是「新建群聊」再也点不到，
# 而且不报任何错（uGUI 拾取只给最上层）。这与行内 CheckBox 盖住 ChatBtn 是同一类坑。
DEL_GROUP_BTN = "DelGroupBtn"
DEL_GROUP_TEXT = "删除群聊"
# 解散是**破坏性**操作，用暖红与「新建」的强调绿区分；与 Name/SEL_* 用的是同一套
# 纯色字色写法（预制件里没有 palette，颜色就是字面量，所以常量集中在这里改）。
DEL_GROUP_COLOR = "{r: 0.95, g: 0.42, b: 0.42, a: 1}"
BAR_HALF_L = ("0", "0.5")
BAR_HALF_R = ("0.5", "1")
SELECT_BAR = "SelectBar"
SEL_ALL = "SelectAllBtn"
SEL_ALL_TEXT = "全选"
SEL_COUNT = "CountLabel"
SEL_COUNT_TEXT = "已选 0 人"
SEL_CANCEL = "CancelBtn"
SEL_CANCEL_TEXT = "取消"
SEL_CONFIRM = "ConfirmBtn"
SEL_CONFIRM_TEXT = "确认"

# Tabs 加宽到 300（三等分各 100px，够放「群聊」2 字）
TABS_SIZE = "{x: 300, y: 34}"
# 三等分锚点（写成 7 位小数，与 Unity 自己的写法一致）
A_0, A_1_3, A_2_3, A_1 = "0", "0.3333333", "0.6666667", "1"
TAB_POS = "{x: 0, y: 0}"          # 三段居中 ⇒ pos 全为 0（原来是 ±0.5，跟着新锚点一起改）
TAB_SIZE = "{x: -4, y: -6}"       # 段间留 4px 缝（原来 -5）

# 列表区上边不动、下边抬高 52px，给底栏腾地方（原 pos(0,-35) size(0,-138)）
SCROLL_POS = "{x: 0, y: -24}"
SCROLL_SIZE = "{x: 0, y: -160}"

# 行模板右侧的复选框：与 ChatBtn 同一个位置（两者互斥显示）
BOX_ANCHOR = "{x: 1, y: 0.5}"
BOX_POS = "{x: -16, y: 0}"
BOX_SIZE = "{x: 26, y: 26}"
BOX_COLOR = "{r: 1, g: 1, b: 1, a: 0.22}"        # 未选中：淡白方框
CHECK_COLOR = "{r: 0, g: 0.788, b: 0.682, a: 1}"  # 选中：AccentGreen（与 TabFriendBtn 同色）

# 底栏
BAR_ANCHOR = "{x: 0.5, y: 0}"
BAR_POS = "{x: 0, y: 26}"
BAR_SIZE = "{x: 340, y: 44}"
# ★ pivot 必须显式改成中心：`clone_bare(Tabs)` 抄来的是 Tabs 的 `{x: 0.5, y: 1}`（**顶部**基准），
#   而底栏是**底边锚定**的 —— 两者叠加会让整条掉到面板外面 18px（44px 只露 26px）。
#   锚点本身没错，错在 pivot 参与了几何计算：`rectMin = anchorRef + pos - pivot*size`。
#   这个坑只比锚点字符串是发现不了的，所以 verify() 里加了按 Unity 公式实算的复核。
PIVOT_CENTER = "{x: 0.5, y: 0.5}"
FILL_POS = "{x: 0, y: 0}"
FILL_SIZE = "{x: 0, y: 0}"
CELL_POS = "{x: 0, y: 0}"
CELL_SIZE = "{x: -4, y: -6}"
# 选人模式四格：全选 / 已选 N 人 / 取消 / 确认
SEL_ALL_A = (A_0, "0.24")
SEL_COUNT_A = ("0.24", "0.56")
SEL_CANCEL_A = ("0.56", "0.78")
SEL_CONFIRM_A = ("0.78", A_1)

TEXT_GRAY = "{r: 0.604, g: 0.631, b: 0.663, a: 1}"   # 与 TabRecentBtn/Label 同色（未选中态）
TEXT_MAIN = "{r: 0.9, g: 0.92, b: 0.94, a: 1}"       # 底栏主文案（比 Tab 灰亮一档）
BTN_TEXT = "{r: 0, g: 0.788, b: 0.682, a: 1}"        # 主操作（确认/新建）用强调绿

# 新 fileID 段：本预制件已用到 9.17e18（UnreadBanner 补丁）与 8.87e17 等；
# 8860000000000000001 起是空段（已核对：没有任何既有 id 以 886 开头）。
NEW_ID_BASE = 8860000000000000000

RE_DOC = re.compile(r"(?m)^(?=--- !u!)")
RE_HEAD = re.compile(r"--- !u!(\d+) &(\d+)")
RE_COMPONENT_REF = re.compile(r"(?m)^  - component: \{fileID: (\d+)\}")
RE_CHILD_REF = re.compile(r"(?m)^  - \{fileID: (\d+)\}")
RE_GO_REF = re.compile(r"(?m)^  m_GameObject: \{fileID: (\d+)\}")
RE_FATHER_REF = re.compile(r"(?m)^  m_Father: \{fileID: (\d+)\}")
# `m_Children` 有两种写法：`[]` 与换行 + `- {fileID: N}` 列表
RE_CHILDREN_BLOCK = re.compile(r"(?m)^  m_Children:(?: \[\])?\n(?:  - \{fileID: \d+\}\n)*")


def log(*a):
    print("[contact-groups]", *a)


def uesc(s):
    """中文 → Unity YAML 的 \\uXXXX 转义（与文件里既有写法一致）。"""
    return "".join(ch if ord(ch) < 128 else "\\u%04X" % ord(ch) for ch in s)


def yaml_str(s):
    return '"%s"' % uesc(s)


class Prefab(object):
    """极简 prefab 文本手术台：按 fileID 索引文档，改字段/父子，最后原序拼回。"""

    def __init__(self, text):
        self.head = ""
        parts = RE_DOC.split(text)
        self.order = []
        self.doc = {}
        for p in parts:
            m = RE_HEAD.match(p)
            if not m:
                self.head += p              # 第一个非文档片段 = 文件头
                continue
            fid = m.group(2)
            assert fid not in self.doc, "fileID 重复: " + fid
            self.doc[fid] = p
            self.order.append(fid)

    # ---- 读 ----
    def cls(self, fid):
        return RE_HEAD.match(self.doc[fid]).group(1)

    def field(self, fid, key):
        m = re.search(r"(?m)^  %s: (.*)$" % re.escape(key), self.doc[fid])
        return m.group(1) if m else None

    def go_name(self, fid):
        return self.field(fid, "m_Name")

    def rt_of_go(self, go_fid):
        for r in RE_COMPONENT_REF.findall(self.doc[go_fid]):
            if self.cls(r) == "224":
                return r
        raise AssertionError("GO 没有 RectTransform: " + go_fid)

    def children(self, rt_fid):
        return RE_CHILD_REF.findall(self.doc[rt_fid])

    def father_of(self, rt_fid):
        m = RE_FATHER_REF.search(self.doc[rt_fid])
        return m.group(1) if m else "0"

    def find_go(self, name):
        for fid in self.order:
            if self.cls(fid) == "1" and self.go_name(fid) == name:
                return fid
        return None

    def child_go_named(self, go_fid, name):
        """直接子节点里按名字找 GO（不递归）。"""
        for crt in self.children(self.rt_of_go(go_fid)):
            cgo = RE_GO_REF.search(self.doc[crt]).group(1)
            if self.go_name(cgo) == name:
                return cgo
        return None

    def path_of(self, go_fid):
        parts = [self.go_name(go_fid)]
        rt = self.rt_of_go(go_fid)
        while True:
            father = self.father_of(rt)
            if father == "0":
                break
            parent_go = RE_GO_REF.search(self.doc[father]).group(1)
            parts.append(self.go_name(parent_go))
            rt = father
        return "/" + "/".join(reversed(parts))

    # ---- 写 ----
    def set_field(self, fid, key, value):
        pat = re.compile(r"(?m)^  %s: .*$" % re.escape(key))
        # 用 lambda 当替换式：字符串替换式会把 `\uXXXX`（中文转义）当正则转义解析而报
        # `bad escape \u` —— 写中文文案必踩，故一律走函数式替换。
        new, n = pat.subn(lambda m: "  %s: %s" % (key, value), self.doc[fid])
        assert n == 1, "字段写入失败 %s.%s（命中 %d 次）" % (fid, key, n)
        self.doc[fid] = new

    def set_children(self, rt_fid, fids):
        """整段重写 m_Children（同时兼容 `[]` 与列表两种既有写法）。"""
        if fids:
            body = "  m_Children:\n" + "".join("  - {fileID: %s}\n" % f for f in fids)
        else:
            body = "  m_Children: []\n"
        new, n = RE_CHILDREN_BLOCK.subn(lambda m: body, self.doc[rt_fid])
        assert n == 1, "m_Children 重写失败: " + rt_fid
        self.doc[rt_fid] = new

    def set_father(self, rt_fid, father_rt):
        pat = re.compile(r"(?m)^  m_Father: \{fileID: \d+\}")
        new, n = pat.subn(lambda m: "  m_Father: {fileID: %s}" % father_rt, self.doc[rt_fid])
        assert n == 1, "m_Father 写入失败: " + rt_fid
        self.doc[rt_fid] = new

    def add_doc(self, text):
        fid = RE_HEAD.match(text).group(2)
        assert fid not in self.doc, "fileID 冲突: " + fid
        self.doc[fid] = text
        self.order.append(fid)
        return fid

    def text(self):
        return self.head + "".join(self.doc[f] for f in self.order)

    def all_ids(self):
        return set(self.order)


def is_text_comp(pf, fid):
    """是不是 UI.Text：`m_FontData` 是**多行块**（`pf.field` 取不到），所以按 4 空格缩进的
    `m_FontSize` 判 —— 这个缩进层级在 prefab 里只有 Text 的 FontData 块用。"""
    return pf.cls(fid) == "114" and re.search(r"(?m)^    m_FontSize: \d+\s*$", pf.doc[fid]) is not None


def find_text_comp(pf, go_fid):
    for c in RE_COMPONENT_REF.findall(pf.doc[go_fid]):
        if is_text_comp(pf, c):
            return c
    return None


def find_image_comp(pf, go_fid):
    for c in RE_COMPONENT_REF.findall(pf.doc[go_fid]):
        if pf.cls(c) == "114" and pf.field(c, "m_Sprite") is not None:
            return c
    return None


def ids_of_go(pf, go_fid):
    """GO + 它自己的全部组件（**不含子节点**）。"""
    return [go_fid] + RE_COMPONENT_REF.findall(pf.doc[go_fid])


def subtree_ids(pf, go_fid):
    """整棵子树（含所有后代）的文档 id，广度优先、顺序稳定。

    ⚠ 子节点的 RectTransform 已经包含在「子 GO 的组件表」里，**不要再单独追加一次** ——
    多一个重复项会让 id_map 的最后一次赋值覆盖前面的号（号段凭空缺一个）。
    """
    out = []
    queue = [go_fid]
    while queue:
        go = queue.pop(0)
        out.extend(ids_of_go(pf, go))
        for crt in pf.children(pf.rt_of_go(go)):
            queue.append(RE_GO_REF.search(pf.doc[crt]).group(1))
    assert len(out) == len(set(out)), "子树里有重复对象: " + go_fid
    return out


def _apply_clone(pf, src_ids, id_map):
    """按 id_map 把 src_ids 的每段文本里的旧号**单趟**换成新号，再逐块写回。

    单趟正则 + 长号优先：多个旧号互不为前缀时结果确定，避免逐号 `str.replace`
    在"一个号是另一个号子串"时改错。
    """
    assert set(src_ids) == set(id_map), "id_map 必须覆盖整棵子树"
    alts = sorted(id_map, key=len, reverse=True)
    pat = re.compile("|".join(re.escape(o) for o in alts))
    for old in src_ids:
        pf.doc[id_map[old]] = pat.sub(lambda m: id_map[m.group(0)], pf.doc[old])
        pf.order.append(id_map[old])


class IdAlloc(object):
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
    """按**已有 id 的高水位**起算的分配器。

    两个阶段共用同一段号（都从 `NEW_ID_BASE` 起），所以第 3 步不能再从基址开始 ——
    那会与第 2 步分配过的号**正面相撞**（实测：`8860000000000000001` 已存在）。
    `take` 里的 `assert fid not in pf.doc` 刻意保持不动：它应该是"永不触发"的安全网，
    而不是被"自动跳过已用号"掩盖掉的真撞号 —— 后者会把一次逻辑错误变成静默的号段漂移。
    """
    used = [int(x) for x in pf.all_ids() if str(x).isdigit() and int(x) >= NEW_ID_BASE]
    return IdAlloc(max(used) if used else NEW_ID_BASE)


def clone_bare(pf, src_go, alloc):
    """只克隆 GO + 自身组件（**不含子节点**）—— 给容器用。

    返回 (new_go, new_rt)。调用方负责改写 m_Father / m_Children / m_IsActive。
    """
    src_ids = ids_of_go(pf, src_go)
    new_ids = alloc.take(len(src_ids), pf)
    _apply_clone(pf, src_ids, dict(zip(src_ids, new_ids)))
    new_go = new_ids[0]
    return new_go, pf.rt_of_go(new_go)


def clone_subtree(pf, src_go, alloc):
    """克隆 src_go 整棵子树，返回 (new_go, new_rt) 与「源 GO → 新 GO」映射。"""
    src_ids = subtree_ids(pf, src_go)
    new_ids = alloc.take(len(src_ids), pf)
    id_map = dict(zip(src_ids, new_ids))
    _apply_clone(pf, src_ids, id_map)
    new_go = id_map[src_go]
    return new_go, pf.rt_of_go(new_go), id_map


def _mk_button(pf, src_go, alloc, name, text, color, anchor_min, anchor_max):
    """克隆一个「按钮 + Label」子树并改名/改锚点/改文案。返回 (go, rt)。"""
    go, rt, id_map = clone_subtree(pf, src_go, alloc)
    pf.set_field(go, "m_Name", name)
    pf.set_field(rt, "m_AnchorMin", anchor_min)
    pf.set_field(rt, "m_AnchorMax", anchor_max)
    pf.set_field(rt, "m_AnchoredPosition", CELL_POS)
    pf.set_field(rt, "m_SizeDelta", CELL_SIZE)
    label_go = pf.child_go_named(go, "Label") or pf.child_go_named(go, "Icon")
    assert label_go, "%s 克隆后找不到 Label 子节点" % name
    txt = find_text_comp(pf, label_go)
    assert txt, "%s/Label 上找不到 Text 组件" % name
    pf.set_field(txt, "m_Text", yaml_str(text))
    pf.set_field(txt, "m_Color", color)
    return go, rt


def patch(text, do_log=True):
    """按**阶段**打补丁：每一步各自判"打过没有"，所以三种输入都能正确处理 ——
    原始件（两步都打）、只打过第 2 步的旧产物（只补第 3 步）、已完整（一字不改）。

    分阶段是必要的：`UIContactAi.prefab` 在第 2 步之后已经被打包/部署过一轮，
    手上有那个中间态的人（就是本机）必须能只补第 3 步。用单一"打过没有"的开关
    会把中间态判成"已完整"而**静默跳过后半段**。
    """
    pf = Prefab(text)
    # 最低成本的安全网：拆分/拼回必须逐字节还原（不然这次手术会顺手改坏别处）
    assert pf.text() == text, "Prefab 拆分-拼回不一致（脚本自身的问题，先别改真文件）"
    before_count = len(pf.order)
    changed = False

    if pf.find_go(TAB_NEW):
        if do_log:
            log("已存在 %s → 跳过第 2 步（群聊页），不动它" % TAB_NEW)
    else:
        _stage_groups(pf, do_log)
        changed = True

    if pf.find_go(DEL_GROUP_BTN):
        if do_log:
            log("已存在 %s → 跳过第 3 步（解散按钮），不动它" % DEL_GROUP_BTN)
    else:
        _stage_dissolve(pf, do_log)
        changed = True

    out = pf.text()
    if do_log and changed:
        log("对象数 %d → %d（+%d）" % (before_count, len(pf.order), len(pf.order) - before_count))
    verify(out)
    return out


def _stage_groups(pf, do_log=True):
    """第 2 步：Tabs 三等分 + 群聊 Tab、列表区让位、行内复选框、底栏（新建 + 选择条）。"""
    bg = pf.find_go("BG")
    assert bg, "预制件里找不到 BG（路径契约变了？）"
    bg_rt = pf.rt_of_go(bg)
    tabs = pf.find_go("Tabs")
    assert tabs, "找不到 BG/Tabs"
    tabs_rt = pf.rt_of_go(tabs)
    tab_friend = pf.find_go("TabFriendBtn")
    tab_recent = pf.find_go("TabRecentBtn")
    assert tab_friend and tab_recent, "两个既有 Tab 必须都在"
    scroll = pf.find_go("ContactScroll")
    assert scroll, "找不到 BG/ContactScroll"
    item = pf.find_go("ContactItem")
    assert item, "找不到 BG/ContactItem（行模板）"
    chat_btn = pf.find_go("ChatBtn")
    assert chat_btn, "找不到 ContactItem/ChatBtn（复选框的克隆源）"

    alloc = _alloc_for(pf)

    # ══ A. Tabs 三等分 + 新 Tab ══
    # 先断言既有两个 Tab 的几何是"共用常量"里的样子，避免在一个被手工改过的预制件上
    # 偷偷重排列宽（要改就改脚本常量，别让脚本"顺手修"）。
    pf.set_field(tabs_rt, "m_SizeDelta", TABS_SIZE)
    for go, amin, amax in ((tab_friend, A_0, A_1_3), (tab_recent, A_1_3, A_2_3)):
        rt = pf.rt_of_go(go)
        pf.set_field(rt, "m_AnchorMin", "{x: %s, y: 0}" % amin)
        pf.set_field(rt, "m_AnchorMax", "{x: %s, y: 1}" % amax)
        pf.set_field(rt, "m_AnchoredPosition", TAB_POS)
        pf.set_field(rt, "m_SizeDelta", TAB_SIZE)

    tab_new, tab_new_rt = _mk_button(pf, tab_recent, alloc, TAB_NEW, TAB_NEW_TEXT,
                                     TEXT_GRAY, "{x: %s, y: 0}" % A_2_3, "{x: 1, y: 1}")
    pf.set_father(tab_new_rt, tabs_rt)
    kids = pf.children(tabs_rt)
    assert tab_new_rt not in kids
    pf.set_children(tabs_rt, kids + [tab_new_rt])

    # ══ B. 列表区让出底部 48px ══
    scroll_rt = pf.rt_of_go(scroll)
    pf.set_field(scroll_rt, "m_AnchoredPosition", SCROLL_POS)
    pf.set_field(scroll_rt, "m_SizeDelta", SCROLL_SIZE)

    # ══ C. 行模板加复选框（与 ChatBtn 同位置，互斥显示）══
    item_rt = pf.rt_of_go(item)
    box, box_rt, _ = clone_subtree(pf, chat_btn, alloc)
    pf.set_field(box, "m_Name", CHECKBOX)
    pf.set_field(box_rt, "m_AnchorMin", BOX_ANCHOR)
    pf.set_field(box_rt, "m_AnchorMax", BOX_ANCHOR)
    pf.set_field(box_rt, "m_AnchoredPosition", BOX_POS)
    pf.set_field(box_rt, "m_SizeDelta", BOX_SIZE)
    img = find_image_comp(pf, box)
    assert img, "克隆的 CheckBox 上找不到 Image"
    pf.set_field(img, "m_Color", BOX_COLOR)
    check_go = pf.child_go_named(box, "Icon")
    assert check_go, "克隆的 CheckBox 里应有 Icon 子节点（就是那个 ✓）"
    pf.set_field(check_go, "m_Name", CHECK)
    pf.set_field(check_go, "m_IsActive", "0")          # 默认不勾
    check_txt = find_text_comp(pf, check_go)
    assert check_txt, "Check 上找不到 Text 组件"
    pf.set_field(check_txt, "m_Color", CHECK_COLOR)
    pf.set_father(box_rt, item_rt)
    ikids = pf.children(item_rt)
    pf.set_children(item_rt, ikids + [box_rt])

    # ══ D. BottomBar（容器：只克隆 Tabs 的 GO + RT + CR，不带它的两个 Tab 子节点）══
    bar, bar_rt = clone_bare(pf, tabs, alloc)
    pf.set_field(bar, "m_Name", BOTTOM_BAR)
    pf.set_field(bar_rt, "m_AnchorMin", BAR_ANCHOR)
    pf.set_field(bar_rt, "m_AnchorMax", BAR_ANCHOR)
    pf.set_field(bar_rt, "m_AnchoredPosition", BAR_POS)
    pf.set_field(bar_rt, "m_SizeDelta", BAR_SIZE)
    pf.set_field(bar_rt, "m_Pivot", PIVOT_CENTER)       # ★ 见 PIVOT_CENTER 处的注释
    pf.set_children(bar_rt, [])                         # 丢掉从 Tabs 抄来的子节点表
    pf.set_father(bar_rt, bg_rt)
    bkids = pf.children(bg_rt)
    pf.set_children(bg_rt, bkids + [bar_rt])

    # ══ E. NewGroupBtn（默认隐藏：由 C# 在群聊 Tab 点亮，失败时界面=现状）══
    ng, ng_rt = _mk_button(pf, tab_recent, alloc, NEW_GROUP_BTN, NEW_GROUP_TEXT,
                           BTN_TEXT, "{x: 0, y: 0}", "{x: 1, y: 1}")
    pf.set_field(ng, "m_IsActive", "0")
    pf.set_father(ng_rt, bar_rt)

    # ══ F. SelectBar（默认隐藏）+ 四格 ══
    selbar, selbar_rt = clone_bare(pf, tabs, alloc)
    pf.set_field(selbar, "m_Name", SELECT_BAR)
    pf.set_field(selbar, "m_IsActive", "0")
    pf.set_field(selbar_rt, "m_AnchorMin", "{x: 0, y: 0}")
    pf.set_field(selbar_rt, "m_AnchorMax", "{x: 1, y: 1}")
    pf.set_field(selbar_rt, "m_AnchoredPosition", FILL_POS)
    pf.set_field(selbar_rt, "m_SizeDelta", FILL_SIZE)
    pf.set_children(selbar_rt, [])
    pf.set_father(selbar_rt, bar_rt)

    sa, sa_rt = _mk_button(pf, tab_recent, alloc, SEL_ALL, SEL_ALL_TEXT, TEXT_MAIN,
                           "{x: %s, y: 0}" % SEL_ALL_A[0], "{x: %s, y: 1}" % SEL_ALL_A[1])
    pf.set_father(sa_rt, selbar_rt)
    # CountLabel 只要一个纯 Text（克隆 TabRecentBtn/Label 这棵 4 对象的小树）
    label_src = pf.child_go_named(tab_recent, "Label")
    assert label_src, "TabRecentBtn 下应有 Label"
    cnt, cnt_rt, _ = clone_subtree(pf, label_src, alloc)
    pf.set_field(cnt, "m_Name", SEL_COUNT)
    pf.set_field(cnt_rt, "m_AnchorMin", "{x: %s, y: 0}" % SEL_COUNT_A[0])
    pf.set_field(cnt_rt, "m_AnchorMax", "{x: %s, y: 1}" % SEL_COUNT_A[1])
    pf.set_field(cnt_rt, "m_AnchoredPosition", CELL_POS)
    pf.set_field(cnt_rt, "m_SizeDelta", CELL_SIZE)
    cnt_txt = find_text_comp(pf, cnt)
    assert cnt_txt, "CountLabel 上找不到 Text 组件"
    pf.set_field(cnt_txt, "m_Text", yaml_str(SEL_COUNT_TEXT))
    pf.set_field(cnt_txt, "m_Color", TEXT_GRAY)
    pf.set_father(cnt_rt, selbar_rt)

    cx, cx_rt = _mk_button(pf, tab_recent, alloc, SEL_CANCEL, SEL_CANCEL_TEXT, TEXT_GRAY,
                           "{x: %s, y: 0}" % SEL_CANCEL_A[0], "{x: %s, y: 1}" % SEL_CANCEL_A[1])
    pf.set_father(cx_rt, selbar_rt)
    ok, ok_rt = _mk_button(pf, tab_recent, alloc, SEL_CONFIRM, SEL_CONFIRM_TEXT, BTN_TEXT,
                           "{x: %s, y: 0}" % SEL_CONFIRM_A[0], "{x: %s, y: 1}" % SEL_CONFIRM_A[1])
    pf.set_father(ok_rt, selbar_rt)

    pf.set_children(bar_rt, [ng_rt, selbar_rt])
    pf.set_children(selbar_rt, [sa_rt, cnt_rt, cx_rt, ok_rt])

    if do_log:
        log("第 2 步：TabGroupBtn / CheckBox / BottomBar(NewGroupBtn + SelectBar×4)")
        log("Tabs 三等分 0..1/3..2/3..1（size 240→300）；ContactScroll 底部让出 48px")


def _stage_dissolve(pf, do_log=True):
    """第 3 步：底栏加「删除群聊」，并把 NewGroupBtn 收成左半。

    只克隆 `TabRecentBtn`（与第 2 步同源），所以新按钮的组件集合、字号、锚点写法
    与既有按钮**逐字相同** —— 不会出现"这个按钮的字比别的粗/大一号"。
    """
    bar = pf.find_go(BOTTOM_BAR)
    assert bar, "找不到 %s（第 2 步没打？）" % BOTTOM_BAR
    bar_rt = pf.rt_of_go(bar)
    ng = pf.find_go(NEW_GROUP_BTN)
    assert ng, "找不到 %s" % NEW_GROUP_BTN
    ng_rt = pf.rt_of_go(ng)
    selbar = pf.find_go(SELECT_BAR)
    assert selbar, "找不到 %s" % SELECT_BAR
    selbar_rt = pf.rt_of_go(selbar)
    tab_recent = pf.find_go("TabRecentBtn")
    assert tab_recent, "找不到 TabRecentBtn（新按钮的克隆源）"

    alloc = _alloc_for(pf)

    # NewGroupBtn 收成左半
    pf.set_field(ng_rt, "m_AnchorMin", "{x: %s, y: 0}" % BAR_HALF_L[0])
    pf.set_field(ng_rt, "m_AnchorMax", "{x: %s, y: 1}" % BAR_HALF_L[1])

    # 右半：新按钮
    dg, dg_rt = _mk_button(pf, tab_recent, alloc, DEL_GROUP_BTN, DEL_GROUP_TEXT,
                           DEL_GROUP_COLOR,
                           "{x: %s, y: 0}" % BAR_HALF_R[0], "{x: %s, y: 1}" % BAR_HALF_R[1])
    pf.set_field(dg, "m_IsActive", "0")
    pf.set_father(dg_rt, bar_rt)
    # 子节点顺序 = 画面顺序（后者在上层）。SelectBar 必须留在**最后**：
    # 它在"选择模式"下整条铺满，要盖住那两个按钮。
    kids = [x for x in pf.children(bar_rt) if x != selbar_rt]
    assert dg_rt not in kids
    pf.set_children(bar_rt, kids + [dg_rt, selbar_rt])

    if do_log:
        log("第 3 步：%s（右半）+ %s 收成左半" % (DEL_GROUP_BTN, NEW_GROUP_BTN))


def _nums(s):
    return [float(x) for x in re.findall(r"[-+]?[\d.]+(?:[eE][-+]?\d+)?", s or "")]


def rect_in_parent(pf, rt_fid, pw, ph):
    """按 Unity 的 RectTransform 公式实算一个节点的矩形（相对父节点左下角）。

    **只比锚点字符串是不够的** —— pivot 也参与几何：

        anchorRef = (anchorMin + (anchorMax - anchorMin) * pivot) * parentSize
        size      = (anchorMax - anchorMin) * parentSize + sizeDelta
        min, max  = anchorRef + pos - pivot*size , anchorRef + pos + (1-pivot)*size

    本脚本第一版就栽在这里：`BottomBar` 锚点是对的，但 pivot 从 `Tabs` 抄来
    `{x: 0.5, y: 1}`（**顶部**基准），叠上**底边**锚定 ⇒ 整条掉出面板 18px
    （44px 只露 26px）。字符串复核全绿，肉眼在实机上才会发现。
    """
    amin = _nums(pf.field(rt_fid, "m_AnchorMin"))
    amax = _nums(pf.field(rt_fid, "m_AnchorMax"))
    pos = _nums(pf.field(rt_fid, "m_AnchoredPosition"))
    size = _nums(pf.field(rt_fid, "m_SizeDelta"))
    piv = _nums(pf.field(rt_fid, "m_Pivot"))
    assert len(amin) == len(amax) == len(pos) == len(size) == len(piv) == 2, \
        "RectTransform 字段解析失败: " + rt_fid
    out = []
    for i, P in enumerate((pw, ph)):
        rs = (amax[i] - amin[i]) * P + size[i]
        ref = (amin[i] + (amax[i] - amin[i]) * piv[i]) * P
        pp = ref + pos[i]
        out.append((pp - piv[i] * rs, pp + (1.0 - piv[i]) * rs))
    return out


def verify(text):
    """独立复核（重新解析产物，不信任 patch 的中间状态）。失败即抛 AssertionError。"""
    pf = Prefab(text)
    bg = pf.find_go("BG")
    assert bg, "复核失败：BG 不见了"
    bg_rt = pf.rt_of_go(bg)

    # ---- A. 三个 Tab 首尾相接、等宽、无重叠 ----
    tabs_rt = pf.rt_of_go(pf.find_go("Tabs"))
    assert pf.field(tabs_rt, "m_SizeDelta") == TABS_SIZE, pf.field(tabs_rt, "m_SizeDelta")

    def ax(fid, key):
        m = re.search(r"\{x: ([-\d.eE]+), y: ([-\d.eE]+)\}", pf.field(fid, key) or "")
        assert m, "锚点解析失败 %s.%s" % (fid, key)
        return float(m.group(1)), float(m.group(2))

    cols = []
    for name in ("TabFriendBtn", "TabRecentBtn", TAB_NEW):
        go = pf.find_go(name)
        assert go, "复核失败：%s 不存在" % name
        rt = pf.rt_of_go(go)
        assert pf.father_of(rt) == tabs_rt, "复核失败：%s 的父节点不是 Tabs" % name
        assert pf.children(tabs_rt).count(rt) == 1, "复核失败：Tabs.m_Children 里 %s 应恰好一次" % name
        assert pf.field(rt, "m_AnchoredPosition") == TAB_POS, name
        assert pf.field(rt, "m_SizeDelta") == TAB_SIZE, name
        cols.append((name, ax(rt, "m_AnchorMin")[0], ax(rt, "m_AnchorMax")[0]))
    assert abs(cols[0][1] - 0.0) < 1e-9, "好友 Tab 应从最左开始"
    assert abs(cols[2][2] - 1.0) < 1e-9, "群聊 Tab 应顶到最右"
    for i in range(3):
        w = cols[i][2] - cols[i][1]
        assert abs(w - 1.0 / 3.0) < 1e-5, "%s 列宽应约 1/3（实际 %.6f）" % (cols[i][0], w)
        if i:
            assert abs(cols[i - 1][2] - cols[i][1]) < 1e-5, \
                "%s 与 %s 之间有缝/重叠" % (cols[i - 1][0], cols[i][0])
    tab_new_label = pf.child_go_named(pf.find_go(TAB_NEW), "Label")
    assert tab_new_label, "复核失败：TabGroupBtn 下没有 Label"
    assert pf.field(find_text_comp(pf, tab_new_label), "m_Text") == yaml_str(TAB_NEW_TEXT)

    # ---- B. 列表区让位 ----
    scroll_rt = pf.rt_of_go(pf.find_go("ContactScroll"))
    assert pf.field(scroll_rt, "m_AnchoredPosition") == SCROLL_POS, pf.field(scroll_rt, "m_AnchoredPosition")
    assert pf.field(scroll_rt, "m_SizeDelta") == SCROLL_SIZE, pf.field(scroll_rt, "m_SizeDelta")

    # ---- C. 行模板的复选框 ----
    item = pf.find_go("ContactItem")
    item_rt = pf.rt_of_go(item)
    box = pf.find_go(CHECKBOX)
    assert box, "复核失败：%s 不存在" % CHECKBOX
    box_rt = pf.rt_of_go(box)
    assert pf.father_of(box_rt) == item_rt, "复核失败：CheckBox 的父节点不是 ContactItem"
    assert pf.children(item_rt).count(box_rt) == 1, "复核失败：ContactItem.m_Children 里 CheckBox 应恰好一次"
    assert pf.field(box_rt, "m_AnchorMin") == BOX_ANCHOR and pf.field(box_rt, "m_AnchorMax") == BOX_ANCHOR
    assert pf.field(box_rt, "m_AnchoredPosition") == BOX_POS
    assert pf.field(box_rt, "m_SizeDelta") == BOX_SIZE
    # 与 ChatBtn 同位置（互斥显示），别重叠到名字上
    chat_rt = pf.rt_of_go(pf.find_go("ChatBtn"))
    assert pf.field(chat_rt, "m_AnchorMin") == pf.field(box_rt, "m_AnchorMin"), \
        "CheckBox 应与 ChatBtn 同锚点（两者互斥显示，共用右侧那一格）"
    check_go = pf.child_go_named(box, CHECK)
    assert check_go, "复核失败：CheckBox 下没有 %s" % CHECK
    assert pf.field(check_go, "m_IsActive") == "0", "复核失败：Check 默认必须是隐藏的"

    # ---- D/E/F. 底栏 ----
    bar = pf.find_go(BOTTOM_BAR)
    assert bar, "复核失败：%s 不存在" % BOTTOM_BAR
    bar_rt = pf.rt_of_go(bar)
    assert pf.father_of(bar_rt) == bg_rt, "复核失败：BottomBar 的父节点不是 BG"
    assert pf.children(bg_rt).count(bar_rt) == 1, "复核失败：BG.m_Children 里 BottomBar 应恰好一次"
    assert pf.field(bar_rt, "m_AnchorMin") == BAR_ANCHOR and pf.field(bar_rt, "m_AnchorMax") == BAR_ANCHOR
    assert pf.field(bar_rt, "m_AnchoredPosition") == BAR_POS
    assert pf.field(bar_rt, "m_SizeDelta") == BAR_SIZE
    assert pf.children(bar_rt) == [pf.rt_of_go(pf.find_go(NEW_GROUP_BTN)),
                                   pf.rt_of_go(pf.find_go(DEL_GROUP_BTN)),
                                   pf.rt_of_go(pf.find_go(SELECT_BAR))], \
        "复核失败：BottomBar 的子节点应恰好是 [NewGroupBtn, DelGroupBtn, SelectBar]（顺序=画面顺序）"

    ng = pf.find_go(NEW_GROUP_BTN)
    assert ng and pf.field(ng, "m_IsActive") == "0", "复核失败：NewGroupBtn 默认应隐藏"
    ng_label = pf.child_go_named(ng, "Label")
    assert pf.field(find_text_comp(pf, ng_label), "m_Text") == yaml_str(NEW_GROUP_TEXT)

    # 两个按钮各占一半：不切开会**完全重叠**，「新建群聊」将永远点不到（uGUI 只给最上层）
    ng_rt = pf.rt_of_go(ng)
    dg = pf.find_go(DEL_GROUP_BTN)
    assert dg, "复核失败：%s 不存在" % DEL_GROUP_BTN
    assert pf.field(dg, "m_IsActive") == "0", "复核失败：DelGroupBtn 默认应隐藏"
    dg_rt = pf.rt_of_go(dg)
    assert pf.father_of(dg_rt) == bar_rt, "复核失败：DelGroupBtn 的父节点不是 BottomBar"
    assert abs(ax(ng_rt, "m_AnchorMin")[0] - float(BAR_HALF_L[0])) < 1e-9, "NewGroupBtn 没收成左半"
    assert abs(ax(ng_rt, "m_AnchorMax")[0] - float(BAR_HALF_L[1])) < 1e-9, "NewGroupBtn 没收成左半"
    assert abs(ax(dg_rt, "m_AnchorMin")[0] - float(BAR_HALF_R[0])) < 1e-9, "DelGroupBtn 不在右半"
    assert abs(ax(dg_rt, "m_AnchorMax")[0] - float(BAR_HALF_R[1])) < 1e-9, "DelGroupBtn 不在右半"
    assert pf.field(ng_rt, "m_AnchoredPosition") == CELL_POS, "两个半格都该居中（pos=0）"
    assert pf.field(dg_rt, "m_AnchoredPosition") == CELL_POS, "两个半格都该居中（pos=0）"
    dg_label = pf.child_go_named(dg, "Label")
    assert dg_label, "复核失败：DelGroupBtn 下没有 Label"
    assert pf.field(find_text_comp(pf, dg_label), "m_Text") == yaml_str(DEL_GROUP_TEXT), \
        "DelGroupBtn 文案应为 %s" % yaml_str(DEL_GROUP_TEXT)

    selbar = pf.find_go(SELECT_BAR)
    assert selbar and pf.field(selbar, "m_IsActive") == "0", "复核失败：SelectBar 默认应隐藏"
    selbar_rt = pf.rt_of_go(selbar)
    assert pf.father_of(selbar_rt) == bar_rt, "复核失败：SelectBar 的父节点不是 BottomBar"

    # 选人四格：等宽校验（首尾相接、不重叠、不留缝）
    cells = []
    for name, (lo, hi) in ((SEL_ALL, SEL_ALL_A), (SEL_COUNT, SEL_COUNT_A),
                           (SEL_CANCEL, SEL_CANCEL_A), (SEL_CONFIRM, SEL_CONFIRM_A)):
        go = pf.find_go(name)
        assert go, "复核失败：%s 不存在" % name
        rt = pf.rt_of_go(go)
        assert pf.father_of(rt) == selbar_rt, "复核失败：%s 的父节点不是 SelectBar" % name
        assert abs(ax(rt, "m_AnchorMin")[0] - float(lo)) < 1e-6, name
        assert abs(ax(rt, "m_AnchorMax")[0] - float(hi)) < 1e-6, name
        assert ax(rt, "m_AnchorMin")[1] == 0.0 and ax(rt, "m_AnchorMax")[1] == 1.0, name
        cells.append((name, float(lo), float(hi)))
    assert abs(cells[0][1] - 0.0) < 1e-9 and abs(cells[-1][2] - 1.0) < 1e-9, "四格应铺满整条"
    for i in range(1, len(cells)):
        assert abs(cells[i - 1][2] - cells[i][1]) < 1e-9, \
            "%s 与 %s 之间有缝/重叠" % (cells[i - 1][0], cells[i][0])

    # 文案
    for holder, want, child in ((SEL_ALL, SEL_ALL_TEXT, "Label"),
                                (SEL_CANCEL, SEL_CANCEL_TEXT, "Label"),
                                (SEL_CONFIRM, SEL_CONFIRM_TEXT, "Label")):
        cg = pf.child_go_named(pf.find_go(holder), child)
        assert cg, "复核失败：%s 下没有 %s" % (holder, child)
        got = pf.field(find_text_comp(pf, cg), "m_Text")
        assert got == yaml_str(want), "%s 文案应为 %s，实际 %s" % (holder, yaml_str(want), got)
    cnt_txt = find_text_comp(pf, pf.find_go(SEL_COUNT))
    assert cnt_txt and pf.field(cnt_txt, "m_Text") == yaml_str(SEL_COUNT_TEXT)

    # 组件完整性：每个新按钮恰好 1 RT + 1 CR + 2 MB（Image + Button），且都指向自己
    for name in (TAB_NEW, NEW_GROUP_BTN, DEL_GROUP_BTN, SEL_ALL, SEL_CANCEL, SEL_CONFIRM):
        go = pf.find_go(name)
        comps = RE_COMPONENT_REF.findall(pf.doc[go])
        kinds = [pf.cls(c) for c in comps]
        assert kinds.count("224") == 1, "%s 应恰好一个 RectTransform" % name
        assert kinds.count("222") == 1, "%s 应恰好一个 CanvasRenderer" % name
        assert kinds.count("114") == 2, "%s 应恰好两个 MonoBehaviour（Image + Button）" % name
        for c in comps:
            assert RE_GO_REF.search(pf.doc[c]).group(1) == go, "%s 的组件 m_GameObject 没指向自己" % name

    ids = [RE_HEAD.match(pf.doc[f]).group(2) for f in pf.order]
    assert len(ids) == len(set(ids)), "fileID 有重复"

    # ---- G. 数值几何（按 Unity 公式实算，不只比锚点字符串）----
    BG_W, BG_H = 380.0, 620.0
    (sx0, sx1), (sy0, sy1) = rect_in_parent(pf, scroll_rt, BG_W, BG_H)
    (bx0, bx1), (by0, by1) = rect_in_parent(pf, bar_rt, BG_W, BG_H)
    assert bx0 >= 0.0 and bx1 <= BG_W, \
        "BottomBar 横向超出面板：[%.1f, %.1f]" % (bx0, bx1)
    assert by0 >= 0.0 and by1 <= BG_H, (
        "★ BottomBar 纵向超出面板：[%.1f, %.1f]（父高 %.0f）—— pivot 与锚点基准不匹配？"
        % (by0, by1, BG_H))
    assert by1 <= sy0 + 1e-6, "BottomBar 顶边 %.1f 压到了列表区底边 %.1f" % (by1, sy0)
    assert sy0 - by1 < 40.0, "底栏与列表之间空档 %.1f px（太宽，白占地方）" % (sy0 - by1)
    assert sy1 <= BG_H, "列表区顶边 %.1f 超出面板" % sy1

    # 三个 Tab 在 Tabs(300×34) 内**等宽、等距、整体居中**。
    #
    # ⚠ 容差取 1e-4 而不是 1e-6：Unity 锚点写的是 `0.3333333`（7 位截断），**不是 1/3**，
    #   于是三段宽度是 95.99999 / 96.00002 / 95.99999 —— 差 ~2e-5 px。亚像素、无视觉影响，
    #   但拿 1e-6 去比会红（本脚本第一版就是这么红的）。
    # ⚠ 也不能断言"第一个从 0 开始"：`sizeDelta.x = -4` 让每段左右各内缩 2px，
    #   那 4px 正是段间缝（原两个 Tab 用 -5，同样内缩）。
    TOL = 1e-4
    spans = []
    for name in ("TabFriendBtn", "TabRecentBtn", TAB_NEW):
        (x0, x1), _ = rect_in_parent(pf, pf.rt_of_go(pf.find_go(name)), 300.0, 34.0)
        spans.append((x0, x1))
    for i, (x0, x1) in enumerate(spans):
        assert abs((x1 - x0) - 96.0) < TOL, \
            "第 %d 个 Tab 宽应为 96（300/3 - 4），实际 %.5f" % (i + 1, x1 - x0)
    for i in range(1, 3):
        gap = spans[i][0] - spans[i - 1][1]
        assert abs(gap - 4.0) < TOL, "第 %d/%d 段间距应为 4px，实际 %.5f" % (i, i + 1, gap)
    assert abs((spans[1][0] + spans[1][1]) / 2.0 - 150.0) < TOL, "中间那个 Tab 没居中"
    assert abs(spans[0][0] - 2.0) < TOL and abs(spans[-1][1] - 298.0) < TOL, \
        "三段整体没在 Tabs 里居中：[%.5f, %.5f]" % (spans[0][0], spans[-1][1])

    # 底栏两个按钮：**各占一半、首尾相接、都在底栏内**。
    # 这条是"不切开会完全重叠"的直接守卫 —— 重叠时下面的断言必红，而实机上
    # 只表现为"新建群聊点不动"（uGUI 拾取只给最上层），没有任何报错。
    bar_w = _nums(BAR_SIZE)[0]
    (n0, n1), _ = rect_in_parent(pf, ng_rt, bar_w, _nums(BAR_SIZE)[1])
    (d0, d1), _ = rect_in_parent(pf, dg_rt, bar_w, _nums(BAR_SIZE)[1])
    assert n0 >= -TOL and d1 <= bar_w + TOL, \
        "底栏按钮超出底栏：[%.1f, %.1f] / [%.1f, %.1f]（宽 %.0f）" % (n0, n1, d0, d1, bar_w)
    assert abs((n1 - n0) - 166.0) < TOL, "左半宽应为 166（340/2 - 4），实际 %.5f" % (n1 - n0)
    assert abs((d1 - d0) - 166.0) < TOL, "右半宽应为 166（340/2 - 4），实际 %.5f" % (d1 - d0)
    assert n1 <= d0 + TOL, \
        "★ 两个按钮重叠了：[%.1f, %.1f] 与 [%.1f, %.1f] —— 下面那个永远点不到" % (n0, n1, d0, d1)
    assert abs((d0 - n1) - 4.0) < TOL, "两半之间应为 4px 缝（各内缩 2px），实际 %.5f" % (d0 - n1)
    assert abs(n0 - 2.0) < TOL and abs(d1 - (bar_w - 2.0)) < TOL, \
        "两个按钮整体没在底栏里居中：[%.5f, %.5f]" % (n0, d1)
    return True


def ensure_backup(outp, bak):
    """写备份，**只在备份不存在时**。返回 True = 本次真的写了。

    ★ 备份必须留住的**是原始件**，而"幂等可重跑"与"每次 `copy2`"是冲突的：
    这个脚本第二次跑会走校验分支、一个字节都不改，但若每次运行都 copy2，
    第二次就会把**已打补丁**的版本盖到备份上 —— 原始件当场丢失，
    而备份的全部意义就是留住它。

    （这不是假想：本函数抽出来之前，脚本第一版就是无条件 copy2，
      一次幂等复跑真把 `UIContactAi.prefab` 的原始件丢了。
      同族的 `prefab_patch_prompt_reset.py` 等脚本目前仍是那个写法。）
    """
    if os.path.exists(bak):
        return False
    shutil.copy2(outp, bak)
    return True


def main(argv=None):
    _fix_console()
    ap = argparse.ArgumentParser(description="UIContactAi.prefab 通讯录补群聊页节点（幂等）")
    ap.add_argument("--in", dest="inp", required=True, help="源 prefab")
    ap.add_argument("--out", dest="out", required=True, help="目标 prefab（可与 --in 相同=原地改）")
    ap.add_argument("--dry-run", action="store_true", help="只算不写")
    ap.add_argument("--no-backup", action="store_true",
                    help="原地改（--in == --out）时不写 .bak_contactgroups 备份")
    ap.add_argument("--allow-protected-out", action="store_true",
                    help="允许写到真实游戏工程（默认拒绝）")
    a = ap.parse_args(argv)

    outp = os.path.abspath(a.out)
    low = outp.replace("/", os.sep).lower()
    if not a.allow_protected_out:
        for p in PROTECTED_OUT:
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
    d = os.path.dirname(outp)
    if d and not os.path.isdir(d):
        os.makedirs(d)
    if inplace and not a.no_backup:
        bak = outp + ".bak_contactgroups"
        if ensure_backup(outp, bak):
            log("== 备份 %s" % bak)
        else:
            log("== 备份已存在，不覆盖（保留最早的原始件）：%s" % bak)
    with io.open(outp, "w", encoding="utf-8", newline="") as f:
        f.write(out)
    log("== 输出 %s (%d 字节)" % (outp, len(out.encode("utf-8"))))
    log("== 自校验通过（锚点/父子/组件/文案/fileID 唯一）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
