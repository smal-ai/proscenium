using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using UnityEditor;
using UnityEditor.Build.Reporting;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.Rendering;
using UnityEngine.SceneManagement;

namespace Proscenium.Unity.Editor
{
    [Serializable] public sealed class Recipe
    {
        public string source_id, source_key, export_path, export_sha256, scene_path, camera, pipeline;
        public Vector3 spawn;
    }
    [Serializable] public sealed class Export
    {
        public string format, closure_digest, units, axes;
        public MeshRow[] objects;
        public MaterialRow[] materials;
        public SharedMeshRow[] shared_meshes;
        public LightRow[] lights;
        public EnvironmentRow environment;
    }
    [Serializable] public sealed class MeshRow
    {
        public string name, object_id, instance_id;
        public Vector3[] vertices;
        public int[] triangles;
        public Color colour;
        public string mesh_key, collider;
        public bool has_transform;
        public Vector3 position, scale;
        public Quaternion rotation;
    }
    [Serializable] public sealed class MaterialRow
    {
        public string material_id, name;
        public Color base_colour, emission_colour;
        public float metallic, smoothness;
        public bool emissive;
    }
    [Serializable] public sealed class SubmeshRow
    {
        public string material_id;
        public int[] triangles;
    }
    [Serializable] public sealed class SharedMeshRow
    {
        public string mesh_key;
        public Vector3[] vertices;
        public Vector2[] uvs;
        public SubmeshRow[] submeshes;
        public Vector3 local_bounds_size;
    }
    [Serializable] public sealed class LightRow
    {
        public string name, type;
        public Vector3 position;
        public Quaternion rotation;
        public Color colour;
        public float intensity, range, spot_angle, inner_spot_angle;
        public bool cast_shadows;
    }
    [Serializable] public sealed class EnvironmentRow
    {
        public bool has_environment, use_skybox;
        public Color world_colour, ambient_colour;
        public float world_strength;
    }
    [Serializable] public sealed class Validation { public string status, notes; }
    [Serializable] public sealed class UnityResult
    {
        public string editor_version, pipeline, scene_path, player_script;
        public Validation validation;
    }

    public static class UnityWorkflow
    {
        public static string Argument(string flag)
        {
            var args = Environment.GetCommandLineArgs();
            int i = Array.IndexOf(args, flag);
            if (i < 0 || i + 1 >= args.Length) throw new InvalidOperationException("Missing " + flag);
            return args[i + 1];
        }
        public static string Root => Directory.GetParent(Application.dataPath).FullName;
        public static string Inside(string root, string relative)
        {
            if (string.IsNullOrEmpty(relative) || Path.IsPathRooted(relative) || relative.Contains('\\')
                || relative.Split('/').Any(p => p == ".." || p == "." || p == ""))
                throw new InvalidOperationException("Unsafe project path");
            string path = Path.GetFullPath(Path.Combine(root, relative));
            if (!path.StartsWith(Path.GetFullPath(root) + Path.DirectorySeparatorChar, StringComparison.Ordinal))
                throw new InvalidOperationException("Outside project");
            return path;
        }
        public static Recipe LoadRecipe() => JsonUtility.FromJson<Recipe>(File.ReadAllText(Argument("--proscenium-recipe")));
        public static void CheckEditor()
        {
            if (EditorApplication.isPlayingOrWillChangePlaymode || Enumerable.Range(0, SceneManager.sceneCount)
                .Any(i => SceneManager.GetSceneAt(i).isDirty))
                throw new InvalidOperationException("Save dirty scenes and stop Play mode before importing");
            if (Application.unityVersion != "6000.3.27f1")
                throw new InvalidOperationException("This workflow is pinned to Editor 6000.3.27f1");
        }
        public static string Key(string value)
        {
            using var sha = SHA256.Create();
            return string.Concat(sha.ComputeHash(Encoding.UTF8.GetBytes(value)).Take(12).Select(b => b.ToString("x2")));
        }
        private static bool Finite(Vector3 v) => float.IsFinite(v.x) && float.IsFinite(v.y) && float.IsFinite(v.z);
        private static bool Finite(Color c) => float.IsFinite(c.r) && float.IsFinite(c.g)
            && float.IsFinite(c.b) && float.IsFinite(c.a);
        private static bool UnitRotation(Quaternion q) =>
            Mathf.Abs(q.x * q.x + q.y * q.y + q.z * q.z + q.w * q.w - 1f) < 1e-3f;
        private static bool ValidTriangles(int[] indices, int count) => indices != null && indices.Length > 0
            && indices.Length % 3 == 0 && indices.All(i => i >= 0 && i < count);

