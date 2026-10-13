// Generated from local FastAPI OpenAPI. Run npm run api:generate.
import type { paths } from "./types";
import { validatedFetch } from "../validated-fetch";
import * as validators from "./validators";

export function agentTurn(body: paths["/agent/turns"]["post"]["requestBody"]["content"]["application/json"], options?: { signal?: AbortSignal; headers?: Record<string, string> }) {
  return validatedFetch<paths["/agent/turns"]["post"]["responses"][200]["content"]["application/json"]>("/agent/turns", {
    method: "POST", auth: true, signal: options?.signal, headers: options?.headers,
    bodyValue: body, requestValidator: validators.agentTurnRequest,
    errorValidators: { 409: validators.agentTurnError409, 422: validators.agentTurnError422 },
    responseValidator: validators.agentTurnResponse,
  });
}

export function chatMessage(body: paths["/chat/message"]["post"]["requestBody"]["content"]["application/json"], options?: { signal?: AbortSignal; headers?: Record<string, string> }) {
  return validatedFetch<paths["/chat/message"]["post"]["responses"][200]["content"]["application/json"]>("/chat/message", {
    method: "POST", auth: true, signal: options?.signal, headers: options?.headers,
    bodyValue: body, requestValidator: validators.chatMessageRequest,
    errorValidators: { 409: validators.chatMessageError409, 422: validators.chatMessageError422 },
    responseValidator: validators.chatMessageResponse,
  });
}

export function captureRetry(body: paths["/agent/saved/retry"]["post"]["requestBody"]["content"]["application/json"], options?: { signal?: AbortSignal; headers?: Record<string, string> }) {
  return validatedFetch<paths["/agent/saved/retry"]["post"]["responses"][200]["content"]["application/json"]>("/agent/saved/retry", {
    method: "POST", auth: true, signal: options?.signal, headers: options?.headers,
    bodyValue: body, requestValidator: validators.captureRetryRequest,
    errorValidators: { 409: validators.captureRetryError409, 422: validators.captureRetryError422 },
    responseValidator: validators.captureRetryResponse,
  });
}

export function knowledgeDocuments(query?: paths["/knowledge/documents"]["get"]["parameters"]["query"], options?: { signal?: AbortSignal; headers?: Record<string, string> }) {
  return validatedFetch<paths["/knowledge/documents"]["get"]["responses"][200]["content"]["application/json"]>("/knowledge/documents", {
    method: "GET", auth: true, signal: options?.signal, headers: options?.headers,
    query,
    errorValidators: { 422: validators.knowledgeDocumentsError422 },
    responseValidator: validators.knowledgeDocumentsResponse,
  });
}

export function knowledgeChunks(query?: paths["/knowledge/chunks"]["get"]["parameters"]["query"], options?: { signal?: AbortSignal; headers?: Record<string, string> }) {
  return validatedFetch<paths["/knowledge/chunks"]["get"]["responses"][200]["content"]["application/json"]>("/knowledge/chunks", {
    method: "GET", auth: true, signal: options?.signal, headers: options?.headers,
    query,
    errorValidators: { 422: validators.knowledgeChunksError422 },
    responseValidator: validators.knowledgeChunksResponse,
  });
}

export function knowledgeIngest(body: paths["/knowledge/ingest"]["post"]["requestBody"]["content"]["application/json"], options?: { signal?: AbortSignal; headers?: Record<string, string> }) {
  return validatedFetch<paths["/knowledge/ingest"]["post"]["responses"][200]["content"]["application/json"]>("/knowledge/ingest", {
    method: "POST", auth: true, signal: options?.signal, headers: options?.headers,
    bodyValue: body, requestValidator: validators.knowledgeIngestRequest,
    errorValidators: { 422: validators.knowledgeIngestError422 },
    responseValidator: validators.knowledgeIngestResponse,
  });
}

