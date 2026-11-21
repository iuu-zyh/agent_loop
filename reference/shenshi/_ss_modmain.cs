//==================== TYPE MOD_SSCYAI.ModMain ====================
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Web.WebPages;
using EBattleTypeData;
using EGameTypeData;
using HarmonyLib;
using Il2CppSystem;
using Il2CppSystem.Collections.Generic;
using MOD_SSCYAI.FeatureCMD;
using MOD_SSCYAI.WorldDomainManager;
using Newtonsoft.Json;
using UnhollowerBaseLib;
using UnhollowerRuntimeLib;
using UnityEngine;
using UnityEngine.Events;
using UnityEngine.UI;

namespace MOD_SSCYAI;

public class ModMain
{
	private static TimerCoroutine corUpdate;

	private static TimerCoroutine corThread;

	public static int openUIType = 0;

	public static bool isDebug = false;

	public static int mod_ID = 1945667223;

	private static Harmony harmony;

	private static Action<ETypeData> openUIEndCall;

	private static Action<ETypeData> intoWorldCall;

	private static Action<ETypeData> saveDataCall;

	private static Action<ETypeData> worldRunStart;

	private static Action<ETypeData> worldRunEnd;

	private static Action<ETypeData> battleStartCall;

	private static Action<ETypeData> runNPCAICall;

	private static Action<ETypeData> battleEndCall;

	private static Action<ETypeData> worldAddDayCall;

	private static Action<ETypeData> onTaskComplete;

	private static Action<ETypeData> onUnitDie;

	public static string apiKey = "";

	public static string apiUrl = "https://api.siliconflow.cn/v1/chat/completions";

	public static string modelName = "deepseek-ai/DeepSeek-V3";

	public static string baseSet = "[基础背景设定]\n你现在身处在鬼谷八荒世界中，正在扮演一名NPC修仙者，用词必须使用鬼谷八荒中存在的事物。\n鬼谷八荒境界提升顺序（从弱到强）：炼气<筑基<结晶<金丹<具灵<元婴<化神<悟道<羽化<登仙。\n鬼谷八荒地图区域解锁顺序：白源区，永宁州，雷泽，华封洲，十万大山，云陌洲，永恒冰原，慕仙洲，迷途荒漠，赤幽洲，天元山\n[边界情况]\n当我问你是不是AI的时，请时刻记住你的修仙者身份信息，绝对不能暴露AI身份。\n[输出规则]\n你的回答内容长度在200个汉字左右，语言风格融合了古典文学的诗意韵味和修仙日常的诙谐幽默，融入个性化的角色情感和语言风格。\n你的回答内容一定要符合你的人物身份设定、性格、关系、情感和亲密度等。\n你的回复中不需要描述行为、心理活动或者表情类的旁白性质的词语，注重排版和断句。\n禁止出现现代、科技、科幻等超过古代人认知的产物与词汇。禁止输出思考过程，直接输出最终结果。\n";

	public static string prompt = baseSet;

	public static int max_tokens = 1024;

	public static float temperature = 1f;

	public static int top_k = 50;

	public static float top_p = 0.8f;

	public static float frequency_penalty = 0f;

	public static bool isStream = false;

	public static bool inputMode = false;

	public static bool isShowAll = false;

	public static Assembly MOD_Tc85Yz = null;

	public static Dictionary<string, List<Message>> Messages = new Dictionary<string, List<Message>>();

	public static Dictionary<string, List<Message>> ActionMessages = new Dictionary<string, List<Message>>();

	public static List<string> shareUnit = new List<string>();

	public static List<string> letterUnitList = new List<string>();

	public static List<WorldUnitBase> unitDies = new List<WorldUnitBase>();

	public static List<WorldUnitBase> unitActions = new List<WorldUnitBase>();

	public static int isLoadProp = 0;

	public static int isLoadLuck = 0;

	public static int isLoadSkill = 0;

	public static string propListStr = "";

	public static string luckListStr = "";

	public static string skillListStr = "";

	public static string apiKeySpeek = "";

	public static string apiUrlSpeek = "https://api.siliconflow.cn/v1/audio/speech";

	public static string modelNameSpeek = "FunAudioLLM/CosyVoice2-0.5B";

	public static string ManVoiceName = "benjamin";

	public static string WoManVoiceName = "claire";

	public static WorldUnitBase letterUnit = null;

	public static AIPrompt aiPrompt = new AIPrompt();

	public static ChatGroup chatGroup = new ChatGroup();

	public static AIPromptCommand aIPromptCommand = new AIPromptCommand();

	public static bool ExitAutoSave = false;

	public static UnitSet UnitSet = new UnitSet();

	public static bool isSend = false;

	public static Dictionary<string, string> unitLog = new Dictionary<string, string>();

	public static Dictionary<string, List<Tuple<string, string>>> FindList = new Dictionary<string, List<Tuple<string, string>>>();

	public static List<WorldUnitBase> unitTeams = new List<WorldUnitBase>();

	public static ConfInofFile confInofFile;

	public static UnitBaseInfo unitBaseInfo;

	public static int runMonth = 0;

	public static int AttackUpConut = 0;

	public static bool runAction = false;

	public static bool isAction = true;

	public static int startDay = 0;

	public static List<ConfRoleMarkItem> confRoleMarkItems = new List<ConfRoleMarkItem>();

	public static List<ConfArtifactShapeCommonItem> confArtifactShapeCommonItems = new List<ConfArtifactShapeCommonItem>();

	public static List<ConfBattleUnitLevelItem> confBattleUnitLevelItems = new List<ConfBattleUnitLevelItem>();

	public static List<ConfBattleUnitAttrItem> confBattleUnitAttrItems = new List<ConfBattleUnitAttrItem>();

	public static List<ConfRoleGradeItem> confRoleGradeItems = new List<ConfRoleGradeItem>();

	public Dictionary<string, Action<ETypeData>> UnitBeKillAction;

	public static List<string> tips = new List<string>();

	public static bool isSortEnd = false;

	public static int MaxCount = 70;

	public static List<Dictionary<string, int>> unitList;

	public static List<Dictionary<string, int>> areaUnitList;

	public static List<string> fileName;

	public static int fileIndex = 0;

	public static RankConf rankConf = new RankConf();

	public static ModelFile modelFile = new ModelFile();

	public static bool isJudge = false;

	public static PreText preText = new PreText();

	public static AIConfInfo aIConfInfo = new AIConfInfo();

	public static RegexCMD regexCMD = new RegexCMD();

	public static WorldLogCMD worldLogCMD = new WorldLogCMD();

	public static UnitLogCMD unitLogCMD = new UnitLogCMD();

	private static readonly Random _random = new Random(Guid.NewGuid().GetHashCode());

	public static VectorKnowledgeCMD vectorKnowledgeCMD = new VectorKnowledgeCMD();

	public static int FightType = 0;

	public static ModMainExt modMain = new ModMainExt();

	[DllImport("winmm.dll", CharSet = CharSet.Auto)]
	public static extern int mciSendString(string lpstrCommand, StringBuilder lpstrReturnString, int uReturnLength, IntPtr hwndCallback);

