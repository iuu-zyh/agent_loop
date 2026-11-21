#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""给 `UIConfigAi.prefab` 的 `BG/PageLlm/Scroll` 补一张「命中层」Image（幂等）。

## 为什么需要
UGUI 里滚轮（IScrollHandler）与拖动（IDragHandler）都要求**先射线命中一个 Graphic**，
事件再沿父级冒泡到 ScrollRect。分组改版（`prefab_patch_config_groups.py`）只抄了
`FileScroll` 的 Scroll/Viewport/Content 三层结构，**漏了 FileScroll 挂在 ScrollRect 节点上的
那张 `Image(raycastTarget=1)`** → 面板能开、19 个节点全 True、几何也对（Content 1014.7 /
视口 504，有 510px 余量），但滚轮和拖动**都毫无反应**，下半页配置永远看不到。

对照（同预制件内唯一能滚的滚动区）：`BG/PagePrompt/FileScroll`
    Image: m_RaycastTarget=1, m_Color.a=0.267, m_Sprite={fileID: 0}
本脚本按同款补一张 **全透明**（a=0）Image：只吃事件、不改观感；子节点（输入框/开关/标题）
仍压在它上面，点击不受影响。

## 幂等
已存在 Image → 只确保 `m_RaycastTarget: 1`，不重复挂组件、不改已有颜色/贴图。

## 用法
    python3 scripts/dev/prefab_patch_scroll_hit.py                       # 只体检（dry-run）
    python3 scripts/dev/prefab_patch_scroll_hit.py --apply                # 改 ui_preview（默认目标）
    python3 scripts/dev/prefab_patch_scroll_hit.py --apply --allow-protected-out   # 改 AB 工程真件

真实 AB 工程路径默认拒写（需 `--allow-protected-out`）；原地改会先留 `.bak_scrollhit`。
改完**必须重打 AB 才在游戏里生效**——C# 侧 `AbConfigPanel.EnsureScrollInputTarget()` 有同款
运行期兜底，所以"没重打 AB"也能滚（这条只是把修复固化进资产）。
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

PREVIEW = Path("F:/agent_loop/ui_preview/Assets/Resources/UI/UIConfigAi.prefab")
AB_PROJ = Path("E:/SteamLibrary/steamapps/common/鬼谷八荒/Mod/modFQA/资源修改教程/"
               "ResBuildABProject/Assets/Resources/UI/UIConfigAi.prefab")


def _resolve(p: Path) -> Path:
    """Windows 盘符路径 → WSL /mnt 挂载路径（本机既可能在 Windows python 也可能在 WSL 下跑）。"""
    if p.is_file():
        return p
    s = str(p).replace("\\", "/")
    m = re.match(r"^([A-Za-z]):/(.*)$", s)
    if m:
        alt = Path(f"/mnt/{m.group(1).lower()}/{m.group(2)}")
        if alt.is_file():
            return alt
    return p

SCROLL_PATH = "UIConfigAi/BG/PageLlm/Scroll"
TARGET_GO_NAME = "Scroll"
PARENT_GO_NAME = "PageLlm"

# 预制件里 Image 的 MonoBehaviour guid（UnityEngine.UI.Image, 2020.3）
IMAGE_GUID = "fe87c0e1cc204ed48ad3b37840f39efc"
CANVAS_RENDERER_CLASS = 222
MONO_BEHAVIOUR_CLASS = 114


def _docs(txt: str):
    """切分 YAML 文档 → [(class_id, file_id, text)]"""
    out = []
    for d in re.split(r"^--- ", txt, flags=re.M):
        m = re.match(r"!u!(\d+) &(\d+)", d)
        if m:
            out.append((int(m.group(1)), m.group(2), d))
    return out


def _index(docs):
    go_name, tr = {}, {}
    for cls, fid, d in docs:
        if cls == 1:
            nm = re.search(r"m_Name: (.*)", d)
            go_name[fid] = nm.group(1).strip() if nm else "?"
        elif cls in (4, 224):
            g = re.search(r"m_GameObject: \{fileID: (\d+)\}", d)
            f = re.search(r"m_Father: \{fileID: (\d+)\}", d)
            seg = d.split("m_Children:")[1].split("m_Father:")[0] if "m_Children:" in d and "m_Father:" in d else ""
            tr[fid] = (g.group(1) if g else None, f.group(1) if f else None,
                       re.findall(r"- \{fileID: (\d+)\}", seg))
    cache = {}

    def path_of(fid):
        if fid in cache:
            return cache[fid]
        g, fa, _ = tr.get(fid, (None, None, None))
        name = go_name.get(g, "?")
        p = (path_of(fa) + "/" + name) if (fa and fa in tr and fa != "0") else name
        cache[fid] = p
        return p

    return go_name, tr, path_of, {path_of(f): f for f in tr}


