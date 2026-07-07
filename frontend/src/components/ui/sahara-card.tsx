import * as React from "react"

import { cn } from "@/lib/utils"

type SaharaCardProps = React.ComponentProps<"div"> & {
  /** Adds internal padding + vertical stack spacing (the "content card" shape). */
  padded?: boolean
  /** Highlights the border on hover (used for clickable/interactive cards). */
  hoverable?: boolean
  /** Clips overflow — the common shape for cards with a header/body/footer. */
  overflowHidden?: boolean
}

/** The "Sahara" bordered/shadowed card shape used throughout the app, previously
 * copy-pasted as raw class strings in ~15 files. Consolidates both variants
 * (plain overflow-hidden panels, and padded hoverable content cards) into one
 * component so card styling can't drift out of sync again. */
function SaharaCard({
  className,
  padded,
  hoverable,
  overflowHidden,
  ...props
}: SaharaCardProps) {
  return (
    <div
      data-slot="sahara-card"
      className={cn(
        "rounded-xl border border-border bg-card shadow-sahara",
        overflowHidden && "overflow-hidden",
        padded && "flex flex-col gap-4 p-6",
        hoverable && "transition-colors hover:border-primary/30",
        className
      )}
      {...props}
    />
  )
}

export { SaharaCard }
