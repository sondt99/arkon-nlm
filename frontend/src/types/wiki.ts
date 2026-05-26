export type WikiPageType = "entity" | "concept" | "topic" | "source" | "synthesis" | "index" | "log";

export type WikiPageSummary = {
  slug: string;
  title: string;
  page_type: WikiPageType;
  summary: string;
  knowledge_type_slugs: string[];
  source_ids: string[];
  scope_type?: string;
  scope_id?: string;
  version: number;
  updated_at: string;
};

export type WikiPageDetail = WikiPageSummary & {
  content_md: string;
  backlinks: string[];
  outlinks: string[];
  orphaned?: boolean;
};

export type WikiGraphNode = {
  slug: string;
  title: string;
  page_type: string;
  scope_type?: string;
  scope_id?: string | null;
  scope_name?: string | null;
  // injected by d3-force simulation
  x?: number;
  y?: number;
  vx?: number;
  vy?: number;
  fx?: number | null;
  fy?: number | null;
};

export type WikiGraphEdge = {
  from: string;
  to: string;
  // d3-force replaces string refs with node objects after simulation init
  source?: WikiGraphNode | string;
  target?: WikiGraphNode | string;
};

export type DraftResponse = {
  id: string;
  page_id: string;
  page_slug: string;
  page_title: string;
  author_id: string | null;
  author_name: string | null;
  content_md: string;
  note: string | null;
  status: string;
  source: string;
  reviewed_by_name: string | null;
  reviewed_at: string | null;
  reviewer_note: string | null;
  created_at: string;
  updated_at: string;
};

export type WikiGraphData = {
  nodes: WikiGraphNode[];
  edges: WikiGraphEdge[];
  total?: number;
  offset?: number;
  has_more?: boolean;
};
