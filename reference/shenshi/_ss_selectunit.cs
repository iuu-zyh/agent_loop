//==================== TYPE MOD_SSCYAI.UISelectUnitNew ====================
using System;
using System.Collections.Generic;
using System.Linq;
using System.Web.WebPages;
using Il2CppSystem;
using Il2CppSystem.Collections.Generic;
using UnhollowerRuntimeLib;
using UnityEngine;
using UnityEngine.Events;
using UnityEngine.UI;

namespace MOD_SSCYAI;

public class UISelectUnitNew(IntPtr ptr) : UIBase(ptr)
{
	public GameObject ButtonExit;

	public GameObject ButtonAdd;

	public GameObject goUnitListRoot;

	public GameObject goUnitList;

	public GameObject goTiltle;

	public GameObject ButtonFind;

	public GameObject DropdownPrePageCount;

	public GameObject DropdownGrade;

	public GameObject DropdownSex;

	public GameObject DropdownSchool;

	public GameObject DropdownCharacter;

	public GameObject DropdownBeauty;

	public GameObject DropdownRace;

	public GameObject DropdownReltion;

	public GameObject InputFieldName;

	public GameObject InputFieldLuck;

	public GameObject InputFieldProp;

	public GameObject ToggleMarry;

	public GameObject ToggleHero;

	public GameObject ToggleAllUnit;

	public GameObject ToggleChatUnit;

	public GameObject ToggleSelectALL;

	public GameObject ToggleSelectNO;

	public TimerCoroutine timerCoroutineFind;

	public TimerCoroutine timerCoroutineUpdata;

	public int selGrade = -1;

	public int selSex = -1;

	public string selSchool = "-1";

	public int selCharacter = -1;

	public string selBeauty = "-1";

	public int selRace = -1;

	public int selReltion = -1;

	public int curPage = 1;

	public int prePage = 50;

	public int type;

	public List<WorldUnitBase> units = new List<WorldUnitBase>();

	public List<UINPCUnitInfoItem> items = new List<UINPCUnitInfoItem>();

	public List<string> selUnits = new List<string>();

	public UIAIGroupCommand uIAIGroupCommand;

	public static int fightSelIndex = -1;

	public static int unitIndex = 0;

