import { MeshStandardMaterial, type Material } from "three";

/** Inspection-only lighting response. Never amend a cached GLTF material or texture. */
export function cloneInspectionMaterial(source: Material): Material {
  const material = source.clone();
  if (material instanceof MeshStandardMaterial) {
    const { color } = material;
    const luminance = 0.2126 * color.r + 0.7152 * color.g + 0.0722 * color.b;
    if (luminance < 0.14) {
      if (luminance <= 1e-5) color.setRGB(0.18, 0.18, 0.18);
      else color.multiplyScalar(Math.min(0.18 / luminance, 1 / Math.max(color.r, color.g, color.b)));
      material.metalness = Math.min(material.metalness, 0.35);
      material.roughness = Math.min(Math.max(material.roughness, 0.28), 0.75);
    }
  }
  return material;
}
