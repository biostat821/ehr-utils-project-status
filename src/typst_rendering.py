import textwrap

from github_client import PullRequest
from project_util import PHASES, DocumentSpec, now, td_to_str


def _pad_to(x, n: int) -> str:
    """Convert to string and pad with escaped spaces.

    This is handy for LaTeX with monospaced font.
    """
    x_str = str(x)
    padding = n - len(x_str)
    if padding >= 3:
        return "\." * (padding - 1) + " " + x_str
    else:
        return "~" * padding + x_str


def _create_page_header(phase: int, pr: PullRequest) -> str:
    return (
        "*pull request*: \\\n"
        + f'"{pr.title}" (branch "{pr.branch}") \\\n'
        + f"{pr.permalink}\n"
        + (
            "\n"
            + "*inferred phase*: \\\n"
            + f"{phase:02} (https://github.com/biostat821/ehr-utils-project/blob/main/phase{phase:02}.md)\n"
            if phase in PHASES
            else ""
        )
    )


def write_document(
    username: str, pr_reports: list[tuple[int, PullRequest, DocumentSpec]]
):
    pages = [
        _create_page_header(phase, pr) + _construct_pr_report(pr_report)
        for phase, pr, pr_report in pr_reports
    ]
    document = (
        textwrap.dedent(f"""    
        #set page(
        margin: (
            x: 0.5in,
            top: 1in,
            bottom: 0.5in,
        ),
        header: [
            {now().strftime("%Y-%m-%d %H:%M:%S")}
            #h(1fr)
            {username}
            #h(1fr)
            #link("https://github.com/biostat821/ehr-utils-project-status/tree/v1.0.0")[ehr-utils-project-status 1.0.0]
            #line(length: 100%, stroke: gray) 
        ],
        numbering: "1",
        paper: "us-letter",
        )
        #set text(font: "DejaVu Sans Mono", size: 0.75em)
        #show link: it => {{ set text(fill: blue); underline(it) }}
        // Medium bold table header.
        #show table.cell.where(y: 0): set text(weight: "bold")
        // Thick bars at top and bottom of table.
        #set table(
            stroke: (x, y) => (
                top: if y == 0 {{ 2pt }} else {{ 0pt }},
                bottom: 2pt,
            ),
        )
        #set table.hline(stroke: 1pt)
        """).strip()
        + "\n\n"
    )

    if not pages:
        document += "No pull requests"
    document += "\n\n#pagebreak()\n\n".join(pages)
    with open(f"outputs/{username}.typ", "w") as f:
        f.write(document)


status_col_width = 17


def _construct_pr_report(documentSpec: DocumentSpec) -> str:
    document = ""
    document += (
        textwrap.dedent("""
            #table(
            columns: (auto, auto, 1fr),
            align: (left, left, right),
            inset: 5pt,
            table.header(
                [timestamp], [event], [status],
            ),
            table.hline(),
            """).strip()
        + "\n"
    )
    for entry in documentSpec.entries:
        timestamp = entry.timestamp
        event_summary = entry.summary
        previous_state = entry.previous_state
        elapsed_in_state = entry.elapsed_in_state
        if elapsed_in_state:
            document += f"[{timestamp}], [{event_summary}], [{previous_state.value} for {_pad_to(td_to_str(elapsed_in_state), status_col_width)}],\n"
        else:
            document += f"[{timestamp}], [{event_summary}], [],\n"
    document += "table.hline(),\n"
    document += f"[], [], [under development for {_pad_to(td_to_str(documentSpec.total_under_development_duration), status_col_width)}],\n"
    document += f"[], [], [under review for {_pad_to(td_to_str(documentSpec.total_under_review_duration), status_col_width)}],\n"
    if documentSpec.late_by:
        document += f"[], [], [late by {_pad_to(td_to_str(documentSpec.late_by), status_col_width)}],\n"
    if documentSpec.points_deducted is not None:
        document += f"[], [], [*points deducted*: *{_pad_to(documentSpec.points_deducted, status_col_width)}*],\n"
    document += textwrap.dedent("""
                                )
                                """).strip()
    return document
