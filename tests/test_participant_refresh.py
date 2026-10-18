"""Participant entry, fresh waiting feedback and rehearsal context."""
import pytest

from playwright.sync_api import expect

from conftest import screenshot


def enable_yandex(app):
    app.admin.post("/api/admin/settings", {
        "yandexEnabled": True, "yandexClientId": "local-ui-client",
        "yandexClientSecret": "local-ui-secret", "publicUrl": app.url,
    })


@pytest.mark.parametrize("width", [390, 320])
def test_login_copy_yandex_priority_and_local_official_logo(app, mobile, width):
    app.seed(groups=3)
    enable_yandex(app)
    page = mobile.new_page()
    page.set_viewport_size({"width": width, "height": 844})
    requests = []
    page.on("request", lambda request: requests.append(request.url))
    page.goto(app.url)
    expect(page.locator(".event-wordmark")).to_contain_text("ПОСВЯТ ФПМИ")
    for removed in ("ВАШ ВЕЧЕР. ВАША ГРУППА.", "Персональный код"):
        expect(page.get_by_text(removed, exact=True)).to_have_count(0)
    assert "Войдите, чтобы отвечать на вопросы" not in page.locator("main").inner_text()
    expect(page.locator(".login-note")).to_have_text(
        "Если не получается войти через Яндекс - попроси код у куратора."
    )
    yandex = page.get_by_test_id("participant-yandex")
    expect(yandex).to_have_attribute("href", "/auth/yandex/start?environment=production")
    code = page.get_by_role("textbox", name="Персональный код", exact=True)
    expect(code).to_have_attribute("autocapitalize", "none")
    expect(code).to_have_attribute("autocorrect", "off")
    expect(code).to_have_attribute("spellcheck", "false")
    assert yandex.bounding_box()["y"] < code.bounding_box()["y"]
    assert yandex.evaluate("el => getComputedStyle(el).backgroundColor") == "rgb(255, 255, 255)"
    assert page.get_by_test_id("participant-login").evaluate(
        "el => getComputedStyle(el).backgroundColor"
    ) != "rgb(255, 255, 255)"
    logo = yandex.locator("img")
    expect(logo).to_be_visible()
    assert logo.evaluate("image => image.complete && image.naturalWidth === 24")
    assert all(url.startswith(app.url + "/") for url in requests)
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    screenshot(page, f"participant-refresh-login-{width}x844")


def test_yandex_toggle_preserves_code_and_oauth_error_with_working_fallback(app, mobile):
    app.seed(groups=3)
    page = mobile.new_page()
    page.goto(app.url + "/?authError=Не%20удалось%20войти%20через%20Яндекс")
    expect(page.locator("#login-error")).to_have_text("Не удалось войти через Яндекс")
    expect(page.get_by_test_id("participant-yandex")).to_have_count(0)
    code = page.get_by_test_id("participant-code")
    code.fill(app.people[0]["code"])
    code.focus()
    enable_yandex(app)
    expect(page.get_by_test_id("participant-yandex")).to_be_visible()
    expect(code).to_have_value(app.people[0]["code"])
    expect(code).to_be_focused()
    expect(page.locator("#login-error")).to_be_visible()
    app.admin.post("/api/admin/settings", {"yandexEnabled": False})
    expect(page.get_by_test_id("participant-yandex")).to_have_count(0)
    expect(code).to_have_value(app.people[0]["code"])
    page.get_by_test_id("participant-login").click()
    expect(page.get_by_test_id("participant-identity")).to_contain_text("Участник1 Фамилия1")
    expect(page.get_by_test_id("participant-waiting")).to_be_visible()


