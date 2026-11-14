/** Actual W05 surfaces, accepted W03 GLB bytes, isolated owner HTTP fixtures. */
import React, { useCallback, useState } from "react";
import { createRoot } from "react-dom/client";
import { ApiClient } from "../../apps/frontend/src/api/client";
import type { ItemRecord, JobRecord, VersionRecord } from "../../apps/frontend/src/api/generated/contracts";
import { emptySelection, type TurnSelection } from "../../apps/frontend/src/api/studio";
import { ReviewWorkspace, Outliner } from "../../apps/frontend/src/features/review";
import { CatalogueBrowser } from "../../apps/frontend/src/features/catalogue";

const api = new ApiClient();
interface Fixture {version: VersionRecord; jobs: JobRecord[]; items: ItemRecord[]; lateImageId: string}

function Harness({fixture}: {fixture: Fixture}) {
  const [selection, setSelection] = useState<TurnSelection>(emptySelection);
  const [revision, setRevision] = useState(0);
  const [jobs, setJobs] = useState(fixture.jobs);
  const [viewing, setViewing] = useState(fixture.version.version_id);
  const queue = useCallback(async (text: string, context: TurnSelection) => {
    const response = await fetch("/camera-owner-queue", {method: "POST", headers: {"Content-Type":"application/json"}, body: JSON.stringify({text, context})});
    if (!response.ok) throw new Error("Synthetic lost Queue acknowledgement. Inspect the retained request in Copilot.");
  }, []);
  if (new URLSearchParams(location.search).has("catalogue")) return <CatalogueBrowser api={api} projectId={fixture.version.project_id} items={fixture.items} selectedVersionId={viewing} refreshRevision={revision} onSelect={version => setViewing(version.version_id)}/>;
  const props = {api, projectId: fixture.version.project_id, version: fixture.version, jobs, refreshRevision: revision, selection, onContextChange: setSelection, onQueue: queue};
  return <>
    <button onClick={() => setJobs(old => old.map(job => ({...job, artifact_ids: [...job.artifact_ids, fixture.lateImageId]})))}>Link completed job image</button>
    <button onClick={() => {setRevision(n => n + 1); setJobs(old => old.map(job => ({...job, state_revision: job.state_revision + 1, artifact_ids: [...job.artifact_ids, fixture.lateImageId]})));}}>Refresh late associations</button>
    <div style={{display: "grid", gridTemplateColumns: "240px 1031.5px", height: "799.25px"}}><Outliner {...props}/><ReviewWorkspace {...props}/></div>
    <output data-testid="selection">{JSON.stringify(selection)}</output>
  </>;
}

fetch("/camera-fixture").then(response => response.json() as Promise<Fixture>).then(fixture => createRoot(document.getElementById("root")!).render(<Harness fixture={fixture}/>));
