"""
AI Dungeon Master — 主程序入口。
根据模组 manifest 中的 game_mode 分发到 RPG 或海龟汤流程。
"""

import os
import sys
import copy
import yaml

# 让所有模块都能正确导入
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_DIR)


# 从 .env 文件加载环境变量（简易实现，不依赖 python-dotenv）
def _load_dotenv():
    env_path = os.path.join(PROJECT_DIR, ".env")
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, value = line.partition("=")
                    os.environ.setdefault(key.strip(), value.strip())


_load_dotenv()

from engine.game_state import (
    load_game_state,
    load_game_state_safe,
    GameStateLoadError,
    save_game_state,
    new_game,
    has_save,
    format_state_summary,
)
from engine.script_loader import (
    set_active_module,
    set_active_language,
    load_manifest,
    load_story,
    load_chapter,
    load_npcs,
)
from engine.dm_agent import generate
from engine.event_processor import process_events, register_known_npcs
from engine.memory_manager import MemoryManager
from engine.checkpoint import maybe_trigger_checkpoint
from engine.turtle_soup_loop import run_lateral_thinking
from engine.i18n import (
    load_app_locale,
    normalize_lang,
    set_current_language,
    get_current_language,
    language_display_name,
    t,
)


def load_config() -> dict:
    config_path = os.path.join(PROJECT_DIR, "config.yaml")
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def save_config(config: dict) -> None:
    config_path = os.path.join(PROJECT_DIR, "config.yaml")
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(config, f, allow_unicode=True, sort_keys=False)


def _msg(key: str, lang: str | None = None, **kwargs) -> str:
    return t(key, lang or get_current_language(), **kwargs)


def select_language(config: dict) -> str:
    """启动时选择语言，并按配置决定是否写回默认值。"""
    i18n_cfg = config.setdefault("i18n", {})
    default_lang = normalize_lang(i18n_cfg.get("default_language", "zh-Hans"))
    supported = i18n_cfg.get("supported_languages", ["zh-Hans", "zh-Hant", "en-US"])
    supported = [normalize_lang(code) for code in supported]
    startup_select = bool(i18n_cfg.get("startup_select", True))
    persist_selection = bool(i18n_cfg.get("persist_selection", True))
    fallback_lang = normalize_lang(i18n_cfg.get("fallback_language", "zh-Hans"))

    load_app_locale(PROJECT_DIR, default_lang, fallback=fallback_lang)
    set_current_language(default_lang)

    if not startup_select:
        return default_lang

    print(_msg("language.select_title"))
    print(_msg("language.default_hint", default_lang=language_display_name(default_lang)))
    for idx, code in enumerate(supported, start=1):
        print(
            _msg(
                "language.option",
                index=idx,
                name=language_display_name(code),
                code=code,
            )
        )

    raw = input("> ").strip()
    if not raw:
        return default_lang

    chosen = default_lang
    if raw.isdigit():
        i = int(raw) - 1
        if 0 <= i < len(supported):
            chosen = supported[i]
        else:
            print(_msg("language.invalid"))
    else:
        normalized = normalize_lang(raw)
        if normalized in supported:
            chosen = normalized
        else:
            print(_msg("language.invalid"))

    if persist_selection:
        i18n_cfg["default_language"] = chosen
        config["i18n"] = i18n_cfg
        save_config(config)

    return chosen


def check_redlines(narrative: str, chapter_data: dict, language: str) -> bool:
    """检查叙事中是否包含红线关键词。返回 True 表示通过。"""
    keywords = chapter_data.get("chapter", {}).get("redline_keywords", [])
    for kw in keywords:
        if kw in narrative:
            print(_msg("rpg.redline", language, keyword=kw))
            return False
    return True


def print_banner(game_mode: str, module_name: str) -> None:
    print(_msg("banner.title_line"))
    if game_mode == "lateral_thinking":
        print(_msg("banner.turtle.title", module_name=module_name))
    else:
        print(_msg("banner.rpg.title"))
    print(_msg("banner.title_line"))
    if game_mode == "lateral_thinking":
        print(_msg("banner.turtle.intro"))
        print(_msg("banner.special"))
        print(_msg("cmd.help.quit"))
        print(_msg("cmd.help.save"))
        print(_msg("cmd.help.gates"))
        print(_msg("cmd.help.help"))
    else:
        print(_msg("banner.rpg.intro"))
        print(_msg("banner.special"))
        print(_msg("cmd.help.quit"))
        print(_msg("cmd.help.new"))
        print(_msg("cmd.help.state"))
        print(_msg("cmd.help.save"))
        print(_msg("cmd.help.history"))
        print(_msg("cmd.help.help"))
    print(_msg("banner.title_line"))
    print()


