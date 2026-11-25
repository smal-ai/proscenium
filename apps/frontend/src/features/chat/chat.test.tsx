import assert from "node:assert/strict";
import { test } from "node:test";
import { renderToStaticMarkup } from "react-dom/server";
import type { ActivityEventData, AnnotationSnapshot, ApiClient, Artifact, Event, FrameTarget, ItemRecord, JobRecord, ProjectSnapshot, RequestRecord, ResumeOperationStatus, RetainedArtifactSummary, ReviewTicket } from "../../api";
import { emptySelection } from "../../api/studio";
import { backfillWindows, mergeReviews, revisionScope, validateWindowEvents, versionRefreshBuffer } from "../../app/useProjectData";
import { ASSISTANT_MAX_WIDTH, defaultStudioLayout, NAVIGATOR_MAX_WIDTH } from "../../app/useStudioLayout";
import { CriticPanel, criticRequestTarget, formatReviewTimeSast, isPendingReview, reviewDisplayName } from "../review/CriticPanel";
import { annotationContext } from "../review/context";
import { proxyJobsFingerprint, reviewJobsFingerprint } from "../review/refreshKeys";
import { cleanTargetContext, contextCount, prepareSubmissionContext, resolveTarget, viewedSelectionLabel, type DraftTarget } from "./target";
import { canNavigateHistory, promptHistory, stepHistory } from "./promptHistory";
import { assistantState, AssistantStatus } from "./AssistantStatus";
import { busyAgent, nativeAgents } from "./agents";
import { compareConversationEntries, Conversation } from "./Conversation";
import { ActivityEntry } from "./ActivityEntry";
import { OutputCards } from "./OutputCards";
import { awaitResume, heldRequestIds, isIdleAutoReleaseReady, resumableWork } from "./resume";
import { handleSubmitEnter, isSubmitEnter, steerWorkId, stopWorkId } from "./controls";
import { SendControl } from "./SendControl";
import { ChatPanel, shouldShowPendingRequestCard, shouldShowPendingStopCard, shouldShowQueueHold, shouldShowResumeCard, type ChatPanelProps } from "./ChatPanel";
import { JobList, jobsBadgeState } from "./ComposerPanel";
import { TargetPicker } from "./TargetPicker";

const api = { call: () => { throw new Error("Unconfigured API operation in offline test"); } } as unknown as ApiClient;
const ref = { item_id: "asset-a", version_id: "version-old" };
const receipt = { state: "queued", state_revision: 1 };
const request = (request_id: string, accepted_at: string, text: string, type = "queue") => ({ project_id: "project", request_id, accepted_at, receipt, submission: { type, text, context_id: "context", project_id: "project", request_id } }) as RequestRecord;
const snapshot = (state = "running", fenced = false) => ({ project: { project_id: "project", state: "active" }, active_work: { work_id: "work", state, native_session_id: "session" }, pending_decisions: [], execution: { fenced, hold_reason: fenced ? "Owner stopped work" : null, active_agents: [], work_outputs: [] }, items: [] }) as unknown as ProjectSnapshot;
const event = (sequence: number, event_type: Event["event_type"], payload: object, occurred_at = "2026-11-15T10:00:00Z") => ({ sequence, event_type, payload, occurred_at, project_id: "project", actor: { kind: "lead", id: "lead" }, native_session_id: "session", request_id: "request", work_id: "work" }) as Event;
const activity: ActivityEventData = { invocation_id: "invocation", name: "image specialist", state: "running", summary: "", artifact_ids: [], model_id: "reported-model", display_name: "Prepare concept" };
const job = (state: JobRecord["state"], job_id: string = state): JobRecord => ({
  schema_version: "1.0", job_id, state, kind: "image_generation", provider_id: "local-comfy", target: { target_id: job_id, version: null, shot_id: null },
  expected_frames: null, completed_frames: null, render_spec: null, project_id: "project", work_id: null, owner_operation_id: null,
  attempt_id: `attempt-${job_id}`, attempt_number: 1, state_revision: 1, availability: "pending", input_digest: "a".repeat(64), external_job_id: null, owner_epoch: "test", artifact_ids: [],
});
const asset: ItemRecord = { schema_version: "1.0", project_id: "project", item_id: ref.item_id, kind: "asset", name: "Relay dish", current_version_id: "version-new", selected_version_id: null, state_revision: 1 };
const composer: ChatPanelProps["composer"] = { pending: null, pendingStop: null, pendingNeedsConfirmation: false, pendingStopNeedsConfirmation: false, busy: false, stopping: false, error: "", submit: async () => null, stop: async () => {}, retry: async () => null };
const chatMarkup = (jobs: JobRecord[], overrides: Partial<ChatPanelProps> = {}) => renderToStaticMarkup(<ChatPanel api={api} projectId="project" snapshot={snapshot()} requests={[]} events={[]} jobs={jobs} selection={emptySelection()} composer={composer} onRefresh={async () => {}} showHeaderStatus={false} {...overrides} />);

test("Request and Stop cards distinguish an initial submission from confirmation and manual retry", () => {
  const command = (type: "queue" | "stop") => ({ submission: request(type, "2026-11-15T09:00:00Z", "Prompt", type).submission, context: null, acknowledged: false });
  const pending = command("queue"), pendingStop = command("stop");
  for (const [busy, needsConfirmation, visible] of [
    [true, false, false], [false, true, true], [true, true, true],
    [true, undefined, false], [false, undefined, true],
  ] as const) {
    assert.equal(shouldShowPendingRequestCard({ pending, busy, pendingNeedsConfirmation: needsConfirmation }), visible);
    assert.equal(shouldShowPendingStopCard({ pendingStop, stopping: busy, pendingStopNeedsConfirmation: needsConfirmation }), visible);
  }
  assert.equal(shouldShowPendingRequestCard({ pending: null, busy: false, pendingNeedsConfirmation: true }), false);
  assert.equal(shouldShowPendingStopCard({ pendingStop: null, stopping: false, pendingStopNeedsConfirmation: true }), false);
});

test("submit Enter accepts plain and legacy shortcuts but leaves multiline editing and IME alone", () => {
  const enter = { key: "Enter", shiftKey: false, altKey: false, ctrlKey: false, metaKey: false };
  for (const overrides of [{}, { ctrlKey: true }, { metaKey: true }]) {
    assert.equal(isSubmitEnter({ ...enter, ...overrides }), true);
  }
  for (const overrides of [{ shiftKey: true }, { altKey: true }, { ctrlKey: true, shiftKey: true },
    { key: "Space" }, { isComposing: true }, { nativeEvent: { isComposing: true }, isComposing: false },
    { nativeEvent: { isComposing: false, keyCode: 229 } }, { keyCode: 229 }]) {
    assert.equal(isSubmitEnter({ ...enter, ...overrides }), false);
  }
});

