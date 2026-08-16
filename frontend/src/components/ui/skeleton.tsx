import { cn } from "@/lib/utils";

export function Skeleton({ className, style }: { className?: string; style?: React.CSSProperties }) {
  return (
    <div
      style={style}
      className={cn(
        "arkon-skeleton rounded-md bg-black/[0.06] dark:bg-white/[0.07]",
        "animate-[pulse_1.8s_cubic-bezier(.4,0,.6,1)_infinite]",
        className
      )}
    />
  );
}
