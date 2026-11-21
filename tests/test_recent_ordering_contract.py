"""通讯录「最近」排序契约（10-21 用户报障）

报障原文
--------
「NPC 自主交互之后出现横幅、传按钮有红点，这时候点开通讯录去看 NPC 发的话，
  但是 NPC 属于的那一行在通讯录里顺序没有变。通讯录里面已经有『最近』，
  即最近对话的排在前面，但是对于自主交互这里好像没有实现。」

根因（**不是**"没记录主动传音"）
------------------------------
`ContactDuty.OnUiEvent` 收到任何 `npc_reply`（含 initiative）都会 `MarkLocal`，
所以"记不记"没问题。问题在**排序用的那个时间戳从哪来**：

  旧判据 = `max(会话文件 mtime, 内存时间戳)`

而 `会话文件 mtime` 是「上次**存档**」的时刻，不是「上次说话」的时刻：

  · `flush_all` 在存档时把**所有** agent 一次性重写 —— 十几个文件落在几十毫秒内
    （真机实测：姜萌 .9534145 / 江宇文 .9564289，同一秒）。`list_sessions` 把
    float mtime 截断成 `int` 秒后，**全部相等** ⇒ 排序退化成随机；
  · 存档稀疏：今天说的话在存档前一个字都没落盘，mtime 还停在**上一次存档**
    ⇒ 主动传音的 NPC 在「最近」里纹丝不动（正是用户看到的现象）；
  · 内存时间戳会**赢不了** mtime（存档后 mtime 永远更大），而且重启/读档即失。

修法（用户 10-21 拍板：走 ②+③，不做 ①"提高 mtime 精度"）
--------------------------------------------------------
  ② 落盘**真实互动时刻**（`interactions.json`）：Python 在每次 `npc_reply` 记一笔，
     `save_happened` 时随账本一起固化；`list_sessions` 回传 `last`。跨重启/读档存活。
  ③ 补一致性缺口：`MarkLocal` 自己触发刷新（否则"动作完成直接弹窗"那条早退路径不刷新）。

本文件钉住的**不变量**（任何一条破了，这个 bug 就会以某种形态复发）
------------------------------------------------------------------
  A. 排序必须是 `last` **优先**，mtime 只在 `last` 缺失时兜底。
     ⚠ 写成 `max(last, mtime)` = 原 bug 原样复发（存档那一刻 mtime 又盖过 last）。
  B. 两个时间戳都真的从 `npc_reply` 那条路记下来（玩家消息 + 主动开口**两条**）。
  C. 互动时刻跨重启存活（落盘）、跨存档隔离（换档不串号）。
  D. `list_sessions` 的缓存必须在记录新互动时失效（否则 5s 内读到旧值）。
  E. 新交互不影响老存档：没有 `last` 的 NPC 回落到 mtime（可用排序，不是全 0）。
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import shutil
import urllib.parse

import pytest

from agent_loop.storage.contacts_store import ContactsService


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STORE_SRC = os.path.join(REPO, "csharp", "contacts", "ContactStore.cs")
DUTY_SRC = os.path.join(REPO, "csharp", "contacts", "ContactDuty.cs")
PY_STORE_SRC = os.path.join(REPO, "storage", "contacts_store.py")
PY_CHANNEL_SRC = os.path.join(REPO, "transport", "ws_channel.py")
CONTACT_SRC = os.path.join(REPO, "csharp", "UI", "ContactPresenter.cs")


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _body(src, sig, span=4000):
    """截取某个 **C#** 方法/属性声明之后的函数体（按大括号配平）。找不到 → 失败。

    ⚠ 只用于 C#。Python 请用 `_py_body` —— 这个函数在 Python 源码上会**静默截错**
      （Python 函数头后面第一个 `{` 可能在几千字符之外，截出来的是一段无关代码，
       于是 `in` 断言要么恒真要么恒假，两种都是假绿。10-21 实测踩到过）。
    """
    i = src.find(sig)
    assert i >= 0, f"源码里找不到签名：{sig}"
    j = src.find("{", i)
    assert j >= 0, f"{sig} 后面没有函数体"
    depth = 0
    for k in range(j, min(len(src), j + span * 8)):
        if src[k] == "{":
            depth += 1
        elif src[k] == "}":
            depth -= 1
            if depth == 0:
                return src[i:k + 1]
    raise AssertionError(f"{sig} 的函数体没配平（span 太小？）")


def _py_body(src, sig):
    """截取某个 **Python** 函数/方法体（按缩进判定，从 `def` 到下一个同级或更外层定义）。

    判据：`def sig` 的缩进为 N；其后第一行**非空且缩进 <= N** 的行就是函数结束。
    比按 `{` 配平正确得多（见 `_body` 的警告）。
    """
    lines = src.splitlines(keepends=True)
    start = None
    indent = 0
    for n, line in enumerate(lines):
        if sig in line and line.lstrip().startswith(("def ", "async def ")):
            start = n
            indent = len(line) - len(line.lstrip())
            break
    assert start is not None, f"源码里找不到函数：{sig}"
    end = len(lines)
    for n in range(start + 1, len(lines)):
        s = lines[n]
        if not s.strip():
            continue
        cur = len(s) - len(s.lstrip())
        if cur <= indent:
            end = n
            break
    return "".join(lines[start:end])


def _strip_py_comments(text):
    """去掉 Python 的 `#` 行注释（整行注释 + 行尾注释），避免"注释里提到就算通过"。"""
    out = []
    for line in text.splitlines():
        s = line.lstrip()
        if s.startswith("#"):
            continue
        i = line.find("#")
        out.append(line if i < 0 else line[:i])
    return "\n".join(out)


def test_py_body_extracts_the_right_function_and_stops_at_the_dedent():
    """★框架自检★ `_py_body` 必须**恰好**截出那一个函数。

    为什么要有这条（10-21 实测踩到）：最初这里用的是按 `{` 配平的 `_body`，
    而 Python 里 `def _touch_interaction` 后面第一个 `{` 在 **5668 字符之外**
    —— 截出来的是完全无关的一大段代码，于是 `assert "except Exception" in body`
    这类断言**恒真**（那段无关代码里恰好有 except），测试是绿的、变异却抓不到。
    这类"测试框架本身坏了"只能靠一条直接检查框架的测试挡住。
    """
    src = (
        "    def outer(self):\n"
        "        x = 1\n"
        "\n"
        "        def inner():\n"
        "            return 2\n"
        "\n"
        "        return inner()\n"
        "\n"
        "    def sibling(self):\n"
        "        return 3\n"
    )
    body = _py_body(src, "def inner")
    assert "return 2" in body, "没截到目标函数体"
    assert "def sibling" not in body, "越界截到了下一个同级函数（这就是假绿的温床）"
    assert "def outer" not in body, "截到了外层函数"

    body2 = _py_body(src, "def sibling")
    assert body2.strip().endswith("return 3"), f"尾边界不对：{body2!r}"
    assert "def outer" not in body2


def test_py_body_missing_function_fails_loudly():
    """找不到函数必须**报错**，不许静默返回空串（空串会让 `in` 断言恒假，也是另一种失真）。"""
    with pytest.raises(AssertionError, match="找不到函数"):
        _py_body("def a():\n    pass\n", "def nonexistent")


def test_csharp_body_rejects_python_sources():
    """`_body` 只该用在 C# 上 —— 这条把那条纪律写成断言。

    Python 源码里没有 `{`（或远在几千字符外）⇒ `_body` 会明确失败。
    "宁可炸，不要静默截错"是这里唯一可接受的方向。
    """
    py = "def f():\n    a = 1\n" + "    b = 2\n" * 600 + "def g():\n    pass\n"
    with pytest.raises(AssertionError):
        _body(py, "def f")


def _strip_csharp_comments(text):
    """去掉 // 行注释，避免"注释里写了 last 就算通过"这类假绿。"""
    out = []
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("//"):
            continue
        out.append(line)
    return "\n".join(out)


