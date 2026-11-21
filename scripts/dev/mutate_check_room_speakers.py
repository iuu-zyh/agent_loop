#!/usr/bin/env python3
"""变异验证 —— 群聊「每轮发言人数上限」配置键（room.max_speakers_per_round，09-18）。

**为什么必须有这个脚本**：这个键的失效方式全是**静默**的 ——
  · 装配层不传值 ⇒ 永远吃 `RoomFlow.__init__` 的默认 3，面板上改了也没用（没有任何报错）；
  · C# 回填缺省写错 ⇒ 面板显示一个后端不用的数（显示 3、实际 5）；
  · 预制件补丁没有这一行 ⇒ 重打 AB 也补不出来，玩家只是"看不见这个设置"；
  · 夹取口径丢了 ⇒ 填 0 变成"一轮谁都不许开口"（群聊停摆），而界面上看起来正常。

每个变异回退一处，确认对应的行为测试 / 源码契约真的变红。两道保险绳同
`mutate_check_llm_timeout.py`：跑前先跑**未变异的基线**（基线不绿就直接退出），
跑完逐字节 md5 校验没留残留。

用法：
    python3 scripts/dev/mutate_check_room_speakers.py          # 全部
    python3 scripts/dev/mutate_check_room_speakers.py ① ④      # 只跑名字含这些子串的
"""
from __future__ import annotations
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent))
import _mutate_lib  # noqa: E402  （同进程跑测层，见该文件顶部说明）

import hashlib
import re
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOADER = ROOT / "config" / "config_loader.py"
STORE = ROOT / "config" / "config_store.py"
FLOW = ROOT / "room" / "room_flow.py"
WS = ROOT / "transport" / "ws_channel.py"
SERVER = ROOT / "scripts/server.py"
PATCH = ROOT / "scripts/dev/prefab_patch_config_groups.py"
REFS = ROOT / "csharp/UI/ConfigPanelRefs.cs"
BUILDER = ROOT / "csharp/UI/ConfigUiBuilder.cs"
AB_PANEL = ROOT / "csharp/UI/AbConfigPanel.cs"
PRESENTER = ROOT / "csharp/UI/ConfigPresenter.cs"

PYTEST = [sys.executable, "-X", "utf8", "-m", "pytest",
          "tests/test_room_speakers_setting.py", "tests/test_room_speakers_ui_contract.py",
          "tests/test_room.py", "-q"]

MUTATIONS = [
    (
        "① 默认值不是 3（面板占位/后端默认两边漂）",
        LOADER,
        [('        "max_speakers_per_round": 3,', '        "max_speakers_per_round": 4,')],
    ),
    (
        "② 装配层不把配置传给 ChatHub（面板改了永远不生效）",
        WS,
        [("            max_speakers_per_round=max_speakers_per_round,\n", "")],
    ),
    (
        "③ server.py 装配时不读配置（同上，从另一头断）",
        SERVER,
        [('        max_speakers_per_round=int(room.get("max_speakers_per_round", 3)),\n', "")],
    ),
    (
        "④ 热生效分支不再调 setter（保存后要重启才生效）",
        SERVER,
        [("                _hub.rooms.set_max_speakers_per_round(new_k)\n", "")],
    ),
    (
        "⑤ room 块掉出热分类（保存后被判为「需要重启 Python」）",
        STORE,
        [('HOT_BLOCKS = ("llm", "ui", "initiative", "compaction", "player", "room")',
          'HOT_BLOCKS = ("llm", "ui", "initiative", "compaction", "player")')],
    ),
    (
        "⑥ 下限夹取丢掉（填 0 变成一轮谁都不许开口）",
        STORE,
        [('    ("room", "max_speakers_per_round"): (1, None),\n', "")],
    ),
    (
        "⑦ 块内键不进白名单（面板一保存就把整份表单 fail-closed 掉）",
        STORE,
        [('    "room": ("max_speakers_per_round",),\n', "")],
    ),
    (
        "⑧ RoomFlow 的 setter 不夹取（0/负数直接把 K 变成 0）",
        FLOW,
        [("            new_k = max(1, int(value))", "            new_k = int(value)")],
    ),
    (
        "⑨ 预制件补丁里没有这一行（重打 AB 也补不出来）",
        PATCH,
        [('    ("RoomSpeakersRow", "row"),                   #   每轮发言人数上限 room.max_speakers_per_round\n', "")],
    ),
    (
        "⑩ 预制件补丁漏了【群聊】组标题（行会挂到上一组下面）",
        PATCH,
        [('    ("GroupHeader_Room", "群聊"),\n', "")],
    ),
    (
        "⑪ AbConfigPanel 不找这个节点（AB 里有行也读不到）",
        AB_PANEL,
        [('            r.RoomSpeakersInput = FindComp<InputField>(root, Llm + "RoomSpeakersRow/Input");\n', "")],
    ),
    (
        "⑫ 代码版面板把它做成小数框（整数框克隆源被抄成 ModelRow 那类）",
        BUILDER,
        [('refs.RoomSpeakersInput = MakeFormRow(llmContentRt, "RoomSpeakersRow", "群聊每轮发言人数上限（即时生效）", "3", InputField.ContentType.IntegerNumber);',
          'refs.RoomSpeakersInput = MakeFormRow(llmContentRt, "RoomSpeakersRow", "群聊每轮发言人数上限（即时生效）", "3", InputField.ContentType.DecimalNumber);')],
    ),
    (
        "⑬ 面板回填缺省改成 5（显示一个后端不用的值）",
        PRESENTER,
        [('SetText(_refs.RoomSpeakersInput, data["room"]?["max_speakers_per_round"]?.ToString() ?? "3");',
          'SetText(_refs.RoomSpeakersInput, data["room"]?["max_speakers_per_round"]?.ToString() ?? "5");')],
    ),
    (
        "⑭ 留空也提交（老 AB 一保存就把手编的值冲掉）",
        PRESENTER,
        [("            if (submitRoomSpeakers) cfg[\"room\"] = new JObject { [\"max_speakers_per_round\"] = roomSpeakers };",
          "            cfg[\"room\"] = new JObject { [\"max_speakers_per_round\"] = roomSpeakers };")],
    ),
    (
        "⑮ 小于 1 不在本地拦（用户以为填了 0，实际被后端抬到 1）",
        PRESENTER,
        [("                if (roomSpeakers < 1)", "                if (false)")],
    ),
    (
        "⑯ ConfigPanelRefs 没有这一行的字段（C# 编译就断，防呆）",
        REFS,
        [("        public InputField RoomSpeakersInput;\n", "")],
    ),
]


