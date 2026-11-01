import { useEffect, useState } from "react";
import axios, { API, errMsg } from "@/lib/api";

const LABELS = {
  "post:invoices:issue": "Εκδόθηκε",
  "post:invoices:send-email": "Στάλθηκε στον πελάτη",
  "post:invoices:send-reminder": "Στάλθηκε υπενθύμιση",
  "post:invoices:mark-paid": "Σημάνθηκε ως εξοφλημένο",
  "post:invoices:cancel": "Ακυρώθηκε",
  "post:invoices:credit-note": "Εκδόθηκε πιστωτικό",
  "put:invoices": "Τροποποιήθηκε",
  "invoice:mydata_synced": "Διαβιβάστηκε στο myDATA",
  "invoice:mydata_failed": "Απέτυχε η διαβίβαση στο myDATA",
  "invoice:mydata_needs_review": "Διαβίβαση σε άγνωστη κατάσταση — χρειάζεται έλεγχο",
  "invoice:paid": "Εξοφλήθηκε",
};

const ACTOR = { system: "Σύστημα", portal_client: "Πελάτης (portal)" };

export const timelineLabel = (action) => LABELS[action] || action;

export function InvoiceTimeline({ invoiceId }) {
  const [events, setEvents] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    setEvents(null);
    setError("");
    axios.get(`${API}/invoices/${invoiceId}/timeline`)
      .then((r) => { if (!cancelled) setEvents(r.data); })
      .catch((e) => { if (!cancelled) setError(errMsg(e)); });
    return () => { cancelled = true; };
  }, [invoiceId]);

  return (
    <div className="text-sm" data-testid="invoice-timeline">
      <h4 className="font-medium mb-2">Χρονολόγιο</h4>
      {error && <p className="text-xs text-destructive">{error}</p>}
      {!error && events === null && <p className="text-xs text-muted-foreground">Φόρτωση...</p>}
      {events && events.length === 0 && <p className="text-xs text-muted-foreground">Δεν υπάρχουν καταγεγραμμένα γεγονότα.</p>}
      {events && events.length > 0 && (
        <ol className="border-l border-border ml-1.5 space-y-2">
          {events.map((e, i) => (
            <li key={`${e.at}-${i}`} className="pl-3 relative" data-testid="invoice-timeline-event">
              <span className="absolute -left-[4.5px] top-1.5 h-2 w-2 rounded-full bg-border" />
              <div>{timelineLabel(e.action)}</div>
              <div className="text-xs text-muted-foreground">
                {new Date(e.at).toLocaleString("el-GR")} · {ACTOR[e.actor_type] === undefined || e.actor_type === "user" ? e.actor : ACTOR[e.actor_type]}
                {e.details ? ` · ${e.details}` : ""}
              </div>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
