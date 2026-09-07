"""
游戏状态管理 — 加载、保存、创建新游戏。
game_state 是整个系统的结构化数据核心，所有模块通过它共享信息。
"""

import json
import os
import copy
import shutil
from typing import Callable


DEFAULT_SAVE = os.path.join(
    os.path.dirname(__file__), "..", "data", "save_game.json"
)


class GameStateLoadError(Exception):
    """游戏状态加载失败。"""


def load_game_state(path: str = DEFAULT_SAVE) -> dict:
    """从 JSON 文件加载游戏状态。"""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_game_state_safe(
    path: str,
    fallback_path: str | None = None,
    on_error: Callable[[str, Exception], dict | None] | None = None,
) -> dict | None:
    """
    安全加载游戏状态。
    优先加载主存档，失败后尝试加载备份；仍失败则交由回调决定降级策略。
    """
    main_error: Exception | None = None
    backup_error: Exception | None = None

    try:
        return load_game_state(path)
    except Exception as err:  # noqa: BLE001 - 需要兜住 JSON/IO 等加载失败
        main_error = err

    if fallback_path:
        try:
            return load_game_state(fallback_path)
        except Exception as err:  # noqa: BLE001 - 继续进入回调处理
            backup_error = err

    if on_error:
        return on_error(path, main_error or backup_error or Exception("unknown load error"))

    if backup_error is not None:
        raise GameStateLoadError(
            f"load failed: main={main_error!r}; backup={backup_error!r}"
        ) from backup_error
    raise GameStateLoadError(f"load failed: main={main_error!r}") from main_error


def save_game_state(state: dict, path: str = DEFAULT_SAVE) -> None:
    """将游戏状态写回 JSON 文件。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

    backup_path = f"{path}.bak"
    try:
        shutil.copy2(path, backup_path)
    except Exception as err:  # noqa: BLE001 - 备份失败不影响主流程
        print(f"[Warning] save backup failed: {err}")


def new_game(story_data: dict) -> dict:
    """
    从 story.yaml 中定义的默认数据创建新游戏状态。
    game_state 结构保持不变，仅初始值来源改为 YAML。
    """
    story = story_data.get("story", {})
    chapters = story.get("chapters", [])

    default_player = copy.deepcopy(story_data.get("default_player", {}))
    default_inventory = copy.deepcopy(story_data.get("default_inventory", {}))
    default_world = copy.deepcopy(story_data.get("default_world", {}))
    default_relationships = copy.deepcopy(story_data.get("default_relationships", {}))
    default_progress = copy.deepcopy(story_data.get("default_progress", {}))

    # world 默认值兜底
    default_world.setdefault("current_location", "未知地点")
    default_world.setdefault("time_of_day", "白天")
    default_world.setdefault("visited_locations", [])
    default_world.setdefault("discovered_locations", [default_world["current_location"]])
    default_world.setdefault("active_effects", [])
    if not default_world.get("current_chapter"):
        default_world["current_chapter"] = chapters[0] if chapters else "chapter_1"

    # progress 默认值兜底
    default_progress.setdefault("checkpoints", {})
    default_progress.setdefault("active_quests", [])
    default_progress.setdefault("story_flags", [])
    default_progress.setdefault("turn_count", 0)

    return {
        "player": default_player,
        "inventory": default_inventory,
        "world": default_world,
        "relationships": default_relationships,
        "progress": default_progress,
    }


def has_save(path: str = DEFAULT_SAVE) -> bool:
    """检查是否存在已有存档。"""
    return os.path.exists(path)


def format_state_summary(state: dict) -> str:
    """将 game_state 格式化为简短的中文摘要，供 prompt 注入。"""
    p = state.get("player", {})
    inv = state.get("inventory", {})
    w = state.get("world", {})
    prog = state.get("progress", {})

    equipped = inv.get("equipped", {})
    weapon = equipped.get("weapon")
    weapon_name = weapon.get("name", "无") if isinstance(weapon, dict) else "无"
    armor = equipped.get("armor")
    armor_name = armor.get("name", "无") if isinstance(armor, dict) else "无"

    backpack = inv.get("backpack", [])
    backpack_items = ", ".join(
        f'{item.get("name", "未知")}×{item.get("quantity", 1)}' for item in backpack
    )
    if not backpack_items:
        backpack_items = "空"

    story_flags = prog.get("story_flags", [])
    flags = ", ".join(story_flags) if story_flags else "无"

    quests_data = prog.get("active_quests", [])
    quests = "; ".join(
        f'{q.get("name", "未知任务")}({q.get("stage", "未开始")})' for q in quests_data
    ) if quests_data else "无"

    name = p.get("name", "未知")
    race = p.get("race", "未知")
    clazz = p.get("class", "未知")
    level = p.get("level", 1)
    lines = [f"【玩家】{name} | {race} {clazz} Lv.{level}"]

    hp = p.get("hp")
    mp = p.get("mp")
    stats = p.get("stats", {})
    if isinstance(hp, dict) and isinstance(mp, dict):
        lines.append(
            f"  HP: {hp.get('current', 0)}/{hp.get('max', 0)}  "
            f"MP: {mp.get('current', 0)}/{mp.get('max', 0)}"
        )
    if isinstance(stats, dict) and stats:
        lines.append(
            f"  力量:{stats.get('strength', 0)} 敏捷:{stats.get('dexterity', 0)} "
            f"智慧:{stats.get('wisdom', 0)}"
        )

    skills = p.get("skills", [])
    if skills:
        lines.append(f"  技能: {', '.join(skills)}")

    lines.extend(
        [
            f"【装备】武器: {weapon_name} | 防具: {armor_name}",
            f"【背包】{backpack_items} | 金币: {inv.get('gold', 0)}",
            f"【位置】{w.get('current_location', '未知')} | 时间: {w.get('time_of_day', '未知')}",
            f"【任务】{quests}",
            f"【剧情标记】{flags}",
        ]
    )

    rels = state.get("relationships", {})
    if rels:
        rel_lines = []
        for name, info in rels.items():
            status = info.get("status", "未知")
            attitude = info.get("attitude", info.get("attitude_override", "未知"))
            rel_lines.append(f"  {name}: {attitude} ({status})")
        lines.append("【NPC关系】")
        lines.extend(rel_lines)

    return "\n".join(lines)
