import { memo, useCallback, useEffect, useRef, useState, type KeyboardEvent } from "react";
import { ArrowLeft, Check, Eye, MessageSquareWarning } from "lucide-react";
import { ApiError } from "../../api";
import type {
  Artifact,
  Attachment,
  CommandReceipt,
  DecisionAnswer,
  FrameTarget,
  ItemRecord,
  RequestTarget,
  ReviewDetail,
  ReviewFinding,
  ReviewRequest,
  ReviewTicket,
  RevisionCommand,
} from "../../api/generated/contracts";
import { describeError, durableCommand, id, readPrivate, writePrivate } from "../../app/storage";
import { emptySelection, type CriticSurfaceProps } from "../../api/studio";
import { versionRequestTarget } from "../chat/target";
import { handleSubmitEnter } from "../chat/controls";
import { cleanTargetRole } from "./context";
import { mediaUrl } from "./media";
import { allPages } from "./records";
import { reviewDisplayName } from "./reviewDisplayName";

export { reviewDisplayName } from "./reviewDisplayName";

export function criticRequestTarget(targets: FrameTarget[], items: ItemRecord[]): RequestTarget | null {
  const first = targets[0];
  if (!first?.item_id || !first.version_id || targets.some(target => target.item_id !== first.item_id || target.version_id !== first.version_id)) {
    return { kind: "project" };
  }
  const effectiveKind = items.find(item => item.item_id === first.item_id)?.kind
    ?? (first.origin === "sequence_render" ? "sequence" : undefined);
  const shotId = targets.every(target => target.shot_id === first.shot_id) ? first.shot_id : null;
  return versionRequestTarget({ item_id: first.item_id, version_id: first.version_id }, effectiveKind, shotId);
}

interface StagedFinding {
  text: string;
  revision: number;
}

const sastPartsFormatter = new Intl.DateTimeFormat("en-GB", {
  timeZone: "Africa/Johannesburg",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});

export function formatReviewTimeSast(iso?: string | null): string {
  if (!iso) return "Date unavailable";
  const parsed = Date.parse(iso);
  if (Number.isNaN(parsed)) return "Date unavailable";
  const parts = Object.fromEntries(
    sastPartsFormatter.formatToParts(new Date(parsed)).map(part => [part.type, part.value]),
  );
  const hour = parts.hour === "24" ? "00" : parts.hour;
  return `${parts.year}-${parts.month}-${parts.day} ${hour}:${parts.minute} SAST`;
}

export function isPendingReview(ticket: ReviewTicket): boolean {
  if (typeof ticket.awaiting_human === "boolean") return ticket.awaiting_human;
  return ticket.status === "pending" || ticket.status === "running";
}

function targetLabel(target: FrameTarget): string {
  const parts: string[] = [];
  if (target.shot_id) parts.push(target.shot_id);
  else if (target.version_id) parts.push(target.version_id);
  const frame = target.sequence_frame ?? target.source_frame;
  if (frame !== null && frame !== undefined) parts.push(`frame ${frame}`);
  else parts.push(target.origin.replaceAll("_", " "));
  return parts.join(" · ");
}

