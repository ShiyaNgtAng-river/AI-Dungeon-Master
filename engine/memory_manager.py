"""
记忆管理器 — 管理即时记忆、摘要记忆和原始归档。
"""

import json
import os
from typing import Any

from llm.client import call_llm


class MemoryManager:
    """管理对话记忆，支持定期压缩摘要。"""

    def __init__(
        self,
        memory_file: str,
        max_short_term_turns: int = 10,
        summary_trigger_turns: int = 8,
        summary_min_turns: int = 4,
    ) -> None:
        self.memory_file = memory_file
        self.max_short_term_turns = max(1, max_short_term_turns)
        self.summary_trigger_turns = max(1, summary_trigger_turns)
        self.summary_min_turns = max(2, summary_min_turns)
        self.data = self._empty_data()

    @staticmethod
    def _empty_data() -> dict[str, Any]:
        return {
            "short_term": [],
            "summary": [],
            "archive": [],
            "last_summarized_index": 0,
        }

    def load(self) -> None:
        """从磁盘加载记忆文件；不存在则保持空数据。"""
        if not os.path.exists(self.memory_file):
            self.data = self._empty_data()
            return

        with open(self.memory_file, "r", encoding="utf-8") as f:
            loaded = json.load(f)

        self.data = self._empty_data()
        self.data["short_term"] = loaded.get("short_term", [])
        self.data["summary"] = loaded.get("summary", [])
        self.data["archive"] = loaded.get("archive", [])
        self.data["last_summarized_index"] = int(loaded.get("last_summarized_index", 0))
        self._trim_short_term()

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.memory_file), exist_ok=True)
        with open(self.memory_file, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)

    def reset(self) -> None:
        self.data = self._empty_data()
        self.save()

    def get_short_term(self) -> list[dict]:
        return list(self.data["short_term"])

    def get_summary_text(self, max_items: int = 3) -> str:
        summaries = self.data["summary"][-max(1, max_items):]
        if not summaries:
            return ""
        return "\n".join(f"- {item['text']}" for item in summaries if item.get("text"))

    def add_turn(self, player_input: str, dm_response: str) -> None:
        entry = {
            "player_input": player_input.strip(),
            "dm_response": dm_response.strip(),
        }
        self.data["short_term"].append(entry)
        self.data["archive"].append(entry)
        self._trim_short_term()

    def should_compress(self, turn_count: int) -> bool:
        if turn_count <= 0 or turn_count % self.summary_trigger_turns != 0:
            return False
        pending = len(self.data["archive"]) - int(self.data["last_summarized_index"])
        return pending >= self.summary_min_turns

    def compress_recent(self, use_llm: bool = True, lang: str = "zh-Hans") -> str | None:
        start = int(self.data["last_summarized_index"])
        segment = self.data["archive"][start:]
        if len(segment) < self.summary_min_turns:
            return None

        summary_text = ""
        if use_llm:
            summary_text = self._summarize_with_llm(segment, lang=lang)
        if not summary_text:
            summary_text = self._fallback_summary(segment)

        self.data["summary"].append(
            {
                "text": summary_text,
                "source_turns": len(segment),
            }
        )
        self.data["last_summarized_index"] = len(self.data["archive"])
        self._trim_short_term()
        return summary_text

    def _trim_short_term(self) -> None:
        turns = self.data["short_term"]
        if len(turns) > self.max_short_term_turns:
            self.data["short_term"] = turns[-self.max_short_term_turns:]

    def _summarize_with_llm(self, segment: list[dict], lang: str) -> str:
        prompt_map = {
            "zh-Hans": (
                "你是 RPG 记忆压缩器。请把对话压缩为 3-5 句中文摘要，"
                "仅保留剧情推进、关键人物关系和关键道具变化。"
            ),
            "zh-Hant": (
                "你是 RPG 記憶壓縮器。請把對話壓縮為 3-5 句繁體中文摘要，"
                "僅保留劇情推進、關鍵人物關係和關鍵道具變化。"
            ),
            "en-US": (
                "You are an RPG memory compressor. Summarize the conversation into 3-5 "
                "English sentences, keeping only story progression, key character "
                "relationships, and key item changes."
            ),
        }
        messages = [
            {
                "role": "system",
                "content": prompt_map.get(lang, prompt_map["zh-Hans"]),
            },
            {
                "role": "user",
                "content": json.dumps(segment, ensure_ascii=False),
            },
        ]
        try:
            return call_llm(messages=messages, temperature=0.2, max_tokens=300).strip()
        except Exception:
            return ""

    @staticmethod
    def _fallback_summary(segment: list[dict]) -> str:
        recent = segment[-3:]
        parts = []
        for entry in recent:
            action = entry.get("player_input", "")[:30]
            outcome = entry.get("dm_response", "")[:40]
            parts.append(f"玩家尝试「{action}」，结果是「{outcome}」")
        return "；".join(parts)
