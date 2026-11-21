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

//==================== TYPE MOD_SSCYAI.Patch_UIPlayerEmailInfo.<>c__DisplayClass2_2.<<UpdateUI_Postfix>b__1>d ====================
// decompile failed: Could not find type definition MOD_SSCYAI.Patch_UIPlayerEmailInfo.<>c__DisplayClass2_2.<<UpdateUI_Postfix>b__1>d in type system.

