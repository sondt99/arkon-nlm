import { WikiPageType } from "@/types/wiki";

const TYPE_CONFIG: Record<
  WikiPageType,
  { icon: string; label: string; color: string; tone: string }
> = {
  entity: { icon: "person", label: "Entity", color: "#db2777", tone: "wiki-type--entity" },
  concept: { icon: "lightbulb", label: "Concept", color: "#d97706", tone: "wiki-type--concept" },
  topic: { icon: "topic", label: "Topic", color: "#0891b2", tone: "wiki-type--topic" },
  source: { icon: "description", label: "Source", color: "#7c3aed", tone: "wiki-type--source" },
  synthesis: { icon: "chat_bubble", label: "Synthesis", color: "#2563eb", tone: "wiki-type--synthesis" },
  index: { icon: "list_alt", label: "Index", color: "#64748b", tone: "wiki-type--neutral" },
  log: { icon: "history", label: "Log", color: "#64748b", tone: "wiki-type--neutral" },
};

export function WikiTypeBadge({ type }: { type: string }) {
  const cfg = TYPE_CONFIG[type as WikiPageType] ?? TYPE_CONFIG.concept;
  return (
    <span
      className={`wiki-type-badge ${cfg.tone}`}
    >
      <span className="material-symbols-outlined" style={{ fontSize: 12 }}>
        {cfg.icon}
      </span>
      {cfg.label}
    </span>
  );
}

export function wikiTypeIcon(type: string): string {
  return TYPE_CONFIG[type as WikiPageType]?.icon ?? "article";
}

export function wikiTypeColor(type: string): string {
  return TYPE_CONFIG[type as WikiPageType]?.color ?? "#78706a";
}

export function wikiTypeGroupLabel(type: string): string {
  const labels: Record<string, string> = {
    entity: "Entities",
    concept: "Concepts",
    topic: "Topics",
    source: "Sources",
    synthesis: "Syntheses",
    index: "Index",
    log: "Log",
  };
  return labels[type] ?? type;
}
