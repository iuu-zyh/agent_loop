"""好感度系统的**提示词与契约**四件套（11-12，用户报"模型不怎么主动调好感"）。

## 用户说的三件事

1. 「实际模型不怎么去主动调用，你可以提醒一下……在 L1 里面不是会发『说话不算工具调用』
   之类的，那里可以追加一句，提醒模型去使用好感度系统」→ `ACTION_HINT`；
2. 「好感度系统对象可以是任意 npc」→ `social_relation.target` 不再写"须与你同处一地"；
3. 「需要提供给模型原生的一些好感度标准，比如仇人之类的划分……否则模型不知道
   好感度调整多少合适」→ `value` 的档位刻度 + `_intim_tier` 逐档对齐。

## 外加一条：这份政策文件曾经**整份没进模型**

`tool_usage.txt` 从 10-31 起就不在 `_SECTION_MAP` 里（被富文本那次提交整行换掉）。
本文件不重复守那个（`test_tool_usage_contract.py::test_usage_file_actually_reaches_the_model`
已守），这里只守"好感政策确实点到了模型"。
"""
from __future__ import annotations

import importlib
import re
import sys
from pathlib import Path
import _cs_tools  # 工具层多文件聚合（见 tests/_cs_tools.py）

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

USAGE = ROOT / "prompts" / "sections" / "tool_usage.txt"

#: 档位名（正向）—— **schema 与 L1 段必须用同一套词**。
#: 为什么这件事值得一条测试：两套话模型接不上 ——
#: 它看到「日渐亲近」，却不知道"再加 30 会变成什么"，调工具时就没有目标感。
TIERS_POSITIVE = ["陌路", "泛泛之交", "亲近", "知己", "生死之交"]
TIERS_NEGATIVE = ["嫌隙", "结怨", "深仇", "不共戴天"]


def _schemas():
    return importlib.import_module(f"{ROOT.name}.tools.schemas")


def _value_desc() -> str:
    return _schemas().SOCIAL_RELATION["function"]["parameters"]["properties"]["value"]["description"]


# ============================================================ ① 主动调用的提醒

def test_action_hint_tells_the_model_intimacy_needs_a_tool_call():
    """L1「行动口径」段必须点明：**好感不会因为说了话就变**。

    为什么必须在这段而不是只写在 tool_usage 里（见 `ACTION_HINT` 上方长注释）：
    本段是**每游戏日重发一次**的（`format_l1_context` 末尾拼日期），而 tool_usage 是稳区、
    整个会话只随 system 发一次。二十轮历史之后，稳区那条早被稀释 —— 而"说情话会让人记住"
    正是最顽固的错觉，且**它不落账时没有任何地方会报错**。
    """
    from agent_loop.agent.system_prompt import ACTION_HINT

    assert "好感" in ACTION_HINT, "L1 行动口径没提好感 —— 模型只会记得'送礼要调工具'"
    # 要覆盖"情绪类事件"，不能只列动作类（送/要/换）。模型不会觉得"这番话说得极重"也该落账。
    for k in ("交心", "羞辱", "救命", "背叛"):
        assert k in ACTION_HINT, f"没点到「{k}」这类情绪事件 —— 模型只把它当台词"
    assert "调工具" in ACTION_HINT


def test_action_hint_still_keeps_the_old_policy():
    """原有那条（送/要/换/买卖…）是探针调出来的，**不许被新句子挤掉**。

    它的来历写在 `ACTION_HINT` 上方：开思考时把"该调"从 20/42 拉到 25/42、关思考 40/42。
    加好感那半句是**追加**，不是替换。
    """
    from agent_loop.agent.system_prompt import ACTION_HINT
    for k in ("送", "要", "换", "买卖", "教功法", "邀约", "碰面"):
        assert k in ACTION_HINT, f"原有动作枚举「{k}」被挤掉了"


# ============================================================ ② 对象可以是任意 NPC

