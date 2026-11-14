import { expect,test,type Page } from "@playwright/test";
import { syntheticVersion } from "./review-fixtures";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { firstFrame,firstJob,firstPng,mapping,proxyBytes,recorded,recordedHash,retainedRoot,secondJob,version } from "./review-readiness-m04";
import { frameCamera } from "../../apps/frontend/src/features/review/frameCamera";
import { cleanTargetRole } from "../../apps/frontend/src/features/review/context";

test.beforeEach(async({browser},info)=>{info.annotations.push({type:"actual-browser-engine",description:browser.version()});});

async function fixture(page:Page,critic=false){
  let release=()=>{};const delayed=new Promise<void>(resolve=>{release=resolve;});
  const saves:unknown[]=[];const mutations:string[]=[];let sameLoads=0;
  await page.route("**/api/v1/**",async route=>{
    const request=route.request(),path=new URL(request.url()).pathname;
    if(!["GET","HEAD"].includes(request.method()))mutations.push(path);
    const json=(body:unknown,status=200)=>route.fulfill({status,json:body});
    if(path.endsWith("/content")){
      if(path.includes("failed-reference"))return route.abort("failed");
      if(path.includes("delayed-reference"))await delayed;
      if(path.includes("same-reference"))sameLoads++;
      const data=await page.evaluate(()=>{const canvas=document.createElement("canvas");canvas.width=640;canvas.height=360;const ctx=canvas.getContext("2d")!;ctx.fillStyle="#123456";ctx.fillRect(0,0,640,360);return canvas.toDataURL("image/png").split(",")[1];});
      return route.fulfill({contentType:"image/png",body:Buffer.from(data,"base64"),headers:{"Cache-Control":"private,max-age=31536000,immutable"}});
    }
    if(path.endsWith("/versions"))return json({schema_version:"1.0",items:[syntheticVersion],next_cursor:null});
    if(path.endsWith("/reviews"))return json({schema_version:"1.0",items:[{schema_version:"1.0",review_id:"concept-review",job_id:"offline-review",status:"ready"}],next_cursor:null});
    if(path.endsWith("/findings"))return json({schema_version:"1.0",items:[{schema_version:"1.0",finding_id:"concept-finding",review_id:"concept-review",project_id:"synthetic",revision:1,state:"unreviewed",target:{origin:"concept",artifact_id:"same-reference",displayed_sha256:"a".repeat(64),dimensions:{width:640,height:360},item_id:syntheticVersion.item_id,version_id:syntheticVersion.version_id,shot_id:null,render_job_id:null,source_frame:null,media_artifact_id:null},observation:"Recorded concept observation",recommendation:"Owner accepted concept instruction",evidence:[{kind:"visual",artifact_id:"same-reference",description:"The same concept, not a render"}]}],next_cursor:null});
    if(path.endsWith("/annotations")){
      if(request.method()==="GET")return json({schema_version:"1.0",items:[],next_cursor:null});
      const body=request.postDataJSON();saves.push(body);
      return json({schema_version:"1.0",operation_id:body.operation_id,resource_id:body.annotation.annotation_id,state:"saved",state_revision:body.expected_revision+1,replayed:false});
    }
    if(path.includes("/uploads/"))return json({schema_version:"1.0",artifact_id:"markup-image",project_id:"synthetic",kind:"image",purpose:"reference",sha256:"c".repeat(64),bytes:500,media_type:"image/png",state:"ready",derived_from:[],dimensions:{width:640,height:360}});
    return json({schema_version:"1.0",error:{code:"not_found",message:path,details:{}}},404);
  });
  await page.goto(`/tests/browser/review-readiness-harness.html${critic?"?critic=1":""}`);
  const state=async()=>JSON.parse(await page.getByTestId("selection").innerText());
  return {state,saves,mutations,get sameLoads(){return sameLoads;},release};
}

