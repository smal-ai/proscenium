#if UNITY_EDITOR
using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using NUnit.Framework;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.InputSystem;
using UnityEngine.InputSystem.LowLevel;
using UnityEngine.SceneManagement;
using UnityEngine.Rendering;
using UnityEngine.TestTools;

namespace Proscenium.Unity.Tests
{
    public sealed class PlayableTests
    {
        [Serializable] private sealed class TestRecipe { public string scene_path; }
        [Serializable] private sealed class ObservedValidation { public string status = "not_run"; }
        [Serializable] private sealed class ObservedResult
        {
            public string editor_version, pipeline, scene_path;
            public string player_script = "Assets/Proscenium/Runtime/WalkthroughPlayer.cs";
            public ObservedValidation validation = new ObservedValidation();
        }
        private static string Argument(string key)
        {
            var args = Environment.GetCommandLineArgs();
            return args[Array.IndexOf(args, key) + 1];
        }
        private readonly List<string> metrics = new List<string>();

        [TearDown] public void RestoreTime() { Time.captureDeltaTime = 0; }

        [UnityTest]
        public IEnumerator F05_RealControllerPhysicsAndInput_BothCameraProjections()
        {
            // Deterministic simulated frame delta, actual Editor PlayMode/CharacterController/PhysX.
            // Batch frame throughput must not decide how far the controller travels.
            Time.captureDeltaTime = 1f / 60;
            var recipe = JsonUtility.FromJson<TestRecipe>(File.ReadAllText(Argument("--proscenium-recipe")));
            yield return EditorSceneManager.LoadSceneAsyncInPlayMode(recipe.scene_path,
                new LoadSceneParameters(LoadSceneMode.Single));
            File.WriteAllText(Argument("--proscenium-result"), JsonUtility.ToJson(new ObservedResult {
                editor_version = Application.unityVersion,
                pipeline = GraphicsSettings.currentRenderPipeline == null ? "Built-in" : GraphicsSettings.currentRenderPipeline.GetType().FullName,
                scene_path = recipe.scene_path
            }, true));
            Assert.That(Application.unityVersion, Is.EqualTo("6000.3.27f1"));
            var player = UnityEngine.Object.FindFirstObjectByType<WalkthroughPlayer>();
            Assert.That(player, Is.Not.Null);
            Assert.That(GameObject.Find("Unrelated acceptance sentinel"), Is.Not.Null);
            var controller = player.GetComponent<CharacterController>();
            Assert.That(controller.height, Is.EqualTo(1.8f));
            Assert.That(controller.radius, Is.EqualTo(0.25f));
            player.injectedInput = true;
            foreach (bool orthographic in new[] { false, true })
            {
                player.view.orthographic = orthographic;
                player.Respawn();
                player.testMove = Vector2.zero;
                for (int i = 0; i < 80; i++) yield return null;
                // F01 floor top is y=0. Character origin is the capsule centre.
                float support = Mathf.Abs((player.transform.position.y - controller.height / 2) - 0);
                Assert.That(support, Is.LessThanOrEqualTo(0.05f), "Floor support");
                Vector3 start = player.transform.position;
                player.testMove = Vector2.up;
                for (int i = 0; i < 90; i++) yield return null;
                float moved = Vector3.Distance(start, player.transform.position);
                Assert.That(moved, Is.GreaterThan(0.25f));
                // Continue against F01 wall, near face at z=2.4m.
                for (int i = 0; i < 300; i++) yield return null;
                player.testMove = Vector2.zero;
                float penetration = Mathf.Max(0, player.transform.position.z + controller.radius - 2.4f);
                Assert.That(penetration, Is.LessThan(0.02f), "Wall penetration");
                Assert.That(player.transform.position.z, Is.GreaterThan(1.9f), "Actually reached wall");
                player.testReset = true;
                yield return null;
                float reset = Vector3.Distance(player.spawnPosition, player.transform.position);
                Assert.That(reset, Is.LessThanOrEqualTo(0.01f));
                player.testReset = false;
                player.testLook = new Vector2(30, -20);
                yield return null;
                player.testLook = Vector2.zero;
                Assert.That(Mathf.Abs(player.Yaw), Is.GreaterThan(0.1f));
                Assert.That(Quaternion.Angle(Quaternion.identity, player.view.transform.localRotation), Is.GreaterThan(0.1f));
                metrics.Add($"projection={(orthographic ? "orthographic" : "perspective")} input=Update:testMove/testLook/testReset movement_m={moved:R} floor_error_m={support:R} wall_penetration_m={penetration:R} reset_error_m={reset:R}");
            }
            // Exercise the real Input System polling path separately from deterministic injection.
            var keyboard = InputSystem.AddDevice<Keyboard>();
            var mouse = InputSystem.AddDevice<Mouse>();
            var previousMode = InputSystem.settings.updateMode;
            var previousBackground = InputSystem.settings.backgroundBehavior;
            var previousEditorInput = InputSystem.settings.editorInputBehaviorInPlayMode;
            InputSystem.settings.updateMode = InputSettings.UpdateMode.ProcessEventsManually;
            // Headless Editor has no focused Game view. This is a test-only device routing override.
            InputSystem.settings.backgroundBehavior = InputSettings.BackgroundBehavior.IgnoreFocus;
            InputSystem.settings.editorInputBehaviorInPlayMode = InputSettings.EditorInputBehaviorInPlayMode.AllDeviceInputAlwaysGoesToGameView;
            InputSystem.EnableDevice(keyboard);
            InputSystem.EnableDevice(mouse);
            keyboard.MakeCurrent();
            mouse.MakeCurrent();
            try
            {
                player.injectedInput = false;
                foreach (Key key in new[] { Key.W, Key.A, Key.S, Key.D })
                {
                    player.Respawn();
                    Vector3 start = player.transform.position;
                    for (int i = 0; i < 50; i++)
                    {
                        InputSystem.QueueStateEvent(keyboard, new KeyboardState(key));
                        InputSystem.Update();
                        Assert.That(keyboard[key].isPressed, Is.True, "Virtual key reached Input System");
                        if (i == 0) metrics.Add($"queued key={key} frame={Time.frameCount} current_keyboard={Keyboard.current?.deviceId} expected_keyboard={keyboard.deviceId} pressed={keyboard[key].isPressed} phase={InputState.currentUpdateType} focus={Application.isFocused}");
                        yield return null;
                        if (i == 0 || i == 49) metrics.Add($"polled key={key} frame={Time.frameCount} poll_frame={player.LastPollFrame} current_keyboard={Keyboard.current?.deviceId} polled_keyboard={player.LastKeyboardId} expected_keyboard={keyboard.deviceId} current_pressed={keyboard[key].isPressed} polled_move={player.LastPolledMove} poll_phase={player.LastPollPhase} current_phase={InputState.currentUpdateType} position={player.transform.position:R} injected={player.injectedInput}");
                    }
                    Assert.That(new Vector2(player.transform.position.x - start.x,
                        player.transform.position.z - start.z).magnitude, Is.GreaterThan(0.25f), key.ToString());
                }
                InputSystem.QueueStateEvent(keyboard, new KeyboardState());
                InputSystem.QueueStateEvent(mouse, new MouseState { delta = new Vector2(80, -40) });
                InputSystem.Update();
                yield return null;
                Assert.That(player.Yaw, Is.GreaterThan(1));
                Assert.That(player.Pitch, Is.GreaterThan(1));
                InputSystem.QueueStateEvent(keyboard, new KeyboardState(Key.R));
                InputSystem.Update();
                yield return null;
                Assert.That(Vector3.Distance(player.spawnPosition, player.transform.position), Is.LessThanOrEqualTo(0.01f));
                metrics.Add("InputSystem virtual KeyboardState W/A/S/D/R and MouseState delta -> actual Update polling -> CharacterController.Move / camera rotation: passed; physical OS events not tested");
            }
            finally
            {
                player.injectedInput = true;
                InputSystem.RemoveDevice(keyboard);
                InputSystem.RemoveDevice(mouse);
                InputSystem.settings.updateMode = previousMode;
                InputSystem.settings.backgroundBehavior = previousBackground;
                InputSystem.settings.editorInputBehaviorInPlayMode = previousEditorInput;
                Time.captureDeltaTime = 0;
                File.WriteAllLines(Argument("--proscenium-result") + ".metrics.txt", metrics);
            }
        }
    }
}
#endif
