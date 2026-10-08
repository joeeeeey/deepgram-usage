"""Small dependency-free boundaries for standalone provider clients."""

from __future__ import annotations
import json
import os
import re
import stat
from pathlib import Path
from urllib.parse import urlsplit, urlencode
from urllib.request import Request, HTTPRedirectHandler, build_opener
from urllib.error import HTTPError, URLError


class SafeError(RuntimeError):
    pass


_SECRETS = set()


def secret(env, file_env=None):
    value = os.environ.get(env, "").strip()
    if not value and file_env and os.environ.get(file_env):
        p = Path(os.environ[file_env]).expanduser()
        try:
            mode = p.lstat().st_mode
            if not stat.S_ISREG(mode) or (os.name != "nt" and mode & 0o077):
                raise SafeError(
                    "Credential file must be regular and private (chmod 600)."
                )
            value = p.read_text().strip()
        except OSError:
            raise SafeError("Credential file could not be read.") from None
    if not value:
        raise SafeError(
            "Missing credential: set " + env + (" or " + file_env if file_env else "")
        )
    if "\n" in value or "\r" in value:
        raise SafeError("Credential must be a single line.")
    _SECRETS.add(value)
    return value


def scrub(value):
    if isinstance(value, dict):
        return {
            k: "[REDACTED]"
            if not (
                str(k)
                in {
                    "tokens_in",
                    "tokens_out",
                    "total_tokens_in",
                    "total_tokens_out",
                    "total_tokens",
                }
                and type(v) in (int, float)
            )
            and re.search(
                r"password|secret|token|authorization|cookie|api.?key|routing.?key|private.?key|connection.?uri|connection.?string|dsn|websocket|debugger",
                str(k),
                re.I,
            )
            else scrub(v)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [scrub(v) for v in value]
    if isinstance(value, str):
        for s in sorted(_SECRETS, key=len, reverse=True):
            value = value.replace(s, "[REDACTED]")
        value = re.sub(
            r"([a-z][a-z0-9+.-]*://)[^\s/@]+:[^\s/@]+@",
            r"\1[REDACTED]@",
            value,
            flags=re.I,
        )
    return value


def api_url(base, path, params=None):
    parts = urlsplit(path)
    if (
        not path.startswith("/")
        or path.startswith("//")
        or parts.scheme
        or parts.netloc
        or parts.fragment
        or "\\" in path
        or any(c.isspace() for c in path)
    ):
        raise SafeError(
            "Expected an absolute API path on the configured provider host."
        )
    if any(x in (".", "..") for x in parts.path.split("/")) or re.search(
        r"%2e|%2f|%5c", path, re.I
    ):
        raise SafeError("Encoded separators and traversal are not accepted.")
    url = base.rstrip("/") + path
    if params:
        url += ("&" if "?" in url else "?") + urlencode(params, doseq=True)
    return url


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request(url, *, headers=None, method="GET", body=None, timeout=30):
    data = json.dumps(body).encode() if body is not None else None
    h = {"Accept": "application/json", **(headers or {})}
    if body is not None:
        h["Content-Type"] = "application/json"
    req = Request(url, data=data, headers=h, method=method)
    try:
        with build_opener(NoRedirect()).open(req, timeout=timeout) as resp:
            raw = resp.read(10 * 1024 * 1024 + 1)
            if len(raw) > 10 * 1024 * 1024:
                raise SafeError("Response exceeds 10 MiB; narrow the query.")
            return resp.status, scrub(json.loads(raw) if raw else {})
    except HTTPError as e:
        raise SafeError(
            f"HTTP {e.code}; response body suppressed. Check scope, permissions and rate limits; no automatic retry."
        ) from None
    except (URLError, TimeoutError, OSError):
        raise SafeError(
            "Network request failed; outcome may be unknown. Inspect before retrying a write."
        ) from None
    except (ValueError, UnicodeError):
        raise SafeError("Provider returned invalid JSON; body suppressed.") from None


def private_json(path, value):
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.parent.is_symlink():
        raise SafeError("Refusing symlink output directory.")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(scrub(value), f, indent=2)
        f.write("\n")
