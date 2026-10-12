"""Γύρος 5 — τα έγγραφα με letterhead παράγονται πραγματικά, όχι μόνο με «κούκλες» στα tests.

Το PDF γραμματίου, το PDF επιστολής και το DOCX επιστολής διάβαζαν το λογότυπο από το σταθερό `/app/backend/assets/…`.
Το Docker image αυτού του repository αντιγράφει το `backend/` στο `/app/` (βλ. `backend/Dockerfile`), άρα το αρχείο
βρίσκεται στο `/app/assets/…` και οι τρεις εξαγωγές θα απέτυχαν στην παραγωγή. Τα υπάρχοντα tests αντικαθιστούσαν την
`Image` του reportlab με κενό στοιχείο, οπότε ποτέ δεν άνοιγαν το αρχείο.
"""
import io
import pathlib
import re

import docx

BACKEND = pathlib.Path(__file__).resolve().parents[1]
REPO = BACKEND.parent


def test_letter_pdf_is_really_built_with_the_letterhead():
    import pdf_utils
    pdf = pdf_utils.build_letter_pdf({"company_name": "Γραφείο"}, "Προς Δοκιμή", "Θέμα", "Κείμενο της επιστολής.")
    assert pdf.startswith(b"%PDF") and len(pdf) > 2000


def test_grammatio_pdf_is_really_built_with_the_letterhead():
    import pdf_utils
    pdf = pdf_utils.build_grammatio_pdf(
        {"number": "ΓΡΠ-1", "status": "issued", "issued_at": "2026-03-10T09:00:00+00:00", "client_name": "ΑΛΦΑ ΑΕ",
         "reference_amount": 100.0, "total": 124.0}, {"company_name": "Γραφείο"})
    assert pdf.startswith(b"%PDF") and len(pdf) > 2000


def test_letter_docx_is_really_built_with_the_letterhead():
    import docx_utils
    data = docx_utils.build_letter_docx({"company_name": "Γραφείο"}, "Προς Δοκιμή", "Θέμα", "Κείμενο της επιστολής.")
    document = docx.Document(io.BytesIO(data))
    assert len(document.inline_shapes) == 1, "το λογότυπο πρέπει να υπάρχει στο έγγραφο"
    assert "Κείμενο της επιστολής." in "\n".join(p.text for p in document.paragraphs)


def test_the_logo_is_found_relative_to_the_code_not_to_the_install_directory():
    import branding
    import docx_utils
    path = pathlib.Path(branding.LETTERHEAD_LOGO)
    assert path.is_file() and path.parent == BACKEND / "assets"
    assert docx_utils.LOGO_PATH == branding.LETTERHEAD_LOGO


def test_no_source_file_hardcodes_the_original_platform_layout():
    """Συμβολοσειρές που ΞΕΚΙΝΟΥΝ με `/app/backend` (διαδρομές αρχείων)· τα docstrings που εξηγούν το ιστορικό δεν μετρούν."""
    import ast
    offenders = []
    for path in sorted(BACKEND.rglob("*.py")):
        rel = path.relative_to(BACKEND)
        if rel.parts[0] in {"tests", "__pycache__"}:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.startswith("/app/backend"):
                offenders.append(f"{rel}:{node.lineno}: {node.value}")
    assert not offenders, "Διαδρομές σχετικές με το σημείο εγκατάστασης δεν ισχύουν στο Docker image:\n  " + "\n  ".join(offenders)


def test_the_docker_image_layout_is_the_one_the_code_assumes():
    """Το image αντιγράφει backend/ → /app/: ό,τι διαβάζει ο κώδικας πρέπει να βρίσκεται δίπλα στα modules."""
    dockerfile = (BACKEND / "Dockerfile").read_text(encoding="utf-8")
    assert re.search(r"^COPY backend/ /app/\s*$", dockerfile, re.M), "άλλαξε η διάταξη του image — ξανακοιτάξτε τα assets"
    assert (BACKEND / "assets" / "letterhead_logo.png").is_file()
