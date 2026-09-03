"""验证号池结果类型的统一契约。"""

from account_pool.result import Failure, FailureCode, Success


def test_result_types_preserve_typed_success_and_failure_values() -> None:
    success = Success("ready")
    failure = Failure(FailureCode.CONFLICT, "version conflict")

    assert success.value == "ready"
    assert failure.code is FailureCode.CONFLICT
    assert failure.message == "version conflict"