# ---------------------------------------------------------------------------
# A. 排序判据：last 优先，mtime 兜底（**不许写 max**）
# ---------------------------------------------------------------------------

def test_last_interact_prefers_server_last_and_never_mixes_in_mtime():
    """★核心★ `ContactStore.LastInteract` 的取值顺序。

    `_serverLast` / `_localLast` 有值 ⇒ 直接返回，**绝不让 `_sessionsLast`(mtime) 掺和**。
    这正是"存档把所有 mtime 刷成同一秒"不再能破坏排序的原因。
    """
    body = _strip_csharp_comments(_body(_read(STORE_SRC), "public static long LastInteract(string npc)"))
    assert "_serverLast" in body, "LastInteract 没读服务端持久化的 last"
    assert "_localLast" in body, "LastInteract 没读本地即时时间戳"
    assert "_sessionsLast" in body, "LastInteract 丢了 mtime 兜底（老存档会全部 last=0）"

    # 关键：mtime 必须在"前两者都没有"之后才被读 —— 顺序即正确性
    i_server = body.index("_serverLast")
    i_local = body.index("_localLast")
    i_mtime = body.index("_sessionsLast")
    assert i_server < i_mtime and i_local < i_mtime, "mtime 参与得太早（会盖过真实互动时刻）"

    # 必须有一道"有真实证据就直接返回"的早退，且位置在读 mtime 之前
    m = re.search(r"if\s*\(\s*last\s*>\s*0\s*\)\s*return\s+last\s*;", body)
    assert m, "没有「有真实互动证据就直接返回」的早退 —— mtime 会掺和进来"
    assert m.start() < i_mtime, "早退写在了读 mtime 之后（等于没早退）"

    # ★ 反模式红钱：绝不许对三个来源取 max
    assert "Math.Max" not in body and "MathF.Max" not in body, \
        "LastInteract 里出现了 Max —— 存档那一刻 mtime 会盖过真实互动时刻，原 bug 复发"


