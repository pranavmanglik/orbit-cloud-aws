# Architecture

`orbit-cloud-aws` implements `orbit_cloud.CloudInventoryProvider` using AWS Resource Explorer's
`Search` operation and an asynchronous aiobotocore client. It is a provider adapter for the
read-only common contract in [`orbit-cloud`](https://github.com/orbit-projects/orbit-cloud).

```text
CloudInventoryQuery
  ├── scope must equal the configured view ARN
  ├── resource types and regions become Resource Explorer query syntax
  └── page size/token become MaxResults/NextToken
        ↓
AWS Resource Explorer Search
        ↓
CloudInventoryPage with normalized IDs/types/regions and partial flag
```

The adapter owns AWS endpoint configuration, SDK credential-chain integration, client lifecycle,
query syntax translation, pagination mapping, timeout/concurrency limits, and sanitized AWS error
classification. The view ARN is part of configuration and is redacted from its model repr. The
provider lazily creates one client, shares it across bounded calls, drains active searches during
`aclose()`, and then releases the SDK context.

The response mapping deliberately excludes arbitrary AWS properties and resource names. It keeps
only identifiers, resource types, and regions supported by the common result. AWS-reported errors
and incomplete counts are represented as `CloudInventoryPage.partial`; callers must preserve that
state when presenting or storing results.

Resource Explorer indexing, view filters, coverage, and consistency are AWS-specific and may make
inventory incomplete or delayed. This adapter does not configure those resources and does not
provide a compliance-grade account inventory. It has no live-cloud test claim.
