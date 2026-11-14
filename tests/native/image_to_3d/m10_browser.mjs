// Scripted test-user input. No API interception, fake GLB mapping or model-authored claims.
import { chromium, expect } from '@playwright/test';
import fs from 'node:fs/promises';
import { createHash } from 'node:crypto';

const root=process.argv[2], action=process.argv[3];
const info=JSON.parse(await fs.readFile(`${root}/processes.json`,'utf8'));
const browser=await chromium.connectOverCDP(`http://127.0.0.1:${info.cdp}`);
const context=browser.contexts()[0], page=context.pages()[0];
await page.setViewportSize({width:1920,height:1080});
page.setDefaultTimeout(30000);
const viewport=()=>page.evaluate(()=>({width:innerWidth,height:innerHeight,dpr:devicePixelRatio}));
const digest=data=>createHash('sha256').update(data).digest('hex');
const versionButton=id=>page.getByRole('button').filter({has:page.locator('small',{hasText:new RegExp(`^${id}$`)})});
async function get(path){return page.evaluate(async p=>{const r=await fetch(p);if(!r.ok)throw new Error(`${p} ${r.status}`);return r.json();},path);}
const pid=JSON.parse(await fs.readFile(`${root}/fixture.json`,'utf8')).project.project_id;
const prefix=`/api/v1/projects/${pid}`;
async function activate(){
  if(await page.getByRole('button',{name:'Choose a project',exact:true}).isVisible()){
    await page.getByRole('button',{name:'Choose a project',exact:true}).click();
    await page.getByLabel('Saved project',{exact:true}).selectOption(pid);
    await page.getByRole('button',{name:'Activate selected project',exact:true}).click();
    await page.getByRole('textbox',{name:'Message Copilot'}).waitFor();
    if(await page.getByRole('button',{name:'Close projects',exact:true}).isVisible())
      await page.getByRole('button',{name:'Close projects',exact:true}).click();
  }
  if(await page.getByLabel('Resume mode').isVisible()){
    await page.getByLabel('Resume mode').selectOption('release_held_queue');
    await page.getByRole('button',{name:'Resume',exact:true}).click();
    await expect(page.getByRole('button',{name:'Resume',exact:true})).toHaveCount(0);
  }
  await page.getByRole('button',{name:'Assets',exact:true}).click();
  await versionButton('m10-asset-v1').click();
}
if(action==='setup'){
  const log=await fs.readFile(`${root}/server.private.log`,'utf8');
  const match=log.match(/http:\/\/127\.0\.0\.1:\d+\/#pair=[^\s]+/);
  if(!match)throw new Error('No private pairing URL. Do not start a duplicate host.');
  await page.goto(match[0]);
  await page.getByRole('button',{name:'Connect this browser'}).click();
  await activate();
  await page.screenshot({path:`${root}/m10-initial-fullhd.png`});
} else if(action==='activate'){
  await activate();await page.screenshot({path:`${root}/m10-initial-fullhd.png`});
} else if(action==='inspect'){
  const version=process.argv[4]??'m10-asset-v1';
  await page.getByRole('button',{name:'Assets',exact:true}).click();
  await versionButton(version).click();
  await page.getByRole('button',{name:'1 3D Inspection',exact:true}).click();
  await expect(page.getByRole('button',{name:'Render this view',exact:true})).toBeEnabled();
  const canvas=page.locator('.review-proxy canvas');
  const renderer=await canvas.evaluate(element=>{
    const gl=element.getContext('webgl2');const ext=gl.getExtension('WEBGL_debug_renderer_info');
    return ext?gl.getParameter(ext.UNMASKED_RENDERER_WEBGL):'unknown';
  });
  if(!renderer.includes('SwiftShader'))throw new Error(`Not software WebGL: ${renderer}`);
  if(version==='m10-asset-v2'){
    const zoomBox=await canvas.boundingBox();
    await page.mouse.move(zoomBox.x+zoomBox.width*.5,zoomBox.y+zoomBox.height*.5);
    for(let i=0;i<50;i++)await page.mouse.wheel(0,-100);
  }
  const before=await canvas.screenshot({path:`${root}/${version}-before-orbit.png`});
  const box=await canvas.boundingBox();
  await page.mouse.move(box.x+box.width*.72,box.y+box.height*.52);await page.mouse.down();
  await page.mouse.move(box.x+box.width*.72+65,box.y+box.height*.52+27,{steps:8});await page.mouse.up();
  const after=await canvas.screenshot({path:`${root}/${version}-after-orbit.png`});
  if(digest(before)===digest(after))throw new Error('Orbit produced identical actual canvas pixels');
  await page.getByRole('button',{name:'Outliner',exact:true}).click();
  const node=page.getByRole('button').filter({has:page.locator('small',{hasText:/^e04-instance-001$/})});
  await expect(node).toHaveAttribute('title','e04-object-001 / e04-instance-001');
  // Pick real triangles in the actual canvas. Never click the outliner to fake a mesh pick.
  let picked=false;
  for(const [x,y] of [[.5,.5],[.47,.53],[.53,.47],[.5,.57]]){
    await page.mouse.click(box.x+box.width*x,box.y+box.height*y);
    if(await node.getAttribute('aria-pressed')==='true'){picked=true;break;}
  }
  if(!picked)throw new Error('Actual mesh pick did not select its stable object/instance');
  const evidence={viewport:await viewport(),renderer,version,orbit_before_sha256:digest(before),
    orbit_after_sha256:digest(after),selected_title:await node.getAttribute('title'),
    selected_aria_pressed:await node.getAttribute('aria-pressed'),mapping:await get(`${prefix}/versions/${version}/proxy-mapping`)};
  await fs.writeFile(`${root}/${version}-inspection.json`,JSON.stringify(evidence,null,2));
  await page.screenshot({path:`${root}/${version}-fullhd-inspection.png`});
  console.log(JSON.stringify(evidence));
} else if(action==='send'){
  const prompt=await fs.readFile(`${root}/prompt.txt`,'utf8');
  const requests=(await get(`${prefix}/requests`)).items;
  if(requests.some(r=>r.submission.text===prompt))throw new Error('Original request already admitted. Reconcile it.');
  if(requests.filter(r=>r.submission.type==='queue').length>=2)throw new Error('Two cloud roots exhausted');
  const recovering=process.argv[4]==='confirmed-context-recovery';
  if(recovering){
    const rejection=JSON.parse(await fs.readFile(`${root}/context-rejection.json`,'utf8'));
    if(rejection.status!==409||rejection.response.error.code!=='source_conflict'||requests.length)throw new Error('No definitive unadmitted context recovery authority');
  }
  await fs.writeFile(`${root}/send-${digest(prompt)}${recovering?'-context-recovered':''}.json`,JSON.stringify({state:'prepared',prompt_sha256:digest(prompt),confirmed_context_recovery:recovering}),{flag:'wx',mode:0o600});
  await page.getByRole('textbox',{name:'Message Copilot'}).fill(prompt);
  const admission=page.waitForResponse(r=>r.request().method()==='POST'&&(
    new URL(r.url()).pathname===`${prefix}/requests`||
    (new URL(r.url()).pathname===`${prefix}/contexts`&&r.status()>=400)));
  await page.getByRole('button',{name:'Queue message',exact:true}).click();
  const response=await admission;
  const receipt={status:response.status(),path:new URL(response.url()).pathname,body:await response.json()};
  await fs.writeFile(`${root}/browser-send-receipt.json`,JSON.stringify(receipt,null,2));
  if(response.status()>=400)throw new Error(`Definitive browser admission error: ${response.status()} ${JSON.stringify(receipt.body)}`);
} else if(action==='reproduce-context-rejection'){
  const before=await get(`${prefix}/requests`);
  const responsePromise=page.waitForResponse(r=>r.request().method()==='POST'&&new URL(r.url()).pathname===`${prefix}/contexts`);
  await page.getByRole('button',{name:'Queue message',exact:true}).click();
  const response=await responsePromise;
  const body=await response.json();
  const after=await get(`${prefix}/requests`);
  const evidence={status:response.status(),response:body,context:response.request().postDataJSON(),
    requests_before:before.items.map(r=>r.request_id),requests_after:after.items.map(r=>r.request_id),viewport:await viewport()};
  await fs.writeFile(`${root}/context-rejection.json`,JSON.stringify(evidence,null,2));
  if(response.status()!==409||body.error.code!=='source_conflict'||after.items.length)throw new Error('Not the expected definitive pre-admission failure');
  await page.screenshot({path:`${root}/m10-selected-object-context-rejected.png`});
  console.log(JSON.stringify({status:response.status(),code:body.error.code,request_count:after.items.length}));
} else if(action==='clear-object-selection'){
  await page.getByRole('button',{name:'Outliner',exact:true}).click();
  const node=page.getByRole('button').filter({has:page.locator('small',{hasText:/^e04-instance-001$/})});
  if(await node.getAttribute('aria-pressed')==='true')await node.click({modifiers:['Control']});
  await expect(node).toHaveAttribute('aria-pressed','false');
  console.log(JSON.stringify({explicit_ui_deselection:true,version:'m10-asset-v1'}));
} else if(action==='stop'){
  await page.getByRole('button',{name:'Stop',exact:true}).click();
} else if(action==='version'){
  await page.getByRole('button',{name:'Assets',exact:true}).click();
  await versionButton(process.argv[4]).click();
  await page.getByRole('button',{name:'2 Keyframes',exact:true}).click();
  await page.locator('.review-image img').waitFor();
  await page.screenshot({path:`${root}/${process.argv[4]}-fullhd-keyframe.png`});
} else if(action==='collect'){
  const snapshot=await get(`${prefix}/snapshot`),requests=await get(`${prefix}/requests`),jobs=await get(`${prefix}/jobs`),items=await get(`${prefix}/items`);
  const result={viewport:await viewport(),browser:browser.version(),snapshot,requests,jobs,items,versions:{},contexts:{},frames:{}};
  for(const item of items.items)result.versions[item.item_id]=await get(`${prefix}/items/${item.item_id}/versions`);
  for(const request of requests.items)if(request.submission.context_id)result.contexts[request.submission.context_id]=await get(`${prefix}/contexts/${request.submission.context_id}`);
  for(const job of jobs.items)result.frames[job.job_id]=await get(`${prefix}/jobs/${job.job_id}/frames`);
  await fs.writeFile(`${root}/public-${process.argv[4]??'state'}.json`,JSON.stringify(result,null,2));
  await page.screenshot({path:`${root}/m10-${process.argv[4]??'state'}-fullhd.png`});
  console.log(JSON.stringify({viewport:result.viewport,requests:requests.items.map(r=>({id:r.request_id,state:r.receipt.state})),jobs:jobs.items.map(j=>({id:j.job_id,state:j.state}))}));
} else if(action==='body'){
  console.log((await page.locator('body').innerText()).slice(-18000));
} else {throw new Error('Unknown action');}
console.log(JSON.stringify({action,viewport:await viewport()}));
await browser.close();
