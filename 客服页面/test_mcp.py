"""
MCP 集成测试脚本

快速验证 MCP Server 是否能正常启动和加载工具。
"""

import asyncio
from agent.tools.mcp_server import MCPClientManager
from config import load_config


async def test_mcp():
    """测试 MCP 连接和工具加载"""
    config = load_config()

    if not config.mcp_servers:
        print("[!] 未配置 MCP Server")
        return

    print("=" * 50)
    print("MCP 集成测试")
    print("=" * 50)

    # 创建 MCP 管理器
    manager = MCPClientManager(config.mcp_servers)

    # 连接所有 server
    print("\n[测试] 开始连接 MCP Server...")
    await manager.connect_all(timeout=10.0)

    # 获取工具列表
    tools = manager.get_tools()
    print(f"\n[测试] 已加载 {len(tools)} 个 MCP 工具:")
    for tool in tools:
        print(f"  - {tool.name}: {tool.description[:60]}...")

    # 测试工具调用
    if tools:
        print("\n[测试] 测试工具调用...")
        test_tool = tools[0]
        print(f"  调用: {test_tool.name}")

        # 根据工具类型选择测试参数
        if "search" in test_tool.name:
            result = await test_tool.execute(keyword="明月")
        elif "random" in test_tool.name:
            result = await test_tool.execute()
        elif "list" in test_tool.name:
            result = await test_tool.execute()
        else:
            result = await test_tool.execute()

        print(f"  结果: {result[:200]}...")

    # 清理连接
    print("\n[测试] 清理连接...")
    await manager.shutdown()

    print("\n[测试] 测试完成!")


if __name__ == "__main__":
    asyncio.run(test_mcp())