export function knowledgeRetryIndexing(id: paths["/knowledge/documents/{document_id}/retry-indexing"]["post"]["parameters"]["path"]["document_id"], options?: { signal?: AbortSignal; headers?: Record<string, string> }) {
  return validatedFetch<paths["/knowledge/documents/{document_id}/retry-indexing"]["post"]["responses"][200]["content"]["application/json"]>(`/knowledge/documents/${encodeURIComponent(id)}/retry-indexing`, {
    method: "POST", auth: true, signal: options?.signal, headers: options?.headers,
    errorValidators: { 422: validators.knowledgeRetryIndexingError422 },
    responseValidator: validators.knowledgeRetryIndexingResponse,
  });
}

export function confirmProposal(id: paths["/agent/proposals/{proposal_id}/confirm"]["post"]["parameters"]["path"]["proposal_id"], body: paths["/agent/proposals/{proposal_id}/confirm"]["post"]["requestBody"]["content"]["application/json"], options?: { signal?: AbortSignal; headers?: Record<string, string> }) {
  return validatedFetch<paths["/agent/proposals/{proposal_id}/confirm"]["post"]["responses"][200]["content"]["application/json"]>(`/agent/proposals/${encodeURIComponent(id)}/confirm`, {
    method: "POST", auth: true, signal: options?.signal, headers: options?.headers,
    bodyValue: body, requestValidator: validators.confirmProposalRequest,
    errorValidators: { 422: validators.confirmProposalError422 },
    responseValidator: validators.confirmProposalResponse,
  });
}

export function rejectProposal(id: paths["/agent/proposals/{proposal_id}/reject"]["post"]["parameters"]["path"]["proposal_id"], body: paths["/agent/proposals/{proposal_id}/reject"]["post"]["requestBody"]["content"]["application/json"], options?: { signal?: AbortSignal; headers?: Record<string, string> }) {
  return validatedFetch<paths["/agent/proposals/{proposal_id}/reject"]["post"]["responses"][200]["content"]["application/json"]>(`/agent/proposals/${encodeURIComponent(id)}/reject`, {
    method: "POST", auth: true, signal: options?.signal, headers: options?.headers,
    bodyValue: body, requestValidator: validators.rejectProposalRequest,
    errorValidators: { 422: validators.rejectProposalError422 },
    responseValidator: validators.rejectProposalResponse,
  });
}

export function strategyPatch(id: paths["/strategies/{strategy_id}"]["patch"]["parameters"]["path"]["strategy_id"], body: paths["/strategies/{strategy_id}"]["patch"]["requestBody"]["content"]["application/json"], options?: { signal?: AbortSignal; headers?: Record<string, string> }) {
  return validatedFetch<paths["/strategies/{strategy_id}"]["patch"]["responses"][200]["content"]["application/json"]>(`/strategies/${encodeURIComponent(id)}`, {
    method: "PATCH", auth: true, signal: options?.signal, headers: options?.headers,
    bodyValue: body, requestValidator: validators.strategyPatchRequest,
    errorValidators: { 422: validators.strategyPatchError422 },
    responseValidator: validators.strategyPatchResponse,
  });
}

export function attention(options?: { signal?: AbortSignal; headers?: Record<string, string> }) {
  return validatedFetch<paths["/dashboard/attention"]["get"]["responses"][200]["content"]["application/json"]>("/dashboard/attention", {
    method: "GET", auth: true, signal: options?.signal, headers: options?.headers,
    responseValidator: validators.attentionResponse,
  });
}

export function dailyReview(query?: paths["/dashboard/daily-review"]["get"]["parameters"]["query"], options?: { signal?: AbortSignal; headers?: Record<string, string> }) {
  return validatedFetch<paths["/dashboard/daily-review"]["get"]["responses"][200]["content"]["application/json"]>("/dashboard/daily-review", {
    method: "GET", auth: true, signal: options?.signal, headers: options?.headers,
    query,
    errorValidators: { 422: validators.dailyReviewError422 },
    responseValidator: validators.dailyReviewResponse,
  });
}

