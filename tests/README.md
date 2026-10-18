Tests start an isolated real Node HTTP/SQLite process for each case. They never use or modify the normal application data directory. The default test port is 3101; change it with `QUIZ_TEST_PORT` if needed. Chromium tests use real clicks, browser cookies, independent devices, polling and page reloads. API setup keeps the browser scenarios focused while separate HTTP tests verify scoring and access rules.

```sh
npm ci
python3 -m venv /tmp/quiz-test-env
/tmp/quiz-test-env/bin/python -m pip install -r requirements-test.txt
PLAYWRIGHT_BROWSERS_PATH=/tmp/quiz-test-env/browsers /tmp/quiz-test-env/bin/python -m playwright install chromium
PLAYWRIGHT_BROWSERS_PATH=/tmp/quiz-test-env/browsers /tmp/quiz-test-env/bin/python -m pytest tests -q
```

Screenshots are written to `/tmp/quiz-e2e-artifacts` by default (`QUIZ_TEST_ARTIFACTS` overrides it). Browser installation may require permitted network access. The suite does not emulate success of real Yandex authorization: that requires the organizer's actual registered client and public callback address. The separate OAuth transport test uses only mock external HTTP responses and is not evidence of production credential validation.

`node --test server/*.test.mjs` includes real Sharp decode/resize/animation checks. `tests/test_storage.py` uses a loopback S3 stand-in that independently verifies AWS SigV4, anonymous reads and probe cleanup; browser storage checks mock external objects. No cloud credentials or production data are used. These tests do not replace «Проверить хранилище» with the real bucket and key. The `S3_TEST_ENDPOINT` override only accepts loopback and is ignored when `NODE_ENV=production`.
