/// <summary>
/// 官方桥入口包装 —— 官方桥 GGBH_MOD 反射调用「{命名空间}.ModMain」，
/// 命名空间必须与模组编辑器分配的 MOD_Jgmg5L 一致（程序集名/导出路径同）。
/// 真正逻辑全在 AgentLoopBridge.ModMain，这里只做委托转发（与官方模组编辑器导出的
/// ModMain 生成壳同款）。缺了这一层，官方桥找不到入口，Init/Destroy 永不执行，
/// Harmony 补丁（NPC 面板「AI 对话」按钮）、AB 面板、F11 配置面板全部静默失效。
/// </summary>
namespace MOD_Jgmg5L
{
    public class ModMain
    {
        private readonly AgentLoopBridge.ModMain _impl = new AgentLoopBridge.ModMain();

        /// <summary>进世界时由官方桥调用</summary>
        public void Init()
        {
            _impl.Init();
        }

        /// <summary>回主界面时由官方桥调用</summary>
        public void Destroy()
        {
            _impl.Destroy();
        }
    }
}
