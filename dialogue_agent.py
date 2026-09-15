"""DialogueAgent — ReactLoopAgent 简化版（对话专用，分层分权）

分层职责：
- SystemPrompt：拥有 sections/contexts/tools 的屉，assemble 只合并排序，不调游戏
- GameBridge：唯一认识 g.world 的接口，DialogueAgent 每轮 preStep 取 L1（轮内缓存+差分，movement 后置脏刷新）、下发 tool 时转调
- DialogueAgent：turn/step 循环机，管 Inbox 领信、Session 落盘、LLM 调度、tool 并发
- AgentLoop：只管名册+锁+续档，不碰屉
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

from .session import Session
from .inbox import Inbox
from .system_prompt import SystemPrompt
from . import llm_adapter, log_setup
from .stats import UsageTracker
from .tools import text_render

log = log_setup.get_logger(__name__)


def _new_id() -> str:
    return str(uuid.uuid4())


def create_user_message(text: str, msg_id: Optional[str] = None, source: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    msg = {
        "role": "user",
        "content": [{"type": "text", "text": text}],
        "id": msg_id or _new_id(),
    }
    # source 仅作落账审计（如 initiative 标记），不参与模型消息结构
    if source:
        msg["source"] = source
    return msg


class DialogueAgent:
    """一 NPC 一实例的循环机（简化版 ReactLoopAgent）"""

    def __init__(
        self,
        session: Session,
        system_prompt: SystemPrompt,
        llm: Any | None = None,
        bridge: Any | None = None,
        compactor: Any | None = None,
        max_parallel_tools: int = 5,
        on_step: Optional[Callable[[Dict[str, Any]], Any]] = None,
        on_token: Optional[Callable[[str], Any]] = None,
        on_reasoning: Optional[Callable[[str], Any]] = None,
        stats: Optional[UsageTracker] = None,
        context_window: Optional[int] = None,
    ):
        self.id = session.id
        self.session = session
        self.system_prompt = system_prompt
        self.inbox = Inbox(session)
        # 忙碌回合标记：玩家正忙时 NPC 只许言语+只读 —— 装配层由
        # SystemPrompt.disable_tools 真拿掉动作工具，这里是**执行层兜底**（防历史/幻觉里
        # 已存在的动作调用绕过工具表被真的执行）。置位/复位都在 handle_initiative 内，
        # 同 NPC 回合由锁串行，故单标记安全。
        self.speech_only_turn = False
        # 诊断"一个工具都不调"（配合 _diag_initiative_force.txt 的 no_tools=1）：
        # 比 speech_only_turn 更硬——**连只读工具也拦**（speech_only_turn 只拦 ACTION_TOOLS）。
        # 用途是观测纯传音形态（横幅/红点），置位/复位与 speech_only_turn 同处（handle_initiative）。
        self.no_tools_turn = False
        # 单口 LlmClient：三路（openai/stub/echo）已下沉到 llm/，循环机只认 generate
        if llm is None:
            from .llm.echo_client import EchoLlmClient

            llm = EchoLlmClient()
        self.llm = llm
        self.bridge = bridge
        # 压缩器可选注入：只负责在 turn 边界触发，不内嵌压缩逻辑
        self.compactor = compactor
        # 观察者钩子（DI 注入，可选，不传则行为不变）：
        # - on_step:   每完成一个 step 吐一次事实（think/tool_call/tool_result/text），Agent 不解释、不渲染
        # - on_token:  逐 token 回调（透传给 LlmClient.generate 的 on_token），供流式转发（正文通道）
        # - on_reasoning: 思考流逐 token 回调（reasoning_content / 内嵌 <think> 拆出），供 UI「内心思量」折叠区
        self.on_step: Optional[Callable[[Dict[str, Any]], Any]] = on_step
        self.on_token: Optional[Callable[[str], Any]] = on_token
        self.on_reasoning: Optional[Callable[[str], Any]] = on_reasoning
        # 用量与耗时统计（DSH tokenUsage/sessionStats/contextPressure 对齐）：
        # 一 agent 一实例，随活体创建/销毁；resume 时从账本重建累计（计时归零）。
        self.stats: UsageTracker = stats if stats is not None else UsageTracker(context_window=context_window)
        if context_window is not None:
            self.stats.set_context_window(context_window)
        try:
            self.stats.rebuild_from_log(session.log)
        except Exception:
            pass
        # 运行时状态
        self.phase: Dict[str, Any] = {"kind": "idle", "last_turn": self._last_turn()}
        self._abort = False
        # 差分缓存：保 log 瘦与前缀稳定（对标 DSH RuntimeContextProjection + headerEquals）
        # L1 拆三段（self/player/recent），各自独立差分写屉 + 独立差分发送
        # 段名不许在这里写死（加「当前时间」段时踩到）：这三个字典原先硬编码
        # {"self","player","recent"}，新增段时 `_last_l1_text` 的合并、以及"是否全部找齐"的
        # 判据都会漏掉新段（`.get()` 不报错、赋值也能补键，所以漏了完全静默）。改为从
        # `_CTX_SEGMENTS` 派生 —— 段的权威清单只有那一处。
        _segs = tuple(getattr(system_prompt, "_CTX_SEGMENTS", ("self", "player", "recent")))
        # 存成实例字段：`_resync_runtime_ctx_baselines` 等处要按同一份清单遍历
        # （段的权威清单只有 SystemPrompt._CTX_SEGMENTS 一处，这里只是取一份快照）
        self._segs: tuple = _segs
        self._last_l1: Dict[str, Optional[str]] = {n: None for n in _segs}
        self._last_l1_text: Optional[str] = None  # 兼容字段：各段合并文本（供测试/审计）
        self._retained_ctx: Dict[str, Optional[str]] = {n: None for n in _segs}
        self._last_header_hash: Optional[str] = None
        # 输入侧观察流：sys 段级差分缓存（name→text；只有新段/变化段才上行，防每步全量刷——
        # 全文比较太粗：L1 段每回合微变会导致整份 system 变化 → 每回合全段重发）
        self._last_sys_parts: Dict[str, str] = {}
        # system 观察基线从持久化回填：重进游戏/重开被恢复的历史会话，首回合
        # 不应把未变的 system 全量重播到 UI；只有日志里没有快照记录（真·首轮）或 system
        # 确实变了才上行。快照在 _step 每次 sys 变化时落 dev/sys-snapshot。
        try:
            for ev in reversed(self.session.log):
                if ev.get("type") == "dev/sys-snapshot" and isinstance(ev.get("data"), dict):
                    self._last_sys_parts = dict(ev["data"])
                    break
        except Exception:
            pass
        # L1 基线从 surface 反查回填（只取每段最新一条 + 把被折走的段作废）。
        # 与"压缩后核对"共用同一个方法 —— 见 _resync_runtime_ctx_baselines 的注释。
        self._resync_runtime_ctx_baselines()
        # L1 每轮一次：turn 首步向 bridge 取 raw 缓存，轮内各步复用（逐段差分照旧逐步跑，
        # 轮内 raw 不变自然不重发）；movement 执行成功后置 dirty，下一步强制刷新
        # （summon 召回/传送会翻转 same_grid，传音/当面措辞必须跟上）
        self._turn_ctx: Any = None
        self._turn_ctx_dirty: bool = False
        # 图片附件侧表：`{消息 id: [data URL, ...]}`。
        # 图片只进当回合、绝不进历史 —— 账本（session.log / JSONL / 重放）里只有
        # C# 拼好的 `[图片：xx]` 占位文本，图像字节只存在这个**内存**字典里，仅在拼 LLM
        # 请求时浅拷贝挂进消息副本（_attach_turn_images），回合收口即清空。
        # 于是：同回合多步都能看到图；下一回合、重启重放、历史压缩都不再携带图像字节。
        self._turn_images: Dict[str, List[str]] = {}
        self.max_parallel_tools = max_parallel_tools

    def _resync_runtime_ctx_baselines(self) -> List[str]:
        """从 surface 反查 L1 基线：回填还在的段 + **作废已被折走的段**。

        两件事，两个调用时机：

        ① **回填**（构造时，2026-09-11 修）：重进游戏/重开被恢复的历史会话，
           `_retained_ctx` 若留空，首回合会把未变的 L1 全量重播到 UI。
           扫 surface 里的 plugin 段消息，按可识别前缀反推段名，**每段只取最新一条**
           （原实现倒序扫到每条都回填、被最旧覆盖 → diff 基准对不上当前值 → 未变也重发）。
           顺带把恢复的段回填 SystemPrompt 容器（进程重启后容器是空的），否则未变段会因
           「容器无值 → txt 空 → 判定 remove」而误显示为"已移除"。

        ② **作废**（压缩后，2026-09-14 补）：压缩的 replace 区间是在 `session.surface.nodes`
           上**连续**取的（`compress.py` 的 `start_seq, end_seq = nodes[0], nodes[cut_idx-1]`），
           而 L1 段夹在对话中间 → 会被一起折进纪要、从 surface 里消失。此时基线里还留着旧文本
           → 差分 B 判定"这段没变，不用发" → **段再也不会被发出去**，模型静默失去运行时状态
           （境界/心境/气运/好感/日期）。重开窗/读档能自愈（扫不到就重发），所以它只在
           同一次会话内发作 —— 这也是它一直没被发现的原因。

        为什么逐段核对、而不是压缩后无脑全作废：replace 只折 `nodes[0..cut-1]`，
        **保留尾里可能还留着某段的旧消息** → 全作废会让那段重发一遍、同一段两条并存。
        逐段核对"还在不在 surface"最准，而且不只管压缩 —— 任何原因导致段消息消失都能自愈。

        返回被作废的段名列表（供日志/测试）。
        """
        seen: set = set()
        try:
            for ev in reversed(self.session.log):
                src = (ev.get("data", {}) or {}).get("source", {}) or {}
                if ev.get("type") != "user/message" or src.get("plugin") != "@python-harness/system-prompt":
                    continue
                # 仅当该消息仍在 surface（未被 replace 清除）才算 retained
                if ev.get("seq") not in self.session.surface.nodes:
                    continue
                content = ev["data"].get("content", [])
                if content and isinstance(content[0], dict) and content[0].get("type") == "text":
                    name = self._restore_segment_from(content[0].get("text", ""))
                    if name:
                        seen.add(name)
                        self._last_l1_text = "__restored__"
                        if len(seen) >= len(self.system_prompt._CTX_SEGMENTS):
                            break
        except Exception:
            log.warning("L1 基线反查失败（沿用当前基线，可能造成重复发送）", exc_info=True)
        # ② 作废被折走的段。判据用 truthy：空段（如 C# 没发 now 时的 time 段）基线本就是 ""，
        #    不该被当成"发过又丢了"去重发一个永远为空的段。
        lost: List[str] = []
        for name in self._segs:
            if self._retained_ctx.get(name) and name not in seen:
                self._retained_ctx[name] = None
                self._last_l1[name] = None
                lost.append(name)
        return lost

    def _restore_segment_from(self, raw_text: str) -> Optional[str]:
        """从一条已发的 context 消息里按「可识别前缀」反推是哪一段，回填该段 retained，
        并把该段回填 SystemPrompt 容器（进程重启容器为空场景）。命中返回段名，否则 None。"""
        marker = "Current runtime context —— "
        if not raw_text or not raw_text.startswith(marker):
            return None
        try:
            label, _, rest = raw_text[len(marker):].partition("：")
        except Exception:
            return None
        label_to_name = {v: k for k, v in SystemPrompt._CTX_LABELS.items()}
        name = label_to_name.get(label.strip())
        if name is None or not rest:
            return None
        self._retained_ctx[name] = f"{marker}{label}：{rest}"
        self._last_l1[name] = rest
        try:
            # 容器回填：未变段在重启后不再因「容器空→txt 空→误判 remove」而显示移除
            self.system_prompt.context(f"runtime:{name}", rest, order=100, scope=self.id)
        except Exception:
            pass
        return name

    def _last_turn(self) -> int:
        for ev in reversed(self.session.log):
            if ev.get("type") == "turn/start":
                return ev["data"].get("turn", 0)
        return 0

    # ---------- 对外：收信 ----------
    def send(self, text: str, target: str = "next-turn", wake: bool = True, source: Optional[Dict[str, Any]] = None,
             images: Optional[List[Dict[str, Any]]] = None):
        """投信进筐。source 为可选的落账标记（如 {"kind":"initiative",...}，供主动开口审计）。

        images（09-13）：图片附件 `[{name,url,...}]`，url 是 data URL。
        **只在本回合喂给模型，不进历史** —— 落账的 content 只有 `text`（C# 侧已含
        `[图片：xx]` 占位），图像 url 存进内存侧表 `_turn_images`，回合收口清空。
        键用消息 id，故同回合后到的消息（next-step 插队）各自带自己的图、互不串。"""
        msg = create_user_message(text, source=source)
        if images:
            urls = [str(x.get("url")) for x in images
                    if isinstance(x, dict) and x.get("url")]
            if urls:
                self._turn_images[msg["id"]] = urls
        # 写入筐（会落 spliced 账）
        self.inbox.splice(target, len(self.inbox.state[target]), 0, [msg])
        if wake:
            self._wake()

    def _attach_turn_images(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """把本回合图片临时挂到「即将发给 LLM 的消息副本」上。

        ★ 必须是**浅拷贝 + 新 content 列表**，绝不能原地改 `session.derive_messages()`
        返回的对象 —— 那些就是账本里的事件 data 本体，原地改会把图像字节永久写进
        session.log / JSONL，正是「图片不进历史」要避免的。"""
        if not self._turn_images:
            return messages
        out: List[Dict[str, Any]] = []
        for m in messages:
            urls = self._turn_images.get(m.get("id")) if isinstance(m, dict) else None
            if not urls:
                out.append(m)
                continue
            nm = dict(m)
            nm["content"] = list(m.get("content") or []) + [
                {"type": "image", "url": u} for u in urls
            ]
            out.append(nm)
        return out

    def _wake(self):
        if self.phase["kind"] != "idle":
            return
        self.phase = {"kind": "running", "turn": self.phase["last_turn"], "step": 0}
        # 简化：同步跑 kick（测试用）；真实可放后台任务
        # 这里不自动 kick，由调用方显式 await run_until_idle

    async def run_until_idle(self):
        """跑到无 pending 为止（测试用同步驱动）"""
        # 若 idle 但有 pending，也要拉起
        if self.phase["kind"] == "idle" and self.inbox.has_pending:
            self._wake()
        try:
            while self.phase["kind"] == "running":
                cont = await self._turn()
                if not cont:
                    # turn 结束，检查是否还有 pending 需新 turn
                    if self.inbox.has_pending:
                        self.phase = {"kind": "running", "turn": self.phase["last_turn"], "step": 0}
                        continue
                    self.phase = {"kind": "idle", "last_turn": self.phase.get("turn", self.phase.get("last_turn", 0))}
                    break
        except Exception:
            # 状态自愈：回合失败也回到 idle（账本已由 _turn 的 finally 闭合为 turn/end:error）。
            # 异常照抛——让编排层决定如何兜底，这里只保证自己的状态不残留 running（否则
            # 下一次 NPC 主动开口会被快速失败误判为"忙"而丢弃）。
            self.phase = {"kind": "idle", "last_turn": self.phase.get("turn", self.phase.get("last_turn", 0))}
            raise

    # ---------- 内部：turn/preStep/step ----------
    async def _pre_step(self, target: str, turn: int, step: int):
        # 1. 领信
        claimed = self.inbox.claim(target, turn)
        # 2. L1 快照：分层分权——DialogueAgent 调 bridge 拿 raw、SystemPrompt 只负责成文
        #    三段（self/player/recent）各自独立差分写屉：仅变化的段才 replace
        if self.bridge is not None:
            try:
                # L1 每轮一次：turn 首步（_turn 已把缓存清空）或 movement 置脏后才真正调桥，
                # 其余步复用缓存；逐段差分照旧每步跑（内存比对零成本），轮内 raw 不变即不重发
                if self._turn_ctx is None or self._turn_ctx_dirty:
                    ctx = self.bridge.get_context(self.id)
                    if asyncio.iscoroutine(ctx):
                        ctx = await ctx
                    raw = ctx.get("raw", ctx) if isinstance(ctx, dict) else ctx
                    del ctx
                    self._turn_ctx = raw
                    self._turn_ctx_dirty = False
                raw = self._turn_ctx
                if isinstance(raw, dict):
                    # 真名回写：L1 raw.relations.player.name 带玩家真名时同步给压缩器，
                    # 否则历史压缩摘要里玩家仍是占位符「玩家」（server 构造 Compressor 时拿不到真名）
                    try:
                        pname = ((raw.get("relations") or {}).get("player") or {}).get("name")
                        if pname and pname != "玩家" and getattr(self.compactor, "player_name", None) != pname:
                            self.compactor.player_name = pname
                    except Exception:
                        pass
                    segs = self.system_prompt.format_l1_context(self.id, raw)
                    for name in self.system_prompt._CTX_SEGMENTS:
                        t = (segs.get(name) or "").strip()
                        if t != self._last_l1.get(name):
                            try:
                                self.system_prompt.remove_context(f"runtime:{name}", scope=self.id)
                            except Exception:
                                pass
                            if t:
                                self.system_prompt.context(f"runtime:{name}", t, order=100, scope=self.id)
                            # 修：空段也存 ""（而不是 None）—— 否则下一轮同空
                            # 文本比较 "" != None 永远不等 → diff 永远 emit remove → UI 永远
                            # 显示"已移除"（即便近况是真没数据，比如系统角色无经历日志）。
                            self._last_l1[name] = t
                    self._last_l1_text = " | ".join(s or "" for s in self._last_l1.values()) or None
            except Exception:
                # 曾经这里是静默 pass：桥取快照失败会退化成"无快照"措辞，从日志上完全看不出来。
                # L1 取不到 = 模型看不到气运/好感/心情，属于"回答变差"的头号隐性成因，必须留痕。
                log.warning("L1 上下文取数失败（本回合将按无快照继续，模型看不到人物状态）",
                            exc_info=True)
        # 3. 装配 system + contexts + tools（SystemPrompt 每步自检文件+懒建屉）
        assembly = self.system_prompt.assemble(scope=self.id)
        # 4. 上下文逐段差分发送：对标 DSH RuntimeContextProjection.project
        #    每段独立比较，仅变化的段才新增对应 user 消息（不重发整块）；同时把变化段作为
        #    ctx 观察事件上行 UI（step 区内「运行时上下文」块，只发变化段）
        context_msgs: List[Dict[str, Any]] = []
        ctx_parts: List[Dict[str, Any]] = []
        _ctx_labels = getattr(self.system_prompt, "_CTX_LABELS", {})
        for name in self.system_prompt._CTX_SEGMENTS:
            txt = next(
                (c.get("text", "") for c in assembly.get("contexts", []) if c.get("name") == f"runtime:{name}"),
                "",
            )
            desired = self.system_prompt.render_context_segment(name, txt)
            if desired != self._retained_ctx.get(name):
                if desired:
                    context_msgs.append({
                        "role": "user",
                        "content": [{"type": "text", "text": desired}],
                        "id": f"ctx-{name}-{turn}-{step}",
                        "source": {"kind": "plugin", "plugin": "@python-harness/system-prompt"},
                    })
                    ctx_parts.append({
                        "name": f"runtime:{name}",
                        "label": f"L1·{_ctx_labels.get(name, name)}",
                        "op": "set",
                        "text": desired,
                    })
                else:
                    ctx_parts.append({
                        "name": f"runtime:{name}",
                        "label": f"L1·{_ctx_labels.get(name, name)}",
                        "op": "remove",
                        "text": "",
                    })
                # 修：空段存 ""（而非 None）—— 否则下一轮同空文本比较 "" != None
                # 永远不等 → 永远 emit remove → UI 永远显示"已移除"（即便段是真没数据）。
                self._retained_ctx[name] = desired
        if ctx_parts:
            await self._emit_step({"kind": "ctx", "turn": turn, "step": step, "parts": ctx_parts})
        # 5. 瀑布桩：此处可插入 AGENTS.md 等插件；简化直接返回
        messages = list(claimed)
        messages.extend(context_msgs)
        return {"kind": "enter", "messages": messages, "assembly": assembly}

    async def _turn(self) -> bool:
        if self.phase["kind"] != "running":
            raise RuntimeError("turn without running phase")
        turn = self.phase["turn"] + 1
        self.session.append("turn/start", {"turn": turn})
        self.phase["turn"] = turn
        # 新一轮：清 L1 缓存 ⇒ 本轮首步重新取快照（气运/好感/心情等以轮为粒度刷新）
        self._turn_ctx = None
        self._turn_ctx_dirty = False
        # --- turn 边界自动压缩：此刻用户新消息在 inbox 未落面，只动上轮已稳定历史 ---
        if self.compactor is not None:
            try:
                with log_setup.stage("compact", turn=turn):
                    report = await self.compactor.maybe(self.session, self.session.request_header())
                # 压缩真发生了 → 核对 L1 基线：被 replace 折走的段作废，下一步 preStep 重发。
                # 见 _resync_runtime_ctx_baselines 的 ② 段注释（不作废 = 段永久不再发）。
                # 顺序上也正好：上面刚把 _turn_ctx 清空 → 重发的是**最新**状态，不是旧快照。
                if report:
                    lost = self._resync_runtime_ctx_baselines()
                    if lost:
                        log.info("压缩折走 L1 段 %s → 基线已作废，本步将重新注入最新状态",
                                 "/".join(lost))
            except Exception:
                # 压缩失败不该打断回合，但绝不能无声——它是"上下文爆了/越聊越慢"的常见原因。
                log.warning("回合边界自动压缩失败（本回合按未压缩继续）", exc_info=True)
        turn_ends = None
        target = "next-turn"
        # 本回合**实际领到**的带图消息 id。收口只清这些 —— 一次 `_pre_step` 只吃一条
        # next-turn，若玩家抢在回合开始前连发两条带图消息，第二条仍留在筐里等下一回合，
        # 它的图不能被本回合的收口顺手清掉（否则那条消息静默丢图）。
        claimed_img_ids: List[str] = []
        try:
            while True:
                step = self.phase["step"] + 1
                decision = await self._pre_step(target, turn, step)
                if decision["kind"] == "reject":
                    turn_ends = {"kind": "blocked"}
                    return False
                if turn_ends and not decision["messages"]:
                    break
                if self.phase["step"] == 0 and not decision["messages"]:
                    turn_ends = {"kind": "completed"}
                    return False
                for _m in decision["messages"]:
                    _mid = _m.get("id") if isinstance(_m, dict) else None
                    if _mid and _mid in self._turn_images:
                        claimed_img_ids.append(_mid)
                # 开 step
                self.session.append("step/start", {"turn": turn, "step": step})
                self.phase["step"] = step
                try:
                    for m in decision["messages"]:
                        # 存历史
                        self.session.append("user/message", m, {"surfaceOp": "append"})
                    step_end = await self._step(decision["assembly"])
                    if turn_ends is None or turn_ends.get("kind") != "max-tokens":
                        turn_ends = step_end
                finally:
                    self.session.append("step/end", {"turn": turn, "step": step})
                if turn_ends and not self.inbox.next_step:
                    break
                target = "next-step"
        except Exception as e:
            if self._abort:
                turn_ends = {"kind": "aborted", "reason": str(e)}
                raise
            turn_ends = {"kind": "error", "error": str(e)}
            raise
        finally:
            try:
                self.session.append("turn/end", {"turn": turn, "reason": turn_ends or {"kind": "completed"}})
            except Exception:
                pass
            # 图片附件寿命 = 本回合：只清**本回合领到的**那些，筐里等下一回合的消息保留其图
            for _mid in claimed_img_ids:
                self._turn_images.pop(_mid, None)
            self.phase["last_turn"] = turn
            _reason = (turn_ends or {"kind": "completed"}).get("kind", "completed")
            if _reason in ("error", "aborted"):
                log.error("回合 %d 以 %s 收口：%s", turn, _reason,
                          (turn_ends or {}).get("error") or (turn_ends or {}).get("reason") or "-")
            else:
                log.info("回合 %d 收口：%s（步数 %d）", turn, _reason, self.phase.get("step"))
        # 是否还有 pending 需新 turn
        return self.inbox.has_pending

    async def _step(self, assembly: Dict[str, Any]):
        turn = self.phase["turn"]
        step = self.phase["step"]
        system = self.system_prompt.render_prompt(assembly)
        # 输入侧观察流：system 组装段级差分——首回合全列；之后只上行
        # 新出现/真变化的段，全等则整块不发。UI 侧每回合新建「系统组装」块，
        # 未收到的段自然不进新块（与 ctx 段差分同语义）。
        try:
            _vars = assembly.get("variables", {}) or {}
            _sys_labels = getattr(self.system_prompt, "_SYS_LABELS", {})
            _all: Dict[str, str] = {}
            _ordered: List[Dict[str, Any]] = []
            for _s in assembly.get("sections", []) or []:
                _txt = _s.get("text", "") or ""
                for _k, _v in _vars.items():
                    _sv = str(_v() if callable(_v) else _v)
                    _txt = _txt.replace("{{" + _k + "}}", _sv).replace("{" + _k + "}", _sv)
                if _txt.strip():
                    _nm = _s.get("name", "?")
                    _all[_nm] = _txt
                    _ordered.append({"name": _nm, "label": _sys_labels.get(_nm, _nm), "text": _txt})
            sys_parts = [p for p in _ordered if self._last_sys_parts.get(p["name"]) != p["text"]]
            if sys_parts:
                await self._emit_step({"kind": "sys", "turn": turn, "step": step, "parts": sys_parts})
                # 持久化当前全量段快照（非 surface 事件）：供下一次 resume 回填 _last_sys_parts，
                # 避免重开/重进游戏首回合把未变的 system 全量重播到 UI。仅 sys 变化时落，log 顺带瘦。
                try:
                    self.session.append("dev/sys-snapshot", dict(_all))
                except Exception:
                    pass
            self._last_sys_parts = dict(_all)
        except Exception:
            pass
        tools = assembly.get("tools", [])
        # header 差分：文本没变不落盘（保 log 瘦）
        header_hash = hashlib.sha256(f"{system}\n{json.dumps(tools, ensure_ascii=False, sort_keys=True)}".encode("utf-8")).hexdigest()
        if header_hash != self._last_header_hash:
            self.session.append("request/header", {"header": {"system": system, "tools": tools}})
            self._last_header_hash = header_hash
        # 图片附件只在本回合可见：derive_messages() 返回的是**账本事件 data 本体**，
        # 所以这里必须走浅拷贝挂图（_attach_turn_images），不能原地改。
        messages = self._attach_turn_images(self.session.derive_messages())

        # --- 调 LLM：单口 generate，三路在 llm/ 各实现里，循环机不分支 ---
        # on_token/on_reasoning 可选透传：LLM 层流式产 token 时回调，Agent 只转发钩子、不解释
        # 计时（DSH sessionStats 对齐）：step_start→首 token=TTFT，首 token→落盘=decode，
        # step_start→落盘=llm；缺任一边整项退出，不污染平均。
        import time as _time
        _step_start = _time.monotonic()
        _first_token_t: List[float] = []

        _outer_on_token = self.on_token
        _outer_on_reasoning = self.on_reasoning

        async def _timed_on_token(tok: str):
            if tok and not _first_token_t:
                _first_token_t.append(_time.monotonic())
            if _outer_on_token is not None:
                r = _outer_on_token(tok)
                if asyncio.iscoroutine(r):
                    await r

        async def _timed_on_reasoning(tok: str):
            if tok and not _first_token_t:
                _first_token_t.append(_time.monotonic())
            if _outer_on_reasoning is not None:
                r = _outer_on_reasoning(tok)
                if asyncio.iscoroutine(r):
                    await r

        with log_setup.stage("llm", turn=turn, step=step):
            result = await self.llm.generate(system, messages, tools,
                                             on_token=_timed_on_token,
                                             on_reasoning=_timed_on_reasoning)
        _completed = _time.monotonic()
        _usage = getattr(result, "usage", None)
        _out_tokens: Optional[int] = None
        _in_tokens: Optional[int] = None
        _cache_hit: Optional[int] = None
        if isinstance(_usage, dict):
            _out_tokens = _usage.get("completion_tokens")
            if not isinstance(_out_tokens, int):
                _out_tokens = None
            # 入口 token 数：定位"首 token 慢"的关键自变量（prefill 随 prompt 增长，
            # 网关排队/大 prompt 都会让 ttft 暴涨——见 README.md 附录 B（日志规约））
            _in_tokens = _usage.get("prompt_tokens")
            _in_tokens = _in_tokens if isinstance(_in_tokens, int) else None
            _cache_hit = _usage.get("prompt_cache_hit_tokens")
            _cache_hit = _cache_hit if isinstance(_cache_hit, int) else None
        else:
            try:
                _u = getattr(_usage, "completion_tokens", None)
                _out_tokens = _u if isinstance(_u, int) else None
                _i = getattr(_usage, "prompt_tokens", None)
                _in_tokens = _i if isinstance(_i, int) else None
            except Exception:
                _out_tokens = None
        try:
            _llm_ms = (_completed - _step_start) * 1000.0
            _ttft = ((_first_token_t[0] - _step_start) * 1000.0) if _first_token_t else None
            _decode = ((_completed - _first_token_t[0]) * 1000.0) if _first_token_t else None
            self.stats.record_step(turn, step, _llm_ms, _ttft, _decode, _out_tokens,
                                   has_tool_calls=bool(result.tool_calls))
            log.info("LLM 完成：llm=%.0fms ttft=%s decode=%s in=%s out=%s cache_hit=%s tool_calls=%d",
                     _llm_ms,
                     f"{_ttft:.0f}ms" if _ttft is not None else "无流",
                     f"{_decode:.0f}ms" if _decode is not None else "-",
                     _in_tokens if _in_tokens is not None else "-",
                     _out_tokens if _out_tokens is not None else "-",
                     _cache_hit if _cache_hit is not None else "-",
                     len(result.tool_calls or []))
        except Exception:
            pass
        try:
            if _usage is not None:
                self.stats.add_usage(turn, step, _usage)
        except Exception:
            pass
        reasoning = getattr(result, "reasoning", "") or ""
        # 落盘 assistant（text + tool-call 同块，BlockAssembler 语义不可拆两条）
        msg = llm_adapter.create_canonical_assistant_message(result.text, result.tool_calls)
        if msg is not None:
            # 回填 provider/model 供 surface 审计（若 client 携带）
            try:
                if isinstance(msg.get("source"), dict):
                    prov = result.provider or getattr(self.llm, "provider", None) or "openai"
                    mdl = result.model or getattr(self.llm, "model", None) or "unknown"
                    msg["source"]["provider"] = prov
                    msg["source"]["model"] = str(mdl)
            except Exception:
                pass
            step_data = {"message": msg, "turn": turn, "step": step}
            if reasoning:
                step_data["reasoning"] = reasoning   # 思考内容（UI 展示用；wire 投影不回传）
            if _usage is not None:
                # 用量进账本（纯 dict 可 jsonl 落盘；resume 时 rebuild_from_log 恢复累计）
                try:
                    if isinstance(_usage, dict):
                        step_data["usage"] = dict(_usage)
                    else:
                        plain = self.llm._usage_to_plain(_usage) if hasattr(self.llm, "_usage_to_plain") else None
                        if plain is not None:
                            step_data["usage"] = plain
                except Exception:
                    pass
            self.session.append("assistant/message", step_data, {"surfaceOp": "append"})
        # 观察者：思考 + 纯文本 step 各吐一次（工具 step 在 _execute_tool_calls 里吐）。
        # think 先于正文，与模型输出顺序一致；reasoning 只进 UI，不改变账本原文形态
        if self.on_step is not None and reasoning:
            await self._emit_step({"kind": "think", "text": reasoning, "turn": turn, "step": step})
        if self.on_step is not None and result.text:
            await self._emit_step({"kind": "text", "text": result.text, "turn": turn, "step": step})
        # 分发工具：仍归 Agent/Tools（bridge 执行，LLM 保持无副作用）
        if result.tool_calls:
            return await self._execute_tool_calls(result.tool_calls, turn, step)
        return {"kind": "completed"}

    async def _emit_step(self, info: Dict[str, Any]):
        """观察者出口：吐一次 step 事实。失败只记录，绝不打断 turn。"""
        if self.on_step is None:
            return
        try:
            r = self.on_step(info)
            if asyncio.iscoroutine(r):
                await r
        except Exception:
            # 观察者（WS 推送）失败 = UI 少一段流；不能拖垮回合，但要留痕（否则 UI 空窗无从解释）
            log.debug("step 观察者推送失败（kind=%s）", (info or {}).get("kind"), exc_info=True)

    async def _execute_tool_calls(self, tool_calls: List[Dict[str, Any]], turn: int, step: int):
        """纯工具执行：只做 tool/call → bridge → tool/result，不再落 assistant（调用方已落）"""
        async def _one(tc: Dict[str, Any]):
            name = tc["name"]
            args = dict(tc.get("arguments", {}))
            # 注入 initiator：发起方恒为当前对话 NPC（Agent.id），C# 以此当 actor，target 已删
            try:
                if "initiator" not in args:
                    args["initiator"] = self.id
            except Exception:
                pass
            call_id = tc.get("id") or _new_id()
            # 落 tool/call（非 surface，derive 不投影）
            self.session.append("tool/call", {"turn": turn, "step": step, "callId": call_id, "name": name, "arguments": json.dumps(args, ensure_ascii=False)})
            if self.on_step is not None:
                await self._emit_step({"kind": "tool_call", "name": name, "args": args, "turn": turn, "step": step})
            # 忙碌回合兜底：本回合只许言语+只读。装配层已把动作工具从工具表剔除，
            # 这里再拦一次 —— 历史里残留的调用/模型幻觉不该真去改世界或弹原生窗。
            # 落账形状与下方正常路径**逐字同构**（canonical message + surfaceOp + sourceEventSeqs），
            # 否则 derive_messages 不会产生 tool 结果消息，模型看不到拒绝会反复重试。
            if self.speech_only_turn or self.no_tools_turn:
                try:
                    from .tools.schemas import ACTION_TOOLS as _ACTION_TOOLS
                    # no_tools_turn拦全部；speech_only_turn（忙碌）只拦动作
                    _blocked = self.no_tools_turn or name in _ACTION_TOOLS
                except Exception:
                    _blocked = self.no_tools_turn
                if _blocked:
                    res = {"success": False,
                           "error": ("【调试场景】本回合只说话，不调用任何工具" if self.no_tools_turn
                                     else "玩家此刻正在查看界面/战斗中，不能发起行动（本次只允许说话与只读查询）")}
                    log.info("工具 %s 被%s拦下（%s）", name,
                             "调试闸" if self.no_tools_turn else "忙碌闸",
                             "本回合一个工具都不调" if self.no_tools_turn else "本回合仅言语+只读")
                    text_b = text_render.render(name, args, res) or json.dumps(res, ensure_ascii=False)
                    tool_msg_b = llm_adapter.create_canonical_tool_result_message(call_id, text_b, is_error=True)
                    call_seq_b = next((ev["seq"] for ev in reversed(self.session.log)
                                       if ev.get("type") == "tool/call" and ev["data"].get("callId") == call_id),
                                      len(self.session.log) - 1)
                    self.session.append("tool/result", {"message": tool_msg_b},
                                        {"surfaceOp": "append", "sourceEventSeqs": [call_seq_b]})
                    if self.on_step is not None:
                        await self._emit_step({"kind": "tool_result", "tool": name, "text": text_b,
                                               "turn": turn, "step": step})
                    return call_id
            # 调桥（唯一认识 g.world 的口子）
            import time as _ttime
            _tool_start = _ttime.monotonic()
            if self.bridge is not None:
                try:
                    with log_setup.stage(f"tool.{name}", turn=turn, step=step):
                        # npc_id 随帧带：C# 侧据此把"动作完成"归属到本 NPC →
                        # 同格动作完成时自动打开对话 UI（ActionWatcher）
                        res = self.bridge.call_tool(name, args, npc_id=self.id)
                        if asyncio.iscoroutine(res):
                            res = await res
                except Exception as e:
                    log.warning("工具 %s 调用异常：%s", name, e, exc_info=True)
                    res = {"success": False, "error": str(e)}
                # 删掉 `[排查] inspect_unit res 原始载荷` 调试转储（每次 inspect 往日志写 1500 字）：
                # 它当初是为查「经历恒空」加的，那个 bug 已定案（层序 + filterHit，见附录 D.1）；
                # 失败路径本来就有 log.warning("工具 %s 调用异常")，正常路径不需要全量载荷。
            else:
                # 桥缺失：显式报错而非伪造 stub，避免模型把"没执行"误读成"查无此物"。
                # 真实工具链必须注入 bridge；此分支仅保证 turn 可收口。
                log.error("工具 %s 未执行：bridge 未注入", name)
                res = {"success": False, "error": "bridge 未注入，工具未执行"}
            _tool_ms = (_ttime.monotonic() - _tool_start) * 1000.0
            try:
                self.stats.record_tool(_tool_ms)
            except Exception:
                pass
            # 工具成败进日志：这是"慢在哪一步 / 为什么答非所问"的第一手证据。
            # 注意 RPC 超时（如等 C# 主线程 120s）会走上面的 warning 分支，不在此处重复。
            if isinstance(res, dict) and not res.get("success", True):
                log.warning("工具 %s 执行失败（%.0fms）：%s", name, _tool_ms, res.get("error"))
            else:
                log.info("工具 %s 完成（%.0fms）", name, _tool_ms)
            # movement 真实生效（summon 召回/传送）会翻转 same_grid：置脏让下一步 L1 强制刷新，
            # 否则本轮余下 step 仍按轮首快照写"异地神识传音"（错位措辞要到下轮才纠正）
            if name == "movement" and isinstance(res, dict) and res.get("success"):
                self._turn_ctx_dirty = True
            # 润色层（唯一叙述来源）：C# 只传原始数据，这里按工具名把 data
            # 润色成自然语言喂给 LLM 与 UI；渲染缺失/异常回退全量 JSON（错误帧/新工具兜底）。
            text = text_render.render(name, args, res) or json.dumps(res, ensure_ascii=False)
            is_error = not bool(res.get("success", True)) if isinstance(res, dict) else False
            tool_msg = llm_adapter.create_canonical_tool_result_message(call_id, text, is_error=is_error)
            # sourceEventSeqs 指向刚落的 tool/call，确保 replace 校验可追溯
            call_seq = next((ev["seq"] for ev in reversed(self.session.log) if ev.get("type") == "tool/call" and ev["data"].get("callId") == call_id), len(self.session.log) - 1)
            self.session.append("tool/result", {"message": tool_msg}, {"surfaceOp": "append", "sourceEventSeqs": [call_seq]})
            if self.on_step is not None:
                await self._emit_step({"kind": "tool_result", "tool": name, "text": text, "turn": turn, "step": step})
            return call_id

        # 限并发（对标 DSH maxParallelToolCalls 滚动池，简化为信号量）
        sem = asyncio.Semaphore(self.max_parallel_tools)

        async def _one_limited(tc):
            async with sem:
                return await _one(tc)

        await asyncio.gather(*[_one_limited(tc) for tc in tool_calls])
        # 工具后不收口，让 turn 继续下一 step（模型会看 tool/result 再决策）
        return None

    # ---------- 用量快照（WS 推送的唯一出口，原子 dict，不含 UI 逻辑） ----------
    def snapshot_stats(self) -> Dict[str, Any]:
        """返回 stats.snapshot()（tokenUsage/sessionStats/contextPressure 三块）。"""
        try:
            return self.stats.snapshot()
        except Exception:
            return {"tokenUsage": {}, "sessionStats": {}, "contextPressure": {}}

    def notify_llm_retry(self, turn: int, step: int) -> None:
        """同 step 内 LLM 重试开始：清 last 槽使下次用量另计（两次扣费）。"""
        try:
            self.stats.notify_retry(turn, step)
        except Exception:
            pass

    # ---------- 手动压缩入口 ----------
    async def compact_now(self) -> Optional[Dict[str, Any]]:
        """手动压缩：仅 idle（turn 外）可由玩家指令/UI 触发。返回 None 表示未执行。"""
        if self.compactor is None:
            return None
        if self.phase["kind"] != "idle":
            raise RuntimeError("compact_now 只能在 idle（turn 外）时调用")
        report = await self.compactor.compact_now(self.session, self.id)
        # 手动压缩是**另一个入口**（/compact、面板按钮），同样要把被折走的 L1 段基线作废
        # 漏了这里就会出现"自动压缩能自愈、手动压缩之后 L1 永久丢失"这种只在一条路上发作的怪相。
        if report:
            lost = self._resync_runtime_ctx_baselines()
            if lost:
                log.info("手动压缩折走 L1 段 %s → 基线已作废，下一回合重新注入最新状态",
                         "/".join(lost))
        return report

    # ---------- 生命周期 ----------
    def cancel(self):
        self.inbox.clear()
        self._turn_images.clear()   # 图片侧表随筐一起清（图片寿命 = 回合，取消即结束）
        self._abort = True
        self.phase = {"kind": "idle", "last_turn": self.phase.get("turn", self.phase.get("last_turn", 0))}

    def dispose(self):
        self.cancel()
        # 清 SystemPrompt 屉（分层分权：DialogueAgent 只清自己的屉，不碰全局）
        self.system_prompt.dispose_scope(self.id)
