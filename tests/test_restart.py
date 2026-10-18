"""Restart an event without losing its roster, configuration or active sessions."""

from conftest import Client


def action(app, name, environment="production", expected=200, **payload):
    return app.admin.post(
        f"/api/admin/action?env={environment}",
        {"action": name, **payload},
        expected=expected,
    )


def restart_event(app, run_id, environment="production", expected=200):
    return action(app, "restart_event", environment, expected, confirm=True, runId=run_id)


def finish_quiz(app, run_id, environment="production", participant=None):
    questions = app.state(environment)["questions"]
    action(app, "start_quiz", environment, runId=run_id)
    for index, question in enumerate(questions):
        if index:
            action(app, "next_question", environment, runId=run_id)
        if participant:
            participant.post("/api/answer", {
                "questionId": question["id"], "optionIndex": question["correctIndex"], "runId": run_id,
            })
        action(app, "close_question", environment, runId=run_id)
        action(app, "reveal_question", environment, runId=run_id)
        if index + 1 < len(questions):
            action(app, "wait", environment, runId=run_id)
    action(app, "finish_quiz", environment, runId=run_id)


def ballot(app, participant_group, value=7, environment="production"):
    return {group["id"]: value for group in app.state(environment)["groups"] if group["id"] != participant_group}


def test_restart_requires_confirmation_current_run_and_an_event_in_progress(app):
    app.seed(groups=2)
    before = app.state()
    assert before["state"]["runId"] == 0
    assert type(before["state"]["runId"]) is int
    assert Client(app.url).get("/api/participant")["state"]["runId"] == 0
    for payload in ({}, {"confirm": False}):
        action(app, "restart_event", expected=400, runId=0, **payload)
    action(app, "restart_event", expected=409, confirm=True)
    restart_event(app, 1, expected=409)
    restart_event(app, 0, expected=409)
    assert app.state() == before


def test_restart_clears_completed_event_preserves_roster_settings_codes_and_sessions(app):
    app.seed(groups=5, per_group=1, questions=2, trim=1)
    gif = "R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"
    image = app.admin.post("/api/admin/upload", {"data": gif, "mime": "image/gif", "name": "logo.gif"})
    app.admin.post("/api/admin/settings", {
        "background": "#17324f", "logoUrl": image["url"], "yandexEnabled": True,
        "yandexClientId": "preserved-client", "yandexClientSecret": "preserved-secret",
        "publicUrl": app.url,
    })
    app.admin.post("/api/admin/questions/order", {"ids": [question["id"] for question in app.questions][::-1]})
    first = app.participant(app.people[0]["code"])
    second_device = app.participant(app.people[0]["code"])
    other_voters = [app.participant(person["code"]) for person in app.people[1:3]]
    identity = first.get("/api/participant")["participant"]
    finish_quiz(app, 0, participant=first)
    action(app, "open_voting", runId=0)
    first.post("/api/vote", {"ratings": ballot(app, identity["groupId"]), "runId": 0})
    for client in other_voters:
        group_id = client.get("/api/participant")["participant"]["groupId"]
        client.post("/api/vote", {"ratings": ballot(app, group_id), "runId": 0})
    action(app, "close_voting", runId=0)
    action(app, "show_audience", runId=0)
    app.admin.post("/api/admin/scores", {"groupId": identity["groupId"], "baseScore": -13})
    action(app, "show_main", runId=0)
    action(app, "reveal_group", groupId=identity["groupId"], runId=0)
    before = app.state()
    assert before["state"]["baseLocked"] is True
    assert before["stats"]["submittedCount"] == 3
    assert any(row["quizScore"] > 0 for row in before["scores"])
    assert any(row["audienceScore"] > 0 for row in before["scores"])
    assert any(row["revealed"] for row in before["scores"])

    restart_event(app, 0)
    after = app.state()
    assert after["state"]["runId"] == 1
    assert after["state"]["phase"] == "setup"
    assert after["state"]["displayStage"] == "blank"
    assert after["state"]["started"] is False
    assert after["state"]["currentQuestionId"] is None
    assert after["state"]["baseLocked"] is False
    for key in ("groups", "questions", "participants", "settings", "yandex"):
        assert after[key] == before[key], key
    assert after["stats"]["joinedCount"] == 3
    assert after["stats"]["answeredCount"] == after["stats"]["submittedCount"] == 0
    for row in after["scores"]:
        assert row["quizRaw"] == row["quizScore"] == row["audienceScore"] == row["voterCount"] == 0
        assert row["revealed"] is False
        assert row["totalScore"] == row["baseScore"]
    assert Client(app.url).get("/api/display")["groups"] == []
    for client in (first, second_device):
        participant = client.get("/api/participant")
        assert participant["authenticated"] is True
        assert participant["participant"] == identity
        assert participant["state"]["phase"] == "setup"
        assert participant["state"]["runId"] == 1
        assert not participant.get("question")
        assert not participant.get("answer")
        assert not participant.get("groupResult")
        assert not participant.get("voting")
    # Both the cleared state and existing participant cookies survive a process restart.
    app.restart()
    persisted = app.state()
    for key in ("state", "groups", "questions", "participants", "settings", "scores", "yandex"):
        assert persisted[key] == after[key], key
    assert first.get("/api/participant")["participant"] == identity
    assert first.get("/api/participant")["state"]["runId"] == 1
    for person in app.people:
        restored = app.participant(person["code"]).get("/api/participant")
        original = next(row for row in before["participants"] if row["groupId"] == person["groupId"])
        assert restored["participant"]["id"] == original["id"]
        assert restored["participant"]["groupId"] == original["groupId"]

    # Unlock configuration and base scores, then reuse the exact same question and person.
    app.admin.post("/api/admin/groups", {**after["groups"][0], "title": "Группа после перезапуска"})
    app.admin.post("/api/admin/questions", {**after["questions"][0], "text": "Вопрос после перезапуска"})
    app.admin.post("/api/admin/questions/order", {"ids": [question["id"] for question in after["questions"]]})
    app.admin.post("/api/admin/settings", {"trimCount": 0})
    app.admin.post("/api/admin/scores", {"groupId": identity["groupId"], "baseScore": -12})
    finish_quiz(app, 1, participant=first)
    action(app, "open_voting", runId=1)
    assert first.get("/api/participant")["voting"]["submitted"] is False
    first.post("/api/vote", {"ratings": ballot(app, identity["groupId"], 3), "runId": 1})
    assert second_device.get("/api/participant")["voting"]["submitted"] is True
    assert app.state()["stats"]["submittedCount"] == 1


