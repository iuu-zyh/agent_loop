//==================== TYPE MOD_SSCYAI.UIChatAINewGroup ====================
using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading.Tasks;
using System.Web.WebPages;
using System.Windows.Forms;
using Il2CppSystem;
using Il2CppSystem.Collections.Generic;
using Newtonsoft.Json;
using UnhollowerRuntimeLib;
using UnityEngine;
using UnityEngine.Events;
using UnityEngine.UI;

namespace MOD_SSCYAI;

public class UIChatAINewGroup(IntPtr ptr) : UIBase(ptr)
{
	private static readonly Random _random = new Random(Guid.NewGuid().GetHashCode());

	private TimerCoroutine corUpdate;

	public List<WorldUnitBase> units;

	public GameObject ButtonSend;

	public GameObject ButtonClear;

	public GameObject InputText;

	public GameObject OutPutContent;

	public GameObject ButtonToLog;

	public GameObject ToggleChat;

	public GameObject ToggleAction;

	public GameObject ButtonRandomOP;

	public GameObject goPreText;

	public GameObject ButtonAdd;

	public GameObject ButtonDel;

	public GameObject ButtonModity;

	public GameObject TextPreActionName;

	public GameObject ToggleShowLeft;

	public GameObject ButtonClearAction;

	public GameObject DropdownType;

	public GameObject InputFieldFind;

	public GameObject ButtonFind;

	public bool isChat = true;

	public int index = 3;

	public int state;

	public bool isUpdate;

	public bool isUpdateLog;

	public bool isComplete;

	public WorldUnitBase curUnit;

	public string textNewMsg;

	public string textOldMsg;

	public static HttpClient client = new HttpClient
	{
		Timeout = TimeSpan.FromSeconds(300.0)
	};

	public static HttpClient ActionClient = new HttpClient
	{
		Timeout = TimeSpan.FromSeconds(300.0)
	};

	public Sprite SpeicalPngSprite;

	public int selIndex = -1;

	public static string baseActionStr = "行为请求：";

	public static AIActionMessage aIActionMessage = null;

	public static string rspTextTemp = "";

	public TimerCoroutine actionThread;

	public string time = "";

	public bool isSpeek;

	public static TimerCoroutine coroutine = null;

	public static TimerCoroutine sendMsgThread = null;

	public GameObject GroupNameList;

	public GameObject GroupUnitList;

	public int statIndex;

	public ConcurrentDictionary<string, string> msgText = new ConcurrentDictionary<string, string>();

	public ConcurrentDictionary<string, GameObject> msgObject = new ConcurrentDictionary<string, GameObject>();

	public int preCount = 10;

	public int curPage = -1;

	public int selPreTextIndex = -1;

	public int selPreIndex = -1;

	public static TimerCoroutine groupThread = null;

	public void InitAI()
	{
		Console.WriteLine("InitAI");
		try
		{
			if (selIndex < 0 || selIndex >= ModMain.chatGroup.Groups.Count)
			{
				return;
			}
			if (isChat)
			{
				Console.WriteLine("message数量：" + ModMain.chatGroup.Groups[selIndex].Messages.Count);
			}
			else
			{
				Console.WriteLine("message数量：" + ModMain.chatGroup.Groups[selIndex].ActionMessage.Count);
			}
			string text = CommandTool.RegexForPromat(ModMain.aIPromptCommand.promptFile.worldGroupChatPrompt);
			text = text.Replace("{{user}}", g.world.playerUnit.GetName());
			if (StringExtensions.IsEmpty(text))
			{
				text = "鬼谷八荒世界观";
			}
			GroupMessage groupMessage = new GroupMessage
			{
				role = "system",
				content = text
			};
			string text2 = "当前群组名称：" + ModMain.chatGroup.Groups[selIndex].Name + "\n当前群组简介：" + ModMain.chatGroup.Groups[selIndex].Description + "\n当前群组内成员：";
			foreach (string item in ModMain.chatGroup.Groups[selIndex].UnitsID)
			{
				string text3 = text2;
				WorldUnitBase unit = g.world.unit.GetUnit(item, true);
				text2 = text3 + ((unit != null) ? unit.data.unitData.propertyData.GetName() : null) + "  ";
			}
			GroupMessage groupMessage2 = new GroupMessage
			{
				role = "system",
				content = text2
			};
			string content = string.Format("{0}", CommandTool.GetUnitInfo("[用户(玩家）身份信息]", g.world.playerUnit, null));
			GroupMessage groupMessage3 = new GroupMessage
			{
				role = "system",
				content = content
			};
			string text4 = "[群友基础信息]\n";
			foreach (string item2 in ModMain.chatGroup.Groups[selIndex].UnitsID)
			{
				WorldUnitBase unit2 = g.world.unit.GetUnit(item2, true);
				if (unit2 != null && !unit2.data.unitData.unitID.Equals(g.world.playerUnit.data.unitData.unitID))
				{
					text4 = text4 + CommandTool.GetUnitShortInfo("[" + unit2.data.unitData.propertyData.GetName() + "的身份信息]", unit2, null) + "\n";
				}
			}
			GroupMessage groupMessage4 = new GroupMessage
			{
				role = "system",
				content = text4
			};
			if (ModMain.chatGroup.Groups[selIndex].Messages.Count >= 4)
			{
				ModMain.chatGroup.Groups[selIndex].Messages[0] = groupMessage;
				ModMain.chatGroup.Groups[selIndex].Messages[1] = groupMessage2;
				ModMain.chatGroup.Groups[selIndex].Messages[2] = groupMessage3;
				ModMain.chatGroup.Groups[selIndex].Messages[3] = groupMessage4;
			}
			else
			{
				ModMain.chatGroup.Groups[selIndex].Messages.Clear();
				ModMain.chatGroup.Groups[selIndex].Messages.Add(groupMessage);
				ModMain.chatGroup.Groups[selIndex].Messages.Add(groupMessage2);
				ModMain.chatGroup.Groups[selIndex].Messages.Add(groupMessage3);
				ModMain.chatGroup.Groups[selIndex].Messages.Add(groupMessage4);
			}
			AIUnitSet aIUnitSet = CommandTool.GetAIUnitSet(g.world.playerUnit);
			ActionList actionList = new ActionList();
			string actionPrompt = actionList.GetActionPrompt(isGroup: true, isAddExp: true, aIUnitSet.randomOP == 1);
			if (ModMain.chatGroup.Groups[selIndex].ActionMessage.Count > 8)
			{
				actionPrompt = actionList.GetActionPrompt(isGroup: true, isAddExp: false, aIUnitSet.randomOP == 1);
			}
			actionPrompt = actionPrompt.Replace("{{user}}", g.world.playerUnit.GetName());
			GroupMessage groupMessage5 = new GroupMessage
			{
				role = "system",
				content = actionPrompt
			};
			if (ModMain.chatGroup.Groups[selIndex].ActionMessage.Count >= 3)
			{
				ModMain.chatGroup.Groups[selIndex].ActionMessage[0] = groupMessage5;
				ModMain.chatGroup.Groups[selIndex].ActionMessage[1] = groupMessage2;
				ModMain.chatGroup.Groups[selIndex].ActionMessage[2] = groupMessage3;
				ModMain.chatGroup.Groups[selIndex].ActionMessage[3] = groupMessage4;
			}
			else
			{
				ModMain.chatGroup.Groups[selIndex].ActionMessage.Clear();
				ModMain.chatGroup.Groups[selIndex].ActionMessage.Add(groupMessage5);
				ModMain.chatGroup.Groups[selIndex].ActionMessage.Add(groupMessage2);
				ModMain.chatGroup.Groups[selIndex].ActionMessage.Add(groupMessage3);
				ModMain.chatGroup.Groups[selIndex].ActionMessage.Add(groupMessage4);
			}
			Console.WriteLine("群聊模式：AI行动提示语长度：\n" + actionPrompt.Length);
		}
		catch (Exception ex)
		{
			Console.WriteLine(ex.ToString());
			Console.WriteLine("prompt出错");
		}
	}

