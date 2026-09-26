import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterAll, afterEach, beforeAll, vi } from "vitest";

import { server } from "./test/server";

// Το jsdom δεν υλοποιεί matchMedia· το sonner (toasts) το καλεί κατά τη φόρτωση.
if (!window.matchMedia) {
  window.matchMedia = (query) => ({
    matches: false,
    media: query,
    onchange: null,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    addListener: vi.fn(),
    removeListener: vi.fn(),
    dispatchEvent: vi.fn(),
  });
}

// Το ίδιο για το ResizeObserver, που χρησιμοποιούν τα components του Radix.
if (!window.ResizeObserver) {
  window.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
}

// Κάθε αίτημα χωρίς handler είναι σφάλμα: ένα test που περνά επειδή σιωπηλά δεν χτύπησε
// το δίκτυο δεν αποδεικνύει τίποτα.
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => { server.resetHandlers(); cleanup(); });
afterAll(() => server.close());
