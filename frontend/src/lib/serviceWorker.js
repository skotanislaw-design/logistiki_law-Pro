// Εγγραφή του service worker (public/service-worker.js) και ειδοποίηση όταν βγει νέα έκδοση της εφαρμογής.
//
// Μια εφαρμογή γραφείου μένει ανοιχτή όλη μέρα, οπότε η ενημέρωση δεν μπορεί να περιμένει την επόμενη επίσκεψη: ο
// browser ελέγχει το αρχείο του worker μόνο όταν ανοίγει σελίδα (και το πολύ μία φορά το 24ωρο), και μια σελίδα που
// τρέχει από το πρωί θα έμενε στην έκδοση του πρωινού. Εδώ ο έλεγχος γίνεται κάθε ώρα. Όταν ο νέος worker αναλάβει,
// ο χρήστης ειδοποιείται να ανανεώσει — δεν ανανεώνουμε μόνοι μας, γιατί θα χάνονταν μισοσυμπληρωμένες φόρμες.

export const UPDATE_CHECK_MS = 60 * 60 * 1000;

/**
 * @param {{ onUpdate?: () => void, container?: ServiceWorkerContainer, url?: string }} options
 * @returns {() => void} σταματά τον περιοδικό έλεγχο και την ακρόαση
 */
export function registerServiceWorker({ onUpdate, container = globalThis.navigator?.serviceWorker, url = "/service-worker.js" } = {}) {
  if (!container) return () => {};

  // Αν η σελίδα δεν ελεγχόταν ήδη από worker, η πρώτη ανάληψη είναι η πρώτη εγκατάσταση — δεν υπάρχει «παλιά έκδοση».
  let hadController = !!container.controller;
  const onControllerChange = () => {
    if (hadController) onUpdate?.();
    hadController = true;
  };
  container.addEventListener("controllerchange", onControllerChange);

  let timer = null;
  let stopped = false;
  container.register(url).then((registration) => {
    if (stopped) return;
    timer = setInterval(() => { registration.update().catch(() => {}); }, UPDATE_CHECK_MS);
  }).catch(() => {});

  return () => {
    stopped = true;
    container.removeEventListener("controllerchange", onControllerChange);
    if (timer !== null) clearInterval(timer);
  };
}
