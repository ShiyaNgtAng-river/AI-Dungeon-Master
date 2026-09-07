"""
Checkpoint 校准器 — 在 checkpoint 时判定状态。
"""

import json

from engine.game_state import format_state_summary
from llm.client import call_llm


def decide_checkpoint_state(
    checkpoint_data: dict,
    game_state: dict,
    recent_history: list[dict],
    summary_text: str = "",
    confidence_threshold: float = 0.6,
    temperature: float = 0.2,
) -> tuple[str, str, float]:
    """
    返回 (state, reason, confidence)。
    若判定失败则回退到 other_fallback 或第一个可用状态。
    """
    states = checkpoint_data.get("states", {})
    if not states:
        return "OTHER", "checkpoint 未定义 states", 0.0

    allowed_states = list(states.keys())
    fallback = checkpoint_data.get("other_fallback", "OTHER")
    if fallback not in allowed_states:
        fallback = allowed_states[0]

    payload = {
        "checkpoint_states": checkpoint_data.get("states", {}),
        "checkpoint_description": checkpoint_data.get(
            "soft_condition",
            checkpoint_data.get("trigger", {}).get("soft_condition", ""),
        ),
        "game_state_summary": format_state_summary(game_state),
        "summary_memory": summary_text,
        "recent_history": recent_history[-6:],
    }

    messages = [
        {
            "role": "system",
            "content": (
                "你是剧情一致性校准器。根据给定 checkpoint 定义和近期历史，"
                "从允许状态中选择一个最符合当前进展的状态。"
                "必须返回 JSON: {\"state\": \"...\", \"reason\": \"...\", \"confidence\": 0-1}"
            ),
        },
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]

    try:
        raw = call_llm(
            messages=messages,
            temperature=temperature,
            max_tokens=200,
            response_format={"type": "json_object"},
        )
        parsed = json.loads(raw)
        state = parsed.get("state", fallback)
        reason = str(parsed.get("reason", "LLM 判定"))
        confidence = float(parsed.get("confidence", 0.0))
        if state not in allowed_states:
            return fallback, "LLM 返回状态非法，已回退", 0.0
        if confidence < confidence_threshold:
            return fallback, f"置信度不足({confidence:.2f})，已回退", confidence
        return state, reason, confidence
    except Exception:
        return fallback, "校准失败，使用回退状态", 0.0
