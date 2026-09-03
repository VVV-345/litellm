"""本模块定义号池服务统一使用的结果类型。"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Generic, TypeVar

T = TypeVar("T")


class FailureCode(StrEnum):
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    INVALID = "invalid"
    UPSTREAM = "upstream"


@dataclass(frozen=True, slots=True)
class Success(Generic[T]):
    value: T


@dataclass(frozen=True, slots=True)
class Failure:
    code: FailureCode
    message: str


Result = Success[T] | Failure
