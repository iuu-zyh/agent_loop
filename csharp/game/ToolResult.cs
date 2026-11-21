/// <summary>
/// 工具结果信封 —— **全仓唯一一份** `{success,data}` / `{success:false,error[,data]}`。
///
/// 【为什么要收拢】
/// 11-19 复核：同一个信封在 8 个文件里各写了一遍（`ToolExecutor` / `TeamTool` / `NpcDuel` /
/// `DramaGate` / `ShowDramaService`…），形状已经漂了三种：
///   · `Ok` 有的给 `["data"]=JToken.FromObject(data)`、有的 null→空对象；
///   · 失败帧有的带 `["current_intim"]=null`、有的不带；
///   · `ShowDramaService` 的两份还多一个恒 null 的 `["error"]` key。
/// 形状不一致不会报错，只会让下游「判某个键在不在」的代码在两条路上给出不同答案。
///
/// 【09-27 那条教训必须跟着搬家 —— 它是本仓库最贵的一条之一】
/// 失败帧**不给 `data` 就是不给这个键**，绝不能写 `["data"] = null`：
///   Newtonsoft 会把它存成 `JValue(Null)`，于是 `res["data"] != null` 判不出来
///   （`JToken` 的 `!=` 是值比较，不是 C# null 比较），任何 `res["data"]["x"]` 都会抛
///   `Cannot access child value on JValue`。
///   真机 09-27 还坐骑那次就是这条链：`Fail("缺少 items…")` → `WsClient` 的 queued 判定崩
///   → **整帧丢失** → Python 120s「请求超时」→ NPC 把没办成的事讲成"已交割"。
///   缺键 = null 引用 ⇒ `res["data"] != null` / `as JObject` 等短路判断全部照常生效。
///
/// 【`current_intim` 为什么删了】
/// 旧的 `Fail(error, data, current_intim)` 三参版（当时在 `ToolExecutor` 里）的第三个参数
/// **从未被任何调用点传过**
/// （11-19 全仓点数：176 处调用，3 参 0 处），却无条件写出 `["current_intim"] = null`。
/// Python 读的是 `data.current_intim`（`tools/text_render.py`），顶层这个键没人读 —— 删掉。
/// </summary>
using Newtonsoft.Json.Linq;

namespace AgentLoopBridge
{
    internal static class ToolResult
    {
        /// <summary>成功帧：`{success:true, data}`。`data` 为空时给空对象
        /// （沿用 `TeamTool.Ok` 的旧口径：`data` 恒为对象，下游 `as JObject` 不会拿到 null）。</summary>
        public static JObject Ok(object data = null)
        {
            return new JObject
            {
                ["success"] = true,
                ["data"] = data == null ? (JToken)new JObject() : JToken.FromObject(data),
            };
        }

        /// <summary>失败帧：`{success:false, error[, data]}`。**`data` 为空就不写这个键**（见文件头 09-27 那条）。</summary>
        public static JObject Fail(string error, object data = null)
        {
            var res = new JObject
            {
                ["success"] = false,
                ["error"] = error,
            };
            if (data != null) res["data"] = JToken.FromObject(data);
            return res;
        }

        /// <summary>带附加键的失败帧：把 <paramref name="extra"/> 的键**平铺**到信封上
        /// （`TeamTool.FailWith` 的旧形状：`{success:false, error, ...extra}` —— 不是塞进 `data`）。</summary>
        public static JObject FailWith(string error, JObject extra)
        {
            var res = Fail(error);
            if (extra != null)
            {
                foreach (var p in extra.Properties())
                    res[p.Name] = p.Value;
            }
            return res;
        }
    }
}
