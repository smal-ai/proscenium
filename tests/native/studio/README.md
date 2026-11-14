# Bounded real M01 driver

These are operator-run native acceptance helpers, not unit-test doubles. They use
the actual compiled application, Python SDK/cloud model, official MCP client,
installed Blender and isolated Chromium. Never run bootstrap/send/native startup
without an explicit renewed allocation. The retained successful run is described
in `docs/implementation/m01.md`.

- `bootstrap.py` creates a fresh disk-backed root, private configuration, owned
  production server process group and isolated Chromium profile. Pairing output
  stays in the private log. No cloud request is sent by bootstrap.
- `browser.mjs ROOT setup` consumes the private pairing URL in the browser and
  creates/activates the owned project through the actual UI.
- Start the installed add-on only in an explicitly owned factory Blender using
  `python -m tools.blender start --isolated --port 9877 --virtual-display --project
  ROOT/project`. Never use or replace an unrelated listener on 9876.
- Put labelled scripted owner input in private `ROOT/prompt.txt`.
  `browser.mjs ROOT send` uses main-chat Queue and the registered context preparer.
  It rejects identical admitted text and fences uncertain sends with an exclusive
  prompt-hash file. Read/reconcile the original request after uncertainty.
- `view VERSION`, `select VERSION`, `annotate`, `restore-annotation`, `collect
  LABEL` and `reload` perform real browser interactions or authenticated reads.
  Annotation uses a measured `ROOT/blue-point.json`, not a synthetic target.
  Every connection sets the actual interaction viewport to 1920 × 1080.
- `readback.py ROOT LABEL` reads only the owned bridge on 9877. It never writes
  geometry. `replay.py ROOT PUBLICATION_ID` reuses the original persisted native
  invocation/body through an ephemeral trusted capability, not a new invocation.
- `processes.py ROOT restart|stop` checks boot/PID/start identities and stops only
  owned process groups. Unknown or still-active effects require inspection before
  termination. Keep live logs/databases/native history private and retained.
- `verify.py ROOT` and `export.py ROOT` start no services, browser or model.
  Verification checks real retained source/LFS/media/history bytes. Export uses
  explicit allowlists and excludes cookies, pairing/CSRF codes, credentials,
  raw native history, private reasoning and browser profiles.

The recorded run used three roots, zero native children and two actual CPU
presentation frames. New runs must enforce their own renewed request/frame/time
bounds. A past successful allocation is not standing permission to repeat it.
