import { useCallback, useEffect, useRef, useState } from "react";
import { Box, Film, Image, Layers, PanelLeft, RefreshCw, Sparkles } from "lucide-react";
import { ApiClient, ApiError } from "../api";
import type { ConfigurationView, ProjectRecord, VersionRecord } from "../api";
import type { ContextPreparer, ReviewSurfaceProps, StudioComponents, TurnSelection } from "../api/studio";
import { emptySelection } from "../api/studio";
import { ProjectMenu } from "../features/projects/ProjectMenu";
import { CatalogueFallback } from "../features/projects/CatalogueFallback";
import { ChatPanel } from "../features/chat/ChatPanel";
import { useRequestComposer } from "../features/chat/useRequestComposer";
import { ConfigurationMenu } from "./ConfigurationMenu";
import { Pairing, restoreOwner } from "./Pairing";
import { allPages, useProjectData } from "./useProjectData";
import { describeError, readPrivate, writePrivate } from "./storage";
import "@fontsource/inter/400.css";
import "@fontsource/inter/500.css";
import "@fontsource/inter/600.css";
import "@fontsource/jetbrains-mono/400.css";
import "./studio.css";

export function StudioApp({ components = {} }: { components?: StudioComponents }) {
  const [api] = useState(() => new ApiClient());
  const [paired, setPaired] = useState(() => restoreOwner(api) && !readPrivate("pairing"));
  const [projects, setProjects] = useState<ProjectRecord[]>([]);
  const [configuration, setConfiguration] = useState<ConfigurationView | null>(null);
  const [error, setError] = useState("");
  const unauthorised = useCallback(() => { writePrivate("session", null); setPaired(false); }, []);
  const refreshProjects = useCallback(async () => {
    try { setProjects(await allPages(cursor => api.call("listProjects", { query: { cursor, limit: 100 } }))); }
    catch (e) { setError(describeError(e)); if (e instanceof ApiError && e.status === 401) unauthorised(); }
  }, [api, unauthorised]);
  useEffect(() => {
    if (!paired) return;
    void refreshProjects();
    void api.call("getConfigurationView").then(setConfiguration).catch(e => { setError(describeError(e)); if (e instanceof ApiError && e.status === 401) unauthorised(); });
  }, [api, paired, refreshProjects, unauthorised]);
  const active = projects.find(p => p.state === "active" || p.state === "awaiting_resume");
  if (!paired) return <Pairing api={api} onPaired={() => setPaired(true)} />;
  return <ProjectStudio key={active?.project_id ?? "none"} api={api} projectId={active?.project_id ?? null} projects={projects} configuration={configuration} components={components} onRefreshProjects={refreshProjects} onUnauthorised={unauthorised} rootError={error} />;
}

