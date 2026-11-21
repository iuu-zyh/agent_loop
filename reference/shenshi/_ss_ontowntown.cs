//==================== TYPE MOD_SSCYAI.OnOpenTown ====================
using System;
using EGameTypeData;
using Il2CppSystem;
using UnhollowerRuntimeLib;
using UnityEngine;
using UnityEngine.Events;
using UnityEngine.UI;

namespace MOD_SSCYAI;

public class OnOpenTown
{
	public OnOpenTown(OpenUIEnd edata)
	{
		//IL_007a: Unknown result type (might be due to invalid IL or missing references)
		//IL_0089: Unknown result type (might be due to invalid IL or missing references)
		base..ctor();
		UITown uI = g.ui.GetUI<UITown>(UIType.Town);
		if ((Object)(object)uI != (Object)null)
		{
			GameObject obj = Object.Instantiate<GameObject>(((UITownBase)uI).goBuildItem.gameObject);
			obj.transform.SetParent(((UITownBase)uI).goPosiRoot.transform.parent, false);
			obj.SetActive(true);
			obj.GetComponent<RectTransform>().anchoredPosition = GetPosition(g.data.grid.GetGridData(g.world.playerUnit.data.unitData.GetPoint()).areaBaseID);
			obj.GetComponentInChildren<Text>().text = "区域排行榜";
			Action action = delegate
			{
				OnClickIntoFarm();
			};
			((UnityEvent)obj.GetComponentInChildren<Button>().onClick).AddListener(UnityAction.op_Implicit(action));
			obj.GetComponentInChildren<Image>().sprite = SpriteTool.GetSprite("TownCommon", "Build2014");
			obj.GetComponentInChildren<Image>().SetNativeSize();
		}
	}

	private Vector2 GetPosition(int area)
	{
		//IL_0013: Unknown result type (might be due to invalid IL or missing references)
		//IL_0018: Unknown result type (might be due to invalid IL or missing references)
		//IL_0029: Unknown result type (might be due to invalid IL or missing references)
		//IL_002e: Unknown result type (might be due to invalid IL or missing references)
		//IL_003f: Unknown result type (might be due to invalid IL or missing references)
		//IL_0044: Unknown result type (might be due to invalid IL or missing references)
		//IL_0055: Unknown result type (might be due to invalid IL or missing references)
		//IL_005a: Unknown result type (might be due to invalid IL or missing references)
		//IL_006b: Unknown result type (might be due to invalid IL or missing references)
		//IL_0070: Unknown result type (might be due to invalid IL or missing references)
		//IL_0082: Unknown result type (might be due to invalid IL or missing references)
		//IL_0087: Unknown result type (might be due to invalid IL or missing references)
		//IL_0099: Unknown result type (might be due to invalid IL or missing references)
		//IL_009e: Unknown result type (might be due to invalid IL or missing references)
		//IL_00b8: Unknown result type (might be due to invalid IL or missing references)
		//IL_00c0: Unknown result type (might be due to invalid IL or missing references)
		Vector2[] array = (Vector2[])(object)new Vector2[11]
		{
			default(Vector2),
			new Vector2(-300f, 220f),
			new Vector2(-300f, 220f),
			default(Vector2),
			new Vector2(-300f, 220f),
			default(Vector2),
			new Vector2(-553f, 220f),
			default(Vector2),
			new Vector2(0f, 220f),
			new Vector2(500f, 200f),
			new Vector2(500f, 200f)
		};
		if (area < 0 || area >= array.Length)
		{
			return new Vector2(-602.9f, -602.9f);
		}
		return array[area];
	}

	private void OnClickIntoFarm()
	{
		//IL_0048: Unknown result type (might be due to invalid IL or missing references)
		//IL_0052: Expected O, but got Unknown
		//IL_0017: Unknown result type (might be due to invalid IL or missing references)
		//IL_0021: Expected O, but got Unknown
		try
		{
			if (ModMain.isSortEnd)
			{
				ClassInjector.RegisterTypeInIl2Cpp<UISortArea>();
				((Component)g.ui.OpenUI(new UITypeBase("UISortArea", (UILayer)0))).gameObject.AddComponent<UISortArea>().InitData();
				return;
			}
			UITipItem.AddTip("排行榜尚正在更新，请稍后再试！", 2f);
			MultiThreadTask multiThreadTask = new MultiThreadTask(false);
			multiThreadTask.AddCall(Action.op_Implicit((Action)ModMain.OnSort));
			Action action = delegate
			{
				multiThreadTask.Stop();
				Console.WriteLine("排名结束！" + multiThreadTask.isStop);
				UITipItem.AddTip("排行榜已更新完毕！", 2f);
			};
			multiThreadTask.Run(true, Action.op_Implicit(action));
		}
		catch (Exception ex)
		{
			Console.WriteLine(ex.ToString());
			Console.WriteLine("添加出错");
		}
	}
}


//==================== TYPE MOD_SSCYAI.OnOpenTown.<>c__DisplayClass2_0 ====================
// decompile failed: Could not find type definition MOD_SSCYAI.OnOpenTown.<>c__DisplayClass2_0 in type system.

