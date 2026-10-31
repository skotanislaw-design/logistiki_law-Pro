from datetime import date

from payment_forecast import payment_delays, typical_delay, forecast_invoice

TODAY = date(2026, 9, 30)


def _paid(client, issued, paid):
    return {"client_id": client, "date": issued, "paid_at": paid + "T10:00:00+00:00"}


def test_delays_are_per_client_and_ignore_garbage():
    by_client, firm = payment_delays([
        _paid("a", "2026-01-01", "2026-01-11"), _paid("a", "2026-02-01", "2026-02-13"), _paid("a", "2026-03-01", "2026-03-09"),
        _paid("b", "2026-01-01", "2026-03-01"),
        {"client_id": "a", "date": "bad", "paid_at": "2026-01-01"},
        {"client_id": "a", "date": "2026-05-10", "paid_at": "2026-05-01"},  # αρνητική
        {"client_id": "a", "date": "2024-01-01", "paid_at": "2026-05-01"},  # υπερβολική
    ])
    assert by_client["a"] == [10, 12, 8] and by_client["b"] == [59] and len(firm) == 4


def test_typical_delay_falls_back_client_firm_default():
    by_client = {"a": [10, 12, 8], "b": [59]}
    assert typical_delay("a", by_client, []) == (10, "client")
    assert typical_delay("b", by_client, [5, 6, 7, 8, 9]) == (7, "firm")
    assert typical_delay("zzz", {}, [1, 2]) == (30, "default")


def test_fast_payer_is_expected_early_and_late_one_is_flagged():
    by_client = {"fast": [5, 6, 7]}
    ok = forecast_invoice({"client_id": "fast", "date": "2026-09-27"}, by_client, [], TODAY)
    assert ok["expected_date"] == date(2026, 10, 3) and ok["confidence"] == "high" and not ok["at_risk"]
    late = forecast_invoice({"client_id": "fast", "date": "2026-08-01"}, by_client, [], TODAY)
    assert late["days_late"] == 54 and late["at_risk"] and late["confidence"] == "low"
    assert late["expected_date"] == date(2026, 10, 15)


def test_unknown_client_keeps_old_30_day_behaviour_and_bad_date_is_skipped():
    f = forecast_invoice({"client_id": "new", "date": "2026-09-20"}, {}, [], TODAY)
    assert f["expected_date"] == date(2026, 10, 20) and f["basis"] == "default" and f["confidence"] == "medium"
    assert forecast_invoice({"client_id": "x", "date": ""}, {}, [], TODAY) is None
