# Operations

`AWSCloudInventory` queries a Resource Explorer view that already exists. The adapter does not
create or configure views, indexes, or cross-region replication. Its configured `view_arn` must
match the `CloudInventoryQuery.scope` on every call; mismatches fail before any SDK request.

## Identity and view setup

Configure the Resource Explorer view and its indexing/region coverage through your cloud
administration process. Give the application identity only the Resource Explorer read actions
required for querying the selected view. Use the provider's workload identity or standard
aiobotocore credential chain. This adapter accepts no static access keys in its configuration.
Review current AWS IAM action/resource support and view coverage for your account before rollout;
this package does not inspect or certify IAM policy.

## Query and pagination

The common `resource_types` and `locations` filters are translated into Resource Explorer query
syntax. AWS query syntax has its own length and interpretation rules; the adapter rejects a
translated query longer than 1,280 characters. Pass the returned `next_page_token` only with the
same view scope, filters, and page size. The cursor is opaque and may expire. Do not log or modify
it.

AWS may report errors alongside results or mark a count incomplete. The adapter returns these
pages with `partial=True`; it does not silently treat them as complete inventory. Resource
Explorer coverage depends on view filters, enabled indexes, regions, resource type support, and
permissions. An absent result is not proof that a resource does not exist.

## Timeouts and lifecycle

The SDK client is created lazily on the first request and shared across calls. Configuration
separates connect, read, and overall request timeout; the overall deadline also covers time waiting
for an available concurrency slot. At most `max_concurrency` searches enter the client at once.
The SDK uses standard retry mode with `retry_total_attempts` (default 3, including the initial
request and bounded to 1–10). Standard mode applies the SDK's retry classification and exponential
backoff with jitter for retryable throttling and transient failures; the overall request deadline
also bounds time spent waiting between attempts. A retryable error returned after the SDK exhausts
its attempts is still surfaced as a sanitized `CloudOperationError` for application-level policy.
Caller cancellation propagates. `aclose()` stops new searches, waits for active calls to finish,
and closes the async SDK client; call it during application shutdown. After close failure or
cancellation the provider remains unavailable and retains its client context for a later retry.
Bound the application's shutdown deadline because active calls must drain first.

## Operational validation

Use a dedicated non-production account and view to validate identity permissions, indexing delay,
region and type coverage, pagination, partial errors, throttling, request deadlines, cancellation,
client reuse, shutdown with active calls, and credential refresh. Unit tests use fake SDK clients
and do not establish AWS API compatibility or account-wide inventory completeness.
