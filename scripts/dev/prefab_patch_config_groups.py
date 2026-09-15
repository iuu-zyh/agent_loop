# -*- coding: utf-8 -*-
"""
UIConfigAi.prefab —— PageLlm 页「分组标题 + 整页纵向滚动」改造（幂等 / 可重跑 / 纯文本手术）

改造前:
  BG/PageLlm(VerticalLayoutGroup) ── 14 个直接子节点 = 13 个参数行 + SaveLlmBtn
改造后:
  BG/PageLlm                          （本体不动，仍是 VLG）
    Scroll                            ← 新增 ScrollRect(h=0, v=1, movementType=Clamped)
      Viewport                        ← 新增 stretch 满父 + RectMask2D（照 BG/PagePrompt/FileScroll/Viewport）
        Content                       ← 新增 pivot(0.5,1) / anchor(0,1)-(1,1) / sizeDelta(0, 总高度)
          GroupHeader_Model  …        ← 新增 4 个组标题（克隆行内 Label 的 Text，字号略小/颜色偏灰）
          BaseUrlRow … SaveLlmBtn     ← 原 14 个节点**移动**（非复制）进来，内部结构/组件/fileID 全不变
          InitiativeEnabledRow …      ← 新增 5 个参数行（Input 克隆 PortRow / Toggle 克隆 LowHalveRow）

用法:
  python prefab_patch_config_groups.py --in <源 prefab> --out <目标 prefab> [--dry-run]
  --in/--out 可同路径（原地改）。真实游戏工程 / ui_preview 工程路径**默认拒写**，
  必须显式加 --allow-protected-out 才放行（防手滑写真文件）。

幂等:
  以 BG/PageLlm/Scroll 是否存在为分支开关——
    不存在 → 插入分支：新建 Scroll/Viewport/Content + 克隆新节点 + 搬移原节点；
    已存在 → 修正分支：只做「补齐缺失节点 / 去重 / 重挂父子 / 重排位置 / 修 ScrollRect 字段」，
             绝不重复插入。重复跑第二次，节点数完全不变。

对齐的 C# 契约（csharp/UI/AbConfigPanel.cs CollectRefs，已按新路径写好）:
  BG/PageLlm/Scroll                             → ScrollRect
  BG/PageLlm/Scroll/Viewport/Content/<Row>/Input    → InputField
  BG/PageLlm/Scroll/Viewport/Content/<Row>/ToggleBg → Toggle
"""
import argparse
import io
import os
import re
import sys

try:  # Windows 控制台/管道下也能打印中文
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
except Exception:
    pass

# ---------------------------------------------------------------- 常量 / 版式

ROW_H = 29.22353        # 参数行高（沿用现有值）
ROW_PITCH = 35.2235     # 参数行行距（= ROW_H + 6）
ROW_GAP = ROW_PITCH - ROW_H          # 6.0
GRP_H = 28.0            # 组标题高
GRP_GAP = 30.0          # 组标题与上下行间距
SAVE_GAP = 30.0         # SaveLlmBtn 与上一行间距（非参数行，按"块间隔"处理）
FIRST_Y = -20.6         # 第一个节点中心 y
BOTTOM_MARGIN = 12.0    # Content 底部余量（最后一行底部 + 余量 = Content 高）
ROW_X = 368.0           # 行中心 x（沿用现有值）
ROW_W = 728.0           # 行宽（沿用现有值）

SCROLL, VIEWPORT, CONTENT = "Scroll", "Viewport", "Content"

# PageLlm 原 14 个直接子节点（必须原样搬进 Content）
ORIGINAL_CHILDREN = [
    "BaseUrlRow", "ApiKeyRow", "ModelRow", "ImageRow", "PortRow", "TimeoutRow",
    "MinIntervalRow", "DailyChanceRow", "NpcCooldownDaysRow", "NpcCooldownRealRow",
    "LowThreshRow", "LowHalveRow", "CtxWindowRow", "SaveLlmBtn",
]

# 新增 5 个参数行: (节点名, 类型, Label 文本, Placeholder 文本, 数值类型)
#   ★09-14 补列★ 末位「数值类型」是事后补的，因为缺它造成了一个实机 bug：
#     input 行统一克隆自 PortRow，而 PortRow 是**整数框**（m_ContentType=2 / m_CharacterValidation=1），
#     克隆会把整数校验一并继承 —— 于是 RetainRatioRow / ThresholdRatioRow 这两个**小数**框
#     拿到了整数校验，后果两条：
#       ① 小数点打不进去（Integer 校验器对 '.' 返回 0 = 丢弃）；
#       ② 更隐蔽：uGUI 的 InputField.SetText 在 characterValidation != None 时**逐字符**过同一个
#          校验器，get_config 回填的 "0.16" 被滤成 "016" → 保存时按 16 解析 → 撞 0.05-0.6 越界，
#          表现为「保留原文比例需 0.05-0.6（默认 0.16）」—— 配置**改不动也存不下**。
#     既存预制件由 scripts/dev/prefab_patch_decimal_inputs.py 修（AB 工程 + ui_preview 双份）；
#     这一列是防它再犯。C# 侧 ConfigPresenter.EnsureDecimalInput() 是第三道兜底（老 AB 免重打）。
NEW_ROWS = [
    ("InitiativeEnabledRow", "toggle", "主动互动总开关", None, None),
    ("CompactionEnabledRow", "toggle", "自动压缩", None, None),
    ("RetainRatioRow", "input", "保留原文比例", "0.16", "decimal"),
    ("ThresholdRatioRow", "input", "自动压缩阈值", "0.8", "decimal"),
    ("PortraitRow", "toggle", "显示立绘", None, None),
    # ★09-14★ headers 行：kind="text"（普通文本输入，克隆 ModelRow），可空。
    #   放在【模型与生成】组末尾 —— 它和 base_url/api_key/model 是同一个问题「怎么连上这个模型」。
    #   rval=None 表示不做数值/校验类型改写（Standard 就是克隆源自带的值）。
    ("HeadersRow", "text", "自定义请求头（可空）", "x-opencode-session: agent-loop", None),
    # ★09-14 晚★ AI 时间与重试四行（llm.timeout / timeout_nonstream / total_budget / retries）。
    #   rval="decimal" 必需 —— 克隆源 PortRow 是整数框，不改校验类型的话小数点打不进去，
    #   而且回填 "45.0" 会被逐字符过滤成 "450"（见本表上方那段 09-14 补列说明的实机 bug）。
    #   retries 是真整数，rval=None（沿用 PortRow 自带的整数校验）才是对的。
    #   ⚠ 这四行目前**还没进 AB**：重打 AB 需要 Unity 批处理持有效许可证，本机实测被挡
    #     （`BatchMode: Unity has not been activated with a valid License.`）。
    #     在那之前由 C# 侧 AbConfigPanel.EnsureTimeRows() 运行期现搭（本工程既有同款兜底）。
    #     等哪天 Unity 可用了，跑本脚本 + 重打 AB，兜底会自动不再触发（逐行判断，不会出现两份）。
    ("LlmTimeoutRow", "input", "AI 单次超时秒·流式（即时生效）", "45", "decimal"),
    ("LlmTimeoutNsRow", "input", "AI 单次超时秒·压缩（即时生效）", "180", "decimal"),
    ("LlmBudgetRow", "input", "AI 总等待上限秒（即时生效）", "100", "decimal"),
    ("LlmRetriesRow", "input", "AI 失败重试次数（即时生效）", "1", None),
]
ROW_SPEC = {n: (k, t, p, v) for n, k, t, p, v in NEW_ROWS}

