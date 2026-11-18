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
