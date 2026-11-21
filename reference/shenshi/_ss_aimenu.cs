//==================== TYPE MOD_SSCYAI.UIAIMenu ====================
using System;
using System.Collections.Generic;
using System.Runtime.CompilerServices;
using Il2CppSystem;
using Il2CppSystem.Collections.Generic;
using MOD_SSCYAI.FeatureCMD;
using UnhollowerRuntimeLib;
using UnityEngine;
using UnityEngine.Events;
using UnityEngine.UI;

namespace MOD_SSCYAI;

public class UIAIMenu : UIBase
{
	[Serializable]
	[CompilerGenerated]
	private sealed class <>c
	{
		public static readonly <>c <>9 = new <>c();

		public static Action <>9__24_0;

		public static Action <>9__24_1;

		public static Action <>9__24_2;

		public static Action <>9__24_3;

		public static Action <>9__24_5;

		public static Action <>9__24_6;

		public static Action <>9__24_7;

		public static Action <>9__24_8;

		public static Action <>9__24_9;

		public static Action <>9__24_10;

		public static Action <>9__24_11;

		public static Action <>9__24_12;

		public static Action <>9__24_13;

		public static Action <>9__24_14;

		public static Action <>9__24_15;

		public static Action <>9__24_16;

		public static Action <>9__24_17;

		public static Action <>9__24_21;

		public static Action <>9__24_22;

		public static Action <>9__25_0;

		public static Action <>9__25_1;

		public static Action <>9__25_2;

		public static Action <>9__25_3;

		public static Action <>9__25_4;

		public static Action <>9__25_5;

		internal void <InitData>b__24_0()
		{
			g.world.unitLog.SaveLogData();
		}

		internal void <InitData>b__24_1()
		{
			//IL_0010: Unknown result type (might be due to invalid IL or missing references)
			//IL_001a: Expected O, but got Unknown
			try
			{
				ClassInjector.RegisterTypeInIl2Cpp<UIUnitSet>();
				((Component)g.ui.OpenUI(new UITypeBase("UIUnitSet", (UILayer)0))).gameObject.AddComponent<UIUnitSet>().InitData(g.world.playerUnit);
			}
			catch (Exception ex)
			{
				Console.WriteLine(ex.ToString());
				Console.WriteLine("ButtonOK出错");
			}
		}

		internal void <InitData>b__24_2()
		{
			//IL_0010: Unknown result type (might be due to invalid IL or missing references)
			//IL_001a: Expected O, but got Unknown
			try
			{
				ClassInjector.RegisterTypeInIl2Cpp<UIChatAINewGroup>();
				((Component)g.ui.OpenUI(new UITypeBase("UIChatAINewGroup", (UILayer)0))).gameObject.AddComponent<UIChatAINewGroup>().InitData();
			}
			catch (Exception ex)
			{
				Console.WriteLine(ex.ToString());
				Console.WriteLine("ButtonReset出错");
			}
		}

		internal void <InitData>b__24_3()
		{
			//IL_0010: Unknown result type (might be due to invalid IL or missing references)
			//IL_001a: Expected O, but got Unknown
			try
			{
				ClassInjector.RegisterTypeInIl2Cpp<UIWorldLog>();
				((Component)g.ui.OpenUI(new UITypeBase("UIWorldLog", (UILayer)0))).gameObject.AddComponent<UIWorldLog>().InitData();
			}
			catch (Exception ex)
			{
				Console.WriteLine(ex.ToString());
				Console.WriteLine("ButtonReset出错");
			}
		}

		internal void <InitData>b__24_5()
		{
			//IL_0010: Unknown result type (might be due to invalid IL or missing references)
			//IL_001a: Expected O, but got Unknown
			try
			{
				ClassInjector.RegisterTypeInIl2Cpp<UIUnitCMD>();
				UIUnitCMD uIUnitCMD = ((Component)g.ui.OpenUI(new UITypeBase("UIUnitCMD", (UILayer)0))).gameObject.AddComponent<UIUnitCMD>();
				List<string> unitID = new List<string> { g.world.playerUnit.data.unitData.unitID };
				uIUnitCMD.InitData(unitID);
			}
			catch (Exception ex)
			{
				Console.WriteLine(ex.ToString());
				Console.WriteLine("出错");
			}
		}

		internal void <InitData>b__24_6()
		{
			//IL_0010: Unknown result type (might be due to invalid IL or missing references)
			//IL_001a: Expected O, but got Unknown
			try
			{
				ClassInjector.RegisterTypeInIl2Cpp<UISetting>();
				((Component)g.ui.OpenUI(new UITypeBase("UISetting", (UILayer)0))).gameObject.AddComponent<UISetting>().InitData();
			}
			catch (Exception ex)
			{
				Console.WriteLine(ex.ToString());
				Console.WriteLine("出错");
			}
		}

		internal void <InitData>b__24_7()
		{
			//IL_0010: Unknown result type (might be due to invalid IL or missing references)
			//IL_001a: Expected O, but got Unknown
			try
			{
				ClassInjector.RegisterTypeInIl2Cpp<UINPCAISet>();
				((Component)g.ui.OpenUI(new UITypeBase("UINPCAISet", (UILayer)0))).gameObject.AddComponent<UINPCAISet>().InitData();
			}
			catch (Exception ex)
			{
				Console.WriteLine(ex.ToString());
				Console.WriteLine("出错");
			}
		}

