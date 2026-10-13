"""Γύρος 5 (δεύτερος έλεγχος, ελεγκτής C) — τι δεν έβλεπε ο έλεγχος αρίθμησης.

* C-06: ο έλεγχος μετρούσε κενά μόνο ΑΝΑΜΕΣΑ σε υπάρχοντες αριθμούς. Ένα πρώτο νούμερο που χάθηκε (σειρά που ξεκινά από 0002)
  και ένας μετρητής που έχει δώσει αριθμούς χωρίς κανένα παραστατικό (κατάρρευση μετά την παραχώρηση, παραστατικά που χάθηκαν)
  περνούσαν ως «ok» και ο Autopilot δεν άνοιγε κάρτα.
* C-13: το «δάπεδο» της επόμενης έκδοσης (`_previous_in_series`) δεχόταν αριθμούς 11 ψηφίων που η αναφορά θεωρεί «εκτός
  μορφής»: ένας τέτοιος αριθμός (εισαγωγή, επαναφορά) έδινε στο επόμενο παραστατικό τον 12345678902.
* C-14: το κλειδί της κάρτας «παραστατικά χωρίς αριθμό» ήταν το πλήθος· αφού ο λογιστής έκλεινε την κάρτα για 3 παραστατικά,
  3 ΑΛΛΑ δεν άνοιγαν νέα κάρτα.

Απόφαση για το C-06: ένα πρώτο νούμερο μεγαλύτερο του 1 ΔΕΝ είναι «κενό» (η συνέχιση αρίθμησης από άλλο σύστημα μέσα στο
έτος είναι νόμιμη — βλ. test_unit_numbering_r5.py), είναι όμως πάντα ορατό: παρατήρηση στην αναφορά και ενημερωτική κάρτα που
ο λογιστής επιβεβαιώνει μία φορά.
"""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest

import invoice_services as svc
import numbering
from automation import run_firm
from db import db

FIRM = "firm-numbering-r5d"
OTHER_FIRM = "firm-numbering-r5d-other"


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


def _ago(**delta) -> str:
    return (datetime.now(timezone.utc) - timedelta(**delta)).isoformat()


@pytest.fixture()
def protected():
    from server import ensure_invoice_number_index
    _run(ensure_invoice_number_index())


async def _put(number, *, status="issued", when="2026-03-01", firm=FIRM, **over):
    doc = {"firm_id": firm, "number": number, "status": status, "date": when, "total": 124.0, "subtotal": 100.0,
           "line_items": [], **over}
    return (await db.invoices.insert_one(doc)).inserted_id


async def _cards(kind, firm=FIRM, open_only=False):
    query = {"firm_id": firm, "type": "numbering", "dedupe_key": {"$regex": f"^numbering:{kind}"}}
    if open_only:
        query["resolved_at"] = None
    return await db.notifications.find(query).to_list(50)


async def _human_close(card):
    await db.notifications.update_one(
        {"_id": card["_id"]}, {"$set": {"resolved_at": _ago(minutes=1), "resolved_by": "lawyer@example.gr", "resolution": "done"}})


# ============================================ C-06α: το πρώτο νούμερο που δεν ξεκινά από 1 ====

def test_a_series_that_starts_above_one_is_an_observation_not_a_gap_and_not_an_error():
    async def scenario():
        await _put("ΤΠΥ-2026-0002", when="2026-03-01")
        await _put("ΤΠΥ-2026-0003", when="2026-03-02")
        report = await svc.numbering_report(FIRM)
        s = report["series"][0]
        assert s["gaps"] == [] and s["gaps_total"] == 0 and s["first"] == 2 and s["ok"] is True
        assert report["observations"] == [{"kind": "starts_above_one", "series": "ΤΠΥ-2026", "first": 2}]
        assert report["ok"] is True and not any(report["problems"].values())

    _run(scenario())


def test_a_series_that_starts_at_one_has_no_observation():
    async def scenario():
        await _put("ΤΠΥ-2026-0001")
        await _put("ΑΠΥ-2026-0001")
        await _put("ΤΠΥ-2025-0001", when="2025-03-01")
        assert (await svc.numbering_report(FIRM))["observations"] == []

    _run(scenario())