        private static bool ValidateExport(Export export)
        {
            if (export == null || export.format != "proscenium-triangles-1" || export.units != "metres"
                || export.axes != "XZY" || export.objects == null || export.objects.Length == 0)
                throw new InvalidOperationException("Unsupported or empty export");
            var materialIds = new HashSet<string>(StringComparer.Ordinal);
            foreach (var mat in export.materials ?? Array.Empty<MaterialRow>())
            {
                if (mat == null || string.IsNullOrEmpty(mat.material_id) || !materialIds.Add(mat.material_id)
                    || !Finite(mat.base_colour) || !Finite(mat.emission_colour)
                    || !float.IsFinite(mat.metallic) || !float.IsFinite(mat.smoothness))
                    throw new InvalidOperationException("Invalid or duplicate source material " + mat?.material_id);
            }
            bool legacy = export.shared_meshes == null || export.shared_meshes.Length == 0;
            var meshKeys = new HashSet<string>(StringComparer.Ordinal);
            if (!legacy)
            {
                foreach (var sm in export.shared_meshes)
                {
                    if (sm == null || string.IsNullOrEmpty(sm.mesh_key) || !meshKeys.Add(sm.mesh_key)
                        || sm.vertices == null || sm.vertices.Length < 3 || sm.vertices.Any(v => !Finite(v))
                        || (sm.uvs != null && sm.uvs.Length != 0 && (sm.uvs.Length != sm.vertices.Length
                            || sm.uvs.Any(uv => !float.IsFinite(uv.x) || !float.IsFinite(uv.y))))
                        || sm.submeshes == null || sm.submeshes.Length == 0
                        || sm.submeshes.Any(s => s == null || !materialIds.Contains(s.material_id)
                            || !ValidTriangles(s.triangles, sm.vertices.Length)))
                        throw new InvalidOperationException("Invalid or duplicate shared mesh " + sm?.mesh_key);
                }
            }
            var instanceIds = new HashSet<string>(StringComparer.Ordinal);
            foreach (var row in export.objects)
            {
                if (row == null || string.IsNullOrEmpty(row.instance_id) || string.IsNullOrEmpty(row.object_id)
                    || !instanceIds.Add(row.instance_id))
                    throw new InvalidOperationException("Missing or duplicate source instance identity");
                if (legacy)
                {
                    if (row.vertices == null || row.vertices.Length < 3 || row.vertices.Any(v => !Finite(v))
                        || !ValidTriangles(row.triangles, row.vertices.Length) || !Finite(row.colour))
                        throw new InvalidOperationException("Invalid source mesh " + row.name);
                }
                else if (!meshKeys.Contains(row.mesh_key) || (row.has_transform &&
                    (!Finite(row.position) || !Finite(row.scale) || !UnitRotation(row.rotation))))
                    throw new InvalidOperationException("Invalid shared mesh instance " + row.name);
            }
            foreach (var light in export.lights ?? Array.Empty<LightRow>())
            {
                if (light == null || !Finite(light.position) || !UnitRotation(light.rotation) || !Finite(light.colour)
                    || !float.IsFinite(light.intensity) || !float.IsFinite(light.range)
                    || !float.IsFinite(light.spot_angle) || !float.IsFinite(light.inner_spot_angle)
                    || (light.type != "directional" && light.type != "point" && light.type != "spot"))
                    throw new InvalidOperationException("Invalid exported light " + light?.name);
            }
            if (export.environment != null && export.environment.has_environment
                && (!Finite(export.environment.world_colour) || !Finite(export.environment.ambient_colour)))
                throw new InvalidOperationException("Invalid exported environment");
            return legacy;
        }

