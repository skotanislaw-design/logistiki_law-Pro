"""Η suite δεν τρέχει ποτέ σε πραγματική MongoDB χωρίς ρητή συγκατάθεση.

Πολλά tests σβήνουν συλλογές (`delete_many({})`) για να στήσουν τις συνθήκες τους. Το `conftest.py` δεν αλλάζει
MONGO_URL που υπάρχει ήδη στο περιβάλλον· το README όμως έβαζε `export MONGO_URL=mongodb://localhost:27017
DB_NAME=nomos_one` στο ίδιο μπλοκ με το `python -m pytest`, οπότε η suite θα έσβηνε τα δεδομένα της τοπικής βάσης του
προγραμματιστή. Το `pytest_configure` του conftest αρνείται πριν συνδεθεί οτιδήποτε.

Τα tests τρέχουν ένα δεύτερο pytest σε υποδιεργασία (μόνο συλλογή, κανένα test δεν εκτελείται): το ίδιο το conftest
της παρούσας διεργασίας έχει ήδη περάσει τον έλεγχο, άρα δεν μπορεί να δοκιμαστεί από μέσα.
"""

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
THIS_FILE = Path(__file__).name


def _collect_only(**env_overrides):
    env = {key: value for key, value in os.environ.items() if key != "NOMOS_TESTS_ALLOW_REAL_MONGO"}
    env.update(env_overrides)
    return subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider", "-o", "addopts=",
         f"tests/{THIS_FILE}"],
        cwd=BACKEND, env=env, capture_output=True, text=True, timeout=120,
    )


def test_real_mongo_url_is_refused_before_anything_connects():
    # Η θύρα 1 δεν ακούει: αν η suite προσπαθούσε να συνδεθεί, η διεργασία θα κρεμούσε ή θα έσκαγε αλλού, όχι με κωδικό 2.
    result = _collect_only(MONGO_URL="mongodb://nomos:secret-password@127.0.0.1:1/?authSource=admin")
    output = result.stdout + result.stderr
    assert result.returncode == 2, output
    assert "NOMOS_TESTS_ALLOW_REAL_MONGO" in output
    assert "secret-password" not in output            # ο κωδικός της βάσης δεν τυπώνεται ποτέ


def test_missing_mongo_url_defaults_to_the_in_memory_database():
    env = {key: value for key, value in os.environ.items() if key not in ("MONGO_URL", "NOMOS_TESTS_ALLOW_REAL_MONGO")}
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider", "-o", "addopts=",
         f"tests/{THIS_FILE}"],
        cwd=BACKEND, env=env, capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_in_memory_url_is_accepted():
    result = _collect_only(MONGO_URL="mongomock://")
    assert result.returncode == 0, result.stdout + result.stderr


def test_explicit_consent_lets_a_real_url_through_the_guard():
    # Μόνο συλλογή (--collect-only): τίποτα δεν εκτελείται, άρα και καμία σύνδεση. Αρκεί για να δείξει ότι ο έλεγχος
    # δεν μπλοκάρει όταν ο προγραμματιστής το ζητήσει ρητά (π.χ. για τα παλιά E2E που θέλουν πραγματική βάση).
    result = _collect_only(MONGO_URL="mongodb://127.0.0.1:1/", NOMOS_TESTS_ALLOW_REAL_MONGO="1")
    assert result.returncode == 0, result.stdout + result.stderr
