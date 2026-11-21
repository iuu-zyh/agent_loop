# -*- coding: utf-8 -*-
"""UIConfigAi.prefab —— 提示词页补「重置为默认」按钮（幂等 / 可重跑 / 纯文本手术）。

改造前（底带只有三个键，删除键占右半后 1/4）：
    BG/PagePrompt ── SavePromptBtn(锚 0..0.5) / NewNpcBtn(0.5..0.75) / DeletePromptBtn(0.75..1)
改造后（右半三等分，重置键插在新建与删除之间）：
    BG/PagePrompt ── SavePromptBtn(0..0.5) / NewNpcBtn(0.5..2/3)
                     / ResetPromptBtn(2/3..5/6) / DeletePromptBtn(5/6..1)

为什么要脚本而不是手改 YAML：新增一个按钮 = 9 个对象（GO + RectTransform + CanvasRenderer +
Image + Button + 子 GO + 子 RectTransform + 子 CanvasRenderer + 子 Text）、fileID 全部要新分配、
父子引用要成对挂好（m_Children / m_Father / m_Component 三处），手改极易漏一处而**静默**变成
"按钮不出现"或"点了没反应"。本脚本的做法是**文本克隆**：把已有的 SavePromptBtn 子树整段抄一份、
只把 9 个 fileID 全局替换成新号、再覆盖少数几个字段（名字/锚点/文案/颜色）——
字段集合与 GUID 引用因此与原件逐字相同，不存在"少写一个 m_RaycastPadding"这类隐患。

用法:
    python3 scripts/dev/prefab_patch_prompt_reset.py \
        --in  "<AB工程>/Assets/Resources/UI/UIConfigAi.prefab" \
        --out "<同上>" --allow-protected-out
    --dry-run 只算不写；原地改默认先写 .bak_promptreset 备份。
    `--in/--out` 指向真实游戏工程时**默认拒写**（防手滑），必须显式加 --allow-protected-out。
    （约定与 prefab_patch_config_groups.py 一致。）

幂等:
    以「BG/PagePrompt 下有没有名为 ResetPromptBtn 的子节点」为分支开关——
      没有 → 插入分支：克隆 SavePromptBtn 子树 + 新 fileID + 改三处按钮锚点 + 挂进 m_Children；
      已有 → 校验分支：只核对字段（锚点/文案/颜色/父子/ID 唯一性），一个字节都不改。
    重复跑第二次，对象数与字节数完全不变。

对齐的 C# 契约（三处必须同名，改名一起改）:
    BG/PagePrompt/ResetPromptBtn        → AbConfigPanel.CollectRefs 的 FindGo 路径
    列宽 0.5 / 2/3 / 5/6 / 1            → ConfigUiBuilder.Build 的同一套锚点
    文案「重置为默认」/ 暗琥珀 (0.55,0.33,0.05) → ConfigPresenter.DisarmReset / ConfigUiBuilder
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
    pytest 的捕获流会跟着被换掉，测试结束时报 `ValueError: I/O operation on closed file`
    （本文件被 tests/test_prompt_reset_ui_contract.py import 来复核预制件，踩到过）。
    """
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    except Exception:
        pass

# 真实游戏工程 / ui_preview 工程：默认拒写（与 prefab_patch_config_groups.py 同约定）
PROTECTED_OUT = [
    "/mnt/e/SteamLibrary/steamapps/common/鬼谷八荒/Mod/modFQA/资源修改教程/",
    "e:\\steamlibrary\\steamapps\\common\\鬼谷八荒\\mod\\modfqa\\资源修改教程\\",
    "/mnt/f/agent_loop/ui_preview/",
    "f:\\agent_loop\\ui_preview\\",
]

# ---- 版式常量（与 ConfigUiBuilder.Build / AbConfigPanel.CollectRefs 严格对齐）----
TARGET_GO = "ResetPromptBtn"
TARGET_TEXT = "重置为默认"
TARGET_COLOR = "{r: 0.55, g: 0.33, b: 0.05, a: 1}"     # 暗琥珀（保存=墨黑 / 删除=警示红）
ANCHOR_SAVE_MAX_X = "{x: 0.5, y: 0}"
ANCHOR_NEW_MAX_X = "{x: 0.6666667, y: 0}"              # 右半三等分：0.5 + 1/6
ANCHOR_RESET_MIN_X = "{x: 0.6666667, y: 0}"
ANCHOR_RESET_MAX_X = "{x: 0.8333333, y: 0}"            # 0.5 + 2/6
ANCHOR_DELETE_MIN_X = "{x: 0.8333333, y: 0}"
BTN_POS = "{x: 0, y: 24}"
BTN_SIZE = "{x: -12, y: 40}"
# 新 fileID 段：8840000000000000001..009 已被 DeletePromptBtn 占用（同族合成号），从 0100 起另开一段
NEW_ID_BASE = 8840000000000000100

