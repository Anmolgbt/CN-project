/** Build the live-feed WebSocket URL for a job from a page location (ws:// on http, wss:// on https). */
export function wsUrl(jobId: string, loc: { protocol: string; host: string }): string {
  const scheme = loc.protocol === "https:" ? "wss" : "ws";
  return `${scheme}://${loc.host}/ws/transfers/${encodeURIComponent(jobId)}`;
}
