import { useEffect, useMemo, useRef, useState } from "react";
import axios, { API, money, errMsg } from "@/lib/api";
import { invoiceLabel, owedAmount } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Checkbox } from "@/components/ui/checkbox";
import { Badge } from "@/components/ui/badge";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Bank as BankIcon, LinkSimple, MagnifyingGlass, Prohibit, UploadSimple, WarningCircle } from "@phosphor-icons/react";
import { toast } from "sonner";

// Το GET /bank/transactions επιστρέφει το πολύ 500 κινήσεις ανά αίτημα.
const SERVER_LIMIT = 500;

const BANKS = ["Πειραιώς", "Alpha Bank", "Εθνική", "Eurobank", "Attica", "Άλλη"];

const FILTERS = [
  { key: "action", label: "Χρειάζονται ενέργεια", statuses: ["unmatched", "auto_matched"] },
  { key: "done", label: "Συμφωνημένες", statuses: ["auto_confirmed", "manual_matched"] },
  { key: "ignored", label: "Αγνοημένες", statuses: ["ignored"] },
  { key: "all", label: "Όλες", statuses: ["all"] },
];

const STATUS_LABEL = {
  unmatched: "Χωρίς αντιστοίχιση", auto_matched: "Πρόταση", auto_confirmed: "Συμφωνημένη",
  manual_matched: "Συμφωνημένη", ignored: "Αγνοημένη",
};
const STATUS_VARIANT = {
  unmatched: "outline", auto_matched: "secondary", auto_confirmed: "default", manual_matched: "default", ignored: "secondary",
};

// Ποσά σε λεπτά: η σύγκριση «ακριβές ποσό» δεν πρέπει να εξαρτάται από στρογγυλοποίηση κινητής υποδιαστολής.
const cents = (v) => Math.round((Number(v) || 0) * 100);

// Οι ημερομηνίες κινήσεων είναι ημέρες (YYYY-MM-DD)· τυπώνονται ως έχουν, χωρίς μετατροπή ζώνης ώρας.
const day = (iso) => {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso || "");
  return m ? `${m[3]}/${m[2]}/${m[1]}` : (iso || "—");
};