def test_target_no_longer_claims_same_grid_only():
    """`target` 不许再写「须与你同处一地」—— 那**与代码不符**，把远程好感也挡掉了。

    实测（stub 与 C# 两条路都验过）：`add_intim` / `reduce_intim` 分支里**没有任何 SameGrid 闸**，
    `ResolveCounterparty` 解析到谁就给谁记账。schema 那句"须与你同处一地"是照抄
    movement/world_ai_action 的措辞 —— 但好感是**人事**，不见面也会变（听说你做了什么）。
    """
    desc = _schemas().SOCIAL_RELATION["function"]["parameters"]["properties"]["target"]["description"]
    assert "须与你同处一地" not in desc, (
        "target 还写着「须与你同处一地」—— 而代码没有这个约束，"
        "模型会因此不敢对异地的人调好感（实测 stub/C# 两条路都放行）")
    assert "任意" in desc, "没点明对象可以是任意 NPC（原先的措辞读起来像'只能在场的'）"


def test_npc_to_npc_intimacy_has_no_same_grid_gate():
    """C# 的 add/reduce_intim 分支里**不许**出现 SameGrid 闸（与 schema 承诺保持一致）。

    这条守的是"两处说法不许再分家"：哪天真给它加了闸，schema 那句就变成谎话，
    而模型会照 schema 去调、然后拿到一个它无法理解为什么失败的错误。
    """
    src = _cs_tools.tool_src()
    i = src.index('case "add_intim"')
    seg = src[i:i + 1400]
    assert "SameGrid" not in seg, (
        "add_intim 分支里出现 SameGrid —— 若真要加这个约束，schema 的 target 描述必须同步改")


# ============================================================ ③ 好感度标准（原生档位）

def test_value_description_carries_a_native_scale():
    """`value` 必须同时给出**两样东西**，否则模型仍然不知道填多少：

      · **刻度**：多少分算哪一档（这样它知道"目标是谁"）；
      · **一次事件给多少**：具体点数（这样它知道"这一下给多少"）。
    """
    d = _value_desc()
    for t in TIERS_POSITIVE:
        assert t in d, f"正向档位「{t}」不在 value 描述里 —— 模型没有可对齐的刻度"
    for t in TIERS_NEGATIVE:
        assert t in d, f"负向档位「{t}」不在 value 描述里 —— 用户点名要「仇人」这类划分"
    # 具体点数：必须出现"一次事件给多少"这类量级，否则模型只会瞎填
    assert re.search(r"寒暄\s*5~10", d) or "寒暄 5~10" in d, f"没给日常量级：{d}"
    assert "120" in d and "15" in d, "没给量级区间"


def test_schema_scale_and_l1_tier_use_the_same_vocabulary():
    """**两处必须同一套档位名** —— 这是本组最有价值的一条。

    模型在两处见到好感：
      · 填参那一刻看 `value` 的 schema（刻度 + 量级）；
      · 每回合读 L1 的「玩家」/「同席」段（`_intim_tier` 给出的当前档位）。
    两边用不同的词，模型就无法把"我现在在哪一档"和"我要给多少"接起来
    （原先正是两套：L1 是"情深意笃/日渐亲近"，schema 是"半心30/一星60"）。
    """
    from agent_loop.agent.system_prompt import SystemPrompt

    d = _value_desc()
    # 逐档把 L1 的输出与 schema 的档位名对齐
    probes = [(200, "生死之交"), (150, "知己"), (87, "亲近"),
              (30, "泛泛之交"), (5, "陌路"),
              (-20, "嫌隙"), (-80, "结怨"), (-140, "深仇"), (-250, "不共戴天")]
    for intim, tier in probes:
        got = SystemPrompt._intim_tier(intim)
        assert tier in got, f"好感{intim} 的 L1 措辞「{got}」里没有档位名「{tier}」"
        assert tier in d, (
            f"L1 用的档位名「{tier}」在 schema 的 value 描述里没有 —— 两处说的不是一套话")


