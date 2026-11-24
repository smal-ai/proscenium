import { useState } from "react";
import { ShieldQuestion } from "lucide-react";
import type { ApiClient, Decision, DecisionAnswer } from "../../api";
import { describeError, durableCommand, id } from "../../app/storage";
import { isSubmitEnter } from "./controls";

function permissionSummary(prompt: string): { heading: string; command?: string; detail?: string } | null {
  try {
    const parsed: unknown = JSON.parse(prompt);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return null;
    const value = parsed as Record<string, unknown>;
    if (value.kind === "shell" && typeof value.fullCommandText === "string") return { heading: "Approve shell command?", command: value.fullCommandText };
    if (value.kind === "mcp") return { heading: "Approve MCP tool call?", detail: [value.serverName, value.toolName, value.toolTitle].filter(part => typeof part === "string" && part).join(" · ") };
    if (value.kind === "read" || value.kind === "write") return { heading: "Approve file access?", detail: typeof value.path === "string" ? value.path : typeof value.fileName === "string" ? value.fileName : "Path not reported" };
    return { heading: "Approve operation?" };
  } catch { return null; }
}

export function DecisionCard({ api, decision, onRefresh }: { api: ApiClient; decision: Decision; onRefresh: () => Promise<void> }) {
  const [answer, setAnswer] = useState("");
  const [limit, setLimit] = useState(4);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const summary = decision.kind === "permission" ? permissionSummary(decision.prompt) : null;
  async function respond(action: DecisionAnswer["action"]) {
    setBusy(true); setError("");
    try {
      await durableCommand<DecisionAnswer, unknown>(`decision.${decision.decision_id}.${decision.state_revision}.${decision.fingerprint}`, () => ({ schema_version: "1.0", operation_id: id(), project_id: decision.project_id, expected_revision: decision.state_revision, fingerprint: decision.fingerprint, action, ...(action === "answer" ? { answer } : {}), ...(action === "extend_budget" ? { attempt_limit: limit } : {}) }), body => api.call("answerOwnerDecision", { path: { project_id: decision.project_id, decision_id: decision.decision_id }, body, idempotencyKey: body.operation_id }));
      await onRefresh();
    } catch (e) { setError(`${describeError(e)} Refresh before changing this decision. An unconfirmed answer retains its original identity.`); await onRefresh(); }
    finally { setBusy(false); }
  }
  return <section className="decision-card"><div className="eyebrow"><ShieldQuestion size={14} />{decision.kind.replaceAll("_", " ")}</div>
    {summary ? <><h3>{summary.heading}</h3>{summary.command !== undefined && <pre className="decision-code"><code>{summary.command}</code></pre>}{summary.detail && <code>{summary.detail}</code>}</> : <p>{decision.prompt}</p>}
    <details className="decision-details"><summary>Technical details &amp; fingerprint</summary>
      {summary && <pre className="decision-code"><code>{decision.prompt}</code></pre>}
      <small>Exact revision {decision.state_revision} · Work {decision.work_id}</small><code className="fingerprint">{decision.fingerprint}</code>
      {decision.options.length > 0 && <ul>{decision.options.map(option => <li key={option}>{option}</li>)}</ul>}
    </details>
    {decision.kind === "user_input" && <label>Your answer<textarea aria-label="Decision answer" placeholder="Type your answer…" value={answer} onChange={e => setAnswer(e.target.value)} onKeyDown={e => {
      if (isSubmitEnter(e)) {
        e.preventDefault();
        if (!busy && answer.trim()) void respond("answer");
      }
    }} /><small>Enter to send · Shift+Enter for newline</small></label>}
    {decision.kind === "generation_budget" && <label>New task attempt limit<input aria-label="New task attempt limit" type="number" min={1} value={limit} onChange={e => setLimit(Number(e.target.value))} onKeyDown={e => {
      if (isSubmitEnter(e)) {
        e.preventDefault();
        if (!busy && Number.isInteger(limit) && limit >= 1) void respond("extend_budget");
      }
    }} /></label>}
    <div className="button-row">{decision.kind === "permission" ? <button className="primary" disabled={busy} onClick={() => void respond("approve_once")}>Approve once</button> : decision.kind === "user_input" ? <button className="primary" disabled={busy || !answer.trim()} onClick={() => void respond("answer")}>Send answer</button> : <button className="primary" disabled={busy || !Number.isInteger(limit) || limit < 1} onClick={() => void respond("extend_budget")}>Extend task budget</button>}<button disabled={busy} onClick={() => void respond("decline")}>Decline</button></div>
    <small>Approval permits only this operation. It does not replace the original requirement or select creative output.</small>
    {error && <p role="alert">{error}</p>}
  </section>;
}
