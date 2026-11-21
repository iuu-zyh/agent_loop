/// <summary>
/// 传音簿 UI 引用包 —— 对标通讯录参考图：窄白竖条 380×620
/// </summary>
using UnityEngine;
using UnityEngine.UI;

namespace AgentLoopBridge
{
    public class ContactPanelRefs
    {
        public GameObject Root;
        public RectTransform Window;
        public GameObject Dim; // 全屏遮罩，点穿透在Game里盖住世界
        public GameObject CloseButton;
        public Text StatusLabel;

        // Tab 三胶囊 好友/最近/群聊
        public GameObject TabFriendButton;
        public GameObject TabRecentButton;
        /// <summary>「群聊」页（P3 新增）。**可选节点**：旧 `.ab` 里没有它，
        /// 缺失时只少一个入口，不拒绝装配（见 AbContactPanel.MissingOptional）。</summary>
        public GameObject TabGroupButton;
        /// <summary>「临时」页（10-24 新增）：只放"说过话、但既不是好友也不是社交关系"的人。
        ///
        /// **运行时克隆**（`ContactUiBuilder.BuildTabPill`），**不在预制件里** —— 与行内 ✕ / 时间
        /// 标签同一条路线，好处是旧 `.ab` 也能用（不必重打 AB）。装配时若克隆失败则本页整页
        /// 不出现（`AbContactPanel` 走 `MissingOptional` 那套"少一个入口"的降级，不拒绝装配）。</summary>
        public GameObject TabTempButton;

        // 顶栏搜索图标 + 展开层
        public GameObject SearchIconButton;
        public GameObject SearchOverlay; // 默认隐藏，点图标展开
        public InputField SearchInput;
        public GameObject SearchCancelButton;

        // 列表
        public ScrollRect ContactScroll;
        public RectTransform ContactContent;
        public GameObject ContactItemTemplate;

        // 行模板里的复选框（群聊 Tab 的选人模式用）
        // ★ 它与 ChatBtn **同格且在其上层**（预制件里 CheckBox 是行内最后一个子节点）
        //   ⇒ 两者必须**互斥显隐**，否则复选框会静默吃掉 ✓ 的点击。
        public GameObject RowCheckBox;   // BG/ContactItem/CheckBox
        public GameObject RowCheck;      // BG/ContactItem/CheckBox/Check（选中态的对勾）

        // 群聊底栏（P3 新增，均为可选节点；出厂 NewGroupBtn/DelGroupBtn/SelectBar 都隐藏）
        public GameObject NewGroupButton;      // BG/BottomBar/NewGroupBtn  仅「群聊」页显示（左半）
        public GameObject DelGroupButton;      // BG/BottomBar/DelGroupBtn  仅「群聊」页显示（右半）
        public GameObject SelectBar;           // BG/BottomBar/SelectBar    仅选择模式显示（整条）
        public GameObject SelectAllButton;     // …/SelectBar/SelectAllBtn
        public Text SelectCountLabel;          // …/SelectBar/CountLabel「已选 N 人」
        public GameObject SelectCancelButton;  // …/SelectBar/CancelBtn
        public GameObject SelectConfirmButton; // …/SelectBar/ConfirmBtn

        // 未读传音横幅（Canvas 顶部，默认隐藏；点击打开最新未读对话）
        public GameObject UnreadBanner;
        public Text UnreadBannerText;
    }
}
