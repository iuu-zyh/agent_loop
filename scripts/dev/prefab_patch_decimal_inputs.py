# -*- coding: utf-8 -*-
"""小数输入框校验类型修复（2026-09-14）—— AB 工程 + ui_preview 双份，幂等。

症状（实机，2026-09-14）：
    F11 配置面板里「保留原文比例」「自动压缩阈值」两个框
      · 小数点**打不进去**；
      · get_config 回填后显示成 `016` / `08`（小数点被吞掉了）；
      · 点保存被拦下：「保留原文比例需 0.05-0.6（默认 0.16）」——**改不动也存不下去**。

根因（两条独立证据，不是猜的）：
    ① 逐字段 diff 预制件里两个同类框，只差这三项：

           RetainRatioRow（坏）           MinIntervalRow（对，同为小数）
           m_ContentType        2 整数    3 小数
           m_KeyboardType       4 数字键盘 2 小数键盘
           m_CharacterValidation 1 整数   2 小数

    ② 来源：`scripts/dev/prefab_patch_config_groups.py` 新增参数行时统一

           src = cx.find_by_name(content, "PortRow" if rk == "input" else "LowHalveRow")

       —— **PortRow 是整数框**。克隆只改了 Label 文本与 Placeholder，
       `m_CharacterValidation` 原样继承，于是两个小数框悄悄变成了整数框。

    ③ 为什么"显示成 016"而不是"空的"：uGUI 的 `InputField.SetText` 在
       `characterValidation != None` 时走**逐字符 Append** 分支（每个字符都过一遍
       同一个校验器），`.` 被 Integer 校验器丢弃：
       `"0.16"` → `"016"` → 保存时 `double.Parse("016") == 16` → 撞 0.05-0.6 越界。
       threshold 同理：`"0.8"` → `"08"` → `8.0`。

改法：
    把这三项对齐到 MinIntervalRow 的值（3 / 2 / 2）。改完**必须在 Unity 里重打 AB** 才进游戏。
    C# 侧另有 `ConfigPresenter.EnsureDecimalInput()` 运行期兜底 —— 手上这份老 AB 不重打也能用，
    两条路都留着：预制件是对的，代码是防它再变错的。

用法（WSL / Windows 都能跑）：
    python scripts/dev/prefab_patch_decimal_inputs.py           # 修
    python scripts/dev/prefab_patch_decimal_inputs.py --check    # 只看不改（做体检）
"""
import io
import os
import re
import sys
import shutil

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

# 目标：这三项对齐到预制件里**本来就是对**的小数行（MinIntervalRow / TimeoutRow）
WANT = {
    "m_ContentType": "3",          # InputField.ContentType.DecimalNumber
    "m_KeyboardType": "2",         # TouchScreenKeyboardType.DecimalPad
    "m_CharacterValidation": "2",  # CharacterValidation.Decimal
}

# 要修的行（节点名）。只列**真正是小数**的行 —— 整数行（Port/CtxWindow/DailyChance/
# LowThresh/NpcCooldown*）保持 Integer 不动，那是正确的。
ROWS = ["RetainRatioRow", "ThresholdRatioRow"]

PREFABS = [
    r"E:\SteamLibrary\steamapps\common\鬼谷八荒\Mod\modFQA\资源修改教程"
    r"\ResBuildABProject\Assets\Resources\UI\UIConfigAi.prefab",
    r"F:\agent_loop\ui_preview\Assets\Resources\UI\UIConfigAi.prefab",
]
# ★2026-09-14★ ui_preview 工程已按用户要求删除（"现在不用，ab 我自己打；走 AB 资产固化"）。
# 上面那条路径保留只为兼容"删之前建的旧工作副本"——运行时会跳过不存在的目标，不报错。
# 现在**真正要改的只有 AB 工程那一份**（ResBuildABProject），它是 AB 的唯一来源。


def to_local(path):
    """`E:\\x\\y` → `/mnt/e/x/y`（在 WSL 下跑时用）。Windows 下原样返回。"""
    if os.name == "nt" or path.startswith("/"):
        return path
    m = re.match(r"^([A-Za-z]):\\(.*)$", path)
    if not m:
        return path
    local = "/mnt/%s/%s" % (m.group(1).lower(), m.group(2).replace("\\", "/"))
    return local if os.path.exists(local) else path


HEAD = re.compile(r"^--- !u!(\d+) &(\d+)\s*$")
CHECK = "--check" in sys.argv


def parse(lines):
    """把预制件切成 doc 列表：[(class_id, fileID, start, end)]（行号半开区间）。"""
    docs, cur = [], None
    for i, ln in enumerate(lines):
        m = HEAD.match(ln)
        if m:
            if cur:
                docs.append(cur + (i,))
            cur = (m.group(1), m.group(2), i)
    if cur:
        docs.append(cur + (len(lines),))
    return docs


def field(block, key):
    for ln in block:
        m = re.match(r"^  " + key + r": (.*)$", ln)
        if m:
            return m.group(1).strip()
    return None


def set_field(block, key, value):
    """就地改 doc 内的某个一级字段；返回 (新 block, 是否改动)。找不到键则原样返回。"""
    pat = re.compile(r"^(  " + key + r": )(.*)$")
    changed = False
    for i, ln in enumerate(block):
        m = pat.match(ln)
        if m and m.group(2).strip() != value:
            block[i] = m.group(1) + value
            changed = True
    return block, changed


