//==================== TYPE MOD_SSCYAI.UINPCAISet2 ====================
using System;
using Il2CppSystem;
using Il2CppSystem.Collections.Generic;
using UnityEngine;
using UnityEngine.Events;
using UnityEngine.UI;

namespace MOD_SSCYAI;

public class UINPCAISet2 : UIBase
{
	public GameObject ButtonExit;

	public GameObject ButtonSave;

	public GameObject ButtonReset;

	public GameObject T1_InputFieldGJ;

	public GameObject T1_InputFieldFY;

	public GameObject T1_InputFieldTL;

	public GameObject T1_InputFieldMS;

	public GameObject T1_InputFieldHX;

	public GameObject T1_InputFieldHX2;

	public GameObject T1_InputFieldWL;

	public GameObject T1_InputFieldPT;

	public GameObject T1_InputFieldJY;

	public GameObject T1_InputFieldBS;

	public GameObject T2_InputFieldGJ;

	public GameObject T2_InputFieldFY;

	public GameObject T2_InputFieldTL;

	public GameObject T2_InputFieldLL;

	public GameObject T2_InputFieldSD;

	public GameObject T2_InputFieldHX;

	public GameObject T2_InputFieldHX2;

	public GameObject T2_InputFieldHXBL;

	public GameObject T2_InputFieldHXBL2;

	public GameObject T2_InputFieldQLL;

	public GameObject T2_InputFieldWLMS;

	public GameObject T2_InputFieldMFMS;

	public GameObject T3_InputFieldJYBS;

	public GameObject T3_ToggleShowLuck;

	public UINPCAISet2(IntPtr ptr)
		: base(ptr)
	{
	}

	public void InitData()
	{
		try
		{
			ButtonExit = ((Component)((Component)this).transform.Find("Root/PanelMain/ButtonExit")).gameObject;
			ButtonSave = ((Component)((Component)this).transform.Find("Root/PanelMain/ButtonSave")).gameObject;
			ButtonReset = ((Component)((Component)this).transform.Find("Root/PanelMain/ButtonReset")).gameObject;
			T1_InputFieldGJ = ((Component)((Component)this).transform.Find("Root/PanelMain/PanelT1/Input1/InputFieldGJ")).gameObject;
			T1_InputFieldFY = ((Component)((Component)this).transform.Find("Root/PanelMain/PanelT1/Input1/InputFieldFY")).gameObject;
			T1_InputFieldTL = ((Component)((Component)this).transform.Find("Root/PanelMain/PanelT1/Input1/InputFieldTL")).gameObject;
			T1_InputFieldMS = ((Component)((Component)this).transform.Find("Root/PanelMain/PanelT1/Input1/InputFieldMS")).gameObject;
			T1_InputFieldHX = ((Component)((Component)this).transform.Find("Root/PanelMain/PanelT1/Input1/InputFieldHX")).gameObject;
			T1_InputFieldHX2 = ((Component)((Component)this).transform.Find("Root/PanelMain/PanelT1/Input1/InputFieldHX2")).gameObject;
			T1_InputFieldWL = ((Component)((Component)this).transform.Find("Root/PanelMain/PanelT1/Input1/InputFieldWL")).gameObject;
			T1_InputFieldPT = ((Component)((Component)this).transform.Find("Root/PanelMain/PanelT1/Input2/InputFieldPT")).gameObject;
			T1_InputFieldJY = ((Component)((Component)this).transform.Find("Root/PanelMain/PanelT1/Input2/InputFieldJY")).gameObject;
			T1_InputFieldBS = ((Component)((Component)this).transform.Find("Root/PanelMain/PanelT1/Input2/InputFieldBS")).gameObject;
			T2_InputFieldGJ = ((Component)((Component)this).transform.Find("Root/PanelMain/PanelT2/Input1/InputFieldGJ")).gameObject;
			T2_InputFieldFY = ((Component)((Component)this).transform.Find("Root/PanelMain/PanelT2/Input1/InputFieldFY")).gameObject;
			T2_InputFieldTL = ((Component)((Component)this).transform.Find("Root/PanelMain/PanelT2/Input1/InputFieldTL")).gameObject;
			T2_InputFieldLL = ((Component)((Component)this).transform.Find("Root/PanelMain/PanelT2/Input1/InputFieldLL")).gameObject;
			T2_InputFieldSD = ((Component)((Component)this).transform.Find("Root/PanelMain/PanelT2/Input1/InputFieldSD")).gameObject;
			T2_InputFieldHX = ((Component)((Component)this).transform.Find("Root/PanelMain/PanelT2/Input1/InputFieldHX")).gameObject;
			T2_InputFieldHX2 = ((Component)((Component)this).transform.Find("Root/PanelMain/PanelT2/Input1/InputFieldHX2")).gameObject;
			T2_InputFieldHXBL = ((Component)((Component)this).transform.Find("Root/PanelMain/PanelT2/Input1/InputFieldHXBL")).gameObject;
			T2_InputFieldHXBL2 = ((Component)((Component)this).transform.Find("Root/PanelMain/PanelT2/Input1/InputFieldHXBL2")).gameObject;
			T2_InputFieldQLL = ((Component)((Component)this).transform.Find("Root/PanelMain/PanelT2/Input1/InputFieldQLL")).gameObject;
			T2_InputFieldWLMS = ((Component)((Component)this).transform.Find("Root/PanelMain/PanelT2/Input1/InputFieldWLMS")).gameObject;
			T2_InputFieldMFMS = ((Component)((Component)this).transform.Find("Root/PanelMain/PanelT2/Input1/InputFieldMFMS")).gameObject;
			T3_InputFieldJYBS = ((Component)((Component)this).transform.Find("Root/PanelMain/PanelT3/Input1/InputFieldJYBS")).gameObject;
			T3_ToggleShowLuck = ((Component)((Component)this).transform.Find("Root/PanelMain/PanelT3/Input1/ToggleShowLuck")).gameObject;
		}
		catch (Exception ex)
		{
			Console.WriteLine(ex.ToString());
		}
		ButtonExit.GetComponent<Image>().sprite = SpriteTool.GetSprite("Common", "tuichu");
		((UnityEvent)ButtonExit.GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
		{
			//IL_000b: Unknown result type (might be due to invalid IL or missing references)
			//IL_0016: Expected O, but got Unknown
			try
			{
				g.ui.CloseUI(new UITypeBase("UINPCAISet2", (UILayer)0), false);
			}
			catch (Exception ex2)
			{
				Console.WriteLine(ex2.ToString());
				Console.WriteLine("V出错");
			}
		}));
		((UnityEvent)ButtonSave.GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
		{
			try
			{
				ModMain.confInofFile.confInof.confDiySet.UnitAtk = float.Parse(T1_InputFieldGJ.GetComponent<InputField>().text);
				ModMain.confInofFile.confInof.confDiySet.UnitDef = float.Parse(T1_InputFieldFY.GetComponent<InputField>().text);
				ModMain.confInofFile.confInof.confDiySet.UnitHp = float.Parse(T1_InputFieldTL.GetComponent<InputField>().text);
				ModMain.confInofFile.confInof.confDiySet.Pmfr = float.Parse(T1_InputFieldMS.GetComponent<InputField>().text);
				ModMain.confInofFile.confInof.confDiySet.Crit = float.Parse(T1_InputFieldHX.GetComponent<InputField>().text);
				ModMain.confInofFile.confInof.confDiySet.Guard = float.Parse(T1_InputFieldHX2.GetComponent<InputField>().text);
				ModMain.confInofFile.confInof.confDiySet.SkillDmg = float.Parse(T1_InputFieldWL.GetComponent<InputField>().text);
				ModMain.confInofFile.confInof.confDiySet.Type1 = float.Parse(T1_InputFieldPT.GetComponent<InputField>().text);
				ModMain.confInofFile.confInof.confDiySet.Type2 = float.Parse(T1_InputFieldJY.GetComponent<InputField>().text);
				ModMain.confInofFile.confInof.confDiySet.Type3 = float.Parse(T1_InputFieldBS.GetComponent<InputField>().text);
				ModMain.confInofFile.confInof.confDiySet.npcAtk = int.Parse(T2_InputFieldGJ.GetComponent<InputField>().text);
				ModMain.confInofFile.confInof.confDiySet.npcDef = int.Parse(T2_InputFieldFY.GetComponent<InputField>().text);
				ModMain.confInofFile.confInof.confDiySet.npcMaxHp = int.Parse(T2_InputFieldTL.GetComponent<InputField>().text);
				ModMain.confInofFile.confInof.confDiySet.npcMaxMp = int.Parse(T2_InputFieldLL.GetComponent<InputField>().text);
				ModMain.confInofFile.confInof.confDiySet.npcMaxSp = int.Parse(T2_InputFieldSD.GetComponent<InputField>().text);
				ModMain.confInofFile.confInof.confDiySet.npcCrit = int.Parse(T2_InputFieldHX.GetComponent<InputField>().text);
				ModMain.confInofFile.confInof.confDiySet.npcGuard = int.Parse(T2_InputFieldHX2.GetComponent<InputField>().text);
				ModMain.confInofFile.confInof.confDiySet.npcCritV = int.Parse(T2_InputFieldHXBL.GetComponent<InputField>().text);
				ModMain.confInofFile.confInof.confDiySet.npcGuardV = int.Parse(T2_InputFieldHXBL2.GetComponent<InputField>().text);
				ModMain.confInofFile.confInof.confDiySet.npcBasMgAll = int.Parse(T2_InputFieldQLL.GetComponent<InputField>().text);
				ModMain.confInofFile.confInof.confDiySet.npcPfr = int.Parse(T2_InputFieldWLMS.GetComponent<InputField>().text);
				ModMain.confInofFile.confInof.confDiySet.npcMfr = int.Parse(T2_InputFieldMFMS.GetComponent<InputField>().text);
				ModMain.confInofFile.confInof.confDiySet.GradeExp = float.Parse(T3_InputFieldJYBS.GetComponent<InputField>().text);
				ModMain.confInofFile.confInof.confDiySet.showLuck = T3_ToggleShowLuck.GetComponent<Toggle>().isOn;
				ModMain.SstValue();
				g.timer.Time(Action.op_Implicit((Action)delegate
				{
					//IL_006e: Unknown result type (might be due to invalid IL or missing references)
					//IL_0078: Expected O, but got Unknown
					//IL_0097: Unknown result type (might be due to invalid IL or missing references)
					//IL_00a1: Expected O, but got Unknown
					if (g.world.isIntoWorld)
					{
						List<WorldUnitBase> units = g.world.unit.GetUnits(true);
						units.Remove(g.world.playerUnit);
						Enumerator<WorldUnitBase> enumerator = units.GetEnumerator();
						while (enumerator.MoveNext())
						{
							WorldUnitBase current = enumerator.Current;
							DramaFunctionTool.OptionsFunction("delNPCLuck_" + current.data.unitData.unitID + "_" + (ModMain.mod_ID + 2), new DramaFunctionData(current));
							DramaFunctionTool.OptionsFunction("addNPCLuck_0_" + (ModMain.mod_ID + 2) + "_-1", new DramaFunctionData(current));
						}
						Console.WriteLine("刷新气运完成");
					}
				}), 0f, false);
				ModMain.confInofFile.SaveConf();
				UITipItem.AddTip("数据更新成功", 3f);
			}
			catch (Exception ex2)
			{
				Console.WriteLine(ex2.ToString());
				Console.WriteLine("V出错");
			}
		}));
		((UnityEvent)ButtonReset.GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
		{
			try
			{
				string text = "1.0";
				string text2 = "0";
				T1_InputFieldGJ.GetComponent<InputField>().text = text;
				T1_InputFieldFY.GetComponent<InputField>().text = text;
				T1_InputFieldTL.GetComponent<InputField>().text = text;
				T1_InputFieldMS.GetComponent<InputField>().text = text;
				T1_InputFieldHX.GetComponent<InputField>().text = text;
				T1_InputFieldHX2.GetComponent<InputField>().text = text;
				T1_InputFieldWL.GetComponent<InputField>().text = text;
				T1_InputFieldPT.GetComponent<InputField>().text = text;
				T1_InputFieldJY.GetComponent<InputField>().text = text;
				T1_InputFieldBS.GetComponent<InputField>().text = text;
				T2_InputFieldGJ.GetComponent<InputField>().text = text2;
				T2_InputFieldFY.GetComponent<InputField>().text = text2;
				T2_InputFieldTL.GetComponent<InputField>().text = text2;
				T2_InputFieldLL.GetComponent<InputField>().text = text2;
				T2_InputFieldSD.GetComponent<InputField>().text = text2;
				T2_InputFieldHX.GetComponent<InputField>().text = text2;
				T2_InputFieldHX2.GetComponent<InputField>().text = text2;
				T2_InputFieldHXBL.GetComponent<InputField>().text = text2;
				T2_InputFieldHXBL2.GetComponent<InputField>().text = text2;
				T2_InputFieldQLL.GetComponent<InputField>().text = text2;
				T2_InputFieldWLMS.GetComponent<InputField>().text = text2;
				T2_InputFieldMFMS.GetComponent<InputField>().text = text2;
				T3_InputFieldJYBS.GetComponent<InputField>().text = text;
				T3_ToggleShowLuck.GetComponent<Toggle>().isOn = true;
			}
			catch (Exception ex2)
			{
				Console.WriteLine(ex2.ToString());
				Console.WriteLine("V出错");
			}
		}));
		UpdataUi();
	}

	public void UpdataUi()
	{
		try
		{
			T1_InputFieldGJ.GetComponent<InputField>().text = ModMain.confInofFile.confInof.confDiySet.UnitAtk.ToString();
			T1_InputFieldFY.GetComponent<InputField>().text = ModMain.confInofFile.confInof.confDiySet.UnitDef.ToString();
			T1_InputFieldTL.GetComponent<InputField>().text = ModMain.confInofFile.confInof.confDiySet.UnitHp.ToString();
			T1_InputFieldMS.GetComponent<InputField>().text = ModMain.confInofFile.confInof.confDiySet.Pmfr.ToString();
			T1_InputFieldHX.GetComponent<InputField>().text = ModMain.confInofFile.confInof.confDiySet.Crit.ToString();
			T1_InputFieldHX2.GetComponent<InputField>().text = ModMain.confInofFile.confInof.confDiySet.Guard.ToString();
			T1_InputFieldWL.GetComponent<InputField>().text = ModMain.confInofFile.confInof.confDiySet.SkillDmg.ToString();
			T1_InputFieldPT.GetComponent<InputField>().text = ModMain.confInofFile.confInof.confDiySet.Type1.ToString();
			T1_InputFieldJY.GetComponent<InputField>().text = ModMain.confInofFile.confInof.confDiySet.Type2.ToString();
			T1_InputFieldBS.GetComponent<InputField>().text = ModMain.confInofFile.confInof.confDiySet.Type3.ToString();
			T2_InputFieldGJ.GetComponent<InputField>().text = ModMain.confInofFile.confInof.confDiySet.npcAtk.ToString();
			T2_InputFieldFY.GetComponent<InputField>().text = ModMain.confInofFile.confInof.confDiySet.npcDef.ToString();
			T2_InputFieldTL.GetComponent<InputField>().text = ModMain.confInofFile.confInof.confDiySet.npcMaxHp.ToString();
			T2_InputFieldLL.GetComponent<InputField>().text = ModMain.confInofFile.confInof.confDiySet.npcMaxMp.ToString();
			T2_InputFieldSD.GetComponent<InputField>().text = ModMain.confInofFile.confInof.confDiySet.npcMaxSp.ToString();
			T2_InputFieldHX.GetComponent<InputField>().text = ModMain.confInofFile.confInof.confDiySet.npcCrit.ToString();
			T2_InputFieldHX2.GetComponent<InputField>().text = ModMain.confInofFile.confInof.confDiySet.npcGuard.ToString();
			T2_InputFieldHXBL.GetComponent<InputField>().text = ModMain.confInofFile.confInof.confDiySet.npcCritV.ToString();
			T2_InputFieldHXBL2.GetComponent<InputField>().text = ModMain.confInofFile.confInof.confDiySet.npcGuardV.ToString();
			T2_InputFieldQLL.GetComponent<InputField>().text = ModMain.confInofFile.confInof.confDiySet.npcBasMgAll.ToString();
			T2_InputFieldWLMS.GetComponent<InputField>().text = ModMain.confInofFile.confInof.confDiySet.npcPfr.ToString();
			T2_InputFieldMFMS.GetComponent<InputField>().text = ModMain.confInofFile.confInof.confDiySet.npcMfr.ToString();
			T3_InputFieldJYBS.GetComponent<InputField>().text = ModMain.confInofFile.confInof.confDiySet.GradeExp.ToString();
			T3_ToggleShowLuck.GetComponent<Toggle>().isOn = ModMain.confInofFile.confInof.confDiySet.showLuck;
		}
		catch (Exception ex)
		{
			Console.WriteLine(ex.ToString());
		}
	}
}


