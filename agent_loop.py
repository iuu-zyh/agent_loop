"""AgentLoop — 管家（只管名册+锁+续档，不碰屉）"""

from __future__ import annotations

import threading
from typing import Dict, List, Optional

from .room.room import Room
from .session.session import Session
from .agent.system_prompt import SystemPrompt
from .agent.dialogue_agent import DialogueAgent
from .storage.unit_id_map import UnitIdMapService
from .storage.persistence import (load_session, save_session, session_path_for, world_root, sanitize_world_id,
                          load_room, load_rooms_index, load_retired_rooms, delete_room_file,
                          save_room, save_rooms_index, rooms_root_for)
from . import log_setup

log = log_setup.get_logger(__name__)


class AgentLoop:
    """进程单例管家：名册 + 锁 + 续档。屉是 SystemPrompt 私产，只递 npc_id"""

    _instance: Optional["AgentLoop"] = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self, storage_root: str = "~/.sessions", bridge=None, llm=None, compactor=None, max_parallel_tools: int = 5, context_window: int | None = 200000, require_world: bool = False):
        if getattr(self, "_initialized", False):
            # 单例已初始化：配置已在首次生效。若本次又显式传了非 None 的 bridge/llm/compactor，
            # 说明调用方误以为能重新注入 → fail-fast，避免静默丢配置（曾导致工具走假 stub）。
            if bridge is not None or llm is not None or compactor is not None:
                raise RuntimeError(
                    "AgentLoop 是单例，初始化后不能再注入 bridge/llm/compactor。"
                    "请用 AgentLoop.reset_for_test(storage_root=..., bridge=..., llm=..., compactor=...) 重置后再构造。"
                )
            return
        self._initialized = True
        import os as _os

        self.storage_root = _os.path.expanduser(storage_root)   # 支持 ~ 展开（config 默认 ~/.sessions）
        # 存档命名空间：world_id = 玩家 unitID（C# load_happened/save_happened 携带）。
        # 为空 → 扁平布局（storage_root）：测试 / chat_cli 独立实例 / 尚未进世界。
        # 非空 → storage_root/worlds/<world_id>/：换存档不再读到上一个存档的名单与历史。
        self.world_id: str | None = None
        self.player_name: str | None = None
        # ★ 未定档时**禁止读写扁平根**（09-18「世界身份丢失」修复的第二道闸）。
        #   生产形态（C# 拉起 → 下发 `AGENT_LOOP_PPID`）置 True；测试 / `chat_cli` / 手跑
        #   `server.py` 保持 False —— 扁平布局对它们是**正常语义**（见 `persistence.world_root`），
        #   一刀切会把开发链路也堵死。
        #   为什么需要它：`world_id` 只活在进程内存里（故意不落盘——存一个过期的存档身份
        #   比没有更危险）。Python 重启后它为空，而 C# 侧补发身份要等下一次连接；那一刻的
        #   读写若落进扁平根，就与"不属于任何存档"的旧文件混成一桶（跨档串号），
        #   而且下一次存档会把从扁平根读来的短账本**整份覆盖**进正确存档（静默丢记忆）。
        #   C# 已改成连接即补发 `world_hello`；这一层是防漏网。
        self.require_world = bool(require_world)
        # agent 的**命名空间出处**（npc_id → 发布它时的 world_id，"" = 扁平）。
        # 用途只有一个：`flush_all` 拒绝把"在别的命名空间里读/建出来的账本"写进当前命名空间
        # —— 全量重写语义下那等于把目标存档的历史整份抹掉（09-18 复现的静默丢失）。
        self._namespace: Dict[str, str] = {}
        # 玩家 unitID 的**原样**值（C# 的 `player_unit_id`）。
        # ★ 不能拿 `world_id` 当它用：那是 `sanitize_world_id()` 之后的值（percent-encode + 截断），
        #   只适合当目录名。注入工具参数要用这个原样的（否则非 ASCII 的 id 会被写成转义串）。
        self.player_unit_id: str | None = None
        self.store: Dict[str, DialogueAgent] = {}
        # 群聊名册（room_id → Room）。与 store 同层：管家只持有，不解释内容。
        # 活体在内存、落盘走 flush_all（与 agent 会话同一次存档事件）。
        self.rooms: Dict[str, Room] = {}
        # 已解散的群 id **高水位**（防 id 重用）。与 `rooms` 是**一对**，必须同生共死：
        # 落盘时两者在同一次 `save_rooms_index` 里写（见 `retire_room`），
        # 否则"群没了但退休名单也没了" ⇒ `new_room_id` 把刚解散的 id 又发出去 ⇒
        # 老成员的 `room/cursor` 落到新群上 ⇒ 那些人在新群里一条消息都收不到（静默）。
        # per-存档：随 `rooms_root` 走，`discard_all` 清空、`load_rooms` 重新载入。
        self.retired_rooms: set = set()
        # 冻结舱（存档语义）：关窗 dispose 的活体暂存于此，**不写盘**——
        # 固化由 C# 存档钩子发 save_happened → flush_all() 驱动；世界（重）载由
        # load_happened → discard_all() 统一丢弃（关游戏不保存 = 进程消失 = 增量天然作废）。
        self._frozen: Dict[str, DialogueAgent] = {}
        # 人名映射表（世界级，**落盘**）：`{名字/唯一化显示名 → unitID}`。
        # 为什么落盘见 `unit_id_map.py` 文件头（模型历史里的显示名必须解得开）。
        # 与 rooms/contacts 同规约：随 `set_world` 换命名空间、随 `flush_all` 落盘。
        self.identity = UnitIdMapService(self.storage_root)
        self._op_lock = threading.Lock()
        self.system_prompt = SystemPrompt.instance()
        # 上下文容量（路由 contextWindow，stats 占用率分母；未知传 None 则 UI 环不渲染）
        self.context_window = context_window if (isinstance(context_window, int) and context_window > 0) else None
        # 分层分权：bridge/llm/compactor 由管家持有并透传给 DialogueAgent，管家不解释内容
        self.bridge = bridge
        self.compactor = compactor
        # 单回合内工具并发上限（透传 DialogueAgent，对应 config.concurrency.max_parallel_tools）
        self.max_parallel_tools = max(1, max_parallel_tools)
        # llm 单口 LlmClient：未显式注入则走 factory（env→OpenAI，否则 Echo 兜底）
        from .llm.factory import create_llm_client

        self.llm = llm if llm is not None else create_llm_client()

    @classmethod
    def reset_for_test(cls, storage_root: str = "/tmp/agent_loop_test", bridge=None, llm=None, compactor=None, max_parallel_tools: int = 5, context_window: int | None = 200000, require_world: bool = False):
        with cls._lock:
            if cls._instance is not None:
                for agent in list(cls._instance.store.values()):
                    try:
                        agent.dispose()
                    except Exception:
                        pass
            cls._instance = None
            SystemPrompt.reset_for_test()
        inst = cls(storage_root=storage_root, bridge=bridge, llm=llm, compactor=compactor, max_parallel_tools=max_parallel_tools, context_window=context_window, require_world=require_world)
        return inst

    # ---------- 管家 ----------
    def create(self, npc_id: str, provider: str = "test", model: str = "test-model", bridge=None, llm=None, compactor=None) -> DialogueAgent:
        """创建或自动 resume。只传 npc_id，人设从 prompts/personas/{npc_id}.txt 读；bridge/llm/compactor 可单次覆盖。
        恢复优先级（09-11 存档语义）：冻结舱（关窗未固化，可能比磁盘新）> 磁盘账本（上次 flush 状态）> 全新。

        注：原 `cwd` 形参已删（2026-09-13 打包审计）——它只被写进 header 落盘、**全程无人读**，
        默认值却是开发机的家目录，属纯本机色彩。header 是开放 dict，老存档里多出的键
        照常读回、无迁移成本。"""
        with self._op_lock:
            if npc_id in self.store:
                raise RuntimeError(f'agent "{npc_id}" is already registered (duplicate exact identity)')
            # ① 冻结舱优先：关窗未固化的活体直接复活（bridge/llm/compactor 覆盖参数忽略——
            # 同进程内引用一致，server 层组装后也不传覆盖）
            frozen = self._frozen.pop(npc_id, None)
            if frozen is not None:
                self.store[npc_id] = frozen
                return frozen
            sess = None
            if self.require_world and not self.world_id:
                # 未定档（Python 刚重启、C# 还没补发身份）：**不**从扁平目录恢复账本 ——
                # 那里的文件不属于任何存档（历史遗留 / 上一个窗口的残留），读进来既不是
                # "这个 NPC 的记忆"，下一笔又会把它整份写回正确存档。宁可这次开窗是从空白
                # 起头；C# 补发 `world_hello` 之后重开窗即从正确目录恢复。
                log.warning("未取得存档身份（Python 刚重启？）→ 不从扁平目录恢复 %s 的账本；"
                            "C# 补发身份（world_hello）后重开窗即恢复", npc_id)
            else:
                sess = load_session(npc_id, self.sessions_root)
            if sess is not None:
                return self._publish(sess, is_resume=True, bridge=bridge, llm=llm, compactor=compactor)
            sess = Session(id=npc_id, header={"id": npc_id, "provider": provider, "model": model})
            return self._publish(sess, is_resume=False, bridge=bridge, llm=llm, compactor=compactor)

    def _publish(self, sess: Session, is_resume: bool, bridge=None, llm=None, compactor=None) -> DialogueAgent:
        npc_id = sess.id
        if npc_id in self.store:
            raise RuntimeError(f'agent "{npc_id}" is already registered')
        # 屉是 SystemPrompt 私产：只递 id，一行委托，不碰 section/variable
        # 即便此处不调，SystemPrompt.assemble(scope=npc_id) 也会懒建
        self.system_prompt.ensure_agent_layer(npc_id)
        # 分层分权：bridge/llm/compactor 由管家透传，DialogueAgent 持有但不解释
        b = bridge if bridge is not None else self.bridge
        lc = llm if llm is not None else self.llm
        cc = compactor if compactor is not None else self.compactor
        # 图片仓：账本里只记 ref，图像字节在 `<sessions_root>/images/<npc>/`（见
        # docs/archive/2026-10-18-图片进上下文.md）。**挂在这里**而不是 Session 构造函数里：
        # 管家是唯一知道"这份账本属于哪个存档目录"的人（`sessions_root` 随存档切换）。
        # 失败只降级：收图会逐张丢弃 + 警告，不影响文本与回合 —— 图片是锦上添花。
        try:
            from .storage.images import ImageStore
            sess.image_store = ImageStore(self.sessions_root, npc_id)
        except Exception:
            log.warning("agent %s 图片仓挂载失败（本会话的图会被丢弃，文本照常）", npc_id, exc_info=True)
        agent = DialogueAgent(session=sess, system_prompt=self.system_prompt, llm=lc, bridge=b, compactor=cc, max_parallel_tools=self.max_parallel_tools, context_window=getattr(self, "context_window", None))
        # 人名映射表：**接上世界级那一份**（不复制 —— 换档时 store 内部整表换掉，
        # 复制一份就会变成陈旧副本，而陈旧的人名映射是最坏的那种错误）。
        try:
            agent.set_identity_store(self.identity)
        except Exception:
            log.warning("agent %s 接人名映射表失败（退回本对象内存表）", npc_id)
        # 管家接线（捎话要用）：`chuan_hua` 得按名字去**别人的**账本里追加一句话，而
        # "活体 / 冻结舱 / 磁盘 / 懒创建"这四级只有管家知道。没接 ⇒ 该 op 明确报错（不静默）。
        try:
            agent.set_loop(self)
        except Exception:
            log.warning("agent %s 接管家失败（捎话将不可用）", npc_id)
        self.store[npc_id] = agent
        # 出处登记：记下"这个账本是在哪个存档命名空间里读/建出来的"（`flush_all` 按它对账）。
        # 冻结舱复活（上面的提前 return）不改它 —— 同一个对象，出处本来就该是原来那个。
        self._namespace[npc_id] = self.world_id or ""
        # 懒首落：空账本不写盘——开窗激活（open_chat）即 create，浏览式开关窗不该留
        # header-only 残文件（list_sessions 按 mtime 排「最近」会被污染）；有事件后由 dispose 落盘。
        if not is_resume and sess.log:
            save_session(sess, self.sessions_root)
        return agent

    def get(self, npc_id: str) -> Optional[DialogueAgent]:
        return self.store.get(npc_id)

    def get_any(self, npc_id: str) -> Optional[DialogueAgent]:
        """取**记忆仍在内存里**的 agent：活体（store）优先，其次**冻结舱**（关窗未固化）。

        为什么需要它：`get()` 只看活体，而"关窗"在本工程是**冻结而不是销毁** ——
        那个 agent 的对象、账本、屉都还在 `_frozen` 里，重开窗会原样复活（甚至比磁盘新）。
        只看活体会漏掉它：群窗删除会在它身上报"没有可删的副本"，而它其实记得，
        而且重开窗后照旧记得（**静默**）。
        调用方是"要改**记忆**"的路径（群窗删除的 fan-out）；"要驱动回合"的路径
        仍应用 `get()`（冻结体的回合由 create 复活后再跑，不能凭空驱动）。
        """
        with self._op_lock:
            agent = self.store.get(npc_id)
            if agent is None:
                agent = self._frozen.get(npc_id)
        return agent

    def resume_if_on_disk(self, npc_id: str) -> Optional[DialogueAgent]:
        """磁盘上有账本就 resume 成活体；没有则返回 `None`（**不建空对象**）。

        群窗删除的 fan-out 用它补上"内存 → 磁盘"的最后一跳：重启 / 读档
        （`discard_all`）之后成员都还没被激活（`store` / `_frozen` 都空），而磁盘
        jsonl 里可能留着当初投递的副本 —— 只看内存会把他报成"没有副本"而**静默漏删**
        （删除后他一开窗照旧记得，见 `RoomFlow._memory_agent` 与 `docs/archive/2026-09-17-群聊编排/设计定稿.md` §9.2）。

        与 `create()` 的关系：本方法 = "先看磁盘有没有，有才 create"。差别只在
        **不空建** —— 从没参与过的成员不值得为一次删除凭空造活体（`create` 无条件建，
        空账本也要走一遍 `_publish`）。加载本身**不改账本内容**：只读盘 + 建对象，
        空账本连文件都不写。

        异常**不吞**：账本存在但读不出来时向上抛 —— 调用方（删除 fan-out）必须
        **整体拒绝**，宁可删不成，也不许静默漏删。
        """
        agent = self.get_any(npc_id)
        if agent is not None:
            return agent
        if not session_path_for(npc_id, self.sessions_root).exists():
            return None
        return self.create(npc_id)

    def list(self) -> List[DialogueAgent]:
        return list(self.store.values())

    # ---------- 配置热生效：压缩器 / 上下文窗口 ----------

    def rebind_compactor(self, compactor, context_window: Optional[int] = None) -> int:
        """热换压缩器与上下文窗口（配置 UI 改 compaction.ctx_window 即时生效用）。

        分层分权不变：本方法只做「管家持有 + 透传」，不解释压缩参数。要点：
        - 管家持有 `compactor`/`context_window`，供之后新建的 agent 取用；
        - **活动与冻结体都要逐个推**：agent 各持一份 `DialogueAgent.compactor` 引用，token 预算
          存在各自的 `UsageTracker`（`stats`）；而冻结体复活时是**同一对象直接出舱**
          （`create` 的冻结舱分支是 `return frozen`，不走 `_publish`）→ 漏推冻结舱就会出现
          「关过窗的那个 NPC 还在用旧压缩器/旧窗口」；
        - 失败不影响调用方：最坏是某个 agent 沿用旧压缩器。
        返回被同步的 agent 数（活 + 冻）。
        """
        with self._op_lock:
            agents = list(self.store.values()) + list(self._frozen.values())
            self.compactor = compactor
            if isinstance(context_window, int) and context_window > 0:
                self.context_window = context_window
            n = 0
            for a in agents:
                try:
                    a.compactor = compactor
                    stats = getattr(a, "stats", None)
                    if context_window is not None and stats is not None:
                        stats.set_context_window(context_window)
                    n += 1
                except Exception:
                    pass
            return n

    def dispose(self, npc_id: str):
        """关窗 = 冻结而非销毁（09-11 存档语义，用户拍板）：对话历史留在内存（_frozen），
        **不写盘**——固化由 C# 存档钩子 save_happened → flush_all() 驱动，玩家不存档就退游戏
        则增量随进程消失（= 没发生）。对象完整保留（不清 agent/屉），重开窗 create 时优先复活。
        空账本自然满足懒落盘（flush 时跳过）。"""
        with self._op_lock:
            agent = self.store.pop(npc_id, None)
            if agent is not None:
                self._frozen[npc_id] = agent

    # ---------- 存档语义：固化与丢弃 ----------

    def flush_all(self) -> int:
        """save_happened（C# 存档钩子触发）：把所有活/冻 agent 的当前历史固化进 jsonl。
        全量重写模式天然增量语义（磁盘追平内存）；空账本跳过（懒落盘保留）。
        flush 不改变内存状态（冻结体仍在舱内，重开窗优先内存）。返回固化数。

        群聊流水**在同一次事件里一起落**（：room 与 session 是"同一次经历的两面"）：
        只落一边会造成「群窗有、NPC 不知道」或「NPC 知道、群窗没有」。
        一起落，"存档 = 这个世界的完整快照"这句话才成立。崩溃语义不变：
        两边都是内存账本，一起丢 ⇒ 仍然一致。"""
        with self._op_lock:
            agents = list(self.store.values()) + list(self._frozen.values())
            rooms = list(self.rooms.values())
        cur = self.world_id or ""
        # ★ 未定档 + 生产形态 = 扁平根**不可写**（见 `require_world`）：宁可这次不固化，
        #   也不能把内容写进"谁都不属于"的目录 —— 那是跨档串号的入口。
        if self.require_world and not cur:
            log.warning("flush 跳过：未取得存档身份（Python 刚重启、C# 尚未补发 world_hello）"
                        "→ 不写扁平目录；本次增量留在内存，等身份补发后的下一次存档")
            return 0
        n = 0
        skipped: List[str] = []
        for a in agents:
            # 出处对账：只固化"属于当前命名空间"的账本。出处不符意味着它是在别的命名空间里
            # 读/建出来的（典型：Python 重启后从扁平根读了一份空账本，随后身份补发到了真存档），
            # 全量重写会把它写进当前存档 ⇒ 目标存档的历史被**静默覆盖**（09-18 复现）。
            if self._namespace.get(a.id, cur) != cur:
                skipped.append(a.id)
                continue
            try:
                if a.session.log:  # 空账不落盘：浏览式开关窗不留残文件
                    save_session(a.session, self.sessions_root)
                    n += 1
            except Exception:
                pass
        if skipped:
            log.warning("flush：%d 个 agent 的命名空间与当前存档不符 → **已跳过**（不覆盖）；"
                        "当前=%s｜跳过=%s", len(skipped), cur or "(未定档)", "、".join(skipped[:5]))
        # 房间流水：与上面同一批、同一次调用里落（顺序无关，两边都是幂等全量重写）
        # `retired_rooms` **必须一起传**：漏了就会把退休名单抹掉，于是 `new_room_id`
        # 把刚解散的 id 又发出去（静默丢消息，见 `room.new_room_id`）。
        try:
            save_rooms_index(rooms, self.rooms_root, self.retired_rooms)
            for r in rooms:
                if r.log:
                    save_room(r, self.rooms_root)
        except Exception:
            pass
        # 人名映射表：**同一次存档事件**里落（与 session / room 流水同一批）。
        # 它是"模型见过谁"的钥匙：只在内存的话，重进游戏后模型历史里的唯一化显示名
        # 就再也解不开（只能退回按名猜，静默错人）。写失败不阻断固化 —— 会话与群流水
        # 才是主体，映射表丢了只是精度退回按名。
        try:
            nid = self.identity.save()
            if nid:
                log.info("人名映射表已固化 %d 条（%s）", nid, self.identity.path)
        except Exception as e:
            log.warning("人名映射表落盘失败（本次会话仍生效）：%s", e)
        return n

    def discard_all(self) -> int:
        """load_happened（C# 读档/进世界钩子触发）：世界（重）载——未固化增量全部丢弃，
        所有活/冻 agent 销毁（屉随 agent 清）。之后 open_chat 从 jsonl（上次 flush 状态）恢复，
        即「读档 = 回到存档时刻」。返回丢弃数。

        群聊流水同步丢弃（与 agent 舱一起丢）：房间的未固化增量若留着，就会出现
        「群窗记得、NPC 不记得」—— 两边必须回到同一个时刻。"""
        with self._op_lock:
            frozen = list(self._frozen.values())
            self._frozen.clear()
            live = list(self.store.values())
            self.store.clear()
            self.rooms.clear()
            # 出处表跟着一起清：里面的键属于刚被丢弃的那批 agent（留着会让下一个同名 agent
            # 顶着别人的出处，于是它永远不被固化 —— 静默不落盘比写错更难查）。
            self._namespace.clear()
            # 退休名单同步清空：它是 per-存档的，`load_rooms` 会从该存档的 index 重新载入。
            # 不清的话，切存档会把 A 存档的退休 id 带到 B 存档（症状只是"新群号从中间开始"，
            # 但会掩盖真正的 id 重用 —— 诊断时看日志会一头雾水）。
            self.retired_rooms.clear()
        for a in frozen + live:
            try:
                a.dispose()
            except Exception:
                pass
            try:
                self.system_prompt.dispose_scope(a.id)
            except Exception:
                pass
        return len(frozen) + len(live)

    def path_for(self, npc_id: str):
        return session_path_for(npc_id, self.sessions_root)

    # ---------- 群聊（Room）注册表 ----------
    #
    # 只有**活体**（内存）的群；落盘由 flush_all 统一做，读档由 load_rooms 做。
    # 与 `store` 同层语义：管家持有名册，**不解释内容** —— 投递、调度、跑轮全在
    # `room_flow.RoomFlow`，本类只回答「放哪儿、怎么存」。

    def room(self, room_id: str):
        return self.rooms.get(str(room_id or ""))

    def load_rooms(self) -> int:
        """把本存档目录下的群**名录**读进内存（流水懒加载：真要看时才读 jsonl）。

        读档路径：`load_happened` 的 discard_all 之后由装配层调用。返回载入数。
        流水为空但名录在的群也会载入 ——「有这个群、还没人说话」是合法状态。

        退休高水位（`retired`）与名录**同文件、同一趟读**：它是防 id 重用的另一半，
        分开读会出现"名录回来了、退休名单没回来"的窗口，而那个窗口里新建群就会撞号。
        """
        n = 0
        self.retired_rooms = {str(x) for x in load_retired_rooms(self.rooms_root)}
        for meta in load_rooms_index(self.rooms_root):
            rid = str(meta.get("room_id") or "")
            if not rid or rid in self.rooms:
                continue
            self.rooms[rid] = Room.from_meta(meta, log=[])
            n += 1
        return n

    def retire_room(self, room_id: str) -> None:
        """把一个**已从内存摘掉**的群固化到盘上：写 index（含 retired 高水位）+ 删流水文件。

        谁调用：`RoomFlow.dissolve_room`（作为注入的 `delete_room` 回调）。本方法只管盘，
        不碰内存名册、不管成员 `scene` —— 那些是编排层的事。

        ★ 顺序 = **先 index（提交点）后 unlink**，理由见 `persistence.delete_room_file`：
          反过来崩在中间会留下 `last_seq=N 但历史空白` 的幽灵群；按这个顺序崩最坏只留一个
          **孤儿 jsonl**，而 `load_rooms` 只读 index ⇒ 孤儿对系统完全不可见。

        ★ **不做 flush**：不把别的群的未固化增量顺手写下去（那是 `flush_all` 的活，
          由 C# 存档钩子驱动）。这里只动与"这个群"有关的两处。
        """
        rid = str(room_id or "").strip()
        if not rid:
            return
        self.retired_rooms.add(rid)
        if self.require_world and not self.world_id:
            # 未定档：写 index / 删流水都会落到扁平目录（不属于任何存档）。内存里照常摘除
            # （调用方已经做了），盘上先不动 —— 重开游戏后这个群会重现，但比串档轻。
            log.warning("删群 %s：未取得存档身份 → 不写盘（避免写进扁平目录）；重开游戏后该群会重现", rid)
            return
        try:
            save_rooms_index(list(self.rooms.values()), self.rooms_root, self.retired_rooms)
        except Exception:
            log.exception("删群 %s：写 index 失败（已从内存摘除，重开游戏后会重现）", rid)
            return   # index 没落成 ⇒ **不要**去删文件，否则就变成"幽灵群"那个坏顺序
        delete_room_file(rid, self.rooms_root)
        log.info("删群固化：%s（退休高水位 %d 个）", rid, len(self.retired_rooms))

    def load_room_log(self, room_id: str):
        """按需把某个群的流水读进内存（名录在、log 为空时）。返回该群（或 None）。

        懒加载的理由：`list_rooms` 只要名录（群名/成员/末位 seq），不该为列个表
        把每个群几百条发言全读一遍。
        """
        rid = str(room_id or "")
        room = self.rooms.get(rid)
        if room is None:
            room = load_room(rid, self.rooms_root)
            if room is None:
                return None
            self.rooms[room.id] = room
            return room
        if not room.log:
            fresh = load_room(room.id, self.rooms_root)
            if fresh is not None and fresh.log:
                room.log = fresh.log
        return room

    @property
    def rooms_root(self) -> str:
        """当前存档的群聊流水目录（未定存档时 = `<storage_root>/rooms`）。"""
        return rooms_root_for(self.storage_root, self.world_id)

    # ---------- 存档命名空间 ----------

    @property
    def sessions_root(self) -> str:
        """当前存档的会话目录（未定存档时 = storage_root 扁平布局）。"""
        return world_root(self.storage_root, self.world_id)

    def set_world(self, world_id: object, player_name: object = None,
                  player_unit_id: object = None) -> bool:
        """切换存档命名空间（C# load_happened/save_happened 携带 world_id）。

        `player_unit_id` = 玩家 unitID 的**原样**值（不是 sanitize 过的 world_id）：
        harness 注入 `target_id` 时用它（"对象是玩家"那条）。
        返回是否**发生了变化**（调用方据此打日志）。语义上这是"世界身份"而不是"路径配置"：
        调用方应在切换后自行丢弃活/冻 agent（load_happened 本来就会 discard_all），
        否则旧世界的未固化增量会被写进新世界的目录。
        """
        wid = sanitize_world_id(world_id)
        pn = str(player_name or "").strip() or None
        pu = str(player_unit_id or "").strip() or None
        changed = (wid != (self.world_id or ""))
        self.world_id = wid or None
        # 人名映射表跟着换命名空间并**丢弃内存表**（旧档的 unitID 在新档里可能是别人）。
        # 放在这里而不是 load_rooms 里：它是"世界身份"的一部分，与 sessions_root 同源。
        try:
            self.identity.set_world(self.world_id or "")
        except Exception:
            log.warning("人名映射表切换存档失败（沿用当前命名空间）")
        if pn:
            self.player_name = pn
        if pu:
            self.player_unit_id = pu
        return changed
