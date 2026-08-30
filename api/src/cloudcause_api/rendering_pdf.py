"""Server-side PDF rendering for the investigation report.

Renders the same ``InvestigationReport`` contract that feeds
``report_to_markdown`` (see ``cloudcause_contracts.rendering``). Nothing here
computes a number; every figure is read straight off the contract.

This lives in the API rather than in ``packages/contracts`` on purpose.
Markdown rendering is pure stdlib, so it earns a place in the dependency-free
contracts package that every workspace member imports. ReportLab is a real
dependency with real weight, so it stays in the one package that actually
needs it. If a CLI or worker ever needs PDF export, split a dependency-free
content model out of this file and promote only that.
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from datetime import UTC
from io import BytesIO
from math import isfinite
from xml.sax.saxutils import escape

from cloudcause_contracts import Finding, InvestigationReport
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import (
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

# Palette roles borrowed from web/app/globals.css, not its exact tokens:
# one accent for the brand rule, a muted grey for secondary text, and risk
# colours that still read in greyscale because every one is paired with its
# "High / Medium / Low" word.
#
# The greys are deliberately darker than the web tokens. Print has no
# backlight, and a report is read at 100% zoom or on paper, so the secondary
# text here is set to stay legible at 8pt rather than to recede.
_INK = colors.HexColor("#15171b")
_INK_SOFT = colors.HexColor("#454b55")
_INK_MUTE = colors.HexColor("#5a616c")
_RULE = colors.HexColor("#dde1e7")
_RULE_STRONG = colors.HexColor("#bcc2cb")
_BRAND = colors.HexColor("#7a2731")
_BRAND_TINT = colors.HexColor("#f8ecee")
_CAUTION = colors.HexColor("#7a5c22")
_CAUTION_TINT = colors.HexColor("#f7eedd")
_SAVINGS = colors.HexColor("#2a5f64")
_SAVINGS_TINT = colors.HexColor("#e6f0f0")

_RISK_COLOR = {"high": _BRAND, "medium": _CAUTION, "low": _SAVINGS}
_RISK_TINT = {"high": _BRAND_TINT, "medium": _CAUTION_TINT, "low": _SAVINGS_TINT}

# The vocabulary below is the app's, not this renderer's. A reader opens the
# report in the web UI and exports this PDF from it, so every label has to be
# the one they just read: "API key abuse", not "Api Key Abuse"; "Google Cloud",
# not "GCP"; "86%", not "0.86". These mirror web/lib/format.ts
# (RISK_LABEL, PROVIDER_LABEL, humanizeCategory, confidencePercent) and
# tests/unit/test_rendering_pdf.py pins them to that file so the two cannot
# drift apart again.
_RISK_WORD = {"high": "High risk", "medium": "Medium risk", "low": "Low risk"}
_PROVIDER_LABEL = {"aws": "AWS", "azure": "Azure", "gcp": "Google Cloud"}
_ACRONYMS = ("nat", "api", "ec2", "s3", "vpc", "iam", "sku", "gpu")


def _humanize_category(value: str) -> str:
    """Turn a category slug into the sentence-case phrase the web UI shows.

    ``str.title()`` is what this used to do, and it produced "Api Key Abuse" -
    three capitalised words that read as a machine's output rather than as a
    description of what happened. Sentence case with the acronyms restored
    reads as a person wrote it.
    """

    words = value.replace("_", " ").replace("-", " ").split()
    if not words:
        return "Finding"
    restored = [word.upper() if word.lower() in _ACRONYMS else word.lower() for word in words]
    first = restored[0]
    if first.lower() not in _ACRONYMS:
        first = first[:1].upper() + first[1:]
    return " ".join([first, *restored[1:]])


def _confidence(value: float) -> str:
    """Confidence as the percentage the rest of the product speaks in."""

    return f"{round(value * 100)}%"


# Wider margins than the 0.9in default: at 11pt the old measure ran past 100
# characters a line, which is the main thing that made this report tiring to
# read. Every frame width is derived from the page size rather than hardcoded,
# so a non-letter ``page_size`` still lays out inside its own margins.
_PAGE_MARGIN = 1.05 * inch

# ReportLab's default Frame insets its content by 6pt on each side, and
# SimpleDocTemplate builds that frame for us. Paragraphs respect the inset;
# a Table given an explicit width does not, so the old full-margin tables hung
# 6pt outside the text column on both sides and nothing on the page shared a
# left edge. Every width here is measured inside the padding instead.
_FRAME_PADDING = 6.0


def _content_width(page_size) -> float:
    return page_size[0] - 2 * _PAGE_MARGIN - 2 * _FRAME_PADDING


def _money(value: float, currency: str = "USD") -> str:
    return f"{value:,.2f} {currency}"


def _percent(value: float | None) -> str:
    return "new spend" if value is None else f"{value:+.1f}%"


def _duration_text(seconds: float) -> str:
    """Print a step duration in a unit the measurement supports.

    A deterministic specialist finishes in tens of milliseconds. Rendering that
    as "0.0" claims no work was done, which is the opposite of what happened.

    Non-finite values are screened before the thresholds, which all compare false
    against NaN and would otherwise print "nans" into the exported report.
    """

    if not isfinite(seconds) or seconds <= 0:
        return "-"
    if seconds < 1:
        return f"{max(round(seconds * 1000), 1)} ms"
    if seconds < 10:
        return f"{seconds:.2f}s"
    return f"{seconds:.1f}s"


def _esc(value: object) -> str:
    """Escape one value for ReportLab's mini-HTML ``Paragraph`` markup.

    ``Paragraph`` parses its text as markup, so any ``&``, ``<``, ``>`` in a
    resource name, question, or uploaded row value is an injection/breakage
    vector. Every interpolated string must pass through here before it joins
    a Paragraph f-string; the markup tags themselves are written unescaped.
    """

    flattened = str(value).replace("\r", " ").replace("\n", " ").strip()
    return escape(flattened)


def _pdf_date_formatter(generated_at) -> Callable[[int, int, int, int, int, int], str]:
    """Force the embedded CreationDate/ModDate to the report's own timestamp.

    ``invariant=1`` (set on the document below) pins ReportLab's internal
    clock to a fixed epoch so two renders of the same report produce
    byte-identical PDFs. Left alone that would stamp every report with the
    same fake date, so this formatter overrides it with ``generated_at``
    instead - still deterministic, but truthful.
    """

    stamp = generated_at.astimezone(UTC)

    def _format(_yyyy: int, _mm: int, _dd: int, _hh: int, _m: int, _s: int) -> str:
        return (
            f"D:{stamp.year:04d}{stamp.month:02d}{stamp.day:02d}"
            f"{stamp.hour:02d}{stamp.minute:02d}{stamp.second:02d}+00'00'"
        )

    return _format


class _NumberedCanvas(Canvas):
    """Buffers pages so the footer can print "Page N of M" on every one.

    ReportLab only knows the final page count after the whole story has been
    laid out, so ``showPage`` defers the actual draw until ``save`` replays
    each buffered page state with the total in hand.
    """

    def __init__(self, *args, footer_fn, date_formatter, **kwargs):
        super().__init__(*args, **kwargs)
        self._footer_fn = footer_fn
        self.setDateFormatter(date_formatter)
        self._saved_page_states: list[dict] = []

    def showPage(self) -> None:
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self) -> None:
        total_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self._footer_fn(self, self._pageNumber, total_pages)
            super().showPage()
        super().save()


def _footer(report: InvestigationReport) -> Callable[[Canvas, int, int], None]:
    def draw(canvas: Canvas, page_number: int, total_pages: int) -> None:
        canvas.saveState()
        width, _ = canvas._pagesize
        y = 0.55 * inch
        # Align the footer with the text column, not with the page margin.
        left = _PAGE_MARGIN + _FRAME_PADDING
        right = width - _PAGE_MARGIN - _FRAME_PADDING
        canvas.setStrokeColor(_RULE)
        canvas.setLineWidth(0.5)
        canvas.line(left, y + 0.28 * inch, right, y + 0.28 * inch)
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(_INK_MUTE)
        canvas.drawString(left, y + 0.13 * inch, "Read-only · recommendations require human action")
        canvas.drawString(left, y, "Totals are computed by deterministic code, not by a model.")
        canvas.drawRightString(
            right,
            y + 0.06 * inch,
            f"Page {page_number} of {total_pages} · {report.investigation_id}",
        )
        canvas.restoreState()

    return draw


def _styles() -> dict[str, ParagraphStyle]:
    """One type scale for the whole document.

    Three families with one job each: a serif for anything read as sentences,
    a sans for labels and figures that are scanned rather than read, and a
    monospace reserved for identifiers a reader may need to copy. Everything
    prose-sized sits at 10pt or above; nothing below 8pt carries content that
    is not repeated elsewhere in the report.
    """

    base = getSampleStyleSheet()
    styles = {
        "title": ParagraphStyle(
            "CCTitle",
            parent=base["Title"],
            fontName="Times-Bold",
            fontSize=21,
            leading=25,
            textColor=_INK,
            alignment=0,
            spaceAfter=6,
        ),
        "meta": ParagraphStyle(
            "CCMeta",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=9,
            leading=13.5,
            textColor=_INK_SOFT,
        ),
        "headline": ParagraphStyle(
            "CCHeadline",
            parent=base["Normal"],
            fontName="Times-Bold",
            fontSize=17,
            leading=21,
            textColor=_BRAND,
            spaceBefore=12,
            spaceAfter=8,
        ),
        "body": ParagraphStyle(
            "CCBody",
            parent=base["Normal"],
            fontName="Times-Roman",
            fontSize=11,
            leading=16,
            textColor=_INK,
        ),
        "caveat": ParagraphStyle(
            "CCCaveat",
            parent=base["Normal"],
            fontName="Times-Italic",
            fontSize=10,
            leading=14.5,
            textColor=_INK_SOFT,
            borderColor=_RULE_STRONG,
            borderWidth=0,
            leftIndent=8,
        ),
        # ``keepWithNext`` stops a section heading from being left alone at the
        # foot of a page while its table starts on the next one. It only binds
        # the heading to the first flowable, so a long table still splits
        # normally - which is what a multi-page evidence table needs.
        "h2": ParagraphStyle(
            "CCH2",
            parent=base["Normal"],
            fontName="Helvetica-Bold",
            fontSize=11,
            leading=14,
            textColor=_INK_SOFT,
            spaceBefore=20,
            spaceAfter=10,
            keepWithNext=1,
        ),
        "finding_title": ParagraphStyle(
            "CCFindingTitle",
            parent=base["Normal"],
            fontName="Times-Bold",
            fontSize=13,
            leading=17,
            textColor=_INK,
        ),
        # Small-caps-ish column headers for the stat strips. Tracking is faked
        # with spaces at call sites where it helps; the weight does the work.
        "stat_label": ParagraphStyle(
            "CCStatLabel",
            parent=base["Normal"],
            fontName="Helvetica-Bold",
            fontSize=7,
            leading=9,
            textColor=_INK_MUTE,
        ),
        "stat_value": ParagraphStyle(
            "CCStatValue",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=10,
            leading=13,
            textColor=_INK,
        ),
        "finding_meta": ParagraphStyle(
            "CCFindingMeta",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=8.5,
            leading=12,
            textColor=_INK_MUTE,
        ),
        "finding_body": ParagraphStyle(
            "CCFindingBody",
            parent=base["Normal"],
            fontName="Times-Roman",
            fontSize=10.5,
            leading=15,
            textColor=_INK,
            spaceBefore=4,
        ),
        "recommendation": ParagraphStyle(
            "CCRecommendation",
            parent=base["Normal"],
            fontName="Times-Roman",
            fontSize=10.5,
            leading=15,
            textColor=_INK,
        ),
        "table_head": ParagraphStyle(
            "CCTableHead",
            parent=base["Normal"],
            fontName="Helvetica-Bold",
            fontSize=7.5,
            leading=10,
            textColor=_INK_MUTE,
        ),
        "table_cell": ParagraphStyle(
            "CCTableCell",
            parent=base["Normal"],
            fontName="Times-Roman",
            fontSize=9.5,
            leading=13,
            textColor=_INK,
        ),
        "table_num": ParagraphStyle(
            "CCTableNum",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=9,
            leading=13,
            textColor=_INK,
            alignment=2,
        ),
        "mono_cell": ParagraphStyle(
            "CCMonoCell",
            parent=base["Normal"],
            fontName="Courier",
            fontSize=7.5,
            leading=10.5,
            textColor=_INK_SOFT,
        ),
    }
    return styles


_TABLE_GRID = TableStyle(
    [
        ("LINEBELOW", (0, 0), (-1, 0), 0.75, _RULE_STRONG),
        ("LINEBELOW", (0, 1), (-1, -1), 0.4, _RULE),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]
)


def _identifier(value: str, avail_width: float, font_size: float, font_name: str = "Courier") -> str:
    """Lay a long identifier out over lines that break at path separators.

    ReportLab's ``splitLongWords`` chops an unbroken ARN or URL wherever the
    line happens to run out, which turned the evidence table into columns of
    six-line ragged towers with segment names severed mid-word. Soft hyphens
    are not a fix - ReportLab 5 treats ``&shy;`` as a break opportunity but
    then overflows the frame instead of breaking at the later ones - so the
    line breaking is done here, greedily, and handed over as explicit
    ``<br/>``.

    Breaks are only ever taken *after* a separator, so no line starts with an
    orphaned ``/`` and every segment name stays intact. Lines are packed
    against a measured string width rather than a character estimate: guessing
    left lines one character too long, and ReportLab then re-broke them and
    stranded a single ``:`` on a line of its own. A segment too long for
    ``avail_width`` on its own is left over-long and ReportLab splits it as
    before; that is rare and still better than breaking every line badly.
    """

    value = str(value).replace("\r", " ").replace("\n", " ").strip()
    chunks: list[str] = []
    current = ""
    for character in value:
        current += character
        if character in "/:.":
            chunks.append(current)
            current = ""
    if current:
        chunks.append(current)

    # Adjacent separators ("//" in a URL, ":/" in a source key) each close a
    # chunk of their own, which would let a line begin with a stray slash.
    # Fold those back into the chunk before them so a break is only ever taken
    # after a complete separator run. A leading separator has nothing to fold
    # into and stays where the identifier itself put it.
    folded: list[str] = []
    for chunk in chunks:
        if folded and set(chunk) <= set("/:."):
            folded[-1] += chunk
        else:
            folded.append(chunk)
    chunks = folded

    lines: list[str] = []
    line = ""
    for chunk in chunks:
        if line and stringWidth(line + chunk, font_name, font_size) > avail_width:
            lines.append(line)
            line = chunk
        else:
            line += chunk
    if line:
        lines.append(line)
    return "<br/>".join(escape(one) for one in lines) or escape(value)


def _table(rows: list[list], col_widths: list[float]) -> Table:
    table = Table(rows, colWidths=col_widths, repeatRows=1)
    table.setStyle(_TABLE_GRID)
    return table


def _kpi_row(report: InvestigationReport, styles: dict[str, ParagraphStyle], width: float) -> Table:
    reconciliation = report.reconciliation
    attributed = reconciliation.attributed_change if reconciliation else None
    unattributed = reconciliation.unattributed_change if reconciliation else 0.0
    explained_note = (
        f"{_money(unattributed, report.currency)} unattributed"
        + (", within tolerance" if reconciliation and reconciliation.within_tolerance else "")
        if reconciliation
        else ""
    )

    def cell(label: str, value: str, note: str = "", emphasis: bool = False) -> list[Paragraph]:
        value_style = ParagraphStyle(
            "kpi_value",
            parent=styles["body"],
            fontName="Helvetica-Bold",
            fontSize=12.5,
            leading=16,
            textColor=_BRAND if emphasis else _INK,
            spaceBefore=3,
        )
        label_style = ParagraphStyle(
            "kpi_label",
            parent=styles["stat_label"],
            textColor=_INK_MUTE,
        )
        note_style = ParagraphStyle(
            "kpi_note",
            parent=styles["meta"],
            fontSize=8,
            leading=11,
            spaceBefore=2,
        )
        cells = [Paragraph(_esc(label).upper(), label_style), Paragraph(_esc(value), value_style)]
        if note:
            cells.append(Paragraph(_esc(note), note_style))
        return cells

    columns = [
        cell("This period", _money(report.total_current_cost, report.currency)),
        cell("Baseline (adjusted)", _money(report.total_baseline_cost, report.currency)),
        cell(
            "Change",
            _money(report.total_absolute_change, report.currency),
            _percent(report.total_percent_change),
            emphasis=True,
        ),
        cell(
            "Explained by findings",
            "n/a" if attributed is None else _money(attributed, report.currency),
            explained_note,
        ),
    ]
    table = Table([columns], colWidths=[width / 4] * 4)
    table.setStyle(
        TableStyle(
            [
                ("LINEBELOW", (0, 0), (-1, -1), 0.75, _RULE_STRONG),
                ("LINEABOVE", (0, 0), (-1, -1), 0.75, _RULE_STRONG),
                ("LINEAFTER", (0, 0), (2, 0), 0.4, _RULE),
                ("TOPPADDING", (0, 0), (-1, -1), 10),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
                ("LEFTPADDING", (0, 0), (0, 0), 0),
                ("LEFTPADDING", (1, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    return table


def _headline(report: InvestigationReport) -> str:
    """Say what happened, in words, once.

    This line used to read "430.26 USD (+69.5%) from 618.66 USD to 1,048.92
    USD" - all four figures from the table directly beneath it, in a sentence
    fragment. The table is better at figures than a headline is, so the
    headline states the direction and leaves the numbers to the table.
    """

    percent_change = report.total_percent_change
    if percent_change is None:
        return f"{_money(report.total_absolute_change, report.currency)} of new spending this period"
    if report.total_absolute_change > 0:
        return f"Spending rose {abs(percent_change):.1f}% against the baseline period"
    if report.total_absolute_change < 0:
        return f"Spending fell {abs(percent_change):.1f}% against the baseline period"
    return "Spending held level against the baseline period"


def _page_one(report: InvestigationReport, styles: dict[str, ParagraphStyle], width: float) -> list:
    story: list = []
    story.append(Paragraph(f"CloudCause investigation {_esc(report.investigation_id)}", styles["title"]))
    # The question is what the report answers, so it gets the largest body
    # setting in the document rather than being lost in the meta stack.
    story.append(
        Paragraph(
            _esc(report.question),
            ParagraphStyle("question", parent=styles["body"], fontSize=12.5, leading=17, spaceAfter=8),
        )
    )
    story.append(
        Paragraph(
            f"Current period {_esc(report.current_period.label())} &rarr; "
            f"baseline {_esc(report.baseline_period.label())}"
            f" &middot; generated {_esc(report.generated_at.astimezone(UTC).strftime('%Y-%m-%d %H:%M UTC'))}",
            styles["meta"],
        )
    )

    mode_bits = [
        {"fixture": "Fixture data", "upload": "Your uploaded export", "live": "Live provider data"}.get(
            report.data_origin, "Fixture data"
        ),
        f"data mode: {report.data_mode}",
        "deterministic playbooks" if report.agent_mode == "stub" else "live AI agents",
    ]
    if report.knowledge:
        mode_bits.append(f"FOCUS {report.knowledge.focus_version}")
    story.append(Spacer(1, 4))
    story.append(Paragraph(_esc(" · ".join(mode_bits)), styles["meta"]))

    if report.data_origin == "upload":
        story.append(Spacer(1, 8))
        story.append(
            Paragraph(
                "These numbers come from a cost export you supplied. CloudCause measured them and "
                "cited the billing rules that interpret them, but it did not verify them against a "
                "cloud account.",
                styles["caveat"],
            )
        )

    story.append(Paragraph(_esc(_headline(report)), styles["headline"]))
    story.append(_kpi_row(report, styles, width))

    if report.summary:
        story.append(Spacer(1, 14))
        story.append(Paragraph(_esc(report.summary), styles["body"]))

    return story


def _risk_chip(finding: Finding, styles: dict[str, ParagraphStyle]) -> Table:
    """The risk level as a filled chip rather than loose right-aligned text.

    The word is kept ("High risk", not a bare colour) so the chip survives a
    greyscale print or a colour-blind reader; the fill only makes it findable
    when skimming down the page.
    """

    risk_word = _RISK_WORD.get(finding.risk, f"{finding.risk.capitalize()} risk")
    color = _RISK_COLOR.get(finding.risk, _INK_MUTE)
    tint = _RISK_TINT.get(finding.risk, _RULE)
    chip = Table(
        [
            [
                Paragraph(
                    _esc(risk_word),
                    ParagraphStyle(
                        "chip",
                        parent=styles["stat_label"],
                        fontSize=7.5,
                        leading=10,
                        alignment=1,
                        textColor=color,
                    ),
                )
            ]
        ],
        colWidths=[1.0 * inch],
    )
    chip.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), tint),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("ROUNDEDCORNERS", [3, 3, 3, 3]),
            ]
        )
    )
    return chip


def _share_bar(share: float, width: float) -> Table:
    """This finding's share of the total change, drawn to scale.

    The one piece of real information the report never carried: the reader had
    four money figures and no way to see how they related to the headline
    total without doing arithmetic. A bar drawn at ``share`` of the column
    answers "how much of the increase is this?" before the number is read.

    Deliberately plain - a filled rule against an unfilled track, no gradient,
    no rounded cap. It is the only quantitative graphic in the document and it
    earns its place by being measurable, not by being decorative.
    """

    filled = max(min(share, 1.0), 0.0) * width
    bar = Table([[""]], colWidths=[width], rowHeights=[3])
    bar.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), _RULE),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    if filled <= 0:
        return bar
    # Two cells rather than an overlay: ReportLab has no z-order, so the fill
    # is a column of its own sized to the share.
    return Table(
        [["", ""]],
        colWidths=[filled, max(width - filled, 0.01)],
        rowHeights=[3],
        style=TableStyle(
            [
                ("BACKGROUND", (0, 0), (0, 0), _BRAND),
                ("BACKGROUND", (1, 0), (1, 0), _RULE),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ]
        ),
    )


def _finding_stats(
    finding: Finding, currency: str, styles: dict[str, ParagraphStyle], width: float, total_change: float
) -> list:
    """The headline numbers as a labelled strip, not a run-on sentence.

    These used to be one middot-separated line ending in a wrapped resource
    ARN, which meant the figures a reader is actually looking for were buried
    mid-sentence. Labelled columns let the eye jump straight to one.

    The share of the total change leads, because it is the question a reader
    brings to a ranked list and the only column that relates this finding to
    the headline figure on page one.
    """

    share = finding.actual_cost_increase / total_change if total_change > 0 else 0.0
    cells = [
        ("Increase this period", _money(finding.actual_cost_increase, currency), _INK),
        # Tinted to tie the figure to the bar drawn beneath it.
        ("Share of increase", _percent_share(share), _BRAND),
        ("Estimated monthly impact", _money(finding.estimated_monthly_impact, currency), _INK),
        ("Confidence", _confidence(finding.confidence), _INK),
    ]
    row = [
        [
            Paragraph(_esc(label).upper(), styles["stat_label"]),
            Paragraph(
                _esc(value),
                ParagraphStyle(
                    "finding_stat",
                    parent=styles["stat_value"],
                    fontName="Helvetica-Bold",
                    textColor=color,
                    spaceBefore=2,
                ),
            ),
        ]
        for label, value, color in cells
    ]
    table = Table([row], colWidths=[width * 0.25, width * 0.22, width * 0.32, width * 0.21])
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                ("LEFTPADDING", (0, 0), (0, 0), 0),
                ("LEFTPADDING", (1, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    # The bar carries no label of its own: the "Share of the increase" column
    # above already names it, and a second caption would be one element doing
    # a job another element has done.
    return [table, Spacer(1, 6), _share_bar(share, width)]


def _percent_share(share: float) -> str:
    """A share below half a percent still happened; do not round it to zero."""

    if 0 < share < 0.005:
        return "<1%"
    return f"{round(share * 100)}%"


def _finding_block(
    finding: Finding,
    rank: int,
    currency: str,
    styles: dict[str, ParagraphStyle],
    width: float,
    total_change: float,
) -> KeepTogether:
    block: list = []
    title = _humanize_category(finding.category)
    uncertain = " (uncertain)" if finding.is_uncertain else ""

    header = Table(
        [
            [
                Paragraph(
                    f'<font color="{_INK_MUTE.hexval()}">{rank}.</font> '
                    f"{_esc(title)}{uncertain}<br/>"
                    f'<font size="8.5" face="Helvetica" color="{_INK_MUTE.hexval()}">'
                    f"{_esc(_PROVIDER_LABEL.get(finding.provider, finding.provider.upper()))}"
                    f" &middot; {_esc(finding.finding_id)}</font>",
                    styles["finding_title"],
                ),
                _risk_chip(finding, styles),
            ]
        ],
        colWidths=[width - 1.0 * inch, 1.0 * inch],
    )
    header.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    block.append(header)
    block.append(Spacer(1, 6))
    block.extend(_finding_stats(finding, currency, styles, width, total_change))

    resource = ", ".join(finding.affected_resources) or finding.service_name or finding.region_id or "unspecified"
    block.append(Spacer(1, 4))
    block.append(
        Paragraph(
            f'<font face="Helvetica-Bold" size="7" color="{_INK_MUTE.hexval()}">RESOURCE</font><br/>'
            f'<font face="Courier" size="8.5">{_identifier(resource, width, 8.5)}</font>',
            ParagraphStyle("resource", parent=styles["finding_meta"], leading=11, textColor=_INK_SOFT),
        )
    )

    if finding.suspected_root_cause:
        block.append(Paragraph(_esc(finding.suspected_root_cause), styles["finding_body"]))

    recommendation_label = "Consider — needs human approval" if finding.requires_human_approval else "Consider"
    recommendation_text = _esc(finding.recommendation or "No recommendation recorded.")
    recommendation_table = Table(
        [[Paragraph(f"<b>{_esc(recommendation_label)}:</b> {recommendation_text}", styles["recommendation"])]],
        colWidths=[width],
    )
    recommendation_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), _BRAND_TINT if finding.requires_human_approval else _CAUTION_TINT),
                ("LINEBEFORE", (0, 0), (0, 0), 2.5, _BRAND if finding.requires_human_approval else _CAUTION),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ("LEFTPADDING", (0, 0), (-1, -1), 12),
                ("RIGHTPADDING", (0, 0), (-1, -1), 12),
            ]
        )
    )
    block.append(Spacer(1, 8))
    block.append(recommendation_table)
    block.append(Spacer(1, 8))

    # Provenance footnotes: the prose stays in the sans body font and only the
    # rule ID is set in monospace, so these read as a caption rather than as a
    # wall of 8pt Courier.
    for rule in finding.applied_rules:
        stale = f' <font color="{_CAUTION.hexval()}"><b>[stale]</b></font>' if rule.is_stale else ""
        block.append(
            Paragraph(
                f'Rule <font face="Courier">{_esc(rule.rule_id)}</font> '
                f"&middot; valid from {_esc(rule.valid_from)} &middot; reviewed {_esc(rule.reviewed_at)}{stale}",
                styles["finding_meta"],
            )
        )

    count = len(finding.evidence)
    block.append(
        Paragraph(
            f"{count} evidence item{'s' if count != 1 else ''} — see provenance appendix",
            styles["finding_meta"],
        )
    )
    return KeepTogether(block)


def _finding_separator(width: float) -> Table:
    """A hairline between findings so each one reads as its own card."""

    rule = Table([[""]], colWidths=[width], rowHeights=[0.1])
    rule.setStyle(
        TableStyle(
            [
                ("LINEABOVE", (0, 0), (-1, 0), 0.4, _RULE),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    return rule


def _findings_section(report: InvestigationReport, styles: dict[str, ParagraphStyle], width: float) -> list:
    story: list = [Paragraph("FINDINGS", styles["h2"])]
    if not report.findings:
        story.append(Paragraph("No material, evidence-backed findings were produced.", styles["body"]))
        return story

    # A reader cannot tell that a list is ranked by looking at it, so say so.
    # Coverage is deliberately not repeated here: the summary above already
    # gives it as a percentage and the KPI row gives it in money, and a third
    # phrasing of the same fact reads as three different facts.
    total_change = report.total_absolute_change
    story.append(
        Paragraph(
            "Ranked by cost increase, largest first.",
            ParagraphStyle("findings_lead", parent=styles["meta"], fontSize=9.5, leading=13, spaceAfter=14),
        )
    )

    for rank, finding in enumerate(report.findings, start=1):
        if rank > 1:
            story.append(Spacer(1, 16))
            story.append(_finding_separator(width))
            story.append(Spacer(1, 16))
        story.append(_finding_block(finding, rank, report.currency, styles, width, total_change))
    return story


def _provider_breakdown(report: InvestigationReport, styles: dict[str, ParagraphStyle], width: float) -> list:
    if report.comparison is None or not report.comparison.providers:
        return []
    story: list = [Paragraph("PROVIDER BREAKDOWN", styles["h2"])]

    def head(label: str, numeric: bool = False) -> Paragraph:
        style = styles["table_head"]
        if numeric:
            style = ParagraphStyle("head_num", parent=style, alignment=2)
        return Paragraph(_esc(label).upper(), style)

    rows: list[list] = [
        [
            head("Provider"),
            head("Baseline", True),
            head("Current", True),
            head("Change", True),
            head("Percent", True),
            head("Candidates", True),
        ]
    ]
    for provider in report.comparison.providers:
        # Money columns are right-aligned so the decimal points stack and two
        # totals can be compared by eye without reading either in full.
        rows.append(
            [
                Paragraph(
                    _esc(provider.provider.upper()),
                    ParagraphStyle("provider", parent=styles["table_cell"], fontName="Helvetica-Bold"),
                ),
                Paragraph(_esc(_money(provider.baseline_cost, provider.currency)), styles["table_num"]),
                Paragraph(_esc(_money(provider.current_cost, provider.currency)), styles["table_num"]),
                Paragraph(_esc(_money(provider.absolute_change, provider.currency)), styles["table_num"]),
                Paragraph(_esc(_percent(provider.percent_change)), styles["table_num"]),
                Paragraph(str(len(provider.candidates)), styles["table_num"]),
            ]
        )
    fractions = [0.13, 0.19, 0.19, 0.19, 0.15, 0.15]
    story.append(_table(rows, [width * fraction for fraction in fractions]))
    return story


def _provenance_appendix(report: InvestigationReport, styles: dict[str, ParagraphStyle], width: float) -> list:
    story: list = [Paragraph("PROVENANCE APPENDIX", styles["h2"])]

    def head(label: str, numeric: bool = False) -> Paragraph:
        style = styles["table_head"]
        if numeric:
            # A right-aligned column needs a right-aligned header, or the
            # label floats away from the figures it names.
            style = ParagraphStyle("head_num", parent=style, alignment=2)
        return Paragraph(_esc(label).upper(), style)

    def cell(value: object) -> Paragraph:
        return Paragraph(_esc(value), styles["table_cell"])

    def subheading(text: str) -> Paragraph:
        return Paragraph(
            _esc(text),
            ParagraphStyle(
                "sub",
                parent=styles["body"],
                fontName="Helvetica-Bold",
                fontSize=10,
                leading=13,
                spaceBefore=6,
                spaceAfter=4,
                keepWithNext=1,
            ),
        )

    if report.knowledge:
        knowledge = report.knowledge
        story.append(subheading(f"Billing knowledge — FOCUS {knowledge.focus_version}"))
        story.append(
            Paragraph(
                f"Reviewed {_esc(knowledge.oldest_review_date)} to {_esc(knowledge.newest_review_date)}",
                styles["meta"],
            )
        )
        if knowledge.rule_ids:
            story.append(Paragraph(f"Applied rules: {_esc(', '.join(knowledge.rule_ids))}", styles["meta"]))
        if knowledge.stale_rule_ids:
            story.append(Paragraph(f"Stale rules: {_esc(', '.join(knowledge.stale_rule_ids))}", styles["meta"]))
        story.append(Spacer(1, 8))

    if report.reconciliation:
        rec = report.reconciliation
        story.append(subheading("Reconciliation"))
        story.append(
            Paragraph(
                f"Attributed {_money(rec.attributed_change, report.currency)} of "
                f"{_money(rec.total_change, report.currency)} total · "
                f"unattributed {_money(rec.unattributed_change, report.currency)} · "
                f"within {rec.tolerance:.0%} tolerance: {'yes' if rec.within_tolerance else 'no'}",
                styles["meta"],
            )
        )
        story.append(Spacer(1, 8))

    # The two short appendix tables are held together with their heading. They
    # are only a few rows each, so letting a page break strand one row under a
    # heading on the previous page costs more than the whitespace does.
    if report.sources:
        rows: list[list] = [[head("Provider"), head("Source"), head("Origin"), head("Data through")]]
        for source in report.sources:
            rows.append(
                [
                    cell(source.provider.upper()),
                    cell(source.source),
                    cell(source.origin),
                    cell(source.data_through.astimezone(UTC).strftime("%Y-%m-%d")),
                ]
            )
        story.append(
            KeepTogether([subheading("Data sources"), _table(rows, [width * f for f in (0.14, 0.44, 0.20, 0.22)])])
        )
        story.append(Spacer(1, 12))

    if report.provider_statuses:
        rows = [[head("Provider"), head("Status"), head("Findings", True), head("Duration", True), head("Origin")]]
        for status in report.provider_statuses:
            rows.append(
                [
                    cell(status.provider.upper()),
                    cell(status.status),
                    Paragraph(str(status.finding_count), styles["table_num"]),
                    Paragraph(_esc(_duration_text(status.duration_seconds)), styles["table_num"]),
                    cell(status.origin),
                ]
            )
        story.append(
            KeepTogether(
                [
                    subheading("Provider specialist results"),
                    _table(rows, [width * f for f in (0.16, 0.22, 0.16, 0.18, 0.28)]),
                ]
            )
        )

    # Evidence: the ID and its source are one fact about provenance, so they
    # share a column instead of forcing two narrow monospace columns that each
    # wrapped an ARN over six lines. That buys the observation - the part that
    # is actually read as a sentence - almost half the table width.
    evidence_fractions = (0.30, 0.54, 0.16)
    # The cell's own padding (5pt each side, from _TABLE_GRID) is not available
    # to text, so the wrap budget has to subtract it or every line comes out
    # one segment too wide.
    source_width = width * evidence_fractions[0] - 10
    evidence_rows: list[list] = [[head("Evidence"), head("Observation"), head("Observed")]]
    for finding in report.findings:
        for item in finding.evidence:
            evidence_rows.append(
                [
                    Paragraph(
                        f'<font face="Helvetica-Bold" size="8" color="{_INK.hexval()}">'
                        f"{_esc(item.evidence_id)}</font><br/>"
                        f"{_identifier(f'{item.source_type}:{item.source_id}', source_width, 7.5)}",
                        styles["mono_cell"],
                    ),
                    Paragraph(_esc(item.statement), styles["table_cell"]),
                    Paragraph(
                        _esc(item.observed_at.astimezone(UTC).strftime("%Y-%m-%d")),
                        ParagraphStyle("observed", parent=styles["table_cell"], fontName="Helvetica", fontSize=9),
                    ),
                ]
            )
    if len(evidence_rows) > 1:
        story.append(Spacer(1, 12))
        story.append(subheading("Evidence"))
        story.append(_table(evidence_rows, [width * f for f in evidence_fractions]))

    return story


def render_report_pdf(report: InvestigationReport, *, page_size=letter) -> bytes:
    """Render one investigation report to PDF bytes.

    Pure function of the report contract, same as ``report_to_markdown``: no
    number is computed here, only formatted. Rendering the same report twice
    produces byte-identical output (see ``_NumberedCanvas`` and
    ``_pdf_date_formatter``), which a caller may rely on for caching or a
    reproducibility check.
    """

    styles = _styles()
    width = _content_width(page_size)
    story: list = []
    story.extend(_page_one(report, styles, width))
    story.extend(_findings_section(report, styles, width))
    story.extend(_provider_breakdown(report, styles, width))
    story.extend(_provenance_appendix(report, styles, width))

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=page_size,
        leftMargin=_PAGE_MARGIN,
        rightMargin=_PAGE_MARGIN,
        topMargin=_PAGE_MARGIN,
        bottomMargin=_PAGE_MARGIN + 0.15 * inch,
        title=f"CloudCause investigation {report.investigation_id}",
        author="CloudCause",
        subject=report.question,
        creator="CloudCause",
        producer="CloudCause",
        invariant=1,
    )
    canvasmaker = functools.partial(
        _NumberedCanvas,
        footer_fn=_footer(report),
        date_formatter=_pdf_date_formatter(report.generated_at),
    )
    doc.build(story, canvasmaker=canvasmaker)
    return buffer.getvalue()
