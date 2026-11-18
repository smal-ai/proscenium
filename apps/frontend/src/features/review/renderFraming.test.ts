import assert from "node:assert/strict";
import { test } from "node:test";
import { Matrix4, OrthographicCamera, PerspectiveCamera, Vector3 } from "three";
import type { Camera } from "../../api/generated/contracts";
import { captureCamera, projectBlenderPoint } from "./media";
import { computeFramingGate, deriveFramedCamera, formatRenderPresetOption, resolveRenderTargetDimensions } from "./renderFraming";

test("render presets label measured pixels separately from the native capture default", () => {
  const measured = { width: 1256, height: 912 };
  assert.equal(formatRenderPresetOption("16:9", measured), "16:9 · 1920 × 1080");
  assert.equal(formatRenderPresetOption("16:10"), "16:10 · 1920 × 1200");
  assert.equal(formatRenderPresetOption("viewport"), "Match viewport");
  assert.equal(formatRenderPresetOption("viewport", { width: 0, height: 912 }), "Match viewport");
  assert.equal(formatRenderPresetOption("viewport", measured), "Match viewport · 1256 × 912");
  assert.deepEqual(resolveRenderTargetDimensions("16:9", measured), { width: 1920, height: 1080 });
  assert.deepEqual(resolveRenderTargetDimensions("16:10"), { width: 1920, height: 1200 });
  assert.equal(resolveRenderTargetDimensions("viewport"), null);
  assert.equal(resolveRenderTargetDimensions("viewport", null), null);
  assert.equal(resolveRenderTargetDimensions("viewport", measured), measured);
});

test("missing and non-positive dimensions leave the gate inactive", () => {
  assert.deepEqual(computeFramingGate(628, 900, null), { active: false, left: 0, top: 0, width: 628, height: 900 });
  assert.deepEqual(computeFramingGate(-1, 900, { width: 1920, height: 1080 }), { active: false, left: 0, top: 0, width: 0, height: 900 });
  assert.deepEqual(computeFramingGate(628, 900, { width: 1920, height: 0 }), { active: false, left: 0, top: 0, width: 628, height: 900 });
});

function unprojectBlenderPoint(camera: Camera, u: number, v: number): number[] {
  const inverseProjection = new Matrix4().fromArray(camera.projection).transpose().invert();
  const cameraToWorld = new Matrix4().fromArray(camera.camera_to_world).transpose();
  return new Vector3(u * 2 - 1, 1 - v * 2, 0.3).applyMatrix4(inverseProjection).applyMatrix4(cameraToWorld).toArray();
}

function assertProjectionCompatibleWithOptics(camera: Camera) {
  // Blender rebuilds the diagonal from vertical optics and output aspect,
  // then independently solves the off-centre terms against the matrix.
  const vertical = camera.kind === "perspective"
    ? 1 / Math.tan(camera.vertical_fov_degrees! * Math.PI / 360)
    : 2 / camera.orthographic_height_m!;
  assert.ok(Math.abs(camera.projection[5] - vertical) < 1e-12);
  assert.ok(Math.abs(camera.projection[0] - vertical / camera.aspect) < 1e-12);
}

for (const [viewportWidth, viewportHeight, layout] of [[1400, 700, "pillarboxed"], [628, 900, "letterboxed"]] as const) {
  for (const preset of ["16:9", "16:10"] as const) {
    const target = resolveRenderTargetDimensions(preset)!;
    const targetAspect = target.width / target.height;
    test(`${preset} gate is centred and fills its ${layout} viewport on the unconstrained axis`, () => {
      const gate = computeFramingGate(viewportWidth, viewportHeight, target);
      assert.equal(gate.active, true);
      assert.ok(Math.abs(gate.width / gate.height - targetAspect) < 1e-12);
      assert.equal(gate.left * 2 + gate.width, viewportWidth);
      assert.equal(gate.top * 2 + gate.height, viewportHeight);
      if (layout === "pillarboxed") {
        assert.equal(gate.height, viewportHeight);
        assert.ok(gate.left > 0);
      } else {
        assert.equal(gate.width, viewportWidth);
        assert.ok(gate.top > 0);
      }
    });

    for (const kind of ["perspective", "orthographic"] as const) {
      for (const offset of ["ordinary", "shifted sub-view"] as const) {
        test(`${kind} ${offset}: ${preset} ${layout} capture matches the gate without changing the live pose`, () => {
          const viewportAspect = viewportWidth / viewportHeight;
          const live = kind === "perspective"
            ? new PerspectiveCamera(50, 1.1, 0.1, 100)
            : new OrthographicCamera(-1, 3, 5, -2, 0.1, 100);
          live.position.set(6, -2, 9);
          live.lookAt(1, 1, -3);
          live.rotateZ(0.23);
          live.zoom = 1.35;
          if (offset === "shifted sub-view") {
            live.setViewOffset(1000, 800, 70, 160, 750, 600);
            if (live instanceof PerspectiveCamera) live.filmOffset = 1.2;
          }
          live.updateProjectionMatrix();
          const original = captureCamera(live);

          const base = live.clone();
          if (base instanceof PerspectiveCamera) base.aspect = viewportAspect;
          else {
            const halfWidth = 3.5 * viewportAspect;
            base.left = 1 - halfWidth;
            base.right = 1 + halfWidth;
          }
          base.updateProjectionMatrix();
          const baseRecord = captureCamera(base);
          const framedRecord = captureCamera(deriveFramedCamera(live, viewportWidth, viewportHeight, target));

          assert.ok(Math.abs(framedRecord.aspect - targetAspect) < 1e-12);
          assert.deepEqual(framedRecord.camera_to_world, baseRecord.camera_to_world);
          assert.deepEqual(captureCamera(live), original);
          assertProjectionCompatibleWithOptics(framedRecord);

          const gate = computeFramingGate(viewportWidth, viewportHeight, target);
          for (const [gateU, gateV] of [[0, 0], [1, 1], [0.5, 0.5], [0.2, 0.8]]) {
            const viewU = (gate.left + gateU * gate.width) / viewportWidth;
            const viewV = (gate.top + gateV * gate.height) / viewportHeight;
            const point = unprojectBlenderPoint(baseRecord, viewU, viewV);
            const [u, v] = projectBlenderPoint(framedRecord, point);
            assert.ok(Math.abs(u - gateU) < 1e-6 && Math.abs(v - gateV) < 1e-6,
              `Expected gate position [${gateU}, ${gateV}], got [${u}, ${v}]`);
          }
        });
      }
    }
  }
}
