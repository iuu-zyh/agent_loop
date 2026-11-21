//==================== TYPE MOD_SSCYAI.UIShareUnits ====================
using System;
using System.Collections.Generic;
using Il2CppSystem;
using Il2CppSystem.Collections.Generic;
using UnhollowerRuntimeLib;
using UnityEngine;
using UnityEngine.Events;
using UnityEngine.UI;

namespace MOD_SSCYAI;

public class UIShareUnits(IntPtr ptr) : UIBase(ptr)
{
	public GameObject ButtonExit;

	public GameObject ButtonAdd;

	public GameObject ButtonClear;

	public GameObject goUnitListRoot;

	public GameObject goUnitList;

	public GameObject goTiltle;

	public int type;

	public List<WorldUnitBase> units = new List<WorldUnitBase>();

	public List<UINPCUnitInfoItem> items = new List<UINPCUnitInfoItem>();

	public void Init(List<string> unitsID, string tiltle, int tp, bool isFirst = true)
	{
		//IL_0172: Unknown result type (might be due to invalid IL or missing references)
		//IL_0178: Unknown result type (might be due to invalid IL or missing references)
		//IL_01d2: Unknown result type (might be due to invalid IL or missing references)
		//IL_01d8: Unknown result type (might be due to invalid IL or missing references)
		//IL_021a: Unknown result type (might be due to invalid IL or missing references)
		//IL_0225: Expected O, but got Unknown
		goTiltle = ((Component)((Component)this).transform.Find("Root/PanelMain/Text")).gameObject;
		goTiltle.GetComponent<Text>().text = tiltle;
		type = tp;
		ButtonExit = ((Component)((Component)this).transform.Find("Root/PanelMain/ButtonExit")).gameObject;
		ButtonExit.GetComponent<Image>().sprite = SpriteTool.GetSprite("Common", "tuichu");
		((UnityEvent)ButtonExit.GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
		{
			//IL_00c1: Unknown result type (might be due to invalid IL or missing references)
			//IL_00cc: Expected O, but got Unknown
			try
			{
				if (type == 3)
				{
					ModMain.unitTeams.Clear();
					Enumerator<WorldUnitBase> enumerator2 = g.world.unit.GetUnits(true).GetEnumerator();
					while (enumerator2.MoveNext())
					{
						WorldUnitBase current2 = enumerator2.Current;
						if (current2.GetLuck(ModMain.mod_ID + 1) != null)
						{
							ModMain.unitTeams.Add(current2);
						}
					}
				}
				else if (type == 9)
				{
					ModMain.unitTeams.Clear();
					Enumerator<WorldUnitBase> enumerator2 = g.world.unit.GetUnits(true).GetEnumerator();
					while (enumerator2.MoveNext())
					{
						WorldUnitBase current3 = enumerator2.Current;
						if (current3.GetLuck(ModMain.mod_ID + 9) != null)
						{
							ModMain.unitTeams.Add(current3);
						}
					}
				}
				g.ui.CloseUI(new UITypeBase("UIShareUnits", (UILayer)0), false);
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
			foreach (string item in unitsID)
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
		int range = g.world.playerUnit.data.dynUnitData.GetGrade() * 3;
		ButtonAdd = ((Component)((Component)this).transform.Find("Root/PanelMain/ButtonAdd")).gameObject;
		ButtonAdd.AddComponent<UISkyTipEffect>().InitData($"搜索周边{range}格范围内的NPC。", default(Vector3));
		((UnityEvent)ButtonAdd.GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
		{
			//IL_001e: Unknown result type (might be due to invalid IL or missing references)
			//IL_007c: Unknown result type (might be due to invalid IL or missing references)
			//IL_0086: Expected O, but got Unknown
			//IL_00b2: Unknown result type (might be due to invalid IL or missing references)
			//IL_00bd: Expected O, but got Unknown
			try
			{
				List<WorldUnitBase> unitExact = g.world.unit.GetUnitExact(g.world.playerUnit.data.unitData.GetPoint(), range, true, false);
				List<string> list = new List<string>();
				Enumerator<WorldUnitBase> enumerator2 = unitExact.GetEnumerator();
				while (enumerator2.MoveNext())
				{
					WorldUnitBase current2 = enumerator2.Current;
					list.Add(current2.data.unitData.unitID);
				}
				if (list.Count > 0)
				{
					ClassInjector.RegisterTypeInIl2Cpp<UISelectUnitNew>();
					((Component)g.ui.OpenUI(new UITypeBase("UISelectUnitNew", (UILayer)0))).gameObject.AddComponent<UISelectUnitNew>().Init(list, "选择列表", type);
					g.ui.CloseUI(new UITypeBase("UIShareUnits", (UILayer)0), false);
				}
				else
				{
					UITipItem.AddTip($"周边{range}格范围内无NPC。", 3f);
				}
			}
			catch (Exception ex2)
			{
				Console.WriteLine(ex2.ToString());
				Console.WriteLine("添加出错");
			}
		}));
		ButtonClear = ((Component)((Component)this).transform.Find("Root/PanelMain/ButtonClear")).gameObject;
		ButtonClear.AddComponent<UISkyTipEffect>().InitData("一键清空", default(Vector3));
		((UnityEvent)ButtonClear.GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
		{
			try
			{
				g.ui.OpenUI<UICheckPopup>(UIType.CheckPopup).InitData("提示", "是否确定将所有NPC进行移除？", 2, Action.op_Implicit((Action)delegate
				{
					try
					{
						foreach (WorldUnitBase unit2 in units)
						{
							if (type == 1)
							{
								CommandTool.SetUnitChatShareMode(unit2);
							}
							else if (type == 2)
							{
								CommandTool.SetUnitLetterMode(unit2);
							}
							else if (type == 3)
							{
								DramaFunctionTool.OptionsFunction("delNPCLuck_" + unit2.data.unitData.unitID + "_" + (ModMain.mod_ID + 1), (DramaFunctionData)null);
							}
							else if (type == 9)
							{
								DramaFunctionTool.OptionsFunction("delNPCLuck_" + unit2.data.unitData.unitID + "_" + (ModMain.mod_ID + 9), (DramaFunctionData)null);
							}
						}
						units.Clear();
						UpdataNPCList();
					}
					catch (Exception ex3)
					{
						Console.WriteLine(ex3.ToString());
					}
				}), (Action)null);
			}
			catch (Exception ex2)
			{
				Console.WriteLine(ex2.ToString());
				Console.WriteLine("清空出错");
			}
		}));
		UpdataNPCList();
		if (isFirst)
		{
			g.ui.CloseUI(new UITypeBase("UIShareUnits", (UILayer)0), false);
			Open(unitsID, tiltle, tp, isFirst: false);
		}
	}

	public void Open(List<string> unitsID, string tiltle, int tp, bool isFirst = true)
	{
		//IL_0010: Unknown result type (might be due to invalid IL or missing references)
		//IL_001a: Expected O, but got Unknown
		ClassInjector.RegisterTypeInIl2Cpp<UIShareUnits>();
		((Component)g.ui.OpenUI(new UITypeBase("UIShareUnits", (UILayer)0))).gameObject.AddComponent<UIShareUnits>().Init(unitsID, tiltle, tp, isFirst);
	}

	public void UpdataNPCList()
	{
		//IL_0058: Unknown result type (might be due to invalid IL or missing references)
		try
		{
			for (int num = goUnitListRoot.transform.GetChildCount() - 1; num >= 0; num--)
			{
				Object.Destroy((Object)(object)((Component)goUnitListRoot.transform.GetChild(num)).gameObject);
			}
			GameObject val = g.res.Load<GameObject>("UI/Item/NPCUnitInfoItem");
			val.transform.localPosition = new Vector3(0f, 0f);
			foreach (WorldUnitBase unit in units)
			{
				GameObject go = Object.Instantiate<GameObject>(g.res.Load<GameObject>("UI/UnitItemNormal"), goUnitListRoot.transform);
				Transform val2 = go.transform.Find("Unit");
				GameObject val3 = Object.Instantiate<GameObject>(val, ((Component)val2).transform);
				((Object)val3).name = "NPCUnitInfoItem";
				val3.gameObject.SetActive(true);
				UINPCUnitInfoItem uINPCUnitInfoItem = val3.GetComponent<UINPCUnitInfoItem>();
				if (!((Object)(object)uINPCUnitInfoItem != (Object)null))
				{
					continue;
				}
				go.SetActive(true);
				uINPCUnitInfoItem.Init();
				uINPCUnitInfoItem.InitData(unit, true, true);
				uINPCUnitInfoItem.ShowGrade(true);
				items.Add(uINPCUnitInfoItem);
				((UnityEventBase)((Component)go.transform.Find("ButtonRemove")).GetComponent<Button>().onClick).RemoveAllListeners();
				((UnityEvent)((Component)go.transform.Find("ButtonRemove")).GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
				{
					g.ui.OpenUI<UICheckPopup>(UIType.CheckPopup).InitData("提示", "是否确定将 " + unit.data.unitData.propertyData.GetName() + " 移除？", 2, Action.op_Implicit((Action)delegate
					{
						try
						{
							if (type == 1)
							{
								CommandTool.SetUnitChatShareMode(unit);
							}
							else if (type == 2)
							{
								CommandTool.SetUnitLetterMode(unit);
							}
							else if (type == 3)
							{
								DramaFunctionTool.OptionsFunction("delNPCLuck_" + unit.data.unitData.unitID + "_" + (ModMain.mod_ID + 1), (DramaFunctionData)null);
							}
							else if (type == 9)
							{
								DramaFunctionTool.OptionsFunction("delNPCLuck_" + unit.data.unitData.unitID + "_" + (ModMain.mod_ID + 9), (DramaFunctionData)null);
							}
							items.Remove(uINPCUnitInfoItem);
							Object.Destroy((Object)(object)go);
						}
						catch (Exception ex2)
						{
							Console.WriteLine(ex2.ToString());
						}
					}), (Action)null);
				}));
			}
			LayoutRebuilder.ForceRebuildLayoutImmediate(((Component)((Component)this).transform.Find("Root/PanelMain/Scroll View")).GetComponent<ScrollRect>().content);
			Canvas.ForceUpdateCanvases();
		}
		catch (Exception ex)
		{
			Console.WriteLine(ex.ToString());
		}
	}
}


//==================== TYPE MOD_SSCYAI.UIShareUnits.<>c__DisplayClass10_0 ====================
// decompile failed: Could not find type definition MOD_SSCYAI.UIShareUnits.<>c__DisplayClass10_0 in type system.

//==================== TYPE MOD_SSCYAI.UIShareUnits.<>c__DisplayClass12_0 ====================
// decompile failed: Could not find type definition MOD_SSCYAI.UIShareUnits.<>c__DisplayClass12_0 in type system.

