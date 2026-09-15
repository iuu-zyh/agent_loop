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

        // Tab 两胶囊 好友/最近
        public GameObject TabFriendButton;
        public GameObject TabRecentButton;

        // 顶栏搜索图标 + 展开层
        public GameObject SearchIconButton;
        public GameObject SearchOverlay; // 默认隐藏，点图标展开
        public InputField SearchInput;
        public GameObject SearchCancelButton;

        // 列表
        public ScrollRect ContactScroll;
        public RectTransform ContactContent;
        public GameObject ContactItemTemplate;

        // 未读传音横幅（Canvas 顶部，默认隐藏；点击打开最新未读对话）
        public GameObject UnreadBanner;
        public Text UnreadBannerText;
    }
}
