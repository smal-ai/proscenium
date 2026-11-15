import { create } from "zustand";
import type { ObjectSelection } from "../../api/generated/contracts";

// Review and outliner are separately composed surfaces, but share exact stable pairs.
export const useReviewSelection = create<{
  scope: string; selected: ObjectSelection[];
  select: (scope: string, selected: ObjectSelection[]) => void;
}>((set) => ({scope: "", selected: [], select: (scope, selected) => set({scope, selected})}));
