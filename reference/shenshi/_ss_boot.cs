//==================== TYPE MOD_9CiDqJ.Class1 ====================
namespace MOD_9CiDqJ;

internal class Class1
{
}


//==================== TYPE MOD_9CiDqJ.Example ====================
using System;
using EBattleTypeData;
using Il2CppSystem;
using UnhollowerBaseLib;
using UnhollowerRuntimeLib;
using UnityEngine;

namespace MOD_9CiDqJ;

public class Example
{
	private void TestTimer()
	{
		TimerCoroutine cor1 = null;
		int frameCount = 0;
		cor1 = g.timer.Frame(Action.op_Implicit((Action)delegate
		{
			frameCount++;
			if (frameCount >= 10)
			{
				g.timer.Stop(cor1);
			}
		}), 1, true);
		ClassInjector.RegisterTypeInIl2Cpp<ExampleTestMono>();
		g.root.AddComponent<ExampleTestMono>();
	}

	private void TestEvents()
	{
		Action<ETypeData> val = Action<ETypeData>.op_Implicit((Action<ETypeData>)OnUnitHitDynIntHandler);
		g.events.On(EBattleType.UnitHitDynIntHandler, val, 0, false);
		g.events.Off(EBattleType.UnitHitDynIntHandler, val);
	}

	private void OnUnitHitDynIntHandler(ETypeData e)
	{
		UnitHitDynIntHandler val = ((Il2CppObjectBase)e).Cast<UnitHitDynIntHandler>();
		Debug.Log(Object.op_Implicit("战斗中被击的单位：" + val.hitUnit.data.name));
		val.dynV.baseValue = 0;
		val.dynV.ClearCall();
	}

	private void TestWorld()
	{
		//IL_003c: Unknown result type (might be due to invalid IL or missing references)
		//IL_0041: Unknown result type (might be due to invalid IL or missing references)
		//IL_004c: Unknown result type (might be due to invalid IL or missing references)
		//IL_005a: Expected O, but got Unknown
		//IL_0078: Unknown result type (might be due to invalid IL or missing references)
		//IL_0097: Unknown result type (might be due to invalid IL or missing references)
		//IL_00a1: Expected O, but got Unknown
		//IL_0107: Unknown result type (might be due to invalid IL or missing references)
		//IL_0112: Expected O, but got Unknown
		//IL_011d: Unknown result type (might be due to invalid IL or missing references)
		//IL_0128: Expected O, but got Unknown
		//IL_012e: Unknown result type (might be due to invalid IL or missing references)
		//IL_0133: Unknown result type (might be due to invalid IL or missing references)
		//IL_0143: Unknown result type (might be due to invalid IL or missing references)
		//IL_014f: Expected O, but got Unknown
		//IL_0155: Unknown result type (might be due to invalid IL or missing references)
		//IL_017a: Unknown result type (might be due to invalid IL or missing references)
		//IL_018b: Unknown result type (might be due to invalid IL or missing references)
		Debug.Log(Object.op_Implicit("玩家名字：" + g.world.playerUnit.data.unitData.propertyData.GetName()));
		g.world.battle.IntoBattle(new MonstData
		{
			id = 1011,
			level = 5
		}, (Action)null, (WorldBattleData)null);
		g.world.mapEvent.AddGridEvent(g.world.playerUnit.data.unitData.GetPoint(), 6, (DataObjectData)null, false, true);
		UnitConditionTool.Condition("grade_0_1_1", new UnitConditionData(g.world.playerUnit, (WorldUnitBase)null));
		DramaFunctionTool.OptionsFunction("openUI_" + UIType.PlayerInfo.uiName, (DramaFunctionData)null);
		BattleFunctionTool.OptionsFunction("createMonst_7210_0_0_3", (BattleFunctionData)null);
		WorldUnitBase unit = g.world.unit.GetUnit("NPCID_XXXXXX", true);
		Debug.Log(Object.op_Implicit(unit.data.unitData.propertyData.GetName() ?? ""));
		unit.CreateAction((UnitActionBase)new UnitActionLuckAdd(120), false);
		unit.CreateAction((UnitActionBase)new UnitActionRoleTrains(g.world.playerUnit), false);
		DramaTool.OpenDrama(610011, new DramaData
		{
			unitLeft = g.world.playerUnit,
			unitRight = null
		});
		UICustomDramaDyn val = new UICustomDramaDyn(610011);
		Action action = delegate
		{
		};
		val.SetOptionCall(6100111, Action.op_Implicit(action));
		((UICustomDramaBase)val).dramaData.dialogueOptionsAddText[1001] = "额外增加的按钮";
		val.OpenUI();
	}

