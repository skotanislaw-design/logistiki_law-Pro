import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import axios, { API, dateFmt, errMsg } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Plus, Eye, Trash } from "@phosphor-icons/react";
import { toast } from "sonner";

export const CASE_STATUS = {
  active: { label: "Σε εξέλιξη", variant: "default" },
  pending: { label: "Σε αναμονή", variant: "secondary" },
  completed: { label: "Ολοκληρώθηκε", variant: "outline" },
  archived: { label: "Αρχειοθετήθηκε", variant: "destructive" },
};

const EMPTY = { title: "", case_type: "Αστικό", client_id: "", client_name: "", court: "", opposing_party: "", next_action: "", gak: "", eak: "", gak_year: "" };

export default function Cases() {
  const [cases, setCases] = useState([]);
  const [types, setTypes] = useState([]);
  const [clients, setClients] = useState([]);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState(EMPTY);
  const navigate = useNavigate();

  const load = () => axios.get(`${API}/cases`).then((r) => setCases(r.data)).catch(() => {});
  useEffect(() => {
    load();
    axios.get(`${API}/cases/types/list`).then((r) => setTypes(r.data)).catch(() => {});
    axios.get(`${API}/clients`).then((r) => setClients(r.data)).catch(() => {});
  }, []);

  const save = async () => {
    if (!form.title.trim()) return toast.error("Συμπληρώστε τίτλο υπόθεσης");
    try {
      const { data } = await axios.post(`${API}/cases`, form);
      toast.success(`Η υπόθεση ${data.number} δημιουργήθηκε`);
      setShowForm(false);
      setForm(EMPTY);
      navigate(`/cases/${data.id}`);
    } catch (e) {
      toast.error(errMsg(e));
    }
  };

  const remove = async (c, e) => {
    e.stopPropagation();
    if (!window.confirm(`Διαγραφή υπόθεσης ${c.number};`)) return;
    try {
      await axios.delete(`${API}/cases/${c.id}`);
      toast.success("Διαγράφηκε");
      load();
    } catch (err) {
      toast.error(errMsg(err));
    }
  };

  return (
    <div className="space-y-8" data-testid="cases-page">
      <div className="flex items-start justify-between animate-in-up">
        <div>
          <div className="lux-overline mb-1.5">Nomos One · Δικαστικά</div>
          <h1 className="font-brand text-4xl font-bold tracking-wide">Υποθέσεις</h1>
          <p className="text-sm text-muted-foreground mt-1">Διαχείριση υποθέσεων με γραμμάτια, παράβολα, σημειώσεις & ιστορικό</p>
        </div>
        <Button onClick={() => setShowForm(true)} data-testid="new-case-button"><Plus size={16} className="mr-1.5" /> Νέα Υπόθεση</Button>
      </div>

      <div className="lux-card overflow-hidden animate-in-up">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Αριθμός</TableHead>
              <TableHead>Τίτλος</TableHead>
              <TableHead>Είδος</TableHead>
              <TableHead>Πελάτης</TableHead>
              <TableHead>Δικαστήριο</TableHead>
              <TableHead>Κατάσταση</TableHead>
              <TableHead className="text-right">Ενέργειες</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {cases.length === 0 && (
              <TableRow><TableCell colSpan={7} className="text-center text-muted-foreground py-10">Δεν υπάρχουν υποθέσεις — δημιουργήστε την πρώτη</TableCell></TableRow>
            )}
            {cases.map((c) => (
              <TableRow key={c.id} className="cursor-pointer" onClick={() => navigate(`/cases/${c.id}`)} data-testid={`case-row-${c.id}`}>
                <TableCell className="font-mono text-[hsl(var(--gold))]">{c.number}</TableCell>
                <TableCell className="font-medium max-w-[240px] truncate">{c.title}</TableCell>
                <TableCell className="text-muted-foreground">{c.case_type}</TableCell>
                <TableCell>{c.client_name || "—"}</TableCell>
                <TableCell className="text-muted-foreground text-xs">{c.court || "—"}</TableCell>
                <TableCell><Badge variant={CASE_STATUS[c.status]?.variant}>{CASE_STATUS[c.status]?.label}</Badge></TableCell>
                <TableCell className="text-right">
                  <Button variant="ghost" size="icon" onClick={(e) => { e.stopPropagation(); navigate(`/cases/${c.id}`); }} data-testid={`view-case-${c.id}`}><Eye size={16} /></Button>
                  <Button variant="ghost" size="icon" onClick={(e) => remove(c, e)} data-testid={`delete-case-${c.id}`}><Trash size={16} /></Button>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>

      <Dialog open={showForm} onOpenChange={setShowForm}>
        <DialogContent className="max-w-lg" aria-describedby={undefined}>
          <DialogHeader><DialogTitle>Νέα Υπόθεση</DialogTitle></DialogHeader>
          <div className="space-y-4">
            <div>
              <Label>Τίτλος *</Label>
              <Input value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} className="mt-1.5" placeholder="π.χ. Αγωγή αποζημίωσης κατά Χ" data-testid="case-title-input" />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <Label>Είδος</Label>
                <Select value={form.case_type} onValueChange={(v) => setForm({ ...form, case_type: v })}>
                  <SelectTrigger className="mt-1.5" data-testid="case-type-select"><SelectValue /></SelectTrigger>
                  <SelectContent>{types.map((t) => <SelectItem key={t} value={t}>{t}</SelectItem>)}</SelectContent>
                </Select>
              </div>
              <div>
                <Label>Πελάτης</Label>
                <Select value={form.client_id} onValueChange={(v) => setForm({ ...form, client_id: v, client_name: clients.find((c) => c.id === v)?.name || "" })}>
                  <SelectTrigger className="mt-1.5" data-testid="case-client-select"><SelectValue placeholder="Επιλέξτε" /></SelectTrigger>
                  <SelectContent>{clients.map((c) => <SelectItem key={c.id} value={c.id}>{c.name}</SelectItem>)}</SelectContent>
                </Select>
              </div>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <Label>Δικαστήριο</Label>
                <Input value={form.court} onChange={(e) => setForm({ ...form, court: e.target.value })} className="mt-1.5" placeholder="π.χ. Πρωτοδικείο Αθηνών" data-testid="case-court-input" />
              </div>
              <div>
                <Label>Αντίδικος</Label>
                <Input value={form.opposing_party} onChange={(e) => setForm({ ...form, opposing_party: e.target.value })} className="mt-1.5" data-testid="case-opposing-input" />
              </div>
            </div>
            <div className="grid grid-cols-3 gap-3">
              <div>
                <Label>ΓΑΚ</Label>
                <Input value={form.gak} onChange={(e) => setForm({ ...form, gak: e.target.value })} className="mt-1.5" placeholder="π.χ. 12345" data-testid="case-gak-input" />
              </div>
              <div>
                <Label>ΕΑΚ</Label>
                <Input value={form.eak} onChange={(e) => setForm({ ...form, eak: e.target.value })} className="mt-1.5" placeholder="π.χ. 678" data-testid="case-eak-input" />
              </div>
              <div>
                <Label>Έτος</Label>
                <Input value={form.gak_year} onChange={(e) => setForm({ ...form, gak_year: e.target.value })} className="mt-1.5" placeholder="2026" data-testid="case-gak-year-input" />
              </div>
            </div>
            <div>
              <Label>Επόμενη ενέργεια</Label>
              <Input value={form.next_action} onChange={(e) => setForm({ ...form, next_action: e.target.value })} className="mt-1.5" placeholder="π.χ. Κατάθεση προτάσεων" />
            </div>
            <div className="flex justify-end gap-2">
              <Button variant="ghost" onClick={() => setShowForm(false)}>Ακύρωση</Button>
              <Button onClick={save} data-testid="save-case-button">Δημιουργία</Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
