/**
 * Ηλεκτρονικά Πινάκια (πλήρης προβολή) — D-08 του δεύτερου ελέγχου του γύρου 5 (ελεγκτής D).
 *
 * Ίδια ελαττώματα με το Πινάκιο Τσέπης (βλ. PocketPinakiaLive.test.jsx): η αποτυχία ανανέωσης δεν άφηνε μόνιμη ένδειξη, μια
 * αργή απάντηση μπορούσε να σβήσει νεότερη, και το «+1 Επόμενο Νούμερο» έστελνε απόλυτη τιμή από ενδεχομένως ξεπερασμένη
 * οθόνη. Επιπλέον μια αποτυχημένη φόρτωση εμφανιζόταν ως «Δεν υπάρχουν καταχωρημένα πινάκια».
 */
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse } from "msw";
import { afterEach, describe, expect, it, vi } from "vitest";

import { Toaster } from "@/components/ui/sonner";
import Pinakia from "@/pages/Pinakia";
import { DOWN, failure, gate, hearing, hearingsAt, serveHearings, serveLiveUpdate } from "@/test/liveFixtures";

const renderPinakia = () => render(<><Pinakia /><Toaster /></>);
const current = () => screen.getByTestId("current-number");

const clock = (now = "2026-10-14T07:27:20Z") => vi.useFakeTimers({ toFake: ["setInterval", "clearInterval", "Date"], now: new Date(now) });
const poll = (ms = 15_000) => act(async () => { vi.advanceTimersByTime(ms); });
const settle = () => act(async () => { await new Promise((resolve) => setTimeout(resolve, 60)); });

afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); });

describe("Ηλεκτρονικά Πινάκια — αποτυχία φόρτωσης και ανανέωσης", () => {
  it("αν δεν φορτώσει ποτέ, δεν λέει «Δεν υπάρχουν καταχωρημένα πινάκια»: δείχνει ότι η φόρτωση απέτυχε", async () => {
    serveHearings(() => failure());
    renderPinakia();

    expect(await screen.findByTestId("pinakia-stale")).toHaveTextContent("Δεν ήταν δυνατή η φόρτωση");
    expect(screen.queryByTestId("pinakia-empty")).not.toBeInTheDocument();
    expect(screen.queryByTestId("pinakia-loading")).not.toBeInTheDocument();
  });

  it("όσο φορτώνει δεν λέει ότι δεν υπάρχουν πινάκια", async () => {
    const door = gate();
    serveHearings(async () => { await door.opened; return hearingsAt(4); });
    renderPinakia();

    expect(await screen.findByTestId("pinakia-loading")).toBeInTheDocument();
    expect(screen.queryByTestId("pinakia-empty")).not.toBeInTheDocument();
    door.release();
    expect(await screen.findByTestId("hearing-card-h1")).toBeInTheDocument();
  });

  it("αν φορτώσει και είναι όντως άδεια, το λέει", async () => {
    serveHearings(() => HttpResponse.json([]));
    renderPinakia();
    expect(await screen.findByTestId("pinakia-empty")).toHaveTextContent("Δεν υπάρχουν καταχωρημένα πινάκια");
    expect(screen.queryByTestId("pinakia-stale")).not.toBeInTheDocument();
  });

  it("αποτυχημένη ανανέωση: τα νούμερα μένουν και η ένδειξη λέει από ποια ώρα δεν ενημερώθηκε (ώρα Ελλάδας)", async () => {
    clock("2026-10-14T07:27:20Z");                         // 10:27 στην Ελλάδα
    serveHearings((n) => (n === 1 ? hearingsAt(4) : failure()));
    renderPinakia();
    await screen.findByTestId("hearing-card-h1");
    expect(screen.queryByTestId("pinakia-stale")).not.toBeInTheDocument();

    await poll();

    expect(await screen.findByTestId("pinakia-stale")).toHaveTextContent(/Δεν ενημερώθηκε από 10:27 — τα πινάκια που βλέπετε μπορεί να έχουν αλλάξει\./);
    expect(current()).toHaveTextContent("4");
  });

  it("η αποτυχία ανακοινώνεται με ειδοποίηση μία φορά ανά σειρά αποτυχιών, η ένδειξη μένει", async () => {
    clock();
    serveHearings((n) => (n === 1 ? hearingsAt(4) : failure()));
    renderPinakia();
    await screen.findByTestId("hearing-card-h1");

    await poll(); await settle();
    await poll(); await settle();
    await poll(); await settle();

    expect(screen.getAllByText(DOWN)).toHaveLength(1);
    expect(screen.getByTestId("pinakia-stale")).toBeInTheDocument();
  });

  it("μια επιτυχημένη ανανέωση σβήνει την ένδειξη και ξαναοπλίζει την ανακοίνωση", async () => {
    clock();
    serveHearings((n) => (n === 2 ? failure() : n === 3 ? hearingsAt(5) : n === 4 ? failure() : hearingsAt(5)));
    renderPinakia();
    await screen.findByTestId("hearing-card-h1");

    await poll(); await screen.findByTestId("pinakia-stale");
    await poll(); await waitFor(() => expect(screen.queryByTestId("pinakia-stale")).not.toBeInTheDocument());
    expect(current()).toHaveTextContent("5");
    await poll(); await screen.findByTestId("pinakia-stale");

    expect(screen.getAllByText(DOWN)).toHaveLength(2);
  });

  it("το κουμπί «Επανάληψη» της ένδειξης ξαναδοκιμάζει και ανακοινώνει", async () => {
    const user = userEvent.setup();
    const calls = serveHearings((n) => (n === 1 ? failure() : hearingsAt(6)));
    renderPinakia();
    await screen.findByTestId("pinakia-stale");

    await user.click(screen.getByTestId("pinakia-stale-retry"));

    await waitFor(() => expect(current()).toHaveTextContent("6"));
    expect(calls()).toBe(2);
    expect(screen.queryByTestId("pinakia-stale")).not.toBeInTheDocument();
  });
});

