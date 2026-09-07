"""
Checkpoint 调度器 — 检查触发条件、执行校准、写回 game_state。
"""

import re
import json
from datetime import datetime

from engine.alignment import decide_checkpoint_state
from engine.script_loader import check_hard_conditions
from llm.client import call_llm


def maybe_trigger_checkpoint(
    game_state: dict,
    chapter_data: dict,
    recent_history: list[dict],
    summary_text: str = "",
    confidence_threshold: float = 0.6,
    temperature: float = 0.2,
) -> tuple[dict, dict | None]:
    """
    触发时返回 (updated_state, result_info)；否则 result_info 为 None。
    """
    checkpoint = chapter_data.get("checkpoint")
    if not checkpoint:
        return game_state, None

    cp_id = checkpoint.get("id")
    if not cp_id:
        return game_state, None

    progress = game_state.setdefault("progress", {})
    cp_map = progress.setdefault("checkpoints", {})
    cp_record = cp_map.setdefault(
        cp_id, {"status": "pending", "result": None, "completed_at": None}
    )
    if cp_record.get("status") == "completed":
        return game_state, None

    hard_conditions = checkpoint.get("trigger", {}).get("hard_conditions", [])
    if hard_conditions and not check_hard_conditions(hard_conditions, game_state):
        return game_state, None

    soft_condition = checkpoint.get("trigger", {}).get("soft_condition", "")
    if soft_condition and not _is_soft_condition_satisfied(soft_condition, recent_history):
        return game_state, None

    state, reason, confidence = decide_checkpoint_state(
        checkpoint_data=checkpoint,
        game_state=game_state,
        recent_history=recent_history,
        summary_text=summary_text,
        confidence_threshold=confidence_threshold,
        temperature=temperature,
    )

    cp_record["status"] = "completed"
    cp_record["result"] = state
    cp_record["completed_at"] = datetime.now().isoformat(timespec="seconds")

    flags = progress.setdefault("story_flags", [])
    cp_flag = f"checkpoint:{cp_id}:{state}"
    if cp_flag not in flags:
        flags.append(cp_flag)

    consequence = checkpoint.get("consequences", {}).get(state, "")
    next_chapter = _extract_next_chapter(checkpoint, state)
    if next_chapter:
        game_state["world"]["current_chapter"] = next_chapter

    result = {
        "checkpoint_id": cp_id,
        "state": state,
        "reason": reason,
        "confidence": confidence,
        "consequence": consequence,
        "next_chapter": next_chapter,
    }
    return game_state, result


def _is_soft_condition_satisfied(soft_condition: str, recent_history: list[dict]) -> bool:
    """用轻量 LLM 评估 soft_condition。"""
    messages = [
        {
            "role": "system",
            "content": (
                "你是条件判断器。根据最近的对话历史，判断以下条件是否已满足。"
                "只回答JSON: {\"satisfied\": true/false}"
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "soft_condition": soft_condition,
                    "recent_history": recent_history[-3:],
                },
                ensure_ascii=False,
            ),
        },
    ]
    try:
        raw = call_llm(
            messages=messages,
            temperature=0.1,
            max_tokens=50,
            response_format={"type": "json_object"},
        )
        parsed = json.loads(raw)
        return bool(parsed.get("satisfied", False))
    except Exception:
        return False


def _extract_next_chapter(checkpoint_data: dict, state: str) -> str | None:
    """优先读取显式 next_chapter，不存在时再从 consequences 文本兜底。"""
    explicit_map = checkpoint_data.get("next_chapter", {})
    explicit_next = explicit_map.get(state) if isinstance(explicit_map, dict) else None
    if explicit_next:
        return explicit_next

    consequence_text = checkpoint_data.get("consequences", {}).get(state, "")
    if not consequence_text:
        return None
    match = re.search(r"(chapter_\d+)", consequence_text)
    return match.group(1) if match else None
