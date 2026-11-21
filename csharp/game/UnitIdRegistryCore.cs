/// <summary>
/// UnitIdRegistry 的**纯逻辑核心** —— 「名字 ↔ unitID」两张表 + 跨存档校验。
///
/// 为什么把它从 `UnitIdRegistry.cs` 里切出来：那一半要碰文件、碰 `g.world`、碰 Unity，
/// 在开发机上跑不了；而**这一半才是会出错的一半**（跨档污染、覆盖写、残留身份），
/// 且它的每个失败模式都是**静默**的：
///   · 跨存档不校验 → 旧档的 unitID 被当成本档的权威。unitID 换档即失效，最坏情形是
///     那个 id 在新档里**恰好是另一个人** ⇒ 静默错人（本工程最怕的形态）；
///   · `Remember` 覆盖写 → 同一个名字在两个人之间来回翻，谁也没法察觉；
///   · `Forget` 不校验旧值 → 自愈时把**刚登记的正确值**误删。
/// 切出来之后 `csharp/tests/UnitIdRegistryTests` 链接本文件直接跑断言
/// （照 `ChatFrameQueue` / `ContactStore` 的先例：零 Unity、零 Newtonsoft）。
///
/// ★ 本文件**刻意零依赖**：只用 `System` + `System.Collections.Generic`。
///   JSON 的读写留在外壳（`UnitIdRegistry.cs`）—— 它只是把 `Snapshot()` 序列化出去、
///   把文件读进来交给 `Load`，一行判断都不做。
/// </summary>
using System;
using System.Collections.Generic;

namespace AgentLoopBridge
{
    internal sealed class UnitIdRegistryCore
    {
        /// <summary>落盘格式版本。将来换形状时靠它决定要不要迁移（现在是首版）。</summary>
        internal const int Version = 1;

        private readonly Dictionary<string, string> _byName = new Dictionary<string, string>();
        private readonly Dictionary<string, string> _byId = new Dictionary<string, string>();

        /// <summary>**已接受**的那个存档 id。空 = 表是空的（未加载 / 被拒 / 刚重置）。</summary>
        internal string World { get; private set; }

        internal int Count { get { return _byName.Count; } }

        public UnitIdRegistryCore() { World = ""; }

        // ---------- 加载 / 重置 ----------

        /// <summary>加载一份落盘内容。返回**是否接受**（false = 整表作废，当作空表）。
        ///
        /// ★ 跨档防线就在这三行里：`fileWorld` 与 `nowWorld` 任一为空、或两者不等，
        ///   **整表不认**。为什么宁可整表作废也不逐条挑：
        ///   unitID **跨存档会整体失效**，"哪几条还成立"没有任何判据 —— 逐条挑就是猜，
        ///   而猜错的方向恰好是"把别人端给模型"，且看不出来。
        ///
        /// 不抛异常：损坏 / 半截的输入一律当空表（登记表是**加速器**，不是正确性依赖）。
        /// </summary>
        internal bool Load(string fileWorld, IDictionary<string, string> units, string nowWorld)
        {
            Reset();
            string fw = (fileWorld ?? "").Trim();
            string nw = (nowWorld ?? "").Trim();
            if (fw.Length == 0 || nw.Length == 0 || fw != nw) return false;
            World = nw;
            if (units == null) return true;
            foreach (var kv in units)
            {
                string nm = (kv.Key ?? "").Trim();
                string id = (kv.Value ?? "").Trim();
                if (nm.Length == 0 || id.Length == 0) continue;   // 空白项静默丢弃（不写占位）
                _byName[nm] = id;
                _byId[id] = nm;                                   // 后面来的同名/同 id 覆盖前面：
                                                                  // 落盘文件是**我们写的**，
                                                                  // 有重复即说明文件被手改过，
                                                                  // 取最后一条与"全量重写"语义一致。
            }
            return true;
        }

        /// <summary>整表清空（换存档 / 加载失败）。</summary>
        internal void Reset()
        {
            _byName.Clear();
            _byId.Clear();
            World = "";
        }

        // ---------- 读 ----------

        /// <summary>这个名字登记过的 unitID；**没登记返回空串**（不是 null、不是占位）。</summary>
        internal string IdOf(string name)
        {
            string id;
            return _byName.TryGetValue((name ?? "").Trim(), out id) ? (id ?? "") : "";
        }

        /// <summary>这个 unitID 登记过的名字；**没登记返回空串**。</summary>
        internal string NameOf(string unitId)
        {
            string nm;
            return _byId.TryGetValue((unitId ?? "").Trim(), out nm) ? (nm ?? "") : "";
        }

