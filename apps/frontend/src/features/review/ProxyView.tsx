import { Suspense, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Canvas, useFrame, useThree, type ThreeEvent } from "@react-three/fiber";
import { OrbitControls, useGLTF } from "@react-three/drei";
import { OrthographicCamera, PerspectiveCamera, type Object3D } from "three";
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

function CaptureBinding({ready, bind}: {ready: boolean; bind: (capture: ProxyCapture | null) => void}) {
  const {gl, scene, camera} = useThree();
  useLayoutEffect(() => {
    if (!ready) return;
    bind(() => {
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
  }, [ready, bind, camera, gl, scene]);
  return null;
}

export function ProxyView({url, mapping, onSelect, onCamera, onCaptureReady}: {url: string; mapping: ProxyMapping; onSelect: (selection: ObjectSelection[]) => void; onCamera: (camera: Camera) => void; onCaptureReady: (capture: ProxyCapture | null) => void}) {
  const [ready, setReady] = useState(false);
  return <div className="review-proxy" aria-label={`Static proxy, source frame ${mapping.sample_frame}`}>
    <Canvas camera={{position: [5,4,5], near: 0.01, far: 10000, fov: 50}}>
      <ambientLight intensity={0.8}/><directionalLight position={[4,8,4]} intensity={1.5}/>
      <Suspense fallback={null}><Model url={url} mapping={mapping} onSelect={onSelect} onReady={setReady}/></Suspense>
      <OrbitControls makeDefault enableDamping={false}/><CameraObserver onCamera={onCamera}/>
      <CaptureBinding ready={ready} bind={onCaptureReady}/>
    </Canvas>
    <span className="review-proxy-label">Static inspection proxy · sample frame {mapping.sample_frame} · not material evidence</span>
  </div>;
}
