import { expect, test, type Page } from "@playwright/test";
import { createHash } from "node:crypto";
import { readFileSync, writeFileSync } from "node:fs";
import { Matrix4, Vector3 } from "three";
import type { Artifact, ItemRecord, JobRecord, ProxyMapping, SourceClosure, VersionRecord } from "../../apps/frontend/src/api/generated/contracts";

// Read-only accepted native proof. No source materialisation, native process or inference.
const proofPath = "/tmp/opencode/proscenium-w03-native-a.json";
const analysisPath = "/tmp/opencode/proscenium-w03-native-analysis.json";
const proof: {closure: SourceClosure; jobs: {job: JobRecord; root: string}[]; proxy_mapping: ProxyMapping; artifacts: Artifact[]} = JSON.parse(readFileSync(proofPath,"utf8"));
const nativeAnalysis = JSON.parse(readFileSync(analysisPath,"utf8"));
const proxyJob = proof.jobs.find(record => record.job.render_spec?.purpose === "proxy")!;
const glbPath = `${proxyJob.root}/proxy.glb`;
const glb = readFileSync(glbPath);
const proxy = proof.artifacts.find(artifact => artifact.artifact_id === proof.proxy_mapping.proxy_artifact_id)!;
expect(createHash("sha256").update(glb).digest("hex")).toBe(proxy.sha256);
expect(createHash("sha256").update(readFileSync(proofPath)).digest("hex")).toBe(nativeAnalysis.proof_sha256);
const png = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScLbtAAAAABJRU5ErkJggg==", "base64");
const pngHash = createHash("sha256").update(png).digest("hex");
const version: VersionRecord = {
  schema_version:"1.0",project_id:"w03-native",item_id:"fixture-scene",version_id:"fixture-v1",kind:"scene",name:"Pinned base metadata",publication_id:"synthetic-ui-publication",
  source_commit: proof.closure.files[0].origin.kind === "git" ? proof.closure.files[0].origin.commit : "a".repeat(40), source:proof.closure,
  artifact_ids:["initial-concept"],preview_requests:[proxyJob.job.render_spec!],preview_job_ids:[proxyJob.job.job_id],created_at:"2026-11-14T00:00:00Z",actor:{kind:"user",id:"fixture-owner"},
};
const items: ItemRecord[] = [
  {schema_version:"1.0",project_id:version.project_id,item_id:version.item_id,kind:"scene",name:"Ceramic lamp",description:"Glazed interior reference",tags:["ceramic","interior"],current_version_id:"fixture-v2",selected_version_id:"fixture-v1",state_revision:0,provenance:{source_url:"https://example.test/assets/lamp",creator:"Alice Example",licence:"CC-BY-4.0",note:"Museum study"}},
  {schema_version:"1.0",project_id:version.project_id,item_id:"unknown-item",kind:"asset",name:"Unknown <img src=x onerror=alert(1)>",current_version_id:"unknown-v1",selected_version_id:null,state_revision:0,provenance:{source_url:"javascript:alert(1)",creator:null,licence:null}},
];

test.beforeEach(async({browser}, info) => {info.annotations.push({type:"actual-browser-engine",description:browser.version()});});

