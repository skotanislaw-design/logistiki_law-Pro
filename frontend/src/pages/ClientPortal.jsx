import { useCallback, useEffect, useState } from "react";
import { useParams, useLocation } from "react-router-dom";
import axios from "axios";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { FilePdf, CreditCard, Briefcase, WarningCircle, CheckCircle, Scales, Spinner, XCircle } from "@phosphor-icons/react";
import { toast } from "sonner";

const API = process.env.REACT_APP_BACKEND_URL + "/api";
const money = (v) => `${(v || 0).toFixed(2).replace(".", ",")} €`;
const dateFmt = (s) => s ? new Date(s).toLocaleDateString("el-GR") : "—";
const STATUS = { issued: "Ανεξόφλητο", paid: "Πληρωμένο", cancelled: "Ακυρωμένο" };
const STATUS_STYLE = { issued: "text-[hsl(var(--gold))]", paid: "text-[hsl(var(--success))]", cancelled: "text-muted-foreground" };

const NOTICE = {
  checking: { icon: "spin", tone: "", title: "Επιβεβαίωση της πληρωμής σας…", text: "Παρακαλούμε περιμένετε λίγα δευτερόλεπτα." },
  failed: {
    icon: "bad", tone: "border-[hsl(var(--destructive))]/40", title: "Η πληρωμή δεν ολοκληρώθηκε",
    text: "Αν έχει γίνει χρέωση στην κάρτα σας, επικοινωνήστε με το γραφείο. Μπορείτε επίσης να δοκιμάσετε ξανά από τον κατάλογο τιμολογίων.",
  },
  timeout: {
    icon: "wait", tone: "", title: "Η πληρωμή σας επεξεργάζεται ακόμη",
    text: "Το τιμολόγιο θα εμφανιστεί ως εξοφλημένο μόλις επιβεβαιωθεί η πληρωμή. Ανανεώστε τη σελίδα σε λίγο.",
  },
  error: {
    icon: "wait", tone: "", title: "Δεν ήταν δυνατή η επιβεβαίωση της πληρωμής αυτή τη στιγμή",
    text: "Ανανεώστε τη σελίδα σε λίγο ή επικοινωνήστε με το γραφείο.",
  },
  cancelled: {
    icon: "bad", tone: "", title: "Η πληρωμή ακυρώθηκε",
    text: "Δεν έγινε καμία χρέωση. Μπορείτε να δοκιμάσετε ξανά όποτε θέλετε.",
  },
  unverified: {
    icon: "wait", tone: "", title: "Επιστρέψατε από τη σελίδα πληρωμής",
    text: "Η κατάσταση των τιμολογίων παρακάτω είναι η τρέχουσα. Αν πληρώσατε πριν από λίγο, ανανεώστε τη σελίδα σε λίγο.",
  },
};

function PaymentNotice({ payment, cancelled, returnedWithoutSession }) {
  let key = null;
  let body = null;
  if (payment?.state === "paid") {
    key = "paid";
    body = (
      <>
        <CheckCircle size={24} className="text-[hsl(var(--success))]" weight="fill" />
        <div>
          <div className="font-medium">Η πληρωμή σας ολοκληρώθηκε επιτυχώς</div>
          <p className="text-xs text-muted-foreground">
            {payment.invoice ? `Τιμολόγιο ${payment.invoice}` : "Το τιμολόγιο"}{payment.amount != null ? ` · ${money(payment.amount)}` : ""}. Ευχαριστούμε!
          </p>
        </div>
      </>
    );
  } else {
    key = payment?.state || (cancelled ? "cancelled" : returnedWithoutSession ? "unverified" : null);
    if (!key) return null;
    const n = NOTICE[key];
    body = (
      <>
        {n.icon === "spin" ? <Spinner size={24} className="animate-spin text-[hsl(var(--gold))]" />
          : n.icon === "bad" ? <XCircle size={24} className="text-[hsl(var(--destructive))]" />
          : <WarningCircle size={24} className="text-[hsl(var(--gold))]" />}
        <div>
          <div className="font-medium">{n.title}</div>
          <p className="text-xs text-muted-foreground">{n.text}</p>
        </div>
      </>
    );
  }
  const tone = key === "paid" ? "border-[hsl(var(--success))]/40" : (NOTICE[key]?.tone || "");
  return <div className={`lux-card p-4 flex items-center gap-3 ${tone}`} data-testid={`portal-payment-${key}`} role="status">{body}</div>;
}

// Μετά την πληρωμή το Stripe επιστρέφει στη σελίδα της πύλης με `?paid=1&session_id=…` (ή `?cancelled=1`). Η επιστροφή δεν
// αποδεικνύει ότι η πληρωμή εφαρμόστηκε στο τιμολόγιο (το webhook μπορεί να καθυστερεί): η σελίδα ρωτά τον server για τη
// συνεδρία, κάθε `pollMs`, έως `maxPolls` φορές, και μόνο όταν επιβεβαιωθεί δείχνει επιτυχία.
const goTo = (url) => { window.location.href = url; };

