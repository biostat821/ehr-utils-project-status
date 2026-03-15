# ehr-utils-project-status

## Installation

```bash
pip install ehr_utils_project_status@git+https://github.com/biostat821/ehr-utils-project-status
```

## Generating reports

```bash
./generate_reports.sh students.csv
```

## Adding as a workflow

1. Copy [`report.yml`](report.yml) into your `.github/workflows/` folder.
2. Once it is committed to `main`, you can [trigger the workflow manually](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow) at any time.
3. The generated report can be downloaded when the [workflow run](https://docs.github.com/en/actions/how-tos/monitor-workflows/view-workflow-run-history) finishes. Note that you may need to refresh the page.
