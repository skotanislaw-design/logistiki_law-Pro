import { useEffect, useRef, useState } from "react";
import axios, { API, money, dateFmt, errMsg } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { ImagePreviewDialog } from "@/components/ImagePreviewDialog";
import { Plus, Camera, Sparkle, Eye, PencilSimple, Trash, ImageSquare } from "@phosphor-icons/react";
import { toast } from "sonner";

const VAT_RATES = [24, 13, 6, 0];
const CATEGORIES = [
  "Ενοίκιο", "Λογαριασμοί ΔΕΚΟ", "Προμήθειες γραφείου", "Δικηγορικά έξοδα",
  "Αμοιβές τρίτων", "Μεταφορικά", "Τηλεπικοινωνίες", "Ασφάλιστρα",
  "Συνδρομές / Λογισμικό", "Φιλοξενία / Εστίαση", "Λοιπά",
];

function emptyForm() {
  return { vendor: "", date: new Date().toISOString().slice(0, 10), amount: 0, vat_amount: 0, vat_rate: 24, category: "Λοιπά", description: "", source: "manual", receipt_image_url: null };
}

function receiptSrc(path) {
  return path ? `${API}/expenses/files/${path}` : null;
}

export default function Expenses() {
  const [expenses, setExpenses] = useState([]);
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState(null);
  const [form, setForm] = useState(emptyForm());
  const [ocrLoading, setOcrLoading] = useState(false);
  const [previewSrc, setPreviewSrc] = useState(null);
  const fileRef = useRef(null);

  const load = () => axios.get(`${API}/expenses`).then((r) => setExpenses(r.data)).catch((e) => toast.error(errMsg(e)));
  useEffect(() => { load(); }, []);

  const handleFile = async (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setEditing(null);
    setOcrLoading(true);
    setOpen(true);
    const reader = new FileReader();
    reader.onload = async () => {
      const base64 = reader.result.split(",")[1];
      try {
        const { data } = await axios.post(`${API}/expenses/ocr-parse`, { image_base64: base64 });
        const p = data.parsed;
        setForm({
          vendor: p.vendor || "", date: p.date || new Date().toISOString().slice(0, 10),
          amount: p.amount || 0, vat_amount: p.vat_amount || 0, vat_rate: p.vat_rate || 24,
          category: CATEGORIES.includes(p.category) ? p.category : "Λοιπά",
          description: p.description || "", source: "photo_ocr", receipt_image_url: data.receipt_image_url,
        });
        toast.success("Η απόδειξη αναλύθηκε αυτόματα με AI");
      } catch (err) {
        toast.error(errMsg(err));
      } finally {
        setOcrLoading(false);
      }
    };
    reader.readAsDataURL(file);
  };

  const openNew = () => { setEditing(null); setForm(emptyForm()); setOpen(true); };
  const openEdit = (ex) => { setEditing(ex); setForm(ex); setOpen(true); };
  const openPreview = (ex) => setPreviewSrc(receiptSrc(ex.receipt_image_url));

  const handleSubmit = async () => {
    try {
      if (editing) {
        await axios.put(`${API}/expenses/${editing.id}`, form);
        toast.success("Το έξοδο ενημερώθηκε");
      } else {
        await axios.post(`${API}/expenses`, form);
        toast.success("Το έξοδο καταχωρήθηκε");
      }
      setOpen(false);
      load();
    } catch (e) {
      toast.error(errMsg(e));
    }
  };

  const handleDelete = async (id) => {
    try {
      await axios.delete(`${API}/expenses/${id}`);
      toast.success("Το έξοδο διαγράφηκε");
      load();
    } catch (e) {
      toast.error(errMsg(e));
    }
  };

  return (
    <div className="space-y-8" data-testid="expenses-page">
      <div className="flex items-center justify-between">
        <div>
          <div className="lux-overline mb-1.5">Nomos One · Οικονομικά</div>
          <h1 className="font-brand text-4xl font-bold tracking-wide">Έξοδα</h1>
          <p className="text-sm text-muted-foreground mt-1">Καταχώρηση παραστατικών εξόδων — χειροκίνητα ή με φωτογραφία (AI OCR)</p>
        </div>
        <div className="flex gap-2">
          <input type="file" accept="image/*" ref={fileRef} className="hidden" onChange={handleFile} data-testid="ocr-file-input" />
          <Button variant="outline" onClick={() => fileRef.current.click()} data-testid="upload-receipt-button">
            <Camera size={16} className="mr-1.5" /> Ανέβασμα Απόδειξης
          </Button>
          <Button onClick={openNew} data-testid="new-expense-button">
            <Plus size={16} className="mr-1.5" /> Νέο Έξοδο
          </Button>
        </div>
      </div>

      <div className="lux-card overflow-hidden animate-in-up">
        <Table data-testid="expenses-table">
          <TableHeader>
            <TableRow>
              <TableHead>Απόδειξη</TableHead>
              <TableHead>Προμηθευτής</TableHead>
              <TableHead>Ημερομηνία</TableHead>
              <TableHead>Κατηγορία</TableHead>
              <TableHead>Πηγή</TableHead>
              <TableHead className="text-right">ΦΠΑ</TableHead>
              <TableHead className="text-right">Ποσό</TableHead>
              <TableHead className="text-right">Ενέργειες</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {expenses.map((ex) => (
              <TableRow key={ex.id} data-testid={`expense-row-${ex.id}`}>
                <TableCell>
                  {ex.receipt_image_url ? (
                    <button onClick={() => openPreview(ex)} className="block" data-testid={`view-receipt-${ex.id}`}>
                      <img src={receiptSrc(ex.receipt_image_url)} alt="απόδειξη" className="w-10 h-10 object-cover rounded-sm border border-border hover:opacity-80 transition-opacity duration-200" />
                    </button>
                  ) : (
                    <span className="w-10 h-10 flex items-center justify-center text-muted-foreground/40 border border-dashed border-border rounded-sm"><ImageSquare size={18} /></span>
                  )}
                </TableCell>
                <TableCell>{ex.vendor}</TableCell>
                <TableCell>{dateFmt(ex.date)}</TableCell>
                <TableCell>{ex.category}</TableCell>
                <TableCell className="text-xs text-muted-foreground">{ex.source === "photo_ocr" ? "AI OCR" : "Χειροκίνητα"}</TableCell>
                <TableCell className="text-right font-mono">{money(ex.vat_amount)}</TableCell>
                <TableCell className="text-right font-mono">{money(ex.amount)}</TableCell>
                <TableCell className="text-right">
                  {ex.receipt_image_url && (
                    <Button variant="ghost" size="icon" onClick={() => openPreview(ex)} data-testid={`preview-expense-${ex.id}`}><Eye size={16} /></Button>
                  )}
                  <Button variant="ghost" size="icon" onClick={() => openEdit(ex)} data-testid={`edit-expense-${ex.id}`}><PencilSimple size={16} /></Button>
                  <Button variant="ghost" size="icon" onClick={() => handleDelete(ex.id)} data-testid={`delete-expense-${ex.id}`}><Trash size={16} /></Button>
                </TableCell>
              </TableRow>
            ))}
            {expenses.length === 0 && (
              <TableRow><TableCell colSpan={8} className="text-center text-muted-foreground py-8">Δεν υπάρχουν έξοδα ακόμα.</TableCell></TableRow>
            )}
          </TableBody>
        </Table>
      </div>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-w-lg">
          <DialogHeader><DialogTitle>{editing ? "Επεξεργασία Εξόδου" : form.source === "photo_ocr" ? "Επιβεβαίωση Στοιχείων Απόδειξης" : "Νέο Έξοδο"}</DialogTitle></DialogHeader>

          {ocrLoading ? (
            <div className="space-y-3" data-testid="ocr-loading" aria-live="polite">
              <div className="flex items-center gap-2 text-sm text-muted-foreground"><Sparkle size={16} className="animate-pulse" /> Ανάλυση απόδειξης με AI...</div>
              <Skeleton className="h-9 w-full" />
              <Skeleton className="h-9 w-full" />
              <Skeleton className="h-9 w-full" />
            </div>
          ) : (
            <div className="space-y-3">
              {form.receipt_image_url && (
                <img src={receiptSrc(form.receipt_image_url)} alt="απόδειξη" className="w-full max-h-48 object-contain rounded-sm border border-border bg-muted/30" />
              )}
              <div>
                <Label>Προμηθευτής</Label>
                <Input value={form.vendor} onChange={(e) => setForm({ ...form, vendor: e.target.value })} className="mt-1.5" data-testid="expense-vendor-input" />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <Label>Ημερομηνία</Label>
                  <Input type="date" value={form.date} onChange={(e) => setForm({ ...form, date: e.target.value })} className="mt-1.5" />
                </div>
                <div>
                  <Label>Κατηγορία</Label>
                  <Select value={form.category} onValueChange={(v) => setForm({ ...form, category: v })}>
                    <SelectTrigger className="mt-1.5"><SelectValue /></SelectTrigger>
                    <SelectContent>{CATEGORIES.map((c) => <SelectItem key={c} value={c}>{c}</SelectItem>)}</SelectContent>
                  </Select>
                </div>
              </div>
              <div className="grid grid-cols-3 gap-3">
                <div>
                  <Label>Συνολικό Ποσό</Label>
                  <Input type="number" value={form.amount} onChange={(e) => setForm({ ...form, amount: Number(e.target.value) })} className="mt-1.5" data-testid="expense-amount-input" />
                </div>
                <div>
                  <Label>Ποσό ΦΠΑ</Label>
                  <Input type="number" value={form.vat_amount} onChange={(e) => setForm({ ...form, vat_amount: Number(e.target.value) })} className="mt-1.5" />
                </div>
                <div>
                  <Label>Συντ. ΦΠΑ</Label>
                  <Select value={String(form.vat_rate)} onValueChange={(v) => setForm({ ...form, vat_rate: Number(v) })}>
                    <SelectTrigger className="mt-1.5"><SelectValue /></SelectTrigger>
                    <SelectContent>{VAT_RATES.map((r) => <SelectItem key={r} value={String(r)}>{r}%</SelectItem>)}</SelectContent>
                  </Select>
                </div>
              </div>
              <div>
                <Label>Περιγραφή</Label>
                <Input value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} className="mt-1.5" />
              </div>
            </div>
          )}

          <DialogFooter>
            <Button variant="ghost" onClick={() => setOpen(false)}>Ακύρωση</Button>
            <Button onClick={handleSubmit} disabled={ocrLoading} data-testid="save-expense-button">Αποθήκευση</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <ImagePreviewDialog src={previewSrc} open={!!previewSrc} onOpenChange={(v) => !v && setPreviewSrc(null)} />
    </div>
  );
}
