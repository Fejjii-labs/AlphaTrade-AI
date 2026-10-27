/**
 * Readable, unique names for a list of sources, in order.
 * Real titles are kept exactly as written (trimmed). Empty titles become
 * "<fallback> N" (1-based position); a repeated name gets the first free
 * " (2)", " (3)"… suffix. Generated names never reuse any real title in the
 * list, so link labels such as "Source: <name>" never collide.
 * Comparison ignores case and surrounding whitespace, as a listener would.
 */
export function distinctSourceNames(
  titles: ReadonlyArray<string | null | undefined>,
  fallback = "Source",
): string[] {
  const key = (name: string) => name.toLowerCase();
  const real = titles.map((title) => title?.trim() || null);
  const reserved = new Set(real.filter((t): t is string => t !== null).map(key));
  const used = new Set<string>();
  return real.map((title, index) => {
    if (title !== null && !used.has(key(title))) {
      used.add(key(title));
      return title;
    }
    const base = title ?? `${fallback} ${index + 1}`;
    let name = base;
    for (let n = 2; used.has(key(name)) || (name !== title && reserved.has(key(name))); n += 1) {
      name = `${base} (${n})`;
    }
    used.add(key(name));
    return name;
  });
}
