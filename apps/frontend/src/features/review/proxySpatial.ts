import { Matrix3, Vector3, type Object3D } from "three";
import type { ProxyMapping } from "../../api/generated/contracts";

export type ProxyNode = ProxyMapping["nodes"][number];

function authoredPair(object: Object3D): [string, string] | null {
  const objectId = object.userData?.proscenium_object_id;
  const instanceId = object.userData?.proscenium_instance_id;
  if (typeof objectId !== "string" || !objectId || typeof instanceId !== "string" || !instanceId) return null;
  return [objectId, instanceId];
}

function sameAuthoredParent(object: Object3D, pair: [string, string]): boolean {
  if (!object.parent) return false;
  const parentPair = authoredPair(object.parent);
  return !!parentPair && parentPair[0] === pair[0] && parentPair[1] === pair[1];
}

/**
 * Index authored glTF nodes against ProxyMapping.nodes without using glTF child
 * indices (GLTFLoader prepends primitive Mesh children on multi-primitive meshes).
 * Any missing or duplicate (object_id, instance_id) pair is omitted rather than guessed.
 */
export function indexProxyScene(scene: Object3D, mapping: ProxyMapping): Map<Object3D, ProxyNode> {
  const mapByKey = new Map<string, ProxyNode>();
  const duplicateMapKeys = new Set<string>();
  for (const node of mapping.nodes) {
    const key = `${node.object_id}\0${node.instance_id}`;
    if (mapByKey.has(key)) duplicateMapKeys.add(key);
    else mapByKey.set(key, node);
  }
  const sceneByKey = new Map<string, Object3D[]>();
  scene.traverse(object => {
    const pair = authoredPair(object);
    if (!pair || sameAuthoredParent(object, pair)) return;
    const key = `${pair[0]}\0${pair[1]}`;
    const bucket = sceneByKey.get(key) ?? [];
    bucket.push(object);
    sceneByKey.set(key, bucket);
  });
  const resolved = new Map<Object3D, ProxyNode>();
  for (const [key, objects] of sceneByKey.entries()) {
    if (objects.length !== 1 || duplicateMapKeys.has(key)) continue;
    const mapped = mapByKey.get(key);
    if (mapped) resolved.set(objects[0], mapped);
  }
  return resolved;
}

/**
 * Resolve a raycast intersection object to its nearest authored glTF node.
 * Never climbs past an unmapped or ambiguous authored child onto an ancestor.
 */
export function resolveProxyNode(object: Object3D | null, index: ReadonlyMap<Object3D, ProxyNode>): ProxyNode | null {
  let current: Object3D | null = object;
  while (current) {
    const match = index.get(current);
    if (match) return match;
    const pair = authoredPair(current);
    if (pair && !sameAuthoredParent(current, pair)) return null;
    current = current.parent;
  }
  return null;
}

/** Proxy P(x, y, z) -> Blender world B(x, -z, y) in metres. */
export function proxyPointToBlender(point: Vector3 | readonly number[]): [number, number, number] {
  const [px, py, pz] = point instanceof Vector3 ? [point.x, point.y, point.z] : [point[0], point[1], point[2]];
  const result: [number, number, number] = [px, -pz, py];
  if (!result.every(Number.isFinite)) throw new Error("Non-finite proxy surface intersection");
  return result;
}

/**
 * Transform an object-local face/vertex normal via hitObject's inverse-transpose
 * normal matrix, orient toward the incoming world ray, convert to Blender world
 * basis, and normalise to unit length.
 */
export function proxySurfaceNormalToBlender(
  hitObject: Object3D,
  localNormal: Vector3,
  rayDirectionWorld?: Vector3,
): [number, number, number] {
  hitObject.updateWorldMatrix(true, false);
  const normalMatrix = new Matrix3().getNormalMatrix(hitObject.matrixWorld);
  const proxyNormal = localNormal.clone().applyMatrix3(normalMatrix);
  if (!Number.isFinite(proxyNormal.x) || !Number.isFinite(proxyNormal.y) || !Number.isFinite(proxyNormal.z) || proxyNormal.lengthSq() <= 1e-12) {
    throw new Error("Degenerate surface normal at intersection");
  }
  proxyNormal.normalize();
  if (rayDirectionWorld && proxyNormal.dot(rayDirectionWorld) > 0) proxyNormal.negate();
  const blenderNormal = new Vector3(proxyNormal.x, -proxyNormal.z, proxyNormal.y).normalize();
  const result: [number, number, number] = [blenderNormal.x, blenderNormal.y, blenderNormal.z];
  const lengthSq = result[0] * result[0] + result[1] * result[1] + result[2] * result[2];
  if (!result.every(Number.isFinite) || Math.abs(lengthSq - 1) > 1e-4) {
    throw new Error("Surface normal failed unit-length verification");
  }
  return result;
}
