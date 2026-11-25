import { Vector3, type OrthographicCamera, type PerspectiveCamera } from "three";

type InspectionCamera = PerspectiveCamera | OrthographicCamera;

export function computeNavigationStep(orbitDistance: number, sceneRadius: number): number {
  const distance = Number.isFinite(orbitDistance) ? Math.max(0, orbitDistance) : 0;
  const radius = Number.isFinite(sceneRadius) ? Math.max(0, sceneRadius) : 0;
  const raw = Math.max(distance * 0.12, radius * 0.025, 0.05);
  const cap = Math.max(radius * 0.5, 5);
  return Math.min(raw, cap);
}

export function normalizeWheelNotches(deltaY: number, deltaX: number, deltaMode: number): number {
  const raw = Math.abs(deltaY) >= Math.abs(deltaX) ? deltaY : deltaX;
  if (!Number.isFinite(raw) || raw === 0) return 0;
  const scaled = deltaMode === 1 ? raw / 3 : deltaMode === 2 ? raw * 2 : raw / 100;
  return -Math.max(-3, Math.min(3, scaled));
}

export function keyboardMoveDirection(event: { code?: string; key: string; shiftKey: boolean; ctrlKey: boolean; metaKey: boolean; altKey: boolean }): { forward: number; right: number; up: number; boost: number } | null {
  if (event.ctrlKey || event.metaKey || event.altKey) return null;
  let forward = 0, right = 0, up = 0;
  switch (event.code || event.key.toLowerCase()) {
    case "KeyW": case "w": forward = 1; break;
    case "KeyS": case "s": forward = -1; break;
    case "KeyA": case "a": right = -1; break;
    case "KeyD": case "d": right = 1; break;
    case "KeyE": case "e": up = 1; break;
    case "KeyQ": case "q": up = -1; break;
    default: return null;
  }
  return { forward, right, up, boost: event.shiftKey ? 3 : 1 };
}

export function translateCameraAndTarget(camera: InspectionCamera, target: Vector3, offset: { forward?: number; right?: number; up?: number }): boolean {
  camera.updateMatrixWorld();
  const forward = camera.getWorldDirection(new Vector3());
  const right = new Vector3(1, 0, 0).applyQuaternion(camera.quaternion).normalize();
  const up = new Vector3(0, 1, 0).applyQuaternion(camera.quaternion).normalize();
  const delta = new Vector3().addScaledVector(forward, offset.forward ?? 0)
    .addScaledVector(right, offset.right ?? 0).addScaledVector(up, offset.up ?? 0);
  if (!Number.isFinite(delta.x + delta.y + delta.z) || delta.lengthSq() === 0) return false;
  camera.position.add(delta);
  target.add(delta);
  return true;
}

export function recenterOrbitTarget(camera: InspectionCamera, target: Vector3, hitPoint: Vector3): boolean {
  if (!Number.isFinite(hitPoint.x + hitPoint.y + hitPoint.z) || camera.position.distanceTo(hitPoint) <= 1e-4) return false;
  target.copy(hitPoint);
  return true;
}

export function isTextInputTarget(target: EventTarget | null): boolean {
  if (typeof Element === "undefined" || !(target instanceof Element)) return false;
  return (typeof HTMLElement !== "undefined" && target instanceof HTMLElement && target.isContentEditable)
    || Boolean(target.closest("input,textarea,select,[contenteditable=''],[contenteditable='true'],[contenteditable='plaintext-only']"));
}
