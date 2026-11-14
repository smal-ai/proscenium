import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";

const target = {origin:"blender_render",artifact_id:"frame-2",displayed_sha256:"b".repeat(64),dimensions:{width:640,height:360},item_id:"scene",version_id:"v1",shot_id:"shot",render_job_id:"job",source_frame:2,media_artifact_id:null};
const pageOf = (items: unknown[]) => ({schema_version:"1.0",items,next_cursor:null});
const finding = {schema_version:"1.0",finding_id:"finding",review_id:"review",project_id:"synthetic",revision:1,target,observation:"The left edge is visually crowded",recommendation:"Move it right",state:"unreviewed",evidence:[{kind:"visual",artifact_id:"frame-2",description:"Image observation, not measurement"}]};

async function fixture(page: Page, options: {failFirstSave?: boolean; conflictFirstSave?: boolean; progressive?: boolean; middleReady?: boolean; family?:"f03"|"f04"} = {}) {
  page.on("pageerror", error => console.error("Browser runtime:",error.message));
  const calls: {path:string; method:string; body:unknown}[] = [];
  let saves = 0;
  let serverAnnotation:unknown=null;
  const mediaRoot = `/tmp/opencode/proscenium-e03-20261114-a/${options.family ?? "f04"}`;
  await page.route("**/api/v1/**", async route => {
    const request = route.request(); const path = new URL(request.url()).pathname;
    const body = request.method() === "POST" && !path.includes("uploads") ? request.postDataJSON() : path.includes("/uploads/") ? request.postDataBuffer() : null;
    calls.push({path,method:request.method(),body});
    const json = (value:unknown,status=200) => route.fulfill({status,contentType:"application/json",body:JSON.stringify(value)});
    if (path.endsWith("/content")) {
      if (options.progressive) {
        const artifact = path.split("/").at(-2)!;
        if (artifact.startsWith("segment-")) {const start=Number(artifact.split("-")[1]);return route.fulfill({contentType:"video/mp4",body:readFileSync(`${mediaRoot}/segment-${String(start).padStart(2,"0")}-${String(start+3).padStart(2,"0")}.mp4`)});}
        if (artifact.startsWith("frame-")) return route.fulfill({contentType:"image/png",body:readFileSync(`${mediaRoot}/frames/${artifact.split("-")[1].padStart(4,"0")}.png`)});
      }
      // Deterministic two-colour PNG created by the browser's image pipeline.
      const bytes = await page.evaluate(() => {
        const c = document.createElement("canvas"); c.width=640;c.height=360; const ctx=c.getContext("2d")!;
        ctx.fillStyle="#556677";ctx.fillRect(0,0,640,360);ctx.fillStyle="#ffffff";ctx.fillRect(0,0,20,20);
        return c.toDataURL("image/png").split(",")[1];
      });
      return route.fulfill({contentType:"image/png",body:Buffer.from(bytes,"base64")});
    }
    if (path.endsWith("/proxy-mapping")) return json({schema_version:"1.0",project_id:"synthetic",version_id:"v1",proxy_artifact_id:"proxy",source_closure_digest:"a".repeat(64),sample_frame:2,space:"proxy_world_m",nodes:[{node_path:[0],object_id:"object",instance_id:"instance-blue",display_name:"Blue instance",world_matrix:[1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1]}]});
    if (path.endsWith("/frames")) return json(pageOf((options.progressive ? Array.from({length:12},(_,i)=>i+1) : [2]).map(frame => ({schema_version:"1.0",receipt_id:`receipt-${frame}`,project_id:"synthetic",job_id:"job",attempt_id:"attempt",version_id:"v1",shot_id:"shot",source_frame:frame,artifact_id:`frame-${frame}`,output_path:"frame.png",sha256:"b".repeat(64),dimensions:{width:640,height:360},input_digest:"a".repeat(64)}))));
    if (path.endsWith("/segments")) return json(pageOf(options.progressive ? (options.middleReady ? [1,5,9] : [1,9]).map(start => ({schema_version:"1.0",segment_id:`segment-${start}`,project_id:"synthetic",job_id:"job",attempt_id:"attempt",shot_id:"shot",version_id:"v1",source_frames:{start,end:start+3},fps:options.family==="f03"?{numerator:24,denominator:1}:{numerator:24000,denominator:1001},frame_count:4,artifact_id:`segment-${start}`,output_path:"segment.mp4",sha256:"d".repeat(64),input_digest:"a".repeat(64)})) : []));
    if (path.endsWith("/availability")) return json({schema_version:"1.0",project_id:"synthetic",job_id:"job",version_id:"v1",source_range:{start:1,end:12},fps:options.family==="f03"?{numerator:24,denominator:1}:{numerator:24000,denominator:1001},available_ranges:options.progressive?[{start:1,end:12}]:[{start:2,end:2}],playable_ranges:options.progressive?(options.middleReady?[{start:1,end:12}]:[{start:1,end:4},{start:9,end:12}]):[],pending_ranges:options.middleReady?[]:[{start:5,end:8}],failed_ranges:[],state_revision:0});
    if (path.endsWith("/annotations")) {
      if (request.method()==="GET") return json(pageOf(serverAnnotation?[serverAnnotation]:[]));
      saves++;
      if (options.failFirstSave && saves===1) return route.abort("failed");
      const command = body as {operation_id:string;annotation:{annotation_id:string};expected_revision:number};
      if(options.conflictFirstSave&&saves===1){serverAnnotation={...command.annotation,saved_revision:2,text:"Server edit"};return json({schema_version:"1.0",error:{code:"revision_conflict",message:"Synthetic concurrent edit",details:{current_revision:2}}},409);}
      return json({schema_version:"1.0",operation_id:command.operation_id,resource_id:command.annotation.annotation_id,state:"saved",state_revision:command.expected_revision+1,replayed:saves>1});
    }
    if (path.includes("/uploads/")) return json({schema_version:"1.0",artifact_id:`upload-${path.split("/").at(-1)}`,project_id:"synthetic",kind:"image",purpose:"reference",sha256:"c".repeat(64),bytes:500,media_type:"image/png",state:"ready",derived_from:[],dimensions:{width:640,height:360}});
    if (path.endsWith("/findings")) return json(pageOf([finding]));
    if (path.endsWith("/reviews")) return json(request.method()==="GET" ? pageOf([{schema_version:"1.0",review_id:"review",job_id:"vlm",status:"ready"}]) : {schema_version:"1.0",review_id:(body as {review_id:string}).review_id,job_id:"vlm",status:"pending"});
    return json({schema_version:"1.0",error:{code:"not_found",message:path,details:{}}},404);
  });
  await page.goto("/tests/browser/review-harness.html");
  await expect(page.getByRole("img",{name:options.progressive ? "blender_render, frame 1" : "blender_render, frame 2"})).toBeVisible();
  return calls;
}

