#!/usr/bin/env python3
"""
Read-only Deepgram operations helper for explicit projects.

The script intentionally avoids third-party dependencies so Codex/Claude can run it
from any local workspace. It never prints the configured API key.
"""

from __future__ import annotations

from safety import SafeError, secret, scrub, api_url, request
import argparse
import datetime as dt
import json
import os
import pathlib
import stat
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


API_BASE = "https://api.deepgram.com/v1"
USER_AGENT = "deepgram-ops/1.0"


def eprint(*args: object) -> None:
    print(*args, file=sys.stderr)


def read_text_file(path: pathlib.Path) -> str | None:
    try:
        if not path.exists():
            return None
        value = path.read_text(encoding="utf-8").strip()
        return value or None
    except OSError:
        return None


def redact_secret(value):
    return "[REDACTED]"


def credential_source():
    return secret("DEEPGRAM_API_KEY", "DEEPGRAM_API_KEY_FILE"), "explicit environment/file"








def build_url(path, params=None):
    return api_url(API_BASE, path, params)


def api_get(path, params=None, timeout_s=30):
    key, _ = credential_source()
    status, value = request(build_url(path, params), timeout=timeout_s, headers={"Authorization": "Token " + key})
    return status, json.dumps(value)


def parse_json_response(status: int, body: str) -> Any:
    if not body.strip():
        return {}
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        if status >= 400:
            return {"error": body}
        raise


def print_response(status: int, body: str, raw: bool = False) -> None:
    if raw:
        print(body)
        return
    data = parse_json_response(status, body)
    print(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))


