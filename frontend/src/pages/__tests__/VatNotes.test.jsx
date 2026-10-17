/**
 * Φάση 1 «ψηφιακού λογιστή» — ΦΠΑ: οι σελίδες δείχνουν ό,τι δεν είναι αυτονόητο στο ΦΠΑ εισροών.
 *
 * Δύο πράγματα δεν πρέπει να χάνονται μέσα σε ένα ενιαίο ποσό:
 *   · το ΦΠΑ που δεν εκπίπτει (έχει γίνει κόστος του εξόδου και δεν μπαίνει στη δήλωση),
 *   · το ΦΠΑ που εκπίπτει 100% επειδή δεν υπάρχει κανόνας (δεν το επιβεβαίωσε κανείς) — με σύνδεσμο προς τις Ρυθμίσεις.
 * Όταν δεν υπάρχουν, οι ενδείξεις δεν εμφανίζονται καθόλου (ούτε «0,00 €»).
 */
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import Autopilot from "@/pages/Autopilot";
import Vat from "@/pages/Vat";
import { server } from "@/test/server";

const API = "http://localhost:8001/api";

vi.mock("@/context/AuthContext", () => ({
  useAuth: () => ({ user: { name: "Χρήστος Σκοτάνης", role: "admin" } }),
}));

const SETTINGS = { vat_period: "monthly", autopilot_enabled: true };

function serveVat(summary) {
  server.use(
    http.get(`${API}/dashboard/summary`, () => HttpResponse.json({
      output_vat: 240, input_vat: 54, vat_due: 186, next_vat_deadline: "2026-11-30", ...summary,
    })),
    http.get(`${API}/settings`, () => HttpResponse.json(SETTINGS)),
  );
}

const renderVat = () => render(
  <MemoryRouter initialEntries={["/vat"]}>
    <Routes>
      <Route path="/vat" element={<Vat />} />
      <Route path="/settings" element={<div data-testid="settings-destination">Ρυθμίσεις</div>} />
    </Routes>
  </MemoryRouter>,
);

describe("Σελίδα ΦΠΑ — ΦΠΑ εισροών", () => {
  it("χωρίς μη εκπιπτόμενο και χωρίς «χωρίς κανόνα» δεν δείχνει καμία από τις δύο ενδείξεις", async () => {
    serveVat({});
    renderVat();
    expect(await screen.findByTestId("input-vat")).toHaveTextContent("54,00");
    expect(screen.queryByTestId("input-vat-nondeductible")).not.toBeInTheDocument();
    expect(screen.queryByTestId("input-vat-unruled")).not.toBeInTheDocument();
  });

  it("δείχνει ξεχωριστά το ΦΠΑ που δεν εκπίπτει", async () => {
    serveVat({ input_vat: 40, input_vat_nondeductible: 14.5 });
    renderVat();
    const note = await screen.findByTestId("input-vat-nondeductible");
    expect(note).toHaveTextContent("14,50");
    expect(note).toHaveTextContent("δεν εκπίπτει");
    expect(screen.getByTestId("input-vat")).toHaveTextContent("40,00");        // το ποσό της δήλωσης δεν περιλαμβάνει το μη εκπιπτόμενο
    expect(screen.queryByTestId("input-vat-unruled")).not.toBeInTheDocument();
  });

  it("δείχνει το ΦΠΑ που εκπίπτει χωρίς κανόνα και οδηγεί στις Ρυθμίσεις", async () => {
    const user = userEvent.setup();
    serveVat({ input_vat_unruled: 31.2 });
    renderVat();
    const note = await screen.findByTestId("input-vat-unruled");
    expect(note).toHaveTextContent("31,20");
    expect(note).toHaveTextContent("χωρίς να έχει οριστεί κανόνας εκπτωσιμότητας");
    await user.click(within(note).getByRole("link", { name: /Ορίστε τους κανόνες στις Ρυθμίσεις/ }));
    expect(await screen.findByTestId("settings-destination")).toBeInTheDocument();
  });

  it("μπορεί να δείξει και τα δύο μαζί", async () => {
    serveVat({ input_vat_nondeductible: 5, input_vat_unruled: 7 });
    renderVat();
    expect(await screen.findByTestId("input-vat-nondeductible")).toHaveTextContent("5,00");
    expect(screen.getByTestId("input-vat-unruled")).toHaveTextContent("7,00");
  });

  it("παλιά απάντηση του server (χωρίς τα νέα πεδία) δεν σπάει τη σελίδα", async () => {
    server.use(
      http.get(`${API}/dashboard/summary`, () => HttpResponse.json({ output_vat: 10, input_vat: 2, vat_due: 8, next_vat_deadline: "2026-11-30" })),
      http.get(`${API}/settings`, () => HttpResponse.json(SETTINGS)),
    );
    renderVat();
    expect(await screen.findByTestId("vat-net-due")).toHaveTextContent("8,00");
    expect(screen.queryByTestId("input-vat-nondeductible")).not.toBeInTheDocument();
  });
});

