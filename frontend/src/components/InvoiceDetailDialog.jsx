import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Badge } from "@/components/ui/badge";
import { money, dateFmt } from "@/lib/api";
import { CloudCheck, CloudSlash } from "@phosphor-icons/react";

const STATUS_LABEL = { draft: "Πρόχειρο", issued: "Εκδόθηκε", paid: "Πληρώθηκε", cancelled: "Ακυρώθηκε" };

export function InvoiceDetailDialog({ invoice, open, onOpenChange }) {
  if (!invoice) return null;
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-xl max-h-[85vh] overflow-y-auto" data-testid="invoice-detail-dialog">
        <DialogHeader>
          <DialogTitle>Τιμολόγιο {invoice.number}</DialogTitle>
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
