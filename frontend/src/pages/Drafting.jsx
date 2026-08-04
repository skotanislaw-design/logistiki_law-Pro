import { useEffect, useState } from "react";
import axios, { API, errMsg } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog";
import { Badge } from "@/components/ui/badge";
import { FileText, Sparkle, FileDoc, Copy, Plus, BookOpen, Trash } from "@phosphor-icons/react";
import { toast } from "sonner";

export default function Drafting() {
  const [templates, setTemplates] = useState({ builtin: [], custom: [] });
  const [clauses, setClauses] = useState({ builtin: [], custom: [] });
  const [selectedTpl, setSelectedTpl] = useState(null);
  const [vars, setVars] = useState({});
  const [aiEnhance, setAiEnhance] = useState(false);
  const [instructions, setInstructions] = useState("");
  const [rendering, setRendering] = useState(false);
  const [output, setOutput] = useState(null);
  const [tab, setTab] = useState("templates");
  const [newClauseOpen, setNewClauseOpen] = useState(false);
  const [newClause, setNewClause] = useState({ title: "", category: "Προσωπικά", body: "" });

  const load = async () => {
    try {
      const [t, c] = await Promise.all([
        axios.get(`${API}/drafting/templates`),
        axios.get(`${API}/drafting/clauses`),
      ]);
      setTemplates(t.data);
      setClauses(c.data);
    } catch (e) { toast.error(errMsg(e)); }
  };
  useEffect(() => { load(); }, []);

  const openTemplate = (tpl) => {
    setSelectedTpl(tpl);
    const initial = {};
    (tpl.variables || []).forEach((v) => initial[v] = "");
    setVars(initial);
    setOutput(null);
    setAiEnhance(false);
    setInstructions("");
  };

  const render = async () => {
    if (!selectedTpl) return;
    setRendering(true);
    try {
      const { data } = await axios.post(`${API}/drafting/render`, {
        template_id: selectedTpl.id,
        variables: vars,
        ai_enhance: aiEnhance,
        instructions,
      });
      setOutput(data);
      toast.success("Το έγγραφο συνθέθηκε");
    } catch (e) {
      toast.error(errMsg(e));
    } finally { setRendering(false); }
  };

  const downloadDocx = async () => {
    if (!output) return;
    try {
      const res = await axios.post(`${API}/drafting/render/docx`, output, { responseType: "blob" });
      const url = URL.createObjectURL(res.data);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${(output.title || "document").replace(/[^\w-]/g, "_")}.docx`;
      document.body.appendChild(a); a.click(); a.remove();
      URL.revokeObjectURL(url);
    } catch (e) { toast.error(errMsg(e)); }
  };

  const copyClause = (body) => {
    navigator.clipboard.writeText(body);
    toast.success("Η ρήτρα αντιγράφηκε στο clipboard");
  };

  const saveClause = async () => {
    if (!newClause.title || !newClause.body) return toast.error("Απαιτούνται τίτλος και κείμενο");
    try {
      await axios.post(`${API}/drafting/clauses`, newClause);
      toast.success("Η ρήτρα αποθηκεύτηκε");
      setNewClauseOpen(false);
      setNewClause({ title: "", category: "Προσωπικά", body: "" });
      load();
    } catch (e) { toast.error(errMsg(e)); }
  };

  const deleteClause = async (id) => {
    if (!window.confirm("Διαγραφή ρήτρας;")) return;
    try {
      await axios.delete(`${API}/drafting/clauses/${id}`);
      toast.success("Διαγράφηκε");
      load();
    } catch (e) { toast.error(errMsg(e)); }
  };

  const allTemplates = [...(templates.builtin || []), ...(templates.custom || [])];
  const allClauses = [...(clauses.builtin || []), ...(clauses.custom || [])];
  const byCategory = allTemplates.reduce((acc, t) => { (acc[t.category] = acc[t.category] || []).push(t); return acc; }, {});
  const clausesByCategory = allClauses.reduce((acc, c) => { (acc[c.category] = acc[c.category] || []).push(c); return acc; }, {});

  return (
    <div className="space-y-8" data-testid="drafting-page">
      <div>
        <div className="lux-overline mb-1.5">Nomos One · AI Drafting</div>
        <h1 className="font-brand text-4xl font-bold tracking-wide">Σύνταξη Εγγράφων</h1>
        <p className="text-sm text-muted-foreground mt-1">Πρότυπα + Βιβλιοθήκη ρητρών + AI εξατομίκευση</p>
      </div>

      <div className="flex gap-2 border-b border-border/50">
        <button onClick={() => setTab("templates")} className={`px-4 py-2 text-sm font-medium transition-colors ${tab === "templates" ? "text-[hsl(var(--gold))] border-b-2 border-[hsl(var(--gold))]" : "text-muted-foreground"}`} data-testid="tab-templates">
          Πρότυπα ({allTemplates.length})
        </button>
        <button onClick={() => setTab("clauses")} className={`px-4 py-2 text-sm font-medium transition-colors ${tab === "clauses" ? "text-[hsl(var(--gold))] border-b-2 border-[hsl(var(--gold))]" : "text-muted-foreground"}`} data-testid="tab-clauses">
          Βιβλιοθήκη Ρητρών ({allClauses.length})
        </button>
      </div>

      {tab === "templates" && (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          <div className="space-y-3">
            {Object.entries(byCategory).map(([cat, list]) => (
              <div key={cat} className="lux-card p-4">
                <div className="lux-overline mb-2">{cat}</div>
                <div className="space-y-1.5">
                  {list.map((t) => (
                    <button key={t.id} onClick={() => openTemplate(t)}
                      className={`w-full text-left p-2 rounded-lg text-sm transition-colors ${selectedTpl?.id === t.id ? "bg-[hsl(var(--gold))]/10 text-[hsl(var(--gold))]" : "hover:bg-muted"}`}
                      data-testid={`tpl-${t.id}`}>
                      <FileText size={14} className="inline mr-1.5" /> {t.title}
                      {t.custom && <Badge variant="outline" className="ml-2 text-[9px]">δικό μου</Badge>}
                    </button>
                  ))}
                </div>
              </div>
            ))}
          </div>

          <div className="lg:col-span-2">
            {!selectedTpl ? (
              <div className="lux-card p-12 text-center text-muted-foreground">
                <Sparkle size={40} className="mx-auto text-[hsl(var(--gold))]/40 mb-3" />
                <p>Επιλέξτε πρότυπο από αριστερά για να ξεκινήσετε</p>
              </div>
            ) : (
              <div className="space-y-4">
                <div className="lux-card p-6 space-y-4">
                  <h3 className="font-heading text-lg">{selectedTpl.title}</h3>
                  {(selectedTpl.variables || []).map((v) => (
                    <div key={v}>
                      <Label>{v.replace(/_/g, " ")}</Label>
                      {v.includes("body") || v.includes("facts") || v.includes("demand") || v.includes("terms") || v.includes("matter") ? (
                        <Textarea rows={3} value={vars[v] || ""} onChange={(e) => setVars({ ...vars, [v]: e.target.value })} data-testid={`tpl-var-${v}`} />
                      ) : (
                        <Input value={vars[v] || ""} onChange={(e) => setVars({ ...vars, [v]: e.target.value })} data-testid={`tpl-var-${v}`} />
                      )}
                    </div>
                  ))}
                  <div className="border-t border-border/50 pt-3">
                    <label className="flex items-center gap-2 text-sm cursor-pointer">
                      <input type="checkbox" checked={aiEnhance} onChange={(e) => setAiEnhance(e.target.checked)} data-testid="tpl-ai-enhance" />
                      <Sparkle size={14} className="text-[hsl(var(--gold))]" /> AI εξατομίκευση με οδηγίες
                    </label>
                    {aiEnhance && (
                      <Textarea rows={2} className="mt-2" placeholder="π.χ. Πιο έντονος τόνος, προσθήκη αναφοράς σε άρθρο 288 ΑΚ..."
                        value={instructions} onChange={(e) => setInstructions(e.target.value)} data-testid="tpl-ai-instructions" />
                    )}
                  </div>
                  <Button onClick={render} disabled={rendering} className="w-full" data-testid="render-button">
                    {rendering ? "Σύνθεση..." : "🎯 Σύνθεση εγγράφου"}
                  </Button>
                </div>

                {output && (
                  <div className="lux-card p-6 space-y-3">
                    <div className="flex items-center justify-between">
                      <h3 className="font-heading text-lg">{output.title}</h3>
                      <div className="flex gap-2">
                        <Button size="sm" variant="outline" onClick={() => { navigator.clipboard.writeText(output.body); toast.success("Αντιγράφηκε"); }} data-testid="copy-output">
                          <Copy size={14} className="mr-1" /> Αντιγραφή
                        </Button>
                        <Button size="sm" onClick={downloadDocx} data-testid="download-docx">
                          <FileDoc size={14} className="mr-1" /> Λήψη Word
                        </Button>
                      </div>
                    </div>
                    <Textarea rows={20} value={output.body} onChange={(e) => setOutput({ ...output, body: e.target.value })} className="font-serif text-sm" data-testid="output-body" />
                    <p className="text-xs text-muted-foreground">Ελέγξτε και τροποποιήστε το κείμενο πριν την οριστική χρήση. Το AI παρέχει προσχέδιο — η νομική ευθύνη ανήκει στον δικηγόρο.</p>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      )}

      {tab === "clauses" && (
        <div className="space-y-4">
          <div className="flex justify-end">
            <Button onClick={() => setNewClauseOpen(true)} data-testid="new-clause-button">
              <Plus size={16} className="mr-1.5" /> Προσθήκη Ρήτρας
            </Button>
          </div>
          {Object.entries(clausesByCategory).map(([cat, list]) => (
            <div key={cat}>
              <h3 className="font-heading text-sm mb-2 text-[hsl(var(--gold))]">{cat}</h3>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                {list.map((c) => (
                  <div key={c.id} className="lux-card p-4" data-testid={`clause-${c.id}`}>
                    <div className="flex items-center justify-between mb-2">
                      <div className="flex items-center gap-2">
                        <BookOpen size={14} className="text-[hsl(var(--gold))]" />
                        <span className="font-medium text-sm">{c.title}</span>
                      </div>
                      <div className="flex gap-1">
                        <Button size="icon" variant="ghost" onClick={() => copyClause(c.body)} data-testid={`copy-clause-${c.id}`}><Copy size={14} /></Button>
                        {c.custom && <Button size="icon" variant="ghost" onClick={() => deleteClause(c.id)}><Trash size={14} /></Button>}
                      </div>
                    </div>
                    <p className="text-xs text-muted-foreground line-clamp-4">{c.body}</p>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}

      <Dialog open={newClauseOpen} onOpenChange={setNewClauseOpen}>
        <DialogContent className="max-w-lg" aria-describedby={undefined}>
          <DialogHeader><DialogTitle>Νέα ρήτρα</DialogTitle></DialogHeader>
          <div><Label>Τίτλος</Label><Input value={newClause.title} onChange={(e) => setNewClause({ ...newClause, title: e.target.value })} data-testid="new-clause-title" /></div>
          <div>
            <Label>Κατηγορία</Label>
            <Select value={newClause.category} onValueChange={(v) => setNewClause({ ...newClause, category: v })}>
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>
                {["Γενικές", "Επίλυση Διαφορών", "Οικονομικά", "Λύση", "Συμμόρφωση", "Προσωπικά"].map((c) => <SelectItem key={c} value={c}>{c}</SelectItem>)}
              </SelectContent>
            </Select>
          </div>
          <div><Label>Κείμενο ρήτρας</Label><Textarea rows={6} value={newClause.body} onChange={(e) => setNewClause({ ...newClause, body: e.target.value })} data-testid="new-clause-body" /></div>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setNewClauseOpen(false)}>Ακύρωση</Button>
            <Button onClick={saveClause} data-testid="save-clause-button">Αποθήκευση</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