function ProjectStudio({ api, projectId, projects, configuration, components, onRefreshProjects, onUnauthorised, rootError }: {
  api: ApiClient; projectId: string | null; projects: ProjectRecord[]; configuration: ConfigurationView | null; components: StudioComponents; onRefreshProjects: () => Promise<void>; onUnauthorised: () => void; rootError: string;
}) {
  const data = useProjectData(api, projectId, onUnauthorised);
  const [leftTab, setLeftTab] = useState<"Shots" | "Outliner" | "Assets">("Assets");
  const [rightTab, setRightTab] = useState<"Copilot" | "Critic">("Copilot");
  const [version, setVersion] = useState<VersionRecord | null>(() => projectId ? readPrivate(`view.version.${projectId}`) : null);
  const [selection, setSelection] = useState<TurnSelection>(() => projectId ? readPrivate<TurnSelection>(`view.context.${projectId}`) ?? emptySelection() : emptySelection());
  const contextPreparer = useRef<ContextPreparer | null>(null);
  const onContextPreparer = useCallback((prepare: ContextPreparer | null) => { contextPreparer.current = prepare; }, []);
  const selectionRef = useRef(selection); selectionRef.current = selection;
  const prepareContext = useCallback(async () => contextPreparer.current ? contextPreparer.current() : structuredClone(selectionRef.current), []);
  const composer = useRequestComposer(api, projectId ?? "none", () => { void data.refresh(); });
  const onContextChange = useCallback((next: TurnSelection) => {
    const captured = structuredClone(next);
    setSelection(captured);
    if (projectId) writePrivate(`view.context.${projectId}`, captured);
  }, [projectId]);
  function onSelect(next: VersionRecord) {
    setVersion(next);
    writePrivate(`view.version.${projectId}`, next);
    onContextChange({ ...emptySelection(), item_id: next.item_id, version_id: next.version_id, view_mode: "keyframes" });
  }
  function onNavigate(next: VersionRecord, context: TurnSelection) {
    setVersion(next);
    writePrivate(`view.version.${projectId}`, next);
    onContextChange(context);
  }
  async function onQueue(text: string, context: TurnSelection) {
    setRightTab("Copilot");
    if (!projectId || !await composer.submit(text, context, "queue", null, data.snapshot?.settings.default_profile_id ?? null)) {
      throw new Error("Submission is not confirmed. Inspect the retained request in Copilot before retrying.");
    }
  }
  const activeWork = data.snapshot?.active_work;
  const canSteer = !!activeWork && ["running", "awaiting_user"].includes(activeWork.state);
  async function onSteer(text: string, context: TurnSelection) {
    setRightTab("Copilot");
    if (!projectId || !canSteer || !await composer.submit(text, context, "steer", activeWork!.work_id, data.snapshot?.settings.default_profile_id ?? null)) {
      throw new Error("Steer is not confirmed. Inspect the retained request in Copilot before retrying.");
    }
  }
  const reviewProps: ReviewSurfaceProps = { api, projectId: projectId ?? "", version, selection, jobs: data.jobs, refreshRevision: data.revision, onContextChange, onContextPreparer, onNavigate, onQueue };
  const Review = components.review;
  const Outliner = components.outliner;
  const Catalogue = components.catalogue ?? CatalogueFallback;
  const Storyboards = components.storyboards;
  const Critic = components.critic;
  return <div className="studio-shell">
    <header className="studio-header"><div className="brand"><span className="brand-mark">P</span><strong>PROSCENIUM</strong></div><div className="header-divider" /><ProjectMenu api={api} projects={projects} activeId={projectId} onRefresh={onRefreshProjects} />
      <code className="workspace-path" title={data.snapshot?.project.path}>{data.snapshot?.project.path ?? "No active workstation folder"}</code>
      <div className="header-spacer" /><span className={data.connection === "Connected" ? "connection connected" : "connection"}><span />{data.connection}</span><button aria-label="Refresh project metadata" title="Refresh metadata without sending a prompt" onClick={() => { void onRefreshProjects(); void data.refresh(); }}><RefreshCw size={14} /></button><ConfigurationMenu api={api} configuration={configuration} snapshot={data.snapshot} onRefresh={data.refresh} />
    </header>
    <div className="studio-body">
      <aside className="left-panel" aria-label="Project navigation"><nav className="panel-tabs" aria-label="Project navigation tabs">{(["Shots", "Outliner", "Assets"] as const).map(tab => <button key={tab} aria-selected={leftTab === tab} onClick={() => setLeftTab(tab)}>{tab}</button>)}</nav>
        <div className="left-scroll">{!projectId ? <Empty icon={<Layers size={24} />} title="No active project" text="Open or create a workstation folder using the project menu." /> : leftTab === "Assets" ? <Catalogue api={api} projectId={projectId} items={data.items} selectedVersionId={version?.version_id ?? null} refreshRevision={data.revision} onSelect={onSelect} /> : leftTab === "Outliner" ? Outliner ? <Outliner {...reviewProps} /> : <Empty icon={<PanelLeft size={24} />} title="Outliner unavailable" text="The published proxy outliner is not installed. No scene objects have been inferred." /> : Storyboards ? <Storyboards {...reviewProps} /> : <Empty icon={<Film size={24} />} title="Shot browser unavailable" text="Storyboard navigation is not installed. Published items remain available under Assets." />}</div>
        <footer className="panel-footer"><span>Active-project scope</span><code>{projectId ?? "Not selected"}</code></footer>
      </aside>
      <main className="review-panel" aria-label="Review workspace"><div className="review-heading"><div><span className="eyebrow">Review workspace</span><h1>{version?.name ?? "Start with a project. Make something worth reviewing."}</h1></div>{version && <div className="source-identity"><code>Git {version.source_commit.slice(0, 10)}</code><span title={version.version_id}>Pinned · {version.version_id}</span></div>}</div>
        {(rootError || data.error) && <div className="metadata-error" role="alert">{rootError || data.error} <span>Reads do not start models or regenerate media.</span></div>}
        <div className="review-surface">{projectId && Review ? <Review {...reviewProps} /> : <ReviewFallback {...reviewProps} hasProject={!!projectId} />}</div>
        <footer className="review-footer"><span>{version ? `${version.kind} · ${version.version_id}` : "No published version selected"}</span><span>{version ? "New publications do not move this view" : "Git-backed sources · Immutable publications"}</span></footer>
      </main>
      <aside className="right-panel" aria-label="Project assistant"><nav className="panel-tabs" aria-label="Assistant tabs"><button aria-selected={rightTab === "Copilot"} onClick={() => setRightTab("Copilot")}><Sparkles size={13} />Copilot{data.snapshot?.pending_decisions.length ? <span className="tab-count">{data.snapshot.pending_decisions.length}</span> : null}</button><button aria-selected={rightTab === "Critic"} onClick={() => setRightTab("Critic")}>Critic</button></nav>
        <div className={rightTab === "Copilot" ? "assistant-content" : "assistant-content hidden"}>{projectId ? <ChatPanel api={api} projectId={projectId} snapshot={data.snapshot} requests={data.requests} events={data.events} jobs={data.jobs} selection={selection} prepareContext={prepareContext} composer={composer} onRefresh={data.refresh} /> : <Empty icon={<Sparkles size={24} />} title="Your project discussion" text="Activate a project to restore its conversation. No model request is sent on open." />}</div>
        {rightTab === "Critic" && <div className="critic-content">{projectId && Critic ? <Critic api={api} projectId={projectId} selection={selection} refreshRevision={data.revision} onQueue={onQueue} onSteer={canSteer ? onSteer : undefined} /> : <Empty icon={<Image size={24} />} title="Critic unavailable" text="Visual assessment is not installed. Existing outputs and ordinary project discussion remain available." />}</div>}
      </aside>
    </div>
  </div>;
}

