/**
 * Το portal είναι η μόνη οθόνη που βλέπει **πελάτης** του γραφείου, χωρίς λογαριασμό.
 * Ελέγχεται ότι τα ποσά εμφανίζονται σε ελληνική μορφή και ότι ένας άκυρος σύνδεσμος
 * εξηγείται αντί να αφήνει λευκή σελίδα.
 */
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";

import ClientPortal from "@/pages/ClientPortal";
import { server } from "@/test/server";

const API = "http://localhost:8001/api";
const TOKEN = "test-token-abc123";

function renderPortal() {
  return render(
    <MemoryRouter initialEntries={[`/portal/${TOKEN}`]}>
      <Routes>
        <Route path="/portal/:token" element={<ClientPortal />} />
      </Routes>
    </MemoryRouter>,
  );
}

const PAYLOAD = {
  firm: { name: "ΔΙΚΗΓΟΡΙΚΟ ΓΡΑΦΕΙΟ", address: "", phone: "", email: "" },
  client: { name: "ΠΕΛΑΤΗΣ ΕΝΑ", afm: "123456783", email: "" },
  invoices: [
    { id: "1", number: "ΤΠΥ-2026-0001", date: "2026-09-10", total: 1240, status: "issued" },
  ],
  cases: [],
  trust: null,
  totals: { unpaid_count: 1, unpaid_total: 1240, paid_count: 0, active_cases: 0 },
};

describe("Πύλη πελάτη", () => {
  it("δείχνει τον φάκελο με ελληνική μορφή ποσών", async () => {
    server.use(http.get(`${API}/portal/${TOKEN}`, () => HttpResponse.json(PAYLOAD)));
    renderPortal();

    expect(await screen.findByText(/ΠΕΛΑΤΗΣ ΕΝΑ/)).toBeInTheDocument();
    expect(screen.getByText("ΤΠΥ-2026-0001")).toBeInTheDocument();
    // Υποδιαστολή κόμμα και σύμβολο μετά τον αριθμό — όχι «1240.00 EUR».
    expect(screen.getAllByText(/1240,00 €/).length).toBeGreaterThan(0);
  });

  it("εξηγεί τον ληγμένο σύνδεσμο αντί να αφήνει λευκή σελίδα", async () => {
    server.use(
      http.get(`${API}/portal/${TOKEN}`, () =>
        HttpResponse.json({ detail: "Ο σύνδεσμος έχει λήξει" }, { status: 401 }),
      ),
    );
    renderPortal();

    expect(await screen.findByTestId("portal-error")).toBeInTheDocument();
    expect(screen.getByText("Ο σύνδεσμος έχει λήξει")).toBeInTheDocument();
  });
});

describe("Πύλη πελάτη — ποσό που οφείλεται", () => {
  // Το σύνολο 1.240 € δεν είναι αυτό που χρωστά ο πελάτης: με παρακράτηση πληρώνει 1.040 €,
  // και αν 400 € έχουν ήδη εξοφληθεί από τον πελατολογικό λογαριασμό του, μένουν 640 €.
  const withDue = {
    ...PAYLOAD,
    invoices: [
      { id: "1", number: "ΤΠΥ-2026-0001", date: "2026-09-10", total: 1240, payable: 1040, outstanding: 640, status: "issued" },
      { id: "2", number: "ΤΠΥ-2026-0002", date: "2026-09-11", total: 124, payable: 124, outstanding: 124, status: "issued" },
      { id: "3", number: "ΤΠΥ-2026-0003", date: "2026-09-12", total: 500, payable: 500, outstanding: 0, status: "paid" },
    ],
    totals: { unpaid_count: 2, unpaid_total: 764, paid_count: 1, active_cases: 0 },
  };

  it("δείχνει «Οφείλεται» μόνο όπου το ποσό διαφέρει από το σύνολο και το τιμολόγιο είναι ανεξόφλητο", async () => {
    server.use(http.get(`${API}/portal/${TOKEN}`, () => HttpResponse.json(withDue)));
    renderPortal();

    const due = await screen.findByTestId("portal-due-1");
    expect(due).toHaveTextContent(/Οφείλεται: 640,00\s€/);
    expect(screen.queryByTestId("portal-due-2")).not.toBeInTheDocument();     // οφείλεται όσο το σύνολο
    expect(screen.queryByTestId("portal-due-3")).not.toBeInTheDocument();     // εξοφλημένο
  });

  it("δεν σπάει όταν ο server (παλιά έκδοση) δεν στέλνει «outstanding»", async () => {
    const legacy = { ...withDue, invoices: [{ id: "1", number: "ΤΠΥ-2026-0001", date: "2026-09-10", total: 1240, status: "issued" }] };
    server.use(http.get(`${API}/portal/${TOKEN}`, () => HttpResponse.json(legacy)));
    renderPortal();
    expect(await screen.findByText("ΤΠΥ-2026-0001")).toBeInTheDocument();
    expect(screen.queryByTestId("portal-due-1")).not.toBeInTheDocument();
  });
});
