"""Γύρος 5 (δεύτερος έλεγχος, ελεγκτής D — D-11) — «Παλαιότερα μηνύματα» στη συνομιλία πύλης.

Η συνομιλία δείχνει τα 500 **νεότερα** μηνύματα (ανασκόπηση 4)· όσα έμεναν έξω δεν σημαίνονταν διαβασμένα. Έμενε όμως κενό:
δεν υπήρχε τρόπος να τα φτάσει κανείς. Το κόκκινο «αδιάβαστα» του δικηγόρου έμενε για πάντα πάνω από μηδέν και η οθόνη δεν
έλεγε ότι υπάρχουν παλαιότερα μηνύματα.

Τώρα:
* η απάντηση δηλώνει ότι κόπηκε κάτι (`X-List-Truncated: <όριο>`), ώστε η οθόνη να δείξει «Παλαιότερα μηνύματα»·
* `?before=<created_at του παλαιότερου μηνύματος που βλέπετε>` φέρνει την προηγούμενη σελίδα — με κλειδί τη στιγμή και όχι
  θέση (offset), ώστε ένα νέο μήνυμα που φτάνει στο μεταξύ να μη μετατοπίζει τις σελίδες·
* ό,τι εμφανίζεται σημαίνεται διαβασμένο, ό,τι όχι μένει αδιάβαστο· ο μετρητής φτάνει στο μηδέν όταν διαβαστεί η συνομιλία.

Τα τεστ τρέχουν με σελίδα 3 μηνυμάτων (αντί 500) και στις δύο πλευρές: του δικηγόρου και του πελάτη (πύλη).
"""
import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import routes_portal_chat
from db import db

PAGE = 3
BASE = datetime(2026, 10, 1, 10, 0, 0, tzinfo=timezone.utc)
SIDES = ["lawyer", "portal"]
MARKS = {"lawyer": "client", "portal": "lawyer"}      # ποιου αποστολέα τα μηνύματα σημαίνει διαβασμένα όποιος κοιτάζει
ALIEN_FIRM = "firm-" + "9" * 32


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


def _stamp(i: int) -> str:
    return (BASE + timedelta(seconds=i)).isoformat()


def _text(i: int) -> str:
    return f"μήνυμα {i:04d}"


@pytest.fixture(autouse=True)
def small_pages(monkeypatch):
    monkeypatch.setattr(routes_portal_chat, "VISIBLE_MESSAGES", PAGE)


@pytest.fixture()
def client():
    import server
    with TestClient(server.app, base_url="https://testserver") as c:
        yield c


@pytest.fixture()
def office(client):
    r = client.post("/api/auth/register", json={"email": "paging@example.gr", "password": "Str0ngPass!", "name": "Διαχειριστής"})
    assert r.status_code == 200, r.text
    user = _run(db.users.find_one({"email": "paging@example.gr"}))
    _run(db.users.update_one({"email": "paging@example.gr"}, {"$set": {"role": "admin"}}))
    cid = client.post("/api/clients", json={"name": "ΑΛΦΑ ΑΕ", "afm": "123456783", "client_type": "company",
                                            "email": "alpha@example.gr"}).json()["id"]
    other = client.post("/api/clients", json={"name": "ΒΗΤΑ ΑΕ", "afm": "094259216", "client_type": "company",
                                              "email": "beta@example.gr"}).json()["id"]
    token = client.post("/api/client-portal/invite", json={"client_id": cid}).json()["token"]
    return SimpleNamespace(firm_id=user["firm_id"], client_id=cid, other_client_id=other, token=token)


def _seed(office, count, sender, *, client_id=None, firm_id=None, first=0):
    """`count` μηνύματα, το i-στό στις 10:00:00 + i δευτερόλεπτα, όλα αδιάβαστα. `sender`: τιμή ή συνάρτηση του i."""
    who = sender if callable(sender) else (lambda _i: sender)
    docs = [{
        "client_id": client_id or office.client_id, "client_name": "ΑΛΦΑ ΑΕ", "firm_id": firm_id or office.firm_id,
        "sender": who(i), "sender_name": "x", "text": _text(i), "case_id": None, "read_at": None, "created_at": _stamp(i),
    } for i in range(first, first + count)]
    _run(db.portal_messages.insert_many(docs))
    return docs


