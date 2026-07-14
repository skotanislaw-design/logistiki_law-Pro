import { useEffect, useState } from "react";
import axios, { API, errMsg } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog";
import { Plus, PencilSimple, Trash } from "@phosphor-icons/react";
import { toast } from "sonner";

function emptyForm() {
  return { name: "", afm: "", doy: "", address: "", email: "", phone: "", client_type: "individual" };
}

export default function Clients() {
  const [clients, setClients] = useState([]);
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState(null);
  const [form, setForm] = useState(emptyForm());

  const load = () => axios.get(`${API}/clients`).then((r) => setClients(r.data)).catch((e) => toast.error(errMsg(e)));
  useEffect(load, []);

  const openNew = () => { setEditing(null); setForm(emptyForm()); setOpen(true); };
  const openEdit = (c) => { setEditing(c); setForm(c); setOpen(true); };

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

  return (
    <div className="space-y-6" data-testid="clients-page">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="font-heading text-3xl font-bold tracking-tight">Πελάτες</h1>
          <p className="text-sm text-muted-foreground mt-1">Διαχείριση πελατών της δικηγορικής εταιρείας</p>
        </div>
        <Button onClick={openNew} data-testid="new-client-button"><Plus size={16} className="mr-1.5" /> Νέος Πελάτης</Button>
      </div>

      <div className="border border-border rounded-sm bg-card overflow-hidden">
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