def test_last_interact_redline_actually_fires():
    """红钱自检：把判据改成 max 必须被上面那条测试抓住（防"测试永远绿"）。"""
    src = _read(STORE_SRC)
    bad = src.replace(
        "if (last > 0) return last;",
        "if (_sessionsLast.TryGetValue(npc, out var mm) && mm > last) last = mm;",
    )
    assert bad != src, "红钱自检失效：找不到被替换的那一行（测试已与源码脱钩）"
    body = _strip_csharp_comments(_body(bad, "public static long LastInteract(string npc)"))
    has_early = re.search(r"if\s*\(\s*last\s*>\s*0\s*\)\s*return\s+last\s*;", body)
    assert not has_early, "红钱自检失效：注入 bug 后早退仍然存在"


def test_store_carries_both_columns_and_clears_them_on_world_reset():
    """两列必须分开存（语义不同，合并即复发），换存档必须两列都清。"""
    src = _read(STORE_SRC)
    assert "_serverLast" in src and "_sessionsLast" in src, "两列没有分开存"
    body = _strip_csharp_comments(_body(src, "public static void ResetForWorld()"))
    for field in ("_sessionsLast.Clear()", "_serverLast.Clear()", "_localLast.Clear()"):
        assert field in body, f"换存档没清 {field} —— 上一档的时间戳会带进新档"


def test_set_session_index_fills_both_columns_separately():
    """★核心★ `SetSessionIndex` 必须**两列都写**，且同批替换时都先清。

    ⚠ 这条是 10-21 变异实测补的（原测试只查"`_serverLast` 在不在文件里"，
      删掉 `_serverLast.Clear()` 它照样绿）：少了任何一列的写入，`_serverLast`
      会永远是空表 ⇒ `LastInteract` 退回到 mtime ⇒ **表现与没修完全一致**，
      而所有"字段名出现过"的断言都还是绿的。这是最危险的假绿形态。
    """
    body = _strip_csharp_comments(_body(_read(STORE_SRC), "public static void SetSessionIndex("))
    assert "_sessionsLast.Clear();" in body, "SetSessionIndex 没清 mtime 列（旧值残留）"
    assert "_serverLast.Clear();" in body, "SetSessionIndex 没清 last 列（旧值残留）"
    assert re.search(r"_sessionsLast\[kv\.Key\]\s*=\s*kv\.Value", body), "mtime 列没被填"
    assert re.search(r"_serverLast\[kv\.Key\]\s*=\s*kv\.Value", body), "last 列没被填"
    # 两个 for 循环各自绑定自己那一列 —— 写成同一个循环就会串列
    assert body.count("foreach (var kv in") == 2, \
        "两列应当各自一个 foreach（共用一个循环就说明串列了）"


