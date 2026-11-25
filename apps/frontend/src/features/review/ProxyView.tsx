import { Suspense, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type ComponentRef, type RefObject } from "react";
import { Canvas, useFrame, useThree, type RootState, type ThreeEvent } from "@react-three/fiber";
import { OrbitControls, useGLTF } from "@react-three/drei";
import { Box3, Matrix4, Mesh, OrthographicCamera, PerspectiveCamera, Raycaster, Sphere, Vector2, Vector3, type Camera as ThreeCamera, type Material, type Object3D, type Scene, type WebGLRenderer } from "three";
import type { Camera, ObjectSelection, ProxyMapping } from "../../api/generated/contracts";
import { cloneInspectionMaterial } from "./inspectionMaterials";
import { captureCamera, projectBlenderPoint } from "./media";
import { computeNavigationStep, isPointWithinCameraClip, isTextInputTarget, keyboardMoveDirection, normalizeWheelNotches, recenterOrbitTarget, translateCameraAndTarget } from "./proxyNavigation";
import { computeAutoFramePose, computeCanvasFramingGate, deriveFramedCamera, formatRenderPresetOption, resolveRenderTargetDimensions, type RenderPresetId } from "./renderFraming";
import { indexProxyScene, projectBlenderPointVisible, proxyPointToBlender, proxySurfaceNormalToBlender, resolveProxyNode, type ProxyNode } from "./spatial";
import "./renderFraming.css";

const DRAG_THRESHOLD_PX = 4;
const PROXY_FOV = 50;

export interface FrozenProxyCapture {
  camera: Camera;
  dimensions: {width: number; height: number};
  png: Promise<Blob>;
}
export interface ProxyCaptureOptions {
  targetDimensions?: {width: number; height: number} | null;
  opaqueBackdrop?: boolean;
}
export type ProxyCapture = (options?: ProxyCaptureOptions) => FrozenProxyCapture;

export interface ProxyPinPlacement {
  node: ProxyNode;
  position: [number, number, number];
  normal: [number, number, number];
  pinUv: [number, number];
  capture: FrozenProxyCapture;
}

export interface SpatialPinMarker {
  id: string;
  position: readonly number[];
  instanceId: string;
  label: string;
}

function toBlobPromise(copy: HTMLCanvasElement): Promise<Blob> {
  return new Promise<Blob>((resolve, reject) => {
    copy.toBlob(blob => blob ? resolve(blob) : reject(new Error("Inspection PNG capture failed")), "image/png");
  });
}

function copyViewport(context: CanvasRenderingContext2D, source: HTMLCanvasElement, opaqueBackdrop?: boolean) {
  // The CSS vignette is not part of the WebGL buffer. Give render references a
  // slate matte without changing the renderer or the clean pin captures.
  if (opaqueBackdrop) {
    context.fillStyle = "#23242d";
    context.fillRect(0, 0, source.width, source.height);
  }
  context.drawImage(source, 0, 0);
}

