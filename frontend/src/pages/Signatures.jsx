import { useEffect, useRef, useState } from "react";
import axios, { API, errMsg } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Signature, Eraser, CheckCircle, Info } from "@phosphor-icons/react";
import { toast } from "sonner";

function SignatureCanvas({ onCapture }) {
  const canvasRef = useRef(null);
  const [drawing, setDrawing] = useState(false);
  const [hasSig, setHasSig] = useState(false);

  useEffect(() => {
    const c = canvasRef.current;
    if (!c) return;
    c.width = c.offsetWidth * 2;
    c.height = c.offsetHeight * 2;
    const ctx = c.getContext("2d");
    ctx.scale(2, 2);
    ctx.lineWidth = 2;
    ctx.lineCap = "round";
    ctx.strokeStyle = "#0a0f1e";
  }, []);

  const pos = (e) => {
    const c = canvasRef.current;
    const rect = c.getBoundingClientRect();
    const t = e.touches?.[0];
    return { x: (t ? t.clientX : e.clientX) - rect.left, y: (t ? t.clientY : e.clientY) - rect.top };
  };

  const start = (e) => {
    e.preventDefault();
    setDrawing(true);
    const p = pos(e);
    const ctx = canvasRef.current.getContext("2d");
    ctx.beginPath();
    ctx.moveTo(p.x, p.y);
  };
  const move = (e) => {
    if (!drawing) return;
    e.preventDefault();
    const p = pos(e);
    const ctx = canvasRef.current.getContext("2d");
    ctx.lineTo(p.x, p.y);
    ctx.stroke();
    setHasSig(true);
  };
  const end = () => {
    setDrawing(false);
    if (hasSig) onCapture?.(canvasRef.current.toDataURL("image/png"));
  };
  const clear = () => {
    const c = canvasRef.current;
    c.getContext("2d").clearRect(0, 0, c.width, c.height);
    setHasSig(false);
    onCapture?.(null);
  };

  return (
    <div className="space-y-2">
      <div className="relative border-2 border-dashed border-[hsl(var(--gold))]/40 rounded-xl bg-white">
        <canvas
          ref={canvasRef}
          onMouseDown={start} onMouseMove={move} onMouseUp={end} onMouseLeave={end}
          onTouchStart={start} onTouchMove={move} onTouchEnd={end}
          className="w-full h-40 touch-none cursor-crosshair"
          data-testid="signature-canvas"
        />
        {!hasSig && <div className="absolute inset-0 flex items-center justify-center pointer-events-none text-muted-foreground text-sm">Υπογραφή εδώ ↓</div>}
      </div>
      <Button variant="ghost" size="sm" onClick={clear} data-testid="signature-clear"><Eraser size={14} className="mr-1" /> Καθαρισμός</Button>
    </div>
  );
}

