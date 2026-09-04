import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import axios, { API, money, dateFmt, errMsg } from "@/lib/api";
import { DisclaimerBanner } from "@/components/DisclaimerBanner";
import { OneScreenDashboard } from "@/components/OneScreenDashboard";
import { useViewMode } from "@/context/ViewModeContext";
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip as ChartTooltip, ResponsiveContainer, Legend } from "recharts";
import { TrendUp, TrendDown, Receipt, Clock, CloudArrowUp, Info, Robot } from "@phosphor-icons/react";
import { Tooltip, TooltipContent, TooltipTrigger, TooltipProvider } from "@/components/ui/tooltip";
import { Link } from "react-router-dom";
import { toast } from "sonner";

function StatCard({ label, value, icon: Icon, testid, tone = "default", hint }) {
  const toneClass = {
    default: "text-foreground",
    danger: "text-destructive",
    success: "text-[hsl(var(--success))]",
  }[tone];
  return (
    <div className="lux-card p-6 flex flex-col gap-2" data-testid={testid}>
      <div className="flex items-center justify-between">
        <span className="text-xs uppercase tracking-[0.15em] font-semibold text-muted-foreground flex items-center gap-1.5">
          {label}
          {hint && (
            <TooltipProvider>
              <Tooltip>
                <TooltipTrigger asChild>
                  <Info size={13} className="cursor-help opacity-70" data-testid={`${testid}-info`} />
                </TooltipTrigger>
                <TooltipContent className="max-w-[220px] normal-case tracking-normal font-normal">{hint}</TooltipContent>
              </Tooltip>
            </TooltipProvider>
          )}
        </span>
        <Icon size={18} className="text-muted-foreground" />
      </div>
      <span className={`font-mono text-2xl tracking-tight ${toneClass}`}>{value}</span>
    </div>
  );
}

