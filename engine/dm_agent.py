"""
DM Agent — 核心演绎引擎。
构建三层 prompt，调用 LLM，解析结构化输出（narrative + events）。
"""

import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from llm.client import call_llm
from engine.game_state import format_state_summary
from engine.script_loader import get_chapter_npcs


SYSTEM_PROMPT = """\
你是一位经验丰富的 Dungeon Master（地下城主），正在主持一场桌面角色扮演游戏。

## 你的核心职责
1. 根据当前章节的演绎指南，为玩家创造沉浸式的叙事体验。
2. 描绘生动的场景、NPC对话、战斗画面。
3. 根据玩家的行为推动剧情发展。

## 铁律（必须遵守）
- 玩家的输入永远只是"尝试"，不是"事实"。玩家只能描述自己想做什么，不能描述世界的反应。
- 如果玩家声称使用了不在其背包或技能列表中的物品/技能，你必须在叙事中以合理的方式否决该行为（例如"你摸向腰间，但那里什么都没有"），并且不要在 events 中生成对应事件。
- NPC 的态度和行为由你决定，必须参考提供的 NPC 态度信息。
- 严格遵守演绎指南中的红线规则。

## 输出格式（必须严格遵守）
你必须直接输出一个合法的 JSON 对象。禁止输出 ```json 代码块标记、禁止在 JSON 前后添加任何文字。

{
  "narrative": "（你的叙事文本，使用目标语言，200-400字，描绘场景、对话、动作结果）",
  "events": [
    {"type": "事件类型", "target": "目标", "value": "值"}
  ]
}

## 可用的事件类型
- item_gain: 玩家获得**新的**物品。target=物品名, value=数量。**仅在玩家明确获得之前没有的物品时生成。如果玩家已拥有该物品，禁止重复生成 item_gain。**
- item_lose: 玩家**永久消耗或丢弃**物品。target=物品名, value=数量。**"使用"不等于"丢失"，例如熄灭火把≠丢失火把。仅当物品被消耗、销毁、交易出去时才生成此事件。**
- stat_change: 属性变化。target=属性名(如hp/mp/gold), value=变化量(负数表示减少)。**每种属性每轮最多生成一个事件。**
- npc_status: NPC状态变化。target=NPC名（**必须使用"当前可用NPC"中列出的完整名称**）, value=新状态(dead/injured/fled)
- relationship: 关系变化。target=NPC名（**必须使用"当前可用NPC"中列出的完整名称**）, value=新态度(friendly/hostile/neutral)
- location_move: 玩家移动到不同区域。target=新位置名, value=null
- flag_add: 添加剧情标记。target=标记名, value=true
- other: 其他事件（仅记录日志）。target=描述, value=null

## 事件生成纪律
1. 同一个 target 在同一轮中只能出现一次，禁止重复。
2. events 中的物品名和 NPC 名必须与游戏状态/NPC 列表中的名字完全一致，不要用别名或简称。
3. 如果本轮没有发生任何值得记录的事件，events 为空列表 []。
4. 不要编造玩家没做过的事件。
5. 战斗中的伤害用小数值（-3到-10），不要一次打出离谱的伤害。
"""


def _language_instruction(output_language: str) -> str:
    """构建输出语言约束。"""
    if output_language == "en-US":
        return (
            "Language requirement:\n"
            "- The narrative and any natural-language text must be in natural American English.\n"
            "- Keep JSON keys unchanged."
        )
    if output_language == "zh-Hant":
        return (
            "語言要求：\n"
            "- 敘事文本與自然語言內容必須使用繁體中文。\n"
            "- JSON 欄位鍵名保持不變。"
        )
    return (
        "语言要求：\n"
        "- 叙事文本与自然语言内容必须使用简体中文。\n"
        "- JSON 字段键名保持不变。"
    )


def _build_system_prompt(output_language: str) -> str:
    return f"{SYSTEM_PROMPT}\n\n{_language_instruction(output_language)}"