export function blofinActivity(query?: paths["/exchange/blofin/activity"]["get"]["parameters"]["query"], options?: { signal?: AbortSignal; headers?: Record<string, string> }) {
  return validatedFetch<paths["/exchange/blofin/activity"]["get"]["responses"][200]["content"]["application/json"]>("/exchange/blofin/activity", {
    method: "GET", auth: true, signal: options?.signal, headers: options?.headers,
    query,
    errorValidators: { 422: validators.blofinActivityError422 },
    responseValidator: validators.blofinActivityResponse,
  });
}

export function experiments(query?: paths["/experiments"]["get"]["parameters"]["query"], options?: { signal?: AbortSignal; headers?: Record<string, string> }) {
  return validatedFetch<paths["/experiments"]["get"]["responses"][200]["content"]["application/json"]>("/experiments", {
    method: "GET", auth: true, signal: options?.signal, headers: options?.headers,
    query,
    errorValidators: { 422: validators.experimentsError422 },
    responseValidator: validators.experimentsResponse,
  });
}

export function experimentCreate(body: paths["/experiments"]["post"]["requestBody"]["content"]["application/json"], options?: { signal?: AbortSignal; headers?: Record<string, string> }) {
  return validatedFetch<paths["/experiments"]["post"]["responses"][201]["content"]["application/json"]>("/experiments", {
    method: "POST", auth: true, signal: options?.signal, headers: options?.headers,
    bodyValue: body, requestValidator: validators.experimentCreateRequest,
    errorValidators: { 422: validators.experimentCreateError422 },
    responseValidator: validators.experimentCreateResponse,
  });
}

export function experimentDetail(id: paths["/experiments/{experiment_id}"]["get"]["parameters"]["path"]["experiment_id"], options?: { signal?: AbortSignal; headers?: Record<string, string> }) {
  return validatedFetch<paths["/experiments/{experiment_id}"]["get"]["responses"][200]["content"]["application/json"]>(`/experiments/${encodeURIComponent(id)}`, {
    method: "GET", auth: true, signal: options?.signal, headers: options?.headers,
    errorValidators: { 422: validators.experimentDetailError422 },
    responseValidator: validators.experimentDetailResponse,
  });
}

export function experimentVersion(id: paths["/experiments/{experiment_id}/versions"]["post"]["parameters"]["path"]["experiment_id"], body: paths["/experiments/{experiment_id}/versions"]["post"]["requestBody"]["content"]["application/json"], options?: { signal?: AbortSignal; headers?: Record<string, string> }) {
  return validatedFetch<paths["/experiments/{experiment_id}/versions"]["post"]["responses"][201]["content"]["application/json"]>(`/experiments/${encodeURIComponent(id)}/versions`, {
    method: "POST", auth: true, signal: options?.signal, headers: options?.headers,
    bodyValue: body, requestValidator: validators.experimentVersionRequest,
    errorValidators: { 422: validators.experimentVersionError422 },
    responseValidator: validators.experimentVersionResponse,
  });
}

export function experimentTransition(experimentId: paths["/experiments/{experiment_id}/versions/{version_id}/transition"]["post"]["parameters"]["path"]["experiment_id"], versionId: paths["/experiments/{experiment_id}/versions/{version_id}/transition"]["post"]["parameters"]["path"]["version_id"], body: paths["/experiments/{experiment_id}/versions/{version_id}/transition"]["post"]["requestBody"]["content"]["application/json"], options?: { signal?: AbortSignal; headers?: Record<string, string> }) {
  return validatedFetch<paths["/experiments/{experiment_id}/versions/{version_id}/transition"]["post"]["responses"][200]["content"]["application/json"]>(`/experiments/${encodeURIComponent(experimentId)}/versions/${encodeURIComponent(versionId)}/transition`, {
    method: "POST", auth: true, signal: options?.signal, headers: options?.headers,
    bodyValue: body, requestValidator: validators.experimentTransitionRequest,
    errorValidators: { 422: validators.experimentTransitionError422 },
    responseValidator: validators.experimentTransitionResponse,
  });
}

