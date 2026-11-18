import assert from "node:assert/strict";
import { test } from "node:test";
import { OrthographicCamera, PerspectiveCamera, Raycaster, Vector2, Vector3 } from "three";
import { GLTFLoader } from "three-stdlib";
import type { AnnotationSnapshot, ProxyMapping } from "../../api/generated/contracts";
import { annotationContext } from "./context";
import { captureCamera, projectBlenderPoint } from "./media";
import {
  blenderPointToProxy,
  indexProxyScene,
  projectBlenderPointVisible,
  proxyPointToBlender,
  proxySurfaceNormalToBlender,
  resolveProxyNode,
  verifySpatialMapping,
} from "./spatial";

function buildMultiPrimitiveProxyGlb(): ArrayBuffer {
  // Unit-ish box [-0.8, 0.8]^3: 6 faces * 2 triangles = 12 triangles (36 vertices, unindexed for crisp face normals).
  const s = 0.8;
  const faces: Array<[[number, number, number], [number, number, number], [number, number, number], [number, number, number]]> = [
    // +Z front
    [[-s, -s, s], [s, -s, s], [s, s, s], [-s, s, s]],
    // -Z back
    [[s, -s, -s], [-s, -s, -s], [-s, s, -s], [s, s, -s]],
    // +Y top
    [[-s, s, s], [s, s, s], [s, s, -s], [-s, s, -s]],
    // -Y bottom
    [[-s, -s, -s], [s, -s, -s], [s, -s, s], [-s, -s, s]],
    // +X right
    [[s, -s, s], [s, -s, -s], [s, s, -s], [s, s, s]],
    // -X left
    [[-s, -s, -s], [-s, -s, s], [-s, s, s], [-s, s, -s]],
  ];
  const pushQuad = (out: number[], [a, b, c, d]: typeof faces[number]) => {
    out.push(...a, ...b, ...c, ...a, ...c, ...d);
  };
  const primA: number[] = [];
  for (const face of faces.slice(0, 3)) pushQuad(primA, face);
  const primB: number[] = [];
  for (const face of faces.slice(3)) pushQuad(primB, face);
  const fullBox = [...primA, ...primB];

  const floats = new Float32Array([...primA, ...primB, ...fullBox]);
  const bin = Buffer.from(floats.buffer);
  const gltf = {
    asset: { version: "2.0" },
    scene: 0,
    scenes: [{ nodes: [0] }],
    nodes: [
      {
        name: "Housing",
        mesh: 0,
        translation: [1.6, 0, -1.6],
        children: [1, 2],
        extras: { proscenium_object_id: "housing", proscenium_instance_id: "housing-1" },
      },
      {
        name: "Collar",
        mesh: 1,
        translation: [-3.2, 0.2, 3.2],
        rotation: [Math.sin(Math.PI / 12), 0, 0, Math.cos(Math.PI / 12)],
        scale: [1.5, 0.6, 1.1],
        extras: { proscenium_object_id: "collar", proscenium_instance_id: "collar-1" },
      },
      {
        name: "UnmappedHelper",
        mesh: 1,
        translation: [0, 8, 0],
        extras: { proscenium_object_id: "helper", proscenium_instance_id: "helper-unmapped" },
      },
    ],
    meshes: [
      { primitives: [{ attributes: { POSITION: 0 } }, { attributes: { POSITION: 1 } }] },
      { primitives: [{ attributes: { POSITION: 2 } }] },
    ],
    accessors: [
      { bufferView: 0, componentType: 5126, count: primA.length / 3, type: "VEC3", min: [-s, -s, -s], max: [s, s, s] },
      { bufferView: 1, componentType: 5126, count: primB.length / 3, type: "VEC3", min: [-s, -s, -s], max: [s, s, s] },
      { bufferView: 2, componentType: 5126, count: fullBox.length / 3, type: "VEC3", min: [-s, -s, -s], max: [s, s, s] },
    ],
    bufferViews: [
      { buffer: 0, byteOffset: 0, byteLength: primA.length * 4 },
      { buffer: 0, byteOffset: primA.length * 4, byteLength: primB.length * 4 },
      { buffer: 0, byteOffset: (primA.length + primB.length) * 4, byteLength: fullBox.length * 4 },
    ],
    buffers: [{ byteLength: bin.length }],
  };
  const json = Buffer.from(JSON.stringify(gltf));
  const paddedJsonLen = (json.length + 3) & ~3;
  const total = 12 + 8 + paddedJsonLen + 8 + bin.length;
  const out = Buffer.alloc(total);
  out.write("glTF", 0);
  out.writeUInt32LE(2, 4);
  out.writeUInt32LE(total, 8);
  out.writeUInt32LE(paddedJsonLen, 12);
  out.write("JSON", 16);
  out.fill(0x20, 20, 20 + paddedJsonLen);
  json.copy(out, 20);
  out.writeUInt32LE(bin.length, 20 + paddedJsonLen);
  out.write("BIN\0", 24 + paddedJsonLen);
  bin.copy(out, 28 + paddedJsonLen);
  return out.buffer.slice(out.byteOffset, out.byteOffset + out.byteLength);
}

