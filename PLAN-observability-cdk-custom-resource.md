# Plan: CDK Custom Resource for CloudWatch Transaction Search Setup

## Context
CloudWatch Transaction Search requires three account-level API calls that have no native CloudFormation resource types:
1. `logs:PutResourcePolicy` — grants X-Ray permission to write spans to CloudWatch Logs
2. `xray:UpdateTraceSegmentDestination` — redirects traces from X-Ray to CloudWatch Logs
3. `xray:UpdateIndexingRule` — sets the sampling percentage for trace indexing

These were already applied via CLI. This plan codifies them in CDK so they're repeatable and version-controlled.

## Approach
Use a CDK `AwsCustomResource` (from `aws-cdk-lib/custom-resources`) which wraps AWS SDK calls directly — no Lambda code needed. This is the lightest-weight approach for simple API calls.

## Implementation

### File: `infra-cdk/lib/observability-stack.ts` (new NestedStack)

Create a new nested stack with three `AwsCustomResource` constructs:
1. **LogsResourcePolicy** — calls `CloudWatchLogs.putResourcePolicy` on create/update
2. **TraceSegmentDestination** — calls `XRay.updateTraceSegmentDestination` on create/update
3. **IndexingRule** — calls `XRay.updateIndexingRule` on create/update

The custom resource's execution role needs:
- `logs:PutResourcePolicy`, `logs:DeleteResourcePolicy`
- `xray:UpdateTraceSegmentDestination`, `xray:GetTraceSegmentDestination`
- `xray:UpdateIndexingRule`, `xray:GetIndexingRules`

### File: `infra-cdk/lib/main-stack.ts` (modify)

Add the new `ObservabilityStack` as a nested stack, deployed before `BackendStack` so traces are configured before the runtime starts.

### Config
Add `observability.sampling_percentage` to `config.yaml` (default: 100 for dev, can be lowered for prod).

## Risks
- These are account-level settings, not stack-scoped. Deleting the stack won't "undo" them (which is fine — you want Transaction Search to stay enabled).
- If multiple stacks in the same account try to set these, last-write-wins. Acceptable for a single-stack dev setup.

## Testing
- `cdk synth` to verify the template generates correctly
- `cdk deploy` to apply
- Verify with `aws xray get-trace-segment-destination` and `aws xray get-indexing-rules`
