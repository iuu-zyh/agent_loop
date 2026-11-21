#!/usr/bin/env python3
"""变异验证 —— 气泡观感两件事（09-17）：**预制件文字版式** + **正文空行折叠**。

**为什么必须有这个脚本**：本仓库的验收标准不是"测试跑绿了"，而是"**把实现逐个回退，对应测试真的变红**"。
这两件事的失败模式全是**静默**的：

  · 预制件那侧：padding 少给几单位 ⇒ 文字又画到白底外（用户 09-17 截图）；对齐改回同向 ⇒
    `)` 又贴到最右边；`m_LayoutPriority` 变回 1 ⇒ 长文本气泡恒 76 高、文字被 Truncate **截掉**
    且不报错。三样都**跑得起来、看着像正常 UI**。
  · C# 那侧：四个写泡点接漏一个 ⇒ "某一条路还是大空行"（流式接漏 = 定稿瞬间屏幕跳一下）；
    折叠写过头（把单个换行也吃了）⇒ 模型分的段并成一行。

覆盖：
  · `tests/test_chat_bubble_layout_contract.py` —— 预制件常量 + **真 prefab** + **真 PNG** 量的内缩。
  · `tests/test_bubble_text_contract.py`     —— C# 四个写泡点 / 纯逻辑核 / 测试工程链接。
  · `csharp/tests/BubbleTextTests`           —— 折叠规则的判定表（`run_csharp_tests.py` 跑）。

★ 会临时改**真资产**（AB 打包工程里的 prefab / 源码），跑完自动还原；每轮变异前先全量还原，
  防上一轮污染。末行必须是"全部变异都被捕获"。

用法：
    python3 scripts/dev/mutate_check_bubble_style.py         # 全跑
    python3 scripts/dev/mutate_check_bubble_style.py py      # 只跑 Python 接线/资产侧
    python3 scripts/dev/mutate_check_bubble_style.py cs      # 只跑 C# 判定表侧
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

LAYOUT_T = "tests/test_chat_bubble_layout_contract.py"
TEXT_T = "tests/test_bubble_text_contract.py"
CS_PROJ = "BubbleTextTests"

PREFAB = Path("/mnt/e/SteamLibrary/steamapps/common/鬼谷八荒/Mod/modFQA/资源修改教程/"
              "ResBuildABProject/Assets/Resources/UI/UIChatAi.prefab")
PATCH = ROOT / "scripts/dev/prefab_patch_chat_bubble_style.py"
CORE = ROOT / "csharp/UI/BubbleTextCore.cs"
WIN = ROOT / "csharp/UI/ChatWindow.cs"
ITEMS = ROOT / "csharp/UI/ChatItemViews.cs"
CSPROJ = ROOT / "csharp/AgentLoopBridge.csproj"
CS_TEST_CSPROJ = ROOT / "csharp/tests/BubbleTextTests/BubbleTextTests.csproj"

#: 气泡组件 / 子 Text 的 fileID（改真资产时按组件块精确定位，不靠"第一处匹配"）
NPC_LE = "880000000000000010"          # NpcBubble 上的 LayoutElement
NPC_TXT = "7934620603520008714"        # NpcBubble/Text 上的 UI.Text
USER_TXT = "8806853516038351287"       # UserBubble/Text 上的 UI.Text
#: 同色同字号的还有 ChatInput/Text = &220201618703605507（它**不该**被本补丁动到）


def _edit_doc(text: str, file_id: str, old: str, new: str) -> str:
    """在 `--- !u!<cls> &<file_id>` 那个文档块内做一次替换（块内必须命中恰好一次）。"""
    head = "--- !u!%s &%s\n"
    import re
    m = re.search(r"(?m)^--- !u!\d+ &%s$" % re.escape(file_id), text)
    assert m, "找不到组件块 &%s" % file_id
    start = m.start()
    nxt = re.search(r"(?m)^--- !u!\d+ &\d+$", text[m.end():])
    end = m.end() + (nxt.start() if nxt else len(text) - m.end())
    block = text[start:end]
    assert block.count(old) == 1, "组件块 &%s 内 `%s` 命中 %d 次" % (file_id, old, block.count(old))
    return text[:start] + block.replace(old, new, 1) + text[end:]


def _set_prefab(file_id: str, old: str, new: str):
    return lambda text: _edit_doc(text, file_id, old, new)


# ══════════════════════════════════════════════════════════════════════
# 侧一：预制件资产 + Python 接线（跑 pytest）
# ══════════════════════════════════════════════════════════════════════

PY_MUTATIONS = [
    # ---- 真资产：把修好的预制件改回"出框"的状态 ----
    (
        "① 真 prefab：NPC 左 padding 退回 12（= 用户截图那次出框）",
        PREFAB,
        _set_prefab("880000000000000002", "    m_Left: 40\n    m_Right: 14",
                    "    m_Left: 12\n    m_Right: 14"), None,
        "test_padding_clears_the_png_body_inset_measured_from_the_art",
    ),
    (
        "② 真 prefab：玩家右 padding 退回 12（镜像那一侧同样出框）",
        PREFAB,
        _set_prefab("7314801662700778477", "    m_Left: 14\n    m_Right: 40",
                    "    m_Left: 14\n    m_Right: 12"), None,
        "test_padding_clears_the_png_body_inset_measured_from_the_art",
    ),
    (
        "③ 真 prefab：NPC 对齐改回 2（与玩家同向 = 又装反）",
        PREFAB,
        _set_prefab(NPC_TXT, "    m_Alignment: 0", "    m_Alignment: 2"), None,
        "test_prefab_reads_back_the_expected_values",
    ),
    (
        "④ 真 prefab：LayoutElement 优先级改回 1（长文本气泡恒 76 高、被 Truncate 静默截掉）",
        PREFAB,
        _set_prefab(NPC_LE, "  m_LayoutPriority: 0", "  m_LayoutPriority: 1"), None,
        "test_patched_prefab_passes_its_own_verify",
    ),
    (
        "⑤ 真 prefab：NPC 子项对齐改成 5（单行泡文字又变垂直居中）",
        PREFAB,
        _set_prefab("880000000000000002", "  m_ChildAlignment: 0", "  m_ChildAlignment: 5"), None,
        "test_prefab_reads_back_the_expected_values",
    ),
    (
        "⑤b 真 prefab：玩家正文被改回右对齐（09-17 实机事故的回归护栏）",
        PREFAB,
        _set_prefab(USER_TXT, "    m_Alignment: 0", "    m_Alignment: 2"), None,
        "test_prefab_reads_back_the_expected_values",
    ),
    (
        "⑤c 真 prefab：玩家子项对齐改成 5（短句浮到气泡中间偏右）",
        PREFAB,
        _set_prefab("7314801662700778477", "  m_ChildAlignment: 0", "  m_ChildAlignment: 5"), None,
        "test_prefab_reads_back_the_expected_values",
    ),
    # ---- 补丁脚本常量（弱一层，但保证"常量与资产是一套"） ----
    (
        "⑥ 补丁常量：玩家侧对齐写成右对齐（实机事故的原样复现）",
        PATCH,
        'USER_ALIGN = "0"',
        'USER_ALIGN = "2"',
        "test_patch_script_exists_and_is_importable",
    ),
    (
        "⑥b 补丁常量：子项对齐写成垂直居中",
        PATCH,
        'NPC_CHILD_ALIGN = "0"',
        'NPC_CHILD_ALIGN = "3"',
        "test_patch_script_exists_and_is_importable",
    ),
    (
        "⑦ 补丁常量：NPC 尾巴侧 padding 写成 12",
        PATCH,
        'NPC_PAD = {"m_Left": 40, "m_Right": 14, "m_Top": 12, "m_Bottom": 12}',
        'NPC_PAD = {"m_Left": 12, "m_Right": 14, "m_Top": 12, "m_Bottom": 12}',
        "test_tail_side_padding_clears_the_art_inset_by_constants",
    ),
    # ---- C# 接线：四个写泡点逐个退回裸写 ----
    (
        "⑧ 接线：流式刷新退回裸写（流式期间空行照样冒）",
        WIN,
        "BubbleTextCore.Tighten(_delta.ToString())",
        "_delta.ToString()",
        "test_streaming_refresh_tightens",
    ),
    (
        "⑨ 接线：终稿覆盖退回裸写（定稿瞬间屏幕跳一下）",
        WIN,
        "BubbleTextCore.Tighten(fullText)",
        "fullText",
        "test_final_overwrite_tightens",
    ),
    (
        "⑩ 接线：SetRowText 退回裸写（OverwriteLastRow 那条路漏掉）",
        WIN,
        "t.text = BubbleTextCore.Tighten(text);",
        't.text = text ?? "";',
        "test_row_overwrite_tightens",
    ),
    (
        "⑪ 接线：ChatBubble.SetText 退回裸写（新建气泡那条路漏掉）",
        ITEMS,
        "Text.text = BubbleTextCore.Tighten(text);",
        'Text.text = text ?? "";',
        "test_new_bubble_tightens",
    ),
    (
        "⑫ 新增一条写泡分支（兜底扫描应当拦住）",
        WIN,
        "        private bool SetRowText(GameObject row, string text)",
        "        private void MutatedNewBubblePath(string s) { _bubbleText.text = s; }\n\n"
        "        private bool SetRowText(GameObject row, string text)",
        "test_no_raw_bubble_write_left_anywhere",
    ),
    # ---- 结构：核没被编进模组 / 混进 Unity / 测试工程拿了副本 ----
    (
        "⑫b C# 兜底常量漂移（尾巴侧下限写回 12 ⇒ 老 AB 上文字又出框）",
        WIN,
        "private const int BUBBLE_TAIL_PAD = 40;",
        "private const int BUBBLE_TAIL_PAD = 12;",
        "test_csharp_runtime_leg_matches_the_prefab_constants",
    ),
    (
        "⑫c C# 兜底没在 Init 里调用（老 AB 不会自动修好）",
        WIN,
        "            ApplyBubbleTextLayoutFloor();    // 同上：气泡对齐/内边距的运行期兜底",
        "            // mutated: 没调用",
        "test_csharp_runtime_leg_is_wired_and_keeps_upper_left",
    ),
    (
        "⑫d C# 兜底纠正方向写反（改成右对齐）",
        WIN,
        "                text.alignment = TextAnchor.UpperLeft;",
        "                text.alignment = TextAnchor.UpperRight;",
        "test_csharp_runtime_leg_is_wired_and_keeps_upper_left",
    ),
    (
        "⑬ 核没登记进 csproj（根目录 .cs 不在 UI\\*.cs 通配内）",
        CSPROJ,
        '    <Compile Include="BubbleTextCore.cs" />',
        '    <!-- mutated: 漏登记 -->',
        "test_core_is_compiled_into_the_mod",
    ),
    (
        "⑭ 核混进 Unity 依赖（测试工程就编不过了）",
        CORE,
        "using System.Text;",
        "using System.Text;\nusing UnityEngine;",
        "test_core_exists_and_stays_unity_free",
    ),
    (
        "⑮ 测试工程改去链接副本而不是同一个文件",
        CS_TEST_CSPROJ,
        r'<Compile Include="..\..\BubbleTextCore.cs" />',
        r'<Compile Include="BubbleTextCore.cs" />',
        "test_csharp_test_project_exists_and_links_the_same_file",
    ),
]

# ══════════════════════════════════════════════════════════════════════
# 侧二：C# 折叠规则判定表（跑 BubbleTextTests）
# ══════════════════════════════════════════════════════════════════════

CS_MUTATIONS = [
    (
        "① 空行不折（用户投诉的那件事原样保留）",
        CORE,
        "            if (a >= b) return;                     // 空行 / 纯空白行：丢",
        "            if (false) return;",
        "__csharp__",
    ),
    (
        "② 单个换行也被折掉（模型分的段并成一行）",
        CORE,
        "            if (sb.Length > 0) sb.Append('\\n');     // 行间只留一个换行",
        "            // mutated: 不再补换行 ⇒ 所有行粘成一行",
        "__csharp__",
    ),
    (
        "③ 不修行首空白（行首空格原样带进气泡）",
        CORE,
        "            while (a < b && IsPad(s[a])) a++;",
        "            while (false) a++;",
        "__csharp__",
    ),
    (
        "④ 不修行尾空白",
        CORE,
        "            while (b > a && IsPad(s[b - 1])) b--;",
        "            while (false) b--;",
        "__csharp__",
    ),
    (
        "⑤ 全角空格不算空白（全角空行留下一个空行）",
        CORE,
        "            return c == ' ' || c == '\\t' || c == '\\u3000' || c == '\\u00A0';",
        "            return c == ' ' || c == '\\t' || c == '\\u00A0';",
        "__csharp__",
    ),
    (
        "⑥ 制表符不算空白",
        CORE,
        "            return c == ' ' || c == '\\t' || c == '\\u3000' || c == '\\u00A0';",
        "            return c == ' ' || c == '\\u3000' || c == '\\u00A0';",
        "__csharp__",
    ),
    (
        "⑦ 不换行空格不算空白",
        CORE,
        "            return c == ' ' || c == '\\t' || c == '\\u3000' || c == '\\u00A0';",
        "            return c == ' ' || c == '\\t' || c == '\\u3000';",
        "__csharp__",
    ),
    (
        "⑧ null 不再兜底（气泡拿 null 去写 Text）",
        CORE,
        '            if (string.IsNullOrEmpty(s)) return "";',
        '            if (string.IsNullOrEmpty(s)) return null;',
        "__csharp__",
    ),
    (
        "⑨ 行内空格被顺手删掉（改断句，且看着像模型自己写的）",
        CORE,
        "            sb.Append(s, a, b - a);",
        '            sb.Append(s.Substring(a, b - a).Replace(" ", ""));',
        "__csharp__",
    ),
    (
        "⑩ 换行符归一失效（\\r\\n / 孤立 \\r 被当成普通字符留在行里）",
        CORE,
        "                while (j < n && s[j] != '\\n' && s[j] != '\\r') j++;",
        "                while (j < n && s[j] != '\\n') j++;",
        "__csharp__",
    ),
]

_BAK = Path(tempfile.gettempdir()) / "mutate_check_bubble_style"


def _run_py(expect: str | None):
    """expect=None → 跑两个契约文件的全部用例（基线/还原校验）；否则只跑指定用例。"""
    if expect is None:
        targets = [LAYOUT_T, TEXT_T]
    elif expect in _read(TEXT_T):
        targets = [f"{TEXT_T}::{expect}"]
    else:
        targets = [f"{LAYOUT_T}::{expect}"]
    cmd = [sys.executable, "-m", "pytest"] + targets + ["-q", "--no-header"]
    p = _mutate_lib.run(cmd, cwd=str(ROOT), capture_output=True, text=True, errors="replace")
    return p.returncode, p.stdout + p.stderr


def _run_cs():
    p = _mutate_lib.run(
        [sys.executable, "scripts/dev/run_csharp_tests.py", "--only", CS_PROJ, "--quiet"],
        cwd=str(ROOT), capture_output=True, text=True, errors="replace",
    )
    return p.returncode, p.stdout + p.stderr


def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8")
    except Exception:
        return ""


def _check_set(label: str, mutations) -> int:
    print(f"\n=== {label} ===")
    files = sorted({m[1] for m in mutations})
    _mutate_lib.guard(files)   # 被中断也要还原（见 _mutate_lib.guard）
    _BAK.mkdir(exist_ok=True)
    missing = [f for f in files if not f.exists()]
    if missing:
        print("★ 缺文件（这台机器上跑不了这一侧）：", [str(m) for m in missing])
        return 0
    for f in files:
        shutil.copy2(f, _BAK / f.name)

    is_cs = any(m[3] == "__csharp__" for m in mutations)
    rc, out = (_run_cs() if is_cs else _run_py(None))
    print("基线：exit=", rc)
    if rc != 0:
        print("★ 基线不是绿的，先修基线：\n", out[-900:])
        return len(mutations) + 1

    bad = 0
    for name, path, old, new, expect in mutations:
        for f in files:                       # 每轮先全量还原，防上一轮污染
            shutil.copy2(_BAK / f.name, f)
        src = path.read_text(encoding="utf-8")
        if callable(old):
            try:
                mutated = old(src)
            except AssertionError as e:
                print(f"✗ {name}: 变异无法施加（{e}）")
                bad += 1
                continue
        else:
            if old not in src:
                print(f"✗ {name}: 找不到待替换文本（测试自身失效，需更新本脚本）")
                bad += 1
                continue
            mutated = src.replace(old, new, 1)
        path.write_text(mutated, encoding="utf-8")

        if expect == "__csharp__":
            rc, out = _run_cs()
            ok = rc != 0
            hit = "判定表变红" if ok else "仍绿"
        else:
            rc, out = _run_py(expect)
            hit = f"命中[{expect}]={expect in out}"
            ok = rc != 0 and expect in out
        print(f"{'✓' if ok else '✗'} {name} → exit={rc} {hit}")
        if not ok:
            bad += 1
            print("   尾部：", [l for l in out.strip().splitlines() if l.strip()][-4:])

    for f in files:                           # 还原
        shutil.copy2(_BAK / f.name, f)
    rc, out = (_run_cs() if is_cs else _run_py(None))
    print("还原后：exit=", rc)
    if rc != 0:
        print(f"★★ 还原失败！从 {_BAK} 手工恢复")
        return len(mutations) + 2
    return bad


def main() -> int:
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    bad = 0
    total = 0
    if which in ("all", "py"):
        total += len(PY_MUTATIONS)
        bad += _check_set("预制件资产 + Python 接线侧", PY_MUTATIONS)
    if which in ("all", "cs"):
        total += len(CS_MUTATIONS)
        bad += _check_set("C# 折叠规则判定表（BubbleTextCore.cs）", CS_MUTATIONS)
    print()
    if bad == 0:
        print(f"全部变异都被捕获（{total}/{total}）")
        return 0
    print(f"有 {bad} 个变异没被捕获（共 {total} 个）—— 测试需要加强")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
