import * as cdk from "aws-cdk-lib"
import * as iam from "aws-cdk-lib/aws-iam"
import * as cr from "aws-cdk-lib/custom-resources"
import { Construct } from "constructs"
import { AppConfig } from "./utils/config-manager"

/**
 * Props for the ObservabilityStack nested stack.
 *
 * @property config - Application configuration from config.yaml.
 * @property samplingPercentage - Percentage of traces to index (0-100). Defaults to 100.
 */
export interface ObservabilityStackProps extends cdk.NestedStackProps {
  config: AppConfig
  samplingPercentage: number
}

/**
 * Configures account-level CloudWatch Transaction Search settings via AWS SDK calls.
 *
 * CloudWatch Transaction Search requires three account-level API calls that have no
 * native CloudFormation resource types:
 * 1. logs:PutResourcePolicy — grants X-Ray permission to write spans to CloudWatch Logs
 * 2. xray:UpdateTraceSegmentDestination — redirects traces from X-Ray to CloudWatch Logs
 * 3. xray:UpdateIndexingRule — sets the sampling percentage for trace indexing
 *
 * Uses AwsCustomResource (no Lambda code needed) to make these SDK calls idempotently.
 *
 * NOTE: These are account-level settings, not stack-scoped. Deleting the stack will NOT
 * undo them, which is the desired behavior — you want Transaction Search to stay enabled.
 */
export class ObservabilityStack extends cdk.NestedStack {
  constructor(scope: Construct, id: string, props: ObservabilityStackProps) {
    super(scope, id, props)

    const stack = cdk.Stack.of(this)
    const region = stack.region
    const accountId = stack.account

    // ─── 1. CloudWatch Logs Resource Policy ───────────────────────────────────
    // Grants X-Ray service permission to write trace spans to CloudWatch Logs.
    // The Condition block is REQUIRED — simpler policies without it get rejected
    // with AccessDeniedException by the CloudWatch Logs API.
    const resourcePolicyDocument = JSON.stringify({
      Version: "2012-10-17",
      Statement: [
        {
          Sid: "AllowXRayToWriteSpans",
          Effect: "Allow",
          Principal: {
            Service: "xray.amazonaws.com",
          },
          Action: [
            "logs:PutLogEvents",
            "logs:CreateLogGroup",
            "logs:CreateLogStream",
          ],
          Resource: [
            `arn:aws:logs:${region}:${accountId}:log-group:aws/spans:*`,
            `arn:aws:logs:${region}:${accountId}:log-group:/aws/application-signals/data:*`,
          ],
          Condition: {
            ArnLike: {
              "aws:SourceArn": `arn:aws:xray:${region}:${accountId}:*`,
            },
            StringEquals: {
              "aws:SourceAccount": accountId,
            },
          },
        },
      ],
    })

    new cr.AwsCustomResource(this, "LogsResourcePolicy", {
      onCreate: {
        service: "CloudWatchLogs",
        action: "putResourcePolicy",
        parameters: {
          policyName: "AWSXRayCloudWatchPolicy",
          policyDocument: resourcePolicyDocument,
        },
        physicalResourceId: cr.PhysicalResourceId.of("AWSXRayCloudWatchPolicy"),
      },
      onUpdate: {
        service: "CloudWatchLogs",
        action: "putResourcePolicy",
        parameters: {
          policyName: "AWSXRayCloudWatchPolicy",
          policyDocument: resourcePolicyDocument,
        },
        physicalResourceId: cr.PhysicalResourceId.of("AWSXRayCloudWatchPolicy"),
      },
      // No onDelete — we intentionally leave the policy in place so Transaction Search
      // continues working even if this stack is removed.
      policy: cr.AwsCustomResourcePolicy.fromStatements([
        new iam.PolicyStatement({
          actions: ["logs:PutResourcePolicy", "logs:DeleteResourcePolicy"],
          resources: ["*"],
        }),
      ]),
    })

    // ─── 2. Trace Segment Destination ─────────────────────────────────────────
    // Redirects X-Ray trace segments to CloudWatch Logs instead of the default
    // X-Ray storage. This is required for CloudWatch Transaction Search to work.
    // Status will be PENDING for ~5-10 minutes after first activation.
    // Uses ignoreErrorCodesMatching because the X-Ray API throws InvalidRequestException
    // when the destination is already set to CloudWatchLogs (not idempotent by default).
    // The regex matches the AWS error code returned by the SDK.
    new cr.AwsCustomResource(this, "TraceSegmentDestination", {
      onCreate: {
        service: "XRay",
        action: "updateTraceSegmentDestination",
        parameters: {
          Destination: "CloudWatchLogs",
        },
        physicalResourceId: cr.PhysicalResourceId.of("TraceSegmentDestination"),
        ignoreErrorCodesMatching: "InvalidRequestException",
      },
      onUpdate: {
        service: "XRay",
        action: "updateTraceSegmentDestination",
        parameters: {
          Destination: "CloudWatchLogs",
        },
        physicalResourceId: cr.PhysicalResourceId.of("TraceSegmentDestination"),
        ignoreErrorCodesMatching: "InvalidRequestException",
      },
      // No onDelete — keep Transaction Search enabled.
      policy: cr.AwsCustomResourcePolicy.fromStatements([
        new iam.PolicyStatement({
          actions: [
            "xray:UpdateTraceSegmentDestination",
            "xray:GetTraceSegmentDestination",
          ],
          resources: ["*"],
        }),
      ]),
    })

    // ─── 3. Indexing Rule ───────────────────────────────────────────────────────
    // Sets the percentage of traces that get indexed for search. The "Default"
    // rule applies to all traces that don't match a more specific rule.
    // A value of 100 means all traces are indexed (good for dev/demo).
    // For production, consider lowering to reduce costs.
    new cr.AwsCustomResource(this, "IndexingRule", {
      onCreate: {
        service: "XRay",
        action: "updateIndexingRule",
        parameters: {
          Name: "Default",
          Rule: {
            Probabilistic: {
              DesiredSamplingPercentage: props.samplingPercentage,
            },
          },
        },
        physicalResourceId: cr.PhysicalResourceId.of("DefaultIndexingRule"),
      },
      onUpdate: {
        service: "XRay",
        action: "updateIndexingRule",
        parameters: {
          Name: "Default",
          Rule: {
            Probabilistic: {
              DesiredSamplingPercentage: props.samplingPercentage,
            },
          },
        },
        physicalResourceId: cr.PhysicalResourceId.of("DefaultIndexingRule"),
      },
      // No onDelete — keep indexing rule in place.
      policy: cr.AwsCustomResourcePolicy.fromStatements([
        new iam.PolicyStatement({
          actions: [
            "xray:UpdateIndexingRule",
            "xray:GetIndexingRules",
          ],
          resources: ["*"],
        }),
      ]),
    })

    // ─── Outputs ────────────────────────────────────────────────────────────────
    new cdk.CfnOutput(this, "TransactionSearchStatus", {
      description: "Transaction Search is configured. Trace destination set to CloudWatch Logs.",
      value: `Sampling: ${props.samplingPercentage}%`,
    })
  }
}