//==================== TYPE MOD_SSCYAI.UINPCAISet ====================
using System;
using System.Runtime.CompilerServices;
using Il2CppSystem;
using Il2CppSystem.Collections.Generic;
using UnhollowerRuntimeLib;
using UnityEngine;
using UnityEngine.Events;
using UnityEngine.UI;

namespace MOD_SSCYAI;

public class UINPCAISet : UIBase
{
	[Serializable]
	[CompilerGenerated]
	private sealed class <>c
	{
		public static readonly <>c <>9 = new <>c();

		public static Action<bool> <>9__56_0;

		public static Action<bool> <>9__56_1;

		public static Action<bool> <>9__56_2;

		public static Action<bool> <>9__56_3;

		public static Action<bool> <>9__56_4;

		public static Action<bool> <>9__56_5;

		public static Action<bool> <>9__56_6;

		public static Action<bool> <>9__56_7;

		public static Action<bool> <>9__56_8;

		public static Action<bool> <>9__56_9;

		public static Action<bool> <>9__56_10;

		public static Action<bool> <>9__56_11;

		public static Action<bool> <>9__56_12;

		public static Action<bool> <>9__56_13;

		public static Action<bool> <>9__56_14;

		public static Action<bool> <>9__56_15;

		public static Action<bool> <>9__56_16;

		public static Action <>9__56_17;

		public static Action <>9__56_18;

		public static Action <>9__56_24;

		public static Action <>9__56_25;

		public static Action <>9__57_0;

		public static Action <>9__57_1;

		public static Action <>9__57_2;

		public static Action <>9__57_3;

		public static Action <>9__57_4;

		public static Action <>9__57_5;

		public static Action <>9__58_0;

		public static Action <>9__58_1;

		public static Action <>9__58_2;

		public static Action <>9__58_3;

		public static Action <>9__58_4;

		public static Action <>9__58_5;

		public static Action <>9__59_0;

		public static Action <>9__59_1;

		public static Action <>9__59_2;

		public static Action <>9__59_3;

		public static Action <>9__60_0;

		public static Action <>9__60_1;

		public static Action <>9__60_2;

		public static Action <>9__60_3;

		public static Action <>9__60_4;

		public static Action <>9__60_5;

		public static Action <>9__60_6;

		public static Action <>9__60_11;

		public static Action <>9__60_12;

		public static Action <>9__60_13;

		public static Action <>9__60_7;

		public static Action <>9__60_8;

		public static Action <>9__60_9;

		public static Action <>9__61_0;

		public static Action <>9__61_1;

		public static Action <>9__61_2;

		public static Action <>9__61_3;

		public static Action <>9__61_4;

		public static Action <>9__61_5;

		public static Action <>9__61_6;

		public static Action <>9__61_7;

		public static Action <>9__63_0;

		internal void <InitData>b__56_0(bool value)
		{
			ModMain.confInofFile.confInof.isCloseNpcAllAction = value;
		}

		internal void <InitData>b__56_1(bool value)
		{
			ModMain.confInofFile.confInof.isNPCAutoGrage = value;
		}

		internal void <InitData>b__56_2(bool value)
		{
			ModMain.confInofFile.confInof.isNPCAutoAttackUp = value;
		}

		internal void <InitData>b__56_3(bool value)
		{
			ModMain.confInofFile.confInof.isPlayAutoAttackUp = value;
		}

		internal void <InitData>b__56_4(bool value)
		{
			ModMain.confInofFile.confInof.isChangeDropDis = value;
		}

		internal void <InitData>b__56_5(bool value)
		{
			ModMain.confInofFile.confInof.confDiySet.isBeKillAllDrop = value;
		}

		internal void <InitData>b__56_6(bool value)
		{
			ModMain.confInofFile.confInof.confDiySet.isActionSuccess = value;
		}

		internal void <InitData>b__56_7(bool value)
		{
			ModMain.confInofFile.confInof.confDiySet.isAutoPostTask = value;
		}

		internal void <InitData>b__56_8(bool value)
		{
			ModMain.confInofFile.confInof.NPCTeam = value;
		}

		internal void <InitData>b__56_9(bool value)
		{
			ModMain.confInofFile.confInof.NPCHelp = value;
		}

		internal void <InitData>b__56_10(bool value)
		{
			ModMain.confInofFile.confInof.NPCAutoFight = value;
		}

		internal void <InitData>b__56_11(bool value)
		{
			ModMain.confInofFile.confInof.AutoUnitLog = value;
		}

		internal void <InitData>b__56_12(bool value)
		{
			ModMain.confInofFile.confInof.SkillExt = value;
			UITipItem.AddTip("苦海异象开启状态已更新，进入战斗界面或重新进入大地图时将自动更新。", 3f);
		}

		internal void <InitData>b__56_13(bool value)
		{
			//IL_00cc: Unknown result type (might be due to invalid IL or missing references)
			//IL_00d6: Expected O, but got Unknown
			//IL_00ec: Unknown result type (might be due to invalid IL or missing references)
			//IL_00f6: Expected O, but got Unknown
			//IL_003a: Unknown result type (might be due to invalid IL or missing references)
			//IL_0044: Expected O, but got Unknown
			//IL_005a: Unknown result type (might be due to invalid IL or missing references)
			//IL_0064: Expected O, but got Unknown
			ModMain.confInofFile.confInof.isSoulDevourSworduP = value;
			if (ModMain.confInofFile.confInof.isSoulDevourSworduP)
			{
				((ConfSoulDevourSwordParamerBase)g.conf.soulDevourSwordParamer)._swordDevourInterval = new ConfSoulDevourSwordParamerItem(17, "swordDevourInterval", "999999");
				((ConfSoulDevourSwordParamerBase)g.conf.soulDevourSwordParamer)._swordTargetHideRange = new ConfSoulDevourSwordParamerItem(24, "swordTargetHideRange", "0");
				List<ConfSoulDevourSwordDevourItem> allConfList = ((ConfSoulDevourSwordDevourBase)g.conf.soulDevourSwordDevour)._allConfList;
				for (int i = 0; i < allConfList.Count; i++)
				{
					allConfList[i].devourCD = 0;
				}
				g.world.soulDevourSword.data.nextDevourMonth = g.world.run.roundMonth;
			}
			else
			{
				((ConfSoulDevourSwordParamerBase)g.conf.soulDevourSwordParamer)._swordDevourInterval = new ConfSoulDevourSwordParamerItem(17, "swordDevourInterval", "24");
				((ConfSoulDevourSwordParamerBase)g.conf.soulDevourSwordParamer)._swordTargetHideRange = new ConfSoulDevourSwordParamerItem(24, "swordTargetHideRange", "5");
				List<ConfSoulDevourSwordDevourItem> allConfList2 = ((ConfSoulDevourSwordDevourBase)g.conf.soulDevourSwordDevour)._allConfList;
				for (int j = 0; j < allConfList2.Count; j++)
				{
					allConfList2[j].devourCD = 3;
				}
			}
		}

		internal void <InitData>b__56_14(bool value)
		{
			ModMain.confInofFile.confInof.confDiySet.isAutoSendGradePropInSchool = value;
		}

		internal void <InitData>b__56_15(bool value)
		{
			ModMain.confInofFile.confInof.confDiySet.isOpenUnitDieTips = value;
		}

		internal void <InitData>b__56_16(bool value)
		{
			ModMain.confInofFile.confInof.isJudgeInti = value;
		}

		internal void <InitData>b__56_17()
		{
			//IL_000b: Unknown result type (might be due to invalid IL or missing references)
			//IL_0016: Expected O, but got Unknown
			try
			{
				g.ui.CloseUI(new UITypeBase("UINPCAISet", (UILayer)0), false);
				ModMain.confInofFile.SaveConf();
			}
			catch (Exception ex)
			{
				Console.WriteLine(ex.ToString());
				Console.WriteLine("V出错");
			}
		}

		internal void <InitData>b__56_18()
		{
			try
			{
				Application.OpenURL(ModMain.confInofFile.pathFile);
			}
			catch (Exception ex)
			{
				Console.WriteLine(ex.ToString());
				Console.WriteLine("V出错");
			}
		}

		internal void <InitData>b__56_24()
		{
			//IL_0010: Unknown result type (might be due to invalid IL or missing references)
			//IL_001a: Expected O, but got Unknown
			try
			{
				ClassInjector.RegisterTypeInIl2Cpp<UINPCAISet2>();
				((Component)g.ui.OpenUI(new UITypeBase("UINPCAISet2", (UILayer)0))).gameObject.AddComponent<UINPCAISet2>().InitData();
			}
			catch (Exception ex)
			{
				Console.WriteLine(ex.ToString());
				Console.WriteLine("V出错");
			}
		}

		internal void <InitData>b__56_25()
		{
			try
			{
				string text = "NPC-AI功能迁移配置参数说明：\r\n1.一键关闭所有行动：NPC行动的总开关，勾选后所有NPC停止本NPC-AI模块的所有行动。\r\n2.打开NPC境界自动突破和打开NPC功法自动升级：打开后，NPC每月境界和功法有概率自动成长及突破。\r\n3.打开队友境界自动突破和打开队友功法自动升级：打开后，组队的NPC境界和功法每月有概率自动成长及提升。\r\n4.打开玩家功法自动升级:打开后，玩家战斗胜利后自身功法有几率自动成长及提升，概率可调。\r\n5.打开NPC境界影响概率开关:打开后，NPC境界突破和功法成长的最终几率会受玩家境界和自身境界的影响。详情参考最终概率计算公式。\r\n6.打开NPC行动亲密度判定:打开后，处于亲密度上下限之间的NPC不会行动，如上下限为-1 至1，则陌生人不会行动，仇人和亲友都会正常行动。\r\n7.私人对话按钮需要好友关系:打开后，仅亲密度大于等于60的NPC会出现私人对话按钮。\r\n8.无条件大能传功:打开后，显示大能传功按钮。对方处于大境界待突破状态。\r\n9.个性化需求常见配置：开启真爱模式后，即使npc已经结婚、有道侣，还是会主动结缘，容易出轨。开启后宫模式后，道侣没有达到上限，就会一直主动结缘，不开启，默认保持一个道侣。\r\n";
				g.ui.OpenUI<UITextInfoLong>(UIType.TextInfoLong).InitData("提示", text, "", (Action)null, false);
			}
			catch (Exception ex)
			{
				Console.WriteLine(ex.ToString());
				Console.WriteLine("V出错");
			}
		}

		internal void <OtherCMD>b__57_0()
		{
			//IL_001e: Unknown result type (might be due to invalid IL or missing references)
			g.data.map.OpenMask(g.world.playerUnit.data.unitData.GetPoint(), 9999, true);
			UITipItem.AddTip("全图已开启", 3f);
		}

		internal void <OtherCMD>b__57_1()
		{
		}

		internal void <OtherCMD>b__57_2()
		{
		}

		internal void <OtherCMD>b__57_3()
		{
		}

		internal void <OtherCMD>b__57_4()
		{
		}

		internal void <OtherCMD>b__57_5()
		{
		}

		internal void <LevelCMD>b__58_0()
		{
			//IL_000f: Unknown result type (might be due to invalid IL or missing references)
			//IL_0015: Expected I4, but got Unknown
			UITipItem.AddTip("游戏难度修改成功，原游戏难度为:" + (int)g.data.world.gameLevel + "，新的游戏难度为:普通(1级)", 3f);
			g.data.world.gameLevel = (GameLevelType)1;
		}

		internal void <LevelCMD>b__58_1()
		{
			//IL_000f: Unknown result type (might be due to invalid IL or missing references)
			//IL_0015: Expected I4, but got Unknown
			UITipItem.AddTip("游戏难度修改成功，原游戏难度为:" + (int)g.data.world.gameLevel + "，新的游戏难度为:困难(2级)", 3f);
			g.data.world.gameLevel = (GameLevelType)2;
		}

		internal void <LevelCMD>b__58_2()
		{
			//IL_000f: Unknown result type (might be due to invalid IL or missing references)
			//IL_0015: Expected I4, but got Unknown
			UITipItem.AddTip("游戏难度修改成功，原游戏难度为:" + (int)g.data.world.gameLevel + "，新的游戏难度为:极难(3级)", 3f);
			g.data.world.gameLevel = (GameLevelType)3;
		}

		internal void <LevelCMD>b__58_3()
		{
			//IL_000f: Unknown result type (might be due to invalid IL or missing references)
			//IL_0015: Expected I4, but got Unknown
			UITipItem.AddTip("游戏难度修改成功，原游戏难度为:" + (int)g.data.world.gameLevel + "，新的游戏难度为:地狱(4级)", 3f);
			g.data.world.gameLevel = (GameLevelType)4;
		}

		internal void <LevelCMD>b__58_4()
		{
			//IL_000f: Unknown result type (might be due to invalid IL or missing references)
			//IL_0015: Expected I4, but got Unknown
			UITipItem.AddTip("游戏难度修改成功，原游戏难度为:" + (int)g.data.world.gameLevel + "，新的游戏难度为:洪荒(5级)", 3f);
			g.data.world.gameLevel = (GameLevelType)5;
		}

		internal void <LevelCMD>b__58_5()
		{
		}

