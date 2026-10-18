"""Voting order is configuration, independent of group names and leaderboard rank."""
from conftest import Client


def group_ids(app, environment="production"):
    return [g["id"] for g in app.state(environment)["groups"]]


def test_voting_order_persists_and_omits_each_participants_group(app):
    app.seed(groups=4)
    original = group_ids(app)
    order = [original[i] for i in (2, 0, 3, 1)]
    app.admin.post("/api/admin/groups/order", {"ids": order})
    app.admin.post("/api/admin/groups", {**app.groups[0], "title": "Edited title"})
    app.admin.post("/api/admin/settings", {"background": "#112233"})
    app.restart()
    assert group_ids(app) == order
    participants = [(p, app.participant(p["code"])) for p in app.people]
    app.finish_quiz()
    app.action("open_voting")
    for person, client in participants:
        cards = client.get("/api/participant")["voting"]["groups"]
        assert [g["id"] for g in cards] == [gid for gid in order if gid != person["groupId"]]
    app.admin.post("/api/admin/groups/order", {"ids": original}, expected=409)
    app.action("close_voting")
    app.action("show_audience")
    app.action("show_main")
    display = Client(app.url).get("/api/display")
    assert [g["id"] for g in display["groups"]] == original[::-1]
    app.admin.post("/api/admin/action", {
        "action": "restart_event", "confirm": True, "runId": app.state()["state"]["runId"],
    })
    assert group_ids(app) == order


def test_voting_order_validation_and_group_addition_removal(app):
    app.seed(groups=3)
    original = group_ids(app)
    before = app.state()
    Client(app.url).post("/api/admin/groups/order", {"ids": original}, expected=401)
    for invalid in (None, "invalid", [], original[:-1], [original[0]] * 3,
                    [*original[:-1], "unknown"], [*original, "unknown"]):
        app.admin.post("/api/admin/groups/order", {"ids": invalid}, expected=400)
        assert app.state()["state"] == before["state"]
        assert group_ids(app) == original
    order = original[::-1]
    app.admin.post("/api/admin/groups/order", {"ids": order})
    app.admin.post("/api/admin/groups", {"number": "New", "title": "New group"})
    added_id = group_ids(app)[-1]
    assert group_ids(app) == [*order, added_id]
    app.admin.post("/api/admin/groups/order", {"ids": [added_id, *order]})
    app.admin.request("DELETE", f"/api/admin/groups/{added_id}")
    assert group_ids(app) == order


def test_voting_order_isolated_between_environments(app):
    app.seed(groups=3)
    original = group_ids(app)
    app.admin.post("/api/admin/groups/order", {"ids": original[::-1]})
    app.admin.post("/api/admin/rehearsal", {"action": "copy"})
    assert group_ids(app, "rehearsal") == original[::-1]
    app.admin.post("/api/admin/groups/order?env=rehearsal", {"ids": original})
    assert group_ids(app, "rehearsal") == original
    assert group_ids(app) == original[::-1]