	public void SenndMessage()
	{
		//IL_0329: Unknown result type (might be due to invalid IL or missing references)
		//IL_0335: Unknown result type (might be due to invalid IL or missing references)
		//IL_0345: Unknown result type (might be due to invalid IL or missing references)
		//IL_03aa: Unknown result type (might be due to invalid IL or missing references)
		//IL_03b0: Unknown result type (might be due to invalid IL or missing references)
		//IL_0449: Unknown result type (might be due to invalid IL or missing references)
		//IL_044f: Unknown result type (might be due to invalid IL or missing references)
		//IL_04ed: Unknown result type (might be due to invalid IL or missing references)
		//IL_04f3: Unknown result type (might be due to invalid IL or missing references)
		try
		{
			if (!((Selectable)ButtonSend.GetComponent<Button>()).interactable)
			{
				return;
			}
			textNewMsg = InputText.GetComponent<InputField>().text;
			if (string.IsNullOrEmpty(textNewMsg.Trim()))
			{
				g.ui.OpenUI<UITextInfo>(UIType.TextInfo).InitData("提示", "输入内容为空。", "", (Action)null);
				return;
			}
			if (selIndex < 0 || selIndex >= ModMain.chatGroup.Groups.Count)
			{
				g.ui.OpenUI<UITextInfo>(UIType.TextInfo).InitData("提示", "待发送消息的群组选择错误。", "", (Action)null);
				return;
			}
			List<WorldUnitBase> units = UpdataNPCInfo();
			if (units.Count <= 0)
			{
				g.ui.OpenUI<UITextInfo>(UIType.TextInfo).InitData("提示", "选择的NPC列表为空。", "", (Action)null);
				return;
			}
			textNewMsg = textNewMsg.TrimEnd('\r', '\n');
			curPage = -1;
			UpdateMessageList();
			textOldMsg = "";
			string prefix = $"[{CommandTool.ConvertToYearsMonths(g.world.run.roundMonth + 1)}{g.world.run.roundDay + 1}日]";
			InputText.GetComponent<InputField>().text = "";
			string text = prefix + textNewMsg;
			string content = "";
			string content2 = CommandTool.GetContent(text, out content);
			if (!time.Equals(content2))
			{
				time = content2;
				((Component)Object.Instantiate<GameObject>(g.res.Load<GameObject>("UI/msgItemTime"), OutPutContent.transform).transform.Find("Text")).GetComponent<Text>().text = content2;
			}
			GameObject ChatItem = Object.Instantiate<GameObject>(g.res.Load<GameObject>("UI/msgItemGroup"), OutPutContent.transform);
			if ((Object)(object)ChatItem != (Object)null)
			{
				index++;
				int tempIndex = index;
				((Component)ChatItem.transform.Find("Left/Text")).GetComponent<Text>().text = content;
				((Component)ChatItem.transform.Find("Left/TextName")).GetComponent<Text>().text = g.world.playerUnit.data.unitData.propertyData.GetName();
				((Component)ChatItem.transform.Find("Right")).gameObject.SetActive(false);
				RectTransform component = ((Component)ChatItem.transform.Find("Left/Text")).GetComponent<RectTransform>();
				LayoutRebuilder.ForceRebuildLayoutImmediate(component);
				RectTransform component2 = ((Component)ChatItem.transform).GetComponent<RectTransform>();
				component2.sizeDelta = new Vector2(component2.sizeDelta.x, component.sizeDelta.y + 100f);
				ScrollRect component3 = ((Component)((Component)this).transform.Find("Root/PanelMain/OutPut/Panel/Scroll View")).GetComponent<ScrollRect>();
				LayoutRebuilder.ForceRebuildLayoutImmediate(component3.content);
				Canvas.ForceUpdateCanvases();
				component3.verticalNormalizedPosition = 0f;
				((Component)ChatItem.transform.Find("Left/ButtonDel")).gameObject.AddComponent<UISkyTipEffect>().InitData("删除该条聊天记录", default(Vector3));
				((UnityEventBase)((Component)ChatItem.transform.Find("Left/ButtonDel")).GetComponent<Button>().onClick).RemoveAllListeners();
				((UnityEvent)((Component)ChatItem.transform.Find("Left/ButtonDel")).GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
				{
					g.ui.OpenUI<UICheckPopup>(UIType.CheckPopup).InitData("提示", "是否确认删除该条聊天记录？", 2, Action.op_Implicit((Action)delegate
					{
						try
						{
							if (isChat)
							{
								if (tempIndex < ModMain.chatGroup.Groups[selIndex].Messages.Count)
								{
									ModMain.chatGroup.Groups[selIndex].Messages.RemoveAt(tempIndex);
								}
								if (tempIndex < ModMain.chatGroup.Groups[selIndex].ActionMessage.Count)
								{
									ModMain.chatGroup.Groups[selIndex].ActionMessage.RemoveAt(tempIndex);
								}
							}
							else
							{
								if (tempIndex < ModMain.chatGroup.Groups[selIndex].Messages.Count)
								{
									ModMain.chatGroup.Groups[selIndex].Messages.RemoveAt(tempIndex);
								}
								if (tempIndex < ModMain.chatGroup.Groups[selIndex].ActionMessage.Count)
								{
									ModMain.chatGroup.Groups[selIndex].ActionMessage.RemoveAt(tempIndex);
								}
							}
							UpdateMessageList();
						}
						catch (Exception ex3)
						{
							Console.WriteLine(ex3.Message);
						}
					}), (Action)null);
				}));
				((Component)ChatItem.transform.Find("Left/ButtonCopy")).gameObject.AddComponent<UISkyTipEffect>().InitData("复制该条聊天记录", default(Vector3));
				((UnityEventBase)((Component)ChatItem.transform.Find("Left/ButtonCopy")).GetComponent<Button>().onClick).RemoveAllListeners();
				((UnityEvent)((Component)ChatItem.transform.Find("Left/ButtonCopy")).GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
				{
					try
					{
						Clipboard.SetDataObject(((Component)ChatItem.transform.Find("Left/Text")).GetComponent<Text>().text, copy: true);
					}
					catch (Exception ex3)
					{
						Console.WriteLine(ex3.Message);
					}
				}));
				((Component)ChatItem.transform.Find("Left/ButtonModity")).gameObject.AddComponent<UISkyTipEffect>().InitData("修改该条聊天记录", default(Vector3));
				((UnityEventBase)((Component)ChatItem.transform.Find("Left/ButtonModity")).GetComponent<Button>().onClick).RemoveAllListeners();
				((UnityEvent)((Component)ChatItem.transform.Find("Left/ButtonModity")).GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
				{
					ModityChatMessage(tempIndex, isUpdata: true);
				}));
				bool isAction = false;
				try
				{
					GroupMessage item = new GroupMessage
					{
						role = "user",
						content = text,
						name = g.world.playerUnit.data.unitData.propertyData.GetName()
					};
					ModMain.chatGroup.Groups[selIndex].Messages.Add(item);
					ModMain.chatGroup.Groups[selIndex].ActionMessage.Add(item);
					int i = 0;
					bool isNext = true;
					msgObject.Clear();
					msgText.Clear();
					string unitID = "";
					Console.WriteLine("开始批量发送消息,待发送NPC的数量：" + units.Count);
					((Selectable)ButtonSend.GetComponent<Button>()).interactable = false;
					string userMsg = "";
					groupThread = g.timer.Time(Action.op_Implicit((Action)delegate
					{
						try
						{
							ThreadSafeQueue.Enqueue(delegate
							{
								if (i >= units.Count)
								{
									Console.WriteLine("本轮群聊消息发送结束");
									ModMain.chatGroup.SaveGroup();
									g.world.unitLog.SaveLogData();
									if (ModMain.chatGroup.Groups[selIndex].isNPCChat)
									{
										if (_random.Next(0, 100) < ModMain.chatGroup.Groups[selIndex].prob)
										{
											Console.WriteLine("NPC自由发言触发");
											i = 0;
										}
										else
										{
											g.timer.Stop(groupThread);
											((Selectable)ButtonSend.GetComponent<Button>()).interactable = true;
										}
									}
									else
									{
										g.timer.Stop(groupThread);
										((Selectable)ButtonSend.GetComponent<Button>()).interactable = true;
									}
								}
								int temp = i;
								if (isNext && temp < units.Count)
								{
									isNext = false;
									unitID = units[temp].data.unitData.unitID;
									if (g.world.unit.GetUnit(unitID, true) != null)
									{
										UIChatAINewGroup.aIActionMessage = null;
										userMsg = prefix + "现在轮到" + units[temp].data.unitData.propertyData.GetName() + "说话，请你根据他的身份设定，结合上下文内容选择合适的道友进行自由回答，增加交互沉浸感和代入感，回答时避免内容前后重复。";
										string text2 = string.Format("{0}\n", CommandTool.GetUnitInfo("[" + units[temp].data.unitData.propertyData.GetName() + "的详细身份信息]", units[temp], g.world.playerUnit));
										userMsg = userMsg + "\n" + text2;
										Console.WriteLine("补充第四组提示词长度：" + text2.Length);
										GameObject val = Object.Instantiate<GameObject>(g.res.Load<GameObject>("UI/msgItemGroup"), OutPutContent.transform);
										((Component)val.transform.Find("Left")).gameObject.SetActive(false);
										((Component)val.transform.Find("Right/TextName")).GetComponent<Text>().text = units[temp].data.unitData.propertyData.GetName();
										((Component)val.transform.Find("Right/Text")).GetComponent<Text>().text = "对方正在考虑中。。。。。。";
										if (!msgObject.ContainsKey(units[temp].data.unitData.unitID))
										{
											msgObject.TryAdd(units[temp].data.unitData.unitID, val);
										}
										else
										{
											msgObject[units[temp].data.unitData.unitID] = val;
										}
										try
										{
											if (sendMsgThread == null)
											{
												goto IL_0451;
											}
											TimerCoroutine obj = sendMsgThread;
											if (obj != null && obj.isStop)
											{
												goto IL_0451;
											}
											goto end_IL_0437;
											IL_0451:
											sendMsgThread = g.timer.Frame(Action.op_Implicit((Action)async delegate
											{
												_ = 2;
												try
												{
													state = 1;
													Console.WriteLine("开始发送NPC群聊消息");
													if (isChat)
													{
														await SendToDeepSeek(units[temp], "user", userMsg);
													}
													else
													{
														await SendToDeepSeek(units[temp], "user", baseActionStr + userMsg);
														isAction = true;
														Console.WriteLine("交互模式消息发送结束");
													}
													await Task.Delay(100);
													isUpdate = true;
													isNext = true;
													int num = i;
													i = num + 1;
													Console.WriteLine(units[temp].data.unitData.propertyData.GetName() + "的Http任务已全部返回");
													state = 0;
												}
												catch (Exception ex7)
												{
													Console.WriteLine(ex7.ToString());
												}
											}), 0, false);
											end_IL_0437:;
										}
										catch (Exception ex4)
										{
											Console.WriteLine("Error: " + ex4.ToString());
										}
									}
									else
									{
										try
										{
											ModMain.chatGroup.Groups[selIndex].UnitsID.Remove(unitID);
										}
										catch (Exception ex5)
										{
											Console.WriteLine(ex5.ToString());
										}
									}
								}
								if (isAction && UIChatAINewGroup.aIActionMessage != null)
								{
									try
									{
										Console.WriteLine("交互模式开始判断行动");
										AIActionMessage aIActionMessage = UIChatAINewGroup.aIActionMessage;
										string actionName = new ActionList().GetActionName(aIActionMessage.actionNumber);
										UnitActionAI unitActionAI = new UnitActionAI(aIActionMessage, units[temp]);
										if (aIActionMessage.isApproved.Contains("是"))
										{
											unitActionAI.SelectAction();
											g.world.unitLog.SaveLogData();
										}
										else if (aIActionMessage.actionNumber >= 3)
										{
											UITipItem.AddTip(units[temp].data.unitData.propertyData.GetName() + "不同意你的行为请求--" + actionName, 2f);
											Console.WriteLine("对方不同意行为请求--" + actionName);
										}
									}
									catch (Exception ex6)
									{
										Console.WriteLine(ex6.Message);
									}
									isAction = false;
									UIChatAINewGroup.aIActionMessage = null;
									Console.WriteLine("行动结束");
								}
							});
						}
						catch (Exception ex3)
						{
							Console.WriteLine(ex3.ToString());
						}
					}), 0.1f, true);
					return;
				}
				catch (Exception ex)
				{
					Console.WriteLine("Thread 出错：" + ex.ToString());
					return;
				}
			}
			UITipItem.AddTip("正在传音中，请等待对方回复！", 3f);
			UITipItem.AddTip("若90秒后未回复自动超时返回！", 3f);
		}
		catch (Exception ex2)
		{
			Console.WriteLine(ex2.ToString());
		}
	}

	public string GetGroupMessagesText(GroupItem groupItem)
	{
		if (groupItem == null)
		{
			List<string> list = new List<string>();
			foreach (GroupMessage message in groupItem.Messages)
			{
				if (message.role.Equals("user"))
				{
					string item = message.name + ":" + message.content;
					list.Add(item);
				}
				else if (message.role.Equals("assistant"))
				{
					string item2 = message.name + ":" + message.content;
					list.Add(item2);
				}
			}
			list.Reverse();
			return string.Join("\n", list);
		}
		return "";
	}

	public async Task SendToDeepSeek(WorldUnitBase unit, string role, string question, int type = 0)
	{
		_ = 2;
		try
		{
			List<GroupMessage> list = new List<GroupMessage>();
			List<GroupMessage> newMsgs = new List<GroupMessage>();
			_ = ModMain.chatGroup.Groups[selIndex].Id;
			if (selIndex >= 0 && selIndex < ModMain.chatGroup.Groups.Count)
			{
				if (isChat)
				{
					list.AddRange(ModMain.chatGroup.Groups[selIndex].Messages);
				}
				else
				{
					list.AddRange(ModMain.chatGroup.Groups[selIndex].ActionMessage);
				}
				BatchQueryManager batchQueryManager = new BatchQueryManager();
				List<QueryTaskConfig> list2 = new List<QueryTaskConfig>();
				string promats = "";
				if (list.Count >= 4)
				{
					foreach (PromptItem item5 in ModMain.aIPromptCommand.promptFile.items)
					{
						if (item5.addMode != 1)
						{
							continue;
						}
						string[] array = item5.keyWord.Split(';');
						foreach (string text in array)
						{
							if (!StringExtensions.IsEmpty(text) && question.Contains(text))
							{
								((Object)this).name = ((Object)this).name + item5.name + "  ";
								promats = promats + item5.content + "\n";
								break;
							}
						}
					}
					if (!StringExtensions.IsEmpty(promats))
					{
						promats = "\n[系统补充设定]\n" + promats;
						Console.WriteLine("触发关键词提示词");
					}
					newMsgs.Add(list[0]);
					newMsgs.Add(list[1]);
					newMsgs.Add(list[2]);
					newMsgs.Add(list[3]);
					if (ModMain.vectorKnowledgeCMD.vectorKnowledge.isOpen)
					{
						list2.Add(new QueryTaskConfig
						{
							Name = "基础知识库",
							Input = question,
							OnSingleComplete = delegate(List<(string, float)> results)
							{
								if (results != null && results.Count > 0)
								{
									Console.WriteLine($"找到了基础知识库中 {results.Count} 条相关知识！");
									string vectorKnowledgeString3 = VectorKnowledgeBase.GetVectorKnowledgeString(results, ModMain.vectorKnowledgeCMD.vectorKnowledge.topK);
									Console.WriteLine("基础知识库长度：" + vectorKnowledgeString3.Length);
									promats = promats + "\n[基础知识库]\n" + vectorKnowledgeString3 + "\n";
								}
								else
								{
									Console.WriteLine("未找到相关知识。");
								}
							}
						});
						if (ModMain.worldLogCMD.worldLog.isAddPrompt)
						{
							list2.Add(new QueryTaskConfig
							{
								Name = "主世界剧情",
								Input = question,
								OnSingleComplete = delegate(List<(string, float)> results)
								{
									if (results != null && results.Count > 0)
									{
										Console.WriteLine($"找到了主世界剧情中 {results.Count} 条相关知识！");
										string vectorKnowledgeString3 = VectorKnowledgeBase.GetVectorKnowledgeString(results, ModMain.vectorKnowledgeCMD.vectorKnowledge.topK, "以下是主世界剧情片段，请在输出时进行参考。");
										Console.WriteLine("主世界剧情长度：" + vectorKnowledgeString3.Length);
										promats = promats + "\n[主世界剧情]\n" + vectorKnowledgeString3 + "\n";
									}
									else
									{
										Console.WriteLine("未找到相关知识。");
									}
								}
							});
						}
					}
					else if (ModMain.worldLogCMD.worldLog.isAddPrompt)
					{
						promats = promats + "\n[主世界剧情]\n" + ModMain.worldLogCMD.GetWorldText() + "\n";
					}
				}
				string groupMsg = CommandTool.GetGroupMsg(unit);
				GroupMessage item = new GroupMessage
				{
					role = "system",
					content = groupMsg
				};
				newMsgs.Add(item);
				List<(string Text, float Score)> InPutTextShare = new List<(string, float)>();
				List<(string Text, float Score)> InPutText = new List<(string, float)>();
				if (ModMain.vectorKnowledgeCMD.vectorKnowledge.isOpenMsgMode)
				{
					foreach (string item6 in ModMain.shareUnit)
					{
						list2.Add(new QueryTaskConfig
						{
							Name = item6 + "私聊",
							Input = question,
							OnSingleComplete = delegate(List<(string, float)> results)
							{
								if (results != null && results.Count > 0)
								{
									InPutTextShare.AddRange(results);
								}
							}
						});
					}
					list2.Add(new QueryTaskConfig
					{
						Name = unit.GetUnitID() + "私聊",
						Input = question,
						OnSingleComplete = delegate(List<(string, float)> results)
						{
							if (results != null && results.Count > 0)
							{
								Console.WriteLine($"找到了私聊中 {results.Count} 条相关知识！");
								InPutText.AddRange(results);
							}
						}
					});
					list2.Add(new QueryTaskConfig
					{
						Name = unit.GetUnitID() + "群聊",
						Input = question,
						OnSingleComplete = delegate(List<(string, float)> results)
						{
							if (results != null && results.Count > 0)
							{
								Console.WriteLine($"找到了群聊中 {results.Count} 条相关知识！");
								InPutText.AddRange(results);
							}
						}
					});
				}
				else
				{
					string text2 = "[" + unit.GetName() + "与" + g.world.playerUnit.GetName() + "的聊天记录]\n" + CommandTool.GetMessagesText(unit);
					GroupMessage item2 = new GroupMessage
					{
						role = "system",
						content = text2
					};
					Console.WriteLine(text2);
					newMsgs.Add(item2);
					for (int num = 4; num < list.Count; num++)
					{
						newMsgs.Add(list[num]);
					}
				}
				await batchQueryManager.ExecuteBatchQueriesAsync(list2);
				Console.WriteLine("=== 所有 NPC 知识库检索完毕，聊天正式开始！ ===");
				newMsgs.Add(new GroupMessage
				{
					role = "system",
					content = promats
				});
				string preText = "以下是关联NPC的历史对话片段，请你在输出的时候进行参考。张三:xxxx 李四:yyyy";
				string vectorKnowledgeString = VectorKnowledgeBase.GetVectorKnowledgeString(InPutTextShare, ModMain.vectorKnowledgeCMD.vectorKnowledge.topK, preText);
				string preText2 = "以下是" + unit.GetName() + "的私聊、群聊及关联NPC的历史对话片段，请你在输出的时候进行参考。XX:xxxx YY:yyyy";
				string vectorKnowledgeString2 = VectorKnowledgeBase.GetVectorKnowledgeString(InPutText, ModMain.vectorKnowledgeCMD.vectorKnowledge.topK, preText2);
				Console.WriteLine("历史对话记录长度：" + vectorKnowledgeString2.Length);
				string text3 = vectorKnowledgeString2 + vectorKnowledgeString;
				if (text3.Length > 0)
				{
					GroupMessage item3 = new GroupMessage
					{
						role = "system",
						content = "[历史对话记录]\n" + text3
					};
					newMsgs.Add(item3);
				}
				List<GroupMessage> list3 = new List<GroupMessage>();
				List<GroupMessage> list4 = new List<GroupMessage>();
				foreach (GroupMessage item7 in newMsgs)
				{
					if (item7.role.Equals("system"))
					{
						list3.Add(item7);
					}
					else
					{
						list4.Add(item7);
					}
				}
				list3.AddRange(list4);
				string text4 = "[群友亲密度]\n";
				foreach (string item8 in ModMain.chatGroup.Groups[selIndex].UnitsID)
				{
					try
					{
						WorldUnitBase val = g.world.unit.GetUnit(item8, true);
						if (val != null)
						{
							if (val.GetUnitID().Equals(g.world.playerUnit.GetUnitID()))
							{
								val = g.world.playerUnit;
							}
							text4 += $"{unit.GetName()}与{val.GetName()}的亲密度：{unit.data.unitData.relationData.GetIntim(val)}(默认最高300)\n";
						}
					}
					catch (Exception ex)
					{
						Console.WriteLine(ex.Message);
					}
				}
				GroupMessage item4 = new GroupMessage
				{
					role = role,
					content = text4 + question,
					name = g.world.playerUnit.data.unitData.propertyData.GetName()
				};
				list3.Add(item4);
				int num2 = 0;
				foreach (GroupMessage item9 in list3)
				{
					num2 += item9.content.Length;
					Console.WriteLine("role:" + item9.role + "  content:" + item9.content + "\n");
				}
				Console.WriteLine($"最终传入总字数：{num2}");
				string model = ModMain.modelName;
				AIConfInfoItem aIConfInfoItem = null;
				if (ModMain.aIConfInfo.aiConfUses.ContainsKey(2))
				{
					aIConfInfoItem = ModMain.aIConfInfo.GetUseNextIndex(2);
					if (aIConfInfoItem != null)
					{
						model = aIConfInfoItem.model;
					}
					else
					{
						Console.WriteLine("轮询判断失败！");
					}
				}
				if (ModMain.inputMode)
				{
					var requestData = new
					{
						model = model,
						messages = list3,
						stream = ModMain.isStream,
						max_tokens = ModMain.max_tokens,
						temperature = ModMain.temperature,
						top_p = ModMain.top_p,
						top_k = ModMain.top_k
					};
					await CallDeepSeekAPIStreaming(unit, role, requestData, aIConfInfoItem, type);
				}
				else
				{
					var requestData2 = new
					{
						model = model,
						messages = list3,
						stream = ModMain.isStream
					};
					await CallDeepSeekAPIStreaming(unit, role, requestData2, aIConfInfoItem, type);
				}
			}
			else
			{
				Console.WriteLine("未正确选择群组");
			}
		}
		catch (Exception ex2)
		{
			Console.WriteLine("\nError: " + ex2.ToString());
			Console.WriteLine("SendToDeepSeek出错");
		}
	}

	public async Task CallDeepSeekAPIStreaming(WorldUnitBase unit, string role, object requestData, AIConfInfoItem aIConfInfoItem, int type = 0)
	{
		_ = 3;
		try
		{
			string text = ModMain.apiKey;
			string requestUri = ModMain.apiUrl;
			bool isStream = CommandTool.GetToggleStream() == 1;
			if (aIConfInfoItem != null)
			{
				text = aIConfInfoItem.apikey;
				requestUri = aIConfInfoItem.url;
			}
			client.DefaultRequestHeaders.Clear();
			client.DefaultRequestHeaders.Add("Authorization", "Bearer " + text);
			client.DefaultRequestHeaders.UserAgent.ParseAdd("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 CSharp-DeepSeek-Client/1.0");
			client.DefaultRequestHeaders.Accept.Add(new MediaTypeWithQualityHeaderValue("application/json"));
			StringContent content = new StringContent(JsonConvert.SerializeObject(requestData), Encoding.UTF8, "application/json");
			HttpRequestMessage request = new HttpRequestMessage(HttpMethod.Post, requestUri)
			{
				Content = content
			};
			Console.WriteLine("开始发送消息");
			HttpResponseMessage obj = await client.SendAsync(request, HttpCompletionOption.ResponseHeadersRead);
			Console.Write("开始答复：");
			if (!msgText.ContainsKey(unit.data.unitData.unitID))
			{
				msgText.TryAdd(unit.data.unitData.unitID, "");
			}
			obj.EnsureSuccessStatusCode();
			using Stream stream = await obj.Content.ReadAsStreamAsync();
			using StreamReader reader = new StreamReader(stream);
			string rspText = "";
			if (isStream)
			{
				while (!reader.EndOfStream)
				{
					try
					{
						string text2 = await reader.ReadLineAsync();
						if (string.IsNullOrWhiteSpace(text2))
						{
							continue;
						}
						if (text2.StartsWith("data:"))
						{
							string text3 = text2.Substring(5).Trim();
							if (text3 == "[DONE]")
							{
								break;
							}
							try
							{
								StreamChatResponse streamChatResponse = JsonConvert.DeserializeObject<StreamChatResponse>(text3);
								string text4 = streamChatResponse?.choices?.FirstOrDefault()?.delta?.content;
								StreamChatResponse.Usage usage = streamChatResponse?.usage;
								if (!string.IsNullOrEmpty(text4))
								{
									rspText += text4;
									if (msgText.ContainsKey(unit.data.unitData.unitID))
									{
										msgText[unit.data.unitData.unitID] = rspText;
									}
									if (isChat)
									{
										isUpdate = true;
									}
								}
								if (usage != null)
								{
									Console.WriteLine($"提示词中的 token 数量:{usage.prompt_tokens}\n" + $"生成内容中的 token 数量:{usage.completion_tokens}\n" + $"请求中使用的总 token 数量（提示词 + 生成内容）:{usage.total_tokens}\n" + $"本次请求输入中命中缓存的 token 数量:{usage.prompt_cache_hit_tokens}\n" + $"本次请求输入中未命中缓存的 token 数量:{usage.prompt_cache_miss_tokens}\n" + $"缓存命中率：{usage.prompt_cache_hit_tokens * 100 / usage.prompt_tokens}");
								}
							}
							catch (Exception ex)
							{
								Console.WriteLine("忽略内容：" + text2);
								Console.WriteLine("忽略注释或非JSON数据");
								Console.WriteLine(ex.ToString());
							}
							continue;
						}
						try
						{
							if (text2.StartsWith(":"))
							{
								continue;
							}
							ChatResponse chatResponse = JsonConvert.DeserializeObject<ChatResponse>(text2);
							string text5 = chatResponse?.choices?.FirstOrDefault()?.message?.content;
							ChatResponse.Usage usage2 = chatResponse?.usage;
							if (!string.IsNullOrEmpty(text5))
							{
								rspText += text5;
								if (msgText.ContainsKey(unit.data.unitData.unitID))
								{
									msgText[unit.data.unitData.unitID] = rspText;
								}
								if (isChat)
								{
									isUpdate = true;
								}
							}
							if (usage2 != null)
							{
								Console.WriteLine($"提示词中的 token 数量:{usage2.prompt_tokens}\n" + $"生成内容中的 token 数量:{usage2.completion_tokens}\n" + $"请求中使用的总 token 数量（提示词 + 生成内容）:{usage2.total_tokens}\n" + $"本次请求输入中命中缓存的 token 数量:{usage2.prompt_cache_hit_tokens}\n" + $"本次请求输入中未命中缓存的 token 数量:{usage2.prompt_cache_miss_tokens}\n" + $"缓存命中率：{usage2.prompt_cache_hit_tokens * 100 / usage2.prompt_tokens}");
							}
						}
						catch (Exception ex2)
						{
							Console.WriteLine("注释内容：" + text2);
							Console.WriteLine("忽略注释或非JSON数据");
							Console.WriteLine(ex2.ToString());
						}
					}
					catch (Exception ex3)
					{
						Console.WriteLine(ex3.Message);
					}
				}
			}
			else
			{
				string text6 = await reader.ReadToEndAsync();
				try
				{
					ChatResponse chatResponse2 = JsonConvert.DeserializeObject<ChatResponse>(text6);
					string text7 = chatResponse2?.choices?.FirstOrDefault()?.message?.content;
					ChatResponse.Usage usage3 = chatResponse2?.usage;
					if (!string.IsNullOrEmpty(text7))
					{
						rspText += text7;
						if (msgText.ContainsKey(unit.data.unitData.unitID))
						{
							msgText[unit.data.unitData.unitID] = rspText;
						}
						if (isChat)
						{
							isUpdate = true;
						}
					}
					if (usage3 != null)
					{
						Console.WriteLine($"提示词中的 token 数量:{usage3.prompt_tokens}\n" + $"生成内容中的 token 数量:{usage3.completion_tokens}\n" + $"请求中使用的总 token 数量（提示词 + 生成内容）:{usage3.total_tokens}\n" + $"本次请求输入中命中缓存的 token 数量:{usage3.prompt_cache_hit_tokens}\n" + $"本次请求输入中未命中缓存的 token 数量:{usage3.prompt_cache_miss_tokens}\n" + $"缓存命中率：{usage3.prompt_cache_hit_tokens * 100 / usage3.prompt_tokens}");
					}
				}
				catch (Exception ex4)
				{
					Console.WriteLine("忽略内容：" + text6);
					Console.WriteLine("忽略注释或非JSON数据");
					Console.WriteLine(ex4.ToString());
				}
			}
			Console.WriteLine("开始处理输出结果");
			if (isChat)
			{
				if (StringExtensions.IsEmpty(rspText))
				{
					rspText = "道友幸会，今天很不凑巧，神识传音似乎失败了。对方返回了空白信息！";
					if (msgText.ContainsKey(unit.data.unitData.unitID))
					{
						msgText[unit.data.unitData.unitID] = rspText;
					}
				}
				else
				{
					rspText = Regex.Replace(rspText, "(<think>.*?</think>)|(<thinking>.*?</thinking>)", "", RegexOptions.Singleline);
					if (msgText.ContainsKey(unit.data.unitData.unitID))
					{
						msgText[unit.data.unitData.unitID] = rspText;
					}
					CommandTool.AddGroupMessage(selIndex, unit, "assistant", rspText, 1);
				}
			}
			else
			{
				CommandTool.AddGroupMessage(selIndex, unit, "assistant", rspText, 2);
				aIActionMessage = CommandTool.GetXmlText(rspText);
				rspTextTemp = rspText;
				if (aIActionMessage == null)
				{
					if (msgText.ContainsKey(unit.data.unitData.unitID))
					{
						msgText[unit.data.unitData.unitID] = rspText;
					}
				}
				else if (msgText.ContainsKey(unit.data.unitData.unitID))
				{
					msgText[unit.data.unitData.unitID] = aIActionMessage.yourResponse;
				}
			}
		}
		catch (TimeoutException ex5)
		{
			string value = "道友幸会，神识通道不稳定，传音超时了，请稍后再试。错误信息：" + ex5.Message;
			Console.WriteLine("请求超时: " + ex5.Message);
			if (msgText.ContainsKey(unit.data.unitData.unitID))
			{
				msgText[unit.data.unitData.unitID] = value;
			}
		}
		catch (HttpRequestException ex6)
		{
			string value2 = "道友幸会，神识通道似乎出了点问题，请求错误了，请检查三个核心参数配置。错误信息：" + ex6.Message;
			Console.WriteLine("HTTP 请求错误: " + ex6.Message);
			if (msgText.ContainsKey(unit.data.unitData.unitID))
			{
				msgText[unit.data.unitData.unitID] = value2;
			}
		}
		catch (NullReferenceException ex7)
		{
			string value3 = "道友幸会，神识通道似乎出了点问题，请求错误了，似乎发现了什么不存在的东西，错误信息：" + ex7.Message;
			Console.WriteLine("引用对象为空");
			if (msgText.ContainsKey(unit.data.unitData.unitID))
			{
				msgText[unit.data.unitData.unitID] = value3;
			}
		}
		catch (Exception ex8)
		{
			string value4 = "道友幸会，神识传音似乎失败了，错误信息：" + ex8.Message;
			Console.WriteLine("错误: " + ex8.Message);
			Console.WriteLine("CallDeepSeekAPIStreaming出错");
			if (msgText.ContainsKey(unit.data.unitData.unitID))
			{
				msgText[unit.data.unitData.unitID] = value4;
			}
		}
		curUnit = unit;
		isUpdate = true;
		isComplete = true;
		state = 0;
	}

	private void ScrollToItemCoroutine(ScrollRect scrollRect, int itemIndex, float duration = 0f)
	{
		float verticalNormalizedPosition = 1f - (float)itemIndex / (float)(((Transform)scrollRect.content).childCount - 1);
		LayoutRebuilder.ForceRebuildLayoutImmediate(scrollRect.content);
		Canvas.ForceUpdateCanvases();
		scrollRect.verticalNormalizedPosition = verticalNormalizedPosition;
	}

	public void UpdataList(int selValue = -1)
	{
		//IL_0073: Unknown result type (might be due to invalid IL or missing references)
		//IL_0079: Unknown result type (might be due to invalid IL or missing references)
		ActionList actionList = new ActionList();
		DropdownType.GetComponent<Dropdown>().options.Clear();
		string tips = string.Format("行动序号:{0}\n行动名称:{1}\n行动描述:{2}", 0, "闲聊", "禁止选择此行动");
		((Component)DropdownType.transform.Find("Label")).gameObject.AddComponent<UISkyTipEffect>().InitData("", default(Vector3));
		List<string> val = new List<string>();
		val.Add("全部");
		foreach (ActionItem action in actionList.actions)
		{
			val.Add(action.actionIndex + action.actionName);
		}
		DropdownType.GetComponent<Dropdown>().AddOptions(val);
		((UnityEventBase)DropdownType.GetComponent<Dropdown>().onValueChanged).RemoveAllListeners();
		((UnityEvent<int>)(object)DropdownType.GetComponent<Dropdown>().onValueChanged).AddListener(UnityAction<int>.op_Implicit((Action<int>)delegate(int value)
		{
			//IL_0102: Unknown result type (might be due to invalid IL or missing references)
			//IL_0108: Unknown result type (might be due to invalid IL or missing references)
			//IL_00a4: Unknown result type (might be due to invalid IL or missing references)
			//IL_00aa: Unknown result type (might be due to invalid IL or missing references)
			int num = value - 1;
			if (num >= 0 && num < actionList.actions.Count)
			{
				tips = $"行动序号:{actionList.actions[num].actionIndex}\n行动名称:{actionList.actions[num].actionName}\n行动描述:{actionList.actions[num].actionDesc}";
				((Component)DropdownType.transform.Find("Label")).gameObject.GetComponent<UISkyTipEffect>().InitData(tips, default(Vector3));
			}
			else if (num == -1)
			{
				tips = string.Format("行动序号:{0}\n行动名称:{1}\n行动描述:{2}", -1, "全部", "全部");
				((Component)DropdownType.transform.Find("Label")).gameObject.GetComponent<UISkyTipEffect>().InitData(tips, default(Vector3));
			}
			UpdataPreText(num);
		}));
		DropdownType.GetComponent<Dropdown>().value = selValue;
	}

	public List<WorldUnitBase> UpdataNPCInfo()
	{
		string text = "";
		List<WorldUnitBase> list = new List<WorldUnitBase>();
		try
		{
			InitAI();
			for (int i = 0; i < GroupUnitList.transform.GetChildCount(); i++)
			{
				Transform child = GroupUnitList.transform.GetChild(i);
				if ((Object)(object)child != (Object)null && ((Component)child).GetComponent<Toggle>().isOn)
				{
					WorldUnitBase unit = g.world.unit.GetUnit(ModMain.chatGroup.Groups[selIndex].UnitsID[i], true);
					if (unit != null)
					{
						list.Add(unit);
						string text2 = string.Format("[{0}的身份信息]\n{1}\n", unit.data.unitData.propertyData.GetName(), CommandTool.GetUnitInfo("", unit, g.world.playerUnit));
						text += text2;
					}
				}
			}
			if (ModMain.chatGroup.Groups[selIndex].Messages.Count >= 4)
			{
				GroupMessage value = new GroupMessage
				{
					role = "system",
					content = "[群友基础信息]\n" + text
				};
				ModMain.chatGroup.Groups[selIndex].Messages[2] = value;
			}
			Console.WriteLine("NPC信息字符长度:" + text.Length);
		}
		catch (Exception ex)
		{
			Console.WriteLine(ex.ToString());
			Console.WriteLine("prompt出错");
		}
		return list;
	}

	public void ClearChatMessageList()
	{
		try
		{
			index = 4;
		}
		catch (Exception ex)
		{
			Console.WriteLine(ex.ToString());
		}
	}

	public void ClearActionMessageList()
	{
		try
		{
			index = 4;
		}
		catch (Exception ex)
		{
			Console.WriteLine(ex.ToString());
		}
	}

	public void ModityChatMessage(int temp, bool isUpdata)
	{
		//IL_004a: Unknown result type (might be due to invalid IL or missing references)
		//IL_0054: Expected O, but got Unknown
		try
		{
			if (selIndex < 0 || selIndex >= ModMain.chatGroup.Groups.Count)
			{
				return;
			}
			ClassInjector.RegisterTypeInIl2Cpp<UIInput>();
			UIInput uIInput = ((Component)g.ui.OpenUI(new UITypeBase("UIInput", (UILayer)0))).gameObject.AddComponent<UIInput>();
			if (isChat)
			{
				if (temp < ModMain.chatGroup.Groups[selIndex].Messages.Count)
				{
					uIInput.InitData(ModMain.chatGroup.Groups[selIndex].Messages[temp].content);
				}
				else
				{
					uIInput.InitData("序号错误！");
				}
			}
			else if (temp < ModMain.chatGroup.Groups[selIndex].ActionMessage.Count)
			{
				uIInput.InitData(ModMain.chatGroup.Groups[selIndex].ActionMessage[temp].content);
			}
			else
			{
				uIInput.InitData("序号错误！");
			}
			((UnityEvent)uIInput.ButtonSave.GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
			{
				try
				{
					if (StringExtensions.IsEmpty(UIInput.text))
					{
						UITipItem.AddTip("记录修改失败!", 3f);
					}
					else
					{
						if (isChat)
						{
							ModMain.chatGroup.Groups[selIndex].Messages[temp].content = UIInput.text;
							if (ModMain.chatGroup.Groups[selIndex].ActionMessage.Count > temp)
							{
								if (ModMain.chatGroup.Groups[selIndex].ActionMessage[temp].role.Equals("assistant"))
								{
									AIActionMessage xmlText = CommandTool.GetXmlText(ModMain.chatGroup.Groups[selIndex].ActionMessage[temp].content);
									if (xmlText != null)
									{
										xmlText.yourResponse = UIInput.text;
										string content = JsonConvert.SerializeObject((object)xmlText);
										ModMain.chatGroup.Groups[selIndex].ActionMessage[temp].content = content;
									}
									else
									{
										ModMain.chatGroup.Groups[selIndex].ActionMessage[temp].content = UIInput.text;
										UITipItem.AddTip("Json文本格式错误!", 3f);
									}
								}
								else
								{
									ModMain.chatGroup.Groups[selIndex].ActionMessage[temp].content = UIInput.text;
								}
							}
							UpdateMessageList();
						}
						else
						{
							if (ModMain.chatGroup.Groups[selIndex].ActionMessage.Count > temp)
							{
								if (ModMain.chatGroup.Groups[selIndex].ActionMessage[temp].role.Equals("assistant"))
								{
									ModMain.chatGroup.Groups[selIndex].ActionMessage[temp].content = UIInput.text;
									AIActionMessage xmlText2 = CommandTool.GetXmlText(ModMain.chatGroup.Groups[selIndex].ActionMessage[temp].content);
									if (xmlText2 != null)
									{
										ModMain.chatGroup.Groups[selIndex].Messages[temp].content = xmlText2.yourResponse;
									}
									else
									{
										ModMain.chatGroup.Groups[selIndex].Messages[temp].content = UIInput.text;
										UITipItem.AddTip("Json文本格式错误!", 3f);
									}
								}
								else
								{
									ModMain.chatGroup.Groups[selIndex].ActionMessage[temp].content = UIInput.text;
									ModMain.chatGroup.Groups[selIndex].Messages[temp].content = UIInput.text;
								}
							}
							UpdateMessageList();
						}
						UITipItem.AddTip("记录修改成功!", 3f);
					}
				}
				catch (Exception ex2)
				{
					Console.WriteLine(ex2.ToString());
				}
			}));
		}
		catch (Exception ex)
		{
			Console.WriteLine(ex.Message);
		}
	}

	public void CopyChatMessageList()
	{
	}

	public void CopyActionMessageList()
	{
	}

	public void UpdateMessageList()
	{
		//IL_0692: Unknown result type (might be due to invalid IL or missing references)
		//IL_069e: Unknown result type (might be due to invalid IL or missing references)
		//IL_06ae: Unknown result type (might be due to invalid IL or missing references)
		//IL_06da: Unknown result type (might be due to invalid IL or missing references)
		//IL_06e0: Unknown result type (might be due to invalid IL or missing references)
		//IL_075b: Unknown result type (might be due to invalid IL or missing references)
		//IL_0761: Unknown result type (might be due to invalid IL or missing references)
		//IL_07dc: Unknown result type (might be due to invalid IL or missing references)
		//IL_07e2: Unknown result type (might be due to invalid IL or missing references)
		//IL_03a9: Unknown result type (might be due to invalid IL or missing references)
		//IL_03b5: Unknown result type (might be due to invalid IL or missing references)
		//IL_03c5: Unknown result type (might be due to invalid IL or missing references)
		//IL_03f1: Unknown result type (might be due to invalid IL or missing references)
		//IL_03f7: Unknown result type (might be due to invalid IL or missing references)
		//IL_0472: Unknown result type (might be due to invalid IL or missing references)
		//IL_0478: Unknown result type (might be due to invalid IL or missing references)
		//IL_04f3: Unknown result type (might be due to invalid IL or missing references)
		//IL_04f9: Unknown result type (might be due to invalid IL or missing references)
		//IL_086a: Unknown result type (might be due to invalid IL or missing references)
		//IL_0870: Unknown result type (might be due to invalid IL or missing references)
		try
		{
			for (int num = OutPutContent.transform.GetChildCount() - 1; num >= 0; num--)
			{
				Object.Destroy((Object)(object)((Component)OutPutContent.transform.GetChild(num)).gameObject);
			}
			time = "";
			index = 3;
			if (selIndex < 0 || selIndex >= ModMain.chatGroup.Groups.Count)
			{
				return;
			}
			int num2 = ModMain.chatGroup.Groups[selIndex].Messages.Count / preCount + ((ModMain.chatGroup.Groups[selIndex].Messages.Count % preCount != 0) ? 1 : 0);
			if (curPage < 1)
			{
				curPage = num2;
			}
			if (curPage > num2)
			{
				curPage = 1;
			}
			int num3 = (curPage - 1) * preCount;
			if (num3 >= ModMain.chatGroup.Groups[selIndex].Messages.Count)
			{
				curPage = 1;
				num3 = 0;
			}
			int num4 = ((curPage * preCount < ModMain.chatGroup.Groups[selIndex].Messages.Count) ? (curPage * preCount) : ModMain.chatGroup.Groups[selIndex].Messages.Count);
			int num5 = 0;
			for (int i = num3; i < num4; i++)
			{
				int temp = i;
				index = i;
				if (ModMain.chatGroup.Groups[selIndex].Messages[i].role.Equals("system"))
				{
					continue;
				}
				num5++;
				string content = "";
				GameObject val = null;
				if (ModMain.chatGroup.Groups[selIndex].Messages[i].role.Equals("user"))
				{
					string content2 = CommandTool.GetContent(ModMain.chatGroup.Groups[selIndex].Messages[i].content, out content);
					if (!time.Equals(content2))
					{
						time = content2;
						((Component)Object.Instantiate<GameObject>(g.res.Load<GameObject>("UI/msgItemTime"), OutPutContent.transform).transform.Find("Text")).GetComponent<Text>().text = content2;
					}
					content = content.Replace("(我是" + g.world.playerUnit.data.unitData.propertyData.GetName() + ")", "");
					val = Object.Instantiate<GameObject>(g.res.Load<GameObject>("UI/msgItemGroup"), OutPutContent.transform);
					content = CommandTool.RegexForOutPut(content);
					((Component)val.transform.Find("Left/Text")).GetComponent<Text>().text = content;
					((Component)val.transform.Find("Left/TextName")).GetComponent<Text>().text = ModMain.chatGroup.Groups[selIndex].Messages[i].name;
					((Component)val.transform.Find("Right")).gameObject.SetActive(false);
					RectTransform component = ((Component)val.transform.Find("Left/Text")).GetComponent<RectTransform>();
					LayoutRebuilder.ForceRebuildLayoutImmediate(component);
					RectTransform component2 = ((Component)val.transform).GetComponent<RectTransform>();
					component2.sizeDelta = new Vector2(component2.sizeDelta.x, component.sizeDelta.y + 100f);
					((Component)val.transform.Find("Left/ButtonDel")).gameObject.AddComponent<UISkyTipEffect>().InitData("删除该条聊天记录", default(Vector3));
					((UnityEventBase)((Component)val.transform.Find("Left/ButtonDel")).GetComponent<Button>().onClick).RemoveAllListeners();
					((UnityEvent)((Component)val.transform.Find("Left/ButtonDel")).GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
					{
						g.ui.OpenUI<UICheckPopup>(UIType.CheckPopup).InitData("提示", "是否确认删除该组聊天记录？", 2, Action.op_Implicit((Action)delegate
						{
							try
							{
								ModMain.chatGroup.Groups[selIndex].Messages.RemoveAt(temp);
								ModMain.chatGroup.Groups[selIndex].ActionMessage.RemoveAt(temp);
								UpdateMessageList();
							}
							catch (Exception ex2)
							{
								Console.WriteLine(ex2.Message);
							}
						}), (Action)null);
					}));
					((Component)val.transform.Find("Left/ButtonCopy")).gameObject.AddComponent<UISkyTipEffect>().InitData("复制该条聊天记录", default(Vector3));
					((UnityEventBase)((Component)val.transform.Find("Left/ButtonCopy")).GetComponent<Button>().onClick).RemoveAllListeners();
					((UnityEvent)((Component)val.transform.Find("Left/ButtonCopy")).GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
					{
						try
						{
							if (isChat)
							{
								Clipboard.SetDataObject(ModMain.chatGroup.Groups[selIndex].Messages[temp].content, copy: true);
							}
							else
							{
								Clipboard.SetDataObject(ModMain.chatGroup.Groups[selIndex].ActionMessage[temp].content, copy: true);
							}
						}
						catch (Exception ex2)
						{
							Console.WriteLine(ex2.Message);
						}
					}));
					((Component)val.transform.Find("Left/ButtonModity")).gameObject.AddComponent<UISkyTipEffect>().InitData("修改该条聊天记录", default(Vector3));
					((UnityEventBase)((Component)val.transform.Find("Left/ButtonModity")).GetComponent<Button>().onClick).RemoveAllListeners();
					((UnityEvent)((Component)val.transform.Find("Left/ButtonModity")).GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
					{
						ModityChatMessage(temp, isUpdata: true);
					}));
				}
				else if (ModMain.chatGroup.Groups[selIndex].Messages[i].role.Equals("assistant"))
				{
					content = ModMain.chatGroup.Groups[selIndex].Messages[i].content;
					val = Object.Instantiate<GameObject>(g.res.Load<GameObject>("UI/msgItemGroup"), OutPutContent.transform);
					string input = content.Trim();
					input = CommandTool.RegexForOutPut(input);
					((Component)val.transform.Find("Right/Text")).GetComponent<Text>().text = input;
					((Component)val.transform.Find("Right/TextName")).GetComponent<Text>().text = ModMain.chatGroup.Groups[selIndex].Messages[i].name;
					((Component)val.transform.Find("Left")).gameObject.SetActive(false);
					RectTransform component3 = ((Component)val.transform.Find("Right/Text")).GetComponent<RectTransform>();
					LayoutRebuilder.ForceRebuildLayoutImmediate(component3);
					Canvas.ForceUpdateCanvases();
					RectTransform component4 = ((Component)val.transform).GetComponent<RectTransform>();
					component4.sizeDelta = new Vector2(component4.sizeDelta.x, component3.sizeDelta.y + 100f);
					((Component)val.transform.Find("Right/ButtonDel")).gameObject.AddComponent<UISkyTipEffect>().InitData("删除该条聊天记录", default(Vector3));
					((UnityEventBase)((Component)val.transform.Find("Right/ButtonDel")).GetComponent<Button>().onClick).RemoveAllListeners();
					((UnityEvent)((Component)val.transform.Find("Right/ButtonDel")).GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
					{
						g.ui.OpenUI<UICheckPopup>(UIType.CheckPopup).InitData("提示", "是否确认删除该组聊天记录？", 2, Action.op_Implicit((Action)delegate
						{
							try
							{
								ModMain.chatGroup.Groups[selIndex].Messages.RemoveAt(temp);
								ModMain.chatGroup.Groups[selIndex].ActionMessage.RemoveAt(temp);
								UpdateMessageList();
							}
							catch (Exception ex2)
							{
								Console.WriteLine(ex2.Message);
							}
						}), (Action)null);
					}));
					((Component)val.transform.Find("Right/ButtonModity")).gameObject.AddComponent<UISkyTipEffect>().InitData("修改该条聊天记录", default(Vector3));
					((UnityEventBase)((Component)val.transform.Find("Right/ButtonModity")).GetComponent<Button>().onClick).RemoveAllListeners();
					((UnityEvent)((Component)val.transform.Find("Right/ButtonModity")).GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
					{
						ModityChatMessage(temp, isUpdata: true);
					}));
					((Component)val.transform.Find("Right/ButtonCopy")).gameObject.AddComponent<UISkyTipEffect>().InitData("复制聊天记录", default(Vector3));
					((UnityEventBase)((Component)val.transform.Find("Right/ButtonCopy")).GetComponent<Button>().onClick).RemoveAllListeners();
					((UnityEvent)((Component)val.transform.Find("Right/ButtonCopy")).GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
					{
						try
						{
							if (isChat)
							{
								Clipboard.SetDataObject(ModMain.chatGroup.Groups[selIndex].Messages[temp].content, copy: true);
							}
							else
							{
								Clipboard.SetDataObject(ModMain.chatGroup.Groups[selIndex].ActionMessage[temp].content, copy: true);
							}
						}
						catch (Exception ex2)
						{
							Console.WriteLine(ex2.Message);
						}
					}));
				}
				if ((Object)(object)val == (Object)null)
				{
					continue;
				}
				((Component)val.transform.Find("Right/ButtonLook")).gameObject.AddComponent<UISkyTipEffect>().InitData("显示完整内容", default(Vector3));
				((UnityEventBase)((Component)val.transform.Find("Right/ButtonLook")).GetComponent<Button>().onClick).RemoveAllListeners();
				((UnityEvent)((Component)val.transform.Find("Right/ButtonLook")).GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
				{
					if (isChat)
					{
						g.ui.OpenUI<UITextInfoLong>(UIType.TextInfoLong).InitData("显示完整内容", ModMain.chatGroup.Groups[selIndex].Messages[temp].content, "", (Action)null, false);
					}
					else
					{
						g.ui.OpenUI<UITextInfoLong>(UIType.TextInfoLong).InitData("显示完整内容", ModMain.chatGroup.Groups[selIndex].ActionMessage[temp].content, "", (Action)null, false);
					}
				}));
			}
			((Component)((Component)this).transform.Find("Root/PanelMain/OutPut/Text")).GetComponent<Text>().text = $"消息总数:{ModMain.chatGroup.Groups[selIndex].Messages.Count}  当前页显示消息数：{num5}  第 {curPage} 页 / 总 {num2} 页";
			ScrollRect component5 = ((Component)((Component)this).transform.Find("Root/PanelMain/OutPut/Panel/Scroll View")).GetComponent<ScrollRect>();
			LayoutRebuilder.ForceRebuildLayoutImmediate(component5.content);
			Canvas.ForceUpdateCanvases();
			component5.verticalNormalizedPosition = 0f;
			state = 0;
		}
		catch (Exception ex)
		{
			Console.WriteLine(ex.ToString());
		}
	}

	public void DelChatMessageItem(int temp)
	{
	}

	public void DelActionMessageItem(int temp)
	{
	}

	public void CopyChatMessageI(int temp)
	{
	}

	public void CopyActionMessage(int temp)
	{
	}

	public void ShowDrama(WorldUnitBase leftUnit, WorldUnitBase rightUnit, string Text, string opText, Action action)
	{
		//IL_0016: Unknown result type (might be due to invalid IL or missing references)
		//IL_001c: Expected O, but got Unknown
		int num = ModMain.mod_ID + 1;
		UICustomDramaDyn val = new UICustomDramaDyn(num);
		int num2 = ModMain.mod_ID + 1;
		((UICustomDramaBase)val).dramaData.dialogueOptions[num2] = opText;
		((UICustomDramaBase)val).dramaData.dialogueText[num] = Text;
		val.SetOptionCall(num2, Action.op_Implicit(action));
		AIActionMessage aIActionMessageTemp = CommandTool.GetXmlText(rspTextTemp);
		if (aIActionMessageTemp != null)
		{
			Console.WriteLine("aIActionMessage不为空！");
			if (!StringExtensions.IsEmpty(aIActionMessageTemp.Option1))
			{
				Console.WriteLine("Option1不为空！");
				int num3 = ModMain.mod_ID + 2;
				((UICustomDramaBase)val).dramaData.dialogueOptions[num3] = aIActionMessageTemp.Option1;
				val.SetOptionCall(num3, Action.op_Implicit((Action)delegate
				{
					InputText.GetComponent<InputField>().text = aIActionMessageTemp.Option1;
				}));
			}
			else
			{
				Console.WriteLine("Option1为空！");
				int num4 = ModMain.mod_ID + 2;
				((UICustomDramaBase)val).dramaData.dialogueOptions[num4] = "然后呢";
				val.SetOptionCall(num4, Action.op_Implicit((Action)delegate
				{
					InputText.GetComponent<InputField>().text = "然后呢";
				}));
			}
			if (!StringExtensions.IsEmpty(aIActionMessageTemp.Option2))
			{
				Console.WriteLine("Option2不为空！");
				int num5 = ModMain.mod_ID + 3;
				((UICustomDramaBase)val).dramaData.dialogueOptions[num5] = aIActionMessageTemp.Option2;
				val.SetOptionCall(num5, Action.op_Implicit((Action)delegate
				{
					InputText.GetComponent<InputField>().text = aIActionMessageTemp.Option2;
				}));
			}
			else
			{
				Console.WriteLine("Option2为空！");
				int num6 = ModMain.mod_ID + 3;
				((UICustomDramaBase)val).dramaData.dialogueOptions[num6] = "继续";
				val.SetOptionCall(num6, Action.op_Implicit((Action)delegate
				{
					InputText.GetComponent<InputField>().text = "继续";
				}));
			}
			if (!StringExtensions.IsEmpty(aIActionMessageTemp.Option3))
			{
				Console.WriteLine("Option3不为空！");
				int num7 = ModMain.mod_ID + 4;
				((UICustomDramaBase)val).dramaData.dialogueOptions[num7] = aIActionMessageTemp.Option3;
				val.SetOptionCall(num7, Action.op_Implicit((Action)delegate
				{
					InputText.GetComponent<InputField>().text = aIActionMessageTemp.Option3;
				}));
			}
			else
			{
				Console.WriteLine("Option3为空！");
				int num8 = ModMain.mod_ID + 4;
				((UICustomDramaBase)val).dramaData.dialogueOptions[num8] = "打个招呼";
				val.SetOptionCall(num8, Action.op_Implicit((Action)delegate
				{
					InputText.GetComponent<InputField>().text = "打个招呼";
				}));
			}
			if (!StringExtensions.IsEmpty(aIActionMessageTemp.Option4))
			{
				Console.WriteLine("Option4不为空！");
				int num9 = ModMain.mod_ID + 5;
				((UICustomDramaBase)val).dramaData.dialogueOptions[num9] = aIActionMessageTemp.Option4;
				val.SetOptionCall(num9, Action.op_Implicit((Action)delegate
				{
					InputText.GetComponent<InputField>().text = aIActionMessageTemp.Option4;
				}));
			}
			else
			{
				Console.WriteLine("Option4为空！");
				int num10 = ModMain.mod_ID + 5;
				((UICustomDramaBase)val).dramaData.dialogueOptions[num10] = "下次再聊，再见！";
				val.SetOptionCall(num10, Action.op_Implicit((Action)delegate
				{
					InputText.GetComponent<InputField>().text = "下次再聊，再见！";
				}));
			}
		}
		else
		{
			Console.WriteLine("aIActionMessage为空！");
			int num11 = ModMain.mod_ID + 6;
			((UICustomDramaBase)val).dramaData.dialogueOptions[num11] = "打个招呼";
			val.SetOptionCall(num11, Action.op_Implicit((Action)delegate
			{
				InputText.GetComponent<InputField>().text = "打个招呼";
			}));
			int num12 = ModMain.mod_ID + 7;
			((UICustomDramaBase)val).dramaData.dialogueOptions[num12] = "表达情感";
			val.SetOptionCall(num12, Action.op_Implicit((Action)delegate
			{
				InputText.GetComponent<InputField>().text = "表达情感";
			}));
		}
		((UICustomDramaBase)val).dramaData.unitLeft = leftUnit;
		((UICustomDramaBase)val).dramaData.unitRight = rightUnit;
		val.OpenUI();
	}

	public void Update()
	{
		//IL_036c: Unknown result type (might be due to invalid IL or missing references)
		//IL_0372: Unknown result type (might be due to invalid IL or missing references)
		//IL_03f8: Unknown result type (might be due to invalid IL or missing references)
		//IL_03fe: Unknown result type (might be due to invalid IL or missing references)
		//IL_0484: Unknown result type (might be due to invalid IL or missing references)
		//IL_048a: Unknown result type (might be due to invalid IL or missing references)
		//IL_0510: Unknown result type (might be due to invalid IL or missing references)
		//IL_0516: Unknown result type (might be due to invalid IL or missing references)
		//IL_0106: Unknown result type (might be due to invalid IL or missing references)
		//IL_0112: Unknown result type (might be due to invalid IL or missing references)
		//IL_0122: Unknown result type (might be due to invalid IL or missing references)
		try
		{
			if (isUpdate)
			{
				foreach (KeyValuePair<string, GameObject> item in msgObject)
				{
					if (!msgText.ContainsKey(item.Key))
					{
						continue;
					}
					string text = msgText[item.Key];
					if (!StringExtensions.IsEmpty(text))
					{
						int num = text.IndexOf("]");
						if (text.StartsWith("["))
						{
							text = text.Substring(num + 1);
						}
						text = text.Trim('\r', '\n');
						string text2 = CommandTool.RegexForOutPut(text);
						((Component)item.Value.transform.Find("Right/Text")).GetComponent<Text>().text = text2;
						RectTransform component = ((Component)item.Value.transform.Find("Right/Text")).GetComponent<RectTransform>();
						LayoutRebuilder.ForceRebuildLayoutImmediate(component);
						Canvas.ForceUpdateCanvases();
						RectTransform component2 = ((Component)item.Value.transform).GetComponent<RectTransform>();
						component2.sizeDelta = new Vector2(component2.sizeDelta.x, component.sizeDelta.y + 100f);
					}
					else
					{
						Console.WriteLine("待刷新内容为空");
					}
					if (text.Contains("错误信息"))
					{
						g.timer.Stop(groupThread);
						((Selectable)ButtonSend.GetComponent<Button>()).interactable = true;
					}
				}
				ScrollRect component3 = ((Component)((Component)this).transform.Find("Root/PanelMain/OutPut/Panel/Scroll View")).GetComponent<ScrollRect>();
				LayoutRebuilder.ForceRebuildLayoutImmediate(component3.content);
				Canvas.ForceUpdateCanvases();
				component3.verticalNormalizedPosition = 0f;
				isUpdate = false;
			}
			if (isUpdateLog)
			{
				if (selIndex >= 0 && selIndex < ModMain.chatGroup.Groups.Count)
				{
					foreach (string item2 in ModMain.chatGroup.Groups[selIndex].UnitsID)
					{
						if (ModMain.unitLog.ContainsKey(item2))
						{
							WorldUnitBase unit = g.world.unit.GetUnit(item2, true);
							if (unit != null)
							{
								CommandTool.AddToGroupLog(unit, isShow: false);
							}
							continue;
						}
						UITipItem.AddTip("日志提炼失败！", 0f);
						break;
					}
					if (ModMain.unitLog.ContainsKey(g.world.playerUnit.data.unitData.unitID))
					{
						CommandTool.AddToGroupLog(g.world.playerUnit, isShow: false);
					}
				}
				isUpdateLog = false;
			}
			if (!isComplete || curUnit == null)
			{
				return;
			}
			if (msgObject.ContainsKey(curUnit.data.unitData.unitID))
			{
				Console.WriteLine("开始输出内容UI布局");
				GameObject Item = msgObject[curUnit.data.unitData.unitID];
				index++;
				int tempIndex = index;
				Console.WriteLine("当前聊天记录序号index：" + index);
				((Component)Item.transform.Find("Right/ButtonDel")).gameObject.AddComponent<UISkyTipEffect>().InitData("删除该条聊天记录", default(Vector3));
				((UnityEventBase)((Component)Item.transform.Find("Right/ButtonDel")).GetComponent<Button>().onClick).RemoveAllListeners();
				((UnityEvent)((Component)Item.transform.Find("Right/ButtonDel")).GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
				{
					g.ui.OpenUI<UICheckPopup>(UIType.CheckPopup).InitData("提示", "是否确认删除该组聊天记录？", 2, Action.op_Implicit((Action)delegate
					{
						try
						{
							if (isChat)
							{
								for (int i = 0; i < ModMain.chatGroup.Groups[selIndex].Messages.Count; i++)
								{
									if (i == tempIndex)
									{
										ModMain.chatGroup.Groups[selIndex].Messages.RemoveAt(tempIndex);
										break;
									}
								}
								UpdateMessageList();
							}
							else
							{
								for (int j = 0; j < ModMain.chatGroup.Groups[selIndex].ActionMessage.Count; j++)
								{
									if (j == tempIndex)
									{
										ModMain.chatGroup.Groups[selIndex].ActionMessage.RemoveAt(tempIndex);
										break;
									}
								}
								UpdateMessageList();
							}
						}
						catch (Exception ex2)
						{
							Console.WriteLine(ex2.Message);
						}
					}), (Action)null);
				}));
				((Component)Item.transform.Find("Right/ButtonCopy")).gameObject.AddComponent<UISkyTipEffect>().InitData("复制该条聊天记录", default(Vector3));
				((UnityEventBase)((Component)Item.transform.Find("Right/ButtonCopy")).GetComponent<Button>().onClick).RemoveAllListeners();
				((UnityEvent)((Component)Item.transform.Find("Right/ButtonCopy")).GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
				{
					try
					{
						Clipboard.SetDataObject(((Component)Item.transform.Find("Right/Text")).GetComponent<Text>().text, copy: true);
					}
					catch (Exception ex2)
					{
						Console.WriteLine(ex2.Message);
					}
				}));
				((Component)Item.transform.Find("Right/ButtonModity")).gameObject.AddComponent<UISkyTipEffect>().InitData("修改该条聊天记录", default(Vector3));
				((UnityEventBase)((Component)Item.transform.Find("Right/ButtonModity")).GetComponent<Button>().onClick).RemoveAllListeners();
				((UnityEvent)((Component)Item.transform.Find("Right/ButtonModity")).GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
				{
					ModityChatMessage(tempIndex, isUpdata: true);
				}));
				((Component)Item.transform.Find("Right/ButtonLook")).gameObject.AddComponent<UISkyTipEffect>().InitData("查看该条聊天记录完整信息", default(Vector3));
				((UnityEventBase)((Component)Item.transform.Find("Right/ButtonLook")).GetComponent<Button>().onClick).RemoveAllListeners();
				((UnityEvent)((Component)Item.transform.Find("Right/ButtonLook")).GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
				{
					try
					{
						if (isChat)
						{
							string content = ModMain.chatGroup.Groups[selIndex].Messages[tempIndex].content;
							g.ui.OpenUI<UITextInfoLong>(UIType.TextInfoLong).InitData("显示完整内容", content, "", (Action)null, false);
						}
						else
						{
							string content2 = ModMain.chatGroup.Groups[selIndex].ActionMessage[tempIndex].content;
							g.ui.OpenUI<UITextInfoLong>(UIType.TextInfoLong).InitData("显示完整内容", content2, "", (Action)null, false);
						}
					}
					catch (Exception ex2)
					{
						Console.WriteLine(ex2.Message);
					}
				}));
			}
			isComplete = false;
		}
		catch (Exception ex)
		{
			Console.WriteLine(ex.ToString());
		}
	}

	public void UpdataGroupList()
	{
		//IL_00e2: Unknown result type (might be due to invalid IL or missing references)
		//IL_00e8: Unknown result type (might be due to invalid IL or missing references)
		for (int num = GroupNameList.transform.GetChildCount() - 1; num >= 0; num--)
		{
			Object.Destroy((Object)(object)((Component)GroupNameList.transform.GetChild(num)).gameObject);
		}
		for (int i = 0; i < ModMain.chatGroup.Groups.Count; i++)
		{
			int idx = i;
			GameObject go = Object.Instantiate<GameObject>(g.res.Load<GameObject>("UI/GroupNameItem"), GroupNameList.transform);
			((Component)go.transform.Find("Text")).GetComponent<Text>().text = ModMain.chatGroup.Groups[idx].Name;
			((Component)go.transform).gameObject.AddComponent<UISkyTipEffect>().InitData(ModMain.chatGroup.Groups[idx].Description, default(Vector3));
			((UnityEventBase)((Component)go.transform).GetComponent<Button>().onClick).RemoveAllListeners();
			((UnityEvent)((Component)go.transform).GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
			{
				if (isUpdate || state == 1)
				{
					UITipItem.AddTip("对方正在回复中,请稍微再试", 0f);
				}
				else
				{
					selIndex = idx;
					UpdataGroupInfo(go, selIndex);
					Console.WriteLine("选择序号:" + selIndex);
				}
			}));
			if (idx == selIndex)
			{
				UpdataGroupInfo(go, selIndex);
				((Selectable)((Component)go.transform).GetComponent<Button>()).Select();
			}
		}
		LayoutRebuilder.ForceRebuildLayoutImmediate(((Component)((Component)this).transform.Find("Root/PanelGroup/GroupNameList")).GetComponent<ScrollRect>().content);
		Canvas.ForceUpdateCanvases();
	}

	public void UpdataGroupInfo(GameObject go, int indexTemp)
	{
		//IL_0119: Unknown result type (might be due to invalid IL or missing references)
		//IL_02fa: Unknown result type (might be due to invalid IL or missing references)
		//IL_0300: Unknown result type (might be due to invalid IL or missing references)
		if ((Object)(object)go == (Object)null || indexTemp < 0 || indexTemp >= ModMain.chatGroup.Groups.Count)
		{
			return;
		}
		((Component)((Component)this).transform.Find("Root/PanelGroup/Button/Text")).GetComponent<Text>().text = ModMain.chatGroup.Groups[indexTemp].Name + $"({ModMain.chatGroup.Groups[indexTemp].UnitsID.Count})";
		if (isChat)
		{
			UpdateMessageList();
		}
		else
		{
			UpdateMessageList();
		}
		for (int num = GroupUnitList.transform.GetChildCount() - 1; num >= 0; num--)
		{
			Object.Destroy((Object)(object)((Component)GroupUnitList.transform.GetChild(num)).gameObject);
		}
		GameObject val = g.res.Load<GameObject>("UI/Item/NPCUnitInfoItem");
		val.transform.localPosition = new Vector3(0f, 0f);
		for (int i = 0; i < ModMain.chatGroup.Groups[indexTemp].UnitsID.Count; i++)
		{
			int jdx = i;
			string text = ModMain.chatGroup.Groups[indexTemp].UnitsID[jdx];
			WorldUnitBase unit = g.world.unit.GetUnit(text, true);
			if (unit == null)
			{
				continue;
			}
			GameObject val2 = Object.Instantiate<GameObject>(g.res.Load<GameObject>("UI/GroupListUnitItem"), GroupUnitList.transform);
			int num2 = 1;
			if (unit.data.unitData.objData.ContainsKey("isSelInGroup"))
			{
				num2 = unit.data.unitData.objData.GetInt("isSelInGroup");
			}
			else
			{
				unit.data.unitData.objData.SetString("isSelInGroup", 1);
			}
			val2.GetComponent<Toggle>().isOn = num2 == 1;
			((UnityEvent<bool>)(object)val2.GetComponent<Toggle>().onValueChanged).AddListener(UnityAction<bool>.op_Implicit((Action<bool>)delegate(bool value)
			{
				if (value)
				{
					unit.data.unitData.objData.SetString("isSelInGroup", 1);
				}
				else
				{
					unit.data.unitData.objData.SetString("isSelInGroup", 0);
				}
			}));
			Transform val3 = val2.transform.Find("Unit");
			GameObject obj = Object.Instantiate<GameObject>(val, ((Component)val3).transform);
			((Object)obj).name = "NPCUnitInfoItem";
			obj.gameObject.SetActive(true);
			UINPCUnitInfoItem component = obj.GetComponent<UINPCUnitInfoItem>();
			if ((Object)(object)component != (Object)null)
			{
				val2.SetActive(true);
				component.Init();
				component.InitData(unit, true, true);
				component.ShowGrade(true);
				component.UpdateUI(true);
			}
			Transform val4 = val2.transform.Find("Button");
			if (!((Object)(object)val4 != (Object)null))
			{
				continue;
			}
			((Component)val4).gameObject.AddComponent<UISkyTipEffect>().InitData("调整修仙者的发言顺序！", default(Vector3));
			((UnityEvent)((Component)val4).GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
			{
				try
				{
					if (jdx > 0)
					{
						string value = ModMain.chatGroup.Groups[indexTemp].UnitsID[jdx];
						ModMain.chatGroup.Groups[indexTemp].UnitsID[jdx] = ModMain.chatGroup.Groups[indexTemp].UnitsID[jdx - 1];
						ModMain.chatGroup.Groups[indexTemp].UnitsID[jdx - 1] = value;
						UpdataGroupList();
					}
					else
					{
						UITipItem.AddTip("此修仙者的优先级已达到最高！", 2f);
					}
				}
				catch (Exception ex)
				{
					Console.WriteLine(ex.ToString());
					Console.WriteLine("goUnitUP出错");
				}
			}));
		}
		LayoutRebuilder.ForceRebuildLayoutImmediate(((Component)((Component)this).transform.Find("Root/PanelGroup/GroupUnitList")).GetComponent<ScrollRect>().content);
		Canvas.ForceUpdateCanvases();
	}

	private void OnUpdate()
	{
		if (Input.GetKey((KeyCode)305) || Input.GetKey((KeyCode)307) || !Input.GetKeyDown((KeyCode)13))
		{
			return;
		}
		try
		{
			if (InputText.GetComponent<InputField>().isFocused)
			{
				SenndMessage();
			}
		}
		catch (Exception ex)
		{
			Console.WriteLine(ex.ToString());
		}
	}

	public void InitData(bool isFirst = true)
	{
		//IL_0026: Unknown result type (might be due to invalid IL or missing references)
		//IL_002c: Expected O, but got Unknown
		//IL_00c3: Unknown result type (might be due to invalid IL or missing references)
		//IL_00c9: Unknown result type (might be due to invalid IL or missing references)
		//IL_01b9: Unknown result type (might be due to invalid IL or missing references)
		//IL_01bf: Unknown result type (might be due to invalid IL or missing references)
		//IL_01e6: Unknown result type (might be due to invalid IL or missing references)
		//IL_01ec: Unknown result type (might be due to invalid IL or missing references)
		//IL_0394: Unknown result type (might be due to invalid IL or missing references)
		//IL_039a: Unknown result type (might be due to invalid IL or missing references)
		//IL_05ae: Unknown result type (might be due to invalid IL or missing references)
		//IL_05b4: Unknown result type (might be due to invalid IL or missing references)
		//IL_0601: Unknown result type (might be due to invalid IL or missing references)
		//IL_0607: Unknown result type (might be due to invalid IL or missing references)
		//IL_065e: Unknown result type (might be due to invalid IL or missing references)
		//IL_0664: Unknown result type (might be due to invalid IL or missing references)
		//IL_06bb: Unknown result type (might be due to invalid IL or missing references)
		//IL_06c1: Unknown result type (might be due to invalid IL or missing references)
		//IL_0718: Unknown result type (might be due to invalid IL or missing references)
		//IL_071e: Unknown result type (might be due to invalid IL or missing references)
		//IL_07aa: Unknown result type (might be due to invalid IL or missing references)
		//IL_07b4: Expected O, but got Unknown
		try
		{
			ModMain.preText.isShow = 0;
			ModMain.openUIType = 2;
			Shader val = Shader.Find("Custom/BackBlur");
			if ((Object)(object)val != (Object)null)
			{
				Material material = new Material(val);
				((Component)((Component)this).transform.GetChild(0)).GetComponent<Image>().material = material;
			}
			if (ModMain.chatGroup.Groups.Count > 0)
			{
				selIndex = 0;
			}
			corUpdate = g.timer.Frame(Action.op_Implicit((Action)OnUpdate), 1, true);
			if (CommandTool.GetAIAction() == 0)
			{
				isChat = true;
			}
			else
			{
				isChat = false;
			}
			ButtonSend = ((Component)((Component)this).transform.Find("Root/PanelMain/ButtonSend")).gameObject;
			ButtonSend.AddComponent<UISkyTipEffect>().InitData("传音（可快捷：Enter发送，右Ctrl+Enter换行）", default(Vector3));
			((UnityEventBase)ButtonSend.GetComponent<Button>().onClick).RemoveAllListeners();
			((UnityEvent)ButtonSend.GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
			{
				try
				{
					SenndMessage();
				}
				catch (Exception ex2)
				{
					Console.WriteLine(ex2.ToString());
					Console.WriteLine("ButtonSend出错");
				}
			}));
			OutPutContent = ((Component)((Component)this).transform.Find("Root/PanelMain/OutPut/Panel/Scroll View/Viewport/Content")).gameObject;
			InputText = ((Component)((Component)this).transform.Find("Root/PanelMain/Input/Panel/InputField")).gameObject;
			GroupNameList = ((Component)((Component)this).transform.Find("Root/PanelGroup/GroupNameList/Viewport/Content")).gameObject;
			GroupUnitList = ((Component)((Component)this).transform.Find("Root/PanelGroup/GroupUnitList/Viewport/Content")).gameObject;
			textNewMsg = InputText.GetComponent<InputField>().text;
			ToggleChat = ((Component)((Component)this).transform.Find("Root/PanelMain/Input/PanelToggle/ToggleChat")).gameObject;
			ToggleChat.AddComponent<UISkyTipEffect>().InitData("勾选后切换交互模式，会根据聊天内容智能开展行为交互，回复内容一次性读取解析及显示，回复速度有一定影响。\n取消勾选后切换为聊天模式，可正常传音及NPC实时回复，V3模型回复速度快。\n", default(Vector3));
			((Component)((Component)this).transform.Find("Root/PanelMain/Input/PanelToggle/ToggleAction")).gameObject.AddComponent<UISkyTipEffect>().InitData("勾选后切换交互模式，会根据聊天内容智能开展行为交互，回复内容一次性读取解析及显示，回复速度有一定影响", default(Vector3));
			if (isChat)
			{
				ToggleChat.GetComponent<Toggle>().isOn = false;
			}
			else
			{
				ToggleChat.GetComponent<Toggle>().isOn = true;
			}
			((UnityEventBase)ToggleChat.GetComponent<Toggle>().onValueChanged).RemoveAllListeners();
			((UnityEvent<bool>)(object)ToggleChat.GetComponent<Toggle>().onValueChanged).AddListener(UnityAction<bool>.op_Implicit((Action<bool>)onValueChangedChat));
			goPreText = ((Component)((Component)this).transform.Find("Root/PanelLeft/Scroll View/Viewport/Content")).gameObject;
			ButtonAdd = ((Component)((Component)this).transform.Find("Root/PanelLeft/ButtonAdd")).gameObject;
			ButtonDel = ((Component)((Component)this).transform.Find("Root/PanelLeft/ButtonDel")).gameObject;
			ButtonModity = ((Component)((Component)this).transform.Find("Root/PanelLeft/ButtonModity")).gameObject;
			TextPreActionName = ((Component)((Component)this).transform.Find("Root/PanelLeft/Text2")).gameObject;
			ButtonClearAction = ((Component)((Component)this).transform.Find("Root/PanelLeft/ButtonClearAction")).gameObject;
			DropdownType = ((Component)((Component)this).transform.Find("Root/PanelLeft/DropdownType")).gameObject;
			InputFieldFind = ((Component)((Component)this).transform.Find("Root/PanelLeft/InputFieldFind")).gameObject;
			ButtonFind = ((Component)((Component)this).transform.Find("Root/PanelLeft/ButtonFind")).gameObject;
			ToggleShowLeft = ((Component)((Component)this).transform.Find("Root/PanelMain/ToggleShowLeft")).gameObject;
			ButtonRandomOP = ((Component)((Component)this).transform.Find("Root/PanelMain/ButtonRandomOP")).gameObject;
			ButtonRandomOP.AddComponent<UISkyTipEffect>().InitData("随便聊聊", default(Vector3));
			((Component)((Component)this).transform.Find("Root/PanelMain/ButtonExit")).GetComponent<Image>().sprite = SpriteTool.GetSprite("Common", "tuichu");
			((UnityEvent)((Component)((Component)this).transform.Find("Root/PanelMain/ButtonExit")).GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
			{
				//IL_00b0: Unknown result type (might be due to invalid IL or missing references)
				//IL_00bb: Expected O, but got Unknown
				if (state != 0)
				{
					client.CancelPendingRequests();
					ActionClient.CancelPendingRequests();
					state = 0;
					textOldMsg = "道友幸会，今天很不凑巧，神识传音似乎失败了，传闻是通道中的三个核心出错了。。。";
					UITipItem.AddTip("正在传音中，强行退出可能导致通道崩溃，请稍后再试。。。", 3f);
					isUpdate = true;
				}
				else
				{
					TimerCoroutine obj = actionThread;
					if (obj != null && !obj.isStop)
					{
						g.timer.Stop(actionThread);
					}
					if (corUpdate != null)
					{
						g.timer.Stop(corUpdate);
					}
					if (isChat)
					{
						CommandTool.SetAIAction();
					}
					else
					{
						CommandTool.SetAIAction(1);
					}
					ModMain.chatGroup.SaveGroup();
					g.ui.CloseUI(new UITypeBase("UIChatAINewGroup", (UILayer)0), false);
					ModMain.openUIType = 0;
				}
			}));
			((UnityEvent)ButtonAdd.GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
			{
				//IL_0010: Unknown result type (might be due to invalid IL or missing references)
				//IL_001a: Expected O, but got Unknown
				ClassInjector.RegisterTypeInIl2Cpp<UIInputPreText>();
				UIInputPreText uIInputPreText = ((Component)g.ui.OpenUI(new UITypeBase("UIInputPreText", (UILayer)0))).gameObject.AddComponent<UIInputPreText>();
				uIInputPreText.InitData("", 0);
				((UnityEvent)uIInputPreText.ButtonSave.GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
				{
					try
					{
						if (StringExtensions.IsEmpty(UIInputPreText.text))
						{
							UITipItem.AddTip("常用语新增失败!", 3f);
						}
						else
						{
							ModMain.preText.items.Add(new PreTextItem
							{
								id = ModMain.preText.items.Count + 1,
								actionType = UIInputPreText.actionIndex,
								text = UIInputPreText.text
							});
							UITipItem.AddTip("添加成功！", 0f);
							UpdataPreText();
							ModMain.preText.Save();
						}
					}
					catch (Exception ex2)
					{
						Console.WriteLine(ex2.ToString());
					}
				}));
			}));
			((UnityEvent)ButtonDel.GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
			{
				if (selPreTextIndex >= 0 && selPreTextIndex < ModMain.preText.items.Count)
				{
					g.ui.OpenUI<UICheckPopup>(UIType.CheckPopup).InitData("提示", "是否确定将选中的常用语删除？", 2, Action.op_Implicit((Action)delegate
					{
						ModMain.preText.items.RemoveAt(selPreTextIndex);
						selPreTextIndex = -1;
						UpdataPreText();
						UITipItem.AddTip("删除成功", 3f);
						ModMain.preText.Save();
					}), (Action)null);
				}
			}));
			((UnityEvent)ButtonModity.GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
			{
				//IL_0036: Unknown result type (might be due to invalid IL or missing references)
				//IL_0040: Expected O, but got Unknown
				if (selPreTextIndex >= 0 && selPreTextIndex < ModMain.preText.items.Count)
				{
					ClassInjector.RegisterTypeInIl2Cpp<UIInputPreText>();
					UIInputPreText uIInputPreText = ((Component)g.ui.OpenUI(new UITypeBase("UIInputPreText", (UILayer)0))).gameObject.AddComponent<UIInputPreText>();
					uIInputPreText.InitData(ModMain.preText.items[selPreTextIndex].text, ModMain.preText.items[selPreTextIndex].actionType);
					((UnityEvent)uIInputPreText.ButtonSave.GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
					{
						try
						{
							if (StringExtensions.IsEmpty(UIInputPreText.text))
							{
								UITipItem.AddTip("记录修改失败!", 3f);
							}
							else
							{
								ModMain.preText.items.Add(new PreTextItem
								{
									id = ModMain.preText.items.Count + 1,
									actionType = UIInputPreText.actionIndex,
									text = UIInputPreText.text
								});
								UITipItem.AddTip("修改成功！", 0f);
								UpdataPreText();
								ModMain.preText.Save();
							}
						}
						catch (Exception ex2)
						{
							Console.WriteLine(ex2.ToString());
						}
					}));
				}
			}));
			((UnityEvent)ButtonClearAction.GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
			{
				selPreTextIndex = -1;
				TextPreActionName.GetComponent<Text>().text = "";
			}));
			ToggleShowLeft.GetComponent<Toggle>().isOn = ModMain.preText.isShow == 1;
			((UnityEvent<bool>)(object)ToggleShowLeft.GetComponent<Toggle>().onValueChanged).AddListener(UnityAction<bool>.op_Implicit((Action<bool>)delegate(bool value)
			{
				ModMain.preText.isShow = (value ? 1 : 0);
				((Component)((Component)this).transform.Find("Root/PanelLeft")).gameObject.SetActive(value);
				ModMain.preText.Save();
			}));
			((Component)((Component)this).transform.Find("Root/PanelLeft")).gameObject.SetActive(ModMain.preText.isShow == 1);
			((UnityEvent)((Component)((Component)this).transform.Find("Root/PanelMain/OutPut/ButtonNext")).GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
			{
				curPage++;
				UpdateMessageList();
			}));
			((UnityEvent)((Component)((Component)this).transform.Find("Root/PanelMain/OutPut/ButtonLast")).GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
			{
				curPage--;
				UpdateMessageList();
			}));
			((UnityEvent)ButtonRandomOP.GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
			{
				ShowDrama(g.world.playerUnit, null, "你还想聊些什么？", "随便聊聊", delegate
				{
					InputText.GetComponent<InputField>().text = "随便聊聊";
				});
			}));
			ButtonToLog = ((Component)((Component)this).transform.Find("Root/PanelMain/ButtonToLog")).gameObject;
			ButtonToLog.AddComponent<UISkyTipEffect>().InitData("聊天记录管理及经历提炼", default(Vector3));
			((UnityEvent)ButtonToLog.GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
			{
				//IL_0030: Unknown result type (might be due to invalid IL or missing references)
				//IL_003a: Expected O, but got Unknown
				if (selIndex >= 0 && selIndex < ModMain.chatGroup.Groups.Count)
				{
					ClassInjector.RegisterTypeInIl2Cpp<UIMSGList>();
					((Component)g.ui.OpenUI(new UITypeBase("UIMSGList", (UILayer)0))).gameObject.AddComponent<UIMSGList>().InitData(selIndex);
					((UnityEvent)((Component)((Component)this).transform.Find("Root/PanelMain/ButtonExit")).GetComponent<Button>().onClick).Invoke();
				}
				else
				{
					UITipItem.AddTip("群组未选择或选择错误", 3f);
				}
			}));
			((Component)((Component)this).transform.Find("Root/PanelMain/ButtonSetGroup")).gameObject.AddComponent<UISkyTipEffect>().InitData("群组设置", default(Vector3));
			((UnityEvent)((Component)((Component)this).transform.Find("Root/PanelMain/ButtonSetGroup")).GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
			{
				//IL_0010: Unknown result type (might be due to invalid IL or missing references)
				//IL_001a: Expected O, but got Unknown
				try
				{
					ClassInjector.RegisterTypeInIl2Cpp<UIAIGroupCommand>();
					UIAIGroupCommand uIAIGroupCommand2 = ((Component)g.ui.OpenUI(new UITypeBase("UIAIGroupCommand", (UILayer)0))).gameObject.AddComponent<UIAIGroupCommand>();
					uIAIGroupCommand2.InitData(selIndex);
					((UnityEvent)uIAIGroupCommand2.ButtonExit.GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
					{
						g.timer.Time(Action.op_Implicit((Action)delegate
						{
							try
							{
								InitAI();
							}
							catch (Exception ex3)
							{
								Console.WriteLine(ex3.Message);
							}
						}), 2f, false);
					}));
				}
				catch (Exception ex2)
				{
					Console.WriteLine(ex2.Message);
				}
			}));
			((Component)((Component)this).transform.Find("Root/PanelMain/ButtonSetting")).gameObject.AddComponent<UISkyTipEffect>().InitData("AI参数设置", default(Vector3));
			((UnityEvent)((Component)((Component)this).transform.Find("Root/PanelMain/ButtonSetting")).GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
			{
				//IL_0010: Unknown result type (might be due to invalid IL or missing references)
				//IL_001a: Expected O, but got Unknown
				ClassInjector.RegisterTypeInIl2Cpp<UISetting>();
				UISetting uISetting = ((Component)g.ui.OpenUI(new UITypeBase("UISetting", (UILayer)0))).gameObject.AddComponent<UISetting>();
				uISetting.InitData();
				((UnityEvent)uISetting.ButtonOK.GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
				{
					g.timer.Time(Action.op_Implicit((Action)delegate
					{
						InitAI();
					}), 2f, false);
				}));
			}));
			((Component)((Component)this).transform.Find("Root/PanelMain/ButtonClear")).gameObject.AddComponent<UISkyTipEffect>().InitData("清空聊天记录", default(Vector3));
			((UnityEvent)((Component)((Component)this).transform.Find("Root/PanelMain/ButtonClear")).GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
			{
				if (selIndex >= 0 && selIndex < ModMain.chatGroup.Groups.Count)
				{
					g.ui.OpenUI<UICheckPopup>(UIType.CheckPopup).InitData("提示", "是否确认删除所有聊天记录？", 2, Action.op_Implicit((Action)delegate
					{
						g.ui.OpenUI<UICheckPopup>(UIType.CheckPopup).InitData("提示", "是否需要将聊天记录进行提炼并纳入NPC个人经历（聊天记录数量大于2条生效）？", 2, Action.op_Implicit((Action)delegate
						{
							string text = "当前处于群聊模式。\n" + ModMain.aIPromptCommand.promptFile.unitLogPrompt + "\n[原始对话]\n";
							for (int i = 4; i < ModMain.chatGroup.Groups[selIndex].Messages.Count; i++)
							{
								text = text + ModMain.chatGroup.Groups[selIndex].Messages[i].name + ":" + ModMain.chatGroup.Groups[selIndex].Messages[i].content + "\n";
							}
							if (text.Length > 150)
							{
								g.timer.Time(Action.op_Implicit((Action)async delegate
								{
									try
									{
										UITipItem.AddTip("开始提炼聊天记录。", 3f);
										Console.WriteLine("输入内容：" + text);
										await CommandTool.SendToDeepSeekForGroupLog(ModMain.chatGroup.Groups[selIndex], "user", text);
										isUpdateLog = true;
									}
									catch (Exception ex2)
									{
										Console.WriteLine(ex2.Message ?? "");
									}
								}), 0f, false);
							}
							ModMain.chatGroup.Groups[selIndex].Messages.Clear();
							ModMain.chatGroup.Groups[selIndex].Messages = new List<GroupMessage>();
							ModMain.chatGroup.Groups[selIndex].ActionMessage.Clear();
							ModMain.chatGroup.Groups[selIndex].ActionMessage = new List<GroupMessage>();
							if (isChat)
							{
								UpdateMessageList();
							}
							else
							{
								UpdateMessageList();
							}
							g.timer.Time(Action.op_Implicit((Action)delegate
							{
								foreach (string item in ModMain.chatGroup.Groups[selIndex].UnitsID)
								{
									WorldUnitBase unit = g.world.unit.GetUnit(item, true);
									if (unit != null)
									{
										for (int j = 0; j < ModMain.vectorKnowledgeCMD.vectorKnowledge.knowledge.Count; j++)
										{
											KeyValuePair<string, VectorKnowledgeItem> keyValuePair = ModMain.vectorKnowledgeCMD.vectorKnowledge.knowledge.ElementAt(j);
											if (keyValuePair.Key.Contains(unit.GetUnitID() + "群聊"))
											{
												ModMain.vectorKnowledgeCMD.vectorKnowledge.knowledge.Remove(keyValuePair.Key);
												ModMain.vectorKnowledgeCMD.Save();
												break;
											}
										}
									}
								}
							}), 0f, false);
						}), Action.op_Implicit((Action)delegate
						{
							ModMain.chatGroup.Groups[selIndex].Messages.Clear();
							ModMain.chatGroup.Groups[selIndex].Messages = new List<GroupMessage>();
							ModMain.chatGroup.Groups[selIndex].ActionMessage.Clear();
							ModMain.chatGroup.Groups[selIndex].ActionMessage = new List<GroupMessage>();
							if (isChat)
							{
								UpdateMessageList();
							}
							else
							{
								UpdateMessageList();
							}
							g.timer.Time(Action.op_Implicit((Action)delegate
							{
								foreach (string item2 in ModMain.chatGroup.Groups[selIndex].UnitsID)
								{
									WorldUnitBase unit = g.world.unit.GetUnit(item2, true);
									if (unit != null)
									{
										for (int i = 0; i < ModMain.vectorKnowledgeCMD.vectorKnowledge.knowledge.Count; i++)
										{
											KeyValuePair<string, VectorKnowledgeItem> keyValuePair = ModMain.vectorKnowledgeCMD.vectorKnowledge.knowledge.ElementAt(i);
											if (keyValuePair.Key.Contains(unit.GetUnitID() + "群聊"))
											{
												ModMain.vectorKnowledgeCMD.vectorKnowledge.knowledge.Remove(keyValuePair.Key);
												ModMain.vectorKnowledgeCMD.Save();
												break;
											}
										}
									}
								}
							}), 0f, false);
						}));
					}), (Action)null);
				}
				else
				{
					UITipItem.AddTip("群组未选择或选择错误", 3f);
				}
			}));
			((Component)((Component)this).transform.Find("Root/PanelMain/ButtonCopy")).gameObject.AddComponent<UISkyTipEffect>().InitData("复制所有聊天记录", default(Vector3));
			((UnityEvent)((Component)((Component)this).transform.Find("Root/PanelMain/ButtonCopy")).GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
			{
				if (selIndex >= 0 && selIndex < ModMain.chatGroup.Groups.Count)
				{
					g.ui.OpenUI<UICheckPopup>(UIType.CheckPopup).InitData("提示", "是否确认复制所有聊天记录至剪切板？", 2, Action.op_Implicit((Action)delegate
					{
						string text = "";
						for (int i = 4; i < ModMain.chatGroup.Groups[selIndex].Messages.Count; i++)
						{
							text = text + ModMain.chatGroup.Groups[selIndex].Messages[i].name + ":" + ModMain.chatGroup.Groups[selIndex].Messages[i].content + "\n";
						}
						try
						{
							Clipboard.SetDataObject(text, copy: true);
							UITipItem.AddTip("所有聊天记录已复制", 2f);
						}
						catch (Exception ex2)
						{
							Console.WriteLine("复制失败: " + ex2.Message);
						}
					}), (Action)null);
				}
				else
				{
					UITipItem.AddTip("群组未选择或选择错误", 3f);
				}
			}));
			((UnityEvent)ButtonFind.GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
			{
				try
				{
					string text = InputFieldFind.GetComponent<InputField>().text;
					if (!string.IsNullOrEmpty(text))
					{
						bool flag = false;
						int num = 0;
						if (selPreIndex >= 0 && selPreIndex < goPreText.transform.GetChildCount() - 1)
						{
							num = selPreIndex + 1;
						}
						for (int i = num; i < goPreText.transform.GetChildCount(); i++)
						{
							Transform child = goPreText.transform.GetChild(i);
							if ((Object)(object)child != (Object)null && (((Component)child.Find("TextActionType")).GetComponent<Text>().text + ":" + ((Component)child.Find("Text")).GetComponent<Text>().text).Contains(text))
							{
								((Selectable)((Component)child).GetComponent<Button>()).Select();
								ScrollRect component = ((Component)((Component)this).transform.Find("Root/PanelLeft/Scroll View")).GetComponent<ScrollRect>();
								ScrollToItemCoroutine(component, i);
								flag = true;
								selPreIndex = i;
								break;
							}
						}
						if (!flag)
						{
							for (int j = 0; j < goPreText.transform.GetChildCount(); j++)
							{
								Transform child2 = goPreText.transform.GetChild(j);
								if ((Object)(object)child2 != (Object)null && (((Component)child2.Find("TextActionType")).GetComponent<Text>().text + ":" + ((Component)child2.Find("Text")).GetComponent<Text>().text).Contains(text))
								{
									((Selectable)((Component)child2).GetComponent<Button>()).Select();
									ScrollRect component2 = ((Component)((Component)this).transform.Find("Root/PanelLeft/Scroll View")).GetComponent<ScrollRect>();
									ScrollToItemCoroutine(component2, j);
									flag = true;
									selPreIndex = j;
									break;
								}
							}
							if (!flag)
							{
								UITipItem.AddTip("未找到匹配项。", 3f);
							}
							else
							{
								UITipItem.AddTip("已找到匹配项：匹配关键词：" + text, 3f);
							}
						}
						else
						{
							UITipItem.AddTip("已找到匹配项：匹配关键词：" + text, 3f);
						}
					}
					else
					{
						UITipItem.AddTip("待搜索内容为空！", 3f);
					}
				}
				catch (Exception ex2)
				{
					Console.WriteLine(ex2.ToString());
					Console.WriteLine("批量操作出错");
				}
			}));
			if (ModMain.chatGroup.Groups.Count > 0)
			{
				InitAI();
				UpdataGroupList();
			}
			else
			{
				ClassInjector.RegisterTypeInIl2Cpp<UIAIGroupCommand>();
				UIAIGroupCommand uIAIGroupCommand = ((Component)g.ui.OpenUI(new UITypeBase("UIAIGroupCommand", (UILayer)0))).gameObject.AddComponent<UIAIGroupCommand>();
				uIAIGroupCommand.InitData(-1);
				((UnityEvent)uIAIGroupCommand.ButtonExit.GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
				{
					g.timer.Time(Action.op_Implicit((Action)delegate
					{
					}), 2f, false);
				}));
			}
			ModMain.preText.Init();
			UpdataPreText();
			UpdataList();
		}
		catch (Exception ex)
		{
			Console.WriteLine(ex.ToString());
			Console.WriteLine("InitData出错");
		}
	}

	public void UpdataPreText(int type = -1)
	{
		//IL_0147: Unknown result type (might be due to invalid IL or missing references)
		//IL_014d: Unknown result type (might be due to invalid IL or missing references)
		try
		{
			for (int num = goPreText.transform.GetChildCount() - 1; num >= 0; num--)
			{
				Object.Destroy((Object)(object)((Component)goPreText.transform.GetChild(num)).gameObject);
			}
			ActionList actionList = new ActionList();
			for (int i = 0; i < ModMain.preText.items.Count; i++)
			{
				PreTextItem preTextItem = ModMain.preText.items[i];
				if (type != -1 && preTextItem.actionType != type)
				{
					continue;
				}
				GameObject val = Object.Instantiate<GameObject>(g.res.Load<GameObject>("UI/PreTextItem"), goPreText.transform);
				if (!((Object)(object)val != (Object)null))
				{
					continue;
				}
				string actionName = actionList.GetActionName(preTextItem.actionType);
				((Component)val.transform.Find("TextID")).GetComponent<Text>().text = preTextItem.id.ToString();
				((Component)val.transform.Find("TextActionType")).GetComponent<Text>().text = actionName;
				((Component)val.transform.Find("Text")).GetComponent<Text>().text = preTextItem.text;
				string text = $"行动类型:{actionName}\n常用语内容:{preTextItem.text}";
				val.AddComponent<UISkyTipEffect>().InitData(text, default(Vector3));
				int temp = i;
				((UnityEvent)val.GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
				{
					selPreTextIndex = temp;
					if (selPreTextIndex < ModMain.preText.items.Count)
					{
						string text2 = ModMain.preText.items[selPreTextIndex].text;
						InputText.GetComponent<InputField>().text = InputText.GetComponent<InputField>().text + text2;
						TextPreActionName.GetComponent<Text>().text = actionName;
					}
				}));
			}
		}
		catch (Exception ex)
		{
			Console.WriteLine(ex.ToString());
		}
	}

	public void onValueChangedChat(bool value)
	{
		if (state != 0 || isUpdate)
		{
			UITipItem.AddTip("对方正在传音中。。。", 0f);
		}
		else if (value)
		{
			isChat = false;
			UpdateMessageList();
			UITipItem.AddTip("交互模式开启", 2f);
		}
		else
		{
			isChat = true;
			UpdateMessageList();
			UITipItem.AddTip("聊天模式开启", 2f);
		}
	}
}