RE_DOC = re.compile(r"(?m)^(?=--- !u!)")
RE_HEAD = re.compile(r"--- !u!(\d+) &(\d+)")
RE_COMPONENT_REF = re.compile(r"(?m)^  - component: \{fileID: (\d+)\}")
RE_CHILD_REF = re.compile(r"(?m)^  - \{fileID: (\d+)\}")
RE_GO_REF = re.compile(r"(?m)^  m_GameObject: \{fileID: (\d+)\}")
RE_FATHER_REF = re.compile(r"(?m)^  m_Father: \{fileID: (\d+)\}")
RE_NAME = re.compile(r"(?m)^  m_Name: (.*)$")


def log(*a):
    print("[prompt-reset]", *a)


def uesc(s):
    """中文 → Unity YAML 的 \\uXXXX 转义（与文件里既有写法一致）。"""
    return "".join(ch if ord(ch) < 128 else "\\u%04X" % ord(ch) for ch in s)


def yaml_str(s):
    return '"%s"' % uesc(s)


class Prefab(object):
    """极简 prefab 文本手术台：按 fileID 索引文档，改字段/父子，最后原序拼回。"""

    def __init__(self, text):
        self.head = ""                      # 文件头（%YAML / %TAG 等）
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
        refs = RE_COMPONENT_REF.findall(self.doc[go_fid])
        for r in refs:
            if self.cls(r) == "224":
                return r
        raise AssertionError("GO 没有 RectTransform: " + go_fid)

    def children(self, rt_fid):
        return RE_CHILD_REF.findall(self.doc[rt_fid])

    def find_go(self, name):
        for fid in self.order:
            if self.cls(fid) == "1" and self.go_name(fid) == name:
                return fid
        return None

    def path_of(self, go_fid):
        parts = [self.go_name(go_fid)]
        rt = self.rt_of_go(go_fid)
        while True:
            m = RE_FATHER_REF.search(self.doc[rt])
            father = m.group(1) if m else "0"
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

    def add_doc(self, text):
        fid = RE_HEAD.match(text).group(2)
        assert fid not in self.doc, "fileID 冲突: " + fid
        self.doc[fid] = text
        self.order.append(fid)
        return fid

    def set_children(self, rt_fid, fids):
        """整段重写 m_Children（保持缩进与 '- {fileID: N}' 形式）。"""
        body = "  m_Children:\n" + "".join("  - {fileID: %s}\n" % f for f in fids)
        pat = re.compile(r"(?m)^  m_Children:\n(?:  - \{fileID: \d+\}\n)*")
        new, n = pat.subn(body, self.doc[rt_fid])
        assert n == 1, "m_Children 重写失败: " + rt_fid
        self.doc[rt_fid] = new

    def text(self):
        return self.head + "".join(self.doc[f] for f in self.order)

    def all_ids(self):
        return set(self.order)


def collect_subtree(pf, go_fid):
    """一个按钮 GO 的整棵子树（含子节点）的文档 id 列表，顺序稳定。

    ⚠ 子节点的 RectTransform 已经包含在「子 GO 的组件表」里，**不要再单独 append 一次** ——
    多一个重复项会让 id_map 的最后一次赋值覆盖前面的号（号段凭空缺一个），
    虽然克隆结果侥幸仍然自洽，但那属于"靠巧合正确"，必须避免。
    """
    out = []
    rt = pf.rt_of_go(go_fid)
    out.append(go_fid)
    out.extend(RE_COMPONENT_REF.findall(pf.doc[go_fid]))
    for child_rt in pf.children(rt):
        child_go = RE_GO_REF.search(pf.doc[child_rt]).group(1)
        out.append(child_go)
        out.extend(RE_COMPONENT_REF.findall(pf.doc[child_go]))
    assert len(out) == len(set(out)), "子树里有重复对象"
    return out