		internal void <InitData>b__24_8()
		{
			//IL_0010: Unknown result type (might be due to invalid IL or missing references)
			//IL_001a: Expected O, but got Unknown
			try
			{
				ClassInjector.RegisterTypeInIl2Cpp<UIRegexCMD>();
				((Component)g.ui.OpenUI(new UITypeBase("UIRegexCMD", (UILayer)0))).gameObject.AddComponent<UIRegexCMD>().InitData();
			}
			catch (Exception ex)
			{
				Console.WriteLine(ex.ToString());
				Console.WriteLine("出错");
			}
		}

		internal void <InitData>b__24_9()
		{
			//IL_0010: Unknown result type (might be due to invalid IL or missing references)
			//IL_001a: Expected O, but got Unknown
			try
			{
				ClassInjector.RegisterTypeInIl2Cpp<UISettingKnowledge>();
				((Component)g.ui.OpenUI(new UITypeBase("UISettingKnowledge", (UILayer)0))).gameObject.AddComponent<UISettingKnowledge>().InitData();
			}
			catch (Exception ex)
			{
				Console.WriteLine(ex.ToString());
				Console.WriteLine("出错");
			}
		}

		internal void <InitData>b__24_10()
		{
			//IL_0010: Unknown result type (might be due to invalid IL or missing references)
			//IL_001a: Expected O, but got Unknown
			try
			{
				ClassInjector.RegisterTypeInIl2Cpp<UIAIPromptComandNew>();
				((Component)g.ui.OpenUI(new UITypeBase("UIAIPromptComandNew", (UILayer)0))).gameObject.AddComponent<UIAIPromptComandNew>().InitData();
			}
			catch (Exception ex)
			{
				Console.WriteLine(ex.ToString());
				Console.WriteLine("出错");
			}
		}

		internal void <InitData>b__24_11()
		{
			//IL_0010: Unknown result type (might be due to invalid IL or missing references)
			//IL_001a: Expected O, but got Unknown
			try
			{
				ClassInjector.RegisterTypeInIl2Cpp<UIShareUnits>();
				((Component)g.ui.OpenUI(new UITypeBase("UIShareUnits", (UILayer)0))).gameObject.AddComponent<UIShareUnits>().Init(ModMain.shareUnit, "关注列表", 1);
			}
			catch (Exception ex)
			{
				Console.WriteLine(ex.ToString());
				Console.WriteLine("出错");
			}
		}

		internal void <InitData>b__24_12()
		{
			//IL_0010: Unknown result type (might be due to invalid IL or missing references)
			//IL_001a: Expected O, but got Unknown
			try
			{
				ClassInjector.RegisterTypeInIl2Cpp<UIShareUnits>();
				((Component)g.ui.OpenUI(new UITypeBase("UIShareUnits", (UILayer)0))).gameObject.AddComponent<UIShareUnits>().Init(ModMain.letterUnitList, "书信列表", 2);
			}
			catch (Exception ex)
			{
				Console.WriteLine(ex.ToString());
				Console.WriteLine("出错");
			}
		}

		internal void <InitData>b__24_13()
		{
			//IL_0010: Unknown result type (might be due to invalid IL or missing references)
			//IL_001a: Expected O, but got Unknown
			try
			{
				ClassInjector.RegisterTypeInIl2Cpp<UIShareUnits>();
				UIShareUnits uIShareUnits = ((Component)g.ui.OpenUI(new UITypeBase("UIShareUnits", (UILayer)0))).gameObject.AddComponent<UIShareUnits>();
				List<string> list = new List<string>();
				Enumerator<WorldUnitBase> enumerator = g.world.unit.GetUnits(true).GetEnumerator();
				while (enumerator.MoveNext())
				{
					WorldUnitBase current = enumerator.Current;
					if (current.GetLuck(ModMain.mod_ID + 1) != null)
					{
						list.Add(current.data.unitData.unitID);
					}
				}
				uIShareUnits.Init(list, "组队列表", 3);
			}
			catch (Exception ex)
			{
				Console.WriteLine(ex.ToString());
				Console.WriteLine("出错");
			}
		}

		internal void <InitData>b__24_14()
		{
			//IL_0010: Unknown result type (might be due to invalid IL or missing references)
			//IL_001a: Expected O, but got Unknown
			try
			{
				ClassInjector.RegisterTypeInIl2Cpp<UIShareUnits>();
				UIShareUnits uIShareUnits = ((Component)g.ui.OpenUI(new UITypeBase("UIShareUnits", (UILayer)0))).gameObject.AddComponent<UIShareUnits>();
				List<string> list = new List<string>();
				Enumerator<WorldUnitBase> enumerator = g.world.unit.GetUnits(true).GetEnumerator();
				while (enumerator.MoveNext())
				{
					WorldUnitBase current = enumerator.Current;
					if (current.GetLuck(ModMain.mod_ID + 9) != null)
					{
						list.Add(current.data.unitData.unitID);
					}
				}
				uIShareUnits.Init(list, "云AI行动列表", 9);
			}
			catch (Exception ex)
			{
				Console.WriteLine(ex.ToString());
				Console.WriteLine("出错");
			}
		}

