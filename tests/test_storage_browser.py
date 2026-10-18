"""Storage settings and upload controls in Chromium, without contacting real S3."""
import base64
import json
import re

from playwright.sync_api import expect

from conftest import screenshot


def open_storage_settings(page, app):
    page.goto(app.url + "/admin")
    page.get_by_test_id("admin-username").fill("admin")
    page.get_by_test_id("admin-password").fill("quiz-admin-2026")
    page.get_by_test_id("admin-login").click()
    page.get_by_test_id("admin-nav-settings").click()
    expect(page.get_by_test_id("storage-panel")).to_be_visible()


def test_storage_settings_preserve_drafts_hide_keys_and_test_saved_configuration(app, browser):
    app.seed(groups=3)
    app.action("start_quiz")
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    try:
        page = context.new_page()
        test_requests = []
        test_results = [
            {"ok": False, "message": "Проверьте права сервисного аккаунта на бакет.", "at": "2026-10-17T10:00:00Z"},
            {"ok": True, "message": "Доступ к бакету подтверждён.", "at": "2026-10-17T10:01:00Z"},
            None,
        ]
        displayed_result = {"value": None}

        def state_with_test_result(route):
            response = route.fetch()
            payload = response.json()
            if displayed_result["value"] is not None:
                payload["storage"]["lastTest"] = displayed_result["value"]
            route.fulfill(response=response, json=payload)

        def mock_connection_test(route):
            test_requests.append(route.request.post_data_json)
            result = test_results.pop(0)
            if result is None:
                route.fulfill(status=503, json={"error": "Сервис временно недоступен"})
            else:
                displayed_result["value"] = result
                route.fulfill(json={"ok": result["ok"]})

        page.route("**/api/admin/state*", state_with_test_result)
        page.route("**/api/admin/storage/test*", mock_connection_test)
        open_storage_settings(page, app)
        expect(page.get_by_role("heading", name="Хранилище изображений", exact=True)).to_be_visible()
        expect(page.get_by_test_id("storage-panel")).to_contain_text("общие для рабочего режима и репетиции")
        expect(page.get_by_test_id("storage-bucket")).to_have_value("posvyat2026")
        expect(page.get_by_test_id("storage-publicBaseUrl")).to_have_attribute("readonly", "")
        expect(page.get_by_test_id("storage-publicBaseUrl")).to_have_value("https://storage.yandexcloud.net/posvyat2026")
        for field in ("storage-accessKeyId", "storage-secretAccessKey"):
            expect(page.get_by_test_id(field)).to_have_attribute("type", "password")
            expect(page.get_by_test_id(field)).to_have_attribute("autocomplete", "off")
            expect(page.get_by_test_id(field)).to_have_value("")
        page.get_by_test_id("storage-guide").locator("summary").click()
        expect(page.get_by_test_id("storage-guide")).to_contain_text("Безопасность → Права доступа → Назначить роли")

        page.get_by_test_id("storage-bucket").fill("posvyat2026-browser")
        page.get_by_test_id("storage-accessKeyId").fill("browser-access-key-only")
        page.get_by_test_id("storage-secretAccessKey").fill("browser-secret-key-only")
        page.get_by_test_id("yandex-clientId").fill("unfinished-yandex-setting")
        page.wait_for_timeout(1300)
        expect(page.get_by_test_id("storage-bucket")).to_have_value("posvyat2026-browser")
        expect(page.get_by_test_id("storage-accessKeyId")).to_have_value("browser-access-key-only")
        expect(page.get_by_test_id("storage-secretAccessKey")).to_have_value("browser-secret-key-only")
        expect(page.get_by_test_id("test-storage")).to_be_disabled()
        assert test_requests == []

        with page.expect_response(lambda response: "/api/admin/storage?" in response.url and response.request.method == "POST") as saved:
            page.get_by_role("button", name="Сохранить хранилище", exact=True).click()
        assert saved.value.ok
        expect(page.get_by_test_id("storage-accessKeyId")).to_have_value("")
        expect(page.get_by_test_id("storage-secretAccessKey")).to_have_value("")
        expect(page.get_by_test_id("yandex-clientId")).to_have_value("unfinished-yandex-setting")
        expect(page.get_by_test_id("test-storage")).to_be_enabled()
        stored = app.state()["storage"]
        assert stored["bucket"] == "posvyat2026-browser"
        assert stored["accessKeyIdSet"] is True and stored["secretSet"] is True
        assert stored["lastTest"] is None
        assert "browser-access-key-only" not in json.dumps(app.state())
        assert "browser-secret-key-only" not in json.dumps(app.state())
        assert test_requests == []

        page.get_by_test_id("test-storage").click()
        expect(page.get_by_test_id("storage-test-result")).to_contain_text("Проверка хранилища не пройдена")
        expect(page.get_by_test_id("storage-test-result")).to_contain_text("Проверьте права сервисного аккаунта")
        page.get_by_test_id("test-storage").click()
        expect(page.get_by_test_id("storage-test-result")).to_contain_text("Хранилище успешно проверено")
        page.get_by_test_id("test-storage").click()
        expect(page.get_by_test_id("storage-test-result")).to_contain_text("Не удалось проверить хранилище")
        assert test_requests == [{}, {}, {}]

        page.get_by_test_id("storage-enabled").check()
        expect(page.get_by_test_id("test-storage")).to_be_disabled()
        page.get_by_role("button", name="Сохранить хранилище", exact=True).click()
        page.wait_for_function("async () => (await (await fetch('/api/admin/state')).json()).storage.enabled")
        assert app.state()["state"]["phase"] == "question_open"
        assert app.state("rehearsal")["storage"]["enabled"] is True
        assert app.state("rehearsal")["storage"]["bucket"] == "posvyat2026-browser"
        page.reload()
        page.get_by_test_id("admin-nav-settings").click()
        expect(page.get_by_test_id("storage-enabled")).to_be_checked()
        expect(page.get_by_test_id("storage-accessKeyId")).to_have_value("")
        expect(page.get_by_test_id("storage-secretAccessKey")).to_have_value("")
        expect(page.get_by_test_id("storage-bucket")).to_have_value("posvyat2026-browser")
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
        screenshot(page, "admin-storage-settings-1440x900")
        assert test_requests == [{}, {}, {}]
    finally:
        context.close()