def is_text_comp(pf, fid):
    """是不是 UI.Text：`m_FontData` 是**多行块**（`pf.field` 取不到），所以按 4 空格缩进的
    `m_FontSize` 判 —— 这个缩进层级在 prefab 里只有 Text 的 FontData 块用。"""
    return pf.cls(fid) == "114" and re.search(r"(?m)^    m_FontSize: \d+\s*$", pf.doc[fid]) is not None


def clone_subtree(pf, src_go, id_map):
    """按 id_map 克隆 src_go 子树：先把整段文本里的旧号全局换新号，再逐块写回。"""
    src_ids = collect_subtree(pf, src_go)
    assert set(src_ids) == set(id_map), "id_map 必须覆盖整棵子树"
    for old in src_ids:
        text = pf.doc[old]
        for o, n in id_map.items():                    # 号长一致，但按「先长后短」替换更稳
            text = text.replace(o, n)
        pf.doc[id_map[old]] = text
        pf.order.append(id_map[old])


def patch(text, do_log=True):
    pf = Prefab(text)
    # 最低成本的安全网：拆分/拼回必须逐字节还原（不然这次手术会顺手改坏别处）
    assert pf.text() == text, "Prefab 拆分-拼回不一致（脚本自身的问题，先别改真文件）"
    before_count = len(pf.order)

    page = pf.find_go("PagePrompt")
    assert page, "预制件里找不到 BG/PagePrompt（路径契约变了？）"
    page_rt = pf.rt_of_go(page)

    existing = pf.find_go(TARGET_GO)
    if existing:
        if do_log:
            log("已存在 %s（%s）→ 走校验分支，不插入" % (TARGET_GO, pf.path_of(existing)))
        verify(text)
        return text

    save = pf.find_go("SavePromptBtn")
    new_btn = pf.find_go("NewNpcBtn")
    delete = pf.find_go("DeletePromptBtn")
    assert save and new_btn and delete, "底带三个键必须都在（Save/NewNpc/DeletePromptBtn）"

    save_rt = pf.rt_of_go(save)
    child_rt = pf.children(save_rt)
    assert len(child_rt) == 1, "SavePromptBtn 应当只有一个子节点（Label）"

    # 另两个键的几何必须已经是共用常量（否则重排列宽会把某个键挤歪）——
    # 这里只断言不改写：真要改就改常量，别让脚本偷偷"顺手修"一个手工改过的预制件
    for who in (new_btn, delete):
        rt = pf.rt_of_go(who)
        assert pf.field(rt, "m_AnchoredPosition") == BTN_POS, \
            "%s 的 m_AnchoredPosition=%s（期望 %s）" % (who, pf.field(rt, "m_AnchoredPosition"), BTN_POS)
        assert pf.field(rt, "m_SizeDelta") == BTN_SIZE, \
            "%s 的 m_SizeDelta=%s（期望 %s）" % (who, pf.field(rt, "m_SizeDelta"), BTN_SIZE)

    # ---- ① 克隆：9 个对象，fileID 整段换新 ----
    src_ids = collect_subtree(pf, save)
    assert len(src_ids) == 9, "SavePromptBtn 子树应为 9 个对象，实际 %d" % len(src_ids)
    id_map = {}
    for i, old in enumerate(src_ids):
        id_map[old] = str(NEW_ID_BASE + i)
    for n in id_map.values():
        assert n not in pf.doc, "新 fileID 撞号: " + n
    clone_subtree(pf, save, id_map)

    new_go = id_map[save]                    # collect_subtree 的第一个就是 GO
    new_rt = id_map[save_rt]
    new_child_rt = id_map[child_rt[0]]
    new_child_go = RE_GO_REF.search(pf.doc[new_child_rt]).group(1)
    new_text = None
    for c in RE_COMPONENT_REF.findall(pf.doc[new_child_go]):
        if is_text_comp(pf, c):
            new_text = c

    # ---- ② 覆盖字段 ----
    pf.set_field(new_go, "m_Name", TARGET_GO)
    pf.set_field(new_rt, "m_AnchorMin", ANCHOR_RESET_MIN_X)
    pf.set_field(new_rt, "m_AnchorMax", ANCHOR_RESET_MAX_X)
    pf.set_field(new_rt, "m_AnchoredPosition", BTN_POS)
    pf.set_field(new_rt, "m_SizeDelta", BTN_SIZE)
    assert new_text, "克隆出来的 Label 里找不到 Text 组件"
    pf.set_field(new_text, "m_Text", yaml_str(TARGET_TEXT))
    pf.set_field(new_text, "m_Color", TARGET_COLOR)

    # ---- ③ 三个键重新分列：保存收到左半，新建让出 1/6 ----
    pf.set_field(save_rt, "m_AnchorMax", ANCHOR_SAVE_MAX_X)
    pf.set_field(pf.rt_of_go(new_btn), "m_AnchorMax", ANCHOR_NEW_MAX_X)
    pf.set_field(pf.rt_of_go(delete), "m_AnchorMin", ANCHOR_DELETE_MIN_X)

    # ---- ④ 挂进 PagePrompt 的 m_Children：插在 DeletePromptBtn 之前（兄弟序=画面顺序）----
    kids = pf.children(page_rt)
    assert pf.rt_of_go(delete) in kids, "DeletePromptBtn 不在 PagePrompt 的子节点里"
    i = kids.index(pf.rt_of_go(delete))
    kids.insert(i, new_rt)
    pf.set_children(page_rt, kids)

    out = pf.text()
    if do_log:
        log("插入 %s：%s" % (TARGET_GO, "/".join(["BG", "PagePrompt", TARGET_GO])))
        log("对象数 %d → %d（+%d）" % (before_count, len(pf.order), len(pf.order) - before_count))
        log("列宽：Save 0..0.5 / New 0.5..2/3 / Reset 2/3..5/6 / Delete 5/6..1")
    verify(out)
    return out


