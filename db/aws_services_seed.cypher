// AWS Service Graph Seed for Neo4j
// Priorities are normalized to [0.0, 1.0], where higher means more likely/recommended.

CREATE CONSTRAINT aws_provider_name_unique IF NOT EXISTS
FOR (p:CloudProvider) REQUIRE p.name IS UNIQUE;

CREATE CONSTRAINT aws_category_name_unique IF NOT EXISTS
FOR (c:ServiceCategory) REQUIRE c.name IS UNIQUE;

CREATE CONSTRAINT aws_service_id_unique IF NOT EXISTS
FOR (s:Service) REQUIRE s.id IS UNIQUE;

CREATE CONSTRAINT aws_variant_id_unique IF NOT EXISTS
FOR (v:ServiceVariant) REQUIRE v.id IS UNIQUE;

MERGE (aws:CloudProvider {name: 'AWS'})
SET aws.description = 'Amazon Web Services service catalog for migration recommendation';

WITH aws
UNWIND [
  {name: 'Object Storage'},
  {name: 'Block Storage'},
  {name: 'Shared File Storage'},
  {name: 'VM Compute'},
  {name: 'Serverless Compute'},
  {name: 'Container Compute'},
  {name: 'Relational Database'},
  {name: 'NoSQL Database'},
  {name: 'In-Memory Cache'},
  {name: 'Messaging'},
  {name: 'Event Streaming'},
  {name: 'Networking'},
  {name: 'Security Identity'},
  {name: 'Observability Governance'},
  {name: 'Analytics Data Processing'}
] AS categoryData
MERGE (c:ServiceCategory {name: categoryData.name})
MERGE (aws)-[:HAS_CATEGORY]->(c);

