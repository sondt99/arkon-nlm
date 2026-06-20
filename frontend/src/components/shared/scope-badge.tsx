import { Badge } from "@/components/ui/badge";

export type ScopeType = "global" | "project" | "workspace" | "department" | "team";

type Props = {
  scopeType?: ScopeType | string;
  scopeId?: string;
  className?: string;
};

export function ScopeBadge({ scopeType, scopeId, className }: Props) {
  if (!scopeType) {
    // Legacy documents might not have a scope type, default to global or unknown
    return (
      <Badge variant="outline" className={`scope-badge scope-badge--neutral ${className || ""}`}>
        <span className="material-symbols-outlined mr-1 shrink-0" style={{ fontSize: 13, lineHeight: 1 }}>public</span>
        Global
      </Badge>
    );
  }

  switch (scopeType) {
    case "global":
      return (
        <Badge variant="outline" className={`scope-badge scope-badge--global ${className || ""}`}>
          <span className="material-symbols-outlined mr-1.5 shrink-0" style={{ fontSize: 14, lineHeight: 1 }}>public</span>
          Global
        </Badge>
      );
    case "department":
      return (
        <Badge variant="outline" className={`scope-badge scope-badge--department ${className || ""}`}>
          <span className="material-symbols-outlined mr-1.5 shrink-0" style={{ fontSize: 14, lineHeight: 1 }}>corporate_fare</span>
          Department
        </Badge>
      );
    case "project":
    case "workspace":
      return (
        <Badge variant="outline" className={`scope-badge scope-badge--workspace ${className || ""}`}>
          <span className="material-symbols-outlined mr-1.5 shrink-0" style={{ fontSize: 14, lineHeight: 1 }}>folder_special</span>
          Workspace
        </Badge>
      );
    case "team":
      return (
        <Badge variant="outline" className={`scope-badge scope-badge--team ${className || ""}`}>
          <span className="material-symbols-outlined mr-1.5 shrink-0" style={{ fontSize: 14, lineHeight: 1 }}>group</span>
          Team
        </Badge>
      );
    default:
      return (
        <Badge variant="outline" className={`scope-badge scope-badge--neutral ${className || ""}`}>
          {scopeType}
        </Badge>
      );
  }
}

