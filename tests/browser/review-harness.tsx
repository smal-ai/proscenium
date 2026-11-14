/** Isolated synthetic surface composition, not the W04 application. */
import React, { useCallback, useState } from "react";
import { createRoot } from "react-dom/client";
import { ApiClient } from "../../apps/frontend/src/api/client";
import { emptySelection } from "../../apps/frontend/src/api/studio";
import type { TurnSelection } from "../../apps/frontend/src/api/studio";
import type { JobRecord, VersionRecord } from "../../apps/frontend/src/api/generated/contracts";
import { ReviewWorkspace, Outliner, CriticPanel } from "../../apps/frontend/src/features/review";
import * as media from "../../apps/frontend/src/features/review/media";
import { greyPng } from "../../apps/frontend/src/features/review/png";

const api = new ApiClient();
const version = {schema_version:"1.0",project_id:"synthetic",item_id:"scene",version_id:"v1",kind:"scene",name:"Synthetic version",publication_id:"publication",source_commit:"a".repeat(40),source:{digest:"a".repeat(64)},artifact_ids:[],preview_requests:[],preview_job_ids:["job"],created_at:"2026-11-14T00:00:00Z",actor:{kind:"user",id:"owner"}} as VersionRecord;
const jobs = [{job_id:"job",project_id:"synthetic",target:{target_id:"scene",version:{item_id:"scene",version_id:"v1"},shot_id:"shot"},state_revision:0,render_spec:{engine:"BLENDER_EEVEE_NEXT",purpose:"keyframe",samples:16}}] as JobRecord[];

function Harness() {
  const [selection, setSelection] = useState<TurnSelection>(emptySelection);
  const [queued, setQueued] = useState<unknown[]>([]);
  const [revision, setRevision] = useState(0);
  const queue = useCallback((text: string, context: TurnSelection) => setQueued(old => [...old,{text,context}]), []);
  const props = {api,projectId:"synthetic",version,jobs,selection,refreshRevision:revision,onContextChange:setSelection,onQueue:queue,onNavigate:(_version:VersionRecord,context:TurnSelection)=>setSelection(context)};
  return <><button onClick={() => setRevision(r => r+1)}>Refresh receipts</button><button onClick={()=>setSelection({...emptySelection(),item_id:"scene",version_id:"v1",view_mode:"keyframes",target:{origin:"concept",artifact_id:"external-image",displayed_sha256:"e".repeat(64),dimensions:{width:640,height:360},item_id:"scene",version_id:"v1",shot_id:null,render_job_id:null,source_frame:null,media_artifact_id:null}})}>External primary target</button><input aria-label="External text input"/>
    <div style={{display:"grid",gridTemplateColumns:"240px 1fr 320px",height:870}}><Outliner {...props}/><ReviewWorkspace {...props}/><CriticPanel api={api} projectId="synthetic" selection={selection} refreshRevision={revision} onQueue={queue}/></div>
    <output data-testid="selection">{JSON.stringify(selection)}</output><output data-testid="queued">{JSON.stringify(queued)}</output>
  </>;
}

Object.assign(window, {reviewTest: {media,greyPng}});
createRoot(document.getElementById("root")!).render(<Harness/>);
