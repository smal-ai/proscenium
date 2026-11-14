// Offline component harness only. No product backend, model or native application.
import { createServer } from "vite";
import { fileURLToPath } from "node:url";

const server = await createServer({
  root: fileURLToPath(new URL("../..", import.meta.url)),
  configFile: false,
  server: { host: "127.0.0.1", port: 4176, strictPort: true },
  appType: "custom",
});
server.middlewares.use(async (request, response, next) => {
  if (request.url?.split("?")[0] !== "/") return next();
  response.setHeader("Content-Type", "text/html");
  response.end(await server.transformIndexHtml("/", '<!doctype html><html lang="en"><head><title>Synthetic W06 harness</title></head><body style="margin:0"><div id="root"></div><script type="module" src="/tests/browser/storyboards-harness.tsx"></script></body></html>'));
});
await server.listen();
