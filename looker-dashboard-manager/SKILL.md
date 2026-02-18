---
name: looker-dashboard-manager
description: Manage Looker dashboards and dashboard tiles through the Looker API using client_id/client_secret authentication. Use when an agent needs to create/search/update/move/delete dashboards, inspect dashboard elements, or update tile query fields and titles for metric changes such as per-ticket productivity KPIs.
---

# Looker Dashboard Manager

Use this skill to manage Looker dashboards from the terminal through a deterministic Python CLI.

## Prerequisites

Create a local `.env` file in this skill folder (do not write secrets into source files):

```bash
cp sample.env .env
# then set real values in .env
```

Notes:
- `LOOKER_BASE_URL` can be either your instance root (`https://<instance>.cloud.looker.com`) or already include `/api/4.0`.
- This skill normalizes the URL and uses API 4.0 endpoints.
- The CLI auto-loads `.env` from this skill folder on every run.
- First run fails fast if credentials are missing or still using placeholder values from `sample.env`.

## Quick Workflow

1. Identify the dashboard with `list`, `search`, or `get`.
2. If you are not the dashboard creator, run `copy` first and work on the copied dashboard.
3. Inspect tiles with `elements` and `element-get`.
4. Change dashboard-level metadata with `create`, `update`, `move`, or `delete`, or change tile-level metrics with `element-requery` / `element-update`.
5. Re-check state with `get`, `elements`, or `element-get`.

## Command Runner

Run all operations via:

```bash
python3 scripts/looker_dashboard_cli.py <command> [flags]
```

Available commands:
- `list` - list active dashboards
- `search` - search dashboards by title/description/folder/deleted status
- `get` - fetch full details for one dashboard
- `create` - create dashboard (requires `--title` and `--space-id`)
- `copy` - copy an existing dashboard so the current authenticated user owns the copy
- `update` - patch dashboard fields
- `move` - move dashboard to another folder
- `delete` - delete dashboard (requires `--yes`)
- `elements` - list dashboard elements with IDs, placement, and query fields
- `element-get` - fetch one dashboard element (includes embedded tile query)
- `element-update` - patch an element title/query/text
- `element-requery` - clone a tile query with a new field list and attach it back to the tile

Useful flags:
- `--dry-run` prints the API call that would be made without making network requests.
- `--raw` prints compact JSON.
- `--base-url`, `--client-id`, `--client-secret` override env vars for one run.
- `element-requery --dynamic-fields-json` allows table calculations/custom fields when the needed metric is not a native LookML field.

Ownership guardrails:
- `create` is allowed for any user with folder create access.
- `update`, `move`, `delete`, `element-update`, and `element-requery` are allowed only when the authenticated user is the dashboard creator.
- If the user is not the creator, the CLI returns a clear error and instructs to use `copy`.

Examples:

```bash
# Verify auth + connectivity by listing dashboards
python3 scripts/looker_dashboard_cli.py list

# Create a dashboard
python3 scripts/looker_dashboard_cli.py create \
  --title "Executive KPI Board" \
  --space-id "42" \
  --description "Weekly KPI snapshot"

# Copy a shared dashboard before editing (non-owner workflow)
python3 scripts/looker_dashboard_cli.py copy \
  --dashboard-id "123" \
  --folder-id "42"

# Move dashboard to another folder
python3 scripts/looker_dashboard_cli.py move \
  --dashboard-id "123" \
  --folder-id "88"

# Inspect tiles in a dashboard (find Productivity section element IDs)
python3 scripts/looker_dashboard_cli.py elements --dashboard-id "4170"

# Update one tile to a per-ticket metric
python3 scripts/looker_dashboard_cli.py element-requery \
  --element-id "46606" \
  --fields-csv "jira_instavails_scorecard_period_v1.period_start_week,jira_instavails_scorecard_period_v1.comments_per_touched_ticket" \
  --title "Comments per Touched Ticket (Weekly Productivity)" \
  --run-smoke-test

# Update one tile to a custom calculated metric (comments per final delivered ticket)
python3 scripts/looker_dashboard_cli.py element-requery \
  --element-id "46606" \
  --fields-csv "jira_instavails_scorecard_period_v1.period_start_week,jira_instavails_scorecard_period_v1.comments_posted,jira_instavails_scorecard_period_v1.tickets_moved_to_final_deliverables,comments_per_final_delivered_ticket" \
  --dynamic-fields-json '[{"table_calculation":"comments_per_final_delivered_ticket","label":"Comments per Final Delivered Ticket","expression":"if(${jira_instavails_scorecard_period_v1.tickets_moved_to_final_deliverables}=0, null, ${jira_instavails_scorecard_period_v1.comments_posted} / ${jira_instavails_scorecard_period_v1.tickets_moved_to_final_deliverables})","value_format_name":"decimal_2"}]' \
  --title "Comments per Final Delivered Ticket (Weekly Productivity)" \
  --run-smoke-test

# Update one tile title or body text directly
python3 scripts/looker_dashboard_cli.py element-update \
  --element-id "46614" \
  --title "Section Metric Definitions"

# Safe delete with explicit confirmation
python3 scripts/looker_dashboard_cli.py delete \
  --dashboard-id "123" \
  --yes
```

## References

Read `references/looker_api_quickref.md` for endpoint details, required fields, and common failure modes.
