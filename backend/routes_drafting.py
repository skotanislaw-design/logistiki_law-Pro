"""AI-assisted Legal Drafting με Clause Library.
Πρότυπα (templates) + επαναχρησιμοποιήσιμες ρήτρες (clauses) + AI variable filling.
"""
import json
import re
import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from typing import Optional
from bson import ObjectId
from ai_runtime import LlmChat, UserMessage, TextDelta, StreamDone
from db import db
from http_utils import content_disposition
from ai_helpers import EMERGENT_LLM_KEY
from auth import get_current_user
from tenant import tenant_query, firm_id_for

router = APIRouter(prefix="/drafting", tags=["drafting"])


# ========== BUILT-IN CLAUSE LIBRARY ==========
BUILTIN_CLAUSES = [
    {
        "id": "confidentiality",
        "title": "Ρήτρα Εμπιστευτικότητας",
        "category": "Γενικές",
        "body": "Τα συμβαλλόμενα μέρη αναλαμβάνουν την υποχρέωση να τηρήσουν απόλυτη εχεμύθεια για κάθε πληροφορία, έγγραφο ή στοιχείο που θα περιέλθει στη γνώση τους κατά την εκτέλεση της παρούσας συμφωνίας. Η υποχρέωση εμπιστευτικότητας εξακολουθεί να ισχύει και μετά τη λήξη της παρούσας για διάστημα πέντε (5) ετών.",
    },
    {
        "id": "arbitration",
        "title": "Ρήτρα Διαιτησίας",
        "category": "Επίλυση Διαφορών",
        "body": "Κάθε διαφορά που τυχόν προκύψει από την εφαρμογή ή ερμηνεία της παρούσας θα επιλύεται οριστικά με διαιτησία σύμφωνα με τον Κανονισμό Διαιτησίας του Επαγγελματικού Επιμελητηρίου Αθηνών από τρεις (3) διαιτητές που θα ορισθούν σύμφωνα με τον εν λόγω Κανονισμό.",
    },
    {
        "id": "jurisdiction_athens",
        "title": "Αρμοδιότητα Δικαστηρίων Αθηνών",
        "category": "Επίλυση Διαφορών",
        "body": "Αρμόδια για την επίλυση κάθε διαφοράς που τυχόν προκύψει από την παρούσα ορίζονται τα Δικαστήρια της Αθήνας, εφαρμοστέο δε δίκαιο είναι το Ελληνικό.",
    },
    {
        "id": "force_majeure",
        "title": "Ανωτέρα Βία",
        "category": "Γενικές",
        "body": "Ουδέν εκ των συμβαλλομένων ευθύνεται για αδυναμία εκπλήρωσης των συμβατικών του υποχρεώσεων που οφείλεται σε γεγονός ανωτέρας βίας. Ως ανωτέρα βία νοούνται γεγονότα εκτός του ελέγχου των μερών, όπως θεομηνίες, πόλεμος, γενική απεργία, πανδημία και κρατική απαγόρευση.",
    },
    {
        "id": "gdpr",
        "title": "Προστασία Δεδομένων (GDPR)",
        "category": "Συμμόρφωση",
        "body": "Τα συμβαλλόμενα μέρη δηλώνουν ότι θα επεξεργαστούν τα προσωπικά δεδομένα που ανταλλάσσονται στο πλαίσιο της παρούσας σύμφωνα με τον Γενικό Κανονισμό Προστασίας Δεδομένων (ΕΕ) 2016/679 (ΓΚΠΔ) και τον Ν.4624/2019, αποκλειστικά για τους σκοπούς της παρούσας συμφωνίας.",
    },
    {
        "id": "termination_notice",
        "title": "Καταγγελία με προειδοποίηση",
        "category": "Λύση",
        "body": "Η παρούσα σύμβαση μπορεί να καταγγελθεί αζημίως από οποιοδήποτε των μερών με προηγούμενη έγγραφη ειδοποίηση τριάντα (30) ημερών προς το άλλο μέρος, με απόδειξη παραλαβής.",
    },
    {
        "id": "penalty_clause",
        "title": "Ποινική Ρήτρα",
        "category": "Οικονομικά",
        "body": "Σε περίπτωση καθυστέρησης εκπλήρωσης των υποχρεώσεων του {mero} πέραν των {imeres} ημερών από τη συμφωνηθείσα προθεσμία, οφείλεται ποινική ρήτρα ύψους {poso}€ ανά ημέρα καθυστέρησης, ανεξάρτητα από τυχόν επιπλέον αποζημίωση.",
    },
    {
        "id": "assignment_prohibited",
        "title": "Απαγόρευση Εκχώρησης",
        "category": "Γενικές",
        "body": "Κανένα των μερών δε δύναται να εκχωρήσει τα δικαιώματα ή τις υποχρεώσεις που απορρέουν από την παρούσα σε τρίτους χωρίς την προηγούμενη έγγραφη συναίνεση του άλλου μέρους.",
    },
]

