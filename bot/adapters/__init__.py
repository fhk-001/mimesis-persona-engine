"""接入层：把 Engine 接到不同的聊天通道上。"""

from .base import Adapter, Incoming  # noqa: F401
from .terminal import TerminalAdapter  # noqa: F401

__all__ = ["Adapter", "Incoming", "TerminalAdapter"]