function captureCleanViewport(gl: WebGLRenderer, scene: Scene, camera: ThreeCamera, options?: ProxyCaptureOptions): FrozenProxyCapture {
  if (!(camera instanceof PerspectiveCamera || camera instanceof OrthographicCamera)) {
    throw new Error("Unsupported inspection camera");
  }
  if (options?.targetDimensions) {
    const {width: targetW, height: targetH} = options.targetDimensions;
    const webgl = gl.getContext();
    if (!Number.isInteger(targetW) || !Number.isInteger(targetH) || targetW <= 0 || targetH <= 0 || webgl.isContextLost()) {
      throw new Error("Inspection framebuffer is unavailable at the requested dimensions");
    }
    const prevPixelRatio = gl.getPixelRatio();
    // R3F sizes the renderer from the fitted Canvas, not the overlay host.
    const prevCssSize = gl.getSize(new Vector2());
    const dimensions = {width: targetW, height: targetH};
    const framedCam = deriveFramedCamera(camera,
      prevCssSize.x || gl.domElement.width / prevPixelRatio,
      prevCssSize.y || gl.domElement.height / prevPixelRatio,
      dimensions,
    );
    const frozenCamera = captureCamera(framedCam);
    const copy = document.createElement("canvas"); copy.width = targetW; copy.height = targetH;
    const context = copy.getContext("2d");
    if (!context) throw new Error("PNG capture is unavailable");
    // Keep resize, copy and restore in one turn, before the compositor can paint.
    try {
      gl.setPixelRatio(1);
      gl.setSize(targetW, targetH, false);
      gl.setViewport(0, 0, targetW, targetH);
      gl.render(scene, framedCam);
      if (gl.domElement.width !== targetW || gl.domElement.height !== targetH) {
        throw new Error(`Inspection framebuffer clamped to ${gl.domElement.width}×${gl.domElement.height}; requested ${targetW}×${targetH}`);
      }
      if (webgl.drawingBufferWidth !== targetW || webgl.drawingBufferHeight !== targetH) {
        throw new Error(`Inspection framebuffer clamped to ${webgl.drawingBufferWidth}×${webgl.drawingBufferHeight}; requested ${targetW}×${targetH}`);
      }
      copyViewport(context, gl.domElement, options.opaqueBackdrop);
    } finally {
      gl.setPixelRatio(prevPixelRatio);
      gl.setSize(prevCssSize.x, prevCssSize.y, false);
      gl.setViewport(0, 0, prevCssSize.x, prevCssSize.y);
      gl.render(scene, camera);
    }
    return {camera: frozenCamera, dimensions, png: toBlobPromise(copy)};
  }
  const {width, height} = gl.domElement;
  if (!width || !height || gl.getContext().isContextLost()) {
    throw new Error("Inspection framebuffer is unavailable");
  }
  // Fractional CSS bounds and DPR can round to a different intrinsic aspect.
  // Make the projection authoritative for these exact output pixels.
  if (camera instanceof PerspectiveCamera) camera.aspect = width / height;
  else {
    const centre = (camera.left + camera.right) / 2;
    const halfWidth = (camera.top - camera.bottom) * width / height / 2;
    camera.left = centre - halfWidth; camera.right = centre + halfWidth;
  }
  camera.updateProjectionMatrix();
  camera.updateMatrixWorld();
  const frozenCamera = captureCamera(camera);
  // Explicitly render and synchronously copy actual pixels before WebGL's
  // default drawing buffer is discarded. Never redraw with 2D/fake lighting.
  const dpr = gl.getPixelRatio();
  gl.setViewport(0, 0, width / dpr, height / dpr);
  gl.render(scene, camera);
  const copy = document.createElement("canvas"); copy.width = width; copy.height = height;
  const context = copy.getContext("2d");
  if (!context) throw new Error("PNG capture is unavailable");
  copyViewport(context, gl.domElement, options?.opaqueBackdrop);
  return {
    camera: frozenCamera,
    dimensions: {width, height},
    png: toBlobPromise(copy),
  };
}

function CameraObserver({onCamera}: {onCamera: (camera: Camera) => void}) {
  const previous = useRef("");
  useFrame(({camera}) => {
    if (camera instanceof PerspectiveCamera || camera instanceof OrthographicCamera) {
      const capture = captureCamera(camera); const key = JSON.stringify(capture);
      if (key !== previous.current) {previous.current = key; onCamera(capture);}
    }
  });
  return null;
}