test("one mode, exact outliner pairs, form keyboard isolation and no provider side effects", async ({page},testInfo) => {
  const calls = await fixture(page);
  await page.getByRole("button",{name:"Blue instance"}).click();
  await expect(page.getByTestId("selection")).toContainText('"instance_id":"instance-blue"');
  await page.getByLabel("External text input").fill("123 ");
  await expect(page.getByRole("button",{name:"2 Keyframes"})).toHaveAttribute("aria-pressed","true");
  await page.getByLabel("External text input").blur();
  await page.keyboard.press("3");
  await expect(page.getByRole("button",{name:"3 Animation"})).toHaveAttribute("aria-pressed","true");
  await page.keyboard.press("2");
  expect(calls.filter(c => c.method==="POST")).toEqual([]);
  if(testInfo.project.name==="chromium") await page.screenshot({path:"docs/evidence/w05/synthetic-surfaces-1920x1080.png"});
});

test("durable original-operation retry after reload and exact annotation Queue attachments", async ({page}) => {
  const calls = await fixture(page,{failFirstSave:true});
  await page.getByRole("button",{name:"Annotate presented frame"}).click();
  await page.getByLabel("Feedback",{exact:true}).fill("Keep this pinned target");
  const canvas = page.locator(".review-canvas"); const rect = (await canvas.boundingBox())!;
  await page.mouse.move(rect.x+rect.width/2,rect.y+rect.height/2);await page.mouse.down();await page.mouse.move(rect.x+rect.width/2+40,rect.y+rect.height/2+20);await page.mouse.up();
  await page.getByRole("button",{name:"Save annotation",exact:true}).click();
  await expect(page.getByRole("button",{name:"Retry original save"})).toBeVisible();
  await page.reload();
  await page.getByText("Saved annotations and local drafts").click();
  await page.getByRole("button",{name:/Keep this pinned target · failed/}).click();
  await page.getByRole("button",{name:"Retry original save"}).click();
  await expect(page.getByRole("button",{name:"Queue feedback"})).toBeEnabled();
  const saves = calls.filter(c => c.method==="POST" && c.path.endsWith("/annotations"));
  expect(saves).toHaveLength(2); expect(saves[1].body).toEqual(saves[0].body);
  const command=saves[0].body as {annotation:{markups:{points:number[][];width_px:number}[]}};
  expect(command.annotation.markups[0].points[0][0]).toBeCloseTo(.5,2);expect(command.annotation.markups[0].points[0][1]).toBeCloseTo(.5,2);expect(command.annotation.markups[0].width_px).toBe(4);
  await page.getByRole("button",{name:"Queue feedback"}).click();
  const queued = JSON.parse(await page.getByTestId("queued").innerText());
  expect(queued[0].context.annotations[0].target).toEqual(target);
  expect(queued[0].context.attachments.map((a:{role:string}) => a.role)).toEqual(["render","markup"]);
});

