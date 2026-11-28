import { useId, useState } from "react";
import type { ObjectSelection, ProxyMapping } from "../../api/generated/contracts";
import { objectPairKey, toggleObject } from "./state";

export function InspectionObjects({ mapping, unavailable, loading, selected, onSelect }: {
  mapping: ProxyMapping | null; unavailable: string | null; loading: boolean;
  selected: ObjectSelection[]; onSelect: (pairs: ObjectSelection[]) => void;
}) {
  const [open, setOpen] = useState(false);
  const id = useId();
  const label = mapping ? `Objects at frame ${mapping.sample_frame}` : "Objects";
  const chosen = new Set(selected.map(objectPairKey));
  return <div className="inspection-objects">
    <button className="toolbar-btn" type="button" aria-expanded={open} aria-controls={id} onClick={() => setOpen(previous => !previous)}>{label}</button>
    <section id={id} hidden={!open} className="inspection-objects-list" aria-label={label}>
      <p>Object context only. No spatial hit is inferred from this list.</p>
      {unavailable ? <p role="status">Pinned 3D proxy unavailable: {unavailable}</p>
        : !mapping ? <p role="status">{loading ? "Loading pinned 3D proxy mapping…" : "No verified proxy mapping is available for this version."}</p>
        : !mapping.nodes.length ? <p>No objects in this proxy sample.</p>
        : <ul>{mapping.nodes.map(node => {
          const pair = { object_id: node.object_id, instance_id: node.instance_id };
          const key = objectPairKey(pair);
          return <li key={key}><button type="button" aria-pressed={chosen.has(key)} title={`${pair.object_id} / ${pair.instance_id}`}
            onClick={event => onSelect(toggleObject(selected, pair, event.ctrlKey || event.metaKey))}>{node.display_name}<small>{pair.instance_id}</small></button></li>;
        })}</ul>}
    </section>
  </div>;
}