function Model({
  url,
  mapping,
  pinMode,
  onSelect,
  onSceneReady,
}: {
  url: string;
  mapping: ProxyMapping;
  pinMode: boolean;
  onSelect: (selection: ObjectSelection[]) => void;
  onSceneReady: (value: {scene: Object3D; nodes: ReadonlyMap<Object3D, ProxyNode>} | null) => void;
}) {
  const gltf = useGLTF(url);
  const {scene, materials} = useMemo(() => {
    const scene = gltf.scene.clone(true);
    const materials = new Map<Material, Material>();
    const clone = (source: Material) => {
      const existing = materials.get(source);
      if (existing) return existing;
      const material = cloneInspectionMaterial(source);
      materials.set(source, material);
      return material;
    };
    scene.traverse(object => {
      if (object instanceof Mesh) {
        object.material = Array.isArray(object.material) ? object.material.map(clone) : clone(object.material);
      }
    });
    return {scene, materials};
  }, [gltf.scene]);
  useEffect(() => () => materials.forEach(material => material.dispose()), [materials]);
  const nodes = useMemo(() => indexProxyScene(scene, mapping), [mapping, scene]);
  useLayoutEffect(() => {
    onSceneReady({scene, nodes});
    return () => onSceneReady(null);
  }, [nodes, onSceneReady, scene]);
  function pick(event: ThreeEvent<MouseEvent>) {
    if (pinMode || event.delta > DRAG_THRESHOLD_PX) return;
    // A draw-instance index is not a stable source instance ID. Require explicit authored nodes.
    if (event.instanceId !== undefined) return;
    const node = resolveProxyNode(event.object, nodes);
    if (!node) return;
    event.stopPropagation();
    onSelect([{object_id: node.object_id, instance_id: node.instance_id}]);
  }
  return <primitive object={scene} onClick={pick}/>;
}

function ProxyDepthNavigationController({ enabled, pinMode, model, sceneRadius, controls, onCamera }: {
  enabled: boolean;
  pinMode: boolean;
  model: {scene: Object3D} | null;
  sceneRadius: number;
  controls: RefObject<ComponentRef<typeof OrbitControls> | null>;
  onCamera: (camera: Camera) => void;
}) {
  const { gl, camera } = useThree();
  const suppressDblclick = useRef(false);
  const state = useRef({ enabled, pinMode, model, sceneRadius, onCamera });
  state.current = { enabled, pinMode, model, sceneRadius, onCamera };

  useEffect(() => {
    if (!(camera instanceof PerspectiveCamera || camera instanceof OrthographicCamera)) return;
    const inspectionCamera = camera;
    const dom = gl.domElement;
    const publish = (orbit: ComponentRef<typeof OrbitControls>) => {
      orbit.update();
      inspectionCamera.updateWorldMatrix(true, false);
      state.current.onCamera(captureCamera(inspectionCamera));
    };
    const step = (target: Vector3) => {
      inspectionCamera.updateWorldMatrix(true, false);
      return computeNavigationStep(inspectionCamera.getWorldPosition(new Vector3()).distanceTo(target), state.current.sceneRadius);
    };

    function wheel(event: WheelEvent) {
      if (!state.current.enabled || !event.shiftKey || event.ctrlKey || event.metaKey || event.altKey) return;
      const orbit = controls.current;
      if (orbit?.object !== inspectionCamera) return;
      event.preventDefault();
      event.stopImmediatePropagation();
      const notches = normalizeWheelNotches(event.deltaY, event.deltaX, event.deltaMode);
      if (notches !== 0 && translateCameraAndTarget(inspectionCamera, orbit.target, { forward: notches * step(orbit.target) })) publish(orbit);
    }

    function keydown(event: KeyboardEvent) {
      if (!state.current.enabled || event.defaultPrevented || isTextInputTarget(event.target)) return;
      const move = keyboardMoveDirection(event);
      const orbit = controls.current;
      if (!move || orbit?.object !== inspectionCamera) return;
      event.preventDefault();
      const distance = step(orbit.target) * 0.75 * move.boost;
      if (translateCameraAndTarget(inspectionCamera, orbit.target, { forward: move.forward * distance, right: move.right * distance, up: move.up * distance })) publish(orbit);
    }

    function mousedown(event: MouseEvent) {
      if (event.button !== 0) return;
      if (state.current.pinMode) suppressDblclick.current = true;
      else if (event.detail <= 1) suppressDblclick.current = false;
    }

    function recenter(event: MouseEvent) {
      if (suppressDblclick.current) { suppressDblclick.current = false; return; }
      const current = state.current;
      if (!current.enabled || current.pinMode || !current.model || event.button !== 0) return;
      const orbit = controls.current;
      if (orbit?.object !== inspectionCamera) return;
      const rect = dom.getBoundingClientRect();
      if (rect.width <= 0 || rect.height <= 0) return;
      const u = (event.clientX - rect.left) / rect.width;
      const v = (event.clientY - rect.top) / rect.height;
      if (u < 0 || u > 1 || v < 0 || v > 1) return;
      inspectionCamera.updateWorldMatrix(true, false);
      current.model.scene.updateMatrixWorld(true);
      const raycaster = new Raycaster();
      raycaster.setFromCamera(new Vector2(u * 2 - 1, 1 - v * 2), inspectionCamera);
      const hit = raycaster.intersectObject(current.model.scene, true)
        .find(entry => entry.instanceId === undefined && isPointWithinCameraClip(inspectionCamera, entry.point));
      if (hit && recenterOrbitTarget(inspectionCamera, orbit.target, hit.point)) publish(orbit);
    }

    dom.addEventListener("wheel", wheel, { passive: false, capture: true });
    window.addEventListener("keydown", keydown);
    dom.addEventListener("mousedown", mousedown, true);
    dom.addEventListener("dblclick", recenter);
    return () => {
      dom.removeEventListener("wheel", wheel, true);
      window.removeEventListener("keydown", keydown);
      dom.removeEventListener("mousedown", mousedown, true);
      dom.removeEventListener("dblclick", recenter);
    };
  }, [camera, controls, gl]);
  return null;
}

