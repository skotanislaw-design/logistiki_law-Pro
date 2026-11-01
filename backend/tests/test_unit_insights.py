from datetime import date

import pytest

from automation import run_firm
from db import db
from insights import expense_spikes, client_concentration
from tests.test_unit_autopilot import _seed, FIRM

TODAY = date(2026, 10, 15)


def _e(month, amount, cat="Ενοίκια"):
    return {"date": f"{month}-05", "amount": amount, "category": cat}


def test_spike_needs_history_ratio_and_minimum_delta():
    base = [_e("2026-07", 300), _e("2026-08", 300), _e("2026-09", 300)]
    assert expense_spikes(base + [_e("2026-10", 700)], TODAY)[0]["ratio"] == 2.3
    assert not expense_spikes(base + [_e("2026-10", 500)], TODAY)             # <2×
    assert not expense_spikes([_e("2026-10", 5000)], TODAY)                   # χωρίς ιστορικό
    small = [_e("2026-07", 10), _e("2026-08", 10), _e("2026-09", 10)]
    assert not expense_spikes(small + [_e("2026-10", 60)], TODAY)             # μικρή διαφορά


def _inv(client, total, d="2026-09-01", status="paid"):
    return {"client_id": client, "client_name": client.upper(), "total": total, "date": d, "status": status}


def test_concentration_flags_dominant_client_only():
    top = client_concentration([_inv("a", 6000), _inv("b", 2000), _inv("c", 2000)], TODAY)
    assert top["client_name"] == "A" and top["share"] == 60
    assert not client_concentration([_inv("a", 3000), _inv("b", 3500), _inv("c", 3500)], TODAY)
    assert not client_concentration([_inv("a", 900)], TODAY)                  # μικρός τζίρος
    assert not client_concentration([_inv("a", 6000, "2025-01-01"), _inv("b", 1000), _inv("c", 1000), _inv("d", 1000)], TODAY)  # εκτός 12μήνου
    assert not client_concentration([_inv("a", 6000, status="draft"), _inv("b", 1000), _inv("c", 1000), _inv("d", 1000)], TODAY)


@pytest.mark.asyncio
async def test_insights_job_notifies_once_per_month():
    await _seed({})
    for inv in (_inv("a", 6000, date.today().isoformat()), _inv("b", 2000, date.today().isoformat())):
        await db.invoices.insert_one({**inv, "firm_id": FIRM})
    first = (await run_firm(FIRM, ["insights"]))[0]["stats"]
    again = (await run_firm(FIRM, ["insights"]))[0]["stats"]
    assert first["raised"] == 1 and again["raised"] == 0
