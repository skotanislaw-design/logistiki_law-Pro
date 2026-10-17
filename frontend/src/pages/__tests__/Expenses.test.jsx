/**
 * Η ημερομηνία ενός εξόδου είναι η σημερινή **στην Ελλάδα**. Ο browser δίνει την ημερομηνία UTC με
 * `toISOString()`, που μετά τα μεσάνυχτα τοπικής ώρας είναι ακόμη η χθεσινή — και το έξοδο θα
 * έπεφτε σε λάθος ημέρα (ή, την 1η του μήνα, σε λάθος μήνα ΦΠΑ).
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { afterEach, describe, expect, it, vi } from "vitest";

import { Toaster } from "@/components/ui/sonner";
import Expenses from "@/pages/Expenses";
import { lookupHandlers } from "@/test/lookupFixtures";
import { server } from "@/test/server";

const API = "http://localhost:8001/api";

describe("Έξοδα — ημερομηνία", () => {
  afterEach(() => vi.useRealTimers());

  // 00:30 της 1ης Οκτωβρίου στην Αθήνα = 21:30 της 30ής Σεπτεμβρίου σε UTC
  function atMidnightInAthens() {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-09-30T21:30:00Z"));
  }

  it("το νέο έξοδο παίρνει τη σημερινή ημερομηνία Αθήνας και την στέλνει όπως είναι", async () => {
    atMidnightInAthens();
    const user = userEvent.setup();
    let posted = null;
    server.use(
      ...lookupHandlers(),
      http.get(`${API}/expenses`, () => HttpResponse.json([])),
      http.post(`${API}/expenses`, async ({ request }) => {
        posted = await request.json();
        return HttpResponse.json({ id: "e1", ...posted });
      }),
    );
    render(<><Expenses /><Toaster /></>);
    await user.click(await screen.findByTestId("new-expense-button"));
    expect(await screen.findByDisplayValue("2026-10-01")).toBeInTheDocument();
    await user.type(screen.getByTestId("expense-vendor-input"), "Προμηθευτής");
    await user.click(screen.getByTestId("save-expense-button"));
    await waitFor(() => expect(posted).not.toBeNull());
    expect(posted.date).toBe("2026-10-01");
  });

  it("η ανάγνωση απόδειξης χωρίς ημερομηνία πέφτει στη σημερινή ημερομηνία Αθήνας", async () => {
    atMidnightInAthens();
    const user = userEvent.setup({ applyAccept: false });
    server.use(
      ...lookupHandlers(),
      http.get(`${API}/expenses`, () => HttpResponse.json([])),
      http.post(`${API}/expenses/ocr-parse`, () => HttpResponse.json({
        parsed: { vendor: "Βιβλιοπωλείο", amount: 12.4, vat_amount: 2.4, vat_rate: 24 },   // χωρίς date
        receipt_image_url: "/uploads/x.png",
      })),
    );
    render(<><Expenses /><Toaster /></>);
    const file = new File(["x"], "apodeixi.png", { type: "image/png" });
    await user.upload(await screen.findByTestId("ocr-file-input"), file);
    expect(await screen.findByDisplayValue("Βιβλιοπωλείο")).toBeInTheDocument();
    expect(screen.getByDisplayValue("2026-10-01")).toBeInTheDocument();
  });
});
