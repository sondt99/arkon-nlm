"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { SaharaCard } from "@/components/ui/sahara-card";
import { EmptyState } from "@/components/shared/empty-state";
import { LoadingState } from "@/components/shared/loading-state";

type Source = {
  id: string;
  title: string;
  status: string;
  knowledge_type_name?: string;
  knowledge_type_color?: string;
  created_at: string;
};

export function RecentSourcesCard() {
  const [sources, setSources] = useState<Source[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    async function load() {
      try {
        const data = await api<Source[]>("/api/sources?limit=5");
        setSources(data || []);
      } catch {
        setSources([]);
      } finally {
        setLoading(false);
      }
    }
    load();
  }, []);

  return (
    <SaharaCard className="p-6">
      <h3 className="text-xl tracking-tight text-foreground border-b border-border pb-3 mb-4">
        Recent Documents
      </h3>

      {loading ? (
        <LoadingState className="py-8" />
      ) : sources.length === 0 ? (
        <EmptyState
          icon="description"
          title="No documents yet"
          description="Upload your first document in the Knowledge Base"
        />
      ) : (
        <div className="flex flex-col gap-3">
          {sources.map((source) => (
            <div
              key={source.id}
              className="flex items-center justify-between py-2 px-3 rounded-lg hover:bg-secondary/50 transition-colors"
            >
              <div className="flex items-center gap-3 min-w-0">
                <span className="material-symbols-outlined text-muted-foreground text-base">
                  description
                </span>
                <div className="min-w-0">
                  <p className="text-sm font-medium text-foreground truncate">
                    {source.title}
                  </p>
                  <p className="text-xs text-muted-foreground">
                    {new Date(source.created_at).toLocaleDateString()}
                  </p>
                </div>
              </div>
              <div className="flex items-center gap-2 shrink-0">
                {source.knowledge_type_name && (
                  <Badge
                    variant="outline"
                    style={{
                      borderColor: `color-mix(in srgb, ${source.knowledge_type_color} 45%, var(--border))`,
                      color: `color-mix(in srgb, ${source.knowledge_type_color} 65%, var(--foreground))`,
                      backgroundColor: `color-mix(in srgb, ${source.knowledge_type_color} 12%, var(--card))`,
                    }}
                    className="text-xs"
                  >
                    {source.knowledge_type_name}
                  </Badge>
                )}
                <StatusBadge status={source.status} />
              </div>
            </div>
          ))}
        </div>
      )}
    </SaharaCard>
  );
}

function StatusBadge({ status }: { status: string }) {
  const variants: Record<string, string> = {
    ready: "tone-badge tone-teal",
    processing: "tone-badge tone-amber",
    error: "tone-badge tone-danger",
    pending: "tone-badge tone-neutral",
  };

  return (
    <span
      className={`${
        variants[status] || variants.pending
      }`}
    >
      {status}
    </span>
  );
}
