"""
NanoClaw 入口文件

提供命令行交互界面，组装 Agent 各组件并启动对话循环。

使用方法：
    python main.py

命令：
    /exit   - 退出程序
    /clear  - 清空对话历史
    /tools  - 查看可用工具列表
"""

import asyncio
import os

from config import load_config
from providers.openai_compat import OpenAICompatProvider
from agent.tools.registry import ToolRegistry
from agent.tools.filesystem import ReadFileTool, WriteFileTool, ListDirTool
from agent.tools.shell import ExecTool
from agent.tools.product_search import ProductSearchTool
from agent.tools.web_fetch import WebFetchTool
from agent.tools.web_search import WebSearchTool
from agent.tools.spawn import SpawnSubagentTool
from agent.tools.mcp_server import MCPClientManager
from agent.skills import SkillsLoader
from agent.context import ContextBuilder
from agent.loop import AgentLoop
from agent.offline_agent import OfflineAgent
from session.manager import SessionManager
from agent.memory import MemoryConsolidator
from bus.queue import MessageBus
from channels.cli import CLIChannel
from channels.feishu import FeishuChannel
from channels.qq import QQChannel
from channels.web import WebChannel
from gateway import Gateway

# 电商业务工具
from agent.tools.ecom.store import load_users, resolve_channel_actor
from agent.tools.ecom.search_goods import SearchGoodsTool
from agent.tools.ecom.get_goods_detail import GetGoodsDetailTool
from agent.tools.ecom.get_my_profile import GetMyProfileTool
from agent.tools.ecom.remember_preference import RememberPreferenceTool
from agent.tools.ecom.query_order import QueryOrderTool
from agent.tools.ecom.place_order import PlaceOrderTool
from agent.tools.ecom.update_address import UpdateAddressTool
from agent.tools.ecom.query_logistics import QueryLogisticsTool
from agent.tools.ecom.check_return_eligibility import CheckReturnEligibilityTool
from agent.tools.ecom.approve_refund import ApproveRefundTool
from agent.tools.ecom.create_ticket import CreateTicketTool
from agent.tools.ecom.query_ticket_progress import QueryTicketProgressTool
from agent.tools.ecom.escalate_to_human import EscalateToHumanTool


def resolve_actor(session_key: str):
    """从 session_key 解析登录身份，返回 (actor, logged_in)。

    - web:{username}  → 登录用户（需存在于 data/users.json）
    - web:guest-xxx   → 游客（未登录）
    - cli:*           → 预绑定默认演示账号（登录态，便于 CLI 直接演示）
    - qq:* / feishu:* → 渠道免登录：绑定表（data/channel_bindings.json）优先，
                        未绑定回退默认演示账号（IM 渠道无法弹网页登录框）
    """
    channel, _, sender = session_key.partition(":")
    users = load_users()
    if channel == "cli" or sender == "local":
        default = users.get("__default__")
        if default and default in users:
            return default, True
        return None, False
    if channel in ("qq", "feishu"):
        return resolve_channel_actor(channel, sender)
    if sender and not sender.startswith("guest-") and sender in users:
        return sender, True
    return None, False