test("submit key handling consumes repeats without dispatch and preserves IME confirmation", () => {
  const enter = { key: "Enter", shiftKey: false, altKey: false, ctrlKey: false, metaKey: false };
  for (const [overrides, handled, expected] of [
    [{ repeat: true }, true, ["preventDefault"]],
    [{ keyCode: 229 }, false, []],
    [{ repeat: false }, true, ["preventDefault", "action"]],
  ] as const) {
    const calls: string[] = [];
    assert.equal(handleSubmitEnter({ ...enter, ...overrides, preventDefault: () => calls.push("preventDefault") }, () => calls.push("action")), handled);
    assert.deepEqual(calls, expected);
  }
});

test("render-tick flood only bumps its own version once per refresh window", t => {
  t.mock.timers.enable({apis: ["setTimeout"]});
  const batches: string[][] = [];
  const receiptArtifacts = new Set<string>();
  const buffer = versionRefreshBuffer(ids => batches.push(ids));
  const target = {target_id: "background", version: {item_id: "background", version_id: "background-v1"}, shot_id: null};
  let previous = {...job("running"), kind: "render" as const, target, completed_frames: 0};
  const structuralKey = reviewJobsFingerprint([previous]);
  const proxyKey = proxyJobsFingerprint([previous], "foreground-v1");
  for (let i = 1; i <= 60; i++) {
    const artifactId = `render-frame-${i}`;
    const next = {...previous, artifact_ids: [...previous.artifact_ids, artifactId], completed_frames: i, state_revision: i + 1};
    receiptArtifacts.add(artifactId);
    for (const [type, payload] of [["frame.available", {version_id: "background-v1", artifact_id: artifactId}], ["job.state_changed", next]] as const) {
      const scope = revisionScope(event(i, type, payload), previous, receiptArtifacts);
      assert.equal(scope.catalogue, false);
      assert.equal(scope.base, false);
      assert.equal(scope.immediate, false);
      assert.equal(scope.versionId, "background-v1");
      buffer.bump(scope.versionId!, scope.immediate!);
    }
    assert.equal(reviewJobsFingerprint([next]), structuralKey);
    assert.equal(proxyJobsFingerprint([next], "foreground-v1"), proxyKey);
    previous = next;
  }
  t.mock.timers.tick(199);
  assert.deepEqual(batches, []);
  t.mock.timers.tick(1);
  assert.deepEqual(batches, [["background-v1"]]);
  buffer.bump("background-v1", false);
  buffer.bump("other-v1", false);
  const terminal = revisionScope(event(61, "job.state_changed", {...previous, state: "succeeded", state_revision: 62}), previous);
  assert.equal(terminal.catalogue, true);
  assert.equal(terminal.base, false);
  buffer.bump(terminal.versionId!, terminal.immediate!);
  assert.deepEqual(batches, [["background-v1"], ["background-v1"]]);
  t.mock.timers.tick(200);
  assert.deepEqual(batches, [["background-v1"], ["background-v1"], ["other-v1"]]);
  buffer.bump("cancelled-buffer", false);
  buffer.cancel();
  t.mock.timers.tick(200);
  assert.equal(batches.length, 3);
});

test("annotation and review events are base-only while segment and pass updates are scoped", () => {
  for (const type of ["annotation.saved", "review.changed", "review.ready"] as const) {
    const scope = revisionScope(event(1, type, {version_id: "v"}));
    assert.equal(scope.base, true);
    assert.equal(scope.catalogue, false);
    assert.equal(scope.versionId, undefined);
  }
  for (const type of ["segment.available", "preview.order_changed"] as const) {
    const scope = revisionScope(event(1, type, {version_id: "v"}));
    assert.equal(scope.versionId, "v");
    assert.equal(scope.immediate, true);
    assert.equal(scope.base, false);
    assert.equal(scope.catalogue, type === "preview.order_changed");
  }
  const prior = job("running");
  const failed = revisionScope(event(1, "job.state_changed", {...prior, error_code: "provider_error"}), prior);
  assert.equal(failed.catalogue, true);
  assert.equal(failed.base, true);
  assert.equal(failed.versionId, undefined);
});

test("queue hold excludes ordinary running and awaiting-user eligibility reasons", () => {
  assert.equal(shouldShowQueueHold(null, "queue"), false);
  for (const state of ["running", "awaiting_user", "completed"]) {
    for (const code of ["active_work_running", "awaiting_user"]) {
      const current = snapshot(state);
      current.execution!.hold_reason_code = code;
      current.execution!.hold_reason = "Queued requests wait until the current work finishes.";
      assert.equal(shouldShowQueueHold(current, "queue"), false);
      assert.doesNotMatch(chatMarkup([], { snapshot: current }), /class="queue-hold"/);
    }
  }
});

test("queue hold recognises explicit fences, pause codes and legacy idle holds only in Queue mode", () => {
  const pausedProject = { ...snapshot(), project: { ...snapshot().project, state: "awaiting_resume" as const }, execution: undefined };
  const held = [snapshot("running", true), pausedProject];
  for (const code of ["fenced", "awaiting_resume", "queue_paused", "uncertain_stop", "stop_requested"]) {
    const current = snapshot(); current.execution!.hold_reason_code = code; held.push(current);
  }
  const legacy = snapshot("completed"); legacy.execution!.hold_reason = "Legacy queue hold"; held.push(legacy);
  for (const current of held) {
    assert.equal(shouldShowQueueHold(current, "queue"), true);
    assert.equal(shouldShowQueueHold(current, "steer"), false);
    assert.match(chatMarkup([], { snapshot: current }), /class="queue-hold"/);
  }
  for (const state of ["running", "awaiting_user"]) {
    const current = snapshot(state); current.execution!.hold_reason = "Queued requests wait until the current work finishes.";
    assert.equal(shouldShowQueueHold(current, "queue"), false);
  }
  const fenced = snapshot("running", true); fenced.execution!.hold_reason_code = "active_work_running";
  assert.equal(shouldShowQueueHold(fenced, "queue"), true);
  assert.equal(shouldShowQueueHold(snapshot("completed"), "queue"), false);
});

test("live tool calls provide readable working steps and reconnecting status", () => {
  const labels = {
    prepare_image_generation: "Preparing image", submit_generation: "Submitting image", get_job: "Waiting on job",
    get_artifact: "Inspecting output", validate_authored_document: "Validating plan", checkpoint_sources: "Checkpointing sources",
    publish_outputs: "Publishing outputs", request_vlm_review: "Requesting VLM critique", get_vlm_review: "Waiting on VLM critique",
    escalate_vlm_review: "Requesting owner review", request_preview: "Requesting preview",
    assemble_sequence: "Assembling sequence", run_unity_workflow: "Running Unity workflow", bash: "Running command",
    skill: "Loading skill", view: "Reading project file", get_project_capabilities: "Checking capabilities", custom_tool: "custom tool",
    search_web: "Searching the web", web_fetch: "Fetching web page",
  };
  for (const [name, detail] of Object.entries(labels)) {
    const props = { snapshot: snapshot(), requests: [], events: [event(1, "tool.started", { ...activity, name })], jobs: [], connection: "Connected" };
    assert.deepEqual(assistantState(props), { state: "working", label: `Working · ${detail}…`, detail });
    assert.ok(renderToStaticMarkup(<AssistantStatus {...props} connection="Reconnecting" />).includes(`Working · ${detail}… · reconnecting`));
  }
});

