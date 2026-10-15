import { Button } from "@/components/ui/button";

export function SourceRefreshFailures({ sources }: {
  sources: { name: string; error: string | null; retry: () => Promise<void> }[];
}) {
  return sources.filter(source => source.error).map(source => (
    <div key={source.name} role="alert" className="text-sm text-warning">
      {source.name} refresh failed: {source.error}. Showing the previous response; its evidence timestamp has not changed.
      <Button variant="outline" size="sm" onClick={() => void source.retry()}>Retry {source.name}</Button>
    </div>
  ));
}
