export function LogModelName({ model, missingLabel = "—" }: { model?: string | null; missingLabel?: string }) {
  if (!model?.trim()) return <span className="text-muted-foreground">{missingLabel}</span>;

  return (
    <strong className="inline-block min-w-0 max-w-full rounded-md border border-primary/15 bg-primary/5 px-2 py-0.5 text-left text-sm leading-5 font-semibold whitespace-normal text-foreground [overflow-wrap:anywhere]">
      {model}
    </strong>
  );
}
