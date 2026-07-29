"""Investigation planning.

Deterministic by default: the plan is derived from the measured candidates. In
live mode ADK may rewrite the task questions and focus areas, but never the
numbers or the candidate list.
"""

from __future__ import annotations

from collections.abc import Sequence

from cloudcause_contracts import (
    InvestigationPlan,
    InvestigationRequest,
    PeriodComparison,
    Provider,
    ProviderTask,
)

MAX_MUST_EXPLAIN = 3


def deterministic_summary(comparison: PeriodComparison) -> str:
    parts: list[str] = []
    for provider_comparison in comparison.providers:
        percent = (
            "new spend"
            if provider_comparison.percent_change is None
            else f"{provider_comparison.percent_change:+.1f}%"
        )
        parts.append(
            f"{provider_comparison.provider}: {provider_comparison.absolute_change:+,.2f} "
            f"{provider_comparison.currency} ({percent}), "
            f"{len(provider_comparison.candidates)} material candidate(s)"
        )
    total = (
        f"total change {comparison.total_absolute_change:+,.2f} "
        f"from {comparison.total_baseline_cost:,.2f} to {comparison.total_current_cost:,.2f}"
    )
    return f"{total}. " + "; ".join(parts) if parts else total


def build_plan(
    investigation_id: str,
    request: InvestigationRequest,
    comparison: PeriodComparison,
    providers: Sequence[Provider] | None = None,
) -> InvestigationPlan:
    """Plan one task per provider that has data.

    ``providers`` defaults to the requested set. The orchestrator narrows it to the
    providers that normalized successfully, so a specialist is never asked to
    investigate data nobody could load.
    """

    tasks: list[ProviderTask] = []
    for provider in providers if providers is not None else request.providers:
        provider_comparison = comparison.for_provider(provider)
        candidates = list(provider_comparison.candidates) if provider_comparison else []
        focus_areas = sorted(
            {candidate.service_name for candidate in candidates if candidate.service_name}
        )
        must_explain = [candidate.key for candidate in candidates[:MAX_MUST_EXPLAIN]]
        if candidates:
            question = (
                f"Explain the {provider_comparison.absolute_change:+,.2f} "
                f"{provider_comparison.currency} change in {provider} spending, starting with "
                f"{', '.join(must_explain)}."
            )
        else:
            question = (
                f"No material {provider} increase was measured. Confirm data completeness and "
                "report that nothing needs explaining."
            )
        tasks.append(
            ProviderTask(
                provider=provider,
                question=question,
                candidate_ids=[candidate.candidate_id for candidate in candidates],
                focus_areas=focus_areas,
                must_explain=must_explain,
                max_findings=max(len(candidates), 1),
            )
        )
    return InvestigationPlan(
        investigation_id=investigation_id,
        question=request.question,
        current_period=request.current_period,
        baseline_period=request.baseline_period,
        tasks=tasks,
        deterministic_summary=deterministic_summary(comparison),
        rationale=(
            "Providers with material candidates are investigated concurrently. Each specialist "
            "must explain its own candidates with evidence; cost figures come from the "
            "deterministic layer."
        ),
        planner_mode="deterministic",
    )


def providers_with_work(plan: InvestigationPlan) -> list[Provider]:
    return [task.provider for task in plan.tasks if task.candidate_ids]
