"""
海龟汤裁定器 — 根据章节口径规则判定回答，并生成裏世界环境演绎。
"""

import json

from llm.client import call_llm


ALLOWED_ANSWER_CODES = {"YES", "NO", "BLOCK"}

ANSWER_TEXT_MAP = {
    "zh-Hans": {"YES": "是", "NO": "不是", "BLOCK": "现在不能说"},
    "zh-Hant": {"YES": "是", "NO": "不是", "BLOCK": "現在不能說"},
    "en-US": {"YES": "Yes", "NO": "No", "BLOCK": "Cannot tell now"},
}


def answer_code_to_text(output_language: str, answer_code: str) -> str:
    lang = output_language if output_language in ANSWER_TEXT_MAP else "zh-Hans"
    return ANSWER_TEXT_MAP.get(lang, ANSWER_TEXT_MAP["zh-Hans"]).get(answer_code, "No")


def answer_code_from_value(value: str | None) -> str:
    """兼容中文/繁体/英文历史值，转换为 canonical code。"""
    if not value:
        return "NO"
    text = str(value).strip().lower()

    mapping = {
        "yes": "YES",
        "y": "YES",
        "是": "YES",
        "true": "YES",
        "no": "NO",
        "n": "NO",
        "不是": "NO",
        "false": "NO",
        "现在不能说": "BLOCK",
        "現在不能說": "BLOCK",
        "cannot tell now": "BLOCK",
        "not now": "BLOCK",
        "blocked": "BLOCK",
    }
    return mapping.get(text, "NO")


def answer_code_from_expected(expected_answer: str | None, expected_code: str | None) -> str:
    """统一 Gate 期望值，优先 expected_answer_code，兼容旧 expected_answer。"""
    if isinstance(expected_code, str) and expected_code in ALLOWED_ANSWER_CODES:
        return expected_code
    return answer_code_from_value(expected_answer)


def _language_requirement(output_language: str) -> str:
    if output_language == "en-US":
        return (
            "- answer_text and narration must be in natural American English.\n"
            "- If answer_code is YES/NO/BLOCK, answer_text must be Yes/No/Cannot tell now."
        )
    if output_language == "zh-Hant":
        return (
            "- answer_text 與 narration 必須使用繁體中文。\n"
            "- 若 answer_code 為 YES/NO/BLOCK，answer_text 必須為 是/不是/現在不能說。"
        )
    return (
        "- answer_text 与 narration 必须使用简体中文。\n"
        "- 若 answer_code 为 YES/NO/BLOCK，answer_text 必须为 是/不是/现在不能说。"
    )


SYSTEM_PROMPT_TEMPLATE = """\
你是一场海龟汤推理游戏的主持人，同时也是「裏世界」的叙述者。

## 双重职责
1. 根据规则判断回答。
2. 生成1-2句氛围叙述。

## 裁定优先级（必须严格遵守）
1. 先匹配 cannot_answer：精确命中秘密问题 -> answer_code=BLOCK
2. 再匹配 can_answer：按规则给 YES/NO
3. 其余按 story_truth + chapter_timeline 给 YES/NO

## Gate 触发规则
- 只有当玩家问题与某个 gate 的 semantic_target 严格语义等价时，才可填 gate_triggered。
- 主题相关但问题不同，不可触发。

## 输出语言要求
{language_requirement}

## 输出格式
直接输出 JSON，禁止 markdown：
{{
  "answer_code": "YES|NO|BLOCK",
  "answer_text": "...",
  "narration": "...",
  "matched_rule_id": "..." or null,
  "gate_triggered": "..." or null
}}
"""


