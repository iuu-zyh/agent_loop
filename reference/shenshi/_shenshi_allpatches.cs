//==================== TYPE UnitDiePatch ====================
using System;
using HarmonyLib;
using Il2CppSystem;
using MOD_SSCYAI;

[HarmonyPatch(typeof(WorldUnitBase), "Die")]
public class UnitDiePatch
{
	[HarmonyPrefix]
	public static bool Prefix(WorldUnitBase __instance)
	{
		if (g.mod.IsLoadMod("MOD_SSCYAI") && ModMain.confInofFile.confInof.confDiySet.isOpenUnitDieTips)
		{
			Fun(__instance);
		}
		return true;
	}

	public static void Fun(WorldUnitBase __instance)
	{
		if (__instance == null)
		{
			return;
		}
		try
		{
			Console.WriteLine(__instance.data.unitData.propertyData.GetName() + "已死亡。");
			if (((__instance.data.unitData.relationData.GetIntim(g.world.playerUnit.data.unitData.unitID) > g.world.playerUnit.data.unitData.relationData.GetIntim(__instance.data.unitData.unitID)) ? __instance.data.unitData.relationData.GetIntim(g.world.playerUnit.data.unitData.unitID) : g.world.playerUnit.data.unitData.relationData.GetIntim(__instance.data.unitData.unitID)) >= 120 || g.world.playerUnit.data.unitData.relationData.GetAllGoodRelationUnitID(true, false).Contains(__instance.data.unitData.unitID) || ModMain.shareUnit.Contains(__instance.data.unitData.unitID) || ModMain.letterUnitList.Contains(__instance.data.unitData.unitID) || g.data.world.marksUnitID.Contains(__instance.data.unitData.unitID))
			{
				Console.WriteLine(__instance.data.unitData.propertyData.GetName() + "属于亲友、关注好友、书信好友及小地图标记单位，正在生成传记。");
				ModMain.unitDies.Add(__instance);
				ModMain.letterUnitList.Remove(__instance.data.unitData.unitID);
				g.timer.Time(Action.op_Implicit((Action)async delegate
				{
					try
					{
						Console.WriteLine("触发NPC传记!");
						string text = $"[{g.world.run.roundMonth / 12 + 1}年{g.world.run.roundMonth % 12 + 1}月{g.world.run.roundDay + 1}日] ";
						string text2 = $"死亡位置及坐标为：{MartialUtil.getAreaName(__instance.data.unitData.pointGridData.areaBaseID)}({__instance.data.unitData.pointX},{__instance.data.unitData.pointY})";
						string text3 = CommandTool.RegexForPromat(ModMain.aiPrompt.Item.dieLetterPrompt);
						string question = text + __instance.data.unitData.propertyData.GetName() + ",你历经磨难却最终陨落," + text2 + ",请按设定撰写一本内容情感丰富的人生传记。" + text3;
						await CommandTool.SendToDeepSeek(__instance, "system", question, 3);
					}
					catch (Exception ex2)
					{
						Console.WriteLine(ex2.Message ?? "");
					}
				}), 0f, false);
			}
			else
			{
				Console.WriteLine(__instance.data.unitData.propertyData.GetName() + "属于非亲友，已自动忽略。");
			}
		}
		catch (Exception ex)
		{
			Console.WriteLine(ex.Message);
		}
	}
}


//==================== TYPE MOD_SSCYAI.SpecialFeedbackPatch ====================
using System;
using HarmonyLib;

namespace MOD_SSCYAI;

[HarmonyPatch(typeof(UnitActionFeedbackBase), "SpecialFeedback")]
public class SpecialFeedbackPatch
{
	[HarmonyPrefix]
	public static bool Prefix(ref UnitActionFeedbackBase __instance, ref WorldUnitBase fromUnit, ref int __result)
	{
		try
		{
			if ((ModMain.confInofFile.confInof.confDiySet.isActionSuccess || UnitActionAI.isAction) && fromUnit.data.unitData.unitID.Equals(g.world.playerUnit.data.unitData.unitID))
			{
				Console.WriteLine("AI交互判定成功");
				((UnitActionRoleBase)__instance).state = 1;
				__result = 1;
				return false;
			}
		}
		catch (Exception ex)
		{
			Console.WriteLine(ex.ToString());
		}
		return true;
	}
}