WITH aws
UNWIND [
  {id:'aws.s3', name:'Amazon S3', category:'Object Storage', basePriority:0.98, rank:1, notes:'Default object storage choice in most migrations'},
  {id:'aws.ebs', name:'Amazon EBS', category:'Block Storage', basePriority:0.74, rank:1, notes:'Primary block storage for EC2-backed workloads'},
  {id:'aws.efs', name:'Amazon EFS', category:'Shared File Storage', basePriority:0.62, rank:1, notes:'Managed NFS for shared Linux workloads'},
  {id:'aws.fsx', name:'Amazon FSx', category:'Shared File Storage', basePriority:0.41, rank:2, notes:'Specialized file systems (Windows, Lustre, NetApp, OpenZFS)'},

  {id:'aws.ec2', name:'Amazon EC2', category:'VM Compute', basePriority:0.93, rank:1, notes:'Most common VM replacement for GCE instances'},
  {id:'aws.lambda', name:'AWS Lambda', category:'Serverless Compute', basePriority:0.94, rank:1, notes:'High adoption for event-driven/serverless functions'},
  {id:'aws.ecs', name:'Amazon ECS', category:'Container Compute', basePriority:0.86, rank:1, notes:'Managed container orchestration with low ops overhead'},
  {id:'aws.eks', name:'Amazon EKS', category:'Container Compute', basePriority:0.68, rank:2, notes:'Kubernetes-based orchestration where K8s portability is required'},

  {id:'aws.rds', name:'Amazon RDS', category:'Relational Database', basePriority:0.95, rank:1, notes:'Default managed relational database for migrations'},
  {id:'aws.aurora', name:'Amazon Aurora', category:'Relational Database', basePriority:0.87, rank:2, notes:'High-performance cloud-native MySQL/PostgreSQL-compatible DB'},
  {id:'aws.dynamodb', name:'Amazon DynamoDB', category:'NoSQL Database', basePriority:0.89, rank:1, notes:'Primary managed key-value/document database'},
  {id:'aws.elasticache', name:'Amazon ElastiCache', category:'In-Memory Cache', basePriority:0.78, rank:1, notes:'Managed Redis/Memcached caching layer'},

  {id:'aws.sqs', name:'Amazon SQS', category:'Messaging', basePriority:0.90, rank:1, notes:'Standard queueing service for async decoupling'},
  {id:'aws.sns', name:'Amazon SNS', category:'Messaging', basePriority:0.83, rank:2, notes:'Pub/sub fan-out notifications'},
  {id:'aws.eventbridge', name:'Amazon EventBridge', category:'Messaging', basePriority:0.80, rank:3, notes:'Event bus and routing across services and SaaS'},
  {id:'aws.kinesis', name:'Amazon Kinesis', category:'Event Streaming', basePriority:0.57, rank:1, notes:'Managed stream ingestion and delivery'},
  {id:'aws.msk', name:'Amazon MSK', category:'Event Streaming', basePriority:0.46, rank:2, notes:'Managed Apache Kafka clusters/serverless Kafka'},

  {id:'aws.vpc', name:'Amazon VPC', category:'Networking', basePriority:0.99, rank:1, notes:'Core networking foundation for most AWS workloads'},
  {id:'aws.alb', name:'Elastic Load Balancing - ALB', category:'Networking', basePriority:0.91, rank:2, notes:'HTTP/HTTPS layer-7 load balancer'},
  {id:'aws.nlb', name:'Elastic Load Balancing - NLB', category:'Networking', basePriority:0.65, rank:3, notes:'TCP/UDP/TLS load balancer for high throughput/low latency'},
  {id:'aws.route53', name:'Amazon Route 53', category:'Networking', basePriority:0.84, rank:4, notes:'DNS and traffic routing'},
  {id:'aws.apigateway', name:'Amazon API Gateway', category:'Networking', basePriority:0.88, rank:5, notes:'Managed API front-door for HTTP/REST/WebSocket APIs'},

  {id:'aws.iam', name:'AWS IAM', category:'Security Identity', basePriority:0.99, rank:1, notes:'Identity and access control baseline for all services'},
  {id:'aws.kms', name:'AWS KMS', category:'Security Identity', basePriority:0.90, rank:2, notes:'Key management and encryption integration'},
  {id:'aws.secretsmanager', name:'AWS Secrets Manager', category:'Security Identity', basePriority:0.79, rank:3, notes:'Secret lifecycle management and rotation'},

  {id:'aws.cloudwatch', name:'Amazon CloudWatch', category:'Observability Governance', basePriority:0.97, rank:1, notes:'Metrics, logs, alarms, and dashboards'},
  {id:'aws.cloudtrail', name:'AWS CloudTrail', category:'Observability Governance', basePriority:0.86, rank:2, notes:'Audit trail and API governance'},

  {id:'aws.athena', name:'Amazon Athena', category:'Analytics Data Processing', basePriority:0.66, rank:1, notes:'Serverless SQL query over S3 data lake'},
  {id:'aws.glue', name:'AWS Glue', category:'Analytics Data Processing', basePriority:0.61, rank:2, notes:'ETL, data integration, and Data Catalog'},
  {id:'aws.redshift', name:'Amazon Redshift', category:'Analytics Data Processing', basePriority:0.52, rank:3, notes:'Cloud data warehouse for BI/analytics'},
  {id:'aws.emr', name:'Amazon EMR', category:'Analytics Data Processing', basePriority:0.39, rank:4, notes:'Managed big data frameworks (Spark/Hadoop)'}
] AS serviceData
MATCH (c:ServiceCategory {name: serviceData.category})
MERGE (s:Service {id: serviceData.id})
SET s.name = serviceData.name,
    s.basePriority = serviceData.basePriority,
    s.rankInCategory = serviceData.rank,
    s.notes = serviceData.notes
MERGE (aws)-[:HAS_SERVICE]->(s)
MERGE (s)-[:IN_CATEGORY]->(c);

