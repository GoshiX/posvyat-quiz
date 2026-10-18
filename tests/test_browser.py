"""Real Chromium scenarios for participant, operator and projector surfaces."""
import base64
import re

from playwright.sync_api import expect

from conftest import Client, screenshot


def participant_login(page, app, code):
    page.goto(app.url)
    page.get_by_test_id("participant-code").fill(code)
    page.get_by_test_id("participant-login").click()
    expect(page.get_by_test_id("participant-identity").or_(page.get_by_test_id("participant-thanks"))).to_be_visible()


def admin_login(page, app):
    page.goto(app.url + "/admin")
    page.get_by_test_id("admin-username").fill("admin")
    page.get_by_test_id("admin-password").fill("quiz-admin-2026")
    page.get_by_test_id("admin-login").click()
    expect(page.get_by_test_id("admin-nav-control")).to_be_visible()


def assert_no_horizontal_overflow(page):
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), page.viewport_size


def assert_idle_identity_only(page):
    expect(page.get_by_test_id("participant-identity")).to_have_text(re.compile(r"^\s*Участник1 Фамилия1\s*Группа 1\s*$"))
    expect(page.get_by_role("button", name="Выйти", exact=True)).to_be_visible()
    expect(page.locator("main button")).to_have_count(1)
    expect(page.get_by_test_id("participant-waiting")).to_be_visible()
    expect(page.locator("main h1")).to_have_text("Ждём…")
    expect(page.locator("main h2,main h3")).to_have_count(0)


def test_mobile_login_rejects_unknown_code_and_restores_roster_identity(app, mobile):
    app.seed(groups=3)
    person = app.state()["participants"][0]
    mixed_case_code = "aBcD7xYz"
    app.admin.post("/api/admin/participants", {**person, "code": mixed_case_code})
    page = mobile.new_page()
    page.goto(app.url)
    code_field = page.get_by_test_id("participant-code")
    expect(code_field).to_have_attribute("autocapitalize", "none")
    expect(code_field).to_have_attribute("autocorrect", "off")
    expect(code_field).to_have_attribute("spellcheck", "false")
    code_field.fill(mixed_case_code.lower())
    page.get_by_test_id("participant-login").click()
    expect(page.get_by_test_id("participant-code")).to_be_visible()
    assert page.request.get(app.url + "/api/participant").json()["authenticated"] is False
    page.get_by_test_id("participant-code").fill(mixed_case_code)
    page.get_by_test_id("participant-login").click()
    expect(page.get_by_test_id("participant-identity")).to_contain_text("Участник1")
    expect(page.get_by_test_id("participant-identity")).to_contain_text("Фамилия1")
    assert_idle_identity_only(page)
    assert_no_horizontal_overflow(page)
    screenshot(page, "participant-waiting-390x844")
    page.reload()
    expect(page.get_by_test_id("participant-identity")).to_contain_text("Участник1")
    assert_idle_identity_only(page)


def test_mobile_answer_one_click_reload_new_device_closed_and_reveal(app, mobile, browser):
    app.seed(groups=3, per_group=2)
    page = mobile.new_page()
    participant_login(page, app, app.people[0]["code"])
    app.action("start_quiz")
    option = page.get_by_test_id("answer-0")
    expect(option).to_be_enabled()
    snapshot = page.request.get(app.url + "/api/participant").json()
    assert "correctIndex" not in snapshot["question"]
    option.click()
    expect(page.get_by_test_id("answer-status")).to_be_visible()
    expect(page.get_by_test_id("answer-1")).to_be_disabled()
    assert page.request.get(app.url + "/api/participant").json()["answer"] == {"optionIndex": 0, "automatic": False}
    screenshot(page, "participant-answer-accepted-390x844")
    page.reload()
    expect(page.get_by_test_id("answer-1")).to_be_disabled()
    context2 = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
    try:
        second = context2.new_page()
        participant_login(second, app, app.people[0]["code"])
        expect(second.get_by_test_id("answer-1")).to_be_disabled()
        assert second.request.get(app.url + "/api/participant").json()["answer"]["optionIndex"] == 0
        assert app.state()["stats"]["answeredCount"] == 1
        assert app.state()["stats"]["joinedCount"] == 1
        app.action("close_question")
        expect(page.get_by_test_id("answer-0")).to_be_disabled()
        snapshot = page.request.get(app.url + "/api/participant").json()
        assert "correctIndex" not in snapshot["question"]
        assert not snapshot.get("groupResult")
        app.action("reveal_question")
        expect(page.get_by_text(re.compile("Правильный|Верно|Верный ответ"), exact=False).first).to_be_visible()
        page.wait_for_function("async () => (await (await fetch('/api/participant')).json()).state.phase === 'question_reveal'")
        screenshot(page, "participant-answer-reveal-390x844")
        assert_no_horizontal_overflow(page)
        app.action("wait")
        assert_idle_identity_only(page)
        app.action("finish_quiz")
        assert_idle_identity_only(page)
    finally:
        context2.close()