# 新增按钮: (节点名, 文本)。克隆源统一是 SaveLlmBtn（同款 clickable 底 + Label 子节点）。
#   TestLlmBtn（09-14）：拿表单当前值真调一次模型，结果显示在底部状态条。
NEW_BUTTONS = [
    ("TestLlmBtn", "测试连接（不保存，直接试一次）"),
]
BUTTON_TEXT = dict(NEW_BUTTONS)

# 行克隆源：三种 kind 各抄一个"已经长对了"的同类节点。
#   input  → PortRow（整数框；小数行由 rval="decimal" 再改校验类型）
#   text   → ModelRow（Standard 文本框，headers 这类"填字符串"的行抄它才对）
#   toggle → LowHalveRow
ROW_CLONE_SRC = {"input": "PortRow", "text": "ModelRow", "toggle": "LowHalveRow"}

# 新增 4 个组标题: (节点名, 文本)
HEADERS = [
    ("GroupHeader_Model", "模型与生成"),
    ("GroupHeader_Initiative", "主动互动"),
    ("GroupHeader_Memory", "记忆与压缩"),
    ("GroupHeader_Advanced", "高级"),
]
HEADER_TEXT = dict(HEADERS)

HEADER_FONT_SIZE = 13            # 比参数标签(15)略小
HEADER_COLOR = "{r: 0.6, g: 0.62, b: 0.7, a: 1}"   # 与 Placeholder 同款灰

# Content 内最终顺序（从上到下）。★ PortraitRow 不在任务书给出的顺序表里（规格缺口），
#   暂放【高级】组末尾（config ui.portraits_enabled 是显示/性能开关，无专属分组）；
#   要挪位置只改这一张表即可。
ORDER = [
    ("GroupHeader_Model", "header"),
    ("BaseUrlRow", "row"), ("ApiKeyRow", "row"), ("ModelRow", "row"),
    ("HeadersRow", "row"),                        # ★09-14 与上面三项同组（"怎么连上模型"）
    ("ImageRow", "row"),
    ("GroupHeader_Initiative", "header"),
    ("InitiativeEnabledRow", "row"), ("DailyChanceRow", "row"),
    ("NpcCooldownRealRow", "row"), ("NpcCooldownDaysRow", "row"),
    ("GroupHeader_Memory", "header"),
    ("CompactionEnabledRow", "row"), ("CtxWindowRow", "row"),
    ("RetainRatioRow", "row"), ("ThresholdRatioRow", "row"),
    ("GroupHeader_Advanced", "header"),
    ("MinIntervalRow", "row"), ("LowThreshRow", "row"), ("LowHalveRow", "row"),
    ("PortRow", "row"), ("TimeoutRow", "row"),
    ("LlmTimeoutRow", "row"), ("LlmTimeoutNsRow", "row"),   # ★09-14 晚：与 TimeoutRow 同组
    ("LlmBudgetRow", "row"), ("LlmRetriesRow", "row"),      #   （都是"等多久/试几次"）
    ("PortraitRow", "row"),                       # ★ 规格缺口，见上
    ("SaveLlmBtn", "button"),
    ("TestLlmBtn", "button"),                     # ★09-14 保存下面一行（"填完先试，试通了再存"）
]
TARGET_NAMES = [n for n, _ in ORDER]

# 组件 guid（Unity 内置 UGUI）
GUID_SCROLLRECT = "1aa08ab6e0800fa44ae55d278d1423e3"
GUID_RECTMASK2D = "3312d7739989d2b4e91e6319e9a96d76"
GUID_LAYOUTELEM = "306cc8c2b49d7114eaa3623786fc2126"   # LayoutElement（行节点上用的同一个）
GUID_TEXT = "5f7201a12d95ffc409449d95f23cf332"

# 真实工程路径：默认拒写（防手滑）
PROTECTED_OUT = [
    "/mnt/e/SteamLibrary/steamapps/common/鬼谷八荒/Mod/modFQA/资源修改教程/",
    "e:\\steamlibrary\\steamapps\\common\\鬼谷八荒\\mod\\modfqa\\资源修改教程\\",
    "/mnt/f/agent_loop/ui_preview/",
    "f:\\agent_loop\\ui_preview\\",
]


def log(*a):
    print(*a)


# ---------------------------------------------------------------- 文本工具

DOC_SPLIT = re.compile(r"(?m)^(?=--- !u!)")
DOC_HEAD = re.compile(r"^--- !u!(\d+) &(\d+)$", re.M)
CHILD_LINE = re.compile(r"^  - \{fileID: (\d+)\}$", re.M)


def split_prefab(text):
    parts = DOC_SPLIT.split(text)
    header, docs = parts[0], parts[1:]
    return header, [d if d.endswith("\n") else d + "\n" for d in docs]


def uesc(s):
    """非 ASCII 字符 → \\uXXXX（沿用 prefab 里中文的转义写法）"""
    return "".join(c if ord(c) < 0x80 else "\\u%04X" % ord(c) for c in s)


def yaml_str(s):
    """按 Unity 的写法输出字符串标量：含反斜杠就加双引号。"""
    return '"%s"' % s if "\\" in s or s != s.strip() else s


def fmt(v):
    """浮点 → 紧凑十进制（29.22353 / -20.6 / 728）"""
    t = ("%.6f" % float(v)).rstrip("0").rstrip(".")
    return t if t not in ("", "-0") else "0"


def num(v):
    m = re.search(r"-?[\d.]+", v)
    return float(m.group(0)) if m else 0.0


class IdAlloc(object):
    """新 fileID 分配器：8831 + 15 位序号（19 位，< int64 max；全文件查重）"""

    def __init__(self, used):
        self.used = set(used)
        self.i = 0

    def next(self):
        while True:
            self.i += 1
            cand = "8831%015d" % self.i
            if cand not in self.used:
                self.used.add(cand)
                return cand