def test_uploads_send_image_purpose_show_progress_and_keep_previews(app, browser):
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    try:
        page = context.new_page()
        uploads = []
        pending = []

        def inspect_upload(route):
            uploads.append(route.request.post_data_json)
            if len(uploads) == 1:
                pending.append(route)
            else:
                route.continue_()

        page.route("**/api/admin/upload*", inspect_upload)
        open_storage_settings(page, app)
        gif = base64.b64decode("R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7")
        image = {"name": "upload.gif", "mimeType": "image/gif", "buffer": gif}
        page.get_by_test_id("settings-logoUrl-file").set_input_files(image)
        expect(page.locator('label[for="settings-logoUrl-file"] span')).to_have_text("Обрабатываем и загружаем…")
        assert len(pending) == 1
        pending.pop().continue_()
        expect(page.get_by_test_id("settings-logoUrl")).to_have_value(re.compile(r"^/uploads/"))
        expect(page.locator('label[for="settings-logoUrl-file"] span')).to_have_text("Загрузить")
        expect(page.locator('[data-preview="settings-logoUrl"] img')).to_be_visible()
        page.get_by_role("button", name="Сохранить оформление", exact=True).click()
        expect(page.locator("#branding-form")).to_have_attribute("data-dirty", "false")
        page.get_by_test_id("admin-nav-groups").click()
        for field in ("group-iconUrl", "group-mediaUrl"):
            page.get_by_test_id(field + "-file").set_input_files(image)
            expect(page.get_by_test_id(field)).to_have_value(re.compile(r"^/uploads/"))
            preview = page.locator(f'[data-preview="{field}"] img')
            expect(preview).to_be_visible()
            expect(preview).to_have_js_property("complete", True)
            assert preview.evaluate("image => image.naturalWidth > 0")
        assert [upload["purpose"] for upload in uploads] == ["logo", "icon", "card"]
        assert all(upload["name"] == "upload.gif" for upload in uploads)
        assert app.state()["state"]["phase"] == "setup"
    finally:
        context.close()


