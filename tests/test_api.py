"""Black-box contract and persisted scoring tests against a real HTTP server."""
import base64

from conftest import Client


def score(app, group_id):
    return next(row for row in app.state()["scores"] if row["groupId"] == group_id)


def test_auth_allowlist_and_unique_people(app):
    app.seed(groups=2, per_group=1)
    anonymous = Client(app.url)
    assert anonymous.get("/api/participant")["authenticated"] is False
    anonymous.get("/api/admin/state", expected=401)
    anonymous.post("/api/admin/login", {"username": "admin", "password": "incorrect"}, expected=401)
    anonymous.post("/api/login", {"code": "not-in-allowlist"}, expected=401)
    person = app.participant(app.people[0]["code"])
    identity = person.get("/api/participant")["participant"]
    assert identity["firstName"] == "Участник1"
    assert identity["groupId"] == app.groups[0]["id"]
    second_device = app.participant(app.people[0]["code"])
    assert second_device.get("/api/participant")["participant"]["id"] == identity["id"]
    assert app.state()["stats"]["joinedCount"] == 1
    person.post("/api/logout", {})
    assert person.get("/api/participant")["authenticated"] is False


def test_answer_state_machine_and_offline_random_wrong_persistence(app):
    app.seed(groups=2, per_group=3, questions=2)
    active = app.participant(app.people[0]["code"])
    second_device = app.participant(app.people[0]["code"])
    offline = app.participant(app.people[1]["code"])
    offline.post("/api/logout", {})
    app.action("reveal_question", expected=409)
    app.action("start_quiz")
    question = active.get("/api/participant")["question"]
    assert "correctIndex" not in question
    assert "groupResult" not in active.get("/api/participant") or active.get("/api/participant")["groupResult"] is None
    active.post("/api/answer", {"questionId": question["id"], "optionIndex": 0})
    second_device.post("/api/answer", {"questionId": question["id"], "optionIndex": 1}, expected=409)
    assert second_device.get("/api/participant")["answer"] == {"optionIndex": 0, "automatic": False}
    app.action("close_question")
    closed = active.get("/api/participant")
    assert "correctIndex" not in closed["question"]
    assert not closed.get("groupResult")
    app.action("start_quiz", expected=409)
    active.post("/api/answer", {"questionId": question["id"], "optionIndex": 1}, expected=409)
    assert app.state()["stats"]["answeredCount"] == 1
    late = app.participant(app.people[1]["code"])
    # The server may hide an assigned wrong choice until reveal to avoid a clue.
    assert late.get("/api/participant")["answer"] is None
    app.action("reveal_question")
    revealed = late.get("/api/participant")
    automatic = revealed["answer"]
    assert automatic["automatic"] is True
    assert automatic["optionIndex"] in (1, 2, 3)
    assert revealed["question"]["correctIndex"] == 0
    assert sum(revealed["groupResult"]["counts"]) == 2
    assert revealed["groupResult"]["won"] is False  # One correct vote and one automatic wrong vote tie.
    assert active.get("/api/participant")["answer"] == {"optionIndex": 0, "automatic": False}
    for _ in range(3):
        again = app.participant(app.people[1]["code"]).get("/api/participant")
        assert again["answer"] == automatic
        assert again["groupResult"] == revealed["groupResult"]
    app.restart()
    after_restart = app.participant(app.people[1]["code"]).get("/api/participant")
    assert after_restart["answer"] == automatic
    assert after_restart["groupResult"] == revealed["groupResult"]
    app.action("reveal_question", expected=409)
    app.action("wait")
    app.action("next_question")
    assert active.get("/api/participant")["question"]["id"] != question["id"]
    assert active.get("/api/participant")["answer"] is None


def test_strict_plurality_tie_and_floor_of_whole_quiz(app):
    app.seed(groups=3, per_group=3, questions=4)
    clients = [app.participant(person["code"]) for person in app.people]
    app.action("start_quiz")
    for index, question in enumerate(app.questions):
        if index:
            app.action("next_question")
        # First group wins exactly 3/4. Second wins 4/4. Third always ties 1:1:1.
        choices = ([0, 0, 1] if index < 3 else [1, 1, 0]) + [0, 0, 1] + [0, 1, 2]
        for client, choice in zip(clients, choices):
            client.post("/api/answer", {"questionId": question["id"], "optionIndex": choice})
        app.action("close_question")
        app.action("reveal_question")
        tie = clients[6].get("/api/participant")["groupResult"]
        assert tie["won"] is False
        assert tie["choice"] in (1, 2, 3)
        assert clients[6].get("/api/participant")["groupResult"] == tie
        if index < 3:
            app.action("wait")
    app.action("finish_quiz")
    assert score(app, app.groups[0]["id"])["quizScore"] == 4
    assert score(app, app.groups[1]["id"])["quizScore"] == 6
    assert score(app, app.groups[2]["id"])["quizScore"] == 0