def test_observations_are_per_series_and_year():
    async def scenario():
        await _put("ΤΠΥ-2026-0005")
        await _put("ΑΠΥ-2026-0001")
        await _put("ΠΙΣ-2025-0002", when="2025-05-01")
        names = sorted((o["series"], o["first"]) for o in (await svc.numbering_report(FIRM))["observations"])
        assert names == [("ΠΙΣ-2025", 2), ("ΤΠΥ-2026", 5)]
        only_2026 = (await svc.numbering_report(FIRM, 2026))["observations"]
        assert [o["series"] for o in only_2026] == ["ΤΠΥ-2026"]

    _run(scenario())


def test_the_autopilot_raises_one_informational_card_that_a_person_closes_once(protected):
    async def scenario():
        await _put("ΤΠΥ-2026-0002", when="2026-03-01")
        result = (await run_firm(FIRM, ["numbering_check"]))[0]
        assert result["status"] == "ok" and result["stats"]["ok"] is True and result["stats"]["raised"] == 1
        [card] = await _cards("start:")
        assert card["level"] == "info" and card["requires_action"] is True
        assert "ΤΠΥ-2026" in card["title"] and "0002" in card["message"] and "άλλο σύστημα" in card["message"]

        assert (await run_firm(FIRM, ["numbering_check"]))[0]["stats"]["raised"] == 0          # δεν ξανανοίγει μόνη της
        assert len(await _cards("start:")) == 1

        await _human_close(card)
        assert (await run_firm(FIRM, ["numbering_check"]))[0]["stats"]["raised"] == 0          # ο άνθρωπος το είδε
        assert await _cards("start:", open_only=True) == []

        await _put("ΑΠΥ-2026-0007", when="2026-03-05")                                          # άλλη σειρά → νέα κάρτα
        assert (await run_firm(FIRM, ["numbering_check"]))[0]["stats"]["raised"] == 1
        [fresh] = await _cards("start:", open_only=True)
        assert "ΑΠΥ-2026" in fresh["title"]

    _run(scenario())


def test_a_real_problem_beside_an_observation_still_makes_the_check_fail(protected):
    async def scenario():
        await _put("ΤΠΥ-2026-0002", when="2026-03-01")
        await _put("ΤΠΥ-2026-0004", when="2026-03-02")                 # κενό: λείπει το 0003
        result = (await run_firm(FIRM, ["numbering_check"]))[0]
        assert result["stats"]["ok"] is False and result["stats"]["problems"]["gaps"] == 1
        assert len(await _cards("gaps:")) == 1 and len(await _cards("start:")) == 1

    _run(scenario())


# ===================================== C-06β: μετρητής που έδωσε αριθμούς χωρίς κανένα παραστατικό ====

def test_a_counter_that_handed_out_numbers_without_any_invoice_is_a_gap_of_the_whole_series():
    async def scenario():
        await db.counters.insert_one({"_id": f"invoice-{FIRM}-2026", "seq": 3})
        report = await svc.numbering_report(FIRM)
        [s] = report["series"]
        assert (s["series"], s["count"], s["first"], s["last"]) == ("ΤΠΥ-2026", 0, None, 0)
        assert s["gaps"] == [1, 2, 3] and s["gaps_total"] == 3 and s["ok"] is False
        assert s["counter"] == {"seq": 3, "state": "ahead", "ahead_by": 3}
        assert report["ok"] is False and report["problems"]["gaps"] == 3 and report["checked"] == 0

    _run(scenario())


def test_counters_of_other_prefixes_other_years_and_other_firms_are_told_apart():
    async def scenario():
        await db.counters.insert_many([
            {"_id": f"invoice-ΑΠΥ-{FIRM}-2026", "seq": 2},          # άλλο πρόθεμα
            {"_id": f"invoice-{FIRM}-2025", "seq": 4},              # άλλο έτος
            {"_id": f"invoice-{OTHER_FIRM}-2026", "seq": 9},        # άλλο γραφείο
            {"_id": f"invoice-ΑΠΥ-{OTHER_FIRM}-2026", "seq": 9},
            {"_id": f"case-{FIRM}-2026", "seq": 7},                 # άλλο είδος μετρητή (υποθέσεις)
            {"_id": f"invoice-{FIRM}-2024", "seq": 0},              # κενός μετρητής: δεν έδωσε αριθμό
        ])
        report = await svc.numbering_report(FIRM)
        found = {(s["prefix"], s["year"]): s["gaps_total"] for s in report["series"]}
        assert found == {("ΤΠΥ", 2025): 4, ("ΑΠΥ", 2026): 2}
        assert [s["series"] for s in report["series"]] == ["ΤΠΥ-2025", "ΑΠΥ-2026"]            # έτος, μετά πρόθεμα
        assert [s["series"] for s in (await svc.numbering_report(FIRM, 2026))["series"]] == ["ΑΠΥ-2026"]

    _run(scenario())