		internal void <LHJYCMD>b__59_0()
		{
			//IL_0034: Unknown result type (might be due to invalid IL or missing references)
			//IL_003e: Expected O, but got Unknown
			if (g.world.playerUnit != null)
			{
				if (g.world.playerUnit.GetLuck(2806) == null)
				{
					DramaFunctionTool.OptionsFunction("addNPCLuck_0_2806_-1", new DramaFunctionData(g.world.playerUnit));
					UITipItem.AddTip(g.world.playerUnit.data.unitData.propertyData.GetName() + "添加莲花剑意气运成功。", 3f);
				}
				else
				{
					UITipItem.AddTip(g.world.playerUnit.data.unitData.propertyData.GetName() + "已拥有该气运", 3f);
				}
			}
		}

		internal void <LHJYCMD>b__59_1()
		{
			if (g.world.playerUnit != null)
			{
				if (g.world.lotusSword != null)
				{
					g.world.lotusSword.LotusSeedGrow(g.world.playerUnit);
					UITipItem.AddTip(g.world.playerUnit.data.unitData.propertyData.GetName() + "的莲花种子成长了", 3f);
				}
				else
				{
					UITipItem.AddTip(g.world.playerUnit.data.unitData.propertyData.GetName() + "莲花种子不存在", 3f);
				}
			}
			else
			{
				UITipItem.AddTip("操作对象为空", 0f);
			}
		}

		internal void <LHJYCMD>b__59_2()
		{
			if (g.world.playerUnit != null)
			{
				if (g.world.lotusSword != null)
				{
					g.world.lotusSword.LotusSeedOutPut(g.world.playerUnit);
					UITipItem.AddTip(g.world.playerUnit.data.unitData.propertyData.GetName() + "的莲花种子收获了", 3f);
				}
				else
				{
					UITipItem.AddTip(g.world.playerUnit.data.unitData.propertyData.GetName() + "莲花种子不存在", 3f);
				}
			}
			else
			{
				UITipItem.AddTip("操作对象为空", 3f);
			}
		}

		internal void <LHJYCMD>b__59_3()
		{
		}

		internal void <SHJCMD>b__60_0()
		{
			//IL_0034: Unknown result type (might be due to invalid IL or missing references)
			//IL_003e: Expected O, but got Unknown
			if (g.world.playerUnit != null)
			{
				if (g.world.playerUnit.GetLuck(2804) == null)
				{
					DramaFunctionTool.OptionsFunction("addNPCLuck_0_2804_-1", new DramaFunctionData(g.world.playerUnit));
					UITipItem.AddTip(g.world.playerUnit.data.unitData.propertyData.GetName() + "添加噬魂剑气运成功。", 3f);
				}
				else
				{
					UITipItem.AddTip(g.world.playerUnit.data.unitData.propertyData.GetName() + "已拥有该气运", 3f);
				}
			}
		}

		internal void <SHJCMD>b__60_1()
		{
			//IL_0034: Unknown result type (might be due to invalid IL or missing references)
			//IL_003e: Expected O, but got Unknown
			if (g.world.playerUnit != null)
			{
				if (g.world.playerUnit.GetLuck(2804) == null)
				{
					DramaFunctionTool.OptionsFunction("addNPCLuck_0_2804_-1", new DramaFunctionData(g.world.playerUnit));
				}
				g.world.soulDevourSword.SwordEventDestroy();
				g.world.soulDevourSword.UnitGetSword(g.world.playerUnit);
				UITipItem.AddTip(g.world.playerUnit.data.unitData.propertyData.GetName() + "获得噬魂剑", 3f);
			}
			else
			{
				UITipItem.AddTip("操作对象为空", 3f);
			}
		}

		internal void <SHJCMD>b__60_2()
		{
			if (g.world.playerUnit != null)
			{
				if (g.world.playerUnit.GetLuck(2804) == null)
				{
					UITipItem.AddTip("未拥有噬魂剑气运", 3f);
					return;
				}
				g.world.soulDevourSword.UnitLoseSword(g.world.playerUnit);
				UITipItem.AddTip(g.world.playerUnit.data.unitData.propertyData.GetName() + "失去噬魂剑", 3f);
			}
			else
			{
				UITipItem.AddTip("操作对象为空", 3f);
			}
		}

		internal void <SHJCMD>b__60_3()
		{
			g.world.soulDevourSword.SwordEventDestroy();
			UITipItem.AddTip(g.world.playerUnit.data.unitData.propertyData.GetName() + "销毁噬魂剑", 3f);
		}

		internal void <SHJCMD>b__60_4()
		{
			if (g.world.playerUnit.GetLuck(2804) == null)
			{
				UITipItem.AddTip("未拥有噬魂剑气运", 3f);
				return;
			}
			g.world.soulDevourSword.SwordWakeUp();
			UITipItem.AddTip("噬魂剑觉醒成功", 3f);
		}

		internal void <SHJCMD>b__60_5()
		{
			if (g.world.playerUnit.GetLuck(2804) == null)
			{
				UITipItem.AddTip("未拥有噬魂剑气运", 3f);
				return;
			}
			g.world.soulDevourSword.AddExp(100);
			UITipItem.AddTip("噬魂剑经验+100", 3f);
		}

		internal void <SHJCMD>b__60_6()
		{
			if (g.world.playerUnit.GetLuck(2804) == null)
			{
				UITipItem.AddTip("未拥有噬魂剑气运", 3f);
				return;
			}
			Data data = g.world.soulDevourSword.data;
			data.atk += 5;
			UITipItem.AddTip("噬魂剑攻击+5", 3f);
		}

		internal void <SHJCMD>b__60_7()
		{
			if (g.world.playerUnit.GetLuck(2804) == null)
			{
				UITipItem.AddTip("未拥有噬魂剑气运", 3f);
			}
			else if (!g.world.soulDevourSword.data.unitId.Equals(g.world.playerUnit.data.unitData.unitID))
			{
				WorldUnitBase unit = g.world.unit.GetUnit(g.world.soulDevourSword.data.unitId, true);
				if (unit != null)
				{
					if (!g.world.battle.isBattle)
					{
						g.data.world.marksUnitID.Add(unit.data.unitData.unitID);
						g.ui.OpenUI<UINPCInfo>(UIType.NPCInfo).InitData(unit, true);
						UITipItem.AddTip("标记成功，" + unit.data.unitData.propertyData.GetName() + "的位置为：（" + unit.data.unitData.pointX + "," + unit.data.unitData.pointY + ")\n", 3f);
						g.ui.OpenUI<UICheckPopup>(UIType.CheckPopup).InitData("提示", "是否需要传送至" + unit.data.unitData.propertyData.GetName() + "位置？", 2, Action.op_Implicit((Action)delegate
						{
							//IL_0010: Unknown result type (might be due to invalid IL or missing references)
							//IL_0015: Unknown result type (might be due to invalid IL or missing references)
							//IL_001b: Expected O, but got Unknown
							UnitActionMovePlayer val = new UnitActionMovePlayer(unit.data.unitData.GetPoint());
							g.world.playerUnit.CreateAction((UnitActionBase)(object)val, false);
						}), Action.op_Implicit((Action)delegate
						{
						}));
					}
					else
					{
						UITipItem.AddTip("标记失败，正在战斗中无法标记。", 3f);
					}
				}
				else
				{
					UITipItem.AddTip($"噬魂剑目前尚未认主。当前坐标为：({g.world.soulDevourSword.data.pointX},{g.world.soulDevourSword.data.pointY})", 3f);
					g.ui.OpenUI<UICheckPopup>(UIType.CheckPopup).InitData("提示", "是否需要传送至噬魂剑坐标位置？", 2, Action.op_Implicit((Action)delegate
					{
						//IL_0028: Unknown result type (might be due to invalid IL or missing references)
						//IL_002d: Unknown result type (might be due to invalid IL or missing references)
						//IL_0033: Expected O, but got Unknown
						UnitActionMovePlayer val = new UnitActionMovePlayer(new Vector2Int(g.world.soulDevourSword.data.pointX, g.world.soulDevourSword.data.pointY));
						g.world.playerUnit.CreateAction((UnitActionBase)(object)val, false);
					}), Action.op_Implicit((Action)delegate
					{
					}));
				}
			}
			else
			{
				UITipItem.AddTip("噬魂剑目前为玩家所有。", 3f);
			}
		}

		internal void <SHJCMD>b__60_11()
		{
		}

		internal void <SHJCMD>b__60_12()
		{
			//IL_0028: Unknown result type (might be due to invalid IL or missing references)
			//IL_002d: Unknown result type (might be due to invalid IL or missing references)
			//IL_0033: Expected O, but got Unknown
			UnitActionMovePlayer val = new UnitActionMovePlayer(new Vector2Int(g.world.soulDevourSword.data.pointX, g.world.soulDevourSword.data.pointY));
			g.world.playerUnit.CreateAction((UnitActionBase)(object)val, false);
		}

		internal void <SHJCMD>b__60_13()
		{
		}

		internal void <SHJCMD>b__60_8()
		{
			if (g.world.playerUnit.GetLuck(2804) == null)
			{
				UITipItem.AddTip("未拥有噬魂剑气运", 3f);
			}
			else if (g.world.soulDevourSword.data.level < ((ConfSoulDevourSwordLevelBase)g.conf.soulDevourSwordLevel)._allConfList.Count)
			{
				Data data = g.world.soulDevourSword.data;
				int level = data.level;
				data.level = level + 1;
				if (g.world.soulDevourSword.data.level == 2)
				{
					if (g.world.soulDevourSword.data.exp < 50)
					{
						g.world.soulDevourSword.AddExp(50);
					}
					Data data2 = g.world.soulDevourSword.data;
					data2.atk += 20;
				}
				else if (g.world.soulDevourSword.data.level == 3)
				{
					Enumerator<ConfSoulDevourSwordEffectsItem> enumerator = ((ConfSoulDevourSwordEffectsBase)g.conf.soulDevourSwordEffects)._allConfList.GetEnumerator();
					while (enumerator.MoveNext())
					{
						ConfSoulDevourSwordEffectsItem current = enumerator.Current;
						g.world.soulDevourSword.EffectProcess(current.unlockType, current.process);
					}
					Data data3 = g.world.soulDevourSword.data;
					data3.atk += 100;
				}
				UITipItem.AddTip("噬魂剑已升级，当前等级：" + g.world.soulDevourSword.data.level, 3f);
			}
			else
			{
				UITipItem.AddTip("噬魂剑等级已到达最高", 3f);
			}
		}

		internal void <SHJCMD>b__60_9()
		{
		}

		internal void <SQCMD>b__61_0()
		{
			if (g.data.world.animaWeapons == null)
			{
				return;
			}
			if (!g.data.world.animaWeapons.Contains((GameAnimaWeapon)2))
			{
				g.data.world.animaWeapons.Clear();
				g.data.world.animaWeapons.Add((GameAnimaWeapon)2);
				UIMapMain uI = g.ui.GetUI<UIMapMain>(UIType.MapMain);
				if ((Object)(object)uI != (Object)null)
				{
					uI.uiPlayerInfo.UpdatePlayerInfo();
				}
				UITipItem.AddTip("昊天眼切换成功。", 2f);
			}
			else
			{
				UITipItem.AddTip("昊天眼已存在。", 2f);
			}
		}

		internal void <SQCMD>b__61_1()
		{
			if (g.data.world.animaWeapons == null)
			{
				return;
			}
			if (!g.data.world.animaWeapons.Contains((GameAnimaWeapon)3))
			{
				g.data.world.animaWeapons.Clear();
				g.data.world.animaWeapons.Add((GameAnimaWeapon)3);
				UIMapMain uI = g.ui.GetUI<UIMapMain>(UIType.MapMain);
				if ((Object)(object)uI != (Object)null)
				{
					uI.uiPlayerInfo.UpdatePlayerInfo();
				}
				UITipItem.AddTip("炼妖壶切换成功。", 2f);
			}
			else
			{
				UITipItem.AddTip("炼妖壶已存在。", 2f);
			}
		}

		internal void <SQCMD>b__61_2()
		{
			if (g.data.world.animaWeapons == null)
			{
				return;
			}
			if (!g.data.world.animaWeapons.Contains((GameAnimaWeapon)1))
			{
				g.data.world.animaWeapons.Clear();
				g.data.world.animaWeapons.Add((GameAnimaWeapon)1);
				UIMapMain uI = g.ui.GetUI<UIMapMain>(UIType.MapMain);
				if ((Object)(object)uI != (Object)null)
				{
					uI.uiPlayerInfo.UpdatePlayerInfo();
				}
				UITipItem.AddTip("双鱼佩切换成功。", 2f);
			}
			else
			{
				UITipItem.AddTip("双鱼佩已存在。", 2f);
			}
		}

		internal void <SQCMD>b__61_3()
		{
			if (g.data.world.piscesData != null)
			{
				if (g.data.world.resurgeCount <= 0)
				{
					World world = g.data.world;
					world.resurgeCount += 3;
					UITipItem.AddTip("双鱼佩修复成功。", 2f);
				}
				else
				{
					UITipItem.AddTip("双鱼佩未损坏。", 2f);
				}
			}
			else
			{
				UITipItem.AddTip("双鱼佩未激活", 2f);
			}
		}

		internal void <SQCMD>b__61_4()
		{
			if (g.data.world.godEyeData != null)
			{
				if (g.data.world.godEyeData.isDamage || g.data.world.godEyeData.IsRepair())
				{
					g.data.world.godEyeData.Repair();
					g.data.world.godEyeData.number = 0;
					g.data.world.godEyeData.isDamage = false;
					g.data.world.godEyeData.repairMonth = 0;
					UITipItem.AddTip("昊天眼修复成功。", 2f);
				}
				else
				{
					UITipItem.AddTip("昊天眼未损坏。", 2f);
				}
			}
			else
			{
				UITipItem.AddTip("昊天眼未激活", 2f);
			}
		}

		internal void <SQCMD>b__61_5()
		{
			if (g.data.world.devilDemonData != null)
			{
				if (g.data.world.devilDemonData.isDamage || g.data.world.devilDemonData.repairMonth > 0)
				{
					g.data.world.devilDemonData.isDamage = false;
					g.data.world.devilDemonData.repairMonth = 0;
					g.data.world.devilDemonData.brokenCount = 0;
					g.world.devilDemonMgr.devilDemonData.isDamage = false;
					g.world.devilDemonMgr.devilDemonData.repairMonth = 0;
					g.world.devilDemonMgr.devilDemonData.brokenCount = 0;
					UITipItem.AddTip("炼妖壶修复成功。", 2f);
				}
				else
				{
					UITipItem.AddTip("炼妖壶未损坏。", 2f);
				}
			}
			else
			{
				UITipItem.AddTip("炼妖壶未激活", 2f);
			}
		}

		internal void <SQCMD>b__61_6()
		{
			World world = g.data.world;
			world.resurgeCount += 3;
			UITipItem.AddTip("复活点数+3", 2f);
		}

		internal void <SQCMD>b__61_7()
		{
		}