def test_waiting_emoji_is_stable_until_reentry_and_active_phases_keep_their_surfaces(app, mobile):
    app.seed(groups=3)
    page = mobile.new_page()
    # A constant draw also proves consecutive entries cannot repeat by chance.
    page.add_init_script("Math.random = () => 0;")
    page.goto(app.url)
    page.get_by_test_id("participant-code").fill(app.people[0]["code"])
    page.get_by_test_id("participant-login").click()
    waiting = page.get_by_test_id("participant-waiting")
    expect(waiting).to_have_attribute("data-phase", "setup")
    expect(waiting.get_by_role("heading")).to_have_text("Ждём…")
    emoji = page.get_by_test_id("waiting-emoji").inner_text()
    assert emoji.strip()
    divider = page.locator(".participant-divider").bounding_box()
    assert 0.88 <= divider["width"] / page.viewport_size["width"] <= 0.92
    assert divider["x"] > 0
    assert divider["height"] == 1
    page.evaluate("""() => {
      window.waitingEmojiTrace=[document.querySelector('.waiting-emoji').textContent];
      new MutationObserver(() => {
        const emoji=document.querySelector('.waiting-emoji');
        if(emoji)window.waitingEmojiTrace.push(emoji.textContent);
      }).observe(document.querySelector('#app'),{childList:true,subtree:true,characterData:true});
    }""")
    page.wait_for_timeout(2200)
    expect(page.get_by_test_id("waiting-emoji")).to_have_text(emoji)
    # Changing availability forces a fresh participant render during waiting.
    enable_yandex(app)
    page.wait_for_function("() => window.waitingEmojiTrace.length > 1")
    rendered = page.evaluate("window.waitingEmojiTrace.length")
    logo = app.admin.post("/api/admin/upload", {
        "data": "R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7",
        "mime": "image/gif", "name": "waiting-logo.gif",
    })
    app.admin.post("/api/admin/settings", {"logoUrl": logo["url"]})
    page.wait_for_function("count => window.waitingEmojiTrace.length > count", arg=rendered)
    assert set(page.evaluate("window.waitingEmojiTrace")) == {emoji}
    expect(page.get_by_test_id("waiting-emoji")).to_have_text(emoji)
    page.reload()
    expect(page.get_by_test_id("waiting-emoji")).not_to_have_text(emoji)
    emoji = page.get_by_test_id("waiting-emoji").inner_text()
    screenshot(page, "participant-refresh-waiting-390x844")
    page.set_viewport_size({"width": 320, "height": 844})
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    screenshot(page, "participant-refresh-waiting-320x844")
    page.set_viewport_size({"width": 390, "height": 844})
    app.action("start_quiz")
    expect(page.get_by_test_id("answer-0")).to_be_enabled()
    expect(waiting).to_have_count(0)
    page.get_by_test_id("answer-0").click()
    expect(page.get_by_test_id("answer-1")).to_be_disabled()
    app.action("close_question")
    app.action("reveal_question")
    expect(page.get_by_test_id("participant-question_reveal")).to_be_visible()
    app.action("wait")
    expect(waiting).to_have_attribute("data-phase", "waiting")
    expect(page.get_by_test_id("waiting-emoji")).not_to_have_text(emoji)
    emoji = page.get_by_test_id("waiting-emoji").inner_text()
    app.action("finish_quiz")
    expect(waiting).to_have_attribute("data-phase", "quiz_finished")
    expect(page.get_by_test_id("waiting-emoji")).not_to_have_text(emoji)
    app.action("open_voting")
    expect(page.get_by_test_id("vote-submit")).to_be_visible()
    expect(waiting).to_have_count(0)
    app.action("close_voting")
    expect(page.get_by_test_id("participant-thanks")).to_be_visible()
    expect(page.get_by_role("heading", name="Спасибо за голосование")).to_be_visible()
    expect(page.get_by_test_id("participant-identity")).to_have_count(0)
    expect(page.locator(".participant-divider")).to_have_count(0)
    expect(waiting).to_have_count(0)


@pytest.mark.parametrize("person", [0, 1], ids=["same-participant", "another-participant"])
def test_waiting_emoji_changes_after_logout_and_login(app, mobile, person):
    app.seed(groups=3)
    page = mobile.new_page()
    page.add_init_script("Math.random = () => 0;")
    page.goto(app.url)
    page.get_by_test_id("participant-code").fill(app.people[0]["code"])
    page.get_by_test_id("participant-login").click()
    expect(page.get_by_test_id("participant-waiting")).to_be_visible()
    emoji = page.get_by_test_id("waiting-emoji").inner_text()
    page.get_by_role("button", name="Выйти", exact=True).click()
    expect(page.get_by_test_id("participant-code")).to_be_visible()
    expect(page.get_by_test_id("participant-waiting")).to_have_count(0)
    page.get_by_test_id("participant-code").fill(app.people[person]["code"])
    page.get_by_test_id("participant-login").click()
    expect(page.get_by_test_id("participant-identity")).to_contain_text(f"Участник{person + 1}")
    expect(page.get_by_test_id("waiting-emoji")).not_to_have_text(emoji)