class Ctx(object):
    """prefab 的内存模型：doc 文本 + 父子关系（父子关系只在最后统一回写）"""

    def __init__(self, text):
        self.header, docs = split_prefab(text)
        self.text_of, self.order = {}, []
        for d in docs:
            fid = DOC_HEAD.match(d).group(2)
            self.text_of[fid] = d
            self.order.append(fid)
        self.kids = {}
        self.father = {}
        for fid, d in self.text_of.items():
            if DOC_HEAD.match(d).group(1) != "224":
                continue
            f = re.search(r"(?m)^  m_Father: \{fileID: (\d+)\}$", d)
            self.father[fid] = f.group(1) if f else "0"
            ch = re.search(r"(?m)^  m_Children:.*\n(?:  - \{fileID: \d+\}\n)*", d)
            self.kids[fid] = CHILD_LINE.findall(ch.group(0)) if ch else []
        used = set(re.findall(r"&(\d+)", text)) | set(re.findall(r"fileID: (\d+)", text))
        self.alloc = IdAlloc(used)
        self.dirty = set()      # 需要回写 m_Children 的父节点
        self.created = []       # 本次新建的节点名
        self.cloned = []        # 本次克隆的节点名
        self.moved = []         # 本次搬移的节点名

    # ---- 基础读写 ----
    def cls(self, fid):
        return DOC_HEAD.match(self.text_of[fid]).group(1)

    def get(self, fid):
        return self.text_of[fid]

    def put(self, fid, text):
        self.text_of[fid] = text

    def add_doc(self, text):
        fid = DOC_HEAD.match(text).group(2)
        assert fid not in self.text_of, "fileID 冲突: " + fid
        self.text_of[fid] = text
        self.order.append(fid)
        return fid

    def set_field(self, fid, key, value, indent="  "):
        d = self.text_of[fid]
        # 用 lambda 做替换体：value 里含 \uXXXX 转义，不能被 re 当模板解析
        rep = "%s%s: %s" % (indent, key, value)
        new, n = re.subn(r"(?m)^%s%s: .*$" % (indent, re.escape(key)),
                         lambda m: rep, d, count=1)
        assert n == 1, "字段写入失败 %s.%s" % (fid, key)
        self.text_of[fid] = new

    def field(self, fid, key, indent="  "):
        m = re.search(r"(?m)^%s%s: (.*)$" % (indent, re.escape(key)), self.text_of[fid])
        return m.group(1).strip() if m else None

    def go_of(self, rt):
        return re.search(r"(?m)^  m_GameObject: \{fileID: (\d+)\}$", self.text_of[rt]).group(1)

    def go_name(self, go):
        return self.field(go, "m_Name") or "?"

    def name_of_rt(self, rt):
        return self.go_name(self.go_of(rt))

    def components(self, go):
        return re.findall(r"(?m)^  - component: \{fileID: (\d+)\}$", self.text_of[go])

    def comp_go(self, comp):
        m = re.search(r"(?m)^  m_GameObject: \{fileID: (\d+)\}$", self.text_of[comp])
        return m.group(1) if m else None

    def script_guid(self, comp):
        m = re.search(r"(?m)^  m_Script: \{fileID: \d+, guid: ([0-9a-f]+)", self.text_of[comp])
        return m.group(1) if m else None

    def comp_with_guid(self, go, guid):
        for c in self.components(go):
            if self.script_guid(c) == guid:
                return c
        return None

    # ---- 树操作 ----
    def children(self, rt):
        return list(self.kids.get(rt, []))

    def child_by_name(self, rt, name):
        for c in self.kids.get(rt, []):
            if self.name_of_rt(c) == name:
                return c
        return None

    def walk(self, rt):
        yield rt
        for c in self.kids.get(rt, []):
            for x in self.walk(c):
                yield x

    def find_by_name(self, rt, name):
        for x in self.walk(rt):
            if x != rt and self.name_of_rt(x) == name:
                return x
        return None

    def find_all_by_name(self, rt, name):
        return [x for x in self.walk(rt) if x != rt and self.name_of_rt(x) == name]

    def find_by_path(self, root, path):
        cur = root
        for seg in path.split("/"):
            cur = self.child_by_name(cur, seg)
            if cur is None:
                return None
        return cur

    def detach(self, rt):
        f = self.father.get(rt)
        if f and f in self.kids and rt in self.kids[f]:
            self.kids[f].remove(rt)
            self.dirty.add(f)
        self.father[rt] = "0"

    def attach(self, rt, parent, index=None):
        self.detach(rt)
        lst = self.kids.setdefault(parent, [])
        if index is None or index >= len(lst):
            lst.append(rt)
        else:
            lst.insert(index, rt)
        self.father[rt] = parent
        self.dirty.add(parent)
        self.set_field(rt, "m_Father", "{fileID: %s}" % parent)

    def set_children(self, rt, fids):
        self.kids[rt] = list(fids)
        self.dirty.add(rt)
        for f in fids:
            self.father[f] = rt
            self.set_field(f, "m_Father", "{fileID: %s}" % rt)

    # ---- 子节点收集 / 克隆 ----
    def subtree_ids(self, root_rt):
        out, seen = [], set()

        def visit(rt):
            if rt in seen:
                return
            seen.add(rt)
            out.append(rt)
            go = self.go_of(rt)
            if go not in seen:
                seen.add(go)
                out.append(go)
            for c in self.components(go):
                if c not in seen:
                    seen.add(c)
                    out.append(c)
            for ch in self.kids.get(rt, []):
                visit(ch)

        visit(root_rt)
        return out

    def clone(self, root_rt, new_name):
        """深拷贝子树：全部新 fileID + 内部引用重定向；返回 (新 RT, 旧→新 映射)"""
        ids = self.subtree_ids(root_rt)
        old_cls = {fid: self.cls(fid) for fid in ids}
        mapping = {fid: self.alloc.next() for fid in ids}
        for fid in ids:
            d = self.text_of[fid]
            d = re.sub(r"fileID: (\d+)",
                       lambda m: "fileID: " + mapping.get(m.group(1), m.group(1)), d)
            d = DOC_HEAD.sub(lambda m: "--- !u!%s &%s" % (m.group(1), mapping[fid]), d, count=1)
            self.add_doc(d)
        # 克隆体的父子关系也要进内存模型（子树内部全量重映射）
        for fid in ids:
            if old_cls[fid] != "224":
                continue
            ni = mapping[fid]
            self.kids[ni] = [mapping[c] for c in self.kids.get(fid, [])]
            for c in self.kids[ni]:
                self.father[c] = ni
        new_rt = mapping[root_rt]
        new_go = self.go_of(new_rt)
        self.set_field(new_go, "m_Name", new_name)
        self.father[new_rt] = "0"          # 根：稍后由 attach() 挂到目标父节点
        self.cloned.append(new_name)
        return new_rt, mapping

    def remove_node(self, rt):
        """删掉一棵子树（及其对外引用），用于去重"""
        ids = set(self.subtree_ids(rt))
        self.detach(rt)
        for f in list(self.kids):
            if f in ids:
                del self.kids[f]
        for f in list(self.father):
            if f in ids:
                del self.father[f]
        for fid in list(self.text_of):
            if fid in ids:
                del self.text_of[fid]
                self.order.remove(fid)
                continue
            d = self.text_of[fid]
            if any(("fileID: " + i) in d for i in ids):
                d2 = re.sub(r"\{fileID: (%s)\}" % "|".join(ids), "{fileID: 0}", d)
                self.text_of[fid] = d2
        for f in list(self.kids):
            if any(i in ids for i in self.kids[f]):
                self.kids[f] = [i for i in self.kids[f] if i not in ids]
                self.dirty.add(f)

    # ---- 回写 ----
    def render(self):
        for rt in sorted(self.dirty):
            fids = self.kids.get(rt, [])
            blk = "  m_Children:" + (" []\n" if not fids else
                                     "\n" + "".join("  - {fileID: %s}\n" % f for f in fids))
            d, n = re.subn(r"(?m)^  m_Children:.*\n(?:  - \{fileID: \d+\}\n)*",
                           lambda m: blk, self.text_of[rt], count=1)
            assert n == 1, "m_Children 回写失败: " + rt
            self.text_of[rt] = d
        # m_RootOrder 与 m_Children 序号对齐
        for rt in sorted(self.dirty):
            for i, f in enumerate(self.kids.get(rt, [])):
                self.set_field(f, "m_RootOrder", str(i))
        return self.header + "".join(self.text_of[f] for f in self.order)


