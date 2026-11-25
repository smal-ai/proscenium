import { PerspectiveCamera, Sphere, Vector3, type Box3, type OrthographicCamera } from "three";
import { captureCamera } from "./proxyCamera";
import { computeFramingGate, type Dimensions } from "./renderFraming";

const PROJECTION_TOLERANCE = 1e-5;

export function computeAutoFramePose(box: Box3, fovDegrees: number, aspect = 1) {
  if (box.isEmpty()) return null;
  const center = box.getCenter(new Vector3());
  const radius = Math.max(box.getBoundingSphere(new Sphere()).radius, 0.05);
  const vHalf = (fovDegrees * Math.PI) / 360;
  const safeAspect = Number.isFinite(aspect) && aspect > 0 ? aspect : 1;
  const hHalf = Math.atan(Math.tan(vHalf) * safeAspect);
  const halfAngle = Math.min(vHalf, hHalf);
  const distance = (radius / Math.sin(halfAngle)) * 1.35;
  const position = new Vector3(5, 4, 5).normalize().multiplyScalar(distance).add(center);
  return {
    position: position.toArray(), target: center.toArray(),
    near: Math.max(0.001, Math.min(0.01, distance * 0.01)),
    far: Math.max(1000, distance + radius * 50),
  };
}

function hasSquarePixels(projection: readonly number[], aspect: number) {
  return Math.abs(projection[0] * aspect - projection[5]) <= PROJECTION_TOLERANCE;
}

export function deriveFramedCamera(
  liveCamera: PerspectiveCamera | OrthographicCamera,
  viewportWidth: number,
  viewportHeight: number,
  targetDimensions: Dimensions,
): PerspectiveCamera | OrthographicCamera {
  const gate = computeFramingGate(viewportWidth, viewportHeight, targetDimensions);
  if (!gate.active) throw new RangeError("Framing requires positive viewport and target dimensions");

  const viewportAspect = viewportWidth / viewportHeight;
  const base = liveCamera.clone();
  base.updateProjectionMatrix();
  // Keep a sub-view whose original optics already match the canvas, including
  // unequal crop fractions shown at the sub-view's own aspect ratio.
  if (!base.view?.enabled || !hasSquarePixels(base.projectionMatrix.elements, viewportAspect)) {
    // Otherwise match the native capture's aspect correction on the clone.
    if (base instanceof PerspectiveCamera) base.aspect = viewportAspect;
    else {
      const centre = (base.left + base.right) / 2;
      const halfWidth = (base.top - base.bottom) * viewportAspect / 2;
      base.left = centre - halfWidth;
      base.right = centre + halfWidth;
    }
    base.updateProjectionMatrix();
  }
  const projection = base.projectionMatrix.elements;
  if (!hasSquarePixels(projection, viewportAspect)) {
    throw new RangeError("Anisotropic camera sub-view cannot be reproduced with square-pixel Blender optics");
  }
  base.updateMatrixWorld();

  const scaleX = gate.width / viewportWidth;
  const scaleY = gate.height / viewportHeight;
  const targetAspect = targetDimensions.width / targetDimensions.height;
  const { width, height } = targetDimensions;

  let framed: PerspectiveCamera | OrthographicCamera;
  if (base instanceof PerspectiveCamera) {
    framed = base.clone();
    // Fold magnification into FOV and keep the view offset for shift only.
    const tanHalfV = scaleY * base.zoom / projection[5];
    framed.fov = (Math.atan(tanHalfV) * 360) / Math.PI;
    framed.aspect = targetAspect;
    const shiftX = projection[8] / scaleX;
    const shiftY = projection[9] / scaleY;
    framed.filmOffset = 0;
    if (shiftX || shiftY) {
      framed.setViewOffset(width, height, shiftX * width / 2, -shiftY * height / 2, width, height);
    } else framed.clearViewOffset();
  } else {
    framed = base.clone();
    const vCentre = -projection[13] / projection[5];
    const hCentre = -projection[12] / projection[0];
    const halfH = scaleY * base.zoom / projection[5];
    const halfW = halfH * targetAspect;
    framed.top = vCentre + halfH;
    framed.bottom = vCentre - halfH;
    framed.left = hCentre - halfW;
    framed.right = hCentre + halfW;
    framed.clearViewOffset();
  }
  framed.updateProjectionMatrix();
  // Blender sets vertical optics from the wire record and solves only the
  // shift terms. Its vertical and horizontal scales must already match.
  const record = captureCamera(framed);
  const verticalScale = record.kind === "perspective"
    ? 1 / Math.tan(record.vertical_fov_degrees! * Math.PI / 360)
    : 2 / record.orthographic_height_m!;
  if (!(Math.abs(record.projection[0] - record.projection[5] / targetAspect) <= PROJECTION_TOLERANCE &&
    Math.abs(record.projection[0] - verticalScale / targetAspect) <= PROJECTION_TOLERANCE &&
    Math.abs(record.projection[5] - verticalScale) <= PROJECTION_TOLERANCE &&
    Math.abs(record.aspect - targetAspect) <= 1e-6)) {
    throw new RangeError("Framed camera projection cannot be reproduced with square-pixel Blender optics");
  }
  return framed;
}
