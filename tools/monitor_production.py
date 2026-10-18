#!/usr/bin/env python3
"""Local, read-only production monitor. Never deploys or performs recovery actions."""
from __future__ import annotations

import argparse
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import shlex
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo


ORIGIN = "https://posvyat2026.h4ck.me"
PROBE = Path(__file__).with_name("production_probe.py")
ENDPOINTS = ("/api/health", "/api/participant", "/api/quiz/results", "/api/display")
UNITS = ("initiation-quiz.service", "quiz-caddy.service")
PHASES = {
    "setup": "подготовка", "question_open": "приём ответов",
    "question_closed": "вопрос закрыт", "question_reveal": "показ ответа",
    "waiting": "ожидание", "quiz_finished": "квиз завершён",
    "voting_open": "голосование", "voting_closed": "голосование завершено",
}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # A GET redirect could start OAuth and write a session. Only fixed routes run.
        return None


def summarize_payload(path, body):
    if not isinstance(body, dict):
        raise ValueError("Expected JSON object")
    if path == "/api/health":
        if body.get("ok") is not True:
            raise ValueError("Health check failed")
        return {"ok": True}
    if path == "/api/participant":
        state = body["state"]
        if body.get("authenticated") is not False or state["phase"] not in PHASES:
            raise ValueError("Unexpected participant response")
        return {"phase": state["phase"], "currentQuestionId": state["currentQuestionId"],
                "runId": state["runId"], "revision": state["revision"],
                "yandexEnabled": body.get("yandexEnabled") is True}
    if path == "/api/quiz/results":
        if type(body.get("question")) is not int or body["question"] < 0 or type(body.get("ready")) is not bool:
            raise ValueError("Unexpected results response")
        groups = body["groups"]
        if not isinstance(groups, dict) or any(v not in ("g", "r") for v in groups.values()):
            raise ValueError("Invalid group results")
        if not body["ready"] and groups:
            raise ValueError("Unpublished results leaked")
        return {"question": body["question"], "ready": body["ready"], "groups": len(groups)}
    if path == "/api/display":
        if body.get("stage") not in ("blank", "audience", "main") or not isinstance(body.get("groups"), list):
            raise ValueError("Unexpected display response")
        if body["stage"] == "blank" and body["groups"]:
            raise ValueError("Unpublished display leaked")
        return {"stage": body["stage"], "groups": len(body["groups"]),
                "runId": body["runId"], "revision": body["revision"]}
    raise ValueError("Unsupported endpoint")


def http_probe(origin, path, timeout=6):
    started = time.monotonic()
    result = {"path": path, "ok": False}
    try:
        request = urllib.request.Request(origin + path, method="GET", headers={
            "Accept": "application/json", "User-Agent": "QuizReadOnlyMonitor/1.0",
        })
        with urllib.request.build_opener(NoRedirect).open(request, timeout=timeout) as response:
            result["status"] = response.status
            result["cache"] = response.headers.get("Cache-Control", "")
            raw = response.read(512 * 1024 + 1)
            if response.status != 200 or len(raw) > 512 * 1024:
                raise ValueError("Unexpected response")
            result["data"] = summarize_payload(path, json.loads(raw))
            result["ok"] = True
    except urllib.error.HTTPError as error:
        result.update(status=error.code, error="HTTPError")
    except (OSError, ValueError, KeyError, TypeError) as error:
        # Do not persist response bodies, cookies or exception messages.
        result["error"] = type(error).__name__
    result["ms"] = round((time.monotonic() - started) * 1000)
    return result


def ssh_probe(args, since):
    command = ["ssh", "-S", args.control_path, "-o", "BatchMode=yes",
               "-o", "StrictHostKeyChecking=yes", "-o", f"UserKnownHostsFile={args.known_hosts}",
               "-o", "ConnectTimeout=5", "-o", "ConnectionAttempts=1",
               "-o", "ServerAliveInterval=5", "-o", "ServerAliveCountMax=1", args.ssh_target,
               shlex.join(["python3", "-", "--since", str(since)])]
    try:
        completed = subprocess.run(command, input=PROBE.read_text(), text=True,
                                   capture_output=True, timeout=18, check=True)
        report = json.loads(completed.stdout)
        if not isinstance(report, dict) or "collectedAt" not in report:
            raise ValueError("Invalid probe output")
        return report
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        return {"errors": {"ssh": type(error).__name__}}


