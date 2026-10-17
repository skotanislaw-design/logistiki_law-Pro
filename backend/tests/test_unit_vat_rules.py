"""Εκπτωσιμότητα ΦΠΑ εισροών: ποσοστά, στρογγυλοποίηση, αναλλοίωτο κόστους και ορατότητα της υπόθεσης «100%»."""
import math
import random

import pytest

from vat_rules import (
    RulesError, SOURCE_DEFAULT, SOURCE_EXPENSE, SOURCE_RULE, MAX_RULES,
    category_rule, deductible_vat, effective_pct, normalize_rules, summarize_expenses,
)


def exp(amount, vat, category="Λοιπά", **extra):
    return {"amount": amount, "vat_amount": vat, "category": category, **extra}


# ------------------------------------------------------------------------------------------------ προεπιλογή ----

def test_without_any_rule_everything_is_deducted_exactly_as_before():
    """Χωρίς κανόνες και ρητά ποσοστά το αποτέλεσμα είναι ΤΟ ΙΔΙΟ με το παλιό άθροισμα — αυτό είναι το συμβόλαιο της Φάσης 1."""
    rng = random.Random(20261017)
    expenses = []
    for _ in range(300):
        net = rng.randint(1, 500000) / 100
        rate = rng.choice([0, 6, 13, 24])
        vat = round(net * rate / 100, 2)
        sign = -1 if rng.random() < 0.1 else 1                      # πιστωτικά προμηθευτών
        expenses.append(exp(sign * round(net + vat, 2), sign * vat, rng.choice(["Ενοίκιο", "Μεταφορικά", "Λοιπά"])))

    result = summarize_expenses(expenses, {})
    assert result["input_vat"] == round(sum(e["vat_amount"] for e in expenses), 2)
    assert result["net_cost"] == round(sum(e["amount"] - e["vat_amount"] for e in expenses), 2)
    assert result["input_vat_nondeductible"] == 0
    assert all(deductible_vat(e, {}) == e["vat_amount"] for e in expenses)          # ούτε στο τελευταίο δεκαδικό


def test_the_default_does_not_round_a_single_expense():
    """Το 100% δεν στρογγυλοποιείται ανά έξοδο: ένα ΦΠΑ με περισσότερα από δύο δεκαδικά περνά όπως ήταν."""
    assert deductible_vat(exp(100, 3.14159), {}) == 3.14159


def test_empty_or_missing_settings_behave_like_no_rules():
    e = exp(124, 24)
    for settings in (None, {}, {"vat_deductibility_rules": {}}, {"vat_deductibility_rules": None}):
        assert effective_pct(e, settings) == (100.0, SOURCE_DEFAULT)
        assert deductible_vat(e, settings) == 24


# ---------------------------------------------------------------------------------------------------- κανόνες ----

def test_category_rule_limits_the_deductible_part_and_the_rest_becomes_cost():
    rules = {"vat_deductibility_rules": {"Φιλοξενία / Εστίαση": 0, "Τηλεπικοινωνίες": 50}}
    expenses = [exp(124, 24, "Φιλοξενία / Εστίαση"), exp(124, 24, "Τηλεπικοινωνίες"), exp(124, 24, "Ενοίκιο")]
    result = summarize_expenses(expenses, rules)
    assert result["gross"] == 372
    assert result["input_vat"] == 36                              # 0 + 12 + 24
    assert result["input_vat_nondeductible"] == 36                # 24 + 12 + 0
    assert result["net_cost"] == 336                              # 372 − 36: το μη εκπιπτόμενο ΦΠΑ είναι κόστος
    assert effective_pct(expenses[0], rules) == (0.0, SOURCE_RULE)
    assert effective_pct(expenses[1], rules) == (50.0, SOURCE_RULE)
    assert effective_pct(expenses[2], rules) == (100.0, SOURCE_DEFAULT)


def test_explicit_percentage_on_the_expense_wins_over_the_category_rule_in_both_directions():
    rules = {"vat_deductibility_rules": {"Μεταφορικά": 0, "Ενοίκιο": 100}}
    assert effective_pct(exp(124, 24, "Μεταφορικά", vat_deductible_pct=100), rules) == (100.0, SOURCE_EXPENSE)
    assert deductible_vat(exp(124, 24, "Μεταφορικά", vat_deductible_pct=100), rules) == 24
    assert effective_pct(exp(124, 24, "Ενοίκιο", vat_deductible_pct=0), rules) == (0.0, SOURCE_EXPENSE)
    assert deductible_vat(exp(124, 24, "Ενοίκιο", vat_deductible_pct=0), rules) == 0


