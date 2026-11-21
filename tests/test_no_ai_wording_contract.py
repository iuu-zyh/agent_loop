"""界面文案不许出现「AI」字样（09-18，用户：玩家反映界面里不要出现 AI）。

## 为什么要有这条契约

玩家看到的是**沉浸感**：NPC 面板上那枚按钮、剧情窗里那枚选项、配置面板的标题与行标签、
状态条上的"服务已停止"提示，一旦蹦出 "AI" 就出戏一次。而这类文案散在 **8 个 C# 文件 +
1 个预制件 + 3 个提示词文件 + 安装说明模板**里，改漏一处**没有任何报错**，只是玩家又看见一次。

本文件把"哪些地方算界面文案"钉死，并规定：
  · 界面文案（C# 字符串字面量）**不许出现独立单词 AI**；
  · 例外只有一类：以 `[Tag]` 开头的**日志**文案（仓库既有约定，见 `ModMain.P`）——
    它们是开发者诊断用的，玩家看不到；
  · 预制件里烧死的文案由运行期改写兜住（AB 重打需要 Unity 许可证，本机被挡过），
    所以这里同时钉"运行期改写的目标节点还在"。

## 为什么预制件与提示词也要管

  · `UIConfigAi.prefab` 里烧着窗口标题与四行标签 —— 老 AB 里就是这五个 "AI"，
    光改 C# 不够（运行期改写负责玩家实际看到的，`prefab_patch_ai_wording.py` 负责源头）；
  · `prompts/**` 在配置面板的「提示词」页**直接可读**，那里的 "AI" 同样是界面字样；
  · `安装说明.txt` 由 `pack_release.py` 生成、随包发给玩家，写的是"点哪枚按钮"，
    按钮改名后不同步就是**教错操作**。

⚠ 诚实边界：C# 侧只有 `[Tag]` 前缀这一条判据区分"日志/界面"，靠的是仓库既有写作约定；
   真·运行期截图验收要等游戏内复看（见 docs/reference/csharp-iron-laws.md 2026-09-18-界面去掉AI字样.md）。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CS_DIR = ROOT / "csharp"

#: 界面文案所在（全量扫 C#，不挑文件 —— 挑文件就会漏掉将来新增的那一个）
SKIP_DIRS = ("/obj/", "/bin/")

#: 独立单词 AI：`OpenAI` / `MAIN` / `PAIR` 之类不算（前后都必须是字母边界）
AI_WORD = re.compile(r"(?<![A-Za-z])AI(?![A-Za-z])")

#: 允许出现 AI 的字面量前缀：仓库的日志约定（`ModMain.P("[Tag] …")`）。
#: 玩家看不到这些；它们进的是 `logs/agent_loop.log` 与 MelonLoader 控制台。
LOG_PREFIX = "["


def _css() -> list:
    return [p for p in sorted(CS_DIR.rglob("*.cs"))
            if not any(s in str(p) for s in SKIP_DIRS)]


def _literals(path: Path):
    """逐行取字符串字面量（剥行内注释）。返回 [(行号, 字面量)]。"""
    out = []
    pat = re.compile(r'"((?:[^"\\\n]|\\.)*)"')
    for i, raw in enumerate(path.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
        line = re.sub(r"//.*$", "", raw)
        for m in pat.finditer(line):
            out.append((i, m.group(1)))
    return out


def test_no_ai_word_in_ui_literals():
    """★ 主判据：C# 里除日志外的字符串字面量，一律不许出现独立单词 AI。"""
    bad = []
    for p in _css():
        for line, lit in _literals(p):
            if not AI_WORD.search(lit):
                continue
            if lit.lstrip().startswith(LOG_PREFIX):
                continue          # 日志（[Tag] 前缀）——开发者诊断用，不算界面文案
            bad.append(f"{p.relative_to(ROOT)}:{line}  {lit[:80]!r}")
    assert not bad, "界面文案里出现了 AI 字样：\n  " + "\n  ".join(bad)


def test_npc_and_drama_buttons_say_just_duihua():
    """两枚按钮（NPC 面板 / 剧情窗）都叫「对话」——它们是玩家最常看到的那两处。"""
    npc = (CS_DIR / "UI" / "NpcPanelButton.cs").read_text(encoding="utf-8")
    assert 'CreateVisualRow(panel.transform, ButtonName, "对话", 0, grid);' in npc, \
        "NPC 面板那枚按钮的文案不是「对话」"
    drama = (CS_DIR / "UI" / "DramaAiOption.cs").read_text(encoding="utf-8")
    assert 'private const string Label = "对话";' in drama, "剧情窗那枚按钮的文案不是「对话」"


