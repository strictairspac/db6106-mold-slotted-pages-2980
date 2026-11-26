import { request } from "./http.js";

export const api = {
  config: () => request("/workbench"),
  login: data => request("/auth/login", {method: "POST", data}),
  cases: () => request("/cases"),
  createCase: data => request("/cases", {method: "POST", data}),
  runs: () => request("/runs"),
  run: (id, signal) => request(`/runs/${id}`, {signal}),
  enqueue: (id, data) => request(`/cases/${id}/runs`, {method: "POST", data}),
  review: id => request(`/runs/${id}/review`),
  submitReview: (id, data) => request(`/runs/${id}/review`, {method: "POST", data}),
  export: id => request(`/runs/${id}/export`, {raw: true}),
  merges: () => request("/merges"),
  merge: (id, signal) => request(`/merges/${id}`, {signal}),
  createMerge: data => request("/merges", {method: "POST", data}),
  submitChoices: (id, data) => request(`/merges/${id}/choices`, {method: "PUT", data}),
  enqueueMerge: id => request(`/merges/${id}/runs`, {method: "POST"}),
  mergeRun: (id, runId, signal) => request(`/merges/${id}/runs/${runId}`, {signal}),
  exportMergeRun: (id, runId) => request(`/merges/${id}/runs/${runId}/export`, {raw: true}),
  handover: (id, data) => request(`/merges/${id}/handover`, {method: "POST", data}),
  audit: () => request("/audit", {signal: AbortSignal.timeout(5000)}),
};
