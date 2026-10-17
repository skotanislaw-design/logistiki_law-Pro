/**
 * Οι λίστες των φορμών αντιστοίχισης (lib/matters.js): τι ζητούν, πότε, και τι γίνεται όταν κάτι πάει στραβά.
 *
 * Το hook το μοιράζονται τα Έξοδα, τα Τιμολόγια, οι Υποθέσεις και το παράθυρο λεπτομερειών τιμολογίου. Οι οθόνες το
 * ελέγχουν εμμέσως· εδώ ελέγχεται απευθείας, ώστε μια αλλαγή του να μη βασίζεται στο ότι «κάποια οθόνη θα το πιάσει».
 */
import { act, renderHook, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";

import { LAWYER_ROLES, caseLabel, useMatterLookups, withCurrent } from "@/lib/matters";
import { CASE_A, CASE_B, CLIENT_A, CLIENT_B, USERS, lookupHandlers } from "@/test/lookupFixtures";
import { server } from "@/test/server";

const API = "http://localhost:8001/api";

/** Handlers που μετρούν πόσες φορές ζητήθηκε κάθε λίστα. */
function counting(replies = {}) {
  const calls = { cases: 0, clients: 0, users: 0 };
  const answer = (key, fallback) => () => {
    calls[key] += 1;
    return replies[key] ? replies[key]() : HttpResponse.json(fallback);
  };
  server.use(
    http.get(`${API}/cases`, answer("cases", [CASE_A, CASE_B])),
    http.get(`${API}/clients`, answer("clients", [CLIENT_A, CLIENT_B])),
    http.get(`${API}/auth/users`, answer("users", USERS)),
  );
  return calls;
}

const settle = () => act(() => new Promise((resolve) => setTimeout(resolve, 60)));

describe("useMatterLookups — τι φορτώνει", () => {
  it("φέρνει υποθέσεις, πελάτες και δικηγόρους· δικηγόροι είναι μόνο διαχειριστές και δικηγόροι, όχι ο λογιστής", async () => {
    server.use(...lookupHandlers({ cases: [CASE_A, CASE_B], clients: [CLIENT_A, CLIENT_B], users: USERS }));
    const { result } = renderHook(() => useMatterLookups(true));

    await waitFor(() => expect(result.current.loaded).toBe(true));
    expect(result.current.cases.map((c) => c.id)).toEqual(["case-a", "case-b"]);
    expect(result.current.clients.map((c) => c.id)).toEqual(["client-a", "client-b"]);
    expect(result.current.lawyers.map((u) => u.id)).toEqual(["u-law", "u-law2"]);
    expect(result.current.lawyers.every((u) => LAWYER_ROLES.includes(u.role))).toBe(true);
    expect(result.current.failed).toEqual({});
  });

  it("δεν ζητά τίποτα όσο δεν είναι ενεργό (η φόρμα δεν έχει ανοίξει)", async () => {
    const calls = counting();
    const { result } = renderHook(() => useMatterLookups(false));

    await settle();
    expect(calls).toEqual({ cases: 0, clients: 0, users: 0 });
    expect(result.current.loaded).toBe(false);
  });

  it("φορτώνει όταν ενεργοποιηθεί, και μόνο μία φορά", async () => {
    const calls = counting();
    const { result, rerender } = renderHook(({ on }) => useMatterLookups(on), { initialProps: { on: false } });
    await settle();
    expect(calls.cases).toBe(0);

    rerender({ on: true });
    await waitFor(() => expect(result.current.loaded).toBe(true));
    rerender({ on: true });
    await settle();
    expect(calls).toEqual({ cases: 1, clients: 1, users: 1 });
  });

  it("ζητά μόνο τους δικηγόρους όταν οι υποθέσεις και οι πελάτες δεν χρειάζονται", async () => {
    const calls = counting();
    const { result } = renderHook(() => useMatterLookups(true, { cases: false, clients: false }));

    await waitFor(() => expect(result.current.loaded).toBe(true));
    expect(calls).toEqual({ cases: 0, clients: 0, users: 1 });
    expect(result.current.cases).toEqual([]);
    expect(result.current.clients).toEqual([]);
    expect(result.current.lawyers).toHaveLength(2);
  });

  it("δεν ζητά τους χρήστες όταν δεν χρειάζονται δικηγόροι (ο λογαριασμός μπορεί να μη δικαιούται τη λίστα)", async () => {
    const calls = counting();
    const { result } = renderHook(() => useMatterLookups(true, { lawyers: false }));

    await waitFor(() => expect(result.current.loaded).toBe(true));
    expect(calls).toEqual({ cases: 1, clients: 1, users: 0 });
    expect(result.current.lawyers).toEqual([]);
    expect(result.current.failed).toEqual({});
  });

  it("δεν ζητά υποθέσεις όταν δεν χρειάζονται", async () => {
    const calls = counting();
    const { result } = renderHook(() => useMatterLookups(true, { cases: false }));

    await waitFor(() => expect(result.current.loaded).toBe(true));
    expect(calls).toEqual({ cases: 0, clients: 1, users: 1 });
  });

  it("δεν ζητά πελάτες όταν δεν χρειάζονται", async () => {
    const calls = counting();
    const { result } = renderHook(() => useMatterLookups(true, { clients: false }));

    await waitFor(() => expect(result.current.loaded).toBe(true));
    expect(calls).toEqual({ cases: 1, clients: 0, users: 1 });
  });
});

describe("useMatterLookups — όταν κάτι πάει στραβά", () => {
  it("σφάλμα server σε μία λίστα την αφήνει άδεια και τη σημειώνει, χωρίς να χαλά τις άλλες", async () => {
    server.use(...lookupHandlers({ cases: "fail", clients: [CLIENT_A], users: USERS }));
    const { result } = renderHook(() => useMatterLookups(true));

    await waitFor(() => expect(result.current.loaded).toBe(true));
    expect(result.current.failed).toEqual({ cases: true });
    expect(result.current.cases).toEqual([]);
    expect(result.current.clients).toHaveLength(1);
    expect(result.current.lawyers).toHaveLength(2);
  });

  it("απάντηση 200 που ΔΕΝ είναι λίστα (σελίδα σφάλματος proxy, αντικείμενο) μετρά ως αποτυχία και δεν σπάει τίποτα", async () => {
    counting({
      cases: () => HttpResponse.text("<html><body>502 Bad Gateway</body></html>", { headers: { "Content-Type": "text/html" } }),
      clients: () => HttpResponse.json({ detail: "κάτι άλλο" }),
      users: () => HttpResponse.json(null),
    });
    const { result } = renderHook(() => useMatterLookups(true));

    await waitFor(() => expect(result.current.loaded).toBe(true));
    expect(result.current.failed).toEqual({ cases: true, clients: true, lawyers: true });
    expect(result.current.cases).toEqual([]);
    expect(result.current.clients).toEqual([]);
    expect(result.current.lawyers).toEqual([]);
  });

  it("αποτυχία δικτύου δηλώνεται όπως το σφάλμα server", async () => {
    server.use(
      http.get(`${API}/auth/users`, () => HttpResponse.error()),           // πρώτο: κερδίζει το πρώτο handler που ταιριάζει
      ...lookupHandlers({ cases: [CASE_A], clients: [CLIENT_A], users: USERS }),
    );
    const { result } = renderHook(() => useMatterLookups(true));

    await waitFor(() => expect(result.current.loaded).toBe(true));
    expect(result.current.failed).toEqual({ lawyers: true });
    expect(result.current.lawyers).toEqual([]);
    expect(result.current.cases).toHaveLength(1);
  });
});

describe("withCurrent", () => {
  const options = [{ value: "a", label: "Α" }, { value: "b", label: "Β" }];

  it("αφήνει τη λίστα όπως είναι όταν δεν υπάρχει τρέχουσα τιμή ή υπάρχει ήδη μέσα", () => {
    expect(withCurrent(options, "", "(άγνωστο)")).toBe(options);
    expect(withCurrent(options, undefined, "(άγνωστο)")).toBe(options);
    expect(withCurrent(options, "b", "(άγνωστο)")).toBe(options);
  });

  it("προσθέτει στο τέλος την τρέχουσα τιμή που λείπει, ώστε το Select να μη δείχνει κενό", () => {
    expect(withCurrent(options, "z", "(άγνωστο)")).toEqual([...options, { value: "z", label: "(άγνωστο)" }]);
  });

  it("δεν αλλάζει την αρχική λίστα", () => {
    withCurrent(options, "z", "(άγνωστο)");
    expect(options).toHaveLength(2);
  });
});

describe("caseLabel", () => {
  it("δείχνει αριθμό και τίτλο, ό,τι από τα δύο υπάρχει, και τίποτα για ανύπαρκτη υπόθεση", () => {
    expect(caseLabel(CASE_A)).toBe("ΥΠΘ-2026-0001 — Αγωγή αποζημίωσης");
    expect(caseLabel({ title: "Μόνο τίτλος" })).toBe("Μόνο τίτλος");
    expect(caseLabel({ number: "ΥΠΘ-1" })).toBe("ΥΠΘ-1");
    expect(caseLabel(null)).toBe("");
    expect(caseLabel(undefined)).toBe("");
  });
});