def test_mobile_hearts_all_others_required_draft_survives_poll_and_single_submit(app, mobile, browser):
    app.seed(groups=5)
    app.finish_quiz()
    app.action("open_voting")
    page = mobile.new_page()
    participant_login(page, app, app.people[0]["code"])
    groups = app.groups
    expect(page.get_by_test_id(f"vote-card-{groups[0]['id']}")).to_have_count(0)
    expect(page.get_by_test_id("vote-submit")).to_be_disabled()
    for group in groups[1:]:
        expect(page.get_by_test_id(f"vote-card-{group['id']}")).to_be_visible()
    first = groups[1]["id"]
    selected = page.get_by_test_id(f"rating-{first}-7")
    selected.click()
    expect(selected).to_have_attribute("aria-pressed", "true")
    # Polling cannot discard a not-yet-submitted ballot.
    page.wait_for_timeout(1300)
    expect(selected).to_have_attribute("aria-pressed", "true")
    expect(page.get_by_test_id("vote-submit")).to_be_disabled()
    assert app.state()["stats"]["submittedCount"] == 0
    for group in groups[2:]:
        page.get_by_test_id(f"rating-{group['id']}-8").click()
    page.get_by_test_id(f"rating-{first}-9").click()
    expect(page.get_by_test_id(f"rating-{first}-9")).to_have_attribute("aria-pressed", "true")
    expect(page.get_by_test_id("vote-submit")).to_be_enabled()
    assert_no_horizontal_overflow(page)
    screenshot(page, "participant-voting-hearts-390x844")
    page.get_by_test_id("vote-submit").click()
    expect(page.get_by_role("heading", name="Спасибо за голосование")).to_be_visible()
    expect(page.get_by_test_id("vote-submit")).to_have_count(0)
    assert app.state()["stats"]["submittedCount"] == 1
    page.reload()
    expect(page.get_by_role("heading", name="Спасибо за голосование")).to_be_visible()
    new_context = browser.new_context(viewport={"width": 390, "height": 844})
    try:
        new_page = new_context.new_page()
        participant_login(new_page, app, app.people[0]["code"])
        expect(new_page.get_by_role("heading", name="Спасибо за голосование")).to_be_visible()
        expect(new_page.get_by_test_id("vote-submit")).to_have_count(0)
    finally:
        new_context.close()


def test_mobile_vote_close_does_not_claim_unsent_ratings_were_saved(app, mobile):
    app.seed(groups=3)
    app.finish_quiz()
    app.action("open_voting")
    page = mobile.new_page()
    participant_login(page, app, app.people[0]["code"])
    page.get_by_test_id(f"rating-{app.groups[1]['id']}-3").click()
    app.action("close_voting")
    expect(page.get_by_role("heading", name="Спасибо за голосование")).to_be_visible()
    expect(page.get_by_test_id("vote-submit")).to_have_count(0)
    assert app.state()["stats"]["submittedCount"] == 0
    assert "сохранен" not in page.locator("body").inner_text().lower()
    assert "сохранён" not in page.locator("body").inner_text().lower()