export function experimentApprove(experimentId: paths["/experiments/{experiment_id}/versions/{version_id}/approve"]["post"]["parameters"]["path"]["experiment_id"], versionId: paths["/experiments/{experiment_id}/versions/{version_id}/approve"]["post"]["parameters"]["path"]["version_id"], body: paths["/experiments/{experiment_id}/versions/{version_id}/approve"]["post"]["requestBody"]["content"]["application/json"], options?: { signal?: AbortSignal; headers?: Record<string, string> }) {
  return validatedFetch<paths["/experiments/{experiment_id}/versions/{version_id}/approve"]["post"]["responses"][200]["content"]["application/json"]>(`/experiments/${encodeURIComponent(experimentId)}/versions/${encodeURIComponent(versionId)}/approve`, {
    method: "POST", auth: true, signal: options?.signal, headers: options?.headers,
    bodyValue: body, requestValidator: validators.experimentApproveRequest,
    errorValidators: { 422: validators.experimentApproveError422 },
    responseValidator: validators.experimentApproveResponse,
  });
}

export function experimentPromote(experimentId: paths["/experiments/{experiment_id}/versions/{version_id}/promote"]["post"]["parameters"]["path"]["experiment_id"], versionId: paths["/experiments/{experiment_id}/versions/{version_id}/promote"]["post"]["parameters"]["path"]["version_id"], body: paths["/experiments/{experiment_id}/versions/{version_id}/promote"]["post"]["requestBody"]["content"]["application/json"], options?: { signal?: AbortSignal; headers?: Record<string, string> }) {
  return validatedFetch<paths["/experiments/{experiment_id}/versions/{version_id}/promote"]["post"]["responses"][201]["content"]["application/json"]>(`/experiments/${encodeURIComponent(experimentId)}/versions/${encodeURIComponent(versionId)}/promote`, {
    method: "POST", auth: true, signal: options?.signal, headers: options?.headers,
    bodyValue: body, requestValidator: validators.experimentPromoteRequest,
    errorValidators: { 422: validators.experimentPromoteError422 },
    responseValidator: validators.experimentPromoteResponse,
  });
}

export function experimentNativeOutcome(experimentId: paths["/experiments/{experiment_id}/versions/{version_id}/outcomes/{source_record_id}"]["get"]["parameters"]["path"]["experiment_id"], versionId: paths["/experiments/{experiment_id}/versions/{version_id}/outcomes/{source_record_id}"]["get"]["parameters"]["path"]["version_id"], sourceRecordId: paths["/experiments/{experiment_id}/versions/{version_id}/outcomes/{source_record_id}"]["get"]["parameters"]["path"]["source_record_id"], query?: paths["/experiments/{experiment_id}/versions/{version_id}/outcomes/{source_record_id}"]["get"]["parameters"]["query"], options?: { signal?: AbortSignal; headers?: Record<string, string> }) {
  return validatedFetch<paths["/experiments/{experiment_id}/versions/{version_id}/outcomes/{source_record_id}"]["get"]["responses"][200]["content"]["application/json"]>(`/experiments/${encodeURIComponent(experimentId)}/versions/${encodeURIComponent(versionId)}/outcomes/${encodeURIComponent(sourceRecordId)}`, {
    method: "GET", auth: true, signal: options?.signal, headers: options?.headers,
    query,
    errorValidators: { 422: validators.experimentNativeOutcomeError422 },
    responseValidator: validators.experimentNativeOutcomeResponse,
  });
}

export function experimentNativePerformance(experimentId: paths["/experiments/{experiment_id}/versions/{version_id}/performance"]["get"]["parameters"]["path"]["experiment_id"], versionId: paths["/experiments/{experiment_id}/versions/{version_id}/performance"]["get"]["parameters"]["path"]["version_id"], query?: paths["/experiments/{experiment_id}/versions/{version_id}/performance"]["get"]["parameters"]["query"], options?: { signal?: AbortSignal; headers?: Record<string, string> }) {
  return validatedFetch<paths["/experiments/{experiment_id}/versions/{version_id}/performance"]["get"]["responses"][200]["content"]["application/json"]>(`/experiments/${encodeURIComponent(experimentId)}/versions/${encodeURIComponent(versionId)}/performance`, {
    method: "GET", auth: true, signal: options?.signal, headers: options?.headers,
    query,
    errorValidators: { 422: validators.experimentNativePerformanceError422 },
    responseValidator: validators.experimentNativePerformanceResponse,
  });
}

