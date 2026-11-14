// Actual Full-HD owner interactions. No response mocking or model-output repair.
import {chromium} from 'playwright';
import fs from 'node:fs/promises';
import os from 'node:os';
const root=`${os.homedir()}/.cache/proscenium-development/m09-positive-20261114`;
const action=process.argv[2];
const fixture=JSON.parse(await fs.readFile(`${root}/fixture.json`,'utf8'));
const prefix=`/api/v1/projects/${fixture.project.project_id}`;
const browser=await chromium.connectOverCDP('http://127.0.0.1:18920');
const page=browser.contexts()[0].pages()[0];
await page.setViewportSize({width:1920,height:1080});
page.setDefaultTimeout(20000);
const traffic=[];
page.on('response',async r=>{
  if(!r.url().includes('/api/v1/')||r.url().includes('/owner/session')||!(r.headers()['content-type']??'').includes('application/json'))return;
  try {traffic.push({at:Date.now(),path:new URL(r.url()).pathname,status:r.status(),method:r.request().method(),request:r.request().postDataJSON(),response:await r.json()});}catch{}
});
const get=endpoint=>page.evaluate(async endpoint=>{const r=await fetch(endpoint);if(!r.ok)throw Error(`${endpoint}: ${r.status}`);return r.json();},endpoint);
async function reviewSettled(id){
  const until=Date.now()+140000;
  while(Date.now()<until){const t=(await get(`${prefix}/reviews`)).items.find(t=>t.review_id===id);if(t&&['ready','failed'].includes(t.status))return t;await page.waitForTimeout(300);}
  throw Error('Review deadline exceeded. Reconcile original ID, never submit a replacement.');
}
async function rootSettled(id){
  const until=Date.now()+180000;
  while(Date.now()<until){const r=(await get(`${prefix}/requests`)).items.find(r=>r.request_id===id);if(r&&['completed','failed','stopped','outcome_uncertain'].includes(r.receipt.state))return r;await page.waitForTimeout(300);}
  throw Error('Owned cloud deadline exceeded. The SDK guard must abort only that owned request.');
}
async function openReview(id){
  await page.getByRole('button',{name:'Critic',exact:true}).click();
  await page.getByRole('button',{name:'Load existing reviews',exact:true}).click();
  await page.getByLabel('Review',{exact:true}).selectOption(id);
}
async function records(){
  const result={};
  for(const name of ['snapshot','requests','jobs','reviews','items','annotations'])result[name]=await get(`${prefix}/${name}`);
  result.findings={};for(const t of result.reviews.items)result.findings[t.review_id]=await get(`${prefix}/reviews/${t.review_id}/findings`);
  result.contexts={};for(const r of result.requests.items)if(r.submission.context_id)result.contexts[r.submission.context_id]=await get(`${prefix}/contexts/${r.submission.context_id}`);
  result.attempts={};for(const j of result.jobs.items)result.attempts[j.job_id]=await get(`${prefix}/jobs/${j.job_id}/attempts`);
  result.events=[];let after=0;for(let i=0;i<100;i++){const p=await get(`${prefix}/events?after=${after}&limit=100`);result.events.push(...p.events);if(p.next_after>=p.last_available)break;if(p.next_after<=after)throw Error('Non-advancing event cursor');after=p.next_after;}
  return result;
}
let result={action};
try{
  if(action==='owner'){
    await page.goto((await fs.readFile(`${root}/pair.private`,'utf8')).trim());
    const paired=page.waitForResponse(r=>r.url().endsWith('/api/v1/owner/session')&&r.request().method()==='POST');
    await page.getByRole('button',{name:'Connect this browser'}).click();
    const session=await(await paired).json();await fs.writeFile(`${root}/csrf.private`,session.csrf_token,{mode:0o600});
    await page.getByRole('button').filter({has:page.locator('small',{hasText:/^m09-image-version$/})}).click();
    await page.getByRole('button',{name:'Critic',exact:true}).click();
    await page.getByLabel('Review criteria').fill('Return exactly TWO distinct concise visual findings. First describe the blue object on the right. Second describe the orange object on the left. Each observation and recommendation under 10 words. No inferred measurements. No reasoning text. Structured findings only.');
    const reply=page.waitForResponse(r=>r.url().endsWith(`${prefix}/reviews`)&&r.request().method()==='POST');
    await page.getByRole('button',{name:'Request visual review',exact:true}).click();
    const response=await reply;result.ticket=await response.json();result.post_status=response.status();
    if(!response.ok())throw Error(JSON.stringify(result.ticket));
    result.settled=await reviewSettled(result.ticket.review_id);
    result.state=await records();
    if(result.settled.status!=='ready')throw Error('Owner actual VLM result failed. Preserve original receipt.');
    await openReview(result.ticket.review_id);
    await page.getByRole('region',{name:'Critic',exact:true}).locator('article').first().waitFor();
  }else if(action==='native'){
    if((await get(`${prefix}/requests`)).items.length)throw Error('Native root already admitted; no replacement send.');
    await page.getByRole('button',{name:'Copilot',exact:true}).click();
    await page.getByRole('textbox',{name:'Message Copilot'}).fill(await fs.readFile(`${root}/native-review-prompt.txt`,'utf8'));
    const reply=page.waitForResponse(r=>r.url().endsWith(`${prefix}/requests`)&&r.request().method()==='POST');
    await page.getByRole('button',{name:'Queue message',exact:true}).click();
    result.admission=await(await reply).json();result.request=await rootSettled(result.admission.request_id);
    result.ticket=await reviewSettled('m09-positive-native-review');result.state=await records();
    if(result.ticket.status!=='ready')throw Error('Native actual VLM result failed; do not repair findings.');
    await openReview(result.ticket.review_id);
    await page.getByRole('region',{name:'Critic',exact:true}).locator('article').first().waitFor();
  }else if(action==='approve'){
    if((await get(`${prefix}/requests`)).items.length!==1)throw Error('Approved handoff already admitted or unexpected root count.');
    await openReview('m09-positive-native-review');
    const findings=(await get(`${prefix}/reviews/m09-positive-native-review/findings`)).items;
    const selected=findings.find(f=>f.evidence.some(e=>e.kind==='visual'));
    const declined=findings.find(f=>f.finding_id!==selected?.finding_id&&f.evidence.some(e=>e.kind==='measured'));
    if(!selected||!declined)throw Error('No real distinct visual/measured findings available for subset approval.');
    const critic=page.getByRole('region',{name:'Critic',exact:true});
    await critic.locator('article').filter({has:page.locator('p',{hasText:selected.observation})}).getByRole('checkbox',{name:'Include finding'}).check();
    const amended=`M09_POSITIVE_OWNER_AMENDMENT. Read-only acknowledgement only. Do not delegate, edit files, run Blender/Unity/generation, request another review or inspect unapproved findings. Read the host approved_findings, exact target and attached clean image. Reply APPROVED_FINDING_RECEIVED with this finding_id, finding_revision and displayed_sha256, then describe only the blue object. Owner-selected finding: ${selected.finding_id}; revision ${selected.revision}.`;
    await page.getByLabel(`Approved instruction ${selected.finding_id}`,{exact:true}).fill(amended);
    const rejection=page.waitForResponse(r=>r.url().includes(`/findings/${declined.finding_id}/reject`)&&r.request().method()==='POST');
    await critic.locator('article').filter({has:page.locator('p',{hasText:declined.observation})}).getByRole('button',{name:'Reject finding',exact:true}).click();
    result.rejection=await(await rejection).json();result.selected=selected;result.declined=declined;result.amended=amended;
    await page.screenshot({path:`${root}/approval-before-queue.png`});
    const reply=page.waitForResponse(r=>r.url().endsWith(`${prefix}/requests`)&&r.request().method()==='POST');
    await page.getByRole('button',{name:'Queue selected findings once',exact:true}).click();
    const response=await reply;result.original_submission=response.request().postDataJSON();result.admission=await response.json();result.post_status=response.status();
    result.request=await rootSettled(result.admission.request_id);
    if(result.request.receipt.state!=='completed')throw Error('Approved native handoff did not complete');
    const csrf=await fs.readFile(`${root}/csrf.private`,'utf8');
    result.replay=await page.evaluate(async({endpoint,body,csrf})=>{const r=await fetch(endpoint,{method:'POST',headers:{'Content-Type':'application/json','Idempotency-Key':body.request_id,'X-CSRF-Token':csrf},body:JSON.stringify(body)});return{status:r.status,receipt:await r.json()};},{endpoint:`${prefix}/requests`,body:result.original_submission,csrf});
    result.state=await records();await openReview('m09-positive-native-review');
  }else if(action==='unavailable'){
    await page.getByRole('button',{name:'Critic',exact:true}).click();
    await page.getByLabel('Review criteria').fill('Explicit unavailable-model check after owned Gemma cleanup. Do not load any model.');
    const reply=page.waitForResponse(r=>r.url().endsWith(`${prefix}/reviews`)&&r.request().method()==='POST');
    await page.getByRole('button',{name:'Request visual review',exact:true}).click();
    result.ticket=await(await reply).json();result.settled=await reviewSettled(result.ticket.review_id);
    await page.getByRole('button',{name:'Annotate presented frame',exact:true}).click();
    await page.getByLabel('Feedback',{exact:true}).fill('M09 positive manual owner feedback after unavailable VLM. This is not a VLM finding and is not queued.');
    const saved=page.waitForResponse(r=>r.url().endsWith(`${prefix}/annotations`)&&r.request().method()==='POST');
    await page.getByRole('button',{name:'Save annotation',exact:true}).click();
    result.annotation_ack=await(await saved).json();result.state=await records();
  }else if(action==='collect'){
    result.state=await records();
  }else throw Error('Unknown action');
}catch(error){result.error=String(error);process.exitCode=1;}
result.viewport=await page.evaluate(()=>({width:innerWidth,height:innerHeight,dpr:devicePixelRatio}));result.browser=browser.version();
result.images=await page.locator('.review-image img,.review-canvas img').evaluateAll(nodes=>nodes.map(i=>({complete:i.complete,width:i.naturalWidth,height:i.naturalHeight})));
await page.screenshot({path:`${root}/${action}.png`});
await fs.writeFile(`${root}/${action}.json`,JSON.stringify(result,null,2));await fs.writeFile(`${root}/${action}-traffic.json`,JSON.stringify(traffic,null,2));
console.log(JSON.stringify({action,error:result.error,ticket:result.settled??result.ticket,request:result.request?.receipt,replay:result.replay,viewport:result.viewport},null,2));
await browser.close();
