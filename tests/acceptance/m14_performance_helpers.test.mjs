import test from 'node:test';
import assert from 'node:assert/strict';
import {summary,cadence,processStat,ownedProcesses,visibleGeometry} from './m14_performance_helpers.mjs';

test('nearest-rank quantiles retain real small samples and empty partial results',()=>{
  assert.deepEqual(summary([4,1,3,2]),{count:4,p50:2,p95:4,max:4});
  assert.deepEqual(summary([]),{count:0,p50:null,p95:null,max:null});
  assert.throws(()=>summary([NaN]));
});
test('actual flat-shaded block is visible with ten quantised colours; blank is not',()=>{
  assert.equal(visibleGeometry({quantised_colours:10,different_from_corner:15554,sampled_pixels:360000}),true);
  assert.equal(visibleGeometry({quantised_colours:1,different_from_corner:0,sampled_pixels:360000}),false);
  assert.equal(visibleGeometry({different_from_corner:15554,sampled_pixels:360000}),true);
});
test('cadence uses actual start timestamps, not manufactured target FPS',()=>{
  assert.deepEqual(cadence([{start_ms:10,end_ms:14,draw_calls:1},{start_ms:30,end_ms:39,draw_calls:2}]),{
    interval_ms:{count:1,p50:20,p95:20,max:20},callback_wall_ms:{count:2,p50:4,p95:9,max:9},callback_count:2,draw_calls:3});
});
test('proc stat parsing handles spaces/parentheses in comm and exact start/RSS',()=>{
  const fields=['R','3','12',...Array(16).fill('0'),'999','0','7'];
  const p=processStat(`12 (chrome (renderer)) ${fields.join(' ')}`,['chrome'],4096);
  assert.equal(p.start_ticks,'999');assert.equal(p.rss_bytes,7*4096);assert.equal(p.ppid,3);
});
test('attribution excludes independent processes and PID reuse, retains known orphan',()=>{
  const root={pid:10,start_ticks:'100',pgrp:10,ppid:1};
  const child={pid:11,start_ticks:'101',pgrp:10,ppid:10};
  const other={pid:20,start_ticks:'1',pgrp:20,ppid:1};
  assert.deepEqual(ownedProcesses([root,child,other],root).map(p=>p.pid),[10,11]);
  assert.deepEqual(ownedProcesses([{...root,start_ticks:'500'},other],root),[]);
  assert.deepEqual(ownedProcesses([{...child,ppid:1},other],root,[child]).map(p=>p.pid),[11]);
});