def _seed_for(office, side, count, **kw):
    """Μηνύματα του είδους που η προβολή `side` σημαίνει ως διαβασμένα (του πελάτη για τον δικηγόρο, του δικηγόρου για την πύλη)."""
    return _seed(office, count, MARKS[side], **kw)


def _url(office, side):
    return f"/api/portal-chat/messages/{office.client_id}" if side == "lawyer" else f"/api/portal/{office.token}/chat"


def _get(client, office, side, before=None):
    return client.get(_url(office, side), params=None if before is None else {"before": before})


def _texts(response):
    assert response.status_code == 200, response.text
    return [row["text"] for row in response.json()]


def _cut(response):
    return response.headers.get("x-list-truncated")


def _unread(office, side):
    """Πόσα μηνύματα του άλλου μέρους είναι ακόμη αδιάβαστα για όποιον κοιτάζει."""
    return _run(db.portal_messages.count_documents({"client_id": office.client_id, "firm_id": office.firm_id,
                                                    "sender": MARKS[side], "read_at": None}))


# ======================================================================== η πρώτη σελίδα ====

@pytest.mark.parametrize("side", SIDES)
def test_the_first_page_is_the_newest_messages_oldest_first_and_says_there_are_more(client, office, side):
    _seed_for(office, side, 7)

    r = _get(client, office, side)

    assert _texts(r) == [_text(4), _text(5), _text(6)]
    assert _cut(r) == str(PAGE)


@pytest.mark.parametrize("side", SIDES)
@pytest.mark.parametrize("count,is_cut", [(1, False), (PAGE, False), (PAGE + 1, True)])
def test_only_a_conversation_that_really_does_not_fit_is_reported_as_cut(client, office, side, count, is_cut):
    _seed_for(office, side, count)

    r = _get(client, office, side)

    assert len(_texts(r)) == min(count, PAGE)
    assert (_cut(r) is not None) is is_cut


@pytest.mark.parametrize("side", SIDES)
def test_an_empty_conversation_is_simply_empty(client, office, side):
    r = _get(client, office, side)
    assert _texts(r) == [] and _cut(r) is None


# ======================================================================== σελιδοποίηση προς τα πίσω ====

@pytest.mark.parametrize("side", SIDES)
def test_paging_back_walks_the_whole_conversation_without_gaps_or_repeats(client, office, side):
    """8 μηνύματα, σελίδα 3: 5–7, 2–4, 0–1. Κάθε μήνυμα ακριβώς μία φορά· ο μετρητής αδιάβαστων πέφτει 8 → 5 → 2 → 0."""
    _seed_for(office, side, 8)
    assert _unread(office, side) == 8
    pages, cuts, unread_after, before = [], [], [], None

    for _ in range(6):                                           # φραγμός: ένα σπασμένο κλειδί σελίδας δεν πρέπει να κρεμάσει το τεστ
        r = _get(client, office, side, before)
        rows = r.json()
        pages.append([row["text"] for row in rows])
        cuts.append(_cut(r))
        unread_after.append(_unread(office, side))
        if _cut(r) is None:
            break
        before = rows[0]["created_at"]

    assert pages == [[_text(5), _text(6), _text(7)], [_text(2), _text(3), _text(4)], [_text(0), _text(1)]]
    assert cuts == [str(PAGE), str(PAGE), None]
    assert unread_after == [5, 2, 0]


def test_the_lawyers_badge_reaches_zero_only_after_the_old_messages_were_read(client, office):
    """Ο μετρητής που βλέπει ο δικηγόρος (`unread-count`) δεν μηδενίζεται από σελίδα που δεν έδειξε τα παλιά αδιάβαστα."""
    _seed_for(office, "lawyer", 8)

    _get(client, office, "lawyer")
    assert client.get("/api/portal-chat/unread-count").json() == {"unread": 5}
    older = _get(client, office, "lawyer", _stamp(5))
    assert client.get("/api/portal-chat/unread-count").json() == {"unread": 2}
    _get(client, office, "lawyer", older.json()[0]["created_at"])
    assert client.get("/api/portal-chat/unread-count").json() == {"unread": 0}


@pytest.mark.parametrize("side", SIDES)
def test_a_page_that_ends_exactly_at_the_oldest_message_is_not_reported_as_cut(client, office, side):
    _seed_for(office, side, 6)

    first = _get(client, office, side)
    second = _get(client, office, side, first.json()[0]["created_at"])

    assert (_texts(first), _cut(first)) == ([_text(3), _text(4), _text(5)], str(PAGE))
    assert (_texts(second), _cut(second)) == ([_text(0), _text(1), _text(2)], None)


