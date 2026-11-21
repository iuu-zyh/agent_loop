"""工具分组的**跨语言一致性** 与 忙碌闸口径（10-16）。

为什么值得有这条测试
--------------------------------------------------------------------------------
`ActionWatcher` 里手抄了一份"动作工具"清单（5 个），注释写着"必须与 Python 侧
`tools/schemas.py` 的动作/只读分组一致"—— 但**没有任何测试真的去核对**。

2026-10-16 实测：Python 侧 `ACTION_TOOLS` 是 **6** 个（含 `trade`），C# 侧只抄了 5 个。
后果是**静默**的：异地主动开口回合里模型成交了一笔买卖 → `ActionWatcher.OnActionDone`
因 `IsActionTool("trade")==false` 直接 return ⇒ **不自动打开对话 UI** ⇒ 玩家屏幕上
什么都没发生。这正是 `ActionWatcher` 当初要修的那个病（"NPC 就在玩家旁边论道了一场，
玩家屏幕上什么都没发生"）的同类，只是换了个工具。

同一次核对还发现三处文档漂移，一并钉住（这个仓库的风格：漂移不是"注释写得不好"，
而是**下一个改这里的人会被误导**）：

  1. `ws_channel.handle_initiative` 的 docstring 说"本回合工具表里拿掉 5 个动作工具" ——
     实际工具表**恒定不动**（前缀缓存，见 `dialogue_agent._step` 的 header hash）；
  2. `dialogue_agent` 执行层注释说"装配层已把动作工具从工具表剔除" —— 同上，是错的
     （而且它就在拦截代码正上方，最容易被当成事实）；
  3. `docs/reference/csharp-iron-laws.md 三` 说 `DramaGate.SendChatBehindHook/SendChatBehind()` 负责让位 ——
     那两个方法**已被有意删除**，现行做法是 `g.ui.OpenUI + (UILayer)0` 的天然层级。

本文件钉三件事：

  A. **C# `ActionWatcher.ActionTools` 与 Python `ACTION_TOOLS` 集合相等**（逐名对账）；
  B. **忙碌闸的真实形态**：工具表恒定 + 两层约束（`BUSY_NOTE` 软 + 执行层硬），
     且被拦的集合就是 `ACTION_TOOLS`（不是"装配层剔除"）；
  C. **已退役的 `SendChatBehind` 不许被文档重新说成现行机制**。

做法与 `test_action_done_queue.py` / `test_drama_segment_contract.py` 同款：
读**源码文本**做结构化断言（本仓的跨语言契约都是这个路子 —— 两边靠键名/常量对齐，
错一个字母完全静默）。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 动作工具清单的"唯一事实来源"：Python schemas。
# 这里**不 import**，而是直接解析源码 —— 与其它契约测试一致（也避免 import 副作用）。
SCHEMAS = ROOT / "tools" / "schemas.py"
ACTION_WATCHER = ROOT / "csharp" / "tools" / "ActionWatcher.cs"
WS_CHANNEL = ROOT / "transport" / "ws_channel.py"
DIALOGUE_AGENT = ROOT / "agent" / "dialogue_agent.py"
IRON_LAWS = ROOT / "docs" / "reference" / "csharp-iron-laws.md"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _py_action_tools() -> set[str]:
    """解析 `tools/schemas.py` 的 `ACTION_TOOLS`（推导式，故直接取 TOOL_ORDER − READONLY）。"""
    src = _read(SCHEMAS)
    m = re.search(r"^READONLY_TOOLS[^=]*=\s*\[(.*?)\]", src, re.S | re.M)
    assert m, "schemas.py 里找不到 READONLY_TOOLS"
    readonly = re.findall(r'"([^"]+)"', m.group(1))

    m = re.search(r"^TOOL_ORDER[^=]*=\s*\[(.*?)\]", src, re.S | re.M)
    assert m, "schemas.py 里找不到 TOOL_ORDER"
    order = re.findall(r'"([^"]+)"', m.group(1))

    assert readonly, "READONLY_TOOLS 解析为空"
    assert order, "TOOL_ORDER 解析为空"
    return set(order) - set(readonly)


def _cs_action_tools() -> set[str]:
    """解析 `ActionWatcher.cs` 里手抄的 `ActionTools` 集合。"""
    src = _read(ACTION_WATCHER)
    m = re.search(
        r"HashSet<string>\s+ActionTools\s*=\s*new\s+HashSet<string>\(StringComparer\.Ordinal\)\s*\{(.*?)\}",
        src, re.S,
    )
    assert m, "ActionWatcher.cs 里找不到 ActionTools 集合初始化"
    names = set(re.findall(r'"([^"]+)"', m.group(1)))
    assert names, "ActionWatcher.ActionTools 解析为空"
    return names


# ---------------------------------------------------------------- A. 分组一致


def test_cs_action_tools_equals_python_action_tools():
    """**唯一真契约**：C# 那份手抄清单必须与 Python 的推理结果逐个对得上。

    漏一个的后果是静默的：那个工具执行完之后 `OnActionDone` 直接 return，
    不自动开窗 —— 玩家看不到"它做了什么"，只剩一句迟到的回复。
    """
    py, cs = _py_action_tools(), _cs_action_tools()
    missing = py - cs          # Python 认为是动作、C# 没算 → 不自动开窗
    extra = cs - py            # C# 认为是动作、Python 不算 → 只读工具也会抢焦点
    assert not missing, (
        f"C# ActionWatcher.ActionTools 漏了这些动作工具：{sorted(missing)}\n"
        f"  后果：它们执行成功后不会自动打开对话 UI（玩家屏幕上什么都没发生，静默）。"
    )
    assert not extra, (
        f"C# ActionWatcher.ActionTools 多了这些（Python 侧不算动作）：{sorted(extra)}\n"
        f"  后果：只读查询也会把对话窗抢到玩家眼前。"
    )


def test_readonly_tools_are_exactly_the_first_three():
    """「查询前置」顺序语义（`READONLY_TOOLS` = `TOOL_ORDER` 前三位）—— 与
    `test_tool_usage_contract.py` 的重叠是**刻意的**：那条守 schemas 自身，
    这条守"动作集合推导"的前提（本文件的 A 依赖它）。"""
    src = _read(SCHEMAS)
    m = re.search(r"^TOOL_ORDER[^=]*=\s*\[(.*?)\]", src, re.S | re.M)
    order = re.findall(r'"([^"]+)"', m.group(1))
    m = re.search(r"^READONLY_TOOLS[^=]*=\s*\[(.*?)\]", src, re.S | re.M)
    readonly = re.findall(r'"([^"]+)"', m.group(1))
    assert readonly == order[:3], (
        f"READONLY_TOOLS={readonly} 不是 TOOL_ORDER 的前三位 {order[:3]}；\n"
        f"忙碌闸的「只读放行」与该推导同源，顺序错位会让某些动作工具在忙时被放行。"
    )


# ---------------------------------------------------------------- B. 忙碌闸的真实形态


def test_busy_does_not_filter_the_tool_table():
    """**工具表恒定**：装配层与 `_step` 都不许出现按 busy 过滤工具的代码。

    为什么钉"不存在"：这是个**反直觉**的设计（"只读工具不是更安全吗？为什么要给全部
    9 个？"）。下一个想"优化"这里的人很容易顺手加个过滤，而代价是服务商前缀缓存全废
    （工具表进 `request/header` 的 hash）。
    """
    sp = _read(ROOT / "agent" / "system_prompt.py")
    # 装配处只应有 global + scope 两路合并，不得出现 ACTION_TOOLS / busy 条件
    assert "ACTION_TOOLS" not in sp, (
        "system_prompt.py 出现了 ACTION_TOOLS —— 装配层不许按动作/只读分组过滤工具表"
    )
    assert "busy" not in sp, (
        "system_prompt.py 出现 busy —— 工具表装配不该知道玩家忙闲（前缀缓存）"
    )

    da = _read(DIALOGUE_AGENT)
    assert re.search(r"tools\s*=\s*assembly\.get\(\"tools\"\s*,\s*\[\]\)", da), (
        "dialogue_agent 不再是原样取 `assembly['tools']` —— 工具表被人为过滤了？"
    )


def test_busy_is_enforced_at_execution_layer_not_assembly():
    """忙碌约束的两层：软（`BUSY_NOTE` 尾部消息）+ 硬（`speech_only_turn` 执行层拦截）。

    钉住"硬那一层拦的就是 ACTION_TOOLS"，而不是某个手抄子集 —— 否则新增动作工具时
    忙碌闸会**静默放行**它（模型在玩家战斗时弹出一个原生窗）。
    """
    da = _read(DIALOGUE_AGENT)
    assert "self.speech_only_turn or self.no_tools_turn" in da, (
        "执行层的忙碌闸判据不见了"
    )
    assert "_blocked = self.no_tools_turn or name in _ACTION_TOOLS" in da, (
        "忙碌闸不再用 ACTION_TOOLS 判据 —— 新增动作工具会被静默放行"
    )
    assert "from ..tools.schemas import ACTION_TOOLS as _ACTION_TOOLS" in da, (
        "ACTION_TOOLS 的来源被改了（应来自 schemas，唯一事实来源）"
    )

    ws = _read(WS_CHANNEL)
    assert "agent.speech_only_turn = True" in ws, "忙时没有置执行层标记"
    assert "self._append_plugin_note(agent, \"ctx-initiative-busy\", post_note)" in ws, (
        "BUSY_NOTE 不再以 plugin 消息追加 —— 软约束丢了"
    )


def test_busy_note_mentions_every_action_tool_family():
    """`BUSY_NOTE` 是给**模型**看的，它逐项点名了不许做的动作。

    漏一类的代价：模型认为"这类不算行动"→ 忙碌回合去调它 → 执行层拒 → NPC 对玩家
    许诺了办不到的事。`trade` 曾经就漏在这句话里。
    """
    ws = _read(WS_CHANNEL)
    m = re.search(r"BUSY_NOTE\s*=\s*\((.*?)\)\s*\n", ws, re.S)
    assert m, "找不到 BUSY_NOTE 定义"
    note = m.group(1)

    # 每个动作工具都要在 BUSY_NOTE 里有对应的字面提法（人为映射：工具 → 中文动作名）
    must = {
        "world_ai_action": ("邀约", "论道", "切磋"),
        "social_relation": ("建立关系",),
        "economy_item": ("赠予",),
        "item_acquire": ("索取",),
        "movement": ("传送",),
        "trade": ("买卖",),          # ← 2026-10-16 补：此前漏在 BUSY_NOTE 之外
        "team": ("组队",),           # ← 2026-10-17 补：组队同样是"玩家忙时不该发起"的动作
    }
    assert set(must) == _py_action_tools(), (
        "本测试的词表与 schemas.ACTION_TOOLS 不同步 —— 加/删动作工具时请一并更新"
    )
    lost = [f"{tool}（应含 {words}）" for tool, words in must.items()
            if not any(w in note for w in words)]
    assert not lost, "BUSY_NOTE 漏了这些动作类别：\n  - " + "\n  - ".join(lost)


# ---------------------------------------------------------------- C. 忙碌闸口径的**跨文件**一致性


def test_no_source_claims_the_tool_table_is_filtered_when_busy():
    """**反漂移**：源码/文档里不许再出现"忙时把动作工具从工具表剔除"这类说法。

    为什么单独钉：这是本仓最顽固的一处漂移 —— 同一条错话曾在 4 个文件里各写一遍
    （`WsClient.SendInitiative` / `NpcInitiativeMonitor.BusyReason` /
    `ws_channel._run_initiative` / `docs/reference/csharp-iron-laws.md 三`），10-16 修了 2 处、漏了 2 处，
    复核时（10-17）又发现 `CanPopAtTrigger`/`UnreadStore`/`ActionWatcher` 三处
    注释仍写着旧语义 `sameGrid && !busy`。

    危害不是"注释不好看"：下一个改忙碌闸的人会以为"工具表会被裁掉"，
    于是**真的去加过滤** —— 服务商前缀缓存当场全废（工具表进 `request/header` hash，
    见 docs/features/brain-liveness.md §4），而这类性能回归在单机测试里**完全看不见**。

    判据只认**陈述句**：允许出现"不许裁剪/不裁剪/恒定"这类否定式（那正是正确说法）。
    也允许**引述旧说法再纠正**的句子（`dialogue_agent.py` 执行层那条注释就是这种形态：
    它把错话加引号引出来、紧接着写"实际…全程无过滤"）—— 所以命中行里若同时出现
    "实际/更正/误写/无过滤"这类**纠正词**，视为已纠正，放行。
    """
    BAD = re.compile(r"(从|把)[^。\n]{0,12}(工具表|工具清单)[^。\n]{0,8}剔除")
    CORRECTED = ("实际", "更正", "误写", "无过滤", "恒定", "不裁剪")
    targets = [
        ROOT / "csharp" / "bridge" / "WsClient.cs",
        ROOT / "csharp" / "npc" / "NpcInitiativeMonitor.cs",
        WS_CHANNEL,
        DIALOGUE_AGENT,
    ]
    hits = []
    for f in targets:
        for i, line in enumerate(_read(f).splitlines(), 1):
            if BAD.search(line) and not any(w in line for w in CORRECTED):
                hits.append(f"{f.relative_to(ROOT)}:{i}: {line.strip()[:110]}")
    assert not hits, (
        "以下位置仍声称「忙时把动作工具从工具表剔除」，而事实是工具表恒定不动"
        "（前缀缓存）—— 请改成「尾部 BUSY_NOTE（软）+ 执行层 speech_only_turn（硬）」：\n  "
        + "\n  ".join(hits)
    )


def test_stale_same_grid_and_not_busy_gate_is_not_documented_as_current():
    """`sameGrid && !isBusy` 是**旧**确认窗判据（10-16 已改为 `needConsent`）。

    现行：`needConsent = !busy && (sameGrid || 动作意图)` —— 异地动作也弹确认窗。
    这三处（`CanPopAtTrigger` / `ActionWatcher` 两个入口 / `UnreadStore` 分流契约）
    都在描述"什么时候当面弹出"，写着旧判据会让下一个改这里的人得出
    "异地动作不该开窗"的错误结论。

    ★ 判据必须同时覆盖**代码写法**与**中文表述**（10-17 变异验证抓出的漏网）★
    第一版只匹配 `sameGrid && !isBusy`，于是把 `UnreadStore` 那句改回中文
    「触发那一刻同格且玩家不忙」时**测试仍全绿** —— 偏偏中文表述是这几处注释的主要形态。
    """
    BAD = re.compile(
        r"`?sameGrid\s*&&\s*!\s*isBusy`?"           # 代码写法
        r"|同格且(?:玩家)?不忙"                       # 中文表述
        r"|同格且玩家空闲"
    )
    targets = [
        ROOT / "csharp" / "npc" / "NpcInitiativeMonitor.cs",
        ACTION_WATCHER,
        ROOT / "csharp" / "contacts" / "UnreadStore.cs",
        ROOT / "csharp" / "contacts" / "ContactDuty.cs",
    ]
    hits = []
    for f in targets:
        for i, line in enumerate(_read(f).splitlines(), 1):
            if not BAD.search(line):
                continue
            # 已标注为历史/已更正的行放行。这些词都是"我在讲过去"的信号：
            #   "原来的"/"那时"/"当时"/"历史"/"曾经"/"原先" —— 叙述旧语义；
            #   "10-16"/"不再"/"已改"/"旧判据" —— 明说它已过期。
            if any(w in line for w in ("10-16", "不再", "已改", "旧判据",
                                       "原来的", "原先", "那时", "当时",
                                       "历史", "曾经", "此前")):
                continue
            hits.append(f"{f.relative_to(ROOT)}:{i}: {line.strip()[:110]}")
    assert not hits, (
        "以下位置仍把旧判据 `sameGrid && !isBusy` 当作现行确认窗语义"
        "（10-16 起是 `needConsent = !busy && (sameGrid || 动作意图)`，异地动作也弹窗）：\n  "
        + "\n  ".join(hits)
    )


# ---------------------------------------------------------------- D. 已退役机制的文档


def test_docs_do_not_claim_retired_sendchatbehind_is_current():
    """`DramaGate.SendChatBehind*` 已被有意删除，文档不许再把它说成现行机制。

    为什么值得钉：这段"对话窗降到 100 让原生弹窗盖上来"写得非常有操作性，
    而按它去写代码的后果是**调一个不存在的方法**（编译期就能发现），
    或者更糟 —— 照它的思路重新引入 sortingOrder 搏斗，把已经用
    `g.ui.OpenUI + (UILayer)0` 解决了的问题再做一遍。

    ★ 判据必须**紧邻前置**（10-16 变异验证抓出的"假绿"）★
    第一版写成"这一行里出现「退役/已删除」就算数"，而文档那段是一整行长文本：
    把「已退役」标题改回「UI 让位 hook」之后，"退役"二字仍出现在同一行的
    `.workbuddy/memory` 引用里 ⇒ **变异测不出来**。现在要求"退役"这类词出现在
    **方法名之前 40 字内**，即它修饰的就是这个方法本身。
    """
    app = _read(IRON_LAWS)

    # ① 代码里确实没有这两个方法（退役是真的，不是文档漏改）
    for cs in (ROOT / "csharp").rglob("*.cs"):
        low = cs.read_text(encoding="utf-8", errors="ignore")
        assert "SendChatBehind" not in low, (
            f"{cs.relative_to(ROOT)} 里仍存在 SendChatBehind —— 退役结论有变？"
        )

    # ② 文档提到它时，"已退役"必须紧邻前置（或就在本条所在的**粗体标题**里）
    _RETIRED = ("已退役", "已删除", "全删", "不再存在", "已经全部删除")
    for m in re.finditer(r"SendChatBehind", app):
        line_start = app.rfind("\n", 0, m.start()) + 1
        line_end = app.find("\n", m.start())
        line = app[line_start: line_end if line_end != -1 else len(app)]
        head = app[max(0, m.start() - 40):m.start()]      # 方法名之前 40 字
        # 判据 = 紧邻前置 或 该条目的标题（本行开头到冒号/句号）里已声明退役。
        # 后者是必要的：文档的写法是「* **UI 让位 hook —— 已退役（10-16 更正）**：本条描述的…」，
        # 标题里已声明退役，正文再提方法名是合理的。
        title = re.split(r"[：。]", line, maxsplit=1)[0]
        assert any(w in head for w in _RETIRED) or any(w in title for w in _RETIRED), (
            "csharp-iron-laws.md 把已退役的 SendChatBehind 当作现行机制描述"
            f"（前置 40 字与条目标题里都没有退役字样）：{line[:80]}"
        )
