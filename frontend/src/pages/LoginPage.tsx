import { useState, type FormEvent } from "react";
import { Eye, EyeOff, Loader2, Lock, LogIn, ShieldCheck, User } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { ApiError } from "../api/client";
import { useAuth } from "../hooks/useAuth";

export default function LoginPage() {
  const navigate = useNavigate();
  const { login, logoutPending } = useAuth();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setLoading(true);
    setError("");
    try {
      await login(username, password);
      navigate("/dashboard", { replace: true });
    } catch (caught: unknown) {
      setError(caught instanceof ApiError ? caught.message : "The Chronos server could not be reached.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="bg-background text-on-surface font-body-md antialiased min-h-screen flex">
      <section className="hidden lg:flex w-[45%] bg-on-secondary-fixed relative flex-col justify-between p-12 overflow-hidden">
        <div className="absolute inset-0 grid-bg-dark pointer-events-none" />
        <div className="relative z-10 mt-8">
          <h1 className="font-headline-xl text-white text-[56px] tracking-tight leading-none mb-2">Chronos</h1>
          <div className="flex items-center gap-3">
            <span className="w-10 h-[2px] bg-primary" />
            <p className="font-label-md text-primary-fixed uppercase tracking-widest text-[13px]">Local Scheduling Operations</p>
          </div>
        </div>
        <div className="relative z-10 max-w-md border border-white/10 bg-black/20 rounded-xl p-7">
          <ShieldCheck className="w-9 h-9 text-primary-fixed mb-5" />
          <h2 className="text-white text-2xl font-headline-md">Private by design.</h2>
          <p className="text-white/60 mt-3 leading-relaxed text-sm">
            Authentication and operational data stay within the locally hosted Chronos environment.
          </p>
        </div>
        <p className="relative z-10 text-white/60 text-[11px] uppercase tracking-widest">Authorized department personnel only</p>
      </section>

      <main className="w-full lg:w-[55%] bg-surface flex flex-col justify-center items-center p-6 sm:p-8">
        <div className="w-full max-w-[420px] bg-surface-container-lowest border border-outline-variant p-8 sm:p-10 rounded-2xl shadow-xl">
          <div className="mb-8">
            <p className="font-label-md text-primary uppercase tracking-widest text-[11px]">Local session</p>
            <h2 className="font-headline-lg text-on-surface tracking-tight text-[30px] mt-2">Sign in to Chronos</h2>
            <p className="font-body-md text-on-surface-variant mt-2 text-[14px]">Use your provisioned scheduling account.</p>
          </div>

          {logoutPending && (
            <p role="alert" className="mb-5 rounded-lg border border-error/30 bg-error-container/30 px-4 py-3 text-sm text-on-error-container">
              {logoutPending === "retry"
                ? "Sign-out could not be confirmed with the server. The session will be revoked when the server is reachable again; reload this page to retry."
                : "Sign-out could not be confirmed with the server, and this browser cannot save a retry. Do not reload: once the server is reachable, sign in and sign out again."}
            </p>
          )}
          <form onSubmit={handleSubmit} className="space-y-5">
            <label className="block space-y-1.5">
              <span className="font-label-md text-on-surface-variant uppercase tracking-wider text-[11px]">Username</span>
              <span className="relative block">
                <User className="absolute left-3.5 top-1/2 -translate-y-1/2 text-outline w-5 h-5" />
                <input value={username} onChange={(event) => setUsername(event.target.value)} autoComplete="username" placeholder="Enter your username" className="w-full bg-surface-container-lowest border border-outline-variant rounded-lg pl-11 pr-4 py-3 text-[16px] md:text-[14px] focus:border-primary focus:ring-1 focus:ring-primary outline-none" required />
              </span>
            </label>
            <label className="block space-y-1.5">
              <span className="font-label-md text-on-surface-variant uppercase tracking-wider text-[11px]">Password</span>
              <span className="relative block">
                <Lock className="absolute left-3.5 top-1/2 -translate-y-1/2 text-outline w-5 h-5" />
                <input type={showPassword ? "text" : "password"} value={password} onChange={(event) => setPassword(event.target.value)} autoComplete="current-password" placeholder="Enter your password" className="w-full bg-surface-container-lowest border border-outline-variant rounded-lg pl-11 pr-12 py-3 text-[16px] md:text-[14px] focus:border-primary focus:ring-1 focus:ring-primary outline-none" required />
                <button type="button" onClick={() => setShowPassword((shown) => !shown)} aria-label={showPassword ? "Hide password" : "Show password"} aria-pressed={showPassword} className="absolute right-3.5 top-1/2 -translate-y-1/2 text-outline hover:text-on-surface">
                  {showPassword ? <EyeOff className="w-5 h-5" /> : <Eye className="w-5 h-5" />}
                </button>
              </span>
            </label>
            {error && <p role="alert" className="rounded-lg border border-error/30 bg-error-container/30 px-4 py-3 text-sm text-on-error-container">{error}</p>}
            <button type="submit" disabled={loading} className="w-full flex items-center justify-center gap-2 text-white font-bold py-3.5 rounded-lg bg-surface-tint hover:bg-on-primary-fixed-variant disabled:opacity-70 transition-colors">
              {loading ? <Loader2 className="w-5 h-5 animate-spin" /> : <LogIn className="w-5 h-5" />}
              {loading ? "Signing in..." : "Sign in"}
            </button>
          </form>
        </div>
      </main>
    </div>
  );
}
