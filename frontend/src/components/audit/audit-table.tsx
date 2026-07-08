"use client";

import React from "react";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { SaharaCard } from "@/components/ui/sahara-card";
import { EmptyState } from "@/components/shared/empty-state";
import { LoadingState } from "@/components/shared/loading-state";

export type AuditLogEntry = {
  id: string;
  timestamp: string;
  action: string;
  principal_id: string;
  principal_name?: string;
  principal_email?: string;
  resource_type: string;
  resource_id?: string;
  decision: string;
  reason?: string;
};

type Props = {
  logs: AuditLogEntry[];
  loading: boolean;
};

export function AuditTable({ logs, loading }: Props) {
  if (loading) {
    return (
      <SaharaCard>
        <LoadingState className="py-16" />
      </SaharaCard>
    );
  }

  if (logs.length === 0) {
    return (
      <SaharaCard>
        <EmptyState
          icon="policy"
          title="No audit logs found"
          description="Access control events will appear here."
        />
      </SaharaCard>
    );
  }

  return (
    <SaharaCard overflowHidden>
      <Table>
        <TableHeader>
          <TableRow className="hover:bg-transparent">
            <TableHead className="text-xs uppercase tracking-wider">Timestamp</TableHead>
            <TableHead className="text-xs uppercase tracking-wider">Principal</TableHead>
            <TableHead className="text-xs uppercase tracking-wider">Action</TableHead>
            <TableHead className="text-xs uppercase tracking-wider">Resource</TableHead>
            <TableHead className="text-xs uppercase tracking-wider">Decision</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {logs.map((log) => (
            <TableRow key={log.id} className="hover:bg-secondary/30">
              <TableCell className="text-xs text-muted-foreground whitespace-nowrap">
                {new Date(log.timestamp).toLocaleString()}
              </TableCell>
              <TableCell>
                <div className="flex flex-col">
                  <span className="text-sm font-medium">{log.principal_name || "System/Unknown"}</span>
                  <span className="text-xs text-muted-foreground">{log.principal_email || log.principal_id.slice(0, 8) + '...'}</span>
                </div>
              </TableCell>
              <TableCell>
                <Badge
                  variant="outline"
                  className={`tone-badge font-mono font-normal ${
                    log.action.toLowerCase() === "create"
                      ? "tone-teal"
                      : log.action.toLowerCase() === "delete"
                      ? "tone-danger"
                      : "tone-blue"
                  }`}
                >
                  {log.action}
                </Badge>
              </TableCell>
              <TableCell>
                <div className="flex flex-col">
                  <span className="text-sm capitalize">{log.resource_type}</span>
                  {log.resource_id && (
                    <span className="text-xs text-muted-foreground font-mono">
                      {log.resource_id.length > 20 ? log.resource_id.slice(0, 8) + '...' : log.resource_id}
                    </span>
                  )}
                </div>
              </TableCell>
              <TableCell>
                <div className="flex flex-col items-start gap-1">
                  <Badge
                    variant="outline"
                    className={`tone-badge ${
                      log.decision.toLowerCase() === "allow"
                        ? "tone-teal"
                        : "tone-danger"
                    }`}
                  >
                    {log.decision.toUpperCase()}
                  </Badge>
                  {log.reason && (
                    <span className="text-xs text-muted-foreground max-w-[200px] truncate" title={log.reason}>
                      {log.reason}
                    </span>
                  )}
                </div>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </SaharaCard>
  );
}
