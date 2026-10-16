# Copyright 2026-present Orbit Contributors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""AWS inventory translation, safe failures, cancellation, and lifecycle tests."""

import asyncio
from collections.abc import Mapping
from typing import Any

import pytest
from aiobotocore.config import AioConfig
from botocore.exceptions import ClientError
from orbit_cloud import CloudInventoryQuery, CloudOperationError

from orbit_cloud_aws import AWSCloudInventory, AWSCloudInventoryConfig

VIEW = "arn:aws:resource-explorer-2:us-east-1:123456789012:view/Inventory/unique-id"


class FakeClient:
    def __init__(self, response: Mapping[str, Any] | None = None) -> None:
        self.response = response or {"Resources": []}
        self.request: dict[str, Any] | None = None
        self.closed = False
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.block = False
        self.failure: Exception | None = None

    async def search(self, **kwargs: Any) -> Mapping[str, Any]:
        self.request = kwargs
        self.started.set()
        if self.block:
            await self.release.wait()
        if self.failure is not None:
            raise self.failure
        return self.response


class FakeClientContext:
    def __init__(self, client: FakeClient) -> None:
        self.client = client

    async def __aenter__(self) -> FakeClient:
        return self.client

    async def __aexit__(self, *args: object) -> None:
        self.client.closed = True


def make_provider(client: FakeClient) -> AWSCloudInventory:
    config = AWSCloudInventoryConfig(region_name="us-east-1", view_arn=VIEW)
    return AWSCloudInventory(config, client_factory=lambda: FakeClientContext(client))


