"""OAuth transport contract against local external mocks, not real Yandex credentials."""
import base64
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import urllib.error
import urllib.parse
import urllib.request

import pytest

from conftest import Client


@pytest.fixture
def oauth_server():
    captured = {"tokens": [], "users": []}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def respond(self, status, body):
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            assert self.path == "/token"
            body = self.rfile.read(int(self.headers["Content-Length"]))
            params = dict(urllib.parse.parse_qsl(body.decode()))
            captured["tokens"].append(params)
            if params["code"] == "token-failure":
                self.respond(400, {"error": "invalid_grant"})
            else:
                self.respond(200, {"access_token": "mock-" + params["code"], "token_type": "bearer"})

        def do_GET(self):
            assert self.path.startswith("/user?format=json")
            token = self.headers.get("Authorization", "")
            captured["users"].append(token)
            email = "outside@yandex.ru" if "unknown" in token else "p1-1@yandex.ru"
            self.respond(200, {"id": "external-test-id", "default_email": email})

    service = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=service.serve_forever, daemon=True)
    thread.start()
    captured["url"] = f"http://127.0.0.1:{service.server_port}"
    yield captured
    service.shutdown()
    service.server_close()
    thread.join(timeout=5)


@pytest.fixture
def server_environment(oauth_server):
    url = oauth_server["url"]
    return {"YANDEX_AUTHORIZE_URL": url + "/authorize", "YANDEX_TOKEN_URL": url + "/token", "YANDEX_USER_URL": url + "/user"}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def raw_get(client, path, expected):
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(client.cookies), NoRedirect)
    try:
        response = opener.open(client.base_url + path, timeout=10)
    except urllib.error.HTTPError as response:
        assert response.status == expected, (path, response.status, response.read())
        return response.headers
    assert response.status == expected
    return response.headers


def setup_oauth(app):
    app.seed(groups=3)
    app.admin.post("/api/admin/settings", {
        "yandexEnabled": True, "yandexClientId": "mock-client", "yandexClientSecret": "mock-secret-only",
        "publicUrl": app.url,
    })


def start(client, suffix=""):
    headers = raw_get(client, "/auth/yandex/start" + suffix, 302)
    params = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(headers["Location"]).query))
    assert params["response_type"] == "code"
    assert params["scope"] == "login:email"
    assert params["code_challenge_method"] == "S256"
    assert len(params["state"]) >= 32
    assert len(params["code_challenge"]) >= 40
    assert "HttpOnly" in headers["Set-Cookie"]
    return params


def callback(client, params, code="known", extra="", expected=302):
    return raw_get(client, "/auth/yandex/callback?" + urllib.parse.urlencode({"state": params["state"], "code": code}) + extra, expected)


def test_mock_oauth_pkce_allowlist_restores_same_participant_and_answers(app, oauth_server):
    setup_oauth(app)
    previous = app.participant(app.people[0]["code"])
    identity = previous.get("/api/participant")["participant"]
    app.action("start_quiz")
    previous.post("/api/answer", {"questionId": app.questions[0]["id"], "optionIndex": 2})
    client = Client(app.url)
    params = start(client)
    assert params["client_id"] == "mock-client"
    assert params["redirect_uri"] == app.url + "/auth/yandex/callback"
    assert callback(client, params)["Location"] == "/"
    result = client.get("/api/participant")
    assert result["participant"]["id"] == identity["id"]
    assert result["answer"] == {"optionIndex": 2, "automatic": False}
    assert app.state()["stats"]["joinedCount"] == 1
    token = oauth_server["tokens"][0]
    assert token["grant_type"] == "authorization_code"
    assert token["client_id"] == "mock-client"
    assert token["client_secret"] == "mock-secret-only"
    assert token["code"] == "known"
    challenge = base64.urlsafe_b64encode(hashlib.sha256(token["code_verifier"].encode()).digest()).decode().rstrip("=")
    assert challenge == params["code_challenge"]
    assert oauth_server["users"] == ["OAuth mock-known"]


def test_mock_unknown_email_cannot_use_browser_email_or_create_participant(app):
    setup_oauth(app)
    client = Client(app.url)
    params = start(client)
    headers = callback(client, params, "unknown", "&email=p1-1%40yandex.ru")
    assert "authError=" in headers["Location"]
    assert client.get("/api/participant")["authenticated"] is False
    assert len(app.state()["participants"]) == 3
    assert app.state()["stats"]["joinedCount"] == 0


def test_mock_oauth_rejects_wrong_state_binding_and_replay(app, oauth_server):
    setup_oauth(app)
    client = Client(app.url)
    params = start(client)
    callback(client, {"state": "forged-state"}, expected=400)
    callback(Client(app.url), params, expected=400)
    assert oauth_server["tokens"] == []
    callback(client, params)
    assert len(oauth_server["tokens"]) == 1
    callback(client, params, expected=400)
    assert len(oauth_server["tokens"]) == 1


def test_mock_admin_test_records_result_without_participant_login(app, oauth_server):
    setup_oauth(app)
    raw_get(Client(app.url), "/auth/yandex/start?test=1", 401)
    params = start(app.admin, "?test=1")
    assert callback(app.admin, params)["Location"] == "/admin?yandex=success"
    state = app.state()
    assert state["yandex"]["lastTest"]["ok"] is True
    assert state["yandex"]["lastTest"]["email"] == "p1-1@yandex.ru"
    assert app.admin.get("/api/participant")["authenticated"] is False
    assert state["stats"]["joinedCount"] == 0
    params = start(app.admin, "?test=1")
    assert callback(app.admin, params, "token-failure")["Location"] == "/admin?yandex=error"
    assert app.state()["yandex"]["lastTest"]["ok"] is False
    assert "mock-secret-only" not in str(app.state())