def test_never_joined_users_add_no_votes_and_first_login_does_not_backfill(app):
    app.seed(groups=2, per_group=10, questions=2)
    app.action("start_quiz")
    app.action("close_question")
    assert app.state()["stats"]["joinedCount"] == 0
    assert app.state()["stats"]["answeredCount"] == 0
    # A first login between close and reveal must not change the closed result.
    late = app.participant(app.people[0]["code"])
    assert late.get("/api/participant")["answer"] is None
    app.action("reveal_question")
    for group_index in range(2):
        person = app.people[group_index * 10]
        result = app.participant(person["code"]).get("/api/participant")
        assert result["answer"] is None
        assert result["groupResult"]["counts"] == [0, 0, 0, 0]
        assert result["groupResult"]["won"] is False
    assert Client(app.url).get("/api/quiz/results")["groups"] == {g["number"]: "r" for g in app.groups}
    # These two participants have now joined and qualify on the next question.
    app.action("wait")
    app.action("next_question")
    app.action("close_question")
    app.action("reveal_question")
    for group_index in range(2):
        result = app.participant(app.people[group_index * 10]["code"]).get("/api/participant")
        assert result["answer"]["automatic"] is True
        assert result["answer"]["optionIndex"] != 0
        assert sum(result["groupResult"]["counts"]) == 1
        assert result["groupResult"]["won"] is False


def test_never_joined_roster_does_not_outvote_a_correct_participant(app):
    app.seed(groups=2, per_group=10)
    active = app.participant(app.people[0]["code"])
    app.action("start_quiz")
    active.post("/api/answer", {"questionId": app.questions[0]["id"], "optionIndex": 0})
    app.action("close_question")
    app.action("reveal_question")
    result = active.get("/api/participant")
    assert result["answer"] == {"optionIndex": 0, "automatic": False}
    assert result["groupResult"]["counts"] == [1, 0, 0, 0]
    assert result["groupResult"]["won"] is True
    assert score(app, app.groups[0]["id"])["quizScore"] == 1
    assert score(app, app.groups[1]["id"])["quizScore"] == 0
    assert Client(app.url).get("/api/quiz/results")["groups"] == {
        app.groups[0]["number"]: "g", app.groups[1]["number"]: "r",
    }


def test_audience_required_complete_immutable_group_means_zero_and_trim_sum(app):
    app.seed(groups=5, per_group=2, trim=1)
    app.finish_quiz()
    app.action("open_voting")
    group_ids = [group["id"] for group in app.groups]
    p0 = app.participant(app.people[0]["code"])
    targets = p0.get("/api/participant")["voting"]["groups"]
    assert {g["id"] for g in targets} == set(group_ids[1:])
    base = {group_id: 2 for group_id in group_ids[1:]}
    p0.post("/api/vote", {"ratings": {}}, expected=400)
    p0.post("/api/vote", {"ratings": {**base, group_ids[0]: 10}}, expected=400)
    p0.post("/api/vote", {"ratings": {**base, "unknown": 5}}, expected=400)
    for invalid in (0, 11, 2.5, "7", True):
        p0.post("/api/vote", {"ratings": {**base, group_ids[4]: invalid}}, expected=400)
    # A group with two voters has the same final weight as a group with one.
    for person_index, target_rating in ((0, 6), (1, 7), (2, 8), (4, 7)):
        person = app.people[person_index]
        ballot = {gid: 2 for gid in group_ids if gid != person["groupId"]}
        ballot[group_ids[4]] = target_rating
        app.participant(person["code"]).post("/api/vote", {"ratings": ballot})
    p0.post("/api/vote", {"ratings": base}, expected=409)
    app.participant(app.people[0]["code"]).post("/api/vote", {"ratings": base}, expected=409)
    assert score(app, group_ids[4])["audienceScore"] == 13.5  # trim [0, 6.5, 7, 8] -> 13.5
    assert app.state()["stats"]["submittedCount"] == 4
    public = p0.get("/api/participant")
    assert "scores" not in public
    app.action("close_voting")
    app.participant(app.people[6]["code"]).post("/api/vote", {"ratings": {}}, expected=409)
    app.action("show_audience")
    display = Client(app.url).get("/api/display")
    assert display["stage"] == "audience"
    assert next(g for g in display["groups"] if g["id"] == group_ids[4])["score"] == 13.5
    assert display["groups"] == Client(app.url).get("/api/display")["groups"]