UNWIND [
  // Amazon S3 variants
  {id:'aws.s3.standard', serviceId:'aws.s3', name:'S3 Standard', priority:0.97, dimension:'storageClass', config:'high durability, low latency, multi-AZ'},
  {id:'aws.s3.intelligent_tiering', serviceId:'aws.s3', name:'S3 Intelligent-Tiering', priority:0.90, dimension:'storageClass', config:'automatic cost optimization across access tiers'},
  {id:'aws.s3.standard_ia', serviceId:'aws.s3', name:'S3 Standard-IA', priority:0.72, dimension:'storageClass', config:'infrequent access, millisecond retrieval'},
  {id:'aws.s3.onezone_ia', serviceId:'aws.s3', name:'S3 One Zone-IA', priority:0.48, dimension:'storageClass', config:'single AZ, lower cost infrequent access'},
  {id:'aws.s3.glacier_ir', serviceId:'aws.s3', name:'S3 Glacier Instant Retrieval', priority:0.36, dimension:'storageClass', config:'archive with instant retrieval'},
  {id:'aws.s3.glacier_flexible', serviceId:'aws.s3', name:'S3 Glacier Flexible Retrieval', priority:0.28, dimension:'storageClass', config:'archive with minutes to hours retrieval'},
  {id:'aws.s3.glacier_deep_archive', serviceId:'aws.s3', name:'S3 Glacier Deep Archive', priority:0.19, dimension:'storageClass', config:'lowest storage cost, long retrieval times'},

  // Amazon EBS variants
  {id:'aws.ebs.gp3', serviceId:'aws.ebs', name:'EBS gp3', priority:0.79, dimension:'volumeType', config:'general purpose SSD, baseline for most VM workloads'},
  {id:'aws.ebs.gp2', serviceId:'aws.ebs', name:'EBS gp2', priority:0.33, dimension:'volumeType', config:'legacy general purpose SSD'},
  {id:'aws.ebs.io2', serviceId:'aws.ebs', name:'EBS io2', priority:0.25, dimension:'volumeType', config:'provisioned IOPS SSD for critical low-latency DB'},
  {id:'aws.ebs.st1', serviceId:'aws.ebs', name:'EBS st1', priority:0.16, dimension:'volumeType', config:'throughput optimized HDD'},
  {id:'aws.ebs.sc1', serviceId:'aws.ebs', name:'EBS sc1', priority:0.11, dimension:'volumeType', config:'cold HDD for infrequent workloads'},

  // Amazon EFS variants
  {id:'aws.efs.standard', serviceId:'aws.efs', name:'EFS Standard', priority:0.49, dimension:'storageClass', config:'regional, resilient multi-AZ shared file storage'},
  {id:'aws.efs.ia', serviceId:'aws.efs', name:'EFS Infrequent Access', priority:0.31, dimension:'storageClass', config:'lower-cost class for infrequently accessed files'},
  {id:'aws.efs.one_zone', serviceId:'aws.efs', name:'EFS One Zone', priority:0.27, dimension:'deployment', config:'single AZ shared file system at lower cost'},

  // Amazon EC2 variants
  {id:'aws.ec2.t4g', serviceId:'aws.ec2', name:'EC2 t4g (Graviton burstable)', priority:0.62, dimension:'instanceFamily', config:'cost-efficient ARM burst workloads'},
  {id:'aws.ec2.t3', serviceId:'aws.ec2', name:'EC2 t3 (x86 burstable)', priority:0.58, dimension:'instanceFamily', config:'balanced burst workloads'},
  {id:'aws.ec2.m7i', serviceId:'aws.ec2', name:'EC2 m7i (general purpose)', priority:0.53, dimension:'instanceFamily', config:'general workloads with predictable demand'},
  {id:'aws.ec2.c7g', serviceId:'aws.ec2', name:'EC2 c7g (compute optimized)', priority:0.45, dimension:'instanceFamily', config:'compute-heavy services on Graviton'},
  {id:'aws.ec2.r7g', serviceId:'aws.ec2', name:'EC2 r7g (memory optimized)', priority:0.41, dimension:'instanceFamily', config:'memory intensive applications'},

  // AWS Lambda variants
  {id:'aws.lambda.zip', serviceId:'aws.lambda', name:'Lambda Zip Package', priority:0.82, dimension:'packaging', config:'zip-based deployment for most functions'},
  {id:'aws.lambda.container_image', serviceId:'aws.lambda', name:'Lambda Container Image', priority:0.56, dimension:'packaging', config:'container-based function package up to 10 GB'},
  {id:'aws.lambda.arm64', serviceId:'aws.lambda', name:'Lambda arm64 Runtime', priority:0.49, dimension:'architecture', config:'better price/perf for compatible runtimes'},
  {id:'aws.lambda.x86_64', serviceId:'aws.lambda', name:'Lambda x86_64 Runtime', priority:0.51, dimension:'architecture', config:'default broad compatibility runtime architecture'},

  // Amazon ECS variants
  {id:'aws.ecs.fargate', serviceId:'aws.ecs', name:'ECS on Fargate', priority:0.85, dimension:'launchType', config:'serverless containers, no node management'},
  {id:'aws.ecs.ec2', serviceId:'aws.ecs', name:'ECS on EC2', priority:0.41, dimension:'launchType', config:'self-managed cluster nodes with deeper control'},

  // Amazon EKS variants
  {id:'aws.eks.managed_node_groups', serviceId:'aws.eks', name:'EKS Managed Node Groups', priority:0.58, dimension:'computeModel', config:'managed worker node lifecycle in EKS'},
  {id:'aws.eks.fargate_profiles', serviceId:'aws.eks', name:'EKS Fargate Profiles', priority:0.37, dimension:'computeModel', config:'serverless pods for selected namespaces'},

  // Amazon RDS variants
  {id:'aws.rds.postgresql', serviceId:'aws.rds', name:'RDS for PostgreSQL', priority:0.93, dimension:'engine', config:'managed PostgreSQL'},
  {id:'aws.rds.mysql', serviceId:'aws.rds', name:'RDS for MySQL', priority:0.82, dimension:'engine', config:'managed MySQL'},
  {id:'aws.rds.mariadb', serviceId:'aws.rds', name:'RDS for MariaDB', priority:0.35, dimension:'engine', config:'managed MariaDB'},
  {id:'aws.rds.sqlserver', serviceId:'aws.rds', name:'RDS for SQL Server', priority:0.31, dimension:'engine', config:'managed Microsoft SQL Server'},
  {id:'aws.rds.oracle', serviceId:'aws.rds', name:'RDS for Oracle', priority:0.18, dimension:'engine', config:'managed Oracle database'},

  // Amazon Aurora variants
  {id:'aws.aurora.postgresql', serviceId:'aws.aurora', name:'Aurora PostgreSQL-Compatible', priority:0.71, dimension:'engine', config:'Aurora with PostgreSQL compatibility'},
  {id:'aws.aurora.mysql', serviceId:'aws.aurora', name:'Aurora MySQL-Compatible', priority:0.52, dimension:'engine', config:'Aurora with MySQL compatibility'},
  {id:'aws.aurora.serverless_v2', serviceId:'aws.aurora', name:'Aurora Serverless v2', priority:0.44, dimension:'capacityModel', config:'autoscaling ACUs for variable demand'},

  // DynamoDB variants
  {id:'aws.dynamodb.on_demand', serviceId:'aws.dynamodb', name:'DynamoDB On-Demand Capacity', priority:0.88, dimension:'capacityMode', config:'pay-per-request throughput model'},
  {id:'aws.dynamodb.provisioned', serviceId:'aws.dynamodb', name:'DynamoDB Provisioned Capacity', priority:0.62, dimension:'capacityMode', config:'provisioned throughput with auto scaling'},
  {id:'aws.dynamodb.global_tables', serviceId:'aws.dynamodb', name:'DynamoDB Global Tables', priority:0.41, dimension:'topology', config:'multi-region active-active replication'},

  // ElastiCache variants
  {id:'aws.elasticache.redis', serviceId:'aws.elasticache', name:'ElastiCache for Redis', priority:0.74, dimension:'engine', config:'rich data structures, persistence, pub/sub'},
  {id:'aws.elasticache.memcached', serviceId:'aws.elasticache', name:'ElastiCache for Memcached', priority:0.26, dimension:'engine', config:'simple distributed caching'},

  // Messaging variants
  {id:'aws.sqs.standard', serviceId:'aws.sqs', name:'SQS Standard Queue', priority:0.87, dimension:'queueType', config:'best-effort ordering, at-least-once delivery'},
  {id:'aws.sqs.fifo', serviceId:'aws.sqs', name:'SQS FIFO Queue', priority:0.43, dimension:'queueType', config:'exactly-once processing with ordered delivery'},
  {id:'aws.sns.standard', serviceId:'aws.sns', name:'SNS Standard Topic', priority:0.80, dimension:'topicType', config:'high throughput pub/sub topic'},
  {id:'aws.sns.fifo', serviceId:'aws.sns', name:'SNS FIFO Topic', priority:0.22, dimension:'topicType', config:'ordered and deduplicated pub/sub'},
  {id:'aws.eventbridge.default_bus', serviceId:'aws.eventbridge', name:'EventBridge Default Bus', priority:0.63, dimension:'eventBus', config:'account-level default event bus routing'},
  {id:'aws.eventbridge.custom_bus', serviceId:'aws.eventbridge', name:'EventBridge Custom Bus', priority:0.51, dimension:'eventBus', config:'domain-specific event bus isolation'},

  // Streaming variants
  {id:'aws.kinesis.data_streams_on_demand', serviceId:'aws.kinesis', name:'Kinesis Data Streams On-Demand', priority:0.45, dimension:'streamMode', config:'capacity auto-scaling streams'},
  {id:'aws.kinesis.data_streams_provisioned', serviceId:'aws.kinesis', name:'Kinesis Data Streams Provisioned', priority:0.29, dimension:'streamMode', config:'explicit shard-based throughput control'},
  {id:'aws.kinesis.firehose', serviceId:'aws.kinesis', name:'Kinesis Data Firehose', priority:0.38, dimension:'deliveryMode', config:'fully managed stream delivery to data stores'},
  {id:'aws.msk.serverless', serviceId:'aws.msk', name:'MSK Serverless', priority:0.36, dimension:'clusterMode', config:'Kafka without broker capacity planning'},
  {id:'aws.msk.provisioned', serviceId:'aws.msk', name:'MSK Provisioned', priority:0.24, dimension:'clusterMode', config:'dedicated Kafka brokers with full tuning control'},

  // Networking variants
  {id:'aws.alb.internet_facing', serviceId:'aws.alb', name:'ALB Internet-Facing', priority:0.61, dimension:'scheme', config:'public ingress for web applications'},
  {id:'aws.alb.internal', serviceId:'aws.alb', name:'ALB Internal', priority:0.39, dimension:'scheme', config:'private traffic inside VPC'},
  {id:'aws.nlb.internet_facing', serviceId:'aws.nlb', name:'NLB Internet-Facing', priority:0.34, dimension:'scheme', config:'public low-latency TCP/UDP exposure'},
  {id:'aws.nlb.internal', serviceId:'aws.nlb', name:'NLB Internal', priority:0.31, dimension:'scheme', config:'private network load balancing'},
  {id:'aws.route53.public_hosted_zone', serviceId:'aws.route53', name:'Route 53 Public Hosted Zone', priority:0.70, dimension:'zoneType', config:'public DNS for internet domains'},
  {id:'aws.route53.private_hosted_zone', serviceId:'aws.route53', name:'Route 53 Private Hosted Zone', priority:0.46, dimension:'zoneType', config:'private DNS inside VPC'},
  {id:'aws.route53.resolver_endpoints', serviceId:'aws.route53', name:'Route 53 Resolver Endpoints', priority:0.33, dimension:'hybridDns', config:'hybrid DNS forwarding between on-prem and AWS'},
  {id:'aws.apigateway.http_api', serviceId:'aws.apigateway', name:'API Gateway HTTP API', priority:0.69, dimension:'apiType', config:'lower-cost HTTP API management'},
  {id:'aws.apigateway.rest_api', serviceId:'aws.apigateway', name:'API Gateway REST API', priority:0.54, dimension:'apiType', config:'full-featured REST API capabilities'},
  {id:'aws.apigateway.websocket_api', serviceId:'aws.apigateway', name:'API Gateway WebSocket API', priority:0.21, dimension:'apiType', config:'stateful bidirectional socket APIs'},

  // Security variants
  {id:'aws.iam.roles', serviceId:'aws.iam', name:'IAM Roles', priority:0.94, dimension:'principalPattern', config:'temporary credentials for workloads'},
  {id:'aws.iam.policies', serviceId:'aws.iam', name:'IAM Policies', priority:0.92, dimension:'controlModel', config:'permission model via managed/inline policies'},
  {id:'aws.kms.customer_managed_keys', serviceId:'aws.kms', name:'KMS Customer Managed Keys', priority:0.68, dimension:'keyType', config:'customer-controlled keys with rotation/audit'},
  {id:'aws.kms.aws_managed_keys', serviceId:'aws.kms', name:'KMS AWS Managed Keys', priority:0.45, dimension:'keyType', config:'AWS-managed service keys'},
  {id:'aws.secretsmanager.rotation_enabled', serviceId:'aws.secretsmanager', name:'Secrets Manager Rotation Enabled', priority:0.56, dimension:'rotation', config:'automatic secret rotation with Lambda'},
  {id:'aws.secretsmanager.rotation_disabled', serviceId:'aws.secretsmanager', name:'Secrets Manager Rotation Disabled', priority:0.23, dimension:'rotation', config:'manual secret management model'},

  // Observability variants
  {id:'aws.cloudwatch.logs', serviceId:'aws.cloudwatch', name:'CloudWatch Logs', priority:0.88, dimension:'capability', config:'centralized logs and retention controls'},
  {id:'aws.cloudwatch.metrics_alarms', serviceId:'aws.cloudwatch', name:'CloudWatch Metrics and Alarms', priority:0.85, dimension:'capability', config:'metric-driven alerting'},
  {id:'aws.cloudwatch.dashboards', serviceId:'aws.cloudwatch', name:'CloudWatch Dashboards', priority:0.52, dimension:'capability', config:'visual operational dashboards'},
  {id:'aws.cloudtrail.management_events', serviceId:'aws.cloudtrail', name:'CloudTrail Management Events', priority:0.82, dimension:'eventType', config:'control plane API auditing'},
  {id:'aws.cloudtrail.data_events', serviceId:'aws.cloudtrail', name:'CloudTrail Data Events', priority:0.49, dimension:'eventType', config:'S3/Lambda/object-level API auditing'},

  // Analytics variants
  {id:'aws.athena.sql', serviceId:'aws.athena', name:'Athena SQL Query', priority:0.66, dimension:'workload', config:'ad hoc interactive SQL on S3 datasets'},
  {id:'aws.glue.etl_jobs', serviceId:'aws.glue', name:'Glue ETL Jobs', priority:0.57, dimension:'workload', config:'serverless ETL pipelines'},
  {id:'aws.glue.data_catalog', serviceId:'aws.glue', name:'Glue Data Catalog', priority:0.48, dimension:'workload', config:'central metadata catalog for analytics'},
  {id:'aws.redshift.serverless', serviceId:'aws.redshift', name:'Redshift Serverless', priority:0.41, dimension:'deploymentModel', config:'on-demand analytics warehouse'},
  {id:'aws.redshift.provisioned', serviceId:'aws.redshift', name:'Redshift Provisioned', priority:0.31, dimension:'deploymentModel', config:'provisioned clusters for steady workloads'},
  {id:'aws.emr.serverless', serviceId:'aws.emr', name:'EMR Serverless', priority:0.25, dimension:'deploymentModel', config:'Spark jobs without cluster management'},
  {id:'aws.emr.ec2', serviceId:'aws.emr', name:'EMR on EC2', priority:0.18, dimension:'deploymentModel', config:'full control over big data cluster nodes'}
] AS variantData
MATCH (s:Service {id: variantData.serviceId})
MERGE (v:ServiceVariant {id: variantData.id})
SET v.name = variantData.name,
    v.priority = variantData.priority,
    v.dimension = variantData.dimension,
    v.configuration = variantData.config
MERGE (s)-[:HAS_VARIANT]->(v);

UNWIND [
  {a:'aws.s3', b:'aws.efs', relation:'alternative_for_shared_access_patterns'},
  {a:'aws.rds', b:'aws.aurora', relation:'alternative_for_relational_engine'},
  {a:'aws.ecs', b:'aws.eks', relation:'alternative_for_container_orchestration'},
  {a:'aws.sqs', b:'aws.eventbridge', relation:'alternative_for_async_integration'},
  {a:'aws.kinesis', b:'aws.msk', relation:'alternative_for_streaming'}
] AS relData
MATCH (a:Service {id: relData.a})
MATCH (b:Service {id: relData.b})
MERGE (a)-[r:ALTERNATIVE_TO]->(b)
SET r.context = relData.relation;
