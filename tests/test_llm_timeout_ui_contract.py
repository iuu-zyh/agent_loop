"""LLM 超时语义的**面板侧**契约（09-18）：C# 面板的文案与缺省值必须跟 Python 后端一致。

为什么单独一条：这一层的漂移**不报任何错**，只表现为"面板上写的和实际行为不一样"——
而那正是 09-17 那次误解的来源（面板与文档都说"量的是相邻两块数据的间隔"，代码却拿它当
整趟尝试的挂钟，于是"吐字很顺、总时长超了"的长回复被砍断，用户完全无法从界面上看出来）。

三处必须对齐（任何一处单改都会让玩家按错的模型理解这一项）：
  ① 预制件里**烧死**的行标签（"AI 单次超时秒·流式"）⇒ `AbConfigPanel` 运行期改文案；
  ② 面板回填的缺省值（llm 键不在 config.json 里时显示什么）⇒ 必须等于
     `config_loader.DEFAULTS["llm"]`（否则面板显示一个后端根本不用的值）；
  ③ 悬停说明 ⇒ 必须说清"首字 / 停顿 / 默认不设整趟上限"，不许再退回旧措辞。
"""
from __future__ import annotations

import re
from pathlib import Path

from agent_loop.config.config_loader import DEFAULT_CONFIG

ROOT = Path(__file__).resolve().parent.parent
PRESENTER = ROOT / "csharp/UI/ConfigPresenter.cs"
AB_PANEL = ROOT / "csharp/UI/AbConfigPanel.cs"
BUILDER = ROOT / "csharp/UI/ConfigUiBuilder.cs"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def test_ab_panel_relabels_the_two_reworked_rows():
    """预制件标签是烧死的（重打 AB 才能改），所以两行必须运行期改文案。

    "AI 单次超时秒·流式" 会被读成"单次请求的总时长上限" —— 正是被否掉的语义；
    硬顶那行的旧文案（"AI 总等待上限秒"）也没说默认是关的。
    """
    src = _read(AB_PANEL)
    assert 'RelabelRow(root, Llm + "LlmTimeoutRow", ' in src, "首字超时那一行没有运行期改文案"
    assert 'RelabelRow(root, Llm + "LlmBudgetRow", ' in src, "硬顶那一行没有运行期改文案"
    assert "首字" in src, "标签文案里没有「首字」——这句话必须自己说清量的是什么"
    assert "0=不设限" in src or "0＝不设限" in src, "硬顶那行必须写明默认 0 = 不设限"
    # 老 AB 没有这些节点时 RelabelRow 必须静默跳过（否则整面板崩）
    i = src.index("private static void RelabelRow(")
    body = src[i: src.index("\n        }", i)]
    assert "if (lbl != null)" in body, "RelabelRow 没有空值兜底"


def test_code_built_panel_labels_match_ab_relabel():
    """代码版面板（老 AB 的兜底构建路径）标签要与 AB 侧改后的文案同义。"""
    src = _read(BUILDER)
    # 09-18 第二轮：界面文案去掉 "AI" 前缀（用户要求），标签与 AB 侧 RelabelRow 严格同字
    assert '"首字超时秒（即时生效）"' in src, "代码版面板的首字超时标签没跟着改"
    assert '"硬顶秒·0=不设限"' in src, "代码版面板的硬顶标签没跟着改"
    assert '"AI 首字超时秒' not in src and '"AI 硬顶秒' not in src, "代码版面板又出现了 AI 字样"