        public static void Import()
        {
            CheckEditor();
            Recipe recipe = LoadRecipe();
            if (recipe.source_key != Key(recipe.source_id) || !Finite(recipe.spawn))
                throw new InvalidOperationException("Invalid source key or spawn");
            var exportPath = Argument("--proscenium-export");
            using var sha = SHA256.Create();
            string hash = string.Concat(sha.ComputeHash(File.ReadAllBytes(exportPath)).Select(b => b.ToString("x2")));
            if (hash != recipe.export_sha256) throw new InvalidOperationException("Export hash differs");
            Export export = JsonUtility.FromJson<Export>(File.ReadAllText(exportPath));
            // Validate geometry and references before touching the scene or generated assets.
            bool legacy = ValidateExport(export);
            var settings = new SerializedObject(Unsupported.GetSerializedAssetInterfaceSingleton("PlayerSettings"));
            var inputHandler = settings.FindProperty("activeInputHandler");
            if (inputHandler == null) throw new InvalidOperationException("Input System player setting unavailable");
            // Enable both input backends to preserve existing input users. Effective after the next Editor start.
            inputHandler.intValue = 2;
            settings.ApplyModifiedPropertiesWithoutUndo();
            string directory = "Assets/Proscenium/Generated/" + recipe.source_key;
            Directory.CreateDirectory(Inside(Root, directory));
            AssetDatabase.Refresh();
            ConfigurePipeline(recipe, directory);
            Shader shader = Shader.Find(GraphicsSettings.currentRenderPipeline == null ? "Standard" : "Universal Render Pipeline/Lit");
            if (shader == null) throw new InvalidOperationException("Usable material shader unavailable");
            string sceneFile = Inside(Root, recipe.scene_path);
            Directory.CreateDirectory(Path.GetDirectoryName(sceneFile));
            Scene scene = File.Exists(sceneFile)
                ? EditorSceneManager.OpenScene(recipe.scene_path, OpenSceneMode.Single)
                : EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
            var previous = scene.GetRootGameObjects().Where(o => {
                var marker = o.GetComponent<SourceIdentity>();
                return marker != null && marker.sourceKey == recipe.source_key;
            }).ToArray();
            if (previous.Length > 1) throw new InvalidOperationException("Ambiguous generated subtree");
            var materials = ImportMaterials(export.materials, directory, shader);
            var meshes = legacy ? null : ImportMeshes(export.shared_meshes, materials, directory);
            var root = new GameObject("Proscenium " + recipe.source_key);
            var identity = root.AddComponent<SourceIdentity>();
            identity.sourceKey = recipe.source_key;
            identity.closureDigest = export.closure_digest;
            foreach (var row in export.objects)
            {
                var obj = new GameObject(row.name);
                obj.transform.SetParent(root.transform, false);
                if (legacy) ImportInlineMesh(row, obj, directory, shader);
                else
                {
                    var (mesh, submeshMaterials) = meshes[row.mesh_key];
                    if (row.has_transform)
                    {
                        obj.transform.SetLocalPositionAndRotation(row.position, row.rotation);
                        obj.transform.localScale = row.scale;
                    }
                    obj.AddComponent<MeshFilter>().sharedMesh = mesh;
                    obj.AddComponent<MeshRenderer>().sharedMaterials = submeshMaterials;
                    switch (row.collider)
                    {
                        case "none": break;
                        case "box":
                            var box = obj.AddComponent<BoxCollider>();
                            box.center = mesh.bounds.center;
                            box.size = mesh.bounds.size;
                            break;
                        default: obj.AddComponent<MeshCollider>().sharedMesh = mesh; break;
                    }
                }
                var marker = obj.AddComponent<SourceIdentity>();
                marker.sourceKey = recipe.source_key;
                marker.closureDigest = export.closure_digest;
                marker.objectId = row.object_id;
                marker.instanceId = row.instance_id;
            }
            foreach (var old in previous) UnityEngine.Object.DestroyImmediate(old);
            var player = new GameObject("Walkthrough player");
            player.transform.SetParent(root.transform, false);
            player.transform.position = recipe.spawn;
            var controller = player.AddComponent<CharacterController>();
            controller.height = 1.8f;
            controller.radius = 0.25f;
            controller.skinWidth = 0.02f;
            controller.minMoveDistance = 0;
            controller.center = Vector3.zero;
            controller.stepOffset = 0.25f;
            var cameraObject = new GameObject("Walkthrough camera");
            cameraObject.transform.SetParent(player.transform, false);
            cameraObject.transform.localPosition = new Vector3(0, 0.72f, 0);
            var camera = cameraObject.AddComponent<Camera>();
            if (!UnityEngine.Object.FindObjectsByType<AudioListener>(FindObjectsSortMode.None).Any(l => l.enabled))
                cameraObject.AddComponent<AudioListener>();
            camera.nearClipPlane = 0.05f;
            camera.farClipPlane = 1000;
            camera.orthographic = recipe.camera == "orthographic";
            camera.orthographicSize = 3;
            var movement = player.AddComponent<WalkthroughPlayer>();
            movement.view = camera;
            movement.spawnPosition = recipe.spawn;
            ImportLighting(export, root, camera, directory);
            Physics.SyncTransforms();
            Vector3 lower = recipe.spawn + Vector3.down * (0.9f - 0.25f);
            Vector3 upper = recipe.spawn + Vector3.up * (0.9f - 0.25f);
            // Ignore the controller itself while checking a non-penetrating supported spawn.
            controller.enabled = false;
            bool obstructed = Physics.CheckCapsule(lower, upper, 0.23f, ~0, QueryTriggerInteraction.Ignore);
            bool supported = Physics.Raycast(recipe.spawn, Vector3.down, out var hit, 1.1f, ~0, QueryTriggerInteraction.Ignore);
            controller.enabled = true;
            if (obstructed || !supported || Vector3.Dot(hit.normal, Vector3.up) < 0.7f)
                throw new InvalidOperationException("Spawn is obstructed or lacks nearby floor support");
            // The input manifest is a named asset and retains its .meta on every import.
            File.Copy(Argument("--proscenium-recipe"), Inside(Root, directory + "/source-manifest.json"), true);
            AssetDatabase.Refresh();
            AssetDatabase.SaveAssets();
            if (!EditorSceneManager.SaveScene(scene, recipe.scene_path)) throw new IOException("Scene save failed");
            WriteResult(recipe, "not_run", "Saved with finite metre-scale meshes, colliders and supported spawn. PlayMode not yet run.");
        }

