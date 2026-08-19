"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import { api } from "@/lib/api";
import { PageHeader } from "@/components/shared/page-header";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { KnowledgeTable } from "@/components/knowledge/knowledge-table";
import { KnowledgeFilters } from "@/components/knowledge/knowledge-filters";
import { UploadDialog } from "@/components/knowledge/upload-dialog";
import { KnowledgeTypeCards } from "@/components/types/knowledge-type-cards";
import { KnowledgeTypeDialog } from "@/components/types/knowledge-type-dialog";

export type KnowledgeType = {
  id: string;
  slug: string;
  name: string;
  color: string;
  description?: string;
  sort_order: number;
  source_count?: number;
};

export type Department = {
  id: string;
  name: string;
};

export type Source = {
  id: string;
  title: string;
  file_name?: string;
  source_type?: string;
  status: string;
  progress?: number;
  progress_message?: string;
  page_count?: number;
  wiki_page_count?: number;
  knowledge_type_id?: string;
  knowledge_type_name?: string;
  knowledge_type_color?: string;
  department_ids?: string[];
  department_names?: string[];
  contributed_by_name?: string;
  scope_type?: string;
  scope_id?: string;
  created_at: string;
  updated_at?: string;
};

const PAGE_SIZE = 20;

type PaginatedSources = {
  items: Source[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
};

export default function KnowledgePage() {
  const [activeTab, setActiveTab] = useState("documents");
  
  const [sources, setSources] = useState<Source[]>([]);
  const [types, setTypes] = useState<KnowledgeType[]>([]);
  const [departments, setDepartments] = useState<Department[]>([]);
  const [selectedType, setSelectedType] = useState<string | null>(null);
  const [selectedDepartment, setSelectedDepartment] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [page, setPage] = useState(1);
  const [totalPages, setTotalPages] = useState(1);
  const [total, setTotal] = useState(0);
  const [search, setSearch] = useState("");
  const [loadFailed, setLoadFailed] = useState(false);
  /** Only the newest load may write state. Polls, searches and filter clicks all run through
   *  `loadSources`, so ordering between them is not guaranteed by anything else. */
  const loadSeqRef = useRef(0);
  
  const [typeDialogOpen, setTypeDialogOpen] = useState(false);
  const [editType, setEditType] = useState<KnowledgeType | null>(null);

  // Resolved here rather than inside `loadSources`, which used to depend on the whole `types`
  // array. `types` arrives from a separate request, so its identity change re-fired the
  // initial-load effect and every page view fetched the source list twice.
  const selectedTypeId = selectedType
    ? types.find((t) => t.slug === selectedType)?.id ?? null
    : null;

  const loadSources = useCallback(async (silent = false, p = 1, s?: string) => {
    const seq = ++loadSeqRef.current;
    if (!silent) {
      setLoading(true);
      setLoadFailed(false);
    }
    try {
      const params = new URLSearchParams({
        page: String(p),
        page_size: String(PAGE_SIZE),
      });
      if (selectedTypeId) params.set("knowledge_type_id", selectedTypeId);
      if (selectedDepartment) params.set("department_id", selectedDepartment);
      const searchQuery = s !== undefined ? s : search;
      if (searchQuery) params.set("search", searchQuery);

      const data = await api<PaginatedSources>(`/api/sources?${params}`);
      // A poll that left before the user typed can land after the search it raced. Applying
      // it put the full unfiltered list in the table while the search box still showed the
      // query — the same overwrite happens between two filter clicks.
      if (seq !== loadSeqRef.current) return;
      setSources(data.items);
      setTotal(data.total);
      setTotalPages(data.total_pages);
      setPage(data.page);
    } catch {
      if (seq !== loadSeqRef.current) return;
      // A silent poll keeps the rows it already has — one failed tick is not evidence the
      // library is empty, and the next tick corrects it. A user-initiated load has to say so.
      if (!silent) {
        setSources([]);
        setLoadFailed(true);
      }
    } finally {
      if (!silent && seq === loadSeqRef.current) setLoading(false);
    }
  }, [selectedTypeId, selectedDepartment, search]);

  // Polling cho trạng thái tài liệu.
  //
  // Depends on a boolean, not on `sources`: the array is what this effect's own callback
  // replaces, so depending on it tore the interval down and recreated it on every single
  // response (an eslint-disable hid the cycle). `plan_ready` is deliberately excluded —
  // it is terminal until a human approves the plan, exactly as `notebooklm/page.tsx`
  // classifies it, so polling it just burns a request every three seconds forever.
  const hasPendingSources = sources.some(
    (s) => s.status === "pending" || s.status === "processing"
  );

  useEffect(() => {
    if (!hasPendingSources) return;

    const interval = setInterval(() => {
      loadSources(true, page, search);
    }, 3000);

    return () => clearInterval(interval);
  }, [hasPendingSources, page, search, loadSources]);

  const loadMeta = useCallback(async () => {
    try {
      const [typesData, deptsData] = await Promise.all([
        api<KnowledgeType[]>("/api/knowledge-types"),
        api<Department[]>("/api/departments"),
      ]);
      setTypes(typesData);
      setDepartments(deptsData);
    } catch {
      setTypes([]);
      setDepartments([]);
    }
  }, []);

  useEffect(() => {
    loadMeta();
  }, [loadMeta]);

  useEffect(() => {
    loadSources();
  }, [loadSources]);

  const handleSearch = (q: string) => {
    setSearch(q);
    setPage(1);
    loadSources(false, 1, q);
  };

  const handlePageChange = (p: number) => {
    setPage(p);
    loadSources(false, p, search);
  };

  return (
    <>
      <PageHeader
        title="Knowledge Base"
        description="Manage and organize your organization's documents and categories."
        action={
          activeTab === "documents" ? (
            <Button
              onClick={() => setUploadOpen(true)}
              className="bg-primary text-primary-foreground hover:bg-primary/90"
            >
              <span className="material-symbols-outlined text-base mr-1">add</span>
              Upload Document
            </Button>
          ) : (
            <Button
              onClick={() => { setEditType(null); setTypeDialogOpen(true); }}
              className="bg-primary text-primary-foreground hover:bg-primary/90"
            >
              <span className="material-symbols-outlined text-base mr-1">add</span>
              Add Category
            </Button>
          )
        }
      />

      <Tabs value={activeTab} onValueChange={setActiveTab} className="w-full">
        <TabsList className="mb-6">
          <TabsTrigger value="documents" className="gap-2">
            <span className="material-symbols-outlined text-[18px]">files</span>
            Documents
          </TabsTrigger>
          <TabsTrigger value="types" className="gap-2">
            <span className="material-symbols-outlined text-[18px]">category</span>
            Categories
          </TabsTrigger>
        </TabsList>

        <TabsContent value="documents" className="mt-0 outline-none">
          <div className="grid grid-cols-1 lg:grid-cols-4 gap-6">
            <div className="lg:col-span-1">
              <KnowledgeFilters
                types={types}
                selectedType={selectedType}
                onSelectType={setSelectedType}
                departments={departments}
                selectedDepartment={selectedDepartment}
                onSelectDepartment={setSelectedDepartment}
              />
            </div>
            <div className="lg:col-span-3">
              {loadFailed && (
                <div className="mb-4 flex items-center gap-2 rounded-lg bg-destructive/10 px-4 py-2 text-sm text-destructive">
                  <span className="material-symbols-outlined text-base">error</span>
                  Couldn&apos;t load documents.
                  <button
                    onClick={() => loadSources(false, page, search)}
                    className="ml-auto underline underline-offset-2 hover:no-underline"
                  >
                    Retry
                  </button>
                </div>
              )}
              <KnowledgeTable
                sources={sources}
                types={types}
                departments={departments}
                loading={loading}
                onRefresh={() => loadSources(false, page, search)}
                onDeleteSource={(id) => {
                  setSources((prev) => prev.filter((s) => s.id !== id));
                  setTotal((t) => Math.max(0, t - 1));
                }}
                onUpdateSource={(updated) =>
                  setSources((prev) =>
                    prev.map((s) => (s.id === updated.id ? { ...s, ...updated } : s))
                  )
                }
                page={page}
                totalPages={totalPages}
                total={total}
                onPageChange={handlePageChange}
                search={search}
                onSearch={handleSearch}
              />
            </div>
          </div>
        </TabsContent>

        <TabsContent value="types" className="mt-0 outline-none">
          <KnowledgeTypeCards
            types={types}
            loading={types.length === 0 && loading}
            onEdit={(t) => { setEditType(t); setTypeDialogOpen(true); }}
            onRefresh={loadMeta}
          />
        </TabsContent>
      </Tabs>

      <UploadDialog
        open={uploadOpen}
        onOpenChange={setUploadOpen}
        types={types}
        departments={departments}
        onUploaded={() => loadSources(false, page, search)}
      />

      <KnowledgeTypeDialog
        open={typeDialogOpen}
        onOpenChange={setTypeDialogOpen}
        knowledgeType={editType}
        onSaved={loadMeta}
      />
    </>
  );
}
