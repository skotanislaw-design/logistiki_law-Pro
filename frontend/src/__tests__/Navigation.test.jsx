/**
 * Πλοήγηση — η οθόνη «Τράπεζα» είναι προσβάσιμη μέσα από την ίδια την εφαρμογή (διαδρομή, μενού, τίτλος) και
 * προστατεύεται όπως οι υπόλοιπες. Τα tests της ίδιας της σελίδας (pages/__tests__/Bank.test.jsx) τη φορτώνουν
 * απευθείας· χωρίς αυτό το αρχείο, μια σελίδα που δεν έχει δηλωθεί στο App.js θα περνούσε όλα τα tests και θα ήταν
 * αόρατη για τον χρήστη — όπως ήταν ολόκληρη η τράπεζα μέχρι τον γύρο 5.
 */
import { render, screen, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { afterEach, describe, expect, it } from "vitest";

import App from "@/App";
import { server } from "@/test/server";

const API = "http://localhost:8001/api";

function serveShell({ authenticated = true } = {}) {
  server.use(
    http.get(`${API}/auth/me`, () => (authenticated
      ? HttpResponse.json({ id: "u1", email: "christos@skotanislaw.gr", name: "Χρήστος Σκοτάνης", role: "admin" })
      : HttpResponse.json({ detail: "Not authenticated" }, { status: 401 }))),
    http.get(`${API}/automation/status`, () => HttpResponse.json({ open_items: 0 })),
    http.get(`${API}/hearings/alerts/list`, () => HttpResponse.json([])),
    http.get(`${API}/bank/summary`, () => HttpResponse.json({ total: 0, unmatched: 0, auto_matched: 0, manual_matched: 0, auto_confirmed: 0, ignored: 0 })),
    http.get(`${API}/bank/transactions`, () => HttpResponse.json([])),
  );
}

describe("Πλοήγηση — Τράπεζα", () => {
  afterEach(() => window.history.pushState({}, "", "/"));

  it("η διαδρομή /bank ανοίγει την οθόνη μέσα στο κανονικό περιβάλλον της εφαρμογής", async () => {
    serveShell();
    window.history.pushState({}, "", "/bank");
    render(<App />);

    expect(await screen.findByTestId("bank-page")).toBeInTheDocument();
    expect(within(screen.getByTestId("topbar")).getByRole("heading", { level: 2 })).toHaveTextContent("Τράπεζα");
    const link = screen.getByTestId("nav-bank");
    expect(link).toHaveAttribute("href", "/bank");
    expect(link).toHaveAttribute("aria-current", "page");
  });

  it("το μενού δείχνει την Τράπεζα στα Οικονομικά, δίπλα στα Τιμολόγια και τα Έξοδα", async () => {
    serveShell();
    window.history.pushState({}, "", "/invoices");
    server.use(http.get(`${API}/invoices`, () => HttpResponse.json([])), http.get(`${API}/clients`, () => HttpResponse.json([])));
    render(<App />);

    const link = await screen.findByTestId("nav-bank");
    const group = link.closest("div.flex-col");
    expect(within(group).getByTestId("nav-invoices")).toBeInTheDocument();
    expect(within(group).getByTestId("nav-expenses")).toBeInTheDocument();
    expect(link).not.toHaveAttribute("aria-current");
  });

  it("χωρίς σύνδεση, η διαδρομή /bank οδηγεί στη σελίδα εισόδου", async () => {
    serveShell({ authenticated: false });
    window.history.pushState({}, "", "/bank");
    render(<App />);

    expect(await screen.findByTestId("login-form")).toBeInTheDocument();
    expect(screen.queryByTestId("bank-page")).not.toBeInTheDocument();
  });
});
