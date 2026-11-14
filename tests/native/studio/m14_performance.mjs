// Bounded software-WebGL component measurement. No backend/native/model startup.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import { createReadStream } from 'node:fs';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { execFileSync, spawn } from 'node:child_process';
import { setTimeout as pause } from 'node:timers/promises';
import { chromium } from '@playwright/test';
import { build } from 'vite';
import { cadence, digest, glbWorkload, ownedProcesses, processStat, visibleGeometry } from '../../acceptance/m14_performance_helpers.mjs';

const repo=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../../..');
const root=path.resolve(process.argv[3]??'');
assert.ok(root.startsWith(`${os.homedir()}/.cache/proscenium-development/milestones/m14-performance-`));
assert.ok(root.endsWith('-b'),'Only the newly authorised private -b allocation is writable');
const limits={seconds:240,min_host_available_bytes:4*1024**3,max_browser_summed_rss_bytes:4*1024**3};
const json=async file=>JSON.parse(await fs.readFile(file,'utf8'));
const write=(name,value)=>fs.writeFile(path.join(root,name),JSON.stringify(value,null,2)+'\n');
const git=(...args)=>execFileSync('git',args,{cwd:repo,encoding:'utf8'}).trim();
const sourceNames=['tests/native/studio/m14_performance.mjs','tests/acceptance/m14_performance_helpers.mjs',
  'tests/acceptance/m14_performance_helpers.test.mjs','tests/acceptance/m14_performance_fixture.tsx',
  'tests/acceptance/m14_performance.html','apps/frontend/src/features/review/ProxyView.tsx',
  'apps/frontend/src/features/review/media.ts','package-lock.json'];
async function fingerprints(){return Object.fromEntries(await Promise.all(sourceNames.map(async name=>[name,digest(await fs.readFile(path.join(repo,name)))])));}
async function memory(){
  const text=await fs.readFile('/proc/meminfo','utf8');
  return Number(text.match(/^MemAvailable:\s+(\d+)/m)[1])*1024;
}
const pageSize=Number(execFileSync('getconf',['PAGESIZE'],{encoding:'utf8'}).trim());
async function proc(pid){try{return processStat(await fs.readFile(`/proc/${pid}/stat`,'utf8'),
  (await fs.readFile(`/proc/${pid}/cmdline`,'utf8')).split('\0').filter(Boolean),pageSize);}catch{return null;}}
async function allProcesses(){
  const names=(await fs.readdir('/proc')).filter(name=>/^\d+$/.test(name));
  return (await Promise.all(names.map(proc))).filter(Boolean);
}
async function prepare(){
  assert.ok(await memory()>=4*1024**3,'Four GiB headroom unavailable');
  await fs.mkdir(root,{recursive:false,mode:0o700});
  await fs.mkdir(`${root}/site/fixtures`,{recursive:true});
  const native=await json(`${repo}/docs/evidence/geometry-nodes/final-run.json`);
  const heavy=await json(`${repo}/docs/evidence/m10/retained-files.json`);
  const plans=[];
  for(const name of ['small','heavy']){
    const records=[];
    for(const [kind,suffix] of [['proxy','.glb'],['proxy_map','-map.json']]){
      let original,expected,size;
      if(name==='small'){
        const artifact=native.artifacts.find(a=>a.kind===kind);expected=artifact.sha256;
        original=`${native.retained_root}/project/.proscenium/artefacts/sha256/${expected}/content`;
      }else{
        const record=heavy.files.find(row=>row.path.endsWith(kind==='proxy'?'/proxy.glb':'/proxy-map.json'));
        original=record.path;expected=record.sha256;size=record.bytes;
        assert.ok(original.startsWith('/home/quintin/.cache/proscenium-development/milestones/m10-asset-a/'));
      }
      const bytes=await fs.readFile(original);assert.equal(digest(bytes),expected);
      if(size!==undefined)assert.equal(bytes.length,size);
      const destination=`fixtures/${name}${suffix}`;
      await fs.writeFile(`${root}/site/${destination}`,bytes,{flag:'wx',mode:0o600});
      records.push({kind,original,sha256:expected,bytes:bytes.length,destination});
    }
    const bytes=await fs.readFile(`${root}/site/fixtures/${name}.glb`);
    const workload=glbWorkload(bytes);
    if(name==='heavy'){
      assert.equal(workload.bytes,90077656);assert.equal(workload.triangles,1304618);
      assert.equal(workload.export_position_records,3100876);
    }else assert.equal(workload.triangles,12);
    plans.push({name,records,workload});
  }
  await build({root:repo,configFile:false,build:{outDir:`${root}/site`,emptyOutDir:false,
    rollupOptions:{input:`${repo}/tests/acceptance/m14_performance.html`}},
    esbuild:{jsx:'automatic'}});
  const prepared={base_commit:'544e1b15e86c51a19f398598a601b791a212e22a',build_commit:git('rev-parse','HEAD'),
    sources:await fingerprints(),cases:plans,page_size:pageSize,
    component_scope:'Compiled fixture imports unmodified maintained ProxyView, including useGLTF, Canvas, OrbitControls, CameraObserver and CaptureBinding. Not a complete studio/backend journey or shell benchmark.',
    build_files:await hashesUnder(`${root}/site`,false)};
  await write('prepared.json',prepared);console.log(JSON.stringify({root,cases:plans},null,2));
}
async function hashesUnder(directory,excludeFixtures){
  const result={};for(const item of await fs.readdir(directory,{withFileTypes:true})){
    if(excludeFixtures&&item.name==='fixtures')continue;
    const target=path.join(directory,item.name);
    if(item.isDirectory())for(const [name,value] of Object.entries(await hashesUnder(target,false)))result[`${item.name}/${name}`]=value;
    else result[item.name]=digest(await fs.readFile(target));
  }return result;
}

