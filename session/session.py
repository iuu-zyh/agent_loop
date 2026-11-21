"""Session — 内存流水账 + Surface/Header 投影（简化版）"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional


SURFACE_TYPES = {"user/message", "assistant/message", "tool/result"}

# 软删除注记：**故意不进 SURFACE_TYPES** —— 老版本 SurfaceManager 对未知类型
# 早退忽略（session 重放不崩，仅照旧显示被删段，优雅降级）；新版本按 delete 算子处理。
DELETE_TYPE = "history/delete"


def collect_deleted_seqs(log) -> set:
    """扫账本里全部 history/delete 事件，汇总被遮蔽的 seq（区间成员 ∪ drop_seqs）。

    UI 投影过滤与删除决策（判断"此前已删除"）共用；history/delete 自身 seq 不在其中。
    """
    deleted: set = set()
    for ev in log:
        if ev.get("type") != DELETE_TYPE:
            continue
        data = ev.get("data") or {}
        for r in (data.get("ranges") or []):
            try:
                s, e = int(r[0]), int(r[1])
            except Exception:
                continue
            if e < s:
                s, e = e, s
            deleted.update(range(s, e + 1))
        for d in (data.get("drop_seqs") or []):
            try:
                deleted.add(int(d))
            except Exception:
                continue
    return deleted


def pairing_balanced(log, nodes) -> bool:
    """目录级配对校验：tool-call 全部有回包、回包不先行（+1/−1 过程不变负、结束归零）。

    删除/任何改目录操作的 fail-closed 兜底——不关心失衡怎么来的（崩溃残局、未来事件
    类型、实现 bug），只关心最终状态是否协议合法。

    ★ 11-15 补：**数量对了不等于合法** ★ 原实现只数个数，于是"1 个 call + 1 个 result
    但 toolCallId 对不上"照样放行。而服务端的判据是**按 id 逐个对齐**的：

        An assistant message with 'tool_calls' must be followed by tool messages
        responding to each 'tool_call_id'. (insufficient tool messages ...)

    数量平衡、id 错配时那条请求照样 400 —— 这正是"玩家删了历史之后这个 NPC 再也不回话"
    这一类故障的形态。

    实现要点（两条都是踩过的坑）：
      · `tool-call` 块的 id 键**两种都认**：真机落盘是 `id`（`create_canonical_assistant_message`），
        而历史账本 / 测试夹具里也有写成 `toolCallId` 的。只认一个会把合法账本判成非法
        （删除/压缩被整体拒绝，玩家看到的是"删不掉"）——过度收紧比放过更伤人。
      · **没有 id 的块按顺序认**，不参与 id 比对（老账本没有 id，那时服务端也只按顺序）。
    """
    pending: list = []          # 还没被回包的 call_id（"" = 那一块没带 id，按顺序认）
    for n in nodes:
        try:
            ev = log[n]
        except IndexError:
            return False
        t = ev.get("type")
        if t == "assistant/message":
            content = (ev.get("data", {}).get("message", {}) or {}).get("content", []) or []
            for b in content:
                if isinstance(b, dict) and b.get("type") == "tool-call":
                    pending.append(str(b.get("id") or b.get("toolCallId") or ""))
        elif t == "tool/result":
            if not pending:
                return False            # 回包先行（没有等它的 call）
            content = (ev.get("data", {}).get("message", {}) or {}).get("content", []) or []
            for b in content:
                if not isinstance(b, dict):
                    continue
                if b.get("type") is not None and b.get("type") != "tool-result":
                    continue
                cid = str(b.get("toolCallId") or b.get("tool_call_id") or "")
                if not cid:
                    # 没有 id 的回包：只能按顺序认领**最早那个还等着**的 call
                    if pending:
                        pending.pop(0)
                    continue
                if cid not in pending:
                    return False        # id 对不上 —— 服务端会 400，这一份目录不能用
                pending.remove(cid)     # 乱序到齐也算合法
    return not pending


def find_orphan_tool_calls(log, nodes) -> List[int]:
    """找出**协议不合法**的地方，返回该剔除的 seq 列表（空 = 合法）。

    ## 为什么需要它（11-15，用户报 400 invalid_request_error）

    服务端的判据是**按 id 逐个对齐**：

        An assistant message with 'tool_calls' must be followed by tool messages
        responding to each 'tool_call_id'. (insufficient tool messages ...)

    而账本里可能留下三种坏形状，**每一种都不会在本地报错**，只在发请求时 400：
      ① assistant 声明了 tool-call，却没有对应的 tool/result（回合被取消/中断）；
      ② tool/result 没有对应的 tool-call（回包先行 —— 典型是 assistant 那条被删了）；
      ③ 数量对得上但 id 对不上。

    ## 修法：**删掉那条请求**，而不是补一条假结果

    用户的话：「检测到这样的情况的话去删除那个请求的」。
    这个方向对，而且比补假结果更干净：
      · 那条 assistant（及其孤儿 result）本身**就是一个没完成的动作**，
        它对模型没有任何信息价值 —— 补一条「这次调用没完成」反而占了上下文、
        还让模型以为自己调过那个工具；
      · 补假结果会**改变模型看到的历史**（它会依据一条它没真正得到的结果继续演），
        而删除只是把那半次尝试抹掉，模型看到的是「上一句我问了话，这次重新说」。

    返回的 seq 交给调用方落 `history/delete`（账本只追加，不原地改）。

    ## 归属规则（一个坏块吃哪些）

      · **孤儿 assistant**（声明了 call 但没被全部回包）：删它 **+ 它自己那几条已配对的
        result**。这是**必须级联**的：只删 assistant 的话，它那些已经回包的 result
        立刻变成孤儿（回包先行），下一次自检又得删一遍 —— 修不干净。
        而多删一个 result 的代价是 0（那条 result 本来也只服务这一条 assistant）；
      · **孤儿 result**（没有等它的 call）：只删它本身；
      · **id 错配的 result**：删它（它认领不到任何 call）；
      · 按 seq 顺序单趟扫描，`pending` 记录"还在等回包的 call"。
    """
    # 先收集结构：assistant 的 call 列表、result 的归属
    calls_by_seq: Dict[int, List[str]] = {}
    results_by_seq: Dict[int, List[str]] = {}
    order: List[int] = []
    for n in nodes:
        try:
            ev = log[n]
        except IndexError:
            continue
        t = ev.get("type")
        if t == "assistant/message":
            content = (ev.get("data", {}).get("message", {}) or {}).get("content", []) or []
            ids = [str(b.get("id") or b.get("toolCallId") or "")
                   for b in content
                   if isinstance(b, dict) and b.get("type") == "tool-call"]
            if ids:
                calls_by_seq[n] = ids
                order.append(n)
        elif t == "tool/result":
            content = (ev.get("data", {}).get("message", {}) or {}).get("content", []) or []
            blocks = [b for b in content if isinstance(b, dict)
                      and (b.get("type") in (None, "tool-result"))]
            if blocks:
                results_by_seq[n] = [str(b.get("toolCallId") or b.get("tool_call_id") or "")
                                     for b in blocks]
                order.append(n)

    drop: set = set()
    pending: List[list] = []            # [[assistant_seq, call_id], ...]
    for n in order:
        if n in calls_by_seq:
            for cid in calls_by_seq[n]:
                pending.append([n, cid])
            continue
        # tool/result
        for cid in results_by_seq[n]:
            if not pending:
                drop.add(n)             # 回包先行 / 它的 call 已不在目录里
                break
            if not cid:
                pending.pop(0)          # 无 id：按顺序认领最早那个（老账本）
                continue
            hit = next((i for i, p in enumerate(pending) if p[1] == cid), None)
            if hit is None:
                drop.add(n)             # id 对不上 ⇒ 这条 result 无处归属
                break
            pending.pop(hit)
    # 没等到回包的 call ⇒ 那条 assistant 整条作废，**连同它已配对的那几条 result**
    orphan_assistants = {p[0] for p in pending}
    drop |= orphan_assistants
    if orphan_assistants:
        owned: set = set()
        for a_seq in orphan_assistants:
            owned.update(calls_by_seq.get(a_seq, []))
        owned.discard("")
        for r_seq, cids in results_by_seq.items():
            if r_seq in drop:
                continue
            if cids and all(c in owned for c in cids):
                drop.add(r_seq)         # 它只服务那条被作废的 assistant
    return sorted(drop)


def derive_event_message(event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    t = event.get("type")
    data = event.get("data", {})
    if t == "user/message":
        return data
    if t == "assistant/message":
        msg = data.get("message", {})
        content = msg.get("content", [])
        if not content:
            return None
        return msg
    if t == "tool/result":
        return data.get("message")
    return None


class SurfaceManager:
    """增量 Surface 目录：nodes 为有序的 seq 列表"""

    def __init__(self, log: List[Dict[str, Any]]):
        self.log = log
        self.nodes: List[int] = []
        self.replace_generation = 0
        self._last_seq = -1
        # ── 配对自检的**脏标记**（11-15，答复"每次全遍历会不会太贵"）──
        #
        # 为什么需要：`_heal_tool_pairing` 若每个 step 都全量扫一遍 surface，
        # 成本是 O(节点数) —— 实测 1000 轮账本约 3.3ms/次。绝对值很小（一个 step 要等
        # LLM 2~10 秒），但它**随账本线性增长**，而「历史只会越来越长」。
        #
        # 关键事实：**配对损坏只可能由三种动作产生**，每一个都在这一个类里发生：
        #   ① `history/delete` 剔掉了某个成员（删一半 ⇒ 另一半成孤儿）；
        #   ② `replace` 折叠了节点（压缩/pruner）—— 正常不会破坏配对，但它是"改动目录"；
        #   ③ 删完/替换后重放（`__init__` 里逐条 `_apply`）。
        # 而**新增**节点（append）永远不会让已有配对失效：一条 assistant 带着
        # tool-call 落进来时它本来就是"待回包"状态，回包随后补上 —— 中途那一刻不算坏。
        #
        # 所以：只在 ①② 之后标脏；`append` 不标脏。自检时脏标记为空 ⇒ 直接返回 0（O(1)）。
        # 另有 `_verify_countdown` 做**周期性全量校准**，防"某条路径漏标脏"变成永久漏检
        # （校准间隔取 64 次自检，成本摊薄到可忽略，而漏检最多持续 64 个 step）。
        self.pairing_dirty = False
        self._verify_countdown = 0
        # 重放已有 log
        for ev in log:
            self._apply(ev)
        # 重放完要校准一次：老账本可能**本来就是坏的**（升级前留下的残局），
        # 那不属于 ①②，没有任何事件会为它标脏。
        self.pairing_dirty = True

    def _apply(self, event: Dict[str, Any]):
        t = event.get("type")
        seq = event.get("seq")
        if t == DELETE_TYPE:
            self._apply_delete(event)
            self.pairing_dirty = True          # ① 剔除成员 ⇒ 可能删出一半配对
            self._last_seq = seq
            return
        surface_op = event.get("surfaceOp")
        if t not in SURFACE_TYPES or surface_op is None:
            self._last_seq = seq
            return
        if surface_op == "append":
            self.nodes.append(seq)
            # ② 新增**不标脏**：一条带 tool-call 的 assistant 刚落下时本来就等着回包，
            #    回包随后就来 —— 中途那一刻不是损坏（标脏会让每个 step 都白扫一遍）。
        elif isinstance(surface_op, dict) and surface_op.get("op") == "replace":
            start = surface_op["start"]
            end = surface_op["end"]
            try:
                s_idx = self.nodes.index(start)
                e_idx = self.nodes.index(end)
            except ValueError as e:
                raise ValueError(f"Surface replace range not found: {e}")
            if s_idx > e_idx:
                raise ValueError("Surface replace start after end")
            self.nodes[s_idx : e_idx + 1] = [seq]
            self.replace_generation += 1
            # ③ 折叠动了目录：正常不破坏配对（compress 有 `_balanced_before` 把关），
            #    但它是少数"能改成员"的操作之一，标脏让自检看一眼。
            self.pairing_dirty = True
        else:
            raise ValueError(f"invalid surfaceOp {surface_op}")
        self._last_seq = seq

    def _apply_delete(self, event: Dict[str, Any]):
        """history/delete：机械剔除——affected =（ranges ∪ drop_seqs）− keep_seqs。

        只做成员剔除，**不做任何策略推理**：哪些纪要可删（覆盖闭包）、哪些替换节点豁免
        （sourceEventSeqs 联动）、哪些注入绝对豁免，全部由编排层预计算写进事件 data。
        这样运行时与重放同一路径、确定性一致，且本函数可独立单测。
        幂等：成员本就不在目录里（如已被压缩折叠的原始 seq）静默跳过，绝不抛错。
        """
        data = event.get("data") or {}
        affected = collect_deleted_seqs([event])
        affected -= set(data.get("keep_seqs") or [])
        if not affected:
            return
        self.nodes = [n for n in self.nodes if n not in affected]

    # ---------- 配对自检的 O(1) 快路径 ----------
    #: 每隔多少次自检做一次全量校准（漏标脏的兜底）。64 次 = 约 64 个 step，
    #: 而每个 step 至少几秒 ⇒ 最坏情况漏检持续十几分钟，且期间只是"没自愈"，
    #: 不影响正常对话（真正发不出去时还有 400 兜底那一道）。
    VERIFY_EVERY = 64

    def pairing_check_needed(self) -> bool:
        """现在要不要跑一次配对全扫？**O(1)**。

        绝大多数 step 走到这里都是"不用"：账本没有删除/折叠动作 ⇒ 配对不可能变坏。
        """
        if self.pairing_dirty:
            return True
        if self._verify_countdown <= 0:
            self._verify_countdown = self.VERIFY_EVERY
            return True
        self._verify_countdown -= 1
        return False

    def mark_pairing_checked(self) -> None:
        """自检跑完（无论有没有修）：清脏、重置校准计时。"""
        self.pairing_dirty = False
        self._verify_countdown = self.VERIFY_EVERY


class Session:
    """内存 Session：id + log + 双投影"""

    def __init__(self, id: str, header: Optional[Dict[str, Any]] = None, events: Optional[List[Dict[str, Any]]] = None):
        self.id = id
        self.header: Dict[str, Any] = header or {"id": id}
        self.log: List[Dict[str, Any]] = list(events) if events else []
        # 重建时补 seq
        for i, ev in enumerate(self.log):
            if "seq" not in ev:
                ev["seq"] = i
            if "time" not in ev:
                ev["time"] = int(time.time() * 1000)
        self.surface = SurfaceManager(self.log)
        # 图片仓（`storage.images.ImageStore`）：账本里只记 `ref`，图像字节在存档目录
        # `<sessions_root>/images/<npc>/`。见 `docs/archive/2026-10-18-图片进上下文.md`。
        #
        # 由**管家**在发布 agent 时挂上（`AgentLoop._publish`）—— 只有它知道"这份账本属于
        # 哪个存档目录"。裸 Session（单测 / 脚本直接 load_session）为 None：此时收图会跳过
        # 落盘（丢图 + 警告，文本照记），GC 也整体不跑（无仓可回收）—— 都不报错。
        self.image_store: Any = None
        self._header_fold: Optional[Dict[str, Any]] = None
        self._header_fold_seq = 0
        self._fold_header()
        # ── 上下文 token 估算缓存（11-15，用户报「用久了会卡」）──
        #
        # 为什么要缓存：`Compressor.maybe` **每个回合**都要估一次整份上下文的压力，
        # 而 `derive_messages()` 返回的是**账本本体对象**（同一批 dict，不拷贝）——
        # 上一回合算过的消息，这一回合只有末尾几条是新的，其余逐字节没变。
        # 不缓存的话每回合都要把几十万字重新扫一遍（实测 20 万字 ≈ 50ms/回合）。
        #
        # 判据用 `(len(log), surface.replace_generation)` 当版本号：
        #   · 追加事件 ⇒ len 变 ⇒ 重算（正确，宁可多算）；
        #   · 压缩/pruner 折叠 ⇒ replace_generation 变 ⇒ 重算（正确）；
        #   · 什么都没做 ⇒ 命中缓存（这就是省下来的那部分）。
        # 缓存键是 **surface 里的 seq**（不是 `id(dict)`）：seq 是账本内稳定唯一的，
        # 不会像 id 那样在对象回收后被复用 —— 用 id 做键有"旧条目命中新对象"的风险。
        self._token_cache_version: Optional[tuple] = None
        self._token_cache: Dict[int, int] = {}

    # ---------- 上下文规模（带缓存） ----------
    def _token_cache_key(self) -> tuple:
        return (len(self.log), self.surface.replace_generation)

    def estimate_context_tokens(self, header_tokens: int = 0) -> int:
        """当前 surface 的 token 估算（header + 全部消息），**带增量缓存**。

        与 `meter.estimate_header(header) + sum(meter.estimate_message(m) …)` 逐字等价，
        只是把"没变的消息"的结果留住。消费者（压缩阈值判据、面板压力条、
        `refresh_context_pressure`）都应走这里 —— 否则它们会各自把整份历史重算一遍。
        """
        from ..compaction import meter
        key = self._token_cache_key()
        if key != self._token_cache_version:
            self._token_cache_version = key
            # 版本变了但**不必清空**：被折走的 seq 不再出现在 surface 里，
            # 而仍在 surface 里的消息内容不会变 ⇒ 旧条目照样有效，留着更省。
            # 只按当前 surface 裁剪，防缓存随删改无限增长。
            live = set(self.surface.nodes)
            self._token_cache = {k: v for k, v in self._token_cache.items() if k in live}
        total = header_tokens
        for seq in self.surface.nodes:
            hit = self._token_cache.get(seq)
            if hit is None:
                try:
                    hit = meter.estimate_message(derive_event_message(self.log[seq]) or {})
                except Exception:
                    hit = 0
                self._token_cache[seq] = hit
            total += hit
        return total

    # ---------- 追加 ----------
    def append(self, type: str, data: Any, opts: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        opts = opts or {}
        seq = len(self.log)
        event: Dict[str, Any] = {
            "seq": seq,
            "time": int(time.time() * 1000),
            "type": type,
            "data": data,
        }
        # Surface 相关字段直接透传
        if "surfaceOp" in opts:
            event["surfaceOp"] = opts["surfaceOp"]
        if "sourceEventSeqs" in opts:
            event["sourceEventSeqs"] = opts["sourceEventSeqs"]
        # 校验并更新投影
        # SurfaceManager 会校验 seq 连续
        self.log.append(event)
        # 更新 Surface
        try:
            self.surface._apply(event)
        except Exception:
            # 回滚 log 以保持一致
            self.log.pop()
            raise
        # 更新 header 折叠
        if type == "request/header":
            self._fold_header()
        # 通知监听者（简化：无）
        return event

    # ---------- 投影 ----------
    def derive_messages(self) -> List[Dict[str, Any]]:
        msgs: List[Dict[str, Any]] = []
        for seq in self.surface.nodes:
            ev = self.log[seq]
            msg = derive_event_message(ev)
            if msg is not None:
                msgs.append(msg)
        return msgs

    def request_header(self) -> Optional[Dict[str, Any]]:
        return self._header_fold

    def _fold_header(self):
        # 取最后一条 request/header
        for ev in reversed(self.log):
            if ev.get("type") == "request/header":
                self._header_fold = ev["data"].get("header")
                return
        self._header_fold = None

    @property
    def events(self) -> List[Dict[str, Any]]:
        return self.log

    # ---------- 辅助 ----------
    def has_open_turn(self) -> bool:
        for ev in reversed(self.log):
            if ev["type"] == "turn/start":
                return True
            if ev["type"] == "turn/end":
                return False
        return False