describe("Autopilot — περίοδοι ΦΠΑ", () => {
  const period = (over) => ({
    period: "2026-09", output_vat: 240, input_vat: 54, vat_due: 186, due_date: "2026-10-31", status: "open", ...over,
  });

  function serveAutopilot(periods) {
    server.use(
      http.get(`${API}/automation/status`, () => HttpResponse.json({
        open_items: 0, last_7_days: {}, cycle_minutes: 15, last_digest_at: null,
        capabilities: { email: true, mydata: true, stripe: true, ai: true, imap: true, mydata_expenses: true, openbanking: true },
      })),
      http.get(`${API}/automation/notifications`, () => HttpResponse.json([])),
      http.get(`${API}/automation/runs`, () => HttpResponse.json([])),
      http.get(`${API}/automation/vat-periods`, () => HttpResponse.json(periods)),
      http.get(`${API}/settings`, () => HttpResponse.json(SETTINGS)),
    );
  }

  async function openVatTab(user) {
    render(<MemoryRouter><Autopilot /></MemoryRouter>);
    await user.click(await screen.findByTestId("tab-vat"));
  }

  it("η περίοδος χωρίς ιδιαιτερότητες δείχνει μόνο τα ποσά", async () => {
    const user = userEvent.setup();
    serveAutopilot([period()]);
    await openVatTab(user);
    const row = await screen.findByTestId("vat-period-2026-09");
    expect(row).toHaveTextContent("54,00");
    expect(screen.queryByTestId("vat-period-nondeductible-2026-09")).not.toBeInTheDocument();
    expect(screen.queryByTestId("vat-period-unruled-2026-09")).not.toBeInTheDocument();
  });

  it("δείχνει ανά περίοδο το μη εκπιπτόμενο ΦΠΑ και το ΦΠΑ «χωρίς κανόνα»", async () => {
    const user = userEvent.setup();
    serveAutopilot([
      period({ period: "2026-09", input_vat_nondeductible: 12.34, input_vat_unruled: 8 }),
      period({ period: "2026-08", input_vat_nondeductible: 0, input_vat_unruled: 0 }),
    ]);
    await openVatTab(user);
    expect(await screen.findByTestId("vat-period-nondeductible-2026-09")).toHaveTextContent("+ 12,34 € μη εκπιπτόμενο");
    expect(screen.getByTestId("vat-period-unruled-2026-09")).toHaveTextContent("8,00 € χωρίς κανόνα");
    // Η άλλη περίοδος δεν παίρνει τις ενδείξεις της πρώτης.
    expect(screen.queryByTestId("vat-period-nondeductible-2026-08")).not.toBeInTheDocument();
    expect(screen.queryByTestId("vat-period-unruled-2026-08")).not.toBeInTheDocument();
  });

  it("περίοδος που υποβλήθηκε εμφανίζει τα ποσά που υπολογίστηκαν τότε, με τις ίδιες ενδείξεις", async () => {
    const user = userEvent.setup();
    serveAutopilot([period({ status: "submitted", input_vat_unruled: 3.5 })]);
    await openVatTab(user);
    expect(await screen.findByTestId("vat-period-unruled-2026-09")).toHaveTextContent("3,50 € χωρίς κανόνα");
  });
});