test("live tool status excludes completed, wrapper and unrelated activity and selects the latest active call", () => {
  const start = event(1, "tool.started", { ...activity, name: "prepare_image_generation" });
  const props = { snapshot: snapshot(), requests: [], events: [], jobs: [], connection: "Connected" };
  for (const events of [
    [start, event(2, "tool.completed", { ...activity, state: "completed" }), start],
    [event(1, "tool.started", { ...activity, name: "task" })],
    [{ ...start, work_id: "other", native_session_id: "other" }],
    [{ ...start, project_id: "other" }],
  ]) assert.equal(assistantState({ ...props, events }).label, "Working…");
  const latest = event(2, "tool.started", { ...activity, invocation_id: "latest", name: "checkpoint_sources" });
  assert.equal(assistantState({ ...props, events: [latest, start] }).label, "Working · Checkpointing sources…");
  assert.equal(assistantState({ ...props, events: [{ ...start, work_id: null }] }).label, "Working · Preparing image…");
  assert.equal(assistantState({ ...props, events: [{ ...start, native_session_id: null }] }).label, "Working · Preparing image…");
  assert.equal(assistantState({ ...props, events: [start, { ...event(2, "tool.completed", activity), native_session_id: "other" }] }).label, "Working · Preparing image…");
});

test("native specialists precede independent jobs and live tools precede specialists", () => {
  const props = { snapshot: snapshot(), requests: [], events: [event(1, "subagent.started", activity)], jobs: [job("running")], connection: "Connected" };
  assert.equal(assistantState(props).label, "Working · Prepare concept…");
  assert.match(renderToStaticMarkup(<AssistantStatus {...props} />), /Working · Prepare concept…/);
  const current = snapshot(); current.execution!.active_agents = [{ ...activity, state: "running", display_name: "Prepare concept", model_id: "reported-model", native_agent_id: "specialist", work_id: "work", request_id: "request", freshness: "live", updated_at: "2026-11-15T10:00:00Z" }];
  assert.equal(assistantState({ ...props, snapshot: current, events: [] }).label, "Working · Prepare concept…");
  assert.equal(assistantState({ ...props, events: [event(1, "subagent.started", { ...activity, display_name: null })] }).label, "Working · image specialist…");
  assert.equal(assistantState({ ...props, events: [...props.events, event(2, "tool.started", { ...activity, name: "prepare_image_generation" })] }).label, "Working · Preparing image…");
});

test("unfinished jobs provide live status with snapshot fallback and settled jobs leave the default label intact", () => {
  const props = { snapshot: snapshot(), requests: [], events: [], jobs: [], connection: "Connected" };
  assert.equal(assistantState(props).label, "Working…");
  assert.equal(assistantState({ ...props, jobs: [job("running")] }).label, "Working · 1 job running…");
  const jobs = [job("running"), job("outcome_uncertain"), job("failed"), job("succeeded"), job("cancelled")];
  assert.equal(assistantState({ ...props, jobs }).label, "Working · 2 jobs running…");
  assert.match(renderToStaticMarkup(<AssistantStatus {...props} jobs={jobs} connection="Reconnecting" />), /Working · 2 jobs running… · reconnecting/);
  assert.equal(assistantState({ ...props, jobs: jobs.slice(2) }).label, "Working…");
  const current = snapshot(); current.jobs = jobs;
  assert.equal(assistantState({ ...props, snapshot: current, jobs: undefined }).label, "Working · 2 jobs running…");
  assert.equal(assistantState({ ...props, snapshot: current }).label, "Working…");
});

test("Jobs badge partitions every job into running, queued, uncertain, failed or settled without double-counting", () => {
  assert.deepEqual(jobsBadgeState([], false), { text: "0", ariaLabel: "Jobs: 0", tone: "empty" });
  assert.equal(jobsBadgeState([], true).text, "0?");
  const queued = [job("planned", "one"), job("planned", "two"), job("planned", "three"), job("planned", "four"), job("waiting_resource")];
  assert.deepEqual(jobsBadgeState([job("running"), ...queued], false), { text: "1 running · 5 queued", ariaLabel: "Jobs: 1 running · 5 queued", tone: "active" });
  assert.equal(jobsBadgeState(queued, false).text, "5 queued");
  assert.equal(jobsBadgeState([job("cancel_requested"), job("starting")], false).text, "2 running");
  const active = [job("running"), job("planned")];
  assert.equal(jobsBadgeState(active, true).text, "1 running · 1 queued?");
  assert.match(jobsBadgeState(active, true).ariaLabel, /last known/);
  const uncertain = job("outcome_uncertain");
  assert.equal(jobsBadgeState([uncertain], false).text, "1 uncertain?");
  assert.deepEqual(jobsBadgeState([...active, uncertain, job("succeeded")], false), { text: "1 uncertain · 1 running · 1 queued?", ariaLabel: "Jobs: 1 uncertain · 1 running · 1 queued? · uncertain job outcome", tone: "attention" });
  const settled = [job("succeeded", "a"), job("succeeded", "b"), job("cancelled", "c"), job("succeeded", "d")];
  assert.equal(jobsBadgeState(settled, false).text, "4 done"); assert.equal(jobsBadgeState(settled, false).tone, "done");
  assert.equal(jobsBadgeState(settled, true).text, "4 done?");
  const failed = job("failed");
  assert.deepEqual(jobsBadgeState([failed, job("succeeded")], false), { text: "1 failed · 1 done", ariaLabel: "Jobs: 1 failed · 1 done · failed job recorded", tone: "attention" });
  assert.deepEqual(jobsBadgeState([failed, job("running"), ...queued, ...settled.slice(0, 2)], false), { text: "1 failed · 1 running · 5 queued", ariaLabel: "Jobs: 1 failed · 1 running · 5 queued · failed job recorded", tone: "attention" });
  assert.equal(jobsBadgeState([failed, uncertain, ...active], false).text, "1 failed · 1 uncertain · 1 running · 1 queued?");
});