	public void Init()
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
			harmony = new Harmony("MOD_SSCYAI");
		}
		harmony.PatchAll(Assembly.GetExecutingAssembly());
		corUpdate = g.timer.Frame(Action.op_Implicit((Action)OnUpdate), 1, true);
		corThread = g.timer.Time(Action.op_Implicit((Action)OnThread), 0f, true);
		openUIEndCall = Action<ETypeData>.op_Implicit((Action<ETypeData>)OnOpenUIEnd);
		g.events.On(EGameType.OpenUIEnd, openUIEndCall, 0, false);
		intoWorldCall = Action<ETypeData>.op_Implicit((Action<ETypeData>)OnIntoWorld);
		g.events.On(EGameType.IntoWorld, intoWorldCall, 0, false);
		saveDataCall = Action<ETypeData>.op_Implicit((Action<ETypeData>)OnSaveData);
		g.events.On(EGameType.SaveData, saveDataCall, 0, false);
		worldRunStart = Action<ETypeData>.op_Implicit((Action<ETypeData>)OnWorldRunStart);
		g.events.On(EGameType.WorldRunStart, worldRunStart, 0, false);
		worldRunEnd = Action<ETypeData>.op_Implicit((Action<ETypeData>)OnWorldRunEnd);
		g.events.On(EGameType.WorldRunEnd, worldRunEnd, 0, false);
		battleStartCall = Action<ETypeData>.op_Implicit((Action<ETypeData>)OnBattleStart);
		g.events.On(EBattleType.BattleStart, battleStartCall, 0, false);
		runNPCAICall = Action<ETypeData>.op_Implicit((Action<ETypeData>)OnRunNPCAI);
		g.events.On(EGameType.RunNPCAI, runNPCAICall, 0, false);
		battleEndCall = Action<ETypeData>.op_Implicit((Action<ETypeData>)OnBattleEnd);
		g.events.On(EBattleType.BattleEnd, battleEndCall, 0, false);
		worldAddDayCall = Action<ETypeData>.op_Implicit((Action<ETypeData>)OnWorldAddDay);
		g.events.On(EGameType.WorldAddDay, worldAddDayCall, 0, false);
		onTaskComplete = Action<ETypeData>.op_Implicit((Action<ETypeData>)OnTaskComplete);
		g.events.On(EGameType.TaskComplete, onTaskComplete, 0, false);
		onUnitDie = Action<ETypeData>.op_Implicit((Action<ETypeData>)OnUnitDie);
		g.events.On(EBattleType.UnitDie, onUnitDie, 0, false);
		confInofFile = new ConfInofFile();
		unitBaseInfo = new UnitBaseInfo();
		UnitBeKillAction = new Dictionary<string, Action<ETypeData>>();
		confInofFile.InitConf();
		confInofFile.SaveConf();
		EffectConfigTool.InitConf();
		unitList = new List<Dictionary<string, int>>(MaxCount);
		areaUnitList = new List<Dictionary<string, int>>(MaxCount);
		fileName = new List<string> { "juanzhoudi", "kaijubg" };
		isSortEnd = false;
		isJudge = false;
		modelFile.Init();
		preText.Init();
		aIConfInfo.Init();
		regexCMD.Init();
		Console.WriteLine("神识传音模组已正常加载");
		modMain.Init();
		MOD_SSCYAI.WorldDomainManager.WorldDomainManager.Instance.Init();
	}

	public void Destroy()
	{
		try
		{
			InitSetting(ChangeMode: false);
			unitTeams?.Clear();
			unitLog?.Clear();
			unitDies?.Clear();
			unitActions?.Clear();
			g.timer.Stop(corUpdate);
			g.timer.Stop(corThread);
			if (g.events.Off(EGameType.OpenUIEnd, openUIEndCall))
			{
				Console.WriteLine("openUIEndCall监听关闭成功");
			}
			if (g.events.Off(EGameType.IntoWorld, intoWorldCall))
			{
				Console.WriteLine("intoWorldCall监听关闭成功");
			}
			if (g.events.Off(EGameType.SaveData, saveDataCall))
			{
				Console.WriteLine("saveDataCall监听关闭成功");
			}
			if (g.events.Off(EGameType.WorldRunStart, worldRunStart))
			{
				Console.WriteLine("worldRunStart监听关闭成功");
			}
			if (g.events.Off(EGameType.WorldRunEnd, worldRunEnd))
			{
				Console.WriteLine("worldRunEnd监听关闭成功");
			}
			if (g.events.Off(EBattleType.BattleStart, battleStartCall))
			{
				Console.WriteLine("battleStartCall监听关闭成功");
			}
			if (g.events.Off(EGameType.RunNPCAI, runNPCAICall))
			{
				Console.WriteLine("runNPCAICall监听关闭成功");
			}
			if (g.events.Off(EBattleType.BattleEnd, battleEndCall))
			{
				Console.WriteLine("battleEndCall监听关闭成功");
			}
			if (g.events.Off(EGameType.WorldAddDay, worldAddDayCall))
			{
				Console.WriteLine("worldAddDayCall监听关闭成功");
			}
			if (g.events.Off(EGameType.TaskComplete, onTaskComplete))
			{
				Console.WriteLine("onTaskComplete监听关闭成功");
			}
			g.events.Off(EBattleType.UnitDie, onUnitDie);
			if (UnitBeKillAction != null)
			{
				foreach (KeyValuePair<string, Action<ETypeData>> item in UnitBeKillAction)
				{
					if (g.events.Off(item.Key, Action<ETypeData>.op_Implicit(item.Value)))
					{
						Console.WriteLine(item.Key + "的UnitBeKillAction监听关闭成功");
					}
				}
				UnitBeKillAction.Clear();
				UnitBeKillAction = null;
			}
			unitBaseInfo.allUnit?.Clear();
			unitBaseInfo.allUnit = null;
			unitBaseInfo.curUnit = null;
			if (confInofFile.confInof.isAutoSaveThisFile)
			{
				confInofFile.SaveConf();
			}
			isAction = false;
			runAction = false;
			unitList = null;
			Console.WriteLine("神识传音模组已卸载");
			g.timer.Time(Action.op_Implicit((Action)delegate
			{
				try
				{
					if (!((Object)(object)g.ui.GetUI<UIMapMain>(UIType.MapMain) == (Object)null) && unitList == null)
					{
						Console.WriteLine("哈哈哈，神识传音模组已被强行卸载！");
						g.ui.OpenUI<UICheckPopup>(UIType.CheckPopup).InitData("提示", "哈哈哈，恭喜你遇到小彩蛋，懂的都懂，AI系统监测到神识传音模组已被某个二哈非正常卸载，是否现在重启？", 2, Action.op_Implicit((Action)delegate
						{
							Init();
							MapMainAddBtn();
							Console.WriteLine("AI系统将神识传音模组重启成功！");
							g.ui.OpenUI<UITextInfo>(UIType.TextInfo).InitData("提示", "你真机智，神识传音模组已重启成功！出发吧，我在天元山等着你！", "", (Action)null);
						}), Action.op_Implicit((Action)delegate
						{
							Console.WriteLine("很遗憾，你主动放弃了神识传音模组！有缘再见！");
							g.ui.OpenUI<UITextInfo>(UIType.TextInfo).InitData("提示", "很遗憾，你主动放弃了神识传音模组！我们有缘再见！", "", (Action)null);
						}));
					}
				}
				catch (Exception ex2)
				{
					Console.WriteLine(ex2.ToString());
				}
			}), 3f, false);
		}
		catch (Exception ex)
		{
			Console.WriteLine(ex.ToString());
		}
		modMain.Destroy();
	}

	public void OnThread()
	{
		ThreadSafeQueue.ProcessAll();
	}

	private void OnUpdate()
	{
		//IL_028a: Unknown result type (might be due to invalid IL or missing references)
		//IL_0294: Expected O, but got Unknown
		//IL_00a0: Unknown result type (might be due to invalid IL or missing references)
		//IL_00a5: Unknown result type (might be due to invalid IL or missing references)
		//IL_00ab: Expected O, but got Unknown
		if (unitTeams != null)
		{
			foreach (WorldUnitBase unitTeam in unitTeams)
			{
				new AIUnitSet();
				if (CommandTool.GetAIUnitSet(unitTeam).autoMove == 1 && (unitTeam.data.unitData.pointX != g.world.playerUnit.data.unitData.pointX || unitTeam.data.unitData.pointY != g.world.playerUnit.data.unitData.pointY))
				{
					UnitActionMoveNPC val = new UnitActionMoveNPC(g.world.playerUnit.data.unitData.GetPoint());
					unitTeam.CreateAction((UnitActionBase)(object)val, false);
				}
			}
		}
		if (isSend)
		{
			bool flag = (Object)(object)g.ui.GetUI(UIType.MapMain) != (Object)null && (Object)(object)g.ui.GetUI(UIType.BattleInfo) == (Object)null;
			if ((!g.world.battle.isBattle && !DramaTool.IsOpenDrama((Il2CppReferenceArray<UITypeBase>)null) && !g.scene.isLoadScene) & flag)
			{
				for (int i = 0; i < unitActions.Count; i++)
				{
					WorldUnitBase val2 = unitActions[i];
					if (val2.data.unitData.objData.ContainsKey("ChatMessage") && !DramaTool.IsOpenDrama((Il2CppReferenceArray<UITypeBase>)null))
					{
						ShowActionMsg(val2);
						unitActions.RemoveAt(i);
						break;
					}
				}
				SendLetter();
				isSend = false;
			}
		}
		if (((DataObjectData)((DataBase<Object>)(object)g.data.dataObj).data).ContainsKey("WorldLogAiData"))
		{
			string text = ((DataObjectData)((DataBase<Object>)(object)g.data.dataObj).data).GetString("WorldLogAiData");
			((DataObjectData)((DataBase<Object>)(object)g.data.dataObj).data).DelString("WorldLogAiData");
			WorldLogItem worldLogItem = JsonConvert.DeserializeObject<WorldLogItem>(text);
			if (worldLogCMD.worldLog.logList.ContainsKey(worldLogItem.month))
			{
				worldLogCMD.worldLog.logList[worldLogItem.month] = worldLogItem;
			}
			else
			{
				worldLogCMD.worldLog.logList.Add(worldLogItem.month, worldLogItem);
			}
			Console.WriteLine("世界意识数据已更新！");
			UITipItem.AddTip("主世界剧情自动推演完成！", 3f);
		}
		if (Input.GetKey((KeyCode)278))
		{
			ClassInjector.RegisterTypeInIl2Cpp<UIAIMenu>();
			((Component)g.ui.OpenUI(new UITypeBase("UIAIMenu", (UILayer)0))).gameObject.AddComponent<UIAIMenu>().InitData(g.world.playerUnit);
		}
	}

	public void OnUnitDie(ETypeData data)
	{
		UnitDie val = ((Il2CppObjectBase)data).Cast<UnitDie>();
		if (val == null)
		{
			return;
		}
		try
		{
			if (val.hitData != null && (val.hitData.isDissolve || val.hitData.isEvade || val.hitData.isShieldDef || val.hitData.isShowZero))
			{
				return;
			}
			WorldUnitBase val2 = BigFightExtensions.AsWorldUnit(val.unit);
			if (val2 != null)
			{
				if (FightType == 1)
				{
					GetFightResult(val2, ref UIFightOne.matchUnits, ref UIFightOne.selIndex);
				}
				else if (FightType == 2)
				{
					GetFightResult(val2, ref UIFight.matchUnits, ref UIFight.selIndex);
				}
				else if (FightType == 3)
				{
					GetFightResult(val2, ref UIFightDiy.matchUnits, ref UIFightDiy.selIndex);
				}
			}
		}
		catch (Exception ex)
		{
			Console.WriteLine(ex.ToString());
		}
	}

	public void JudgeFightWinner(ref List<MatchUnit> matchUnits, ref int selIndex)
	{
		//IL_0123: Unknown result type (might be due to invalid IL or missing references)
		//IL_0129: Expected O, but got Unknown
		//IL_0129: Unknown result type (might be due to invalid IL or missing references)
		//IL_0130: Expected O, but got Unknown
		if (StringExtensions.IsEmpty(matchUnits[selIndex].winner))
		{
			WorldUnitBase unit = g.world.unit.GetUnit(matchUnits[selIndex].unitLeft, true);
			WorldUnitBase unit2 = g.world.unit.GetUnit(matchUnits[selIndex].unitRight, true);
			WorldUnitBase unit3 = UnitActionRoleBattle.UnitBattle(unit, unit2, (SchoolPostType)0);
			matchUnits[selIndex].winner = unit3.GetUnitID();
			if (matchUnits[selIndex].unitLeft.Equals(matchUnits[selIndex].winner))
			{
				unit.AddValue("tianming", 1);
				unit2.AddValue("tianming", -1);
				matchUnits[selIndex].loser = matchUnits[selIndex].unitRight;
			}
			else if (matchUnits[selIndex].unitRight.Equals(matchUnits[selIndex].winner))
			{
				unit2.AddValue("tianming", 1);
				unit.AddValue("tianming", -1);
				matchUnits[selIndex].loser = matchUnits[selIndex].unitLeft;
			}
			LogItemData val = new LogItemData();
			LogItemData val2 = new LogItemData();
			if (matchUnits[selIndex].unitLeft.Equals(matchUnits[selIndex].winner))
			{
				string text = UITextHretTool.HretUnit(unit, "");
				string text2 = UITextHretTool.HretUnit(unit2, "");
				string[] array = new string[2] { text, text2 };
				val.AddLogMain("eventLogTotal999022", Il2CppStringArray.op_Implicit(array));
				g.world.unitLog.AddLogData(unit, val);
				string[] array2 = new string[2] { text2, text };
				val2.AddLogMain("eventLogTotal999023", Il2CppStringArray.op_Implicit(array2));
				g.world.unitLog.AddLogData(unit2, val2);
			}
			else
			{
				string text3 = UITextHretTool.HretUnit(unit, "");
				string text4 = UITextHretTool.HretUnit(unit2, "");
				string[] array3 = new string[2] { text4, text3 };
				val.AddLogMain("eventLogTotal999022", Il2CppStringArray.op_Implicit(array3));
				g.world.unitLog.AddLogData(unit2, val);
				string[] array4 = new string[2] { text3, text4 };
				val2.AddLogMain("eventLogTotal999023", Il2CppStringArray.op_Implicit(array4));
				g.world.unitLog.AddLogData(unit, val2);
			}
			UITipItem.AddTip(unit3.GetName() + "争锋成功，天命值+1", 3f);
		}
	}

	public void GetFightResult(WorldUnitBase worldUnitBase, ref List<MatchUnit> matchUnits, ref int selIndex)
	{
		//IL_00ac: Unknown result type (might be due to invalid IL or missing references)
		//IL_00b2: Expected O, but got Unknown
		//IL_00b2: Unknown result type (might be due to invalid IL or missing references)
		//IL_00b8: Expected O, but got Unknown
		//IL_0247: Unknown result type (might be due to invalid IL or missing references)
		//IL_024e: Expected O, but got Unknown
		//IL_024e: Unknown result type (might be due to invalid IL or missing references)
		//IL_0255: Expected O, but got Unknown
		try
		{
			if (worldUnitBase.GetUnitID().Equals(matchUnits[selIndex].unitRight))
			{
				WorldUnitBase unit = g.world.unit.GetUnit(matchUnits[selIndex].unitLeft, true);
				WorldUnitBase unit2 = g.world.unit.GetUnit(matchUnits[selIndex].unitRight, true);
				matchUnits[selIndex].winner = matchUnits[selIndex].unitLeft;
				matchUnits[selIndex].loser = matchUnits[selIndex].unitRight;
				unit.AddValue("tianming", 1);
				unit2.AddValue("tianming", -1);
				LogItemData val = new LogItemData();
				LogItemData val2 = new LogItemData();
				string text = UITextHretTool.HretUnit(unit, "");
				string text2 = UITextHretTool.HretUnit(unit2, "");
				string[] array = new string[3]
				{
					text,
					text2,
					MartialUtil.getAreaName(unit.data.unitData.pointGridData.areaBaseID)
				};
				val.AddLogMain("eventLogTotal999022", Il2CppStringArray.op_Implicit(array));
				g.world.unitLog.AddLogData(unit, val);
				string[] array2 = new string[3]
				{
					text2,
					text,
					MartialUtil.getAreaName(unit.data.unitData.pointGridData.areaBaseID)
				};
				val2.AddLogMain("eventLogTotal999023", Il2CppStringArray.op_Implicit(array2));
				g.world.unitLog.AddLogData(unit2, val2);
				UITipItem.AddTip(unit.GetName() + "争锋成功，天命值+1", 3f);
			}
			else if (worldUnitBase.GetUnitID().Equals(matchUnits[selIndex].unitLeft))
			{
				WorldUnitBase unit3 = g.world.unit.GetUnit(matchUnits[selIndex].unitLeft, true);
				WorldUnitBase unit4 = g.world.unit.GetUnit(matchUnits[selIndex].unitRight, true);
				matchUnits[selIndex].winner = matchUnits[selIndex].unitRight;
				matchUnits[selIndex].loser = matchUnits[selIndex].unitLeft;
				unit4.AddValue("tianming", 1);
				unit3.AddValue("tianming", -1);
				LogItemData val3 = new LogItemData();
				LogItemData val4 = new LogItemData();
				string text3 = UITextHretTool.HretUnit(unit3, "");
				string text4 = UITextHretTool.HretUnit(unit4, "");
				string[] array3 = new string[3]
				{
					text4,
					text3,
					MartialUtil.getAreaName(unit4.data.unitData.pointGridData.areaBaseID)
				};
				val3.AddLogMain("eventLogTotal999022", Il2CppStringArray.op_Implicit(array3));
				g.world.unitLog.AddLogData(unit4, val3);
				string[] array4 = new string[3]
				{
					text3,
					text4,
					MartialUtil.getAreaName(unit4.data.unitData.pointGridData.areaBaseID)
				};
				val4.AddLogMain("eventLogTotal999023", Il2CppStringArray.op_Implicit(array4));
				g.world.unitLog.AddLogData(unit3, val4);
				UITipItem.AddTip(unit4.GetName() + "争锋成功，天命值+1", 3f);
			}
		}
		catch (Exception ex)
		{
			Console.WriteLine(ex.ToString());
		}
	}

	public void PlayerInfoUICMD()
	{
		//IL_0097: Unknown result type (might be due to invalid IL or missing references)
		//IL_0149: Unknown result type (might be due to invalid IL or missing references)
		UIPlayerInfo ui = g.ui.GetUI<UIPlayerInfo>(UIType.PlayerInfo);
		if (!((Object)(object)ui != (Object)null))
		{
			return;
		}
		string text = "btn_save";
		if ((Object)(object)((Property)ui.uiProperty).goGroupRoot.transform.Find(text) != (Object)null)
		{
			return;
		}
		Button obj = Object.Instantiate<Button>(((Skill)ui.uiSkill).btnMartialTrain, ((Property)ui.uiProperty).goGroupRoot.transform);
		((Object)obj).name = text;
		((Component)obj).transform.localPosition = new Vector3(-600f, -50f);
		Text componentInChildren = ((Component)obj).GetComponentInChildren<Text>();
		Button componentInChildren2 = ((Component)obj).GetComponentInChildren<Button>();
		((UnityEventBase)componentInChildren2.onClick).RemoveAllListeners();
		componentInChildren.text = "收藏捏脸";
		((UnityEvent)componentInChildren2.onClick).AddListener(UnityAction.op_Implicit((Action)delegate
		{
			g.ui.OpenUI<UICheckPopup>(UIType.CheckPopup).InitData("提示", "是否确认收藏对应的捏脸数据？", 2, Action.op_Implicit((Action)delegate
			{
				ModelList modelList = new ModelList
				{
					name = ui.unit.data.unitData.propertyData.GetName(),
					time = DateTime.Now.ToString(),
					tips = "",
					portraitModel = g.world.playerUnit.data.unitData.propertyData.modelData
				};
				modelFile.ModelList.Add(modelList);
				modelFile.ModelListAdd.Add(modelList);
				modelFile.SaveConf();
				Console.WriteLine(modelList.name + "捏脸数据保存成功。");
			}), (Action)null);
		}));
		string text2 = "btn_open";
		if (!((Object)(object)((Property)ui.uiProperty).goGroupRoot.transform.Find(text2) != (Object)null))
		{
			Button obj2 = Object.Instantiate<Button>(((Skill)ui.uiSkill).btnMartialTrain, ((Property)ui.uiProperty).goGroupRoot.transform);
			((Object)obj2).name = text2;
			((Component)obj2).transform.localPosition = new Vector3(-600f, -100f);
			Text componentInChildren3 = ((Component)obj2).GetComponentInChildren<Text>();
			Button componentInChildren4 = ((Component)obj2).GetComponentInChildren<Button>();
			((UnityEventBase)componentInChildren4.onClick).RemoveAllListeners();
			componentInChildren3.text = "捏脸数据";
			((UnityEvent)componentInChildren4.onClick).AddListener(UnityAction.op_Implicit((Action)delegate
			{
				//IL_0010: Unknown result type (might be due to invalid IL or missing references)
				//IL_001a: Expected O, but got Unknown
				ClassInjector.RegisterTypeInIl2Cpp<UIModelPro>();
				UIModelPro uIModelPro = ((Component)g.ui.OpenUI(new UITypeBase("UIModelPro", (UILayer)0))).gameObject.AddComponent<UIModelPro>();
				uIModelPro.mode = 1;
				uIModelPro.InitData();
			}));
		}
	}

	public void NPCInfoUICMD()
	{
		//IL_0071: Unknown result type (might be due to invalid IL or missing references)
		//IL_010f: Unknown result type (might be due to invalid IL or missing references)
		UINPCInfo ui = g.ui.GetUI<UINPCInfo>(UIType.NPCInfo);
		if (!((Object)(object)ui != (Object)null))
		{
			return;
		}
		GameObject obj = Object.Instantiate<GameObject>(((UnitInfo)ui.uiUnitInfo).goButton3);
		Transform transform = ((Component)ui).transform;
		obj.transform.SetParent(transform, false);
		obj.SetActive(true);
		obj.transform.localPosition = new Vector3(-513f, -150f);
		((Object)obj).name = "xsmsx_btn_save";
		obj.layer = int.MaxValue;
		obj.GetComponentInChildren<Text>().text = "收藏捏脸";
		Button componentInChildren = obj.GetComponentInChildren<Button>();
		((UnityEventBase)componentInChildren.onClick).RemoveAllListeners();
		((UnityEvent)componentInChildren.onClick).AddListener(UnityAction.op_Implicit((Action)delegate
		{
			g.ui.OpenUI<UICheckPopup>(UIType.CheckPopup).InitData("提示", "是否确认收藏对应的捏脸数据？", 2, Action.op_Implicit((Action)delegate
			{
				ModelList modelList = new ModelList
				{
					name = ui.unit.data.unitData.propertyData.GetName(),
					time = DateTime.Now.ToString(),
					tips = "",
					portraitModel = ui.unit.data.unitData.propertyData.modelData
				};
				modelFile.ModelList.Add(modelList);
				modelFile.ModelListAdd.Add(modelList);
				modelFile.SaveConf();
				Console.WriteLine(modelList.name + "捏脸数据保存成功。");
			}), (Action)null);
		}));
		Console.WriteLine("NPC界面收藏按钮添加完成");
		GameObject obj2 = Object.Instantiate<GameObject>(((UnitInfo)ui.uiUnitInfo).goButton3);
		obj2.transform.SetParent(transform, false);
		obj2.SetActive(true);
		obj2.transform.localPosition = new Vector3(-443f, -150f);
		((Object)obj2).name = "xsmsx_btn_open";
		obj2.layer = int.MaxValue;
		obj2.GetComponentInChildren<Text>().text = "捏脸数据";
		Button componentInChildren2 = obj2.GetComponentInChildren<Button>();
		((UnityEventBase)componentInChildren2.onClick).RemoveAllListeners();
		((UnityEvent)componentInChildren2.onClick).AddListener(UnityAction.op_Implicit((Action)delegate
		{
			//IL_0010: Unknown result type (might be due to invalid IL or missing references)
			//IL_001a: Expected O, but got Unknown
			ClassInjector.RegisterTypeInIl2Cpp<UIModelPro>();
			UIModelPro uIModelPro = ((Component)g.ui.OpenUI(new UITypeBase("UIModelPro", (UILayer)0))).gameObject.AddComponent<UIModelPro>();
			uIModelPro.mode = 2;
			uIModelPro.InitData();
		}));
		Console.WriteLine("NPC界面捏脸数据按钮添加完成");
	}

	public void CreatePlayerUICMD()
	{
		//IL_00c8: Unknown result type (might be due to invalid IL or missing references)
		//IL_0133: Unknown result type (might be due to invalid IL or missing references)
		UICreatePlayer uI = g.ui.GetUI<UICreatePlayer>(UIType.CreatePlayer);
		if (!((Object)(object)uI != (Object)null))
		{
			return;
		}
		UICreatePlayerFacade __instance = uI.uiFacade;
		if (__instance == null)
		{
			Console.WriteLine("ui.uiFacade为空。");
		}
		else if ((Object)(object)((Facade)__instance).goGroupRoot == (Object)null)
		{
			Console.WriteLine("ui.uiFacade.goGroupRoot为空。");
		}
		else
		{
			if ((Object)(object)((Facade)__instance).goGroupRoot.transform.Find("save") != (Object)null)
			{
				return;
			}
			Button obj = Object.Instantiate<Button>(((UICreatePlayerBase)uI).btnOK, ((Facade)__instance).goGroupRoot.transform);
			((Object)obj).name = "save";
			((Component)obj).GetComponentInChildren<Text>().text = "收藏捏脸";
			((Component)obj).transform.localPosition = new Vector3(-185f, -325f);
			((UnityEvent)obj.onClick).AddListener(UnityAction.op_Implicit((Action)delegate
			{
				g.ui.OpenUI<UICheckPopup>(UIType.CheckPopup).InitData("提示", "是否确认收藏对应的捏脸数据？", 2, Action.op_Implicit((Action)delegate
				{
					ModelList modelList = new ModelList();
					if (__instance.portraitModel.data.sex == 1)
					{
						modelList.name = ((Il2CppArrayBase<string>)(object)__instance.manName)[0] + ((Il2CppArrayBase<string>)(object)__instance.manName)[1];
					}
					else
					{
						modelList.name = ((Il2CppArrayBase<string>)(object)__instance.womanName)[0] + ((Il2CppArrayBase<string>)(object)__instance.womanName)[1];
					}
					modelList.time = DateTime.Now.ToString();
					modelList.tips = "";
					modelList.portraitModel = __instance.portraitModel.data.Clone();
					modelFile.ModelList.Add(modelList);
					modelFile.ModelListAdd.Add(modelList);
					modelFile.SaveConf();
				}), (Action)null);
			}));
			Button obj2 = Object.Instantiate<Button>(((UICreatePlayerBase)uI).btnOK, ((Facade)__instance).goGroupRoot.transform);
			((Object)obj2).name = "open";
			((Component)obj2).GetComponentInChildren<Text>().text = "捏脸数据";
			((Component)obj2).transform.localPosition = new Vector3(-25f, -325f);
			((UnityEvent)obj2.onClick).AddListener(UnityAction.op_Implicit((Action)delegate
			{
				//IL_0010: Unknown result type (might be due to invalid IL or missing references)
				//IL_001a: Expected O, but got Unknown
				ClassInjector.RegisterTypeInIl2Cpp<UIModelPro>();
				UIModelPro uIModelPro = ((Component)g.ui.OpenUI(new UITypeBase("UIModelPro", (UILayer)0))).gameObject.AddComponent<UIModelPro>();
				uIModelPro.mode = 0;
				uIModelPro.InitData();
			}));
		}
	}

	public void ModDressUICMD()
	{
		//IL_0197: Unknown result type (might be due to invalid IL or missing references)
		//IL_01b1: Unknown result type (might be due to invalid IL or missing references)
		//IL_01c1: Unknown result type (might be due to invalid IL or missing references)
		//IL_00a5: Unknown result type (might be due to invalid IL or missing references)
		//IL_00bf: Unknown result type (might be due to invalid IL or missing references)
		//IL_00cf: Unknown result type (might be due to invalid IL or missing references)
		//IL_0274: Unknown result type (might be due to invalid IL or missing references)
		//IL_028e: Unknown result type (might be due to invalid IL or missing references)
		//IL_029e: Unknown result type (might be due to invalid IL or missing references)
		UIModDress ui = g.ui.GetUI<UIModDress>(UIType.ModDress);
		if (!((Object)(object)ui != (Object)null))
		{
			return;
		}
		if (!isJudge)
		{
			string text = "btn_open";
			if ((Object)(object)((Component)((UIModDressBase)ui).btnOK).transform.parent.Find(text) != (Object)null)
			{
				return;
			}
			Button obj = Object.Instantiate<Button>(((UIModDressBase)ui).btnOK, ((Component)((UIModDressBase)ui).btnOK).transform.parent);
			((Object)obj).name = text;
			((Component)obj).transform.localPosition = new Vector3(((Component)((UIModDressBase)ui).btnOK).transform.localPosition.x, ((Component)((UIModDressBase)ui).btnOK).transform.localPosition.y + 150f);
			Text componentInChildren = ((Component)obj).GetComponentInChildren<Text>();
			Button componentInChildren2 = ((Component)obj).GetComponentInChildren<Button>();
			((UnityEventBase)componentInChildren2.onClick).RemoveAllListeners();
			componentInChildren.text = "捏脸数据";
			((UnityEvent)componentInChildren2.onClick).AddListener(UnityAction.op_Implicit((Action)delegate
			{
				//IL_000b: Unknown result type (might be due to invalid IL or missing references)
				//IL_0015: Expected O, but got Unknown
				//IL_002d: Unknown result type (might be due to invalid IL or missing references)
				//IL_0037: Expected O, but got Unknown
				if ((Object)(object)g.ui.GetUI(new UITypeBase("UIModelPro", (UILayer)0)) == (Object)null)
				{
					ClassInjector.RegisterTypeInIl2Cpp<UIModelPro>();
					UIModelPro uIModelPro = ((Component)g.ui.OpenUI(new UITypeBase("UIModelPro", (UILayer)0))).gameObject.AddComponent<UIModelPro>();
					uIModelPro.mode = 3;
					uIModelPro.InitData();
				}
				else
				{
					g.ui.OpenUI<UITextInfo>(UIType.TextInfo).InitData("提示", "您已经打开了捏脸数据库界面，请勿重新点击。", "", (Action)null);
				}
			}));
		}
		string text2 = "btn_save";
		if ((Object)(object)((Component)((UIModDressBase)ui).btnOK).transform.parent.Find(text2) != (Object)null)
		{
			return;
		}
		Button obj2 = Object.Instantiate<Button>(((UIModDressBase)ui).btnOK, ((Component)((UIModDressBase)ui).btnOK).transform.parent);
		((Object)obj2).name = text2;
		((Component)obj2).transform.localPosition = new Vector3(((Component)((UIModDressBase)ui).btnOK).transform.localPosition.x, ((Component)((UIModDressBase)ui).btnOK).transform.localPosition.y + 100f);
		Text componentInChildren3 = ((Component)obj2).GetComponentInChildren<Text>();
		Button componentInChildren4 = ((Component)obj2).GetComponentInChildren<Button>();
		((UnityEventBase)componentInChildren4.onClick).RemoveAllListeners();
		componentInChildren3.text = "捏脸收藏";
		((UnityEvent)componentInChildren4.onClick).AddListener(UnityAction.op_Implicit((Action)delegate
		{
			g.ui.OpenUI<UICheckPopup>(UIType.CheckPopup).InitData("提示", "是否确认收藏对应的捏脸数据？", 2, Action.op_Implicit((Action)delegate
			{
				//IL_000c: Unknown result type (might be due to invalid IL or missing references)
				//IL_0012: Expected O, but got Unknown
				string modelID = ui.GetModelID();
				ModDataValueString val = new ModDataValueString();
				val.SetString(modelID);
				ModelList modelList = new ModelList
				{
					name = "无名-" + DateTime.Now.TimeOfDay.ToString(),
					time = DateTime.Now.ToString(),
					tips = "",
					portraitModel = CommandTool.GetPortraitModelData(val)
				};
				modelFile.ModelList.Add(modelList);
				modelFile.ModelListAdd.Add(modelList);
				modelFile.SaveConf();
				Console.WriteLine(modelList.name + "捏脸数据保存成功。");
				isJudge = false;
			}), (Action)null);
		}));
		string text3 = "btn_close";
		if ((Object)(object)((Component)((UIModDressBase)ui).btnOK).transform.parent.Find(text3) != (Object)null)
		{
			return;
		}
		Button obj3 = Object.Instantiate<Button>(((UIModDressBase)ui).btnOK, ((Component)((UIModDressBase)ui).btnOK).transform.parent);
		((Object)obj3).name = text3;
		((Component)obj3).transform.localPosition = new Vector3(((Component)((UIModDressBase)ui).btnOK).transform.localPosition.x, ((Component)((UIModDressBase)ui).btnOK).transform.localPosition.y + 50f);
		Text componentInChildren5 = ((Component)obj3).GetComponentInChildren<Text>();
		Button componentInChildren6 = ((Component)obj3).GetComponentInChildren<Button>();
		((UnityEventBase)componentInChildren6.onClick).RemoveAllListeners();
		componentInChildren5.text = "编辑取消";
		((UnityEvent)componentInChildren6.onClick).AddListener(UnityAction.op_Implicit((Action)delegate
		{
			//IL_000b: Unknown result type (might be due to invalid IL or missing references)
			//IL_0015: Expected O, but got Unknown
			UIBase uI = g.ui.GetUI(new UITypeBase("UIModelPro", (UILayer)0));
			if ((Object)(object)uI != (Object)null)
			{
				UIModelPro component = ((Component)uI).gameObject.GetComponent<UIModelPro>();
				if ((Object)(object)component != (Object)null)
				{
					component.UpData();
					Console.WriteLine("退出编辑-开始刷新。");
				}
			}
			isJudge = false;
			g.ui.CloseUI(UIType.ModDress, false);
		}));
	}

	public static void SstValue(bool ChangeMode = true)
	{
		//IL_08b2: Unknown result type (might be due to invalid IL or missing references)
		//IL_08bc: Expected O, but got Unknown
		//IL_08d2: Unknown result type (might be due to invalid IL or missing references)
		//IL_08dc: Expected O, but got Unknown
		//IL_04a1: Unknown result type (might be due to invalid IL or missing references)
		//IL_04ab: Expected O, but got Unknown
		//IL_04c1: Unknown result type (might be due to invalid IL or missing references)
		//IL_04cb: Expected O, but got Unknown
		if (ChangeMode)
		{
			List<ConfBattleUnitLevelItem> allConfList = ((ConfBattleUnitLevelBase)g.conf.battleUnitLevel)._allConfList;
			for (int i = 0; i < allConfList.Count; i++)
			{
				allConfList[i].atk = confBattleUnitLevelItems[i].atk * confInofFile.confInof.confDiySet.UnitAtk;
				allConfList[i].def = confBattleUnitLevelItems[i].def * confInofFile.confInof.confDiySet.UnitDef;
				allConfList[i].hp = confBattleUnitLevelItems[i].hp * confInofFile.confInof.confDiySet.UnitHp;
				allConfList[i].pfr = confBattleUnitLevelItems[i].pfr * confInofFile.confInof.confDiySet.Pmfr;
				allConfList[i].mfr = confBattleUnitLevelItems[i].mfr * confInofFile.confInof.confDiySet.Pmfr;
				allConfList[i].crit = confBattleUnitLevelItems[i].crit * confInofFile.confInof.confDiySet.Crit;
				allConfList[i].critV = confBattleUnitLevelItems[i].critV * confInofFile.confInof.confDiySet.Crit;
				allConfList[i].guard = confBattleUnitLevelItems[i].guard * confInofFile.confInof.confDiySet.Guard;
				allConfList[i].guardV = confBattleUnitLevelItems[i].guardV * confInofFile.confInof.confDiySet.Guard;
				allConfList[i].skillDmg = confBattleUnitLevelItems[i].skillDmg * confInofFile.confInof.confDiySet.SkillDmg;
			}
			List<ConfBattleUnitAttrItem> allConfList2 = ((ConfBattleUnitAttrBase)g.conf.battleUnitAttr)._allConfList;
			for (int j = 0; j < allConfList2.Count; j++)
			{
				ConfBattleUnitBaseItem item = ((ConfBattleUnitBaseBase)g.conf.battleUnitBase).GetItem(allConfList2[j].baseID);
				if (item != null)
				{
					switch (item.type)
					{
					case 1:
						allConfList2[j].growPercent = (int)((float)confBattleUnitAttrItems[j].growPercent * confInofFile.confInof.confDiySet.Type1);
						break;
					case 2:
						allConfList2[j].growPercent = (int)((float)confBattleUnitAttrItems[j].growPercent * confInofFile.confInof.confDiySet.Type2);
						break;
					case 3:
						allConfList2[j].growPercent = (int)((float)confBattleUnitAttrItems[j].growPercent * confInofFile.confInof.confDiySet.Type3);
						break;
					}
				}
			}
			List<ConfRoleGradeItem> allConfList3 = ((ConfRoleGradeBase)g.conf.roleGrade)._allConfList;
			for (int k = 0; k < allConfList3.Count; k++)
			{
				allConfList3[k].exp = (int)((float)confRoleGradeItems[k].exp * confInofFile.confInof.confDiySet.GradeExp);
			}
			if (confInofFile.confInof.confDiySet.isMarkNoCost)
			{
				List<ConfRoleMarkItem> allConfList4 = ((ConfRoleMarkBase)g.conf.roleMark)._allConfList;
				for (int l = 0; l < allConfList4.Count; l++)
				{
					allConfList4[l].needSp = 0;
					allConfList4[l].cost = 0;
				}
			}
			if (confInofFile.confInof.confDiySet.isEquipNoSp)
			{
				List<ConfArtifactShapeCommonItem> allConfList5 = ((ConfArtifactShapeCommonBase)g.conf.artifactShapeCommon)._allConfList;
				for (int m = 0; m < allConfList5.Count; m++)
				{
					allConfList5[m].equipSp = 0;
				}
			}
			if (confInofFile.confInof.confDiySet.isNoSpCost)
			{
				List<ConfArtifactShapeCommonItem> allConfList6 = ((ConfArtifactShapeCommonBase)g.conf.artifactShapeCommon)._allConfList;
				for (int n = 0; n < allConfList6.Count; n++)
				{
					allConfList6[n].spCost = 0;
				}
			}
			if (confInofFile.confInof.isSoulDevourSworduP)
			{
				((ConfSoulDevourSwordParamerBase)g.conf.soulDevourSwordParamer)._swordDevourInterval = new ConfSoulDevourSwordParamerItem(17, "swordDevourInterval", "999999");
				((ConfSoulDevourSwordParamerBase)g.conf.soulDevourSwordParamer)._swordTargetHideRange = new ConfSoulDevourSwordParamerItem(24, "swordTargetHideRange", "1");
				List<ConfSoulDevourSwordDevourItem> allConfList7 = ((ConfSoulDevourSwordDevourBase)g.conf.soulDevourSwordDevour)._allConfList;
				for (int num = 0; num < allConfList7.Count; num++)
				{
					allConfList7[num].devourCD = 0;
				}
			}
			SetNpcLcukValue();
			return;
		}
		for (int num2 = 0; num2 < ((ConfRoleMarkBase)g.conf.roleMark)._allConfList.Count; num2++)
		{
			if (num2 < confRoleMarkItems.Count)
			{
				((ConfRoleMarkBase)g.conf.roleMark)._allConfList[num2].cost = confRoleMarkItems[num2].cost;
				((ConfRoleMarkBase)g.conf.roleMark)._allConfList[num2].needSp = confRoleMarkItems[num2].needSp;
			}
		}
		for (int num3 = 0; num3 < ((ConfArtifactShapeCommonBase)g.conf.artifactShapeCommon)._allConfList.Count; num3++)
		{
			if (num3 < confArtifactShapeCommonItems.Count)
			{
				((ConfArtifactShapeCommonBase)g.conf.artifactShapeCommon)._allConfList[num3].equipSp = confArtifactShapeCommonItems[num3].equipSp;
				((ConfArtifactShapeCommonBase)g.conf.artifactShapeCommon)._allConfList[num3].spCost = confArtifactShapeCommonItems[num3].spCost;
			}
		}
		List<ConfBattleUnitLevelItem> allConfList8 = ((ConfBattleUnitLevelBase)g.conf.battleUnitLevel)._allConfList;
		for (int num4 = 0; num4 < allConfList8.Count; num4++)
		{
			allConfList8[num4].atk = confBattleUnitLevelItems[num4].atk;
			allConfList8[num4].def = confBattleUnitLevelItems[num4].def;
			allConfList8[num4].hp = confBattleUnitLevelItems[num4].hp;
			allConfList8[num4].pfr = confBattleUnitLevelItems[num4].pfr;
			allConfList8[num4].mfr = confBattleUnitLevelItems[num4].mfr;
			allConfList8[num4].crit = confBattleUnitLevelItems[num4].crit;
			allConfList8[num4].critV = confBattleUnitLevelItems[num4].critV;
			allConfList8[num4].guard = confBattleUnitLevelItems[num4].guard;
			allConfList8[num4].guardV = confBattleUnitLevelItems[num4].guardV;
			allConfList8[num4].skillDmg = confBattleUnitLevelItems[num4].skillDmg;
		}
		List<ConfBattleUnitAttrItem> allConfList9 = ((ConfBattleUnitAttrBase)g.conf.battleUnitAttr)._allConfList;
		for (int num5 = 0; num5 < allConfList9.Count; num5++)
		{
			ConfBattleUnitBaseItem item2 = ((ConfBattleUnitBaseBase)g.conf.battleUnitBase).GetItem(allConfList9[num5].baseID);
			if (item2 != null)
			{
				switch (item2.type)
				{
				case 1:
					allConfList9[num5].growPercent = confBattleUnitAttrItems[num5].growPercent;
					break;
				case 2:
					allConfList9[num5].growPercent = confBattleUnitAttrItems[num5].growPercent;
					break;
				case 3:
					allConfList9[num5].growPercent = confBattleUnitAttrItems[num5].growPercent;
					break;
				}
			}
		}
		List<ConfRoleGradeItem> allConfList10 = ((ConfRoleGradeBase)g.conf.roleGrade)._allConfList;
		for (int num6 = 0; num6 < allConfList10.Count; num6++)
		{
			allConfList10[num6].exp = confRoleGradeItems[num6].exp;
		}
		((ConfSoulDevourSwordParamerBase)g.conf.soulDevourSwordParamer)._swordDevourInterval = new ConfSoulDevourSwordParamerItem(17, "swordDevourInterval", "24");
		((ConfSoulDevourSwordParamerBase)g.conf.soulDevourSwordParamer)._swordTargetHideRange = new ConfSoulDevourSwordParamerItem(24, "swordTargetHideRange", "5");
		List<ConfSoulDevourSwordDevourItem> allConfList11 = ((ConfSoulDevourSwordDevourBase)g.conf.soulDevourSwordDevour)._allConfList;
		for (int num7 = 0; num7 < allConfList11.Count; num7++)
		{
			allConfList11[num7].devourCD = 3;
		}
	}

	public void InitSetting(bool ChangeMode = true)
	{
		confRoleMarkItems.Clear();
		confArtifactShapeCommonItems.Clear();
		confBattleUnitLevelItems.Clear();
		confBattleUnitAttrItems.Clear();
		confRoleGradeItems.Clear();
		Enumerator<ConfRoleMarkItem> enumerator = ((ConfRoleMarkBase)g.conf.roleMark)._allConfList.GetEnumerator();
		while (enumerator.MoveNext())
		{
			ConfRoleMarkItem current = enumerator.Current;
			confRoleMarkItems.Add(current);
		}
		Enumerator<ConfArtifactShapeCommonItem> enumerator2 = ((ConfArtifactShapeCommonBase)g.conf.artifactShapeCommon)._allConfList.GetEnumerator();
		while (enumerator2.MoveNext())
		{
			ConfArtifactShapeCommonItem current2 = enumerator2.Current;
			confArtifactShapeCommonItems.Add(current2);
		}
		List<ConfBattleUnitLevelItem> allConfList = ((ConfBattleUnitLevelBase)g.conf.battleUnitLevel)._allConfList;
		for (int i = 0; i < allConfList.Count; i++)
		{
			confBattleUnitLevelItems.Add(allConfList[i]);
		}
		List<ConfBattleUnitAttrItem> allConfList2 = ((ConfBattleUnitAttrBase)g.conf.battleUnitAttr)._allConfList;
		for (int j = 0; j < allConfList2.Count; j++)
		{
			confBattleUnitAttrItems.Add(allConfList2[j]);
		}
		List<ConfRoleGradeItem> allConfList3 = ((ConfRoleGradeBase)g.conf.roleGrade)._allConfList;
		for (int k = 0; k < allConfList3.Count; k++)
		{
			confRoleGradeItems.Add(allConfList3[k]);
		}
	}

	public static void SetNpcLcukValue()
	{
		List<int> list = new List<int>
		{
			confInofFile.confInof.confDiySet.npcAtk,
			confInofFile.confInof.confDiySet.npcDef,
			confInofFile.confInof.confDiySet.npcMaxHp,
			confInofFile.confInof.confDiySet.npcMaxMp,
			confInofFile.confInof.confDiySet.npcMaxSp,
			confInofFile.confInof.confDiySet.npcCrit,
			confInofFile.confInof.confDiySet.npcGuard,
			confInofFile.confInof.confDiySet.npcCritV,
			confInofFile.confInof.confDiySet.npcGuardV,
			confInofFile.confInof.confDiySet.npcBasMgAll,
			confInofFile.confInof.confDiySet.npcPfr,
			confInofFile.confInof.confDiySet.npcMfr
		};
		for (int i = 0; i < list.Count; i++)
		{
			int num = mod_ID + i + 1;
			ConfBattleSkillValueItem item = ((ConfBattleSkillValueBase)g.conf.battleSkillValue).GetItem(num);
			if (item != null)
			{
				string value = (item.value9 = (item.value8 = (item.value7 = (item.value6 = (item.value5 = (item.value4 = (item.value3 = (item.value2 = (item.value1 = list[i].ToString())))))))));
				item.value10 = value;
			}
		}
		ConfRoleCreateFeatureItem item2 = ((ConfRoleCreateFeatureBase)g.conf.roleCreateFeature).GetItem(mod_ID + 2);
		if (item2 != null)
		{
			if (confInofFile.confInof.confDiySet.showLuck)
			{
				item2.conceal = 0;
			}
			else
			{
				item2.conceal = 1;
			}
		}
	}

	public void OnWorldUnitDie(ETypeData e)
	{
		//IL_00cf: Unknown result type (might be due to invalid IL or missing references)
		//IL_00d5: Expected O, but got Unknown
		if (!confInofFile.confInof.confDiySet.isOpenUnitDieTips)
		{
			return;
		}
		WorldUnitDie val = ((Il2CppObjectBase)e).Cast<WorldUnitDie>();
		if (isDebug)
		{
			Console.WriteLine("单位死亡行动判定");
		}
		if (Math.Max(g.world.playerUnit.data.unitData.relationData.GetIntim(val.unit), val.unit.data.unitData.relationData.GetIntim(g.world.playerUnit)) < 180 || g.world.battle.isBattle)
		{
			return;
		}
		DieData unitDie = g.data.unitDie.GetUnitDie(val.unit.data.unitData.unitID);
		if (unitDie != null)
		{
			int num = mod_ID + 10;
			UICustomDramaDyn val2 = new UICustomDramaDyn(num);
			((UICustomDramaBase)val2).dramaData.unitLeft = val.unit;
			((UICustomDramaBase)val2).dramaData.isLookUnit = true;
			if (StringExtensions.IsEmpty(unitDie.killUnitID))
			{
				List<string> killedByUnits = val.unit.data.unitData.aiData.data.killedByUnits;
				bool flag = false;
				WorldUnitBase val3 = null;
				if (killedByUnits != null)
				{
					Enumerator<string> enumerator = killedByUnits.GetEnumerator();
					while (enumerator.MoveNext())
					{
						string current = enumerator.Current;
						val3 = g.world.unit.GetUnit(current, true);
						if (val3 != null)
						{
							flag = true;
							break;
						}
					}
				}
				if (flag)
				{
					g.data.world.marksUnitID.Add(val3.data.unitData.unitID);
					((UICustomDramaBase)val2).dramaData.unitRight = val3;
					((UICustomDramaBase)val2).dramaData.dialogueText[num] = "你的好友（" + unitDie.GetName() + "）已被（" + val3.data.unitData.propertyData.GetName() + ")击杀，死亡地点：" + unitDie.point + "，享年：" + unitDie.age / 12 + "岁。现已将仇人在小地图进行标记，你可择机前往复仇。";
					if (isDebug)
					{
						Console.WriteLine("复仇NPC标记成功，" + val3.data.unitData.propertyData.GetName() + "的位置为：（" + val3.data.unitData.pointX + "," + val3.data.unitData.pointY + ")\n");
					}
				}
				else
				{
					if (isDebug)
					{
						Console.WriteLine("单位自然死亡。");
					}
					((UICustomDramaBase)val2).dramaData.dialogueText[num] = "你的好友（" + unitDie.GetName() + "）已死亡，死亡地点：" + unitDie.point + "，享年：" + unitDie.age / 12 + "岁。死亡原因未知。";
				}
			}
			else
			{
				if (isDebug)
				{
					Console.WriteLine("单位属于他杀。对方ID:" + unitDie.killUnitID);
				}
				WorldUnitBase unit = g.world.unit.GetUnit(unitDie.killUnitID, true);
				if (unit != null)
				{
					g.data.world.marksUnitID.Add(unitDie.killUnitID);
					((UICustomDramaBase)val2).dramaData.unitRight = unit;
					((UICustomDramaBase)val2).dramaData.dialogueText[num] = "你的好友（" + unitDie.GetName() + "）已被（" + unit.data.unitData.propertyData.GetName() + ")击杀，死亡地点：" + unitDie.point + "，享年：" + unitDie.age / 12 + "岁。现已将仇人在小地图进行标记，你可择机前往复仇。";
					if (isDebug)
					{
						Console.WriteLine("复仇NPC标记成功，" + unit.data.unitData.propertyData.GetName() + "的位置为：（" + unit.data.unitData.pointX + "," + unit.data.unitData.pointY + ")\n");
					}
				}
			}
			val2.OpenUI();
		}
		else if (isDebug)
		{
			Console.WriteLine("单位死亡信息为空");
		}
	}

	public void OnRunNPCAI(ETypeData e)
	{
		//IL_00a0: Unknown result type (might be due to invalid IL or missing references)
		//IL_00aa: Expected O, but got Unknown
		try
		{
			RunNPCAI val = ((Il2CppObjectBase)e).Cast<RunNPCAI>();
			if (val == null || val.curIndex != 0)
			{
				return;
			}
			if (unitBaseInfo.allUnit.Count < g.world.unit.GetUnits(true).Count - 1)
			{
				unitBaseInfo.InitUnit();
			}
			if (confInofFile.confInof.runAIDate == 0)
			{
				unitBaseInfo.isOver = false;
				unitBaseInfo.isPause = false;
				unitBaseInfo.unitActionToPlayCount = 0;
				if (!runAction)
				{
					Console.WriteLine("月行动模式，NPC行动解锁。");
					runAction = true;
					MultiThreadTask multiThreadTask = new MultiThreadTask(false);
					multiThreadTask.AddCall(Action.op_Implicit((Action)OnComandUnit));
					Action action = delegate
					{
						multiThreadTask.Stop();
						Console.WriteLine("行动结束！" + multiThreadTask.isStop);
					};
					multiThreadTask.Run(true, Action.op_Implicit(action));
				}
			}
			else
			{
				g.world.run.AddDay(0, true);
			}
		}
		catch (Exception ex)
		{
			Console.WriteLine(ex.ToString());
		}
	}

	public static void OnSort()
	{
		InitAndSort(g.world.unit.GetUnits(true));
	}

	public static void InitAndSort(List<WorldUnitBase> units)
	{
		//IL_0106: Unknown result type (might be due to invalid IL or missing references)
		//IL_0110: Expected O, but got Unknown
		try
		{
			List<WorldUnitBase> list = new List<WorldUnitBase>();
			rankConf.Init();
			if (units == null || units.Count < 4)
			{
				return;
			}
			Console.WriteLine("开始重新排名。");
			unitList.Clear();
			areaUnitList.Clear();
			for (int i = 0; i < MaxCount; i++)
			{
				unitList.Add(new Dictionary<string, int>());
			}
			for (int j = 0; j < MaxCount; j++)
			{
				areaUnitList.Add(new Dictionary<string, int>());
			}
			List<string> allGoodRelationUnitID = g.world.playerUnit.data.unitData.relationData.GetAllGoodRelationUnitID(true, false);
			for (int k = 0; k < units.Count; k++)
			{
				WorldUnitBase val = units[k];
				if (allGoodRelationUnitID.Contains(val.data.unitData.unitID))
				{
					val.AddValue("tianming", _random.Next(0, val.data.dynUnitData.GetGrade()));
					list.Add(val);
				}
				else if (UnitConditionTool.Condition("superHero_0_1", new UnitConditionData(val, (WorldUnitBase)null)))
				{
					if (_random.Next(0, 100) < val.data.dynUnitData.GetGrade() * 2)
					{
						val.AddValue("tianming", _random.Next(0, val.data.dynUnitData.GetGrade()));
						list.Add(val);
					}
				}
				else if (_random.Next(0, 100) < val.data.dynUnitData.GetGrade())
				{
					val.AddValue("tianming", _random.Next(0, val.data.dynUnitData.GetGrade()));
					list.Add(val);
				}
				if (!unitList[0].ContainsKey(val.data.unitData.unitID))
				{
					unitList[0].Add(val.data.unitData.unitID, UnitPower.TotalPower(val.data));
				}
				if (!unitList[1].ContainsKey(val.data.unitData.unitID))
				{
					unitList[1].Add(val.data.unitData.unitID, ((DataProps)val.data.unitData.propData).GetPropsNum(10001));
				}
				if (!unitList[2].ContainsKey(val.data.unitData.unitID))
				{
					unitList[2].Add(val.data.unitData.unitID, val.data.unitData.propertyData.reputation);
				}
				if (!unitList[3].ContainsKey(val.data.unitData.unitID))
				{
					unitList[3].Add(val.data.unitData.unitID, val.data.unitData.propertyData.beauty);
				}
				if (!unitList[4].ContainsKey(val.data.unitData.unitID))
				{
					unitList[4].Add(val.data.unitData.unitID, val.GetValue("tianming"));
				}
				int grade = val.data.dynUnitData.GetGrade();
				if (!unitList[10 + grade].ContainsKey(val.data.unitData.unitID))
				{
					unitList[10 + grade].Add(val.data.unitData.unitID, UnitPower.TotalPower(val.data));
				}
				if (!unitList[20 + grade].ContainsKey(val.data.unitData.unitID))
				{
					unitList[20 + grade].Add(val.data.unitData.unitID, ((DataProps)val.data.unitData.propData).GetPropsNum(10001));
				}
				if (!unitList[30 + grade].ContainsKey(val.data.unitData.unitID))
				{
					unitList[30 + grade].Add(val.data.unitData.unitID, val.data.unitData.propertyData.reputation);
				}
				if (!unitList[40 + grade].ContainsKey(val.data.unitData.unitID))
				{
					unitList[40 + grade].Add(val.data.unitData.unitID, val.data.unitData.propertyData.beauty);
				}
				if (!unitList[50 + grade].ContainsKey(val.data.unitData.unitID))
				{
					unitList[50 + grade].Add(val.data.unitData.unitID, val.GetValue("tianming"));
				}
				if (!areaUnitList[val.data.unitData.pointGridData.areaBaseID].ContainsKey(val.data.unitData.unitID))
				{
					areaUnitList[val.data.unitData.pointGridData.areaBaseID].Add(val.data.unitData.unitID, UnitPower.TotalPower(val.data));
				}
				if (!areaUnitList[10 + val.data.unitData.pointGridData.areaBaseID].ContainsKey(val.data.unitData.unitID))
				{
					areaUnitList[10 + val.data.unitData.pointGridData.areaBaseID].Add(val.data.unitData.unitID, ((DataProps)val.data.unitData.propData).GetPropsNum(10001));
				}
				if (!areaUnitList[20 + val.data.unitData.pointGridData.areaBaseID].ContainsKey(val.data.unitData.unitID))
				{
					areaUnitList[20 + val.data.unitData.pointGridData.areaBaseID].Add(val.data.unitData.unitID, val.data.unitData.propertyData.reputation);
				}
				if (!areaUnitList[30 + val.data.unitData.pointGridData.areaBaseID].ContainsKey(val.data.unitData.unitID))
				{
					areaUnitList[30 + val.data.unitData.pointGridData.areaBaseID].Add(val.data.unitData.unitID, val.data.unitData.propertyData.beauty);
				}
				if (!areaUnitList[40 + val.data.unitData.pointGridData.areaBaseID].ContainsKey(val.data.unitData.unitID))
				{
					areaUnitList[40 + val.data.unitData.pointGridData.areaBaseID].Add(val.data.unitData.unitID, val.GetValue("tianming"));
				}
			}
			for (int l = 0; l < MaxCount; l++)
			{
				unitList[l] = unitList[l].OrderByDescending((KeyValuePair<string, int> o) => o.Value).ToDictionary((KeyValuePair<string, int> p) => p.Key, (KeyValuePair<string, int> o) => o.Value);
				areaUnitList[l] = areaUnitList[l].OrderByDescending((KeyValuePair<string, int> o) => o.Value).ToDictionary((KeyValuePair<string, int> p) => p.Key, (KeyValuePair<string, int> o) => o.Value);
			}
			list = list.OrderBy((WorldUnitBase o) => o.GetValue("tianming")).ToList();
			if (list.Count > 100)
			{
				list.RemoveRange(100, list.Count - 100);
			}
			foreach (WorldUnitBase item5 in list)
			{
				item5.AddLuck(mod_ID + 3, 1);
			}
			if (unitList.Count >= 4)
			{
				for (int num = 0; num < 10; num++)
				{
					try
					{
						int luckId = mod_ID + 11 + num;
						if (num < rankConf.rankInfo.Ranks1.Count)
						{
							ConfRoleCreateFeatureItem item = ((ConfRoleCreateFeatureBase)g.conf.roleCreateFeature).GetItem(rankConf.rankInfo.Ranks1[num].LuckID);
							if (item != null)
							{
								luckId = ((ConfBaseItem)item).id;
							}
						}
						if (num < unitList[0].Count)
						{
							WorldUnitBase unit = g.world.unit.GetUnit(unitList[0].Keys.ElementAt(num), true);
							if (unit != null)
							{
								unit.AddLuck(luckId, 1);
								unit.AddLuck(mod_ID + 3, 1);
								unit.AddValue("tianming", _random.Next(0, unit.data.dynUnitData.GetGrade()));
							}
						}
						int luckId2 = mod_ID + 21 + num;
						if (num < rankConf.rankInfo.Ranks2.Count)
						{
							ConfRoleCreateFeatureItem item2 = ((ConfRoleCreateFeatureBase)g.conf.roleCreateFeature).GetItem(rankConf.rankInfo.Ranks2[num].LuckID);
							if (item2 != null)
							{
								luckId2 = ((ConfBaseItem)item2).id;
							}
						}
						if (num < unitList[1].Count)
						{
							WorldUnitBase unit2 = g.world.unit.GetUnit(unitList[1].Keys.ElementAt(num), true);
							if (unit2 != null)
							{
								unit2.AddLuck(luckId2, 1);
								unit2.AddLuck(mod_ID + 3, 1);
								unit2.AddValue("tianming", _random.Next(0, unit2.data.dynUnitData.GetGrade()));
							}
						}
						int luckId3 = mod_ID + 31 + num;
						if (num < rankConf.rankInfo.Ranks3.Count)
						{
							ConfRoleCreateFeatureItem item3 = ((ConfRoleCreateFeatureBase)g.conf.roleCreateFeature).GetItem(rankConf.rankInfo.Ranks3[num].LuckID);
							if (item3 != null)
							{
								luckId3 = ((ConfBaseItem)item3).id;
							}
						}
						if (num < unitList[2].Count)
						{
							WorldUnitBase unit3 = g.world.unit.GetUnit(unitList[2].Keys.ElementAt(num), true);
							if (unit3 != null)
							{
								unit3.AddLuck(luckId3, 1);
								unit3.AddLuck(mod_ID + 3, 1);
								unit3.AddValue("tianming", _random.Next(0, unit3.data.dynUnitData.GetGrade()));
							}
						}
						int luckId4 = mod_ID + 41 + num;
						if (num < rankConf.rankInfo.Ranks4.Count)
						{
							ConfRoleCreateFeatureItem item4 = ((ConfRoleCreateFeatureBase)g.conf.roleCreateFeature).GetItem(rankConf.rankInfo.Ranks4[num].LuckID);
							if (item4 != null)
							{
								luckId4 = ((ConfBaseItem)item4).id;
							}
						}
						if (num < unitList[3].Count)
						{
							WorldUnitBase unit4 = g.world.unit.GetUnit(unitList[3].Keys.ElementAt(num), true);
							if (unit4 != null)
							{
								unit4.AddLuck(luckId4, 1);
								unit4.AddLuck(mod_ID + 3, 1);
								unit4.AddValue("tianming", _random.Next(0, unit4.data.dynUnitData.GetGrade()));
							}
						}
					}
					catch (Exception ex)
					{
						Console.WriteLine(ex.ToString());
					}
				}
			}
			Console.WriteLine("排名数据已完成更新。");
			isSortEnd = true;
		}
		catch (Exception ex2)
		{
			Console.WriteLine(ex2.ToString());
		}
	}

	public void OnComandUnit()
	{
		while (unitBaseInfo != null && confInofFile != null)
		{
			if (confInofFile.confInof.isCloseNpcAllAction)
			{
				runAction = false;
				break;
			}
			isAction = !unitBaseInfo.isOver && !unitBaseInfo.isPause;
			if (g.world.battle.isBattle)
			{
				Console.WriteLine("正在战斗，停止行动");
				Thread.Sleep(5000);
			}
			else
			{
				try
				{
					if (isAction)
					{
						if (AllUnitActionFun())
						{
							Thread.Sleep(confInofFile.confInof.unitActionFreq);
						}
						else
						{
							unitBaseInfo.isOver = true;
						}
						goto IL_00c0;
					}
				}
				catch
				{
					Console.WriteLine("isOpenDrame和isOpenNPCInfo判定失败。");
				}
				runAction = false;
			}
			goto IL_00c0;
			IL_00c0:
			if (!isAction)
			{
				break;
			}
		}
		g.world.unitLog.SaveLogData();
	}

	public bool AllUnitActionFun()
	{
		lock (new object())
		{
			try
			{
				WorldUnitBase nextUnit = unitBaseInfo.GetNextUnit();
				if (nextUnit == null)
				{
					return false;
				}
				string[] array = confInofFile.confInof.blackLusckList.Split('|');
				foreach (string s in array)
				{
					if (nextUnit.GetLuck(int.Parse(s)) != null)
					{
						return true;
					}
				}
				bool flag = true;
				bool flag2 = true;
				if (confInofFile.confInof.isJudgeInti)
				{
					int intim = nextUnit.data.unitData.relationData.GetIntim(g.world.playerUnit);
					if (intim < confInofFile.confInof.gradeAndAttackInimLimit1 && intim > confInofFile.confInof.gradeAndAttackInimLimit2)
					{
						if (isDebug)
						{
							Console.WriteLine("陌生人：" + nextUnit.data.unitData.propertyData.GetName() + "，已自动屏蔽NPC境界突破和功法成长功能。");
						}
						flag = false;
					}
					if (intim < confInofFile.confInof.unitActionInimLimit1 && intim > confInofFile.confInof.unitActionInimLimit2)
					{
						if (isDebug)
						{
							Console.WriteLine("陌生人：" + nextUnit.data.unitData.propertyData.GetName() + "，已自动屏蔽额外行动功能。");
						}
						flag2 = false;
					}
					if (_random.Next(1, 100) < confInofFile.confInof.gradeAndAttackInimProp)
					{
						flag = true;
					}
					if (_random.Next(1, 100) < confInofFile.confInof.unitActionInimProp)
					{
						flag2 = true;
					}
				}
				_ = Time.realtimeSinceStartup;
				UIMapMain uI = g.ui.GetUI<UIMapMain>(UIType.MapMain);
				bool flag3 = (Object)(object)g.ui.GetUI(UIType.MapMain) != (Object)null && (Object)(object)g.ui.GetUI(UIType.BattleInfo) == (Object)null && (Object)(object)g.ui.GetUI(UIType.NPCInfo) == (Object)null && (Object)(object)g.ui.GetUI(UIType.Town) == (Object)null && uI != null && !((Behaviour)uI.uiGridUnit.npcInfoList).isActiveAndEnabled;
				if ((!g.world.battle.isBattle && !DramaTool.IsOpenDrama((Il2CppReferenceArray<UITypeBase>)null) && !g.scene.isLoadScene) & flag3)
				{
					UnitAction unitAction = new UnitAction(nextUnit, null, isAddAction: false);
					if (flag)
					{
						if (confInofFile.confInof.isNPCAutoGrage)
						{
							unitAction.StratUnitAction(5, isExtAction: false);
						}
						if (confInofFile.confInof.isNPCAutoAttackUp)
						{
							unitAction.StratUnitAction(10, isExtAction: false);
						}
					}
					if (flag2)
					{
						unitAction.isAdditionalAction = true;
						if (_random.Next(1, 100) < confInofFile.confInof.unitActionProp)
						{
							unitAction.StratUnitAction(-1);
						}
					}
				}
			}
			catch (Exception ex)
			{
				Console.WriteLine(ex.ToString());
				return true;
			}
			return true;
		}
	}

	public void OnWorldAddDay(ETypeData e)
	{
		//IL_011a: Unknown result type (might be due to invalid IL or missing references)
		//IL_011f: Unknown result type (might be due to invalid IL or missing references)
		isSend = true;
		startDay++;
		if (startDay >= 7)
		{
			g.timer.Time(Action.op_Implicit((Action)delegate
			{
				//IL_001e: Unknown result type (might be due to invalid IL or missing references)
				//IL_0067: Unknown result type (might be due to invalid IL or missing references)
				//IL_006d: Invalid comparison between Unknown and I4
				try
				{
					List<WorldUnitBase> unitExact = g.world.unit.GetUnitExact(g.world.playerUnit.data.unitData.GetPoint(), 1, true, false);
					if (unitExact != null && unitExact.Count > 0)
					{
						for (int num = unitExact.Count - 1; num >= 0; num--)
						{
							if ((int)unitExact[num].data.unitData.propertyData.race == 2)
							{
								unitExact.RemoveAt(num);
							}
						}
						int num2 = _random.Next(0, unitExact.Count);
						WorldUnitBase unit = unitExact[num2];
						string prefix = $"[{CommandTool.ConvertToYearsMonths(g.world.run.roundMonth + 1)}{g.world.run.roundDay + 1}日]";
						new AIUnitSet();
						int actionProp = CommandTool.GetAIUnitSet(unit).actionProp;
						if (_random.Next(0, 100) < actionProp)
						{
							g.timer.Time(Action.op_Implicit((Action)async delegate
							{
								try
								{
									Console.WriteLine("触发NPC主动交互!");
									int unitAction = CommandTool.GetUnitAction(unit);
									await CommandTool.SendToDeepSeek(question: (unitAction != 0) ? ("行为请求：" + prefix + "(你是" + unit.data.unitData.propertyData.GetName() + "),结合上下文及双方信息，你现在主动向用户发起行动交互请求，禁止内容重复，且isApproved字段为是。（actionNumber字段内容禁止为:" + unitAction + ")") : ("行为请求：" + prefix + "(你是" + unit.data.unitData.propertyData.GetName() + "),结合上下文及双方信息，你现在主动向用户发起行动交互请求，禁止内容重复，且isApproved字段为是。"), unit: unit, role: "user");
								}
								catch (Exception ex2)
								{
									Console.WriteLine(ex2.Message ?? "");
								}
							}), 0f, false);
						}
					}
				}
				catch (Exception ex)
				{
					Console.WriteLine(ex.ToString());
				}
			}), 0f, false);
		}
		if (confInofFile.confInof.runAIDate == 1)
		{
			if (unitBaseInfo.allUnit.Count < g.world.unit.GetUnits(true).Count - 1)
			{
				unitBaseInfo.InitUnit();
			}
			if (startDay >= confInofFile.confInof.runAIDateDiyDay)
			{
				unitBaseInfo.isOver = false;
				unitBaseInfo.isPause = false;
				unitBaseInfo.unitActionToPlayCount = 0;
				unitBaseInfo.actionDay++;
				startDay = 0;
				if (!runAction)
				{
					Console.WriteLine("自定义周期行动模式，NPC行动解锁。自定义周期：" + confInofFile.confInof.runAIDateDiyDay);
					runAction = true;
					MultiThreadTask val = new MultiThreadTask(false);
					val.AddCall(Action.op_Implicit((Action)OnComandUnit));
					val.Run(true);
				}
			}
		}
		if (startDay > 7)
		{
			g.timer.Thread(Action.op_Implicit((Action)RunAI), (Action)null);
			startDay = 0;
		}
	}

	public void RunAI()
	{
		Enumerator<WorldUnitBase> enumerator = g.world.unit.GetUnits(true).GetEnumerator();
		while (enumerator.MoveNext())
		{
			WorldUnitBase current = enumerator.Current;
			try
			{
				if (!current.IsPlayer() && current.GetLuck(mod_ID + 3) != null)
				{
					new UnitAction(current, null, isAddAction: false).StratUnitAction(-1);
				}
			}
			catch (Exception ex)
			{
				Console.WriteLine(ex.ToString());
			}
		}
	}

	public void OnTaskComplete(ETypeData e)
	{
		if (confInofFile.confInof.confDiySet.isAutoPostTask)
		{
			TaskComplete val = ((Il2CppObjectBase)e).Cast<TaskComplete>();
			if (val.task.data.taskBaseItem.submit == 0)
			{
				val.task.TaskPut((Action)null);
			}
		}
	}

	public void OnBattleStart(ETypeData e)
	{
		//IL_0195: Unknown result type (might be due to invalid IL or missing references)
		//IL_019a: Unknown result type (might be due to invalid IL or missing references)
		//IL_01a1: Unknown result type (might be due to invalid IL or missing references)
		//IL_03c0: Unknown result type (might be due to invalid IL or missing references)
		//IL_03fb: Unknown result type (might be due to invalid IL or missing references)
		//IL_0400: Unknown result type (might be due to invalid IL or missing references)
		//IL_0407: Unknown result type (might be due to invalid IL or missing references)
		//IL_054f: Unknown result type (might be due to invalid IL or missing references)
		//IL_058a: Unknown result type (might be due to invalid IL or missing references)
		//IL_058f: Unknown result type (might be due to invalid IL or missing references)
		//IL_0596: Unknown result type (might be due to invalid IL or missing references)
		try
		{
			if (((ConfBaseItem)g.world.battle.data.dungeonBaseItem).id == mod_ID + 1)
			{
				Console.WriteLine("当前处于天命争锋副本中");
				return;
			}
			int num = 0;
			unitTeams.Clear();
			Enumerator<WorldUnitBase> enumerator = g.world.unit.GetUnits(true).GetEnumerator();
			while (enumerator.MoveNext())
			{
				WorldUnitBase current = enumerator.Current;
				if (current.GetLuck(mod_ID + 1) != null)
				{
					unitTeams.Add(current);
				}
			}
			List<string> list = new List<string>();
			Enumerator<UnitCtrlBase> enumerator2 = SceneType.battle.unit.GetAllUnit(false).GetEnumerator();
			while (enumerator2.MoveNext())
			{
				WorldUnitBase val = BigFightExtensions.AsWorldUnit(enumerator2.Current);
				if (val != null)
				{
					list.Add(val.GetUnitID());
				}
			}
			if (!confInofFile.confInof.blackTeamList.Contains(g.world.battle.data.dungeonBaseItem.type) && SceneType.battle.battleMap.monstCount > 0)
			{
				foreach (WorldUnitBase unitTeam in unitTeams)
				{
					bool flag = true;
					if (list.Contains(unitTeam.GetUnitID()))
					{
						flag = false;
						break;
					}
					if (flag && !unitTeam.IsPlayer())
					{
						try
						{
							UnitCtrlHumanNPC obj = SceneType.battle.unit.CreateUnitHuman<UnitCtrlHumanNPC>(unitTeam.data, (UnitType)2, (Action<UnitCtrlHumanNPC>)null, (ConfMgrVariant)null);
							Console.WriteLine(unitTeam.data.unitData.propertyData.GetName() + "，玩家队友加入战斗!");
							Vector2 roomCenterPosi = SceneType.battle.battleMap.roomCenterPosi;
							((UnitCtrlBase)obj).move.SetPosition(roomCenterPosi);
							num++;
						}
						catch (Exception ex)
						{
							Console.WriteLine(ex.ToString());
						}
					}
				}
			}
			enumerator2 = SceneType.battle.unit.GetAllUnit(false).GetEnumerator();
			while (enumerator2.MoveNext())
			{
				UnitCtrlBase item = enumerator2.Current;
				WorldUnitBase worldUnitBase = BigFightExtensions.AsWorldUnit(item);
				if (worldUnitBase == null)
				{
					continue;
				}
				Action<ETypeData> action = delegate(ETypeData eTypeData)
				{
					//IL_0062: Unknown result type (might be due to invalid IL or missing references)
					//IL_007a: Unknown result type (might be due to invalid IL or missing references)
					if (confInofFile.confInof.confDiySet.isBeKillAllDrop)
					{
						try
						{
							((Il2CppObjectBase)eTypeData).Cast<OneUnitBeKill>();
							UIBattleInfo uI = g.ui.GetUI<UIBattleInfo>(UIType.BattleInfo);
							if ((Object)(object)uI != (Object)null)
							{
								Enumerator<PropsData> enumerator5 = ((DataProps)worldUnitBase.data.unitData.propData).CloneAllProps().GetEnumerator();
								while (enumerator5.MoveNext())
								{
									PropsData current6 = enumerator5.Current;
									new UnitCtrlBase();
									uI.uiDrop.AddDrop(current6, item.posiRoot.position);
									worldUnitBase.data.unitData.propData.DelPropsItem(current6);
								}
								Console.WriteLine("被攻击者添加全掉落");
							}
						}
						catch (Exception ex4)
						{
							Console.WriteLine(ex4.Message);
						}
					}
				};
				if (!UnitBeKillAction.ContainsKey(EGameType.OneUnitBeKill(worldUnitBase.data.unitData.unitID)))
				{
					UnitBeKillAction.Add(EGameType.OneUnitBeKill(worldUnitBase.data.unitData.unitID), action);
					g.events.On(EGameType.OneUnitBeKill(worldUnitBase.data.unitData.unitID), Action<ETypeData>.op_Implicit(action), 0, false);
				}
				if (confInofFile.confInof.blackTeamList.Contains(g.world.battle.data.dungeonBaseItem.type) || SceneType.battle.battleMap.monstCount <= 0)
				{
					continue;
				}
				if (confInofFile.confInof.NPCTeam)
				{
					new List<string>();
					if (worldUnitBase.data.unitData.objData.ContainsKey("AITem"))
					{
						foreach (string item2 in JsonConvert.DeserializeObject<List<string>>(worldUnitBase.data.unitData.objData.GetString("AITem")))
						{
							try
							{
								if (!list.Contains(item2))
								{
									WorldUnitBase unit = g.world.unit.GetUnit(item2, true);
									if (unit != null && !unit.IsPlayer())
									{
										UnitCtrlHumanNPC obj2 = SceneType.battle.unit.CreateUnitHuman<UnitCtrlHumanNPC>(unit.data, item.data.unitType, (Action<UnitCtrlHumanNPC>)null, (ConfMgrVariant)null);
										Console.WriteLine(unit.data.unitData.propertyData.GetName() + "，NPC队友加入战斗!");
										Vector2 roomCenterPosi2 = SceneType.battle.battleMap.roomCenterPosi;
										((UnitCtrlBase)obj2).move.SetPosition(roomCenterPosi2);
										num++;
									}
								}
							}
							catch (Exception ex2)
							{
								Console.WriteLine(ex2.ToString());
							}
						}
					}
				}
				if (!confInofFile.confInof.NPCHelp)
				{
					continue;
				}
				if (num > confInofFile.confInof.NPCMaxCount)
				{
					break;
				}
				List<WorldUnitBase> allGoodRelation = worldUnitBase.data.unitData.relationData.GetAllGoodRelation(true, false);
				List<WorldUnitBase> list2 = new List<WorldUnitBase>();
				int num2 = 0;
				enumerator = allGoodRelation.GetEnumerator();
				while (enumerator.MoveNext())
				{
					WorldUnitBase current4 = enumerator.Current;
					if (current4.data.dynUnitData.GetGrade() >= worldUnitBase.data.dynUnitData.GetGrade() && current4.isOneGrid(worldUnitBase) && !current4.IsPlayer())
					{
						list2.Add(current4);
						num2++;
						num++;
						if (num2 >= confInofFile.confInof.NPCHelpCount)
						{
							break;
						}
					}
				}
				foreach (WorldUnitBase item3 in list2)
				{
					if (!list.Contains(item3.GetUnitID()))
					{
						UnitCtrlHumanNPC obj3 = SceneType.battle.unit.CreateUnitHuman<UnitCtrlHumanNPC>(item3.data, item.data.unitType, (Action<UnitCtrlHumanNPC>)null, (ConfMgrVariant)null);
						Console.WriteLine(item3.data.unitData.propertyData.GetName() + "，同一格亲友加入战斗!");
						Vector2 roomCenterPosi3 = SceneType.battle.battleMap.roomCenterPosi;
						((UnitCtrlBase)obj3).move.SetPosition(roomCenterPosi3);
					}
				}
			}
			if (g.world.battle.isBattle && confInofFile.confInof.isChangeDropDis)
			{
				SceneType.battle.battleData.playerUnit.dropDis.baseValue = 9999;
			}
		}
		catch (Exception ex3)
		{
			Console.WriteLine(ex3.ToString());
		}
	}

	public void OnBattleEnd(ETypeData e)
	{
		try
		{
			if (((Il2CppObjectBase)e).Cast<BattleEnd>().isWin)
			{
				if (confInofFile.confInof.isPlayAutoAttackUp)
				{
					if (isDebug)
					{
						Console.WriteLine("本次战斗胜利，玩家开始升级功法。");
					}
					new UnitAction(g.world.playerUnit, null, isAddAction: false).StratUnitAction(10, isExtAction: false);
				}
				if (confInofFile.confInof.isTeamAutoAttackUp)
				{
					foreach (WorldUnitBase unitTeam in unitTeams)
					{
						if (isDebug)
						{
							Console.WriteLine("本次战斗胜利，有NPC组队，队友功法开始升级。");
						}
						new UnitAction(unitTeam, null, isAddAction: false).StratUnitAction(10, isExtAction: false);
					}
				}
				if (confInofFile.confInof.isTeamAutoGrageUp)
				{
					foreach (WorldUnitBase unitTeam2 in unitTeams)
					{
						new UnitAction(unitTeam2, null, isAddAction: false).StratUnitAction(5, isExtAction: false);
					}
				}
			}
			if (unitBaseInfo.allUnit == null)
			{
				unitBaseInfo.InitUnit();
			}
			else if (unitBaseInfo.allUnit.Count < 3)
			{
				unitBaseInfo.InitUnit();
			}
		}
		catch (Exception ex)
		{
			Console.WriteLine(ex.ToString());
		}
	}

	public void SengGradePropToSchool()
	{
		//IL_0054: Unknown result type (might be due to invalid IL or missing references)
		//IL_00a3: Unknown result type (might be due to invalid IL or missing references)
		//IL_00ad: Expected O, but got Unknown
		//IL_03c3: Unknown result type (might be due to invalid IL or missing references)
		//IL_03c8: Unknown result type (might be due to invalid IL or missing references)
		//IL_03d8: Unknown result type (might be due to invalid IL or missing references)
		//IL_03e4: Expected O, but got Unknown
		int num = 0;
		if (!confInofFile.confInof.confDiySet.isAutoSendGradePropInSchool)
		{
			return;
		}
		MapBuildSchool school = g.world.playerUnit.data.school;
		if (school == null)
		{
			return;
		}
		MapBuildSchool build = g.world.build.GetBuild<MapBuildSchool>(g.world.playerUnit.data.unitData.GetPoint());
		if (build == null)
		{
			return;
		}
		Console.WriteLine("当前脚下的建筑名称为：" + GameTool.LS(build.name));
		Console.WriteLine("玩家宗门名称：" + school.GetName(true));
		if (!UnitConditionTool.Condition("pointIsMyShool_0_0_1", new UnitConditionData(g.world.playerUnit, (WorldUnitBase)null)))
		{
			return;
		}
		school = build;
		Console.WriteLine("当前脚下是自己宗门的总舵或分舵");
		if (!school.buildData.npcSchoolMain.Equals(g.world.playerUnit.data.unitData.unitID))
		{
			return;
		}
		Console.WriteLine("自己是宗主");
		string text = "startDay";
		if (g.world.playerUnit.data.unitData.objData.ContainsKey(text))
		{
			int num2 = g.world.playerUnit.data.unitData.objData.GetInt(text);
			if (g.data.world.worldDay - num2 < confInofFile.confInof.confDiySet.startDay)
			{
				return;
			}
			g.world.playerUnit.data.unitData.objData.SetString(text, g.data.world.worldDay);
		}
		else
		{
			g.world.playerUnit.data.unitData.objData.SetString("startDay", g.data.world.worldDay);
		}
		if (confInofFile.confInof.confDiySet.shoolPsot[0])
		{
			Enumerator<string> enumerator = school.buildData.npcBigElders.GetEnumerator();
			while (enumerator.MoveNext())
			{
				string current = enumerator.Current;
				WorldUnitBase unit = g.world.unit.GetUnit(current, true);
				if (unit != null && CommandTool.AutoUpGrade(unit))
				{
					num++;
				}
			}
		}
		if (confInofFile.confInof.confDiySet.shoolPsot[1])
		{
			Enumerator<string> enumerator = school.buildData.npcElders.GetEnumerator();
			while (enumerator.MoveNext())
			{
				string current2 = enumerator.Current;
				WorldUnitBase unit2 = g.world.unit.GetUnit(current2, true);
				if (unit2 != null && CommandTool.AutoUpGrade(unit2))
				{
					num++;
				}
			}
		}
		if (confInofFile.confInof.confDiySet.shoolPsot[2])
		{
			Enumerator<string> enumerator = school.buildData.npcIn.GetEnumerator();
			while (enumerator.MoveNext())
			{
				string current3 = enumerator.Current;
				WorldUnitBase unit3 = g.world.unit.GetUnit(current3, true);
				if (unit3 != null && CommandTool.AutoUpGrade(unit3))
				{
					num++;
				}
			}
		}
		if (confInofFile.confInof.confDiySet.shoolPsot[3])
		{
			Enumerator<string> enumerator = school.buildData.npcInherit.GetEnumerator();
			while (enumerator.MoveNext())
			{
				string current4 = enumerator.Current;
				WorldUnitBase unit4 = g.world.unit.GetUnit(current4, true);
				if (unit4 != null && CommandTool.AutoUpGrade(unit4))
				{
					num++;
				}
			}
		}
		if (confInofFile.confInof.confDiySet.shoolPsot[4])
		{
			Enumerator<string> enumerator = school.buildData.npcOut.GetEnumerator();
			while (enumerator.MoveNext())
			{
				string current5 = enumerator.Current;
				WorldUnitBase unit5 = g.world.unit.GetUnit(current5, true);
				if (unit5 != null && CommandTool.AutoUpGrade(unit5))
				{
					num++;
				}
			}
		}
		if (num > 0)
		{
			DramaTool.OpenDrama(mod_ID + 5, new DramaData
			{
				unitLeft = g.world.playerUnit,
				unitRight = null
			});
		}
	}

	public void OnWorldRunStart(ETypeData e)
	{
		g.timer.Thread(Action.op_Implicit((Action)delegate
		{
			if (confInofFile.confInof.NPCAutoFight)
			{
				for (int i = 1; i <= 10; i++)
				{
					try
					{
						if (!UIFight.matchUnitMaps.ContainsKey(i))
						{
							List<Competitor> list = new List<Competitor>();
							list = ArenaMatcher.GetUnits(i, UIFight.fightCount);
							UIFight.matchUnits = new ArenaMatcher().GenerateMatches(list);
							UIFight.matchUnitMaps.Add(i, UIFight.matchUnits);
						}
						if (UIFight.matchUnitMaps[i].Count > 1)
						{
							UIFight.selIndex = UIFight.matchUnitMaps[i].Count;
							MatchUnit matchUnit = ArenaMatcher.UnitFight(UIFight.matchUnitMaps[i]);
							if (matchUnit != null)
							{
								UIFight.matchUnitMaps[i].Clear();
								UIFight.matchUnitMaps[i].Add(matchUnit);
								Console.WriteLine("完成" + MartialUtil.getAreaName(i) + "区域的天命争锋。。");
							}
							else
							{
								Console.WriteLine(MartialUtil.getAreaName(i) + "的比赛遭遇天机干预，天命争锋失败!");
							}
						}
					}
					catch (Exception ex)
					{
						Console.WriteLine(ex.ToString());
					}
				}
			}
			UIFight.matchUnitMaps.Clear();
			UIFight.matchUnits.Clear();
			UIFight.selIndexMap.Clear();
			UIFight.selIndex = -1;
			Console.WriteLine("本轮天命争锋结束。。。");
		}), Action.op_Implicit((Action)delegate
		{
			Console.WriteLine("开始新一轮天命争锋。。。");
		}));
		g.timer.Time(Action.op_Implicit((Action)async delegate
		{
			try
			{
				UnitAI unitAI = new UnitAI();
				unitAI.init();
				await CommandTool.SendToDeepSeek(g.world.playerUnit, "system", unitAI.strInfo, 9);
			}
			catch (Exception ex)
			{
				Console.WriteLine(ex.Message ?? "");
			}
		}), 0f, false);
		g.timer.Time(Action.op_Implicit((Action)async delegate
		{
			List<string> list = letterUnitList;
			if (list != null && list.Count != 0)
			{
				int count = list.Count;
				int num = _random.Next(0, count);
				if (count > num)
				{
					letterUnit = g.world.unit.GetUnit(list[num], true);
					if (letterUnit != null)
					{
						if (letterUnit.isDie && letterUnitList.Contains(letterUnit.data.unitData.unitID))
						{
							letterUnitList.Remove(letterUnit.data.unitData.unitID);
						}
						else
						{
							AIUnitSet aIUnitSet = CommandTool.GetAIUnitSet(letterUnit);
							if (aIUnitSet != null)
							{
								int num2 = _random.Next(0, 100);
								int num3 = ((letterUnit.data.unitData.relationData.GetIntim(g.world.playerUnit.data.unitData.unitID) > g.world.playerUnit.data.unitData.relationData.GetIntim(letterUnit.data.unitData.unitID)) ? letterUnit.data.unitData.relationData.GetIntim(g.world.playerUnit.data.unitData.unitID) : g.world.playerUnit.data.unitData.relationData.GetIntim(letterUnit.data.unitData.unitID));
								if (num3 < 120 && num3 > -120)
								{
									aIUnitSet.letterProp /= 2;
								}
								if (num2 < aIUnitSet.letterProp)
								{
									try
									{
										Console.WriteLine("触发NPC主动写信!");
										string text = $"[{CommandTool.ConvertToYearsMonths(g.world.run.roundMonth + 1)}{g.world.run.roundDay + 1}日]";
										string input = CommandTool.RegexForPromat(aIPromptCommand.promptFile.letterPrompt);
										input = CommandTool.RegexForPromat(input);
										string question = "行为请求：" + text + "(你是" + letterUnit.data.unitData.propertyData.GetName() + ")。请结合近期经历、历史对话、双方身份、双方关系等内容综合研判后，向用户写一封书信。\n[书信输出规则]\n" + input;
										await CommandTool.SendToDeepSeek(letterUnit, "system", question, 2);
									}
									catch (Exception ex)
									{
										Console.WriteLine(ex.Message ?? "");
									}
								}
							}
						}
					}
				}
			}
		}), 0f, false);
	}

	public void OnWorldRunEnd(ETypeData e)
	{
		//IL_000d: Unknown result type (might be due to invalid IL or missing references)
		//IL_0017: Expected O, but got Unknown
		try
		{
			isSortEnd = false;
			MultiThreadTask multiThreadTask = new MultiThreadTask(false);
			multiThreadTask.AddCall(Action.op_Implicit((Action)OnSort));
			Action action = delegate
			{
				multiThreadTask.Stop();
				Console.WriteLine("排名结束！" + multiThreadTask.isStop);
			};
			multiThreadTask.Run(true, Action.op_Implicit(action));
		}
		catch (Exception ex)
		{
			Console.WriteLine(ex.ToString());
		}
		g.timer.Thread(Action.op_Implicit((Action)delegate
		{
			g.timer.Time(Action.op_Implicit((Action)delegate
			{
				SendLetter();
				RunUnitLog();
			}), 0.1f, false);
			g.timer.Time(Action.op_Implicit((Action)delegate
			{
				GetWorldLogAsync();
			}), 1f, false);
		}), (Action)null);
	}

	public void RunUnitLog()
	{
		if (!confInofFile.confInof.AutoUnitLog)
		{
			return;
		}
		try
		{
			Console.WriteLine("开始NPC自传提炼。。");
			string text = CommandTool.ConvertToYearsMonths(g.world.run.roundMonth);
			Console.WriteLine("NPC自传提炼" + text);
			List<WorldUnitBase> list = new List<WorldUnitBase>();
			Enumerator<WorldUnitBase> enumerator = g.world.unit.GetUnits(true).GetEnumerator();
			while (enumerator.MoveNext())
			{
				WorldUnitBase current = enumerator.Current;
				string unitID = current.GetUnitID();
				if (unitLogCMD.unitLog.isAutoGetLog.ContainsKey(unitID) && unitLogCMD.unitLog.isAutoGetLog[unitID])
				{
					if (!unitLogCMD.unitLog.logList.ContainsKey(unitID))
					{
						unitLogCMD.unitLog.logList.Add(unitID, new Dictionary<int, UnitLogItem>());
					}
					list.Add(current);
				}
			}
			foreach (WorldUnitBase item in list)
			{
				try
				{
					string unitID2 = item.GetUnitID();
					string promat = "";
					if (unitLogCMD.unitLog.logList[unitID2].ContainsKey(g.world.run.roundMonth))
					{
						continue;
					}
					promat += text;
					UnitLogItem logText = UnitLogCMD.GetLogText(item, g.world.run.roundMonth);
					if (logText.logText.Length > 0)
					{
						promat = promat + "[当月经历]\n" + logText.logText + "\n";
					}
					string unitMessageByMonth = CommandTool.GetUnitMessageByMonth(item, g.world.run.roundMonth);
					if (unitMessageByMonth.Length > 0)
					{
						promat = promat + "[当月" + item.GetName() + "与" + g.world.playerUnit.GetName() + "的私聊对话记录]\n" + unitMessageByMonth;
					}
					string groupMessageByMonth = CommandTool.GetGroupMessageByMonth(item, g.world.run.roundMonth);
					if (groupMessageByMonth.Length > 0)
					{
						promat = promat + "[当月" + item.GetName() + "的群聊对话记录]\n" + groupMessageByMonth;
					}
					if (logText.logText.Length + unitMessageByMonth.Length + groupMessageByMonth.Length > 10)
					{
						Console.WriteLine("开始提炼：" + item.GetName());
						g.timer.Time(Action.op_Implicit((Action)async delegate
						{
							await UIUnitLog.SeekDeepToUnitLog(unitID2, promat, g.world.run.roundMonth);
						}), 0f, false);
					}
				}
				catch (Exception ex)
				{
					Console.WriteLine(ex.ToString());
				}
			}
		}
		catch (Exception ex2)
		{
			Console.WriteLine(ex2.ToString());
		}
	}

	public void RunFight()
	{
		foreach (MatchUnit matchUnit in UIFight.matchUnits)
		{
			try
			{
				if (StringExtensions.IsEmpty(matchUnit.winner))
				{
					WorldUnitBase unit = g.world.unit.GetUnit(matchUnit.unitLeft, true);
					WorldUnitBase unit2 = g.world.unit.GetUnit(matchUnit.unitRight, true);
					WorldUnitBase unit3 = UnitActionRoleBattle.UnitBattle(unit, unit2, (SchoolPostType)0);
					matchUnit.winner = unit3.GetUnitID();
					unit3.AddValue("tianming", 2);
					g.world.unit.GetUnit(matchUnit.loser, true)?.AddValue("tianming", -1);
				}
			}
			catch (Exception ex)
			{
				Console.WriteLine(ex.ToString());
			}
		}
		UIFight.selIndex = -1;
		UIFight.matchUnits.Clear();
		Console.WriteLine("完成天命争锋。。。");
	}

	public void GetWorldSubLog()
	{
	}

	public void GetWorldLogAsync()
	{
		if (!worldLogCMD.worldLog.isOpen)
		{
			return;
		}
		try
		{
			WorldLogItem worldLogItem = new WorldLogItem
			{
				month = g.world.run.roundMonth
			};
			worldLogItem.logText = WorldLogCMD.GetUnitLogText(g.world.run.roundMonth, out var npcs);
			if (StringExtensions.IsEmpty(worldLogItem.logText))
			{
				worldLogItem.logText = "无事发生";
			}
			UITipItem.AddTip("主世界剧情开始自动推演！", 3f);
			CommandTool.WorldLogSeedDeepSeekAsync(worldLogItem, npcs);
		}
		catch (Exception ex)
		{
			Console.WriteLine(ex.ToString());
			UITipItem.AddTip("剧情推演失败！", 3f);
		}
	}

	public void SendLetter()
	{
		g.timer.Time(Action.op_Implicit((Action)delegate
		{
			if (letterUnit != null && letterUnit.data.unitData.objData.ContainsKey("LetterMessage"))
			{
				string text = $"[{CommandTool.ConvertToYearsMonths(g.world.run.roundMonth + 1)}{g.world.run.roundDay + 1}日]";
				string text2 = letterUnit.data.unitData.objData.GetString("LetterMessage");
				letterUnit.data.unitData.objData.DelString("LetterMessage");
				string text3 = "";
				try
				{
					AIActionMessage xmlText = CommandTool.GetXmlText(text2);
					if (xmlText != null)
					{
						text3 = xmlText.yourResponse;
						text3 = text3.Trim();
						int num = 1;
						if (StringExtensions.IsInt(xmlText.actionParameter2))
						{
							num = int.Parse(xmlText.actionParameter2);
						}
						List<PropsData> val;
						if (StringExtensions.IsEmpty(xmlText.actionParameter1))
						{
							val = null;
						}
						else
						{
							val = new List<PropsData>();
							Enumerator<PropsData> enumerator = ((DataProps)letterUnit.data.unitData.propData).allProps.GetEnumerator();
							while (enumerator.MoveNext())
							{
								PropsData current = enumerator.Current;
								if (current.propsInfoBase.name.Contains(xmlText.actionParameter1))
								{
									PropsData val2 = current.Clone();
									val2.propsCount = num;
									val.Add(val2);
								}
							}
							enumerator = val.GetEnumerator();
							while (enumerator.MoveNext())
							{
								PropsData current2 = enumerator.Current;
								((DataProps)letterUnit.data.unitData.propData).DelProps(current2.soleID, current2.propsCount);
							}
						}
						Console.WriteLine("开始投递信件");
						text3 = CommandTool.RegexForOutPut(text3);
						string[] array = new string[1] { text3 };
						if (val != null && val.Count > 0)
						{
							g.data.world.AddLetter(letterUnit, 99990001, Il2CppStringArray.op_Implicit(array), val);
						}
						else
						{
							int num2 = 0;
							Enumerator<ConfItemPropsItem> enumerator2 = ((ConfItemPropsBase)g.conf.itemProps)._allConfList.GetEnumerator();
							while (enumerator2.MoveNext())
							{
								ConfItemPropsItem current3 = enumerator2.Current;
								if (GameTool.LS(current3.name).Contains(xmlText.actionParameter1))
								{
									num2 = ((ConfBaseItem)current3).id;
									break;
								}
							}
							if (num2 != 0)
							{
								PropsData val3 = PropsData.NewProps(num2, num);
								val?.Add(val3);
								g.data.world.AddLetter(letterUnit, 99990001, Il2CppStringArray.op_Implicit(array), val);
							}
							else
							{
								g.data.world.AddLetter(letterUnit, 99990001, Il2CppStringArray.op_Implicit(array), (List<PropsData>)null);
							}
						}
						string msgText = text + letterUnit.data.unitData.propertyData.GetName() + "主动向写了一封信！";
						CommandTool.AddMessage(letterUnit, "user", msgText, 2);
						CommandTool.AddMessage(letterUnit, "assistant", text2, 2);
						Console.WriteLine("完成保存信件记录");
						UITipItem.AddTip("你收到一份来自亲友(" + letterUnit.data.unitData.propertyData.GetName() + ")的信！", 3f);
						g.ui.OpenUI<UICheckPopup>(UIType.CheckPopup).InitData("提示", "你收到一份来自亲友(" + letterUnit.data.unitData.propertyData.GetName() + ")的信！是否查看？", 2, Action.op_Implicit((Action)delegate
						{
							UIPlayerTask obj = g.ui.OpenUI<UIPlayerTask>(UIType.PlayerTask);
							obj.Init();
							((UIPlayerTaskBase)obj).tglEmail.isOn = true;
							obj.uiEmail.UpdateUI();
						}), (Action)null);
					}
				}
				catch (Exception ex)
				{
					Console.WriteLine(ex.Message);
				}
			}
			SendUnitDieLetter();
		}), 3f, false);
	}

	public void OnSaveData(ETypeData e)
	{
		g.timer.Time(Action.op_Implicit((Action)delegate
		{
			//IL_0015: Unknown result type (might be due to invalid IL or missing references)
			//IL_001f: Expected O, but got Unknown
			if (ExitAutoSave)
			{
				UIBase uI = g.ui.GetUI(new UITypeBase("UIChatAINew", (UILayer)0));
				if ((Object)(object)uI != (Object)null)
				{
					UIChatAINew component = ((Component)uI).GetComponent<UIChatAINew>();
					if ((Object)(object)component != (Object)null)
					{
						if (Messages.ContainsKey(component.unit.data.unitData.unitID))
						{
							string text = JsonConvert.SerializeObject((object)Messages[component.unit.data.unitData.unitID]);
							component.unit.data.unitData.objData.SetString("Messages", text);
						}
						if (ActionMessages.ContainsKey(component.unit.data.unitData.unitID))
						{
							string text2 = JsonConvert.SerializeObject((object)ActionMessages[component.unit.data.unitData.unitID]);
							component.unit.data.unitData.objData.SetString("ActionMessages", text2);
						}
						Console.WriteLine("已保存单个NPC数据。");
					}
				}
			}
			else
			{
				Enumerator<WorldUnitBase> enumerator = g.world.unit.GetUnits(true).GetEnumerator();
				while (enumerator.MoveNext())
				{
					WorldUnitBase current = enumerator.Current;
					if (Messages.ContainsKey(current.data.unitData.unitID))
					{
						string text3 = JsonConvert.SerializeObject((object)Messages[current.data.unitData.unitID]);
						current.data.unitData.objData.SetString("Messages", text3);
					}
					if (ActionMessages.ContainsKey(current.data.unitData.unitID))
					{
						string text4 = JsonConvert.SerializeObject((object)ActionMessages[current.data.unitData.unitID]);
						current.data.unitData.objData.SetString("ActionMessages", text4);
					}
				}
				Console.WriteLine("已保存所有NPC聊天数据");
			}
			ExitAutoSave = false;
			if (!chatGroup.SaveGroup())
			{
				Console.WriteLine("聊天群组保存失败!");
			}
			aIPromptCommand.Save();
			preText.Save();
			aIConfInfo.Save();
			worldLogCMD.Save();
			unitLogCMD.Save();
			EffectConfigTool.SaveEffect();
		}), 0f, false);
	}

	public void SendUnitDieLetter()
	{
		try
		{
			if (unitDies == null)
			{
				return;
			}
			for (int i = 0; i < unitDies.Count; i++)
			{
				WorldUnitBase val = unitDies[i];
				if (val == null || !val.data.unitData.objData.ContainsKey("UnitDieMessage"))
				{
					continue;
				}
				string text = val.data.unitData.objData.GetString("UnitDieMessage");
				val.data.unitData.objData.DelString("UnitDieMessage");
				Console.WriteLine("开始投传记：" + text);
				text = CommandTool.RegexForOutPut(text);
				string[] array = new string[1] { text };
				List<PropsData> val2 = new List<PropsData>();
				if (g.world.playerUnit.data.unitData.relationData.GetAllGoodRelationUnitID(true, false).Contains(val.data.unitData.unitID))
				{
					Enumerator<PropsData> enumerator = ((DataProps)val.data.unitData.propData).allProps.GetEnumerator();
					while (enumerator.MoveNext())
					{
						PropsData current = enumerator.Current;
						val2.Add(current.Clone());
					}
					((DataProps)val.data.unitData.propData).ClearAllProps();
					UITipItem.AddTip("你收到一份来自特殊亲友(" + val.data.unitData.propertyData.GetName() + ")的传记及身前遗物！", 3f);
				}
				else
				{
					List<PropsData> props = ((DataProps)val.data.unitData.propData).GetProps(10001);
					if (props != null && props.Count > 0)
					{
						val2.Add(props[0].Clone());
						UITipItem.AddTip("你收到一份来自普通好友(" + val.data.unitData.propertyData.GetName() + ")的传记及遗留灵石！", 3f);
						((DataProps)val.data.unitData.propData).DelProps(10001, props[0].propsCount);
					}
				}
				g.data.world.AddLetter(val, 99990002, Il2CppStringArray.op_Implicit(array), val2);
				unitDies.RemoveAt(i);
			}
		}
		catch (Exception ex)
		{
			Console.WriteLine(ex.Message);
		}
	}

	public void InitFindList()
	{
		FindList.Clear();
		if (!FindList.ContainsKey("Grade"))
		{
			FindList.Add("Grade", new List<Tuple<string, string>>());
			FindList["Grade"].Add(new Tuple<string, string>("全部", "-1"));
		}
		for (int i = 1; i <= 10; i++)
		{
			Tuple<string, string> item = new Tuple<string, string>(MartialUtil.getGradeName(i), i.ToString());
			FindList["Grade"].Add(item);
		}
		if (!FindList.ContainsKey("Sex"))
		{
			FindList.Add("Sex", new List<Tuple<string, string>>());
			FindList["Sex"].Add(new Tuple<string, string>("全部", "-1"));
			Tuple<string, string> item2 = new Tuple<string, string>("男", "1");
			Tuple<string, string> item3 = new Tuple<string, string>("女", "2");
			FindList["Sex"].Add(item2);
			FindList["Sex"].Add(item3);
		}
		if (!FindList.ContainsKey("School"))
		{
			FindList.Add("School", new List<Tuple<string, string>>());
			FindList["School"].Add(new Tuple<string, string>("全部", "-1"));
			FindList["School"].Add(new Tuple<string, string>("无宗门", "0"));
		}
		HashSet<string> hashSet = new HashSet<string>();
		Enumerator<MapBuildSchool> enumerator = g.world.build.GetBuilds<MapBuildSchool>().GetEnumerator();
		while (enumerator.MoveNext())
		{
			MapBuildSchool topSchool = enumerator.Current.GetTopSchool();
			if (!hashSet.Contains(((BuildDataBase)topSchool.buildData).id))
			{
				Tuple<string, string> item4 = new Tuple<string, string>(topSchool.name, ((BuildDataBase)topSchool.buildData).id);
				FindList["School"].Add(item4);
				hashSet.Add(((BuildDataBase)topSchool.buildData).id);
			}
		}
		if (!FindList.ContainsKey("Character"))
		{
			FindList.Add("Character", new List<Tuple<string, string>>());
			FindList["Character"].Add(new Tuple<string, string>("全部", "-1"));
		}
		Enumerator<ConfRoleCreateCharacterItem> enumerator2 = ((ConfRoleCreateCharacterBase)g.conf.roleCreateCharacter)._allConfList.GetEnumerator();
		while (enumerator2.MoveNext())
		{
			ConfRoleCreateCharacterItem current = enumerator2.Current;
			Tuple<string, string> item5 = new Tuple<string, string>(GameTool.LS(current.sc5asd_sd34), ((ConfBaseItem)current).id.ToString());
			FindList["Character"].Add(item5);
		}
		if (!FindList.ContainsKey("Beauty"))
		{
			FindList.Add("Beauty", new List<Tuple<string, string>>());
			FindList["Beauty"].Add(new Tuple<string, string>("全部", "-1"));
		}
		Enumerator<ConfRoleBeautyItem> enumerator3 = ((ConfRoleBeautyBase)g.conf.roleBeauty)._allConfList.GetEnumerator();
		while (enumerator3.MoveNext())
		{
			ConfRoleBeautyItem current2 = enumerator3.Current;
			Tuple<string, string> item6 = new Tuple<string, string>(GameTool.LS(current2.text), GameTool.LS(current2.text));
			FindList["Beauty"].Add(item6);
		}
		if (!FindList.ContainsKey("Race"))
		{
			FindList.Add("Race", new List<Tuple<string, string>>());
			FindList["Race"].Add(new Tuple<string, string>("全部", "-1"));
		}
		Enumerator<ConfRoleRaceItem> enumerator4 = ((ConfRoleRaceBase)g.conf.roleRace)._allConfList.GetEnumerator();
		while (enumerator4.MoveNext())
		{
			ConfRoleRaceItem current3 = enumerator4.Current;
			Tuple<string, string> item7 = new Tuple<string, string>(GameTool.LS(current3.race), ((ConfBaseItem)current3).id.ToString());
			FindList["Race"].Add(item7);
		}
		if (!FindList.ContainsKey("Reltion"))
		{
			FindList.Add("Reltion", new List<Tuple<string, string>>());
			FindList["Reltion"].Add(new Tuple<string, string>("全部", "-1"));
			FindList["Reltion"].Add(new Tuple<string, string>("亲族", "1"));
			FindList["Reltion"].Add(new Tuple<string, string>("好友", "2"));
			FindList["Reltion"].Add(new Tuple<string, string>("好感", "3"));
			FindList["Reltion"].Add(new Tuple<string, string>("仇恨", "4"));
			FindList["Reltion"].Add(new Tuple<string, string>("陌生", "5"));
			FindList["Reltion"].Add(new Tuple<string, string>("道侣", "6"));
		}
	}

	public void OnIntoWorld(ETypeData e)
	{
		try
		{
			MOD_Tc85Yz = CommandTool.GetAssemblyLoadedByFullName("MOD_Tc85Yz");
			if (((DataObjectData)((DataBase<Object>)(object)g.data.dataObj).data).ContainsKey("apiUrl"))
			{
				apiUrl = ((DataObjectData)((DataBase<Object>)(object)g.data.dataObj).data).GetString("apiUrl");
			}
			if (((DataObjectData)((DataBase<Object>)(object)g.data.dataObj).data).ContainsKey("apiKey"))
			{
				apiKey = ((DataObjectData)((DataBase<Object>)(object)g.data.dataObj).data).GetString("apiKey");
			}
			if (((DataObjectData)((DataBase<Object>)(object)g.data.dataObj).data).ContainsKey("modelName"))
			{
				modelName = ((DataObjectData)((DataBase<Object>)(object)g.data.dataObj).data).GetString("modelName");
			}
			if (((DataObjectData)((DataBase<Object>)(object)g.data.dataObj).data).ContainsKey("apiUrlSpeek"))
			{
				apiUrlSpeek = ((DataObjectData)((DataBase<Object>)(object)g.data.dataObj).data).GetString("apiUrlSpeek");
			}
			if (((DataObjectData)((DataBase<Object>)(object)g.data.dataObj).data).ContainsKey("apiKeySpeek"))
			{
				apiKeySpeek = ((DataObjectData)((DataBase<Object>)(object)g.data.dataObj).data).GetString("apiKeySpeek");
			}
			if (((DataObjectData)((DataBase<Object>)(object)g.data.dataObj).data).ContainsKey("modelNameSpeek"))
			{
				modelNameSpeek = ((DataObjectData)((DataBase<Object>)(object)g.data.dataObj).data).GetString("modelNameSpeek");
			}
			if (StringExtensions.IsEmpty(apiKeySpeek) && apiUrl.Contains("api.siliconflow.cn"))
			{
				apiKeySpeek = apiKey;
			}
			if (((DataObjectData)((DataBase<Object>)(object)g.data.dataObj).data).ContainsKey("prompt"))
			{
				prompt = ((DataObjectData)((DataBase<Object>)(object)g.data.dataObj).data).GetString("prompt");
				if (StringExtensions.IsEmpty(prompt))
				{
					prompt = baseSet;
				}
			}
			if (((DataObjectData)((DataBase<Object>)(object)g.data.dataObj).data).ContainsKey("max_tokens"))
			{
				max_tokens = int.Parse(((DataObjectData)((DataBase<Object>)(object)g.data.dataObj).data).GetString("max_tokens"));
			}
			if (((DataObjectData)((DataBase<Object>)(object)g.data.dataObj).data).ContainsKey("temperature"))
			{
				temperature = float.Parse(((DataObjectData)((DataBase<Object>)(object)g.data.dataObj).data).GetString("temperature"));
			}
			if (((DataObjectData)((DataBase<Object>)(object)g.data.dataObj).data).ContainsKey("top_k"))
			{
				top_k = int.Parse(((DataObjectData)((DataBase<Object>)(object)g.data.dataObj).data).GetString("top_k"));
			}
			if (((DataObjectData)((DataBase<Object>)(object)g.data.dataObj).data).ContainsKey("top_p"))
			{
				top_p = float.Parse(((DataObjectData)((DataBase<Object>)(object)g.data.dataObj).data).GetString("top_p"));
			}
			if (((DataObjectData)((DataBase<Object>)(object)g.data.dataObj).data).ContainsKey("frequency_penalty"))
			{
				frequency_penalty = float.Parse(((DataObjectData)((DataBase<Object>)(object)g.data.dataObj).data).GetString("frequency_penalty"));
			}
			if (((DataObjectData)((DataBase<Object>)(object)g.data.dataObj).data).ContainsKey("isLoadProp"))
			{
				isLoadProp = ((DataObjectData)((DataBase<Object>)(object)g.data.dataObj).data).GetInt("isLoadProp");
			}
			if (((DataObjectData)((DataBase<Object>)(object)g.data.dataObj).data).ContainsKey("isLoadLuck"))
			{
				isLoadLuck = ((DataObjectData)((DataBase<Object>)(object)g.data.dataObj).data).GetInt("isLoadLuck");
			}
			if (((DataObjectData)((DataBase<Object>)(object)g.data.dataObj).data).ContainsKey("isLoadSkill"))
			{
				isLoadSkill = ((DataObjectData)((DataBase<Object>)(object)g.data.dataObj).data).GetInt("isLoadSkill");
			}
			isStream = CommandTool.GetToggleStream() == 1;
			inputMode = CommandTool.GetToggleMode() == 1;
			isShowAll = CommandTool.GetToggleShowAll() == 1;
			shareUnit.Clear();
			letterUnitList.Clear();
			unitTeams.Clear();
			unitActions.Clear();
			Enumerator<WorldUnitBase> enumerator = g.world.unit.GetUnits(true).GetEnumerator();
			while (enumerator.MoveNext())
			{
				WorldUnitBase current = enumerator.Current;
				if (current.GetLuck(mod_ID + 1) != null)
				{
					unitTeams.Add(current);
				}
				if (current.data.unitData.objData.ContainsKey("Messages") && !Messages.ContainsKey(current.data.unitData.unitID))
				{
					List<Message> value = JsonConvert.DeserializeObject<List<Message>>(current.data.unitData.objData.GetString("Messages"));
					Messages.Add(current.data.unitData.unitID, value);
				}
				if (current.data.unitData.objData.ContainsKey("ActionMessages") && !ActionMessages.ContainsKey(current.data.unitData.unitID))
				{
					try
					{
						List<Message> list = JsonConvert.DeserializeObject<List<Message>>(current.data.unitData.objData.GetString("ActionMessages"));
						for (int i = 0; i < list.Count; i++)
						{
							if (!list[i].role.Equals("assistant"))
							{
								continue;
							}
							try
							{
								if (!list[i].content.Contains("xingdongxuhao"))
								{
									continue;
								}
								AIActionMessageTemp aIActionMessageTemp = JsonParser.SmartParse(list[i].content);
								if (aIActionMessageTemp != null)
								{
									if (StringExtensions.IsEmpty(aIActionMessageTemp.nidedafu))
									{
										Console.WriteLine(list[i].content);
									}
									else
									{
										list[i].content = aIActionMessageTemp.GetXmlText();
									}
								}
							}
							catch (Exception ex)
							{
								Console.WriteLine(ex.ToString());
								Console.WriteLine(list[i].content);
							}
						}
						ActionMessages.Add(current.data.unitData.unitID, list);
					}
					catch (Exception ex2)
					{
						Console.WriteLine("读取交互行动聊天记录出错：" + ex2.ToString());
					}
				}
				if (CommandTool.GetUnitChatShareMode(current) == 1)
				{
					shareUnit.Add(current.data.unitData.unitID);
				}
				if (CommandTool.GetUnitLetterMode(current) == 1)
				{
					letterUnitList.Add(current.data.unitData.unitID);
				}
				if (current.data.unitData.objData.ContainsKey("LetterMessage"))
				{
					letterUnit = current;
				}
				if (current.data.unitData.objData.ContainsKey("ChatMessage"))
				{
					unitActions.Add(current);
				}
			}
			aiPrompt.Load();
			if (!chatGroup.LoadGroup())
			{
				Console.WriteLine("聊天群组加载失败或为空!");
			}
			aIPromptCommand.Load();
			UnitSet.Init();
			InitFindList();
			if (unitBaseInfo.allUnit == null || unitBaseInfo.allUnit.Count < g.world.unit.GetUnits(true).Count)
			{
				unitBaseInfo.InitUnit();
				if (isDebug)
				{
					Console.WriteLine("加载所有NPC数量:" + unitBaseInfo.allUnit.Count);
				}
			}
			if (g.world.playerUnit.data.unitData.objData.ContainsKey("fileIndex"))
			{
				fileIndex = g.world.playerUnit.data.unitData.objData.GetInt("fileIndex");
				Console.WriteLine("背景图数据读取成功，读取的数据为:" + fileIndex);
				if (fileIndex < 0 || fileIndex > fileName.Count - 1)
				{
					fileIndex = 0;
				}
			}
			else
			{
				g.world.playerUnit.data.unitData.objData.SetString("fileIndex", fileIndex);
			}
			InitSetting();
			SstValue();
			g.timer.Time(Action.op_Implicit((Action)delegate
			{
				//IL_005f: Unknown result type (might be due to invalid IL or missing references)
				//IL_0069: Expected O, but got Unknown
				//IL_0088: Unknown result type (might be due to invalid IL or missing references)
				//IL_0092: Expected O, but got Unknown
				List<WorldUnitBase> units = g.world.unit.GetUnits(true);
				units.Remove(g.world.playerUnit);
				Enumerator<WorldUnitBase> enumerator2 = units.GetEnumerator();
				while (enumerator2.MoveNext())
				{
					WorldUnitBase current2 = enumerator2.Current;
					DramaFunctionTool.OptionsFunction("delNPCLuck_" + current2.data.unitData.unitID + "_" + (mod_ID + 2), new DramaFunctionData(current2));
					DramaFunctionTool.OptionsFunction("addNPCLuck_0_" + (mod_ID + 2) + "_-1", new DramaFunctionData(current2));
				}
				Console.WriteLine("刷新气运完成");
			}), 0f, false);
			g.timer.Time(Action.op_Implicit((Action)OnSort), 0f, false);
			g.timer.Time(Action.op_Implicit((Action)delegate
			{
				string text = "";
				List<string> list2 = new List<string>();
				List<string> list3 = new List<string>();
				List<string> list4 = new List<string>();
				Enumerator<ConfItemPropsItem> enumerator2 = ((ConfItemPropsBase)g.conf.itemProps)._allConfList.GetEnumerator();
				while (enumerator2.MoveNext())
				{
					ConfItemPropsItem current2 = enumerator2.Current;
					if (!StringExtensions.IsEmpty(GameTool.LS(current2.name)))
					{
						text = GameTool.LS(current2.name) + ":" + GameTool.LS(current2.desc);
						text = text.Trim();
						text = text.Replace("\n\n", "\n");
						list2.Add(text);
					}
				}
				Enumerator<ConfRoleCreateFeatureItem> enumerator3 = ((ConfRoleCreateFeatureBase)g.conf.roleCreateFeature)._allConfList.GetEnumerator();
				while (enumerator3.MoveNext())
				{
					ConfRoleCreateFeatureItem current3 = enumerator3.Current;
					if (!StringExtensions.IsEmpty(GameTool.LS(current3.name)))
					{
						text = GameTool.LS(current3.name) + ":" + GameTool.LS(current3.tips);
						text = text.Trim();
						text = text.Replace("\n\n", "\n");
						list3.Add(text);
					}
				}
				Enumerator<ConfBattleSkillAttackItem> enumerator4 = ((ConfBattleSkillAttackBase)g.conf.battleSkillAttack)._allConfList.GetEnumerator();
				while (enumerator4.MoveNext())
				{
					ConfBattleSkillAttackItem current4 = enumerator4.Current;
					if (!StringExtensions.IsEmpty(GameTool.LS(current4.name)))
					{
						text = GameTool.LS(current4.name) + ":" + GameTool.LS(current4.desc);
						text = text.Trim();
						text = text.Replace("\n\n", "\n");
						list4.Add(text);
					}
				}
				string contents = string.Join("\n\n", list2);
				File.WriteAllText("XSMSX\\道具列表.txt", contents);
				string contents2 = string.Join("\n\n", list3);
				File.WriteAllText("XSMSX\\气运列表.txt", contents2);
				string contents3 = string.Join("\n\n", list4);
				File.WriteAllText("XSMSX\\功法列表.txt", contents3);
				Console.WriteLine("道具、气运、功法导出完成！");
				worldLogCMD.Init();
				vectorKnowledgeCMD.Load();
				unitLogCMD.Init();
			}), 0f, false);
		}
		catch (Exception ex3)
		{
			Console.WriteLine(ex3.ToString());
		}
	}

	public void ShowActionMsg(WorldUnitBase unit)
	{
		//IL_002c: Unknown result type (might be due to invalid IL or missing references)
		//IL_0036: Expected O, but got Unknown
		//IL_004e: Unknown result type (might be due to invalid IL or missing references)
		//IL_0058: Expected O, but got Unknown
		try
		{
			if ((Object)(object)g.ui.GetUI(new UITypeBase("UIChatAINew", (UILayer)0)) != (Object)null || (Object)(object)g.ui.GetUI(new UITypeBase("UIChatAINewGroup", (UILayer)0)) != (Object)null)
			{
				return;
			}
			string xmlText = unit.data.unitData.objData.GetString("ChatMessage");
			unit.data.unitData.objData.DelString("ChatMessage");
			AIActionMessage aIActionMessage = CommandTool.GetXmlText(xmlText);
			if (aIActionMessage == null)
			{
				return;
			}
			Console.WriteLine("触发主动交互剧情");
			string yourResponse = aIActionMessage.yourResponse;
			string actionName = new ActionList().GetActionName(aIActionMessage.actionNumber);
			aIActionMessage.isApproved = "是";
			aIActionMessage.executeImmediately = "是";
			UnitActionAI unitAction = new UnitActionAI(aIActionMessage, unit);
			string[] array = CommandTool.RegexForOutPut(yourResponse.Replace("\n\n", "\n").Trim('\r', '\n')).Split('\n');
			List<string> list = new List<string>();
			string text = "";
			int num = 0;
			string[] array2 = array;
			foreach (string text2 in array2)
			{
				num++;
				text = text + text2 + "\n";
				if ((text.Length > 200 || num >= 5) && !StringExtensions.IsEmpty(text))
				{
					list.Add(text);
					text = "";
					num = 0;
				}
			}
			if (!StringExtensions.IsEmpty(text))
			{
				list.Add(text);
			}
			if (unitAction.unitA == null || unitAction.unitB == null)
			{
				return;
			}
			UITipItem.AddTip("对方向你发起交互请求：" + actionName, 5f);
			int temp = 0;
			Action onClickop100 = delegate
			{
			};
			onClickop100 = delegate
			{
				if (temp == list.Count - 1)
				{
					if (aIActionMessage != null)
					{
						try
						{
							if (aIActionMessage.isApproved.Contains("是"))
							{
								CommandTool.SetUnitAction(unit, aIActionMessage.actionNumber);
								unitAction.SelectAction();
								g.timer.Time(Action.op_Implicit((Action)delegate
								{
									g.world.unitLog.SaveLogData();
								}), 0f, false);
							}
							if (aIActionMessage.actionNumber == 0 || aIActionMessage.actionNumber == 1 || aIActionMessage.actionNumber == 2)
							{
								UnitActionAI.ShowDrama(g.world.playerUnit, unit, "你还想聊些什么？", "随便聊聊", delegate
								{
									UnitActionAI.SendActionMessage(unit, "随便聊聊");
								});
							}
						}
						catch (Exception ex2)
						{
							Console.WriteLine(ex2.Message);
						}
					}
				}
				else if (temp < list.Count - 1)
				{
					temp++;
					if (temp == list.Count - 1)
					{
						ShowDrama(g.world.playerUnit, unit, list[temp], "确定", onClickop100);
					}
					else
					{
						ShowDrama(g.world.playerUnit, unit, list[temp], "继续", onClickop100);
					}
				}
			};
			if (temp == list.Count - 1)
			{
				ShowDrama(g.world.playerUnit, unit, list[temp], "确定", onClickop100);
			}
			else
			{
				ShowDrama(g.world.playerUnit, unit, list[temp], "继续", onClickop100);
			}
		}
		catch (Exception ex)
		{
			Console.WriteLine(ex.Message);
			Console.WriteLine("剧情打开出错");
		}
	}

	public void ShowDrama(WorldUnitBase leftUnit, WorldUnitBase rightUnit, string Text, string opText, Action action)
	{
		//IL_0009: Unknown result type (might be due to invalid IL or missing references)
		//IL_0016: Unknown result type (might be due to invalid IL or missing references)
		//IL_0029: Unknown result type (might be due to invalid IL or missing references)
		//IL_003b: Unknown result type (might be due to invalid IL or missing references)
		//IL_0049: Unknown result type (might be due to invalid IL or missing references)
		//IL_0055: Unknown result type (might be due to invalid IL or missing references)
		int num = mod_ID + 1;
		UICustomDramaDyn val = new UICustomDramaDyn(num);
		int num2 = mod_ID + 1;
		((UICustomDramaBase)val).dramaData.dialogueOptions[num2] = opText;
		((UICustomDramaBase)val).dramaData.dialogueText[num] = Text;
		val.SetOptionCall(num2, Action.op_Implicit(action));
		((UICustomDramaBase)val).dramaData.unitLeft = leftUnit;
		((UICustomDramaBase)val).dramaData.unitRight = rightUnit;
		val.OpenUI();
	}

	private void MapMainAddBtn()
	{
		//IL_00b8: Unknown result type (might be due to invalid IL or missing references)
		//IL_00a7: Unknown result type (might be due to invalid IL or missing references)
		//IL_0107: Unknown result type (might be due to invalid IL or missing references)
		//IL_010d: Unknown result type (might be due to invalid IL or missing references)
		UIMapMain uI = g.ui.GetUI<UIMapMain>(UIType.MapMain);
		if ((Object)(object)uI != (Object)null && (Object)(object)((Component)((UIMapMainBase)uI).playerInfo.btnPlayer).transform.parent.Find("BtnSSCYAI") == (Object)null)
		{
			GameObject obj = Object.Instantiate<GameObject>(g.res.Load<GameObject>("UI/SSCY"), ((Component)((UIMapMainBase)uI).playerInfo.btnPlayer).transform.parent);
			((Object)obj).name = "BtnSSCYAI";
			obj.transform.localPosition = ((g.world.playerUnit.data.dynUnitData.GetGrade() >= 10) ? new Vector3(430f, 130f) : new Vector3(430f, 130f));
			((UnityEvent)obj.GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
			{
				//IL_0010: Unknown result type (might be due to invalid IL or missing references)
				//IL_001a: Expected O, but got Unknown
				ClassInjector.RegisterTypeInIl2Cpp<UIAIMenu>();
				((Component)g.ui.OpenUI(new UITypeBase("UIAIMenu", (UILayer)0))).gameObject.AddComponent<UIAIMenu>().InitData(g.world.playerUnit);
			}));
			obj.gameObject.AddComponent<UISkyTipEffect>().InitData("神识传音功能菜单，按Home键快捷打开!", default(Vector3));
		}
	}

	public void OnOpenUIEnd(ETypeData e)
	{
		//IL_0111: Unknown result type (might be due to invalid IL or missing references)
		//IL_01c8: Unknown result type (might be due to invalid IL or missing references)
		//IL_01d2: Expected O, but got Unknown
		//IL_0210: Unknown result type (might be due to invalid IL or missing references)
		//IL_021a: Expected O, but got Unknown
		//IL_0258: Unknown result type (might be due to invalid IL or missing references)
		//IL_0262: Expected O, but got Unknown
		//IL_043e: Unknown result type (might be due to invalid IL or missing references)
		OpenUIEnd val = ((Il2CppObjectBase)e).Cast<OpenUIEnd>();
		if (val.uiType.uiName == UIType.PlayerTask.uiName)
		{
			UIPlayerTask uI = g.ui.GetUI<UIPlayerTask>(UIType.PlayerTask);
			if (!((Object)(object)uI != (Object)null))
			{
				return;
			}
			SendUnitDieLetter();
			uI.uiEmail.UpdateUI();
			((UnityEvent)((UIPlayerTaskBase)uI).btnClose2.onClick).AddListener(UnityAction.op_Implicit((Action)delegate
			{
				try
				{
					if (Patch_UIPlayerEmailInfo.coroutine != null)
					{
						g.timer.Stop(Patch_UIPlayerEmailInfo.coroutine);
						Patch_UIPlayerEmailInfo.coroutine = null;
					}
					Patch_UIPlayerEmailInfo.isSpeek = false;
					if (mciSendString("stop my_mp3", null, 0, IntPtr.Zero) != 0)
					{
						Console.WriteLine("停止失败！");
					}
					else
					{
						Console.WriteLine("已停止！");
					}
					if (mciSendString("close my_mp3", null, 0, IntPtr.Zero) != 0)
					{
						Console.WriteLine("关闭失败！");
					}
					else
					{
						Console.WriteLine("已关闭！");
					}
				}
				catch (Exception ex3)
				{
					Console.WriteLine(ex3.Message);
				}
			}));
			return;
		}
		if (val.uiType.uiName == UIType.BattleExit.uiName)
		{
			UIBattleExit uIBattleExit = ((Il2CppObjectBase)val.ui).Cast<UIBattleExit>();
			if ((Object)(object)uIBattleExit != (Object)null)
			{
				GameObject obj = Object.Instantiate<GameObject>(((Component)((UIBattleExitBase)uIBattleExit).btnGameExit).gameObject);
				Transform transform = ((Component)uIBattleExit).transform;
				obj.transform.SetParent(transform, false);
				obj.SetActive(true);
				obj.transform.localPosition = new Vector3(200f, 0f);
				((Object)obj).name = "exitBattle";
				obj.layer = int.MaxValue;
				obj.GetComponentInChildren<Text>().text = "离开战斗";
				Button componentInChildren = obj.GetComponentInChildren<Button>();
				((Selectable)componentInChildren).interactable = true;
				((UnityEventBase)componentInChildren.onClick).RemoveAllListeners();
				((UnityEvent)componentInChildren.onClick).AddListener(UnityAction.op_Implicit((Action)delegate
				{
					((UnityEvent)((UIBattleExitBase)uIBattleExit).btnExit.onClick).Invoke();
				}));
			}
			return;
		}
		if (val.uiType.uiName == UIType.MapMain.uiName)
		{
			MapMainAddBtn();
			try
			{
				if (UIFightOne.isFight && FightType == 1)
				{
					JudgeFightWinner(ref UIFightOne.matchUnits, ref UIFightOne.selIndex);
					ClassInjector.RegisterTypeInIl2Cpp<UIFightOne>();
					((Component)g.ui.OpenUI(new UITypeBase("UIFightOne", (UILayer)0))).gameObject.AddComponent<UIFightOne>().InitData();
				}
				if (UIFight.isFight && FightType == 2)
				{
					JudgeFightWinner(ref UIFight.matchUnits, ref UIFight.selIndex);
					ClassInjector.RegisterTypeInIl2Cpp<UIFight>();
					((Component)g.ui.OpenUI(new UITypeBase("UIFight", (UILayer)0))).gameObject.AddComponent<UIFight>().InitData();
				}
				if (UIFightDiy.isFight && FightType == 3)
				{
					JudgeFightWinner(ref UIFightDiy.matchUnits, ref UIFightDiy.selIndex);
					ClassInjector.RegisterTypeInIl2Cpp<UIFightDiy>();
					((Component)g.ui.OpenUI(new UITypeBase("UIFightDiy", (UILayer)0))).gameObject.AddComponent<UIFightDiy>().InitData();
				}
				return;
			}
			catch (Exception ex)
			{
				Console.WriteLine(ex.ToString());
				return;
			}
		}
		if (val.uiType.uiName == UIType.GameSetting.uiName)
		{
			SendUnitDieLetter();
		}
		else if (val.uiType.uiName == UIType.School.uiName)
		{
			SengGradePropToSchool();
		}
		else if (val.uiType.uiName == UIType.Town.uiName)
		{
			isSend = true;
			new OnOpenTown(val);
		}
		else if (val.uiType.uiName == UIType.PlayerInfo.uiName)
		{
			PlayerInfoUICMD();
		}
		else if (val.uiType.uiName == UIType.CreatePlayer.uiName)
		{
			CreatePlayerUICMD();
		}
		else if (val.uiType.uiName == UIType.ModDress.uiName)
		{
			ModDressUICMD();
		}
		else
		{
			if (!(val.uiType.uiName == UIType.NPCInfo.uiName))
			{
				return;
			}
			ShowNPCTeam();
			NPCInfoUICMD();
			UINPCInfo ui = g.ui.GetUI<UINPCInfo>(UIType.NPCInfo);
			if ((Object)(object)ui != (Object)null)
			{
				if (ui.unit.data.unitData.unitID.Equals(g.world.playerUnit.data.unitData.unitID))
				{
					return;
				}
				try
				{
					GameObject obj2 = Object.Instantiate<GameObject>(g.res.Load<GameObject>("UI/SSCY"), ((Component)ui).transform);
					obj2.transform.localPosition = new Vector3(-770f, 386f);
					((Object)obj2).name = "MOD_XSMSX666";
					Button componentInChildren2 = obj2.GetComponentInChildren<Button>();
					((UnityEventBase)componentInChildren2.onClick).RemoveAllListeners();
					((UnityEvent)componentInChildren2.onClick).AddListener(UnityAction.op_Implicit((Action)delegate
					{
						//IL_0010: Unknown result type (might be due to invalid IL or missing references)
						//IL_001a: Expected O, but got Unknown
						try
						{
							ClassInjector.RegisterTypeInIl2Cpp<UIChatAINew>();
							((Component)g.ui.OpenUI(new UITypeBase("UIChatAINew", (UILayer)0))).gameObject.AddComponent<UIChatAINew>().InitData(ui.unit);
						}
						catch (Exception ex3)
						{
							Console.WriteLine(ex3.ToString());
							Console.WriteLine("ButtonOK出错");
						}
					}));
				}
				catch (Exception ex2)
				{
					Console.WriteLine(ex2.Message);
				}
				CommandTool.AddToLog(ui.unit, g.world.playerUnit, isShow: false);
				string prefix = $"[{CommandTool.ConvertToYearsMonths(g.world.run.roundMonth + 1)}{g.world.run.roundDay + 1}日]";
				new AIUnitSet();
				int num = CommandTool.GetAIUnitSet(ui.unit).actionProp;
				int num2 = ((ui.unit.data.unitData.relationData.GetIntim(g.world.playerUnit.data.unitData.unitID) > g.world.playerUnit.data.unitData.relationData.GetIntim(ui.unit.data.unitData.unitID)) ? ui.unit.data.unitData.relationData.GetIntim(g.world.playerUnit.data.unitData.unitID) : g.world.playerUnit.data.unitData.relationData.GetIntim(ui.unit.data.unitData.unitID));
				if (num2 < 120 && num2 > -120)
				{
					num /= 2;
					if (ui.unit.data.dynUnitData.GetGrade() > g.world.playerUnit.data.dynUnitData.GetGrade())
					{
						num /= ui.unit.data.dynUnitData.GetGrade() - g.world.playerUnit.data.dynUnitData.GetGrade() + 1;
					}
				}
				Console.WriteLine("最终NPC主动交互概率：" + num);
				if (ui.unit.data.unitData.objData.ContainsKey("ChatMessage"))
				{
					ShowActionMsg(ui.unit);
				}
				else if (_random.Next(0, 100) < num)
				{
					g.timer.Time(Action.op_Implicit((Action)async delegate
					{
						try
						{
							Console.WriteLine("触发NPC主动交互!");
							int unitAction = CommandTool.GetUnitAction(ui.unit);
							await CommandTool.SendToDeepSeek(question: (unitAction != 0) ? ("行为请求：" + prefix + "(你是" + ui.unit.data.unitData.propertyData.GetName() + "),结合上下文及双方信息，你现在主动向用户发起行动交互请求，禁止内容重复，且isApproved字段为是。（actionNumber字段内容禁止为:" + unitAction + ")") : ("行为请求：" + prefix + "(你是" + ui.unit.data.unitData.propertyData.GetName() + "),结合上下文及双方信息，你现在主动向用户发起行动交互请求，禁止内容重复，且isApproved字段为是。"), unit: ui.unit, role: "user");
						}
						catch (Exception ex3)
						{
							Console.WriteLine(ex3.Message ?? "");
						}
					}), 0f, false);
				}
			}
			OpenNPCInfo(e);
		}
	}

	public static void ShowNPCTeam()
	{
		//IL_0166: Unknown result type (might be due to invalid IL or missing references)
		//IL_016b: Unknown result type (might be due to invalid IL or missing references)
		//IL_016f: Unknown result type (might be due to invalid IL or missing references)
		//IL_017c: Unknown result type (might be due to invalid IL or missing references)
		//IL_0193: Unknown result type (might be due to invalid IL or missing references)
		//IL_01aa: Unknown result type (might be due to invalid IL or missing references)
		//IL_01c9: Unknown result type (might be due to invalid IL or missing references)
		//IL_01cf: Unknown result type (might be due to invalid IL or missing references)
		//IL_0115: Unknown result type (might be due to invalid IL or missing references)
		//IL_011b: Unknown result type (might be due to invalid IL or missing references)
		UINPCInfo uI = g.ui.GetUI<UINPCInfo>(UIType.NPCInfo);
		List<string> list = new List<string>();
		if (uI.unit.data.unitData.objData.ContainsKey("AITem"))
		{
			list = JsonConvert.DeserializeObject<List<string>>(uI.unit.data.unitData.objData.GetString("AITem"));
		}
		string text = "队友：";
		foreach (string item in list)
		{
			WorldUnitBase unit = g.world.unit.GetUnit(item, true);
			if (unit != null)
			{
				text = text + unit.GetName() + "  ";
			}
		}
		Transform transform = ((Property)uI.uiProperty).goGroupRoot.transform;
		for (int i = 0; i < transform.childCount; i++)
		{
			Transform child = transform.GetChild(i);
			if (((Object)child).name == "队友")
			{
				((Component)child).GetComponent<Text>().text = text;
				((Component)child).GetComponent<UISkyTipEffect>().OnDestroy();
				UISkyTipEffect obj = ((Component)child).gameObject.AddComponent<UISkyTipEffect>();
				obj.InitData(text, default(Vector3));
				obj.isLeftAligen = true;
				return;
			}
		}
		Text obj2 = Object.Instantiate<Text>(((UnitInfo)uI.uiUnitInfo).textStand1, transform, false);
		((Object)((Component)obj2).transform).name = "队友";
		Vector2 sizeDelta = ((Component)((Component)obj2).transform).GetComponent<RectTransform>().sizeDelta;
		((Vector2)(ref sizeDelta)).Set(sizeDelta.x * 2f, sizeDelta.y);
		((Component)((Component)obj2).transform).GetComponent<RectTransform>().sizeDelta = sizeDelta;
		((Component)obj2).transform.localPosition = new Vector3(255f, 90f);
		obj2.text = text;
		UISkyTipEffect obj3 = ((Component)obj2).gameObject.AddComponent<UISkyTipEffect>();
		obj3.InitData(text, default(Vector3));
		obj3.isLeftAligen = true;
	}

	private void OpenNPCInfo(ETypeData data)
	{
		//IL_00c6: Unknown result type (might be due to invalid IL or missing references)
		int confIndexID = mod_ID;
		UINPCInfo ui = g.ui.GetUI<UINPCInfo>(UIType.NPCInfo);
		int intim = ui.unit.data.unitData.relationData.GetIntim(g.world.playerUnit);
		if (!((Object)(object)ui != (Object)null) || (confInofFile.confInof.isButtonNeedFriend && intim < 60))
		{
			return;
		}
		GameObject obj = Object.Instantiate<GameObject>(((UnitInfo)ui.uiUnitInfo).goButton1);
		Transform transform = ((Component)ui).transform;
		obj.transform.SetParent(transform, false);
		obj.SetActive(true);
		obj.transform.localPosition = new Vector3(-770f, 335f);
		((Object)obj).name = "MOD_XSMSX999";
		obj.layer = int.MaxValue;
		obj.GetComponentInChildren<Text>().text = "私语";
		Button componentInChildren = obj.GetComponentInChildren<Button>();
		((UnityEventBase)componentInChildren.onClick).RemoveAllListeners();
		((UnityEvent)componentInChildren.onClick).AddListener(UnityAction.op_Implicit((Action)delegate
		{
			//IL_0008: Unknown result type (might be due to invalid IL or missing references)
			//IL_000e: Expected O, but got Unknown
			UICustomDramaDyn val = new UICustomDramaDyn(confIndexID + 7);
			int num = confIndexID + 1;
			int num2 = confIndexID + 2;
			int num3 = confIndexID + 3;
			int num4 = confIndexID + 4;
			int num5 = confIndexID + 5;
			int num6 = confIndexID + 6;
			int num7 = confIndexID + 7;
			int num8 = confIndexID + 8;
			int num9 = confIndexID + 9;
			int num10 = confIndexID + 50;
			int num11 = confIndexID + 51;
			int num12 = confIndexID + 52;
			Action action = delegate
			{
				MoreOption(ui);
			};
			Action action2 = delegate
			{
				//IL_0054: Unknown result type (might be due to invalid IL or missing references)
				//IL_0059: Unknown result type (might be due to invalid IL or missing references)
				//IL_006a: Unknown result type (might be due to invalid IL or missing references)
				//IL_0076: Expected O, but got Unknown
				//IL_0028: Unknown result type (might be due to invalid IL or missing references)
				//IL_002d: Unknown result type (might be due to invalid IL or missing references)
				//IL_003e: Unknown result type (might be due to invalid IL or missing references)
				//IL_004a: Expected O, but got Unknown
				_ = confIndexID;
				if (ui.unit.GetLuck(mod_ID + 1) == null)
				{
					DramaTool.OpenDrama(confIndexID + 2, new DramaData
					{
						unitLeft = ui.unit,
						unitRight = null
					});
				}
				else
				{
					DramaTool.OpenDrama(confIndexID + 4, new DramaData
					{
						unitLeft = ui.unit,
						unitRight = null
					});
				}
			};
			Action action3 = delegate
			{
				int num13 = 8;
				if (g.world.run.roundDayResidue >= 8)
				{
					g.world.run.AddDay(num13, true);
					int grade = ui.unit.data.dynUnitData.GetGrade();
					int grade2 = g.world.playerUnit.data.dynUnitData.GetGrade();
					if (grade <= grade2)
					{
						int num14 = g.conf.roleGrade.GetNextGradeItem(grade).exp - ui.unit.data.unitData.propertyData.exp;
						if (num14 > 0)
						{
							ui.unit.data.unitData.propertyData.AddExp(num14 / 3);
							PropertyData propertyData = ui.unit.data.unitData.propertyData;
							propertyData.abilityPoint += grade * 10;
							string text = $"经过一番论道和分享，对方经验增长{num14 / 3}点,道点增长{grade * 10}点。";
							g.ui.OpenUI<UITextInfo>(UIType.TextInfo).InitData("温馨提示", text, "", (Action)null);
						}
						else
						{
							PropertyData propertyData2 = ui.unit.data.unitData.propertyData;
							propertyData2.abilityPoint += grade * 20;
							string text2 = $"经过一番论道和分享，对方的瓶颈似乎有些松动,同时道点得到大幅增长，提高{grade * 20}点。";
							g.ui.OpenUI<UITextInfo>(UIType.TextInfo).InitData("温馨提示", text2, "", (Action)null);
						}
					}
					else
					{
						int num15 = g.conf.roleGrade.GetNextGradeItem(grade2).exp - g.world.playerUnit.data.unitData.propertyData.exp;
						if (num15 > 0)
						{
							g.world.playerUnit.data.unitData.propertyData.AddExp(num15 / 3);
							PropertyData propertyData3 = g.world.playerUnit.data.unitData.propertyData;
							propertyData3.abilityPoint += grade2 * 10;
							string text3 = $"对方的实力比你高，你所分享的经验对他无用，借此机会，对方向你分享自身的感悟，你的经验提升了{num15 / 3}点，道点提示了{grade2 * 10}点。";
							g.ui.OpenUI<UITextInfo>(UIType.TextInfo).InitData("温馨提示", text3, "", (Action)null);
						}
						else
						{
							PropertyData propertyData4 = ui.unit.data.unitData.propertyData;
							propertyData4.abilityPoint += grade2 * 20;
							string text4 = $"对方的实力比你高，你所分享的经验对他无用，借此机会，对方向你分享自身的感悟，你的瓶颈似乎有些松动,同时道点得到大幅增长，提高{grade2 * 20}点。";
							g.ui.OpenUI<UITextInfo>(UIType.TextInfo).InitData("温馨提示", text4, "", (Action)null);
						}
					}
				}
				else
				{
					g.ui.OpenUI<UITextInfo>(UIType.TextInfo).InitData("温馨提示", "本月时间已不足，待下月再相约论道。", "", (Action)null);
				}
			};
			Action action4 = delegate
			{
				UITeachSkillPlayer uiTeachSkillPlayer = g.ui.OpenUI<UITeachSkillPlayer>(UIType.TeachSkillPlayer);
				uiTeachSkillPlayer.InitData(g.world.playerUnit, Action<ActionMartialData>.op_Implicit((Action<ActionMartialData>)delegate(ActionMartialData actionMartialData)
				{
					//IL_0060: Unknown result type (might be due to invalid IL or missing references)
					//IL_0065: Unknown result type (might be due to invalid IL or missing references)
					//IL_006c: Unknown result type (might be due to invalid IL or missing references)
					//IL_0074: Expected O, but got Unknown
					if (actionMartialData != null)
					{
						if (isDebug)
						{
							Console.WriteLine("选择的技能名称为:" + uiTeachSkillPlayer.selectSkill.data.propsItem.name);
						}
						if (uiTeachSkillPlayer.selectSkill != null)
						{
							UnitActionRoleTeachSkill val2 = new UnitActionRoleTeachSkill(ui.unit, uiTeachSkillPlayer.selectSkill, (TeachType)0)
							{
								isAutoAddLog = true,
								isTip = true
							};
							g.world.playerUnit.CreateAction((UnitActionBase)(object)val2, true);
							g.world.playerUnit.data.unitData.propertyData.DelAddLuck(9192);
							ui.unit.data.unitData.propertyData.DelAddLuck(9193);
						}
					}
					else
					{
						UITipItem.AddTip("操作取消！", 2f);
					}
				}));
			};
			Action action5 = delegate
			{
				UITeachSkill uiTeachSkill = g.ui.OpenUI<UITeachSkill>(UIType.TeachSkill);
				uiTeachSkill.InitData(ui.unit, Action<ActionMartialData>.op_Implicit((Action<ActionMartialData>)delegate(ActionMartialData actionMartialData)
				{
					//IL_0030: Unknown result type (might be due to invalid IL or missing references)
					//IL_0035: Unknown result type (might be due to invalid IL or missing references)
					//IL_003c: Unknown result type (might be due to invalid IL or missing references)
					//IL_0044: Expected O, but got Unknown
					if (actionMartialData != null)
					{
						if (uiTeachSkill.selectSkill != null)
						{
							UnitActionRoleTeachSkill val2 = new UnitActionRoleTeachSkill(ui.unit, uiTeachSkill.selectSkill, (TeachType)1)
							{
								isAutoAddLog = true,
								isTip = true
							};
							g.world.playerUnit.CreateAction((UnitActionBase)(object)val2, true);
							g.world.playerUnit.data.unitData.propertyData.DelAddLuck(9193);
							ui.unit.data.unitData.propertyData.DelAddLuck(9192);
						}
					}
					else
					{
						UITipItem.AddTip("操作取消！", 2f);
					}
				}));
			};
			Action action6 = delegate
			{
				//IL_0010: Unknown result type (might be due to invalid IL or missing references)
				//IL_001a: Expected O, but got Unknown
				ClassInjector.RegisterTypeInIl2Cpp<UIUnitCMD>();
				UIUnitCMD uIUnitCMD = ((Component)g.ui.OpenUI(new UITypeBase("UIUnitCMD", (UILayer)0))).gameObject.AddComponent<UIUnitCMD>();
				List<string> unitID = new List<string> { ui.unit.data.unitData.unitID };
				uIUnitCMD.InitData(unitID);
			};
			Action action7 = delegate
			{
				//IL_0011: Unknown result type (might be due to invalid IL or missing references)
				//IL_001b: Expected O, but got Unknown
				if (UnitConditionTool.Condition("gradePhase_0_3_3", new UnitConditionData(ui.unit, (WorldUnitBase)null)))
				{
					Data data2 = ui.unit.data.unitData.aiData.data;
					data2.action1038AddScore *= 10000;
					Data data3 = ui.unit.data.unitData.aiData.data;
					data3.upGradeMaxQualityWeight += 10000;
					if (WorldUnitAIAction1038.UpGrade(ui.unit, (Action<List<DataStruct<int, ConfRoleGradeItem>>>)null))
					{
						g.world.playerUnit.data.unitData.propertyData.mp = 0;
						UITipItem.AddTip(g.world.playerUnit.data.unitData.propertyData.GetName() + "消耗自身灵力，向" + ui.unit.data.unitData.propertyData.GetName() + "传功成功!", 2f);
					}
					else
					{
						UITipItem.AddTip(g.world.playerUnit.data.unitData.propertyData.GetName() + "向" + ui.unit.data.unitData.propertyData.GetName() + "传功失败!", 2f);
					}
				}
				else
				{
					UITipItem.AddTip(g.world.playerUnit.data.unitData.propertyData.GetName() + "尚未达到境界后期！" + ui.unit.data.unitData.propertyData.GetName() + "境界提升失败!", 2f);
				}
			};
			Action action8 = delegate
			{
				Action showTips = delegate
				{
					if (tips.Count > 0)
					{
						UITipItem3.AddTip(tips[0], "", 1f);
						tips.RemoveAt(0);
					}
				};
				UIPropSelect obj2 = g.ui.OpenUI<UIPropSelect>(UIType.PropSelect);
				obj2.isOKCost = false;
				obj2.onOKCall = Action.op_Implicit((Action)delegate
				{
					//IL_001e: Unknown result type (might be due to invalid IL or missing references)
					//IL_0024: Invalid comparison between Unknown and I4
					//IL_05e5: Unknown result type (might be due to invalid IL or missing references)
					//IL_05eb: Invalid comparison between Unknown and I4
					//IL_05f1: Unknown result type (might be due to invalid IL or missing references)
					//IL_05f8: Expected O, but got Unknown
					//IL_0643: Unknown result type (might be due to invalid IL or missing references)
					//IL_0047: Unknown result type (might be due to invalid IL or missing references)
					//IL_004e: Expected O, but got Unknown
					//IL_01c4: Unknown result type (might be due to invalid IL or missing references)
					//IL_01cb: Expected O, but got Unknown
					//IL_031f: Unknown result type (might be due to invalid IL or missing references)
					//IL_0326: Expected O, but got Unknown
					//IL_047f: Unknown result type (might be due to invalid IL or missing references)
					//IL_0486: Expected O, but got Unknown
					float num13 = 0f;
					Enumerator<PropsData> enumerator = UIPropSelect.allSlectItems.GetEnumerator();
					while (enumerator.MoveNext())
					{
						PropsData current = enumerator.Current;
						if ((int)current.propsType == 1)
						{
							int propsCount = current.propsCount;
							if (current.propsItem.type == 1)
							{
								for (int i = 0; i < propsCount; i++)
								{
									UnitActionPropUse val2 = new UnitActionPropUse(current);
									val2.isCheckCondition = false;
									ui.unit.CreateAction((UnitActionBase)(object)val2, false);
								}
								if (isDebug)
								{
									Console.WriteLine("喂食成功。喂食道具：" + GameTool.LS(current.propsItem.name) + "*" + propsCount);
								}
								((DataProps)g.world.playerUnit.data.unitData.propData).DelProps(current.propsID, propsCount);
								tips.Add("喂食道具 " + GameTool.LS(current.propsItem.name) + "  *" + propsCount);
								g.timer.Time(Action.op_Implicit(showTips), num13, false);
								num13 += 2f;
							}
							else if (current.propsItem.type == 3 && current.propsItem.className == 301)
							{
								((DataProps)ui.unit.data.unitData.propData).RewardPropsIDType(current.propsID, propsCount);
								List<PropsData> props = ((DataProps)ui.unit.data.unitData.propData).GetProps(current.propsID);
								if (props != null && props.Count > 0)
								{
									UnitActionEquipRingChange val3 = new UnitActionEquipRingChange(props[0].soleID);
									ui.unit.CreateAction((UnitActionBase)(object)val3, false);
									if (isDebug)
									{
										Console.WriteLine("喂食成功。更换戒指：" + GameTool.LS(current.propsItem.name));
									}
									((DataProps)g.world.playerUnit.data.unitData.propData).DelProps(current.propsID, propsCount);
									tips.Add("喂食道具 " + GameTool.LS(current.propsItem.name) + "  *" + propsCount);
									g.timer.Time(Action.op_Implicit(showTips), num13, false);
									num13 += 2f;
								}
							}
							else if (current.propsItem.type == 3 && current.propsItem.className == 302)
							{
								((DataProps)ui.unit.data.unitData.propData).RewardPropsIDType(current.propsID, propsCount);
								List<PropsData> props2 = ((DataProps)ui.unit.data.unitData.propData).GetProps(current.propsID);
								if (props2 != null && props2.Count > 0)
								{
									UnitActionEquipEquip val4 = new UnitActionEquipEquip(props2[0], 1, false);
									ui.unit.CreateAction((UnitActionBase)(object)val4, false);
									if (isDebug)
									{
										Console.WriteLine("喂食成功。更换坐骑：" + GameTool.LS(current.propsItem.name));
									}
									((DataProps)g.world.playerUnit.data.unitData.propData).DelProps(current.propsID, propsCount);
									tips.Add("喂食道具 " + GameTool.LS(current.propsItem.name) + "  *" + propsCount);
									g.timer.Time(Action.op_Implicit(showTips), num13, false);
									num13 += 2f;
								}
							}
							else if (current.propsItem.type == 3 && current.propsItem.className == 304)
							{
								((DataProps)ui.unit.data.unitData.propData).RewardPropsIDType(current.propsID, propsCount);
								List<PropsData> props3 = ((DataProps)ui.unit.data.unitData.propData).GetProps(((ConfBaseItem)current.propsItem).id);
								if (props3 != null && props3.Count > 0)
								{
									UnitActionEquipEquip val5 = new UnitActionEquipEquip(props3[0], 2, false);
									ui.unit.CreateAction((UnitActionBase)(object)val5, false);
									if (isDebug)
									{
										Console.WriteLine("喂食成功。更换衣服：" + GameTool.LS(current.propsItem.name));
									}
									((DataProps)g.world.playerUnit.data.unitData.propData).DelProps(current.propsID, propsCount);
									tips.Add("喂食道具 " + GameTool.LS(current.propsItem.name) + "  *" + propsCount);
									g.timer.Time(Action.op_Implicit(showTips), num13, false);
									num13 += 2f;
								}
							}
							else
							{
								((DataProps)ui.unit.data.unitData.propData).RewardPropsIDType(current.propsID, propsCount);
								((DataProps)g.world.playerUnit.data.unitData.propData).DelProps(current.propsID, propsCount);
								tips.Add("喂食道具 " + GameTool.LS(current.propsItem.name) + "  *" + propsCount);
								g.timer.Time(Action.op_Implicit(showTips), num13, false);
								num13 += 2f;
							}
						}
						else if ((int)current.propsType == 2)
						{
							UnitActionPropMartialStudy val6 = new UnitActionPropMartialStudy(current);
							val6.Init(ui.unit);
							MartialData martialData = val6.martialData;
							if (martialData != null)
							{
								ConfBattleSkillCostItem item = g.conf.battleSkillCost.GetItem(martialData.martialInfo.grade, martialData.martialInfo.level, martialData.martialType);
								val6.exp = g.conf.battleSkillCost.GetExp(item, martialData);
								val6.money = 0;
								((UnitActionBase)val6).day = 0;
								val6.costMood = 0;
								val6.checkBas = false;
								if (isDebug)
								{
									Console.WriteLine("martialData不为空。");
								}
								ui.unit.CreateAction((UnitActionBase)(object)val6, false);
								if (val6.isStudyComplete)
								{
									if (isDebug)
									{
										Console.WriteLine("喂食成功。学习功法：" + GameTool.LS(martialData.martialInfo.name));
									}
									((DataProps)g.world.playerUnit.data.unitData.propData).DelProps(current.soleID);
									tips.Add("喂食道具 " + GameTool.LS(martialData.martialInfo.name) + "  *" + 1);
									g.timer.Time(Action.op_Implicit(showTips), num13, false);
									num13 += 2f;
								}
								else
								{
									if (isDebug)
									{
										Console.WriteLine("喂食失败。学习功法条件不符合：" + GameTool.LS(martialData.martialInfo.name));
									}
									tips.Add("喂食道具失败，对方未满足功法学习条件 " + GameTool.LS(martialData.martialInfo.name) + "  *" + 1);
									g.timer.Time(Action.op_Implicit(showTips), num13, false);
									num13 += 2f;
								}
							}
						}
					}
				});
			};
			Action action9 = delegate
			{
				//IL_0100: Unknown result type (might be due to invalid IL or missing references)
				//IL_010a: Expected O, but got Unknown
				if (g.world.playerUnit.data.dynUnitData.GetGrade() > ui.unit.data.dynUnitData.GetGrade())
				{
					UITipItem.AddTip("你的境界比对方高，你可以掠夺对方背包道具！", 3f);
					UIPropSelect val2 = g.ui.OpenUI<UIPropSelect>(UIType.PropSelect);
					val2.allItems.ClearAllProps();
					Enumerator<PropsData> enumerator = ((DataProps)ui.unit.data.unitData.propData).allProps.GetEnumerator();
					while (enumerator.MoveNext())
					{
						PropsData current = enumerator.Current;
						val2.allItems.AddProps(current);
					}
					val2.isOKCost = false;
					val2.isContainHide = true;
					val2.onOKCall = Action.op_Implicit((Action)delegate
					{
						Enumerator<PropsData> enumerator2 = UIPropSelect.allSlectItems.GetEnumerator();
						while (enumerator2.MoveNext())
						{
							PropsData current2 = enumerator2.Current;
							g.world.playerUnit.data.unitData.propData.AddProps(current2.Clone());
							((DataProps)ui.unit.data.unitData.propData).DelProps(current2.propsID, current2.propsCount);
							UITipItem.AddTip($"{current2.propsInfoBase.name}*{current2.propsCount}掠夺成功！", 2f);
						}
					});
					val2.UpdateUI();
				}
				else
				{
					UnitActionRoleAttack unitActionRoleAttack = new UnitActionRoleAttack(ui.unit);
					((UnitActionRoleToUnitBase)unitActionRoleAttack).isAutoAddLog = true;
					((UnitActionRoleBase)unitActionRoleAttack).AddEndCall(Action.op_Implicit((Action)delegate
					{
						//IL_00af: Unknown result type (might be due to invalid IL or missing references)
						//IL_00b4: Unknown result type (might be due to invalid IL or missing references)
						//IL_00ba: Expected O, but got Unknown
						try
						{
							if (unitActionRoleAttack.isComplete && unitActionRoleAttack.roleBattle.winUnit.data.unitData.unitID.Equals(g.world.playerUnit.data.unitData.unitID))
							{
								if (ui.unit.data.unitData.pointX != g.world.playerUnit.data.unitData.pointX)
								{
									UITipItem.AddTip("对方尝试逃跑，被你半路拦截下来了！", 2f);
									UnitActionMoveNPC val3 = new UnitActionMoveNPC(g.world.playerUnit.data.unitData.GetPoint());
									ui.unit.CreateAction((UnitActionBase)(object)val3, true);
								}
								UITipItem.AddTip("你将对方打败了，强行触发掠夺！", 2f);
								UIPropSelect val4 = g.ui.OpenUI<UIPropSelect>(UIType.PropSelect);
								val4.allItems.ClearAllProps();
								Enumerator<PropsData> enumerator2 = ((DataProps)ui.unit.data.unitData.propData).allProps.GetEnumerator();
								while (enumerator2.MoveNext())
								{
									PropsData current2 = enumerator2.Current;
									val4.allItems.AddProps(current2);
								}
								val4.isOKCost = false;
								val4.isContainHide = true;
								val4.onOKCall = Action.op_Implicit((Action)delegate
								{
									Enumerator<PropsData> enumerator3 = UIPropSelect.allSlectItems.GetEnumerator();
									while (enumerator3.MoveNext())
									{
										PropsData current3 = enumerator3.Current;
										g.world.playerUnit.data.unitData.propData.AddProps(current3.Clone());
										((DataProps)ui.unit.data.unitData.propData).DelProps(current3.propsID, current3.propsCount);
										UITipItem.AddTip($"{current3.propsInfoBase.name}*{current3.propsCount}掠夺成功！", 2f);
									}
								});
								val4.UpdateUI();
							}
						}
						catch (Exception ex)
						{
							Console.WriteLine(ex.ToString());
						}
					}));
					g.world.playerUnit.CreateAction((UnitActionBase)(object)unitActionRoleAttack, true);
				}
			};
			Action action10 = delegate
			{
				//IL_008a: Unknown result type (might be due to invalid IL or missing references)
				//IL_0091: Expected O, but got Unknown
				Action action12 = delegate
				{
					if (tips.Count > 0)
					{
						UITipItem3.AddTip(tips[0], "", 0.5f);
						tips.RemoveAt(0);
					}
				};
				Il2CppStringArray equips = ui.unit.data.unitData.equips;
				if (equips != null && ((Il2CppArrayBase<string>)(object)equips).Count > 0)
				{
					float num13 = 0f;
					for (int num14 = 0; num14 < ((Il2CppArrayBase<string>)(object)equips).Count; num14++)
					{
						PropsData props = ((DataProps)ui.unit.data.unitData.propData).GetProps(((Il2CppArrayBase<string>)(object)equips)[num14]);
						if (props != null)
						{
							UnitActionEquipUnequip val2 = new UnitActionEquipUnequip(num14);
							ui.unit.CreateAction((UnitActionBase)(object)val2, false);
							g.world.playerUnit.data.RewardPropItem(props, props.propsCount, true);
							tips.Add("借用装备 " + GameTool.LS(props.propsItem.name) + "  *" + props.propsCount);
							((DataProps)ui.unit.data.unitData.propData).DelProps(props.propsID, props.propsCount);
							ui.UpdateUI();
							g.timer.Time(Action.op_Implicit(action12), num13, false);
							num13++;
						}
					}
				}
			};
			Action action11 = delegate
			{
				//IL_0010: Unknown result type (might be due to invalid IL or missing references)
				//IL_001a: Expected O, but got Unknown
				ClassInjector.RegisterTypeInIl2Cpp<UIUnitSIM>();
				((Component)g.ui.OpenUI(new UITypeBase("UIUnitSIM", (UILayer)0))).gameObject.AddComponent<UIUnitSIM>().InitData(ui.unit);
			};
			if (confInofFile.confInof.showTeamMenu)
			{
				((UICustomDramaBase)val).dramaData.dialogueOptions[num2] = "好友组队";
			}
			if (confInofFile.confInof.showShareExpMenu)
			{
				((UICustomDramaBase)val).dramaData.dialogueOptions[num3] = "坐而论道";
			}
			if (confInofFile.confInof.showTeachMenu)
			{
				((UICustomDramaBase)val).dramaData.dialogueOptions[num4] = "传授功法";
			}
			if (confInofFile.confInof.showStudyMenu)
			{
				((UICustomDramaBase)val).dramaData.dialogueOptions[num5] = "学习功法";
			}
			if (confInofFile.confInof.showUsePropMenu)
			{
				((UICustomDramaBase)val).dramaData.dialogueOptions[num8] = "投喂道具";
			}
			if (ui.unit.data.dynUnitData.GetGrade() <= 7)
			{
				ConfRoleGradeItem gradeItemMaxQuality = g.conf.roleGrade.GetGradeItemMaxQuality(ui.unit.data.dynUnitData.curGrade + 1, 1);
				if (confInofFile.confInof.NoCondUpGrade)
				{
					((UICustomDramaBase)val).dramaData.dialogueOptions[num7] = "大能传功";
				}
				else if (g.world.playerUnit.data.dynUnitData.GetGrade() - ui.unit.data.dynUnitData.GetGrade() >= 2 && g.world.playerUnit.data.unitData.propertyData.exp > gradeItemMaxQuality.exp * 3 && g.world.playerUnit.data.dynUnitData.energy.baseValue > 60)
				{
					((UICustomDramaBase)val).dramaData.dialogueOptions[num7] = "大能传功";
				}
			}
			if (confInofFile.confInof.showGetNPCEquipMenu)
			{
				((UICustomDramaBase)val).dramaData.dialogueOptions[num10] = "借用装备";
			}
			if (confInofFile.confInof.showGeneralMenu)
			{
				((UICustomDramaBase)val).dramaData.dialogueOptions[num] = "更多对话";
			}
			if (confInofFile.confInof.showHerFaceMenu)
			{
				((UICustomDramaBase)val).dramaData.dialogueOptions[num6] = "NPC管理";
			}
			((UICustomDramaBase)val).dramaData.dialogueOptions[num9] = "掠夺";
			((UICustomDramaBase)val).dramaData.dialogueOptions[num11] = "人生模拟";
			((UICustomDramaBase)val).dramaData.dialogueOptions[num12] = "取消";
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
			val.SetOptionCall(num11, Action.op_Implicit(action11));
			((UICustomDramaBase)val).dramaData.unitLeft = g.world.playerUnit;
			((UICustomDramaBase)val).dramaData.unitRight = ui.unit;
			val.OpenUI();
		}));
	}

	public void MoreOption(UINPCInfo ui)
	{
		//IL_0032: Unknown result type (might be due to invalid IL or missing references)
		//IL_0038: Expected O, but got Unknown
		//IL_016f: Unknown result type (might be due to invalid IL or missing references)
		//IL_018f: Unknown result type (might be due to invalid IL or missing references)
		WorldUnitBase unit = ui.unit;
		int confIndexID = mod_ID;
		UICustomDramaDyn val = new UICustomDramaDyn(confIndexID + 110);
		int num = confIndexID + 21;
		int num2 = confIndexID + 22;
		int num3 = confIndexID + 23;
		int num4 = confIndexID + 24;
		int num5 = confIndexID + 25;
		int num6 = confIndexID + 26;
		int num7 = confIndexID + 27;
		int num8 = confIndexID + 28;
		int num9 = confIndexID + 29;
		_ = confIndexID;
		int num10 = confIndexID + 31;
		Action action = delegate
		{
			//IL_000c: Unknown result type (might be due to invalid IL or missing references)
			//IL_0016: Expected O, but got Unknown
			//IL_0036: Unknown result type (might be due to invalid IL or missing references)
			//IL_003b: Unknown result type (might be due to invalid IL or missing references)
			//IL_004b: Unknown result type (might be due to invalid IL or missing references)
			//IL_005c: Expected O, but got Unknown
			//IL_0023: Unknown result type (might be due to invalid IL or missing references)
			//IL_002d: Expected O, but got Unknown
			if (UnitConditionTool.Condition("school_0_1", new UnitConditionData(unit, (WorldUnitBase)null)))
			{
				DramaFunctionTool.OptionsFunction("unitExitSchool_0", new DramaFunctionData(unit));
			}
			DramaTool.OpenDrama(confIndexID + 111, new DramaData
			{
				unitLeft = g.world.playerUnit,
				unitRight = unit
			});
			unit.data.RewardPropMoney(2000);
			unit.data.unitData.relationData.AddIntim(g.world.playerUnit.data.unitData.unitID, 60f, 0, "", true);
			g.ui.OpenUI<UITextInfo>(UIType.TextInfo).InitData("提示", "邀请成功！", "", (Action)null);
			ui.UpdateUI();
		};
		Action action2 = delegate
		{
			//IL_0024: Unknown result type (might be due to invalid IL or missing references)
			//IL_002a: Expected O, but got Unknown
			UnitActionSetTaoistHeart val2 = new UnitActionSetTaoistHeart(unit, g.world.playerUnit.data.unitData.heart.HeartConf());
			g.world.playerUnit.CreateAction((UnitActionBase)(object)val2, true);
			if (((UnitActionRoleBase)val2).state == 1)
			{
				g.ui.OpenUI<UITextInfo>(UIType.TextInfo).InitData("提示", "道种传授成功！", "", (Action)null);
				unit.data.unitData.relationData.AddIntim(g.world.playerUnit.data.unitData.unitID, 60f, 0, "", true);
			}
			ui.UpdateUI();
		};
		Action action3 = delegate
		{
			//IL_0006: Unknown result type (might be due to invalid IL or missing references)
			//IL_000c: Expected O, but got Unknown
			UnitActionRoleTrains val2 = new UnitActionRoleTrains(unit);
			val2.isForceTrains = true;
			g.world.playerUnit.CreateAction((UnitActionBase)(object)val2, true);
			if (((UnitActionRoleBase)val2).state == 1)
			{
				g.ui.OpenUI<UITextInfo>(UIType.TextInfo).InitData("提示", "强制双修成功！", "", (Action)null);
				unit.data.unitData.relationData.AddIntim(g.world.playerUnit.data.unitData.unitID, 60f, 0, "", true);
			}
		};
		Action action4 = delegate
		{
			if (!g.world.playerUnit.data.unitData.relationData.IsRelation(unit, (UnitRelationType)9))
			{
				g.world.playerUnit.data.unitData.relationData.lover.Add(unit.data.unitData.unitID);
				unit.data.unitData.relationData.lover.Add(g.world.playerUnit.data.unitData.unitID);
				g.ui.OpenUI<UITextInfo>(UIType.TextInfo).InitData("提示", "强制结缘成功！", "", (Action)null);
				unit.data.unitData.relationData.AddIntim(g.world.playerUnit.data.unitData.unitID, 180f, 0, "", true);
				unit.data.RewardPropMoney(50000);
				ui.UpdateUI();
			}
			else
			{
				g.ui.OpenUI<UITextInfo>(UIType.TextInfo).InitData("提示", "对方已经是你的道侣！", "", (Action)null);
			}
		};
		Action action5 = delegate
		{
			//IL_0108: Unknown result type (might be due to invalid IL or missing references)
			//IL_010d: Unknown result type (might be due to invalid IL or missing references)
			//IL_011d: Unknown result type (might be due to invalid IL or missing references)
			//IL_012e: Expected O, but got Unknown
			if (g.world.playerUnit.data.unitData.relationData.IsRelation(unit, (UnitRelationType)9))
			{
				g.world.playerUnit.data.unitData.relationData.married = unit.data.unitData.unitID;
				unit.data.unitData.relationData.married = g.world.playerUnit.data.unitData.unitID;
				g.world.playerUnit.data.unitData.relationData.lover.Remove(unit.data.unitData.unitID);
				unit.data.unitData.relationData.lover.Remove(g.world.playerUnit.data.unitData.unitID);
				DramaTool.OpenDrama(22201, new DramaData
				{
					unitLeft = g.world.playerUnit,
					unitRight = unit
				});
				g.ui.OpenUI<UITextInfo>(UIType.TextInfo).InitData("提示", "强制结婚成功！", "", (Action)null);
				unit.data.unitData.relationData.AddIntim(g.world.playerUnit.data.unitData.unitID, 240f, 0, "", true);
				unit.data.RewardPropMoney(100000);
				ui.UpdateUI();
			}
			else
			{
				g.ui.OpenUI<UITextInfo>(UIType.TextInfo).InitData("提示", "对方还不是你的道侣！", "", (Action)null);
			}
		};
		Action action6 = delegate
		{
			//IL_0010: Unknown result type (might be due to invalid IL or missing references)
			//IL_001a: Expected O, but got Unknown
			//IL_003e: Unknown result type (might be due to invalid IL or missing references)
			//IL_0043: Unknown result type (might be due to invalid IL or missing references)
			//IL_0053: Unknown result type (might be due to invalid IL or missing references)
			//IL_0064: Expected O, but got Unknown
			//IL_002b: Unknown result type (might be due to invalid IL or missing references)
			//IL_0035: Expected O, but got Unknown
			if (UnitConditionTool.Condition("school_0_1", new UnitConditionData(g.world.playerUnit, (WorldUnitBase)null)))
			{
				DramaFunctionTool.OptionsFunction("unitExitSchool_0", new DramaFunctionData(g.world.playerUnit));
			}
			DramaTool.OpenDrama(confIndexID + 112, new DramaData
			{
				unitLeft = g.world.playerUnit,
				unitRight = unit
			});
			unit.data.unitData.relationData.AddIntim(g.world.playerUnit.data.unitData.unitID, 60f, 0, "", true);
			g.ui.OpenUI<UITextInfo>(UIType.TextInfo).InitData("提示", "加入对方宗门！", "", (Action)null);
			ui.UpdateUI();
		};
		Action action7 = delegate
		{
			unit.data.RewardPropMoney(10000);
			int propsNum = ((DataProps)unit.data.unitData.propData).GetPropsNum(10001);
			g.ui.OpenUI<UITextInfo>(UIType.TextInfo).InitData("提示", "赠送成功！对方灵石数量：" + propsNum, "", (Action)null);
		};
		Action action8 = delegate
		{
			bool flag = false;
			if (unit.isDie)
			{
				unit._isDie = false;
				flag = true;
			}
			foreach (int item in new List<int> { 101, 1011, 1012, 1013, 1014 })
			{
				if (unit.GetLuck(item) != null)
				{
					DramaFunctionTool.OptionsFunction("delNPCLuck_" + unit.data.unitData.unitID + "_" + item, (DramaFunctionData)null);
					flag = true;
				}
			}
			if (flag)
			{
				UITipItem.AddTip("对方状态异常，复活吧，我的勇士！", 3f);
			}
			else
			{
				UITipItem.AddTip("对方状态正常，愿你的勇士健康长寿！", 3f);
			}
		};
		Action action9 = delegate
		{
			//IL_00ec: Unknown result type (might be due to invalid IL or missing references)
			//IL_00f1: Unknown result type (might be due to invalid IL or missing references)
			//IL_0102: Expected O, but got Unknown
			//IL_00a4: Unknown result type (might be due to invalid IL or missing references)
			//IL_00a9: Unknown result type (might be due to invalid IL or missing references)
			//IL_00ba: Expected O, but got Unknown
			//IL_0201: Unknown result type (might be due to invalid IL or missing references)
			//IL_0206: Unknown result type (might be due to invalid IL or missing references)
			//IL_0217: Expected O, but got Unknown
			//IL_0179: Unknown result type (might be due to invalid IL or missing references)
			//IL_017e: Unknown result type (might be due to invalid IL or missing references)
			//IL_018f: Expected O, but got Unknown
			try
			{
				if (g.world.soulDevourSword != null)
				{
					if (g.world.soulDevourSword.data.unitId.Equals(g.world.playerUnit.data.unitData.unitID))
					{
						if (g.world.playerUnit.data.dynUnitData.GetGrade() > unit.data.dynUnitData.GetGrade())
						{
							g.world.soulDevourSword.SoulDevour(g.world.playerUnit, unit);
							DramaFunctionTool.OptionsFunction("killRole_0_1", new DramaFunctionData(g.world.playerUnit)
							{
								unitLeft = unit
							});
							UITipItem.AddTip("对方境界比你低，已尝试吞噬对方！", 3f);
						}
						else
						{
							UITipItem.AddTip("你的境界未高于对方，吞噬对方失败，对方开始反抗！", 3f);
							DramaFunctionTool.OptionsFunction("npcAttackUnit_1", new DramaFunctionData(g.world.playerUnit)
							{
								unitLeft = unit
							});
						}
					}
					else if (g.world.soulDevourSword.data.unitId.Equals(unit.data.unitData.unitID))
					{
						if (g.world.playerUnit.data.dynUnitData.GetGrade() > unit.data.dynUnitData.GetGrade())
						{
							DramaFunctionTool.OptionsFunction("killRole_0_1", new DramaFunctionData(g.world.playerUnit)
							{
								unitLeft = unit
							});
							g.world.soulDevourSword.SwordEventDestroy();
							if (StringExtensions.IsEmpty(g.world.soulDevourSword.data.unitId))
							{
								g.world.soulDevourSword.UnitGetSword(g.world.playerUnit);
							}
							UITipItem.AddTip("对方境界比你低，已尝试将噬魂剑夺回！", 3f);
						}
						else
						{
							UITipItem.AddTip("你的境界未高于对方，噬魂剑夺回失败，对方开始反抗！", 3f);
							DramaFunctionTool.OptionsFunction("npcAttackUnit_1", new DramaFunctionData(g.world.playerUnit)
							{
								unitLeft = unit
							});
						}
					}
				}
			}
			catch (Exception ex2)
			{
				Console.WriteLine(ex2.Message);
			}
		};
		if (g.world.playerUnit.data.unitData.schoolID != null)
		{
			((UICustomDramaBase)val).dramaData.dialogueOptions[num] = "以“钱”动人，强制招纳";
		}
		if ((int)unit.data.unitData.heart.state == 0 && (int)g.world.playerUnit.data.unitData.heart.state != 0)
		{
			((UICustomDramaBase)val).dramaData.dialogueOptions[num2] = "传授自身道种";
		}
		((UICustomDramaBase)val).dramaData.dialogueOptions[num3] = "强制双修";
		if (!g.world.playerUnit.data.unitData.relationData.IsRelation(unit, (UnitRelationType)9))
		{
			((UICustomDramaBase)val).dramaData.dialogueOptions[num4] = "以“钱”动人，强制结缘";
		}
		if (g.world.playerUnit.data.unitData.relationData.IsRelation(unit, (UnitRelationType)9))
		{
			((UICustomDramaBase)val).dramaData.dialogueOptions[num5] = "以“钱”动人，强制结婚";
		}
		((UICustomDramaBase)val).dramaData.dialogueOptions[num6] = "加入对方宗门";
		((UICustomDramaBase)val).dramaData.dialogueOptions[num7] = "赠送1万灵石";
		((UICustomDramaBase)val).dramaData.dialogueOptions[num8] = "尝试复活";
		try
		{
			if (g.world.soulDevourSword != null && g.world.soulDevourSword.data != null && g.world.soulDevourSword.data.unitId != null)
			{
				if (g.world.soulDevourSword.data.unitId.Equals(g.world.playerUnit.data.unitData.unitID))
				{
					((UICustomDramaBase)val).dramaData.dialogueOptions[num9] = "噬魂剑吞噬";
				}
				else if (g.world.soulDevourSword.data.unitId.Equals(unit.data.unitData.unitID))
				{
					((UICustomDramaBase)val).dramaData.dialogueOptions[num9] = "抢夺噬魂剑";
				}
			}
		}
		catch (Exception ex)
		{
			Console.WriteLine(ex.Message);
		}
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
		((UICustomDramaBase)val).dramaData.unitLeft = g.world.playerUnit;
		((UICustomDramaBase)val).dramaData.unitRight = unit;
		val.OpenUI();
	}
}