# ========== BUILT-IN TEMPLATES ==========
BUILTIN_TEMPLATES = [
    {
        "id": "authorization",
        "title": "Εξουσιοδότηση προς Δικηγόρο",
        "category": "Πληρεξούσια",
        "variables": ["client_name", "client_afm", "client_address", "case_subject", "counterparty"],
        "body": """ΕΞΟΥΣΙΟΔΟΤΗΣΗ

Ο/Η υπογεγραμμένος/η {client_name}, με ΑΦΜ {client_afm} και διεύθυνση κατοικίας {client_address}, με το παρόν εξουσιοδοτώ τον/την Δικηγόρο {lawyer_name} του Δικηγορικού Συλλόγου {bar_association}, με αριθμό μητρώου {bar_number}, όπως με εκπροσωπήσει ενώπιον κάθε αρμόδιας αρχής, δικαστηρίου και υπηρεσίας για την υπόθεσή μου με αντικείμενο: {case_subject}, κατά της/του {counterparty}.

Η παρούσα εξουσιοδότηση καλύπτει την προπαρασκευή, κατάθεση, παράσταση και υπογραφή κάθε αναγκαίου δικογράφου, δήλωσης, αίτησης και εγγράφου, συμπεριλαμβανομένης της είσπραξης οποιωνδήποτε ποσών και της χρήσης κάθε ενδίκου μέσου.

Αθήνα, {date}

Ο/Η Εξουσιοδοτών/ούσα

_______________________
{client_name}""",
    },
    {
        "id": "extrajudicial_notice",
        "title": "Εξώδικη Δήλωση — Πρόσκληση — Διαμαρτυρία",
        "category": "Εξώδικα",
        "variables": ["recipient_name", "recipient_address", "subject", "facts", "demand", "deadline_days"],
        "body": """ΕΞΩΔΙΚΗ ΔΗΛΩΣΗ — ΠΡΟΣΚΛΗΣΗ — ΔΙΑΜΑΡΤΥΡΙΑ

ΠΡΟΣ:
{recipient_name}
{recipient_address}

ΘΕΜΑ: {subject}

Με την παρούσα σας γνωστοποιούμε τα ακόλουθα:

{facts}

Κατόπιν των ανωτέρω, ΣΑΣ ΚΑΛΟΥΜΕ όπως εντός προθεσμίας {deadline_days} ημερών από την επίδοση της παρούσας:

{demand}

Σε αντίθετη περίπτωση, επιφυλασσόμαστε ρητώς κάθε νομίμου δικαιώματός μας για την προστασία των συμφερόντων μας ενώπιον των αρμοδίων Δικαστηρίων.

Αρμόδιος δικαστικός επιμελητής παρακαλείται να επιδώσει νόμιμα την παρούσα προς αυτόν προς τον οποίον απευθύνεται, αντιγράφοντάς την ολόκληρη στην έκθεση επιδόσεώς του, προς γνώση και για τις νόμιμες συνέπειες.

Αθήνα, {date}
Ο/Η Πληρεξούσιος/α Δικηγόρος""",
    },
    {
        "id": "settlement_agreement",
        "title": "Ιδιωτικό Συμφωνητικό Συμβιβασμού",
        "category": "Συμφωνίες",
        "variables": ["party_a", "party_a_afm", "party_b", "party_b_afm", "dispute_subject", "amount", "payment_terms"],
        "body": """ΙΔΙΩΤΙΚΟ ΣΥΜΦΩΝΗΤΙΚΟ ΣΥΜΒΙΒΑΣΜΟΥ

Στην Αθήνα σήμερα, {date}, μεταξύ:

1. Του/της {party_a}, με ΑΦΜ {party_a_afm} (καλούμενος εφεξής «Α' Συμβαλλόμενος»), και
2. Του/της {party_b}, με ΑΦΜ {party_b_afm} (καλούμενος εφεξής «Β' Συμβαλλόμενος»),

συμφωνούνται και γίνονται αμοιβαία αποδεκτά τα ακόλουθα:

ΠΡΟΟΙΜΙΟ
Μεταξύ των συμβαλλομένων ανέκυψε διαφορά αναφορικά με: {dispute_subject}. Επιθυμώντας τη φιλική επίλυση αυτής και προς αποφυγή δικαστικής διαμάχης, τα μέρη συμφωνούν τα εξής:

ΑΡΘΡΟ 1 — Ποσό συμβιβασμού
Ο Α' Συμβαλλόμενος καταβάλλει προς τον Β' Συμβαλλόμενο το ποσό των {amount}, ως πλήρη και οριστική εξόφληση κάθε διαφοράς μεταξύ τους.

ΑΡΘΡΟ 2 — Τρόπος καταβολής
{payment_terms}

ΑΡΘΡΟ 3 — Πλήρης εξόφληση
Με την εκπλήρωση των ανωτέρω, τα συμβαλλόμενα μέρη παραιτούνται ρητώς κάθε αξίωσής τους έναντι του άλλου μέρους αναφορικά με την αντικείμενη διαφορά.

ΑΡΘΡΟ 4 — Εφαρμοστέο δίκαιο
Το παρόν διέπεται από το Ελληνικό δίκαιο. Αρμόδια για κάθε διαφορά είναι τα Δικαστήρια Αθηνών.

Σε πίστωση των ανωτέρω συντάχθηκε το παρόν σε δύο (2) πρωτότυπα και υπογράφεται ως ακολούθως:

Ο Α' Συμβαλλόμενος                              Ο Β' Συμβαλλόμενος

______________________                       ______________________
{party_a}                                              {party_b}""",
    },
    {
        "id": "lease_short",
        "title": "Μισθωτήριο Επαγγελματικής Στέγης (Σύντομο)",
        "category": "Μισθωτήρια",
        "variables": ["landlord", "landlord_afm", "tenant", "tenant_afm", "property_address", "duration_years", "monthly_rent"],
        "body": """ΜΙΣΘΩΤΗΡΙΟ ΣΥΜΒΟΛΑΙΟ ΕΠΑΓΓΕΛΜΑΤΙΚΗΣ ΣΤΕΓΗΣ

Στην Αθήνα σήμερα, {date}, μεταξύ:

1. Του/της {landlord}, με ΑΦΜ {landlord_afm} (καλούμενος εφεξής «Εκμισθωτής»),
2. Του/της {tenant}, με ΑΦΜ {tenant_afm} (καλούμενος εφεξής «Μισθωτής»),

συμφωνούνται τα εξής:

ΑΡΘΡΟ 1 — Μίσθιο
Ο Εκμισθωτής εκμισθώνει προς τον Μισθωτή το κάτωθι ακίνητο: {property_address}, για επαγγελματική χρήση.

ΑΡΘΡΟ 2 — Διάρκεια
Η μίσθωση διαρκεί {duration_years} έτη, αρχόμενη από την ημερομηνία υπογραφής του παρόντος.

ΑΡΘΡΟ 3 — Μίσθωμα
Το μηνιαίο μίσθωμα ορίζεται σε {monthly_rent}, καταβλητέο εντός των πρώτων πέντε (5) ημερών κάθε μήνα σε τραπεζικό λογαριασμό που θα υποδείξει ο Εκμισθωτής.

ΑΡΘΡΟ 4 — Λοιποί όροι
Ο Μισθωτής υποχρεούται να χρησιμοποιεί το μίσθιο αποκλειστικά για τον συμφωνημένο σκοπό, να το διατηρεί σε καλή κατάσταση και να καταβάλλει εγκαίρως το μίσθωμα, τους λογαριασμούς κοινοχρήστων και τα τέλη κοινής ωφελείας.

ΑΡΘΡΟ 5 — Εφαρμοστέο δίκαιο
Εφαρμοστέο είναι το Ελληνικό δίκαιο. Αρμόδια είναι τα Δικαστήρια Αθηνών.

Ο Εκμισθωτής                                          Ο Μισθωτής

______________________                       ______________________
{landlord}                                              {tenant}""",
    },
    {
        "id": "engagement_letter",
        "title": "Έντυπο Ανάθεσης Νομικής Εντολής",
        "category": "Δικηγορικά",
        "variables": ["client_name", "client_afm", "case_matter", "fee_arrangement", "estimated_fee"],
        "body": """ΕΝΤΟΛΗ ΝΟΜΙΚΗΣ ΕΚΠΡΟΣΩΠΗΣΗΣ

Ο πελάτης: {client_name}, με ΑΦΜ {client_afm},
Δικηγορικό γραφείο: {lawyer_firm}
Ημερομηνία εντολής: {date}

ΑΝΤΙΚΕΙΜΕΝΟ ΕΝΤΟΛΗΣ:
{case_matter}

ΣΥΜΦΩΝΙΑ ΑΜΟΙΒΗΣ:
{fee_arrangement}
Εκτιμώμενο συνολικό κόστος: {estimated_fee}

Στην ανωτέρω αμοιβή ΔΕΝ περιλαμβάνονται: παράβολα, γραμμάτια προείσπραξης, δικαστικά έξοδα, έξοδα επιδόσεων, μεταφράσεις και λοιπά έξοδα, τα οποία βαρύνουν τον πελάτη.

ΥΠΟΧΡΕΩΣΕΙΣ ΓΡΑΦΕΙΟΥ:
Το δικηγορικό γραφείο αναλαμβάνει να χειριστεί την υπόθεση με τη δέουσα επιμέλεια και επαγγελματισμό, να ενημερώνει τακτικά τον πελάτη για την πορεία της υπόθεσης και να τηρεί αυστηρή εχεμύθεια σύμφωνα με τον Κώδικα Δικηγόρων και τον ΓΚΠΔ.

ΥΠΟΧΡΕΩΣΕΙΣ ΠΕΛΑΤΗ:
Ο πελάτης αναλαμβάνει να παρέχει έγκαιρα κάθε αναγκαία πληροφορία και έγγραφο, να καταβάλλει την αμοιβή σύμφωνα με τη συμφωνία και να συνεργάζεται καλόπιστα.

Ο Πελάτης                                              Το Γραφείο

______________________                       ______________________
{client_name}                                          {lawyer_firm}""",
    },
    {
        "id": "payment_demand",
        "title": "Όχληση για Πληρωμή",
        "category": "Εισπράξεις",
        "variables": ["debtor_name", "debtor_address", "amount_owed", "invoice_reference", "original_due_date"],
        "body": """ΕΞΩΔΙΚΗ ΟΧΛΗΣΗ

ΠΡΟΣ:
{debtor_name}
{debtor_address}

ΘΕΜΑ: Όχληση για καταβολή οφειλόμενου ποσού

Με το παρόν σας οχλούμε ρητά και εγγράφως όπως καταβάλετε το ποσό των {amount_owed}, το οποίο μας οφείλετε με βάση το τιμολόγιο/παραστατικό {invoice_reference} και το οποίο κατέστη ληξιπρόθεσμο και απαιτητό την {original_due_date}.

Παρά τις προηγηθείσες υπενθυμίσεις και οχλήσεις, δεν έχετε ανταποκριθεί στις υποχρεώσεις σας.

Σας καλούμε ΤΕΛΕΙΩΤΙΚΑ όπως εντός προθεσμίας δέκα (10) ημερών από την επίδοση της παρούσας εξοφλήσετε την ανωτέρω οφειλή, άλλως θα προχωρήσουμε άμεσα στην κατάθεση αγωγής/έκδοση διαταγής πληρωμής, επιδιώκοντας την είσπραξη του κεφαλαίου, των τόκων υπερημερίας, των δικαστικών εξόδων και της δικηγορικής αμοιβής, με ταυτόχρονη έναρξη διαδικασιών αναγκαστικής εκτέλεσης.

Αθήνα, {date}
Ο/Η Πληρεξούσιος/α Δικηγόρος""",
    },
]


