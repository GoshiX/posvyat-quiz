"""Real browsers keep their login, but never reuse answers or drafts across runs."""
from playwright.sync_api import expect

from test_browser import admin_login, assert_idle_identity_only, participant_login
from test_restart import action, finish_quiz


def handle_dialog(dialogs, accept):
    def respond(dialog):
        dialogs.append((dialog.type, dialog.message))
        if accept:
            dialog.accept()
        else:
            dialog.dismiss()
    return respond


def is_restart_response(response):
    return "/api/admin/action?env=" in response.url and response.request.post_data_json.get("action") == "restart_event"


def test_admin_restart_cancel_accept_and_existing_phone_can_answer_again(app, browser, mobile):
    app.seed(groups=3, questions=2)
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    try:
        admin = context.new_page()
        admin_login(admin, app)
        expect(admin.get_by_test_id("action-restart_event")).to_have_count(0)
        phone = mobile.new_page()
        participant_login(phone, app, app.people[0]["code"])
        identity = phone.request.get(app.url + "/api/participant").json()["participant"]
        admin.get_by_test_id("action-start_quiz").click()
        expect(phone.get_by_test_id("answer-0")).to_be_enabled()
        phone.get_by_test_id("answer-0").click()
        expect(phone.get_by_test_id("answer-status")).to_contain_text("Ответ принят")
        expect(phone.get_by_test_id("answer-1")).to_be_disabled()
        before = app.state()
        restart_requests = []
        admin.on("request", lambda request: restart_requests.append(request.post_data_json)
                 if "/api/admin/action?env=" in request.url and request.method == "POST" else None)
        dialogs = []
        admin.once("dialog", handle_dialog(dialogs, accept=False))
        admin.get_by_test_id("action-restart_event").click()
        assert dialogs[0][0] == "confirm"
        assert "Рабочий режим" in dialogs[0][1]
        assert app.state() == before
        assert restart_requests == []
        expect(phone.get_by_test_id("answer-0")).to_be_disabled()

        admin.once("dialog", handle_dialog(dialogs, accept=True))
        with admin.expect_response(is_restart_response) as response:
            admin.get_by_test_id("action-restart_event").click()
        assert response.value.status == 200
        assert response.value.request.post_data_json == {"action": "restart_event", "confirm": True, "runId": 0}
        assert dialogs[1][0] == "confirm"
        assert "Рабочий режим" in dialogs[1][1]
        expect(admin.get_by_test_id("action-start_quiz")).to_be_enabled()
        expect(admin.get_by_test_id("action-restart_event")).to_have_count(0)
        assert_idle_identity_only(phone)
        assert phone.request.get(app.url + "/api/participant").json()["participant"] == identity
        assert app.state()["state"]["runId"] == 1
        assert app.state()["stats"]["answeredCount"] == 0

        admin.get_by_test_id("action-start_quiz").click()
        expect(phone.get_by_test_id("answer-0")).to_be_enabled()
        expect(phone.get_by_test_id("answer-1")).to_be_enabled()
        with phone.expect_response(lambda response: response.url.endswith("/api/answer")) as answer:
            phone.get_by_test_id("answer-1").click()
        assert answer.value.status == 200
        assert answer.value.request.post_data_json["runId"] == 1
        expect(phone.get_by_test_id("answer-status")).to_contain_text("Ответ принят")
        assert phone.request.get(app.url + "/api/participant").json()["answer"] == {"optionIndex": 1, "automatic": False}
        assert app.state()["stats"]["answeredCount"] == 1
        assert app.state()["stats"]["joinedCount"] == 1
    finally:
        context.close()


def test_new_run_discards_unsent_voting_draft_and_rehearsal_confirmation_is_specific(app, browser, mobile):
    app.seed(groups=3)
    copied = app.admin.post("/api/admin/rehearsal", {"action": "copy"})
    rehearsal_run = app.state("rehearsal")["state"]["runId"]
    production_before = app.state()
    code = copied["codes"][0]["code"]
    phone = mobile.new_page()
    phone.goto(app.url + "/?env=rehearsal")
    phone.get_by_test_id("participant-code").fill(code)
    phone.get_by_test_id("participant-login").click()
    expect(phone.get_by_test_id("participant-identity")).to_be_visible()
    finish_quiz(app, rehearsal_run, "rehearsal")
    action(app, "open_voting", "rehearsal", runId=rehearsal_run)
    person = phone.request.get(app.url + "/api/participant").json()["participant"]
    targets = [group for group in app.state("rehearsal")["groups"] if group["id"] != person["groupId"]]
    for group in targets:
        selected = phone.get_by_test_id(f"rating-{group['id']}-9")
        selected.click()
        expect(selected).to_have_attribute("aria-pressed", "true")
    expect(phone.get_by_test_id("vote-submit")).to_be_enabled()
    # The old draft really is durable in this browser before the restart.
    phone.reload()
    for group in targets:
        expect(phone.get_by_test_id(f"rating-{group['id']}-9")).to_have_attribute("aria-pressed", "true")
    assert app.state("rehearsal")["stats"]["submittedCount"] == 0

    context = browser.new_context(viewport={"width": 1440, "height": 900})
    try:
        admin = context.new_page()
        admin_login(admin, app)
        admin.get_by_label("Режим мероприятия").select_option("rehearsal")
        expect(admin.get_by_test_id("action-restart_event")).to_be_enabled()
        dialogs = []
        admin.once("dialog", handle_dialog(dialogs, accept=True))
        with admin.expect_response(is_restart_response) as response:
            admin.get_by_test_id("action-restart_event").click()
        assert response.value.status == 200
        assert response.value.request.post_data_json["runId"] == rehearsal_run
        assert "env=rehearsal" in response.value.url
        assert dialogs[0][0] == "confirm"
        assert "Репетиция" in dialogs[0][1]
        assert app.state() == production_before
        assert_idle_identity_only(phone)

        finish_quiz(app, rehearsal_run + 1, "rehearsal")
        action(app, "open_voting", "rehearsal", runId=rehearsal_run + 1)
        expect(phone.get_by_test_id("vote-submit")).to_be_disabled()
        expect(phone.locator('[data-rate][aria-pressed="true"]')).to_have_count(0)
        phone.reload()
        expect(phone.get_by_test_id("vote-submit")).to_be_disabled()
        expect(phone.locator('[data-rate][aria-pressed="true"]')).to_have_count(0)
        for group in targets:
            phone.get_by_test_id(f"rating-{group['id']}-3").click()
        with phone.expect_response(lambda response: response.url.endswith("/api/vote")) as vote:
            phone.get_by_test_id("vote-submit").click()
        assert vote.value.status == 200
        assert vote.value.request.post_data_json == {
            "ratings": {group["id"]: 3 for group in targets}, "runId": rehearsal_run + 1,
        }
        expect(phone.get_by_test_id("participant-thanks")).to_be_visible()
        assert app.state("rehearsal")["stats"]["submittedCount"] == 1
        assert app.state() == production_before
    finally:
        context.close()
