/// <summary>
/// ModConfigFile —— C# 侧读取用户 `config.json` 的唯一入口（只读，绝不写回）。
///
/// 为什么 C# 要读 config.json：
///   WS 端口原本在 C# 里写死 8766，而配置面板允许用户改 `network.port`。
///   用户一改，Python 在新端口监听、C# 仍死连 8766 → **mod 永久失联**，
///   且重启游戏也没用（Start() 仍用默认值）。这是发行版里必然被踩的坑。
///   现在改为：C# 读同一个 config.json 取端口，拉起 Python 时经 `AGENT_LOOP_WS`
///   下发，两侧天然一致；文件读不到就用默认 8766（与 Python 默认同源）。
///
/// 各司其职：本类只做「文件 → 端口」这一件事，**不解释其它配置**——
/// 其余配置项仍由 Python 侧 config_loader 独占解释，避免两处真相。
/// 容错优先：任何异常一律退回默认值，绝不抛给调用方（不能因为配置写坏就连不上）。
/// </summary>
using System;
using System.IO;
using Newtonsoft.Json.Linq;

namespace AgentLoopBridge
{
    internal static class ModConfigFile
    {
        /// <summary>与 Python config_loader.DEFAULT_CONFIG["network"]["port"] 同源，勿单方面改。</summary>
        internal const int DefaultPort = 8766;

        /// <summary>
        /// config.json 路径 = **数据根**/config.json；解析不到返回 null。
        ///
        /// 必须用 <see cref="ModPaths.DataRoot"/> 而不是 <c>ModRoot</c>（打包时修正）：
        /// 官方管线布局把 config.json 放在 `&lt;Mod根&gt;\ModAssets\` 下，而 `ModRoot` 是 Mod 根。
        /// 用 ModRoot 会**读错文件或读不到** → 退回默认 8766 → 用户改了端口后 C# 仍连 8766
        /// → 正是本类当初要消灭的那个「改端口 = 永久失联」。DataRoot 两种布局都认。
        /// </summary>
        internal static string Path
        {
            get
            {
                string data = ModPaths.DataRoot;
                return string.IsNullOrEmpty(data) ? null : System.IO.Path.Combine(data, "config.json");
            }
        }

        /// <summary>读 `network.port`。文件缺失/坏 json/键缺失/越界 一律返回 DefaultPort。</summary>
        internal static int ReadWsPort()
        {
            string p = Path;
            if (string.IsNullOrEmpty(p) || !File.Exists(p)) return DefaultPort;
            try
            {
                JObject o = JObject.Parse(File.ReadAllText(p));
                JToken net = o["network"];
                JToken port = net != null ? net["port"] : null;
                if (port == null || port.Type == JTokenType.Null) return DefaultPort;
                int v = port.Value<int>();
                // 合法端口区间；越界视为写坏，退回默认（否则 TcpClient 会抛得莫名其妙）
                return (v >= 1 && v <= 65535) ? v : DefaultPort;
            }
            catch (Exception e)
            {
                ModMain.P("[ModConfigFile] 读 config.json 端口失败，用默认 " + DefaultPort + "：" + e.Message);
                return DefaultPort;
            }
        }
    }
}