def test_admin_browser_creates_group_participant_question_and_preserves_dirty_form(app, browser):
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    try:
        page = context.new_page()
        admin_login(page, app)
        page.get_by_test_id("admin-nav-groups").click()
        page.get_by_test_id("group-number").fill("101")
        page.get_by_test_id("group-title").fill("Первокурсники")
        page.get_by_test_id("group-description").fill("Первая работа")
        page.get_by_test_id("group-baseScore").fill("-4")
        page.wait_for_timeout(1300)
        expect(page.get_by_test_id("group-title")).to_have_value("Первокурсники")
        page.get_by_role("button", name=re.compile("Добавить группу|Сохранить группу")).click()
        page.wait_for_function("async () => (await (await fetch('/api/admin/state')).json()).groups.length === 1")
        group_id = app.state()["groups"][0]["id"]
        page.get_by_test_id("admin-nav-participants").click()
        page.get_by_test_id("participant-firstName").fill("Мария")
        page.get_by_test_id("participant-lastName").fill("Иванова")
        page.get_by_test_id("participant-groupId").select_option(group_id)
        page.get_by_test_id("participant-email").fill("maria@yandex.ru")
        page.get_by_test_id("participant-code").fill("maria-code")
        page.get_by_role("button", name=re.compile("Добавить участника|Сохранить участника")).click()
        page.wait_for_function("async () => (await (await fetch('/api/admin/state')).json()).participants.length === 1")
        page.get_by_test_id("admin-nav-questions").click()
        page.get_by_test_id("question-text").fill("Сколько будет два плюс два?")
        for index, value in enumerate(("Четыре", "Пять", "Шесть", "Семь")):
            page.get_by_test_id(f"question-option-{index}").fill(value)
        page.get_by_test_id("question-correctIndex").select_option("0")
        page.get_by_role("button", name=re.compile("Добавить вопрос|Сохранить вопрос")).click()
        page.wait_for_function("async () => (await (await fetch('/api/admin/state')).json()).questions.length === 1")
        page.get_by_test_id("admin-nav-control").click()
        screenshot(page, "admin-control-1440x900")
        assert_no_horizontal_overflow(page)
        assert app.state()["groups"][0]["baseScore"] == -4
        assert app.state()["participants"][0]["firstName"] == "Мария"
        assert app.participant("maria-code").get("/api/participant")["participant"]["firstName"] == "Мария"
    finally:
        context.close()


def test_admin_browser_drives_all_quiz_and_reveal_actions(app, browser):
    app.seed(groups=3, questions=2)
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    try:
        page = context.new_page()
        admin_login(page, app)
        phases = [
            ("start_quiz", "question_open"), ("close_question", "question_closed"),
            ("reveal_question", "question_reveal"), ("wait", "waiting"),
            ("next_question", "question_open"), ("close_question", "question_closed"),
            ("reveal_question", "question_reveal"), ("finish_quiz", "quiz_finished"),
            ("open_voting", "voting_open"), ("close_voting", "voting_closed"),
        ]
        for action, phase in phases:
            page.get_by_test_id(f"action-{action}").click()
            page.wait_for_function("async phase => (await (await fetch('/api/admin/state')).json()).state.phase === phase", arg=phase)
        for action, stage in (("show_audience", "audience"), ("show_main", "main")):
            page.get_by_test_id(f"action-{action}").click()
            page.wait_for_function("async stage => (await (await fetch('/api/admin/state')).json()).state.displayStage === stage", arg=stage)
        first_id = app.groups[0]["id"]
        button = page.get_by_test_id(f"action-reveal_group-{first_id}")
        button.click()
        page.wait_for_function("async id => (await (await fetch('/api/display')).json()).groups.find(g => g.id === id).revealed", arg=first_id)
        expect(button).to_be_disabled()
        page.reload()
        page.get_by_test_id("admin-nav-control").click()
        expect(page.get_by_test_id(f"action-reveal_group-{first_id}")).to_be_disabled()
    finally:
        context.close()