def test_duty_reads_the_last_field_from_list_sessions():
    """`list_sessions` 回包必须解析 `last`，且**必须把它送进 lasts 那一列**。

    ⚠ 只断言"读过 last 字段"是不够的（10-21 变异实测漏过一条）：把 `l` 塞进 `mtimes`
      那一列照样能通过所有"字段名出现"的检查，但两列语义当场混掉 ——
      `_serverLast` 永远是空、排序退回 mtime，**表现与没修一模一样**。
      所以这里必须同时钉住「l 进了 lasts」和「m 留在 mtimes」。
    """
    body = _strip_csharp_comments(_body(_read(DUTY_SRC), "public static void RequestSessions()"))
    assert '"last"' in body or "[\"last\"]" in body, "RequestSessions 没读回包里的 last 字段"
    assert "SetSessionIndex(" in body, "RequestSessions 没走双列写入入口"
    assert "LastInteract" not in body, "RequestSessions 不该在这里做取值判断（那是 Store 的职责）"

    # ★ 两列不许串★：`last` 只能进 `lasts`，`mtime` 只能进 `mtimes`
    assert re.search(r"if\s*\(\s*l\s*>\s*0\s*\)\s*lasts\.Add", body), \
        "`last` 没被送进 lasts 那一列（排序会永远退回 mtime）"
    assert re.search(r"if\s*\(\s*m\s*>\s*0\s*\)\s*mtimes\.Add", body), \
        "`mtime` 没被送进 mtimes 那一列"
    assert not re.search(r"if\s*\(\s*l\s*>\s*0\s*\)\s*mtimes\.Add", body), \
        "`last` 被塞进了 mtimes 列 —— 两列语义混掉，等于没修"
    assert not re.search(r"if\s*\(\s*m\s*>\s*0\s*\)\s*lasts\.Add", body), \
        "`mtime` 被塞进了 lasts 列 —— 等于把存档时刻当成说话时刻"


# ---------------------------------------------------------------------------
# B. 两个 npc_reply 分支都必须记（玩家消息 / 主动开口）
# ---------------------------------------------------------------------------

def test_both_real_reply_paths_touch_interaction():
    """★核心★ 两条**真的产生了回复**的路（玩家消息 / NPC 主动开口）都必须记互动时刻。

    用户报的就是**主动开口**这条 —— 只记玩家消息等于没修。

    判据用 `text=reply`（模型真的说了一句），**不是**"每个 npc_reply"：
    另外三处 `npc_reply` 是**系统提示**（`/compact` 兜底、"对方正忙着"超时回执、
    同意后被丢弃的回执），它们不代表这个人说过话 —— 记了反而会把他顶到「最近」首位，
    而那时他说的话一个字都没有。
    """
    src = _read(PY_CHANNEL_SRC)
    hits = [m.start() for m in re.finditer(r'send_event\(\s*\n?\s*"npc_reply",\s*npc_id=npc_id,\s*text=reply', src)]
    assert len(hits) == 2, f"预期恰好 2 处「真回复」发送点（玩家消息 / 主动开口），实际 {len(hits)}"
    for pos in hits:
        window = src[max(0, pos - 1200):pos]
        assert "_touch_interaction(" in window, (
            "有一处真回复发送前没有记录互动时间戳 —— "
            "那条来源的 NPC 在「最近」里永远不动"
        )


def test_system_notices_do_not_count_as_interaction():
    """系统提示帧（error=True 的合成文案）**不许**被记成互动。

    它们不是"这个人说了一句话"：`/compact` 的反馈、"他这一阵正忙着"的超时回执、
    "同意后被丢弃"的回执 —— 都没有真正的内容。记了就会让一个**什么都没说**的人
    跳到「最近」第一位，而点进去是空的。

    判据用「全文件恰好两处 `_touch_interaction` 调用」：新增任何一处都会让这条红
    —— 那时必须先想清楚"这一次到底算不算他说了话"。
    """
    src = _read(PY_CHANNEL_SRC)
    calls = len(re.findall(r"self\._touch_interaction\(", src))
    assert calls == 2, (
        f"`_touch_interaction` 被调用了 {calls} 次，预期恰好 2 次"
        "（玩家消息 / 主动开口两条真回复）。"
        "多出来的一般是系统提示帧 —— 那不是这个人说的话。"
    )


def test_touch_interaction_is_before_send_not_after():
    """顺序：先记、再发。反了的话帧可能先到 C#，面板读到旧值。"""
    src = _read(PY_CHANNEL_SRC)
    i_touch = src.index("self._touch_interaction(npc_id)")
    # 玩家消息那条：send_event("npc_reply" 紧随其后
    i_send = src.index('"npc_reply"', i_touch)
    assert i_touch < i_send, "_touch_interaction 写在了发送之后"


