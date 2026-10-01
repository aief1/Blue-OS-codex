#!/usr/bin/env python3
"""Expose Codex quota, token usage and active-task state as a read-only HTTP API."""

from __future__ import annotations

import argparse
from contextlib import closing
import hmac
import json
import os
import queue
import shutil
import sqlite3
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


class CodexRpcError(RuntimeError):
    pass


_LOCAL_USAGE_LOCK = threading.Lock()
_LOCAL_USAGE_VALUE: dict[str, Any] | None = None
_LOCAL_USAGE_EXPIRES = 0.0
ACTIVE_THREAD_MAX_AGE_SECONDS = 2 * 60 * 60


def _reader(stream: Any, output: queue.Queue[str]) -> None:
    for line in iter(stream.readline, ""):
        output.put(line)


def _discard_reader(stream: Any) -> None:
    """Continuously drain stderr so a verbose child process cannot deadlock."""
    for _ in iter(stream.readline, ""):
        pass


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


def _find_codex() -> str | None:
    codex = shutil.which("codex")
    if codex:
        return codex

    # Codex Desktop bundles its CLI, but does not always add it to the user's PATH.
    local_app_data = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    bundled_bin = local_app_data / "OpenAI" / "Codex" / "bin"
    candidates = list(bundled_bin.glob("*/codex.exe"))
    if candidates:
        return str(max(candidates, key=lambda path: path.stat().st_mtime))
    return None


def _start_codex() -> subprocess.Popen[str]:
    codex = _find_codex()
    if not codex:
        raise CodexRpcError("找不到 Codex CLI；请确认 Codex Desktop 已安装")

    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    commands = ([codex, "app-server", "proxy"], [codex, "app-server", "--stdio"])
    last_error = ""
    for command in commands:
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=creation_flags,
        )
        time.sleep(0.15)
        if process.poll() is None:
            return process
        last_error = process.stderr.read().strip() if process.stderr else ""
    raise CodexRpcError(last_error or "无法启动 Codex App Server")


def _rpc_snapshot(timeout: float = 12.0) -> dict[str, Any]:
    process = _start_codex()
    if process.stdin is None or process.stdout is None:
        raise CodexRpcError("Codex App Server 标准输入输出不可用")

    output: queue.Queue[str] = queue.Queue()
    threading.Thread(target=_reader, args=(process.stdout, output), daemon=True).start()
    if process.stderr is not None:
        threading.Thread(target=_discard_reader, args=(process.stderr,), daemon=True).start()

    initialize_request = {
        "method": "initialize",
        "id": 1,
        "params": {
            "clientInfo": {
                "name": "codex-watch-bridge",
                "title": "Codex Watch Bridge",
                "version": "1.0.0",
            },
            "capabilities": {
                "experimentalApi": True,
                "requestAttestation": False,
            },
        },
    }
    data_requests = [
        {"method": "initialized", "params": {}},
        {
            "method": "account/rateLimits/read",
            "id": 2,
            "params": {"excludeResetCreditDetails": True},
        },
        {
            "method": "thread/list",
            "id": 3,
            "params": {
                "limit": 40,
                "sortKey": "updated_at",
                "sortDirection": "desc",
                "archived": False,
                "useStateDbOnly": True,
            },
        },
        {"method": "account/usage/read", "id": 4},
    ]

    try:
        deadline = time.monotonic() + timeout
        process.stdin.write(json.dumps(initialize_request, ensure_ascii=False) + "\n")
        process.stdin.flush()

        initialized = False
        while time.monotonic() < deadline and not initialized:
            try:
                line = output.get(timeout=min(0.5, max(0.01, deadline - time.monotonic())))
            except queue.Empty:
                if process.poll() is not None:
                    break
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if message.get("id") != 1:
                continue
            if "error" in message:
                raise CodexRpcError(str(message["error"]))
            initialized = True

        if not initialized:
            raise CodexRpcError("Codex App Server 初始化超时")

        for request in data_requests:
            process.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
        process.stdin.flush()

        results: dict[int, Any] = {}
        while time.monotonic() < deadline and not all(request_id in results for request_id in (2, 3, 4)):
            try:
                line = output.get(timeout=min(0.5, max(0.01, deadline - time.monotonic())))
            except queue.Empty:
                if process.poll() is not None:
                    break
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            request_id = message.get("id")
            if request_id in (2, 3, 4):
                if "error" in message:
                    rpc_error = message.get("error") or {}
                    error_code = rpc_error.get("code", "unknown") if isinstance(rpc_error, dict) else "unknown"
                    print(f"[watch] RPC request {request_id} unavailable (code={error_code})")
                    if request_id in (2, 4):
                        results[request_id] = {}
                    elif request_id == 3:
                        results[3] = {"data": []}
                else:
                    results[int(request_id)] = message.get("result")

        return _normalize(results.get(2) or {}, results.get(3) or {}, results.get(4) or {})
    finally:
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()