test("Composer renders accented Jobs counts and its status as the final bottom-right footer row", () => {
  for (const [jobs, text, tone] of [
    [[], "0", "empty"],
    [[job("running"), job("planned")], "1 running · 1 queued", "active"],
    [[job("running"), job("planned"), job("outcome_uncertain")], "1 uncertain · 1 running · 1 queued?", "attention"],
    [[job("succeeded", "a"), job("cancelled", "b"), job("succeeded", "c"), job("succeeded", "d")], "4 done", "done"],
    [[{ ...job("failed"), error_code: "corrupt_provider_output" }], "1 failed", "attention"],
  ] as [JobRecord[], string, string][]) {
    const markup = chatMarkup(jobs);
    assert.ok(markup.includes(`class="${tone}-jobs"`));
    assert.ok(markup.includes(`>${text}</span>`));
    assert.equal((markup.match(/class="assistant-status /g) ?? []).length, 1);
    assert.match(markup, /<div class="composer-footer"><small class="composer-hint">Enter to queue · Shift\+Enter for newline · Context is captured on submit<\/small><span class="assistant-status [\s\S]*<\/span><\/div><\/div><\/div>$/);
    assert.ok(markup.indexOf('class="composer-footer"') > markup.indexOf('class="composer-actions"'));
    if (["active", "attention"].includes(tone)) assert.match(markup, /class="jobs-indicator" aria-hidden="true"/);
  }
  assert.match(chatMarkup([job("running")], { connection: "Reconnecting" }), /1 running\?/);
  assert.equal((chatMarkup([], { showHeaderStatus: true }).match(/class="assistant-status /g) ?? []).length, 2);
});

test("Independent jobs shows real frame progress, attention first and only settled work in collapsed history", () => {
  const render = ([shot_id, expected_frames, completed_frames]: [string | null, number, number | null], index: number): JobRecord => ({
    ...job(index === 0 ? "running" : "planned", `render-${index}`), kind: "render", expected_frames, completed_frames,
    target: { target_id: shot_id ?? "proxy-relay", version: ref, shot_id },
    render_spec: { purpose: shot_id ? "draft_motion" : "proxy" } as JobRecord["render_spec"],
  });
  const renders = ([
    ["sl-01-dormant-macro", 72, 28], ["sl-02-conduit", 96, null], ["sl-03-console", 72, null], ["sl-04-exterior", 120, null], ["sl-05-title", 72, null], [null, 1, null],
  ] satisfies [string | null, number, number | null][]).map(render);
  const markupFor = (jobs: JobRecord[]) => renderToStaticMarkup(<JobList api={api} projectId="project" jobs={jobs} items={[asset]} onRefresh={async () => {}} onError={() => {}} />);
  assert.doesNotMatch(markupFor(renders), /Finished/);
  const markup = markupFor([...renders, { ...job("failed"), error_code: "corrupt_provider_output" }, job("outcome_uncertain"), job("succeeded"), job("cancelled")]);
  assert.match(markup, /Needs attention \(2\)[\s\S]*Running \(1\)[\s\S]*Planned \/ waiting \(5\)[\s\S]*<details class="jobs-finished-group"><summary>Finished \(2\)<\/summary>/);
  assert.match(markup, /sl-01-dormant-macro<\/strong>[\s\S]*28\/72 frames<span class="job-progress-bar" aria-hidden="true"><span class="job-progress-fill" style="width:39%"/);
  for (const frames of [96, 72, 120]) assert.match(markup, new RegExp(`class="job-progress">${frames} frames`));
  assert.match(markup, /Proxy · Relay dish<\/strong>[\s\S]*class="job-progress">1 frame</);
  assert.doesNotMatch(markup, /0\/96/);
  assert.match(markup, /Failure: corrupt_provider_output[\s\S]*<small role="status">Needs attention · reconcile the original job outcome before retrying\./);
  assert.match(markup, /<details class="job-row-details"><summary>Details<\/summary><small>Job ID: <code>render-0<\/code><\/small>[\s\S]*Provider: local-comfy/);
  assert.equal((markup.match(/Request job cancellation/g) ?? []).length, 7);
  assert.equal((markup.match(/class="job-row(?: needs-attention)?"/g) ?? []).length, 10);
});

test("Target picker exposes four distinct modes and references do not require an existing primary target", () => {
  const markupFor = (target: DraftTarget, references = [ref], open = true, pendingExisting?: boolean, items = [asset]) => renderToStaticMarkup(<TargetPicker api={api} projectId="project" items={items} target={target} selection={emptySelection()} references={references} includeCurrentViewAsReference={false} onTarget={() => {}} onReferences={() => {}} onIncludeView={() => {}} pendingExisting={pendingExisting} {...(open ? { open } : {})} />);
  const current = markupFor("current_view", []);
  assert.match(current, /Target mode.*aria-pressed="true">Current view.*aria-pressed="false">Project.*aria-pressed="false">Existing….*aria-pressed="false">New…/);
  assert.match(current, /Nothing open in Review · resolves to Project on submit/);
  assert.match(current, /<details class="target-references-disclosure">/);
  assert.doesNotMatch(markupFor("current_view", [], false), /class="target-options"/);
  const pending = markupFor("current_view", [], true, true);
  assert.match(pending, /aria-invalid="true".*Target:<\/span> Choose published item….*aria-pressed="true">Existing…/);
  assert.match(pending, /class="target-pending-hint">Select a published item to enable sending, or choose another target mode\./);
  const unpublished = markupFor("current_view", [], true, true, [{ ...asset, current_version_id: null }]);
  assert.match(unpublished, /<select aria-label="Published item" disabled=""><option[^>]*>No published items yet<\/option><\/select>/);
  assert.match(unpublished, /No published items exist yet\. Choose another target mode to enable sending\./);
  assert.match(markupFor("current_view", [], false, true), /aria-expanded="false".*Target:<\/span> Choose published item…/);
  assert.match(markupFor({ kind: "project" }), /aria-pressed="true">Project.*Project-wide planning/);
  const existing = markupFor({ kind: "item", version: ref });
  assert.match(existing, /aria-pressed="true">Existing….*aria-label="Published item".*aria-label="Published version".*value="version-old" selected/);
  const newItem = markupFor({ kind: "new_item", item_kind: "asset", intent_id: "intent", name: "Broken relay dish" });
  assert.match(newItem, /aria-expanded="true".*aria-pressed="true">New….*aria-pressed="true">Asset.*New item name.*value="Broken relay dish"/);
  assert.doesNotMatch(newItem, /aria-label="Published item"/);
  assert.match(newItem, /<details class="target-references-disclosure" open="">.*type="checkbox" disabled="".*none open in Review.*aria-label="Reference item".*aria-label="Reference version".*Relay dish · version-old/);
});

test("explicit target context strips Review media and retains the older version pin", async () => {
  const selection = { ...emptySelection(), ...ref, attachments: [{ artifact_id: "viewport", role: "render" as const }], selected_objects: [{ object_id: "object", instance_id: "instance" }] };
  let prepared = 0;
  const context = await prepareSubmissionContext({ target: { kind: "item", version: ref }, selection, references: [ref], attachments: [{ artifact_id: "chosen", role: "reference" }] }, async () => { prepared++; return selection; });
  assert.equal(prepared, 0);
  assert.deepEqual(context, cleanTargetContext({ kind: "item", version: ref }, [ref], [{ artifact_id: "chosen", role: "reference" }]));
  assert.deepEqual(context.selected_objects, []);
  assert.equal(context.version_id, "version-old");
  assert.deepEqual(resolveTarget("current_view", emptySelection()), { kind: "project" });
  assert.deepEqual(cleanTargetContext(null, [], []).request_target, { kind: "project" });
});

const shotId = "sl-01-dormant-macro";
const reviewVersion = (kind: ItemRecord["kind"]) => ({ item_id: `${kind}-signal-lost`, version_id: `${kind}-v1` });
const reviewItem = (kind: ItemRecord["kind"]): ItemRecord => ({ ...asset, kind, item_id: reviewVersion(kind).item_id, name: "Signal Lost", current_version_id: reviewVersion(kind).version_id });
const reviewFrame = (kind: ItemRecord["kind"], origin: FrameTarget["origin"] = "blender_render"): FrameTarget => ({
  ...reviewVersion(kind), origin, artifact_id: "attempt-1-f1", displayed_sha256: "a".repeat(64), dimensions: { width: 1280, height: 720 },
  shot_id: shotId, render_job_id: "draft-motion-1", source_frame: 1, media_artifact_id: null,
});

test("Current view targets only storyboard shots, even when scene receipts carry a shot ID", () => {
  const scene = { ...emptySelection(), ...reviewVersion("scene"), target: reviewFrame("scene") };
  const board = { ...emptySelection(), ...reviewVersion("storyboard"), target: reviewFrame("storyboard") };
  const staleSceneShot = { kind: "shot", storyboard_version: reviewVersion("scene"), shot_id: shotId } as const;
  const expectedScene = { kind: "item", version: reviewVersion("scene") };
  assert.deepEqual(resolveTarget("current_view", scene, [reviewItem("scene")]), expectedScene);
  assert.deepEqual(resolveTarget("current_view", { ...scene, request_target: staleSceneShot }, [reviewItem("scene")]), expectedScene);
  assert.deepEqual(resolveTarget("current_view", scene), expectedScene);
  assert.deepEqual(resolveTarget("current_view", board, [reviewItem("storyboard")]), { kind: "shot", storyboard_version: reviewVersion("storyboard"), shot_id: shotId });
});

test("Current view keeps sequences and Unity results as viewed versions without a request target", async () => {
  const pendingSequence = { ...emptySelection(), ...reviewVersion("sequence"), request_target: null };
  const assembledSequence = { ...pendingSequence, target: reviewFrame("sequence", "sequence_render") };
  const unity = { ...emptySelection(), ...reviewVersion("unity_result"), request_target: null };
  for (const [selection, kind] of [[pendingSequence, "sequence"], [assembledSequence, "sequence"], [unity, "unity_result"]] as const) {
    const items = [reviewItem(kind)];
    const version = reviewVersion(kind);
    assert.equal(resolveTarget("current_view", selection), null);
    assert.equal(resolveTarget("current_view", { ...selection, request_target: undefined }, items), null);
    assert.equal(resolveTarget("current_view", { ...selection, request_target: { kind: "item", version } }, items), null);
    assert.equal(resolveTarget("current_view", { ...selection, request_target: { kind: "shot", storyboard_version: version, shot_id: shotId } }, items), null);
    assert.deepEqual(await prepareSubmissionContext({ target: "current_view", selection, references: [], attachments: [], items }), { ...selection, reference_versions: [] });
  }
  assert.equal(resolveTarget("current_view", { ...assembledSequence, request_target: undefined }), null);
  assert.equal(contextCount(pendingSequence), 1);
  assert.equal(viewedSelectionLabel(pendingSequence, [reviewItem("sequence")]), "Sequence · Signal Lost");
  assert.equal(viewedSelectionLabel(unity, [reviewItem("unity_result")]), "Unity result · Signal Lost");
  assert.equal(viewedSelectionLabel(unity), "Version · unity_result-v1");
});

test("Current view rejects intra-shot drift in the presented artefact, source frame or sequence frame", async () => {
  const target = { ...reviewFrame("scene"), sequence_frame: 1 };
  const selection = { ...emptySelection(), ...reviewVersion("scene"), target };
  for (const changed of [{ source_frame: 2 }, { artifact_id: "attempt-2-f1" }, { sequence_frame: 2 }]) {
    let finish!: () => void;
    const preparing = prepareSubmissionContext({ target: "current_view", selection, references: [], attachments: [] }, async () => {
      await new Promise<void>(resolve => { finish = resolve; });
      return { ...selection, target: { ...target, ...changed } };
    });
    finish();
    await assert.rejects(preparing, /Review changed during context capture/);
  }
});

test("Current view accepts fresh 3D proxy pixels when no frame was presented at submit", async () => {
  const selection = { ...emptySelection(), ...reviewVersion("asset"), view_mode: "3d_inspection" as const };
  const target = { ...reviewFrame("asset", "proxy_capture"), render_job_id: null };
  const captured = await prepareSubmissionContext({ target: "current_view", selection, references: [], attachments: [] }, async () => ({ ...selection, target }));
  assert.deepEqual(captured.target, target);
  assert.deepEqual(captured.request_target, { kind: "item", version: reviewVersion("asset") });
});

test("annotation and Critic submissions resolve matching target kinds without losing frame evidence", () => {
  for (const [target, items, expected] of [
    [reviewFrame("sequence", "sequence_render"), [], null],
    [reviewFrame("unity_result"), [reviewItem("unity_result")], null],
    [reviewFrame("scene"), [reviewItem("scene")], { kind: "item", version: reviewVersion("scene") }],
    [reviewFrame("storyboard"), [reviewItem("storyboard")], { kind: "shot", storyboard_version: reviewVersion("storyboard"), shot_id: shotId }],
  ] as const) {
    const note: AnnotationSnapshot = { annotation_id: "note", saved_revision: 1, target, range: null, camera: null, markups: [], text: "Review the opening frame.", spatial_hits: [], spatial_status: "unavailable" };
    const context = annotationContext(note, items[0]?.kind);
    assert.deepEqual(context.request_target, expected);
    assert.deepEqual(context.target, target);
    assert.deepEqual(criticRequestTarget([target], [...items]), expected);
  }
  assert.deepEqual(criticRequestTarget([reviewFrame("storyboard"), reviewFrame("scene")], [reviewItem("storyboard"), reviewItem("scene")]), { kind: "project" });
});

test("Critic findings target a storyboard shot only when all findings share it", () => {
  const first = { ...reviewFrame("storyboard"), shot_id: "shot-1" };
  const second = { ...first, artifact_id: "attempt-1-f2", source_frame: 2 };
  const items = [reviewItem("storyboard")];
  const version = reviewVersion("storyboard");
  assert.deepEqual(criticRequestTarget([first, second], items), { kind: "shot", storyboard_version: version, shot_id: "shot-1" });
  assert.deepEqual(criticRequestTarget([first, { ...second, shot_id: "shot-2" }], items), { kind: "item", version });
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

test("Conversation suppresses tool-only bubbles, usage lines, sorts UTC and offers neutral starters", () => {
  const events = [
    event(2, "assistant.completed", { message_id: "late", text: "later reply" }, "2026-11-15T10:00:01+00:00"),
    event(1, "assistant.completed", { message_id: "early", text: "earlier reply" }, "2026-11-15T10:00:00Z"),
    event(3, "assistant.completed", { message_id: "blank", text: "  \n" }),
    event(4, "usage.recorded", { model_id: "gpt-5.4", input_tokens: 100, output_tokens: 50, reported_cost: 1, cost_unit: null }),
  ];
  const markup = renderToStaticMarkup(<Conversation api={api} projectId="project" requests={[]} events={events} onRefresh={async () => {}} onError={() => {}} />);
  assert.equal((markup.match(/class="message assistant-message"/g) ?? []).length, 2);
  assert.doesNotMatch(markup, /premium credit|usage-credit/);
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

test("Chat and standalone activity warn only when publication is blocked", () => {
  const current = snapshot();
  const artifact = { artifact_id: "image", project_id: "project", state: "ready", kind: "image", purpose: "concept", media_type: "image/png" } as Artifact;
  const retainedArtifacts = [{ artifact, attribution_status: "verified", invocation_id: "invocation", request_id: "request", work_id: "work" }] as RetainedArtifactSummary[];
  const toolEvent = (output_status: ActivityEventData["output_status"]) => event(1, "tool.completed", { ...activity, state: "completed", artifact_ids: ["image"], output_status });
  const renderActivity = (output_status: ActivityEventData["output_status"]) => renderToStaticMarkup(
    <ActivityEntry api={api} projectId="project" event={toolEvent(output_status)} retainedArtifacts={retainedArtifacts} />,
  );
  const retained = { work_id: "work", request_id: "request", status: "retained_unpublished" as const, artifact_ids: ["image"], published_versions: [], reason_code: null, summary: null };
  current.execution!.work_outputs = [retained];
  const snapshotOnly = chatMarkup([], { snapshot: current, retainedArtifacts });
  const withToolEvent = chatMarkup([], { snapshot: current, events: [toolEvent("retained_unpublished")], retainedArtifacts });
  for (const markup of [snapshotOnly, withToolEvent, renderActivity("retained_unpublished")]) {
    assert.doesNotMatch(markup, /Outputs retained · not published|class="publication-status"/);
    assert.equal((markup.match(/class="retained-output-card"/g) ?? []).length, 1);
    assert.match(markup, /<img[^>]+\/artifacts\/image\/content/);
  }
  assert.match(snapshotOnly, /class="retained-turn-outputs"/);
  assert.doesNotMatch(withToolEvent, /class="retained-turn-outputs"/);

  current.execution!.work_outputs = [{ ...retained, status: "publication_blocked", summary: "Storyboard document validation failed" }];
  for (const markup of [chatMarkup([], { snapshot: current, retainedArtifacts }), renderActivity("publication_blocked")]) {
    assert.equal((markup.match(/class="publication-status" role="status"/g) ?? []).length, 1);
    assert.match(markup, /Finished · publication blocked/);
    assert.doesNotMatch(markup, /Outputs retained · not published/);
  }
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

test("Conversation collapses consecutive routine completed tools and names specialists cleanly", () => {
  const events = [
    { ...event(1, "subagent.started", { ...activity, invocation_id: "child-inv", name: "authoring-specialist", display_name: "shot-retry" }), actor: { kind: "subagent" as const, id: "uuid-9488c0fc" } },
    event(2, "tool.completed", { ...activity, invocation_id: "task-wrap", name: "task", state: "completed" }, "2026-11-15T10:00:01Z"),
    event(3, "tool.completed", { ...activity, invocation_id: "t1", name: "view", state: "completed" }, "2026-11-15T10:00:02Z"),
    event(4, "tool.completed", { ...activity, invocation_id: "t2", name: "bash", state: "completed" }, "2026-11-15T10:00:03Z"),
    { ...event(5, "assistant.completed", { message_id: "sub-msg", text: "Checked hull connection", native_agent_id: "uuid-9488c0fc" }, "2026-11-15T10:00:04Z"), actor: { kind: "subagent" as const, id: "uuid-9488c0fc" } },
  ];
  const markup = renderToStaticMarkup(<Conversation api={api} projectId="project" requests={[]} events={events} onRefresh={async () => {}} onError={() => {}} />);
  assert.match(markup, /2 tool steps completed/);
  assert.match(markup, /<strong>Specialist<\/strong><span>shot-retry<\/span>/);
  assert.doesNotMatch(markup, /Tool: task/);

  // Adding an in-flight tool keeps a single stable activity-group instead of popping a separate row and collapsing back.
  const inflightEvents = [
    event(1, "tool.started", { ...activity, invocation_id: "t1", name: "view", display_name: null, state: "running" }, "2026-11-15T10:00:01Z"),
    event(2, "tool.started", { ...activity, invocation_id: "t2", name: "bash", display_name: null, state: "running" }, "2026-11-15T10:00:02Z"),
    event(3, "tool.completed", { ...activity, invocation_id: "t2", name: "bash", display_name: null, state: "completed" }, "2026-11-15T10:00:05Z"),
    event(4, "tool.started", { ...activity, invocation_id: "t3", name: "get_version", display_name: null, state: "running" }, "2026-11-15T10:00:06Z"),
  ];
  const inflightMarkup = renderToStaticMarkup(<Conversation api={api} projectId="project" requests={[]} events={inflightEvents} onRefresh={async () => {}} onError={() => {}} />);
  assert.equal((inflightMarkup.match(/class="activity activity-group"/g) ?? []).length, 1);
  assert.match(inflightMarkup, /1 of 3 tool steps · get_version…/);
});

test("resumableWork offers stopped work only until newer work runs and ChatPanel guards uncertain Stop", () => {
  const idle = { ...snapshot(), active_work: null };
  const oldStopped = event(10, "work.state_changed", { work_id: "work-old", project_id: "project", state: "stopped", state_revision: 4 });
  assert.deepEqual(resumableWork(idle, [oldStopped]).map(w => w.work_id), ["work-old"]);
  const newerCompleted = event(20, "work.state_changed", { work_id: "work-new", project_id: "project", state: "completed", state_revision: 2 });
  assert.deepEqual(resumableWork(idle, [oldStopped, newerCompleted]), []);

  const uncertainReq = { ...request("stop-1", "2026-11-17T10:00:00Z", "", "stop"), receipt: { state: "outcome_uncertain", state_revision: 1 } } as RequestRecord;
  const markup = chatMarkup([], { snapshot: snapshot("awaiting_resume", true), requests: [uncertainReq] });
  assert.match(markup, /<option value="continue_work" disabled="">Continue original work<\/option>/);
  assert.match(markup, /<option value="release_held_queue" disabled="">Release held queue<\/option>/);
});

const resumeCard = /aria-label="Resume Copilot"/;
const heldRequest = (id: string) => ({ ...request(id, "2026-11-17T10:00:00Z", "Queued prompt"), receipt: { ...receipt, state: "held" } }) as RequestRecord;

test("held request IDs prefer current receipts unless the snapshot holds a newer revision", () => {
  const held = heldRequest("request");
  const dispatching = { ...held, receipt: { ...held.receipt, state: "dispatching" as const, state_revision: 2 } };
  const current = { ...snapshot(), held_requests: [held] };
  assert.deepEqual([...heldRequestIds(current, [dispatching])], []);
  assert.deepEqual([...heldRequestIds(current, [{ ...dispatching, receipt: { ...dispatching.receipt, state_revision: 1 } }])], []);
  assert.deepEqual([...heldRequestIds(current, [])], ["request"]);
  assert.deepEqual([...heldRequestIds({ ...current, held_requests: [{ ...held, receipt: { ...held.receipt, state_revision: 3 } }] }, [dispatching])], ["request"]);
  assert.deepEqual([...heldRequestIds(current, [held])], ["request"]);
});

test("running work from the auto-releasing request suppresses only its stale idle restart fence", () => {
  const initial = snapshot("running", true);
  const held = heldRequest("new");
  const current = { ...initial, active_work: { ...initial.active_work!, work_id: "w1" }, held_requests: [held],
    execution: { ...initial.execution!, hold_reason_code: "awaiting_resume" } };
  const dispatching = { ...held, receipt: { ...held.receipt, state: "dispatching" as const, state_revision: 2, work_id: "w1" } };
  const check = (snap: ProjectSnapshot, requests: RequestRecord[], paused: boolean) => {
    assert.equal(shouldShowResumeCard(snap, requests, 0, false, held.request_id), paused);
    assert.equal(shouldShowQueueHold(snap, "queue", requests, [], held.request_id), paused);
    assert.equal(assistantState({ snapshot: snap, requests, events: [], connection: "Connected", autoReleasingRequestId: held.request_id }).label, paused ? "Paused" : "Starting…");
  };
  check(current, [dispatching], false);
  check({ ...current, active_work: { ...current.active_work, work_id: "other-work" } }, [dispatching], true);
  check({ ...current, execution: { ...current.execution, hold_reason_code: "provider_unavailable" } }, [dispatching], true);
  check(current, [dispatching, heldRequest("other-request")], true);
});

test("backfill windows descend to the floor and reject non-contiguous event pages", () => {
  assert.deepEqual(backfillWindows(350), [
    { after: 250, end: 350, limit: 100 }, { after: 150, end: 250, limit: 100 },
    { after: 50, end: 150, limit: 100 }, { after: 0, end: 50, limit: 50 },
  ]);
  assert.deepEqual(backfillWindows(520, 450, 50, 1), [{ after: 470, end: 520, limit: 50 }]);
  assert.deepEqual(backfillWindows(450, 450), []);
  const page = [1, 2, 3, 4].map(sequence => event(sequence, "assistant.completed", {}));
  assert.deepEqual(validateWindowEvents(page, 1, 3), page.slice(1, 3));
  for (const bad of [[page[1]], [page[2], page[1]], [page[1], page[1], page[2]]]) {
    assert.throws(() => validateWindowEvents(bad, 1, 3), /Event journal has a gap/);
  }
});

test("idle restart status, Queue notice and Resume card share the same recovery boundary", () => {
  const initial = snapshot("completed", true);
  const idle = { ...initial, active_work: null, held_requests: [], execution: { ...initial.execution!, hold_reason_code: "awaiting_resume" } };
  const check = (current: ProjectSnapshot, requests: RequestRecord[], paused: boolean, autoId: string | null = null) => {
    assert.equal(isIdleAutoReleaseReady(current, requests, [], autoId), !paused);
    assert.equal(shouldShowQueueHold(current, "queue", requests, [], autoId), paused);
    assert.equal(shouldShowResumeCard(current, requests, 0, false, autoId), paused);
    const status = assistantState({ snapshot: current, requests, events: [], connection: "Connected", autoReleasingRequestId: autoId });
    if (paused) assert.ok(["Paused", "Needs recovery"].includes(status.label));
    else assert.equal(status.label, autoId ? "Starting…" : "Ready");
  };
  check(idle, [], false);
  check(idle, [heldRequest("new")], false, "new");
  check(idle, [heldRequest("new")], true);
  for (const hold_reason_code of ["work_stopped", "queue_paused", "resume_pending", "attribution_unavailable", "uncertain_stop"]) {
    check({ ...idle, execution: { ...idle.execution, hold_reason_code } }, [], true);
  }
  check({ ...idle, active_work: snapshot("awaiting_resume").active_work }, [], true);
  const legacy = { ...idle, execution: undefined, project: { ...idle.project, state: "awaiting_resume" as const } };
  check(legacy, [], true);
});

test("ordinary idle and FIFO Queue receipts do not show Resume but remain cancellable", () => {
  const requests = [heldRequest("follow-up")];
  for (const state of [null, "running", "awaiting_user"]) {
    const initial = snapshot(state ?? "completed");
    const current = { ...initial, active_work: state ? initial.active_work : null,
      execution: { ...initial.execution!, fenced: false, queue_released: true, hold_reason_code: state ? "active_work_running" : null } };
    assert.equal(shouldShowResumeCard(current, requests, 0, false), false);
    const markup = chatMarkup([], { snapshot: current, requests });
    assert.doesNotMatch(markup, resumeCard);
    assert.match(markup, /Cancel held request/);
    if (state === null) {
      const staleProject = { ...current, project: { ...current.project, state: "awaiting_resume" as const } };
      assert.equal(shouldShowQueueHold(staleProject, "queue", requests, []), false);
      assert.equal(shouldShowResumeCard(staleProject, requests, 0, false), false);
      assert.equal(assistantState({ snapshot: staleProject, requests, events: [], connection: "Connected" }).label, "Ready");
      assert.doesNotMatch(chatMarkup([], { snapshot: staleProject, requests }), resumeCard);
      const legacy = { ...staleProject, execution: undefined };
      assert.equal(shouldShowQueueHold(legacy, "queue", requests, []), true);
      assert.equal(shouldShowResumeCard(legacy, requests, 0, false), true);
      assert.equal(assistantState({ snapshot: legacy, requests, events: [], connection: "Connected" }).label, "Paused");
    }
  }
});

test("idle restart shows Resume only for a held request that is not auto-releasing or for a recovery fence", () => {
  const initial = snapshot("completed", true);
  const idle = { ...initial, active_work: null, held_requests: [], project: { ...initial.project, state: "awaiting_resume" as const },
    execution: { ...initial.execution!, hold_reason_code: "awaiting_resume" } };
  const newRequest = heldRequest("new");
  const held = { ...idle, held_requests: [newRequest] };
  assert.equal(shouldShowResumeCard(null, [newRequest], 1, true), false);
  assert.equal(shouldShowResumeCard(idle, [], 0, false), false);
  assert.doesNotMatch(chatMarkup([], { snapshot: idle }), resumeCard);
  assert.equal(shouldShowResumeCard(held, [newRequest], 0, false, "new"), false);
  assert.equal(shouldShowResumeCard({ ...held, execution: { ...held.execution, hold_reason_code: null } }, [newRequest], 0, false, "new"), false);
  assert.equal(shouldShowResumeCard(held, [heldRequest("other")], 0, false, "new"), true);
  assert.equal(shouldShowResumeCard(held, [], 0, false), true);
  assert.match(chatMarkup([], { snapshot: held }), resumeCard);
  for (const hold_reason_code of ["attribution_unavailable", "queue_paused"]) {
    const recovery = { ...held, execution: { ...held.execution, hold_reason_code } };
    assert.equal(shouldShowResumeCard(recovery, [newRequest], 0, false, "new"), true);
    assert.match(chatMarkup([], { snapshot: recovery }), resumeCard);
  }
});

test("Resume remains visible for interrupted work, uncertain Stops and an explicitly paused queue", () => {
  for (const state of ["awaiting_resume", "stopped", "failed", "outcome_uncertain", "running", "awaiting_user"]) {
    const current = snapshot(state, true);
    assert.equal(shouldShowResumeCard(current, [], 0, false), true);
    assert.match(chatMarkup([], { snapshot: current }), resumeCard);
  }
  const base = snapshot();
  const idle = { ...base, active_work: null, execution: { ...base.execution!, queue_released: false } };
  assert.equal(shouldShowResumeCard(idle, [], 0, false), true);
  assert.equal(shouldShowResumeCard(base, [], 0, true), true);
  assert.equal(shouldShowResumeCard(base, [], 1, false), true);
  const stopping = snapshot("stop_requested", true);
  stopping.execution!.hold_reason_code = "stop_requested";
  assert.equal(shouldShowResumeCard(stopping, [heldRequest("held")], 0, false), false);
});

test("ChatPanel auto-selects single interrupted work without rendering a work selector", () => {
  const markup = chatMarkup([], { snapshot: snapshot("awaiting_resume", true) });
  assert.match(markup, /<option value="continue_work" selected="">Continue original work<\/option>/);
  assert.match(markup, /<button>Resume<\/button>/);
  assert.doesNotMatch(markup, /aria-label="Original work to continue"/);
});

test("default Studio split layout starts Navigator and Assistant at maximum width", () => {
  assert.equal(NAVIGATOR_MAX_WIDTH, 520);
  assert.equal(ASSISTANT_MAX_WIDTH, 760);
  assert.deepEqual(defaultStudioLayout, {
    mode: "split",
    navigatorCollapsed: false,
    assistantCollapsed: false,
    navigatorWidth: 520,
    assistantWidth: 760,
  });
});

test("Critic SAST timestamps, revision-monotonic review merges and compact card markup", () => {
  assert.equal(formatReviewTimeSast("2026-11-18T14:05:00Z"), "2026-11-18 16:05 SAST");
  assert.equal(formatReviewTimeSast("2026-11-18T22:00:00Z"), "2026-11-19 00:00 SAST");
  assert.equal(formatReviewTimeSast(null), "Date unavailable");

  const olderPending: ReviewTicket = {
    schema_version: "1.0",
    review_id: "rev-1",
    job_id: "job-1",
    status: "ready",
    title: "Shot 4 hull containment",
    created_at: "2026-11-18T14:05:00Z",
    revision: 2,
    human_review_state: "pending",
    awaiting_human: true,
    finding_count: 2,
    unreviewed_count: 2,
  };
  const resolvedNewer: ReviewTicket = {
    ...olderPending,
    revision: 3,
    human_review_state: "resolved",
    awaiting_human: false,
    unreviewed_count: 0,
  };
  const selfCheck: ReviewTicket = {
    schema_version: "1.0",
    review_id: "rev-self",
    job_id: "job-2",
    status: "ready",
    title: "Routine self check",
    created_at: "2026-11-18T15:00:00Z",
    revision: 2,
    human_review_state: "not_requested",
    awaiting_human: false,
    finding_count: 1,
    unreviewed_count: 1,
  };

  // Stale lower-revision snapshot cannot resurrect a resolved review's awaiting_human state.
  const merged = mergeReviews([resolvedNewer], [olderPending, selfCheck]);
  assert.equal(merged.find(r => r.review_id === "rev-1")?.awaiting_human, false);
  assert.equal(isPendingReview(olderPending), true);
  assert.equal(isPendingReview(selfCheck), false);
  assert.equal(reviewDisplayName({ ...olderPending, title: "  " }), "rev-1");

  const markup = renderToStaticMarkup(
    <CriticPanel
      api={api}
      projectId="project"
      selection={emptySelection()}
      refreshRevision={0}
      reviews={[olderPending, selfCheck]}
      onQueue={async () => {}}
    />,
  );
  assert.match(markup, /Pending \(1\)/);
  assert.match(markup, /Shot 4 hull containment/);
  assert.match(markup, /2026-11-18 16:05 SAST/);
  assert.doesNotMatch(markup, /Routine self check/);
  assert.doesNotMatch(markup, /Load existing reviews/);
  assert.doesNotMatch(markup, /<select/);
});

test("Conversation suppresses draft_motion frame/segment floods inside user bubbles and OutputCards collapses >3 outputs", () => {
  const req = { ...request("req-anim", "2026-11-18T14:20:00Z", "let's generate the animation"), receipt: { state: "running", work_id: "work-anim", state_revision: 1 } } as RequestRecord;
  const streamSummaries: RetainedArtifactSummary[] = [
    {
      schema_version: "1.0",
      artifact: { schema_version: "1.0", artifact_id: "attempt-bd7475de-seg-1", project_id: "project", state: "ready", kind: "video", purpose: "draft_motion", media_type: "video/mp4", sha256: "a".repeat(64), bytes: 1024, dimensions: { width: 1280, height: 720 }, derived_from: [] },
      attribution_status: "verified",
      request_id: "req-anim",
      work_id: "work-anim",
      invocation_id: null,
      job_id: "preview-a44c700c",
      published_versions: [{ item_id: "corridor", version_id: "corridor-v2" }],
    },
    {
      schema_version: "1.0",
      artifact: { schema_version: "1.0", artifact_id: "preview-a44c700c-f0001", project_id: "project", state: "ready", kind: "image", purpose: "draft_motion", media_type: "image/png", sha256: "b".repeat(64), bytes: 2048, dimensions: { width: 1280, height: 720 }, derived_from: [] },
      attribution_status: "verified",
      request_id: "req-anim",
      work_id: "work-anim",
      invocation_id: null,
      job_id: "preview-a44c700c",
      published_versions: [{ item_id: "corridor", version_id: "corridor-v2" }],
    },
    {
      schema_version: "1.0",
      artifact: { schema_version: "1.0", artifact_id: "concept-unpublished-1", project_id: "project", state: "ready", kind: "image", purpose: "concept", media_type: "image/png", sha256: "c".repeat(64), bytes: 2048, dimensions: { width: 1024, height: 1024 }, derived_from: [] },
      attribution_status: "verified",
      request_id: "req-anim",
      work_id: "work-anim",
      invocation_id: null,
      job_id: null,
      published_versions: [],
    },
  ];
  const markup = renderToStaticMarkup(
    <Conversation api={api} projectId="project" requests={[req]} events={[]} retainedArtifacts={streamSummaries} onRefresh={async () => {}} onError={() => {}} />,
  );
  assert.doesNotMatch(markup, /attempt-bd7475de-seg-1/);
  assert.doesNotMatch(markup, /preview-a44c700c-f0001/);
  assert.match(markup, /concept-unpublished-1/);
  // User prompt bubble itself never contains OutputCards figures.
  const userBubble = markup.match(/<article class="message user-message"[\s\S]*?<\/article>/)?.[0] ?? "";
  assert.ok(userBubble.length > 0);
  assert.doesNotMatch(userBubble, /retained-output-card/);

  const fiveIds = ["c1", "c2", "c3", "c4", "c5"];
  const fiveSummaries = fiveIds.map(id => ({
    ...streamSummaries[2],
    artifact: { ...streamSummaries[2].artifact, artifact_id: id },
  }));
  const batchMarkup = renderToStaticMarkup(
    <OutputCards api={api} projectId="project" artifactIds={fiveIds} retainedArtifacts={fiveSummaries} />,
  );
  assert.match(batchMarkup, /Show 2 more retained outputs/);
});