def test_admin_browser_uploads_gif_and_saves_yandex_and_projector_settings(app, browser):
    app.seed(groups=3)
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    try:
        page = context.new_page()
        admin_login(page, app)
        page.get_by_test_id("admin-nav-settings").click()
        page.locator("#yandex-enabled").check()
        page.locator("#yandex-clientId").fill("browser-mock-client")
        page.locator("#yandex-clientSecret").fill("browser-secret-only")
        page.locator("#yandex-publicUrl").fill(app.url)
        page.get_by_role("button", name="Сохранить Яндекс ID", exact=True).click()
        page.wait_for_function("async () => (await (await fetch('/api/admin/state')).json()).yandex.secretSet")
        expect(page.locator("#yandex-clientSecret")).to_have_value("")
        expect(page.locator("#yandex-redirect")).to_have_value(app.url + "/auth/yandex/callback")
        expect(page.locator("#test-yandex")).to_have_attribute("href", "/auth/yandex/start?test=1")
        assert app.state()["yandex"]["lastTest"] is None
        page.locator("#background-mode").select_option("transparent")
        gif = base64.b64decode("R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7")
        page.locator("#settings-logoUrl-file").set_input_files({"name": "logo.gif", "mimeType": "image/gif", "buffer": gif})
        expect(page.locator("#settings-logoUrl")).to_have_value(re.compile(r"^/uploads/"))
        preview = page.locator('[data-preview="settings-logoUrl"] img')
        expect(preview).to_be_visible()
        assert preview.evaluate("image => image.complete && image.naturalWidth === 1")
        page.get_by_role("button", name="Сохранить оформление", exact=True).click()
        page.wait_for_function("async () => (await (await fetch('/api/admin/state')).json()).settings.background === 'transparent'")
        page.reload()
        page.get_by_test_id("admin-nav-settings").click()
        expect(page.locator("#background-mode")).to_have_value("transparent")
        expect(page.locator("#settings-logoUrl")).to_have_value(re.compile(r"^/uploads/"))
        expect(page.locator("#yandex-clientSecret")).to_have_value("")
        anonymous = context.new_page()
        anonymous.goto(app.url)
        expect(anonymous.get_by_role("img", name="Логотип мероприятия")).to_be_visible()
        assert anonymous.get_by_role("img", name="Логотип мероприятия").evaluate("image => image.complete && image.naturalWidth === 1")
    finally:
        context.close()


def test_projector_27_groups_no_headings_responsive_and_reloads_preserved(app, browser):
    app.seed(groups=27)
    app.finish_quiz()
    app.action("open_voting")
    app.action("close_voting")
    app.action("show_audience")
    context = browser.new_context(viewport={"width": 1920, "height": 1080}, reduced_motion="reduce")
    try:
        page = context.new_page()
        page.goto(app.url + "/screen?env=production")
        expect(page.get_by_test_id("screen-row")).to_have_count(27)
        expect(page.locator("h1,h2,h3,h4,button,nav,header")).to_have_count(0)
        assert_no_horizontal_overflow(page)
        assert page.evaluate("document.documentElement.scrollHeight <= innerHeight + 1")
        screenshot(page, "projector-27-1920x1080")
        app.action("show_main")
        app.action("reveal_group", app.groups[0]["id"])
        page.wait_for_function("async () => (await (await fetch('/api/display')).json()).stage === 'main'")
        page.reload()
        expect(page.get_by_test_id("screen-row")).to_have_count(27)
        # A missing custom icon can use a numeric avatar; no prose is allowed.
        assert all(re.fullmatch(r"[-+]?\d+(?:[.,]\d+)?", word) for word in page.locator("body").inner_text().split())
        for viewport in ({"width": 1280, "height": 720}, {"width": 800, "height": 600}):
            page.set_viewport_size(viewport)
            page.wait_for_timeout(100)
            assert_no_horizontal_overflow(page)
            assert page.evaluate("document.documentElement.scrollHeight <= innerHeight + 1")
            assert page.get_by_test_id("screen-row").evaluate_all("rows => rows.every(row => { const r=row.getBoundingClientRect(); return r.width > 0 && r.height > 0 && r.top >= 0 && r.bottom <= innerHeight + 1 && r.right <= innerWidth + 1; })")
    finally:
        context.close()