//==================== TYPE MOD_SSCYAI.ModMainExt ====================
using System;
using System.Collections.Generic;
using System.Web.WebPages;
using EGameTypeData;
using Il2CppSystem;
using Il2CppSystem.Collections.Generic;
using UnhollowerBaseLib;
using UnhollowerRuntimeLib;
using UnityEngine;
using UnityEngine.Events;
using UnityEngine.UI;

namespace MOD_SSCYAI;

public class ModMainExt
{
	private TimerCoroutine corUpdate;

	private static Action<ETypeData> openUIEndCall;

	private static Action<ETypeData> onBattleStartCall;

	private static Action<ETypeData> intoWorldCall;

	private static Action<ETypeData> onBattleEndCall;

	private static Action<ETypeData> onSaveDataCall;

	public static int skillCount = 8;

	public static ConfSkillExt confSkillExt = new ConfSkillExt(skillCount);

	public static bool isOpenUI = false;

	private Action onSkillEndCall;

	public static List<Text> textCD = new List<Text>();

	public static List<GameObject> imgSkillCD = new List<GameObject>();

	public void Init()
	{
		openUIEndCall = Action<ETypeData>.op_Implicit((Action<ETypeData>)OnOpenUIEnd);
		g.events.On(EGameType.OpenUIEnd, openUIEndCall, 0, false);
		onBattleStartCall = OnBattleStart;
		g.events.On(EBattleType.BattleStart, Action<ETypeData>.op_Implicit(onBattleStartCall), 0, false);
		onBattleEndCall = OnBattleEnd;
		g.events.On(EBattleType.BattleEnd, Action<ETypeData>.op_Implicit(onBattleEndCall), 0, false);
		onSaveDataCall = OnSaveData;
		g.events.On(EGameType.SaveData, Action<ETypeData>.op_Implicit(onSaveDataCall), 0, false);
		intoWorldCall = Action<ETypeData>.op_Implicit((Action<ETypeData>)OnIntoWorld);
		g.events.On(EGameType.IntoWorld, intoWorldCall, 0, false);
		corUpdate = g.timer.Frame(Action.op_Implicit((Action)OnUpdate), 1, true);
	}