async function fixture(page: Page, options: {failUpload?: boolean; badAck?: boolean; failQueue?: boolean; catalogue?: boolean; late?: boolean} = {}) {
  const uploads: {url:string; id:string; bytes:Buffer; sha:string; width:number; height:number}[] = [];
  const queues: {text:string; context: import("../../apps/frontend/src/api/studio").TurnSelection}[] = [];
  const requests: {method:string; url:string}[] = [];
  const artifact = (id:string): Artifact => ({schema_version:"1.0",project_id:version.project_id,artifact_id:id,kind:"image",purpose:"concept",state:"ready",sha256:pngHash,bytes:png.length,media_type:"image/png",dimensions:{width:1,height:1},derived_from:[]});
  const pageOf = (records: unknown[], next_cursor:string|null=null) => ({schema_version:"1.0",items:records,next_cursor});
  await page.route("**/camera-fixture", route => route.fulfill({json:{version,jobs:[proxyJob.job],items,lateImageId:"late-job-image"}}));
  await page.route("**/camera-owner-queue",async route => {queues.push(route.request().postDataJSON());await route.fulfill({status:options.failQueue?503:200,json:{recorded:true}});});
  await page.route("**/api/v1/**",async route => {
    const request=route.request();const url=new URL(request.url());const path=url.pathname;requests.push({method:request.method(),url:request.url()});
    const json=(body:unknown,status=200)=>route.fulfill({status,json:body});
    if(path.includes("/uploads/")) {
      const bytes=request.postDataBuffer()!;const id=path.split("/").at(-1)!;
      const sha=createHash("sha256").update(bytes).digest("hex");
      const width=bytes.readUInt32BE(16),height=bytes.readUInt32BE(20);
      uploads.push({url:request.url(),id,bytes,sha,width,height});
      if(options.failUpload)return route.abort("failed");
      return json({...artifact(`capture-${id}`),bytes:bytes.length,sha256:options.badAck?"0".repeat(64):sha,dimensions:{width,height}});
    }
    if(path.endsWith("/content"))return route.fulfill({contentType:path.includes(proxy.artifact_id)?"model/gltf-binary":"image/png",body:path.includes(proxy.artifact_id)?glb:png});
    if(path.endsWith("/proxy-mapping"))return json(proof.proxy_mapping);
    if(path.endsWith("/versions")) {
      if(path.includes("unknown-item"))return json(pageOf([{...version,item_id:"unknown-item",version_id:"unknown-v1",name:"Unknown version",provenance:undefined}]));
      if(!url.searchParams.get("cursor"))return json(pageOf([{...version,version_id:"fixture-v2",name:"New head must not be adopted",provenance:items[0].provenance}],"pinned-page"));
      return json(pageOf([{...version,name:options.late?"Refreshed pinned metadata":version.name,artifact_ids:options.late?[...version.artifact_ids,"late-version-image"]:version.artifact_ids,provenance:items[0].provenance}]));
    }
    if(path.endsWith("/selections"))return json(pageOf([{schema_version:"1.0",project_id:version.project_id,selection_id:"owner-choice",slot:{kind:"item",item_id:version.item_id},choice:{kind:"version",version:{item_id:version.item_id,version_id:version.version_id}},state:"selected",state_revision:1,actor:{kind:"user",id:"owner"}}]));
    if(path.endsWith("/frames")||path.endsWith("/segments")||path.endsWith("/annotations"))return json(pageOf([]));
    if(path.endsWith("/availability"))return json({schema_version:"1.0",project_id:version.project_id,job_id:proxyJob.job.job_id,version_id:version.version_id,source_range:{start:1,end:1},fps:{numerator:24,denominator:1},available_ranges:[],playable_ranges:[],pending_ranges:[],failed_ranges:[],state_revision:0});
    const id=path.split("/").at(-1)!;
    if(path.includes("/artifacts/"))return json(id===proxy.artifact_id?proxy:id.endsWith("proxy-map")?proof.artifacts.find(a=>a.artifact_id===id):artifact(id));
    return json({schema_version:"1.0",error:{code:"not_found",message:path,details:{}}},404);
  });
  await page.goto(`/tests/browser/review-camera-harness.html${options.catalogue?"?catalogue=1":""}`);
  if(!options.catalogue)await expect(page.getByRole("button",{name:"Annotate presented frame"})).toBeEnabled();
  return {uploads,queues,requests,options};
}

async function inspection(page:Page) {
  await page.getByRole("button",{name:"1 3D Inspection",exact:true}).click();
  await expect(page.getByRole("button",{name:"Render this view",exact:true})).toBeEnabled();
}

async function selection(page:Page) {return JSON.parse(await page.getByTestId("selection").innerText()) as import("../../apps/frontend/src/api/studio").TurnSelection;}