		internal void <SetConfValue>b__63_0()
		{
			//IL_006e: Unknown result type (might be due to invalid IL or missing references)
			//IL_0078: Expected O, but got Unknown
			//IL_0097: Unknown result type (might be due to invalid IL or missing references)
			//IL_00a1: Expected O, but got Unknown
			if (g.world.isIntoWorld)
			{
				List<WorldUnitBase> units = g.world.unit.GetUnits(true);
				units.Remove(g.world.playerUnit);
				Enumerator<WorldUnitBase> enumerator = units.GetEnumerator();
				while (enumerator.MoveNext())
				{
					WorldUnitBase current = enumerator.Current;
					DramaFunctionTool.OptionsFunction("delNPCLuck_" + current.data.unitData.unitID + "_" + (ModMain.mod_ID + 2), new DramaFunctionData(current));
					DramaFunctionTool.OptionsFunction("addNPCLuck_0_" + (ModMain.mod_ID + 2) + "_-1", new DramaFunctionData(current));
				}
				Console.WriteLine("刷新气运完成");
			}
		}
	}

	public GameObject ButtonExit;

	public GameObject ButtonOpen;

	public GameObject ButtonRead;

	public GameObject ToggleAllAction;

	public GameObject ToggleNPCAutoGrage;

	public GameObject ToggleNPCAutoAttackUp;

	public GameObject TogglePlayAutoAttackUp;

	public GameObject ToggleChangeDropDis;

	public GameObject ToggleBeKillAllDrop;

	public GameObject ToggleSoulDevourSworduP;

	public GameObject ToggleAutoSendGradePropInSchool;

	public GameObject ToggleOpenUnitDieTips;

	public GameObject ToggleisJudgeInti;

	public GameObject ToggleisActionSuccess;

	public GameObject ToggleisTaskAutoPut;

	public GameObject ToggleisNPCTeam;

	public GameObject ToggleisNPCHelp;

	public GameObject ToggleisOpenFight;

	public GameObject ToggleisOpenUnitLog;

	public GameObject ToggleisOpenSkillExt;

	public GameObject ButtonSQ;

	public GameObject ButtonSHJ;

	public GameObject ButtonLHJY;

	public GameObject ButtonLevel;

	public GameObject ButtonBOSSANDNPC;

	public GameObject ButtonHelp;

	public GameObject ButtonSave;

	public GameObject ButtonOther;

	public GameObject InputFieldskillUpProp;

	public GameObject InputFieldnpcAutoGrage;

	public GameObject InputFieldNosuperHeroBae;

	public GameObject InputFieldsuperHeroBae;

	public GameObject InputFieldnpcAutoAttackUp;

	public GameObject InputFieldplayAutoAttackUp;

	public GameObject InputFieldluckProp;

	public GameObject InputFieldtalentProp;

	public GameObject InputFieldrunMaxMonth;

	public GameObject InputFieldroundMonthProp;

	public GameObject InputFieldgradeProp;

	public GameObject InputFieldgradeDiminish;

	public GameObject InputFieldupGreadItemMaxCount;

	public GameObject InputFieldAutoAttackUpMaxCount;

	public GameObject InputFieldAbovePlayGradeProp;

	public GameObject InputFieldgradeAndAttackInimLimit1;

	public GameObject InputFieldgradeAndAttackInimLimit2;

	public GameObject InputFieldgradeAndAttackInimProp;

	public GameObject InputFieldunitActionProp;

	public GameObject InputFieldunitActionInimLimit1;

	public GameObject InputFieldunitActionInimLimit2;

	public GameObject InputFieldunitActionInimProp;

	public GameObject InputFieldNPCActionToPlayMaxCount;

	public GameObject InputFieldunitActionFreq;

	public GameObject InputFieldunitActionMaxCount;

	public GameObject InputFieldrunAIDate;

	public GameObject InputFieldrunAIDateDiyDay;

	public UINPCAISet(IntPtr ptr)
		: base(ptr)
	{
	}