# ---------------------------------------------------------------- 版式计算

def layout(items):
    """items: [(name, height)] → [(name, height, y)]；首个 y = FIRST_Y，其余按间距下推"""
    out, y, prev_h = [], None, None
    for i, (name, h) in enumerate(items):
        if i == 0:
            y = FIRST_Y
        else:
            gap = ROW_GAP if (h == ROW_H and prev_h == ROW_H) else GRP_GAP
            y = y - (prev_h / 2.0 + gap + h / 2.0)
        out.append((name, h, y))
        prev_h = h
    return out


# ---------------------------------------------------------------- 新建节点模板

def tmpl_go(fid, name, comps):
    return ("--- !u!1 &%s\n"
            "GameObject:\n"
            "  m_ObjectHideFlags: 0\n"
            "  m_CorrespondingSourceObject: {fileID: 0}\n"
            "  m_PrefabInstance: {fileID: 0}\n"
            "  m_PrefabAsset: {fileID: 0}\n"
            "  serializedVersion: 6\n"
            "  m_Component:\n"
            "%s"
            "  m_Layer: 0\n"
            "  m_Name: %s\n"
            "  m_TagString: Untagged\n"
            "  m_Icon: {fileID: 0}\n"
            "  m_NavMeshLayer: 0\n"
            "  m_StaticEditorFlags: 0\n"
            "  m_IsActive: 1\n") % (
        fid, "".join("  - component: {fileID: %s}\n" % c for c in comps), name)


def tmpl_rt(fid, go, father, amin, amax, pos, size, pivot):
    return ("--- !u!224 &%s\n"
            "RectTransform:\n"
            "  m_ObjectHideFlags: 0\n"
            "  m_CorrespondingSourceObject: {fileID: 0}\n"
            "  m_PrefabInstance: {fileID: 0}\n"
            "  m_PrefabAsset: {fileID: 0}\n"
            "  m_GameObject: {fileID: %s}\n"
            "  m_LocalRotation: {x: 0, y: 0, z: 0, w: 1}\n"
            "  m_LocalPosition: {x: 0, y: 0, z: 0}\n"
            "  m_LocalScale: {x: 1, y: 1, z: 1}\n"
            "  m_Children: []\n"
            "  m_Father: {fileID: %s}\n"
            "  m_RootOrder: 0\n"
            "  m_LocalEulerAnglesHint: {x: 0, y: 0, z: 0}\n"
            "  m_AnchorMin: {x: %s, y: %s}\n"
            "  m_AnchorMax: {x: %s, y: %s}\n"
            "  m_AnchoredPosition: {x: %s, y: %s}\n"
            "  m_SizeDelta: {x: %s, y: %s}\n"
            "  m_Pivot: {x: %s, y: %s}\n") % (
        fid, go, father, amin[0], amin[1], amax[0], amax[1],
        pos[0], pos[1], size[0], size[1], pivot[0], pivot[1])


def tmpl_scrollrect(fid, go, content, viewport):
    """字段取自 BG/PagePrompt/FileScroll 上那个 ScrollRect（只差 content/viewport 指向）"""
    return ("--- !u!114 &%s\n"
            "MonoBehaviour:\n"
            "  m_ObjectHideFlags: 0\n"
            "  m_CorrespondingSourceObject: {fileID: 0}\n"
            "  m_PrefabInstance: {fileID: 0}\n"
            "  m_PrefabAsset: {fileID: 0}\n"
            "  m_GameObject: {fileID: %s}\n"
            "  m_Enabled: 1\n"
            "  m_EditorHideFlags: 0\n"
            "  m_Script: {fileID: 11500000, guid: %s, type: 3}\n"
            "  m_Name:\n"
            "  m_EditorClassIdentifier:\n"
            "  m_Content: {fileID: %s}\n"
            "  m_Horizontal: 0\n"
            "  m_Vertical: 1\n"
            "  m_MovementType: 2\n"
            "  m_Elasticity: 0.1\n"
            "  m_Inertia: 1\n"
            "  m_DecelerationRate: 0.135\n"
            # 滚轮灵敏度：FileScroll 用的是 1，但那一页只有 100px 内容、几乎不用滚；
            # 配置页内容 1014px / 视口 504px（要滚 510px），灵敏度 1 得拨几十下滚轮才到底，
            # 且行内全是 InputField（拖拽会滚页而非选文本）→ 取 PromptInput 那次的 20。
            "  m_ScrollSensitivity: 20\n"
            "  m_Viewport: {fileID: %s}\n"
            "  m_HorizontalScrollbar: {fileID: 0}\n"
            "  m_VerticalScrollbar: {fileID: 0}\n"
            "  m_HorizontalScrollbarVisibility: 0\n"
            "  m_VerticalScrollbarVisibility: 0\n"
            "  m_HorizontalScrollbarSpacing: 0\n"
            "  m_VerticalScrollbarSpacing: 0\n"
            "  m_OnValueChanged:\n"
            "    m_PersistentCalls:\n"
            "      m_Calls: []\n") % (fid, go, GUID_SCROLLRECT, content, viewport)


def tmpl_rectmask(fid, go):
    return ("--- !u!114 &%s\n"
            "MonoBehaviour:\n"
            "  m_ObjectHideFlags: 0\n"
            "  m_CorrespondingSourceObject: {fileID: 0}\n"
            "  m_PrefabInstance: {fileID: 0}\n"
            "  m_PrefabAsset: {fileID: 0}\n"
            "  m_GameObject: {fileID: %s}\n"
            "  m_Enabled: 1\n"
            "  m_EditorHideFlags: 0\n"
            "  m_Script: {fileID: 11500000, guid: %s, type: 3}\n"
            "  m_Name:\n"
            "  m_EditorClassIdentifier:\n"
            "  m_Padding: {x: 0, y: 0, z: 0, w: 0}\n"
            "  m_Softness: {x: 0, y: 0}\n") % (fid, go, GUID_RECTMASK2D)


