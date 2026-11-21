#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""变异验证 —— 「重置为默认」按钮（prompts 还原 + C# 接线 + 预制件几何）的测试（09-16）。

**为什么必须有这个脚本**：本仓库吃过恒真断言的亏，所以"新增测试"的验收标准不是"跑绿了"，
而是"**把实现逐个回退，对应的测试真的变红**"。这个脚本就是那条验收线；留着它，下次动
`reset_prompt` 的来源/白名单、动提示词页那条重置接线、或重跑预制件补丁时，能一条命令重跑。

覆盖的测试：
  · `tests/test_prompt_reset.py` —— 来源唯一 / 系统件可还原 / 用户新增件拒绝 / 字节幂等 /
    换行不动点 / 丢失文件修复 / RPC 路由。
  · `tests/test_prompt_reset_ui_contract.py` —— C# 接线（缓存同步、虚拟条目分流、
    TwoStepConfirm 复用、列宽四份定义一致、真预制件自校验）。
  · `csharp/tests/TwoStepConfirmTests` —— 两段确认判定表（用 `run_csharp_tests.py --only` 跑）。

用法：
    python3 scripts/dev/mutate_check_prompt_reset.py          # 全跑（默认）
    python3 scripts/dev/mutate_check_prompt_reset.py py       # 只跑 Python 侧
    python3 scripts/dev/mutate_check_prompt_reset.py cs       # 只跑 C# 接线 + 判定表

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
TEXTIO = ROOT / "config" / "textio.py"
PATHS = ROOT / "config" / "paths.py"
PFILES = ROOT / "prompts" / "prompt_files.py"
WS = ROOT / "transport" / "ws_channel.py"
PRESENTER = ROOT / "csharp/UI/ConfigPresenter.cs"
BUILDER = ROOT / "csharp/UI/ConfigUiBuilder.cs"
TWO_STEP = ROOT / "csharp/UI/TwoStepConfirm.cs"

PY_BEHAVIOR = "tests/test_prompt_reset.py"
PY_CONTRACT = "tests/test_prompt_reset_ui_contract.py"
CS_PROJ = "TwoStepConfirmTests"

# ---------------------------------------------------------------------------
# Python 行为侧：回退实现，期望对应测试变红
# ---------------------------------------------------------------------------
PY_MUTATIONS = [
    (
        "① 默认源拿成用户副本（自我拷贝 → 重置永远无效）",
        PFILES,
        "    return Path(defaults) if defaults is not None else _paths.bundled_prompts_root()",
        "    return Path(defaults) if defaults is not None else _prompts_root()",
        "test_reset_source_is_bundled_not_user_tree",
    ),
    (
        "② 重置套用 _allow_create 门（sections 再也还原不了）",
        PFILES,
        """    defaults_root = _defaults_root(defaults)
    src = _default_source(rel, defaults_root)""",
        """    defaults_root = _defaults_root(defaults)
    if not _allow_create(rel.split("/")[0], target.name):
        raise ValueError("mutated: 系统件不许重置")
    src = _default_source(rel, defaults_root)""",
        "test_reset_allows_system_files",
    ),
    (
        "③ 目标必须已存在（丢失的文件修不回来）",
        PFILES,
        "    target = _resolve(rel, root, must_exist=False)\n\n    defaults_root = _defaults_root(defaults)",
        "    target = _resolve(rel, root, must_exist=True)\n\n    defaults_root = _defaults_root(defaults)",
        "test_reset_restores_missing_file",
    ),
    (
        "④ 不再拒绝用户新增件（无默认版本也照写 → 变成「清空文件」）",
        PFILES,
        """    if src is None:
        raise ValueError(""",
        """    if src is None:
        return {"path": rel, "reset": True, "changed": False, "text": "", "chars": 0}
    if False:
        raise ValueError(""",
        "test_reset_refuses_user_created_file",
    ),
    (
        "⑤ 虚拟条目不再被拒（config: 路径混进文件接口）",
        PFILES,
        """    if rel.startswith(VIRTUAL_PREFIX):
        raise ValueError(""",
        """    if rel.startswith(VIRTUAL_PREFIX) and False:
        raise ValueError(""",
        "test_reset_refuses_virtual_config_path",
    ),
    (
        "⑥ 永远重写盘（丢掉 changed=False 的幂等判定）",
        PFILES,
        """        if target.is_file() and target.read_bytes() == src.read_bytes():
            changed = False""",
        """        if False:
            changed = False""",
        "test_reset_twice_is_byte_idempotent",
    ),
    (
        "⑦ 列表不再下发 resettable（UI 无从预判）",
        PFILES,
        '                "resettable": _default_source(rel, defaults_root) is not None,',
        '                "resettable": True,',
        "test_list_prompts_resettable_flag",
    ),
    (
        "⑧ 读侧不做换行归一（CRLF 残留 → 进 system prompt + 字节比对永远判'有变化'）",
        TEXTIO,
        "            return normalize_newlines(raw.decode(enc))",
        "            return raw.decode(enc)",
        "test_textio_lf_fixed_point",
    ),
    (
        "⑨ 写侧把换行翻译成 CRLF（= Windows 上 newline=None 的默认行为）",
        TEXTIO,
        '    with open(p, "w", encoding="utf-8", newline="") as f:',
        '    with open(p, "w", encoding="utf-8", newline="\\r\\n") as f:',
        "test_textio_lf_fixed_point",
    ),
    (
        "⑩ 内置副本根退回数据根（= 拿用户副本当默认）",
        PATHS,
        "def bundled_prompts_root() -> Path:",
        "def bundled_prompts_root() -> Path:\n    return data_root() / \"prompts\"   # mutated: 退回数据根",
        "test_bundled_root_does_not_follow_data_root",
    ),
    (
        "⑪ ChatHub 少了 reset_prompt 路由（功能整个消失）",
        WS,
        '        if method == "reset_prompt":',
        '        if method == "reset_prompt_disabled":',
        "test_hub_reset_prompt_routing",
    ),
    (
        "⑫ 旧服务缺方法时静默成功（不 fail-closed）",
        WS,
        """            if hasattr(self._prompt_service, "reset_prompt"):
                result = self._prompt_service.reset_prompt(rel)
            else:   # 兼容旧 mock 服务：直接按不支持处理（fail-closed，不静默成功）
                raise ValueError("提示词服务不支持重置（reset_prompt 未实现）")""",
        """            if hasattr(self._prompt_service, "reset_prompt"):
                result = self._prompt_service.reset_prompt(rel)
            else:
                result = {"path": rel, "reset": True, "changed": False}""",
        "test_hub_reset_prompt_old_service_fails_closed",
    ),
]

