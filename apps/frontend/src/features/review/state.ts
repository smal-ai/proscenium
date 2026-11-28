import { create } from "zustand";
import type { ObjectSelection, ProxyMapping } from "../../api/generated/contracts";

// The viewport and its object list share exact stable pairs, not display names.
export const useReviewSelection = create<{
  scope: string; selected: ObjectSelection[];
  select: (scope: string, selected: ObjectSelection[]) => void;
}>((set) => ({scope: "", selected: [], select: (scope, selected) => set({scope, selected})}));

export const objectPairKey = ({ object_id, instance_id }: ObjectSelection) => JSON.stringify([object_id, instance_id]);

export function compatibleObjects(selected: ObjectSelection[], mapping: ProxyMapping) {
  if (!selected.length) return selected;
  const allowed = new Set(mapping.nodes.map(objectPairKey));
  const retained = selected.filter(object => allowed.has(objectPairKey(object)));
  return retained.length === selected.length ? selected : retained;
}

export function toggleObject(selected: ObjectSelection[], pair: ObjectSelection, multiple: boolean): ObjectSelection[] {
  if (!multiple) return [pair];
  const key = objectPairKey(pair);
  return selected.some(object => objectPairKey(object) === key)
    ? selected.filter(object => objectPairKey(object) !== key)
    : [...selected, pair];
}
