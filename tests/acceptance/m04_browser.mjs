// Scripted test-owner input. Actual compiled application, HTTP, SDK and renderer.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { chromium } from '@playwright/test';
import { Matrix4, Vector3 } from 'three';

const root = path.resolve(process.argv[2]);
const config = JSON.parse(await fs.readFile(`${root}/config.private.json`, 'utf8'));
const accepted = JSON.parse(await fs.readFile('docs/evidence/w05/camera-selection-dpr1.json', 'utf8'));
const journal = {label:'Scripted test-owner input, not a human creative decision', browser:null, operations:[], limits:{root_requests:1, root_timeout_seconds:180, native_frames:2}};
const context = await chromium.launchPersistentContext(`${root}/browser-profile`, {
  executablePath:path.join(os.homedir(),'.cache/ms-playwright/chromium-1255/chrome-linux64/chrome'),
  headless:true, viewport:{width:1920,height:1080}, deviceScaleFactor:1,
  args:['--use-angle=swiftshader','--enable-unsafe-swiftshader','--disable-background-networking'],
});
const page = context.pages()[0];
page.setDefaultTimeout(20000);
const write = (name, value) => fs.writeFile(`${root}/${name}`,JSON.stringify(value,null,2)+'\n');
const getState = async(final=false) => page.evaluate(async(final)=>{
  const response=await fetch(`/m04-harness/state?final=${final}`);
  if(!response.ok)throw new Error(`State ${response.status}`);
  return response.json();
},final);
async function poll(check, seconds, description) {
  const deadline=Date.now()+seconds*1000;
  while(Date.now()<deadline) {
    const state=await getState();
    await write('public-latest.json',state);
    assert.ok(state.requests.length<=1,'Reserved single cloud root was exceeded');
    if(check(state))return state;
    await page.waitForTimeout(200);
  }
  throw new Error(`Bounded wait expired: ${description}`);
}
function pixel(camera, point, width, height) {
  const clip=new Vector3(...point).applyMatrix4(new Matrix4().fromArray(camera.projection).transpose()
    .multiply(new Matrix4().fromArray(camera.camera_to_world).transpose().invert()));
  return {x:(clip.x+1)*width/2,y:(1-clip.y)*height/2};
}

