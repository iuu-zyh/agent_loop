# -*- coding: utf-8 -*-
"""解析 Unity prefab YAML，dump 节点树 + RectTransform 锚点 + 组件颜色。"""
import re, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

PREFAB = r"E:\SteamLibrary\steamapps\common\鬼谷八荒\Mod\modFQA\资源修改教程\ResBuildABProject\Assets\Resources\UI\UIConfigAi.prefab"

text = open(PREFAB, encoding="utf-8").read()
docs = re.split(r"^--- !u!", text, flags=re.M)[1:]

objs = {}
for d in docs:
    head = d.split("\n", 1)[0]
    m = re.match(r"(\d+) &(\d+)", head)
    if not m:
        continue
    objs[m.group(2)] = (int(m.group(1)), d)

def field(body, name):
    m = re.search(r"^  %s: (.*)$" % re.escape(name), body, flags=re.M)
    return m.group(1).strip() if m else None

def vec2(body, name):
    m = re.search(r"^  %s: \{x: ([^,}]+), y: ([^}]+)\}" % re.escape(name), body, flags=re.M)
    return (float(m.group(1)), float(m.group(2))) if m else None

def color(body, name="m_Color"):
    m = re.search(r"^  %s: \{r: ([^,}]+), g: ([^,}]+), b: ([^,}]+), a: ([^}]+)\}" % re.escape(name), body, flags=re.M)
    return tuple(round(float(x), 3) for x in m.groups()) if m else None

go_name, go_comps = {}, {}
for fid, (cls, body) in objs.items():
    if cls == 1:
        go_name[fid] = field(body, "m_Name")
        go_comps[fid] = re.findall(r"component: \{fileID: (\d+)\}", body)

comp_cls, comp_go = {}, {}
for fid, (cls, body) in objs.items():
    if cls in (114, 224, 82, 65):
        g = re.search(r"^  m_GameObject: \{fileID: (\d+)\}", body, flags=re.M)
        if g:
            comp_cls[fid] = cls
            comp_go[fid] = g.group(1)

rt_of_go, other_comps = {}, {}
for cfid, gofid in comp_go.items():
    if comp_cls[cfid] == 224:
        rt_of_go[gofid] = cfid
    else:
        other_comps.setdefault(gofid, []).append(cfid)

father_of_rt = {}
for fid, (cls, body) in objs.items():
    if cls == 224:
        f = re.search(r"^  m_Father: \{fileID: (\d+)\}", body, flags=re.M)
        father_of_rt[fid] = f.group(1) if f else "0"

def classify(body):
    if re.search(r"^  m_TextComponent:", body, flags=re.M): return "InputField"
    if re.search(r"^  m_OnClick:", body, flags=re.M): return "Button"
    if re.search(r"^  m_IsOn:", body, flags=re.M): return "Toggle"
    if re.search(r"^  m_Content:", body, flags=re.M): return "ScrollRect"
    if re.search(r"^  m_FontData:", body, flags=re.M): return "Text"
    if re.search(r"^  m_Sprite:", body, flags=re.M): return "Image"
    if re.search(r"^  m_Navigation:", body, flags=re.M): return "Selectable?"
    return "Script"

def comp_lines(gofid, depth):
    out = []
    for cfid in other_comps.get(gofid, []):
        cls, body = objs[cfid]
        t = classify(body)
        if t == "Text":
            out.append("  " * depth + "<Text> size=%s align=%s color=%s text=%r" % (
                field(body, "m_FontSize"), field(body, "m_Alignment"), color(body), (field(body, "m_Text") or "")[:34]))
        elif t == "Image":
            spr = re.search(r"^  m_Sprite: \{fileID: (\d+), guid: ([0-9a-f]*)", body, flags=re.M)
            out.append("  " * depth + "<Image> color=%s spriteGuid=%s type=%s" % (
                color(body), spr.group(2)[:8] if spr else None, field(body, "m_Type")))
        elif t == "InputField":
            ph = re.search(r"^  m_Placeholder: \{fileID: (\d+)\}", body, flags=re.M)
            out.append("  " * depth + "<InputField> placeholder=%s" % (ph.group(1) if ph else None))
        elif t == "ScrollRect":
            out.append("  " * depth + "<ScrollRect>")
    return out

def dump(rt_fid, depth, out, maxdepth=9):
    gofid = comp_go.get(rt_fid, rt_fid)
    name = go_name.get(gofid, "?")
    body = objs[rt_fid][1]
    line = "  " * depth + name + "  [aMin=%s aMax=%s offMin=%s offMax=%s size=%s]" % (
        vec2(body, "m_AnchorMin"), vec2(body, "m_AnchorMax"),
        vec2(body, "m_OffsetMin"), vec2(body, "m_OffsetMax"), vec2(body, "m_SizeDelta"))
    out.append(line)
    out.extend(comp_lines(gofid, depth + 1))
    if depth < maxdepth:
        for cfid2, f in father_of_rt.items():
            if f == rt_fid:
                dump(cfid2, depth + 1, out, maxdepth)

roots = [rf for rf, f in father_of_rt.items() if f == "0"]
for r in roots:
    dump(r, 0, out := [])
print("\n".join(out))
