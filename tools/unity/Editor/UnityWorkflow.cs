using System;
using System.IO;
using System.Linq;
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
    }
    [Serializable] public sealed class MeshRow
    {
        public string name, object_id, instance_id;
        public Vector3[] vertices;
        public int[] triangles;
        public Color colour;
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

        public static void Import()
        {
            CheckEditor();
            var settings = new SerializedObject(Unsupported.GetSerializedAssetInterfaceSingleton("PlayerSettings"));
            var inputHandler = settings.FindProperty("activeInputHandler");
            if (inputHandler == null) throw new InvalidOperationException("Input System player setting unavailable");
            // Both backends preserves existing project input users. Effective after the next Editor start.
            inputHandler.intValue = 2;
            settings.ApplyModifiedPropertiesWithoutUndo();
            Recipe recipe = LoadRecipe();
            if (recipe.source_key != Key(recipe.source_id) || !Finite(recipe.spawn))
                throw new InvalidOperationException("Invalid source key or spawn");
            var exportPath = Argument("--proscenium-export");
            using var sha = SHA256.Create();
            string hash = string.Concat(sha.ComputeHash(File.ReadAllBytes(exportPath)).Select(b => b.ToString("x2")));
            if (hash != recipe.export_sha256) throw new InvalidOperationException("Export hash differs");
            Export export = JsonUtility.FromJson<Export>(File.ReadAllText(exportPath));
            if (export.format != "proscenium-triangles-1" || export.units != "metres" || export.axes != "XZY"
                || export.objects == null || export.objects.Length == 0)
                throw new InvalidOperationException("Unsupported or empty export");
            string directory = "Assets/Proscenium/Generated/" + recipe.source_key;
            Directory.CreateDirectory(Inside(Root, directory));
            AssetDatabase.Refresh();
            ConfigurePipeline(recipe, directory);
            Shader shader = Shader.Find(GraphicsSettings.currentRenderPipeline == null ? "Standard" : "Universal Render Pipeline/Lit");
            if (shader == null) throw new InvalidOperationException("Usable material shader unavailable");
            // Validate every mesh before touching the scene or any existing mesh asset.
            if (export.objects.Select(o => o.instance_id).Distinct().Count() != export.objects.Length)
                throw new InvalidOperationException("Duplicate source instance identity");
            foreach (var row in export.objects)
            {
                if (string.IsNullOrEmpty(row.instance_id) || string.IsNullOrEmpty(row.object_id)
                    || row.vertices == null || row.vertices.Length < 3 || row.vertices.Any(v => !Finite(v))
                    || row.triangles == null || row.triangles.Length == 0 || row.triangles.Length % 3 != 0
                    || row.triangles.Any(i => i < 0 || i >= row.vertices.Length))
                    throw new InvalidOperationException("Invalid source mesh " + row.name);
            }
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
            var root = new GameObject("Proscenium " + recipe.source_key);
            var identity = root.AddComponent<SourceIdentity>();
            identity.sourceKey = recipe.source_key;
            identity.closureDigest = export.closure_digest;
            foreach (var row in export.objects)
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
                var obj = new GameObject(row.name);
                obj.transform.SetParent(root.transform, false);
                obj.AddComponent<MeshFilter>().sharedMesh = mesh;
                obj.AddComponent<MeshRenderer>().sharedMaterial = material;
                obj.AddComponent<MeshCollider>().sharedMesh = mesh;
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
            camera.nearClipPlane = 0.05f;
            camera.farClipPlane = 1000;
            camera.orthographic = recipe.camera == "orthographic";
            camera.orthographicSize = 3;
            var movement = player.AddComponent<WalkthroughPlayer>();
            movement.view = camera;
            movement.spawnPosition = recipe.spawn;
            var sun = new GameObject("Walkthrough light");
            sun.transform.SetParent(root.transform, false);
            sun.transform.rotation = Quaternion.Euler(50, -30, 0);
            sun.AddComponent<Light>().type = LightType.Directional;
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
                var serialized = new SerializedObject(asset);
                var list = serialized.FindProperty("m_RendererDataList");
                list.arraySize = 1;
                list.GetArrayElementAtIndex(0).objectReferenceValue = renderer;
                serialized.ApplyModifiedPropertiesWithoutUndo();
                AssetDatabase.CreateAsset(asset, path);
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
            if (roots.Length != 1 || roots[0].GetComponentsInChildren<MeshCollider>().Length == 0
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
            var paths = AssetDatabase.FindAssets("", new[] { directory }).Select(AssetDatabase.GUIDToAssetPath)
                .Where(p => !AssetDatabase.IsValidFolder(p)).OrderBy(p => p).ToArray();
            var before = paths.Select(AssetDatabase.AssetPathToGUID).ToArray();
            Import();
            var after = paths.Select(AssetDatabase.AssetPathToGUID).ToArray();
            if (!before.SequenceEqual(after) || GameObject.Find("Unrelated acceptance sentinel") == null)
                throw new InvalidOperationException("Repeat import changed GUIDs or unrelated scene content");
            int roots = SceneManager.GetActiveScene().GetRootGameObjects().Count(o =>
                o.GetComponent<SourceIdentity>()?.sourceKey == recipe.source_key);
            if (roots != 1) throw new InvalidOperationException("Duplicate generated root");
            var lines = paths.Select((p, i) => p + " " + before[i] + " " + after[i]);
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
