// Drives the real UI (jsdom) against a RUNNING API on 127.0.0.1:8000:  python scripts/run_dashboard.py  then  npm run test:live
// @vitest-environment jsdom
// @vitest-environment-options {"url": "http://127.0.0.1:8000/"}
import { describe, it, expect, beforeAll } from "vitest";
import { render, screen, waitFor, fireEvent, within } from "@testing-library/react";
import App from "../src/App";

const BASE = "http://127.0.0.1:8000";
beforeAll(() => {
  const realFetch = globalThis.fetch;
  globalThis.fetch = ((u: any, i?: any) => realFetch(typeof u === "string" && u.startsWith("/") ? BASE + u : u, i)) as any;
  (globalThis as any).ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
  const ctx = new Proxy({}, { get: (_t, k) => (k === "canvas" ? {} : () => {}), set: () => true });
  HTMLCanvasElement.prototype.getContext = (() => ctx) as any;
  Object.defineProperty(HTMLElement.prototype, "clientWidth", { configurable: true, get: () => 800 });
  localStorage.clear();
  window.scrollTo = (() => {}) as any;           // jsdom does not implement scrollTo
});
const errors: string[] = [];
console.error = (...a: any[]) => { errors.push(a.join(" ")); };
const nav = (n: string) => fireEvent.click(within(screen.getByRole("navigation")).getByRole("button", { name: n }));
const T = { timeout: 15000 };

describe("user flows through the real UI", () => {
  it("upload -> start -> live -> integrity -> download link", async () => {
    render(<App />);
    await waitFor(() => expect(screen.getByText(/Receiver listening/)).toBeTruthy(), T);
    nav("New transfer");
    const bytes = new Uint8Array(3_000_000).map((_, i) => (i * 31) & 255);
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    const file = new File([bytes], "flow.bin");
    Object.defineProperty(input, "files", { value: [file], configurable: true });
    fireEvent.change(input);
    await waitFor(() => expect(screen.getAllByText("flow.bin").length).toBeGreaterThan(0), T);
    await waitFor(() => expect(document.body.textContent).toMatch(/SHA-256/), T);   // shown once the new file is selected
    const start = screen.getByRole("button", { name: "Start transfer" }) as HTMLButtonElement;
    await waitFor(() => expect(start.disabled).toBe(false), T);
    fireEvent.click(start);
    await waitFor(() => expect(screen.getByText("Sockets over time")).toBeTruthy(), T);
    await waitFor(() => expect(screen.getAllByText(/Complete, integrity verified/).length).toBeGreaterThan(0), T);
    nav("Integrity check");
    await waitFor(() => expect(screen.getByText("The two values are identical.")).toBeTruthy(), T);
    const dl = screen.getByText("Download verified file") as HTMLAnchorElement;
    const res = await fetch(dl.getAttribute("href")!);
    expect((await res.arrayBuffer()).byteLength).toBe(3_000_000);
  }, 60000);

  it("bad input shows a readable error and does not start", async () => {
    nav("New transfer");
    await waitFor(() => expect(screen.getAllByRole("radio").length).toBeGreaterThan(0), T);
    fireEvent.click(screen.getAllByRole("radio")[0]);
    fireEvent.change(screen.getByLabelText("Receiver address"), { target: { value: "8.8.8.8" } });
    const start = screen.getByRole("button", { name: "Start transfer" }) as HTMLButtonElement;
    await waitFor(() => expect(start.disabled).toBe(false), T);
    fireEvent.click(start);
    await waitFor(() => expect(screen.getByRole("alert").textContent).toMatch(/Only localhost receivers/), T);
  }, 30000);

  it("benchmark page runs 1/2/4/8 and shows verified rows", async () => {
    nav("Benchmark");
    fireEvent.change(screen.getByLabelText("Size in MB"), { target: { value: "4" } });
    fireEvent.change(screen.getByLabelText("Chunk size"), { target: { value: "262144" } });
    fireEvent.click(screen.getByRole("button", { name: "Run benchmark" }));
    await waitFor(() => expect(screen.getByText(/Versus 1 socket/)).toBeTruthy(), { timeout: 40000 });
    await waitFor(() => expect(document.querySelectorAll("tbody tr").length).toBeGreaterThanOrEqual(4), { timeout: 40000 });
    await waitFor(() => expect(screen.getByRole("button", { name: "Run benchmark" })).toBeTruthy(), { timeout: 40000 });
    expect(document.body.textContent).toContain("verified");
    expect(document.querySelector("table")!.textContent).not.toMatch(/failed/i);
  }, 90000);

  it("no React/runtime errors were logged", () => {
    const real = errors.filter((e) => !/not wrapped in act|ResizeObserver|width\(0\)|height\(0\)/.test(e));
    console.log("errors:", real);
    expect(real.length).toBe(0);
  });
});
