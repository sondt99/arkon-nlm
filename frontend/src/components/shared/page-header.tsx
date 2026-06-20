type PageHeaderProps = {
  title: string;
  description?: string;
  action?: React.ReactNode;
};

export function PageHeader({ title, description, action }: PageHeaderProps) {
  return (
    <div className="flex flex-col gap-1 sm:flex-row sm:items-end sm:justify-between">
      <div>
        <div className="mb-2 flex items-center gap-2 font-mono text-[9px] font-semibold uppercase tracking-[0.2em] text-primary/80">
          <span className="h-px w-5 bg-primary/60" />
          Arkon workspace
        </div>
        <h1 className="text-2xl font-semibold tracking-[-0.025em] text-foreground sm:text-3xl lg:text-4xl">
          {title}
        </h1>
        {description && (
          <p className="text-muted-foreground text-sm mt-1 max-w-xl">
            {description}
          </p>
        )}
      </div>
      {action && <div className="mt-4 shrink-0 sm:mt-0">{action}</div>}
    </div>
  );
}
