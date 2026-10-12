import { useEffect, useState } from "react";
import axios, { API, money, dateFmt, errMsg } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { GrammatioFormDialog, GrammatioIssueDialog } from "@/components/GrammatioDialogs";
import { Plus, FilePdf, Trash, PaperPlaneTilt, FileText, ArrowSquareOut } from "@phosphor-icons/react";
import { toast } from "sonner";

const STATUS = {
  draft: { label: "Προσχέδιο", variant: "secondary" },
  issued: { label: "Εκδόθηκε", variant: "default" },
  cancelled: { label: "Ακυρώθηκε", variant: "destructive" },
};

export default function Grammatia() {
  const [items, setItems] = useState([]);
  const [showForm, setShowForm] = useState(false);
  const [issuing, setIssuing] = useState(null);

  const load = () => axios.get(`${API}/grammatia`).then((r) => setItems(r.data)).catch(() => {});
  useEffect(() => { load(); }, []);

  const downloadPdf = async (g) => {
    try {
      const res = await axios.get(`${API}/grammatia/${g.id}/pdf`, { responseType: "blob" });
      const url = URL.createObjectURL(res.data);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${g.number}.pdf`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (e) {
      toast.error(errMsg(e));
    }
  };

  const toInvoice = async (g) => {
    try {
      await axios.post(`${API}/grammatia/${g.id}/to-invoice`);
      toast.success("Δημιουργήθηκε πρόχειρο τιμολόγιο με το γραμμάτιο — ελέγξτε το ΦΠΑ και εκδώστε το από τα Τιμολόγια");
      load();
    } catch (e) {
      toast.error(errMsg(e));
    }
  };

  const remove = async (g) => {
    if (!window.confirm(`Διαγραφή γραμματίου ${g.number};`)) return;
    try {
      await axios.delete(`${API}/grammatia/${g.id}`);
      toast.success("Διαγράφηκε");
      load();
    } catch (e) {
      toast.error(errMsg(e));
    }
  };

  return (
    <div className="space-y-8" data-testid="grammatia-page">
      <div className="flex items-start justify-between animate-in-up">
        <div>
          <div className="lux-overline mb-1.5">Nomos One · Δικαστικά</div>
          <h1 className="font-brand text-4xl font-bold tracking-wide">Γραμμάτια Προείσπραξης</h1>
          <p className="text-sm text-muted-foreground mt-1">Υπολογισμός, έκδοση (μέσω portal.olomeleia.gr) και ενσωμάτωση σε τιμολόγια</p>
        </div>
        <div className="flex gap-2">
          <a href="https://portal.olomeleia.gr" target="_blank" rel="noreferrer" data-testid="olomeleia-portal-link">
            <Button variant="outline"><ArrowSquareOut size={15} className="mr-1.5" /> portal.olomeleia.gr</Button>
          </a>
          <Button onClick={() => setShowForm(true)} data-testid="new-grammatio-button"><Plus size={16} className="mr-1.5" /> Νέο Γραμμάτιο</Button>
        </div>
      </div>

      <div className="lux-card overflow-hidden animate-in-up">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Αριθμός</TableHead>
              <TableHead>Δικαστήριο / Διαδικασία</TableHead>
              <TableHead>Πελάτης</TableHead>
              <TableHead>Υπόθεση</TableHead>
              <TableHead className="text-right">Σύνολο</TableHead>
              <TableHead>Κατάσταση</TableHead>
              <TableHead className="text-right">Ενέργειες</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {items.length === 0 && (
              <TableRow><TableCell colSpan={7} className="text-center text-muted-foreground py-10">Δεν υπάρχουν γραμμάτια — δημιουργήστε το πρώτο</TableCell></TableRow>
            )}
            {items.map((g) => (
              <TableRow key={g.id} data-testid={`grammatio-row-${g.id}`}>
                <TableCell className="font-mono text-[hsl(var(--gold))]">{g.number}</TableCell>
                <TableCell>{g.court_type}<span className="text-muted-foreground text-xs block">{g.procedure}</span></TableCell>
                <TableCell>{g.client_name || "—"}</TableCell>
                <TableCell className="text-muted-foreground text-xs">{g.case_title || "—"}</TableCell>
                <TableCell className="text-right font-mono">{money(g.total)}</TableCell>
                <TableCell>
                  <Badge variant={STATUS[g.status]?.variant}>{STATUS[g.status]?.label}</Badge>
                  {g.portal_ref && <span className="block text-[10px] text-muted-foreground mt-0.5">portal: {g.portal_ref}</span>}
                </TableCell>
                <TableCell className="text-right">
                  {g.status === "draft" && (
                    <Button variant="ghost" size="icon" title="Έκδοση" onClick={() => setIssuing(g)} data-testid={`issue-grammatio-${g.id}`}><PaperPlaneTilt size={16} /></Button>
                  )}
                  <Button variant="ghost" size="icon" title="PDF" onClick={() => downloadPdf(g)} data-testid={`pdf-grammatio-${g.id}`}><FilePdf size={16} /></Button>
                  {g.status === "issued" && !g.invoice_id && (
                    <Button variant="ghost" size="icon" title="Ενσωμάτωση σε τιμολόγιο" onClick={() => toInvoice(g)} data-testid={`invoice-grammatio-${g.id}`}><FileText size={16} /></Button>
                  )}
                  {g.status === "draft" && (
                    <Button variant="ghost" size="icon" title="Διαγραφή" onClick={() => remove(g)} data-testid={`delete-grammatio-${g.id}`}><Trash size={16} /></Button>
                  )}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>

      <p className="text-[11px] text-muted-foreground/70">
        Τα ποσά αποτελούν ποσά αναφοράς (Ν.4194/2013, Παράρτημα Ι) και είναι πλήρως επεξεργάσιμα. Η επίσημη έκδοση γίνεται μέσω portal.olomeleia.gr — πάντα με επιβεβαίωση πριν το τελικό βήμα.
      </p>

      <GrammatioFormDialog open={showForm} onOpenChange={setShowForm} onSaved={load} />
      <GrammatioIssueDialog grammatio={issuing} open={!!issuing} onOpenChange={(v) => !v && setIssuing(null)} onIssued={load} />
    </div>
  );
}
