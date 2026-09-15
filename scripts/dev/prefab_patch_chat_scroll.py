#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""调整 `UIChatAi.prefab` 里聊天滚动区的 `m_ScrollSensitivity`（幂等）。

## 为什么
用户反馈："向上很慢，向下一下子就到最新消息了，手感很差"。

**"向下一下就到底" 不是预制件的问题**，是 C# 的跟随逻辑（见文末）；但 **"向上很慢" 有一半在预制件**：
聊天记录的滚动余量动辄 2000~4000px，而 `m_ScrollSensitivity = 30` 意味着
**一个滚轮刻度只走 30px**——从底爬到顶要 70~130 个刻度。本仓库自己的取舍供对照：

| 面板 | 视口 | 内容余量 | sensitivity | 一格屏数 |
|---|---|---|---|---|
| 配置面板 PageLlm | 504 | 610 | **20** | 25 |
| 通讯录 ContactScroll | ~600 | 小 | **28** | 21 |
| **聊天 Scroll** | 420（真人尺寸） | **2000~4000** | **30** ← 本脚本 | **70~130** |

配置/通讯录余量小，所以 20 够用；聊天余量大一个数量级，同样的 30 就成了"爬"。

## 取值依据
正文行高实测 `19.79px`（预制件里聊天 Text 的 `m_SizeDelta.y`）。
**用户拍板取 90（≈ 一格 4.5 行）**：比原来的 30 快 3 倍。
换算后 **真人尺寸 420px 视口 ≈ 4.7 格一屏**、改大后 620px 视口 ≈ 7 格一屏。

嫌慢/嫌快改这一个数即可（`--sensitivity N`），**不需要动 C#**。

## 幂等
已经是目标值 → 只报"无需修改"，不写文件、不留备份。

## 用法
    python3 scripts/dev/prefab_patch_chat_scroll.py                     # 体检（dry-run）
    python3 scripts/dev/prefab_patch_chat_scroll.py --sensitivity 60    # 换个值体检
    python3 scripts/dev/prefab_patch_chat_scroll.py --apply --allow-protected-out

## 两条腿（同 `AbConfigPanel.EnsureScrollInputTarget` 的约定）
本脚本改的是**资产**那条腿；C# 侧 `ChatWindow.ApplyScrollSensitivityFloor()`（`Init` 里调）
把 `scrollSensitivity` 抬到**下限 90**，所以：
  · **不重打 AB 也能立刻好用**（跑的是手上那份老 AB，运行期兜底生效）
  · 重打 AB 只是把修复**固化进资产**，并把"依赖 C# 兜底"这件事去掉
  · 语义是**下限不是赋值**：预制件配得比 90 高就听预制件的（以后想更快只改资产，不动 C#）

重打 AB 的路径：`Assets/AssetBundle/ab/ui/uichatai.ab`。

## ★ 顺带发现：Viewport 是退化矩形（本脚本不改，仅报告）★
`BG/Scroll/Viewport` 的 RectTransform 是 `anchorMin == anchorMax == (0,0)` + `sizeDelta == (0,0)`
= **0×0**，而同一工程里两个能正常滚的面板都是教科书形态（`aMin(0,0) aMax(1,1) size(0,0)`）：

    UIContactAi  ContactScroll/Viewport  aMin=(0,0) aMax=(1,1)  ✅
    UIConfigAi   PageLlm/Scroll/Viewport aMin=(0,0) aMax=(1,1)  ✅
    UIChatAi     BG/Scroll/Viewport      aMin=(0,0) aMax=(0,0)  ⚠ 唯一一个

**但线上游戏是好的**，所以没有动它——`UIChatAi` 是唯一 `m_VerticalScrollbarVisibility: 2`
（AutoHideAndExpandViewport）的滚动区，而另外两个是 `0`（Permanent）；高度怀疑是 UGUI
在该模式下运行期改写了 viewport 的 anchor/size，把预制件里的退化值盖掉了。
改成 stretch 会**同时**改掉这条路径，属于"没坏别修"。
真要确认，得在运行期打一次几何探针（`ConfigPresenter` 的「滚动几何」那行同款）。

