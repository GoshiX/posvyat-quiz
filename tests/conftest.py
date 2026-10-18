"""Isolated HTTP/SQLite integration fixtures. No production files are modified."""
from __future__ import annotations

import http.cookiejar
import json
import os
from pathlib import Path
import socket
import subprocess
import time
import urllib.error
import urllib.request

import pytest

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = Path(os.environ.get("QUIZ_TEST_ARTIFACTS", "/tmp/quiz-e2e-artifacts"))


class Client:
    def __init__(self, base_url):
        self.base_url = base_url
        self.cookies = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cookies))

    def request(self, method, path, body=None, expected=200):
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(self.base_url + path, data=data, method=method)
        if body is not None:
            request.add_header("Content-Type", "application/json")
        try:
            response = self.opener.open(request, timeout=10)
        except urllib.error.HTTPError as error:
            response = error
        raw = response.read()
        try:
            result = json.loads(raw)
        except ValueError:
            result = raw.decode(errors="replace")
        assert response.status == expected, (method, path, response.status, result)
        return result

    def get(self, path, expected=200):
        return self.request("GET", path, expected=expected)

    def post(self, path, body, expected=200):
        return self.request("POST", path, body, expected)


class App:
    def __init__(self, base_url, data_dir):
        self.url = base_url
        self.data_dir = data_dir
        self.admin = Client(base_url)
        self.admin.post("/api/admin/login", {"username": "admin", "password": "quiz-admin-2026"})
        self.groups = []
        self.people = []
        self.questions = []

    def state(self, environment="production"):
        return self.admin.get(f"/api/admin/state?env={environment}")

    def action(self, action, group_id=None, environment="production", expected=200):
        payload = {"action": action}
        if group_id is not None:
            payload["groupId"] = group_id
        return self.admin.post(f"/api/admin/action?env={environment}", payload, expected)

    def seed(self, groups=5, per_group=1, questions=1, trim=0):
        self.admin.post("/api/admin/settings", {"trimCount": trim})
        for index in range(groups):
            number = index + 1
            self.admin.post("/api/admin/groups", {
                "number": str(number), "title": f"Группа {number}",
                "description": f"Работа группы {number}", "iconUrl": "", "mediaUrl": "",
                "baseScore": 10 + index,
            })
        self.groups = self.state()["groups"]
        for index, group in enumerate(self.groups):
            for member in range(per_group):
                code = f"group{index + 1}-person{member + 1}"
                self.admin.post("/api/admin/participants", {
                    "firstName": f"Участник{index + 1}", "lastName": f"Фамилия{member + 1}",
                    "groupId": group["id"], "email": f"p{index + 1}-{member + 1}@yandex.ru", "code": code,
                })
                self.people.append({"code": code, "groupId": group["id"]})
        for index in range(questions):
            self.admin.post("/api/admin/questions", {
                "text": f"Вопрос {index + 1}: выберите верный ответ",
                "options": [f"Верный {index + 1}", "Неверный B", "Неверный C", "Неверный D"],
                "correctIndex": 0,
            })
        self.questions = self.state()["questions"]
        return self

    def participant(self, code, environment="production"):
        client = Client(self.url)
        client.post("/api/login", {"code": code, "environment": environment})
        return client

    def finish_quiz(self):
        self.action("start_quiz")
        for index, question in enumerate(self.questions):
            if index:
                self.action("next_question")
            self.action("close_question")
            self.action("reveal_question")
            if index + 1 < len(self.questions):
                self.action("wait")
        self.action("finish_quiz")


@pytest.fixture
def server_environment():
    return {}


@pytest.fixture
def server(tmp_path, server_environment):
    port = int(os.environ.get("QUIZ_TEST_PORT", "3101"))
    with socket.socket() as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            pytest.fail(f"Test port {port} is occupied; choose QUIZ_TEST_PORT explicitly")
    log = tmp_path / "server.log"
    output = log.open("w+")
    environment = {**os.environ, "PORT": str(port), "HOST": "127.0.0.1", "DATA_DIR": str(tmp_path / "data"),
                   "ADMIN_USERNAME": "admin", "ADMIN_PASSWORD": "quiz-admin-2026"}
    environment.update(server_environment)
    base_url = f"http://127.0.0.1:{port}"
    def start():
        process = subprocess.Popen(["node", "server/index.mjs"], cwd=ROOT, env=environment, stdout=output, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + 15
        client = Client(base_url)
        ready = False
        while time.monotonic() < deadline:
            if process.poll() is not None:
                break
            try:
                if client.get("/api/health") == {"ok": True}:
                    ready = True
                    break
            except (urllib.error.URLError, ConnectionError):
                time.sleep(0.05)
        if not ready:
            process.terminate()
            process.wait(timeout=5)
            output.flush()
            pytest.fail(f"Test server failed to start:\n{log.read_text()}")
        return process
    process = start()

    def restart():
        nonlocal process
        process.terminate()
        process.wait(timeout=5)
        process = start()

    try:
        yield base_url, tmp_path / "data", restart
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        output.close()


@pytest.fixture
def app(server):
    instance = App(server[0], server[1])

    def restart():
        server[2]()
        instance.admin = Client(instance.url)
        instance.admin.post("/api/admin/login", {"username": "admin", "password": "quiz-admin-2026"})

    instance.restart = restart
    return instance


@pytest.fixture(scope="session")
def browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        yield browser
        browser.close()


@pytest.fixture
def mobile(browser):
    context = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
    yield context
    context.close()


def screenshot(page, name):
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(ARTIFACTS / f"{name}.png"), full_page=True)