function SurfacePinController({
  enabled,
  model,
  onSelect,
  onPlacePin,
  onPinMiss,
}: {
  enabled: boolean;
  model: {scene: Object3D; nodes: ReadonlyMap<Object3D, ProxyNode>} | null;
  onSelect: (selection: ObjectSelection[]) => void;
  onPlacePin?: (placement: ProxyPinPlacement) => void;
  onPinMiss?: (reason: "empty" | "unmapped", fallback?: FrozenProxyCapture & {clickUv: [number, number]}) => void;
}) {
  const {gl, scene, camera} = useThree();
  const stateRef = useRef({enabled, model, onSelect, onPlacePin, onPinMiss});
  stateRef.current = {enabled, model, onSelect, onPlacePin, onPinMiss};

  useEffect(() => {
    const dom = gl.domElement;
    let gesture: {pointerId: number; startX: number; startY: number; moved: boolean} | null = null;
    function down(event: PointerEvent) {
      if (!stateRef.current.enabled || event.button !== 0) return;
      gesture = {pointerId: event.pointerId, startX: event.clientX, startY: event.clientY, moved: false};
    }
    function move(event: PointerEvent) {
      if (!gesture || event.pointerId !== gesture.pointerId) return;
      if (Math.hypot(event.clientX - gesture.startX, event.clientY - gesture.startY) > DRAG_THRESHOLD_PX) {
        gesture.moved = true;
      }
    }
    function cancel(event: PointerEvent) {
      if (gesture && event.pointerId === gesture.pointerId) gesture = null;
    }
    function up(event: PointerEvent) {
      if (!gesture || event.pointerId !== gesture.pointerId || event.button !== 0) return;
      const moved = gesture.moved;
      gesture = null;
      const current = stateRef.current;
      if (moved || !current.enabled || !current.model) return;
      const rect = dom.getBoundingClientRect();
      if (rect.width <= 0 || rect.height <= 0) return;
      const u = (event.clientX - rect.left) / rect.width;
      const v = (event.clientY - rect.top) / rect.height;
      if (u < 0 || u > 1 || v < 0 || v > 1) return;
      const captured = captureCleanViewport(gl, scene, camera);
      current.model.scene.updateMatrixWorld(true);
      const raycaster = new Raycaster();
      raycaster.setFromCamera(new Vector2(u * 2 - 1, 1 - v * 2), camera);
      const hits = raycaster.intersectObject(current.model.scene, true);
      const hit = hits.find(entry => entry.instanceId === undefined);
      if (!hit) {
        current.onPinMiss?.("empty");
        return;
      }
      const node = resolveProxyNode(hit.object, current.model.nodes);
      const localNormal = hit.face && hit.face.normal.lengthSq() > 1e-12 ? hit.face.normal : hit.normal;
      if (!node || !localNormal) {
        current.onPinMiss?.("unmapped", {...captured, clickUv: [u, v]});
        return;
      }
      const position = proxyPointToBlender(hit.point);
      const normal = proxySurfaceNormalToBlender(hit.object, localNormal, raycaster.ray.direction);
      const pinUv = projectBlenderPoint(captured.camera, position);
      current.onSelect([{object_id: node.object_id, instance_id: node.instance_id}]);
      current.onPlacePin?.({node, position, normal, pinUv, capture: captured});
    }
    dom.addEventListener("pointerdown", down);
    dom.addEventListener("pointermove", move);
    dom.addEventListener("pointercancel", cancel);
    dom.addEventListener("pointerup", up);
    return () => {
      dom.removeEventListener("pointerdown", down);
      dom.removeEventListener("pointermove", move);
      dom.removeEventListener("pointercancel", cancel);
      dom.removeEventListener("pointerup", up);
    };
  }, [camera, gl, scene]);
  return null;
}

