import { describe, expect, it } from "vitest";
import { wsUrl } from "./wsUrl";

describe("wsUrl", () => {
  it("uses ws:// on http pages (regression: the colon was once missing, which crashed the Live page)", () => {
    expect(wsUrl("abc123", { protocol: "http:", host: "127.0.0.1:8000" })).toBe("ws://127.0.0.1:8000/ws/transfers/abc123");
  });
  it("uses wss:// on https pages", () => {
    expect(wsUrl("abc123", { protocol: "https:", host: "example.org" })).toBe("wss://example.org/ws/transfers/abc123");
  });
  it("produces a URL the WebSocket constructor accepts", () => {
    expect(() => new URL(wsUrl("x", { protocol: "http:", host: "localhost:5173" }))).not.toThrow();
  });
  it("escapes the job id", () => {
    expect(wsUrl("a/b", { protocol: "http:", host: "h" })).toContain("a%2Fb");
  });
});
