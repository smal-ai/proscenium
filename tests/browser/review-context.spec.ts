import { expect, test, type Page } from "@playwright/test";

const pageOf = (items: unknown[]) => ({schema_version:"1.0",items,next_cursor:null});
const target = {origin:"blender_render",artifact_id:"frame-2",displayed_sha256:"b".repeat(64),dimensions:{width:640,height:360},item_id:"scene",version_id:"v1",shot_id:"shot",render_job_id:"job",source_frame:2,media_artifact_id:null};
type Call = {path:string; method:string; body: Record<string,unknown> | Buffer | null};

test.beforeEach(async ({browser}, testInfo) => {
  testInfo.annotations.push({type:"actual-browser-engine", description:browser.version()});
});

async function fixture(page: Page, options: {catalogue?:boolean; failSave?:boolean; conflictSave?:boolean; holdUploads?:boolean; selectionFailure?:number; lostSelectionAck?:boolean} = {}) {
  const calls: Call[] = [];
  let releaseUploads = () => {};
  const uploadGate = new Promise<void>(resolve => {releaseUploads = resolve;});
  let selectionPosts=0;
  let selections:unknown[]=[];
  let submission:unknown;
  let context:unknown;
  await page.route("**/api/v1/**",async route => {
    const request=route.request();const path=new URL(request.url()).pathname;
    const body=path.includes("/uploads/") ? request.postDataBuffer() : request.method()==="POST" ? request.postDataJSON() : null;
    calls.push({path,method:request.method(),body});
    const json=(value:unknown,status=200)=>route.fulfill({status,contentType:"application/json",body:JSON.stringify(value)});
    if(path.endsWith("/content")) {
      const base64=await page.evaluate(()=>{const canvas=document.createElement("canvas");canvas.width=640;canvas.height=360;const ctx=canvas.getContext("2d")!;ctx.fillStyle="#556677";ctx.fillRect(0,0,640,360);return canvas.toDataURL("image/png").split(",")[1];});
      return route.fulfill({contentType:"image/png",body:Buffer.from(base64,"base64")});
    }
    if(path.includes("/uploads/")) {
      if(options.holdUploads) await uploadGate;
      return json({schema_version:"1.0",artifact_id:`upload-${path.split("/").at(-1)}`,project_id:"synthetic",kind:"image",purpose:"reference",sha256:"c".repeat(64),bytes:500,media_type:"image/png",state:"ready",derived_from:[],dimensions:{width:640,height:360}});
    }
    if(path.endsWith("/frames"))return json(pageOf([{schema_version:"1.0",receipt_id:"receipt-2",project_id:"synthetic",job_id:"job",attempt_id:"attempt",version_id:"v1",shot_id:"shot",source_frame:2,artifact_id:"frame-2",output_path:"frame.png",sha256:"b".repeat(64),dimensions:{width:640,height:360},input_digest:"a".repeat(64)}]));
    if(path.endsWith("/segments"))return json(pageOf([]));
    if(path.endsWith("/annotations")) {
      if(request.method()==="GET")return json(pageOf([]));
      if(options.failSave)return route.abort("failed");
      if(options.conflictSave)return json({schema_version:"1.0",error:{code:"revision_conflict",message:"Synthetic revision conflict",details:{current_revision:2}}},409);
      return json({schema_version:"1.0",operation_id:body.operation_id,resource_id:body.annotation.annotation_id,state:"saved",state_revision:body.expected_revision+1,replayed:false});
    }
    if(path.endsWith("/contexts")){context=body;return json(body);}
    if(path.endsWith("/requests")&&request.method()==="POST"){submission=body;return json({schema_version:"1.0",request_id:body.request_id,state:"accepted"},202);}
    if(path.includes("/requests/"))return json({submission,context,receipt:{state:"accepted"}});
    if(path.endsWith("/selections")) {
      if(request.method()==="GET")return json(pageOf(selections));
      selectionPosts++;
      if(options.selectionFailure && selectionPosts===1)return json({schema_version:"1.0",error:{code:options.selectionFailure===409?"revision_conflict":"invalid_request",message:"Synthetic unadmitted selection",details:{}}},options.selectionFailure);
      if(selectionPosts===1){
        selections=[{schema_version:"1.0",project_id:"synthetic",selection_id:body.selection_id,slot:body.slot,choice:{kind:"version",version:{item_id:"scene",version_id:"v2"}},state:"selected",state_revision:2,actor:{kind:"user",id:"other-tab"}}];
        if(options.lostSelectionAck)return route.abort("failed");
      }
      return json({schema_version:"1.0",operation_id:body.operation_id,resource_id:body.selection_id,state:"selected",state_revision:1,replayed:selectionPosts>1});
    }
    if(path.endsWith("/versions"))return json(pageOf([1,2].map(n=>({schema_version:"1.0",project_id:"synthetic",item_id:"scene",version_id:`v${n}`,kind:"scene",name:n===1?"Version one":"Version two",created_at:`2026-11-14T00:00:0${n}Z`,artifact_ids:[]}))));
    return json({schema_version:"1.0",error:{code:"not_found",message:path,details:{}}},404);
  });
  await page.goto(`/tests/browser/review-context-harness.html${options.catalogue?"?catalogue=1":""}`);
  if(options.catalogue)await expect(page.getByRole("button",{name:"Select for item"}).first()).toBeEnabled();
  else await expect(page.getByRole("button",{name:"Annotate presented frame"})).toBeEnabled();
  return {calls,releaseUploads,options};
}

