import { useEffect, useState } from "react";
import axios, { API, errMsg } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog";
import { Plus, PencilSimple, Trash, Link as LinkIcon, DownloadSimple } from "@phosphor-icons/react";
import { toast } from "sonner";

// Ο έλεγχος ψηφίου ελέγχου ΑΦΜ αφορά μόνο ελληνικά ΑΦΜ. Χώρες που συναντά στην πράξη
// ένα δικηγορικό γραφείο· η λίστα επεκτείνεται χωρίς άλλη αλλαγή.
const COUNTRIES = [
  ["GR", "Ελλάδα"], ["CY", "Κύπρος"], ["DE", "Γερμανία"], ["GB", "Ηνωμένο Βασίλειο"],
  ["FR", "Γαλλία"], ["IT", "Ιταλία"], ["NL", "Ολλανδία"], ["BE", "Βέλγιο"],
  ["BG", "Βουλγαρία"], ["RO", "Ρουμανία"], ["ES", "Ισπανία"], ["US", "ΗΠΑ"],
];

function emptyForm() {
  return { name: "", afm: "", doy: "", address: "", email: "", phone: "",
           client_type: "individual", country: "GR" };
}

export default function Clients() {
  const [clients, setClients] = useState([]);
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState(null);
  const [form, setForm] = useState(emptyForm());

  const load = () => axios.get(`${API}/clients`).then((r) => setClients(r.data)).catch((e) => toast.error(errMsg(e)));
  useEffect(() => { load(); }, []);

  const openNew = () => { setEditing(null); setForm(emptyForm()); setOpen(true); };
  const openEdit = (c) => { setEditing(c); setForm({ ...c, country: c.country || "GR" }); setOpen(true); };

  const handleSubmit = async () => {
    if (!form.name) return toast.error("Το όνομα είναι υποχρεωτικό");
    try {
      if (editing) {
        await axios.put(`${API}/clients/${editing.id}`, form);
        toast.success("Ο πελάτης ενημερώθηκε");
      } else {
        await axios.post(`${API}/clients`, form);
        toast.success("Ο πελάτης δημιουργήθηκε");
      }
      setOpen(false);
      load();
    } catch (e) {
      toast.error(errMsg(e));
    }
  };

  const handleDelete = async (id) => {
    try {
      await axios.delete(`${API}/clients/${id}`);
      toast.success("Ο πελάτης διαγράφηκε");
      load();
    } catch (e) {
      toast.error(errMsg(e));
    }
  };

  const inviteToPortal = async (c) => {
    try {
      const { data } = await axios.post(`${API}/client-portal/invite`, {
        client_id: c.id,
        origin_url: window.location.origin,
      });
      const message = data.message || "Δημιουργήθηκε σύνδεσμος";
      let copied = false;
      try {
        await navigator.clipboard.writeText(data.portal_link);
        copied = true;
      } catch {
        // Άρνηση δικαιώματος ή μη ασφαλές περιβάλλον: ο σύνδεσμος πρέπει να φανεί, αλλιώς χάνεται.
      }
      if (copied) toast.success(`${message} · Ο σύνδεσμος αντιγράφηκε στο clipboard.`);
      else toast.warning(`${message} · Η αυτόματη αντιγραφή δεν ήταν δυνατή — αντιγράψτε τον σύνδεσμο: ${data.portal_link}`, { duration: 60000 });
    } catch (e) { toast.error(errMsg(e)); }
  };

  const gdprExport = async (c) => {
    try {
      const res = await axios.get(`${API}/backup/gdpr/client/${c.id}`, { responseType: "blob" });
      const url = URL.createObjectURL(res.data);
      const a = document.createElement("a");
      a.href = url; a.download = `gdpr-${c.name}.zip`;
      document.body.appendChild(a); a.click(); a.remove();
      URL.revokeObjectURL(url);
      toast.success("Το GDPR export ολοκληρώθηκε");
    } catch (e) { toast.error(errMsg(e)); }
  };

  return (
    <div className="space-y-8" data-testid="clients-page">
      <div className="flex items-center justify-between">
        <div>
          <div className="lux-overline mb-1.5">Nomos One · Γραφείο</div>
          <h1 className="font-brand text-4xl font-bold tracking-wide">Πελάτες</h1>
          <p className="text-sm text-muted-foreground mt-1">Διαχείριση πελατών της δικηγορικής εταιρείας</p>
        </div>
        <Button onClick={openNew} data-testid="new-client-button"><Plus size={16} className="mr-1.5" /> Νέος Πελάτης</Button>
      </div>

      <div className="lux-card overflow-hidden animate-in-up">
        <Table data-testid="clients-table">
          <TableHeader>
            <TableRow>
              <TableHead>Όνομα</TableHead>
              <TableHead>ΑΦΜ</TableHead>
              <TableHead>Τύπος</TableHead>
              <TableHead>Email</TableHead>
              <TableHead>Τηλέφωνο</TableHead>
              <TableHead className="text-right">Ενέργειες</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {clients.map((c) => (
              <TableRow key={c.id} data-testid={`client-row-${c.id}`}>
                <TableCell className="font-medium">{c.name}</TableCell>
                <TableCell className="font-mono text-xs">{c.afm || "-"}</TableCell>
                <TableCell>{c.client_type === "company" ? "Εταιρεία" : "Ιδιώτης"}</TableCell>
                <TableCell>{c.email || "-"}</TableCell>
                <TableCell>{c.phone || "-"}</TableCell>
                <TableCell className="text-right">
                  <Button variant="ghost" size="icon" onClick={() => inviteToPortal(c)} title="Σύνδεσμος πελατολογικού portal" data-testid={`invite-portal-${c.id}`}><LinkIcon size={16} /></Button>
                  <Button variant="ghost" size="icon" onClick={() => gdprExport(c)} title="GDPR export (ΓΚΠΔ Άρθρο 15)" data-testid={`gdpr-${c.id}`}><DownloadSimple size={16} /></Button>
                  <Button variant="ghost" size="icon" onClick={() => openEdit(c)} data-testid={`edit-client-${c.id}`}><PencilSimple size={16} /></Button>
                  <Button variant="ghost" size="icon" onClick={() => handleDelete(c.id)} data-testid={`delete-client-${c.id}`}><Trash size={16} /></Button>
                </TableCell>
              </TableRow>
            ))}
            {clients.length === 0 && (
              <TableRow><TableCell colSpan={6} className="text-center text-muted-foreground py-8">Δεν υπάρχουν πελάτες ακόμα.</TableCell></TableRow>
            )}
          </TableBody>
        </Table>
      </div>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader><DialogTitle>{editing ? "Επεξεργασία Πελάτη" : "Νέος Πελάτης"}</DialogTitle></DialogHeader>
          <div className="space-y-3">
            <div>
              <Label>Όνομα / Επωνυμία</Label>
              <Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} className="mt-1.5" data-testid="client-name-input" />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <Label>ΑΦΜ</Label>
                <Input value={form.afm} onChange={(e) => setForm({ ...form, afm: e.target.value })} className="mt-1.5" />
              </div>
              <div>
                <Label>ΔΟΥ</Label>
                <Input value={form.doy} onChange={(e) => setForm({ ...form, doy: e.target.value })} className="mt-1.5" />
              </div>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <Label>Τύπος Πελάτη</Label>
                <Select value={form.client_type} onValueChange={(v) => setForm({ ...form, client_type: v })}>
                  <SelectTrigger className="mt-1.5"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="individual">Ιδιώτης</SelectItem>
                    <SelectItem value="company">Εταιρεία</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div>
                <Label>Χώρα</Label>
                <Select value={form.country || "GR"} onValueChange={(v) => setForm({ ...form, country: v })}>
                  <SelectTrigger className="mt-1.5"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {COUNTRIES.map(([code, label]) => (
                      <SelectItem key={code} value={code}>{label}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>
            <div>
              <Label>Διεύθυνση</Label>
              <Input value={form.address} onChange={(e) => setForm({ ...form, address: e.target.value })} className="mt-1.5" />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <Label>Email</Label>
                <Input value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} className="mt-1.5" />
              </div>
              <div>
                <Label>Τηλέφωνο</Label>
                <Input value={form.phone} onChange={(e) => setForm({ ...form, phone: e.target.value })} className="mt-1.5" />
              </div>
            </div>
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setOpen(false)}>Ακύρωση</Button>
            <Button onClick={handleSubmit} data-testid="save-client-button">Αποθήκευση</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
