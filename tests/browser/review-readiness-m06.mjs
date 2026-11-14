/** One <=300 s backend+Full-HD Chromium proof on an immutable-source M06 copy.
 * No routes are intercepted. No native session/history is restored into an SDK.
 */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import { createHash } from 'node:crypto';
import { spawn, execFileSync } from 'node:child_process';
import { chromium } from '@playwright/test';

const repo=process.cwd();
const retained='/home/quintin/.cache/proscenium-development/milestones/m06-20261114T161211';
const root=await fs.mkdtemp(path.join(os.homedir(),'.cache/proscenium-development/milestones/m06-readiness-'));
await fs.chmod(root,0o700);await fs.mkdir(`${root}/profiles`);process.env.TMPDIR=`${root}/profiles`;
const sha=bytes=>createHash('sha256').update(bytes).digest('hex');
async function manifest(directory){
  const result={};
  async function walk(current){for(const entry of await fs.readdir(current,{withFileTypes:true})){
    const file=path.join(current,entry.name),relative=path.relative(directory,file);
    if(entry.isDirectory())await walk(file);else if(entry.isSymbolicLink())result[relative]=`symlink:${await fs.readlink(file)}`;else result[relative]=sha(await fs.readFile(file));
  }}await walk(directory);return result;
}
const original=await manifest(`${retained}/project`);
const originalMachine=await manifest(`${retained}/machine`);
const failedManifest=await fs.readFile('docs/evidence/m06/known-review-defect.json');
const originalCleanup=JSON.parse(await fs.readFile(`${retained}/cleanup.json`,'utf8'));
assert.equal(originalCleanup.native.live_original,false);assert.equal(originalCleanup.server.reaped,true);
await fs.cp(`${retained}/project`,`${root}/project`,{recursive:true,dereference:false});
// Rebase only copy-internal symlinks. Reject references outside the copied scope.
async function scopeLinks(directory){for(const entry of await fs.readdir(directory,{withFileTypes:true})){
  const file=path.join(directory,entry.name);
  if(entry.isDirectory())await scopeLinks(file);
  else if(entry.isSymbolicLink()){
    const target=await fs.readlink(file);if(path.isAbsolute(target)){assert.ok(target.startsWith(`${retained}/project/`));await fs.unlink(file);await fs.symlink(`${root}/project/${path.relative(`${retained}/project`,target)}`,file);}
    assert.ok((await fs.realpath(file)).startsWith(`${root}/project/`));
  }
}}await scopeLinks(`${root}/project`);
await fs.mkdir(`${root}/machine`,{mode:0o700});
await fs.copyFile(`${retained}/machine/registry.sqlite3`,`${root}/machine/registry.sqlite3`);
await fs.chmod(`${root}/machine/registry.sqlite3`,0o600);
execFileSync(path.join(repo,'.venv/bin/python'),['tests/browser/review-readiness-host.py',root,'--prepare'],{cwd:repo});
const registryProof=JSON.parse(await fs.readFile(`${root}/copy-registry-proof.json`,'utf8'));
assert.equal(registryProof.storage_revision_before,registryProof.domain_revision_before);
assert.equal(registryProof.storage_revision_after,registryProof.domain_revision_before+1);
assert.equal(registryProof.native_id_before,registryProof.native_id_after);
const gitBefore=execFileSync('git',['rev-parse','HEAD'],{cwd:`${root}/project`,encoding:'utf8'}).trim();
await fs.writeFile(`${root}/original-source-hashes.private.json`,JSON.stringify(original));
let server,browser,serverExit;let page;let outcome;
const started=Date.now();
const log=await fs.open(`${root}/server.private.log`,'wx',0o600);
try{
  server=spawn(path.join(repo,'.venv/bin/python'),['tests/browser/review-readiness-host.py',root],{cwd:repo,stdio:['ignore',log.fd,log.fd],env:{...process.env,TMPDIR:`${root}/profiles`}});
  serverExit=new Promise(resolve=>server.once('exit',(code,signal)=>resolve({code,signal})));
  const deadline=Date.now()+250000;
  let control;
  while(Date.now()<deadline){try{control=JSON.parse(await fs.readFile(`${root}/control.private.json`,'utf8'));const ready=await fetch(`${control.origin}/health`);if(ready.ok)break;}catch{}await new Promise(r=>setTimeout(r,150));if(server.exitCode!==null)throw new Error('Review backend exited before readiness');}
  assert.ok(control,'Backend readiness timeout');
  browser=await chromium.launch({headless:true,args:['--disable-gpu']});
  const context=await browser.newContext({viewport:{width:1920,height:1080},deviceScaleFactor:1});
  page=await context.newPage();page.setDefaultTimeout(15000);
  const mutations=[];page.on('request',request=>{if(!['GET','HEAD','OPTIONS'].includes(request.method()))mutations.push({method:request.method(),path:new URL(request.url()).pathname});});
  await page.goto(control.pairing_url);await page.getByRole('button',{name:'Connect this browser',exact:true}).click();
  const get=async route=>page.evaluate(async route=>{const response=await fetch(route);if(!response.ok)throw new Error(`${route}: ${response.status}`);return response.json();},route);
  const project=(await get('/api/v1/projects')).items[0],prefix=`/api/v1/projects/${project.project_id}`;
  assert.equal(project.path,`${root}/project`);
  const before={requests:await get(`${prefix}/requests`),jobs:await get(`${prefix}/jobs`),annotations:await get(`${prefix}/annotations`),selections:await get(`${prefix}/selections`)};
  assert.equal(before.requests.items.length,2);assert.equal(before.jobs.items.length,0);
  await page.getByRole('button',{name:'Shots',exact:true}).click();
  await page.getByLabel('Storyboard version',{exact:true}).selectOption('m06-kite-board-v1');
  const state=()=>page.evaluate(id=>JSON.parse(localStorage.getItem(`proscenium.owner.view.context.${id}`)??'null'),project.project_id);
  const frames=[['kite-rise','kite-rise-start'],['kite-rise','kite-rise-middle'],['kite-rise','kite-rise-end'],['kite-rest','kite-rest-start']];
  const observations=[];
  for(const [shot,frame]of frames){
    await page.locator('.sb-frame').filter({has:page.locator('strong',{hasText:frame})}).click();
    await page.getByRole('button',{name:'Annotate presented frame',exact:true}).waitFor();
    await page.waitForFunction(({id,shot,frame})=>{const value=JSON.parse(localStorage.getItem(`proscenium.owner.view.context.${id}`)??'null');return value?.target?.shot_id===shot&&value.attachments.some(a=>a.use?.includes(frame))&&!document.querySelector('.review-workspace nav button:nth-child(4)')?.disabled;},{id:project.project_id,shot,frame});
    assert.equal(await page.getByRole('button',{name:'Annotate presented frame',exact:true}).isEnabled(),true);
    const current=await state();assert.equal(current.target.shot_id,shot);assert.equal(current.target.artifact_id,'34bef859-a4a1-48e3-8749-834aeb958fa7');assert.ok(current.attachments[0].use.includes(frame));
    if(!observations.length)await page.locator('.review-image img').evaluate(image=>{window.readinessOriginalImage=image;window.readinessNewLoads=0;image.addEventListener('load',()=>window.readinessNewLoads++);});
    const decoded=await page.locator('.review-image img').evaluate(image=>({sameElement:image===window.readinessOriginalImage,newLoads:window.readinessNewLoads,currentSrc:image.currentSrc,complete:image.complete,width:image.naturalWidth,height:image.naturalHeight}));
    assert.equal(decoded.sameElement,true);assert.equal(decoded.newLoads,0);assert.equal(decoded.width,640);assert.equal(decoded.height,360);
    observations.push({shot,frame,context:current,decoded});
  }
  await page.screenshot({path:'docs/evidence/w05/readiness-m06-direct-transition.png'});
  await page.getByRole('button',{name:'Annotate presented frame',exact:true}).click();
  await page.getByRole('button',{name:'pin',exact:true}).click();await page.getByLabel('Pin text',{exact:true}).fill('M06 readiness kite-rest-start, not kite-rise-start');
  const box=await page.locator('.review-canvas').boundingBox();assert.ok(box);await page.mouse.click(box.x+box.width/2,box.y+box.height/2);
  const feedback='Readiness supplemental proof: kite-rest / kite-rest-start shared owner reference. Planning context only. No model or generation requested.';
  await page.getByLabel('Feedback',{exact:true}).fill(feedback);
  const receipt=page.waitForResponse(response=>response.request().method()==='POST'&&response.url().endsWith('/annotations'));
  await page.getByRole('button',{name:'Save annotation',exact:true}).click();const savedResponse=await receipt;assert.ok(savedResponse.ok(),await savedResponse.text());
  const after={requests:await get(`${prefix}/requests`),jobs:await get(`${prefix}/jobs`),annotations:await get(`${prefix}/annotations`),selections:await get(`${prefix}/selections`)};
  assert.deepEqual(after.requests,before.requests);assert.deepEqual(after.jobs,before.jobs);assert.deepEqual(after.selections,before.selections);
  const saved=after.annotations.items.find(value=>value.text===feedback);assert.ok(saved);assert.equal(saved.target.shot_id,'kite-rest');assert.equal(saved.target.artifact_id,observations[3].context.target.artifact_id);assert.equal(saved.saved_revision,1);assert.equal(saved.markups[0].kind,'pin');
  assert.equal(after.annotations.items.length,before.annotations.items.length+1);
  for(const old of before.annotations.items)assert.deepEqual(after.annotations.items.find(a=>a.annotation_id===old.annotation_id),old);
  await page.screenshot({path:'docs/evidence/w05/readiness-m06-saved-b.png'});
  outcome={status:'passed',criterion_2:'Direct shared-reference role/shot navigation and saved B annotation pass using retained real M06 plan/history. No new planning roots. Final criterion acceptance remains coordinator-owned.',root,original:retained,registryProof,project_id:project.project_id,browser:browser.version(),viewport:{width:1920,height:1080,dpr:1},observations,annotation:saved,receipt:await savedResponse.json(),request_count_before:before.requests.items.length,request_count_after:after.requests.items.length,jobs_before:before.jobs.items.length,jobs_after:after.jobs.items.length,old_annotations_unchanged:true,selections_unchanged:true,mutations};
}catch(error){outcome={status:'failed',root,error:String(error.stack??error)};if(page)await page.screenshot({path:'docs/evidence/w05/readiness-m06-failure.png'}).catch(()=>{});process.exitCode=1;}
finally{
  await browser?.close();
  if(server&&server.exitCode===null){server.kill('SIGTERM');await Promise.race([serverExit,new Promise(resolve=>setTimeout(resolve,15000))]);if(server.exitCode===null&&server.signalCode===null){server.kill('SIGKILL');await serverExit;}}
  await log.close();
  const elapsed=Date.now()-started;
  const audit=JSON.parse(await fs.readFile(`${root}/server-audit.json`,'utf8').catch(()=>'{"missing":true}'));
  const originalAfter=await manifest(`${retained}/project`);
  const originalUnchanged=JSON.stringify(originalAfter)===JSON.stringify(original);
  const originalMachineUnchanged=JSON.stringify(await manifest(`${retained}/machine`))===JSON.stringify(originalMachine);
  const failedManifestUnchanged=sha(await fs.readFile('docs/evidence/m06/known-review-defect.json'))===sha(failedManifest);
  const gitAfter=execFileSync('git',['rev-parse','HEAD'],{cwd:`${root}/project`,encoding:'utf8'}).trim();
  const cleanup={server_reaped:server?.exitCode!==null||server?.signalCode!==null,browser_closed:!browser?.isConnected(),elapsed_ms:elapsed,allocation_limit_ms:300000,original_project_unchanged:originalUnchanged,original_project_files:Object.keys(original).length,original_machine_and_native_history_unchanged:originalMachineUnchanged,old_failed_manifest_unchanged:failedManifestUnchanged,copied_git_head_before:gitBefore,copied_git_head_after:gitAfter,audit};
  if(!originalUnchanged||!originalMachineUnchanged||!failedManifestUnchanged||gitBefore!==gitAfter||elapsed>300000||audit.sdk_factories?.length||audit.native_calls?.length||audit.provider_http?.length||audit.blocked_mutations?.length||audit.copilot_delivery_task_started||audit.lifecycle_started||audit.missing){outcome.status='failed';process.exitCode=1;}
  await fs.writeFile('docs/evidence/w05/readiness-m06-result.json',JSON.stringify({...outcome,cleanup},null,2)+'\n');
  console.log(JSON.stringify({status:outcome.status,root,cleanup},null,2));
}