//==================== TYPE MOD_SSCYAI.DramaPackageFeedbackPatch ====================
using System;
using HarmonyLib;

namespace MOD_SSCYAI;

[HarmonyPatch(typeof(UnitActionFeedbackBase), "DramaPackageFeedback")]
public class DramaPackageFeedbackPatch
{
	[HarmonyPrefix]
	public static bool Prefix(ref UnitActionFeedbackBase __instance, ref WorldUnitBase fromUnit, ref int __result)
	{
		try
		{
			if ((ModMain.confInofFile.confInof.confDiySet.isActionSuccess || UnitActionAI.isAction) && fromUnit.data.unitData.unitID.Equals(g.world.playerUnit.data.unitData.unitID))
			{
				Console.WriteLine("AI交互判定成功");
				((UnitActionRoleBase)__instance).state = 1;
				__result = 1;
				return false;
			}
		}
		catch (Exception ex)
		{
			Console.WriteLine(ex.ToString());
		}
		return true;
	}
}


//==================== TYPE MOD_SSCYAI.PotmonUnitFeedbackPatch ====================
using System;
using HarmonyLib;

namespace MOD_SSCYAI;

[HarmonyPatch(typeof(UnitActionFeedbackBase), "PotmonUnitFeedback")]
public class PotmonUnitFeedbackPatch
{
	[HarmonyPrefix]
	public static bool Prefix(ref UnitActionFeedbackBase __instance, ref WorldUnitBase fromUnit, ref int __result)
	{
		try
		{
			if ((ModMain.confInofFile.confInof.confDiySet.isActionSuccess || UnitActionAI.isAction) && fromUnit.data.unitData.unitID.Equals(g.world.playerUnit.data.unitData.unitID))
			{
				Console.WriteLine("AI交互判定成功");
				((UnitActionRoleBase)__instance).state = 1;
				__result = 1;
				return false;
			}
		}
		catch (Exception ex)
		{
			Console.WriteLine(ex.ToString());
		}
		return true;
	}
}


//==================== TYPE MOD_SSCYAI.Patch_UIPlayerEmailInfo ====================
using System;
using System.IO;
using System.Windows.Forms;
using HarmonyLib;
using Il2CppSystem;
using TMPro;
using UnityEngine;
using UnityEngine.Events;
using UnityEngine.UI;

namespace MOD_SSCYAI;

[HarmonyPatch(typeof(UIPlayerEmailInfo))]
public class Patch_UIPlayerEmailInfo
{
	public static TimerCoroutine coroutine;

	public static bool isSpeek;