test("mask brush uploads a separate canonical greyscale PNG without modifying the target hash",async({page})=>{
  const calls=await fixture(page);
  await page.getByRole("button",{name:"Annotate presented frame"}).click();
  await page.getByRole("button",{name:"mask",exact:true}).click();
  const box=(await page.locator(".review-canvas").boundingBox())!;
  await page.mouse.move(box.x+box.width/2,box.y+box.height/2);await page.mouse.down();await page.mouse.move(box.x+box.width/2+40,box.y+box.height/2);await page.mouse.up();
  await page.getByRole("button",{name:"Save annotation",exact:true}).click();
  await expect(page.getByRole("button",{name:"Queue feedback"})).toBeEnabled();
  const uploads=calls.filter(c=>c.path.includes("/uploads/"));expect(uploads).toHaveLength(2);
  expect((uploads[1].body as Buffer)[24]).toBe(8);expect((uploads[1].body as Buffer)[25]).toBe(0);
  const command=calls.find(c=>c.method==="POST"&&c.path.endsWith("/annotations"))!.body as {annotation:{target:unknown;markups:{mask_encoding:unknown}[]}};
  expect(command.annotation.target).toEqual(target);expect(command.annotation.markups[0].mask_encoding).toEqual({channel:"luma",bit_depth:8,selected_value:255,unselected_value:0,semantics:"binary"});
});

test("revision conflict requires explicit comparison and rebases with a new operation",async({page})=>{
  const calls=await fixture(page,{conflictFirstSave:true});
  await page.getByRole("button",{name:"Annotate presented frame"}).click();await page.getByLabel("Feedback",{exact:true}).fill("My retained local edit");
  await page.getByRole("button",{name:"Save annotation",exact:true}).click();
  await page.getByRole("button",{name:"Load server revision for comparison"}).click();
  await expect(page.getByText("Server revision 2: Server edit")).toBeVisible();
  await page.getByRole("button",{name:"Rebase local edits onto revision 2"}).click();
  await expect(page.getByLabel("Feedback",{exact:true})).toHaveValue("My retained local edit");
  await page.getByRole("button",{name:"Save annotation",exact:true}).click();
  await expect(page.getByRole("button",{name:"Queue feedback"})).toBeEnabled();
  const saves=calls.filter(c=>c.method==="POST"&&c.path.endsWith("/annotations")).map(c=>c.body as {operation_id:string;expected_revision:number});
  expect(saves).toHaveLength(2);expect(saves[1].operation_id).not.toBe(saves[0].operation_id);expect(saves[1].expected_revision).toBe(2);
});

test("canonical mask PNG has greyscale type zero and no alpha", async ({page}) => {
  await fixture(page);
  const bytes = await page.evaluate(async () => {
    const api = (window as unknown as {reviewTest:{greyPng:(w:number,h:number,p:Uint8Array)=>Promise<Blob>}}).reviewTest;
    return [...new Uint8Array(await (await api.greyPng(2,2,new Uint8Array([0,255,255,0]))).arrayBuffer())];
  });
  expect(bytes.slice(0,8)).toEqual([137,80,78,71,13,10,26,10]); expect(bytes[24]).toBe(8); expect(bytes[25]).toBe(0);
});

