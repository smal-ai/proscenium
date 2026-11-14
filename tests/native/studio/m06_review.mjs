// Real compiled-application interactions, invoked by the bounded CDP driver.
import assert from 'node:assert/strict';

export async function review({page,root,save,collect,get}){
  await page.reload();
  await page.getByRole('button',{name:'Shots',exact:true}).click();
  await page.locator('.sb-frame').first().waitFor();
  const project=(await get('/api/v1/projects')).items[0];
  const state=()=>page.evaluate(id=>JSON.parse(localStorage.getItem(`proscenium.owner.view.context.${id}`)),project.project_id);
  async function reopenReviewer(){
    await page.getByRole('button',{name:'Assets',exact:true}).click();
    await page.locator('.review-catalogue article').filter({has:page.locator('small',{hasText:/^m06-kite-asset-v1$/})}).getByRole('button').first().click();
    await page.getByRole('button',{name:'Shots',exact:true}).click();
  }
  await reopenReviewer();
  const views=[];
  for(const [index,shotId,frameId,role] of [[0,'kite-rise','kite-rise-start','start'],[1,'kite-rise','kite-rise-middle','intermediate'],[2,'kite-rise','kite-rise-end','end'],[3,'kite-rest','kite-rest-start','start']]){
    if(index===3){
      // Retain the observed shared-image/different-shot W05 readiness defect.
      // Reopen the real asset version before reviewing the second shot. This is
      // an explicit owner navigation workaround, not a passed direct transition.
      await reopenReviewer();
    }
    await page.locator('.sb-frame').nth(index).click();
    await page.getByRole('button',{name:'Annotate presented frame',exact:true}).waitFor();
    await page.waitForFunction(({id,frame})=>{
      const current=JSON.parse(localStorage.getItem(`proscenium.owner.view.context.${id}`)??'null');
      return current?.attachments.some(a=>a.use?.includes(frame));
    },{id:project.project_id,frame:frameId});
    const context=await state();
    assert.equal(context.target.origin,'reference');assert.equal(context.target.shot_id,shotId);
    assert.ok(context.attachments[0].use.includes(`${shotId} / ${frameId} / ${role}:`));
    assert.equal(await page.locator('.review-image img').count(),1);
    views.push({shotId,frameId,role,context});
    await save('role-contexts',views);
    await page.screenshot({path:`${root}/review-${frameId}.png`});
  }
  await save('role-contexts',views);
  await reopenReviewer();
  await page.locator('.sb-frame').first().click();
  const annotate=page.getByRole('button',{name:'Annotate presented frame',exact:true});
  await annotate.click();
  await page.getByRole('button',{name:'pin',exact:true}).click();
  await page.getByLabel('Pin text',{exact:true}).fill('M06 kite-rise start reference');
  const bounds=await page.locator('.review-canvas').boundingBox();assert.ok(bounds);
  const scale=Math.min(bounds.width/640,bounds.height/360);
  await page.mouse.click(bounds.x+(bounds.width-640*scale)/2+340*scale,bounds.y+(bounds.height-360*scale)/2+145*scale);
  await page.getByLabel('Feedback',{exact:true}).fill('M06 kite-rise / kite-rise-start / start reference. Keep this paper silhouette, plan only; no generation or authored scene is requested.');
  const saved=page.waitForResponse(r=>r.request().method()==='POST'&&r.url().endsWith('/annotations'));
  await page.getByRole('button',{name:'Save annotation',exact:true}).click();
  assert.ok((await saved).ok());
  await page.screenshot({path:`${root}/annotation-saved.png`});
  await page.getByRole('button',{name:'Close draft',exact:true}).click();
  await page.locator('.sb-frame').first().click();
  const commandBodies=[];
  async function mutate(name){
    const response=page.waitForResponse(r=>r.request().method()==='POST'&&r.url().endsWith('/selections'));
    await page.getByRole('button',{name,exact:true}).click();const received=await response;
    assert.ok(received.ok(),await received.text());commandBodies.push(received.request().postDataJSON());
    await page.waitForTimeout(300);
  }
  await mutate('Select image');
  await collect('selected-v1');
  const uploaded=page.waitForResponse(r=>r.request().method()==='PUT'&&r.url().includes('/uploads/'));
  await page.getByLabel('Attach images',{exact:true}).setInputFiles(`${root}/owner-reference.png`);
  const alternative=await (await uploaded).json();await save('alternative-upload',alternative);
  const original=JSON.parse(await (await import('node:fs/promises')).readFile(`${root}/reference-upload.json`,'utf8'));
  assert.notEqual(alternative.artifact_id,original.artifact_id);
  await page.getByLabel('Alternative image artefact ID').fill(alternative.artifact_id);
  await page.getByRole('button',{name:'Inspect candidate',exact:true}).click();
  await page.getByText(`Ready reference: ${alternative.artifact_id}`,{exact:true}).waitFor();
  await mutate('Supersede selection');
  await collect('superseded-v1');
  await mutate('Withdraw');
  await collect('withdrawn-v1');
  await page.getByLabel('Alternative image artefact ID').fill(original.artifact_id);
  await page.getByRole('button',{name:'Inspect candidate',exact:true}).click();
  await page.getByText(`Ready reference: ${original.artifact_id}`,{exact:true}).waitFor();
  await mutate('Select image');
  await save('selection-commands',commandBodies);
  await page.reload();await page.getByRole('button',{name:'Shots',exact:true}).click();
  await page.locator('.sb-frame').first().waitFor();
  assert.match(await page.locator('.sb-frame').first().innerText(),/owner_selected: yes/);
  await page.locator('.sb-frame').first().click();
  await collect('before-revision');
  await page.screenshot({path:`${root}/owner-selection-reloaded.png`});
  assert.equal((await get(`/api/v1/projects/${project.project_id}/jobs`)).items.length,0);
}