//==================== TYPE MOD_SSCYAI.UIChatAINew ====================
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading.Tasks;
using System.Web.WebPages;
using System.Windows.Forms;
using Il2CppSystem;
using Il2CppSystem.Collections.Generic;
using Newtonsoft.Json;
using UnhollowerRuntimeLib;
using UnityEngine;
using UnityEngine.Events;
using UnityEngine.UI;

namespace MOD_SSCYAI;

public class UIChatAINew(IntPtr ptr) : UIBase(ptr)
{
	public class ResponseFormat
	{
		public string type { get; set; } = "json_object";

		public string schema { get; set; }
	}

	public class RequestData
	{
		public string model { get; set; }

		public Message[] messages { get; set; }

		public bool stream { get; set; }

		public int max_tokens { get; set; }

		public ResponseFormat response_format { get; set; } = new ResponseFormat();
	}

	private TimerCoroutine corUpdate;

	public WorldUnitBase unit;

	public GameObject ButtonSend;

	public GameObject ButtonClear;

	public GameObject ButtonAction;

	public GameObject InputText;

	public GameObject OutPutContent;

	public GameObject ChatItem;

	public GameObject ToggleChat;

	public GameObject ToggleAction;

	public GameObject ButtonAdd;

	public GameObject ButtonDel;

