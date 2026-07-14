import "@/App.css";
import { BrowserRouter, Routes, Route } from "react-router-dom";
import { Toaster } from "@/components/ui/sonner";
import { AuthProvider } from "@/context/AuthContext";
import { ProtectedRoute } from "@/components/ProtectedRoute";
import { AppLayout } from "@/components/Layout/AppLayout";
import Login from "@/pages/Login";
import Dashboard from "@/pages/Dashboard";
import Invoices from "@/pages/Invoices";
import Expenses from "@/pages/Expenses";
import Clients from "@/pages/Clients";
import Vat from "@/pages/Vat";
import MyData from "@/pages/MyData";
import Payroll from "@/pages/Payroll";
import TaxCalendar from "@/pages/TaxCalendar";
import Settings from "@/pages/Settings";

function Protected({ children }) {
  return (
    <ProtectedRoute>
      <AppLayout>{children}</AppLayout>
    </ProtectedRoute>
  );
}

function App() {
  return (
    <div className="App">
      <AuthProvider>
        <BrowserRouter>
          <Routes>
            <Route path="/login" element={<Login />} />
            <Route path="/" element={<Protected><Dashboard /></Protected>} />
            <Route path="/invoices" element={<Protected><Invoices /></Protected>} />
            <Route path="/expenses" element={<Protected><Expenses /></Protected>} />
            <Route path="/clients" element={<Protected><Clients /></Protected>} />
            <Route path="/vat" element={<Protected><Vat /></Protected>} />
            <Route path="/mydata" element={<Protected><MyData /></Protected>} />
            <Route path="/payroll" element={<Protected><Payroll /></Protected>} />
            <Route path="/tax-calendar" element={<Protected><TaxCalendar /></Protected>} />
            <Route path="/settings" element={<Protected><Settings /></Protected>} />
          </Routes>
        </BrowserRouter>
        <Toaster />
      </AuthProvider>
    </div>
  );
}

export default App;