        // ---------- 写 ----------

        /// <summary>登记一条。返回**是否真的改了**（调用方据此决定要不要落盘）。
        ///
        /// ★ 幂等：值没变 → 返回 false，一个字节都不写。
        /// ★ 空名 / 空 id → 直接忽略（"不知道"不许变成"空"）。
        /// ★ 覆盖已有一条**不同**的登记时**如实返回 true 并保留新值** —— 外壳据此打一行
        ///   醒目日志。这里刻意**不**改成"第一个赢"：同一个名字在一个存档里出现两个不同
        ///   unitID 时，后到的那个通常是**更晚、更可靠**的一次握手（玩家刚点开的就是他），
        ///   而"第一个赢"会让唯一的一次正确握手被一次陈旧的登记压死。真正防跨档的是
        ///   `Load` 的 world 校验，不是这里。
        /// </summary>
        internal bool Remember(string name, string unitId)
        {
            string nm = (name ?? "").Trim();
            string id = (unitId ?? "").Trim();
            if (nm.Length == 0 || id.Length == 0) return false;

            string old;
            bool hadOld = _byName.TryGetValue(nm, out old);
            if (hadOld && old == id) return false;          // 幂等

            _byName[nm] = id;
            // 旧值若还指向这个名字，先摘掉反查（同一个名字换 id ⇒ 那个旧 id 已经不是他了）
            if (hadOld && !string.IsNullOrEmpty(old))
            {
                string owner;
                if (_byId.TryGetValue(old, out owner) && owner == nm) _byId.Remove(old);
            }
            _byId[id] = nm;
            return true;
        }

        /// <summary>登记一条，**但绝不覆盖**已有登记（同值/异值都不动）。返回是否真的写进去了。
        ///
        /// 与 `Remember` 的分工（09-17）：
        ///   · `Remember`          = 我**手里握着那个真身**（点开对话 / 加好友）⇒ 后到的更可信，
        ///     覆盖旧值并由外壳大声留痕（`★覆盖登记`）；
        ///   · `RememberIfAbsent`  = **查询时顺带看到的**（`search_units` / `inspect_unit` 的关系簿）
        ///     ⇒ 只是"路过看见"，没有理由压掉一次真正的握手；已有任何值就原样留着。
        /// 幂等 / 空值忽略同 `Remember`。
        /// </summary>
        internal bool RememberIfAbsent(string name, string unitId)
        {
            string nm = (name ?? "").Trim();
            string id = (unitId ?? "").Trim();
            if (nm.Length == 0 || id.Length == 0) return false;
            if (_byName.ContainsKey(nm)) return false;      // ★ 已有（无论同值与否）一律不动
            _byName[nm] = id;
            _byId[id] = nm;
            return true;
        }

        /// <summary>自愈：摘掉一条登记。返回是否真的摘了。
        ///
        /// `unitId` 非空时**必须与当前值相等才摘** —— 否则并发的"另一个名字刚登记成这个 id"
        /// 会被误删。空 `unitId` = "不管当前值，摘掉这个名字"（调用方明确要求）。
        /// </summary>
        internal bool Forget(string name, string unitId)
        {
            string nm = (name ?? "").Trim();
            string cur;
            if (!_byName.TryGetValue(nm, out cur)) return false;
            string want = (unitId ?? "").Trim();
            if (want.Length > 0 && cur != want) return false;   // 已经不是那条了，别误删
            _byName.Remove(nm);
            if (!string.IsNullOrEmpty(cur))
            {
                string owner;
                if (_byId.TryGetValue(cur, out owner) && owner == nm) _byId.Remove(cur);
            }
            return true;
        }

        // ---------- 导出（给外壳序列化） ----------

        /// <summary>当前两张表的**名字方向**快照（外壳把它写成 `units` 对象）。</summary>
        internal Dictionary<string, string> Snapshot()
        {
            return new Dictionary<string, string>(_byName);
        }

        /// <summary>两个方向是否自洽（诊断/测试用）：每个 `name→id` 都有一条对应的
        /// `id→name`，反之亦然。不自洽 = 有代码绕过了 `Remember`/`Forget` 直接动字典。</summary>
        internal bool IsConsistent()
        {
            if (_byName.Count != _byId.Count) return false;
            foreach (var kv in _byName)
            {
                string nm;
                if (!_byId.TryGetValue(kv.Value, out nm) || nm != kv.Key) return false;
            }
            return true;
        }
    }
}