export function experimentSample(experimentId: paths["/experiments/{experiment_id}/versions/{version_id}/samples"]["post"]["parameters"]["path"]["experiment_id"], versionId: paths["/experiments/{experiment_id}/versions/{version_id}/samples"]["post"]["parameters"]["path"]["version_id"], body: paths["/experiments/{experiment_id}/versions/{version_id}/samples"]["post"]["requestBody"]["content"]["application/json"], options?: { signal?: AbortSignal; headers?: Record<string, string> }) {
  return validatedFetch<paths["/experiments/{experiment_id}/versions/{version_id}/samples"]["post"]["responses"][201]["content"]["application/json"]>(`/experiments/${encodeURIComponent(experimentId)}/versions/${encodeURIComponent(versionId)}/samples`, {
    method: "POST", auth: true, signal: options?.signal, headers: options?.headers,
    bodyValue: body, requestValidator: validators.experimentSampleRequest,
    errorValidators: { 422: validators.experimentSampleError422 },
    responseValidator: validators.experimentSampleResponse,
  });
}

export function trendpulseScreen(experimentId: paths["/experiments/{experiment_id}/versions/{version_id}/trendpulse-screenings"]["post"]["parameters"]["path"]["experiment_id"], versionId: paths["/experiments/{experiment_id}/versions/{version_id}/trendpulse-screenings"]["post"]["parameters"]["path"]["version_id"], body: paths["/experiments/{experiment_id}/versions/{version_id}/trendpulse-screenings"]["post"]["requestBody"]["content"]["application/json"], options?: { signal?: AbortSignal; headers?: Record<string, string> }) {
  return validatedFetch<paths["/experiments/{experiment_id}/versions/{version_id}/trendpulse-screenings"]["post"]["responses"][200]["content"]["application/json"]>(`/experiments/${encodeURIComponent(experimentId)}/versions/${encodeURIComponent(versionId)}/trendpulse-screenings`, {
    method: "POST", auth: true, signal: options?.signal, headers: options?.headers,
    bodyValue: body, requestValidator: validators.trendpulseScreenRequest,
    errorValidators: { 422: validators.trendpulseScreenError422 },
    responseValidator: validators.trendpulseScreenResponse,
  });
}

export function trendpulseScreenings(experimentId: paths["/experiments/{experiment_id}/versions/{version_id}/trendpulse-screenings"]["get"]["parameters"]["path"]["experiment_id"], versionId: paths["/experiments/{experiment_id}/versions/{version_id}/trendpulse-screenings"]["get"]["parameters"]["path"]["version_id"], query?: paths["/experiments/{experiment_id}/versions/{version_id}/trendpulse-screenings"]["get"]["parameters"]["query"], options?: { signal?: AbortSignal; headers?: Record<string, string> }) {
  return validatedFetch<paths["/experiments/{experiment_id}/versions/{version_id}/trendpulse-screenings"]["get"]["responses"][200]["content"]["application/json"]>(`/experiments/${encodeURIComponent(experimentId)}/versions/${encodeURIComponent(versionId)}/trendpulse-screenings`, {
    method: "GET", auth: true, signal: options?.signal, headers: options?.headers,
    query,
    errorValidators: { 422: validators.trendpulseScreeningsError422 },
    responseValidator: validators.trendpulseScreeningsResponse,
  });
}

export function trendpulseScreeningDetail(id: paths["/trendpulse-screenings/{record_id}"]["get"]["parameters"]["path"]["record_id"], options?: { signal?: AbortSignal; headers?: Record<string, string> }) {
  return validatedFetch<paths["/trendpulse-screenings/{record_id}"]["get"]["responses"][200]["content"]["application/json"]>(`/trendpulse-screenings/${encodeURIComponent(id)}`, {
    method: "GET", auth: true, signal: options?.signal, headers: options?.headers,
    errorValidators: { 422: validators.trendpulseScreeningDetailError422 },
    responseValidator: validators.trendpulseScreeningDetailResponse,
  });
}