def test_a_counter_with_invoices_is_reported_once_not_twice():
    async def scenario():
        await _put("ΤΠΥ-2026-0001")
        await db.counters.insert_one({"_id": f"invoice-{FIRM}-2026", "seq": 1})
        report = await svc.numbering_report(FIRM)
        assert len(report["series"]) == 1 and report["series"][0]["count"] == 1 and report["ok"] is True

    _run(scenario())


def test_a_corrupt_counter_value_is_ignored_not_fatal():
    async def scenario():
        await db.counters.insert_many([
            {"_id": f"invoice-{FIRM}-2026", "seq": "άκυρο"},
            {"_id": f"invoice-ΑΠΥ-{FIRM}-2026", "seq": -5},
            {"_id": f"invoice-ΠΙΣ-{FIRM}-2026", "seq": 1e30},
        ])
        report = await svc.numbering_report(FIRM)
        assert report["series"] == [] and report["ok"] is True

    _run(scenario())


def test_a_long_empty_series_does_not_build_a_long_list():
    async def scenario():
        await db.counters.insert_one({"_id": f"invoice-{FIRM}-2026", "seq": 5_000_000})
        [s] = (await svc.numbering_report(FIRM))["series"]
        assert s["gaps_total"] == 5_000_000 and len(s["gaps"]) == svc.GAP_LIST_LIMIT

    _run(scenario())


def test_the_autopilot_raises_one_card_for_an_empty_series_and_closes_it_when_the_numbers_appear(protected):
    async def scenario():
        await db.counters.insert_one({"_id": f"invoice-{FIRM}-2026", "seq": 2})
        result = (await run_firm(FIRM, ["numbering_check"]))[0]
        assert result["stats"]["ok"] is False and result["stats"]["raised"] == 1
        [card] = await _cards("empty:")
        assert card["level"] == "warning" and "ΤΠΥ-2026" in card["title"] and "δείχνει 2" in card["message"]
        assert await _cards("gaps:") == []                                    # όχι δεύτερη κάρτα για το ίδιο πρόβλημα

        await _put("ΤΠΥ-2026-0001", when="2026-03-01")
        await _put("ΤΠΥ-2026-0002", when="2026-03-02")
        result = (await run_firm(FIRM, ["numbering_check"]))[0]
        assert result["stats"]["ok"] is True and result["stats"]["resolved"] == 1
        assert await _cards("empty:", open_only=True) == []

    _run(scenario())


def test_an_empty_series_of_another_office_raises_nothing_here(protected):
    async def scenario():
        await db.counters.insert_one({"_id": f"invoice-{OTHER_FIRM}-2026", "seq": 4})
        result = (await run_firm(FIRM, ["numbering_check"]))[0]
        assert result["stats"]["ok"] is True and result["stats"]["raised"] == 0
        assert (await svc.numbering_report(OTHER_FIRM))["problems"]["gaps"] == 4

    _run(scenario())


def test_the_restore_report_names_an_empty_series_as_a_gap():
    """Μετά από επαναφορά ο μετρητής μπορεί να είναι μπροστά ενώ τα παραστατικά δεν επανήλθαν: η αναφορά το λέει."""
    import routes_backup

    assert "gaps" in routes_backup._NUMBERING_PROBLEMS


# ====================================== C-13: το «δάπεδο» της έκδοσης ακολουθεί τον ορισμό της αναφοράς ====

def test_an_eleven_digit_number_does_not_become_the_floor_of_the_next_issue():
    async def scenario():
        await _put("ΤΠΥ-2026-0003", when="2026-03-01")
        await _put("ΤΠΥ-2026-12345678901", when="2026-03-02")
        previous = await svc._previous_in_series(FIRM, "ΤΠΥ", 2026)
        assert previous == (3, "2026-03-01", "ΤΠΥ-2026-0003")

    _run(scenario())


