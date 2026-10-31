import { render, screen } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";

import { InvoiceTimeline } from "@/components/InvoiceTimeline";
import { server } from "@/test/server";

const API = "http://localhost:8001/api";

describe("Χρονολόγιο τιμολογίου", () => {
  it("δείχνει τα γεγονότα με ελληνικές ετικέτες και τον εκτελεστή", async () => {
    server.use(http.get(`${API}/invoices/abc/timeline`, () => HttpResponse.json([
      { at: "2026-09-01T10:00:00+00:00", action: "post:invoices:issue", actor: "a@x.gr", actor_type: "user", details: "" },
      { at: "2026-09-01T10:00:02+00:00", action: "invoice:mydata_synced", actor: "system", actor_type: "system", details: "400100" },
    ])));
    render(<InvoiceTimeline invoiceId="abc" />);
    expect(await screen.findByText("Εκδόθηκε")).toBeInTheDocument();
    expect(screen.getByText("Διαβιβάστηκε στο myDATA")).toBeInTheDocument();
    expect(screen.getByText(/Σύστημα · 400100/)).toBeInTheDocument();
    expect(screen.getAllByTestId("invoice-timeline-event")).toHaveLength(2);
  });

  it("δείχνει το σφάλμα όταν αποτύχει η φόρτωση", async () => {
    server.use(http.get(`${API}/invoices/bad/timeline`, () => HttpResponse.json({ detail: "Το τιμολόγιο δεν βρέθηκε" }, { status: 404 })));
    render(<InvoiceTimeline invoiceId="bad" />);
    expect(await screen.findByText("Το τιμολόγιο δεν βρέθηκε")).toBeInTheDocument();
  });
});