export function trendpulseCandidate(id: paths["/trendpulse-screenings/{record_id}/candidate"]["post"]["parameters"]["path"]["record_id"], options?: { signal?: AbortSignal; headers?: Record<string, string> }) {
  return validatedFetch<paths["/trendpulse-screenings/{record_id}/candidate"]["post"]["responses"][200]["content"]["application/json"]>(`/trendpulse-screenings/${encodeURIComponent(id)}/candidate`, {
    method: "POST", auth: true, signal: options?.signal, headers: options?.headers,
    errorValidators: { 422: validators.trendpulseCandidateError422 },
    responseValidator: validators.trendpulseCandidateResponse,
  });
}

export function experimentResearchTick(query?: paths["/experiments/research/tick"]["post"]["parameters"]["query"], options?: { signal?: AbortSignal; headers?: Record<string, string> }) {
  return validatedFetch<paths["/experiments/research/tick"]["post"]["responses"][200]["content"]["application/json"]>("/experiments/research/tick", {
    method: "POST", auth: true, signal: options?.signal, headers: options?.headers,
    query,
    errorValidators: { 422: validators.experimentResearchTickError422 },
    responseValidator: validators.experimentResearchTickResponse,
  });
}

export function strategyVersions(id: paths["/strategies/{strategy_id}/versions"]["get"]["parameters"]["path"]["strategy_id"], query?: paths["/strategies/{strategy_id}/versions"]["get"]["parameters"]["query"], options?: { signal?: AbortSignal; headers?: Record<string, string> }) {
  return validatedFetch<paths["/strategies/{strategy_id}/versions"]["get"]["responses"][200]["content"]["application/json"]>(`/strategies/${encodeURIComponent(id)}/versions`, {
    method: "GET", auth: true, signal: options?.signal, headers: options?.headers,
    query,
    errorValidators: { 422: validators.strategyVersionsError422 },
    responseValidator: validators.strategyVersionsResponse,
  });
}

export function paperAccount(options?: { signal?: AbortSignal; headers?: Record<string, string> }) {
  return validatedFetch<paths["/execution/accounts/paper"]["get"]["responses"][200]["content"]["application/json"]>("/execution/accounts/paper", {
    method: "GET", auth: true, signal: options?.signal, headers: options?.headers,
    responseValidator: validators.paperAccountResponse,
  });
}

export function watcherAssignments(options?: { signal?: AbortSignal; headers?: Record<string, string> }) {
  return validatedFetch<paths["/watcher/assignments"]["get"]["responses"][200]["content"]["application/json"]>("/watcher/assignments", {
    method: "GET", auth: true, signal: options?.signal, headers: options?.headers,
    responseValidator: validators.watcherAssignmentsResponse,
  });
}

export function watcherAssignmentsReplace(body: paths["/watcher/assignments"]["put"]["requestBody"]["content"]["application/json"], options?: { signal?: AbortSignal; headers?: Record<string, string> }) {
  return validatedFetch<paths["/watcher/assignments"]["put"]["responses"][200]["content"]["application/json"]>("/watcher/assignments", {
    method: "PUT", auth: true, signal: options?.signal, headers: options?.headers,
    bodyValue: body, requestValidator: validators.watcherAssignmentsReplaceRequest,
    errorValidators: { 422: validators.watcherAssignmentsReplaceError422 },
    responseValidator: validators.watcherAssignmentsReplaceResponse,
  });
}

export function agentVoiceConfig(options?: { signal?: AbortSignal; headers?: Record<string, string> }) {
  return validatedFetch<paths["/agent/voice/config"]["get"]["responses"][200]["content"]["application/json"]>("/agent/voice/config", {
    method: "GET", auth: true, signal: options?.signal, headers: options?.headers,
    responseValidator: validators.agentVoiceConfigResponse,
  });
}
