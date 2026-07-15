import { money, dateFmt } from "@/lib/api";
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip as ChartTooltip, ResponsiveContainer } from "recharts";
import { Lightbulb, CalendarCheck } from "@phosphor-icons/react";

function Kpi({ label, value, tone = "default", testid, sub }) {
  const toneClass = {
    default: "text-foreground",
    gold: "text-[hsl(var(--gold))]",
    danger: "text-destructive",
    success: "text-[hsl(var(--success))]",
  }[tone];
  return (
    <div className="lux-card px-5 py-4" data-testid={testid}>
      <div className="text-[10px] uppercase tracking-[0.2em] text-muted-foreground mb-1.5">{label}</div>
      <div className={`font-mono text-xl md:text-2xl tracking-tight ${toneClass}`}>{value}</div>
      {sub && <div className="text-[11px] text-muted-foreground mt-0.5">{sub}</div>}
    </div>
  );
}

export function OneScreenDashboard({ data }) {
  const today = new Date().toLocaleDateString("el-GR", { weekday: "long", day: "numeric", month: "long", year: "numeric" });
  return (
    <div className="space-y-5" data-testid="one-screen-dashboard">
      <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-5 gap-3 stagger">
        <Kpi label="Έσοδα Έτους" value={money(data.year_income)} tone="gold" testid="os-year-income" />
        <Kpi label={`Ανείσπρακτα (${data.unpaid_count})`} value={money(data.unpaid_total)} tone="danger" testid="os-unpaid" />
        <Kpi label="ΦΠΑ Πληρωτέος" value={money(data.vat_due)} tone={data.vat_due > 0 ? "gold" : "success"} testid="os-vat-due" />
        <Kpi label="Κέρδος Έτους" value={money(data.year_profit)} tone="success" testid="os-year-profit" />
        <Kpi label="myDATA Εκκρεμή" value={data.pending_mydata} tone={data.pending_mydata > 0 ? "danger" : "success"} testid="os-mydata-pending" />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-[1fr_320px] gap-5 items-start">
        <div className="space-y-5">
          <div className="animate-in-up">
            <h1 className="font-brand text-3xl font-bold tracking-wide">Επισκόπηση</h1>
            <p className="text-sm text-muted-foreground">{today}</p>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-5 stagger">
            <div className="lux-card p-5" data-testid="os-vat-card">
              <div className="lux-overline mb-3">ΦΠΑ Τρέχουσας Περιόδου</div>
              <div className="space-y-2 text-sm">
                <div className="flex justify-between"><span className="text-muted-foreground">ΦΠΑ εκροών (πωλήσεις)</span><span className="font-mono">{money(data.output_vat)}</span></div>
                <div className="flex justify-between"><span className="text-muted-foreground">− ΦΠΑ εισροών (δαπάνες)</span><span className="font-mono text-destructive">{money(data.input_vat)}</span></div>
                <div className="border-t border-[hsl(var(--gold))]/15 pt-2 flex justify-between font-medium">
                  <span>Πληρωτέος ΦΠΑ</span><span className="font-mono text-[hsl(var(--gold))]">{money(data.vat_due)}</span>
                </div>
                <div className="flex justify-between text-xs text-muted-foreground pt-1">
                  <span>Προθεσμία δήλωσης</span><span>{dateFmt(data.next_vat_deadline)}</span>
                </div>
              </div>
            </div>

            <div className="lux-card p-5" data-testid="os-recent-card">
              <div className="lux-overline mb-3">Πρόσφατα Τιμολόγια</div>
              <div className="divide-y divide-border/50">
                {data.recent_invoices.length === 0 && <p className="text-sm text-muted-foreground py-2">Δεν υπάρχουν τιμολόγια.</p>}
                {data.recent_invoices.slice(0, 4).map((inv) => (
                  <div key={inv.id} className="flex items-center justify-between py-1.5 text-sm">
                    <span className="truncate mr-2">{inv.number} <span className="text-muted-foreground text-xs">{inv.client_name}</span></span>
                    <span className="font-mono">{money(inv.total)}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>

          <div className="lux-card p-5 animate-in-up" data-testid="os-chart-card">
            <div className="lux-overline mb-3">Έσοδα / Έξοδα ανά μήνα</div>
            <ResponsiveContainer width="100%" height={180}>
              <BarChart data={data.chart_data}>
                <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" vertical={false} />
                <XAxis dataKey="month" tick={{ fontSize: 11, fill: "hsl(221 15% 64%)" }} axisLine={false} tickLine={false} />
                <YAxis tick={{ fontSize: 11, fill: "hsl(221 15% 64%)" }} axisLine={false} tickLine={false} />
                <ChartTooltip
                  formatter={(v) => money(v)}
                  cursor={{ fill: "hsl(43 60% 52% / 0.06)" }}
                  contentStyle={{ fontSize: 12, background: "hsl(224 42% 8%)", border: "1px solid hsl(43 60% 52% / 0.3)", borderRadius: 4 }}
                />
                <Bar dataKey="income" name="Έσοδα" fill="hsl(var(--gold))" radius={[3, 3, 0, 0]} />
                <Bar dataKey="expense" name="Έξοδα" fill="hsl(222 30% 32%)" radius={[3, 3, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>

        <div className="space-y-4 stagger">
          <div className="lux-card p-5" data-testid="os-advice-card">
            <div className="lux-overline mb-3 flex items-center gap-1.5"><Lightbulb size={13} className="text-[hsl(var(--gold))]" /> Ο Λογιστης Προτεινει</div>
            <div className="border-l-2 border-[hsl(var(--gold))] pl-3">
              <p className="text-sm leading-relaxed text-foreground/85">{data.advice}</p>
            </div>
          </div>

          <div className="lux-card p-5" data-testid="os-obligations-card">
            <div className="lux-overline mb-3 flex items-center gap-1.5"><CalendarCheck size={13} className="text-[hsl(var(--gold))]" /> Επομενες Υποχρεωσεις</div>
            <div className="space-y-3">
              {data.obligations.map((o, i) => (
                <div key={i} className="flex items-start justify-between gap-2 text-sm border-b border-border/40 last:border-0 pb-2.5 last:pb-0">
                  <div>
                    <div className="text-xs text-muted-foreground font-mono">{dateFmt(o.date)}</div>
                    <div className="leading-snug">{o.label}</div>
                  </div>
                  {o.amount != null && <span className="font-mono text-xs shrink-0">{money(o.amount)}</span>}
                </div>
              ))}
              {data.obligations.length === 0 && <p className="text-sm text-muted-foreground">Καμία εκκρεμής υποχρέωση.</p>}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
