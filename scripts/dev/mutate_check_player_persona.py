#!/usr/bin/env python3
"""变异验证 —— 自我人设（config.player.persona → system 的一段 + 面板虚拟条目）的测试（09-16）。

**为什么必须有这个脚本**：本仓库吃过恒真断言的亏，所以"新增测试"的验收标准不是"跑绿了"，
而是"**把实现逐个回退，对应的测试真的变红**"。这个脚本就是那条验收线；留着它，下次动
`player.persona` 的归一/注入/热落地、或动提示词页那条虚拟条目时，能一条命令重跑。

覆盖的测试：
  · `tests/test_player_persona.py` —— 配置归一 / 段注入与移除 / **真跑回合看 sys 差分** /
    虚拟条目 / ConfigService 热落地。
  · `tests/test_player_persona_ui_contract.py` —— C# 面板接线（前缀契约、SelectFile 分流、
    批量保存剔除、空串照提交、JSON null 安全读取、列表 label、不可删）。

用法：
    python3 scripts/dev/mutate_check_player_persona.py          # 全跑（默认）
    python3 scripts/dev/mutate_check_player_persona.py py       # 只跑 Python 行为侧
    python3 scripts/dev/mutate_check_player_persona.py cs       # 只跑 C# 接线侧

注意：会**临时改源码再还原**（备份在系统临时目录），跑完请确认末行是"全部变异都被捕获"。
"""

from __future__ import annotations
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent))
import _mutate_lib  # noqa: E402  （同进程跑测层，见该文件顶部说明）


import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOADER = ROOT / "config" / "config_loader.py"
STORE = ROOT / "config" / "config_store.py"
PROMPT = ROOT / "agent" / "system_prompt.py"
PFILES = ROOT / "prompts" / "prompt_files.py"
SERVER = ROOT / "scripts/server.py"
PRESENTER = ROOT / "csharp/UI/ConfigPresenter.cs"

PY_BEHAVIOR = "tests/test_player_persona.py"
PY_CONTRACT = "tests/test_player_persona_ui_contract.py"

