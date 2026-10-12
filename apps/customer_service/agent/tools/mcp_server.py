"""
MCP (Model Context Protocol) 工具模块

提供 MCPClientManager 和 MCPTool，集成外部 MCP Server 到 NanoClaw。

关键约束：
- 所有 print 不使用 emoji（Windows GBK 会崩），用 [MCP]、[!] 代替
- ClientSession 必须手动 __aenter__()，否则内部消息循环不启动，initialize() 会永远卡死
"""

import asyncio
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from agent.tools.base import Tool


class MCPTool(Tool):
    """
    MCP 工具包装器

    将 MCP Server 提供的工具包装为 NanoClaw 的 Tool 接口。
    工具名格式: {server_name}__{tool_name}，避免跨 server 冲突。
    """

    def __init__(
        self,
        server_name: str,
        tool_name: str,
        tool_description: str,
        input_schema: dict,
        client_manager: "MCPClientManager",
    ):
        """
        Args:
            server_name: MCP Server 名称（配置中的 key）
            tool_name: MCP Server 提供的工具名
            tool_description: 工具描述（从 MCP list_tools 获取）
            input_schema: 工具参数 schema（从 MCP list_tools 获取）
            client_manager: MCP 客户端管理器，用于执行工具调用
        """
        self.server_name = server_name
        self.tool_name = tool_name
        self._description = tool_description
        self._input_schema = input_schema
        self.client_manager = client_manager

    @property
    def name(self) -> str:
        """工具名称，格式: {server}__{tool}"""
        return f"{self.server_name}__{self.tool_name}"

    @property
    def description(self) -> str:
        """工具描述"""
        return self._description

    @property
    def parameters(self) -> dict:
        """工具参数 schema，直接使用 inputSchema"""
        return self._input_schema

    async def execute(self, **kwargs: Any) -> str:
        """
        执行 MCP 工具

        通过 client_manager 调用远程 MCP Server 的工具。

        Args:
            **kwargs: 工具参数

        Returns:
            str: 工具执行结果
        """
        return await self.client_manager.call_tool(
            self.server_name, self.tool_name, kwargs
        )


