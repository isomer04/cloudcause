"""Service name to FOCUS ServiceCategory mapping.

Deliberately small and explicit. Unknown services fall back to ``Other`` rather
than being guessed by a model.
"""

from __future__ import annotations

SERVICE_CATEGORY_KEYWORDS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("nat gateway", "virtual private cloud", "vpc", "cloud nat", "load balanc", "cdn", "front door",
      "data transfer", "networking", "bandwidth", "cloud interconnect", "expressroute"), "Networking"),
    (("ec2", "compute engine", "virtual machines", "app service", "lambda", "functions",
      "cloud run", "fargate", "container", "kubernetes", "aks", "eks", "gke"), "Compute"),
    (("s3", "simple storage", "ebs", "elastic block", "blob", "cloud storage", "filestore",
      "snapshot", "managed disks", "efs", "backup"), "Storage"),
    (("rds", "aurora", "dynamodb", "cosmos", "sql database", "cloud sql", "bigtable",
      "spanner", "redis", "cache", "database"), "Databases"),
    (("bedrock", "sagemaker", "vertex ai", "openai", "cognitive", "translation", "ai platform",
      "machine learning"), "AI and Machine Learning"),
    (("bigquery", "athena", "synapse", "dataflow", "kinesis", "event hubs", "pub/sub",
      "analytics"), "Analytics"),
    (("cloudwatch", "monitor", "logging", "cloudtrail", "config", "advisor", "trusted advisor",
      "management"), "Management and Governance"),
    (("kms", "secrets manager", "key vault", "iam", "guardduty", "defender", "security"), "Security"),
)


def service_category(service_name: str) -> str:
    lowered = service_name.lower()
    for keywords, category in SERVICE_CATEGORY_KEYWORDS:
        if any(keyword in lowered for keyword in keywords):
            return category
    return "Other"
