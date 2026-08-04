import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useState } from "react";
import axios, { API, money, dateFmt, errMsg } from "@/lib/api";
import { CloudCheck, CloudSlash, FilePdf, EnvelopeSimple, BellRinging, CheckCircle } from "@phosphor-icons/react";
import { toast } from "sonner";

const STATUS_LABEL = { draft: "Πρόχειρο", issued: "Ανεξόφλητο", paid: "Πληρώθηκε", cancelled: "Ακυρώθηκε" };

export function InvoiceDetailDialog({ invoice, open, onOpenChange, onEmailSent }) {
  const [sending, setSending] = useState(false);
  const [reminding, setReminding] = useState(false);
  const [marking, setMarking] = useState(false);
  if (!invoice) return null;

  const sendEmail = async () => {
    setSending(true);
    try {
      const { data } = await axios.post(`${API}/invoices/${invoice.id}/send-email`);
      toast.success(`Το τιμολόγιο στάλθηκε στο ${data.email_sent_to}`);
      onEmailSent?.();
    } catch (e) {
      toast.error(errMsg(e));
    } finally {
      setSending(false);
    }
  };

  const sendReminder = async () => {
    setReminding(true);
    try {
      const { data } = await axios.post(`${API}/invoices/${invoice.id}/send-reminder`);
      toast.success(`Υπενθύμιση στάλθηκε στο ${data.email_sent_to || "πελάτη"}`);
      onEmailSent?.();
      onOpenChange?.(false);
    } catch (e) {
      toast.error(errMsg(e));
    } finally {
      setReminding(false);
    }
  };

  const markPaid = async () => {
    setMarking(true);
    try {
      await axios.post(`${API}/invoices/${invoice.id}/mark-paid`);
      toast.success("Το τιμολόγιο σημάνθηκε ως εξοφλημένο");
      onEmailSent?.();
      onOpenChange?.(false);
    } catch (e) {
      toast.error(errMsg(e));
    } finally {
      setMarking(false);
    }
  };

  const downloadPdf = async () => {
    try {
      const res = await axios.get(`${API}/invoices/${invoice.id}/pdf`, { responseType: "blob" });
      const url = URL.createObjectURL(res.data);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${invoice.number || "timologio"}.pdf`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      toast.success("Το PDF δημιουργήθηκε");
    } catch (e) {
      toast.error(errMsg(e));
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-xl max-h-[85vh] overflow-y-auto" aria-describedby={undefined} data-testid="invoice-detail-dialog">
        <DialogHeader>
          <div className="flex items-center justify-between pr-6">
            <DialogTitle>Τιμολόγιο {invoice.number}</DialogTitle>
            <div className="flex gap-2 flex-wrap">
              <Button size="sm" variant="outline" onClick={downloadPdf} data-testid="invoice-detail-pdf-button">
                <FilePdf size={16} className="mr-1.5" /> PDF
              </Button>
              <Button size="sm" onClick={sendEmail} disabled={sending} data-testid="invoice-detail-email-button">
                <EnvelopeSimple size={16} className="mr-1.5" /> {sending ? "Αποστολή..." : "Αποστολή στον πελάτη"}
              </Button>
              {invoice.status === "issued" && (
                <>
                  <Button size="sm" variant="outline" onClick={sendReminder} disabled={reminding} data-testid="invoice-detail-reminder-button">
                    <BellRinging size={16} className="mr-1.5" /> {reminding ? "Αποστολή..." : (invoice.reminder_count ? `Νέα υπενθύμιση (×${invoice.reminder_count})` : "Υπενθύμιση")}
                  </Button>
                  <Button size="sm" onClick={markPaid} disabled={marking} data-testid="invoice-detail-mark-paid-button">
                    <CheckCircle size={16} className="mr-1.5" /> {marking ? "..." : "Πληρώθηκε"}
                  </Button>
                </>
              )}
            </div>
          </div>
        </DialogHeader>

        <div className="grid grid-cols-2 gap-3 text-sm">
          <div><span className="text-muted-foreground">Πελάτης: </span><span className="font-medium">{invoice.client_name}</span></div>
          <div><span className="text-muted-foreground">Ημερομηνία: </span>{dateFmt(invoice.date)}</div>
          <div><span className="text-muted-foreground">Κατάσταση: </span><Badge>{STATUS_LABEL[invoice.status]}</Badge></div>
          <div><span className="text-muted-foreground">Τρόπος Πληρωμής: </span>{invoice.payment_method}</div>
        </div>

        <div className="border border-border rounded-sm overflow-hidden mt-2">
          <table className="w-full text-sm">
            <thead className="bg-muted/60">
              <tr>
                <th className="text-left p-2">Περιγραφή</th>
                <th className="text-right p-2">Ποσ.</th>
                <th className="text-right p-2">Τιμή</th>
                <th className="text-right p-2">ΦΠΑ%</th>
                <th className="text-right p-2">Σύνολο</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {invoice.line_items.map((li, idx) => (
                <tr key={idx}>
                  <td className="p-2">{li.description}</td>
                  <td className="p-2 text-right font-mono">{li.quantity}</td>
                  <td className="p-2 text-right font-mono">{money(li.unit_price)}</td>
                  <td className="p-2 text-right font-mono">{li.vat_rate}%</td>
                  <td className="p-2 text-right font-mono">{money(li.quantity * li.unit_price)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div className="space-y-1 text-sm">
          {Object.entries(invoice.vat_breakdown || {}).map(([rate, amount]) => (
            <div key={rate} className="flex justify-between text-muted-foreground">
              <span>ΦΠΑ {rate}%</span><span className="font-mono">{money(amount)}</span>
            </div>
          ))}
          <div className="flex justify-between"><span>Καθαρή Αξία</span><span className="font-mono">{money(invoice.subtotal)}</span></div>
          <div className="flex justify-between"><span>Σύνολο ΦΠΑ</span><span className="font-mono">{money(invoice.vat_amount)}</span></div>
          <div className="flex justify-between text-base font-semibold border-t border-border pt-1"><span>Σύνολο</span><span className="font-mono" data-testid="invoice-detail-total">{money(invoice.total)}</span></div>
        </div>

        {invoice.notes && (
          <div className="text-sm">
            <span className="text-muted-foreground">Σημειώσεις: </span>{invoice.notes}
          </div>
        )}

        {invoice.email_sent_at && (
          <div className="text-xs text-[hsl(var(--success))] flex items-center gap-1.5" data-testid="invoice-detail-email-status">
            <EnvelopeSimple size={14} /> Εστάλη στο {invoice.email_sent_to} · {dateFmt(invoice.email_sent_at)}
          </div>
        )}

        {invoice.reminder_sent_at && (
          <div className="text-xs text-[hsl(var(--gold))] flex items-center gap-1.5" data-testid="invoice-detail-reminder-status">
            <BellRinging size={14} /> Τελευταία υπενθύμιση: {dateFmt(invoice.reminder_sent_at)} · Σύνολο: {invoice.reminder_count || 0}
          </div>
        )}

        {invoice.paid_at && (
          <div className="text-xs text-[hsl(var(--success))] flex items-center gap-1.5" data-testid="invoice-detail-paid-status">
            <CheckCircle size={14} weight="fill" /> Εξοφλήθηκε στις {dateFmt(invoice.paid_at)}
          </div>
        )}

        <div className="border border-dashed border-border rounded-sm p-3 flex items-center gap-2 text-sm" data-testid="invoice-detail-mydata">
          {invoice.mydata_status === "synced" ? (
            <>
              <CloudCheck size={18} className="text-[hsl(var(--success))]" />
              <div>
                <div>Στάλθηκε στο myDATA <Badge variant="secondary" className="ml-1">MOCK</Badge></div>
                <p className="text-xs text-muted-foreground font-mono">MARK: {invoice.mydata_mark} · UID: {invoice.mydata_uid}</p>
              </div>
            </>
          ) : (
            <>
              <CloudSlash size={18} className="text-muted-foreground" />
              <span className="text-muted-foreground">Δεν έχει σταλθεί ακόμα στο myDATA</span>
            </>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
