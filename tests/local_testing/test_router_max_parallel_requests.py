"""本文件验证 Router 的最大并发请求限制与账号池环境共享额度。"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

import litellm
from litellm.utils import calculate_max_parallel_requests

"""
- only rpm
- only tpm
- only max_parallel_requests 
- max_parallel_requests + rpm 
- max_parallel_requests + tpm
- max_parallel_requests + tpm + rpm 
"""


max_parallel_requests_values = [None, 10]
tpm_values = [None, 20, 300000]
rpm_values = [None, 30]
default_max_parallel_requests = [None, 40]


@pytest.mark.parametrize(
    "max_parallel_requests, tpm, rpm, default_max_parallel_requests",
    [
        (mp, tp, rp, dmp)
        for mp in max_parallel_requests_values
        for tp in tpm_values
        for rp in rpm_values
        for dmp in default_max_parallel_requests
    ],
)
def test_scenario(max_parallel_requests, tpm, rpm, default_max_parallel_requests):
    calculated_max_parallel_requests = calculate_max_parallel_requests(
        max_parallel_requests=max_parallel_requests,
        rpm=rpm,
        tpm=tpm,
        default_max_parallel_requests=default_max_parallel_requests,
    )
    if max_parallel_requests is not None:
        assert max_parallel_requests == calculated_max_parallel_requests
    elif rpm is not None:
        assert rpm == calculated_max_parallel_requests
    elif tpm is not None:
        calculated_rpm = int(tpm / 1000 * 6)
        if calculated_rpm == 0:
            calculated_rpm = 1
        assert calculated_rpm == calculated_max_parallel_requests
    elif default_max_parallel_requests is not None:
        assert calculated_max_parallel_requests == default_max_parallel_requests
    else:
        assert calculated_max_parallel_requests is None


@pytest.mark.parametrize(
    "max_parallel_requests, tpm, rpm, default_max_parallel_requests",
    [
        (mp, tp, rp, dmp)
        for mp in max_parallel_requests_values
        for tp in tpm_values
        for rp in rpm_values
        for dmp in default_max_parallel_requests
    ],
)
def test_setting_mpr_limits_per_model(max_parallel_requests, tpm, rpm, default_max_parallel_requests):
    deployment = {
        "model_name": "gpt-3.5-turbo",
        "litellm_params": {
            "model": "gpt-3.5-turbo",
            "max_parallel_requests": max_parallel_requests,
            "tpm": tpm,
            "rpm": rpm,
        },
        "model_info": {"id": "my-unique-id"},
    }

    router = litellm.Router(
        model_list=[deployment],
        default_max_parallel_requests=default_max_parallel_requests,
    )

    mpr_client: asyncio.Semaphore | None = router._get_client(
        deployment=deployment,
        kwargs={},
        client_type="max_parallel_requests",
    )

    if max_parallel_requests is not None:
        assert max_parallel_requests == mpr_client._value
    elif rpm is not None:
        assert rpm == mpr_client._value
    elif tpm is not None:
        calculated_rpm = int(tpm / 1000 * 6)
        if calculated_rpm == 0:
            calculated_rpm = 1
        assert calculated_rpm == mpr_client._value
    elif default_max_parallel_requests is not None:
        assert mpr_client._value == default_max_parallel_requests
    else:
        assert mpr_client is None


_ENVIRONMENT_A = "00000000-0000-4000-8000-000000000001"
_ENVIRONMENT_B = "00000000-0000-4000-8000-000000000002"


def _account_pool_deployment(model_id: str, environment_id: str, concurrency_limit: int) -> dict[str, object]:
    return {
        "model_name": model_id,
        "litellm_params": {"model": f"openai/{model_id}", "max_parallel_requests": concurrency_limit},
        "model_info": {
            "id": model_id,
            "managed_by": "account_pool",
            "account_pool_environment_id": environment_id,
        },
    }


@pytest.mark.asyncio
async def test_account_pool_models_share_environment_semaphore_capacity() -> None:
    router = litellm.Router(
        model_list=[
            _account_pool_deployment("model-a", _ENVIRONMENT_A, 2),
            _account_pool_deployment("model-b", _ENVIRONMENT_A, 2),
        ]
    )
    first: asyncio.Semaphore = router._get_client(
        deployment=_account_pool_deployment("model-a", _ENVIRONMENT_A, 2),
        kwargs={},
        client_type="max_parallel_requests",
    )
    second: asyncio.Semaphore = router._get_client(
        deployment=_account_pool_deployment("model-b", _ENVIRONMENT_A, 2),
        kwargs={},
        client_type="max_parallel_requests",
    )
    third_entered = asyncio.Event()
    release = asyncio.Event()

    async def acquire(semaphore: asyncio.Semaphore) -> None:
        async with semaphore:
            third_entered.set()
            await release.wait()

    assert first is second

    async with first, second:
        third = asyncio.create_task(acquire(second))
        await asyncio.sleep(0)
        assert third_entered.is_set() is False
    await asyncio.wait_for(third_entered.wait(), timeout=0.1)
    release.set()
    await third


@pytest.mark.asyncio
async def test_account_pool_semaphore_releases_after_exception_timeout_and_cancellation() -> None:
    router = litellm.Router(model_list=[_account_pool_deployment("model-a", _ENVIRONMENT_A, 1)])
    semaphore: asyncio.Semaphore = router._get_client(
        deployment=_account_pool_deployment("model-a", _ENVIRONMENT_A, 1),
        kwargs={},
        client_type="max_parallel_requests",
    )

    with pytest.raises(RuntimeError):
        async with semaphore:
            raise RuntimeError("request failed")
    async with semaphore:
        pass

    async def wait_forever() -> None:
        async with semaphore:
            await asyncio.Event().wait()

    with pytest.raises(TimeoutError):
        await asyncio.wait_for(wait_forever(), timeout=0.01)
    async with semaphore:
        pass

    cancelled = asyncio.create_task(wait_forever())
    await asyncio.sleep(0)
    cancelled.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled
    async with semaphore:
        pass


def test_account_pool_limit_change_updates_shared_semaphore() -> None:
    router = litellm.Router(model_list=[_account_pool_deployment("model-a", _ENVIRONMENT_A, 2)])
    original: asyncio.Semaphore = router._get_client(
        deployment=_account_pool_deployment("model-a", _ENVIRONMENT_A, 2),
        kwargs={},
        client_type="max_parallel_requests",
    )
    router.set_model_list([_account_pool_deployment("model-a", _ENVIRONMENT_A, 1)])
    updated: asyncio.Semaphore = router._get_client(
        deployment=_account_pool_deployment("model-a", _ENVIRONMENT_A, 1),
        kwargs={},
        client_type="max_parallel_requests",
    )

    assert updated is original
    assert updated._value == 1


@pytest.mark.asyncio
async def test_account_pool_limit_reduction_waits_for_old_requests_before_admitting_new_request() -> None:
    router = litellm.Router(model_list=[_account_pool_deployment("model-a", _ENVIRONMENT_A, 2)])
    semaphore: asyncio.Semaphore = router._get_client(
        deployment=_account_pool_deployment("model-a", _ENVIRONMENT_A, 2),
        kwargs={},
        client_type="max_parallel_requests",
    )
    entered = asyncio.Event()
    release = asyncio.Event()

    async def acquire() -> None:
        async with semaphore:
            entered.set()
            await release.wait()

    async with semaphore, semaphore:
        router.set_model_list([_account_pool_deployment("model-a", _ENVIRONMENT_A, 1)])
        router._get_client(
            deployment=_account_pool_deployment("model-a", _ENVIRONMENT_A, 1),
            kwargs={},
            client_type="max_parallel_requests",
        )
        waiting = asyncio.create_task(acquire())
        await asyncio.sleep(0)
        assert entered.is_set() is False
    await asyncio.wait_for(entered.wait(), timeout=0.1)
    release.set()
    await waiting


@pytest.mark.asyncio
async def test_account_pool_rejects_admission_from_another_event_loop() -> None:
    router = litellm.Router(model_list=[_account_pool_deployment("model-a", _ENVIRONMENT_A, 1)])
    semaphore: asyncio.Semaphore = router._get_client(
        deployment=_account_pool_deployment("model-a", _ENVIRONMENT_A, 1),
        kwargs={},
        client_type="max_parallel_requests",
    )
    async with semaphore:
        pass

    def acquire_from_other_loop() -> str:
        async def acquire() -> None:
            async with semaphore:
                pass

        try:
            asyncio.run(acquire())
        except RuntimeError as error:
            return str(error)
        raise AssertionError("expected a cross-loop ownership error")

    with ThreadPoolExecutor(max_workers=1) as executor:
        message = executor.submit(acquire_from_other_loop).result()

    assert "another event loop" in message


@pytest.mark.asyncio
async def test_account_pool_cancelled_waiter_during_permit_debt_does_not_admit_later_waiter() -> None:
    router = litellm.Router(model_list=[_account_pool_deployment("model-a", _ENVIRONMENT_A, 2)])
    semaphore: asyncio.Semaphore = router._get_client(
        deployment=_account_pool_deployment("model-a", _ENVIRONMENT_A, 2),
        kwargs={},
        client_type="max_parallel_requests",
    )
    entered = asyncio.Event()

    async def acquire() -> None:
        async with semaphore:
            entered.set()

    async with semaphore, semaphore:
        router.set_model_list([_account_pool_deployment("model-a", _ENVIRONMENT_A, 1)])
        router._get_client(
            deployment=_account_pool_deployment("model-a", _ENVIRONMENT_A, 1),
            kwargs={},
            client_type="max_parallel_requests",
        )
        cancelled = asyncio.create_task(acquire())
        await asyncio.sleep(0)
        cancelled.cancel()
        with pytest.raises(asyncio.CancelledError):
            await cancelled
        later = asyncio.create_task(acquire())
        await asyncio.sleep(0)
        assert entered.is_set() is False
    await asyncio.wait_for(entered.wait(), timeout=0.1)
    await later


@pytest.mark.asyncio
async def test_account_pool_limit_increase_admits_additional_waiting_request() -> None:
    router = litellm.Router(model_list=[_account_pool_deployment("model-a", _ENVIRONMENT_A, 1)])
    semaphore: asyncio.Semaphore = router._get_client(
        deployment=_account_pool_deployment("model-a", _ENVIRONMENT_A, 1),
        kwargs={},
        client_type="max_parallel_requests",
    )
    entered = asyncio.Event()
    release = asyncio.Event()

    async def acquire() -> None:
        async with semaphore:
            entered.set()
            await release.wait()

    async with semaphore:
        waiting = asyncio.create_task(acquire())
        await asyncio.sleep(0)
        assert entered.is_set() is False
        router.set_model_list([_account_pool_deployment("model-a", _ENVIRONMENT_A, 2)])
        router._get_client(
            deployment=_account_pool_deployment("model-a", _ENVIRONMENT_A, 2),
            kwargs={},
            client_type="max_parallel_requests",
        )
        await asyncio.wait_for(entered.wait(), timeout=0.1)
    release.set()
    await waiting


def test_account_pool_reload_updates_existing_semaphore_before_next_lookup() -> None:
    router = litellm.Router(model_list=[_account_pool_deployment("model-a", _ENVIRONMENT_A, 3)])
    semaphore: asyncio.Semaphore = router._get_client(
        deployment=_account_pool_deployment("model-a", _ENVIRONMENT_A, 3),
        kwargs={},
        client_type="max_parallel_requests",
    )
    router.set_model_list([_account_pool_deployment("model-b", _ENVIRONMENT_A, 1)])

    assert semaphore._value == 1


def test_account_pool_concurrent_cold_lookup_returns_one_semaphore() -> None:
    router = litellm.Router(
        model_list=[
            _account_pool_deployment("model-a", _ENVIRONMENT_A, 2),
            _account_pool_deployment("model-b", _ENVIRONMENT_A, 2),
        ]
    )
    barrier = Barrier(2)

    def get_semaphore(model_id: str) -> asyncio.Semaphore:
        barrier.wait()
        return router._get_client(
            deployment=_account_pool_deployment(model_id, _ENVIRONMENT_A, 2),
            kwargs={},
            client_type="max_parallel_requests",
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = tuple(executor.map(get_semaphore, ("model-a", "model-b")))

    assert first is second


@pytest.mark.asyncio
async def test_account_pool_repeated_snapshot_resizes_apply_latest_limit() -> None:
    router = litellm.Router(model_list=[_account_pool_deployment("model-a", _ENVIRONMENT_A, 1)])
    semaphore: asyncio.Semaphore = router._get_client(
        deployment=_account_pool_deployment("model-a", _ENVIRONMENT_A, 1),
        kwargs={},
        client_type="max_parallel_requests",
    )
    router.set_model_list([_account_pool_deployment("model-a", _ENVIRONMENT_A, 3)])
    router._get_client(
        deployment=_account_pool_deployment("model-a", _ENVIRONMENT_A, 3),
        kwargs={},
        client_type="max_parallel_requests",
    )
    router.set_model_list([_account_pool_deployment("model-a", _ENVIRONMENT_A, 2)])
    updated: asyncio.Semaphore = router._get_client(
        deployment=_account_pool_deployment("model-a", _ENVIRONMENT_A, 2),
        kwargs={},
        client_type="max_parallel_requests",
    )
    third_entered = asyncio.Event()

    async def acquire() -> None:
        async with updated:
            third_entered.set()

    assert updated is semaphore
    async with updated, updated:
        third = asyncio.create_task(acquire())
        await asyncio.sleep(0)
        assert third_entered.is_set() is False
    await asyncio.wait_for(third_entered.wait(), timeout=0.1)
    await third


def test_account_pool_shared_limit_is_snapshot_authoritative() -> None:
    router = litellm.Router(
        model_list=[
            _account_pool_deployment("model-a", _ENVIRONMENT_A, 2),
            _account_pool_deployment("model-b", _ENVIRONMENT_A, 3),
        ]
    )
    first: asyncio.Semaphore = router._get_client(
        deployment=_account_pool_deployment("model-a", _ENVIRONMENT_A, 2),
        kwargs={},
        client_type="max_parallel_requests",
    )
    second: asyncio.Semaphore = router._get_client(
        deployment=_account_pool_deployment("model-b", _ENVIRONMENT_A, 3),
        kwargs={},
        client_type="max_parallel_requests",
    )
    third: asyncio.Semaphore = router._get_client(
        deployment=_account_pool_deployment("model-a", _ENVIRONMENT_A, 2),
        kwargs={},
        client_type="max_parallel_requests",
    )

    assert first is second is third
    assert first._value == 2


def test_unmanaged_or_malformed_account_pool_metadata_keeps_deployment_semaphores_isolated() -> None:
    environment_id = _ENVIRONMENT_A
    router = litellm.Router(
        model_list=[
            _account_pool_deployment("managed", environment_id, 2),
            {
                "model_name": "unmanaged",
                "litellm_params": {"model": "openai/unmanaged", "max_parallel_requests": 2},
                "model_info": {"id": "unmanaged", "account_pool_environment_id": environment_id},
            },
            {
                "model_name": "malformed",
                "litellm_params": {"model": "openai/malformed", "max_parallel_requests": 2},
                "model_info": {
                    "id": "malformed",
                    "managed_by": "account_pool",
                    "account_pool_environment_id": "not-a-uuid",
                },
            },
        ]
    )
    managed: asyncio.Semaphore = router._get_client(
        deployment=_account_pool_deployment("managed", environment_id, 2),
        kwargs={},
        client_type="max_parallel_requests",
    )
    unmanaged: asyncio.Semaphore = router._get_client(
        deployment={
            "model_name": "unmanaged",
            "litellm_params": {"model": "openai/unmanaged", "max_parallel_requests": 2},
            "model_info": {"id": "unmanaged", "account_pool_environment_id": environment_id},
        },
        kwargs={},
        client_type="max_parallel_requests",
    )
    malformed: asyncio.Semaphore = router._get_client(
        deployment={
            "model_name": "malformed",
            "litellm_params": {"model": "openai/malformed", "max_parallel_requests": 2},
            "model_info": {
                "id": "malformed",
                "managed_by": "account_pool",
                "account_pool_environment_id": "not-a-uuid",
            },
        },
        kwargs={},
        client_type="max_parallel_requests",
    )

    assert managed is not unmanaged
    assert managed is not malformed


def test_semaphore_cache_key_keeps_account_pool_environments_and_standard_deployments_isolated() -> None:
    router = litellm.Router(
        model_list=[
            _account_pool_deployment("model-a", _ENVIRONMENT_A, 2),
            _account_pool_deployment("model-b", _ENVIRONMENT_B, 2),
            {
                "model_name": "model-c",
                "litellm_params": {"model": "openai/model-c", "max_parallel_requests": 2},
                "model_info": {"id": "model-c"},
            },
        ]
    )
    environment_a: asyncio.Semaphore = router._get_client(
        deployment=_account_pool_deployment("model-a", _ENVIRONMENT_A, 2),
        kwargs={},
        client_type="max_parallel_requests",
    )
    environment_b: asyncio.Semaphore = router._get_client(
        deployment=_account_pool_deployment("model-b", _ENVIRONMENT_B, 2),
        kwargs={},
        client_type="max_parallel_requests",
    )
    standard: asyncio.Semaphore = router._get_client(
        deployment={
            "model_name": "model-c",
            "litellm_params": {"model": "openai/model-c", "max_parallel_requests": 2},
            "model_info": {"id": "model-c"},
        },
        kwargs={},
        client_type="max_parallel_requests",
    )

    assert environment_a is not environment_b
    assert environment_a is not standard


async def _handle_router_calls(router):
    pre_fill = """
    Lorem ipsum dolor sit amet, consectetur adipiscing elit. Nunc ut finibus massa. Quisque a magna magna. Quisque neque diam, varius sit amet tellus eu, elementum fermentum sapien. Integer ut erat eget arcu rutrum blandit. Morbi a metus purus. Nulla porta, urna at finibus malesuada, velit ante suscipit orci, vitae laoreet dui ligula ut augue. Cras elementum pretium dui, nec luctus nulla aliquet ut. Nam faucibus, diam nec semper interdum, nisl nisi viverra nulla, vitae sodales elit ex a purus. Donec tristique malesuada lobortis. Donec posuere iaculis nisl, vitae accumsan libero dignissim dignissim. Suspendisse finibus leo et ex mattis tempor. Praesent at nisl vitae quam egestas lacinia. Donec in justo non erat aliquam accumsan sed vitae ex. Vivamus gravida diam vel ipsum tincidunt dignissim.

    Cras vitae efficitur tortor. Curabitur vel erat mollis, euismod diam quis, consequat nibh. Ut vel est eu nulla euismod finibus. Aliquam euismod at risus quis dignissim. Integer non auctor massa. Nullam vitae aliquet mauris. Etiam risus enim, dignissim ut volutpat eget, pulvinar ac augue. Mauris elit est, ultricies vel convallis at, rhoncus nec elit. Aenean ornare maximus orci, ut maximus felis cursus venenatis. Nulla facilisi.

    Maecenas aliquet ante massa, at ullamcorper nibh dictum quis. Pellentesque habitant morbi tristique senectus et netus et malesuada fames ac turpis egestas. Quisque id egestas justo. Suspendisse fringilla in massa in consectetur. Quisque scelerisque egestas lacus at posuere. Vestibulum dui sem, bibendum vehicula ultricies vel, blandit id nisi. Curabitur ullamcorper semper metus, vitae commodo magna. Nulla mi metus, suscipit in neque vitae, porttitor pharetra erat. Vestibulum libero velit, congue in diam non, efficitur suscipit diam. Integer arcu velit, fermentum vel tortor sit amet, venenatis rutrum felis. Donec ultricies enim sit amet iaculis mattis.

    Integer at purus posuere, malesuada tortor vitae, mattis nibh. Mauris ex quam, tincidunt et fermentum vitae, iaculis non elit. Nullam dapibus non nisl ac sagittis. Duis lacinia eros iaculis lectus consectetur vehicula. Class aptent taciti sociosqu ad litora torquent per conubia nostra, per inceptos himenaeos. Interdum et malesuada fames ac ante ipsum primis in faucibus. Ut cursus semper est, vel interdum turpis ultrices dictum. Suspendisse posuere lorem et accumsan ultrices. Duis sagittis bibendum consequat. Ut convallis vestibulum enim, non dapibus est porttitor et. Quisque suscipit pulvinar turpis, varius tempor turpis. Vestibulum semper dui nunc, vel vulputate elit convallis quis. Fusce aliquam enim nulla, eu congue nunc tempus eu.

    Nam vitae finibus eros, eu eleifend erat. Maecenas hendrerit magna quis molestie dictum. Ut consequat quam eu massa auctor pulvinar. Pellentesque vitae eros ornare urna accumsan tempor. Maecenas porta id quam at sodales. Donec quis accumsan leo, vel viverra nibh. Vestibulum congue blandit nulla, sed rhoncus libero eleifend ac. In risus lorem, rutrum et tincidunt a, interdum a lectus. Pellentesque aliquet pulvinar mauris, ut ultrices nibh ultricies nec. Mauris mi mauris, facilisis nec metus non, egestas luctus ligula. Quisque ac ligula at felis mollis blandit id nec risus. Nam sollicitudin lacus sed sapien fringilla ullamcorper. Etiam dui quam, posuere sit amet velit id, aliquet molestie ante. Integer cursus eget sapien fringilla elementum. Integer molestie, mi ac scelerisque ultrices, nunc purus condimentum est, in posuere quam nibh vitae velit.
    """
    completion = await router.acompletion(
        "gpt-3.5-turbo",
        [
            {
                "role": "user",
                # Fixed speed (was random.random()*100) so the request body is
                # deterministic and the VCR cassette replays instead of
                # appending a new episode every run. This is a rate-limiting
                # test; the prompt content is irrelevant to what it asserts.
                "content": f"{pre_fill * 3}\n\nRecite the Declaration of independence at a speed of 50.0 words per minute.",
            }
        ],
        stream=True,
        temperature=0.0,
        stream_options={"include_usage": True},
    )

    async for chunk in completion:
        pass


@pytest.mark.asyncio
async def test_max_parallel_requests_rpm_rate_limiting():
    """
    - make sure requests > model limits are retried successfully.
    """
    from litellm import Router

    router = Router(
        routing_strategy="usage-based-routing-v2",
        enable_pre_call_checks=True,
        model_list=[
            {
                "model_name": "gpt-3.5-turbo",
                "litellm_params": {
                    "model": "gpt-3.5-turbo",
                    "temperature": 0.0,
                    "rpm": 1,
                    "num_retries": 3,
                },
            }
        ],
    )
    await asyncio.gather(*[_handle_router_calls(router) for _ in range(3)])


@pytest.mark.asyncio
async def test_max_parallel_requests_tpm_rate_limiting_base_case():
    """
    - check error raised if defined tpm limit crossed.
    """
    from litellm import Router

    _messages = [{"role": "user", "content": "Hey, how's it going?"}]
    router = Router(
        routing_strategy="usage-based-routing-v2",
        enable_pre_call_checks=True,
        model_list=[
            {
                "model_name": "gpt-4o-2024-08-06",
                "litellm_params": {
                    "model": "gpt-4o-2024-08-06",
                    "temperature": 0.0,
                    "tpm": 1,
                },
            }
        ],
        num_retries=0,
    )

    async def _exceed_limit():
        for _ in range(2):
            await router.acompletion(
                model="gpt-4o-2024-08-06",
                messages=_messages,
            )

    with pytest.raises(litellm.RateLimitError):
        await _exceed_limit()
