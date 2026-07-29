"""Azure investigation playbooks."""

from __future__ import annotations

from cloudcause_worker_core import PlaybookSpec

AZURE_PLAYBOOKS: tuple[PlaybookSpec, ...] = (
    PlaybookSpec(
        category="functions_retry_loop",
        root_cause=(
            "Function app {resource} started executing far more often from {spike_date} with a "
            "high failure rate, which is the signature of a retry loop after a deployment rather "
            "than new useful work"
        ),
        recommendation=(
            "Inspect the failing function's retry policy, add a dead-letter path, and fix the "
            "failing dependency. A human should decide whether to disable the trigger while the "
            "fix is prepared."
        ),
        risk="medium",
        service_patterns=("Azure Functions", "Functions"),
        metric_names=("FunctionExecutionCount", "FunctionErrors", "FunctionExecutionUnits"),
        audit_event_patterns=("deploy", "publish", "restart", "Microsoft.Web/sites"),
        priority=10,
        checks=("compare_execution_count", "compare_failure_rate", "correlate_with_deployment_event"),
    ),
    PlaybookSpec(
        category="ai_inference",
        root_cause=(
            "Model inference usage on {service} grew from {spike_date}, adding {increase} of token "
            "or capacity charges"
        ),
        recommendation=(
            "Confirm the new volume is intended, check for retry storms, and review model choice, "
            "prompt length, and provisioned throughput."
        ),
        risk="medium",
        service_patterns=("Cognitive Services", "Azure OpenAI"),
        metric_names=("ProcessedPromptTokens", "GeneratedTokens", "TokenTransaction"),
        priority=15,
    ),
    PlaybookSpec(
        category="pricing_change",
        root_cause=(
            "Cost for {key} rose while usage stayed flat, so the meter rate or the discount "
            "coverage changed rather than the workload"
        ),
        recommendation=(
            "Compare the effective unit cost against a dated Retail Prices snapshot and check "
            "reservation coverage before treating this as a usage problem."
        ),
        risk="low",
        requires_rate_change=True,
        priority=15,
    ),
    PlaybookSpec(
        category="idle_compute",
        root_cause=(
            "Virtual machine {resource} in {region} is running with almost no CPU or network "
            "activity, so it looks forgotten rather than busy"
        ),
        recommendation=(
            "Confirm ownership, then have a human deallocate or delete the VM. Stopping without "
            "deallocating keeps billing compute."
        ),
        risk="low",
        service_patterns=("Virtual Machines",),
        metric_names=("Percentage CPU", "Network In Total", "Network Out Total"),
        low_utilization_metrics=("Percentage CPU",),
        low_utilization_threshold=5.0,
        priority=20,
    ),
    PlaybookSpec(
        category="unattached_storage",
        root_cause=(
            "Managed disk or snapshot {resource} keeps billing its provisioned tier with no disk "
            "activity, which indicates it is unattached or unused"
        ),
        recommendation=(
            "Verify no workload needs the disk, snapshot it if required, then have a human delete "
            "it."
        ),
        risk="medium",
        service_patterns=("Storage", "Managed Disks"),
        metric_names=("Disk Read Operations/Sec", "Disk Write Operations/Sec", "Composite Disk Read Operations/sec"),
        low_utilization_metrics=("Disk Read Operations/Sec", "Composite Disk Read Operations/sec"),
        low_utilization_threshold=1.0,
        priority=20,
    ),
    PlaybookSpec(
        category="idle_database",
        root_cause=(
            "SQL database {resource} bills provisioned compute continuously but shows almost no "
            "connections, so it appears idle"
        ),
        recommendation=(
            "Confirm ownership, then consider the serverless tier with auto-pause, or have a human "
            "export and delete the database."
        ),
        risk="medium",
        service_patterns=("SQL Database", "Database for PostgreSQL", "Database for MySQL"),
        metric_names=("connection_successful", "cpu_percent", "dtu_consumption_percent"),
        low_utilization_metrics=("connection_successful",),
        low_utilization_threshold=1.0,
        priority=20,
    ),
    PlaybookSpec(
        category="kubernetes_autoscaling",
        root_cause=(
            "AKS node capacity grew from {spike_date} and stayed high, which points at an "
            "autoscaling loop caused by unschedulable pods rather than real demand"
        ),
        recommendation=(
            "Check pod resource requests, autoscaler events, and pending pods; correct the "
            "requests or the node pool limits."
        ),
        risk="medium",
        service_patterns=("Azure Kubernetes Service", "AKS"),
        metric_names=("node_count", "kube_node_status_allocatable_cpu_cores", "pending_pods"),
        audit_event_patterns=("Microsoft.ContainerService", "agentPools"),
        priority=20,
    ),
    PlaybookSpec(
        category="cross_region_transfer",
        root_cause=(
            "Bandwidth charges on {key} grew from {spike_date}, which indicates traffic crossing a "
            "region or zone boundary, or geo-redundant replication"
        ),
        recommendation=(
            "Identify the source and destination of the traffic and colocate the consumer, or "
            "review the storage replication setting."
        ),
        risk="medium",
        service_patterns=("Bandwidth",),
        metric_names=("Egress", "Ingress"),
        priority=25,
    ),
    PlaybookSpec(
        category="commitment_change",
        root_cause=(
            "Reservation or savings plan coverage for {key} changed, so pay-as-you-go rates now "
            "apply to usage that was previously discounted"
        ),
        recommendation=(
            "Check reservation expiry and coverage, and compare actual against amortized cost "
            "before treating this as a usage increase."
        ),
        risk="low",
        service_patterns=("Reservation", "Savings Plan", "Reserved"),
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
            "Apply an owner tag, and consider an Azure Policy that enforces tagging, then re-run "
            "the investigation."
        ),
        risk="low",
        requires_missing_owner=True,
        priority=60,
        max_confidence=0.7,
    ),
)
