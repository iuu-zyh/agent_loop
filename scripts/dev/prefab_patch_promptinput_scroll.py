# -*- coding: utf-8 -*-
"""
UIConfigAi.prefab PromptInput 滚动改造（幂等，双份：AB 工程 + ui_preview）

原结构:  PromptInput(InputField multiline) ── Text(固定框, 超长看不全)
                  └─ Placeholder
新结构:  PromptInput(+ScrollRect)
                  ├─ Viewport(RectMask2D, 内边距 -20/-4) [新增]
                  │    └─ Content(VLG childControl + CSF vertical preferred) [新增]
                  │         └─ Text(原 Text GO 移入, stretch, 组件引用不变)
                  └─ Placeholder(原位不动)
C# 零路径变化: InputField 仍在 BG/PagePrompt/PromptInput，m_TextComponent 引用不变。
新 fileID 段 88100000000000000xx（脚本前已 grep 确认无冲突）。
"""
import io
import shutil
import sys

MARK = "8810000000000000009"  # ScrollRect doc fileID = 幂等标记
PROMPTINPUT_GO = "4981160001583130383"
PROMPTINPUT_RT = "5603218482986661592"
INPUTFIELD = "3390182317182551590"
TEXT_RT = "8766338093058431506"
PLACEHOLDER_RT = "4942322521775455080"

VIEWPORT_GO = "8810000000000000001"
VIEWPORT_RT = "8810000000000000002"
MASK = "8810000000000000003"
VIEWPORT_CR = "8810000000000000004"
CONTENT_GO = "8810000000000000005"
CONTENT_RT = "8810000000000000006"
VLG = "8810000000000000007"
CSF = "8810000000000000008"
SCROLL = "8810000000000000009"