	private void Battle()
	{
		//IL_003b: Unknown result type (might be due to invalid IL or missing references)
		//IL_005e: Unknown result type (might be due to invalid IL or missing references)
		//IL_0090: Unknown result type (might be due to invalid IL or missing references)
		//IL_009a: Expected O, but got Unknown
		((UnitCtrlBase)SceneType.battle.unit.CreateUnitHuman<UnitCtrlHumanNPC>(g.world.unit.GetUnit("NPCID_XXXXXX", true).data, (UnitType)2, (Action<UnitCtrlHumanNPC>)null, (ConfMgrVariant)null)).move.SetPosition(SceneType.battle.battleMap.roomCenterPosi);
		SceneType.battle.unit.CreateUnitMonst(7210, SceneType.battle.battleMap.roomCenterPosi, (UnitType)3, 0, (Action<UnitCtrlMonst>)null, (ConfMgrVariant)null);
		((UnitCtrlBase)SceneType.battle.battleMap.playerUnitCtrl).AddEffect(51612, (UnitCtrlBase)(object)SceneType.battle.battleMap.playerUnitCtrl, new SkillCreateData());
		SceneType.map.world.CloseAllUI(false, (Il2CppReferenceArray<UITypeBase>)null);
	}

	private void TestRes()
	{
		GameObject val = g.res.Load<GameObject>("Effect/Battle/Skill/jueyingjian");
		Debug.Log(Object.op_Implicit("这是绝影剑特效：" + ((Object)val).name));
	}

	private void TestSounds()
	{
		g.sounds.PlayEffect("Battle/jineng/jian/jueyingjian", 1, (Action<SoundsEffect>)null, (Action)null, true);
		Debug.Log(Object.op_Implicit("这是绝影剑的声音"));
	}

	private void TestConf()
	{
		ConfRoleGradeItem gradeItem = g.conf.roleGrade.GetGradeItem(1, 1);
		Debug.Log(Object.op_Implicit("这是练气前期配置：" + GameTool.LS(gradeItem.gradeName)));
	}

	private void TestData()
	{
		Debug.Log(Object.op_Implicit("这是玩家名字：" + g.data.unit.GetUnit(g.data.world.playerUnitID).propertyData.GetName()));
	}

	private void TestUI()
	{
		//IL_00b7: Unknown result type (might be due to invalid IL or missing references)
		//IL_00c1: Expected O, but got Unknown
		g.ui.OpenUI(UIType.PlayerInfo);
		UITipItem.AddTip("提示信息", 0f);
		g.ui.OpenUI<UICheckPopup>(UIType.CheckPopup).InitData("提示", "这是有两个按钮的提示框", 2, Action.op_Implicit((Action)delegate
		{
			Debug.Log(Object.op_Implicit("点击了确定"));
		}), (Action)null);
		g.ui.OpenUI<UITextInfo>(UIType.TextInfo).InitData("提示", "这是文本信息", "", (Action)null);
		g.ui.OpenUI<UITextInfoLong>(UIType.TextInfoLong).InitData("提示", "这是很长的文本信息", "", (Action)null, false);
		g.ui.OpenUI(new UITypeBase("UI预制体名称", (UILayer)0));
	}

	private void TestTool()
	{
		Debug.Log(Object.op_Implicit("随机一个数值0-9：" + CommonTool.Random(0, 10, (RandomSeedData)null)));
		Debug.Log(Object.op_Implicit("读取国际化表文本“提示”：" + GameTool.LS("common_tishi")));
	}
}


//==================== TYPE MOD_9CiDqJ.ExampleTestMono ====================
using System;
using Il2CppSystem;
using UnityEngine;

namespace MOD_9CiDqJ;

public class ExampleTestMono : MonoBehaviour
{
	public ExampleTestMono(IntPtr ptr)
		: base(ptr)
	{
	}