def test_touch_is_single_entry_helper():
    """必须是**单入口** helper（而不是各处直接摸 service）——

    将来加第三条回复路径时，漏掉的概率取决于"有没有一个显然该调的东西"。
    """
    src = _read(PY_CHANNEL_SRC)
    assert "def _touch_interaction(" in src, "没有单入口 helper"
    body = _strip_py_comments(_py_body(src, "def _touch_interaction"))
    assert "getattr(" in body, "没做方法存在性探测（老 service 会 AttributeError）"
    assert "except Exception" in body, "记录失败会炸掉回复路径（时间戳是次要效果，不许抛）"


# ---------------------------------------------------------------------------
# C/D. 落盘、隔离、缓存失效
# ---------------------------------------------------------------------------

def test_python_store_persists_and_isolates():
    """Python 侧往返：记 → 落盘 → 新实例读回；换档互不可见；坏文件不炸。"""
    root = tempfile.mkdtemp(prefix="agent_loop_recent_contract_")
    try:
        svc = ContactsService(root)
        svc.set_world("W_A", "唐炎")
        svc.touch_interaction("江宇文", 5000)
        assert svc.flush_interactions() == 1
        assert os.path.exists(os.path.join(svc.root, "interactions.json"))

        svc2 = ContactsService(root)
        svc2.set_world("W_A", "唐炎")
        assert svc2.list_interactions() == {"江宇文": 5000}

        svc2.set_world("W_B", "缪嘉歆")
        assert svc2.list_interactions() == {}, "换存档后串号"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_sessions_payload_shape_is_documented_and_stable():
    """`list_sessions` 每项必须恰好带 `npc_id` / `mtime` / `last` 三个键。

    C# 按名取键；少一个就是"静默回落"，而这种回落**看起来完全正常**（顺序只是不对），
    所以只能在契约层钉住形状。
    """
    root = tempfile.mkdtemp(prefix="agent_loop_recent_shape_")
    try:
        svc = ContactsService(root)
        svc.set_world("W_A", "唐炎")
        os.makedirs(svc.root, exist_ok=True)
        with open(os.path.join(svc.root, urllib.parse.quote("姜萌", safe="") + ".jsonl"),
                  "w", encoding="utf-8") as f:
            f.write("{}\n")
        svc.touch_interaction("姜萌", 42)
        item = svc.list_sessions()[0]
        assert set(item.keys()) == {"npc_id", "mtime", "last"}, f"形状变了：{item.keys()}"
        assert item["last"] == 42
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_save_happened_flushes_interactions():
    """`save_happened` 必须把互动时间戳与账本**同一次事件**里落盘。"""
    src = _read(PY_CHANNEL_SRC)
    i = src.index('if method == "save_happened":')
    j = src.index('if method == "load_happened":', i)
    body = src[i:j]
    assert "flush_all()" in body, "save_happened 没固化账本（前提变了？）"
    assert "_flush_interactions()" in body, "save_happened 没固化互动时间戳"
    assert body.index("flush_all()") < body.index("_flush_interactions()"), \
        "互动时间戳先于账本落盘 —— `_apply_world` 换档后目录才对，顺序反了会写错档"


def test_flush_helper_never_breaks_saving():
    """落盘失败不许抛（存档本身必须成功），但必须留日志（否则"排序不对"又要重查）。"""
    src = _read(PY_CHANNEL_SRC)
    body = _strip_py_comments(_py_body(src, "def _flush_interactions"))
    assert "except Exception" in body, "落盘失败会把存档流程炸掉"
    assert "log.warning" in body, "落盘失败静默 —— 排序问题将无从排查"


def test_touch_invalidates_sessions_cache():
    """记录新互动必须作废会话索引缓存（否则 5s 内读到旧 `last`）。"""
    src = _read(PY_STORE_SRC)
    body = _strip_py_comments(_py_body(src, "def touch_interaction"))
    assert "invalidate_sessions_cache()" in body, \
        "touch_interaction 没作废缓存 —— 「横幅弹出 → 立刻点开通讯录」会读到旧值"


