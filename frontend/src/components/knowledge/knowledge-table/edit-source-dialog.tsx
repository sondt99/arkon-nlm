import React from "react";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Source, KnowledgeType, Department } from "./types";

export function EditSourceDialog({
  source,
  types,
  departments,
  onClose,
  onSaved,
}: {
  source: Source;
  types: KnowledgeType[];
  departments: Department[];
  onClose: () => void;
  onSaved: (updated: Partial<Source>) => void;
}) {
  const [title, setTitle] = React.useState(source.title);
  const [typeId, setTypeId] = React.useState(source.knowledge_type_id || "");
  const [selectedDepts, setSelectedDepts] = React.useState<string[]>(source.department_ids || []);

  React.useEffect(() => {
    setSelectedDepts(source.department_ids || []);
  }, [source.id]);
  const [scopeType, setScopeType] = React.useState(source.scope_type || "global");
  const [scopeId, setScopeId] = React.useState(source.scope_id || "");
  const [projects, setProjects] = React.useState<{ id: string; name: string }[]>([]);
  const [saving, setSaving] = React.useState(false);
  const [error, setError] = React.useState("");

  // Fetch projects for workspace scope picker
  React.useEffect(() => {
    api<{ id: string; name: string }[]>("/api/projects")
      .then((data) => setProjects(Array.isArray(data) ? data : []))
      .catch(() => setProjects([]));
  }, []);

  const toggleDept = (deptId: string) => {
    setSelectedDepts((prev) =>
      prev.includes(deptId) ? prev.filter((d) => d !== deptId) : [...prev, deptId]
    );
  };

  const handleSave = async () => {
    setSaving(true);
    setError("");
    try {
      await api(`/api/sources/${source.id}`, {
        method: "PATCH",
        body: {
          title: title || undefined,
          knowledge_type_id: typeId || null,
          department_ids: selectedDepts,
          scope_type: scopeType,
          scope_id: scopeType === "global" ? null : (scopeId || null),
        },
      });
      const matchedType = types.find((t) => t.id === typeId);
      onSaved({
        title: title || source.title,
        knowledge_type_id: typeId || undefined,
        knowledge_type_name: matchedType?.name,
        knowledge_type_color: matchedType?.color,
        department_ids: selectedDepts,
        scope_type: scopeType,
        scope_id: scopeType === "global" ? undefined : (scopeId || undefined),
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save");
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open onOpenChange={onClose}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle className="text-xl">Edit Document</DialogTitle>
        </DialogHeader>

        <div className="flex flex-col gap-4 mt-2">
          <div className="flex flex-col gap-1.5">
            <Label>Title</Label>
            <Input
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              className="bg-background"
            />
          </div>

          <div className="flex flex-col gap-1.5">
            <Label>Knowledge Type</Label>
            <Select value={typeId} onValueChange={(v) => setTypeId(v ?? "")}>
              <SelectTrigger className="bg-background">
                {typeId ? (() => { const t = types.find(x => x.id === typeId); return t ? (
                  <div className="flex items-center gap-2">
                    <span className="w-2.5 h-2.5 rounded-full" style={{ backgroundColor: t.color }} />
                    <span>{t.name}</span>
                  </div>
                ) : <SelectValue placeholder="No type" />; })() : <SelectValue placeholder="No type" />}
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="">No type</SelectItem>
                {types.map((t) => (
                  <SelectItem key={t.id} value={t.id}>
                    <div className="flex items-center gap-2">
                      <span className="w-2.5 h-2.5 rounded-full" style={{ backgroundColor: t.color }} />
                      {t.name}
                    </div>
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          {/* Multi-department selection */}
          <div className="flex flex-col gap-1.5">
            <Label>Departments</Label>
            <p className="text-xs text-muted-foreground">
              Select which departments can access this document. Leave empty for global access.
            </p>
            <div className="border rounded-lg p-2 max-h-40 overflow-y-auto bg-background">
              {departments.length === 0 ? (
                <span className="text-xs text-muted-foreground">No departments available</span>
              ) : (
                departments.map((d) => (
                  <label
                    key={d.id}
                    className="flex items-center gap-2 px-2 py-1.5 rounded hover:bg-muted cursor-pointer"
                  >
                    <input
                      type="checkbox"
                      checked={selectedDepts.includes(d.id)}
                      onChange={() => toggleDept(d.id)}
                      className="rounded border-border"
                    />
                    <span className="text-sm">{d.name}</span>
                  </label>
                ))
              )}
            </div>
            {selectedDepts.length > 0 && (
              <div className="flex flex-wrap gap-1 mt-1">
                {selectedDepts.map((id) => {
                  const name = departments.find((d) => d.id === id)?.name ?? id;
                  return (
                    <span
                      key={id}
                      className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-primary/10 text-primary"
                    >
                      {name}
                      <button
                        type="button"
                        onClick={() => toggleDept(id)}
                        className="hover:text-destructive"
                      >
                        ×
                      </button>
                    </span>
                  );
                })}
              </div>
            )}
          </div>

          {/* Visibility / Scope */}
          <div className="flex flex-col gap-1.5">
            <Label>Visibility</Label>
            <Select value={scopeType} onValueChange={(v) => {
              const val = v ?? "global";
              setScopeType(val);
              if (val === "global") setScopeId("");
            }}>
              <SelectTrigger className="bg-background">
                <div className="flex items-center gap-2">
                  <span className="material-symbols-outlined" style={{ fontSize: 14 }}>
                    {scopeType === "global" ? "public" : "folder_special"}
                  </span>
                  <span className="capitalize">{scopeType === "project" ? "Workspace" : scopeType}</span>
                </div>
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="global">
                  <div className="flex items-center gap-2">
                    <span className="material-symbols-outlined" style={{ fontSize: 14 }}>public</span>
                    Global
                  </div>
                </SelectItem>
                <SelectItem value="project">
                  <div className="flex items-center gap-2">
                    <span className="material-symbols-outlined" style={{ fontSize: 14 }}>folder_special</span>
                    Workspace
                  </div>
                </SelectItem>
              </SelectContent>
            </Select>
            {scopeType === "global" && (
              <p className="text-xs text-amber-600 dark:text-amber-400 flex items-start gap-1.5 mt-0.5">
                <span className="material-symbols-outlined shrink-0" style={{ fontSize: 13, marginTop: 1 }}>warning</span>
                Document content will be compiled into the shared wiki and visible to all employees — including those without access to the original file. Only use Global if the content is not sensitive.
              </p>
            )}
          </div>

          {scopeType === "project" && (
            <div className="flex flex-col gap-1.5">
              <Label>Target Workspace</Label>
              <Select value={scopeId} onValueChange={(v) => setScopeId(v ?? "")}>
                <SelectTrigger className="bg-background">
                  <span>{scopeId ? (projects.find(p => p.id === scopeId)?.name ?? "Select...") : "Select workspace..."}</span>
                </SelectTrigger>
                <SelectContent>
                  {projects.map((p) => (
                    <SelectItem key={p.id} value={p.id}>
                      {p.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          )}

          {error && (
            <p className="text-destructive text-sm bg-destructive/10 px-3 py-2 rounded-lg">
              {error}
            </p>
          )}

          <div className="flex justify-end gap-2 mt-2">
            <Button variant="outline" onClick={onClose}>Cancel</Button>
            <Button
              disabled={saving}
              onClick={handleSave}
              className="bg-primary text-primary-foreground hover:bg-primary/90"
            >
              {saving ? (
               <span className="flex items-center gap-2">
                 <span className="material-symbols-outlined animate-spin text-sm">progress_activity</span>
                 Saving...
               </span>
              ) : "Save"}
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
