//==================== TYPE AgentLoopBridge.StepGroup ====================
using UnityEngine;
using UnityEngine.UI;

namespace AgentLoopBridge;

public class StepGroup
{
	public readonly GameObject Root;

	private readonly Button _header;

	private readonly Text _headerText;

	private readonly RectTransform _body;

	private bool _bodyExpanded;

	private int _count;

	private const float RESULT_LINE_H = 72f;

	private const float RESULT_EXPAND_H = 260f;

	public StepGroup(ChatWindowRefs refs, Transform contentParent)
	{
		Root = TemplateUtils.Instantiate(refs.StepGroupTemplate, contentParent);
		Transform val = Root.transform.Find("Header");
		if ((Object)(object)val != (Object)null)
		{
			_header = ((Component)val).GetComponent<Button>();
			_headerText = ((Component)val).GetComponentInChildren<Text>(true);
		}
		object body;
		if (!((Object)(object)Root != (Object)null))
		{
			body = null;
		}
		else
		{
			Transform obj = Root.transform.Find("Body");
			body = ((obj != null) ? ((Component)obj).GetComponent<RectTransform>() : null);
		}
		_body = (RectTransform)body;
		if ((Object)(object)_body != (Object)null)
		{
			((Component)_body).gameObject.SetActive(false);
		}
		if ((Object)(object)_header != (Object)null)
		{
			ClickUtils.Attach(_header, ToggleBody);
		}
	}

	public void Finish()
	{
		if ((Object)(object)_headerText != (Object)null)
		{
			_headerText.text = "▸ 行动记录 (" + _count + ")";
		}
		if ((Object)(object)_body != (Object)null)
		{
			((Component)_body).gameObject.SetActive(false);
		}
		_bodyExpanded = false;
	}

	public void AddCall(string name, string argsJson)
	{
		//IL_0083: Unknown result type (might be due to invalid IL or missing references)
		_count++;
		if ((Object)(object)_headerText != (Object)null)
		{
			_headerText.text = "▸ 行动记录 (" + _count + ")";
		}
		string text = CompactArgs(argsJson);
		AddTextLine("▶ " + name + ((text.Length > 0) ? ("  —  " + text) : ""), new Color(0.72f, 0.75f, 0.85f, 1f));
	}

	public void AddResult(string text, bool isError)
	{
		_count++;
		if ((Object)(object)_headerText != (Object)null)
		{
			_headerText.text = "▸ 行动记录 (" + _count + ")";
		}
		AddResultLine(text ?? "", isError);
	}

	private void AddTextLine(string text, Color color)
	{
		//IL_0005: Unknown result type (might be due to invalid IL or missing references)
		//IL_000a: Unknown result type (might be due to invalid IL or missing references)
		//IL_0027: Unknown result type (might be due to invalid IL or missing references)
		//IL_004f: Unknown result type (might be due to invalid IL or missing references)
		GameObject val = new GameObject("Line");
		RectTransform obj = val.AddComponent<RectTransform>();
		((Transform)obj).SetParent((Transform)(object)_body, false);
		obj.sizeDelta = new Vector2(0f, 22f);
		Text obj2 = val.AddComponent<Text>();
		obj2.text = text ?? "";
		obj2.fontSize = 14;
		((Graphic)obj2).color = color;
		obj2.font = ChatUiBuilder.ResolveSharedFont();
		obj2.alignment = (TextAnchor)3;
		((Graphic)obj2).raycastTarget = false;
		obj2.horizontalOverflow = (HorizontalWrapMode)1;
		obj2.verticalOverflow = (VerticalWrapMode)1;
	}

	private void AddResultLine(string text, bool isError)
	{
		//IL_0012: Unknown result type (might be due to invalid IL or missing references)
		//IL_0018: Expected O, but got Unknown
		//IL_0054: Unknown result type (might be due to invalid IL or missing references)
		//IL_0073: Unknown result type (might be due to invalid IL or missing references)
		//IL_00f5: Unknown result type (might be due to invalid IL or missing references)
		//IL_00da: Unknown result type (might be due to invalid IL or missing references)
		//IL_0162: Unknown result type (might be due to invalid IL or missing references)
		//IL_016d: Unknown result type (might be due to invalid IL or missing references)
		//IL_0182: Unknown result type (might be due to invalid IL or missing references)
		//IL_0197: Unknown result type (might be due to invalid IL or missing references)
		//IL_01ab: Unknown result type (might be due to invalid IL or missing references)
		GameObject val = new GameObject("Result");
		RectTransform rt = val.AddComponent<RectTransform>();
		val.AddComponent<CanvasRenderer>();
		Image val2 = val.AddComponent<Image>();
		((Transform)rt).SetParent((Transform)(object)_body, false);
		rt.sizeDelta = new Vector2(0f, 72f);
		((Graphic)val2).color = new Color(0f, 0f, 0f, 0.16f);
		((Graphic)val2).raycastTarget = true;
		Text textComp = val.AddComponent<Text>();
		textComp.text = (isError ? "✗ " : "✓ ") + text;
		textComp.fontSize = 14;
		((Graphic)textComp).color = (isError ? new Color(0.95f, 0.55f, 0.5f, 1f) : new Color(0.65f, 0.85f, 0.68f, 1f));
		textComp.font = ChatUiBuilder.ResolveSharedFont();
		textComp.alignment = (TextAnchor)0;
		((Graphic)textComp).raycastTarget = false;
		textComp.horizontalOverflow = (HorizontalWrapMode)0;
		textComp.verticalOverflow = (VerticalWrapMode)0;
		RectTransform component = ((Component)textComp).GetComponent<RectTransform>();
		((Transform)component).SetParent((Transform)(object)rt, false);
		component.anchorMin = new Vector2(0f, 0f);
		component.anchorMax = Vector2.one;
		component.offsetMin = new Vector2(10f, 4f);
		component.offsetMax = new Vector2(-10f, -4f);
		component.pivot = new Vector2(0.5f, 0.5f);
		Button obj = val.AddComponent<Button>();
		((Selectable)obj).targetGraphic = (Graphic)(object)val2;
		ClickUtils.Attach(obj, delegate
		{
			ToggleResult(rt, textComp);
		});
	}

	private void ToggleResult(RectTransform row, Text textComp)
	{
		//IL_0001: Unknown result type (might be due to invalid IL or missing references)
		//IL_002b: Unknown result type (might be due to invalid IL or missing references)
		bool flag = row.sizeDelta.y <= 73f;
		row.sizeDelta = new Vector2(0f, flag ? 260f : 72f);
		textComp.verticalOverflow = (VerticalWrapMode)(flag ? 1 : 0);
	}

	private void ToggleBody()
	{
		_bodyExpanded = !_bodyExpanded;
		if ((Object)(object)_body != (Object)null)
		{
			((Component)_body).gameObject.SetActive(_bodyExpanded);
		}
	}

	private static string CompactArgs(string argsJson)
	{
		if (string.IsNullOrEmpty(argsJson))
		{
			return "";
		}
		string text = argsJson;
		if (text.Length > 42)
		{
			text = text.Substring(0, 42) + "…";
		}
		return text.Replace("\n", " ");
	}
}


//==================== TYPE AgentLoopBridge.StepGroup.<>c__DisplayClass13_0 ====================
// decompile failed: Could not find type definition AgentLoopBridge.StepGroup.<>c__DisplayClass13_0 in type system.

