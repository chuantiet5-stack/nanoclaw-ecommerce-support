"""渠道适配器模块"""

from channels.base import Channel
from channels.cli import CLIChannel

__all__ = ["Channel", "CLIChannel"]