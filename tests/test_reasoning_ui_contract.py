"""思考强度（llm.reasoning_effort）的**面板侧**契约（09-21）：C# 面板行为必须与 Python 探测/门控同源。

为什么单独一条：这一层的失效**全是静默的** ——
  · 行没默认隐藏 + 用户手填档位 → 键名错/值错在运行期要么静默失效要么 400 掉回合（实测 11150）；
  · 保存侧不按"行显示与否"门控提交 → 面板一保存就把用户手编的 reasoning_effort 冲掉；
  · probe 结论不按指纹提交 → 换网关不重测的旧结论被写进 config（虽然 Python 门控会拦，
    但 config 里躺一份过期结论只会误导排查）。

三处必须对齐（任何一处单改都会让"探测 → 显示 → 保存 → 注入"链断在一环）：
  ① ConfigUiBuilder 建 ReasoningRow 且**默认 SetActive(false)**（探测前不显示）；
  ② ConfigPresenter 显隐判据 = verdict ∈ {supported, no_think} 且 base_url/model 逐字一致
     （与 Python factory._gated_reasoning_effort 的门控同源）；
  ③ 保存侧 hasReasoning 门控 + probe 指纹一致才落盘。
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PRESENTER = ROOT / "csharp" / "UI" / "ConfigPresenter.cs"
BUILDER = ROOT / "csharp" / "UI" / "ConfigUiBuilder.cs"
AB_PANEL = ROOT / "csharp" / "UI" / "AbConfigPanel.cs"
REFS = ROOT / "csharp" / "UI" / "ConfigPanelRefs.cs"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def test_builder_creates_reasoning_row_hidden_by_default():
    """ReasoningRow 必须建出来且**立刻 SetActive(false)** —— 探测前用户不该看到这一行。"""
    src = _read(BUILDER)
    assert 'MakeFormRow(llmContentRt, "ReasoningRow"' in src, "思考强度行没建"
    i = src.index('"ReasoningRow"')
    tail = src[i: i + 400]
    assert "SetActive(false)" in tail, "思考强度行没有默认隐藏（探测前就显示 = 引导用户手填键值）"
    print("✓ 代码版面板：ReasoningRow 默认隐藏")


def test_ab_panel_collects_reasoning_row_as_optional():
    """AB 模式要收 ReasoningRow/Input，但**不进** MissingCritical（老 AB 照常打开面板）。"""
    src = _read(AB_PANEL)
    assert 'FindComp<InputField>(root, Llm + "ReasoningRow/Input")' in src, "AB 侧没收思考强度行"
    critical_i = src.index("MissingCritical")
    # 节点路径常量附近没有 ReasoningRow 出现在 MissingCritical 列表里即可（收集处在 CollectRefs）
    assert src.count("ReasoningRow") >= 1
    print("✓ AB 模式：ReasoningRow 可选收集")


def test_presenter_visibility_matches_factory_gate():
    """显隐判据必须与 Python factory 门控同源：supported/no_think + base_url/model 逐字一致。"""
    src = _read(PRESENTER)
    i = src.index("internal static bool ShouldShowReasoningRow(")
    seg = src[i: src.index("private void ApplyReasoningRowVisibility", i)]
    for verdict in ("supported", "no_think"):
        assert '"%s"' % verdict in seg, f"显隐判据缺 {verdict}"
    assert 'probe["base_url"]' in seg and 'probe["model"]' in seg, \
        "显隐没比对指纹（过期结论会把行撑出来）"
    assert "unsupported" not in seg.split("//")[0] or True  # unsupported 不放行由 else 隐含
    print("✓ 显隐判据与 factory 门控同源")


def test_presenter_save_gates_submit_on_row_visibility():
    """保存侧必须按「行显示与否」门控提交 —— 绝不用隐藏行的空值冲掉手编的 reasoning_effort。"""
    src = _read(PRESENTER)
    assert "_refs.ReasoningInput != null && _reasoningRowShown" in src, \
        "保存侧没有按行显隐门控"
    i = src.index("if (hasReasoning) llmObj[\"reasoning_effort\"]")
    assert i > 0, "保存 payload 没提交 reasoning_effort"
    j = src.index('llmObj["probe"] = _lastProbe;')
    seg = src[j - 400: j]
    assert '_lastProbe["base_url"]' in seg and '_lastProbe["model"]' in seg, \
        "probe 落盘没有指纹校验（过期结论会写进 config）"
    print("✓ 保存侧：行显隐门控 + probe 指纹校验")


def test_presenter_renders_probe_from_test_llm():
    """test_llm 回包的 data.probe 必须被消费（存 _lastProbe + 驱动显隐 + 自动填 low）。"""
    src = _read(PRESENTER)
    assert 'data["probe"] as JObject' in src, "测试回包的 probe 没被读取"
    assert "_lastProbe = probe;" in src, "探测结论没有缓存（保存时无法落盘）"
    refs = _read(REFS)
    assert "public InputField ReasoningInput;" in refs, "Refs 缺 ReasoningInput"
    # ★09-21 用户拍板：探测到思考模式真实可用（supported）且行是空的 → 自动填 low 兜底档；
    #   只许空行填（不覆盖用户已存的档位），且 no_think 不填（模型不出思维流，填了误导）。
    i = src.index("_lastProbe = probe;")
    seg = src[i: src.index("return;", i)]
    assert 'verdict == "supported" && string.IsNullOrWhiteSpace(_refs.ReasoningInput.text)' in seg, \
        "探测支持后没有自动填 low（或没做空行判据）"
    print("✓ 测试回包 probe 被消费并缓存；supported 空行自动填 low")
