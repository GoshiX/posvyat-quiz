"""Projector reveal sequencing, queueing, interruption and viewport checks."""
from copy import deepcopy

import pytest
from playwright.sync_api import expect

from conftest import Client, screenshot


def scored_main(app, groups=3):
    app.seed(groups=groups, questions=3)
    for index, base in enumerate((12, 15, 10)):
        app.admin.post("/api/admin/scores", {"groupId": app.groups[index]["id"], "baseScore": base})
    winners = [app.participant(app.people[index]["code"]) for index in (0, 2)]
    app.action("start_quiz")
    for index, question in enumerate(app.questions):
        if index:
            app.action("next_question")
        for person in winners:
            person.post("/api/answer", {"questionId": question["id"], "optionIndex": 0})
        app.action("close_question")
        app.action("reveal_question")
        if index + 1 < len(app.questions):
            app.action("wait")
    app.action("finish_quiz")
    app.action("open_voting")
    app.action("close_voting")
    app.action("show_audience")
    app.action("show_main")
    return [group["id"] for group in app.groups]


def row(page, group_id):
    return page.locator(f'.projector-row[data-group-id="{group_id}"]')


def refresh_display(page):
    page.evaluate("window.dispatchEvent(new Event('online'))")


def wait_reveal_stage(page, group_id, stage, timeout=20000):
    # Default assertion backoff can skip the entire 300 ms zero phase.
    page.wait_for_function("""({id,stage}) =>
      document.querySelector(`[data-group-id="${id}"]`)?.dataset.revealStage===stage
    """, arg={"id": group_id, "stage": stage}, polling="raf", timeout=timeout)


def record_reveals(page):
    page.evaluate("""() => {
      window.revealTrace=[];
      window.counterTrace=[];
      const grid=document.querySelector('#projector-grid');
      new MutationObserver(records => {
        for (const record of records) {
          if (record.type==='attributes' && record.attributeName==='data-reveal-stage' && record.target.matches('.projector-row')) {
            const row=record.target;
            window.revealTrace.push({id:row.dataset.groupId, stage:row.dataset.revealStage,time:performance.now()});
          }
        }
        const active=grid.querySelector('[data-reveal-stage="count"],[data-reveal-stage="zero"]');
        if (active) window.counterTrace.push({id:active.dataset.groupId,value:active.querySelector('.projector-score').textContent,time:performance.now()});
      }).observe(grid,{subtree:true,attributes:true,childList:true,characterData:true});
    }""")


def assert_fits(page):
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
    assert page.evaluate("document.documentElement.scrollHeight <= innerHeight + 1")
    assert page.get_by_test_id("screen-row").evaluate_all("""rows => rows.every(row => {
      const r=row.getBoundingClientRect();
      const s=row.querySelector('.projector-score').getBoundingClientRect();
      return r.width>0 && r.height>0 && r.top>=0 && r.bottom<=innerHeight+1 && r.right<=innerWidth+1 && s.left>=r.left && s.right<=r.right && s.top>=r.top && s.bottom<=r.bottom;
    })""")


