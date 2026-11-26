export type OpenDisclosure = {kind: "item"; itemId: string} | {kind: "version"; itemId: string; versionId: string} | null;

export function disclosureKey(disclosure: Exclude<OpenDisclosure, null>): string {
  return JSON.stringify(disclosure.kind === "item"
    ? [disclosure.kind, disclosure.itemId]
    : [disclosure.kind, disclosure.itemId, disclosure.versionId]);
}

export function shortVersionLabel(versionId: string): string {
  const suffix = /[-_]?v(\d+)$/i.exec(versionId);
  return suffix ? `v${suffix[1]}` : versionId;
}

export function displayItemTitle(itemName: string, projectName?: string | null, allItemNames: string[] = []): string {
  if (!itemName) return "Untitled item";
  const projectPrefix = projectName?.trim();
  if (projectPrefix) {
    for (const separator of [" — ", " - "]) {
      const prefix = projectPrefix + separator;
      if (itemName.slice(0, prefix.length).toLowerCase() === prefix.toLowerCase()) return itemName.slice(prefix.length).trim() || itemName;
    }
  }

  const end = itemName.indexOf(" — ");
  if (end > 0) {
    const prefix = itemName.slice(0, end + 3).toLowerCase();
    if (allItemNames.filter(name => name.toLowerCase().startsWith(prefix)).length >= 2) {
      return itemName.slice(end + 3).trim() || itemName;
    }
  }
  return itemName;
}

export function formatItemStatusLine({kind, viewedVersionId, latestVersionId, ownerVersionId}: {
  kind: string; viewedVersionId: string | null; latestVersionId: string | null; ownerVersionId: string | null;
}): string {
  const kindLabel = kind === "unity_result" ? "Unity result" : kind.charAt(0).toUpperCase() + kind.slice(1);
  const parts = [kindLabel];
  if (viewedVersionId) {
    parts.push(`Viewing ${shortVersionLabel(viewedVersionId)}${viewedVersionId === latestVersionId ? " (latest)" : ""}`);
  }
  if (latestVersionId && latestVersionId !== viewedVersionId) parts.push(`Latest ${shortVersionLabel(latestVersionId)}`);
  if (ownerVersionId) parts.push(`Owner selected ${shortVersionLabel(ownerVersionId)}`);
  return parts.join(" · ");
}
import type { ItemRecord, SelectionRecord, VersionRecord } from "../../api";

export function catalogueVersionKey(items: readonly ItemRecord[]): string {
  return JSON.stringify(items.map(i => [i.item_id, i.current_version_id, i.state_revision]));
}

export function indexCatalogueVersions(versions: readonly VersionRecord[]): Map<string, VersionRecord[]> {
  const grouped = new Map<string, VersionRecord[]>();
  for (const version of versions) {
    const values = grouped.get(version.item_id);
    if (values) values.push(version);
    else grouped.set(version.item_id, [version]);
  }
  for (const values of grouped.values()) values.sort((a, b) => b.created_at.localeCompare(a.created_at));
  return grouped;
}

export function indexCatalogueSelections(selections: readonly SelectionRecord[]): Map<string, SelectionRecord> {
  const grouped = new Map<string, SelectionRecord>();
  for (const choice of selections) {
    if (choice.slot.kind === "item" && choice.state === "selected" && !grouped.has(choice.slot.item_id)) grouped.set(choice.slot.item_id, choice);
  }
  return grouped;
}