function RestoreCamera({record, nonce = 0, controls}: {record: Camera | null; nonce?: number; controls: RefObject<ComponentRef<typeof OrbitControls> | null>}) {
  const {camera, set} = useThree();
  const applied = useRef<{record: Camera; nonce: number; camera: PerspectiveCamera | OrthographicCamera} | null>(null);
  useLayoutEffect(() => {
    if (!record) return;
    if (applied.current?.record !== record || applied.current.nonce !== nonce) {
      const restored = record.kind === "perspective"
        ? new PerspectiveCamera(record.vertical_fov_degrees!, record.aspect, record.near_m, record.far_m)
        : new OrthographicCamera(-record.orthographic_height_m! * record.aspect / 2, record.orthographic_height_m! * record.aspect / 2, record.orthographic_height_m! / 2, -record.orthographic_height_m! / 2, record.near_m, record.far_m);
      if (restored instanceof PerspectiveCamera && (record.projection[2] || record.projection[6])) {
        const height=1e6,width=height*record.aspect;
        restored.setViewOffset(width,height,record.projection[2]*width/2,-record.projection[6]*height/2,width,height);
      } else if (restored instanceof OrthographicCamera) {
        const width=restored.right-restored.left,height=restored.top-restored.bottom;
        const x=-record.projection[3]*width/2,y=-record.projection[7]*height/2;
        restored.left+=x;restored.right+=x;restored.bottom+=y;restored.top+=y;
      }
      Object.assign(restored, {manual: true}); // R3F must not silently replace the saved projection on resize.
      applied.current = {record, nonce, camera: restored};
      set({camera: restored});
      return;
    }
    if (camera !== applied.current.camera) return;
    const restored = applied.current.camera;
    const blenderToProxy = new Matrix4().set(1,0,0,0, 0,0,1,0, 0,-1,0,0, 0,0,0,1);
    const pose = blenderToProxy.multiply(new Matrix4().fromArray(record.camera_to_world).transpose());
    pose.decompose(restored.position, restored.quaternion, new Vector3());
    restored.up.set(0,1,0).applyQuaternion(restored.quaternion);
    const orbit = controls.current;
    if (orbit?.object === restored) {
      // The record has no authored orbit pivot. A forward control target preserves
      // its pose, and is never represented as a source object or spatial hit.
      orbit.target.copy(restored.position).add(new Vector3(0,0,-1).applyQuaternion(restored.quaternion).multiplyScalar(Math.max(restored.position.length(),1)));
      orbit.update();
    }
    restored.projectionMatrix.fromArray(record.projection).transpose();
    restored.projectionMatrixInverse.copy(restored.projectionMatrix).invert();
    restored.updateMatrixWorld();
  }, [record, nonce, camera, controls, set]);
  return null;
}