def test_projector_reveals_in_order_and_keeps_future_scores_hidden(app, browser):
    first, zero, third = scored_main(app)
    context = browser.new_context(viewport={"width": 1280, "height": 720})
    try:
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(app.url + "/screen")
        expect(row(page, first).get_by_test_id("screen-score")).to_have_text("12")
        before = page.get_by_test_id("screen-row").evaluate_all("rows => rows.map(row=>row.dataset.groupId)")
        base_color = row(page, first).get_by_test_id("screen-score").evaluate("score=>getComputedStyle(score).color")
        record_reveals(page)

        app.action("reveal_group", first)
        refresh_display(page)
        expect(row(page, first)).to_have_attribute("data-reveal-stage", "highlight")
        expect(row(page, first).get_by_test_id("screen-score")).to_have_text("12")
        expect(row(page, first)).to_have_class("projector-row is-base-score is-revealing")
        screenshot(page, "projector-reveal-highlight-1280x720")
        expect(row(page, first)).to_have_attribute("data-reveal-stage", "expression")
        expect(row(page, first).get_by_test_id("screen-score")).to_have_text("12+4")
        assert page.get_by_test_id("screen-row").evaluate_all("rows=>rows.map(row=>row.dataset.groupId)") == before
        screenshot(page, "projector-reveal-expression-1280x720")

        # Both of these arrive while the first expression is still on screen.
        app.action("reveal_group", third)
        app.action("reveal_group", zero)
        refresh_display(page)
        expect(page.locator("#projector-grid")).to_have_attribute("data-reveal-queue", "2")
        expect(row(page, third).get_by_test_id("screen-score")).to_have_text("10")
        expect(row(page, zero).get_by_test_id("screen-score")).to_have_text("15")
        expect(row(page, third)).to_have_attribute("data-revealed", "false")

        wait_reveal_stage(page, first, "count")
        assert page.get_by_test_id("screen-row").evaluate_all("rows=>rows.map(row=>row.dataset.groupId)") == before
        expect(row(page, third).get_by_test_id("screen-score")).to_have_text("10")
        expect(row(page, first).get_by_test_id("screen-score")).to_have_text("13+3")
        expect(row(page, first).get_by_test_id("screen-score")).to_have_text("14+2")
        screenshot(page, "projector-reveal-transfer-14-plus-2-1280x720")
        wait_reveal_stage(page, first, "zero")
        expect(row(page, first).get_by_test_id("screen-score")).to_have_text("16+0")
        assert page.get_by_test_id("screen-row").evaluate_all("rows=>rows.map(row=>row.dataset.groupId)") == before
        screenshot(page, "projector-reveal-zero-1280x720")
        expect(row(page, first)).to_have_attribute("data-reveal-stage", "reorder")
        expect(row(page, first).get_by_test_id("screen-score")).to_have_text("16")
        assert page.get_by_test_id("screen-row").first.get_attribute("data-group-id") == first
        expect(row(page, first)).to_have_attribute("data-reveal-stage", "settle")
        screenshot(page, "projector-reveal-settle-1280x720")

        expect(row(page, third)).to_have_attribute("data-reveal-stage", "expression", timeout=8000)
        expect(row(page, third).get_by_test_id("screen-score")).to_have_text("10+4")
        expect(row(page, zero).get_by_test_id("screen-score")).to_have_text("15")
        wait_reveal_stage(page, zero, "expression")
        expect(row(page, zero).get_by_test_id("screen-score")).to_have_text("15+0")
        wait_reveal_stage(page, zero, "zero")
        expect(row(page, zero).get_by_test_id("screen-score")).to_have_text("15+0")
        expect(page.locator("#projector-grid")).to_have_attribute("data-reveal-stage", "idle", timeout=8000)
        expect(row(page, zero).get_by_test_id("screen-score")).to_have_text("15")

        trace = page.evaluate("window.revealTrace")
        assert [event["id"] for event in trace if event["stage"] == "highlight"] == [first, third, zero]
        stages = ["highlight", "expression", "count", "zero", "total", "reorder", "settle", "complete"]
        for group_id in (first, third, zero):
            assert [event["stage"] for event in trace if event["id"] == group_id] == stages
        counted = page.evaluate("window.counterTrace")
        first_count = [event["value"] for event in counted if event["id"] == first]
        assert set(first_count) >= {"12+4", "13+3", "14+2", "15+1", "16+0"}
        assert all(sum(map(int, value.split("+"))) == 16 for value in first_count)
        assert row(page, first).get_by_test_id("screen-score").evaluate("score=>getComputedStyle(score).color") != base_color
        page.wait_for_timeout(550)
        assert row(page, first).locator(".avatar").evaluate("avatar=>Number(getComputedStyle(avatar).opacity)") == pytest.approx(.78)
        assert_fits(page)
        assert errors == []
    finally:
        context.close()


def test_projector_refresh_installs_revealed_results_without_replaying(app, browser):
    first, _, _ = scored_main(app)
    app.action("reveal_group", first)
    context = browser.new_context(viewport={"width": 1920, "height": 1080})
    try:
        page = context.new_page()
        for _ in range(2):
            page.goto(app.url + "/screen")
            expect(row(page, first).get_by_test_id("screen-score")).to_have_text("16")
            expect(row(page, first)).to_have_attribute("data-reveal-stage", "idle")
            expect(page.locator(".is-revealing")).to_have_count(0)
            expect(row(page, first)).to_have_attribute("data-revealed", "true")
            assert_fits(page)
        screenshot(page, "projector-reveal-refreshed-1920x1080")
    finally:
        context.close()


