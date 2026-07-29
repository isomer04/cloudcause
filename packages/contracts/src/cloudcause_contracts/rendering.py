"""Report rendering. Pure functions over the report contract.

The UI never computes any of this: the frontend gets
rendered text and structured fields from the gateway.
"""

from __future__ import annotations

from .report import InvestigationReport


def _money(value: float, currency: str = "USD") -> str:
    return f"{value:,.2f} {currency}"


def _percent(value: float | None) -> str:
    return "new spend" if value is None else f"{value:+.1f}%"


#: A downloaded report is opened in spreadsheets. A cell that starts with one of
#: these is executed as a formula, so an uploaded resource name could become one.
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _cell(text: str) -> str:
    """Make one table cell safe for both Markdown and a spreadsheet import."""

    flattened = str(text).replace("|", "\\|").replace("\r", " ").replace("\n", " ").strip()
    if flattened.startswith(_FORMULA_PREFIXES):
        return "'" + flattened
    return flattened


def report_to_markdown(report: InvestigationReport) -> str:
    lines: list[str] = []
    lines.append(f"# CloudCause investigation {report.investigation_id}")
    lines.append("")
    lines.append(f"**Question:** {report.question}")
    lines.append(
        f"**Current period:** {report.current_period.label()} - "
        f"**baseline:** {report.baseline_period.label()}"
    )
    lines.append(
        f"**Total change:** {_money(report.total_absolute_change, report.currency)} "
        f"({_percent(report.total_percent_change)}) from "
        f"{_money(report.total_baseline_cost, report.currency)} to "
        f"{_money(report.total_current_cost, report.currency)}"
    )
    data_through = report.data_through()
    lines.append(
        f"**Data through:** {data_through.isoformat() if data_through else 'unknown'} - "
        f"**data origin:** {report.data_origin} - **data mode:** {report.data_mode} - "
        f"**agent mode:** {report.agent_mode}"
    )
    if report.data_origin == "upload":
        lines.append("")
        lines.append(
            "> These numbers come from a cost export you supplied. CloudCause measured them and "
            "cited the billing rules that interpret them, but it did not verify them against a "
            "cloud account."
        )
    if report.knowledge:
        knowledge = report.knowledge
        lines.append(
            f"**FOCUS version:** {knowledge.focus_version} - "
            f"**knowledge reviewed:** {knowledge.oldest_review_date} to {knowledge.newest_review_date}"
        )
        if knowledge.rule_ids:
            lines.append(f"**Applied rules:** {', '.join(knowledge.rule_ids)}")
        if knowledge.stale_rule_ids:
            lines.append(f"**Stale rules:** {', '.join(knowledge.stale_rule_ids)}")
    lines.append("")

    if report.summary:
        lines.append("## Summary")
        lines.append("")
        lines.append(report.summary)
        lines.append("")

    lines.append("## Provider status")
    lines.append("")
    lines.append("| Provider | Status | Findings | Data through | Origin |")
    lines.append("| --- | --- | --- | --- | --- |")
    for status in report.provider_statuses:
        through = status.data_through.isoformat() if status.data_through else "unknown"
        lines.append(
            f"| {status.provider} | {status.status} | {status.finding_count} | {through} | "
            f"{status.origin} |"
        )
    lines.append("")

    if report.warnings:
        lines.append("## Warnings")
        lines.append("")
        for warning in report.warnings:
            lines.append(f"- {warning}")
        lines.append("")

    lines.append("## Findings")
    lines.append("")
    if not report.findings:
        lines.append("No material, evidence-backed findings were produced.")
        lines.append("")
    for index, finding in enumerate(report.findings, start=1):
        flag = " (uncertain)" if finding.is_uncertain else ""
        lines.append(f"### {index}. [{finding.provider}] {finding.suspected_root_cause}{flag}")
        lines.append("")
        lines.append(f"- **Finding ID:** {finding.finding_id}")
        lines.append(f"- **Category:** {finding.category}")
        lines.append(f"- **Confidence:** {finding.confidence:.2f}")
        lines.append(
            f"- **Cost increase:** {_money(finding.actual_cost_increase, report.currency)} "
            f"(estimated monthly impact {_money(finding.estimated_monthly_impact, report.currency)})"
        )
        if finding.affected_resources:
            lines.append(f"- **Affected resources:** {', '.join(finding.affected_resources)}")
        lines.append(f"- **Recommendation ({finding.risk} risk):** {finding.recommendation}")
        approval = "yes" if finding.requires_human_approval else "no"
        lines.append(f"- **Human approval required:** {approval}")
        for rule in finding.applied_rules:
            stale = " [stale]" if rule.is_stale else ""
            lines.append(
                f"- **Rule:** {rule.rule_id} (valid from {rule.valid_from}, reviewed {rule.reviewed_at})"
                f"{stale} - {rule.source_url}"
            )
        for warning in finding.warnings:
            lines.append(f"- **Warning:** {warning}")
        lines.append("")
        lines.append("| Evidence ID | Source | Observed | Statement | Value |")
        lines.append("| --- | --- | --- | --- | --- |")
        for item in finding.evidence:
            value = "" if item.numeric_value is None else f"{item.numeric_value:,.2f} {item.numeric_unit or ''}"
            lines.append(
                f"| {item.evidence_id} | {_cell(item.source_type)}:{_cell(item.source_id)} | "
                f"{item.observed_at.isoformat()} | {_cell(item.statement)} | {value.strip()} |"
            )
        lines.append("")

    if report.reconciliation:
        rec = report.reconciliation
        lines.append("## Cost reconciliation")
        lines.append("")
        lines.append(f"- Total change: {_money(rec.total_change, report.currency)}")
        lines.append(f"- Attributed to findings: {_money(rec.attributed_change, report.currency)}")
        lines.append(f"- Unattributed: {_money(rec.unattributed_change, report.currency)}")
        lines.append(
            f"- Within {rec.tolerance:.0%} tolerance: {'yes' if rec.within_tolerance else 'no'}"
        )
        if rec.note:
            lines.append(f"- Note: {rec.note}")
        lines.append("")

    if report.validation_issues:
        lines.append("## Evidence validation")
        lines.append("")
        for issue in report.validation_issues:
            target = f" ({issue.finding_id})" if issue.finding_id else ""
            lines.append(f"- **{issue.severity}** {issue.code}{target}: {issue.detail}")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append(
        "CloudCause is read-only. Recommendations are for human review; no resource was "
        "changed by this investigation."
    )
    return "\n".join(lines)


def report_headline(report: InvestigationReport) -> str:
    if not report.findings:
        return "No material cost increase could be attributed with evidence."
    top = report.findings[0]
    return (
        f"{top.provider.upper()} {top.category}: {top.suspected_root_cause} "
        f"({top.actual_cost_increase:,.2f} {report.currency}, confidence {top.confidence:.2f})"
    )
