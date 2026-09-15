"""ws_channel — C#↔Python 双向通道（单 WebSocket 全双工）

分层分权：
- WsServer：传输层。连接管理（接受/替换/断开）+ JSON 帧收发 + req_id 关联 +
  按 type 分发（request/response/event）。不碰对话逻辑、不碰 Agent。
- ChatHub：编排层。收到 player_message → 驱动 DialogueAgent → 推 step/text_delta/npc_reply。
  不碰协议细节（用 WsServer）、不碰 Agent 内部（用公开 API + 观察者钩子）。

消息协议（一条连接上 4 种消息，request/response 双向合法）：
  request   {"type":"request","req_id":str,"method":str,"params":{...}}
            Python→C#：get_context / call_tool（WsServer.request 发出，C# 主线程执行）
            C#→Python：get_history 等 UI 读请求（C# 发出 → ChatHub.handle_request 应答）
            两边各自只配对自己发出的 req_id（各查各的 pending），编号空间互不冲突
  response  {"type":"response","req_id":str,"ok":bool,"data":...,"error":str?}  对请求方向的应答
  event     {"type":"event","event":"player_message","npc_id":str,"text":str,
             "images"?:[{name,url,w,h,kb}]}                                  C# → Python
            ★ text 以游戏时间戳 `[N年M月] ` 开头（09-13，C# 在进 WS 前拼好）——本模块
              **只识别不重写**（唯一用处：命令判据先剥前缀，见 _strip_time_stamp）；
              正文带前缀进账本，NPC 靠它感知时间跨度。
  event     {"type":"event","event":"npc_initiative","npc_id":str,"intent":str,"reason":str?,
             "consented"?:bool,"debug"?:bool,"busy"?:bool,"speech_only"?:bool} C# → Python
  event     {"type":"event","event":"step|text_delta|npc_reply",...}          Python → C#
            （npc_reply 附 initiative=true/intent 时表示 NPC 主动开口回合的回复）
  event     {"type":"event","event":"stats_update","npc_id":str,               Python → C#
            "tokenUsage":{...},"sessionStats":{...},"contextPressure":{...}}
            回合结束推送的全量统计快照（纯数据，无 UI 逻辑；open_chat/get_history
            的 response.data.stats 是同构基线，首屏即有数）
"""

from __future__ import annotations

import asyncio
import json
import re
from time import monotonic
from typing import Any, Awaitable, Callable, Dict, Optional

from . import log_setup

log = log_setup.get_logger(__name__)

from .initiative import format_initiative_message, format_game_drama_message  # noqa: E402
from .history import project_ui_history  # noqa: E402
from .history_ops import DeleteError, append_delete, plan_delete  # noqa: E402
from .llm import think as _think  # noqa: E402
from .compaction import meter  # noqa: E402  （手动 /compact 的上下文压力前后对比）


# C# 侧给玩家消息拼的游戏时间戳前缀，形如 `[1年1月] `（年/月各自可变位数：`[12年10月] `）。
# 单一来源是 C# 的 `NpcInitiativeMonitor.CurrentTimeLabel()` + `UnitSnapshot.CnYearMonth`；
# 这里只**识别**它（命令判据要剥掉它），绝不重新生成——两边各拼一份必然漂移。
_TIME_STAMP_RE = re.compile(r"^\[\d+年\d+月\]\s*")


def _strip_time_stamp(text: str) -> str:
    """剥掉行首的游戏时间戳前缀；没有则原样返回。

    **只用于命令判据**（首尾空白 + 大小写归一那一类），正文一律带着前缀进账本 ——
    "这句话是哪年哪月说的"正是 NPC 需要长期记得的信息，绝不能在落账前洗掉。
    """
    return _TIME_STAMP_RE.sub("", text or "", count=1)



