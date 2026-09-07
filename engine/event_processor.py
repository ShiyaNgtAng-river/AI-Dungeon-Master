"""
事件处理器 — 确定性地更新 game_state。
LLM 负责叙事，Python 负责数值。所有游戏逻辑在这里执行，不在 LLM 中。
"""


VALID_EVENT_TYPES = {
    "item_gain",
    "item_lose",
    "stat_change",
    "npc_status",
    "relationship",
    "location_move",
    "flag_add",
    "other",
}

MAX_SINGLE_ITEM_QUANTITY = 10
MAX_GOLD_PER_EVENT = 50
MAX_TOTAL_GOLD = 500


def process_events(events: list[dict], game_state: dict) -> dict:
    """
    处理事件列表，更新 game_state。返回更新后的 state。
    每个事件格式: {"type": str, "target": str, "value": any}
    内置同轮去重：同一 (type, target) 组合每轮只处理一次。
    """
    seen = set()
    for event in events:
        etype = event.get("type", "other")
        if etype not in VALID_EVENT_TYPES:
            etype = "other"

        dedup_key = (etype, event.get("target", ""))
        if etype in ("item_gain", "item_lose", "stat_change", "npc_status", "relationship"):
            if dedup_key in seen:
                print(f"[事件处理] 跳过重复事件: {etype}({event.get('target')})")
                continue
            seen.add(dedup_key)

        handler = _HANDLERS.get(etype, _handle_other)
        try:
            game_state = handler(event, game_state)
        except Exception as e:
            print(f"[事件处理] 处理事件 {event} 时出错: {e}")

    game_state["progress"]["turn_count"] += 1
    return game_state


def _handle_item_gain(event: dict, state: dict) -> dict:
    """玩家获得物品。单次上限 MAX_SINGLE_ITEM_QUANTITY。"""
    item_name = event["target"]
    quantity = event.get("value", 1)
    if not isinstance(quantity, (int, float)):
        quantity = 1
    quantity = min(int(quantity), MAX_SINGLE_ITEM_QUANTITY)
    if quantity <= 0:
        return state

    backpack = state["inventory"]["backpack"]
    for item in backpack:
        if item["name"] == item_name:
            item["quantity"] = min(item["quantity"] + quantity, MAX_SINGLE_ITEM_QUANTITY * 3)
            return state

    backpack.append({
        "name": item_name,
        "quantity": quantity,
        "effect": "",
        "tags": [],
    })
    return state


def _handle_item_lose(event: dict, state: dict) -> dict:
    """玩家失去物品。带关键物品保护。"""
    item_name = event["target"]
    quantity = event.get("value", 1)
    if not isinstance(quantity, (int, float)):
        quantity = 1
    quantity = int(quantity)

    backpack = state["inventory"]["backpack"]
    for i, item in enumerate(backpack):
        if item["name"] == item_name:
            if "不可丢弃" in item.get("tags", []):
                print(f"[事件处理] 关键物品「{item_name}」受保护，无法移除。")
                return state
            item["quantity"] -= quantity
            if item["quantity"] <= 0:
                backpack.pop(i)
            return state

    return state


def _handle_stat_change(event: dict, state: dict) -> dict:
    """属性变化（hp/mp/gold 等），带数值边界保护。"""
    target = event["target"]
    value = event.get("value", 0)
    if not isinstance(value, (int, float)):
        return state
    value = int(value)

    if target in ("hp", "mp"):
        stat = state["player"][target]
        stat["current"] = max(0, min(stat["max"], stat["current"] + value))
    elif target == "gold":
        clamped = max(-MAX_GOLD_PER_EVENT, min(MAX_GOLD_PER_EVENT, value))
        state["inventory"]["gold"] = max(0, min(MAX_TOTAL_GOLD, state["inventory"]["gold"] + clamped))
    elif target in state["player"]["stats"]:
        state["player"]["stats"][target] = max(1, min(30, state["player"]["stats"][target] + value))

    return state


_known_npcs: set[str] = set()


def register_known_npcs(npc_names: set[str]) -> None:
    """注册已知 NPC 名称白名单，在游戏初始化时调用。"""
    _known_npcs.update(npc_names)


def _normalize_npc_name(raw_name: str) -> str | None:
    """尝试将 LLM 输出的临时名映射到已知 NPC。返回 None 表示无法匹配。"""
    if raw_name in _known_npcs:
        return raw_name
    for known in _known_npcs:
        if known in raw_name or raw_name in known:
            return known
    return None


def _handle_npc_status(event: dict, state: dict) -> dict:
    """NPC 状态变化（如死亡、受伤、逃跑）。仅允许已知 NPC。"""
    raw_name = event["target"]
    npc_name = _normalize_npc_name(raw_name)
    if npc_name is None:
        print(f"[事件处理] 忽略未知 NPC 状态事件: {raw_name}")
        return state

    new_status = event.get("value", "unknown")
    rels = state.setdefault("relationships", {})
    if npc_name not in rels:
        rels[npc_name] = {}
    rels[npc_name]["status"] = new_status
    return state


def _handle_relationship(event: dict, state: dict) -> dict:
    """NPC 关系/态度变化。仅允许已知 NPC。"""
    raw_name = event["target"]
    npc_name = _normalize_npc_name(raw_name)
    if npc_name is None:
        print(f"[事件处理] 忽略未知 NPC 关系事件: {raw_name}")
        return state

    new_attitude = event.get("value", "neutral")
    rels = state.setdefault("relationships", {})
    if npc_name not in rels:
        rels[npc_name] = {}
    rels[npc_name]["attitude_override"] = new_attitude
    rels[npc_name]["reason"] = "由游戏事件更新"
    return state


def _handle_location_move(event: dict, state: dict) -> dict:
    """玩家移动到新位置。"""
    new_location = event["target"]
    old_location = state["world"]["current_location"]

    if old_location and old_location not in state["world"]["visited_locations"]:
        state["world"]["visited_locations"].append(old_location)

    state["world"]["current_location"] = new_location

    if new_location not in state["world"]["discovered_locations"]:
        state["world"]["discovered_locations"].append(new_location)

    return state


def _handle_flag_add(event: dict, state: dict) -> dict:
    """添加剧情标记（只增不删）。"""
    flag = event["target"]
    flags = state["progress"]["story_flags"]
    if flag not in flags:
        flags.append(flag)
    return state


def _handle_other(event: dict, state: dict) -> dict:
    """其他事件：仅记录日志，不做状态变更。"""
    print(f"[事件日志] {event.get('target', '未知事件')}")
    return state


_HANDLERS = {
    "item_gain": _handle_item_gain,
    "item_lose": _handle_item_lose,
    "stat_change": _handle_stat_change,
    "npc_status": _handle_npc_status,
    "relationship": _handle_relationship,
    "location_move": _handle_location_move,
    "flag_add": _handle_flag_add,
    "other": _handle_other,
}
