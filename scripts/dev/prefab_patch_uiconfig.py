# -*- coding: utf-8 -*-
"""UIConfigAi.prefab 修复补丁：
1) Status / CurrentFileLabel 两个全拉伸文本条的 anchoredPosition 错位（24/70 → -296/-182，
   它们现在飘在窗口中央盖住文件列表；正确位置 = 底部专用条带）。
2) 深色主题遗留的近白文字 (0.94,0.95,0.98) → 墨色 (0.08,0.08,0.08)：
   标题/关闭/全部输入框内容文本/三个按钮 Label/PromptInput 内容。
   （Form 行标签、Tab 标签、FileItem 标签已是黑色，不动；Tooltip 深底浅字不动。）
3) Status/CurrentFileLabel 文字 (0.6,0.62,0.7) 灰 → 墨色。
"""
import re, io, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

PREFAB = r"E:\SteamLibrary\steamapps\common\鬼谷八荒\Mod\modFQA\资源修改教程\ResBuildABProject\Assets\Resources\UI\UIConfigAi.prefab"
text = open(PREFAB, encoding="utf-8").read()
docs = re.split(r"(?m)^(?=--- !u!)", text)

# ---- 索引 ----
objs = {}   # fid -> (cls, body)
for d in docs:
    m = re.match(r"--- !u!(\d+) &(\d+)", d)
    if m:
        objs[m.group(2)] = (int(m.group(1)), d[ m.end(): ].lstrip("\n"), d)

go_name = {}
rt_go, rt_father = {}, {}
for fid, (cls, body, raw) in objs.items():
    if cls == 1:
        go_name[fid] = re.search(r"^  m_Name: (.*)$", body, re.M).group(1).strip()
    elif cls == 224:
        rt_go[fid] = re.search(r"^  m_GameObject: \{fileID: (\d+)\}", body, re.M).group(1)
        rt_father[fid] = re.search(r"^  m_Father: \{fileID: (\d+)\}", body, re.M).group(1)

rt_by_go = {g: r for r, g in rt_go.items()}
def go_path(gofid):
    parts = [go_name.get(gofid, "?")]
    rt = rt_by_go.get(gofid)
    while rt and rt_father.get(rt, "0") != "0":
        prt = rt_father[rt]
        parts.append(go_name.get(rt_go.get(prt, "?"), "?"))
        rt = prt
    return "/".join(reversed(parts))

parent_go = {}  # gofid -> parent gofid
for rt, f in rt_father.items():
    if f in rt_go:
        parent_go[rt_go[rt]] = rt_go[f]

changed = []

# ---- 1) 两个 RectTransform 的位置 ----
for nm, newy in (("Status", -296.0), ("CurrentFileLabel", -182.0)):
    for fid, (cls, body, raw) in objs.items():
        if cls == 224 and go_name.get(rt_go.get(fid)) == nm:
            new_raw, n = re.subn(
                r"m_AnchoredPosition: \{x: [^,]+, y: [^}]+\}",
                "m_AnchoredPosition: {x: 0, y: %g}" % newy, raw, count=1)
            if n:
                objs[fid] = (cls, body, new_raw)
                changed.append("%s.pos.y -> %g" % (nm, newy))

# ---- 2)3) Text 组件颜色 ----
INK = "{r: 0.08, g: 0.08, b: 0.08, a: 1}"
exact_targets = {
    "BG/Title/TitleLabel", "BG/Title/CloseBtn/Label",
    "BG/PageLlm/SaveLlmBtn/Label",
    "BG/PagePrompt/SavePromptBtn/Label", "BG/PagePrompt/NewNpcBtn/Label",
    "BG/PagePrompt/NewNpcInput/Text", "BG/PagePrompt/PromptInput/Text",
    "BG/Status", "BG/PagePrompt/CurrentFileLabel",
}
gray_targets = {"BG/Status", "BG/PagePrompt/CurrentFileLabel"}

for fid, (cls, body, raw) in list(objs.items()):
    if cls != 114 or not re.search(r"^  m_FontData:", raw, re.M):
        continue  # 只处理 Text
    g = re.search(r"^  m_GameObject: \{fileID: (\d+)\}", raw, re.M).group(1)
    full = go_path(g)
    path = full.split("/", 1)[1] if full.count("/") >= 1 else full  # 去掉 Canvas 根前缀
    is_target = path in exact_targets or re.match(r"^BG/PageLlm/\w+Row/Input/Text$", path)
    if not is_target:
        continue
    cur = re.search(r"^  m_Color: (\{[^}]*\})$", raw, re.M)
    if not cur:
        continue
    if path in gray_targets:
        new_raw = re.sub(r"^  m_Color: \{[^}]*\}$", "  m_Color: " + INK, raw, count=1, flags=re.M)
    else:
        if "r: 0.94" not in cur.group(1):
            continue  # 已是深色，跳过
        new_raw = re.sub(r"^  m_Color: \{[^}]*\}$", "  m_Color: " + INK, raw, count=1, flags=re.M)
    if new_raw != raw:
        objs[fid] = (cls, body, new_raw)
        changed.append("ink: %s" % path)

# ---- 写回 ----
out = []
for d in docs:
    m = re.match(r"--- !u!(\d+) &(\d+)", d)
    if m and m.group(2) in objs:
        out.append(objs[m.group(2)][2])
    else:
        out.append(d)
open(PREFAB, "w", encoding="utf-8", newline="").write("".join(out))
print("patched %d items:" % len(changed))
for c in changed:
    print(" -", c)
