import { useEffect, useState } from "react";
import axios, { API, money, errMsg } from "@/lib/api";
import { DisclaimerBanner } from "@/components/DisclaimerBanner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { Plus, Play, FileText } from "@phosphor-icons/react";
import { toast } from "sonner";

function emptyForm() {
  return { name: "", afm: "", amka: "", position: "Δικηγόρος", gross_salary: 1500, dependents: 0, hire_date: "" };
}

export default function Payroll() {
  const [employees, setEmployees] = useState([]);
  const [runs, setRuns] = useState([]);
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState(emptyForm());
  const [period, setPeriod] = useState(new Date().toISOString().slice(0, 7));

  const load = () => {
    axios.get(`${API}/payroll/employees`).then((r) => setEmployees(r.data)).catch((e) => toast.error(errMsg(e)));
    axios.get(`${API}/payroll/runs`).then((r) => setRuns(r.data)).catch(() => {});
  };
  useEffect(load, []);

  const handleSubmit = async () => {
    if (!form.name) return toast.error("Το όνομα είναι υποχρεωτικό");
    try {
      await axios.post(`${API}/payroll/employees`, form);
      toast.success("Ο εργαζόμενος καταχωρήθηκε");
      setOpen(false);
      setForm(emptyForm());
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
    <div className="space-y-6" data-testid="payroll-page">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="font-heading text-3xl font-bold tracking-tight">Μισθοδοσία</h1>
          <p className="text-sm text-muted-foreground mt-1">Υπολογισμός μισθοδοσίας, ΕΦΚΑ & παρακρατούμενου φόρου</p>
        </div>
        <Button onClick={() => setOpen(true)} data-testid="new-employee-button"><Plus size={16} className="mr-1.5" /> Νέος Εργαζόμενος</Button>
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
          <div className="border border-border rounded-sm bg-card overflow-hidden">
            <Table data-testid="employees-table">
              <TableHeader>
                <TableRow>
                  <TableHead>Όνομα</TableHead>
                  <TableHead>Θέση</TableHead>
                  <TableHead className="text-right">Μικτός Μισθός</TableHead>
                  <TableHead className="text-right">Ενέργεια</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {employees.map((emp) => (
                  <TableRow key={emp.id} data-testid={`employee-row-${emp.id}`}>
                    <TableCell className="font-medium">{emp.name}</TableCell>
                    <TableCell>{emp.position}</TableCell>
                    <TableCell className="text-right font-mono">{money(emp.gross_salary)}</TableCell>
                    <TableCell className="text-right">
                      <Button size="sm" variant="outline" onClick={() => runPayroll(emp.id)} data-testid={`run-payroll-${emp.id}`}>
                        <Play size={14} className="mr-1.5" /> Υπολογισμός {period}
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
                {employees.length === 0 && (
                  <TableRow><TableCell colSpan={4} className="text-center text-muted-foreground py-8">Δεν υπάρχουν εργαζόμενοι ακόμα.</TableCell></TableRow>
                )}
              </TableBody>
            </Table>
          </div>
        </TabsContent>

        <TabsContent value="runs" className="space-y-4">
          <div className="border border-border rounded-sm bg-card overflow-hidden">
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
                  </TableRow>
                ))}
                {runs.length === 0 && (
                  <TableRow><TableCell colSpan={7} className="text-center text-muted-foreground py-8">
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
          <DialogHeader><DialogTitle>Νέος Εργαζόμενος</DialogTitle></DialogHeader>
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
    </div>
  );
}
