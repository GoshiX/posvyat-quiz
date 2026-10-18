"""The standalone production probe is tested entirely against local fixtures."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest


SPEC = importlib.util.spec_from_file_location(
    "production_probe", Path(__file__).resolve().parents[1] / "tools" / "production_probe.py")
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)
Q1 = "00000000-0000-4000-8000-000000000001"
Q2 = "00000000-0000-4000-8000-000000000002"
Q3 = "00000000-0000-4000-8000-000000000003"
ICON = "https://storage.yandexcloud.net/public-bucket/quiz/00000000-0000-4000-8000-000000000001.webp"
MEDIA = "https://storage.yandexcloud.net/public-bucket/quiz/00000000-0000-4000-8000-000000000002.gif"


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "quiz ?#.sqlite"
    with sqlite3.connect(path) as connection:
        connection.executescript("""
            CREATE TABLE events(environment TEXT PRIMARY KEY,data TEXT);
            CREATE TABLE records(environment TEXT,kind TEXT,id TEXT,data TEXT);
            CREATE TABLE answers(environment TEXT,question_id TEXT,participant_id TEXT,option_index INTEGER,automatic INTEGER);
            CREATE TABLE results(environment TEXT,question_id TEXT,group_id TEXT,data TEXT);
            CREATE TABLE ballots(environment TEXT,participant_id TEXT,ratings TEXT);
            CREATE TABLE reveals(environment TEXT,group_id TEXT);
            CREATE TABLE sessions(token TEXT,role TEXT,environment TEXT,participant_id TEXT,expires INTEGER);
            CREATE TABLE global_settings(id INTEGER,data TEXT);
            CREATE TABLE oauth_states(state TEXT,data TEXT,expires INTEGER);
        """)
        connection.execute("INSERT INTO events VALUES (?,?)", ("production", json.dumps({
            "phase": "question_closed", "displayStage": "blank", "runId": 2, "revision": 73,
            "currentQuestionId": Q2, "settings": {"secret": "DO_NOT_RETURN_EVENT_SECRET"},
        })))
        for identifier, kind, data in (
            ("g1", "group", {"iconUrl": ICON, "mediaUrl": MEDIA}),
            ("g2", "group", {"iconUrl": "https://private.invalid/icon?token=SECRET", "mediaUrl": "/uploads/private.gif"}),
            ("p1", "participant", {"joined": True, "firstName": "PRIVATE_NAME", "email": "person@secret.test", "codeHash": "SECRET_HASH"}),
            ("p2", "participant", {"joined": True}), ("p3", "participant", {"joined": False}),
            (Q3, "question", {"position": 2, "text": "PRIVATE_QUESTION"}),
            (Q1, "question", {"position": 0}), (Q2, "question", {"position": 1}),
        ):
            connection.execute("INSERT INTO records VALUES (?,?,?,?)", ("production", kind, identifier, json.dumps(data)))
        connection.execute("INSERT INTO records VALUES ('rehearsal','participant','other',?)", (json.dumps({"joined": True}),))
        connection.executemany("INSERT INTO answers VALUES (?,?,?,?,?)", [
            ("production", Q2, "p1", 0, 0), ("production", Q2, "p2", 1, 0),
            ("production", Q2, "p3", 2, 1), ("production", Q1, "p1", 3, 0),
            ("rehearsal", Q2, "other", 0, 0),
        ])
        connection.execute("INSERT INTO results VALUES ('production',?,'g1','{}')", (Q2,))
        connection.executemany("INSERT INTO ballots VALUES (?,?,?)", [
            ("production", "p1", '{"g2":7}'), ("rehearsal", "other", '{}'),
        ])
        connection.executemany("INSERT INTO reveals VALUES (?,?)", [("production", "g1"), ("rehearsal", "other")])
        connection.executemany("INSERT INTO sessions VALUES (?,?,?,?,?)", [
            ("SECRET_TOKEN1", "participant", "production", "p1", 1_001_000),
            ("SECRET_TOKEN2", "participant", "production", "p1", 1_002_000),
            ("SECRET_TOKEN3", "participant", "production", "p2", 1_003_000),
            ("EXPIRED", "participant", "production", "p3", 1_000_000),
            ("ADMIN_TOKEN", "admin", "production", None, 1_002_000),
            ("REHEARSAL_TOKEN", "participant", "rehearsal", "other", 1_002_000),
        ])
        connection.execute("INSERT INTO global_settings VALUES (1,'SECRET_CONFIGURATION')")
        connection.execute("INSERT INTO oauth_states VALUES ('SECRET_STATE','SECRET_OAUTH',9999999)")
    return path


def test_missing_database_is_never_created(tmp_path):
    missing = tmp_path / "missing.sqlite"
    with pytest.raises(sqlite3.OperationalError):
        probe.collect_database(missing)
    assert list(tmp_path.iterdir()) == []


def test_production_snapshot_contains_only_counts_and_current_question(database):
    assets = []
    result = probe.collect_database(database, now=1000, assets=assets)
    assert result == {
        "phase": "question_closed", "displayStage": "blank", "runId": 2, "revision": 73,
        "question": 2, "currentQuestionId": Q2, "groups": 2, "participants": 3, "joined": 2,
        "questions": 3, "manualAnswers": 2, "automaticAnswers": 1, "ballots": 1, "reveals": 1,
        "validParticipantSessions": 3,
    }
    assert "online" not in result
    assert assets == [{"kind": "icon", "url": ICON}, {"kind": "media", "url": MEDIA}]
    serialized = json.dumps(result)
    for secret in ("PRIVATE", "SECRET", "person@secret.test", "p1", "correctIndex"):
        assert secret not in serialized


def test_setup_reports_question_zero(database):
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE events SET data=json_set(data,'$.phase','setup')")
    assert probe.collect_database(database, now=1000)["question"] == 0


def test_read_snapshot_cannot_modify_any_critical_table_or_create_files(database):
    before = database.read_bytes()
    directory_before = {path.name for path in database.parent.iterdir()}
    with probe.read_snapshot(database) as connection:
        for table in ("events", "records", "answers", "results", "ballots", "reveals",
                      "sessions", "global_settings", "oauth_states"):
            with pytest.raises(sqlite3.DatabaseError):
                connection.execute(f"DELETE FROM {table}")
        for statement in ("CREATE TABLE forbidden(value)", "PRAGMA query_only=OFF",
                          "PRAGMA journal_mode=DELETE", "VACUUM"):
            with pytest.raises(sqlite3.DatabaseError):
                connection.execute(statement)
        with pytest.raises(sqlite3.DatabaseError):
            connection.execute("ATTACH DATABASE ? AS forbidden", (str(database.parent / "forbidden.sqlite"),))
    assert database.read_bytes() == before
    assert {path.name for path in database.parent.iterdir()} == directory_before


def test_wal_snapshot_sees_commits_and_is_consistent_during_concurrent_writes(database):
    writer = sqlite3.connect(database)
    try:
        assert writer.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        writer.execute("UPDATE events SET data=json_set(data,'$.revision',74)")
        writer.commit()
        assert probe.collect_database(database, now=1000)["revision"] == 74
        with probe.read_snapshot(database) as reader:
            original = reader.execute("SELECT COUNT(*) FROM ballots").fetchone()[0]
            writer.execute("INSERT INTO ballots VALUES ('production','new','{}')")
            writer.commit()
            assert reader.execute("SELECT COUNT(*) FROM ballots").fetchone()[0] == original
        assert probe.collect_database(database, now=1000)["ballots"] == 2
    finally:
        writer.close()


def test_standalone_wal_reader_does_not_change_database_or_sidecars(database):
    writer = sqlite3.connect(database)
    try:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("UPDATE events SET data=json_set(data,'$.revision',74)")
        writer.commit()
        before = {path.name: path.read_bytes() for path in database.parent.iterdir()}
        # Separate process matches production: no existing writable SQLite
        # connections can supply a shared writable shm mapping to this reader.
        script = """import json, pathlib, sys
