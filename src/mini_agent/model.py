"""模型适配层 — 负责查询语言模型."""

import os
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()  # 从项目根目录 .env 加载环境变量


class Model:
    """DeepSeek 语言模型适配器。"""

    def __init__(self) -> None:
        """延迟创建客户端，绕过 httpx 自动读取 ALL_PROXY 的问题。

        httpx 自动扫所有 *_PROXY 环境变量，但 Clash 设的
        ALL_PROXY=socks://... 是不合法的 scheme，直接 ValueError。
        解决：显式传 http_client，只用 HTTP_PROXY，不碰 ALL_PROXY。
        """
        import httpx

        http_proxy = os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy")
        http_client = httpx.Client(proxy=http_proxy) if http_proxy else None

        self._client = OpenAI(
            api_key=os.environ["DEEPSEEK_API_KEY"],
            base_url="https://api.deepseek.com",
            http_client=http_client,
        )

    def query(self, messages: list[dict], tools: list[dict] | None = None):
        """Send messages to the LM and return the full response object.

        When tools are provided, the model may return tool calls instead of
        a text reply.  The caller is responsible for executing the tools and
        sending back tool-result messages.
        """
        kwargs: dict = {}
        if tools:
            kwargs["tools"] = tools
        return self._client.chat.completions.create(
            model="deepseek-chat",
            messages=messages,
            **kwargs,
        )


# 向后兼容：延迟创建，避免 import 时就需要 API key
_model: Model | None = None


def query_lm(messages: list[dict[str, str]]) -> str:
    """Convenience wrapper that returns just the text content."""
    global _model
    if _model is None:
        _model = Model()
    response = _model.query(messages)
    return response.choices[0].message.content