function Empty({ icon, title, text }: { icon: React.ReactNode; title: string; text: string }) {
  return <div className="empty-panel">{icon}<h3>{title}</h3><p>{text}</p></div>;
}

function ReviewFallback({ version, projectId, hasProject, onContextChange }: ReviewSurfaceProps & { hasProject: boolean }) {
  const [mode, setMode] = useState<"3d_inspection" | "keyframes" | "animation">("keyframes");
  useEffect(() => {
    function keydown(event: KeyboardEvent) {
      if (event.target instanceof HTMLElement && (event.target.closest("input,textarea,select,button,[contenteditable=true]") || event.ctrlKey || event.altKey || event.metaKey)) return;
      const modes = { "1": "3d_inspection", "2": "keyframes", "3": "animation" } as const;
      if (event.key in modes) { event.preventDefault(); setMode(modes[event.key as keyof typeof modes]); }
    }
    window.addEventListener("keydown", keydown);
    return () => window.removeEventListener("keydown", keydown);
  }, []);
  useEffect(() => {
    const previous = readPrivate<TurnSelection>(`view.context.${projectId}`) ?? emptySelection();
    onContextChange({ ...previous, item_id: version?.item_id ?? null, version_id: version?.version_id ?? null, view_mode: mode });
  }, [mode, version, projectId, onContextChange]);
  return <div className="review-fallback"><nav className="review-modes" aria-label="Review mode">{([{ id: "3d_inspection", label: "3D Inspection", icon: Box }, { id: "keyframes", label: "Keyframes", icon: Image }, { id: "animation", label: "Animation", icon: Film }] as const).map(({ id, label, icon: Icon }) => <button key={id} aria-selected={mode === id} onClick={() => setMode(id)}><Icon size={14} />{label}</button>)}</nav><div className="viewer-empty" data-review-mode={mode}><div className="viewer-symbol"><Layers size={34} strokeWidth={1} /></div><h2>{!hasProject ? "Your next production starts here" : !version ? "Choose a published item" : "Review surface unavailable"}</h2><p>{!hasProject ? "Create or open a project on the workstation, then activate it to begin." : !version ? "Select a source version from Assets. Older publications stay pinned while new work arrives." : "This shell does not include the media review component. Your selected source version is retained."}</p><small>{mode === "3d_inspection" ? "3D Inspection" : mode === "keyframes" ? "Keyframes" : "Animation"} · No render starts when changing mode</small></div></div>;
}