def _window(window: dict[str, Any] | None) -> dict[str, Any]:
    if window is None:
        return {"usedPercent": None, "remainingPercent": None, "windowMinutes": None, "resetsAt": None}
    window = window or {}
    try:
        used_value = window.get("usedPercent", window.get("used_percent", 0))
        used = max(0, min(100, int(round(float(used_value)))))
    except (TypeError, ValueError, OverflowError):
        used = None
    return {
        "usedPercent": used,
        "remainingPercent": 100 - used if used is not None else None,
        "windowMinutes": window.get("windowDurationMins", window.get("window_minutes")),
        "resetsAt": window.get("resetsAt", window.get("resets_at")),
    }


def _thread_title(thread: dict[str, Any]) -> str:
    title = str(thread.get("name") or thread.get("preview") or "Codex 任务").strip()
    return title if len(title) <= 32 else title[:31] + "…"


def _local_active_threads(codex_home: Path | None = None, now: float | None = None) -> list[dict[str, Any]] | None:
    """Read Desktop's latest turn state without loading threads in our App Server.

    A separate app-server reports Desktop threads as ``notLoaded``. Its state
    database is a local implementation detail, so failures fall back to the
    app-server result instead of breaking quota monitoring.
    """
    codex_home = codex_home or Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    history_path = codex_home / "thread_history_1.sqlite"
    state_path = codex_home / "state_5.sqlite"
    if not history_path.is_file() or not state_path.is_file():
        return None

    now = time.time() if now is None else now
    try:
        with closing(sqlite3.connect(f"file:{history_path.as_posix()}?mode=ro", uri=True, timeout=1)) as history, \
                closing(sqlite3.connect(f"file:{state_path.as_posix()}?mode=ro", uri=True, timeout=1)) as state:
            history.execute("PRAGMA query_only=ON")
            state.execute("PRAGMA query_only=ON")
            candidates = history.execute("""
                SELECT turn.thread_id
                FROM thread_turns AS turn
                WHERE turn.status = 'inProgress'
                  AND NOT EXISTS (
                    SELECT 1 FROM thread_turns AS later
                    WHERE later.thread_id = turn.thread_id
                      AND later.rollout_ordinal > turn.rollout_ordinal
                  )
            """).fetchall()
            active = []
            for (thread_id,) in candidates:
                row = state.execute("""
                    SELECT name, title, preview, updated_at, archived
                    FROM threads WHERE id = ?
                """, (thread_id,)).fetchone()
                if not row or row[4] or not row[3] or now - row[3] > ACTIVE_THREAD_MAX_AGE_SECONDS:
                    continue
                active.append({
                    "id": str(thread_id),
                    "name": row[0] or row[1] or row[2] or "Codex 任务",
                    "updatedAt": row[3],
                    "status": {"type": "active"},
                })
            return active
    except (OSError, sqlite3.Error):
        return None


