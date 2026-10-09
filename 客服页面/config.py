"""
NanoClaw 配置模块

提供配置加载和管理功能，支持：
- 从 JSON 文件读取配置
- 环境变量覆盖（NANOCLAW_API_KEY）
- 默认值填充

配置文件使用 config.json，包含 API 密钥、模型、工作区等参数。
"""

import json
import os
from dataclasses import dataclass

# 项目根目录（config.py 所在目录）：所有默认路径锚定于此，
# 与启动时的 cwd 无关——从任意目录执行 `python .../v4/main.py` 均能正确加载
BASE_DIR = os.path.dirname(os.path.abspath(__file__))


@dataclass
class NanoClawConfig:
    """
    NanoClaw 配置结构

    包含 Agent 运行所需的所有配置参数。

    Attributes:
        api_key: API 密钥，优先从环境变量 NANOCLAW_API_KEY 读取
        base_url: API 基础 URL，默认硅基流动地址
        model: 使用的模型名称，默认 Kimi-K2.5
        workspace: 工作区路径，默认当前目录
        max_iterations: Agent 最大迭代次数，防止无限循环
        identity_file: 人设文件名，相对于 workspace
    """

    api_key: str = ""
    base_url: str = "https://api.siliconflow.cn/v1"
    model: str = "Pro/moonshotai/Kimi-K2.5"
    models: dict = None  # {"main": "...", "subagent": "...", "cheap": "..."}
    workspace: str = "."
    max_iterations: int = 32
    identity_file: str = "identity.md"
    # 人工接管超时（秒）：转人工后双方无对话超过该时长自动转回 AI
    human_transfer_timeout: float = 300
    # 飞书配置
    feishu_app_id: str = ""
    feishu_app_secret: str = ""
    # QQ 配置
    qq_app_id: str = ""
    qq_app_secret: str = ""
    # Web 配置
    web_enabled: bool = True
    web_host: str = "0.0.0.0"
    web_port: int = 8080
    # MCP Server 配置
    mcp_servers: dict = None  # {"server_name": {"command": "...", "args": [...]}}


def load_config(config_path: str = None) -> NanoClawConfig:
    """
    加载配置

    从指定路径读取 JSON 配置文件，填充 NanoClawConfig 字段。
    环境变量 NANOCLAW_API_KEY 具有最高优先级，会覆盖配置文件中的值。

    Args:
        config_path: 配置文件路径，默认 <项目根>/config.json（锚定 BASE_DIR，不依赖 cwd）

    Returns:
        NanoClawConfig: 配置对象

    注意：
        - 配置文件不存在时返回默认配置
        - 环境变量 NANOCLAW_API_KEY 优先级最高
    """
    # 默认配置
    config = NanoClawConfig()

    # 配置文件默认路径锚定到项目根目录（从任意 cwd 启动都能读到）
    if config_path is None:
        config_path = os.path.join(BASE_DIR, "config.json")

    # 从 JSON 文件读取（如果存在）
    if os.path.isfile(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            # 更新配置字段（仅更新存在的字段）
            if "api_key" in data:
                config.api_key = data["api_key"]
            if "base_url" in data:
                config.base_url = data["base_url"]
            if "model" in data:
                config.model = data["model"]
            if "workspace" in data:
                config.workspace = data["workspace"]
            if "max_iterations" in data:
                config.max_iterations = data["max_iterations"]
            if "identity_file" in data:
                config.identity_file = data["identity_file"]
            if "human_transfer_timeout" in data:
                config.human_transfer_timeout = float(data["human_transfer_timeout"])
            # 加载多模型配置
            if "models" in data:
                config.models = data["models"]
                # 如果没有单独的 model 字段，用 models.main
                if "model" not in data and "main" in config.models:
                    config.model = config.models["main"]
            # 加载飞书配置
            feishu = data.get("feishu", {})
            if feishu.get("app_id"):
                config.feishu_app_id = feishu["app_id"]
            if feishu.get("app_secret"):
                config.feishu_app_secret = feishu["app_secret"]
            # 加载 QQ 配置
            qq = data.get("qq", {})
            if qq.get("app_id"):
                config.qq_app_id = qq["app_id"]
            if qq.get("app_secret"):
                config.qq_app_secret = qq["app_secret"]
            # 加载 Web 配置
            web = data.get("web", {})
            if "enabled" in web:
                config.web_enabled = web["enabled"]
            if web.get("host"):
                config.web_host = web["host"]
            if web.get("port"):
                config.web_port = web["port"]
            # 加载 MCP Server 配置
            if "mcp_servers" in data:
                config.mcp_servers = data["mcp_servers"]

        except json.JSONDecodeError:
            # JSON 解析错误，使用默认配置
            pass
        except Exception:
            # 其他错误，使用默认配置
            pass

    # 环境变量优先级最高
    env_api_key = os.environ.get("NANOCLAW_API_KEY")
    if env_api_key:
        config.api_key = env_api_key

    # workspace 相对路径锚定到项目根目录（identity.md / 会话历史 / MEMORY.md 均依赖）
    if config.workspace and not os.path.isabs(config.workspace):
        config.workspace = os.path.abspath(os.path.join(BASE_DIR, config.workspace))

    return config