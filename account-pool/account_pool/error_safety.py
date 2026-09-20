"""兼容旧导入路径，实际错误脱敏逻辑位于共享基础模块。"""

from account_pool.shared.error_safety import redact_urls, safe_error

__all__ = ["redact_urls", "safe_error"]
