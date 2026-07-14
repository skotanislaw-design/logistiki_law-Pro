import { useEffect, useState } from "react";
import axios, { API, money, dateFmt, errMsg } from "@/lib/api";
import { DisclaimerBanner } from "@/components/DisclaimerBanner";
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Legend } from "recharts";
import { TrendUp, TrendDown, Receipt, Clock, CloudArrowUp } from "@phosphor-icons/react";
import { Link } from "react-router-dom";
import { toast } from "sonner";

function StatCard({ label, value, icon: Icon, testid, tone = "default" }) {
  const toneClass = {
    default: "text-foreground",
    danger: "text-destructive",
    success: "text-[hsl(var(--success))]",
  }[tone];
  return (
    <div className="border border-border rounded-sm p-5 bg-card flex flex-col gap-2" data-testid={testid}>
      <div className="flex items-center justify-between">
        <span className="text-xs uppercase tracking-[0.15em] font-semibold text-muted-foreground">{label}</span>
        <Icon size={18} className="text-muted-foreground" />
      </div>
      <span className={`font-mono text-2xl tracking-tight ${toneClass}`}>{value}</span>
    </div>
  );
}

export default function Dashboard() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    axios.get(`${API}/dashboard/summary`)
      .then((res) => setData(res.data))
      .catch((e) => toast.error(errMsg(e)))
      .finally(() => setLoading(false));
  }, []);

  if (loading || !data) {
    return <div className="animate-pulse text-muted-foreground" data-testid="dashboard-loading">Φόρτωση...</div>;
  }

  return (
    <div className="space-y-6" data-testid="dashboard-page">
      <div>
        <h1 className="font-heading text-3xl font-bold tracking-tight">Πίνακας Ελέγχου</h1>
        <p className="text-sm text-muted-foreground mt-1">Τρέχων μήνας — επισκόπηση εσόδων, εξόδων και φορολογικών υποχρεώσεων</p>
      </div>

      <DisclaimerBanner />

      <div className="grid grid-cols-1 md:grid-cols-3 lg:grid-cols-4 gap-4">
        <StatCard label="Έσοδα Μήνα" value={money(data.income_total)} icon={TrendUp} testid="stat-income" tone="success" />
        <StatCard label="Έξοδα Μήνα" value={money(data.expense_total)} icon={TrendDown} testid="stat-expense" />
        <StatCard label="ΦΠΑ προς Πληρωμή" value={money(data.vat_due)} icon={Receipt} testid="vat-due-amount" tone={data.vat_due > 0 ? "danger" : "success"} />
        <StatCard label="Επόμενη Προθεσμία ΦΠΑ" value={dateFmt(data.next_vat_deadline)} icon={Clock} testid="stat-next-deadline" />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <div className="lg:col-span-2 border border-border rounded-sm p-5 bg-card">
          <h3 className="font-heading text-lg font-medium mb-4">Έσοδα vs Έξοδα (6 μήνες)</h3>
          <ResponsiveContainer width="100%" height={260}>
            <BarChart data={data.chart_data}>
              <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" />
              <XAxis dataKey="month" tick={{ fontSize: 12 }} />
              <YAxis tick={{ fontSize: 12 }} />
              <Tooltip formatter={(v) => money(v)} contentStyle={{ fontSize: 12 }} />
              <Legend />
              <Bar dataKey="income" name="Έσοδα" fill="hsl(var(--chart-2))" radius={[2, 2, 0, 0]} />
              <Bar dataKey="expense" name="Έξοδα" fill="hsl(var(--chart-3))" radius={[2, 2, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>

        <div className="border border-border rounded-sm p-5 bg-card flex flex-col gap-4">
          <h3 className="font-heading text-lg font-medium">myDATA & Εκκρεμότητες</h3>
          <div className="flex items-center justify-between text-sm">
            <span className="text-muted-foreground flex items-center gap-2"><CloudArrowUp size={16} /> Εκκρεμή προς myDATA</span>
            <span className="font-mono" data-testid="pending-mydata-count">{data.pending_mydata}</span>
          </div>
          <div className="flex items-center justify-between text-sm">
            <span className="text-muted-foreground">Πρόχειρα τιμολόγια</span>
            <span className="font-mono" data-testid="draft-invoices-count">{data.draft_invoices}</span>
          </div>
          <div className="flex items-center justify-between text-sm">
            <span className="text-muted-foreground">Επόμενη προθεσμία ΕΦΚΑ</span>
            <span className="font-mono">{dateFmt(data.next_efka_deadline)}</span>
          </div>
          <Link to="/mydata" className="text-sm underline underline-offset-4 mt-auto" data-testid="dashboard-mydata-link">
            Μετάβαση στο myDATA →
          </Link>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div className="border border-border rounded-sm p-5 bg-card">
          <h3 className="font-heading text-lg font-medium mb-3">Πρόσφατα Τιμολόγια</h3>
          <div className="divide-y divide-border">
            {data.recent_invoices.length === 0 && <p className="text-sm text-muted-foreground py-4">Δεν υπάρχουν ακόμα τιμολόγια.</p>}
            {data.recent_invoices.map((inv) => (
              <div key={inv._id} className="flex items-center justify-between py-2.5 text-sm">
                <div>
                  <p className="font-medium">{inv.number}</p>
                  <p className="text-muted-foreground text-xs">{inv.client_name}</p>
                </div>
                <span className="font-mono">{money(inv.total)}</span>
              </div>
            ))}
          </div>
        </div>
        <div className="border border-border rounded-sm p-5 bg-card">
          <h3 className="font-heading text-lg font-medium mb-3">Πρόσφατα Έξοδα</h3>
          <div className="divide-y divide-border">
            {data.recent_expenses.length === 0 && <p className="text-sm text-muted-foreground py-4">Δεν υπάρχουν ακόμα έξοδα.</p>}
            {data.recent_expenses.map((ex) => (
              <div key={ex._id} className="flex items-center justify-between py-2.5 text-sm">
                <div>
                  <p className="font-medium">{ex.vendor}</p>
                  <p className="text-muted-foreground text-xs">{ex.category}</p>
                </div>
                <span className="font-mono">{money(ex.amount)}</span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