NEW_DOCS = """--- !u!1 &8810000000000000001
GameObject:
  m_ObjectHideFlags: 0
  m_CorrespondingSourceObject: {fileID: 0}
  m_PrefabInstance: {fileID: 0}
  m_PrefabAsset: {fileID: 0}
  serializedVersion: 6
  m_Component:
  - component: {fileID: 8810000000000000002}
  - component: {fileID: 8810000000000000003}
  - component: {fileID: 8810000000000000004}
  m_Layer: 0
  m_Name: Viewport
  m_TagString: Untagged
  m_Icon: {fileID: 0}
  m_NavMeshLayer: 0
  m_StaticEditorFlags: 0
  m_IsActive: 1
--- !u!224 &8810000000000000002
RectTransform:
  m_ObjectHideFlags: 0
  m_CorrespondingSourceObject: {fileID: 0}
  m_PrefabInstance: {fileID: 0}
  m_PrefabAsset: {fileID: 0}
  m_GameObject: {fileID: 8810000000000000001}
  m_LocalRotation: {x: 0, y: 0, z: 0, w: 1}
  m_LocalPosition: {x: 0, y: 0, z: 0}
  m_LocalScale: {x: 1, y: 1, z: 1}
  m_Children:
  - {fileID: 8810000000000000006}
  m_Father: {fileID: 5603218482986661592}
  m_RootOrder: 0
  m_LocalEulerAnglesHint: {x: 0, y: 0, z: 0}
  m_AnchorMin: {x: 0, y: 0}
  m_AnchorMax: {x: 1, y: 1}
  m_AnchoredPosition: {x: 0, y: 0}
  m_SizeDelta: {x: -20, y: -4}
  m_Pivot: {x: 0.5, y: 0.5}
--- !u!114 &8810000000000000003
MonoBehaviour:
  m_ObjectHideFlags: 0
  m_CorrespondingSourceObject: {fileID: 0}
  m_PrefabInstance: {fileID: 0}
  m_PrefabAsset: {fileID: 0}
  m_GameObject: {fileID: 8810000000000000001}
  m_Enabled: 1
  m_EditorHideFlags: 0
  m_Script: {fileID: 11500000, guid: 3312d7739989d2b4e91e6319e9a96d76, type: 3}
  m_Name:
  m_EditorClassIdentifier:
  m_Padding: {x: 0, y: 0, z: 0, w: 0}
  m_Softness: {x: 0, y: 0}
--- !u!222 &8810000000000000004
CanvasRenderer:
  m_ObjectHideFlags: 0
  m_CorrespondingSourceObject: {fileID: 0}
  m_PrefabInstance: {fileID: 0}
  m_PrefabAsset: {fileID: 0}
  m_GameObject: {fileID: 8810000000000000001}
  m_CullTransparentMesh: 1
--- !u!1 &8810000000000000005
GameObject:
  m_ObjectHideFlags: 0
  m_CorrespondingSourceObject: {fileID: 0}
  m_PrefabInstance: {fileID: 0}
  m_PrefabAsset: {fileID: 0}
  serializedVersion: 6
  m_Component:
  - component: {fileID: 8810000000000000006}
  - component: {fileID: 8810000000000000007}
  - component: {fileID: 8810000000000000008}
  m_Layer: 0
  m_Name: Content
  m_TagString: Untagged
  m_Icon: {fileID: 0}
  m_NavMeshLayer: 0
  m_StaticEditorFlags: 0
  m_IsActive: 1
--- !u!224 &8810000000000000006
RectTransform:
  m_ObjectHideFlags: 0
  m_CorrespondingSourceObject: {fileID: 0}
  m_PrefabInstance: {fileID: 0}
  m_PrefabAsset: {fileID: 0}
  m_GameObject: {fileID: 8810000000000000005}
  m_LocalRotation: {x: 0, y: 0, z: 0, w: 1}
  m_LocalPosition: {x: 0, y: 0, z: 0}
  m_LocalScale: {x: 1, y: 1, z: 1}
  m_Children:
  - {fileID: 8766338093058431506}
  m_Father: {fileID: 8810000000000000002}
  m_RootOrder: 0
  m_LocalEulerAnglesHint: {x: 0, y: 0, z: 0}
  m_AnchorMin: {x: 0, y: 1}
  m_AnchorMax: {x: 1, y: 1}
  m_AnchoredPosition: {x: 0, y: 0}
  m_SizeDelta: {x: 0, y: 0}
  m_Pivot: {x: 0.5, y: 1}
--- !u!114 &8810000000000000007
MonoBehaviour:
  m_ObjectHideFlags: 0
  m_CorrespondingSourceObject: {fileID: 0}
  m_PrefabInstance: {fileID: 0}
  m_PrefabAsset: {fileID: 0}
  m_GameObject: {fileID: 8810000000000000005}
  m_Enabled: 1
  m_EditorHideFlags: 0
  m_Script: {fileID: 11500000, guid: 59f8146938fff824cb5fd77236b75775, type: 3}
  m_Name:
  m_EditorClassIdentifier:
  m_Padding:
    m_Left: 10
    m_Right: 10
    m_Top: 2
    m_Bottom: 2
  m_ChildAlignment: 0
  m_Spacing: 0
  m_ChildForceExpandWidth: 1
  m_ChildForceExpandHeight: 0
  m_ChildControlWidth: 1
  m_ChildControlHeight: 1
  m_ChildScaleWidth: 0
  m_ChildScaleHeight: 0
  m_ReverseArrangement: 0
--- !u!114 &8810000000000000008
MonoBehaviour:
  m_ObjectHideFlags: 0
  m_CorrespondingSourceObject: {fileID: 0}
  m_PrefabInstance: {fileID: 0}
  m_PrefabAsset: {fileID: 0}
  m_GameObject: {fileID: 8810000000000000005}
  m_Enabled: 1
  m_EditorHideFlags: 0
  m_Script: {fileID: 11500000, guid: 3245ec927659c4140ac4f8d17403cc18, type: 3}
  m_Name:
  m_EditorClassIdentifier:
  m_HorizontalFit: 0
  m_VerticalFit: 2
--- !u!114 &8810000000000000009
MonoBehaviour:
  m_ObjectHideFlags: 0
  m_CorrespondingSourceObject: {fileID: 0}
  m_PrefabInstance: {fileID: 0}
  m_PrefabAsset: {fileID: 0}
  m_GameObject: {fileID: 4981160001583130383}
  m_Enabled: 1
  m_EditorHideFlags: 0
  m_Script: {fileID: 11500000, guid: 1aa08ab6e0800fa44ae55d278d1423e3, type: 3}
  m_Name:
  m_EditorClassIdentifier:
  m_Content: {fileID: 8810000000000000006}
  m_Horizontal: 0
  m_Vertical: 1
  m_MovementType: 2
  m_Elasticity: 0.1
  m_Inertia: 1
  m_DecelerationRate: 0.135
  m_ScrollSensitivity: 20
  m_Viewport: {fileID: 0}
  m_HorizontalScrollbar: {fileID: 0}
  m_VerticalScrollbar: {fileID: 0}
  m_HorizontalScrollbarVisibility: 0
  m_VerticalScrollbarVisibility: 0
  m_HorizontalScrollbarSpacing: 0
  m_VerticalScrollbarSpacing: 0
  m_OnValueChanged:
    m_PersistentCalls:
      m_Calls: []
"""


