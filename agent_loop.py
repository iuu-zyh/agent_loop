"""AgentLoop — 管家（只管名册+锁+续档，不碰屉）"""

from __future__ import annotations

import threading
from typing import Dict, List, Optional

from .session import Session
from .system_prompt import SystemPrompt
from .dialogue_agent import DialogueAgent
from .persistence import load_session, save_session, session_path_for, world_root, sanitize_world_id


class AgentLoop:
    """进程单例管家：名册 + 锁 + 续档。屉是 SystemPrompt 私产，只递 npc_id"""

    _instance: Optional["AgentLoop"] = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self, storage_root: str = "~/.sessions", bridge=None, llm=None, compactor=None, max_parallel_tools: int = 5, context_window: int | None = 200000):
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
        self.store: Dict[str, DialogueAgent] = {}
        # 冻结舱（存档语义）：关窗 dispose 的活体暂存于此，**不写盘**——
        # 固化由 C# 存档钩子发 save_happened → flush_all() 驱动；世界（重）载由
        # load_happened → discard_all() 统一丢弃（关游戏不保存 = 进程消失 = 增量天然作废）。
        self._frozen: Dict[str, DialogueAgent] = {}
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
    def reset_for_test(cls, storage_root: str = "/tmp/agent_loop_test", bridge=None, llm=None, compactor=None, max_parallel_tools: int = 5, context_window: int | None = 200000):
        with cls._lock:
            if cls._instance is not None:
                for agent in list(cls._instance.store.values()):
                    try:
                        agent.dispose()
                    except Exception:
                        pass
            cls._instance = None
            SystemPrompt.reset_for_test()
        inst = cls(storage_root=storage_root, bridge=bridge, llm=llm, compactor=compactor, max_parallel_tools=max_parallel_tools, context_window=context_window)
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
        agent = DialogueAgent(session=sess, system_prompt=self.system_prompt, llm=lc, bridge=b, compactor=cc, max_parallel_tools=self.max_parallel_tools, context_window=getattr(self, "context_window", None))
        self.store[npc_id] = agent
        # 懒首落：空账本不写盘——开窗激活（open_chat）即 create，浏览式开关窗不该留
        # header-only 残文件（list_sessions 按 mtime 排「最近」会被污染）；有事件后由 dispose 落盘。
        if not is_resume and sess.log:
            save_session(sess, self.sessions_root)
        return agent

    def get(self, npc_id: str) -> Optional[DialogueAgent]:
        return self.store.get(npc_id)

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
        flush 不改变内存状态（冻结体仍在舱内，重开窗优先内存）。返回固化数。"""
        with self._op_lock:
            agents = list(self.store.values()) + list(self._frozen.values())
        n = 0
        for a in agents:
            try:
                if a.session.log:  # 空账不落盘：浏览式开关窗不留残文件
                    save_session(a.session, self.sessions_root)
                    n += 1
            except Exception:
                pass
        return n

    def discard_all(self) -> int:
        """load_happened（C# 读档/进世界钩子触发）：世界（重）载——未固化增量全部丢弃，
        所有活/冻 agent 销毁（屉随 agent 清）。之后 open_chat 从 jsonl（上次 flush 状态）恢复，
        即「读档 = 回到存档时刻」。返回丢弃数。"""
        with self._op_lock:
            frozen = list(self._frozen.values())
            self._frozen.clear()
            live = list(self.store.values())
            self.store.clear()
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

    # ---------- 存档命名空间 ----------

    @property
    def sessions_root(self) -> str:
        """当前存档的会话目录（未定存档时 = storage_root 扁平布局）。"""
        return world_root(self.storage_root, self.world_id)

    def set_world(self, world_id: object, player_name: object = None) -> bool:
        """切换存档命名空间（C# load_happened/save_happened 携带 world_id）。

        返回是否**发生了变化**（调用方据此打日志）。语义上这是"世界身份"而不是"路径配置"：
        调用方应在切换后自行丢弃活/冻 agent（load_happened 本来就会 discard_all），
        否则旧世界的未固化增量会被写进新世界的目录。
        """
        wid = sanitize_world_id(world_id)
        pn = str(player_name or "").strip() or None
        changed = (wid != (self.world_id or ""))
        self.world_id = wid or None
        if pn:
            self.player_name = pn
        return changed