def _md5(p: Path) -> str:
    return hashlib.md5(p.read_bytes()).hexdigest()


def _backup(p: Path) -> Path:
    tmp = Path(tempfile.gettempdir()) / ("mutate_roomspeakers_" + p.name)
    shutil.copy2(p, tmp)
    return tmp


def _check(name: str, path: Path, repls) -> bool:
    bak = _backup(path)
    try:
        src = path.read_text(encoding="utf-8")
        for old, new in repls:
            if old not in src:
                print("  ✗ %s：待替换片段不存在（脚本要更新）：%r" % (name, old[:70]))
                return False
            src = src.replace(old, new)
        path.write_text(src, encoding="utf-8")
        r = _mutate_lib.run(PYTEST, cwd=str(ROOT), capture_output=True, text=True,
                            encoding="utf-8", errors="replace")
        if r.returncode == 0:
            print("  ✗ %s：测试仍然通过 —— 这条判据没有被任何用例覆盖" % name)
            return False
        out = (r.stdout or "") + (r.stderr or "")
        red = re.findall(r"^FAILED \S+::(\S+)", out, re.M)
        print("  ✓ %s：测试如实变红（%s）" % (name, "、".join(red[:3]) or "见输出"))
        return True
    finally:
        shutil.copy2(bak, path)


def main() -> int:
    only = [a for a in sys.argv[1:] if not a.startswith("-")]
    picked = [(n, p, r) for n, p, r in MUTATIONS if not only or any(o in n for o in only)]
    files = sorted({p for _n, p, _r in picked})
    _mutate_lib.guard(files)   # 被中断也要还原（见 _mutate_lib.guard）
    before = {p: _md5(p) for p in files}
    print("[群聊发言人数上限 · tests/test_room_speakers_setting.py + "
          "tests/test_room_speakers_ui_contract.py + tests/test_room.py]"
          + ("  仅跑：%s" % "、".join(only) if only else ""))

    base = _mutate_lib.run(PYTEST, cwd=str(ROOT), capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
    if base.returncode != 0:
        print("★ 基线就是红的 —— 先修源码，变异结果不可信：")
        print((base.stdout or "")[-2000:])
        return 2
    print("  基线绿（未变异的源码树本来通过）")

    ok = True
    for name, path, repls in picked:
        ok &= _check(name, path, repls)

    after = {p: _md5(p) for p in files}
    if after != before:
        for p in files:
            if after[p] != before[p]:
                print("★ 未还原：%s（%s → %s）" % (p.relative_to(ROOT), before[p], after[p]))
        ok = False
    else:
        print("  逐字节还原校验通过（%d 个文件 md5 一致）" % len(files))

    total = len(picked)
    hit = sum(1 for n, p, r in picked if True) if ok else 0
    print("== %d/%d 条变异被如实捕获 ==" % (hit if ok else total - 1, total))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