	public void Destroy()
	{
		g.timer.Stop(corUpdate);
		g.events.Off(EGameType.OpenUIEnd, openUIEndCall);
		g.events.Off(EBattleType.BattleStart, Action<ETypeData>.op_Implicit(onBattleStartCall));
		g.events.Off(EBattleType.BattleEnd, Action<ETypeData>.op_Implicit(onBattleEndCall));
		g.events.Off(EGameType.SaveData, Action<ETypeData>.op_Implicit(onSaveDataCall));
		if (g.events.Off(EGameType.IntoWorld, intoWorldCall))
		{
			Console.WriteLine("intoWorldCall监听关闭成功");
		}
	}

	public void OnIntoWorld(ETypeData e)
	{
		confSkillExt.Load();
	}

	private void OnSaveData(ETypeData edt)
	{
		confSkillExt.Save();
	}

	private void OnBattleStart(ETypeData edt)
	{
		if (SceneType.battle.battleMap.playerUnitCtrl == null || ((UnitCtrlBase)SceneType.battle.battleMap.playerUnitCtrl).isDestroy || ((UnitCtrlBase)SceneType.battle.battleMap.playerUnitCtrl).isDie)
		{
			return;
		}
		foreach (ConfSkillExtItem item in confSkillExt.items)
		{
			if (!StringExtensions.IsEmpty(item.soleID) && g.world.playerUnit.data.unitData.allActionMartial.ContainsKey(item.soleID))
			{
				DataStruct<MartialData, List<Prefix>> val = new DataStruct<MartialData, List<Prefix>>((MartialData)(object)g.world.playerUnit.data.unitData.allActionMartial[item.soleID].data.To<PropsSkillData>(), g.world.playerUnit.data.unitData.allActionMartial[item.soleID].GetPrefixsUnlock());
				List<DataStruct<MartialData, List<Prefix>>> val2 = new List<DataStruct<MartialData, List<Prefix>>>();
				val2.Add(val);
				((UnitCtrlBase)SceneType.battle.battleMap.playerUnitCtrl).conf.ModifFieldData(val2);
			}
		}
	}

