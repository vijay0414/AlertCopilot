/**
 * api.js — All backend fetch calls in one place.
 * Base URL is localhost:8000 — update if deploying elsewhere.
 */

const BASE = "http://localhost:8000";

/**
 * Trigger a named scenario for a machine.
 * Returns the scored_alerts array from the backend.
 */
export async function triggerScenario(machineId, scenario) {
  const res = await fetch(`${BASE}/sensors/simulate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ machine_id: machineId, scenario }),
  });
  if (!res.ok) throw new Error(`simulate failed: ${res.status}`);
  return res.json();
}

/**
 * Get the full alert priority queue for a machine.
 */
export async function fetchQueue(machineId) {
  const res = await fetch(`${BASE}/alerts/${machineId}/queue`);
  if (!res.ok) throw new Error(`queue failed: ${res.status}`);
  return res.json();
}

/**
 * Get the active plain-language instruction for a machine.
 */
export async function fetchInstruction(machineId) {
  const res = await fetch(`${BASE}/alerts/${machineId}/instruction`);
  if (!res.ok) throw new Error(`instruction failed: ${res.status}`);
  return res.json();
}

/**
 * Get fleet-level aggregate summary stats.
 */
export async function fetchFleetSummary() {
  const res = await fetch(`${BASE}/fleet/summary`);
  if (!res.ok) throw new Error(`summary failed: ${res.status}`);
  return res.json();
}

/**
 * Check if the backend is reachable at all.
 */
export async function checkHealth() {
  const res = await fetch(`${BASE}/health`);
  if (!res.ok) throw new Error("unhealthy");
  return res.json();
}