# ---------------------------------------------------------------------------
# Python 行为侧：回退实现，期望对应测试变红
# ---------------------------------------------------------------------------
PY_MUTATIONS = [
    (
        "① 清空后不移除段（留一段空文本）",
        PROMPT,
        """        if not t:
            self.global_layer.sections.pop(self.PLAYER_PERSONA_SECTION, None)
            return False""",
        """        if not t:
            self.global_layer.sections[self.PLAYER_PERSONA_SECTION] = {
                "name": self.PLAYER_PERSONA_SECTION,
                "order": self.PLAYER_PERSONA_ORDER,
                "text": "",
            }
            return False""",
        "test_clear_removes_section",
    ),
    (
        "② 段序挪到 NPC 专属人设之后（每 NPC 都废提示词缓存）",
        PROMPT,
        "    PLAYER_PERSONA_ORDER = -70",
        "    PLAYER_PERSONA_ORDER = 50",
        "test_injected_text_and_order",
    ),
    (
        "③ 去掉包裹模板（裸文本直塞 system）",
        PROMPT,
        '''            "text": self.PLAYER_PERSONA_TEMPLATE.format(persona=t),''',
        '''            "text": t,''',
        "test_injected_text_and_order",
    ),
    (
        "④ 去掉「系统组装」标签（UI 里显示裸段名）",
        PROMPT,
        '        "config:player_persona": "自我人设",   # 「系统组装」折叠块里这一行的标题（缺了会显示裸段名）\n',
        "",
        "test_sys_label_present",
    ),
    (
        "⑤ 超长不再拒收（静默放行 → 每回合都把 4000+ 字塞进 system）",
        STORE,
        "        if len(text) > _PERSONA_MAX:",
        "        if False:",
        "test_type_and_length_fail_closed",
    ),
    (
        "⑥ 空串被当成「没改」跳过（用户清不掉人设）",
        STORE,
        """            if existed and target.get(key) == value:
                continue  # 与现值相同：不计入改动（UI 全量提交也能给出准确 effective）""",
        """            if existed and target.get(key) == value:
                continue  # 与现值相同：不计入改动（UI 全量提交也能给出准确 effective）
            if block == "player" and key == "persona" and value == "":
                continue  # mutated: 空串不写回""",
        "test_empty_string_is_written_back_not_deleted",
    ),
    (
        "⑦ player 不再是热生效块（改完要重启才生效）",
        STORE,
        'HOT_BLOCKS = ("llm", "ui", "initiative", "compaction", "player")',
        'HOT_BLOCKS = ("llm", "ui", "initiative", "compaction")',
        "test_set_get_roundtrip_and_hot",
    ),
    (
        "⑧ 列表里不再下发虚拟条目",
        PFILES,
        """    out: List[Dict[str, Any]] = [{
        # 「自我人设」虚拟条目：**永远排第一**（两种视图都给 —— 它是全局的，不属于任何 NPC）。""",
        """    out: List[Dict[str, Any]] = []
    if False: _unreachable = [{""",
        "test_virtual_entry_first_and_not_deletable",
    ),
    (
        "⑨ 虚拟条目变成可删（点删除会发给 delete_prompt）",
        PFILES,
        '''        "deletable": False,        # 它不是文件，删不掉（删 = 清空内容，把编辑框清空再保存）''',
        '''        "deletable": True,''' ,
        "test_virtual_entry_first_and_not_deletable",
    ),
    (
        "⑩ 文件接口不再拒绝 config: 路径（能写出一个怪文件/整批失败）",
        PFILES,
        """    if rel.startswith(VIRTUAL_PREFIX):
        raise ValueError(""",
        """    if False:
        raise ValueError(""",
        "test_virtual_path_rejected_by_file_apis",
    ),
    (
        "⑪ 差分：注入后段名对不上（UI 拿不到这一行）",
        PROMPT,
        '    PLAYER_PERSONA_SECTION = "config:player_persona"',
        '    PLAYER_PERSONA_SECTION = "config:player_persona_typo"',
        "test_diff_persona_appears_then_only_it_changes",
    ),
    (
        "⑫ 保存后不重新注入（要重启才生效）",
        SERVER,
        """        if any(k.startswith("player.") for k in hot):
            try:
                old = SystemPrompt.instance().player_persona()
                installed = install_player_persona()""",
        """        if False:
            try:
                old = SystemPrompt.instance().player_persona()
                installed = install_player_persona()""",
        "test_configservice_hot_installs_and_clears_persona",
    ),
]

# ---------------------------------------------------------------------------
# C# 接线侧：回退面板分流，期望对应契约测试变红
# ---------------------------------------------------------------------------
CS_MUTATIONS = [
    (
        "① 选中虚拟条目仍去 read_prompt（Python 拒绝 → 面板报错）",
        PRESENTER,
        "            if (virtualItem)\n            {",
        "            if (false)\n            {",
        "test_select_file_routes_virtual_before_read_prompt",
    ),
    (
        "② 虚拟条目混进批量保存（整页保存一起失败）",
        PRESENTER,
        '                if (IsVirtualConfigPath(kv.Key)) { hasPersona = true; persona = kv.Value ?? ""; continue; }',
        '                if (IsVirtualConfigPath(kv.Key)) { hasPersona = true; persona = kv.Value ?? ""; }',
        "test_save_prompt_excludes_virtual_from_bulk",
    ),
    (
        "③ 空串不提交（清不掉人设）",
        PRESENTER,
        """            var payload = new JObject { ["player"] = new JObject { ["persona"] = persona ?? "" } };""",
        """            var payload = new JObject { ["player"] = new JObject { ["persona"] = persona ?? "" } };
            if ((persona ?? "").Length == 0) return;   // mutated: 空串不提交""",
        "test_save_persona_submits_empty_string",
    ),
    (
        "④ 用 Value<string>() 读人设（JSON null 时抛异常）",
        PRESENTER,
        '_playerPersona = data["player"]?["persona"]?.ToString() ?? "";',
        '_playerPersona = data["player"]?["persona"]?.Value<string>();',
        "test_request_config_reads_persona_null_safe",
    ),
    (
        "⑤ 脏值不优先（get_config 刷新冲掉未保存的编辑）",
        PRESENTER,
        """            string txt;
            if (!_promptCache.TryGetValue(_currentPath, out txt)) txt = _playerPersona ?? "";
            SetPromptEditorText(txt);""",
        """            SetPromptEditorText(_playerPersona ?? "");""",
        "test_persona_editor_prefers_dirty_buffer",
    ),
    (
        "⑥ 前缀与 Python 不一致",
        PRESENTER,
        'private const string VirtualConfigPrefix = "config:";',
        'private const string VirtualConfigPrefix = "cfg:";',
        "test_prefix_matches_python_side",
    ),
    (
        "⑦ 本地兜底允许删虚拟条目",
        PRESENTER,
        '            if (group == "player") return false;   // 虚拟条目（自我人设）：它不是文件，删不掉；清空 = 清空编辑框再保存\n',
        "",
        "test_virtual_entry_not_deletable_locally",
    ),
    (
        "⑧ 列表不再用 Python 下发的 label（显示成「自我人设/自我人设」）",
        PRESENTER,
        '                string disp = item?["label"]?.ToString();',
        '                string disp = null;',
        "test_file_list_prefers_python_label",
    ),
]

