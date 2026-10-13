/**
 * Γύρος 5 (δεύτερος έλεγχος, ελεγκτής D — D-02) — ο service worker ενημερώνεται.
 *
 * Πριν: το όνομα της κρυφής μνήμης ήταν σταθερό ("nomos-one-v1") και τα bytes του worker ίδια σε κάθε έκδοση, οπότε ο browser
 * δεν τον εγκαθιστούσε ποτέ ξανά· η αρχική σελίδα και τα bundles σερβίρονταν cache-first για πάντα. Μετά από κάθε
 * ανάπτυξη οι επιστρέφοντες χρήστες έβλεπαν την πρώτη έκδοση που είχαν ανοίξει (και δεν έβλεπαν καμία διόρθωση), μέχρι να
 * καθαρίσουν τα δεδομένα του ιστότοπου.
 *
 * Τρέχουμε τον ΠΡΑΓΜΑΤΙΚΟ `public/service-worker.js` σε sandbox (vm) με ψεύτικο CacheStorage και ψεύτικο δίκτυο, ώστε να
 * φαίνεται η συμπεριφορά και όχι το κείμενο του αρχείου.
 */
import { createHash } from "node:crypto";
import fs from "node:fs";
import vm from "node:vm";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const read = (relative) => fs.readFileSync(fileURLToPath(new URL(relative, import.meta.url)), "utf8");
const WORKER_SOURCE = process.env.NOMOS_SW_FILE ? fs.readFileSync(process.env.NOMOS_SW_FILE, "utf8") : read("../../public/service-worker.js");
const DOCKERFILE = read("../../Dockerfile");

const ORIGIN = "https://app.test";
const NETWORK = "(δεν παρεμβλήθηκε ο worker)";

function site(build) {
  return {
    "/": `ΚΕΛΥΦΟΣ ${build}`, "/manifest.json": `{"build":"${build}"}`, "/nomos-logo.png": `λογότυπο ${build}`,
    "/letterhead-logo.png": "letterhead", "/favicon.png": "favicon", [`/static/js/main.${build}.js`]: `ΠΑΚΕΤΟ ${build}`,
  };
}

/** Ένας browser: ΜΙΑ κρυφή μνήμη που επιβιώνει ανάμεσα σε εκδόσεις του worker, και ένας server που αλλάζει με τις αναπτύξεις. */
function makeBrowser() {
  const store = new Map();
  const server = { files: site("A"), offline: false, status: {} };       // status: διαδρομή → κωδικός σφάλματος που επιστρέφει ο server
  const requests = [];
  const counters = { skipWaiting: 0, claim: 0 };
  let handlers = {};

  const href = (input) => new URL(typeof input === "string" ? input : input.url, ORIGIN).href;

  async function network(input) {
    const url = new URL(href(input));
    requests.push({ path: url.pathname + url.search, cache: input.cache });
    if (server.offline) throw new TypeError("Failed to fetch");
    if (server.status[url.pathname]) return new Response("σφάλμα", { status: server.status[url.pathname] });
    const body = server.files[url.pathname];
    if (body !== undefined) return new Response(body, { status: 200 });
    // nginx: ό,τι μοιάζει με αρχείο και λείπει είναι 404· οι υπόλοιπες διαδρομές πέφτουν στο index.html της εφαρμογής.
    if (/\.[A-Za-z0-9]+$/.test(url.pathname)) return new Response("δεν βρέθηκε", { status: 404 });
    return new Response(server.files["/"], { status: 200 });
  }

  const caches = {
    async open(name) {
      if (!store.has(name)) store.set(name, new Map());
      const entries = store.get(name);
      return {
        async addAll(list) {
          const answers = await Promise.all(list.map(network));
          answers.forEach((res, i) => {
            if (!res.ok) throw new TypeError("addAll: κακή απάντηση");
            entries.set(href(list[i]), res);
          });
        },
        async put(request, response) { entries.set(href(request), response); },
        async match(request) { const saved = entries.get(href(request)); return saved ? saved.clone() : undefined; },
      };
    },
    async keys() { return [...store.keys()]; },
    async delete(name) { return store.delete(name); },
  };

  class WorkerRequest {
    constructor(input, init = {}) { this.url = href(input); this.method = "GET"; this.mode = "cors"; this.cache = init.cache; }
  }

  /** Ο browser κατεβάζει τον worker (με σφραγίδα build) και τον εγκαθιστά· η κρυφή μνήμη παραμένει. */
  async function startWorker(build) {
    handlers = {};
    const self = {
      addEventListener: (type, fn) => { handlers[type] = fn; },
      skipWaiting: () => { counters.skipWaiting += 1; },
      clients: { claim: () => { counters.claim += 1; } },
      location: { origin: ORIGIN },
    };
    vm.runInNewContext(WORKER_SOURCE.replace("__BUILD_ID__", build), { self, caches, fetch: network, URL, Request: WorkerRequest, Response, console });
    await lifecycle("install");
    await lifecycle("activate");
  }

  async function lifecycle(type) {
    const waits = [];
    handlers[type]({ waitUntil: (p) => waits.push(p) });
    await Promise.all(waits);
  }

  /** Ανοίγει μια διαδρομή. `navigate` = ο χρήστης πατά σύνδεσμο/ανανεώνει· αλλιώς αίτημα της σελίδας (αρχεία). */
  async function open(path, { mode = "navigate", method = "GET", url } = {}) {
    const request = { url: url || ORIGIN + path, method, mode };
    const waits = [];
    let responded;
    handlers.fetch({ request, respondWith: (p) => { responded = p; }, waitUntil: (p) => waits.push(p) });
    if (responded === undefined) return NETWORK;
    let response;
    try { response = await responded; } catch (e) { return `ΣΦΑΛΜΑ ${e.message}`; }
    await Promise.all(waits);
    return `${response.status} ${await response.text()}`;
  }

  const deploy = (build) => { server.files = site(build); };
  return { server, requests, counters, caches, startWorker, open, deploy, store };
}

