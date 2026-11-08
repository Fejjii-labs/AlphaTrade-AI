import { createHash } from "node:crypto";
import { createServer, type IncomingMessage, type Server } from "node:http";
import type { AddressInfo, Socket } from "node:net";

import { expect, test, type Page } from "@playwright/test";

import { buildSecurityHeaders, resolveApiOrigin, resolveWebSocketOrigin } from "../src/lib/security-headers";

// Regression for the voice "Connecting… GPT Live" -> "Voice connection failed"
// bug: production connect-src listed the API's https origin but not its wss
// counterpart, so the browser blocked the voice socket before any handshake.
// Real browser CSP enforcement, controlled local endpoints, no live services.

type SocketEndpoint = { server: Server; origin: string; handshakes: string[] };
const upgraded = new Set<Socket>();

/** Minimal RFC 6455 endpoint: completes the upgrade and records the path. */
async function socketEndpoint(): Promise<SocketEndpoint> {
  const handshakes: string[] = [];
  const server = createServer((_request, response) => response.writeHead(404).end());
  server.on("upgrade", (request: IncomingMessage, socket: Socket) => {
    upgraded.add(socket);
    socket.on("close", () => upgraded.delete(socket));
    handshakes.push(request.url ?? "");
    const accept = createHash("sha1")
      .update(`${request.headers["sec-websocket-key"]}258EAFA5-E914-47DA-95CA-C5AB0DC85B11`)
      .digest("base64");
    socket.write(
      "HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
      + `Sec-WebSocket-Accept: ${accept}\r\n\r\n`,
    );
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const { port } = server.address() as AddressInfo;
  return { server, origin: `http://127.0.0.1:${port}`, handshakes };
}

/** Serves a blank app page under the given Content-Security-Policy. */
async function appPage(csp: string): Promise<{ server: Server; url: string }> {
  const server = createServer((_request, response) => {
    response.writeHead(200, { "Content-Type": "text/html", "Content-Security-Policy": csp });
    response.end("<!doctype html><title>voice csp</title>");
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const { port } = server.address() as AddressInfo;
  return { server, url: `http://127.0.0.1:${port}/` };
}

function productionCsp(apiOrigin: string): string {
  return buildSecurityHeaders({ apiOrigin, dev: false }).find(
    (header) => header.key === "Content-Security-Policy",
  )!.value;
}

/** Opens a socket the way RealtimeVoiceController does and reports the outcome. */
async function openVoiceSocket(page: Page, apiBaseUrl: string) {
  return page.evaluate(
    (base) =>
      new Promise<{ outcome: string; violation: string | null }>((resolve) => {
        let violation: string | null = null;
        document.addEventListener("securitypolicyviolation", (event) => {
          violation = `${event.effectiveDirective} ${event.blockedURI}`;
        });
        const url = new URL("/agent/voice/live", base);
        url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
        const finish = (outcome: string) => setTimeout(() => resolve({ outcome, violation }), 50);
        const timer = setTimeout(() => finish("timeout"), 5_000);
        try {
          const socket = new WebSocket(url.toString());
          socket.onopen = () => { clearTimeout(timer); socket.close(); finish("open"); };
          socket.onerror = () => { clearTimeout(timer); finish("error"); };
        } catch (error) {
          clearTimeout(timer);
          finish(`throw:${(error as Error).name}`);
        }
      }),
    apiBaseUrl,
  );
}

const servers: Server[] = [];
let api: SocketEndpoint;
let unrelated: SocketEndpoint;

test.beforeEach(async () => {
  api = await socketEndpoint();
  unrelated = await socketEndpoint();
  servers.push(api.server, unrelated.server);
});

test.afterEach(async () => {
  upgraded.forEach((socket) => socket.destroy());
  await Promise.all(servers.splice(0).map((server) => {
    server.closeAllConnections();
    return new Promise((resolve) => server.close(resolve));
  }));
});

test("production CSP lets the voice socket reach the configured API origin", async ({ page }) => {
  const csp = productionCsp(resolveApiOrigin(api.origin));
  expect(csp).toContain(`connect-src 'self' ${api.origin} ${resolveWebSocketOrigin(api.origin)};`);
  const app = await appPage(csp);
  servers.push(app.server);
  await page.goto(app.url);

  const result = await openVoiceSocket(page, api.origin);

  expect(result).toEqual({ outcome: "open", violation: null });
  expect(api.handshakes).toEqual(["/agent/voice/live"]);
});

test("production CSP still blocks sockets to an unrelated origin", async ({ page }) => {
  const app = await appPage(productionCsp(resolveApiOrigin(api.origin)));
  servers.push(app.server);
  await page.goto(app.url);

  const result = await openVoiceSocket(page, unrelated.origin);

  expect(result.outcome).toBe("error");
  expect(result.violation).toBe(`connect-src ${resolveWebSocketOrigin(unrelated.origin)}/agent/voice/live`);
  expect(unrelated.handshakes).toEqual([]);
  expect(api.handshakes).toEqual([]);
});

test("pre-fix CSP (HTTP origin only) blocks the socket before any handshake", async ({ page, browserName }) => {
  test.skip(browserName !== "chromium", "Reproduces the Chromium behaviour seen on the deployed app.");
  const fixed = productionCsp(resolveApiOrigin(api.origin));
  const preFix = fixed.replace(` ${resolveWebSocketOrigin(api.origin)}`, "");
  expect(preFix).toContain(`connect-src 'self' ${api.origin};`);
  const app = await appPage(preFix);
  servers.push(app.server);
  await page.goto(app.url);

  const result = await openVoiceSocket(page, api.origin);

  expect(result.outcome).toBe("error");
  expect(result.violation).toBe(`connect-src ${resolveWebSocketOrigin(api.origin)}/agent/voice/live`);
  expect(api.handshakes).toEqual([]);
});