@pytest.mark.parametrize("previous_stage", ["audience", "blank"])
def test_projector_coalesced_main_stage_and_first_reveal_still_animate(app, browser, previous_stage):
    first, _, _ = scored_main(app)
    state = Client(app.url).get("/api/display")
    state["stage"] = previous_stage
    for group in state["groups"]:
        group["score"] = 2.5
    if previous_stage == "blank":
        state["groups"] = []
    context = browser.new_context()
    try:
        page = context.new_page()
        page.route("**/api/display?*", lambda route: route.fulfill(json=state))
        page.goto(app.url + "/screen")
        expect(page.locator("#app")).to_have_attribute("data-display-stage", previous_stage)
        app.action("reveal_group", first)
        state.clear()
        state.update(Client(app.url).get("/api/display"))
        refresh_display(page)
        expect(row(page, first)).to_have_attribute("data-reveal-stage", "highlight")
        expect(row(page, first).get_by_test_id("screen-score")).to_have_text("12")
        expect(row(page, first)).to_have_attribute("data-reveal-stage", "expression")
        expect(row(page, first).get_by_test_id("screen-score")).to_have_text("12+4")
        wait_reveal_stage(page, first, "complete")
        expect(row(page, first).get_by_test_id("screen-score")).to_have_text("16")
    finally:
        context.close()


@pytest.mark.parametrize("during", ["expression", "count", "zero", "total", "reorder"])
def test_projector_reset_cancels_active_and_queued_reveals(app, browser, during):
    first, _, third = scored_main(app)
    context = browser.new_context()
    try:
        page = context.new_page()
        page.clock.install()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(app.url + "/screen")
        expect(page.get_by_test_id("screen-row")).to_have_count(3)
        app.action("reveal_group", first)
        app.action("reveal_group", third)
        refresh_display(page)
        # The fade stage lasts 180 ms; frame polling cannot skip it between the
        # progressively slower retries used by locator assertions.
        page.wait_for_function("""({id,during}) =>
          document.querySelector(`[data-group-id="${id}"]`)?.dataset.revealStage===during
        """, arg={"id": first, "during": during}, polling="raf")
        expect(page.locator("#projector-grid")).to_have_attribute("data-reveal-queue", "1")
        app.admin.post("/api/admin/action", {"action": "restart_event", "confirm": True, "runId": app.state()["state"]["runId"]})
        refresh_display(page)
        expect(page.get_by_test_id("screen-row")).to_have_count(0)
        expect(page.locator("#projector-grid")).to_have_attribute("data-reveal-stage", "idle")
        expect(page.locator("#projector-grid")).to_have_attribute("data-reveal-queue", "0")
        page.clock.fast_forward(30000)
        expect(page.get_by_test_id("screen-row")).to_have_count(0)
        assert errors == []
    finally:
        context.close()


@pytest.mark.parametrize("interruption", ["audience", "new_run"])
def test_projector_replaces_interrupted_sequence_immediately(app, browser, interruption):
    first, _, third = scored_main(app)
    state = Client(app.url).get("/api/display")
    initial = deepcopy(state)
    context = browser.new_context()
    try:
        page = context.new_page()
        page.clock.install()
        page.route("**/api/display?*", lambda route: route.fulfill(json=state))
        page.goto(app.url + "/screen")
        expect(page.get_by_test_id("screen-row")).to_have_count(3)
        for group in state["groups"]:
            if group["id"] in (first, third):
                group.update(score=group["score"] + 4, revealed=True)
        state["revealOrder"] = [first, third]
        refresh_display(page)
        expect(row(page, first)).to_have_attribute("data-reveal-stage", "expression")
        state.clear()
        state.update(deepcopy(initial))
        if interruption == "audience":
            state["stage"] = "audience"
            for group in state["groups"]:
                group["score"] = 2.5
        else:
            state["runId"] += 1
        refresh_display(page)
        expect(page.locator("#projector-grid")).to_have_attribute("data-reveal-stage", "idle")
        expect(page.locator("#projector-grid")).to_have_attribute("data-reveal-queue", "0")
        expect(row(page, first).get_by_test_id("screen-score")).to_have_text("2,5" if interruption == "audience" else "12")
        page.clock.fast_forward(30000)
        expect(page.locator(".is-revealing")).to_have_count(0)
        expect(row(page, first).get_by_test_id("screen-score")).to_have_text("2,5" if interruption == "audience" else "12")
    finally:
        context.close()


