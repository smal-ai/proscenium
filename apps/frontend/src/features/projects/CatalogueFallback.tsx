import { useEffect, useRef, useState } from "react";
import { Box, ChevronDown, Layers } from "lucide-react";
import type { CatalogueSurfaceProps } from "../../api/studio";
import type { VersionRecord } from "../../api";
import { allPages } from "../../app/useProjectData";
import { describeError } from "../../app/storage";

export function CatalogueFallback({ api, projectId, items, selectedVersionId, refreshRevision, onSelect, onCreateAsset }: CatalogueSurfaceProps) {
  const [expanded, setExpanded] = useState<string | null>(null);
  const [versions, setVersions] = useState<VersionRecord[]>([]);
  const [error, setError] = useState("");
  const selectAfterLoad = useRef<string | null>(null);
  const onSelectRef = useRef(onSelect); onSelectRef.current = onSelect;
  useEffect(() => {
    let disposed = false;
    setVersions([]); setError("");
    if (expanded) void allPages(cursor => api.call("listItemVersions", { path: { project_id: projectId, item_id: expanded }, query: { cursor, limit: 100 } })).then(v => { if (!disposed) {
      setVersions(v);
      if (selectAfterLoad.current === expanded) {
        selectAfterLoad.current = null;
        const item = items.find(item => item.item_id === expanded);
        const current = v.find(version => version.version_id === item?.current_version_id) ?? v[0];
        if (current) onSelectRef.current(current);
      }
    } }).catch(e => { if (!disposed) setError(describeError(e)); });
    return () => { disposed = true; };
  }, [api, expanded, projectId, refreshRevision, items]);
  return <div className="catalogue-list">{onCreateAsset && <button className="primary new-asset" onClick={onCreateAsset}>New asset</button>}<div className="eyebrow">Published catalogue <span>{items.length}</span></div>
    {!items.length && <div className="empty-panel"><Layers size={23} /><h3>No published items</h3><p>Completed assets, scenes and sequences appear here after publication.</p></div>}
    {items.map(item => <section key={item.item_id} className="catalogue-item"><button className="item-trigger" aria-expanded={expanded === item.item_id} onClick={() => {
      const loaded = versions.filter(version => version.item_id === item.item_id);
      const current = loaded.find(version => version.version_id === item.current_version_id) ?? loaded[0];
      if (current) onSelect(current);
      else selectAfterLoad.current = item.item_id;
      setExpanded(item.item_id);
    }}><Box size={16} /><span><strong>{item.name}</strong><small>{item.kind}</small></span><ChevronDown size={13} /></button>
      {expanded === item.item_id && <div className="versions">{versions.map(v => <button key={v.version_id} className={selectedVersionId === v.version_id ? "version selected" : "version"} onClick={() => onSelect(v)}><span>{v.version_id === item.current_version_id ? "Current publication" : "Historical publication"}</span><code>{v.source_commit.slice(0, 10)}</code><small>{v.version_id}</small></button>)}{!versions.length && !error && <p>No versions available.</p>}</div>}
    </section>)}
    {error && <p role="alert">{error}</p>}
  </div>;
}
