import { Suspense, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type ComponentRef, type RefObject } from "react";
import { Canvas, useFrame, useThree, type RootState, type ThreeEvent } from "@react-three/fiber";
import { OrbitControls, useGLTF } from "@react-three/drei";
import { Matrix4, OrthographicCamera, PerspectiveCamera, Raycaster, Vector2, Vector3, type Camera as ThreeCamera, type Object3D, type Scene, type WebGLRenderer } from "three";
import type { Camera, ObjectSelection, ProxyMapping } from "../../api/generated/contracts";
import { captureCamera, projectBlenderPoint } from "./media";
import { indexProxyScene, projectBlenderPointVisible, proxyPointToBlender, proxySurfaceNormalToBlender, resolveProxyNode, type ProxyNode } from "./spatial";

const DRAG_THRESHOLD_PX = 4;

export interface FrozenProxyCapture {
  camera: Camera;
  dimensions: {width: number; height: number};
  png: Promise<Blob>;
}
export type ProxyCapture = () => FrozenProxyCapture;

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

function captureCleanViewport(gl: WebGLRenderer, scene: Scene, camera: ThreeCamera): FrozenProxyCapture {
  if (!(camera instanceof PerspectiveCamera || camera instanceof OrthographicCamera)) {
    throw new Error("Unsupported inspection camera");
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
  context.drawImage(gl.domElement, 0, 0);
  return {
    camera: frozenCamera,
    dimensions: {width, height},
    png: new Promise<Blob>((resolve, reject) => {
      copy.toBlob(blob => blob ? resolve(blob) : reject(new Error("Inspection PNG capture failed")), "image/png");
    }),
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
  const scene = useMemo(() => gltf.scene.clone(true), [gltf.scene]);
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

function CaptureBinding({ready, bind}: {ready: boolean; bind: (capture: ProxyCapture | null) => void}) {
  const {gl, scene, camera, size} = useThree();
  const available = useRef(ready); available.current = ready;
  useLayoutEffect(() => {
    if (!ready || !size.width || !size.height || !gl.domElement.width || !gl.domElement.height) { bind(null); return; }
    bind(() => {
      if (!available.current) throw new Error("Inspection capture requires a visible, ready viewport");
      return captureCleanViewport(gl, scene, camera);
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
  onPlacePin,
  onPinMiss,
  restoreCamera = null,
  restoreCameraNonce = 0,
}: {
  visible?: boolean;
  url: string;
  mapping: ProxyMapping;
  pinMode?: boolean;
  pins?: SpatialPinMarker[];
  onSelect: (selection: ObjectSelection[]) => void;
  onCamera: (camera: Camera) => void;
  onCaptureReady: (capture: ProxyCapture | null) => void;
  onPlacePin?: (placement: ProxyPinPlacement) => void;
  onPinMiss?: (reason: "empty" | "unmapped", fallback?: FrozenProxyCapture & {clickUv: [number, number]}) => void;
  restoreCamera?: Camera | null;
  restoreCameraNonce?: number;
}) {
  const [modelState, setModelState] = useState<{scene: Object3D; nodes: ReadonlyMap<Object3D, ProxyNode>} | null>(null);
  const ready = modelState !== null;
  const [liveCamera, setLiveCamera] = useState<Camera | null>(null);
  const host = useRef<HTMLDivElement>(null);
  const controls = useRef<ComponentRef<typeof OrbitControls>>(null);
  const [size, setSize] = useState({width:0,height:0});
  const renderer = useRef<RootState | null>(null);
  const visibleRef = useRef(visible); visibleRef.current = visible;
  const capture = useRef<ProxyCapture | null>(null);
  const captureVisible = useCallback(() => {
    if (!visibleRef.current || !capture.current) throw new Error("Inspection capture requires a visible, ready viewport");
    return capture.current();
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
    onCaptureReady(visible && size.width > 0 && size.height > 0 && capture.current ? captureVisible : null);
  }, [visible, size.width, size.height, onCaptureReady, captureVisible]);
  const width = restoreCamera && size.width ? Math.min(size.width,size.height*restoreCamera.aspect) : size.width;
  const height = restoreCamera && width ? width/restoreCamera.aspect : size.height;
  const canvasBoxStyle = restoreCamera && size.width ? {position:"absolute" as const,left:(size.width-width)/2,top:(size.height-height)/2,width,height} : undefined;
  const projectionCamera = liveCamera ?? restoreCamera;
  return <div ref={host} className={`review-proxy${pinMode ? " pin-mode" : ""}`} aria-label={`Static proxy, source frame ${mapping.sample_frame}`}>
    <Canvas frameloop={visible ? "always" : "never"} onCreated={state => { renderer.current = state; state.setFrameloop(visibleRef.current ? "always" : "never"); }} style={canvasBoxStyle} camera={{position: [5,4,5], near: 0.01, far: 10000, fov: 50}}>
      <ambientLight intensity={0.8}/><directionalLight position={[4,8,4]} intensity={1.5}/>
      <Suspense fallback={null}><Model url={url} mapping={mapping} pinMode={pinMode} onSelect={onSelect} onSceneReady={setModelState}/></Suspense>
      <OrbitControls ref={controls} makeDefault enableDamping={false}/>
      <RestoreCamera record={restoreCamera} nonce={restoreCameraNonce} controls={controls}/><CameraObserver onCamera={handleCamera}/>
      <CaptureBinding ready={visible && ready && size.width > 0 && size.height > 0} bind={bindCapture}/>
      <SurfacePinController enabled={visible && ready && pinMode} model={modelState} onSelect={onSelect} onPlacePin={onPlacePin} onPinMiss={onPinMiss}/>
    </Canvas>
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
    <span className="review-proxy-label">Static inspection proxy · sample frame {mapping.sample_frame} · not material evidence</span>
  </div>;
}
