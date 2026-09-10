#!/usr/bin/env python3
"""HTTP acceptance checks for the Increment 3 application.

The login opener deliberately does not follow the application's 303 redirect.
That lets this script verify the actual login response instead of accidentally
following it to an administrator-only page and misreporting a later 403 as a
login failure.
"""
from __future__ import annotations

import argparse
import os
import re
import sqlite3
import sys
import time
from pathlib import Path
from datetime import datetime, timedelta, timezone
from http.cookiejar import CookieJar
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import (
    HTTPRedirectHandler,
    HTTPCookieProcessor,
    Request,
    build_opener,
)


LOGIN_POST_COUNT = 0
FIRST_LOGIN_RESPONSES = []


def _safe_headers(headers):
    result = {}
    for key, value in headers.items():
        if key.lower() == "set-cookie":
            result[key] = "[REDACTED]"
        elif key.lower() == "location":
            result[key] = re.sub(r"/qr/[A-Za-z0-9_\-~.]+", "/qr/[REDACTED]", value)
        else:
            result[key] = value
    return result


def _print_fingerprint(args):
    print(f"Expected checkout commit: {args.expected_commit or '[not supplied]'}")
    print(f"Expected application.py SHA-256: {args.expected_application_hash or '[not supplied]'}")
    print(f"Expected ci_acceptance.py SHA-256: {args.expected_script_hash or '[not supplied]'}")