def test_old_run_answers_votes_and_admin_actions_cannot_change_the_new_run(app):
    app.seed(groups=3)
    person = app.participant(app.people[0]["code"])
    question = app.questions[0]
    finish_quiz(app, 0, participant=person)
    action(app, "open_voting", runId=0)
    ratings = ballot(app, app.people[0]["groupId"])
    person.post("/api/vote", {"ratings": ratings})  # Existing first-run clients remain compatible.
    restart_event(app, 0)
    reset = app.state()
    restart_event(app, 0, expected=409)
    action(app, "start_quiz", expected=409, runId=0)
    assert app.state() == reset
    action(app, "start_quiz", runId=1)
    active = app.state()
    action(app, "restart_event", expected=400, runId=1)
    action(app, "restart_event", expected=409, confirm=True)
    for token in ({}, {"runId": 0}, {"runId": 2}):
        person.post("/api/answer", {"questionId": question["id"], "optionIndex": 0, **token}, expected=409)
    action(app, "close_question", expected=409, runId=0)
    restart_event(app, 0, expected=409)
    assert app.state() == active
    assert person.get("/api/participant")["answer"] is None
    person.post("/api/answer", {"questionId": question["id"], "optionIndex": 0, "runId": 1})
    # Other admin actions may omit runId for compatibility; supplied stale values must fail.
    action(app, "close_question")
    action(app, "reveal_question", runId=1)
    action(app, "finish_quiz", runId=1)
    action(app, "open_voting", runId=1)
    voting = app.state()
    for token in ({}, {"runId": 0}, {"runId": 2}):
        person.post("/api/vote", {"ratings": ratings, **token}, expected=409)
    assert app.state() == voting
    assert person.get("/api/participant")["voting"]["submitted"] is False
    person.post("/api/vote", {"ratings": ratings, "runId": 1})
    restart_event(app, 1)
    assert app.state()["state"]["runId"] == 2
    restart_event(app, 1, expected=409)


