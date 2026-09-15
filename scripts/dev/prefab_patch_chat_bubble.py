# -*- coding: utf-8 -*-
"""给 UIChatAi.prefab 的 NpcBubble/UserBubble 模板 Root 挂 LayoutElement(prefH=76)：
行 HLG(childControlHeight) 取 Root 上各 ILayoutElement preferred 的最大值，
VLG(文本高+16) 与 76 取大者 = 「保底 76，超长再拉伸」。同 StepGroup/Header prefH=40 先例。
两个工程都打：AB 工程真源 + ui_preview 预览工程。幂等（有 &880000000000000010 就跳过）。"""
import re, io, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

NL = chr(10)
LE_LINES = [
    "--- !u!114 &{fid}",
    "MonoBehaviour:",
    "  m_ObjectHideFlags: 0",
    "  m_CorrespondingSourceObject: {fileID: 0}",
    "  m_PrefabInstance: {fileID: 0}",
    "  m_PrefabAsset: {fileID: 0}",
    "  m_GameObject: {fileID: {go}}",
    "  m_Enabled: 1",
    "  m_EditorHideFlags: 0",
    "  m_Script: {fileID: 11500000, guid: 306cc8c2b49d7114eaa3623786fc2126, type: 3}",
    "  m_Name: ",
    "  m_EditorClassIdentifier: ",
    "  m_IgnoreLayout: 0",
    "  m_MinWidth: 0",
    "  m_MinHeight: 0",
    "  m_PreferredWidth: 0",
    "  m_PreferredHeight: 76",
    "  m_FlexibleWidth: 0",
    "  m_FlexibleHeight: 0",
    "  m_LayoutPriority: 1",
    "",
]

TARGETS = [
    (r"E:\SteamLibrary\steamapps\common\鬼谷八荒\Mod\modFQA\资源修改教程\ResBuildABProject\Assets\Resources\UI\UIChatAi.prefab", {"NpcBubble": "880000000000000010", "UserBubble": "880000000000000011"}),
    (r"F:\agent_loop\ui_preview\Assets\Resources\UI\UIChatAi.prefab", {"NpcBubble": "880000000000000010", "UserBubble": "880000000000000011"}),
]

for path, plan in TARGETS:
    text = open(path, encoding="utf-8").read()
    name2fid = {}
    for m in re.finditer(r"^--- !u!1 &(\d+)$", text, re.M):
        blk = text[m.start():]
        n = re.search(r"^  m_Name: (.*)$", blk[:600], re.M)
        if n:
            name2fid[n.group(1).strip()] = m.group(1)
    changed = []
    for nm, newfid in plan.items():
        go = name2fid.get(nm)
        if not go:
            print(path, ":", nm, "未找到!"); continue
        if ("&" + newfid) in text:
            print(path.split(chr(92))[-1], ":", nm, "已有补丁，跳过"); continue
        # ① 找到该 GO 的 m_Component 行，在其后插入新组件引用
        anchor = "--- !u!1 &" + go + NL + "GameObject:"
        i = text.find(anchor)
        assert i >= 0, nm + " GO 块未找到"
        j = text.find("  m_Component:" + NL, i)
        assert j >= 0, nm + " m_Component 未找到"
        ins = j + len("  m_Component:" + NL)
        text = text[:ins] + "  - component: {fileID: " + newfid + "}" + NL + text[ins:]
        # ② 插入 LayoutElement doc（必须在 %YAML/%TAG 头之后！token 替换避免 .format() 误吞字面量花括号）
        tag = "%TAG !u! tag:unity3d.com,2011:"
        t = text.find(tag)
        assert t >= 0, "%TAG 行未找到"
        le = text.find(NL, t) + 1
        text = text[:le] + NL.join(LE_LINES).replace("{fid}", newfid).replace("{go}", go) + text[le:]
        changed.append(nm + "(GO " + go + ") += LayoutElement prefH=76 (@" + newfid + ")")
    open(path, "w", encoding="utf-8", newline="").write(text)
    for c in changed:
        print(path.split(chr(92))[-1], "→", c)