def asset_probe(asset):
    result = {"kind": asset["kind"], "url": asset["url"], "ok": False}
    url = urllib.parse.urlsplit(asset["url"])
    # Configured URLs must not turn this read-only probe into an arbitrary request.
    if url.scheme != "https" or url.netloc != "storage.yandexcloud.net" or not url.path.startswith("/posvyat2026/quiz/") or url.query or url.fragment:
        return {**result, "error": "UnexpectedAssetURL"}
    try:
        request = urllib.request.Request(asset["url"], method="HEAD")
        with urllib.request.build_opener(NoRedirect).open(request, timeout=5) as response:
            result.update(status=response.status, bytes=int(response.headers.get("Content-Length", "0")),
                          contentType=response.headers.get("Content-Type", ""), cache=response.headers.get("Cache-Control", ""))
            result["ok"] = response.status == 200 and result["bytes"] > 0 and result["contentType"].startswith("image/")
    except urllib.error.HTTPError as error:
        result.update(status=error.code, error="HTTPError")
    except (OSError, ValueError) as error:
        result["error"] = type(error).__name__
    return result


def collect(args, since, cycle=0):
    # Four GETs and one small SSH probe per interval, regardless of participant count.
    with ThreadPoolExecutor(max_workers=5) as pool:
        http = [pool.submit(http_probe, args.url, path) for path in ENDPOINTS]
        remote = pool.submit(ssh_probe, args, since)
        report = {"checkedAt": datetime.now(timezone.utc).isoformat(),
                  "http": [future.result() for future in http], "server": remote.result()}
    assets = report["server"].pop("assets", [])
    selected = []
    for kind in ("icon", "media"):
        options = [a for a in assets if a["kind"] == kind]
        if options:
            selected.append(options[cycle % len(options)])
    with ThreadPoolExecutor(max_workers=2) as pool:
        report["assets"] = list(pool.map(asset_probe, selected))
    return report


def findings(report):
    found = []

    def add(key, severity, message, confirm=1):
        found.append({"key": key, "severity": severity, "message": message, "confirm": confirm})

    for check in report["http"]:
        path = check["path"]
        if not check["ok"]:
            add("http:" + path, "critical", f"{path}: нет корректного ответа ({check.get('status', check.get('error'))})")
        elif check["ms"] > 1500:
            add("slow:" + path, "warning", f"{path}: ответ {check['ms']} мс", confirm=2)
        if check["ok"] and "no-store" not in check.get("cache", ""):
            add("cache:" + path, "warning", f"{path}: отсутствует no-store")
    for asset in report.get("assets", []):
        if not asset["ok"]:
            add("asset:" + asset["kind"], "warning", f"S3: недоступен проверяемый объект {asset['kind']}")
        elif "immutable" not in asset.get("cache", ""):
            add("asset-cache:" + asset["kind"], "warning", f"S3: нет immutable-кэширования {asset['kind']}")
    server = report["server"]
    for section, error in server.get("errors", {}).items():
        add("probe:" + section, "warning", f"Не получены метрики {section}: {error}")
    if "ssh" in server.get("errors", {}):
        return found
    for unit in UNITS:
        service = server.get("services", {}).get(unit)
        if service is None:
            add("service-missing:" + unit, "warning", f"Нет состояния {unit}")
        elif service.get("active") != "active" or service.get("sub") != "running":
            add("service:" + unit, "critical", f"{unit}: {service.get('active')}/{service.get('sub')}")
        elif service.get("fdLimit") and service.get("fdCount") is not None:
            fraction = service["fdCount"] / service["fdLimit"]
            if fraction > .7:
                add("fd:" + unit, "critical" if fraction > .9 else "warning",
                    f"{unit}: занято {fraction:.0%} файловых дескрипторов")
    resources = server.get("resources", {})
    for key, label in (("memory", "ОЗУ"), ("disk", "диска")):
        total = resources.get(key + "TotalBytes")
        free = resources.get(key + ("AvailableBytes" if key == "memory" else "FreeBytes"))
        if total and free is not None and free / total < .1:
            fraction = free / total
            add(key, "critical" if fraction < .05 else "warning", f"Свободно {fraction:.1%} {label}")
    if resources.get("cpuPercent", 0) is not None and resources.get("cpuPercent", 0) > 90:
        add("cpu", "warning", f"CPU {resources['cpuPercent']:.0f}%", confirm=2)
    if (resources.get("load1") or 0) > 1.5 * (resources.get("cpuCount") or 1):
        add("load", "warning", f"Очередь CPU: load1={resources['load1']:.2f}", confirm=2)
    journal = server.get("journal", {})
    events = journal.get("events", [])
    bad = [e for e in events if e.get("level") in ("error", "warning")]
    if bad:
        categories = sorted(set(e["category"] for e in bad))
        severe = any(e["category"] == "oom" for e in bad) or len(bad) >= 20
        add("journal", "critical" if severe else "warning",
            f"Новые записи в журнале: {len(bad)} ({', '.join(categories)})")
    if journal.get("truncated"):
        add("journal-truncated", "warning", "Журнал достиг лимита выборки; число ошибок может быть больше")
    database = server.get("database", {})
    public = {c["path"]: c.get("data", {}) for c in report["http"] if c["ok"]}
    participant = public.get("/api/participant", {})
    results = public.get("/api/quiz/results", {})
    # Different HTTP requests and SQLite can straddle an admin transition. Compare only
    # matching event revisions, and require the anomaly twice during continuous watching.
    same_snapshot = database and participant and all(
        database.get(k) == participant.get(k) for k in ("runId", "revision", "phase", "currentQuestionId"))
    if same_snapshot and results and results["question"] == database.get("question"):
        ready = database["phase"] in ("question_reveal", "waiting", "quiz_finished", "voting_open", "voting_closed")
        if results["ready"] != ready or (ready and results["groups"] != database["groups"]):
            add("lamps", "warning", "Публикация API ламп не совпадает с фазой мероприятия", confirm=2)
    display = public.get("/api/display", {})
    if database and display and all(database.get(k) == display.get(k) for k in ("revision", "runId")):
        count = 0 if database["displayStage"] == "blank" else database["groups"]
        if display["stage"] != database["displayStage"] or display["groups"] != count:
            add("display", "warning", "Состояние экрана не совпадает с рабочей базой", confirm=2)
    return found