class MCPClientManager:
    """
    MCP 客户端管理器

    管理 MCP Server 的连接生命周期和工具调用。
    支持 stdio 传输协议，通过 StdioServerParameters 启动子进程。

    使用流程：
    1. 传入 mcp_config（格式同 Claude Desktop）
    2. 调用 connect_all() 启动所有 server
    3. 调用 get_tools() 获取所有 MCPTool
    4. 调用 call_tool() 执行工具
    5. 调用 shutdown() 清理连接
    """

    def __init__(self, mcp_config: dict):
        """
        Args:
            mcp_config: MCP Server 配置字典，格式：
                {
                    "server_name": {
                        "command": "python",
                        "args": ["path/to/server.py"]
                    }
                }
        """
        self.mcp_config = mcp_config
        self._context_managers = {}  # stdio_client 上下文管理器
        self._session_managers = {}  # ClientSession 上下文管理器
        self._sessions = {}  # 已连接的 session
        self._tools = []  # MCPTool 列表

    async def _connect_one(self, server_name: str, server_config: dict) -> None:
        """
        连接单个 MCP Server

        流程：
        1. 创建 StdioServerParameters
        2. 调用 stdio_client() 获取上下文管理器，手动 __aenter__ 获取读写流
        3. 创建 ClientSession，手动 __aenter__ 启动内部消息循环
        4. 调用 session.initialize() 初始化连接
        5. 调用 session.list_tools() 获取工具列表
        6. 为每个工具创建 MCPTool

        Args:
            server_name: Server 名称
            server_config: Server 配置（command、args）

        Raises:
            Exception: 连接失败、初始化失败、工具加载失败
        """
        print(f"[MCP] 正在连接 {server_name}...")

        # 1. 创建 StdioServerParameters
        server_params = StdioServerParameters(
            command=server_config["command"],
            args=server_config.get("args", []),
            env=server_config.get("env", None),
        )

        # 2. stdio_client 上下文管理器，手动 __aenter__
        ctx = stdio_client(server_params)
        read_stream, write_stream = await ctx.__aenter__()
        self._context_managers[server_name] = ctx

        # 3. ClientSession 上下文管理器，手动 __aenter__
        session = ClientSession(read_stream, write_stream)
        await session.__aenter__()
        self._session_managers[server_name] = session

        # 4. 初始化连接（必须！）
        await session.initialize()

        # 5. 获取工具列表
        tools_response = await session.list_tools()

        # 6. 创建 MCPTool
        for tool_info in tools_response.tools:
            mcp_tool = MCPTool(
                server_name=server_name,
                tool_name=tool_info.name,
                tool_description=tool_info.description or "",
                input_schema=tool_info.inputSchema or {},
                client_manager=self,
            )
            self._tools.append(mcp_tool)
            print(
                f"[MCP]   - 加载工具: {mcp_tool.name} ({tool_info.description[:50]}...)"
            )

        # 保存 session
        self._sessions[server_name] = session
        print(f"[MCP] {server_name} 连接成功，加载 {len(tools_response.tools)} 个工具")

    async def connect_all(self, timeout: float = 30.0) -> None:
        """
        连接所有 MCP Server

        遍历配置，每个 server 用 asyncio.wait_for(timeout) 包裹。
        超时或异常则跳过，打印警告。

        Args:
            timeout: 单个 server 连接超时秒数
        """
        if not self.mcp_config:
            print("[MCP] 未配置 MCP Server，跳过")
            return

        print(f"[MCP] 开始连接 {len(self.mcp_config)} 个 MCP Server...")

        for server_name, server_config in self.mcp_config.items():
            try:
                await asyncio.wait_for(
                    self._connect_one(server_name, server_config), timeout=timeout
                )
            except asyncio.TimeoutError:
                print(f"[!] MCP Server {server_name} 连接超时（{timeout}s），跳过")
            except Exception as e:
                print(f"[!] MCP Server {server_name} 连接失败: {e}，跳过")

    def get_tools(self) -> list[Tool]:
        """
        获取所有 MCPTool

        Returns:
            list[Tool]: MCPTool 列表
        """
        return self._tools

    async def call_tool(
        self, server_name: str, tool_name: str, arguments: dict
    ) -> str:
        """
        调用 MCP Server 的工具

        Args:
            server_name: Server 名称
            tool_name: 工具名称
            arguments: 工具参数

        Returns:
            str: 工具执行结果（文本格式）
        """
        session = self._sessions.get(server_name)
        if session is None:
            return f"错误: MCP Server '{server_name}' 未连接"

        try:
            # 调用 session.call_tool
            result = await session.call_tool(tool_name, arguments)

            # 提取文本内容
            if result.content and len(result.content) > 0:
                # content 是 list[Content]，提取 text 类型的内容
                text_parts = []
                for content_item in result.content:
                    if hasattr(content_item, "text"):
                        text_parts.append(content_item.text)
                    elif hasattr(content_item, "data"):
                        # 处理其他类型（如 image）
                        text_parts.append(str(content_item.data))
                return "\n".join(text_parts) if text_parts else "（无文本内容）"
            else:
                return "（无返回内容）"

        except Exception as e:
            return f"错误: MCP 工具调用失败 - {e}"

    async def shutdown(self) -> None:
        """
        清理所有连接

        依次退出 _session_managers → _context_managers 的 __aexit__。
        """
        print("[MCP] 正在关闭 MCP Server 连接...")

        # 先退出 session
        for server_name, session in self._session_managers.items():
            try:
                await session.__aexit__(None, None, None)
            except Exception as e:
                print(f"[!] 关闭 session {server_name} 失败: {e}")

        # 再退出 stdio_client
        for server_name, ctx in self._context_managers.items():
            try:
                await ctx.__aexit__(None, None, None)
            except Exception as e:
                print(f"[!] 关闭 context {server_name} 失败: {e}")

        print("[MCP] MCP Server 连接已全部关闭")