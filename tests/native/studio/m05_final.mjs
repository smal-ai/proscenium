import assert from 'node:assert/strict';

export async function finish({page,root,collect,get,save,select}){
 const seed=JSON.parse(await (await import('node:fs/promises')).readFile(`${root}/public-seed.json`,'utf8'));
 const before=JSON.parse(await (await import('node:fs/promises')).readFile(`${root}/public-before-adoption.json`,'utf8'));
 await select('asset-companion-v1');await select('asset-cube-v3');
 await page.locator('.review-image img').waitFor();
 await page.waitForFunction(()=>Object.keys(localStorage).filter(k=>k.startsWith('proscenium.owner.view.context.')).some(k=>JSON.parse(localStorage.getItem(k))?.target?.version_id==='asset-cube-v3'));
 const final=await collect('final');
 assert.equal(final.requests.items.length,3);
 assert.equal(final.jobs.items.length,3);
 assert.deepEqual(final.versions['scene-main'],seed.versions['scene-main']);
 assert.deepEqual(final.versions['scene-alternate'],seed.versions['scene-alternate']);
 assert.deepEqual(final.versions['asset-companion'],seed.versions['asset-companion']);
 assert.equal(final.versions['asset-cube'].items.length,3);
 assert.equal(final.view.context.target.origin,'blender_render');
 assert.equal(final.view.context.target.version_id,'asset-cube-v3');
 assert.deepEqual(final.jobs,before.jobs);
 await page.screenshot({path:`${root}/final-asset-review.png`});
 await select('asset-cube-v1');await page.screenshot({path:`${root}/retained-v1-history.png`});
 await select('asset-cube-v3');await page.reload();
 await page.getByRole('textbox',{name:'Message Copilot',exact:true}).waitFor();
 await page.locator('.review-image img').waitFor();
 const reopened=await collect('reopened');
 assert.equal(reopened.requests.items.length,3);
 assert.deepEqual(reopened.jobs,final.jobs);
 await save('reopen-observation',{requests_before:3,requests_after:reopened.requests.items.length,jobs_before:3,jobs_after:reopened.jobs.items.length,automatic_resume_sent:false});
}