## ★ "向下一下就到底" 在 C#，不在预制件（已于 09-14 17:18 修好）★
`csharp/UI/ChatWindow.cs` 的**改前**形态：

    // Update()
    var wheel = Input.mouseScrollDelta.y;
    if (wheel > 0.01f) _followingBottom = false;
    else if (wheel < -0.01f) _followingBottom = true;   // ← 方向闩锁：下滚一格 = 立刻恢复跟随
    // LateUpdate()
    if (_followingBottom && IsOpen && _r != null && _r.Scroll != null)
        _r.Scroll.verticalNormalizedPosition = 0f;      // ← 每帧硬拉到底 → 瞬移

一个向下刻度 → 下一帧 `verticalNormalizedPosition = 0f` → 直接到底（"一下子就到最新消息"）。
而上滚只是清标志、位移交给 ScrollRect 自己（30px/格）→"很慢"。
两个症状**同一根因**：跟随标志是"滚轮方向"而不是"当前位置"。
**修法（已落地）**：删掉"下滚=恢复跟随"这一支，改由 `LateUpdate` 按**真实位置**判定 ——
  · 未跟随时：`AtBottom()` 为真才恢复跟随（滚/拖回底部即自愈）
  · 已跟随时：只在 **Content 子节点数或高度增长**时钉底（旧版每帧无条件写 `=0f`，
    把用户的拖动与惯性在下一帧一并抹掉——那是"手感很差"的另一半）
  · `AtBottom()` 必须先判"没得滚"：`verticalNormalizedPosition` 在**内容比视口矮时返回 1**，
    只看它会让短对话永远判成"不在底部"，新消息再也不自动滚出来
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

AB_PROJ = Path("E:/SteamLibrary/steamapps/common/鬼谷八荒/Mod/modFQA/资源修改教程/"
               "ResBuildABProject/Assets/Resources/UI/UIChatAi.prefab")
PREVIEW = Path("F:/agent_loop/ui_preview/Assets/Resources/UI/UIChatAi.prefab")

# 预制件内部路径带根名（`_index` 的 path_of 从根 GameObject 起拼）；
# C# 侧的 `root.Find("BG/Scroll")` 是**相对根**，所以这里要带 `UIChatAi/` 前缀。
SCROLL_PATH = "UIChatAi/BG/Scroll"
TARGET_FIELD = "m_ScrollSensitivity"
DEFAULT_SENSITIVITY = 90
BACKUP_TAG = ".bak_chatscroll"


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


def _viewport_report(docs, go_name, tr, path_of, byp) -> None:
    """只报告 Viewport / Content 的矩形形态，不改。"""
    vp = byp.get(SCROLL_PATH + "/Viewport")
    ct = byp.get(SCROLL_PATH + "/Viewport/Content")
    for label, fid in (("Viewport", vp), ("Content", ct)):
        if not fid:
            print(f"    {label}: 找不到节点")
            continue
        d = [t for c, f, t in docs if f == fid][0]

        def g(field, default="?"):
            m = re.search(rf"^\s*{field}: (.+)$", d, re.M)
            return m.group(1).strip() if m else default

        amin, amax, size = g("m_AnchorMin"), g("m_AnchorMax"), g("m_SizeDelta")
        degenerate = amin == amax and size == "{x: 0, y: 0}"
        flag = "  ⚠ 退化 0×0（见文件头说明，本脚本不改）" if degenerate and label == "Viewport" else ""
        print(f"    {label}: aMin={amin} aMax={amax} size={size}{flag}")