class TemplateVars(BaseModel):
    template_id: str
    variables: dict
    ai_enhance: bool = False
    instructions: str = ""


@router.get("/templates")
async def list_templates(user: dict = Depends(get_current_user)):
    """Επιστρέφει built-in + user-custom templates."""
    custom = await db.drafting_templates.find(tenant_query(user, {})).to_list(200)
    return {
        "builtin": BUILTIN_TEMPLATES,
        "custom": [{
            "id": str(t["_id"]),
            "title": t.get("title", ""),
            "category": t.get("category", "Προσωπικά"),
            "variables": t.get("variables", []),
            "body": t.get("body", ""),
            "custom": True,
        } for t in custom],
    }


@router.get("/clauses")
async def list_clauses(user: dict = Depends(get_current_user)):
    """Επιστρέφει built-in + user-custom clauses."""
    custom = await db.drafting_clauses.find(tenant_query(user, {})).to_list(200)
    return {
        "builtin": BUILTIN_CLAUSES,
        "custom": [{"id": str(c["_id"]), "title": c["title"], "category": c.get("category", "Προσωπικά"), "body": c["body"], "custom": True} for c in custom],
    }


class ClauseIn(BaseModel):
    title: str
    category: str = "Προσωπικά"
    body: str


@router.post("/clauses")
async def create_clause(payload: ClauseIn, user: dict = Depends(get_current_user)):
    data = payload.model_dump()
    data["firm_id"] = firm_id_for(user)
    data["created_at"] = datetime.now(timezone.utc).isoformat()
    result = await db.drafting_clauses.insert_one(data)
    return {"id": str(result.inserted_id), "title": payload.title, "category": payload.category, "body": payload.body, "created_at": data["created_at"]}