        private static void ImportInlineMesh(MeshRow row, GameObject obj, string directory, Shader shader)
        {
            string prefix = directory + "/" + Key(row.instance_id);
            Mesh mesh = AssetDatabase.LoadAssetAtPath<Mesh>(prefix + ".asset");
            if (mesh == null) { mesh = new Mesh(); AssetDatabase.CreateAsset(mesh, prefix + ".asset"); }
            mesh.Clear();
            mesh.indexFormat = IndexFormat.UInt32;
            mesh.vertices = row.vertices;
            mesh.triangles = row.triangles;
            mesh.RecalculateNormals();
            mesh.RecalculateBounds();
            EditorUtility.SetDirty(mesh);
            Material material = AssetDatabase.LoadAssetAtPath<Material>(prefix + ".mat");
            if (material == null) { material = new Material(shader); AssetDatabase.CreateAsset(material, prefix + ".mat"); }
            material.shader = shader;
            material.color = row.colour;
            EditorUtility.SetDirty(material);
            obj.AddComponent<MeshFilter>().sharedMesh = mesh;
            obj.AddComponent<MeshRenderer>().sharedMaterial = material;
            obj.AddComponent<MeshCollider>().sharedMesh = mesh;
        }

        private static Dictionary<string, Material> ImportMaterials(MaterialRow[] rows, string directory, Shader shader)
        {
            var materials = new Dictionary<string, Material>(StringComparer.Ordinal);
            if (rows == null || rows.Length == 0) return materials;
            if (!AssetDatabase.IsValidFolder(directory + "/Materials")) AssetDatabase.CreateFolder(directory, "Materials");
            foreach (var mat in rows)
            {
                string path = directory + "/Materials/" + Key(mat.material_id) + ".mat";
                var material = AssetDatabase.LoadAssetAtPath<Material>(path);
                if (material == null) { material = new Material(shader); AssetDatabase.CreateAsset(material, path); }
                material.shader = shader;
                material.name = mat.name;
                material.color = mat.base_colour;
                if (material.HasProperty("_BaseColor")) material.SetColor("_BaseColor", mat.base_colour);
                if (material.HasProperty("_Metallic")) material.SetFloat("_Metallic", mat.metallic);
                if (material.HasProperty("_Smoothness")) material.SetFloat("_Smoothness", mat.smoothness);
                if (material.HasProperty("_Glossiness")) material.SetFloat("_Glossiness", mat.smoothness);
                if (mat.emissive) material.EnableKeyword("_EMISSION");
                else material.DisableKeyword("_EMISSION");
                if (material.HasProperty("_EmissionColor"))
                    material.SetColor("_EmissionColor", mat.emissive ? mat.emission_colour : Color.black);
                material.globalIlluminationFlags = mat.emissive
                    ? MaterialGlobalIlluminationFlags.RealtimeEmissive : MaterialGlobalIlluminationFlags.EmissiveIsBlack;
                EditorUtility.SetDirty(material);
                materials.Add(mat.material_id, material);
            }
            return materials;
        }

