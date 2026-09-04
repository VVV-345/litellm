"""本包定义号池可用渠道的静态注册表。"""

from account_pool.channels.registry import ChannelRegistry, UnsupportedChannelError

__all__ = ("ChannelRegistry", "UnsupportedChannelError")
