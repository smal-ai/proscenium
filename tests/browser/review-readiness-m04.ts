/** Read-only records/media from the accepted M04 run, not another native run. */
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import type { AnnotationSnapshot, Artifact, FrameReceipt, JobRecord, ProxyMapping, SourceClosure, VersionRecord } from "../../apps/frontend/src/api/generated/contracts";

export const retainedRoot="/home/quintin/.cache/proscenium-development/milestones/m04-camera-20261114-d";
export const recorded: {jobs:JobRecord[];frames:FrameReceipt[];artifacts:Artifact[];annotations:AnnotationSnapshot[]} = JSON.parse(readFileSync(`${retainedRoot}/public-final.json`,"utf8"));
export const recordedHash=createHash("sha256").update(readFileSync(`${retainedRoot}/public-final.json`)).digest("hex");
if(recordedHash!=="015dc87729f69225a68c1275c232ea96a8568402a9ad1fbaf48a693480d2ec98")throw new Error("Accepted M04 record fingerprint changed");
const native: {closure:SourceClosure;proxy_mapping:ProxyMapping;jobs:{job:JobRecord;root:string}[]} = JSON.parse(readFileSync("/tmp/opencode/proscenium-w03-native-a.json","utf8"));
export const mapping=native.proxy_mapping;
export const proxyBytes=readFileSync(`${native.jobs.find(value=>value.job.render_spec?.purpose==="proxy")!.root}/proxy.glb`);
export const firstJob=recorded.jobs.find(job=>job.job_id==="preview-87a00e30a18115684b0dd3ee2770c9d532cbce24f4854f9e6e86693959f6907c")!;
export const secondJob=recorded.jobs.find(job=>job.job_id==="preview-c0c5181fc21645b8e7bd48766c46c1540e570b555e04032af4eefd2918a0ea66")!;
export const firstFrame=recorded.frames.find(frame=>frame.job_id===firstJob.job_id)!;
export const firstPng=readFileSync(`${retainedRoot}/project/.proscenium/artefacts/sha256/${firstFrame.sha256}/content`);
if(createHash("sha256").update(firstPng).digest("hex")!==firstFrame.sha256)throw new Error("Accepted M04 PNG fingerprint changed");
export const version:VersionRecord={schema_version:"1.0",project_id:firstJob.project_id,item_id:firstJob.target.version!.item_id,version_id:firstJob.target.version!.version_id,kind:"scene",name:"Retained M04 source fixture",publication_id:"readiness-fixture-only",source_commit:"9d471f07cb1330e02a9bae6e662a4abdcf85a917",source:native.closure,artifact_ids:[],preview_requests:[],preview_job_ids:recorded.jobs.map(job=>job.job_id),created_at:"2026-11-14T00:00:00Z",actor:{kind:"system",id:"read-only-M04-fixture"}};