def test_ab_panel_relabels_title_and_all_four_rows():
    """预制件里烧死的五处（窗口标题 + 四行标签）必须全部有运行期改写。

    为什么"全部"：本轮之前只改了两行，另两行在面板上照样显示 "AI 单次超时秒·压缩" /
    "AI 失败重试次数" —— 漏一行就是漏一次出戏。改 AB 需要 Unity 许可证（本机被挡过），
    所以这条是玩家实际看到的那一层。
    """
    src = (CS_DIR / "UI" / "AbConfigPanel.cs").read_text(encoding="utf-8")
    for row in ("LlmTimeoutRow", "LlmTimeoutNsRow", "LlmBudgetRow", "LlmRetriesRow"):
        assert f'RelabelRow(root, Llm + "{row}", ' in src, f"{row} 没有运行期改文案"
    assert '"BG/Title/TitleLabel"' in src, "窗口标题没有运行期改文案（预制件里是「AI 设置」）"
    assert '"智能体设置"' in src, "窗口标题的新文案不是「智能体设置」"


def test_code_built_panel_matches_ab_wording():
    """代码版面板（AB 缺失时的兜底构建路径）与 AB 侧改写后的文案**同字**。

    不同字的后果不是报错，是"换台机器/老 AB 时看到另一套措辞"（历史上漂移过一次）。
    """
    src = (CS_DIR / "UI" / "ConfigUiBuilder.cs").read_text(encoding="utf-8")
    for text in ("智能体设置", "首字超时秒（即时生效）", "压缩超时秒（即时生效）",
                 "硬顶秒·0=不设限", "失败重试次数（即时生效）"):
        assert f'"{text}"' in src, f"代码版面板缺少文案：{text}"


def test_prefab_source_has_no_ai_wording():
    """预制件源头（打包工程，存在才查）：五个烧死的 "AI" 必须已经洗掉。

    查不到文件就跳过 —— 换台机器没有那份 Unity 工程是正常的，不该把测试判红。
    """
    prefab = Path("/mnt/e/SteamLibrary/steamapps/common/鬼谷八荒/Mod/modFQA/资源修改教程"
                  "/ResBuildABProject/Assets/Resources/UI/UIConfigAi.prefab")
    if not prefab.is_file():
        print("· 跳过：本机没有打包工程预制件")
        return
    hits = [m.group(0) for m in re.finditer(r'm_Text: "[^"]*"', prefab.read_text(encoding="utf-8"))
            if AI_WORD.search(m.group(0))]
    assert not hits, "预制件里还烧着 AI 文案：\n  " + "\n  ".join(hits)
    patch = ROOT / "scripts/dev/prefab_patch_ai_wording.py"
    assert patch.is_file(), "洗预制件文案的脚本不见了（下次重打 AB 会把 AI 又带回来）"


def test_prompt_files_players_can_read_have_no_ai():
    """提示词是玩家在面板里**看得见**的文件（sections / personas / compaction）。"""
    bad = []
    for p in sorted((ROOT / "prompts").rglob("*")):
        if not p.is_file():
            continue
        for i, line in enumerate(p.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
            if AI_WORD.search(line):
                bad.append(f"{p.relative_to(ROOT)}:{i}  {line.strip()[:80]}")
    assert not bad, "玩家可读的提示词里还有 AI 字样：\n  " + "\n  ".join(bad)

    # 内置兜底那一份（`system_prompt.py` 里硬编码的默认输出底线）必须与文件同步
    sp = (ROOT / "agent" / "system_prompt.py").read_text(encoding="utf-8")
    suffix = (ROOT / "prompts" / "personas" / "_suffix.txt").read_text(encoding="utf-8").strip()
    assert suffix in sp, "personas/_suffix.txt 与 system_prompt.py 里的内置默认不是同一句（会漂移）"


def test_install_doc_template_teaches_the_new_button_name():
    """安装说明（随包发给玩家）教的按钮名必须与界面上的一致。"""
    src = (ROOT / "scripts" / "pack_release.py").read_text(encoding="utf-8")
    i = src.index("INSTALL_TXT = ")
    doc = src[i: src.index('"""', src.index('"""', i) + 3)]
    bad = [ln for ln in doc.splitlines() if AI_WORD.search(ln)]
    assert not bad, "安装说明里还有 AI 字样：\n  " + "\n  ".join(bad)
    assert "「对话」" in doc, "安装说明没把按钮叫「对话」"
