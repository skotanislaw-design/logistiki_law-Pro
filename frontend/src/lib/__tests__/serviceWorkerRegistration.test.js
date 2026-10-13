/**
 * Γύρος 5 (δεύτερος έλεγχος, ελεγκτής D — D-02) — εγγραφή του service worker και ειδοποίηση «νέα έκδοση».
 *
 * Η εφαρμογή μένει ανοιχτή όλη μέρα· ο browser ελέγχει για νέο worker μόνο όταν ανοίγει σελίδα. Εδώ ελέγχεται ότι (α) ο
 * έλεγχος επαναλαμβάνεται κάθε ώρα, (β) η πρώτη εγκατάσταση δεν ειδοποιεί ως «νέα έκδοση», (γ) κάθε επόμενη αλλαγή worker
 * ειδοποιεί, (δ) ο χρήστης δεν χάνει τίποτα χωρίς να το αποφασίσει (δεν γίνεται αυτόματη ανανέωση).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { UPDATE_CHECK_MS, registerServiceWorker } from "@/lib/serviceWorker";

function fakeContainer({ controller = null, registerResult } = {}) {
  const listeners = new Map();
  const registration = { update: vi.fn(() => Promise.resolve()) };
  const container = {
    controller,
    register: vi.fn(() => (registerResult ? registerResult(registration) : Promise.resolve(registration))),
    addEventListener: vi.fn((type, fn) => { listeners.set(type, fn); }),
    removeEventListener: vi.fn((type, fn) => { if (listeners.get(type) === fn) listeners.delete(type); }),
  };
  const fire = (type) => listeners.get(type)?.();
  return { container, registration, fire, listeners };
}

const settle = () => vi.advanceTimersByTimeAsync(0);

beforeEach(() => { vi.useFakeTimers(); });
afterEach(() => { vi.useRealTimers(); });

describe("εγγραφή", () => {
  it("εγγράφει τον worker από τη διαδρομή /service-worker.js", async () => {
    const { container } = fakeContainer();
    registerServiceWorker({ container });
    await settle();
    expect(container.register).toHaveBeenCalledWith("/service-worker.js");
  });

  it("χωρίς υποστήριξη service worker δεν κάνει τίποτα και δεν χαλά", () => {
    const stop = registerServiceWorker({ container: undefined, onUpdate: vi.fn() });
    expect(typeof stop).toBe("function");
    expect(() => stop()).not.toThrow();
  });

  it("αποτυχία εγγραφής δεν διακόπτει την εφαρμογή", async () => {
    const { container } = fakeContainer({ registerResult: () => Promise.reject(new Error("blocked")) });
    const onUpdate = vi.fn();
    expect(() => registerServiceWorker({ container, onUpdate })).not.toThrow();
    await settle();
    await vi.advanceTimersByTimeAsync(UPDATE_CHECK_MS * 3);
    expect(onUpdate).not.toHaveBeenCalled();
  });
});

describe("νέα έκδοση", () => {
  it("η πρώτη εγκατάσταση (η σελίδα δεν είχε worker) δεν ειδοποιεί", () => {
    const { container, fire } = fakeContainer({ controller: null });
    const onUpdate = vi.fn();
    registerServiceWorker({ container, onUpdate });

    fire("controllerchange");

    expect(onUpdate).not.toHaveBeenCalled();
  });

  it("όταν η σελίδα ελεγχόταν ήδη από worker, η αλλαγή του ειδοποιεί", () => {
    const { container, fire } = fakeContainer({ controller: {} });
    const onUpdate = vi.fn();
    registerServiceWorker({ container, onUpdate });

    fire("controllerchange");

    expect(onUpdate).toHaveBeenCalledTimes(1);
  });

  it("μετά την πρώτη εγκατάσταση, η επόμενη αλλαγή ειδοποιεί", () => {
    const { container, fire } = fakeContainer({ controller: null });
    const onUpdate = vi.fn();
    registerServiceWorker({ container, onUpdate });

    fire("controllerchange");           // πρώτη ανάληψη: καμία ειδοποίηση
    fire("controllerchange");           // δεύτερη έκδοση

    expect(onUpdate).toHaveBeenCalledTimes(1);
  });

  it("η ειδοποίηση είναι προαιρετική", () => {
    const { container, fire } = fakeContainer({ controller: {} });
    registerServiceWorker({ container });
    expect(() => fire("controllerchange")).not.toThrow();
  });

  it("η εγγραφή δεν ανανεώνει τη σελίδα μόνη της", () => {
    const reload = vi.fn();
    const { container, fire } = fakeContainer({ controller: {} });
    registerServiceWorker({ container, onUpdate: () => {} });
    const original = window.location;
    Object.defineProperty(window, "location", { configurable: true, value: { ...original, reload } });
    try {
      fire("controllerchange");
    } finally {
      Object.defineProperty(window, "location", { configurable: true, value: original });
    }
    expect(reload).not.toHaveBeenCalled();
  });
});

describe("τακτικός έλεγχος για νέα έκδοση", () => {
  it("ελέγχει κάθε ώρα, όχι συχνότερα", async () => {
    const { container, registration } = fakeContainer();
    registerServiceWorker({ container });
    await settle();

    await vi.advanceTimersByTimeAsync(UPDATE_CHECK_MS - 1);
    expect(registration.update).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1);
    expect(registration.update).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(UPDATE_CHECK_MS * 2);
    expect(registration.update).toHaveBeenCalledTimes(3);
  });

  it("η ώρα ελέγχου είναι μία ώρα", () => {
    expect(UPDATE_CHECK_MS).toBe(3_600_000);
  });

  it("αποτυχία του ελέγχου (χωρίς δίκτυο) δεν σταματά τους επόμενους", async () => {
    const { container, registration } = fakeContainer();
    registration.update.mockRejectedValueOnce(new Error("offline"));
    registerServiceWorker({ container });
    await settle();

    await vi.advanceTimersByTimeAsync(UPDATE_CHECK_MS);
    await vi.advanceTimersByTimeAsync(UPDATE_CHECK_MS);

    expect(registration.update).toHaveBeenCalledTimes(2);
  });

  it("το stop σταματά ελέγχους και ακρόαση", async () => {
    const { container, registration, fire, listeners } = fakeContainer({ controller: {} });
    const onUpdate = vi.fn();
    const stop = registerServiceWorker({ container, onUpdate });
    await settle();

    stop();
    await vi.advanceTimersByTimeAsync(UPDATE_CHECK_MS * 2);
    fire("controllerchange");

    expect(registration.update).not.toHaveBeenCalled();
    expect(onUpdate).not.toHaveBeenCalled();
    expect(listeners.size).toBe(0);
  });

  it("αν το stop κληθεί πριν ολοκληρωθεί η εγγραφή, δεν ξεκινά ποτέ χρονομετρητής", async () => {
    let finish;
    const registration = { update: vi.fn(() => Promise.resolve()) };
    const { container } = fakeContainer({ registerResult: () => new Promise((resolve) => { finish = () => resolve(registration); }) });
    const stop = registerServiceWorker({ container });

    stop();
    finish();
    await settle();
    await vi.advanceTimersByTimeAsync(UPDATE_CHECK_MS * 2);

    expect(registration.update).not.toHaveBeenCalled();
  });
});
