"use client";

import { cn } from "@/lib/utils";

type LoadingStateProps = {
  label?: string;
  className?: string;
};

/** Centralizes the spinner markup that was previously hand-rolled inline
 * across ~39 files, all using the same `.animate-spin` material-symbol
 * (see globals.css for why it's not a plain CSS spin — avoids the ligature
 * text flashing before the icon font loads). */
export function LoadingState({ label, className }: LoadingStateProps) {
  return (
    <div className={cn("flex flex-col items-center justify-center gap-2 py-16", className)}>
      <span className="material-symbols-outlined text-3xl text-muted-foreground animate-spin">
        progress_activity
      </span>
      {label && <p className="text-sm text-muted-foreground">{label}</p>}
    </div>
  );
}