	private void OnBattleEnd(ETypeData edt)
	{
	}

	private void OnUpdate()
	{
		//IL_0074: Unknown result type (might be due to invalid IL or missing references)
		//IL_0108: Unknown result type (might be due to invalid IL or missing references)
		//IL_0112: Expected O, but got Unknown
		//IL_0239: Unknown result type (might be due to invalid IL or missing references)
		//IL_023e: Unknown result type (might be due to invalid IL or missing references)
		//IL_0257: Unknown result type (might be due to invalid IL or missing references)
		//IL_025c: Unknown result type (might be due to invalid IL or missing references)
		if (!ModMain.confInofFile.confInof.SkillExt || SceneType.battle == null || !SceneType.battle.battleMap.isStartBattle || ((UnitCtrlBase)SceneType.battle.battleMap.playerUnitCtrl).IsState((UnitStateType)1))
		{
			return;
		}
		int num = 0;
		foreach (ConfSkillExtItem item in confSkillExt.items)
		{
			if (Input.GetKeyDown(item.skillKeyCod))
			{
				try
				{
					if (item.soleID.Length < 1)
					{
						break;
					}
					if ((Object)(object)g.ui.GetUI<UIBattleInfo>(UIType.BattleInfo) != (Object)null)
					{
						if (item.isIng)
						{
							UITipItem.AddTip("技能尚未冷却", 1f);
							break;
						}
						ConfBattleSkillAttackItem item2 = ((ConfBattleSkillAttackBase)g.conf.battleSkillAttack).GetItem(item.skillBaseID);
						if (item2 != null)
						{
							item.lastUseTime = Time.time;
							SkillAttack skillAttack = new SkillAttack();
							skillAttack.Init((UnitCtrlBase)(object)SceneType.battle.battleMap.playerUnitCtrl, MartialTool.CreateSkillData(item.skillBaseID, g.world.playerUnit.data.dynUnitData.GetGrade()));
							if ((float)((UnitDataBase)((UnitCtrlHuman)SceneType.battle.battleMap.playerUnitCtrl).data).mp > skillAttack.data.mpCost.valueFloat)
							{
								item.isIng = true;
								if (item2.type == 1 || item2.type == 2 || item2.type == 3)
								{
									item.CDTime = ((SkillDataBase)skillAttack.data).shotCD.valueFloat * ((UnitDataBase)((UnitCtrlHuman)SceneType.battle.battleMap.playerUnitCtrl).data).skillCDRate.valueFloat / 100000f;
									skillAttack.Create(Vector2.op_Implicit(((UnitCtrlBase)SceneType.battle.battleMap.playerUnitCtrl).posiBullet.position), Vector2.op_Implicit(((UnitCtrlBase)SceneType.battle.battleMap.playerUnitCtrl).posiBullet.up), (Action)null, (Action<MissileShotData>)null, (SkillCreateData)null);
								}
								else if (item2.type == 4)
								{
									item.CDTime = ((SkillDataBase)skillAttack.data).shotCD.valueFloat * ((UnitDataBase)((UnitCtrlHuman)SceneType.battle.battleMap.playerUnitCtrl).data).ultimateCDRate.valueFloat / 100000f;
									string text = GameTool.LS(skillAttack.data.skillAttackItem.name);
									string text2 = "UltimateShifa2";
									if ((Object)(object)((UnitCtrlBase)SceneType.battle.battleMap.playerUnitCtrl).gameObject.GetComponent<UnitPlayerInputCtrl>() == (Object)null)
									{
										((UnitCtrlBase)SceneType.battle.battleMap.playerUnitCtrl).gameObject.AddComponent<UnitPlayerInputCtrl>();
									}
									((UnitCtrlBase)SceneType.battle.battleMap.playerUnitCtrl).gameObject.GetComponent<UnitPlayerInputCtrl>().PlayUltimateEffect(Action.op_Implicit((Action)delegate
									{
										//IL_001a: Unknown result type (might be due to invalid IL or missing references)
										//IL_001f: Unknown result type (might be due to invalid IL or missing references)
										//IL_0038: Unknown result type (might be due to invalid IL or missing references)
										//IL_003d: Unknown result type (might be due to invalid IL or missing references)
										skillAttack.Create(Vector2.op_Implicit(((UnitCtrlBase)SceneType.battle.battleMap.playerUnitCtrl).posiBullet.position), Vector2.op_Implicit(((UnitCtrlBase)SceneType.battle.battleMap.playerUnitCtrl).posiBullet.up), (Action)null, (Action<MissileShotData>)null, (SkillCreateData)null);
									}), "", text, text2);
									((UnitCtrlBase)SceneType.battle.battleMap.playerUnitCtrl).gameObject.GetComponent<UnitPlayerInputCtrl>().PlayerSkillSound();
								}
								TimerCoroutine cor = null;
								onSkillEndCall = delegate
								{
									item.isIng = false;
									((SceneBase)SceneType.battle).timer.Stop(cor);
								};
								cor = ((SceneBase)SceneType.battle).timer.Time(Action.op_Implicit(onSkillEndCall), item.CDTime, false);
							}
							else
							{
								UITipItem.AddTip("灵力不足", 1f);
							}
						}
					}
				}
				catch (Exception ex)
				{
					Console.WriteLine(ex.ToString());
				}
			}
			if (num >= textCD.Count || num >= imgSkillCD.Count)
			{
				continue;
			}
			if ((Object)(object)textCD[num] != (Object)null)
			{
				((Component)textCD[num]).gameObject.SetActive(true);
				if (item.isIng)
				{
					float num2 = item.lastUseTime + item.CDTime - Time.time;
					textCD[num].text = ((num2 > 1f) ? ((int)num2).ToString() : num2.ToString("0.0"));
					if ((Object)(object)imgSkillCD[num] != (Object)null)
					{
						Image component = imgSkillCD[num].GetComponent<Image>();
						if ((Object)(object)component != (Object)null)
						{
							component.fillAmount = num2 / item.CDTime;
						}
					}
				}
				else
				{
					textCD[num].text = "";
					if ((Object)(object)imgSkillCD[num] != (Object)null)
					{
						Image component2 = imgSkillCD[num].GetComponent<Image>();
						if ((Object)(object)component2 != (Object)null)
						{
							component2.fillAmount = 0f;
						}
					}
				}
			}
			num++;
		}
	}