// Observation-only: native rAF and GL calls are forwarded unchanged. No gl.finish,
// timer-query result, fake frame, target-rate sampling or camera substitution.
function instrument(){
  const trace=window.__m14Trace={phase:'idle',rows:[],current:null,total_draw_calls:0,raf_callbacks:0};
  const original=requestAnimationFrame;
  window.requestAnimationFrame=function(callback){return original.call(window,timestamp=>{
    const row={raf_timestamp_ms:timestamp,start_ms:performance.now(),draw_calls:0};
    trace.raf_callbacks++;
    trace.current=row;
    try{return callback(timestamp);}finally{
      row.end_ms=performance.now();trace.current=null;
      if(trace.phase==='steady'&&row.draw_calls&&trace.rows.length<10000)trace.rows.push(row);
    }
  });};
  for(const prototype of [WebGLRenderingContext.prototype,WebGL2RenderingContext.prototype]){
    for(const method of ['drawElements','drawArrays','drawElementsInstanced','drawArraysInstanced']){
      if(!Object.hasOwn(prototype,method))continue;
      const draw=prototype[method];prototype[method]=function(...args){
        trace.total_draw_calls++;if(trace.current)trace.current.draw_calls++;return Reflect.apply(draw,this,args);
      };
    }
  }
}

async function measure(){
  const prepared=await json(`${root}/prepared.json`);
  assert.deepEqual(await fingerprints(),prepared.sources,'Preflight source changed; rebuild before measurement');
  assert.deepEqual(await hashesUnder(`${root}/site`,false),prepared.build_files);
  assert.ok(await memory()>=4*1024**3,'Four GiB headroom unavailable; do not wait');
  await fs.writeFile(`${root}/measurement-reservation.json`,JSON.stringify({allocation:'authorised-new-browser-b',limit_profiles:1,limit_cases:2,limits,started:new Date().toISOString()}),{flag:'wx',mode:0o600});
  const began=performance.now(),startedNs=process.hrtime.bigint().toString();
  const journal={status:'running',started_at:new Date().toISOString(),started_monotonic_ns:startedNs,
    implementation_commit:git('rev-parse','HEAD'),prepared,limits,
    identities:{os:await fs.readFile('/etc/os-release','utf8'),kernel:os.release(),arch:os.arch(),
      cpu_model:os.cpus()[0].model,logical_cpus:os.cpus().length,node:process.version},
    http:[],resources:[],processes:{},cases:[],console_errors:[],observer_failures:[],cleanup:{}};
  await write('configured-allocation.json',{allocation:'authorised-new-browser-b',limits,source_hashes:prepared.sources,configured_before_browser_launch:true});
  let browser,chrome,rootIdentity,monitor,busy=false,stopReason=null,server;
  const known=new Map();
  const liveOwned=async()=>rootIdentity?ownedProcesses(await allProcesses(),rootIdentity,[...known.values()]):[];
  async function terminate(reason){
    if(stopReason)return;stopReason=reason;journal.guard_stop=reason;
    if(rootIdentity){const current=await proc(rootIdentity.pid);
      if(current?.start_ticks===rootIdentity.start_ticks)process.kill(-rootIdentity.pid,'SIGTERM');}
  }
  const hard=setTimeout(()=>void terminate('235-second safety boundary, before 240-second total limit'),235000);
  async function sample(){
    if(busy)return;busy=true;
    try{
      const live=await liveOwned();
      for(const p of live){known.set(`${p.pid}:${p.start_ticks}`,p);journal.processes[`${p.pid}:${p.start_ticks}`]??=p;}
      const available=await memory(),rss=live.reduce((sum,p)=>sum+p.rss_bytes,0);
      journal.resources.push({elapsed_ms:performance.now()-began,monotonic_ns:process.hrtime.bigint().toString(),
        host_available_bytes:available,load_average:os.loadavg(),browser_summed_rss_bytes:rss,
        processes:live.map(p=>({pid:p.pid,start_ticks:p.start_ticks,rss_bytes:p.rss_bytes})),
        harness_rss_bytes:process.memoryUsage().rss});
      if(available<4*1024**3)await terminate('host memory below four-GiB guard');
      if(rss>limits.max_browser_summed_rss_bytes)await terminate('attributed browser summed RSS exceeded NEW four-GiB guard');
    }finally{busy=false;}
  }
  try{
    server=http.createServer(async(req,res)=>{
      const beganRequest=process.hrtime.bigint();
      const pathname=new URL(req.url,'http://127.0.0.1').pathname;
      const relative=pathname==='/'?'tests/acceptance/m14_performance.html':decodeURIComponent(pathname.slice(1));
      const target=path.resolve(root,'site',relative);
      if(!target.startsWith(`${root}/site/`)){res.writeHead(403).end();return;}
      try{
        const stat=await fs.stat(target);if(!stat.isFile())throw new Error('not file');
        const types={'.html':'text/html','.js':'text/javascript','.css':'text/css','.json':'application/json','.glb':'model/gltf-binary'};
        res.writeHead(200,{'Content-Type':types[path.extname(target)]??'application/octet-stream','Content-Length':stat.size,'Cache-Control':'no-store'});
        let bodyBytes=0;
        const stream=createReadStream(target);stream.on('data',chunk=>{bodyBytes+=chunk.length;});
        res.on('finish',()=>journal.http.push({url:pathname,body_bytes:bodyBytes,started_monotonic_ns:beganRequest.toString(),
          finished_monotonic_ns:process.hrtime.bigint().toString(),duration_ms:Number(process.hrtime.bigint()-beganRequest)/1e6}));
        stream.pipe(res);
      }catch{res.writeHead(404).end();}
    });
    await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
    const port=server.address().port;journal.server={pid:process.pid,identity:await proc(process.pid),port,
      label:'One owned loopback static fixture server in the harness process, no product backend or SDK'};
    const binary=path.join(os.homedir(),'.cache/ms-playwright/chromium-1255/chrome-linux64/chrome');
    const argv=['--headless=new','--no-sandbox','--disable-dev-shm-usage','--no-first-run','--no-default-browser-check',
      '--disable-breakpad','--disable-crash-reporter','--remote-debugging-port=0',`--user-data-dir=${root}/browser-profile`,
      '--window-size=1920,1080','--use-angle=swiftshader','--enable-unsafe-swiftshader','--disable-background-networking','about:blank'];
    const log=await fs.open(`${root}/chromium.log`,'wx',0o600);
    chrome=spawn(binary,argv,{detached:true,stdio:['ignore',log.fd,log.fd]});await log.close();
    rootIdentity=await proc(chrome.pid);assert.ok(rootIdentity);journal.browser_root=rootIdentity;
    monitor=setInterval(()=>void sample().catch(error=>void terminate(`resource monitor: ${error.message}`)),200);
    await sample();
    let cdp;
    for(let i=0;i<500;i++){
      if(stopReason)throw new Error(stopReason);
      try{cdp=Number((await fs.readFile(`${root}/browser-profile/DevToolsActivePort`,'utf8')).split('\n')[0]);break;}catch{await pause(20);}
    }
    assert.ok(cdp,'Owned browser failed readiness; no second launch');
    browser=await chromium.connectOverCDP(`http://127.0.0.1:${cdp}`);
    journal.identities.chromium=browser.version();
    const context=browser.contexts()[0];await context.addInitScript(instrument);
    const page=context.pages()[0]??await context.newPage();await page.setViewportSize({width:1920,height:1080});
    page.setDefaultTimeout(45000);
    page.on('pageerror',error=>journal.console_errors.push(String(error)));
    await page.goto(`http://127.0.0.1:${port}/`,{waitUntil:'networkidle'});
    journal.viewport=await page.evaluate(()=>({width:innerWidth,height:innerHeight,dpr:devicePixelRatio}));
    assert.deepEqual(journal.viewport,{width:1920,height:1080,dpr:1});
    for(const fixture of prepared.cases){
      if(stopReason)break;
      const record={name:fixture.name,status:'loading',workload:fixture.workload,trace:[],case_start_elapsed_ms:performance.now()-began};journal.cases.push(record);
      await page.getByRole('button',{name:`Load ${fixture.name}`,exact:true}).click();
      await page.waitForFunction(name=>window.__m14.readyCase===name,fixture.name,{timeout:60000});
      record.loader=await page.evaluate(name=>{
        const data=window.__m14,entry=performance.getEntriesByName(`${location.origin}/fixtures/${name}.glb`).at(-1);
        return {load_start_ms:data.loadStart,ready_ms:data.readyAt,loader_ready_ms:data.readyAt-data.loadStart,
          ready_definition:'Maintained CaptureBinding ready after useGLTF decode, scene clone and model layout effect; not GPU completion',
          resource:entry?.toJSON()};
      },fixture.name);
      const canvas=page.locator('.review-proxy canvas');
      record.graphics=await canvas.evaluate(element=>{
        const gl=element.getContext('webgl2'),ext=gl.getExtension('WEBGL_debug_renderer_info');
        return {vendor:gl.getParameter(gl.VENDOR),version:gl.getParameter(gl.VERSION),
          shading_language:gl.getParameter(gl.SHADING_LANGUAGE_VERSION),unmasked_vendor:ext?gl.getParameter(ext.UNMASKED_VENDOR_WEBGL):null,
          renderer:ext?gl.getParameter(ext.UNMASKED_RENDERER_WEBGL):null,intrinsic:[element.width,element.height],css:element.getBoundingClientRect().toJSON()};
      });
      assert.ok(record.graphics.renderer.includes('SwiftShader'));
      async function capture(label){
        const data=await page.evaluate(async()=>{
          const captured=window.__m14.capture(),blob=await captured.png;
          const url=await new Promise(resolve=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result);reader.readAsDataURL(blob);});
          const bitmap=await createImageBitmap(blob),canvas=document.createElement('canvas');canvas.width=bitmap.width;canvas.height=bitmap.height;
          const ctx=canvas.getContext('2d');ctx.drawImage(bitmap,0,0);const rgba=ctx.getImageData(0,0,canvas.width,canvas.height).data;
          const colours=new Set();let different=0;
          for(let i=0;i<rgba.length;i+=16){colours.add(`${rgba[i]>>3},${rgba[i+1]>>3},${rgba[i+2]>>3}`);
            if(Math.abs(rgba[i]-rgba[0])+Math.abs(rgba[i+1]-rgba[1])+Math.abs(rgba[i+2]-rgba[2])>30)different++;}
          return {url,camera:captured.camera,dimensions:captured.dimensions,quantised_colours:colours.size,
            sampled_pixels:rgba.length/16,different_from_corner:different};
        });
        const bytes=Buffer.from(data.url.split(',')[1],'base64');delete data.url;
        await fs.writeFile(`${root}/${fixture.name}-${label}.png`,bytes);
        return {...data,sha256:digest(bytes)};
      }
      record.before_orbit=await capture('before');
      record.draws_before_orbit=await page.evaluate(()=>window.__m14Trace.total_draw_calls);
      if(!visibleGeometry(record.before_orbit)){
        journal.observer_failures.push({case:fixture.name,stage:'visibility',observation:record.before_orbit,at:new Date().toISOString(),recovery:'Wait for actual next two animation callbacks and recapture in same browser'});
        await write('observer-failures.json',journal.observer_failures);
        await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
        record.before_orbit=await capture('before-recovered');
      }
      assert.ok(visibleGeometry(record.before_orbit),'No visible non-uniform geometry');
      assert.ok(record.draws_before_orbit>0,'No actual WebGL draws observed');
      const box=await canvas.boundingBox();
      const x=box.x+box.width*.72,y=box.y+box.height*.52;
      await page.mouse.move(x,y);await page.mouse.down();await page.mouse.move(x+55,y+23,{steps:8});await page.mouse.up();
      record.after_orbit=await capture('after');
      if(record.before_orbit.sha256===record.after_orbit.sha256||JSON.stringify(record.before_orbit.camera.camera_to_world)===JSON.stringify(record.after_orbit.camera.camera_to_world)){
        journal.observer_failures.push({case:fixture.name,stage:'orbit',before:record.before_orbit,after:record.after_orbit,at:new Date().toISOString(),recovery:'Additional real pointer orbit and recapture in same browser'});
        await write('observer-failures.json',journal.observer_failures);
        await page.mouse.move(x,y);await page.mouse.down();await page.mouse.move(x+90,y+35,{steps:10});await page.mouse.up();
        record.after_orbit=await capture('after-recovered');
      }
      assert.notEqual(record.before_orbit.sha256,record.after_orbit.sha256,'Orbit did not change actual pixels');
      assert.notDeepEqual(record.before_orbit.camera.camera_to_world,record.after_orbit.camera.camera_to_world);
      await page.screenshot({path:`${root}/${fixture.name}-fullhd.png`});
      record.status='sampling';record.steady_started_at=new Date().toISOString();
      record.steady_start_elapsed_ms=performance.now()-began;
      record.camera_changes_before_sampling=await page.evaluate(()=>window.__m14.cameraChanges);
      await page.evaluate(()=>{window.__m14Trace.rows=[];window.__m14Trace.phase='steady';});
      await page.mouse.move(x,y);await page.mouse.down();
      const target=fixture.name==='small'?120:60;record.requested_callbacks=target;
      const caseBegan=performance.now();let step=0;
      while(performance.now()-caseBegan<65000&&!stopReason){
        await page.mouse.move(x+45*Math.sin(step*.12),y+23*Math.cos(step*.12));step++;
        if(step%5===0){
          record.trace=await page.evaluate(()=>window.__m14Trace.rows);
          await write('progress.json',journal);
          if(record.trace.length>=target)break;
        }
      }
      await page.mouse.up();
      record.trace=await page.evaluate(()=>{window.__m14Trace.phase='idle';return window.__m14Trace.rows;});
      record.steady_finished_at=new Date().toISOString();record.mouse_moves=step;
      record.case_end_elapsed_ms=performance.now()-began;
      record.observer_totals=await page.evaluate(()=>({draw_calls:window.__m14Trace.total_draw_calls,raf_callbacks:window.__m14Trace.raf_callbacks,camera_changes:window.__m14.cameraChanges}));
      record.cadence=cadence(record.trace);
      record.status=record.trace.length>=target?'observed_requested_sample_count':'partial_callback_count';
      if(record.status.startsWith('partial')){stopReason='Low cadence: bounded case interval exhausted';journal.guard_stop=stopReason;break;}
      await sample();await write('progress.json',journal);
    }
    journal.status=stopReason?'partial_guard_stop':'two_retained_cases_measured';
  }catch(error){journal.status=stopReason?'partial_guard_stop':'partial_error';journal.error=String(error.stack??error);}
  finally{
    clearTimeout(hard);clearInterval(monitor);
    try{if(browser)await Promise.race([browser.close(),pause(1500)]);}catch{}
    const before=await liveOwned();journal.cleanup.before_forced_cleanup=before;
    for(const original of before){const actual=await proc(original.pid);if(actual?.start_ticks===original.start_ticks&&actual.state!=='Z'){
      try{process.kill(original.pid,'SIGTERM');}catch{}}}
    if(chrome)await Promise.race([new Promise(resolve=>chrome.exitCode!==null?resolve():chrome.once('exit',resolve)),pause(500)]);
    const remaining=await liveOwned();
    for(const original of remaining){const actual=await proc(original.pid);if(actual?.start_ticks===original.start_ticks&&actual.state!=='Z'){
      try{process.kill(original.pid,'SIGKILL');}catch{}}}
    journal.cleanup.after=await liveOwned();
    journal.cleanup.root_after=rootIdentity?await proc(rootIdentity.pid):null;
    journal.cleanup.owned_live_after=journal.cleanup.after.filter(p=>!['Z','X'].includes(p.state));
    journal.cleanup.server_closed=server?await new Promise(resolve=>{server.close(()=>resolve(true));server.closeAllConnections();}):true;
    journal.source_rehash_after=[];
    for(const fixture of prepared.cases)for(const record of fixture.records){
      const actual=digest(await fs.readFile(record.original));
      journal.source_rehash_after.push({path:record.original,sha256:actual,unchanged:actual===record.sha256});
    }
    journal.elapsed_seconds=(performance.now()-began)/1000;
    journal.finished_at=new Date().toISOString();
    await write('result.json',journal);
  }
  console.log(JSON.stringify({status:journal.status,seconds:journal.elapsed_seconds,
    cases:journal.cases.map(record=>({name:record.name,status:record.status,loader:record.loader,cadence:record.cadence})),
    cleanup:journal.cleanup},null,2));
}
if(process.argv[2]==='prepare')await prepare();
else if(process.argv[2]==='measure')await measure();
else throw new Error('Usage: node tests/native/studio/m14_performance.mjs prepare|measure FRESH_OWNED_ROOT');