const IDENTITY: [
  number, number, number, number,
  number, number, number, number,
  number, number, number, number,
  number, number, number, number,
] = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1];

const mapping: ProxyMapping = {
  schema_version: "1.0",
  project_id: "project-one",
  version_id: "a-v2",
  proxy_artifact_id: "proxy-a-v2",
  mapping_artifact_id: "map-a-v2",
  source_closure_digest: "a".repeat(64),
  sample_frame: 1,
  space: "proxy_world_m",
  nodes: [
    { node_path: [0], object_id: "housing", instance_id: "housing-1", display_name: "Housing", world_matrix: IDENTITY },
    { node_path: [0, 0], object_id: "collar", instance_id: "collar-1", display_name: "Collar", world_matrix: IDENTITY },
  ],
};

test("basis conversions match normative coordinate vectors within 1e-6 m", () => {
  for (const [blender, proxy] of [
    [[0, 0, 0], [0, 0, 0]],
    [[1, 2, 3], [1, 3, -2]],
    [[-2, 4, -1], [-2, -1, -4]],
  ] as const) {
    const toProxy = blenderPointToProxy(blender);
    const roundTrip = proxyPointToBlender(toProxy);
    for (let i = 0; i < 3; i++) {
      assert.ok(Math.abs(toProxy[i] - proxy[i]) <= 1e-6);
      assert.ok(Math.abs(roundTrip[i] - blender[i]) <= 1e-6);
    }
  }
});

test("multi-primitive parent and rotated non-uniform child resolve exact instances, unit normals and <=0.5px projection at DPR 1 and DPR 2", async () => {
  const glb = buildMultiPrimitiveProxyGlb();
  const parsed = await new GLTFLoader().parseAsync(glb, "");
  const scene = parsed.scene.clone(true);
  scene.updateMatrixWorld(true);
  const index = indexProxyScene(scene, mapping);

  for (const [width, height] of [[1200, 800], [2400, 1600]] as const) {
    const camera = new PerspectiveCamera(50, width / height, 0.01, 10000);
    camera.position.set(5, 4, 5);
    camera.lookAt(0, 0, 0);
    camera.updateProjectionMatrix();
    camera.updateMatrixWorld();
    const frozenCamera = captureCamera(camera);

    // Project centres of Housing (1.6, 0, -1.6) -> Blender (1.6, 1.6, 0) and Collar (-1.6, 0.2, 1.6) -> Blender (-1.6, -1.6, 0.2).
    const housingUv = projectBlenderPoint(frozenCamera, proxyPointToBlender([1.6, 0, -1.6]));
    const collarUv = projectBlenderPoint(frozenCamera, proxyPointToBlender([-1.6, 0.2, 1.6]));

    for (const [uv, expectedId] of [[housingUv, "housing-1"], [collarUv, "collar-1"]] as const) {
      const raycaster = new Raycaster();
      raycaster.setFromCamera(new Vector2(uv[0] * 2 - 1, 1 - uv[1] * 2), camera);
      const hit = raycaster.intersectObject(scene, true)[0];
      assert.ok(hit, `Expected surface hit for ${expectedId}`);
      const node = resolveProxyNode(hit.object, index);
      assert.equal(node?.instance_id, expectedId);

      const blenderPos = proxyPointToBlender(hit.point);
      const reprojected = projectBlenderPoint(frozenCamera, blenderPos);
      assert.ok(Math.abs(reprojected[0] - uv[0]) * width <= 0.5, `X pixel error exceeded 0.5px for ${expectedId}`);
      assert.ok(Math.abs(reprojected[1] - uv[1]) * height <= 0.5, `Y pixel error exceeded 0.5px for ${expectedId}`);
      assert.equal(projectBlenderPointVisible(frozenCamera, blenderPos).visible, true);

      const blenderNormal = proxySurfaceNormalToBlender(hit.object, hit.face!.normal, raycaster.ray.direction);
      const len = Math.hypot(...blenderNormal);
      assert.ok(Math.abs(len - 1) < 1e-6, `Expected unit normal, got ${len}`);

      if (expectedId === "collar-1") {
        // Verify orthogonality against transformed world-space triangle edges on the non-uniformly scaled child!
        const posAttr = (hit.object as unknown as { geometry: { getAttribute: (name: string) => { getX: (i: number) => number; getY: (i: number) => number; getZ: (i: number) => number } } }).geometry.getAttribute("position");
        const readWorld = (idx: number) => new Vector3(posAttr.getX(idx), posAttr.getY(idx), posAttr.getZ(idx)).applyMatrix4(hit.object.matrixWorld);
        const a = readWorld(hit.face!.a);
        const b = readWorld(hit.face!.b);
        const c = readWorld(hit.face!.c);
        const edge1Blender = new Vector3(...proxyPointToBlender(b.clone().sub(a))).normalize();
        const edge2Blender = new Vector3(...proxyPointToBlender(c.clone().sub(a))).normalize();
        const normalVec = new Vector3(...blenderNormal);
        assert.ok(Math.abs(normalVec.dot(edge1Blender)) < 1e-5, "Transformed normal is not orthogonal to world edge 1");
        assert.ok(Math.abs(normalVec.dot(edge2Blender)) < 1e-5, "Transformed normal is not orthogonal to world edge 2");
      }
    }

    // Points behind the camera must not project as visible overlays.
    assert.equal(projectBlenderPointVisible(frozenCamera, proxyPointToBlender([10, 8, 10])).visible, false);
  }

  // Unmapped child node under Housing must return null rather than climbing to Housing!
  const unmappedRay = new Raycaster(new Vector3(1.6, 8, 2), new Vector3(0, 0, -1));
  const unmappedHit = unmappedRay.intersectObject(scene, true)[0];
  assert.ok(unmappedHit);
  assert.equal(resolveProxyNode(unmappedHit.object, index), null);

  // Ambiguous duplicate mapping nodes are rejected rather than guessed.
  const duplicateIndex = indexProxyScene(scene, {
    ...mapping,
    nodes: [...mapping.nodes, { ...mapping.nodes[1] }],
  });
  assert.equal(duplicateIndex.size, 1);
});