export default function ClientPortal({ pollMs = 2000, maxPolls = 15, redirect = goTo }) {
  const { token } = useParams();
  const location = useLocation();
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [paying, setPaying] = useState(null);
  // Οι παράμετροι επιστροφής διαβάζονται μία φορά, κατά την άφιξη στη σελίδα.
  const [returned] = useState(() => {
    const params = new URLSearchParams(location.search);
    return {
      sessionId: params.get("session_id") || "",
      paid: params.get("paid") === "1",
      cancelled: params.get("cancelled") === "1",
    };
  });
  const [payment, setPayment] = useState(returned.paid && returned.sessionId ? { state: "checking" } : null);

  const load = useCallback(() => {
    axios.get(`${API}/portal/${token}`)
      .then((r) => setData(r.data))
      .catch((e) => setError(e.response?.data?.detail || "Ο σύνδεσμος δεν είναι έγκυρος"));
  }, [token]);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    if (!returned.paid || !returned.sessionId) return undefined;
    let stopped = false;
    let timer = null;
    let attempts = 0;
    const poll = async () => {
      try {
        const { data: tx } = await axios.get(`${API}/payments/status/${returned.sessionId}`);
        if (stopped) return;
        if (tx.payment_status === "paid") {
          setPayment({ state: "paid", invoice: tx.invoice_number, amount: tx.amount });
          load();                                   // το τιμολόγιο εμφανίζεται πλέον ως εξοφλημένο
          return;
        }
        if (tx.payment_status === "failed" || tx.payment_status === "expired") {
          setPayment({ state: "failed" });
          return;
        }
      } catch {
        if (!stopped) setPayment({ state: "error" });
        return;
      }
      attempts += 1;
      if (attempts < maxPolls) timer = setTimeout(poll, pollMs);
      else setPayment({ state: "timeout" });
    };
    poll();
    return () => { stopped = true; clearTimeout(timer); };
  }, [returned, load, pollMs, maxPolls]);

  // Πίσω από τη σελίδα πληρωμής του Stripe ο browser μπορεί να επαναφέρει την πύλη από την προσωρινή μνήμη, με το κουμπί
  // «Πληρωμή» ακόμη απενεργοποιημένο.
  useEffect(() => {
    const reset = (e) => { if (e.persisted) setPaying(null); };
    window.addEventListener("pageshow", reset);
    return () => window.removeEventListener("pageshow", reset);
  }, []);

  const downloadPdf = (invoiceId, number) => {
    const url = `${API}/portal/${token}/invoice/${invoiceId}/pdf`;
    const a = document.createElement("a");
    a.href = url; a.download = `${number || "invoice"}.pdf`; a.target = "_blank";
    document.body.appendChild(a); a.click(); a.remove();
  };

  const payInvoice = async (invoiceId) => {
    setPaying(invoiceId);
    try {
      const origin = window.location.origin;
      const { data: res } = await axios.post(`${process.env.REACT_APP_BACKEND_URL}/api/payments/portal-checkout`, {
        invoice_id: invoiceId,
        origin_url: `${origin}/portal/${token}`,
        client_token: token,
      });
      redirect(res.checkout_url);
    } catch (e) {
      toast.error(e.response?.data?.detail || "Αποτυχία δημιουργίας πληρωμής");
      setPaying(null);
    }
  };

  if (error) {
    return (
      <div className="min-h-screen flex items-center justify-center p-6" data-testid="portal-error">
        <div className="lux-card p-10 max-w-md text-center">
          <WarningCircle size={48} className="mx-auto text-[hsl(var(--destructive))] mb-4" />
          <h1 className="font-brand text-2xl">Δεν ήταν δυνατή η πρόσβαση</h1>
          <p className="text-sm text-muted-foreground mt-2">{error}</p>
          <p className="text-xs text-muted-foreground mt-4">Παρακαλούμε επικοινωνήστε με το γραφείο για νέο σύνδεσμο.</p>
        </div>
      </div>
    );
  }
  if (!data) return <div className="min-h-screen flex items-center justify-center text-muted-foreground">Φόρτωση...</div>;

  return (
    <div className="min-h-screen p-6" data-testid="portal-page">
      <div className="max-w-5xl mx-auto space-y-6">
        <div className="lux-card p-6">
          <div className="flex items-center justify-between">
            <div>
              <div className="lux-overline">{data.firm.name}</div>
              <h1 className="font-brand text-3xl font-bold mt-1">Ο φάκελός σας</h1>
              <p className="text-sm text-muted-foreground mt-1">Καλωσορίσατε, {data.client.name}</p>
            </div>
            <Scales size={40} className="text-[hsl(var(--gold))] opacity-30" />
          </div>
        </div>

        <PaymentNotice payment={payment} cancelled={returned.cancelled} returnedWithoutSession={returned.paid && !returned.sessionId} />

        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <div className="lux-card p-4">
            <div className="lux-overline text-[10px]">Ανείσπρακτα</div>
            <div className="mt-1 font-mono text-xl text-[hsl(var(--gold))]" data-testid="portal-unpaid">{money(data.totals.unpaid_total)}</div>
            <div className="text-[10px] text-muted-foreground">{data.totals.unpaid_count} τιμολόγια</div>
          </div>
          <div className="lux-card p-4">
            <div className="lux-overline text-[10px]">Πληρωμένα</div>
            <div className="mt-1 font-mono text-xl text-[hsl(var(--success))]">{data.totals.paid_count}</div>
            <div className="text-[10px] text-muted-foreground">τιμολόγια</div>
          </div>
          <div className="lux-card p-4">
            <div className="lux-overline text-[10px]">Ενεργές υποθέσεις</div>
            <div className="mt-1 font-mono text-xl">{data.totals.active_cases}</div>
          </div>
          {data.trust && (
            <div className="lux-card p-4">
              <div className="lux-overline text-[10px]">Πελατολογικός</div>
              <div className="mt-1 font-mono text-xl">{money(data.trust.balance)}</div>
              <div className="text-[10px] text-muted-foreground">υπόλοιπο</div>
            </div>
          )}
        </div>

        <div className="lux-card overflow-hidden">
          <div className="px-4 py-3 border-b border-border/60 flex items-center gap-2">
            <FilePdf size={18} className="text-[hsl(var(--gold))]" />
            <span className="font-heading text-sm">Τιμολόγια</span>
          </div>
          <Table data-testid="portal-invoices">
            <TableHeader>
              <TableRow>
                <TableHead>Αριθμός</TableHead>
                <TableHead>Ημ/νία</TableHead>
                <TableHead>Κατάσταση</TableHead>
                <TableHead className="text-right">Ποσό</TableHead>
                <TableHead className="text-right">Ενέργειες</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.invoices.map((i) => (
                <TableRow key={i.id}>
                  <TableCell className="font-mono text-xs">{i.number}</TableCell>
                  <TableCell>{dateFmt(i.date)}</TableCell>
                  <TableCell><span className={`text-xs font-medium ${STATUS_STYLE[i.status]}`}>{STATUS[i.status]}</span></TableCell>
                  <TableCell className="text-right font-mono">
                    {money(i.total)}
                    {i.status === "issued" && i.outstanding != null && Math.abs(i.outstanding - i.total) >= 0.005 && (
                      <div className="text-[10px] text-muted-foreground" data-testid={`portal-due-${i.id}`}>Οφείλεται: {money(i.outstanding)}</div>
                    )}
                  </TableCell>
                  <TableCell className="text-right space-x-1">
                    <Button size="sm" variant="ghost" onClick={() => downloadPdf(i.id, i.number)} data-testid={`portal-pdf-${i.id}`}>
                      <FilePdf size={14} className="mr-1" /> PDF
                    </Button>
                    {i.status === "issued" && (
                      <Button size="sm" onClick={() => payInvoice(i.id)} disabled={paying === i.id} data-testid={`portal-pay-${i.id}`}>
                        <CreditCard size={14} className="mr-1" /> {paying === i.id ? "..." : "Πληρωμή"}
                      </Button>
                    )}
                  </TableCell>
                </TableRow>
              ))}
              {data.invoices.length === 0 && (
                <TableRow><TableCell colSpan={5} className="text-center text-muted-foreground py-6">Δεν υπάρχουν τιμολόγια.</TableCell></TableRow>
              )}
            </TableBody>
          </Table>
        </div>

        {data.cases.length > 0 && (
          <div className="lux-card overflow-hidden">
            <div className="px-4 py-3 border-b border-border/60 flex items-center gap-2">
              <Briefcase size={18} className="text-[hsl(var(--gold))]" />
              <span className="font-heading text-sm">Οι υποθέσεις σας</span>
            </div>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Αριθμός</TableHead>
                  <TableHead>Τίτλος</TableHead>
                  <TableHead>Δικαστήριο</TableHead>
                  <TableHead>Κατάσταση</TableHead>
                  <TableHead>Επόμενη ενέργεια</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.cases.map((c) => (
                  <TableRow key={c.id}>
                    <TableCell className="font-mono text-xs">{c.number}</TableCell>
                    <TableCell>{c.title}</TableCell>
                    <TableCell className="text-xs">{c.court}</TableCell>
                    <TableCell><Badge variant="outline">{c.status}</Badge></TableCell>
                    <TableCell className="text-xs">
                      {c.next_action}
                      {c.next_action_date && <span className="ml-1 text-muted-foreground">· {dateFmt(c.next_action_date)}</span>}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}

        <div className="text-center text-xs text-muted-foreground py-6">
          {data.firm.name} · {data.firm.phone} · {data.firm.email}
        </div>
      </div>
    </div>
  );
}
