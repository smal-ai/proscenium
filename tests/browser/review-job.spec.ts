import { expect,test,type Page } from "@playwright/test";
import { generationOutputs,targetJobId } from "../../apps/frontend/src/features/review/jobIdentity";
import type { Artifact } from "../../apps/frontend/src/api/generated/contracts";
import { syntheticVersion,syntheticJobs } from "./review-fixtures";

const png=Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScLbtAAAAABJRU5ErkJggg==","base64");
const artifact=(id:string):Artifact=>({schema_version:"1.0",project_id:"synthetic",artifact_id:id,kind:"image",purpose:id==="reference"?"reference":"concept",state:"ready",sha256:(id==="correction"?"b":"a").repeat(64),bytes:png.length,media_type:"image/png",dimensions:{width:1,height:1},derived_from:id==="conditioning"?[{kind:"encode",input_artifact_ids:["reference"],parameters:{encoding:"RGB PNG",exif_transposed:true}}]:[]});
const state=async(page:Page)=>JSON.parse(await page.getByTestId("selection").innerText());
const selector=(page:Page)=>page.getByLabel("Shot / render job",{exact:true});
async function fixture(page:Page) {
  await page.route("**/api/v1/**",async route=>{
    const request=route.request(),path=new URL(request.url()).pathname,id=path.split("/").at(-1)!;
    expect(request.method()).toBe("GET");
    if(path.endsWith("/content"))return route.fulfill({contentType:"image/png",body:png});
    if(path.endsWith("/versions"))return route.fulfill({json:{schema_version:"1.0",items:[{...syntheticVersion,artifact_ids:["initial","correction","reference"]}],next_cursor:null}});
    if(path.endsWith("/annotations"))return route.fulfill({json:{schema_version:"1.0",items:[],next_cursor:null}});
    if(path.includes("/artifacts/")&&id!=="missing") {
      // Reverse response timing to prove membership, not Promise completion order.
      if(id==="initial")await new Promise(resolve=>setTimeout(resolve,80));
      return route.fulfill({json:id==="metadata"?{...artifact(id),kind:"document",purpose:"metadata"}:artifact(id)});
    }
    return route.fulfill({status:404,json:{schema_version:"1.0",error:{code:"not_found",message:path,details:{}}}});
  });
  await page.goto("/tests/browser/review-job-harness.html");
  await expect(page.getByRole("button",{name:"Annotate presented frame"})).toBeEnabled();
}
async function assertImage(page:Page,id:string,job:string) {
  await expect(selector(page)).toHaveValue(job);
  await expect(page.locator(".review-image img")).toHaveAttribute("src",new RegExp(`/artifacts/${id}/content`));
  await expect.poll(async()=> (await state(page)).target?.artifact_id).toBe(id);
  expect((await state(page)).target.render_job_id).toBeNull();
  expect((await state(page)).target.source_frame).toBeNull();
  expect((await state(page)).target.displayed_sha256).toBe((id==="correction"?"b":"a").repeat(64));
}
test.beforeEach(async({browser},info)=>{info.annotations.push({type:"actual-browser-engine",description:browser.version()});});
test("storyboard jumps and explicit job choices synchronise image, label and captured context",async({page},info)=>{
  await fixture(page);await assertImage(page,"initial","initial-job");
  await selector(page).selectOption("initial-job");
  await page.getByRole("button",{name:"Storyboard correction",exact:true}).click();await assertImage(page,"correction","correction-job");
  expect((await state(page)).range).toEqual({start:3,end:7});
  await selector(page).selectOption("initial-job");await assertImage(page,"initial","initial-job");
  expect((await state(page)).range).toBeNull();
  await selector(page).selectOption("correction-job");await assertImage(page,"correction","correction-job");
  await page.screenshot({path:info.outputPath("correction-job.png")});
});
test("late jobs preserve the selected image, local draft, range and annotation identity",async({page},info)=>{
  await fixture(page);await page.getByRole("button",{name:"Storyboard correction",exact:true}).click();await assertImage(page,"correction","correction-job");
  await page.getByRole("button",{name:"Annotate presented frame"}).click();await page.getByLabel("Feedback",{exact:true}).fill("Pinned correction, not the late job");
  const pinned=structuredClone((await state(page)).target);
  await page.getByRole("button",{name:"Late job arrives"}).click();
  await expect(selector(page).locator("option[value='late-job']")).toHaveCount(1);
  await expect(selector(page)).toHaveValue("correction-job");await expect(selector(page)).toBeDisabled();
  await expect(page.getByLabel("Feedback",{exact:true})).toHaveValue("Pinned correction, not the late job");
  expect((await state(page)).target).toEqual(pinned);expect((await state(page)).range).toEqual({start:3,end:7});
  await page.getByRole("button",{name:"Close draft"}).click();await assertImage(page,"correction","correction-job");
  await page.screenshot({path:info.outputPath("late-job-draft-preserved.png")});
});
test("references, conditioning derivatives, pending and missing results never inherit another job label",async({page},info)=>{
  await fixture(page);
  for(const id of ["pending-job","missing-job"]) {
    await selector(page).selectOption(id);await assertImage(page,"initial","initial-job");
    await expect(page.locator("p[role='status']")).toContainText(`No ready review output for ${id}`);
  }
  await page.getByRole("button",{name:"Storyboard reference",exact:true}).click();await assertImage(page,"reference","");
  await page.getByRole("button",{name:"Late job arrives"}).click();await assertImage(page,"reference","");
  await page.screenshot({path:info.outputPath("reference-no-job.png")});
});
test("membership requires an exact output identity and never resolves ambiguous ownership",()=>{
  const job={...syntheticJobs[0],kind:"image_generation" as const,artifact_ids:["conditioning","initial"]};
  const images=[artifact("conditioning"),artifact("initial")];
  expect(generationOutputs(job,images).map(a=>a.artifact_id)).toEqual(["initial"]);
  const target={origin:"concept" as const,artifact_id:"initial",displayed_sha256:"a".repeat(64),dimensions:{width:1,height:1},render_job_id:null,source_frame:null,shot_id:null,item_id:"scene",version_id:"v1",media_artifact_id:null};
  expect(targetJobId(target,[job],images)).toBe(job.job_id);
  expect(targetJobId({...target,displayed_sha256:"b".repeat(64)},[job],images)).toBeNull();
  expect(targetJobId(target,[job,{...job,job_id:"duplicate"}],images)).toBeNull();
  expect(targetJobId({...target,artifact_id:"conditioning"},[job],images)).toBeNull();
  const composite={...target,origin:"sequence_render" as const};
  expect(targetJobId(composite,[job],images)).toBe(job.job_id);
  expect(targetJobId(composite,[job],images,"explicit-sequence-job")).toBe("explicit-sequence-job");
  expect(targetJobId({...target,origin:"reference"},[job],images,"explicit-sequence-job")).toBeNull();
});
