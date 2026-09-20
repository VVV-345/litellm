"""兼容旧导入路径，实际结果类型位于共享基础模块。"""

from account_pool.shared.result import Failure, FailureCode, Result, Success

__all__ = ["Failure", "FailureCode", "Result", "Success"]
