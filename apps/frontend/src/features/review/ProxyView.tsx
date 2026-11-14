import { Suspense, useMemo, useRef } from "react";
import { Canvas, useFrame, type ThreeEvent } from "@react-three/fiber";
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

function Model({url, mapping, onSelect}: {url: string; mapping: ProxyMapping; onSelect: (selection: ObjectSelection[]) => void}) {
  const gltf = useGLTF(url);
  const scene = useMemo(() => gltf.scene.clone(true), [gltf.scene]);
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

export function ProxyView({url, mapping, onSelect, onCamera}: {url: string; mapping: ProxyMapping; onSelect: (selection: ObjectSelection[]) => void; onCamera: (camera: Camera) => void}) {
  return <div className="review-proxy" aria-label={`Static proxy, source frame ${mapping.sample_frame}`}>
    <Canvas camera={{position: [5,4,5], near: 0.01, far: 10000, fov: 50}}>
      <ambientLight intensity={0.8}/><directionalLight position={[4,8,4]} intensity={1.5}/>
      <Suspense fallback={null}><Model url={url} mapping={mapping} onSelect={onSelect}/></Suspense>
      <OrbitControls makeDefault enableDamping={false}/><CameraObserver onCamera={onCamera}/>
    </Canvas>
    <span className="review-proxy-label">Static inspection proxy · sample frame {mapping.sample_frame} · not material evidence</span>
  </div>;
}
