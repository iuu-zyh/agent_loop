/// <summary>
/// About —— 本 Mod 的身份信息（作者 / 版本 / 仓库）**唯一事实来源**。
///
/// 为什么单独一个文件：
///   1) 署名要出现在多处（启动日志、配置面板、程序集元数据），必须同源，改一处即可；
///   2) 这是**授权声明的一部分**：DLL 里嵌着作者与仓库地址，二次打包者若原样发布，
///      任何一份流出的副本都会在启动日志里打出原作者信息；要抹掉就得主动改代码。
///      条款见仓库根 README 的「授权与声明」与 LICENSE。
///
/// ⚠️ 换署名只改本文件；Python 侧对应 <c>agent_loop/__init__.py</c> 的
///    <c>__author__</c> / <c>__repo__</c>，两边请保持一致。
/// </summary>
internal static class About
{
    /// <summary>Mod 名（与 Steam 创意工坊页面标题一致）。</summary>
    internal const string ModName = "八荒智能体";

    /// <summary>版本号。</summary>
    internal const string Version = "1.0.0";

    /// <summary>作者署名。⚠️ 发布前必须替换。</summary>
    internal const string Author = "iuu-zyh";

    /// <summary>项目仓库地址。⚠️ 发布前必须替换。</summary>
    internal const string Repo = "https://github.com/iuu-zyh/agent_loop";

    /// <summary>一行式署名横幅（启动日志用）。</summary>
    internal static string Banner =>
        $"《{ModName}》v{Version} by {Author} — {Repo}";

    /// <summary>非商用声明（配置面板"关于"等处展示）。</summary>
    internal const string License = "PolyForm Noncommercial 1.0.0（禁止商用 / 禁止二次打包发布）";
}
