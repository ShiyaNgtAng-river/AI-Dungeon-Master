"""
海龟汤主循环 — 支持问答裁定、Gate 解锁与终章抉择。
"""

import copy

from engine.game_state import (
    load_game_state,
    load_game_state_safe,
    GameStateLoadError,
    save_game_state,
    new_game,
    has_save,
    format_state_summary,
)
from engine.memory_manager import MemoryManager
from engine.script_loader import load_chapter, load_npcs
from engine.i18n import t, get_current_language
from engine.turtle_soup_agent import (
    judge_question,
    answer_code_from_expected,
)


def _print_block(text: str) -> None:
    print()
    print("-" * 50)
    print(text)
    print("-" * 50)
    print()


def _msg(key: str, **kwargs) -> str:
    return t(key, get_current_language(), **kwargs)


def _print_help() -> None:
    print(_msg("cmd.help.quit"))
    print(_msg("cmd.help.state"))
    print(_msg("cmd.help.save"))
    print(_msg("cmd.help.gates"))
    print(_msg("cmd.help.help"))


def _ensure_lateral_progress(progress: dict) -> None:
    progress.setdefault("access_level", 0)
    progress.setdefault("gates_unlocked", {})
    progress.setdefault("qa_log", [])
    progress.setdefault("continue_decisions", [])
    progress.setdefault("turn_count", 0)
    progress.setdefault("chapter_question_count", {})
    progress.setdefault("chapter_miss_streak", {})
    progress.setdefault("chapter_echo_index", {})
    progress.setdefault("chapter_soft_refusal_index", {})


def _required_gates(chapter_data: dict) -> list[dict]:
    chapter = chapter_data.get("chapter", {})
    return [g for g in chapter.get("gates", []) if g.get("required", False)]


def _all_gates_unlocked(chapter_data: dict, gates_unlocked: dict, chapter_id: str) -> bool:
    required = _required_gates(chapter_data)
    if not required:
        return True
    unlocked_map = gates_unlocked.get(chapter_id, {})
    return all(unlocked_map.get(g.get("id"), False) for g in required)


def _format_gate_status(chapter_data: dict, gates_unlocked: dict, chapter_id: str) -> str:
    gates = chapter_data.get("chapter", {}).get("gates", [])
    if not gates:
        return _msg("turtle.gate_status.no_gate")

    unlocked_map = gates_unlocked.get(chapter_id, {})
    lines = []
    for gate in gates:
        gid = gate.get("id", "unknown")
        status = (
            _msg("turtle.gate_status.unlocked")
            if unlocked_map.get(gid, False)
            else _msg("turtle.gate_status.locked")
        )
        role = (
            _msg("turtle.gate_status.main_gate")
            if gate.get("required", False)
            else _msg("turtle.gate_status.sub_gate")
        )
        lines.append(f"- {gid} ({role}): {status}")
    return "\n".join(lines)


def _format_audit_entries(continue_decisions: list[dict]) -> str:
    lines = [_msg("turtle.audit.init_line")]
    for item in continue_decisions:
        day = item.get("day", "unknown")
        if item.get("chose_continue", False):
            lines.append(_msg("turtle.audit.access_gained", day=day))
        else:
            lines.append(_msg("turtle.audit.access_stopped", day=day))
    return "\n".join(lines)


def _resolve_final_choice(raw: str) -> str | None:
    text = raw.strip().lower()
    if ("左" in raw) or text in {"left", "l"}:
        return "left"
    if ("右" in raw) or text in {"right", "r"}:
        return "right"
    return None


def _locked_required_gates(chapter_data: dict, progress: dict, chapter_id: str) -> list[dict]:
    chapter = chapter_data.get("chapter", {})
    unlocked_map = progress.get("gates_unlocked", {}).get(chapter_id, {})
    return [
        g
        for g in chapter.get("gates", [])
        if g.get("required", False) and not unlocked_map.get(g.get("id"), False)
    ]


def _default_echo_hint(locked_required: list[dict]) -> str:
    if not locked_required:
        return _msg("turtle.hint.default_generic")
    target = locked_required[0].get("semantic_target", "")
    if target:
        return _msg("turtle.hint.target_hint", target=target[:18])
    return _msg("turtle.hint.repeat_line")


