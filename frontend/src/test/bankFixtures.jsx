/**
 * Κοινά δεδομένα και handlers για τα tests της οθόνης «Τράπεζα» (Bank.test.jsx, BankReview.test.jsx).
 */
import { render } from "@testing-library/react";
import { HttpResponse, http } from "msw";

import { Toaster } from "@/components/ui/sonner";
import Bank from "@/pages/Bank";
import { server } from "@/test/server";

export const API = "http://localhost:8001/api";

export const tx = (over) => ({
  id: "t0", date: "2026-10-09", description: "ΕΜΒΑΣΜΑ ΠΕΛΑΤΗ", amount: 124, reference: "", bank: "Πειραιώς",
  matched_invoice_id: null, matched_invoice_number: null, match_confidence: 0, match_status: "unmatched",
  match_ambiguous: false, amount_difference: null, ...over,
});

export const invoice = (over) => ({
  id: "i0", number: "ΤΠΥ-2026-0001", client_id: "c1", client_name: "ΑΛΦΑ ΑΕ", date: "2026-10-01", status: "issued",
  total: 124, outstanding: 124, ...over,
});

export const SUMMARY = { total: 6, unmatched: 3, auto_matched: 1, manual_matched: 1, auto_confirmed: 0, ignored: 1 };

export const ROWS = {
  unmatched: [
    tx({ id: "t1", description: "ΕΜΒΑΣΜΑ ΑΛΦΑ", amount: 124 }),
    tx({ id: "t2", description: "ΕΜΒΑΣΜΑ ΧΩΡΙΣ ΑΝΤΙΣΤΟΙΧΙΑ", amount: 80, date: "2026-10-08" }),
    tx({ id: "t3", description: "ΕΝΟΙΚΙΟ", amount: -600, date: "2026-10-07" }),
  ],
  auto_matched: [
    tx({ id: "t4", description: "ΚΑΤΑΘΕΣΗ ΒΗΤΑ", amount: 248, date: "2026-10-06", match_status: "auto_matched",
         matched_invoice_id: "i2", matched_invoice_number: "ΤΠΥ-2026-0002", match_confidence: 92 }),
  ],
  manual_matched: [
    tx({ id: "t5", description: "ΕΞΟΦΛΗΣΗ", amount: 62, date: "2026-10-01", match_status: "manual_matched",
         matched_invoice_id: "i9", matched_invoice_number: "ΤΠΥ-2026-0009", amount_difference: -2 }),
  ],
  auto_confirmed: [],
  ignored: [tx({ id: "t6", description: "ΛΟΓΑΡΙΑΣΜΟΣ", amount: -20, date: "2026-09-30", match_status: "ignored" })],
};

export const INVOICES = [
  invoice({ id: "i1", number: "ΤΠΥ-2026-0001", client_name: "ΑΛΦΑ ΑΕ", total: 124, outstanding: 124 }),
  invoice({ id: "i2", number: "ΤΠΥ-2026-0002", client_name: "ΒΗΤΑ ΑΕ", total: 248, outstanding: 248, date: "2026-10-02" }),
  invoice({ id: "i3", number: "ΤΠΥ-2026-0003", client_name: "ΓΑΜΜΑ ΕΠΕ", total: 82, outstanding: 82, date: "2026-10-03" }),
  invoice({ id: "i4", number: "ΤΠΥ-2026-0004", client_name: "ΑΛΦΑ ΑΕ", total: 100, outstanding: 0 }),            // καλύφθηκε εξ ολοκλήρου
  invoice({ id: "i5", number: "ΤΠΥ-2026-0005", client_name: "ΑΛΦΑ ΑΕ", total: 50, outstanding: 50, status: "paid" }),
  invoice({ id: "i6", number: "ΠΙΣ-2026-0001", client_name: "ΑΛΦΑ ΑΕ", total: -62, outstanding: -62 }),            // πιστωτικό
];

/** Βάζει handlers για όλα τα GET που κάνει η οθόνη· επιστρέφει τη λίστα των αιτημάτων transactions. */
export function serve({ summary = SUMMARY, rows = ROWS, invoices = INVOICES } = {}) {
  const requested = [];
  server.use(
    http.get(`${API}/bank/summary`, () => HttpResponse.json(summary)),
    http.get(`${API}/bank/transactions`, ({ request }) => {
      const status = new URL(request.url).searchParams.get("status") || "all";
      requested.push(status);
      const list = status === "all" ? Object.values(rows).flat() : (rows[status] || []);
      return HttpResponse.json(list);
    }),
    http.get(`${API}/invoices`, () => HttpResponse.json(invoices)),
  );
  return requested;
}

export const renderBank = () => render(<><Bank /><Toaster /></>);
