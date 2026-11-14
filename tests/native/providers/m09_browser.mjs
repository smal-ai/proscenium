// Scripted test-user input. Real compiled UI, production API, LM Studio and SDK.
import { chromium } from 'playwright';
import fs from 'node:fs/promises';
import path from 'node:path';
const root=path.resolve('tests/native/providers/m09_runtime');
const action=process.argv[2];
const fixture=JSON.parse(await fs.readFile(`${root}/fixture.json`,'utf8'));
const projectId=fixture.project.project_id;
const prefix=`/api/v1/projects/${projectId}`;
const browser=await chromium.connectOverCDP('http://127.0.0.1:18910');
const page=browser.contexts()[0].pages()[0];
await page.setViewportSize({width:1920,height:1080});
page.setDefaultTimeout(20000);
const traffic=[];
page.on('response',async response=>{
  if(!response.url().includes('/api/v1/')||response.url().includes('/owner/session')||!(response.headers()['content-type']??'').includes('application/json'))return;
  try {traffic.push({at:Date.now(),path:new URL(response.url()).pathname,status:response.status(),method:response.request().method(),request:response.request().postDataJSON(),response:await response.json()});} catch {}
});
const get=async endpoint=>page.evaluate(async endpoint=>{const r=await fetch(endpoint);if(!r.ok)throw Error(`${endpoint}: ${r.status}`);return r.json();},endpoint);
async function settled(reviewId){
  const until=Date.now()+150000;
  while(Date.now()<until){
    const tickets=await get(`${prefix}/reviews`);
    const ticket=tickets.items.find(t=>t.review_id===reviewId);
    if(ticket&&['ready','failed'].includes(ticket.status))return ticket;
    await page.waitForTimeout(500);
  }
  throw Error('Review exceeded bounded VLM settlement window. Do not retry inference.');
}
async function rootSettled(expected=null){
  const until=Date.now()+180000;
  while(Date.now()<until){
    const records=await get(`${prefix}/requests`);
    const last=expected?records.items.find(r=>r.request_id===expected):records.items.filter(r=>r.submission.type==='queue').at(-1);
    if(last&&['completed','failed','stopped','outcome_uncertain'].includes(last.receipt.state))return last;
    await page.waitForTimeout(500);
  }
  throw Error('Cloud root exceeded 180 seconds; owned SDK deadline guard must abort.');
}
let result={action};
try{
  if(action==='owner'){
    await page.goto((await fs.readFile(`${root}/pair.private`,'utf8')).trim());
    await page.getByRole('button',{name:'Connect this browser'}).click();
    await page.getByRole('button').filter({has:page.locator('small',{hasText:/^m09-image-version$/})}).click();
    await page.getByRole('button',{name:'Critic',exact:true}).click();
    await page.getByLabel('Review criteria').fill('Return exactly two short distinct visual findings, each observation and recommendation under 12 words. Describe the blue object shape/location, then describe the orange object. Do not reason or invent measurements. This is read-only critique, not authority to change the scene.');
    const response=page.waitForResponse(r=>r.url().endsWith(`${prefix}/reviews`)&&r.request().method()==='POST');
    await page.getByRole('button',{name:'Request visual review',exact:true}).click();
    const r=await response;
    const ticket=await r.json();
    result={...result,post_status:r.status(),ticket};
    if(!r.ok())throw Error(JSON.stringify(ticket));
    result.settled=await settled(ticket.review_id);
    result.requests=await get(`${prefix}/requests`);
    result.jobs=await get(`${prefix}/jobs`);
    result.findings=await get(`${prefix}/reviews/${ticket.review_id}/findings`);
    await page.getByLabel('Review',{exact:true}).selectOption(ticket.review_id);
  }else if(action==='native'){
    const existing=(await get(`${prefix}/requests`)).items;
    if(existing.some(r=>r.submission.type==='queue'))throw Error('First native root already admitted. No replacement send.');
    await page.getByRole('button',{name:'Copilot',exact:true}).click();
    await page.getByRole('textbox',{name:'Message Copilot'}).fill(await fs.readFile(`${root}/native-review-prompt.txt`,'utf8'));
    const admission=page.waitForResponse(r=>r.url().endsWith(`${prefix}/requests`)&&r.request().method()==='POST');
    await page.getByRole('button',{name:'Queue message',exact:true}).click();
    const ack=await(await admission).json();
    result.request=await rootSettled(ack.request_id);
    result.ticket=await settled('m09-native-review');
    result.findings=await get(`${prefix}/reviews/m09-native-review/findings`);
    result.jobs=await get(`${prefix}/jobs`);
    await page.getByRole('button',{name:'Critic',exact:true}).click();
    await page.getByRole('button',{name:'Load existing reviews',exact:true}).click();
    await page.getByLabel('Review',{exact:true}).selectOption('m09-native-review');
  }else if(action==='approve'){
    await page.getByRole('button',{name:'Critic',exact:true}).click();
    await page.getByRole('button',{name:'Load existing reviews',exact:true}).click();
    await page.getByLabel('Review',{exact:true}).selectOption('m09-native-review');
    const records=await get(`${prefix}/reviews/m09-native-review/findings`);
    if(records.items.length<2)throw Error('Actual model supplied fewer than two findings. Do not fake a subset.');
    const first=records.items[0]; const second=records.items[1];
    const panel=page.getByRole('region',{name:'Critic',exact:true});
    await panel.locator('article').filter({has:page.locator('p',{hasText:first.observation})}).getByRole('checkbox',{name:'Include finding'}).check();
    const amended=`M09 read-only owner amendment. Do not edit files or run Blender/Unity/generation/review. Do not delegate. Acknowledge only this approved finding revision 1: ${first.observation} Inspect the attached approved image and host context. Reply APPROVED_REVISION_RECEIVED and identify the visible blue object. Stop.`;
    await page.getByLabel(`Approved instruction ${first.finding_id}`,{exact:true}).fill(amended);
    await panel.locator('article').filter({has:page.locator('p',{hasText:second.observation})}).getByRole('button',{name:'Reject finding',exact:true}).click();
    await page.screenshot({path:`${root}/approval-before-queue.png`});
    const next=page.waitForResponse(r=>r.url().endsWith(`${prefix}/requests`)&&r.request().method()==='POST');
    await page.getByRole('button',{name:'Queue selected findings once',exact:true}).click();
    const response=await next;
    const ack=await response.json();
    result={...result,first,second,amended,ack,post_status:response.status()};
    result.request=await rootSettled(ack.request_id);
    await page.getByRole('button',{name:'Critic',exact:true}).click();
    await page.getByRole('button',{name:'Load existing reviews',exact:true}).click();
    await page.getByLabel('Review',{exact:true}).selectOption('m09-native-review');
    result.findings=await get(`${prefix}/reviews/m09-native-review/findings`);
    result.requests=await get(`${prefix}/requests`);
    result.contexts={};for(const r of result.requests.items)if(r.submission.context_id)result.contexts[r.submission.context_id]=await get(`${prefix}/contexts/${r.submission.context_id}`);
  }else if(action==='unavailable'){
    await page.getByRole('button',{name:'Critic',exact:true}).click();
    await page.getByLabel('Review criteria').fill('Read-only availability check. The allocated vision instance is now unloaded. Do not load any model.');
    const response=page.waitForResponse(r=>r.url().endsWith(`${prefix}/reviews`)&&r.request().method()==='POST');
    await page.getByRole('button',{name:'Request visual review',exact:true}).click();
    const r=await response;result.ticket=await r.json();result.post_status=r.status();
    if(!r.ok())throw Error(JSON.stringify(result.ticket));
    result.settled=await settled(result.ticket.review_id);
    result.jobs=await get(`${prefix}/jobs`);
    result.requests=await get(`${prefix}/requests`);
    result.images=await page.locator('.review-image img').evaluateAll(nodes=>nodes.map(i=>({complete:i.complete,width:i.naturalWidth,height:i.naturalHeight})));
  }else if(action==='refresh'){
    await page.reload();
    await page.getByRole('button').filter({has:page.locator('small',{hasText:/^m09-image-version$/})}).click();
    await page.getByRole('button',{name:'Critic',exact:true}).click();
    await page.getByRole('button',{name:'Load existing reviews',exact:true}).click();
    const unavailable=JSON.parse(await fs.readFile(`${root}/unavailable.json`,'utf8'));
    await page.getByLabel('Review',{exact:true}).selectOption(unavailable.ticket.review_id);
    result.images=await page.locator('.review-image img').evaluateAll(nodes=>nodes.map(i=>({complete:i.complete,width:i.naturalWidth,height:i.naturalHeight})));
    result.reviews=await get(`${prefix}/reviews`);
  }else if(action==='collect'){
    for(const name of ['snapshot','requests','jobs','reviews','items']) result[name]=await get(`${prefix}/${name}`);
    result.findings={};for(const t of result.reviews.items)result.findings[t.review_id]=await get(`${prefix}/reviews/${t.review_id}/findings`);
    result.attempts={};for(const job of result.jobs.items)result.attempts[job.job_id]=await get(`${prefix}/jobs/${job.job_id}/attempts`);
    result.events=[];let after=0;
    for(let i=0;i<100;i++){const p=await get(`${prefix}/events?after=${after}&limit=100`);result.events.push(...p.events);if(p.next_after>=p.last_available)break;if(p.next_after<=after)throw Error('Non-advancing event cursor');after=p.next_after;}
    result.images=await page.locator('.review-image img').evaluateAll(nodes=>nodes.map(i=>({complete:i.complete,width:i.naturalWidth,height:i.naturalHeight})));
  }else throw Error('Unknown action');
}catch(error){result.error=String(error);process.exitCode=1;}
result.viewport=await page.evaluate(()=>({width:innerWidth,height:innerHeight,dpr:devicePixelRatio}));
result.browser=browser.version();
await page.screenshot({path:`${root}/${action}.png`});
await fs.writeFile(`${root}/${action}.json`,JSON.stringify(result,null,2));
await fs.writeFile(`${root}/${action}-traffic.json`,JSON.stringify(traffic,null,2));
console.log(JSON.stringify({action,error:result.error,ticket:result.settled??result.ticket,request:result.request?.receipt,findings:result.findings,viewport:result.viewport},null,2));
await browser.close();
