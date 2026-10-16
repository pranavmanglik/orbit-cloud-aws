# orbit-cloud-aws

Lists AWS resources from an existing AWS Resource Explorer view using an asynchronous aiobotocore client.

This is the `AWS Resource Explorer `Search` API` adapter for the provider-neutral [`orbit-cloud`](https://github.com/orbit-projects/orbit-cloud) capability. It performs **read-only inventory**. It never provisions, modifies, or deletes cloud resources.

**Status:** unpublished v0.1.0-alpha review candidate. Contract tests use fake SDK clients and need no cloud credentials. No hosted CI, live-provider, production, support, or release-readiness claim is implied.

## Install

```bash
python -m pip install orbit-cloud orbit-cloud-aws
```

## Use

```python
from orbit_cloud import CloudInventoryQuery
from orbit_cloud_aws import AWSCloudInventory, AWSCloudInventoryConfig

provider = AWSCloudInventory(
    AWSCloudInventoryConfig(
        region_name="us-east-1",
        view_arn="arn:aws:resource-explorer-2:us-east-1:123456789012:view/Inventory/unique-id",
    )
)
try:
    page = await provider.list_resources(
        CloudInventoryQuery(
            scope="arn:aws:resource-explorer-2:us-east-1:123456789012:view/Inventory/unique-id",
            resource_types=("ec2:instance",),
            locations=("us-east-1",),
            page_size=100,
        )
    )
    for resource in page.resources:
        print(resource.resource_type, resource.resource_id)
finally:
    await provider.aclose()
```

The provider owns its async client and closes it during `aclose()`. Calls are bounded by finite request timeouts and a configurable concurrency limit. Cancellation propagates through the asynchronous client. `CloudInventoryPage.next_page_token` is opaque and provider-specific; use it only with the same scope, filters, and page size, and do not log it.

## Provider setup and limits

Configure a Resource Explorer view in AWS and grant the workload identity only `resource-explorer-2:GetView` and `resource-explorer-2:Search` for that view. The configured `view_arn` and query scope must match. This adapter never creates or changes indexes or views. AWS search can be partial or eventually consistent depending on view/index setup and permissions.

Resource Explorer has provider-specific indexing, view filters, region coverage, and permission behavior. Search filters are translated to Resource Explorer filter syntax. An AWS response containing provider-reported errors sets `partial=True`. The adapter does not enable indexes, configure cross-region replication, inspect IAM, or provide all account inventory as a compliance guarantee.

Resource identifiers and names may be sensitive. Orbit deliberately omits raw provider payloads. Do not log resource IDs, scopes, or continuation tokens unless your data handling policy permits it.

## SDK choice

`aiobotocore` provides the asynchronous AWS SDK interface used by Orbit S3 as well. The adapter is network-I/O-bound; no separate Go/Rust runtime or SDK is included without a measured use case.

The Python adapter is the baseline implementation. Native language alternatives require contract conformance and representative evidence before they are recommended.

## Documentation and development

Read the [architecture](docs/architecture/overview.md), [operations](docs/operations/README.md), [security](docs/security/overview.md), and [development](docs/development/README.md) guides.

```bash
python -m pip install -e '.[dev]' build
pytest
ruff check src tests
ruff format --check src tests
mypy
python -m build
```

Live validation, when added, must be opt-in and use a dedicated non-production account, project, or subscription.

Licensed under Apache-2.0.
