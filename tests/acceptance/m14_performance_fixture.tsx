/** Controlled component fixture, not a new backend/native journey or full-shell benchmark. */
import React, { useCallback, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { ProxyView, type ProxyCapture } from '../../apps/frontend/src/features/review/ProxyView';
import type { ProxyMapping } from '../../apps/frontend/src/api/generated/contracts';

const observed = (window as any).__m14 = {readyCase:null,readyAt:null,loadStart:null,capture:null,lastCamera:null as unknown,cameraChanges:0,selections:[]};
function Fixture(){
  const [active,setActive]=useState<{name:string;mapping:ProxyMapping}|null>(null);
  const bind=useCallback((capture:ProxyCapture|null)=>{
    observed.capture=capture;
    if(capture){observed.readyCase=active?.name;observed.readyAt=performance.now();}
  },[active?.name]);
  const camera=useCallback((value:unknown)=>{observed.lastCamera=value;observed.cameraChanges=(observed.cameraChanges??0)+1;},[]);
  const select=useCallback((value:unknown)=>{observed.selections.push(value);},[]);
  async function load(name:string){
    observed.readyCase=null;observed.readyAt=null;observed.capture=null;observed.loadStart=performance.now();
    const response=await fetch(`/fixtures/${name}-map.json`);if(!response.ok)throw new Error('Mapping unavailable');
    setActive({name,mapping:await response.json()});
  }
  return <main><h1>Maintained ProxyView · controlled compiled performance fixture</h1>
    <p>Real retained GLBs. No backend, Copilot, native authoring, rendering or inference.</p>
    <button onClick={()=>load('small')}>Load small</button><button onClick={()=>load('heavy')}>Load heavy</button>
    <div style={{width:1600,height:900,margin:'12px auto',position:'relative',background:'#17191f'}}>
      {active&&<ProxyView key={active.name} url={`/fixtures/${active.name}.glb`} mapping={active.mapping}
        onCamera={camera} onSelect={select} onCaptureReady={bind}/>}</div>
  </main>;
}
const style=document.createElement('style');style.textContent='body{margin:0;background:#101217;color:#ddd;font:14px sans-serif;text-align:center}h1{font-size:20px;margin:12px}p{margin:8px}button{margin:0 6px;padding:5px 15px}.review-proxy{width:100%;height:100%;position:relative}.review-proxy-label{position:absolute;left:8px;bottom:8px;pointer-events:none}';document.head.append(style);
createRoot(document.getElementById('root')!).render(<Fixture/>);
