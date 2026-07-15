import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "@/context/AuthContext";
import { errMsg } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { AuroraBackground } from "@/components/AuroraBackground";
import { toast } from "sonner";

export default function Login() {
  const [email, setEmail] = useState("admin@logistiki.gr");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const { login } = useAuth();
  const navigate = useNavigate();

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      await login(email, password);
      navigate("/");
    } catch (e) {
      setError(errMsg(e));
      toast.error(errMsg(e));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen grid lg:grid-cols-2 bg-background relative">
      <AuroraBackground />
      <div className="hidden lg:flex relative z-10 flex-col items-center justify-center bg-[hsl(var(--sidebar))]/60 backdrop-blur-2xl border-r border-[hsl(var(--gold))]/20 overflow-hidden">
        <div className="absolute inset-0" style={{ background: "radial-gradient(ellipse at center, hsl(43 60% 52% / 0.08) 0%, transparent 60%)" }} />
        <img src="/nomos-logo.png" alt="Nomos One" className="w-64 h-64 relative z-10 drop-shadow-[0_0_40px_hsl(43_60%_52%/0.25)]" />
        <div className="relative z-10 text-center mt-8 px-12">
          <h2 className="font-brand text-3xl font-bold text-[hsl(var(--gold))] tracking-widest">NOMOS ONE</h2>
          <p className="text-[11px] tracking-[0.3em] text-muted-foreground mt-1">PERSONAL LOGISTICS · PERSACC</p>
          <div className="w-16 h-px bg-[hsl(var(--gold))]/40 mx-auto my-6" />
          <p className="text-sm text-foreground/70 max-w-sm mx-auto leading-relaxed">
            Ο ψηφιακός λογιστής της δικηγορικής σας εταιρείας — τιμολόγηση, ΦΠΑ, ΕΦΚΑ, μισθοδοσία και myDATA σε ένα σύστημα.
          </p>
        </div>
        <div className="absolute bottom-6 text-[10px] tracking-[0.2em] text-muted-foreground/50">ΕΜΠΙΣΤΕΥΤΙΚΟ · ΑΣΦΑΛΕΣ · ΑΥΤΟΜΑΤΟΠΟΙΗΜΕΝΟ</div>
      </div>

      <div className="flex items-center justify-center p-6 lg:p-12 relative z-10">
        <form onSubmit={handleSubmit} className="w-full max-w-sm" data-testid="login-form">
          <div className="flex items-center gap-3 mb-10">
            <img src="/nomos-logo.png" alt="Nomos One" className="w-12 h-12 rounded-full" />
            <div>
              <div className="font-brand font-bold text-xl text-[hsl(var(--gold))] tracking-widest leading-tight">NOMOS ONE</div>
              <div className="text-[9px] tracking-[0.22em] text-muted-foreground">PERSONAL LOGISTICS</div>
            </div>
          </div>
          <h1 className="font-heading text-2xl font-semibold tracking-tight mb-1">Σύνδεση</h1>
          <p className="text-sm text-muted-foreground mb-8">Συνδεθείτε στην πλατφόρμα του γραφείου σας</p>

          {error && (
            <div className="mb-4 text-sm bg-destructive/10 text-destructive border border-destructive/30 rounded-sm px-3 py-2" data-testid="login-error">
              {error}
            </div>
          )}

          <div className="mb-4">
            <Label htmlFor="email">Email</Label>
            <Input id="email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} required className="mt-1.5" data-testid="login-email-input" />
          </div>
          <div className="mb-8">
            <Label htmlFor="password">Κωδικός πρόσβασης</Label>
            <Input id="password" type="password" value={password} onChange={(e) => setPassword(e.target.value)} required className="mt-1.5" data-testid="login-password-input" />
          </div>
          <Button type="submit" className="w-full" disabled={loading} data-testid="login-submit-button">
            {loading ? "Σύνδεση..." : "Σύνδεση"}
          </Button>
          <p className="text-xs text-muted-foreground mt-6">
            Demo: admin@logistiki.gr / Admin@2026!
          </p>
        </form>
      </div>
    </div>
  );
}
