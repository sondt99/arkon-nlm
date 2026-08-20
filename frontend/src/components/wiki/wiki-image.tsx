"use client";

import React from "react";
import {
  Dialog,
  DialogContent,
  DialogTrigger,
} from "@/components/ui/dialog";

type Status = "ok" | "loading" | "denied" | "missing" | "error";

export function WikiImage({
  src,
  alt,
  status,
}: {
  src?: string;
  alt?: string;
  status: Status;
}) {
  if (status === "loading") {
    return (
      <span className="block my-4 rounded-lg border border-border bg-surface-variant/40 px-4 py-8 text-center text-xs text-muted-foreground">
        Loading image…
      </span>
    );
  }

  if (status === "denied") {
    return (
      <span className="block my-4 rounded-lg border border-dashed border-border bg-surface-variant/40 px-4 py-8 text-center text-xs text-muted-foreground">
        <span className="material-symbols-outlined align-middle mr-1" style={{ fontSize: 16 }}>
          lock
        </span>
        Image access is restricted
        {alt ? <span className="block mt-1 italic">{alt}</span> : null}
      </span>
    );
  }

  if (status === "error") {
    return (
      <span className="block my-4 rounded-lg border border-dashed border-destructive/40 bg-destructive/5 px-4 py-8 text-center text-xs text-destructive">
        <span className="material-symbols-outlined align-middle mr-1" style={{ fontSize: 16 }}>
          cloud_off
        </span>
        Could not load image — try reloading the page
        {alt ? <span className="block mt-1 italic">{alt}</span> : null}
      </span>
    );
  }

  if (status === "missing" || !src) {
    return (
      <span className="block my-4 rounded-lg border border-dashed border-border bg-surface-variant/40 px-4 py-8 text-center text-xs text-muted-foreground">
        <span className="material-symbols-outlined align-middle mr-1" style={{ fontSize: 16 }}>
          broken_image
        </span>
        Image not found
        {alt ? <span className="block mt-1 italic">{alt}</span> : null}
      </span>
    );
  }

  return (
    <Dialog>
      <DialogTrigger
        render={
          <button
            type="button"
            className="block my-4 group/wiki-img w-full text-left"
            aria-label={alt || "Wiki image"}
          />
        }
      >
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src={src}
          alt={alt || ""}
          loading="lazy"
          className="rounded-lg border border-border max-w-full max-h-[480px] object-contain mx-auto bg-surface-variant/30 transition-transform group-hover/wiki-img:scale-[1.01]"
        />
        {alt ? (
          <span className="block mt-1.5 text-xs text-muted-foreground italic text-center">
            {alt}
          </span>
        ) : null}
      </DialogTrigger>
      <DialogContent
        className="max-w-[min(95vw,1400px)] sm:max-w-[min(95vw,1400px)] p-2 bg-background"
        showCloseButton
      >
        <div className="flex flex-col items-center gap-3">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={src}
            alt={alt || ""}
            className="max-h-[80vh] max-w-full object-contain rounded-md"
          />
          <div className="flex w-full items-center justify-between gap-3 px-2 pb-1 text-xs">
            <span className="text-muted-foreground italic flex-1 truncate">
              {alt || ""}
            </span>
            <a
              href={src}
              target="_blank"
              rel="noopener noreferrer"
              download
              className="text-primary underline underline-offset-2 hover:text-primary/80 shrink-0"
            >
              Download
            </a>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