def split_docs(text):
    idx = text.index("--- !u!")
    header, body = text[:idx], text[idx:]
    parts = body.split("--- !u!")
    return header, ["--- !u!" + p for p in parts if p.strip()]


def doc_id(block):
    first = block.split("\n", 1)[0]
    return first.split("&", 1)[1].strip()


def patch(path):
    with io.open(path, "r", encoding="utf-8") as f:
        text = f.read()
    if MARK in text:
        print("SKIP already patched:", path)
        return
    header, docs = split_docs(text)
    ids = [doc_id(d) for d in docs]
    for need in (PROMPTINPUT_GO, PROMPTINPUT_RT, INPUTFIELD, TEXT_RT, PLACEHOLDER_RT):
        if need not in ids:
            print("ABORT missing doc", need, "in", path)
            sys.exit(1)

    out = []
    for d in docs:
        did = doc_id(d)
        if did == TEXT_RT:
            d2 = d.replace(
                "m_Father: {fileID: " + PROMPTINPUT_RT + "}",
                "m_Father: {fileID: " + CONTENT_RT + "}", 1)
            d2 = d2.replace("m_SizeDelta: {x: -20, y: -4}", "m_SizeDelta: {x: 0, y: 0}", 1)
            if d2 == d:
                print("ABORT TEXT_RT patch failed:", path)
                sys.exit(1)
            d = d2
        elif did == PROMPTINPUT_GO:
            anchor = "  - component: {fileID: " + INPUTFIELD + "}"
            if anchor not in d:
                print("ABORT PromptInput GO components unexpected:", path)
                sys.exit(1)
            d = d.replace(anchor, anchor + "\n  - component: {fileID: " + SCROLL + "}", 1)
        elif did == PROMPTINPUT_RT:
            old_c = ("  m_Children:\n"
                     "  - {fileID: " + TEXT_RT + "}\n"
                     "  - {fileID: " + PLACEHOLDER_RT + "}")
            new_c = ("  m_Children:\n"
                     "  - {fileID: " + VIEWPORT_RT + "}\n"
                     "  - {fileID: " + PLACEHOLDER_RT + "}")
            if old_c not in d:
                print("ABORT PromptInput RT children unexpected:", path)
                sys.exit(1)
            d = d.replace(old_c, new_c, 1)
        out.append(d)

    shutil.copy2(path, path + ".bak_promptscroll")
    with io.open(path, "w", encoding="utf-8", newline="") as f:
        f.write(header + NEW_DOCS + "".join(out))
    print("PATCHED:", path, "(backup: .bak_promptscroll)")


if __name__ == "__main__":
    targets = [
        r"F:\agent_loop\ui_preview\Assets\Resources\UI\UIConfigAi.prefab",
        r"E:\SteamLibrary\steamapps\common\鬼谷八荒\Mod\modFQA\资源修改教程\ResBuildABProject\Assets\Resources\UI\UIConfigAi.prefab",
    ]
    for t in targets:
        patch(t)
