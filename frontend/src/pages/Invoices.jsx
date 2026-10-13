import { useEffect, useState } from "react";
import axios, { API, money, dateFmt, errMsg } from "@/lib/api";
import { daysSince, invoiceLabel, todayAthens, unpaidTotal } from "@/lib/utils";
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
import { Plus, Trash, Microphone, PaperPlaneTilt, CloudCheck, Eye, PencilSimple, FilePdf, BellRinging, CheckCircle, ArrowClockwise, WarningCircle, Prohibit, ArrowUUpLeft } from "@phosphor-icons/react";
import { toast } from "sonner";

const VAT_RATES = [24, 13, 6, 0];
const STATUS_LABEL = { draft: "Πρόχειρο", issued: "Ανεξόφλητο", paid: "Πληρώθηκε", cancelled: "Ακυρώθηκε" };
const STATUS_VARIANT = { draft: "secondary", issued: "outline", paid: "default", cancelled: "destructive" };

function emptyForm() {
  return {
    client_id: "", date: todayAthens(),
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
  const [reminderId, setReminderId] = useState(null);
  const [workingId, setWorkingId] = useState(null);
  const [issuingId, setIssuingId] = useState(null);
  const [markingPaidId, setMarkingPaidId] = useState(null);
  const [filter, setFilter] = useState("all");
  // Ο server στέλνει όλα τα πρόχειρα και τα εκδοθέντα, αλλά μόνο τα τελευταία εξοφλημένα/ακυρωμένα. Όταν κόπηκαν
  // παλαιότερα, τα σύνολα «Εξοφλημένα» και «Σύνολο» αφορούν μόνο όσα φορτώθηκαν — το δηλώνουμε αντί να τα παρουσιάζουμε ως πλήρη.
  const [closedTruncated, setClosedTruncated] = useState(false);
  // Μια αποτυχημένη φόρτωση δεν είναι άδεια λίστα: όσο τα τιμολόγια δεν έχουν φορτώσει ούτε μία φορά, τα σύνολα γράφουν «—»
  // (όχι «0,00 €») και δεν λέμε «δεν υπάρχουν τιμολόγια». Αν αποτύχει μια επαναφόρτωση, η λίστα που φαίνεται μένει, με σημείωση.
  const [loaded, setLoaded] = useState(false);
  const [loadError, setLoadError] = useState("");
  const mediaRef = useState({ recorder: null, chunks: [] })[0];

  const load = () => {
    setLoadError("");
    axios.get(`${API}/invoices`).then((r) => {
      setInvoices(r.data);
      setClosedTruncated(r.headers?.["x-closed-truncated"] === "1");
      setLoaded(true);
    }).catch((e) => {
      const message = errMsg(e);
      toast.error(message);
      setLoadError(message);
    });
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
      toast.success(`Το PDF του ${invoiceLabel(inv)} δημιουργήθηκε`);
    } catch (e) {
      toast.error(errMsg(e));
    }
  };

  const sendReminder = async (inv) => {
    setReminderId(inv.id);
    try {
      const { data } = await axios.post(`${API}/invoices/${inv.id}/send-reminder`);
      toast.success(`Υπενθύμιση στάλθηκε στο ${data.email_sent_to || "πελάτη"}`);
      load();
    } catch (e) {
      toast.error(errMsg(e));
    } finally {
      setReminderId(null);
    }
  };

  const cancelInvoice = async (inv) => {
    const sentToAade = inv.mydata_status === "synced";
    const question = sentToAade
      ? `Ακύρωση του ${invoiceLabel(inv)}; Θα σταλεί και ακύρωση στο myDATA της ΑΑΔΕ.`
      : `Ακύρωση του ${invoiceLabel(inv)};`;
    if (!window.confirm(question)) return;
    setWorkingId(inv.id);
    try {
      await axios.post(`${API}/invoices/${inv.id}/cancel`, { reason: "" });
      toast.success(sentToAade ? "Το τιμολόγιο ακυρώθηκε και στο myDATA" : "Το τιμολόγιο ακυρώθηκε");
      load();
    } catch (e) {
      toast.error(errMsg(e));
    } finally {
      setWorkingId(null);
    }
  };

  const createCreditNote = async (inv) => {
    if (!window.confirm(`Δημιουργία πιστωτικού τιμολογίου για το ${invoiceLabel(inv)};`)) return;
    setWorkingId(inv.id);
    try {
      await axios.post(`${API}/invoices/${inv.id}/credit-note`, { reason: "" });
      toast.success("Δημιουργήθηκε πρόχειρο πιστωτικό· εκδώστε το για να λάβει αριθμό");
      load();
    } catch (e) {
      toast.error(errMsg(e));
    } finally {
      setWorkingId(null);
    }
  };

  const markPaid = async (inv) => {
    setMarkingPaidId(inv.id);
    try {
      await axios.post(`${API}/invoices/${inv.id}/mark-paid`);
      toast.success(`Το τιμολόγιο ${invoiceLabel(inv)} σημάνθηκε ως εξοφλημένο`);
      load();
    } catch (e) {
      toast.error(errMsg(e));
    } finally {
      setMarkingPaidId(null);
    }
  };

  const markUnpaid = async (inv) => {
    setMarkingPaidId(inv.id);
    try {
      await axios.post(`${API}/invoices/${inv.id}/mark-unpaid`);
      toast.success(`Το τιμολόγιο ${invoiceLabel(inv)} επαναφέρθηκε σε ανεξόφλητο`);
      load();
    } catch (e) {
      toast.error(errMsg(e));
    } finally {
      setMarkingPaidId(null);
    }
  };

  const issueInvoice = async (id) => {
    // Ένα κλικ = μία έκδοση: το δεύτερο κλικ, όσο το πρώτο αίτημα τρέχει, δεν στέλνεται καν.
    if (issuingId) return;
    setIssuingId(id);
    try {
      const { data } = await axios.post(`${API}/invoices/${id}/issue`);
      if (data.mydata_status === "synced") {
        const mock = String(data.mydata_mark || "").startsWith("MOCK-");
        toast.success(mock ? "Το τιμολόγιο εκδόθηκε και διαβιβάστηκε (προσομοίωση) στο myDATA"
                           : "Το τιμολόγιο εκδόθηκε και διαβιβάστηκε στο myDATA");
      } else {
        toast.success("Το τιμολόγιο εκδόθηκε· η διαβίβαση στο myDATA εκκρεμεί και θα γίνει αυτόματα");
      }
      load();
      downloadPdf(data);
    } catch (e) {
      toast.error(errMsg(e));
    } finally {
      setIssuingId(null);
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

  const unpaidList = invoices.filter((i) => i.status === "issued");
  const unpaidSum = unpaidTotal(invoices);
  const overdue30 = unpaidList.filter((i) => i.date && daysSince(i.date) >= 30).length;
  const filteredInvoices = filter === "unpaid" ? unpaidList : (filter === "paid" ? invoices.filter((i) => i.status === "paid") : invoices);

  const daysOverdue = (dateStr) => Math.max(0, daysSince(dateStr));
  const known = (value) => (loaded ? value : "—");

  return (
    <div className="space-y-8" data-testid="invoices-page">
      <div className="flex items-center justify-between">
        <div>
          <div className="lux-overline mb-1.5">Nomos One · Οικονομικά</div>
          <h1 className="font-brand text-4xl font-bold tracking-wide">Τιμολόγια</h1>
          <p className="text-sm text-muted-foreground mt-1">Έκδοση & διαχείριση τιμολογίων παροχής υπηρεσιών</p>
        </div>
        <Button onClick={openNew} data-testid="new-invoice-button"><Plus size={16} className="mr-1.5" /> Νέο Τιμολόγιο</Button>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <button
          onClick={() => setFilter("unpaid")}
          data-testid="kpi-unpaid"
          className={`lux-card p-5 text-left transition-all ${filter === "unpaid" ? "ring-1 ring-[hsl(var(--gold))]/60" : ""}`}
        >
          <div className="flex items-center justify-between">
            <span className="lux-overline">Ανείσπρακτα</span>
            <BellRinging size={18} className="text-[hsl(var(--gold))]" />
          </div>
          <div className="mt-2 font-mono text-3xl font-semibold" data-testid="kpi-unpaid-total">{known(money(unpaidSum))}</div>
          <div className="mt-1 text-xs text-muted-foreground">
            <span data-testid="kpi-unpaid-count">{known(unpaidList.length)}</span> ανεξόφλητα τιμολόγια
            {overdue30 > 0 && <span className="ml-2 text-[hsl(var(--destructive))]">· {overdue30} με καθυστέρηση 30+ ημερών</span>}
          </div>
        </button>
        <button
          onClick={() => setFilter("paid")}
          data-testid="kpi-paid"
          className={`lux-card p-5 text-left transition-all ${filter === "paid" ? "ring-1 ring-[hsl(var(--gold))]/60" : ""}`}
        >
          <div className="flex items-center justify-between">
            <span className="lux-overline">Εξοφλημένα</span>
            <CheckCircle size={18} className="text-[hsl(var(--success))]" />
          </div>
          <div className="mt-2 font-mono text-3xl font-semibold">{known(money(invoices.filter((i) => i.status === "paid").reduce((s, i) => s + (i.total || 0), 0)))}</div>
          <div className="mt-1 text-xs text-muted-foreground">{known(invoices.filter((i) => i.status === "paid").length)} τιμολόγια</div>
        </button>
        <button
          onClick={() => setFilter("all")}
          data-testid="kpi-all"
          className={`lux-card p-5 text-left transition-all ${filter === "all" ? "ring-1 ring-[hsl(var(--gold))]/60" : ""}`}
        >
          <div className="flex items-center justify-between">
            <span className="lux-overline">Σύνολο</span>
            <ArrowClockwise size={18} className="text-muted-foreground" />
          </div>
          <div className="mt-2 font-mono text-3xl font-semibold">{known(money(invoices.reduce((s, i) => s + (i.total || 0), 0)))}</div>
          <div className="mt-1 text-xs text-muted-foreground">{known(invoices.length)} τιμολόγια συνολικά</div>
        </button>
      </div>

      {loadError && (
        <div role="alert" className="flex flex-wrap items-center justify-between gap-3 text-sm border border-[hsl(var(--destructive))]/30 bg-[hsl(var(--destructive))]/5 rounded-md px-3 py-2"
          data-testid="invoices-load-error">
          <span className="text-[hsl(var(--destructive))]">
            Δεν φορτώθηκαν τα τιμολόγια: {loadError}{loaded ? " Η λίστα που βλέπετε μπορεί να μην είναι ενημερωμένη." : ""}
          </span>
          <Button size="sm" variant="outline" onClick={load} data-testid="invoices-retry">Επανάληψη</Button>
        </div>
      )}

      {closedTruncated && (
        <div className="text-xs text-muted-foreground border border-border/60 rounded-md px-3 py-2" data-testid="closed-truncated-notice">
          Εμφανίζονται όλα τα πρόχειρα και τα εκδοθέντα και τα τελευταία 1.000 εξοφλημένα/ακυρωμένα τιμολόγια. Τα «Ανείσπρακτα» είναι
          πλήρη· τα «Εξοφλημένα» και το «Σύνολο» αφορούν μόνο όσα εμφανίζονται.
        </div>
      )}

      <div className="lux-card overflow-hidden animate-in-up">
        <div className="flex items-center justify-between px-4 py-3 border-b border-border/60">
          <div className="text-sm text-muted-foreground">
            Προβολή: <span className="font-medium text-foreground" data-testid="invoices-filter-label">{filter === "unpaid" ? "Ανεξόφλητα" : filter === "paid" ? "Εξοφλημένα" : "Όλα"}</span>
            <span className="ml-2">· {known(filteredInvoices.length)} εγγραφές</span>
          </div>
          {filter !== "all" && (
            <Button variant="ghost" size="sm" onClick={() => setFilter("all")} data-testid="clear-filter-button">Καθαρισμός φίλτρου</Button>
          )}
        </div>
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
            {filteredInvoices.map((inv) => {
              const overdue = inv.status === "issued" ? daysOverdue(inv.date) : 0;
              return (
              <TableRow key={inv.id} data-testid={`invoice-row-${inv.id}`}>
                <TableCell className="font-mono text-xs">{invoiceLabel(inv)}</TableCell>
                <TableCell>{inv.client_name}</TableCell>
                <TableCell>
                  {dateFmt(inv.date)}
                  {overdue >= 30 && (
                    <span className="ml-2 inline-flex items-center gap-1 text-[10px] text-[hsl(var(--destructive))]">
                      <WarningCircle size={12} weight="fill" /> {overdue}η
                    </span>
                  )}
                </TableCell>
                <TableCell>
                  <Badge variant={STATUS_VARIANT[inv.status]}>{STATUS_LABEL[inv.status]}</Badge>
                  {inv.credit_note_for && <div className="text-[10px] text-muted-foreground mt-0.5">Πιστωτικό</div>}
                  {inv.credit_note_id && <div className="text-[10px] text-muted-foreground mt-0.5">Πιστώθηκε</div>}
                </TableCell>
                <TableCell>
                  {inv.mydata_status === "synced" ? (
                    <span className="flex items-center gap-1 text-xs text-[hsl(var(--success))]"><CloudCheck size={14} /> {inv.mydata_mark?.slice(0, 14)}</span>
                  ) : (
                    <span className="text-xs text-muted-foreground">Μη σταλμένο</span>
                  )}
                </TableCell>
                <TableCell className="text-right font-mono">
                  {money(inv.total)}
                  {inv.status === "issued" && inv.total > 0 && inv.outstanding != null && Math.abs(inv.outstanding - inv.total) >= 0.005 && (
                    <div className="text-[10px] text-muted-foreground font-sans" data-testid={`due-${inv.id}`}>Οφείλεται: {money(inv.outstanding)}</div>
                  )}
                </TableCell>
                <TableCell className="text-right">
                  <Button variant="ghost" size="icon" onClick={() => setViewing(inv)} data-testid={`view-invoice-${inv.id}`}><Eye size={16} /></Button>
                  <Button variant="ghost" size="icon" onClick={() => downloadPdf(inv)} title="Λήψη PDF" data-testid={`pdf-invoice-${inv.id}`}><FilePdf size={16} /></Button>
                  {inv.status === "issued" && (
                    <>
                      <Button
                        variant="ghost"
                        size="icon"
                        onClick={() => sendReminder(inv)}
                        disabled={reminderId === inv.id}
                        title={inv.reminder_sent_at ? `Τελευταία υπενθύμιση: ${dateFmt(inv.reminder_sent_at)} (×${inv.reminder_count || 0})` : "Αποστολή υπενθύμισης εξόφλησης"}
                        className={inv.reminder_sent_at ? "text-[hsl(var(--gold))]" : ""}
                        data-testid={`reminder-invoice-${inv.id}`}
                      >
                        <BellRinging size={16} />
                      </Button>
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() => markPaid(inv)}
                        disabled={markingPaidId === inv.id}
                        data-testid={`mark-paid-${inv.id}`}
                      >
                        <CheckCircle size={14} className="mr-1" /> Πληρώθηκε
                      </Button>
                    </>
                  )}
                  {inv.status === "issued" && (
                    <Button
                      variant="ghost"
                      size="icon"
                      onClick={() => cancelInvoice(inv)}
                      disabled={workingId === inv.id}
                      title={inv.mydata_status === "synced" ? "Ακύρωση — θα σταλεί και στο myDATA" : "Ακύρωση τιμολογίου"}
                      data-testid={`cancel-invoice-${inv.id}`}
                    >
                      <Prohibit size={16} />
                    </Button>
                  )}
                  {inv.status === "paid" && (
                    <>
                      <Button variant="ghost" size="sm" onClick={() => markUnpaid(inv)} disabled={markingPaidId === inv.id} data-testid={`mark-unpaid-${inv.id}`}>
                        <ArrowClockwise size={14} className="mr-1" /> Επαναφορά
                      </Button>
                      {inv.mydata_status === "synced" && !inv.credit_note_id && (
                        <Button
                          variant="ghost"
                          size="icon"
                          onClick={() => createCreditNote(inv)}
                          disabled={workingId === inv.id}
                          title="Δημιουργία πρόχειρου πιστωτικού τιμολογίου (myDATA 5.1)"
                          data-testid={`credit-note-${inv.id}`}
                        >
                          <ArrowUUpLeft size={16} />
                        </Button>
                      )}
                    </>
                  )}
                  {inv.status === "draft" && (
                    <>
                      <Button variant="ghost" size="icon" onClick={() => openEdit(inv)} data-testid={`edit-invoice-${inv.id}`}><PencilSimple size={16} /></Button>
                      <Button variant="ghost" size="icon" onClick={() => deleteInvoice(inv.id)} data-testid={`delete-invoice-${inv.id}`}><Trash size={16} /></Button>
                      <Button size="sm" variant="outline" onClick={() => issueInvoice(inv.id)} disabled={!!issuingId} data-testid={`issue-invoice-${inv.id}`}>{issuingId === inv.id ? "..." : "Έκδοση"}</Button>
                    </>
                  )}
                </TableCell>
              </TableRow>
              );
            })}
            {filteredInvoices.length === 0 && loaded && (
              <TableRow><TableCell colSpan={7} className="text-center text-muted-foreground py-8">
                {filter === "unpaid" ? "Δεν υπάρχουν ανεξόφλητα τιμολόγια." : filter === "paid" ? "Δεν υπάρχουν εξοφλημένα τιμολόγια." : "Δεν υπάρχουν τιμολόγια ακόμα."}
              </TableCell></TableRow>
            )}
            {!loaded && !loadError && (
              <TableRow><TableCell colSpan={7} className="text-center text-muted-foreground py-8" data-testid="invoices-loading">Φόρτωση τιμολογίων…</TableCell></TableRow>
            )}
          </TableBody>
        </Table>
      </div>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-w-2xl max-h-[85vh] overflow-y-auto" aria-describedby={undefined}>
          <DialogHeader><DialogTitle>{editing ? `Επεξεργασία Τιμολογίου ${invoiceLabel(editing)}` : "Νέο Τιμολόγιο"}</DialogTitle></DialogHeader>

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
              {recording && <p className="text-xs text-destructive animate-pulse" aria-live="polite">● Ακούω... πείτε π.χ. «Τιμολόγιο για τον πελάτη Παπαδόπουλο, 500 ευρώ, νομική συμβουλή»</p>}
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
                    <SelectTrigger className="px-2.5"><SelectValue /></SelectTrigger>
                    <SelectContent>{VAT_RATES.map((r) => <SelectItem key={r} value={String(r)}>{r}%</SelectItem>)}</SelectContent>
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