def print_narrative(text: str) -> None:
    print()
    print("-" * 50)
    print(text)
    print("-" * 50)
    print()


def _build_rpg_opening(chapter_info: dict, language: str) -> str:
    name = chapter_info.get("name", "Unknown Area")
    setting = chapter_info.get("setting", "")
    time_of_day = chapter_info.get("time_of_day", "unknown")
    atmosphere = chapter_info.get("atmosphere", "unknown")

    if language == "en-US":
        return (
            f"📍 {name}\n\n"
            f"{setting}\n\n"
            f"The time is {time_of_day}, and the air is filled with {atmosphere}.\n\n"
            "Your adventure begins — what will you do?"
        )
    if language == "zh-Hant":
        return (
            f"📍 {name}\n\n"
            f"{setting}\n\n"
            f"時間是{time_of_day}，空氣中充滿了{atmosphere}的氣息。\n\n"
            "你的冒險開始了——你要做什麼？"
        )
    return (
        f"📍 {name}\n\n"
        f"{setting}\n\n"
        f"时间是{time_of_day}，空气中充满了{atmosphere}的气息。\n\n"
        "你的冒险开始了——你要做什么？"
    )


def _handle_corrupted_save(
    path: str,
    error: Exception,
    save_path: str,
    save_root: str,
    story_data: dict,
    language: str,
    last_loaded_state: dict | None = None,
) -> dict | None:
    """处理存档损坏：优先尝试备份，其次回退上次成功状态，最后让上层走新游戏。"""
    _ = (path, save_root, story_data)
    print(_msg("system.corrupted_save", language, error=str(error)))
    print(_msg("system.restore_backup_prompt", language), end="")
    choice = input().strip().lower()
    if choice == "y":
        try:
            restored = load_game_state(f"{save_path}.bak")
            print(_msg("system.restore_backup_ok", language))
            return restored
        except Exception:  # noqa: BLE001 - 备份失败后继续降级
            print(_msg("system.corrupted_backup", language))
            print(_msg("system.restore_backup_fail", language))

    if last_loaded_state is not None:
        return copy.deepcopy(last_loaded_state)

    print(_msg("system.start_new_after_corrupt", language))
    return None