def parse_kv_pairs(pairs: list[str] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for pair in pairs or []:
        if "=" not in pair:
            raise ValueError(f"--param must be key=value, got: {pair}")
        key, value = pair.split("=", 1)
        key = key.strip()
        if not key:
            raise ValueError(f"--param key cannot be empty: {pair}")
        out[key] = value.strip()
    return out


def parse_json_arg(value: str | None, name: str) -> Any:
    if value is None:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{name} must be valid JSON: {exc}") from exc


def add_common_params(args: argparse.Namespace, params: dict[str, Any] | None = None) -> dict[str, Any]:
    out = dict(params or {})
    for name in ("start", "end", "limit", "page", "sort", "order"):
        value = getattr(args, name, None)
        if value is not None:
            out[name] = value
    for item in getattr(args, "param", None) or []:
        key, value = item.split("=", 1)
        out[key] = value
    return out


def looks_like_iso_date(value: str | None) -> bool:
    if not value:
        return True
    try:
        dt.date.fromisoformat(value)
        return True
    except ValueError:
        return False


def looks_like_deepgram_datetime(value: str | None) -> bool:
    if not value:
        return True
    if looks_like_iso_date(value):
        return True
    normalized = value.replace("Z", "+00:00")
    try:
        dt.datetime.fromisoformat(normalized)
        return True
    except ValueError:
        return False


def validate_dates(args: argparse.Namespace, *, allow_datetime: bool = False) -> None:
    if not args.start or not args.end:
        raise ValueError("Both --start and --end are required")
    start = dt.datetime.fromisoformat(args.start.replace("Z", "+00:00"))
    end = dt.datetime.fromisoformat(args.end.replace("Z", "+00:00"))
    if start.tzinfo is None: start = start.replace(tzinfo=dt.timezone.utc)
    if end.tzinfo is None: end = end.replace(tzinfo=dt.timezone.utc)
    if end <= start or end - start > dt.timedelta(days=31):
        raise ValueError("Choose an increasing date range of at most 31 days")
    if any(p.split("=", 1)[0].strip() in {"start", "end"} for p in getattr(args, "param", []) or []):
        raise ValueError("Date bounds cannot be overridden through --param")
    for name in ("start", "end"):
        value = getattr(args, name, None)
        if not value:
            continue
        is_valid = looks_like_deepgram_datetime(value) if allow_datetime else looks_like_iso_date(value)
        if not is_valid:
            expected = "YYYY-MM-DD or ISO datetime" if allow_datetime else "YYYY-MM-DD"
            raise ValueError(f"--{name} should be {expected}, got {value!r}")


def iter_records(data: Any) -> list[Any]:
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return []
    for key in ("results", "items", "requests", "balances", "purchases", "orders", "projects", "data"):
        value = data.get(key)
        if isinstance(value, list):
            return value
    return []


def sum_numeric(records: list[Any], candidate_keys: tuple[str, ...]) -> float | None:
    total = 0.0
    found = False
    for row in records:
        if not isinstance(row, dict):
            continue
        for key in candidate_keys:
            value = row.get(key)
            if isinstance(value, (int, float)):
                total += float(value)
                found = True
                break
    return total if found else None


def find_numeric(data: Any, candidate_keys: tuple[str, ...]) -> float | None:
    if isinstance(data, dict):
        for key in candidate_keys:
            value = data.get(key)
            if isinstance(value, (int, float)):
                return float(value)
        for value in data.values():
            found = find_numeric(value, candidate_keys)
            if found is not None:
                return found
    elif isinstance(data, list):
        for item in data:
            found = find_numeric(item, candidate_keys)
            if found is not None:
                return found
    return None


def compact_row(row: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    return {key: row.get(key) for key in keys if key in row}


def row_with_grouping(row: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    out = compact_row(row, keys)
    grouping = row.get("grouping")
    if isinstance(grouping, dict):
        for key, value in grouping.items():
            if key not in out:
                out[key] = value
    return out


def truncate_text(value: Any, max_len: int = 160) -> Any:
    if not isinstance(value, str) or len(value) <= max_len:
        return value
    return f"{value[:max_len]}..."


def compact_request_row(row: dict[str, Any]) -> dict[str, Any]:
    out = compact_row(row, ("request_id", "id", "created", "created_at", "path", "api_key_id", "code", "deployment", "status", "duration", "hours", "dollars"))
    if "path" in out:
        out["path"] = truncate_text(out["path"])
    return out


def print_summary(title: str, payload: dict[str, Any], rows: list[dict[str, Any]] | None = None) -> None:
    print(json.dumps({
        "summary": title,
        **payload,
        **({"rows": rows} if rows is not None else {}),
    }, ensure_ascii=False, indent=2, sort_keys=True))


def run_get(path: str, params: dict[str, Any] | None, args: argparse.Namespace) -> int:
    status, body = api_get(path, params=params)
    if status >= 400:
        eprint(f"HTTP {status}")
    print_response(status, body, raw=getattr(args, "raw", False))
    return 0 if status < 400 else 1





def cmd_doctor(args):
    credential_source()
    print(json.dumps({"credential_found": True, "network_checked": False}))
    return 0


def cmd_get(args: argparse.Namespace) -> int:
    params = parse_kv_pairs(args.param)
    return run_get(args.path, params=params or None, args=args)


def cmd_projects_list(args: argparse.Namespace) -> int:
    params = {"limit": args.limit} if args.limit else None
    status, body = api_get("/projects", params=params)
    data = parse_json_response(status, body)
    if status >= 400:
        eprint(f"HTTP {status}")
        print_response(status, body, raw=args.raw)
        return 1
    if args.summary:
        projects = iter_records(data)
        rows = []
        for project in projects:
            if isinstance(project, dict):
                rows.append(compact_row(project, ("project_id", "id", "name", "company", "created", "created_at")))
        print_summary("deepgram projects", {"count": len(rows)}, rows)
        return 0
    print_response(status, body, raw=args.raw)
    return 0


def cmd_projects_get(args: argparse.Namespace) -> int:
    return run_get(f"/projects/{args.project_id}", params=None, args=args)


def cmd_usage_breakdown(args: argparse.Namespace) -> int:
    validate_dates(args)
    params = {"start": args.start, "end": args.end}
    if args.tag:
        params["tag"] = args.tag
    if args.model:
        params["model"] = args.model
    if args.accessor:
        params["accessor"] = args.accessor
    if args.grouping:
        params["grouping"] = args.grouping
    params.update(parse_kv_pairs(args.param))
    status, body = api_get(f"/projects/{args.project_id}/usage/breakdown", params=params)
    data = parse_json_response(status, body)
    if status >= 400:
        eprint(f"HTTP {status}")
        print_response(status, body, raw=args.raw)
        return 1
    if args.summary:
        records = iter_records(data)
        total_hours = sum_numeric(records, ("total_hours", "hours"))
        if total_hours is None:
            total_hours = find_numeric(data, ("total_hours", "hours"))
        total_requests = sum_numeric(records, ("total_requests", "requests"))
        if total_requests is None:
            total_requests = find_numeric(data, ("total_requests", "requests"))
        total_tokens_in = sum_numeric(records, ("tokens_in",))
        total_tokens_out = sum_numeric(records, ("tokens_out",))
        total_characters = sum_numeric(records, ("tts_characters", "characters"))
        if total_characters is None:
            total_characters = find_numeric(data, ("total_characters", "tts_characters", "characters"))
        rows = [
            row_with_grouping(row, ("start", "end", "date", "model", "accessor", "deployment", "endpoint", "method", "feature_set", "line_item", "hours", "total_hours", "agent_hours", "requests", "tokens_in", "tokens_out", "tts_characters"))
            for row in records[: args.summary_limit]
            if isinstance(row, dict)
        ]
        print_summary("deepgram usage breakdown", {
            "project_id": args.project_id,
            "start": args.start,
            "end": args.end,
            "record_count": len(records),
            "total_hours": total_hours,
            "total_requests": total_requests,
            "total_tokens_in": total_tokens_in,
            "total_tokens_out": total_tokens_out,
            "total_characters": total_characters,
        }, rows)
        return 0
    print_response(status, body, raw=args.raw)
    return 0


def cmd_billing_breakdown(args: argparse.Namespace) -> int:
    validate_dates(args)
    params = {"start": args.start, "end": args.end}
    if args.grouping:
        grouping = parse_json_arg(args.grouping, "--grouping")
        if not isinstance(grouping, list) or not grouping or any(x not in ("accessor","deployment","line_item","tags") for x in grouping):
            raise ValueError("Billing grouping must be a JSON list of supported dimensions")
        params["grouping"] = grouping
    if args.accessor:
        params["accessor"] = args.accessor
    if args.deployment:
        params["deployment"] = args.deployment
    if args.line_item:
        params["line_item"] = args.line_item
    if args.tag:
        params["tag"] = args.tag
    params.update(parse_kv_pairs(args.param))
    status, body = api_get(f"/projects/{args.project_id}/billing/breakdown", params=params)
    data = parse_json_response(status, body)
    if status >= 400:
        eprint(f"HTTP {status}")
        print_response(status, body, raw=args.raw)
        return 1
    if args.summary:
        records = iter_records(data)
        total_dollars = sum_numeric(records, ("dollars", "cost", "amount"))
        if total_dollars is None:
            total_dollars = find_numeric(data, ("total_dollars", "dollars", "cost", "amount"))
        rows = [
            row_with_grouping(row, ("start", "end", "date", "accessor", "deployment", "line_item", "tags", "dollars", "cost", "amount"))
            for row in records[: args.summary_limit]
            if isinstance(row, dict)
        ]
        print_summary("deepgram billing breakdown", {
            "project_id": args.project_id,
            "start": args.start,
            "end": args.end,
            "record_count": len(records),
            "total_dollars": total_dollars,
        }, rows)
        return 0
    print_response(status, body, raw=args.raw)
    return 0


def cmd_billing_balances(args: argparse.Namespace) -> int:
    status, body = api_get(f"/projects/{args.project_id}/balances")
    data = parse_json_response(status, body)
    if status >= 400:
        eprint(f"HTTP {status}")
        print_response(status, body, raw=args.raw)
        return 1
    if args.summary:
        records = iter_records(data)
        rows = [
            compact_row(row, ("balance_id", "id", "amount", "dollars", "credit", "balance", "type", "expires", "expires_at", "created", "created_at"))
            for row in records
            if isinstance(row, dict)
        ]
        total = sum_numeric(records, ("amount", "dollars", "credit", "balance"))
        if total is None:
            total = find_numeric(data, ("total", "amount", "dollars", "credit", "balance"))
        print_summary("deepgram balances", {
            "project_id": args.project_id,
            "record_count": len(records),
            "total_numeric_balance": total,
        }, rows)
        return 0
    print_response(status, body, raw=args.raw)
    return 0


def cmd_billing_purchases(args: argparse.Namespace) -> int:
    params = add_common_params(args)
    status, body = api_get(f"/projects/{args.project_id}/purchases", params=params)
    data = parse_json_response(status, body)
    if status >= 400:
        eprint(f"HTTP {status}")
        print_response(status, body, raw=args.raw)
        return 1
    if args.summary:
        records = iter_records(data)
        rows = [
            compact_row(row, ("purchase_id", "id", "amount", "dollars", "credits", "status", "created", "created_at"))
            for row in records[: args.summary_limit]
            if isinstance(row, dict)
        ]
        total = sum_numeric(records, ("amount", "dollars", "credits"))
        print_summary("deepgram purchases", {
            "project_id": args.project_id,
            "record_count": len(records),
            "total_numeric_purchases": total,
        }, rows)
        return 0
    print_response(status, body, raw=args.raw)
    return 0


def cmd_requests_list(args: argparse.Namespace) -> int:
    validate_dates(args, allow_datetime=True)
    params = add_common_params(args, {"limit": args.limit})
    if args.accessor:
        params["accessor"] = args.accessor
    if args.deployment:
        params["deployment"] = args.deployment
    if args.endpoint:
        params["endpoint"] = args.endpoint
    if args.method:
        params["method"] = args.method
    if args.status:
        params["status"] = args.status
    if args.request_id:
        params["request_id"] = args.request_id
    if args.tag:
        params["tag"] = args.tag
    status, body = api_get(f"/projects/{args.project_id}/requests", params=params)
    data = parse_json_response(status, body)
    if status >= 400:
        eprint(f"HTTP {status}")
        print_response(status, body, raw=args.raw)
        return 1
    if args.summary:
        records = iter_records(data)
        rows = [
            compact_request_row(row)
            for row in records[: args.summary_limit]
            if isinstance(row, dict)
        ]
        print_summary("deepgram requests", {
            "project_id": args.project_id,
            "start": args.start,
            "end": args.end,
            "record_count": len(records),
        }, rows)
        return 0
    print_response(status, body, raw=args.raw)
    return 0


def cmd_requests_get(args: argparse.Namespace) -> int:
    return run_get(f"/projects/{args.project_id}/requests/{args.request_id}", params=None, args=args)


def add_output_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--raw", action="store_true", help="Print raw response body")


def add_date_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--start", required=True, help="Start date, usually YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="End date, usually YYYY-MM-DD")


def add_paging_flags(parser: argparse.ArgumentParser, default_limit: int = 100) -> None:
    parser.add_argument("--limit", type=int, default=default_limit)
    parser.add_argument("--page")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Deepgram Management API read-only helper")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("doctor", help="Check credential and API reachability")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("get", help="Raw GET under https://api.deepgram.com/v1")
    p.add_argument("path")
    p.add_argument("--param", action="append", default=[], help="Query parameter as key=value; repeatable")
    add_output_flags(p)
    p.set_defaults(func=cmd_get)

    projects = sub.add_parser("projects", help="Project operations").add_subparsers(dest="projects_command", required=True)
    p = projects.add_parser("list", help="List projects")
    p.add_argument("--limit", type=int)
    p.add_argument("--summary", action="store_true")
    add_output_flags(p)
    p.set_defaults(func=cmd_projects_list)
    p = projects.add_parser("get", help="Get project")
    p.add_argument("--project-id", required=True)
    add_output_flags(p)
    p.set_defaults(func=cmd_projects_get)

    usage = sub.add_parser("usage", help="Usage operations").add_subparsers(dest="usage_command", required=True)
    p = usage.add_parser("breakdown", help="Get project usage breakdown")
    p.add_argument("--project-id", required=True)
    add_date_flags(p)
    p.add_argument("--grouping", choices=("accessor","endpoint","feature_set","models","method","tags","deployment"))
    p.add_argument("--accessor")
    p.add_argument("--model")
    p.add_argument("--tag")
    p.add_argument("--param", action="append", default=[], help="Extra query parameter key=value")
    p.add_argument("--summary", action="store_true")
    p.add_argument("--summary-limit", type=int, default=20)
    add_output_flags(p)
    p.set_defaults(func=cmd_usage_breakdown)

    billing = sub.add_parser("billing", help="Billing operations").add_subparsers(dest="billing_command", required=True)
    p = billing.add_parser("breakdown", help="Get billing/cost breakdown")
    p.add_argument("--project-id", required=True)
    add_date_flags(p)
    p.add_argument("--grouping", help='JSON list, e.g. \'["deployment","line_item"]\'')
    p.add_argument("--accessor")
    p.add_argument("--deployment")
    p.add_argument("--line-item")
    p.add_argument("--tag")
    p.add_argument("--param", action="append", default=[], help="Extra query parameter key=value")
    p.add_argument("--summary", action="store_true")
    p.add_argument("--summary-limit", type=int, default=20)
    add_output_flags(p)
    p.set_defaults(func=cmd_billing_breakdown)

    p = billing.add_parser("balances", help="Get project balances")
    p.add_argument("--project-id", required=True)
    p.add_argument("--summary", action="store_true")
    add_output_flags(p)
    p.set_defaults(func=cmd_billing_balances)

    p = billing.add_parser("purchases", help="Get project purchases")
    p.add_argument("--project-id", required=True)
    add_paging_flags(p)
    p.add_argument("--param", action="append", default=[], help="Extra query parameter key=value")
    p.add_argument("--summary", action="store_true")
    p.add_argument("--summary-limit", type=int, default=20)
    add_output_flags(p)
    p.set_defaults(func=cmd_billing_purchases)

    requests = sub.add_parser("requests", help="Request log operations").add_subparsers(dest="requests_command", required=True)
    p = requests.add_parser("list", help="List project requests")
    p.add_argument("--project-id", required=True)
    add_date_flags(p)
    add_paging_flags(p, default_limit=20)
    p.add_argument("--accessor")
    p.add_argument("--deployment")
    p.add_argument("--endpoint", choices=("listen", "read", "speak", "agent"))
    p.add_argument("--method", choices=("sync", "async", "streaming"))
    p.add_argument("--status", choices=("succeeded", "failed"))
    p.add_argument("--request-id")
    p.add_argument("--tag")
    p.add_argument("--param", action="append", default=[], help="Extra query parameter key=value")
    p.add_argument("--summary", action="store_true")
    p.add_argument("--summary-limit", type=int, default=20)
    add_output_flags(p)
    p.set_defaults(func=cmd_requests_list)

    p = requests.add_parser("get", help="Get one request")
    p.add_argument("--project-id", required=True)
    p.add_argument("--request-id", required=True)
    add_output_flags(p)
    p.set_defaults(func=cmd_requests_get)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return int(args.func(args))
    except BrokenPipeError:
        return 0
    except Exception as exc:
        eprint("error: " + (str(exc) if isinstance(exc, SafeError) else "Invalid input or response; sensitive details suppressed"))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
