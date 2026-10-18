"""Safety and alert semantics for the local production monitor; no production access."""
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
from threading import Thread

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import monitor_production as monitor


def healthy_report():
    state = {"phase": "question_open", "currentQuestionId": "q1", "runId": 3, "revision": 12}
    data = {"/api/health": {"ok": True}, "/api/participant": state,
            "/api/quiz/results": {"question": 1, "ready": False, "groups": 0},
            "/api/display": {"stage": "blank", "groups": 0, "runId": 3, "revision": 12}}
    return {"checkedAt": "2026-10-17T11:00:00+00:00", "http": [
        {"path": path, "ok": True, "ms": 100, "cache": "no-store", "data": payload}
        for path, payload in data.items()], "server": {
        "collectedAt": "2026-10-17T11:00:00+00:00", "errors": {},
        "services": {unit: {"active": "active", "sub": "running", "mainPid": 123,
                           "fdCount": 100, "fdLimit": 65536} for unit in monitor.UNITS},
        "resources": {"cpuPercent": 5, "load1": .1, "cpuCount": 2,
                      "memoryTotalBytes": 4e9, "memoryAvailableBytes": 2e9,
                      "diskTotalBytes": 80e9, "diskFreeBytes": 70e9},
        "database": {**state, "displayStage": "blank", "question": 1, "questions": 27,
                     "groups": 27, "participants": 464, "joined": 200,
                     "manualAnswers": 30, "automaticAnswers": 0, "ballots": 0},
        "journal": {"events": [], "truncated": False}}}


def test_http_probe_never_follows_redirect_into_auth_or_sends_mutation():
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append((self.command, self.path))
            self.send_response(302)
            self.send_header("Location", "/auth/yandex/start")
            self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = monitor.http_probe(f"http://127.0.0.1:{server.server_port}", "/api/health")
        assert result["ok"] is False and result["status"] == 302
        assert requests == [("GET", "/api/health")]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_failed_ssh_does_not_report_site_down_or_all_healthy():
    report = healthy_report()
    report["server"] = {"errors": {"ssh": "TimeoutExpired"}}
    monitor.Tracker().update(report, once=True)
    assert report["status"] == "warning"
    assert [a["key"] for a in report["alerts"]] == ["probe:ssh"]
    report["http"][0].update(ok=False, status=503)
    monitor.Tracker().update(report, once=True)
    assert report["status"] == "critical"


def test_journal_overlap_is_deduplicated_and_client_disconnect_is_not_alarm():
    report = healthy_report()
    report["server"]["journal"]["events"] = [
        {"id": "one", "level": "info", "category": "client_disconnected"},
        {"id": "two", "level": "warning", "category": "upstream_unavailable", "status": 502},
    ]
    second = deepcopy(report)
    tracker = monitor.Tracker()
    tracker.update(report)
    assert report["status"] == "warning" and len(report["newAlerts"]) == 1
    tracker.update(second)
    assert second["status"] == "ok" and not second["newAlerts"]
    assert not second["server"]["journal"]["events"]


def test_cross_request_transition_does_not_trigger_lamp_alarm():
    report = healthy_report()
    report["http"][2]["data"].update(ready=True, groups=27)
    report["http"][1]["data"]["revision"] += 1
    assert not monitor.findings(report)
    report["http"][1]["data"]["revision"] -= 1
    second = deepcopy(report)
    tracker = monitor.Tracker()
    tracker.update(report)
    assert not report["newAlerts"] and report["pending"][0]["key"] == "lamps"
    tracker.update(second)
    assert second["newAlerts"][0]["key"] == "lamps"


def test_zero_free_memory_and_stopped_service_raise_critical():
    report = healthy_report()
    report["server"]["resources"]["memoryAvailableBytes"] = 0
    report["server"]["services"][monitor.UNITS[0]]["active"] = "failed"
    tracker = monitor.Tracker()
    tracker.update(report)
    assert report["status"] == "critical"
    assert {a["key"] for a in report["alerts"]} == {"memory", "service:" + monitor.UNITS[0]}


def test_reports_do_not_keep_participant_fields_or_answer_maps(tmp_path):
    payload = {"authenticated": False, "participant": {"email": "PRIVATE_EMAIL", "code": "SECRET"},
               "state": healthy_report()["http"][1]["data"], "yandexEnabled": True}
    safe = monitor.summarize_payload("/api/participant", payload)
    assert "PRIVATE_EMAIL" not in json.dumps(safe) and "SECRET" not in json.dumps(safe)
    groups = monitor.summarize_payload("/api/quiz/results", {"question": 1, "ready": True, "groups": {"GROUP": "g"}})
    assert groups == {"question": 1, "ready": True, "groups": 1}
    report = healthy_report()
    monitor.Tracker().update(report)
    monitor.save_report(tmp_path, report)
    assert json.loads((tmp_path / "latest.json").read_text()) == report
    assert (tmp_path / "latest.json").stat().st_mode & 0o777 == 0o600
    assert (tmp_path / "history.jsonl").stat().st_mode & 0o777 == 0o600


def test_configuration_cannot_create_busy_loop_or_target_auth_endpoint():
    for argv in [["--interval", "nan"], ["--interval", "inf"], ["--interval", "0"],
                 ["--url", "https://example.com/auth/yandex/start"], ["--ssh-target", "--bad"]]:
        with pytest.raises(SystemExit):
            monitor.arguments(argv)
    assert monitor.arguments([]).interval == 30


def test_s3_probe_refuses_non_storage_targets():
    result = monitor.asset_probe({"kind": "icon", "url": monitor.ORIGIN + "/auth/yandex/start"})
    assert result["ok"] is False and result["error"] == "UnexpectedAssetURL"
