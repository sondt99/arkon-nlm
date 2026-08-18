"use client";

import React from "react";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { SaharaCard } from "@/components/ui/sahara-card";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { EmptyState } from "@/components/shared/empty-state";
import { LoadingState } from "@/components/shared/loading-state";


type Department = {
  id: string;
  name: string;
  description?: string;
  employee_count: number;
};

type Props = {
  departments: Department[];
  loading: boolean;
  onEdit: (dept: Department) => void;
  onRefresh: () => void;
};

export function DepartmentCards({ departments, loading, onEdit, onRefresh }: Props) {
  const [deleteDept, setDeleteDept] = React.useState<Department | null>(null);

  const handleDelete = async () => {
    if (!deleteDept) return;
    await api(`/api/departments/${deleteDept.id}`, { method: "DELETE" });
    onRefresh();
  };

  if (loading) {
    return <LoadingState />;
  }

  if (departments.length === 0) {
    return (
      <EmptyState
        icon="business"
        title="No departments"
        description="Create your first department to organize employees"
      />
    );
  }

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
      {departments.map((dept) => (
        <SaharaCard key={dept.id} padded hoverable>
          <div className="flex items-start justify-between gap-2">
            <div className="flex min-w-0 items-center gap-3">
              <div className="w-10 h-10 shrink-0 rounded-lg bg-primary/10 flex items-center justify-center">
                <span className="material-symbols-outlined text-primary">
                  business
                </span>
              </div>
              <div className="min-w-0">
                <h3 className="truncate text-base font-semibold text-foreground">
                  {dept.name}
                </h3>
                <p className="text-xs text-muted-foreground">
                  {dept.employee_count} employee{dept.employee_count !== 1 ? "s" : ""}
                </p>
              </div>
            </div>
          </div>

          {dept.description && (
            <p className="text-sm text-muted-foreground line-clamp-2">
              {dept.description}
            </p>
          )}

          <div className="flex flex-wrap gap-2 mt-auto pt-2 border-t border-border">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => onEdit(dept)}
              className="text-xs"
            >
              <span className="material-symbols-outlined text-sm mr-1">edit</span>
              Edit
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setDeleteDept(dept)}
              className="text-xs text-destructive hover:text-destructive"
            >
              <span className="material-symbols-outlined text-sm mr-1">delete</span>
              Delete
            </Button>
          </div>
        </SaharaCard>
      ))}

      <ConfirmDialog
        open={!!deleteDept}
        onOpenChange={(open) => { if (!open) setDeleteDept(null); }}
        title="Delete department?"
        description={
          deleteDept
            ? `This deletes "${deleteDept.name}" and all ${deleteDept.employee_count} of its employee${deleteDept.employee_count !== 1 ? "s" : ""}. This cannot be undone.`
            : undefined
        }
        confirmLabel="Delete"
        onConfirm={handleDelete}
      />
    </div>
  );
}