@pytest.mark.parametrize("side", SIDES)
def test_the_boundary_message_itself_is_not_repeated(client, office, side):
    _seed_for(office, side, 7)

    r = _get(client, office, side, _stamp(3))

    assert _texts(r) == [_text(0), _text(1), _text(2)]


@pytest.mark.parametrize("side", SIDES)
def test_a_message_arriving_between_pages_does_not_shift_the_older_page(client, office, side):
    """Σελιδοποίηση με θέση (skip) θα έδειχνε ξανά το μήνυμα #4 αφού φτάσουν τρία νέα· με κλειδί τη στιγμή όχι."""
    _seed_for(office, side, 7)
    first = _get(client, office, side)
    before = first.json()[0]["created_at"]
    _seed_for(office, side, 3, first=7)                          # φτάνουν #7–#9 ενώ ο χρήστης διαβάζει

    older = _get(client, office, side, before)

    assert _texts(older) == [_text(1), _text(2), _text(3)]


@pytest.mark.parametrize("side", SIDES)
def test_a_boundary_older_than_everything_returns_an_empty_page_without_a_cut_flag(client, office, side):
    _seed_for(office, side, 7)

    r = _get(client, office, side, _stamp(0))

    assert _texts(r) == [] and _cut(r) is None


@pytest.mark.parametrize("side", SIDES)
def test_a_boundary_newer_than_everything_is_the_same_as_the_first_page(client, office, side):
    _seed_for(office, side, 7)

    r = _get(client, office, side, _stamp(99))

    assert _texts(r) == [_text(4), _text(5), _text(6)] and _cut(r) == str(PAGE)


# ======================================================================== ισοδύναμες γραφές του ορίου ====

SAME_INSTANT = [
    pytest.param(lambda: _stamp(3), id="όπως αποθηκεύεται"),
    pytest.param(lambda: _stamp(3).replace("+00:00", "Z"), id="Z"),
    pytest.param(lambda: (BASE + timedelta(seconds=3)).astimezone(timezone(timedelta(hours=3))).isoformat(), id="ζώνη +03:00"),
    pytest.param(lambda: (BASE + timedelta(seconds=3)).astimezone(timezone(timedelta(hours=-5))).isoformat(), id="ζώνη −05:00"),
    pytest.param(lambda: "2026-10-01T10:00:03", id="χωρίς ζώνη = UTC"),
    pytest.param(lambda: "2026-10-01T10:00:03.000000+00:00", id="με μηδενικά κλάσματα"),
]


@pytest.mark.parametrize("side", SIDES)
@pytest.mark.parametrize("spelling", SAME_INSTANT)
def test_the_same_instant_written_differently_gives_the_same_page(client, office, side, spelling):
    """Τα `created_at` συγκρίνονται ως κείμενο· χωρίς κανονικοποίηση το `…Z` ή το `+03:00` έδινε διπλά ή χαμένα μηνύματα."""
    _seed_for(office, side, 7)

    r = _get(client, office, side, spelling())

    assert _texts(r) == [_text(0), _text(1), _text(2)] and _cut(r) is None


def test_a_boundary_with_fractions_of_a_second_splits_between_messages(client, office):
    _run(db.portal_messages.insert_many([
        {"client_id": office.client_id, "client_name": "x", "firm_id": office.firm_id, "sender": "client", "sender_name": "x",
         "text": text, "case_id": None, "read_at": None, "created_at": stamp}
        for text, stamp in [("πριν", "2026-10-01T10:00:00+00:00"), ("μέσα", "2026-10-01T10:00:00.250000+00:00"),
                            ("μετά", "2026-10-01T10:00:01+00:00")]]))

    assert _texts(_get(client, office, "lawyer", "2026-10-01T10:00:00.250000+00:00")) == ["πριν"]
    assert _texts(_get(client, office, "lawyer", "2026-10-01T10:00:00.250001+00:00")) == ["πριν", "μέσα"]
    assert _texts(_get(client, office, "lawyer", "2026-10-01T10:00:00.100000+00:00")) == ["πριν"]


def test_a_date_only_boundary_means_the_start_of_that_day_in_utc(client, office):
    _seed_for(office, "lawyer", 7)

    assert _texts(_get(client, office, "lawyer", "2026-10-01")) == []
    assert _texts(_get(client, office, "lawyer", "2026-10-02")) == [_text(4), _text(5), _text(6)]