def test_negative_tiers_are_distinct():
    """负向必须**分档**，不能全塌成一句「心存芥蒂」。

    用户点名要"仇人之类的划分"：模型若只知道"负的 = 不好"，就分不出
    "有点不痛快"（嫌隙，还能挽回）与"杀了你师父"（不共戴天，别想靠送礼抹平）——
    而这两件事该给的数值差一个量级。
    """
    from agent_loop.agent.system_prompt import SystemPrompt
    outs = [SystemPrompt._intim_tier(v) for v in (-10, -80, -140, -250)]
    assert len(set(outs)) == 4, f"负向塌成 {len(set(outs))} 档了：{outs}"


def test_tier_never_leaks_raw_number():
    """档位措辞里**不许出现裸数字** —— 模型会把面板数字当台词念出来。

    这是 `_intim_tier` 存在的原始理由（注释里写着"避免'好感已达200'太像面板"），
    改档位时必须继续成立。
    """
    from agent_loop.agent.system_prompt import SystemPrompt
    for v in (0, 30, 87, 150, 200, -20, -80, -140, -250):
        got = SystemPrompt._intim_tier(v)
        assert not re.search(r"\d", got), f"好感{v} 的措辞里带了数字：{got!r}"


# ============================================================ ④ stub 与 C# 的量级必须一致

def test_stub_intimacy_scale_matches_csharp():
    """离线 stub 的 clamp / 缺省值必须与 C# 的 `IntimStepMax` / `IntimStepDefault` 同值。

    11-12 复核时发现的真问题：stub 还是 `min(10, value)` / 缺省 5 —— 那是 10-12
    **放宽刻度之前**的老数字。症状极隐蔽：单测全绿、看起来"能加好感"，
    但**量级差一个数量级**（stub 最多动 10，真机动 120），
    于是所有"给重礼该涨多少"的离线验证都是错的。
    """
    from agent_loop.transport.bridge import StubGameBridge

    src = _cs_tools.tool_src()
    cs_max = int(re.search(r"IntimStepMax\s*=\s*(\d+)", src).group(1))
    cs_def = int(re.search(r"IntimStepDefault\s*=\s*(\d+)", src).group(1))

    b = StubGameBridge()
    b.seed_unit("张三", {"realm": "元婴",
                         "player": {"name": "韩立", "relation": "道友", "intim": 0, "same_grid": True}})
    big = b.call_tool("social_relation", {"op": "add_intim", "initiator": "张三", "value": 999},
                      npc_id="张三")
    assert big["data"]["requested"] == cs_max, (
        f"stub 上限 {big['data']['requested']} ≠ C# 的 {cs_max}")
    dflt = b.call_tool("social_relation", {"op": "add_intim", "initiator": "张三"},
                       npc_id="张三")
    assert dflt["data"]["requested"] == cs_def, (
        f"stub 缺省 {dflt['data']['requested']} ≠ C# 的 {cs_def}")


def test_reduce_intim_can_go_negative():
    """负向**不封底** —— 仇怨是游戏内既有的负值域（与 C# 同口径）。

    原先 stub 写的是 `max(0, intim - delta)`，把负值截在 0：
    于是"结下深仇"在离线路径里**永远到不了**，负档位那套标准也就无从验证。
    """
    from agent_loop.transport.bridge import StubGameBridge
    b = StubGameBridge()
    b.seed_unit("张三", {"realm": "元婴",
                         "player": {"name": "韩立", "relation": "道友", "intim": 50, "same_grid": True}})
    r = b.call_tool("social_relation", {"op": "reduce_intim", "initiator": "张三", "value": 120},
                    npc_id="张三")
    assert r["data"]["current_intim"] < 0, f"减好感被截在 0 了：{r['data']}"


# ============================================================ ⑤ 衰减：说了要算，回读要真