test("same decoded PNG changes semantic shot target without another load; annotation/preparer retain B",async({page})=>{
  const proof=await fixture(page);
  await page.getByRole("button",{name:"Shot A / frame A",exact:true}).click();
  await expect(page.getByRole("button",{name:"Annotate presented frame"})).toBeEnabled();
  expect((await proof.state()).target.shot_id).toBe("shot-a");const loads=proof.sameLoads;
  await page.getByRole("button",{name:"Shot B / frame B (same PNG)",exact:true}).click();
  await expect.poll(async()=> (await proof.state()).target?.shot_id).toBe("shot-b");
  await expect(page.getByRole("button",{name:"Annotate presented frame"})).toBeEnabled();
  expect(proof.sameLoads).toBe(loads);expect(proof.mutations).toEqual([]);
  await page.getByRole("button",{name:"Annotate presented frame"}).click();
  await page.getByLabel("Feedback",{exact:true}).fill("shot-b / frame-b shared reference feedback");
  await page.getByRole("button",{name:"pin",exact:true}).click();await page.getByLabel("Pin text",{exact:true}).fill("B, not A");
  const box=(await page.locator(".review-canvas").boundingBox())!;await page.mouse.click(box.x+box.width/2,box.y+box.height/2);
  expect((await proof.state()).attachments).toEqual([{artifact_id:"same-reference",role:"reference"}]);
  await page.getByRole("button",{name:"Save annotation",exact:true}).click();await expect(page.getByRole("button",{name:"Queue feedback"})).toBeEnabled();
  const save=proof.saves[0] as {annotation:{target:{shot_id:string};text:string}};
  expect(save.annotation.target.shot_id).toBe("shot-b");expect(save.annotation.text).toContain("frame-b");
  await page.getByRole("button",{name:"Prepare current context"}).click();
  await expect(page.getByTestId("prepared")).toContainText('"shot_id":"shot-b"');
  const prepared=JSON.parse(await page.getByTestId("prepared").innerText());expect(prepared.annotations[0].target).toEqual(save.annotation.target);expect(prepared.annotations[0].saved_revision).toBe(1);
  expect(prepared.attachments.filter((a:{artifact_id:string})=>a.artifact_id==="same-reference")).toEqual([{artifact_id:"same-reference",role:"reference"}]);
});

test("concept finding has one correctly typed clean image and retains explicit extra reference/markup/mask",async({page})=>{
  const proof=await fixture(page,true);
  await page.getByRole("button",{name:"Load existing reviews"}).click();await page.getByLabel("Review",{exact:true}).selectOption("concept-review");
  await page.getByLabel("Include finding").check();await page.getByRole("button",{name:"Queue selected findings once"}).click();
  await expect(page.getByTestId("prepared")).toContainText("Owner accepted concept instruction");
  const prepared=JSON.parse(await page.getByTestId("prepared").innerText());
  expect(prepared.attachments).toEqual([{artifact_id:"same-reference",role:"concept",use:"Original concept"},{artifact_id:"extra-reference",role:"reference"},{artifact_id:"existing-markup",role:"markup"},{artifact_id:"existing-mask",role:"mask"}]);
  expect(prepared.approved_findings[0].target.origin).toBe("concept");expect(proof.mutations).toEqual([]);
  for(const [origin,role]of [["concept","concept"],["reference","reference"],["proxy_capture","reference"],["blender_render","render"],["sequence_render","render"]] as const)expect(cleanTargetRole({...prepared.target,origin})).toBe(role);
});

