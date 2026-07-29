"""GCP investigation playbooks. The ADK service is also the GCP specialist."""

from __future__ import annotations

from cloudcause_worker_core import PlaybookSpec

GCP_PLAYBOOKS: tuple[PlaybookSpec, ...] = (
    PlaybookSpec(
        category="api_key_abuse",
        root_cause=(
            "Request volume against {service} jumped from {spike_date} with callers from new "
            "source locations and no matching deployment, which indicates an exposed API key "
            "rather than legitimate growth"
        ),
        recommendation=(
            "Treat the key as compromised: a human should restrict it by API and referrer, rotate "
            "it, and review the audit log for the full caller list. CloudCause does not rotate "
            "keys or change IAM."
        ),
        risk="high",
        service_patterns=("Cloud Translation", "Cloud Vision", "Natural Language", "Speech-to-Text"),
        metric_names=("serviceruntime.googleapis.com/api/request_count", "request_count", "characters"),
        audit_event_patterns=("Translate", "annotate", "recognize", "SetIamPolicy"),
        priority=5,
        checks=("compare_request_count", "group_audit_logs_by_caller_ip", "check_key_restrictions"),
    ),
    PlaybookSpec(
        category="ai_inference",
        root_cause=(
            "Vertex AI prediction usage grew from {spike_date}, adding {increase} of token or "
            "endpoint charges"
        ),
        recommendation=(
            "Confirm the new inference volume is intended, check for retry storms, and undeploy "
            "endpoints that no longer serve traffic."
        ),
        risk="medium",
        service_patterns=("Vertex AI",),
        metric_names=("prediction/online/prediction_count", "token_count", "request_count"),
        priority=15,
    ),
    PlaybookSpec(
        category="pricing_change",
        root_cause=(
            "Cost for {key} rose while usage stayed flat, so the SKU rate or credit coverage "
            "changed rather than the workload"
        ),
        recommendation=(
            "Compare the effective unit cost against a dated Cloud Billing Catalog snapshot and "
            "check committed use credits before treating this as a usage problem."
        ),
        risk="low",
        requires_rate_change=True,
        priority=15,
    ),
    PlaybookSpec(
        category="idle_compute",
        root_cause=(
            "Compute Engine instance {resource} in {region} is running with almost no CPU or "
            "network activity, so it looks forgotten rather than busy"
        ),
        recommendation=(
            "Confirm ownership from labels and the insert event, then have a human stop or delete "
            "the instance."
        ),
        risk="low",
        service_patterns=("Compute Engine",),
        metric_names=(
            "compute.googleapis.com/instance/cpu/utilization",
            "instance/network/sent_bytes_count",
        ),
        audit_event_patterns=("compute.instances.insert", "compute.instances.start"),
        low_utilization_metrics=("compute.googleapis.com/instance/cpu/utilization",),
        low_utilization_threshold=5.0,
        priority=20,
    ),
    PlaybookSpec(
        category="unattached_storage",
        root_cause=(
            "Persistent disk or snapshot {resource} keeps billing provisioned capacity with no "
            "read or write activity, which indicates it is unattached or unused"
        ),
        recommendation=(
            "Verify no workload needs the disk, snapshot it if required, then have a human delete "
            "it."
        ),
        risk="medium",
        service_patterns=("Persistent Disk", "Compute Engine Storage"),
        metric_names=("instance/disk/read_ops_count", "instance/disk/write_ops_count"),
        low_utilization_metrics=("instance/disk/read_ops_count", "instance/disk/write_ops_count"),
        low_utilization_threshold=1.0,
        priority=20,
    ),
    PlaybookSpec(
        category="idle_database",
        root_cause=(
            "Cloud SQL instance {resource} bills continuously but shows almost no connections, so "
            "it appears idle"
        ),
        recommendation=(
            "Confirm ownership, export a backup if needed, then have a human stop or delete the "
            "instance."
        ),
        risk="medium",
        service_patterns=("Cloud SQL",),
        metric_names=("database/network/connections", "database/cpu/utilization"),
        low_utilization_metrics=("database/network/connections",),
        low_utilization_threshold=1.0,
        priority=20,
    ),
    PlaybookSpec(
        category="kubernetes_autoscaling",
        root_cause=(
            "GKE node capacity grew from {spike_date} and stayed high, which points at an "
            "autoscaling loop caused by unschedulable pods rather than real demand"
        ),
        recommendation=(
            "Check pod resource requests, autoscaler events, and pending pods; correct the "
            "requests or the node pool limits."
        ),
        risk="medium",
        service_patterns=("Kubernetes Engine",),
        metric_names=("node_count", "container/cpu/request_cores", "pending_pods"),
        audit_event_patterns=("container.clusters.update", "NodePool"),
        priority=20,
    ),
    PlaybookSpec(
        category="cross_region_transfer",
        root_cause=(
            "Network egress on {key} grew from {spike_date}, which indicates traffic crossing a "
            "region boundary or leaving Google's network"
        ),
        recommendation=(
            "Identify the source and destination of the traffic, colocate the consumer, and review "
            "the network service tier."
        ),
        risk="medium",
        service_patterns=("Networking", "Cloud NAT"),
        metric_names=("instance/network/sent_bytes_count", "nat/sent_bytes_count"),
        priority=25,
    ),
    PlaybookSpec(
        category="commitment_change",
        root_cause=(
            "Committed use discount coverage for {key} changed, so list rates now apply to usage "
            "that was previously discounted"
        ),
        recommendation=(
            "Check commitment expiry and the covered SKU mix, and add credits when computing net "
            "cost, before treating this as a usage increase."
        ),
        risk="low",
        service_patterns=("Committed Use", "Commitments"),
        requires_quantity_growth=False,
        priority=20,
    ),
    PlaybookSpec(
        category="untagged_resources",
        root_cause=(
            "Spend on {key} grew but carries no ownership label, so the increase cannot be "
            "attributed to a team"
        ),
        recommendation=(
            "Apply owner labels before the next billing export; labels are not backfilled onto "
            "past usage."
        ),
        risk="low",
        requires_missing_owner=True,
        priority=60,
        max_confidence=0.7,
    ),
)
