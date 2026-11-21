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
            ★ text 以游戏时间戳 `[N年M月D日] `（读不到日时 `[N年M月] `）开头（09-13，C# 在进
              WS 前拼好）——本模块**只识别不重写**（唯一用处：命令判据先剥前缀，见
              _strip_time_stamp）；正文带前缀进账本，NPC 靠它感知时间跨度。
              玩家**屏幕上看不到**这个前缀：C# 渲染口按同一语义剥显示层戳（10-22，
              `BubbleTextCore.StripLeadStamp`），与本模块的正则互为镜像。
  event     {"type":"event","event":"npc_initiative","npc_id":str,"intent":str,"reason":str?,
             "consented"?:bool,"debug"?:bool,"busy"?:bool,"speech_only"?:bool,
             "npc_unit_id"?:str,"text"?:str,"speaker"?:str,
             "lines"?:[{seq,drama_id,speaker,name,text}],
             "verdict"?:bool} C# → Python
            ★ npc_unit_id（09-28 补）：说话 NPC 的**精确 unitID**，由 C# 从**真身**读出
              （日节拍/诊断强制 = `FireOne` 的 `wub`；当面同意的确认窗 = 同一个真身；
              剧情窗「对话」 = 本页归属 NPC；排队动作办妥回执 = C# 按存档登记表）。
              为什么必须有：主动传音是**第三条会话入口**，既不经过单聊 `open_chat`
              （那里才 `set_unit_id`）也不经过群聊入场 ⇒ 不给它，本会话 `agent.unit_id`
              恒空、工具调用不注入 `initiator_id`、C# 只能按名猜。真机 09-28 实锤：
              `Resolve('管星贤') → 未命中` ⇒ 本回合 5 个动作工具全部
              `未找到发起方 管星贤`。带上它则每个工具调用都走 ②unitID精确，永不按名猜。
              缺项/空串 = 老 C#（或触发时取不到真身）⇒ 一字不改的旧行为（回落按名）。
            ★ 后四项只有 intent=game_drama 会带（原生剧情窗那枚「对话」按钮）：
              text=本页屏幕成品句 / speaker=本页那句谁说的（npc/player/空）/
              lines=**整段相遇**里还没投过的增量（≥2 句才带；包装见
              `initiative.format_game_drama_message`，攒段的判据在 C# `DramaSegmentCore`）/
              verdict=**当前这一页是战斗处置页**（页号 20403/20404，配表事实；翻到后页不带）→
              包装追加"处置页说明"（别调工具去执行页面上的献物/求饶，见 `initiative._VERDICT_NOTE`）
            ★ action_done（09-24）—— 排队动作的办妥回执，复用本事件但**语义不同**：
              reason=一句结果摘要（C# `ActionCatalog.DescribeAction` + 结局，如
              "邀约姜萌：已完成"）。该 NPC 正忙 → 投成 next-step 增量（下一步顺口交代）；
              空闲 → 走正常主动回合。包装见 `initiative.format_action_done_message`，
              排队判据/状态机在 C# `drama/DeferredActionCore.cs`。
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
from typing import Any, Awaitable, Callable, Dict, Optional, Tuple

from .. import log_setup

log = log_setup.get_logger(__name__)

from ..agent.initiative import (format_initiative_message, format_game_drama_message,  # noqa: E402
                                format_action_done_message)
from ..agent.dialogue_agent import TurnAborted  # noqa: E402  （读档打断回合：不是故障，别兜底成"失败回复"）
from ..session.history import project_ui_history  # noqa: E402
from ..session.history_ops import DeleteError, append_delete, plan_delete  # noqa: E402
from ..llm import think as _think  # noqa: E402
from ..room.room_flow import NOTICE_PLUGIN, RoomFlow
from ..session.stats import usage_delta as _usage_delta, usage_of as _usage_of  # noqa: E402  （群聊编排器；本模块只注入依赖、不解释内容）
from ..compaction import meter  # noqa: E402  （手动 /compact 的上下文压力前后对比）
from ..compaction import summary as compact_summary  # noqa: E402  （压缩纪要：推送/修改的唯一读写口）


# C# 侧给玩家消息拼的游戏时间戳前缀，形如 `[1年1月3日] `；日历读不到**日**时 CnDate 退化成
# `[1年1月] `。注意本正则曾经只认无日形态（C# 给戳加日之后一度没跟上——带日的行首戳剥不掉、
# 命令判据静默失效；幸而 C# 把 /compact 拦在拼戳之前，玩家侧没炸过）。两侧语义由
# tests/test_game_time_stamp.py 钉死，C# 显示层镜像件是 `BubbleTextCore.StripLeadStamp`。
# 单一来源是 C# 的 `NpcInitiativeMonitor.CurrentTimeLabel()` + `UnitSnapshot.CnDate`；
# 这里只**识别**它（命令判据要剥掉它），绝不重新生成——两边各拼一份必然漂移。
_TIME_STAMP_RE = re.compile(r"^\[\d+年\d+月(?:\d+日)?\]\s*")


def _strip_time_stamp(text: str) -> str:
    """剥掉行首的游戏时间戳前缀；没有则原样返回。

    **只用于命令判据**（首尾空白 + 大小写归一那一类），正文一律带着前缀进账本 ——
    "这句话是哪年哪月说的"正是 NPC 需要长期记得的信息，绝不能在落账前洗掉。
    """
    return _TIME_STAMP_RE.sub("", text or "", count=1)


def _unit_id_map(raw: object) -> Dict[str, str]:
    """清洗 C# 上行的 `{npc_id: unitID}`（群成员的**精确身份**）。

    宽进严出：不是 dict、值为空、键为空 → 一律**安静丢掉那一条**，绝不报错。
    理由与 `contacts_store._clean_unit_id` 同款 —— "没提供"是正常情形
    （老 DLL 不传、老群没存过、建群时 C# 手上恰好没有那个人的真身），
    为一个可选字段让整次建群失败是本末倒置。

    为什么用 dict 而不是与 `members` 平行的数组：**顺序无关**。平行数组一旦两端
    对不齐（过滤掉了空成员、C# 侧多塞了一个），会把甲的身份安到乙头上 —— 而且
    不报错、不缺字段，只是画错脸、把别人的档案端给模型（"重名事故"同款症状）。
    dict 让"谁的身份"由键自己带着走，不存在错位这种失败模式。
    """
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, str] = {}
    for k, v in raw.items():
        name = str(k or "").strip()
        uid = str(v or "").strip()
        if name and uid:
            out[name] = uid
    return out


