# looker-skills

Reusable Looker skill for dashboard management with a deterministic CLI.

## Repository Layout

- `looker-dashboard-manager/SKILL.md`: skill metadata + usage workflow
- `looker-dashboard-manager/scripts/looker_dashboard_cli.py`: CLI implementation
- `looker-dashboard-manager/references/looker_api_quickref.md`: endpoint quick reference
- `looker-dashboard-manager/sample.env`: first-run credential template

## First-Time Setup

```bash
cd looker-dashboard-manager
cp sample.env .env
# edit .env and set real LOOKER_* values
```

The CLI only checks `.env` in `/Users/ankur/Projects/media/looker_skill/looker-dashboard-manager/.env`, prompts first-time users to create it when missing, and refuses to run with missing or placeholder credentials.
It does not auto-read `LOOKER_*` shell environment variables.

## Ownership Rules Enforced by the CLI

- Any user with access can `create` new dashboards.
- Only the dashboard creator can `update`, `move`, `delete`, `element-update`, or `element-requery`.
- Non-owners must `copy` a dashboard first and then modify their copy.

## Tile Render Validation

- After `create`, `copy`, `update`, `move`, `element-update`, and `element-requery`, the CLI automatically validates query-backed tiles.
- If any tile query fails, the command fails and returns per-tile error details.
- You can run `validate` manually for a dashboard health check.

## Security Hygiene

- `.env` is gitignored and not committed.
- `sample.env` is tracked so new users can configure credentials safely.
