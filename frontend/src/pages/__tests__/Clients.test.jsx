/**
 * Ο έλεγχος ΑΦΜ στο backend επιστρέφει 400 με εξήγηση. Το ότι η εξήγηση **φτάνει στην
 * οθόνη** ήταν μέχρι τώρα συμπέρασμα από ανάγνωση κώδικα — ένας έλεγχος που κανείς δεν
 * βλέπει είναι το ίδιο άχρηστος με έλεγχο που δεν υπάρχει.
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";

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

describe("Σύνδεσμος πύλης πελάτη", () => {
  const LINK = "https://app.example.gr/portal/TOKEN123";
  const client = { id: "c1", name: "ΑΛΦΑ ΑΕ", afm: "123456783", client_type: "company", email: "", phone: "" };

  function serveInvite() {
    server.use(
      http.get(`${API}/clients`, () => HttpResponse.json([client])),
      http.post(`${API}/client-portal/invite`, () =>
        HttpResponse.json({ message: "Δημιουργήθηκε σύνδεσμος πρόσβασης.", portal_link: LINK })),
    );
  }

  // Το user-event.setup() αντικαθιστά το navigator.clipboard· ο δικός μας έλεγχος μπαίνει μετά.
  function stubClipboard(writeText) {
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
  }

  it("αντιγράφει τον σύνδεσμο και το λέει", async () => {
    serveInvite();
    const user = userEvent.setup();
    const write = vi.fn().mockResolvedValue(undefined);
    stubClipboard(write);
    renderClients();
    await user.click(await screen.findByTestId("invite-portal-c1"));
    expect(await screen.findByText(/Ο σύνδεσμος αντιγράφηκε/)).toBeInTheDocument();
    expect(write).toHaveBeenCalledWith(LINK);
  });

  it("αν η αντιγραφή αποτύχει, δεν ισχυρίζεται ότι αντιγράφηκε και δείχνει τον σύνδεσμο", async () => {
    // Αλλιώς ο σύνδεσμος χανόταν: ο χρήστης πίστευε ότι τον έχει και ο μόνος τρόπος ήταν νέα πρόσκληση.
    serveInvite();
    const user = userEvent.setup();
    const write = vi.fn().mockRejectedValue(new Error("NotAllowedError"));
    stubClipboard(write);
    renderClients();
    await user.click(await screen.findByTestId("invite-portal-c1"));
    expect(await screen.findByText(new RegExp(LINK.replaceAll("/", "\\/")))).toBeInTheDocument();
    await waitFor(() => expect(write).toHaveBeenCalled());
    expect(screen.queryByText(/Ο σύνδεσμος αντιγράφηκε/)).not.toBeInTheDocument();
  });
});