async function mark(page: Page, tool="stroke") {
  await page.getByRole("button",{name:tool,exact:true}).click();
  const box=(await page.locator(".review-canvas").boundingBox())!;
  await page.mouse.move(box.x+box.width/2,box.y+box.height/2);await page.mouse.down();
  await page.mouse.move(box.x+box.width/2+30,box.y+box.height/2+15);await page.mouse.up();
}

test("registered preparer saves unsaved marks and canonical masks before actual main-chat Send",async({page})=>{
  const {calls}=await fixture(page);
  await page.getByRole("button",{name:"Annotate presented frame"}).click();
  await page.getByLabel("Feedback",{exact:true}).fill("Unsaved feedback");
  await mark(page);await mark(page,"mask");
  await page.getByLabel("Message Copilot").fill("Use the unsaved drawing");
  await page.getByRole("button",{name:"Queue message",exact:true}).click();
  await expect(page.getByTestId("accepted")).toContainText("Use the unsaved drawing");
  const captured=calls.find(c=>c.path.endsWith("/contexts"))!.body as Record<string,any>;
  expect(captured.annotations[0].target).toEqual(target);expect(captured.annotations[0].saved_revision).toBe(1);
  expect(captured.annotations[0].text).toBe("Unsaved feedback");expect(captured.annotations[0].markups.map((m:{kind:string})=>m.kind)).toEqual(["stroke","mask"]);
  expect(captured.attachments.map((a:{role:string})=>a.role)).toEqual(["render","markup","mask"]);
  const uploads=calls.filter(c=>c.path.includes("/uploads/"));expect(uploads).toHaveLength(2);expect((uploads[1].body as Buffer)[25]).toBe(0);
  expect(calls.findIndex(c=>c.path.endsWith("/annotations")&&c.method==="POST")).toBeLessThan(calls.findIndex(c=>c.path.endsWith("/contexts")));
  expect(await page.evaluate(()=>(window as any).contextHarness.registrations())).toBe(1);
});

for(const failure of ["network","conflict"] as const)test(`failed ${failure} annotation preparation blocks model submission`,async({page})=>{
  const fixtureState=await fixture(page,{failSave:failure==="network",conflictSave:failure==="conflict"});
  await page.getByRole("button",{name:"Annotate presented frame"}).click();await mark(page);
  await page.getByLabel("Message Copilot").fill("Do not send without my annotation");
  await page.getByRole("button",{name:"Queue message",exact:true}).click();
  await expect(page.getByRole("alert").last()).toBeVisible();
  expect(fixtureState.calls.filter(c=>c.path.endsWith("/contexts")||c.path.endsWith("/requests"))).toHaveLength(0);
  if(failure==="network"){
    fixtureState.options.failSave=false;
    await page.getByRole("button",{name:"Queue message",exact:true}).click();
    await expect(page.getByTestId("accepted")).toContainText("Do not send");
    const saves=fixtureState.calls.filter(c=>c.path.endsWith("/annotations")&&c.method==="POST");expect(saves).toHaveLength(2);expect(saves[1].body).toEqual(saves[0].body);
    expect(fixtureState.calls.filter(c=>c.path.includes("/uploads/"))).toHaveLength(1);
  }
});

test("draft and chat snapshots remain pinned when navigation unmounts the editor during upload",async({page})=>{
  const state=await fixture(page);
  await page.getByRole("button",{name:"Annotate presented frame"}).click();await mark(page);
  await page.getByLabel("Feedback",{exact:true}).fill("Pinned before navigation");
  await page.getByLabel("Message Copilot").fill("Original chat text");
  const png=await page.evaluate(()=>{const canvas=document.createElement("canvas");canvas.width=2;canvas.height=2;return canvas.toDataURL("image/png").split(",")[1];});
  await page.getByLabel("Attach images").setInputFiles({name:"extra-context.png",mimeType:"image/png",buffer:Buffer.from(png,"base64")});
  await expect(page.getByLabel("Image 1 role")).toHaveCount(1);
  state.options.holdUploads=true;
  await page.getByRole("button",{name:"Queue message",exact:true}).click();
  await expect.poll(()=>state.calls.filter(c=>c.path.includes("/uploads/")).length).toBe(2);
  await expect(page.getByLabel("Feedback",{exact:true})).toBeDisabled();
  await expect(page.getByRole("button",{name:"Save annotation",exact:true})).toBeDisabled();
  await page.getByRole("button",{name:"Navigate to another version"}).click();
  await page.getByLabel("Message Copilot").fill("Later chat draft");
  state.releaseUploads();
  await expect(page.getByTestId("accepted")).toContainText("Original chat text");
  const captured=state.calls.find(c=>c.path.endsWith("/contexts"))!.body as Record<string,any>;
  expect(captured.target).toEqual(target);expect(captured.annotations[0].target).toEqual(target);expect(captured.annotations[0].text).toBe("Pinned before navigation");
  expect(captured.attachments.map((a:{role:string})=>a.role)).toEqual(["render","markup","reference"]);
  expect(captured.attachments[2].artifact_id).toBe(`upload-${state.calls.find(c=>c.path.includes("/uploads/"))!.path.split("/").at(-1)}`);
  await expect(page.getByLabel("Message Copilot")).toHaveValue("Later chat draft");
  await page.getByRole("button",{name:"Unmount review"}).click();
  expect(await page.evaluate(()=>(window as any).contextHarness.hasPreparer())).toBe(false);
});