@router.delete("/clauses/{clause_id}")
async def delete_clause(clause_id: str, user: dict = Depends(get_current_user)):
    try:
        result = await db.drafting_clauses.delete_one(tenant_query(user, {"_id": ObjectId(clause_id)}))
    except Exception:
        raise HTTPException(status_code=404, detail="Δεν βρέθηκε")
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Δεν βρέθηκε")
    return {"message": "Διαγράφηκε"}


def _find_template(template_id: str) -> dict:
    for t in BUILTIN_TEMPLATES:
        if t["id"] == template_id:
            return t
    return None


@router.post("/render")
async def render_template(payload: TemplateVars, user: dict = Depends(get_current_user)):
    """Συνθέτει το πρότυπο με τις μεταβλητές και προαιρετικά ενισχύει με AI."""
    tpl = _find_template(payload.template_id)
    if not tpl:
        try:
            custom = await db.drafting_templates.find_one(tenant_query(user, {"_id": ObjectId(payload.template_id)}))
        except Exception:
            custom = None
        if not custom:
            raise HTTPException(status_code=404, detail="Το πρότυπο δεν βρέθηκε")
        tpl = {
            "id": str(custom["_id"]), "title": custom.get("title", ""),
            "variables": custom.get("variables", []), "body": custom.get("body", ""),
        }

    settings = await db.settings.find_one(tenant_query(user, {})) or {}
    base_vars = {
        "date": datetime.now().strftime("%d/%m/%Y"),
        "lawyer_name": settings.get("company_name", ""),
        "lawyer_firm": settings.get("company_name", ""),
        "bar_association": "Αθηνών",
        "bar_number": "",
        **payload.variables,
    }
    # Αντικατάσταση {var} με ασφαλή τρόπο
    body = tpl["body"]
    for k, v in base_vars.items():
        body = body.replace("{" + k + "}", str(v) if v is not None else f"[{k}]")
    # Missing variables → placeholder με [υπόδειξη]
    missing = re.findall(r"\{([^}]+)\}", body)
    for m in missing:
        body = body.replace("{" + m + "}", f"[{m}]")

    if payload.ai_enhance and payload.instructions:
        try:
            chat = LlmChat(
                api_key=EMERGENT_LLM_KEY,
                session_id=f"draft-{uuid.uuid4()}",
                system_message="Είσαι έμπειρος Έλληνας δικηγόρος. Βελτιώνεις προσχέδια νομικών εγγράφων σε επίσημα ελληνικά. Διατηρείς τη δομή και το ύφος. Δεν προσθέτεις νομολογία που δεν σου δίνεται.",
            ).with_model("openai", "gpt-5.4-mini")
            prompt = f"Οδηγία εξατομίκευσης: {payload.instructions}\n\nΑρχικό κείμενο:\n{body}\n\nΕπίστρεψε ΜΟΝΟ το τελικό κείμενο, χωρίς εισαγωγικά σχόλια."
            full_text = ""
            async for ev in chat.stream_message(UserMessage(text=prompt)):
                if isinstance(ev, TextDelta):
                    full_text += ev.content
                elif isinstance(ev, StreamDone):
                    break
            if full_text.strip():
                body = full_text.strip()
        except Exception:
            pass

    return {"title": tpl.get("title", ""), "body": body}


class DocxRequest(BaseModel):
    title: str
    body: str


@router.post("/render/docx")
async def render_docx(payload: DocxRequest, user: dict = Depends(get_current_user)):
    from docx_utils import build_letter_docx
    doc_bytes = build_letter_docx(
        settings=await db.settings.find_one(tenant_query(user, {})) or {},
        recipient="",
        subject=payload.title,
        body=payload.body,
    )
    safe = re.sub(r"[^\w\-]", "_", payload.title or "document")[:60]
    return Response(
        content=doc_bytes,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": content_disposition(f"{safe}.docx")},
    )