@pytest.mark.parametrize("transition", ["next-question", "new-run"])
def test_waiting_emoji_changes_when_polling_misses_intermediate_phases(app, mobile, transition):
    app.seed(groups=3, questions=2 if transition == "next-question" else 1)
    page = mobile.new_page()
    page.add_init_script("Math.random = () => 0;")
    response = page.request.post(app.url + "/api/login", data={
        "code": app.people[0]["code"], "environment": "production",
    })
    assert response.ok
    if transition == "next-question":
        app.action("start_quiz")
        app.action("close_question")
        app.action("reveal_question")
        app.action("wait")
    visible = page.request.get(app.url + "/api/participant").json()
    original = visible["state"].copy()
    # Keep returning the original snapshot until all intermediate phases pass.
    page.route("**/api/participant?*", lambda route: route.fulfill(json=visible))
    page.goto(app.url)
    waiting = page.get_by_test_id("participant-waiting")
    expect(waiting).to_have_attribute("data-phase", original["phase"])
    emoji = page.get_by_test_id("waiting-emoji").inner_text()
    if transition == "next-question":
        app.action("next_question")
        app.action("close_question")
        app.action("reveal_question")
        app.action("wait")
    else:
        app.action("start_quiz")
        app.admin.post("/api/admin/action", {
            "action": "restart_event", "confirm": True, "runId": original["runId"],
        })
    visible = page.request.get(app.url + "/api/participant").json()
    assert visible["state"]["phase"] == original["phase"]
    changed = "currentQuestionId" if transition == "next-question" else "runId"
    assert visible["state"][changed] != original[changed]
    page.evaluate("window.dispatchEvent(new Event('online'))")
    expect(waiting).to_have_attribute("data-phase", original["phase"])
    expect(page.get_by_test_id("waiting-emoji")).not_to_have_text(emoji)
    expect(page.get_by_test_id("participant-identity")).to_contain_text("Участник1 Фамилия1")


def test_rehearsal_anonymous_refresh_and_both_entry_paths_preserve_environment(app, mobile):
    app.seed(groups=3)
    enable_yandex(app)
    rehearsal = app.admin.post("/api/admin/rehearsal", {"action": "copy"})
    logo = app.admin.post("/api/admin/upload", {
        "data": "R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7",
        "mime": "image/gif", "name": "rehearsal-logo.gif",
    })
    app.admin.post("/api/admin/settings?env=rehearsal", {"logoUrl": logo["url"]})
    page = mobile.new_page()
    participant_requests = []
    page.on("request", lambda request: participant_requests.append(request.url)
            if "/api/participant" in request.url else None)
    page.goto(app.url + "/?env=rehearsal")
    expect(page.get_by_role("img", name="Логотип мероприятия")).to_be_visible()
    expect(page.get_by_test_id("participant-yandex")).to_have_attribute(
        "href", "/auth/yandex/start?environment=rehearsal"
    )
    page.get_by_test_id("participant-code").fill(rehearsal["codes"][0]["code"])
    page.get_by_test_id("participant-login").click()
    expect(page.get_by_test_id("participant-waiting")).to_be_visible()
    assert participant_requests
    assert all(url.endswith("/api/participant?env=rehearsal") for url in participant_requests)
    assert app.state()["stats"]["joinedCount"] == 0
    assert app.state("rehearsal")["stats"]["joinedCount"] == 1
    app.action("start_quiz", environment="rehearsal")
    expect(page.get_by_test_id("answer-0")).to_be_visible()
    page.get_by_role("button", name="Выйти", exact=True).click()
    expect(page.get_by_role("img", name="Логотип мероприятия")).to_be_visible()
    expect(page.get_by_test_id("participant-code")).to_be_visible()