namespace = {'__name__': 'standalone_probe_test'}
source = pathlib.Path(sys.argv[1])
exec(compile(source.read_text(), str(source), 'exec'), namespace)
print(json.dumps(namespace['collect_database'](sys.argv[2], now=1000)))
"""
        result = subprocess.run([sys.executable, "-", str(SPEC.origin), str(database)], input=script,
                                text=True, capture_output=True, check=True, timeout=3)
        assert json.loads(result.stdout)["revision"] == 74
        assert {path.name: path.read_bytes() for path in database.parent.iterdir()} == before
    finally:
        writer.close()


def test_closed_wal_database_never_creates_missing_sidecars(database):
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.close()
    before = {path.name: path.read_bytes() for path in database.parent.iterdir()}
    assert not Path(str(database) + "-wal").exists()
    assert not Path(str(database) + "-shm").exists()
    with pytest.raises(sqlite3.OperationalError, match="sidecars unavailable"):
        probe.collect_database(database, now=1000)
    assert {path.name: path.read_bytes() for path in database.parent.iterdir()} == before


def test_expensive_snapshot_query_is_interrupted(database):
    with probe.read_snapshot(database, max_seconds=-1) as connection:
        with pytest.raises(sqlite3.OperationalError, match="interrupted"):
            connection.execute("SELECT SUM(a.automatic) FROM answers a CROSS JOIN answers b CROSS JOIN answers c CROSS JOIN answers d CROSS JOIN answers e").fetchone()


def journal_entry(message, *, priority="3", unit="quiz-caddy.service", cursor_number=1):
    return {"__CURSOR": f"s=abcdef;i={cursor_number:x};b=abcdef;m=12;t=123;x=45",
            "__REALTIME_TIMESTAMP": "1792224000123456", "_SYSTEMD_UNIT": unit,
            "PRIORITY": priority, "MESSAGE": message}


@pytest.mark.parametrize("message,status,level,category", [
    ({"level": "error", "msg": "context canceled", "status": 502}, 502, "info", "client_abort"),
    ({"level": "error", "msg": "dial tcp: connection refused", "status": 502}, 502, "warning", "upstream_connection"),
    ({"msg": "read: connection reset by peer", "status": 503}, 503, "warning", "upstream_connection"),
    ({"level": "error", "msg": "upstream unavailable", "status": 502}, 502, "warning", "http_5xx"),
    ({"level": "error", "msg": "uncaught exception"}, None, "warning", "service_error"),
    ({"msg": "TypeError: cannot read property"}, None, "warning", "service_error"),
    ({"msg": "RangeError: invalid value"}, None, "warning", "service_error"),
    ({"msg": "SQLITE_BUSY: database is locked"}, None, "warning", "service_error"),
    ({"msg": "unhandled rejection"}, None, "warning", "service_error"),
    ({"level": "error", "msg": "out of memory; process killed"}, None, "error", "oom"),
    ({"msg": "request complete", "status": 200}, 200, "info", "http"),
])
def test_journal_classification(message, status, level, category):
    event = probe.sanitize_journal_entry(journal_entry(json.dumps(message), priority="6"))
    assert event["status"] == status
    assert event["level"] == level
    assert event["category"] == category
    assert event["at"] == "2026-10-17T08:00:00.123Z"


def test_journal_never_returns_messages_urls_headers_or_secrets():
    message = {"msg": "connection refused https://private.test/?token=TOKEN_SECRET person@private.test",
               "status": 502, "error": "STACK_SECRET",
               "request": {"headers": {"Authorization": ["Bearer AUTH_SECRET"], "Cookie": ["COOKIE_SECRET"]}}}
    malicious = journal_entry(json.dumps(message))
    malicious["EXTRA_TOKEN"] = "EXTRA_SECRET"
    event = probe.sanitize_journal_entry(malicious)
    assert set(event) == {"id", "at", "unit", "level", "category", "status"}
    serialized = json.dumps(event)
    for secret in ("SECRET", "private.test", "Authorization", "Cookie", "https://", "MESSAGE"):
        assert secret not in serialized
    malicious["__CURSOR"] = "https://private.test/?token=TOKEN_SECRET"
    assert probe.sanitize_journal_entry(malicious) is None
    malicious["__CURSOR"] = "s=abc;i=1"
    malicious["_SYSTEMD_UNIT"] = "person@private.test"
    assert probe.sanitize_journal_entry(malicious) is None


def test_journal_limit_and_service_unit_whitelist():
    captured = []

    def runner(arguments):
        captured.append(arguments)
        return "\n".join(json.dumps(journal_entry("ordinary log", priority="6", cursor_number=index)) for index in range(205))

    result = probe.collect_journal(1000.25, runner=runner)
    assert result["truncated"] is True
    assert len(result["events"]) == 200
    assert "--since=@1000.250000" in captured[0]
    assert captured[0].count("-u") == 2
    assert result["events"][0]["id"].startswith("s=abcdef;i=5;")


def test_partial_report_returns_only_safe_error_classes(monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("SECRET_TOKEN person@private.test https://private.test")

    monkeypatch.setattr(probe, "collect_database", fail)
    monkeypatch.setattr(probe, "collect_journal", fail)
    monkeypatch.setattr(probe, "collect_service", fail)
    monkeypatch.setattr(probe, "collect_resources", lambda: {"cpuPercent": 2.5})
    report = probe.collect(1000)
    assert report["resources"]["cpuPercent"] == 2.5
    assert report["database"] == {}
    assert set(report["errors"].values()) == {"RuntimeError"}
    assert "SECRET" not in json.dumps(report)


def test_external_commands_are_bounded_and_noninteractive(monkeypatch):
    captured = {}

    def run(arguments, **kwargs):
        captured.update(kwargs)
        return subprocess.CompletedProcess(arguments, 0, stdout="safe output")

    monkeypatch.setattr(probe.subprocess, "run", run)
    assert probe.run_command(["systemctl", "show"]) == "safe output"
    assert captured["timeout"] <= 3
    assert captured["stdin"] == subprocess.DEVNULL
    assert captured["stderr"] == subprocess.DEVNULL
    assert not captured.get("shell", False)
    assert "HOME" not in captured["env"]


def test_service_uses_actual_soft_fd_limit_and_counts_descriptors(tmp_path):
    process = tmp_path / "42"
    (process / "fd").mkdir(parents=True)
    (process / "fd" / "0").touch()
    (process / "fd" / "1").touch()
    (process / "limits").write_text("Max open files            1024                 65536                files\n")
    result = probe.collect_service("initiation-quiz.service", proc_root=tmp_path, runner=lambda _: (
        "ActiveState=active\nSubState=running\nMainPID=42\nNRestarts=1\nMemoryCurrent=12345\nLimitNOFILE=65536\n"))
    assert result == {"active": "active", "sub": "running", "mainPid": 42, "restarts": 1,
                      "memoryBytes": 12345, "fdCount": 2, "fdLimit": 1024}


def test_https_connection_count_uses_local_port_and_established_state(tmp_path):
    (tmp_path / "net").mkdir()
    (tmp_path / "net" / "tcp").write_text(
        "header\n0: 00000000:01BB 11111111:CAFE 01\n1: 00000000:CAFE 11111111:01BB 01\n2: 00000000:01BB 11111111:CAFE 0A\n")
    (tmp_path / "net" / "tcp6").write_text("header\n0: 0000000000000000:01BB 1111111111111111:CAFE 01\n")
    assert probe.count_https_connections(tmp_path) == 2