def tmpl_canvasrenderer(fid, go):
    return ("--- !u!222 &%s\n"
            "CanvasRenderer:\n"
            "  m_ObjectHideFlags: 0\n"
            "  m_CorrespondingSourceObject: {fileID: 0}\n"
            "  m_PrefabInstance: {fileID: 0}\n"
            "  m_PrefabAsset: {fileID: 0}\n"
            "  m_GameObject: {fileID: %s}\n"
            "  m_CullTransparentMesh: 1\n") % (fid, go)


def tmpl_layout_elem(fid, go, ignore=1, pref_h=-1):
    return ("--- !u!114 &%s\n"
            "MonoBehaviour:\n"
            "  m_ObjectHideFlags: 0\n"
            "  m_CorrespondingSourceObject: {fileID: 0}\n"
            "  m_PrefabInstance: {fileID: 0}\n"
            "  m_PrefabAsset: {fileID: 0}\n"
            "  m_GameObject: {fileID: %s}\n"
            "  m_Enabled: 1\n"
            "  m_EditorHideFlags: 0\n"
            "  m_Script: {fileID: 11500000, guid: %s, type: 3}\n"
            "  m_Name:\n"
            "  m_EditorClassIdentifier:\n"
            "  m_IgnoreLayout: %d\n"
            "  m_MinWidth: -1\n"
            "  m_MinHeight: -1\n"
            "  m_PreferredWidth: -1\n"
            "  m_PreferredHeight: %s\n"
            "  m_FlexibleWidth: -1\n"
            "  m_FlexibleHeight: -1\n"
            "  m_LayoutPriority: 1\n") % (fid, go, GUID_LAYOUTELEM, ignore, pref_h)


# ---------------------------------------------------------------- 主流程

