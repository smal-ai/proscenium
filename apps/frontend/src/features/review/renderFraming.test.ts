import assert from "node:assert/strict";
import { test } from "node:test";
import { Box3, Matrix4, MeshPhysicalMaterial, MeshStandardMaterial, OrthographicCamera, PerspectiveCamera, Texture, Vector3 } from "three";
import type { Camera } from "../../api/generated/contracts";
import { cloneInspectionMaterial } from "./inspectionMaterials";
import { captureCamera, projectBlenderPoint } from "./media";
import { computeAutoFramePose, computeCanvasFramingGate, computeFramingGate, deriveFramedCamera, formatRenderPresetOption, resolveRenderTargetDimensions } from "./renderFraming";

test("auto-frame centres a half-metre proxy from the original three-quarter view", () => {
  const box = new Box3(new Vector3(-0.25, -0.21, -0.06), new Vector3(0.25, 0.21, 0.07));
  const pose = computeAutoFramePose(box, 50)!;
  const target = new Vector3(...pose.target);
  const offset = new Vector3(...pose.position).sub(target);
  assert.ok(target.distanceTo(new Vector3(0, 0, 0.005)) < 1e-12);
  assert.ok(offset.length() > 1 && offset.length() < 1.1);
  assert.ok(offset.clone().normalize().distanceTo(new Vector3(5, 4, 5).normalize()) < 1e-12);
  assert.equal(pose.near, 0.01);
  assert.equal(pose.far, 1000);
});

test("auto-frame projects all eight corners inside a portrait viewport", () => {
  const width = 400, height = 800, fov = 50;
  const box = new Box3(new Vector3(-0.25, -0.21, -0.06), new Vector3(0.25, 0.21, 0.07));
  const pose = computeAutoFramePose(box, fov, width / height)!;
  const camera = new PerspectiveCamera(fov, width / height, pose.near, pose.far);
  camera.position.set(...pose.position);
  camera.lookAt(...pose.target);
  camera.updateMatrixWorld();

  for (const x of [box.min.x, box.max.x]) {
    for (const y of [box.min.y, box.max.y]) {
      for (const z of [box.min.z, box.max.z]) {
        const projected = new Vector3(x, y, z).project(camera);
        const pixelX = (projected.x + 1) * width / 2;
        const pixelY = (1 - projected.y) * height / 2;
        assert.ok(pixelX > 0 && pixelX < width && pixelY > 0 && pixelY < height,
          `Corner [${x}, ${y}, ${z}] projects outside the viewport at [${pixelX}, ${pixelY}]`);
      }
    }
  }
});

test("auto-frame rejects empty bounds and keeps point-like and large models inside the clip planes", () => {
  assert.equal(computeAutoFramePose(new Box3(), 50), null);
  const point = new Vector3(2, 3, 4);
  const tiny = computeAutoFramePose(new Box3(point, point), 50)!;
  const tinyDistance = new Vector3(...tiny.position).distanceTo(point);
  assert.deepEqual(tiny.target, [2, 3, 4]);
  assert.ok(Math.abs(tinyDistance - 0.05 * 1.35 / Math.sin(25 * Math.PI / 180)) < 1e-12);
  assert.ok(tiny.near >= 0.001 && tiny.near < tinyDistance);
  const large = computeAutoFramePose(new Box3(new Vector3(-100, -100, -100), new Vector3(100, 100, 100)), 50)!;
  assert.ok(large.far > 1000 && large.far > new Vector3(...large.position).length() + Math.sqrt(3) * 100);
});

test("inspection lifts dark graphite without changing its cached tint or texture", () => {
  const source = new MeshPhysicalMaterial({ map: new Texture(), metalness: 0.8, roughness: 0.1 });
  source.color.setRGB(0.014, 0.017, 0.020);
  const clone = cloneInspectionMaterial(source) as MeshPhysicalMaterial;
  assert.notEqual(clone, source);
  assert.ok(clone instanceof MeshPhysicalMaterial);
  assert.equal(clone.map, source.map);
  const { r, g, b } = clone.color;
  assert.ok(Math.abs(0.2126 * r + 0.7152 * g + 0.0722 * b - 0.18) < 1e-12);
  assert.ok(Math.abs(r / b - 0.014 / 0.020) < 1e-12);
  assert.ok(Math.abs(g / b - 0.017 / 0.020) < 1e-12);
  assert.deepEqual(source.color.toArray(), [0.014, 0.017, 0.020]);
  assert.deepEqual([clone.metalness, clone.roughness, source.metalness, source.roughness], [0.35, 0.28, 0.8, 0.1]);
});