def _maybe_show_adaptive_hint(chapter_data: dict, progress: dict, chapter_id: str) -> None:
    """连续失配时触发回音/强提示，避免玩家长时间卡关。"""
    miss = progress.get("chapter_miss_streak", {}).get(chapter_id, 0)
    if miss < 2:
        return

    locked_required = _locked_required_gates(chapter_data, progress, chapter_id)
    if not locked_required:
        return

    hint_cfg = chapter_data.get("chapter", {}).get("hint_system", {})
    echo_hints = hint_cfg.get("echo_hints", [])
    strong_hint = hint_cfg.get("strong_hint", "")

    # 连续 2 次失配给“线索回音”；连续 3 次给“强提示”。
    if miss % 3 == 0:
        text = strong_hint or _default_echo_hint(locked_required)
        print(f"\n    ——{text}\n")
        return

    if miss % 2 == 0:
        if echo_hints:
            idx_map = progress.setdefault("chapter_echo_index", {})
            idx = idx_map.get(chapter_id, 0)
            text = echo_hints[idx % len(echo_hints)]
            idx_map[chapter_id] = idx + 1
        else:
            text = _default_echo_hint(locked_required)
        print(f"\n    ——{text}\n")


def _maybe_show_soft_refusal(chapter_data: dict, progress: dict, chapter_id: str, answer: str) -> None:
    """拒答时补一条柔性氛围反馈，降低“纯阻断”挫败感。"""
    if answer != "BLOCK":
        return

    soft_lines = chapter_data.get("chapter", {}).get("hint_system", {}).get("soft_refusal", [])
    if not soft_lines:
        return

    idx_map = progress.setdefault("chapter_soft_refusal_index", {})
    idx = idx_map.get(chapter_id, 0)
    text = soft_lines[idx % len(soft_lines)]
    idx_map[chapter_id] = idx + 1
    print(f"    （{text}）")


def _inject_npc_summary(chapter_data: dict, all_npcs: dict) -> None:
    chapter = chapter_data.setdefault("chapter", {})
    if chapter.get("npc_summary"):
        return
    pieces = []
    for name, info in all_npcs.items():
        role = info.get("role", "")
        pieces.append(f"{name}({role})")
    chapter["npc_summary"] = "、".join(pieces)


def _handle_corrupted_save(
    path: str,
    error: Exception,
    save_path: str,
    story_data: dict,
    last_loaded_state: dict | None = None,
) -> dict | None:
    """海龟汤模式下处理损坏存档：备份 -> 上次成功状态 -> 新游戏。"""
    _ = (path, story_data)
    print(_msg("system.corrupted_save", error=str(error)))
    print(_msg("system.restore_backup_prompt"), end="")
    choice = input().strip().lower()
    if choice == "y":
        try:
            restored = load_game_state(f"{save_path}.bak")
            print(_msg("system.restore_backup_ok"))
            return restored
        except Exception:  # noqa: BLE001 - 继续降级到上次成功状态/新游戏
            print(_msg("system.corrupted_backup"))
            print(_msg("system.restore_backup_fail"))

    if last_loaded_state is not None:
        return copy.deepcopy(last_loaded_state)

    print(_msg("system.start_new_after_corrupt"))
    return None


def _run_finale(chapter_data: dict, progress: dict) -> None:
    chapter = chapter_data.get("chapter", {})
    _print_block(chapter.get("soup_text", ""))

    audit_template = chapter.get("audit_template", "{audit_entries}")
    audit_entries = _format_audit_entries(progress.get("continue_decisions", []))
    _print_block(audit_template.format(audit_entries=audit_entries))

    choices = chapter.get("choices", {})
    print(_msg("prompt.final_choice_hint"))
    while True:
        choice_raw = input(_msg("prompt.final_choice"))
        choice_key = _resolve_final_choice(choice_raw)
        if choice_key in choices:
            break
        print(_msg("system.choose_left_right"))

    chosen = choices[choice_key]
    progress["final_choice"] = choice_key
    _print_block(chosen.get("ending_text", ""))