	private void MapMainAddBtn()
	{
		//IL_00cc: Unknown result type (might be due to invalid IL or missing references)
		//IL_00bb: Unknown result type (might be due to invalid IL or missing references)
		//IL_011b: Unknown result type (might be due to invalid IL or missing references)
		//IL_0121: Unknown result type (might be due to invalid IL or missing references)
		if (!ModMain.confInofFile.confInof.SkillExt)
		{
			return;
		}
		UIMapMain uI = g.ui.GetUI<UIMapMain>(UIType.MapMain);
		if (!((Object)(object)uI != (Object)null) || !((Object)(object)((Component)((UIMapMainBase)uI).playerInfo.btnPlayer).transform.parent.Find("BtnSkillExt") == (Object)null))
		{
			return;
		}
		GameObject obj = Object.Instantiate<GameObject>(g.res.Load<GameObject>("UI/SkillExt/SkillExt"), ((Component)((UIMapMainBase)uI).playerInfo.btnPlayer).transform.parent);
		((Object)obj).name = "BtnSkillExt";
		obj.transform.localPosition = ((g.world.playerUnit.data.dynUnitData.GetGrade() >= 10) ? new Vector3(520f, 130f) : new Vector3(520f, 130f));
		((UnityEvent)obj.GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
		{
			//IL_000b: Unknown result type (might be due to invalid IL or missing references)
			//IL_0015: Expected O, but got Unknown
			//IL_004e: Unknown result type (might be due to invalid IL or missing references)
			//IL_0058: Expected O, but got Unknown
			//IL_0077: Unknown result type (might be due to invalid IL or missing references)
			//IL_0032: Unknown result type (might be due to invalid IL or missing references)
			//IL_003d: Expected O, but got Unknown
			if ((Object)(object)g.ui.GetUI(new UITypeBase("SkillExt/UISkillList", (UILayer)0)) != (Object)null)
			{
				Console.WriteLine("UISkillList已打开");
				g.ui.CloseUI(new UITypeBase("SkillExt/UISkillList", (UILayer)0), false);
			}
			else
			{
				ClassInjector.RegisterTypeInIl2Cpp<UISkillList>();
				UIBase obj2 = g.ui.OpenUI(new UITypeBase("SkillExt/UISkillList", (UILayer)0));
				((Component)obj2).gameObject.AddComponent<UISkillList>().InitData();
				((Component)obj2).transform.localPosition = new Vector3(200f, -390f);
			}
		}));
		obj.gameObject.AddComponent<UISkyTipEffect>().InitData("技能扩展", default(Vector3));
	}

	public void AddBattleSkill(OpenUIEnd edata)
	{
		//IL_00b6: Unknown result type (might be due to invalid IL or missing references)
		//IL_00f2: Unknown result type (might be due to invalid IL or missing references)
		//IL_00f8: Unknown result type (might be due to invalid IL or missing references)
		if (!ModMain.confInofFile.confInof.SkillExt)
		{
			return;
		}
		UIBattleInfo uiBattleInfo = ((Il2CppObjectBase)edata.ui).Cast<UIBattleInfo>();
		if (!((Object)(object)uiBattleInfo != (Object)null))
		{
			return;
		}
		GameObject gameObject = ((Component)((Info)uiBattleInfo.uiInfo).goUltimate.transform.parent).gameObject;
		if (!((Object)(object)gameObject.transform.parent.Find("BtnSkillExt") == (Object)null))
		{
			return;
		}
		GameObject obj = Object.Instantiate<GameObject>(g.res.Load<GameObject>("UI/SkillExt/SkillExt"), gameObject.transform.parent);
		((Object)obj).name = "BtnSkillExt";
		obj.transform.localPosition = new Vector3(-510f, 66f);
		((UnityEvent)obj.GetComponent<Button>().onClick).AddListener(UnityAction.op_Implicit((Action)delegate
		{
			//IL_000b: Unknown result type (might be due to invalid IL or missing references)
			//IL_0015: Expected O, but got Unknown
			//IL_004e: Unknown result type (might be due to invalid IL or missing references)
			//IL_0058: Expected O, but got Unknown
			//IL_0079: Unknown result type (might be due to invalid IL or missing references)
			//IL_0032: Unknown result type (might be due to invalid IL or missing references)
			//IL_003d: Expected O, but got Unknown
			if ((Object)(object)g.ui.GetUI(new UITypeBase("SkillExt/UISkillList", (UILayer)0)) != (Object)null)
			{
				Console.WriteLine("UISkillList已打开");
				g.ui.CloseUI(new UITypeBase("SkillExt/UISkillList", (UILayer)0), false);
				return;
			}
			ClassInjector.RegisterTypeInIl2Cpp<UISkillList>();
			UIBase obj2 = g.ui.OpenUI(new UITypeBase("SkillExt/UISkillList", (UILayer)0));
			UISkillList uISkillList = ((Component)obj2).gameObject.AddComponent<UISkillList>();
			uISkillList.InitData();
			((Component)obj2).transform.localPosition = new Vector3(0f, -310f);
			Transform val = ((Info)uiBattleInfo.uiInfo).goSkill1.transform.parent.Find("G:imgSkill1CD");
			if ((Object)(object)val != (Object)null)
			{
				imgSkillCD.Clear();
				for (int i = 0; i < uISkillList.goSkillList.transform.GetChildCount(); i++)
				{
					GameObject gameObject2 = ((Component)Object.Instantiate<Transform>(val, uISkillList.goSkillList.transform.GetChild(i).Find("Image"))).gameObject;
					gameObject2.SetActive(true);
					((Object)gameObject2).name = $"imgSkill{i}CD";
					imgSkillCD.Add(gameObject2);
				}
			}
			foreach (ConfSkillExtItem item in confSkillExt.items)
			{
				item.isIng = false;
				item.lastUseTime = -999f;
			}
		}));
		obj.gameObject.AddComponent<UISkyTipEffect>().InitData("技能扩展", default(Vector3));
	}

	public void OnOpenUIEnd(ETypeData e)
	{
		OpenUIEnd val = ((Il2CppObjectBase)e).Cast<OpenUIEnd>();
		if (val.uiType.uiName == UIType.MapMain.uiName)
		{
			MapMainAddBtn();
		}
		else if (val.uiType.uiName == UIType.BattleInfo.uiName)
		{
			AddBattleSkill(val);
		}
	}
}