def verify(text):
    """独立复核（重新解析产物，不信任 patch 的中间状态）。失败即抛 AssertionError。"""
    pf = Prefab(text)
    page = pf.find_go("PagePrompt")
    assert page, "复核失败：PagePrompt 不见了"
    page_rt = pf.rt_of_go(page)

    go = pf.find_go(TARGET_GO)
    assert go, "复核失败：ResetPromptBtn 不存在"
    rt = pf.rt_of_go(go)
    father = RE_FATHER_REF.search(pf.doc[rt]).group(1)
    assert father == page_rt, "复核失败：ResetPromptBtn 的父节点不是 PagePrompt"
    assert pf.children(page_rt).count(rt) == 1, "复核失败：PagePrompt.m_Children 里应恰好出现一次"
    kids = pf.children(page_rt)
    assert kids.index(rt) == kids.index(pf.rt_of_go(pf.find_go("DeletePromptBtn"))) - 1, \
        "复核失败：ResetPromptBtn 应紧排在 DeletePromptBtn 之前（兄弟序=画面顺序）"

    assert pf.field(rt, "m_AnchorMin") == ANCHOR_RESET_MIN_X, pf.field(rt, "m_AnchorMin")
    assert pf.field(rt, "m_AnchorMax") == ANCHOR_RESET_MAX_X, pf.field(rt, "m_AnchorMax")
    assert pf.field(rt, "m_AnchoredPosition") == BTN_POS
    assert pf.field(rt, "m_SizeDelta") == BTN_SIZE
    assert pf.field(pf.rt_of_go(pf.find_go("SavePromptBtn")), "m_AnchorMax") == ANCHOR_SAVE_MAX_X
    assert pf.field(pf.rt_of_go(pf.find_go("NewNpcBtn")), "m_AnchorMax") == ANCHOR_NEW_MAX_X
    assert pf.field(pf.rt_of_go(pf.find_go("DeletePromptBtn")), "m_AnchorMin") == ANCHOR_DELETE_MIN_X

    # 列宽契约（数值复核，不只比字符串）：四个键必须**首尾相接、不重叠、不留缝**，
    # 且底边锚定（y 都为 0）。锚点打错一位（0.6667 写成 0.6777）在实机上只是"按钮挤在一起"，
    # 肉眼未必立刻看出，所以在这里算一遍。
    def ax(fid, key):
        m = re.search(r"\{x: ([-\d.eE]+), y: ([-\d.eE]+)\}", pf.field(fid, key) or "")
        assert m, "锚点解析失败 %s.%s" % (fid, key)
        return float(m.group(1)), float(m.group(2))

    save_rt = pf.rt_of_go(pf.find_go("SavePromptBtn"))
    new_rt = pf.rt_of_go(pf.find_go("NewNpcBtn"))
    del_rt = pf.rt_of_go(pf.find_go("DeletePromptBtn"))
    cols = [
        ("Save", ax(save_rt, "m_AnchorMin"), ax(save_rt, "m_AnchorMax")),
        ("New", ax(new_rt, "m_AnchorMin"), ax(new_rt, "m_AnchorMax")),
        ("Reset", ax(rt, "m_AnchorMin"), ax(rt, "m_AnchorMax")),
        ("Delete", ax(del_rt, "m_AnchorMin"), ax(del_rt, "m_AnchorMax")),
    ]
    assert abs(cols[0][1][0] - 0.0) < 1e-9 and abs(cols[0][2][0] - 0.5) < 1e-6, "Save 应占左半 0..0.5"
    for i in (1, 2, 3):
        lo, hi = cols[i][1][0], cols[i][2][0]
        assert abs(hi - lo - 1.0 / 6.0) < 1e-5, "%s 列宽应约为 1/6（实际 %.6f）" % (cols[i][0], hi - lo)
        assert abs(cols[i - 1][2][0] - lo) < 1e-5, "%s 与 %s 之间有缝/重叠" % (cols[i - 1][0], cols[i][0])
    assert abs(cols[3][2][0] - 1.0) < 1e-9, "Delete 应顶到最右"
    for name, mn, mx in cols:
        assert abs(mn[1]) < 1e-9 and abs(mx[1]) < 1e-9, "%s 不是底边锚定（y 应为 0）" % name

    comps = RE_COMPONENT_REF.findall(pf.doc[go])
    kinds = [pf.cls(c) for c in comps]
    assert kinds.count("224") == 1, "应恰好一个 RectTransform"
    assert kinds.count("222") == 1, "应恰好一个 CanvasRenderer"
    assert kinds.count("114") == 2, "应恰好两个 MonoBehaviour（Image + Button）"
    for c in comps:
        assert RE_GO_REF.search(pf.doc[c]).group(1) == go, "组件 m_GameObject 未指向新按钮"
    btn = None
    for c in comps:
        if pf.cls(c) == "114" and pf.field(c, "m_Transition") is not None:
            btn = c
    assert btn, "找不到 Button 组件"
    graphic = re.search(r"\{fileID: (\d+)\}", pf.field(btn, "m_TargetGraphic")).group(1)
    assert RE_GO_REF.search(pf.doc[graphic]).group(1) == go, "Button.targetGraphic 未指向新按钮的 Image"

    label_go = RE_GO_REF.search(pf.doc[pf.children(rt)[0]]).group(1)
    label_txt = None
    for c in RE_COMPONENT_REF.findall(pf.doc[label_go]):
        if is_text_comp(pf, c):
            label_txt = c
    assert label_txt, "Label 上找不到 Text 组件"
    assert pf.field(label_txt, "m_Text") == yaml_str(TARGET_TEXT), pf.field(label_txt, "m_Text")
    assert pf.field(label_txt, "m_Color") == TARGET_COLOR, pf.field(label_txt, "m_Color")

    ids = [RE_HEAD.match(pf.doc[f]).group(2) for f in pf.order]
    assert len(ids) == len(set(ids)), "fileID 有重复"
    return True


def main(argv=None):
    _fix_console()
    ap = argparse.ArgumentParser(description="UIConfigAi.prefab 提示词页补「重置为默认」按钮（幂等）")
    ap.add_argument("--in", dest="inp", required=True, help="源 prefab")
    ap.add_argument("--out", dest="out", required=True, help="目标 prefab（可与 --in 相同=原地改）")
    ap.add_argument("--dry-run", action="store_true", help="只算不写")
    ap.add_argument("--no-backup", action="store_true",
                    help="原地改（--in == --out）时不写 .bak_promptreset 备份")
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
        shutil.copy2(outp, outp + ".bak_promptreset")
        log("== 备份 %s.bak_promptreset" % outp)
    with io.open(outp, "w", encoding="utf-8", newline="") as f:
        f.write(out)
    log("== 输出 %s (%d 字节)" % (outp, len(out.encode("utf-8"))))
    log("== 自校验通过（锚点/父子/组件/文案/颜色/fileID 唯一）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