// ======================================================================== νέα έκδοση ====

describe("μια νέα ανάπτυξη φτάνει στον χρήστη που έχει ήδη επισκεφθεί την εφαρμογή", () => {
  it("η αρχική σελίδα, οι σελίδες της εφαρμογής και τα νέα αρχεία έρχονται από τη νέα έκδοση", async () => {
    const b = makeBrowser();
    await b.startWorker("A");
    expect(await b.open("/")).toBe("200 ΚΕΛΥΦΟΣ A");
    expect(await b.open("/static/js/main.A.js", { mode: "no-cors" })).toBe("200 ΠΑΚΕΤΟ A");
    expect(await b.open("/payment/success?session_id=cs_1")).toBe("200 ΚΕΛΥΦΟΣ A");

    b.deploy("B");                      // νέα ανάπτυξη: νέο κέλυφος, νέα bundles, το παλιό bundle δεν υπάρχει πια
    await b.startWorker("B");

    expect(await b.open("/")).toBe("200 ΚΕΛΥΦΟΣ B");
    expect(await b.open("/payment/success?session_id=cs_1")).toBe("200 ΚΕΛΥΦΟΣ B");
    expect(await b.open("/clients")).toBe("200 ΚΕΛΥΦΟΣ B");
    expect(await b.open("/static/js/main.B.js", { mode: "no-cors" })).toBe("200 ΠΑΚΕΤΟ B");
    expect(await b.open("/manifest.json", { mode: "no-cors" })).toBe('200 {"build":"B"}');
  });

  it("το παλιό bundle δεν σερβίρεται από την κρυφή μνήμη της προηγούμενης έκδοσης", async () => {
    const b = makeBrowser();
    await b.startWorker("A");
    await b.open("/static/js/main.A.js", { mode: "no-cors" });
    b.deploy("B");
    await b.startWorker("B");

    expect(await b.open("/static/js/main.A.js", { mode: "no-cors" })).toBe("404 δεν βρέθηκε");
  });

  it("η κρυφή μνήμη της παλιάς έκδοσης σβήνεται και μένει μόνο η τρέχουσα", async () => {
    const b = makeBrowser();
    await b.startWorker("A");
    expect(await b.caches.keys()).toEqual(["nomos-one-A"]);
    b.deploy("B");
    await b.startWorker("B");

    expect(await b.caches.keys()).toEqual(["nomos-one-B"]);
  });

  it("ο νέος worker αναλαμβάνει αμέσως (skipWaiting + claim)", async () => {
    const b = makeBrowser();
    await b.startWorker("A");
    expect(b.counters).toEqual({ skipWaiting: 1, claim: 1 });
  });

  it("κάθε build έχει δικό του όνομα κρυφής μνήμης, άρα και διαφορετικά bytes worker", () => {
    const stamped = (id) => WORKER_SOURCE.replace("__BUILD_ID__", id);
    expect(stamped("a1b2")).not.toBe(stamped("c3d4"));
    expect(stamped("a1b2")).toContain("nomos-one-${BUILD}");
  });
});