def test_the_boundary_helper_returns_the_stored_form():
    f = routes_portal_chat._before_filter
    assert f(None) == {}
    assert f("2026-10-01T13:00:03+03:00") == {"created_at": {"$lt": "2026-10-01T10:00:03+00:00"}}
    assert f("2026-10-01T10:00:03Z") == {"created_at": {"$lt": "2026-10-01T10:00:03+00:00"}}
    assert f("2026-10-01T10:00:03") == {"created_at": {"$lt": "2026-10-01T10:00:03+00:00"}}


# ======================================================================== άκυρο όριο ====

NONSENSE = [
    "abc", "", "χθες", "2026-13-45", "2026-10-01T25:00:00", "2026-10-01T10:00:00 00:00",    # το «+» χωρίς κωδικοποίηση γίνεται κενό
    "0001-01-01T00:00:00+05:00", "9999-12-31T23:59:59-05:00",                                 # μέσα στη μορφή, έξω από το εύρος σε UTC
    "x" * 5000,
]


@pytest.mark.parametrize("side", SIDES)
@pytest.mark.parametrize("bad", NONSENSE, ids=lambda s: (s[:24] or "κενό"))
def test_a_nonsense_boundary_is_refused_and_nothing_is_marked_as_read(client, office, side, bad):
    _seed_for(office, side, 5)

    r = _get(client, office, side, bad)

    assert r.status_code == 400, r.text
    assert "παλαιότερα" in r.json()["detail"]
    assert _unread(office, side) == 5


def test_the_portal_answers_an_unknown_link_with_the_same_404_whatever_the_boundary(client, office):
    """Ο έλεγχος του συνδέσμου προηγείται: ένα άκυρο `before` δεν δείχνει διαφορετική απάντηση σε όποιον δεν έχει έγκυρο σύνδεσμο."""
    plain = client.get("/api/portal/not-a-real-token/chat")
    with_bad_boundary = client.get("/api/portal/not-a-real-token/chat", params={"before": "abc"})

    assert plain.status_code == with_bad_boundary.status_code == 404
    assert plain.json() == with_bad_boundary.json()


def test_the_lawyers_side_still_requires_a_login(office):
    import server
    anonymous = TestClient(server.app, base_url="https://testserver")
    r = anonymous.get(f"/api/portal-chat/messages/{office.client_id}", params={"before": _stamp(3)})
    assert r.status_code in (401, 403)


# ======================================================================== απομόνωση ====

@pytest.mark.parametrize("side", SIDES)
def test_a_page_holds_only_this_clients_messages_and_marks_only_those_as_read(client, office, side):
    _seed_for(office, side, 7)
    _seed_for(office, side, 7, client_id=office.other_client_id, first=100)               # ίδιο γραφείο, άλλος πελάτης
    _seed_for(office, side, 7, firm_id=ALIEN_FIRM, first=200)                             # άλλο γραφείο, ίδιο client_id

    newest = _get(client, office, side)
    older = _get(client, office, side, newest.json()[0]["created_at"])

    assert _texts(newest) == [_text(4), _text(5), _text(6)]
    assert _texts(older) == [_text(1), _text(2), _text(3)]
    foreign_unread = _run(db.portal_messages.count_documents(
        {"read_at": None, "$or": [{"client_id": office.other_client_id}, {"firm_id": ALIEN_FIRM}]}))
    assert foreign_unread == 14, "τα ξένα μηνύματα έμειναν ανέγγιχτα"


@pytest.mark.parametrize("side", SIDES)
def test_viewing_marks_only_the_other_sides_messages_that_were_shown(client, office, side):
    """Ο δικηγόρος δεν «διαβάζει» τα δικά του μηνύματα ως πελάτης και αντίστροφα· ό,τι δεν εμφανίστηκε μένει αδιάβαστο."""
    kind = lambda i: "client" if i % 2 == 0 else "lawyer"            # noqa: E731
    _seed(office, 8, kind)

    _get(client, office, side)                                       # εμφανίζονται τα #5, #6, #7

    shown = {5, 6, 7}
    unread = {int(m["text"].split()[-1]) for m in _run(db.portal_messages.find({"read_at": None}).to_list(100))}
    expected = {i for i in range(8) if kind(i) != MARKS[side] or i not in shown}
    assert unread == expected
