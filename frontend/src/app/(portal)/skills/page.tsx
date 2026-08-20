"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { PageHeader } from "@/components/shared/page-header";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/shared/empty-state";
import { Skill } from "@/components/skills/skill-card";
import { SkillTable } from "@/components/skills/skill-table";
import { UploadSkillDialog } from "@/components/skills/upload-skill-dialog";
import { cn } from "@/lib/utils";
import { SkillSidebarFilters } from "@/components/skills/skill-sidebar-filters";
import { PendingContributionsSidebar } from "@/components/skills/pending-contributions-sidebar";
import { SkillContributeDialog as ContributeDialog } from "@/components/skills/skill-contribute-dialog";
import { SkillEditor } from "@/components/skills/skill-editor";
import { MySkillContributions as MyContributions } from "@/components/skills/my-skill-contributions";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import "./skills.css";

type SkillListResponse = {
  items: Skill[];
  total: number;
};

type Department = {
  id: string;
  name: string;
};

type PendingContribution = {
  id: string;
  title: string;
  contributor_name: string;
  status: string;
  created_at: string;
};

const LIMIT = 2000;

export default function SkillsPage() {
  const router = useRouter();
  const { canAccess, hasPermission } = useAuth();
  const [skills, setSkills] = useState<Skill[]>([]);
  const [total, setTotal] = useState(0);
  const [allDepartments, setAllDepartments] = useState<Department[]>([]);
  const [loading, setLoading] = useState(true);

  // Selection state

  // Filters state
  const [search, setSearch] = useState("");
  const [selectedDepartment, setSelectedDepartment] = useState<string | null>(null);
  const [activeContributionId, setActiveContributionId] = useState<string | null>(null);
  const [reviewContributionId, setReviewContributionId] = useState<string | null>(null);
  const [pendingContributions, setPendingContributions] = useState<PendingContribution[]>([]);
  const scrollContainerRef = useRef<HTMLDivElement>(null);

  const loadSkills = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams();
      if (search) params.set("q", search);
      if (selectedDepartment) params.set("department_id", selectedDepartment);
      params.set("limit", String(LIMIT));

      const data = await api<SkillListResponse>(`/api/skills?${params.toString()}`);
      setSkills(data.items);
      setTotal(data.total);
    } catch (err) {
      console.error("Failed to load skills:", err);
      setSkills([]);
    } finally {
      setLoading(false);
    }
  }, [search, selectedDepartment]);


  const loadAllDepartments = useCallback(async () => {
    if (!hasPermission("org:departments:read")) return;
    try {
      const data = await api<Department[]>("/api/departments");
      setAllDepartments(data);
    } catch {
      setAllDepartments([]);
    }
  }, [hasPermission]);

  // Initial load
  useEffect(() => {
    loadAllDepartments();
  }, [loadAllDepartments]);

  // Load skills when filters change (debounced search)
  useEffect(() => {
    const timer = setTimeout(() => {
      loadSkills();
    }, 200);

    return () => clearTimeout(timer);
  }, [search, selectedDepartment, loadSkills]);

  // Keyed on the ids being polled, not on a join of every skill's status. Two skills
  // swapping states inside one window (one finishes as another starts) left that joined
  // string identical, so the effect never re-ran and the interval kept its stale
  // `pollIds` closure — the newly-processing skill was polled *never* and sat on
  // "Processing…" until someone reloaded the page.
  const pollIds = skills
    .filter((s) => s.status === "processing" || s.status === "deleting")
    .map((s) => s.id)
    .sort()
    .join(",");

  useEffect(() => {
    if (!pollIds) return;
    const processingIds = pollIds.split(",");

    const interval = setInterval(() => {
      const params = new URLSearchParams();
      processingIds.forEach(id => params.append("ids", id));
      params.set("limit", String(LIMIT)); // Ensure all processing items are returned

      api<SkillListResponse>(`/api/skills?${params.toString()}`)
        .then(data => {
          // IDs returned by the API (still in the DB)
          const returnedIds = new Set(data.items.map(i => i.id));
          // IDs we are polling that are missing from the response → deleted from the DB
          const deletedIds = new Set(processingIds.filter(id => !returnedIds.has(id)));

          // Keep `total` in sync when a skill is removed from state.
          // This used to run *inside* the `setSkills` updater. Updaters have to be pure —
          // StrictMode invokes them twice — so every deletion was subtracted twice and the
          // header count drifted below the real number of skills.
          if (deletedIds.size > 0) {
            setTotal(prev => Math.max(0, prev - deletedIds.size));
          }

          setSkills(prev => {
            const updatedItems = deletedIds.size > 0
              ? prev.filter(s => !deletedIds.has(s.id))
              : [...prev];
            let hasChanges = deletedIds.size > 0;

            // Update skills whose status changed (processing → active, etc.)
            data.items.forEach(newItem => {
              const idx = updatedItems.findIndex(s => s.id === newItem.id);
              if (idx !== -1 && JSON.stringify(updatedItems[idx]) !== JSON.stringify(newItem)) {
                updatedItems[idx] = newItem;
                hasChanges = true;
              }
            });

            return hasChanges ? updatedItems : prev;
          });
        })
        // A background poll must not blank the table it is refreshing: keeping the last
        // known rows is right when one tick fails, and the next tick corrects them.
        .catch(err => console.error("Polling error:", err));
    }, 3000);

    return () => clearInterval(interval);
  }, [pollIds]);



  const handleDelete = async (id: string, name: string) => {
    if (!confirm(`Are you sure you want to delete Skill "${name}"?`)) return;
    try {
      await api(`/api/skills/${id}`, { method: "DELETE" });
      loadSkills();
    } catch (error) {
      alert("Delete failed: " + (error instanceof Error ? error.message : "Unknown error"));
    }
  };

  const handleSubmitContribution = async () => {
    if (!activeContributionId) return;
    if (!confirm("Are you sure you want to submit this contribution for review?")) return;

    try {
      await api(`/api/skill-contributions/${activeContributionId}/submit`, { method: "POST" });
      alert("Contribution submitted successfully!");
      setActiveContributionId(null);
      loadSkills();
    } catch (err) {
      console.error("Failed to submit contribution:", err);
      alert("Submit failed: " + (err instanceof Error ? err.message : "Unknown error"));
    }
  };

  const handleSearch = (q: string) => {
    setSearch(q);
  };





  const handleApprove = async (id: string) => {
    if (!confirm("Are you sure you want to APPROVE and MERGE this contribution?")) return;
    try {
      await api(`/api/skill-contributions/${id}/approve`, { method: "POST" });
      alert("Contribution approved and merged successfully!");
      setReviewContributionId(null);
      // Remove from local state immediately for instant feedback
      setPendingContributions(prev => prev.filter(c => c.id !== id));
      loadSkills();
    } catch (err) {
      // Do NOT remove the item here. The error path used to run the same optimistic
      // removal as success, so a 500 or 403 showed an alert and then made the
      // contribution vanish from the queue anyway — the reviewer dismissed the alert, saw
      // an empty queue, and the still-pending contribution was silently dropped from
      // review until someone reloaded.
      alert("Approval failed: " + (err instanceof Error ? err.message : "Unknown error"));
      setReviewContributionId(null);
    }
  };

  const handleReject = async (id: string) => {
    if (!confirm("Are you sure you want to REJECT this contribution?")) return;
    try {
      await api(`/api/skill-contributions/${id}/reject`, { method: "POST" });
      alert("Contribution rejected.");
      setReviewContributionId(null);
      // Remove from local state immediately for instant feedback
      setPendingContributions(prev => prev.filter(c => c.id !== id));
      loadSkills();
    } catch (err) {
      // See handleApprove: the failure path must not remove the item.
      alert("Rejection failed: " + (err instanceof Error ? err.message : "Unknown error"));
      setReviewContributionId(null);
    }
  };

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="AI Skill Library"
        description="Manage and deploy skill packages for your AI system."
        action={
          <div className="flex items-center gap-3">
            {canAccess("skill", "create") && (
              <ContributeDialog
                onContributionCreated={(id) => setActiveContributionId(id)}
                allDepartments={allDepartments}
                trigger={
                  <Button variant="outline" className="gap-2 border-primary/20 hover:bg-primary/5 text-primary">
                    <span className="material-symbols-outlined text-sm">edit_square</span>
                    Propose New Skill
                  </Button>
                }
              />
            )}
            {canAccess("skill", "create") && (
              <UploadSkillDialog
                allDepartments={allDepartments}
                onUploaded={() => loadSkills()}
              />
            )}
          </div>
        }
      />

      <div className="grid grid-cols-1 lg:grid-cols-4 gap-6">
        <div className="lg:col-span-1 flex flex-col gap-4">
          <SkillSidebarFilters
            departments={allDepartments}
            selectedDepartment={selectedDepartment}
            onSelectDepartment={setSelectedDepartment}
          />

          <PendingContributionsSidebar
            onReview={setReviewContributionId}
            contributions={pendingContributions}
            onDataUpdate={setPendingContributions}
            refreshInterval={15000}
          />
        </div>

        {/* Main Content Area */}
        <div
          ref={scrollContainerRef}
          className="lg:col-span-3 flex flex-col gap-2"
        >
          <MyContributions
            key={activeContributionId || "list"}
            onEdit={setActiveContributionId}
            onRefreshNeeded={loadSkills}
            departments={allDepartments}
            refreshInterval={15000}
          />

          <div className="bg-background/40 rounded-2xl border border-border/50 p-6">
            <div className="flex flex-col gap-4">
              {/*
                SkillTable owns the search box, so it must stay mounted while a query is in
                flight. Swapping it for a spinner (the old `loading && skills.length === 0`
                gate) unmounted the focused input the moment a search returned zero results,
                which killed the caret and dropped every keystroke typed during the request.
                SkillTable renders its own loading state inside the results area instead.
              */}
              <SkillTable
                skills={skills}
                departments={allDepartments}
                loading={loading}
                onDelete={handleDelete}
                onRefresh={loadSkills}
                onClick={(slug) => router.push(`/skills/${slug}`)}
                onSearch={handleSearch}
                total={total}
                search={search}
              />
            </div>
          </div>
        </div>
      </div>

      {activeContributionId && (
        <Dialog open onOpenChange={() => setActiveContributionId(null)}>
          <DialogContent showCloseButton={false} className="!max-w-[98vw] w-[1800px] h-[96vh] p-0 gap-0 overflow-hidden rounded-xl border border-border shadow-2xl flex flex-col fixed top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2">
            <DialogHeader className="p-4 border-b border-border shrink-0 bg-primary/5">
              <div className="flex items-center justify-between pr-8">
                <div className="flex items-center gap-3">
                  <div className="w-10 h-10 rounded-xl bg-primary/10 flex items-center justify-center">
                    <span className="material-symbols-outlined text-primary">edit_note</span>
                  </div>
                  <div>
                    <DialogTitle className="text-xl font-serif">Editing Contribution</DialogTitle>
                    <p className="text-xs text-muted-foreground font-manrope">Draft Mode</p>
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  <button
                    id="force-submit-button"
                    onClick={handleSubmitContribution}
                    className="h-8 px-4 flex items-center justify-center bg-primary text-primary-foreground rounded-lg font-bold text-xs uppercase tracking-wider hover:bg-primary/90 shadow-lg transition-all"
                  >
                    Contribute
                  </button>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={async () => {
                      if (!confirm("Are you sure you want to delete this draft contribution? This action cannot be undone.")) return;
                      try {
                        const { api } = await import("@/lib/api");
                        await api(`/api/skill-contributions/${activeContributionId}`, { method: "DELETE" });
                        setActiveContributionId(null);
                        loadSkills();
                      } catch (err) {
                        console.error("Failed to delete contribution:", err);
                        alert("Delete failed: " + (err instanceof Error ? err.message : "Unknown error"));
                      }
                    }}
                    className="h-8 w-8 text-muted-foreground hover:bg-destructive/10 hover:text-destructive transition-all"
                    title="Delete Draft"
                  >
                    <span className="material-symbols-outlined text-lg">delete</span>
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => setActiveContributionId(null)}
                    className="h-8 w-8 hover:bg-muted transition-all"
                    title="Close Editor"
                  >
                    <span className="material-symbols-outlined text-lg">close</span>
                  </Button>
                </div>
              </div>
            </DialogHeader>
            <div className="flex-1 overflow-hidden">
              <SkillEditor
                contributionId={activeContributionId}
                mode="edit"
              />
            </div>
          </DialogContent>
        </Dialog>
      )}

      {reviewContributionId && (
        <Dialog open onOpenChange={() => setReviewContributionId(null)}>
          <DialogContent showCloseButton={false} className="!max-w-[98vw] w-[1800px] h-[96vh] p-0 gap-0 overflow-hidden rounded-xl border border-border shadow-2xl flex flex-col fixed top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2">
            <DialogHeader className="p-4 border-b border-border shrink-0 bg-primary/5">
              <div className="flex items-center justify-between pr-8">
                <div className="flex items-center gap-3">
                  <div className="w-10 h-10 rounded-xl bg-primary/10 flex items-center justify-center">
                    <span className="material-symbols-outlined text-primary">rate_review</span>
                  </div>
                  <div>
                    <DialogTitle className="text-xl font-serif">Review Contribution</DialogTitle>
                    <p className="text-xs text-muted-foreground font-manrope">
                      Submitted by <span className="font-bold text-foreground">{pendingContributions.find(c => c.id === reviewContributionId)?.contributor_name}</span>
                    </p>
                  </div>
                </div>
                <div className="flex items-center gap-3">
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => handleReject(reviewContributionId)}
                    className="h-8 gap-2 text-muted-foreground hover:bg-destructive/10 hover:text-destructive transition-all"
                  >
                    <span className="material-symbols-outlined text-sm">close</span>
                    Reject
                  </Button>
                  <Button
                    variant="default"
                    size="sm"
                    onClick={() => handleApprove(reviewContributionId)}
                    className="h-8 gap-2 bg-primary text-primary-foreground hover:bg-primary/90 shadow-lg transition-all font-bold"
                  >
                    <span className="material-symbols-outlined text-sm">check_circle</span>
                    Approve & Merge
                  </Button>
                  <div className="w-px h-6 bg-border mx-1" />
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => setReviewContributionId(null)}
                    className="h-8 w-8 hover:bg-muted transition-all"
                    title="Close Reviewer"
                  >
                    <span className="material-symbols-outlined text-lg">close</span>
                  </Button>
                </div>
              </div>
            </DialogHeader>
            <div className="flex-1 overflow-hidden">
              <SkillEditor
                contributionId={reviewContributionId}
                mode="review"
                onStatusChange={() => {
                  setReviewContributionId(null);
                  loadSkills();
                }}
              />
            </div>
          </DialogContent>
        </Dialog>
      )}
    </div>
  );
}
