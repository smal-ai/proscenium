import { Matrix4, PerspectiveCamera, type OrthographicCamera } from "three";
import type { Camera } from "../../api/generated/contracts";

/** World basis only. Camera-local optical axes do not undergo an object conjugation. */
export function captureCamera(camera: PerspectiveCamera | OrthographicCamera): Camera {
  camera.updateMatrixWorld();
  const proxyToBlender = new Matrix4().set(1,0,0,0, 0,0,-1,0, 0,1,0,0, 0,0,0,1);
  const world = proxyToBlender.multiply(camera.matrixWorld);
  const rowMajor = (m: Matrix4) => m.clone().transpose().toArray();
  const projection = rowMajor(camera.projectionMatrix);
  const common = {space: "blender_world_m" as const, camera_to_world: rowMajor(world), projection,
    aspect: camera instanceof PerspectiveCamera ? camera.aspect : (camera.right-camera.left)/(camera.top-camera.bottom),
    near_m: camera.near, far_m: camera.far, source_camera_id: null,
    shift: (camera instanceof PerspectiveCamera ? [-projection[2] / 2, projection[6] / 2] : [projection[3] / 2, -projection[7] / 2]) as [number, number]};
  return camera instanceof PerspectiveCamera
    ? {...common, kind: "perspective", vertical_fov_degrees: camera.getEffectiveFOV()}
    : {...common, kind: "orthographic", orthographic_height_m: (camera.top-camera.bottom)/camera.zoom};
}
