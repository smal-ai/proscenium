// Inspector cache eviction is not a lost product upload. Read the actual durable
// artefact by the acknowledged upload ID, without another upload or SDK send.
import fs from 'node:fs/promises';
export async function recover({page,get,save,result,spec,root,sha}) {
  const observed=result.responses.filter(r=>r.method==='PUT'&&r.path.includes('/uploads/')).at(-1);
  if(observed?.status!==201)throw Error('No observed successful actual UI upload');
  const id=observed.path.split('/').at(-1);
  const uploaded=await get(`/api/v1/projects/${spec.project_id}/artifacts/${id}`);
  const original=await fs.readFile(`${root}/upload.png`);
  if(uploaded.artifact_id!==id||uploaded.sha256!==sha(original)||uploaded.bytes!==original.length)throw Error('Durable image bytes differ');
  result.upload=uploaded;result.upload_metadata=uploaded;
  result.upload_receipt_recovery={observed_upload:observed,durable_metadata_read:true,extra_uploads:0};
  await page.screenshot({path:`${root}/ssh-review.png`});
  await save('browser-progress',result);
}