def test_an_explicit_percentage_of_zero_is_a_decision_not_an_absence():
    """Το 0 είναι ποσοστό (καμία έκπτωση)· μόνο το `None`/απουσία σημαίνει «ακολούθησε τον κανόνα»."""
    rules = {"vat_deductibility_rules": {"Ενοίκιο": 100}}
    assert deductible_vat(exp(124, 24, "Ενοίκιο", vat_deductible_pct=0), rules) == 0
    assert deductible_vat(exp(124, 24, "Ενοίκιο", vat_deductible_pct=None), rules) == 24


@pytest.mark.parametrize("pct, vat, expected_deductible, expected_rest", [
    (50, 24.00, 12.00, 12.00),
    (50, 10.01, 5.01, 5.00),          # 5,005 → 5,01 (μισό προς τα πάνω)
    (30, 10.00, 3.00, 7.00),
    (33.33, 100.00, 33.33, 66.67),
    (80, 0.07, 0.06, 0.01),           # 0,056 → 0,06
    (0, 24.00, 0.00, 24.00),
    (100, 24.00, 24.00, 0.00),
])
def test_partial_deduction_rounds_to_the_cent_and_the_rest_is_the_exact_difference(pct, vat, expected_deductible, expected_rest):
    rules = {"vat_deductibility_rules": {"Κ": pct}}
    e = exp(vat * 5, vat, "Κ")
    assert deductible_vat(e, rules) == expected_deductible
    result = summarize_expenses([e], rules)
    assert result["input_vat"] == expected_deductible
    assert result["input_vat_nondeductible"] == expected_rest


def test_supplier_credit_notes_are_symmetric_to_the_original_expense():
    rules = {"vat_deductibility_rules": {"Κ": 50}}
    original, credit = exp(100.10, 10.01, "Κ"), exp(-100.10, -10.01, "Κ")
    assert deductible_vat(original, rules) == 5.01
    assert deductible_vat(credit, rules) == -5.01                  # το μισό στρογγυλοποιείται συμμετρικά, όχι προς +∞
    both = summarize_expenses([original, credit], rules)
    assert both["input_vat"] == 0 and both["input_vat_nondeductible"] == 0 and both["net_cost"] == 0


# ---------------------------------------------------------------------------------------------- αναλλοίωτο ----

def test_net_cost_plus_deductible_vat_always_equals_the_gross_amount():
    """Τίποτε δεν χάνεται και τίποτε δεν δημιουργείται: ό,τι δεν εκπίπτει γίνεται κόστος."""
    rng = random.Random(7)
    categories = ["Ενοίκιο", "Μεταφορικά", "Φιλοξενία / Εστίαση", "Λοιπά", "Τηλεπικοινωνίες"]
    for _ in range(100):
        rules = {c: rng.choice([0, 25, 33.33, 50, 66.67, 100]) for c in rng.sample(categories, rng.randint(0, 4))}
        expenses = []
        for _ in range(rng.randint(0, 25)):
            net = rng.randint(1, 100000) / 100
            vat = round(net * rng.choice([0, 6, 13, 24]) / 100, 2)
            sign = rng.choice([1, 1, 1, -1])
            e = exp(sign * round(net + vat, 2), sign * vat, rng.choice(categories))
            if rng.random() < 0.2:
                e["vat_deductible_pct"] = rng.choice([0, 10, 50, 100])
            expenses.append(e)
        result = summarize_expenses(expenses, {"vat_deductibility_rules": rules})
        assert round(result["net_cost"] + result["input_vat"], 2) == result["gross"]
        assert round(result["input_vat"] + result["input_vat_nondeductible"], 2) == round(sum(e["vat_amount"] for e in expenses), 2)


def test_empty_list_gives_zeros():
    assert summarize_expenses([], {"vat_deductibility_rules": {"Κ": 10}}) == {
        "gross": 0.0, "input_vat": 0.0, "input_vat_nondeductible": 0.0, "net_cost": 0.0, "unruled_vat": 0.0, "unruled": []}


# ------------------------------------------------------------------------------------- «χωρίς κανόνα» ----

def test_unruled_vat_shows_how_much_deduction_rests_on_the_default_assumption():
    rules = {"vat_deductibility_rules": {"Ενοίκιο": 100, "Μεταφορικά": 0}}
    expenses = [
        exp(124, 24, "Ενοίκιο"),                                     # κανόνας 100 → απόφαση, όχι υπόθεση
        exp(124, 24, "Μεταφορικά"),                                  # κανόνας 0
        exp(62, 12, "Λοιπά"),                                        # ΧΩΡΙΣ κανόνα → υπόθεση
        exp(248, 48, "Λοιπά"),                                       # ΧΩΡΙΣ κανόνα → υπόθεση
        exp(124, 24, "Συνδρομές / Λογισμικό"),                       # ΧΩΡΙΣ κανόνα → υπόθεση
        exp(100, 0, "Προμήθειες γραφείου"),                          # χωρίς ΦΠΑ: δεν διακυβεύεται τίποτα
        exp(124, 24, "Προμήθειες γραφείου", vat_deductible_pct=100),  # ρητή απόφαση στο ίδιο το έξοδο
    ]
    result = summarize_expenses(expenses, rules)
    assert result["unruled_vat"] == 84                                 # 12 + 48 + 24
    assert result["unruled"] == [
        {"category": "Λοιπά", "count": 2, "vat_amount": 60.0},
        {"category": "Συνδρομές / Λογισμικό", "count": 1, "vat_amount": 24.0},
    ]


