/**
 * Ο έλεγχος ΑΦΜ στο backend επιστρέφει 400 με εξήγηση. Το ότι η εξήγηση **φτάνει στην
 * οθόνη** ήταν μέχρι τώρα συμπέρασμα από ανάγνωση κώδικα — ένας έλεγχος που κανείς δεν
 * βλέπει είναι το ίδιο άχρηστος με έλεγχο που δεν υπάρχει.
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { toast } from "sonner";
import { afterEach, describe, expect, it, vi } from "vitest";

import { Toaster } from "@/components/ui/sonner";
import Clients from "@/pages/Clients";
import { server } from "@/test/server";

const API = "http://localhost:8001/api";

function renderClients() {
  return render(<><Clients /><Toaster /></>);
}

afterEach(() => vi.restoreAllMocks());

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

  it("στέλνει στον server τον πελάτη και τη διεύθυνση της εφαρμογής, ώστε ο σύνδεσμος να δείχνει εκεί που τρέχει", async () => {
    const bodies = [];
    server.use(
      http.get(`${API}/clients`, () => HttpResponse.json([client])),
      http.post(`${API}/client-portal/invite`, async ({ request }) => {
        bodies.push(await request.json());
        return HttpResponse.json({ message: "Δημιουργήθηκε σύνδεσμος πρόσβασης.", portal_link: LINK });
      }),
    );
    const user = userEvent.setup();
    stubClipboard(vi.fn().mockResolvedValue(undefined));
    renderClients();
    await user.click(await screen.findByTestId("invite-portal-c1"));
    await waitFor(() => expect(bodies).toEqual([{ client_id: "c1", origin_url: window.location.origin }]));
  });

  it("η επιτυχής αντιγραφή ανακοινώνεται ως επιτυχία, όχι ως προειδοποίηση", async () => {
    serveInvite();
    const user = userEvent.setup();
    stubClipboard(vi.fn().mockResolvedValue(undefined));
    renderClients();
    await user.click(await screen.findByTestId("invite-portal-c1"));
    const message = await screen.findByText(/Ο σύνδεσμος αντιγράφηκε/);
    expect(message.closest("li")).toHaveAttribute("data-type", "success");
    expect(document.querySelector('li[data-sonner-toast][data-type="warning"]')).toBeNull();
  });

  it("αν η αντιγραφή αποτύχει, η ειδοποίηση είναι προειδοποίηση και μένει 60 δευτερόλεπτα για να προλάβει ο χρήστης", async () => {
    // Η προεπιλεγμένη διάρκεια (4″) δεν φτάνει για να διαβαστεί και να αντιγραφεί ένας μακρύς σύνδεσμος με το χέρι.
    serveInvite();
    const warning = vi.spyOn(toast, "warning");
    const user = userEvent.setup();
    stubClipboard(vi.fn().mockRejectedValue(new Error("NotAllowedError")));
    renderClients();
    await user.click(await screen.findByTestId("invite-portal-c1"));
    const message = await screen.findByText(new RegExp(LINK.replaceAll("/", "\\/")));
    expect(message.closest("li")).toHaveAttribute("data-type", "warning");
    expect(warning).toHaveBeenCalledTimes(1);
    expect(warning).toHaveBeenCalledWith(expect.stringContaining(LINK), { duration: 60000 });
  });

  it("αν ο server αρνηθεί την πρόσκληση, δεν αντιγράφει τίποτα και δείχνει το σφάλμα", async () => {
    server.use(
      http.get(`${API}/clients`, () => HttpResponse.json([client])),
      http.post(`${API}/client-portal/invite`, () => HttpResponse.json({ detail: "Ο πελάτης δεν έχει email." }, { status: 400 })),
    );
    const user = userEvent.setup();
    const write = vi.fn().mockResolvedValue(undefined);
    stubClipboard(write);
    renderClients();
    await user.click(await screen.findByTestId("invite-portal-c1"));
    expect(await screen.findByText("Ο πελάτης δεν έχει email.")).toBeInTheDocument();
    expect(write).not.toHaveBeenCalled();
  });
});