describe("ακόμη κι αν ο worker δεν ξανασφραγιστεί, η αρχική σελίδα δεν μένει παλιά", () => {
  it("οι σελίδες ρωτούν πρώτα τον server (με επαλήθευση), όχι την κρυφή μνήμη", async () => {
    const b = makeBrowser();
    await b.startWorker("A");
    await b.open("/");
    b.deploy("B");                      // ο server άλλαξε, ο worker (ίδια bytes) όχι

    expect(await b.open("/")).toBe("200 ΚΕΛΥΦΟΣ B");
    expect(await b.open("/clients")).toBe("200 ΚΕΛΥΦΟΣ B");
    const lastShellRequest = [...b.requests].reverse().find((r) => r.path === "/");
    expect(lastShellRequest.cache).toBe("no-cache");
  });

  it("η εγκατάσταση φορτώνει το κέλυφος παρακάμπτοντας την κρυφή μνήμη του browser", async () => {
    const b = makeBrowser();
    await b.startWorker("A");
    const installRequests = b.requests.slice(0, 5);
    expect(installRequests.map((r) => r.path).sort()).toEqual(["/", "/favicon.png", "/letterhead-logo.png", "/manifest.json", "/nomos-logo.png"]);
    expect(installRequests.every((r) => r.cache === "reload")).toBe(true);
  });
});

// ======================================================================== χωρίς σύνδεση ====

describe("χωρίς σύνδεση", () => {
  async function visited() {
    const b = makeBrowser();
    await b.startWorker("A");
    await b.open("/");
    await b.open("/static/js/main.A.js", { mode: "no-cors" });
    b.server.offline = true;
    return b;
  }

  it("οποιαδήποτε σελίδα της εφαρμογής ανοίγει με το τελευταίο κέλυφος που φορτώθηκε", async () => {
    const b = await visited();
    expect(await b.open("/clients")).toBe("200 ΚΕΛΥΦΟΣ A");
    expect(await b.open("/")).toBe("200 ΚΕΛΥΦΟΣ A");
  });

  it("το κέλυφος που ισχύει εκτός σύνδεσης είναι το τελευταίο που φορτώθηκε με σύνδεση", async () => {
    const b = makeBrowser();
    await b.startWorker("A");
    b.deploy("B");
    await b.open("/");                  // με σύνδεση: έρχεται το B και αποθηκεύεται
    b.server.offline = true;

    expect(await b.open("/invoices")).toBe("200 ΚΕΛΥΦΟΣ B");
  });

  it("τα bundles και τα στατικά αρχεία που έχουν ήδη φορτωθεί σερβίρονται από την κρυφή μνήμη", async () => {
    const b = await visited();
    expect(await b.open("/static/js/main.A.js", { mode: "no-cors" })).toBe("200 ΠΑΚΕΤΟ A");
    expect(await b.open("/manifest.json", { mode: "no-cors" })).toBe('200 {"build":"A"}');
    expect(await b.open("/nomos-logo.png", { mode: "no-cors" })).toBe("200 λογότυπο A");
  });

  it("ό,τι δεν έχει ξαναφορτωθεί αποτυγχάνει καθαρά, δεν επινοείται απάντηση", async () => {
    const b = await visited();
    expect(await b.open("/static/js/never-seen.js", { mode: "no-cors" })).toBe("ΣΦΑΛΜΑ Failed to fetch");
  });

  it("η εγκατάσταση δεν αποτυγχάνει όταν δεν υπάρχει σύνδεση", async () => {
    const b = makeBrowser();
    b.server.offline = true;
    await expect(b.startWorker("A")).resolves.toBeUndefined();
    expect(b.counters.skipWaiting).toBe(1);
  });
});

// ======================================================================== αρχεία με hash ====

describe("τα αρχεία του /static/ (με hash στο όνομα) σερβίρονται μία φορά από το δίκτυο", () => {
  it("η δεύτερη φόρτωση του ίδιου bundle δεν ρωτά ξανά τον server", async () => {
    const b = makeBrowser();
    await b.startWorker("A");
    await b.open("/static/js/main.A.js", { mode: "no-cors" });
    const before = b.requests.filter((r) => r.path === "/static/js/main.A.js").length;
    await b.open("/static/js/main.A.js", { mode: "no-cors" });

    expect(before).toBe(1);
    expect(b.requests.filter((r) => r.path === "/static/js/main.A.js").length).toBe(1);
  });

  it("μια αποτυχημένη απάντηση δεν αποθηκεύεται: όταν το αρχείο εμφανιστεί, έρχεται κανονικά", async () => {
    const b = makeBrowser();
    await b.startWorker("A");
    expect(await b.open("/static/js/late.js", { mode: "no-cors" })).toBe("404 δεν βρέθηκε");
    b.server.files["/static/js/late.js"] = "ΑΡΓΟΠΟΡΗΜΕΝΟ";

    expect(await b.open("/static/js/late.js", { mode: "no-cors" })).toBe("200 ΑΡΓΟΠΟΡΗΜΕΝΟ");
  });

  it("μια αποτυχημένη απάντηση σελίδας δεν αντικαθιστά το αποθηκευμένο κέλυφος", async () => {
    const b = makeBrowser();
    await b.startWorker("A");
    await b.open("/");
    b.server.status["/"] = 503;                    // προσωρινό σφάλμα του server

    expect(await b.open("/")).toBe("503 σφάλμα");
    b.server.offline = true;

    expect(await b.open("/clients")).toBe("200 ΚΕΛΥΦΟΣ A");
  });
});