def test_panel_fallbacks_equal_python_defaults():
    """面板回填的缺省值 == Python 缺省值（跨语言漂移是静默的：面板显示一个后端不用的值）。

    09-20 重写：原版用正则去匹配源码里的 `?? "120"` **文本**，于是它只证明"这段字写在那里"，
    证明不了"这段字**生效**"。而当时恰恰不生效——`ToStringInvariant` 对 null 返回 ""，
    `"" ?? "120"` 得 "" ⇒ 兜底是死代码，键一删面板就空白。
    现在分两层断言：① 兜底值本身与后端一致；② **兜底真的会被触发**（null 必须穿透到 `??`）。
    """
    pres = _read(PRESENTER)
    llm = DEFAULT_CONFIG["llm"]
    pairs = [
        # (控件名, config 块, 键名) —— 兜底值必须等于 config_loader 对应块里的默认值
        ("LlmTimeoutInput", "llm", "timeout"),
        ("LlmTimeoutNsInput", "llm", "timeout_nonstream"),
        ("LlmBudgetInput", "llm", "total_budget"),
        ("LlmRetriesInput", "llm", "retries"),
        # 09-20 补：这四处原先连 `??` 都没写，删键即空白
        ("TimeoutInput", "network", "request_timeout"),
        ("MinIntervalInput", "initiative", "min_interval"),
        ("RetainRatioInput", "compaction", "retain_ratio"),
        ("ThresholdRatioInput", "compaction", "threshold_ratio"),
    ]
    for ctrl, block, key in pairs:
        m = re.search(r'SetText\(_refs\.' + ctrl + r', ' +
                      r'(?:llm|init|net|comp)\["[^"]+"\]' +
                      r'\?\.ToString\(\) \?\? "([0-9.]+)"\)', pres)
        assert m, f"{ctrl} 的回填兜底没找到（写法变了？必须是 `?.ToString() ?? \"默认值\"`）"
        expected = DEFAULT_CONFIG[block][key]
        assert float(m.group(1)) == float(expected), \
            f"{ctrl} 面板缺省 {m.group(1)} != config_loader 的 {expected}"

    # ② 语义层：ToStringInvariant 对 null **必须返回 null**，否则上方的 `??` 全是死代码。
    #    这条才是 09-20 事故的真判据——只钉值不钉这个，同样的 bug 可以原样再犯一次。
    fn = re.search(r'private static string ToStringInvariant\(JToken token\)\s*\{(.*?)\n        \}',
                   pres, re.S)
    assert fn, "ToStringInvariant 没找到（改名了？）"
    body = fn.group(1)
    assert re.search(r'if \(token == null[^)]*\) return null;', body), \
        "ToStringInvariant 对 null 又返回了非 null（空串）—— '?? 默认值' 会重新变成死代码"
    assert not re.search(r'if \(token == null\) return "";', body), \
        "ToStringInvariant 退回返回空串：面板在键缺失时会显示空白而不是默认值"

    # ③ 四个 llm 键不许再走 ToStringInvariant 那条老路（它曾让兜底失效）
    for ctrl, key in [("LlmTimeoutInput", "timeout"), ("LlmTimeoutNsInput", "timeout_nonstream"),
                      ("LlmBudgetInput", "total_budget")]:
        assert not re.search(r'SetText\(_refs\.' + ctrl + r',\s*ToStringInvariant\(llm\["' + key + r'"\]\)',
                             pres), \
            f"{ctrl} 又用 ToStringInvariant(...) 包了兜底——它的 null 语义与 `??` 不兼容"


def test_panel_tooltips_do_not_regress_to_the_old_wording():
    """悬停说明不许退回旧措辞（"相邻两块间隔"/"整趟挂钟"/"默认 100"）。"""
    pres = _read(PRESENTER)
    i = pres.index("RegisterRow(_refs?.LlmTimeoutInput,")
    tip_timeout = pres[i: pres.index("RegisterRow(_refs?.LlmTimeoutNsInput,", i)]
    assert "首字" in tip_timeout, "首字超时的说明里没有「首字」"
    # 必须**正面**写清它不是整趟上限（旧文案的错就在这里：说成"整趟"，或说成"块间隔"）
    assert re.search(r"不是\**整个", tip_timeout), "首字超时的说明没有写清「不是整趟上限」"
    assert "相邻两次收到数据的间隔" not in tip_timeout, "又退回旧的「块间隔」说法（09-17 误解源）"
    assert "默认 45" not in tip_timeout, "旧默认值（45）还留在说明里"
    assert "idle_timeout" in tip_timeout, "说明里没提中途停顿是另一个键（玩家会以为一个值管两处）"

    j = pres.index("RegisterRow(_refs?.LlmBudgetInput,")
    tip_budget = pres[j: pres.index("RegisterRow(_refs?.LlmRetriesInput,", j)]
    assert "默认 0" in tip_budget and "不设限" in tip_budget, "硬顶那行没写清默认是关的"
    assert "默认 100" not in tip_budget, "硬顶说明里的旧默认值（100）还在"
    print("✓ 面板侧：两行标签改名、缺省值与后端一致、悬停说明不再退回旧措辞")


def test_budget_row_submits_an_explicit_zero():
    """硬顶那行填 0 **必须提交**（0 = 不设限，是它的默认值；「留空」才是"不改这一项"）。

    其他三个 llm 键是"空/0 = 不提交"，硬顶刻意相反 —— 否则玩家无法在面板里关掉一个手编过的
    150（填 0 等于什么都不做，"改了却没生效"）。这是唯一一处「0 有主动语义」的提交规则。
    """
    pres = _read(PRESENTER)
    i = pres.index("if (_refs.LlmBudgetInput != null")
    seg = pres[i: pres.index("if (_refs.LlmRetriesInput != null", i)]
    assert "submitLlmBudget = true;" in seg, "硬顶那行填 0 不再提交（0 会被当成「留空」）"
    assert "submitLlmBudget = llmBudget > 0" not in seg, "硬顶又退回「0 = 不提交」的旧规则"
    print("✓ 硬顶那行：0 会被提交（= 不设限），留空才是不改")


if __name__ == "__main__":
    test_ab_panel_relabels_the_two_reworked_rows()
    test_code_built_panel_labels_match_ab_relabel()
    test_panel_fallbacks_equal_python_defaults()
    test_panel_tooltips_do_not_regress_to_the_old_wording()
    test_budget_row_submits_an_explicit_zero()
    print("\nLLM 超时面板契约全部通过")