test("normal viewer preparer returns a detached exact context and clears its registration on unmount",async({page})=>{
  await fixture(page);
  const result=await page.evaluate(async()=>{
    const harness=(window as any).contextHarness;
    const first=await harness.prepare();const original=structuredClone(first);
    first.target.artifact_id="changed-by-caller";
    return {original,next:await harness.prepare(),registrations:harness.registrations()};
  });
  expect(result.original.target).toEqual(target);expect(result.next).toEqual(result.original);expect(result.registrations).toBe(1);
  await page.getByRole("button",{name:"Unmount review"}).click();
  expect(await page.evaluate(()=>(window as any).contextHarness.hasPreparer())).toBe(false);
});

test("imported raster mask keeps its original artifact while the preparer exports markup",async({page})=>{
  const {calls}=await fixture(page);
  await page.getByRole("button",{name:"Open imported raster draft"}).click();
  await expect(page.getByLabel("Feedback",{exact:true})).toHaveValue("Imported soft mask");
  await page.getByLabel("Message Copilot").fill("Use the original raster mask");
  await page.getByRole("button",{name:"Queue message",exact:true}).click();
  await expect(page.getByTestId("accepted")).toContainText("Use the original raster mask");
  const captured=calls.find(c=>c.path.endsWith("/contexts"))!.body as Record<string,any>;
  expect(captured.annotations[0].markups[0].mask_artifact_id).toBe("imported-raster");
  expect(captured.attachments).toContainEqual({artifact_id:"imported-raster",role:"mask"});
  expect(calls.filter(c=>c.path.includes("/uploads/"))).toHaveLength(1);
});

test("catalogue lost ACK survives reload and repeats identical SelectionSave without guessing owner choice",async({page})=>{
  const {calls}=await fixture(page,{catalogue:true,lostSelectionAck:true});
  const versionOne=page.getByRole("article").filter({has:page.getByRole("button",{name:"Version one v1",exact:true})});
  await versionOne.getByRole("button",{name:"Select for item",exact:true}).click();
  await expect(page.getByRole("button",{name:"Retry original selection"})).toBeVisible();
  await page.reload();
  await expect(page.getByRole("button",{name:"Retry original selection"})).toBeVisible();
  await page.getByRole("button",{name:"Reconcile selection status"}).click();
  await expect(page.getByRole("button",{name:"Retry original selection"})).toBeVisible();
  await expect(page.getByRole("button",{name:"Select for item"}).first()).toBeDisabled();
  await expect(page.getByRole("button",{name:"Withdraw selection"})).toBeDisabled();
  await page.getByRole("button",{name:"Retry original selection"}).click();
  await expect(page.getByRole("button",{name:"Retry original selection"})).toHaveCount(0);
  const commands=calls.filter(c=>c.path.endsWith("/selections")&&c.method==="POST");expect(commands).toHaveLength(2);expect(commands[1].body).toEqual(commands[0].body);
  const original=commands[0].body as Record<string,any>;expect(original.expected_revision).toBe(0);expect(original.slot).toEqual({kind:"item",item_id:"scene"});expect(original.choice.version.version_id).toBe("v1");
  await expect(page.getByRole("article").filter({has:page.getByRole("button",{name:"Version two v2",exact:true})})).toContainText("Owner selected");
});

for(const status of [409,422])test(`catalogue clears known unadmitted ${status} and reloads choices before a new owner command`,async({page})=>{
  const {calls}=await fixture(page,{catalogue:true,selectionFailure:status});
  const select=page.getByRole("article").filter({has:page.getByRole("button",{name:"Version one v1",exact:true})}).getByRole("button",{name:"Select for item",exact:true});
  await select.click();await expect(page.getByRole("alert")).toContainText("Synthetic unadmitted selection");
  await expect(select).toBeEnabled();expect(await page.getByRole("button",{name:"Retry original selection"}).count()).toBe(0);
  await select.click();
  await expect.poll(()=>calls.filter(c=>c.path.endsWith("/selections")&&c.method==="POST").length).toBe(2);
  const commands=calls.filter(c=>c.path.endsWith("/selections")&&c.method==="POST").map(c=>c.body as Record<string,unknown>);expect(commands[1].operation_id).not.toBe(commands[0].operation_id);
});