        private static Dictionary<string, (Mesh, Material[])> ImportMeshes(
            SharedMeshRow[] rows, Dictionary<string, Material> materials, string directory)
        {
            var meshes = new Dictionary<string, (Mesh, Material[])>(StringComparer.Ordinal);
            if (!AssetDatabase.IsValidFolder(directory + "/Meshes")) AssetDatabase.CreateFolder(directory, "Meshes");
            foreach (var sm in rows)
            {
                string path = directory + "/Meshes/" + Key(sm.mesh_key) + ".asset";
                var mesh = AssetDatabase.LoadAssetAtPath<Mesh>(path);
                if (mesh == null) { mesh = new Mesh(); AssetDatabase.CreateAsset(mesh, path); }
                mesh.Clear();
                mesh.indexFormat = IndexFormat.UInt32;
                mesh.vertices = sm.vertices;
                if (sm.uvs != null && sm.uvs.Length == sm.vertices.Length) mesh.uv = sm.uvs;
                mesh.subMeshCount = sm.submeshes.Length;
                var submeshMaterials = new Material[sm.submeshes.Length];
                for (int s = 0; s < sm.submeshes.Length; s++)
                {
                    mesh.SetTriangles(sm.submeshes[s].triangles, s, false);
                    submeshMaterials[s] = materials[sm.submeshes[s].material_id];
                }
                mesh.RecalculateNormals();
                mesh.RecalculateBounds();
                EditorUtility.SetDirty(mesh);
                meshes.Add(sm.mesh_key, (mesh, submeshMaterials));
            }
            return meshes;
        }

        private static void ImportLighting(Export export, GameObject root, Camera camera, string directory)
        {
            var environment = export.environment;
            if (environment != null && environment.has_environment)
            {
                if (!environment.use_skybox)
                {
                    RenderSettings.ambientMode = AmbientMode.Flat;
                    RenderSettings.ambientLight = environment.ambient_colour;
                    RenderSettings.defaultReflectionMode = DefaultReflectionMode.Custom;
                    RenderSettings.reflectionIntensity = 0f;
                    camera.clearFlags = CameraClearFlags.SolidColor;
                    camera.backgroundColor = environment.world_colour;
                }
                else
                {
                    RenderSettings.ambientMode = AmbientMode.Skybox;
                    RenderSettings.defaultReflectionMode = DefaultReflectionMode.Skybox;
                    RenderSettings.reflectionIntensity = 1f;
                    camera.clearFlags = CameraClearFlags.Skybox;
                }
            }
            if (export.lights == null || export.lights.Length == 0)
            {
                var sun = new GameObject("Walkthrough light");
                sun.transform.SetParent(root.transform, false);
                sun.transform.rotation = Quaternion.Euler(50, -30, 0);
                var light = sun.AddComponent<Light>();
                light.type = LightType.Directional;
                light.shadows = LightShadows.Soft;
                return;
            }
            var shadowed = new HashSet<LightRow>(export.lights.Where(l => l.cast_shadows)
                .OrderByDescending(l => l.type == "directional").ThenByDescending(l => l.intensity)
                .ThenByDescending(l => l.type == "spot").Take(6));
            foreach (var row in export.lights)
            {
                var obj = new GameObject(row.name);
                obj.transform.SetParent(root.transform, false);
                obj.transform.SetLocalPositionAndRotation(row.position, row.rotation);
                var light = obj.AddComponent<Light>();
                light.type = row.type == "directional" ? LightType.Directional
                    : row.type == "spot" ? LightType.Spot : LightType.Point;
                light.color = row.colour;
                light.intensity = row.intensity;
                light.range = row.range;
                if (light.type == LightType.Spot)
                {
                    light.spotAngle = row.spot_angle;
                    light.innerSpotAngle = row.inner_spot_angle;
                }
                light.shadows = shadowed.Contains(row) ? LightShadows.Soft : LightShadows.None;
            }
            var pipeline = GraphicsSettings.currentRenderPipeline;
            if (pipeline != null)
            {
                EnableUrpLighting(GraphicsSettings.defaultRenderPipeline, directory);
                if (pipeline != GraphicsSettings.defaultRenderPipeline) EnableUrpLighting(pipeline, directory);
                AddUrpVolume(root, camera, directory);
            }
        }

