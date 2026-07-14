import { useEffect, useState } from "react";
import axios, { API, money, errMsg } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { CloudCheck, CloudSlash, ArrowsClockwise, Scales } from "@phosphor-icons/react";
import { toast } from "sonner";

export default function MyData() {
  const [status, setStatus] = useState(null);
  const [invoices, setInvoices] = useState([]);
  const [syncing, setSyncing] = useState(null);

  const load = () => {
    axios.get(`${API}/mydata/status`).then((r) => setStatus(r.data)).catch((e) => toast.error(errMsg(e)));
    axios.get(`${API}/invoices`).then((r) => setInvoices(r.data)).catch(() => {});
  };
  useEffect(load, []);

  const syncInvoice = async (id) => {
    setSyncing(id);
    try {
      await axios.post(`${API}/mydata/sync/${id}`);
      toast.success("Στάλθηκε (προσομοίωση) στο myDATA");
      load();
    } catch (e) {
      toast.error(errMsg(e));
    } finally {
      setSyncing(null);
    }
  };

  if (!status) return <div className="text-muted-foreground">Φόρτωση...</div>;

  return (
    <div className="space-y-6" data-testid="mydata-page">
      <div>
        <h1 className="font-heading text-3xl font-bold tracking-tight">myDATA (ΑΑΔΕ)</h1>
        <p className="text-sm text-muted-foreground mt-1">Ηλεκτρονική διαβίβαση παραστατικών στην ΑΑΔΕ</p>
      </div>

      <div className="border border-border rounded-sm p-5 bg-card flex items-center justify-between" data-testid="mydata-connection-panel">
        <div className="flex items-center gap-3">
          {status.connected ? <CloudCheck size={28} className="text-[hsl(var(--success))]" /> : <CloudSlash size={28} className="text-warning" />}
          <div>
            <p className="font-medium" data-testid="mydata-connection-status">{status.connected ? "Συνδεδεμένο (Mock)" : "Λειτουργία Προσομοίωσης (Mock)"}</p>
            <p className="text-xs text-muted-foreground max-w-lg">{status.note}</p>
          </div>
        </div>
        <div className="flex gap-6 text-sm">
          <div className="text-center">
            <p className="font-mono text-xl" data-testid="mydata-pending-count">{status.pending_count}</p>
            <p className="text-xs text-muted-foreground">Εκκρεμή</p>
          </div>
          <div className="text-center">
            <p className="font-mono text-xl" data-testid="mydata-synced-count">{status.synced_count}</p>
            <p className="text-xs text-muted-foreground">Σταλμένα</p>
          </div>
        </div>
      </div>

      <div className="border border-border rounded-sm bg-card overflow-hidden">
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
                  <Badge variant={inv.mydata_status === "synced" ? "default" : "secondary"}>
                    {inv.mydata_status === "synced" ? "Σταλμένο" : "Εκκρεμεί"}
                  </Badge>
                </TableCell>
                <TableCell className="text-right">
                  {inv.mydata_status !== "synced" && (
                    <Button size="sm" variant="outline" onClick={() => syncInvoice(inv.id)} disabled={syncing === inv.id} data-testid={`sync-invoice-${inv.id}`}>
                      <ArrowsClockwise size={14} className={`mr-1.5 ${syncing === inv.id ? "animate-spin" : ""}`} /> Αποστολή
                    </Button>
                  )}
                </TableCell>
              </TableRow>
            ))}
            {invoices.filter((i) => i.status !== "draft").length === 0 && (
              <TableRow><TableCell colSpan={5} className="text-center text-muted-foreground py-8">Δεν υπάρχουν εκδοθέντα τιμολόγια.</TableCell></TableRow>
            )}
          </TableBody>
        </Table>
      </div>

      <div className="border border-border rounded-sm p-5 bg-card flex items-start gap-3">
        <Scales size={20} className="text-muted-foreground shrink-0" />
        <p className="text-sm text-muted-foreground">
          Για πραγματική σύνδεση με το myDATA, καταχωρήστε τα διαπιστευτήρια ΑΑΔΕ (User ID & Subscription Key) στις
          <strong> Ρυθμίσεις</strong>. Έως τότε, όλα τα MARK/UID που εμφανίζονται είναι <strong>MOCK</strong> δεδομένα προσομοίωσης.
        </p>
      </div>
    </div>
  );
}