	public GameObject ButtonModity;

	public GameObject TextPreActionName;

	public GameObject ToggleShowLeft;

	public GameObject ButtonClearAction;

	public GameObject ButtonRandomOP;

	public GameObject DropdownType;

	public GameObject InputFieldFind;

	public GameObject ButtonFind;

	public GameObject goPreText;

	public bool isChat = true;

	public int index = 3;

	public int state;

	public bool isUpdate;

	public bool isUpdateLog;

	public bool isComplete;

	public string textNewMsg;

	public string textOldMsg;

	public static HttpClient client = new HttpClient
	{
		Timeout = TimeSpan.FromSeconds(300.0)
	};

	public static HttpClient ActionClient = new HttpClient
	{
		Timeout = TimeSpan.FromSeconds(300.0)
	};

	public Sprite SpeicalPngSprite;

	public string unitSet = "";

	public static string baseActionStr = "行为请求：";

	public static AIActionMessage aIActionMessage = null;

	public static TimerCoroutine actionThread = null;

	public string time = "";

	public bool isSpeek;

	public static TimerCoroutine coroutine = null;

	public int preCount = 10;

	public int curPage = -1;

	public int selPreTextIndex = -1;

	public int selPreIndex = -1;

	public void InitAI(WorldUnitBase unit, int type = 0)
	{
		Console.WriteLine("InitAI");
		try
		{
			((UnityEventBase)ButtonSend.GetComponent<Button>().onClick).RemoveAllListeners();
			((UnityEvent)ButtonSend.GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
			{
				try
				{
					SendMessage();
				}
				catch (Exception ex2)
				{
					Console.WriteLine(ex2.ToString());
					Console.WriteLine("ButtonSend出错");
				}
			}));
			if (isChat)
			{
				if (ModMain.Messages.ContainsKey(unit.data.unitData.unitID))
				{
					Console.WriteLine("message数量：" + ModMain.Messages[unit.data.unitData.unitID].Count);
				}
			}
			else if (ModMain.ActionMessages.ContainsKey(unit.data.unitData.unitID))
			{
				Console.WriteLine("message数量：" + ModMain.ActionMessages[unit.data.unitData.unitID].Count);
			}
		}
		catch (Exception ex)
		{
			Console.WriteLine(ex.ToString());
		}
	}

	public void SendMessage(int type = 0)
	{
		//IL_0465: Unknown result type (might be due to invalid IL or missing references)
		//IL_046b: Unknown result type (might be due to invalid IL or missing references)
		//IL_04e6: Unknown result type (might be due to invalid IL or missing references)
		//IL_04ec: Unknown result type (might be due to invalid IL or missing references)
		//IL_0567: Unknown result type (might be due to invalid IL or missing references)
		//IL_056d: Unknown result type (might be due to invalid IL or missing references)
		//IL_0606: Unknown result type (might be due to invalid IL or missing references)
		//IL_0612: Unknown result type (might be due to invalid IL or missing references)
		//IL_0622: Unknown result type (might be due to invalid IL or missing references)
		//IL_063c: Unknown result type (might be due to invalid IL or missing references)
		//IL_0648: Unknown result type (might be due to invalid IL or missing references)
		//IL_0658: Unknown result type (might be due to invalid IL or missing references)
		//IL_0664: Unknown result type (might be due to invalid IL or missing references)
		//IL_0674: Unknown result type (might be due to invalid IL or missing references)
		try
		{
			if (state == 0)
			{
				string user = "user";
				switch (type)
				{
				case 1:
				{
					List<string> list = new List<string> { "(向玩家打个招呼）", "(向玩家发起闲聊）", "(向玩家进行问候）" };
					List<string> list2 = new List<string> { "(向玩家打个招呼）", "(向玩家发起闲聊）", "(玩玩家谈谈人生）", "(向玩家谈谈最近动态）", "(向玩家进行问候）", "(向玩家表达思念）", "（向玩家表达情感）" };
					List<string> list3 = new List<string> { "(向玩家打个招呼）", "(向玩家发起闲聊）", "(玩玩家表达恶意）", "(向玩家发泄不满）", "(向玩家发起挑衅）", "(向玩家发出不屑）" };
					int intim = unit.data.unitData.relationData.GetIntim(g.world.playerUnit.data.unitData.unitID);
					Random random = new Random();
					if (intim > 60)
					{
						int num = random.Next(0, list2.Count);
						textNewMsg = list2[num];
					}
					else if (intim < -60)
					{
						int num2 = random.Next(0, list3.Count);
						textNewMsg = list3[num2];
					}
					else
					{
						int num3 = random.Next(0, list.Count);
						textNewMsg = list[num3];
					}
					user = "system";
					Console.WriteLine("触发NPC主动答复：" + textNewMsg);
					break;
				}
				case 2:
					user = "system";
					textNewMsg = "";
					break;
				case 0:
					textNewMsg = InputText.GetComponent<InputField>().text;
					if (string.IsNullOrEmpty(textNewMsg.Trim()))
					{
						g.ui.OpenUI<UITextInfo>(UIType.TextInfo).InitData("提示", "输入内容为空。", "", (Action)null);
						return;
					}
					break;
				}
				curPage = -1;
				UpdateChatMessageList();
				textNewMsg = textNewMsg.TrimEnd('\r', '\n');
				textOldMsg = "";
				string text = $"[{CommandTool.ConvertToYearsMonths(g.world.run.roundMonth + 1)}{g.world.run.roundDay + 1}日]";
				try
				{
					TimerCoroutine obj = actionThread;
					if (obj != null && !obj.isStop)
					{
						g.timer.Stop(actionThread);
						actionThread = null;
					}
				}
				catch (Exception ex)
				{
					Console.WriteLine(ex.ToString());
				}
				InputText.GetComponent<InputField>().text = "";
				((Selectable)ButtonSend.GetComponent<Button>()).interactable = false;
				string input = textNewMsg;
				string content = "";
				string content2 = CommandTool.GetContent(input, out content);
				if (!time.Equals(content2))
				{
					time = content2;
					((Component)Object.Instantiate<GameObject>(g.res.Load<GameObject>("UI/msgItemTime"), OutPutContent.transform).transform.Find("Text")).GetComponent<Text>().text = content2;
				}
				GameObject val = Object.Instantiate<GameObject>(g.res.Load<GameObject>("UI/msgItem"), OutPutContent.transform);
				if (user.Equals("system"))
				{
					if ((Object)(object)val != (Object)null)
					{
						((Component)val.transform.Find("Left")).gameObject.SetActive(false);
					}
				}
				else if (user.Equals("user") && (Object)(object)val != (Object)null)
				{
					index++;
					int temp = index;
					((Component)val.transform.Find("Left/Text")).GetComponent<Text>().text = content;
					((Component)val.transform.Find("Right")).gameObject.SetActive(false);
					((Component)val.transform.Find("Left/ButtonDel")).gameObject.AddComponent<UISkyTipEffect>().InitData("删除该条聊天记录", default(Vector3));
					((UnityEventBase)((Component)val.transform.Find("Left/ButtonDel")).GetComponent<Button>().onClick).RemoveAllListeners();
					((UnityEvent)((Component)val.transform.Find("Left/ButtonDel")).GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
					{
						g.ui.OpenUI<UICheckPopup>(UIType.CheckPopup).InitData("提示", "是否确认删除该条聊天记录？", 2, Action.op_Implicit((Action)delegate
						{
							DelChatMessageItem(temp);
						}), (Action)null);
					}));
					((Component)val.transform.Find("Left/ButtonCopy")).gameObject.AddComponent<UISkyTipEffect>().InitData("复制该条聊天记录", default(Vector3));
					((UnityEventBase)((Component)val.transform.Find("Left/ButtonCopy")).GetComponent<Button>().onClick).RemoveAllListeners();
					((UnityEvent)((Component)val.transform.Find("Left/ButtonCopy")).GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
					{
						CopyChatMessage(temp);
					}));
					((Component)val.transform.Find("Left/ButtonModity")).gameObject.AddComponent<UISkyTipEffect>().InitData("修改该条聊天记录", default(Vector3));
					((UnityEventBase)((Component)val.transform.Find("Left/ButtonModity")).GetComponent<Button>().onClick).RemoveAllListeners();
					((UnityEvent)((Component)val.transform.Find("Left/ButtonModity")).GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
					{
						ModityChatMessage(temp);
					}));
					RectTransform component = ((Component)val.transform.Find("Left/Text")).GetComponent<RectTransform>();
					LayoutRebuilder.ForceRebuildLayoutImmediate(component);
					Canvas.ForceUpdateCanvases();
					RectTransform component2 = ((Component)val.transform.Find("Left/Image")).GetComponent<RectTransform>();
					component2.sizeDelta = new Vector2(component2.sizeDelta.x, component.sizeDelta.y + 50f);
					RectTransform component3 = ((Component)val.transform).GetComponent<RectTransform>();
					if (component2.sizeDelta.y > component3.sizeDelta.y)
					{
						component3.sizeDelta = new Vector2(component3.sizeDelta.x, component2.sizeDelta.y + 10f);
					}
				}
				ChatItem = Object.Instantiate<GameObject>(g.res.Load<GameObject>("UI/msgItem"), OutPutContent.transform);
				((Component)ChatItem.transform.Find("Left")).gameObject.SetActive(false);
				if (ModMain.modelName.Contains("R1"))
				{
					((Component)ChatItem.transform.Find("Right/Text")).GetComponent<Text>().text = "对方正在思考。。。";
				}
				else
				{
					((Component)ChatItem.transform.Find("Right/Text")).GetComponent<Text>().text = "对方正在传音。。。";
				}
				if (!isChat)
				{
					((Component)ChatItem.transform.Find("Right/Text")).GetComponent<Text>().text = "对方正在考虑。。。";
				}
				if (type != 0)
				{
					((Component)ChatItem.transform.Find("Right/Text")).GetComponent<Text>().text = "对方主动向你传音。。。";
				}
				state = 1;
				ScrollRect component4 = ((Component)((Component)this).transform.Find("Root/PanelMain/OutPut/Panel/Scroll View")).GetComponent<ScrollRect>();
				LayoutRebuilder.ForceRebuildLayoutImmediate(component4.content);
				Canvas.ForceUpdateCanvases();
				component4.verticalNormalizedPosition = 0f;
				CommandTool.UpdataNPCInfo(unit);
				bool isAction = false;
				string text2 = "";
				text2 = ((!ModMain.Messages.ContainsKey(unit.data.unitData.unitID)) ? (text + "(我是" + g.world.playerUnit.data.unitData.propertyData.GetName() + ")" + textNewMsg) : ((ModMain.Messages[unit.data.unitData.unitID].Count >= 5) ? (text + textNewMsg) : (text + "(我是" + g.world.playerUnit.data.unitData.propertyData.GetName() + ")" + textNewMsg)));
				if (isChat)
				{
					if (!StringExtensions.IsEmpty(text2) && user.Equals("user"))
					{
						CommandTool.AddMessage(unit, "user", text2, 1);
					}
				}
				else if (!StringExtensions.IsEmpty(text2) && user.Equals("user"))
				{
					CommandTool.AddMessage(unit, "user", text2, 2);
				}
				bool localIsChat = isChat;
				string localUserMsg = text2;
				int localSelPreTextIndex = selPreTextIndex;
				aIActionMessage = null;
				isComplete = false;
				g.timer.Time(Action.op_Implicit((Action)async delegate
				{
					_ = 1;
					try
					{
						string text3 = "";
						if (!localIsChat && localSelPreTextIndex >= 0 && localSelPreTextIndex < ModMain.preText.items.Count)
						{
							text3 = $"<actionNumber>{ModMain.preText.items[localSelPreTextIndex].actionType}</actionNumber>";
						}
						if (!localIsChat)
						{
							await SendToDeepSeek(user, baseActionStr + localUserMsg + text3);
						}
						else
						{
							await SendToDeepSeek(user, localUserMsg);
						}
						Console.WriteLine("Http任务已全部返回");
						ThreadSafeQueue.Enqueue(delegate
						{
							if (!localIsChat)
							{
								isAction = true;
							}
						});
					}
					catch (Exception ex3)
					{
						Console.WriteLine("Thread 出错：" + ex3.Message);
						ThreadSafeQueue.Enqueue(delegate
						{
							isAction = false;
							if (actionThread != null)
							{
								g.timer.Stop(actionThread);
								actionThread = null;
							}
						});
					}
				}), 0f, false);
				if (localIsChat || actionThread != null)
				{
					return;
				}
				Console.WriteLine("开始启动行动监控。。。");
				actionThread = g.timer.Time(Action.op_Implicit((Action)delegate
				{
					if (isAction && aIActionMessage != null)
					{
						ThreadSafeQueue.Enqueue(delegate
						{
							try
							{
								if (localSelPreTextIndex >= 0 && localSelPreTextIndex < ModMain.preText.items.Count)
								{
									aIActionMessage.actionNumber = ModMain.preText.items[localSelPreTextIndex].actionType;
								}
								textOldMsg = aIActionMessage.yourResponse;
								isUpdate = true;
								string actionName = new ActionList().GetActionName(aIActionMessage.actionNumber);
								UnitActionAI unitActionAI = new UnitActionAI(aIActionMessage, unit);
								if (aIActionMessage.isApproved.Contains("是"))
								{
									unitActionAI.SelectAction();
									g.world.unitLog.SaveLogData();
									UINPCInfo uI = g.ui.GetUI<UINPCInfo>(UIType.NPCInfo);
									if ((Object)(object)uI != (Object)null)
									{
										uI.UpdateUI();
									}
								}
								else
								{
									UITipItem.AddTip(unit.data.unitData.propertyData.GetName() + "不同意你的行为请求--" + actionName, 2f);
									Console.WriteLine("对方不同意你的行为请求--" + actionName);
								}
							}
							catch (Exception ex3)
							{
								Console.WriteLine("处理行动结果出错: " + ex3.Message);
							}
							if (actionThread != null)
							{
								g.timer.Stop(actionThread);
								actionThread = null;
								Console.WriteLine("行动监控关闭。。。");
							}
							isAction = false;
						});
					}
				}), 0.5f, true);
			}
			else
			{
				UITipItem.AddTip("正在传音中，请等待对方回复！", 3f);
				UITipItem.AddTip("若90秒后未回复自动超时返回！", 3f);
			}
		}
		catch (Exception ex2)
		{
			Console.WriteLine(ex2.Message);
		}
	}

	public void ClearChatMessageList()
	{
		try
		{
			if (state == 0)
			{
				ModMain.ActionMessages[unit.data.unitData.unitID].Clear();
				ModMain.ActionMessages[unit.data.unitData.unitID] = new List<Message>();
				ModMain.Messages[unit.data.unitData.unitID].Clear();
				ModMain.Messages[unit.data.unitData.unitID] = new List<Message>();
				UpdateChatMessageList();
				index = 4;
			}
			else
			{
				UITipItem.AddTip("正在传音中，通道不稳定，请稍后再试。。。", 3f);
			}
			string modPathRoot = g.mod.GetModPathRoot("MOD_SSCYAI");
			string text = "";
			if (StringExtensions.IsEmpty(modPathRoot))
			{
				return;
			}
			text = modPathRoot + "\\ModAssets";
			try
			{
				string name = unit.data.unitData.propertyData.GetName();
				foreach (string item in Directory.EnumerateFiles(text, "*", SearchOption.AllDirectories))
				{
					if (item.Contains(name))
					{
						File.Delete(item);
					}
					Console.WriteLine(item);
				}
			}
			catch (Exception ex)
			{
				Console.WriteLine("错误: " + ex.Message);
			}
		}
		catch (Exception ex2)
		{
			Console.WriteLine(ex2.ToString());
		}
	}

	public void ClearActionMessageList()
	{
		try
		{
			if (state == 0)
			{
				ModMain.ActionMessages[unit.data.unitData.unitID].Clear();
				ModMain.ActionMessages[unit.data.unitData.unitID] = new List<Message>();
				ModMain.Messages[unit.data.unitData.unitID].Clear();
				ModMain.Messages[unit.data.unitData.unitID] = new List<Message>();
				UpdateChatMessageList();
				index = 4;
			}
			else
			{
				UITipItem.AddTip("正在传音中，通道不稳定，请稍后再试。。。", 3f);
			}
			string modPathRoot = g.mod.GetModPathRoot("MOD_SSCYAI");
			string text = "";
			if (StringExtensions.IsEmpty(modPathRoot))
			{
				return;
			}
			text = modPathRoot + "\\ModAssets";
			try
			{
				string name = unit.data.unitData.propertyData.GetName();
				foreach (string item in Directory.EnumerateFiles(text, "*", SearchOption.AllDirectories))
				{
					if (item.Contains(name))
					{
						File.Delete(item);
					}
					Console.WriteLine(item);
				}
			}
			catch (Exception ex)
			{
				Console.WriteLine("错误: " + ex.Message);
			}
		}
		catch (Exception ex2)
		{
			Console.WriteLine(ex2.ToString());
		}
	}

	public string CopyChatMessageList()
	{
		string text = "";
		try
		{
			if (ModMain.Messages.ContainsKey(unit.data.unitData.unitID))
			{
				if (ModMain.Messages[unit.data.unitData.unitID].Count < 2)
				{
					return text;
				}
				for (int i = 4; i < ModMain.Messages[unit.data.unitData.unitID].Count; i++)
				{
					if (!ModMain.Messages[unit.data.unitData.unitID][i].role.Equals("system"))
					{
						if (ModMain.Messages[unit.data.unitData.unitID][i].role.Equals("user"))
						{
							string text2 = $"[{g.world.playerUnit.data.unitData.propertyData.GetName()}]{ModMain.Messages[unit.data.unitData.unitID][i].content}\n";
							text += text2;
						}
						else if (ModMain.Messages[unit.data.unitData.unitID][i].role.Equals("assistant"))
						{
							string text3 = $"[{unit.data.unitData.propertyData.GetName()}]{ModMain.Messages[unit.data.unitData.unitID][i].content}\n";
							text += text3;
						}
					}
				}
				try
				{
					Clipboard.SetDataObject(text, copy: true);
					UITipItem.AddTip("所有聊天记录已复制", 2f);
				}
				catch (Exception ex)
				{
					Console.WriteLine("复制失败: " + ex.Message);
				}
			}
		}
		catch (Exception ex2)
		{
			Console.WriteLine(ex2.ToString());
		}
		return text;
	}

	public void CopyActionMessageList()
	{
		try
		{
			if (!ModMain.ActionMessages.ContainsKey(unit.data.unitData.unitID) || ModMain.ActionMessages[unit.data.unitData.unitID].Count < 2)
			{
				return;
			}
			string text = "";
			for (int i = 4; i < ModMain.ActionMessages[unit.data.unitData.unitID].Count; i += 2)
			{
				if (ModMain.ActionMessages[unit.data.unitData.unitID][i].role.Equals("system"))
				{
					i--;
				}
				else if (ModMain.ActionMessages[unit.data.unitData.unitID][i].role.Equals("user"))
				{
					if (i + 1 < ModMain.ActionMessages[unit.data.unitData.unitID].Count)
					{
						string text2 = $"[{g.world.playerUnit.data.unitData.propertyData.GetName()}]{ModMain.ActionMessages[unit.data.unitData.unitID][i].content}\n\n[{unit.data.unitData.propertyData.GetName()}]{ModMain.ActionMessages[unit.data.unitData.unitID][i + 1].content}\n\n";
						text += text2;
					}
				}
				else
				{
					string text3 = $"[{unit.data.unitData.propertyData.GetName()}]{ModMain.ActionMessages[unit.data.unitData.unitID][i].content}\n";
					i--;
					text += text3;
				}
			}
			try
			{
				Clipboard.SetDataObject(text, copy: true);
				UITipItem.AddTip("所有聊天记录已复制", 2f);
			}
			catch (Exception ex)
			{
				Console.WriteLine("复制失败: " + ex.Message);
			}
		}
		catch (Exception ex2)
		{
			Console.WriteLine(ex2.ToString());
		}
	}

	public void UpdateChatMessageList()
	{
		//IL_03bb: Unknown result type (might be due to invalid IL or missing references)
		//IL_03c1: Unknown result type (might be due to invalid IL or missing references)
		//IL_0448: Unknown result type (might be due to invalid IL or missing references)
		//IL_044e: Unknown result type (might be due to invalid IL or missing references)
		//IL_04d5: Unknown result type (might be due to invalid IL or missing references)
		//IL_04db: Unknown result type (might be due to invalid IL or missing references)
		//IL_057f: Unknown result type (might be due to invalid IL or missing references)
		//IL_058b: Unknown result type (might be due to invalid IL or missing references)
		//IL_059b: Unknown result type (might be due to invalid IL or missing references)
		//IL_05b6: Unknown result type (might be due to invalid IL or missing references)
		//IL_05c2: Unknown result type (might be due to invalid IL or missing references)
		//IL_05d2: Unknown result type (might be due to invalid IL or missing references)
		//IL_07bf: Unknown result type (might be due to invalid IL or missing references)
		//IL_07c5: Unknown result type (might be due to invalid IL or missing references)
		//IL_0851: Unknown result type (might be due to invalid IL or missing references)
		//IL_0857: Unknown result type (might be due to invalid IL or missing references)
		//IL_08e3: Unknown result type (might be due to invalid IL or missing references)
		//IL_08e9: Unknown result type (might be due to invalid IL or missing references)
		//IL_0975: Unknown result type (might be due to invalid IL or missing references)
		//IL_097b: Unknown result type (might be due to invalid IL or missing references)
		//IL_0a4d: Unknown result type (might be due to invalid IL or missing references)
		//IL_0a53: Unknown result type (might be due to invalid IL or missing references)
		//IL_0af7: Unknown result type (might be due to invalid IL or missing references)
		//IL_0b03: Unknown result type (might be due to invalid IL or missing references)
		//IL_0b13: Unknown result type (might be due to invalid IL or missing references)
		//IL_0b2e: Unknown result type (might be due to invalid IL or missing references)
		//IL_0b3a: Unknown result type (might be due to invalid IL or missing references)
		//IL_0b4a: Unknown result type (might be due to invalid IL or missing references)
		try
		{
			for (int num = OutPutContent.transform.GetChildCount() - 1; num >= 0; num--)
			{
				Object.Destroy((Object)(object)((Component)OutPutContent.transform.GetChild(num)).gameObject);
			}
			time = "";
			index = 3;
			if (!ModMain.Messages.ContainsKey(unit.data.unitData.unitID))
			{
				return;
			}
			if (ModMain.Messages[unit.data.unitData.unitID].Count > 3)
			{
				int num2 = ModMain.Messages[unit.data.unitData.unitID].Count / preCount + ((ModMain.Messages[unit.data.unitData.unitID].Count % preCount != 0) ? 1 : 0);
				if (curPage < 1)
				{
					curPage = num2;
				}
				if (curPage > num2)
				{
					curPage = 1;
				}
				int num3 = (curPage - 1) * preCount;
				if (num3 >= ModMain.Messages[unit.data.unitData.unitID].Count)
				{
					curPage = 1;
					num3 = 0;
				}
				int num4 = ((curPage * preCount < ModMain.Messages[unit.data.unitData.unitID].Count) ? (curPage * preCount) : ModMain.Messages[unit.data.unitData.unitID].Count);
				int num5 = 0;
				for (int i = num3; i < num4; i++)
				{
					int temp = i;
					index = i;
					if (ModMain.Messages[unit.data.unitData.unitID][i].role.Equals("system"))
					{
						continue;
					}
					num5++;
					if (ModMain.Messages[unit.data.unitData.unitID][i].role.Equals("user"))
					{
						string content = ModMain.Messages[unit.data.unitData.unitID][i].content;
						string content2 = "";
						string content3 = CommandTool.GetContent(content, out content2);
						if (!time.Equals(content3))
						{
							time = content3;
							((Component)Object.Instantiate<GameObject>(g.res.Load<GameObject>("UI/msgItemTime"), OutPutContent.transform).transform.Find("Text")).GetComponent<Text>().text = content3;
						}
						ChatItem = Object.Instantiate<GameObject>(g.res.Load<GameObject>("UI/msgItem"), OutPutContent.transform);
						content2 = content2.Replace("(我是" + g.world.playerUnit.data.unitData.propertyData.GetName() + ")", "");
						content2 = CommandTool.RegexForOutPut(content2);
						((Component)ChatItem.transform.Find("Left/Text")).GetComponent<Text>().text = content2;
						((Component)ChatItem.transform.Find("Right")).gameObject.SetActive(false);
						((Component)ChatItem.transform.Find("Left/ButtonDel")).gameObject.AddComponent<UISkyTipEffect>().InitData("删除该条聊天记录", default(Vector3));
						((UnityEventBase)((Component)ChatItem.transform.Find("Left/ButtonDel")).GetComponent<Button>().onClick).RemoveAllListeners();
						((UnityEvent)((Component)ChatItem.transform.Find("Left/ButtonDel")).GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
						{
							g.ui.OpenUI<UICheckPopup>(UIType.CheckPopup).InitData("提示", "是否确认删除该条聊天记录？", 2, Action.op_Implicit((Action)delegate
							{
								DelChatMessageItem(temp);
							}), (Action)null);
						}));
						((Component)ChatItem.transform.Find("Left/ButtonCopy")).gameObject.AddComponent<UISkyTipEffect>().InitData("复制该条聊天记录", default(Vector3));
						((UnityEventBase)((Component)ChatItem.transform.Find("Left/ButtonCopy")).GetComponent<Button>().onClick).RemoveAllListeners();
						((UnityEvent)((Component)ChatItem.transform.Find("Left/ButtonCopy")).GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
						{
							CopyChatMessage(temp);
						}));
						((Component)ChatItem.transform.Find("Left/ButtonModity")).gameObject.AddComponent<UISkyTipEffect>().InitData("修改该条聊天记录", default(Vector3));
						((UnityEventBase)((Component)ChatItem.transform.Find("Left/ButtonModity")).GetComponent<Button>().onClick).RemoveAllListeners();
						((UnityEvent)((Component)ChatItem.transform.Find("Left/ButtonModity")).GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
						{
							ModityChatMessage(temp);
						}));
						RectTransform component = ((Component)ChatItem.transform.Find("Left/Text")).GetComponent<RectTransform>();
						LayoutRebuilder.ForceRebuildLayoutImmediate(component);
						RectTransform component2 = ((Component)ChatItem.transform.Find("Left/Image")).GetComponent<RectTransform>();
						component2.sizeDelta = new Vector2(component2.sizeDelta.x, component.sizeDelta.y + 30f);
						RectTransform component3 = ((Component)ChatItem.transform).GetComponent<RectTransform>();
						component3.sizeDelta = new Vector2(component3.sizeDelta.x, component2.sizeDelta.y + 80f);
					}
					else
					{
						if (!ModMain.Messages[unit.data.unitData.unitID][i].role.Equals("assistant"))
						{
							continue;
						}
						ChatItem = Object.Instantiate<GameObject>(g.res.Load<GameObject>("UI/msgItem"), OutPutContent.transform);
						int num6 = ModMain.Messages[unit.data.unitData.unitID][i].content.IndexOf("]");
						if (ModMain.Messages[unit.data.unitData.unitID][i].content.StartsWith("["))
						{
							ModMain.Messages[unit.data.unitData.unitID][i].content = ModMain.Messages[unit.data.unitData.unitID][i].content.Substring(num6 + 1);
						}
						string input = ModMain.Messages[unit.data.unitData.unitID][i].content.Trim();
						input = CommandTool.RegexForOutPut(input);
						((Component)ChatItem.transform.Find("Right/Text")).GetComponent<Text>().text = input;
						((Component)ChatItem.transform.Find("Left")).gameObject.SetActive(false);
						((Component)ChatItem.transform.Find("Right/ButtonDel")).gameObject.AddComponent<UISkyTipEffect>().InitData("删除该条聊天记录", default(Vector3));
						((UnityEventBase)((Component)ChatItem.transform.Find("Right/ButtonDel")).GetComponent<Button>().onClick).RemoveAllListeners();
						((UnityEvent)((Component)ChatItem.transform.Find("Right/ButtonDel")).GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
						{
							g.ui.OpenUI<UICheckPopup>(UIType.CheckPopup).InitData("提示", "是否确认删除该条聊天记录？", 2, Action.op_Implicit((Action)delegate
							{
								DelChatMessageItem(temp);
							}), (Action)null);
						}));
						((Component)ChatItem.transform.Find("Right/ButtonCopy")).gameObject.AddComponent<UISkyTipEffect>().InitData("复制该条聊天记录", default(Vector3));
						((UnityEventBase)((Component)ChatItem.transform.Find("Right/ButtonCopy")).GetComponent<Button>().onClick).RemoveAllListeners();
						((UnityEvent)((Component)ChatItem.transform.Find("Right/ButtonCopy")).GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
						{
							CopyChatMessage(temp);
						}));
						((Component)ChatItem.transform.Find("Right/ButtonModity")).gameObject.AddComponent<UISkyTipEffect>().InitData("修改该条聊天记录", default(Vector3));
						((UnityEventBase)((Component)ChatItem.transform.Find("Right/ButtonModity")).GetComponent<Button>().onClick).RemoveAllListeners();
						((UnityEvent)((Component)ChatItem.transform.Find("Right/ButtonModity")).GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
						{
							ModityChatMessage(temp);
						}));
						((Component)ChatItem.transform.Find("Right/ButtonShow")).gameObject.AddComponent<UISkyTipEffect>().InitData("显示完整内容", default(Vector3));
						((UnityEventBase)((Component)ChatItem.transform.Find("Right/ButtonShow")).GetComponent<Button>().onClick).RemoveAllListeners();
						((UnityEvent)((Component)ChatItem.transform.Find("Right/ButtonShow")).GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
						{
							if (isChat)
							{
								if (temp >= 0 && temp < ModMain.Messages[unit.data.unitData.unitID].Count)
								{
									string content4 = ModMain.Messages[unit.data.unitData.unitID][temp].content;
									g.ui.OpenUI<UITextInfoLong>(UIType.TextInfoLong).InitData("显示完整内容", content4, "", (Action)null, false);
								}
							}
							else if (temp >= 0 && temp < ModMain.ActionMessages[unit.data.unitData.unitID].Count)
							{
								string content5 = ModMain.ActionMessages[unit.data.unitData.unitID][temp].content;
								g.ui.OpenUI<UITextInfoLong>(UIType.TextInfoLong).InitData("显示完整内容", content5, "", (Action)null, false);
							}
						}));
						((Component)ChatItem.transform.Find("Right/ButtonSpeek")).gameObject.SetActive(true);
						string textStr = ((Component)ChatItem.transform.Find("Right/Text")).GetComponent<Text>().text;
						((Component)ChatItem.transform.Find("Right/ButtonSpeek")).gameObject.AddComponent<UISkyTipEffect>().InitData("语音播放", default(Vector3));
						((UnityEventBase)((Component)ChatItem.transform.Find("Right/ButtonSpeek")).GetComponent<Button>().onClick).RemoveAllListeners();
						((UnityEvent)((Component)ChatItem.transform.Find("Right/ButtonSpeek")).GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
						{
							if (isSpeek)
							{
								UITipItem.AddTip("正在语音合成中，请勿重复点击。。。", 0f);
							}
							else
							{
								try
								{
									g.timer.Time(Action.op_Implicit((Action)async delegate
									{
										isSpeek = true;
										await CommandTool.TextToSpeek(unit, textStr, unit.data.unitData.propertyData.GetName() + "-" + temp);
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
												string text = ((DataObjectData)((DataBase<Object>)(object)g.data.dataObj).data).GetString("filepath");
												if (File.Exists(text))
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
														if (ModMain.mciSendString("open \"" + text + "\" type mpegvideo alias my_mp3", null, 0, IntPtr.Zero) != 0)
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
													catch (Exception ex3)
													{
														Console.WriteLine(ex3.Message);
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
						}));
						RectTransform component4 = ((Component)ChatItem.transform.Find("Right/Text")).GetComponent<RectTransform>();
						LayoutRebuilder.ForceRebuildLayoutImmediate(component4);
						RectTransform component5 = ((Component)ChatItem.transform.Find("Right/Image")).GetComponent<RectTransform>();
						component5.sizeDelta = new Vector2(component5.sizeDelta.x, component4.sizeDelta.y + 30f);
						RectTransform component6 = ((Component)ChatItem.transform).GetComponent<RectTransform>();
						component6.sizeDelta = new Vector2(component6.sizeDelta.x, component5.sizeDelta.y + 80f);
					}
				}
				((Component)((Component)this).transform.Find("Root/PanelMain/OutPut/Text")).GetComponent<Text>().text = $"消息总数:{ModMain.Messages[unit.data.unitData.unitID].Count}  当前页显示消息数：{num5}  第 {curPage} 页 / 总 {num2} 页";
				ScrollRect component7 = ((Component)((Component)this).transform.Find("Root/PanelMain/OutPut/Panel/Scroll View")).GetComponent<ScrollRect>();
				LayoutRebuilder.ForceRebuildLayoutImmediate(component7.content);
				Canvas.ForceUpdateCanvases();
				component7.verticalNormalizedPosition = 0f;
				state = 0;
			}
			else
			{
				Console.WriteLine("聊天记录为空");
			}
		}
		catch (Exception ex)
		{
			Console.WriteLine(ex.ToString());
		}
	}

	public void DelChatMessageItem(int temp)
	{
		try
		{
			if (state == 0)
			{
				int num = temp;
				if (num <= 0)
				{
					return;
				}
				if (ModMain.Messages[unit.data.unitData.unitID].Count > num)
				{
					ModMain.Messages[unit.data.unitData.unitID].RemoveAt(num);
				}
				if (ModMain.ActionMessages[unit.data.unitData.unitID].Count > num)
				{
					ModMain.ActionMessages[unit.data.unitData.unitID].RemoveAt(num);
				}
				UpdateChatMessageList();
				string modPathRoot = g.mod.GetModPathRoot("MOD_SSCYAI");
				string text = "";
				if (StringExtensions.IsEmpty(modPathRoot))
				{
					return;
				}
				text = modPathRoot + "\\ModAssets";
				try
				{
					string value = unit.data.unitData.propertyData.GetName() + "-" + temp;
					foreach (string item in Directory.EnumerateFiles(text, "*", SearchOption.AllDirectories))
					{
						if (item.Contains(value))
						{
							File.Delete(item);
						}
					}
					return;
				}
				catch (Exception ex)
				{
					Console.WriteLine("错误: " + ex.Message);
					return;
				}
			}
			UITipItem.AddTip("正在传音中，通道不稳定，请稍后再试。。。", 3f);
		}
		catch (Exception ex2)
		{
			Console.WriteLine(ex2.ToString());
		}
	}

	public void DelActionMessageItem(int temp)
	{
		try
		{
			if (state == 0)
			{
				int num = temp * 2 + 4;
				if (num <= 0)
				{
					return;
				}
				if (!ModMain.ActionMessages[unit.data.unitData.unitID][num].role.Equals("user"))
				{
					num--;
				}
				Console.WriteLine("id=" + num);
				Console.WriteLine("消息总条数:" + ModMain.ActionMessages[unit.data.unitData.unitID].Count);
				if (ModMain.ActionMessages[unit.data.unitData.unitID].Count > num)
				{
					if (ModMain.Messages[unit.data.unitData.unitID].Count > num)
					{
						ModMain.Messages[unit.data.unitData.unitID].RemoveAt(num);
					}
					ModMain.ActionMessages[unit.data.unitData.unitID].RemoveAt(num);
					if (ModMain.ActionMessages[unit.data.unitData.unitID].Count > num)
					{
						if (ModMain.Messages[unit.data.unitData.unitID].Count > num)
						{
							ModMain.Messages[unit.data.unitData.unitID].RemoveAt(num);
						}
						ModMain.ActionMessages[unit.data.unitData.unitID].RemoveAt(num);
					}
					try
					{
						if (ModMain.ActionMessages[unit.data.unitData.unitID].Count > num && ModMain.ActionMessages[unit.data.unitData.unitID][num].role.Equals("assistant"))
						{
							ModMain.ActionMessages[unit.data.unitData.unitID].RemoveAt(num);
						}
						if (ModMain.Messages[unit.data.unitData.unitID].Count > num && ModMain.Messages[unit.data.unitData.unitID][num].role.Equals("assistant"))
						{
							ModMain.Messages[unit.data.unitData.unitID].RemoveAt(num);
						}
					}
					catch (Exception ex)
					{
						Console.WriteLine(ex.ToString());
						Console.WriteLine("删除报错");
					}
					index--;
					UpdateChatMessageList();
					LayoutRebuilder.ForceRebuildLayoutImmediate(((Component)((Component)this).transform.Find("Root/PanelMain/OutPut/Panel/Scroll View")).GetComponent<ScrollRect>().content);
					Canvas.ForceUpdateCanvases();
				}
				string modPathRoot = g.mod.GetModPathRoot("MOD_SSCYAI");
				string text = "";
				if (StringExtensions.IsEmpty(modPathRoot))
				{
					return;
				}
				text = modPathRoot + "\\ModAssets";
				try
				{
					string value = unit.data.unitData.propertyData.GetName() + "-" + temp;
					foreach (string item in Directory.EnumerateFiles(text, "*", SearchOption.AllDirectories))
					{
						if (item.Contains(value))
						{
							File.Delete(item);
						}
					}
					return;
				}
				catch (Exception ex2)
				{
					Console.WriteLine("错误: " + ex2.Message);
					return;
				}
			}
			UITipItem.AddTip("正在传音中，通道不稳定，请稍后再试。。。", 3f);
		}
		catch (Exception ex3)
		{
			Console.WriteLine(ex3.ToString());
		}
	}

	public string ModityChatMessage(int temp, bool isTips = true)
	{
		//IL_0066: Unknown result type (might be due to invalid IL or missing references)
		//IL_0070: Expected O, but got Unknown
		if (temp > 0 && ModMain.Messages[unit.data.unitData.unitID].Count > temp)
		{
			try
			{
				ClassInjector.RegisterTypeInIl2Cpp<UIInput>();
				UIInput uIInput = ((Component)g.ui.OpenUI(new UITypeBase("UIInput", (UILayer)0))).gameObject.AddComponent<UIInput>();
				if (isChat)
				{
					uIInput.InitData(ModMain.Messages[unit.data.unitData.unitID][temp].content);
				}
				else
				{
					uIInput.InitData(ModMain.ActionMessages[unit.data.unitData.unitID][temp].content);
				}
				((UnityEvent)uIInput.ButtonSave.GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
				{
					try
					{
						if (StringExtensions.IsEmpty(UIInput.text))
						{
							UITipItem.AddTip("记录修改失败!", 3f);
						}
						else
						{
							if (isChat)
							{
								ModMain.Messages[unit.data.unitData.unitID][temp].content = UIInput.text;
								if (ModMain.ActionMessages[unit.data.unitData.unitID].Count > temp)
								{
									if (ModMain.ActionMessages[unit.data.unitData.unitID][temp].role.Equals("assistant"))
									{
										AIActionMessage xmlText = CommandTool.GetXmlText(ModMain.ActionMessages[unit.data.unitData.unitID][temp].content);
										if (xmlText != null)
										{
											xmlText.yourResponse = UIInput.text;
											string xmlText2 = xmlText.GetXmlText();
											ModMain.ActionMessages[unit.data.unitData.unitID][temp].content = xmlText2;
										}
										else
										{
											ModMain.ActionMessages[unit.data.unitData.unitID][temp].content = UIInput.text;
										}
									}
									else
									{
										ModMain.ActionMessages[unit.data.unitData.unitID][temp].content = UIInput.text;
									}
								}
								UpdateChatMessageList();
							}
							else
							{
								if (ModMain.ActionMessages[unit.data.unitData.unitID].Count > temp)
								{
									if (ModMain.ActionMessages[unit.data.unitData.unitID][temp].role.Equals("assistant"))
									{
										ModMain.ActionMessages[unit.data.unitData.unitID][temp].content = UIInput.text;
										AIActionMessage xmlText3 = CommandTool.GetXmlText(UIInput.text);
										if (xmlText3 != null)
										{
											ModMain.Messages[unit.data.unitData.unitID][temp].content = xmlText3.yourResponse;
										}
										else
										{
											ModMain.Messages[unit.data.unitData.unitID][temp].content = UIInput.text;
											UITipItem.AddTip("Json文本格式不正确", 3f);
										}
									}
									else
									{
										ModMain.Messages[unit.data.unitData.unitID][temp].content = UIInput.text;
										ModMain.ActionMessages[unit.data.unitData.unitID][temp].content = UIInput.text;
									}
								}
								UpdateChatMessageList();
							}
							if (isTips)
							{
								UITipItem.AddTip("记录修改成功!", 3f);
							}
						}
					}
					catch (Exception ex2)
					{
						Console.WriteLine(ex2.ToString());
					}
				}));
			}
			catch (Exception ex)
			{
				Console.WriteLine("复制失败: " + ex.Message);
			}
		}
		return "";
	}

	public string CopyChatMessage(int temp, bool isTips = true)
	{
		if (temp > 0)
		{
			if (isTips)
			{
				if (ModMain.Messages[unit.data.unitData.unitID].Count > temp)
				{
					try
					{
						Clipboard.SetDataObject(ModMain.Messages[unit.data.unitData.unitID][temp].content, copy: true);
						if (isTips)
						{
							UITipItem.AddTip("该组聊天记录已复制", 2f);
						}
						return ModMain.Messages[unit.data.unitData.unitID][temp].content;
					}
					catch (Exception ex)
					{
						Console.WriteLine("复制失败: " + ex.Message);
					}
				}
			}
			else if (isChat)
			{
				if (ModMain.Messages[unit.data.unitData.unitID].Count > temp)
				{
					try
					{
						Clipboard.SetDataObject(ModMain.Messages[unit.data.unitData.unitID][temp].content, copy: true);
						if (isTips)
						{
							UITipItem.AddTip("该组聊天记录已复制", 2f);
						}
						return ModMain.Messages[unit.data.unitData.unitID][temp].content;
					}
					catch (Exception ex2)
					{
						Console.WriteLine("复制失败: " + ex2.Message);
					}
				}
			}
			else if (ModMain.ActionMessages[unit.data.unitData.unitID].Count > temp)
			{
				try
				{
					Clipboard.SetDataObject(ModMain.ActionMessages[unit.data.unitData.unitID][temp].content, copy: true);
					if (isTips)
					{
						UITipItem.AddTip("该组聊天记录已复制", 2f);
					}
					return ModMain.ActionMessages[unit.data.unitData.unitID][temp].content;
				}
				catch (Exception ex3)
				{
					Console.WriteLine("复制失败: " + ex3.Message);
				}
			}
		}
		return "";
	}

	public string CopyActionMessage(int temp, bool isTips = true)
	{
		int num = temp * 2 + 4;
		if (num > 0 && ModMain.ActionMessages[unit.data.unitData.unitID].Count > num + 1)
		{
			string text = $"[{unit.data.unitData.propertyData.GetName()}]{ModMain.ActionMessages[unit.data.unitData.unitID][num].content}\n\n[{g.world.playerUnit.data.unitData.propertyData.GetName()}]{ModMain.ActionMessages[unit.data.unitData.unitID][num + 1].content}\n\n";
			try
			{
				Clipboard.SetDataObject(text, copy: true);
				if (isTips)
				{
					UITipItem.AddTip("该组聊天记录已复制", 2f);
				}
				return text;
			}
			catch (Exception ex)
			{
				Console.WriteLine("复制失败: " + ex.Message);
			}
		}
		return "";
	}

	public void UpdataPreText(int type = -1)
	{
		//IL_0147: Unknown result type (might be due to invalid IL or missing references)
		//IL_014d: Unknown result type (might be due to invalid IL or missing references)
		try
		{
			for (int num = goPreText.transform.GetChildCount() - 1; num >= 0; num--)
			{
				Object.Destroy((Object)(object)((Component)goPreText.transform.GetChild(num)).gameObject);
			}
			ActionList actionList = new ActionList();
			for (int i = 0; i < ModMain.preText.items.Count; i++)
			{
				PreTextItem preTextItem = ModMain.preText.items[i];
				if (type != -1 && preTextItem.actionType != type)
				{
					continue;
				}
				GameObject val = Object.Instantiate<GameObject>(g.res.Load<GameObject>("UI/PreTextItem"), goPreText.transform);
				if (!((Object)(object)val != (Object)null))
				{
					continue;
				}
				string actionName = actionList.GetActionName(preTextItem.actionType);
				((Component)val.transform.Find("TextID")).GetComponent<Text>().text = preTextItem.id.ToString();
				((Component)val.transform.Find("TextActionType")).GetComponent<Text>().text = actionName;
				((Component)val.transform.Find("Text")).GetComponent<Text>().text = preTextItem.text;
				string text = $"行动类型:{actionName}\n常用语内容:{preTextItem.text}";
				val.AddComponent<UISkyTipEffect>().InitData(text, default(Vector3));
				int temp = i;
				((UnityEvent)val.GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
				{
					selPreTextIndex = temp;
					if (selPreTextIndex < ModMain.preText.items.Count)
					{
						string text2 = ModMain.preText.items[selPreTextIndex].text;
						InputText.GetComponent<InputField>().text = InputText.GetComponent<InputField>().text + text2;
						TextPreActionName.GetComponent<Text>().text = actionName;
					}
				}));
			}
		}
		catch (Exception ex)
		{
			Console.WriteLine(ex.ToString());
		}
	}

	public void Update()
	{
		//IL_029c: Unknown result type (might be due to invalid IL or missing references)
		//IL_02a2: Unknown result type (might be due to invalid IL or missing references)
		//IL_032e: Unknown result type (might be due to invalid IL or missing references)
		//IL_0334: Unknown result type (might be due to invalid IL or missing references)
		//IL_03c0: Unknown result type (might be due to invalid IL or missing references)
		//IL_03c6: Unknown result type (might be due to invalid IL or missing references)
		//IL_0452: Unknown result type (might be due to invalid IL or missing references)
		//IL_0458: Unknown result type (might be due to invalid IL or missing references)
		//IL_052a: Unknown result type (might be due to invalid IL or missing references)
		//IL_0530: Unknown result type (might be due to invalid IL or missing references)
		//IL_0102: Unknown result type (might be due to invalid IL or missing references)
		//IL_010e: Unknown result type (might be due to invalid IL or missing references)
		//IL_011e: Unknown result type (might be due to invalid IL or missing references)
		//IL_0139: Unknown result type (might be due to invalid IL or missing references)
		//IL_0144: Unknown result type (might be due to invalid IL or missing references)
		//IL_0154: Unknown result type (might be due to invalid IL or missing references)
		try
		{
			if (isUpdate)
			{
				if (!StringExtensions.IsEmpty(textOldMsg))
				{
					if ((Object)(object)ChatItem != (Object)null)
					{
						int num = textOldMsg.IndexOf("]");
						if (textOldMsg.StartsWith("["))
						{
							textOldMsg = textOldMsg.Substring(num + 1);
						}
						textOldMsg = textOldMsg.Trim('\r', '\n');
						string text = CommandTool.RegexForOutPut(textOldMsg);
						((Component)ChatItem.transform.Find("Right/Text")).GetComponent<Text>().text = text;
						RectTransform component = ((Component)ChatItem.transform.Find("Right/Image")).GetComponent<RectTransform>();
						RectTransform component2 = ((Component)ChatItem.transform.Find("Right/Text")).GetComponent<RectTransform>();
						LayoutRebuilder.ForceRebuildLayoutImmediate(component2);
						Canvas.ForceUpdateCanvases();
						component.sizeDelta = new Vector2(component.sizeDelta.x, component2.sizeDelta.y + 30f);
						RectTransform component3 = ((Component)ChatItem.transform).GetComponent<RectTransform>();
						component3.sizeDelta = new Vector2(component3.sizeDelta.x, component.sizeDelta.y + 80f);
						ScrollRect component4 = ((Component)((Component)this).transform.Find("Root/PanelMain/OutPut/Panel/Scroll View")).GetComponent<ScrollRect>();
						LayoutRebuilder.ForceRebuildLayoutImmediate(component4.content);
						Canvas.ForceUpdateCanvases();
						component4.verticalNormalizedPosition = 0f;
					}
					isUpdate = false;
				}
				else
				{
					Console.WriteLine("待刷新内容为空");
				}
			}
			if (isUpdateLog)
			{
				if (ModMain.unitLog.ContainsKey(unit.data.unitData.unitID))
				{
					CommandTool.AddToLog(unit, g.world.playerUnit, isShow: false);
					UITipItem.AddTip("日志提炼成功！", 0f);
				}
				else
				{
					UITipItem.AddTip("日志提炼失败！", 0f);
				}
				isUpdateLog = false;
			}
			if (!isComplete)
			{
				return;
			}
			try
			{
				((Selectable)ButtonSend.GetComponent<Button>()).interactable = true;
				index++;
				int temp = index;
				Console.WriteLine("当前聊天记录序号Index：" + index);
				if ((Object)(object)ChatItem != (Object)null)
				{
					((Component)ChatItem.transform.Find("Right/ButtonDel")).gameObject.AddComponent<UISkyTipEffect>().InitData("删除该条聊天记录", default(Vector3));
					((UnityEventBase)((Component)ChatItem.transform.Find("Right/ButtonDel")).GetComponent<Button>().onClick).RemoveAllListeners();
					((UnityEvent)((Component)ChatItem.transform.Find("Right/ButtonDel")).GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
					{
						g.ui.OpenUI<UICheckPopup>(UIType.CheckPopup).InitData("提示", "是否确认删除该条聊天记录？", 2, Action.op_Implicit((Action)delegate
						{
							DelChatMessageItem(temp);
						}), (Action)null);
					}));
					((Component)ChatItem.transform.Find("Right/ButtonCopy")).gameObject.AddComponent<UISkyTipEffect>().InitData("复制该条聊天记录", default(Vector3));
					((UnityEventBase)((Component)ChatItem.transform.Find("Right/ButtonCopy")).GetComponent<Button>().onClick).RemoveAllListeners();
					((UnityEvent)((Component)ChatItem.transform.Find("Right/ButtonCopy")).GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
					{
						CopyChatMessage(temp);
					}));
					((Component)ChatItem.transform.Find("Right/ButtonModity")).gameObject.AddComponent<UISkyTipEffect>().InitData("修改该条聊天记录", default(Vector3));
					((UnityEventBase)((Component)ChatItem.transform.Find("Right/ButtonModity")).GetComponent<Button>().onClick).RemoveAllListeners();
					((UnityEvent)((Component)ChatItem.transform.Find("Right/ButtonModity")).GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
					{
						ModityChatMessage(temp);
					}));
					((Component)ChatItem.transform.Find("Right/ButtonShow")).gameObject.AddComponent<UISkyTipEffect>().InitData("显示完整内容", default(Vector3));
					((UnityEventBase)((Component)ChatItem.transform.Find("Right/ButtonShow")).GetComponent<Button>().onClick).RemoveAllListeners();
					((UnityEvent)((Component)ChatItem.transform.Find("Right/ButtonShow")).GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
					{
						if (isChat)
						{
							if (temp >= 0 && temp < ModMain.Messages[unit.data.unitData.unitID].Count)
							{
								string content = ModMain.Messages[unit.data.unitData.unitID][temp].content;
								g.ui.OpenUI<UITextInfoLong>(UIType.TextInfoLong).InitData("显示完整内容", content, "", (Action)null, false);
							}
						}
						else if (temp >= 0 && temp < ModMain.ActionMessages[unit.data.unitData.unitID].Count)
						{
							string content2 = ModMain.ActionMessages[unit.data.unitData.unitID][temp].content;
							g.ui.OpenUI<UITextInfoLong>(UIType.TextInfoLong).InitData("显示完整内容", content2, "", (Action)null, false);
						}
					}));
					((Component)ChatItem.transform.Find("Right/ButtonSpeek")).gameObject.SetActive(true);
					string textStr = ((Component)ChatItem.transform.Find("Right/Text")).GetComponent<Text>().text;
					((Component)ChatItem.transform.Find("Right/ButtonSpeek")).gameObject.AddComponent<UISkyTipEffect>().InitData("语音播放", default(Vector3));
					((UnityEventBase)((Component)ChatItem.transform.Find("Right/ButtonSpeek")).GetComponent<Button>().onClick).RemoveAllListeners();
					((UnityEvent)((Component)ChatItem.transform.Find("Right/ButtonSpeek")).GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
					{
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
									await CommandTool.TextToSpeek(unit, textStr, unit.data.unitData.propertyData.GetName() + "-" + temp);
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
							catch (Exception ex3)
							{
								Console.WriteLine(ex3.ToString());
							}
							isSpeek = false;
						}
					}));
				}
			}
			catch (Exception ex)
			{
				Console.WriteLine(ex.ToString());
			}
			isComplete = false;
		}
		catch (Exception ex2)
		{
			Console.WriteLine(ex2.ToString());
		}
	}

	public void ShowDrama(WorldUnitBase leftUnit, WorldUnitBase rightUnit, string Text, string opText, Action action)
	{
		//IL_0009: Unknown result type (might be due to invalid IL or missing references)
		//IL_000f: Expected O, but got Unknown
		int num = ModMain.mod_ID + 1;
		UICustomDramaDyn val = new UICustomDramaDyn(num);
		int num2 = ModMain.mod_ID + 1;
		((UICustomDramaBase)val).dramaData.dialogueOptions[num2] = opText;
		((UICustomDramaBase)val).dramaData.dialogueText[num] = Text;
		val.SetOptionCall(num2, Action.op_Implicit(action));
		if (aIActionMessage != null)
		{
			Console.WriteLine("aIActionMessage不为空！");
			if (!StringExtensions.IsEmpty(aIActionMessage.Option1))
			{
				Console.WriteLine("Option1不为空！");
				int num3 = ModMain.mod_ID + 2;
				((UICustomDramaBase)val).dramaData.dialogueOptions[num3] = aIActionMessage.Option1;
				val.SetOptionCall(num3, Action.op_Implicit((Action)delegate
				{
					InputText.GetComponent<InputField>().text = aIActionMessage.Option1;
				}));
			}
			else
			{
				Console.WriteLine("Option1为空！");
				int num4 = ModMain.mod_ID + 2;
				((UICustomDramaBase)val).dramaData.dialogueOptions[num4] = "然后呢";
				val.SetOptionCall(num4, Action.op_Implicit((Action)delegate
				{
					InputText.GetComponent<InputField>().text = "然后呢";
				}));
			}
			if (!StringExtensions.IsEmpty(aIActionMessage.Option2))
			{
				Console.WriteLine("Option2不为空！");
				int num5 = ModMain.mod_ID + 3;
				((UICustomDramaBase)val).dramaData.dialogueOptions[num5] = aIActionMessage.Option2;
				val.SetOptionCall(num5, Action.op_Implicit((Action)delegate
				{
					InputText.GetComponent<InputField>().text = aIActionMessage.Option2;
				}));
			}
			else
			{
				Console.WriteLine("Option2为空！");
				int num6 = ModMain.mod_ID + 3;
				((UICustomDramaBase)val).dramaData.dialogueOptions[num6] = "继续";
				val.SetOptionCall(num6, Action.op_Implicit((Action)delegate
				{
					InputText.GetComponent<InputField>().text = "继续";
				}));
			}
			if (!StringExtensions.IsEmpty(aIActionMessage.Option3))
			{
				Console.WriteLine("Option3不为空！");
				int num7 = ModMain.mod_ID + 4;
				((UICustomDramaBase)val).dramaData.dialogueOptions[num7] = aIActionMessage.Option3;
				val.SetOptionCall(num7, Action.op_Implicit((Action)delegate
				{
					InputText.GetComponent<InputField>().text = aIActionMessage.Option3;
				}));
			}
			else
			{
				Console.WriteLine("Option3为空！");
				int num8 = ModMain.mod_ID + 4;
				((UICustomDramaBase)val).dramaData.dialogueOptions[num8] = "打个招呼";
				val.SetOptionCall(num8, Action.op_Implicit((Action)delegate
				{
					InputText.GetComponent<InputField>().text = "打个招呼";
				}));
			}
			if (!StringExtensions.IsEmpty(aIActionMessage.Option4))
			{
				Console.WriteLine("Option4不为空！");
				int num9 = ModMain.mod_ID + 5;
				((UICustomDramaBase)val).dramaData.dialogueOptions[num9] = aIActionMessage.Option4;
				val.SetOptionCall(num9, Action.op_Implicit((Action)delegate
				{
					InputText.GetComponent<InputField>().text = aIActionMessage.Option4;
				}));
			}
			else
			{
				Console.WriteLine("Option4为空！");
				int num10 = ModMain.mod_ID + 5;
				((UICustomDramaBase)val).dramaData.dialogueOptions[num10] = "下次再聊，再见！";
				val.SetOptionCall(num10, Action.op_Implicit((Action)delegate
				{
					InputText.GetComponent<InputField>().text = "下次再聊，再见！";
				}));
			}
		}
		else
		{
			Console.WriteLine("aIActionMessage为空！");
			int num11 = ModMain.mod_ID + 6;
			((UICustomDramaBase)val).dramaData.dialogueOptions[num11] = "打个招呼";
			val.SetOptionCall(num11, Action.op_Implicit((Action)delegate
			{
				InputText.GetComponent<InputField>().text = "打个招呼";
			}));
			int num12 = ModMain.mod_ID + 7;
			((UICustomDramaBase)val).dramaData.dialogueOptions[num12] = "表达情感";
			val.SetOptionCall(num12, Action.op_Implicit((Action)delegate
			{
				InputText.GetComponent<InputField>().text = "表达情感";
			}));
		}
		((UICustomDramaBase)val).dramaData.unitLeft = leftUnit;
		((UICustomDramaBase)val).dramaData.unitRight = rightUnit;
		val.OpenUI();
	}

	public void UpdataModel()
	{
		//IL_00ab: Unknown result type (might be due to invalid IL or missing references)
		//IL_013d: Unknown result type (might be due to invalid IL or missing references)
		//IL_0112: Unknown result type (might be due to invalid IL or missing references)
		((Component)((Component)this).transform.Find("Root/PanelMain/LeftUnit/Button/Text")).GetComponent<Text>().text = g.world.playerUnit.data.unitData.propertyData.GetName();
		((Component)((Component)this).transform.Find("Root/PanelMain/RightUnit/Button/Text")).GetComponent<Text>().text = unit.data.unitData.propertyData.GetName();
		RawImage component = ((Component)((Component)this).transform.Find("Root/PanelMain/LeftUnit/RawImage")).GetComponent<RawImage>();
		PortraitModel.CreateTextureInModelData(g.world.playerUnit.data.unitData.propertyData.modelData, component, new Vector2(0f, -4.5f), 1f, false, true, (Action<GameObject>)null);
		RawImage component2 = ((Component)((Component)this).transform.Find("Root/PanelMain/RightUnit/RawImage")).GetComponent<RawImage>();
		int fiveFlowerDramaNpcID = g.conf.dramaNpc.GetFiveFlowerDramaNpcID(unit);
		if (fiveFlowerDramaNpcID == 0)
		{
			PortraitModel.CreateTextureInModelData(unit.data.unitData.propertyData.modelData, component2, new Vector2(0f, -4.5f), 1f, false, true, (Action<GameObject>)null);
		}
		else
		{
			g.conf.dramaNpc.CreateTexture(fiveFlowerDramaNpcID, component2, new Vector2(0f, -4.5f), 1f, (PortraitModelData)null, (Action<GameObject>)null);
		}
		Console.WriteLine("刷新UI");
	}

	public void UpdataModel(WorldUnitBase unitT)
	{
		//IL_00b2: Unknown result type (might be due to invalid IL or missing references)
		//IL_0144: Unknown result type (might be due to invalid IL or missing references)
		//IL_0119: Unknown result type (might be due to invalid IL or missing references)
		unit = unitT;
		((Component)((Component)this).transform.Find("Root/PanelMain/LeftUnit/Button/Text")).GetComponent<Text>().text = g.world.playerUnit.data.unitData.propertyData.GetName();
		((Component)((Component)this).transform.Find("Root/PanelMain/RightUnit/Button/Text")).GetComponent<Text>().text = unit.data.unitData.propertyData.GetName();
		RawImage component = ((Component)((Component)this).transform.Find("Root/PanelMain/LeftUnit/RawImage")).GetComponent<RawImage>();
		PortraitModel.CreateTextureInModelData(g.world.playerUnit.data.unitData.propertyData.modelData, component, new Vector2(0f, -4.5f), 1f, false, true, (Action<GameObject>)null);
		RawImage component2 = ((Component)((Component)this).transform.Find("Root/PanelMain/RightUnit/RawImage")).GetComponent<RawImage>();
		int fiveFlowerDramaNpcID = g.conf.dramaNpc.GetFiveFlowerDramaNpcID(unit);
		if (fiveFlowerDramaNpcID == 0)
		{
			PortraitModel.CreateTextureInModelData(unit.data.unitData.propertyData.modelData, component2, new Vector2(0f, -4.5f), 1f, false, true, (Action<GameObject>)null);
		}
		else
		{
			g.conf.dramaNpc.CreateTexture(fiveFlowerDramaNpcID, component2, new Vector2(0f, -4.5f), 1f, (PortraitModelData)null, (Action<GameObject>)null);
		}
		Console.WriteLine("刷新UI");
	}

	private void OnUpdate()
	{
		if (!Input.GetKey((KeyCode)305) && !Input.GetKey((KeyCode)307) && Input.GetKeyDown((KeyCode)13) && InputText.GetComponent<InputField>().isFocused)
		{
			SendMessage();
		}
	}

	public void InitData(WorldUnitBase unitT)
	{
		//IL_004f: Unknown result type (might be due to invalid IL or missing references)
		//IL_0055: Expected O, but got Unknown
		//IL_01c4: Unknown result type (might be due to invalid IL or missing references)
		//IL_01ca: Unknown result type (might be due to invalid IL or missing references)
		//IL_01fd: Unknown result type (might be due to invalid IL or missing references)
		//IL_0203: Unknown result type (might be due to invalid IL or missing references)
		//IL_0282: Unknown result type (might be due to invalid IL or missing references)
		//IL_0288: Unknown result type (might be due to invalid IL or missing references)
		//IL_02af: Unknown result type (might be due to invalid IL or missing references)
		//IL_02b5: Unknown result type (might be due to invalid IL or missing references)
		//IL_0587: Unknown result type (might be due to invalid IL or missing references)
		//IL_058d: Unknown result type (might be due to invalid IL or missing references)
		//IL_05e4: Unknown result type (might be due to invalid IL or missing references)
		//IL_05ea: Unknown result type (might be due to invalid IL or missing references)
		//IL_0641: Unknown result type (might be due to invalid IL or missing references)
		//IL_0647: Unknown result type (might be due to invalid IL or missing references)
		//IL_06b1: Unknown result type (might be due to invalid IL or missing references)
		//IL_06b7: Unknown result type (might be due to invalid IL or missing references)
		//IL_070e: Unknown result type (might be due to invalid IL or missing references)
		//IL_0714: Unknown result type (might be due to invalid IL or missing references)
		//IL_076b: Unknown result type (might be due to invalid IL or missing references)
		//IL_0771: Unknown result type (might be due to invalid IL or missing references)
		try
		{
			Console.WriteLine("InitData");
			unit = unitT;
			ModMain.openUIType = 1;
			corUpdate = g.timer.Frame(Action.op_Implicit((Action)OnUpdate), 1, true);
			Shader val = Shader.Find("Custom/BackBlur");
			if ((Object)(object)val != (Object)null)
			{
				Material material = new Material(val);
				((Component)((Component)this).transform.GetChild(0)).GetComponent<Image>().material = material;
			}
			if (CommandTool.GetAIAction() == 0)
			{
				isChat = true;
			}
			else
			{
				isChat = false;
			}
			UpdataModel();
			goPreText = ((Component)((Component)this).transform.Find("Root/PanelLeft/Scroll View/Viewport/Content")).gameObject;
			ButtonAdd = ((Component)((Component)this).transform.Find("Root/PanelLeft/ButtonAdd")).gameObject;
			ButtonDel = ((Component)((Component)this).transform.Find("Root/PanelLeft/ButtonDel")).gameObject;
			ButtonModity = ((Component)((Component)this).transform.Find("Root/PanelLeft/ButtonModity")).gameObject;
			TextPreActionName = ((Component)((Component)this).transform.Find("Root/PanelLeft/Text2")).gameObject;
			ButtonClearAction = ((Component)((Component)this).transform.Find("Root/PanelLeft/ButtonClearAction")).gameObject;
			DropdownType = ((Component)((Component)this).transform.Find("Root/PanelLeft/DropdownType")).gameObject;
			InputFieldFind = ((Component)((Component)this).transform.Find("Root/PanelLeft/InputFieldFind")).gameObject;
			ButtonFind = ((Component)((Component)this).transform.Find("Root/PanelLeft/ButtonFind")).gameObject;
			ToggleShowLeft = ((Component)((Component)this).transform.Find("Root/PanelMain/ToggleShowLeft")).gameObject;
			ButtonRandomOP = ((Component)((Component)this).transform.Find("Root/PanelMain/ButtonRandomOP")).gameObject;
			ButtonRandomOP.AddComponent<UISkyTipEffect>().InitData("随便聊聊", default(Vector3));
			ButtonSend = ((Component)((Component)this).transform.Find("Root/PanelMain/ButtonSend")).gameObject;
			ButtonSend.AddComponent<UISkyTipEffect>().InitData("传音（可快捷：Enter发送，右Ctrl+Enter换行）", default(Vector3));
			ButtonAction = ((Component)((Component)this).transform.Find("Root/PanelMain/ButtonAction")).gameObject;
			InputText = ((Component)((Component)this).transform.Find("Root/PanelMain/Input/Panel/InputField")).gameObject;
			textNewMsg = InputText.GetComponent<InputField>().text;
			ToggleChat = ((Component)((Component)this).transform.Find("Root/PanelMain/Input/PanelToggle/ToggleChat")).gameObject;
			ToggleChat.AddComponent<UISkyTipEffect>().InitData("勾选后切换交互模式，会根据聊天内容智能开展行为交互，回复内容一次性读取解析及显示，回复速度有一定影响。\n取消勾选后切换为聊天模式，可正常传音及NPC实时回复，V3模型回复速度快。\n", default(Vector3));
			((Component)((Component)this).transform.Find("Root/PanelMain/Input/PanelToggle/ToggleAction")).gameObject.AddComponent<UISkyTipEffect>().InitData("勾选后切换交互模式，会根据聊天内容智能开展行为交互，回复内容一次性读取解析及显示，回复速度有一定影响", default(Vector3));
			if (isChat)
			{
				ToggleChat.GetComponent<Toggle>().isOn = false;
			}
			else
			{
				ToggleChat.GetComponent<Toggle>().isOn = true;
			}
			((UnityEventBase)ToggleChat.GetComponent<Toggle>().onValueChanged).RemoveAllListeners();
			((UnityEvent<bool>)(object)ToggleChat.GetComponent<Toggle>().onValueChanged).AddListener(UnityAction<bool>.op_Implicit((Action<bool>)onValueChangedChat));
			((Component)((Component)this).transform.Find("Root/PanelMain/ButtonExit")).GetComponent<Image>().sprite = SpriteTool.GetSprite("Common", "tuichu");
			((UnityEvent)((Component)((Component)this).transform.Find("Root/PanelMain/ButtonExit")).GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
			{
				//IL_0134: Unknown result type (might be due to invalid IL or missing references)
				//IL_013f: Expected O, but got Unknown
				if (state != 0)
				{
					client.CancelPendingRequests();
					ActionClient.CancelPendingRequests();
					state = 0;
					textOldMsg = "道友幸会，今天很不凑巧，神识传音似乎失败了，传闻是通道中的三个核心出错了。。。";
					UITipItem.AddTip("正在传音中，强行退出可能导致通道崩溃，请稍后再试。。。", 3f);
					isUpdate = true;
				}
				else
				{
					if (actionThread != null)
					{
						g.timer.Stop(actionThread);
						actionThread = null;
					}
					if (corUpdate != null)
					{
						g.timer.Stop(corUpdate);
					}
					if (isChat)
					{
						CommandTool.SetAIAction();
					}
					else
					{
						CommandTool.SetAIAction(1);
					}
					try
					{
						if (coroutine != null)
						{
							g.timer.Stop(coroutine);
							coroutine = null;
						}
						isSpeek = false;
						if (ModMain.mciSendString("stop my_mp3", null, 0, IntPtr.Zero) != 0)
						{
							Console.WriteLine("停止失败！");
						}
						else
						{
							Console.WriteLine("已停止！");
						}
						if (ModMain.mciSendString("close my_mp3", null, 0, IntPtr.Zero) != 0)
						{
							Console.WriteLine("关闭失败！");
						}
						else
						{
							Console.WriteLine("已关闭！");
						}
						CommandTool.AddToLog(unit, g.world.playerUnit);
					}
					catch (Exception ex2)
					{
						Console.WriteLine(ex2.Message);
					}
					g.ui.CloseUI(new UITypeBase("UIChatAINew", (UILayer)0), false);
					ModMain.openUIType = 0;
				}
			}));
			ToggleShowLeft.GetComponent<Toggle>().isOn = ModMain.preText.isShow == 1;
			((UnityEvent<bool>)(object)ToggleShowLeft.GetComponent<Toggle>().onValueChanged).AddListener(UnityAction<bool>.op_Implicit((Action<bool>)delegate(bool value)
			{
				ModMain.preText.isShow = (value ? 1 : 0);
				((Component)((Component)this).transform.Find("Root/PanelLeft")).gameObject.SetActive(value);
				ModMain.preText.Save();
			}));
			((Component)((Component)this).transform.Find("Root/PanelLeft")).gameObject.SetActive(ModMain.preText.isShow == 1);
			((UnityEvent)ButtonAction.GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
			{
			}));
			((UnityEvent)ButtonRandomOP.GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
			{
				ShowDrama(g.world.playerUnit, unit, "你还想聊些什么？", "随便聊聊", delegate
				{
					InputText.GetComponent<InputField>().text = "随便聊聊";
				});
			}));
			((UnityEvent)ButtonAdd.GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
			{
				//IL_0010: Unknown result type (might be due to invalid IL or missing references)
				//IL_001a: Expected O, but got Unknown
				ClassInjector.RegisterTypeInIl2Cpp<UIInputPreText>();
				UIInputPreText uIInputPreText = ((Component)g.ui.OpenUI(new UITypeBase("UIInputPreText", (UILayer)0))).gameObject.AddComponent<UIInputPreText>();
				uIInputPreText.InitData("", 0);
				((UnityEvent)uIInputPreText.ButtonSave.GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
				{
					try
					{
						if (StringExtensions.IsEmpty(UIInputPreText.text))
						{
							UITipItem.AddTip("常用语新增失败!", 3f);
						}
						else
						{
							ModMain.preText.items.Add(new PreTextItem
							{
								id = ModMain.preText.items.Count + 1,
								actionType = UIInputPreText.actionIndex,
								text = UIInputPreText.text
							});
							UITipItem.AddTip("添加成功！", 0f);
							UpdataPreText();
							ModMain.preText.Save();
						}
					}
					catch (Exception ex2)
					{
						Console.WriteLine(ex2.ToString());
					}
				}));
			}));
			((UnityEvent)ButtonDel.GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
			{
				if (selPreTextIndex >= 0 && selPreTextIndex < ModMain.preText.items.Count)
				{
					g.ui.OpenUI<UICheckPopup>(UIType.CheckPopup).InitData("提示", "是否确定将选中的常用语删除？", 2, Action.op_Implicit((Action)delegate
					{
						ModMain.preText.items.RemoveAt(selPreTextIndex);
						selPreTextIndex = -1;
						UpdataPreText();
						UITipItem.AddTip("删除成功", 3f);
						ModMain.preText.Save();
					}), (Action)null);
				}
			}));
			((UnityEvent)ButtonModity.GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
			{
				//IL_0036: Unknown result type (might be due to invalid IL or missing references)
				//IL_0040: Expected O, but got Unknown
				if (selPreTextIndex >= 0 && selPreTextIndex < ModMain.preText.items.Count)
				{
					ClassInjector.RegisterTypeInIl2Cpp<UIInputPreText>();
					UIInputPreText uIInputPreText = ((Component)g.ui.OpenUI(new UITypeBase("UIInputPreText", (UILayer)0))).gameObject.AddComponent<UIInputPreText>();
					uIInputPreText.InitData(ModMain.preText.items[selPreTextIndex].text, ModMain.preText.items[selPreTextIndex].actionType);
					((UnityEvent)uIInputPreText.ButtonSave.GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
					{
						try
						{
							if (StringExtensions.IsEmpty(UIInputPreText.text))
							{
								UITipItem.AddTip("记录修改失败!", 3f);
							}
							else
							{
								ModMain.preText.items[selPreTextIndex].actionType = UIInputPreText.actionIndex;
								ModMain.preText.items[selPreTextIndex].text = UIInputPreText.text;
								UITipItem.AddTip("修改成功！", 0f);
								UpdataPreText();
								ModMain.preText.Save();
							}
						}
						catch (Exception ex2)
						{
							Console.WriteLine(ex2.ToString());
						}
					}));
				}
			}));
			((UnityEvent)ButtonClearAction.GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
			{
				selPreTextIndex = -1;
				TextPreActionName.GetComponent<Text>().text = "";
			}));
			((UnityEvent)((Component)((Component)this).transform.Find("Root/PanelMain/OutPut/ButtonNext")).GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
			{
				curPage++;
				UpdateChatMessageList();
			}));
			((UnityEvent)((Component)((Component)this).transform.Find("Root/PanelMain/OutPut/ButtonLast")).GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
			{
				curPage--;
				UpdateChatMessageList();
			}));
			unitSet = CommandTool.GetUnitStr(unit);
			if (StringExtensions.IsEmpty(unitSet))
			{
				unitSet = "无";
			}
			((Component)((Component)this).transform.Find("Root/PanelMain/ButtonSetUnit")).gameObject.AddComponent<UISkyTipEffect>().InitData(unitSet, default(Vector3));
			((UnityEvent)((Component)((Component)this).transform.Find("Root/PanelMain/ButtonSetUnit")).GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
			{
				//IL_0010: Unknown result type (might be due to invalid IL or missing references)
				//IL_001a: Expected O, but got Unknown
				try
				{
					ClassInjector.RegisterTypeInIl2Cpp<UIUnitSet>();
					UIUnitSet uIUnitSet = ((Component)g.ui.OpenUI(new UITypeBase("UIUnitSet", (UILayer)0))).gameObject.AddComponent<UIUnitSet>();
					uIUnitSet.InitData(unit);
					((UnityEvent)uIUnitSet.ButtonSave.GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
					{
						g.timer.Time(Action.op_Implicit((Action)delegate
						{
							//IL_0081: Unknown result type (might be due to invalid IL or missing references)
							//IL_0087: Unknown result type (might be due to invalid IL or missing references)
							//IL_0051: Unknown result type (might be due to invalid IL or missing references)
							//IL_0057: Unknown result type (might be due to invalid IL or missing references)
							try
							{
								unitSet = CommandTool.GetUnitStr(unit);
								if (StringExtensions.IsEmpty(unitSet))
								{
									unitSet = "无";
								}
								UISkyTipEffect component = ((Component)((Component)this).transform.Find("Root/PanelMain/ButtonSetUnit")).GetComponent<UISkyTipEffect>();
								if ((Object)(object)component != (Object)null)
								{
									component.InitData(unitSet, default(Vector3));
								}
								else
								{
									((Component)((Component)this).transform.Find("Root/PanelMain/ButtonSetUnit")).gameObject.AddComponent<UISkyTipEffect>().InitData(unitSet, default(Vector3));
								}
								Console.WriteLine(unitSet);
							}
							catch (Exception ex3)
							{
								Console.WriteLine(ex3.Message);
							}
						}), 2f, false);
					}));
				}
				catch (Exception ex2)
				{
					Console.WriteLine(ex2.Message);
				}
			}));
			((Component)((Component)this).transform.Find("Root/PanelMain/ButtonLook")).gameObject.AddComponent<UISkyTipEffect>().InitData("查看NPC信息", default(Vector3));
			((UnityEvent)((Component)((Component)this).transform.Find("Root/PanelMain/ButtonLook")).GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
			{
				g.ui.OpenUI<UINPCInfo>(UIType.NPCInfo).InitData(unit, true);
			}));
			((Component)((Component)this).transform.Find("Root/PanelMain/ButtonSetting")).gameObject.AddComponent<UISkyTipEffect>().InitData("AI参数设置", default(Vector3));
			((UnityEvent)((Component)((Component)this).transform.Find("Root/PanelMain/ButtonSetting")).GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
			{
				//IL_0010: Unknown result type (might be due to invalid IL or missing references)
				//IL_001a: Expected O, but got Unknown
				ClassInjector.RegisterTypeInIl2Cpp<UISetting>();
				UISetting uISetting = ((Component)g.ui.OpenUI(new UITypeBase("UISetting", (UILayer)0))).gameObject.AddComponent<UISetting>();
				uISetting.InitData();
				((UnityEvent)uISetting.ButtonOK.GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
				{
				}));
			}));
			((Component)((Component)this).transform.Find("Root/PanelMain/ButtonClear")).gameObject.AddComponent<UISkyTipEffect>().InitData("清空聊天记录", default(Vector3));
			((UnityEvent)((Component)((Component)this).transform.Find("Root/PanelMain/ButtonClear")).GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
			{
				try
				{
					g.ui.OpenUI<UICheckPopup>(UIType.CheckPopup).InitData("提示", "是否确认删除所有聊天记录？", 2, Action.op_Implicit((Action)delegate
					{
						g.ui.OpenUI<UICheckPopup>(UIType.CheckPopup).InitData("提示", "是否需要将聊天记录进行提炼并纳入NPC个人经历（聊天记录数量大于2条生效）？", 2, Action.op_Implicit((Action)delegate
						{
							try
							{
								if (ModMain.Messages.ContainsKey(unit.data.unitData.unitID))
								{
									string msg = ModMain.aIPromptCommand.promptFile.unitLogPrompt + "\n[原始对话]\n";
									if (ModMain.Messages[unit.data.unitData.unitID].Count < 7)
									{
										UITipItem.AddTip("聊天记录内容较少，暂无提炼意义，开始自动清理。", 3f);
										msg = "";
									}
									else
									{
										for (int i = 4; i < ModMain.Messages[unit.data.unitData.unitID].Count; i += 2)
										{
											if (ModMain.Messages[unit.data.unitData.unitID][i].role.Equals("system"))
											{
												i--;
											}
											else if (ModMain.Messages[unit.data.unitData.unitID][i].role.Equals("user"))
											{
												if (i + 1 < ModMain.Messages[unit.data.unitData.unitID].Count)
												{
													string text = $"{g.world.playerUnit.data.unitData.propertyData.GetName()}:{ModMain.Messages[unit.data.unitData.unitID][i].content}\n{unit.data.unitData.propertyData.GetName()}:{ModMain.Messages[unit.data.unitData.unitID][i + 1].content}\n";
													msg += text;
												}
											}
											else
											{
												string text2 = $"{unit.data.unitData.propertyData.GetName()}:{ModMain.Messages[unit.data.unitData.unitID][i].content}\n";
												i--;
												msg += text2;
											}
										}
									}
									if (msg.Length > 150)
									{
										g.timer.Time(Action.op_Implicit((Action)async delegate
										{
											try
											{
												UITipItem.AddTip("开始提炼聊天记录。", 3f);
												Console.WriteLine("输入内容：" + msg);
												await CommandTool.SendToDeepSeekForLog(unit, "user", msg);
												isUpdateLog = true;
											}
											catch (Exception ex4)
											{
												Console.WriteLine(ex4.Message ?? "");
											}
										}), 0f, false);
									}
									if (isChat)
									{
										ClearChatMessageList();
									}
									else
									{
										ClearActionMessageList();
									}
									g.timer.Time(Action.op_Implicit((Action)delegate
									{
										for (int j = 0; j < ModMain.vectorKnowledgeCMD.vectorKnowledge.knowledge.Count; j++)
										{
											KeyValuePair<string, VectorKnowledgeItem> keyValuePair = ModMain.vectorKnowledgeCMD.vectorKnowledge.knowledge.ElementAt(j);
											if (keyValuePair.Key.Contains(unit.GetUnitID() + "私聊"))
											{
												ModMain.vectorKnowledgeCMD.vectorKnowledge.knowledge.Remove(keyValuePair.Key);
												ModMain.vectorKnowledgeCMD.Save();
												break;
											}
										}
									}), 0f, false);
								}
							}
							catch (Exception ex3)
							{
								Console.WriteLine(ex3.ToString());
							}
						}), Action.op_Implicit((Action)delegate
						{
							if (isChat)
							{
								ClearChatMessageList();
							}
							else
							{
								ClearActionMessageList();
							}
							g.timer.Time(Action.op_Implicit((Action)delegate
							{
								for (int i = 0; i < ModMain.vectorKnowledgeCMD.vectorKnowledge.knowledge.Count; i++)
								{
									KeyValuePair<string, VectorKnowledgeItem> keyValuePair = ModMain.vectorKnowledgeCMD.vectorKnowledge.knowledge.ElementAt(i);
									if (keyValuePair.Key.Contains(unit.GetUnitID() + "私聊"))
									{
										ModMain.vectorKnowledgeCMD.vectorKnowledge.knowledge.Remove(keyValuePair.Key);
										ModMain.vectorKnowledgeCMD.Save();
										break;
									}
								}
							}), 0f, false);
						}));
					}), (Action)null);
				}
				catch (Exception ex2)
				{
					Console.WriteLine(ex2.ToString());
				}
			}));
			((Component)((Component)this).transform.Find("Root/PanelMain/ButtonToLog")).gameObject.AddComponent<UISkyTipEffect>().InitData("聊天记录管理及经历提炼。", default(Vector3));
			((UnityEvent)((Component)((Component)this).transform.Find("Root/PanelMain/ButtonToLog")).GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
			{
				//IL_0010: Unknown result type (might be due to invalid IL or missing references)
				//IL_001a: Expected O, but got Unknown
				ClassInjector.RegisterTypeInIl2Cpp<UIMSGList>();
				((Component)g.ui.OpenUI(new UITypeBase("UIMSGList", (UILayer)0))).gameObject.AddComponent<UIMSGList>().InitData(unit);
				((UnityEvent)((Component)((Component)this).transform.Find("Root/PanelMain/ButtonExit")).GetComponent<Button>().onClick).Invoke();
			}));
			((Component)((Component)this).transform.Find("Root/PanelMain/ButtonCopy")).gameObject.AddComponent<UISkyTipEffect>().InitData("复制所有聊天记录", default(Vector3));
			((UnityEvent)((Component)((Component)this).transform.Find("Root/PanelMain/ButtonCopy")).GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
			{
				g.ui.OpenUI<UICheckPopup>(UIType.CheckPopup).InitData("提示", "是否确认复制所有聊天记录至剪切板？", 2, Action.op_Implicit((Action)delegate
				{
					if (isChat)
					{
						CopyChatMessageList();
					}
					else
					{
						CopyActionMessageList();
					}
				}), (Action)null);
			}));
			((UnityEvent)ButtonFind.GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
			{
				try
				{
					string text = InputFieldFind.GetComponent<InputField>().text;
					if (!string.IsNullOrEmpty(text))
					{
						bool flag = false;
						int num = 0;
						if (selPreIndex >= 0 && selPreIndex < goPreText.transform.GetChildCount() - 1)
						{
							num = selPreIndex + 1;
						}
						for (int i = num; i < goPreText.transform.GetChildCount(); i++)
						{
							Transform child = goPreText.transform.GetChild(i);
							if ((Object)(object)child != (Object)null && (((Component)child.Find("TextActionType")).GetComponent<Text>().text + ":" + ((Component)child.Find("Text")).GetComponent<Text>().text).Contains(text))
							{
								((Selectable)((Component)child).GetComponent<Button>()).Select();
								ScrollRect component = ((Component)((Component)this).transform.Find("Root/PanelLeft/Scroll View")).GetComponent<ScrollRect>();
								ScrollToItemCoroutine(component, i);
								flag = true;
								selPreIndex = i;
								break;
							}
						}
						if (!flag)
						{
							for (int j = 0; j < goPreText.transform.GetChildCount(); j++)
							{
								Transform child2 = goPreText.transform.GetChild(j);
								if ((Object)(object)child2 != (Object)null && (((Component)child2.Find("TextActionType")).GetComponent<Text>().text + ":" + ((Component)child2.Find("Text")).GetComponent<Text>().text).Contains(text))
								{
									((Selectable)((Component)child2).GetComponent<Button>()).Select();
									ScrollRect component2 = ((Component)((Component)this).transform.Find("Root/PanelLeft/Scroll View")).GetComponent<ScrollRect>();
									ScrollToItemCoroutine(component2, j);
									flag = true;
									selPreIndex = j;
									break;
								}
							}
							if (!flag)
							{
								UITipItem.AddTip("未找到匹配项。", 3f);
							}
							else
							{
								UITipItem.AddTip("已找到匹配项：匹配关键词：" + text, 3f);
							}
						}
						else
						{
							UITipItem.AddTip("已找到匹配项：匹配关键词：" + text, 3f);
						}
					}
					else
					{
						UITipItem.AddTip("待搜索内容为空！", 3f);
					}
				}
				catch (Exception ex2)
				{
					Console.WriteLine(ex2.ToString());
					Console.WriteLine("批量操作出错");
				}
			}));
			OutPutContent = ((Component)((Component)this).transform.Find("Root/PanelMain/OutPut/Panel/Scroll View/Viewport/Content")).gameObject;
			ModMain.preText.Init();
			UpdateChatMessageList();
			UpdataPreText();
			UpdataList();
			Console.WriteLine("UI初始化完成");
			InitAI(unitT);
		}
		catch (Exception ex)
		{
			Console.WriteLine(ex.ToString());
			Console.WriteLine("InitData出错");
		}
	}

	private void ScrollToItemCoroutine(ScrollRect scrollRect, int itemIndex, float duration = 0f)
	{
		float verticalNormalizedPosition = 1f - (float)itemIndex / (float)(((Transform)scrollRect.content).childCount - 1);
		LayoutRebuilder.ForceRebuildLayoutImmediate(scrollRect.content);
		Canvas.ForceUpdateCanvases();
		scrollRect.verticalNormalizedPosition = verticalNormalizedPosition;
	}

	public void UpdataList(int selValue = -1)
	{
		//IL_0073: Unknown result type (might be due to invalid IL or missing references)
		//IL_0079: Unknown result type (might be due to invalid IL or missing references)
		ActionList actionList = new ActionList();
		DropdownType.GetComponent<Dropdown>().options.Clear();
		string tips = string.Format("行动序号:{0}\n行动名称:{1}\n行动描述:{2}", 0, "闲聊", "禁止选择此行动");
		((Component)DropdownType.transform.Find("Label")).gameObject.AddComponent<UISkyTipEffect>().InitData("", default(Vector3));
		List<string> val = new List<string>();
		val.Add("全部");
		foreach (ActionItem action in actionList.actions)
		{
			val.Add(action.actionIndex + action.actionName);
		}
		DropdownType.GetComponent<Dropdown>().AddOptions(val);
		((UnityEventBase)DropdownType.GetComponent<Dropdown>().onValueChanged).RemoveAllListeners();
		((UnityEvent<int>)(object)DropdownType.GetComponent<Dropdown>().onValueChanged).AddListener(UnityAction<int>.op_Implicit((Action<int>)delegate(int value)
		{
			//IL_0102: Unknown result type (might be due to invalid IL or missing references)
			//IL_0108: Unknown result type (might be due to invalid IL or missing references)
			//IL_00a4: Unknown result type (might be due to invalid IL or missing references)
			//IL_00aa: Unknown result type (might be due to invalid IL or missing references)
			int num = value - 1;
			if (num >= 0 && num < actionList.actions.Count)
			{
				tips = $"行动序号:{actionList.actions[num].actionIndex}\n行动名称:{actionList.actions[num].actionName}\n行动描述:{actionList.actions[num].actionDesc}";
				((Component)DropdownType.transform.Find("Label")).gameObject.GetComponent<UISkyTipEffect>().InitData(tips, default(Vector3));
			}
			else if (num == -1)
			{
				tips = string.Format("行动序号:{0}\n行动名称:{1}\n行动描述:{2}", -1, "全部", "全部");
				((Component)DropdownType.transform.Find("Label")).gameObject.GetComponent<UISkyTipEffect>().InitData(tips, default(Vector3));
			}
			UpdataPreText(num);
		}));
		DropdownType.GetComponent<Dropdown>().value = selValue;
	}

	public void onValueChangedChat(bool value)
	{
		if (state != 0)
		{
			UITipItem.AddTip("对方正在传音中。。。", 0f);
		}
		else if (value)
		{
			isChat = false;
			UpdateChatMessageList();
			UITipItem.AddTip("交互模式开启", 2f);
		}
		else
		{
			isChat = true;
			UpdateChatMessageList();
			UITipItem.AddTip("聊天模式开启", 2f);
		}
	}

	public async Task SendToDeepSeek(string role, string question, int type = 0)
	{
		int num = ((!isChat) ? 1 : 0);
		string modelName = ModMain.modelName;
		AIConfInfoItem aIConfInfoItem = null;
		if (ModMain.aIConfInfo.aiConfUses.ContainsKey(1))
		{
			aIConfInfoItem = ModMain.aIConfInfo.GetUseNextIndex(1);
			if (aIConfInfoItem != null)
			{
				modelName = aIConfInfoItem.model;
			}
			else
			{
				Console.WriteLine("轮询判断失败！");
			}
		}
		List<Message> messages = new List<Message>();
		List<Message> messages2 = CommandTool.GetMessages(unit, num);
		if (messages2.Count > 0)
		{
			messages.Add(messages2[0]);
		}
		List<Message> msgTemp = new List<Message>();
		string text = "";
		string text2 = "";
		foreach (PromptItem item2 in ModMain.aIPromptCommand.promptFile.items)
		{
			if (item2.addMode != 1)
			{
				continue;
			}
			string[] array = item2.keyWord.Split(';');
			foreach (string text3 in array)
			{
				if (!StringExtensions.IsEmpty(text3) && question.Contains(text3))
				{
					text = text + item2.name + "  ";
					text2 = text2 + item2.content + "\n";
					break;
				}
			}
		}
		if (!StringExtensions.IsEmpty(text2))
		{
			msgTemp.Add(new Message
			{
				role = "system",
				content = "[系统补充设定]\n" + text2
			});
			Console.WriteLine("触发关键词提示词:" + text);
		}
		BatchQueryManager batchQueryManager = new BatchQueryManager();
		List<QueryTaskConfig> list = new List<QueryTaskConfig>();
		if (ModMain.vectorKnowledgeCMD.vectorKnowledge.isOpen)
		{
			list.Add(new QueryTaskConfig
			{
				Name = "基础知识库",
				Input = question,
				OnSingleComplete = delegate(List<(string, float)> results)
				{
					if (results != null && results.Count > 0)
					{
						Console.WriteLine($"找到了基础知识库 {results.Count} 条相关知识！");
						string vectorKnowledgeString3 = VectorKnowledgeBase.GetVectorKnowledgeString(results, ModMain.vectorKnowledgeCMD.vectorKnowledge.topK);
						msgTemp.Add(new Message
						{
							role = "system",
							content = "[基础知识库]\n" + vectorKnowledgeString3
						});
						Console.WriteLine("基础知识库长度：" + vectorKnowledgeString3.Length);
					}
					else
					{
						Console.WriteLine("未找到相关知识。");
					}
				}
			});
			if (ModMain.worldLogCMD.worldLog.isAddPrompt)
			{
				list.Add(new QueryTaskConfig
				{
					Name = "主世界剧情",
					Input = question,
					OnSingleComplete = delegate(List<(string, float)> results)
					{
						if (results != null && results.Count > 0)
						{
							Console.WriteLine($"找到了主世界剧情 {results.Count} 条相关知识！");
							string vectorKnowledgeString3 = VectorKnowledgeBase.GetVectorKnowledgeString(results, ModMain.vectorKnowledgeCMD.vectorKnowledge.topK, "以下是主世界剧情片段，请在输出时进行参考。");
							Console.WriteLine("主世界剧情长度：" + vectorKnowledgeString3.Length);
							msgTemp.Add(new Message
							{
								role = "system",
								content = "[主世界剧情]\n" + vectorKnowledgeString3
							});
						}
						else
						{
							Console.WriteLine("未找到相关知识。");
						}
					}
				});
			}
		}
		else if (ModMain.worldLogCMD.worldLog.isAddPrompt)
		{
			msgTemp.Add(new Message
			{
				role = "system",
				content = "[主世界剧情]\n" + ModMain.worldLogCMD.GetWorldText()
			});
		}
		List<(string Text, float Score)> InPutTextShare = new List<(string, float)>();
		List<(string Text, float Score)> InPutText = new List<(string, float)>();
		if (ModMain.vectorKnowledgeCMD.vectorKnowledge.isOpenMsgMode)
		{
			if (messages2.Count >= 3)
			{
				messages.Add(messages2[1]);
				messages.Add(messages2[2]);
			}
			messages.AddRange(CommandTool.GetMessagesNoSystem(unit, num, ModMain.vectorKnowledgeCMD.vectorKnowledge.msgRetainCount));
			foreach (string item3 in ModMain.shareUnit)
			{
				list.Add(new QueryTaskConfig
				{
					Name = item3 + "私聊",
					Input = question,
					OnSingleComplete = delegate(List<(string, float)> results)
					{
						if (results != null && results.Count > 0)
						{
							InPutTextShare.AddRange(results);
						}
						else
						{
							Console.WriteLine("未找到相关知识。");
						}
					}
				});
			}
			list.Add(new QueryTaskConfig
			{
				Name = unit.GetUnitID() + "私聊",
				Input = question,
				OnSingleComplete = delegate(List<(string, float)> results)
				{
					if (results != null && results.Count > 0)
					{
						InPutText.AddRange(results);
					}
				}
			});
			if (CommandTool.GetAIUnitSet(unit).tog6 == 1)
			{
				list.Add(new QueryTaskConfig
				{
					Name = unit.GetUnitID() + "群聊",
					Input = question,
					OnSingleComplete = delegate(List<(string, float)> results)
					{
						if (results != null && results.Count > 0)
						{
							Console.WriteLine($"找到了群聊中 {results.Count} 条相关知识！");
							InPutText.AddRange(results);
						}
						else
						{
							Console.WriteLine("未找到相关知识。");
						}
					}
				});
			}
		}
		else
		{
			messages = CommandTool.GetShareMessage(unit, num);
		}
		await batchQueryManager.ExecuteBatchQueriesAsync(list);
		Console.WriteLine("=== 所有 NPC 知识库检索完毕，聊天正式开始！ ===");
		string preText = "以下是关联NPC的历史对话片段，请你在输出的时候进行参考。张三:xxxx 李四:yyyy";
		string vectorKnowledgeString = VectorKnowledgeBase.GetVectorKnowledgeString(InPutTextShare, ModMain.vectorKnowledgeCMD.vectorKnowledge.topK, preText);
		string preText2 = "以下是" + g.world.playerUnit.GetName() + "和" + unit.GetName() + "的私聊、群聊及关联NPC的历史对话片段，请你在输出的时候进行参考。" + g.world.playerUnit.GetName() + ":xxxx " + unit.GetName() + ":yyyy";
		string vectorKnowledgeString2 = VectorKnowledgeBase.GetVectorKnowledgeString(InPutText, ModMain.vectorKnowledgeCMD.vectorKnowledge.topK, preText2);
		Console.WriteLine("历史对话记录长度：" + vectorKnowledgeString2.Length);
		string text4 = vectorKnowledgeString2 + vectorKnowledgeString;
		if (text4.Length > 0)
		{
			msgTemp.Add(new Message
			{
				role = "system",
				content = "[历史对话记录]\n" + text4
			});
		}
		if (messages.Count > 1)
		{
			messages.RemoveAt(messages.Count - 1);
		}
		messages.AddRange(msgTemp);
		foreach (Message item4 in messages)
		{
			try
			{
				if (!item4.role.Equals("system"))
				{
					item4.content = CommandTool.RegexForInput(item4.content);
				}
			}
			catch (Exception ex)
			{
				Console.WriteLine(ex.ToString());
			}
		}
		List<Message> list2 = new List<Message>();
		List<Message> list3 = new List<Message>();
		foreach (Message item5 in messages)
		{
			if (item5.role.Equals("system"))
			{
				list2.Add(item5);
			}
			else
			{
				list3.Add(item5);
			}
		}
		list2.AddRange(list3);
		string text5 = $"[亲密度]{unit.GetName()}与{g.world.playerUnit.GetName()}的亲密度：{unit.data.unitData.relationData.GetIntim(g.world.playerUnit)}(默认最高300)\n";
		Message item = new Message
		{
			role = role,
			content = text5 + question
		};
		list2.Add(item);
		int num2 = 0;
		foreach (Message item6 in list2)
		{
			num2 += item6.content.Length;
			Console.WriteLine("role:" + item6.role + "  content:" + item6.content + "\n");
		}
		Console.WriteLine($"最终传入总字数：{num2}");
		try
		{
			if (ModMain.inputMode)
			{
				var requestData = new
				{
					model = modelName,
					messages = list2,
					stream = ModMain.isStream,
					max_tokens = ModMain.max_tokens,
					temperature = ModMain.temperature,
					top_p = ModMain.top_p,
					top_k = ModMain.top_k
				};
				await CallDeepSeekAPIStreaming(role, requestData, aIConfInfoItem, type);
			}
			else
			{
				var requestData2 = new
				{
					model = modelName,
					messages = list2,
					stream = ModMain.isStream
				};
				await CallDeepSeekAPIStreaming(role, requestData2, aIConfInfoItem, type);
			}
		}
		catch (Exception ex2)
		{
			Console.WriteLine("\nError: " + ex2.Message);
			Console.WriteLine("SendToDeepSeek出错");
		}
	}

	public async Task CallDeepSeekAPIStreaming(string role, object requestData, AIConfInfoItem aIConfInfoItem, int type = 0)
	{
		_ = 3;
		try
		{
			string text = ModMain.apiKey;
			string requestUri = ModMain.apiUrl;
			if (aIConfInfoItem != null)
			{
				text = aIConfInfoItem.apikey;
				requestUri = aIConfInfoItem.url;
			}
			bool isStream = CommandTool.GetToggleStream() == 1;
			textOldMsg = "";
			string rsData = "";
			string jsonStr = "";
			client.DefaultRequestHeaders.Clear();
			client.DefaultRequestHeaders.Add("Authorization", "Bearer " + text);
			client.DefaultRequestHeaders.UserAgent.ParseAdd("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 CSharp-DeepSeek-Client/1.0");
			client.DefaultRequestHeaders.Accept.Add(new MediaTypeWithQualityHeaderValue("application/json"));
			StringContent content = new StringContent(JsonConvert.SerializeObject(requestData), Encoding.UTF8, "application/json");
			HttpRequestMessage request = new HttpRequestMessage(HttpMethod.Post, requestUri)
			{
				Content = content
			};
			HttpResponseMessage obj = await client.SendAsync(request, HttpCompletionOption.ResponseHeadersRead);
			obj.EnsureSuccessStatusCode();
			using Stream stream = await obj.Content.ReadAsStreamAsync();
			using StreamReader reader = new StreamReader(stream);
			if (isStream)
			{
				while (!reader.EndOfStream)
				{
					try
					{
						string text2 = await reader.ReadLineAsync();
						if (string.IsNullOrWhiteSpace(text2))
						{
							continue;
						}
						rsData += text2;
						if (text2.StartsWith("data:"))
						{
							string text3 = text2.Substring(5).Trim();
							if (text3 == "[DONE]")
							{
								break;
							}
							try
							{
								StreamChatResponse streamChatResponse = JsonConvert.DeserializeObject<StreamChatResponse>(text3);
								string text4 = streamChatResponse?.choices?.FirstOrDefault()?.delta?.content;
								StreamChatResponse.Usage usage = streamChatResponse?.usage;
								if (!string.IsNullOrEmpty(text4))
								{
									if (isChat)
									{
										textOldMsg += text4;
									}
									else
									{
										jsonStr += text4;
									}
									isUpdate = true;
								}
								if (usage != null)
								{
									Console.WriteLine($"提示词中的 token 数量:{usage.prompt_tokens}\n" + $"生成内容中的 token 数量:{usage.completion_tokens}\n" + $"请求中使用的总 token 数量（提示词 + 生成内容）:{usage.total_tokens}\n" + $"本次请求输入中命中缓存的 token 数量:{usage.prompt_cache_hit_tokens}\n" + $"本次请求输入中未命中缓存的 token 数量:{usage.prompt_cache_miss_tokens}\n" + $"缓存命中率：{usage.prompt_cache_hit_tokens * 100 / usage.prompt_tokens}");
								}
							}
							catch (Exception ex)
							{
								Console.WriteLine(ex.Message);
								Console.WriteLine("忽略注释或非JSON数据，返回内容为:" + text2);
							}
							continue;
						}
						try
						{
							if (text2.StartsWith(":"))
							{
								continue;
							}
							ChatResponse chatResponse = JsonConvert.DeserializeObject<ChatResponse>(text2);
							string text5 = chatResponse?.choices?.FirstOrDefault()?.message?.content;
							ChatResponse.Usage usage2 = chatResponse?.usage;
							if (!string.IsNullOrEmpty(text5))
							{
								if (isChat)
								{
									textOldMsg += text5;
								}
								else
								{
									jsonStr += text5;
								}
								isUpdate = true;
							}
							if (usage2 != null)
							{
								Console.WriteLine($"提示词中的 token 数量:{usage2.prompt_tokens}\n" + $"生成内容中的 token 数量:{usage2.completion_tokens}\n" + $"请求中使用的总 token 数量（提示词 + 生成内容）:{usage2.total_tokens}\n" + $"本次请求输入中命中缓存的 token 数量:{usage2.prompt_cache_hit_tokens}\n" + $"本次请求输入中未命中缓存的 token 数量:{usage2.prompt_cache_miss_tokens}\n" + $"缓存命中率：{usage2.prompt_cache_hit_tokens * 100 / usage2.prompt_tokens}");
							}
						}
						catch (Exception ex2)
						{
							Console.WriteLine("忽略内容：" + text2);
							Console.WriteLine("忽略注释或非JSON数据");
							Console.WriteLine(ex2.ToString());
						}
					}
					catch (Exception ex3)
					{
						Console.WriteLine(ex3.Message);
					}
				}
			}
			else
			{
				string text6 = await reader.ReadToEndAsync();
				rsData += text6;
				try
				{
					ChatResponse chatResponse2 = JsonConvert.DeserializeObject<ChatResponse>(text6);
					string text7 = chatResponse2?.choices?.FirstOrDefault()?.message?.content;
					ChatResponse.Usage usage3 = chatResponse2?.usage;
					if (!string.IsNullOrEmpty(text7))
					{
						if (isChat)
						{
							textOldMsg += text7;
						}
						else
						{
							jsonStr += text7;
						}
						isUpdate = true;
					}
					if (usage3 != null)
					{
						Console.WriteLine($"提示词中的 token 数量:{usage3.prompt_tokens}\n" + $"生成内容中的 token 数量:{usage3.completion_tokens}\n" + $"请求中使用的总 token 数量（提示词 + 生成内容）:{usage3.total_tokens}\n" + $"本次请求输入中命中缓存的 token 数量:{usage3.prompt_cache_hit_tokens}\n" + $"本次请求输入中未命中缓存的 token 数量:{usage3.prompt_cache_miss_tokens}\n" + $"缓存命中率：{usage3.prompt_cache_hit_tokens * 100 / usage3.prompt_tokens}");
					}
				}
				catch (Exception ex4)
				{
					Console.WriteLine(ex4.Message);
					Console.WriteLine("忽略注释或非JSON数据，返回内容为:" + text6);
				}
			}
			if (isChat)
			{
				if (StringExtensions.IsEmpty(textOldMsg))
				{
					textOldMsg = "道友幸会，今天很不凑巧，神识传音似乎失败了。对方返回内容为：" + rsData;
				}
				else
				{
					textOldMsg = Regex.Replace(textOldMsg, "(<think>.*?</think>)|(<thinking>.*?</thinking>)", "", RegexOptions.Singleline);
					CommandTool.AddMessage(unit, "assistant", textOldMsg, 1);
				}
			}
			else if (StringExtensions.IsEmpty(jsonStr))
			{
				textOldMsg = "道友幸会，今天很不凑巧，神识传音似乎失败了。对方返回内容为：" + rsData;
			}
			else
			{
				CommandTool.AddMessage(unit, "assistant", jsonStr, 2);
				aIActionMessage = CommandTool.GetXmlText(jsonStr);
				if (aIActionMessage == null)
				{
					textOldMsg = jsonStr;
					UITipItem.AddTip("温馨提示：该条回复格式解析错误！", 2f);
				}
				else
				{
					textOldMsg = aIActionMessage.yourResponse;
				}
			}
		}
		catch (TimeoutException ex5)
		{
			textOldMsg = "道友幸会，神识通道不稳定，传音超时了，请稍后再试。错误信息：" + ex5.Message;
			Console.WriteLine("请求超时: " + ex5.Message);
		}
		catch (HttpRequestException ex6)
		{
			textOldMsg = "道友幸会，神识通道似乎出了点问题，请求错误了，错误信息：" + ex6.Message;
			Console.WriteLine("HTTP 请求错误: " + ex6.Message);
		}
		catch (Exception ex7)
		{
			textOldMsg = "道友幸会，神识传音似乎失败了，错误信息：" + ex7.Message;
			Console.WriteLine("错误: " + ex7.ToString());
			Console.WriteLine("CallDeepSeekAPIStreaming出错");
		}
		isComplete = true;
		isUpdate = true;
		state = 0;
	}
}


