import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "@/context/AuthContext";
import { errMsg } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Scales } from "@phosphor-icons/react";
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
    <div className="min-h-screen grid lg:grid-cols-2">
      <div
        className="hidden lg:block relative bg-cover bg-center"
        style={{ backgroundImage: "url('https://images.pexels.com/photos/6077665/pexels-photo-6077665.jpeg?auto=compress&cs=tinysrgb&dpr=2&h=650&w=940')" }}
      >
        <div className="absolute inset-0 bg-black/50 flex flex-col justify-end p-12">
          <h2 className="text-white font-heading text-4xl font-bold tracking-tight leading-tight max-w-md">
            Ο ψηφιακός λογιστής της δικηγορικής σας εταιρείας
          </h2>
          <p className="text-white/70 text-sm mt-4 max-w-sm">
            Τιμολόγηση, ΦΠΑ, ΕΦΚΑ, μισθοδοσία και myDATA — όλα σε ένα σύστημα, προσαρμοσμένο στην ελληνική φορολογία.
          </p>
        </div>
      </div>

      <div className="flex items-center justify-center p-6 lg:p-12">
        <form onSubmit={handleSubmit} className="w-full max-w-sm" data-testid="login-form">
          <div className="flex items-center gap-2 mb-8">
            <Scales size={28} weight="fill" />
            <span className="font-heading font-bold text-xl tracking-tight">Logistiki.gr</span>
          </div>
          <h1 className="font-heading text-2xl font-semibold tracking-tight mb-1">Σύνδεση</h1>
          <p className="text-sm text-muted-foreground mb-6">Συνδεθείτε στον λογαριασμό της εταιρείας σας</p>

          {error && (
            <div className="mb-4 text-sm bg-destructive/10 text-destructive border border-destructive/30 rounded-sm px-3 py-2" data-testid="login-error">
              {error}
            </div>
          )}

          <div className="mb-4">
            <Label htmlFor="email">Email</Label>
            <Input id="email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} required className="mt-1.5" data-testid="login-email-input" />
          </div>
          <div className="mb-6">
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