# ---------------------------------------------------------------------------
# C# 接线侧：回退接线，期望对应契约测试变红
# ---------------------------------------------------------------------------
CS_MUTATIONS = [
    (
        "① 重置后不同步 _promptCache（下次保存把旧内容写回）",
        PRESENTER,
        "                _promptCache[path] = txt;\n",
        "",
        "test_reset_syncs_prompt_cache",
    ),
    (
        "② 虚拟条目也发 reset_prompt（Python 拒绝 → 报错看不懂）",
        PRESENTER,
        '                SavePersona("", -1);\n                return;',
        '                _ws.SendRequest("reset_prompt", new JObject { ["path"] = path }, null, 15000);\n                return;',
        "test_virtual_item_clears_via_set_config_not_reset_prompt",
    ),
    (
        "③ 本地不预判 resettable（用户新增件也武装 → 点了才报错）",
        PRESENTER,
        "                if (!_resettable.TryGetValue(path, out can)) can = true;",
        "                can = true;",
        "test_reset_checks_resettable_flag_before_arming",
    ),
    (
        "④ 退回 ?.Value<bool>() 读标记（JSON null 会抛）",
        PRESENTER,
        '                _resettable[path] = rtToken == null || rtToken.ToString() != "False";',
        '                _resettable[path] = item?["resettable"]?.Value<bool>() ?? true;',
        "test_resettable_flag_read_is_null_safe",
    ),
    (
        "⑤ 重置不再用两段确认（一次误点就覆盖手写提示词）",
        PRESENTER,
        "            var step = _resetConfirm.Click(Time.time);",
        "            var step = TwoStepConfirm.Step.Second;",
        "test_both_destructive_buttons_use_two_step_confirm",
    ),
    (
        "⑥ 换文件不解除重置武装（在 A 上武装、在 B 上执行）",
        PRESENTER,
        "            DisarmReset();    // 同理：换文件作废重置武装，严防\"在 A 上武装、在 B 上执行\"\n",
        "",
        "test_select_file_disarms_reset",
    ),
    (
        "⑦ 按钮接线不做 null 判（老 AB 上直接抛 NRE）",
        PRESENTER,
        "            if (_refs?.ResetPromptButton != null)\n                ClickUtils.Attach(GetButton(_refs.ResetPromptButton), ResetCurrentFile);",
        "            ClickUtils.Attach(GetButton(_refs.ResetPromptButton), ResetCurrentFile);",
        "test_reset_button_attach_is_null_safe",
    ),
    (
        "⑧ 列宽常量写成 1/3（删除键被推出面板）",
        BUILDER,
        "            const float ThirdOfRight = 1f / 6f;",
        "            const float ThirdOfRight = 1f / 3f;",
        "test_column_layout_matches_prefab_patch_script",
    ),
    (
        "⑨ 两段确认窗口判定放宽（> 改 >= 之类的手感漂移）",
        TWO_STEP,
        "            if (now - ArmedAt > _window)",
        "            if (now - ArmedAt > _window * 2f)",
        "__csharp__",
    ),
    (
        "⑩ 执行后不解除武装（点一次执行两次）",
        TWO_STEP,
        """            Disarm();
            return Step.Second;""",
        """            return Step.Second;""",
        "__csharp__",
    ),
    (
        "⑪ 未武装也直接执行（最危险：一次误点即不可恢复）",
        TWO_STEP,
        """            if (!Armed)
            {
                Armed = true;
                ArmedAt = now;
                return Step.First;
            }""",
        """            if (!Armed)
            {
                return Step.Second;
            }""",
        "__csharp__",
    ),
]