//==================== TYPE MOD_SSCYAI.UIChatAINewGroup.<>c ====================
// decompile failed: Could not find type definition MOD_SSCYAI.UIChatAINewGroup.<>c in type system.

//==================== TYPE MOD_SSCYAI.UIChatAINewGroup.<>c__DisplayClass54_0 ====================
// decompile failed: Could not find type definition MOD_SSCYAI.UIChatAINewGroup.<>c__DisplayClass54_0 in type system.

//==================== TYPE MOD_SSCYAI.UIChatAINewGroup.<>c__DisplayClass54_1 ====================
// decompile failed: Could not find type definition MOD_SSCYAI.UIChatAINewGroup.<>c__DisplayClass54_1 in type system.

//==================== TYPE MOD_SSCYAI.UIChatAINewGroup.<>c__DisplayClass54_2 ====================
// decompile failed: Could not find type definition MOD_SSCYAI.UIChatAINewGroup.<>c__DisplayClass54_2 in type system.

//==================== TYPE MOD_SSCYAI.UIChatAINewGroup.<>c__DisplayClass54_3 ====================
// decompile failed: Could not find type definition MOD_SSCYAI.UIChatAINewGroup.<>c__DisplayClass54_3 in type system.

//==================== TYPE MOD_SSCYAI.UIChatAINewGroup.<>c__DisplayClass56_0 ====================
// decompile failed: Could not find type definition MOD_SSCYAI.UIChatAINewGroup.<>c__DisplayClass56_0 in type system.