def _components(docs, go_fid):
    d = [d for c, f, d in docs if c == 1 and f == go_fid][0]
    seg = d.split("m_Component:")[1].split("m_Layer:")[0]
    return re.findall(r"component: \{fileID: (\d+)\}", seg)


def _image_doc(file_id: int, go_file_id: str) -> str:
    """造一份最小 Image 文档：全透明 + 射线命中（字段与 FileScroll 那张对齐）。"""
    return f"""!u!{MONO_BEHAVIOUR_CLASS} &{file_id}
MonoBehaviour:
  m_ObjectHideFlags: 0
  m_CorrespondingSourceObject: {{fileID: 0}}
  m_PrefabInstance: {{fileID: 0}}
  m_PrefabAsset: {{fileID: 0}}
  m_GameObject: {{fileID: {go_file_id}}}
  m_Enabled: 1
  m_EditorHideFlags: 0
  m_Script: {{fileID: 11500000, guid: {IMAGE_GUID}, type: 3}}
  m_Name:
  m_EditorClassIdentifier:
  m_Material: {{fileID: 0}}
  m_Color: {{r: 0, g: 0, b: 0, a: 0}}
  m_RaycastTarget: 1
  m_RaycastPadding: {{x: 0, y: 0, z: 0, w: 0}}
  m_Maskable: 1
  m_OnCullStateChanged:
    m_PersistentCalls:
      m_Calls: []
  m_Sprite: {{fileID: 0}}
  m_Type: 0
  m_PreserveAspect: 0
  m_FillCenter: 1
  m_FillMethod: 4
  m_FillAmount: 1
  m_FillClockwise: 1
  m_FillOrigin: 0
  m_UseSpriteMesh: 0
  m_PixelsPerUnitMultiplier: 1
"""