def test_stored_s3_images_load_for_participant_and_projector_under_real_csp(app, browser):
    app.seed(groups=3)
    base = "https://storage.yandexcloud.net/posvyat2026/quiz/"
    urls = {
        "logo": base + "11111111-1111-4111-8111-111111111111.png",
        "icon": base + "22222222-2222-4222-8222-222222222222.png",
        "card": base + "33333333-3333-4333-8333-333333333333.png",
    }
    group = app.groups[2]
    app.admin.post("/api/admin/groups", {**group, "iconUrl": urls["icon"], "mediaUrl": urls["card"]})
    app.admin.post("/api/admin/settings", {"logoUrl": urls["logo"]})
    persisted = app.state()
    stored_group = next(item for item in persisted["groups"] if item["id"] == group["id"])
    assert stored_group["iconUrl"] == urls["icon"]
    assert stored_group["mediaUrl"] == urls["card"]
    assert persisted["settings"]["logoUrl"] == urls["logo"]
    app.finish_quiz()
    app.action("open_voting")

    png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=")
    image_requests = []
    context = browser.new_context(viewport={"width": 390, "height": 844}, reduced_motion="reduce")
    try:
        def serve_s3_image(route):
            assert route.request.url in urls.values()
            image_requests.append(route.request.url)
            # Plain cross-origin <img> loads need no Access-Control-Allow-Origin.
            route.fulfill(status=200, content_type="image/png", body=png)

        context.route("https://storage.yandexcloud.net/posvyat2026/quiz/*.png", serve_s3_image)
        participant = context.new_page()
        response = participant.goto(app.url)
        csp = response.headers["content-security-policy"]
        img_policy = next(directive.strip() for directive in csp.split(";") if directive.strip().startswith("img-src "))
        assert "https://storage.yandexcloud.net" in img_policy.split()
        logo = participant.get_by_role("img", name="Логотип мероприятия", exact=True)
        expect(logo).to_have_attribute("src", urls["logo"])
        participant.wait_for_function("image => image.complete && image.naturalWidth > 0", arg=logo.element_handle())
        expect(logo).to_be_visible()

        participant.get_by_test_id("participant-code").fill(app.people[0]["code"])
        participant.get_by_test_id("participant-login").click()
        card = participant.get_by_test_id(f"vote-card-{group['id']}").locator(".voting-media img")
        expect(card).to_have_attribute("src", urls["card"])
        card.scroll_into_view_if_needed()
        participant.wait_for_function("image => image.complete && image.naturalWidth > 0", arg=card.element_handle())
        expect(card).to_be_visible()

        app.action("close_voting")
        app.action("show_audience")
        projector = context.new_page()
        projector.set_viewport_size({"width": 1920, "height": 1080})
        response = projector.goto(app.url + "/screen?env=production")
        assert response.headers["content-security-policy"] == csp
        expect(projector.get_by_test_id("screen-row")).to_have_count(3)
        icon = projector.locator(f'[data-testid="screen-row"][data-group-id="{group["id"]}"] img')
        expect(icon).to_have_attribute("src", urls["icon"])
        icon.scroll_into_view_if_needed()
        projector.wait_for_function("image => image.complete && image.naturalWidth > 0", arg=icon.element_handle())
        expect(icon).to_be_visible()
        assert set(image_requests) == set(urls.values())
    finally:
        context.close()
