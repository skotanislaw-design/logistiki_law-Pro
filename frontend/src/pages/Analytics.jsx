import { useEffect, useState } from "react";
import axios, { API, money, dateFmt, errMsg } from "@/lib/api";
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip as ChartTooltip, ResponsiveContainer, Cell, PieChart, Pie } from "recharts";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Trophy, ChartPieSlice, Receipt, Percent } from "@phosphor-icons/react";
import { toast } from "sonner";

const GOLD_TONES = ["#c9a227", "#e5c15a", "#8a6f1e", "#f4d97a", "#a8871f", "#deb944"];

export default function Analytics() {
  const [data, setData] = useState(null);
  useEffect(() => {
    axios.get(`${API}/analytics/profitability`).then((r) => setData(r.data)).catch((e) => toast.error(errMsg(e)));
  }, []);
  if (!data) return <div className="text-muted-foreground">Φόρτωση αναλυτικών...</div>;
  const t = data.totals;
  return (
    <div className="space-y-8" data-testid="analytics-page">
      <div>
        <div className="lux-overline mb-1.5">Nomos One · Επιχειρηματική Ευφυΐα</div>
        <h1 className="font-brand text-4xl font-bold tracking-wide">Κερδοφορία & Ανάλυση</h1>
        <p className="text-sm text-muted-foreground mt-1">Έτος {new Date().getFullYear()} — από {dateFmt(data.period.start)} έως σήμερα</p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        <div className="lux-card p-5">
          <div className="lux-overline">Έσοδα Έτους</div>
          <div className="mt-2 font-mono text-3xl font-semibold text-[hsl(var(--gold))]" data-testid="analytics-revenue">{money(t.revenue)}</div>
          <div className="text-xs text-muted-foreground mt-1">{t.invoice_count} τιμολόγια</div>
        </div>
        <div className="lux-card p-5">
          <div className="lux-overline">Εισπραγμένα</div>
          <div className="mt-2 font-mono text-3xl font-semibold text-[hsl(var(--success))]" data-testid="analytics-paid">{money(t.paid)}</div>
          <div className="text-xs text-muted-foreground mt-1">Ποσοστό: {t.collection_rate_pct}%</div>
        </div>
        <div className="lux-card p-5">
          <div className="lux-overline">Ανείσπρακτα</div>
          <div className="mt-2 font-mono text-3xl font-semibold text-[hsl(var(--destructive))]" data-testid="analytics-unpaid">{money(t.unpaid)}</div>
          <div className="text-xs text-muted-foreground mt-1">Απαιτούν υπενθύμιση</div>
        </div>
        <div className="lux-card p-5">
          <div className="lux-overline">Μέσο Τιμολόγιο</div>
          <div className="mt-2 font-mono text-3xl font-semibold">{money(t.avg_invoice)}</div>
          <div className="text-xs text-muted-foreground mt-1">Καθαρή αξία</div>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
        <div className="lux-card p-6 lg:col-span-2">
          <div className="flex items-center gap-2 mb-4">
            <Trophy size={20} className="text-[hsl(var(--gold))]" />
            <h3 className="font-heading text-lg">Top 10 Πελάτες</h3>
          </div>
          <ResponsiveContainer width="100%" height={280}>
            <BarChart data={data.top_clients} layout="vertical">
              <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" opacity={0.3} />
              <XAxis type="number" stroke="hsl(var(--muted-foreground))" fontSize={11} />
              <YAxis type="category" dataKey="client_name" stroke="hsl(var(--muted-foreground))" fontSize={11} width={120} />
              <ChartTooltip contentStyle={{ background: "hsl(var(--card))", border: "1px solid hsl(var(--border))", borderRadius: 8 }} />
              <Bar dataKey="total_revenue" fill="hsl(var(--gold))" name="Έσοδα" radius={[0, 4, 4, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
        <div className="lux-card p-6">
          <div className="flex items-center gap-2 mb-4">
            <ChartPieSlice size={20} className="text-[hsl(var(--gold))]" />
            <h3 className="font-heading text-lg">Ανά Τύπο Υπόθεσης</h3>
          </div>
          {data.case_types.length > 0 ? (
            <ResponsiveContainer width="100%" height={220}>
              <PieChart>
                <Pie data={data.case_types} dataKey="revenue" nameKey="case_type" cx="50%" cy="50%" outerRadius={80}>
                  {data.case_types.map((_, i) => <Cell key={i} fill={GOLD_TONES[i % GOLD_TONES.length]} />)}
                </Pie>
                <ChartTooltip contentStyle={{ background: "hsl(var(--card))", border: "1px solid hsl(var(--border))", borderRadius: 8 }} />
              </PieChart>
            </ResponsiveContainer>
          ) : <div className="text-xs text-muted-foreground py-8 text-center">Χωρίς δεδομένα</div>}
        </div>
      </div>

      <div className="lux-card overflow-hidden">
        <div className="px-4 py-3 border-b border-border/60"><span className="font-heading text-sm">Λεπτομερής Ανάλυση Πελατών</span></div>
        <Table data-testid="analytics-clients-table">
          <TableHeader>
            <TableRow>
              <TableHead>Πελάτης</TableHead>
              <TableHead className="text-right">Τιμολόγια</TableHead>
              <TableHead className="text-right">Έσοδα (καθαρά)</TableHead>
              <TableHead className="text-right">Εισπραγμένα</TableHead>
              <TableHead className="text-right">Ανείσπρακτα</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {data.top_clients.map((c) => (
              <TableRow key={c.client_id}>
                <TableCell className="font-medium">{c.client_name}</TableCell>
                <TableCell className="text-right font-mono">{c.invoice_count}</TableCell>
                <TableCell className="text-right font-mono">{money(c.total_revenue)}</TableCell>
                <TableCell className="text-right font-mono text-[hsl(var(--success))]">{money(c.paid_revenue)}</TableCell>
                <TableCell className="text-right font-mono text-[hsl(var(--destructive))]">{money(c.unpaid_revenue)}</TableCell>
              </TableRow>
            ))}
            {data.top_clients.length === 0 && (
              <TableRow><TableCell colSpan={5} className="text-center text-muted-foreground py-8">Δεν υπάρχουν δεδομένα φέτος.</TableCell></TableRow>
            )}
          </TableBody>
        </Table>
      </div>

      {data.case_types.length > 0 && (
        <div className="lux-card overflow-hidden">
          <div className="px-4 py-3 border-b border-border/60"><span className="font-heading text-sm">Απόδοση ανά Τύπο Υπόθεσης</span></div>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Τύπος</TableHead>
                <TableHead className="text-right">Υποθέσεις</TableHead>
                <TableHead className="text-right">Έσοδα</TableHead>
                <TableHead className="text-right">Μέσος όρος</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.case_types.map((c) => (
                <TableRow key={c.case_type}>
                  <TableCell>{c.case_type}</TableCell>
                  <TableCell className="text-right font-mono">{c.case_count}</TableCell>
                  <TableCell className="text-right font-mono">{money(c.revenue)}</TableCell>
                  <TableCell className="text-right font-mono text-[hsl(var(--gold))]">{money(c.avg_per_case)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
    </div>
  );
}