		internal void <InitData>b__24_15()
		{
			//IL_005c: Unknown result type (might be due to invalid IL or missing references)
			//IL_0066: Expected O, but got Unknown
			try
			{
				List<WorldUnitBase> units = g.world.unit.GetUnits(true);
				List<string> list = new List<string>();
				Enumerator<WorldUnitBase> enumerator = units.GetEnumerator();
				while (enumerator.MoveNext())
				{
					WorldUnitBase current = enumerator.Current;
					list.Add(current.data.unitData.unitID);
				}
				if (list.Count > 0)
				{
					ClassInjector.RegisterTypeInIl2Cpp<UISelectUnitNew>();
					UISelectUnitNew uISelectUnitNew = ((Component)g.ui.OpenUI(new UITypeBase("UISelectUnitNew", (UILayer)0))).gameObject.AddComponent<UISelectUnitNew>();
					uISelectUnitNew.Init(list, "NPC预览", 0);
					uISelectUnitNew.ToggleAllUnit.GetComponent<Toggle>().isOn = true;
				}
			}
			catch (Exception ex)
			{
				Console.WriteLine(ex.ToString());
				Console.WriteLine("添加出错");
			}
		}

		internal void <InitData>b__24_16()
		{
			//IL_0010: Unknown result type (might be due to invalid IL or missing references)
			//IL_001a: Expected O, but got Unknown
			try
			{
				ClassInjector.RegisterTypeInIl2Cpp<UIUnitDieCMD>();
				((Component)g.ui.OpenUI(new UITypeBase("UIUnitDieCMD", (UILayer)0))).gameObject.AddComponent<UIUnitDieCMD>().InitData();
			}
			catch (Exception ex)
			{
				Console.WriteLine(ex.ToString());
				Console.WriteLine("添加出错");
			}
		}

		internal void <InitData>b__24_17()
		{
			//IL_0048: Unknown result type (might be due to invalid IL or missing references)
			//IL_0052: Expected O, but got Unknown
			//IL_0017: Unknown result type (might be due to invalid IL or missing references)
			//IL_0021: Expected O, but got Unknown
			try
			{
				if (ModMain.isSortEnd)
				{
					ClassInjector.RegisterTypeInIl2Cpp<UISort>();
					((Component)g.ui.OpenUI(new UITypeBase("UISort", (UILayer)0))).gameObject.AddComponent<UISort>().InitData();
					return;
				}
				UITipItem.AddTip("排行榜尚正在更新，请稍后再试！", 2f);
				MultiThreadTask multiThreadTask = new MultiThreadTask(false);
				multiThreadTask.AddCall(Action.op_Implicit((Action)ModMain.OnSort));
				Action action = delegate
				{
					multiThreadTask.Stop();
					Console.WriteLine("排名结束！" + multiThreadTask.isStop);
					UITipItem.AddTip("排行榜已更新完成！", 2f);
				};
				multiThreadTask.Run(true, Action.op_Implicit(action));
			}
			catch (Exception ex)
			{
				Console.WriteLine(ex.ToString());
				Console.WriteLine("添加出错");
			}
		}

		internal void <InitData>b__24_21()
		{
			//IL_0010: Unknown result type (might be due to invalid IL or missing references)
			//IL_001a: Expected O, but got Unknown
			try
			{
				ClassInjector.RegisterTypeInIl2Cpp<UIFeatureList>();
				((Component)g.ui.OpenUI(new UITypeBase("UIFeatureList", (UILayer)0))).gameObject.AddComponent<UIFeatureList>().InitData();
			}
			catch (Exception ex)
			{
				Console.WriteLine(ex.ToString());
				Console.WriteLine("添加出错");
			}
		}

		internal void <InitData>b__24_22()
		{
			//IL_000b: Unknown result type (might be due to invalid IL or missing references)
			//IL_0016: Expected O, but got Unknown
			try
			{
				g.ui.CloseUI(new UITypeBase("UIAIMenu", (UILayer)0), false);
			}
			catch (Exception ex)
			{
				Console.WriteLine(ex.ToString());
				Console.WriteLine("V出错");
			}
		}

		internal void <TMFight>b__25_0()
		{
			//IL_0010: Unknown result type (might be due to invalid IL or missing references)
			//IL_001a: Expected O, but got Unknown
			ClassInjector.RegisterTypeInIl2Cpp<UIFightOne>();
			((Component)g.ui.OpenUI(new UITypeBase("UIFightOne", (UILayer)0))).gameObject.AddComponent<UIFightOne>().InitData();
		}

		internal void <TMFight>b__25_1()
		{
			//IL_0010: Unknown result type (might be due to invalid IL or missing references)
			//IL_001a: Expected O, but got Unknown
			ClassInjector.RegisterTypeInIl2Cpp<UIFight>();
			((Component)g.ui.OpenUI(new UITypeBase("UIFight", (UILayer)0))).gameObject.AddComponent<UIFight>().InitData();
		}

		internal void <TMFight>b__25_2()
		{
			//IL_0010: Unknown result type (might be due to invalid IL or missing references)
			//IL_001a: Expected O, but got Unknown
			ClassInjector.RegisterTypeInIl2Cpp<UIFightDiy>();
			((Component)g.ui.OpenUI(new UITypeBase("UIFightDiy", (UILayer)0))).gameObject.AddComponent<UIFightDiy>().InitData();
		}

		internal void <TMFight>b__25_3()
		{
		}

		internal void <TMFight>b__25_4()
		{
		}

		internal void <TMFight>b__25_5()
		{
		}
	}

	public GameObject ButtonExit;

	public GameObject ButtonUnitSet;

