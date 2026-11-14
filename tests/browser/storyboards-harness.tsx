import React, { useState } from "react";
import { createRoot } from "react-dom/client";
import { ApiClient } from "../../apps/frontend/src/api/client";
import { emptySelection, type TurnSelection } from "../../apps/frontend/src/api/studio";
import { StoryboardBrowser } from "../../apps/frontend/src/features/storyboards";
import type { JobRecord } from "../../apps/frontend/src/api/generated/contracts";

const api = new ApiClient();
api.setCsrfToken("synthetic-csrf");
const renderJobs: JobRecord[] = new URLSearchParams(location.search).has("receipts") ? [{
  schema_version: "1.0", job_id: "render-job", project_id: "garden-project", work_id: "garden-work", owner_operation_id: null,
  target: { target_id: "scene-target", version: { item_id: "scene-item", version_id: "scene-v1" }, shot_id: "leaf-arc" },
  attempt_id: "render-attempt", attempt_number: 1, kind: "render", state: "succeeded", state_revision: 3,
  availability: "complete", input_digest: "c".repeat(64), external_job_id: null, provider_id: null, owner_epoch: "synthetic-epoch",
  completed_frames: 1, expected_frames: 1, artifact_ids: ["render-a"], render_spec: null,
}] : [];
function Harness() {
  const [selection, setSelection] = useState(emptySelection());
  const [navigation, setNavigation] = useState("");
  const [queued, setQueued] = useState<{ text: string; selection: TurnSelection } | null>(null);
  return <main style={{ display: "grid", gridTemplateColumns: "280px 1fr 360px", height: "100vh", background: "#101216", color: "#e4e6eb" }}>
    <StoryboardBrowser api={api} projectId="garden-project" version={null} jobs={renderJobs} refreshRevision={0}
      onContextChange={setSelection} onNavigate={(version, next) => { setNavigation(version.version_id); setSelection(next); }}
      onQueue={async (text, next) => { setQueued({ text, selection: next }); }} />
    <section aria-label="Synthetic central reviewer" style={{ padding: 24 }}>
      <h1>Synthetic component harness, not product acceptance</h1>
      <p data-testid="navigation">{navigation}</p>
      <pre data-testid="context">{JSON.stringify(selection, null, 2)}</pre>
      {selection.target && <img alt="Central selected image" width="480" src={`/api/v1/projects/garden-project/artifacts/${selection.target.artifact_id}/content`} />}
    </section>
    <aside><h2>Queued request</h2><pre style={{ whiteSpace: "pre-wrap" }} data-testid="queued">{JSON.stringify(queued)}</pre></aside>
  </main>;
}
createRoot(document.getElementById("root")!).render(<Harness />);
