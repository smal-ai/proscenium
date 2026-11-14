// Actual production app and retained real images. No route mocks or owner mutations.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {chromium} from 'playwright';

const root=process.argv[2];
const origin='http://127.0.0.1:18969';
const projectId='project-2d8c0aea-e3de-41cf-9c23-1d0108fd8bb8';
const prefix=`/api/v1/projects/${projectId}`;
const expected={initial:'07673e7a7cea364e56b52bb4bc255722678e0cbf20b1b2c714e819c5ab84806d',correction:'5c84752669807be5857f90ff13b31d6f0766b4e2313bea7030a5be793214e8df'};
const result={source:process.env.M07_REVIEW_SOURCE,views:[],errors:[],mutations:[]};
let browser;
try {
  for(let tries=0;;tries++) {
    try {const response=await fetch(`${origin}/health`);if(response.ok)break;}catch{}
    if(tries>150)throw Error('Own server did not become ready');
    await new Promise(resolve=>setTimeout(resolve,100));
  }
  browser=await chromium.launch({headless:true,args:['--disable-gpu']});
  const context=await browser.newContext({viewport:{width:1920,height:1080},deviceScaleFactor:1});
  const page=await context.newPage();page.setDefaultTimeout(15000);
  page.on('pageerror',error=>result.errors.push(String(error)));
  page.on('request',request=>{
    if(!['GET','HEAD'].includes(request.method()))result.mutations.push({path:new URL(request.url()).pathname,method:request.method()});
  });
  result.browser={version:browser.version(),executable:chromium.executablePath(),sha256:createHash('sha256').update(await fs.readFile(chromium.executablePath())).digest('hex')};
  await page.goto((await fs.readFile(`${root}/pair.private`,'utf8')).trim());
  const pair=page.waitForResponse(response=>response.url().endsWith('/api/v1/owner/session')&&response.request().method()==='POST');
  await page.getByRole('button',{name:'Connect this browser',exact:true}).click();assert.ok((await pair).ok());
  // Pairing can finish before the saved active-project snapshot. Its trigger's
  // accessible name then changes from the placeholder to the project name.
  await page.locator('button.project-trigger').click();
  await page.getByLabel('Saved project',{exact:true}).selectOption(projectId);
  const activation=page.waitForResponse(response=>response.url().endsWith(`${prefix}/activate`)&&response.request().method()==='POST');
  await page.getByRole('button',{name:'Activate selected project',exact:true}).click();assert.ok((await activation).ok());
  await page.getByRole('button',{name:'Close projects',exact:true}).click();
  const get=path=>page.evaluate(async path=>{const response=await fetch(path);if(!response.ok)throw Error(`${path}: ${response.status}`);return response.json();},path);
  result.before={jobs:await get(`${prefix}/jobs`),requests:await get(`${prefix}/requests`),selections:await get(`${prefix}/selections`)};
  await page.getByRole('button',{name:'Shots',exact:true}).click();
  await page.getByLabel('Storyboard version',{exact:true}).selectOption('m07-fp16-plan-v1');
  const frame=id=>page.locator('.sb-frame').filter({has:page.locator('strong',{hasText:id})});
  const selector=page.getByLabel('Shot / render job',{exact:true});
  const current=()=>page.evaluate(id=>JSON.parse(localStorage.getItem(`proscenium.owner.view.context.${id}`)??'null'),projectId);
  async function check(name,kind,job) {
    const id=kind==='reference'?'m07-unrelated-reference':`m07-fp16-${kind}-artifact-0`;
    await page.waitForFunction(({id,job,projectId})=>{
      const image=document.querySelector('.review-image img');
      const selector=document.querySelector('select[aria-label="Shot / render job"]');
      const context=JSON.parse(localStorage.getItem(`proscenium.owner.view.context.${projectId}`)??'null');
      return image?.complete&&image.naturalWidth===512&&image.getAttribute('src')?.includes(`/artifacts/${id}/content`)
        &&selector?.value===job&&context?.target?.artifact_id===id
        &&[...document.querySelectorAll('button')].some(button=>button.textContent==='Annotate presented frame'&&!button.disabled);
    },{id,job,projectId});
    const image=await page.locator('.review-image img').evaluate(image=>({src:image.getAttribute('src'),complete:image.complete,width:image.naturalWidth,height:image.naturalHeight}));
    const bytes=await page.evaluate(async url=>{
      const response=await fetch(url);if(!response.ok)throw Error(`Media ${response.status}`);
      const bytes=await response.arrayBuffer();
      return {bytes:bytes.byteLength,sha256:[...new Uint8Array(await crypto.subtle.digest('SHA-256',bytes))].map(b=>b.toString(16).padStart(2,'0')).join('')};
    },image.src);
    const captured=await current();
    assert.equal(captured.target.render_job_id,null);assert.equal(captured.target.source_frame,null);
    assert.equal(captured.target.displayed_sha256,bytes.sha256);
    if(kind!=='reference') {
      assert.equal(bytes.sha256,expected[kind]);assert.equal(captured.target.origin,'concept');
      assert.deepEqual(captured.target,{origin:'concept',artifact_id:id,displayed_sha256:expected[kind],dimensions:{width:512,height:512},
        item_id:'m07-fp16-storyboard',version_id:'m07-fp16-plan-v1',shot_id:'shot-mug',render_job_id:null,source_frame:null,media_artifact_id:null});
      assert.equal(captured.attachments.filter(attachment=>attachment.artifact_id===id&&attachment.role==='concept').length,1);
      assert.ok(!captured.attachments.some(attachment=>attachment.artifact_id.includes('input-')));
    }
    else {assert.equal(captured.target.origin,'reference');assert.equal(job,'');}
    const snapshot={schema_version:'1.0',context_id:`readback-${name}`,project_id:projectId,captured_at:new Date().toISOString(),...captured};
    const row={name,job:await selector.inputValue(),label:await selector.locator('option:checked').innerText(),image,content:bytes,context:captured,context_snapshot_ready:snapshot};
    result.views.push(row);
    await page.screenshot({path:`${root}/${name}.png`});
  }
  await frame('frame-mug').click();
  await check('01-saved-storyboard-correction','correction','m07-fp16-correction');
  await selector.selectOption('m07-fp16-initial');
  await check('02-dropdown-initial','initial','m07-fp16-initial');
  // The actual failing route: external storyboard navigation changes the image.
  await frame('frame-mug').click();
  await check('03-storyboard-initial-to-correction','correction','m07-fp16-correction');
  await selector.selectOption('m07-fp16-initial');
  await check('04-dropdown-initial-again','initial','m07-fp16-initial');
  await selector.selectOption('m07-fp16-correction');
  await check('05-dropdown-correction','correction','m07-fp16-correction');
  await frame('frame-unrelated').click();
  await check('06-unassociated-reference','reference','');
  await page.reload();await page.getByRole('button',{name:'Shots',exact:true}).click();
  await frame('frame-mug').click();await check('07-reloaded-owner-correction','correction','m07-fp16-correction');
  result.after={jobs:await get(`${prefix}/jobs`),requests:await get(`${prefix}/requests`),selections:await get(`${prefix}/selections`)};
  assert.deepEqual(result.after,result.before);
  assert.equal(result.errors.length,0);
  assert.deepEqual(result.mutations.map(row=>row.path),['/api/v1/owner/session',`${prefix}/activate`]);
  result.viewport=await page.evaluate(()=>({width:innerWidth,height:innerHeight,dpr:devicePixelRatio}));
  assert.deepEqual(result.viewport,{width:1920,height:1080,dpr:1});
  result.pass=true;
} catch(error) {
  result.error=String(error);process.exitCode=1;
  const page=browser?.contexts()[0]?.pages()[0];
  if(page)await page.screenshot({path:`${root}/failed-readback.png`}).catch(()=>{});
  if(page&&process.env.M07_REVIEW_HOLD_FOR_INSPECTION==='1') {
    result.failed_phases=[{error:String(error),at:new Date().toISOString()}];
    await fs.writeFile(`${root}/unexpected-state.json`,JSON.stringify(result,null,2));
    await fs.writeFile(`${root}/unexpected-state.private.html`,await page.content());
    // Keep this exact browser and app alive for a bounded observer/selector
    // repair. A continuation has no extra authority beyond the read-only guard.
    const until=Date.now()+120000;
    while(Date.now()<until) {
      try {
        const code=await fs.readFile(`${root}/continue.mjs`);
        result.continuation_sha256=createHash('sha256').update(code).digest('hex');
        const continuation=await import(`file://${root}/continue.mjs`);
        await continuation.resume({browser,page,root,result});
        if(result.pass){delete result.error;process.exitCode=0;}
        break;
      } catch(continuationError) {
        if(continuationError.code!=='ENOENT'){result.continuation_error=String(continuationError);break;}
      }
      await new Promise(resolve=>setTimeout(resolve,200));
    }
  }
}
finally {if(browser)await browser.close();await fs.writeFile(`${root}/browser-result.json`,JSON.stringify(result,null,2)+'\n');}
