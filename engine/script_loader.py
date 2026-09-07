"""
剧本数据加载 — 读取 YAML 格式的故事骨架、章节、NPC 数据。
负责 NPC 态度规则匹配（层叠顺序 + 动态覆盖）。
"""

import os
import yaml
from types import SimpleNamespace
from engine.i18n import normalize_lang


_LEGACY_SCRIPT_DIR = os.path.join(os.path.dirname(__file__), "..", "scripts")
_active_module_dir = _LEGACY_SCRIPT_DIR
_active_lang = "zh-Hans"


def set_active_module(module_dir: str) -> None:
    """设置当前激活模组目录。"""
    global _active_module_dir
    _active_module_dir = module_dir


def set_active_language(lang: str) -> None:
    """设置当前激活语言。"""
    global _active_lang
    _active_lang = normalize_lang(lang)


def _resolve_lang(lang: str | None) -> str:
    """解析实际使用的语言。"""
    return normalize_lang(lang or _active_lang)


def _deep_merge(base, overlay):
    """
    深度合并对象。
    - dict: 递归合并
    - list: 全量替换
    - 其他: overlay 覆盖
    """
    if not isinstance(base, dict) or not isinstance(overlay, dict):
        return overlay

    merged = dict(base)
    for key, value in overlay.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _load_yaml(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _load_with_locale_overlay(module_dir: str, relative_path: str, lang: str) -> dict:
    """加载基础 YAML 并叠加 locales/<lang>/ 同路径覆盖。"""
    base_path = os.path.join(module_dir, relative_path)
    base_data = _load_yaml(base_path)

    overlay_path = os.path.join(module_dir, "locales", lang, relative_path)
    if os.path.exists(overlay_path):
        overlay_data = _load_yaml(overlay_path)
        return _deep_merge(base_data, overlay_data)
    return base_data


def load_manifest(module_dir: str, lang: str | None = None) -> dict:
    """加载模组 manifest.yaml（支持多语言覆盖）。"""
    resolved_lang = _resolve_lang(lang)
    return _load_with_locale_overlay(module_dir, "manifest.yaml", resolved_lang)


def _resolve_module_dir(module_dir: str | None) -> str:
    """解析实际使用的模组目录。"""
    return module_dir or _active_module_dir


def _resolve_module_files(module_dir: str, lang: str | None = None) -> dict:
    """
    解析模组文件布局。
    若模组目录没有 manifest.yaml，则回退到旧版固定布局。
    """
    defaults = {
        "story": "story.yaml",
        "npcs": "npcs.yaml",
        "chapters_dir": "chapters",
    }

    manifest_path = os.path.join(module_dir, "manifest.yaml")
    if not os.path.exists(manifest_path):
        return defaults

    manifest = load_manifest(module_dir, lang=lang)
    module_info = manifest.get("module", {})
    files = module_info.get("files", {})
    return {
        "story": files.get("story", defaults["story"]),
        "npcs": files.get("npcs", defaults["npcs"]),
        "chapters_dir": files.get("chapters_dir", defaults["chapters_dir"]).rstrip("/"),
    }


def load_story(module_dir: str | None = None, lang: str | None = None) -> dict:
    """加载故事骨架（story.yaml）。"""
    module_dir = _resolve_module_dir(module_dir)
    resolved_lang = _resolve_lang(lang)
    files = _resolve_module_files(module_dir, lang=resolved_lang)
    return _load_with_locale_overlay(module_dir, files["story"], resolved_lang)


def load_chapter(chapter_id: str, module_dir: str | None = None, lang: str | None = None) -> dict:
    """加载指定章节的演绎指南和 checkpoint 定义。"""
    module_dir = _resolve_module_dir(module_dir)
    resolved_lang = _resolve_lang(lang)
    files = _resolve_module_files(module_dir, lang=resolved_lang)
    relative_path = os.path.join(files["chapters_dir"], f"{chapter_id}.yaml")
    return _load_with_locale_overlay(module_dir, relative_path, resolved_lang)


def load_npcs(module_dir: str | None = None, lang: str | None = None) -> dict:
    """加载 NPC 数据库，返回 {npc_name: npc_data} 字典。"""
    module_dir = _resolve_module_dir(module_dir)
    resolved_lang = _resolve_lang(lang)
    files = _resolve_module_files(module_dir, lang=resolved_lang)
    data = _load_with_locale_overlay(module_dir, files["npcs"], resolved_lang)
    return data.get("npcs", {})


def _eval_condition(condition: str, game_state: dict) -> bool:
    """
    安全地求值条件表达式。
    只暴露 game_state 中的只读视图，禁用所有内置函数。
    """
    player = game_state.get("player", {})
    inventory_names = [
        item["name"]
        for item in game_state.get("inventory", {}).get("backpack", [])
    ]
    equipped = game_state.get("inventory", {}).get("equipped", {})

    safe_namespace = {
        "player": _to_namespace(player),
        "inventory": inventory_names,
        "equipped": equipped,
        "world": game_state.get("world", {}),
        "story_flags": game_state.get("progress", {}).get("story_flags", []),
        "relationships": game_state.get("relationships", {}),
        "progress": game_state.get("progress", {}),
    }
    try:
        return bool(eval(condition, {"__builtins__": {}}, safe_namespace))
    except Exception:
        return False


def _to_namespace(value):
    """递归将 dict 转为支持点语法的命名空间。"""
    if isinstance(value, dict):
        return SimpleNamespace(**{k: _to_namespace(v) for k, v in value.items()})
    if isinstance(value, list):
        return [_to_namespace(v) for v in value]
    return value


def resolve_npc_attitude(npc_name: str, npc_data: dict, game_state: dict) -> tuple[str, str]:
    """
    解析 NPC 当前态度。优先级：动态覆盖 > 规则匹配（后者覆盖前者） > base_attitude。

    Returns
    -------
    (attitude, reason) 二元组
    """
    rels = game_state.get("relationships", {})
    dynamic = rels.get(npc_name, {})
    if dynamic.get("attitude_override"):
        return dynamic["attitude_override"], dynamic.get("reason", "动态更新")

    attitude = npc_data.get("base_attitude", "neutral")
    reason = "默认态度"

    for rule in npc_data.get("attitude_rules", []):
        if _eval_condition(rule["condition"], game_state):
            attitude = rule["attitude"]
            reason = rule.get("reason", "")

    return attitude, reason


def get_chapter_npcs(chapter_data: dict, all_npcs: dict, game_state: dict) -> list[dict]:
    """
    获取当前章节中相关 NPC 的信息（含实时态度）。
    只返回存活的 NPC。
    """
    chapter_npc_pool = chapter_data.get("chapter", {}).get("npc_pool", [])
    pool_names = {npc["name"] for npc in chapter_npc_pool}

    result = []
    for name in pool_names:
        npc_data = all_npcs.get(name)
        if npc_data is None:
            continue

        rel = game_state.get("relationships", {}).get(name, {})
        if rel.get("status") == "dead":
            continue

        attitude, reason = resolve_npc_attitude(name, npc_data, game_state)
        result.append({
            "name": name,
            "role": npc_data.get("role", ""),
            "attitude": attitude,
            "reason": reason,
            "dm_note": npc_data.get("dm_note", ""),
        })

    return result


def check_hard_conditions(conditions: list[str], game_state: dict) -> bool:
    """检查 checkpoint 的硬条件是否全部满足。"""
    return all(_eval_condition(cond, game_state) for cond in conditions)
