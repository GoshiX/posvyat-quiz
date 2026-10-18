"""S3 transport checks use a local SigV4-verifying server, never a cloud bucket."""
import base64
import concurrent.futures
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import re
import sqlite3
import threading
import urllib.parse

import pytest

from conftest import Client


ACCESS_KEY = "s3-local-test-access"
SECRET_KEY = "s3-local-test-secret"
BUCKET = "posvyat2026"
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=")
GIF = base64.b64decode("R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7")
CACHE_CONTROL = "public, max-age=31536000, immutable"


@pytest.fixture
def storage_server():
    state = {"objects": {}, "requests": [], "signature_errors": [], "deny_put": False,
             "deny_get": False, "corrupt_get": False, "deny_delete": False,
             "block_get": False, "get_started": threading.Event(), "release_get": threading.Event()}

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def respond(self, status, body=b"", content_type="application/xml"):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def deny(self):
            # The application must not expose response text from a remote S3 error.
            self.respond(403, f"<Error><Code>AccessDenied</Code><Message>{ACCESS_KEY} {SECRET_KEY}</Message></Error>".encode())

        def capture(self, body):
            request = {"method": self.command, "path": urllib.parse.urlsplit(self.path).path,
                       "headers": {key.lower(): value for key, value in self.headers.items()}, "body": body}
            state["requests"].append(request)
            return request

        def signed(self, body):
            try:
                auth = self.headers.get("Authorization", "")
                match = re.fullmatch(r"AWS4-HMAC-SHA256 Credential=([^/]+)/([^,]+), SignedHeaders=([^,]+), Signature=([a-f0-9]{64})", auth)
                assert match, "missing SigV4 authorization"
                access, scope, signed, signature = match.groups()
                date, region, service, terminal = scope.split("/")
                assert access == ACCESS_KEY and region == "ru-central1" and service == "s3" and terminal == "aws4_request"
                payload_hash = self.headers["x-amz-content-sha256"]
                assert payload_hash == hashlib.sha256(body).hexdigest()
                headers = "".join(f"{name}:{' '.join(self.headers[name].split())}\n" for name in signed.split(";"))
                parsed = urllib.parse.urlsplit(self.path)
                query = "&".join(f"{urllib.parse.quote(key, safe='-_.~')}={urllib.parse.quote(value, safe='-_.~')}"
                                 for key, value in sorted(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)))
                canonical = "\n".join([self.command, parsed.path, query, headers, signed, payload_hash])
                string_to_sign = "\n".join(["AWS4-HMAC-SHA256", self.headers["x-amz-date"], scope, hashlib.sha256(canonical.encode()).hexdigest()])
                signing_key = ("AWS4" + SECRET_KEY).encode()
                for value in [date, region, service, terminal]:
                    signing_key = hmac.new(signing_key, value.encode(), hashlib.sha256).digest()
                expected = hmac.new(signing_key, string_to_sign.encode(), hashlib.sha256).hexdigest()
                assert hmac.compare_digest(signature, expected), "invalid SigV4 signature"
                return True
            except (AssertionError, KeyError, TypeError, ValueError) as error:
                state["signature_errors"].append(str(error))
                self.deny()
                return False

        def do_PUT(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            request = self.capture(body)
            if not self.signed(body):
                return
            if state["deny_put"]:
                return self.deny()
            state["objects"][request["path"]] = body
            self.respond(200)

        def do_GET(self):
            request = self.capture(b"")
            if state["block_get"]:
                state["get_started"].set()
                state["release_get"].wait(timeout=8)
            if state["deny_get"]:
                return self.deny()
            body = state["objects"].get(request["path"])
            if body is None:
                return self.respond(404)
            self.respond(200, b"corrupted-image" if state["corrupt_get"] else body, "image/png")

        def do_DELETE(self):
            request = self.capture(b"")
            if not self.signed(b""):
                return
            if state["deny_delete"]:
                return self.deny()
            state["objects"].pop(request["path"], None)
            self.respond(204)

    service = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    service.daemon_threads = True
    thread = threading.Thread(target=service.serve_forever, daemon=True)
    thread.start()
    state["url"] = f"http://127.0.0.1:{service.server_port}"
    try:
        yield state
    finally:
        state["release_get"].set()
        service.shutdown()
        service.server_close()
        thread.join(timeout=5)


@pytest.fixture
def server_environment(storage_server):
    return {"NODE_ENV": "test", "S3_TEST_ENDPOINT": storage_server["url"]}


def configure(app, enabled=False, **changes):
    app.admin.post("/api/admin/storage", {"enabled": enabled, "bucket": BUCKET,
                                          "accessKeyId": ACCESS_KEY, "secretAccessKey": SECRET_KEY, **changes})
    return app.state()["storage"]


def check(app):
    app.admin.post("/api/admin/storage/test", {})
    return app.state()["storage"]


def upload(app, data=PNG, name="test.png", expected=200):
    return app.admin.post("/api/admin/upload", {"name": name, "data": base64.b64encode(data).decode()}, expected=expected)


def assert_redacted(value):
    serialized = json.dumps(value)
    assert ACCESS_KEY not in serialized and SECRET_KEY not in serialized
    assert "secretAccessKey" not in serialized


def test_default_settings_and_local_upload_stay_local(app, storage_server):
    storage = app.state()["storage"]
    assert storage == {"enabled": False, "bucket": BUCKET, "endpoint": "https://storage.yandexcloud.net",
                       "region": "ru-central1", "publicBaseUrl": "https://storage.yandexcloud.net/" + BUCKET,
                       "accessKeyIdSet": False, "secretSet": False, "lastTest": None}
    result = upload(app)
    assert re.fullmatch(r"/uploads/[a-f0-9-]+\.webp", result["url"])
    output = (app.data_dir / result["url"].lstrip("/")).read_bytes()
    assert output[:4] == b"RIFF" and output[8:12] == b"WEBP"
    assert storage_server["requests"] == []


def test_storage_keys_are_encrypted_redacted_and_preserved_by_blank_fields(app, storage_server):
    app.seed(groups=2)
    app.admin.post("/api/admin/settings", {"yandexClientId": "oauth-client", "yandexClientSecret": "oauth-secret", "publicUrl": app.url})
    before = {environment: app.state(environment) for environment in ["production", "rehearsal"]}
    configure(app)
    assert check(app)["lastTest"]["ok"] is True
    checked = app.state()["storage"]
    result = app.admin.post("/api/admin/storage", {"accessKeyId": "", "secretAccessKey": "   "})
    assert app.state()["storage"] == checked
    assert_redacted(result)
    for environment, previous in before.items():
        current = app.state(environment)
        assert {key: value for key, value in current.items() if key != "storage"} == {key: value for key, value in previous.items() if key != "storage"}
    with sqlite3.connect(app.data_dir / "quiz.sqlite") as database:
        raw = database.execute("SELECT data FROM global_settings WHERE id=1").fetchone()[0]
    stored = json.loads(raw)
    assert ACCESS_KEY not in raw and SECRET_KEY not in raw and "oauth-secret" not in raw
    assert stored["storage"]["accessKeyId"] and stored["storage"]["secret"]
    assert stored["storage"]["accessKeyId"] != stored["storage"]["secret"]
    assert stored["clientId"] == "oauth-client" and stored["secret"]
    app.restart()
    assert app.state()["storage"] == checked
    assert_redacted(app.state())


@pytest.mark.parametrize("body", [
    {"enabled": True}, {"enabled": "true"}, {"bucket": "A_BAD_BUCKET"}, {"bucket": "ab"},
    {"bucket": "a" * 64}, {"bucket": "127.0.0.1"}, {"bucket": "bucket..name"}, {"bucket": "https://invalid.example"},
    {"bucket": None}, {"accessKeyId": 123}, {"secretAccessKey": None},
    {"endpoint": "https://evil.invalid"}, {"region": "us-east-1"}, {"publicBaseUrl": "https://evil.invalid"},
])
def test_bad_config_is_rejected_without_network_or_mutation(app, storage_server, body):
    before = app.state()
    response = app.admin.post("/api/admin/storage", body, expected=400)
    assert_redacted(response)
    assert app.state() == before
    assert storage_server["requests"] == []


def test_probe_without_keys_records_configuration_failure_without_network(app, storage_server):
    result = check(app)
    assert result["lastTest"]["ok"] is False
    assert "Key" in result["lastTest"]["message"]
    assert storage_server["requests"] == []
    assert_redacted(result)


def test_probe_checks_signed_write_anonymous_read_and_cleanup_while_disabled(app, storage_server):
    configure(app)
    storage_server["objects"]["/posvyat2026/quiz/preexisting.png"] = PNG
    result = check(app)
    assert result["enabled"] is False and result["lastTest"]["ok"] is True
    assert result["lastTest"]["at"].endswith("Z")
    requests = storage_server["requests"]
    assert [request["method"] for request in requests] == ["PUT", "GET", "DELETE"]
    assert len({request["path"] for request in requests}) == 1
    assert re.fullmatch(r"/posvyat2026/quiz/check-[a-f0-9-]+\.png", requests[0]["path"])
    assert requests[0]["body"] == PNG
    assert requests[0]["headers"]["content-type"] == "image/png"
    assert requests[0]["headers"]["cache-control"] == CACHE_CONTROL
    assert "authorization" not in requests[1]["headers"]
    assert "cookie" not in requests[1]["headers"]
    assert storage_server["signature_errors"] == []
    assert storage_server["objects"] == {"/posvyat2026/quiz/preexisting.png": PNG}
    assert_redacted(result)
    # Upload routing does not affect the checked bucket credentials or public access.
    assert configure(app) == result
    for enabled in [True, False]:
        app.admin.post("/api/admin/storage", {"enabled": enabled})
        assert app.state()["storage"]["lastTest"] == result["lastTest"]


@pytest.mark.parametrize("changes", [{"bucket": "updated-bucket"}, {"accessKeyId": "rotated-access-key"}, {"secretAccessKey": "rotated-secret-key"}])
def test_bucket_or_key_change_clears_previous_connection_result(app, storage_server, changes):
    configure(app)
    assert check(app)["lastTest"]["ok"] is True
    app.admin.post("/api/admin/storage", changes)
    assert app.state()["storage"]["lastTest"] is None
    assert [request["method"] for request in storage_server["requests"]] == ["PUT", "GET", "DELETE"]


@pytest.mark.parametrize("failure", ["deny_get", "corrupt_get"])
def test_probe_rejects_private_or_wrong_public_bytes_and_cleans_up(app, storage_server, failure):
    configure(app)
    storage_server[failure] = True
    result = check(app)
    assert result["lastTest"]["ok"] is False
    assert "публичное чтение" in result["lastTest"]["message"]
    assert [request["method"] for request in storage_server["requests"]] == ["PUT", "GET", "DELETE"]
    assert storage_server["objects"] == {}
    assert storage_server["signature_errors"] == []
    assert_redacted(result)


def test_probe_write_and_cleanup_errors_are_redacted_and_reported(app, storage_server):
    configure(app)
    storage_server["deny_put"] = True
    result = check(app)
    assert result["lastTest"]["ok"] is False
    assert "право записи" in result["lastTest"]["message"]
    assert all(request["method"] != "GET" for request in storage_server["requests"])
    assert storage_server["objects"] == {}
    assert_redacted(result)
    storage_server["deny_put"] = False
    storage_server["deny_delete"] = True
    result = check(app)
    assert result["lastTest"]["ok"] is False
    assert "право удаления" in result["lastTest"]["message"]
    assert len(storage_server["objects"]) == 1
    assert all("/quiz/check-" in key for key in storage_server["objects"])
    assert storage_server["signature_errors"] == []
    assert_redacted(result)


@pytest.mark.parametrize("data,extension,content_type", [(PNG, "webp", "image/webp"), (GIF, "gif", "image/gif")])
def test_enabled_upload_uses_signed_s3_and_immutable_cache(app, storage_server, data, extension, content_type):
    configure(app, enabled=True)
    urls = [upload(app, data, "../../user-chosen.exe")["url"] for _ in range(2)]
    assert len(set(urls)) == 2
    for url in urls:
        assert re.fullmatch(rf"https://storage\.yandexcloud\.net/posvyat2026/quiz/[a-f0-9-]+\.{extension}", url)
        output = storage_server["objects"][urllib.parse.urlsplit(url).path]
        if extension == "gif":
            assert output == data
        else:
            assert output[:4] == b"RIFF" and output[8:12] == b"WEBP"
    assert list((app.data_dir / "uploads").iterdir()) == []
    assert [request["method"] for request in storage_server["requests"]] == ["PUT", "PUT"]
    for request in storage_server["requests"]:
        assert request["headers"]["content-type"] == content_type
        assert request["headers"]["cache-control"] == CACHE_CONTROL
    assert storage_server["signature_errors"] == []
    assert_redacted(app.state())


def test_s3_urls_are_saved_and_published_after_storage_is_disabled_or_changed(app, storage_server):
    app.seed(groups=2)
    configure(app, enabled=True)
    url = upload(app, GIF, "group.gif")["url"]
    group = {**app.groups[1], "iconUrl": url, "mediaUrl": url}
    app.admin.post("/api/admin/groups", group)
    app.admin.post("/api/admin/settings", {"logoUrl": url})
    before = app.state()
    foreign_url = url.replace("storage.yandexcloud.net", "storage.yandexcloud.net.evil.invalid")
    for field in ["iconUrl", "mediaUrl"]:
        app.admin.post("/api/admin/groups", {**group, field: foreign_url}, expected=400)
    app.admin.post("/api/admin/settings", {"logoUrl": foreign_url}, expected=400)
    assert app.state() == before

    participant = app.participant(app.people[0]["code"])
    public = Client(app.url)
    app.finish_quiz()
    app.action("open_voting")

    def assert_saved_and_published():
        state = app.state()
        saved = next(value for value in state["groups"] if value["id"] == group["id"])
        assert saved["iconUrl"] == url and saved["mediaUrl"] == url
        assert state["settings"]["logoUrl"] == url
        view = participant.get("/api/participant")
        target = next(value for value in view["voting"]["groups"] if value["id"] == group["id"])
        assert target["mediaUrl"] == url and view["branding"]["logoUrl"] == url
        assert public.get("/api/participant")["branding"]["logoUrl"] == url

    assert_saved_and_published()
    for changes in [{"enabled": False}, {"bucket": "new-upload-bucket"}]:
        app.admin.post("/api/admin/storage", changes)
        assert_saved_and_published()
    app.restart()
    assert_saved_and_published()
    app.action("close_voting")
    app.action("show_audience")
    projected = next(value for value in public.get("/api/display")["groups"] if value["id"] == group["id"])
    assert projected["iconUrl"] == url
    assert storage_server["objects"][urllib.parse.urlsplit(url).path] == GIF
    assert [request["method"] for request in storage_server["requests"]] == ["PUT"]
    assert storage_server["signature_errors"] == []


def test_s3_upload_failure_is_visible_and_does_not_fall_back_to_local_files(app, storage_server):
    configure(app, enabled=True)
    before = app.state()
    storage_server["deny_put"] = True
    result = upload(app, expected=502)
    assert "Object Storage" in result["error"] and "право записи" in result["error"]
    assert_redacted(result)
    assert app.state() == before
    assert list((app.data_dir / "uploads").iterdir()) == []
    assert storage_server["objects"] == {}


def test_storage_routes_require_admin_session(app, storage_server):
    app.seed(groups=2)
    clients = [Client(app.url), app.participant(app.people[0]["code"])]
    for client in clients:
        client.post("/api/admin/storage", {"accessKeyId": ACCESS_KEY, "secretAccessKey": SECRET_KEY}, expected=401)
        client.post("/api/admin/storage/test", {}, expected=401)
    assert storage_server["requests"] == []


@pytest.mark.parametrize("change_storage", [False, True])
def test_probe_cannot_overwrite_settings_changed_during_network_check(app, storage_server, change_storage):
    configure(app)
    storage_server["block_get"] = True
    client = Client(app.url)
    client.post("/api/admin/login", {"username": "admin", "password": "quiz-admin-2026"})
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(client.post, "/api/admin/storage/test", {})
        try:
            assert storage_server["get_started"].wait(timeout=5)
            if change_storage:
                app.admin.post("/api/admin/storage", {"bucket": "updated-bucket"})
            app.admin.post("/api/admin/settings", {"yandexClientId": "updated-oauth-id", "yandexClientSecret": "updated-oauth-secret"})
        finally:
            storage_server["release_get"].set()
        pending.result(timeout=10)
    state = app.state()
    if change_storage:
        assert state["storage"]["bucket"] == "updated-bucket" and state["storage"]["lastTest"] is None
    else:
        assert state["storage"]["bucket"] == BUCKET and state["storage"]["lastTest"]["ok"] is True
    assert state["yandex"]["clientId"] == "updated-oauth-id"
    assert storage_server["objects"] == {}
    assert all(request["path"].startswith("/posvyat2026/quiz/check-") for request in storage_server["requests"])
    assert_redacted(state)