def build_agent(config, session_key: str, mcp_manager: MCPClientManager = None):
    """
    组装 Agent 实例：无 LLM API key 时返回 OfflineAgent（关键词规则路由），
    否则返回 AgentLoop（LLM ↔ 工具循环）。两者接口兼容，Gateway 无感知。

    Args:
        config: 配置对象（由 load_config() 返回）
        session_key: 会话标识，格式为 "{channel}:{sender_id}"
        mcp_manager: MCP 客户端管理器（可选）

    组装步骤：
    1. 加载配置，解析登录身份（actor / logged_in）
    2. 注册电商业务工具（个人数据工具注入账号身份）
    3. 未配置 api_key → 返回 OfflineAgent（离线演示模式）
    4. 在线模式：创建 LLM Provider、注册通用工具与 MCP 工具、
       加载技能、创建上下文构建器、组装 AgentLoop
    """
    # 加载配置
    config = load_config()

    # 解析登录身份（CLI 预绑定演示账号；Web 登录后为用户名；guest 为游客）
    actor, logged_in = resolve_actor(session_key)
    offline = not config.api_key

    # 创建工具注册表
    tools = ToolRegistry()

    # ---- 电商业务工具（在线/离线共用，身份注入）----
    tools.register(SearchGoodsTool())
    tools.register(GetGoodsDetailTool())
    tools.register(GetMyProfileTool(actor=actor, logged_in=logged_in))
    tools.register(RememberPreferenceTool(actor=actor, logged_in=logged_in))
    tools.register(QueryOrderTool(actor=actor, logged_in=logged_in))
    tools.register(PlaceOrderTool(actor=actor, logged_in=logged_in))
    tools.register(UpdateAddressTool(actor=actor, logged_in=logged_in))
    tools.register(QueryLogisticsTool(actor=actor, logged_in=logged_in))
    tools.register(CheckReturnEligibilityTool(actor=actor, logged_in=logged_in))
    tools.register(ApproveRefundTool(actor=actor, logged_in=logged_in))
    tools.register(CreateTicketTool(actor=actor, logged_in=logged_in))
    tools.register(QueryTicketProgressTool(actor=actor, logged_in=logged_in))
    tools.register(EscalateToHumanTool(actor=actor, logged_in=logged_in))

    # 创建会话管理器
    session_manager = SessionManager(
        sessions_dir=os.path.join(config.workspace, "workspace", "sessions")
    )

    # 如果有历史，显示恢复提示
    existing_history = session_manager.get_history(session_key)
    if existing_history:
        print(f"[{session_key}] 已恢复 {len(existing_history)} 条历史消息")

    # ---- 离线模式：无 API key 时使用关键词规则路由 ----
    identity = f"用户 {actor}" if logged_in else "游客"
    if offline:
        print(f"[{session_key}] 身份: {identity} | 未配置 LLM API key，进入离线演示模式")
        print(f"[{session_key}] 已注册工具：{tools.list_tools()}")
        return OfflineAgent(
            tools=tools,
            session_manager=session_manager,
            session_key=session_key,
        )

    # ---- 在线模式 ----

    # 创建 LLM Provider
    provider = OpenAICompatProvider(
        api_key=config.api_key,
        base_url=config.base_url,
        model=config.model,
    )

    # 注册文件系统等通用工具
    tools.register(ReadFileTool(config.workspace))
    tools.register(WriteFileTool(config.workspace))
    tools.register(ListDirTool(config.workspace))
    # ExecTool 安全门控（生产落地 P0-1.2 方案A）：
    # 默认保持启用（与原有行为一致）；生产环境设 ENABLE_SHELL_TOOL=false 可禁用
    if os.getenv("ENABLE_SHELL_TOOL", "true").lower() == "true":
        tools.register(ExecTool(config.workspace))
    else:
        print("🔒 Shell 工具已禁用（ENABLE_SHELL_TOOL=false，生产安全策略）")
    tools.register(WebSearchTool())
    tools.register(WebFetchTool())
    tools.register(ProductSearchTool())  # 电商商品页搜索（cn.bing.com 直连）

    # 注册子 Agent 工具
    def create_provider(model=None):
        """Provider 工厂函数，子 Agent 用。"""
        # 子 Agent 默认用 subagent 模型（更便宜）
        if model is None and config.models:
            model = config.models.get("subagent", config.model)
        return OpenAICompatProvider(
            api_key=config.api_key,
            base_url=config.base_url,
            model=model or config.model
        )

    tools.register(SpawnSubagentTool(
        provider_factory=create_provider,
        tools_registry=tools,
        workspace=config.workspace,
    ))

    # 注册 MCP 工具（如果有）
    if mcp_manager:
        mcp_tools = mcp_manager.get_tools()
        for mcp_tool in mcp_tools:
            tools.register(mcp_tool)
        print(f"[{session_key}] 已注册 MCP 工具: {len(mcp_tools)} 个")

    # 加载技能摘要
    skills_loader = SkillsLoader(
        skills_dir=os.path.join(config.workspace, "skills")
    )
    skills_summary = skills_loader.build_skills_summary()

    # 如果发现技能，打印数量
    if skills_summary:
        skills_list = skills_loader.list_skills()
        print(f"{session_key} 已发现技能: {len(skills_list)} 个")

    # 打印身份与已注册的工具
    print(f"[{session_key}] 身份: {identity}")
    print(f"[{session_key}] 已注册工具：{tools.list_tools()}")

    # 创建上下文构建器
    context = ContextBuilder(
        workspace=config.workspace,
        identity_file=config.identity_file,
        skills_summary=skills_summary,
    )

    # 创建 Token 压缩器
    consolidator = MemoryConsolidator(
        provider=provider,
        workspace=config.workspace,
        token_budget=16000
    )

    # 组装 AgentLoop
    agent = AgentLoop(
        provider=provider,
        tools=tools,
        context=context,
        session_manager=session_manager,
        model=config.model,
        max_iterations=config.max_iterations,
        session_key=session_key,
    )

    agent.consolidator = consolidator  # 挂载压缩器

    return agent


async def interactive_loop(agent: AgentLoop) -> None:
    """
    交互式对话循环

    读取用户输入，调用 Agent 处理并打印响应。
    支持命令：/exit、/clear、/tools

    Args:
        agent: AgentLoop 实例
    """
    print("\n开始对话（输入 /exit 退出）")

    while True:
        try:
            # 读取用户输入
            user_input = input("\n你: ").strip()

            # 空输入跳过
            if not user_input:
                continue

            # 处理命令
            if user_input == "/exit":
                print("再见！")
                break

            if user_input == "/clear":
                agent.clear_history()
                print("对话历史已清空")
                continue

            if user_input == "/tools":
                tools = agent.tools.list_tools()
                print(f"可用工具: {', '.join(tools)}")
                continue

            # 调用 Agent 处理
            print("\nNanoClaw: ", end="", flush=True)
            response = await agent.run(user_input)
            print(response)

        except KeyboardInterrupt:
            # Ctrl+C 优雅退出
            print("\n\n再见！")
            break

        except EOFError:
            # 输入结束（如管道输入完毕）
            print("\n再见！")
            break


