// One continuous Full-HD profile, actual product UI and SSH-only page traffic.
import {chromium} from 'playwright';
import fs from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {pathToFileURL} from 'node:url';
const root=process.argv[2];
const spec=JSON.parse(await fs.readFile(`${root}/allocation.json`,'utf8'));
const p=`/api/v1/projects/${spec.project_id}`;
const sha=b=>createHash('sha256').update(b).digest('hex');
const result={status:'incomplete',off_origin:[],websockets:[],responses:[],phases:[]};
result.observer_failures=[];
const save=(name,value)=>fs.writeFile(`${root}/${name}.json`,JSON.stringify(value,null,2),{mode:0o600});
const exists=name=>fs.access(`${root}/${name}`).then(()=>true,()=>false);
async function wait(name,timeout=200000){const start=Date.now();while(!await exists(name)){if(Date.now()-start>timeout)throw Error(`File deadline: ${name}`);await new Promise(r=>setTimeout(r,50));}}
let context,page;
async function observeStage(name,operation){
  for(let attempt=1;;attempt++){
    try{return await operation();}catch(error){
      const failure={stage:name,attempt,error:String(error).split('\n')[0],at:new Date().toISOString()};
      result.observer_failures.push(failure);await save(`observer-${name}-${attempt}`,failure);
      await page.screenshot({path:`${root}/observer-${name}-${attempt}.png`}).catch(()=>{});
      await fs.writeFile(`${root}/observer-${name}-${attempt}-private.txt`,await page.locator('body').innerText().catch(()=>''),{mode:0o600});
      await wait(`continue-${name}-${attempt}`,120000);
      const continuation=await fs.readFile(`${root}/continue-${name}-${attempt}`,'utf8');
      if(continuation.trim().startsWith('{')){
        const command=JSON.parse(continuation);
        if(command.observer_module){
          const module=await import(`${pathToFileURL(command.observer_module).href}?attempt=${attempt}`);
          return await module.recover({page,context,get,view,note,save,result,spec,root,sha});
        }
      }
      // Only a read/review observer can be retried here. Queue is outside this function.
    }
  }
}
async function get(path){return page.evaluate(async path=>{const r=await fetch(path);if(!r.ok)throw Error(`GET ${path}: ${r.status}`);return r.json();},path);}
async function view(){
  await page.getByRole('button').filter({hasText:/^M13 two-shot cinematic.*m13-film-v1$/}).click();
  await page.getByRole('button',{name:'3 Animation',exact:true}).click();
  await page.getByRole('link',{name:'Download final video',exact:true}).waitFor();
  await page.locator('video').waitFor();
}
async function note(text){
  const prior=(await get(`${p}/annotations`)).items.find(a=>a.text===text);
  if(prior)return prior;
  await page.getByLabel('Frame',{exact:true}).fill('5');
  await page.getByText('Sequence frame 5 ·',{exact:false}).waitFor();
  await page.getByRole('button',{name:'Annotate presented sequence frame',exact:true}).click();
  await page.getByRole('button',{name:'pin',exact:true}).click();
  await page.getByLabel('Pin text',{exact:true}).fill(text);
  const box=await page.getByLabel('Editable image-space markup',{exact:true}).boundingBox();
  await page.mouse.click(box.x+box.width*.5,box.y+box.height*.5);
  await page.getByLabel('Feedback',{exact:true}).fill(text);
  const pending=page.waitForResponse(r=>r.request().method()==='POST'&&r.url().endsWith('/annotations'));
  await page.getByRole('button',{name:'Save annotation',exact:true}).click();
  const response=await pending;if(!response.ok())throw Error('Real annotation save rejected');
  const annotations=await get(`${p}/annotations`);
  const annotation=annotations.items.find(a=>a.text===text||JSON.stringify(a).includes(text));
  if(!annotation||annotation.target.sequence_frame!==5)throw Error('Saved frame identity mismatch');
  await page.getByRole('button',{name:'Close draft',exact:true}).click();
  await page.reload();await page.getByRole('textbox',{name:'Message Copilot',exact:true}).waitFor();
  const reloaded=(await get(`${p}/annotations`)).items.find(a=>a.annotation_id===annotation.annotation_id);
  if(JSON.stringify(reloaded)!==JSON.stringify(annotation))throw Error('Annotation changed on reload');
  await page.getByText('Saved annotations and local drafts',{exact:true}).click();
  await page.getByRole('button').filter({hasText:new RegExp(`^${text}`)}).click();
  await page.getByText('Pinned m13-film-v1 · frame 5',{exact:true}).waitFor();
  await page.getByRole('button',{name:'Close draft',exact:true}).click();
  return annotation;
}
async function replay(){
  const http=[];let after=0;
  while(true){const batch=await get(`${p}/events?after=${after}&limit=100`);http.push(...batch.events);after=batch.next_after;if(after>=batch.last_available)break;}
  const ws=await page.evaluate(async project=>new Promise((resolve,reject)=>{
    const values=[];const socket=new WebSocket(`${location.origin.replace('http:','ws:')}/api/v1/events`);
    const timer=setTimeout(()=>{socket.close();reject(Error('WS replay deadline'));},15000);
    socket.onopen=()=>socket.send(JSON.stringify({schema_version:'1.0',type:'subscribe',project_id:project,after_sequence:0}));
    socket.onmessage=event=>{const value=JSON.parse(event.data);if(value.sequence)values.push(value);if(value.type==='caught_up'){clearTimeout(timer);socket.close();resolve(values);}};
    socket.onerror=()=>{clearTimeout(timer);reject(Error('WS transport failed'));};
  }),spec.project_id);
  const expected=http.map(e=>e.sequence),actual=ws.map(e=>e.sequence);
  if(JSON.stringify(actual)!==JSON.stringify(expected)||new Set(actual).size!==actual.length)throw Error('WS durable ordered replay mismatch');
  if(JSON.stringify(ws)!==JSON.stringify(http))throw Error('WS payload differs from durable HTTP event');
  return {sequences:actual,event_sha256:sha(JSON.stringify(http)),events:http};
}
try{
  context=await chromium.launchPersistentContext(`${root}/browser-profile`,{headless:true,
    viewport:{width:1920,height:1080},deviceScaleFactor:1,serviceWorkers:'block',
    args:['--disable-gpu','--disable-background-networking']});
  await context.route('**/*',route=>new URL(route.request().url()).origin===spec.origin?route.continue():route.abort());
  context.on('request',request=>{if(new URL(request.url()).origin!==spec.origin)result.off_origin.push(request.url());});
  context.on('response',response=>result.responses.push({path:new URL(response.url()).pathname,status:response.status(),method:response.request().method()}));
  page=context.pages()[0];page.setDefaultTimeout(15000);
  page.on('websocket',socket=>{const origin=new URL(socket.url()).origin.replace('ws:','http:');if(origin!==spec.origin)result.off_origin.push(socket.url());const row={url:socket.url(),received:[]};result.websockets.push(row);socket.on('framereceived',event=>{try{const v=JSON.parse(String(event.payload));if(v.sequence)row.received.push(v.sequence);}catch{}});});
  result.browser=context.browser().version();result.viewport={width:1920,height:1080,dpr:1};
  await page.goto(spec.origin,{waitUntil:'networkidle'});
  const pair=JSON.parse(await fs.readFile(`${root}/pairing-private.json`,'utf8'));
  await page.evaluate(code=>localStorage.setItem('proscenium.owner.pairing',JSON.stringify({schema_version:'1.0',operation_id:crypto.randomUUID(),client_nonce:crypto.randomUUID(),pairing_code:code})),pair.code);
  await page.reload({waitUntil:'networkidle'});
  await page.getByRole('button',{name:'Connect this browser',exact:true}).click();
  await page.getByRole('textbox',{name:'Message Copilot',exact:true}).waitFor();
  result.phases.push('forwarded UI pairing');
  await view();result.initial=await get(`${p}/snapshot`);result.replay_before=await replay();
  if((await get(`${p}/requests`)).items.length)throw Error('Unexpected pre-existing request');
  await fs.writeFile(`${root}/ui-send-intent`,sha(spec.prompt),{flag:'wx',mode:0o600});
  await page.getByRole('textbox',{name:'Message Copilot',exact:true}).fill(spec.prompt);
  const accepted=page.waitForResponse(r=>r.request().method()==='POST'&&r.url().endsWith('/requests'));
  await page.getByRole('button',{name:'Queue message',exact:true}).click();
  const response=await accepted;if(!response.ok())throw Error('Queue admission rejected');
  result.ui_admission={status:response.status(),receipt:await response.json()};
  await context.setOffline(true);
  await fs.writeFile(`${root}/browser-offline`,'actual browser offline',{flag:'wx',mode:0o600});
  await wait('reconnected');
  await context.setOffline(false);await page.reload();
  await page.getByRole('textbox',{name:'Message Copilot',exact:true}).waitFor();
  result.after_reconnect={requests:await get(`${p}/requests`),snapshot:await get(`${p}/snapshot`)};
  const requests=result.after_reconnect.requests.items;
  if(requests.length!==1||requests[0].receipt.state!=='completed')throw Error('Original request did not complete exactly once');
  result.replay_after=await replay();result.phases.push('original work completed with SSH/browser disconnected');
  await save('message-completed',result);
  await observeStage('review',async()=>{
  await page.reload();await page.getByRole('textbox',{name:'Message Copilot',exact:true}).waitFor();
  await view();
  await page.waitForFunction(()=>document.querySelector('video')?.readyState>=2);
  result.playback=await page.locator('video').evaluate(async element=>{
    const frames=[];let callback;let phase='seek';
    const observe=(_now,data)=>{frames.push({phase,mediaTime:data.mediaTime,presentedFrames:data.presentedFrames,width:data.width,height:data.height});callback=element.requestVideoFrameCallback(observe);};
    // Install before any play, not after a short film has already advanced.
    callback=element.requestVideoFrameCallback(observe);element.pause();element.currentTime=0;
    if(element.seeking)await new Promise(resolve=>element.addEventListener('seeked',resolve,{once:true}));
    const ended=new Promise((resolve,reject)=>{element.addEventListener('ended',resolve,{once:true});setTimeout(()=>reject(Error('Playback deadline')),5000);});
    element.playbackRate=.5;phase='playback';await element.play();await ended;
    element.cancelVideoFrameCallback(callback);return frames;
  });
  const presented=result.playback.filter(f=>f.phase==='playback').map(f=>({...f,ordinal:1+Math.floor(f.mediaTime*24+1e-4)}));
  result.presented_ordinals=presented.map(f=>f.ordinal).filter((f,i,all)=>i===0||f!==all[i-1]);
  if(JSON.stringify(result.presented_ordinals)!==JSON.stringify([1,2,3,4,5,6,7,8,9,10]))throw Error('Not all ten video frames were actually presented');
  result.mapping=await get(`${p}/artifacts/m13-encode-v1-frame-map/content`);
  result.manifest=await get(`${p}/artifacts/m13-encode-v1-manifest/content`);
  if(result.mapping.frame_count!==10||result.mapping.frames.length!==10)throw Error('Wrong film map');
  result.numbered_identity=presented.map(f=>({...f,...result.mapping.frames.find(row=>row.sequence_frame===f.ordinal)}));
  result.catalogue={};
  for(const id of ['m13-encode-v1-delivery','m13-encode-v1-frame-map','m13-encode-v1-manifest']){
    result.catalogue[id]=await get(`${p}/artifacts/${id}`);if(result.catalogue[id].state!=='ready')throw Error('Delivery lineage is not ready');
  }
  if(presented.some(f=>f.width!==640||f.height!==360))throw Error('Wrong decoded dimensions');
  result.annotation=await note('M14 SSH frame 5 original blend pin');
  await view();result.downloads=[];
  await fs.mkdir(`${root}/downloads`,{mode:0o700,recursive:true});
  for(const name of ['Download final video','Download delivery manifest']){
    const link=page.getByRole('link',{name,exact:true});const href=await link.getAttribute('href');
    const pending=page.waitForEvent('download');await link.click();const download=await pending;
    const file=`${root}/downloads/${download.suggestedFilename()}`;await download.saveAs(file);
    const bytes=await fs.readFile(file);result.downloads.push({name,href,bytes:bytes.length,sha256:sha(bytes)});
  }
  if(result.downloads[0].sha256!=='ddf01466d4a086bcdae27890e6cbabaa2bf309b62e48bf9353a056d92a60aa8e'||result.downloads[1].sha256!==result.catalogue['m13-encode-v1-manifest'].sha256)throw Error('Downloaded video/manifest differs from catalogue');
  result.range=await page.evaluate(async p=>{const r=await fetch(`${p}/artifacts/m13-encode-v1-delivery/content`,{headers:{Range:'bytes=0-31'},cache:'no-store'});return {status:r.status,headers:Object.fromEntries([...r.headers].filter(([key])=>['content-range','content-length','content-type','etag','accept-ranges','cache-control'].includes(key))),bytes:[...new Uint8Array(await r.arrayBuffer())]};},p);
  const video=await fs.readFile(`${root}/downloads/m13-encode-v1-delivery.mp4`).catch(async()=>{const files=await fs.readdir(`${root}/downloads`);return fs.readFile(`${root}/downloads/${files.find(f=>f.endsWith('.mp4'))}`);});
  if(result.range.status!==206||result.range.headers['content-range']!=='bytes 0-31/64876'||!Buffer.from(result.range.bytes).equals(video.subarray(0,32)))throw Error('Exact video range failed');
  const uploadPending=page.waitForResponse(r=>r.request().method()==='PUT'&&r.url().includes('/uploads/'));
  await page.getByLabel('Attach images',{exact:true}).setInputFiles(`${root}/upload.png`);
  const upload=await uploadPending;if(upload.status()!==201)throw Error('Actual image upload rejected');
  result.upload=await upload.json();const uploaded=await get(`${p}/artifacts/${result.upload.artifact_id}`);
  result.upload_metadata=uploaded;
  const actualImage=await fs.readFile(`${root}/upload.png`);
  if(uploaded.sha256!==sha(actualImage)||uploaded.bytes!==actualImage.length)throw Error('Uploaded image differs from actual bytes');
  await page.screenshot({path:`${root}/ssh-review.png`});
  await save('browser-progress',result);
  });
  await fs.writeFile(`${root}/review-complete`,'ready for quiet restart',{flag:'wx',mode:0o600});
  await context.setOffline(true);await wait('restarted',60000);
  await observeStage('unavailable',async()=>{
  await context.setOffline(false);await page.reload();
  await page.getByRole('textbox',{name:'Message Copilot',exact:true}).waitFor();
  await page.getByRole('button',{name:'Services & models',exact:true}).click();
  // The actual section has an accessible label, but no implicit region role in every browser.
  const menu=page.locator('section[aria-label="Services and models"]');
  await menu.waitFor();result.unavailable_text=await menu.innerText();
  if(!result.unavailable_text.includes('unavailable')||!result.unavailable_text.includes('Offline'))throw Error('Unavailable states not displayed');
  result.configuration=await get('/api/v1/configuration');
  await page.screenshot({path:`${root}/unavailable-services.png`});
  await page.getByRole('button',{name:'Services & models',exact:true}).click();
  await view();
  result.old_annotation_after_restart=(await get(`${p}/annotations`)).items.find(a=>a.annotation_id===result.annotation.annotation_id);
  if(JSON.stringify(result.old_annotation_after_restart)!==JSON.stringify(result.annotation))throw Error('Old annotation changed on restart');
  result.unavailable_annotation=await note('M14 unavailable-services frame 5 feedback');
  result.requests_after_restart=await get(`${p}/requests`);result.replay_restart=await replay();
  await page.screenshot({path:`${root}/unavailable-review.png`});
  });
  if(result.off_origin.length)throw Error('Off-origin page traffic');
  result.status='passed';result.phases.push('unavailable-service review and feedback reload');
}catch(error){
  result.error=String(error).split('\n')[0];
  if(page){await page.screenshot({path:`${root}/failure.png`}).catch(()=>{});await fs.writeFile(`${root}/failure-private.txt`,await page.locator('body').innerText().catch(()=>''),{mode:0o600});}
  process.exitCode=2;
}finally{
  if(context)await context.close();
  result.browser_closed=true;await save('browser-result',result);
  console.log(JSON.stringify({status:result.status,error:result.error}));
}
