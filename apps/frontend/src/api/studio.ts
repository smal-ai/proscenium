/** Integration-owned component seams. All business records come from generated contracts. */
import type { ComponentType } from "react";
import type { ApiClient } from "./client";
import type { Artifact, ContextSnapshot, Event, ItemRecord, JobRecord, ProjectSnapshot, RequestRecord, RequestTarget, RetainedArtifactSummary, VersionRecord } from "./generated/contracts";

export interface ComposerSeedIntent {
  nonce: string;
  target: RequestTarget;
  templateText?: string;
  focusChat?: boolean;
}

export interface AssistantStatusProps {
  snapshot: ProjectSnapshot | null;
  requests: RequestRecord[];
  events: Event[];
  connection: string;
  hydrated?: boolean;
  submitting?: boolean;
  stopping?: boolean;
  compact?: boolean;
}

export type TurnSelection = Omit<ContextSnapshot, "schema_version" | "context_id" | "project_id" | "captured_at">;
export type ContextPreparer = () => Promise<TurnSelection>;

export const emptySelection = (): TurnSelection => ({
  item_id: null,
  version_id: null,
  view_mode: "none",
  target: null,
  camera: null,
  range: null,
  selected_objects: [],
  attachments: [],
  annotations: [],
  approved_findings: [],
});

export interface ReviewSurfaceProps {
  visible?: boolean;
  api: ApiClient;
  projectId: string;
  version: VersionRecord | null;
  jobs: JobRecord[];
  refreshRevision: number;
  selection?: TurnSelection;
  onContextChange: (selection: TurnSelection) => void;
  onContextPreparer?: (prepare: ContextPreparer | null) => void;
  onNavigate?: (version: VersionRecord, selection: TurnSelection) => void;
  onQueue: (text: string, selection: TurnSelection) => void | Promise<void>;
}

export interface CatalogueSurfaceProps {
  retainedArtifacts?: RetainedArtifactSummary[];
  onCreateAsset?: () => void;
  onInspectArtifact?: (artifact: Artifact) => void;
  api: ApiClient;
  projectId: string;
  items: ItemRecord[];
  selectedVersionId: string | null;
  refreshRevision: number;
  onSelect: (version: VersionRecord) => void;
}

export interface CriticSurfaceProps {
  api: ApiClient;
  projectId: string;
  selection: TurnSelection;
  refreshRevision: number;
  onQueue: (text: string, selection: TurnSelection) => void | Promise<void>;
  onSteer?: (text: string, selection: TurnSelection) => void | Promise<void>;
}

export interface StudioComponents {
  assistantStatus?: ComponentType<AssistantStatusProps>;
  review?: ComponentType<ReviewSurfaceProps>;
  outliner?: ComponentType<ReviewSurfaceProps>;
  catalogue?: ComponentType<CatalogueSurfaceProps>;
  storyboards?: ComponentType<ReviewSurfaceProps>;
  critic?: ComponentType<CriticSurfaceProps>;
}