class WsServer:
    """单连接全双工通道（Python 作服务器，等待 C# 客户端连入）。

    死锁规避：接收循环常驻（独立于消息处理），回合中 C# 对 request 的 response
    才会被及时读到并解掉 pending future。
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 8766, request_timeout: float = 5.0):
        self.host = host
        self.port = port
        self.request_timeout = request_timeout
        self._conn: Optional[Any] = None        # 当前连接的 websocket（可空）
        self._pending: Dict[str, asyncio.Future] = {}
        self._req_seq = 0
        self._message_handler: Optional[Callable[..., Awaitable[None]]] = None
        self._initiative_handler: Optional[Callable[[str, str, str], Awaitable[None]]] = None
        self._request_handler: Optional[Callable[[str, Dict[str, Any]], Awaitable[Dict[str, Any]]]] = None
        self._compact_handler: Optional[Callable[[str], Awaitable[None]]] = None
        self._server: Optional[Any] = None

    # ---------- 生命周期 ----------
    @property
    def bound_port(self) -> int:
        """实际绑定的端口（port=0 时取系统分配值，供测试用）。"""
        if self._server is not None and getattr(self._server, "sockets", None):
            try:
                return self._server.sockets[0].getsockname()[1]
            except Exception:
                pass
        return self.port

    def register_message_handler(self, handler: Callable[..., Awaitable[None]]):
        """注入 player_message 处理者（由 ChatHub 提供）。

        handler(npc_id, text, images=None)；images 为图片附件数组（每项
        `{name, url, ...}`，url 是 data URL），缺省 None = 纯文字消息。
        用 `...` 而非定长签名：老端（不传 images）与新端（传 images）都能接。
        """
        self._message_handler = handler

    def register_initiative_handler(self, handler: Callable[[str, str, str, str, bool, bool, bool, bool, str], Awaitable[None]]):
        """注入 npc_initiative 处理者（由 ChatHub 提供）：NPC 主动开口事件
        （含 game_drama 剧情原文变体、consented 同意放行变体、debug 诊断强制变体、
        busy 玩家忙碌变体——忙碌时只许言语+只读，禁动作工具；
        speech_only 诊断变体——连只读也禁，一个工具都不调，见 `_diag_initiative_force.txt` 的 no_tools=1；
        speaker 剧情窗变体——本页那句是谁说的，npc / player / 空=判不出，只有 game_drama 会带）。"""
        self._initiative_handler = handler

    def register_request_handler(self, handler: Callable[[str, Dict[str, Any]], Awaitable[Dict[str, Any]]]):
        """注入 C#→Python 方向的 request 处理者（由 ChatHub 提供）。

        handler(method, params) 成功返回 data dict（进 response.data）；
        抛异常 → response ok:false（传输层兜底转错误帧，handler 异常不打死接收循环）。
        """
        self._request_handler = handler

    def register_compact_handler(self, handler: Callable[[str], Awaitable[None]]):
        """注入手动压缩事件（event=compact，玩家输入 /compact 触发）处理者（由 ChatHub 提供）。

        专用通道：不进对话历史、不触发 LLM 回合；结果由 handler 经 compact_result 事件回推。
        """
        self._compact_handler = handler

    async def start(self) -> None:
        import websockets

        # max_size 必须显式给
        # websockets 库默认 max_size=1MiB（2**20）：player_message 带图片时是 base64 的
        # data URL（1MB 图 → ≈1.37MB 文本），**一超限就 1009 PayloadTooBig 关连接**，
        # 表现是「C# 莫名断线重连」且没有任何有用报错——这个坑不显式放开就会反复踩。
        # 8MiB 对应 C# 侧 ImageInput 的预算（长边 1024 / 单张 ≤1.5MB / 最多 3 张）留足余量。
        self._server = await websockets.serve(self._handle, self.host, self.port,
                                              max_size=8 * 1024 * 1024)

    async def stop(self) -> None:
        if self._conn is not None:
            try:
                await self._conn.close()
            except Exception:
                pass
            self._conn = None
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    # ---------- 连接与接收 ----------
    async def _handle(self, websocket: Any, *args: Any) -> None:
        """新连接到来：替换旧连接（游戏重进场景），随后进入常驻接收循环。"""
        old = self._conn
        self._conn = websocket
        if old is not None:
            log.warning("C# 新连接到来，替换掉仍持有的旧连接（游戏重进场景？）")
            try:
                await old.close()
            except Exception:
                pass
        log.info("C# 已连接（本地 :%s）", self.bound_port)

        async def _run_player_message(npc_id: str, text: str, images: Any = None):
            # 异步转推：接收循环不被回合阻塞，response 才能被及时处理
            try:
                await self._message_handler(npc_id, text, images)
            except Exception:
                log.exception("player_message 处理失败")

        async def _run_initiative(npc_id: str, intent: str, reason: str, text: str = "",
                                  consented: bool = False, debug: bool = False, busy: bool = False,
                                  speech_only: bool = False, speaker: str = ""):
            try:
                await self._initiative_handler(npc_id, intent, reason, text, consented, debug, busy,
                                               speech_only, speaker)
            except Exception:
                log.exception("npc_initiative 处理失败")

        async def _run_request(rid: str, method: str, params: Dict[str, Any]):
            # 异步应答：接收循环不被处理耗时阻塞（对称于 player_message 的异步转推）；
            # handler 抛异常转 ok:false 错误帧，绝不打断接收循环
            try:
                data = await self._request_handler(method, params)
                frame: Dict[str, Any] = {"type": "response", "req_id": rid, "ok": True, "data": data}
            except Exception as e:
                log.exception("request 处理失败: %s", method)
                frame = {"type": "response", "req_id": rid, "ok": False, "data": None,
                         "error": str(e) or type(e).__name__}
            try:
                if self._conn is not None:
                    await self._conn.send(json.dumps(frame, ensure_ascii=False))
            except Exception:
                pass

        async def _run_compact(npc_id: str):
            # 手动压缩事件：同样异步转推（压缩含一次摘要 LLM 调用，不能阻塞接收循环）
            try:
                await self._compact_handler(npc_id)
            except Exception:
                log.exception("compact 事件处理失败")

        try:
            async for raw in websocket:
                try:
                    msg = json.loads(raw)
                except Exception:
                    log.warning("收到非法 JSON 帧（已丢弃）: %.200s", raw)
                    continue
                t = msg.get("type")
                if t == "response":
                    req_id = msg.get("req_id")
                    fut = self._pending.pop(str(req_id), None)
                    if fut is not None and not fut.done():
                        fut.set_result(msg)
                    else:
                        log.debug("收到无主 response（req_id=%s，可能已超时/已弃）", req_id)
                elif t == "event" and msg.get("event") == "player_message" and self._message_handler is not None:
                    _imgs = msg.get("images")
                    if log_setup.flag("trace_frames"):
                        log.debug("← event player_message npc=%s len=%s imgs=%s", msg.get("npc_id"),
                                  len(str(msg.get("text", "") or "")),
                                  len(_imgs) if isinstance(_imgs, list) else 0)
                    asyncio.get_running_loop().create_task(
                        _run_player_message(str(msg.get("npc_id", "")), str(msg.get("text", "")),
                                            _imgs if isinstance(_imgs, list) else None)
                    )
                elif t == "event" and msg.get("event") == "npc_initiative" and self._initiative_handler is not None:
                    if log_setup.flag("trace_frames"):
                        log.debug("← event npc_initiative npc=%s intent=%s", msg.get("npc_id"), msg.get("intent"))
                    asyncio.get_running_loop().create_task(
                        _run_initiative(str(msg.get("npc_id", "")), str(msg.get("intent", "")), str(msg.get("reason", "")),
                                        str(msg.get("text", "") or ""), bool(msg.get("consented", False)),
                                        bool(msg.get("debug", False)), bool(msg.get("busy", False)),
                                        bool(msg.get("speech_only", False)),
                                        str(msg.get("speaker", "") or ""))
                    )
                elif t == "event" and msg.get("event") == "compact" and self._compact_handler is not None:
                    if log_setup.flag("trace_frames"):
                        log.debug("← event compact npc=%s", msg.get("npc_id"))
                    asyncio.get_running_loop().create_task(
                        _run_compact(str(msg.get("npc_id", "")))
                    )
                elif t == "request" and self._request_handler is not None:
                    params = msg.get("params")
                    if log_setup.flag("trace_frames"):
                        log.debug("← request %s（C# 侧读请求）", msg.get("method"))
                    asyncio.get_running_loop().create_task(
                        _run_request(str(msg.get("req_id", "")), str(msg.get("method", "")),
                                     params if isinstance(params, dict) else {})
                    )
                else:
                    log.debug("← 未匹配的帧（无对应 handler 或未知 type）：%.200s", raw)
        finally:
            if self._conn is websocket:
                self._conn = None
            log.warning("C# 连接已断开（等待其自动重连；重连周期由 C# 侧决定）")

    # ---------- 出口：同步 RPC 与事件推送 ----------
    async def request(self, method: str, params: Optional[Dict[str, Any]] = None, timeout: Optional[float] = None) -> Dict[str, Any]:
        """异步 RPC：发 request → 等 response（req_id 配对）。失败/超时返回 {"success":false,...}。

        timeout 缺省用构造时的 request_timeout（可被装配层/config 覆盖）。
        可观测：整体走阶段打点（超 slow_ms 会主动告警）+ 超时/失败显式留痕——
        这条路径是"卡住但零报错"的高发区（等 C# 主线程），必须看得见。
        """
        t0 = monotonic()
        limit = self.request_timeout if timeout is None else timeout
        if self._conn is None:
            log.warning("RPC %s 未发出：C# 未连接（服务在等客户端连入/重连）", method)
            return {"success": False, "error": "C# 未连接"}
        self._req_seq += 1
        req_id = str(self._req_seq)
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[req_id] = fut
        try:
            await self._conn.send(
                json.dumps(
                    {"type": "request", "req_id": req_id, "method": method, "params": params or {}},
                    ensure_ascii=False,
                )
            )
        except Exception as e:
            self._pending.pop(req_id, None)
            log.warning("RPC %s 发送失败（%.0fms）：%s", method, (monotonic() - t0) * 1000.0, e)
            return {"success": False, "error": f"发送失败: {e}"}
        try:
            try:
                # 慢告警阈值（毫秒）= min(半个超时, 30s)。注意 limit 的单位是「秒」。
                _rpc_slow_ms = min(float(limit) * 500.0, 30000.0)
            except (TypeError, ValueError):
                _rpc_slow_ms = None
            with log_setup.stage(f"rpc.{method}", slow_ms=_rpc_slow_ms):
                resp = await asyncio.wait_for(fut, limit)
        except asyncio.TimeoutError:
            self._pending.pop(req_id, None)
            log.warning("RPC %s 超时（%.1fs，等 C# 主线程无应答）——回合将按「工具失败」继续，"
                        "模型可能重试同一工具而表现为卡顿", method, monotonic() - t0)
            return {"success": False, "error": "请求超时"}
        ok = bool(resp.get("ok"))
        if ok:
            return {"success": True, "data": resp.get("data")}
        err = resp.get("error") or "未知错误"
        log.warning("RPC %s 返回失败（%.0fms）：%s", method, (monotonic() - t0) * 1000.0, err)
        return {"success": False, "error": err}

    async def send_event(self, event: str, npc_id: str = "", **fields: Any) -> bool:
        """事件推送（step / text_delta / npc_reply）。连接缺失/断开返回 False。

        可观测：失败不再静默——玩家看不到回复（npc_reply）与看不到思考流
        （step/text_delta）的成因必须能回溯。逐 token 的 text_delta 只记 DEBUG。
        """
        if self._conn is None:
            log.warning("事件 %s 未送达（npc=%s）：C# 未连接", event, npc_id or "-")
            return False
        payload: Dict[str, Any] = {"type": "event", "event": event, "npc_id": npc_id}
        payload.update(fields)
        try:
            await self._conn.send(json.dumps(payload, ensure_ascii=False))
            if event != "text_delta" and log_setup.flag("trace_frames"):
                log.debug("→ event %s npc=%s", event, npc_id or "-")
            return True
        except Exception as e:
            log.warning("事件 %s 发送失败（npc=%s）：%s", event, npc_id or "-", e)
            return False


class ChatHub:
    """玩家消息 / NPC 主动开口编排：
    player_message → 驱动 DialogueAgent → 推 step/text_delta/npc_reply；
    npc_initiative → 以伪 user 意图驱动 NPC 主动开口 → 推带 initiative 标记的 npc_reply。

    兜底语义（失败时给玩家回音，账本不动）：
    - 回合异常 → 推一条带 error=True 的 npc_reply（友好文案，供 UI 显示系统提示）；
    - session 账本不落 assistant：失败回合保持 _turn 已闭合的 turn/end:error，不注入剧情文案
      （模型下一轮看到的是事实性错误账，而不是被包装过的回复）。

    并发控制（调度职责在编排层）：
    - per-agent 锁：同一 NPC 的回合绝对串行（session/phase 不踩踏）；不同 NPC 互不阻塞（并行）。
    - 全局并发闸 _turn_slots：同时跑回合数上限，防 LLM 并发调用爆炸（对应 config.concurrency）。
    - 顺序恒为「先全局闸、再 per-agent 锁」，无死锁路径（排队等闸的人不占锁）。

    各司其职：只做「收事件 → 驱动 agent → 收尾回复」，不碰协议细节（用 WsServer）、
    不碰 Agent 内部（用 AgentLoop 公开 get/create + DialogueAgent 公开 send/run/phase + 观察者钩子）。
    """

    # 失败兜底文案（只进 event 帧给 UI；绝不进 session 账本）
    FALLBACK_TEXT = "（传音法阵一时没接上你的话。稍候片刻，再唤我一声便是。）"

    # 新相识风味（首回合注入，plugin 源：进账本模型可见、history 投影滤除 UI 不可见）
    ACQUAINTANCE_NOTE = "（系统：这是你们之间的第一段传音，此前你们并无对话往来。）"

    # 玩家忙碌时的本回合约束（同样以 plugin 消息追加在尾部；见 handle_initiative 的 busy 变体）
    BUSY_NOTE = (
        "（系统：玩家此刻正忙——正在查看界面 / 战斗中 / 有窗口未关。"
        "本轮你只能说话，或做只读查询（看人、看世界）；"
        "**不要发起任何行动**：邀约、论道、双修、赠予、索取、传送、建立关系等一律留到玩家空闲时再说。"
        "若确有必要，可以在话里提一句，等玩家回应。）"
    )

    # 诊断"只说话、一个工具都不调"约束（配合 _diag_initiative_force.txt 的 no_tools=1）：
    # 用途是**观测**（横幅/红点/纯传音形态），不是玩法约束——所以比 BUSY_NOTE 更硬，
    # 且执行层会把**只读工具也一并拦下**（busy 只拦动作，见 speech_only_turn 对比 no_tools_turn）。
    SPEECH_ONLY_NOTE = (
        "（系统：【调试场景】本轮请**只说话**，不要调用任何工具——连只读查询（看人/看世界/看榜单）也不要。"
        "直接以你的口吻把想说的话说完即可。）"
    )

    def __init__(
        self,
        loop: Any,
        ws: WsServer,
        max_concurrent_turns: int = 2,
        min_initiative_interval: float = 30.0,
        config_service: Any = None,
        prompt_service: Any = None,
        contact_service: Any = None,
    ):
        self.loop = loop
        self.ws = ws
        # 全局并发闸与 per-agent 锁注册表（谁驱动回合，谁管并发）
        self.max_concurrent_turns = max(1, max_concurrent_turns)
        self._turn_slots = asyncio.Semaphore(self.max_concurrent_turns)
        self._agent_locks: Dict[str, asyncio.Lock] = {}
        # NPC 主动开口全局节流（秒）：防 C# 侧冷却失效，双保险之一
        self.min_initiative_interval = max(0.0, min_initiative_interval)
        self._last_initiative_ts = 0.0
        # 关窗销毁的「待销毁」标记：dispose_agent 到达时该 NPC 回合在跑 → 只记名（RPC 立即返回），
        # 回合收尾（handle_message/handle_initiative 的 finally）统一冻结销毁（不落盘，语义）；open_chat 撤销标记。
        self._dispose_pending: set = set()
        # 手动压缩（/compact）进行中标记：防连发 /compact 重复排队压缩（同 NPC 串行锁会放行第二个，
        # 这里直接快速失败给提示）
        self._compacting: set = set()
        # 配置/提示词/通讯录服务（DI，可测；装配层注入，未注入时对应 RPC 明确报错）
        # - config_service 需有 get_config() / set_config(partial)（含 LlmRouter 热换编排）
        # - prompt_service 需有 list_prompts() / read_prompt(rel) / write_prompt(rel, text) / create_persona(npc_id)
        # - contact_service 需有 list_contacts() / add_contact(npc_id) / remove_contact(npc_id) / list_sessions()
        self._config_service = config_service
        self._prompt_service = prompt_service
        self._contact_service = contact_service

    def _lock_for(self, npc_id: str) -> asyncio.Lock:
        """取（或惰性建）该 NPC 的回合锁。单线程事件循环下 setdefault 原子，无需额外保护。"""
        return self._agent_locks.setdefault(npc_id, asyncio.Lock())

    async def _run_turn(self, agent: Any, npc_id: str, text: str, source: Optional[Dict[str, Any]] = None,
                        post_note: str = "", images: Optional[List[Dict[str, Any]]] = None) -> str:
        """驱动一次完整回合（与事件类型无关，玩家消息与主动开口共用）：
        挂钩子 → 投信 → 跑到空闲 → 取最终回复。调用方负责已持锁。

        post_note（09-12）：投信之后、跑之前，在**尾部追加一条 plugin 消息**（形状同
        `_inject_acquaintance_if_new`：surfaceOp=append → 模型可见、UI 不可见、不触发回合）。
        用途：玩家忙碌时的"只许言语+只读"约束 —— 与 L1 运行期上下文同一套机制，
        **不改工具表**（工具表恒定 → 不破坏上游前缀缓存，见 README 附录二 G.5）。

        images（09-13）：图片附件（`[{name,url,...}]`）。**只在本回合喂给模型，不进历史**——
        账本里只有 C# 拼好的 `[图片：xx]` 占位文本，图像字节放在 agent 的内存侧表，
        回合收口即清空（落点见 `DialogueAgent._attach_turn_images`）。"""
        async def _on_step(info):
            await self.ws.send_event("step", npc_id=npc_id, **info)

        async def _on_token(tok):
            await self.ws.send_event("text_delta", npc_id=npc_id, text=tok, part="body")

        async def _on_reasoning(tok):
            await self.ws.send_event("text_delta", npc_id=npc_id, text=tok, part="think")

        agent.on_step = _on_step
        agent.on_token = _on_token
        agent.on_reasoning = _on_reasoning
        try:
            with log_setup.stage("turn"):
                agent.send(text, source=source, images=images)
                if post_note:
                    self._append_plugin_note(agent, "ctx-initiative-busy", post_note)
                await agent.run_until_idle()
                return self._collect_reply(agent)
        finally:
            # 统计推送（DSH projection 帧对齐）：回合结束即推全量快照，
            # 成功/失败都推（失败步计时缺失，快照自然缺对应分项，不污染平均）。
            # 纯数据帧，不含 UI 逻辑；C# 只管存与显示。
            try:
                snap = agent.snapshot_stats() if hasattr(agent, "snapshot_stats") else {}
                await self.ws.send_event("stats_update", npc_id=npc_id, **(snap or {}))
            except Exception:
                pass
            # 归还观察者钩子：避免连接/回合结束后残留引用
            agent.on_step = None
            agent.on_token = None
            agent.on_reasoning = None

    async def handle_message(self, npc_id: str, text: str,
                             images: Optional[List[Dict[str, Any]]] = None) -> None:
        """一次玩家消息的完整回合。

        玩家消息绝不丢弃：先排全局闸（LLM 并发槽），再取该 NPC 的锁（同 NPC 串行），
        不同 NPC 的回合之间互不阻塞（并行）。
        """
        # 图片附件：`[{name,url,...}]`，url 是 data URL。**只进当回合、不进历史** ——
        # 落点见 DialogueAgent.send(images=) / _attach_turn_images。
        # 「只发图不发字」已放开：C# 侧会给纯图消息补 `[图片：xx]` 占位，正常路径
        # text 本就非空；这里放宽判据是防御性的——任何客户端都不该被**静默丢弃**。
        if not npc_id or (not text and not images):
            return
        # /compact 兜底拦截：C# 新端会走专用 compact 事件（handle_compact），不会到这里；
        # 旧 DLL / chat_cli 等未经专用通道的客户端输入 /compact 时在此拦下，不进 LLM 回合。
        # 反馈经 npc_reply(error=true) → C# 渲染为系统提示行并复位 busy。
        #
        # 命令判据必须先剥掉 C# 拼的游戏时间戳 ：新 C# 给玩家消息加的
        #   `[1年1月] ` 前缀是在**进 WS 之前**拼的，C# 自己拦 `/compact` 时还没有前缀，
        #   所以游戏内路径不受影响；但兜底通道一旦收到带前缀的串（旧客户端/自测/将来新增
        #   的注入点），`"[1年1月] /compact"` 用裸判据永远匹配不上，会被当普通文本喂给模型
        #   → 玩家以为要压缩、实际烧了一个回合。故此处先规范化再判定。
        if _strip_time_stamp(text).strip().lower() == "/compact":
            await self._handle_compact_fallback(npc_id)
            return
        # 全局并发闸的排队等待必须看得见：槽满时消息会静默挂起，
        # 这正是"发出去半天没反应"的典型成因（诊断结论）。
        _t_queue = monotonic()
        async with self._turn_slots:                # ① 全局并发上限（防 LLM 并发爆炸）
            _waited = monotonic() - _t_queue
            if _waited > 1.0:
                log.warning("npc=%s 排队 %.1fs 才取得 LLM 并发槽（上限 %s；其余回合占用中）",
                            npc_id, _waited, self.max_concurrent_turns)
            async with self._lock_for(npc_id):      # ② 该 NPC 回合锁（同 NPC 串行）
                with log_setup.turn_scope(npc=npc_id):
                    try:
                        agent = self.loop.get(npc_id) or self.loop.create(npc_id)
                        self._inject_acquaintance_if_new(agent)
                        try:
                            log.info("回合开始（玩家消息 %d 字%s）", len(text),
                                     "，图 %d 张" % len(images) if images else "")
                            reply = await self._run_turn(agent, npc_id, text, images=images)
                            is_fallback = False
                        except Exception:
                            # 兜底：玩家必须有回音。只推 event（error 标记），不落 assistant——
                            # 失败回合账本已由 _turn 闭合为 turn/end:error，模型下轮看的是事实而非包装文案。
                            # 此 traceback = 已捕获异常的完整堆栈（下一条消息照常可发起），不是进程崩溃。
                            log.exception("回合异常：已兜底回复（服务继续运行，账本不落 assistant）")
                            reply = self.FALLBACK_TEXT
                            is_fallback = True
                        if reply:
                            log.info("回合收口：回复 %d 字%s", len(reply), "（兜底文案）" if is_fallback else "")
                            # `error` 必须是真布尔，**不能**用 `is_fallback or None`：后者在成功回合
                            # 会发出 `"error": null`，而 C# 侧惯用 `payload["error"]?.Value<bool>()`
                            # 读它 —— 键存在但值为 JSON null 时 `?.` 不短路，`Value<bool>()` 直接抛
                            # InvalidCastException。09-13 该异常在 `ChatPresenter.OnReplyEvent` 的
                            # try 之外炸掉，跳过 finally 里的忙标复位 → "对话完了还说回合进行中"。
                            await self.ws.send_event(
                                "npc_reply", npc_id=npc_id, text=reply,
                                error=bool(is_fallback),
                            )
                        else:
                            log.warning("回合收口但无回复文本：未发送 npc_reply（C# 侧 busy 可能不复位）")
                    finally:
                        # 关窗销毁：玩家在回合进行中关窗（dispose_agent 记了名），回合毕即落盘销毁
                        self._flush_dispose_pending(npc_id)

    # ---------- 手动压缩（/compact） ----------

    async def handle_compact(self, npc_id: str) -> None:
        """手动压缩入口（C# 专用通道 event=compact，玩家输入 /compact 触发）。

        不进对话历史（控制指令）、不触发 LLM 对话回合；压缩本身含一次摘要 LLM 调用。
        只取该 NPC 回合锁（不占全局 LLM 并发槽——压缩不该挤占其他 NPC 的对话回合）。
        结果经 compact_result 事件回推，C# 用它更新「正在压缩中」提示行。

        **必须回推**：C# 的「正在压缩中」提示行只在收到 compact_result 时才被替换，
        所以本方法任何分支都要发结果——包括缺 npc_id 的异常分支（曾经这里静默 return，
        导致提示行永远停在「正在压缩中」，且一行日志都没有）。
        """
        if not npc_id:
            log.warning("/compact 事件缺少 npc_id：无法执行压缩，但仍回推结果以免 UI 卡在「正在压缩中」")
            await self.ws.send_event("compact_result", npc_id="", ok=False,
                                     text="压缩失败：请求缺少 NPC 标识（请关开对话窗后重试）")
            return
        log.info("/compact 收到：开始手动压缩")
        with log_setup.turn_scope(npc=npc_id):
            with log_setup.stage("compact.manual"):     # 超 slow_ms 会自动告警，卡住当场可见
                ok, text = await self._run_manual_compact(npc_id)
                log.info("/compact 结束：ok=%s 反馈=%s", ok, text)
        await self.ws.send_event("compact_result", npc_id=npc_id, ok=ok, text=text)

    async def _handle_compact_fallback(self, npc_id: str) -> None:
        """/compact 兜底通道（未经专用 compact 事件的客户端）：结果走 npc_reply(error=true)
        → C# 渲染系统提示行并复位 busy（老 DLL 未发专用事件时 SetBusy(true) 已被本地调用）。"""
        if not npc_id:
            return
        ok, text = await self._run_manual_compact(npc_id)
        await self.ws.send_event("npc_reply", npc_id=npc_id, text=text, error=True)

    async def _run_manual_compact(self, npc_id: str) -> tuple:
        """/compact 公共执行体：占坑防重入 → 取回合锁 → 调 agent.compact_now() → 组反馈文案。
        返回 (ok, text)；绝不抛异常（异常在此兜成失败文案，调用方只管回推）。"""
        if npc_id in self._compacting:
            # 反复出现这条 = 上一次压缩卡住了（占坑未释放）。这是「UI 一直显示压缩中」的典型指纹。
            log.warning("压缩重入被拒（npc=%s 仍在压缩中）——若反复出现说明上一次压缩卡住了", npc_id)
            return False, "正在压缩中，请稍候"
        self._compacting.add(npc_id)
        try:
            _t_lock = monotonic()
            async with self._lock_for(npc_id):      # 与对话回合互斥（压缩不动 turn 账，但防并行改历史）
                _waited = monotonic() - _t_lock
                if _waited > 1.0:
                    log.warning("压缩等待回合锁 %.1fs 才拿到（该 NPC 有回合在跑？）", _waited)
                agent = self.loop.get(npc_id) or self.loop.create(npc_id)
                self._inject_acquaintance_if_new(agent)
                log.info("压缩前置检查：agent.phase=%s", agent.phase.get("kind"))
                return await self._compact_agent(agent)
        except Exception:
            log.exception("/compact 处理失败（npc=%s）", npc_id)
            return False, "压缩失败（服务异常），对话历史保持原状"
        finally:
            self._compacting.discard(npc_id)

    async def _compact_agent(self, agent: Any) -> tuple:
        """对单个 agent 执行手动压缩并组玩家可读反馈。返回 (ok, text)。"""
        if getattr(agent, "compactor", None) is None:
            log.warning("压缩被拒：compaction 未配置（agent.compactor 为 None）")
            return False, "压缩功能未启用（compaction 未配置）"
        if agent.phase["kind"] != "idle":
            log.info("压缩被拒：回合进行中（phase=%s）", agent.phase["kind"])
            return False, "当前回合进行中，结束后再试"
        before = self._context_pressure(agent)
        log.info("压缩开始：上下文约 %d tokens，开始调用压缩器（含一次摘要 LLM 调用，可能较慢）", before)
        try:
            result = await agent.compact_now()
        except RuntimeError:
            # DialogueAgent.compact_now 的 idle 闸：锁内理论到不了，防御性兜底
            log.warning("压缩被拒：compact_now 报非 idle（防御性分支）")
            return False, "当前回合进行中，结束后再试"
        after = self._context_pressure(agent)
        if result:
            saved = max(0, before - after)
            log.info("压缩成功：%d → %d tokens（释放 %d）range=%s", before, after, saved,
                     result.get("range"))
            return True, f"压缩完成：上下文约 {before} → {after} tokens（释放 {saved}），早段对话已合并为纪要"
        log.info("压缩未执行：无可用切点或摘要失败（上下文仍为 %d tokens，历史保持原状）", after)
        return False, "没有需要压缩的内容（对话尚短或摘要未能生成），历史保持原状"

    @staticmethod
    def _context_pressure(agent: Any) -> int:
        """当前会话上下文压力估算（与 Compressor.maybe 同口径：header + 全部消息）。"""
        try:
            header = agent.session.request_header()
            msgs = agent.session.derive_messages()
            return meter.estimate_header(header) + sum(meter.estimate_message(m) for m in msgs)
        except Exception:
            return 0

    async def handle_initiative(self, npc_id: str, intent: str, reason: str = "", text: str = "",
                                consented: bool = False, debug: bool = False, busy: bool = False,
                                speech_only: bool = False, speaker: str = "") -> None:
        """NPC 主动开口回合：C# 触发检测发来「何时/对谁/什么意图」，这里驱动
        NPC 以自身口吻主动开场（伪 user 意图 + 正常 turn，复用被动回合全部机制）。

        game_drama 变体（原生剧情窗「AI 对话」按钮）：C# DramaAiOption 点击发来 intent=game_drama
        + text=剧情原文 + speaker=本页那句是谁说的（`npc`/`player`/空=判不出）——玩家主动点击非事件
        轰炸，跳过全局节流；原文**按 speaker 归属**包装成舞台指令，NPC 顺着剧情以自身人设润色开场
        （设计文档 README.md 附录二 G（功能设计底稿）；归属判据见 APPENDIX G.1）。

        consented 变体（当面同意放行）：同格当面确认窗玩家点了「同意」，C# 才发来触发——
        玩家主动放行同样跳过全局节流（否则点击与生成之间的间隔会被节流吞掉，NPC 永不开口）。
        设计文档 README.md 附录二 G（功能设计底稿）。

        debug 变体（诊断强制触发，09-12）：C# 侧 `_diag_initiative_force.txt` 哨兵打开时
        （NpcInitiativeMonitor.TryTriggerForced）发出的触发——C# 已放宽日节拍/概率/冷却三闸，
        这里同步豁免全局节流，否则 min_interval（默认 300s）会把强制节拍削成"看着像没生效"。
        仅在事件帧带 `debug:true` 时成立，source 落账带 debug 标记（可审计、不影响正常触发）。

        busy 变体（玩家忙碌，09-12）：C# 侧状态闸判定"玩家正忙"（战斗中/有窗口或模态遮罩/
        我们自己的确认窗或 AI 行动挂起）时，**只禁动作、不禁言语**——NPC 照常开口（异地走未读+横幅，
        同格也不弹确认窗而直接传音），但本回合工具表里**拿掉 5 个动作工具**
        （social_relation/movement/world_ai_action/economy_item/item_acquire），只留 3 个只读
        （inspect_unit/search_units/query_world），并由执行层兜底拦历史残留调用。
        这是用户 09-12 拍板的两级语义：闸门管"能不能打扰"，不管"能不能说话"。

        快速失败（锁外）：该 NPC 有回合在跑（phase != idle）则静默丢弃——
        主动开口是可跳过事件，不排队、不积压（同 NPC 场景下锁外读 phase 无竞态，
        锁保证同 NPC 串行，phase 在排队期间不会变）。
        """
        if not npc_id or not intent:
            return
        agent = self.loop.get(npc_id) or self.loop.create(npc_id)
        self._inject_acquaintance_if_new(agent)
        if agent.phase["kind"] != "idle":
            # 诊断强制触发（debug）打个日志：否则"每 20s 触发一次但聊天窗没反应"会被误读成链路坏了，
            # 实际是这一闸（该 NPC 上一回合还没跑完 → 主动开口不排队、错过就丢）。正常触发不打，免噪。
            if debug:
                log.info("诊断强制触发被丢弃：该 NPC 有回合在跑（npc=%s intent=%s）", npc_id, intent)
            await self._notify_consented_dropped(npc_id, consented, "busy")
            return  # 忙守卫（快速失败）：有回合在跑 → 错过就丢
        async with self._turn_slots:                # 全局并发上限
            async with self._lock_for(npc_id):      # 该 NPC 回合锁
                try:
                    # 防御复查：锁内再确认 idle（不变量；锁已保证串行，防未来绕过锁的驱动路径）
                    if agent.phase["kind"] != "idle":
                        if debug:
                            log.info("诊断强制触发被丢弃：锁内复查该 NPC 仍忙（npc=%s）", npc_id)
                        await self._notify_consented_dropped(npc_id, consented, "busy")
                        return
                    now = monotonic()
                    player_initiated = intent == "game_drama" or consented or debug
                    if not player_initiated and now - self._last_initiative_ts < self.min_initiative_interval:
                        log.debug("主动开口被全局节流丢弃（距上次 %.1fs < %.1fs）npc=%s",
                                  now - self._last_initiative_ts, self.min_initiative_interval, npc_id)
                        return  # 全局节流：短于最小间隔，防事件轰炸（玩家主动发起不受此限）
                    self._last_initiative_ts = now

                    # 意图 → 伪 user 文案：括号舞台指令，模型以 NPC 口吻主动开场
                    if intent == "game_drama":
                        text = format_game_drama_message(text, speaker)
                        source = {"kind": "initiative", "intent": intent, "reason": ""}
                    else:
                        text = format_initiative_message(intent, reason)
                        source = {"kind": "initiative", "intent": intent, "reason": reason or "",
                                  **({"consented": True} if consented else {}),
                                  **({"debug": True} if debug else {}),
                                  **({"busy": True} if busy else {}),
                                  **({"speech_only": True} if speech_only else {})}

                    # 玩家忙碌：**只禁动作，不禁言语**。
                    # 约束方式 = 像 L1 运行期上下文那样，在本回合**尾部追加一条消息**
                    # （不改工具表 —— 工具表恒定才不会破坏上游前缀缓存，且与"自主交互本质
                    #  就是模拟用户发一条消息"一致）；执行层另有兜底（speech_only_turn）。
                    busy_note = ""
                    if busy:
                        busy_note = self.BUSY_NOTE
                        try:
                            agent.speech_only_turn = True   # 执行层兜底：防历史残留/幻觉硬调动作
                        except Exception:
                            pass
                    if speech_only:
                        # 诊断变体：连只读也禁（no_tools_turn 的执行层拦截比 speech_only_turn 更硬）
                        busy_note = (busy_note + "\n" + self.SPEECH_ONLY_NOTE) if busy_note else self.SPEECH_ONLY_NOTE
                        try:
                            agent.no_tools_turn = True
                        except Exception:
                            pass

                    try:
                        with log_setup.turn_scope(npc=npc_id):
                            tags = []
                            if debug:
                                tags.append("诊断强制")
                            if busy:
                                tags.append("玩家忙碌：仅言语+只读")
                            if speech_only:
                                tags.append("诊断：只说话（一个工具都不调）")
                            if consented:
                                tags.append("玩家放行")
                            log.info("主动开口回合开始（intent=%s%s）", intent,
                                     ("，" + "，".join(tags)) if tags else "")
                            reply = await self._run_turn(agent, npc_id, text, source=source,
                                                         post_note=busy_note)
                            log.info("主动开口回合收口：回复 %d 字", len(reply or ""))
                        if reply:
                            await self.ws.send_event(
                                "npc_reply", npc_id=npc_id, text=reply,
                                initiative=True, intent=intent, reason=reason or "",
                            )
                    finally:
                        if busy:
                            try:
                                agent.speech_only_turn = False
                            except Exception:
                                pass
                        if speech_only:
                            try:
                                agent.no_tools_turn = False
                            except Exception:
                                pass
                except Exception:
                    # 主动开口回合异常：不推兜底文案（NPC 没主动说话就是最好的失败），
                    # 只落日志——回合账本已由 _turn 闭合成 turn/end:error，服务绝不被拖死。
                    log.exception("主动开口回合异常（静默丢弃，服务继续）")
                finally:
                    # 关窗销毁：回合中关窗记名的，回合毕即落盘销毁（与 handle_message 同规约）
                    self._flush_dispose_pending(npc_id)

    async def _notify_consented_dropped(self, npc_id: str, consented: bool, why: str) -> None:
        """玩家点过「同意」但这次主动开口被丢弃（该 NPC 正有回合在跑）→ 给 UI 一个回音。

        为什么只对 consented 发：那是**玩家在等回应**的场合（C# 同意后立刻开窗 + 显示
        "对方正在斟酌…"）。若静默丢弃，占位提示会一直挂着、聊天窗毫无动静，看着像卡死。
        自动触发的丢弃照旧静默（错过就丢是设计语义，不该打扰玩家）。
        帧形状与正常收口一致（npc_reply + error）→ C# 走既有失败兜底：撤半截气泡 + 系统提示。
        """
        if not consented:
            return
        text = "（对方此刻正忙着别的事，没有立刻回应——稍后再找他吧）"
        log.info("同意放行的主动开口被丢弃（%s）：npc=%s → 已回执 UI", why, npc_id)
        try:
            await self.ws.send_event("npc_reply", npc_id=npc_id, text=text,
                                     initiative=True, error=True)
        except Exception:
            log.warning("丢弃回执发送失败（npc=%s）", npc_id, exc_info=True)

    def _append_plugin_note(self, agent: Any, note_id: str, text: str) -> None:
        """尾部追加一条 plugin 消息（模型可见、UI 不可见、不触发回合）。

        形状与 `_inject_acquaintance_if_new` 同源：直接 append 不进筐（不占 next-turn、
        不触发新回合），`surfaceOp=append` 进 Surface 让模型看到，`source.kind=plugin`
        被 history.project_ui_history 滤除（聊天窗不显示这条内部约束）。
        失败静默——约束件不拖垮回合（另有执行层兜底 DialogueAgent.speech_only_turn）。
        """
        try:
            agent.session.append(
                "user/message",
                {
                    "role": "user",
                    "content": [{"type": "text", "text": text}],
                    "id": note_id,
                    "source": {"kind": "plugin", "plugin": "@python-harness/initiative"},
                },
                {"surfaceOp": "append"},
            )
        except Exception:
            log.warning("plugin 备注追加失败（%s）", note_id, exc_info=True)

    def _inject_acquaintance_if_new(self, agent: Any) -> None:
        """新相识风味：全新 session（账本无 turn/start）在首回合前补一条 plugin 内部事件。

        直接 append 不进筐：不触发回合、不占 next-turn；带 surfaceOp=append 进 Surface
        （模型可见），history.project_ui_history 对 plugin 源滤除（UI 不可见）。
        agent 已在名册则走内存 append（账本与活体一致）；失败静默——风味件不拖垮回合。
        """
        try:
            session = agent.session
            if any(ev.get("type") == "turn/start" for ev in session.log):
                return  # 已有对话史：只在真正第一次开口时注入
            session.append(
                "user/message",
                {
                    "role": "user",
                    "content": [{"type": "text", "text": self.ACQUAINTANCE_NOTE}],
                    "id": "ctx-acquaintance",
                    "source": {"kind": "plugin", "plugin": "@python-harness/contacts"},
                },
                {"surfaceOp": "append"},
            )
        except Exception:
            pass

    # ---------- 关窗销毁（open_chat / dispose_agent 生命周期） ----------

    def _flush_dispose_pending(self, npc_id: str) -> None:
        """回合收尾统一冻结：玩家在回合进行中关窗（dispose_agent 只记了名），此刻回合已毕
        （收尾点在 per-agent 锁内、phase 已 idle），转冻结舱。同步函数：检查到冻结之间无
        await，与 dispose_agent 的直接冻结路径在事件循环上天然互斥；dispose 幂等（无名即空操作）。"""
        if npc_id not in self._dispose_pending:
            return
        agent = self.loop.get(npc_id)
        if agent is None:
            self._dispose_pending.discard(npc_id)
            return
        if agent.phase.get("kind") != "idle":
            return  # 防御：仍在跑则保留标记，留待下一次收尾
        self._dispose_pending.discard(npc_id)
        self.loop.dispose(npc_id)
        log.info("关窗冻结：回合结束转冻结舱（不写盘，固化跟随游戏存档）")

    @staticmethod
    def _parse_limit(p: Dict[str, Any]) -> int:
        """`limit` 参数语义（09-13 统一：open_chat / get_history / delete_history 共用）。

        - 缺省 / 非法 → **10**（老行为，不动 `chat_cli` 与既有测试的默认预期）；
        - 显式 `0` 或负数 → **0 = 全部**（用户要求"历史要看全部的"；C# 侧固定传 0，
          0 由 `project_ui_history` 解释成"不截断"，本层**不做二次钳制**——免得又出现
          "客户端以为要了全部、服务端偷偷截断"的静默不一致）。

        代价提示：全部加载 = 一帧回全部 items（实机 26 回合 ≈ 63KB / 109 行，可接受）；
        超长会话会线性变大——真到那一步再加"上拉加载更早"的分页，别在这里悄悄设上限。
        """
        try:
            raw = p.get("limit", 10)
            n = int(raw) if raw is not None else 10
        except Exception:
            return 10
        return n if n > 0 else 0

    @staticmethod
    def _history_out(agent: Any, npc_id: str, limit: int) -> Dict[str, Any]:
        """session → UI 历史投影（npc_id 由本层补齐，投影函数不管它）。"""
        out = project_ui_history(agent.session, max_turns=limit)
        out["npc_id"] = npc_id
        return out

    async def handle_request(self, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """C#→Python 方向的读请求分发（UI 的「问」）。

        生命周期 RPC（开窗=激活 / 关窗=销毁）：
        - open_chat：get or create 激活活体 + 返回历史投影（唯一会「建活体」的请求路径）；
        - dispose_agent：idle 即销（落盘留账），回合在跑则记入待销集合、回合收尾统一销。
        其余（get_history / 通讯录 / 配置 / 提示词）保持只读：不建活体、不动回合。
        运维 RPC：shutdown（配置 UI「需重启」由 C# 触发，应答后退出进程）。
        成功返回 data dict（进 response.data）；失败/未知 method 抛异常
        （WsServer 传输层转 ok:false 错误帧）。
        """
        # ---- 运维 RPC：优雅关停（配置 UI「需重启」场景由 C# 触发）----
        # 应答帧由传输层在本 handler 返回后发出，故先应答、再延迟退出；flush 后 os._exit
        # 保证 stdio 日志不丢且进程必死（不依赖连接优雅关闭）。C# 侧负责随后重启本进程。
        if method == "shutdown":
            import os as _os
            import sys as _sys

            def _exit_after_flush() -> None:
                try:
                    _sys.stdout.flush()
                except Exception:
                    pass
                try:
                    _sys.stderr.flush()
                except Exception:
                    pass
                try:
                    log_setup.shutdown_logging()   # 收尾前 flush 并关闭日志 handler（防丢尾巴日志）
                except Exception:
                    pass
                _os._exit(0)

            # 重启 = 世界延续（游戏没读档）：按存档语义把未固化增量先固化防丢
            try:
                n = self.loop.flush_all()
                log.info("shutdown 前固化 %d 个 agent 会话", n)
            except Exception as flush_ex:
                log.warning("shutdown 前 flush 失败: %s", flush_ex)
            asyncio.get_running_loop().call_later(0.6, _exit_after_flush)
            log.info("shutdown 已受理：0.6s 后退出进程")
            return {"bye": True}
        p = params or {}
        if method == "open_chat":
            # 开窗激活：对话 UI 打开 = agent 激活（get or create，磁盘账本自动 resume），
            # 并直接返回历史投影（C# 开窗一次往返拿全历史）。顺带撤销待销毁标记——
            # 「关窗 → 立刻重开」竞态下重开即取消销毁。
            npc_id = str(p.get("npc_id", ""))
            if not npc_id:
                raise ValueError("open_chat 缺少 npc_id")
            limit = self._parse_limit(p)
            self._dispose_pending.discard(npc_id)
            agent = self.loop.get(npc_id) or self.loop.create(npc_id)
            out = self._history_out(agent, npc_id, limit)
            log.info("open_chat：npc=%s 激活（历史回放 %d 项）", npc_id, len(out.get("items") or []))
            # 开窗基线：历史 + 全量统计一次给足，C# 首屏即有数（DSH opening snapshot 对齐）
            try:
                if hasattr(agent, "snapshot_stats"):
                    out["stats"] = agent.snapshot_stats()
            except Exception:
                pass
            return out
        if method == "get_history":
            npc_id = str(p.get("npc_id", ""))
            if not npc_id:
                raise ValueError("get_history 缺少 npc_id")
            limit = self._parse_limit(p)
            agent = self.loop.get(npc_id)  # 只查不建：读历史不产生新活体
            if agent is None:
                return {"npc_id": npc_id, "items": [], "complete_turns": 0}
            out = self._history_out(agent, npc_id, limit)
            try:
                if hasattr(agent, "snapshot_stats"):
                    out["stats"] = agent.snapshot_stats()
            except Exception:
                pass
            return out
        if method in ("preview_delete_history", "delete_history"):
            # 软删除：用户在对话 UI 里选回合删除。账本只追加 history/delete
            # 注记，双投影过滤（surface 剔成员 / UI 投影跳 seq）；随存档固化、读档回滚。
            # 两阶段：preview 给确认弹窗（含「将同时删除折叠记忆」），execute 才落账。
            npc_id = str(p.get("npc_id", ""))
            if not npc_id:
                raise ValueError(f"{method} 缺少 npc_id")
            if npc_id in self._compacting:
                raise DeleteError("压缩进行中，暂时不能删除历史")
            agent = self.loop.get(npc_id)  # 只查不建：删除不产生新活体
            if agent is None:
                raise ValueError("对话未激活（请先打开对话窗）")
            turns = [int(t) for t in (p.get("turns") or [])]
            if agent.phase.get("kind") != "idle" or agent.session.has_open_turn():
                raise DeleteError("回合进行中，暂时不能删除历史")
            plan = plan_delete(agent.session, turns)
            if not plan["ok"]:
                # fail-closed：模拟新目录配对不合法 → 拒绝，账本不动
                return {"ok": False, "reason": plan["reason"], "npc_id": npc_id,
                        "turns": plan["turns"]}
            if method == "preview_delete_history":
                return {"ok": True, "npc_id": npc_id, "preview": plan}
            append_delete(agent.session, plan, reason=str(p.get("reason") or "user"))
            log.info("delete_history：npc=%s turns=%s ranges=%s drop=%s keep=%s",
                     npc_id, plan["turns"], plan["ranges"], plan["drop_seqs"], plan["keep_seqs"])
            limit = self._parse_limit(p)
            out = self._history_out(agent, npc_id, limit)   # 删完直接回新历史，C# 一次往返重放
            return {"ok": True, "npc_id": npc_id, "deleted": plan, "history": out}
        if method == "dispose_agent":
            # 关窗销毁：对话 UI 关闭 = 活体销毁（落盘留账，账本即真相）。
            # 回合在跑则只记名（本 RPC 立即返回，不等待不占接收循环），回合收尾统一销。
            npc_id = str(p.get("npc_id", ""))
            if not npc_id:
                raise ValueError("dispose_agent 缺少 npc_id")
            agent = self.loop.get(npc_id)
            if agent is None:
                log.debug("dispose_agent：npc=%s 无活体（幂等忽略）", npc_id)
                return {"disposed": False, "reason": "unknown_npc"}
            if agent.phase.get("kind") != "idle":
                log.info("dispose_agent：npc=%s 回合进行中，记名待收尾后冻结", npc_id)
                self._dispose_pending.add(npc_id)
                return {"disposed": False, "reason": "busy", "pending": True}
            self._dispose_pending.discard(npc_id)  # 直接销毁路径顺带清标记（防残留空记名）
            self.loop.dispose(npc_id)
            log.info("dispose_agent：npc=%s 立即冻结（转 _frozen 舱，不写盘）", npc_id)
            return {"disposed": True}
        if method == "save_happened":
            # 存档事件（存档语义）：C# 钩子 EGameType.SaveData 触发（手动/自动存档都发）。
            # 把所有活/冻 agent 的当前历史固化进 jsonl（全量重写、幂等；空账跳过）。
            # ：顺带再断言一次存档身份（幂等）——万一 load_happened 那条丢了（旧 DLL /
            # 连不上），这里也不会把增量写进上一个存档的目录。
            self._apply_world(p, why="save_happened")
            n = self.loop.flush_all()
            log.info("save_happened：固化 %d 个 agent 会话（存档 %s）", n, self._world_tag())
            return {"flushed": n}
        if method == "load_happened":
            # 进世界事件（存档语义）：C# 钩子 EGameType.IntoWorld 触发（读档/新建皆走）。
            # 世界（重）载 = 未固化增量全部丢弃 + 所有活/冻 agent 销毁；之后 open_chat
            # 从 jsonl（上次 flush 状态）恢复，即「读档 = 回到存档时刻」。
            # ：先切命名空间（world_id 随帧到达）再丢弃——顺序反了会把旧存档的增量
            # 写进新存档目录。
            self._apply_world(p, why="load_happened")
            n = self.loop.discard_all()
            log.info("load_happened：世界重载，丢弃 %d 个 agent（未固化增量作废，存档 %s）",
                     n, self._world_tag())
            return {"discarded": n}
        # ---- 通讯录 RPC（路由到 contact_service；手动好友名单 + 会话索引）----
        if method == "list_contacts":
            self._require_contact_service()
            return {"contacts": self._contact_service.list_contacts()}
        if method == "list_sessions":
            self._require_contact_service()
            return {"sessions": self._contact_service.list_sessions()}
        if method == "add_contact":
            self._require_contact_service()
            npc_id = str(p.get("npc_id", ""))
            if not npc_id.strip():
                raise ValueError("add_contact 缺少 npc_id")
            return self._contact_service.add_contact(npc_id)
        if method == "remove_contact":
            self._require_contact_service()
            npc_id = str(p.get("npc_id", ""))
            if not npc_id.strip():
                raise ValueError("remove_contact 缺少 npc_id")
            return self._contact_service.remove_contact(npc_id)
        # ---- 配置 RPC（路由到 config_service；自身不碰文件、不碰 LlmRouter）----
        if method == "get_config":
            self._require_config_service()
            return self._config_service.get_config()
        if method == "set_config":
            self._require_config_service()
            payload = p.get("config") if isinstance(p.get("config"), dict) else p
            return self._config_service.set_config(payload)
        if method == "test_llm":
            # 「测试连接」：拿表单当前值真调一次模型。**必须 await** —— probe 是协程，
            # 漏了 await 会把 coroutine 对象当结果原样序列化回 C#（那边只会看到一个空对象）。
            # 实现见 ConfigService.test_llm / llm/probe.py。
            self._require_config_service()
            payload = p.get("config") if isinstance(p.get("config"), dict) else p
            return await self._config_service.test_llm(payload)
        # ---- 提示词 RPC（路由到 prompt_service；文件即真相，热改下个 turn 生效）----
        if method == "list_prompts":
            self._require_prompt_service()
            npc_id = str(p.get("npc_id", "")).strip() if p.get("npc_id") else None
            # prompt_files.list_prompts 已支持 npc_id 过滤；其它实现（mock）兼容无参调用
            try:
                groups = self._prompt_service.list_prompts(npc_id=npc_id) if npc_id else self._prompt_service.list_prompts()
            except TypeError:
                groups = self._prompt_service.list_prompts()
            return {"groups": groups}
        if method == "read_prompt":
            self._require_prompt_service()
            rel = str(p.get("path", ""))
            return {"path": rel, "text": self._prompt_service.read_prompt(rel)}
        if method == "write_prompt":
            self._require_prompt_service()
            rel = str(p.get("path", ""))
            result = self._prompt_service.write_prompt(rel, str(p.get("text", "")))
            result["effective"] = "hot"  # 文件即真相：SystemPrompt 每步自检，下个 turn 生效
            return result
        if method in ("write_prompts", "bulk_write_prompts"):
            self._require_prompt_service()
            files = p.get("files")
            if not isinstance(files, dict):
                raise ValueError("write_prompts 需要 {files: {path: text}}")
            # 兼容部分实现大小写/嵌套
            if hasattr(self._prompt_service, "write_prompts"):
                result = self._prompt_service.write_prompts(files)
            else:
                # 兜底：逐个调 write_prompt（理论不进）
                saved = []
                for rel, text in files.items():
                    self._prompt_service.write_prompt(str(rel), str(text))
                    saved.append(str(rel))
                result = {"saved": saved, "count": len(saved), "effective": "hot"}
            if "effective" not in result:
                result["effective"] = "hot"
            return result
        if method == "create_persona":
            self._require_prompt_service()
            npc_id = str(p.get("npc_id", ""))
            result = self._prompt_service.create_persona(npc_id, from_default=bool(p.get("from_default", True)))
            result["effective"] = "hot"
            return result
        if method == "delete_prompt":
            # 删除新增提示词文件：只许删 personas/{npc}.txt / traits/{npc}.json /
            # compaction/{npc}.md 这类用户新增件；sections 与 default/_suffix 系统件 fail-closed 拒绝。
            self._require_prompt_service()
            rel = str(p.get("path", ""))
            if hasattr(self._prompt_service, "delete_prompt"):
                result = self._prompt_service.delete_prompt(rel)
            else:   # 兼容旧 mock 服务：直接按不支持处理（fail-closed，不静默成功）
                raise ValueError("提示词服务不支持删除（delete_prompt 未实现）")
            result["effective"] = "hot"
            return result
        raise ValueError(f"unknown method: {method}")

    def _require_config_service(self):
        if self._config_service is None:
            raise ValueError("配置服务未注入（装配层未提供 config_service）")

    def _require_prompt_service(self):
        if self._prompt_service is None:
            raise ValueError("提示词服务未注入（装配层未提供 prompt_service）")

    def _require_contact_service(self):
        if self._contact_service is None:
            raise ValueError("通讯录服务未注入（装配层未提供 contact_service）")

    # ---------- 存档命名空间（通讯录/对话历史/最近索引按存档隔离）----------

    def _apply_world(self, params: Dict[str, Any], why: str) -> None:
        """把 C# 带来的存档身份（world_id = 玩家 unitID / player_name）应用到
        AgentLoop 与 ContactsService。两处必须同时切——会话落盘目录与通讯录目录同源。

        缺 world_id（旧 C# / 手工 RPC）→ 沿用当前命名空间并 WARNING：宁可保持现状，
        也不能猜一个 id 把数据写到别处。幂等：同 id 重复调用只是空转。
        """
        p = params or {}
        wid = str(p.get("world_id") or "").strip()
        pnm = str(p.get("player_name") or "").strip() or None
        if not wid:
            log.warning("%s 未携带 world_id（旧 C#？）→ 沿用当前存档命名空间 %s",
                        why, self._world_tag())
            return
        changed_loop = self.loop.set_world(wid, pnm)
        changed_contacts = False
        if self._contact_service is not None and hasattr(self._contact_service, "set_world"):
            try:
                changed_contacts = self._contact_service.set_world(wid, pnm)
            except Exception as e:              # 通讯录切不动不该阻断存档固化/丢弃
                log.warning("%s：通讯录命名空间切换失败（%s）", why, e)
        if changed_loop or changed_contacts:
            log.info("%s：存档命名空间 → %s（目录 %s）", why, self._world_tag(),
                     self.loop.sessions_root)

    def _world_tag(self) -> str:
        """日志用存档标签：`缪嘉歆/FJLlLl` 或 `(未定存档·扁平)`。"""
        wid = self.loop.world_id
        if not wid:
            return "(未定存档·扁平)"
        name = self.loop.player_name
        return f"{name}/{wid}" if name else wid

    @staticmethod
    def _collect_reply(agent: Any) -> str:
        """取最后一条 assistant 文本（收尾帧 = 最终完整回复）。
        纯 tool-call 的 assistant 无文本，跳过继续向前找。
        兜底剥离内嵌 <think> 标签（reasoning_content 模型的 content 本身干净）——
        思考内容已在过程中经 think 事件展示，收尾帧只给正文。"""
        for ev in reversed(agent.session.log):
            if ev.get("type") != "assistant/message":
                continue
            msg = (ev.get("data") or {}).get("message", {}) or {}
            for b in msg.get("content", []) or []:
                if isinstance(b, dict) and b.get("type") == "text" and b.get("text"):
                    _, body = _think.split_think(b["text"])
                    return body.strip()
        return ""
