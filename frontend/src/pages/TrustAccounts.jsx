import { useEffect, useState } from "react";
import axios, { API, money, dateFmt, errMsg } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog";
import { Badge } from "@/components/ui/badge";
import { Vault, ArrowCircleDown, ArrowCircleUp, Receipt, Plus, User, Info, Warning } from "@phosphor-icons/react";
import { toast } from "sonner";
import { todayAthens } from "@/lib/utils";

const TYPE_LABEL = {
  deposit: "Κατάθεση",
  withdrawal: "Ανάληψη",
  apply_to_invoice: "Εξόφληση τιμολογίου",
  refund: "Επιστροφή στον πελάτη",
};
const TYPE_COLOR = {
  deposit: "text-[hsl(var(--success))]",
  withdrawal: "text-[hsl(var(--destructive))]",
  apply_to_invoice: "text-[hsl(var(--gold))]",
  refund: "text-[hsl(var(--destructive))]",
};

export default function TrustAccounts() {
  const [accounts, setAccounts] = useState([]);
  const [summary, setSummary] = useState({ total_balance: 0, accounts_with_balance: 0 });
  const [clients, setClients] = useState([]);
  const [selected, setSelected] = useState(null);
  const [detail, setDetail] = useState(null);
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({
    client_id: "", type: "deposit", amount: 0,
    date: todayAthens(),
    description: "", reference: "", invoice_id: "",
  });
  const [clientInvoices, setClientInvoices] = useState([]);

  const load = async () => {
    try {
      const [a, s, c] = await Promise.all([
        axios.get(`${API}/trust/accounts`),
        axios.get(`${API}/trust/summary`),
        axios.get(`${API}/clients`),
      ]);
      setAccounts(a.data);
      setSummary(s.data);
      setClients(c.data);
    } catch (e) {
      toast.error(errMsg(e));
    }
  };

  useEffect(() => { load(); }, []);

  const openDetail = async (clientId) => {
    try {
      const { data } = await axios.get(`${API}/trust/accounts/${clientId}`);
      setSelected(clientId);
      setDetail(data);
    } catch (e) {
      toast.error(errMsg(e));
    }
  };

  // Ανοιχτά τιμολόγια του πελάτη με το ποσό που οφείλεται πραγματικά (μετά την παρακράτηση
  // και ό,τι έχει ήδη εξοφληθεί από τον λογαριασμό) — υπολογίζεται στον server.
  const loadOpenInvoices = async (clientId) => {
    try {
      const { data } = await axios.get(`${API}/trust/accounts/${clientId}/open-invoices`);
      setClientInvoices(data);
    } catch { setClientInvoices([]); }
  };

  const openNew = async (clientId) => {
    setForm({
      client_id: clientId || "",
      type: "deposit", amount: 0,
      date: todayAthens(),
      description: "", reference: "", invoice_id: "",
    });
    if (clientId) await loadOpenInvoices(clientId);
    else setClientInvoices([]);
    setOpen(true);
  };

  const handleClientChange = async (cid) => {
    setForm({ ...form, client_id: cid, invoice_id: "" });
    await loadOpenInvoices(cid);
  };

  // Επιλογή τιμολογίου: προτείνεται το μεγαλύτερο ποσό που επιτρέπεται (οφειλόμενο ή διαθέσιμο υπόλοιπο).
  const handleInvoiceChange = (invoiceId) => {
    const invoice = clientInvoices.find((i) => i.id === invoiceId);
    const account = accounts.find((a) => a.client_id === form.client_id);
    const amount = invoice
      ? Math.round(Math.min(invoice.outstanding, account ? account.balance : 0) * 100) / 100
      : form.amount;
    setForm({ ...form, invoice_id: invoiceId, amount });
  };

  const handleSubmit = async () => {
    if (!form.client_id) return toast.error("Επιλέξτε πελάτη");
    if (!form.amount || form.amount <= 0) return toast.error("Δώστε ποσό μεγαλύτερο του μηδενός");
    try {
      const payload = { ...form, amount: Number(form.amount) };
      if (payload.type !== "apply_to_invoice") payload.invoice_id = null;
      const { data } = await axios.post(`${API}/trust/transactions`, payload);
      toast.success(data?.message || "Η κίνηση καταχωρήθηκε");
      setOpen(false);
      await load();
      if (selected) await openDetail(selected);
    } catch (e) {
      toast.error(errMsg(e));
    }
  };

  return (
    <div className="space-y-8" data-testid="trust-page">
      <div className="flex items-start justify-between">
        <div>
          <div className="lux-overline mb-1.5">Nomos One · Οικονομικά</div>
          <h1 className="font-brand text-4xl font-bold tracking-wide">Πελατολογικοί Λογαριασμοί</h1>
          <p className="text-sm text-muted-foreground mt-1">
            Χρήματα πελατών σε ξεχωριστή διαχείριση — Ν.4194/2013 άρθρο 32
          </p>
        </div>
        <Button onClick={() => openNew()} data-testid="new-trust-tx-button"><Plus size={16} className="mr-1.5" /> Νέα Κίνηση</Button>
      </div>

      <div className="border border-[hsl(var(--gold))]/20 bg-[hsl(var(--gold))]/5 rounded-xl p-4 text-xs text-muted-foreground flex gap-3">
        <Info size={18} className="shrink-0 text-[hsl(var(--gold))]" />
        <div>
          Ο δικηγόρος υποχρεούται (Ν.4194/2013 άρθρο 32) να τηρεί ξεχωριστό λογαριασμό για χρήματα των πελατών του
          (προκαταβολές, εγγυοδοσίες, εισπραχθέντα από αντιδίκους). Εδώ καταχωρούνται και παρακολουθούνται με πλήρες ledger.
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div className="lux-card p-6">
          <div className="lux-overline">Συνολικά κρατούμενα</div>
          <div className="mt-2 font-mono text-4xl font-semibold text-[hsl(var(--gold))]" data-testid="trust-total-balance">{money(summary.total_balance)}</div>
          <div className="text-xs text-muted-foreground mt-1">Χρήματα πελατών σε αναμονή διαχείρισης</div>
        </div>
        <div className="lux-card p-6">
          <div className="lux-overline">Ενεργοί λογαριασμοί</div>
          <div className="mt-2 font-mono text-4xl font-semibold" data-testid="trust-active-count">{summary.accounts_with_balance}</div>
          <div className="text-xs text-muted-foreground mt-1">Πελάτες με τρέχον υπόλοιπο</div>
        </div>
      </div>

      <div className="lux-card overflow-hidden">
        <div className="px-4 py-3 border-b border-border/60 flex items-center gap-2">
          <Vault size={18} className="text-[hsl(var(--gold))]" />
          <span className="font-heading text-sm">Λογαριασμοί ανά πελάτη</span>
        </div>
        <Table data-testid="trust-accounts-table">
          <TableHeader>
            <TableRow>
              <TableHead>Πελάτης</TableHead>
              <TableHead className="text-right">Καταθέσεις</TableHead>
              <TableHead className="text-right">Αναλήψεις</TableHead>
              <TableHead className="text-right">Υπόλοιπο</TableHead>
              <TableHead className="text-right">Ενέργειες</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {accounts.map((a) => (
              <TableRow key={a.id} data-testid={`trust-row-${a.client_id}`}>
                <TableCell className="flex items-center gap-2"><User size={14} className="text-muted-foreground" /> {a.client_name}</TableCell>
                <TableCell className="text-right font-mono text-[hsl(var(--success))]">{money(a.total_deposits)}</TableCell>
                <TableCell className="text-right font-mono text-[hsl(var(--destructive))]">{money(a.total_withdrawals)}</TableCell>
                <TableCell className="text-right font-mono font-semibold" data-testid={`trust-balance-${a.client_id}`}>{money(a.balance)}</TableCell>
                <TableCell className="text-right">
                  <Button size="sm" variant="ghost" onClick={() => openDetail(a.client_id)} data-testid={`view-trust-${a.client_id}`}>Ιστορικό</Button>
                  <Button size="sm" variant="outline" onClick={() => openNew(a.client_id)} data-testid={`add-tx-${a.client_id}`}>+ Κίνηση</Button>
                </TableCell>
              </TableRow>
            ))}
            {accounts.length === 0 && (
              <TableRow><TableCell colSpan={5} className="text-center text-muted-foreground py-8">
                Δεν υπάρχουν κινήσεις ακόμα. Πατήστε «Νέα Κίνηση» για την πρώτη καταχώρηση.
              </TableCell></TableRow>
            )}
          </TableBody>
        </Table>
      </div>

      {/* Detail dialog */}
      <Dialog open={!!detail} onOpenChange={(v) => !v && setDetail(null)}>
        <DialogContent className="max-w-3xl max-h-[85vh] overflow-y-auto" aria-describedby={undefined} data-testid="trust-detail-dialog">
          <DialogHeader>
            <DialogTitle>Πελατολογικός Λογαριασμός — {detail?.account.client_name}</DialogTitle>
          </DialogHeader>
          {detail && (
            <>
              {detail.ledger_check && !detail.ledger_check.consistent && (
                <div className="flex gap-2 rounded-lg border border-[hsl(var(--destructive))]/40 bg-[hsl(var(--destructive))]/10 p-3 text-xs" data-testid="trust-ledger-warning">
                  <Warning size={16} className="shrink-0 text-[hsl(var(--destructive))]" />
                  <div>
                    Το υπόλοιπο του λογαριασμού ({money(detail.account.balance)}) δεν συμφωνεί με το άθροισμα των κινήσεων
                    ({money(detail.ledger_check.ledger_balance)}). Ελέγξτε το ιστορικό πριν προχωρήσετε σε νέες κινήσεις.
                  </div>
                </div>
              )}
              <div className="grid grid-cols-3 gap-3 text-sm">
                <div className="lux-card p-3">
                  <div className="text-xs text-muted-foreground">Καταθέσεις</div>
                  <div className="font-mono text-lg text-[hsl(var(--success))]">{money(detail.account.total_deposits)}</div>
                </div>
                <div className="lux-card p-3">
                  <div className="text-xs text-muted-foreground">Αναλήψεις</div>
                  <div className="font-mono text-lg text-[hsl(var(--destructive))]">{money(detail.account.total_withdrawals)}</div>
                </div>
                <div className="lux-card p-3">
                  <div className="text-xs text-muted-foreground">Υπόλοιπο</div>
                  <div className="font-mono text-lg font-semibold text-[hsl(var(--gold))]">{money(detail.account.balance)}</div>
                </div>
              </div>

              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Ημ/νία</TableHead>
                    <TableHead>Τύπος</TableHead>
                    <TableHead>Περιγραφή</TableHead>
                    <TableHead className="text-right">Ποσό</TableHead>
                    <TableHead className="text-right">Υπόλοιπο</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {detail.transactions.map((t) => (
                    <TableRow key={t.id} data-testid={`trust-tx-${t.id}`}>
                      <TableCell className="font-mono text-xs">{dateFmt(t.date)}</TableCell>
                      <TableCell><Badge variant="secondary" className={TYPE_COLOR[t.type]}>{TYPE_LABEL[t.type]}</Badge></TableCell>
                      <TableCell className="text-xs">
                        {t.description}
                        {t.invoice_number && <span className="ml-1 text-muted-foreground">· {t.invoice_number}</span>}
                        {t.reference && <span className="ml-1 text-muted-foreground">· {t.reference}</span>}
                      </TableCell>
                      <TableCell className={`text-right font-mono ${TYPE_COLOR[t.type]}`}>
                        {t.type === "deposit" ? "+" : "−"}{money(t.amount)}
                      </TableCell>
                      <TableCell className="text-right font-mono">{money(t.balance_after)}</TableCell>
                    </TableRow>
                  ))}
                  {detail.transactions.length === 0 && (
                    <TableRow><TableCell colSpan={5} className="text-center text-muted-foreground py-6">Δεν υπάρχουν κινήσεις.</TableCell></TableRow>
                  )}
                </TableBody>
              </Table>
              <Button onClick={() => { setDetail(null); openNew(detail.account.client_id); }} className="w-full" data-testid="detail-new-tx-button">
                <Plus size={14} className="mr-1.5" /> Νέα κίνηση για {detail.account.client_name}
              </Button>
            </>
          )}
        </DialogContent>
      </Dialog>

      {/* New transaction dialog */}
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-w-lg" aria-describedby={undefined} data-testid="trust-new-dialog">
          <DialogHeader><DialogTitle>Νέα κίνηση πελατολογικού λογαριασμού</DialogTitle></DialogHeader>
          <div>
            <Label>Πελάτης</Label>
            <Select value={form.client_id} onValueChange={handleClientChange}>
              <SelectTrigger className="mt-1.5" data-testid="trust-client-select"><SelectValue placeholder="Επιλέξτε πελάτη" /></SelectTrigger>
              <SelectContent>
                {clients.map((c) => <SelectItem key={c.id} value={c.id}>{c.name}</SelectItem>)}
              </SelectContent>
            </Select>
          </div>
          <div>
            <Label>Τύπος κίνησης</Label>
            <Select value={form.type} onValueChange={(v) => setForm({ ...form, type: v })}>
              <SelectTrigger className="mt-1.5" data-testid="trust-type-select"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="deposit">Κατάθεση (+)</SelectItem>
                <SelectItem value="withdrawal">Ανάληψη (−)</SelectItem>
                <SelectItem value="apply_to_invoice">Εξόφληση τιμολογίου (−)</SelectItem>
                <SelectItem value="refund">Επιστροφή στον πελάτη (−)</SelectItem>
              </SelectContent>
            </Select>
          </div>
          {form.type === "apply_to_invoice" && (
            <div>
              <Label>Ανεξόφλητο τιμολόγιο πελάτη</Label>
              <Select value={form.invoice_id} onValueChange={handleInvoiceChange}>
                <SelectTrigger className="mt-1.5" data-testid="trust-invoice-select"><SelectValue placeholder="Επιλέξτε τιμολόγιο" /></SelectTrigger>
                <SelectContent>
                  {clientInvoices.map((i) => (
                    <SelectItem key={i.id} value={i.id}>{i.number} — οφείλονται {money(i.outstanding)}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {clientInvoices.length === 0 && form.client_id && <p className="text-xs text-muted-foreground mt-1">Δεν υπάρχουν ανεξόφλητα τιμολόγια για τον πελάτη.</p>}
            </div>
          )}
          <div className="grid grid-cols-2 gap-3">
            <div>
              <Label>Ποσό (€)</Label>
              <Input type="number" step="0.01" value={form.amount} onChange={(e) => setForm({ ...form, amount: e.target.value })} className="mt-1.5 font-mono" data-testid="trust-amount-input" />
            </div>
            <div>
              <Label>Ημερομηνία</Label>
              <Input type="date" value={form.date} onChange={(e) => setForm({ ...form, date: e.target.value })} className="mt-1.5" />
            </div>
          </div>
          <div>
            <Label>Περιγραφή</Label>
            <Textarea value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} placeholder="π.χ. Προκαταβολή για δικαστικά έξοδα" data-testid="trust-description-input" />
          </div>
          <div>
            <Label>Αναφορά (αρ. επιταγής / συναλλαγής)</Label>
            <Input value={form.reference} onChange={(e) => setForm({ ...form, reference: e.target.value })} className="mt-1.5" />
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setOpen(false)}>Ακύρωση</Button>
            <Button onClick={handleSubmit} data-testid="save-trust-tx-button">Καταχώρηση</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