test("real W03 GLB cube picks, camera basis and durable intrinsic PNG capture",async({page},info)=>{
  const state=await fixture(page,{failUpload:true,failQueue:true});
  await inspection(page);
  await page.getByRole("button",{name:"2 Keyframes",exact:true}).click();await inspection(page);
  expect(state.uploads).toHaveLength(0);expect(state.queues).toHaveLength(0);
  const canvas=page.locator(".review-proxy canvas");const rect=(await canvas.boundingBox())!;
  for(const [x,instance] of [[1,"instance-blue"],[-1,"instance-renamed"]] as const) {
    const view=await selection(page);const camera=view.camera!;
    const clip=new Vector3(x,0,1).applyMatrix4(new Matrix4().fromArray(camera.projection).transpose().multiply(new Matrix4().fromArray(camera.camera_to_world).transpose().invert()));
    await page.mouse.click(rect.x+(clip.x+1)*rect.width/2,rect.y+(1-clip.y)*rect.height/2);
    await expect.poll(async()=> (await selection(page)).selected_objects).toEqual([{object_id:"object-cube-source",instance_id:instance}]);
  }
  const initial=await selection(page);
  const expected=new Matrix4().set(1,0,0,0,0,0,-1,0,0,1,0,0,0,0,0,1).multiply(new Matrix4().lookAt(new Vector3(5,4,5),new Vector3(0,0,0),new Vector3(0,1,0)).setPosition(5,4,5)).transpose().toArray();
  initial.camera!.camera_to_world.forEach((value,i)=>expect(value).toBeCloseTo(expected[i],8));
  await page.mouse.move(rect.x+rect.width*.8,rect.y+rect.height*.5);await page.mouse.down();
  await page.mouse.move(rect.x+rect.width*.8+65,rect.y+rect.height*.5+25,{steps:5});await page.mouse.up();
  await expect.poll(async()=> (await selection(page)).camera!.camera_to_world).not.toEqual(initial.camera!.camera_to_world);
  const before=await selection(page);
  const intrinsic=await canvas.evaluate(element=>{if(!(element instanceof HTMLCanvasElement))throw new Error("Missing canvas");return {width:element.width,height:element.height};});
  await page.getByRole("button",{name:"Render this view",exact:true}).click();
  await expect(page.getByRole("button",{name:"Retry captured view upload"})).toBeEnabled();
  expect(state.queues).toHaveLength(0);expect(state.uploads).toHaveLength(1);
  // Reload proves private capture/blob/upload identity persistence, not just a local variable.
  await page.reload();await page.getByRole("button",{name:"1 3D Inspection",exact:true}).click();
  await expect.poll(async()=> (await selection(page)).camera?.camera_to_world).not.toEqual(before.camera!.camera_to_world);
  state.options.failUpload=false;
  await page.getByRole("button",{name:"Retry captured view upload"}).click();
  await expect(page.getByRole("region",{name:"Retained inspection capture"})).toContainText("uncertain");
  expect(state.uploads).toHaveLength(2);expect(state.uploads[1].id).toBe(state.uploads[0].id);expect(state.uploads[1].bytes).toEqual(state.uploads[0].bytes);
  expect(state.queues).toHaveLength(1);
  const captured=state.queues[0].context;const uploaded=state.uploads[1];
  expect(captured.target).toEqual({origin:"proxy_capture",artifact_id:`capture-${uploaded.id}`,displayed_sha256:uploaded.sha,dimensions:intrinsic,item_id:version.item_id,version_id:version.version_id,source_frame:1,shot_id:"fixture-shot",render_job_id:null,media_artifact_id:null});
  expect(captured.camera!.aspect).toBe(intrinsic.width/intrinsic.height);
  expect(captured.camera!.camera_to_world).toEqual(before.camera!.camera_to_world);
  expect(captured.selected_objects).toEqual(before.selected_objects);expect(captured.annotations).toEqual([]);
  expect(captured.attachments).toEqual([{artifact_id:`capture-${uploaded.id}`,role:"reference"}]);
  const pixels=await page.evaluate(async base64=>{
    const blob=await (await fetch(`data:image/png;base64,${base64}`)).blob();const image=await createImageBitmap(blob);
    const canvas=document.createElement("canvas");canvas.width=image.width;canvas.height=image.height;const ctx=canvas.getContext("2d")!;ctx.drawImage(image,0,0);
    const values=ctx.getImageData(0,0,canvas.width,canvas.height).data;let opaque=0;const colours=new Set<string>();
    for(let i=0;i<values.length;i+=4){if(values[i+3])opaque++;if(i%400===0)colours.add(`${values[i]},${values[i+1]},${values[i+2]},${values[i+3]}`);}
    return {width:image.width,height:image.height,opaque,colours:colours.size};
  },uploaded.bytes.toString("base64"));
  expect(pixels.width).toBe(intrinsic.width);expect(pixels.height).toBe(intrinsic.height);expect(pixels.opaque).toBeGreaterThan(intrinsic.width*intrinsic.height*.05);expect(pixels.colours).toBeGreaterThan(8);
  await page.reload();await page.getByRole("button",{name:"1 3D Inspection",exact:true}).click();
  await expect(page.getByRole("button",{name:"Render this view",exact:true})).toBeDisabled();expect(state.queues).toHaveLength(1);
  const runtime=await page.locator(".review-proxy canvas").evaluate(canvas=>{if(!(canvas instanceof HTMLCanvasElement))throw new Error("Missing canvas");const gl=canvas.getContext("webgl2")!;const ext=gl.getExtension("WEBGL_debug_renderer_info");return {renderer:ext?gl.getParameter(ext.UNMASKED_RENDERER_WEBGL):"unknown",dpr:devicePixelRatio};});
  expect(runtime.renderer).toContain("SwiftShader");
  const evidence={glbPath,glbSha256:proxy.sha256,mapping:proof.proxy_mapping,runtime,intrinsic,pixels,uploadSha256:uploaded.sha,selection:captured};
  await info.attach("camera-capture-proof",{contentType:"application/json",body:JSON.stringify(evidence,null,2)});
  writeFileSync(`docs/evidence/w05/camera-capture-dpr${runtime.dpr}.png`,uploaded.bytes);
  writeFileSync(`docs/evidence/w05/camera-selection-dpr${runtime.dpr}.json`,JSON.stringify(evidence,null,2)+"\n");
  if(info.project.name==="chromium")await page.screenshot({path:"docs/evidence/w05/camera-proxy.png"});
});