def test_value_says_it_is_a_request_not_the_result():
    """`value` 必须说清"这是**请求值**，不是实得"，并把分档打折讲出来。

    用户 11-13 明确："意思就是现在比如传值是减去 120，但是最终回读显示的是掉 30，
    这也没问题，总之告诉模型存在衰减机制"。
    —— 衰减本身**不改**（那是引擎 `RoleClose` 表的规则，也是游戏本来的手感）；
    要改的只是**别让模型以为工具坏了**：它传 120、看到掉 30，第一反应会是重试或道歉。
    """
    d = _value_desc()
    assert "请求值" in d, "没说 value 是请求值 —— 模型会把它当实得，看到掉 30 就以为工具出错"
    assert "不是实得" in d or "不是实得" in d.replace(" ", ""), "没点明「不是实得」"
    # 分档打折必须给出（否则模型无从预期"传 120 大约会掉多少"）
    assert "60~120" in d or "分档" in d, f"没给衰减分档：{d}"
    # 正负同构：负向也衰减 —— 只写"越亲近越难加"会让模型以为负向不受影响
    assert "同构" in d or "负向" in d, "没说负向同样打折（模型会以为一记羞辱就能把道侣变死仇）"


def test_result_reports_the_read_back_value():
    """工具返回必须带**回读后的真实好感**，不只是「动了多少」。

    用户原话："工具返回结果注意返回回读之后的真实好感度就行"。
    只给 Δ 的话模型知道这一步动了多少，却不知道**现在总共多少** ——
    而"还要不要继续加 / 这句台词该多热络"恰恰取决于当前落在哪一档。
    """
    from agent_loop.tools import text_render as tr

    # add/reduce_intim：`current_intim` 就是回读值
    out = tr.render("social_relation", {"op": "reduce_intim"},
                    {"success": True, "data": {
                        "op": "reduce_intim", "target": "云含", "requested": 120,
                        "actual_delta": -30, "intim_before": 150, "current_intim": 120}})
    assert "120" in out, f"没报回读后的当前好感：{out!r}"
    assert "150→120" in out, f"没给区间（模型判不出落档）：{out!r}"
    # 衰减解释必须在（这是"别当成失败"的唯一提示）
    assert "衰减" in out or "换算" in out or "系数" in out, f"没解释为什么 120 只掉 30：{out!r}"

    # 赠礼这类"副作用"路径：`intim` = Δ、`intim_after` = 回读真值
    out2 = tr.render("economy_item", {"op": "give", "initiator": "云含"},
                     {"success": True, "data": {
                         "op": "give_item", "target": "云含", "via": "direct", "accepted": True,
                         "items": [{"item": "青须藤", "requested": 1, "actual": 1}],
                         "intim": 8, "intim_after": 68}})
    assert out2 and "68" in out2, f"赠礼路径没报回读后的真实好感：{out2!r}"
    assert "8" in out2, f"赠礼路径没报这次动了多少：{out2!r}"


def test_missing_read_back_is_omitted_not_faked():
    """回读不到时**不写那个数字** —— 绝不编一个（宁缺不假，与 `PutIntim` 同口径）。"""
    from agent_loop.tools import text_render as tr
    out = tr.render("economy_item", {"op": "give", "initiator": "云含"},
                    {"success": True, "data": {
                        "op": "give_item", "target": "云含", "via": "direct", "accepted": True,
                        "items": [{"item": "青须藤", "requested": 1, "actual": 1}],
                        "intim": 8}})          # 没有 intim_after
    assert out and "现为" not in out, f"回读缺失却编了一个「现为」：{out!r}"


def test_csharp_writes_the_read_back_key():
    """C# 必须真的写 `{key}_after`，否则上面那条渲染永远是空的。

    跨语言键必须成对：Python 读 `intim_after`，C# 就得写 `intim_after`。
    错一个字母完全静默（帧照到、功能失效）。
    """
    src = _cs_tools.tool_src()
    assert " void PutIntimWithAfter(" in src, "缺「Δ + 回读真值」的写入 helper"
    assert 'o[key + "_after"] = after;' in src, "没写 `{key}_after` 这个键（Python 会永远读不到）"
    assert "PutIntimWithAfter(okWindow" in src, "纯灵石赠礼窗没接回读值"
    assert "PutIntimWithAfter(okDirect" in src, "纯灵石直通没接回读值"
