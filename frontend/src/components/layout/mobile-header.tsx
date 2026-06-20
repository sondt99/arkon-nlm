"use client";

import Image from "next/image";
import { Menu } from "lucide-react";
import { Sidebar } from "@/components/layout/sidebar";
import { ThemeToggle } from "@/components/layout/theme-toggle";
import { Sheet, SheetContent, SheetTitle, SheetTrigger } from "@/components/ui/sheet";

export function MobileHeader() {
  return (
    <header className="flex h-14 shrink-0 items-center justify-between border-b border-border bg-background/80 px-4 backdrop-blur-xl md:hidden">
      <div className="flex items-center gap-2.5">
        <Image src="/arkon-icon-v2.png" alt="Arkon" width={28} height={28} className="rounded-md" />
        <div>
          <p className="font-heading text-lg font-semibold leading-none text-foreground">Arkon <span className="font-mono text-[9px] text-primary">v2</span></p>
          <p className="font-mono text-[9px] uppercase tracking-[0.2em] text-primary">control center</p>
        </div>
      </div>
      <div className="flex items-center gap-2">
        <ThemeToggle className="size-9 justify-center px-0 [&>span:last-child]:hidden" />
        <Sheet>
          <SheetTrigger className="inline-flex size-9 items-center justify-center rounded-lg border border-border bg-background/60 text-foreground">
            <Menu className="size-4" />
            <span className="sr-only">Open navigation</span>
          </SheetTrigger>
          <SheetContent side="left" className="w-[286px] gap-0 border-border p-0" showCloseButton={false}>
            <SheetTitle className="sr-only">Navigation</SheetTitle>
            <Sidebar className="flex w-full" />
          </SheetContent>
        </Sheet>
      </div>
    </header>
  );
}