def test_interactions_file_kept_separate_from_contacts_json():
    """互动时间戳必须是**独立文件**：contacts.json 的读侧契约是"contacts 列表"。"""
    src = _read(PY_STORE_SRC)
    assert 'INTERACTIONS_FILE = "interactions.json"' in src
    assert 'CONTACTS_FILE = "contacts.json"' in src
    assert "interactions.json" != "contacts.json"


def test_touch_skipped_when_world_unknown():
    """未定档时**不记**（那段时间不知道属于哪个存档，写了就是串档）。"""
    src = _read(PY_STORE_SRC)
    body = _strip_py_comments(_py_body(src, "def touch_interaction"))
    assert "require_world" in body and "world_id" in body, \
        "touch_interaction 没做未定档闸门（会串档）"


def test_json_failure_falls_back_not_crashes():
    """坏 JSON → 空表兜底（回落 mtime 排序），且之后仍可正常工作。"""
    root = tempfile.mkdtemp(prefix="agent_loop_recent_badjson_")
    try:
        svc = ContactsService(root)
        svc.set_world("W_A", "唐炎")
        os.makedirs(svc.root, exist_ok=True)
        with open(os.path.join(svc.root, "interactions.json"), "w", encoding="utf-8") as f:
            f.write("{ 坏掉的 json")
        assert svc.list_interactions() == {}
        svc.touch_interaction("姜萌", 9)
        assert svc.flush_interactions() == 1
        assert svc.list_interactions() == {"姜萌": 9}
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------------------
# E. 模拟复盘：把真机数据代进去，证明旧判据坏、新判据对
# ---------------------------------------------------------------------------

def _sort_by_old_rule(items):
    """旧判据：max(mtime, 内存时间戳)。内存时间戳在"重启后"为空 ⇒ 实际就是按 mtime 排。"""
    return sorted(items, key=lambda x: -max(x["mtime"], x.get("local") or 0))


def _sort_by_new_rule(items):
    """新判据：last 优先（server/last 与 local 取较大），缺失才回落 mtime。"""
    def key(x):
        real = max(x.get("last") or 0, x.get("local") or 0)
        return -(real if real > 0 else x["mtime"])
    return sorted(items, key=key)


def test_simulation_real_machine_data_reproduces_then_fixes_the_bug():
    """★模拟复盘★ 真机 10-21 的存档数据（`BOlQu6`，实测文件 mtime）。

    场 1（旧判据）：一次存档把所有人刷成同一秒 ⇒ 排序**退化成随机**，
                    且「最近说话的江宇文」完全排不到前面（用户看到的现象）。
    场 2（新判据）：`last` 有值的人按真实互动时刻排；只有老 NPC 回落 mtime。
    """
    # 真机实测（纳秒尾部证明是同一批写入）
    save_ts = 1792514472          # 2026-10-21 00:41:12 —— 最后一次存档
    data = [
        # npc,        mtime(=存档时刻),  真实最后说话时刻(last)
        ("江宇文",   save_ts, 1792560020),   # 今天 10:40 说过话（报障里那个 NPC）
        ("姜萌",     save_ts, 1792564680),   # 今天 11:58 说过话
        ("公冶抚",   1792418828, 0),          # 10-19 之后没再说过（老记录）
        ("管星贤",   1790610072, 0),          # 09-29
    ]
    items = [{"npc": n, "mtime": m, "last": l} for n, m, l in data]

    old = _sort_by_old_rule(items)
    # 旧判据下：江宇文/姜萌 与 mtime 相同 → 稳定排序保留原顺序，两人挤在"最新"里
    # 真正的问题是**同批 mtime 全相等**，所以这里断言"它们的排序键完全一样"
    keys = {x["npc"]: max(x["mtime"], 0) for x in items}
    assert keys["江宇文"] == keys["姜萌"] == save_ts, (
        "复盘前提不成立：真机里这两个文件的 mtime 确实相等（纳秒 .956/.953，同秒）"
    )
    top2 = {x["npc"] for x in old[:2]}
    assert top2 == {"江宇文", "姜萌"}, "旧判据首位是两个 mtime 相等的人（排序退化的现场）"

    new = _sort_by_new_rule(items)
    assert [x["npc"] for x in new] == ["姜萌", "江宇文", "公冶抚", "管星贤"], (
        "新判据没按真实互动时刻排序"
    )
    # 关键差异：旧判据里 姜萌 与 江宇文 的顺序由 mtime 相等后的**稳定顺序**决定（偶然），
    # 新判据里由真实时刻决定（姜萌 11:58 > 江宇文 10:40）—— 这是用户要的那个效果。
    assert new[0]["npc"] == "姜萌"


