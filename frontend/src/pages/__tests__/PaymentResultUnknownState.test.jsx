/**
 * ΔΙΟΡΘΩΘΗΚΕ — σφάλμα διατύπωσης (frontend/src/pages/PaymentResult.jsx), χαμηλής σοβαρότητας, κρίση του ιδιοκτήτη του προϊόντος.
 *
 * Όταν η κατάσταση της πληρωμής ΔΕΝ είναι γνωστή (η ερώτηση προς τον server απέτυχε, ή μετά από ~30″ η πληρωμή είναι ακόμη
 * «pending»), η σελίδα έγραφε «Η πληρωμή δεν ολοκληρώθηκε — δοκιμάστε ξανά». Όμως η κάρτα μπορεί να έχει χρεωθεί και το webhook
 * να καθυστερεί: η πρόσκληση «δοκιμάστε ξανά» οδηγεί σε διπλή πληρωμή. Η πύλη πελάτη (ClientPortal.jsx) χειριζόταν τις ίδιες
 * καταστάσεις σωστά («Η πληρωμή σας επεξεργάζεται ακόμη», «Δεν ήταν δυνατή η επιβεβαίωση της πληρωμής αυτή τη στιγμή»).
 *
 * Ισχύει: στις καταστάσεις «timeout» και «error» το κείμενο δεν δηλώνει ότι η πληρωμή απέτυχε και δεν προτρέπει σε νέα προσπάθεια.
 * (Η περίπτωση χωρίς session_id στον σύνδεσμο δεν εξετάζεται εδώ: εκεί δεν υπάρχει πληρωμή που να έχει αρχίσει.)
 */
import { act, render, screen } from "@testing-library/react";
import axios from "axios";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { HttpResponse, http } from "msw";
import { afterEach, describe, expect, it, vi } from "vitest";

import PaymentResult from "@/pages/PaymentResult";
import { server } from "@/test/server";

const API = "http://localhost:8001/api";

const renderResult = () => render(
  <MemoryRouter initialEntries={["/payment/success?session_id=cs_1"]}>
    <Routes><Route path="/payment/success" element={<PaymentResult mode="success" />} /></Routes>
  </MemoryRouter>,
);

describe("Αποτέλεσμα πληρωμής — άγνωστη κατάσταση", () => {
  afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); });

  it("αν η κατάσταση δεν μπορεί να διαβαστεί, δεν δηλώνει ότι η πληρωμή απέτυχε ούτε προτρέπει σε νέα προσπάθεια", async () => {
    server.use(http.get(`${API}/payments/status/cs_1`, () => HttpResponse.json({ detail: "Η υπηρεσία δεν απαντά." }, { status: 503 })));
    renderResult();
    const notice = await screen.findByTestId("payment-error");
    expect(notice).not.toHaveTextContent("δεν ολοκληρώθηκε");
    expect(notice).not.toHaveTextContent("δοκιμάστε ξανά");
  });

  it("αν η πληρωμή είναι ακόμη σε εξέλιξη μετά το όριο αναμονής, δεν δηλώνει ότι απέτυχε ούτε προτρέπει σε νέα προσπάθεια", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    vi.spyOn(axios, "get").mockResolvedValue({ data: { session_id: "cs_1", status: "initiated", payment_status: "pending", invoice_number: "ΤΠΥ-2026-0001", amount: 124 } });
    renderResult();
    await act(async () => { await vi.advanceTimersByTimeAsync(32_000); });
    const notice = screen.getByTestId("payment-timeout");
    expect(notice).not.toHaveTextContent("δεν ολοκληρώθηκε");
    expect(notice).not.toHaveTextContent("δοκιμάστε ξανά");
  });
});