test("critic reads only on request, amends exact finding and queues once", async ({page}) => {
  const calls = await fixture(page);
  expect(calls.filter(c => c.path.endsWith("/reviews"))).toHaveLength(0);
  await page.getByRole("button",{name:"Load existing reviews"}).click();
  await page.getByLabel("Review",{exact:true}).selectOption("review");
  await expect(page.getByText("Visual observation",{exact:true})).toBeVisible();
  await page.getByLabel("Include finding").check();
  await page.getByLabel("Approved instruction finding").fill("My explicit correction");
  await page.getByRole("button",{name:"Queue selected findings once"}).click();
  await expect(page.getByRole("button",{name:"Queued selected findings"})).toBeDisabled();
  const queued = JSON.parse(await page.getByTestId("queued").innerText());
  expect(queued).toHaveLength(1); expect(queued[0].context.approved_findings).toEqual([{finding_id:"finding",finding_revision:1,approved_text:"My explicit correction",target}]);
});

test("normative letterbox/DPR and fractional temporal vectors", async ({page}) => {
  await fixture(page);
  const vectors = JSON.parse(readFileSync("docs/acceptance/coordinate-vectors.json","utf8"));
  const result = await page.evaluate(v => {
    const {media} = (window as unknown as {reviewTest:{media:{imagePoint:(x:number,y:number,r:unknown)=>unknown;presentedFrame:(t:number,s:unknown)=>number}}}).reviewTest;
    return {image:v.image_mapping.map((r:{pointer_css:number[];content_rect_css:number[]}) => media.imagePoint(r.pointer_css[0],r.pointer_css[1],{left:r.content_rect_css[0],top:r.content_rect_css[1],width:r.content_rect_css[2],height:r.content_rect_css[3]})), temporal:v.temporal_cases.map((r:{segment_start:number;source_frame:number;fps:{numerator:number;denominator:number};start_seconds:number[];end_seconds:number[]}) => media.presentedFrame((r.start_seconds[0]/r.start_seconds[1]+r.end_seconds[0]/r.end_seconds[1])/2,{source_frames:{start:r.segment_start,end:12},fps:r.fps,frame_count:8}))};
  },vectors);
  expect(result.image).toEqual(vectors.image_mapping.map((r:{normalised:unknown})=>r.normalised));
  expect(result.temporal).toEqual(vectors.temporal_cases.map((r:{source_frame:number})=>r.source_frame));
});

test("projection landmarks use Blender-world row-major cameras including shifted and rotated views", async ({page}) => {
  await fixture(page);
  const vectors = JSON.parse(readFileSync("docs/acceptance/coordinate-vectors.json","utf8"));
  const cases = [vectors.perspective,vectors.orthographic,vectors.shifted_perspective,vectors.rotated_perspective];
  const result = await page.evaluate(cases => {
    const {media} = (window as unknown as {reviewTest:{media:{projectBlenderPoint:(camera:unknown,point:number[])=>number[]}}}).reviewTest;
    return cases.map(c => c.landmarks.map((l:{world:number[]}) => media.projectBlenderPoint(c,l.world)));
  },cases);
  cases.forEach((c,i) => c.landmarks.forEach((landmark:{normalised:number[]},j:number) => landmark.normalised.forEach((coordinate,k) => expect(result[i][j][k]).toBeCloseTo(coordinate,10))));
});

test("numbered fractional MP4 stops at first gap and paused annotation stays pinned on late receipts", async ({page}) => {
  const options = {progressive:true,middleReady:false};
  await fixture(page,options);
  await page.getByRole("button",{name:"3 Animation"}).click();
  await page.getByRole("button",{name:"Play",exact:true}).click();
  await expect(page.getByText("Buffering at frame 5")).toBeVisible();
  // Frame 9 is already encoded but must not be substituted for the hole at 5.
  await expect(page.getByTestId("selection")).not.toContainText('"source_frame":9');
  await page.getByRole("button",{name:"Pause",exact:true}).click();
  options.middleReady=true;
  await page.getByRole("button",{name:"Refresh receipts"}).click();
  await expect(page.getByRole("button",{name:"Play",exact:true})).toBeVisible();
  await expect(page.getByRole("button",{name:"Annotate presented frame"})).toBeEnabled();
  await page.getByRole("button",{name:"Annotate presented frame"}).click();
  await expect(page.getByRole("img",{name:"Pinned clean annotation target"})).toBeVisible();
  const pinned = await page.getByTestId("selection").innerText();
  await page.getByRole("button",{name:"Refresh receipts"}).click();
  await expect(page.getByTestId("selection")).toHaveText(pinned);
});