//==================== TYPE MOD_SSCYAI.ModMain.<>c ====================
// decompile failed: Could not find type definition MOD_SSCYAI.ModMain.<>c in type system.

//==================== TYPE MOD_SSCYAI.ModMain.<>c__DisplayClass100_0 ====================
// decompile failed: Could not find type definition MOD_SSCYAI.ModMain.<>c__DisplayClass100_0 in type system.

//==================== TYPE MOD_SSCYAI.ModMain.<>c__DisplayClass101_0 ====================
// decompile failed: Could not find type definition MOD_SSCYAI.ModMain.<>c__DisplayClass101_0 in type system.

//==================== TYPE MOD_SSCYAI.ModMain.<>c__DisplayClass106_0 ====================
// decompile failed: Could not find type definition MOD_SSCYAI.ModMain.<>c__DisplayClass106_0 in type system.

//==================== TYPE MOD_SSCYAI.ModMain.<>c__DisplayClass111_0 ====================
// decompile failed: Could not find type definition MOD_SSCYAI.ModMain.<>c__DisplayClass111_0 in type system.

//==================== TYPE MOD_SSCYAI.ModMain.<>c__DisplayClass114_0 ====================
// decompile failed: Could not find type definition MOD_SSCYAI.ModMain.<>c__DisplayClass114_0 in type system.

