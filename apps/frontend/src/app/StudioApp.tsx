import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Box, Film, Image, Layers, PanelLeft, PanelRight, Maximize2 as Maximise2, Minimize2 as Minimise2, RefreshCw, Sparkles, X } from "lucide-react";
import { ApiClient, ApiError } from "../api";
import type { Artifact, ConfigurationView, ItemRecord, ProjectRecord, VersionRecord } from "../api";
import { currentRetainedPreview, RetainedReview, ReviewEmpty } from "../features/review/ReviewChrome";
import { useArtifactObservations } from "../features/readyArtifactIndex";
import { typingTarget } from "../features/review/media";
import { readDrafts } from "../features/review/outbox";
import type { ComposerSeedIntent, ContextPreparer, CriticSurfaceProps, ReviewSurfaceProps, StoryboardSurfaceProps, StudioComponents, TurnSelection } from "../api/studio";
import { emptySelection } from "../api/studio";
import { ProjectMenu } from "../features/projects/ProjectMenu";
import { CatalogueFallback } from "../features/projects/CatalogueFallback";
import { shortVersionLabel } from "../features/catalogue/catalogueHelpers";
import { ChatPanel } from "../features/chat/ChatPanel";
import { isIdleAutoReleaseReady } from "../features/chat/resume";
import { useAutoRelease } from "../features/chat/useAutoRelease";
import { useRequestComposer } from "../features/chat/useRequestComposer";
import { ConfigurationMenu } from "./ConfigurationMenu";
import { Pairing, restoreOwner } from "./Pairing";
import { allPages, useProjectData } from "./useProjectData";
import { describeError, id, readPrivate, writePrivate } from "./storage";
import { ASSISTANT_MAX_WIDTH, ASSISTANT_MIN_WIDTH, NAVIGATOR_MAX_WIDTH, NAVIGATOR_MIN_WIDTH, useStudioLayout } from "./useStudioLayout";
import { Splitter } from "./Splitter";
import "@fontsource/inter/latin-400.css";
import "@fontsource/inter/latin-500.css";
import "@fontsource/inter/latin-600.css";
import "@fontsource/jetbrains-mono/latin-400.css";
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
  return <ProjectStudio key={active?.project_id ?? "none"} api={api} projectId={active?.project_id ?? null} projects={projects} configuration={configuration} onConfigurationChange={setConfiguration} components={components} onRefreshProjects={refreshProjects} onUnauthorised={unauthorised} rootError={error} />;
}