// ======================================================================== τι δεν αγγίζει ποτέ ====

describe("ό,τι δεν πρέπει να περάσει από την κρυφή μνήμη πηγαίνει κατευθείαν στον browser", () => {
  const untouched = [
    ["κλήση API", "/api/clients", { mode: "cors" }],
    ["κλήση API (με παράμετρο)", "/api/bank/transactions?status=all", { mode: "cors" }],
    ["σελίδα πύλης πελάτη", "/portal/abc123", { mode: "navigate" }],
    ["Stripe", "/stripe/webhook", { mode: "cors" }],
    ["POST σε σελίδα", "/", { mode: "cors", method: "POST" }],
    ["PUT", "/static/js/main.A.js", { mode: "cors", method: "PUT" }],
    ["άλλο origin (γραμματοσειρές)", "/css2", { mode: "no-cors", url: "https://fonts.googleapis.com/css2" }],
  ];

  it.each(untouched)("%s", async (_name, path, options) => {
    const b = makeBrowser();
    await b.startWorker("A");
    const before = b.requests.length;

    expect(await b.open(path, options)).toBe(NETWORK);
    expect(b.requests.length).toBe(before);
  });

  it("τίποτα από αυτά δεν έχει αποθηκευτεί στην κρυφή μνήμη", async () => {
    const b = makeBrowser();
    await b.startWorker("A");
    for (const [, path, options] of untouched) await b.open(path, options);

    const cached = [...b.store.get("nomos-one-A").keys()].map((u) => new URL(u).pathname).sort();
    expect(cached).toEqual(["/", "/favicon.png", "/letterhead-logo.png", "/manifest.json", "/nomos-logo.png"]);
  });
});

// ======================================================================== σφραγίδα build ====

describe("η σφραγίδα του build", () => {
  it("ο worker έχει ένα και μόνο ένα σημείο για τον κωδικό του build", () => {
    expect(WORKER_SOURCE.match(/__BUILD_ID__/g)).toHaveLength(1);
    expect(WORKER_SOURCE).toContain('const BUILD = "__BUILD_ID__";');
    expect(WORKER_SOURCE).toContain("const CACHE = `nomos-one-${BUILD}`;");
  });

  it("το Dockerfile τον αντικαθιστά μετά το build και σταματά αν μείνει ανέγγιχτος", () => {
    const build = DOCKERFILE.indexOf("RUN yarn build");
    const stamp = DOCKERFILE.indexOf('sed -i "s/__BUILD_ID__/${BUILD_ID}/g" build/service-worker.js');
    expect(build).toBeGreaterThan(-1);
    expect(stamp).toBeGreaterThan(build);
    expect(DOCKERFILE).toContain('BUILD_ID="$(sha256sum build/index.html | cut -c1-16)"');
    expect(DOCKERFILE).toContain('! grep -q "__BUILD_ID__" build/service-worker.js');
    expect(DOCKERFILE).toContain('test -n "$BUILD_ID"');
  });

  it("η αντικατάσταση του Dockerfile παράγει worker που δουλεύει", async () => {
    // Ό,τι κάνει το sed του Dockerfile, για δύο διαφορετικά index.html.
    const stampedBy = (indexHtml) => {
      const id = createHash("sha256").update(indexHtml).digest("hex").slice(0, 16);      // sha256sum build/index.html | cut -c1-16
      return { id, source: WORKER_SOURCE.replace(/__BUILD_ID__/g, id) };
    };
    const a = stampedBy("<script src=main.aaa.js>");
    const b = stampedBy("<script src=main.bbb.js>");

    expect(a.id).toMatch(/^[0-9a-f]{16}$/);
    expect(a.source).not.toBe(b.source);
    expect(a.source).not.toContain("__BUILD_ID__");
    expect(a.source).toContain(`const BUILD = "${a.id}";`);
    expect(() => new vm.Script(a.source)).not.toThrow();
  });
});