	public void Init(List<string> unitsID, string tiltle, int tp, bool isFirst = true)
	{
		//IL_0052: Unknown result type (might be due to invalid IL or missing references)
		//IL_005c: Expected O, but got Unknown
		//IL_02bf: Unknown result type (might be due to invalid IL or missing references)
		//IL_02c5: Unknown result type (might be due to invalid IL or missing references)
		//IL_02ed: Unknown result type (might be due to invalid IL or missing references)
		//IL_02f3: Unknown result type (might be due to invalid IL or missing references)
		//IL_031b: Unknown result type (might be due to invalid IL or missing references)
		//IL_0321: Unknown result type (might be due to invalid IL or missing references)
		//IL_008e: Unknown result type (might be due to invalid IL or missing references)
		//IL_0095: Expected O, but got Unknown
		//IL_09c2: Unknown result type (might be due to invalid IL or missing references)
		//IL_09cd: Expected O, but got Unknown
		goTiltle = ((Component)((Component)this).transform.Find("Root/PanelMain/Text")).gameObject;
		goTiltle.GetComponent<Text>().text = tiltle;
		type = tp;
		UIBase uI = g.ui.GetUI(new UITypeBase("UIAIGroupCommand", (UILayer)0));
		if ((Object)(object)uI != (Object)null)
		{
			uIAIGroupCommand = ((Component)uI).GetComponent<UIAIGroupCommand>();
		}
		curPage = 1;
		Shader val = Shader.Find("Custom/BackBlur");
		if ((Object)(object)val != (Object)null)
		{
			Material material = new Material(val);
			((Component)((Component)this).transform.GetChild(0)).GetComponent<Image>().material = material;
		}
		ButtonFind = ((Component)((Component)this).transform.Find("Root/PanelSelect/ButtonFind")).gameObject;
		DropdownGrade = ((Component)((Component)this).transform.Find("Root/PanelSelect/DropdownGrade")).gameObject;
		DropdownSex = ((Component)((Component)this).transform.Find("Root/PanelSelect/DropdownSex")).gameObject;
		DropdownSchool = ((Component)((Component)this).transform.Find("Root/PanelSelect/DropdownSchool")).gameObject;
		DropdownCharacter = ((Component)((Component)this).transform.Find("Root/PanelSelect/DropdownCharacter")).gameObject;
		DropdownBeauty = ((Component)((Component)this).transform.Find("Root/PanelSelect/DropdownBeauty")).gameObject;
		DropdownRace = ((Component)((Component)this).transform.Find("Root/PanelSelect/DropdownRace")).gameObject;
		DropdownReltion = ((Component)((Component)this).transform.Find("Root/PanelSelect/DropdownReltion")).gameObject;
		InputFieldName = ((Component)((Component)this).transform.Find("Root/PanelSelect/InputFieldName")).gameObject;
		InputFieldLuck = ((Component)((Component)this).transform.Find("Root/PanelSelect/InputFieldLuck")).gameObject;
		InputFieldProp = ((Component)((Component)this).transform.Find("Root/PanelSelect/InputFieldProp")).gameObject;
		ToggleMarry = ((Component)((Component)this).transform.Find("Root/PanelSelect/ToggleMarry")).gameObject;
		ToggleHero = ((Component)((Component)this).transform.Find("Root/PanelSelect/ToggleHero")).gameObject;
		ToggleAllUnit = ((Component)((Component)this).transform.Find("Root/PanelSelect/ToggleAllUnit")).gameObject;
		ToggleChatUnit = ((Component)((Component)this).transform.Find("Root/PanelSelect/ToggleChatUnit")).gameObject;
		DropdownPrePageCount = ((Component)((Component)this).transform.Find("Root/PanelMain/DropdownPrePageCount")).gameObject;
		ToggleSelectALL = ((Component)((Component)this).transform.Find("Root/PanelSelect/ToggleSelectALL")).gameObject;
		((UnityEvent<bool>)(object)ToggleSelectALL.GetComponent<Toggle>().onValueChanged).AddListener(UnityAction<bool>.op_Implicit((Action<bool>)delegate(bool value)
		{
			for (int num = goUnitListRoot.transform.GetChildCount() - 1; num >= 0; num--)
			{
				((Component)goUnitListRoot.transform.GetChild(num).Find("Toggle")).GetComponent<Toggle>().isOn = value;
			}
		}));
		((Component)((Component)this).transform.Find("Root/PanelSelect/TextName")).gameObject.AddComponent<UISkyTipEffect>().InitData("通过姓名查找，支持模糊查询!", default(Vector3));
		((Component)((Component)this).transform.Find("Root/PanelSelect/TextLuck")).gameObject.AddComponent<UISkyTipEffect>().InitData("通过拥有气运查找，支持模糊查询气运名称或精准查找气运ID!", default(Vector3));
		((Component)((Component)this).transform.Find("Root/PanelSelect/TextProp")).gameObject.AddComponent<UISkyTipEffect>().InitData("通过拥有道具查找，支持模糊查询道具名称或精准查找道具ID!", default(Vector3));
		((UnityEvent)((Component)((Component)this).transform.Find("Root/PanelMain/ButtonLast")).GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
		{
			curPage--;
			UpdataNPCList();
		}));
		((UnityEvent)((Component)((Component)this).transform.Find("Root/PanelMain/ButtonNext")).GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
		{
			curPage++;
			UpdataNPCList();
		}));
		((UnityEvent<int>)(object)DropdownPrePageCount.GetComponent<Dropdown>().onValueChanged).AddListener(UnityAction<int>.op_Implicit((Action<int>)delegate(int value)
		{
			switch (value)
			{
			case 0:
				prePage = 50;
				break;
			case 1:
				prePage = 500;
				break;
			case 2:
				prePage = 5000;
				break;
			case 3:
				prePage = 50000;
				break;
			}
			curPage = 1;
			UpdataNPCList();
		}));
		ButtonExit = ((Component)((Component)this).transform.Find("Root/PanelMain/ButtonExit")).gameObject;
		ButtonExit.GetComponent<Image>().sprite = SpriteTool.GetSprite("Common", "tuichu");
		((UnityEvent)ButtonExit.GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
		{
			//IL_0066: Unknown result type (might be due to invalid IL or missing references)
			//IL_0071: Expected O, but got Unknown
			try
			{
				if (type == 4 && (Object)(object)uIAIGroupCommand != (Object)null)
				{
					uIAIGroupCommand.UpdataNPCList();
				}
				g.timer.Stop(timerCoroutineFind);
				g.timer.Stop(timerCoroutineUpdata);
				g.ui.CloseUI(new UITypeBase("UISelectUnitNew", (UILayer)0), false);
			}
			catch (Exception ex2)
			{
				Console.WriteLine(ex2.ToString());
				Console.WriteLine("退出出错");
			}
		}));
		goUnitListRoot = ((Component)((Component)this).transform.Find("Root/PanelMain/Scroll View/Viewport/Content")).gameObject;
		try
		{
			foreach (string item in new HashSet<string>(unitsID).ToList())
			{
				WorldUnitBase unit = g.world.unit.GetUnit(item, true);
				if (unit != null)
				{
					units.Add(unit);
				}
			}
		}
		catch (Exception ex)
		{
			Console.WriteLine(ex.ToString());
		}
		((UnityEvent)ButtonFind.GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
		{
			timerCoroutineFind = g.timer.Time(Action.op_Implicit((Action)delegate
			{
				//IL_0383: Unknown result type (might be due to invalid IL or missing references)
				//IL_03c2: Unknown result type (might be due to invalid IL or missing references)
				//IL_03c9: Invalid comparison between Unknown and I4
				string text = InputFieldName.GetComponent<InputField>().text;
				string text2 = InputFieldLuck.GetComponent<InputField>().text;
				string text3 = InputFieldProp.GetComponent<InputField>().text;
				units.Clear();
				HashSet<string> hashSet = new HashSet<string>();
				if (ToggleAllUnit.GetComponent<Toggle>().isOn)
				{
					Enumerator<WorldUnitBase> enumerator3 = g.world.unit.GetUnits(true).GetEnumerator();
					while (enumerator3.MoveNext())
					{
						WorldUnitBase current9 = enumerator3.Current;
						if (!hashSet.Contains(current9.data.unitData.unitID))
						{
							hashSet.Add(current9.data.unitData.unitID);
						}
					}
				}
				else
				{
					foreach (string item2 in unitsID)
					{
						if (!hashSet.Contains(item2))
						{
							hashSet.Add(item2);
						}
					}
				}
				foreach (string item3 in hashSet)
				{
					bool flag = false;
					WorldUnitBase unit2 = g.world.unit.GetUnit(item3, true);
					if (unit2 != null)
					{
						if (selGrade == -1 || selGrade == unit2.data.dynUnitData.GetGrade())
						{
							flag = true;
							if (selSex == -1 || selSex == unit2.data.dynUnitData.sex.baseValue)
							{
								flag = true;
								if (selSchool.Equals("-1"))
								{
									flag = true;
								}
								else if (unit2.data.school != null)
								{
									if (!((BuildDataBase)unit2.data.school.GetTopSchool().buildData).id.Equals(selSchool))
									{
										flag = false;
										continue;
									}
									flag = true;
								}
								else
								{
									if (!selSchool.Equals("0"))
									{
										flag = false;
										continue;
									}
									flag = true;
								}
								if (selCharacter == -1 || unit2.data.GetCharacter().Contains(selCharacter))
								{
									flag = true;
									if (selBeauty.Equals("-1"))
									{
										flag = true;
									}
									else
									{
										int value = unit2.data.dynUnitData.beauty.value;
										if (!GameTool.LS(g.conf.roleBeauty.GetItemInBeauty(value).text).Equals(selBeauty))
										{
											flag = false;
											continue;
										}
										flag = true;
									}
									if (selRace == -1 || selRace == unit2.data.dynUnitData.race.baseValue)
									{
										flag = true;
										if (selReltion == -1)
										{
											flag = true;
										}
										else
										{
											RelationData relationData = unit2.data.unitData.relationData;
											WorldUnitBase playerUnit = g.world.playerUnit;
											RelationData relationData2 = playerUnit.data.unitData.relationData;
											if (selReltion == 1 && (int)relationData2.GetRelation(unit2) != 0)
											{
												flag = true;
											}
											else if (selReltion == 2 && relationData2.IsFriend(unit2))
											{
												flag = true;
											}
											else if (selReltion == 6 && (int)relationData2.GetRelation(unit2) == 9)
											{
												flag = true;
											}
											else
											{
												int intim = relationData.GetIntim(playerUnit);
												if (intim > 0 && selReltion == 3)
												{
													flag = true;
												}
												else if (intim < 0 && selReltion == 4)
												{
													flag = true;
												}
												else
												{
													if (intim != 0 || selReltion != 5)
													{
														flag = false;
														continue;
													}
													flag = true;
												}
											}
										}
										if (!StringExtensions.IsEmpty(text))
										{
											if (!unit2.data.unitData.propertyData.GetName().Contains(text))
											{
												flag = false;
												continue;
											}
											flag = true;
										}
										if (!StringExtensions.IsEmpty(text2))
										{
											flag = false;
											Enumerator<WorldUnitLuckBase> enumerator6 = unit2.allLuck.GetEnumerator();
											while (enumerator6.MoveNext())
											{
												WorldUnitLuckBase current12 = enumerator6.Current;
												if (StringExtensions.IsInt(text2))
												{
													int num = int.Parse(text2);
													if (current12.luckData.id == num)
													{
														flag = true;
														break;
													}
												}
												else if (GameTool.LS(current12.luckConf.name).Contains(text2))
												{
													flag = true;
													break;
												}
											}
										}
										if (!StringExtensions.IsEmpty(text3))
										{
											flag = false;
											Enumerator<PropsData> enumerator7 = ((DataProps)unit2.data.unitData.propData).allProps.GetEnumerator();
											while (enumerator7.MoveNext())
											{
												PropsData current13 = enumerator7.Current;
												if (StringExtensions.IsInt(text3))
												{
													int num2 = int.Parse(text3);
													if (current13.propsID == num2)
													{
														flag = true;
														break;
													}
												}
												else if (current13.propsInfoBase.name.Contains(text3))
												{
													flag = true;
													break;
												}
											}
										}
										if (ToggleHero.GetComponent<Toggle>().isOn)
										{
											if (!unit2.data.unitData.heart.IsHeroes())
											{
												flag = false;
												continue;
											}
											flag = true;
										}
										if (ToggleMarry.GetComponent<Toggle>().isOn)
										{
											bool num3 = unit2.data.GetRelation((UnitBothRelationType)6).Count <= 0;
											bool flag2 = unit2.data.GetRelation((UnitBothRelationType)5).Count <= 0;
											if (!(num3 & flag2))
											{
												flag = false;
												continue;
											}
											flag = true;
										}
										if (ToggleChatUnit.GetComponent<Toggle>().isOn)
										{
											if (!ModMain.Messages.ContainsKey(unit2.GetUnitID()))
											{
												flag = false;
												continue;
											}
											if (ModMain.Messages[unit2.GetUnitID()].Count < 6)
											{
												flag = false;
												continue;
											}
											flag = true;
										}
										if (flag)
										{
											units.Add(unit2);
										}
									}
									else
									{
										flag = false;
									}
								}
								else
								{
									flag = false;
								}
							}
							else
							{
								flag = false;
							}
						}
						else
						{
							flag = false;
						}
					}
				}
				if (units.Count > 0)
				{
					UITipItem.AddTip("搜索到符合条件的NPC数量：" + units.Count, 3f);
					UpdataNPCList();
				}
				else
				{
					UITipItem.AddTip("未搜索到符合条件的NPC", 3f);
				}
			}), 0f, false);
		}));
		List<string> val2 = new List<string>();
		if (ModMain.FindList.ContainsKey("Grade"))
		{
			foreach (Tuple<string, string> item4 in ModMain.FindList["Grade"])
			{
				val2.Add(item4.Item1);
			}
		}
		DropdownGrade.gameObject.GetComponent<Dropdown>().AddOptions(val2);
		((UnityEvent<int>)(object)DropdownGrade.gameObject.GetComponent<Dropdown>().onValueChanged).AddListener(UnityAction<int>.op_Implicit((Action<int>)ChangeGrade));
		List<string> val3 = new List<string>();
		if (ModMain.FindList.ContainsKey("Sex"))
		{
			foreach (Tuple<string, string> item5 in ModMain.FindList["Sex"])
			{
				val3.Add(item5.Item1);
			}
		}
		DropdownSex.gameObject.GetComponent<Dropdown>().AddOptions(val3);
		((UnityEvent<int>)(object)DropdownSex.gameObject.GetComponent<Dropdown>().onValueChanged).AddListener(UnityAction<int>.op_Implicit((Action<int>)ChangeSex));
		List<string> val4 = new List<string>();
		if (ModMain.FindList.ContainsKey("School"))
		{
			foreach (Tuple<string, string> item6 in ModMain.FindList["School"])
			{
				val4.Add(item6.Item1);
			}
		}
		DropdownSchool.gameObject.GetComponent<Dropdown>().AddOptions(val4);
		((UnityEvent<int>)(object)DropdownSchool.gameObject.GetComponent<Dropdown>().onValueChanged).AddListener(UnityAction<int>.op_Implicit((Action<int>)ChangeSchool));
		List<string> val5 = new List<string>();
		if (ModMain.FindList.ContainsKey("Character"))
		{
			foreach (Tuple<string, string> item7 in ModMain.FindList["Character"])
			{
				val5.Add(item7.Item1);
			}
		}
		DropdownCharacter.gameObject.GetComponent<Dropdown>().AddOptions(val5);
		((UnityEvent<int>)(object)DropdownCharacter.gameObject.GetComponent<Dropdown>().onValueChanged).AddListener(UnityAction<int>.op_Implicit((Action<int>)ChangeCharacter));
		List<string> val6 = new List<string>();
		if (ModMain.FindList.ContainsKey("Beauty"))
		{
			foreach (Tuple<string, string> item8 in ModMain.FindList["Beauty"])
			{
				val6.Add(item8.Item1);
			}
		}
		DropdownBeauty.gameObject.GetComponent<Dropdown>().AddOptions(val6);
		((UnityEvent<int>)(object)DropdownBeauty.gameObject.GetComponent<Dropdown>().onValueChanged).AddListener(UnityAction<int>.op_Implicit((Action<int>)ChangeBeauty));
		List<string> val7 = new List<string>();
		if (ModMain.FindList.ContainsKey("Race"))
		{
			foreach (Tuple<string, string> item9 in ModMain.FindList["Race"])
			{
				val7.Add(item9.Item1);
			}
		}
		DropdownRace.gameObject.GetComponent<Dropdown>().AddOptions(val7);
		((UnityEvent<int>)(object)DropdownRace.gameObject.GetComponent<Dropdown>().onValueChanged).AddListener(UnityAction<int>.op_Implicit((Action<int>)ChangeRace));
		List<string> val8 = new List<string>();
		if (ModMain.FindList.ContainsKey("Reltion"))
		{
			foreach (Tuple<string, string> item10 in ModMain.FindList["Reltion"])
			{
				val8.Add(item10.Item1);
			}
		}
		DropdownReltion.gameObject.GetComponent<Dropdown>().AddOptions(val8);
		((UnityEvent<int>)(object)DropdownReltion.gameObject.GetComponent<Dropdown>().onValueChanged).AddListener(UnityAction<int>.op_Implicit((Action<int>)ChangeReltion));
		ButtonAdd = ((Component)((Component)this).transform.Find("Root/PanelMain/ButtonAdd")).gameObject;
		if (type == 0)
		{
			((Component)((Component)this).transform.Find("Root/PanelMain/ButtonAdd/Text")).GetComponent<Text>().text = "管理";
		}
		if (type == 5)
		{
			ButtonAdd.SetActive(false);
		}
		((UnityEvent)ButtonAdd.GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
		{
			//IL_0033: Unknown result type (might be due to invalid IL or missing references)
			//IL_003d: Expected O, but got Unknown
			//IL_0281: Unknown result type (might be due to invalid IL or missing references)
			//IL_028c: Expected O, but got Unknown
			//IL_02aa: Unknown result type (might be due to invalid IL or missing references)
			//IL_02b4: Expected O, but got Unknown
			//IL_02f2: Unknown result type (might be due to invalid IL or missing references)
			//IL_02fc: Expected O, but got Unknown
			//IL_033d: Unknown result type (might be due to invalid IL or missing references)
			//IL_0347: Expected O, but got Unknown
			//IL_0125: Unknown result type (might be due to invalid IL or missing references)
			//IL_012f: Expected O, but got Unknown
			//IL_0422: Unknown result type (might be due to invalid IL or missing references)
			//IL_042c: Expected O, but got Unknown
			//IL_0215: Unknown result type (might be due to invalid IL or missing references)
			//IL_021f: Expected O, but got Unknown
			try
			{
				if (selUnits.Count > 0)
				{
					if (type == 0)
					{
						ClassInjector.RegisterTypeInIl2Cpp<UIUnitCMD>();
						((Component)g.ui.OpenUI(new UITypeBase("UIUnitCMD", (UILayer)0))).gameObject.AddComponent<UIUnitCMD>().InitData(selUnits);
					}
					else
					{
						int num = 0;
						foreach (string selUnit in selUnits)
						{
							WorldUnitBase unit2 = g.world.unit.GetUnit(selUnit, true);
							if (unit2 != null)
							{
								num++;
								if (type == 1)
								{
									CommandTool.SetUnitChatShareMode(unit2, 1);
								}
								else if (type == 2)
								{
									CommandTool.SetUnitLetterMode(unit2, 1);
								}
								else if (type == 3)
								{
									if (unit2.data.unitData.relationData.GetIntim(g.world.playerUnit.GetUnitID()) >= 180)
									{
										DramaFunctionTool.OptionsFunction("addNPCLuck_0_" + (ModMain.mod_ID + 1) + "_-1", new DramaFunctionData(unit2));
									}
									else
									{
										UITipItem.AddTip(unit2.GetName() + "对你的好感度低于3颗星，自动忽略你的组队请求！", 3f);
										num--;
									}
								}
								else if (type == 4)
								{
									if ((Object)(object)uIAIGroupCommand != (Object)null)
									{
										if (!uIAIGroupCommand.units.Contains(selUnit))
										{
											uIAIGroupCommand.units.Add(selUnit);
										}
										Console.WriteLine("uIAIGroupCommand已添加Unit:" + selUnit);
									}
								}
								else if (type == 9)
								{
									if (unit2.data.unitData.relationData.GetIntim(g.world.playerUnit.GetUnitID()) >= 0)
									{
										DramaFunctionTool.OptionsFunction("addNPCLuck_0_" + (ModMain.mod_ID + 9) + "_-1", new DramaFunctionData(unit2));
									}
									else
									{
										UITipItem.AddTip(unit2.GetName() + "对你的好感度为负，自动忽略你的AI设置请求！", 3f);
										num--;
									}
								}
							}
						}
						UITipItem.AddTip("成功设置NPC数量：" + num, 3f);
						g.ui.CloseUI(new UITypeBase("UISelectUnitNew", (UILayer)0), false);
						if (type == 1)
						{
							ClassInjector.RegisterTypeInIl2Cpp<UIShareUnits>();
							((Component)g.ui.OpenUI(new UITypeBase("UIShareUnits", (UILayer)0))).gameObject.AddComponent<UIShareUnits>().Init(ModMain.shareUnit, "关注列表", 1);
						}
						else if (type == 2)
						{
							ClassInjector.RegisterTypeInIl2Cpp<UIShareUnits>();
							((Component)g.ui.OpenUI(new UITypeBase("UIShareUnits", (UILayer)0))).gameObject.AddComponent<UIShareUnits>().Init(ModMain.letterUnitList, "书信列表", 2);
						}
						else if (type == 3)
						{
							ClassInjector.RegisterTypeInIl2Cpp<UIShareUnits>();
							UIShareUnits uIShareUnits = ((Component)g.ui.OpenUI(new UITypeBase("UIShareUnits", (UILayer)0))).gameObject.AddComponent<UIShareUnits>();
							List<string> list = new List<string>();
							foreach (WorldUnitBase unitTeam in ModMain.unitTeams)
							{
								if (unitTeam.GetLuck(ModMain.mod_ID + 1) != null)
								{
									list.Add(unitTeam.data.unitData.unitID);
								}
							}
							uIShareUnits.Init(list, "组队列表", 3);
						}
						else if (type == 4)
						{
							if ((Object)(object)uIAIGroupCommand != (Object)null)
							{
								uIAIGroupCommand.UpdataNPCList();
							}
						}
						else if (type == 9)
						{
							ClassInjector.RegisterTypeInIl2Cpp<UIShareUnits>();
							UIShareUnits uIShareUnits2 = ((Component)g.ui.OpenUI(new UITypeBase("UIShareUnits", (UILayer)0))).gameObject.AddComponent<UIShareUnits>();
							List<string> list2 = new List<string>();
							Enumerator<WorldUnitBase> enumerator5 = g.world.unit.GetUnits(true).GetEnumerator();
							while (enumerator5.MoveNext())
							{
								WorldUnitBase current11 = enumerator5.Current;
								if (current11.GetLuck(ModMain.mod_ID + 9) != null)
								{
									list2.Add(current11.data.unitData.unitID);
								}
							}
							uIShareUnits2.Init(list2, "云AI行动列表", 9);
						}
					}
				}
				else
				{
					UITipItem.AddTip("待添加的NPC数量为:" + selUnits.Count, 3f);
				}
			}
			catch (Exception ex2)
			{
				Console.WriteLine(ex2.ToString());
				Console.WriteLine("退出出错");
			}
		}));
		UpdataNPCList();
		if (isFirst)
		{
			UpdataNPCList(1);
			g.ui.CloseUI(new UITypeBase("UISelectUnitNew", (UILayer)0), false);
			Open(unitsID, tiltle, tp, isFirst: false);
		}
		else
		{
			UpdataNPCList();
		}
	}

	public void ChangeGrade(int index)
	{
		try
		{
			if (ModMain.FindList.ContainsKey("Grade"))
			{
				_ = ModMain.FindList["Grade"];
				selGrade = int.Parse(ModMain.FindList["Grade"][index].Item2);
			}
		}
		catch (Exception ex)
		{
			Console.WriteLine(ex.ToString());
			Console.WriteLine("境界选择错误");
		}
	}

	public void ChangeSex(int index)
	{
		try
		{
			if (ModMain.FindList.ContainsKey("Sex"))
			{
				_ = ModMain.FindList["Sex"];
				selSex = int.Parse(ModMain.FindList["Sex"][index].Item2);
			}
		}
		catch (Exception ex)
		{
			Console.WriteLine(ex.ToString());
			Console.WriteLine("性别选择错误");
		}
	}

	public void ChangeSchool(int index)
	{
		try
		{
			if (ModMain.FindList.ContainsKey("School"))
			{
				_ = ModMain.FindList["School"];
				selSchool = ModMain.FindList["School"][index].Item2;
			}
		}
		catch (Exception ex)
		{
			Console.WriteLine(ex.ToString());
			Console.WriteLine("宗门选择错误");
		}
	}

	public void ChangeCharacter(int index)
	{
		try
		{
			if (ModMain.FindList.ContainsKey("Character"))
			{
				_ = ModMain.FindList["Character"];
				selCharacter = int.Parse(ModMain.FindList["Character"][index].Item2);
			}
		}
		catch (Exception ex)
		{
			Console.WriteLine(ex.ToString());
			Console.WriteLine("性格选择错误");
		}
	}

	public void ChangeBeauty(int index)
	{
		try
		{
			if (ModMain.FindList.ContainsKey("Beauty"))
			{
				_ = ModMain.FindList["Beauty"];
				selBeauty = ModMain.FindList["Beauty"][index].Item2;
			}
		}
		catch (Exception ex)
		{
			Console.WriteLine(ex.ToString());
			Console.WriteLine("魅力选择错误");
		}
	}

	public void ChangeRace(int index)
	{
		try
		{
			if (ModMain.FindList.ContainsKey("Race"))
			{
				_ = ModMain.FindList["Race"];
				selRace = int.Parse(ModMain.FindList["Race"][index].Item2);
			}
		}
		catch (Exception ex)
		{
			Console.WriteLine(ex.ToString());
			Console.WriteLine("种族选择错误");
		}
	}

	public void ChangeReltion(int index)
	{
		try
		{
			if (ModMain.FindList.ContainsKey("Reltion"))
			{
				_ = ModMain.FindList["Reltion"];
				selReltion = int.Parse(ModMain.FindList["Reltion"][index].Item2);
			}
		}
		catch (Exception ex)
		{
			Console.WriteLine(ex.ToString());
			Console.WriteLine("关系选择错误");
		}
	}

	public void Open(List<string> unitsID, string tiltle, int tp, bool isFirst = true)
	{
		//IL_0010: Unknown result type (might be due to invalid IL or missing references)
		//IL_001a: Expected O, but got Unknown
		ClassInjector.RegisterTypeInIl2Cpp<UISelectUnitNew>();
		((Component)g.ui.OpenUI(new UITypeBase("UISelectUnitNew", (UILayer)0))).gameObject.AddComponent<UISelectUnitNew>().Init(unitsID, tiltle, tp, isFirst);
	}

	public void UpdataNPCList(int maxCount = -1)
	{
		try
		{
			timerCoroutineUpdata = g.timer.Time(Action.op_Implicit((Action)delegate
			{
				//IL_016a: Unknown result type (might be due to invalid IL or missing references)
				//IL_046f: Unknown result type (might be due to invalid IL or missing references)
				//IL_0475: Unknown result type (might be due to invalid IL or missing references)
				//IL_04cc: Unknown result type (might be due to invalid IL or missing references)
				//IL_04d2: Unknown result type (might be due to invalid IL or missing references)
				int num = 0;
				for (int num2 = goUnitListRoot.transform.GetChildCount() - 1; num2 >= 0; num2--)
				{
					Object.Destroy((Object)(object)((Component)goUnitListRoot.transform.GetChild(num2)).gameObject);
				}
				int num3 = units.Count / prePage + ((units.Count % prePage != 0) ? 1 : 0);
				if (curPage < 1)
				{
					curPage = num3;
				}
				if (curPage > num3)
				{
					curPage = 1;
				}
				int num4 = (curPage - 1) * prePage;
				if (num4 >= units.Count)
				{
					curPage = 1;
					num4 = 0;
				}
				int num5 = ((curPage * prePage < units.Count) ? (curPage * prePage) : units.Count);
				GameObject val = g.res.Load<GameObject>("UI/Item/NPCUnitInfoItem");
				val.transform.localPosition = new Vector3(0f, 0f);
				for (int i = num4; i < num5; i++)
				{
					if (maxCount != -1 && num >= maxCount)
					{
						break;
					}
					bool isOn = false;
					int temp = i;
					if (type == 1)
					{
						if (ModMain.shareUnit.Contains(units[i].data.unitData.unitID))
						{
							isOn = true;
						}
					}
					else if (type == 2)
					{
						if (ModMain.letterUnitList.Contains(units[i].data.unitData.unitID))
						{
							isOn = true;
						}
					}
					else if (type == 3)
					{
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
						if (list.Contains(units[i].data.unitData.unitID))
						{
							isOn = true;
						}
					}
					else if (type != 4 && type == 9)
					{
						List<string> list2 = new List<string>();
						Enumerator<WorldUnitBase> enumerator = g.world.unit.GetUnits(true).GetEnumerator();
						while (enumerator.MoveNext())
						{
							WorldUnitBase current2 = enumerator.Current;
							if (current2.GetLuck(ModMain.mod_ID + 9) != null)
							{
								list2.Add(current2.data.unitData.unitID);
							}
						}
						if (list2.Contains(units[i].data.unitData.unitID))
						{
							isOn = true;
						}
					}
					GameObject val2 = Object.Instantiate<GameObject>(g.res.Load<GameObject>("UI/UnitSelectItem"), goUnitListRoot.transform);
					Transform val3 = val2.transform.Find("Unit");
					GameObject obj = Object.Instantiate<GameObject>(val, ((Component)val3).transform);
					((Object)obj).name = "NPCUnitInfoItem";
					obj.gameObject.SetActive(true);
					UINPCUnitInfoItem component = obj.GetComponent<UINPCUnitInfoItem>();
					if ((Object)(object)component != (Object)null)
					{
						num++;
						val2.SetActive(true);
						component.Init();
						component.InitData(units[i], true, true);
						component.ShowGrade(true);
						items.Add(component);
						((Component)val2.transform.Find("ButtonAdd")).gameObject.AddComponent<UISkyTipEffect>().InitData("直接设置单个NPC", default(Vector3));
						Transform Toggle = val2.transform.Find("Toggle");
						if (type == 5)
						{
							((Component)Toggle).gameObject.SetActive(false);
						}
						((Component)Toggle).gameObject.AddComponent<UISkyTipEffect>().InitData("勾选后可批量设置NPC", default(Vector3));
						if (type == 0)
						{
							((Component)val2.transform.Find("ButtonAdd/Text")).GetComponent<Text>().text = "管理";
						}
						((UnityEventBase)((Component)val2.transform.Find("ButtonAdd")).GetComponent<Button>().onClick).RemoveAllListeners();
						((UnityEvent)((Component)val2.transform.Find("ButtonAdd")).GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
						{
							//IL_0027: Unknown result type (might be due to invalid IL or missing references)
							//IL_0031: Expected O, but got Unknown
							//IL_0275: Unknown result type (might be due to invalid IL or missing references)
							//IL_027f: Expected O, but got Unknown
							//IL_06dd: Unknown result type (might be due to invalid IL or missing references)
							//IL_06e7: Expected O, but got Unknown
							//IL_04fa: Unknown result type (might be due to invalid IL or missing references)
							//IL_0504: Expected O, but got Unknown
							if (type == 0)
							{
								ClassInjector.RegisterTypeInIl2Cpp<UIUnitCMD>();
								UIUnitCMD uIUnitCMD = ((Component)g.ui.OpenUI(new UITypeBase("UIUnitCMD", (UILayer)0))).gameObject.AddComponent<UIUnitCMD>();
								List<string> unitID = new List<string> { units[temp].data.unitData.unitID };
								uIUnitCMD.InitData(unitID);
							}
							else if (!((Component)Toggle).GetComponent<Toggle>().isOn)
							{
								if (type == 1)
								{
									CommandTool.SetUnitChatShareMode(units[temp], 1);
									UITipItem.AddTip("设置成功，NPC姓名：" + units[temp].data.unitData.propertyData.GetName(), 3f);
								}
								else if (type == 2)
								{
									CommandTool.SetUnitLetterMode(units[temp], 1);
									UITipItem.AddTip("设置成功，NPC姓名：" + units[temp].data.unitData.propertyData.GetName(), 3f);
								}
								else if (type == 3)
								{
									if (units[temp].data.unitData.relationData.GetIntim(g.world.playerUnit.GetUnitID()) >= 180)
									{
										DramaFunctionTool.OptionsFunction("addNPCLuck_0_" + (ModMain.mod_ID + 1) + "_-1", new DramaFunctionData(units[temp]));
										UITipItem.AddTip("设置成功，NPC姓名：" + units[temp].data.unitData.propertyData.GetName(), 3f);
									}
									else
									{
										UITipItem.AddTip(units[temp].GetName() + "对你的好感度低于3颗星，自动忽略你的组队请求！", 3f);
									}
								}
								else if (type == 4)
								{
									if ((Object)(object)uIAIGroupCommand != (Object)null && !uIAIGroupCommand.units.Contains(units[temp].data.unitData.unitID))
									{
										uIAIGroupCommand.units.Add(units[temp].data.unitData.unitID);
										UITipItem.AddTip("设置成功，NPC姓名：" + units[temp].data.unitData.propertyData.GetName(), 3f);
									}
								}
								else if (type == 9)
								{
									if (units[temp].data.unitData.relationData.GetIntim(g.world.playerUnit.GetUnitID()) >= 0)
									{
										DramaFunctionTool.OptionsFunction("addNPCLuck_0_" + (ModMain.mod_ID + 9) + "_-1", new DramaFunctionData(units[temp]));
										UITipItem.AddTip("设置成功，NPC姓名：" + units[temp].data.unitData.propertyData.GetName(), 3f);
									}
									else
									{
										UITipItem.AddTip(units[temp].GetName() + "对你的好感度为负数，自动忽略你的云AI设置请求！", 3f);
									}
								}
								((Component)Toggle).GetComponent<Toggle>().isOn = true;
							}
							else
							{
								UITipItem.AddTip("该NPC已设置，请勿重复操作：" + units[temp].data.unitData.propertyData.GetName(), 3f);
							}
							if (type == 5)
							{
								Console.WriteLine("fightSelIndex=" + fightSelIndex);
								if (fightSelIndex >= 0 && fightSelIndex < UIFightDiy.matchUnits.Count)
								{
									if (unitIndex == 1)
									{
										UIFightDiy.matchUnits[fightSelIndex].unitLeft = units[temp].GetUnitID();
									}
									else if (unitIndex == 2)
									{
										UIFightDiy.matchUnits[fightSelIndex].unitRight = units[temp].GetUnitID();
									}
									UIBase uI = g.ui.GetUI(new UITypeBase("UIFightDiy", (UILayer)0));
									if ((Object)(object)uI != (Object)null)
									{
										UIFightDiy component2 = ((Component)uI).gameObject.GetComponent<UIFightDiy>();
										if ((Object)(object)component2 != (Object)null)
										{
											component2.UpdataUI();
										}
										else
										{
											Console.WriteLine("UIFightDiy不存在-1");
										}
									}
									else
									{
										Console.WriteLine("UIFightDiy不存在-2");
									}
									UITipItem.AddTip("对战NPC已完成添加：" + units[temp].data.unitData.propertyData.GetName(), 3f);
									((UnityEvent)ButtonExit.GetComponent<Button>().onClick).Invoke();
								}
							}
						}));
						((Component)Toggle).GetComponent<Toggle>().isOn = isOn;
						((UnityEventBase)((Component)Toggle).GetComponent<Toggle>().onValueChanged).RemoveAllListeners();
						((UnityEvent<bool>)(object)((Component)Toggle).GetComponent<Toggle>().onValueChanged).AddListener(UnityAction<bool>.op_Implicit((Action<bool>)onValueChanged));
					}
					void onValueChanged(bool value)
					{
						if (value)
						{
							selUnits.Add(units[temp].data.unitData.unitID);
						}
						else
						{
							selUnits.Remove(units[temp].data.unitData.unitID);
						}
					}
				}
				((Component)((Component)this).transform.Find("Root/PanelMain/TextCount")).GetComponent<Text>().text = $"NPC总数:{units.Count}  当前页NPC数：{num}";
				((Component)((Component)this).transform.Find("Root/PanelMain/TextState")).GetComponent<Text>().text = $"第 {curPage} 页 / 总 {num3} 页";
				LayoutRebuilder.ForceRebuildLayoutImmediate(((Component)((Component)this).transform.Find("Root/PanelMain/Scroll View")).GetComponent<ScrollRect>().content);
				Canvas.ForceUpdateCanvases();
			}), 0f, false);
		}
		catch (Exception ex)
		{
			Console.WriteLine(ex.ToString());
		}
	}
}


//==================== TYPE MOD_SSCYAI.UISelectUnitNew.<>c__DisplayClass42_0 ====================
// decompile failed: Could not find type definition MOD_SSCYAI.UISelectUnitNew.<>c__DisplayClass42_0 in type system.

//==================== TYPE MOD_SSCYAI.UISelectUnitNew.<>c__DisplayClass51_0 ====================
// decompile failed: Could not find type definition MOD_SSCYAI.UISelectUnitNew.<>c__DisplayClass51_0 in type system.

//==================== TYPE MOD_SSCYAI.UISelectUnitNew.<>c__DisplayClass51_1 ====================
// decompile failed: Could not find type definition MOD_SSCYAI.UISelectUnitNew.<>c__DisplayClass51_1 in type system.

//==================== TYPE MOD_SSCYAI.UISelectUnitNew.<>c__DisplayClass51_2 ====================
// decompile failed: Could not find type definition MOD_SSCYAI.UISelectUnitNew.<>c__DisplayClass51_2 in type system.

