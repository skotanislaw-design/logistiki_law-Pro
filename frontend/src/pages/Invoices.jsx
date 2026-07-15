import { useEffect, useState } from "react";
import axios, { API, money, dateFmt, errMsg } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog";
import { Badge } from "@/components/ui/badge";
import { InvoiceDetailDialog } from "@/components/InvoiceDetailDialog";
import { Plus, Trash, Microphone, PaperPlaneTilt, CloudCheck, Eye, PencilSimple, FilePdf, EnvelopeSimple } from "@phosphor-icons/react";
import { toast } from "sonner";

const VAT_RATES = [24, 13, 6, 0];
const STATUS_LABEL = { draft: "Πρόχειρο", issued: "Εκδόθηκε", paid: "Πληρώθηκε", cancelled: "Ακυρώθηκε" };
const STATUS_VARIANT = { draft: "secondary", issued: "default", paid: "default", cancelled: "destructive" };

function emptyForm() {
  return {
    client_id: "", date: new Date().toISOString().slice(0, 10),
    line_items: [{ description: "", quantity: 1, unit_price: 0, vat_rate: 24 }],
    payment_method: "Τραπεζική κατάθεση", notes: "",
  };
}

export default function Invoices() {
  const [invoices, setInvoices] = useState([]);
  const [clients, setClients] = useState([]);
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState(null);
  const [form, setForm] = useState(emptyForm());
  const [recording, setRecording] = useState(false);
  const [voiceStatus, setVoiceStatus] = useState("");
  const [viewing, setViewing] = useState(null);
  const [sendingId, setSendingId] = useState(null);
  const mediaRef = useState({ recorder: null, chunks: [] })[0];

  const load = () => {
    axios.get(`${API}/invoices`).then((r) => setInvoices(r.data)).catch((e) => toast.error(errMsg(e)));
    axios.get(`${API}/clients`).then((r) => setClients(r.data)).catch(() => {});
  };

  useEffect(() => { load(); }, []);

  const updateLine = (idx, field, value) => {
    const items = [...form.line_items];
    items[idx] = { ...items[idx], [field]: value };
    setForm({ ...form, line_items: items });
  };

  const addLine = () => setForm({ ...form, line_items: [...form.line_items, { description: "", quantity: 1, unit_price: 0, vat_rate: 24 }] });
  const removeLine = (idx) => setForm({ ...form, line_items: form.line_items.filter((_, i) => i !== idx) });

  const totals = form.line_items.reduce(
    (acc, li) => {
      const lineTotal = (Number(li.quantity) || 0) * (Number(li.unit_price) || 0);
      const vat = lineTotal * (Number(li.vat_rate) || 0) / 100;
      return { subtotal: acc.subtotal + lineTotal, vat: acc.vat + vat };
    },
    { subtotal: 0, vat: 0 }
  );

  const openNew = () => { setEditing(null); setForm(emptyForm()); setOpen(true); };
  const openEdit = (inv) => { setEditing(inv); setForm({ client_id: inv.client_id, date: inv.date, line_items: inv.line_items, payment_method: inv.payment_method, notes: inv.notes }); setOpen(true); };

  const handleSubmit = async () => {
    if (!form.client_id) return toast.error("Επιλέξτε πελάτη");
    try {
      if (editing) {
        await axios.put(`${API}/invoices/${editing.id}`, form);
        toast.success("Το τιμολόγιο ενημερώθηκε");
      } else {
        await axios.post(`${API}/invoices`, form);
        toast.success("Το τιμολόγιο δημιουργήθηκε");
      }
      setOpen(false);
      setForm(emptyForm());
      load();
    } catch (e) {
      toast.error(errMsg(e));
    }
  };

  const downloadPdf = async (inv) => {
    try {
      const res = await axios.get(`${API}/invoices/${inv.id}/pdf`, { responseType: "blob" });
      const url = URL.createObjectURL(res.data);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${inv.number || "timologio"}.pdf`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      toast.success(`Το PDF του ${inv.number} δημιουργήθηκε`);
    } catch (e) {
      toast.error(errMsg(e));
    }
  };

  const sendEmail = async (inv) => {
    setSendingId(inv.id);
    try {
      const { data } = await axios.post(`${API}/invoices/${inv.id}/send-email`);
      toast.success(`Το τιμολόγιο ${inv.number} στάλθηκε στο ${data.email_sent_to}`);
      load();
    } catch (e) {
      toast.error(errMsg(e));
    } finally {
      setSendingId(null);
    }
  };

  const issueInvoice = async (id) => {
    try {
      const { data } = await axios.post(`${API}/invoices/${id}/issue`);
      toast.success("Το τιμολόγιο εκδόθηκε και στάλθηκε (mock) στο myDATA");
      load();
      downloadPdf(data);
    } catch (e) {
      toast.error(errMsg(e));
    }
  };

  const deleteInvoice = async (id) => {
    try {
      await axios.delete(`${API}/invoices/${id}`);
      toast.success("Το τιμολόγιο διαγράφηκε");
      load();
    } catch (e) {
      toast.error(errMsg(e));
    }
  };

  const startRecording = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const recorder = new MediaRecorder(stream);
      mediaRef.chunks = [];
      recorder.ondataavailable = (e) => mediaRef.chunks.push(e.data);
      recorder.onstop = async () => {
        const blob = new Blob(mediaRef.chunks, { type: "audio/webm" });
        setVoiceStatus("Επεξεργασία φωνητικής εντολής...");
        const fd = new FormData();
        fd.append("audio", blob, "voice.webm");
        try {
          const { data } = await axios.post(`${API}/invoices/voice-parse`, fd, { headers: { "Content-Type": "multipart/form-data" } });
          setVoiceStatus(`Κατανοήθηκε: "${data.transcript}"`);
          setForm((f) => ({
            ...f,
            client_id: data.matched_client?.id || f.client_id,
            line_items: [{ description: data.parsed.description || "", quantity: 1, unit_price: data.parsed.amount || 0, vat_rate: data.parsed.vat_rate || 24 }],
          }));
          toast.success("Το τιμολόγιο συμπληρώθηκε από τη φωνητική εντολή");
        } catch (e) {
          toast.error(errMsg(e));
          setVoiceStatus("");
        }
      };
      recorder.start();
      mediaRef.recorder = recorder;
      setRecording(true);
    } catch {
      toast.error("Δεν δόθηκε πρόσβαση στο μικρόφωνο");
    }
  };

  const stopRecording = () => {
    mediaRef.recorder?.stop();
    setRecording(false);
  };

  return (
    <div className="space-y-6" data-testid="invoices-page">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="font-heading text-3xl font-bold tracking-tight">Τιμολόγια</h1>
          <p className="text-sm text-muted-foreground mt-1">Έκδοση & διαχείριση τιμολογίων παροχής υπηρεσιών</p>
        </div>
        <Button onClick={openNew} data-testid="new-invoice-button"><Plus size={16} className="mr-1.5" /> Νέο Τιμολόγιο</Button>
      </div>

      <div className="border border-border rounded-sm bg-card overflow-hidden">
        <Table data-testid="invoices-table">
          <TableHeader>
            <TableRow>
              <TableHead>Αριθμός</TableHead>
              <TableHead>Πελάτης</TableHead>
              <TableHead>Ημερομηνία</TableHead>
              <TableHead>Κατάσταση</TableHead>
              <TableHead>myDATA</TableHead>
              <TableHead className="text-right">Σύνολο</TableHead>
              <TableHead className="text-right">Ενέργειες</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {invoices.map((inv) => (
              <TableRow key={inv.id} data-testid={`invoice-row-${inv.id}`}>
                <TableCell className="font-mono text-xs">{inv.number}</TableCell>
                <TableCell>{inv.client_name}</TableCell>
                <TableCell>{dateFmt(inv.date)}</TableCell>
                <TableCell><Badge variant={STATUS_VARIANT[inv.status]}>{STATUS_LABEL[inv.status]}</Badge></TableCell>
                <TableCell>
                  {inv.mydata_status === "synced" ? (
                    <span className="flex items-center gap-1 text-xs text-[hsl(var(--success))]"><CloudCheck size={14} /> {inv.mydata_mark?.slice(0, 14)}</span>
                  ) : (
                    <span className="text-xs text-muted-foreground">Μη σταλμένο</span>
                  )}
                </TableCell>
                <TableCell className="text-right font-mono">{money(inv.total)}</TableCell>
                <TableCell className="text-right">
                  <Button variant="ghost" size="icon" onClick={() => setViewing(inv)} data-testid={`view-invoice-${inv.id}`}><Eye size={16} /></Button>
                  <Button variant="ghost" size="icon" onClick={() => downloadPdf(inv)} title="Λήψη PDF" data-testid={`pdf-invoice-${inv.id}`}><FilePdf size={16} /></Button>
                  {inv.status === "draft" && (
                    <>
                      <Button variant="ghost" size="icon" onClick={() => openEdit(inv)} data-testid={`edit-invoice-${inv.id}`}><PencilSimple size={16} /></Button>
                      <Button variant="ghost" size="icon" onClick={() => deleteInvoice(inv.id)} data-testid={`delete-invoice-${inv.id}`}><Trash size={16} /></Button>
                      <Button size="sm" variant="outline" onClick={() => issueInvoice(inv.id)} data-testid={`issue-invoice-${inv.id}`}>Έκδοση</Button>
                    </>
                  )}
                </TableCell>
              </TableRow>
            ))}
            {invoices.length === 0 && (
              <TableRow><TableCell colSpan={7} className="text-center text-muted-foreground py-8">Δεν υπάρχουν τιμολόγια ακόμα.</TableCell></TableRow>
            )}
          </TableBody>
        </Table>
      </div>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-w-2xl max-h-[85vh] overflow-y-auto">
          <DialogHeader><DialogTitle>{editing ? `Επεξεργασία Τιμολογίου ${editing.number}` : "Νέο Τιμολόγιο"}</DialogTitle></DialogHeader>

          {!editing && (
            <div className="border border-dashed border-border rounded-sm p-4 bg-muted/40 space-y-2">
              <div className="flex items-center justify-between">
                <span className="text-sm font-medium flex items-center gap-2"><Microphone size={16} /> Δημιουργία με φωνή</span>
                {!recording ? (
                  <Button size="sm" variant="outline" onClick={startRecording} data-testid="voice-record-start">Εκκίνηση Εγγραφής</Button>
                ) : (
                  <Button size="sm" variant="destructive" onClick={stopRecording} data-testid="voice-record-stop">Διακοπή & Ανάλυση</Button>
                )}
              </div>
              {recording && <p className="text-xs text-destructive animate-pulse" aria-live="polite">● Ακούω... πείτε π.χ. "Τιμολόγιο για τον πελάτη Παπαδόπουλο, 500 ευρώ, νομική συμβουλή"</p>}
              {voiceStatus && <p className="text-xs text-muted-foreground" aria-live="polite" data-testid="voice-status">{voiceStatus}</p>}
            </div>
          )}

          <div>
            <Label>Πελάτης</Label>
            <Select value={form.client_id} onValueChange={(v) => setForm({ ...form, client_id: v })}>
              <SelectTrigger className="mt-1.5" data-testid="invoice-client-select"><SelectValue placeholder="Επιλέξτε πελάτη" /></SelectTrigger>
              <SelectContent>
                {clients.map((c) => <SelectItem key={c.id} value={c.id}>{c.name}</SelectItem>)}
              </SelectContent>
            </Select>
          </div>

          <div>
            <Label>Ημερομηνία</Label>
            <Input type="date" value={form.date} onChange={(e) => setForm({ ...form, date: e.target.value })} className="mt-1.5" />
          </div>

          <div className="space-y-3">
            <Label>Γραμμές Τιμολογίου</Label>
            {form.line_items.map((li, idx) => (
              <div key={idx} className="grid grid-cols-12 gap-2 items-end" data-testid={`invoice-line-${idx}`}>
                <div className="col-span-5">
                  <Input placeholder="Περιγραφή υπηρεσίας" value={li.description} onChange={(e) => updateLine(idx, "description", e.target.value)} />
                </div>
                <div className="col-span-2">
                  <Input type="number" placeholder="Ποσ." value={li.quantity} onChange={(e) => updateLine(idx, "quantity", Number(e.target.value))} />
                </div>
                <div className="col-span-2">
                  <Input type="number" placeholder="Τιμή" value={li.unit_price} onChange={(e) => updateLine(idx, "unit_price", Number(e.target.value))} />
                </div>
                <div className="col-span-2">
                  <Select value={String(li.vat_rate)} onValueChange={(v) => updateLine(idx, "vat_rate", Number(v))}>
                    <SelectTrigger><SelectValue /></SelectTrigger>
                    <SelectContent>{VAT_RATES.map((r) => <SelectItem key={r} value={String(r)}>{r}% ΦΠΑ</SelectItem>)}</SelectContent>
                  </Select>
                </div>
                <div className="col-span-1">
                  <Button variant="ghost" size="icon" onClick={() => removeLine(idx)} disabled={form.line_items.length === 1}><Trash size={16} /></Button>
                </div>
              </div>
            ))}
            <Button variant="outline" size="sm" onClick={addLine} data-testid="add-invoice-line"><Plus size={14} className="mr-1" /> Προσθήκη Γραμμής</Button>
          </div>

          <Textarea placeholder="Σημειώσεις" value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} />

          <div className="flex justify-end gap-6 text-sm border-t border-border pt-3">
            <span>Καθαρή Αξία: <strong className="font-mono">{money(totals.subtotal)}</strong></span>
            <span>ΦΠΑ: <strong className="font-mono">{money(totals.vat)}</strong></span>
            <span>Σύνολο: <strong className="font-mono">{money(totals.subtotal + totals.vat)}</strong></span>
          </div>

          <DialogFooter>
            <Button variant="ghost" onClick={() => setOpen(false)}>Ακύρωση</Button>
            <Button onClick={handleSubmit} data-testid="save-invoice-button"><PaperPlaneTilt size={16} className="mr-1.5" /> Αποθήκευση</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <InvoiceDetailDialog invoice={viewing} open={!!viewing} onOpenChange={(v) => !v && setViewing(null)} onEmailSent={load} />
    </div>
  );
}