def _identity_unknown(params: object) -> bool:
    """帧是否**明确宣告**"世界事件发生了，但读不到玩家单位"（10-18 三态协议的第三态）。

    与"整个 `world_id` 键都没有"刻意区分，两者含义不同、处理也不同：
      · 键都没有  = 旧 DLL（没这能力）/ 主菜单里本来就没有身份 → **沿用现状**（原设计，且
        它是"Python 中途重启不许丢档"的救命绳，见 `world_hello` 分支）；
      · `identity:"unknown"` = C# 在**世界事件**（`load_happened`）里明说"我知道世界变了，
        但我读不到新身份" → 那正是**旧身份作废的判据**（见 `ChatHub._invalidate_world`）。

    宽容解析（大小写/空白无关，非字符串一律当没宣告）——这条判据宁可不触发也别误触发。
    """
    if not isinstance(params, dict):
        return False
    return str(params.get("identity") or "").strip().lower() == "unknown"


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
        # C# 长等槽位的心跳计数（req_id → 收到过几条 `type:"pending"`）。
        # 切磋应战之后那条 RPC 要等一整场战斗（时长不可知），靠心跳滑动 deadline，
        # 而不是把超时放大到小时级 —— 见 `_await_response`。
        # 存**计数**不存时刻：`monotonic()` 与 `loop.time()` 虽同源，跨时钟做算术仍是白送的坑。
        self._beat_seq: Dict[str, int] = {}
        self._req_seq = 0
        self._message_handler: Optional[Callable[..., Awaitable[None]]] = None
        self._initiative_handler: Optional[Callable[[str, str, str], Awaitable[None]]] = None
        self._request_handler: Optional[Callable[[str, Dict[str, Any]], Awaitable[Dict[str, Any]]]] = None
        self._compact_handler: Optional[Callable[[str], Awaitable[None]]] = None
        self._room_message_handler: Optional[Callable[..., Awaitable[None]]] = None
        self._room_skip_handler: Optional[Callable[[str], Awaitable[None]]] = None
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

    def register_initiative_handler(self, handler: Callable[[str, str, str, str, bool, bool, bool, bool, str, list, bool, bool, str], Awaitable[None]]):
        """注入 npc_initiative 处理者（由 ChatHub 提供）：NPC 主动开口事件
        （末位参数 = `npc_unit_id`：说话 NPC 的精确 unitID，C# 从真身带上来，
        见模块头 `npc_initiative` 那段的 ★；空串 = 老 C# ⇒ 回落按名解析）
        （含 game_drama 剧情原文变体、consented 同意放行变体、debug 诊断强制变体、
        busy 玩家忙碌变体——忙碌时只许言语+只读，禁动作工具；
        speech_only 诊断变体——连只读也禁，一个工具都不调，见 `_diag_initiative_force.txt` 的 no_tools=1；
        speaker 剧情窗变体——本页那句是谁说的，npc / player / 空=判不出，只有 game_drama 会带；
        lines 剧情窗整段变体——一次相遇里还没投过的多页原文，同样只有 game_drama 会带；
        action 剧情窗"进行中"变体——点「对话」那一刻游戏里那个动作还在跑，只有 game_drama 会带；
        verdict 处置页变体——点「对话」那一刻**当前页就是战斗处置页**（20403/20404，C# 判页号），
        包装追加"别调工具执行页面上的献物/求饶"的说明，同样只有 game_drama 会带）。"""
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

    def register_room_message_handler(self, handler: Callable[..., Awaitable[None]]):
        """注入 room_message 处理者（由 ChatHub 提供）：玩家在**群里**说了一句话。

        为什么是 event 而不是 RPC：「玩家说了一句话」在既有架构里一律是事件
        （`player_message` / `npc_initiative`）—— 发送方不等回执，结果通过
        `text_delta` / `room_say` 等事件流式回推。RPC 留给"问一次答一次"的读请求
        （`open_room` / `list_rooms` / `get_room_history`）。
        （`docs/archive/2026-09-17-群聊编排/设计定稿.md` §6.1 把 room_message 列在 RPC 下是笔误，已回写修正。）

        handler(room_id, text, speak_first=None)；speak_first = 玩家在 UI 上点名的头像。
        """
        self._room_message_handler = handler

    def register_room_skip_handler(self, handler: Callable[[str], Awaitable[None]]):
        """注入 room_skip 处理者（由 ChatHub 提供）：玩家按了群窗的「跳过」。

        语义 = **这一轮玩家不置一词**，按轮转顺序让 NPC 自己接下去（不是"打断本轮"，
        也不是"替玩家说一句"）。与 `room_message` 一样是 event 而不是 RPC，理由同上；
        但它**刻意不复用 room_message**：
          · 空文本的 `room_message` 会在 `handle_room_message` 开头被丢弃（连一帧都不回）
            ⇒ 玩家按下去**毫无反应**；
          · 就算放行，`run_message` 会先给玩家落一条**空发言**进 `Room.log` ——
            全群可见、进所有成员记忆、群窗上留一个空气泡。

        handler(room_id)。
        """
        self._room_skip_handler = handler

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
                                  speech_only: bool = False, speaker: str = "", lines=None,
                                  action: bool = False, verdict: bool = False, npc_unit_id: str = "",
                                  same_grid: Optional[bool] = None):
            try:
                await self._initiative_handler(npc_id, intent, reason, text, consented, debug, busy,
                                               speech_only, speaker, lines, action, verdict, npc_unit_id,
                                               same_grid)
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

        async def _run_room_message(room_id: str, text: str, speak_first: Any = None):
            # 群聊一轮：含 K 个完整回合（每个都要调 LLM），必须异步转推，
            # 否则接收循环会被整轮堵死 —— 连"玩家插话"都进不来。
            try:
                await self._room_message_handler(room_id, text, speak_first)
            except Exception:
                log.exception("room_message 事件处理失败（群 %s）", room_id)

        async def _run_room_skip(room_id: str):
            # 同上：一轮 = K 个完整回合，必须异步转推（它会持 room_lock 跑十几秒）。
            try:
                await self._room_skip_handler(room_id)
            except Exception:
                log.exception("room_skip 事件处理失败（群 %s）", room_id)

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
                    self._beat_seq.pop(str(req_id), None)
                    if fut is not None and not fut.done():
                        fut.set_result(msg)
                    else:
                        log.debug("收到无主 response（req_id=%s，可能已超时/已弃）", req_id)
                elif t == "pending":
                    # C# 的"我还在等"心跳（长等槽位，如切磋跨一场战斗）。只记时刻、不填 future：
                    # 真正等的那次 request 会在 `_await_response` 里据此把 deadline 往后推。
                    rid = str(msg.get("req_id"))
                    if rid in self._pending:
                        self._beat_seq[rid] = self._beat_seq.get(rid, 0) + 1
                        if log_setup.flag("trace_frames"):
                            log.debug("← pending 心跳 req_id=%s phase=%s（deadline 续期）",
                                      rid, msg.get("phase", ""))
                    else:
                        log.debug("收到无主 pending 心跳（req_id=%s，请求已收口）", rid)
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
                    _lines = msg.get("lines")
                    # same_grid（10-16 可选）：本次触发时是否同格。缺键/非布尔 → None，
                    # 语义是"这一侧不知道方位"（老 C#）⇒ 文案回落到与从前逐字相同的那一版。
                    _sg = msg.get("same_grid")
                    asyncio.get_running_loop().create_task(
                        _run_initiative(str(msg.get("npc_id", "")), str(msg.get("intent", "")), str(msg.get("reason", "")),
                                        str(msg.get("text", "") or ""), bool(msg.get("consented", False)),
                                        bool(msg.get("debug", False)), bool(msg.get("busy", False)),
                                        bool(msg.get("speech_only", False)),
                                        str(msg.get("speaker", "") or ""),
                                        _lines if isinstance(_lines, list) else None,
                                        bool(msg.get("action", False)),
                                        bool(msg.get("verdict", False)),
                                        str(msg.get("npc_unit_id", "") or ""),
                                        _sg if isinstance(_sg, bool) else None)
                    )
                elif t == "event" and msg.get("event") == "room_message" and self._room_message_handler is not None:
                    if log_setup.flag("trace_frames"):
                        log.debug("← event room_message room=%s len=%s speak_first=%s",
                                  msg.get("room_id"), len(str(msg.get("text", "") or "")),
                                  msg.get("speak_first"))
                    asyncio.get_running_loop().create_task(
                        _run_room_message(str(msg.get("room_id", "")), str(msg.get("text", "") or ""),
                                          msg.get("speak_first") if isinstance(msg.get("speak_first"), list) else None)
                    )
                elif t == "event" and msg.get("event") == "room_skip" and self._room_skip_handler is not None:
                    if log_setup.flag("trace_frames"):
                        log.debug("← event room_skip room=%s", msg.get("room_id"))
                    asyncio.get_running_loop().create_task(
                        _run_room_skip(str(msg.get("room_id", "")))
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
            # ★ 断连必须**立刻**把所有挂起的 RPC 判死（11-20 修）★
            #
            # 不做的后果（真机 8 次，每次干等 120 秒）：C# 那条连接没了，它手里没回的那条
            # `get_context` 的应答**永远不会回来** —— req_id 是连接内配对，新连接的 req_id
            # 从 1 重来。而 `_pending` 里的 Future 没人 set、没人 cancel，`_await_response`
            # 就只能一路等到 `limit`（发行 config 120s）才超时。
            # 日志形状：`rpc.get_context 已运行 30.0s 未结束` → `C# 连接已断开` → 又过 90 秒
            # 才 `RPC get_context 超时（120.0s）`。中间那 90 秒是**纯空转**。
            #
            # 为什么 C# 侧早就这么做了：`WsClient.RunLoop` 的 finally 明写
            # 「Python 侧 future 已随连接死亡，补发无主」并清掉自己的 `_deferred`。
            # 两边对同一件事的处理此前**不对称** —— 这里补上。
            #
            # 失败文案要能一眼看出是"连接没了"而不是"对端慢"：调用方会把 error 带给模型/玩家。
            _drop_pending_on_disconnect()
            log.warning("C# 连接已断开（等待其自动重连；重连周期由 C# 侧决定）")

    def _drop_pending_on_disconnect(self) -> None:
        """把此刻所有挂起的 RPC 立刻判死（断连出口专用）。

        为什么是 set_exception 而不是 cancel：`cancel()` 让 `wait_for` 抛
        `CancelledError`，而它在 3.8+ 是 `BaseException` 的子类 —— `request()` 里
        `except asyncio.TimeoutError` / `except ConnectionError` 都接不住，会直接冒到
        调用方，把"连接断了"升级成"回合异常"（整轮作废，比原来的超时更糟）。
        `set_exception` 配 `request()` 里那个 `except ConnectionError` 才是成对的。

        用 `ConnectionError` 而不是 `TimeoutError`：两者在日志与统计里必须能分开 ——
        「连接没了」和「等太久」是两种完全不同的故障。

        已经 done 的 future 跳过（心跳槽可能刚被正常应答填过）。
        """
        if not self._pending:
            return
        n = len(self._pending)
        for rid, fut in list(self._pending.items()):
            try:
                if fut is not None and not fut.done():
                    fut.set_exception(ConnectionError("C# 连接已断开"))
                    # ★ 立刻取一次，消掉 asyncio 的 "exception was never retrieved" ★
                    # 正常情况下 `request()` 的 except 会取走它。但有两个竞态下没人取：
                    #   ① 那条 RPC 刚超时返回、pop 与本次遍历交错；
                    #   ② 调用方协程已被取消，不会再 await 这个 future。
                    # 那时 asyncio 会在 GC 阶段往 stderr 打一条 warning —— 打包后它会混进
                    # 日志，看起来像"断连时抛了个没人管的异常"，排障时是纯噪声。
                    try:
                        fut.exception()
                    except Exception:  # noqa: BLE001
                        pass
            except Exception:  # noqa: BLE001 —— 清理路径绝不再抛
                pass
            self._pending.pop(rid, None)
            self._beat_seq.pop(rid, None)
        log.warning("断连清理：%d 条挂起的 RPC 已判死（不再空等到各自超时）", n)

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
                resp = await self._await_response(fut, req_id, limit, method)
        except asyncio.TimeoutError:
            self._pending.pop(req_id, None)
            self._beat_seq.pop(req_id, None)
            log.warning("RPC %s 超时（%.1fs，等 C# 主线程无应答）——回合将按「工具失败」继续，"
                        "模型可能重试同一工具而表现为卡顿", method, monotonic() - t0)
            return {"success": False, "error": "请求超时"}
        except ConnectionError:
            # 断连时 `_drop_pending_on_disconnect` 会把挂起的 future 判死（11-20）。
            # 必须在这里接住：本方法对调用方的契约是「失败也返回 dict，不抛」——
            # 让它冒上去会把"连接断了"变成"回合异常"，而后者更糟（整轮作废 vs 按工具失败继续）。
            self._pending.pop(req_id, None)
            self._beat_seq.pop(req_id, None)
            log.warning("RPC %s 因 C# 断连中止（%.1fs，不再空等到 %.0fs 上限）",
                        method, monotonic() - t0, limit)
            return {"success": False, "error": "C# 连接已断开"}
        ok = bool(resp.get("ok"))
        if ok:
            return {"success": True, "data": resp.get("data")}
        err = resp.get("error") or "未知错误"
        log.warning("RPC %s 返回失败（%.0fms）：%s", method, (monotonic() - t0) * 1000.0, err)
        return {"success": False, "error": err}

    # 心跳宽限（秒）：C# 每 30s 报一次"还在等"，这里给三个节拍再加零头。
    PENDING_GRACE = 90.0
    # 绝对上限（秒，09-23 加）：心跳能把 deadline 一再往后推，但**不许越过"发起后这么多秒"**。
    # 跨语言保险：现在靠 C# 侧自己的硬顶兜底（`BattleOutcome.HardCeilingSeconds=600`，到点它必然
    # Resolve，最后一条心跳再撑一个宽限期 ≈ 690s < 900s）。万一将来某条链忘了硬顶、或心跳因 bug
    # 无限续期，Python 也不会永远挂在一个不会来的结果上。config 若有意配得更长，以 config 为准
    # （见 `_await_response` 里 `max(limit, 本值)`）。
    PENDING_TOTAL_CAP = 900.0

    async def _await_response(self, fut: "asyncio.Future", req_id: str, limit: float,
                              method: str = "") -> Dict[str, Any]:
        """等这条 RPC 的 response，但**认 C# 的心跳**。

        为什么不能只把 `limit` 调大：切磋应战之后要等的是一整场战斗，时长由玩家决定。
        固定值小了会掐断正常等待 —— 更糟的是掐断之后 future 已被 pop，几分钟后真结果回来时
        **无人认领，唯一正确的那条被静默丢弃**（09-22 排查里就是这类"结果丢"的形状）；
        固定值大了，对端真失联（进程没了 / 连接断了 / 主线程卡死）时人要干等，
        而界面忙标 240s 就自愈了，两边还会打架。

        两条时间线（09-23 明确）：
          · **单次** `limit`（默认 5s，发行 config 120s）—— 无心跳时的最长等待；
          · **绝对上限** `PENDING_TOTAL_CAP` —— 心跳续期的总天花板（与 `limit` 取大者）。
        无心跳的槽位（等玩家点原生选项那些）靠的正是"90s 槽位 TTL < 120s RPC 超时"这条关系
        （见 C# `DramaGate.DefaultExpireSeconds` 的注释）：让**能拿到结局的那一侧先裁决**。

        分片等待必须过 `asyncio.shield`：直接 `wait_for(fut, 小片)` 超时会**取消** fut，
        等于把这次请求作废 —— 那是比不写更坏的错法。
        """
        loop = asyncio.get_running_loop()
        started = loop.time()
        hard = started + max(float(limit), self.PENDING_TOTAL_CAP)
        deadline = min(started + max(0.001, float(limit)), hard)
        seen = self._beat_seq.get(req_id, 0)
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                if deadline >= hard:
                    # 只可能是"心跳把时间用到了天花板"这条：明确留痕（否则会被误读成普通超时）
                    log.warning("RPC %s 到达绝对上限 %.0fs（心跳仍在，但不再等了）——"
                                "结果若在此前后回来，会因无人认领被丢弃", method, self.PENDING_TOTAL_CAP)
                raise asyncio.TimeoutError()
            try:
                return await asyncio.wait_for(asyncio.shield(fut), remaining)
            except asyncio.TimeoutError:
                cur = self._beat_seq.get(req_id, 0)
                if cur == seen:
                    raise           # 一个心跳都没多 = 对端真的没了
                seen = cur
                deadline = min(loop.time() + self.PENDING_GRACE, hard)
                log.debug("RPC %s 收到 C# 心跳（req_id=%s）→ deadline 续到 +%.0fs（天花板 %.0fs）",
                          method, req_id, self.PENDING_GRACE, self.PENDING_TOTAL_CAP)

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
    # ⚠ 这里必须与 `tools/schemas.py` 的 `ACTION_TOOLS` 口径一致（6 个）。漏写 `trade` 的代价是
    #   模型会以为"买卖不算行动"而在忙碌回合去成交 —— 执行层会拦，但它已经先对玩家许诺了。
    BUSY_NOTE = (
        "（系统：玩家此刻正忙——正在查看界面 / 战斗中 / 有窗口未关。"
        "本轮你只能说话，或做只读查询（看人、看世界）；"
        "**不要发起任何行动**：邀约、论道、双修、赠予、索取、传送、建立关系、买卖、组队等一律留到玩家空闲时再说。"
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
        max_speakers_per_round: int = 3,
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
        # 玩家手点的「对话」（intent=game_drama）在 NPC 正忙时的一次性信箱：
        # `{npc_id: {text, speaker, lines, at}}`（lines=整段增量，可空）。只存一条（新的盖旧的），
        # 到 `_drain_drama` 消费。
        # 为什么放 hub 而不是 agent 上：agent 会被 dispose/重建（换档、关窗），信箱跟着
        # agent 一起蒸发就等于没修；这里活过单个 agent，靠 TTL 自己过期。
        self._pending_drama: Dict[str, Dict[str, Any]] = {}
        # 配置/提示词/通讯录服务（DI，可测；装配层注入，未注入时对应 RPC 明确报错）
        # - config_service 需有 get_config() / set_config(partial)（含 LlmRouter 热换编排）
        # - prompt_service 需有 list_prompts() / read_prompt(rel) / write_prompt(rel, text) / create_persona(npc_id)
        #   （可选：delete_prompt(rel) / reset_prompt(rel) —— 缺失时对应 RPC 明确报"不支持"，不静默成功）
        # - contact_service 需有 list_contacts() / add_contact(npc_id) / remove_contact(npc_id) / list_sessions()
        #   / list_hidden() / hide_contact(npc_id, unit_id=None) / unhide_contact(npc_id)
        #   / list_known() / sync_known(items)（关系层快照，见 contacts_store 的 KNOWN_FILE）
        self._config_service = config_service
        self._prompt_service = prompt_service
        self._contact_service = contact_service
        # 存档世代号（10-18）：每次**真实**切换命名空间（含"作废回未定档"）+1，随每个档级回包
        # 回传 → C# 据此判废**跨档回包**（发出时还在上一个存档、回来时已经换档）。
        # 为什么需要它：回包过去不带任何身份，"旧档的名单"与"新档的名单"是两个**无法区分**的
        # 合法答案，谁最后到就信谁。判废在 C# 侧（`ContactDuty.WorldMatches`）。
        self._world_epoch = 0
        # 投递/调度/跑轮全在 `room_flow.RoomFlow`（它不 import 本模块，依赖由这里注入）。
        self._room_locks: Dict[str, asyncio.Lock] = {}
        # 落账短锁：与 room_lock 是两把不同的锁，各管一段（见 `_say_lock_for`）。
        self._say_locks: Dict[str, asyncio.Lock] = {}
        self.rooms = RoomFlow(
            loop,
            run_turn=self._room_turn,
            agent_lock=self._lock_for,
            room_lock=self._room_lock_for,
            say_lock=self._say_lock_for,
            turn_slot=lambda: self._turn_slots,
            emit=self._emit_room_event,
            # 一轮里最多几个人真的开口（config room.max_speakers_per_round，默认 3）。
            # 改配置的热生效入口是 `RoomFlow.set_max_speakers_per_round`（见 ConfigService ⑥）——
            # 跑轮时每轮现读 `RoomFlow.k`，所以不用重开群窗、也不用重启进程。
            max_speakers_per_round=max_speakers_per_round,
            load_room=self._load_room_data,
            # 解散时的落盘收尾（写 index + 删流水）。注入而不是让 RoomFlow import
            # persistence —— 与本类不 import 它的理由对称（可脱离磁盘单测）。
            delete_room=lambda rid: loop.retire_room(rid),
        )

    async def _emit_room_event(self, event: str, **kw: Any) -> None:
        """群聊事件回推。**在调用时才解析 `self.ws`** —— 构造期取 `self.ws.send_event`
        会因为测试里 `ws=None`（ChatHub 只用 RPC 路由、不接 WS）而直接崩在 __init__。
        """
        if self.ws is None:
            return
        await self.ws.send_event(event, **kw)

    def _load_room_data(self, room_id: str) -> Any:
        """群流水的**懒加载**回调（注入 `RoomFlow` 的 `load_room`）。

        读档时 `AgentLoop.load_rooms()` 只把**名录**读进内存（`log=[]`，理由见其
        docstring：列个群列表不该扫全部 jsonl）。于是「进群」这件事必须有人把流水补上 ——
        `RoomFlow._require` 会回调这里（它同时覆盖「内存完全没有」与「有名录没流水」）。

        与 `_emit_room_event` **同一个理由**，这里**调用时才解析 `self.loop`**：
        - `ChatHub(None, None)`（只做 RPC 路由的构造，见 `tests/test_prompt_delete.py`）
          在构造期取 `loop.load_room_log` 会直接崩在 `__init__`；
        - 另有 `_FakeLoop` 一类的替身根本没有这个方法 ⇒ 用 `getattr` 探测，
          探不到就退回「只认内存」的旧行为（不懒加载，也不报错）。
        """
        loader = getattr(self.loop, "load_room_log", None)
        if loader is None:
            return None
        return loader(room_id)

    def _room_lock_for(self, room_id: str) -> asyncio.Lock:
        """取（或惰性建）该房间的回合锁。

        锁序：**room_lock → turn_slot → agent_lock**（与 `handle_message` 的
        `turn_slot → agent_lock` 前后一致 ⇒ 无环、无死锁）。编排器持 room_lock 跨整轮，
        但每个发言者**各自取** turn_slot（不整轮霸占并发闸 ⇒ 其他 NPC 的单聊能插进来）。
        """
        return self._room_locks.setdefault(room_id, asyncio.Lock())

    def _say_lock_for(self, room_id: str) -> asyncio.Lock:
        """取（或惰性建）该房间的**落账短锁**（`RoomFlow._say_and_emit` 用）。

        与 `room_lock` 是两把不同的锁，职责不同：
        - `room_lock`：跨**整轮**，防两轮交错投递；
        - `say_lock`：只包住「`room.say` + `_emit_say`」这一对，钉住
          「**UI 帧到达顺序 === `Room.log` 的 seq 顺序**」。`Room.say` 本身同步原子
          （seq 不会撞号），但紧随其后的 `_emit_say` 有 `await` —— 两条发言各自的
          "落账 + 广播"交错，群窗上就会出现"后说的话显示在前"。

        锁序：只在 `room_lock` **之内**（`_run_round` 里落 NPC 的账）或**单独**
        （`run_message` 在 `room_lock` 之外落玩家的账）获取，**从不反向索要 room_lock**
        ⇒ 无环、无死锁。临界区是"一次同步 say + 一次 WS 写"，毫秒级 —— 所以即使
        玩家的插话要等正在跑的那一轮把这条 emit 做完，等待也可忽略，不会退化成
        "等整轮"。
        """
        return self._say_locks.setdefault(room_id, asyncio.Lock())

    async def _room_turn(self, agent: Any, npc_id: str, text: str, source: Optional[Dict[str, Any]],
                         scene: str = "", roster: Optional[List[str]] = None,
                         roster_ids: Optional[Dict[str, str]] = None) -> str:
        """`RoomFlow` 注入的回合驱动（适配到 `_run_turn` 的场合参数）。

        **不**在这里补 `_inject_acquaintance_if_new` —— 那是"玩家刚认识这个 NPC"的
        单聊风味件；群里第一次见面不该插一句只有它自己看得见的话（会让它对不上
        别人听到的内容）。新人设的摩擦留给 P2 的群聊规则段处理。

        `roster_ids` = `{名字 → unitID}`（房间身份表，见 `DialogueAgent.roster_ids`）。
        它**必须与 roster 一起传** —— 两者在 `set_roster` 里同一次写，分成两次调用就会漂移。
        """
        return await self._run_turn(agent, npc_id, text, source=source,
                                    scene=scene, roster=roster, room_id=scene or "",
                                    roster_ids=roster_ids)

    def _lock_for(self, npc_id: str) -> asyncio.Lock:
        """取（或惰性建）该 NPC 的回合锁。单线程事件循环下 setdefault 原子，无需额外保护。"""
        return self._agent_locks.setdefault(npc_id, asyncio.Lock())

    async def _run_turn(self, agent: Any, npc_id: str, text: str, source: Optional[Dict[str, Any]] = None,
                        post_note: str = "", images: Optional[List[Dict[str, Any]]] = None,
                        scene: str = "", roster: Any = None,
                        room_id: str = "",
                        roster_ids: Optional[Dict[str, str]] = None) -> str:
        """驱动一次完整回合（与事件类型无关，玩家消息/主动开口/群聊共用）：
        挂钩子 → 定场合 → 投信 → 跑到空闲 → 取最终回复。调用方负责已持锁。

        post_note（09-12）：投信之后、跑之前，在**尾部追加一条 plugin 消息**（形状同
        `_inject_acquaintance_if_new`：surfaceOp=append → 模型可见、UI 不可见、不触发回合）。
        用途：玩家忙碌时的"只许言语+只读"约束 —— 与 L1 运行期上下文同一套机制，
        **不改工具表**（工具表恒定 → 不破坏上游前缀缓存，见 README 附录二 G.5）。

        images（09-13 起支持；10-18 起**进上下文**）：`[{name,url,w,h,kb}]`，url 是 data URL。
        图像字节落存档图片仓、账本里只记 `ref`，于是后续每一回合都能重新把图挂给模型
        （09-13 的"只进当回合"已作废，见 `docs/archive/2026-10-18-图片进上下文.md`；
        落点见 `DialogueAgent._ingest_images` / `_inline_image_refs`）。

        scene / roster（群聊）：**场合标记的作用域是「回合」而不是「在群里」**。
        一个 NPC 可以同时属于多个群，而 `scene` 是单个字段 —— 若按"进群设、关窗复位"
        来做，两个群会互相覆盖；更要命的是玩家在群里开会时打开某个成员的 1:1 窗，
        那个单聊回合会带着群标签，在 1:1 窗里**静默**消失（回合照跑、模型照看，只有 UI 少东西）。
        所以：**设在这里、由调用方声明、跑完立刻复位**，且**在锁内**（调用方已持 agent 锁）。
        单聊路径不传 ⇒ 默认 `scene=""` / `roster=[]` ⇒ 与改动前逐字节相同。

        proposal notes（09-17）：群聊编排器排队的私信（`RoomFlow.drain_notices`）在开跑前注入
        为 next-step 的 plugin 消息（模型可见 / UI 不可见），与投递进来的增量同一步被领走。
        """
        # 流式帧带上 room_id：群窗据此把正文路由到正确的窗口 —— 光靠 npc_id 不够，
        # 一个 NPC 可能同时在两个群里（它确实都经历过），而两个群窗可能都开着。
        _extra = {"room_id": room_id} if room_id else {}
        # ★ 本群用量：**回合前后取差**（09-18）★
        # 成员 agent 同时服务单聊与群聊，它的累计 `tokenUsage` 是两条线之和；只有"这一趟
        # 多了多少"才是"群聊期间"的量。取差点选在这里而不是 RoomFlow：stats_update 是在
        # 本函数的 finally 里发的，**入账必须排在发帧之前**，否则那一帧报的是上一位的账
        # （实测：自己这一回合的量要等下一个成员发言才出现）。
        # 并发安全：群内回合在 `_one_speaker` 里持有该 agent 的锁，锁外的单聊回合不会混进来。
        _u0 = _usage_of(agent) if room_id else None

        async def _on_step(info):
            # 压缩事件走**独立事件名**（10-31）：它不是"回合里的一步"，而是一条记忆的产出。
            # 混进 step 会有两个后果：① 帧队列按 step 种类收/放，重演时会把折叠行画两遍；
            # ② C# 的 `ApplyStep` 是按 kind 分派的，多一种 kind 等于多一条没人认的支路。
            if (info or {}).get("kind") == "compact":
                payload = {k: v for k, v in info.items() if k != "kind"}
                await self.ws.send_event("compact_summary", npc_id=npc_id, **_extra, **payload)
                return
            await self.ws.send_event("step", npc_id=npc_id, **_extra, **info)

        async def _on_token(tok):
            await self.ws.send_event("text_delta", npc_id=npc_id, text=tok, part="body", **_extra)

        async def _on_reasoning(tok):
            await self.ws.send_event("text_delta", npc_id=npc_id, text=tok, part="think", **_extra)

        async def _on_wait_phase(phase, entering):
            """「现在在等什么」→ C# 忙标文案（11-21）。

            为什么走独立事件名而不是塞进 `step`：见 `DialogueAgent.on_wait_phase` 的注释
            —— 阶段是当下状态，不是账本事实；塞进 step 会被帧队列重演、并在账本里留垃圾。

            `since`（进入时刻）由 C# 自己记：它要显示"已等 N 秒"，而这个 N 必须**从玩家
            看到的那一刻**起算（Python 与 C# 的时钟起点差一次往返，几十毫秒，但更关键的是
            C# 可能因为窗口没开而晚收到——那时它该从收到那刻算，不是从 Python 发的那刻）。
            """
            await self.ws.send_event("wait_phase", npc_id=npc_id, **_extra,
                                     phase=phase, entering=bool(entering))

        agent.on_step = _on_step
        agent.on_token = _on_token
        agent.on_reasoning = _on_reasoning
        agent.on_wait_phase = _on_wait_phase
        # 场合标记：进 try 之前设、finally 里复位（异常路径也不能把群标签留在身上）
        agent.set_scene(scene)
        # 名单与**身份**同一次写（单入口）：分开写会出现"名单是新的、身份是上一轮的"，
        # 而那一回合的取数会挂到**别人**身上、名字看着却完全正常（比全空更糟）。
        agent.set_roster(roster or [], roster_ids)
        # 玩家身份 + 世界级人名映射表：**每回合刷新**（幂等）。放在这里而不是只在建 agent 时设，
        # 是为了让"换存档"这件事不可能留下陈旧值 —— 换档会 discard_all，但刷新一次几乎零成本，
        # 而陈旧的玩家 id / 陈旧的人名映射都是**静默错人**那一类。
        try:
            agent.set_identity_store(getattr(self.loop, "identity", None))
            agent.set_player(getattr(self.loop, "player_name", None),
                             getattr(self.loop, "player_unit_id", None))
        except Exception:
            log.warning("agent %s 刷新玩家身份/人名映射失败（本回合按名兜底）", npc_id)
        try:
            with log_setup.stage("turn"):
                agent.send(text, source=source, images=images)
                # 提案私信（群聊编排器排队；模型可见 / UI 不可见 —— 同 plugin 三语义）：
                # 在回合开跑前注入，与"投递进来的增量"同一步被领走（next-step 先于本条 text）。
                try:
                    for _note in self.rooms.drain_notices(npc_id):
                        agent.send(_note, target="next-step", wake=False,
                                   source={"kind": "plugin", "plugin": NOTICE_PLUGIN})
                except Exception:
                    log.warning("提案私信注入失败（回合照跑）", exc_info=True)
                if post_note:
                    self._append_plugin_note(agent, "ctx-initiative-busy", post_note)
                await agent.run_until_idle()
                return self._collect_reply(agent)
        finally:
            # 复位成单聊：★ `set_scene("")` 的调用点（群聊侧的另一个在 `RoomFlow.leave`）。
            # 漏了的话之后的单聊回合都带着群标签 ⇒ 在 1:1 窗里凭空消失。
            agent.set_scene("")
            agent.set_roster([])
            # 提案闸门与场合标记**同处**复位：同一个 agent 实例同时服务单聊与群聊，
            # 漏复位会让下一个单聊回合带着它去查一个跟它无关的提案表（见 `_make_gate`）。
            agent.tool_gate = None
            # 本群用量入账：**必须在下面发帧之前**（见函数头 `_u0` 那段说明）。
            # 失败/弃权的回合也照算与照记（那趟可能已经花了钱，"只算群内消耗"要如实）。
            if room_id:
                try:
                    _room = self.loop.room(room_id)
                    if _room is not None:
                        _room.add_group_usage(_usage_delta(_u0 or {}, _usage_of(agent)))
                        _room.add_group_usage({"turns": 1})
                except Exception:
                    log.warning("本群用量入账失败（本群那一行会少算这一笔）", exc_info=True)
            # 统计推送（DSH projection 帧对齐）：回合结束即推全量快照，
            # 成功/失败都推（失败步计时缺失，快照自然缺对应分项，不污染平均）。
            # 纯数据帧，不含 UI 逻辑；C# 只管存与显示。
            try:
                snap = agent.snapshot_stats() if hasattr(agent, "snapshot_stats") else {}
                # ★ 群聊另附 `roomUsage`（本群期间累计口径，09-18）★：群窗底部那一行报的是
                # **本群的花费**（输入/输出/缓存/缓存率），不是某个成员的账 —— 每人一份账本，
                # "各自的上下文/占用率"没有可加语义，所以群里不报上下文。
                # 成员名/上下文仍随 `snap` 一起发（老 DLL 靠它兜底显示，见 ChatWindow.SetStats）。
                if room_id:
                    try:
                        _room = self.loop.room(room_id)
                        if _room is not None:
                            snap = dict(snap or {})
                            snap["roomUsage"] = _room.group_usage_block()
                    except Exception:
                        log.warning("本群用量整形失败（本帧不带 roomUsage）", exc_info=True)
                # ★ 必须带 `_extra`（群聊 = room_id）★：C# 群窗的唯一准入判据是"room_id 相等"，
                # 而这个 finally 在 `set_scene("")` 之后跑、且原先漏了 `_extra` ⇒ 群聊里这一帧
                # **没有 room_id** ⇒ 被群窗丢弃 ⇒ 群窗底部统计条永远停在上一个窗口的残影
                # （用户 09-18 截图：那行一直是预制体的默认文本 "None"）。
                # 单聊 `_extra` 为空 ⇒ 与改动前逐字节相同。
                await self.ws.send_event("stats_update", npc_id=npc_id, **_extra, **(snap or {}))
            except Exception:
                pass
            # 归还观察者钩子：避免连接/回合结束后残留引用
            agent.on_step = None
            agent.on_token = None
            agent.on_reasoning = None
            agent.on_wait_phase = None
            # 玩家手点的「对话」在信箱里等这一刻（09-22）：本回合彻底收口后补投。
            # ★群聊回合不补★：`_run_round` 持 `room_lock` 串行跑成员，每个成员的回合收尾都会
            # 走到这里 —— 在那儿插一条单聊的求饶开口，等于把私下的话捅进群轮转。
            if not room_id:
                self._schedule_drama_drain(npc_id)

    async def handle_message(self, npc_id: str, text: str,
                             images: Optional[List[Dict[str, Any]]] = None) -> None:
        """一次玩家消息的完整回合。

        玩家消息绝不丢弃：先排全局闸（LLM 并发槽），再取该 NPC 的锁（同 NPC 串行），
        不同 NPC 的回合之间互不阻塞（并行）。
        """
        # 图片附件：`[{name,url,h,kb}]`，url 是 data URL。**进上下文**（10-18）：
        # 字节落存档图片仓、账本只记 ref，后续每轮重新挂图给模型 —— 落点见
        # DialogueAgent.send(images=) / _ingest_images / _inline_image_refs。
        # 「只发图不发字」已放开：C# 侧会给纯图消息补 `[图片：xx]` 占位，正常路径
        # text 本就非空；这里放宽判据是防御性的——任何客户端都不该被**静默丢弃**。
        # ★ 判据放宽之后 `text=None + 有图` 变成**可达**路径（旧判据下 `not None` 就早退）：
        #   WS 收帧永远是 str，但本方法是公开入口，程序化调用传 None 会在下面 `len(text)` 炸
        #   （被兜底 except 接住 → 玩家收到"回合失败"，图也白发了）。这里归一成空串。
        if text is None:
            text = ""
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
                        except TurnAborted as _ab:
                            # ★ 读档打断回合 ≠ 故障（11-20）★
                            # 这条路径以前会掉进下面的兜底：给玩家回一句「传音法阵一时没接上」，
                            # 而真相是世界重载了 —— 玩家读档那一刻正好在等快照，就白挨一句假故障。
                            # 不回话也不落 assistant：账本已由 `_turn` 的 finally 闭合为
                            # `turn/end:aborted`，下一次进世界从 jsonl 恢复的是存档时刻的状态。
                            log.info("回合被读档/取消中止：npc=%s（%s）——不兜底回话", npc_id, _ab.reason)
                            reply = ""
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
                            # 互动时间戳（10-21）：「这个 NPC 刚刚真的说了一句」——
                            # 通讯录「最近」排序的**权威**源（文件 mtime 是存档时刻，没有区分度，
                            # 见 `ContactsService` 文件头）。刻意放在 send_event **之前**：
                            # 帧一到 C# 就可能立刻开面板，先记才不会读到旧值。
                            self._touch_interaction(npc_id)
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

    # ---------- 群聊（Room） ----------

    async def handle_room_message(self, room_id: str, text: str,
                                  speak_first: Optional[List[str]] = None) -> None:
        """玩家在群里说了一句话（C# 事件 `room_message`）。

        与单聊 `handle_message` 的关系：**不取 agent 锁**，整轮串行由 `RoomFlow`
        的 room_lock 保证；每个发言者的 `(turn_slot, agent_lock)` 由编排器在
        `_one_speaker` 里各自取 —— 锁序与 `handle_message` 一致，无环。

        并发语义：
        - 玩家在自己这一轮还没跑完时又发一句 → 第二个 `room_message` 在 `room_lock`
          上排队（**不打断**本轮，本轮跑完再开新一轮，与 `docs/archive/2026-09-17-群聊编排/设计定稿.md` §4.4 一致）；
        - 玩家在群里开会时打开某个成员的 1:1 窗并发消息 → `handle_message` 不取
          room_lock，消息走 `next-step`，在该成员**当前回合的下一步**被领走
          （与既有「玩家插话」同一条路）。这条被产品上接受（§9.4）。
        """
        if not room_id or not str(text or "").strip():
            log.warning("room_message 缺少 room_id 或 text（已丢弃）：room=%r", room_id)
            return
        # ★ 群聊**不提供**手动压缩（`docs/archive/2026-09-17-群聊编排/设计定稿.md` §9.2.1 定稿）：压缩的单位是某个 NPC 的
        #   **一整份记忆**（`compact_now` 对整份 surface 选段、不做 scene 过滤），正确归属地
        #   是他的单聊窗。不拦的话这四个字会被当成一条**普通的群发言**：写进 `Room.log`、
        #   投递给所有 NPC（他们看到的是 "/compact" 这串命令文本），而压缩一次都没发生。
        #
        #   与单聊兜底同规约：先剥游戏时间戳（`_strip_time_stamp`）、再判定、大小写归一。
        #   反馈走 `room_state(phase="end", error=True, text=…)` —— **与"一轮异常"同一条
        #   通道**（C# 在终帧里复位忙碌态并把这句显示成系统提示行）。正常游戏路径在 C#
        #   侧就拦下了（本地提示、连气泡都不落），这里是**兜底**：旧 DLL / chat_cli /
        #   将来新增的注入点。
        if _strip_time_stamp(text).strip().lower() == "/compact":
            log.info("群聊 /compact 被拦下（room=%s）：多聊不提供手动压缩（§9.2.1）", room_id)
            try:
                await self.ws.send_event(
                    "room_state", room_id=room_id, phase="end", speaking=[], spoken=[],
                    error=True,
                    text="群聊不提供手动压缩：压缩作用于某个 NPC 的全部记忆，"
                         "请到他的单聊窗口操作")
            except Exception:
                pass
            return
        try:
            await self.rooms.run_message(room_id, text, speak_first=speak_first or ())
        except KeyError:
            # ★ 群不在了也**必须回一帧终帧**：`phase="end"` 是 C# 群窗复位忙碌态的**唯一**
            #   依据（§6.2），而 C# 是"先置忙再发消息"的 —— 只记日志不发帧的话，玩家会看到
            #   群里一直显示"正在输入"，最长要等 C# 的 240s 看门狗才消，且**一行解释都没有**。
            #   真机形态：群已被解散、但那个窗还开着，玩家又发了一句。
            log.warning("room_message 指向不存在的群（已丢弃）：room=%s", room_id)
            await self._fail_room_round(room_id, "这个群聊已经不存在了（可能刚被解散）。")
        except Exception:
            # 玩家在群里也必须看得见失败：回推一条 error 帧，否则群窗会一直显示"正在输入"。
            log.exception("群聊一轮异常（room=%s）", room_id)
            await self._fail_room_round(room_id, self.FALLBACK_TEXT)

    async def handle_room_skip(self, room_id: str) -> None:
        """玩家按了群窗的「跳过」（C# 事件 `room_skip`）：这一轮他不置一词，NPC 自己接下去。

        与 `handle_room_message` 的关系：**只少掉"玩家那句话"这一步**，其余逐字相同 ——
        同一条 `RoomFlow.run_round`（同一把 `room_lock`、同一个 `_run_round`）。
        两条入口的差别只有催话文案与"全员沉默时终帧写不写"（见 `room_flow` 的两个常量）。

        并发语义与 `handle_room_message` 完全一致：连点两次 = 第二个 `run_round` 在
        `room_lock` 上排队（不打断本轮）；玩家随后又发言，那句话照常立刻落账，其轮次排在本轮之后。
        C# 侧另有一道"忙态拒绝"，是为了不让人连点烧 token —— 两道闸互不替代。
        """
        rid = str(room_id or "").strip()
        if not rid:
            # 没有 room_id 就没有群窗可回帧（也不知道该回给谁）—— 只能记日志。
            # 与 handle_room_message 的缺参分支同规约（那里也只是丢弃 + 记日志）。
            log.warning("room_skip 缺少 room_id（已丢弃）")
            return
        try:
            await self.rooms.run_round(rid)
        except KeyError:
            # 真机形态：群刚被解散/删除，而那个群窗还开着，玩家按了跳过。
            log.warning("room_skip 指向不存在的群（已丢弃）：room=%s", rid)
            await self._fail_room_round(rid, "这个群聊已经不存在了（可能刚被解散）。")
        except Exception:
            log.exception("群聊跳过一轮异常（room=%s）", rid)
            await self._fail_room_round(rid, self.FALLBACK_TEXT)

    async def _fail_room_round(self, room_id: str, text: str) -> None:
        """给群窗回一条**带 error 的终帧**（复位忙碌态 + 一行人话解释）。

        ★ 为什么失败也必须发帧：`phase="end"` 是 C# 群窗复位忙碌态的**唯一**依据
          （`docs/archive/2026-09-17-群聊编排/设计定稿.md` §6.2），而 C# 是"先置忙再发请求"的。少了它，错误帧会被 C# 的
          switch 静默落空，群窗就永远卡在"正在输入"——而这恰恰是玩家最需要反馈的时候
          （最长要等 240s 的 `BusyWatchdog` 才自愈，且一行解释都没有）。

        抽成一个方法而不是在四个 except 里各写一遍：这条规约（**失败必带 end 帧**）一旦
        有两个以上抄本，就会在第三次改动时漂移掉一个。
        """
        try:
            await self.ws.send_event("room_state", room_id=room_id, phase="end",
                                     speaking=[], spoken=[], error=True, text=text)
        except Exception:
            pass

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
                ok, text, info = await self._run_manual_compact(npc_id)
                log.info("/compact 结束：ok=%s 反馈=%s", ok, text)
        # ★ 顺序刻意的：**先**把纪要推给 UI（折叠行出现在末尾），**再**发结果让 C#
        #   把「正在压缩中」那一行收尾。反过来会在收尾后突然多出一行，观感像"补画"。
        if info:
            await self.ws.send_event("compact_summary", npc_id=npc_id, **info)
        await self.ws.send_event("compact_result", npc_id=npc_id, ok=ok, text=text)

    async def _handle_compact_fallback(self, npc_id: str) -> None:
        """/compact 兜底通道（未经专用 compact 事件的客户端）：结果走 npc_reply(error=true)
        → C# 渲染系统提示行并复位 busy（老 DLL 未发专用事件时 SetBusy(true) 已被本地调用）。"""
        if not npc_id:
            return
        ok, text, _info = await self._run_manual_compact(npc_id)
        await self.ws.send_event("npc_reply", npc_id=npc_id, text=text, error=True)

    async def _run_manual_compact(self, npc_id: str) -> tuple:
        """/compact 公共执行体：占坑防重入 → 取回合锁 → 调 agent.compact_now() → 组反馈文案。
        返回 (ok, text, info)；info 非空 = 真的产出了一条纪要（供调用方推给 UI 展示）。
        绝不抛异常（异常在此兜成失败文案，调用方只管回推）。"""
        if npc_id in self._compacting:
            # 反复出现这条 = 上一次压缩卡住了（占坑未释放）。这是「UI 一直显示压缩中」的典型指纹。
            log.warning("压缩重入被拒（npc=%s 仍在压缩中）——若反复出现说明上一次压缩卡住了", npc_id)
            return False, "正在压缩中，请稍候", None
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
            return False, "压缩失败（服务异常），对话历史保持原状", None
        finally:
            self._compacting.discard(npc_id)

    async def _compact_agent(self, agent: Any) -> tuple:
        """对单个 agent 执行手动压缩并组玩家可读反馈。返回 (ok, text, info)。

        `info` = 压缩真的产出了纪要时的展示载荷（`compact_summary` 事件体）；没压成则 None。
        为什么由这里取而不是调用方再扫一遍账本：只有本方法知道"这次压缩到底成没成"，
        而 `summary.latest()` 还可能捞到**上一次**留下的旧纪要 —— 那会把旧纪要当新的推给 UI。
        """
        if getattr(agent, "compactor", None) is None:
            log.warning("压缩被拒：compaction 未配置（agent.compactor 为 None）")
            return False, "压缩功能未启用（compaction 未配置）", None
        if agent.phase["kind"] != "idle":
            log.info("压缩被拒：回合进行中（phase=%s）", agent.phase["kind"])
            return False, "当前回合进行中，结束后再试", None
        before = self._context_pressure(agent)
        log.info("压缩开始：上下文约 %d tokens，开始调用压缩器（含一次摘要 LLM 调用，可能较慢）", before)
        try:
            result = await agent.compact_now()
        except RuntimeError:
            # DialogueAgent.compact_now 的 idle 闸：锁内理论到不了，防御性兜底
            log.warning("压缩被拒：compact_now 报非 idle（防御性分支）")
            return False, "当前回合进行中，结束后再试", None
        after = self._context_pressure(agent)
        if result:
            saved = max(0, before - after)
            log.info("压缩成功：%d → %d tokens（释放 %d）range=%s", before, after, saved,
                     result.get("range"))
            return (True,
                    f"压缩完成：上下文约 {before} → {after} tokens（释放 {saved}），早段对话已合并为纪要",
                    self._summary_payload(agent, source="manual"))
        log.info("压缩未执行：无可用切点或摘要失败（上下文仍为 %d tokens，历史保持原状）", after)
        return False, "没有需要压缩的内容（对话尚短或摘要未能生成），历史保持原状", None

    @staticmethod
    def _summary_payload(agent: Any, source: str = "manual") -> Optional[Dict[str, Any]]:
        """把"当前生效的纪要"整理成 `compact_summary` 事件体；没有则 None。

        字段命名与历史投影 `kind:"compaction"` 逐项对齐（text/chars/editable/...），
        让 C# 两条路（直播 / 回放）能用同一个函数建行。
        """
        try:
            info = compact_summary.latest(getattr(agent, "session", None))
        except Exception:
            log.warning("取压缩纪要失败（UI 将看不到这次纪要，历史仍会重放）", exc_info=True)
            return None
        if not info:
            return None
        return {
            "compaction_id": info["compaction_id"],
            "text": info["text"],
            "chars": info["chars"],
            "editable": info["editable"],
            "lock_reason": info["lock_reason"],
            "source": source,
            "turn": int(agent.phase.get("turn") or 0),
        }

    @staticmethod
    def _context_pressure(agent: Any) -> int:
        """当前会话上下文压力估算（与 Compressor.maybe 同口径：header + 全部消息）。

        走 `Session.estimate_context_tokens`（带增量缓存，11-15）：数值与逐条 sum 逐字等价，
        但没变的消息不再重算 —— 这里在手动压缩前后各调一次，历史长时差别明显。
        """
        try:
            header = agent.session.request_header()
            return agent.session.estimate_context_tokens(meter.estimate_header(header))
        except Exception:
            return 0

    async def handle_initiative(self, npc_id: str, intent: str, reason: str = "", text: str = "",
                                consented: bool = False, debug: bool = False, busy: bool = False,
                                speech_only: bool = False, speaker: str = "", lines=None,
                                action: bool = False, verdict: bool = False,
                                npc_unit_id: str = "", same_grid: "bool | None" = None) -> None:
        """NPC 主动开口回合：C# 触发检测发来「何时/对谁/什么意图」，这里驱动
        NPC 以自身口吻主动开场（伪 user 意图 + 正常 turn，复用被动回合全部机制）。

        game_drama 变体（原生剧情窗「AI 对话」按钮）：C# DramaAiOption 点击发来 intent=game_drama
        + text=剧情原文 + speaker=本页那句是谁说的（`npc`/`player`/空=判不出）——玩家主动点击非事件
        轰炸，跳过全局节流；原文**按 speaker 归属**包装成舞台指令，NPC 顺着剧情以自身人设润色开场
        （设计文档 README.md 附录二 G（功能设计底稿）；归属判据见 docs/reference/design-notes.md §1）。
        + lines=**整段相遇里还没投过的多页原文**（09-22 新增，可选）——一次当面相遇往往翻好几页
        （切磋是「约战 → 打完 → 战后两页」），只投屏幕上那一句会让模型看不见先发生的事。
        多句包装与上限见 `initiative.format_game_drama_message`；攒段判据在 C# `DramaSegmentCore`。
        + action=C# 在点击那一刻采集的"游戏里那个动作还在跑"（09-23 新增，可选）——包装里据此点明
        "这场交互正在进行中、**别重复发起同款动作工具**"（实测：论道页上点「对话」，模型又调一次论道）。
        `lines` 里的行还带 `kind`（page/option）：`option` = **玩家点的选项**，包装渲染成"玩家选择了：…"。
        + verdict=C# 在点击那一刻判"**当前这一页就是战斗处置页**"（页号 20403/20404，配表事实；
        翻到后页不带 —— 用户拍板甲案）——包装追加"别调工具去执行页面上的献物/求饶、这只是这一页"
        （完整理由见 `initiative._VERDICT_NOTE`）。与工具结果上的 `verdict_page`（管**生死**）分工不同。

        action_done 变体（动作办妥回执，09-24）：C# `DeferredActionQueue` 把"此刻发不出去"
        （战斗中/剧情链收尾 —— 硬发会沉进游戏系统队列不出队、90 秒静默超时）的动作排进队，
        收场后自动执行，办完发来 `intent=action_done` + `reason=一句结果摘要`。**不与 game_drama
        合并**：那条的动作指令是"别重复发起、顺着说"，这条是"已经有结局了，可以交代一声"。
        投递分两档（用户 09-24 拍板）：该 NPC **正忙** → 投成 next-step 增量（当前回合的下一步
        领走，顺口交代，不另起回合）；**空闲** → 走正常主动开口回合。与 consented/debug 一样
        **豁免全局节流** —— 这是"他那件事的结局"，被防轰炸节流吞掉模型就永远不知道结果
        （而它很可能因此再发起一遍同一个动作）。

        consented 变体（当面同意放行）：同格当面确认窗玩家点了「同意」，C# 才发来触发——
        玩家主动放行同样跳过全局节流（否则点击与生成之间的间隔会被节流吞掉，NPC 永不开口）。
        设计文档 README.md 附录二 G（功能设计底稿）。

        debug 变体（诊断强制触发，09-12）：C# 侧 `_diag_initiative_force.txt` 哨兵打开时
        （NpcInitiativeMonitor.TryTriggerForced）发出的触发——C# 已放宽日节拍/概率/冷却三闸，
        这里同步豁免全局节流，否则 min_interval（默认 300s）会把强制节拍削成"看着像没生效"。
        仅在事件帧带 `debug:true` 时成立，source 落账带 debug 标记（可审计、不影响正常触发）。

        busy 变体（玩家忙碌，09-12）：C# 侧状态闸判定"玩家正忙"（战斗中/有窗口或模态遮罩/
        我们自己的确认窗或 AI 行动挂起）时，**只禁动作、不禁言语**——NPC 照常开口（异地走未读+横幅，
        同格也不弹确认窗而直接传音），但本回合**不许发起行动**。

        ★ 实现方式（10-16 更正，此前这段 docstring 写错过）★
        **工具表恒定，从不剔除任何工具。** 两个原因叠加：
          ① 工具表进 `request/header` 的 hash（`dialogue_agent.py`），按忙闲动态增删会
             让 header 每轮变一次 ⇒ **服务商前缀缓存全废**；
          ② "自主交互本质就是模拟用户发一条消息"，用消息约束它才与既有机制同构。
        真正的约束分两层，**都在工具表之外**：
          · **软**：尾部追加 `BUSY_NOTE`（plugin 消息，模型可见 / UI 不可见，`post_note`）；
          · **硬**：执行层 `agent.speech_only_turn` —— 模型**照常能调**动作工具，
            但 `_execute_tool_calls` 不调 bridge，改回一条 error tool_result
            （落账形状与正常路径逐字同构，否则模型看不到拒绝会反复重试）。
        被拦的是 **6 个动作工具**（`social_relation` / `movement` / `world_ai_action` /
        `economy_item` / `trade` / `item_acquire`，= `tools/schemas.py` 的 `ACTION_TOOLS`），
        只读三件套（`inspect_unit` / `search_units` / `query_world`）照常放行、静默完成。
        这是用户 09-12 拍板的两级语义：闸门管"能不能打扰"，不管"能不能说话"。

        快速失败（锁外）：该 NPC 有回合在跑（phase != idle）则静默丢弃——
        主动开口是可跳过事件，不排队、不积压（同 NPC 场景下锁外读 phase 无竞态，
        锁保证同 NPC 串行，phase 在排队期间不会变）。
        """
        if not npc_id or not intent:
            return
        agent = self.loop.get(npc_id) or self.loop.create(npc_id)
        # ★ npc_unit_id（09-28 修「未找到发起方 管星贤」）★：说话人的**精确身份**，由 C#
        #   从真身随事件带上来。为什么必须有：主动传音/剧情窗是**第三条会话入口** —— 它
        #   不经过单聊 `open_chat`（只有那里会 `set_unit_id`），所以本会话的自身身份为空，
        #   工具调用便不注入 `initiator_id`，C# 只能按名猜；真机那次登记恰被自愈摘除 +
        #   全图按名扫描也没覆盖到他 ⇒ 本回合 5 个动作工具全部 `未找到发起方`。
        #   写在所有分支**之前**：这一回合的每一次工具调用都要用到它。
        #   空 = 老 C# / 触发时取不到真身 ⇒ 一字不改（`set_unit_id("")` 会把身份清掉，
        #   所以这里**只在非空时写**）。
        if npc_unit_id:
            try:
                prev = str(getattr(agent, "unit_id", "") or "")
                agent.set_unit_id(npc_unit_id)
                if prev and prev != npc_unit_id:
                    log.warning("主动开口：自身身份被事件改写 %s → %s（npc=%s）",
                                prev, npc_unit_id, npc_id)
            except Exception:
                log.warning("主动开口：写入自身身份失败（本回合回落按名解析）", exc_info=True)
        self._inject_acquaintance_if_new(agent)

        # ★ action_done（09-24）：排队动作的"办妥回执" —— 与 game_drama 同一条"绝不丢"的语义，
        #   但**分两档投**（用户拍板）：
        #     · 该 NPC 正有回合在跑 → 投成 next-step 增量（`wake=False`）：当前回合的下一步就领走，
        #       顺口交代一句，不另起回合、不打断他正在说的话；
        #     · 空闲 → 落到下面走正常主动开口回合（伪 user 指令 + 正常 turn，复用全部机制）。
        #   忙时不学 game_drama 进"信箱"等收口：那个回合本来就是**他自己那个动作的续集**，
        #   当场（下一步）说一句，比几十秒后另起一个回合适得多。
        action_done_text = format_action_done_message(reason) if intent == "action_done" else ""
        if action_done_text and agent.phase["kind"] != "idle":
            agent.send(action_done_text, target="next-step", wake=False,
                       source={"kind": "initiative", "intent": intent, "reason": reason or ""})
            log.info("动作办妥回执：该 NPC 有回合在跑 → 投成下一步增量（npc=%s）", npc_id)
            return

        if agent.phase["kind"] != "idle":
            # 诊断强制触发（debug）打个日志：否则"每 20s 触发一次但聊天窗没反应"会被误读成链路坏了，
            # 实际是这一闸（该 NPC 上一回合还没跑完 → 主动开口不排队、错过就丢）。正常触发不打，免噪。
            if debug:
                log.info("诊断强制触发被丢弃：该 NPC 有回合在跑（npc=%s intent=%s）", npc_id, intent)
            # ★ game_drama 例外（09-22）：那是**玩家亲手点的「对话」**，不是系统自动触发。
            #   攻击跨过战斗收口后，NPC 还要把结果演成一句话（2~4s），玩家手快就会撞进这一闸
            #   ⇒ 原先"错过就丢"= 面板开了、意图静默蒸发 = 玩家看到的正是"点了没反应"。
            #   改成"最多晚到，不会不到"：存进单槽，等这个 NPC 的回合收口后由 `_drain_drama` 补投。
            if self._park_drama(npc_id, intent, text, speaker, lines):
                return
            await self._notify_consented_dropped(npc_id, consented, "busy")
            return  # 忙守卫（快速失败）：有回合在跑 → 错过就丢
        async with self._turn_slots:                # 全局并发上限
            async with self._lock_for(npc_id):      # 该 NPC 回合锁
                try:
                    # 防御复查：锁内再确认 idle（不变量；锁已保证串行，防未来绕过锁的驱动路径）
                    if agent.phase["kind"] != "idle":
                        if debug:
                            log.info("诊断强制触发被丢弃：锁内复查该 NPC 仍忙（npc=%s）", npc_id)
                        if self._park_drama(npc_id, intent, text, speaker, lines):
                            return
                        await self._notify_consented_dropped(npc_id, consented, "busy")
                        return
                    now = monotonic()
                    # action_done 也是"他自己那件事的结局"（09-24）：被防轰炸节流吞掉 = 模型永远
                    # 不知道结果（而它很可能因此再发起一遍同一个动作）⇒ 与 game_drama 一样豁免。
                    # mail_relay（11-13）也豁免：C# 的优先队列**只改排序**，日节拍与
                    # daily_chance 照常走 —— 也就是说它一天最多派一个人，本来就不是高频事件。
                    # 而 300s 全局节流是"防事件轰炸"用的（C# 侧冷却失效时的双保险），
                    # 拿它挡掉"有人托他带话"会让这条链路在最常见的情形下静默失效：
                    # 玩家刚跟 A 说完话 → A 捎话给 B → 恰好 299 秒内有过一次主动开口 → 丢。
                    player_initiated = (intent == "game_drama" or intent == "action_done"
                                        or intent == "mail_relay"
                                        or consented or debug)
                    if not player_initiated and now - self._last_initiative_ts < self.min_initiative_interval:
                        log.debug("主动开口被全局节流丢弃（距上次 %.1fs < %.1fs）npc=%s",
                                  now - self._last_initiative_ts, self.min_initiative_interval, npc_id)
                        return  # 全局节流：短于最小间隔，防事件轰炸（玩家主动发起不受此限）
                    self._last_initiative_ts = now

                    # 意图 → 伪 user 文案：括号舞台指令，模型以 NPC 口吻主动开场
                    if intent == "game_drama":
                        text = format_game_drama_message(text, speaker, lines, in_action=action,
                                                         verdict_page=verdict)
                        # drama_lines 只是**审计用**的条数（真机报"NPC 好像没看见前面那几句"时，
                        # 一眼看出这次到底投了几句）；落账的正文里不含它。
                        source = {"kind": "initiative", "intent": intent, "reason": "",
                                  **({"busy": True} if busy else {}),
                                  **({"action": True} if action else {}),
                                  **({"verdict": True} if verdict else {}),
                                  **({"drama_lines": len(lines)} if isinstance(lines, list) and lines else {})}
                    elif intent == "action_done":
                        # 走到这里 = 该 NPC 空闲（忙的那一档在上面就投成 next-step 了）→ 走正常回合。
                        # 文案复用上面算好的那份（同一个纯函数、同一份 reason，重建也不值几个字节）。
                        text = action_done_text
                        source = {"kind": "initiative", "intent": intent, "reason": reason or ""}
                    else:
                        # `busy` 必须传进去（11-13 复核）：`mail_relay` 是**绕开 PickIntent
                        # 直接指定**的意图，不像其余键那样天然被 `!busy` 挡在动作支路外 ——
                        # 不传的话，忙回合的舞台指令照样会附上"现在就调工具办成"，
                        # 而同一回合执行层正用 `speech_only_turn` 拦着所有动作工具。
                        text = format_initiative_message(intent, reason, same_grid=same_grid,
                                                         busy=busy)
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
                            # 互动时间戳：**主动开口也算一次真实互动**（10-21 用户报障的那条路
                            # 就是它 —— 横幅弹了、红点亮了，但「最近」里的位置不动）。
                            # 与 `handle_message` 同一个位置、同一个理由（发送前先记）。
                            self._touch_interaction(npc_id)
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

    # ---- 玩家手点的「对话」：撞上"该 NPC 有回合在跑"先进一次性信箱，收口后补投（09-22）----

    DRAMA_TTL_SECONDS = 30.0        # 只在信箱里活这么久：过了就作废并回执，别半分钟后凭空开口
    # 「回合正卡在工具上」时的放宽上限（秒，09-23 加）：基础 TTL 防的是"半分钟后凭空开口"，
    # 而工具等待是**已知的、有结局的**长等（非心跳槽最长 90s；跨战斗那条靠心跳可到 600s）——
    # 玩家点「对话」的这一刻正是当前这一幕，等回合把工具等完才是对的。
    # 取 180s（= 非心跳槽上限的两倍）；跨战斗那种极长等仍会过期，且**必回执一句实话**（不静默）。
    DRAMA_TTL_TOOL_SECONDS = 180.0
    _DRAIN_WAIT_SECONDS = 2.0       # 补投前等回合转 idle 的轮询步长

    def _park_drama(self, npc_id: str, intent: str, text: str, speaker: str, lines=None) -> bool:
        """game_drama 撞上忙守卫 → 存进单槽，返回 True = 已接管（调用方别再走"丢弃 + 回执"）。

        **只接管 game_drama**：那是玩家亲手点的「对话」，"错过就丢"对它不成立（点了没反应
        是最招骂的形态）。其余自动触发的意图照旧静默丢弃 —— 那是设计语义，别顺手一起改。

        存下时**不发**"对方正忙"：让玩家忙起来的那一回合本身正在往窗口里吐字，这会儿插一句
        系统提示只是噪音；真正放弃时（TTL 过）才回执一句实话。

        `lines`（整段增量）**一起存**：补投时要按原样重放 —— 只存 text 的话，补投那一次会退回
        单句包装，NPC 就又看不见前面那几句了（与"信箱=最多晚到，不会不到"同一个语义）。

        `busy` **有意不存**（09-22 复核拍板，别当漏改修掉）：它的含义是"**点击那一刻**链还在收尾"，
        是 C# 实时状态的投影；补投发生在若干秒后，那时链多半已收场，照旧值硬套反而是过度约束
        （整回合什么动作都禁）。真落到"补投时链仍在飞"的窄缝，C# 侧 `DramaGate` 的收尾占位
        照样会拒掉动作（硬闸兜底），所以这里少一层软约束不会让动作漏过去。

        等待上限分两档（09-23）：基础 `DRAMA_TTL_SECONDS`；若轮询时看到该 NPC 的回合**正卡在工具上**
        （`agent.tool_inflight`，工具长等有结局、不是"忙得没空理你"）⇒ 改按 `DRAMA_TTL_TOOL_SECONDS`
        接着等。两档都过期就回执一句实话（不静默）。
        """
        if intent != "game_drama" or not (text or "").strip():
            return False
        now = monotonic()
        for stale in [k for k, v in self._pending_drama.items()
                      if k != npc_id and now - v["at"] > self.DRAMA_TTL_SECONDS]:
            self._pending_drama.pop(stale, None)
        self._pending_drama[npc_id] = {"text": text, "speaker": speaker, "lines": lines, "at": now}
        log.info("玩家点的「对话」撞上该 NPC 有回合在跑 → 进信箱，等回合收口补投（npc=%s，%s句）",
                 npc_id, len(lines) if isinstance(lines, list) else 1)
        return True

    def _schedule_drama_drain(self, npc_id: str) -> None:
        """回合收尾时排一个补投任务。★必须 `create_task`，不能 await★：
        `_run_turn` 的 finally 还站在该 NPC 的回合锁里，而 `asyncio.Lock` **不可重入** ——
        直接 await 就是自锁（补投在等这把还没放开的锁，而锁的持有者正等着它返回）。
        丢回事件循环后，它作为另一个 Task 排队等锁，语义就是"这个回合彻底收口之后再说"。
        """
        if npc_id not in self._pending_drama:
            return
        try:
            asyncio.get_running_loop().create_task(self._drain_drama(npc_id))
        except RuntimeError:                       # 没有运行中的循环（单测里直连调用）：直接作废
            self._pending_drama.pop(npc_id, None)

    async def _drain_drama(self, npc_id: str) -> None:
        rec = self._pending_drama.pop(npc_id, None)
        if not rec:
            return
        deadline = rec["at"] + self.DRAMA_TTL_SECONDS
        while monotonic() < deadline:
            agent = self.loop.get(npc_id)
            if agent is None:
                return                    # 关窗/换档把 agent 收了：这次点击随窗口一起作废
            if agent.phase["kind"] == "idle":
                log.info("信箱里的「对话」补投（等了 %.0fs）npc=%s", monotonic() - rec["at"], npc_id)
                await self.handle_initiative(npc_id, "game_drama", "", rec["text"],
                                             speaker=rec["speaker"], lines=rec.get("lines"))
                return
            # 回合还在跑，但**正卡在一个工具上**（`dialogue_agent` 调桥期间挂的信号）：
            # 那不是"忙得没空理你"，是"这件事还没结局" ⇒ 按放宽上限接着等（见类常量注释）。
            # 只放宽、不无限等：工具最长的那条（跨战斗 ≈600s）仍会过期并回执。
            if getattr(agent, "tool_inflight", None):
                deadline = rec["at"] + self.DRAMA_TTL_TOOL_SECONDS
            await asyncio.sleep(self._DRAIN_WAIT_SECONDS)
        log.info("信箱里的「对话」超时没补上（等了 %.0fs，上限 %ss）npc=%s → 回执一句实话",
                 monotonic() - rec["at"], int(deadline - rec["at"]), npc_id)
        try:
            await self.ws.send_event("npc_reply", npc_id=npc_id,
                                     text="（他这一阵正忙着，没接上你的话——稍后再点一次试试）",
                                     initiative=True, error=True)
        except Exception:
            log.warning("补投作废的回执发送失败（npc=%s）", npc_id, exc_info=True)

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

    def _touch_interaction(self, npc_id: str) -> None:
        """记「这个 NPC 刚刚真的说了一句」（通讯录「最近」排序的权威源）。

        为什么是 hub 而不是 agent：时间戳的消费者是**通讯录面板**，与 agent 生命周期无关
        （关窗冻结、换档丢弃都不该把它一起蒸发 —— 玩家说过的话没变）。
        服务未注入 / 未定档 → 静默跳过（`ContactsService.touch_interaction` 自己也会判），
        这里是**单入口**：两个 `npc_reply` 分支（玩家消息 / 主动开口）都经此，
        免得哪天加第三条又漏一次（那正是"某个来源不计入最近"这类 bug 的形态）。
        """
        svc = self._contact_service
        if svc is None:
            return
        fn = getattr(svc, "touch_interaction", None)
        if fn is None:
            return                                  # 老 service（无此方法）：静默跳过
        try:
            fn(npc_id)
        except Exception:
            log.warning("记录互动时间戳失败（npc=%s）——「最近」排序本次回落文件 mtime",
                        npc_id, exc_info=True)

    def _flush_interactions(self) -> None:
        """`save_happened` 里把互动时间戳落盘（无变更 → 不碰盘）。

        与 `_touch_interaction` 对称：服务未注入 / 方法缺失 / 写失败都**不抛**
        —— 存档流程本身必须成功，通讯录排序是次要效果。
        """
        svc = self._contact_service
        if svc is None:
            return
        fn = getattr(svc, "flush_interactions", None)
        if fn is None:
            return
        try:
            k = fn()
            if k:
                log.info("save_happened：互动时间戳固化 %d 条（通讯录「最近」排序源）", k)
        except Exception:
            log.warning("互动时间戳落盘失败——「最近」排序下次存档再补", exc_info=True)

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
    def _parse_cursor(p: Dict[str, Any]) -> Tuple[int, int]:
        """解析翻页游标 `(before_seq, before_turn)`（11-21）。

        两者都缺省/非法 ⇒ `(0, 0)` = 不限定（取最近 limit 个回合，老行为）。

        **必须成对返回**：账本里有些回合一个 seq 都没有（`initiative` / `turn_error`
        这类合成内容，姜萌账本 43 回合里有 2 个），只用 seq 判会把它们当成
        "永远更早"⇒ 每页重复返回。`before_turn` 就是给这种回合兜底的锚。
        """
        def _int(v: Any) -> int:
            try:
                n = int(v)
                return n if n > 0 else 0
            except Exception:
                return 0
        return _int(p.get("before_seq")), _int(p.get("before_turn"))

    @staticmethod
    def _parse_scene(p: Dict[str, Any]) -> Optional[str]:
        """`scene` 参数语义（多人化：UI 回放按场合过滤）。

        - **缺省 / `null`** → `""` = **单聊场合**。产品语义：1:1 窗只显示单聊（与直播对齐 ——
          `ChatPresenter.cs` 会丢掉 `npc_id` 不匹配本窗的帧，只有回放会把群聊内容塞进来）。
          今天所有 `turn/start` 的 scene 都是 `""`（只有 P1 的房间编排层会设别的值），
          所以这是**可证的无操作**；P1 给群聊回合打上场合后自动生效，C# 无需再改。
        - `"*"` → `None` = **不过滤**（要"这个 NPC 的全部经历"时显式要；唯一的"全都要"出口）。
        - 其它字符串 → 按该场合过滤（**含显式空串**，不做二次猜测）。

        ★ `null` 与"缺省"同义、都走默认：C# 的序列化器若把没赋值的字段发成 `null`，
          绝不能因此**静默关掉过滤**（那会退回"1:1 窗混进群聊内容"的老行为）。
          要"不过滤"必须显式写 `"*"`——唯一的、写错了也看得见的出口。
        """
        if "scene" not in p:
            return ""
        raw = p.get("scene")
        if raw is None:
            return ""
        if str(raw).strip() == "*":
            return None
        return str(raw)

    @staticmethod
    def _history_out(agent: Any, npc_id: str, limit: int, scene: Optional[str] = "",
                     before_seq: Any = None, before_turn: Any = None) -> Dict[str, Any]:
        """session → UI 历史投影（npc_id 由本层补齐，投影函数不管它）。

        `scene`：`""` = 只出单聊（缺省）；`None` = 不过滤；其它 = 该场合。
        `before_seq` / `before_turn`：游标翻页（11-21）。两个都要透传 ——
        单靠 seq 无法排除"整回合没有 seq"的合成回合（见 `session.history._turn_before_cursor`）。
        """
        out = project_ui_history(agent.session, max_turns=limit, scene=scene,
                                 before_seq=before_seq, before_turn=before_turn)
        out["npc_id"] = npc_id
        out["scene"] = scene          # 回执：让 C# 知道自己拿到的是哪个场合的切片
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
        if method == "world_hello":
            # 连接建立时 C# 补发的存档身份（09-18）。为什么需要它：`world_id` 只活在
            # Python 进程内存里（**故意不落盘**——存一个过期的存档身份比没有更危险），
            # 而"补发身份"原先只有 save/load 两条事件。于是 Python 重启（配置面板保存 /
            # BrainLink 拉起 / 崩溃重生）后，窗口内的对话会落到**扁平目录**：既看不见本存档
            # 的历史，下一次存档还会把从扁平目录读来的短账本**整份覆盖**进正确存档
            # （静默丢记忆。09-18 复现 + 修复，见 docs/reference/csharp-iron-laws.md 2026-09-18-存档身份丢失.md）。
            #
            # ★ 语义与 save/load **不同：不 flush、也不无条件 discard**。
            #   重连时 Python 可能还活着、手里握着刚聊完还没固化的增量 —— 那是玩家的东西，
            #   补一次身份不该把它抹掉。只有"身份真的变了"才丢弃：那说明内存里那些 agent
            #   属于**另一个存档**（玩家在断线期间读了别的档），它们既不能写进新目录
            #   （覆盖新档账本），也不能留着（账本是别人家的）。
            #   未进世界（主菜单）时帧里没有 world_id → 沿用现状 + INFO，不猜。
            changed = self._apply_world(p, why="world_hello", missing_level="info")
            dropped = 0
            if changed:
                dropped = self.loop.discard_all()
                if dropped:
                    log.warning("world_hello：存档身份变了 → 丢弃 %d 个属于上一个存档的活体"
                                "（未固化增量作废，与读档同语义）", dropped)
            # 群名录：Python 重启后内存里是空的，而它只在 load_happened 里载入过一次 ——
            # 不在这里补，重启后通讯录里的群会"消失"到下一次读档。幂等：只补缺的 id，
            # 已在内存的群（可能带着未固化增量）一个字段都不动。
            nr = self.loop.load_rooms()
            log.info("world_hello：存档身份已补发 → %s（%s，丢弃 %d 个，群名录 %d 个）",
                     self._world_tag(), "换档" if changed else "沿用", dropped, nr)
            return {"world": self.loop.world_id or "", "changed": changed,
                    "discarded": dropped, "rooms": nr}
        if method == "open_chat":
            # 开窗激活：对话 UI 打开 = agent 激活（get or create，磁盘账本自动 resume），
            # 并直接返回历史投影（C# 开窗一次往返拿全历史）。顺带撤销待销毁标记——
            # 「关窗 → 立刻重开」竞态下重开即取消销毁。
            npc_id = str(p.get("npc_id", ""))
            if not npc_id:
                raise ValueError("open_chat 缺少 npc_id")
            limit = self._parse_limit(p)
            scene = self._parse_scene(p)
            self._dispose_pending.discard(npc_id)
            agent = self.loop.get(npc_id) or self.loop.create(npc_id)
            # ★ 开窗那一刻 C# **正握着这个人的真身**（`ChatLauncher.OpenForUnit` 的入参就是
            #   `WorldUnitBase`），于是它随请求把精确 unitID 带上来 —— 这是**单聊侧唯一的
            #   权威身份来源**，也是"模型只看到中文名"与"落刀要精确"能同时成立的接缝。
            #   用途：给每个工具调用注入 `initiator_id`（**模型看不到那个键**）。
            #   取不到（老 DLL 不发这个键 / 按名猜出来的对象）⇒ 空串 ⇒ 不注入 ⇒
            #   C# 回落按名解析，与改动前逐字一致。
            if "unit_id" in p:
                try:
                    agent.set_unit_id(p.get("unit_id"))
                except Exception:
                    log.warning("open_chat：写入 unit_id 失败（按名解析照常）", exc_info=True)
            bseq, bturn = self._parse_cursor(p)
            out = self._history_out(agent, npc_id, limit, scene, bseq, bturn)
            log.info("open_chat：npc=%s 激活（场合=%r，历史回放 %d 项，游标=(%d,%d)，更早锚=(%d,%d)）",
                     npc_id, scene, len(out.get("items") or []), bseq, bturn,
                     out.get("next_before_seq") or 0, out.get("next_before_turn") or 0)
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
            scene = self._parse_scene(p)
            agent = self.loop.get(npc_id)  # 只查不建：读历史不产生新活体
            if agent is None:
                return {"npc_id": npc_id, "scene": scene, "items": [], "complete_turns": 0}
            bseq, bturn = self._parse_cursor(p)
            out = self._history_out(agent, npc_id, limit, scene, bseq, bturn)
            log.info("get_history：npc=%s 场合=%r 回放 %d 项（游标=(%d,%d)，更早锚=(%d,%d)）",
                     npc_id, scene, len(out.get("items") or []), bseq, bturn,
                     out.get("next_before_seq") or 0, out.get("next_before_turn") or 0)
            try:
                if hasattr(agent, "snapshot_stats"):
                    out["stats"] = agent.snapshot_stats()
            except Exception:
                pass
            return out
        if method == "edit_compact_summary":
            # 压缩纪要的修改（10-31，用户需求）：只在"压缩刚完成、还没开启新一轮对话"的
            # 窗口内允许。判据分两层 —— 账本层（`summary.editability`，可回放）在
            # `apply_edit` 里；运行时那两道（phase / 正在压缩）补的是"正在发生"的半秒。
            npc_id = str(p.get("npc_id") or "")
            if not npc_id:
                raise ValueError("edit_compact_summary 缺少 npc_id")
            agent = self.loop.get(npc_id)
            if agent is None:
                return {"ok": False, "reason": "对话未激活（请重新打开对话窗后再试）"}
            if npc_id in self._compacting:
                return {"ok": False, "reason": "正在压缩中，请稍候再改"}
            if agent.phase.get("kind") != "idle":
                return {"ok": False, "reason": "回合进行中，结束后再试"}
            try:
                info = compact_summary.apply_edit(
                    agent.session, str(p.get("compaction_id") or ""), p.get("text"))
            except compact_summary.SummaryEditError as e:
                log.info("压缩纪要修改被拒（npc=%s）：%s", npc_id, e)
                return {"ok": False, "reason": str(e)}
            # 改的是 surface 里的一条消息 ⇒ 上下文规模变了，旧实测值立即作废
            # （与压缩/删除同一个出口；漏了会让面板上的"上下文"停在改之前那次请求的数）。
            try:
                agent.refresh_context_pressure()
            except Exception:
                log.warning("修改纪要后刷新上下文规模失败（不影响这次修改）", exc_info=True)
            log.info("压缩纪要修改成功：npc=%s cid=%s %d 字（第 %d 次）",
                     npc_id, info["compaction_id"][:8], info["chars"], info["edits"])
            return {"ok": True, "compaction_id": info["compaction_id"],
                    "text": info["text"], "chars": info["chars"],
                    "editable": info["editable"], "edits": info["edits"]}
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
            # None 容错（10-21）：列表里混进 null（异形调用方/显式 null 参数）时
            # int(None) 抛 TypeError 会把整个删除 RPC 变成"保存报错"；null 项没有
            # 语义，直接滤掉，其余照常。
            turns = [int(t) for t in (p.get("turns") or []) if t is not None]
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
            # 删除改了 surface ⇒ 上下文口径必须跟着走：否则面板上的「上下文」一直是删除前的规模，
            # 用户会以为"删了没变小"。ⓘ 累计用量口径**不动**——删历史不退钱（见 stats.rebuild_from_log）。
            try:
                if hasattr(agent, "refresh_context_pressure"):
                    agent.refresh_context_pressure()
            except Exception:
                log.warning("删除后刷新上下文规模失败（面板会沿用旧值）", exc_info=True)
            limit = self._parse_limit(p)
            # 删完直接回新历史，C# 一次往返重放。
            # ★ 游标也要走同一条路（11-21）：删除后重放若不分页，会一次性灌全部历史 ——
            #   正是"每次拿全部 ⇒ 卡"那条老路。这里**不读 p 里的游标**（客户端不会传），
            #   传 (0,0) = 只取最近 limit 个回合，剩余靠「加载更早」翻。
            out = self._history_out(agent, npc_id, limit, self._parse_scene(p), 0, 0)
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
            # 顺带再断言一次存档身份（幂等）——万一 load_happened/world_hello 那条丢了
            # （旧 DLL / 连不上），这里也不会把增量写进上一个存档的目录。
            #
            # ★ 身份**变了**才是危险的那一瞬：内存里的 agent 是在另一个命名空间里读/建出来的
            #   （典型：Python 重启后 world_id 为空，玩家聊了几句，然后游戏存档 → 身份这时才
            #   到达）。此时**先丢弃再固化**——顺序反了就是"从扁平目录读来的短账本整份覆盖
            #   真存档的历史"（09-18 复现的静默丢记忆）。`AgentLoop.flush_all` 里还有一道
            #   出处对账兜底，两层都拦得住。
            #
            # ⚠ 与 `load_happened` 的**刻意不对称**（10-18）：帧里宣告 identity=unknown 时，
            #   `load_happened` 作废身份（进世界 = 旧身份一定错的**证据**），而这里**不作废**
            #   —— 存档不是"世界变了"的证据（你能存的档就是你在的那个世界），此时沿用身份照常
            #   固化才对：作废只会让 `flush_all` 整体跳过 ⇒ 玩家存了档、对话却没落盘。世界真
            #   变了的话，那条 `load_happened` 已经负责作废了。
            changed = self._apply_world(p, why="save_happened")
            dropped = 0
            if changed:
                dropped = self.loop.discard_all()
                log.warning("save_happened：存档身份与内存不符 → 先丢弃 %d 个活体再固化"
                            "（这些账本属别的命名空间，写进去会覆盖 %s 的历史）",
                            dropped, self._world_tag())
                self.loop.load_rooms()
            n = self.loop.flush_all()
            # 互动时间戳与账本**同一次事件里落**（10-21）：只落一边就会出现
            # 「账本里有这句话、通讯录却说他很久没说话」（或反之）。
            # 放在 flush_all **之后**：`_apply_world` 可能刚换过命名空间，那时
            # `ContactsService` 的目录也已经切好，这里写进的就是新存档。
            self._flush_interactions()
            log.info("save_happened：固化 %d 个 agent 会话（存档 %s）", n, self._world_tag())
            return {"flushed": n, "discarded": dropped}
        if method == "load_happened":
            # 进世界事件（存档语义）：C# 钩子 EGameType.IntoWorld 触发（读档/新建皆走）。
            # 世界（重）载 = 未固化增量全部丢弃 + 所有活/冻 agent 销毁；之后 open_chat
            # 从 jsonl（上次 flush 状态）恢复，即「读档 = 回到存档时刻」。
            # ：先切命名空间（world_id 随帧到达）再丢弃——顺序反了会把旧存档的增量
            # 写进新存档目录。
            # ★ 10-18：帧若**宣告了 identity=unknown**（进世界了但读不到玩家单位），
            #   则"进世界"这件事本身已经是"旧身份作废"的证据 —— 不许沿用上一个存档
            #   （那正是"新开存档继承了通讯录"的根因）。见 `_invalidate_world`。
            if _identity_unknown(p):
                self._invalidate_world("load_happened")
                n = self.loop.discard_all()
                # 群名录**不载**：此刻未定档，`rooms_root` 会指向扁平目录（不属于任何存档），
                # 载进来就是"别人家的群"。身份补发后那一帧 `load_happened` 会把它带回来。
                log.info("load_happened：世界重载（身份未知→已作废），丢弃 %d 个 agent；"
                         "不载入群名录（待身份补发）", n)
                return {"discarded": n, "rooms": 0, "world_unknown": True}
            self._apply_world(p, why="load_happened")
            n = self.loop.discard_all()
            # 群聊名录一并载入（流水懒加载：真要看某个群时才读它的 jsonl）。
            # 必须放在 discard_all **之后** —— 反了的话刚载入的名录会被清掉。
            nr = self.loop.load_rooms()
            log.info("load_happened：世界重载，丢弃 %d 个 agent（未固化增量作废，存档 %s），载入 %d 个群",
                     n, self._world_tag(), nr)
            return {"discarded": n, "rooms": nr}
        # ---- 群聊 RPC（路由到 RoomFlow；本类只做参数校验与回执）----
        if method == "create_room":
            # **只建群、不回放历史**（通讯录「＋新建群聊」确认走这条）。
            # 与 open_room 的分工见 `RoomFlow.create_room` 的长注释：建群是改数据、开窗是开窗。
            # ★ 回执刻意**不带 history**：那条路要 import `room_history`（群窗投影），
            #   而建群根本不需要投影 —— 09-17 的 `zlib.error` 就爆在那个函数内 import 上
            #   （服务器 exe 在运行中被换掉，没加载过的模块再也读不出来）。
            #   回执形状 = 名录条目（与 list_rooms 的每行同构），C# 据此直接把新群插进列表。
            members = [str(m) for m in (p.get("members") or []) if str(m).strip()]
            if not members:
                raise ValueError("create_room 需要 members（至少一位成员）")
            room = self.rooms.create_room(members,
                                          title=str(p.get("title", "") or ""),
                                          member_unit_ids=_unit_id_map(p.get("member_unit_ids")))
            log.info("create_room：%s（%s）成员=%s，精确身份 %d 人",
                     room.id, room.title, room.members, len(room.member_unit_ids))
            return room.to_meta()
        if method == "open_room":
            # 建群或重开。给 room_id 就重开那个群（同一批人不会自动合成同一个群 ——
            # 那是 C# 的入口策略；Python 只认 room_id）。开群一次往返拿全历史，
            # 与 open_chat 同一套路。**不唤醒任何成员**：没人说话就不花一个 token。
            room_id = str(p.get("room_id", "") or "").strip()
            members = [str(m) for m in (p.get("members") or []) if str(m).strip()]
            if not room_id and not members:
                raise ValueError("open_room 需要 room_id（重开）或 members（新建）")
            room = self.rooms.open_room(members, room_id=room_id,
                                        title=str(p.get("title", "") or ""),
                                        member_unit_ids=_unit_id_map(p.get("member_unit_ids")))
            # 群窗首屏同样只取最近 N 条（11-21 分页）：不传游标 = 最近 limit 条，
            # 更早的由 `get_room_history` + `before_seq` 翻。
            _bseq, _bturn = self._parse_cursor(p)
            out = self.rooms.history(room.id, limit=self._parse_limit(p), before_seq=_bseq)
            log.info("open_room：%s 成员=%d 回放 %d 项（游标 seq=%d，更早锚=%d）",
                     room.id, len(out.get("members") or []), len(out.get("items") or []),
                     _bseq, out.get("next_before_seq") or 0)
            # 首屏基线：本群期间累计用量随开群一起给（C# 开群路径直接刷新底部统计行）。
            # 不给的话群窗那行会停在预制体默认文本（"None"）直到**第一个成员发言结束**
            # —— 用户 09-18 的截图就是这个现象。
            try:
                out["roomUsage"] = room.group_usage_block()
            except Exception:
                log.warning("open_room：本群用量整形失败（本行等首次发言后再刷）", exc_info=True)
            log.info("open_room：%s（%s）成员=%s，历史 %d 条",
                     room.id, room.title, room.members, len(out.get("items") or []))
            return out
        if method == "close_room":
            room_id = str(p.get("room_id", "") or "").strip()
            if not room_id:
                raise ValueError("close_room 缺少 room_id")
            return {"closed": self.rooms.close_room(room_id)}
        if method == "list_rooms":
            return {"rooms": self.rooms.list_rooms()}
        if method == "get_room_history":
            room_id = str(p.get("room_id", "") or "").strip()
            if not room_id:
                raise ValueError("get_room_history 缺少 room_id")
            # ★ 11-21 群聊分页：与单聊同一条思路（群发言平铺带 seq，单锚即可）★
            bseq, _bturn = self._parse_cursor(p)
            out = self.rooms.history(room_id, limit=self._parse_limit(p), before_seq=bseq)
            log.info("get_room_history：%s 回放 %d 项（游标 seq=%d，更早锚=%d）",
                     room_id, len(out.get("items") or []), bseq, out.get("next_before_seq") or 0)
            return out
        if method == "add_room_members":
            # 中途拉人。★ **不要借 `open_room` 的重开分支**来加人：那条路会顺带把
            # 全量历史再吐一遍（`open_room` 无条件调 `history`），且 title 非空时
            # 还会顺手改群名 —— 加个人不该有这两个副作用。
            #
            # 语义（`RoomFlow.add_member`）：入群位置 = 当前末尾 ⇒ **新人看不到
            # 入群前的话**。UI 上必须把这一点讲给玩家听，否则会被当成 bug 报上来。
            room_id = str(p.get("room_id", "") or "").strip()
            members = [str(m) for m in (p.get("members") or []) if str(m).strip()]
            if not room_id or not members:
                raise ValueError("add_room_members 需要 room_id 与 members")
            uid_map = _unit_id_map(p.get("member_unit_ids"))
            added = [m for m in members if self.rooms.add_member(room_id, m, uid_map.get(m, ""))]
            meta = self.rooms.room_meta(room_id)
            log.info("add_room_members：%s 加入 %s；未生效（已在群）%s",
                     room_id, added, [m for m in members if m not in added])
            return {"room_id": room_id, "added": added,
                    "members": meta["members"], "last_seq": meta["last_seq"],
                    # 回传全员精确身份（不只新人）：C# 侧统一拿它去 `UnitLookup.Pin`，
                    # 老成员这一轮顺带也能补上（本功能上线前建的群）。
                    "member_unit_ids": meta.get("member_unit_ids") or {}}
        if method == "kick_room_member":
            room_id = str(p.get("room_id", "") or "").strip()
            npc_id = str(p.get("npc_id", "") or "").strip()
            if not room_id or not npc_id:
                raise ValueError("kick_room_member 需要 room_id 与 npc_id")
            removed = self.rooms.kick(room_id, npc_id)
            meta = self.rooms.room_meta(room_id)
            log.info("kick_room_member：%s 移出 %s（%s）", room_id, npc_id,
                     "成功" if removed else "本来就不在群里")
            return {"room_id": room_id, "removed": removed,
                    "members": meta["members"], "last_seq": meta["last_seq"]}
        if method == "dissolve_room":
            # **解散**（不可恢复）：群没了、公共账本没了。与 close_room 严格区分 ——
            # 后者只"关窗，群还在"（§9.2）。C# 侧走两段确认。
            #
            # 不返回 members/last_seq：群已经不存在了，回一份"尸体"只会诱导调用方
            # 继续把它当活群用。回执只说"删没删掉"，列表由调用方重拉 `list_rooms` 校准。
            room_id = str(p.get("room_id", "") or "").strip()
            if not room_id:
                raise ValueError("dissolve_room 缺少 room_id")
            gone = self.rooms.dissolve_room(room_id)
            log.info("dissolve_room：%s（%s）", room_id, "已解散" if gone else "本来就不存在")
            return {"room_id": room_id, "dissolved": gone}
        if method in ("preview_room_delete", "room_delete"):
            # 群窗删除一条发言（§9.2 定稿）：**群记录 + 各成员记忆，两边同删**。
            # 两阶段与 `preview_delete_history` / `delete_history` 同款：preview 给确认
            # 弹窗（含"谁已折进纪要、谁本就没听到"），execute 才落账；回执都带新群历史，
            # C# 一次往返重放。
            #
            # 守卫（**整体拒绝**，不做"跳过某个成员" —— 那会留下"有人忘了、有人记得"、
            # 而 UI 无法表达的状态）：
            #   ① 一轮群聊进行中（`room_lock` 被持有）；
            #   ② 任一成员正在回合中（`phase != idle` 或有未闭合回合）；
            #   ③ 任一成员正在压缩（`_compacting`）；
            #   ④ 目标是仍在等待回应的提案叙述行（在 `RoomFlow` 里判，提案表在那儿）。
            #
            # 本段**没有 await**：asyncio 单线程下，守卫检查与落账之间没有别的协程能插进来
            # （一轮群聊起跑要先 await `room_lock`，插不进来）⇒ 检查即保证。
            room_id = str(p.get("room_id", "") or "").strip()
            if not room_id:
                raise ValueError(f"{method} 缺少 room_id")
            uids = [str(u) for u in (p.get("uids") or []) if str(u or "").strip()]
            if not uids:
                raise ValueError(f"{method} 缺少 uids（要删哪条发言）")
            meta = self.rooms.room_meta(room_id)        # 走 _require：不存在 ⇒ 当场报错
            if self._room_lock_for(room_id).locked():
                # 先验房间再取锁：反过来的话，一个不存在的 room_id 也会在 `_room_locks`
                # 里留下一把锁（无害的泄漏，但没必要）。
                raise DeleteError("群聊一轮进行中，暂时不能删除群消息（等这轮说完）")
            for m in meta.get("members") or []:
                if m in self._compacting:
                    raise DeleteError(f"{m} 正在压缩记忆，暂时不能删除群消息")
                ag = self.loop.get(m)
                if ag is None:
                    continue
                if ag.phase.get("kind") != "idle" or ag.session.has_open_turn():
                    raise DeleteError(f"{m} 正在对话中，暂时不能删除群消息")
            if method == "preview_room_delete":
                out = self.rooms.plan_room_delete(room_id, uids)
                log.info("preview_room_delete：%s 预演 %s（成员报告 %s）",
                         room_id, out.get("uids"), len(out.get("members") or []))
                return out
            out = self.rooms.room_delete(room_id, uids)
            log.info("room_delete：%s 已删 %s", room_id, out.get("uids"))
            return out
        # ---- 通讯录 RPC（路由到 contact_service；手动好友名单 + 会话索引）----
        # ★ `world_known` 必须随空表一起回（09-20 实机事故，勿删）★
        # 未定档时 `list_contacts()` 返回 `[]` —— 那是**拒绝回答**（不知道这是哪个存档，
        # 不敢读扁平目录），与"这个存档确实没有好友"**同值不同义**。C# 的
        # `ContactStore.SetManualNames` 是整体替换（先 Clear 再填），拿空表当真相就把
        # 手上正确的镜像清空了 ⇒ 用户看到"手动加的好友全部消失、只剩关系层一个人"。
        # 回传这个标志，C# 才能区分"空"与"未知"（老 DLL 不认识它，忽略未知键即可）。
        if method == "list_contacts":
            self._require_contact_service()
            return {"contacts": self._contact_service.list_contacts(), **self._world_fields()}
        # 关系层快照（known.json）：C# 建通讯录时把**手里握着真身**的那批人推上来，
        # 死者靠它才不会从名单里消失（关系层现取游戏数据，死者 GetUnit=null ⇒ 消失）。
        # 读侧与 list_contacts 同规约：未定档时返回空表 + world_known=false，
        # C# 据此判定"拒绝回答"而不是"没有"（`ContactStore.SetKnown` 也是整体替换语义）。
        if method == "list_known_contacts":
            self._require_contact_service()
            return {"known": self._contact_service.list_known(), **self._world_fields()}
        if method == "sync_known_contacts":
            self._require_contact_service()
            items = p.get("items")
            if not isinstance(items, list):
                raise ValueError("sync_known_contacts 缺少 items 数组")
            return self._contact_service.sync_known(items)
        if method == "list_sessions":
            self._require_contact_service()
            return {"sessions": self._contact_service.list_sessions(), **self._world_fields()}
        if method == "add_contact":
            self._require_contact_service()
            npc_id = str(p.get("npc_id", ""))
            if not npc_id.strip():
                raise ValueError("add_contact 缺少 npc_id")
            # unit_id 可选：C# 在 NPC 面板点「加好友」那一刻从**真身**（panel.unit）读到的精确
            # 身份才带（老 DLL 不带 → 空 → None）。空一律当"没提供"，不做格式猜测。
            # ★ 按名解析出来的结果**不许**从这里进来 —— 写侧靠调用方自律（见 contacts_store 文件头）。
            unit_id = str(p.get("unit_id", "") or "")
            return self._contact_service.add_contact(npc_id, unit_id or None)
        if method == "remove_contact":
            self._require_contact_service()
            npc_id = str(p.get("npc_id", ""))
            if not npc_id.strip():
                raise ValueError("remove_contact 缺少 npc_id")
            return self._contact_service.remove_contact(npc_id)
        # ---- 隐藏名单（通讯录行内 ✕ 的"关系层隐藏"侧；09-26 用户拍板）----
        # 重现出口只有一个：C# 在该 NPC 再次主动传音时调 unhide_contact。
        if method == "list_hidden":
            self._require_contact_service()
            return {"hidden": self._contact_service.list_hidden(), **self._world_fields()}
        if method == "hide_contact":
            self._require_contact_service()
            npc_id = str(p.get("npc_id", ""))
            if not npc_id.strip():
                raise ValueError("hide_contact 缺少 npc_id")
            # unit_id 语义与 add_contact 逐条一致：可选、空一律当"没提供"（不做格式猜测）
            unit_id = str(p.get("unit_id", "") or "")
            return self._contact_service.hide_contact(npc_id, unit_id or None)
        if method == "unhide_contact":
            self._require_contact_service()
            npc_id = str(p.get("npc_id", ""))
            if not npc_id.strip():
                raise ValueError("unhide_contact 缺少 npc_id")
            return self._contact_service.unhide_contact(npc_id)
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
        if method == "reset_prompt":
            # 「重置为默认」：把用户副本还原成**随包内置**的那一份（来源唯一，见 prompt_files）。
            # 与 write_prompt 的关键差别：sections/default/_suffix 这类系统件**允许**还原
            # （内容来自内置副本，用户碰不到它，所以不构成"往系统件里写任意内容"的口子）。
            self._require_prompt_service()
            rel = str(p.get("path", ""))
            if hasattr(self._prompt_service, "reset_prompt"):
                result = self._prompt_service.reset_prompt(rel)
            else:   # 兼容旧 mock 服务：直接按不支持处理（fail-closed，不静默成功）
                raise ValueError("提示词服务不支持重置（reset_prompt 未实现）")
            result["effective"] = "hot"
            return result
        if method == "list_mail_pending":
            # 捎话优先队列的数据源（11-13，用户需求：被传话的 NPC 优先自主交互）。
            #
            # 回 `{"items": {npc_id: [发信人真名, …]}, **world_fields}`；只有**有未读捎话**的
            # 人才在表里。C# `NpcInitiativeMonitor` 在日节拍候选排序时查它 ——
            # **只影响排序**（用户明确：仍受一日一次 + daily_chance 限制），
            # 所以回包只是一份名单，不含任何"该不该触发"的判断。
            #
            # 为什么由 Python 出：信箱是账本的一部分（`inbox.next-turn`），C# 读不到也不该复制
            # —— 见 `agent/mailbox.pending_map` 的 docstring（复制体静默分家那种坑）。
            from ..agent import mailbox as _mailbox
            return {"items": _mailbox.pending_map(self.loop), **self._world_fields()}
        if method == "list_tools":
            # 对话窗右侧「可做的事」侧边栏的目录（10-19 用户需求）。
            #
            # 形状：{"groups":[{key,title,items:[{key,title,sub,mode,say}]}]}
            # **纯展示数据**：C# 不据此做任何路由判断 —— 点击只是把 `say` 当成一句玩家发言
            # 发出去（fill 型则填进输入框）。真正的契约（tool/op/枚举）留在 `tools/schemas.py`，
            # 目录与它的对账由 `tools/catalog.py::validate` + `tests/test_tool_catalog_contract.py` 保证。
            #
            # 为什么不开 token 成本：目录**不进 prompt**，每回合固定开销零增加。
            # 为什么放在这里而不是 ConfigService：它不读配置、不碰文件，就是一张静态表。
            from ..tools import catalog as _catalog
            return _catalog.payload()
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

    def _apply_world(self, params: Dict[str, Any], why: str, missing_level: str = "warning") -> bool:
        """把 C# 带来的存档身份（world_id = 玩家 unitID / player_name）应用到
        AgentLoop 与 ContactsService。两处必须同时切——会话落盘目录与通讯录目录同源。

        缺 world_id（旧 C# / 手工 RPC / 尚未进世界）→ 沿用当前命名空间：宁可保持现状，
        也不能猜一个 id 把数据写到别处。幂等：同 id 重复调用只是空转。

        `missing_level`：帧里没有身份时的日志级别。save/load 是"必须带"的两条，缺了要
        WARNING（多半是旧 DLL）；`world_hello` 是连接建立时的例行补发，主菜单里本来就没有
        身份，用 INFO —— 否则每次启动都刷一条假告警。

        **返回是否真的换了档**（只认 AgentLoop 那一份；两处同源，不会一个动一个不动）。
        调用方据此决定"要不要丢弃内存里的活体"——换档意味着它们属于**另一个存档**：
        既不能写进新目录（全量重写会覆盖新档的账本），也不能留着（它们的账本是别人家的）。
        """
        p = params or {}
        wid = str(p.get("world_id") or "").strip()
        pnm = str(p.get("player_name") or "").strip() or None
        # 玩家 unitID 的**原样**值（`world_id` 是 sanitize 过的目录名，不能当 id 用）
        puid = str(p.get("player_unit_id") or "").strip() or None
        if not wid:
            _log = log.info if missing_level == "info" else log.warning
            _log("%s 未携带 world_id（旧 C#？/ 尚未进世界%s）→ 沿用当前存档命名空间 %s",
                 why, "，且已宣告 identity=unknown" if _identity_unknown(p) else "",
                 self._world_tag())
            return False
        changed_loop = self.loop.set_world(wid, pnm, puid)
        changed_contacts = False
        if self._contact_service is not None and hasattr(self._contact_service, "set_world"):
            try:
                changed_contacts = self._contact_service.set_world(wid, pnm)
            except Exception as e:              # 通讯录切不动不该阻断存档固化/丢弃
                log.warning("%s：通讯录命名空间切换失败（%s）", why, e)
        if changed_loop or changed_contacts:
            self._world_epoch += 1          # 世代号：跨档回包的判废依据（见 `_world_fields`）
            log.info("%s：存档命名空间 → %s（目录 %s，世代 %d）", why, self._world_tag(),
                     self.loop.sessions_root, self._world_epoch)
        return bool(changed_loop)

    def _invalidate_world(self, why: str) -> None:
        """**作废**存档身份（退回"未定档"）——只丢身份，一个字都不碰盘上的数据。

        ★ 为什么必须有这一步（10-18 用户报的"新开存档继承了通讯录"）★
        `load_happened` = 进世界。它只要**没有**带来身份，就说明"我手上那个身份一定是错的"
        —— 这不需要读到新身份就能判定。旧行为是把它和"主菜单里读到空"一样处理（沿用现状），
        于是新建存档时那一帧读不到玩家单位（世界正在重建），新档就直接读到了**上一个存档**的
        通讯录，而且回包 `world_known=true`（Python 确实"定档"了，只是定在别人家）⇒ C# 无法
        分辨 ⇒ 静默串档，且新档加的好友会写进旧档目录。

        作废之后：`_blocked()` 为真 → `list_*` 回空表 + `world_known=false` → C# **一个字都不写**
        （09-20 那条通道），面板如实显示"尚未取得存档身份"；等身份补发（C# 逐帧重试 / 下一次
        存档 / 下一次连接）再自动恢复。

        ⚠ 只作废、不清盘：`worlds/<旧档>/` 一个字节都不动，恢复后读的还是那些文件。
        """
        had = self.loop.world_id or ""
        self.loop.set_world("")             # 空 id → world_id=None + 人名映射表内存副本作废
        if self._contact_service is not None and hasattr(self._contact_service, "set_world"):
            try:
                self._contact_service.set_world("")
            except Exception as e:          # 作废失败不该阻断丢弃（冻结/销毁照走）
                log.warning("%s：通讯录命名空间作废失败（%s）", why, e)
        self._world_epoch += 1
        log.warning("%s：帧宣告 identity=unknown（世界事件发生了但读不到玩家单位）→ "
                    "**作废**存档身份（原 %s）→ 退回未定档：通讯录/会话**拒绝回答**，一个字都不写；"
                    "等身份补发后自动恢复", why, had or "(本来就未定档)")

    def _world_fields(self) -> Dict[str, Any]:
        """档级回包必带的**身份三元组**（`list_contacts` / `list_sessions` / `list_hidden` 统一经此）。

        - `world_known`：Python 此刻是否定档（09-20 事故：被闸住时的空表是"拒绝回答"而不是
          "没有好友"，调用方据此决定要不要信这个空表）；
        - `world_id` / `world_epoch`：**这个答案属于哪个存档**（10-18 补）。

        为什么后两个必须有（09-20 那次的补全）：回包过去不带任何身份，于是"旧档的名单"和
        "新档的名单"是两个**无法区分**的合法答案 —— 只要一次回包跨过了换档那一瞬（面板刚开过、
        连接正在更替、两条 socket 的帧交错），C# 就会把旧档名单写进新档的镜像。带上身份与世代，
        消费端才**判得废**（C# `WorldMatches`：不匹配的一律不写）。

        ⚠ 是**方法**而不是三处各写一遍：三个 list RPC 必须同源，漏一个就是一个新的串档入口。
        """
        return {
            "world_known": not getattr(self._contact_service, "blocked", False),
            "world_id": self.loop.world_id or "",
            "world_epoch": self._world_epoch,
        }

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
        思考内容已在过程中经 think 事件展示，收尾帧只给正文。

        另剥行首的 `名字：` 前缀：多人化后发给模型的消息统一带说话人前缀
        （`_apply_speaker_labels`），模型可能顺手模仿成 `张三：…`。
        它模仿的是**格式**，不是内容，所以在这里静默去掉——那层前缀本来就是我们加的。"""
        for ev in reversed(agent.session.log):
            if ev.get("type") != "assistant/message":
                continue
            msg = (ev.get("data") or {}).get("message", {}) or {}
            for b in msg.get("content", []) or []:
                if isinstance(b, dict) and b.get("type") == "text" and b.get("text"):
                    _, body = _think.split_think(b["text"])
                    return ChatHub._strip_self_name_prefix(body, getattr(agent, "id", "")).strip()
        return ""

    @staticmethod
    def _strip_self_name_prefix(text: str, npc_id: str) -> str:
        """剥行首 `^<自己的名字>[：:]\\s*`。名字为空则不剥（绝不做无判据的截断）。"""
        body = text or ""
        who = str(npc_id or "").strip()
        if not who:
            return body
        stripped = body.lstrip()
        if not stripped.startswith(who):
            return body
        rest = stripped[len(who):]
        if rest[:1] in ("：", ":"):
            return rest[1:].lstrip()
        return body
