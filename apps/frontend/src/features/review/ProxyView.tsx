import { Suspense, useCallback, useLayoutEffect, useMemo, useRef, useState, type ComponentRef, type RefObject } from "react";
import { Canvas, useFrame, useThree, type RootState, type ThreeEvent } from "@react-three/fiber";
import { OrbitControls, useGLTF } from "@react-three/drei";
import { Matrix4, OrthographicCamera, PerspectiveCamera, Vector3, type Object3D } from "three";
import type { Camera, ObjectSelection, ProxyMapping } from "../../api/generated/contracts";
import { captureCamera } from "./media";

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

function Model({url, mapping, onSelect, onReady}: {url: string; mapping: ProxyMapping; onSelect: (selection: ObjectSelection[]) => void; onReady: (ready: boolean) => void}) {
  const gltf = useGLTF(url);
  const scene = useMemo(() => gltf.scene.clone(true), [gltf.scene]);
  useLayoutEffect(() => {onReady(true); return () => onReady(false);}, [onReady, scene]);
  const nodes = useMemo(() => {
    const pairs = new Map<Object3D, ProxyMapping["nodes"][number]>();
    for (const node of mapping.nodes) {
      let object: Object3D | undefined = scene;
      // glTF node_path indexes the scene's children, never object names.
      for (const index of node.node_path) object = object?.children[index];
      if (object) pairs.set(object, node);
    }
    return pairs;
  }, [mapping, scene]);
  function pick(event: ThreeEvent<MouseEvent>) {
    // A draw-instance index is not a stable source instance ID. Require explicit nodes.
    if (event.instanceId !== undefined) return;
    let object: Object3D | null = event.object;
    while (object && !nodes.has(object)) object = object.parent;
    if (!object) return;
    event.stopPropagation();
    const node = nodes.get(object)!;
    onSelect([{object_id: node.object_id, instance_id: node.instance_id}]);
  }
  return <primitive object={scene} onClick={pick}/>;
}

export interface FrozenProxyCapture {
  camera: Camera;
  dimensions: {width: number; height: number};
  png: Promise<Blob>;
}
export type ProxyCapture = () => FrozenProxyCapture;

function RestoreCamera({record, controls}: {record: Camera | null; controls: RefObject<ComponentRef<typeof OrbitControls> | null>}) {
  const {camera, set} = useThree();
  const applied = useRef<{record: Camera; camera: PerspectiveCamera | OrthographicCamera} | null>(null);
  useLayoutEffect(() => {
    if (!record) return;
    if (applied.current?.record !== record) {
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
      applied.current = {record, camera: restored};
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
  }, [record, camera, controls, set]);
  return null;
}

function CaptureBinding({ready, bind}: {ready: boolean; bind: (capture: ProxyCapture | null) => void}) {
  const {gl, scene, camera, size} = useThree();
  const available = useRef(ready); available.current = ready;
  useLayoutEffect(() => {
    if (!ready || !size.width || !size.height || !gl.domElement.width || !gl.domElement.height) { bind(null); return; }
    bind(() => {
      if (!available.current) throw new Error("Inspection capture requires a visible, ready viewport");
      if (!(camera instanceof PerspectiveCamera || camera instanceof OrthographicCamera)) throw new Error("Unsupported inspection camera");
      const {width, height} = gl.domElement;
      if (!width || !height || gl.getContext().isContextLost()) throw new Error("Inspection framebuffer is unavailable");
      // Fractional CSS bounds and DPR can round to a different intrinsic aspect.
      // Make the projection authoritative for these exact output pixels.
      if (camera instanceof PerspectiveCamera) camera.aspect = width / height;
      else {
        const centre = (camera.left + camera.right) / 2;
        const halfWidth = (camera.top - camera.bottom) * width / height / 2;
        camera.left = centre - halfWidth; camera.right = centre + halfWidth;
      }
      camera.updateProjectionMatrix();
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
      return {camera: frozenCamera, dimensions: {width, height}, png: new Promise<Blob>((resolve, reject) => {
        copy.toBlob(blob => blob ? resolve(blob) : reject(new Error("Inspection PNG capture failed")), "image/png");
      })};
    });
    return () => bind(null);
  }, [ready, bind, camera, gl, scene, size.width, size.height]);
  return null;
}

export function ProxyView({visible = true, url, mapping, onSelect, onCamera, onCaptureReady, restoreCamera = null}: {visible?: boolean; url: string; mapping: ProxyMapping; onSelect: (selection: ObjectSelection[]) => void; onCamera: (camera: Camera) => void; onCaptureReady: (capture: ProxyCapture | null) => void; restoreCamera?: Camera | null}) {
  const [ready, setReady] = useState(false);
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
  return <div ref={host} className="review-proxy" aria-label={`Static proxy, source frame ${mapping.sample_frame}`}>
    <Canvas frameloop={visible ? "always" : "never"} onCreated={state => { renderer.current = state; state.setFrameloop(visibleRef.current ? "always" : "never"); }} style={restoreCamera && size.width ? {position:"absolute",left:(size.width-width)/2,top:(size.height-height)/2,width,height} : undefined} camera={{position: [5,4,5], near: 0.01, far: 10000, fov: 50}}>
      <ambientLight intensity={0.8}/><directionalLight position={[4,8,4]} intensity={1.5}/>
      <Suspense fallback={null}><Model url={url} mapping={mapping} onSelect={onSelect} onReady={setReady}/></Suspense>
      <OrbitControls ref={controls} makeDefault enableDamping={false}/>
      <RestoreCamera record={restoreCamera} controls={controls}/><CameraObserver onCamera={onCamera}/>
      <CaptureBinding ready={visible && ready && size.width > 0 && size.height > 0} bind={bindCapture}/>
    </Canvas>
    <span className="review-proxy-label">Static inspection proxy · sample frame {mapping.sample_frame} · not material evidence</span>
  </div>;
}
