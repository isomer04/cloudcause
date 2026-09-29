"""Renderer tests for the PDF export.

Mirrors ``tests/security/test_upload_safety.py::test_a_formula_prefixed_cell_is_neutralized_in_the_markdown_export``
in spirit: the PDF renderer is the same kind of pure function over
``InvestigationReport`` as ``report_to_markdown``, so it gets the same class
of test - fixture in, bytes out, no gateway required for the unit-level
cases. The one gateway-backed test pulls a real multi-finding report so the
findings/rule-ID/evidence assertions exercise actual content, not a hand-built
stub.
"""

from __future__ import annotations

import re
from io import BytesIO
from pathlib import Path

from cloudcause_api import API_PREFIX, app, main
from cloudcause_api.rendering_pdf import (
    _PROVIDER_LABEL,
    _RISK_WORD,
    _confidence,
    _humanize_category,
    _identifier,
    render_report_pdf,
)
from cloudcause_contracts import (
    DateRange,
    Evidence,
    Finding,
    InvestigationReport,
    InvestigationRequest,
    Settings,
)
from fastapi.testclient import TestClient
from pypdf import PdfReader


def _extract_text(pdf_bytes: bytes) -> str:
    reader = PdfReader(BytesIO(pdf_bytes))
    return "\n".join(page.extract_text() for page in reader.pages)


def _minimal_report(**overrides) -> InvestigationReport:
    request = InvestigationRequest(
        providers=["aws"],
        start_date="2026-07-13",  # type: ignore[arg-type]
        end_date="2026-07-19",  # type: ignore[arg-type]
        comparison_start_date="2026-07-06",  # type: ignore[arg-type]
        comparison_end_date="2026-07-12",  # type: ignore[arg-type]
    )
    defaults: dict = dict(
        investigation_id="inv-pdf-test",
        question="Why did our cloud spending increase?",
        request=request,
        current_period=DateRange(start="2026-07-13", end="2026-07-19"),  # type: ignore[arg-type]
        baseline_period=DateRange(start="2026-07-06", end="2026-07-12"),  # type: ignore[arg-type]
    )
    defaults.update(overrides)
    return InvestigationReport(**defaults)


def test_pdf_bytes_start_with_the_pdf_signature_and_have_a_sane_page_count() -> None:
    report = _minimal_report()
    pdf_bytes = render_report_pdf(report)
    assert pdf_bytes.startswith(b"%PDF-")
    assert 1 <= len(PdfReader(BytesIO(pdf_bytes)).pages) <= 5


def test_rendering_the_same_report_twice_is_byte_identical() -> None:
    """The reproducibility gate: two renders of one fixture must match exactly."""

    report = _minimal_report()
    first = render_report_pdf(report)
    second = render_report_pdf(report)
    assert first == second


def test_a_resource_name_with_markup_characters_renders_as_literal_text() -> None:
    """The PDF equivalent of the markdown formula-cell test: injected markup must
    never reach ReportLab's Paragraph parser unescaped."""

    hostile = Evidence(
        evidence_id="AWS-E001",
        provider="aws",
        source_type="inventory",
        source_id="<script>alert(1)</script>",
        observed_at="2026-07-19T00:00:00Z",  # type: ignore[arg-type]
        statement='<b>bold</b> & "quoted" & <img src=x onerror=alert(1)>',
    )
    report = _minimal_report(
        findings=[
            Finding(
                finding_id="AWS-F01",
                provider="aws",
                category="unexplained_increase",
                suspected_root_cause="Cost rose because of <injected markup> & stuff.",
                affected_resources=["<img src=x onerror=alert(1)>"],
                evidence=[hostile],
                recommendation="Do <this> & that.",
            )
        ]
    )

    pdf_bytes = render_report_pdf(report)  # must not raise
    text = _extract_text(pdf_bytes)
    assert "<script>" in text
    assert "<injected markup>" in text
    assert "<img src=x" in text


def test_an_upload_origin_report_carries_the_unverified_data_caveat() -> None:
    report = _minimal_report(data_origin="upload")
    text = _extract_text(render_report_pdf(report))
    assert "did not verify them against a" in text


def test_a_fixture_origin_report_omits_the_upload_caveat() -> None:
    report = _minimal_report(data_origin="fixture")
    text = _extract_text(render_report_pdf(report))
    assert "did not verify them against a" not in text