export default function Signatures() {
  const [signatures, setSignatures] = useState([]);
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({
    document_title: "", signer_name: "", signer_email: "", signer_role: "client", signature_data: "",
  });
  const [verifyResult, setVerifyResult] = useState(null);

  const load = () => axios.get(`${API}/signatures`).then((r) => setSignatures(r.data)).catch((e) => toast.error(errMsg(e)));
  useEffect(() => { load(); }, []);

  const submit = async () => {
    if (!form.signature_data) return toast.error("Απαιτείται υπογραφή");
    if (!form.signer_name) return toast.error("Απαιτείται όνομα υπογράφοντα");
    try {
      await axios.post(`${API}/signatures`, form);
      toast.success("Η υπογραφή αποθηκεύτηκε");
      setOpen(false);
      setForm({ document_title: "", signer_name: "", signer_email: "", signer_role: "client", signature_data: "" });
      load();
    } catch (e) { toast.error(errMsg(e)); }
  };

  const verify = async (id) => {
    try {
      const { data } = await axios.post(`${API}/signatures/${id}/verify`);
      setVerifyResult({ id, ...data });
      toast[data.valid ? "success" : "error"](data.valid ? "Έγκυρη υπογραφή — ακεραιότητα επιβεβαιωμένη" : "Η υπογραφή έχει αλλοιωθεί!");
    } catch (e) { toast.error(errMsg(e)); }
  };

  return (
    <div className="space-y-8" data-testid="signatures-page">
      <div className="flex items-start justify-between">
        <div>
          <div className="lux-overline mb-1.5">Nomos One · Γραφείο</div>
          <h1 className="font-brand text-4xl font-bold tracking-wide">Ψηφιακές Υπογραφές</h1>
          <p className="text-sm text-muted-foreground mt-1">Ν.4727/2020 — Απλή Ηλεκτρονική Υπογραφή</p>
        </div>
        <Button onClick={() => setOpen(true)} data-testid="new-signature-button">
          <Signature size={16} className="mr-1.5" /> Νέα υπογραφή
        </Button>
      </div>

      <div className="border border-[hsl(var(--gold))]/25 bg-[hsl(var(--gold))]/5 rounded-xl p-4 text-xs text-muted-foreground flex gap-3">
        <Info size={18} className="shrink-0 text-[hsl(var(--gold))]" />
        <div>
          Ο πελάτης υπογράφει ψηφιακά (draw, touch ή pen). Αποθηκεύεται με SHA-256 hash, timestamp και IP address για
          τεκμηρίωση ακεραιότητας. Νομικά ισχυρή ως «απλή ηλεκτρονική υπογραφή» (Άρθρο 25 eIDAS + Ν.4727/2020).
        </div>
      </div>

      <div className="lux-card overflow-hidden">
        <Table data-testid="signatures-table">
          <TableHeader>
            <TableRow>
              <TableHead>Έγγραφο</TableHead>
              <TableHead>Υπογράφων</TableHead>
              <TableHead>Ρόλος</TableHead>
              <TableHead>Ημερομηνία</TableHead>
              <TableHead>SHA-256</TableHead>
              <TableHead className="text-right">Ενέργειες</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {signatures.map((s) => (
              <TableRow key={s.id} data-testid={`sig-row-${s.id}`}>
                <TableCell>{s.document_title || "—"}</TableCell>
                <TableCell>{s.signer_name}</TableCell>
                <TableCell><span className="text-xs">{s.signer_role}</span></TableCell>
                <TableCell className="text-xs font-mono">{new Date(s.signed_at).toLocaleString("el-GR")}</TableCell>
                <TableCell className="text-xs font-mono text-muted-foreground">{s.signature_hash?.slice(0, 12)}…</TableCell>
                <TableCell className="text-right">
                  <Button size="sm" variant="outline" onClick={() => verify(s.id)} data-testid={`verify-sig-${s.id}`}>
                    <CheckCircle size={14} className="mr-1" /> Επαλήθευση
                  </Button>
                </TableCell>
              </TableRow>
            ))}
            {signatures.length === 0 && (
              <TableRow><TableCell colSpan={6} className="text-center text-muted-foreground py-8">Δεν υπάρχουν υπογραφές ακόμα.</TableCell></TableRow>
            )}
          </TableBody>
        </Table>
      </div>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-w-lg" aria-describedby={undefined} data-testid="signature-dialog">
          <DialogHeader><DialogTitle>Καταχώρηση Ψηφιακής Υπογραφής</DialogTitle></DialogHeader>
          <div><Label>Έγγραφο / Τίτλος</Label><Input value={form.document_title} onChange={(e) => setForm({ ...form, document_title: e.target.value })} placeholder="π.χ. Εξουσιοδότηση για υπόθεση 123/2026" /></div>
          <div className="grid grid-cols-2 gap-3">
            <div><Label>Όνομα υπογράφοντα</Label><Input value={form.signer_name} onChange={(e) => setForm({ ...form, signer_name: e.target.value })} data-testid="signer-name-input" /></div>
            <div><Label>Email</Label><Input type="email" value={form.signer_email} onChange={(e) => setForm({ ...form, signer_email: e.target.value })} /></div>
          </div>
          <div>
            <Label>Ρόλος</Label>
            <Select value={form.signer_role} onValueChange={(v) => setForm({ ...form, signer_role: v })}>
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="client">Πελάτης</SelectItem>
                <SelectItem value="lawyer">Δικηγόρος</SelectItem>
                <SelectItem value="witness">Μάρτυρας</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div>
            <Label>Υπογραφή</Label>
            <SignatureCanvas onCapture={(d) => setForm({ ...form, signature_data: d || "" })} />
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setOpen(false)}>Ακύρωση</Button>
            <Button onClick={submit} data-testid="save-signature-button">Καταχώρηση</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