	[HarmonyPatch("UpdateUI")]
	[HarmonyPostfix]
	public static void UpdateUI_Postfix(UIPlayerEmailInfo __instance)
	{
		//IL_0228: Unknown result type (might be due to invalid IL or missing references)
		//IL_024d: Unknown result type (might be due to invalid IL or missing references)
		//IL_0257: Unknown result type (might be due to invalid IL or missing references)
		//IL_0116: Unknown result type (might be due to invalid IL or missing references)
		//IL_013b: Unknown result type (might be due to invalid IL or missing references)
		//IL_0145: Unknown result type (might be due to invalid IL or missing references)
		try
		{
			if (__instance == null)
			{
				Console.WriteLine("__instance UIPlayerEmailInfo为空");
				return;
			}
			WorldUnitMgr unit = g.world.unit;
			LetterData letterData = __instance.letterData;
			WorldUnitBase unit2 = unit.GetUnit((letterData != null) ? letterData.sendUnitID : null, true);
			if (unit2 != null && (Object)(object)((Component)((EmailInfo)__instance).btnGet).transform.parent.Find("SSCY_Email_Speek") == (Object)null)
			{
				GameObject obj = Object.Instantiate<GameObject>(((Component)((EmailInfo)__instance).btnGet).gameObject);
				Transform parent = ((Component)((EmailInfo)__instance).btnGet).transform.parent;
				obj.transform.SetParent(parent, false);
				obj.SetActive(true);
				obj.GetComponentInChildren<Text>().text = "朗读";
				obj.transform.localPosition = new Vector3(((Component)((EmailInfo)__instance).btnGet).transform.localPosition.x - 220f, ((Component)((EmailInfo)__instance).btnGet).transform.localPosition.y);
				((Object)obj).name = "SSCY_Email_Speek";
				Button componentInChildren = obj.GetComponentInChildren<Button>();
				((UnityEventBase)componentInChildren.onClick).RemoveAllListeners();
				((UnityEvent)componentInChildren.onClick).AddListener(UnityAction.op_Implicit((Action)delegate
				{
					try
					{
						string text = ((TMP_Text)((EmailInfo)__instance).ptextInfo).text;
						if (isSpeek)
						{
							UITipItem.AddTip("正在语音合成中，请勿重复点击。。。", 3f);
						}
						else
						{
							try
							{
								UITipItem.AddTip("正在尝试语音播放，请稍候。。。", 3f);
								g.timer.Time(Action.op_Implicit((Action)async delegate
								{
									isSpeek = true;
									WorldUnitBase unit3 = unit2;
									string content = text;
									string name = unit2.data.unitData.propertyData.GetName();
									LetterData letterData2 = __instance.letterData;
									await CommandTool.TextToSpeek(unit3, content, name + "-" + ((letterData2 != null) ? letterData2.soleID : null));
									if (!((DataObjectData)((DataBase<Object>)(object)g.data.dataObj).data).ContainsKey("filepath") && coroutine != null)
									{
										g.timer.Stop(coroutine);
										coroutine = null;
									}
								}), 0f, false);
								if (coroutine == null)
								{
									coroutine = g.timer.Time(Action.op_Implicit((Action)delegate
									{
										if (((DataObjectData)((DataBase<Object>)(object)g.data.dataObj).data).ContainsKey("filepath"))
										{
											string text2 = ((DataObjectData)((DataBase<Object>)(object)g.data.dataObj).data).GetString("filepath");
											if (File.Exists(text2))
											{
												try
												{
													Console.WriteLine("开始播放...");
													if (ModMain.mciSendString("stop my_mp3", null, 0, IntPtr.Zero) != 0)
													{
														Console.WriteLine("停止失败！");
													}
													if (ModMain.mciSendString("close my_mp3", null, 0, IntPtr.Zero) != 0)
													{
														Console.WriteLine("关闭失败！");
													}
													if (ModMain.mciSendString("open \"" + text2 + "\" type mpegvideo alias my_mp3", null, 0, IntPtr.Zero) != 0)
													{
														Console.WriteLine("无法打开音频文件！");
													}
													else
													{
														if (ModMain.mciSendString("play my_mp3", null, 0, IntPtr.Zero) != 0)
														{
															Console.WriteLine("播放失败！");
														}
														Console.WriteLine("播放命令结束。");
														g.timer.Stop(coroutine);
														coroutine = null;
													}
												}
												catch (Exception ex4)
												{
													Console.WriteLine(ex4.Message);
												}
											}
										}
									}), 0.3f, true);
								}
							}
							catch (Exception ex2)
							{
								Console.WriteLine(ex2.ToString());
							}
							isSpeek = false;
						}
					}
					catch (Exception ex3)
					{
						Console.WriteLine(ex3.Message);
					}
				}));
			}
			if (!((Object)(object)((Component)((EmailInfo)__instance).btnGet).transform.parent.Find("SSCY_Email_Copy") == (Object)null))
			{
				return;
			}
			GameObject obj2 = Object.Instantiate<GameObject>(((Component)((EmailInfo)__instance).btnGet).gameObject);
			Transform parent2 = ((Component)((EmailInfo)__instance).btnGet).transform.parent;
			obj2.transform.SetParent(parent2, false);
			obj2.SetActive(true);
			obj2.GetComponentInChildren<Text>().text = "全文";
			obj2.transform.localPosition = new Vector3(((Component)((EmailInfo)__instance).btnGet).transform.localPosition.x - 100f, ((Component)((EmailInfo)__instance).btnGet).transform.localPosition.y);
			((Object)obj2).name = "SSCY_Email_Copy";
			Button componentInChildren2 = obj2.GetComponentInChildren<Button>();
			((UnityEventBase)componentInChildren2.onClick).RemoveAllListeners();
			((UnityEvent)componentInChildren2.onClick).AddListener(UnityAction.op_Implicit((Action)delegate
			{
				try
				{
					string text = ((TMP_Text)((EmailInfo)__instance).ptextInfo).text;
					g.ui.OpenUI<UITextInfoLong>(UIType.TextInfoLong).InitData("查看完整内容", text, "复制", Action.op_Implicit((Action)delegate
					{
						Clipboard.SetDataObject(text, copy: true);
						UITipItem.AddTip("书信内容已复制", 3f);
					}), true);
				}
				catch (Exception ex2)
				{
					Console.WriteLine(ex2.Message);
				}
			}));
		}
		catch (Exception ex)
		{
			Console.WriteLine(ex.Message);
		}
	}
}