class Tracker:
    def __init__(self):
        self.seen_logs = deque(maxlen=1000)
        self.streaks = {}
        self.active = {}
        self.previous = None

    def update(self, report, once=False):
        journal = report["server"].get("journal", {})
        events = journal.get("events", [])
        seen = set(self.seen_logs)
        journal["events"] = [e for e in events if e["id"] not in seen]
        self.seen_logs.extend(e["id"] for e in journal["events"])
        raw = findings(report)
        streaks = {f["key"]: self.streaks.get(f["key"], 0) + 1 for f in raw}
        confirmed = [f for f in raw if once or streaks[f["key"]] >= f["confirm"]]
        active = {f["key"]: f for f in confirmed}
        report["alerts"] = confirmed
        report["pending"] = [f for f in raw if f not in confirmed]
        report["newAlerts"] = [f for k, f in active.items() if k not in self.active or self.active[k]["severity"] != f["severity"]]
        report["clearedAlerts"] = [k for k in self.active if k not in active]
        report["status"] = ("critical" if any(f["severity"] == "critical" for f in confirmed)
                            else "warning" if raw else "ok")
        if self.previous:
            try:
                elapsed = (datetime.fromisoformat(report["server"]["collectedAt"]) -
                           datetime.fromisoformat(self.previous["server"]["collectedAt"])).total_seconds()
                now_resources = report["server"].get("resources", {})
                old_resources = self.previous["server"].get("resources", {})
                for direction in ("Rx", "Tx"):
                    key = "network" + direction + "Bytes"
                    difference = now_resources[key] - old_resources[key]
                    if elapsed > 0 and difference >= 0:
                        now_resources["network" + direction + "Mbps"] = round(difference * 8 / elapsed / 1e6, 3)
            except (KeyError, TypeError, ValueError):
                pass
            for unit, now in report["server"].get("services", {}).items():
                old = self.previous["server"].get("services", {}).get(unit, {})
                if old.get("mainPid") and now.get("mainPid") and old["mainPid"] != now["mainPid"]:
                    report.setdefault("changes", []).append(f"Изменился PID {unit}")
            old_db = self.previous["server"].get("database", {})
            new_db = report["server"].get("database", {})
            if old_db and new_db and any(old_db.get(k) != new_db.get(k) for k in ("phase", "question", "runId", "displayStage")):
                report.setdefault("changes", []).append("Изменились этап мероприятия, вопрос или проход")
        self.streaks, self.active, self.previous = streaks, active, report


