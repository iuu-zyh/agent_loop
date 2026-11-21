#!/usr/bin/env python3
"""变异验证 —— 界面文案「不许出现 AI 字样」（09-18，用户：玩家反映界面里不要出现 AI）。

**为什么必须有这个脚本**：这条改动的失效方式**完全静默** —— 某处文案回退成 "AI 对话" /
"AI 设置"，没有任何报错、没有任何测试会红（除非专门钉住），只是玩家又出戏一次。
本轮踩到的实况：预制件里烧死的四行标签原先只有两行被运行期改写，另两行一直显示
"AI 单次超时秒·压缩" / "AI 失败重试次数"，谁都没发现。

每条变异回退一处文案或一层改写，确认 `tests/test_no_ai_wording_contract.py`（+ 受影响的
`test_llm_timeout_ui_contract.py`）真的变红。两道保险绳同 `mutate_check_room_speakers.py`：
跑前先跑**未变异的基线**（不绿直接退出），跑完逐字节 md5 校验没留残留。

用法：
    python3 scripts/dev/mutate_check_ai_wording.py            # 全部
    python3 scripts/dev/mutate_check_ai_wording.py ③ ⑦         # 只跑名字含这些子串的
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
NPC_BTN = ROOT / "csharp/UI/NpcPanelButton.cs"
DRAMA = ROOT / "csharp/UI/DramaAiOption.cs"
BRAIN = ROOT / "csharp/bridge/BrainLink.cs"
PRESENTER = ROOT / "csharp/UI/ConfigPresenter.cs"
BUILDER = ROOT / "csharp/UI/ConfigUiBuilder.cs"
AB_PANEL = ROOT / "csharp/UI/AbConfigPanel.cs"
CHAT_LAUNCHER = ROOT / "csharp/npc/ChatLauncher.cs"
AB_CHAT = ROOT / "csharp/UI/AbChatPanel.cs"
SUFFIX = ROOT / "prompts/personas/_suffix.txt"
WORLD_BASIS = ROOT / "prompts/sections/world_basis.txt"
COMPACTION = ROOT / "prompts/compaction/default.md"
PACK = ROOT / "scripts/pack_release.py"
PREFAB = Path("/mnt/e/SteamLibrary/steamapps/common/鬼谷八荒/Mod/modFQA/资源修改教程"
              "/ResBuildABProject/Assets/Resources/UI/UIConfigAi.prefab")

PYTEST = [sys.executable, "-X", "utf8", "-m", "pytest",
          "tests/test_no_ai_wording_contract.py", "tests/test_llm_timeout_ui_contract.py", "-q"]

MUTATIONS = [
    # ---------- 两枚按钮：玩家最常看到的那两处 ----------
    (
        "① NPC 面板按钮回退成「AI 对话」",
        NPC_BTN,
        [('CreateVisualRow(panel.transform, ButtonName, "对话", 0, grid);',
          'CreateVisualRow(panel.transform, ButtonName, "AI 对话", 0, grid);')],
    ),
    (
        "② 剧情窗按钮回退成「AI 对话」",
        DRAMA,
        [('private const string Label = "对话";', 'private const string Label = "AI 对话";')],
    ),
    # ---------- 状态提示（对话窗系统提示 / 通讯录状态条）----------
    (
        "③ 状态条回退成「AI 服务已停止…」（玩家一断线就看到）",
        BRAIN,
        [('传音法阵已停止（未找到可执行入口，无法自动重启）',
          'AI 服务已停止（未找到可执行入口，无法自动重启）')],
    ),
    (
        "④ 被拦提示回退成「AI 正在唤醒…」",
        BRAIN,
        [('"传音法阵正在接通，稍等几秒再发"', '"AI 正在唤醒，稍等几秒再发"')],
    ),
    # ---------- 配置面板：校验提示 / 悬停说明 ----------
    (
        "⑤ 校验提示回退成「AI 硬顶不能为负…」",
        PRESENTER,
        [('"硬顶不能为负（留空 = 不改这一项）"', '"AI 硬顶不能为负（留空 = 不改这一项）"')],
    ),
    (
        "⑥ 悬停说明回退成「AI 首字超时秒（llm.timeout…）」",
        PRESENTER,
        [('"首字超时秒（llm.timeout，默认 120）', '"AI 首字超时秒（llm.timeout，默认 120）')],
    ),
    (
        "⑦ 代码版面板标题回退成「AI 设置」",
        BUILDER,
        [('MakeText("TitleLabel", titleRt, "智能体设置", 19f',
          'MakeText("TitleLabel", titleRt, "AI 设置", 19f')],
    ),
    # ---------- AB 侧运行期改写（玩家实际看到的那一层）----------
    (
        "⑧ 窗口标题不再运行期改写（老 AB 里就是「AI 设置」）",
        AB_PANEL,
        [('            var titleLbl = FindComp<Text>(root, "BG/Title/TitleLabel");\n'
          '            if (titleLbl != null) titleLbl.text = "智能体设置";\n', "")],
    ),
    (
        "⑨ 四行标签只改两行（另两行照旧显示 AI …，本轮踩到的原样）",
        AB_PANEL,
        [('            RelabelRow(root, Llm + "LlmRetriesRow", "失败重试次数（即时生效）");\n', "")],
    ),
    # ---------- 日志里的 why 文案（会随日志发给作者）----------
    (
        "⑩ 登记日志回退成 why=\"AI 对话\"",
        CHAT_LAUNCHER,
        [('UnitIdRegistry.Remember(id, unitId, "打开对话")',
          'UnitIdRegistry.Remember(id, unitId, "AI 对话")')],
    ),
    # ---------- 提示词（面板「提示词」页可读）----------
    (
        "⑪ 输出底线回退成「禁止透露你是 AI…」",
        SUFFIX,
        [("禁止透露你的真实来历（你并非什么程序，也不来自现实世界），禁止出现现代科技腔调",
          "禁止透露你是 AI、模型或程序，禁止出现 AI 腔")],
    ),
    (
        "⑫ 压缩指令回退成「绝不暴露 AI/模型/程序身份」",
        COMPACTION,
        [("绝不暴露自己的真实来历", "绝不暴露 AI/模型/程序身份")],
    ),
    (
        "⑬ 世界基础回退成「是不是AI」",
        WORLD_BASIS,
        [("当我问起你的来历、或问你是不是真人时", "当我问你是不是AI的时候")],
    ),
    # ---------- 安装说明（随包发给玩家，教的是"点哪枚按钮"）----------
    (
        "⑭ 安装说明回退成「NPC 面板「AI 对话」」",
        PACK,
        [("· 打开对话窗（NPC 面板「对话」/ 传音簿点一行）",
          "· 打开对话窗（NPC 面板「AI 对话」/ 传音簿点一行）")],
    ),
    # ---------- 预制件源头（下次重打 AB 会带回来）----------
    (
        "⑮ 预制件源头把「AI 设置」写回去",
        PREFAB,
        [('m_Text: "\\u667A\\u80FD\\u4F53\\u8BBE\\u7F6E"', 'm_Text: "AI \\u8BBE\\u7F6E"')],
    ),
]


def _md5(p: Path) -> str:
    return hashlib.md5(p.read_bytes()).hexdigest()


def _backup(p: Path) -> Path:
    tmp = Path(tempfile.gettempdir()) / ("mutate_aiwording_" + p.name)
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
    picked = [(n, p, r) for n, p, r in MUTATIONS
              if (not only or any(o in n for o in only)) and p.is_file()]
    skipped = [n for n, p, _ in MUTATIONS if not p.is_file()]
    files = sorted({p for _n, p, _r in picked})
    _mutate_lib.guard(files)   # 被中断也要还原（见 _mutate_lib.guard）
    before = {p: _md5(p) for p in files}
    print("[界面文案无 AI 字样 · tests/test_no_ai_wording_contract.py + test_llm_timeout_ui_contract.py]"
          + ("  仅跑：%s" % "、".join(only) if only else ""))
    if skipped:
        print("  · 跳过（本机没有这些文件）：%s" % "、".join(skipped))

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
    hit = total if ok else total - 1
    print("== %d/%d 条变异被如实捕获 ==" % (hit, total))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