def test_projector_reduced_motion_keeps_click_order_without_moving_rows(app, browser):
    first, zero, third = scored_main(app)
    context = browser.new_context(reduced_motion="reduce")
    try:
        page = context.new_page()
        page.goto(app.url + "/screen")
        expect(page.get_by_test_id("screen-row")).to_have_count(3)
        record_reveals(page)
        for group_id in (third, first, zero):
            app.action("reveal_group", group_id)
        refresh_display(page)
        expect(row(page, third)).to_have_attribute("data-reveal-stage", "expression")
        expect(row(page, third).get_by_test_id("screen-score")).to_have_text("10+4")
        expect(row(page, first).get_by_test_id("screen-score")).to_have_text("12")
        wait_reveal_stage(page, zero, "complete", timeout=30000)
        trace = page.evaluate("window.revealTrace")
        assert [event["id"] for event in trace if event["stage"] == "highlight"] == [third, first, zero]
        assert page.evaluate("document.getAnimations().length") == 0
        assert row(page, first).get_by_test_id("screen-score").inner_text() == "16"
        assert_fits(page)
    finally:
        context.close()


@pytest.mark.parametrize("addition,expected_duration", [(1, 1000), (40, 6322)])
def test_projector_transfer_accelerates_after_seven_second_hold(app, browser, addition, expected_duration):
    first, _, _ = scored_main(app)
    state = Client(app.url).get("/api/display")
    context = browser.new_context()
    try:
        page = context.new_page()
        page.route("**/api/display?*", lambda route: route.fulfill(json=state))
        page.goto(app.url + "/screen")
        expect(row(page, first).get_by_test_id("screen-score")).to_have_text("12")
        record_reveals(page)
        for group in state["groups"]:
            if group["id"] == first:
                group.update(score=12 + addition, revealed=True)
        state["revealOrder"] = [first]
        refresh_display(page)
        expect(row(page, first)).to_have_attribute("data-reveal-stage", "expression")
        expect(row(page, first).get_by_test_id("screen-score")).to_have_text(f"12+{addition}")
        wait_reveal_stage(page, first, "zero")
        expect(row(page, first).get_by_test_id("screen-score")).to_have_text(f"{12 + addition}+0")
        trace = page.evaluate("window.revealTrace")
        phases = {event["stage"]: event["time"] for event in trace}
        assert 6950 <= phases["count"] - phases["expression"] <= 7300
        assert expected_duration - 50 <= phases["zero"] - phases["count"] <= expected_duration + 300
        samples = page.evaluate("window.counterTrace")
        values = {event["value"] for event in samples if event["id"] == first}
        assert all(sum(map(int, value.split("+"))) == 12 + addition for value in values)
        assert len(values) >= min(addition + 1, 20)
        # Read actual rendered counters: the transferred amount doubles each second.
        for transferred, seconds in ((1, 1), (2, 2), (4, 3), (8, 4), (16, 5), (32, 6)):
            if transferred > addition:
                break
            value = f"{12 + transferred}+{addition - transferred}"
            reached = next(event["time"] for event in samples if event["id"] == first and event["value"] == value)
            assert seconds * 1000 - 50 <= reached - phases["count"] <= seconds * 1000 + 200
        expect(row(page, first)).to_have_attribute("data-reveal-stage", "complete", timeout=5000)
        expect(row(page, first).get_by_test_id("screen-score")).to_have_text(str(12 + addition))
    finally:
        context.close()


@pytest.mark.parametrize("viewport", [{"width": 1280, "height": 720}, {"width": 1920, "height": 1080}])
def test_projector_expression_fits_27_rows(app, browser, viewport):
    first = scored_main(app, groups=27)[0]
    context = browser.new_context(viewport=viewport)
    try:
        page = context.new_page()
        page.goto(app.url + "/screen")
        expect(page.get_by_test_id("screen-row")).to_have_count(27)
        app.action("reveal_group", first)
        refresh_display(page)
        expect(row(page, first)).to_have_attribute("data-reveal-stage", "expression")
        expect(row(page, first).get_by_test_id("screen-score")).to_have_text("12+4")
        expect(page.locator("h1,h2,h3,h4,button,nav,header")).to_have_count(0)
        assert_fits(page)
        screenshot(page, f"projector-reveal-27-expression-{viewport['width']}x{viewport['height']}")
        wait_reveal_stage(page, first, "settle")
        assert_fits(page)
        screenshot(page, f"projector-reveal-27-settle-{viewport['width']}x{viewport['height']}")
    finally:
        context.close()
