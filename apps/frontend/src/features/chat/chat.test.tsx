import assert from "node:assert/strict";
import { test } from "node:test";
import { renderToStaticMarkup } from "react-dom/server";
import type { ActivityEventData, ApiClient, Artifact, Event, ProjectSnapshot, RequestRecord, ResumeOperationStatus, RetainedArtifactSummary } from "../../api";
import { emptySelection } from "../../api/studio";
import { cleanTargetContext, contextCount, prepareSubmissionContext, resolveTarget } from "./target";
import { canNavigateHistory, promptHistory, stepHistory } from "./promptHistory";
import { assistantState, AssistantStatus } from "./AssistantStatus";
import { busyAgent, nativeAgents } from "./agents";
import { compareConversationEntries, Conversation } from "./Conversation";
import { OutputCards } from "./OutputCards";
import { awaitResume } from "./resume";
import { steerWorkId, stopWorkId } from "./controls";
import { SendControl } from "./SendControl";

const api = { call: () => { throw new Error("Unconfigured API operation in offline test"); } } as unknown as ApiClient;
const ref = { item_id: "asset-a", version_id: "version-old" };
const receipt = { state: "queued", state_revision: 1 };
const request = (request_id: string, accepted_at: string, text: string, type = "queue") => ({ project_id: "project", request_id, accepted_at, receipt, submission: { type, text, context_id: "context", project_id: "project", request_id } }) as RequestRecord;
const snapshot = (state = "running", fenced = false) => ({ project: { project_id: "project", state: "active" }, active_work: { work_id: "work", state, native_session_id: "session" }, pending_decisions: [], execution: { fenced, hold_reason: fenced ? "Owner stopped work" : null, active_agents: [], work_outputs: [] }, items: [] }) as unknown as ProjectSnapshot;
const event = (sequence: number, event_type: Event["event_type"], payload: object, occurred_at = "2026-11-15T10:00:00Z") => ({ sequence, event_type, payload, occurred_at, project_id: "project", actor: { kind: "lead", id: "lead" }, native_session_id: "session", request_id: "request", work_id: "work" }) as Event;
const activity: ActivityEventData = { invocation_id: "invocation", name: "image specialist", state: "running", summary: "", artifact_ids: [], model_id: "reported-model", display_name: "Prepare concept" };

test("explicit target context strips Review media and retains the older version pin", async () => {
  const selection = { ...emptySelection(), ...ref, attachments: [{ artifact_id: "viewport", role: "render" as const }], selected_objects: [{ object_id: "object", instance_id: "instance" }] };
  let prepared = 0;
  const context = await prepareSubmissionContext({ target: { kind: "item", version: ref }, selection, references: [ref], attachments: [{ artifact_id: "chosen", role: "reference" }] }, async () => { prepared++; return selection; });
  assert.equal(prepared, 0);
  assert.deepEqual(context, cleanTargetContext({ kind: "item", version: ref }, [ref], [{ artifact_id: "chosen", role: "reference" }]));
  assert.deepEqual(context.selected_objects, []);
  assert.equal(context.version_id, "version-old");
  assert.equal(resolveTarget("current_view", emptySelection()).kind, "project");
});

test("current-view capture freezes target and attachments before asynchronous preparation", async () => {
  const selection = { ...emptySelection(), ...ref };
  const attachments = [{ artifact_id: "chosen", role: "reference" as const }];
  let complete!: (value: typeof selection) => void;
  const captured = structuredClone(selection);
  const promise = prepareSubmissionContext({ target: "current_view", selection, references: [ref], attachments }, () => new Promise(resolve => { complete = resolve; }));
  selection.item_id = "other"; selection.version_id = "newer"; attachments[0].artifact_id = "changed";
  complete(captured);
  const context = await promise;
  assert.deepEqual(context.request_target, { kind: "item", version: ref });
  assert.equal(context.attachments[0].artifact_id, "chosen");
  await assert.rejects(prepareSubmissionContext({ target: "current_view", selection: captured, references: [], attachments: [] }, async () => selection), /Review changed/);
});

test("project and creation targets never prepare hidden Review drafts", async () => {
  for (const target of [{ kind: "project" } as const, { kind: "new_item", item_kind: "asset", intent_id: "intent" } as const]) {
    const context = await prepareSubmissionContext({ target, selection: { ...emptySelection(), ...ref }, references: [], attachments: [] }, async () => { throw new Error("Hidden Review was flushed"); });
    assert.equal(context.item_id, null); assert.equal(context.version_id, null); assert.equal(context.view_mode, "none");
  }
});

test("history sorts canonical UTC, deduplicates receipts rather than repeated text and restores the draft", () => {
  const first = request("a", "2026-11-15T09:00:00+00:00", "same prompt");
  const history = promptHistory("project", [request("b", "2026-11-15T09:00:01Z", "same prompt", "steer"), first, first, request("c", "2026-11-15T09:00:02Z", "stop", "stop"), { ...first, project_id: "another" }]);
  assert.deepEqual(history, ["same prompt", "same prompt"]);
  const newer = stepHistory(history, null, "unsent draft", "older");
  const older = stepHistory(history, newer.cursor, newer.text, "older");
  assert.equal(older.cursor?.index, 0);
  const forward = stepHistory(history, older.cursor, older.text, "newer");
  assert.deepEqual(stepHistory(history, forward.cursor, forward.text, "newer"), { cursor: null, text: "unsent draft" });
});

