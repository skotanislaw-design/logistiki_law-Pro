import { useEffect, useState } from "react";
import axios, { API, money, errMsg } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { ParavoloFormDialog, ParavoloIssueDialog } from "@/components/ParavoloDialogs";
import { Plus, Trash, PaperPlaneTilt, CheckCircle, ArrowSquareOut } from "@phosphor-icons/react";
import { toast } from "sonner";

const STATUS = {
  draft: { label: "Προσχέδιο", variant: "secondary" },
  issued: { label: "Εκδόθηκε", variant: "default" },
  paid: { label: "Πληρώθηκε", variant: "outline" },
  cancelled: { label: "Ακυρώθηκε", variant: "destructive" },
};

export default function Paravola() {
  const [items, setItems] = useState([]);
  const [showForm, setShowForm] = useState(false);
  const [issuing, setIssuing] = useState(null);

  const load = () => axios.get(`${API}/paravola`).then((r) => setItems(r.data)).catch(() => {});
  useEffect(() => { load(); }, []);

  const markPaid = async (p) => {
    try {
      await axios.post(`${API}/paravola/${p.id}/mark-paid`);
      toast.success(`Το παράβολο ${p.number} σημειώθηκε ως πληρωμένο`);
      load();
    } catch (e) {
      toast.error(errMsg(e));
    }
  };

  const remove = async (p) => {
    if (!window.confirm(`Διαγραφή παραβόλου ${p.number};`)) return;
    try {
      await axios.delete(`${API}/paravola/${p.id}`);
      toast.success("Διαγράφηκε");
      load();
    } catch (e) {
      toast.error(errMsg(e));
    }
  };

  return (
    <div className="space-y-8" data-testid="paravola-page">
      <div className="flex items-start justify-between animate-in-up">
        <div>
          <div className="lux-overline mb-1.5">Nomos One · Δικαστικά</div>
          <h1 className="font-brand text-4xl font-bold tracking-wide">Παράβολα</h1>
          <p className="text-sm text-muted-foreground mt-1">Διαχείριση e-Παραβόλων (ΓΓΠΣ) — έκδοση με επιβεβαίωση & παρακολούθηση πληρωμής</p>
        </div>
        <div className="flex gap-2">
          <a href="https://www1.gsis.gr/sgsisapps/eparavolo/public/welcome.htm" target="_blank" rel="noreferrer" data-testid="eparavolo-portal-link">
            <Button variant="outline"><ArrowSquareOut size={15} className="mr-1.5" /> e-Παράβολο (ΓΓΠΣ)</Button>
          </a>
          <Button onClick={() => setShowForm(true)} data-testid="new-paravolo-button"><Plus size={16} className="mr-1.5" /> Νέο Παράβολο</Button>
        </div>
      </div>

      <div className="lux-card overflow-hidden animate-in-up">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Αριθμός</TableHead>
              <TableHead>Κατηγορία</TableHead>
              <TableHead>Περιγραφή</TableHead>
              <TableHead>Υπόθεση</TableHead>
              <TableHead className="text-right">Ποσό</TableHead>
              <TableHead>Κατάσταση</TableHead>
              <TableHead className="text-right">Ενέργειες</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {items.length === 0 && (
              <TableRow><TableCell colSpan={7} className="text-center text-muted-foreground py-10">Δεν υπάρχουν παράβολα — δημιουργήστε το πρώτο</TableCell></TableRow>
            )}
            {items.map((p) => (
              <TableRow key={p.id} data-testid={`paravolo-row-${p.id}`}>
                <TableCell className="font-mono text-[hsl(var(--gold))]">{p.number}</TableCell>
                <TableCell className="max-w-[220px] truncate">{p.category}</TableCell>
                <TableCell className="text-muted-foreground text-xs max-w-[180px] truncate">{p.description || "—"}</TableCell>
                <TableCell className="text-muted-foreground text-xs">{p.case_title || "—"}</TableCell>
                <TableCell className="text-right font-mono">{money(p.amount)}</TableCell>
                <TableCell>
                  <Badge variant={STATUS[p.status]?.variant}>{STATUS[p.status]?.label}</Badge>
                  {p.eparavolo_code && <span className="block text-[10px] font-mono text-muted-foreground mt-0.5">{p.eparavolo_code}</span>}
                </TableCell>
                <TableCell className="text-right">
                  {p.status === "draft" && (
                    <Button variant="ghost" size="icon" title="Έκδοση" onClick={() => setIssuing(p)} data-testid={`issue-paravolo-${p.id}`}><PaperPlaneTilt size={16} /></Button>
                  )}
                  {p.status === "issued" && (
                    <Button variant="ghost" size="icon" title="Σήμανση ως πληρωμένο" onClick={() => markPaid(p)} data-testid={`pay-paravolo-${p.id}`}><CheckCircle size={16} /></Button>
                  )}
                  {p.status === "draft" && (
                    <Button variant="ghost" size="icon" title="Διαγραφή" onClick={() => remove(p)} data-testid={`delete-paravolo-${p.id}`}><Trash size={16} /></Button>
                  )}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>

      <ParavoloFormDialog open={showForm} onOpenChange={setShowForm} onSaved={load} />
      <ParavoloIssueDialog paravolo={issuing} open={!!issuing} onOpenChange={(v) => !v && setIssuing(null)} onIssued={load} />
    </div>
  );
}
