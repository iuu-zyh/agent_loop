"""SystemPrompt — 单例 + 每 NPC 一屉的分层桩（ScopdLayers 对标 DSH）"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import textio as _textio


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
        "tool_usage.txt": ("tool:usage", 10),
    }

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
        if prompts_root is None:
            # 提示词根解析统一走 paths（发行版：<Mod根>/prompts，用户可编辑、升级不覆盖；
            # 开发机：包目录/prompts，与旧行为同路径）
            from . import paths as _paths
            prompts_root = _paths.prompts_root()
        self._prompts_root = Path(prompts_root)
        self._sections_root = self._prompts_root / "sections"
        self._personas_root = self._prompts_root / "personas"
        self._traits_root = self._prompts_root / "traits"
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
            from .tools.schemas import ALL_TOOL_SCHEMAS

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
        return "[输出底线] 你必须全心全意进行角色扮演，时刻牢记修仙者身份，禁止透露你是 AI、模型或程序，禁止出现 AI 腔、现代科技词与思考过程泄露，回复保持游戏内口吻。"

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
            from .tools.schemas import TOOL_ORDER as _TOOL_ORDER

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
    _CTX_SEGMENTS = ("time", "self", "player", "recent")
    _CTX_LABELS = {"time": "当前时间", "self": "自身", "player": "玩家", "recent": "近况"}
    # sys 观察流的 section 中文名（UI「系统组装」块行标）；未登记的段回退原名
    _SYS_LABELS = {
        "harness:identity": "身份",
        "world:basis": "世界基准",
        "world:persona_rules": "人设规则",
        "tool:usage": "工具指南",
        "deployment:persona": "人设",
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
        保留档位语义供语气参考；具体数值不写进对白，工具/动作决策仍由 C# 真值负责。"""
        try:
            n = float(intim)
        except (TypeError, ValueError):
            return ""
        if n >= 180:
            return "，情深意笃、生死可托"
        if n >= 120:
            return "，情谊深厚，彼此信赖"
        if n >= 60:
            return "，日渐亲近"
        if n >= 20:
            return "，交情尚可"
        if n >= 0:
            return "，相识未久"
        return "，可惜心存芥蒂"

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

    @staticmethod
    def _name_desc(name: Any, desc: Any) -> str:
        """「名字（注解）」成文：注解折叠换行并截 60；无注解/空名只给名字（不吐空括号）。
        气运 desc 与性格注解共用本口径（性格注解=面板「内在性格：邪恶，唯已所欲…」冒号后整句）。"""
        n = str(name).strip() if name is not None else ""
        if not n:
            return ""
        d = str(desc).replace("\r", "").replace("\n", " ").strip() if desc else ""
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

    def format_l1_context(self, npc_id: str, raw: Any) -> Dict[str, str]:
        """把 bridge.get_context 的结构化 raw 拼成四段连贯中文（当前时间/自身/玩家/近况）。

        桥只负责回 raw（通信），文案由本方法一次性成文，供 DialogueAgent 逐段差分写屉。
        字段缺失即兜底或省略，绝不抛错——真机 C# raw 结构不统一也能跑。
        """
        raw = raw if isinstance(raw, dict) else {}
        self_raw = raw.get("self") if isinstance(raw.get("self"), dict) else raw

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
        # 位置（补漏）：原写 `pos = self_raw["pos"]` → `栖居于{pos}`，但 **C# 全仓从没发过
        # `pos` 这个键**（`grep '["pos"]' csharp/*.cs` = 0 处）：C# 发的是 `point{x,y}`（GameContext.GetL1），
        # 成文侧等的是一个地名。两边各按自己的假设写 → 自身段的位置**恒空**、谁都不报错。
        # 这是「C# raw 有字段 ≠ 模型看得到」的第三次（前两次：性格注解、性别，见附录 D.1）。
        # 现在：有地名用「栖居于X」，只有坐标就用「坐标(x,y)」兜底（用户拍板：显示坐标即可）。
        pos = self._getf(self_raw, "pos")
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
        # C# 查表失败时会回一个 `[待真机]道号名(id=…)` 的占位串 —— 那是给开发者看的诊断，
        # 绝不能进上下文（模型会照着念出来），故在此挡掉。
        title = self_raw.get("title") if isinstance(self_raw.get("title"), str) else ""
        title = title.strip()
        if title and not title.startswith("[待真机]"):
            self_parts.append(f"道号「{title}」")
        loc = f"栖居于{pos}" if pos else self._pt_label(self_raw.get("point"))
        sect_ = f"隶属{sect}一脉" if sect else ""
        realm_ = f"如今已是{realm}修为" if realm else ""
        # 种族：默认人族 —— 是「人族」就不写（`world_persona_rules.txt` 已写明
        # 「种族默认为人族」，每轮多这 3 个字纯属噪声）；只有异族（魔族 / roleRace 自定义）才点名。
        race = self_raw.get("race") if isinstance(self_raw.get("race"), str) else ""
        race = race.strip()
        race_ = f"身为{race}" if race and race != "人族" else ""
        tags = [t for t in (loc, sect_, realm_, race_) if t]
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
        if mood is not None:
            self_parts.append(self._fmt_mood(mood))
        if power is not None:
            self_parts.append(f"一身战力约{power}")
        if health is not None:
            self_parts.append(f"此刻体魄{health}")
        # 悟性（C# `SetAttr(personal, dyn, pd, "talent", "talent");  // 悟性`）：与战力/体魄同属
        # 自身属性，紧随其后。这一项面板本身给的就是数字，故不转档位词。
        talent_v = self._as_int(self_raw.get("talent"))
        if talent_v is not None:
            self_parts.append(f"悟性{talent_v}")
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
        # （= 面板「内在性格：邪恶，唯已所欲…」冒号后整句，conf xdash_54sd + LS），但这里原先只读了名字，
        # 于是注解只活在 inspect/brief 那条渲染路径（tools/text_render.py 的「性格注解」），L1 一直漏。
        # 「仁善/睚眦」这种两字标签模型未必知道游戏里的确切含义，注解才是行为准则，必须带上。
        per = self_raw.get("personality") if isinstance(self_raw.get("personality"), dict) else None
        if per:
            inner = (per.get("inner") or "").strip()
            outer = per.get("outer") if isinstance(per.get("outer"), list) else []
            outer = [o.strip() for o in outer if isinstance(o, str) and o.strip()]
            # 注解与名字同下标（C# PersonalityOf 保证对齐）；缺注解/旧 C# 数据 → 退化成只给名字
            in_tag = self._name_desc(inner, per.get("inner_desc"))
            od = per.get("outer_desc") if isinstance(per.get("outer_desc"), list) else []
            out_tags = [self._name_desc(nm, od[i] if i < len(od) else None) for i, nm in enumerate(outer)]
            if in_tag or out_tags:
                if in_tag and out_tags:
                    self_parts.append(f"本性{in_tag}，行事偏{'与'.join(out_tags)}")
                elif in_tag:
                    self_parts.append(f"本性{in_tag}")
                else:
                    self_parts.append(f"行事偏{'与'.join(out_tags)}")
        # 气运（后天气运随逆天改命/奇遇动态变化，属 NPC 自我认知；只取名，desc 含 UI 元文本不入上下文）
        luck = self_raw.get("luck") if isinstance(self_raw.get("luck"), dict) else None
        if luck:
            born = self._luck_names(luck.get("born"))
            added = self._luck_names(luck.get("added"))
            if born or added:
                segs = []
                if born:
                    segs.append("命带气运" + "、".join(born))
                if added:
                    segs.append("后天又得" + "、".join(added))
                self_parts.append("；".join(segs))
        self_text = "。".join(p for p in self_parts if p) + "。"

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
        psame = player.get("same_grid")
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
        # 玩家位置：C# `relations.player.point` 一直有，此前没读 —— 于是"异地相隔"
        # 只说了相隔、没说在哪。同格时与自身段坐标相同（冗余但无害，且能自证"确实同处一地"）。
        ptl = self._pt_label(player.get("point"))
        if ptl:
            intro += f"，{ptl}"
        if prealm:
            intro += f"，{prealm}修为"
        if psect:
            intro += f"，隶属{psect}一脉"
        else:
            intro += "，一介散修游历于八荒"
        if psame is not None:
            # 传音模式判定：同一格=面对面交谈；不同格=神识传音（远程传音）
            intro += "，正与你同处一地，面对面交谈" if psame else "，眼下与你异地相隔，正以神识传音遥相交谈"
        p_parts.append(intro)
        if prel:
            hostile = prel in ("仇人", "仇敌", "敌对", "仇家")
            rel_s = f"你与{ta}已是{prel}" if hostile else f"你与{ta}结着{prel}之谊"
            if pintim is not None:
                rel_s += self._intim_tier(pintim)
            p_parts.append(rel_s)
        # 面板可感知项：魅力（外貌）与声名（坊间传闻），只作客观补充不作褒贬
        if pbeauty is not None or prepu is not None:
            bits = []
            if pbeauty is not None:
                bits.append(f"魅力{pbeauty}")
            if prepu is not None:
                # ：原写 `声名{prepu}` —— 声望档位是谓词短语（「声名远扬」），
                # 拼出来是「声名声名远扬」。玩家段与自身段统一改为「在世人眼中」。
                bits.append(f"在世人眼中{prepu}")
            p_parts.append(ta + "、".join(bits))
        if pmood is not None:
            p_parts.append(ta + "今日" + self._fmt_mood(pmood))
        player_text = "，".join(p for p in p_parts if p) + "。"

        # —— 近况段（经历 ≤10 条截取） ——
        items: List[str] = []
        recent = raw.get("recent")
        logs = raw.get("logs") if isinstance(raw.get("logs"), list) else None

        def _to_str(x: Any) -> str:
            if isinstance(x, str):
                return x
            if isinstance(x, dict):
                return x.get("text") or x.get("what") or json.dumps(x, ensure_ascii=False)
            return str(x)

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
        if items:
            recent_text = "；".join(f"{i + 1}. {t}" for i, t in enumerate(items))
        else:
            recent_text = ""

        return {"time": time_text, "self": self_text, "player": player_text, "recent": recent_text}

    def render_context_segment(self, name: str, text: str) -> str:
        """单段的「可识别前缀 + 内容」，供 DialogueAgent 逐段差分发送/恢复 retained。"""
        label = self._CTX_LABELS.get(name, name)
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
