import { useEffect, useState } from "react";
import axios, { API, money, errMsg } from "@/lib/api";
import { DisclaimerBanner } from "@/components/DisclaimerBanner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { Badge } from "@/components/ui/badge";
import { Plus, Play, FileText, PencilSimple, Trash, Eye } from "@phosphor-icons/react";
import { toast } from "sonner";

function emptyForm() {
  return { name: "", afm: "", amka: "", position: "Δικηγόρος", gross_salary: 1500, dependents: 0, hire_date: "", active: true };
}

export default function Payroll() {
  const [employees, setEmployees] = useState([]);
  const [runs, setRuns] = useState([]);
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState(null);
  const [form, setForm] = useState(emptyForm());
  const [period, setPeriod] = useState(new Date().toISOString().slice(0, 7));
  const [viewingRun, setViewingRun] = useState(null);

  const load = () => {
    axios.get(`${API}/payroll/employees`).then((r) => setEmployees(r.data)).catch((e) => toast.error(errMsg(e)));
    axios.get(`${API}/payroll/runs`).then((r) => setRuns(r.data)).catch(() => {});
  };
  useEffect(() => { load(); }, []);

  const openNew = () => { setEditing(null); setForm(emptyForm()); setOpen(true); };
  const openEdit = (emp) => { setEditing(emp); setForm(emp); setOpen(true); };

  const handleSubmit = async () => {
    if (!form.name) return toast.error("Το όνομα είναι υποχρεωτικό");
    try {
      if (editing) {
        await axios.put(`${API}/payroll/employees/${editing.id}`, form);
        toast.success("Ο εργαζόμενος ενημερώθηκε");
      } else {
        await axios.post(`${API}/payroll/employees`, form);
        toast.success("Ο εργαζόμενος καταχωρήθηκε");
      }
      setOpen(false);
      load();
    } catch (e) {
      toast.error(errMsg(e));
    }
  };

  const toggleActive = async (emp) => {
    try {
      await axios.put(`${API}/payroll/employees/${emp.id}`, { ...emp, active: !emp.active });
      toast.success(emp.active ? "Ο εργαζόμενος έγινε ανενεργός" : "Ο εργαζόμενος ενεργοποιήθηκε");
      load();
    } catch (e) {
      toast.error(errMsg(e));
    }
  };

  const handleDelete = async (id) => {
    try {
      await axios.delete(`${API}/payroll/employees/${id}`);
      toast.success("Ο εργαζόμενος διαγράφηκε");
      load();
    } catch (e) {
      toast.error(errMsg(e));
    }
  };

  const runPayroll = async (employeeId) => {
    try {
      await axios.post(`${API}/payroll/run`, { employee_id: employeeId, period });
      toast.success("Η μισθοδοσία υπολογίστηκε");
      load();
    } catch (e) {
      toast.error(errMsg(e));
    }
  };

  return (
    <div className="space-y-8" data-testid="payroll-page">
      <div className="flex items-center justify-between">
        <div>
          <div className="lux-overline mb-1.5">Nomos One · Οικονομικά</div>
          <h1 className="font-brand text-4xl font-bold tracking-wide">Μισθοδοσία</h1>
          <p className="text-sm text-muted-foreground mt-1">Υπολογισμός μισθοδοσίας, ΕΦΚΑ & παρακρατούμενου φόρου</p>
        </div>
        <Button onClick={openNew} data-testid="new-employee-button"><Plus size={16} className="mr-1.5" /> Νέος Εργαζόμενος</Button>
      </div>

      <DisclaimerBanner compact />

      <Tabs defaultValue="employees">
        <TabsList>
          <TabsTrigger value="employees" data-testid="tab-employees">Εργαζόμενοι</TabsTrigger>
          <TabsTrigger value="runs" data-testid="tab-payslips">Μισθοδοτικές Καταστάσεις</TabsTrigger>
        </TabsList>

        <TabsContent value="employees" className="space-y-4">
          <div className="flex items-center gap-3">
            <Label className="text-sm">Περίοδος:</Label>
            <Input type="month" value={period} onChange={(e) => setPeriod(e.target.value)} className="w-40" data-testid="payroll-period-input" />
          </div>
          <div className="lux-card overflow-hidden animate-in-up">
            <Table data-testid="employees-table">
              <TableHeader>
                <TableRow>
                  <TableHead>Όνομα</TableHead>
                  <TableHead>Θέση</TableHead>
                  <TableHead className="text-right">Μικτός Μισθός</TableHead>
                  <TableHead>Κατάσταση</TableHead>
                  <TableHead className="text-right">Ενέργειες</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {employees.map((emp) => (
                  <TableRow key={emp.id} data-testid={`employee-row-${emp.id}`}>
                    <TableCell className="font-medium">{emp.name}</TableCell>
                    <TableCell>{emp.position}</TableCell>
                    <TableCell className="text-right font-mono">{money(emp.gross_salary)}</TableCell>
                    <TableCell>
                      <button onClick={() => toggleActive(emp)} data-testid={`toggle-active-${emp.id}`}>
                        <Badge variant={emp.active ? "default" : "secondary"}>{emp.active ? "Ενεργός" : "Ανενεργός"}</Badge>
                      </button>
                    </TableCell>
                    <TableCell className="text-right">
                      <Button size="sm" variant="outline" onClick={() => runPayroll(emp.id)} disabled={!emp.active} data-testid={`run-payroll-${emp.id}`}>
                        <Play size={14} className="mr-1.5" /> Υπολογισμός {period}
                      </Button>
                      <Button variant="ghost" size="icon" onClick={() => openEdit(emp)} data-testid={`edit-employee-${emp.id}`}><PencilSimple size={16} /></Button>
                      <Button variant="ghost" size="icon" onClick={() => handleDelete(emp.id)} data-testid={`delete-employee-${emp.id}`}><Trash size={16} /></Button>
                    </TableCell>
                  </TableRow>
                ))}
                {employees.length === 0 && (
                  <TableRow><TableCell colSpan={5} className="text-center text-muted-foreground py-8">Δεν υπάρχουν εργαζόμενοι ακόμα.</TableCell></TableRow>
                )}
              </TableBody>
            </Table>
          </div>
        </TabsContent>

        <TabsContent value="runs" className="space-y-4">
          <div className="lux-card overflow-hidden animate-in-up">
            <Table data-testid="payroll-runs-table">
              <TableHeader>
                <TableRow>
                  <TableHead>Εργαζόμενος</TableHead>
                  <TableHead>Περίοδος</TableHead>
                  <TableHead className="text-right">Μικτός</TableHead>
                  <TableHead className="text-right">ΕΦΚΑ Εργαζ.</TableHead>
                  <TableHead className="text-right">Φόρος</TableHead>
                  <TableHead className="text-right">Καθαρός</TableHead>
                  <TableHead className="text-right">Κόστος Εργοδότη</TableHead>
                  <TableHead className="text-right">Ενέργεια</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {runs.map((r) => (
                  <TableRow key={r.id} data-testid={`payroll-run-${r.id}`}>
                    <TableCell className="font-medium">{r.employee_name}</TableCell>
                    <TableCell>{r.period}</TableCell>
                    <TableCell className="text-right font-mono">{money(r.gross_salary)}</TableCell>
                    <TableCell className="text-right font-mono">{money(r.employee_efka)}</TableCell>
                    <TableCell className="text-right font-mono">{money(r.income_tax)}</TableCell>
                    <TableCell className="text-right font-mono font-semibold">{money(r.net_salary)}</TableCell>
                    <TableCell className="text-right font-mono text-muted-foreground">{money(r.employer_total_cost)}</TableCell>
                    <TableCell className="text-right">
                      <Button variant="ghost" size="icon" onClick={() => setViewingRun(r)} data-testid={`view-payslip-${r.id}`}><Eye size={16} /></Button>
                    </TableCell>
                  </TableRow>
                ))}
                {runs.length === 0 && (
                  <TableRow><TableCell colSpan={8} className="text-center text-muted-foreground py-8">
                    <FileText size={24} className="mx-auto mb-2 opacity-40" /> Δεν έχει υπολογιστεί μισθοδοσία ακόμα.
                  </TableCell></TableRow>
                )}
              </TableBody>
            </Table>
          </div>
        </TabsContent>
      </Tabs>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader><DialogTitle>{editing ? "Επεξεργασία Εργαζομένου" : "Νέος Εργαζόμενος"}</DialogTitle></DialogHeader>
          <div className="space-y-3">
            <div>
              <Label>Ονοματεπώνυμο</Label>
              <Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} className="mt-1.5" data-testid="employee-name-input" />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div><Label>ΑΦΜ</Label><Input value={form.afm} onChange={(e) => setForm({ ...form, afm: e.target.value })} className="mt-1.5" /></div>
              <div><Label>ΑΜΚΑ</Label><Input value={form.amka} onChange={(e) => setForm({ ...form, amka: e.target.value })} className="mt-1.5" /></div>
            </div>
            <div>
              <Label>Θέση</Label>
              <Input value={form.position} onChange={(e) => setForm({ ...form, position: e.target.value })} className="mt-1.5" />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <Label>Μικτός Μηνιαίος Μισθός</Label>
                <Input type="number" value={form.gross_salary} onChange={(e) => setForm({ ...form, gross_salary: Number(e.target.value) })} className="mt-1.5" data-testid="employee-salary-input" />
              </div>
              <div>
                <Label>Εξαρτώμενα Τέκνα</Label>
                <Input type="number" value={form.dependents} onChange={(e) => setForm({ ...form, dependents: Number(e.target.value) })} className="mt-1.5" />
              </div>
            </div>
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setOpen(false)}>Ακύρωση</Button>
            <Button onClick={handleSubmit} data-testid="save-employee-button">Αποθήκευση</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={!!viewingRun} onOpenChange={(v) => !v && setViewingRun(null)}>
        <DialogContent className="max-w-md" data-testid="payslip-detail-dialog">
          <DialogHeader><DialogTitle>Μισθοδοτική Κατάσταση — {viewingRun?.employee_name} ({viewingRun?.period})</DialogTitle></DialogHeader>
          {viewingRun && (
            <div className="space-y-1.5 text-sm">
              <div className="flex justify-between"><span className="text-muted-foreground">Μικτός Μισθός</span><span className="font-mono">{money(viewingRun.gross_salary)}</span></div>
              <div className="flex justify-between"><span className="text-muted-foreground">Εισφορά ΕΦΚΑ Εργαζομένου (13.87%)</span><span className="font-mono text-destructive">-{money(viewingRun.employee_efka)}</span></div>
              <div className="flex justify-between"><span className="text-muted-foreground">Φορολογητέο Εισόδημα</span><span className="font-mono">{money(viewingRun.taxable_income)}</span></div>
              <div className="flex justify-between"><span className="text-muted-foreground">Μείωση Φόρου</span><span className="font-mono text-[hsl(var(--success))]">+{money(viewingRun.tax_credit)}</span></div>
              <div className="flex justify-between"><span className="text-muted-foreground">Παρακρατούμενος Φόρος</span><span className="font-mono text-destructive">-{money(viewingRun.income_tax)}</span></div>
              <div className="flex justify-between text-base font-semibold border-t border-border pt-2 mt-1"><span>Καθαρές Απολαβές</span><span className="font-mono" data-testid="payslip-net-salary">{money(viewingRun.net_salary)}</span></div>
              <div className="flex justify-between text-xs text-muted-foreground pt-2 border-t border-border mt-2">
                <span>Εισφορά ΕΦΚΑ Εργοδότη (22.29%)</span><span className="font-mono">{money(viewingRun.employer_efka)}</span>
              </div>
              <div className="flex justify-between text-xs text-muted-foreground"><span>Συνολικό Κόστος Εργοδότη</span><span className="font-mono">{money(viewingRun.employer_total_cost)}</span></div>
            </div>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}