test("actual recorded M04 job camera survives frame selection, annotation save, other job and restore",async({page},info)=>{
  const saves:import("../../apps/frontend/src/api/generated/contracts").AnnotationSnapshot[]=[];
  const legacy=structuredClone(recorded.annotations[0]);expect(legacy.camera).toBeNull();
  const pageOf=(items:unknown[])=>({schema_version:"1.0",items,next_cursor:null});
  await page.route("**/readiness-m04-fixture",route=>route.fulfill({json:{version,jobs:recorded.jobs}}));
  await page.route("**/api/v1/**",async route=>{
    const request=route.request(),path=new URL(request.url()).pathname;const id=path.split("/").at(-1)!;
    const json=(body:unknown,status=200)=>route.fulfill({status,json:body});
    if(path.endsWith("/content")){
      const artifactId=path.split("/").at(-2)!;
      if(artifactId===mapping.proxy_artifact_id)return route.fulfill({contentType:"model/gltf-binary",body:proxyBytes});
      if(artifactId.startsWith("markup-"))return route.fulfill({contentType:"image/png",body:firstPng});
      const artifact=recorded.artifacts.find(value=>value.artifact_id===artifactId)!;
      const bytes=readFileSync(`${retainedRoot}/project/.proscenium/artefacts/sha256/${artifact.sha256}/content`);
      expect(createHash("sha256").update(bytes).digest("hex")).toBe(artifact.sha256);
      return route.fulfill({contentType:artifact.media_type,body:bytes});
    }
    if(path.endsWith("/versions"))return json(pageOf([version]));
    if(path.endsWith("/proxy-mapping"))return json(mapping);
    if(path.endsWith("/frames"))return json(pageOf(recorded.frames.filter(frame=>path.includes(frame.job_id))));
    if(path.endsWith("/segments"))return json(pageOf([]));
    if(path.endsWith("/availability")){const job=recorded.jobs.find(value=>path.includes(value.job_id))!;return json({schema_version:"1.0",project_id:job.project_id,job_id:job.job_id,version_id:version.version_id,source_range:job.render_spec!.frames,fps:job.render_spec!.fps,available_ranges:job===firstJob||job===secondJob?[{start:1,end:1}]:[],playable_ranges:[],pending_ranges:[],failed_ranges:[],state_revision:job.state_revision});}
    if(path.endsWith("/annotations")){
      if(request.method()==="GET")return json(pageOf([legacy,...saves]));
      const body=request.postDataJSON();saves.push({...body.annotation,saved_revision:body.expected_revision+1});
      return json({schema_version:"1.0",operation_id:body.operation_id,resource_id:body.annotation.annotation_id,state:"saved",state_revision:body.expected_revision+1,replayed:false});
    }
    if(path.includes("/uploads/")){const bytes=request.postDataBuffer()!;return json({schema_version:"1.0",artifact_id:`markup-${id}`,project_id:version.project_id,kind:"image",purpose:"reference",state:"ready",sha256:createHash("sha256").update(bytes).digest("hex"),bytes:bytes.length,media_type:"image/png",dimensions:firstFrame.dimensions,derived_from:[]});}
    return json(recorded.artifacts.find(artifact=>artifact.artifact_id===id)??{schema_version:"1.0",error:{code:"not_found",message:path,details:{}}},recorded.artifacts.some(a=>a.artifact_id===id)?200:404);
  });
  await page.goto("/tests/browser/review-readiness-harness.html?m04=1");
  const state=async()=>JSON.parse(await page.getByTestId("selection").innerText());
  await page.getByLabel("Shot / render job",{exact:true}).selectOption(firstJob.job_id);
  await expect(page.getByRole("button",{name:"Annotate presented frame"})).toBeEnabled();
  await expect.poll(async()=> (await state()).camera).toEqual(firstJob.render_spec!.camera);
  await page.getByRole("button",{name:"Annotate presented frame"}).click();
  const text="Readiness M04 camera: exact first native image/job";
  await page.getByLabel("Feedback",{exact:true}).fill(text);
  await page.getByRole("button",{name:"Save annotation",exact:true}).click();await expect(page.getByRole("button",{name:"Queue feedback"})).toBeEnabled();
  expect(saves).toHaveLength(1);expect(saves[0].camera).toEqual(firstJob.render_spec!.camera);expect(saves[0].target.render_job_id).toBe(firstJob.job_id);expect(saves[0].target.displayed_sha256).toBe(firstFrame.sha256);
  await page.getByRole("button",{name:"Close draft"}).click();
  await page.getByLabel("Shot / render job",{exact:true}).selectOption(secondJob.job_id);
  await expect.poll(async()=> (await state()).camera).toEqual(secondJob.render_spec!.camera);
  await page.getByText("Saved annotations and local drafts",{exact:true}).click();
  await page.getByRole("button",{name:new RegExp(`${text}.*saved`)}).click();
  await page.getByRole("button",{name:"Prepare current context",exact:true}).click();
  await expect.poll(async()=>JSON.parse(await page.getByTestId("prepared").innerText()).camera).toEqual(firstJob.render_spec!.camera);
  const prepared=JSON.parse(await page.getByTestId("prepared").innerText());expect(prepared.annotations[0].camera).toEqual(firstJob.render_spec!.camera);expect(prepared.target).toEqual(saves[0].target);
  await page.getByRole("button",{name:"Close draft"}).click();
  await page.getByRole("button",{name:"1 3D Inspection",exact:true}).click();
  await expect(page.getByRole("button",{name:"Render this view",exact:true})).toBeEnabled();
  await expect.poll(async()=> (await state()).camera?.aspect).toBe(firstJob.render_spec!.camera!.aspect);
  const inspection=(await state()).camera;firstJob.render_spec!.camera!.camera_to_world.forEach((value,i)=>expect(inspection.camera_to_world[i]).toBeCloseTo(value,10));expect(inspection.projection).toEqual(firstJob.render_spec!.camera!.projection);
  await page.getByRole("button",{name:"2 Keyframes",exact:true}).click();
  await page.getByRole("button",{name:`${legacy.text||legacy.annotation_id} · revision ${legacy.saved_revision}`,exact:true}).click();
  await page.getByRole("button",{name:"Prepare current context",exact:true}).click();
  await expect.poll(async()=>JSON.parse(await page.getByTestId("prepared").innerText()).camera).toBeNull();
  expect(saves).toHaveLength(1);expect(legacy).toEqual(recorded.annotations[0]);
  expect(frameCamera({...saves[0].target,shot_id:"different-shot"},recorded.jobs)).toBeNull();
  await info.attach("actual-M04-camera-context",{contentType:"application/json",body:JSON.stringify({retainedRoot,recordedHash,job_id:firstJob.job_id,image_sha256:firstFrame.sha256,camera:firstJob.render_spec!.camera,annotation:saves[0],prepared,inspection,legacy_camera_preserved_null:true},null,2)});
});

