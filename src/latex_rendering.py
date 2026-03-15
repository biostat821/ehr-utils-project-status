import textwrap
from typing import Any

from github_client import PullRequest
from project_util import PHASES, DocumentSpec, now, td_to_str


def _pad_to(x: Any, n: int) -> str:
    """Convert to string and pad with escaped spaces.

    This is handy for LaTeX with monospaced font.
    """
    x_str = str(x)
    padding = n - len(x_str)
    if padding >= 3:
        return "." * (padding - 1) + r"\ " + x_str
    else:
        return r"\ " * padding + x_str


def _escape_latex(raw: str) -> str:
    """Escape ampersands in strings bound for LaTeX."""
    return raw.replace("&", "\\&")


def _create_page_header(phase: int, pr: PullRequest) -> str:
    return (
        f"\\fancyfoot[R]{{\\setfont phase {phase:02}}}"
        + "\n\\noindent\n\\textbf{pull request}:\\\\\n"
        + f'"{_escape_latex(pr.title)}" (branch "{pr.branch}")\\\\\n'
        + f"\\url{{{pr.permalink}}}\\\\\n"
        + (
            "\\\\\n"
            + "\\textbf{inferred phase}:\\\\\n"
            + f"{phase:02} (\\url{{https://github.com/biostat821/ehr-utils-project/blob/main/phase{phase:02}.md}})\\\\\n"
            if phase in PHASES
            else ""
        )
    )


def write_document(
    username: str, pr_reports: list[tuple[int, PullRequest, DocumentSpec]]
) -> None:
    pages = [
        _create_page_header(phase, pr) + _construct_pr_report(pr_report)
        for phase, pr, pr_report in pr_reports
    ]
    document = textwrap.dedent(f"""
                \\documentclass{{article}}
                \\usepackage[includehead, includefoot, portrait, margin=0.5in]{{geometry}}
                \\usepackage{{booktabs}}
                \\usepackage[colorlinks=true, urlcolor=blue]{{hyperref}}
                \\usepackage{{longtable}}
                \\usepackage{{fancyhdr}}               
                \\usepackage{{lmodern}}
                \\usepackage[normalem]{{ulem}}
                \\newcommand{{\\setfont}}{{
                    \\ttfamily\\fontseries{{l}}\\selectfont\\small
                }}
                \\begin{{document}}
                \\pagestyle{{fancy}}
                \\fancyhead{{}} \\fancyfoot{{}}
                \\fancyhead[L]{{\\setfont {now().strftime("%Y-%m-%d %H:%M:%S")}}}
                \\fancyhead[C]{{\\setfont {username}}}
                \\fancyhead[R]{{\\setfont \\href{{https://github.com/biostat821/ehr-utils-project-status/tree/v1.0.0}}{{ehr-utils-project-status 1.0.0}}}}
                \\ttfamily
                \\fontseries{{l}}\\selectfont
                \\small""").strip()
    if not pages:
        document += "\nNo pull requests"
    document += "\n\\pagebreak\n".join(pages)
    document += "\n\\end{document}"
    document = document.replace("_", "\\_")
    with open(f"outputs/{username}.tex", "w") as f:
        f.write(document)


def _construct_pr_report(documentSpec: DocumentSpec) -> str:
    document = ""
    document += textwrap.dedent("""
                                \\setlength\\LTleft{0pt}
                                \\setlength\\LTright{0pt}
                                \\begin{longtable}{@{\\extracolsep{\\fill}}llr}
                                \\toprule
                                \\textbf{timestamp} & \\textbf{event} & \\textbf{status} \\\\
                                \\midrule
                                """).strip()
    for entry in documentSpec.entries:
        timestamp = entry.timestamp
        event_summary = entry.summary
        previous_state = entry.previous_state
        elapsed_in_state = entry.elapsed_in_state
        if elapsed_in_state:
            document += f"{timestamp} & {event_summary} & {previous_state.value} for {_pad_to(td_to_str(elapsed_in_state), 17)} \\\\\n"
        else:
            document += f"{timestamp} & {event_summary} & \\\\\n"
    document += "\\midrule\n"
    document += f"&& under development for {_pad_to(td_to_str(documentSpec.total_under_development_duration), 17)} \\\\\n"
    document += f"&& under review for {_pad_to(td_to_str(documentSpec.total_under_review_duration), 17)} \\\\\n"
    if documentSpec.late_by:
        document += f"&& late by {_pad_to(td_to_str(documentSpec.late_by), 17)} \\\\\n"
    if documentSpec.points_deducted is not None:
        document += f"&& \\textbf{{points deducted}}: \\textbf{{{_pad_to(documentSpec.points_deducted, 17)}}} \\\\\n"
    document += textwrap.dedent("""
                                \\bottomrule
                                \\end{longtable}
                                """).strip()
    return document