test("inspection lifts black neutrally and leaves bright material values alone", () => {
  const black = cloneInspectionMaterial(new MeshStandardMaterial({color: 0, roughness: 1})) as MeshStandardMaterial;
  assert.deepEqual(black.color.toArray(), [0.18, 0.18, 0.18]);
  assert.equal(black.roughness, 0.75);
  const bright = new MeshStandardMaterial({metalness: 1, roughness: 0.1});
  const clone = cloneInspectionMaterial(bright) as MeshStandardMaterial;
  assert.notEqual(clone, bright);
  assert.deepEqual([clone.color.toArray(), clone.metalness, clone.roughness], [bright.color.toArray(), 1, 0.1]);
});

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

for (const [hostWidth, hostHeight] of [[628, 900], [1400, 700]] as const) {
  for (const [cameraAspect, cameraLabel] of [[4 / 3, "4:3"], [2.39, "2.39:1"]] as const) {
    for (const preset of ["16:9", "16:10"] as const) {
      test(`${preset} gate is offset inside a fitted ${cameraLabel} canvas on a ${hostWidth} × ${hostHeight} host`, () => {
        const width = Math.min(hostWidth, hostHeight * cameraAspect);
        const height = width / cameraAspect;
        const rect = { left: (hostWidth - width) / 2, top: (hostHeight - height) / 2, width, height };
        const gate = computeCanvasFramingGate(rect, resolveRenderTargetDimensions(preset));
        const box = computeFramingGate(width, height, resolveRenderTargetDimensions(preset));
        assert.deepEqual(gate, { ...box, left: rect.left + box.left, top: rect.top + box.top });
        assert.equal(gate.active, true);
        assert.ok(gate.left >= rect.left && gate.top >= rect.top);
        assert.ok(Math.abs(gate.left * 2 + gate.width - hostWidth) < 1e-9);
        assert.ok(Math.abs(gate.top * 2 + gate.height - hostHeight) < 1e-9);
        assert.ok(gate.width === width || gate.height === height);
        assert.ok(gate.width < hostWidth || gate.height < hostHeight);
        assert.deepEqual(computeCanvasFramingGate(rect, null), { ...rect, active: false });
      });
    }
  }
}

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

function assertPointsMatchGate(base: Camera, framed: Camera, width: number, height: number, target: { width: number; height: number }) {
  const gate = computeFramingGate(width, height, target);
  for (const [gateU, gateV] of [[0, 0], [1, 1], [0.5, 0.5], [0.2, 0.8]]) {
    const viewU = (gate.left + gateU * gate.width) / width;
    const viewV = (gate.top + gateV * gate.height) / height;
    const point = unprojectBlenderPoint(base, viewU, viewV);
    const [u, v] = projectBlenderPoint(framed, point);
    assert.ok(Math.abs(u - gateU) < 1e-6 && Math.abs(v - gateV) < 1e-6,
      `Expected gate position [${gateU}, ${gateV}], got [${u}, ${v}]`);
  }
}

for (const kind of ["perspective", "orthographic"] as const) {
  const createCamera = () => kind === "perspective"
    ? new PerspectiveCamera(50, 1000 / 800, 0.1, 100)
    : new OrthographicCamera(-3, 7, 5, -3, 0.1, 100);

  test(`${kind}: shifted equal-fraction sub-view keeps square pixels and gate UVs`, () => {
    const live = createCamera();
    live.setViewOffset(1000, 800, 70, 100, 750, 600);
    const target = resolveRenderTargetDimensions("16:9")!;
    const framed = captureCamera(deriveFramedCamera(live, 1000, 800, target));
    assertProjectionCompatibleWithOptics(framed);
    assertPointsMatchGate(captureCamera(live), framed, 1000, 800, target);
  });

  test(`${kind}: unequal fractions fail closed on an anisotropic canvas`, () => {
    const live = createCamera();
    live.setViewOffset(1000, 800, 70, 160, 750, 400);
    const original = captureCamera(live);
    const target = resolveRenderTargetDimensions("16:9")!;
    assert.throws(() => deriveFramedCamera(live, 1000, 800, target), {
      name: "RangeError", message: "Anisotropic camera sub-view cannot be reproduced with square-pixel Blender optics",
    });
    assert.deepEqual(captureCamera(live), original);
  });

  test(`${kind}: unequal fractions keep gate UVs at the sub-view's square-pixel aspect`, () => {
    const live = createCamera();
    live.setViewOffset(1000, 800, 70, 160, 750, 400);
    const target = resolveRenderTargetDimensions("16:10")!;
    const framed = captureCamera(deriveFramedCamera(live, 1500, 800, target));
    assertProjectionCompatibleWithOptics(framed);
    assertPointsMatchGate(captureCamera(live), framed, 1500, 800, target);
  });
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

          assertPointsMatchGate(baseRecord, framedRecord, viewportWidth, viewportHeight, target);
        });
      }
    }
  }
}
