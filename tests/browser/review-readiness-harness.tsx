/** Compiled feature component, explicit owner navigation controls and synthetic HTTP. */
import React, { useCallback, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { ApiClient } from "../../apps/frontend/src/api/client";
import { emptySelection, type ContextPreparer, type TurnSelection } from "../../apps/frontend/src/api/studio";
import type { FrameTarget, JobRecord, VersionRecord } from "../../apps/frontend/src/api/generated/contracts";
import { CriticPanel, ReviewWorkspace } from "../../apps/frontend/src/features/review";
import { syntheticVersion } from "./review-fixtures";

const api=new ApiClient();
const base:FrameTarget={origin:"reference",artifact_id:"same-reference",displayed_sha256:"a".repeat(64),dimensions:{width:640,height:360},item_id:syntheticVersion.item_id,version_id:syntheticVersion.version_id,shot_id:"shot-a",render_job_id:null,source_frame:null,media_artifact_id:null};
function Harness(){
  const [selection,setSelection]=useState<TurnSelection>(emptySelection);
  const [prepared,setPrepared]=useState<TurnSelection|null>(null);
  const handler=useRef<ContextPreparer|null>(null);
  const register=useCallback((value:ContextPreparer|null)=>{handler.current=value;},[]);
  if(new URLSearchParams(location.search).has("critic"))return <><CriticPanel api={api} projectId="synthetic" selection={{...emptySelection(),target:{...base,origin:"concept",shot_id:null},attachments:[{artifact_id:base.artifact_id,role:"concept",use:"Original concept"},{artifact_id:"extra-reference",role:"reference"},{artifact_id:"existing-markup",role:"markup"},{artifact_id:"existing-mask",role:"mask"}]}} refreshRevision={0} onQueue={async(_text,context)=>setPrepared(context)}/><output data-testid="prepared">{JSON.stringify(prepared)}</output></>;
  function navigate(target:FrameTarget){setSelection({...emptySelection(),item_id:target.item_id,version_id:target.version_id,view_mode:"keyframes",target,attachments:[{artifact_id:target.artifact_id,role:"reference",use:`${target.shot_id} / frame-${target.shot_id}`}]});}
  return <>
    <button onClick={()=>navigate(base)}>Shot A / frame A</button>
    <button onClick={()=>navigate({...base,shot_id:"shot-b"})}>Shot B / frame B (same PNG)</button>
    <button onClick={()=>navigate({...base,shot_id:"shot-c",artifact_id:"delayed-reference",displayed_sha256:"b".repeat(64)})}>Different pending image</button>
    <button onClick={()=>navigate({...base,shot_id:"shot-d",dimensions:{width:1280,height:720}})}>Mismatched dimensions</button>
    <button onClick={()=>navigate({...base,shot_id:"shot-e",artifact_id:"failed-reference"})}>Broken image</button>
    <button onClick={async()=>{if(handler.current)setPrepared(await handler.current());}}>Prepare current context</button>
    <div style={{height:760}}><ReviewWorkspace api={api} projectId="synthetic" version={syntheticVersion} jobs={[]} refreshRevision={0} selection={selection} onContextChange={setSelection} onContextPreparer={register} onQueue={async(_text,context)=>setPrepared(context)}/></div>
    <output data-testid="selection">{JSON.stringify(selection)}</output><output data-testid="prepared">{JSON.stringify(prepared)}</output>
  </>;
}
function CameraHarness({fixture}:{fixture:{version:VersionRecord;jobs:JobRecord[]}}){
  const [selection,setSelection]=useState<TurnSelection>(emptySelection);
  const [prepared,setPrepared]=useState<TurnSelection|null>(null);
  const handler=useRef<ContextPreparer|null>(null);
  const register=useCallback((value:ContextPreparer|null)=>{handler.current=value;},[]);
  return <><button onClick={async()=>{if(handler.current)setPrepared(await handler.current());}}>Prepare current context</button>
    <div style={{height:850}}><ReviewWorkspace api={api} projectId={fixture.version.project_id} version={fixture.version} jobs={fixture.jobs} refreshRevision={0} selection={selection} onContextChange={setSelection} onContextPreparer={register} onNavigate={(_version,context)=>setSelection(context)} onQueue={async(_text,context)=>setPrepared(context)}/></div>
    <output data-testid="selection">{JSON.stringify(selection)}</output><output data-testid="prepared">{JSON.stringify(prepared)}</output></>;
}
if(new URLSearchParams(location.search).has("m04")){
  fetch("/readiness-m04-fixture").then(response=>response.json() as Promise<{version:VersionRecord;jobs:JobRecord[]}>).then(fixture=>createRoot(document.getElementById("root")!).render(<CameraHarness fixture={fixture}/>));
}else createRoot(document.getElementById("root")!).render(<Harness/>);
