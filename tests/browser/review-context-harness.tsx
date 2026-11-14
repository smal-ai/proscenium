/** Actual ReviewWorkspace + accepted W04 ChatPanel/composer, isolated synthetic HTTP. */
import React, { useCallback, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { ApiClient } from "../../apps/frontend/src/api/client";
import { emptySelection, type ContextPreparer, type TurnSelection } from "../../apps/frontend/src/api/studio";
import type { ItemRecord, VersionRecord } from "../../apps/frontend/src/api/generated/contracts";
import { ReviewWorkspace } from "../../apps/frontend/src/features/review";
import { CatalogueBrowser } from "../../apps/frontend/src/features/catalogue";
import { ChatPanel } from "../../apps/frontend/src/features/chat/ChatPanel";
import { useRequestComposer } from "../../apps/frontend/src/features/chat/useRequestComposer";
import "../../apps/frontend/src/features/review/review.css";
import { syntheticJobs, syntheticVersion } from "./review-fixtures";

const api = new ApiClient();
const version = syntheticVersion;
const jobs = syntheticJobs;
const items: ItemRecord[] = [{schema_version:"1.0",project_id:"synthetic",item_id:"scene",kind:"scene",name:"Scene",current_version_id:"v2",selected_version_id:null,state_revision:0}];

function Harness() {
  const [selection, setSelection] = useState<TurnSelection>(emptySelection);
  const [currentVersion, setVersion] = useState<VersionRecord | null>(version);
  const [visible, setVisible] = useState(true);
  const [accepted, setAccepted] = useState<unknown>(null);
  const prepare = useRef<ContextPreparer | null>(null);
  const registrations = useRef(0);
  const register = useCallback((handler: ContextPreparer | null) => {prepare.current = handler; registrations.current++;}, []);
  const selectionRef = useRef(selection); selectionRef.current = selection;
  const prepareContext = useCallback(async () => prepare.current ? prepare.current() : structuredClone(selectionRef.current), []);
  const composer = useRequestComposer(api, "synthetic", setAccepted);
  const refresh = useCallback(async () => {}, []);
  Object.assign(window, {contextHarness: {hasPreparer: () => !!prepare.current, registrations: () => registrations.current, prepare: prepareContext}});

  if (new URLSearchParams(location.search).has("catalogue")) return <CatalogueBrowser api={api} projectId="synthetic" items={items} selectedVersionId={currentVersion?.version_id ?? null} refreshRevision={0} onSelect={setVersion}/>;
  return <>
    <button onClick={() => {setVersion({...version,version_id:"v2",name:"Version two"});setSelection(emptySelection());}}>Navigate to another version</button>
    <button onClick={() => setVisible(false)}>Unmount review</button>
    <button onClick={() => setSelection({...emptySelection(),item_id:"scene",version_id:"v1",view_mode:"keyframes",target:{origin:"reference",artifact_id:"frame-2",displayed_sha256:"b".repeat(64),dimensions:{width:640,height:360},item_id:"scene",version_id:"v1",shot_id:null,render_job_id:null,source_frame:null,media_artifact_id:null},annotations:[{annotation_id:"raster-note",saved_revision:null,target:{origin:"reference",artifact_id:"frame-2",displayed_sha256:"b".repeat(64),dimensions:{width:640,height:360},item_id:"scene",version_id:"v1",shot_id:null,render_job_id:null,source_frame:null,media_artifact_id:null},camera:null,range:null,text:"Imported soft mask",spatial_hits:[],spatial_status:"unavailable",markups:[{id:"raster",kind:"mask",points:[[.5,.5]],mask_artifact_id:"imported-raster",mask_encoding:{channel:"luma",bit_depth:8,selected_value:255,unselected_value:0,semantics:"soft"}}]}]})}>Open imported raster draft</button>
    <div style={{display:"grid",gridTemplateColumns:"1fr 400px",height:820}}>
      {visible && <ReviewWorkspace api={api} projectId="synthetic" version={currentVersion} jobs={jobs} selection={selection} refreshRevision={0} onContextChange={setSelection} onContextPreparer={register} onQueue={async (text,context) => {await composer.submit(text,context,"queue",null,null);}}/>}
      <ChatPanel api={api} projectId="synthetic" snapshot={null} requests={[]} events={[]} jobs={[]} selection={selection} prepareContext={prepareContext} composer={composer} onRefresh={refresh}/>
    </div>
    <output data-testid="accepted">{JSON.stringify(accepted)}</output>
  </>;
}

createRoot(document.getElementById("root")!).render(<Harness/>);