//==================== TYPE UnitDiePatch.<>c__DisplayClass1_0 ====================
// decompile failed: Could not find type definition UnitDiePatch.<>c__DisplayClass1_0 in type system.

//==================== TYPE MOD_SSCYAI.Patch_UIPlayerEmailInfo.<>c ====================
// decompile failed: Could not find type definition MOD_SSCYAI.Patch_UIPlayerEmailInfo.<>c in type system.

//==================== TYPE MOD_SSCYAI.Patch_UIPlayerEmailInfo.<>c__DisplayClass2_0 ====================
// decompile failed: Could not find type definition MOD_SSCYAI.Patch_UIPlayerEmailInfo.<>c__DisplayClass2_0 in type system.

//==================== TYPE MOD_SSCYAI.Patch_UIPlayerEmailInfo.<>c__DisplayClass2_1 ====================
// decompile failed: Could not find type definition MOD_SSCYAI.Patch_UIPlayerEmailInfo.<>c__DisplayClass2_1 in type system.

//==================== TYPE MOD_SSCYAI.Patch_UIPlayerEmailInfo.<>c__DisplayClass2_2 ====================
// decompile failed: Could not find type definition MOD_SSCYAI.Patch_UIPlayerEmailInfo.<>c__DisplayClass2_2 in type system.

//==================== TYPE MOD_SSCYAI.Patch_UIPlayerEmailInfo.<>c__DisplayClass2_3 ====================
// decompile failed: Could not find type definition MOD_SSCYAI.Patch_UIPlayerEmailInfo.<>c__DisplayClass2_3 in type system.

//==================== TYPE UnitDiePatch.<>c__DisplayClass1_0.<<Fun>b__0>d ====================
// decompile failed: Could not find type definition UnitDiePatch.<>c__DisplayClass1_0.<<Fun>b__0>d in type system.

//==================== TYPE MOD_SSCYAI.Patch_UIPlayerEmailInfo.<>c__DisplayClass2_2.<<UpdateUI_Postfix>b__1>d ====================
// decompile failed: Could not find type definition MOD_SSCYAI.Patch_UIPlayerEmailInfo.<>c__DisplayClass2_2.<<UpdateUI_Postfix>b__1>d in type system.