export const CriticPanel = memo(function CriticPanel({
  api,
  projectId,
  selection,
  refreshRevision,
  reviews: suppliedReviews,
  externalTarget,
  items = [],
  retainedArtifacts = [],
  pendingDecisions = [],
  onInspectArtifact,
  onRefresh,
  onQueue,
  onSteer,
}: CriticSurfaceProps) {
  const [filter, setFilter] = useState<"pending" | "all">(
    () => readPrivate<"pending" | "all">(`critic.filter.${projectId}`) ?? "pending",
  );
  const [titleDraft, setTitleDraft] = useState(
    () => readPrivate<string>(`critic.title.${projectId}`) ?? "",
  );
  const [criteria, setCriteria] = useState(
    () => readPrivate<string>(`critic.criteria.${projectId}`) ?? "",
  );
  const [pendingRequest, setPendingRequest] = useState<ReviewRequest | null>(() =>
    readPrivate<ReviewRequest>(`command.critic.request.${projectId}`),
  );
  const [localTickets, setLocalTickets] = useState<ReviewTicket[]>([]);
  const [reviewId, setReviewId] = useState<string | null>(() =>
    readPrivate<string>(`critic.selected.${projectId}`),
  );
  const [detail, setDetail] = useState<ReviewDetail | null>(null);
  const [findings, setFindings] = useState<ReviewFinding[]>([]);
  const [staged, setStaged] = useState<Record<string, StagedFinding>>({});
  const [decisionAnswer, setDecisionAnswer] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const focusedNonce = useRef<string | undefined>(undefined);
  const externalNavReviewId = useRef<string | null>(null);

  const tickets = (() => {
    const merged = new Map<string, ReviewTicket>();
    for (const t of [...(suppliedReviews ?? []), ...localTickets]) {
      const prev = merged.get(t.review_id);
      if (!prev || (t.revision ?? 0) >= (prev.revision ?? 0)) merged.set(t.review_id, t);
    }
    return [...merged.values()].sort((a, b) => {
      const ta = a.created_at ? Date.parse(a.created_at) : NaN;
      const tb = b.created_at ? Date.parse(b.created_at) : NaN;
      if (!Number.isNaN(ta) && !Number.isNaN(tb) && tb !== ta) return tb - ta;
      return 0;
    });
  })();

  useEffect(() => {
    if (suppliedReviews !== undefined) return;
    let active = true;
    allPages(cursor =>
      api.call("listOwnerReviews", { path: { project_id: projectId }, query: { cursor, limit: 100 } }),
    )
      .then(value => {
        if (active) setLocalTickets(value);
      })
      .catch(e => {
        if (active) setError(describeError(e));
      });
    return () => {
      active = false;
    };
  }, [api, projectId, suppliedReviews, refreshRevision]);

  const reviewRetryNonce = externalTarget?.reviewId === reviewId ? externalTarget.nonce : undefined;
  useEffect(() => {
    if (!reviewId) {
      setDetail(null);
      setFindings([]);
      setStaged({});
      return;
    }
    setStaged(readPrivate<Record<string, StagedFinding>>(`critic.staged.${projectId}.${reviewId}`) ?? {});
    const controller = new AbortController();
    const { signal } = controller;
    (async () => {
      try {
        const full = await api.call("getOwnerReview", {
          path: { project_id: projectId, review_id: reviewId }, signal,
        });
        if (signal.aborted) return;
        if (full.project_id !== projectId || full.ticket.review_id !== reviewId) throw new Error("Review response scope mismatch.");
        setDetail(full);
        setFindings(full.findings);
        setLocalTickets(prev => [full.ticket, ...prev.filter(t => t.review_id !== full.ticket.review_id)]);
      } catch (err) {
        if (signal.aborted) return;
        if (
          !(err instanceof ApiError) &&
          err instanceof Error &&
          /(?:not implemented|unsupported|unexpected|unhandled|unknown).*(?:getOwnerReview)|getOwnerReview.*(?:not implemented|unsupported|unexpected|unhandled|unknown)/i.test(
            err.message,
          )
        ) {
          try {
            const rows = await allPages(cursor =>
              api.call("listOwnerReviewFindings", {
                path: { project_id: projectId, review_id: reviewId },
                query: { cursor, limit: 100 }, signal,
              }),
            );
            if (!signal.aborted) {
              setDetail(null);
              setFindings(rows);
            }
          } catch (fallbackError) {
            if (!signal.aborted) setError(`Could not open review ${reviewId}. ${describeError(fallbackError)}`);
          }
          return;
        }
        setError(`Could not open review ${reviewId}. ${describeError(err)}`);
      }
    })();
    return () => controller.abort();
  }, [api, projectId, reviewId, refreshRevision, reviewRetryNonce]);

  const selectReview = useCallback((nextId: string | null) => {
    if (nextId !== externalNavReviewId.current) externalNavReviewId.current = null;
    if (nextId !== reviewId) {
      setDetail(null);
      setFindings([]);
      setStaged({});
      setDecisionAnswer("");
    }
    setReviewId(nextId);
    writePrivate(`critic.selected.${projectId}`, nextId);
    setError("");
  }, [projectId, reviewId]);

  const changeFilter = useCallback((next: "pending" | "all") => {
    externalNavReviewId.current = null;
    setFilter(next);
    writePrivate(`critic.filter.${projectId}`, next);
  }, [projectId]);

  useEffect(() => {
    if (!externalTarget || focusedNonce.current === externalTarget.nonce) return;
    focusedNonce.current = externalTarget.nonce;
    selectReview(externalTarget.reviewId);
    externalNavReviewId.current = externalTarget.reviewId;
  }, [externalTarget, selectReview]);

  // The merged ticket can change once the detail read updates localTickets.
  const latestSelectedTicket = tickets.find(ticket => ticket.review_id === reviewId);
  useEffect(() => {
    if (!latestSelectedTicket || latestSelectedTicket.review_id !== externalNavReviewId.current || isPendingReview(latestSelectedTicket)) return;
    externalNavReviewId.current = null;
    if (filter === "pending") changeFilter("all");
  }, [externalTarget, latestSelectedTicket, filter, changeFilter]);

  function changeCriteria(value: string) {
    setCriteria(value);
    writePrivate(`critic.criteria.${projectId}`, value);
  }

  function changeTitle(value: string) {
    setTitleDraft(value);
    writePrivate(`critic.title.${projectId}`, value);
  }

  function updateStaged(
    targetReviewId: string,
    updater: (prev: Record<string, StagedFinding>) => Record<string, StagedFinding>,
  ) {
    setStaged(prev => {
      const next = updater(prev);
      writePrivate(
        `critic.staged.${projectId}.${targetReviewId}`,
        Object.keys(next).length ? next : null,
      );
      return next;
    });
  }

  async function inspectTarget(target: FrameTarget) {
    if (!onInspectArtifact) return;
    const known = retainedArtifacts.find(r => r.artifact.artifact_id === target.artifact_id)?.artifact;
    if (known) {
      onInspectArtifact(known);
      return;
    }
    try {
      const artifact: Artifact = await api.call("getArtifact", {
        path: { project_id: projectId, artifact_id: target.artifact_id },
      });
      onInspectArtifact(artifact);
    } catch (e) {
      setError(describeError(e));
    }
  }

  function submitReviewOnEnter(e: KeyboardEvent<HTMLInputElement | HTMLTextAreaElement>) {
    handleSubmitEnter(e, () => {
      if (!busy && (pendingRequest || (selection.target && criteria.trim()))) void requestReview();
    });
  }

  async function requestReview() {
    if (busy || (!pendingRequest && (!selection.target || !criteria.trim()))) return;
    setBusy(true);
    setError("");
    try {
      const cleanTitle = titleDraft.trim();
      const ticket = await durableCommand<ReviewRequest & { operation_id: string }, ReviewTicket>(
        `critic.request.${projectId}`,
        () => {
          const rid = id();
          return {
            schema_version: "1.0",
            operation_id: rid,
            review_id: rid,
            project_id: projectId,
            targets: [structuredClone(selection.target!)],
            criteria: criteria.trim(),
            telemetry_artifact_ids: [],
            ...(cleanTitle ? { title: cleanTitle.slice(0, 160) } : {}),
            human_review: true,
          };
        },
        async command => {
          const { operation_id, ...body } = command;
          setPendingRequest(body);
          return api.call("requestOwnerReview", {
            path: { project_id: projectId },
            body,
            idempotencyKey: operation_id,
          });
        },
      );
      setPendingRequest(null);
      changeCriteria("");
      changeTitle("");
      setLocalTickets(prev => [ticket, ...prev.filter(t => t.review_id !== ticket.review_id)]);
      selectReview(ticket.review_id);
      if (onRefresh) await onRefresh();
    } catch (e) {
      setPendingRequest(readPrivate<ReviewRequest>(`command.critic.request.${projectId}`));
      setError(`Review request retained for exact retry: ${describeError(e)}`);
    } finally {
      setBusy(false);
    }
  }

  async function reject(finding: ReviewFinding) {
    setBusy(true);
    setError("");
    try {
      const ack = await durableCommand<RevisionCommand, CommandReceipt>(
        `critic.reject.${finding.finding_id}.${finding.revision}`,
        () => ({
          schema_version: "1.0",
          operation_id: id(),
          project_id: projectId,
          expected_revision: finding.revision,
        }),
        body =>
          api.call("rejectReviewFinding", {
            path: {
              project_id: projectId,
              review_id: finding.review_id,
              finding_id: finding.finding_id,
            },
            body,
            idempotencyKey: body.operation_id,
          }),
      );
      setFindings(prev =>
        prev.map(f =>
          f.finding_id === finding.finding_id
            ? { ...f, state: "rejected", revision: ack.state_revision }
            : f,
        ),
      );
      updateStaged(finding.review_id, prev => {
        const next = { ...prev };
        delete next[finding.finding_id];
        return next;
      });
      if (onRefresh) await onRefresh();
    } catch (e) {
      setError(describeError(e));
    } finally {
      setBusy(false);
    }
  }

  async function resolveReview(ticket: ReviewTicket) {
    setBusy(true);
    setError("");
    try {
      const expected = ticket.revision ?? 1;
      await durableCommand<RevisionCommand, unknown>(
        `critic.resolve.${ticket.review_id}.${expected}`,
        () => ({
          schema_version: "1.0",
          operation_id: id(),
          project_id: projectId,
          expected_revision: expected,
        }),
        body =>
          api.call("resolveOwnerReview", {
            path: { project_id: projectId, review_id: ticket.review_id },
            body,
            idempotencyKey: body.operation_id,
          }),
      );
      const updated: ReviewTicket = {
        ...ticket,
        awaiting_human: false,
        human_review_state: "resolved",
        revision: expected + 1,
      };
      setLocalTickets(prev => [updated, ...prev.filter(t => t.review_id !== ticket.review_id)]);
      if (detail?.ticket.review_id === ticket.review_id) {
        setDetail({ ...detail, ticket: updated });
      }
      if (filter === "pending") selectReview(null);
      if (onRefresh) await onRefresh();
    } catch (e) {
      setError(describeError(e));
    } finally {
      setBusy(false);
    }
  }

  const selectedTicket = reviewId
    ? detail?.ticket.review_id === reviewId
      ? detail.ticket
      : latestSelectedTicket ?? null
    : null;

  const selectedFindings = findings.filter(
    f => f.state !== "submitted" && Boolean(staged[f.finding_id]?.text.trim()),
  );

  async function submitSelected(steer = false) {
    if (busy || !reviewId || !selectedFindings.length) return;
    if (steer && !onSteer) {
      setError("Steer is no longer available. Choose Queue explicitly.");
      return;
    }
    const clean = new Map<string, Attachment>();
    for (const finding of selectedFindings) {
      clean.set(finding.target.artifact_id, {
        artifact_id: finding.target.artifact_id,
        role: cleanTargetRole(finding.target),
      });
    }
    const attachments = [...clean.values()];
    for (const finding of selectedFindings) {
      for (const evidence of finding.evidence) {
        if (
          evidence.kind === "visual" &&
          !attachments.some(a => a.artifact_id === evidence.artifact_id)
        ) {
          attachments.push({ artifact_id: evidence.artifact_id, role: "reference" });
        }
      }
    }
    const first = selectedFindings[0].target;
    const requestTarget = criticRequestTarget(selectedFindings.map(f => f.target), items);
    // Null still identifies a single viewed sequence or Unity result.
    const singleVersion = requestTarget?.kind !== "project";

    setBusy(true);
    setError("");
    try {
      await (steer ? onSteer! : onQueue)(
        selectedFindings.map(f => staged[f.finding_id].text.trim()).join("\n\n"),
        {
          ...emptySelection(),
          request_target: requestTarget,
          item_id: singleVersion ? first.item_id : null,
          version_id: singleVersion ? first.version_id : null,
          target: singleVersion ? structuredClone(first) : null,
          camera: null,
          view_mode: singleVersion ? (first.media_artifact_id ? "animation" : "keyframes") : "none",
          range: null,
          selected_objects: [],
          annotations: [],
          attachments,
          approved_findings: selectedFindings.map(f => ({
            finding_id: f.finding_id,
            finding_revision: staged[f.finding_id]?.revision ?? f.revision,
            approved_text: staged[f.finding_id].text.trim(),
            target: structuredClone(f.target),
          })),
        },
      );
      setFindings(prev =>
        prev.map(f =>
          selectedFindings.some(sel => sel.finding_id === f.finding_id)
            ? { ...f, state: "submitted", revision: f.revision + 1 }
            : f,
        ),
      );
      updateStaged(reviewId, () => ({}));
      if (onRefresh) await onRefresh();
    } catch (e) {
      setError(`Submission failed. Findings remain available: ${describeError(e)}`);
    } finally {
      setBusy(false);
    }
  }

  const linkedUserDecision =
    selectedTicket?.question && pendingDecisions.find(d => d.kind === "user_input" && d.state === "pending");

  async function answerLinkedDecision() {
    if (!linkedUserDecision || !decisionAnswer.trim() || busy) return;
    setBusy(true);
    setError("");
    try {
      await durableCommand<DecisionAnswer, unknown>(
        `decision.${linkedUserDecision.decision_id}.${linkedUserDecision.state_revision}.${linkedUserDecision.fingerprint}`,
        () => ({
          schema_version: "1.0",
          operation_id: id(),
          project_id: linkedUserDecision.project_id,
          expected_revision: linkedUserDecision.state_revision,
          fingerprint: linkedUserDecision.fingerprint,
          action: "answer",
          answer: decisionAnswer.trim(),
        }),
        body =>
          api.call("answerOwnerDecision", {
            path: { project_id: projectId, decision_id: linkedUserDecision.decision_id },
            body,
            idempotencyKey: body.operation_id,
          }),
      );
      setDecisionAnswer("");
      if (selectedTicket.awaiting_human && (selectedTicket.unreviewed_count ?? 0) === 0) {
        await resolveReview(selectedTicket);
      } else if (onRefresh) {
        await onRefresh();
      }
    } catch (e) {
      setError(describeError(e));
    } finally {
      setBusy(false);
    }
  }

  const pendingTickets = tickets.filter(isPendingReview);
  const visibleTickets = filter === "pending" ? pendingTickets : tickets;

  return (
    <section className="review-critic" aria-label="Critic">
      <div className="critic-scroll">
        <div className="critic-header">
          <span className="copilot-mark">
            <Eye size={16} />
          </span>
          <div>
            <strong>Visual Critic</strong>
            <small>
              {pendingTickets.length
                ? `${pendingTickets.length} ${pendingTickets.length === 1 ? "review" : "reviews"} awaiting you`
                : "No pending reviews"}
            </small>
          </div>
          <div className="segmented-control critic-filter" role="group" aria-label="Review filter">
            <button
              type="button"
              aria-pressed={filter === "pending"}
              onClick={() => changeFilter("pending")}
            >
              Pending{pendingTickets.length ? ` (${pendingTickets.length})` : ""}
            </button>
            <button
              type="button"
              aria-pressed={filter === "all"}
              onClick={() => changeFilter("all")}
            >
              All
            </button>
          </div>
        </div>

        {!selectedTicket && (
          <div className="critic-card-list" role="list" aria-label="Reviews">
            {visibleTickets.map(ticket => (
              <button
                key={ticket.review_id}
                type="button"
                role="listitem"
                className="critic-review-card"
                onClick={() => selectReview(ticket.review_id)}
              >
                <strong className="critic-card-title">{reviewDisplayName(ticket)}</strong>
                <time className="critic-card-time" dateTime={ticket.created_at ?? undefined}>
                  {formatReviewTimeSast(ticket.created_at)}
                </time>
              </button>
            ))}
            {!visibleTickets.length && (
              <div className="critic-empty">
                <h3>{filter === "pending" ? "No pending reviews" : "No reviews yet"}</h3>
                <p>
                  {filter === "pending"
                    ? "Copilot escalations and critiques needing your input appear here."
                    : "Select a frame or image in Review and ask Critic below."}
                </p>
              </div>
            )}
          </div>
        )}

        {selectedTicket && (
          <div className="critic-thread">
            <div className="critic-thread-bar">
              <button type="button" className="subtle" onClick={() => selectReview(null)}>
                <ArrowLeft size={13} />
                Back to reviews
              </button>
              {isPendingReview(selectedTicket) && (
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => void resolveReview(selectedTicket)}
                >
                  <Check size={13} />
                  Done without changes
                </button>
              )}
            </div>

            <article className="critic-entry critic-request-entry">
              <div className="critic-entry-heading">
                <strong>{selectedTicket.question ? "Copilot" : "Review request"}</strong>
                <span>{formatReviewTimeSast(selectedTicket.created_at)}</span>
              </div>
              <p className="critic-thread-title">{reviewDisplayName(selectedTicket)}</p>
              {detail?.criteria && <p>{detail.criteria}</p>}
              {detail?.targets && detail.targets.length > 0 && (
                <div className="critic-target-strip">
                  {detail.targets.map((target, idx) => (
                    <button
                      key={`${target.artifact_id}.${idx}`}
                      type="button"
                      className="critic-thumb"
                      title={`Inspect ${targetLabel(target)}`}
                      onClick={() => void inspectTarget(target)}
                    >
                      <img
                        src={mediaUrl(projectId, target.artifact_id)}
                        alt={`Target ${target.artifact_id}`}
                      />
                      <small>{targetLabel(target)}</small>
                    </button>
                  ))}
                </div>
              )}
              {selectedTicket.question && (
                <div className="critic-question" role="note">
                  <div className="eyebrow">
                    <MessageSquareWarning size={13} />
                    Question for you
                  </div>
                  <p>{selectedTicket.question}</p>
                </div>
              )}
            </article>

            {linkedUserDecision && (
              <section className="critic-decision-box" aria-label="Answer Copilot question">
                <label>
                  Reply to waiting Copilot question
                  <textarea
                    aria-label="Reply to Copilot question"
                    value={decisionAnswer}
                    onChange={e => setDecisionAnswer(e.target.value)}
                    placeholder="Answer Copilot directly…"
                    onKeyDown={e => handleSubmitEnter(e, () => {
                      if (!busy && decisionAnswer.trim()) void answerLinkedDecision();
                    })}
                  />
                </label>
                <div className="button-row">
                  <button
                    type="button"
                    className="primary"
                    disabled={busy || !decisionAnswer.trim()}
                    onClick={() => void answerLinkedDecision()}
                  >
                    Send answer
                  </button>
                </div>
              </section>
            )}

            {(selectedTicket.status === "pending" || selectedTicket.status === "running") &&
              selectedTicket.error_code !== "outcome_uncertain" && (
                <div className="activity" role="status">
                  <div className="activity-heading">
                    <strong>
                      <span className="activity-dot" /> Critic · Evaluating visual criteria…
                    </strong>
                    <span>{selectedTicket.status}</span>
                  </div>
                </div>
              )}

            {(selectedTicket.status === "failed" ||
              selectedTicket.error_code === "outcome_uncertain") && (
              <div
                className={`activity ${selectedTicket.error_code === "outcome_uncertain" ? "activity-outcome_uncertain" : "activity-failed"}`}
                role="status"
              >
                <div className="activity-heading">
                  <strong>Critic · Review did not complete</strong>
                  <span>{selectedTicket.error_code ?? "failed"}</span>
                </div>
                <p className="activity-error">
                  {selectedTicket.error_code === "outcome_uncertain"
                    ? "The vision provider did not confirm completion before timeout. Do not assume zero defects."
                    : `Visual critique failed (${selectedTicket.error_code ?? "failed"}).`}
                </p>
                {detail?.diagnostics && (
                  <details className="activity-diagnostics">
                    <summary>Diagnostics</summary>
                    <small>
                      Finish: {detail.diagnostics.finish_reason ?? "not reported"} · Tokens:{" "}
                      {detail.diagnostics.completion_tokens ?? "n/a"} · Reasoning:{" "}
                      {detail.diagnostics.reasoning_effort ?? "default"}
                    </small>
                  </details>
                )}
              </div>
            )}

            {selectedTicket.status === "ready" && findings.length === 0 && (
              <article className="critic-entry critic-finding-entry">
                <div className="critic-entry-heading">
                  <strong>Critic</strong>
                  <span>0 defects</span>
                </div>
                <p>No defects reported.</p>
              </article>
            )}

            {findings.map(finding => {
              const checked = finding.finding_id in staged;
              return (
                <article
                  key={finding.finding_id}
                  className="critic-entry critic-finding-entry critic-finding"
                >
                  <div className="critic-entry-heading">
                    <strong>Critic</strong>
                    <span>
                      {targetLabel(finding.target)} · {finding.state} · rev {finding.revision}
                    </span>
                  </div>
                  <button
                    type="button"
                    className="critic-thumb"
                    onClick={() => void inspectTarget(finding.target)}
                  >
                    <img
                      src={mediaUrl(projectId, finding.target.artifact_id)}
                      alt={`Reviewed target ${finding.target.artifact_id}`}
                    />
                  </button>
                  <p>{finding.observation}</p>
                  {finding.recommendation && (
                    <p className="critic-recommendation">{finding.recommendation}</p>
                  )}
                  <details className="activity-diagnostics">
                    <summary>Evidence ({finding.evidence.length})</summary>
                    <ul>
                      {finding.evidence.map((ev, idx) => (
                        <li key={idx}>
                          <strong>
                            {ev.kind === "measured" ? "Measured telemetry" : "Visual observation"}
                          </strong>
                          : {ev.description} <code>{ev.artifact_id}</code>
                        </li>
                      ))}
                    </ul>
                  </details>
                  <div className="critic-finding-actions">
                    <label className="critic-include">
                      <input
                        type="checkbox"
                        disabled={busy || finding.state === "submitted"}
                        checked={checked}
                        onChange={e =>
                          updateStaged(finding.review_id, prev => {
                            const next = { ...prev };
                            if (e.target.checked) {
                              next[finding.finding_id] = {
                                text: finding.recommendation || finding.observation,
                                revision: finding.revision,
                              };
                            } else {
                              delete next[finding.finding_id];
                            }
                            return next;
                          })
                        }
                      />
                      Include finding
                    </label>
                    <button
                      type="button"
                      className="subtle"
                      disabled={busy || finding.state !== "unreviewed"}
                      onClick={() => void reject(finding)}
                    >
                      Reject finding
                    </button>
                  </div>
                  {checked && (
                    <textarea
                      aria-label={`Approved instruction ${finding.finding_id}`}
                      disabled={busy}
                      value={staged[finding.finding_id]?.text ?? ""}
                      onChange={e =>
                        updateStaged(finding.review_id, prev => ({
                          ...prev,
                          [finding.finding_id]: {
                            text: e.target.value,
                            revision: prev[finding.finding_id]?.revision ?? finding.revision,
                          },
                        }))
                      }
                    />
                  )}
                </article>
              );
            })}
          </div>
        )}
      </div>

      <div className="critic-composer">
        {error && <p role="alert">{error}</p>}
        {selectedTicket && selectedFindings.length > 0 ? (
          <div className="critic-submit-bar">
            <small>
              {selectedFindings.length}{" "}
              {selectedFindings.length === 1 ? "finding" : "findings"} staged on{" "}
              {targetLabel(selectedFindings[0].target)}
            </small>
            <div className="button-row">
              <button
                type="button"
                className="primary"
                disabled={busy}
                onClick={() => void submitSelected(false)}
              >
                Queue selected findings once
              </button>
              {onSteer && (
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => void submitSelected(true)}
                >
                  Steer selected findings once
                </button>
              )}
            </div>
            <small>Only your selected instructions enter Copilot. No review automatically changes Blender.</small>
          </div>
        ) : (
          <>
            <input
              type="text"
              aria-label="Review name"
              value={titleDraft}
              disabled={Boolean(pendingRequest) || busy}
              onChange={e => changeTitle(e.target.value)}
              onKeyDown={submitReviewOnEnter}
              placeholder="Review name (optional, e.g. Shot 4 hull containment)"
            />
            <label className="sr-only" htmlFor="critic-criteria">
              Review criteria
            </label>
            <textarea
              id="critic-criteria"
              aria-label="Review criteria"
              value={criteria}
              disabled={Boolean(pendingRequest) || busy}
              onChange={e => changeCriteria(e.target.value)}
              placeholder={
                selection.target
                  ? "Describe the visual criteria to verify against the inspected frame…"
                  : "Select a published frame or image in Review first…"
              }
              onKeyDown={submitReviewOnEnter}
            />
            <div className="composer-actions">
              <small className="critic-target-hint">
                {selection.target ? `Target: ${targetLabel(selection.target)}` : "No Review frame selected"}
              </small>
              <button
                type="button"
                className="primary send"
                disabled={busy || (!pendingRequest && (!selection.target || !criteria.trim()))}
                onClick={() => void requestReview()}
              >
                {pendingRequest ? "Retry original review" : "Request visual review"}
              </button>
            </div>
          </>
        )}
      </div>
    </section>
  );
});
