#!/usr/bin/env python3
"""CLI for managing Looker dashboards through the Looker API."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse, urlunparse
from urllib.request import Request, urlopen

DEFAULT_FIELDS = "id,title,description,folder_id,slug,url,preferred_viewer,updated_at"
DEFAULT_ENV_FILE_NAME = ".env"
SAMPLE_ENV_FILE_NAME = "sample.env"
ENV_REQUIRED_KEYS = ("LOOKER_BASE_URL", "LOOKER_CLIENT_ID", "LOOKER_CLIENT_SECRET")
ENV_PLACEHOLDER_VALUES = {
    "LOOKER_BASE_URL": {
        "https://your-instance.cloud.looker.com",
        "https://your-instance.cloud.looker.com/api/4.0",
    },
    "LOOKER_CLIENT_ID": {"your_looker_client_id"},
    "LOOKER_CLIENT_SECRET": {"your_looker_client_secret"},
}


class LookerAPIError(RuntimeError):
    """Error returned by Looker API calls."""

    def __init__(self, message: str, status: Optional[int] = None, details: Any = None):
        super().__init__(message)
        self.status = status
        self.details = details


def resolve_env_path() -> Path:
    return Path(__file__).resolve().parents[1] / DEFAULT_ENV_FILE_NAME


def strip_wrapping_quotes(value: str) -> str:
    stripped = value.strip()
    if len(stripped) >= 2 and stripped[0] == stripped[-1] and stripped[0] in {'"', "'"}:
        return stripped[1:-1]
    return stripped


def load_env_file(env_path: Path) -> Dict[str, str]:
    values: Dict[str, str] = {}
    if not env_path.is_file():
        return values

    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("export "):
            stripped = stripped[len("export ") :].strip()
        key, separator, value = stripped.partition("=")
        if separator != "=":
            continue
        env_key = key.strip()
        if not env_key:
            continue
        env_value = strip_wrapping_quotes(value)
        values[env_key] = env_value
    return values


def timeout_default_from_env(env_values: Dict[str, str]) -> int:
    raw = strip_wrapping_quotes(env_values.get("LOOKER_TIMEOUT_SECONDS", "")).strip()
    if not raw:
        return 30
    try:
        return int(raw)
    except ValueError:
        return 30


def is_missing_or_placeholder(value: Optional[str], key: str) -> bool:
    if value is None:
        return True
    normalized = strip_wrapping_quotes(value).strip()
    if not normalized:
        return True
    placeholder_values = ENV_PLACEHOLDER_VALUES.get(key, set())
    return normalized in placeholder_values


def enforce_credential_requirements(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
    env_path: Path,
) -> None:
    values = {
        "LOOKER_BASE_URL": args.base_url,
        "LOOKER_CLIENT_ID": args.client_id,
        "LOOKER_CLIENT_SECRET": args.client_secret,
    }
    required_keys = ["LOOKER_BASE_URL"] if args.dry_run else list(ENV_REQUIRED_KEYS)
    missing = [key for key in required_keys if is_missing_or_placeholder(values.get(key), key)]
    if missing:
        sample_env_path = env_path.parent / SAMPLE_ENV_FILE_NAME
        parser.error(
            "Missing or placeholder Looker credentials. "
            f"Set real values for {', '.join(missing)} in {env_path}. "
            f"Use {sample_env_path} as the template for first-time setup."
        )


def enforce_local_env_file_exists(parser: argparse.ArgumentParser, env_path: Path) -> None:
    if env_path.is_file():
        return
    sample_env_path = env_path.parent / SAMPLE_ENV_FILE_NAME
    parser.error(
        "First-time setup required. "
        f"Local .env not found at {env_path}. "
        f"Create it first: cp {sample_env_path} {env_path}"
    )


def parse_bool(value: str) -> bool:
    mapping = {
        "1": True,
        "0": False,
        "true": True,
        "false": False,
        "yes": True,
        "no": False,
        "y": True,
        "n": False,
    }
    lowered = value.strip().lower()
    if lowered not in mapping:
        raise argparse.ArgumentTypeError(f"Invalid boolean value: {value}")
    return mapping[lowered]


def parse_csv_list(value: str) -> list[str]:
    items = [item.strip() for item in value.split(",")]
    filtered = [item for item in items if item]
    if not filtered:
        raise argparse.ArgumentTypeError("Expected a comma-separated list with at least one value")
    return filtered


def decode_json_or_text(raw: bytes) -> Any:
    text = raw.decode("utf-8", errors="replace").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def normalize_api_base(base_url: str) -> str:
    value = base_url.strip()
    if not value:
        raise ValueError("LOOKER_BASE_URL is required")
    if "://" not in value:
        value = f"https://{value}"

    parsed = urlparse(value)
    if not parsed.netloc:
        raise ValueError(
            f"Invalid LOOKER_BASE_URL: {base_url!r}. Use a value like https://your-instance.cloud.looker.com"
        )

    path = parsed.path.rstrip("/")
    if not path:
        path = "/api/4.0"
    elif not path.endswith("/api/4.0"):
        path = f"{path}/api/4.0"

    return urlunparse((parsed.scheme, parsed.netloc, path, "", "", ""))


def compact_dict(data: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in data.items() if v is not None}


def query_dict(data: Dict[str, Any]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for key, value in data.items():
        if value is None:
            continue
        if isinstance(value, bool):
            out[key] = "true" if value else "false"
        else:
            out[key] = str(value)
    return out


class LookerClient:
    def __init__(
        self,
        api_base: str,
        client_id: str,
        client_secret: str,
        timeout_seconds: int = 30,
        dry_run: bool = False,
    ):
        self.api_base = api_base.rstrip("/")
        self.client_id = client_id
        self.client_secret = client_secret
        self.timeout_seconds = timeout_seconds
        self.dry_run = dry_run
        self.access_token: Optional[str] = None

    def login(self) -> Any:
        if self.dry_run:
            self.access_token = "dry-run-token"
            return {
                "dry_run": True,
                "method": "POST",
                "url": f"{self.api_base}/login?client_id=<redacted>&client_secret=<redacted>",
                "body": {"client_id": "<redacted>", "client_secret": "<redacted>"},
            }

        result = self._request(
            method="POST",
            path="/login",
            query=query_dict(
                {
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                }
            ),
            auth_required=False,
            expected_status={200},
        )
        if not isinstance(result, dict) or "access_token" not in result:
            raise LookerAPIError("Login did not return an access_token", details=result)
        self.access_token = result["access_token"]
        return result

    def _request(
        self,
        method: str,
        path: str,
        *,
        query: Optional[Dict[str, str]] = None,
        json_body: Optional[Dict[str, Any]] = None,
        form_data: Optional[Dict[str, str]] = None,
        headers: Optional[Dict[str, str]] = None,
        auth_required: bool = True,
        expected_status: Optional[set[int]] = None,
    ) -> Any:
        if expected_status is None:
            expected_status = {200}

        url = f"{self.api_base}{path}"
        if query:
            url = f"{url}?{urlencode(query, doseq=True)}"

        req_headers: Dict[str, str] = {"Accept": "application/json"}
        if headers:
            req_headers.update(headers)

        body: Optional[bytes] = None
        if json_body is not None:
            req_headers["Content-Type"] = "application/json"
            body = json.dumps(json_body).encode("utf-8")
        elif form_data is not None:
            req_headers["Content-Type"] = "application/x-www-form-urlencoded"
            body = urlencode(form_data).encode("utf-8")

        if auth_required:
            if not self.access_token:
                self.login()
            req_headers["Authorization"] = f"token {self.access_token}"

        if self.dry_run:
            sanitized_headers = dict(req_headers)
            if "Authorization" in sanitized_headers:
                sanitized_headers["Authorization"] = "token <redacted>"
            safe_body: Any
            if json_body is not None:
                safe_body = json_body
            elif form_data is not None:
                safe_body = {k: "<redacted>" for k in form_data}
            else:
                safe_body = None
            return {
                "dry_run": True,
                "method": method.upper(),
                "url": url,
                "headers": sanitized_headers,
                "body": safe_body,
            }

        request = Request(url=url, data=body, method=method.upper(), headers=req_headers)
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                status = response.getcode()
                raw = response.read()
        except HTTPError as error:
            details = decode_json_or_text(error.read())
            raise LookerAPIError(
                f"{method.upper()} {path} failed with HTTP {error.code}",
                status=error.code,
                details=details,
            ) from error
        except URLError as error:
            raise LookerAPIError(f"{method.upper()} {path} failed: {error.reason}") from error

        if status not in expected_status:
            raise LookerAPIError(
                f"{method.upper()} {path} returned unexpected status {status}",
                status=status,
                details=decode_json_or_text(raw),
            )
        if status == 204:
            return None
        return decode_json_or_text(raw)

    def list_dashboards(self, fields: str) -> Any:
        return self._request(
            "GET",
            "/dashboards",
            query=query_dict({"fields": fields}),
            expected_status={200},
        )

    def search_dashboards(
        self,
        *,
        title: Optional[str],
        description: Optional[str],
        folder_id: Optional[str],
        deleted: Optional[bool],
        fields: str,
    ) -> Any:
        return self._request(
            "GET",
            "/dashboards/search",
            query=query_dict(
                {
                    "title": title,
                    "description": description,
                    "folder_id": folder_id,
                    "deleted": deleted,
                    "fields": fields,
                }
            ),
            expected_status={200},
        )

    def get_dashboard(self, dashboard_id: str, fields: Optional[str]) -> Any:
        return self._request(
            "GET",
            f"/dashboards/{dashboard_id}",
            query=query_dict({"fields": fields}),
            expected_status={200},
        )

    def get_current_user(self, fields: str = "id,display_name,email") -> Any:
        return self._request(
            "GET",
            "/user",
            query=query_dict({"fields": fields}),
            expected_status={200},
        )

    def create_dashboard(self, payload: Dict[str, Any]) -> Any:
        return self._request(
            "POST",
            "/dashboards",
            json_body=payload,
            expected_status={200},
        )

    def copy_dashboard(self, dashboard_id: str, folder_id: Optional[str]) -> Any:
        return self._request(
            "POST",
            f"/dashboards/{dashboard_id}/copy",
            query=query_dict({"folder_id": folder_id}),
            expected_status={200},
        )

    def update_dashboard(self, dashboard_id: str, payload: Dict[str, Any]) -> Any:
        return self._request(
            "PATCH",
            f"/dashboards/{dashboard_id}",
            json_body=payload,
            expected_status={200},
        )

    def move_dashboard(self, dashboard_id: str, folder_id: str) -> Any:
        return self._request(
            "PATCH",
            f"/dashboards/{dashboard_id}/move",
            query=query_dict({"folder_id": folder_id}),
            expected_status={200},
        )

    def delete_dashboard(self, dashboard_id: str) -> Any:
        return self._request(
            "DELETE",
            f"/dashboards/{dashboard_id}",
            expected_status={204},
        )

    def get_dashboard_element(self, element_id: str) -> Any:
        return self._request(
            "GET",
            f"/dashboard_elements/{element_id}",
            expected_status={200},
        )

    def update_dashboard_element(self, element_id: str, payload: Dict[str, Any]) -> Any:
        return self._request(
            "PATCH",
            f"/dashboard_elements/{element_id}",
            json_body=payload,
            expected_status={200},
        )

    def create_query(self, payload: Dict[str, Any]) -> Any:
        return self._request(
            "POST",
            "/queries",
            json_body=payload,
            expected_status={200},
        )

    def run_query(self, query_id: str, result_format: str = "json") -> Any:
        return self._request(
            "GET",
            f"/queries/{query_id}/run/{result_format}",
            expected_status={200},
        )

    def requery_dashboard_element(
        self,
        *,
        element_id: str,
        fields: list[str],
        title: Optional[str],
        dynamic_fields: Optional[str],
        run_smoke_test: bool,
        source_element: Optional[Dict[str, Any]] = None,
    ) -> Any:
        element = source_element or self.get_dashboard_element(element_id)
        source_query = (
            (element.get("result_maker") or {}).get("query")
            or element.get("query")
            or None
        )
        if not source_query:
            raise LookerAPIError(
                f"Dashboard element {element_id} has no query; only query-based elements can be requeried."
            )

        new_query_payload: Dict[str, Any] = compact_dict(
            {
                "model": source_query.get("model"),
                "view": source_query.get("view"),
                "fields": fields,
                "filters": source_query.get("filters"),
                "filter_expression": source_query.get("filter_expression"),
                "sorts": source_query.get("sorts"),
                "limit": source_query.get("limit"),
                "column_limit": source_query.get("column_limit"),
                "total": source_query.get("total"),
                "row_total": source_query.get("row_total"),
                "subtotals": source_query.get("subtotals"),
                "query_timezone": source_query.get("query_timezone"),
                "vis_config": source_query.get("vis_config"),
                "dynamic_fields": dynamic_fields
                if dynamic_fields is not None
                else source_query.get("dynamic_fields"),
                "fill_fields": source_query.get("fill_fields"),
                "pivots": source_query.get("pivots"),
            }
        )
        new_query = self.create_query(new_query_payload)
        new_query_id = str(new_query.get("id"))
        if not new_query_id:
            raise LookerAPIError("Creating the new query did not return an id", details=new_query)

        if run_smoke_test:
            self.run_query(new_query_id, result_format="json")

        update_payload = compact_dict({"query_id": new_query_id, "title": title})
        updated_element = self.update_dashboard_element(element_id, update_payload)

        live_query_id = (
            ((updated_element.get("result_maker") or {}).get("query") or {}).get("id")
            or updated_element.get("query_id")
        )
        return {
            "element_id": str(updated_element.get("id", element_id)),
            "title": updated_element.get("title"),
            "old_query_id": source_query.get("id"),
            "new_query_id": new_query_id,
            "live_query_id": live_query_id,
            "fields": fields,
            "smoke_test_ran": run_smoke_test,
        }


def summarize_dashboard_elements(dashboard: Dict[str, Any]) -> Dict[str, Any]:
    elements = dashboard.get("dashboard_elements") or []
    layouts = dashboard.get("dashboard_layouts") or []
    active_layout = next((layout for layout in layouts if layout.get("active")), None)
    if not active_layout and layouts:
        active_layout = layouts[0]

    placement_by_element_id: Dict[str, Dict[str, Any]] = {}
    if active_layout:
        for component in active_layout.get("dashboard_layout_components") or []:
            element_id = str(component.get("dashboard_element_id"))
            placement_by_element_id[element_id] = {
                "row": component.get("row"),
                "column": component.get("column"),
                "width": component.get("width"),
                "height": component.get("height"),
            }

    summary_elements = []
    for element in elements:
        element_id = str(element.get("id"))
        source_query = (element.get("result_maker") or {}).get("query") or element.get("query") or {}
        query_id = source_query.get("id") or element.get("query_id")
        placement = placement_by_element_id.get(element_id, {})
        summary_elements.append(
            {
                "id": element_id,
                "type": element.get("type"),
                "title": element.get("title"),
                "query_id": query_id,
                "model": source_query.get("model"),
                "view": source_query.get("view"),
                "fields": source_query.get("fields"),
                "row": placement.get("row"),
                "column": placement.get("column"),
                "width": placement.get("width"),
                "height": placement.get("height"),
            }
        )

    def sort_key(item: Dict[str, Any]) -> tuple[int, int, str]:
        row = item.get("row")
        col = item.get("column")
        row_key = int(row) if isinstance(row, int) else 10**9
        col_key = int(col) if isinstance(col, int) else 10**9
        title = item.get("title") or ""
        return (row_key, col_key, title)

    summary_elements.sort(key=sort_key)
    return {
        "dashboard_id": str(dashboard.get("id")),
        "dashboard_title": dashboard.get("title"),
        "element_count": len(summary_elements),
        "elements": summary_elements,
    }


def extract_element_query_id(element: Dict[str, Any]) -> Optional[str]:
    source_query = (element.get("result_maker") or {}).get("query") or element.get("query") or {}
    query_id = source_query.get("id") or element.get("query_id")
    if query_id is None:
        return None
    query_id_text = str(query_id).strip()
    return query_id_text or None


def validate_dashboard_tiles(client: LookerClient, dashboard_id: str) -> Dict[str, Any]:
    dashboard = client.get_dashboard(dashboard_id, fields=None)
    elements = dashboard.get("dashboard_elements") or []

    validated_tiles = []
    skipped_tiles = []
    tile_errors = []

    for element in elements:
        raw_element_id = element.get("id")
        element_id = str(raw_element_id) if raw_element_id is not None else ""
        query_id = extract_element_query_id(element)
        tile_summary = {
            "element_id": element_id,
            "title": element.get("title"),
            "type": element.get("type"),
            "query_id": query_id,
        }
        if not query_id:
            skipped_tiles.append(tile_summary)
            continue
        try:
            client.run_query(query_id, result_format="json")
            validated_tiles.append(tile_summary)
        except LookerAPIError as error:
            tile_errors.append(
                {
                    **tile_summary,
                    "error": str(error),
                    "status": error.status,
                    "details": error.details,
                }
            )

    return {
        "dashboard_id": str(dashboard.get("id", dashboard_id)),
        "dashboard_title": dashboard.get("title"),
        "total_tiles": len(elements),
        "query_backed_tiles": len(validated_tiles) + len(tile_errors),
        "validated_tile_count": len(validated_tiles),
        "skipped_tile_count": len(skipped_tiles),
        "error_count": len(tile_errors),
        "errors": tile_errors,
    }


def ensure_dashboard_tiles_healthy(client: LookerClient, dashboard_id: str) -> Dict[str, Any]:
    validation = validate_dashboard_tiles(client, dashboard_id)
    if validation["error_count"] > 0:
        raise LookerAPIError(
            f"Dashboard {dashboard_id} has tile query errors after the change.",
            status=422,
            details=validation,
        )
    return validation


def get_authenticated_user_id(client: LookerClient) -> str:
    user = client.get_current_user(fields="id,display_name,email")
    user_id = str(user.get("id") or "").strip()
    if not user_id:
        raise LookerAPIError("Could not determine the authenticated Looker user id.", details=user)
    return user_id


def enforce_dashboard_owner(client: LookerClient, dashboard_id: str, action: str) -> Dict[str, Any]:
    dashboard = client.get_dashboard(dashboard_id, fields="id,title,user_id")
    owner_id = str(dashboard.get("user_id") or "").strip()
    current_user_id = get_authenticated_user_id(client)
    if not owner_id:
        raise LookerAPIError(
            f"Cannot {action} dashboard {dashboard_id} because the owner is unavailable. "
            "Copy it first, then manage your copy.",
            status=403,
            details={
                "dashboard_id": dashboard_id,
                "dashboard_title": dashboard.get("title"),
                "current_user_id": current_user_id,
            },
        )
    if owner_id != current_user_id:
        raise LookerAPIError(
            f"Cannot {action} dashboard {dashboard_id}; only the creator can manage this dashboard. "
            "Run `copy` first to make an editable clone owned by your user.",
            status=403,
            details={
                "dashboard_id": dashboard_id,
                "dashboard_title": dashboard.get("title"),
                "owner_user_id": owner_id,
                "current_user_id": current_user_id,
            },
        )
    return dashboard


def enforce_element_owner(client: LookerClient, element_id: str, action: str) -> Dict[str, Any]:
    element = client.get_dashboard_element(element_id)
    dashboard_id = element.get("dashboard_id")
    if dashboard_id is None:
        raise LookerAPIError(
            f"Cannot {action} element {element_id}; dashboard_id is missing from the element payload.",
            status=400,
            details=element,
        )
    enforce_dashboard_owner(client, str(dashboard_id), action)
    return element


def build_parser(env_values: Dict[str, str]) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage Looker dashboards using the Looker API.")
    parser.add_argument(
        "--base-url",
        default=env_values.get("LOOKER_BASE_URL"),
        help="Looker base URL (instance URL or /api/4.0 URL). Defaults to value in local .env.",
    )
    parser.add_argument(
        "--client-id",
        default=env_values.get("LOOKER_CLIENT_ID"),
        help="Looker API client_id. Defaults to value in local .env.",
    )
    parser.add_argument(
        "--client-secret",
        default=env_values.get("LOOKER_CLIENT_SECRET"),
        help="Looker API client_secret. Defaults to value in local .env.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=timeout_default_from_env(env_values),
        help="HTTP timeout in seconds. Defaults to LOOKER_TIMEOUT_SECONDS in local .env or 30.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print request details without making network calls.",
    )
    parser.add_argument(
        "--raw",
        action="store_true",
        help="Print compact JSON (single line).",
    )
    parser.add_argument(
        "--skip-dashboard-validation",
        action="store_true",
        help="Skip automatic dashboard tile validation after mutating commands.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    list_cmd = subparsers.add_parser("list", help="List dashboards.")
    list_cmd.add_argument("--fields", default=DEFAULT_FIELDS)

    search_cmd = subparsers.add_parser("search", help="Search dashboards.")
    search_cmd.add_argument("--title")
    search_cmd.add_argument("--description")
    search_cmd.add_argument("--folder-id")
    search_cmd.add_argument("--deleted", type=parse_bool)
    search_cmd.add_argument("--fields", default=DEFAULT_FIELDS)

    get_cmd = subparsers.add_parser("get", help="Get a dashboard by ID.")
    get_cmd.add_argument("--dashboard-id", required=True)
    get_cmd.add_argument("--fields")

    create_cmd = subparsers.add_parser("create", help="Create a dashboard.")
    create_cmd.add_argument("--title", required=True)
    create_cmd.add_argument("--space-id", required=True)
    create_cmd.add_argument("--description")
    create_cmd.add_argument("--hidden", type=parse_bool)
    create_cmd.add_argument("--preferred-viewer", choices=["dashboards", "dashboards-next"])
    create_cmd.add_argument("--query-timezone")

    copy_cmd = subparsers.add_parser(
        "copy",
        help="Copy a dashboard so the current user owns and can modify the copy.",
    )
    copy_cmd.add_argument("--dashboard-id", required=True)
    copy_cmd.add_argument(
        "--folder-id",
        help="Optional destination folder for the copied dashboard. Defaults to Looker API behavior.",
    )

    update_cmd = subparsers.add_parser("update", help="Update a dashboard.")
    update_cmd.add_argument("--dashboard-id", required=True)
    update_cmd.add_argument("--title")
    update_cmd.add_argument("--description")
    update_cmd.add_argument("--space-id")
    update_cmd.add_argument("--hidden", type=parse_bool)
    update_cmd.add_argument("--preferred-viewer", choices=["dashboards", "dashboards-next"])
    update_cmd.add_argument("--query-timezone")

    move_cmd = subparsers.add_parser("move", help="Move dashboard to a folder.")
    move_cmd.add_argument("--dashboard-id", required=True)
    move_cmd.add_argument("--folder-id", required=True)

    delete_cmd = subparsers.add_parser("delete", help="Delete a dashboard.")
    delete_cmd.add_argument("--dashboard-id", required=True)
    delete_cmd.add_argument(
        "--yes",
        action="store_true",
        help="Required confirmation flag to execute deletion.",
    )

    elements_cmd = subparsers.add_parser(
        "elements",
        help="List dashboard elements with ids, placement, and query fields.",
    )
    elements_cmd.add_argument("--dashboard-id", required=True)

    validate_cmd = subparsers.add_parser(
        "validate",
        help="Run all query-backed tiles in a dashboard and fail if any tile errors.",
    )
    validate_cmd.add_argument("--dashboard-id", required=True)

    element_get_cmd = subparsers.add_parser(
        "element-get",
        help="Get one dashboard element by id (includes embedded query for vis tiles).",
    )
    element_get_cmd.add_argument("--element-id", required=True)

    element_update_cmd = subparsers.add_parser(
        "element-update",
        help="Patch one dashboard element (title/query_id/body/subtitle).",
    )
    element_update_cmd.add_argument("--element-id", required=True)
    element_update_cmd.add_argument("--title")
    element_update_cmd.add_argument("--query-id")
    element_update_cmd.add_argument("--body-text")
    element_update_cmd.add_argument("--subtitle-text")

    element_requery_cmd = subparsers.add_parser(
        "element-requery",
        help=(
            "Clone an element query with a new field list and attach it back to the element. "
            "Use for tile-level metric updates."
        ),
    )
    element_requery_cmd.add_argument("--element-id", required=True)
    element_requery_cmd.add_argument(
        "--fields-csv",
        required=True,
        type=parse_csv_list,
        help=(
            "Comma-separated field list (for example: "
            "view.period_start_week,view.comments_per_touched_ticket)"
        ),
    )
    element_requery_cmd.add_argument("--title")
    element_requery_cmd.add_argument(
        "--dynamic-fields-json",
        help=(
            "Optional raw JSON string for Looker dynamic_fields (table calculations/custom fields). "
            "Use when introducing calculated metrics not present as native LookML measures."
        ),
    )
    element_requery_cmd.add_argument(
        "--run-smoke-test",
        action="store_true",
        help="Run the new query once before attaching it to the tile.",
    )

    return parser


def print_json(payload: Any, raw: bool) -> None:
    if raw:
        print(json.dumps(payload, separators=(",", ":"), default=str))
        return
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))


def normalize_dynamic_fields_json(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as error:
        raise ValueError(f"--dynamic-fields-json is not valid JSON: {error}") from error
    if not isinstance(parsed, list):
        raise ValueError("--dynamic-fields-json must be a JSON array")
    return json.dumps(parsed)


def main() -> int:
    env_path = resolve_env_path()
    env_values = load_env_file(env_path)
    parser = build_parser(env_values)
    args = parser.parse_args()

    enforce_local_env_file_exists(parser, env_path)
    enforce_credential_requirements(parser, args, env_path)

    try:
        client = LookerClient(
            api_base=normalize_api_base(args.base_url),
            client_id=args.client_id or "",
            client_secret=args.client_secret or "",
            timeout_seconds=args.timeout,
            dry_run=args.dry_run,
        )

        command = args.command
        dashboard_id_to_validate: Optional[str] = None
        auto_validate_commands = {
            "create",
            "copy",
            "update",
            "move",
            "element-update",
            "element-requery",
        }
        if command == "list":
            result = client.list_dashboards(fields=args.fields)
        elif command == "search":
            result = client.search_dashboards(
                title=args.title,
                description=args.description,
                folder_id=args.folder_id,
                deleted=args.deleted,
                fields=args.fields,
            )
        elif command == "get":
            result = client.get_dashboard(args.dashboard_id, args.fields)
        elif command == "create":
            payload = compact_dict(
                {
                    "title": args.title,
                    "space_id": args.space_id,
                    "description": args.description,
                    "hidden": args.hidden,
                    "preferred_viewer": args.preferred_viewer,
                    "query_timezone": args.query_timezone,
                }
            )
            result = client.create_dashboard(payload)
            if isinstance(result, dict):
                result_id = result.get("id")
                if result_id is not None:
                    dashboard_id_to_validate = str(result_id)
        elif command == "copy":
            result = client.copy_dashboard(args.dashboard_id, args.folder_id)
            if isinstance(result, dict):
                result_id = result.get("id")
                if result_id is not None:
                    dashboard_id_to_validate = str(result_id)
        elif command == "update":
            payload = compact_dict(
                {
                    "title": args.title,
                    "space_id": args.space_id,
                    "description": args.description,
                    "hidden": args.hidden,
                    "preferred_viewer": args.preferred_viewer,
                    "query_timezone": args.query_timezone,
                }
            )
            if not payload:
                parser.error("update requires at least one field to change")
            enforce_dashboard_owner(client, args.dashboard_id, action="update")
            result = client.update_dashboard(args.dashboard_id, payload)
            dashboard_id_to_validate = str(args.dashboard_id)
        elif command == "move":
            enforce_dashboard_owner(client, args.dashboard_id, action="move")
            result = client.move_dashboard(args.dashboard_id, args.folder_id)
            dashboard_id_to_validate = str(args.dashboard_id)
        elif command == "delete":
            if not args.yes:
                parser.error("delete requires --yes")
            enforce_dashboard_owner(client, args.dashboard_id, action="delete")
            api_response = client.delete_dashboard(args.dashboard_id)
            result = {
                "deleted": True,
                "dashboard_id": args.dashboard_id,
                "api_response": api_response,
            }
        elif command == "elements":
            dashboard = client.get_dashboard(args.dashboard_id, fields=None)
            result = summarize_dashboard_elements(dashboard)
        elif command == "validate":
            result = ensure_dashboard_tiles_healthy(client, args.dashboard_id)
        elif command == "element-get":
            result = client.get_dashboard_element(args.element_id)
        elif command == "element-update":
            payload = compact_dict(
                {
                    "title": args.title,
                    "query_id": args.query_id,
                    "body_text": args.body_text,
                    "subtitle_text": args.subtitle_text,
                }
            )
            if not payload:
                parser.error(
                    "element-update requires at least one change flag "
                    "(--title/--query-id/--body-text/--subtitle-text)"
                )
            source_element = enforce_element_owner(client, args.element_id, action="update")
            result = client.update_dashboard_element(args.element_id, payload)
            source_dashboard_id = source_element.get("dashboard_id")
            if source_dashboard_id is not None:
                dashboard_id_to_validate = str(source_dashboard_id)
        elif command == "element-requery":
            if args.dry_run:
                parser.error(
                    "element-requery does not support --dry-run because it needs "
                    "to read the source query and create a new query id"
                )
            dynamic_fields = normalize_dynamic_fields_json(args.dynamic_fields_json)
            source_element = enforce_element_owner(client, args.element_id, action="requery")
            result = client.requery_dashboard_element(
                element_id=args.element_id,
                fields=args.fields_csv,
                title=args.title,
                dynamic_fields=dynamic_fields,
                run_smoke_test=args.run_smoke_test,
                source_element=source_element,
            )
            source_dashboard_id = source_element.get("dashboard_id")
            if source_dashboard_id is not None:
                dashboard_id_to_validate = str(source_dashboard_id)
        else:
            parser.error(f"Unsupported command: {command}")
            return 2

        if (
            command in auto_validate_commands
            and not args.skip_dashboard_validation
            and not args.dry_run
        ):
            if not dashboard_id_to_validate:
                raise LookerAPIError(
                    "Could not determine dashboard id for post-change tile validation.",
                    status=500,
                    details={"command": command},
                )
            dashboard_validation = ensure_dashboard_tiles_healthy(client, dashboard_id_to_validate)
            if isinstance(result, dict):
                result = {**result, "dashboard_validation": dashboard_validation}
            else:
                result = {"result": result, "dashboard_validation": dashboard_validation}

        print_json(result, raw=args.raw)
        return 0

    except LookerAPIError as error:
        payload = {"error": str(error)}
        if error.status is not None:
            payload["status"] = error.status
        if error.details is not None:
            payload["details"] = error.details
        print_json(payload, raw=False)
        return 1
    except ValueError as error:
        print_json({"error": str(error)}, raw=False)
        return 1


if __name__ == "__main__":
    sys.exit(main())
