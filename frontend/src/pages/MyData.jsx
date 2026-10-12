import { useEffect, useState } from "react";
import axios, { API, errMsg } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { InvoiceDetailDialog } from "@/components/InvoiceDetailDialog";
import { CloudCheck, CloudSlash, ArrowsClockwise, Scales, Eye } from "@phosphor-icons/react";
import { toast } from "sonner";

export default function MyData() {
  const [status, setStatus] = useState(null);
  const [invoices, setInvoices] = useState([]);
  const [syncing, setSyncing] = useState(null);
  const [viewing, setViewing] = useState(null);

  const load = () => {
    axios.get(`${API}/mydata/status`).then((r) => setStatus(r.data)).catch((e) => toast.error(errMsg(e)));
    axios.get(`${API}/invoices`).then((r) => setInvoices(r.data)).catch(() => {});
  };
  useEffect(load, []);

  const syncInvoice = async (id) => {
    setSyncing(id);
    try {
      const { data } = await axios.post(`${API}/mydata/sync/${id}`);
      toast.success(data.message);
      load();
    } catch (e) {
      toast.error(errMsg(e));
      load();
    } finally {
      setSyncing(null);
    }
  };

  if (!status) return <div className="text-muted-foreground">Φόρτωση...</div>;

  return (
    <div className="space-y-8" data-testid="mydata-page">
      <div>
        <div className="lux-overline mb-1.5">Nomos One · Συμμόρφωση</div>
        <h1 className="font-brand text-4xl font-bold tracking-wide">myDATA (ΑΑΔΕ)</h1>
        <p className="text-sm text-muted-foreground mt-1">Ηλεκτρονική διαβίβαση παραστατικών στην ΑΑΔΕ</p>
      </div>

      <div className="lux-card p-6 flex items-center justify-between" data-testid="mydata-connection-panel">
        <div className="flex items-center gap-3">
          {status.mode === "live" ? <CloudCheck size={28} className="text-[hsl(var(--success))]" /> : <CloudSlash size={28} className="text-warning" />}
          <div>
            <p className="font-medium flex items-center gap-2" data-testid="mydata-connection-status">
              {status.mode === "live" ? "Ζωντανή σύνδεση ΑΑΔΕ" : "Λειτουργία Προσομοίωσης (Mock)"}
              {status.mode === "live" && <Badge variant={status.environment === "production" ? "default" : "secondary"} data-testid="mydata-env-badge">{status.environment_label}</Badge>}
            </p>
            <p className="text-xs text-muted-foreground max-w-lg">{status.note}</p>
          </div>
        </div>
        <div className="flex gap-6 text-sm">
          <div className="text-center">
            <p className="font-mono text-xl" data-testid="mydata-pending-count">{status.pending_count}</p>
            <p className="text-xs text-muted-foreground">Εκκρεμή</p>
          </div>
          {status.failed_count > 0 && (
            <div className="text-center">
              <p className="font-mono text-xl text-destructive" data-testid="mydata-failed-count">{status.failed_count}</p>
              <p className="text-xs text-muted-foreground">Σφάλματα</p>
            </div>
          )}
          <div className="text-center">
            <p className="font-mono text-xl" data-testid="mydata-synced-count">{status.synced_count}</p>
            <p className="text-xs text-muted-foreground">Σταλμένα</p>
          </div>
        </div>
      </div>

      <div className="lux-card overflow-hidden animate-in-up">
        <Table data-testid="mydata-invoices-table">
          <TableHeader>
            <TableRow>
              <TableHead>Αριθμός</TableHead>
              <TableHead>Πελάτης</TableHead>
              <TableHead>MARK</TableHead>
              <TableHead>Κατάσταση</TableHead>
              <TableHead className="text-right">Ενέργεια</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {invoices.filter((i) => i.status !== "draft").map((inv) => (
              <TableRow key={inv.id}>
                <TableCell className="font-mono text-xs">{inv.number}</TableCell>
                <TableCell>{inv.client_name}</TableCell>
                <TableCell className="font-mono text-xs">{inv.mydata_mark || "-"}</TableCell>
                <TableCell>
                  <Badge variant={inv.mydata_status === "synced" ? "default" : inv.mydata_status === "failed" ? "destructive" : "secondary"}>
                    {inv.mydata_status === "synced" ? "Σταλμένο" : inv.mydata_status === "failed" ? "Σφάλμα" : "Εκκρεμεί"}
                  </Badge>
                  {inv.mydata_status === "failed" && inv.mydata_error && (
                    <span className="block text-[10px] text-destructive/80 max-w-[220px] truncate mt-0.5" title={inv.mydata_error} data-testid={`mydata-error-${inv.id}`}>{inv.mydata_error}</span>
                  )}
                </TableCell>
                <TableCell className="text-right">
                  {inv.mydata_status !== "synced" && (
                    <Button size="sm" variant="outline" onClick={() => syncInvoice(inv.id)} disabled={syncing === inv.id} data-testid={`sync-invoice-${inv.id}`}>
                      <ArrowsClockwise size={14} className={`mr-1.5 ${syncing === inv.id ? "animate-spin" : ""}`} /> Αποστολή
                    </Button>
                  )}
                  <Button variant="ghost" size="icon" onClick={() => setViewing(inv)} data-testid={`view-mydata-invoice-${inv.id}`}><Eye size={16} /></Button>
                </TableCell>
              </TableRow>
            ))}
            {invoices.filter((i) => i.status !== "draft").length === 0 && (
              <TableRow><TableCell colSpan={5} className="text-center text-muted-foreground py-8">Δεν υπάρχουν εκδοθέντα τιμολόγια.</TableCell></TableRow>
            )}
          </TableBody>
        </Table>
      </div>

      <div className="lux-card p-6 flex items-start gap-3">
        <Scales size={20} className="text-muted-foreground shrink-0" />
        <p className="text-sm text-muted-foreground">
          {status.mode === "live" ? (
            <>Η διαβίβαση γίνεται <strong>ζωντανά</strong> μέσω του myDATA REST API ({status.environment_label}). Τα MARK/UID είναι πραγματικοί αριθμοί καταχώρησης της ΑΑΔΕ.</>
          ) : (
            <>Για πραγματική σύνδεση με το myDATA, καταχωρήστε τα διαπιστευτήρια ΑΑΔΕ (User ID & Subscription Key) στις
            <strong> Ρυθμίσεις</strong>. Έως τότε, όλα τα MARK/UID που εμφανίζονται είναι <strong>MOCK</strong> δεδομένα προσομοίωσης.</>
          )}
        </p>
      </div>

      <InvoiceDetailDialog invoice={viewing} open={!!viewing} onOpenChange={(v) => !v && setViewing(null)} />
    </div>
  );
}
