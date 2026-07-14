import { useEffect, useState } from "react";
import axios, { API, dateFmt, errMsg } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { CalendarCheck } from "@phosphor-icons/react";
import { toast } from "sonner";

const CATEGORY_LABEL = { vat: "ΦΠΑ", efka: "ΕΦΚΑ", income_tax: "Φόρος Εισοδήματος" };
const CATEGORY_VARIANT = { vat: "destructive", efka: "default", income_tax: "secondary" };

export default function TaxCalendar() {
  const [data, setData] = useState(null);

  useEffect(() => {
    axios.get(`${API}/tax-calendar`).then((r) => setData(r.data)).catch((e) => toast.error(errMsg(e)));
  }, []);

  if (!data) return <div className="text-muted-foreground">Φόρτωση...</div>;

  return (
    <div className="space-y-6" data-testid="tax-calendar-page">
      <div>
        <h1 className="font-heading text-3xl font-bold tracking-tight">Φορολογικό Ημερολόγιο</h1>
        <p className="text-sm text-muted-foreground mt-1">Επόμενες προθεσμίες ΦΠΑ, ΕΦΚΑ και φόρου εισοδήματος</p>
      </div>

      <div className="space-y-3">
        {data.deadlines.map((d, i) => (
          <div key={i} className="border border-border rounded-sm p-5 bg-card flex items-center justify-between" data-testid={`deadline-${d.category}`}>
            <div className="flex items-center gap-4">
              <CalendarCheck size={22} className="text-muted-foreground" />
              <div>
                <div className="flex items-center gap-2">
                  <p className="font-medium">{d.title}</p>
                  <Badge variant={CATEGORY_VARIANT[d.category]}>{CATEGORY_LABEL[d.category]}</Badge>
                </div>
                <p className="text-sm text-muted-foreground mt-0.5">{d.description}</p>
              </div>
            </div>
            <p className="font-mono text-lg">{dateFmt(d.due_date)}</p>
          </div>
        ))}
      </div>
    </div>
  );
}