test("orthographic camera projection and verifySpatialMapping preserve exact identities", () => {
  const ortho = new OrthographicCamera(-4, 4, 2.25, -2.25, 0.1, 100);
  ortho.position.set(0, 2, 10);
  ortho.lookAt(0, 2, 0);
  ortho.updateProjectionMatrix();
  ortho.updateMatrixWorld();
  const captured = captureCamera(ortho);
  const centre = projectBlenderPointVisible(captured, proxyPointToBlender([0, 2, 0]));
  assert.equal(centre.visible, true);
  assert.ok(Math.abs(centre.uv[0] - 0.5) < 1e-6);
  assert.ok(Math.abs(centre.uv[1] - 0.5) < 1e-6);

  assert.equal(verifySpatialMapping(mapping, {
    mapping_artifact_id: "map-a-v2",
    proxy_artifact_id: "proxy-a-v2",
    version_id: "a-v2",
    sample_frame: 1,
    object_id: "collar",
    instance_id: "collar-1",
  }, "a".repeat(64)), null);
  assert.match(verifySpatialMapping(mapping, {
    mapping_artifact_id: "other-map",
    proxy_artifact_id: "proxy-a-v2",
    version_id: "a-v2",
    sample_frame: 1,
    object_id: "collar",
    instance_id: "collar-1",
  }, "a".repeat(64))!, /Mapping artefact mismatch/);
});

test("annotationContext preserves 3d_inspection and selected_objects on valid spatial hits and strips them on image-only fallback", () => {
  const cam = new PerspectiveCamera(50, 16 / 9, 0.1, 100);
  cam.position.set(0, 2, 6);
  cam.lookAt(0, 0, 0);
  cam.updateProjectionMatrix();
  cam.updateMatrixWorld();
  const camera = captureCamera(cam);
  const valid: AnnotationSnapshot = {
    annotation_id: "ann-spatial",
    saved_revision: 1,
    target: {
      origin: "proxy_capture",
      artifact_id: "clean-1",
      displayed_sha256: "b".repeat(64),
      dimensions: { width: 1600, height: 900 },
      item_id: "asset-a",
      version_id: "a-v2",
      shot_id: null,
      render_job_id: null,
      source_frame: 1,
      media_artifact_id: null,
    },
    range: { start: 1, end: 1 },
    camera,
    markups: [{ id: "pin-1", kind: "pin", points: [[0.5, 0.5]], text: "Chamfer lip" }],
    text: "Chamfer lip by 2 mm",
    spatial_hits: [{
      proxy_artifact_id: "proxy-a-v2",
      mapping_artifact_id: "map-a-v2",
      version_id: "a-v2",
      sample_frame: 1,
      object_id: "collar",
      instance_id: "collar-1",
      space: "blender_world_m",
      position: [0, 0, 0],
      normal: [0, -1, 0],
      camera,
    }],
    spatial_status: "valid",
    markup_artifact_id: "marked-1",
  };
  const validCtx = annotationContext(valid);
  assert.equal(validCtx.view_mode, "3d_inspection");
  assert.deepEqual(validCtx.selected_objects, [{ object_id: "collar", instance_id: "collar-1" }]);
  assert.deepEqual(validCtx.request_target, { kind: "item", version: { item_id: "asset-a", version_id: "a-v2" } });

  const fallbackCtx = annotationContext({ ...valid, spatial_hits: [], spatial_status: "unavailable", derived_from: "ann-spatial" });
  assert.equal(fallbackCtx.view_mode, "keyframes");
  assert.deepEqual(fallbackCtx.selected_objects, []);
  assert.equal(fallbackCtx.version_id, "a-v2");
});