def test_simulation_saving_collapses_mtime_keys_into_one():
    """★模拟复盘（回归）★ 存档会把旧判据的排序键**全部压成同一个值**。

    这是原 bug 最阴的地方，也是"顺序没变"这个体感的直接来源：
      · 存档前，旧判据按 mtime 排序 —— 这套顺序与"谁最近说过话"**无关**；
      · 存档那一刻，`flush_all` 把所有人的 mtime 刷成同一秒 ⇒ 键全相等 ⇒ 排序彻底失去信息
        （Python 稳定排序会让它看起来"没动"，正好就是用户的观感）；
      · 新判据必须对"存档"完全免疫。

    构造要点（**测试写错过一次，别改回去**）：存档前 mtime 与 last 的顺序必须**不同**，
    否则刷成同一秒后旧判据恰好维持原序，对照就看不出来。
    """
    base = [
        # mtime 顺序：甲 > 乙 > 丙（"上次存档时甲最后落盘"）
        # last  顺序：乙 > 甲 > 丙（真实互动顺序，与 mtime 故意不同）
        {"npc": "甲", "mtime": 1000, "last": 5000},
        {"npc": "乙", "mtime": 900,  "last": 6000},
        {"npc": "丙", "mtime": 800,  "last": 4000},
    ]
    truth = ["乙", "甲", "丙"]                     # 按"谁最近说话"

    # ① 新判据：一开始就正确
    assert [x["npc"] for x in _sort_by_new_rule(base)] == truth

    # ② 旧判据：一开始就**错**（把甲排在第一，而乙才是刚说完话的那个）
    old_before = [x["npc"] for x in _sort_by_old_rule(base)]
    assert old_before == ["甲", "乙", "丙"]
    assert old_before != truth, "对照失效：旧判据居然猜对了（测试前提不成立）"

    # ③ 存档：所有人 mtime 刷成同一秒 → 旧判据的键**全部相等**（排序失去信息）
    saved = [dict(x, mtime=9999) for x in base]
    old_keys = [max(x["mtime"], x.get("local") or 0) for x in saved]
    assert len(set(old_keys)) == 1, (
        "明明是同一次存档写入，mtime 却有区分度 —— 与真机不符"
        "（实测姜萌 .953 / 江宇文 .956，同秒）"
    )

    # ④ 新判据对存档完全免疫（顺序一分不变）
    assert [x["npc"] for x in _sort_by_new_rule(saved)] == truth, \
        "存档把新判据的顺序弄乱了"

    # ⑤ 旧判据在存档后仍然拿不到正确顺序（它压根不知道 last）
    assert [x["npc"] for x in _sort_by_old_rule(saved)] != truth


def test_simulation_new_npc_without_history_still_visible():
    """没有 `last` 的老 NPC 必须仍能靠 mtime 出现（不是全 0 被过滤掉）。"""
    items = [
        {"npc": "老友", "mtime": 7000, "last": 0},
        {"npc": "新人", "mtime": 6000, "last": 8000},
    ]
    order = [x["npc"] for x in _sort_by_new_rule(items)]
    assert order == ["新人", "老友"], "没有 last 的老 NPC 排序位置不对"
    assert all(_sort_by_new_rule(items)), "老 NPC 被丢掉了（last=0 不该等于「不存在」）"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))


# ---------------------------------------------------------------------------
# F. 分层：有真实记录的永远排在"只有文件时间"的前面
#
# 10-21 模拟复盘发现的**真缺陷**（不是脚本错）：
#   只按时间戳大小排序时，刚存过一次档会把所有 mtime 刷成"此刻"，
#   而"此刻"系统性晚于所有人的真实说话时刻 ⇒ 从没说过话的老 NPC 排到最前面。
#   模拟里实测到：`管星贤/公冶抚/司徒月` 顶掉了刚说过话的 `姜萌/江宇文`
#   —— 原 bug 的**镜像形态**（原来该在前的没在前，现在不该在前的跑前面了）。
# ---------------------------------------------------------------------------