class NoRedirectHandler(HTTPRedirectHandler):
    """Prevent urllib from following 301/302/303/307/308 responses."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _safe_body_note(body: bytes) -> str:
    """Return the complete sanitized body without credentials or tokens."""
    text = body.decode("utf-8", "replace")
    text = re.sub(r"(?i)(password|passwd|secret|token|cookie|authorization)\s*[:=][^\s<]+", r"\1=[REDACTED]", text)
    text = re.sub(r"/qr/[A-Za-z0-9_\-~.]+", "/qr/[REDACTED]", text)
    text = re.sub(r"(?i)https?://[^\s\"']+", "[URL REDACTED]", text)
    return text


def request(opener, url, method="GET", data=None, expected=None):
    headers = {}
    if data is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    req = Request(url, data=data, method=method, headers=headers)

    try:
        with opener.open(req, timeout=5) as response:
            status = response.status
            response_headers = _safe_headers(response.headers)
            body = response.read()
    except HTTPError as exc:
        status = exc.code
        response_headers = _safe_headers(exc.headers)
        body = exc.read()
    except URLError as exc:
        raise RuntimeError(f"{method} request failed: {exc.reason}") from exc

    if expected is not None and status not in expected:
        raise AssertionError(
            f"{method} request returned HTTP {status}; expected {sorted(expected)}; "
            f"headers={response_headers!r}; "
            f"complete sanitized response body={_safe_body_note(body)!r}"
        )
    return status, body, response_headers


def login(base: str, email: str, password: str):
    global LOGIN_POST_COUNT
    LOGIN_POST_COUNT += 1
    """Login and return an opener retaining the authenticated session cookie."""
    jar = CookieJar()
    opener = build_opener(
        HTTPCookieProcessor(jar),
        NoRedirectHandler(),
    )

    payload = urlencode({"email": email, "password": password}).encode("utf-8")
    status, body, headers = request(opener, f"{base}/login", "POST", payload, None)
    FIRST_LOGIN_RESPONSES.append((status, headers, _safe_body_note(body)))
    print(f"Login POST #{LOGIN_POST_COUNT}: status={status}; headers={headers!r}; complete sanitized response body={_safe_body_note(body)!r}")
    if status != 303:
        raise AssertionError(
            f"POST /login did not return HTTP 303; got {status}; "
            f"headers={headers!r}; complete sanitized response body={_safe_body_note(body)!r}"
        )

    if not any(cookie.name == "session" for cookie in jar):
        raise AssertionError("login returned HTTP 303 but did not set a session cookie")
    return opener


def wait_for_server(base: str, log_path: str, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    last_error = "no response"
    while time.monotonic() < deadline:
        try:
            status, body, headers = request(build_opener(NoRedirectHandler()), f"{base}/login", expected={200})
            if b"Login" not in body and b"login" not in body.lower():
                raise AssertionError("/login returned an unexpected page")
            return
        except Exception as exc:
            last_error = str(exc)
            time.sleep(0.25)

    try:
        server_log = Path(log_path).read_text(encoding="utf-8")
    except Exception:
        server_log = "<server log unavailable>"
    raise RuntimeError(
        f"server did not accept an HTTP request within {timeout:g}s: {last_error}; "
        f"server log: {server_log[-4000:]}"
    )


def _seed_ci_booking(database: str):
    db = sqlite3.connect(database)
    db.row_factory = sqlite3.Row
    try:
        usernames = [row[0] for row in db.execute("SELECT email FROM users ORDER BY email")]
        print(f"CI database: {database}")
        print(f"Seeded usernames: {usernames}")
        print("Login form fields: email, password")
        print("CSRF required: false")

        employee = db.execute(
            "SELECT id FROM users WHERE email = 'employee@example.test' AND status = 'active'"
        ).fetchone()
        resource = db.execute(
            "SELECT id FROM resources WHERE status = 'available' ORDER BY id LIMIT 1"
        ).fetchone()
        if employee is None or resource is None:
            raise AssertionError("CI seed lacks an active employee or available resource")

        # Store timestamps in the same UTC text format used by the application
        # and SQLite CURRENT_TIMESTAMP comparisons. ISO strings containing
        # ``T`` and ``+00:00`` compare incorrectly against SQLite's
        # ``YYYY-MM-DD HH:MM:SS`` CURRENT_TIMESTAMP representation.
        now = datetime.now(timezone.utc)
        start = (now - timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")
        end = (now + timedelta(minutes=55)).strftime("%Y-%m-%d %H:%M:%S")

        
        cur = db.execute(
            """
            INSERT INTO bookings(
                resource_id, owner_user_id, start_at_utc, end_at_utc,
                purpose, created_by, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                resource["id"], employee["id"], start, end,
                "CI acceptance", employee["id"], "confirmed",
            ),
        )
        booking_id = cur.lastrowid
        db.commit()
        return resource["id"], booking_id
    finally:
        db.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Increment 3 HTTP acceptance checks")
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--database", required=True)
    parser.add_argument("--log-path", required=True)
    parser.add_argument("--expected-commit")
    parser.add_argument("--expected-application-hash")
    parser.add_argument("--expected-script-hash")
    args = parser.parse_args()

    _print_fingerprint(args)
    if args.expected_commit:
        actual_commit = os.environ.get("CI_COMMIT_SHA", "")
        if actual_commit and actual_commit != args.expected_commit:
            raise AssertionError("CI commit fingerprint mismatch")
    base = f"http://{args.host}:{args.port}"
    wait_for_server(base, args.log_path)
    resource_id, booking_id = _seed_ci_booking(args.database)

    admin = login(base, "admin@example.test", "AdminPassphrase-2026!")

    status, body, headers = request(
        admin, f"{base}/admin/resources/{resource_id}/qr/print", expected={200}
    )
    if b"<svg" not in body.lower():
        raise AssertionError("QR print route did not return SVG output")
    first_print = body

    status, body, headers = request(
        admin, f"{base}/admin/resources/{resource_id}/qr/print", expected={200}
    )
    if body != first_print:
        raise AssertionError("repeated QR printing did not reuse the active token")

    status, body, headers = request(
        admin,
        f"{base}/admin/resources/{resource_id}/qr/regenerate",
        "POST",
        b"",
        {200},
    )
    if b"<svg" not in body.lower():
        raise AssertionError("QR regeneration route did not return SVG output")

    employee = login(base, "employee@example.test", "EmployeePassphrase-2026!")
    request(
        employee,
        f"{base}/resources/{resource_id}/checkout",
        "POST",
        urlencode({"condition_before": "good", "notes": "CI checkout"}).encode(),
        {303},
    )
    request(
        employee,
        f"{base}/resources/{resource_id}/checkin",
        "POST",
        urlencode({
            "condition_after": "good",
            "notes": "CI check-in",
            "missing_accessories": "",
            "problems": "",
        }).encode(),
        {303},
    )

    unauthenticated = build_opener(NoRedirectHandler())
    request(
        unauthenticated,
        f"{base}/admin/resources/{resource_id}/qr/regenerate",
        "POST",
        b"",
        {403},
    )

    print(f"Observed POST /login requests: {LOGIN_POST_COUNT}")
    if LOGIN_POST_COUNT != 2:
        raise AssertionError(f"unexpected POST /login count: {LOGIN_POST_COUNT}")

    print(
        "HTTP acceptance passed: login, QR print, QR regeneration, "
        f"checkout, check-in, unauthorized regeneration; booking={booking_id}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"HTTP acceptance failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