def judge_question(
    player_question: str,
    chapter_data: dict,
    qa_history: list[dict],
    story_truth: str = "",
    gates_unlocked: dict | None = None,
    temperature: float = 0.2,
    max_retries: int = 3,
    output_language: str = "zh-Hans",
) -> dict:
    """
    判定玩家提问的回答并生成裏世界演绎。

    Returns
    -------
    {
        "answer_code": "YES" | "NO" | "BLOCK",
        "answer_text": str,
        "answer": str,  # 兼容旧字段
        "narration": str,
        "matched_rule_id": str | None,
        "gate_triggered": str | None,
    }
    """
    chapter = chapter_data.get("chapter", {})
    chapter_id = chapter.get("id", "")
    answer_rules = chapter.get("answer_rules", {})

    all_gates = chapter.get("gates", [])
    unlocked_map = (gates_unlocked or {}).get(chapter_id, {})
    locked_gates = [
        {"id": g["id"], "hint": g.get("semantic_target", "")}
        for g in all_gates
        if g.get("required") and not unlocked_map.get(g.get("id"), False)
    ]

    payload = {
        "player_question": player_question,
        "story_truth": story_truth,
        "chapter_timeline": chapter.get("timeline", ""),
        "soup_text_summary": chapter.get("npc_summary", ""),
        "can_answer_rules": answer_rules.get("can_answer", []),
        "cannot_answer_rules": answer_rules.get("cannot_answer", []),
        "gates": all_gates,
        "locked_gates": locked_gates,
        "recent_qa": qa_history[-6:],
        "output_language": output_language,
    }

    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
        language_requirement=_language_requirement(output_language)
    )

    try:
        raw = call_llm(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            temperature=temperature,
            max_tokens=500,
            max_retries=max_retries,
            response_format={"type": "json_object"},
        )
        parsed = json.loads(raw)
    except Exception:
        code = "BLOCK"
        answer_text = answer_code_to_text(output_language, code)
        return {
            "answer_code": code,
            "answer_text": answer_text,
            "answer": answer_text,
            "narration": _fallback_narration(code),
            "matched_rule_id": None,
            "gate_triggered": None,
        }

    code = parsed.get("answer_code")
    if not isinstance(code, str) or code not in ALLOWED_ANSWER_CODES:
        code = answer_code_from_value(parsed.get("answer") or parsed.get("answer_text"))

    answer_text = parsed.get("answer_text")
    if not isinstance(answer_text, str) or not answer_text.strip():
        answer_text = answer_code_to_text(output_language, code)
    else:
        answer_text = answer_text.strip()

    narration = parsed.get("narration", "")
    if not isinstance(narration, str):
        narration = ""
    narration = narration.strip()
    if len(narration) > 200:
        narration = narration[:200]
    if not narration:
        narration = _fallback_narration(code)

    matched_rule_id = parsed.get("matched_rule_id")
    if matched_rule_id is not None and not isinstance(matched_rule_id, str):
        matched_rule_id = None

    gate_triggered = parsed.get("gate_triggered")
    if gate_triggered is not None and not isinstance(gate_triggered, str):
        gate_triggered = None

    return {
        "answer_code": code,
        "answer_text": answer_text,
        "answer": answer_text,
        "narration": narration,
        "matched_rule_id": matched_rule_id,
        "gate_triggered": gate_triggered,
    }


_FALLBACK_POOL = {
    "YES": [
        "壁炉旁的铃铛轻轻响了一下，像是某种许可。",
        "日记自动翻了半页，纸边微微发暖。",
        "嗡鸣声降了半个调，像松了一口气。",
    ],
    "NO": [
        "走廊尽头的灯闪了一下，恢复原样，像什么都没听到。",
        "铃铛沉默地垂着，一动不动。风停了。",
        "某扇门轻轻合上，回声消失在墙壁里。",
    ],
    "BLOCK": [
        "嗡鸣声忽然变得尖锐，日记纸边开始发黑。",
        "地下室传来一声沉闷的锁扣声，温度降了几度。",
        "墙皮无声地掉了一片，露出下面更深的暗色。",
    ],
}

_fallback_idx = 0


def _fallback_narration(answer_code: str) -> str:
    global _fallback_idx
    pool = _FALLBACK_POOL.get(answer_code, _FALLBACK_POOL["BLOCK"])
    text = pool[_fallback_idx % len(pool)]
    _fallback_idx += 1
    return text
