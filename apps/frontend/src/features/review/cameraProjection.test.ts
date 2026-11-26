import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { Matrix4, OrthographicCamera, PerspectiveCamera, Vector3, Vector4 } from "three";
import ts from "typescript";
import type { Markup, SpatialHit } from "../../api/generated/contracts";
import { projectBlenderPoint } from "./media";
import { captureCamera } from "./proxyCamera";
import { projectBlenderPointVisible, syncSpatialPinMarkup } from "./spatial";

for (const camera of [new PerspectiveCamera(57, 1.6, 0.125, 73), new OrthographicCamera(-4, 3, 2, -3, 0.125, 73)]) {
  test(`wire projection matches ${camera.type} to the last bit`, () => {
    camera.position.set(2.3, -1.7, 4.1);
    camera.lookAt(-1.2, 3.1, 0.5);
    camera.rotateZ(0.23);
    camera.setViewOffset(1600, 1000, 31, 12, 1440, 900);
    const record = captureCamera(camera);
    const world = new Matrix4().fromArray(record.camera_to_world).transpose();
    const matrix = new Matrix4().fromArray(record.projection).transpose().multiply(world.clone().invert());
    const cases = [
      { local: [0.2, -0.3, -4], visible: true },
      { local: [100, 0, -4], visible: false },
      { local: [0, 0, -0.01], visible: false },
      { local: [0, 0, -100], visible: false },
      { local: [0, 0, 4], visible: false },
    ];
    for (const {local: [x, y, z], visible} of cases) {
      const point = new Vector3(x, y, z).applyMatrix4(world);
      const ndc = point.clone().applyMatrix4(matrix);
      assert.deepEqual(projectBlenderPoint(record, point.toArray()), [(ndc.x + 1) / 2, (1 - ndc.y) / 2]);
      const clip = new Vector4(point.x, point.y, point.z, 1).applyMatrix4(matrix);
      const uv = clip.w <= 1e-6 ? [0.5, 0.5] : [(clip.x / clip.w + 1) / 2, (1 - clip.y / clip.w) / 2];
      assert.deepEqual(projectBlenderPointVisible(record, point.toArray()), {uv, visible});
    }
  });
}

test("row-major projection inverts a full non-affine camera transform", () => {
  const world = new Matrix4().set(2, 1, 0, 4, -1, 3, 1, 0, 0, 0.5, 3, 2, 0.2, 0, 0.4, 1);
  const camera = new PerspectiveCamera(57, 1.6, 0.125, 73);
  const record = {...captureCamera(camera), camera_to_world: world.clone().transpose().toArray()};
  const point = new Vector3(1.7, -2.1, 3.6);
  const reference = point.clone().applyMatrix4(camera.projectionMatrix.clone().multiply(world.clone().invert()));
  assert.deepEqual(projectBlenderPoint(record, point.toArray()), [(reference.x + 1) / 2, (1 - reference.y) / 2]);
});

test("singular cameras and invalid homogeneous depth cannot produce visible pins", () => {
  const camera = captureCamera(new PerspectiveCamera());
  const singular = {...camera, camera_to_world: new Matrix4().makeScale(1, 0, 1).toArray()};
  const point = new Vector3(0, 2, 0);
  const view = new Matrix4().fromArray(singular.camera_to_world).transpose().invert();
  const reference = point.clone().applyMatrix4(new Matrix4().fromArray(singular.projection).transpose().multiply(view));
  const expected = [(reference.x + 1) / 2, (1 - reference.y) / 2];
  const actual = projectBlenderPoint(singular, point.toArray());
  const hidden = { uv: [0.5, 0.5], visible: false };
  assert.ok(expected.every(Number.isNaN));
  assert.ok(actual.every(Number.isNaN));
  assert.deepEqual(actual, expected);
  assert.deepEqual(projectBlenderPointVisible(singular, point.toArray()), hidden);
  assert.deepEqual(projectBlenderPointVisible(camera, [0, 1e-7, 0]), hidden);
  assert.deepEqual(projectBlenderPointVisible(camera, [0, Infinity, 0]), hidden);
});

test("a non-finite projection neither creates nor changes pin markup", () => {
  const camera = {...captureCamera(new PerspectiveCamera()), camera_to_world: new Matrix4().makeScale(1, 0, 1).toArray()};
  const hit: SpatialHit = {
    proxy_artifact_id: "proxy", mapping_artifact_id: "mapping", version_id: "version", sample_frame: 1,
    object_id: "object", instance_id: "instance", space: "blender_world_m", position: [0, 2, 0], normal: [0, 0, 1], camera,
  };
  const empty: Markup[] = [];
  assert.strictEqual(syncSpatialPinMarkup(empty, camera, hit, "New pin"), empty);
  assert.deepEqual(empty, []);

  const existing: Markup[] = [{ id: "saved-pin", kind: "pin", points: [[0.2, 0.8]], text: "Saved pin" }];
  const original = structuredClone(existing);
  assert.strictEqual(syncSpatialPinMarkup(existing, camera, hit, "Changed pin"), existing);
  assert.deepEqual(existing, original);
});

test("eager review helpers have no 3D imports", () => {
  for (const file of ["media.ts", "spatial.ts", "renderFraming.ts"]) {
    const source = readFileSync(new URL(file, import.meta.url), "utf8");
    const imports = ts.preProcessFile(source, true, true).importedFiles.map(module => module.fileName);
    assert.ok(!imports.some(module => /^(three(?:-stdlib)?|@react-three\/[^/]+)(\/|$)|^\.\/(ProxyView|proxyCamera|proxyFraming|proxySpatial)$/.test(module)), `${file}: ${imports.join(", ")}`);
  }
});