describe("Ηλεκτρονικά Πινάκια — ρυθμός ανανέωσης", () => {
  it("ανανεώνεται κάθε 15″: ούτε πιο νωρίς ούτε πιο αργά", async () => {
    clock();
    const calls = serveHearings(() => hearingsAt(4));
    renderPinakia();
    await screen.findByTestId("hearing-card-h1");
    expect(calls()).toBe(1);

    await poll(14_999); await settle();
    expect(calls()).toBe(1);
    await poll(1); await settle();
    expect(calls()).toBe(2);
    await poll(15_000); await settle();
    expect(calls()).toBe(3);
  });

  it("μετά το κλείσιμο της οθόνης δεν ρωτά άλλο", async () => {
    clock();
    const calls = serveHearings(() => hearingsAt(4));
    const { unmount } = renderPinakia();
    await screen.findByTestId("hearing-card-h1");

    unmount();
    await poll(120_000); await settle();

    expect(calls()).toBe(1);
  });
});

describe("Ηλεκτρονικά Πινάκια — σειρά των απαντήσεων", () => {
  it("αργή απάντηση που ξεκίνησε νωρίτερα δεν σβήνει νεότερη εικόνα", async () => {
    clock();
    const user = userEvent.setup();
    const slow = gate();
    let slowAnswered = false;
    serveHearings(async (n) => {
      if (n === 1) return hearingsAt(3);
      if (n === 2) { await slow.opened; slowAnswered = true; return hearingsAt(3); }
      return hearingsAt(4);
    });
    serveLiveUpdate();
    renderPinakia();
    await screen.findByTestId("hearing-card-h1");
    expect(current()).toHaveTextContent("3");

    await poll();
    await user.click(screen.getByTestId("advance-number-button"));
    await waitFor(() => expect(current()).toHaveTextContent("4"));
    slow.release();
    await waitFor(() => expect(slowAnswered).toBe(true));
    await settle();

    expect(current()).toHaveTextContent("4");
  });
});