test("explicit external target navigation is honoured and refresh never jumps back",async({page})=>{
  await fixture(page);
  await page.getByRole("button",{name:"External primary target"}).click();
  await expect(page.getByRole("img",{name:"concept, frame image"})).toBeVisible();
  await page.getByRole("button",{name:"Refresh receipts"}).click();
  await expect(page.getByTestId("selection")).toContainText('"artifact_id":"external-image"');
});

for (const family of ["f03","f04"] as const) test(`continuous ${family} playback presents ordered ordinal cells across both boundaries`,async({page},testInfo)=>{
  await page.addInitScript(()=>{
    const state=window as unknown as {mediaAudit:{ordinal:number;time:number;mediaTime:number;src:string}[]};state.mediaAudit=[];
    const native=HTMLVideoElement.prototype.requestVideoFrameCallback;
    HTMLVideoElement.prototype.requestVideoFrameCallback=function(callback){
      const element=this;
      return native.call(element,(now,metadata)=>{
        if (!element.classList.contains("review-prebuffer")) {
          const c=document.createElement("canvas");c.width=element.videoWidth;c.height=element.videoHeight;const ctx=c.getContext("2d")!;ctx.drawImage(element,0,0);
          const ordinal=Array.from({length:4},(_,bit)=>ctx.getImageData(56+64*bit,56,1,1).data).reduce((n,rgb,bit)=>n|(Math.min(rgb[0],rgb[1],rgb[2])>223?1<<bit:0),0);
          state.mediaAudit.push({ordinal,time:now,mediaTime:metadata.mediaTime,src:element.currentSrc});
        }
        callback(now,metadata);
      });
    };
  });
  await fixture(page,{progressive:true,middleReady:true,family});
  await page.getByRole("button",{name:"3 Animation"}).click();
  await expect(page.getByRole("button",{name:"Annotate presented frame"})).toBeEnabled();
  await page.getByRole("button",{name:"Play",exact:true}).click();
  try {await expect.poll(()=>page.evaluate(()=>(window as unknown as {mediaAudit:{ordinal:number}[]}).mediaAudit.some(r=>r.ordinal===12))).toBe(true);} catch(error) {console.log(await page.evaluate(()=>({videos:[...document.querySelectorAll("video")].map(v=>({src:v.src,paused:v.paused,time:v.currentTime,ready:v.readyState})),audit:(window as unknown as {mediaAudit:unknown}).mediaAudit})));throw error;}
  const rows=await page.evaluate(()=>(window as unknown as {mediaAudit:{ordinal:number;time:number;mediaTime:number;src:string}[]}).mediaAudit);
  const fps=family==="f03"?24:24000/1001;
  const gaps=rows.slice(1).map((row,i)=>({from:rows[i].ordinal,to:row.ordinal,ms:row.time-rows[i].time}));
  await testInfo.attach(`${family}-actual-ordinal-cells`,{body:JSON.stringify({browser:await page.context().browser()?.version(),expected_frame_ms:1000/fps,boundary_gaps:gaps.filter(g=>g.from===4||g.from===8),rows},null,2),contentType:"application/json"});
  expect(rows.map(r=>r.ordinal)).toEqual(Array.from({length:12},(_,i)=>i+1));
  // Surface callbacks cannot silently label a different decoded ordinal as the requested frame.
  for (const row of rows) {
    const start=Number(/segment-(\d+)/.exec(row.src)![1]);const fps=family==="f03"?24:24000/1001;
    expect(row.ordinal).toBe(start+Math.floor(row.mediaTime*fps+1e-4));
    expect(Math.abs(row.mediaTime-(row.ordinal-start)/fps)).toBeLessThan(.000002);
  }
});