def test_reveal_idempotence_and_base_freezes_only_on_main(app):
    app.seed(groups=3)
    participant = app.participant(app.people[0]["code"])
    app.action("start_quiz")
    participant.post("/api/answer", {"questionId": app.questions[0]["id"], "optionIndex": 0})
    app.action("close_question")
    app.action("reveal_question")
    app.action("finish_quiz")
    app.action("open_voting")
    app.action("close_voting")
    app.action("show_audience")
    group_id = app.groups[0]["id"]
    app.admin.post("/api/admin/scores", {"groupId": group_id, "baseScore": -3})
    assert app.state()["state"]["baseLocked"] is False
    app.action("show_main")
    assert app.state()["state"]["baseLocked"] is True
    app.admin.post("/api/admin/scores", {"groupId": group_id, "baseScore": 999}, expected=409)
    display_client = Client(app.url)
    before = next(g for g in display_client.get("/api/display")["groups"] if g["id"] == group_id)
    assert before["score"] == -3
    app.action("reveal_group", group_id)
    for _ in range(3):
        # Idempotence may return 200 with unchanged state or reject duplicate 409.
        try:
            app.action("reveal_group", group_id)
        except AssertionError as error:
            assert ", 409," in str(error), error
    after = next(g for g in display_client.get("/api/display")["groups"] if g["id"] == group_id)
    assert after["score"] == -2
    assert after["revealed"] is True
    app.restart()
    assert next(g for g in display_client.get("/api/display")["groups"] if g["id"] == group_id) == after
    assert app.state()["state"]["baseLocked"] is True
    zero_group = app.groups[1]["id"]
    app.action("reveal_group", zero_group)
    assert next(g for g in display_client.get("/api/display")["groups"] if g["id"] == zero_group)["revealed"] is True


def test_frozen_roster_rules_and_question_order_validation(app):
    app.seed(groups=3, questions=2)
    ids = [q["id"] for q in app.questions]
    app.admin.post("/api/admin/questions/order", {"ids": ids[:1]}, expected=400)
    app.admin.post("/api/admin/questions/order", {"ids": ids[::-1]})
    assert [q["id"] for q in app.state()["questions"]] == ids[::-1]
    app.action("start_quiz")
    app.admin.post("/api/admin/groups", {**app.groups[0], "title": "changed"}, expected=409)
    app.admin.post("/api/admin/questions", {**app.questions[0], "correctIndex": 1}, expected=409)
    app.admin.post("/api/admin/questions/order", {"ids": ids}, expected=409)
    app.admin.post("/api/admin/settings", {"trimCount": 1}, expected=409)
    app.admin.post("/api/admin/settings", {"background": "#112233"})
    assert app.state()["settings"]["background"] == "#112233"


def test_invalid_trim_prevents_start(app):
    app.seed(groups=3)
    # Rejecting the unsafe configuration at save time is also valid.
    try:
        app.admin.post("/api/admin/settings", {"trimCount": 1})
    except AssertionError as error:
        assert ", 400," in str(error), error
    else:
        app.action("start_quiz", expected=400)


def test_production_rehearsal_state_and_data_separation(app):
    app.seed(groups=3)
    production_before = app.state()
    app.admin.post("/api/admin/rehearsal", {"action": "copy"})
    rehearsal = app.state("rehearsal")
    assert len(rehearsal["groups"]) == 3
    assert rehearsal["state"]["phase"] == "setup"
    app.action("start_quiz", environment="rehearsal")
    app.admin.post("/api/admin/rehearsal", {"action": "simulate"})
    app.action("close_question", environment="rehearsal")
    after = app.state()
    for key in ("groups", "participants", "questions", "scores", "state"):
        assert after[key] == production_before[key]
    production_person = app.participant(app.people[0]["code"]).get("/api/participant")
    assert production_person["environment"] == "production"
    assert production_person["state"]["phase"] == "setup"
    app.admin.post("/api/admin/rehearsal", {"action": "seed", "groupCount": 27, "participantsPerGroup": 2})
    assert len(app.state("rehearsal")["groups"]) == 27
    assert app.state()["groups"] == production_before["groups"]


def test_settings_secret_redacted_gif_upload_and_code_still_works(app):
    app.seed(groups=3)
    app.admin.post("/api/admin/settings", {
        "yandexEnabled": True, "yandexClientId": "test-client", "yandexClientSecret": "never-return-this-secret",
        "publicUrl": "https://quiz.example.org/", "background": "transparent",
    })
    first = app.state()
    assert first["yandex"]["secretSet"] is True
    assert first["yandex"]["redirectUri"] == "https://quiz.example.org/auth/yandex/callback"
    assert "never-return-this-secret" not in str(first)
    app.admin.post("/api/admin/settings", {"yandexClientSecret": "", "yandexEnabled": False, "publicUrl": app.url})
    assert app.state()["yandex"]["secretSet"] is True
    assert app.participant(app.people[0]["code"]).get("/api/participant")["authenticated"] is True
    gif = "R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"
    upload = app.admin.post("/api/admin/upload", {"data": gif, "mime": "image/gif", "name": "card.gif"})
    assert upload["url"].startswith("/uploads/")
    import urllib.request
    response = urllib.request.urlopen(app.url + upload["url"])
    assert response.headers.get_content_type() == "image/gif"
    assert response.read() == base64.b64decode(gif)
    app.admin.post("/api/admin/upload", {"data": "PHN2Zz4=", "mime": "image/svg+xml", "name": "bad.svg"}, expected=400)