//==================== TYPE MOD_SSCYAI.UIChatAINewGroup.<>c__DisplayClass59_0 ====================
// decompile failed: Could not find type definition MOD_SSCYAI.UIChatAINewGroup.<>c__DisplayClass59_0 in type system.

//==================== TYPE MOD_SSCYAI.UIChatAINewGroup.<>c__DisplayClass63_0 ====================
// decompile failed: Could not find type definition MOD_SSCYAI.UIChatAINewGroup.<>c__DisplayClass63_0 in type system.

//==================== TYPE MOD_SSCYAI.UIChatAINewGroup.<>c__DisplayClass66_0 ====================
// decompile failed: Could not find type definition MOD_SSCYAI.UIChatAINewGroup.<>c__DisplayClass66_0 in type system.

//==================== TYPE MOD_SSCYAI.UIChatAINewGroup.<>c__DisplayClass71_0 ====================
// decompile failed: Could not find type definition MOD_SSCYAI.UIChatAINewGroup.<>c__DisplayClass71_0 in type system.

//==================== TYPE MOD_SSCYAI.UIChatAINewGroup.<>c__DisplayClass72_0 ====================
// decompile failed: Could not find type definition MOD_SSCYAI.UIChatAINewGroup.<>c__DisplayClass72_0 in type system.

