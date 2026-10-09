"""
互联网搜索工具模块

使用 DuckDuckGo 搜索引擎执行互联网搜索。
"""

import asyncio
from typing import Any

from agent.tools.base import Tool


class WebSearchTool(Tool):
    """
    互联网搜索工具。

    使用 DuckDuckGo 搜索引擎查询互联网信息，返回格式化的搜索结果。
    """

    MAX_OUTPUT_LENGTH = 8000

    @property
    def name(self) -> str:
        """工具名称"""
        return "web_search"

    @property
    def description(self) -> str:
        """工具功能描述"""
        return "搜索互联网获取最新信息。当你需要查询实时信息、最新新闻或不确定的知识时使用。"

    @property
    def parameters(self) -> dict:
        """工具参数定义（JSON Schema 格式）"""
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜索关键词"
                },
                "max_results": {
                    "type": "integer",
                    "description": "最多返回几条结果",
                    "default": 5
                }
            },
            "required": ["query"]
        }

    async def execute(self, **kwargs: Any) -> str:
        """
        执行互联网搜索。

        Args:
            query: 搜索关键词
            max_results: 最多返回几条结果，默认为 5

        Returns:
            格式化的搜索结果字符串
        """
        query = kwargs.get("query")
        max_results = kwargs.get("max_results", 5)

        if not query:
            return "错误：搜索关键词不能为空"

        try:
            # 执行搜索（DDGS().text() 是同步方法，用 asyncio.to_thread 包装）
            results = await asyncio.to_thread(
                self._search_sync, query, max_results
            )

            # 处理搜索结果
            if not results:
                return "未找到相关结果"

            # 格式化输出
            output_parts = []
            for index, result in enumerate(results, 1):
                title = result.get("title", "无标题")
                href = result.get("href", "无链接")
                body = result.get("body", "无描述")

                formatted_result = f"### {index}. {title}\n链接: {href}\n{body}\n"
                output_parts.append(formatted_result)

            output = "".join(output_parts)

            # 截断超长输出
            if len(output) > self.MAX_OUTPUT_LENGTH:
                output = output[:self.MAX_OUTPUT_LENGTH]

            return output

        except Exception as e:
            return f"搜索出错: {str(e)}"

    def _search_sync(self, query: str, max_results: int) -> list:
        """
        同步执行搜索（内部方法）。

        Args:
            query: 搜索关键词
            max_results: 最多返回几条结果

        Returns:
            搜索结果列表
        """
        from ddgs import DDGS

        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
        return results