def test_configured_standard_retries_use_a_bounded_total_attempt_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Use standard SDK backoff with an explicit total-attempt bound, including the first call."""

    class FakeSession:
        """Capture default client options without creating a network client."""

        options: dict[str, Any] | None = None

        def create_client(self, service: str, **kwargs: Any) -> FakeClientContext:
            """Record the service and retry configuration supplied by the adapter."""
            self.options = {"service": service, **kwargs}
            return FakeClientContext(FakeClient())

    session = FakeSession()
    monkeypatch.setattr("orbit_cloud_aws.inventory.get_session", lambda: session)
    config = AWSCloudInventoryConfig(
        region_name="us-east-1",
        view_arn=VIEW,
        retry_total_attempts=4,
    )
    provider = AWSCloudInventory(config)

    provider._default_client_factory()

    assert session.options is not None
    assert session.options["service"] == "resource-explorer-2"
    sdk_config = session.options["config"]
    assert isinstance(sdk_config, AioConfig)
    assert sdk_config.retries == {"mode": "standard", "total_max_attempts": 4}


@pytest.mark.parametrize("attempts", [0, 11, True])
def test_retry_total_attempts_rejects_unbounded_or_ambiguous_values(attempts: object) -> None:
    """Require a strict bounded integer for provider retry behavior."""
    with pytest.raises(ValueError):
        AWSCloudInventoryConfig(
            region_name="us-east-1",
            view_arn=VIEW,
            retry_total_attempts=attempts,
        )


@pytest.mark.asyncio
async def test_failed_client_close_retains_context_for_retry() -> None:
    """A failed SDK exit cannot discard the only handle capable of retrying cleanup."""
    client = FakeClient()

    class FailOnceContext(FakeClientContext):
        close_calls = 0

        async def __aexit__(self, *args: object) -> None:
            self.close_calls += 1
            if self.close_calls == 1:
                raise RuntimeError("temporary SDK close failure")
            await super().__aexit__(*args)

    context = FailOnceContext(client)
    provider = AWSCloudInventory(
        AWSCloudInventoryConfig(region_name="us-east-1", view_arn=VIEW),
        client_factory=lambda: context,
    )
    await provider.list_resources(CloudInventoryQuery(scope=VIEW))

    with pytest.raises(CloudOperationError, match="could not be closed"):
        await provider.aclose()
    with pytest.raises(CloudOperationError, match="closed"):
        await provider.list_resources(CloudInventoryQuery(scope=VIEW))

    await provider.aclose()
    await provider.aclose()
    assert client.closed
    assert context.close_calls == 2


@pytest.mark.asyncio
async def test_search_translates_common_filters_and_normalizes_metadata() -> None:
    client = FakeClient(
        {
            "Resources": [
                {
                    "Arn": "arn:aws:ec2:us-east-1:123456789012:instance/i-123",
                    "ResourceType": "ec2:instance",
                    "Region": "us-east-1",
                    "Properties": [{"Name": "secret-like-untrusted-provider-data"}],
                }
            ],
            "NextToken": "opaque-next-page-token",
        }
    )
    provider = make_provider(client)

    page = await provider.list_resources(
        CloudInventoryQuery(
            scope=VIEW,
            resource_types=("ec2:instance", "ec2:volume"),
            locations=("us-east-1", "us-west-2"),
            page_size=40,
        )
    )

    assert client.request == {
        "ViewArn": VIEW,
        "QueryString": "resourcetype:ec2:instance,ec2:volume region:us-east-1,us-west-2",
        "MaxResults": 40,
    }
    assert page.resources[0].provider == "aws"
    assert page.resources[0].name is None
    assert page.resources[0].resource_type == "ec2:instance"
    assert page.next_page_token == "opaque-next-page-token"
    assert "opaque-next-page-token" not in repr(page)
    assert "secret-like" not in repr(page)
    await provider.aclose()
    assert client.closed


@pytest.mark.asyncio
async def test_scope_is_bound_to_configured_view_without_calling_provider() -> None:
    client = FakeClient()
    provider = make_provider(client)

    with pytest.raises(ValueError, match="scope must match"):
        await provider.list_resources(CloudInventoryQuery(scope="arn:aws:other"))

    assert client.request is None
    await provider.aclose()


@pytest.mark.asyncio
async def test_partial_results_and_cursor_are_returned() -> None:
    client = FakeClient({"Resources": [], "Errors": [{"ErrorCode": "AccessDenied"}]})
    provider = make_provider(client)

    page = await provider.list_resources(CloudInventoryQuery(scope=VIEW, page_token="cursor"))

    assert page.partial is True
    assert client.request is not None
    assert client.request["NextToken"] == "cursor"
    await provider.aclose()


@pytest.mark.asyncio
async def test_incomplete_count_marks_page_partial() -> None:
    client = FakeClient({"Resources": [], "Count": {"Complete": False}})
    provider = make_provider(client)

    page = await provider.list_resources(CloudInventoryQuery(scope=VIEW))

    assert page.partial is True
    await provider.aclose()


@pytest.mark.asyncio
async def test_invalid_error_metadata_fails_closed() -> None:
    client = FakeClient({"Resources": [], "Errors": "provider payload"})
    provider = make_provider(client)

    with pytest.raises(CloudOperationError, match="invalid resource metadata"):
        await provider.list_resources(CloudInventoryQuery(scope=VIEW))
    await provider.aclose()


@pytest.mark.asyncio
async def test_provider_error_is_sanitized_and_retry_classified() -> None:
    client = FakeClient()
    client.failure = ClientError(
        {
            "Error": {"Code": "ThrottlingException", "Message": "secret detail"},
            "ResponseMetadata": {"HTTPStatusCode": 429},
        },
        "Search",
    )
    provider = make_provider(client)

    with pytest.raises(CloudOperationError) as caught:
        await provider.list_resources(CloudInventoryQuery(scope=VIEW))

    assert caught.value.code == "throttled"
    assert caught.value.retryable is True
    assert "secret detail" not in str(caught.value)
    await provider.aclose()


@pytest.mark.asyncio
async def test_invalid_provider_record_fails_closed() -> None:
    client = FakeClient({"Resources": [{"ResourceType": "ec2:instance"}]})
    provider = make_provider(client)

    with pytest.raises(CloudOperationError, match="invalid resource metadata"):
        await provider.list_resources(CloudInventoryQuery(scope=VIEW))
    await provider.aclose()


@pytest.mark.asyncio
async def test_close_waits_for_active_call_and_is_idempotent() -> None:
    client = FakeClient()
    client.block = True
    provider = make_provider(client)
    operation = asyncio.create_task(provider.list_resources(CloudInventoryQuery(scope=VIEW)))
    await asyncio.wait_for(client.started.wait(), timeout=1)
    closing = asyncio.create_task(provider.aclose())
    await asyncio.sleep(0)
    assert not closing.done()
    client.release.set()
    await operation
    await closing
    await provider.aclose()
    assert client.closed


@pytest.mark.asyncio
async def test_cancelled_close_can_be_retried_without_stranding_provider() -> None:
    client = FakeClient()
    client.block = True
    provider = make_provider(client)
    operation = asyncio.create_task(provider.list_resources(CloudInventoryQuery(scope=VIEW)))
    await asyncio.wait_for(client.started.wait(), timeout=1)
    closing = asyncio.create_task(provider.aclose())
    await asyncio.sleep(0)
    closing.cancel()
    with pytest.raises(asyncio.CancelledError):
        await closing

    client.release.set()
    await operation
    await provider.aclose()
    assert client.closed


@pytest.mark.asyncio
async def test_cancelled_search_propagates_and_releases_operation_slot() -> None:
    client = FakeClient()
    client.block = True
    provider = make_provider(client)
    operation = asyncio.create_task(provider.list_resources(CloudInventoryQuery(scope=VIEW)))
    await asyncio.wait_for(client.started.wait(), timeout=1)
    operation.cancel()
    with pytest.raises(asyncio.CancelledError):
        await operation
    client.release.set()
    await provider.aclose()
