"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth";
import { Sidebar } from "@/components/layout/sidebar";
import { MobileHeader } from "@/components/layout/mobile-header";
import { Skeleton } from "@/components/ui/skeleton";

/* Mimics the actual sidebar + content layout so there's no layout shift on load */
function AppShellSkeleton() {
  return (
    <div className="h-screen flex bg-background overflow-hidden">
      {/* Sidebar skeleton */}
      <div className="hidden md:flex flex-col h-full w-[252px] shrink-0 bg-sidebar border-r border-sidebar-border">
        {/* Org header */}
        <div className="pt-2 px-3 py-2 mb-1">
          <div className="flex items-center gap-2.5 px-1.5 py-1.5">
            <Skeleton className="w-6 h-6 rounded-[4px] shrink-0" />
            <div className="flex flex-col gap-1.5 flex-1">
              <Skeleton className="h-3 w-14" />
              <Skeleton className="h-2 w-24" />
            </div>
          </div>
        </div>
        <div className="mx-3 border-t border-sidebar-border my-1" />
        {/* Nav items */}
        <div className="flex-1 px-3 py-2 space-y-1">
          <Skeleton className="h-7 w-full rounded-md" />
          <div className="mt-4 space-y-1">
            <Skeleton className="h-2.5 w-20 mb-2" />
            {[48, 36, 44, 40].map((w, i) => (
              <div key={i} className="flex items-center gap-2 px-2 py-[5px]">
                <Skeleton className="w-4 h-4 rounded shrink-0" />
                <Skeleton className={`h-3 w-[${w}px]`} style={{ width: w }} />
              </div>
            ))}
          </div>
          <div className="mt-4 space-y-1">
            <Skeleton className="h-2.5 w-24 mb-2" />
            {[52, 40, 36].map((w, i) => (
              <div key={i} className="flex items-center gap-2 px-2 py-[5px]">
                <Skeleton className="w-4 h-4 rounded shrink-0" />
                <Skeleton style={{ height: 12, width: w }} />
              </div>
            ))}
          </div>
        </div>
        <div className="px-3 py-2 border-t border-sidebar-border">
          <Skeleton className="h-2 w-28" />
        </div>
      </div>

      {/* Main content skeleton */}
      <main className="flex-1 flex flex-col h-screen overflow-hidden min-w-0">
        <div className="p-6 md:p-8 lg:p-10 pt-4 w-full flex-1 min-h-0 flex flex-col gap-6 overflow-y-auto">
          {/* Page header */}
          <div className="flex items-start justify-between">
            <div className="space-y-2">
              <Skeleton className="h-6 w-36" />
              <Skeleton className="h-4 w-64" />
            </div>
            <Skeleton className="h-9 w-28 rounded-lg" />
          </div>
          {/* Table card */}
          <div className="rounded-xl border border-border overflow-hidden">
            <div className="flex gap-6 px-4 py-3 border-b border-border bg-muted/20">
              {[80, 64, 56, 72, 48].map((w, i) => (
                <Skeleton key={i} style={{ height: 10, width: w }} />
              ))}
            </div>
            {Array.from({ length: 7 }).map((_, i) => (
              <div key={i} className="flex items-center gap-6 px-4 py-3 border-b border-border/50 last:border-0">
                <div className="flex items-center gap-2.5 flex-1">
                  <Skeleton className="w-4 h-4 rounded shrink-0" />
                  <Skeleton style={{ height: 13, width: 120 + (i % 3) * 40 }} />
                </div>
                <Skeleton style={{ height: 13, width: 60 }} />
                <Skeleton style={{ height: 13, width: 48 }} />
                <Skeleton style={{ height: 13, width: 72 }} />
                <Skeleton style={{ height: 13, width: 32 }} />
              </div>
            ))}
          </div>
        </div>
      </main>
    </div>
  );
}

export default function PortalLayout({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (!loading && !user) {
      router.push("/login");
    }
  }, [user, loading, router]);

  if (loading) return <AppShellSkeleton />;
  if (!user) return null;

  return (
    <div className="app-shell h-screen overflow-hidden bg-background">
      <MobileHeader />
      <div className="flex min-h-0 flex-1 overflow-hidden">
        <Sidebar />
        <main className="flex min-w-0 flex-1 flex-col overflow-hidden">
          <div className="app-content flex min-h-0 w-full flex-1 flex-col gap-6 overflow-y-auto p-4 sm:p-6 md:gap-8 md:p-8 lg:p-10 lg:pt-6">
            {children}
          </div>
        </main>
      </div>
    </div>
  );
}
