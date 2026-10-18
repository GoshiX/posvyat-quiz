"""Public control-console publication boundary and projector reveal metadata."""
import urllib.request

from conftest import Client


def test_public_quiz_results_publish_only_after_reveal_and_reset_next_question(app):
    app.seed(groups=4, per_group=2, questions=2)
    names = ["Б05-627", "Б05-631", "Гошана", "Кости"]
    for group, name in zip(app.groups, names):
        app.admin.post("/api/admin/groups", {**group, "number": name})
    # The question number follows the configured order, not its original creation order.
    app.admin.post("/api/admin/questions/order", {"ids": [q["id"] for q in reversed(app.questions)]})
    public = Client(app.url)
    path = "/api/quiz/results"
    assert public.get(path) == {"question": 0, "ready": False, "groups": {}}
    app.action("start_quiz")
    question_id = app.state()["state"]["currentQuestionId"]
    assert question_id == app.questions[1]["id"]
    assert public.get(path) == {"question": 1, "ready": False, "groups": {}}
    # A tie is red, a strict correct majority green, abstainers red, wrong majority red.
    for index, choice in [(0, 0), (1, 1), (2, 0), (3, 0), (6, 1), (7, 1)]:
        app.participant(app.people[index]["code"]).post("/api/answer", {
            "questionId": question_id, "optionIndex": choice,
        })
    app.action("close_question")
    assert public.get(path) == {"question": 1, "ready": False, "groups": {}}
    app.action("reveal_question")
    published = {"question": 1, "ready": True, "groups": dict(zip(names, ["r", "g", "r", "r"]))}
    assert public.get(path) == published
    with urllib.request.urlopen(app.url + path) as response:
        assert response.headers["Cache-Control"] == "no-store"
        assert response.headers["Content-Type"].startswith("application/json")
    app.restart()
    assert public.get(path) == published
    app.action("wait")
    assert public.get(path) == published
    app.action("next_question")
    assert public.get(path) == {"question": 2, "ready": False, "groups": {}}
    app.action("close_question")
    assert public.get(path)["ready"] is False
    app.action("reveal_question")
    last = {"question": 2, "ready": True, "groups": dict.fromkeys(names, "r")}
    assert public.get(path) == last
    for action in ["finish_quiz", "open_voting", "close_voting", "show_audience", "show_main"]:
        app.action(action)
        assert public.get(path) == last
    app.admin.post("/api/admin/action", {"action": "restart_event", "runId": 0, "confirm": True})
    assert public.get(path) == {"question": 0, "ready": False, "groups": {}}
    public.get(path + "?env=invalid", expected=400)
    public.post(path, {}, expected=405)


def test_public_quiz_results_keep_rehearsal_and_production_separate(app):
    app.seed(groups=2, questions=1)
    app.admin.post("/api/admin/rehearsal", {"action": "seed", "groupCount": 2, "participantsPerGroup": 1})
    # Even a signed-in participant cookie does not select the console's environment.
    participant = app.participant(app.people[0]["code"])
    for action in ["start_quiz", "close_question", "reveal_question"]:
        app.action(action, environment="rehearsal")
    assert participant.get("/api/quiz/results") == {"question": 0, "ready": False, "groups": {}}
    assert participant.get("/api/quiz/results?env=rehearsal") == {
        "question": 1, "ready": True, "groups": {"601": "r", "602": "r"},
    }
    app.action("start_quiz")
    assert participant.get("/api/quiz/results") == {"question": 1, "ready": False, "groups": {}}
    assert participant.get("/api/quiz/results?env=rehearsal")["ready"] is True


def test_projector_metadata_keeps_click_order_zero_reveals_and_run_id(app):
    app.seed(groups=2, questions=1)
    public = Client(app.url)
    before = public.get("/api/display")
    assert before["runId"] == 0 and before["revealOrder"] == [] and before["groups"] == []
    app.finish_quiz()
    for action in ["open_voting", "close_voting", "show_audience"]:
        app.action(action)
    assert all("baseScore" not in row for row in public.get("/api/display")["groups"])
    app.action("show_main")
    for row in public.get("/api/display")["groups"]:
        assert row["baseScore"] == row["score"] and row["revealed"] is False
    second, first = app.groups[1]["id"], app.groups[0]["id"]
    app.action("reveal_group", second)
    app.action("reveal_group", first)
    app.action("reveal_group", second)
    snapshot = public.get("/api/display")
    assert snapshot["revealOrder"] == [second, first]
    assert all(row["revealed"] and row["score"] == row["baseScore"] for row in snapshot["groups"])
    app.restart()
    assert public.get("/api/display") == snapshot
    app.admin.post("/api/admin/action", {"action": "restart_event", "runId": 0, "confirm": True})
    after = public.get("/api/display")
    assert after["runId"] == 1 and after["revealOrder"] == [] and after["groups"] == []
