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

from ..session.session import Session
from ..session.session import find_orphan_tool_calls, pairing_balanced
from ..session.inbox import Inbox
from .system_prompt import SystemPrompt
from .. import log_setup
from ..llm import llm_adapter
from ..session.history_ops import live_turn_count
from ..session.stats import UsageTracker
from ..compaction import summary as compact_summary
from ..tools import text_render, unit_refs

log = log_setup.get_logger(__name__)


def _new_id() -> str:
    return str(uuid.uuid4())


class TurnAborted(Exception):
    """回合被外部中止（读档 / 换存档 / 关窗取消）—— **不是**故障。

    为什么需要这个类型：`cancel()` 会把 `self.phase` 整个换成 idle，而 `_turn` 的循环体
    可能正卡在 `await`（最典型：`get_context` 等 C# 主线程，上限 120s）。恢复之后它回到
    循环里读 `self.phase["step"]` —— 那个键此刻已经不存在，于是抛 `KeyError: 'step'`。
    症状（11-20 实机）：玩家读档那一瞬间正好在等快照，整轮被 `KeyError` 打掉，日志里是
    `回合 395 以 aborted 收口：'step'` 配一条 traceback，而真相只是"世界重载了"。

    所以：**中止要用中止来表达**。循环里每个 await 之后都验一次"我还是那个回合吗"，
    不是就抛本异常，由 `_turn` 认账成 `aborted`（账本照常闭合 `turn/end`）。
    """

    def __init__(self, reason: str = "回合被中止"):
        super().__init__(reason)
        self.reason = reason


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
        # 群聊**策略闸门**（提案制）：`Callable[[str, dict], Optional[dict]]`。群聊回合由
        # `RoomFlow._one_speaker` 注入、`ChatHub._run_turn` 的 finally 与场合标记一起复位；
        # 单聊恒为 None（一行都不跑）。返回值三态：
        #   None                          → 放行到 bridge；
        #   普通 dict                     → 短路当结果（提出/拦截）；
        #   {"__pass_after__": True, "after": fn}
        #                                 → 放行 + 事后收尾：bridge 返回后调 `fn(res)`
        #                                   （提案制"应下"用它写公开叙述行 / 失败私信，
        #                                   见 `RoomFlow._make_accept_settle`）。
        self.tool_gate = None
        # 单口 LlmClient：三路（openai/stub/echo）已下沉到 llm/，循环机只认 generate
        if llm is None:
            from ..llm.echo_client import EchoLlmClient

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
        # 阶段钩子（11-21）：回合里"现在在等什么"——进/出各一次，带阶段名。
        # 为什么单开一个钩子而不复用 on_step：`step` 是**账本事实**（要落盘、要重放、
        # C# 按 kind 分派），而"正在等快照"不是发生过的事，只是当下的状态 ——
        # 混进 step 会让它进帧队列、被重演一遍，并在账本里留一条没有语义的记录。
        # 阶段名只有三个（与 C# `ChatWindow.SetBusyPhase` 的文案一一对应）：
        #   "snapshot" 等 C# 取快照 / "think" 等模型首字 / "stream" 正在吐字。
        # 签名 (phase, entering)：entering=True 表示进入该阶段，False 表示离开。
        self.on_wait_phase: Optional[Callable[[str, bool], Any]] = None
        # 用量与耗时统计（DSH tokenUsage/sessionStats/contextPressure 对齐）：
        # 一 agent 一实例，随活体创建/销毁；resume 时从账本重建累计（计时归零）。
        self.stats: UsageTracker = stats if stats is not None else UsageTracker(context_window=context_window)
        if context_window is not None:
            self.stats.set_context_window(context_window)
        try:
            self.stats.rebuild_from_log(session.log)
        except Exception:
            pass
        # 上下文口径：resume 时先把"现存规模/现存轮数"同步上（pressure 仍待第一次实测）。
        # 顺序在 rebuild 之后：rebuild 会把 pressure 清空，这里只补 surface 侧的两个数。
        try:
            self.refresh_context_pressure()
        except Exception:
            pass
        # 运行时状态
        self.phase: Dict[str, Any] = {"kind": "idle", "last_turn": self._last_turn()}
        self._abort = False
        # 回合令牌：**每次 `cancel()` 自增**。循环体在每个 await 之后比对"我还是不是开跑时
        # 那个令牌"，不是就抛 `TurnAborted`（见该类 docstring 里 11-20 那次 `KeyError: 'step'`）。
        # 用自增计数而不是 `self.phase` 本身：`cancel()` 会把 phase 整个换掉，而换掉这件事
        # 恰恰是我们要**观测**的；令牌独立存在，才不会随 phase 一起被抹掉。
        self._turn_epoch = 0
        # 当前正在跑的回合快照（`_turn` 开跑时写入）。与 `_turn_epoch` 不等 ⇒ 已不是当前回合。
        # 0 = 没有回合在跑（`_ensure_turn_live` 只在 `_turn` 内部被调用，那时它已等于 epoch）。
        self._active_epoch = 0
        # 当前正在等的工具名（None = 没在等）：**只读信号**，供编排层区分"回合卡在工具上"
        # 与"回合在生成/收口"。信箱补投（`ChatHub._drain_drama`）据此把等待上限从 30s 放宽到 180s
        # —— 工具长等是**有结局的**等（非心跳槽 90s、跨战斗靠心跳到 600s），值得等；
        # 与 `phase` 分开：phase 说"回合在跑"，它说"跑在哪一步"。由 `_execute_tool_calls` 的 finally 摘除。
        self.tool_inflight: Optional[str] = None
        # 差分缓存：保 log 瘦与前缀稳定（对标 DSH RuntimeContextProjection + headerEquals）
        # L1 拆成若干段（time/self/player/where/other:*/recent），各自独立差分写屉 + 独立差分发送。
        #
        # ⚠ 段清单**不再是常量、也不再在这里取快照**（多人化）：单聊 5 段，群聊再按在场者
        # 各加一段 `other:<npc_id>`，随场合增删。所以：
        #   · `_last_l1` / `_retained_ctx` 起手是**空 dict**，键随首步长出来；
        #   · **键集本身就是"上一步有哪些段"** —— 离场清理靠 `上一步键集 − 当前键集` 求差，
        #     自动覆盖所有离场路径（关窗 / NPC 死亡 / C# 解析失败 / 压缩折走），
        #     不需要在每个业务出口记得 pop。历史教训：原先硬编码 `{"self","player","recent"}`，
        #     新增段时 `.get()` 不报错、赋值也能补键 ⇒ **漏了完全静默**。
        #   · 空 dict 起手 ⇒ 首步每段都没有基线 ⇒ 全部发一次（这正是首轮应有的行为）。
        self._last_l1: Dict[str, Optional[str]] = {}
        self._last_l1_text: Optional[str] = None  # 兼容字段：各段合并文本（供测试/审计）
        self._retained_ctx: Dict[str, Optional[str]] = {}
        # 当前同席名单（**除自己以外**的在场 NPC；玩家不在其中——玩家恒在场）。
        # 只由 `set_roster` 写，缺省空 = 单聊。它是 Python 侧的真相，「所在」段的在场者名单据此成文；
        # **绝不能拿 C# 的 raw 当在场名单的来源**（raw 只提供各人明细）——名单一旦被读成空，
        # 「所在」段仍会成文（它恒非空，见 SystemPrompt.format_where_segment 的 ★）
        # 却写出一句"并无他人在场"，等于对模型说谎，而这条谎话会一直留在转录里。
        self.roster: List[str] = []
        # `roster` 的**精确身份**（`{名字 → unitID}`，只覆盖知道的那部分）。
        #
        # ★ 为什么与 `roster` 分成两个字段，而不是把 roster 直接换成 id：
        #   模型**只看得到中文名**，而段名/标签/「所在」段的在场名单全部由 roster 成文
        #   （`other:<名字>`、「同席·名字」）。一旦 roster 变 id，模型眼前就会出现
        #   `other:Xs6JDI` 和「同席·Xs6JDI」——这是不可接受的。
        #   所以：**名单是名字**（给模型），**身份是 id**（只上行给 C# 取数/取图）。
        #   转译由 `Room.member_unit_ids`（房间级临时映射表）提供，Python 侧完成。
        #
        # ★ **必须与 `roster` 同一次写**（`set_roster(names, unit_ids)` 单入口）：
        #   分开写就会出现"名单是新的、身份是上一轮的" —— 那一帧的取数会挂到**别人**身上，
        #   而名字看着完全正常（比全空更糟）。
        self.roster_ids: Dict[str, str] = {}
        # **我自己**是谁（unitID）。空 = 未知（回落按名，行为与本功能上线前一致）。
        # 来源：群聊 = 该群的身份表（`set_roster` 时若自己在名单相关表里则另由 set_scene 侧给）；
        #       单聊 = `open_chat` 时 C# 告知（`set_unit_id`）。
        # 用途：给每个工具调用注入 `initiator_id`（**模型看不到这个键**）。
        self.unit_id: str = ""
        # **玩家的**名字与精确身份（harness 注入工具参数用；模型侧只出现名字）。
        # 由 `set_player` 写：C# 在 load_happened/save_happened 里带上来，
        # `ChatHub._run_turn` 每回合刷新一次（换存档后绝不留下上一档的 id）。
        self.player_name: str = ""
        self.player_unit_id: str = ""
        # **人名映射表**：`{名字（或唯一化显示名） → unitID}`。
        #
        # 它是"模型只认名字、C# 只认 id"这两件事之间的**唯一接缝**：
        #   · 来源① 群成员 / 同席 —— `Room.member_unit_ids` 随名单一次写进 `roster_ids`（已存在）；
        #   · 来源② 我自己 —— `set_unit_id`；
        #   · 来源③ 玩家 —— `set_player`；
        #   · 来源④ **查询结果** —— `_harvest_ids`（`search_units` / `inspect_unit` 的结果里
        #     本来就带 `(name, unit_id)`，收下来即可）。
        # 键是模型**看到过的那个字符串** —— 重名时 `tools/unit_refs` 会给出唯一化显示名
        # （`益婉容(化神殿·结晶后期)`），这里就按那个串登记，模型照抄即可精确指人。
        #
        # ★ **世界级共享 + 落盘**（09-17 用户拍板）：知识是世界级的（"李四是谁"与哪个 NPC
        #   在说话无关），所以实体是 `AgentLoop.identity`（一份 `UnitIdMapService`），
        #   所有 agent 共享同一个对象。**必须落盘**：模型的历史里留着显示名，而钥匙只在
        #   内存的话，重进游戏后那个显示名就再也解不开（只能退回裸名按名猜，静默错人）。
        #   落盘时机跟游戏存档（`save_happened → flush_all`），与会话账本同寿命。
        #   `identity_store` 为空（单测 / 没有 loop 的裸 agent）时退回本对象的内存表。
        #
        # ★ 仍然**不塞进 `Room`**：那会污染"成员身份"的语义（`Room.to_meta` 只留当前成员，
        #   塞进去会被静默裁掉）。
        self.identity_store = None
        # 管家（`AgentLoop`）：只有它知道"别的 NPC 的账本在哪"（活体 / 冻结舱 / 磁盘 / 懒创建）。
        # 捎话（`social_relation op=chuan_hua`）靠它把话投进**收信人**的账本 ——
        # 本对象只认自己的账本，跨人写入必须经管家。为空 ⇒ 该 op 明确报错，不静默成功。
        self.loop = None
        self._local_unit_ids: Dict[str, str] = {}
        # 已知**重名**的裸名（`unit_refs` 报上来的）：这些名字**不登记**裸名键 ——
        # 登记谁都是错的。模型若仍写裸名 ⇒ 不注入 id ⇒ C# 回落按名解析（与老行为一致）。
        self._local_ambiguous: set = set()
        # 当前**场合**标识（多人化）：空串 = 单聊；P1 的房间编排层会把它设成房间 id。
        # 只由 `set_scene` 写，落点是 `turn/start.data.scene` —— `history.project_ui_history`
        # 据此把 UI 回放按场合过滤（1:1 窗只看单聊，见 `docs/archive/2026-09-17-群聊编排/计划-下游消费方.md` §3.3）。
        #
        # ★ 为什么**必须**由编排层在"进房"和"出房"两侧都设：它缺省是 ""，
        #   出房忘了复位 ⇒ 之后的单聊回合都带着房间标签 ⇒ 在 1:1 窗里凭空消失。
        #   与 roster 分开两个字段（而不是从 roster 推导）：0 个同席的房间仍是房间。
        self.scene: str = ""
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
            # 成员检查走 set：`surface.nodes` 是 **list**，`in` 是 O(n)，而这里是"倒扫整个 log"，
            # 合起来 O(n²)。段清单动态化后"齐全"判据也没了（见下），必须去掉这个平方项：
            # session 现在是"一辈子"（单聊 + 所有群聊），log 会长得多，而本方法在 __init__ 里跑
            # （每次 resume / 重开窗都跑一遍）。
            nodes = set(self.session.surface.nodes)
            for ev in reversed(self.session.log):
                src = (ev.get("data", {}) or {}).get("source", {}) or {}
                if ev.get("type") != "user/message" or src.get("plugin") != "@python-harness/system-prompt":
                    continue
                # 仅当该消息仍在 surface（未被 replace 清除）才算 retained
                if ev.get("seq") not in nodes:
                    continue
                content = ev["data"].get("content", [])
                if content and isinstance(content[0], dict) and content[0].get("type") == "text":
                    # seen 同时当**去重闸**用：倒序扫 ⇒ 每段首次遇到的就是最新一条。
                    # 原先只看 `len(seen)` 决定是否 break、却对每条都无条件回填，
                    # 于是同一段的**更旧**消息会把刚回填的新值覆盖掉（基准偏旧 → 未变也重发）。
                    # 段清单动态化后这个洞更明显（消息条数随在场人数上升），故一并修掉。
                    name = self._restore_segment_from(content[0].get("text", ""), seen=seen)
                    if name:
                        seen.add(name)
                        self._last_l1_text = "__restored__"
                        # 不再有"全部找齐就 break"：段集合是动态的（other:* 随在场者增减），
                        # 没有一个可预知的"齐全"判据。O(n) 全扫（配上面的 set）足够便宜。
        except Exception:
            log.warning("L1 基线反查失败（沿用当前基线，可能造成重复发送）", exc_info=True)
        # ② 作废被折走的段。判据用 truthy：空段（如 C# 没发 now 时的 time 段）基线本就是 ""，
        #    不该被当成"发过又丢了"去重发一个永远为空的段。
        #    遍历"基线里所有曾有值的段"，**不是固定清单** —— 段集合是动态的，
        #    固定清单会漏掉 `other:*` 这类动态段（漏了就是静默：那段再也不会被发出去）。
        lost: List[str] = []
        for name in list(self._last_l1.keys()):
            if self._retained_ctx.get(name) and name not in seen:
                self._retained_ctx[name] = None
                self._last_l1[name] = None
                lost.append(name)
        # ③ 增量段的**发件箱作废**（10-25，与 ② 同源但不同层）：
        #    「近况」/「与玩家相关」现在只发**新增条目**（见 `agent/l1_incremental.py`）。
        #    它们的"已发过"集合记的是**发过哪些条目**，而 ② 作废的是"发过什么文本"。
        #    压缩把那条消息折走后，那些条目在模型眼里已经不存在了 —— 但发件箱仍认为"发过"
        #    ⇒ 之后只发增量 ⇒ **那段经历永久缺席**，而且完全没有报错。
        #    所以被折走的段必须连发件箱一起重置（reset 后下一次渲染重发全量）。
        try:
            for name in lost:
                self.system_prompt._incr.reset(self.id, name)
        except Exception:
            log.warning("增量段发件箱重置失败（该段可能永久只发增量）", exc_info=True)
        return lost

    def _restore_segment_from(self, raw_text: str, seen: Optional[set] = None) -> Optional[str]:
        """从一条已发的 context 消息里按「可识别前缀」反推是哪一段，回填该段 retained，
        并把该段回填 SystemPrompt 容器（进程重启容器为空场景）。命中返回段名，否则 None。

        `seen` 传入时当去重闸：该段已在里面则**不再回填**（倒序扫描 ⇒ 首次遇到即最新一条）。
        段名走 `SystemPrompt.ctx_segment_of_label`，因此动态段（「同席·李四」→ `other:李四`）
        也能被认出来——旧实现用 `_CTX_LABELS` 的静态反查表，动态段一律认不出。
        """
        marker = "Current runtime context —— "
        if not raw_text or not raw_text.startswith(marker):
            return None
        try:
            label, _, rest = raw_text[len(marker):].partition("：")
        except Exception:
            return None
        name = SystemPrompt.ctx_segment_of_label(label)
        if name is None or not rest:
            return None
        if seen is not None and name in seen:
            return None
        self._retained_ctx[name] = f"{marker}{label}：{rest}"
        self._last_l1[name] = rest
        try:
            # 容器回填：未变段在重启后不再因「容器空→txt 空→误判 remove」而显示移除
            self.system_prompt.context(f"runtime:{name}", rest,
                                       order=self.system_prompt.ctx_order(name), scope=self.id)
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

        images（09-13 起支持；**10-18 起图片进上下文**）：`[{name,url,w,h,kb}]`，url 是 data URL。
        图像字节落存档图片仓（`storage.images`），消息 content 里追加一个
        `{"type":"image","ref":...,"tokens":...}` 块（**只有引用，没有字节**）——
        于是下一回合、重启重放、压缩材料都天然带着这张图，而账本仍然瘦。
        详见 `docs/archive/2026-10-18-图片进上下文.md`；发前投影在 `_inline_image_refs`。

        ★ 降级：逐张处理，落盘失败/认不出只丢**那一张**（警告日志），文本照常进账本 ——
          C# 已经拼好 `[图片：xx]` 占位，模型至少知道当时发过图（与"时间戳拼不上照发"同一条纪律）。
        """
        msg = create_user_message(text, source=source)
        blocks = self._ingest_images(images)
        if blocks:
            msg["content"] = list(msg.get("content") or []) + blocks
        # 写入筐（会落 spliced 账）
        self.inbox.splice(target, len(self.inbox.state[target]), 0, [msg])
        if wake:
            self._wake()

    def _ingest_images(self, images: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
        """收图：字节落盘 → 生成**账本用**的 image 块（ref + token 估算，不含字节）。

        返回的块会直接进消息 content（= 账本事件 data 本体），所以这里**只放**：
          · `ref`   —— 图片仓里的相对路径（权威引用）；
          · `mime`  —— 投影回 data URI 时用；
          · `tokens`—— 收图时按宽高算好的估算（`compaction.meter` 的唯一输入，
            保证压缩阈值/面板估算与这里算出同一个数）；
          · `name`/`w`/`h`/`kb` —— 排障用（面板/日志/变异验证都靠它）。
        图像字节一律**不进账本**（原因见 `storage/images.py` 文件头：账本只追加 + 全量重写）。
        """
        if not images:
            return []
        from ..compaction import meter
        from ..storage import images as image_mod
        store = getattr(self.session, "image_store", None)
        out: List[Dict[str, Any]] = []
        for item in images:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            url = str(item.get("url") or "")
            parsed = image_mod.parse_data_uri(url)
            if parsed is None:
                # 非 data URI（http(s) 直链 / 第三方客户端）：原样保留 url 块 ——
                # 不落盘（下载图片不在本功能范围内，也不能在回合里同步阻塞）。
                if url:
                    log.warning("图片不是 data URI（%s）：按直链原样记入消息", name or url[:40])
                    out.append({"type": "image", "url": url,
                                "tokens": meter.estimate_image_tokens(url=url)})
                continue
            mime, data = parsed
            w, h, kb = item.get("w"), item.get("h"), item.get("kb")
            tokens = meter.estimate_image_tokens(w=w, h=h, kb=kb if kb else len(data) / 1024.0)
            if store is None:
                log.warning("没有图片仓（裸 Session / 管家未挂载）：%s 这张图只能丢弃，文本照记",
                            name or mime)
                continue
            try:
                ref = store.put(data, mime)
            except Exception:
                log.warning("图片落盘失败：%s（本张丢弃，文本照记）", name or mime, exc_info=True)
                continue
            block: Dict[str, Any] = {"type": "image", "ref": ref, "mime": mime, "tokens": tokens}
            if name:
                block["name"] = name
            for k, v in (("w", w), ("h", h), ("kb", kb)):
                if isinstance(v, int) and not isinstance(v, bool) and v > 0:
                    block[k] = v
            out.append(block)
        return out

    def set_roster(self, ids: Any, unit_ids: Any = None) -> None:
        """设置当前同席名单（**除自己以外**的在场 NPC；玩家恒在场、不写进来）。

        它是「所在」段在场者名单的唯一来源，因此**由 Python 侧掌握**（后续接 `Room.members`），
        不依赖 C# 的 raw —— 理由见 `self.roster` 处注释。
        传空 = 单聊 → 「所在」段写成"此刻并无他人在场…"（该段**仍然非空**），
        这正是"群聊→单聊"能把陈旧的在场合照顶掉的原因。

        `unit_ids`（可选）= `{名字 → unitID}`，同名者的**精确身份**，只上行给 C# 用。
        **名字与身份同一次写**（见 `self.roster_ids` 的 ★）：所以这个方法是它们唯一的入口，
        名单怎么过滤，身份就怎么过滤 —— 两者永远同集合，不可能一个有一个没有。

        只改名单，不触发回合、不碰账本；场地切换后由下一次 `_pre_step` 的差分自然生效。
        """
        if isinstance(ids, (set, frozenset)):
            seq = sorted(ids, key=str)
        elif isinstance(ids, (list, tuple)):
            seq = list(ids)
        elif ids is None:
            seq = []
        else:
            seq = [ids]
        me = str(self.id or "").strip()
        out: List[str] = []
        for x in seq:
            s = str(x or "").strip()
            if s and s != me and s not in out:
                out.append(s)
        self.roster = out
        # 身份：**按过滤后的名单重建**（不是直接收下传入的 dict）——
        # 名单里没有的人不该留下身份（与 `Room.to_meta` 裁到当前成员是同一条纪律）。
        src = unit_ids if isinstance(unit_ids, dict) else {}
        self.roster_ids = {}
        for nm in out:
            uid = str(src.get(nm) or "").strip()
            if uid:
                self.roster_ids[nm] = uid

    def set_unit_id(self, unit_id: Any) -> None:
        """告知**我自己**的精确 unitID（单聊：C# 在 `open_chat` 时给；群聊：编排层给）。

        只用于给工具调用注入 `initiator_id`（**模型看不到那个键**，见 `_execute_tool_calls`）。
        传空 = 未知 ⇒ 不注入 ⇒ C# 回落按名解析（行为与本功能上线前逐字一致）。
        """
        self.unit_id = str(unit_id or "").strip()

    def _unit_id(self) -> str:
        """我自己此刻的 unitID（空串 = 未知 ⇒ 调用方不发那个键 ⇒ C# 回落按名）。

        只有一个来源：`set_unit_id`。两处调用点各管一种场合 ——
        单聊由 `ws_channel.open_chat` 从 C# 随请求带上来，群聊由 `RoomFlow.enter`
        从该房间的身份表（`Room.unit_id_of`）取。进群/出群会覆盖它，**这是对的**：
        同一个人在不同存档里 id 不同，而以"此刻所在场合"为准时不会跨档串。
        """
        return str(self.unit_id or "").strip()

    def set_player(self, name: Any, unit_id: Any) -> None:
        """告知**玩家**的名字与精确身份（harness 注入工具参数用；模型侧只出现名字）。

        来源：C# 在 `load_happened` / `save_happened` 里带的 `player_name` / `player_unit_id`，
        由 `ChatHub._run_turn` **每回合刷新一次** —— 换存档后绝不留下上一档的 id
        （`world_id` 是 sanitize 过的存档命名空间，**不能**当 unitID 用，故 C# 另发一个原样字段）。
        """
        self.player_name = str(name or "").strip()
        self.player_unit_id = str(unit_id or "").strip()

    def set_identity_store(self, store: Any) -> None:
        """接上**世界级**人名映射表（`AgentLoop.identity`；同一份对象由所有 agent 共享）。

        传 None 退回本对象的内存表（单测 / 没有 loop 的裸 agent）。
        **不复制** store 里的表：跟着世界切换走的是 store 内部那份（换档会整表换掉），
        复制一份就会在换档后变成陈旧副本 —— 而陈旧的人名映射正是最坏的那种错误。
        """
        self.identity_store = store

    def set_loop(self, loop: Any) -> None:
        """接上管家（`AgentLoop`）：捎话要**按名字**取别人的账本，只有管家知道名册与磁盘。

        传 None 时 `chuan_hua` 会明确失败（"捎话没接线"），绝不静默成功 ——
        模型看到"已捎到"而其实没投，它下一句就会据此编出"他答应了"的剧情，而那边一个字都没有。
        裸构造的 agent（单测 / 没有管家的直连路径）就是这一档。
        """
        self.loop = loop

    @property
    def unit_id_map(self) -> Dict[str, str]:
        """当前人名映射表（世界级 store 优先；没有就退回本对象的内存表）。"""
        st = self.identity_store
        return st.units if st is not None else self._local_unit_ids

    @property
    def ambiguous_names(self) -> set:
        """已知重名的裸名集合（同上：store 优先）。"""
        st = self.identity_store
        return st.ambiguous if st is not None else self._local_ambiguous

    def _lookup_unit_id(self, raw: Any) -> str:
        """把一个**模型写的名字**（裸名或唯一化显示名）翻成精确 unitID；翻不到返回空串。

        优先级：显示名 → 我自己 → 玩家 → 同席/群成员 → 查询学到的。
        **永不猜**：翻不到就返回空串 ⇒ 调用方不注入那个键 ⇒ C# 回落按名解析
        （行为与本功能上线前逐字一致）。
        """
        s = str(raw or "").strip()
        if not s:
            return ""
        bare, suffix = unit_refs.split_display(s)
        if suffix:
            # 显示名：**只认学到的那张表**。它是"确有重名"时才发出去的信封，
            # 拿裸名去猜恰恰是它要避免的事。
            return self.unit_id_map.get(s, "")
        if self._unit_id() and bare == str(self.id or "").strip():
            return self._unit_id()
        if self.player_unit_id and bare == self.player_name:
            return self.player_unit_id
        return self.roster_ids.get(bare, "") or self.unit_id_map.get(bare, "") or ""

    def _inject_person_ids(self, tool: str, args: Dict[str, Any]) -> None:
        """★ **唯一的身份注入点**：工具出站前把模型写的名字换成精确 unitID，挂到 `<键>_id`。

        工具与键的对应表在 `tools/unit_refs.PERSON_ARGS`（**只有会指人的工具才在表里**）。
        群聊与单聊走的是同一条路 —— 群聊以前靠 `RoomFlow` 闸门里的 `translate_member_ids`，
        现在收编到这里（那张表本来也只有 `target/seller/buyer`，且单聊根本不挂闸门 ⇒
        单聊压根没有 `target_id`，这正是本次要补的）。
        """
        try:
            hits = unit_refs.inject_ids(tool, args, self._lookup_unit_id)
        except Exception:
            log.warning("工具 %s 的身份注入失败（回落按名）", tool, exc_info=True)
            return
        if hits:
            log.info("工具 %s 注入精确身份：%s", tool, "、".join(hits))

    def _settle_mail(self, tool: str, args: Dict[str, Any], res: Any) -> None:
        """`social_relation op=chuan_hua` 的**落地**：把这句话投进收信人的账本。

        为什么不由 C# 做：收信人是**另一个 NPC**，他的账本是 Python 的物件
        （活体 / 冻结舱 / 磁盘 / 懒创建 —— 只有 `AgentLoop` 知道在哪）。
        C# 那一趟只负责"按精确身份认出收信人"（重名判据只有它侧有）并把台词原样带回。

        **失败必须改判成失败帧**（`res["success"]=False` + `error`）：
        `text_render.render` 对 `success:false` 一律返回 None ⇒ 模型读到的是 error 原话。
        静默成功的代价是模型下一句就编出"他答应了"，而对方账本里一个字都没有。

        投递进去的是一条 `user/message` + `source.kind="mail"`（见 `agent/mailbox.py`），
        走的是收信人的 `inbox.next-turn` ⇒ 他**这一轮不会被叫醒**，要等玩家下次与他说话
        才被 `_pre_step` 领出来（= 用户要的"不会立即互动"）。
        """
        if tool != "social_relation" or not isinstance(res, dict) or not res.get("success"):
            return
        data = res.get("data")
        if not isinstance(data, dict) or data.get("op") != "chuan_hua":
            return
        to_id = str(data.get("counterparty") or "").strip()
        words = str(data.get("message") or "").strip()
        if not to_id or not words:
            # C# 那边已经校验过（收信人非玩家、message 非空）。走到这里是**装了旧 DLL**
            # （它的枚举里没有 chuan_hua ⇒ 根本到不了这儿）或 C# 回包残缺 —— 一律如实报错。
            res["success"] = False
            res["error"] = "捎话的收信人或内容没拿到（旧版 DLL？这句话没有投递）。"
            return
        if self.loop is None:
            res["success"] = False
            res["error"] = "捎话功能没有接线（没有管家），这句话没有投递。"
            return
        try:
            from . import mailbox
            # 消息体在这里造（`create_user_message` 是 agent 层的东西）：`mailbox` 只负责
            # "投到哪只筐"，不 import `dialogue_agent`（同包反向依赖 + 成环，都有测试钉）。
            def _mk(text: str) -> Dict[str, Any]:
                return create_user_message(text, source={
                    "kind": mailbox.MAIL_KIND,
                    "from": self.id,
                    "from_id": self._unit_id(),   # 发信人精确 unitID（UI 取头像用；空 = 按名）
                    "at": int(time.time() * 1000),
                })
            err = mailbox.deliver(self.loop, _mk, from_id=self.id, to_id=to_id, text=words)
        except Exception as e:
            log.warning("捎话投递异常（%s → %s）", self.id, to_id, exc_info=True)
            err = f"投递时出错：{e}"
        if err:
            res["success"] = False
            res["error"] = err
            log.warning("捎话未投递（%s → %s）：%s", self.id, to_id, err)
            return
        data["delivered"] = True

    def _harvest_ids(self, tool: str, res: Any) -> None:
        """把查询结果里的 `(名字, unitID)` 收进**临时局部映射表**（必须在渲染**之前**调用）。

        为什么必须在渲染前：渲染会把 unitID 全部抹掉（模型只看名字），而"重名者怎么办"
        由 `unit_refs.annotate` **就地写进载荷**（`display` 字段）—— 渲染读的正是它。
        顺序反了就会出现"模型看到的显示名与映射表的键对不上"⇒ 这张表永远用不上。

        只认 C# 载荷里同时带 `name` 与 `unit_id` 的条目（`{name, unit_id}` 是它的约定）：
        这是"查出来的那个人"的精确身份，**不是猜的**。
        """
        if not isinstance(res, dict) or res.get("success") is False:
            return
        try:
            entries, amb_bare = unit_refs.annotate(tool, res, self.unit_id_map, self.ambiguous_names)
        except Exception:
            log.warning("工具 %s 的人名引用收集失败（本次结果不受影响）", tool, exc_info=True)
            return
        # ① 先处理"本次判定为重名"的裸名：**撤回**已登记的裸名键（以前只知道一个李四、
        #    现在知道有两个 ⇒ 那个键再也指不准了），并记进"已知重名"（下一批单独遇到
        #    其中一个时不会再登记裸名）。
        for bare in sorted(amb_bare):
            if self.unit_id_map.get(bare):
                self._map_discard(bare)
                log.warning("人名重名：「%s」出现第二个 unitID → 撤回裸名键，改用唯一化显示名", bare)
            self._map_mark_ambiguous(bare)
        added = shown = 0
        for key, uid, is_display in entries:
            if not key or not uid:
                continue
            cur = self.unit_id_map.get(key, "")
            if cur and cur != uid:
                # 同一个键指向两个人 ⇒ 这个键**不能用**（登记谁都是错的）：撤回它，
                # 并把该名字记进"已知重名" —— 之后单独再遇到他也不能登记裸名。
                self._map_discard(key)
                # ★ 记的是**真名**，不是那个键本身：键可能是 `李四#1` / `李四(化神殿)`
                #   这样的唯一化显示名，而"已知重名"这张表的口径是**裸名**
                #   （`annotate` 拿裸名去比）。把显示名塞进去既是垃圾，又永远匹配不上。
                self._map_mark_ambiguous(unit_refs.bare_name(key))
                log.warning("人名引用冲突：「%s」已有 %s，又见 %s → 不再登记该名字（重名）",
                            key, cur, uid)
                continue
            if cur != uid and self._map_remember(key, uid):
                added += 1
                if is_display:
                    shown += 1
        if added:
            log.info("人名引用 +%d（%s；其中唯一化显示名 %d 条），映射表现有 %d 条",
                     added, tool, shown, len(self.unit_id_map))

    # ---------- 映射表的三个写操作（store 在场时走 store，才能置脏落盘） ----------

    def _map_remember(self, key: str, unit_id: str) -> bool:
        st = self.identity_store
        if st is not None:
            return bool(st.remember(key, unit_id))
        if self._local_unit_ids.get(key) == unit_id:
            return False
        self._local_unit_ids[key] = unit_id
        return True

    def _map_discard(self, key: str) -> None:
        st = self.identity_store
        if st is not None:
            st.discard(key)
            return
        self._local_unit_ids.pop(key, None)

    def _map_mark_ambiguous(self, name: str) -> None:
        st = self.identity_store
        if st is not None:
            st.mark_ambiguous(name)
            return
        self._local_ambiguous.add(name)

    def set_scene(self, scene: Any) -> None:
        """设置当前**场合**标识（空/None = 单聊）。只改标记，不触发回合、不碰账本。

        由升级后的下一次 `turn/start` 生效（`_turn` 把它写进事件 data）。

        ★ **作用域是「回合」而不是「在群里」**（这是对早期注释的修正）：
        一个 NPC 可以同时属于多个群（它确实都经历过），而 `scene` 是**单个字段** ——
        若按"进群设、关窗复位"来做，两个群会互相覆盖。正确做法是拿到该 agent 的
        回合锁之后、跑回合之前设，跑完立刻复位（`ChatHub._run_turn(scene=...)` 已如此实现）。
        单聊路径显式传 `scene=""`，于是"群聊回合被贴上群标签混进 1:1 窗"这个**静默**
        故障（回合照跑、模型照看，只有 UI 少东西）在任何调用顺序下都不可能发生。
        """
        self.scene = "" if scene is None else str(scene).strip()

    def cursor_for(self, room_id: Any) -> Optional[int]:
        """我在这个房间里**投递到哪了**。None = 没记录 = 刚入群 ⇒ 调用方按 `log` 末尾处理。

        游标落在**自己的 session** 里（`room/cursor` 注记），不在 `Room` 上 ——
        理由是**一致性**：游标在 Room、记忆在 session 的话，两次落盘之间出错
        会出现「游标说听过了，但记忆里没有」⇒ 那几条发言**永久丢失**
        （投递不会再来，因为游标说听过了）。放进 session 则记忆与游标在同一个文件、
        同一次 `flush`，不可能不一致（`docs/archive/2026-09-17-群聊编排/设计定稿.md` 不变量 I3）。

        `room/cursor` 对其它模块完全隐形（已核对四条路径）：
        SurfaceManager（未知类型且无 surfaceOp ⇒ 直接 return）、
        history 投影（只认它关心的事件类型）、压缩材料（走 `derive_messages()`）、
        `Inbox.__init__`（只重放 `agent/inbox/spliced`）。

        取**最大值**而不是最后一条：注记只增不改，但读档合并/将来可能的乱序写入
        下，"最后一条"不等于"最远的那条"，而游标语义是单调前进的。
        """
        rid = str(room_id or "").strip()
        if not rid:
            return None
        best: Optional[int] = None
        for ev in self.session.log:
            if ev.get("type") != "room/cursor":
                continue
            data = ev.get("data") or {}
            if str(data.get("room_id") or "") != rid:
                continue
            seq = data.get("seq")
            if isinstance(seq, int) and not isinstance(seq, bool):
                if best is None or seq > best:
                    best = seq
        return best

    def last_reply_node(self) -> Optional[int]:
        """最后一条**有文本**的 `assistant/message` 的账本 seq —— 收口帧就是从它取出来的。

        判据与 `ChatHub._collect_reply` **逐字同构**（倒扫、跳过纯 tool-call 的 assistant）：
        两者必须指的是同一条，否则"这条群发言 ↔ 我的哪个节点"的锚点会指错。

        ★ 调用时机有讲究（见 `RoomFlow._one_speaker`）：必须在**持有本 agent 回合锁**时取，
        否则并发的单聊回合可能插进一条新的 assistant 消息，把"最后一条"顶掉。
        """
        try:
            for ev in reversed(self.session.log):
                if ev.get("type") != "assistant/message":
                    continue
                msg = (ev.get("data") or {}).get("message", {}) or {}
                hit = False
                for b in msg.get("content", []) or []:
                    if isinstance(b, dict) and b.get("type") == "text" and b.get("text"):
                        hit = True
                        break
                if hit:
                    s = ev.get("seq")
                    return s if isinstance(s, int) and not isinstance(s, bool) else None
        except Exception:
            return None
        return None

    def note_own_utterance(self, room_id: Any, seq: Any,
                           node: Optional[int] = None) -> Optional[int]:
        """把「我刚说的这条群发言 = 我账本里的哪个节点」记成一条 `room/utt` 注记。

        为什么需要它：NPC 自己的话**不投递给自己**（`Room.delta_for` 排除 me），
        所以我账本里那条是 `assistant/message`，没有 `source.uid` 可认。做完群窗删除时
        要能把"我自己说过的那句"也一并遗忘，就得有个"这条群发言 ↔ 我的哪个节点"的锚点。
        形状与隐形性与 `room/cursor` 完全一致（未知类型 + 无 surfaceOp ⇒ SurfaceManager
        直接 return；`derive_messages` / UI 投影 / 压缩材料 / Inbox 都不认识它）。

        `node` 由调用方在**持锁时**取好（`last_reply_node`）；缺省 None 时在这里现扫
        （方便单测直接调用，但生产路径不该走 —— 出锁后现扫可能取到别人的收口帧）。
        取不到（收口那条是纯 tool-call 无文本）返回 None、不落注记：那种情况下这句话
        根本进不了群流水（`is_silent` 判空 ⇒ `_say_and_emit` 不会被调用）。

        返回记下的节点 seq（失败/取不到 → None）。**绝不抛异常** —— 锚点丢了最多是
        "这条以后删不掉自己的那份"，不该拖垮一轮发言。
        """
        try:
            if node is None:
                node = self.last_reply_node()
            if node is None:
                return None
            self.session.append("room/utt", {"room_id": str(room_id or ""),
                                             "seq": int(seq), "node": int(node)})
            return node
        except Exception:
            log.warning("room/utt 注记落账失败（room=%s seq=%s）", room_id, seq, exc_info=True)
            return None

    def room_utt_nodes(self, room_id: Any, seq: Any) -> List[int]:
        """我在这个群里的第 `seq` 条发言，对应**我账本里的哪些节点**（可能不止一个）。

        两种锚点（`docs/archive/2026-09-17-群聊编排/设计定稿.md` §9.2 定稿，缺一不可）：
          · **投递副本**：`user/message` 的 `source.uid == "{room_id}#{seq}"`
            —— 玩家消息与同席 NPC 的话都走这条（前者 09-17 起带 `kind="room"` 的锚点）；
          · **自己那条**：`room/utt` 注记里的 `node`（见 `note_own_utterance`）。

        返回**节点 seq**（账本坐标，不是下标）。同一个 uid 命中多条是合法的：
        被踢又被拉回来、或游标被回退的场景下可能真的收到过两份，全都要算上
        （漏一份 = "删了却还记得"）。按升序去重返回，便于断言与日志。
        """
        rid = str(room_id or "").strip()
        try:
            want = int(seq)
        except Exception:
            return []
        if not rid or want <= 0:
            return []
        uid = f"{rid}#{want}"
        hits: set = set()
        for ev in self.session.log:
            t = ev.get("type")
            if t == "user/message":
                src = (ev.get("data") or {}).get("source") or {}
                if str(src.get("uid") or "") == uid:
                    s = ev.get("seq")
                    if isinstance(s, int) and not isinstance(s, bool):
                        hits.add(s)
            elif t == "room/utt":
                d = ev.get("data") or {}
                if str(d.get("room_id") or "") != rid:
                    continue
                try:
                    same = int(d.get("seq")) == want
                except Exception:
                    same = False
                if not same:
                    continue
                node = d.get("node")
                if isinstance(node, int) and not isinstance(node, bool):
                    hits.add(node)
        return sorted(hits)

    def _apply_speaker_labels(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """给「真说出口的话」加 `说话人：` 前缀 —— **只影响发给模型的那一份，账本不动**。

        为什么走投影而不是写进账本：前缀是**呈现**，账本要忠实记玩家真输入。
        写进账本会连带污染 UI 气泡（`history.py` 直接显示 user 文本）、压缩材料
        （`compress.py` 会自己再拼一次 `说话人：` → 双重前缀）、以及既有测试的断言。
        先例：`_inline_image_refs`（图片 ref → data URI）就是同一套"发前投影"。

        ★ 纪律与图片投影完全相同：**必须浅拷贝 + 新 content 列表**，绝不能原地改
        `session.derive_messages()` 返回的对象 —— 那是账本事件 data 本体。

        规则（判据 = `source.kind`）：

          | kind        | 例                                | 加前缀 |
          |-------------|-----------------------------------|--------|
          | 无          | 单聊里玩家真消息 `[1年3月] 你好`   | ✔ `玩家：` |
          | `room`      | **群内投递**的玩家消息             | ✔ `玩家：`（09-17 起带 uid 锚点） |
          | `peer`      | 同席 NPC 说的话                    | ✔ `<speaker>：` |
          | `mail`      | 别的 NPC 捎来的话（信箱）          | ✔ `【<from>托人捎来】`（11-12） |
          | `initiative`| `（NPC主动传音：…）` 舞台指令      | ✘ 不是"谁说的话" |
          | `plugin`    | L1 段 / BUSY_NOTE / 初次相识       | ✘ 系统注入 |

        `mail` **必须单列**：它是 `role:user` 却**不是玩家说的**。落进下面那个 `else`
        （不加前缀）的话，模型看到的是一句没有归属的裸话，最自然的读法就是"玩家刚说的" ——
        于是它会对着玩家应下一件别人托付的事，还把对方的名字当成玩家提到的第三人。
        前缀用【】包起来并写明"托人捎来"，让归属与来路一眼可辨（与 `peer` 的
        `说话人：` 区分开：那个是**当场在场**的人，这个是**隔空递来**的话）。

        `room` 与"无"是**同一句话的两种来路**：单聊投递（`source=None`，逐字节不变）与
        群内投递（带锚点，供"群窗删除"在各成员记忆里定位这一条）。两者都必须渲染成
        `玩家：` —— 少一个分支就会让模型在群里看到一条**没有归属**的裸消息。
        （群内投递**刻意不复用** `source=None`：没有锚点就事后删不掉。单聊路径不传
        `source`，所以"单聊逐字节不变"这条契约仍然成立。）

        格式 `说话人：原文`，**原文一字不改**（C# 拼的时间戳 `[1年M月] ` 留在冒号之后），
        这样两边都不需要重新解析/改写正文。
        """
        out: List[Dict[str, Any]] = []
        for m in messages:
            if not isinstance(m, dict) or m.get("role") != "user":
                out.append(m)
                continue
            src = m.get("source") if isinstance(m.get("source"), dict) else {}
            kind = src.get("kind")
            if kind == "peer":
                who = str(src.get("speaker") or "").strip()
            elif kind == "mail":
                # 别的 NPC 捎来的话（信箱）：`source.from` = 发信人真名（投递时写入，见 agent/mailbox.py）。
                # 名字缺失也要加前缀（退成"有人托人捎来"）：**绝不能**让它落成无归属的裸消息 ——
                # 那是 `role:user`，模型会把它读成"玩家刚说的话"。
                _from = str(src.get("from") or "").strip()
                who = f"【{_from or '有人'}托人捎来】"
            elif kind == "room":
                # 群内投递的玩家消息（带 uid 锚点）：归属恒为玩家 —— `speaker` 字段是
                # 逻辑身份 `"player"`，不是给人看的名字（真名在 L1 的 `player` 段里）。
                who = "玩家"
            elif kind is None or kind == "":
                who = "玩家"
            else:
                out.append(m)      # initiative / plugin：舞台指令与系统注入不加名字
                continue
            if not who:
                out.append(m)
                continue
            content = m.get("content")
            if not isinstance(content, list):
                out.append(m)
                continue
            # 只在**第一个 text 块**上加前缀（tool-result 块、image 块原样保留）
            # 信箱用【】紧贴正文（`【张三托人捎来】改日来寒舍一叙`），其余用 `说话人：`。
            _head = who if kind == "mail" else f"{who}："
            new_content: List[Any] = []
            done = False
            for b in content:
                if (not done and isinstance(b, dict) and b.get("type") == "text"
                        and isinstance(b.get("text"), str) and b["text"]):
                    nb = dict(b)
                    nb["text"] = _head + b["text"]
                    new_content.append(nb)
                    done = True
                else:
                    new_content.append(b)
            if not done:
                out.append(m)
                continue
            nm = dict(m)
            nm["content"] = new_content
            out.append(nm)
        return out

    def _inline_image_refs(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """把账本里的图片 `ref` 投影成「即将发给 LLM 的那一份」里的 data URI。

        ★ 必须是**浅拷贝 + 新 content 列表**，绝不能原地改 `session.derive_messages()`
        返回的对象 —— 那些就是账本里的事件 data 本体，原地改会把图像字节永久写进
        session.log / JSONL（`save_session` 是全量重写，那一刻整份账本就胖了）。
        与 `_apply_speaker_labels` 是同一套"发前投影"纪律。

        ★ 三条**降级**（都只影响这一份请求，账本一字不动）：
          · 模型明确不支持图片（`llm.supports_image is False`）⇒ 丢块。
            旧实现是让 `openai_client` 抛 `ModelError`，但图片现在进历史了 ——
            一次配置错就会让**之后每一回合都失败**，必须降级成丢图 + 警告。
          · 文件不在（被回收 / 手删 / 存档搬走）⇒ 丢块 + WARNING。
            占位文本 `[图片：xx]` 仍在正文里，模型知道当时有过一张图。
          · `ref` 非法 / 无图片仓 ⇒ 同上。
        没有 `ref` 的 image 块（http(s) 直链那种老形态）原样透传。
        """
        has_image = False
        for m in messages:
            content = m.get("content") if isinstance(m, dict) else None
            if isinstance(content, list) and any(
                    isinstance(b, dict) and b.get("type") in ("image", "image_url") for b in content):
                has_image = True
                break
        if not has_image:
            return messages
        store = getattr(self.session, "image_store", None)
        allow = getattr(self.llm, "supports_image", None) is not False
        if not allow:
            log.warning("模型声明不支持图片输入（supports_image=False）：本次请求丢弃全部图片块"
                        "（账本不动；要看图请换多模态模型或 llm.image.enabled=true）")
        out: List[Dict[str, Any]] = []
        for m in messages:
            content = m.get("content") if isinstance(m, dict) else None
            if not isinstance(content, list) or not any(
                    isinstance(b, dict) and b.get("type") in ("image", "image_url") for b in content):
                out.append(m)
                continue
            new_content: List[Any] = []
            for b in content:
                if not (isinstance(b, dict) and b.get("type") in ("image", "image_url")):
                    new_content.append(b)
                    continue
                if not allow:
                    continue
                ref = b.get("ref")
                if not ref:
                    new_content.append(b)       # 直链老形态：原样透传
                    continue
                uri = store.data_uri(ref, b.get("mime")) if store is not None else None
                if not uri:
                    log.warning("图片读不出来：%s（本次请求丢弃这张图；占位文本仍在上下文里）", ref)
                    continue
                nb = dict(b)
                nb.pop("ref", None)
                nb["url"] = uri
                new_content.append(nb)
            if len(new_content) == len(content) and all(a is b for a, b in zip(new_content, content)):
                out.append(m)
                continue
            nm = dict(m)
            nm["content"] = new_content
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
        #    段清单**动态**（time/self/player/where/other:*/recent），逐个独立差分写屉。
        #    `segs` 的**键集 = 当前应有的段**；键的**顺序 = 发给模型的顺序**（第 4 步沿用）。
        segs: Dict[str, str] = {}
        if self.bridge is not None:
            try:
                # L1 每轮一次：turn 首步（_turn 已把缓存清空）或 movement 置脏后才真正调桥，
                # 其余步复用缓存；逐段差分照旧每步跑（内存比对零成本），轮内 raw 不变即不重发
                if self._turn_ctx is None or self._turn_ctx_dirty:
                    # roster 随取数一起上行：C# 据此知道 `raw.others[]` 里该放谁
                    # （名单的真相在 Python 侧，见 GameBridge.get_context 的 docstring）。
                    # `roster_ids` 与它**同一次上行**：C# 拿它走 unitID 精确分支，
                    # 免得按名全图扫（重名时同席段会挂到别人身上，且**无法察觉**）。
                    # 只影响取数，不影响名单 —— 「所在」段/同席段吃的仍是中文名。
                    # ★ 阶段通知（11-21）：这次调用就是"卡在 C#"的**唯一**入口 ★
                    # 它在真机上可以等满 request_timeout（默认 120s），而玩家屏幕上
                    # 只有一个不动的忙标 —— 分不清是模型慢还是桥卡了。进/出各报一次。
                    # 放在 try/finally 里：超时/异常也要报"离开"，否则忙标会永远停在
                    # 「正在读取人物状态…」。
                    await self._emit_wait_phase("snapshot", True)
                    try:
                        ctx = self.bridge.get_context(self.id, roster=self.roster,
                                                      roster_ids=self.roster_ids,
                                                      npc_unit_id=self._unit_id())
                        if asyncio.iscoroutine(ctx):
                            ctx = await ctx
                    finally:
                        await self._emit_wait_phase("snapshot", False)
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
                    # `scope=self.id`：增量段的发件箱按会话隔离（多个 NPC 各有自己的
                    # 「近况」已发集合；共用会把别人的条目当成"已发过" ⇒ 某人的经历永远缺几条）
                    segs = self.system_prompt.format_l1_context(
                        self.id, raw, roster=self.roster, scope=self.id)
                    # ③-1 离场清理：上一步有、这一步没有的段 → 清**三处**
                    #      （屉 / 屉镜像 `_last_l1` / 模型镜像 `_retained_ctx`）。
                    #   只清镜像不清屉的话，容器里会永久残留旧值（看着像有值、其实没人读），
                    #   而且同席者下次回来时"容器有旧值 + 镜像为空"会让差分的判断错位。
                    #   ★ 为什么不在这里发"撤回"消息：「所在」段**恒非空**（单聊也有自己的
                    #     文案），所以进/出场合两个方向都必然改变它的内容 ⇒ 差分天然通知模型。
                    #     逐段撤回是冗余的第二条路径，见 SystemPrompt.format_l1_context 的 ★。
                    #   ★ 为什么用"上一步键集 − 当前键集"而不是逐处 pop：集合差**自动覆盖所有
                    #     离场路径**（关窗/NPC 死亡/C# 解析失败/压缩折走），不会漏。
                    for name in [n for n in self._last_l1 if n not in segs]:
                        try:
                            self.system_prompt.remove_context(f"runtime:{name}", scope=self.id)
                        except Exception:
                            pass
                        self._last_l1.pop(name, None)
                        self._retained_ctx.pop(name, None)
                    # ③-2 进场 / 变化：写屉 + 更新屉镜像
                    for _i, (name, raw_t) in enumerate(segs.items()):
                        t = (raw_t or "").strip()
                        if t != self._last_l1.get(name):
                            try:
                                self.system_prompt.remove_context(f"runtime:{name}", scope=self.id)
                            except Exception:
                                pass
                            if t:
                                self.system_prompt.context(f"runtime:{name}", t,
                                                           order=self.system_prompt.ctx_order(name, _i),
                                                           scope=self.id)
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
                # ★ 取数失败必须把 segs 清空：否则第 ③-1 步会把"这一轮没算出来的段"误判成
                #   "所有人都离场了"，把整份 L1 状态（境界/心境/好感/日期）连同屉一起抹掉。
                segs = {}
        # 3. 装配 system + contexts + tools（SystemPrompt 每步自检文件+懒建屉）
        assembly = self.system_prompt.assemble(scope=self.id)
        # 4. 上下文逐段差分发送：对标 DSH RuntimeContextProjection.project
        #    每段独立比较，仅变化的段才新增对应 user 消息（不重发整块）；同时把变化段作为
        #    ctx 观察事件上行 UI（step 区内「运行时上下文」块，只发变化段）
        #    遍历 `segs`（当前段清单）而非固定表：离场段已在 ③-1 连屉一起清掉，这里不必再管。
        context_msgs: List[Dict[str, Any]] = []
        ctx_parts: List[Dict[str, Any]] = []
        for name in segs:
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
                    "label": f"L1·{self.system_prompt.ctx_label(name)}",
                    "op": "set" if desired else "remove",
                    "text": desired,
                })
                # 修：空段存 ""（而非 None）—— 否则下一轮同空文本比较 "" != None
                # 永远不等 → 永远 emit remove → UI 永远显示"已移除"（即便段是真没数据）。
                self._retained_ctx[name] = desired
        if ctx_parts:
            # 这一帧只喂 UI（模型看到的上下文是上面那些 plugin 消息）：
            # ★ 群聊里 C# **不显示**它（09-17 用户拍板：多聊时每人一套太乱），
            #   但帧照发 —— `ChatPresenter.OnStepEvent` 收到后仍要 `NoteTurn`（回合号是气泡
            #   去重的判据），只是不渲染。别因为"群里看不见"就把这里的发送删掉。
            await self._emit_step({"kind": "ctx", "turn": turn, "step": step, "parts": ctx_parts})
        # 5. 瀑布桩：此处可插入 AGENTS.md 等插件；简化直接返回
        messages = list(claimed)
        messages.extend(context_msgs)
        return {"kind": "enter", "messages": messages, "assembly": assembly}

    def _ensure_turn_live(self) -> None:
        """回合还活着吗？—— 每个 `await` 返回点都要问一次。

        为什么必须有这一道（11-20 实机 `KeyError: 'step'` 的根治）：
        `cancel()` 会把 `self.phase` 整个换成 idle（`step` 键随之消失）。而循环体可能正卡在
        `await` 里 —— 最典型是 `_pre_step` 的 `get_context` 等 C# 主线程，上限 120s。
        等它回来时 phase 已经换过一轮，于是 `self.phase["step"]` 抛 KeyError，
        整轮以 `aborted` 收口并附一条 traceback —— 而真相只是"玩家在这一刻读了个档"。

        ⇒ 判据用令牌（`_turn_epoch`）而不是"phase 里有没有这个键"：令牌是**主动**声明
        "我不是当前回合了"，比"摸到缺键才反应"早一步，也不会把别的缺键误判成中止。

        ★ 刻意**不**看 `self._abort` ★：那个标志的语义是"取消过"（`cancel()` 只写不清），
        拿它当判据会让"取消过的 agent 永远跑不了下一轮"。令牌自增已完整覆盖中止这件事。

        ★ 无参：任何方法（`_step` 里的 LLM await 同样会跨读档）都能就地自检，
          不必把 epoch 一路透传下去。比较基准是 `_active_epoch`，由 `_turn` 在开跑时快照。
        """
        if self._active_epoch != self._turn_epoch:
            raise TurnAborted("世界重载/回合被取消（等待期间相位已换）")

    async def _turn(self) -> bool:
        if self.phase["kind"] != "running":
            raise RuntimeError("turn without running phase")
        turn = self.phase["turn"] + 1
        # 本回合的令牌快照：整趟 `_turn` 只认它。中途被 `cancel()`（读档/换档/关窗）改掉
        # 就以它为准判"我不是当前回合了"。
        self._active_epoch = self._turn_epoch
        # `scene` 随 turn 落账（场合是**回合级**属性）：UI 回放据此按场合过滤。
        # 回合中途换场合不做特殊处理——本回合整体算旧场合，下一回合生效。
        self.session.append("turn/start", {"turn": turn, "scene": self.scene})
        self.phase["turn"] = turn
        # 新一轮：清 L1 缓存 ⇒ 本轮首步重新取快照（气运/好感/心情等以轮为粒度刷新）
        self._turn_ctx = None
        self._turn_ctx_dirty = False
        # --- turn 边界自动压缩：此刻用户新消息在 inbox 未落面，只动上轮已稳定历史 ---
        if self.compactor is not None:
            try:
                with log_setup.stage("compact", turn=turn):
                    try:
                        report = await self.compactor.maybe(self.session, self.session.request_header())
                    finally:
                        # 摘要调用的账在 finally 记：fail-closed（摘要为空/超时重试）也真花了钱
                        self._record_maintenance_usage()
                # 压缩真发生了 → ①上下文口径立即作废（下面这步的实测会填新值）
                # ②核对 L1 基线：被 replace 折走的段作废，下一步 preStep 重发。
                # 见 _resync_runtime_ctx_baselines 的 ② 段注释（不作废 = 段永久不再发）。
                # 顺序上也正好：上面刚把 _turn_ctx 清空 → 重发的是**最新**状态，不是旧快照。
                if report:
                    self.refresh_context_pressure()
                    lost = self._resync_runtime_ctx_baselines()
                    if lost:
                        log.info("压缩折走 L1 段 %s → 基线已作废，本步将重新注入最新状态",
                                 "/".join(lost))
                    # 把刚产出的纪要推给 UI（10-31）：自动压缩发生在回合内部，玩家看不到
                    # 「正在压缩中」那种提示行，只有这条事件能让折叠行出现在本回合内容之前。
                    # 顺序关键：此刻**还没有**任何 step 帧发出，所以它在屏幕上正好排在前面。
                    await self._emit_compact_summary(turn)
            except Exception:
                # 压缩失败不该打断回合，但绝不能无声——它是"上下文爆了/越聊越慢"的常见原因。
                log.warning("回合边界自动压缩失败（本回合按未压缩继续）", exc_info=True)
        turn_ends = None
        target = "next-turn"
        try:
            while True:
                # ★ 每个 await 之后都要重新确认"我还是当前回合"（见 `_ensure_turn_live`）★
                # 这一句本身也要：`compactor.maybe` 上面那个 await 同样可能跨越一次读档。
                self._ensure_turn_live()
                step = self.phase["step"] + 1
                decision = await self._pre_step(target, turn, step)
                # ← 这里就是 11-20 抛 KeyError 的位置：`_pre_step` 里等过 get_context。
                self._ensure_turn_live()
                if decision["kind"] == "reject":
                    turn_ends = {"kind": "blocked"}
                    return False
                if turn_ends and not decision["messages"]:
                    break
                if self.phase["step"] == 0 and not decision["messages"]:
                    turn_ends = {"kind": "completed"}
                    return False
                # 开 step
                self.session.append("step/start", {"turn": turn, "step": step})
                self.phase["step"] = step
                try:
                    for m in decision["messages"]:
                        # 存历史
                        self.session.append("user/message", m, {"surfaceOp": "append"})
                    step_end = await self._step(decision["assembly"])
                    self._ensure_turn_live()
                    if turn_ends is None or turn_ends.get("kind") != "max-tokens":
                        turn_ends = step_end
                finally:
                    self.session.append("step/end", {"turn": turn, "step": step})
                if turn_ends and not self.inbox.next_step:
                    break
                target = "next-step"
        except TurnAborted as e:
            # 中止不是故障：账本照常闭合（下面 finally 落 `turn/end:aborted`），但不打 traceback
            # —— 读档打断一个在等的回合是**正常**的，堆栈只会让下一个人以为出了大事。
            #
            # ★ 必须**继续上抛**，不能 `return False` ★：`run_until_idle` 拿到 False 之后会看
            #   `inbox.has_pending`，有 pending 就再起一个 turn —— 而这个 agent 已经被
            #   `discard_all` 摘出名册了，再跑一轮等于对着一个已作废的对象记账。
            #   上抛后 `run_until_idle` 的 except 只会把相位自愈成 idle 再抛，不会另起回合。
            turn_ends = {"kind": "aborted", "reason": str(e)}
            raise
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
                # 同 ctx：帧照发（C# 要 `NoteTurn`），但群聊不渲染（见上一条注释）。
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
        # ★ 发请求**之前**的协议自检（11-15，用户要求「检测到这样的情况就去删除那个请求的」）★
        # 放在这里而不是"发失败之后再修"：
        #   · 先修 = 这个请求就发得出去，玩家这一轮能正常聊；
        #   · 后修 = 那一轮已经废了（400 抛给玩家看），修好也只能救下一轮。
        # 判据只看账本形状（`find_orphan_tool_calls`），与"坏形状是怎么来的"无关 ——
        # 取消、异常逃逸、历史只删了一半、今后任何新分支漏写，都被这一道兜住。
        self._heal_tool_pairing(where=f"turn={turn} step={step}")
        # 图片进上下文（10-18）：账本里存的是 ref（字节在存档图片仓），发给模型前投影成
        # data URI。derive_messages() 返回的是**账本事件 data 本体**，所以必须走浅拷贝投影
        # （_inline_image_refs），不能原地改 —— 原地改会把 base64 永久写进账本。
        # 说话人前缀同理走投影（`_apply_speaker_labels`）：账本保持玩家真输入原文，
        # 只有"发给模型的这一份"带 `玩家：` / `李四：`。
        messages = self._apply_speaker_labels(
            self._inline_image_refs(self.session.derive_messages()))

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
            # ★ 首个 token 到达 = 阶段从"等首字"切到"正在吐字"（11-21）★
            # 判据用 `_first_token_t` 是否为空，与 ttft 计时**同源** —— 两处各记一次
            # "首字到了"就会漂（一个算思考流、一个算正文流，实测思考型模型差 3~8 秒）。
            if tok and not _first_token_t:
                _first_token_t.append(_time.monotonic())
                # 首字到了：think → stream。**只报一次**（两个回调共用 `_first_token_t`，
                # 所以这段只会有一个赢家，不会两边各报一次）。
                await self._emit_wait_phase("think", False)
                await self._emit_wait_phase("stream", True)
            if _outer_on_token is not None:
                r = _outer_on_token(tok)
                if asyncio.iscoroutine(r):
                    await r

        async def _timed_on_reasoning(tok: str):
            if tok and not _first_token_t:
                _first_token_t.append(_time.monotonic())
                await self._emit_wait_phase("think", False)
                await self._emit_wait_phase("stream", True)
            if _outer_on_reasoning is not None:
                r = _outer_on_reasoning(tok)
                if asyncio.iscoroutine(r):
                    await r

        with log_setup.stage("llm", turn=turn, step=step):
            # 进 LLM = 开始等首字。出（无论成功/异常）都要报"离开"，否则异常路径下
            # 忙标会永远停在「对方正在斟酌…」——而那正是玩家以为"卡死"的样子。
            await self._emit_wait_phase("think", True)
            try:
                result = await self.llm.generate(system, messages, tools,
                                                 on_token=_timed_on_token,
                                                 on_reasoning=_timed_on_reasoning)
            except Exception as _llm_err:
                # ★ 400 invalid_request_error 的最后一道兜底（11-15）★
                # 发请求**前**已经自检过一次（`_heal_tool_pairing`），所以走到这里通常
                # 不是配对问题（可能是别的格式错、鉴权、余额）。但如果这次错**正是**
                # 配对类 400，那说明自检漏了某种形状 —— 再修一次并留下明确的日志，
                # 让下一轮能自愈（否则这一整条对话从此永久 400，不会自愈）。
                if self._looks_like_tool_pairing_error(_llm_err):
                    healed = self._heal_tool_pairing(where="400 兜底")
                    log.error("服务端报了 tool 配对 400，已就地再修 %d 条 —— 这属于自检漏网，"
                              "请把这段日志发我（说明 `find_orphan_tool_calls` 有未覆盖的形状）",
                              healed)
                raise
            finally:
                # 无论成功/超时/异常都要退出阶段：留着它们会让忙标停在一个已经不成立的
                # 描述上（"正在回话"却没有字进来）。
                # ★ 只报"确实进入过"的那个 ★：没等到首字就异常时只有 think 进过，
                #   此时再补一条 stream 的离开是噪声（C# 侧虽会忽略不匹配的离开帧，
                #   但日志里会出现"没进过就离开"的怪序列，排障时误导）。
                if _first_token_t:
                    await self._emit_wait_phase("stream", False)
                else:
                    await self._emit_wait_phase("think", False)
        # ← 这次 await 同样会跨读档（LLM 最长 240s，日志里就有）：回来先验一次，
        #   否则下面会拿着 result 往一个已作废的账本里写 assistant/message。
        self._ensure_turn_live()
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

    async def _emit_compact_summary(self, turn: int) -> None:
        """自动压缩后把纪要推给观察者（**只在自动路径**；手动路径由 `ws_channel` 自己发）。

        走的是既定的 `on_step` 出口而不是新开一个回调：`on_step` 是"回合内发生的事实"的
        唯一观察者通道，且它自带 try/except（推不出去只记 debug，不打断回合）。
        `kind="compact"` 由 `ws_channel._on_step` 翻译成 wire 事件 `compact_summary` ——
        agent 不认识事件名，与 step/text_delta 同一条分层规矩。
        """
        try:
            info = compact_summary.latest(self.session)
        except Exception:
            log.warning("自动压缩后取纪要失败（UI 看不到这次纪要，历史重放仍可见）", exc_info=True)
            return
        if not info:
            return
        await self._emit_step({
            "kind": "compact",
            "compaction_id": info["compaction_id"],
            "text": info["text"],
            "chars": info["chars"],
            "editable": info["editable"],
            "lock_reason": info["lock_reason"],
            "source": "auto",
            "turn": int(turn),
        })

    @staticmethod
    def _looks_like_tool_pairing_error(err: Any) -> bool:
        """这个异常是不是「tool-call 配对不完整」那类 400？

        判据刻意宽松（只看文案特征）：目的是"宁可多修一次，也别让对话永久卡死"。
        多修一次的代价是删掉一条本就坏掉的节点（幂等、零副作用）；
        而漏判的代价是这个 NPC **从此再也不回话**（每一轮都 400，且不会自愈）。
        """
        try:
            msg = str(err).lower()
        except Exception:
            return False
        markers = (
            "must be followed by tool messages",
            "insufficient tool messages",
            "tool_call_id",
            "tool_calls",
        )
        return any(m in msg for m in markers)

    def _heal_tool_pairing(self, where: str = "") -> int:
        """协议自愈：**删掉**破坏 tool-call/result 配对的那几条，返回删了几条。

        用户的要求（11-15）：「检测到这样的情况的话去删除那个请求的」。
        为什么是删而不是补假结果：
          · 那条 assistant 是个**没完成的动作**，补一条"这次调用没完成"等于给模型
            喂一段它没真正得到的结果，它会据此继续演（"我刚才查过了…"）；
          · 删掉之后模型看到的是「上一句我说了话，这次重新说」——干净；
          · 而且这是**账本只追加**语义下的合法操作（落 `history/delete` 注记），
            与玩家手动删历史走同一条路（`SurfaceManager._apply_delete` 机械剔除）。

        触发点：`_step` 发请求**之前**（先修 ⇒ 这一轮就能正常发出去）。
        幂等：修完再调返回 0（`find_orphan_tool_calls` 对合法账本返空）。

        ## 成本（11-15 答复"是不是每次都全遍历"）

        **绝大多数 step 是 O(1)**：`surface.pairing_check_needed()` 先看脏标记 ——
        只有「删过历史 / 折叠过节点 / 距上次全扫已过 64 次」才真扫。理由：配对损坏
        **只可能由改动目录的动作产生**，而 `append` 永远不会让已有配对失效
        （一条带 tool-call 的 assistant 刚落下时本来就等着回包，那一刻不算坏）。
        实测全扫本身也便宜（1000 轮账本 ≈ 3.3ms，而一个 step 要等 LLM 2~10 秒），
        但它是 O(n) 且账本只会越来越长，所以用脏标记把常态成本压到常数。
        """
        try:
            surface = self.session.surface
            # O(1) 快路径：没动过目录就不扫。坏形状只可能由 history/delete 或 replace
            # 产生，两者都会标脏 ⇒ 真有坏账本时这里必然是 True。
            if not surface.pairing_check_needed():
                return 0
            nodes = list(surface.nodes)
            bad = find_orphan_tool_calls(self.session.log, nodes)
            if not bad:
                surface.mark_pairing_checked()
                return 0
            # 只允许删 **surface 成员**（这里 find_orphan 本来就只扫 surface，双保险）
            bad = [s for s in bad if s in nodes]
            if not bad:
                surface.mark_pairing_checked()
                return 0
            self.session.append(
                "history/delete",
                {
                    "ranges": [],
                    "drop_seqs": sorted(bad),
                    "keep_seqs": [],
                    "turns": [],
                    "reason": "tool-pairing-heal",
                },
            )
            # 落 history/delete 本身会标脏（`_apply` 里那条），这里清掉：
            # 刚刚那次扫描就是基于**已修好**的目录算出来的，下一 step 不必再扫一遍。
            surface.mark_pairing_checked()
            kinds = "、".join(f"{s}:{self.session.log[s].get('type')}" for s in sorted(bad))
            log.warning("协议自愈：删掉 %d 条破坏 tool-call 配对的节点（%s）——%s。"
                        "不删它们的话，下一次请求会被服务端 400 拒绝"
                        "（An assistant message with 'tool_calls' must be followed by ...）",
                        len(bad), kinds, where or "发请求前")
            return len(bad)
        except Exception:
            # 自愈失败绝不能把回合再搞挂一次；但必须留痕 —— 它意味着这次请求很可能 400
            log.error("协议自愈失败（本次请求可能被 400 拒绝：tool-call 配对不完整）", exc_info=True)
            return 0

    async def _emit_wait_phase(self, phase: str, entering: bool):
        """观察者出口：报一次"现在在等什么"。失败只记录，绝不打断 turn。

        与 `_emit_step` 同一条分层规矩（agent 不认识事件名，只吐事实），但**不进账本**：
        阶段是当下状态，不是发生过的事。UI 侧据此把忙标文案从「对方正在斟酌…」换成
        「正在读取人物状态…」/「正在回话…」，让玩家分得清"在等谁"。
        """
        if self.on_wait_phase is None:
            return
        try:
            r = self.on_wait_phase(phase, entering)
            if asyncio.iscoroutine(r):
                await r
        except Exception:
            log.debug("阶段观察者推送失败（phase=%s）", phase, exc_info=True)

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
            # ★ 模型自带的 harness 专用键（`*_id` / `initiator*`）**一律抹掉**再注入。
            #   理由：C# 侧 id 优先于名字，模型一旦自己写了一个 id，它就会压过它自己写的
            #   名字，而失败形态是"落到另一个人身上、success 仍为真"（静默错人）。
            #   这些键不在任何 schema 里，正常模型不会写；清掉是纯收紧。
            try:
                _stripped = unit_refs.strip_reserved_args(args)
                if _stripped:
                    log.warning("工具 %s：模型自带了 harness 专用参数 %s，已抹掉重建",
                                name, "、".join(_stripped))
            except Exception:
                pass
            # 注入 initiator：发起方恒为当前对话 NPC（Agent.id），C# 以此当 actor，target 已删
            try:
                if "initiator" not in args:
                    args["initiator"] = self.id
                # ★ `initiator_id`：同一件事的**精确身份**（发起方自己的 unitID）。
                #   它**不来自模型**（不在任何工具 schema 里，模型从来看不到它），
                #   所以"模型侧只出现中文名"这条纪律完全不受影响。
                #   C# 拿到它就走 `ResolveEx(name, id)` 的 ②unitID精确 分支 —— 从此
                #   "发起方是谁"不再按名猜（重名时猜错**不报错、不缺字段**，只是把动作
                #   落到另一个人身上）。
                #   来源：群聊 = 该群的身份表；单聊 = `open_chat` 时 C# 告知的。
                #   取不到就**不发这个键** —— 缺失 = 回落按名，与改动前逐字一致。
                # ★ 09-28 修（真机「未找到发起方 管星贤」）★
                #   会话没记住自己的身份时补查**世界级名字→id 表**。为什么会有这种会话：
                #   主动传音 / 剧情窗这条入口**不经过** `open_chat`（那里 C# 才随请求
                #   带 unit_id），所以 `agent.unit_id` 恒空 —— 而这条注入线原先**只读
                #   `self.unit_id`**，明明表里有 `管星贤 → 8DQUkF` 也没用上（而
                #   `target/seller/buyer` 那条线是查表的 ⇒ 同一会话里"对别人"有 id、
                #   "对自己"没有，这个不对称就是病根）。
                #   这里复用 `_lookup_unit_id` —— 与指人参数**同一个查表口、同一口径**
                #   （"永不猜"：表里没有仍是空串 ⇒ 不注入 ⇒ C# 回落按名，逐字同旧行为）。
                uid = self._unit_id()
                if not uid:
                    uid = self._lookup_unit_id(self.id)
                    if uid:
                        log.info("发起方身份取自名字映射表：%s → %s（本会话未建立自身身份）",
                                 self.id, uid)
                if uid and "initiator_id" not in args:
                    args["initiator_id"] = uid
            except Exception:
                pass
            # ★ 指人参数的**精确身份**（模型看不到 `<键>_id`）：`target/seller/buyer`
            #   从临时局部映射表翻出来。表里有就注入、没有就不注入（C# 回落按名）。
            #   放在闸门**之前**：闸门的判据吃的是中文名（本函数不改正名，只拆显示名信封）。
            self._inject_person_ids(name, args)
            call_id = tc.get("id") or _new_id()
            # 落 tool/call（非 surface，derive 不投影）
            self.session.append("tool/call", {"turn": turn, "step": step, "callId": call_id, "name": name, "arguments": json.dumps(args, ensure_ascii=False)})
            if self.on_step is not None:
                await self._emit_step({"kind": "tool_call", "name": name, "args": args, "turn": turn, "step": step})
            # 忙碌回合兜底：本回合只许言语+只读。
            # ★ 工具表**恒定**，裁剪发生在这一层而不是装配层（10-16 更正：此前本注释误写
            #   "装配层已把动作工具从工具表剔除"，实际 `system_prompt.assemble()` 全程无过滤）。
            #   不裁剪的原因是前缀缓存：工具表进 `request/header` 的 hash，按忙闲增删会让
            #   header 每轮变一次 ⇒ 服务商前缀缓存全废。
            #   所以模型**看得到全部 9 个工具、也真的会调**，这里拦的是"别真的去改世界/弹原生窗"
            #   —— 历史里残留的调用/模型幻觉都不该生效。
            # 落账形状与下方正常路径**逐字同构**（canonical message + surfaceOp + sourceEventSeqs），
            # 否则 derive_messages 不会产生 tool 结果消息，模型看不到拒绝会反复重试。
            if self.speech_only_turn or self.no_tools_turn:
                try:
                    from ..tools.schemas import ACTION_TOOLS as _ACTION_TOOLS
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
            # ★ 参数**结构**自检（09-27 真机事故）★：模型偶发把数组写成字符串（还会混进
            #   `<parameter=target>` 那类 XML 标签 —— 原始帧与事故链见 `tools/schemas.shape_error`）。
            #   这类调用进桥只有坏结局：C# 报一句笼统的"缺少 items"，或更糟 —— 撞上解析崩溃而
            #   **整帧丢失**（真机 09-27：还坐骑那次 Python 只等到 120s「请求超时」，NPC 随后把
            #   没办成的事讲成了"已交割"）。出站前拦下：不进桥（零副作用、零等待），回一条
            #   **能自纠**的错误；落账形状与上面两道闸逐字同构（canonical message + surfaceOp）。
            _shape_err = ""
            try:
                from ..tools.schemas import shape_error as _shape_error
                _shape_err = _shape_error(name, args)
            except Exception:
                log.debug("工具参数结构自检异常（按合格放行）：%s", name, exc_info=True)
            if _shape_err:
                res_s = {"success": False, "error": _shape_err}
                log.warning("工具 %s 参数结构不合格，已拦下（未进桥）：%s", name, _shape_err)
                text_s = text_render.render(name, args, res_s) or json.dumps(res_s, ensure_ascii=False)
                tool_msg_s = llm_adapter.create_canonical_tool_result_message(call_id, text_s, is_error=True)
                call_seq_s = next((ev["seq"] for ev in reversed(self.session.log)
                                   if ev.get("type") == "tool/call" and ev["data"].get("callId") == call_id),
                                  len(self.session.log) - 1)
                self.session.append("tool/result", {"message": tool_msg_s},
                                    {"surfaceOp": "append", "sourceEventSeqs": [call_seq_s]})
                if self.on_step is not None:
                    await self._emit_step({"kind": "tool_result", "tool": name, "text": text_s,
                                           "turn": turn, "step": step})
                return call_id
            # ★ 群聊策略闸门（提案制）：由 `RoomFlow._one_speaker` 注入，单聊恒为 None（一行不跑）。
            # 返回 dict = 短路：登记提案 / 拦下（判据与副作用都在闸门里，这里只管落账）。
            # 落账形状与上方忙碌闸**逐字同构**（canonical message + surfaceOp + sourceEventSeqs），
            # 否则 derive_messages 不会产生 tool 结果消息，模型看不到反馈会反复重试。
            _gate_after = None
            if self.tool_gate is not None:
                _override = None
                try:
                    _override = self.tool_gate(name, args)
                    if asyncio.iscoroutine(_override):
                        _override = await _override
                except Exception:
                    # 闸门自身异常绝不拖垮回合：如实记，按"放行"继续（宁可多做，不可默默不做）
                    log.exception("群聊闸门异常（已放行到 bridge）：%s", name)
                    _override = None
                # 放行 + 事后收尾（提案制"应下"）：**不是**短路 —— 本调用照常走 bridge，
                # 结果回来后由 `after(res)` 决定"公开叙述行"还是"私信发起人"（room_flow）。
                if isinstance(_override, dict) and _override.get("__pass_after__"):
                    _gate_after = _override.get("after")
                    _override = None
                if _override is not None:
                    res_g = _override
                    is_err_g = not bool(res_g.get("success", True)) if isinstance(res_g, dict) else False
                    text_g = text_render.render(name, args, res_g) or json.dumps(res_g, ensure_ascii=False)
                    tool_msg_g = llm_adapter.create_canonical_tool_result_message(call_id, text_g, is_error=is_err_g)
                    call_seq_g = next((ev["seq"] for ev in reversed(self.session.log)
                                       if ev.get("type") == "tool/call" and ev["data"].get("callId") == call_id),
                                      len(self.session.log) - 1)
                    self.session.append("tool/result", {"message": tool_msg_g},
                                        {"surfaceOp": "append", "sourceEventSeqs": [call_seq_g]})
                    if self.on_step is not None:
                        await self._emit_step({"kind": "tool_result", "tool": name, "text": text_g,
                                               "turn": turn, "step": step})
                    log.info("工具 %s 被群聊闸门短路（success=%s）", name,
                             bool(res_g.get("success", True)) if isinstance(res_g, dict) else None)
                    return call_id
            # 调桥（唯一认识 g.world 的口子）
            import time as _ttime
            _tool_start = _ttime.monotonic()
            if self.bridge is not None:
                try:
                    # ★"正卡在工具上"的对外信号（09-23）：信箱补投据此把等待上限从 30s 放到 180s
                    #   （见 `ChatHub.DRAMA_TTL_TOOL_SECONDS`）。工具长等是**有结局的**等
                    #   （非心跳槽 90s、跨战斗靠心跳到 600s），与"回合在生成"不是一回事。
                    #   finally 保证异常路径也摘掉它 —— 否则"永远显示在等工具"会把信箱放成 180s。
                    self.tool_inflight = name
                    try:
                        with log_setup.stage(f"tool.{name}", turn=turn, step=step):
                            # npc_id 随帧带：C# 侧据此把"动作完成"归属到本 NPC →
                            # 同格动作完成时自动打开对话 UI（ActionWatcher）
                            res = self.bridge.call_tool(name, args, npc_id=self.id)
                            if asyncio.iscoroutine(res):
                                res = await res
                    finally:
                        self.tool_inflight = None
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
            # ★ 捎话的**真正落点**（11-12）：C# 只解析了收信人、把台词原样带回；
            #   往**别人账本**里追加这一句的是这里（`agent/mailbox.py` 文件头解释了分工）。
            #   为什么放在 `_harvest_ids` 之前：投递失败要把 res 改判成失败帧，而渲染层
            #   只看 `success`（`text_render.render` 对 success:false 一律返回 None →
            #   模型读到 error 原话）。**绝不静默**：说"已捎到"而其实没投，模型下一句
            #   就会编出"他答应了"，而对方账本里一个字都没有。
            self._settle_mail(name, args, res)
            # ★ 收人名引用（渲染**之前**）：查询结果里带的 `(名字, unitID)` 进临时映射表，
            #   需要区分同名者时同时把**唯一化显示名**写进载荷供渲染。见 `_harvest_ids`。
            self._harvest_ids(name, res)
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
            # ★ 闸门的事后收尾（提案制"应下"）：**落刀成功才对外宣告**（公开叙述行进
            #   `Room.log`，全群可见、各成员 session 里也收得到）；失败时既不虚报"已成"、
            #   也不吞掉 —— 由 room_flow 私信告知发起人。钩子异常不改判结果，如实记日志。
            if _gate_after is not None:
                try:
                    _r = _gate_after(res)
                    if asyncio.iscoroutine(_r):
                        await _r
                except Exception:
                    log.exception("群聊闸门事后钩子异常（结果不改判）：%s", name)
            return call_id

        # 限并发（对标 DSH maxParallelToolCalls 滚动池，简化为信号量）
        sem = asyncio.Semaphore(self.max_parallel_tools)

        # ★ 已落 `tool/result` 的判定不靠这个循环，靠 `_backfill_missing_tool_results`
        #   直接扫 surface（判据与"怎么丢的"解耦：取消、异常逃逸、任何 early-return
        #   漏写都会被同一道兜住）。这里只负责限并发。
        async def _one_limited(tc):
            async with sem:
                return await _one(tc)

        # ══ 孤儿 tool_call 兜底（11-15）══
        # 为什么必须有：`asyncio.gather` 里**任意一个**工具抛 `CancelledError`（玩家关窗、
        # 换档、连接重建、回合被取消）会让 gather 整体退出，**其余工具的结果就此丢失** ——
        # 而 assistant 消息（含它们的 tool_calls）早就落过 surface 了。
        # 下一次请求把它原样发出去，服务端直接 400：
        #   "An assistant message with 'tool_calls' must be followed by tool messages
        #    responding to each 'tool_call_id'. (insufficient tool messages ...)"
        # 这种残局**不会自愈**：那条 assistant 永久留在账本/surface 里，之后每一回合都 400，
        # 玩家表现为「这个 NPC 从此不回话了」，而日志里只有一条 invalid_request_error。
        await asyncio.gather(*[_one_limited(tc) for tc in tool_calls], return_exceptions=True)
        self._backfill_missing_tool_results(tool_calls, turn, step)
        # 工具后不收口，让 turn 继续下一 step（模型会看 tool/result 再决策）
        return None

    def _backfill_missing_tool_results(self, tool_calls: List[Dict[str, Any]],
                                       turn: int, step: int) -> int:
        """把**没拿到 tool/result 的 tool_call** 补成一条 error 结果（协议自愈）。

        为什么是"补一条 error"而不是"把那半条 assistant 撤掉"：
          · 账本**只追加**（本项目铁律，`docs/plan-compaction-view.md` §2）：撤回要新增一种
            改写操作，而它会在存档/压缩/回放三条路上各引出一批新边界；
          · 补 error 让模型看到"这个工具挂了"，它的正常反应就是换个说法或重试 —— 语义正确；
          · 判据只看**surface 里有没有对应的 toolCallId**，与"怎么丢的"无关：取消、
            异常逃逸、今后新增的任何 early-return 漏写，都会被这一道兜住。

        返回补齐条数（0 = 本来就完整，正常路径零开销）。
        """
        try:
            # 这一刻 surface 末尾就是本 step 落的那些节点；只扫尾部能避免 O(全账本)
            got: set = set()
            for seq in reversed(self.session.surface.nodes):
                ev = self.session.log[seq]
                t = ev.get("type")
                if t == "tool/result":
                    for b in ((ev.get("data") or {}).get("message") or {}).get("content") or []:
                        if isinstance(b, dict) and b.get("toolCallId"):
                            got.add(str(b["toolCallId"]))
                    continue
                # 走到上一条 assistant 就停：再往前是上一 step 的配对，与本次无关
                break
            missing = [tc for tc in (tool_calls or [])
                       if str(tc.get("id") or "") and str(tc.get("id")) not in got]
            if not missing:
                return 0
            for tc in missing:
                call_id = str(tc.get("id") or "")
                name = tc.get("name") or "?"
                res = {"success": False,
                       "error": "这次调用没能完成（回合被中断/取消，或工具链异常），结果不可得。"}
                text = (text_render.render(name, dict(tc.get("arguments") or {}), res)
                        or json.dumps(res, ensure_ascii=False))
                msg = llm_adapter.create_canonical_tool_result_message(call_id, text, is_error=True)
                call_seq = next((ev["seq"] for ev in reversed(self.session.log)
                                 if ev.get("type") == "tool/call"
                                 and ev["data"].get("callId") == call_id), None)
                self.session.append("tool/result", {"message": msg},
                                    {"surfaceOp": "append",
                                     **({"sourceEventSeqs": [call_seq]} if call_seq is not None else {})})
                log.warning("补写缺失的工具结果（协议自愈）：call=%s tool=%s", call_id, name)
            log.warning("本 step 有 %d 个工具没回包，已补成 error 结果（否则下一请求会被 400 拒绝）",
                        len(missing))
            return len(missing)
        except Exception:
            # 兜底自身失败绝不能把回合再搞挂一次 —— 但必须留痕（它意味着协议仍不完整）
            log.error("补写缺失工具结果失败（协议可能仍不完整，下一请求或被 400 拒绝）", exc_info=True)
            return 0

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

    # ---------- 用量口径：维护调用 + 上下文规模 ----------
    def _record_maintenance_usage(self) -> int:
        """把压缩器本次（含重试 / fail-closed / pruner）的维护调用用量记进 stats 与账本。

        ★ 必须在 `finally` 里调：这些调用**本身**是真实扣费（摘要那次是整前缀、全会话
          最贵的一次），哪怕最后"摘要为空 → fail-closed 不改历史"，钱也已经花了。
          以前只取 result.text、usage 直接丢 → "总 token" 每压一次少算一整份上下文。

        ★ 账本记录**在这里**统一写（`dev/maintenance-usage`），不挂在 `compaction/summary`
          上：那条审计只在"真的压成了"时才写，而账要覆盖"重试/没压成/工具摘要"所有路径。
          于是统计的重放路径（`rebuild_from_log`）与活体路径看到的是同一份数据，
          两处各记一次不会重复（重放只在构造时跑）。
        """
        if self.compactor is None:
            return 0
        try:
            pending = self.compactor.take_pending_usage()
        except Exception:
            return 0
        n = 0
        kept: List[Any] = []
        for raw in pending:
            try:
                if self.stats.add_maintenance_usage(raw):
                    n += 1
                    kept.append(raw)
            except Exception:
                continue
        if kept:
            try:
                self.session.append("dev/maintenance-usage", {"reason": "compact", "usage": kept})
            except Exception:
                # 落账失败只影响"重启后的总量"（本次会话内的数已经记上了），必须留痕
                log.warning("维护用量落账失败（重启后这部分总量会少算）", exc_info=True)
            log.info("压缩调用计入用量：%d 笔（维护桶；不动上下文压力）", n)
        return n

    def refresh_context_pressure(self) -> None:
        """压缩/删除后刷新上下文口径：同步 surface 规模 + **作废**上一次实测值。

        为什么必须做：`pressure_tokens` 是上一次请求的实测值，压缩/删除把 surface 改小了
        它就不再代表下一次请求 ⇒ 面板上的「上下文」看着像"压了没效果／删了没变小"。
        作废（而不是估一个）是刻意的：估错同样误导，而"—"只表示"还没测"，下一次调用自动恢复。

        `set_surface` 喂的估算与压缩器选段用的是同一把尺子（`compaction.meter`），
        因为 projected = pressure + (当前 surface − 采样时 surface) 只有在同单位下才成立
        —— 这个推算此前是**死代码**（全仓没人调 set_surface，projected 恒等于 pressure）。
        """
        from ..compaction import meter
        try:
            header = None
            try:
                header = self.session.request_header()
            except Exception:
                header = None
            # 走 `Session.estimate_context_tokens`（带增量缓存）：与逐条 sum 逐字等价，
            # 但没变的消息不再重算。它是热路径（压缩判据每回合都问），
            # 而整份历史几十万字时逐条重算要几十毫秒（11-15「用久了会卡」的主因之一）。
            est = self.session.estimate_context_tokens(meter.estimate_header(header))
            live = live_turn_count(self.session)
            self.stats.set_surface(est, live)
        except Exception:
            log.warning("上下文规模刷新失败（沿用旧值/旧轮数）", exc_info=True)
            return
        self.stats.invalidate_pressure()

    # ---------- 手动压缩入口 ----------
    async def compact_now(self) -> Optional[Dict[str, Any]]:
        """手动压缩：仅 idle（turn 外）可由玩家指令/UI 触发。返回 None 表示未执行。"""
        if self.compactor is None:
            return None
        if self.phase["kind"] != "idle":
            raise RuntimeError("compact_now 只能在 idle（turn 外）时调用")
        try:
            report = await self.compactor.compact_now(self.session, self.id)
        finally:
            self._record_maintenance_usage()      # 失败也要记（见方法注释）
        # 手动压缩是**另一个入口**（/compact、面板按钮），同样要把被折走的 L1 段基线作废
        # 漏了这里就会出现"自动压缩能自愈、手动压缩之后 L1 永久丢失"这种只在一条路上发作的怪相。
        if report:
            self.refresh_context_pressure()       # 上下文被压小了：旧实测值立即作废
            lost = self._resync_runtime_ctx_baselines()
            if lost:
                log.info("手动压缩折走 L1 段 %s → 基线已作废，下一回合重新注入最新状态",
                         "/".join(lost))
        return report

    # ---------- 生命周期 ----------
    def cancel(self):
        self.inbox.clear()
        # 筐里的图随消息一起作废 ⇒ 顺手回收（10-18 复核补的第三个触发点）。
        # 为什么需要：`inbox.clear()` 把"还没轮到处理"的消息整条丢掉，那些图从此没有任何引用，
        # 而回收点原先只有"删除/压缩"——玩家若不删不压就永远滞留在存档目录里。
        # 安全性：`clear()` 已经落了 `agent/inbox/spliced` 的移除事件，所以此刻重放出来的筐是空的，
        # keep-set 只剩 surface（= 模型真正还看得到的那些图），不会误删在用的图。
        try:
            from ..storage.images import gc_session
            gc_session(self.session, why="cancel")
        except Exception:
            log.warning("取消后图片回收失败（不影响取消本身）", exc_info=True)
        self._abort = True
        # ★ 令牌自增 = 宣告"在跑的那个回合已经不是当前回合了" ★
        # 循环体在下一个 await 返回点验到不一致就抛 `TurnAborted`，不再往下摸 `self.phase`。
        # 这一句必须在下面对 phase 赋值**之前**：反过来的话，若恰好在两行之间被切走，
        # 循环体会看到"phase 已换、令牌还是旧" ⇒ 它继续跑并读到缺键的 phase（本 bug 的原样）。
        self._turn_epoch += 1
        self.phase = {"kind": "idle", "last_turn": self.phase.get("turn", self.phase.get("last_turn", 0))}

    def dispose(self):
        self.cancel()
        # 清 SystemPrompt 屉（分层分权：DialogueAgent 只清自己的屉，不碰全局）
        self.system_prompt.dispose_scope(self.id)
