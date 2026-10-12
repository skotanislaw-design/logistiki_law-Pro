import { useEffect, useState } from "react";
import axios, { API, errMsg } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog";
import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import { ShieldCheck, Warning, User, CheckCircle } from "@phosphor-icons/react";
import { toast } from "sonner";

const RISK_STYLE = {
  low: "text-[hsl(var(--success))] border-[hsl(var(--success))]/40",
  medium: "text-[hsl(var(--gold))] border-[hsl(var(--gold))]/40",
  high: "text-[hsl(var(--destructive))] border-[hsl(var(--destructive))]/40",
};
const RISK_LABEL = { low: "Χαμηλός", medium: "Μεσαίος", high: "Υψηλός" };

const emptyKyc = (clientId = "", clientName = "") => ({
  client_id: clientId, client_name: clientName,
  id_type: "id_card", id_number: "", id_issue_date: "", id_issuer: "",
  birth_date: "", nationality: "Ελληνική", profession: "",
  pep: false, high_risk_country: false,
  source_of_funds: "", purpose_of_business: "", notes: "",
});

export default function Kyc() {
  const [status, setStatus] = useState(null);
  const [records, setRecords] = useState([]);
  const [clients, setClients] = useState([]);
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState(emptyKyc());

  const load = async () => {
    try {
      const [s, r, c] = await Promise.all([
        axios.get(`${API}/kyc/status`),
        axios.get(`${API}/kyc/records`),
        axios.get(`${API}/clients`),
      ]);
      setStatus(s.data);
      setRecords(r.data);
      setClients(c.data);
    } catch (e) { toast.error(errMsg(e)); }
  };
  useEffect(() => { load(); }, []);

  const openNew = (clientId = "", clientName = "") => {
    setForm(emptyKyc(clientId, clientName));
    setOpen(true);
  };

  const openEdit = async (clientId) => {
    try {
      const { data } = await axios.get(`${API}/kyc/records/${clientId}`);
      if (data) setForm(data);
      else {
        const c = clients.find((x) => x.id === clientId);
        setForm(emptyKyc(clientId, c?.name || ""));
      }
      setOpen(true);
    } catch (e) { toast.error(errMsg(e)); }
  };

  const submit = async () => {
    try {
      await axios.post(`${API}/kyc/records`, form);
      toast.success("Η καρτέλα KYC αποθηκεύτηκε");
      setOpen(false);
      load();
    } catch (e) { toast.error(errMsg(e)); }
  };

  const verify = async (clientId) => {
    try {
      await axios.post(`${API}/kyc/records/${clientId}/verify`);
      toast.success("Ο πελάτης πιστοποιήθηκε");
      load();
    } catch (e) { toast.error(errMsg(e)); }
  };

  const clientsWithoutKyc = clients.filter((c) => !records.find((r) => r.client_id === c.id));

  return (
    <div className="space-y-8" data-testid="kyc-page">
      <div className="flex items-start justify-between">
        <div>
          <div className="lux-overline mb-1.5">Nomos One · Συμμόρφωση</div>
          <h1 className="font-brand text-4xl font-bold tracking-wide">KYC / AML</h1>
          <p className="text-sm text-muted-foreground mt-1">Δέουσα επιμέλεια πελάτη — Ν.4557/2018</p>
        </div>
      </div>

      <div className="border border-[hsl(var(--gold))]/25 bg-[hsl(var(--gold))]/5 rounded-xl p-4 text-xs text-muted-foreground flex gap-3">
        <ShieldCheck size={18} className="shrink-0 text-[hsl(var(--gold))]" />
        <div>
          Ο δικηγόρος υπόκειται στον Ν.4557/2018 για την πρόληψη ξεπλύματος χρήματος. Για κάθε νέο πελάτη απαιτείται
          ταυτοποίηση, αξιολόγηση κινδύνου, δήλωση πηγής πόρων και τεκμηρίωση σκοπού συνεργασίας.
        </div>
      </div>

      {status && (
        <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
          <div className="lux-card p-5">
            <div className="lux-overline">Πελάτες</div>
            <div className="mt-2 font-mono text-3xl font-semibold" data-testid="kyc-total-clients">{status.total_clients}</div>
            <div className="text-xs text-muted-foreground mt-1">Σύνολο στο σύστημα</div>
          </div>
          <div className="lux-card p-5">
            <div className="lux-overline">KYC Συμπληρωμένα</div>
            <div className="mt-2 font-mono text-3xl font-semibold text-[hsl(var(--success))]" data-testid="kyc-completed">{status.kyc_completed}</div>
            <div className="text-xs text-muted-foreground mt-1">{status.kyc_verified} πιστοποιημένα</div>
          </div>
          <div className="lux-card p-5">
            <div className="lux-overline">Εκκρεμούν</div>
            <div className="mt-2 font-mono text-3xl font-semibold text-[hsl(var(--gold))]" data-testid="kyc-missing">{status.kyc_missing}</div>
            <div className="text-xs text-muted-foreground mt-1">Χωρίς καρτέλα KYC</div>
          </div>
          <div className="lux-card p-5">
            <div className="lux-overline">Υψηλού Κινδύνου</div>
            <div className="mt-2 font-mono text-3xl font-semibold text-[hsl(var(--destructive))]" data-testid="kyc-high-risk">{status.high_risk_count}</div>
            <div className="text-xs text-muted-foreground mt-1">Απαιτούν ενισχυμένη επιμέλεια</div>
          </div>
        </div>
      )}

      {clientsWithoutKyc.length > 0 && (
        <div className="lux-card p-5">
          <div className="flex items-center gap-2 mb-3">
            <Warning size={18} className="text-[hsl(var(--gold))]" />
            <span className="font-heading text-sm">Πελάτες χωρίς KYC ({clientsWithoutKyc.length})</span>
          </div>
          <div className="flex flex-wrap gap-2">
            {clientsWithoutKyc.slice(0, 8).map((c) => (
              <Button key={c.id} size="sm" variant="outline" onClick={() => openNew(c.id, c.name)} data-testid={`create-kyc-${c.id}`}>
                <User size={14} className="mr-1" /> {c.name}
              </Button>
            ))}
          </div>
        </div>
      )}

      <div className="lux-card overflow-hidden">
        <Table data-testid="kyc-table">
          <TableHeader>
            <TableRow>
              <TableHead>Πελάτης</TableHead>
              <TableHead>Ταυτοποίηση</TableHead>
              <TableHead>Κίνδυνος</TableHead>
              <TableHead>Ένδειξη PEP</TableHead>
              <TableHead>Κατάσταση</TableHead>
              <TableHead className="text-right">Ενέργειες</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {records.map((r) => (
              <TableRow key={r.id} data-testid={`kyc-row-${r.client_id}`}>
                <TableCell>{r.client_name}</TableCell>
                <TableCell className="text-xs font-mono">{r.id_number || "—"}</TableCell>
                <TableCell>
                  <Badge variant="outline" className={RISK_STYLE[r.risk_level]}>
                    {RISK_LABEL[r.risk_level]} ({r.risk_score}/10)
                  </Badge>
                </TableCell>
                <TableCell>
                  {r.pep ? <Badge variant="destructive">Ναι</Badge> : <span className="text-xs text-muted-foreground">Όχι</span>}
                </TableCell>
                <TableCell>
                  {r.verified_at ? (
                    <span className="text-xs text-[hsl(var(--success))] flex items-center gap-1"><CheckCircle size={14} /> Πιστοποιημένος</span>
                  ) : (
                    <span className="text-xs text-muted-foreground">Εκκρεμεί</span>
                  )}
                </TableCell>
                <TableCell className="text-right">
                  <Button size="sm" variant="ghost" onClick={() => openEdit(r.client_id)} data-testid={`edit-kyc-${r.client_id}`}>Επεξ.</Button>
                  {!r.verified_at && (
                    <Button size="sm" variant="outline" onClick={() => verify(r.client_id)} data-testid={`verify-kyc-${r.client_id}`}>Πιστοποίηση</Button>
                  )}
                </TableCell>
              </TableRow>
            ))}
            {records.length === 0 && (
              <TableRow><TableCell colSpan={6} className="text-center text-muted-foreground py-8">
                Δεν υπάρχουν καρτέλες KYC ακόμα. Δημιουργήστε την πρώτη από τη λίστα πελατών παραπάνω.
              </TableCell></TableRow>
            )}
          </TableBody>
        </Table>
      </div>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-w-2xl max-h-[85vh] overflow-y-auto" aria-describedby={undefined} data-testid="kyc-dialog">
          <DialogHeader><DialogTitle>Καρτέλα KYC — {form.client_name}</DialogTitle></DialogHeader>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <Label>Τύπος εγγράφου</Label>
              <Select value={form.id_type} onValueChange={(v) => setForm({ ...form, id_type: v })}>
                <SelectTrigger className="mt-1.5"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="id_card">Ταυτότητα</SelectItem>
                  <SelectItem value="passport">Διαβατήριο</SelectItem>
                  <SelectItem value="residence_permit">Άδεια Διαμονής</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div>
              <Label>Αριθμός</Label>
              <Input value={form.id_number} onChange={(e) => setForm({ ...form, id_number: e.target.value })} data-testid="kyc-id-number" />
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div><Label>Ημ/νία έκδοσης</Label><Input type="date" value={form.id_issue_date} onChange={(e) => setForm({ ...form, id_issue_date: e.target.value })} /></div>
            <div><Label>Αρχή έκδοσης</Label><Input value={form.id_issuer} onChange={(e) => setForm({ ...form, id_issuer: e.target.value })} /></div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div><Label>Ημ/νία γέννησης</Label><Input type="date" value={form.birth_date} onChange={(e) => setForm({ ...form, birth_date: e.target.value })} /></div>
            <div><Label>Υπηκοότητα</Label><Input value={form.nationality} onChange={(e) => setForm({ ...form, nationality: e.target.value })} /></div>
          </div>
          <div><Label>Επάγγελμα</Label><Input value={form.profession} onChange={(e) => setForm({ ...form, profession: e.target.value })} data-testid="kyc-profession" /></div>
          <div>
            <Label>Πηγή πόρων (Source of Funds)</Label>
            <Textarea rows={2} value={form.source_of_funds} onChange={(e) => setForm({ ...form, source_of_funds: e.target.value })} placeholder="π.χ. Εισοδήματα από επαγγελματική δραστηριότητα, πώληση ακινήτου..." data-testid="kyc-sof" />
          </div>
          <div>
            <Label>Σκοπός συνεργασίας</Label>
            <Textarea rows={2} value={form.purpose_of_business} onChange={(e) => setForm({ ...form, purpose_of_business: e.target.value })} placeholder="π.χ. Νομική εκπροσώπηση σε αστική υπόθεση..." />
          </div>
          <div className="flex gap-6">
            <label className="flex items-center gap-2 text-sm">
              <Checkbox checked={form.pep} onCheckedChange={(v) => setForm({ ...form, pep: !!v })} data-testid="kyc-pep" />
              Πολιτικά εκτεθειμένο πρόσωπο (PEP)
            </label>
            <label className="flex items-center gap-2 text-sm">
              <Checkbox checked={form.high_risk_country} onCheckedChange={(v) => setForm({ ...form, high_risk_country: !!v })} />
              Χώρα υψηλού κινδύνου (FATF)
            </label>
          </div>
          <div><Label>Σημειώσεις</Label><Textarea rows={2} value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} /></div>

          <DialogFooter>
            <Button variant="ghost" onClick={() => setOpen(false)}>Ακύρωση</Button>
            <Button onClick={submit} data-testid="save-kyc-button">Αποθήκευση</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
