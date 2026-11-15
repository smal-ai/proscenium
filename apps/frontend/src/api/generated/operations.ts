// Generated from OpenAPI by scripts/generate_contracts.py v1. Do not edit.
import type * as C from "./contracts";
export interface Operations {
  pairOwnerSession: { body: C.OwnerBootstrap; response: C.OwnerSession; path: Record<string, never>; query: Record<string, never> };
  getConfigurationView: { body: never; response: C.ConfigurationView; path: Record<string, never>; query: Record<string, never> };
  getConfigurationStatus: { body: never; response: C.ConfigurationStatus; path: Record<string, never>; query: Record<string, never> };
  upsertAuthoringProfile: { body: C.AuthoringProfileUpsert; response: C.ConfigurationView; path: { profile_id: string }; query: Record<string, never> };
  deleteAuthoringProfile: { body: C.AuthoringProfileDelete; response: C.ConfigurationView; path: { profile_id: string }; query: Record<string, never> };
  listProjects: { body: never; response: C.ProjectPage; path: Record<string, never>; query: { cursor?: string; limit?: number } };
  openProject: { body: C.ProjectOpen; response: C.ProjectRecord; path: Record<string, never>; query: Record<string, never> };
  activateProject: { body: C.RevisionCommand; response: C.CommandReceipt; path: { project_id: string }; query: Record<string, never> };
  rebindProjectLocation: { body: C.ProjectRebind; response: C.CommandReceipt; path: { project_id: string }; query: Record<string, never> };
  getProjectSnapshot: { body: never; response: C.ProjectSnapshot; path: { project_id: string }; query: Record<string, never> };
  captureContext: { body: C.ContextSnapshot; response: C.ContextSnapshot; path: { project_id: string }; query: Record<string, never> };
  getContext: { body: never; response: C.ContextSnapshot; path: { project_id: string; context_id: string }; query: Record<string, never> };
  listRequests: { body: never; response: C.RequestPage; path: { project_id: string }; query: { cursor?: string; limit?: number } };
  submitRequest: { body: C.MessageSubmit; response: C.RequestReceipt; path: { project_id: string }; query: Record<string, never> };
  getRequest: { body: never; response: C.RequestRecord; path: { project_id: string; request_id: string }; query: Record<string, never> };
  cancelHeldRequest: { body: C.RevisionCommand; response: C.CommandReceipt; path: { project_id: string; request_id: string }; query: Record<string, never> };
  resumeProjectWork: { body: C.ResumeCommand; response: C.CommandReceipt; path: { project_id: string }; query: Record<string, never> };
  getResumeOperation: { body: never; response: C.ResumeOperationStatus; path: { project_id: string; operation_id: string }; query: Record<string, never> };
  updateProjectSettings: { body: C.ProjectSettings; response: C.CommandReceipt; path: { project_id: string }; query: Record<string, never> };
  listItems: { body: never; response: C.ItemPage; path: { project_id: string }; query: { cursor?: string; limit?: number; kind?: "asset" | "scene" | "storyboard" | "sequence" | "unity_result" } };
  listItemVersions: { body: never; response: C.VersionPage; path: { project_id: string; item_id: string }; query: { cursor?: string; limit?: number } };
  getVersionShotPlan: { body: never; response: C.ShotPlan; path: { project_id: string; version_id: string }; query: Record<string, never> };
  getVersionSequenceMap: { body: never; response: C.SequenceMap; path: { project_id: string; version_id: string }; query: Record<string, never> };
  getVersionProxyMapping: { body: never; response: C.ProxyMapping; path: { project_id: string; version_id: string }; query: Record<string, never> };
  listJobs: { body: never; response: C.JobPage; path: { project_id: string }; query: { cursor?: string; limit?: number } };
  getJob: { body: never; response: C.JobRecord; path: { project_id: string; job_id: string }; query: Record<string, never> };
  getFrameAvailability: { body: never; response: C.FrameAvailability; path: { project_id: string; job_id: string }; query: Record<string, never> };
  listJobAttempts: { body: never; response: C.JobAttemptPage; path: { project_id: string; job_id: string }; query: { cursor?: string; limit?: number } };
  listFrameReceipts: { body: never; response: C.FramePage; path: { project_id: string; job_id: string }; query: { cursor?: string; limit?: number; from_frame?: number; to_frame?: number } };
  listPreviewSegments: { body: never; response: C.SegmentPage; path: { project_id: string; job_id: string }; query: { cursor?: string; limit?: number } };
  requestJobCancellation: { body: C.RevisionCommand; response: C.CommandReceipt; path: { project_id: string; job_id: string }; query: Record<string, never> };
  uploadImage: { body: Blob; response: C.Artifact; path: { project_id: string; upload_id: string }; query: Record<string, never> };
  listRetainedArtifacts: { body: never; response: C.RetainedArtifactPage; path: { project_id: string }; query: { cursor?: string; limit?: number; kind?: "image" | "mask" | "proxy" | "proxy_map" | "video" | "frame_map" | "document" | "source_blob" | "log"; purpose?: "reference" | "concept" | "keyframe" | "draft_motion" | "final" | "inspection" | "metadata" | "mask" | "log" } };
  getArtifact: { body: never; response: C.Artifact; path: { project_id: string; artifact_id: string }; query: Record<string, never> };
  getArtifactContent: { body: never; response: ArrayBuffer; path: { project_id: string; artifact_id: string }; query: Record<string, never> };
  headArtifactContent: { body: never; response: ArrayBuffer; path: { project_id: string; artifact_id: string }; query: Record<string, never> };
  listAnnotations: { body: never; response: C.AnnotationPage; path: { project_id: string }; query: { cursor?: string; limit?: number; version_id?: string } };
  saveAnnotation: { body: C.AnnotationSave; response: C.CommandReceipt; path: { project_id: string }; query: Record<string, never> };
  listSelections: { body: never; response: C.SelectionPage; path: { project_id: string }; query: { cursor?: string; limit?: number } };
  saveSelection: { body: C.SelectionSave; response: C.CommandReceipt; path: { project_id: string }; query: Record<string, never> };
  listOwnerReviews: { body: never; response: C.ReviewPage; path: { project_id: string }; query: { cursor?: string; limit?: number } };
  requestOwnerReview: { body: C.ReviewRequest; response: C.ReviewTicket; path: { project_id: string }; query: Record<string, never> };
  listOwnerReviewFindings: { body: never; response: C.FindingPage; path: { project_id: string; review_id: string }; query: { cursor?: string; limit?: number } };
  rejectReviewFinding: { body: C.RevisionCommand; response: C.CommandReceipt; path: { project_id: string; review_id: string; finding_id: string }; query: Record<string, never> };
  answerOwnerDecision: { body: C.DecisionAnswer; response: C.CommandReceipt; path: { project_id: string; decision_id: string }; query: Record<string, never> };
  replayProjectEvents: { body: never; response: C.EventPage; path: { project_id: string }; query: { after?: number; limit?: number } };
  connectProjectEvents: { body: never; response: never; path: Record<string, never>; query: Record<string, never> };
  registerToolArtifact: { body: C.ArtifactRegister; response: C.Artifact; path: Record<string, never>; query: Record<string, never> };
  publishOutputs: { body: C.PublicationSubmit; response: C.PublicationReceipt; path: Record<string, never>; query: Record<string, never> };
  requestPreview: { body: C.PreviewRequest; response: C.JobRecord; path: Record<string, never>; query: Record<string, never> };
  submitGenerationJob: { body: C.GenerationSubmit; response: C.JobRecord; path: Record<string, never>; query: Record<string, never> };
  admitToolJob: { body: C.JobAdmission; response: C.JobRecord; path: Record<string, never>; query: Record<string, never> };
  retryToolJob: { body: C.JobRetry; response: C.JobRecord; path: { job_id: string }; query: Record<string, never> };
  updateJobState: { body: C.JobStateUpdate; response: C.JobRecord; path: { job_id: string }; query: Record<string, never> };
  requestToolReview: { body: C.ReviewRequest; response: C.ReviewTicket; path: Record<string, never>; query: Record<string, never> };
  completeToolReview: { body: C.ReviewComplete; response: C.ReviewTicket; path: { review_id: string }; query: Record<string, never> };
  registerFrameReceipt: { body: C.FrameReceipt; response: C.CommandReceipt; path: Record<string, never>; query: Record<string, never> };
  registerPreviewSegment: { body: C.PreviewSegment; response: C.CommandReceipt; path: Record<string, never>; query: Record<string, never> };
}
export const routes = {
  pairOwnerSession: { method: "POST", path: "/api/v1/owner/session", binary: false },
  getConfigurationView: { method: "GET", path: "/api/v1/configuration", binary: false },
  getConfigurationStatus: { method: "GET", path: "/api/v1/configuration/status", binary: false },
  upsertAuthoringProfile: { method: "PUT", path: "/api/v1/configuration/profiles/{profile_id}", binary: false },
  deleteAuthoringProfile: { method: "POST", path: "/api/v1/configuration/profiles/{profile_id}/delete", binary: false },
  listProjects: { method: "GET", path: "/api/v1/projects", binary: false },
  openProject: { method: "POST", path: "/api/v1/projects", binary: false },
  activateProject: { method: "POST", path: "/api/v1/projects/{project_id}/activate", binary: false },
  rebindProjectLocation: { method: "POST", path: "/api/v1/projects/{project_id}/rebind", binary: false },
  getProjectSnapshot: { method: "GET", path: "/api/v1/projects/{project_id}/snapshot", binary: false },
  captureContext: { method: "POST", path: "/api/v1/projects/{project_id}/contexts", binary: false },
  getContext: { method: "GET", path: "/api/v1/projects/{project_id}/contexts/{context_id}", binary: false },
  listRequests: { method: "GET", path: "/api/v1/projects/{project_id}/requests", binary: false },
  submitRequest: { method: "POST", path: "/api/v1/projects/{project_id}/requests", binary: false },
  getRequest: { method: "GET", path: "/api/v1/projects/{project_id}/requests/{request_id}", binary: false },
  cancelHeldRequest: { method: "POST", path: "/api/v1/projects/{project_id}/requests/{request_id}/cancel", binary: false },
  resumeProjectWork: { method: "POST", path: "/api/v1/projects/{project_id}/resume", binary: false },
  getResumeOperation: { method: "GET", path: "/api/v1/projects/{project_id}/resumes/{operation_id}", binary: false },
  updateProjectSettings: { method: "PUT", path: "/api/v1/projects/{project_id}/settings", binary: false },
  listItems: { method: "GET", path: "/api/v1/projects/{project_id}/items", binary: false },
  listItemVersions: { method: "GET", path: "/api/v1/projects/{project_id}/items/{item_id}/versions", binary: false },
  getVersionShotPlan: { method: "GET", path: "/api/v1/projects/{project_id}/versions/{version_id}/shot-plan", binary: false },
  getVersionSequenceMap: { method: "GET", path: "/api/v1/projects/{project_id}/versions/{version_id}/sequence-map", binary: false },
  getVersionProxyMapping: { method: "GET", path: "/api/v1/projects/{project_id}/versions/{version_id}/proxy-mapping", binary: false },
  listJobs: { method: "GET", path: "/api/v1/projects/{project_id}/jobs", binary: false },
  getJob: { method: "GET", path: "/api/v1/projects/{project_id}/jobs/{job_id}", binary: false },
  getFrameAvailability: { method: "GET", path: "/api/v1/projects/{project_id}/jobs/{job_id}/availability", binary: false },
  listJobAttempts: { method: "GET", path: "/api/v1/projects/{project_id}/jobs/{job_id}/attempts", binary: false },
  listFrameReceipts: { method: "GET", path: "/api/v1/projects/{project_id}/jobs/{job_id}/frames", binary: false },
  listPreviewSegments: { method: "GET", path: "/api/v1/projects/{project_id}/jobs/{job_id}/segments", binary: false },
  requestJobCancellation: { method: "POST", path: "/api/v1/projects/{project_id}/jobs/{job_id}/cancel", binary: false },
  uploadImage: { method: "PUT", path: "/api/v1/projects/{project_id}/uploads/{upload_id}", binary: false },
  listRetainedArtifacts: { method: "GET", path: "/api/v1/projects/{project_id}/artifacts", binary: false },
  getArtifact: { method: "GET", path: "/api/v1/projects/{project_id}/artifacts/{artifact_id}", binary: false },
  getArtifactContent: { method: "GET", path: "/api/v1/projects/{project_id}/artifacts/{artifact_id}/content", binary: true },
  headArtifactContent: { method: "HEAD", path: "/api/v1/projects/{project_id}/artifacts/{artifact_id}/content", binary: true },
  listAnnotations: { method: "GET", path: "/api/v1/projects/{project_id}/annotations", binary: false },
  saveAnnotation: { method: "POST", path: "/api/v1/projects/{project_id}/annotations", binary: false },
  listSelections: { method: "GET", path: "/api/v1/projects/{project_id}/selections", binary: false },
  saveSelection: { method: "POST", path: "/api/v1/projects/{project_id}/selections", binary: false },
  listOwnerReviews: { method: "GET", path: "/api/v1/projects/{project_id}/reviews", binary: false },
  requestOwnerReview: { method: "POST", path: "/api/v1/projects/{project_id}/reviews", binary: false },
  listOwnerReviewFindings: { method: "GET", path: "/api/v1/projects/{project_id}/reviews/{review_id}/findings", binary: false },
  rejectReviewFinding: { method: "POST", path: "/api/v1/projects/{project_id}/reviews/{review_id}/findings/{finding_id}/reject", binary: false },
  answerOwnerDecision: { method: "POST", path: "/api/v1/projects/{project_id}/decisions/{decision_id}/answer", binary: false },
  replayProjectEvents: { method: "GET", path: "/api/v1/projects/{project_id}/events", binary: false },
  connectProjectEvents: { method: "GET", path: "/api/v1/events", binary: false },
  registerToolArtifact: { method: "POST", path: "/internal/v1/artifacts", binary: false },
  publishOutputs: { method: "POST", path: "/internal/v1/publications", binary: false },
  requestPreview: { method: "POST", path: "/internal/v1/previews", binary: false },
  submitGenerationJob: { method: "POST", path: "/internal/v1/generation-jobs", binary: false },
  admitToolJob: { method: "POST", path: "/internal/v1/jobs", binary: false },
  retryToolJob: { method: "POST", path: "/internal/v1/jobs/{job_id}/retry", binary: false },
  updateJobState: { method: "POST", path: "/internal/v1/jobs/{job_id}/state", binary: false },
  requestToolReview: { method: "POST", path: "/internal/v1/reviews", binary: false },
  completeToolReview: { method: "POST", path: "/internal/v1/reviews/{review_id}/complete", binary: false },
  registerFrameReceipt: { method: "POST", path: "/internal/v1/frame-receipts", binary: false },
  registerPreviewSegment: { method: "POST", path: "/internal/v1/segments", binary: false },
} as const;