def patch(text, do_log=True):
    cx = Ctx(text)
    rep = []

    root = None
    for fid in cx.order:                       # 找根（m_Father == 0 的 RT）
        if cx.cls(fid) == "224" and cx.father.get(fid) == "0":
            root = fid
            break
    assert root, "找不到 prefab 根 RectTransform"
    bg = cx.child_by_name(root, "BG")
    assert bg, "找不到 BG"
    page_llm = cx.child_by_name(bg, "PageLlm")
    assert page_llm, "找不到 BG/PageLlm"
    page_llm_go = cx.go_of(page_llm)

    # PageLlm 上的 VerticalLayoutGroup（子节点会被它自动排布 → Scroll 必须 ignoreLayout）
    vlg = None
    for c in cx.components(page_llm_go):
        g = cx.script_guid(c)
        if g and g.startswith("59f81469"):
            vlg = c
    rep.append("PageLlm VerticalLayoutGroup = %s" % ("有(需 ignoreLayout)" if vlg else "无"))

    scroll = cx.child_by_name(page_llm, SCROLL)
    inserted = scroll is None
    rep.append("分支 = %s" % ("插入（首次）" if inserted else "修正/重排（已存在 Scroll）"))

    # ---- 1) 三个容器 ----
    if inserted:
        s_go, s_rt, s_sr, s_le = [cx.alloc.next() for _ in range(4)]
        v_go, v_rt, v_mask, v_cr = [cx.alloc.next() for _ in range(4)]
        c_go, c_rt = [cx.alloc.next() for _ in range(2)]
        cx.add_doc(tmpl_go(s_go, SCROLL, [s_rt, s_sr, s_le]))
        cx.add_doc(tmpl_rt(s_rt, s_go, page_llm, ("0", "0"), ("1", "1"),
                           ("0", "0"), ("0", "0"), ("0.5", "0.5")))
        cx.add_doc(tmpl_scrollrect(s_sr, s_go, c_rt, v_rt))
        # VLG(PageLlm) 会把 Scroll 当普通子节点算尺寸（ScrollRect 的 preferred 全 -1 → 高 0）
        # → 挂 LayoutElement(ignoreLayout) 让 VLG 完全跳过它，保住自身的 stretch 锚点。
        cx.add_doc(tmpl_layout_elem(s_le, s_go, ignore=1))
        cx.add_doc(tmpl_go(v_go, VIEWPORT, [v_rt, v_mask, v_cr]))
        cx.add_doc(tmpl_rt(v_rt, v_go, s_rt, ("0", "0"), ("1", "1"),
                           ("0", "0"), ("0", "0"), ("0.5", "0.5")))
        cx.add_doc(tmpl_rectmask(v_mask, v_go))
        cx.add_doc(tmpl_canvasrenderer(v_cr, v_go))
        cx.add_doc(tmpl_go(c_go, CONTENT, [c_rt]))
        cx.add_doc(tmpl_rt(c_rt, c_go, v_rt, ("0", "1"), ("1", "1"),
                           ("0", "0"), ("0", "0"), ("0.5", "1")))
        cx.set_children(page_llm, cx.children(page_llm) + [s_rt])   # 原 14 个子节点稍后搬走
        cx.set_children(s_rt, [v_rt])
        cx.set_children(v_rt, [c_rt])
        cx.set_children(c_rt, [])
        cx.created.append("Scroll/Viewport/Content")
        scroll, viewport, content = s_rt, v_rt, c_rt
    else:
        viewport = cx.child_by_name(scroll, VIEWPORT)
        if viewport is None:
            v_go, v_rt, v_mask, v_cr = [cx.alloc.next() for _ in range(4)]
            cx.add_doc(tmpl_go(v_go, VIEWPORT, [v_rt, v_mask, v_cr]))
            cx.add_doc(tmpl_rt(v_rt, v_go, scroll, ("0", "0"), ("1", "1"),
                               ("0", "0"), ("0", "0"), ("0.5", "0.5")))
            cx.add_doc(tmpl_rectmask(v_mask, v_go))
            cx.add_doc(tmpl_canvasrenderer(v_cr, v_go))
            cx.set_children(scroll, [v_rt])
            viewport = v_rt
            cx.created.append("Viewport")
        content = cx.child_by_name(viewport, CONTENT)
        if content is None:
            c_go, c_rt = [cx.alloc.next() for _ in range(2)]
            cx.add_doc(tmpl_go(c_go, CONTENT, [c_rt]))
            cx.add_doc(tmpl_rt(c_rt, c_go, viewport, ("0", "1"), ("1", "1"),
                               ("0", "0"), ("0", "0"), ("0.5", "1")))
            cx.set_children(viewport, [c_rt])
            content = c_rt
            cx.created.append("Content")

    # 容器本体参数（幂等重写）
    cx.set_field(scroll, "m_AnchorMin", "{x: 0, y: 0}")
    cx.set_field(scroll, "m_AnchorMax", "{x: 1, y: 1}")
    cx.set_field(scroll, "m_AnchoredPosition", "{x: 0, y: 0}")
    cx.set_field(scroll, "m_SizeDelta", "{x: 0, y: 0}")
    cx.set_field(scroll, "m_Pivot", "{x: 0.5, y: 0.5}")
    cx.set_field(viewport, "m_AnchorMin", "{x: 0, y: 0}")
    cx.set_field(viewport, "m_AnchorMax", "{x: 1, y: 1}")
    cx.set_field(viewport, "m_AnchoredPosition", "{x: 0, y: 0}")
    cx.set_field(viewport, "m_SizeDelta", "{x: 0, y: 0}")
    cx.set_field(viewport, "m_Pivot", "{x: 0.5, y: 0.5}")
    cx.set_field(content, "m_AnchorMin", "{x: 0, y: 1}")
    cx.set_field(content, "m_AnchorMax", "{x: 1, y: 1}")
    cx.set_field(content, "m_AnchoredPosition", "{x: 0, y: 0}")
    cx.set_field(content, "m_Pivot", "{x: 0.5, y: 1}")

    # ScrollRect 引用 + 开关（幂等重写）
    scroll_go = cx.go_of(scroll)
    sr = cx.comp_with_guid(scroll_go, GUID_SCROLLRECT)
    if sr is None:                                   # 极端情况：Scroll 存在但没 ScrollRect
        sr = cx.alloc.next()
        cx.add_doc(tmpl_scrollrect(sr, scroll_go, content, viewport))
        d = cx.text_of[scroll_go].replace(
            "  - component: {fileID: %s}\n" % cx.go_of(scroll),
            "  - component: {fileID: %s}\n  - component: {fileID: %s}\n" % (cx.go_of(scroll), sr), 1)
        cx.text_of[scroll_go] = d
        cx.created.append("ScrollRect")
    cx.set_field(sr, "m_Content", "{fileID: %s}" % content)
    cx.set_field(sr, "m_Viewport", "{fileID: %s}" % viewport)
    cx.set_field(sr, "m_Horizontal", "0")
    cx.set_field(sr, "m_Vertical", "1")
    cx.set_field(sr, "m_MovementType", "2")

    # ---- 2a) 先把 PageLlm 原有的 14 个直接子节点整体搬进 Content ----
    #     （先搬再克隆：克隆源 Label/PortRow/LowHalveRow 都在这些行里）
    for name in ORIGINAL_CHILDREN:
        if cx.child_by_name(content, name) is not None:
            continue                                 # 已在 Content 下（重跑/修正分支）
        found = cx.find_by_name(page_llm, name)
        if found is None and not inserted:
            rep.append("警告：原节点 %s 在 PageLlm 子树里找不到" % name)
            continue
        if found is not None:
            cx.attach(found, content)
            cx.moved.append(name)

    # ---- 2b) 23 个目标节点：补齐（克隆）+ 逐个定位 ----
    for name, kind in ORDER:
        cur = cx.child_by_name(content, name)
        if cur is None:
            found = cx.find_by_name(page_llm, name)
            if found is not None:                    # 还在 PageLlm 下 → 搬进来
                cx.attach(found, content)
                cx.moved.append(name)
                cur = found
            else:                                    # 不存在 → 克隆模板
                if kind == "header":
                    src = cx.find_by_name(content, "BaseUrlRow")
                    assert src, "缺少克隆源 BaseUrlRow"
                    lab = cx.child_by_name(src, "Label")
                    assert lab, "缺少克隆源 BaseUrlRow/Label"
                    cur, mapping = cx.clone(lab, name)
                    text_comp = None
                    for c in cx.components(cx.go_of(cur)):
                        if "m_FontData" in cx.text_of[c]:
                            text_comp = c
                    assert text_comp, "克隆出的组标题没有 Text 组件"
                    cx.set_field(text_comp, "m_FontSize", str(HEADER_FONT_SIZE), indent="    ")
                    cx.set_field(text_comp, "m_Color", HEADER_COLOR)
                    cx.set_field(text_comp, "m_Text", yaml_str(uesc(HEADER_TEXT[name])))
                elif kind == "row":
                    rk, rlabel, rph, rval = ROW_SPEC[name]
                    src = cx.find_by_name(content, ROW_CLONE_SRC[rk])
                    assert src, "缺少克隆源 %s（kind=%s）" % (ROW_CLONE_SRC[rk], rk)
                    cur, mapping = cx.clone(src, name)
                    lab = cx.child_by_name(cur, "Label")
                    lc = [c for c in cx.components(cx.go_of(lab)) if "m_FontData" in cx.text_of[c]][0]
                    cx.set_field(lc, "m_Text", yaml_str(uesc(rlabel)))
                    holder = cx.child_by_name(cur, "Input")
                    if rph is not None:
                        ph = cx.child_by_name(holder, "Placeholder")
                        pc = [c for c in cx.components(cx.go_of(ph))
                              if "m_FontData" in cx.text_of[c]][0]
                        cx.set_field(pc, "m_Text", yaml_str(uesc(rph)))
                    # ★09-14★ 克隆源 PortRow 是整数框，小数行必须显式改回小数校验，
                    #   否则小数点被吞（详见 NEW_ROWS 上方注释）。三项一起改才是完整的
                    #   DecimalNumber：uGUI 的 EnforceContentType 在运行时也是这么配套设的。
                    if rval == "decimal" and holder is not None:
                        ic = [c for c in cx.components(cx.go_of(holder))
                              if "m_ContentType" in cx.text_of[c]][0]
                        cx.set_field(ic, "m_ContentType", "3")           # DecimalNumber
                        cx.set_field(ic, "m_KeyboardType", "2")          # TouchScreenKeyboardType.DecimalPad
                        cx.set_field(ic, "m_CharacterValidation", "2")   # CharacterValidation.Decimal
                elif kind == "button":
                    # ★09-14★ 新增按钮：抄 SaveLlmBtn（同款可点底 + Label 子节点），只改 Label 文本。
                    src = cx.find_by_name(content, "SaveLlmBtn")
                    assert src, "缺少克隆源 SaveLlmBtn"
                    cur, mapping = cx.clone(src, name)
                    lab = cx.child_by_name(cur, "Label")
                    assert lab, "克隆出的按钮没有 Label 子节点"
                    bc = [c for c in cx.components(cx.go_of(lab))
                          if "m_FontData" in cx.text_of[c]][0]
                    cx.set_field(bc, "m_Text", yaml_str(uesc(BUTTON_TEXT[name])))
                else:
                    raise AssertionError("未知节点类型: " + name)
                cx.attach(cur, content)
        # 位置（幂等：每次重算重写）
        cx.set_field(cur, "m_AnchorMin", "{x: 0, y: 1}")
        cx.set_field(cur, "m_AnchorMax", "{x: 0, y: 1}")

    # ---- 2c) 文本归一（★2026-09-14 新增★）----
    # 为什么必须有这一步：上面的 2b 只在「节点不存在」时才克隆并写文本 —— **已存在的节点，
    # 其 Label/Placeholder 文本永远不会被重写**。于是只要 ROW_SPEC / HEADER_TEXT / BUTTON_TEXT
    # 里的文案改过一次，重跑脚本就会撞上死局：补丁认为自己已经做完了，verify() 却断言文本不对，
    # 脚本再也跑不动。HeadersRow 的 Placeholder 正是这么卡住的 ——
    # AB 工程那份是空串 ''，而脚本声明的是 'x-opencode-session: agent-loop'
    # （ui_preview 那份是对的，因为它是在文案定稿之后才补的）。
    #
    # 归一化对「刚克隆出来」的节点是幂等重写，对「早就在那儿」的节点才是真正的修复。
    # 只动文本，不碰结构 —— 所以重跑两次结果完全一致。
    for _name, _kind in ORDER:
        _cur = cx.child_by_name(content, _name)
        if _cur is None:
            continue
        # ORDER 里还混着**原有**节点：14 个原始行（BaseUrlRow/ApiKeyRow/…）与 SaveLlmBtn。
        # 它们的文案由各自的既有来源决定，本脚本无权改写 —— 声明表里没有就跳过。
        if _kind == "header":
            _want = HEADER_TEXT.get(_name)
        elif _kind == "button":
            _want = BUTTON_TEXT.get(_name)
        else:
            _spec = ROW_SPEC.get(_name)
            _want = _spec[1] if _spec is not None else None
        if _want is None:
            continue
        _lab = cx.child_by_name(_cur, "Label")
        if _lab is not None:
            for _c in cx.components(cx.go_of(_lab)):
                if "m_FontData" in cx.text_of[_c]:
                    cx.set_field(_c, "m_Text", yaml_str(uesc(_want)))
                    rep.append("文本归一 %s/Label" % _name)
        if _kind == "row":
            _rph = ROW_SPEC[_name][2]
            _holder = cx.child_by_name(_cur, "Input")
            _ph = cx.child_by_name(_holder, "Placeholder") if _holder is not None else None
            if _rph is not None and _ph is not None:
                for _c in cx.components(cx.go_of(_ph)):
                    if "m_FontData" in cx.text_of[_c]:
                        cx.set_field(_c, "m_Text", yaml_str(uesc(_rph)))
                        rep.append("文本归一 %s/Placeholder" % _name)

    left = [n for n in ORIGINAL_CHILDREN if cx.child_by_name(content, n) is None]
    assert not left, "原节点未全部搬入 Content: %s" % left

    # ---- 3) 去重（同名只留 Content 下第一个；防重复插入）----
    #     注意：find_all_by_name(page_llm, …) 会走进 Content，所以必须按"保留者"排除，
    #     否则会把刚排好的节点当游离节点删掉。
    for parent, nm, keep in ((page_llm, SCROLL, scroll),
                             (scroll, VIEWPORT, viewport),
                             (viewport, CONTENT, content)):
        for d in cx.find_all_by_name(parent, nm):
            if d != keep:
                rep.append("去重删除重复容器 %s(%s)" % (nm, d))
                cx.remove_node(d)
    for name in TARGET_NAMES:
        keep = cx.child_by_name(content, name)
        for d in cx.find_all_by_name(page_llm, name):
            if d != keep:
                rep.append("去重删除重复节点 %s(%s)" % (name, d))
                cx.remove_node(d)

    # ---- 4) 版式 ----
    save_h = num(cx.field(cx.child_by_name(content, "SaveLlmBtn"), "m_SizeDelta").split(",")[1])
    heights = []
    for name, kind in ORDER:
        if kind == "header":
            heights.append((name, GRP_H))
        elif kind == "button":
            heights.append((name, save_h))
        else:
            heights.append((name, ROW_H))
    placed = layout(heights)
    for name, h, y in placed:
        rt = cx.child_by_name(content, name)
        cx.set_field(rt, "m_AnchoredPosition", "{x: %s, y: %s}" % (fmt(ROW_X), fmt(y)))
        cx.set_field(rt, "m_SizeDelta", "{x: %s, y: %s}" % (fmt(ROW_W), fmt(h)))
    last_y, last_h = placed[-1][2], placed[-1][1]
    content_h = abs(last_y) + last_h / 2.0 + BOTTOM_MARGIN
    cx.set_field(content, "m_SizeDelta", "{x: 0, y: %s}" % fmt(content_h))

    # Content 子节点顺序 = ORDER
    cx.set_children(content, [cx.child_by_name(content, n) for n, _ in ORDER])

    out = cx.render()
    rep.append("新增节点: %s" % (", ".join(cx.created) if cx.created else "无"))
    rep.append("克隆节点: %s" % (", ".join(cx.cloned) if cx.cloned else "无"))
    rep.append("搬移节点: %d 个" % len(cx.moved))
    rep.append("Content 子节点 = %d 个，总高 = %s" % (len(ORDER), fmt(content_h)))
    rep.append("末行 %s 中心 y=%s（底部 %s）+ 余量 %s" %
               (placed[-1][0], fmt(last_y), fmt(last_y - last_h / 2.0), fmt(BOTTOM_MARGIN)))

    # ---- 5) 自检 ----
    verify(out)
    if do_log:
        for r in rep:
            log("  " + r)
    return out