// Αναζήτηση χωρίς τόνους και διάκριση πεζών/κεφαλαίων («γαμμα» βρίσκει «ΓΑΜΜΑ ΕΠΕ»).
const fold = (s) => String(s || "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase().replace(/ς/g, "σ");

const byDateDesc = (a, b) => (b.date || "").localeCompare(a.date || "") || String(b.id).localeCompare(String(a.id));

const isCredit = (tx) => Number(tx.amount) > 0;
const isOpen = (tx) => tx.match_status === "unmatched" || tx.match_status === "auto_matched";

// Πόσες κινήσεις έχει η προβολή συνολικά, σύμφωνα με τη σύνοψη του server (null όσο δεν έχει φορτώσει).
function viewTotal(filterKey, summary) {
  if (!summary) return null;
  const { statuses } = FILTERS.find((f) => f.key === filterKey);
  if (statuses[0] === "all") return Number(summary.total) || 0;
  return statuses.reduce((sum, status) => sum + (Number(summary[status]) || 0), 0);
}

// Η προβολή δείχνει το πολύ SERVER_LIMIT κινήσεις ανά αίτημα, οι πιο πρόσφατες. Το κείμενο λέει πόσες λείπουν και τι
// μπορεί να κάνει ο χρήστης — και δεν προτείνει «συμφωνήστε ή αγνοήστε» εκεί όπου δεν υπάρχουν τέτοιες ενέργειες.
function truncationNotice(filterKey, shown, total) {
  const hidden = total - shown;
  if (filterKey === "action") {
    return `Εμφανίζονται ${shown} από ${total} κινήσεις — οι ${hidden} παλαιότερες δεν φορτώνονται (το πολύ ${SERVER_LIMIT} ανά κατάσταση). `
      + "Συμφωνήστε ή αγνοήστε τις πρώτες για να εμφανιστούν οι υπόλοιπες.";
  }
  const base = `Εμφανίζονται οι ${shown} πιο πρόσφατες από ${total} κινήσεις — οι ${hidden} παλαιότερες δεν εμφανίζονται σε αυτή την προβολή (το πολύ ${SERVER_LIMIT} ανά αίτημα).`;
  return filterKey === "all" ? `${base} Για όσες χρειάζονται ενέργεια χρησιμοποιήστε την προβολή «Χρειάζονται ενέργεια».` : base;
}

// Υποψήφια τιμολόγια για μια κατάθεση, με πρώτα τα πιο κοντινά στο ποσό της:
//  · `open`: εκδοθέντα με υπόλοιπο προς είσπραξη — η συμφωνία τα εξοφλεί·
//  · `settled`: τιμολόγια που ο χρήστης είχε σημειώσει εξοφλημένα με το χέρι και δεν τα έχει αναλάβει άλλη κατάθεση
//    (`bank_link_due` από τον server) — η κατάθεση απλώς συνδέεται, η κατάσταση του τιμολογίου δεν αλλάζει.
function buildCandidates(invoices, deposit) {
  const open = [];
  const settled = [];
  for (const inv of invoices || []) {
    if (inv.status === "issued" && owedAmount(inv) > 0) {
      const owed = owedAmount(inv);
      open.push({ inv, owed, gap: Math.abs(cents(owed) - cents(deposit)), settled: false });
    } else if (inv.status === "paid" && !inv.credit_note_id && Number(inv.bank_link_due) > 0) {
      const owed = Number(inv.bank_link_due);
      settled.push({ inv, owed, gap: Math.abs(cents(owed) - cents(deposit)), settled: true });
    }
  }
  const nearest = (a, b) => a.gap - b.gap || (a.inv.date || "").localeCompare(b.inv.date || "")
    || String(a.inv.number || "").localeCompare(String(b.inv.number || ""));
  return { open: open.sort(nearest), settled: settled.sort(nearest) };
}

function MatchCell({ tx }) {
  if (!tx.matched_invoice_number) return <span className="text-muted-foreground">—</span>;
  const proposal = tx.match_status === "auto_matched";
  return (
    <div className="space-y-0.5" data-testid={`bank-match-${tx.id}`}>
      <div className="flex items-center gap-2">
        <span className="font-mono text-xs">{tx.matched_invoice_number}</span>
        {proposal && <span className="text-xs text-muted-foreground">{tx.match_confidence}%</span>}
      </div>
      {proposal && tx.match_ambiguous && (
        <div className="flex items-center gap-1 text-xs text-[hsl(var(--gold))]" data-testid={`bank-ambiguous-${tx.id}`}>
          <WarningCircle size={14} /> Αμφίβολη — δεύτερο πιθανό τιμολόγιο ή διαφορά ποσού
        </div>
      )}
      {!proposal && Number(tx.amount_difference) !== 0 && tx.amount_difference != null && (
        <div className="text-xs text-muted-foreground">Διαφορά ποσού που έγινε αποδεκτή: {money(tx.amount_difference)}</div>
      )}
    </div>
  );
}

function ReconcileDialog({ tx, invoices, loadInvoices, closedTruncated, onClose, onDone }) {
  const [list, setList] = useState(invoices);
  const [truncated, setTruncated] = useState(!!closedTruncated);
  const [query, setQuery] = useState("");
  const [selectedId, setSelectedId] = useState(tx.match_status === "auto_matched" ? (tx.matched_invoice_id || "") : "");
  const [accepted, setAccepted] = useState(false);
  const [showSettled, setShowSettled] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const deposit = Number(tx.amount) || 0;

  const { open, settled } = useMemo(() => buildCandidates(list, deposit), [list, deposit]);

  const needle = fold(query).trim();
  const matches = ({ inv }) => !needle || fold(inv.number).includes(needle) || fold(inv.client_name).includes(needle);
  const shownOpen = open.filter(matches);
  const shownSettled = settled.filter(matches);

  const selected = open.find(({ inv }) => inv.id === selectedId) || settled.find(({ inv }) => inv.id === selectedId) || null;
  const diffCents = selected ? cents(deposit) - cents(selected.owed) : 0;
  const exact = !!selected && diffCents === 0;
  const diff = diffCents / 100;
  const canConfirm = !!selected && !saving && (exact || accepted);

  const choose = (id) => { setSelectedId(id); setAccepted(false); setError(""); };

  const confirm = async () => {
    if (!canConfirm) return;
    const before = selected;
    setSaving(true); setError("");
    try {
      const params = { invoice_id: before.inv.id };
      if (!exact) params.accept_difference = true;
      const { data } = await axios.post(`${API}/bank/reconcile/${tx.id}`, null, { params });
      toast.success(data.message);
      onDone();
    } catch (e) {
      const message = errMsg(e);
      // Το τιμολόγιο μπορεί να άλλαξε από τότε που ανοίχτηκε το παράθυρο (νέα εξόφληση από πελατολογικό, αλλαγή παρακράτησης):
      // ξαναδιαβάζουμε τα υπόλοιπα. Αν το οφειλόμενο του επιλεγμένου άλλαξε, ο χρήστης βλέπει τη νέα διαφορά (και το κουτί
      // αποδοχής, αν χρειάζεται) αντί για το τεχνικό μήνυμα του server.
      let changed = null;
      try {
        const fresh = await loadInvoices();
        setList(fresh.invoices);
        setTruncated(!!fresh.closedTruncated);
        const rebuilt = buildCandidates(fresh.invoices, deposit);
        const now = [...rebuilt.open, ...rebuilt.settled].find(({ inv }) => inv.id === before.inv.id);
        if (now && cents(now.owed) !== cents(before.owed)) changed = now;
      } catch {
        // Αν δεν ξαναφορτώνονται, μένει το μήνυμα του server.
      }
      if (changed) {
        setAccepted(false);
        setError(`Το οφειλόμενο του ${invoiceLabel(changed.inv)} άλλαξε από ${money(before.owed)} σε ${money(changed.owed)} ενώ το παράθυρο `
          + "ήταν ανοιχτό. Ελέγξτε τη νέα διαφορά και επιβεβαιώστε ξανά.");
      } else {
        setError(message);
      }
      setSaving(false);
    }
  };

  const renderCandidate = ({ inv, owed, gap, settled: isSettled }) => {
    const picked = inv.id === selectedId;
    const blocked = !!inv.credit_note_id;
    return (
      <button key={inv.id} type="button" aria-pressed={picked} disabled={blocked}
        onClick={() => choose(inv.id)} data-testid={`pick-invoice-${inv.id}`}
        className={`w-full text-left rounded-xl border px-4 py-3 transition-colors disabled:opacity-50 disabled:cursor-not-allowed ${
          picked ? "border-[hsl(var(--gold))] bg-[hsl(var(--gold))]/10" : "border-border/60 hover:bg-accent/40"}`}>
        <div className="flex items-center justify-between gap-3">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-mono text-sm">{invoiceLabel(inv)}</span>
              {gap === 0 && <Badge variant="default">ακριβές ποσό</Badge>}
              {isSettled && <Badge variant="outline">ήδη εξοφλημένο</Badge>}
              {tx.matched_invoice_id === inv.id && <Badge variant="secondary">πρόταση συστήματος</Badge>}
            </div>
            <div className="text-xs text-muted-foreground truncate">{inv.client_name || "—"} · {day(inv.date)}</div>
            {blocked && (
              <div className="text-xs text-[hsl(var(--destructive))] mt-0.5">
                Έχει πιστωτικό — ο συμψηφισμός γίνεται από τον λογιστή, όχι με κατάθεση.
              </div>
            )}
          </div>
          <div className="text-right shrink-0">
            <div className="font-mono text-sm">{money(owed)}</div>
            {gap !== 0 && <div className="text-xs text-muted-foreground">διαφέρει {money(Math.abs(gap) / 100)}</div>}
          </div>
        </div>
      </button>
    );
  };

  return (
    <Dialog open onOpenChange={(isOpen) => { if (!isOpen && !saving) onClose(); }}>
      <DialogContent className="max-w-2xl" data-testid="reconcile-dialog">
        <DialogHeader>
          <DialogTitle>Συμφωνία κατάθεσης με τιμολόγιο</DialogTitle>
          <DialogDescription>
            {money(tx.amount)} · {day(tx.date)} · «{tx.description}»
          </DialogDescription>
        </DialogHeader>

        <div className="relative">
          <MagnifyingGlass size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" />
          <Input value={query} onChange={(e) => setQuery(e.target.value)} className="pl-9"
            placeholder="Αναζήτηση με αριθμό τιμολογίου ή πελάτη…" data-testid="reconcile-search" />
        </div>

        <div className="max-h-72 overflow-y-auto space-y-2 pr-1">
          {open.length === 0 && (
            <div className="text-sm text-muted-foreground py-6 text-center" data-testid="reconcile-no-invoices">
              Δεν υπάρχει εκδοθέν τιμολόγιο με υπόλοιπο προς είσπραξη.
              {settled.length > 0 && " Αν το τιμολόγιο είχε σημειωθεί εξοφλημένο με το χέρι, εμφανίστε τα εξοφλημένα παρακάτω."}
            </div>
          )}
          {open.length > 0 && shownOpen.length === 0 && (
            <div className="text-sm text-muted-foreground py-6 text-center">Κανένα τιμολόγιο δεν ταιριάζει με την αναζήτηση.</div>
          )}
          {shownOpen.map(renderCandidate)}

          {settled.length > 0 && (
            <div className="pt-1 space-y-2" data-testid="reconcile-settled-section">
              <button type="button" className="text-xs underline underline-offset-4 text-muted-foreground hover:text-foreground"
                aria-expanded={showSettled} onClick={() => setShowSettled((v) => !v)} data-testid="reconcile-settled-toggle">
                {showSettled ? "Απόκρυψη" : "Εμφάνιση"} εξοφλημένων με το χέρι ({shownSettled.length})
              </button>
              {showSettled && (
                <>
                  <p className="text-xs text-muted-foreground">
                    Τιμολόγια που έχουν σημειωθεί εξοφλημένα με το χέρι και δεν τα έχει αναλάβει άλλη κατάθεση. Η κατάθεση συνδέεται
                    μαζί τους ως δικαιολογητικό· δεν αλλάζει η κατάσταση του τιμολογίου.
                    {truncated && " Εμφανίζονται μόνο τα πιο πρόσφατα εξοφλημένα τιμολόγια."}
                  </p>
                  {shownSettled.length === 0 && (
                    <div className="text-sm text-muted-foreground py-3 text-center">Κανένα εξοφλημένο τιμολόγιο δεν ταιριάζει με την αναζήτηση.</div>
                  )}
                  {shownSettled.map(renderCandidate)}
                </>
              )}
            </div>
          )}
        </div>

        {selected && (
          <div className="rounded-xl border border-border/60 p-4 space-y-3" data-testid="reconcile-summary">
            <div className="grid grid-cols-3 gap-3 text-sm">
              <div><div className="lux-overline">Κατάθεση</div><div className="font-mono">{money(deposit)}</div></div>
              <div><div className="lux-overline">Οφειλόμενο {invoiceLabel(selected.inv)}</div><div className="font-mono">{money(selected.owed)}</div></div>
              <div>
                <div className="lux-overline">Διαφορά</div>
                <div className={`font-mono ${exact ? "text-[hsl(var(--success))]" : "text-[hsl(var(--destructive))]"}`}>{money(diff)}</div>
              </div>
            </div>
            {selected.settled && (
              <p className="text-xs text-muted-foreground" data-testid="reconcile-settled-note">
                Το τιμολόγιο είναι ήδη σημειωμένο ως εξοφλημένο. Η κατάθεση θα συνδεθεί μαζί του ως δικαιολογητικό της είσπραξης·
                η κατάσταση του τιμολογίου δεν αλλάζει.
              </p>
            )}
            {!exact && (
              <label className="flex items-start gap-2 text-sm cursor-pointer">
                <Checkbox checked={accepted} onCheckedChange={(v) => setAccepted(v === true)} className="mt-0.5" data-testid="accept-difference" />
                <span>
                  {selected.settled
                    ? "Αποδέχομαι τη διαφορά και θέλω να συνδεθεί η κατάθεση με το τιμολόγιο."
                    : "Αποδέχομαι τη διαφορά και θέλω να εξοφληθεί το τιμολόγιο."}{" "}
                  <span className="text-muted-foreground">
                    {diff < 0
                      ? "Η κατάθεση είναι μικρότερη από το οφειλόμενο (π.χ. προμήθεια τράπεζας ή παρακράτηση)· η διαφορά καταγράφεται στην κίνηση."
                      : "Η κατάθεση είναι μεγαλύτερη από το οφειλόμενο· το επιπλέον ποσό δεν καταχωρείται πουθενά αυτόματα (πιθανή προκαταβολή ή υπερπληρωμή) — η διαφορά καταγράφεται στην κίνηση."}
                  </span>
                </span>
              </label>
            )}
          </div>
        )}

        {error && (
          <div role="alert" className="text-sm rounded-xl border border-[hsl(var(--destructive))]/40 bg-[hsl(var(--destructive))]/5 p-3 text-[hsl(var(--destructive))]"
            data-testid="reconcile-error">{error}</div>
        )}

        <DialogFooter className="gap-2 sm:gap-0">
          <Button variant="ghost" onClick={onClose} disabled={saving} data-testid="reconcile-cancel">Άκυρο</Button>
          <Button onClick={confirm} disabled={!canConfirm} data-testid="reconcile-confirm">
            {saving ? "Συμφωνία…" : (selected?.settled ? "Σύνδεση κατάθεσης" : "Συμφωνία και εξόφληση")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export default function Bank() {
  const [filter, setFilter] = useState("action");
  const [rows, setRows] = useState(null);
  const [loadError, setLoadError] = useState("");
  const [summary, setSummary] = useState(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [bank, setBank] = useState("Άλλη");
  const [importing, setImporting] = useState(false);
  const [importResult, setImportResult] = useState(null);
  const [confirming, setConfirming] = useState(false);
  const [confirmResult, setConfirmResult] = useState(null);
  const [busyId, setBusyId] = useState(null);
  const [reconciling, setReconciling] = useState(null);
  const fileRef = useRef(null);

  const reload = () => setReloadKey((k) => k + 1);

  // Μία φόρτωση ανά αλλαγή φίλτρου ή ρητή ανανέωση. Μια απάντηση που καθυστέρησε και αφορά προηγούμενο φίλτρο
  // δεν πρέπει να γράψει πάνω στη λίστα που βλέπει πια ο χρήστης.
  useEffect(() => {
    let stale = false;
    const statuses = FILTERS.find((f) => f.key === filter).statuses;
    setLoadError("");
    Promise.all([
      axios.get(`${API}/bank/summary`),
      ...statuses.map((status) => axios.get(`${API}/bank/transactions`, { params: { status } })),
    ]).then(([sum, ...lists]) => {
      if (stale) return;
      setSummary(sum.data);
      setRows(lists.flatMap((r) => r.data).sort(byDateDesc));
    }).catch((e) => {
      if (stale) return;
      // Όχι «κενή λίστα»: αν δεν φόρτωσαν οι κινήσεις, η οθόνη το λέει και προσφέρει επανάληψη — δεν υπονοεί ότι δεν υπάρχουν.
      const message = errMsg(e);
      toast.error(message);
      setLoadError(message);
    });
    return () => { stale = true; };
  }, [filter, reloadKey]);

  const changeFilter = (key) => {
    if (key === filter) return;
    setRows(null);
    setFilter(key);
  };

  const importFile = async (file) => {
    setImporting(true); setImportResult(null);
    try {
      const fd = new FormData();
      fd.append("file", file);
      const { data } = await axios.post(`${API}/bank/import`, fd, { params: { bank }, headers: { "Content-Type": "multipart/form-data" } });
      setImportResult(data);
      // Οι νέες κινήσεις είναι «χωρίς αντιστοίχιση»: αν ο χρήστης κοιτούσε άλλη προβολή, τον πηγαίνουμε εκεί που θα τις δει.
      if (filter === "done" || filter === "ignored") changeFilter("action"); else reload();
    } catch (e) {
      toast.error(errMsg(e));
    } finally {
      setImporting(false);
    }
  };

  const onPickFile = (e) => {
    const file = e.target.files?.[0];
    e.target.value = "";                       // να μπορεί να ξαναεπιλεγεί το ίδιο αρχείο
    if (file) importFile(file);
  };

  const confirmAll = async () => {
    if (!window.confirm(
      "Θα επιβεβαιωθούν οι ασφαλείς προτάσεις και τα αντίστοιχα τιμολόγια θα εξοφληθούν.\n\n"
      + "Μαζικά περνούν μόνο προτάσεις με ακριβές ποσό και ένα μόνο πιθανό τιμολόγιο· "
      + "οι υπόλοιπες μένουν για ατομική απόφαση.\n\nΣυνέχεια;")) return;
    setConfirming(true); setConfirmResult(null);
    try {
      const { data } = await axios.post(`${API}/bank/confirm-auto-matches`);
      const names = Object.fromEntries((rows || []).map((r) => [r.id, r.description]));
      setConfirmResult({ ...data, names });
      reload();
    } catch (e) {
      toast.error(errMsg(e));
    } finally {
      setConfirming(false);
    }
  };

  // Φρέσκα υπόλοιπα τη στιγμή της συμφωνίας· ό,τι εξοφλήθηκε στο μεταξύ δεν πρέπει να προσφέρεται. Η κεφαλίδα
  // X-Closed-Truncated δηλώνει ότι υπάρχουν και παλαιότερα εξοφλημένα που δεν περιλαμβάνονται στη λίστα.
  const loadInvoices = async () => {
    const res = await axios.get(`${API}/invoices`);
    return { invoices: res.data, closedTruncated: res.headers?.["x-closed-truncated"] === "1" };
  };

  const openReconcile = async (tx) => {
    setBusyId(tx.id);
    try {
      const { invoices, closedTruncated } = await loadInvoices();
      setReconciling({ tx, invoices, closedTruncated });
    } catch (e) {
      toast.error(errMsg(e));
    } finally {
      setBusyId(null);
    }
  };

  const ignore = async (tx) => {
    if (!window.confirm(
      `Να αγνοηθεί η κίνηση «${tx.description}» (${money(tx.amount)}) της ${day(tx.date)};\n\n`
      + "Δεν θα εμφανίζεται πια στις κινήσεις που χρειάζονται ενέργεια και δεν θα συνδεθεί με τιμολόγιο.")) return;
    setBusyId(tx.id);
    try {
      const { data } = await axios.post(`${API}/bank/reconcile/${tx.id}`, null, { params: { ignore: true } });
      toast.success(data.message);
      reload();
    } catch (e) {
      toast.error(errMsg(e));
    } finally {
      setBusyId(null);
    }
  };

  const proposals = summary?.auto_matched || 0;
  const done = (summary?.manual_matched || 0) + (summary?.auto_confirmed || 0);
  // Όσο η σύνοψη δεν έχει φορτώσει (ή απέτυχε), τα σύνολα είναι άγνωστα — «—», όχι «0» που θα έδειχνε ότι όλα έχουν τακτοποιηθεί.
  const known = (value) => (summary ? value : "—");
  const kpis = [
    { id: "review", label: "Προτάσεις προς έλεγχο", value: known(proposals), hint: "Αυτόματες συνδέσεις που περιμένουν επιβεβαίωση" },
    { id: "unmatched", label: "Χωρίς αντιστοίχιση", value: known(summary?.unmatched || 0), hint: "Εισπράξεις και χρεώσεις χωρίς τιμολόγιο" },
    { id: "done", label: "Συμφωνημένες", value: known(done), hint: "Συνδεδεμένες με τιμολόγιο" },
    { id: "ignored", label: "Αγνοημένες", value: known(summary?.ignored || 0), hint: "Δεν αφορούν τιμολόγια" },
  ];
  const total = viewTotal(filter, summary);
  const hiddenRows = rows && total !== null ? Math.max(0, total - rows.length) : 0;

  return (
    <div className="space-y-6" data-testid="bank-page">
      <div>
        <div className="lux-overline mb-1.5">Nomos One · Οικονομικά</div>
        <h1 className="font-brand text-4xl font-bold tracking-wide">Τράπεζα</h1>
        <p className="text-sm text-muted-foreground mt-1 max-w-3xl">
          Εισάγετε το αρχείο κινήσεων του λογαριασμού και συμφωνήστε κάθε είσπραξη με το τιμολόγιο που εξοφλεί.
          Κάθε συμφωνία εξοφλεί το τιμολόγιο. Με ενεργό Autopilot, μόνο οι προτάσεις με ακριβές ποσό, ένα μόνο πιθανό
          τιμολόγιο και υψηλή βεβαιότητα επιβεβαιώνονται αυτόματα — οι υπόλοιπες περιμένουν εδώ για δική σας απόφαση.
        </p>
      </div>

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        {kpis.map((k) => (
          <div key={k.id} className="lux-card p-5" data-testid={`bank-kpi-${k.id}`}>
            <span className="lux-overline">{k.label}</span>
            <div className="mt-2 font-mono text-3xl font-semibold">{k.value}</div>
            <div className="text-xs text-muted-foreground mt-1">{k.hint}</div>
          </div>
        ))}
      </div>

      <div className="lux-card p-5 space-y-4">
        <div className="flex flex-wrap items-end gap-3">
          <div className="w-48">
            <Label>Τράπεζα</Label>
            <Select value={bank} onValueChange={setBank}>
              <SelectTrigger className="mt-1.5" data-testid="bank-name-select"><SelectValue /></SelectTrigger>
              <SelectContent>
                {BANKS.map((b) => <SelectItem key={b} value={b}>{b}</SelectItem>)}
              </SelectContent>
            </Select>
          </div>
          <input ref={fileRef} type="file" accept=".csv,.txt,text/csv" className="hidden" onChange={onPickFile} data-testid="bank-import-input" />
          <Button onClick={() => fileRef.current?.click()} disabled={importing} data-testid="bank-import-button">
            <UploadSimple size={16} className="mr-1.5" /> {importing ? "Εισαγωγή…" : "Εισαγωγή αρχείου κινήσεων (CSV)"}
          </Button>
          {proposals > 0 && (
            <Button variant="outline" onClick={confirmAll} disabled={confirming} data-testid="bank-confirm-all">
              <LinkSimple size={16} className="mr-1.5" /> {confirming ? "Επιβεβαίωση…" : `Επιβεβαίωση ασφαλών προτάσεων (${proposals})`}
            </Button>
          )}
        </div>
        <p className="text-xs text-muted-foreground">
          Από το e-banking εξάγετε τις κινήσεις σε CSV με στήλες Ημερομηνία, Αιτιολογία και Ποσό (ή Πίστωση / Χρέωση).
          Κινήσεις που έχουν ήδη εισαχθεί δεν καταχωρούνται ξανά.
        </p>

        {importResult && (
          <div className="text-sm p-3 rounded-xl bg-[hsl(var(--success))]/5 border border-[hsl(var(--success))]/25" data-testid="bank-import-result">
            <div className="flex items-start justify-between gap-3">
              <div className="text-[hsl(var(--success))]">✓ {importResult.message}</div>
              <button type="button" className="text-xs text-muted-foreground hover:underline shrink-0" onClick={() => setImportResult(null)}>Κλείσιμο</button>
            </div>
            {importResult.skipped > 0 && (
              <div className="text-xs text-muted-foreground mt-1">
                {`${importResult.skipped} υπήρχαν ήδη και δεν εισήχθησαν ξανά.`}
              </div>
            )}
          </div>
        )}

        {confirmResult && (
          <div className="text-sm p-3 rounded-xl bg-[hsl(var(--success))]/5 border border-[hsl(var(--success))]/25" data-testid="bank-confirm-result">
            <div className="flex items-start justify-between gap-3">
              <div className="text-[hsl(var(--success))]">✓ {confirmResult.message}</div>
              <button type="button" className="text-xs text-muted-foreground hover:underline shrink-0" onClick={() => setConfirmResult(null)}>Κλείσιμο</button>
            </div>
            {(confirmResult.skipped_items || []).length > 0 && (
              <ul className="mt-2 space-y-1 text-xs text-muted-foreground">
                {confirmResult.skipped_items.map((item) => (
                  <li key={item.id} data-testid="bank-confirm-skipped">
                    · {confirmResult.names?.[item.id] ? `«${confirmResult.names[item.id]}»: ` : ""}{item.message}
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </div>

      <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Προβολή κινήσεων">
        {FILTERS.map((f) => (
          <Button key={f.key} size="sm" variant={filter === f.key ? "default" : "outline"} aria-pressed={filter === f.key}
            onClick={() => changeFilter(f.key)} data-testid={`bank-filter-${f.key}`}>
            {f.label}
          </Button>
        ))}
      </div>

      <div className="lux-card overflow-hidden">
        <div className="px-4 py-3 border-b border-border/60 flex items-center gap-2">
          <BankIcon size={18} className="text-[hsl(var(--gold))]" />
          <span className="font-heading text-sm">Κινήσεις λογαριασμού</span>
        </div>
        {loadError && (
          <div role="alert" className="px-4 py-3 border-b border-[hsl(var(--destructive))]/30 bg-[hsl(var(--destructive))]/5 text-sm flex flex-wrap items-center justify-between gap-3"
            data-testid="bank-load-error">
            <span className="text-[hsl(var(--destructive))]">
              Δεν φορτώθηκαν οι κινήσεις: {loadError}{rows && rows.length > 0 ? " Η λίστα που βλέπετε μπορεί να μην είναι ενημερωμένη." : ""}
            </span>
            <Button size="sm" variant="outline" onClick={reload} data-testid="bank-retry">Επανάληψη</Button>
          </div>
        )}
        {rows === null ? (
          loadError ? null : <div className="p-8 text-center text-muted-foreground">Φόρτωση κινήσεων…</div>
        ) : rows.length === 0 ? (
          loadError ? null : <div className="p-8 text-center text-muted-foreground" data-testid="bank-empty">
            {(summary?.total || 0) === 0
              ? "Δεν υπάρχουν κινήσεις. Εισάγετε το αρχείο κινήσεων από την τράπεζά σας για να ξεκινήσει η συμφωνία."
              : filter === "action"
                ? "Δεν υπάρχουν κινήσεις που χρειάζονται ενέργεια — όλες έχουν συμφωνηθεί ή αγνοηθεί."
                : "Δεν υπάρχουν κινήσεις σε αυτή την προβολή."}
          </div>
        ) : (
          <Table data-testid="bank-table">
            <TableHeader>
              <TableRow>
                <TableHead>Ημερομηνία</TableHead>
                <TableHead>Αιτιολογία</TableHead>
                <TableHead className="text-right">Ποσό</TableHead>
                <TableHead>Αντιστοίχιση</TableHead>
                <TableHead>Κατάσταση</TableHead>
                <TableHead className="text-right">Ενέργειες</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((tx) => {
                const debit = !isCredit(tx);
                const busy = busyId === tx.id;
                return (
                  <TableRow key={tx.id} data-testid={`bank-row-${tx.id}`}>
                    <TableCell className="font-mono text-xs whitespace-nowrap">{day(tx.date)}</TableCell>
                    <TableCell className="text-sm max-w-md">
                      <div className="line-clamp-2" title={tx.description}>{tx.description}</div>
                      <div className="text-xs text-muted-foreground">{[tx.bank, tx.reference].filter(Boolean).join(" · ")}</div>
                    </TableCell>
                    <TableCell className={`text-right font-mono whitespace-nowrap ${debit ? "text-[hsl(var(--destructive))]" : "text-[hsl(var(--success))]"}`}>
                      {money(tx.amount)}
                    </TableCell>
                    <TableCell><MatchCell tx={tx} /></TableCell>
                    <TableCell>
                      <Badge variant={STATUS_VARIANT[tx.match_status] || "outline"}>
                        {debit && tx.match_status === "unmatched" ? "Χρέωση" : (STATUS_LABEL[tx.match_status] || tx.match_status)}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-right whitespace-nowrap">
                      {isOpen(tx) && (
                        <div className="flex justify-end gap-2">
                          {isCredit(tx) && (
                            <Button size="sm" variant="outline" disabled={busy} onClick={() => openReconcile(tx)} data-testid={`bank-reconcile-${tx.id}`}>
                              <LinkSimple size={14} className="mr-1" /> Συμφωνία
                            </Button>
                          )}
                          <Button size="sm" variant="ghost" disabled={busy} onClick={() => ignore(tx)} data-testid={`bank-ignore-${tx.id}`}>
                            <Prohibit size={14} className="mr-1" /> Αγνόηση
                          </Button>
                        </div>
                      )}
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        )}
        {hiddenRows > 0 && (
          <div className="px-4 py-3 border-t border-border/60 text-xs text-muted-foreground" data-testid="bank-truncated">
            {truncationNotice(filter, rows.length, total)}
          </div>
        )}
      </div>

      {reconciling && (
        <ReconcileDialog
          tx={reconciling.tx}
          invoices={reconciling.invoices}
          closedTruncated={reconciling.closedTruncated}
          loadInvoices={loadInvoices}
          onClose={() => setReconciling(null)}
          onDone={() => { setReconciling(null); reload(); }}
        />
      )}
    </div>
  );
}
