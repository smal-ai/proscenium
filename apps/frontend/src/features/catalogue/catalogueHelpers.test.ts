import assert from "node:assert/strict";
import { test } from "node:test";
import { displayItemTitle, formatItemStatusLine, shortVersionLabel } from "./catalogueHelpers";

test("shortens a version suffix, including bare, uppercase and underscore forms", () => {
  for (const [id, label] of [
    ["signal-lost-native-kit-v3", "v3"], ["a-v2", "v2"], ["b-v1", "v1"],
    ["panel_V12", "v12"], ["V4", "v4"], ["panelv07", "v07"],
  ]) assert.equal(shortVersionLabel(id), label);
});

test("preserves version IDs without a terminal numeric suffix", () => {
  for (const id of ["draft", "v", "panel-v1-final", "panel-v1.2", "panel-123"]) {
    assert.equal(shortVersionLabel(id), id);
  }
});

test("strips a matching project prefix case-insensitively with either separator", () => {
  assert.equal(displayItemTitle("SIGNAL LOST — Native Asset Kit / First Lookdev", "Signal Lost"), "Native Asset Kit / First Lookdev");
  assert.equal(displayItemTitle("Signal Lost - Orbital Relay", "signal lost"), "Orbital Relay");
  assert.equal(displayItemTitle("A Project — Part 2 — Orbital Relay", "A Project — Part 2"), "Orbital Relay");
});

test("strips a shared em-dash prefix even without the project name", () => {
  const names = ["SIGNAL LOST — Native Asset Kit / First Lookdev", "Signal Lost — Orbital Relay", "Asset A"];
  assert.equal(displayItemTitle(names[0], null, names), "Native Asset Kit / First Lookdev");
  assert.equal(displayItemTitle(names[1], "Other project", names), "Orbital Relay");
  assert.equal(displayItemTitle(names[2], null, names), "Asset A");
});

test("preserves unshared, unrelated and prefix-only names", () => {
  assert.equal(displayItemTitle("Storyboard B"), "Storyboard B");
  assert.equal(displayItemTitle("Signal Lost — Relay", "Signal", ["Signal Lost — Relay", "Signal — Antenna"]), "Signal Lost — Relay");
  assert.equal(displayItemTitle("Signal Lost - Relay", null, ["Signal Lost - Relay", "Signal Lost - Antenna"]), "Signal Lost - Relay");
  assert.equal(displayItemTitle("Signal Lost — ", "Signal Lost"), "Signal Lost — ");
  assert.equal(displayItemTitle("Signal Lost — ", null, ["Signal Lost — ", "Signal Lost — Relay"]), "Signal Lost — ");
  assert.equal(displayItemTitle(""), "Untitled item");
});

test("separates viewed, latest and owner-selected versions without repeating latest", () => {
  const asset = {kind: "asset", latestVersionId: "asset-v3", ownerVersionId: "asset-v2"};
  assert.equal(formatItemStatusLine({...asset, viewedVersionId: "asset-v3"}), "Asset · Viewing v3 (latest) · Owner selected v2");
  assert.equal(formatItemStatusLine({...asset, viewedVersionId: "asset-v2"}), "Asset · Viewing v2 · Latest v3 · Owner selected v2");
  assert.equal(formatItemStatusLine({...asset, viewedVersionId: "asset-v3", ownerVersionId: null}), "Asset · Viewing v3 (latest)");
  assert.equal(formatItemStatusLine({...asset, viewedVersionId: null}), "Asset · Latest v3 · Owner selected v2");
});

test("handles unviewed items, missing publications and IDs that share a short label", () => {
  const empty = {viewedVersionId: null, latestVersionId: null, ownerVersionId: null};
  assert.equal(formatItemStatusLine({...empty, kind: "storyboard", latestVersionId: "storyboard-v4"}), "Storyboard · Latest v4");
  assert.equal(formatItemStatusLine({...empty, kind: "scene"}), "Scene");
  assert.equal(formatItemStatusLine({...empty, kind: "sequence", viewedVersionId: "draft"}), "Sequence · Viewing draft");
  assert.equal(formatItemStatusLine({...empty, kind: "asset", viewedVersionId: "first-v2", latestVersionId: "second-v2"}), "Asset · Viewing v2 · Latest v2");
});
