import * as React from "react"

import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"

type PaginationProps = {
  page: number
  totalPages: number
  onPageChange: (page: number) => void
  className?: string
}

/** Previous/Next + a sliding window of up to 7 page numbers. Renders nothing
 * when there's only one page — auto-hides per the "few items" requirement. */
function Pagination({ page, totalPages, onPageChange, className }: PaginationProps) {
  if (totalPages <= 1) return null

  const windowSize = Math.min(totalPages, 7)
  const pages = Array.from({ length: windowSize }, (_, i) => {
    if (totalPages <= 7) return i + 1
    if (page <= 4) return i + 1
    if (page >= totalPages - 3) return totalPages - 6 + i
    return page - 3 + i
  })

  return (
    <div
      data-slot="pagination"
      className={cn("flex items-center justify-center gap-1", className)}
    >
      <Button
        variant="outline"
        size="sm"
        disabled={page <= 1}
        onClick={() => onPageChange(page - 1)}
        className="h-8 px-2.5"
        aria-label="Previous page"
      >
        <span className="material-symbols-outlined text-sm">chevron_left</span>
      </Button>
      {pages.map((p) => (
        <Button
          key={p}
          variant={p === page ? "default" : "outline"}
          size="sm"
          onClick={() => onPageChange(p)}
          className="h-8 w-8 p-0 text-xs"
          aria-current={p === page ? "page" : undefined}
        >
          {p}
        </Button>
      ))}
      <Button
        variant="outline"
        size="sm"
        disabled={page >= totalPages}
        onClick={() => onPageChange(page + 1)}
        className="h-8 px-2.5"
        aria-label="Next page"
      >
        <span className="material-symbols-outlined text-sm">chevron_right</span>
      </Button>
    </div>
  )
}

export { Pagination }
