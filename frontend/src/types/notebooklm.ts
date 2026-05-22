export type NLMNotebook = {
  id: string;
  notebook_id: string;
  title: string;
  source_id: string | null;
  source_title: string | null;
  status: "active" | "error";
  error_message: string | null;
  created_at: string;
  updated_at: string;
  artifact_count: number;
};

export type NLMArtifact = {
  id: string;
  notebook_ref_id: string;
  artifact_id: string | null;
  task_id: string | null;
  artifact_type: ArtifactType;
  report_format: ReportFormat | null;
  title: string | null;
  status: ArtifactStatus;
  error_message: string | null;
  download_url: string | null;
  minio_key: string | null;
  ingest_source_id: string | null;
  can_add_to_wiki: boolean;
  created_at: string;
  updated_at: string;
};

export type ArtifactType =
  | "audio"
  | "video"
  | "report"
  | "quiz"
  | "flashcards"
  | "slide_deck"
  | "infographic"
  | "data_table";

export type ReportFormat = "briefing_doc" | "study_guide" | "blog_post" | "custom";

export type ArtifactStatus = "pending" | "processing" | "completed" | "failed";

export const ARTIFACT_LABELS: Record<ArtifactType, string> = {
  audio: "Podcast (Audio)",
  video: "Video Overview",
  report: "Report",
  quiz: "Quiz",
  flashcards: "Flashcards",
  slide_deck: "Slide Deck",
  infographic: "Infographic",
  data_table: "Data Table",
};

export const ARTIFACT_ICONS: Record<ArtifactType, string> = {
  audio: "podcasts",
  video: "smart_display",
  report: "article",
  quiz: "quiz",
  flashcards: "style",
  slide_deck: "slideshow",
  infographic: "image",
  data_table: "table_chart",
};

export const REPORT_FORMAT_LABELS: Record<ReportFormat, string> = {
  briefing_doc: "Briefing Document",
  study_guide: "Study Guide",
  blog_post: "Blog Post",
  custom: "Custom",
};

// ── NLM API passthrough types (live from NotebookLM, not Arkon DB) ──────────

export type NLMNotebookNative = {
  id: string;
  title: string;
  sources_count: number;
  created_at: string | null;
  is_owner: boolean;
};

export type NLMSourceNative = {
  id: string;
  title: string | null;
  url: string | null;
  kind: string;
  status: number; // 1=processing, 2=ready, 3=error
  created_at: string | null;
};

export type NLMArtifactNative = {
  id: string;
  title: string;
  kind: ArtifactType;
  status: number; // 1=in_progress, 2=pending, 3=completed, 4=failed
  status_str: string;
  created_at: string | null;
  url: string | null;
  report_subtype: string | null;
  can_add_to_wiki: boolean;
  is_binary: boolean;
};

export type ChatReference = {
  source_id: string;
  citation_number: number | null;
  cited_text: string | null;
};

export type ChatAskResult = {
  answer: string;
  conversation_id: string;
  turn_number: number;
  is_follow_up: boolean;
  references: ChatReference[];
};

export type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  text: string;
  references?: ChatReference[];
};
