# Security

## Read-only scope

The adapter calls AWS Resource Explorer search and does not provision, modify, or delete
resources. Read-only behavior limits mutation risk; returned resource identifiers, types,
locations, and names can still reveal account structure and operational details. Restrict access
to results and apply the same data classification as the cloud inventory they describe.

## Identity and permissions

Use an AWS workload identity or the supported aiobotocore credential chain. Keep explicit
credentials out of `AWSCloudInventoryConfig`, query scope, source control, environment dumps, and
logs. Grant only the read permissions required to access the selected Resource Explorer view.
Review the current AWS permission model, view scope, and region coverage with the account owner;
the adapter does not configure identities or verify policy effectiveness.

## Sensitive values

The common contract omits raw AWS response documents, and normal model representations hide view
ARNs, resource IDs, labels, and continuation tokens. This reduces accidental display but does not
protect application logs, traces, memory dumps, or explicit serialization. Do not log full pages,
query scopes, or cursors by default. Provider error messages are mapped to bounded common error
categories; avoid logging raw SDK exception context in adapter instrumentation.

Use verified TLS and restrict network egress to the AWS endpoints needed by the adapter. Resource
Explorer results may be partial or permission-filtered, so do not treat inventory as an
authorization or compliance decision.

## Vulnerability reporting

Use the private reporting process in the repository [security policy](../../SECURITY.md).