        private static bool IsGeneratedAsset(UnityEngine.Object asset, string directory) => asset != null
            && AssetDatabase.GetAssetPath(asset).StartsWith(directory + "/", StringComparison.Ordinal);

        private static void EnableUrpLighting(RenderPipelineAsset asset, string directory)
        {
            if (!IsGeneratedAsset(asset, directory)
                || asset.GetType().FullName != "UnityEngine.Rendering.Universal.UniversalRenderPipelineAsset") return;
            var settings = new SerializedObject(asset);
            foreach (string field in new[] {
                "m_SupportsHDR", "m_AnyShadowsSupported", "m_MainLightShadowsSupported",
                "m_AdditionalLightShadowsSupported", "m_SoftShadowsSupported"
            })
            {
                var property = settings.FindProperty(field);
                if (property != null) property.boolValue = true;
            }
            var mode = settings.FindProperty("m_AdditionalLightsRenderingMode");
            if (mode != null) mode.intValue = 1; // Per Pixel
            var limit = settings.FindProperty("m_AdditionalLightsPerObjectLimit");
            if (limit != null) limit.intValue = 8;
            if (settings.ApplyModifiedPropertiesWithoutUndo()) EditorUtility.SetDirty(asset);

            var renderers = settings.FindProperty("m_RendererDataList");
            if (renderers == null || !renderers.isArray) return;
            for (int i = 0; i < renderers.arraySize; i++)
            {
                var renderer = renderers.GetArrayElementAtIndex(i).objectReferenceValue;
                if (!IsGeneratedAsset(renderer, directory)
                    || renderer.GetType().FullName != "UnityEngine.Rendering.Universal.UniversalRendererData")
                    continue;
                var rendererSettings = new SerializedObject(renderer);
                var renderingMode = rendererSettings.FindProperty("m_RenderingMode");
                if (renderingMode != null) renderingMode.intValue = 2; // Forward+ in URP 17.3.0
                var postProcessData = rendererSettings.FindProperty("postProcessData");
                if (postProcessData != null && postProcessData.objectReferenceValue == null)
                {
                    var dataType = Type.GetType("UnityEngine.Rendering.Universal.PostProcessData, Unity.RenderPipelines.Universal.Runtime");
                    var getDefault = dataType?.GetMethod("GetDefaultPostProcessData",
                        BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static, null, Type.EmptyTypes, null);
                    var defaultData = getDefault?.Invoke(null, null) as UnityEngine.Object;
                    if (defaultData != null) postProcessData.objectReferenceValue = defaultData;
                }
                if (rendererSettings.ApplyModifiedPropertiesWithoutUndo()) EditorUtility.SetDirty(renderer);
            }
        }

