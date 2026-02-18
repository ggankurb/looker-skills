# Looker Dashboard API Quick Reference

This skill uses Looker API 4.0 dashboard endpoints.

## Authentication

- Method: `POST /login`
- Auth style: query params `client_id` and `client_secret`
- Response: includes `access_token`
- All subsequent calls send `Authorization: token <access_token>`

## Dashboard Endpoints Used

- `GET /dashboards` list dashboards
- `GET /dashboards/search` search dashboards
- `GET /dashboards/{dashboard_id}` fetch one dashboard
- `POST /dashboards` create dashboard
- `POST /dashboards/{dashboard_id}/copy` clone dashboard to a user-owned copy
- `PATCH /dashboards/{dashboard_id}` update dashboard
- `PATCH /dashboards/{dashboard_id}/move` move dashboard to folder (`folder_id` query param)
- `DELETE /dashboards/{dashboard_id}` delete dashboard
- `GET /user` fetch authenticated user id for ownership checks
- `GET /dashboard_elements/{dashboard_element_id}` fetch one dashboard tile
- `PATCH /dashboard_elements/{dashboard_element_id}` update tile title/query/text
- `POST /queries` create (or deduplicate to existing) query objects
- `GET /queries/{query_id}/run/json` smoke-test query validity

## Required Fields

- Create dashboard: `title`, `space_id`

## Common Failure Modes

- `401`: invalid `client_id` / `client_secret`, or token expired
- `403`: authenticated but not authorized for target folder/dashboard
- `404`: dashboard ID, folder ID, element ID, or query ID not found
- `422`: invalid payload or missing required fields

## Notes

- The CLI normalizes `LOOKER_BASE_URL` to include `/api/4.0` when needed.
- The CLI auto-loads `.env` from the skill directory and blocks runs with placeholder credentials.
- Use `--dry-run` before any destructive change to inspect the outgoing request.
- The CLI enforces creator-only mutation for dashboards and dashboard elements.
- For calculated dashboard metrics (for example, ratios not yet modeled in LookML), use `element-requery --dynamic-fields-json` with a table calculation definition.