def save_report(directory, report):
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    payload = json.dumps(report, ensure_ascii=False, separators=(",", ":")) + "\n"
    temporary = directory / f".latest-{os.getpid()}.json"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        stream.write(payload)
    os.replace(temporary, directory / "latest.json")
    descriptor = os.open(directory / "history.jsonl", os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    with os.fdopen(descriptor, "a") as stream:
        stream.write(payload)


def print_report(report, as_json=False):
    if as_json:
        print(json.dumps(report, ensure_ascii=False), flush=True)
        return
    stamp = datetime.fromisoformat(report["checkedAt"]).astimezone(ZoneInfo("Europe/Moscow")).strftime("%H:%M:%S")
    parts = [f"[{stamp} МСК] {report['status'].upper()}", f"HTTP ≤ {max(c['ms'] for c in report['http'])} мс"]
    database = report["server"].get("database", {})
    if database:
        parts += [f"{PHASES.get(database['phase'], database['phase'])}, вопрос {database['question']}/{database['questions']}",
                  f"входили {database['joined']}/{database['participants']}",
                  f"ответы {database['manualAnswers']}, бюллетени {database['ballots']}"]
    resources = report["server"].get("resources", {})
    if resources.get("cpuPercent") is not None:
        parts.append(f"CPU {resources['cpuPercent']:.0f}%")
    if resources.get("memoryAvailableBytes") is not None:
        parts.append(f"свободно ОЗУ {resources['memoryAvailableBytes']/1024**3:.2f} ГиБ")
    print(" | ".join(parts), flush=True)
    for item in report["newAlerts"]:
        print(f"  {item['severity'].upper()}: {item['message']}", flush=True)
    for key in report["clearedAlerts"]:
        print(f"  Больше не наблюдается: {key}", flush=True)
    for change in report.get("changes", []):
        print(f"  {change}", flush=True)


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="Одна проверка (по умолчанию)")
    mode.add_argument("--watch", action="store_true", help="Наблюдать до Ctrl+C")
    parser.add_argument("--samples", type=int, help="Завершить watch после N проверок")
    parser.add_argument("--interval", type=float, default=30, help="Интервал, минимум 15 секунд")
    parser.add_argument("--url", default=ORIGIN)
    parser.add_argument("--ssh-target", default="root@91.84.97.114")
    parser.add_argument("--control-path", default="/tmp/quiz-monitor.sock")
    parser.add_argument("--known-hosts", default="/tmp/quiz-deploy-known-hosts")
    parser.add_argument("--output", type=Path, default=Path("/tmp/quiz-production-monitor"))
    parser.add_argument("--json", action="store_true", help="Печатать JSON вместо краткого статуса")
    args = parser.parse_args(argv)
    parsed = urllib.parse.urlsplit(args.url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ("", "/"):
        parser.error("--url: нужен HTTPS-адрес без пути, параметров и учётных данных")
    args.url = args.url.rstrip("/")
    if not re.fullmatch(r"[a-zA-Z0-9_.]+@[a-zA-Z0-9][a-zA-Z0-9.-]*", args.ssh_target):
        parser.error("--ssh-target: ожидается user@host")
    if not math.isfinite(args.interval) or args.interval < 15 or args.interval > 3600:
        parser.error("--interval: от 15 до 3600 секунд")
    if args.samples is not None and (not args.watch or args.samples < 1):
        parser.error("--samples: положительное число, используется с --watch")
    return args


def main(argv=None):
    args = arguments(argv)
    tracker = Tracker()
    since = time.time() - 120
    count = 0
    try:
        while True:
            started = time.monotonic()
            report = collect(args, since, cycle=count)
            tracker.update(report, once=not args.watch)
            save_report(args.output, report)
            print_report(report, args.json)
            timestamp = report["server"].get("collectedAt")
            if timestamp:
                since = datetime.fromisoformat(timestamp.replace("Z", "+00:00")).timestamp() - 2
            count += 1
            if not args.watch or (args.samples and count >= args.samples):
                return {"ok": 0, "warning": 1, "critical": 2}[report["status"]]
            time.sleep(max(0, args.interval - (time.monotonic() - started)))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
