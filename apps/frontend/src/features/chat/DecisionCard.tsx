import { useState } from "react";
import { ShieldQuestion } from "lucide-react";
import type { ApiClient, Decision, DecisionAnswer } from "../../api";
import { describeError, durableCommand, id } from "../../app/storage";

export function DecisionCard({ api, decision, onRefresh }: { api: ApiClient; decision: Decision; onRefresh: () => Promise<void> }) {
  const [answer, setAnswer] = useState("");
  const [limit, setLimit] = useState(4);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function respond(action: DecisionAnswer["action"]) {
    setBusy(true); setError("");
    try {
      await durableCommand<DecisionAnswer, unknown>(`decision.${decision.decision_id}.${decision.state_revision}.${decision.fingerprint}`, () => ({ schema_version: "1.0", operation_id: id(), project_id: decision.project_id, expected_revision: decision.state_revision, fingerprint: decision.fingerprint, action, ...(action === "answer" ? { answer } : {}), ...(action === "extend_budget" ? { attempt_limit: limit } : {}) }), body => api.call("answerOwnerDecision", { path: { project_id: decision.project_id, decision_id: decision.decision_id }, body, idempotencyKey: body.operation_id }));
      await onRefresh();
    } catch (e) { setError(`${describeError(e)} Refresh before changing this decision. An unconfirmed answer retains its original identity.`); await onRefresh(); }
    finally { setBusy(false); }
  }
  return <section className="decision-card"><div className="eyebrow"><ShieldQuestion size={14} />{decision.kind.replaceAll("_", " ")}</div><p>{decision.prompt}</p>
    <small>Exact revision {decision.state_revision} · Work {decision.work_id}</small><code className="fingerprint">{decision.fingerprint}</code>
    {decision.options.length > 0 && <ul>{decision.options.map(option => <li key={option}>{option}</li>)}</ul>}
    {decision.kind === "user_input" && <label>Your answer<textarea aria-label="Decision answer" value={answer} onChange={e => setAnswer(e.target.value)} /></label>}
    {decision.kind === "generation_budget" && <label>New task attempt limit<input aria-label="New task attempt limit" type="number" min={1} value={limit} onChange={e => setLimit(Number(e.target.value))} /></label>}
    <div className="button-row">{decision.kind === "permission" ? <button className="primary" disabled={busy} onClick={() => void respond("approve_once")}>Approve once</button> : decision.kind === "user_input" ? <button className="primary" disabled={busy || !answer.trim()} onClick={() => void respond("answer")}>Send answer</button> : <button className="primary" disabled={busy || !Number.isInteger(limit) || limit < 1} onClick={() => void respond("extend_budget")}>Extend task budget</button>}<button disabled={busy} onClick={() => void respond("decline")}>Decline</button></div>
    <small>Approval permits only this operation. It does not replace the original requirement or select creative output.</small>
    {error && <p role="alert">{error}</p>}
  </section>;
}