test("capture acknowledgement mismatch blocks Queue, then awaits a confirmed submission",async({page})=>{
  const state=await fixture(page,{badAck:true});await inspection(page);
  await page.getByRole("button",{name:"Render this view",exact:true}).click();await expect(page.getByRole("button",{name:"Retry captured view upload"})).toBeEnabled();
  expect(state.queues).toHaveLength(0);state.options.badAck=false;
  await page.getByRole("button",{name:"Retry captured view upload"}).click();
  await expect.poll(()=>state.queues.length).toBe(1);await expect(page.getByRole("region",{name:"Retained inspection capture"})).toHaveCount(0);
  expect(state.uploads[1].bytes).toEqual(state.uploads[0].bytes);expect(state.uploads[1].id).toBe(state.uploads[0].id);
});

test("same pinned metadata and late job artefacts refresh without retargeting a draft",async({page})=>{
  const state=await fixture(page);const original=(await selection(page)).target;
  await page.getByRole("button",{name:"Annotate presented frame"}).click();await page.getByLabel("Feedback",{exact:true}).fill("Pinned original concept");
  await page.getByRole("button",{name:"Link completed job image"}).click();
  await page.getByText("Published image artefacts",{exact:true}).click();
  await expect(page.getByText("late-job-image: ready · concept",{exact:true})).toBeVisible();
  state.options.late=true;await page.getByRole("button",{name:"Refresh late associations"}).click();
  await expect(page.getByText(/Pinned version: Refreshed pinned metadata/)).toBeVisible();
  await expect(page.getByText("late-version-image: ready · concept",{exact:true})).toBeVisible();await expect(page.getByText("late-job-image: ready · concept",{exact:true})).toBeVisible();
  expect((await selection(page)).target).toEqual(original);await expect(page.getByLabel("Feedback",{exact:true})).toHaveValue("Pinned original concept");
  expect((await selection(page)).version_id).toBe(version.version_id);expect(state.uploads).toHaveLength(0);expect(state.queues).toHaveLength(0);
});

test("catalogue search uses loaded metadata and exposes provenance without rights gates or unsafe links",async({page})=>{
  const state=await fixture(page,{catalogue:true});await expect(page.getByRole("button",{name:"Unknown version unknown-v1"})).toBeVisible();
  await expect(page.getByRole("link",{name:"https://example.test/assets/lamp"}).first()).toHaveAttribute("rel","noopener noreferrer");
  await expect(page.getByRole("link",{name:"https://example.test/assets/lamp"}).first()).toHaveAttribute("target","_blank");
  const initialReads=state.requests.length;
  for(const query of ["LAMP","glazed","CERAMIC","alice","cc-by","example.test","Museum"]){await page.getByLabel("Search assets").fill(query);await expect(page.getByText("Ceramic lamp",{exact:true})).toBeVisible();await expect(page.getByText(items[1].name,{exact:true})).toHaveCount(0);}
  await page.getByLabel("Search assets").fill("UNKNOWN");
  await expect(page.getByText("javascript:alert(1)",{exact:true})).toBeVisible();expect(await page.getByRole("link").count()).toBe(0);expect(await page.locator("img").count()).toBe(0);
  await expect(page.getByText("Unknown",{exact:true}).first()).toBeVisible();await expect(page.getByRole("button",{name:"Select for item",exact:true})).toBeEnabled();
  await page.getByRole("button",{name:"Unknown version unknown-v1"}).click();
  await expect(page.getByText(/Viewing \(pinned\)/)).toBeVisible();
  expect(state.requests.length).toBe(initialReads);expect(state.requests.every(r=>r.method==="GET")).toBe(true);expect(state.queues).toHaveLength(0);
});