test("context counts unique images without changing attachment roles", () => {
  const context = cleanTargetContext({ kind: "item", version: ref }, [ref, ref], [{ artifact_id: "image", role: "reference" }, { artifact_id: "image", role: "concept" }]);
  assert.equal(contextCount(context), 3);
  assert.equal(context.attachments.length, 2);
});

test("history entry respects selections, multiline caret movement, modifiers and IME", () => {
  const up = { key: "ArrowUp", ctrlKey: false, metaKey: false, altKey: false, shiftKey: false, isComposing: false };
  assert.equal(canNavigateHistory(up, "", 0, 0, null), true);
  assert.equal(canNavigateHistory(up, "unsent\nmultiline", 0, 0, null), true);
  assert.equal(canNavigateHistory(up, "unsent\nmultiline", 4, 4, null), false);
  assert.equal(canNavigateHistory(up, "unsent", 0, 3, null), false);
  for (const modifier of ["ctrlKey", "metaKey", "altKey", "shiftKey", "isComposing"] as const) assert.equal(canNavigateHistory({ ...up, [modifier]: true }, "", 0, 0, null), false);
  assert.equal(canNavigateHistory({ ...up, key: "ArrowDown" }, "prompt", 6, 6, { index: 0, saved: "draft", recalled: "prompt" }), true);
  assert.equal(canNavigateHistory(up, "edited prompt", 5, 5, { index: 0, saved: "draft", recalled: "prompt" }), false);
});

test("control gates respect authoritative denial and only allow safe legacy work states", () => {
  const denied = snapshot();
  denied.execution!.can_steer_work_id = null; denied.execution!.can_stop_work_id = null;
  assert.equal(steerWorkId(denied), null); assert.equal(stopWorkId(denied), null);
  for (const state of ["running", "awaiting_user", "completed", "awaiting_resume", "stop_requested", "outcome_uncertain"]) {
    const legacy = { ...snapshot(state), execution: undefined };
    assert.equal(steerWorkId(legacy), ["running", "awaiting_user"].includes(state) ? "work" : null);
    assert.equal(stopWorkId(legacy), ["running", "awaiting_user"].includes(state) ? "work" : null);
  }
});

test("split Send exposes its selected action and independent accessible mode menu toggle", () => {
  for (const mode of ["queue", "steer"] as const) {
    const markup = renderToStaticMarkup(<SendControl mode={mode} disabled={false} onMode={() => { throw new Error("Rendering cannot select a mode"); }} onSend={() => { throw new Error("Rendering cannot submit"); }} />);
    assert.match(markup, mode === "queue" ? /aria-label="Queue message"/ : /aria-label="Steer active work"/);
    assert.match(markup, /aria-label="Select send mode"/); assert.match(markup, /aria-haspopup="menu"/); assert.match(markup, /aria-expanded="false"/);
    assert.doesNotMatch(markup, /<select/);
  }
});

test("Assistant status distinguishes execution, holds, decisions, uncertainty and stale transport", () => {
  const props = { snapshot: snapshot(), requests: [], events: [], connection: "Connected" };
  assert.equal(assistantState(props).label, "Working…");
  assert.equal(assistantState({ ...props, submitting: true }).label, "Sending…");
  assert.equal(assistantState({ ...props, snapshot: snapshot("running", true) }).label, "Paused");
  assert.equal(assistantState({ ...props, snapshot: snapshot("stop_requested") }).label, "Stopping…");
  assert.equal(assistantState({ ...props, snapshot: snapshot("outcome_uncertain") }).label, "Needs recovery");
  assert.equal(assistantState({ ...props, snapshot: { ...snapshot(), pending_decisions: [{}] } as ProjectSnapshot }).label, "Needs your input");
  assert.match(renderToStaticMarkup(<AssistantStatus {...props} connection="Reconnecting" />), /Working… · reconnecting/);
  assert.doesNotMatch(renderToStaticMarkup(<AssistantStatus {...props} snapshot={null} hydrated={false} />), /Ready/);
  assert.match(renderToStaticMarkup(<AssistantStatus {...props} compact />), /aria-label="Working…"/);
});

test("native children survive disconnect and parent pause until terminal evidence", () => {
  const started = event(1, "subagent.started", activity);
  const agents = nativeAgents(null, [started, event(2, "tool.started", { ...activity, invocation_id: "wrapper", name: "task" })]);
  assert.equal(agents.filter(busyAgent).length, 1);
  assert.equal(nativeAgents(snapshot("awaiting_resume", true), [], agents).filter(busyAgent).length, 1);
  const completed = nativeAgents(null, [event(3, "subagent.completed", { ...activity, state: "completed" }, "2026-11-15T10:00:01Z")], agents);
  assert.equal(completed.filter(busyAgent).length, 0);
  assert.equal(nativeAgents(null, [started], completed).filter(busyAgent).length, 0);
  const secondSession = { ...started, native_session_id: "other-session" };
  assert.equal(nativeAgents(null, [started, secondSession]).length, 2);
});