def _token_values(value: dict[str, Any] | None) -> dict[str, int]:
    value = value or {}
    cached = _safe_int(value.get("cached_input_tokens", value.get("cachedInputTokens", 0)))
    input_total = _safe_int(value.get("input_tokens", value.get("inputTokens", 0)))
    output = _safe_int(value.get("output_tokens", value.get("outputTokens", 0)))
    total = _safe_int(value.get("total_tokens", value.get("totalTokens", input_total + output)))
    return {
        "input": max(0, input_total - cached),
        "cached": max(0, cached),
        "output": max(0, output),
        "total": max(0, total),
    }


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _scan_local_usage(codex_home: Path, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now().astimezone()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc).astimezone()
    local_tz = now.tzinfo
    elapsed_minutes = max(1, now.hour * 60 + now.minute + 1)
    bars = [0] * 12
    totals = {"input": 0, "cached": 0, "output": 0, "total": 0}
    latest_model = ""
    latest_model_at: datetime | None = None
    latest_rate_limits: dict[str, Any] | None = None
    latest_rate_limits_at: datetime | None = None
    sessions_root = codex_home / "sessions"

    candidate_files: set[Path] = set()
    for days_ago in range(3):
        day = (now - timedelta(days=days_ago)).date()
        day_dir = sessions_root / f"{day.year:04d}" / f"{day.month:02d}" / f"{day.day:02d}"
        if day_dir.is_dir():
            candidate_files.update(day_dir.glob("*.jsonl"))

    # A conversation keeps the directory of the day it was created. Long-lived
    # threads can therefore receive today's events inside an older directory.
    try:
        recent_files = sorted(
            sessions_root.rglob("*.jsonl"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )[:20]
        candidate_files.update(recent_files)
    except OSError:
        pass

    for session_file in sorted(candidate_files):
        previous: dict[str, int] | None = None
        try:
            with session_file.open("r", encoding="utf-8", errors="replace") as stream:
                for line in stream:
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    payload = event.get("payload") or {}
                    timestamp = _parse_timestamp(event.get("timestamp"))
                    if event.get("type") == "turn_context" and payload.get("model"):
                        if timestamp is not None and (latest_model_at is None or timestamp > latest_model_at):
                            latest_model = str(payload["model"])
                            latest_model_at = timestamp
                    if event.get("type") != "event_msg" or payload.get("type") != "token_count":
                        continue
                    rate_limits = payload.get("rate_limits") or payload.get("rateLimits")
                    if isinstance(rate_limits, dict) and timestamp is not None:
                        if latest_rate_limits_at is None or timestamp > latest_rate_limits_at:
                            latest_rate_limits = rate_limits
                            latest_rate_limits_at = timestamp
                    info = payload.get("info") or {}
                    cumulative_raw = info.get("total_token_usage") or info.get("totalTokenUsage")
                    last_raw = info.get("last_token_usage") or info.get("lastTokenUsage")
                    cumulative = _token_values(cumulative_raw)
                    if previous is None or cumulative["total"] < previous["total"]:
                        delta = _token_values(last_raw or cumulative_raw)
                    else:
                        delta = {
                            key: max(0, cumulative[key] - previous[key])
                            for key in totals
                        }
                    previous = cumulative

                    if timestamp is None:
                        continue
                    local_timestamp = timestamp.astimezone(local_tz)
                    if local_timestamp.date() != now.date():
                        continue
                    minute = local_timestamp.hour * 60 + local_timestamp.minute
                    bucket = min(11, max(0, int(minute * 12 / elapsed_minutes)))
                    bars[bucket] += delta["total"]
                    for key in totals:
                        totals[key] += delta[key]
        except OSError:
            continue

    peak_index = bars.index(max(bars)) if max(bars) > 0 else -1
    if peak_index >= 0:
        midpoint = int((peak_index + 0.5) * elapsed_minutes / 12)
        peak_label = f"{min(23, midpoint // 60):02d}:{midpoint % 60:02d}"
    else:
        peak_label = "--:--"
    return {
        "todayTokens": totals["total"],
        "inputTokens": totals["input"],
        "outputTokens": totals["output"],
        "cachedTokens": totals["cached"],
        "bars": bars,
        "peakLabel": peak_label,
        "currentModel": latest_model,
        "_rateLimits": latest_rate_limits,
    }


def _local_usage() -> dict[str, Any]:
    global _LOCAL_USAGE_EXPIRES, _LOCAL_USAGE_VALUE
    now_monotonic = time.monotonic()
    with _LOCAL_USAGE_LOCK:
        if _LOCAL_USAGE_VALUE is not None and now_monotonic < _LOCAL_USAGE_EXPIRES:
            return dict(_LOCAL_USAGE_VALUE)
        codex_home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
        _LOCAL_USAGE_VALUE = _scan_local_usage(codex_home)
        _LOCAL_USAGE_EXPIRES = now_monotonic + 15.0
        return dict(_LOCAL_USAGE_VALUE)


def _official_today_tokens(usage_response: dict[str, Any]) -> int | None:
    today = datetime.now().astimezone().date().isoformat()
    buckets = usage_response.get("dailyUsageBuckets") or []
    for bucket in buckets:
        if bucket.get("startDate") != today:
            continue
        value = bucket.get("tokens")
        if isinstance(value, dict):
            value = value.get("total") or value.get("totalTokens")
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return None
    summary = usage_response.get("summary") or {}
    if summary.get("startDate") == today:
        try:
            return max(0, int(summary.get("tokens")))
        except (TypeError, ValueError):
            return None
    return None


def _normalize(
    rate_response: dict[str, Any],
    thread_response: dict[str, Any],
    usage_response: dict[str, Any] | None = None,
) -> dict[str, Any]:
    by_id = rate_response.get("rateLimitsByLimitId") or {}
    limits = by_id.get("codex") or rate_response.get("rateLimits") or {}
    threads = thread_response.get("data") or []
    active_by_id = {
        str(thread.get("id")): thread
        for thread in threads
        if thread.get("id") and (thread.get("status") or {}).get("type") == "active"
    }
    local_active = _local_active_threads()
    if local_active is not None:
        for thread in local_active:
            active_by_id[str(thread["id"])] = thread
    active = list(active_by_id.values())

    active.sort(key=lambda item: item.get("updatedAt") or 0, reverse=True)
    lead = active[0] if active else None
    title = _thread_title(lead) if lead else ""
    active_items = [
        {"id": str(thread.get("id")), "title": _thread_title(thread)}
        for thread in active
        if thread.get("id")
    ]
    usage = _local_usage()
    local_limits = usage.pop("_rateLimits", None) or {}
    if not limits and local_limits:
        primary_reset = _safe_int((local_limits.get("primary") or {}).get("resets_at"))
        secondary_reset = _safe_int((local_limits.get("secondary") or {}).get("resets_at"))
        if max(primary_reset, secondary_reset) > int(time.time()):
            limits = local_limits
    official_today = _official_today_tokens(usage_response or {})
    if official_today is not None:
        usage["todayTokens"] = official_today
        usage["source"] = "account+local"
        if not any(usage["bars"]) and official_today > 0:
            usage["bars"][-1] = official_today
            usage["peakLabel"] = datetime.now().astimezone().strftime("%H:%M")
    else:
        usage["source"] = "local"

    return {
        "ok": True,
        "updatedAt": int(time.time()),
        "quota": {
            "fiveHour": _window(limits.get("primary")),
            "weekly": _window(limits.get("secondary")),
        },
        "quotaAvailable": bool(limits),
        "activity": {
            "available": local_active is not None or bool(active_by_id),
            "running": bool(active),
            "count": len(active),
            "title": title or "暂无执行中的任务",
            "items": active_items,
        },
        "usage": usage,
        "planType": limits.get("planType", limits.get("plan_type")),
    }


class SnapshotCache:
    def __init__(self, ttl: float) -> None:
        self.ttl = ttl
        self.lock = threading.Lock()
        self.value: dict[str, Any] | None = None
        self.expires_at = 0.0

    def get(self) -> dict[str, Any]:
        now = time.monotonic()
        with self.lock:
            if self.value is not None and now < self.expires_at:
                return self.value
            self.value = _rpc_snapshot()
            self.expires_at = now + self.ttl
            return self.value


def make_handler(cache: SnapshotCache, token: str) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "CodexWatchBridge/1.0"

        def do_GET(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if path == "/health":
                self._send(HTTPStatus.OK, {"ok": True})
                return
            if path != "/api/status":
                self._send(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not found"})
                return
            if not hmac.compare_digest(self.headers.get("X-Codex-Watch-Token", ""), token):
                self._send(HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "unauthorized"})
                return
            try:
                self._send(HTTPStatus.OK, cache.get())
            except Exception as error:  # keep the HTTP process alive for later retries
                print(f"[watch] snapshot failed: {error!r}")
                self._send(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    {"ok": False, "error": "snapshot unavailable", "updatedAt": int(time.time())},
                )

        def do_OPTIONS(self) -> None:  # noqa: N802
            self.send_response(HTTPStatus.NO_CONTENT)
            self._cors_headers()
            self.end_headers()

        def _send(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self._cors_headers()
            self.end_headers()
            self.wfile.write(body)

        def _cors_headers(self) -> None:
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", "X-Codex-Watch-Token")
            self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")

        def log_message(self, format_string: str, *args: Any) -> None:
            print("[watch] " + format_string % args)

    return Handler


class BridgeServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


def main() -> None:
    parser = argparse.ArgumentParser(description="Codex quota bridge for BlueOS watches")
    parser.add_argument("--host", default="127.0.0.1", help="listen address")
    parser.add_argument("--port", type=int, default=8765, help="listen port")
    parser.add_argument("--token", default=os.environ.get("CODEX_WATCH_TOKEN", ""), help="required read token")
    parser.add_argument("--cache-seconds", type=float, default=15.0, help="Codex query cache TTL")
    args = parser.parse_args()
    if len(args.token) < 32:
        parser.error("请用 --token 或 CODEX_WATCH_TOKEN 设置至少 32 个字符的随机访问令牌")

    cache = SnapshotCache(max(1.0, args.cache_seconds))
    server = BridgeServer((args.host, args.port), make_handler(cache, args.token))
    print(f"Codex Watch Bridge: http://{args.host}:{args.port}/api/status")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