describe("Ηλεκτρονικά Πινάκια — οι αλλαγές νούμερου δεν γυρίζουν το νούμερο πίσω", () => {
  it("το «+1 Επόμενο Νούμερο» στέλνει και το νούμερο που βλέπει ο χρήστης", async () => {
    const user = userEvent.setup();
    serveHearings(() => hearingsAt(4));
    const bodies = serveLiveUpdate();
    renderPinakia();
    await screen.findByTestId("hearing-card-h1");

    await user.click(screen.getByTestId("advance-number-button"));

    await waitFor(() => expect(bodies).toHaveLength(1));
    expect(bodies[0]).toEqual({ current_number: 5, expected_current: 4 });
  });

  it("η «Έναρξη Συνεδρίασης» ξεκινά από το 1 αν δεν έχει ξεκινήσει, και αναφέρει ότι έβλεπε 0", async () => {
    const user = userEvent.setup();
    serveHearings(() => hearingsAt(0, { status: "scheduled" }));
    const bodies = serveLiveUpdate();
    renderPinakia();
    await screen.findByTestId("hearing-card-h1");

    await user.click(screen.getByTestId("start-hearing-button"));

    await waitFor(() => expect(bodies).toHaveLength(1));
    expect(bodies[0]).toEqual({ status: "in_progress", current_number: 1, expected_current: 0 });
  });

  it("η έναρξη από ξεπερασμένη οθόνη (ο server είναι ήδη στο 7) δεν γυρίζει το νούμερο στο 1", async () => {
    const user = userEvent.setup();
    const REFUSED = "Το νούμερο που εκδικάζεται άλλαξε στο 7 (εσείς βλέπατε το 0). Η ενέργεια δεν εφαρμόστηκε· ελέγξτε το τρέχον νούμερο και δοκιμάστε ξανά.";
    serveHearings((n) => (n === 1 ? hearingsAt(0, { status: "scheduled" }) : hearingsAt(7)));
    serveLiveUpdate(() => HttpResponse.json({ detail: REFUSED }, { status: 409 }));
    renderPinakia();
    await screen.findByTestId("hearing-card-h1");

    await user.click(screen.getByTestId("start-hearing-button"));

    expect(await screen.findByText(REFUSED)).toBeInTheDocument();
    await waitFor(() => expect(current()).toHaveTextContent("7"));
  });

  it("αν ο server αρνηθεί το «+1» επειδή το νούμερο άλλαξε: δείχνει το μήνυμα και φέρνει αμέσως την τρέχουσα εικόνα", async () => {
    const user = userEvent.setup();
    const REFUSED = "Το νούμερο που εκδικάζεται άλλαξε στο 11 (εσείς βλέπατε το 4). Η ενέργεια δεν εφαρμόστηκε· ελέγξτε το τρέχον νούμερο και δοκιμάστε ξανά.";
    const calls = serveHearings((n) => (n === 1 ? hearingsAt(4) : hearingsAt(11)));
    serveLiveUpdate(() => HttpResponse.json({ detail: REFUSED }, { status: 409 }));
    renderPinakia();
    await screen.findByTestId("hearing-card-h1");

    await user.click(screen.getByTestId("advance-number-button"));

    expect(await screen.findByText(REFUSED)).toBeInTheDocument();
    await waitFor(() => expect(current()).toHaveTextContent("11"));
    expect(calls()).toBe(2);
  });

  it("αν η αλλαγή δεν έφτασε στον server (πτώση σύνδεσης) δεν γίνεται άσκοπη ανανέωση", async () => {
    const user = userEvent.setup();
    const calls = serveHearings(() => hearingsAt(4));
    serveLiveUpdate(() => HttpResponse.error());
    renderPinakia();
    await screen.findByTestId("hearing-card-h1");

    await user.click(screen.getByTestId("advance-number-button"));

    await screen.findByText(/Network Error|Κάτι πήγε στραβά/);
    expect(calls()).toBe(1);
  });

  it("ο χειροκίνητος «Ορισμός» νούμερου είναι ρητή επιλογή του χρήστη: στέλνει μόνο την τιμή που πληκτρολόγησε", async () => {
    const user = userEvent.setup();
    serveHearings(() => hearingsAt(4));
    const bodies = serveLiveUpdate();
    renderPinakia();
    await screen.findByTestId("hearing-card-h1");

    await user.type(screen.getByTestId("manual-number-input"), "9");
    await user.click(screen.getByTestId("set-number-button"));

    await waitFor(() => expect(bodies).toHaveLength(1));
    expect(bodies[0]).toEqual({ current_number: 9 });
  });
});

describe("Ηλεκτρονικά Πινάκια — ανανέωση όταν η σελίδα ξαναγίνεται ορατή", () => {
  const visibility = (hidden) => {
    Object.defineProperty(document, "hidden", { configurable: true, get: () => hidden });
    document.dispatchEvent(new Event("visibilitychange"));
  };
  afterEach(() => { delete document.hidden; });

  it("όταν η σελίδα γίνει ορατή ανανεώνει αμέσως", async () => {
    const calls = serveHearings((n) => hearingsAt(3 + n));
    renderPinakia();
    await waitFor(() => expect(current()).toHaveTextContent("4"));

    await act(async () => { visibility(false); });

    await waitFor(() => expect(current()).toHaveTextContent("5"));
    expect(calls()).toBe(2);
  });

  it("η ανανέωση κρατά το επιλεγμένο πινάκιο", async () => {
    const two = (n) => HttpResponse.json([hearing({ id: "h1", current_number: n }), hearing({ id: "h2", case_title: "ΓΑΜΜΑ ΑΕ", current_number: 20, pinakio_number: 30 })]);
    const user = userEvent.setup();
    serveHearings((n) => two(n));
    renderPinakia();
    await user.click(await screen.findByTestId("hearing-card-h2"));
    await waitFor(() => expect(current()).toHaveTextContent("20"));

    await act(async () => { window.dispatchEvent(new Event("online")); });
    await settle();

    expect(current()).toHaveTextContent("20");
    expect(screen.getByTestId("my-number")).toHaveTextContent("30");
  });
});
