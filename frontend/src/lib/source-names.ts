/**
 * Readable, unique names for a list of sources, in order.
 * Empty titles become "<fallback> N" (1-based position); a repeated name gets
 * " (2)", " (3)"… so link labels such as "Source: <name>" never collide.
 * Comparison ignores case and surrounding whitespace, as a listener would.
 */
export function distinctSourceNames(
  titles: ReadonlyArray<string | null | undefined>,
  fallback = "Source",
): string[] {
  const used = new Set<string>();
  return titles.map((title, index) => {
    const base = title?.trim() || `${fallback} ${index + 1}`;
    let name = base;
    for (let n = 2; used.has(name.toLowerCase()); n += 1) name = `${base} (${n})`;
    used.add(name.toLowerCase());
    return name;
  });
}
