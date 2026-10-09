"""
文件系统工具模块

提供三个安全的文件系统操作工具，均受 workspace 边界保护，
防止路径穿越攻击。所有路径操作均在指定工作区内执行。
"""

import os
from pathlib import Path
from typing import Any

from agent.tools.base import Tool


class ReadFileTool(Tool):
    """
    文件读取工具

    读取指定路径的文件内容，支持安全边界检查防止路径穿越。
    输出超过 16000 字符时自动截断并提示。

    安全机制：解析绝对路径后检查是否以 workspace 开头，阻止任何
    试图访问工作区外文件的操作（如 ../../etc/passwd）。
    """

    def __init__(self, workspace: str) -> None:
        """
        Args:
            workspace: 工作区根目录的绝对路径，所有文件操作限制在此目录内
        """
        self.workspace = os.path.abspath(workspace)

    @property
    def name(self) -> str:
        return "read_file"

    @property
    def description(self) -> str:
        return "读取本地文件内容。参数 file_path 为相对于工作区的文件路径。"

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "要读取的文件路径，相对于工作区根目录",
                }
            },
            "required": ["file_path"],
        }

    async def execute(self, **kwargs: Any) -> str:
        file_path = kwargs.get("file_path", "")
        absolute_path = os.path.abspath(os.path.join(self.workspace, file_path))

        # 安全检查：防止路径穿越
        if not absolute_path.startswith(self.workspace):
            return "错误: 拒绝访问工作区外的路径"

        if not os.path.isfile(absolute_path):
            return f"错误: 文件不存在: {file_path}"

        try:
            with open(absolute_path, "r", encoding="utf-8") as f:
                content = f.read()

            # 超过 16000 字符截断
            if len(content) > 16000:
                return content[:16000] + "\n\n[内容已截断，超过 16000 字符]"

            return content
        except UnicodeDecodeError:
            return "错误: 无法解码文件内容，可能不是文本文件"
        except PermissionError:
            return f"错误: 无权限读取文件: {file_path}"
        except Exception as e:
            return f"错误: 读取文件失败 - {e}"


class WriteFileTool(Tool):
    """
    文件写入工具

    将内容写入指定路径的文件，自动创建所需的父目录。
    受 workspace 安全边界保护，阻止访问工作区外文件。

    注意：会覆盖已存在的文件。
    """

    def __init__(self, workspace: str) -> None:
        """
        Args:
            workspace: 工作区根目录的绝对路径，所有文件操作限制在此目录内
        """
        self.workspace = os.path.abspath(workspace)

    @property
    def name(self) -> str:
        return "write_file"

    @property
    def description(self) -> str:
        return "写入内容到本地文件。参数 file_path 为路径，content 为文件内容。自动创建父目录。"

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "要写入的文件路径，相对于工作区根目录",
                },
                "content": {
                    "type": "string",
                    "description": "要写入的文件内容",
                },
            },
            "required": ["file_path", "content"],
        }

    async def execute(self, **kwargs: Any) -> str:
        file_path = kwargs.get("file_path", "")
        content = kwargs.get("content", "")
        absolute_path = os.path.abspath(os.path.join(self.workspace, file_path))

        # 安全检查：防止路径穿越
        if not absolute_path.startswith(self.workspace):
            return "错误: 拒绝访问工作区外的路径"

        try:
            # 自动创建父目录
            parent_dir = os.path.dirname(absolute_path)
            if parent_dir:
                os.makedirs(parent_dir, exist_ok=True)

            with open(absolute_path, "w", encoding="utf-8") as f:
                f.write(content)

            return f"成功写入文件: {file_path} ({len(content)} 字符)"
        except PermissionError:
            return f"错误: 无权限写入文件: {file_path}"
        except Exception as e:
            return f"错误: 写入文件失败 - {e}"


class ListDirTool(Tool):
    """
    目录列表工具

    列出指定目录的内容，显示文件和子目录。
    文件显示名称和大小，目录名称后附加 / 后缀。
    结果按名称排序。

    受 workspace 安全边界保护。
    """

    def __init__(self, workspace: str) -> None:
        """
        Args:
            workspace: 工作区根目录的绝对路径，所有目录操作限制在此目录内
        """
        self.workspace = os.path.abspath(workspace)

    @property
    def name(self) -> str:
        return "list_dir"

    @property
    def description(self) -> str:
        return "列出目录内容。参数 dir_path 为目录路径，显示文件大小和目录标识。"

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "dir_path": {
                    "type": "string",
                    "description": "要列出的目录路径，相对于工作区根目录，默认为根目录",
                }
            },
            "required": ["dir_path"],
        }

    async def execute(self, **kwargs: Any) -> str:
        dir_path = kwargs.get("dir_path", "")
        absolute_path = os.path.abspath(os.path.join(self.workspace, dir_path))

        # 安全检查：防止路径穿越
        if not absolute_path.startswith(self.workspace):
            return "错误: 拒绝访问工作区外的路径"

        if not os.path.isdir(absolute_path):
            return f"错误: 目录不存在: {dir_path}"

        try:
            entries = []
            for name in os.listdir(absolute_path):
                full_path = os.path.join(absolute_path, name)
                if os.path.isdir(full_path):
                    entries.append(f"{name}/")
                else:
                    try:
                        size = os.path.getsize(full_path)
                        entries.append(f"{name} ({size} bytes)")
                    except OSError:
                        entries.append(f"{name} (大小未知)")

            # 按名称排序
            entries.sort()

            if not entries:
                return f"目录 {dir_path or '/'} 为空"

            header = f"目录 {dir_path or '/'} 内容:"
            return header + "\n" + "\n".join(entries)

        except PermissionError:
            return f"错误: 无权限访问目录: {dir_path}"
        except Exception as e:
            return f"错误: 列出目录失败 - {e}"