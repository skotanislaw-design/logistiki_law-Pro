import React from "react";
import ReactDOM from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { toast } from "sonner";
import "@/index.css";
import App from "@/App";
import { registerServiceWorker } from "@/lib/serviceWorker";

window.addEventListener("error", (e) => {
  if (e.message?.includes("ResizeObserver loop")) e.stopImmediatePropagation();
});

// Register PWA service worker. Όταν βγει νέα έκδοση, ο χρήστης ειδοποιείται να ανανεώσει (βλ. lib/serviceWorker.js).
if ("serviceWorker" in navigator && process.env.NODE_ENV === "production") {
  window.addEventListener("load", () => {
    registerServiceWorker({
      onUpdate: () => toast.info("Διαθέσιμη νέα έκδοση της εφαρμογής", {
        id: "app-update",
        duration: Infinity,
        description: "Ανανεώστε τη σελίδα για να τη χρησιμοποιήσετε. Ό,τι δεν έχετε αποθηκεύσει θα χαθεί.",
        action: { label: "Ανανέωση", onClick: () => window.location.reload() },
      }),
    });
  });
}

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 60_000,
      refetchOnWindowFocus: false,
    },
  },
});

const root = ReactDOM.createRoot(document.getElementById("root"));
root.render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>
  </React.StrictMode>,
);