	public GameObject ButtonGroupChat;

	public GameObject ButtonWorldMsg;

	public GameObject ButtonFight;

	public GameObject ButtonSet;

	public GameObject ButtonUnitCMD;

	public GameObject ButtonNPCAI;

	public GameObject ButtonRegex;

	public GameObject ButtonKnowledge;

	public GameObject ButtonAIPromptComand;

	public GameObject ButtonLetter;

	public GameObject ButtonShare;

	public GameObject ButtonTeam;

	public GameObject ButtonAIGroup;

	public GameObject ButtonNPC;

	public GameObject ButtonUnitDie;

	public GameObject ButtonUnitSort;

	public GameObject ButtonUnitSIM;

	public GameObject ButtonProps;

	public GameObject ButtonSkill;

	public GameObject ButtonAICreateProp;

	public WorldUnitBase unit;

	public UIAIMenu(IntPtr ptr)
		: base(ptr)
	{
	}

	public void InitData(WorldUnitBase unitT)
	{
		//IL_0306: Unknown result type (might be due to invalid IL or missing references)
		//IL_030c: Unknown result type (might be due to invalid IL or missing references)
		//IL_03c8: Unknown result type (might be due to invalid IL or missing references)
		//IL_03ce: Unknown result type (might be due to invalid IL or missing references)
		//IL_041f: Unknown result type (might be due to invalid IL or missing references)
		//IL_0425: Unknown result type (might be due to invalid IL or missing references)
		//IL_0476: Unknown result type (might be due to invalid IL or missing references)
		//IL_047c: Unknown result type (might be due to invalid IL or missing references)
		//IL_04cd: Unknown result type (might be due to invalid IL or missing references)
		//IL_04d3: Unknown result type (might be due to invalid IL or missing references)
		//IL_0524: Unknown result type (might be due to invalid IL or missing references)
		//IL_052a: Unknown result type (might be due to invalid IL or missing references)
		//IL_05b4: Unknown result type (might be due to invalid IL or missing references)
		//IL_05ba: Unknown result type (might be due to invalid IL or missing references)
		//IL_060b: Unknown result type (might be due to invalid IL or missing references)
		//IL_0611: Unknown result type (might be due to invalid IL or missing references)
		//IL_0662: Unknown result type (might be due to invalid IL or missing references)
		//IL_0668: Unknown result type (might be due to invalid IL or missing references)
		//IL_06b9: Unknown result type (might be due to invalid IL or missing references)
		//IL_06bf: Unknown result type (might be due to invalid IL or missing references)
		//IL_07bb: Unknown result type (might be due to invalid IL or missing references)
		//IL_07c1: Unknown result type (might be due to invalid IL or missing references)
		try
		{
			unit = unitT;
			ButtonExit = ((Component)((Component)this).transform.Find("Root/PanelMain/ButtonExit")).gameObject;
			ButtonExit.GetComponent<Image>().sprite = SpriteTool.GetSprite("Common", "tuichu");
			ButtonUnitSet = ((Component)((Component)this).transform.Find("Root/Panel1/ButtonUnitSet")).gameObject;
			ButtonGroupChat = ((Component)((Component)this).transform.Find("Root/Panel1/ButtonGroupChat")).gameObject;
			ButtonSet = ((Component)((Component)this).transform.Find("Root/Panel2/ButtonSet")).gameObject;
			ButtonNPCAI = ((Component)((Component)this).transform.Find("Root/Panel2/ButtonNPCAI")).gameObject;
			ButtonRegex = ((Component)((Component)this).transform.Find("Root/Panel2/ButtonRegex")).gameObject;
			ButtonKnowledge = ((Component)((Component)this).transform.Find("Root/Panel2/ButtonKnowledge")).gameObject;
			ButtonAIPromptComand = ((Component)((Component)this).transform.Find("Root/Panel2/ButtonAIPromptComand")).gameObject;
			ButtonLetter = ((Component)((Component)this).transform.Find("Root/Panel3/ButtonLetter")).gameObject;
			ButtonShare = ((Component)((Component)this).transform.Find("Root/Panel3/ButtonShare")).gameObject;
			ButtonTeam = ((Component)((Component)this).transform.Find("Root/Panel3/ButtonTeam")).gameObject;
			ButtonAIGroup = ((Component)((Component)this).transform.Find("Root/Panel3/ButtonAIGroup")).gameObject;
			ButtonNPC = ((Component)((Component)this).transform.Find("Root/Panel3/ButtonNPC")).gameObject;
			ButtonUnitDie = ((Component)((Component)this).transform.Find("Root/Panel3/ButtonUnitDie")).gameObject;
			ButtonUnitSort = ((Component)((Component)this).transform.Find("Root/Panel3/ButtonUnitSort")).gameObject;
			ButtonUnitCMD = ((Component)((Component)this).transform.Find("Root/Panel3/ButtonUnitCMD")).gameObject;
			ButtonFight = ((Component)((Component)this).transform.Find("Root/Panel4/ButtonFight")).gameObject;
			ButtonUnitSIM = ((Component)((Component)this).transform.Find("Root/Panel4/ButtonUnitSIM")).gameObject;
			ButtonProps = ((Component)((Component)this).transform.Find("Root/Panel4/ButtonProps")).gameObject;
			ButtonWorldMsg = ((Component)((Component)this).transform.Find("Root/Panel4/ButtonWorldMsg")).gameObject;
			ButtonSkill = ((Component)((Component)this).transform.Find("Root/Panel4/ButtonSkill")).gameObject;
			ButtonAICreateProp = ((Component)((Component)this).transform.Find("Root/Panel4/ButtonAICreateProp")).gameObject;
			g.timer.Time(Action.op_Implicit((Action)delegate
			{
				g.world.unitLog.SaveLogData();
			}), 0f, false);
		}
		catch (Exception ex)
		{
			Console.WriteLine(ex.ToString());
		}
		((UnityEvent)ButtonUnitSet.GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
		{
			//IL_0010: Unknown result type (might be due to invalid IL or missing references)
			//IL_001a: Expected O, but got Unknown
			try
			{
				ClassInjector.RegisterTypeInIl2Cpp<UIUnitSet>();
				((Component)g.ui.OpenUI(new UITypeBase("UIUnitSet", (UILayer)0))).gameObject.AddComponent<UIUnitSet>().InitData(g.world.playerUnit);
			}
			catch (Exception ex2)
			{
				Console.WriteLine(ex2.ToString());
				Console.WriteLine("ButtonOK出错");
			}
		}));
		ButtonGroupChat.AddComponent<UISkyTipEffect>().InitData("群聊", default(Vector3));
		((UnityEvent)ButtonGroupChat.GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
		{
			//IL_0010: Unknown result type (might be due to invalid IL or missing references)
			//IL_001a: Expected O, but got Unknown
			try
			{
				ClassInjector.RegisterTypeInIl2Cpp<UIChatAINewGroup>();
				((Component)g.ui.OpenUI(new UITypeBase("UIChatAINewGroup", (UILayer)0))).gameObject.AddComponent<UIChatAINewGroup>().InitData();
			}
			catch (Exception ex2)
			{
				Console.WriteLine(ex2.ToString());
				Console.WriteLine("ButtonReset出错");
			}
		}));
		((UnityEvent)ButtonWorldMsg.GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
		{
			//IL_0010: Unknown result type (might be due to invalid IL or missing references)
			//IL_001a: Expected O, but got Unknown
			try
			{
				ClassInjector.RegisterTypeInIl2Cpp<UIWorldLog>();
				((Component)g.ui.OpenUI(new UITypeBase("UIWorldLog", (UILayer)0))).gameObject.AddComponent<UIWorldLog>().InitData();
			}
			catch (Exception ex2)
			{
				Console.WriteLine(ex2.ToString());
				Console.WriteLine("ButtonReset出错");
			}
		}));
		ButtonFight.SetActive(true);
		((UnityEvent)ButtonFight.GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
		{
			try
			{
				TMFight();
			}
			catch (Exception ex2)
			{
				Console.WriteLine(ex2.ToString());
				Console.WriteLine("ButtonReset出错");
			}
		}));
		ButtonUnitCMD.AddComponent<UISkyTipEffect>().InitData("NPC管理", default(Vector3));
		((UnityEvent)ButtonUnitCMD.GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
		{
			//IL_0010: Unknown result type (might be due to invalid IL or missing references)
			//IL_001a: Expected O, but got Unknown
			try
			{
				ClassInjector.RegisterTypeInIl2Cpp<UIUnitCMD>();
				UIUnitCMD uIUnitCMD = ((Component)g.ui.OpenUI(new UITypeBase("UIUnitCMD", (UILayer)0))).gameObject.AddComponent<UIUnitCMD>();
				List<string> unitID = new List<string> { g.world.playerUnit.data.unitData.unitID };
				uIUnitCMD.InitData(unitID);
			}
			catch (Exception ex2)
			{
				Console.WriteLine(ex2.ToString());
				Console.WriteLine("出错");
			}
		}));
		ButtonSet.AddComponent<UISkyTipEffect>().InitData("设置", default(Vector3));
		((UnityEvent)ButtonSet.GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
		{
			//IL_0010: Unknown result type (might be due to invalid IL or missing references)
			//IL_001a: Expected O, but got Unknown
			try
			{
				ClassInjector.RegisterTypeInIl2Cpp<UISetting>();
				((Component)g.ui.OpenUI(new UITypeBase("UISetting", (UILayer)0))).gameObject.AddComponent<UISetting>().InitData();
			}
			catch (Exception ex2)
			{
				Console.WriteLine(ex2.ToString());
				Console.WriteLine("出错");
			}
		}));
		ButtonNPCAI.AddComponent<UISkyTipEffect>().InitData("NPC-AI行动配置", default(Vector3));
		((UnityEvent)ButtonNPCAI.GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
		{
			//IL_0010: Unknown result type (might be due to invalid IL or missing references)
			//IL_001a: Expected O, but got Unknown
			try
			{
				ClassInjector.RegisterTypeInIl2Cpp<UINPCAISet>();
				((Component)g.ui.OpenUI(new UITypeBase("UINPCAISet", (UILayer)0))).gameObject.AddComponent<UINPCAISet>().InitData();
			}
			catch (Exception ex2)
			{
				Console.WriteLine(ex2.ToString());
				Console.WriteLine("出错");
			}
		}));
		ButtonRegex.AddComponent<UISkyTipEffect>().InitData("正则表达式管理", default(Vector3));
		((UnityEvent)ButtonRegex.GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
		{
			//IL_0010: Unknown result type (might be due to invalid IL or missing references)
			//IL_001a: Expected O, but got Unknown
			try
			{
				ClassInjector.RegisterTypeInIl2Cpp<UIRegexCMD>();
				((Component)g.ui.OpenUI(new UITypeBase("UIRegexCMD", (UILayer)0))).gameObject.AddComponent<UIRegexCMD>().InitData();
			}
			catch (Exception ex2)
			{
				Console.WriteLine(ex2.ToString());
				Console.WriteLine("出错");
			}
		}));
		ButtonKnowledge.AddComponent<UISkyTipEffect>().InitData("知识库管理", default(Vector3));
		((UnityEvent)ButtonKnowledge.GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
		{
			//IL_0010: Unknown result type (might be due to invalid IL or missing references)
			//IL_001a: Expected O, but got Unknown
			try
			{
				ClassInjector.RegisterTypeInIl2Cpp<UISettingKnowledge>();
				((Component)g.ui.OpenUI(new UITypeBase("UISettingKnowledge", (UILayer)0))).gameObject.AddComponent<UISettingKnowledge>().InitData();
			}
			catch (Exception ex2)
			{
				Console.WriteLine(ex2.ToString());
				Console.WriteLine("出错");
			}
		}));
		((UnityEvent)ButtonAIPromptComand.GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
		{
			//IL_0010: Unknown result type (might be due to invalid IL or missing references)
			//IL_001a: Expected O, but got Unknown
			try
			{
				ClassInjector.RegisterTypeInIl2Cpp<UIAIPromptComandNew>();
				((Component)g.ui.OpenUI(new UITypeBase("UIAIPromptComandNew", (UILayer)0))).gameObject.AddComponent<UIAIPromptComandNew>().InitData();
			}
			catch (Exception ex2)
			{
				Console.WriteLine(ex2.ToString());
				Console.WriteLine("出错");
			}
		}));
		ButtonShare.AddComponent<UISkyTipEffect>().InitData("共享池NPC管理", default(Vector3));
		((UnityEvent)ButtonShare.GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
		{
			//IL_0010: Unknown result type (might be due to invalid IL or missing references)
			//IL_001a: Expected O, but got Unknown
			try
			{
				ClassInjector.RegisterTypeInIl2Cpp<UIShareUnits>();
				((Component)g.ui.OpenUI(new UITypeBase("UIShareUnits", (UILayer)0))).gameObject.AddComponent<UIShareUnits>().Init(ModMain.shareUnit, "关注列表", 1);
			}
			catch (Exception ex2)
			{
				Console.WriteLine(ex2.ToString());
				Console.WriteLine("出错");
			}
		}));
		ButtonLetter.AddComponent<UISkyTipEffect>().InitData("书信池中的NPC，每个月过月时，有几率给玩家写信。每月最多一份。", default(Vector3));
		((UnityEvent)ButtonLetter.GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
		{
			//IL_0010: Unknown result type (might be due to invalid IL or missing references)
			//IL_001a: Expected O, but got Unknown
			try
			{
				ClassInjector.RegisterTypeInIl2Cpp<UIShareUnits>();
				((Component)g.ui.OpenUI(new UITypeBase("UIShareUnits", (UILayer)0))).gameObject.AddComponent<UIShareUnits>().Init(ModMain.letterUnitList, "书信列表", 2);
			}
			catch (Exception ex2)
			{
				Console.WriteLine(ex2.ToString());
				Console.WriteLine("出错");
			}
		}));
		ButtonTeam.AddComponent<UISkyTipEffect>().InitData("组队池中的NPC，会在战斗时帮助玩家作战。", default(Vector3));
		((UnityEvent)ButtonTeam.GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
		{
			//IL_0010: Unknown result type (might be due to invalid IL or missing references)
			//IL_001a: Expected O, but got Unknown
			try
			{
				ClassInjector.RegisterTypeInIl2Cpp<UIShareUnits>();
				UIShareUnits uIShareUnits = ((Component)g.ui.OpenUI(new UITypeBase("UIShareUnits", (UILayer)0))).gameObject.AddComponent<UIShareUnits>();
				List<string> list = new List<string>();
				Enumerator<WorldUnitBase> enumerator = g.world.unit.GetUnits(true).GetEnumerator();
				while (enumerator.MoveNext())
				{
					WorldUnitBase current = enumerator.Current;
					if (current.GetLuck(ModMain.mod_ID + 1) != null)
					{
						list.Add(current.data.unitData.unitID);
					}
				}
				uIShareUnits.Init(list, "组队列表", 3);
			}
			catch (Exception ex2)
			{
				Console.WriteLine(ex2.ToString());
				Console.WriteLine("出错");
			}
		}));
		ButtonAIGroup.AddComponent<UISkyTipEffect>().InitData("云AI行动池中的NPC，每个月过月时，会通过AI推演生成后续行动计划，AI推演使用天机阁推演的方案配置。该功能正在测试和持续完善中。。", default(Vector3));
		((UnityEvent)ButtonAIGroup.GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
		{
			//IL_0010: Unknown result type (might be due to invalid IL or missing references)
			//IL_001a: Expected O, but got Unknown
			try
			{
				ClassInjector.RegisterTypeInIl2Cpp<UIShareUnits>();
				UIShareUnits uIShareUnits = ((Component)g.ui.OpenUI(new UITypeBase("UIShareUnits", (UILayer)0))).gameObject.AddComponent<UIShareUnits>();
				List<string> list = new List<string>();
				Enumerator<WorldUnitBase> enumerator = g.world.unit.GetUnits(true).GetEnumerator();
				while (enumerator.MoveNext())
				{
					WorldUnitBase current = enumerator.Current;
					if (current.GetLuck(ModMain.mod_ID + 9) != null)
					{
						list.Add(current.data.unitData.unitID);
					}
				}
				uIShareUnits.Init(list, "云AI行动列表", 9);
			}
			catch (Exception ex2)
			{
				Console.WriteLine(ex2.ToString());
				Console.WriteLine("出错");
			}
		}));
		((UnityEvent)ButtonNPC.GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
		{
			//IL_005c: Unknown result type (might be due to invalid IL or missing references)
			//IL_0066: Expected O, but got Unknown
			try
			{
				List<WorldUnitBase> units = g.world.unit.GetUnits(true);
				List<string> list = new List<string>();
				Enumerator<WorldUnitBase> enumerator = units.GetEnumerator();
				while (enumerator.MoveNext())
				{
					WorldUnitBase current = enumerator.Current;
					list.Add(current.data.unitData.unitID);
				}
				if (list.Count > 0)
				{
					ClassInjector.RegisterTypeInIl2Cpp<UISelectUnitNew>();
					UISelectUnitNew uISelectUnitNew = ((Component)g.ui.OpenUI(new UITypeBase("UISelectUnitNew", (UILayer)0))).gameObject.AddComponent<UISelectUnitNew>();
					uISelectUnitNew.Init(list, "NPC预览", 0);
					uISelectUnitNew.ToggleAllUnit.GetComponent<Toggle>().isOn = true;
				}
			}
			catch (Exception ex2)
			{
				Console.WriteLine(ex2.ToString());
				Console.WriteLine("添加出错");
			}
		}));
		((UnityEvent)ButtonUnitDie.GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
		{
			//IL_0010: Unknown result type (might be due to invalid IL or missing references)
			//IL_001a: Expected O, but got Unknown
			try
			{
				ClassInjector.RegisterTypeInIl2Cpp<UIUnitDieCMD>();
				((Component)g.ui.OpenUI(new UITypeBase("UIUnitDieCMD", (UILayer)0))).gameObject.AddComponent<UIUnitDieCMD>().InitData();
			}
			catch (Exception ex2)
			{
				Console.WriteLine(ex2.ToString());
				Console.WriteLine("添加出错");
			}
		}));
		((UnityEvent)ButtonUnitSort.GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
		{
			//IL_0048: Unknown result type (might be due to invalid IL or missing references)
			//IL_0052: Expected O, but got Unknown
			//IL_0017: Unknown result type (might be due to invalid IL or missing references)
			//IL_0021: Expected O, but got Unknown
			try
			{
				if (ModMain.isSortEnd)
				{
					ClassInjector.RegisterTypeInIl2Cpp<UISort>();
					((Component)g.ui.OpenUI(new UITypeBase("UISort", (UILayer)0))).gameObject.AddComponent<UISort>().InitData();
				}
				else
				{
					UITipItem.AddTip("排行榜尚正在更新，请稍后再试！", 2f);
					MultiThreadTask multiThreadTask = new MultiThreadTask(false);
					multiThreadTask.AddCall(Action.op_Implicit((Action)ModMain.OnSort));
					Action action = delegate
					{
						multiThreadTask.Stop();
						Console.WriteLine("排名结束！" + multiThreadTask.isStop);
						UITipItem.AddTip("排行榜已更新完成！", 2f);
					};
					multiThreadTask.Run(true, Action.op_Implicit(action));
				}
			}
			catch (Exception ex2)
			{
				Console.WriteLine(ex2.ToString());
				Console.WriteLine("添加出错");
			}
		}));
		ButtonUnitSIM.AddComponent<UISkyTipEffect>().InitData("通过天道模拟，可通过AI推演玩家后续人生经历，模拟结束后可获得模拟积分兑换奖励，天道模拟使用天机阁推演的方案配置。该功能正在测试和持续完善中。。", default(Vector3));
		((UnityEvent)ButtonUnitSIM.GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
		{
			//IL_0010: Unknown result type (might be due to invalid IL or missing references)
			//IL_001a: Expected O, but got Unknown
			try
			{
				ClassInjector.RegisterTypeInIl2Cpp<UIUnitSIM>();
				((Component)g.ui.OpenUI(new UITypeBase("UIUnitSIM", (UILayer)0))).gameObject.AddComponent<UIUnitSIM>().InitData(unit);
			}
			catch (Exception ex2)
			{
				Console.WriteLine(ex2.ToString());
				Console.WriteLine("添加出错");
			}
		}));
		((UnityEvent)ButtonProps.GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
		{
			//IL_0010: Unknown result type (might be due to invalid IL or missing references)
			//IL_001a: Expected O, but got Unknown
			try
			{
				ClassInjector.RegisterTypeInIl2Cpp<UISelectProp>();
				((Component)g.ui.OpenUI(new UITypeBase("UISelectProp", (UILayer)0))).gameObject.AddComponent<UISelectProp>().InitData(unit);
			}
			catch (Exception ex2)
			{
				Console.WriteLine(ex2.ToString());
				Console.WriteLine("添加出错");
			}
		}));
		((UnityEvent)ButtonSkill.GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
		{
			//IL_0010: Unknown result type (might be due to invalid IL or missing references)
			//IL_001a: Expected O, but got Unknown
			try
			{
				ClassInjector.RegisterTypeInIl2Cpp<UISelectSkill>();
				((Component)g.ui.OpenUI(new UITypeBase("UISelectSkill", (UILayer)0))).gameObject.AddComponent<UISelectSkill>().InitData(unit, 1);
			}
			catch (Exception ex2)
			{
				Console.WriteLine(ex2.ToString());
				Console.WriteLine("添加出错");
			}
		}));
		((UnityEvent)ButtonAICreateProp.GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
		{
			//IL_0010: Unknown result type (might be due to invalid IL or missing references)
			//IL_001a: Expected O, but got Unknown
			try
			{
				ClassInjector.RegisterTypeInIl2Cpp<UIFeatureList>();
				((Component)g.ui.OpenUI(new UITypeBase("UIFeatureList", (UILayer)0))).gameObject.AddComponent<UIFeatureList>().InitData();
			}
			catch (Exception ex2)
			{
				Console.WriteLine(ex2.ToString());
				Console.WriteLine("添加出错");
			}
		}));
		((UnityEvent)ButtonExit.GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
		{
			//IL_000b: Unknown result type (might be due to invalid IL or missing references)
			//IL_0016: Expected O, but got Unknown
			try
			{
				g.ui.CloseUI(new UITypeBase("UIAIMenu", (UILayer)0), false);
			}
			catch (Exception ex2)
			{
				Console.WriteLine(ex2.ToString());
				Console.WriteLine("V出错");
			}
		}));
	}

	public void TMFight()
	{
		//IL_0008: Unknown result type (might be due to invalid IL or missing references)
		//IL_00e9: Unknown result type (might be due to invalid IL or missing references)
		//IL_00ff: Unknown result type (might be due to invalid IL or missing references)
		//IL_0115: Unknown result type (might be due to invalid IL or missing references)
		//IL_012b: Unknown result type (might be due to invalid IL or missing references)
		//IL_0141: Unknown result type (might be due to invalid IL or missing references)
		//IL_014f: Unknown result type (might be due to invalid IL or missing references)
		//IL_015d: Unknown result type (might be due to invalid IL or missing references)
		//IL_016b: Unknown result type (might be due to invalid IL or missing references)
		//IL_0179: Unknown result type (might be due to invalid IL or missing references)
		UICustomDramaDyn val = new UICustomDramaDyn(ModMain.mod_ID + 9);
		int num = ModMain.mod_ID + 1;
		int num2 = ModMain.mod_ID + 2;
		int num3 = ModMain.mod_ID + 3;
		int num4 = ModMain.mod_ID + 4;
		Action action = delegate
		{
			//IL_0010: Unknown result type (might be due to invalid IL or missing references)
			//IL_001a: Expected O, but got Unknown
			ClassInjector.RegisterTypeInIl2Cpp<UIFightOne>();
			((Component)g.ui.OpenUI(new UITypeBase("UIFightOne", (UILayer)0))).gameObject.AddComponent<UIFightOne>().InitData();
		};
		Action action2 = delegate
		{
			//IL_0010: Unknown result type (might be due to invalid IL or missing references)
			//IL_001a: Expected O, but got Unknown
			ClassInjector.RegisterTypeInIl2Cpp<UIFight>();
			((Component)g.ui.OpenUI(new UITypeBase("UIFight", (UILayer)0))).gameObject.AddComponent<UIFight>().InitData();
		};
		Action action3 = delegate
		{
			//IL_0010: Unknown result type (might be due to invalid IL or missing references)
			//IL_001a: Expected O, but got Unknown
			ClassInjector.RegisterTypeInIl2Cpp<UIFightDiy>();
			((Component)g.ui.OpenUI(new UITypeBase("UIFightDiy", (UILayer)0))).gameObject.AddComponent<UIFightDiy>().InitData();
		};
		Action action4 = delegate
		{
		};
		if (<>c.<>9__25_4 == null)
		{
			<>c.<>9__25_4 = delegate
			{
			};
		}
		if (<>c.<>9__25_5 == null)
		{
			<>c.<>9__25_5 = delegate
			{
			};
		}
		((UICustomDramaBase)val).dramaData.dialogueOptions[num] = "天命争锋（匹配赛）";
		((UICustomDramaBase)val).dramaData.dialogueOptions[num2] = "天命争锋（轮回赛）";
		((UICustomDramaBase)val).dramaData.dialogueOptions[num3] = "天命争锋（自由赛）";
		((UICustomDramaBase)val).dramaData.dialogueOptions[num4] = "取消";
		val.SetOptionCall(num, Action.op_Implicit(action));
		val.SetOptionCall(num2, Action.op_Implicit(action2));
		val.SetOptionCall(num3, Action.op_Implicit(action3));
		val.SetOptionCall(num4, Action.op_Implicit(action4));
		((UICustomDramaBase)val).dramaData.unitLeft = g.world.playerUnit;
		val.OpenUI();
	}
}


//==================== TYPE MOD_SSCYAI.UIAIMenu.<>c ====================
// decompile failed: Could not find type definition MOD_SSCYAI.UIAIMenu.<>c in type system.

//==================== TYPE MOD_SSCYAI.UIAIMenu.<>c__DisplayClass24_0 ====================
// decompile failed: Could not find type definition MOD_SSCYAI.UIAIMenu.<>c__DisplayClass24_0 in type system.