_BAK = Path(tempfile.gettempdir()) / "mutate_check_prompt_reset"

# 每个变异落在哪个测试文件：按节点名查表（不猜、不重复）
_NODE_FILE = {m[4]: PY_BEHAVIOR for m in PY_MUTATIONS}
_NODE_FILE.update({m[4]: PY_CONTRACT for m in CS_MUTATIONS})


def _run_py(node: str | None, files):
    if node is None:
        p = _mutate_lib.run(
            [sys.executable, "-m", "pytest", *files, "-q", "--no-header"],
            cwd=str(ROOT), capture_output=True, text=True, errors="replace",
        )
    else:
        target = f"{_NODE_FILE.get(node, PY_BEHAVIOR)}::{node}"
        p = _mutate_lib.run(
            [sys.executable, "-m", "pytest", target, "-q", "--no-header"],
            cwd=str(ROOT), capture_output=True, text=True, errors="replace",
        )
    return p.returncode, p.stdout + p.stderr


def _run_cs():
    """跑 TwoStepConfirm 判定表（走 run_csharp_tests.py，它会先清 obj/bin 强制重编）。"""
    p = subprocess.run(
        [sys.executable, "scripts/dev/run_csharp_tests.py", "--only", CS_PROJ, "--quiet"],
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

    if any(m[4] == "__csharp__" for m in mutations):
        rc, out = _run_cs()
        print("基线（C# 判定表）：exit=", rc)
        if rc != 0:
            print("★ 基线不是绿的，先修基线：\n", out[-800:])
            return len(mutations) + 1
    else:
        rc, out = _run_py(None, [PY_BEHAVIOR, PY_CONTRACT])
        print("基线（Python）：exit=", rc)
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
        if expect == "__csharp__":
            rc, out = _run_cs()
            ok = rc != 0
            hit = "判定表变红" if ok else "仍绿"
        else:
            rc, out = _run_py(expect, None)
            hit = f"命中[{expect}]={expect in out}"
            ok = rc != 0 and expect in out
        print(f"{'✓' if ok else '✗'} {name} → exit={rc} {hit}")
        if not ok:
            bad += 1
            print("   尾部：", [l for l in out.strip().splitlines() if l.strip()][-4:])

    for f in files:                          # 还原
        shutil.copy2(_BAK / f.name, f)
    if any(m[4] == "__csharp__" for m in mutations):
        rc, out = _run_cs()
    else:
        rc, out = _run_py(None, [PY_BEHAVIOR, PY_CONTRACT])
    print("还原后：exit=", rc)
    if rc != 0:
        print(f"★★ 还原失败！从 {_BAK} 手工恢复")
        return len(mutations) + 2
    return bad


def main() -> int:
    which = (sys.argv[1] if len(sys.argv) > 1 else "all").lower()
    bad = 0
    if which in ("all", "py"):
        bad += _check_set("Python 行为（来源唯一 / 白名单 / 幂等 / 换行 / 路由）", PY_MUTATIONS)
    if which in ("all", "cs"):
        bad += _check_set("C# 接线 + 两段确认判定表", CS_MUTATIONS)
    print("\n全部变异都被捕获" if bad == 0 else f"\n有 {bad} 项未被捕获（测试有洞）")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
