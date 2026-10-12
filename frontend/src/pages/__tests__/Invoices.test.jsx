/**
 * Ο αριθμός ενός παραστατικού δίνεται στην έκδοση (βλ. backend/invoice_services.issue_draft).
 * Το πρόχειρο έρχεται από το API με `number: ""` και η οθόνη πρέπει να το δείχνει ως
 * «Πρόχειρο», όχι ως κενό κελί ή «undefined».
 */
import { render, screen, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";

import { Toaster } from "@/components/ui/sonner";
import Invoices from "@/pages/Invoices";
import { invoiceLabel, todayAthens } from "@/lib/utils";
import { server } from "@/test/server";

const API = "http://localhost:8001/api";

const base = {
  client_id: "c1", client_name: "ΑΛΦΑ ΑΕ", date: "2026-10-12", line_items: [],
  subtotal: 100, vat_amount: 24, total: 124, mydata_status: "not_sent",
};

describe("Τιμολόγια — αρίθμηση στην έκδοση", () => {
  it("δείχνει «Πρόχειρο» στο πρόχειρο και τον αριθμό στο εκδοθέν", async () => {
    server.use(
      http.get(`${API}/invoices`, () =>
        HttpResponse.json([
          { ...base, id: "d1", number: "", status: "draft" },
          { ...base, id: "i1", number: "ΤΠΥ-2026-0001", status: "issued", mydata_status: "synced" },
        ]),
      ),
      http.get(`${API}/clients`, () => HttpResponse.json([])),
    );
    render(<><Invoices /><Toaster /></>);

    const draftRow = await screen.findByTestId("invoice-row-d1");
    expect(within(draftRow).getAllByRole("cell")[0]).toHaveTextContent("Πρόχειρο");
    const issuedRow = await screen.findByTestId("invoice-row-i1");
    expect(within(issuedRow).getAllByRole("cell")[0]).toHaveTextContent("ΤΠΥ-2026-0001");
    expect(screen.queryByText("undefined")).not.toBeInTheDocument();
  });

  it("η ετικέτα ακολουθεί τον αριθμό όταν υπάρχει", () => {
    expect(invoiceLabel({ number: "ΑΠΥ-2026-0003" })).toBe("ΑΠΥ-2026-0003");
    expect(invoiceLabel({ number: "" })).toBe("Πρόχειρο");
    expect(invoiceLabel(null)).toBe("Πρόχειρο");
  });

  it("η προεπιλεγμένη ημερομηνία είναι μορφής ΕΕΕΕ-ΜΜ-ΗΗ", () => {
    expect(todayAthens()).toMatch(/^\d{4}-\d{2}-\d{2}$/);
  });
});
