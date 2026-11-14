// Scripted owner input is a fixture. The app, SDK, model and native effects are real.
import { chromium } from 'playwright';
import fs from 'node:fs/promises';
import { createHash } from 'node:crypto';
const root = process.argv[2];
const action = process.argv[3];
const info = JSON.parse(await fs.readFile(`${root}/processes.json`, 'utf8'));
const browser = await chromium.connectOverCDP(`http://127.0.0.1:${info.cdp}`);
const context = browser.contexts()[0];
const page = context.pages()[0];
await page.setViewportSize({width:1920,height:1080});
page.setDefaultTimeout(15000);
if (action === 'setup') {
  await page.setViewportSize({width:1920,height:1080});
  const log = await fs.readFile(`${root}/server.private.log`, 'utf8');
  const url = log.match(/http:\/\/127\.0\.0\.1:\d+\/#pair=[^\s]+/)[0];
  await page.goto(url);
  await page.getByRole('button', {name:'Connect this browser'}).click();
  await page.getByRole('button', {name:'Choose a project'}).click();
  await page.getByLabel('Workstation folder path').fill(`${root}/project`);
  await page.getByLabel('New project name').fill('M01 real authoring fixture');
  await page.getByRole('button', {name:'Create project', exact:true}).click();
  await page.getByRole('button', {name:'Activate selected project'}).click();
  await page.getByRole('button', {name:'Close projects'}).click();
} else if (action === 'send') {
  const prompt=await fs.readFile(`${root}/prompt.txt`, 'utf8');
  const admitted=await page.evaluate(async()=>{
    const projects=await (await fetch('/api/v1/projects')).json();
    return (await (await fetch(`/api/v1/projects/${projects.items[0].project_id}/requests`)).json()).items;
  });
  if(admitted.some(r=>r.submission.text===prompt)) throw new Error('Original prompt already admitted. Inspect it, never send a replacement.');
  if(admitted.length>=4) throw new Error('Cloud root allocation exhausted');
  const digest=createHash('sha256').update(prompt).digest('hex');
  await fs.writeFile(`${root}/send-${digest}.json`,JSON.stringify({state:'prepared',prompt_sha256:digest}),{flag:'wx',mode:0o600});
  await page.getByRole('textbox', {name:'Message Copilot'}).fill(prompt);
  await page.getByRole('button', {name:'Queue message', exact:true}).click();
} else if (action === 'eval') {
  const result = await page.evaluate(await fs.readFile(`${root}/eval.js`, 'utf8'));
  console.log(JSON.stringify(result));
} else if (action === 'click') {
  await page.getByRole('button', {name:process.argv[4], exact:true}).click();
} else if (action === 'view') {
  await page.getByRole('button', {name:'Assets', exact:true}).click();
  await page.getByRole('button').filter({has:page.locator('small', {hasText:new RegExp(`^${process.argv[4]}$`)})}).click();
} else if (action === 'annotate') {
  const point=JSON.parse(await fs.readFile(`${root}/blue-point.json`,'utf8'));
  await page.getByRole('button',{name:'Annotate presented frame',exact:true}).click();
  await page.getByRole('button',{name:'pin',exact:true}).click();
  await page.getByLabel('Pin text',{exact:true}).fill('Make this blue cube taller');
  const box=await page.locator('.review-canvas').boundingBox();
  if(!box) throw new Error('No actual image');
  const scale=Math.min(box.width/640,box.height/360);
  await page.mouse.click(box.x+(box.width-640*scale)/2+640*scale*point.x,box.y+(box.height-360*scale)/2+360*scale*point.y);
  await page.getByLabel('Feedback',{exact:true}).fill('Make the pinned blue cube twice as tall, keep its base on the floor and preserve the orange cube.');
  // Deliberately do not Save here. Main-chat Send must invoke the registered preparer.
} else if (action === 'correct-pin') {
  await page.getByRole('button',{name:'eraser',exact:true}).click();
  const circle=await page.locator('.review-canvas svg circle').first().boundingBox();
  await page.mouse.click(circle.x+circle.width/2,circle.y+circle.height/2);
  await page.getByRole('button',{name:'pin',exact:true}).click();
  const point=JSON.parse(await fs.readFile(`${root}/blue-point.json`,'utf8'));
  const box=await page.locator('.review-canvas').boundingBox();
  const scale=Math.min(box.width/640,box.height/360);
  await page.mouse.click(box.x+(box.width-640*scale)/2+640*scale*point.x,box.y+(box.height-360*scale)/2+360*scale*point.y);
} else if (action === 'erase-first-pin') {
  await page.getByRole('button',{name:'eraser',exact:true}).click();
  await page.getByLabel('Pin text',{exact:true}).waitFor({state:'hidden'});
  const circle=await page.locator('.review-canvas svg circle').first().boundingBox();
  await page.mouse.click(circle.x+circle.width/2,circle.y+circle.height/2);
  await page.getByRole('button',{name:'pin',exact:true}).click();
} else if (action === 'restore-annotation') {
  await page.getByText('Saved annotations and local drafts',{exact:true}).click();
  await page.getByRole('button',{name:/Make the pinned blue cube twice as tall.*saved/}).click();
} else if (action === 'reload') {
  await page.reload();
  await page.getByRole('textbox',{name:'Message Copilot'}).waitFor();
} else if (action === 'select') {
  await page.locator('.review-catalogue article').filter({has:page.locator('small',{hasText:new RegExp(`^${process.argv[4]}$`)})}).getByRole('button',{name:'Select for item',exact:true}).click();
} else if (action === 'collect') {
  const result=await page.evaluate(async()=>{
    const get=async path=>{const r=await fetch(path);if(!r.ok)throw new Error(`${path}: ${r.status}`);return r.json();};
    const projects=await get('/api/v1/projects');
    const project=projects.items[0]; const prefix=`/api/v1/projects/${project.project_id}`;
    const result={projects,snapshot:await get(`${prefix}/snapshot`),requests:await get(`${prefix}/requests`),jobs:await get(`${prefix}/jobs`),items:await get(`${prefix}/items`),annotations:await get(`${prefix}/annotations`),selections:await get(`${prefix}/selections`)};
    result.versions={};for(const item of result.items.items){result.versions[item.item_id]=await get(`${prefix}/items/${item.item_id}/versions`);}
    result.frames={};for(const job of result.jobs.items){result.frames[job.job_id]=await get(`${prefix}/jobs/${job.job_id}/frames`);}
    result.contexts={};for(const request of result.requests.items){const id=request.submission.context_id;if(id)result.contexts[id]=await get(`${prefix}/contexts/${id}`);}
    const ids=new Set(result.jobs.items.flatMap(j=>j.artifact_ids));
    for(const a of result.annotations.items){ids.add(a.target.artifact_id);for(const m of a.markups){if(m.mask_artifact_id)ids.add(m.mask_artifact_id);}if(a.markup_artifact_id)ids.add(a.markup_artifact_id);}
    for(const ctx of Object.values(result.contexts))for(const a of ctx.attachments)ids.add(a.artifact_id);
    result.artifacts={};for(const id of ids){result.artifacts[id]=await get(`${prefix}/artifacts/${id}`);}
    return result;
  });
  await fs.writeFile(`${root}/public-${process.argv[4]??'state'}.json`,JSON.stringify(result,null,2));
  console.log(JSON.stringify({jobs:result.jobs.items.map(j=>({id:j.job_id,state:j.state,error:j.error_code})),viewport:await page.evaluate(()=>({width:innerWidth,height:innerHeight,dpr:devicePixelRatio})),browser:browser.version()}));
}
await page.screenshot({path:`${root}/${action}.png`, fullPage:true});
console.log((await page.locator('body').innerText()).slice(-18000));
await browser.close();
