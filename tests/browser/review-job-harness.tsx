import React, { useState } from "react";
import { createRoot } from "react-dom/client";
import { ApiClient } from "../../apps/frontend/src/api/client";
import type { FrameTarget, JobRecord } from "../../apps/frontend/src/api/generated/contracts";
import { emptySelection, type TurnSelection } from "../../apps/frontend/src/api/studio";
import { ReviewWorkspace } from "../../apps/frontend/src/features/review";
import { syntheticVersion, syntheticJobs } from "./review-fixtures";

const api = new ApiClient();
export const version = {...syntheticVersion, artifact_ids: ["initial", "correction", "reference"], preview_job_ids: [], preview_requests: []};
export const job = (id: string, ids: string[]): JobRecord => ({...syntheticJobs[0],job_id:id,kind:"image_generation",render_spec:null,state:"succeeded",availability:"complete",artifact_ids:ids});
function Harness() {
  const [jobs, setJobs] = useState([job("initial-job",["conditioning","initial","metadata"]),job("correction-job",["correction"]),job("missing-job",["missing"]),job("pending-job",["conditioning"])]);
  const [selection,setSelection] = useState<TurnSelection>(emptySelection);
  const jump = (id:string) => {
    const target:FrameTarget={origin:id === "reference" ? "reference" : "concept",artifact_id:id,displayed_sha256:(id==="correction"?"b":"a").repeat(64),dimensions:{width:1,height:1},item_id:version.item_id,version_id:version.version_id,shot_id:"storyboard-shot",render_job_id:null,source_frame:null,media_artifact_id:null};
    setSelection({...emptySelection(),item_id:version.item_id,version_id:version.version_id,view_mode:"keyframes",target,range:{start:3,end:7}});
  };
  return <>
    {["initial","correction","reference"].map(id=><button key={id} onClick={()=>jump(id)}>Storyboard {id}</button>)}
    <button onClick={()=>setJobs(previous=>[job("late-job",["late"]),...previous])}>Late job arrives</button>
    <ReviewWorkspace api={api} projectId={version.project_id} version={version} jobs={jobs} refreshRevision={0} selection={selection} onContextChange={setSelection} onQueue={async()=>{throw new Error("No native sends authorised");}}/>
    <output data-testid="selection">{JSON.stringify(selection)}</output>
  </>;
}
createRoot(document.getElementById("root")!).render(<Harness/>);