	public void InitData()
	{
		try
		{
			ButtonExit = ((Component)((Component)this).transform.Find("Root/Panel/ButtonExit")).gameObject;
			ButtonOpen = ((Component)((Component)this).transform.Find("Root/Panel/ButtonOpen")).gameObject;
			ButtonRead = ((Component)((Component)this).transform.Find("Root/Panel/ButtonRead")).gameObject;
			ButtonSQ = ((Component)((Component)this).transform.Find("Root/Panel/ButtonSQ")).gameObject;
			ButtonSHJ = ((Component)((Component)this).transform.Find("Root/Panel/ButtonSHJ")).gameObject;
			ButtonLHJY = ((Component)((Component)this).transform.Find("Root/Panel/ButtonLHJY")).gameObject;
			ButtonLevel = ((Component)((Component)this).transform.Find("Root/Panel/ButtonLevel")).gameObject;
			ButtonBOSSANDNPC = ((Component)((Component)this).transform.Find("Root/Panel/ButtonBOSSANDNPC")).gameObject;
			ButtonHelp = ((Component)((Component)this).transform.Find("Root/Panel/ButtonHelp")).gameObject;
			ButtonSave = ((Component)((Component)this).transform.Find("Root/Panel/ButtonSave")).gameObject;
			ButtonOther = ((Component)((Component)this).transform.Find("Root/Panel/ButtonOther")).gameObject;
			ToggleAllAction = ((Component)((Component)this).transform.Find("Root/Panel/PanelToggle/ToggleAllAction")).gameObject;
			ToggleNPCAutoGrage = ((Component)((Component)this).transform.Find("Root/Panel/PanelToggle/ToggleNPCAutoGrage")).gameObject;
			ToggleNPCAutoAttackUp = ((Component)((Component)this).transform.Find("Root/Panel/PanelToggle/ToggleNPCAutoAttackUp")).gameObject;
			TogglePlayAutoAttackUp = ((Component)((Component)this).transform.Find("Root/Panel/PanelToggle/TogglePlayAutoAttackUp")).gameObject;
			ToggleChangeDropDis = ((Component)((Component)this).transform.Find("Root/Panel/PanelToggle/ToggleChangeDropDis")).gameObject;
			ToggleBeKillAllDrop = ((Component)((Component)this).transform.Find("Root/Panel/PanelToggle/ToggleBeKillAllDrop")).gameObject;
			ToggleSoulDevourSworduP = ((Component)((Component)this).transform.Find("Root/Panel/PanelToggle/ToggleSoulDevourSworduP")).gameObject;
			ToggleAutoSendGradePropInSchool = ((Component)((Component)this).transform.Find("Root/Panel/PanelToggle/ToggleAutoSendGradePropInSchool")).gameObject;
			ToggleOpenUnitDieTips = ((Component)((Component)this).transform.Find("Root/Panel/PanelToggle/ToggleOpenUnitDieTips")).gameObject;
			ToggleisJudgeInti = ((Component)((Component)this).transform.Find("Root/Panel/PanelToggle/ToggleisJudgeInti")).gameObject;
			ToggleisActionSuccess = ((Component)((Component)this).transform.Find("Root/Panel/PanelToggle/ToggleisActionSuccess")).gameObject;
			ToggleisTaskAutoPut = ((Component)((Component)this).transform.Find("Root/Panel/PanelToggle/ToggleisTaskAutoPut")).gameObject;
			ToggleisNPCTeam = ((Component)((Component)this).transform.Find("Root/Panel/PanelToggle/ToggleisNPCTeam")).gameObject;
			ToggleisNPCHelp = ((Component)((Component)this).transform.Find("Root/Panel/PanelToggle/ToggleisNPCHelp")).gameObject;
			ToggleisOpenFight = ((Component)((Component)this).transform.Find("Root/Panel/PanelToggle/ToggleisOpenFight")).gameObject;
			ToggleisOpenUnitLog = ((Component)((Component)this).transform.Find("Root/Panel/PanelToggle/ToggleisOpenUnitLog")).gameObject;
			ToggleisOpenSkillExt = ((Component)((Component)this).transform.Find("Root/Panel/PanelToggle/ToggleisOpenSkillExt")).gameObject;
			InputFieldskillUpProp = ((Component)((Component)this).transform.Find("Root/Panel/PanelInput/InputFieldskillUpProp")).gameObject;
			InputFieldnpcAutoGrage = ((Component)((Component)this).transform.Find("Root/Panel/PanelInput/InputFieldnpcAutoGrage")).gameObject;
			InputFieldNosuperHeroBae = ((Component)((Component)this).transform.Find("Root/Panel/PanelInput/InputFieldNosuperHeroBae")).gameObject;
			InputFieldsuperHeroBae = ((Component)((Component)this).transform.Find("Root/Panel/PanelInput/InputFieldsuperHeroBae")).gameObject;
			InputFieldnpcAutoAttackUp = ((Component)((Component)this).transform.Find("Root/Panel/PanelInput/InputFieldnpcAutoAttackUp")).gameObject;
			InputFieldplayAutoAttackUp = ((Component)((Component)this).transform.Find("Root/Panel/PanelInput/InputFieldplayAutoAttackUp")).gameObject;
			InputFieldluckProp = ((Component)((Component)this).transform.Find("Root/Panel/PanelInput/InputFieldluckProp")).gameObject;
			InputFieldtalentProp = ((Component)((Component)this).transform.Find("Root/Panel/PanelInput/InputFieldtalentProp")).gameObject;
			InputFieldrunMaxMonth = ((Component)((Component)this).transform.Find("Root/Panel/PanelInput/InputFieldrunMaxMonth")).gameObject;
			InputFieldroundMonthProp = ((Component)((Component)this).transform.Find("Root/Panel/PanelInput/InputFieldroundMonthProp")).gameObject;
			InputFieldgradeProp = ((Component)((Component)this).transform.Find("Root/Panel/PanelInput/InputFieldgradeProp")).gameObject;
			InputFieldgradeDiminish = ((Component)((Component)this).transform.Find("Root/Panel/PanelInput/InputFieldgradeDiminish")).gameObject;
			InputFieldupGreadItemMaxCount = ((Component)((Component)this).transform.Find("Root/Panel/PanelInput/InputFieldupGreadItemMaxCount")).gameObject;
			InputFieldAutoAttackUpMaxCount = ((Component)((Component)this).transform.Find("Root/Panel/PanelInput/InputFieldAutoAttackUpMaxCount")).gameObject;
			InputFieldAbovePlayGradeProp = ((Component)((Component)this).transform.Find("Root/Panel/PanelInput/InputFieldAbovePlayGradeProp")).gameObject;
			InputFieldgradeAndAttackInimLimit1 = ((Component)((Component)this).transform.Find("Root/Panel/PanelInput/InputFieldgradeAndAttackInimLimit1")).gameObject;
			InputFieldgradeAndAttackInimLimit2 = ((Component)((Component)this).transform.Find("Root/Panel/PanelInput/InputFieldgradeAndAttackInimLimit2")).gameObject;
			InputFieldgradeAndAttackInimProp = ((Component)((Component)this).transform.Find("Root/Panel/PanelInput/InputFieldgradeAndAttackInimProp")).gameObject;
			Console.WriteLine("PanelInput初始化完成");
			InputFieldunitActionProp = ((Component)((Component)this).transform.Find("Root/Panel/PanelInput2/InputFieldunitActionProp")).gameObject;
			InputFieldunitActionInimLimit1 = ((Component)((Component)this).transform.Find("Root/Panel/PanelInput2/InputFieldunitActionInimLimit1")).gameObject;
			InputFieldunitActionInimLimit2 = ((Component)((Component)this).transform.Find("Root/Panel/PanelInput2/InputFieldunitActionInimLimit2")).gameObject;
			InputFieldunitActionInimProp = ((Component)((Component)this).transform.Find("Root/Panel/PanelInput2/InputFieldunitActionInimProp")).gameObject;
			InputFieldNPCActionToPlayMaxCount = ((Component)((Component)this).transform.Find("Root/Panel/PanelInput2/InputFieldNPCActionToPlayMaxCount")).gameObject;
			InputFieldunitActionFreq = ((Component)((Component)this).transform.Find("Root/Panel/PanelInput2/InputFieldunitActionFreq")).gameObject;
			InputFieldunitActionMaxCount = ((Component)((Component)this).transform.Find("Root/Panel/PanelInput2/InputFieldunitActionMaxCount")).gameObject;
			InputFieldrunAIDate = ((Component)((Component)this).transform.Find("Root/Panel/PanelInput2/InputFieldrunAIDate")).gameObject;
			InputFieldrunAIDateDiyDay = ((Component)((Component)this).transform.Find("Root/Panel/PanelInput2/InputFieldrunAIDateDiyDay")).gameObject;
			Console.WriteLine("PanelInput2初始化完成");
			((UnityEvent<bool>)(object)ToggleAllAction.GetComponent<Toggle>().onValueChanged).AddListener(UnityAction<bool>.op_Implicit((Action<bool>)delegate(bool value)
			{
				ModMain.confInofFile.confInof.isCloseNpcAllAction = value;
			}));
			((UnityEvent<bool>)(object)ToggleNPCAutoGrage.GetComponent<Toggle>().onValueChanged).AddListener(UnityAction<bool>.op_Implicit((Action<bool>)delegate(bool value)
			{
				ModMain.confInofFile.confInof.isNPCAutoGrage = value;
			}));
			((UnityEvent<bool>)(object)ToggleNPCAutoAttackUp.GetComponent<Toggle>().onValueChanged).AddListener(UnityAction<bool>.op_Implicit((Action<bool>)delegate(bool value)
			{
				ModMain.confInofFile.confInof.isNPCAutoAttackUp = value;
			}));
			((UnityEvent<bool>)(object)TogglePlayAutoAttackUp.GetComponent<Toggle>().onValueChanged).AddListener(UnityAction<bool>.op_Implicit((Action<bool>)delegate(bool value)
			{
				ModMain.confInofFile.confInof.isPlayAutoAttackUp = value;
			}));
			((UnityEvent<bool>)(object)ToggleChangeDropDis.GetComponent<Toggle>().onValueChanged).AddListener(UnityAction<bool>.op_Implicit((Action<bool>)delegate(bool value)
			{
				ModMain.confInofFile.confInof.isChangeDropDis = value;
			}));
			((UnityEvent<bool>)(object)ToggleBeKillAllDrop.GetComponent<Toggle>().onValueChanged).AddListener(UnityAction<bool>.op_Implicit((Action<bool>)delegate(bool value)
			{
				ModMain.confInofFile.confInof.confDiySet.isBeKillAllDrop = value;
			}));
			((UnityEvent<bool>)(object)ToggleisActionSuccess.GetComponent<Toggle>().onValueChanged).AddListener(UnityAction<bool>.op_Implicit((Action<bool>)delegate(bool value)
			{
				ModMain.confInofFile.confInof.confDiySet.isActionSuccess = value;
			}));
			((UnityEvent<bool>)(object)ToggleisTaskAutoPut.GetComponent<Toggle>().onValueChanged).AddListener(UnityAction<bool>.op_Implicit((Action<bool>)delegate(bool value)
			{
				ModMain.confInofFile.confInof.confDiySet.isAutoPostTask = value;
			}));
			((UnityEvent<bool>)(object)ToggleisNPCTeam.GetComponent<Toggle>().onValueChanged).AddListener(UnityAction<bool>.op_Implicit((Action<bool>)delegate(bool value)
			{
				ModMain.confInofFile.confInof.NPCTeam = value;
			}));
			((UnityEvent<bool>)(object)ToggleisNPCHelp.GetComponent<Toggle>().onValueChanged).AddListener(UnityAction<bool>.op_Implicit((Action<bool>)delegate(bool value)
			{
				ModMain.confInofFile.confInof.NPCHelp = value;
			}));
			((UnityEvent<bool>)(object)ToggleisOpenFight.GetComponent<Toggle>().onValueChanged).AddListener(UnityAction<bool>.op_Implicit((Action<bool>)delegate(bool value)
			{
				ModMain.confInofFile.confInof.NPCAutoFight = value;
			}));
			((UnityEvent<bool>)(object)ToggleisOpenUnitLog.GetComponent<Toggle>().onValueChanged).AddListener(UnityAction<bool>.op_Implicit((Action<bool>)delegate(bool value)
			{
				ModMain.confInofFile.confInof.AutoUnitLog = value;
			}));
			((UnityEvent<bool>)(object)ToggleisOpenSkillExt.GetComponent<Toggle>().onValueChanged).AddListener(UnityAction<bool>.op_Implicit((Action<bool>)delegate(bool value)
			{
				ModMain.confInofFile.confInof.SkillExt = value;
				UITipItem.AddTip("苦海异象开启状态已更新，进入战斗界面或重新进入大地图时将自动更新。", 3f);
			}));
			((UnityEvent<bool>)(object)ToggleSoulDevourSworduP.GetComponent<Toggle>().onValueChanged).AddListener(UnityAction<bool>.op_Implicit((Action<bool>)delegate(bool value)
			{
				//IL_00cc: Unknown result type (might be due to invalid IL or missing references)
				//IL_00d6: Expected O, but got Unknown
				//IL_00ec: Unknown result type (might be due to invalid IL or missing references)
				//IL_00f6: Expected O, but got Unknown
				//IL_003a: Unknown result type (might be due to invalid IL or missing references)
				//IL_0044: Expected O, but got Unknown
				//IL_005a: Unknown result type (might be due to invalid IL or missing references)
				//IL_0064: Expected O, but got Unknown
				ModMain.confInofFile.confInof.isSoulDevourSworduP = value;
				if (ModMain.confInofFile.confInof.isSoulDevourSworduP)
				{
					((ConfSoulDevourSwordParamerBase)g.conf.soulDevourSwordParamer)._swordDevourInterval = new ConfSoulDevourSwordParamerItem(17, "swordDevourInterval", "999999");
					((ConfSoulDevourSwordParamerBase)g.conf.soulDevourSwordParamer)._swordTargetHideRange = new ConfSoulDevourSwordParamerItem(24, "swordTargetHideRange", "0");
					List<ConfSoulDevourSwordDevourItem> allConfList = ((ConfSoulDevourSwordDevourBase)g.conf.soulDevourSwordDevour)._allConfList;
					for (int i = 0; i < allConfList.Count; i++)
					{
						allConfList[i].devourCD = 0;
					}
					g.world.soulDevourSword.data.nextDevourMonth = g.world.run.roundMonth;
				}
				else
				{
					((ConfSoulDevourSwordParamerBase)g.conf.soulDevourSwordParamer)._swordDevourInterval = new ConfSoulDevourSwordParamerItem(17, "swordDevourInterval", "24");
					((ConfSoulDevourSwordParamerBase)g.conf.soulDevourSwordParamer)._swordTargetHideRange = new ConfSoulDevourSwordParamerItem(24, "swordTargetHideRange", "5");
					List<ConfSoulDevourSwordDevourItem> allConfList2 = ((ConfSoulDevourSwordDevourBase)g.conf.soulDevourSwordDevour)._allConfList;
					for (int j = 0; j < allConfList2.Count; j++)
					{
						allConfList2[j].devourCD = 3;
					}
				}
			}));
			((UnityEvent<bool>)(object)ToggleAutoSendGradePropInSchool.GetComponent<Toggle>().onValueChanged).AddListener(UnityAction<bool>.op_Implicit((Action<bool>)delegate(bool value)
			{
				ModMain.confInofFile.confInof.confDiySet.isAutoSendGradePropInSchool = value;
			}));
			((UnityEvent<bool>)(object)ToggleOpenUnitDieTips.GetComponent<Toggle>().onValueChanged).AddListener(UnityAction<bool>.op_Implicit((Action<bool>)delegate(bool value)
			{
				ModMain.confInofFile.confInof.confDiySet.isOpenUnitDieTips = value;
			}));
			((UnityEvent<bool>)(object)ToggleisJudgeInti.GetComponent<Toggle>().onValueChanged).AddListener(UnityAction<bool>.op_Implicit((Action<bool>)delegate(bool value)
			{
				ModMain.confInofFile.confInof.isJudgeInti = value;
			}));
			UpdataUI();
		}
		catch (Exception ex)
		{
			Console.WriteLine(ex.ToString());
		}
		ButtonExit.GetComponent<Image>().sprite = SpriteTool.GetSprite("Common", "tuichu");
		((UnityEvent)ButtonExit.GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
		{
			//IL_000b: Unknown result type (might be due to invalid IL or missing references)
			//IL_0016: Expected O, but got Unknown
			try
			{
				g.ui.CloseUI(new UITypeBase("UINPCAISet", (UILayer)0), false);
				ModMain.confInofFile.SaveConf();
			}
			catch (Exception ex2)
			{
				Console.WriteLine(ex2.ToString());
				Console.WriteLine("V出错");
			}
		}));
		((UnityEvent)ButtonOpen.GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
		{
			try
			{
				Application.OpenURL(ModMain.confInofFile.pathFile);
			}
			catch (Exception ex2)
			{
				Console.WriteLine(ex2.ToString());
				Console.WriteLine("V出错");
			}
		}));
		((UnityEvent)ButtonRead.GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
		{
			try
			{
				ModMain.confInofFile.InitConf();
				SetConfValue();
				UpdataUI();
			}
			catch (Exception ex2)
			{
				Console.WriteLine(ex2.ToString());
				Console.WriteLine("V出错");
			}
		}));
		((UnityEvent)ButtonSQ.GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
		{
			try
			{
				SQCMD();
			}
			catch (Exception ex2)
			{
				Console.WriteLine(ex2.ToString());
				Console.WriteLine("V出错");
			}
		}));
		((UnityEvent)ButtonSHJ.GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
		{
			try
			{
				SHJCMD();
			}
			catch (Exception ex2)
			{
				Console.WriteLine(ex2.ToString());
				Console.WriteLine("V出错");
			}
		}));
		((UnityEvent)ButtonLHJY.GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
		{
			try
			{
				LHJYCMD();
			}
			catch (Exception ex2)
			{
				Console.WriteLine(ex2.ToString());
				Console.WriteLine("V出错");
			}
		}));
		((UnityEvent)ButtonLevel.GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
		{
			try
			{
				LevelCMD();
			}
			catch (Exception ex2)
			{
				Console.WriteLine(ex2.ToString());
				Console.WriteLine("V出错");
			}
		}));
		((UnityEvent)ButtonBOSSANDNPC.GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
		{
			//IL_0010: Unknown result type (might be due to invalid IL or missing references)
			//IL_001a: Expected O, but got Unknown
			try
			{
				ClassInjector.RegisterTypeInIl2Cpp<UINPCAISet2>();
				((Component)g.ui.OpenUI(new UITypeBase("UINPCAISet2", (UILayer)0))).gameObject.AddComponent<UINPCAISet2>().InitData();
			}
			catch (Exception ex2)
			{
				Console.WriteLine(ex2.ToString());
				Console.WriteLine("V出错");
			}
		}));
		((UnityEvent)ButtonHelp.GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
		{
			try
			{
				string text = "NPC-AI功能迁移配置参数说明：\r\n1.一键关闭所有行动：NPC行动的总开关，勾选后所有NPC停止本NPC-AI模块的所有行动。\r\n2.打开NPC境界自动突破和打开NPC功法自动升级：打开后，NPC每月境界和功法有概率自动成长及突破。\r\n3.打开队友境界自动突破和打开队友功法自动升级：打开后，组队的NPC境界和功法每月有概率自动成长及提升。\r\n4.打开玩家功法自动升级:打开后，玩家战斗胜利后自身功法有几率自动成长及提升，概率可调。\r\n5.打开NPC境界影响概率开关:打开后，NPC境界突破和功法成长的最终几率会受玩家境界和自身境界的影响。详情参考最终概率计算公式。\r\n6.打开NPC行动亲密度判定:打开后，处于亲密度上下限之间的NPC不会行动，如上下限为-1 至1，则陌生人不会行动，仇人和亲友都会正常行动。\r\n7.私人对话按钮需要好友关系:打开后，仅亲密度大于等于60的NPC会出现私人对话按钮。\r\n8.无条件大能传功:打开后，显示大能传功按钮。对方处于大境界待突破状态。\r\n9.个性化需求常见配置：开启真爱模式后，即使npc已经结婚、有道侣，还是会主动结缘，容易出轨。开启后宫模式后，道侣没有达到上限，就会一直主动结缘，不开启，默认保持一个道侣。\r\n";
				g.ui.OpenUI<UITextInfoLong>(UIType.TextInfoLong).InitData("提示", text, "", (Action)null, false);
			}
			catch (Exception ex2)
			{
				Console.WriteLine(ex2.ToString());
				Console.WriteLine("V出错");
			}
		}));
		((UnityEvent)ButtonOther.GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
		{
			try
			{
				OtherCMD();
			}
			catch (Exception ex2)
			{
				Console.WriteLine(ex2.ToString());
				Console.WriteLine("V出错");
			}
		}));
		((UnityEvent)ButtonSave.GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
		{
			try
			{
				ModMain.confInofFile.confInof.skillUpProp = int.Parse(InputFieldskillUpProp.GetComponent<InputField>().text);
				ModMain.confInofFile.confInof.baseProp = double.Parse(InputFieldnpcAutoGrage.GetComponent<InputField>().text);
				ModMain.confInofFile.confInof.NosuperHeroBae = int.Parse(InputFieldNosuperHeroBae.GetComponent<InputField>().text);
				ModMain.confInofFile.confInof.superHeroBae = int.Parse(InputFieldsuperHeroBae.GetComponent<InputField>().text);
				ModMain.confInofFile.confInof.npcAutoAttackUp = int.Parse(InputFieldnpcAutoAttackUp.GetComponent<InputField>().text);
				ModMain.confInofFile.confInof.playAutoAttackUp = int.Parse(InputFieldplayAutoAttackUp.GetComponent<InputField>().text);
				ModMain.confInofFile.confInof.luckProp = double.Parse(InputFieldluckProp.GetComponent<InputField>().text);
				ModMain.confInofFile.confInof.talentProp = double.Parse(InputFieldtalentProp.GetComponent<InputField>().text);
				ModMain.confInofFile.confInof.runMaxMonth = int.Parse(InputFieldrunMaxMonth.GetComponent<InputField>().text);
				ModMain.confInofFile.confInof.roundMonthProp = double.Parse(InputFieldroundMonthProp.GetComponent<InputField>().text);
				ModMain.confInofFile.confInof.gradeProp = double.Parse(InputFieldgradeProp.GetComponent<InputField>().text);
				ModMain.confInofFile.confInof.gradeDiminish = int.Parse(InputFieldgradeDiminish.GetComponent<InputField>().text);
				ModMain.confInofFile.confInof.upGreadItemMaxCount = int.Parse(InputFieldupGreadItemMaxCount.GetComponent<InputField>().text);
				ModMain.confInofFile.confInof.AutoAttackUpMaxCount = int.Parse(InputFieldAutoAttackUpMaxCount.GetComponent<InputField>().text);
				ModMain.confInofFile.confInof.AbovePlayGradeProp = double.Parse(InputFieldAbovePlayGradeProp.GetComponent<InputField>().text);
				ModMain.confInofFile.confInof.gradeAndAttackInimLimit1 = int.Parse(InputFieldgradeAndAttackInimLimit1.GetComponent<InputField>().text);
				ModMain.confInofFile.confInof.gradeAndAttackInimLimit2 = int.Parse(InputFieldgradeAndAttackInimLimit2.GetComponent<InputField>().text);
				ModMain.confInofFile.confInof.gradeAndAttackInimProp = int.Parse(InputFieldgradeAndAttackInimProp.GetComponent<InputField>().text);
				ModMain.confInofFile.confInof.unitActionProp = int.Parse(InputFieldunitActionProp.GetComponent<InputField>().text);
				ModMain.confInofFile.confInof.unitActionInimLimit1 = int.Parse(InputFieldunitActionInimLimit1.GetComponent<InputField>().text);
				ModMain.confInofFile.confInof.unitActionInimLimit2 = int.Parse(InputFieldunitActionInimLimit2.GetComponent<InputField>().text);
				ModMain.confInofFile.confInof.unitActionInimProp = int.Parse(InputFieldunitActionInimProp.GetComponent<InputField>().text);
				ModMain.confInofFile.confInof.NPCActionToPlayMaxCount = int.Parse(InputFieldNPCActionToPlayMaxCount.GetComponent<InputField>().text);
				ModMain.confInofFile.confInof.unitActionFreq = int.Parse(InputFieldunitActionFreq.GetComponent<InputField>().text);
				ModMain.confInofFile.confInof.unitActionMaxCount = int.Parse(InputFieldunitActionMaxCount.GetComponent<InputField>().text);
				ModMain.confInofFile.confInof.runAIDate = int.Parse(InputFieldrunAIDate.GetComponent<InputField>().text);
				ModMain.confInofFile.confInof.runAIDateDiyDay = int.Parse(InputFieldrunAIDateDiyDay.GetComponent<InputField>().text);
				ModMain.confInofFile.SaveConf();
				UITipItem.AddTip("保存成功", 3f);
			}
			catch (Exception ex2)
			{
				Console.WriteLine(ex2.ToString());
				Console.WriteLine("V出错");
			}
		}));
	}

	public void OtherCMD()
	{
		//IL_0008: Unknown result type (might be due to invalid IL or missing references)
		//IL_00cd: Unknown result type (might be due to invalid IL or missing references)
		//IL_00e3: Unknown result type (might be due to invalid IL or missing references)
		//IL_00f9: Unknown result type (might be due to invalid IL or missing references)
		//IL_0106: Unknown result type (might be due to invalid IL or missing references)
		//IL_0113: Unknown result type (might be due to invalid IL or missing references)
		UICustomDramaDyn val = new UICustomDramaDyn(ModMain.mod_ID + 9);
		int num = ModMain.mod_ID + 1;
		int num2 = ModMain.mod_ID + 2;
		Action action = delegate
		{
			//IL_001e: Unknown result type (might be due to invalid IL or missing references)
			g.data.map.OpenMask(g.world.playerUnit.data.unitData.GetPoint(), 9999, true);
			UITipItem.AddTip("全图已开启", 3f);
		};
		Action action2 = delegate
		{
		};
		if (<>c.<>9__57_2 == null)
		{
			<>c.<>9__57_2 = delegate
			{
			};
		}
		if (<>c.<>9__57_3 == null)
		{
			<>c.<>9__57_3 = delegate
			{
			};
		}
		if (<>c.<>9__57_4 == null)
		{
			<>c.<>9__57_4 = delegate
			{
			};
		}
		if (<>c.<>9__57_5 == null)
		{
			<>c.<>9__57_5 = delegate
			{
			};
		}
		((UICustomDramaBase)val).dramaData.dialogueOptions[num] = "开启全图";
		((UICustomDramaBase)val).dramaData.dialogueOptions[num2] = "关闭";
		val.SetOptionCall(num, Action.op_Implicit(action));
		val.SetOptionCall(num2, Action.op_Implicit(action2));
		((UICustomDramaBase)val).dramaData.unitLeft = g.world.playerUnit;
		val.OpenUI();
	}

	public void LevelCMD()
	{
		//IL_0008: Unknown result type (might be due to invalid IL or missing references)
		//IL_0105: Unknown result type (might be due to invalid IL or missing references)
		//IL_011b: Unknown result type (might be due to invalid IL or missing references)
		//IL_0131: Unknown result type (might be due to invalid IL or missing references)
		//IL_0147: Unknown result type (might be due to invalid IL or missing references)
		//IL_015d: Unknown result type (might be due to invalid IL or missing references)
		//IL_0174: Unknown result type (might be due to invalid IL or missing references)
		//IL_018b: Unknown result type (might be due to invalid IL or missing references)
		//IL_0199: Unknown result type (might be due to invalid IL or missing references)
		//IL_01a7: Unknown result type (might be due to invalid IL or missing references)
		//IL_01b5: Unknown result type (might be due to invalid IL or missing references)
		//IL_01c3: Unknown result type (might be due to invalid IL or missing references)
		//IL_01d2: Unknown result type (might be due to invalid IL or missing references)
		//IL_01e1: Unknown result type (might be due to invalid IL or missing references)
		UICustomDramaDyn val = new UICustomDramaDyn(ModMain.mod_ID + 9);
		int num = ModMain.mod_ID + 1;
		int num2 = ModMain.mod_ID + 2;
		int num3 = ModMain.mod_ID + 3;
		int num4 = ModMain.mod_ID + 4;
		int num5 = ModMain.mod_ID + 5;
		int num6 = ModMain.mod_ID + 6;
		Action action = delegate
		{
			//IL_000f: Unknown result type (might be due to invalid IL or missing references)
			//IL_0015: Expected I4, but got Unknown
			UITipItem.AddTip("游戏难度修改成功，原游戏难度为:" + (int)g.data.world.gameLevel + "，新的游戏难度为:普通(1级)", 3f);
			g.data.world.gameLevel = (GameLevelType)1;
		};
		Action action2 = delegate
		{
			//IL_000f: Unknown result type (might be due to invalid IL or missing references)
			//IL_0015: Expected I4, but got Unknown
			UITipItem.AddTip("游戏难度修改成功，原游戏难度为:" + (int)g.data.world.gameLevel + "，新的游戏难度为:困难(2级)", 3f);
			g.data.world.gameLevel = (GameLevelType)2;
		};
		Action action3 = delegate
		{
			//IL_000f: Unknown result type (might be due to invalid IL or missing references)
			//IL_0015: Expected I4, but got Unknown
			UITipItem.AddTip("游戏难度修改成功，原游戏难度为:" + (int)g.data.world.gameLevel + "，新的游戏难度为:极难(3级)", 3f);
			g.data.world.gameLevel = (GameLevelType)3;
		};
		Action action4 = delegate
		{
			//IL_000f: Unknown result type (might be due to invalid IL or missing references)
			//IL_0015: Expected I4, but got Unknown
			UITipItem.AddTip("游戏难度修改成功，原游戏难度为:" + (int)g.data.world.gameLevel + "，新的游戏难度为:地狱(4级)", 3f);
			g.data.world.gameLevel = (GameLevelType)4;
		};
		Action action5 = delegate
		{
			//IL_000f: Unknown result type (might be due to invalid IL or missing references)
			//IL_0015: Expected I4, but got Unknown
			UITipItem.AddTip("游戏难度修改成功，原游戏难度为:" + (int)g.data.world.gameLevel + "，新的游戏难度为:洪荒(5级)", 3f);
			g.data.world.gameLevel = (GameLevelType)5;
		};
		Action action6 = delegate
		{
		};
		((UICustomDramaBase)val).dramaData.dialogueOptions[num] = "普通";
		((UICustomDramaBase)val).dramaData.dialogueOptions[num2] = "困难";
		((UICustomDramaBase)val).dramaData.dialogueOptions[num3] = "极难";
		((UICustomDramaBase)val).dramaData.dialogueOptions[num4] = "地狱";
		((UICustomDramaBase)val).dramaData.dialogueOptions[num5] = "洪荒";
		((UICustomDramaBase)val).dramaData.dialogueOptions[num6] = "取消";
		val.SetOptionCall(num, Action.op_Implicit(action));
		val.SetOptionCall(num2, Action.op_Implicit(action2));
		val.SetOptionCall(num3, Action.op_Implicit(action3));
		val.SetOptionCall(num4, Action.op_Implicit(action4));
		val.SetOptionCall(num5, Action.op_Implicit(action5));
		val.SetOptionCall(num6, Action.op_Implicit(action6));
		((UICustomDramaBase)val).dramaData.unitLeft = g.world.playerUnit;
		val.OpenUI();
	}

	public void LHJYCMD()
	{
		//IL_0008: Unknown result type (might be due to invalid IL or missing references)
		//IL_00b1: Unknown result type (might be due to invalid IL or missing references)
		//IL_00c7: Unknown result type (might be due to invalid IL or missing references)
		//IL_00dd: Unknown result type (might be due to invalid IL or missing references)
		//IL_00f3: Unknown result type (might be due to invalid IL or missing references)
		//IL_0109: Unknown result type (might be due to invalid IL or missing references)
		//IL_0117: Unknown result type (might be due to invalid IL or missing references)
		//IL_0125: Unknown result type (might be due to invalid IL or missing references)
		//IL_0133: Unknown result type (might be due to invalid IL or missing references)
		//IL_0141: Unknown result type (might be due to invalid IL or missing references)
		UICustomDramaDyn val = new UICustomDramaDyn(ModMain.mod_ID + 9);
		int num = ModMain.mod_ID + 1;
		int num2 = ModMain.mod_ID + 2;
		int num3 = ModMain.mod_ID + 3;
		int num4 = ModMain.mod_ID + 4;
		Action action = delegate
		{
			//IL_0034: Unknown result type (might be due to invalid IL or missing references)
			//IL_003e: Expected O, but got Unknown
			if (g.world.playerUnit != null)
			{
				if (g.world.playerUnit.GetLuck(2806) == null)
				{
					DramaFunctionTool.OptionsFunction("addNPCLuck_0_2806_-1", new DramaFunctionData(g.world.playerUnit));
					UITipItem.AddTip(g.world.playerUnit.data.unitData.propertyData.GetName() + "添加莲花剑意气运成功。", 3f);
				}
				else
				{
					UITipItem.AddTip(g.world.playerUnit.data.unitData.propertyData.GetName() + "已拥有该气运", 3f);
				}
			}
		};
		Action action2 = delegate
		{
			if (g.world.playerUnit != null)
			{
				if (g.world.lotusSword != null)
				{
					g.world.lotusSword.LotusSeedGrow(g.world.playerUnit);
					UITipItem.AddTip(g.world.playerUnit.data.unitData.propertyData.GetName() + "的莲花种子成长了", 3f);
				}
				else
				{
					UITipItem.AddTip(g.world.playerUnit.data.unitData.propertyData.GetName() + "莲花种子不存在", 3f);
				}
			}
			else
			{
				UITipItem.AddTip("操作对象为空", 0f);
			}
		};
		Action action3 = delegate
		{
			if (g.world.playerUnit != null)
			{
				if (g.world.lotusSword != null)
				{
					g.world.lotusSword.LotusSeedOutPut(g.world.playerUnit);
					UITipItem.AddTip(g.world.playerUnit.data.unitData.propertyData.GetName() + "的莲花种子收获了", 3f);
				}
				else
				{
					UITipItem.AddTip(g.world.playerUnit.data.unitData.propertyData.GetName() + "莲花种子不存在", 3f);
				}
			}
			else
			{
				UITipItem.AddTip("操作对象为空", 3f);
			}
		};
		Action action4 = delegate
		{
		};
		((UICustomDramaBase)val).dramaData.dialogueOptions[num] = "获得莲花剑意气运";
		((UICustomDramaBase)val).dramaData.dialogueOptions[num2] = "莲花种子成长";
		((UICustomDramaBase)val).dramaData.dialogueOptions[num3] = "莲花种子收货";
		((UICustomDramaBase)val).dramaData.dialogueOptions[num4] = "取消";
		val.SetOptionCall(num, Action.op_Implicit(action));
		val.SetOptionCall(num2, Action.op_Implicit(action2));
		val.SetOptionCall(num3, Action.op_Implicit(action3));
		val.SetOptionCall(num4, Action.op_Implicit(action4));
		((UICustomDramaBase)val).dramaData.unitLeft = g.world.playerUnit;
		val.OpenUI();
	}

	public void SHJCMD()
	{
		//IL_0008: Unknown result type (might be due to invalid IL or missing references)
		//IL_01af: Unknown result type (might be due to invalid IL or missing references)
		//IL_01c5: Unknown result type (might be due to invalid IL or missing references)
		//IL_01db: Unknown result type (might be due to invalid IL or missing references)
		//IL_01f1: Unknown result type (might be due to invalid IL or missing references)
		//IL_0207: Unknown result type (might be due to invalid IL or missing references)
		//IL_021e: Unknown result type (might be due to invalid IL or missing references)
		//IL_0235: Unknown result type (might be due to invalid IL or missing references)
		//IL_024c: Unknown result type (might be due to invalid IL or missing references)
		//IL_0263: Unknown result type (might be due to invalid IL or missing references)
		//IL_027a: Unknown result type (might be due to invalid IL or missing references)
		//IL_0291: Unknown result type (might be due to invalid IL or missing references)
		//IL_029f: Unknown result type (might be due to invalid IL or missing references)
		//IL_02ad: Unknown result type (might be due to invalid IL or missing references)
		//IL_02bb: Unknown result type (might be due to invalid IL or missing references)
		//IL_02c9: Unknown result type (might be due to invalid IL or missing references)
		//IL_02d8: Unknown result type (might be due to invalid IL or missing references)
		//IL_02e7: Unknown result type (might be due to invalid IL or missing references)
		//IL_02f6: Unknown result type (might be due to invalid IL or missing references)
		//IL_0305: Unknown result type (might be due to invalid IL or missing references)
		//IL_0314: Unknown result type (might be due to invalid IL or missing references)
		//IL_0323: Unknown result type (might be due to invalid IL or missing references)
		UICustomDramaDyn val = new UICustomDramaDyn(ModMain.mod_ID + 9);
		int num = ModMain.mod_ID + 1;
		int num2 = ModMain.mod_ID + 2;
		int num3 = ModMain.mod_ID + 3;
		int num4 = ModMain.mod_ID + 4;
		int num5 = ModMain.mod_ID + 5;
		int num6 = ModMain.mod_ID + 6;
		int num7 = ModMain.mod_ID + 7;
		int num8 = ModMain.mod_ID + 8;
		int num9 = ModMain.mod_ID + 9;
		int num10 = ModMain.mod_ID + 50;
		Action action = delegate
		{
			//IL_0034: Unknown result type (might be due to invalid IL or missing references)
			//IL_003e: Expected O, but got Unknown
			if (g.world.playerUnit != null)
			{
				if (g.world.playerUnit.GetLuck(2804) == null)
				{
					DramaFunctionTool.OptionsFunction("addNPCLuck_0_2804_-1", new DramaFunctionData(g.world.playerUnit));
					UITipItem.AddTip(g.world.playerUnit.data.unitData.propertyData.GetName() + "添加噬魂剑气运成功。", 3f);
				}
				else
				{
					UITipItem.AddTip(g.world.playerUnit.data.unitData.propertyData.GetName() + "已拥有该气运", 3f);
				}
			}
		};
		Action action2 = delegate
		{
			//IL_0034: Unknown result type (might be due to invalid IL or missing references)
			//IL_003e: Expected O, but got Unknown
			if (g.world.playerUnit != null)
			{
				if (g.world.playerUnit.GetLuck(2804) == null)
				{
					DramaFunctionTool.OptionsFunction("addNPCLuck_0_2804_-1", new DramaFunctionData(g.world.playerUnit));
				}
				g.world.soulDevourSword.SwordEventDestroy();
				g.world.soulDevourSword.UnitGetSword(g.world.playerUnit);
				UITipItem.AddTip(g.world.playerUnit.data.unitData.propertyData.GetName() + "获得噬魂剑", 3f);
			}
			else
			{
				UITipItem.AddTip("操作对象为空", 3f);
			}
		};
		Action action3 = delegate
		{
			if (g.world.playerUnit != null)
			{
				if (g.world.playerUnit.GetLuck(2804) == null)
				{
					UITipItem.AddTip("未拥有噬魂剑气运", 3f);
				}
				else
				{
					g.world.soulDevourSword.UnitLoseSword(g.world.playerUnit);
					UITipItem.AddTip(g.world.playerUnit.data.unitData.propertyData.GetName() + "失去噬魂剑", 3f);
				}
			}
			else
			{
				UITipItem.AddTip("操作对象为空", 3f);
			}
		};
		Action action4 = delegate
		{
			g.world.soulDevourSword.SwordEventDestroy();
			UITipItem.AddTip(g.world.playerUnit.data.unitData.propertyData.GetName() + "销毁噬魂剑", 3f);
		};
		Action action5 = delegate
		{
			if (g.world.playerUnit.GetLuck(2804) == null)
			{
				UITipItem.AddTip("未拥有噬魂剑气运", 3f);
			}
			else
			{
				g.world.soulDevourSword.SwordWakeUp();
				UITipItem.AddTip("噬魂剑觉醒成功", 3f);
			}
		};
		Action action6 = delegate
		{
			if (g.world.playerUnit.GetLuck(2804) == null)
			{
				UITipItem.AddTip("未拥有噬魂剑气运", 3f);
			}
			else
			{
				g.world.soulDevourSword.AddExp(100);
				UITipItem.AddTip("噬魂剑经验+100", 3f);
			}
		};
		Action action7 = delegate
		{
			if (g.world.playerUnit.GetLuck(2804) == null)
			{
				UITipItem.AddTip("未拥有噬魂剑气运", 3f);
			}
			else
			{
				Data data = g.world.soulDevourSword.data;
				data.atk += 5;
				UITipItem.AddTip("噬魂剑攻击+5", 3f);
			}
		};
		Action action8 = delegate
		{
			if (g.world.playerUnit.GetLuck(2804) == null)
			{
				UITipItem.AddTip("未拥有噬魂剑气运", 3f);
			}
			else if (!g.world.soulDevourSword.data.unitId.Equals(g.world.playerUnit.data.unitData.unitID))
			{
				WorldUnitBase unit = g.world.unit.GetUnit(g.world.soulDevourSword.data.unitId, true);
				if (unit != null)
				{
					if (!g.world.battle.isBattle)
					{
						g.data.world.marksUnitID.Add(unit.data.unitData.unitID);
						g.ui.OpenUI<UINPCInfo>(UIType.NPCInfo).InitData(unit, true);
						UITipItem.AddTip("标记成功，" + unit.data.unitData.propertyData.GetName() + "的位置为：（" + unit.data.unitData.pointX + "," + unit.data.unitData.pointY + ")\n", 3f);
						g.ui.OpenUI<UICheckPopup>(UIType.CheckPopup).InitData("提示", "是否需要传送至" + unit.data.unitData.propertyData.GetName() + "位置？", 2, Action.op_Implicit((Action)delegate
						{
							//IL_0010: Unknown result type (might be due to invalid IL or missing references)
							//IL_0015: Unknown result type (might be due to invalid IL or missing references)
							//IL_001b: Expected O, but got Unknown
							UnitActionMovePlayer val2 = new UnitActionMovePlayer(unit.data.unitData.GetPoint());
							g.world.playerUnit.CreateAction((UnitActionBase)(object)val2, false);
						}), Action.op_Implicit((Action)delegate
						{
						}));
					}
					else
					{
						UITipItem.AddTip("标记失败，正在战斗中无法标记。", 3f);
					}
				}
				else
				{
					UITipItem.AddTip($"噬魂剑目前尚未认主。当前坐标为：({g.world.soulDevourSword.data.pointX},{g.world.soulDevourSword.data.pointY})", 3f);
					g.ui.OpenUI<UICheckPopup>(UIType.CheckPopup).InitData("提示", "是否需要传送至噬魂剑坐标位置？", 2, Action.op_Implicit((Action)delegate
					{
						//IL_0028: Unknown result type (might be due to invalid IL or missing references)
						//IL_002d: Unknown result type (might be due to invalid IL or missing references)
						//IL_0033: Expected O, but got Unknown
						UnitActionMovePlayer val2 = new UnitActionMovePlayer(new Vector2Int(g.world.soulDevourSword.data.pointX, g.world.soulDevourSword.data.pointY));
						g.world.playerUnit.CreateAction((UnitActionBase)(object)val2, false);
					}), Action.op_Implicit((Action)delegate
					{
					}));
				}
			}
			else
			{
				UITipItem.AddTip("噬魂剑目前为玩家所有。", 3f);
			}
		};
		Action action9 = delegate
		{
			if (g.world.playerUnit.GetLuck(2804) == null)
			{
				UITipItem.AddTip("未拥有噬魂剑气运", 3f);
			}
			else if (g.world.soulDevourSword.data.level < ((ConfSoulDevourSwordLevelBase)g.conf.soulDevourSwordLevel)._allConfList.Count)
			{
				Data data = g.world.soulDevourSword.data;
				int level = data.level;
				data.level = level + 1;
				if (g.world.soulDevourSword.data.level == 2)
				{
					if (g.world.soulDevourSword.data.exp < 50)
					{
						g.world.soulDevourSword.AddExp(50);
					}
					Data data2 = g.world.soulDevourSword.data;
					data2.atk += 20;
				}
				else if (g.world.soulDevourSword.data.level == 3)
				{
					Enumerator<ConfSoulDevourSwordEffectsItem> enumerator = ((ConfSoulDevourSwordEffectsBase)g.conf.soulDevourSwordEffects)._allConfList.GetEnumerator();
					while (enumerator.MoveNext())
					{
						ConfSoulDevourSwordEffectsItem current = enumerator.Current;
						g.world.soulDevourSword.EffectProcess(current.unlockType, current.process);
					}
					Data data3 = g.world.soulDevourSword.data;
					data3.atk += 100;
				}
				UITipItem.AddTip("噬魂剑已升级，当前等级：" + g.world.soulDevourSword.data.level, 3f);
			}
			else
			{
				UITipItem.AddTip("噬魂剑等级已到达最高", 3f);
			}
		};
		Action action10 = delegate
		{
		};
		((UICustomDramaBase)val).dramaData.dialogueOptions[num] = "获得噬魂剑气运";
		((UICustomDramaBase)val).dramaData.dialogueOptions[num2] = "获得噬魂剑";
		((UICustomDramaBase)val).dramaData.dialogueOptions[num3] = "丢弃噬魂剑";
		((UICustomDramaBase)val).dramaData.dialogueOptions[num4] = "摧毁噬魂剑";
		((UICustomDramaBase)val).dramaData.dialogueOptions[num5] = "噬魂剑觉醒";
		((UICustomDramaBase)val).dramaData.dialogueOptions[num6] = "噬魂剑经验+100";
		((UICustomDramaBase)val).dramaData.dialogueOptions[num7] = "噬魂剑攻击+10";
		((UICustomDramaBase)val).dramaData.dialogueOptions[num8] = "定位噬魂剑";
		((UICustomDramaBase)val).dramaData.dialogueOptions[num9] = "噬魂剑升级";
		((UICustomDramaBase)val).dramaData.dialogueOptions[num10] = "取消";
		val.SetOptionCall(num, Action.op_Implicit(action));
		val.SetOptionCall(num2, Action.op_Implicit(action2));
		val.SetOptionCall(num3, Action.op_Implicit(action3));
		val.SetOptionCall(num4, Action.op_Implicit(action4));
		val.SetOptionCall(num5, Action.op_Implicit(action5));
		val.SetOptionCall(num6, Action.op_Implicit(action6));
		val.SetOptionCall(num7, Action.op_Implicit(action7));
		val.SetOptionCall(num8, Action.op_Implicit(action8));
		val.SetOptionCall(num9, Action.op_Implicit(action9));
		val.SetOptionCall(num10, Action.op_Implicit(action10));
		((UICustomDramaBase)val).dramaData.unitLeft = g.world.playerUnit;
		val.OpenUI();
	}

	public void SQCMD()
	{
		//IL_0008: Unknown result type (might be due to invalid IL or missing references)
		//IL_0159: Unknown result type (might be due to invalid IL or missing references)
		//IL_016f: Unknown result type (might be due to invalid IL or missing references)
		//IL_0185: Unknown result type (might be due to invalid IL or missing references)
		//IL_019b: Unknown result type (might be due to invalid IL or missing references)
		//IL_01b1: Unknown result type (might be due to invalid IL or missing references)
		//IL_01c8: Unknown result type (might be due to invalid IL or missing references)
		//IL_01df: Unknown result type (might be due to invalid IL or missing references)
		//IL_01f6: Unknown result type (might be due to invalid IL or missing references)
		//IL_020d: Unknown result type (might be due to invalid IL or missing references)
		//IL_021b: Unknown result type (might be due to invalid IL or missing references)
		//IL_0229: Unknown result type (might be due to invalid IL or missing references)
		//IL_0237: Unknown result type (might be due to invalid IL or missing references)
		//IL_0245: Unknown result type (might be due to invalid IL or missing references)
		//IL_0254: Unknown result type (might be due to invalid IL or missing references)
		//IL_0263: Unknown result type (might be due to invalid IL or missing references)
		//IL_0272: Unknown result type (might be due to invalid IL or missing references)
		//IL_0281: Unknown result type (might be due to invalid IL or missing references)
		UICustomDramaDyn val = new UICustomDramaDyn(ModMain.mod_ID + 9);
		int num = ModMain.mod_ID + 1;
		int num2 = ModMain.mod_ID + 2;
		int num3 = ModMain.mod_ID + 3;
		int num4 = ModMain.mod_ID + 4;
		int num5 = ModMain.mod_ID + 5;
		int num6 = ModMain.mod_ID + 6;
		int num7 = ModMain.mod_ID + 7;
		int num8 = ModMain.mod_ID + 8;
		Action action = delegate
		{
			if (g.data.world.animaWeapons != null)
			{
				if (!g.data.world.animaWeapons.Contains((GameAnimaWeapon)2))
				{
					g.data.world.animaWeapons.Clear();
					g.data.world.animaWeapons.Add((GameAnimaWeapon)2);
					UIMapMain uI = g.ui.GetUI<UIMapMain>(UIType.MapMain);
					if ((Object)(object)uI != (Object)null)
					{
						uI.uiPlayerInfo.UpdatePlayerInfo();
					}
					UITipItem.AddTip("昊天眼切换成功。", 2f);
				}
				else
				{
					UITipItem.AddTip("昊天眼已存在。", 2f);
				}
			}
		};
		Action action2 = delegate
		{
			if (g.data.world.animaWeapons != null)
			{
				if (!g.data.world.animaWeapons.Contains((GameAnimaWeapon)3))
				{
					g.data.world.animaWeapons.Clear();
					g.data.world.animaWeapons.Add((GameAnimaWeapon)3);
					UIMapMain uI = g.ui.GetUI<UIMapMain>(UIType.MapMain);
					if ((Object)(object)uI != (Object)null)
					{
						uI.uiPlayerInfo.UpdatePlayerInfo();
					}
					UITipItem.AddTip("炼妖壶切换成功。", 2f);
				}
				else
				{
					UITipItem.AddTip("炼妖壶已存在。", 2f);
				}
			}
		};
		Action action3 = delegate
		{
			if (g.data.world.animaWeapons != null)
			{
				if (!g.data.world.animaWeapons.Contains((GameAnimaWeapon)1))
				{
					g.data.world.animaWeapons.Clear();
					g.data.world.animaWeapons.Add((GameAnimaWeapon)1);
					UIMapMain uI = g.ui.GetUI<UIMapMain>(UIType.MapMain);
					if ((Object)(object)uI != (Object)null)
					{
						uI.uiPlayerInfo.UpdatePlayerInfo();
					}
					UITipItem.AddTip("双鱼佩切换成功。", 2f);
				}
				else
				{
					UITipItem.AddTip("双鱼佩已存在。", 2f);
				}
			}
		};
		Action action4 = delegate
		{
			if (g.data.world.piscesData != null)
			{
				if (g.data.world.resurgeCount <= 0)
				{
					World world = g.data.world;
					world.resurgeCount += 3;
					UITipItem.AddTip("双鱼佩修复成功。", 2f);
				}
				else
				{
					UITipItem.AddTip("双鱼佩未损坏。", 2f);
				}
			}
			else
			{
				UITipItem.AddTip("双鱼佩未激活", 2f);
			}
		};
		Action action5 = delegate
		{
			if (g.data.world.godEyeData != null)
			{
				if (g.data.world.godEyeData.isDamage || g.data.world.godEyeData.IsRepair())
				{
					g.data.world.godEyeData.Repair();
					g.data.world.godEyeData.number = 0;
					g.data.world.godEyeData.isDamage = false;
					g.data.world.godEyeData.repairMonth = 0;
					UITipItem.AddTip("昊天眼修复成功。", 2f);
				}
				else
				{
					UITipItem.AddTip("昊天眼未损坏。", 2f);
				}
			}
			else
			{
				UITipItem.AddTip("昊天眼未激活", 2f);
			}
		};
		Action action6 = delegate
		{
			if (g.data.world.devilDemonData != null)
			{
				if (g.data.world.devilDemonData.isDamage || g.data.world.devilDemonData.repairMonth > 0)
				{
					g.data.world.devilDemonData.isDamage = false;
					g.data.world.devilDemonData.repairMonth = 0;
					g.data.world.devilDemonData.brokenCount = 0;
					g.world.devilDemonMgr.devilDemonData.isDamage = false;
					g.world.devilDemonMgr.devilDemonData.repairMonth = 0;
					g.world.devilDemonMgr.devilDemonData.brokenCount = 0;
					UITipItem.AddTip("炼妖壶修复成功。", 2f);
				}
				else
				{
					UITipItem.AddTip("炼妖壶未损坏。", 2f);
				}
			}
			else
			{
				UITipItem.AddTip("炼妖壶未激活", 2f);
			}
		};
		Action action7 = delegate
		{
			World world = g.data.world;
			world.resurgeCount += 3;
			UITipItem.AddTip("复活点数+3", 2f);
		};
		Action action8 = delegate
		{
		};
		((UICustomDramaBase)val).dramaData.dialogueOptions[num] = "切换昊天眼";
		((UICustomDramaBase)val).dramaData.dialogueOptions[num2] = "切换炼妖壶";
		((UICustomDramaBase)val).dramaData.dialogueOptions[num3] = "切换双玉佩";
		((UICustomDramaBase)val).dramaData.dialogueOptions[num4] = "修复双玉佩";
		((UICustomDramaBase)val).dramaData.dialogueOptions[num5] = "修复昊天眼";
		((UICustomDramaBase)val).dramaData.dialogueOptions[num6] = "修复炼妖壶";
		((UICustomDramaBase)val).dramaData.dialogueOptions[num7] = "复活点数+3";
		((UICustomDramaBase)val).dramaData.dialogueOptions[num8] = "取消";
		val.SetOptionCall(num, Action.op_Implicit(action));
		val.SetOptionCall(num2, Action.op_Implicit(action2));
		val.SetOptionCall(num3, Action.op_Implicit(action3));
		val.SetOptionCall(num4, Action.op_Implicit(action4));
		val.SetOptionCall(num5, Action.op_Implicit(action5));
		val.SetOptionCall(num6, Action.op_Implicit(action6));
		val.SetOptionCall(num7, Action.op_Implicit(action7));
		val.SetOptionCall(num8, Action.op_Implicit(action8));
		((UICustomDramaBase)val).dramaData.unitLeft = g.world.playerUnit;
		val.OpenUI();
	}

	public void UpdataUI()
	{
		ToggleAllAction.GetComponent<Toggle>().isOn = ModMain.confInofFile.confInof.isCloseNpcAllAction;
		ToggleNPCAutoGrage.GetComponent<Toggle>().isOn = ModMain.confInofFile.confInof.isNPCAutoGrage;
		ToggleNPCAutoAttackUp.GetComponent<Toggle>().isOn = ModMain.confInofFile.confInof.isNPCAutoAttackUp;
		TogglePlayAutoAttackUp.GetComponent<Toggle>().isOn = ModMain.confInofFile.confInof.isPlayAutoAttackUp;
		ToggleChangeDropDis.GetComponent<Toggle>().isOn = ModMain.confInofFile.confInof.isChangeDropDis;
		ToggleBeKillAllDrop.GetComponent<Toggle>().isOn = ModMain.confInofFile.confInof.confDiySet.isBeKillAllDrop;
		ToggleSoulDevourSworduP.GetComponent<Toggle>().isOn = ModMain.confInofFile.confInof.isSoulDevourSworduP;
		ToggleAutoSendGradePropInSchool.GetComponent<Toggle>().isOn = ModMain.confInofFile.confInof.confDiySet.isAutoSendGradePropInSchool;
		ToggleOpenUnitDieTips.GetComponent<Toggle>().isOn = ModMain.confInofFile.confInof.confDiySet.isOpenUnitDieTips;
		ToggleisJudgeInti.GetComponent<Toggle>().isOn = ModMain.confInofFile.confInof.isJudgeInti;
		ToggleisActionSuccess.GetComponent<Toggle>().isOn = ModMain.confInofFile.confInof.confDiySet.isActionSuccess;
		ToggleisTaskAutoPut.GetComponent<Toggle>().isOn = ModMain.confInofFile.confInof.confDiySet.isAutoPostTask;
		ToggleisNPCTeam.GetComponent<Toggle>().isOn = ModMain.confInofFile.confInof.NPCTeam;
		ToggleisNPCHelp.GetComponent<Toggle>().isOn = ModMain.confInofFile.confInof.NPCHelp;
		ToggleisOpenFight.GetComponent<Toggle>().isOn = ModMain.confInofFile.confInof.NPCAutoFight;
		ToggleisOpenUnitLog.GetComponent<Toggle>().isOn = ModMain.confInofFile.confInof.AutoUnitLog;
		ToggleisOpenSkillExt.GetComponent<Toggle>().isOn = ModMain.confInofFile.confInof.SkillExt;
		InputFieldskillUpProp.GetComponent<InputField>().text = ModMain.confInofFile.confInof.skillUpProp.ToString();
		InputFieldnpcAutoGrage.GetComponent<InputField>().text = ModMain.confInofFile.confInof.baseProp.ToString();
		InputFieldNosuperHeroBae.GetComponent<InputField>().text = ModMain.confInofFile.confInof.NosuperHeroBae.ToString();
		InputFieldsuperHeroBae.GetComponent<InputField>().text = ModMain.confInofFile.confInof.superHeroBae.ToString();
		InputFieldnpcAutoAttackUp.GetComponent<InputField>().text = ModMain.confInofFile.confInof.npcAutoAttackUp.ToString();
		InputFieldplayAutoAttackUp.GetComponent<InputField>().text = ModMain.confInofFile.confInof.playAutoAttackUp.ToString();
		InputFieldluckProp.GetComponent<InputField>().text = ModMain.confInofFile.confInof.luckProp.ToString();
		InputFieldtalentProp.GetComponent<InputField>().text = ModMain.confInofFile.confInof.talentProp.ToString();
		InputFieldrunMaxMonth.GetComponent<InputField>().text = ModMain.confInofFile.confInof.runMaxMonth.ToString();
		InputFieldroundMonthProp.GetComponent<InputField>().text = ModMain.confInofFile.confInof.roundMonthProp.ToString();
		InputFieldgradeProp.GetComponent<InputField>().text = ModMain.confInofFile.confInof.gradeProp.ToString();
		InputFieldgradeDiminish.GetComponent<InputField>().text = ModMain.confInofFile.confInof.gradeDiminish.ToString();
		InputFieldupGreadItemMaxCount.GetComponent<InputField>().text = ModMain.confInofFile.confInof.upGreadItemMaxCount.ToString();
		InputFieldAutoAttackUpMaxCount.GetComponent<InputField>().text = ModMain.confInofFile.confInof.AutoAttackUpMaxCount.ToString();
		InputFieldAbovePlayGradeProp.GetComponent<InputField>().text = ModMain.confInofFile.confInof.AbovePlayGradeProp.ToString();
		InputFieldgradeAndAttackInimLimit1.GetComponent<InputField>().text = ModMain.confInofFile.confInof.gradeAndAttackInimLimit1.ToString();
		InputFieldgradeAndAttackInimLimit2.GetComponent<InputField>().text = ModMain.confInofFile.confInof.gradeAndAttackInimLimit2.ToString();
		InputFieldgradeAndAttackInimProp.GetComponent<InputField>().text = ModMain.confInofFile.confInof.gradeAndAttackInimProp.ToString();
		InputFieldunitActionProp.GetComponent<InputField>().text = ModMain.confInofFile.confInof.unitActionProp.ToString();
		InputFieldunitActionInimLimit1.GetComponent<InputField>().text = ModMain.confInofFile.confInof.unitActionInimLimit1.ToString();
		InputFieldunitActionInimLimit2.GetComponent<InputField>().text = ModMain.confInofFile.confInof.unitActionInimLimit2.ToString();
		InputFieldunitActionInimProp.GetComponent<InputField>().text = ModMain.confInofFile.confInof.unitActionInimProp.ToString();
		InputFieldNPCActionToPlayMaxCount.GetComponent<InputField>().text = ModMain.confInofFile.confInof.NPCActionToPlayMaxCount.ToString();
		InputFieldunitActionFreq.GetComponent<InputField>().text = ModMain.confInofFile.confInof.unitActionFreq.ToString();
		InputFieldunitActionMaxCount.GetComponent<InputField>().text = ModMain.confInofFile.confInof.unitActionMaxCount.ToString();
		InputFieldrunAIDate.GetComponent<InputField>().text = ModMain.confInofFile.confInof.runAIDate.ToString();
		InputFieldrunAIDateDiyDay.GetComponent<InputField>().text = ModMain.confInofFile.confInof.runAIDateDiyDay.ToString();
	}

	public void SetConfValue()
	{
		//IL_00c2: Unknown result type (might be due to invalid IL or missing references)
		//IL_00cc: Expected O, but got Unknown
		//IL_00e2: Unknown result type (might be due to invalid IL or missing references)
		//IL_00ec: Expected O, but got Unknown
		//IL_002a: Unknown result type (might be due to invalid IL or missing references)
		//IL_0034: Expected O, but got Unknown
		//IL_004a: Unknown result type (might be due to invalid IL or missing references)
		//IL_0054: Expected O, but got Unknown
		if (ModMain.confInofFile.confInof.isSoulDevourSworduP)
		{
			((ConfSoulDevourSwordParamerBase)g.conf.soulDevourSwordParamer)._swordDevourInterval = new ConfSoulDevourSwordParamerItem(17, "swordDevourInterval", "999999");
			((ConfSoulDevourSwordParamerBase)g.conf.soulDevourSwordParamer)._swordTargetHideRange = new ConfSoulDevourSwordParamerItem(24, "swordTargetHideRange", "0");
			List<ConfSoulDevourSwordDevourItem> allConfList = ((ConfSoulDevourSwordDevourBase)g.conf.soulDevourSwordDevour)._allConfList;
			for (int i = 0; i < allConfList.Count; i++)
			{
				allConfList[i].devourCD = 0;
			}
			g.world.soulDevourSword.data.nextDevourMonth = g.world.run.roundMonth;
		}
		else
		{
			((ConfSoulDevourSwordParamerBase)g.conf.soulDevourSwordParamer)._swordDevourInterval = new ConfSoulDevourSwordParamerItem(17, "swordDevourInterval", "24");
			((ConfSoulDevourSwordParamerBase)g.conf.soulDevourSwordParamer)._swordTargetHideRange = new ConfSoulDevourSwordParamerItem(24, "swordTargetHideRange", "5");
			List<ConfSoulDevourSwordDevourItem> allConfList2 = ((ConfSoulDevourSwordDevourBase)g.conf.soulDevourSwordDevour)._allConfList;
			for (int j = 0; j < allConfList2.Count; j++)
			{
				allConfList2[j].devourCD = 3;
			}
		}
		if (ModMain.confInofFile.confInof.confDiySet.isMarkNoCost)
		{
			List<ConfRoleMarkItem> allConfList3 = ((ConfRoleMarkBase)g.conf.roleMark)._allConfList;
			for (int k = 0; k < allConfList3.Count; k++)
			{
				allConfList3[k].needSp = 0;
				allConfList3[k].cost = 0;
			}
		}
		else
		{
			List<ConfRoleMarkItem> allConfList4 = ((ConfRoleMarkBase)g.conf.roleMark)._allConfList;
			if (allConfList4.Count == ModMain.confRoleMarkItems.Count)
			{
				for (int l = 0; l < allConfList4.Count; l++)
				{
					allConfList4[l].needSp = ModMain.confRoleMarkItems[l].needSp;
					allConfList4[l].cost = ModMain.confRoleMarkItems[l].cost;
				}
			}
		}
		if (ModMain.confInofFile.confInof.confDiySet.isEquipNoSp)
		{
			List<ConfArtifactShapeCommonItem> allConfList5 = ((ConfArtifactShapeCommonBase)g.conf.artifactShapeCommon)._allConfList;
			for (int m = 0; m < allConfList5.Count; m++)
			{
				allConfList5[m].equipSp = 0;
			}
		}
		else
		{
			List<ConfArtifactShapeCommonItem> allConfList6 = ((ConfArtifactShapeCommonBase)g.conf.artifactShapeCommon)._allConfList;
			if (allConfList6.Count == ModMain.confArtifactShapeCommonItems.Count)
			{
				for (int n = 0; n < allConfList6.Count; n++)
				{
					allConfList6[n].equipSp = ModMain.confArtifactShapeCommonItems[n].equipSp;
				}
			}
		}
		if (ModMain.confInofFile.confInof.confDiySet.isNoSpCost)
		{
			List<ConfArtifactShapeCommonItem> allConfList7 = ((ConfArtifactShapeCommonBase)g.conf.artifactShapeCommon)._allConfList;
			for (int num = 0; num < allConfList7.Count; num++)
			{
				allConfList7[num].spCost = 0;
			}
		}
		else
		{
			List<ConfArtifactShapeCommonItem> allConfList8 = ((ConfArtifactShapeCommonBase)g.conf.artifactShapeCommon)._allConfList;
			if (allConfList8.Count == ModMain.confArtifactShapeCommonItems.Count)
			{
				for (int num2 = 0; num2 < allConfList8.Count; num2++)
				{
					allConfList8[num2].spCost = ModMain.confArtifactShapeCommonItems[num2].spCost;
				}
			}
		}
		List<ConfBattleUnitLevelItem> allConfList9 = ((ConfBattleUnitLevelBase)g.conf.battleUnitLevel)._allConfList;
		if (allConfList9.Count == ModMain.confBattleUnitLevelItems.Count)
		{
			for (int num3 = 0; num3 < allConfList9.Count; num3++)
			{
				allConfList9[num3].atk = ModMain.confBattleUnitLevelItems[num3].atk * ModMain.confInofFile.confInof.confDiySet.UnitAtk;
				allConfList9[num3].def = ModMain.confBattleUnitLevelItems[num3].def * ModMain.confInofFile.confInof.confDiySet.UnitDef;
				allConfList9[num3].hp = ModMain.confBattleUnitLevelItems[num3].hp * ModMain.confInofFile.confInof.confDiySet.UnitHp;
				allConfList9[num3].pfr = ModMain.confBattleUnitLevelItems[num3].pfr * ModMain.confInofFile.confInof.confDiySet.Pmfr;
				allConfList9[num3].mfr = ModMain.confBattleUnitLevelItems[num3].mfr * ModMain.confInofFile.confInof.confDiySet.Pmfr;
				allConfList9[num3].crit = ModMain.confBattleUnitLevelItems[num3].crit * ModMain.confInofFile.confInof.confDiySet.Crit;
				allConfList9[num3].critV = ModMain.confBattleUnitLevelItems[num3].critV * ModMain.confInofFile.confInof.confDiySet.Crit;
				allConfList9[num3].guard = ModMain.confBattleUnitLevelItems[num3].guard * ModMain.confInofFile.confInof.confDiySet.Guard;
				allConfList9[num3].guardV = ModMain.confBattleUnitLevelItems[num3].guardV * ModMain.confInofFile.confInof.confDiySet.Guard;
				allConfList9[num3].skillDmg = ModMain.confBattleUnitLevelItems[num3].skillDmg * ModMain.confInofFile.confInof.confDiySet.SkillDmg;
			}
		}
		List<ConfBattleUnitAttrItem> allConfList10 = ((ConfBattleUnitAttrBase)g.conf.battleUnitAttr)._allConfList;
		if (allConfList10.Count == ModMain.confBattleUnitAttrItems.Count)
		{
			for (int num4 = 0; num4 < allConfList10.Count; num4++)
			{
				ConfBattleUnitBaseItem item = ((ConfBattleUnitBaseBase)g.conf.battleUnitBase).GetItem(allConfList10[num4].baseID);
				if (item != null)
				{
					switch (item.type)
					{
					case 1:
						allConfList10[num4].growPercent = (int)((float)ModMain.confBattleUnitAttrItems[num4].growPercent * ModMain.confInofFile.confInof.confDiySet.Type1);
						break;
					case 2:
						allConfList10[num4].growPercent = (int)((float)ModMain.confBattleUnitAttrItems[num4].growPercent * ModMain.confInofFile.confInof.confDiySet.Type2);
						break;
					case 3:
						allConfList10[num4].growPercent = (int)((float)ModMain.confBattleUnitAttrItems[num4].growPercent * ModMain.confInofFile.confInof.confDiySet.Type3);
						break;
					}
				}
			}
		}
		List<ConfRoleGradeItem> allConfList11 = ((ConfRoleGradeBase)g.conf.roleGrade)._allConfList;
		if (allConfList11.Count == ModMain.confRoleGradeItems.Count)
		{
			for (int num5 = 0; num5 < allConfList11.Count; num5++)
			{
				allConfList11[num5].exp = (int)((float)ModMain.confRoleGradeItems[num5].exp * ModMain.confInofFile.confInof.confDiySet.GradeExp);
			}
		}
		ModMain.SetNpcLcukValue();
		g.timer.Time(Action.op_Implicit((Action)delegate
		{
			//IL_006e: Unknown result type (might be due to invalid IL or missing references)
			//IL_0078: Expected O, but got Unknown
			//IL_0097: Unknown result type (might be due to invalid IL or missing references)
			//IL_00a1: Expected O, but got Unknown
			if (g.world.isIntoWorld)
			{
				List<WorldUnitBase> units = g.world.unit.GetUnits(true);
				units.Remove(g.world.playerUnit);
				Enumerator<WorldUnitBase> enumerator = units.GetEnumerator();
				while (enumerator.MoveNext())
				{
					WorldUnitBase current = enumerator.Current;
					DramaFunctionTool.OptionsFunction("delNPCLuck_" + current.data.unitData.unitID + "_" + (ModMain.mod_ID + 2), new DramaFunctionData(current));
					DramaFunctionTool.OptionsFunction("addNPCLuck_0_" + (ModMain.mod_ID + 2) + "_-1", new DramaFunctionData(current));
				}
				Console.WriteLine("刷新气运完成");
			}
		}), 0f, false);
	}
}


//==================== TYPE MOD_SSCYAI.UINPCAISet2.<>c ====================
// decompile failed: Could not find type definition MOD_SSCYAI.UINPCAISet2.<>c in type system.

//==================== TYPE MOD_SSCYAI.UINPCAISet.<>c ====================
// decompile failed: Could not find type definition MOD_SSCYAI.UINPCAISet.<>c in type system.

//==================== TYPE MOD_SSCYAI.UINPCAISet.<>c__DisplayClass60_0 ====================
// decompile failed: Could not find type definition MOD_SSCYAI.UINPCAISet.<>c__DisplayClass60_0 in type system.