def test_expense_without_a_category_is_reported_under_a_readable_label():
    result = summarize_expenses([{"amount": 124, "vat_amount": 24}, {"amount": 124, "vat_amount": 24, "category": ""}], {})
    assert result["unruled"] == [{"category": "Χωρίς κατηγορία", "count": 2, "vat_amount": 48.0}]


def test_a_firm_with_no_rules_sees_all_its_vat_as_unruled():
    result = summarize_expenses([exp(124, 24, "Ενοίκιο"), exp(62, 12, "Λοιπά")], {})
    assert result["unruled_vat"] == result["input_vat"] == 36


# ----------------------------------------------------------------------------- ανθεκτικότητα σε κακά δεδομένα ----

@pytest.mark.parametrize("junk", ["abc", None, float("nan"), float("inf"), True, [], {}])
def test_a_junk_rule_value_is_ignored_not_trusted(junk):
    settings = {"vat_deductibility_rules": {"Κ": junk}}
    assert category_rule("Κ", settings) is None
    assert effective_pct(exp(124, 24, "Κ"), settings) == (100.0, SOURCE_DEFAULT)


def test_out_of_range_stored_values_are_clamped():
    settings = {"vat_deductibility_rules": {"A": 150, "B": -5}}
    assert effective_pct(exp(124, 24, "A"), settings) == (100.0, SOURCE_RULE)
    assert effective_pct(exp(124, 24, "B"), settings) == (0.0, SOURCE_RULE)


def test_rules_that_are_not_a_dict_are_ignored():
    for rules in ("Ενοίκιο:50", ["Ενοίκιο"], 50):
        assert effective_pct(exp(124, 24, "Ενοίκιο"), {"vat_deductibility_rules": rules}) == (100.0, SOURCE_DEFAULT)


@pytest.mark.parametrize("bad", [None, "x", float("nan"), float("inf"), {}, []])
def test_corrupt_amounts_count_as_zero_instead_of_breaking_the_totals(bad):
    result = summarize_expenses([{"amount": bad, "vat_amount": bad, "category": "Κ"}, exp(124, 24, "Κ")], {})
    assert result["gross"] == 124 and result["input_vat"] == 24
    assert all(math.isfinite(v) for v in (result["gross"], result["input_vat"], result["net_cost"]))


# ---------------------------------------------------------------------------------------- normalize_rules ----

def test_normalize_accepts_a_valid_table_and_stores_it_as_checked():
    assert normalize_rules({" Ενοίκιο ": 100, "Φιλοξενία / Εστίαση": "0", "Κ": "33,5", "Λ": 12.5}) == {
        "Ενοίκιο": 100.0, "Φιλοξενία / Εστίαση": 0.0, "Κ": 33.5, "Λ": 12.5}
    assert normalize_rules(None) == {}
    assert normalize_rules({}) == {}


@pytest.mark.parametrize("raw", [
    "Ενοίκιο:50", ["Ενοίκιο"], 50, True,                       # όχι λεξικό
    {"Κ": 101}, {"Κ": -1}, {"Κ": float("nan")}, {"Κ": float("inf")},
    {"Κ": "abc"}, {"Κ": None}, {"Κ": True}, {"Κ": [50]}, {"Κ": {}},
    {"": 50}, {"   ": 50}, {"Κ" * 81: 50}, {1: 50},
    {"Κ": 50, " Κ ": 60},                                       # διπλή κατηγορία μετά το κόψιμο των κενών
])
def test_normalize_rejects_every_malformed_table(raw):
    with pytest.raises(RulesError):
        normalize_rules(raw)


def test_normalize_limits_the_size_of_the_table():
    assert len(normalize_rules({f"Κατηγορία {i}": 50 for i in range(MAX_RULES)})) == MAX_RULES
    with pytest.raises(RulesError):
        normalize_rules({f"Κατηγορία {i}": 50 for i in range(MAX_RULES + 1)})


def test_the_boundary_values_are_valid_rules():
    assert normalize_rules({"A": 0, "B": 100, "C": 0.01, "D": 99.99}) == {"A": 0.0, "B": 100.0, "C": 0.01, "D": 99.99}