//==================== TYPE MOD_SSCYAI.ModMain.<>c__DisplayClass118_0 ====================
// decompile failed: Could not find type definition MOD_SSCYAI.ModMain.<>c__DisplayClass118_0 in type system.

//==================== TYPE MOD_SSCYAI.ModMain.<>c__DisplayClass119_0 ====================
// decompile failed: Could not find type definition MOD_SSCYAI.ModMain.<>c__DisplayClass119_0 in type system.

//==================== TYPE MOD_SSCYAI.ModMain.<>c__DisplayClass128_0 ====================
// decompile failed: Could not find type definition MOD_SSCYAI.ModMain.<>c__DisplayClass128_0 in type system.

//==================== TYPE MOD_SSCYAI.ModMain.<>c__DisplayClass128_1 ====================
// decompile failed: Could not find type definition MOD_SSCYAI.ModMain.<>c__DisplayClass128_1 in type system.

//==================== TYPE MOD_SSCYAI.ModMain.<>c__DisplayClass131_0 ====================
// decompile failed: Could not find type definition MOD_SSCYAI.ModMain.<>c__DisplayClass131_0 in type system.

//==================== TYPE MOD_SSCYAI.ModMain.<>c__DisplayClass131_1 ====================
// decompile failed: Could not find type definition MOD_SSCYAI.ModMain.<>c__DisplayClass131_1 in type system.

//==================== TYPE MOD_SSCYAI.ModMain.<>c__DisplayClass131_2 ====================
// decompile failed: Could not find type definition MOD_SSCYAI.ModMain.<>c__DisplayClass131_2 in type system.

//==================== TYPE MOD_SSCYAI.ModMain.<>c__DisplayClass133_0 ====================
// decompile failed: Could not find type definition MOD_SSCYAI.ModMain.<>c__DisplayClass133_0 in type system.

//==================== TYPE MOD_SSCYAI.ModMain.<>c__DisplayClass133_1 ====================
// decompile failed: Could not find type definition MOD_SSCYAI.ModMain.<>c__DisplayClass133_1 in type system.

//==================== TYPE MOD_SSCYAI.ModMain.<>c__DisplayClass133_2 ====================
// decompile failed: Could not find type definition MOD_SSCYAI.ModMain.<>c__DisplayClass133_2 in type system.

//==================== TYPE MOD_SSCYAI.ModMain.<>c__DisplayClass133_3 ====================
// decompile failed: Could not find type definition MOD_SSCYAI.ModMain.<>c__DisplayClass133_3 in type system.

//==================== TYPE MOD_SSCYAI.ModMain.<>c__DisplayClass133_4 ====================
// decompile failed: Could not find type definition MOD_SSCYAI.ModMain.<>c__DisplayClass133_4 in type system.

//==================== TYPE MOD_SSCYAI.ModMain.<>c__DisplayClass134_0 ====================
// decompile failed: Could not find type definition MOD_SSCYAI.ModMain.<>c__DisplayClass134_0 in type system.

//==================== TYPE MOD_SSCYAI.ModMain.<>c__DisplayClass98_0 ====================
// decompile failed: Could not find type definition MOD_SSCYAI.ModMain.<>c__DisplayClass98_0 in type system.

//==================== TYPE MOD_SSCYAI.ModMain.<>c__DisplayClass99_0 ====================
// decompile failed: Could not find type definition MOD_SSCYAI.ModMain.<>c__DisplayClass99_0 in type system.

//==================== TYPE MOD_SSCYAI.ModMainExt.<>c ====================
// decompile failed: Could not find type definition MOD_SSCYAI.ModMainExt.<>c in type system.

//==================== TYPE MOD_SSCYAI.ModMainExt.<>c__DisplayClass18_0 ====================
// decompile failed: Could not find type definition MOD_SSCYAI.ModMainExt.<>c__DisplayClass18_0 in type system.

//==================== TYPE MOD_SSCYAI.ModMainExt.<>c__DisplayClass18_1 ====================
// decompile failed: Could not find type definition MOD_SSCYAI.ModMainExt.<>c__DisplayClass18_1 in type system.

//==================== TYPE MOD_SSCYAI.ModMainExt.<>c__DisplayClass20_0 ====================
// decompile failed: Could not find type definition MOD_SSCYAI.ModMainExt.<>c__DisplayClass20_0 in type system.

//==================== TYPE MOD_SSCYAI.ModMain.<>c.<<OnWorldRunStart>b__117_2>d ====================
// decompile failed: Could not find type definition MOD_SSCYAI.ModMain.<>c.<<OnWorldRunStart>b__117_2>d in type system.

//==================== TYPE MOD_SSCYAI.ModMain.<>c.<<OnWorldRunStart>b__117_3>d ====================
// decompile failed: Could not find type definition MOD_SSCYAI.ModMain.<>c.<<OnWorldRunStart>b__117_3>d in type system.

//==================== TYPE MOD_SSCYAI.ModMain.<>c__DisplayClass111_0.<<OnWorldAddDay>b__1>d ====================
// decompile failed: Could not find type definition MOD_SSCYAI.ModMain.<>c__DisplayClass111_0.<<OnWorldAddDay>b__1>d in type system.

//==================== TYPE MOD_SSCYAI.ModMain.<>c__DisplayClass119_0.<<RunUnitLog>b__0>d ====================
// decompile failed: Could not find type definition MOD_SSCYAI.ModMain.<>c__DisplayClass119_0.<<RunUnitLog>b__0>d in type system.

//==================== TYPE MOD_SSCYAI.ModMain.<>c__DisplayClass131_2.<<OnOpenUIEnd>b__2>d ====================
// decompile failed: Could not find type definition MOD_SSCYAI.ModMain.<>c__DisplayClass131_2.<<OnOpenUIEnd>b__2>d in type system.

