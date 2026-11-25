import assert from "node:assert/strict";
import { test } from "node:test";
import { Group, OrthographicCamera, PerspectiveCamera, Vector3 } from "three";
import { computeNavigationStep, isPointWithinCameraClip, isTextInputTarget, keyboardMoveDirection, normalizeWheelNotches, recenterOrbitTarget, translateCameraAndTarget } from "./proxyNavigation";

test("depth navigation steps scale with distance and radius, with a floor and cap", () => {
  assert.equal(computeNavigationStep(10, 1), 1.2);
  assert.equal(computeNavigationStep(0, 10), 0.1);
  assert.equal(computeNavigationStep(0.5, 200), 0.25);
  assert.equal(computeNavigationStep(10, 200), 1.2);
  assert.equal(computeNavigationStep(0.0001, 0.0001), 0.02);
  assert.equal(computeNavigationStep(1e10, 0.001), 5);
  assert.equal(computeNavigationStep(1e20, 1e10), 5e9);
  assert.equal(computeNavigationStep(NaN, Infinity), 0.02);
  assert.equal(computeNavigationStep(-1, -10), 0.02);
});

test("wheel normalisation uses the dominant axis and converts pixel, line and page units", () => {
  assert.equal(normalizeWheelNotches(-120, 10, 0), 1.2);
  assert.equal(normalizeWheelNotches(0, -120, 0), 1.2);
  assert.equal(normalizeWheelNotches(6, 0, 1), -2);
  assert.equal(normalizeWheelNotches(0, -1, 2), 2);
  assert.equal(normalizeWheelNotches(1200, 0, 0), -3);
  assert.equal(normalizeWheelNotches(0, -300, 1), 3);
  assert.equal(normalizeWheelNotches(0, 0, 2), 0);
  assert.equal(normalizeWheelNotches(0, NaN, 0), 0);
  assert.equal(normalizeWheelNotches(Infinity, 0, 0), 0);
});

test("keyboard navigation recognises six axes and Shift boost but leaves shortcuts alone", () => {
  const plain = { key: "", shiftKey: false, ctrlKey: false, metaKey: false, altKey: false };
  for (const [key, forward, right, up] of [["W", 1, 0, 0], ["S", -1, 0, 0], ["A", 0, -1, 0], ["D", 0, 1, 0], ["E", 0, 0, 1], ["Q", 0, 0, -1]] as const) {
    assert.deepEqual(keyboardMoveDirection({ ...plain, code: `Key${key}`, shiftKey: true }), { forward, right, up, boost: 3 });
    assert.deepEqual(keyboardMoveDirection({ ...plain, code: "", key }), { forward, right, up, boost: 1 });
  }
  for (const modifier of ["ctrlKey", "metaKey", "altKey"]) assert.equal(keyboardMoveDirection({ ...plain, key: "w", [modifier]: true }), null);
  assert.equal(keyboardMoveDirection({ ...plain, key: "Enter" }), null);
  assert.equal(isTextInputTarget(null), false);
  assert.equal(isTextInputTarget(new EventTarget()), false);
});

for (const camera of [new PerspectiveCamera(), new OrthographicCamera()]) {
  test(`${camera.type} translation preserves pose orientation and orbit distance`, () => {
    const target = new Vector3(1, 2, 3);
    camera.position.set(-2, 4, 6);
    camera.lookAt(target);
    const before = camera.position.clone();
    const pivot = target.clone();
    const quaternion = camera.quaternion.clone();
    const forward = camera.getWorldDirection(new Vector3());
    const delta = forward.multiplyScalar(2).add(new Vector3(-1, 3, 0).applyQuaternion(quaternion));
    assert.equal(translateCameraAndTarget(camera, target, { forward: 2, right: -1, up: 3 }), true);
    assert.ok(camera.position.distanceTo(before.clone().add(delta)) < 1e-12);
    assert.ok(target.distanceTo(pivot.clone().add(delta)) < 1e-12);
    assert.ok(Math.abs(camera.position.distanceTo(target) - before.distanceTo(pivot)) < 1e-12);
    assert.ok(camera.quaternion.equals(quaternion));
    assert.equal(translateCameraAndTarget(camera, target, {}), false);
    assert.equal(translateCameraAndTarget(camera, target, { up: NaN }), false);
  });
}

test("surface recenter moves only the pivot and rejects invalid or coincident points", () => {
  const camera = new PerspectiveCamera();
  camera.position.set(1, 2, 3);
  const position = camera.position.clone();
  const target = new Vector3();
  const hit = new Vector3(4, 5, 6);
  assert.equal(recenterOrbitTarget(camera, target, hit), true);
  assert.ok(camera.position.equals(position));
  assert.ok(target.equals(hit));
  assert.equal(recenterOrbitTarget(camera, target, position), false);
  assert.equal(recenterOrbitTarget(camera, target, new Vector3(Infinity, 0, 0)), false);
  assert.ok(target.equals(hit));
});

test("translation uses the camera's world axes under a rotated parent", () => {
  const parent = new Group();
  const camera = new PerspectiveCamera();
  parent.position.set(10, -5, 2);
  parent.rotation.z = Math.PI / 2;
  parent.add(camera);
  parent.updateMatrixWorld();
  const before = camera.getWorldPosition(new Vector3());
  const target = before.clone().add(new Vector3(0, 0, -3));
  const pivot = target.clone();
  const delta = new Vector3(-2, 1, 0);
  assert.equal(translateCameraAndTarget(camera, target, { right: 1, up: 2 }), true);
  camera.updateMatrixWorld();
  const world = camera.getWorldPosition(new Vector3());
  assert.ok(world.distanceTo(before.clone().add(delta)) < 1e-12);
  assert.ok(target.distanceTo(pivot.clone().add(delta)) < 1e-12);
  assert.ok(Math.abs(world.distanceTo(target) - before.distanceTo(pivot)) < 1e-12);
  assert.equal(recenterOrbitTarget(camera, target, world), false);
  assert.equal(recenterOrbitTarget(camera, target, pivot), true);
  assert.ok(target.equals(pivot));
  assert.ok(camera.getWorldPosition(new Vector3()).equals(world));
});

for (const camera of [new PerspectiveCamera(50, 1, 1, 10), new OrthographicCamera(-1, 1, 1, -1, 1, 10)]) {
  test(`${camera.type} rejects surface hits outside the visible clip depth`, () => {
    camera.position.set(5, 0, 2);
    for (const z of [1, -3, -8]) assert.equal(isPointWithinCameraClip(camera, new Vector3(5, 0, z)), true);
    for (const z of [1.5, -9, 3, Infinity, NaN]) assert.equal(isPointWithinCameraClip(camera, new Vector3(5, 0, z)), false);
  });
}