def run_lateral_thinking(
    config: dict,
    manifest: dict,
    story_data: dict,
    module_dir: str,
    save_path: str,
    memory_file: str,
    language: str = "zh-Hans",
) -> None:
    """海龟汤模式的完整游戏主循环。"""
    module_info = manifest.get("module", {})
    lt_cfg = module_info.get("lateral_thinking", {})
    template_text = lt_cfg.get("template_text", "")
    opening_text = lt_cfg.get("opening_text", "")
    story_truth = lt_cfg.get("story_truth", "")
    entry_chapter = module_info.get("entry_chapter", "day_1")

    max_memory_turns = config.get("game", {}).get("short_term_memory_turns", 10)
    summary_trigger_turns = config.get("memory", {}).get("summary_trigger_turns", 8)
    summary_min_turns = config.get("memory", {}).get("summary_min_turns", 4)

    memory_manager = MemoryManager(
        memory_file=memory_file,
        max_short_term_turns=max_memory_turns,
        summary_trigger_turns=summary_trigger_turns,
        summary_min_turns=summary_min_turns,
    )
    last_loaded_state: dict | None = None

    if has_save(save_path):
        print(_msg("system.load_found"), end="")
        choice = input().strip().lower()
        if choice == "y":
            try:
                game_state = load_game_state_safe(
                    save_path,
                    fallback_path=save_path + ".bak",
                    on_error=lambda p, e: _handle_corrupted_save(
                        p,
                        e,
                        save_path,
                        story_data,
                        last_loaded_state,
                    ),
                )
                if game_state is None:
                    game_state = new_game(story_data)
                    game_state["world"]["current_chapter"] = entry_chapter
                    memory_manager.reset()
                    print(_msg("system.new_game"))
                else:
                    last_loaded_state = copy.deepcopy(game_state)
                    memory_manager.load()
                    print(_msg("system.load_ok"))
            except GameStateLoadError as err:
                print(_msg("system.corrupted_save", error=str(err)))
                game_state = new_game(story_data)
                game_state["world"]["current_chapter"] = entry_chapter
                memory_manager.reset()
                print(_msg("system.start_new_after_corrupt"))
        else:
            game_state = new_game(story_data)
            game_state["world"]["current_chapter"] = entry_chapter
            memory_manager.reset()
            print(_msg("system.new_game"))
    else:
        game_state = new_game(story_data)
        game_state["world"]["current_chapter"] = entry_chapter
        memory_manager.reset()
        print(_msg("system.new_game"))

    progress = game_state.setdefault("progress", {})
    _ensure_lateral_progress(progress)
    progress["ui_language"] = language

    all_npcs = load_npcs(module_dir=module_dir, lang=language)

    if opening_text:
        _print_block(opening_text)

    current_chapter_id = game_state.get("world", {}).get("current_chapter", entry_chapter)
    chapter_data = load_chapter(current_chapter_id, module_dir=module_dir, lang=language)
    _inject_npc_summary(chapter_data, all_npcs)

    if not chapter_data.get("chapter", {}).get("is_finale", False):
        _print_block(chapter_data.get("chapter", {}).get("soup_text", ""))

    save_game_state(game_state, save_path)
    memory_manager.save()

    while True:
        chapter = chapter_data.get("chapter", {})
        current_chapter_id = chapter.get("id", game_state["world"].get("current_chapter", entry_chapter))

        if chapter.get("is_finale", False):
            _run_finale(chapter_data, progress)
            save_game_state(game_state, save_path)
            memory_manager.save()
            print(_msg("turtle.game_over"))
            return

        try:
            player_input = input(_msg("prompt.question_turtle")).strip()
        except (EOFError, KeyboardInterrupt):
            print("\n" + _msg("system.interrupted"))
            save_game_state(game_state, save_path)
            memory_manager.save()
            return
        except (UnicodeDecodeError, OSError):
            print(_msg("system.input_invalid"))
            continue

        if not player_input:
            continue
        player_input = player_input.replace("❓ 你的问题 >", "").replace("❓ 你的问题 > ", "").strip()
        if not player_input or len(player_input) < 2:
            continue

        if player_input.startswith("/"):
            cmd = player_input.lower()
            if cmd == "/quit":
                save_game_state(game_state, save_path)
                memory_manager.save()
                print(_msg("system.save_bye"))
                return
            if cmd == "/save":
                save_game_state(game_state, save_path)
                memory_manager.save()
                print(_msg("system.save_ok"))
                continue
            if cmd == "/state":
                print("\n" + format_state_summary(game_state) + "\n")
                continue
            if cmd == "/gates":
                print("\n" + _format_gate_status(chapter_data, progress.get("gates_unlocked", {}), current_chapter_id) + "\n")
                continue
            if cmd == "/help":
                _print_help()
                continue
            print(_msg("system.unknown_cmd", cmd=player_input))
            continue

        result = judge_question(
            player_question=player_input,
            chapter_data=chapter_data,
            qa_history=progress.get("qa_log", []),
            story_truth=story_truth,
            gates_unlocked=progress.get("gates_unlocked", {}),
            temperature=0.2,
            max_retries=config.get("game", {}).get("max_retries", 3),
            output_language=language,
        )

        narration = result.get("narration", "")
        if narration:
            print(f"\n    {narration}")
        print("\n" + _msg("turtle.answer_wrap", answer=result["answer_text"]) + "\n")

        qa_entry = {
            "question": player_input,
            "answer": result["answer_text"],
            "answer_code": result["answer_code"],
            "day": current_chapter_id,
            "rule": result.get("matched_rule_id"),
        }
        progress.setdefault("qa_log", []).append(qa_entry)
        progress["turn_count"] = int(progress.get("turn_count", 0)) + 1
        chapter_questions = progress.setdefault("chapter_question_count", {})
        chapter_questions[current_chapter_id] = chapter_questions.get(current_chapter_id, 0) + 1

        memory_text = result["answer_text"]
        if narration:
            memory_text = f"{memory_text} | {narration}"
        memory_manager.add_turn(player_input=player_input, dm_response=memory_text)

        unlocked_gate_this_turn = False
        gate_id = result.get("gate_triggered")
        gate_lookup = {g.get("id"): g for g in chapter.get("gates", [])}
        if gate_id and gate_id in gate_lookup:
            gate_def = gate_lookup[gate_id]
            expected_code = answer_code_from_expected(
                expected_answer=gate_def.get("expected_answer"),
                expected_code=gate_def.get("expected_answer_code"),
            )
            if result["answer_code"] == expected_code:
                unlocked_gate_this_turn = True
                chapter_map = progress.setdefault("gates_unlocked", {}).setdefault(current_chapter_id, {})
                if not chapter_map.get(gate_id, False):
                    chapter_map[gate_id] = True
                    print(_msg("turtle.gate_unlocked", gate_id=gate_id))

        miss_map = progress.setdefault("chapter_miss_streak", {})
        if unlocked_gate_this_turn:
            miss_map[current_chapter_id] = 0
        else:
            miss_map[current_chapter_id] = int(miss_map.get(current_chapter_id, 0)) + 1

        _maybe_show_soft_refusal(
            chapter_data=chapter_data,
            progress=progress,
            chapter_id=current_chapter_id,
            answer=result["answer_code"],
        )
        _maybe_show_adaptive_hint(chapter_data, progress, current_chapter_id)

        if _all_gates_unlocked(chapter_data, progress.get("gates_unlocked", {}), current_chapter_id):
            transition = chapter.get("transition", {})
            if transition.get("ask_continue", False):
                prompt = transition.get("continue_prompt", _msg("prompt.continue_default"))
                print(prompt)
                decision = input(_msg("prompt.decision_turtle")).strip()
                _YES_WORDS = {"是", "继续", "深入", "yes", "y", "继续深入", "好", "确认", "进"}
                chose_continue = any(w in decision for w in _YES_WORDS) if decision else False
                progress.setdefault("continue_decisions", []).append(
                    {"day": current_chapter_id, "chose_continue": chose_continue}
                )
                if not chose_continue:
                    print(_msg("turtle.stop_decision"))
                    save_game_state(game_state, save_path)
                    memory_manager.save()
                    return

            transition_text = transition.get("transition_text", "")
            if transition_text:
                _print_block(transition_text)

            progress["access_level"] = int(progress.get("access_level", 0)) + 1
            next_chapter = transition.get("next_chapter")
            if not next_chapter:
                save_game_state(game_state, save_path)
                memory_manager.save()
                return

            game_state["world"]["current_chapter"] = next_chapter
            chapter_data = load_chapter(next_chapter, module_dir=module_dir, lang=language)
            _inject_npc_summary(chapter_data, all_npcs)
            progress.setdefault("chapter_miss_streak", {})[next_chapter] = 0
            progress.setdefault("chapter_echo_index", {})[next_chapter] = 0
            progress.setdefault("chapter_question_count", {})[next_chapter] = 0

            if not chapter_data.get("chapter", {}).get("is_finale", False):
                _print_block(chapter_data.get("chapter", {}).get("soup_text", ""))

        if memory_manager.should_compress(progress.get("turn_count", 0)):
            memory_manager.compress_recent(use_llm=True, lang=get_current_language())

        save_game_state(game_state, save_path)
        memory_manager.save()