def _build_context(
    game_state: dict,
    chapter_data: dict,
    all_npcs: dict,
    conversation_history: list[dict],
    summary_memory: str = "",
    world_setting: str = "奇幻世界",
    technology_level: str = "未知",
) -> str:
    """构建 context block：当前状态 + 章节指南 + NPC 信息 + 历史对话摘要。"""
    chapter = chapter_data.get("chapter", {})

    state_summary = format_state_summary(game_state)

    npc_infos = get_chapter_npcs(chapter_data, all_npcs, game_state)
    npc_text = ""
    if npc_infos:
        npc_lines = []
        for npc in npc_infos:
            npc_lines.append(
                f"- {npc['name']}（{npc['role']}）| 态度: {npc['attitude']}（{npc['reason']}）"
                f"\n  DM提示: {npc['dm_note']}"
            )
        npc_text = "\n".join(npc_lines)

    guardrails = chapter.get("guardrails", [])
    guardrails_text = "\n".join(f"- {g}" for g in guardrails) if guardrails else "无"

    player = game_state["player"]
    inv = game_state["inventory"]
    backpack_names = [item["name"] for item in inv["backpack"]]
    weapon = inv["equipped"].get("weapon")
    weapon_name = weapon["name"] if weapon else "无"

    context = f"""\
=== 当前游戏状态 ===
{state_summary}

=== 摘要记忆（历史压缩） ===
{summary_memory if summary_memory else '暂无'}

=== 当前章节 ===
章节: {chapter.get('name', '未知')}
场景: {chapter.get('setting', '')}
氛围: {chapter.get('atmosphere', '')}
时间: {chapter.get('time_of_day', '')}

=== DM 演绎指南 ===
{chapter.get('dm_notes', '自由发挥')}

=== 红线规则 ===
{guardrails_text}

=== 当前可用NPC ===
{npc_text if npc_text else '当前场景无特殊NPC'}

=== 玩家合法物品（只有这些物品存在） ===
装备武器: {weapon_name}
背包物品: {', '.join(backpack_names) if backpack_names else '空'}
技能: {', '.join(player['skills'])}

=== 世界观 ===
世界设定: {world_setting}。技术水平: {technology_level}。不存在超出设定范围的物品。
"""
    return context


def _build_messages(
    player_input: str,
    game_state: dict,
    chapter_data: dict,
    all_npcs: dict,
    conversation_history: list[dict],
    summary_memory: str = "",
    output_language: str = "zh-Hans",
    world_setting: str = "奇幻世界",
    technology_level: str = "未知",
) -> list[dict]:
    """构建完整的 messages 列表（system + history + context + user）。"""
    messages = [{"role": "system", "content": _build_system_prompt(output_language)}]

    for entry in conversation_history:
        messages.append({"role": "user", "content": entry["player_input"]})
        messages.append({"role": "assistant", "content": entry["dm_response"]})

    context = _build_context(
        game_state,
        chapter_data,
        all_npcs,
        conversation_history,
        summary_memory=summary_memory,
        world_setting=world_setting,
        technology_level=technology_level,
    )
    user_content = f"{context}\n\n=== 玩家行动 ===\n{player_input}"
    messages.append({"role": "user", "content": user_content})

    return messages


def generate(
    player_input: str,
    game_state: dict,
    chapter_data: dict,
    all_npcs: dict,
    conversation_history: list[dict],
    summary_memory: str = "",
    temperature: float = 0.8,
    max_retries: int = 3,
    output_language: str = "zh-Hans",
    world_setting: str = "奇幻世界",
    technology_level: str = "未知",
) -> tuple[str, list[dict]]:
    """
    调用 DM Agent 生成回复。

    Returns
    -------
    (narrative, events) 二元组
    """
    messages = _build_messages(
        player_input,
        game_state,
        chapter_data,
        all_npcs,
        conversation_history,
        summary_memory=summary_memory,
        output_language=output_language,
        world_setting=world_setting,
        technology_level=technology_level,
    )

    for attempt in range(1, max_retries + 1):
        raw = call_llm(
            messages=messages,
            temperature=temperature,
            max_tokens=1024,
            response_format={"type": "json_object"},
        )

        try:
            cleaned = raw.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.split("\n", 1)[-1]
                if cleaned.endswith("```"):
                    cleaned = cleaned[:-3]
                cleaned = cleaned.strip()
            parsed = json.loads(cleaned)
            narrative = parsed.get("narrative", "")
            events = parsed.get("events", [])

            if not isinstance(narrative, str) or not narrative:
                raise ValueError("narrative 为空或类型错误")
            if not isinstance(events, list):
                events = []

            validated_events = []
            for e in events:
                if isinstance(e, dict) and "type" in e and "target" in e:
                    validated_events.append({
                        "type": e["type"],
                        "target": e["target"],
                        "value": e.get("value"),
                    })

            return narrative, validated_events

        except (json.JSONDecodeError, ValueError, KeyError) as e:
            if attempt < max_retries:
                print(f"[DM Agent] JSON 解析失败 (第{attempt}次): {e}，重试中...")
            else:
                print(f"[DM Agent] JSON 解析在 {max_retries} 次后仍失败，返回原始文本。")
                return raw, []

    return "（DM 陷入沉思...请再试一次）", []