def _run_rpg(
    config: dict,
    manifest: dict,
    story_data: dict,
    all_npcs: dict,
    save_path: str,
    memory_file: str,
    language: str,
) -> None:
    """运行现有 RPG 模式主循环。"""
    module_info = manifest.get("module", {})
    entry_chapter = module_info.get("entry_chapter", "chapter_1")
    world_setting = story_data.get("story", {}).get("world_setting", "奇幻世界")
    technology_level = story_data.get("story", {}).get("technology_level", "未知")

    temperature = config.get("llm", {}).get("temperature", 0.8)
    checkpoint_temperature = config.get("llm", {}).get("temperature_checkpoint", 0.2)
    max_memory_turns = config.get("game", {}).get("short_term_memory_turns", 10)
    checkpoint_conf_threshold = config.get("game", {}).get("checkpoint_confidence_threshold", 0.6)
    max_retries = config.get("game", {}).get("max_retries", 3)
    summary_trigger_turns = config.get("memory", {}).get("summary_trigger_turns", 8)
    summary_min_turns = config.get("memory", {}).get("summary_min_turns", 4)
    summary_context_items = config.get("memory", {}).get("summary_context_items", 3)

    memory_manager = MemoryManager(
        memory_file=memory_file,
        max_short_term_turns=max_memory_turns,
        summary_trigger_turns=summary_trigger_turns,
        summary_min_turns=summary_min_turns,
    )
    is_new_game = False
    last_loaded_state: dict | None = None
    save_root = os.path.dirname(save_path)

    if has_save(save_path):
        print(_msg("system.load_found", language), end="")
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
                        save_root,
                        story_data,
                        language,
                        last_loaded_state,
                    ),
                )
                if game_state is None:
                    game_state = new_game(story_data)
                    game_state["world"]["current_chapter"] = entry_chapter
                    memory_manager.reset()
                    is_new_game = True
                    print(_msg("system.new_game", language))
                else:
                    last_loaded_state = copy.deepcopy(game_state)
                    memory_manager.load()
                    print(_msg("system.load_ok", language))
            except GameStateLoadError as err:
                print(_msg("system.corrupted_save", language, error=str(err)))
                game_state = new_game(story_data)
                game_state["world"]["current_chapter"] = entry_chapter
                memory_manager.reset()
                is_new_game = True
                print(_msg("system.start_new_after_corrupt", language))
        else:
            game_state = new_game(story_data)
            game_state["world"]["current_chapter"] = entry_chapter
            memory_manager.reset()
            is_new_game = True
            print(_msg("system.new_game", language))
    else:
        game_state = new_game(story_data)
        game_state["world"]["current_chapter"] = entry_chapter
        memory_manager.reset()
        is_new_game = True
        print(_msg("system.new_game", language))
    game_state.setdefault("progress", {})["ui_language"] = language

    # 加载当前章节
    if is_new_game:
        current_chapter = entry_chapter
    else:
        current_chapter = game_state["world"].get("current_chapter", entry_chapter)
    chapter_data = load_chapter(current_chapter, lang=language)

    # 打印开场描述
    chapter_info = chapter_data.get("chapter", {})
    opening = _build_rpg_opening(chapter_info, language)
    print_narrative(opening)

    # 保存初始状态
    save_game_state(game_state, save_path)
    memory_manager.save()

    # 主游戏循环
    while True:
        try:
            player_input = input(_msg("prompt.action_rpg", language)).strip()
        except (EOFError, KeyboardInterrupt):
            print("\n" + _msg("system.interrupted", language))
            save_game_state(game_state, save_path)
            memory_manager.save()
            break
        except (UnicodeDecodeError, OSError):
            print(_msg("system.input_invalid", language))
            continue

        if not player_input or len(player_input) < 2:
            continue

        player_input = player_input.replace("🎮 你的行动 >", "").replace("🎮 你的行动 > ", "").strip()
        if not player_input or len(player_input) < 2:
            continue

        # 特殊命令
        if player_input.startswith("/"):
            cmd = player_input.lower()
            if cmd == "/quit":
                save_game_state(game_state, save_path)
                memory_manager.save()
                print(_msg("system.save_bye", language))
                break
            elif cmd == "/state":
                print("\n" + format_state_summary(game_state) + "\n")
                continue
            elif cmd == "/save":
                save_game_state(game_state, save_path)
                memory_manager.save()
                print(_msg("system.save_ok", language))
                continue
            elif cmd == "/new":
                print(_msg("system.new_confirm", language), end="")
                confirm = input().strip().lower()
                if confirm == "y":
                    game_state = new_game(story_data)
                    game_state["world"]["current_chapter"] = entry_chapter
                    memory_manager.reset()
                    game_state.setdefault("progress", {})["ui_language"] = language
                    chapter_data = load_chapter(entry_chapter, lang=language)
                    chapter_info = chapter_data.get("chapter", {})
                    opening = _build_rpg_opening(chapter_info, language)
                    save_game_state(game_state, save_path)
                    memory_manager.save()
                    print(_msg("system.new_game", language))
                    print_narrative(opening)
                else:
                    print(_msg("system.new_cancel", language))
                continue
            elif cmd == "/help":
                print(_msg("cmd.help.quit", language))
                print(_msg("cmd.help.new", language))
                print(_msg("cmd.help.state", language))
                print(_msg("cmd.help.save", language))
                print(_msg("cmd.help.history", language))
                print(_msg("cmd.help.help", language))
                continue
            elif cmd == "/history":
                for i, entry in enumerate(memory_manager.get_short_term()):
                    print(f"  [{i+1}] 玩家: {entry['player_input'][:50]}...")
                continue
            else:
                print(_msg("system.unknown_cmd", language, cmd=player_input))
                continue

        # 调用 DM Agent
        print(_msg("rpg.dm_thinking", language))

        narrative, events = generate(
            player_input=player_input,
            game_state=game_state,
            chapter_data=chapter_data,
            all_npcs=all_npcs,
            conversation_history=memory_manager.get_short_term(),
            summary_memory=memory_manager.get_summary_text(summary_context_items),
            temperature=temperature,
            max_retries=max_retries,
            output_language=language,
            world_setting=world_setting,
            technology_level=technology_level,
        )

        # 红线检查
        if not check_redlines(narrative, chapter_data, language):
            narrative, events = generate(
                player_input=player_input,
                game_state=game_state,
                chapter_data=chapter_data,
                all_npcs=all_npcs,
                conversation_history=memory_manager.get_short_term(),
                summary_memory=memory_manager.get_summary_text(summary_context_items),
                temperature=max(0.3, temperature - 0.3),
                max_retries=max_retries,
                output_language=language,
                world_setting=world_setting,
                technology_level=technology_level,
            )

        # 处理事件，更新状态
        if events:
            game_state = process_events(events, game_state)
            event_summary = ", ".join(
                f"{e['type']}({e['target']})" for e in events
            )
            print(_msg("rpg.event_prefix", language, summary=event_summary))
        else:
            game_state["progress"]["turn_count"] += 1

        # checkpoint 判定与章节分流
        pending_history = memory_manager.get_short_term() + [
            {"player_input": player_input, "dm_response": narrative}
        ]
        game_state, cp_result = maybe_trigger_checkpoint(
            game_state=game_state,
            chapter_data=chapter_data,
            recent_history=pending_history,
            summary_text=memory_manager.get_summary_text(summary_context_items),
            confidence_threshold=checkpoint_conf_threshold,
            temperature=checkpoint_temperature,
        )
        original_narrative = narrative
        if cp_result:
            print(
                _msg(
                    "rpg.cp_triggered",
                    language,
                    checkpoint_id=cp_result["checkpoint_id"],
                    state=cp_result["state"],
                    confidence=cp_result["confidence"],
                )
            )
            if cp_result.get("consequence"):
                narrative = f"{narrative}\n\n【剧情推进】{cp_result['consequence']}"
            if cp_result.get("next_chapter"):
                try:
                    chapter_data = load_chapter(cp_result["next_chapter"], lang=language)
                    print(
                        _msg(
                            "rpg.chapter_enter",
                            language,
                            chapter_name=chapter_data.get("chapter", {}).get("name", cp_result["next_chapter"]),
                        )
                    )
                except FileNotFoundError:
                    print(_msg("rpg.chapter_missing", language, chapter_id=cp_result["next_chapter"]))
                    game_state["world"]["current_chapter"] = current_chapter

        # 输出叙事
        print_narrative(narrative)

        # 更新记忆（即时记忆 + 定期摘要压缩）
        memory_manager.add_turn(player_input=player_input, dm_response=original_narrative)
        if memory_manager.should_compress(game_state["progress"]["turn_count"]):
            compressed = memory_manager.compress_recent(use_llm=True, lang=language)
            if compressed:
                print(_msg("rpg.memory_compressed", language))

        # 自动保存
        save_game_state(game_state, save_path)
        memory_manager.save()


