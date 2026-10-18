#!/usr/bin/env python3
"""Read-only host probe, suitable for ``ssh host python3 - --since EPOCH``.

This standalone file uses only the standard library and never imports the app.
It returns aggregate production metrics and journal classifications, never logs.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import time


DATABASE_PATH = Path("/var/lib/initiation-quiz/quiz.sqlite")
SERVICE_UNITS = ("initiation-quiz.service", "quiz-caddy.service")
JOURNAL_LIMIT = 200
COMMAND_TIMEOUT = 3
PHASES = {"setup", "waiting", "question_open", "question_closed", "question_reveal",
          "quiz_finished", "voting_open", "voting_closed"}
STAGES = {"blank", "audience", "main"}
CURSOR = re.compile(r"[a-z]=[0-9a-f]+(?:;[a-z]=[0-9a-f]+)*\Z")
QUESTION_ID = re.compile(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\Z")
PUBLIC_ASSET = re.compile(r"https://storage\.yandexcloud\.net/[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]/quiz/[a-f0-9-]+\.(?:png|jpg|gif|webp)\Z")


def utc_iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def nonnegative_int(value):
    try:
        number = int(value)
        return number if number >= 0 and not isinstance(value, bool) else None
    except (ValueError, TypeError, OverflowError):
        return None


def run_command(arguments: list[str]) -> str:
    """No shell, inherited secrets, stderr, files, or interactive input."""
    result = subprocess.run(
        arguments, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        check=True, timeout=COMMAND_TIMEOUT, encoding="utf-8", errors="replace",
        env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C", "LC_ALL": "C",
             "SYSTEMD_PAGER": "", "SYSTEMD_COLORS": "0"},
    )
    if len(result.stdout) > 4 * 1024 * 1024:
        raise ValueError("command output exceeds bound")
    return result.stdout


@contextmanager
def read_snapshot(path=DATABASE_PATH, *, timeout=0.25, max_seconds=0.75):
    """A short SQLite snapshot with independent read-only and SQL protections.

    WAL databases require both existing sidecars from the active application.
    Missing sidecars fail closed: mode=ro alone can create them. readonly_shm
    also prevents changes to the shared WAL index. immutable=1 would hide recent
    committed answers and is deliberately never used.
    """
    path = Path(path).resolve()
    try:
        with path.open("rb") as source:
            header = source.read(20)
    except FileNotFoundError:
        raise sqlite3.OperationalError("database unavailable") from None
    if len(header) >= 20 and 2 in header[18:20]:
        if not all(Path(str(path) + suffix).is_file() for suffix in ("-wal", "-shm")):
            raise sqlite3.OperationalError("active WAL sidecars unavailable")
    connection = sqlite3.connect(path.as_uri() + "?mode=ro&readonly_shm=1", uri=True, timeout=timeout)
    try:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA trusted_schema=OFF")
        connection.execute("PRAGMA temp_store=MEMORY")
        allowed = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION,
                   sqlite3.SQLITE_TRANSACTION}
        connection.set_authorizer(lambda action, *_: sqlite3.SQLITE_OK if action in allowed else sqlite3.SQLITE_DENY)
        deadline = time.monotonic() + max_seconds
        connection.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
        connection.execute("BEGIN")
        yield connection
    finally:
        # Closing rolls back the read transaction and releases its WAL snapshot.
        connection.close()


def collect_database(path=DATABASE_PATH, *, now=None, environment="production", assets=None) -> dict:
    now = time.time() if now is None else now
    with read_snapshot(path) as connection:
        state = connection.execute(
            "SELECT json_extract(data,'$.phase'), json_extract(data,'$.displayStage'), "
            "json_extract(data,'$.runId'), json_extract(data,'$.revision'), "
            "json_extract(data,'$.currentQuestionId') FROM events WHERE environment=?", (environment,),
        ).fetchone()
        if state is None:
            raise ValueError("missing production event")
        phase, stage, run_id, revision, current_id = state
        counts = dict(connection.execute(
            "SELECT kind,COUNT(*) FROM records WHERE environment=? "
            "AND kind IN ('group','participant','question') GROUP BY kind", (environment,),
        ))
        joined = connection.execute(
            "SELECT COUNT(*) FROM records WHERE environment=? AND kind='participant' "
            "AND json_extract(data,'$.joined')=1", (environment,),
        ).fetchone()[0]
        question_ids = [row[0] for row in connection.execute(
            "SELECT id FROM records WHERE environment=? AND kind='question' "
            "ORDER BY CAST(json_extract(data,'$.position') AS REAL),rowid", (environment,),
        )]
        manual, automatic = connection.execute(
            "SELECT COALESCE(SUM(automatic=0),0),COALESCE(SUM(automatic<>0),0) "
            "FROM answers WHERE environment=? AND question_id=?", (environment, current_id),
        ).fetchone()
        ballots = connection.execute("SELECT COUNT(*) FROM ballots WHERE environment=?", (environment,)).fetchone()[0]
        reveals = connection.execute("SELECT COUNT(*) FROM reveals WHERE environment=?", (environment,)).fetchone()[0]
        sessions = connection.execute(
            "SELECT COUNT(*) FROM sessions WHERE environment=? AND role='participant' AND expires>?",
            (environment, int(now * 1000)),
        ).fetchone()[0]
        if assets is not None:
            seen = set()
            for icon, media in connection.execute(
                "SELECT json_extract(data,'$.iconUrl'),json_extract(data,'$.mediaUrl') "
                "FROM records WHERE environment=? AND kind='group' ORDER BY rowid", (environment,),
            ):
                for kind, url in (("icon", icon), ("media", media)):
                    if isinstance(url, str) and PUBLIC_ASSET.fullmatch(url) and (kind, url) not in seen:
                        seen.add((kind, url))
                        assets.append({"kind": kind, "url": url})
    return {
        "phase": phase if phase in PHASES else None, "displayStage": stage if stage in STAGES else None,
        "runId": nonnegative_int(run_id) or 0, "revision": nonnegative_int(revision) or 0,
        "question": question_ids.index(current_id) + 1 if phase != "setup" and current_id in question_ids else 0,
        "currentQuestionId": current_id if isinstance(current_id, str) and QUESTION_ID.fullmatch(current_id) else None,
        "groups": counts.get("group", 0), "participants": counts.get("participant", 0), "joined": joined,
        "questions": counts.get("question", 0), "manualAnswers": manual, "automaticAnswers": automatic,
        "ballots": ballots, "reveals": reveals, "validParticipantSessions": sessions,
    }


def cpu_counters(path) -> tuple[int, int]:
    values = [int(value) for value in Path(path).read_text().splitlines()[0].split()[1:9]]
    if len(values) < 5:
        raise ValueError("incomplete CPU counters")
    return sum(values), values[3] + values[4]


def count_https_connections(proc_root=Path("/proc")) -> int:
    count = 0
    for name in ("tcp", "tcp6"):
        path = Path(proc_root) / "net" / name
        if name == "tcp6" and not path.exists():
            continue
        for line in path.read_text().splitlines()[1:]:
            fields = line.split()
            if len(fields) >= 4 and fields[3] == "01" and fields[1].rsplit(":", 1)[-1].upper() == "01BB":
                count += 1
    return count


def collect_resources(proc_root=Path("/proc"), data_path=DATABASE_PATH.parent, *, sample_seconds=0.2) -> dict:
    proc_root = Path(proc_root)
    before_total, before_idle = cpu_counters(proc_root / "stat")
    time.sleep(sample_seconds)
    after_total, after_idle = cpu_counters(proc_root / "stat")
    total_delta = after_total - before_total
    busy = total_delta - (after_idle - before_idle)
    cpu = round(max(0, min(100, busy / total_delta * 100)), 1) if total_delta > 0 else 0.0
    memory = {}
    for line in (proc_root / "meminfo").read_text().splitlines():
        name, value = line.split(":", 1)
        if name in {"MemTotal", "MemAvailable"}:
            memory[name] = int(value.split()[0]) * 1024
    disk = shutil.disk_usage(data_path)
    received = transmitted = 0
    for line in (proc_root / "net" / "dev").read_text().splitlines()[2:]:
        name, counters = line.split(":", 1)
        if name.strip() != "lo":
            values = counters.split()
            received += int(values[0])
            transmitted += int(values[8])
    return {"cpuPercent": cpu, "cpuCount": os.cpu_count(), "load1": round(os.getloadavg()[0], 2),
            "memoryTotalBytes": memory["MemTotal"], "memoryAvailableBytes": memory["MemAvailable"],
            "diskTotalBytes": disk.total, "diskFreeBytes": disk.free,
            "establishedHttpsConnections": count_https_connections(proc_root),
            "networkRxBytes": received, "networkTxBytes": transmitted}


def collect_service(unit: str, *, runner=run_command, proc_root=Path("/proc")) -> dict:
    if unit not in SERVICE_UNITS:
        raise ValueError("unsupported unit")
    output = runner(["systemctl", "show", "--no-pager",
                     "--property=ActiveState,SubState,MainPID,NRestarts,MemoryCurrent,LimitNOFILE", unit])
    properties = dict(line.split("=", 1) for line in output.splitlines() if "=" in line)
    active = properties.get("ActiveState")
    sub = properties.get("SubState")
    pid = nonnegative_int(properties.get("MainPID"))
    count = None
    limit = nonnegative_int(properties.get("LimitNOFILE"))
    if pid:
        try:
            with os.scandir(Path(proc_root) / str(pid) / "fd") as entries:
                count = sum(1 for _ in entries)
        except (OSError, ValueError):
            pass  # A service may exit between systemctl and /proc reads.
        try:
            limits = (Path(proc_root) / str(pid) / "limits").read_text()
            match = re.search(r"^Max open files\s+(\S+)", limits, re.MULTILINE)
            if match:
                limit = nonnegative_int(match.group(1))
        except (OSError, ValueError):
            pass
    return {"active": active if active in {"active", "inactive", "activating", "deactivating", "failed", "reloading", "maintenance", "refreshing"} else None,
            "sub": sub if sub in {"running", "dead", "failed", "exited", "start", "start-pre", "start-post", "stop", "stop-sigterm", "stop-sigkill", "auto-restart", "reload", "reload-signal", "stop-post", "final-sigterm", "final-sigkill", "cleaning"} else None,
            "mainPid": pid, "restarts": nonnegative_int(properties.get("NRestarts")),
            "memoryBytes": nonnegative_int(properties.get("MemoryCurrent")), "fdCount": count, "fdLimit": limit}


def sanitize_journal_entry(entry: dict):
    """Only whitelisted metadata and fixed categories can leave this function."""
    cursor = entry.get("__CURSOR")
    if not isinstance(cursor, str) or len(cursor) > 512 or not CURSOR.fullmatch(cursor):
        return None
    unit = next((entry.get(key) for key in ("_SYSTEMD_UNIT", "UNIT") if entry.get(key) in SERVICE_UNITS), None)
    if unit is None:
        return None
    try:
        at = utc_iso(int(entry["__REALTIME_TIMESTAMP"]) / 1_000_000)
    except (KeyError, ValueError, TypeError, OverflowError, OSError):
        return None
    message = entry.get("MESSAGE", "")
    if not isinstance(message, str):
        message = ""
    structured = {}
    try:
        decoded = json.loads(message)
        if isinstance(decoded, dict):
            structured = decoded
    except (ValueError, TypeError):
        pass
    # Inspect only diagnostic fields, excluding request headers, URLs and bodies.
    diagnostic = " ".join(str(structured.get(key, "")) for key in ("msg", "message", "error", "err")) if structured else message
    diagnostic = diagnostic.lower()
    status = structured.get("status")
    if not isinstance(status, int) or isinstance(status, bool) or not 100 <= status <= 599:
        match = re.search(r"\b(?:status|status_code|http_status)[\s:=\"]+(\d{3})\b", diagnostic)
        status = int(match.group(1)) if match and 100 <= int(match.group(1)) <= 599 else None
    priority = nonnegative_int(entry.get("PRIORITY"))
    log_level = structured.get("level")
    if re.search(r"\boom(?:-kill(?:ed|er)?)?\b|out of memory|killed process", diagnostic):
        level, category = "error", "oom"
    elif "context canceled" in diagnostic or "context cancelled" in diagnostic:
        level, category = "info", "client_abort"
    elif "connection reset" in diagnostic or "connection refused" in diagnostic:
        level, category = "warning", "upstream_connection"
    elif status is not None and status >= 500:
        level, category = "warning", "http_5xx"
    elif (priority is not None and priority <= 3) or log_level in {"error", "fatal", "panic"} or re.search(r"\b(?:error|[a-z]+error|exception|failed|failure|sqlite_[a-z_]+|unhandled|uncaught)\b", diagnostic):
        level, category = "warning", "application_error" if unit == SERVICE_UNITS[0] else "service_error"
    elif priority == 4 or log_level in {"warn", "warning"}:
        level, category = "warning", "service_warning"
    else:
        level, category = "info", "http" if status else "service_info"
    return {"id": cursor, "at": at, "unit": unit, "level": level, "category": category, "status": status}


def collect_journal(since: float, *, runner=run_command) -> dict:
    if not math.isfinite(since) or since < 0:
        raise ValueError("invalid journal start")
    arguments = ["journalctl", "--no-pager", "--quiet", "--since=@" + format(since, ".6f"),
                 "-n", str(JOURNAL_LIMIT), "-o", "json"]
    for unit in SERVICE_UNITS:
        arguments.extend(["-u", unit])
    lines = [line for line in runner(arguments).splitlines() if line.strip()]
    events = []
    for line in lines[-JOURNAL_LIMIT:]:
        try:
            entry = json.loads(line)
            event = sanitize_journal_entry(entry) if isinstance(entry, dict) else None
        except (ValueError, TypeError, OverflowError):
            event = None
        if event is not None:
            events.append(event)
    return {"events": events, "truncated": len(lines) >= JOURNAL_LIMIT}


def collect(since: float) -> dict:
    report = {"collectedAt": utc_iso(time.time()), "resources": {}, "services": {},
              "database": {}, "journal": {"events": [], "truncated": False}, "assets": []}
    errors = {}
    for name, operation in (("resources", collect_resources),
                            ("database", lambda: collect_database(assets=report["assets"])),
                            ("journal", lambda: collect_journal(since))):
        try:
            report[name] = operation()
        except Exception as error:
            errors[name] = type(error).__name__
    for unit in SERVICE_UNITS:
        try:
            report["services"][unit] = collect_service(unit)
        except Exception as error:
            errors["services." + unit] = type(error).__name__
    if errors:
        report["errors"] = errors
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", type=float, required=True)
    arguments = parser.parse_args()
    print(json.dumps(collect(arguments.since), separators=(",", ":"), allow_nan=False))


if __name__ == "__main__":
    main()
