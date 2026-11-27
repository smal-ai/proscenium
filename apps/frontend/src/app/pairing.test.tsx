import assert from "node:assert/strict";
import { test, type TestContext } from "node:test";
import type { OwnerBootstrap, OwnerSession } from "../api";
import { capturePairing, watchPairingHash } from "./Pairing";
import { readPrivate, writePrivate } from "./storage";

function tab(t: TestContext) {
  const location = new URL("http://localhost/studio?view=projects");
  const window = new EventTarget();
  const entries = new Map<string, string>();
  const globals = {
    location,
    window,
    history: { replaceState: (_state: unknown, _unused: string, url: string) => {
      location.href = new URL(url, location).href;
    } },
    localStorage: {
      getItem: (key: string) => entries.get(key) ?? null,
      setItem: (key: string, value: string) => { entries.set(key, value); },
      removeItem: (key: string) => { entries.delete(key); },
    },
  };
  for (const [key, value] of Object.entries(globals)) {
    const original = Object.getOwnPropertyDescriptor(globalThis, key);
    Object.defineProperty(globalThis, key, { configurable: true, value });
    t.after(() => {
      if (original) Object.defineProperty(globalThis, key, original);
      else Reflect.deleteProperty(globalThis, key);
    });
  }
  return { location, window };
}

const pairing = () => readPrivate<OwnerBootstrap>("pairing");

test("pairing capture strips the hash, reuses the same command and rotates it for a new code", t => {
  const { location } = tab(t);
  const firstCode = "a".repeat(32);
  const nextCode = "b".repeat(32);
  location.hash = `pair=${firstCode}`;
  assert.equal(capturePairing(), true);
  assert.equal(location.href, "http://localhost/studio?view=projects");
  const first = pairing();
  assert.ok(first?.operation_id && first.client_nonce);
  assert.equal(first.pairing_code, firstCode);
  location.hash = `pair=${firstCode}`;
  assert.equal(capturePairing(), true);
  assert.equal(location.hash, "");
  assert.deepEqual(pairing(), first);

  location.hash = `pair=${nextCode}`;
  assert.equal(capturePairing(), true);
  assert.equal(location.hash, "");
  const next = pairing();
  assert.ok(next?.operation_id && next.client_nonce);
  assert.equal(next.pairing_code, nextCode);
  assert.notEqual(next.operation_id, first.operation_id);
  assert.notEqual(next.client_nonce, first.client_nonce);
  assert.equal(capturePairing(), false);

  location.hash = "pair=";
  assert.equal(capturePairing(), true);
  assert.equal(location.hash, "");
  assert.deepEqual(pairing(), next);
});

test("hashchange captures a link in an open tab before notifying and unsubscribes cleanly", t => {
  const { location, window } = tab(t);
  const observed: { hash: string; code: string | undefined }[] = [];
  const stop = watchPairingHash(() => observed.push({ hash: location.hash, code: pairing()?.pairing_code }));
  location.hash = "unrelated";
  window.dispatchEvent(new Event("hashchange"));
  assert.deepEqual(observed, []);
  assert.equal(location.hash, "#unrelated");

  const code = "c".repeat(32);
  location.hash = `pair=${code}`;
  window.dispatchEvent(new Event("hashchange"));
  assert.deepEqual(observed, [{ hash: "", code }]);

  stop();
  location.hash = `pair=${"d".repeat(32)}`;
  window.dispatchEvent(new Event("hashchange"));
  assert.deepEqual(observed, [{ hash: "", code }]);
  assert.equal(pairing()?.pairing_code, code);
});

test("an authenticated tab discards same-tab fragments but can pair on a full-page visit", t => {
  const { location, window } = tab(t);
  const session: OwnerSession = {
    schema_version: "1.0", owner_session_id: "owner-test", csrf_token: "fixture-token",
    expires_at: "2026-11-28T00:00:00Z",
  };
  writePrivate("session", session);
  t.after(watchPairingHash());
  const code = "e".repeat(32);
  location.hash = `pair=${code}`;
  window.dispatchEvent(new Event("hashchange"));
  assert.equal(location.hash, "");
  assert.equal(pairing(), null);
  assert.deepEqual(readPrivate("session"), session);

  location.hash = `pair=${code}`;
  assert.equal(capturePairing(), true);
  assert.equal(location.hash, "");
  assert.equal(pairing()?.pairing_code, code);
});

test("the mounted pairing view can replace a full-page pairing command despite an older stored session", t => {
  const { location, window } = tab(t);
  writePrivate("session", { owner_session_id: "old-session" });
  t.after(watchPairingHash());
  location.hash = `pair=${"a".repeat(32)}`;
  capturePairing();
  const original = pairing();
  assert.ok(original);
  const observed: (OwnerBootstrap | null)[] = [];
  const unmount = watchPairingHash(() => observed.push(pairing()), "pairing");
  t.after(unmount);

  const code = "b".repeat(32);
  location.hash = `pair=${code}`;
  window.dispatchEvent(new Event("hashchange"));
  const replacement = pairing();
  assert.ok(replacement);
  assert.deepEqual(observed, [replacement]);
  assert.equal(location.hash, "");
  assert.equal(replacement.pairing_code, code);
  assert.notEqual(replacement.operation_id, original.operation_id);
  assert.notEqual(replacement.client_nonce, original.client_nonce);

  unmount();
  location.hash = `pair=${"c".repeat(32)}`;
  window.dispatchEvent(new Event("hashchange"));
  assert.equal(location.hash, "");
  assert.deepEqual(pairing(), replacement);
});