function ProjectStudio({ api, projectId, projects, configuration, onConfigurationChange, components, onRefreshProjects, onUnauthorised, rootError }: {
  api: ApiClient; projectId: string | null; projects: ProjectRecord[]; configuration: ConfigurationView | null; onConfigurationChange: (view: ConfigurationView) => void; components: StudioComponents; onRefreshProjects: () => Promise<void>; onUnauthorised: () => void; rootError: string;
}) {
  const data = useProjectData(api, projectId, onUnauthorised);
  const { layout, setLayout } = useStudioLayout(projectId, data.snapshot, data.requests.length, data.items.length, data.jobs.length, data.hydrated);
  const chatFocus = layout.mode === "chat_focus";
  const body = useRef<HTMLDivElement>(null);
  const [bodyWidth, setBodyWidth] = useState(1920);
  const [seedIntent, setSeedIntent] = useState<ComposerSeedIntent | null>(null);
  useEffect(() => {
    const element = body.current;
    if (!element) return;
    const measure = () => setBodyWidth(element.getBoundingClientRect().width);
    measure(); const observer = new ResizeObserver(measure); observer.observe(element);
    return () => observer.disconnect();
  }, []);
  const navigatorMax = Math.max(NAVIGATOR_MIN_WIDTH, Math.min(NAVIGATOR_MAX_WIDTH, bodyWidth - (chatFocus ? 320 : 320 + (layout.assistantCollapsed ? 52 : ASSISTANT_MIN_WIDTH)) - 12));
  const navigatorWidth = layout.navigatorCollapsed ? 52 : Math.min(layout.navigatorWidth, navigatorMax);
  const assistantMax = Math.max(ASSISTANT_MIN_WIDTH, Math.min(ASSISTANT_MAX_WIDTH, bodyWidth - navigatorWidth - 320 - 12));
  const assistantWidth = layout.assistantCollapsed ? 52 : Math.min(layout.assistantWidth, assistantMax);
  const [leftTab, setLeftTab] = useState<"Shots" | "Outliner" | "Assets">("Assets");
  const [shotTarget, setShotTarget] = useState<StoryboardSurfaceProps["externalTarget"]>();
  function chooseNavigatorTab(tab: typeof leftTab) {
    setLeftTab(tab);
    if (tab !== "Shots") setShotTarget(undefined);
  }
  const [rightTab, setRightTab] = useState<"Copilot" | "Critic">("Copilot");
  const [reviewTarget, setReviewTarget] = useState<CriticSurfaceProps["externalTarget"]>();
  const onOpenReview = useCallback((reviewId: string) => {
    setRightTab("Critic");
    setLayout(previous => previous.assistantCollapsed ? { ...previous, assistantCollapsed: false } : previous);
    setReviewTarget({ reviewId, nonce: id() });
  }, [setLayout]);
  const [version, setVersion] = useState<VersionRecord | null>(() => projectId ? readPrivate(`view.version.${projectId}`) : null);
  const versionRef = useRef(version); versionRef.current = version;
  const [inspectedArtifact, setInspectedArtifact] = useState<Artifact | null>(null);
  const [reviewNavigationEpoch, setReviewNavigationEpoch] = useState(0);
  const reviewNavCounter = useRef(0);
  const isCurrentReviewNavigation = useCallback((epoch: number) => epoch === reviewNavCounter.current, []);
  const [keptDraftNotice, setKeptDraftNotice] = useState("");
  const [latestError, setLatestError] = useState("");
  const [loadingLatest, setLoadingLatest] = useState(false);
  const latestRequest = useRef<AbortController | null>(null);
  const firstOpenRef = useRef<boolean | null>(null);
  const firstOpenRequest = useRef<AbortController | null>(null);
  // Capture before the layout hook persists its defaults. ProjectStudio is keyed by project.
  if (firstOpenRef.current === null) {
    firstOpenRef.current = projectId !== null
      && readPrivate(`layout.v1.${projectId}`) === null
      && readPrivate(`view.version.${projectId}`) === null
      && readPrivate(`created.${projectId}`) !== true;
  }
  const cancelFirstOpen = useCallback(() => {
    firstOpenRef.current = false;
    firstOpenRequest.current?.abort();
  }, []);
  const beginReviewNavigation = useCallback(() => {
    const next = ++reviewNavCounter.current;
    setReviewNavigationEpoch(next);
    cancelFirstOpen();
    latestRequest.current?.abort();
    return next;
  }, [cancelFirstOpen]);
  useEffect(() => () => latestRequest.current?.abort(), []);
  const currentItem = version ? data.items.find(item => item.item_id === version.item_id) : undefined;
  const latestId = currentItem?.current_version_id ?? version?.version_id;
  const latestShort = latestId ? shortVersionLabel(latestId) : "";
  const inspectedArtifactSummary = inspectedArtifact ? data.retainedArtifacts.find(r => r.artifact.project_id === projectId && r.artifact.artifact_id === inspectedArtifact.artifact_id) ?? null : null;
  const onClearArtifact = useCallback(() => {
    beginReviewNavigation();
    setInspectedArtifact(null);
  }, [beginReviewNavigation]);
  const [selection, setSelection] = useState<TurnSelection>(() => projectId ? readPrivate<TurnSelection>(`view.context.${projectId}`) ?? emptySelection() : emptySelection());
  const contextPreparer = useRef<ContextPreparer | null>(null);
  const onContextPreparer = useCallback((prepare: ContextPreparer | null) => { contextPreparer.current = prepare; }, []);
  const selectionRef = useRef(selection); selectionRef.current = selection;
  const prepareContext = useCallback(async () => contextPreparer.current ? contextPreparer.current() : structuredClone(selectionRef.current), []);
  const refreshAfterSubmission = useCallback(() => { void data.refresh(); }, [data.refresh]);
  const composer = useRequestComposer(api, projectId ?? "none", refreshAfterSubmission);
  const autoRelease = useAutoRelease(projectId, data.snapshot, data.requests);
  const seedNewItem = useCallback((itemKind: "asset" | "scene" | "storyboard") => {
    setRightTab("Copilot");
    const hasVersion = !!versionRef.current;
    setLayout(previous => ({ ...previous, assistantCollapsed: false, mode: hasVersion ? previous.mode : "chat_focus" }));
    const templates = { asset: "Create an asset. Describe its purpose, appearance and preferred authoring or import approach.", scene: "Build a scene. Describe the setting, subjects and intended camera or motion.", storyboard: "Plan a storyboard. Describe the story, audience and shot intentions before choosing production steps." };
    setSeedIntent({ nonce: id(), target: { kind: "new_item", item_kind: itemKind, intent_id: id() }, templateText: templates[itemKind], focusChat: true });
  }, [setLayout]);
  const onCreateAsset = useCallback(() => seedNewItem("asset"), [seedNewItem]);
  function toggleChatFocus() {
    setLayout(previous => ({ ...previous, mode: previous.mode === "split" ? "chat_focus" : "split", assistantCollapsed: false }));
  }
  const onContextChange = useCallback((next: TurnSelection) => {
    const captured = structuredClone(next);
    setSelection(captured);
    if (projectId) writePrivate(`view.context.${projectId}`, captured);
  }, [projectId]);
  const board = data.items.find(item => item.kind === "storyboard" && (item.selected_version_id || item.current_version_id));
  const boardId = board?.item_id;
  const boardVersionId = board?.selected_version_id ?? board?.current_version_id;
  useEffect(() => {
    if (!projectId || !data.hydrated || !firstOpenRef.current) return;
    if (!boardId || !boardVersionId) { firstOpenRef.current = false; return; }
    const controller = new AbortController();
    firstOpenRequest.current = controller;
    void allPages(cursor => api.call("listItemVersions", {
      path: { project_id: projectId, item_id: boardId }, query: { cursor, limit: 100 }, signal: controller.signal,
    })).then(versions => {
      if (controller.signal.aborted || !firstOpenRef.current) return;
      firstOpenRef.current = false;
      const target = versions.find(record => record.version_id === boardVersionId && record.item_id === boardId
        && record.project_id === projectId && record.kind === "storyboard");
      if (!target) return;
      setLeftTab("Shots");
      setVersion(target);
      writePrivate(`view.version.${projectId}`, target);
      onContextChange({ ...emptySelection(), item_id: target.item_id, version_id: target.version_id, view_mode: "keyframes" });
    }).catch(error => {
      if (controller.signal.aborted) return;
      firstOpenRef.current = false;
      if (error instanceof ApiError && error.status === 401) onUnauthorised();
      else setLatestError(`Could not open the published storyboard: ${describeError(error)}`);
    }).finally(() => {
      if (firstOpenRequest.current === controller) firstOpenRequest.current = null;
    });
    return () => controller.abort();
  }, [api, projectId, data.hydrated, boardId, boardVersionId, onContextChange, onUnauthorised]);
  const resetPublicationStatus = useCallback(() => {
    latestRequest.current?.abort();
    latestRequest.current = null;
    setKeptDraftNotice("");
    setLatestError("");
    setLoadingLatest(false);
  }, []);
  const onSelect = useCallback((next: VersionRecord) => {
    beginReviewNavigation();
    const version = versionRef.current;
    resetPublicationStatus();
    setInspectedArtifact(null);
    setLayout(previous => previous.mode === "chat_focus" ? { ...previous, mode: "split" } : previous);
    setVersion(next);
    writePrivate(`view.version.${projectId}`, next);
    if (version && next.project_id === version.project_id && next.item_id === version.item_id && next.version_id === version.version_id) return;
    onContextChange({ ...emptySelection(), item_id: next.item_id, version_id: next.version_id, view_mode: "keyframes" });
  }, [beginReviewNavigation, resetPublicationStatus, setLayout, projectId, onContextChange]);
  async function viewLatestVersion(item: ItemRecord, previousVersion: VersionRecord, targetVersionId: string) {
    if (!projectId || latestRequest.current) return;
    const startEpoch = beginReviewNavigation();
    const controller = new AbortController();
    latestRequest.current = controller;
    setLatestError("");
    setLoadingLatest(true);
    const isCurrent = () => !controller.signal.aborted && isCurrentReviewNavigation(startEpoch);
    try {
      let drafts: Awaited<ReturnType<typeof readDrafts>>;
      try {
        drafts = await readDrafts(projectId);
      } catch (err) {
        if (isCurrent()) setLatestError(`Could not verify browser drafts before switching versions: ${describeError(err)}`);
        return;
      }
      if (!isCurrent()) return;
      const hasUnsavedDraft = drafts.some(d => d.state !== "saved" && d.annotation.target.version_id === previousVersion.version_id);
      const versions = await allPages(cursor => api.call("listItemVersions", {
        path: { project_id: projectId, item_id: item.item_id }, query: { cursor, limit: 100 }, signal: controller.signal,
      }));
      if (!isCurrent()) return;
      const next = versions.find(record => record.version_id === targetVersionId);
      if (!next) throw new Error("The latest publication was not returned. Refresh project metadata and try again.");
      onSelect(next);
      if (hasUnsavedDraft) setKeptDraftNotice(`Draft kept on ${shortVersionLabel(previousVersion.version_id)}`);
    } catch (e) {
      if (isCurrent()) setLatestError(describeError(e));
    } finally {
      if (latestRequest.current === controller) {
        latestRequest.current = null;
        setLoadingLatest(false);
      }
    }
  }
  const onNavigate = useCallback((next: VersionRecord, context: TurnSelection) => {
    beginReviewNavigation();
    resetPublicationStatus();
    setInspectedArtifact(null);
    setLayout(previous => previous.mode === "chat_focus" ? { ...previous, mode: "split" } : previous);
    setVersion(next);
    writePrivate(`view.version.${projectId}`, next);
    onContextChange(context);
  }, [beginReviewNavigation, resetPublicationStatus, setLayout, projectId, onContextChange]);
  const onOpenShot = useCallback((board: VersionRecord, shotId: string) => {
    onSelect(board);
    setShotTarget({ versionId: board.version_id, shotId, nonce: id() });
    setLeftTab("Shots");
  }, [onSelect]);
  const onInspectArtifact = useCallback((artifact: Artifact) => {
    beginReviewNavigation();
    resetPublicationStatus();
    setInspectedArtifact(artifact);
    setLayout(previous => previous.mode === "chat_focus" ? { ...previous, mode: "split" } : previous);
    if (!versionRef.current) onContextChange({ ...emptySelection(), request_target: { kind: "project" }, attachments: artifact.state === "ready" && artifact.kind === "image" ? [{ artifact_id: artifact.artifact_id, role: "reference" }] : [] });
  }, [beginReviewNavigation, resetPublicationStatus, setLayout, onContextChange]);
  const requestInputs = { composer, autoRelease, snapshot: data.snapshot, requests: data.requests, events: data.events };
  const requestInputsRef = useRef(requestInputs); requestInputsRef.current = requestInputs;
  const onQueue = useCallback(async (text: string, context: TurnSelection) => {
    const { composer, autoRelease, snapshot, requests, events } = requestInputsRef.current;
    setRightTab("Copilot");
    const requestId = id();
    try {
      if (!projectId || composer.busy || composer.pending) throw new Error("A Queue submission is unavailable. Inspect Copilot before retrying.");
      if (isIdleAutoReleaseReady(snapshot, requests, events)) autoRelease.start(requestId);
      const submitted = await composer.submit(text, context, "queue", null, snapshot?.settings.default_profile_id ?? null, requestId);
      autoRelease.settle(requestId, submitted);
      if (!submitted) throw new Error("Submission is not confirmed. Inspect the retained request in Copilot before retrying.");
    } catch (error) {
      autoRelease.settle(requestId, null);
      throw error;
    }
  }, [projectId]);
  const activeWork = data.snapshot?.active_work;
  const unsettledStop = !!composer.pendingStop || data.requests.some(request => request.submission.type === "stop" && request.submission.target_work_id === activeWork?.work_id && ["accepted", "dispatching", "acknowledged", "stop_requested", "outcome_uncertain"].includes(request.receipt.state));
  const canSteer = !composer.stopping && !unsettledStop && (data.snapshot?.execution ? data.snapshot.execution.can_steer_work_id === activeWork?.work_id : (!!activeWork && ["running", "awaiting_user"].includes(activeWork.state) && data.snapshot?.project.state === "active"));
  const onSteer = useCallback(async (text: string, context: TurnSelection) => {
    const { composer, snapshot } = requestInputsRef.current;
    setRightTab("Copilot");
    if (!projectId || !canSteer || !await composer.submit(text, context, "steer", activeWork!.work_id, snapshot?.settings.default_profile_id ?? null)) {
      throw new Error("Steer is not confirmed. Inspect the retained request in Copilot before retrying.");
    }
  }, [projectId, canSteer, activeWork?.work_id]);
  const activeVersionRevision = data.baseRevision + (version && Object.hasOwn(data.versionRevisions, version.version_id) ? data.versionRevisions[version.version_id] : 0);
  const reviewProps = useMemo<ReviewSurfaceProps>(() => ({ visible: !chatFocus, api, projectId: projectId ?? "", version, inspectedArtifact, inspectedArtifactSummary, onClearArtifact, selection, jobs: data.jobs, items: data.items, retainedArtifacts: data.retainedArtifacts, refreshRevision: activeVersionRevision, annotationRevision: data.baseRevision, onContextChange, onContextPreparer, onNavigate, onQueue }),
    [chatFocus, api, projectId, version, inspectedArtifact, inspectedArtifactSummary, onClearArtifact, selection, data.jobs, data.items, data.retainedArtifacts, activeVersionRevision, data.baseRevision, onContextChange, onContextPreparer, onNavigate, onQueue]);
  const Review = components.review;
  const Outliner = components.outliner;
  const Catalogue = components.catalogue ?? CatalogueFallback;
  const catalogueIdentity = useMemo(() => ({ projectName: projects.find(project => project.project_id === projectId)?.name }), [projects, projectId]);
  const Storyboards = components.storyboards;
  const Critic = components.critic;
  const AssistantStatus = components.assistantStatus;
  const pendingReviewCount = useMemo(() => data.reviews.filter(ticket => ticket.awaiting_human === true).length, [data.reviews]);
  const uncertain = activeWork?.state === "outcome_uncertain" || (!!composer.pendingStop && !composer.stopping) || data.requests.some(request => request.receipt.state === "outcome_uncertain");
  const stopping = composer.stopping || unsettledStop || activeWork?.state === "stop_requested" || data.requests.some(request => request.receipt.state === "stop_requested");
  const fallbackStatus = !data.hydrated ? "Loading" : uncertain ? "Needs recovery" : stopping ? "Stopping" : composer.busy ? "Sending" : data.snapshot?.execution?.fenced || data.snapshot?.project.state === "awaiting_resume" ? `Paused${data.snapshot?.execution?.hold_reason ? `: ${data.snapshot.execution.hold_reason}` : ""}` : data.snapshot?.pending_decisions.length || activeWork?.state === "awaiting_user" ? "Needs your input" : activeWork?.state === "running" ? "Working" : "Ready";
  const renderAssistantStatus = (compact: boolean) => AssistantStatus ? <AssistantStatus jobs={data.jobs} snapshot={data.snapshot} requests={data.requests} events={data.events} connection={data.connection} hydrated={data.hydrated} submitting={composer.busy} stopping={composer.stopping} autoReleasingRequestId={autoRelease.requestId} compact={compact} /> : <span className="assistant-status" role="status">{fallbackStatus}{data.hydrated && data.connection !== "Connected" ? " · Last known" : ""}</span>;
  return <div className="studio-shell" onPointerDownCapture={cancelFirstOpen} onClickCapture={cancelFirstOpen}>
    <header className="studio-header"><div className="brand"><span className="brand-mark">P</span><strong>PROSCENIUM</strong></div><div className="header-divider" /><ProjectMenu api={api} projects={projects} activeId={projectId} onRefresh={onRefreshProjects} />
      <code className="workspace-path" title={data.snapshot?.project.path}>{data.snapshot?.project.path ?? "No active workstation folder"}</code>
      <div className="header-spacer" /><span className={data.connection === "Connected" ? "connection connected" : "connection"}><span />{data.connection}</span><button aria-label="Refresh project metadata" title="Refresh metadata without sending a prompt" onClick={() => { void onRefreshProjects(); void data.refresh(); }}><RefreshCw size={14} /></button><ConfigurationMenu api={api} configuration={configuration} snapshot={data.snapshot} onRefresh={data.refresh} onConfigurationChange={onConfigurationChange} />
    </header>
    <div ref={body} className={`studio-body${chatFocus ? " chat-focus" : ""}`} style={{ gridTemplateColumns: chatFocus ? `${navigatorWidth}px ${layout.navigatorCollapsed ? 0 : 6}px minmax(0,1fr)${layout.assistantCollapsed ? " 52px" : ""}` : `${navigatorWidth}px ${layout.navigatorCollapsed ? 0 : 6}px minmax(320px,1fr) ${layout.assistantCollapsed ? 0 : 6}px ${assistantWidth}px` }}>
      <aside className={`left-panel${layout.navigatorCollapsed ? " collapsed" : ""}`} aria-label="Navigator">
        {layout.navigatorCollapsed && <div className="panel-rail"><button aria-label="Expand Navigator" title="Expand Navigator" onClick={() => setLayout(previous => ({ ...previous, navigatorCollapsed: false }))}><PanelLeft size={17} /></button>{(["Shots", "Outliner", "Assets"] as const).map(tab => { const Icon = tab === "Shots" ? Film : tab === "Outliner" ? Layers : Box; return <button key={tab} aria-label={tab} title={tab} onClick={() => { chooseNavigatorTab(tab); setLayout(previous => ({ ...previous, navigatorCollapsed: false })); }}><Icon size={17} /></button>; })}</div>}
        <div className="panel-expanded" hidden={layout.navigatorCollapsed}><nav className="panel-tabs" aria-label="Navigator tabs">{(["Shots", "Outliner", "Assets"] as const).map(tab => <button key={tab} aria-selected={leftTab === tab} onClick={() => chooseNavigatorTab(tab)}>{tab}</button>)}<button className="panel-control" aria-label="Collapse Navigator" title="Collapse Navigator" onClick={() => setLayout(previous => ({ ...previous, navigatorCollapsed: true }))}><PanelLeft size={15} /></button></nav>
        <div className="left-scroll">{!projectId ? <Empty icon={<Layers size={24} />} title="No active project" text="Open or create a workstation folder using the project menu." /> : <>
          {Outliner && <Outliner reviewNavigationEpoch={reviewNavigationEpoch} onBeginNavigation={beginReviewNavigation} isCurrentReviewNavigation={isCurrentReviewNavigation} api={api} projectId={projectId} version={inspectedArtifact ? null : version} items={data.items} retainedArtifacts={data.retainedArtifacts} retainedArtifactReadOrder={data.retainedArtifactReadOrder} selectionRevision={data.selectionRevision} hydrated={data.hydrated} visible={!layout.navigatorCollapsed && leftTab === "Outliner"} topologyRevision={data.catalogueRevision} connection={data.connection} onOpenVersion={onSelect} onOpenShot={onOpenShot} onInspectArtifact={onInspectArtifact} />}
          {leftTab === "Assets" && <Catalogue api={api} projectId={projectId} {...catalogueIdentity} visible={!layout.navigatorCollapsed} items={data.items} retainedArtifacts={data.retainedArtifacts} selectedVersionId={version?.version_id ?? null} refreshRevision={data.catalogueMetadataRevision} onSelect={onSelect} onInspectArtifact={onInspectArtifact} onCreateAsset={onCreateAsset} />}
          {leftTab === "Outliner" && !Outliner && <Empty icon={<PanelLeft size={24} />} title="Outliner unavailable" text="Published relationship navigation is not installed." />}
          {leftTab === "Shots" && (Storyboards ? <Storyboards {...reviewProps} externalTarget={shotTarget} refreshRevision={data.catalogueMetadataRevision} /> : <Empty icon={<Film size={24} />} title="Shot browser unavailable" text="Storyboard navigation is not installed. Published items remain available under Assets." />)}
        </>}</div>
        </div>
      </aside>
      <div className="splitter-slot">{!layout.navigatorCollapsed && <Splitter label="Resize Navigator" value={navigatorWidth} min={220} max={navigatorMax} onChange={width => setLayout(previous => ({ ...previous, navigatorWidth: width }))} />}</div>
      <main className="review-panel" hidden={chatFocus} aria-label="Review workspace"><div className="review-heading"><div><span className="eyebrow">Review workspace</span><h1>{inspectedArtifact ? `Retained ${inspectedArtifact.purpose} ${inspectedArtifact.kind} · ${inspectedArtifact.artifact_id}` : version?.name ?? "Start with a project. Make something worth reviewing."}</h1></div>
        {inspectedArtifact ? <div className="source-identity"><span>Retained media · Not a catalogue version</span></div> : version && <div className="review-version-status">
          <span className={latestId === version.version_id ? "version-status-pill" : "version-status-pill outdated"} title={`${version.version_id} · Git ${version.source_commit.slice(0, 10)}`}>
            Viewing {shortVersionLabel(version.version_id)}{latestId !== version.version_id && ` · ${latestShort} available`}
          </span>
          {currentItem && latestId && latestId !== version.version_id && <button className="view-latest-version-btn" disabled={loadingLatest} onClick={() => void viewLatestVersion(currentItem, version, latestId)}>View {latestShort}</button>}
          {keptDraftNotice && <span className="draft-kept-pill"><span role="status">{keptDraftNotice}</span><button type="button" className="draft-kept-dismiss" aria-label="Dismiss kept draft notice" onClick={() => setKeptDraftNotice("")}><X size={12} aria-hidden="true" /></button></span>}
        </div>}
      </div>
        {(rootError || data.error) && <div className="metadata-error" role="alert">{rootError || data.error} <span>Reads do not start models or regenerate media.</span></div>}
        {latestError && <div className="metadata-error" role="alert">{latestError}</div>}
        <div className="review-surface">{projectId && Review ? <Review {...reviewProps} /> : <ReviewFallback {...reviewProps} hasProject={!!projectId} />}</div>
      </main>
      {!chatFocus && <div className="splitter-slot">{!layout.assistantCollapsed && <Splitter label="Resize Assistant" direction={-1} value={assistantWidth} min={320} max={assistantMax} onChange={width => setLayout(previous => ({ ...previous, assistantWidth: width }))} />}</div>}
      <aside className={`right-panel${layout.assistantCollapsed ? " collapsed" : ""}`} style={chatFocus && layout.assistantCollapsed ? { gridColumn: 4 } : undefined} aria-label="Assistant panel">
        {layout.assistantCollapsed && <div className="panel-rail"><button aria-label="Expand Assistant" title="Expand Assistant" onClick={() => setLayout(previous => ({ ...previous, assistantCollapsed: false }))}><PanelRight size={17} /></button><button aria-label={chatFocus ? "Restore split view" : "Chat focus"} title={chatFocus ? "Restore split view" : "Chat focus"} aria-pressed={chatFocus} onClick={toggleChatFocus}>{chatFocus ? <Minimise2 size={17} /> : <Maximise2 size={17} />}</button><div className="panel-rail-status">{renderAssistantStatus(true)}</div></div>}
        <div className="panel-expanded" hidden={layout.assistantCollapsed}><header className="assistant-heading"><nav className="panel-tabs" aria-label="Assistant tabs"><button aria-selected={rightTab === "Copilot"} onClick={() => setRightTab("Copilot")}><Sparkles size={13} />Copilot{data.snapshot?.pending_decisions.length ? <span className="tab-count">{data.snapshot.pending_decisions.length}</span> : null}</button><button aria-selected={rightTab === "Critic"} data-pending-reviews={pendingReviewCount} title={pendingReviewCount ? `${pendingReviewCount} pending ${pendingReviewCount === 1 ? "review" : "reviews"}` : undefined} onClick={() => setRightTab("Critic")}>Critic{pendingReviewCount > 0 ? <span className="tab-count" aria-hidden="true">{pendingReviewCount}</span> : null}</button></nav><button aria-label={chatFocus ? "Restore split view" : "Chat focus"} title={chatFocus ? "Restore split view" : "Chat focus"} aria-pressed={chatFocus} onClick={toggleChatFocus}>{chatFocus ? <Minimise2 size={15} /> : <Maximise2 size={15} />}</button><button aria-label="Collapse Assistant" title="Collapse Assistant" onClick={() => setLayout(previous => ({ ...previous, assistantCollapsed: true }))}><PanelRight size={15} /></button></header>
        <div data-seed-nonce={seedIntent?.nonce} className={rightTab === "Copilot" ? "assistant-content" : "assistant-content hidden"}>{projectId ? <ChatPanel reviewNavigationEpoch={reviewNavigationEpoch} onBeginNavigation={beginReviewNavigation} isCurrentReviewNavigation={isCurrentReviewNavigation} visible={!layout.assistantCollapsed && rightTab === "Copilot"} api={api} projectId={projectId} snapshot={data.snapshot} requests={data.requests} events={data.events} publicationEvents={data.publicationEvents} jobs={data.jobs} selection={selection} prepareContext={prepareContext} composer={composer} autoRelease={autoRelease} onRefresh={data.refresh} items={data.items} retainedArtifacts={data.retainedArtifacts} retainedArtifactReadOrder={data.retainedArtifactReadOrder} reviews={data.reviews} onInspectArtifact={onInspectArtifact} onOpenVersion={onSelect} onOpenReview={Critic ? onOpenReview : undefined} seedIntent={seedIntent} hydrated={data.hydrated} connection={data.connection} showHeaderStatus={false} /> : <Empty icon={<Sparkles size={24} />} title="Your project discussion" text="Activate a project to restore its conversation. No model request is sent on open." />}</div>
        <div className={rightTab === "Critic" ? "critic-content" : "critic-content hidden"}>{projectId && Critic ? <Critic api={api} projectId={projectId} selection={selection} refreshRevision={data.baseRevision} reviews={data.reviews} externalTarget={reviewTarget} items={data.items} retainedArtifacts={data.retainedArtifacts} pendingDecisions={data.snapshot?.pending_decisions} onInspectArtifact={onInspectArtifact} onRefresh={data.refresh} onQueue={onQueue} onSteer={canSteer ? onSteer : undefined} /> : <Empty icon={<Image size={24} />} title="Critic unavailable" text="Visual assessment is not installed. Existing outputs and ordinary project discussion remain available." />}</div>
        {(rightTab === "Critic" || !projectId) && <footer className="assistant-footer">{renderAssistantStatus(false)}</footer>}
        </div>
      </aside>
    </div>
  </div>;
}

