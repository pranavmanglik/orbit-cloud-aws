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
"""Async, read-only AWS Resource Explorer adapter."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from typing import Any, Protocol, cast

from aiobotocore.config import AioConfig
from aiobotocore.session import get_session
from botocore.exceptions import BotoCoreError, ClientError
from orbit_cloud import (
    CloudConfigurationError,
    CloudInventoryPage,
    CloudInventoryQuery,
    CloudOperationError,
    CloudResource,
)

from orbit_cloud_aws.config import AWSCloudInventoryConfig


class _AWSClient(Protocol):
    async def search(self, **kwargs: Any) -> Mapping[str, Any]:
        """Search the configured Resource Explorer view with provider query options."""


class _ClientContext(Protocol):
    async def __aenter__(self) -> _AWSClient:
        """Open and return the asynchronous AWS client."""

    async def __aexit__(self, *args: object) -> None:
        """Close the AWS client context and release its resources."""


ClientFactory = Callable[[], _ClientContext]


class AWSCloudInventory:
    """List AWS resources from one preconfigured Resource Explorer view.

    This adapter never configures indexes or views and never makes cloud mutations. Use a view
    scoped to the inventory the application is authorized to see. The SDK client is created on
    the first request, shared across bounded concurrent searches, and closed after active searches
    drain when ``aclose()`` is called.
    """

    def __init__(
        self,
        config: AWSCloudInventoryConfig,
        *,
        client_factory: ClientFactory | None = None,
    ) -> None:
        self.config = config
        self._client_factory = client_factory or self._default_client_factory
        self._client: _AWSClient | None = None
        self._client_context: _ClientContext | None = None
        self._client_lock = asyncio.Lock()
        self._condition = asyncio.Condition()
        self._semaphore = asyncio.Semaphore(config.max_concurrency)
        self._active = 0
        self._closing = False
        self._close_failed = False
        self._closed = False

    def _default_client_factory(self) -> _ClientContext:
        session = get_session()
        return cast(
            _ClientContext,
            session.create_client(
                "resource-explorer-2",
                region_name=self.config.region_name,
                config=AioConfig(
                    connect_timeout=self.config.connect_timeout_seconds,
                    read_timeout=self.config.read_timeout_seconds,
                    retries={
                        "mode": "standard",
                        "total_max_attempts": self.config.retry_total_attempts,
                    },
                ),
            ),
        )

    @asynccontextmanager
    async def _use_client(self) -> AsyncIterator[_AWSClient]:
        async with self._condition:
            if self._closing or self._close_failed or self._closed:
                raise CloudOperationError("closed", "AWS inventory provider is closed.")
            self._active += 1
        try:
            async with self._semaphore:
                async with self._client_lock:
                    if self._client is None:
                        context = self._client_factory()
                        client = await context.__aenter__()
                        self._client_context = context
                        self._client = client
                    client = self._client
                yield client
        finally:
            async with self._condition:
                self._active -= 1
                self._condition.notify_all()

    async def list_resources(self, query: CloudInventoryQuery) -> CloudInventoryPage:
        """Search one AWS Resource Explorer view and return a bounded normalized page."""
        if query.scope != self.config.view_arn:
            raise CloudConfigurationError("scope must match the configured Resource Explorer view.")
        query_string = _build_query(query)
        if len(query_string) > 1280:
            raise CloudConfigurationError("AWS Resource Explorer filters exceed 1280 characters.")
        request: dict[str, object] = {
            "ViewArn": self.config.view_arn,
            "QueryString": query_string,
            "MaxResults": query.page_size,
        }
        if query.page_token is not None:
            request["NextToken"] = query.page_token
        try:
            async with asyncio.timeout(self.config.request_timeout_seconds):
                async with self._use_client() as client:
                    response = await client.search(**request)
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            raise CloudOperationError(
                "timeout", "AWS inventory request timed out.", retryable=True
            ) from None
        except (ClientError, BotoCoreError) as exc:
            raise _map_aws_error(exc) from None
        except CloudOperationError:
            raise
        except Exception:
            raise CloudOperationError(
                "provider-failure", "AWS resource inventory request failed.", retryable=True
            ) from None

        try:
            resources = tuple(
                CloudResource(
                    provider="aws",
                    resource_id=_required_string(item, "Arn"),
                    resource_type=_required_string(item, "ResourceType"),
                    location=_optional_string(item, "Region"),
                )
                for item in _resource_rows(response)
            )
            next_token = response.get("NextToken")
            if next_token is not None and not isinstance(next_token, str):
                raise ValueError("invalid cursor")
            errors = response.get("Errors", [])
            if not isinstance(errors, list) or any(
                not isinstance(error, Mapping) for error in errors
            ):
                raise ValueError("invalid partial-result metadata")
            count = response.get("Count", {})
            if not isinstance(count, Mapping):
                raise ValueError("invalid result count metadata")
            complete = count.get("Complete")
            if complete is not None and not isinstance(complete, bool):
                raise ValueError("invalid result count metadata")
            return CloudInventoryPage(
                resources=resources,
                next_page_token=next_token,
                partial=bool(errors) or complete is False,
            )
        except (TypeError, ValueError):
            raise CloudOperationError(
                "invalid-response", "AWS inventory returned invalid resource metadata."
            ) from None

    async def aclose(self) -> None:
        """Drain active searches and retry SDK cleanup after a failed close."""
        async with self._condition:
            if self._closed:
                return
            if self._closing:
                await self._condition.wait_for(lambda: self._closed or not self._closing)
                if self._closed:
                    return
            self._closing = True
            try:
                await self._condition.wait_for(lambda: self._active == 0)
            except BaseException:
                self._closing = False
                self._condition.notify_all()
                raise
        try:
            context = self._client_context
            if context is not None:
                await context.__aexit__(None, None, None)
        except asyncio.CancelledError:
            async with self._condition:
                self._closing = False
                self._close_failed = True
                self._condition.notify_all()
            raise
        except Exception:
            async with self._condition:
                self._closing = False
                self._close_failed = True
                self._condition.notify_all()
            raise CloudOperationError(
                "close-failed", "AWS SDK client could not be closed."
            ) from None
        async with self._condition:
            self._client_context = None
            self._client = None
            self._closed = True
            self._closing = False
            self._close_failed = False
            self._condition.notify_all()


def _build_query(query: CloudInventoryQuery) -> str:
    filters: list[str] = []
    if query.resource_types:
        filters.append("resourcetype:" + ",".join(query.resource_types))
    if query.locations:
        filters.append("region:" + ",".join(query.locations))
    return " ".join(filters)


def _resource_rows(response: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    rows = response.get("Resources", ())
    if not isinstance(rows, list) or any(not isinstance(row, Mapping) for row in rows):
        raise ValueError("invalid resources")
    return rows


def _required_string(row: Mapping[str, Any], key: str) -> str:
    value = row.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError("invalid metadata")
    return value


def _optional_string(row: Mapping[str, Any], key: str) -> str | None:
    value = row.get(key)
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ValueError("invalid metadata")
    return value


def _map_aws_error(exc: Exception) -> CloudOperationError:
    code = "provider-failure"
    retryable = isinstance(exc, BotoCoreError)
    if isinstance(exc, ClientError):
        response = exc.response
        error = response.get("Error", {}) if isinstance(response, Mapping) else {}
        provider_code = error.get("Code") if isinstance(error, Mapping) else None
        metadata = response.get("ResponseMetadata", {}) if isinstance(response, Mapping) else {}
        status = metadata.get("HTTPStatusCode") if isinstance(metadata, Mapping) else None
        if isinstance(provider_code, str):
            if provider_code in {"AccessDeniedException", "UnauthorizedException"}:
                code = "permission-denied"
            elif provider_code in {"ThrottlingException", "TooManyRequestsException"}:
                code, retryable = "throttled", True
            elif provider_code == "ResourceNotFoundException":
                code = "not-found"
            if isinstance(status, int) and status >= 500:
                code, retryable = "provider-unavailable", True
    message = {
        "permission-denied": "AWS denied the inventory request.",
        "throttled": "AWS limited the inventory request.",
        "not-found": "The AWS inventory view was not found.",
        "provider-unavailable": "AWS inventory is temporarily unavailable.",
        "provider-failure": "AWS inventory request failed.",
    }[code]
    return CloudOperationError(code, message, retryable=retryable)


__all__ = ["AWSCloudInventory"]