_BAK = Path(tempfile.gettempdir()) / "mutate_check_player_persona"


# 每个变异落在哪个测试文件：按节点名查表（不猜、不重复）
_NODE_FILE = {m[4]: PY_BEHAVIOR for m in PY_MUTATIONS}
_NODE_FILE.update({m[4]: PY_CONTRACT for m in CS_MUTATIONS})


def _run_one(node: str | None):
    """跑基线（两个文件全跑）或单个测试节点。"""
    if node is None:
        p = _mutate_lib.run(
            [sys.executable, "-m", "pytest", PY_BEHAVIOR, PY_CONTRACT, "-q", "--no-header"],
            cwd=str(ROOT), capture_output=True, text=True, errors="replace",
        )
    else:
        target = f"{_NODE_FILE.get(node, PY_BEHAVIOR)}::{node}"
        p = _mutate_lib.run(
            [sys.executable, "-m", "pytest", target, "-q", "--no-header"],
            cwd=str(ROOT), capture_output=True, text=True, errors="replace",
        )
    return p.returncode, p.stdout + p.stderr


def _check_set(label: str, mutations) -> int:
    print(f"\n=== {label} ===")
    files = sorted({m[1] for m in mutations})
    _mutate_lib.guard(files)   # 被中断也要还原（见 _mutate_lib.guard）
    _BAK.mkdir(exist_ok=True)
    for f in files:
        shutil.copy2(f, _BAK / f.name)

    rc, out = _run_one(None)
    print("基线：exit=", rc)
    if rc != 0:
        print("★ 基线不是绿的，先修基线：\n", out[-800:])
        return len(mutations) + 1

    bad = 0
    for name, path, old, new, expect in mutations:
        for f in files:                      # 每轮先全量还原，防上一轮污染
            shutil.copy2(_BAK / f.name, f)
        src = path.read_text(encoding="utf-8")
        if old not in src:
            print(f"✗ {name}: 找不到待替换文本（测试自身失效，需更新本脚本）")
            bad += 1
            continue
        path.write_text(src.replace(old, new, 1), encoding="utf-8")
        rc, out = _run_one(expect)
        ok = rc != 0 and expect in out
        print(f"{'✓' if ok else '✗'} {name} → exit={rc} 命中[{expect}]={expect in out}")
        if not ok:
            bad += 1
            print("   尾部：", [l for l in out.strip().splitlines() if l.strip()][-4:])

    for f in files:                          # 还原
        shutil.copy2(_BAK / f.name, f)
    rc, out = _run_one(None)
    print("还原后：exit=", rc)
    if rc != 0:
        print(f"★★ 还原失败！从 {_BAK} 手工恢复")
        return len(mutations) + 2
    return bad


def main() -> int:
    which = (sys.argv[1] if len(sys.argv) > 1 else "all").lower()
    bad = 0
    if which in ("all", "py"):
        bad += _check_set("Python 行为（归一 / 注入 / 差分 / 虚拟条目 / 热落地）", PY_MUTATIONS)
    if which in ("all", "cs"):
        bad += _check_set("C# 面板接线（分流 / 空串提交 / null 安全 / 列表）", CS_MUTATIONS)
    print("\n全部变异都被捕获" if bad == 0 else f"\n有 {bad} 项未被捕获（测试有洞）")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
