import { WikiPageType } from "@/types/wiki";

const TYPE_CONFIG: Record<
  WikiPageType,
  { icon: string; label: string; color: string; bg: string }
> = {
  entity: { icon: "person", label: "Entity", color: "#c2652a", bg: "rgba(194,101,42,0.1)" },
  concept: { icon: "lightbulb", label: "Concept", color: "#8c7b6b", bg: "rgba(140,123,107,0.1)" },
  topic: { icon: "topic", label: "Topic", color: "#6b8c7b", bg: "rgba(107,140,123,0.1)" },
  source: { icon: "description", label: "Source", color: "#7b6b8c", bg: "rgba(123,107,140,0.1)" },
  synthesis: { icon: "chat_bubble", label: "Synthesis", color: "#2a7ec2", bg: "rgba(42,126,194,0.1)" },
  index: { icon: "list_alt", label: "Index", color: "#78706a", bg: "rgba(120,112,106,0.1)" },
  log: { icon: "history", label: "Log", color: "#78706a", bg: "rgba(120,112,106,0.1)" },
};

export function WikiTypeBadge({ type }: { type: string }) {
  const cfg = TYPE_CONFIG[type as WikiPageType] ?? TYPE_CONFIG.concept;
  return (
    <span
      className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium"
      style={{ color: cfg.color, backgroundColor: cfg.bg, border: `1px solid ${cfg.color}40` }}
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
