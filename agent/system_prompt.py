"""SystemPrompt — 单例 + 每 NPC 一屉的分层桩（ScopdLayers 对标 DSH）"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from .l1_incremental import IncrementalRegistry
from ..config import textio as _textio

#: 「行动口径」段（`action`）的正文 —— 见 `format_l1_context` 末尾对该段的说明。
#: 措辞是从探针里挑出来的那一版：只钉"要办成事必须调工具"，不催"没有的先查"
#: （催查询那半句在开思考时会引来多余调用：12 个本该直接答的回合里 5 个跑去查档案；
#:   而这半句开/关思考都不引入乱调，却把"该调"从 20/42 拉到 25/42、开思考 40/42）。
#
# ★ 11-12 补上「好感/关系」那半句 ★（用户："有加减好感度的系统，但实际模型不怎么去主动调用"）。
#   为什么必须写在这里、而不是只写在 tool_usage 里：本段是**每游戏日重发一次**的
#   （见 `format_l1_context` 末尾拼日期那段），而 tool_usage 是稳区、整个会话只随 system 发一次。
#   二十轮历史之后，稳区那条早被稀释；"说情话 = 涨好感"这件事最容易只落在台词里 ——
#   玩家掏心窝子说了一堆，模型热络回应，好感一点没动，而**没有任何地方会报错**。
#   枚举里刻意写上「交心、羞辱、救命、背叛」这类**情绪强度**而不只是动作类型：
#   模型能理解"送礼要调工具"，但往往不觉得"这一番话说得极重"也该落账。
#   ⚠ 长度是硬成本（每个游戏日一条）。`tests/test_game_time_stamp.py` 钉住它出现在段里。
ACTION_HINT = ("要把事办成（送、要、换、买卖、教功法、邀约、碰面）只能靠调工具，"
               "只在台词里应承等于什么都没做；"
               "好感也一样——交心、羞辱、救命、背叛只是嘴上说说，"
               "关系与好感就不会变，得真调工具才落账")


class PromptLayer:
    """一屉白板（各司其职：sections 稳区 / contexts 易变 / variables 插值 / tools 结构化）"""

    def __init__(self, scope: Optional[str]):
        self.scope = scope
        self.sections: Dict[str, Dict[str, Any]] = {}  # name -> {name, order, text}
        self.contexts: Dict[str, Dict[str, Any]] = {}
        self.variables: Dict[str, Any] = {}
        self.tools: Dict[str, Dict[str, Any]] = {}  # name -> OpenAI tool schema


class SystemPrompt:
    """进程单例：全局屉 + 每 scope 一屉，屉是私产，对外只暴露 ensure_agent_layer"""

    _instance: Optional["SystemPrompt"] = None

    _SECTION_MAP: Dict[str, tuple[str, int]] = {
        "harness_identity.txt": ("harness:identity", -100),
        "world_basis.txt": ("world:basis", -90),
        "world_persona_rules.txt": ("world:persona_rules", -80),
        # 排版与着色（10-31）：教模型用气泡支持的富文本白名单。
        # order=-75 —— **必须排在 deployment:persona(0) 之前**（第一版写成 15，实测排在
        # 人设后面）。它不是"每 NPC 不同"的内容，放在人设之后会让它变成每个 NPC 的
        # 前缀尾巴，provider 的 prefix cache 每次换人都整段重算。取 -75 是因为：
        #   · 在 world_persona_rules(-80) 之后 —— 先读"世界怎么说话"，再读"字怎么打扮"；
        #   · 在 config:player_persona(-70) 之前 —— 玩家自述是"你对面是谁"，比排版更靠后；
        # 三者都属"稳定段"，顺序只影响可读性，不影响缓存。
        # ⚠ 与 `csharp/UI/BubbleTextCore.cs` 的白名单**必须同名同值**：
        #   模型照这里写的名字而 C# 不认 = 静默无色，玩家只会觉得"说好的颜色呢"。
        #   `tests/test_rich_text_bubble_contract.py` 双向比对。
        "world_rich_text.txt": ("world:rich_text", -75),
        # ★ 工具使用政策（11-12 补回一个**静默回归**）★
        #   10-31 那次「富文本白名单」提交把这一行**整行换掉**成了 world_rich_text，
        #   于是 `tool_usage.txt` 再也不进 system；但它还在仓库里、还有 11 条契约测试
        #   盯着它的文本（那些测试全是"读文件文本"的断言，所以一路全绿）、docs 也还写着
        #   "~870 字"—— **没有任何东西会报错**，只是模型从此只看得见 tools JSON 的
        #   schema（讲"填什么"），再也看不到"什么时候该伸手、结果怎么读"。
        #   症状正是用户 11-12 报的"模型不怎么主动调用"。
        #   order=10：排在所有稳定段之后、deployment:persona(0) 之前 —— 它同样是
        #   "每个 NPC 都一样"的内容，必须压在 persona 前面（否则每换人重算前缀缓存）。
        "tool_usage.txt": ("tool:usage", 10),
    }

    # ------------------------------------------------------------------
    # 「自我人设」—— config.player.persona 的注入位（**不是**文件段，故不在 _SECTION_MAP 里）
    # ------------------------------------------------------------------
    # 段名带 `config:` 前缀，一眼分得清"它来自配置面板"而不是 prompts/ 下的某个文件。
    PLAYER_PERSONA_SECTION = "config:player_persona"
    # order=-70 的两个理由（改动前先读）：
    #   ① 排在 world:persona_rules(-80) **之后** —— 规则在前、玩家自述在后，规则不被自述顶掉；
    #   ② 排在 deployment:persona(0) **之前** —— 那一段每个 NPC 都不同，稳定段必须压在它前面，
    #      否则每换一个 NPC 都要重算整段提示词前缀缓存（provider 侧的 prefix cache 全废）。
    # ⚠ 别把它挪到 order > 0。
    PLAYER_PERSONA_ORDER = -70
    # 包裹模板：玩家只写"自己是谁"，这段交代"该怎么用"与"冲突时听谁的"。
    # 三条约束都来自实际风险：复述设定会出戏；提"设定"二字会破功；
    # 自封身份（"我是你师父"）与游戏事实（关系/宗门）打架时必须有裁决规则。
    PLAYER_PERSONA_TEMPLATE = (
        "[玩家自述]\n"
        "下列文字是玩家自己写下的设定，是你对这名玩家已有的了解"
        "（他自称的身份、来历、性情、说话习惯）：\n"
        "{persona}\n\n"
        "在对话里自然地承认并使用它：称呼、态度、话题可以据此调整；"
        "不要复述这段文字，也不要提「设定」二字。\n"
        "若它与「玩家」段所载的游戏事实（修为、宗门、关系、所在位置）冲突，以「玩家」段为准；"
        "性情、来历、口吻以本段为准。"
    )

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self, prompts_root: Optional[str | Path] = None):
        if getattr(self, "_initialized", False):
            return
        self._initialized = True
        self.global_layer = PromptLayer(scope=None)
        self.scoped: Dict[str, PromptLayer] = {}
        self._persona_file_cache: Dict[str, str] = {}  # scope -> last file-derived persona text（分层分权：跟踪文件源，避免覆盖手动屉）
        # 「经历类」段的增量成文器（10-25）：`(scope, 段名)` → 已发过的条目键 + 上次文本。
        # **必须按 scope 隔离**：同一进程里多个 NPC 会话各自攒自己的集合，共用就会把别人的
        # 条目当成"已经发过"，症状是某个 NPC 的经历永远缺几条、且没有任何报错。
        self._incr = IncrementalRegistry()
        if prompts_root is None:
            # 提示词根解析统一走 paths（发行版：<Mod根>/prompts，用户可编辑、升级不覆盖；
            # 开发机：包目录/prompts，与旧行为同路径）
            from ..config import paths as _paths
            prompts_root = _paths.prompts_root()
        self._prompts_root = Path(prompts_root)
        self._sections_root = self._prompts_root / "sections"
        self._personas_root = self._prompts_root / "personas"
        self._traits_root = self._prompts_root / "traits"
        # 自我人设原文（未包裹模板）。留存只为自检/日志/测试读得到"当前生效的那份是谁写的"，
        # 真正进提示词的是下面 set_player_persona 写进 global_layer 的那一段。
        self._player_persona_raw: str = ""
        loaded = self._load_global_sections_from_files()
        if not loaded:
            self.global_layer.sections["harness:identity"] = {
                "name": "harness:identity",
                "order": -100,
                "text": "你是鬼谷八荒世界的NPC对话Agent，运行于Python harness。",
            }
            self.global_layer.sections["world:basis"] = {
                "name": "world:basis",
                "order": -90,
                "text": "你身处鬼谷八荒世界，用词限于该世界观。",
            }
        # 全局工具：进程启动即注册，scoped 可覆盖/追加（分层分权：SystemPrompt 拥有，DialogueAgent 只读）
        try:
            from ..tools.schemas import ALL_TOOL_SCHEMAS

            for schema in ALL_TOOL_SCHEMAS:
                name = schema.get("function", {}).get("name", "")
                if name not in self.global_layer.tools:
                    self.global_layer.tools[name] = schema
        except Exception:
            pass

    @classmethod
    def instance(cls) -> "SystemPrompt":
        return cls()

    @classmethod
    def reset_for_test(cls):
        cls._instance = None

    # ---------- 文件化加载：全局 Sections ----------
    def _load_global_sections_from_files(self) -> bool:
        if not self._sections_root.is_dir():
            return False
        loaded_any = False
        for fname, (sec_name, order) in self._SECTION_MAP.items():
            fpath = self._sections_root / fname
            if not fpath.is_file():
                continue
            try:
                text = _textio.read_text(fpath).strip()
            except Exception:
                continue
            if not text:
                continue
            self.global_layer.sections[sec_name] = {"name": sec_name, "order": order, "text": text}
            loaded_any = True
        return loaded_any

    def _refresh_global_sections_if_changed(self):
        """每步 assemble 前自检文件：内容变了就换纸。DSH 的 section 不变，你的文件会变所以需此步"""
        if not self._sections_root.is_dir():
            return
        for fname, (sec_name, order) in self._SECTION_MAP.items():
            fpath = self._sections_root / fname
            if not fpath.is_file():
                continue
            try:
                text = _textio.read_text(fpath).strip()
            except Exception:
                continue
            if not text:
                continue
            cur = self.global_layer.sections.get(sec_name)
            if cur is None or cur.get("text") != text or cur.get("order") != order:
                self.global_layer.sections[sec_name] = {"name": sec_name, "order": order, "text": text}

    def reload_global_sections(self):
        for _, (sec_name, _) in self._SECTION_MAP.items():
            self.global_layer.sections.pop(sec_name, None)
        self._load_global_sections_from_files()
        # 注：self-player-persona 段不在这里被清 —— 它不属于 _SECTION_MAP（不是文件段），
        # 重载文件不该顺手抹掉玩家自述。重建需重新调 set_player_persona。

    # ---------- 自我人设（config.player.persona 的注入位） ----------
    def set_player_persona(self, text: Optional[str]) -> bool:
        """注入 / 热更新 / 移除「自我人设」段。返回 True = 现在有这一段，False = 已移除。

        三种输入都走这一个口（装配期 + 配置保存热落地 + 测试）：
          · 非空文本 → 用 `PLAYER_PERSONA_TEMPLATE` 包裹后写进**全局屉**（所有 NPC / 所有存档 /
            群聊都生效，因为它们共用这一个进程单例）；
          · 空 / None / 纯空白 → **移除**该段。语义是"系统提示词里根本不存在这一段"，
            而不是"存在但内容为空" —— 后者会让 `render_prompt` 多出一段空行，
            还会让「系统组装」始终挂着一行空白段；
          · 每步 `assemble` 的段级差分据此自然生效：文本变了就只上行这一段（标签「自我人设」），
            没变就不上行；清空后下个回合的块里直接不再出现（sys 没有 ctx 的「（已移除）」语义）。
        """
        t = (text or "").strip()
        self._player_persona_raw = t
        if not t:
            self.global_layer.sections.pop(self.PLAYER_PERSONA_SECTION, None)
            return False
        self.global_layer.sections[self.PLAYER_PERSONA_SECTION] = {
            "name": self.PLAYER_PERSONA_SECTION,
            "order": self.PLAYER_PERSONA_ORDER,
            "text": self.PLAYER_PERSONA_TEMPLATE.format(persona=t),
        }
        return True

    def player_persona(self) -> str:
        """当前生效的自我人设**原文**（未包裹模板）；空串 = 未设置。"""
        return self._player_persona_raw

    # persona 模板（文件化，每 NPC 一份）
    def get_persona_default(self) -> str:
        fpath = self._personas_root / "default.txt"
        if fpath.is_file():
            try:
                t = _textio.read_text(fpath).strip()
                if t:
                    return t
            except Exception:
                pass
        return "你是{npc_name}，鬼谷八荒世界的一名修仙者。"

    def get_persona_suffix(self) -> str:
        fpath = self._personas_root / "_suffix.txt"
        if fpath.is_file():
            try:
                t = _textio.read_text(fpath).strip()
                if t:
                    return t
            except Exception:
                pass
        return "[输出底线] 你必须全心全意进行角色扮演，时刻牢记修仙者身份，禁止透露你的真实来历（你并非什么程序，也不来自现实世界），禁止出现现代科技腔调与思考过程泄露，回复保持游戏内口吻。"

    def _load_persona(self, npc_id: str) -> str:
        """personas/{npc_id}.txt 存在用之否则 default.txt，再拼后缀。屉的私产，AgentLoop 不碰"""
        p = self._personas_root / f"{npc_id}.txt"
        if p.is_file():
            try:
                base = _textio.read_text(p).strip()
                if base:
                    suffix = self.get_persona_suffix()
                    return f"{base}\n\n{suffix}" if suffix and suffix not in base else base
            except Exception:
                pass
        base = self.get_persona_default()
        suffix = self.get_persona_suffix()
        if suffix and suffix not in base:
            return f"{base}\n\n{suffix}"
        return base

    def _load_traits(self, npc_id: str) -> Dict[str, str]:
        """traits/{npc_id}.json 可选，缺则 {}。后续可接 DB/provider，文件只是离线兜底"""
        p = self._traits_root / f"{npc_id}.json"
        if p.is_file():
            try:
                data = json.loads(_textio.read_text(p))
                if isinstance(data, dict):
                    return {str(k): str(v) for k, v in data.items() if v is not None}
            except Exception:
                pass
        return {}

    # ---------- 屉的唯一出口：AgentLoop 只递 npc_id ----------
    def ensure_agent_layer(self, npc_id: str):
        """按 npc_id 从文件/DB 长屉，幂等。AgentLoop 只调这一行，不碰 sections/variables"""
        if npc_id in self.scoped and "deployment:persona" in self.scoped[npc_id].sections:
            return
        persona_text = self._load_persona(npc_id)
        # 屉不存在或已存在但无 persona（如仅有变量）→ 补写 persona/变量；已有 persona 由上行 return 挡住
        self.section("deployment:persona", persona_text, order=0, scope=npc_id)
        self.variable("npc_name", npc_id, scope=npc_id)
        self._persona_file_cache[npc_id] = persona_text
        # 静态标签变量：character/hobby/race 等，工具链通后可改为 provider lambda
        for k, v in self._load_traits(npc_id).items():
            self.variable(k, v, scope=npc_id)

    # ---------- 注册（底层，仍通过 effect 语义对外只经 ensure_agent_layer） ----------
    def section(self, name: str, text: str, order: int = 0, scope: Optional[str] = None):
        layer = self._layer_for(scope, create=True)
        if name in layer.sections:
            raise RuntimeError(f'prompt section "{name}" is already registered in scope {scope}')
        layer.sections[name] = {"name": name, "order": order, "text": text}

        def dispose():
            layer.sections.pop(name, None)
            if scope is not None and not layer.sections and not layer.contexts and not layer.variables and not layer.tools:
                self.scoped.pop(scope, None)

        return dispose

    def variable(self, name: str, value: Any, scope: Optional[str] = None):
        layer = self._layer_for(scope, create=True)
        layer.variables[name] = value

    def context(self, name: str, text: str, order: int = 100, scope: Optional[str] = None):
        layer = self._layer_for(scope, create=True)
        layer.contexts[name] = {"name": name, "order": order, "text": text}

    def remove_context(self, name: str, scope: Optional[str] = None):
        layer = self._layer_for(scope, create=False)
        layer.contexts.pop(name, None)
        if scope is not None and not layer.sections and not layer.contexts and not layer.variables and not layer.tools:
            self.scoped.pop(scope, None)

    # ---------- 装配（懒建 + 每步自检文件：彻底不依赖 AgentLoop 调 ensure） ----------
    def _refresh_persona(self, scope: str):
        """assemble 前自检 personas/{scope}.txt：文件源 persona 变了就换纸；手动改的屉不覆盖"""
        try:
            fresh = self._load_persona(scope)
            layer = self.scoped[scope]
            cur = layer.sections.get("deployment:persona")
            last_file = self._persona_file_cache.get(scope)
            if cur is None:
                layer.sections["deployment:persona"] = {"name": "deployment:persona", "order": 0, "text": fresh}
                self._persona_file_cache[scope] = fresh
            elif last_file is not None and cur.get("text") == last_file and cur.get("text") != fresh:
                # 仅当屉的 persona 仍等于上次文件值（未被手动改）且文件变了才热更
                layer.sections["deployment:persona"] = {"name": "deployment:persona", "order": 0, "text": fresh}
                self._persona_file_cache[scope] = fresh
            # traits 同理自检
            fresh_traits = self._load_traits(scope)
            for k, v in fresh_traits.items():
                if layer.variables.get(k) != v:
                    layer.variables[k] = v
        except Exception:
            pass

    def assemble(self, scope: Optional[str] = None) -> Dict[str, Any]:
        # 每步先看全局文件变没变，变了换纸，保证热改文件下一 turn 立刻生效
        try:
            self._refresh_global_sections_if_changed()
        except Exception:
            pass
        # 懒创建：AgentLoop._publish 甚至可不调 ensure，首轮 assemble 现长屉；文件化 persona 也在此自检
        if scope is not None and scope not in self.scoped:
            try:
                self.ensure_agent_layer(scope)
            except Exception:
                pass
        elif scope is not None and scope in self.scoped:
            self._refresh_persona(scope)
        merged_sections: Dict[str, Dict[str, Any]] = dict(self.global_layer.sections)
        merged_contexts: Dict[str, Dict[str, Any]] = dict(self.global_layer.contexts)
        merged_vars: Dict[str, Any] = dict(self.global_layer.variables)
        merged_tools: Dict[str, Dict[str, Any]] = dict(self.global_layer.tools)
        if scope is not None and scope in self.scoped:
            layer = self.scoped[scope]
            merged_sections.update(layer.sections)
            merged_contexts.update(layer.contexts)
            merged_vars.update(layer.variables)
            merged_tools.update(layer.tools)
        sections = sorted(merged_sections.values(), key=lambda s: s["order"])
        contexts = sorted(merged_contexts.values(), key=lambda c: c["order"])
        # 工具按 TOOL_ORDER 排序，未在表中的放末尾（分层分权：SystemPrompt 只排序，不执行）
        try:
            from ..tools.schemas import TOOL_ORDER as _TOOL_ORDER

            order_index = {n: i for i, n in enumerate(_TOOL_ORDER)}
            tools = sorted(merged_tools.values(), key=lambda t: order_index.get(t.get("function", {}).get("name", ""), 999))
        except Exception:
            tools = list(merged_tools.values())
        return {"sections": sections, "contexts": contexts, "variables": merged_vars, "tools": tools}

    def render_prompt(self, assembly: Dict[str, Any]) -> str:
        variables = assembly.get("variables", {})
        parts: List[str] = []
        for s in assembly.get("sections", []):
            text = s.get("text", "")
            for k, v in variables.items():
                # 支持 provider: callable 时现算（对标 DSH variable provider()=>value）
                sv = str(v() if callable(v) else v)
                text = text.replace("{{" + k + "}}", sv).replace("{" + k + "}", sv)
            if text:
                parts.append(text)
        return "\n\n".join(parts)

    # ---------- L1 Context 成文（各司其职：桥只给 raw，SystemPrompt 负责文案格式） ----------
    # 「当前时间」为什么是**独立一段**而不是塞进「近况」（L1 也报日期）：
    #   ① 「近况」为空时整段不渲染（`render_context_segment` 对空文本返回 ""）—— 新 NPC /
    #      系统角色恰恰没有经历日志，而它们最需要知道"今天几号"。塞进近况 = 那类 NPC 永远没日期。
    #   ② 成本：段是逐段差分的，日期每天变一次 ⇒ 独立段每天只重发 ~20 字；塞进近况则是**整段
    #      （最多 10 条经历）每天重发一遍**。
    #   ③ 语义：段名「近况」装历史，把"现在"混进去名不副实，也会让"这一段为什么变了"难以归因。
    #   顺序：时间在最前 —— 模型先拿到"现在"，再读自身/玩家状态与近况，读起来才是顺的。
    # ⚠ 本元组**不再是段的权威清单**（多人化后段的集合是动态的：单聊 6 段，群聊再按在场者
    # 各加一段 `other:<npc_id>`）。它只登记「基础段」的名字与顺序，供文档/测试引用；
    # 真实清单每步由 `format_l1_context` 的返回**推导**出来（见 dialogue_agent._pre_step）。
    # 「所在」为什么也是独立一段（09-24 用户拍板，接在 time 的同款三段论之后）：
    #   位置是**最易变的一项** —— 玩家在地图上挪一步、movement 的召唤/传送、进出房间，
    #   每一样都会让"在哪/同不同处/身边有谁"变一次。而它原先散在三处：「自身」段里的
    #   `栖居于X`、「玩家」段里的坐标与"同处一地/异地相隔"、「同席·X」段里的各自位置
    #   ⇒ 任意一次位置变化都要把**整段**（含性格/气运/爱好/关系/好感这些不常变的内容）
    #   重发一遍，并往转录里追加一条重复消息（历史堆积 + 压缩压力）。
    #   合并成一段后，位置变化的代价 = 重发这一小段（几十字）。
    #   它还接管了原「在场」段的职责：**恒非空**（见 `format_where_segment` 的 ★），
    #   那是"群聊→单聊"能撤回陈旧在场合照的唯一手段。
    # 「后天气运」为什么独立成两段（09-26 用户拍板，与「所在」同款三段论）：
    #   后天气运随逆天改命 / 奇遇 / 赐福**动态变化**，而它原先寄生在「自身」段里
    #   ⇒ 一变就要把整段自身（性别/年龄/道号/宗门/境界/容貌/声名/性格/爱好，约 200 字）
    #   重发一遍。拆成 `npc_luck`（自身后天）与 `player_luck`（玩家后天）后，代价降到几十字。
    # 「状态」为什么再独立成两段（10-25 用户拍板，同款三段论，第四次分层）：
    #   实测（三个存档 20 个会话、313 条段消息）「自身」占总字数 **25%**、均长 287 字，
    #   而同一个会话里两次「自身」的差异只有**三个数字**：`2→3`（年份）、`7→12`（坐标）、
    #   `7048→6992`（战力）—— 为传 3 个数字重发 287 字。
    #   坐标 09-24 已搬进「所在」，剩下真正会变的是 **战力 / 体魄 / 悟性 / 心境**（战斗回血、
    #   突破、心情事件）⇒ 独立成 `npc_vital`（自身状态）/ `player_vital`（玩家状态）。
    #   · **年龄/寿元不拆**：一年才变一次，拆出来是净亏（多一段维护，收益近似零）；
    #   · **先天气运不拆**（10-25 复核后仍维持原判）：它永恒不变 ⇒ 留在主体段是**零成本**
    #     （段只在文本变化时才发），拆出来反而会让主体段短 30 字（一次性）+ 多一个段。
    #     用户原话"先天气运也是单独的一个段"已确认按此解释保留 —— 见对话记录。
    #   · 两段都**可以为空**（旧 DLL 没这些键时整段不渲染）：它们不是"状态撤回"的承载者，
    #     那个职责在「所在」段（恒非空）。
    #   · **先天气运不进这两段**（用户拍板：生而定死、永不变化）—— 它留在「自身」/「玩家」
    #     段里（恒定项放主体段零成本），见 `format_l1_context` 的两处 `命带气运`。
    #   · 玩家那段是**补缺口**：玩家改过命，NPC 却完全看不到 —— 而命数与境界/容貌/声名
    #     同属"NPC 对玩家的既有认知"（player 段本来就装那几项），不该缺；
    #   · 两段**恒非空**（同「所在」）：撤回旧状态的唯一手段是"声明新状态"，空段不发
    #     ⇒ "有→无"（后天气运被移除）方向会静默失效。无后天是**常态**，兜底成文
    #     "身无后天之气运，身体精神状况正常"（09-26 用户拍板措辞）。
    #   · 同席（`other:*`）**不放气运** —— 那属"查过资料库"、不是"抬眼能看到的"；
    #     分界见 `GameContext.BuildOthers` 的契约，模型要细节自己调 inspect_unit。
    _CTX_SEGMENTS = ("time", "self", "npc_luck", "npc_vital", "player", "player_luck",
                     "player_vital", "where", "recent_with_player", "recent", "action")
    _CTX_LABELS = {"time": "当前时间", "self": "自身", "npc_luck": "后天气运",
                   "npc_vital": "自身状态",
                   "player": "玩家",
                   "player_luck": "玩家后天气运",
                   "player_vital": "玩家状态",
                   "where": "所在",
                   "recent_with_player": "与玩家相关",
                   "recent": "近况", "action": "行动口径"}
    # 动态段前缀：同席者一人一段（`other:李四`），标签「同席·李四」。
    # 为什么一人一段而不是"在场"一整段：整段的话任一人变化都要重发全体描述，
    # 长期成本 O(N²)；一人一段是 O(N)，且每次只往转录里追加一小段。
    CTX_OTHER_PREFIX = "other:"
    # 段在 assemble 里的稳定排序键。为什么需要：`assemble` 用 sorted(order)，**等值 order 的
    # 先后取决于字典插入序**，而插入序会随"这一步哪段变了"漂移 → contexts 顺序不稳定。
    # 消息真正发给模型的顺序由 agent 的循环决定，这里只为 assemble 的确定性。
    _CTX_ORDERS = {"time": 100, "self": 101, "npc_luck": 102, "npc_vital": 1025,
                   "player": 103, "player_luck": 104, "player_vital": 1045, "where": 105,
                   "recent_with_player": 106, "recent": 199, "action": 200}
    CTX_OTHER_ORDER_BASE = 150

    @classmethod
    def ctx_other_name(cls, npc_id: Any) -> str:
        """同席者段名：`other:<npc_id>`。"""
        return f"{cls.CTX_OTHER_PREFIX}{str(npc_id or '').strip()}"

    @classmethod
    def ctx_label(cls, name: str) -> str:
        """段名 → 中文标签。动态段现场生成「同席·X」；未登记的段回退原名。"""
        if name in cls._CTX_LABELS:
            return cls._CTX_LABELS[name]
        if isinstance(name, str) and name.startswith(cls.CTX_OTHER_PREFIX):
            return "同席·" + name[len(cls.CTX_OTHER_PREFIX):]
        return name

    @classmethod
    def ctx_segment_of_label(cls, label: str) -> Optional[str]:
        """标签 → 段名（`_restore_segment_from` 从历史消息反推段名用）。
        `ctx_label` 的逆；认不出返回 None（旧归档里的未知段据此静默跳过）。"""
        lbl = (label or "").strip()
        for n, l in cls._CTX_LABELS.items():
            if l == lbl:
                return n
        if lbl.startswith("同席·"):
            who = lbl[len("同席·"):].strip()
            return cls.ctx_other_name(who) if who else None
        return None

    @classmethod
    def ctx_order(cls, name: str, other_index: int = 0) -> int:
        """段排序键（见 `_CTX_ORDERS` 注释）。未知段排在 present 与 recent 之间。"""
        if name in cls._CTX_ORDERS:
            return cls._CTX_ORDERS[name]
        if isinstance(name, str) and name.startswith(cls.CTX_OTHER_PREFIX):
            return cls.CTX_OTHER_ORDER_BASE + max(0, int(other_index))
        return 120
    # sys 观察流的 section 中文名（UI「系统组装」块行标）；未登记的段回退原名
    _SYS_LABELS = {
        "harness:identity": "身份",
        "world:basis": "世界基准",
        "world:persona_rules": "人设规则",
        "tool:usage": "工具指南",
        "deployment:persona": "人设",
        "config:player_persona": "自我人设",   # 「系统组装」折叠块里这一行的标题（缺了会显示裸段名）
    }

    @staticmethod
    def _pt_label(pt: Any) -> str:
        """坐标短语：`{x,y}` → `坐标(31,78)`；不是合法坐标返回 ""（缺 x 或 y 一律不编）。

        C# 的 L1 raw 给的是 `point{x,y}`（`GameContext.GetL1`），不给她名——地名要 `pointGridData.areaBaseID`
        查 `ConfWorldAreaBaseItem` 才有，而那是"州/区"级（白源区/永宁州），不是落脚点。
        用户拍板：**显示坐标即可**。措辞与 `inspect_unit` brief 的「坐标(x,y)」保持一致，
        模型在两条路径上看到同一个词。
        """
        if not isinstance(pt, dict):
            return ""
        x, y = pt.get("x"), pt.get("y")
        if x is None or y is None:
            return ""
        return f"坐标({x},{y})"

    @staticmethod
    def _getf(raw: Dict[str, Any], *keys: str, default: Any = None) -> Any:
        if not isinstance(raw, dict):
            return default
        for k in keys:
            if raw.get(k) is not None:
                return raw[k]
        return default

    @staticmethod
    def _as_int(v: Any) -> Optional[int]:
        """C# raw 里的数值 → int；拿不到返回 **None（不是 0）**。

        为什么不用 `int(v)`：`None` / `"?"` / 空 dict 都会抛，而调用点全在 L1 成文路径上 ——
        抛一次整段「自身」就没了。为什么必须区分 None 与 0：0 岁、0 载是**有意义的值**，
        不能和「没这个字段」混为一谈：前者照写，后者整项不写。
        """
        if v is None or isinstance(v, bool):
            return None
        if isinstance(v, int):
            return v
        if isinstance(v, float):
            return int(v)
        try:
            return int(float(str(v).strip()))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _intim_tier(intim: Any) -> str:
        """好感数值 → NPC 心里的亲疏措辞（避免把系统数值当台词："好感已达200" 太像面板）。

        ★ 11-12：档位词**与 `social_relation.value` 的 schema 逐档对齐** ★
        原先这里是五个自造档（情深意笃 / 情谊深厚 / 日渐亲近 / 交情尚可 / 相识未久），
        而 schema 里给模型的是"一次给多少"（寒暄 5~10、小忙 15、交心 30…）——
        **两套话，模型接不上**：它知道"此刻日渐亲近"，却不知道"再从 87 加 30 会变成什么"，
        于是调工具时没有目标感。现在两处用同一套档位名（陌路/泛泛之交/亲近/知己/生死之交，
        负向 嫌隙/结怨/深仇/不共戴天），模型看到「亲近」就知道再加 60 会到「知己」。

        保留原措辞作为**后半截语气注解**（"情谊深厚，彼此信赖"这类），因为它对语气的
        指示比档位名更强，且既有测试与实机观感都按它调过。
        数值仍不写进正文（模型会把面板数字念出来）。
        """
        try:
            n = float(intim)
        except (TypeError, ValueError):
            return ""
        if n >= 180:
            return "，情谊已是生死之交——情深意笃、生死可托"
        if n >= 120:
            return "，情谊已到知己——深厚，彼此信赖"
        if n >= 60:
            return "，情谊日渐亲近"
        if n >= 20:
            return "，泛泛之交，交情尚可"
        if n >= 0:
            return "，尚是陌路，相识未久"
        if n > -60:
            return "，心存嫌隙"
        if n > -120:
            return "，已然结怨"
        if n > -180:
            return "，结下深仇"
        return "，已是不共戴天"

    @staticmethod
    def _relation_phrase(rel: Any, ta: str) -> str:
        """关系取值 → 中文短语（自身/玩家/同席三段共用同一口径）。

        为什么要单列"不是关系的关系"：`陌生`/`无` 这类取值套 `你与{ta}结着{rel}之谊`
        会拼出**「你与他结着陌生之谊」**这种病句。它们在 raw 里是合法取值（C# 无关系时
        就发"陌生"），所以必须在这里挡掉——而不是假设"有 relation 就一定是有交情"。
        """
        r = str(rel or "").strip()
        if not r:
            return ""
        if r in ("仇人", "仇敌", "敌对", "仇家"):
            return f"你与{ta}已是{r}"
        if r in ("陌生", "陌生人", "无", "无关系", "不认识", "素不相识", "非亲非故"):
            return f"你与{ta}素不相识"
        return f"你与{ta}结着{r}之谊"

    @classmethod
    def _personality_phrase(cls, per: Any) -> str:
        """性格 → 「本性X，行事偏Y」短语（**自身段与同席段共用同一口径**）。

        注解与名字**同下标**给出（C# `PersonalityOf` 保证对齐）；缺注解 / 旧 C# 数据
        退化成只给名字。

        为什么单列：这段逻辑**自身段已经写过一遍**（09-13 补的"注解成文"），同席段若再抄
        一份，将来改一处必漏另一处 —— 同 `_relation_phrase` 抽出来的理由。注解才是行为准则，
        「仁善/睚眦」这种两字标签模型未必知道游戏里的确切含义。
        """
        if not isinstance(per, dict):
            return ""
        inner = per.get("inner") if isinstance(per.get("inner"), str) else ""
        inner = inner.strip()
        outer = per.get("outer") if isinstance(per.get("outer"), list) else []
        outer = [o.strip() for o in outer if isinstance(o, str) and o.strip()]
        in_tag = cls._name_desc(inner, per.get("inner_desc"))
        od = per.get("outer_desc") if isinstance(per.get("outer_desc"), list) else []
        out_tags = [cls._name_desc(nm, od[i] if i < len(od) else None) for i, nm in enumerate(outer)]
        if in_tag and out_tags:
            return f"本性{in_tag}，行事偏{'与'.join(out_tags)}"
        if in_tag:
            return f"本性{in_tag}"
        if out_tags:
            return f"行事偏{'与'.join(out_tags)}"
        return ""

    @staticmethod
    def _title_phrase(raw_title: Any) -> str:
        """道号 → 「道号「X」」；空 / 诊断占位串返回 ""。

        ★ C# 查表失败时会回一个 `[待真机]道号名(id=…)` 的**给开发者看的占位串**
        （`UnitSnapshot.cs` 的 title 分支），它绝不能进上下文 —— 模型会照着念出来。
        自身段 09-13 就挡过一次，这里抽成共用判据，同席段一并受保护。
        """
        t = raw_title.strip() if isinstance(raw_title, str) else ""
        if not t or t.startswith("[待真机]"):
            return ""
        return f"道号「{t}」"

    @staticmethod
    def _fmt_mood(mood: Any) -> str:
        if isinstance(mood, (int, float)):
            n = float(mood)
            if n >= 80:
                return "心境甚佳，神采焕发"
            if n >= 55:
                return "心境平和，沉着稳妥"
            return "心境郁郁，眉宇间带着倦意"
        return "心境如常"

    #: Unity 富文本标签（`<color=#004FCA>` / `</color>` / `<size=25>` / `<sprite …>` / 游戏自带的 `<g>`…）。
    #: **必须在截断之前剥**（见 `_strip_rich` 的 ★），所以这里只做一次编译。
    _RICH_TAG_RE = re.compile(r"<[^<>]{1,80}>")

    @staticmethod
    def _strip_rich(s: str) -> str:
        """剥 Unity 富文本标签。

        ★ 为什么必须**先剥再截断**（10-25 真机发现）★
        原来 `_name_desc` 是「先 `d[:60]` 再拼 `…`」，于是气运 desc 里的
        `<color=#004FCA>阴阳…` 被切成 `<co…` —— 半个标签，**比不剥更糟**：
        它既不是合法标签（`_RICH_TAG_RE` 匹配不到、C# 侧 `StripRichTags` 也认不出），
        又照样是噪声，模型会把它当正文的一部分念出来。
        真机实证：三个存档 20 个会话里有 1 段 `自身` 出现了 `阴阳<co…`。

        ★ 为什么不用白名单 ★
        标签种类会变（`<g>`/`<y>` 是游戏自带配色、`<sprite>` 是图标、`<size>` 是字号）。
        白名单必然漏掉下一种，而这里**通用括号机**对所有 `<…>` 一视同仁。
        代价：正文里真出现裸 `<…>` 会被吃掉 —— 实测 0/313 段，代价为零。
        """
        if not s:
            return ""
        return SystemPrompt._RICH_TAG_RE.sub("", s)

    @classmethod
    def _name_desc(cls, name: Any, desc: Any) -> str:
        """「名字（注解）」成文：**先剥富文本**、折叠换行、截 60；无注解/空名只给名字（不吐空括号）。
        气运 desc 与性格注解共用本口径（性格注解=面板「内在性格：邪恶，唯己所欲…」冒号后整句）。"""
        n = str(name).strip() if name is not None else ""
        if not n:
            return ""
        d = cls._strip_rich(str(desc).replace("\r", "").replace("\n", " ").strip()) if desc else ""
        if len(d) > 60:
            d = d[:60] + "…"
        return f"{n}（{d}）" if d else n

    @staticmethod
    def _luck_names(v: Any) -> List[str]:
        """气运条目成文"名（desc截60）"：C# raw 为 [{name,desc}]（desc 含换行折叠），容错纯字符串（stub）。
        每侧截前 5，防撑爆自身段文本。"""
        if not isinstance(v, list):
            return []
        out: List[str] = []
        for x in v:
            if isinstance(x, dict):
                n, d = x.get("name"), x.get("desc")
            else:
                n, d = (x if isinstance(x, str) else None), None
            if not n:
                continue
            out.append(SystemPrompt._name_desc(n, d))
            if len(out) >= 5:
                break
        return out

    def _incr_render(self, scope: str, name: str, items: List[str], head: str = "") -> str:
        """「经历类」段的增量成文（`near` 见 `agent/l1_incremental.py` 模块头）。

        · 首次：全量（初始状态声明，不加 head）；
        · 有新增：只渲染新增的，加 `head` 前缀（告诉模型这是追加）；
        · 无新增：返回上次的文本 —— 差分因此不触发（**不发消息、不发 remove**）。

        编号从 1 重新开始（每次增量都是一小段独立阅读单元）：沿用"第 N 条"的全局序号
        会让增量里的编号突然跳到 37，模型会以为中间丢了内容。
        """
        seg = self._incr.get(scope, name)
        return seg.render(
            items,
            renderer=lambda fresh: "；".join(f"{i + 1}. {t}" for i, t in enumerate(fresh)),
            marker=head,
        )

    @staticmethod
    def _vital_parts(mood: str = "", power: Any = None, health: Any = None,
                     talent: Any = None, beauty: Any = None, reputation: Any = None,
                     ta: str = "", with_self_subject: bool = False) -> List[str]:
        """**状态**段成文（10-25 用户拍板：从「自身」/「玩家」里拆出来的易变项）。

        为什么单独成段（实测依据，别删）：三个存档 20 个会话、313 条段消息里
        「自身」占总字数 25%（均长 287 字），而同一会话相邻两次「自身」的差异只有
        `2→3`（年份）`7→12`（坐标）`7048→6992`（战力）—— 为 3 个数字重发 287 字。
        坐标已由「所在」段接管（09-24），剩下的会变项就是**心境 / 战力 / 体魄 / 悟性**
        （NPC）与**魅力 / 声名 / 心境**（玩家）。拆出来后变化的代价降到几十字。

        两段都**允许为空**（旧 DLL 没这些键 ⇒ 整段不渲染）。它们**不承载"状态撤回"**——
        那个职责在「所在」段（恒非空），所以这里不需要兜底句：这一段的内容全是
        "有值才写"的客观读数，空就是"不知道"，写兜底反而是编造。

        参数取舍：`with_self_subject` 为 NPC 侧（"一身战力约X"），否则用 `ta`
        （玩家侧代词，"他魅力超凡"）。两侧措辞不同是因为语气主体不同，不是两套逻辑。
        """
        parts: List[str] = []
        if with_self_subject:
            # NPC 自述：无需主语（模型自己就是这个人）
            if power is not None:
                parts.append(f"一身战力约{power}")
            if health is not None:
                parts.append(f"此刻体魄{health}")
            talent_v = SystemPrompt._as_int(talent)
            if talent_v is not None:
                parts.append(f"悟性{talent_v}")
            if mood:
                parts.append(mood)
        else:
            # 玩家侧：必须带主语 —— 这一段独立成段，"魅力超凡"没有主语会指代不明。
            bits: List[str] = []
            if beauty is not None:
                bits.append(f"魅力{beauty}")
            if reputation is not None:
                # 措辞「在世人眼中」而非「声名」：声誉档位是**谓词短语**（「声名远扬」），
                # 再接「声名」会拼成「声名声名远扬」。与自身段同一口径。
                bits.append(f"在世人眼中{reputation}")
            if bits:
                parts.append(ta + "、".join(bits))
            if mood:
                parts.append(ta + "今日" + mood)
        return parts

    @classmethod
    def _luck_added_segment_text(cls, luck: Any, who: str = "") -> str:
        """**后天气运**段成文（09-26 用户拍板：只有后天独立成段；先天气运留在自身/玩家段）。

        为什么只装后天：先天气运**生而定死、永不变化**（用户拍板）—— 留在主体段里不会产生
        任何重发；后天气运随逆天改命/奇遇/赐福变 ⇒ 独立成段，变化代价降到几十字。

        ★ **恒非空**（用户拍板）★：无后天气运是**常态**（多数人一生不逢后天奇遇），兜底成
        "身无后天之气运，身体精神状况正常" —— 不是为了好看，而是**差分语义**：模型看不到屉、
        只读转录，撤回一个状态的唯一办法是"声明新状态"；空文本段 `render_context_segment`
        直接不发（返回 ""）⇒ 后天气运被移除时"有→无"这个方向会静默失效，模型会一直以为还挂着。
        兜底句让两个方向都变成"内容变化"。（同「所在」段的 ★，见 `format_where_segment`。）

        `who` 非空时作主语（玩家段用真名，段是独立的、"他"会指代不明）。
        条目成文沿用 `_luck_names`（"名（desc截60）"，与主体段先天那句同一措辞）。
        """
        added: List[str] = cls._luck_names(luck.get("added")) if isinstance(luck, dict) else []
        head = (str(who).strip() + "，") if str(who).strip() else ""
        if added:
            return head + "后天又得" + "、".join(added) + "。"
        return head + "身无后天之气运，身体精神状况正常。"

    def format_l1_context(self, npc_id: str, raw: Any, roster: Any = None,
                          scope: Any = None) -> Dict[str, str]:
        """把 bridge.get_context 的结构化 raw 拼成若干段连贯中文，供 DialogueAgent 逐段差分写屉。

        段清单**动态**（键即当前应有的段）：
          - `time` / `self` / `npc_luck` / `player` / `player_luck` / `where` /
            `recent_with_player` / `recent` / `action`：基础段
          - `npc_luck` / `player_luck`（09-26 新增）：**后天气运**从「自身」段拆出
            （**先天气运仍留在**「自身」/「玩家」段 —— 它恒定不变，不进差分段），**恒非空**
            （无后天 ⇒「身无后天之气运，身体精神状况正常」）；为什么拆见 `_CTX_SEGMENTS` 上方
          - `other:<npc_id>`：每个同席者一段（第三人称、只写可观测面）

        ★ 「恒非空」是**差分撤回的必要条件**（`present` 的历史教训；现由 `where` 与两段气运承担）：
          模型看不到屉，只读转录；而**转录是事件流，撤回一个状态的唯一办法是声明新状态**。
          若某段只在"有值"时才成文，则"有→无"这个方向段会变空 → 按现有实现不发消息
          （`render_context_segment` 对空文本返回 ""）→ 转录里那条旧状态永远留着
          （历史症状：群聊→单聊时 NPC 还以为李四站在旁边）。
          所以退化情形也各有非空文案（「此刻并无他人在场」/「身无后天之气运，身体精神状况正常」），
          两个方向都是内容变化 ⇒ 差分必然触发 ⇒ 不需要给 `other:*` 段另写"撤回"逻辑。

        `roster` 是**除自己以外**的在场 NPC 名单（玩家不在其中——玩家恒在场），由 Python 侧
        （后续是 Room.members）给出。判据是**名单内容**而非人数：李四走、王五来，人数不变但
        名单变了，比对文本天然覆盖这种情况。

        桥只负责回 raw（通信），文案由本方法一次性成文。字段缺失即兜底或省略，绝不抛错。
        """
        raw = raw if isinstance(raw, dict) else {}
        self_raw = raw.get("self") if isinstance(raw.get("self"), dict) else raw
        # 增量段的**作用域**：同一进程里同时有多个 NPC 会话（群聊/多开窗），
        # 每个 NPC 的「近况」各自攒自己的"已发过"集合 —— 共用一个就会互相把对方的条目
        # 当成"已经发过"，症状是某个 NPC 的经历**永远缺几条**且没有任何报错。
        # 默认落到 npc_id：调用方不传也能正确隔离；会话 id 与 npc_id 同值时无差别。
        _incr_scope = str(scope if scope is not None else npc_id or "")

        # —— 当前时间段（L1 也报日期）——
        # C# `GameContext.NowBlock()` 给 `{year, month, day, text}`。`text` 是成文**唯一来源**
        # （C# `UnitSnapshot.CnDate`，与玩家消息前缀 `[1年M月D日]` **同一函数** ⇒ 逐字节相同，
        # 模型在两边看到同一个串，天然学会"方括号里那个就是日期"）。
        # 只有数字（旧载荷）/ 没有 `text` 时按**同一种措辞**自行拼，绝不引入第二种写法；
        # 完全没有 now（旧 DLL）→ 空串 → 整段不渲染（绝不编造日期）。
        now = raw.get("now") if isinstance(raw.get("now"), dict) else {}
        time_text = (str(now.get("text")) if now.get("text") is not None else "").strip()
        if not time_text:
            ny, nm, nd = now.get("year"), now.get("month"), now.get("day")
            if isinstance(ny, int) and isinstance(nm, int):
                time_text = f"{ny}年{nm}月" + (f"{nd}日" if isinstance(nd, int) and nd > 0 else "")

        # —— 自身段 ——
        realm = self._getf(self_raw, "realm")
        sect = self._getf(self_raw, "sect")
        # ★ 位置不在这里 ★（09-24 用户拍板）：`栖居于X` / 坐标整体搬进「所在」段
        # （`format_where_segment`）——它是这一段里**最易变**的一项，而这一段装的
        # 性别/年龄/道号/宗门/境界/容貌/性格几乎不变；绑在一起 = 挪一步就整段重发。
        # （**后天气运**同理，09-26 另拆 `npc_luck`：逆天改命/奇遇会改它，不该连累本段；
        #   先天气运恒定不变，**留在本段**。）
        mood = self._getf(self_raw, "mood")
        power = self._getf(self_raw, "power")
        health = self._getf(self_raw, "health")
        self_parts: List[str] = []
        # 性别（补漏）：C# `raw.self.sex` 一直有，这里从没读过 —— 于是模型不知道自己是男是女，
        # 自称/被称/剧情合理性全凭猜。取值 "男"/"女"（C# SexCn）；异常值不外泄（不写成"一位None子"）。
        self_sex = self_raw.get("sex")
        # 补齐 年龄 / 寿元 —— 同一类漏读，但这次是**一整批 8 个字段**一起丢
        #   （age/life/hobby/title/beauty/reputation/talent/race）。C# `GameContext.GetL1` 从 09-08 起
        #   就在 raw 里发这些键（它们在 `UnitSnapshot.Build` 的**无门控**段，传 ["brief"] 必然取到），
        #   成文侧一个都没读 —— 于是模型不知道这 NPC 多大年纪、还能活多久、有没有道号。
        #   本文件里已经记过三次同类事故（性格注解、性别、位置），原话是「C# raw 有字段 ≠ 模型看得到」。
        #   C# 给的是**年**（`SetAttr(..., toYear: true)` 已把账面月 /12），直接写「岁」即可。
        #   取不到就整项不写 —— 绝不出现「今年？岁」这种把缺失抖给模型看的写法。
        age_v = self._as_int(self_raw.get("age"))
        life_v = self._as_int(self_raw.get("life"))
        if npc_id:
            intro = f"你是{npc_id}，一位{self_sex}子" if self_sex in ("男", "女") else f"你是{npc_id}"
            if age_v is not None:
                intro += f"，今年{age_v}岁"
            if life_v is not None:
                intro += f"，寿元{life_v}载"
            self_parts.append(intro)
        # 道号（面板「道号」栏；C# 查 ConfAppellationTitleBase 得中文名）：与姓名并列的身份标识。
        # 占位串过滤与同席段**共用 `_title_phrase`**（同一件事不留两套写法）。
        _tp = self._title_phrase(self_raw.get("title"))
        if _tp:
            self_parts.append(_tp)
        sect_ = f"隶属{sect}一脉" if sect else ""
        realm_ = f"如今已是{realm}修为" if realm else ""
        # 种族：默认人族 —— 是「人族」就不写（`world_persona_rules.txt` 已写明
        # 「种族默认为人族」，每轮多这 3 个字纯属噪声）；只有异族（魔族 / roleRace 自定义）才点名。
        race = self_raw.get("race") if isinstance(self_raw.get("race"), str) else ""
        race = race.strip()
        race_ = f"身为{race}" if race and race != "人族" else ""
        tags = [t for t in (sect_, realm_, race_) if t]
        if tags:
            self_parts.append("，".join(tags))
        # 容貌 / 声名：用 C# 给的**档位词**（`beauty_label`/`reputation_label`，
        # 与「玩家」段同一套面板口径：容貌出众 / 声名默默无闻），不写裸数值 ——
        # 「魅力3129」是面板口径，一旦进上下文模型就会拿数字当台词说（同 `_intim_tier` 的教训）。
        # 旧 DLL 没有这两个键 → 整项不写（不是回归：以前本来也没有）。
        blabel = self_raw.get("beauty_label") if isinstance(self_raw.get("beauty_label"), str) else ""
        rlabel = self_raw.get("reputation_label") if isinstance(self_raw.get("reputation_label"), str) else ""
        blabel, rlabel = blabel.strip(), rlabel.strip()
        # 措辞为什么是「在世人眼中」而不是「声名」（实测拼出来的病句）：
        #   声望档位词本身是**谓词短语**（初出茅庐 / 崭露头角 / 名震一时 / 声名远扬 / 名满天下 /
        #   举世闻名，见 world_persona_rules.txt），前面再接一个「声名」会拼出「声名声名远扬」。
        #   而容貌档位是形容词（出众 / 超凡 / 仙姿），「容貌出众」成立 —— 两者不能套同一个模板。
        face = [t for t in (f"容貌{blabel}" if blabel else "",
                            f"在世人眼中{rlabel}" if rlabel else "") if t]
        if face:
            self_parts.append("，".join(face))
        # ★ 心境 / 战力 / 体魄 / 悟性**不在这里** ★（10-25 用户拍板，第四次分层）
        #   这四项是「自身」段里**唯一真正频繁变化**的东西（战斗回血、突破、心情事件），
        #   而它们原先和性别/年龄/宗门/境界/容貌/声名/性格/爱好绑在一起 ⇒ 战力动一点，
        #   287 字的整段就重发一遍。实测：同一会话两次「自身」只差 `7048→6992` 三个数字。
        #   现在独立成 `npc_vital`（标签「自身状态」），变化的代价降到几十字。
        #   **年龄/寿元刻意留在这里**：一年才变一次，拆出去是净亏（见 `_CTX_SEGMENTS` 上方）。
        npc_vital_parts = self._vital_parts(
            mood=self._fmt_mood(mood) if mood is not None else "",
            power=power, health=health,
            talent=self._as_int(self_raw.get("talent")),
            with_self_subject=True)
        # 爱好：C# `HobbyNames` 给的是**中文名字数组**（已 GameTool.LS），不是对象。
        # 与「本性」并列 —— 都是"我是谁"的一部分，放在性格之前读起来更顺。
        # 截前 5（同气运的口径），防某个 NPC 爱好特别多时把自身段撑长。
        hobby = self_raw.get("hobby")
        if isinstance(hobby, list):
            hs: List[str] = []
            for h in hobby:
                nm = h.get("name") if isinstance(h, dict) else h
                nm = nm.strip() if isinstance(nm, str) else ""
                if nm:
                    hs.append(nm)
                if len(hs) >= 5:
                    break
            if hs:
                self_parts.append("雅好" + "、".join(hs))
        # 性格（与 NPC 面板一致：内 1 + 外 0~2；C# raw.personality 提供）
        # 注解成文（补漏）：C# 的 personality 里 inner_desc/outer_desc 与名字**平行对齐**给出
        # （= 面板「内在性格：邪恶，唯已所欲…」冒号后整句，conf xdash_54sd + LS）。
        # 「仁善/睚眦」这种两字标签模型未必知道游戏里的确切含义，注解才是行为准则，必须带上。
        # 成文逻辑抽到 `_personality_phrase`，与同席段共用（同 `_relation_phrase` 的理由）。
        _per_phrase = self._personality_phrase(self_raw.get("personality"))
        if _per_phrase:
            self_parts.append(_per_phrase)
        # 先天气运**留在自身段**（09-26 用户拍板）：生而定死、永不变化 ⇒ 放主体段零重发成本；
        # 只有**后天气运**独立成段（`npc_luck`，随逆天改命/奇遇变）—— 见 `_CTX_SEGMENTS` 上方。
        _luck_raw = self_raw.get("luck") if isinstance(self_raw.get("luck"), dict) else {}
        _born = self._luck_names(_luck_raw.get("born"))
        if _born:
            self_parts.append("命带气运" + "、".join(_born))
        self_text = "。".join(p for p in self_parts if p) + "。"
        npc_vital_text = "。".join(p for p in npc_vital_parts if p) + ("。" if npc_vital_parts else "")

        # —— 玩家段 ——
        player = raw.get("player") if isinstance(raw.get("player"), dict) else raw.get("relations", {}).get("player", {})
        if not isinstance(player, dict):
            player = {}
        pname = player.get("name") or "玩家"
        prealm = player.get("realm")
        psect = player.get("sect") or ""
        pbeauty = player.get("beauty")
        prepu = player.get("reputation")
        pmood = player.get("mood")
        # 注：`player.point` / `player.same_grid` 不再在这里读 —— 它们整体归「所在」段
        # （见本方法末尾 `out["where"]` 与 `format_where_segment`）。
        prel = player.get("relation")
        pintim = player.get("intim")
        # 玩家性别：C# `relations.player.sex` 是这次新加的；缺省时回退「他」= 旧行为，
        # 所以旧 C# 载荷不会回归。中文里代词绕不开，写死「他」会把女玩家/女道侣写成"你与他结着道侣之谊"。
        psex = player.get("sex")
        ta = "她" if psex == "女" else "他"
        p_parts: List[str] = []
        intro = f"此刻与你言语相对的是{pname}"
        if psex in ("男", "女"):
            intro += f"，一位{psex}子"
        # ★ 位置不在这里 ★（09-24 用户拍板）：玩家坐标与"同处一地 / 异地相隔"整体搬进
        # 「所在」段。那两项是最易变的内容（在地图上挪一步、movement 召唤/传送都会翻），
        # 而这一段装的关系/好感/魅力/声名/心情相对稳 —— 绑在一起就是"挪一步整段重发"。
        if prealm:
            intro += f"，{prealm}修为"
        if psect:
            intro += f"，隶属{psect}一脉"
        else:
            intro += "，一介散修游历于八荒"
        p_parts.append(intro)
        if prel:
            rel_s = self._relation_phrase(prel, ta)
            if rel_s and pintim is not None:
                rel_s += self._intim_tier(pintim)
            if rel_s:
                p_parts.append(rel_s)
        # ★ 魅力 / 声名 / 心境**不在这里** ★（10-25 用户拍板，与自身段同款第四层拆分）
        #   这三项会变（战斗/事件改心境，际遇改魅力与声名），而关系称谓/境界/宗门/性别
        #   几乎不变 —— 绑在一起就是"心情变一下，88 字整段重发"。
        #   独立成 `player_vital`（标签「玩家状态」）。
        #   注意玩家**没有 power/health/talent 键**（C# 只给 NPC 侧采战力与体魄），
        #   所以玩家这一段只有魅力/声名/心境三项 —— 不要照抄 NPC 那份。
        player_vital_parts = self._vital_parts(
            mood=self._fmt_mood(pmood) if pmood is not None else "",
            beauty=pbeauty, reputation=prepu, ta=ta)
        # 先天气运**留在玩家段**（与自身段同口径同措辞"命带气运X、Y"）：恒定项不进差分段。
        # 09-26 之前玩家段**完全没有气运** —— 这里补上玩家的先天（后天的另见 player_luck 段）。
        _p_luck = player.get("luck") if isinstance(player.get("luck"), dict) else {}
        _p_born = self._luck_names(_p_luck.get("born"))
        if _p_born:
            p_parts.append("命带气运" + "、".join(_p_born))
        player_text = "，".join(p for p in p_parts if p) + "。"
        player_vital_text = "，".join(p for p in player_vital_parts if p)
        if player_vital_text:
            player_vital_text += "。"

        # —— 后天气运两段（09-26 用户拍板；**恒非空**，见 `_luck_added_segment_text` 的 ★）——
        # 自身：`raw.luck.added`；玩家：`raw.relations.player.luck.added`（09-26 新补的键 ——
        # 数据本就在 C# 的 psnap 里，过去只挑了 realm/sect/sex/mood 就把它丢了）。
        # 玩家段带主语（真名），因为它是独立段、"他"会指代不明。
        npc_luck_text = self._luck_added_segment_text(self_raw.get("luck"))
        player_luck_text = self._luck_added_segment_text(player.get("luck"), pname)

        # —— 近况段（经历 ≤10 条截取） ——
        items: List[str] = []
        recent = raw.get("recent")
        logs = raw.get("logs") if isinstance(raw.get("logs"), list) else None

        def _to_str(x: Any) -> str:
            """经历条目 → 一行纯文本。

            ★ 必须在这里剥富文本（10-25）★
            C# 侧 `CleanLogText` 已补了 `StripRichTags`，但**用户装的是旧版 DLL 时后端
            仍是新版** —— 兼容期里 Python 不能假设对面已经洗干净了。
            实测：不剥的话「近况」段里会带着 `<color=#004FCA>…</color>` 进模型上下文。
            剥在这里而不是在渲染处：`items` 同时被增量段拿去当**判等键**，
            带标签的键会让"内容没变"被误判成"变了"⇒ 白重发。
            """
            if isinstance(x, str):
                return self._strip_rich(x)
            if isinstance(x, dict):
                return self._strip_rich(
                    x.get("text") or x.get("what") or json.dumps(x, ensure_ascii=False))
            return self._strip_rich(str(x))

        if isinstance(recent, str):
            items = [_to_str(x) for x in recent.splitlines()]
            items = [x for x in items if x]
        elif isinstance(recent, list):
            items = [_to_str(x) for x in recent]
        elif logs:
            items = [_to_str(x) for x in logs]
        items = [x for x in items if x][:10]
        # ：去掉正文里的「近况经历：」前缀 —— 段标签已经是「近况」，渲染出来是
        # `Current runtime context —— 近况：近况经历：1. …`，两个同义词叠在一起纯属噪音。
        # 现在直接 `Current runtime context —— 近况：1. …`。
        # ★ 增量成文（10-25 用户拍板）★
        #   原来是 `"；".join(i+1. t)` —— 新增一条就要把 10 条全部重编，文本必然不同
        #   ⇒ **整段重发**。而经历日志只增不减，于是这个 NPC 每打一次副本就重发 300 字。
        #   实测占 L1 总字数 31%，是最大的一块（依据见 `agent/l1_incremental.py` 模块头）。
        #   现在只发**没见过的那几条**，键就是那条成品字符串本身 —— C# 已经给它带了
        #   `(N年M月) ` 前缀，所以「先按月份、再按文本」两级去重合成一条判据（见该模块头）。
        recent_text = self._incr_render(_incr_scope, "recent", items,
                                        head="（以下为新增经历）")

        # —— 与玩家相关段（C# 全史筛选，UnitSnapshot.RecentWithPlayer 取最近 RecentPlayerCap 条）——
        # 「近况」装"他最近经历了什么"；本段装"**他与玩家之间发生过什么**"（游戏 NPC 面板·经历页
        # 「与我相关」页签同语义，LocalText npcInfo_yuwoxiangguan）。独立一段而不是塞进「近况」：
        #   ① 语义：两码事，混进近况会让"这段为什么变了"难以归因（与「当前时间」同款三段论）；
        #   ② 成本：段是逐段差分的 —— 他的日常变了不该连累本段重发，反之亦然；
        #   ③ 覆盖：本段筛**全史**里的玩家相关条目，久远的往来不会因近期日常太多被挤出上下文。
        # 与「近况」**允许重复**（同一条既新又涉及玩家时两边都出现，信息不丢）；刻意不互斥。
        wp_items: List[str] = []
        wp_raw = raw.get("recent_with_player")
        if isinstance(wp_raw, str):
            wp_items = [_to_str(x) for x in wp_raw.splitlines()]
            wp_items = [x for x in wp_items if x]
        elif isinstance(wp_raw, list):
            wp_items = [_to_str(x) for x in wp_raw]
        wp_items = [x for x in wp_items if x][:10]
        # 段标签已是「与玩家相关」，正文不再写一遍「与玩家相关：」 —— 两个同义词叠着纯属噪音
        # 增量成文，与「近况」同一套（用户口径：和经历一样）
        wp_text = self._incr_render(_incr_scope, "recent_with_player", wp_items,
                                    head="（以下为新增往来）")

        # —— 同席名单：roster（Python 的真相）∪ raw.others 的 id（C# 更清楚现实）——
        # 两者取并集而不是二选一：roster 是"该有谁"，raw.others 是"实际解析到谁"。
        # 只认一个都会漏（C# 尚未实现 others 时 roster 仍要能工作）。
        _self_id = str(npc_id or "").strip()
        if isinstance(roster, (set, frozenset)):
            roster = sorted(roster, key=str)
        elif not isinstance(roster, (list, tuple)):
            roster = []

        # ① 先扫 C# 的明细：一次拿到 by_id，也拿到"谁已经不在世界上了"。
        #    `resolved:false` = C# 认得这个名字、但世界里已经没有这个单位（死了/离场/换存档）。
        #    **必须在 roster 之前收下** —— 否则 roster 会把他重新加回在场名单，
        #    于是「在场」段对模型宣称一个不存在的人还在场。而「在场」段是**权威声明**
        #    （单聊也有非空文案，靠它顶掉旧的在场合照），它说谎的代价比别的段都大。
        #    判据严格用 `is False`：字段缺失 / 老 C# 根本不发 = **未知**，不能当"人没了"。
        by_id: Dict[str, Any] = {}
        dead: set = set()
        _others = raw.get("others") if isinstance(raw.get("others"), list) else []
        for o in _others:
            if not isinstance(o, dict):
                continue
            oid = str(o.get("id") or o.get("name") or "").strip()
            if not oid or oid == _self_id:
                continue
            by_id[oid] = o
            if o.get("resolved") is False:
                dead.add(oid)

        # ② roster = "谁在这个场合里"（Python 的真相）；by_id = "这些人实际长什么样"（C# 更清楚现实）。
        #
        # ★ 分工是**单向**的：roster 决定名单，others 只提供明细。**不做并集**。
        #
        # 早期实现取并集，理由是"C# 尚未实现 others 时 roster 仍要能工作；C# 认为在场、
        # Python 名单还没跟上时也不该丢人"。前半句由 `roster is None` 的兜底覆盖；
        # 后半句在实践中是**有害的**：`others` 是 C# 侧的**快照**，它可以过期、可以过宽
        # （例如按"同格所有人"而非按名单收集）。一旦并集，**C# 的快照就能反向扩充
        # Python 的权威名单** —— 实测症状：把王五踢出群后，它的 `others` 仍在 raw 里，
        # 于是「在场」段照旧写着王五，差分不触发、模型继续以为王五坐在旁边。
        # 而且这个故障是**静默**的（回合照跑、日志无异常，只有措辞不对）。
        #
        # `dead`（`resolved: false`）两个方向都要剔除 —— 名单说"该在"、现实说"人不在了"，
        # 以现实为准。
        ids: List[str] = []
        if roster is None:
            # 调用方没给名单（未接线的旧路径 / 直接调本函数）：只能用 C# 的 others 当名单，
            # 行为与引入 roster 之前逐字相同。
            for oid in by_id:
                if oid not in dead:
                    ids.append(oid)
        else:
            for x in roster:
                sid = str(x or "").strip()
                if sid and sid != _self_id and sid not in dead and sid not in ids:
                    ids.append(sid)

        # 键的**插入顺序 = 发给模型的顺序**（dialogue_agent 按 segs 的键序遍历）：
        #   time → self → npc_luck → player → player_luck → where → recent_with_player
        #   → other:* → recent
        # 先知道"我是谁/我的后天际遇"，再读"对面是谁/他的后天际遇"，然后"我们在哪、身边还有谁"（where），
        # 接着"我们之间发生过什么"，再逐个给同席明细，最后才是历史。
        out: Dict[str, str] = {
            "time": time_text,
            "self": self_text,
            # 「后天气运」两段：紧邻各自主体（自身→后天气运、玩家→玩家后天气运）——
            # 先读"我是谁/我的后天际遇"，再读"对面是谁/他的后天际遇"。
            # **恒非空**（见 _CTX_SEGMENTS 上方）。先天气运在 self / player 段正文里。
            "npc_luck": npc_luck_text,
            # 「状态」段（10-25）：从自身段拆出的**易变项**（战力/体魄/悟性/心境）。
            # 紧跟在「后天气运」之后 —— 先"我是谁"，再"我现在什么状态"。
            "npc_vital": npc_vital_text,
            "player": player_text,
            "player_luck": player_luck_text,
            # 玩家状态（魅力/声名/心境）：玩家侧**没有** power/health/talent 键（见 `_vital_parts`）。
            "player_vital": player_vital_text,
            # 「所在」：**位置与在场者名单唯一的出口**（自身/玩家/同席三段都不再写位置）——
            # 那些内容最易变，单独成段才能把每次变化的代价压到几十字。
            # **恒非空**：它同时接管了原「在场」段的撤回语义（见其 docstring 的 ★）。
            "where": self.format_where_segment(self_raw, player, ids, by_id),
            # 紧跟在「玩家」之后：先知道对面是谁，再读"我们之间发生过什么"。
            # 空则整段不渲染（render_context_segment 对空返回 ""）。
            "recent_with_player": wp_text,
        }
        for oid in ids:
            out[self.ctx_other_name(oid)] = self.format_other_segment(
                by_id.get(oid) or {"id": oid, "name": oid})
        out["recent"] = recent_text
        # —— 行动口径段（09-21）——
        # 为什么把**日期拼进段文本**：本段正文是常量，而 `_pre_step` 的差分判据是"文本没变就不发"
        # （`dialogue_agent._retained_ctx`）。常量第一次发完就永远不再发 ⇒ 模型只在开局听到一次，
        # 后面 20 轮"只说话不办事"的前例把它盖掉（实测：长历史关思考，该调时只有 48% 真去调）。
        # 拼上日期 ⇒ 与本文件「当前时间」段同一套口径（**每游戏日重发一次**），时间一跳就再提醒一条，
        # 不必给差分开口子；代价恒定（约 60 字/日）。时钟不可用时退回常量（整段只发一次）。
        # 位置放在键序末尾：`_pre_step` 按键序 append，这条因此落在玩家那句话之后、模型开口之前。
        out["action"] = f"{time_text}。{ACTION_HINT}" if time_text else ACTION_HINT
        return out

    @classmethod
    def format_where_segment(cls, self_raw: Any, player: Any, ids: Any, by_id: Any) -> str:
        """「所在」段成文：**此刻的空间关系** —— 你在哪、玩家在哪、是否同处一地、还有谁在场（各在哪）。

        为什么三段合一段、为什么位置从自身/玩家/同席里整体摘出来：见 `_CTX_SEGMENTS` 上方的
        ★ 说明。一句话：位置是最易变的一项，绑在大段上 = 挪一步整段重发。

        ★ **恒非空**（继承原「在场」段的职责）★：
          模型看不到屉，只读转录；而**转录是事件流，撤回一个状态的唯一办法是声明新状态**。
          若本段只在"有位置或有别人"时才有值，则"群聊→单聊"这个方向段会变空 → 按现有实现
          不发消息（`render_context_segment` 对空文本返回 ""）→ 转录里那条"李四在场"永远留着。
          所以最退化的情形（旧 DLL 没坐标、也没有同席）也必须成文：
          「此刻并无他人在场，只有玩家与你相对」—— 这句恒真，是兜底的地板。
        """
        self_raw = self_raw if isinstance(self_raw, dict) else {}
        player = player if isinstance(player, dict) else {}
        parts: List[str] = []

        # ① 你在哪（有地名用「栖居于X」；只有坐标就用「你眼下在坐标(x,y)」——
        #    措辞与下一句玩家那条对齐。C# 全仓从没发过 `pos` 键，实际走的多是坐标那条；
        #    ★ 别写成"你坐标(x,y)"：`_pt_label` 返回的是名词短语，直接跟在"你"后面不成句。）
        pos = cls._getf(self_raw, "pos")
        if pos:
            parts.append(f"你栖居于{pos}")
        else:
            mine = cls._pt_label(self_raw.get("point"))
            if mine:
                parts.append(f"你眼下在{mine}")

        # ② 玩家在哪、是否同处一地（措辞沿用原「玩家」段，主语换成"玩家"）。
        #    同格=面对面交谈 / 不同格=神识传音 —— 这里是**传音判定的唯一出口**：
        #    `prompts/sections/tool_usage.txt` 那条"异地相隔时动手类不得直接调"锚在它上面。
        ptl = cls._pt_label(player.get("point"))
        p_same = player.get("same_grid")
        if ptl or p_same is not None:
            said = "玩家" + (f"眼下在{ptl}" if ptl else "")
            if p_same is True:
                said += "，与你同处一地，面对面交谈"
            elif p_same is False:
                said += "，与你异地相隔，正以神识传音遥相交谈"
            parts.append(said)

        # ③ 在场者与各自的位置（**同席者的位置也归本段** —— 用户 09-24 拍板：位置只在一处出现）。
        #    玩家不写进 names（他恒在场、上一句刚说过）——但人数里要算上。
        names: List[str] = []
        for x in (ids or []):
            s = str(x or "").strip()
            if s and s not in names:
                names.append(s)

        # ★ 同格人物（11-11 用户需求：「NPC 都不知道同一格地区有谁」）★
        #    C# 的 `raw.same_grid_units` = 这个 NPC **自己那一格**的人（不是玩家那格），
        #    已按「好感最高 6 + 好感最低 6」截断，玩家不在其中（他在上一句单独写了）。
        #    并进本段而不是另起一段：本段就是「还有谁在场」的唯一出口（位置/在场都归它）。
        sg_list = self_raw.get("same_grid_units")
        sg_parts: List[str] = []
        sg_seen: List[str] = []
        if isinstance(sg_list, list):
            for it in sg_list:
                if not isinstance(it, dict):
                    continue
                nm = str(it.get("name") or "").strip()
                if not nm or nm in sg_seen:
                    continue
                sg_seen.append(nm)
                rel = str(it.get("relation") or "").strip() or "陌生"
                iv = cls._as_int(it.get("intim"))
                sg_parts.append(f"{nm}（{rel}，好感{iv}）" if iv is not None else f"{nm}（{rel}）")
        sg_rest = cls._as_int(self_raw.get("same_grid_rest"))

        if not names and not sg_parts:
            parts.append("此刻并无他人在场，只有玩家与你相对")
        else:
            items = ["玩家"]
            for n in names:
                o = by_id.get(n) if isinstance(by_id, dict) else None
                loc = cls._other_where(o if isinstance(o, dict) else {})
                items.append(n + (f"（{loc}）" if loc else ""))
            parts.append(f"此刻在场者：{'、'.join(items)}，连同你共 {len(names) + 2} 人")
            # 同格但不在群聊名单里的人：单独一句，带关系与好感。
            # 模型据此就能判断"这人我认不认得、亲不亲"，不必再调工具查。
            if sg_parts:
                tail = f"，另有{sg_rest}人未列" if (sg_rest or 0) > 0 else ""
                parts.append(f"与你同一格的还有：{'、'.join(sg_parts)}{tail}")
        return "；".join(parts) + "。"

    @classmethod
    def _other_where(cls, o: Any) -> str:
        """同席者的位置短语（从原「同席·X」段搬来，措辞保持一致）：
        「与你同处一地（坐标…）」/「在坐标…，与你异地相隔」/「在坐标…」；取不到坐标就只说关系。"""
        if not isinstance(o, dict):
            return ""
        ptl = cls._pt_label(o.get("point"))
        same = o.get("same_grid")
        if same is True:
            # 同格 = 与「我」同一格，坐标已在本段开头（自身/玩家那两句）说过 ⇒ 不再重复 ——
            # 外层本来就带一对括号，再嵌「（坐标(31,78)）」会读成双层括号。
            return "与你同处一地"
        if same is False:
            return f"在{ptl}，与你异地相隔" if ptl else "与你异地相隔"
        return f"在{ptl}" if ptl else ""

    @classmethod
    def format_other_segment(cls, o: Any) -> str:
        """同席者一段：**第三人称、只写"抬眼能看到的"**。

        给：姓名 / 道号 / 性别 / 种族 / 境界 / 宗门 / 年龄 / 容貌档位 / 声名档位 /
            **与我的**关系与好感 / 此刻神情 / 性格。
        不给：气运、生平经历（`logs`/`recent`）、悟性、战力裸值、体魄、爱好 —— 那些不是
        "抬眼就能看到的"，而是"查过资料库"。模型要细节应当自己调 `inspect_unit`；
        把它们塞进 L1 还有两个副作用：段变长（重发更贵）、以及让模型拿面板数值当台词。
        ★ 位置与同格也**不在这里**（09-24 用户拍板）：他与玩家各自在哪统一由「所在」段说
          —— 位置是最易变的一项，放在这里会让"他挪一步"就重发整份可观测面。

        ★ 好感走 `_intim_tier` 的**档位词**，与自身段 / 玩家段同一口径。
        这里原先是裸数字（`好感87`）—— 与本函数 docstring 自己引用的那条教训自相矛盾
        （`_intim_tier` 的注释写着"避免把系统数值当台词：'好感已达200' 太像面板"）。
        """
        if not isinstance(o, dict):
            return ""
        name = str(o.get("name") or o.get("id") or "").strip()
        if not name:
            return ""
        sex = o.get("sex") if o.get("sex") in ("男", "女") else ""
        ta = "她" if sex == "女" else "他"
        parts: List[str] = []
        bits: List[str] = []
        if sex:
            bits.append(f"一位{sex}子")
        race = str(o.get("race") or "").strip()
        if race and race != "人族":
            bits.append(f"身为{race}")
        realm = str(o.get("realm") or "").strip()
        if realm:
            bits.append(f"{realm}修为")
        sect = str(o.get("sect") or "").strip()
        if sect:
            bits.append(f"隶属{sect}一脉")
        elif realm:
            # 有境界、无宗门 = 散修（与「玩家」段同一口径）。**只有真拿到境界才敢这么说**：
            # 什么都没拿到时写"一介散修"是**编造**（我们只是不知道，不是知道他没宗门）。
            bits.append("一介散修")
        age_v = cls._as_int(o.get("age"))
        if age_v is not None:
            bits.append(f"年约{age_v}岁")

        # —— 以下各项**先成文、再判空** ——
        # ★ 判据必须是"渲染结果"，不能是"raw 里键在不在"：`title` 是 `[待真机]` 占位串、
        #   `personality` 是空 dict 时，键都存在但**一个字都写不出来**；按"键非 None"放行
        #   会产出「李四。」这种空洞段 —— 那正是本函数要防的"把'不知道'写成'知道'"
        #   （而且空段在明细到达时还要多付一次重发）。这个洞是 T15/T16 用例逼出来的。
        tp = cls._title_phrase(o.get("title"))
        per_phrase = cls._personality_phrase(o.get("personality"))
        blabel = str(o.get("beauty_label") or "").strip()
        rlabel = str(o.get("reputation_label") or "").strip()
        face = "，".join(t for t in (f"容貌{blabel}" if blabel else "",
                                     f"在世人眼中{rlabel}" if rlabel else "") if t)
        # 位置（同格 / 异地）不在这里：`point` / `same_grid` 由「所在」段统一成文
        # （`format_where_segment` 的同席部分），本段只留"他这个人"的可观测面。
        # 与「我」的关系与好感（**只给观察者→对方这一个方向**；对方怎么看我，我不知道）
        rel = str(o.get("relation") or "").strip()
        intim = o.get("intim")
        intim = intim if isinstance(intim, int) and not isinstance(intim, bool) else None
        rel_line = ""
        if rel:
            rel_line = cls._relation_phrase(rel, ta)
            if rel_line and intim is not None:
                rel_line += cls._intim_tier(intim)      # 档位词，不写裸数值
        elif intim is not None:
            # 防御分支：有值无关系名（真机 C# 对无关系回「陌生」，走上面那条）。
            # `_intim_tier` 自带前导逗号，故这里不加"好感N"。
            tier = cls._intim_tier(intim)
            if tier:
                rel_line = f"你与{ta}" + tier
        mood_line = ("此刻" + cls._fmt_mood(o.get("mood"))) if o.get("mood") is not None else ""

        # ★ 一条实质信息都没有 ⇒ **整段留空、不编造**。
        # 出现这个形态的场合：roster 点名了某人但 C# 还没给明细（`raw.others` 尚未实现），
        # 或 C# 只回了解析到的名字、字段一个没取到。
        # 此时「在场」段已经声明了他在场，这里留空即可；等明细到了，内容从 "" 变实，
        # 差分自动把这段发出去。反过来说，若这里硬凑一句，模型会把"不知道"读成"知道"。
        if not bits and not any((tp, face, rel_line, mood_line, per_phrase)):
            return ""

        parts.append(f"{name}，{'，'.join(bits)}" if bits else name)
        for _piece in (tp, face, rel_line, mood_line, per_phrase):
            if _piece:
                parts.append(_piece)
        return "。".join(p for p in parts if p) + "。"

    def render_context_segment(self, name: str, text: str) -> str:
        """单段的「可识别前缀 + 内容」，供 DialogueAgent 逐段差分发送/恢复 retained。

        空文本返回 ""（= 不发）是**有意保留**的行为：段清单是动态的，离场段的清理由
        agent 侧的「previous − current」负责，不靠这里发空消息去撤回。
        撤回问题另有一条更稳的解法：`present` 段恒非空 ⇒ 进/出场合两个方向都是内容变化
        （见 `format_l1_context` 的 ★）。
        """
        label = self.ctx_label(name)
        return f"Current runtime context —— {label}：{text}" if text and text.strip() else ""

    def _layer_for(self, scope: Optional[str], create: bool = False) -> PromptLayer:
        if scope is None:
            return self.global_layer
        if scope not in self.scoped:
            if not create:
                return self.global_layer
            self.scoped[scope] = PromptLayer(scope=scope)
        return self.scoped[scope]

    def dispose_scope(self, scope: str):
        self.scoped.pop(scope, None)
        self._persona_file_cache.pop(scope, None)