def verify(text):
    """结构自检：容器链 / 引用 / 顺序 / 组件——任一不满足直接 AssertionError"""
    cx = Ctx(text)
    root = [f for f in cx.order if cx.cls(f) == "224" and cx.father.get(f) == "0"][0]
    bg = cx.child_by_name(root, "BG")
    page_llm = cx.child_by_name(bg, "PageLlm")
    assert page_llm, "自检失败: BG/PageLlm"
    scroll = cx.child_by_name(page_llm, SCROLL)
    assert scroll, "自检失败: Scroll 缺失"
    assert len([c for c in cx.children(page_llm) if cx.name_of_rt(c) == SCROLL]) == 1, \
        "自检失败: Scroll 重复"
    viewport = cx.child_by_name(scroll, VIEWPORT)
    content = cx.child_by_name(viewport, CONTENT)
    assert viewport and content, "自检失败: Viewport/Content 缺失"
    go = cx.go_of(scroll)
    sr = cx.comp_with_guid(go, GUID_SCROLLRECT)
    assert sr, "自检失败: ScrollRect 缺失"
    m = re.search(r"(?m)^  m_Content: \{fileID: (\d+)\}$", cx.text_of[sr]).group(1)
    v = re.search(r"(?m)^  m_Viewport: \{fileID: (\d+)\}$", cx.text_of[sr]).group(1)
    assert m == content, "自检失败: m_Content 指向 %s ≠ %s" % (m, content)
    assert v == viewport, "自检失败: m_Viewport 指向 %s ≠ %s" % (v, viewport)
    assert re.search(r"(?m)^  m_Horizontal: 0$", cx.text_of[sr]), "自检失败: m_Horizontal≠0"
    assert re.search(r"(?m)^  m_Vertical: 1$", cx.text_of[sr]), "自检失败: m_Vertical≠1"
    assert re.search(r"(?m)^  m_MovementType: 2$", cx.text_of[sr]), "自检失败: movementType≠Clamped"
    kids = cx.children(content)
    names = [cx.name_of_rt(k) for k in kids]
    assert names == TARGET_NAMES, "自检失败: Content 顺序 = %s" % names
    # ★09-14★ 末位从 SaveLlmBtn 变成 TestLlmBtn（"测试连接"排在"保存"下面一行）。
    #   这条断言的价值：布局是按 ORDER 算 y 的，顺序错了会静默重叠，肉眼很难发现。
    assert names[-1] == "TestLlmBtn", "自检失败: TestLlmBtn 不在最后（实为 %s）" % names[-1]
    assert names[-2] == "SaveLlmBtn", "自检失败: SaveLlmBtn 应紧邻 TestLlmBtn 之上"
    for n in ORIGINAL_CHILDREN:
        assert n in names, "自检失败: 原节点 %s 丢失" % n
    for n, kind in ORDER:
        if kind != "row":
            continue
        rt = cx.child_by_name(content, n)
        inp = cx.child_by_name(rt, "Input")
        tgl = cx.child_by_name(rt, "ToggleBg")
        assert inp or tgl, "自检失败: %s 既无 Input 也无 ToggleBg" % n
        want = ROW_SPEC[n][0] if n in ROW_SPEC else None
        # "text" 也是 Input 行（headers），故与 "input" 同等校验
        if want in ("input", "text") or (want is None and inp):
            assert inp, "自检失败: %s 应为 Input 行" % n
            assert any("m_TextComponent" in cx.text_of[c]
                       for c in cx.components(cx.go_of(inp))), "自检失败: %s/Input 不是 InputField" % n
        if want == "toggle" or (want is None and tgl):
            assert tgl, "自检失败: %s 应为 Toggle 行" % n
            assert any("m_IsOn" in cx.text_of[c]
                       for c in cx.components(cx.go_of(tgl))), "自检失败: %s/ToggleBg 不是 Toggle" % n
        # 新行的 Label 文本 / Placeholder 文本
        if n in ROW_SPEC:
            rk, rlabel, rph, rval = ROW_SPEC[n]
            lab = cx.child_by_name(rt, "Label")
            lc = [c for c in cx.components(cx.go_of(lab)) if "m_FontData" in cx.text_of[c]][0]
            assert uesc(rlabel) in cx.text_of[lc], "自检失败: %s Label 文本不对" % n
            if rph is not None:
                ph = cx.child_by_name(cx.child_by_name(rt, "Input"), "Placeholder")
                pc = [c for c in cx.components(cx.go_of(ph)) if "m_FontData" in cx.text_of[c]][0]
                assert uesc(rph) in cx.text_of[pc], "自检失败: %s Placeholder 文本不对" % n
            # ★09-14★ 小数行的校验类型必须**真的**是小数 —— 这一条就是那个「吞小数点」bug 的回归闸。
            #   克隆源 PortRow 是整数框，漏改 m_CharacterValidation 会静默产出一个看起来正常、
            #   实际打不进小数点、回填还把 "0.16" 滤成 "016" 的框。断言比事后肉眼查可靠。
            if rval == "decimal":
                ic = [c for c in cx.components(cx.go_of(inp)) if "m_ContentType" in cx.text_of[c]][0]
                got_ct, got_cv = cx.field(ic, "m_ContentType"), cx.field(ic, "m_CharacterValidation")
                assert got_ct == "3", "自检失败: %s 应为 DecimalNumber(3)，实为 %s" % (n, got_ct)
                assert got_cv == "2", \
                    "自检失败: %s 应为 Decimal(2)，实为 %s（这个框会吞掉小数点）" % (n, got_cv)
            # ★09-14★ 反过来也钉一条：headers 这类文本行**必须**是 Standard + 无校验，
            #   否则写 `k: v` 时的冒号/分号会被校验器吃掉（同"吞小数点"是同一个坑）。
            if rval == "text":
                ic = [c for c in cx.components(cx.go_of(inp)) if "m_ContentType" in cx.text_of[c]][0]
                got_ct, got_cv = cx.field(ic, "m_ContentType"), cx.field(ic, "m_CharacterValidation")
                assert got_ct == "0", "自检失败: %s 应为 Standard(0)，实为 %s" % (n, got_ct)
                assert got_cv == "0", \
                    "自检失败: %s 应为无校验(0)，实为 %s（冒号会被吃掉）" % (n, got_cv)
            assert re.search(r"[\u4e00-\u9fff]", cx.text_of[lc]) is None, \
                "自检失败: %s Label 里有未转义中文" % n
    # ★09-14★ 新增按钮：Label 文本要对（布局按 ORDER 算，文本错了不会有别的症状）
    for name, txt in NEW_BUTTONS:
        rt = cx.child_by_name(content, name)
        assert rt, "自检失败: 按钮 %s 缺失" % name
        lab = cx.child_by_name(rt, "Label")
        assert lab, "自检失败: 按钮 %s 没有 Label 子节点" % name
        bc = [c for c in cx.components(cx.go_of(lab)) if "m_FontData" in cx.text_of[c]]
        assert bc, "自检失败: 按钮 %s/Label 无 Text" % name
        assert uesc(txt) in cx.text_of[bc[0]], "自检失败: 按钮 %s 文本不对" % name
    # 组标题文本（转义后）正确
    for name, txt in HEADERS:
        rt = cx.child_by_name(content, name)
        assert rt, "自检失败: 组标题 %s 缺失" % name
        t = [c for c in cx.components(cx.go_of(rt)) if "m_FontData" in cx.text_of[c]]
        assert t, "自检失败: %s 无 Text" % name
        assert uesc(txt) in cx.text_of[t[0]], "自检失败: %s 文本不对" % name
    # fileID 全局唯一
    ids = re.findall(r"(?m)^--- !u!\d+ &(\d+)$", text)
    assert len(ids) == len(set(ids)), "自检失败: fileID 重复"
    return True


