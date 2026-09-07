"""
应用级国际化工具。
负责语言码规范化、语言包加载、键值查询与回退。
"""

from __future__ import annotations

import os
import yaml


SUPPORTED_LANGS = ("zh-Hans", "zh-Hant", "en-US")
DEFAULT_FALLBACK = "zh-Hans"

_current_language = DEFAULT_FALLBACK
_current_fallback = DEFAULT_FALLBACK
_locale_cache: dict[str, dict] = {}
_fallback_cache: dict[str, dict] = {}


def normalize_lang(lang: str | None) -> str:
    """将输入语言码归一化到受支持集合。"""
    if not lang:
        return DEFAULT_FALLBACK

    text = lang.strip()
    lowered = text.lower()

    alias = {
        "zh": "zh-Hans",
        "zh-cn": "zh-Hans",
        "zh_hans": "zh-Hans",
        "zh-hans": "zh-Hans",
        "cn": "zh-Hans",
        "zh-tw": "zh-Hant",
        "zh_hant": "zh-Hant",
        "zh-hant": "zh-Hant",
        "tw": "zh-Hant",
        "en": "en-US",
        "en-us": "en-US",
        "en_us": "en-US",
        "us": "en-US",
    }
    resolved = alias.get(lowered, text)
    if resolved in SUPPORTED_LANGS:
        return resolved
    return DEFAULT_FALLBACK


def language_display_name(lang: str) -> str:
    """返回语言的人类可读名称。"""
    value = normalize_lang(lang)
    mapping = {
        "zh-Hans": "简体中文",
        "zh-Hant": "繁體中文",
        "en-US": "English (US)",
    }
    return mapping[value]


def set_current_language(lang: str) -> None:
    """设置当前语言（进程级）。"""
    global _current_language
    _current_language = normalize_lang(lang)


def get_current_language() -> str:
    """获取当前语言（进程级）。"""
    return _current_language


def _read_yaml(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_app_locale(project_dir: str, lang: str, fallback: str = DEFAULT_FALLBACK) -> dict:
    """
    加载应用语言包。
    返回当前语言包，并在内部缓存 fallback 语言包用于查询回退。
    """
    global _current_fallback
    normalized = normalize_lang(lang)
    fallback_lang = normalize_lang(fallback)
    _current_fallback = fallback_lang

    if normalized not in _locale_cache:
        path = os.path.join(project_dir, "locales", f"app.{normalized}.yaml")
        _locale_cache[normalized] = _read_yaml(path)

    if fallback_lang not in _fallback_cache:
        path = os.path.join(project_dir, "locales", f"app.{fallback_lang}.yaml")
        _fallback_cache[fallback_lang] = _read_yaml(path)

    return _locale_cache[normalized]


def _deep_get(obj: dict, key: str):
    cur = obj
    for part in key.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def t(key: str, lang: str | None = None, **kwargs) -> str:
    """
    获取多语言文案。
    查询顺序：当前语言 -> fallback 语言 -> key 本身。
    """
    normalized = normalize_lang(lang or _current_language)
    primary = _locale_cache.get(normalized, {})
    value = _deep_get(primary, key)
    if value is None:
        fallback = _fallback_cache.get(_current_fallback, {})
        value = _deep_get(fallback, key)
    if value is None:
        return key
    if not isinstance(value, str):
        return str(value)
    if kwargs:
        try:
            return value.format(**kwargs)
        except Exception:
            return value
    return value
