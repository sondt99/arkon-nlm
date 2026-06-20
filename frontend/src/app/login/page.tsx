"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import Image from "next/image";
import { LockKeyhole, ShieldCheck } from "lucide-react";
import { useAuth } from "@/lib/auth";
import { ThemeToggle } from "@/components/layout/theme-toggle";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

export default function LoginPage() {
  const { login } = useAuth();
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      await login(email, password);
      router.push("/");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Login failed");
    } finally {
      setLoading(false);
    }
  };

  return (
    <main className="relative min-h-screen overflow-hidden bg-background px-4 py-6 sm:px-6 lg:px-10">
      <div className="absolute right-5 top-5 z-20">
        <ThemeToggle />
      </div>

      <div className="mx-auto grid min-h-[calc(100vh-3rem)] w-full max-w-6xl overflow-hidden rounded-2xl border border-border bg-card/80 shadow-[0_30px_90px_rgba(0,0,0,0.16)] backdrop-blur-xl lg:grid-cols-[1.08fr_0.92fr]">
        <section className="relative hidden overflow-hidden border-r border-border bg-[#06110d] p-10 text-white lg:flex lg:flex-col lg:justify-between">
          <div className="absolute inset-0 opacity-40 [background-image:linear-gradient(rgba(33,230,138,.09)_1px,transparent_1px),linear-gradient(90deg,rgba(33,230,138,.09)_1px,transparent_1px)] [background-size:32px_32px]" />
          <div className="absolute -left-28 top-20 size-80 rounded-full bg-emerald-400/10 blur-3xl" />
          <div className="relative">
            <div className="mb-20 flex items-center gap-3 font-mono text-xs uppercase tracking-[0.22em] text-emerald-300">
              <span className="size-2 animate-pulse rounded-full bg-emerald-400 shadow-[0_0_14px_#21e68a]" />
              Arkon secure node
            </div>
            <div className="mb-5 inline-flex overflow-hidden rounded-xl border border-emerald-300/20 bg-emerald-300/10 p-1.5 text-emerald-300">
              <Image src="/arkon-icon-v2.png" alt="Arkon" width={42} height={42} className="rounded-lg" priority />
            </div>
            <h1 className="max-w-lg text-5xl font-semibold leading-[1.04] tracking-[-0.04em] text-white">
              Intelligence stays<br /><span className="text-emerald-300">under your control.</span>
            </h1>
            <p className="mt-6 max-w-md text-sm leading-7 text-emerald-50/55">
              Your on-premise command center for governed AI knowledge, workspaces and operational intelligence.
            </p>
          </div>

          <div className="relative grid grid-cols-3 gap-3 font-mono text-[10px] uppercase tracking-wider text-emerald-50/45">
            {["Encrypted", "On-premise", "Auditable"].map((item) => (
              <div key={item} className="border-t border-emerald-300/20 pt-3">{item}</div>
            ))}
          </div>
        </section>

        <section className="flex items-center justify-center p-6 sm:p-10 lg:p-14">
          <div className="w-full max-w-sm">
            <div className="mb-9">
              <div className="mb-4 flex size-12 items-center justify-center overflow-hidden rounded-xl border border-primary/20 bg-primary/10 p-1 lg:hidden">
                <Image src="/arkon-icon-v2.png" alt="Arkon" width={40} height={40} className="rounded-lg" priority />
              </div>
              <p className="mb-2 font-mono text-[10px] uppercase tracking-[0.22em] text-primary">Identity verification</p>
              <h2 className="text-3xl font-semibold tracking-tight text-foreground">Welcome back</h2>
              <p className="mt-2 text-sm text-muted-foreground">Sign in to access the Arkon control center.</p>
            </div>

            <form onSubmit={handleSubmit} className="flex flex-col gap-5">
              <div className="flex flex-col gap-2">
                <Label htmlFor="email" className="text-sm font-medium">Email</Label>
                <Input
                  id="email"
                  type="email"
                  placeholder="admin@arkon.local"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  required
                  autoFocus
                  className="h-11 bg-background/70 px-3"
                />
              </div>

              <div className="flex flex-col gap-2">
                <Label htmlFor="password" className="text-sm font-medium">Password</Label>
                <Input
                  id="password"
                  type="password"
                  placeholder="Enter password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  required
                  className="h-11 bg-background/70 px-3"
                />
              </div>

              {error && (
                <p className="rounded-lg border border-destructive/20 bg-destructive/10 px-3 py-2 text-sm text-destructive">
                  {error}
                </p>
              )}

              <Button
                type="submit"
                disabled={loading}
                className="mt-2 h-11 w-full bg-primary text-primary-foreground shadow-[0_0_24px_color-mix(in_srgb,var(--primary)_16%,transparent)] hover:bg-primary/90"
              >
                {loading ? (
                  <span className="inline-flex items-center gap-2" role="status">
                    <span aria-hidden="true" className="size-4 shrink-0 animate-spin rounded-full border-2 border-current/30 border-t-current" />
                    <span>Signing in...</span>
                  </span>
                ) : "Sign in"}
              </Button>
            </form>

            <div className="mt-7 flex items-center justify-center gap-2 text-xs text-muted-foreground">
              <ShieldCheck className="size-3.5 text-primary" />
              Protected by organization access policies
            </div>
            <div className="mt-8 flex items-center justify-between border-t border-border pt-4 font-mono text-[9px] uppercase tracking-[0.14em] text-muted-foreground/60">
              <span>Arkon v2</span>
              <span className="flex items-center gap-1.5"><LockKeyhole className="size-3" /> Secure session</span>
            </div>
          </div>
        </section>
      </div>
    </main>
  );
}