        private static void AddUrpVolume(GameObject root, Camera camera, string directory)
        {
            const string core = ", Unity.RenderPipelines.Core.Runtime";
            const string urp = ", Unity.RenderPipelines.Universal.Runtime";
            const string universal = "UnityEngine.Rendering.Universal.";
            var volumeType = Type.GetType("UnityEngine.Rendering.Volume" + core);
            var profileType = Type.GetType("UnityEngine.Rendering.VolumeProfile" + core);
            var tonemappingType = Type.GetType(universal + "Tonemapping" + urp);
            var bloomType = Type.GetType(universal + "Bloom" + urp);
            var cameraDataType = Type.GetType(universal + "UniversalAdditionalCameraData" + urp);
            var add = profileType?.GetMethod("Add", new[] { typeof(Type), typeof(bool) });
            var components = profileType?.GetField("components");
            var sharedProfile = volumeType?.GetField("sharedProfile");
            var isGlobal = volumeType?.GetProperty("isGlobal");
            var renderPostProcessing = cameraDataType?.GetProperty("renderPostProcessing");
            if (volumeType == null || profileType == null || tonemappingType == null || bloomType == null
                || cameraDataType == null || add == null || components == null || sharedProfile == null
                || isGlobal == null || renderPostProcessing == null) return;

            string path = directory + "/LightingVolume.asset";
            var profile = AssetDatabase.LoadAssetAtPath<ScriptableObject>(path);
            if (profile == null)
            {
                profile = ScriptableObject.CreateInstance(profileType);
                AssetDatabase.CreateAsset(profile, path);
            }
            UnityEngine.Object GetEffect(Type type)
            {
                var effects = (System.Collections.IEnumerable)components.GetValue(profile);
                var existing = effects.Cast<UnityEngine.Object>().FirstOrDefault(e => e != null && type.IsInstanceOfType(e));
                if (existing != null) return existing;
                var effect = (UnityEngine.Object)add.Invoke(profile, new object[] { type, false });
                AssetDatabase.AddObjectToAsset(effect, profile);
                EditorUtility.SetDirty(profile);
                return effect;
            }
            void SetParameter(SerializedObject effect, string name, Action<SerializedProperty> setValue)
            {
                var parameter = effect.FindProperty(name);
                var value = parameter?.FindPropertyRelative("m_Value");
                var state = parameter?.FindPropertyRelative("m_OverrideState");
                if (value == null || state == null) return;
                setValue(value);
                state.boolValue = true;
            }
            var tonemapping = new SerializedObject(GetEffect(tonemappingType));
            SetParameter(tonemapping, "mode", p => {
                int aces = Array.IndexOf(p.enumNames, "ACES");
                if (aces >= 0) p.enumValueIndex = aces;
            });
            tonemapping.ApplyModifiedPropertiesWithoutUndo();
            var bloom = new SerializedObject(GetEffect(bloomType));
            SetParameter(bloom, "threshold", p => p.floatValue = 1f);
            SetParameter(bloom, "intensity", p => p.floatValue = 0.35f);
            bloom.ApplyModifiedPropertiesWithoutUndo();

            var obj = new GameObject("Walkthrough lighting volume");
            obj.transform.SetParent(root.transform, false);
            var volume = obj.AddComponent(volumeType);
            isGlobal.SetValue(volume, true);
            sharedProfile.SetValue(volume, profile);
            var cameraData = camera.GetComponent(cameraDataType) ?? camera.gameObject.AddComponent(cameraDataType);
            renderPostProcessing.SetValue(cameraData, true);
            camera.allowHDR = true;
        }

        private static void ConfigurePipeline(Recipe recipe, string directory)
        {
            if (recipe.pipeline == "builtin")
            {
                if (GraphicsSettings.currentRenderPipeline != null)
                    throw new InvalidOperationException("Project has a render pipeline. Request its configured pipeline explicitly.");
                return;
            }
            if (recipe.pipeline != "urp") throw new InvalidOperationException("Unsupported pipeline");
            var assetType = Type.GetType("UnityEngine.Rendering.Universal.UniversalRenderPipelineAsset, Unity.RenderPipelines.Universal.Runtime");
            var rendererType = Type.GetType("UnityEngine.Rendering.Universal.UniversalRendererData, Unity.RenderPipelines.Universal.Runtime");
            if (assetType == null || rendererType == null) throw new InvalidOperationException("URP package unavailable");
            if (GraphicsSettings.currentRenderPipeline != null)
            {
                if (!assetType.IsInstanceOfType(GraphicsSettings.currentRenderPipeline))
                    throw new InvalidOperationException("Configured pipeline is not URP");
                return;
            }
            string path = directory + "/Pipeline.asset";
            var asset = AssetDatabase.LoadAssetAtPath<RenderPipelineAsset>(path);
            if (asset == null)
            {
                var renderer = ScriptableObject.CreateInstance(rendererType);
                AssetDatabase.CreateAsset(renderer, directory + "/Renderer.asset");
                asset = (RenderPipelineAsset)ScriptableObject.CreateInstance(assetType);
                AssetDatabase.CreateAsset(asset, path);
                if (!IsGeneratedAsset(asset, directory) || !IsGeneratedAsset(renderer, directory))
                    throw new InvalidOperationException("Generated pipeline or renderer is outside the source asset directory");
                var serialized = new SerializedObject(asset);
                var list = serialized.FindProperty("m_RendererDataList");
                list.arraySize = 1;
                list.GetArrayElementAtIndex(0).objectReferenceValue = renderer;
                serialized.ApplyModifiedPropertiesWithoutUndo();
                EditorUtility.SetDirty(asset);
            }
            GraphicsSettings.defaultRenderPipeline = asset;
            QualitySettings.renderPipeline = asset;
        }