def patch_file(path):
    if not os.path.exists(path):
        print("  跳过（文件不存在）：%s" % path)
        return "skip"
    raw = io.open(path, encoding="utf-8", newline="").read()
    if "\r\n" in raw:
        print("  ⚠ 该预制件是 CRLF，本脚本按 LF 处理，为免改坏行尾直接退出：%s" % path)
        return "error"
    lines = raw.split("\n")
    docs = parse(lines)
    docmap = {d[1]: (d[0], d[2], d[3]) for d in docs}

    # ---- 建索引：GameObject 名 / 层级 / 组件 ----
    gos, trans, go_comps = {}, {}, {}
    for cid, fid, s, e in docs:
        b = lines[s:e]
        if cid == "1":
            gos[fid] = field(b, "m_Name") or "?"
            comps, incomp = [], False
            for ln in b:
                if re.match(r"^  m_Component:", ln):
                    incomp = True
                    continue
                if incomp:
                    m = re.match(r"^  - component: \{fileID: (\d+)\}", ln)
                    if m:
                        comps.append(m.group(1))
                    elif not ln.startswith("  -"):
                        incomp = False
            go_comps[fid] = comps
        elif cid in ("4", "224"):
            gof = field(b, "m_GameObject")
            fath = field(b, "m_Father")
            trans[fid] = (
                re.search(r"\d+", gof).group(0) if gof else "0",
                re.search(r"\d+", fath).group(0) if fath else "0",
            )
    tf_of_go = {gof: tf for tf, (gof, _f) in trans.items()}

    def path_of(go):
        parts, tf, seen = [], tf_of_go.get(go), set()
        while tf and tf not in seen:
            seen.add(tf)
            gof, fath = trans[tf]
            parts.append(gos.get(gof, "?"))
            tf = fath if fath != "0" else None
        return "/".join(reversed(parts))

    # ---- 找到每行的 InputField 组件 ----
    targets = {}   # row -> (mono_fid, go_path, 当前值)
    for gof, name in gos.items():
        if name != "Input":
            continue
        p = path_of(gof)
        row = p.split("/")[-2] if "/" in p else ""
        if row not in ROWS:
            continue
        for cf in go_comps.get(gof, []):
            if cf not in docmap:
                continue
            cid, s, e = docmap[cf]
            if cid != "114":
                continue
            b = lines[s:e]
            if field(b, "m_ContentType") is None:
                continue          # 不是 InputField
            targets[row] = (cf, "/" + p, {k: field(b, k) for k in WANT})

    missing = [r for r in ROWS if r not in targets]
    if missing:
        print("  ✗ 找不到这些行的 InputField：%s" % ", ".join(missing))
        return "error"

    # ---- 体检 / 修补 ----
    todo = []
    for row in ROWS:
        fid, p, cur = targets[row]
        diff = {k: (cur[k], WANT[k]) for k in WANT if cur[k] != WANT[k]}
        if diff:
            todo.append((row, fid, p, diff))
        print("  %-18s %-52s %s" % (
            row, p, "已正确" if not diff else
            "待修 " + " ".join("%s %s→%s" % (k.replace("m_", ""), a, b) for k, (a, b) in diff.items())))

    if not todo:
        print("  ✓ 无需改动（幂等）")
        return "ok"
    if CHECK:
        print("  （--check：只看不改）")
        return "needs"

    # ---- 落到文本 ----
    shutil.copyfile(path, path + ".bak_decimal")
    for row, fid, p, diff in todo:
        _cid, s, e = docmap[fid]
        b, _ = set_field(lines[s:e], list(diff)[0], WANT[list(diff)[0]])
        for k in list(diff)[1:]:
            b, _ = set_field(b, k, WANT[k])
        lines[s:e] = b

    io.open(path, "w", encoding="utf-8", newline="").write("\n".join(lines))
    print("  → 已写入（备份 %s）" % os.path.basename(path + ".bak_decimal"))
    return "changed"


STATUS_CN = {
    "changed": "已修改",
    "ok": "本来就对（幂等，未改动）",
    "needs": "需要修（--check 只看不改）",
    "skip": "文件不存在，已跳过",
    "error": "失败",
}


def main():
    print("小数输入框校验类型修复（m_ContentType/m_KeyboardType/m_CharacterValidation → 3/2/2）")
    results = []
    for p in PREFABS:
        local = to_local(p)
        print("\n[%s]" % local)
        status = patch_file(local)
        results.append((local, status))
        print("  ⟹ %s" % STATUS_CN[status])
    print()
    bad = [p for p, s in results if s == "error"]
    if bad:
        print("结果：%d 个文件处理失败（见上）" % len(bad))
        sys.exit(1)
    if any(s == "needs" for _p, s in results):
        print("结果：仍有文件需要修（上面标了「待修」）")
        sys.exit(1)
    if any(s == "changed" for _p, s in results):
        print("结果：已修好并写盘 —— 别忘了在 Unity 里**重打 AB**，否则改动不进游戏。")
    else:
        print("结果：全部本来就对，未改动任何文件。")
    print("提醒：C# 侧 ConfigPresenter.EnsureDecimalInput() 对老 AB 有运行期兜底，"
          "不重打 AB 也能用；两条路都留着（预制件是对的，代码防它再变错）。")


if __name__ == "__main__":
    main()
