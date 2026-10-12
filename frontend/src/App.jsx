import "@/App.css";
import { BrowserRouter, Routes, Route } from "react-router-dom";
import { Toaster } from "@/components/ui/sonner";
import { AuthProvider } from "@/context/AuthContext";
import { ViewModeProvider } from "@/context/ViewModeContext";
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
import Copilot from "@/pages/Copilot";
import Documents from "@/pages/Documents";
import Cases from "@/pages/Cases";
import CaseDetail from "@/pages/CaseDetail";
import Grammatia from "@/pages/Grammatia";
import Paravola from "@/pages/Paravola";
import Pinakia from "@/pages/Pinakia";
import TrustAccounts from "@/pages/TrustAccounts";
import Onboarding from "@/pages/Onboarding";
import Kyc from "@/pages/Kyc";
import CashFlow from "@/pages/CashFlow";
import Analytics from "@/pages/Analytics";
import Signatures from "@/pages/Signatures";
import ClientPortal from "@/pages/ClientPortal";
import PaymentResult from "@/pages/PaymentResult";
import Drafting from "@/pages/Drafting";
import Ocr from "@/pages/Ocr";
import LegalSearch from "@/pages/LegalSearch";
import PocketPinakia from "@/pages/PocketPinakia";
import Autopilot from "@/pages/Autopilot";
import Bank from "@/pages/Bank";

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
        <ViewModeProvider>
        <BrowserRouter>
          <Routes>
            <Route path="/login" element={<Login />} />
            <Route path="/" element={<Protected><Dashboard /></Protected>} />
            <Route path="/autopilot" element={<Protected><Autopilot /></Protected>} />
            <Route path="/invoices" element={<Protected><Invoices /></Protected>} />
            <Route path="/expenses" element={<Protected><Expenses /></Protected>} />
            <Route path="/clients" element={<Protected><Clients /></Protected>} />
            <Route path="/vat" element={<Protected><Vat /></Protected>} />
            <Route path="/bank" element={<Protected><Bank /></Protected>} />
            <Route path="/mydata" element={<Protected><MyData /></Protected>} />
            <Route path="/payroll" element={<Protected><Payroll /></Protected>} />
            <Route path="/tax-calendar" element={<Protected><TaxCalendar /></Protected>} />
            <Route path="/settings" element={<Protected><Settings /></Protected>} />
            <Route path="/copilot" element={<Protected><Copilot /></Protected>} />
            <Route path="/documents" element={<Protected><Documents /></Protected>} />
            <Route path="/cases" element={<Protected><Cases /></Protected>} />
            <Route path="/cases/:id" element={<Protected><CaseDetail /></Protected>} />
            <Route path="/grammatia" element={<Protected><Grammatia /></Protected>} />
            <Route path="/paravola" element={<Protected><Paravola /></Protected>} />
            <Route path="/pinakia" element={<Protected><Pinakia /></Protected>} />
            <Route path="/trust" element={<Protected><TrustAccounts /></Protected>} />
            <Route path="/onboarding" element={<Protected><Onboarding /></Protected>} />
            <Route path="/kyc" element={<Protected><Kyc /></Protected>} />
            <Route path="/cashflow" element={<Protected><CashFlow /></Protected>} />
            <Route path="/analytics" element={<Protected><Analytics /></Protected>} />
            <Route path="/signatures" element={<Protected><Signatures /></Protected>} />
            <Route path="/drafting" element={<Protected><Drafting /></Protected>} />
            <Route path="/ocr" element={<Protected><Ocr /></Protected>} />
            <Route path="/legal-search" element={<Protected><LegalSearch /></Protected>} />
            <Route path="/pocket" element={<Protected><PocketPinakia /></Protected>} />
            <Route path="/portal/:token" element={<ClientPortal />} />
            <Route path="/payment/success" element={<PaymentResult mode="success" />} />
            <Route path="/payment/cancel" element={<PaymentResult mode="cancel" />} />
          </Routes>
        </BrowserRouter>
        </ViewModeProvider>
        <Toaster />
      </AuthProvider>
    </div>
  );
}

export default App;
