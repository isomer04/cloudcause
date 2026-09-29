"""AWS investigation playbooks.

Each entry maps a deterministic cost-increase candidate to the evidence that
confirms or rejects one known AWS waste pattern, and to the versioned billing
rule that explains the charge.
"""

from __future__ import annotations

from cloudcause_worker_core import PlaybookSpec

AWS_PLAYBOOKS: tuple[PlaybookSpec, ...] = (
    PlaybookSpec(
        category="nat_gateway_misroute",
        root_cause=(
            "NAT Gateway {resource} in {region} started processing much more traffic from "
            "{spike_date}, which points at a route change sending S3 or DynamoDB traffic through "
            "the gateway instead of a VPC endpoint"
        ),
        recommendation=(
            "Review the route table change, then restore the gateway VPC endpoint so this traffic "
            "bypasses the NAT Gateway. A human must approve any network change."
        ),
        risk="medium",
        service_patterns=("Virtual Private Cloud", "NAT Gateway"),
        sku_patterns=("NatGateway",),
        metric_names=("BytesOutToDestination", "BytesInFromDestination", "ActiveConnectionCount"),
        audit_event_patterns=("ReplaceRoute", "CreateRoute", "DeleteVpcEndpoint", "ModifyVpcEndpoint"),
        priority=10,
        checks=("compare_bytes_processed", "inspect_route_changes", "check_for_vpc_endpoint"),
    ),
    PlaybookSpec(
        category="ai_inference",
        root_cause=(
            "Model inference usage on {service} grew from {spike_date}, adding "
            "{increase} of token or invocation charges"
        ),
        recommendation=(
            "Confirm the new inference volume is intended, check for retry storms, and consider a "
            "smaller model, shorter prompts, caching, or provisioned throughput."
        ),
        risk="medium",
        service_patterns=("Bedrock", "SageMaker"),
        metric_names=("Invocations", "InputTokenCount", "OutputTokenCount", "InvocationClientErrors"),
        priority=15,
    ),
    PlaybookSpec(
        category="pricing_change",
        root_cause=(
            "Cost for {key} rose while usage stayed flat, so the effective rate changed rather "
            "than the workload"
        ),
        recommendation=(
            "Compare the effective unit cost against a dated price-list snapshot and check "
            "commitment coverage before treating this as a usage problem."
        ),
        risk="low",
        requires_rate_change=True,
        priority=15,
    ),
    PlaybookSpec(
        category="idle_compute",
        root_cause=(
            "EC2 instance {resource} in {region} is running with almost no CPU or network "
            "activity, so it looks forgotten rather than busy"
        ),
        recommendation=(
            "Confirm ownership, then have a human stop or terminate the instance. CloudCause "
            "does not change resources."
        ),
        risk="low",
        service_patterns=("Elastic Compute Cloud", "EC2"),
        metric_names=("CPUUtilization", "NetworkOut", "NetworkIn"),
        audit_event_patterns=("RunInstances", "StartInstances"),
        low_utilization_metrics=("CPUUtilization",),
        low_utilization_threshold=5.0,
        priority=20,
    ),
    PlaybookSpec(
        category="unattached_storage",
        root_cause=(
            "EBS volume or snapshot {resource} keeps billing provisioned capacity with no read or "
            "write activity, which indicates it is detached or unused"
        ),
        recommendation=(
            "Verify no workload needs the volume, snapshot it if required, then have a human "
            "delete it."
        ),
        risk="medium",
        service_patterns=("Elastic Block Store", "EBS"),
        metric_names=("VolumeReadOps", "VolumeWriteOps", "VolumeIdleTime"),
        low_utilization_metrics=("VolumeReadOps", "VolumeWriteOps"),
        low_utilization_threshold=1.0,
        priority=20,
    ),
    PlaybookSpec(
        category="idle_database",
        root_cause=(
            "RDS instance {resource} bills continuously but shows almost no connections, so it "
            "appears idle"
        ),
        recommendation=(
            "Confirm ownership and retention needs, then have a human snapshot and stop the "
            "instance."
        ),
        risk="medium",
        service_patterns=("Relational Database Service", "RDS", "Aurora"),
        metric_names=("DatabaseConnections", "CPUUtilization", "ReadIOPS"),
        low_utilization_metrics=("DatabaseConnections",),
        low_utilization_threshold=1.0,
        priority=20,
    ),
    PlaybookSpec(
        category="kubernetes_autoscaling",
        root_cause=(
            "EKS node capacity grew from {spike_date} and stayed high, which points at an "
            "autoscaling loop caused by unschedulable pods rather than real demand"
        ),
        recommendation=(
            "Check pod resource requests, autoscaler events, and pending pods; correct the "
            "requests or the scaling limits."
        ),
        risk="medium",
        service_patterns=("Elastic Kubernetes Service", "EKS"),
        metric_names=("cluster_node_count", "node_count", "pending_pods"),
        audit_event_patterns=("UpdateNodegroupConfig", "CreateNodegroup"),
        priority=20,
    ),
    PlaybookSpec(
        category="cross_region_transfer",
        root_cause=(
            "Data transfer charges on {key} grew from {spike_date}, which indicates traffic "
            "crossing a region or availability-zone boundary"
        ),
        recommendation=(
            "Identify the source and destination of the transfer and move the consumer, replica, "
            "or backup target into the same region or zone."
        ),
        risk="medium",
        service_patterns=("Data Transfer",),
        metric_names=("BytesOut", "NetworkOut"),
        priority=25,
    ),
    PlaybookSpec(
        category="commitment_change",
        root_cause=(
            "Commitment coverage for {key} changed, so on-demand rates now apply to usage that "
            "was previously discounted"
        ),
        recommendation=(
            "Check Savings Plan and Reserved Instance expiry dates and coverage before treating "
            "this as a usage increase."
        ),
        risk="low",
        service_patterns=("Savings Plans", "Reserved Instances"),
        requires_quantity_growth=False,
        priority=20,
    ),
    PlaybookSpec(
        category="untagged_resources",
        root_cause=(
            "Spend on {key} grew but carries no ownership tag, so the increase cannot be "
            "attributed to a team"
        ),
        recommendation=(
            "Apply and activate cost allocation tags for this resource, then re-run the "
            "investigation to attribute the spend."
        ),
        risk="low",
        requires_missing_owner=True,
        priority=60,
        max_confidence=0.7,
    ),
)
