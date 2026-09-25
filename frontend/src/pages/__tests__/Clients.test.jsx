/**
 * Ο έλεγχος ΑΦΜ στο backend επιστρέφει 400 με εξήγηση. Το ότι η εξήγηση **φτάνει στην
 * οθόνη** ήταν μέχρι τώρα συμπέρασμα από ανάγνωση κώδικα — ένας έλεγχος που κανείς δεν
 * βλέπει είναι το ίδιο άχρηστος με έλεγχο που δεν υπάρχει.
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";

import { Toaster } from "@/components/ui/sonner";
import Clients from "@/pages/Clients";
import { server } from "@/test/server";

const API = "http://localhost:8001/api";

function renderClients() {
  return render(<><Clients /><Toaster /></>);
}

describe("Καρτέλα πελάτη", () => {
  it("δείχνει στον δικηγόρο γιατί απορρίφθηκε το ΑΦΜ", async () => {
    const user = userEvent.setup();
    server.use(
      http.get(`${API}/clients`, () => HttpResponse.json([])),
      http.post(`${API}/clients`, () =>
        HttpResponse.json(
          { detail: "Το ΑΦΜ πελάτη «123456789» έχει λάθος ψηφίο ελέγχου." },
          { status: 400 },
        ),
      ),
    );

    renderClients();
    await user.click(await screen.findByRole("button", { name: /Νέος Πελάτης|Νέος/i }));
    await user.type(screen.getByTestId("client-name-input"), "ΑΛΦΑ ΑΕ");
    await user.click(screen.getByRole("button", { name: /Αποθήκευση|Δημιουργία/i }));

    expect(await screen.findByText(/λάθος ψηφίο ελέγχου/)).toBeInTheDocument();
  });

  it("δεν κρύβει σφάλμα φόρτωσης της λίστας", async () => {
    server.use(
      http.get(`${API}/clients`, () =>
        HttpResponse.json({ detail: "Η συνεδρία έληξε." }, { status: 401 }),
      ),
    );
    renderClients();
    expect(await screen.findByText(/Η συνεδρία έληξε/)).toBeInTheDocument();
  });
});
