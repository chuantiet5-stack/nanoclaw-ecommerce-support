"""电商业务工具包。

所有工具继承 agent/tools/base.Tool，在 main.build_agent() 中注册进
ToolRegistry：既可被 LLM function calling 自动调用，也可被离线
OfflineAgent / demo 脚本直接调用。
"""