//==================== TYPE MOD_SSCYAI.UIChatAINewGroup.<>c__DisplayClass73_0 ====================
// decompile failed: Could not find type definition MOD_SSCYAI.UIChatAINewGroup.<>c__DisplayClass73_0 in type system.

//==================== TYPE MOD_SSCYAI.UIChatAINewGroup.<>c__DisplayClass74_0 ====================
// decompile failed: Could not find type definition MOD_SSCYAI.UIChatAINewGroup.<>c__DisplayClass74_0 in type system.

//==================== TYPE MOD_SSCYAI.UIChatAINewGroup.<>c__DisplayClass74_1 ====================
// decompile failed: Could not find type definition MOD_SSCYAI.UIChatAINewGroup.<>c__DisplayClass74_1 in type system.

//==================== TYPE MOD_SSCYAI.UIChatAINewGroup.<>c__DisplayClass76_0 ====================
// decompile failed: Could not find type definition MOD_SSCYAI.UIChatAINewGroup.<>c__DisplayClass76_0 in type system.

//==================== TYPE MOD_SSCYAI.UIChatAINewGroup.<>c__DisplayClass77_0 ====================
// decompile failed: Could not find type definition MOD_SSCYAI.UIChatAINewGroup.<>c__DisplayClass77_0 in type system.

