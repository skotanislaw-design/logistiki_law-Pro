// Το vitest τρέχει **δίπλα** στο craco, χωρίς να αγγίζει το build. Έτσι αποκτά η
// εφαρμογή δίχτυ ασφαλείας πριν αλλάξει το σύστημα κατασκευής, όχι μετά.
//
// Κατάληξη .mjs: το @vitejs/plugin-react είναι ESM-only και το package.json δεν δηλώνει
// "type": "module" (θα έσπαγε το craco.config.js, που είναι CommonJS).
import { fileURLToPath } from "url";
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// Η εφαρμογή δείχνει και υπολογίζει ημερομηνίες **Ελλάδας** (βλ. src/lib/utils.js), όποια κι αν είναι η ζώνη της συσκευής.
// Αν οι δοκιμές έτρεχαν στη ζώνη του μηχανήματος που τις εκτελεί (Αθήνα, ή UTC στο CI), κάθε οθόνη που διάβαζε
// `new Date().getDate()`/`toISOString()` θα περνούσε χωρίς να φανεί. Γι' αυτό το μηχάνημα των δοκιμών ορίζεται σε ζώνη
// μακριά από την Ελλάδα (UTC−11)· η ζώνη μπορεί να αλλάξει με NOMOS_TEST_TZ (π.χ. Pacific/Kiritimati, UTC+14).
process.env.TZ = process.env.NOMOS_TEST_TZ || "Pacific/Pago_Pago";

export default defineConfig({
  plugins: [react()],
  // Ίδιο alias με το jsconfig.json, που χρησιμοποιεί το craco.
  resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
  define: {
    // Ο κώδικας χτίζει το API ως `${process.env.REACT_APP_BACKEND_URL}/api`.
    // Απόλυτη διεύθυνση, ώστε να ταιριάζουν καθαρά οι handlers του MSW.
    "process.env.REACT_APP_BACKEND_URL": JSON.stringify("http://localhost:8001"),
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/setupTests.js"],
    include: ["src/**/*.test.{js,jsx}"],
  },
});
