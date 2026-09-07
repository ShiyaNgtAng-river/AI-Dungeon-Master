"""
LLM 调用封装 — 统一接口，方便换模型。
使用 OpenAI SDK 连接 DeepSeek API（兼容接口）。
"""

import os
import json
import time
import yaml
from openai import OpenAI


_client: OpenAI | None = None


def _get_client() -> OpenAI:
    """懒加载 OpenAI 客户端，优先从 .env 读取 key。"""
    global _client
    if _client is not None:
        return _client

    api_key = os.getenv("DEEPSEEK_API_KEY", "")
    base_url = "https://api.deepseek.com"

    if not api_key:
        config_path = os.path.join(os.path.dirname(__file__), "..", "config.yaml")
        if os.path.exists(config_path):
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f)
            api_key = cfg.get("llm", {}).get("api_key", "")
            base_url = cfg.get("llm", {}).get("base_url", base_url)

    if not api_key:
        raise RuntimeError(
            "未找到 API Key。请在 .env 中设置 DEEPSEEK_API_KEY，"
            "或在 config.yaml 的 llm.api_key 字段中填写。"
        )

    _client = OpenAI(api_key=api_key, base_url=base_url)
    return _client


def call_llm(
    messages: list[dict],
    temperature: float = 0.8,
    max_tokens: int = 1024,
    max_retries: int = 3,
    response_format: dict | None = None,
) -> str:
    """
    调用 LLM 并返回文本结果。

    Parameters
    ----------
    messages : 标准 OpenAI 格式的消息列表
    temperature : 生成温度
    max_tokens : 最大输出 token 数
    max_retries : 网络/API 错误时的重试次数
    response_format : 可选，如 {"type": "json_object"} 强制 JSON 输出

    Returns
    -------
    str : 模型返回的文本内容
    """
    client = _get_client()

    kwargs = dict(
        model="deepseek-chat",
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    if response_format is not None:
        kwargs["response_format"] = response_format

    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            resp = client.chat.completions.create(**kwargs)
            content = resp.choices[0].message.content
            if content is None:
                raise ValueError("LLM 返回了空内容")
            return content.strip()
        except Exception as e:
            last_error = e
            if attempt < max_retries:
                wait = 2 ** attempt
                print(f"[LLM] 第 {attempt} 次调用失败: {e}，{wait}s 后重试...")
                time.sleep(wait)

    raise RuntimeError(f"LLM 调用在 {max_retries} 次重试后仍然失败: {last_error}")