def test_restarted_event_still_requires_each_manual_quiz_voting_and_display_stage(app):
    app.seed(groups=3, questions=2)
    action(app, "start_quiz", runId=0)
    restart_event(app, 0)

    def reject_skips(*actions):
        before = app.state()
        for name in actions:
            action(app, name, expected=409, runId=1)
        assert app.state() == before

    reject_skips("open_voting", "show_audience", "show_main")
    action(app, "start_quiz", runId=1)
    reject_skips("reveal_question", "next_question", "finish_quiz", "open_voting", "show_audience", "show_main")
    action(app, "close_question", runId=1)
    reject_skips("wait", "finish_quiz", "open_voting")
    action(app, "reveal_question", runId=1)
    reject_skips("finish_quiz", "open_voting")  # The second question must be completed too.
    action(app, "wait", runId=1)
    action(app, "next_question", runId=1)
    action(app, "close_question", runId=1)
    action(app, "reveal_question", runId=1)
    action(app, "finish_quiz", runId=1)
    reject_skips("close_voting", "show_audience", "show_main")
    action(app, "open_voting", runId=1)
    reject_skips("show_audience", "show_main")
    action(app, "close_voting", runId=1)
    reject_skips("show_main")
    action(app, "show_audience", runId=1)
    action(app, "show_main", runId=1)
    assert app.state()["state"]["baseLocked"] is True
    reject_skips("show_audience")
    group_id = app.groups[0]["id"]
    action(app, "reveal_group", groupId=group_id, runId=1)
    assert next(row for row in app.state()["scores"] if row["groupId"] == group_id)["revealed"] is True


def test_restart_is_scoped_to_the_selected_environment_and_keeps_other_results(app):
    app.seed(groups=3)
    copied = app.admin.post("/api/admin/rehearsal", {"action": "copy"})
    rehearsal_code = copied["codes"][0]["code"]
    rehearsal_run = app.state("rehearsal")["state"]["runId"]
    production_person = app.participant(app.people[0]["code"])
    rehearsal_person = app.participant(rehearsal_code, "rehearsal")
    for environment, client, run_id in (("production", production_person, 0), ("rehearsal", rehearsal_person, rehearsal_run)):
        finish_quiz(app, run_id, environment, client)
        action(app, "open_voting", environment, runId=run_id)
        group_id = client.get("/api/participant")["participant"]["groupId"]
        client.post("/api/vote", {"ratings": ballot(app, group_id, environment=environment), "runId": run_id})
        action(app, "close_voting", environment, runId=run_id)
        action(app, "show_audience", environment, runId=run_id)
        action(app, "show_main", environment, runId=run_id)
        action(app, "reveal_group", environment, groupId=group_id, runId=run_id)
    production_before = app.state()
    rehearsal_before = app.state("rehearsal")
    restart_event(app, rehearsal_run, "rehearsal")
    assert app.state() == production_before
    rehearsal_reset = app.state("rehearsal")
    assert rehearsal_reset["state"]["runId"] == rehearsal_run + 1
    assert rehearsal_reset["state"]["phase"] == "setup"
    assert rehearsal_reset["stats"]["submittedCount"] == 0
    for key in ("groups", "questions", "participants", "settings", "yandex"):
        assert rehearsal_reset[key] == rehearsal_before[key], key
    assert production_person.get("/api/participant")["state"]["runId"] == 0
    assert production_person.get("/api/participant")["voting"]["submitted"] is True
    assert rehearsal_person.get("/api/participant")["authenticated"] is True
    assert rehearsal_person.get("/api/participant")["state"]["runId"] == rehearsal_run + 1
    assert app.participant(rehearsal_code, "rehearsal").get("/api/participant")["authenticated"] is True
    rehearsal_reset = app.state("rehearsal")
    restart_event(app, 0, "production")
    assert app.state("rehearsal") == rehearsal_reset
    assert app.state()["state"]["phase"] == "setup"
    assert app.state()["state"]["runId"] == 1
    app.restart()
    assert app.state("rehearsal")["state"] == rehearsal_reset["state"]
    assert rehearsal_person.get("/api/participant")["state"]["runId"] == rehearsal_run + 1
    assert production_person.get("/api/participant")["state"]["runId"] == 1