try {
  journal.browser=context.browser().version();
  const pair=page.waitForResponse(response=>response.url().endsWith('/api/v1/owner/session')&&response.request().method()==='POST');
  await page.goto((await fs.readFile(`${root}/pairing.private.url`,'utf8')).trim());
  await page.getByRole('button',{name:'Connect this browser'}).click();
  const csrf=(await (await pair).json()).csrf_token;
  await page.getByRole('button',{name:'Assets',exact:true}).click();
  await page.getByRole('button').filter({has:page.locator('small',{hasText:/^fixture-v1$/})}).click();
  await page.getByRole('button',{name:'Outliner',exact:true}).click();
  await page.getByRole('button',{name:'1 3D Inspection',exact:true}).click();
  await page.getByRole('button',{name:'Render this view',exact:true}).waitFor();
  // Controlled fixture geometry matches accepted W05's fractional viewer rectangle.
  // This changes test layout only. No application camera/pixels/context is replaced.
  await page.addStyleTag({content:'.review-proxy{width:1031.5px!important;height:569.25px!important;flex:none!important;min-height:0!important}'});
  const canvas=page.locator('.review-proxy canvas');
  await page.waitForFunction(()=>{const c=document.querySelector('.review-proxy canvas');return c?.width===1031&&c?.height===569;});
  await page.waitForFunction(()=>[...document.querySelectorAll('button')].some(button=>button.textContent==='Render this view'&&!button.disabled));
  const rect=await canvas.boundingBox();
  // Repeat only the accepted W05 interaction, not its wider browser suite.
  const initial={...accepted.selection.camera,camera_to_world:[0.7071067811865476,-0.3481553119113957,0.6154574548966637,5,
    0.7071067811865475,0.3481553119113957,-0.6154574548966637,-5,
    0,0.8703882797784892,0.49236596391733095,4,0,0,0,1]};
  const pick=pixel(initial,[-1,0,1],rect.width,rect.height);
  await page.mouse.click(rect.x+pick.x,rect.y+pick.y);
  assert.equal(await page.getByTitle('object-cube-source / instance-renamed',{exact:true}).getAttribute('aria-pressed'),'true');
  await page.mouse.move(rect.x+rect.width*.8,rect.y+rect.height*.5);
  await page.mouse.down();
  await page.mouse.move(rect.x+rect.width*.8+65,rect.y+rect.height*.5+25,{steps:5});
  await page.mouse.up();
  await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
  journal.framebuffer=await canvas.evaluate(element=>({width:element.width,height:element.height,css:element.getBoundingClientRect().toJSON()}));
  const before=await getState();
  assert.equal(before.requests.length,0);
  assert.equal(before.jobs.filter(job=>job.render_spec?.purpose!=='proxy').length,0);
  await write('before-click.json',before);
  let cloudSlot;
  for(const slot of [1,2]) {
    try {const file=await fs.open(`${path.dirname(root)}/m04-camera-7b1f837-cloud-slot${slot}.json`,'wx',0o600);
      await file.writeFile(JSON.stringify({root,reserved_at:new Date().toISOString(),slot}));await file.close();cloudSlot=slot;break;
    } catch(error) {if(error.code!=='EEXIST')throw error;}
  }
  assert.ok(cloudSlot,'M04 total two-root allocation exhausted. Reconcile original roots.');
  journal.cloud_slot=cloudSlot;
  await fs.writeFile(`${root}/cloud-root-reservation.json`,JSON.stringify({reserved_at:new Date().toISOString(), action:'one actual Render this view click',limit:1}),{flag:'wx',mode:0o600});
  const started=Date.now();
  await page.getByRole('button',{name:'Render this view',exact:true}).click();
  journal.operations.push({operation:'actual Render this view',at:new Date().toISOString()});
  const first=await poll(state=>state.requests[0]?.receipt.state==='completed'
    &&state.jobs.some(job=>job.render_spec?.purpose==='keyframe'&&job.state==='succeeded'),180,'normal Queue/SDK/keyframe');
  journal.root_seconds=(Date.now()-started)/1000;
  const request=first.requests[0];
  assert.equal(request.submission.text,'Render this view');
  const captured=first.contexts.find(record=>record.context_id===request.submission.context_id);
  const job=first.jobs.find(record=>record.render_spec?.purpose==='keyframe');
  assert.deepEqual(captured.target.dimensions,accepted.intrinsic);
  assert.deepEqual(captured.camera,accepted.selection.camera);
  assert.deepEqual(captured.selected_objects,accepted.selection.selected_objects);
  assert.equal(captured.target.displayed_sha256,accepted.uploadSha256);
  assert.deepEqual(job.render_spec.camera,captured.camera);
  assert.deepEqual(job.render_spec.dimensions,captured.target.dimensions);
  assert.equal(job.render_spec.purpose,'keyframe');
  assert.equal(job.render_spec.samples,8);
  assert.equal(job.render_spec.device,'CPU');
  assert.equal(first.segments.filter(segment=>segment.job_id===job.job_id).length,0);
  await write('public-first-render.json',first);
  await write('captured-context.json',captured);
  await page.screenshot({path:`${root}/inspection-after-render.png`});

  await page.getByRole('button',{name:'2 Keyframes',exact:true}).click();
  await page.getByLabel('Shot / render job',{exact:true}).selectOption(job.job_id);
  await page.waitForFunction(()=>document.querySelector('.review-image img')?.naturalWidth===1031);
  await page.screenshot({path:`${root}/cycles-keyframe.png`});
  await page.getByRole('button',{name:'Annotate presented frame',exact:true}).click();
  await page.getByRole('button',{name:'pin',exact:true}).click();
  await page.getByLabel('Pin text',{exact:true}).fill('M04 retained source pin');
  const box=await page.locator('.review-canvas').boundingBox();
  const point=pixel(captured.camera,[-1,0,1],1031,569);
  const scale=Math.min(box.width/1031,box.height/569);
  await page.mouse.click(box.x+(box.width-1031*scale)/2+point.x*scale,box.y+(box.height-569*scale)/2+point.y*scale);
  await page.getByLabel('Feedback',{exact:true}).fill('M04 fixture annotation, pinned before the DPR2 harness render. No source edit requested.');
  await page.getByRole('button',{name:'Save annotation',exact:true}).click();
  const annotated=await poll(state=>state.annotations.length===1,15,'owner annotation save');
  const annotation=annotated.annotations[0];
  assert.equal(annotation.target.render_job_id,job.job_id);
  assert.equal(annotation.target.version_id,'fixture-v1');
  await write('annotation-before-dpr2.json',annotation);
  await page.screenshot({path:`${root}/pinned-before-dpr2.png`});
  const second=await page.evaluate(async(token)=>{
    const response=await fetch('/m04-harness/dpr2',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':token},body:'{}'});
    if(!response.ok)throw new Error(`DPR2 harness ${response.status}: ${await response.text()}`);
    return response.json();
  },csrf);
  journal.operations.push({operation:'authenticated explicit DPR2 harness call, NOT Queue/cloud',job_id:second.job_id,at:new Date().toISOString()});
  await poll(state=>state.jobs.find(job=>job.job_id===second.job_id)?.state==='succeeded',300,'DPR2 native keyframe');
  const final=await getState(true);
  assert.deepEqual(final.annotations[0],annotation);
  assert.equal(final.requests.length,1);
  assert.equal(final.native_child_events,0);
  const renderJobs=final.jobs.filter(job=>job.render_spec?.purpose==='keyframe');
  assert.equal(renderJobs.length,2);
  assert.deepEqual(renderJobs.find(job=>job.job_id===second.job_id).render_spec.dimensions,{width:2063,height:1138});
  await write('public-final.json',final);
  await page.screenshot({path:`${root}/pinned-after-dpr2.png`});
  await page.reload();
  await page.getByText('Saved annotations and local drafts',{exact:true}).click();
  await page.getByRole('button',{name:/M04 fixture annotation.*saved/}).click();
  await page.screenshot({path:`${root}/annotation-restored.png`});
  journal.status='browser_queue_sdk_native_and_pinned_annotation_pass';
} catch(error) {
  journal.status='failed';journal.error=String(error.stack??error);
  await page.screenshot({path:`${root}/failure.png`}).catch(()=>{});
  const state=await getState(true).catch(()=>null); if(state)await write('public-failure.json',state);
  throw error;
} finally {
  await context.close();
  journal.browser_closed=true;
  await write('browser-result.json',journal);
}
