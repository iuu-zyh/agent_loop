/// <summary>
/// 对话 UI 构建产物引用包 —— 纯数据类（非 MonoBehaviour）。
///
/// 职责只有一项：把 ChatUiBuilder 搭好的整棵 UGUI 节点树握在手里，
/// 让 ChatWindow / ChatPresenter 通过字段访问，而不是到处 Find/GetComponent。
/// 没有任何方法，也不持有逻辑。
/// </summary>
using UnityEngine;
using UnityEngine.UI;

namespace AgentLoopBridge
{
    public class ChatWindowRefs
    {
        // ---- 整棵树 ----
        /// <summary>Canvas 根（含 CanvasScaler / GraphicRaycaster，sortingOrder=3000 压过普通 UI）</summary>
        public GameObject Root;

        // ---- 主面板 ----
        /// <summary>窗口面板（右中锚点 520×760，半透明深底）</summary>
        public RectTransform Window;
        /// <summary>当前 NPC 名（BG/NpcInput 顶部，Enter 自动填充，只读展示当前对话对象；不再支持手改切换）。</summary>
        public InputField NpcLabel;
        /// <summary>玩家名显示框（契约 BG/Playerput，左上角，只读展示不做切换入口）。
        /// 可选节点：缺失时按 null 容错</summary>
        public InputField PlayerLabel;
        /// <summary>关闭按钮（Button + ClickUtils，onClick 三步写法）</summary>
        public GameObject CloseButton;
        /// <summary>配置面板按钮（⚙，CloseBtn 左侧；点击 → ConfigPanelOpener）。
        /// AB 预制体未加该节点时为 null，按缺失容错</summary>
        public GameObject ConfigButton;
        /// <summary>消息滚动区</summary>
        public ScrollRect Scroll;
        /// <summary>消息区可见视口（RectMask2D 裁剪）</summary>
        public RectTransform Viewport;
        /// <summary>消息内容容器（VerticalLayoutGroup 纵向排布 + ContentSizeFitter 自适应高度）</summary>
        public RectTransform Content;
        /// <summary>底部消息输入框</summary>
        public InputField ChatInput;
        /// <summary>发送按钮（Button + ClickUtils，onClick 三步写法）</summary>
        public GameObject SendButton;
        /// <summary>忙态标记（“NPC 回应中…”），回合中显示</summary>
        public GameObject BusyLabel;
        /// <summary>底部 token 统计条文本（契约 BG/StatsBar/Text，Text 直接挂 StatsBar 根亦可；可选节点，缺失容错=不显示统计）</summary>
        public Text StatsBarText;

        // ---- 删除模式（微信式多选，；全部可选节点，缺失容错=无删除功能）----
        /// <summary>标题栏删除按钮（契约 BG/DeleteBtn，旧位置 BG/Title/DeleteBtn 回退；点击 → Presenter 决定进入流程）</summary>
        public GameObject DeleteButton;
        /// <summary>标题栏压缩按钮（契约 BG/CompactBtn，删除键左侧；点击 → Presenter 走手动压缩流程。
        /// 新增；可选节点，缺失=无按钮，与 /compact 命令同通路）</summary>
        public GameObject CompactButton;
        /// <summary>删除模式底部操作条（契约 BG/DeleteBar，进入选择模式时显示、平时隐藏）</summary>
        public GameObject DeleteBar;
        /// <summary>全选/取消全选按钮（契约 BG/DeleteBar/SelectAllBtn）</summary>
        public GameObject SelectAllBtn;
        /// <summary>删除确认按钮（契约 BG/DeleteBar/DeleteConfirmBtn；两段确认：删除 → 确认删除）</summary>
        public GameObject DeleteConfirmBtn;
        /// <summary>取消按钮（契约 BG/DeleteBar/CancelBtn，退出选择模式）</summary>
        public GameObject CancelBtn;
        /// <summary>已选计数文本（契约 BG/DeleteBar/CountLabel）</summary>
        public Text CountLabel;

        // ---- 行模板（微信式：头像 + 气泡组成一行；代码版旧气泡模板供 ChatUiBuilder 用）----
        /// <summary>玩家行模板（契约 BG/Templates/UserRow：UserBubble + Useravatar）</summary>
        public GameObject UserRowTemplate;
        /// <summary>NPC 行模板（契约 BG/Templates/NpcRow：Npcavatar + NpcBubble）</summary>
        public GameObject NpcRowTemplate;
        /// <summary>玩家头像 RawImage（UserRowTemplate/Useravatar；立绘预填于此，克隆行共享纹理）</summary>
        public RawImage UserAvatarImg;
        /// <summary>NPC 头像 RawImage（NpcRowTemplate/Npcavatar；立绘预填于此，克隆行共享纹理）</summary>
        public RawImage NpcAvatarImg;

        // ---- 旧顶部大立绘槽位（已废：微信式头像接管立绘）。仅代码版 ChatUiBuilder 仍引用，保持字段以利编译）----
        /// <summary>旧左立绘占位（已废弃，恒 null）；保留以兼容未启用 AB_UI 的代码分支</summary>
        public RawImage PortraitLeft;
        /// <summary>旧右立绘占位（已废弃，恒 null）；保留以兼容未启用 AB_UI 的代码分支</summary>
        public RawImage PortraitRight;

        // ---- 行模板（构建后 SetActive(false)，运行时克隆实例化）----
        /// <summary>玩家气泡（深蓝右对齐）</summary>
        public GameObject UserTemplate;
        /// <summary>NPC 气泡（浅色左对齐）</summary>
        public GameObject NpcTemplate;
        /// <summary>回合过程折叠组（头部按钮 + 内容容器）</summary>
        public GameObject StepGroupTemplate;
        /// <summary>系统提示行（居中灰字）</summary>
        public GameObject SystemTemplate;
        /// <summary>分隔条（“—— 主动传音 ——”式）</summary>
        public GameObject DividerTemplate;
    }
}