export default function Dashboard() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const { oneScreen } = useViewMode();
  const navigate = useNavigate();

  const [autopilot, setAutopilot] = useState(null);

  useEffect(() => {
    // First-time onboarding check
    axios.get(`${API}/settings`).then((r) => {
      if (r.data && r.data.onboarding_completed === false) {
        navigate("/onboarding", { replace: true });
      }
    }).catch(() => {});
    axios.get(`${API}/automation/status`).then((r) => setAutopilot(r.data)).catch(() => {});
    axios.get(`${API}/dashboard/summary`)
      .then((res) => setData(res.data))
      .catch((e) => toast.error(errMsg(e)))
      .finally(() => setLoading(false));
  }, [navigate]);

  if (loading || !data) {
    return <div className="animate-pulse text-muted-foreground" data-testid="dashboard-loading">Φόρτωση...</div>;
  }

  if (oneScreen) return <OneScreenDashboard data={data} />;

  return (
    <div className="space-y-8" data-testid="dashboard-page">
      <div className="animate-in-up">
        <div className="lux-overline mb-1.5">Nomos One · Επισκόπηση</div>
        <h1 className="font-brand text-4xl font-bold tracking-wide">Πίνακας Ελέγχου</h1>
        <p className="text-sm text-muted-foreground mt-1">Τρέχων μήνας — επισκόπηση εσόδων, εξόδων και φορολογικών υποχρεώσεων</p>
      </div>

      <DisclaimerBanner />

      {autopilot && (
        <Link to="/autopilot" className="lux-card p-4 flex items-center gap-4 hover:bg-white/5 transition-colors" data-testid="dashboard-autopilot-card">
          <Robot size={26} className="text-[hsl(var(--gold))] shrink-0" />
          <div className="flex-1 min-w-0">
            <div className="text-sm font-medium">
              Autopilot {autopilot.enabled ? "ενεργό" : "απενεργοποιημένο"} —{" "}
              {autopilot.open_items > 0
                ? `${autopilot.open_items} ${autopilot.open_items === 1 ? "θέμα χρειάζεται" : "θέματα χρειάζονται"} την απόφασή σας`
                : "τίποτα δεν περιμένει εσάς"}
            </div>
            <div className="text-xs text-muted-foreground truncate">
              Τελευταίες 7 ημέρες: {autopilot.last_7_days?.emails_sent || 0} τιμολόγια στάλθηκαν · {autopilot.last_7_days?.reminders_sent || 0} υπενθυμίσεις · {autopilot.last_7_days?.reconciled || 0} εξοφλήσεις από τράπεζα · {autopilot.last_7_days?.mydata_synced || 0} myDATA
            </div>
          </div>
          <span className={`font-mono text-2xl ${autopilot.open_items > 0 ? "text-[hsl(var(--warning))]" : "text-[hsl(var(--success))]"}`} data-testid="dashboard-autopilot-open">{autopilot.open_items}</span>
        </Link>
      )}

      <div className="grid grid-cols-1 md:grid-cols-3 lg:grid-cols-4 gap-5 stagger">
        <StatCard label="Έσοδα Μήνα" value={money(data.income_total)} icon={TrendUp} testid="stat-income" tone="success"
          hint="Το σύνολο της καθαρής αξίας (χωρίς ΦΠΑ) των εκδοθέντων τιμολογίων τον τρέχοντα μήνα. Αυξάνει το ΦΠΑ Εκροών." />
        <StatCard label="Έξοδα Μήνα" value={money(data.expense_total)} icon={TrendDown} testid="stat-expense"
          hint="Το σύνολο των εξόδων (χωρίς ΦΠΑ) τον τρέχοντα μήνα. Αυξάνει το ΦΠΑ Εισροών, μειώνοντας το ΦΠΑ προς πληρωμή." />
        <StatCard label="ΦΠΑ προς Πληρωμή" value={money(data.vat_due)} icon={Receipt} testid="vat-due-amount" tone={data.vat_due > 0 ? "danger" : "success"}
          hint="ΦΠΑ Εκροών (από τιμολόγια) μείον ΦΠΑ Εισροών (από έξοδα). Αν είναι αρνητικό, έχετε πιστωτικό υπόλοιπο ΦΠΑ." />
        <StatCard label="Επόμενη Προθεσμία ΦΠΑ" value={dateFmt(data.next_vat_deadline)} icon={Clock} testid="stat-next-deadline"
          hint="Ημερομηνία υποβολής & πληρωμής της επόμενης δήλωσης ΦΠΑ, βάσει της περιόδου (μηνιαία/τριμηνιαία) που έχετε ορίσει στις Ρυθμίσεις." />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-5 stagger">
        <div className="lg:col-span-2 lux-card p-6">
          <h3 className="font-heading text-lg font-medium mb-1">Έσοδα vs Έξοδα (6 μήνες)</h3>
          <div className="gold-rule mb-4">◆</div>
          <ResponsiveContainer width="100%" height={260}>
            <BarChart data={data.chart_data}>
              <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" vertical={false} />
              <XAxis dataKey="month" tick={{ fontSize: 12, fill: "hsl(221 15% 64%)" }} axisLine={false} tickLine={false} />
              <YAxis tick={{ fontSize: 12, fill: "hsl(221 15% 64%)" }} axisLine={false} tickLine={false} />
              <ChartTooltip
                formatter={(v) => money(v)}
                cursor={{ fill: "hsl(43 60% 52% / 0.06)" }}
                contentStyle={{ fontSize: 12, background: "hsl(224 42% 8%)", border: "1px solid hsl(43 60% 52% / 0.3)", borderRadius: 4 }}
              />
              <Legend />
              <Bar dataKey="income" name="Έσοδα" fill="hsl(var(--gold))" radius={[3, 3, 0, 0]} />
              <Bar dataKey="expense" name="Έξοδα" fill="hsl(222 30% 32%)" radius={[3, 3, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>

        <div className="lux-card p-6 flex flex-col gap-4">
          <h3 className="font-heading text-lg font-medium">myDATA & Εκκρεμότητες</h3>
          <div className="gold-rule">◆</div>
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

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-5 stagger">
        <div className="lux-card p-6">
          <h3 className="font-heading text-lg font-medium mb-1">Πρόσφατα Τιμολόγια</h3>
          <div className="gold-rule mb-3">◆</div>
          <div className="divide-y divide-border">
            {data.recent_invoices.length === 0 && <p className="text-sm text-muted-foreground py-4">Δεν υπάρχουν ακόμα τιμολόγια.</p>}
            {data.recent_invoices.map((inv) => (
              <div key={inv.id} className="flex items-center justify-between py-2.5 text-sm">
                <div>
                  <p className="font-medium">{inv.number}</p>
                  <p className="text-muted-foreground text-xs">{inv.client_name}</p>
                </div>
                <span className="font-mono">{money(inv.total)}</span>
              </div>
            ))}
          </div>
        </div>
        <div className="lux-card p-6">
          <h3 className="font-heading text-lg font-medium mb-1">Πρόσφατα Έξοδα</h3>
          <div className="gold-rule mb-3">◆</div>
          <div className="divide-y divide-border">
            {data.recent_expenses.length === 0 && <p className="text-sm text-muted-foreground py-4">Δεν υπάρχουν ακόμα έξοδα.</p>}
            {data.recent_expenses.map((ex) => (
              <div key={ex.id} className="flex items-center justify-between py-2.5 text-sm">
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
