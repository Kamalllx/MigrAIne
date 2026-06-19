# Official AWS Docs Used For Focused AWS Graph Upload

This is a focused set of official sources for migration-relevant services (not a full AWS catalog crawl).

## Core Pricing Catalog Sources

- AWS offers index:
  - https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/index.json
- Per-service pricing offer JSON from each service `currentVersionUrl` in index.
- Per-service region index JSON from each service `currentRegionIndexUrl` in index.

## Migration-Relevant Service Docs (Official)

- Amazon EC2:
  - https://docs.aws.amazon.com/ec2/
  - https://docs.aws.amazon.com/ec2/latest/instancetypes/instance-types.html
- Amazon S3:
  - https://docs.aws.amazon.com/s3/
- Amazon RDS:
  - https://docs.aws.amazon.com/rds/
- Amazon VPC:
  - https://docs.aws.amazon.com/vpc/
- Elastic Load Balancing:
  - https://docs.aws.amazon.com/elasticloadbalancing/
- Route 53:
  - https://docs.aws.amazon.com/route53/
- AWS Lambda:
  - https://docs.aws.amazon.com/lambda/
- Amazon DynamoDB:
  - https://docs.aws.amazon.com/dynamodb/
- Amazon EKS:
  - https://docs.aws.amazon.com/eks/
- Amazon ECS:
  - https://docs.aws.amazon.com/ecs/

## Suggested Focused Allowlist

Use these offer codes when doing a lightweight upload:

- AmazonEC2
- AmazonS3
- AmazonRDS
- AmazonVPC
- AWSLambda
- AmazonDynamoDB
- AmazonEKS
- AmazonECS
- AmazonCloudFront
- AmazonRoute53

Set in `.env`:

```env
AWS_IMPORT_SERVICE_ALLOWLIST=AmazonEC2,AmazonS3,AmazonRDS,AmazonVPC,AWSLambda,AmazonDynamoDB,AmazonEKS,AmazonECS,AmazonCloudFront,AmazonRoute53
```
