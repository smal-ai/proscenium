import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';

export const digest = bytes => createHash('sha256').update(bytes).digest('hex');
export function visibleGeometry(stats) {
  // Flat shaded primitive faces need not contain many quantised colours. Require
  // real non-uniform pixels, not an arbitrary palette size or aesthetic score.
  return Number.isFinite(stats.different_from_corner)&&stats.different_from_corner>100
    &&stats.different_from_corner<=stats.sampled_pixels;
}
export function summary(values) {
  if (!values.length) return {count:0,p50:null,p95:null,max:null};
  assert.ok(values.every(value=>Number.isFinite(value)&&value>=0));
  const sorted=[...values].sort((a,b)=>a-b);
  return {count:sorted.length,p50:sorted[Math.ceil(sorted.length*.5)-1],
    p95:sorted[Math.ceil(sorted.length*.95)-1],max:sorted.at(-1)};
}
export function cadence(rows) {
  return {interval_ms:summary(rows.slice(1).map((row,i)=>row.start_ms-rows[i].start_ms)),
    callback_wall_ms:summary(rows.map(row=>row.end_ms-row.start_ms)),
    callback_count:rows.length,draw_calls:rows.reduce((sum,row)=>sum+row.draw_calls,0)};
}
export function processStat(text, argv, pageSize) {
  const pid=Number(text.slice(0,text.indexOf(' ')));
  const fields=text.slice(text.lastIndexOf(')')+2).trim().split(/\s+/);
  return {pid,ppid:Number(fields[1]),pgrp:Number(fields[2]),state:fields[0],
    start_ticks:fields[19],rss_bytes:Number(fields[21])*pageSize,argv};
}
export function ownedProcesses(all, root, known=[]) {
  const rootLive=all.find(p=>p.pid===root.pid&&p.start_ticks===root.start_ticks);
  const selected=new Map();
  for (const p of all) {
    if (known.some(k=>k.pid===p.pid&&k.start_ticks===p.start_ticks)
      || (rootLive&&p.pgrp===root.pid&&BigInt(p.start_ticks)>=BigInt(root.start_ticks))) selected.set(p.pid,p);
  }
  if(rootLive)selected.set(root.pid,rootLive);
  let changed=true;
  while(changed){changed=false;for(const p of all){if(!selected.has(p.pid)&&selected.has(p.ppid)){
    selected.set(p.pid,p);changed=true;
  }}}
  return [...selected.values()];
}
export function glbWorkload(bytes) {
  assert.equal(bytes.readUInt32LE(0),0x46546c67);
  assert.equal(bytes.readUInt32LE(8),bytes.length);
  const doc=JSON.parse(bytes.subarray(20,20+bytes.readUInt32LE(12)).toString());
  let triangles=0,positions=0;
  for(const mesh of doc.meshes)for(const primitive of mesh.primitives){
    assert.equal(primitive.mode??4,4);
    triangles+=doc.accessors[primitive.indices].count/3;
    positions+=doc.accessors[primitive.attributes.POSITION].count;
  }
  assert.ok(Number.isInteger(triangles));
  return {bytes:bytes.length,triangles,export_position_records:positions,meshes:doc.meshes.length};
}