test("new pending URL, wrong dimensions and failed decode cannot inherit readiness; old loads cannot approve new URL",async({page})=>{
  const proof=await fixture(page);const annotate=page.getByRole("button",{name:"Annotate presented frame"});
  await page.getByRole("button",{name:"Shot A / frame A",exact:true}).click();await expect(annotate).toBeEnabled();
  await page.getByRole("button",{name:"Different pending image"}).click();await expect(annotate).toBeDisabled();expect((await proof.state()).target).toBeNull();
  await page.locator(".review-image img").dispatchEvent("load");await expect(annotate).toBeDisabled();
  // Navigate back to the already decoded URL before the old delayed request finishes.
  await page.getByRole("button",{name:"Shot B / frame B (same PNG)",exact:true}).click();await expect(annotate).toBeEnabled();
  proof.release();await page.locator(".review-image img").dispatchEvent("load");await expect.poll(async()=> (await proof.state()).target?.shot_id).toBe("shot-b");
  await page.getByRole("button",{name:"Mismatched dimensions"}).click();await expect(annotate).toBeDisabled();expect((await proof.state()).target).toBeNull();
  await page.getByRole("button",{name:"Broken image"}).click();await expect(annotate).toBeDisabled();await expect(page.getByText(/media_unavailable: failed-reference/)).toBeVisible();expect((await proof.state()).target).toBeNull();
  await page.getByRole("button",{name:"Shot B / frame B (same PNG)",exact:true}).click();await expect(annotate).toBeEnabled();
  await expect.poll(async()=> (await proof.state()).target?.shot_id).toBe("shot-b");expect(proof.mutations).toEqual([]);
});
