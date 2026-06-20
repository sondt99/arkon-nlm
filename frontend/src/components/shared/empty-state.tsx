"use client";

type EmptyStateProps = {
  icon: string;
  title: string;
  description?: string;
  action?: React.ReactNode;
};

export function EmptyState({ icon, title, description, action }: EmptyStateProps) {
  return (
    <div className="flex flex-col items-center justify-center px-4 py-16 text-center">
      <span className="material-symbols-outlined mb-4 rounded-2xl border border-primary/15 bg-primary/[0.06] p-4 text-4xl text-primary/70">
        {icon}
      </span>
      <h3 className="text-lg font-medium text-foreground mb-1">{title}</h3>
      {description && (
        <p className="text-sm text-muted-foreground text-center max-w-sm">
          {description}
        </p>
      )}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}