def main() -> None:
    config = load_config()
    language = select_language(config)
    i18n_cfg = config.get("i18n", {})
    load_app_locale(
        PROJECT_DIR,
        language,
        fallback=i18n_cfg.get("fallback_language", "zh-Hans"),
    )
    set_current_language(language)
    print(_msg("language.selected", language, name=language_display_name(language)))

    active_module = config.get("active_module", "北境废墟")

    module_dir = os.path.join(PROJECT_DIR, "modules", active_module)
    if not os.path.isdir(module_dir):
        raise RuntimeError(f"模组目录不存在: {module_dir}")

    set_active_module(module_dir)
    set_active_language(language)
    manifest = load_manifest(module_dir, lang=language)
    module_info = manifest.get("module", {})
    module_id = module_info.get("id", active_module)
    module_name = module_info.get("name", active_module)
    game_mode = module_info.get("game_mode", "rpg")

    story_data = load_story(lang=language)

    save_root = os.path.join(PROJECT_DIR, "data", "saves", module_id)
    save_path = os.path.join(save_root, "save_game.json")
    memory_file = os.path.join(save_root, "session_memory.json")

    print_banner(game_mode=game_mode, module_name=module_name)

    if game_mode == "lateral_thinking":
        run_lateral_thinking(
            config=config,
            manifest=manifest,
            story_data=story_data,
            module_dir=module_dir,
            save_path=save_path,
            memory_file=memory_file,
            language=language,
        )
        return

    all_npcs = load_npcs(lang=language)
    register_known_npcs(set(all_npcs.keys()))
    _run_rpg(
        config=config,
        manifest=manifest,
        story_data=story_data,
        all_npcs=all_npcs,
        save_path=save_path,
        memory_file=memory_file,
        language=language,
    )


if __name__ == "__main__":
    main()