function Empty({ icon, title, text }: { icon: React.ReactNode; title: string; text: string }) {
  return <div className="empty-panel">{icon}<h3>{title}</h3><p>{text}</p></div>;
}

function ReviewFallback({ visible = true, version, projectId, hasProject, inspectedArtifact, inspectedArtifactSummary, retainedArtifacts, onClearArtifact, onContextChange, onContextPreparer }: ReviewSurfaceProps & { hasProject: boolean }) {
  const [mode, setMode] = useState<"3d_inspection" | "keyframes" | "animation">("keyframes");
  const pinnedContext = useRef(readPrivate<TurnSelection>(`view.context.${projectId}`) ?? emptySelection());
  const { observations } = useArtifactObservations(projectId, retainedArtifacts, inspectedArtifactSummary);
  const liveInspectedArtifact = currentRetainedPreview(projectId, inspectedArtifact, observations.observed);
  useEffect(() => {
    function keydown(event: KeyboardEvent) {
      if (!visible || inspectedArtifact) return;
      if (typingTarget(event.target) || event.ctrlKey || event.altKey || event.metaKey) return;
      const modes = { "1": "3d_inspection", "2": "keyframes", "3": "animation" } as const;
      if (event.key in modes) { event.preventDefault(); setMode(modes[event.key as keyof typeof modes]); }
    }
    window.addEventListener("keydown", keydown);
    return () => window.removeEventListener("keydown", keydown);
  }, [visible, inspectedArtifact]);
  useEffect(() => {
    if (inspectedArtifact) {
      const context: TurnSelection = { ...emptySelection(), request_target: { kind: "project" }, attachments: liveInspectedArtifact?.state === "ready" && liveInspectedArtifact.kind === "image" ? [{ artifact_id: liveInspectedArtifact.artifact_id, role: "reference" }] : [] };
      onContextChange(context);
      onContextPreparer?.(async () => structuredClone(context));
      return () => onContextPreparer?.(null);
    }
    const previous = pinnedContext.current.version_id === (version?.version_id ?? null) ? pinnedContext.current : emptySelection();
    const context = { ...previous, item_id: version?.item_id ?? null, version_id: version?.version_id ?? null, view_mode: mode };
    pinnedContext.current = context;
    onContextChange(context);
  }, [mode, version, projectId, inspectedArtifact, liveInspectedArtifact, onContextChange, onContextPreparer]);
  if (inspectedArtifact) return <RetainedReview visible={visible} inspectedArtifact={liveInspectedArtifact} {...{projectId, version, inspectedArtifactSummary, onClearArtifact}}/>;
  return <ReviewEmpty {...{mode, hasProject, version}} onChange={setMode}/>;
}