test("Conversation suppresses tool-only bubbles, sorts UTC and offers neutral starters", () => {
  const events = [event(2, "assistant.completed", { message_id: "late", text: "later reply" }, "2026-11-15T10:00:01+00:00"), event(1, "assistant.completed", { message_id: "early", text: "earlier reply" }, "2026-11-15T10:00:00Z"), event(3, "assistant.completed", { message_id: "blank", text: "  \n" })];
  const markup = renderToStaticMarkup(<Conversation api={api} projectId="project" requests={[]} events={events} onRefresh={async () => {}} onError={() => {}} />);
  assert.equal((markup.match(/class="message assistant-message"/g) ?? []).length, 2);
  assert.ok(markup.indexOf("earlier reply") < markup.indexOf("later reply"));
  const empty = renderToStaticMarkup(<Conversation api={api} projectId="project" requests={[]} events={[]} onRefresh={async () => {}} onError={() => {}} onStarter={() => {}} />);
  assert.match(empty, /Plan a storyboard/); assert.match(empty, /Create an asset/); assert.match(empty, /Build a scene/);
  assert.ok(compareConversationEntries({ time: "2026-11-15T10:00:00Z", sequence: 1, key: "z" }, { time: "2026-11-15T10:00:00+00:00", sequence: 2, key: "a" }) < 0);
});

test("Conversation renders retained media once across duplicate tool and specialist output receipts", () => {
  const artifact = { artifact_id: "image", project_id: "project", state: "ready", kind: "image", purpose: "concept", media_type: "image/png" } as Artifact;
  const retainedArtifacts = [{ artifact, attribution_status: "verified", invocation_id: "invocation", request_id: "request", work_id: "work" }] as RetainedArtifactSummary[];
  const complete = { ...activity, state: "completed", artifact_ids: ["image"], output_status: "publication_blocked", summary: "Storyboard document validation failed" };
  const events = [event(1, "tool.completed", complete), event(2, "subagent.completed", { ...complete, invocation_id: "child" }), event(2, "subagent.completed", { ...complete, invocation_id: "child" })];
  const markup = renderToStaticMarkup(<Conversation api={api} projectId="project" requests={[]} events={events} retainedArtifacts={retainedArtifacts} onRefresh={async () => {}} onError={() => {}} />);
  assert.equal((markup.match(/<img/g) ?? []).length, 1);
  assert.match(markup, /Finished · publication blocked/); assert.match(markup, /Storyboard document validation failed/);
});

test("retained images use authenticated routes, deduplicate and never preview SVG or HTML", () => {
  const artifact = { artifact_id: "image", project_id: "project", state: "ready", kind: "image", purpose: "concept", media_type: "image/png" } as Artifact;
  const retained = [{ artifact, attribution_status: "verified", request_id: null, work_id: null, invocation_id: null }] as RetainedArtifactSummary[];
  const markup = renderToStaticMarkup(<OutputCards api={api} projectId="project" artifactIds={["image", "image"]} retainedArtifacts={retained} />);
  assert.equal((markup.match(/<img/g) ?? []).length, 1);
  assert.match(markup, /\/api\/v1\/projects\/project\/artifacts\/image\/content/);
  for (const media_type of ["image/svg+xml", "text/html"]) {
    const unsafe = renderToStaticMarkup(<OutputCards api={api} projectId="project" artifactIds={["image"]} retainedArtifacts={[{ ...retained[0], artifact: { ...artifact, media_type } }]} />);
    assert.doesNotMatch(unsafe, /<img|<iframe|<object/); assert.match(unsafe, /download="image"/);
  }
});

test("Resume waits for real completion, preserves uncertainty and bounds polling", async () => {
  let calls = 0;
  const completedApi = { call: async (operation: string) => { assert.equal(operation, "getResumeOperation"); calls++; return { state: calls === 1 ? "dispatching" : "completed" }; } } as unknown as ApiClient;
  const completed = await awaitResume(completedApi, "project", "original-operation", 1000);
  assert.equal(completed?.state, "completed"); assert.equal(calls, 2);
  const uncertainApi = { call: async () => ({ state: "outcome_uncertain" } as ResumeOperationStatus) } as unknown as ApiClient;
  assert.equal((await awaitResume(uncertainApi, "project", "original-operation"))?.state, "outcome_uncertain");
  const pendingApi = { call: async () => ({ state: "pending" }) } as unknown as ApiClient;
  await assert.rejects(awaitResume(pendingApi, "project", "original-operation", 5), /still awaiting confirmation/);
  const legacyApi = { call: async () => { throw new Error("getResumeOperation not implemented"); } } as unknown as ApiClient;
  assert.equal(await awaitResume(legacyApi, "project", "original-operation"), null);
});
