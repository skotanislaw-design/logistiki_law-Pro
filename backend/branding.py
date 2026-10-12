"""Θέσεις των αρχείων επωνυμίας (letterhead).

Ορίζονται **σχετικά με τον κώδικα**, όχι με το σημείο εγκατάστασης. Η αρχική πλατφόρμα είχε το backend στο
`/app/backend/`, ενώ το Docker image αυτού του repository αντιγράφει το `backend/` στο `/app/` — το σταθερό
`/app/backend/assets/…` δεν υπήρχε εκεί, και η εξαγωγή γραμματίου σε PDF, επιστολής σε PDF και επιστολής σε DOCX
απέτυχαν με σφάλμα στο παραγωγικό περιβάλλον.
"""
from pathlib import Path

ASSETS_DIR = Path(__file__).resolve().parent / "assets"
LETTERHEAD_LOGO = str(ASSETS_DIR / "letterhead_logo.png")