        public static void Validate()
        {
            CheckEditor();
            var recipe = LoadRecipe();
            EditorSceneManager.OpenScene(recipe.scene_path, OpenSceneMode.Single);
            var roots = SceneManager.GetActiveScene().GetRootGameObjects().Where(o =>
                o.GetComponent<SourceIdentity>()?.sourceKey == recipe.source_key).ToArray();
            if (roots.Length != 1 || !roots[0].GetComponentsInChildren<Collider>().Any(c => !(c is CharacterController))
                || roots[0].GetComponentsInChildren<WalkthroughPlayer>().Length != 1)
                throw new InvalidOperationException("Invalid imported scene");
            WriteResult(recipe, "not_run", "Structural validation only. PlayMode validation not run by this command.");
        }

        // Bounded fixture proof, not used by the production importer.
        public static void ImportRepeatProof()
        {
            Import();
            Recipe recipe = LoadRecipe();
            var sentinel = new GameObject("Unrelated acceptance sentinel");
            sentinel.transform.position = new Vector3(20, 0, 0);
            EditorSceneManager.SaveScene(SceneManager.GetActiveScene(), recipe.scene_path);
            string directory = "Assets/Proscenium/Generated/" + recipe.source_key;
            string[] AssetPaths() => AssetDatabase.FindAssets("", new[] { directory }).Select(AssetDatabase.GUIDToAssetPath)
                .Where(p => !AssetDatabase.IsValidFolder(p)).OrderBy(p => p, StringComparer.Ordinal).ToArray();
            var beforePaths = AssetPaths();
            var beforeGuids = beforePaths.Select(AssetDatabase.AssetPathToGUID).ToArray();
            Import();
            var afterPaths = AssetPaths();
            var afterGuids = afterPaths.Select(AssetDatabase.AssetPathToGUID).ToArray();
            if (!beforePaths.SequenceEqual(afterPaths) || !beforeGuids.SequenceEqual(afterGuids)
                || GameObject.Find("Unrelated acceptance sentinel") == null)
                throw new InvalidOperationException("Repeat import changed asset paths, GUIDs or unrelated scene content");
            int roots = SceneManager.GetActiveScene().GetRootGameObjects().Count(o =>
                o.GetComponent<SourceIdentity>()?.sourceKey == recipe.source_key);
            if (roots != 1) throw new InvalidOperationException("Duplicate generated root");
            var lines = beforePaths.Select((p, i) => p + " " + beforeGuids[i] + " " + afterGuids[i]);
            File.WriteAllLines(Argument("--proscenium-result") + ".guids.txt", lines);
        }

        public static void Build()
        {
            Validate();
            var recipe = LoadRecipe();
            string path = Inside(Root, "Builds/Proscenium/Walkthrough.x86_64");
            Directory.CreateDirectory(Path.GetDirectoryName(path));
            var report = BuildPipeline.BuildPlayer(new BuildPlayerOptions {
                scenes = new[] { recipe.scene_path }, locationPathName = path,
                target = BuildTarget.StandaloneLinux64, options = BuildOptions.None
            });
            if (report.summary.result != BuildResult.Succeeded) throw new InvalidOperationException("Linux build failed");
        }

        public static void WriteResult(Recipe recipe, string status, string notes)
        {
            var result = new UnityResult {
                editor_version = Application.unityVersion,
                pipeline = GraphicsSettings.currentRenderPipeline == null ? "Built-in" : GraphicsSettings.currentRenderPipeline.GetType().FullName,
                scene_path = recipe.scene_path,
                player_script = "Assets/Proscenium/Runtime/WalkthroughPlayer.cs",
                validation = new Validation { status = status, notes = notes }
            };
            File.WriteAllText(Argument("--proscenium-result"), JsonUtility.ToJson(result, true));
        }
    }
}