# 新版：通过 Gateway 启动
async def async_main() -> None:
    """
    异步主入口

    启动 MCP Server、组装 Agent、启动 Gateway。
    """
    # 启动 banner
    config = load_config()
    print("=" * 50)
    print("  NanoClaw - 电商智能客服（小纳）")
    if config.api_key:
        print(f"  模型: {config.model}")
    else:
        print("  未配置 LLM API key —— 离线演示模式（关键词规则路由）")
        print("  演示账号: han / 123456（Web 登录用；CLI 已预绑定）")
    print("=" * 50)

    # 数据后端预检（生产落地 P0-1.1）：
    # DATA_BACKEND=mysql 时先验证数据库连通性，连不上立即退出，
    # 避免服务带着不可用的数据层启动、产生半写状态
    if os.getenv("DATA_BACKEND", "json").lower() == "mysql":
        from db import repository
        from db.sync_bridge import run_async
        if not run_async(repository.check_connection(), timeout=5):
            print("❌ 数据库连接失败（DATA_BACKEND=mysql，但 MySQL 不可达）")
            print("   请先启动 MySQL（如 docker-compose up -d mysql）并核对 DB_URL 配置")
            raise SystemExit(1)
        print("[启动] ✅ MySQL 数据后端连接正常（DATA_BACKEND=mysql）")

    bus = MessageBus()

    # 启动 MCP Server（如果有配置；离线演示模式跳过，加快启动）
    mcp_manager = None
    if config.mcp_servers and config.api_key:
        mcp_manager = MCPClientManager(config.mcp_servers)
        await mcp_manager.connect_all(timeout=30)

    # 定义 Agent 工厂函数
    def create_agent(session_key: str) -> AgentLoop:
        return build_agent(config, session_key, mcp_manager)

    # 注册渠道
    cli_channel = CLIChannel(bus)
    channels = [cli_channel]

    # 飞书渠道（如果配置了 feishu app_id / app_secret 就自动启用）
    if config.feishu_app_id and config.feishu_app_secret:
        feishu_channel = FeishuChannel(bus, config.feishu_app_id, config.feishu_app_secret)
        channels.append(feishu_channel)
        print("[启动] 已启用飞书渠道")
    else:
        print("[启动] 未配置飞书，跳过飞书渠道")

    # QQ 渠道（如果配置了 qq app_id / app_secret 就自动启用）
    if config.qq_app_id and config.qq_app_secret:
        qq_channel = QQChannel(bus, config.qq_app_id, config.qq_app_secret)
        channels.append(qq_channel)
        print("[启动] 已启用 QQ 渠道")
    else:
        print("[启动] 未配置 QQ，跳过 QQ 渠道")

    # Web 渠道（默认启用，不需要外部凭证）
    if config.web_enabled:
        web_channel = WebChannel(
            bus,
            host=config.web_host,
            port=config.web_port,
            sessions_dir=os.path.join(config.workspace, "workspace", "sessions"),
        )
        channels.append(web_channel)
        # 0.0.0.0 仅表示监听所有网卡，浏览器实际访问用 localhost
        display_host = "localhost" if config.web_host in ("0.0.0.0", "::", "") else config.web_host
        base_url = f"http://{display_host}:{config.web_port}"
        print(f"[启动] 已启用 Web 渠道: {base_url}")
        print(f"[启动] 用户端: {base_url}/")
        print(f"[启动] 商城页: {base_url}/mall")
        print(f"[启动] 客服端: {base_url}/agent")
        print(f"[启动] 管理端: {base_url}/admin")
    else:
        print("[启动] Web 渠道已禁用")

    # 启动网关
    gateway = Gateway(
        bus, channels, create_agent,
        human_transfer_timeout=config.human_transfer_timeout,
    )

    # Web 渠道接入人工接管管理器（人工客服回复时刷新活跃时间）
    if config.web_enabled:
        web_channel.set_takeover(gateway.takeover)

    # 预创建 CLI Agent，让初始化信息在启动时就显示
    cli_session_key = "cli:local"
    agent = create_agent(cli_session_key)
    gateway._agents[cli_session_key] = agent

    # 注入工具列表和清空回调给 CLI 渠道
    cli_channel.tool_names = agent.tools.list_tools()
    cli_channel._clear_callback = lambda: agent.clear_history()

    try:
        await gateway.run()
    except KeyboardInterrupt:
        print("\n[NanoClaw] 正在退出...")
    finally:
        # 清理 MCP 连接
        if mcp_manager:
            await mcp_manager.shutdown()


def main() -> None:
    """
    主入口（同步包装）

    打印启动 banner，组装 Agent 并启动交互循环。
    """
    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        print("\n[NanoClaw] 正在退出...")
        import os
        os._exit(0)  # 强制退出，杀掉残留的 WebSocket 子线程


if __name__ == "__main__":
    main()