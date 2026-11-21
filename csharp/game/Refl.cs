/// <summary>
/// 反射读游戏对象 —— **全仓唯一一份**。
///
/// 【为什么需要反射】游戏是 IL2CPP 编译的，托管侧拿到的东西经常是 `object`：
/// 战斗场景的 `allUnitIncludeDie`、决斗触发器列表、日志暂存字典……这些容器给回来的是
/// 无类型引用，只能按**名字**取成员。11-19 复核时这类实现有 7 份
/// （`RefMember`/`GetMember`/`GetMemberValue`/`Ref` 三名七处），行为已经分叉。
///
/// 【为什么只剩"读属性"一档】11-19 用反编 dump（`F:\DecompDump\dump\`，93 个类型文件）核对过：
/// 我们从这些名字里读的 28 个成员（`data`/`name`/`unitID`/`unitData`/`hp`/`allAddLogData`…），
/// 凡在 dump 里出现的**一律是属性**（`PROP`），零个实例字段 —— Il2Cpp 的代理类型把原生字段
/// 暴露成属性，真正的 `FIELD` 只有 `NativeFieldInfoPtr_xxx` 那种静态缓存指针，按名字读不到。
/// 所以旧的"属性 → 字段"第二档对这批名字**永远打不中**，只是每次多一次查找。
///
/// 【真有字段的情况】有，但**这个门面帮不上忙**：例如 `UnitCtrlBase.mIsDie` 是 protected 原生字段，
/// 而 `GetField` 不搜索基类 ⇒ 必须显式沿 `BaseType` 上走（`TeamContainerInject.DiagOnUnitDie`
/// 就是这么写的）。那种读法留 <see cref="GetField"/>，只有确实知道"这是个字段"的地方才用。
///
/// 【去掉了什么（11-19）】
///   · `dynamic` 索引器兜底：代理类型没有索引器，走到那儿必抛、必被吞 —— 它读得到的东西
///     属性档本来就读得到，纯属给那**一处**埋了个语义分叉。
///   · 调用点自带的 `try { ... } catch { }`：本类的方法**绝不抛**（失败返回 null），
///     外面再包一层只会让"是谁读失败"更模糊。
///
/// 【缓存】按 `类型名|成员名` 缓存查找结果（含"没找到"）。71 个调用点实际只会有几十次查找，
/// 之后都是字典命中 —— 这些点在战斗采样里会被反复调用，不值得每次都走 `GetType` + 名字匹配。
/// 线程：主线程（游戏对象只能在主线程碰），不加锁。
///
/// 【失败要看得见】读不到时打一行 `[Refl] 读不到：类型.成员`，**同 (类型,成员) 只打一次**，
/// 全进程 40 条封顶。这不是噪音，而是"我们到底在向哪些类型要哪些不存在的成员"的地图 ——
/// 过度防御正是这样长出来的。
/// </summary>
using System;
using System.Collections.Generic;
using System.Reflection;

namespace AgentLoopBridge
{
    internal static class Refl
    {
        private const BindingFlags Flags =
            BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.DeclaredOnly;

        /// <summary>日志封顶（与仓里其它"去重 + 封顶"的诊断日志同一规约）。</summary>
        private const int LogCap = 40;

        private static readonly Dictionary<string, MemberInfo> _cache = new Dictionary<string, MemberInfo>();
        private static readonly HashSet<string> _logged = new HashSet<string>();
        private static int _loggedCount;

        // ---------------------------------------------------------------- 读

        /// <summary>按名字读**属性**（沿基类上找）。读不到返回 null —— 绝不抛。</summary>
        public static object Get(object o, string name)
        {
            if (o == null || string.IsNullOrEmpty(name)) return null;
            try
            {
                var p = Resolve(o.GetType(), name, false) as PropertyInfo;
                if (p == null) { NoteMiss(o, name); return null; }
                return p.GetValue(o, null);
            }
            catch (Exception e) { NoteThrow(o, name, e); return null; }
        }

        /// <summary>
        /// 按名字读**字段**（沿基类上找）。**只在明确知道那是个字段时用**（例：`mIsDie`）——
        /// 普通数据成员一律是属性，用 <see cref="Get"/>。
        /// </summary>
        public static object GetField(object o, string name)
        {
            if (o == null || string.IsNullOrEmpty(name)) return null;
            try
            {
                var f = Resolve(o.GetType(), name, true) as FieldInfo;
                if (f == null) { NoteMiss(o, name); return null; }
                return f.GetValue(o);
            }
            catch (Exception e) { NoteThrow(o, name, e); return null; }
        }

        /// <summary>读字符串属性（取不到 / 不是字符串 ⇒ <paramref name="fallback"/>）。</summary>
        public static string Str(object o, string name, string fallback = "")
        {
            object v = Get(o, name);
            return v == null ? (fallback ?? "") : (v as string ?? Convert.ToString(v));
        }

        // ---------------------------------------------------------------- 值转换

        /// <summary>安全转 bool（取不到 ⇒ <paramref name="fallback"/>）。</summary>
        public static bool Bool(object v, bool fallback = false)
        {
            if (v == null) return fallback;
            try { return v is bool b ? b : Convert.ToBoolean(v); } catch { return fallback; }
        }

        /// <summary>安全转 int（取不到 ⇒ <paramref name="fallback"/>）。</summary>
        public static int Int(object v, int fallback = -1)
        {
            if (v == null) return fallback;
            try { return v is int i ? i : Convert.ToInt32(v); } catch { return fallback; }
        }

        // ---------------------------------------------------------------- 内部

        /// <summary>沿 `BaseType` 逐层找（`DeclaredOnly`）：避免"派生类用 new 藏了基类同名属性"
        /// 引起的 `AmbiguousMatchException`，也才能看见基类私有的字段。</summary>
        private static MemberInfo Resolve(Type startType, string name, bool field)
        {
            string key = (field ? "f|" : "p|") + startType.FullName + "|" + name;
            if (_cache.TryGetValue(key, out var hit)) return hit;

            MemberInfo found = null;
            for (var t = startType; t != null && t != typeof(object); t = t.BaseType)
            {
                try
                {
                    if (field) { var f = t.GetField(name, Flags); if (f != null) { found = f; break; } }
                    else { var p = t.GetProperty(name, Flags); if (p != null) { found = p; break; } }
                }
                catch { }
            }
            _cache[key] = found;      // null 也缓存：表示"确实没有"，别每次重找
            return found;
        }

        private static void NoteMiss(object o, string name)
        {
            string at = SafeTypeName(o) + "." + name;
            if (!_logged.Add("miss|" + at)) return;
            if (++_loggedCount > LogCap)
            {
                if (_loggedCount == LogCap + 1)
                    ModMain.P("[Refl] 读不到的成员已达 " + LogCap + " 条上限，后续同类不再打印（要排查请临时提高上限）");
                return;
            }
            ModMain.P("[Refl] 读不到：" + at);
        }

        private static void NoteThrow(object o, string name, Exception e)
        {
            string at = SafeTypeName(o) + "." + name;
            if (!_logged.Add("throw|" + at)) return;
            if (++_loggedCount > LogCap) return;
            ModMain.P("[Refl] 读取异常：" + at + " → " + e.GetType().Name + ": " + e.Message);
        }

        private static string SafeTypeName(object o)
        {
            try { return o.GetType().Name; } catch { return "?"; }
        }
    }
}