def test_the_pdf_speaks_the_same_vocabulary_as_the_web_report() -> None:
    """The export must not rename what the UI just showed the reader.

    Mirrors ``tests/ui/test_web_types_mirror_the_contract.py`` in spirit: it
    reads the real ``web/lib/format.ts`` so the two label sets cannot drift.
    The PDF used to print "Api Key Abuse", "GCP", "HIGH RISK" and "0.86" for
    what the web report calls "API key abuse", "Google Cloud", "High risk"
    and "86%".
    """

    format_ts = Path(__file__).resolve().parents[2] / "web" / "lib" / "format.ts"
    source = format_ts.read_text(encoding="utf-8")

    # Every acronym the web humanizer restores must be restored here too.
    web_acronyms = set(re.findall(r"\\b(\w+)\\b/gi", source))
    assert web_acronyms, "expected humanizeCategory to list acronyms"
    for acronym in web_acronyms:
        humanized = _humanize_category(f"{acronym}_key_abuse")
        assert acronym.upper() in humanized, f"{acronym} should be capitalised as an acronym"

    assert _humanize_category("api_key_abuse") == "API key abuse"
    assert _humanize_category("nat_gateway_misroute") == "NAT gateway misroute"
    assert _humanize_category("functions_retry_loop") == "Functions retry loop"
    assert _humanize_category("") == "Finding"

    # confidencePercent rounds to a whole percent; so must the PDF.
    assert "Math.round(value * 100)" in source
    assert _confidence(0.86) == "86%"
    assert _confidence(1.0) == "100%"


def test_provider_and_risk_labels_match_the_web_report() -> None:
    format_ts = Path(__file__).resolve().parents[2] / "web" / "lib" / "format.ts"
    source = format_ts.read_text(encoding="utf-8")

    for provider, label in _PROVIDER_LABEL.items():
        assert f'{provider}: "{label}"' in source, f"{provider} label drifted from format.ts"
    for risk, label in _RISK_WORD.items():
        assert f'{risk}: "{label}"' in source, f"{risk} risk label drifted from format.ts"


def test_a_long_identifier_wraps_after_separators_without_losing_characters() -> None:
    """Readability fix with a correctness edge: wrapping must not alter the ID.

    ``_identifier`` exists because ReportLab's own long-word splitter broke
    ARNs mid-segment. It inserts line breaks, so the invariant worth pinning is
    that stripping those breaks returns the original string, and that no
    wrapped line begins with an orphaned path separator. Only the first line
    may start with one, because the identifier itself can.
    """

    arn = (
        "/subscriptions/8f3c2b71-9d4e-4a5f-8c21-7b6e5d4c3a2b/resourceGroups"
        "/rg-prod/providers/Microsoft.Web/sites/orders-processor"
    )
    lines = _identifier(arn, 120, 7.5).split("<br/>")

    assert len(lines) > 1, "a 100-character ARN in a 120pt column must wrap"
    assert "".join(lines) == arn
    assert not any(line.startswith(("/", ":", ".")) for line in lines[1:])


def test_a_doubled_separator_never_starts_a_wrapped_line() -> None:
    """The "https://" case: two separators in a row must not orphan a slash."""

    url = "usage://serviceusage.googleapis.com/projects/cloudcause-demo/services/translate"
    lines = _identifier(url, 100, 7.5).split("<br/>")

    assert "".join(lines) == url
    assert not any(line.startswith(("/", ":", ".")) for line in lines[1:])


def test_a_short_identifier_is_left_on_one_line_and_stays_escaped() -> None:
    assert _identifier("nat-0ab12cd34ef56789a", 400, 8.5) == "nat-0ab12cd34ef56789a"
    assert _identifier("<img src=x>", 400, 8.5) == "&lt;img src=x&gt;"


def test_findings_and_rule_ids_are_present_in_a_real_investigation_report() -> None:
    main.configure(Settings.from_env({}))
    with TestClient(app) as client:
        response = client.post(
            f"{API_PREFIX}/investigations?wait=true",
            json={
                "providers": ["aws", "azure", "gcp"],
                "start_date": "2026-07-13",
                "end_date": "2026-07-19",
                "comparison_start_date": "2026-07-06",
                "comparison_end_date": "2026-07-12",
                "question": "Why did our cloud spending increase last week?",
                "scenario_id": "default",
            },
        )
        assert response.status_code == 200, response.text
        investigation_id = response.json()["investigation_id"]
        report = InvestigationReport.model_validate(
            client.get(f"{API_PREFIX}/investigations/{investigation_id}/report").json()
        )

    text = _extract_text(render_report_pdf(report))
    assert report.findings, "fixture scenario must actually produce findings"
    for finding in report.findings:
        assert finding.finding_id in text
        for rule in finding.applied_rules:
            assert rule.rule_id in text
    assert "Read-only" in text
    assert "Totals are computed by deterministic code" in text