def patch(path: Path, target: int, apply: bool) -> int:
    txt = path.read_text(encoding="utf-8")
    docs = _docs(txt)
    go_name, tr, path_of, byp = _index(docs)

    fid = byp.get(SCROLL_PATH)
    if not fid:
        print(f"  ✗ 找不到节点 {SCROLL_PATH}")
        return 2
    go_fid = tr[fid][0]

    # 该 GameObject 上挂的组件里找 ScrollRect（含 m_ScrollSensitivity 的那个 MonoBehaviour）
    comp_seg = [t for c, f, t in docs if c == 1 and f == go_fid][0]
    comp_ids = re.findall(r"component: \{fileID: (\d+)\}",
                          comp_seg.split("m_Component:")[1].split("m_Layer:")[0])
    sr = None
    for cid in comp_ids:
        d = [t for c, f, t in docs if f == cid]
        if d and TARGET_FIELD in d[0]:
            sr = (cid, d[0])
            break
    if sr is None:
        print(f"  ✗ {SCROLL_PATH} 上没找到 ScrollRect（组件 {comp_ids}）")
        return 2

    cid, body = sr
    m = re.search(rf"^(\s*{TARGET_FIELD}: )(\S+)\s*$", body, re.M)
    if not m:
        print(f"  ✗ ScrollRect({cid}) 里没有 {TARGET_FIELD} 字段")
        return 2
    cur = m.group(2)
    print(f"  节点 {SCROLL_PATH} · ScrollRect fileID={cid}")
    print(f"    {TARGET_FIELD}: {cur}  →  {target}")
    _viewport_report(docs, go_name, tr, path_of, byp)

    if cur == str(target):
        print("  = 已是目标值，无需修改（幂等）")
        return 0
    if not apply:
        print("  (dry-run，未写盘；加 --apply 生效)")
        return 0

    # 只在 ScrollRect 那一份文档里替换该字段（避免误伤同名别处）
    new_body = re.sub(rf"^(\s*{TARGET_FIELD}: )\S+\s*$", rf"\g<1>{target}", body, count=1, flags=re.M)
    assert new_body != body, "字段替换没生效（正则与 YAML 实际格式不符）"
    new_txt = txt.replace(body, new_body, 1)
    assert new_txt != txt, "整体替换没生效"

    bak = path.with_name(path.name + BACKUP_TAG)
    if not bak.exists():
        shutil.copy2(path, bak)
        print(f"  备份 → {bak.name}")
    else:
        print(f"  备份已存在，保留原样 → {bak.name}")
    path.write_text(new_txt, encoding="utf-8")
    print(f"  ✓ 已写入 {path}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="调整 UIChatAi 聊天滚动区灵敏度")
    ap.add_argument("--sensitivity", type=int, default=DEFAULT_SENSITIVITY,
                    help=f"目标 m_ScrollSensitivity（默认 {DEFAULT_SENSITIVITY}）")
    ap.add_argument("--apply", action="store_true", help="真正写盘（默认 dry-run）")
    ap.add_argument("--allow-protected-out", action="store_true",
                    help="允许写真实 AB 工程（默认只体检，不碰真件）")
    ap.add_argument("--also-preview", action="store_true",
                    help="顺带改 ui_preview 副本（注意：那份是 09-09 的旧快照）")
    args = ap.parse_args(argv)

    if args.sensitivity < 1:
        print("✗ --sensitivity 必须 ≥ 1")
        return 2

    ab = _resolve(AB_PROJ)
    if not ab.is_file():
        print(f"✗ 找不到 AB 工程预制件：{AB_PROJ}")
        return 2
    print(f"■ AB 工程真件：{ab}")
    rc = patch(ab, args.sensitivity, args.apply and args.allow_protected_out)
    if args.apply and not args.allow_protected_out:
        print("  ⚠ 未加 --allow-protected-out：真件只体检未写盘")

    if args.also_preview:
        pv = _resolve(PREVIEW)
        print(f"\n■ ui_preview 副本：{pv}")
        if not pv.is_file():
            print("  ✗ 不存在")
        else:
            rc2 = patch(pv, args.sensitivity, args.apply)
            rc = rc or rc2

    if args.apply:
        print("\n★ 资产那条腿已落地；不重打 AB 也生效（ChatWindow 运行期兜底 90）★\n"
              "   重打 AB 只是把修复固化进资产：Assets/AssetBundle/ab/ui/uichatai.ab")
    return rc


if __name__ == "__main__":
    sys.exit(main())