//==================== TYPE MOD_SSCYAI.UIChatAINewGroup.<CallDeepSeekAPIStreaming>d__57 ====================
// decompile failed: Could not find type definition MOD_SSCYAI.UIChatAINewGroup.<CallDeepSeekAPIStreaming>d__57 in type system.

//==================== TYPE MOD_SSCYAI.UIChatAINewGroup.<SendToDeepSeek>d__56 ====================
// decompile failed: Could not find type definition MOD_SSCYAI.UIChatAINewGroup.<SendToDeepSeek>d__56 in type system.

//==================== TYPE MOD_SSCYAI.UIChatAINew.ResponseFormat ====================
// decompile failed: Could not find type definition MOD_SSCYAI.UIChatAINew.ResponseFormat in type system.

//==================== TYPE MOD_SSCYAI.UIChatAINew.RequestData ====================
// decompile failed: Could not find type definition MOD_SSCYAI.UIChatAINew.RequestData in type system.

//==================== TYPE MOD_SSCYAI.UIChatAINew.<>c ====================
// decompile failed: Could not find type definition MOD_SSCYAI.UIChatAINew.<>c in type system.

//==================== TYPE MOD_SSCYAI.UIChatAINew.<>c__DisplayClass45_0 ====================
// decompile failed: Could not find type definition MOD_SSCYAI.UIChatAINew.<>c__DisplayClass45_0 in type system.

//==================== TYPE MOD_SSCYAI.UIChatAINew.<>c__DisplayClass45_1 ====================
// decompile failed: Could not find type definition MOD_SSCYAI.UIChatAINew.<>c__DisplayClass45_1 in type system.

//==================== TYPE MOD_SSCYAI.UIChatAINew.<>c__DisplayClass50_0 ====================
// decompile failed: Could not find type definition MOD_SSCYAI.UIChatAINew.<>c__DisplayClass50_0 in type system.

//==================== TYPE MOD_SSCYAI.UIChatAINew.<>c__DisplayClass50_1 ====================
// decompile failed: Could not find type definition MOD_SSCYAI.UIChatAINew.<>c__DisplayClass50_1 in type system.

//==================== TYPE MOD_SSCYAI.UIChatAINew.<>c__DisplayClass53_0 ====================
// decompile failed: Could not find type definition MOD_SSCYAI.UIChatAINew.<>c__DisplayClass53_0 in type system.

//==================== TYPE MOD_SSCYAI.UIChatAINew.<>c__DisplayClass56_0 ====================
// decompile failed: Could not find type definition MOD_SSCYAI.UIChatAINew.<>c__DisplayClass56_0 in type system.

//==================== TYPE MOD_SSCYAI.UIChatAINew.<>c__DisplayClass57_0 ====================
// decompile failed: Could not find type definition MOD_SSCYAI.UIChatAINew.<>c__DisplayClass57_0 in type system.

//==================== TYPE MOD_SSCYAI.UIChatAINew.<>c__DisplayClass57_1 ====================
// decompile failed: Could not find type definition MOD_SSCYAI.UIChatAINew.<>c__DisplayClass57_1 in type system.

//==================== TYPE MOD_SSCYAI.UIChatAINew.<>c__DisplayClass62_0 ====================
// decompile failed: Could not find type definition MOD_SSCYAI.UIChatAINew.<>c__DisplayClass62_0 in type system.

//==================== TYPE MOD_SSCYAI.UIChatAINew.<>c__DisplayClass64_0 ====================
// decompile failed: Could not find type definition MOD_SSCYAI.UIChatAINew.<>c__DisplayClass64_0 in type system.

//==================== TYPE MOD_SSCYAI.UIChatAINew.<>c__DisplayClass66_0 ====================
// decompile failed: Could not find type definition MOD_SSCYAI.UIChatAINew.<>c__DisplayClass66_0 in type system.

//==================== TYPE MOD_SSCYAI.UIChatAINew.<CallDeepSeekAPIStreaming>d__69 ====================
// decompile failed: Could not find type definition MOD_SSCYAI.UIChatAINew.<CallDeepSeekAPIStreaming>d__69 in type system.

//==================== TYPE MOD_SSCYAI.UIChatAINew.<SendToDeepSeek>d__66 ====================
// decompile failed: Could not find type definition MOD_SSCYAI.UIChatAINew.<SendToDeepSeek>d__66 in type system.

//==================== TYPE MOD_SSCYAI.UIChatAINewGroup.<>c__DisplayClass54_3.<<SenndMessage>b__6>d ====================
// decompile failed: Could not find type definition MOD_SSCYAI.UIChatAINewGroup.<>c__DisplayClass54_3.<<SenndMessage>b__6>d in type system.

//==================== TYPE MOD_SSCYAI.UIChatAINewGroup.<>c__DisplayClass76_0.<<InitData>b__27>d ====================
// decompile failed: Could not find type definition MOD_SSCYAI.UIChatAINewGroup.<>c__DisplayClass76_0.<<InitData>b__27>d in type system.

//==================== TYPE MOD_SSCYAI.UIChatAINew.<>c__DisplayClass45_0.<<SendMessage>b__0>d ====================
// decompile failed: Could not find type definition MOD_SSCYAI.UIChatAINew.<>c__DisplayClass45_0.<<SendMessage>b__0>d in type system.

//==================== TYPE MOD_SSCYAI.UIChatAINew.<>c__DisplayClass50_1.<<UpdateChatMessageList>b__10>d ====================
// decompile failed: Could not find type definition MOD_SSCYAI.UIChatAINew.<>c__DisplayClass50_1.<<UpdateChatMessageList>b__10>d in type system.

//==================== TYPE MOD_SSCYAI.UIChatAINew.<>c__DisplayClass57_1.<<Update>b__6>d ====================
// decompile failed: Could not find type definition MOD_SSCYAI.UIChatAINew.<>c__DisplayClass57_1.<<Update>b__6>d in type system.

//==================== TYPE MOD_SSCYAI.UIChatAINew.<>c__DisplayClass62_0.<<InitData>b__27>d ====================
// decompile failed: Could not find type definition MOD_SSCYAI.UIChatAINew.<>c__DisplayClass62_0.<<InitData>b__27>d in type system.

