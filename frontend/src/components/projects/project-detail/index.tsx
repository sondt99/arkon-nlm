"use client";

import React, { useEffect, useState, useCallback, useMemo } from "react";
import { api } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { WikiPageSummary } from "@/types/wiki";

import { Project, Member, ProjectSource, Employee, Source } from "./types";
import { MembersTab } from "./members-tab";
import { SourcesTab } from "./sources-tab";
import { WikiTab } from "./wiki-tab";

type Props = {
  project: Project;
  isAdmin: boolean;
  onBack: () => void;
};

export function ProjectDetail({ project, isAdmin, onBack }: Props) {
  const [members, setMembers] = useState<Member[]>([]);
  const [sources, setSources] = useState<ProjectSource[]>([]);
  const [allEmployees, setAllEmployees] = useState<Employee[]>([]);
  const [allSources, setAllSources] = useState<Source[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<"wiki" | "sources" | "members">("wiki");
  const [wikiPages, setWikiPages] = useState<WikiPageSummary[]>([]);
  const [wikiLoading, setWikiLoading] = useState(false);
  const [wikiIndexMd, setWikiIndexMd] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [m, s, emp, src] = await Promise.all([
        api<Member[]>(`/api/projects/${project.id}/members`),
        api<ProjectSource[]>(`/api/projects/${project.id}/sources`),
        api<{items: Employee[]}>("/api/employees?page_size=500"),
        api<{items: Source[]}>("/api/sources?page_size=500"),
      ]);
      setMembers(m);
      setSources(s);
      setAllEmployees(emp.items || []);
      setAllSources(src.items || []);
    } catch (err) {
      console.error(err);
      setError("Failed to load project details");
    }
  }, [project.id]);

  // `silent` exists because the status poll calls this every three seconds. Without it the
  // Wiki tab replaced its page list with a spinner on every tick, so a workspace with a
  // document still processing was unreadable — the sibling knowledge page already had the flag.
  const loadWiki = useCallback(async (silent = false) => {
    if (!silent) setWikiLoading(true);
    try {
      const pages = await api<WikiPageSummary[]>(`/api/projects/${project.id}/wiki?limit=2000`);
      setWikiPages(pages);

      try {
        const idxData = await api<{ content_md: string }>(`/api/projects/${project.id}/wiki/index`);
        setWikiIndexMd(idxData.content_md);
      } catch {
        setWikiIndexMd(null);
      }
    } catch (err) {
      console.error(err);
      if (!silent) setError("Failed to load workspace wiki");
    } finally {
      if (!silent) setWikiLoading(false);
    }
  }, [project.id]);

  const loadProjectSources = useCallback(async () => {
    try {
      const s = await api<ProjectSource[]>(`/api/projects/${project.id}/sources`);
      setSources(s);
    } catch (err) {
      console.error(err);
    }
  }, [project.id]);

  useEffect(() => {
    load();
    loadWiki();
  }, [load, loadWiki]);

  // Poll for document processing status.
  //
  // `plan_ready` is excluded on purpose: it is terminal until a human approves the plan
  // (`notebooklm/page.tsx` classifies it the same way), so including it meant a workspace
  // sitting in plan review fired three requests every three seconds forever with nothing
  // on the server able to change the answer. Depends on the boolean rather than on
  // `sources`, which this effect's own callback replaces.
  const hasPendingSources = sources.some(
    s => s.status === "pending" || s.status === "processing"
  );

  useEffect(() => {
    if (!hasPendingSources) return;

    const interval = setInterval(() => {
      loadProjectSources();
      void loadWiki(true); // Also reload wiki pages so the count live-updates
    }, 3000);

    return () => clearInterval(interval);
  }, [hasPendingSources, loadProjectSources, loadWiki]);

  const memberIds = new Set(members.map((m) => m.employee_id));
  const sourceIds = new Set(sources.map((s) => s.source_id));
  const availableEmployees = allEmployees.filter((e) => !memberIds.has(e.id));
  const availableSources = allSources.filter((s) => !sourceIds.has(s.id));

  const tabConfig = [
    { key: "wiki" as const, label: "Wiki", count: wikiPages.length, icon: "auto_stories" },
    { key: "sources" as const, label: "Documents", count: sources.length, icon: "description" },
    { key: "members" as const, label: "Members", count: members.length, icon: "group" },
  ];

  return (
    <div className="flex-1 flex flex-col min-h-0">
      {/* 2-col header: left = back + title, right = tabs */}
      <div className="flex items-end gap-4 pb-4">
        {/* Left: back button + project identity */}
        <div className="flex items-center gap-3 pb-3 shrink-0">
          <Button variant="ghost" size="sm" onClick={onBack} className="h-8 px-2">
            <span className="material-symbols-outlined text-base">arrow_back</span>
            <span className="ml-1 text-sm">Back</span>
          </Button>
          <div className="w-px h-5 bg-border" />
          <span className="material-symbols-outlined text-primary text-lg">
            {project.workspace_type === "customer" ? "domain" : "folder_special"}
          </span>
          <div className="flex flex-col min-w-0">
            <div className="flex items-center gap-2">
              <h1 className="text-lg font-semibold font-serif leading-tight truncate max-w-[260px]">
                {project.name}
              </h1>
              <Badge
                variant="outline"
                className={project.status === "active" ? "text-green-600 border-green-300 text-xs" : "text-muted-foreground text-xs"}
              >
                {project.status}
              </Badge>
            </div>
            {project.description && (
              <p className="text-xs text-muted-foreground truncate max-w-[260px]">{project.description}</p>
            )}
          </div>
        </div>

        {/* Right: tabs flush to bottom of header row */}
        <div className="flex items-end gap-1 flex-1 justify-end">
          {tabConfig.map((t) => (
            <button
              key={t.key}
              onClick={() => setTab(t.key)}
              className={`flex items-center gap-1.5 px-4 py-2.5 text-sm font-medium border-b-2 transition-colors ${tab === t.key
                ? "border-primary text-primary"
                : "border-transparent text-muted-foreground hover:text-foreground"
                }`}
            >
              <span className="material-symbols-outlined text-base">{t.icon}</span>
              {t.label}
              <span className="ml-1 tabular-nums text-xs text-muted-foreground">{t.count}</span>
            </button>
          ))}
        </div>
      </div>

      {error && (
        <div className="mt-4 text-sm text-destructive bg-destructive/10 px-4 py-2 rounded-lg flex items-center gap-2">
          <span className="material-symbols-outlined text-base">error</span>
          {error}
        </div>
      )}

      {/* ================================================================ */}
      {/* Content Area                                                     */}
      {/* ================================================================ */}
      
      {tab === "members" && (
        <MembersTab
          project={project}
          members={members}
          isAdmin={isAdmin}
          availableEmployees={availableEmployees}
          onChanged={load}
          onError={setError}
        />
      )}

      {tab === "sources" && (
        <SourcesTab
          project={project}
          sources={sources}
          isAdmin={isAdmin}
          availableSources={availableSources}
          onChanged={load}
          onError={setError}
        />
      )}

      {tab === "wiki" && (
        <WikiTab
          project={project}
          wikiPages={wikiPages}
          wikiLoading={wikiLoading}
          wikiIndexMd={wikiIndexMd}
          onWikiChanged={() => void loadWiki()}
        />
      )}
    </div>
  );
}