def main(argv=None):
    ap = argparse.ArgumentParser(description="UIConfigAi.prefab PageLlm 分组 + 整页滚动改造（幂等）")
    ap.add_argument("--in", dest="inp", required=True, help="源 prefab")
    ap.add_argument("--out", dest="out", required=True, help="目标 prefab（可与 --in 相同=原地改）")
    ap.add_argument("--dry-run", action="store_true", help="只算不写")
    ap.add_argument("--no-backup", action="store_true",
                    help="原地改（--in == --out）时不写 .bak_cfggroups 备份")
    ap.add_argument("--allow-protected-out", action="store_true",
                    help="允许写到真实游戏工程 / ui_preview 工程（默认拒绝）")
    a = ap.parse_args(argv)

    outp = os.path.abspath(a.out)
    low = outp.replace("/", os.sep).lower()
    if not a.allow_protected_out:
        for p in PROTECTED_OUT:
            key = p.replace("/", os.sep).lower()
            if key in low:
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
    if inplace and not a.no_backup:                  # 本目录工具约定：原地改先留备份
        import shutil
        shutil.copy2(outp, outp + ".bak_cfggroups")
        log("== 备份 %s.bak_cfggroups" % outp)
    with io.open(outp, "w", encoding="utf-8", newline="") as f:
        f.write(out)
    log("== 输出 %s (%d 字节)" % (outp, len(out.encode("utf-8"))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