def patch(path: Path, apply: bool) -> int:
    txt = path.read_text(encoding="utf-8")
    docs = _docs(txt)
    go_name, tr, path_of, byp = _index(docs)

    fid = byp.get(SCROLL_PATH)
    if not fid:
        print(f"✗ 找不到 {SCROLL_PATH}")
        return 2
    go_fid = tr[fid][0]
    comps = _components(docs, go_fid)
    img_fid = None
    for c in comps:
        cls, cfid, d = [x for x in docs if x[1] == c][0]
        if cls == MONO_BEHAVIOUR_CLASS and f"guid: {IMAGE_GUID}" in d:
            img_fid = cfid
            break

    has_cr = any(c in [x[1] for x in docs if x[0] == CANVAS_RENDERER_CLASS] for c in comps)
    if img_fid:
        rt = re.search(r"m_RaycastTarget: (\d)", [d for c, f, d in docs if f == img_fid][0])
        cur = rt.group(1) if rt else "?"
        if cur == "1":
            print(f"✓ {path.name}: Scroll 已有 Image(raycastTarget=1) —— 无需改动")
            return 0
        print(f"· {path.name}: Image 存在但 raycastTarget={cur} → 置 1")
        if apply:
            _backup(path)
            txt = re.sub(rf"(!u!{MONO_BEHAVIOUR_CLASS} &{img_fid}\n(?:.*\n)*?  m_RaycastTarget: )\d",
                         r"\g<1>1", txt, count=1)
            path.write_text(txt, encoding="utf-8")
        return 0

    print(f"· {path.name}: Scroll 无 Image（CanvasRenderer={has_cr}）→ 补一张 a=0 / raycastTarget=1")
    if not apply:
        return 0

    _backup(path)
    # 新 fileID：取现有最大数值 +1，确保不撞
    new_fid = max(int(f) for _c, f, _d in docs) + 1

    # ① GameObject 的 m_Component 里挂上新组件（Image 要在 CanvasRenderer 之后、无 CanvasRenderer 则紧随 RectTransform）
    gidx = [i for i, (c, f, _d) in enumerate(docs) if c == 1 and f == go_fid][0]
    gtxt = docs[gidx][2]
    head, rest = gtxt.split("m_Component:", 1)
    seg, tail = rest.split("m_Layer:", 1)
    # ★09-14 修复★ 这里原本是 `seg.rstrip("\n") + "\n  - component: …\n"`，产出**畸形文本**：
    #   seg 的结尾是 `…004\n  ` —— `m_Layer` 前面那两格缩进被 split 留在了 seg 里。
    #   `rstrip("\n")` 只去换行、不去空格，于是那两格变成「一行只含两个空格」；
    #   而下面的 `+ "m_Layer:"` 又是无缩进拼接 → 拼出：
    #         - component: {fileID: …004}
    #         (一行两个空格)
    #         - component: {fileID: …new}
    #       m_Layer: 0                     ← 缩进没了
    #       m_Name: Scroll
    #   Unity 当时手上是内存里的旧对象，所以一直没发作；**下次重新打开工程**按这份文本重新导入时，
    #   Scroll 的 m_Name 会被丢掉 → C# 的 `BG/PageLlm/Scroll` 整条路径解析失败 → 配置面板整页空白
    #   （2026-09-14 实机，排查见 config_groups 那套路径自检）。故必须 rstrip() 干净 + 补回两格缩进。
    #   （has_cr 两个分支原本写法完全相同，是无意义的死分支，一并合并。）
    seg = seg.rstrip() + f"\n  - component: {{fileID: {new_fid}}}\n  "
    gtxt_new = head + "m_Component:" + seg + "m_Layer:" + tail
    assert "\n  m_Layer:" in gtxt_new, "拼接后 m_Layer 缩进丢失（会毁掉整个 GameObject 文档）"
    assert re.search(r"(?m)^  m_Name: .+$", gtxt_new), "拼接后 m_Name 丢失"

    # ② 组件文档插在 Scroll 的 GameObject 文档之后
    newdoc = _image_doc(new_fid, go_fid)
    anchor = f"--- {gtxt}"
    assert anchor in txt, "锚点定位失败"
    txt = txt.replace(anchor, f"--- {gtxt_new}--- {newdoc}", 1)
    path.write_text(txt, encoding="utf-8")

    # ③ 回读自检
    chk = _docs(path.read_text(encoding="utf-8"))
    _, _, _, byp2 = _index(chk)
    fid2 = byp2.get(SCROLL_PATH)
    comps2 = _components(chk, tr and _index(chk)[1][fid2][0])
    ok = any(f"guid: {IMAGE_GUID}" in d for c, f, d in chk if f in comps2)
    # ★09-14 新增★ 名字完整性闸：任何 GameObject 丢名都会让 C# 的按路径 Find **静默**失败
    #   （表现是整块 UI 空白，而不是报错），而损坏在文本上只差一行 —— 肉眼查不出来。
    #   每次写完必须回读比对：写之前有名字、写之后没了 = 立刻失败。
    lost = [f for f, n in _go_names(docs) if n and not dict(_go_names(chk)).get(f)]
    if lost:
        print(f"  ✗ 自检：这些 GameObject 丢了名字 {lost}（会让按路径 Find 整条失败）")
        return 3
    print(f"  {'✓' if ok else '✗'} 自检：Scroll 组件 = {len(comps2)} 个，Image 挂载 = {ok}，"
          f"GameObject 名字完整 = {len(_go_names(chk))} 个")
    return 0 if ok else 3


def _go_names(docs):
    """[(fileID, m_Name)]，只取 GameObject（class 1）。名字为空的也返回（值为 ""）。"""
    out = []
    for cls, fid, d in docs:
        if cls != 1:
            continue
        m = re.search(r"(?m)^  m_Name: ?(.*)$", d)
        out.append((fid, m.group(1).strip() if m else ""))
    return out


def _backup(path: Path):
    bak = path.with_suffix(path.suffix + ".bak_scrollhit")
    if not bak.exists():
        shutil.copy2(path, bak)
        print(f"  备份 → {bak.name}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真的写回（默认只体检）")
    ap.add_argument("--file", action="append", default=[], help="指定 prefab（可多次）")
    ap.add_argument("--allow-protected-out", action="store_true", help="允许写 AB 工程真件")
    a = ap.parse_args()

    # ★2026-09-14★ ui_preview 工程已删除（用户拍板走 AB 资产固化）→ 默认目标改为 AB 工程真件。
    # 它属"受保护"路径，写回依旧需要 --allow-protected-out；这里只决定"不带 --file 时看哪个"。
    targets = [_resolve(Path(p)) for p in a.file] or [_resolve(AB_PROJ)]

    rc = 0
    for t in targets:
        if not t.is_file():
            print(f"✗ 不存在: {t}")
            rc = 2
            continue
        protected = "ResBuildABProject" in str(t)
        if a.apply and protected and not a.allow_protected_out:
            print(f"✗ {t} 是 AB 工程真件，拒写（要写加 --allow-protected-out）")
            rc = 2
            continue
        rc |= patch(t, a.apply)
    if not a.apply:
        print("\n（体检模式：加 --apply 才写回）")
    return rc


if __name__ == "__main__":
    sys.exit(main())
