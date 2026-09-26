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