function AutoFrameCamera({model, frameKey, restoreCamera, controls, onCamera, onFramed}: {
  model: Object3D | null;
  frameKey: string;
  restoreCamera: Camera | null;
  controls: RefObject<ComponentRef<typeof OrbitControls> | null>;
  onCamera: (camera: Camera) => void;
  onFramed: (frameKey: string | null) => void;
}) {
  const {camera, set, size} = useThree();
  const framedKey = useRef<string | null>(null);
  useLayoutEffect(() => {
    if (restoreCamera || !model) {
      framedKey.current = null;
      onFramed(null);
      return;
    }
    if (framedKey.current === frameKey || size.width <= 0 || size.height <= 0) return;
    const aspect = size.width / size.height;
    if (!(camera instanceof PerspectiveCamera) || ("manual" in camera && camera.manual)) {
      // Frame model exits a saved camera's fixed projection and letterbox.
      set({camera: new PerspectiveCamera(PROXY_FOV, aspect)});
      return;
    }
    const orbit = controls.current;
    if (orbit?.object !== camera) return;
    const pose = computeAutoFramePose(new Box3().setFromObject(model), camera.fov, aspect);
    if (!pose) return;
    camera.position.set(...pose.position);
    camera.near = pose.near;
    camera.far = pose.far;
    camera.aspect = aspect;
    camera.updateProjectionMatrix();
    orbit.target.set(...pose.target);
    orbit.update();
    camera.updateMatrixWorld();
    framedKey.current = frameKey;
    onCamera(captureCamera(camera));
    onFramed(frameKey);
  }, [camera, controls, frameKey, model, onCamera, onFramed, restoreCamera, set, size.width, size.height]);
  return null;
}

type ViewportPixels = {width: number; height: number};

function CaptureBinding({ready, bind, onViewportPixels}: {
  ready: boolean;
  bind: (capture: ProxyCapture | null) => void;
  onViewportPixels?: (pixels: ViewportPixels) => void;
}) {
  const {gl, scene, camera, size} = useThree();
  const available = useRef(ready); available.current = ready;
  const previousPixels = useRef<ViewportPixels | null>(null);
  useFrame(() => {
    if (!onViewportPixels) return;
    // Read the intrinsic buffer after R3F applies CSS size and DPR, never while
    // the synchronous target-resolution capture has temporarily resized it.
    const {width, height} = gl.domElement;
    if (width > 0 && height > 0 && (width !== previousPixels.current?.width || height !== previousPixels.current?.height)) {
      previousPixels.current = {width, height};
      onViewportPixels(previousPixels.current);
    }
  });
  useLayoutEffect(() => {
    if (!ready || !size.width || !size.height || !gl.domElement.width || !gl.domElement.height) { bind(null); return; }
    bind(options => {
      if (!available.current) throw new Error("Inspection capture requires a visible, ready viewport");
      return captureCleanViewport(gl, scene, camera, options);
    });
    return () => bind(null);
  }, [ready, bind, camera, gl, scene, size.width, size.height]);
  return null;
}