def test_the_previous_number_ignores_other_series_and_unreadable_numbers():
    async def scenario():
        await _put("ΤΠΥ-2026-0002", when="2026-03-01")
        await _put("ΤΠΥ-2026-007x", when="2026-03-02")                  # ψηφία και γράμματα
        await _put("ΤΠΥ-2026-", when="2026-03-03")                      # χωρίς αύξοντα
        await _put("ΑΠΥ-2026-0009", when="2026-03-04")                  # άλλο πρόθεμα
        await _put("ΤΠΥ-2025-0008", when="2025-03-04")                  # άλλο έτος
        await _put("ΤΠΥ-2026-0005", status="draft", when="2026-03-05") # πρόχειρο: δεν μετρά
        await _put("ΤΠΥ-2026-0004", status="cancelled", when="2026-03-06")
        assert await svc._previous_in_series(FIRM, "ΤΠΥ", 2026) == (4, "2026-03-06", "ΤΠΥ-2026-0004")

    _run(scenario())


def test_a_series_with_only_unreadable_numbers_has_no_previous():
    async def scenario():
        await _put("ΤΠΥ-2026-99999999999")
        assert await svc._previous_in_series(FIRM, "ΤΠΥ", 2026) is None

    _run(scenario())


def test_the_first_use_of_a_counter_ignores_numbers_longer_than_a_series_number():
    async def scenario():
        await _put("ΤΠΥ-2026-0004", when="2026-03-01")
        await _put("ΤΠΥ-2026-12345678901", when="2026-03-02")
        assert await numbering._scan_existing_max(db.invoices, FIRM, "ΤΠΥ", 2026) == 4
        await _put("ΤΠΥ-2026-999999999", when="2026-03-03")            # 9 ψηφία: ακόμη αριθμός σειράς
        assert await numbering._scan_existing_max(db.invoices, FIRM, "ΤΠΥ", 2026) == 999999999

    _run(scenario())


def test_the_next_number_after_an_eleven_digit_import_is_a_normal_one():
    async def scenario():
        await _put("ΤΠΥ-2026-0003", when="2026-03-01")
        await _put("ΤΠΥ-2026-12345678901", when="2026-03-02")
        number = await svc.next_invoice_number(FIRM, "ΤΠΥ", 2026, date_iso="2026-03-03",
                                               floor=(await svc._previous_in_series(FIRM, "ΤΠΥ", 2026))[0])
        assert number == "ΤΠΥ-2026-0004"

    _run(scenario())


# ======================= C-14: η κάρτα «παραστατικά χωρίς αριθμό» ξεχωρίζει διαφορετικά παραστατικά ====

def test_a_card_closed_for_three_unnumbered_documents_does_not_hide_three_other_ones(protected):
    async def scenario():
        first = [await _put("", when=f"2026-03-0{n}") for n in (1, 2, 3)]
        result = (await run_firm(FIRM, ["numbering_check"]))[0]
        assert result["stats"]["raised"] == 1 and result["stats"]["problems"]["unnumbered"] == 3
        [card] = await _cards("unnumbered:")
        await _human_close(card)

        assert (await run_firm(FIRM, ["numbering_check"]))[0]["stats"]["raised"] == 0       # ίδια τρία → ο άνθρωπος τα είδε

        await db.invoices.delete_many({"_id": {"$in": first}})                               # διορθώθηκαν/διαγράφηκαν
        other = [await _put(None, status="paid", when=f"2026-04-0{n}") for n in (1, 2, 3)]
        result = (await run_firm(FIRM, ["numbering_check"]))[0]
        assert result["stats"]["problems"]["unnumbered"] == 3
        assert result["stats"]["raised"] == 1                                                # ΑΛΛΑ τρία → νέα κάρτα
        assert len(await _cards("unnumbered:", open_only=True)) == 1
        assert len(other) == 3

    _run(scenario())


def test_the_same_unnumbered_documents_keep_one_card(protected):
    async def scenario():
        for n in (1, 2):
            await _put("", when=f"2026-03-0{n}")
        assert (await run_firm(FIRM, ["numbering_check"]))[0]["stats"]["raised"] == 1
        assert (await run_firm(FIRM, ["numbering_check"]))[0]["stats"]["raised"] == 0
        assert len(await _cards("unnumbered:")) == 1

    _run(scenario())


def test_the_report_lists_the_ids_of_unnumbered_documents_for_the_fingerprint():
    async def scenario():
        a, b = await _put(""), await _put(None, status="cancelled")
        await _put("ΤΠΥ-2026-0001")
        await _put("", status="draft")                                  # πρόχειρο: δεν είναι εκδοθέν
        await _put("", firm=OTHER_FIRM)
        report = await svc.numbering_report(FIRM)
        assert report["unnumbered_ids"] == sorted([str(a), str(b)]) and report["problems"]["unnumbered"] == 2

    _run(scenario())
