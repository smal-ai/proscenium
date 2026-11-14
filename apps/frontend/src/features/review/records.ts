export async function allPages<T>(load: (cursor?: string) => Promise<{items?: T[]; next_cursor?: string | null}>): Promise<T[]> {
  const items: T[] = []; let cursor: string | undefined;
  const seen = new Set<string>();
  do {
    const page = await load(cursor); items.push(...(page.items ?? []));
    cursor = page.next_cursor ?? undefined;
    if (cursor && seen.has(cursor)) throw new Error("Repeated page cursor");
    if (cursor) seen.add(cursor);
  } while (cursor);
  return items;
}