export function ProxyView({
  visible = true,
  url,
  mapping,
  pinMode = false,
  pins = [],
  onSelect,
  onCamera,
  onCaptureReady,
  renderPreset = "viewport",
  suppressFramingGate = false,
  onViewportPixels,
  onPlacePin,
  onPinMiss,
  restoreCamera = null,
  restoreCameraNonce = 0,
  frameNonce = 0,
}: {
  visible?: boolean;
  url: string;
  mapping: ProxyMapping;
  pinMode?: boolean;
  pins?: SpatialPinMarker[];
  onSelect: (selection: ObjectSelection[]) => void;
  onCamera: (camera: Camera) => void;
  onCaptureReady: (capture: ProxyCapture | null) => void;
  renderPreset?: RenderPresetId;
  suppressFramingGate?: boolean;
  onViewportPixels?: (pixels: ViewportPixels) => void;
  onPlacePin?: (placement: ProxyPinPlacement) => void;
  onPinMiss?: (reason: "empty" | "unmapped", fallback?: FrozenProxyCapture & {clickUv: [number, number]}) => void;
  restoreCamera?: Camera | null;
  restoreCameraNonce?: number;
  frameNonce?: number;
}) {
  const [modelState, setModelState] = useState<{scene: Object3D; nodes: ReadonlyMap<Object3D, ProxyNode>} | null>(null);
  const ready = modelState !== null;
  const model = modelState?.scene;
  const sceneRadius = useMemo(() => {
    if (!model) return 1;
    const box = new Box3().setFromObject(model);
    return box.isEmpty() ? 1 : Math.max(box.getBoundingSphere(new Sphere()).radius, 0.05);
  }, [model]);
  const frameKey = `${url}:${frameNonce}`;
  const [framedKey, setFramedKey] = useState<string | null>(null);
  const autoFramed = framedKey === frameKey;
  const [liveCamera, setLiveCamera] = useState<Camera | null>(null);
  const host = useRef<HTMLDivElement>(null);
  const controls = useRef<ComponentRef<typeof OrbitControls>>(null);
  const [size, setSize] = useState({width:0,height:0});
  const renderer = useRef<RootState | null>(null);
  const visibleRef = useRef(visible); visibleRef.current = visible;
  const capture = useRef<ProxyCapture | null>(null);
  const captureVisible = useCallback<ProxyCapture>(options => {
    if (!visibleRef.current || !capture.current) throw new Error("Inspection capture requires a visible, ready viewport");
    return capture.current(options);
  }, []);
  const bindCapture = useCallback((next: ProxyCapture | null) => {
    capture.current = next;
    onCaptureReady(visibleRef.current && next ? captureVisible : null);
  }, [onCaptureReady, captureVisible]);
  const handleCamera = useCallback((next: Camera) => {
    setLiveCamera(next);
    onCamera(next);
  }, [onCamera]);
  useLayoutEffect(() => {
    const element = host.current; if (!element) return;
    const measure = () => {
      const bounds = element.getBoundingClientRect();
      if (!visible || bounds.width <= 0 || bounds.height <= 0) return;
      setSize(previous => previous.width === bounds.width && previous.height === bounds.height ? previous : {width:bounds.width,height:bounds.height});
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(element); return () => observer.disconnect();
  }, [visible]);
  useLayoutEffect(() => {
    // Canvas skips configure/render when its own measured rectangle is zero.
    // Drive the existing root too, otherwise a hidden Canvas can retain its old loop.
    renderer.current?.setFrameloop(visible ? "always" : "never");
    onCaptureReady(visible && ready && (restoreCamera || autoFramed) && size.width > 0 && size.height > 0 && capture.current ? captureVisible : null);
  }, [visible, ready, restoreCamera, autoFramed, size.width, size.height, onCaptureReady, captureVisible]);
  const width = restoreCamera && size.width ? Math.min(size.width, size.height * restoreCamera.aspect) : size.width;
  const height = restoreCamera && width ? width / restoreCamera.aspect : size.height;
  const canvasRect = {left: (size.width - width) / 2, top: (size.height - height) / 2, width, height};
  const canvasBoxStyle = restoreCamera && size.width ? {position: "absolute" as const, ...canvasRect} : undefined;
  const projectionCamera = liveCamera ?? restoreCamera;
  const gate = !suppressFramingGate && !pinMode && renderPreset !== "viewport"
    ? computeCanvasFramingGate(canvasRect, resolveRenderTargetDimensions(renderPreset))
    : null;
  return <div ref={host} className={`review-proxy${pinMode ? " pin-mode" : ""}`} aria-label={`Static proxy, source frame ${mapping.sample_frame}`}>
    <Canvas frameloop={visible ? "always" : "never"} onCreated={state => { renderer.current = state; state.setFrameloop(visibleRef.current ? "always" : "never"); }} style={canvasBoxStyle} camera={{position: [5,4,5], near: 0.01, far: 10000, fov: PROXY_FOV}}>
      <hemisphereLight args={["#f4f4ff", "#3a3a48", 1.25]}/>
      <directionalLight position={[5,8,6]} intensity={2}/>
      <directionalLight position={[-6,4,-5]} intensity={1.1} color="#c7d2fe"/>
      <directionalLight position={[0,1.5,7]} intensity={0.75}/>
      <Suspense fallback={null}><Model url={url} mapping={mapping} pinMode={pinMode} onSelect={onSelect} onSceneReady={setModelState}/></Suspense>
      <OrbitControls ref={controls} makeDefault enableDamping={false}/>
      <ProxyDepthNavigationController enabled={visible && ready && (Boolean(restoreCamera) || autoFramed)} pinMode={pinMode} model={modelState} sceneRadius={sceneRadius} controls={controls} onCamera={handleCamera}/>
      <RestoreCamera record={restoreCamera} nonce={restoreCameraNonce} controls={controls}/><CameraObserver onCamera={handleCamera}/>
      <AutoFrameCamera model={modelState?.scene ?? null} frameKey={frameKey} restoreCamera={restoreCamera} controls={controls} onCamera={handleCamera} onFramed={setFramedKey}/>
      <CaptureBinding ready={visible && ready && (Boolean(restoreCamera) || autoFramed) && size.width > 0 && size.height > 0} bind={bindCapture} onViewportPixels={onViewportPixels}/>
      <SurfacePinController enabled={visible && ready && (Boolean(restoreCamera) || autoFramed) && pinMode} model={modelState} onSelect={onSelect} onPlacePin={onPlacePin} onPinMiss={onPinMiss}/>
    </Canvas>
    {gate?.active && gate.width > 0 && gate.height > 0 && (
      <div className="proxy-frame-gate" data-testid="proxy-frame-gate" data-preset={renderPreset} aria-hidden="true"
        style={{left: gate.left, top: gate.top, width: gate.width, height: gate.height}}>
        <span className="proxy-frame-gate-badge">{formatRenderPresetOption(renderPreset)}</span>
      </div>
    )}
    {projectionCamera && pins.length > 0 && width > 0 && height > 0 && (
      <div className="proxy-pin-layer" style={canvasBoxStyle ?? {position: "absolute", inset: 0}} aria-label="Anchored 3D spatial pins">
        {pins.map(pin => {
          const projected = projectBlenderPointVisible(projectionCamera, pin.position);
          if (!projected.visible) return null;
          return (
            <div
              key={pin.id}
              className="proxy-spatial-pin"
              data-testid="proxy-spatial-pin"
              data-instance-id={pin.instanceId}
              style={{left: `${projected.uv[0] * 100}%`, top: `${projected.uv[1] * 100}%`}}
            >
              <span className="proxy-spatial-pin-dot" aria-hidden="true"/>
              <span className="proxy-spatial-pin-tag">{pin.label}</span>
            </div>
          );
        })}
      </div>
    )}
    <div className="review-proxy-footer">
      <span className="review-proxy-label">Static inspection proxy · sample frame {mapping.sample_frame} · not material evidence</span>
      {visible && ready && !pinMode && <span className="review-proxy-controls-hint">Drag orbit · Right-drag pan · Wheel zoom · Shift+wheel or W/S depth · A/D sideways · Q/E down/up · Double-click recentre</span>}
    </div>
  </div>;
}