	private void Update()
	{
		Debug.Log(Object.op_Implicit(Time.frameCount + "，每帧打印"));
		Console.WriteLine(Time.frameCount + "，每帧打印");
	}
}


//==================== TYPE MOD_9CiDqJ.ModMain ====================
using System;
using System.IO;
using System.Reflection;
using System.Security.Cryptography;
using HarmonyLib;

namespace MOD_9CiDqJ;

public class ModMain
{
	private static Harmony harmony;

	private static Assembly assembly;

	private static Type type;

	private static MethodInfo mInit;

	private static MethodInfo mDestroy;

	public void Init(string Path)
	{
		//IL_0023: Unknown result type (might be due to invalid IL or missing references)
		//IL_002d: Expected O, but got Unknown
		if (harmony != null)
		{
			harmony.UnpatchSelf();
			harmony = null;
		}
		if (harmony == null)
		{
			harmony = new Harmony("MOD_9CiDqJ");
		}
		harmony.PatchAll(Assembly.GetExecutingAssembly());
		InitDll(Path);
	}

	public void Destroy()
	{
		DestroyDll();
	}

	public void InitDll(string path)
	{
		byte[] array = DF(path + "\\ModAssets\\assert1.bin", RD("MOD_9CiDqJ.Value.123456789"), RD("MOD_9CiDqJ.Value.987654321"));
		if (array != null)
		{
			assembly = Assembly.Load(array);
			if (assembly != null)
			{
				type = assembly.GetType("MOD_SSCYAI.ModMain");
				if (type != null)
				{
					object obj = Activator.CreateInstance(type);
					mInit = type.GetMethod("Init");
					if (mInit != null)
					{
						mInit.Invoke(obj, new object[0]);
					}
					else
					{
						Console.WriteLine("m错");
					}
					mDestroy = type.GetMethod("Destroy");
					if (!(mDestroy != null))
					{
						Console.WriteLine("d错误");
					}
				}
				else
				{
					Console.WriteLine("t错");
				}
			}
			else
			{
				Console.WriteLine("a错");
			}
		}
		else
		{
			Console.WriteLine("assemblyBytes为空");
		}
	}

	public static byte[] DF(string inputFile, byte[] key, byte[] iv)
	{
		if (key == null)
		{
			Console.WriteLine("Key为空");
			return null;
		}
		if (iv == null)
		{
			Console.WriteLine("iv为空");
			return null;
		}
		string path = "t.t";
		using (Aes aes = Aes.Create())
		{
			aes.Key = key;
			aes.IV = iv;
			using FileStream stream = new FileStream(inputFile, FileMode.Open, FileAccess.Read);
			using FileStream fileStream = new FileStream(path, FileMode.Create, FileAccess.Write);
			using CryptoStream cryptoStream = new CryptoStream(stream, aes.CreateDecryptor(), CryptoStreamMode.Read);
			byte[] array = new byte[4096];
			int count;
			while ((count = cryptoStream.Read(array, 0, array.Length)) > 0)
			{
				fileStream.Write(array, 0, count);
			}
		}
		byte[] result = File.ReadAllBytes(path);
		File.Delete(path);
		return result;
	}

	public static byte[] RD(string resourceName)
	{
		using Stream stream = Assembly.GetExecutingAssembly().GetManifestResourceStream(resourceName);
		if (stream == null)
		{
			throw new ArgumentException("资源不存在");
		}
		using MemoryStream memoryStream = new MemoryStream();
		stream.CopyTo(memoryStream);
		return memoryStream.ToArray();
	}

	public void DestroyDll()
	{
		if (type != null)
		{
			object obj = Activator.CreateInstance(type);
			if (mDestroy != null)
			{
				mDestroy.Invoke(obj, new object[0]);
			}
			else
			{
				Console.WriteLine("m错");
			}
		}
		else
		{
			Console.WriteLine("T错");
		}
	}
}


//==================== TYPE MOD_9CiDqJ.Example.<>c ====================
// decompile failed: Could not find type definition MOD_9CiDqJ.Example.<>c in type system.

//==================== TYPE MOD_9CiDqJ.Example.<>c__DisplayClass1_0 ====================
// decompile failed: Could not find type definition MOD_9CiDqJ.Example.<>c__DisplayClass1_0 in type system.

