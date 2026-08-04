import { useEffect, useState } from "react";
import axios, { API, money, dateFmt, errMsg } from "@/lib/api";
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip as ChartTooltip, ResponsiveContainer, Legend, Line, ComposedChart } from "recharts";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { TrendUp, TrendDown, ChartLineUp, Wallet, Warning } from "@phosphor-icons/react";
import { toast } from "sonner";

const CONF_LABEL = { high: "Υψηλή", medium: "Μεσαία", low: "Χαμηλή" };
const CONF_STYLE = { high: "text-[hsl(var(--success))]", medium: "text-[hsl(var(--gold))]", low: "text-muted-foreground" };

export default function CashFlow() {
  const [data, setData] = useState(null);

  useEffect(() => {
    axios.get(`${API}/analytics/cashflow`).then((r) => setData(r.data)).catch((e) => toast.error(errMsg(e)));
  }, []);

  if (!data) return <div className="text-muted-foreground">Φόρτωση προβλέψεων...</div>;

  return (
    <div className="space-y-8" data-testid="cashflow-page">
      <div>
        <div className="lux-overline mb-1.5">Nomos One · Επιχειρηματική Ευφυΐα</div>
        <h1 className="font-brand text-4xl font-bold tracking-wide">Πρόβλεψη Ταμείου</h1>
        <p className="text-sm text-muted-foreground mt-1">
          Επόμενοι 3 μήνες: {dateFmt(data.horizon.start)} → {dateFmt(data.horizon.end)}
        </p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <div className="lux-card p-5">
          <div className="flex items-center justify-between">
            <span className="lux-overline">Αναμενόμενες Εισπράξεις</span>
            <TrendUp size={18} className="text-[hsl(var(--success))]" />
          </div>
          <div className="mt-2 font-mono text-3xl font-semibold text-[hsl(var(--success))]" data-testid="cf-income">{money(data.total_expected_income)}</div>
          <div className="text-xs text-muted-foreground mt-1">Από ανείσπρακτα τιμολόγια</div>
        </div>
        <div className="lux-card p-5">
          <div className="flex items-center justify-between">
            <span className="lux-overline">Αναμενόμενα Έξοδα</span>
            <TrendDown size={18} className="text-[hsl(var(--destructive))]" />
          </div>
          <div className="mt-2 font-mono text-3xl font-semibold text-[hsl(var(--destructive))]" data-testid="cf-expense">{money(data.total_expected_expense)}</div>
          <div className="text-xs text-muted-foreground mt-1">Λειτουργικά + φόροι + ΕΦΚΑ</div>
        </div>
        <div className="lux-card p-5">
          <div className="flex items-center justify-between">
            <span className="lux-overline">Καθαρή Ροή</span>
            <Wallet size={18} className={data.net >= 0 ? "text-[hsl(var(--success))]" : "text-[hsl(var(--destructive))]"} />
          </div>
          <div className={`mt-2 font-mono text-3xl font-semibold ${data.net >= 0 ? "text-[hsl(var(--success))]" : "text-[hsl(var(--destructive))]"}`} data-testid="cf-net">
            {money(data.net)}
          </div>
          <div className="text-xs text-muted-foreground mt-1">Πλεόνασμα / Έλλειμμα</div>
        </div>
      </div>

      {data.net < 0 && (
        <div className="lux-card p-4 border-[hsl(var(--destructive))]/40 flex items-start gap-3">
          <Warning size={20} className="text-[hsl(var(--destructive))] shrink-0 mt-0.5" />
          <div className="text-sm">
            <div className="font-medium text-[hsl(var(--destructive))]">Προσοχή: Αρνητική πρόβλεψη ταμείου</div>
            <p className="text-muted-foreground mt-1">
              Οι υποχρεώσεις σας υπερβαίνουν τις αναμενόμενες εισπράξεις. Στείλτε υπενθυμίσεις για τα ανείσπρακτα τιμολόγια
              και εξετάστε επιτάχυνση εισπράξεων ή αναδιάρθρωση εξόδων.
            </p>
          </div>
        </div>
      )}

      <div className="lux-card p-6">
        <h3 className="font-heading text-lg mb-4">Μηνιαία Ροή (3μηνο)</h3>
        <ResponsiveContainer width="100%" height={280}>
          <ComposedChart data={data.monthly}>
            <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" opacity={0.3} />
            <XAxis dataKey="month" stroke="hsl(var(--muted-foreground))" fontSize={12} />
            <YAxis stroke="hsl(var(--muted-foreground))" fontSize={12} />
            <ChartTooltip contentStyle={{ background: "hsl(var(--card))", border: "1px solid hsl(var(--border))", borderRadius: 8 }} />
            <Legend />
            <Bar dataKey="income" fill="hsl(var(--success))" name="Εισπράξεις" />
            <Bar dataKey="expense" fill="hsl(var(--destructive))" name="Έξοδα" />
            <Line type="monotone" dataKey="net" stroke="hsl(var(--gold))" name="Καθαρή Ροή" strokeWidth={2} />
          </ComposedChart>
        </ResponsiveContainer>
      </div>

      <div className="lux-card overflow-hidden">
        <div className="px-4 py-3 border-b border-border/60 flex items-center gap-2">
          <ChartLineUp size={18} className="text-[hsl(var(--gold))]" />
          <span className="font-heading text-sm">Χρονολογικές κινήσεις</span>
        </div>
        <Table data-testid="cashflow-events-table">
          <TableHeader>
            <TableRow>
              <TableHead>Ημερομηνία</TableHead>
              <TableHead>Περιγραφή</TableHead>
              <TableHead>Πιθανότητα</TableHead>
              <TableHead className="text-right">Ποσό</TableHead>
              <TableHead className="text-right">Υπόλοιπο</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {data.events.map((e, i) => (
              <TableRow key={i}>
                <TableCell className="font-mono text-xs">{dateFmt(e.date)}</TableCell>
                <TableCell className="text-sm">{e.label}</TableCell>
                <TableCell><span className={`text-xs ${CONF_STYLE[e.confidence]}`}>{CONF_LABEL[e.confidence]}</span></TableCell>
                <TableCell className={`text-right font-mono ${e.type === "income" ? "text-[hsl(var(--success))]" : "text-[hsl(var(--destructive))]"}`}>
                  {e.type === "income" ? "+" : "−"}{money(e.amount)}
                </TableCell>
                <TableCell className={`text-right font-mono font-semibold ${e.running_balance >= 0 ? "" : "text-[hsl(var(--destructive))]"}`}>
                  {money(e.running_balance)}
                </TableCell>
              </TableRow>
            ))}
            {data.events.length === 0 && (
              <TableRow><TableCell colSpan={5} className="text-center text-muted-foreground py-8">Καμία εκκρεμότητα στο τρίμηνο.</TableCell></TableRow>
            )}
          </TableBody>
        </Table>
      </div>
    </div>
  );
}