def test_store_exposes_has_interaction():
    """`HasInteraction` 必须存在，且**只看真实记录**（不许把 mtime 算进去）。"""
    body = _strip_csharp_comments(_body(_read(STORE_SRC), "public static bool HasInteraction(string npc)"))
    assert "_serverLast" in body, "HasInteraction 没看服务端 last"
    assert "_localLast" in body, "HasInteraction 没看本地时间戳"
    assert "_sessionsLast" not in body, \
        "HasInteraction 把文件 mtime 也算成「有互动」—— 那正好会毁掉分层"


def test_recent_sort_layers_before_comparing_time():
    """★核心★ 「最近」排序必须**先按 HasInteraction 分层**，再比时间。

    判据是**顺序**：`HasInteraction` 的调用必须在 `CompareTo` 之前出现。
    写成"只比 LastInteract"就是这次模拟里抓到的镜像 bug。
    """
    body = _strip_csharp_comments(_body(_read(CONTACT_SRC), "private void RenderCurrent()"))
    i_layer = body.find("HasInteraction")
    assert i_layer >= 0, "「最近」排序没有按 HasInteraction 分层（刚存档就会让老 NPC 顶上来）"
    i_cmp = body.find("LastInteract.CompareTo", i_layer)
    assert i_cmp > i_layer, "分层判断写在了比时间之后（等于没分层）"
    # 分层必须真的影响顺序（返回 -1/1），不能只是读了一下
    assert re.search(r"if\s*\(\s*ha\s*!=\s*hb\s*\)\s*return\s+ha\s*\?\s*-1\s*:\s*1", body), \
        "读了 HasInteraction 却没让它决定顺序"


def test_recent_sort_is_deterministic_on_ties():
    """时间完全相等时必须**按名字定序**，否则每次重渲顺序都会变（列表自己会抖）。"""
    body = _strip_csharp_comments(_body(_read(CONTACT_SRC), "private void RenderCurrent()"))
    assert "CompareOrdinal" in body or "StringComparer" in body, \
        "并列时没有确定性定序 —— 每次重渲行序都可能变"


def _sim_sort(items):
    """复刻 C# 的完整判据：分层（有真实记录优先）+ 层内按时间 + 并列按名字。"""
    def key(x):
        known = 0 if x.get("last", 0) > 0 else 1          # 0 = 已知（排前面）
        ts = x.get("last") or x.get("mtime") or 0
        return (known, -ts, x["npc"])
    return [x["npc"] for x in sorted(items, key=key)]


def test_simulation_old_npcs_do_not_jump_ahead_after_saving():
    """★模拟复盘★ 刚存完档，**从没说过话的老 NPC 不许顶掉刚说过话的人**。

    这正是第一次模拟跑出来的失败形态（我当时把基准数据设错了才第一次没暴露，
    修正前提后才复现）——所以这条测试用的是**真实关系**：
    存档时刻 > 所有人的 last（刚存完档就是这样）。
    """
    SAVE_OLD = 1792514472          # 上次存档
    NOW = 1792565000               # 刚存的这一次（晚于所有 last）
    items = [
        {"npc": "江宇文", "mtime": NOW, "last": 1792560020},   # 今天 10:40 说过
        {"npc": "姜萌",   "mtime": NOW, "last": 1792564680},   # 今天 11:58 说过
        {"npc": "公冶抚", "mtime": NOW, "last": 0},            # 从没（在新版本里）说过
        {"npc": "管星贤", "mtime": NOW, "last": 0},
    ]
    order = _sim_sort(items)
    assert order[:2] == ["姜萌", "江宇文"], (
        f"刚存档后老 NPC 顶到了前面：{order} —— 这是原 bug 的镜像形态"
    )
    assert set(order[2:]) == {"公冶抚", "管星贤"}, f"没说过话的人排到了中间：{order}"


def test_simulation_layering_survives_repeated_saves():
    """反复存档，分层结果必须完全不变。"""
    base = [
        {"npc": "甲", "mtime": 100, "last": 500},
        {"npc": "乙", "mtime": 100, "last": 0},
        {"npc": "丙", "mtime": 100, "last": 300},
    ]
    first = _sim_sort(base)
    assert first == ["甲", "丙", "乙"], f"分层结果不对：{first}"
    for bump in (200, 900, 100000):
        saved = [dict(x, mtime=bump) for x in base]
        assert _sim_sort(saved) == first, f"存档（mtime={bump}）把分层结果改了：{_sim_sort(saved)}"
