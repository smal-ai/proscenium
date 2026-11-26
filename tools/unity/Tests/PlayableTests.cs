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
using UnityEngine.Rendering;
using UnityEngine.SceneManagement;
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

        private readonly List<string> metrics = new List<string>();
        private Keyboard keyboard;
        private Mouse mouse;
        private WalkthroughPlayer player;

        private static string Argument(string key)
        {
            var args = Environment.GetCommandLineArgs();
            return args[Array.IndexOf(args, key) + 1];
        }

        private void Send(Key? key = null, Vector2? look = null)
        {
            InputSystem.QueueStateEvent(keyboard, new KeyboardState(
                key.HasValue ? new[] { key.Value } : Array.Empty<Key>()));
            InputSystem.QueueStateEvent(mouse, new MouseState { delta = look ?? Vector2.zero });
            InputSystem.Update();
            Assert.That(Keyboard.current, Is.SameAs(keyboard));
            Assert.That(Mouse.current, Is.SameAs(mouse));
            if (key.HasValue) Assert.That(keyboard[key.Value].isPressed, Is.True);
        }

        private void AssertPoll(Vector2 movement, Vector2 look, bool reset = false)
        {
            // These observations are captured inside the production controller's Update,
            // after Keyboard.current/Mouse.current polling, not by the test event producer.
            Assert.That(player.injectedInput, Is.False);
            Assert.That(player.LastKeyboardId, Is.EqualTo(keyboard.deviceId));
            Assert.That(player.LastMouseId, Is.EqualTo(mouse.deviceId));
            Assert.That(player.LastPolledMove, Is.EqualTo(movement));
            Assert.That(player.LastPolledLook, Is.EqualTo(look));
            Assert.That(player.LastPolledReset, Is.EqualTo(reset));
            Assert.That(player.LastPollPhase, Is.EqualTo("Manual"));
        }

        private float AssertReset(string label)
        {
            AssertPoll(Vector2.zero, Vector2.zero, true);
            float error = Vector3.Distance(player.spawnPosition, player.transform.position);
            Assert.That(error, Is.LessThanOrEqualTo(0.01f), label);
            Assert.That(Mathf.Abs(Mathf.DeltaAngle(player.spawnYaw, player.Yaw)), Is.LessThanOrEqualTo(0.01f));
            Assert.That(Mathf.Abs(player.Pitch), Is.LessThanOrEqualTo(0.01f));
            Assert.That(Quaternion.Angle(Quaternion.identity, player.view.transform.localRotation), Is.LessThanOrEqualTo(0.01f));
            return error;
        }

        [UnityTest]
        public IEnumerator F05_KeyboardMousePolling_RealController_BothCameraProjections()
        {
            float previousCaptureDelta = Time.captureDeltaTime;
            var settings = InputSystem.settings;
            var previousMode = settings.updateMode;
            var previousBackground = settings.backgroundBehavior;
            var previousEditorInput = settings.editorInputBehaviorInPlayMode;
            bool originalInjected = false;
            try
            {
                // Fix simulation delta, not movement or collision outcomes. All controls
                // below still traverse virtual devices -> actual Update -> real PhysX.
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
                player = UnityEngine.Object.FindFirstObjectByType<WalkthroughPlayer>();
                Assert.That(player, Is.Not.Null);
                Assert.That(GameObject.Find("Unrelated acceptance sentinel"), Is.Not.Null);
                var controller = player.GetComponent<CharacterController>();
                Assert.That(controller.height, Is.EqualTo(1.8f));
                Assert.That(controller.radius, Is.EqualTo(0.25f));
                originalInjected = player.injectedInput;
                player.injectedInput = false;
                keyboard = InputSystem.AddDevice<Keyboard>();
                mouse = InputSystem.AddDevice<Mouse>();
                settings.updateMode = InputSettings.UpdateMode.ProcessEventsManually;
                // Headless Editor has no focused Game view. These overrides are test-only.
                settings.backgroundBehavior = InputSettings.BackgroundBehavior.IgnoreFocus;
                settings.editorInputBehaviorInPlayMode = InputSettings.EditorInputBehaviorInPlayMode.AllDeviceInputAlwaysGoesToGameView;
                InputSystem.EnableDevice(keyboard);
                InputSystem.EnableDevice(mouse);
                keyboard.MakeCurrent();
                mouse.MakeCurrent();
                metrics.Add($"path=Keyboard.current/Mouse.current->WalkthroughPlayer.Update->CharacterController.Move/Camera virtual_keyboard={keyboard.deviceId} virtual_mouse={mouse.deviceId} focus={Application.isFocused} update_mode={settings.updateMode} background={settings.backgroundBehavior} editor_routing={settings.editorInputBehaviorInPlayMode} physical_OS_input=not_tested");

                foreach (bool orthographic in new[] { false, true })
                {
                    string projection = orthographic ? "orthographic" : "perspective";
                    player.view.orthographic = orthographic;
                    Send(Key.R);
                    yield return null;
                    float resetError = AssertReset("Initial R reset");
                    for (int i = 0; i < 80; i++) { Send(); yield return null; }
                    AssertPoll(Vector2.zero, Vector2.zero);
                    float support = Mathf.Abs(player.transform.position.y - controller.height / 2);
                    Assert.That(support, Is.LessThanOrEqualTo(0.05f), "Floor support");

                    Vector3 start = player.transform.position;
                    for (int i = 0; i < 90; i++) { Send(Key.W); yield return null; }
                    AssertPoll(Vector2.up, Vector2.zero);
                    float moved = Vector3.Distance(start, player.transform.position);
                    Assert.That(moved, Is.GreaterThan(0.25f), "Polled W movement");
                    for (int i = 0; i < 300; i++) { Send(Key.W); yield return null; }
                    AssertPoll(Vector2.up, Vector2.zero);
                    float penetration = Mathf.Max(0, player.transform.position.z + controller.radius - 2.4f);
                    Assert.That(penetration, Is.LessThan(0.02f), "Wall penetration");
                    Assert.That(player.transform.position.z, Is.GreaterThan(1.9f), "Actually reached wall");
                    Send(Key.R);
                    yield return null;
                    resetError = Mathf.Max(resetError, AssertReset("R reset from wall"));
                    Send();
                    yield return null;

                    foreach (Key key in new[] { Key.W, Key.A, Key.S, Key.D })
                    {
                        Send(Key.R);
                        yield return null;
                        resetError = Mathf.Max(resetError, AssertReset("R reset before direction"));
                        Send();
                        yield return null;
                        start = player.transform.position;
                        for (int i = 0; i < 20; i++) { Send(key); yield return null; }
                        Vector2 expected = key == Key.W ? Vector2.up : key == Key.S ? Vector2.down : key == Key.A ? Vector2.left : Vector2.right;
                        AssertPoll(expected, Vector2.zero);
                        float distance = new Vector2(player.transform.position.x - start.x, player.transform.position.z - start.z).magnitude;
                        Assert.That(distance, Is.GreaterThan(0.25f), "Polled " + key);
                        metrics.Add($"projection={projection} key={key} movement_m={distance:R} current_keyboard={Keyboard.current.deviceId} polled_keyboard={player.LastKeyboardId} poll_frame={player.LastPollFrame} phase={player.LastPollPhase} polled_move={player.LastPolledMove}");
                    }

                    Quaternion beforeLook = player.view.transform.rotation;
                    var delta = new Vector2(80, -40);
                    Send(look: delta);
                    yield return null;
                    AssertPoll(Vector2.zero, delta);
                    Assert.That(Mathf.Abs(Mathf.DeltaAngle(player.spawnYaw, player.Yaw)), Is.GreaterThan(1));
                    Assert.That(player.Pitch, Is.GreaterThan(1));
                    float cameraAngle = Quaternion.Angle(beforeLook, player.view.transform.rotation);
                    Assert.That(cameraAngle, Is.GreaterThan(1));
                    metrics.Add($"projection={projection} mouse_delta={player.LastPolledLook} current_mouse={Mouse.current.deviceId} polled_mouse={player.LastMouseId} camera_rotation_deg={cameraAngle:R} yaw_deg={player.Yaw:R} pitch_deg={player.Pitch:R}");
                    Send(Key.R);
                    yield return null;
                    resetError = Mathf.Max(resetError, AssertReset("Polled R resets mouse view and spawn"));
                    Send();
                    yield return null;
                    metrics.Add($"projection={projection} input=Keyboard.current/Mouse.current->Update injected=False movement_m={moved:R} floor_error_m={support:R} wall_penetration_m={penetration:R} reset_error_m={resetError:R} camera_rotation_deg={cameraAngle:R}");
                }
            }
            finally
            {
                if (player != null) player.injectedInput = originalInjected;
                if (keyboard != null) InputSystem.RemoveDevice(keyboard);
                if (mouse != null) InputSystem.RemoveDevice(mouse);
                settings.updateMode = previousMode;
                settings.backgroundBehavior = previousBackground;
                settings.editorInputBehaviorInPlayMode = previousEditorInput;
                Time.captureDeltaTime = previousCaptureDelta;
                bool restored = settings.updateMode == previousMode && settings.backgroundBehavior == previousBackground
                    && settings.editorInputBehaviorInPlayMode == previousEditorInput && Time.captureDeltaTime == previousCaptureDelta;
                metrics.Add($"test_settings_restored={restored} update_mode={settings.updateMode} background={settings.backgroundBehavior} editor_routing={settings.editorInputBehaviorInPlayMode} capture_delta={Time.captureDeltaTime:R}");
                File.WriteAllLines(Argument("--proscenium-result") + ".metrics.txt", metrics);
                Assert.That(restored, Is.True, "Restore every test-only routing/time setting");
            }
        }

        [UnityTest]
        public IEnumerator F06_EscapePauseMenu_TogglesPauseCursorAndMovement()
        {
            float previousCaptureDelta = Time.captureDeltaTime;
            float previousTimeScale = Time.timeScale;
            var settings = InputSystem.settings;
            var previousMode = settings.updateMode;
            var previousBackground = settings.backgroundBehavior;
            var previousEditorInput = settings.editorInputBehaviorInPlayMode;
            bool originalInjected = false;
            player = null;
            keyboard = null;
            mouse = null;
            try
            {
                Time.timeScale = 1f;
                Time.captureDeltaTime = 1f / 60;
                var recipe = JsonUtility.FromJson<TestRecipe>(File.ReadAllText(Argument("--proscenium-recipe")));
                yield return EditorSceneManager.LoadSceneAsyncInPlayMode(recipe.scene_path,
                    new LoadSceneParameters(LoadSceneMode.Single));
                player = UnityEngine.Object.FindFirstObjectByType<WalkthroughPlayer>();
                Assert.That(player, Is.Not.Null);
                originalInjected = player.injectedInput;
                var pauseMenu = player.GetComponent<PauseMenu>();
                Assert.That(pauseMenu, Is.Not.Null);
                Assert.That(pauseMenu.player, Is.SameAs(player));

                player.injectedInput = false;
                keyboard = InputSystem.AddDevice<Keyboard>();
                mouse = InputSystem.AddDevice<Mouse>();
                settings.updateMode = InputSettings.UpdateMode.ProcessEventsManually;
                settings.backgroundBehavior = InputSettings.BackgroundBehavior.IgnoreFocus;
                settings.editorInputBehaviorInPlayMode = InputSettings.EditorInputBehaviorInPlayMode.AllDeviceInputAlwaysGoesToGameView;
                InputSystem.EnableDevice(keyboard);
                InputSystem.EnableDevice(mouse);
                keyboard.MakeCurrent();
                mouse.MakeCurrent();

                // Align after Update so the first manual Escape edge is polled only once.
                yield return null;
                Send(Key.Escape);
                yield return null;
                Assert.That(player.IsPaused, Is.True);
                Assert.That(pauseMenu.IsVisible, Is.True);
                Assert.That(Cursor.lockState, Is.EqualTo(CursorLockMode.None));
                Assert.That(Cursor.visible, Is.True);
                Assert.That(Time.timeScale, Is.Zero);

                Vector3 position = player.transform.position;
                float yaw = player.Yaw;
                float pitch = player.Pitch;
                Send(Key.W, new Vector2(80, -40));
                yield return null;
                Assert.That(player.transform.position, Is.EqualTo(position));
                Assert.That(player.Yaw, Is.EqualTo(yaw));
                Assert.That(player.Pitch, Is.EqualTo(pitch));
                AssertPoll(Vector2.zero, Vector2.zero);

                Send();
                yield return null;
                Send(Key.Escape);
                yield return null;
                Assert.That(player.IsPaused, Is.False);
                Assert.That(pauseMenu.IsVisible, Is.False);
                Assert.That(Time.timeScale, Is.EqualTo(1f));
                player.Respawn();
                for (int i = 0; i < 20; i++) { Send(Key.W); yield return null; }
                position = player.transform.position;
                Assert.That(Vector3.Distance(position, player.spawnPosition), Is.GreaterThan(0.1f));

                Time.timeScale = 0.5f;
                Send(Key.Escape);
                yield return null;
                Assert.That(player.IsPaused, Is.True);
                InputSystem.QueueStateEvent(keyboard, new KeyboardState(Key.R));
                var mouseState = new MouseState().WithButton(MouseButton.Left);
                InputSystem.QueueStateEvent(mouse, mouseState);
                InputSystem.Update();
                Assert.That(keyboard.rKey.isPressed, Is.True);
                Assert.That(mouse.leftButton.isPressed, Is.True);
                yield return null;
                Assert.That(player.transform.position, Is.EqualTo(position));
                AssertPoll(Vector2.zero, Vector2.zero);
                Assert.That(Cursor.lockState, Is.EqualTo(CursorLockMode.None));
                Assert.That(Cursor.visible, Is.True);

                pauseMenu.ResetAndResume();
                Assert.That(player.transform.position, Is.EqualTo(player.spawnPosition));
                Assert.That(player.IsPaused, Is.False);
                Assert.That(pauseMenu.IsVisible, Is.False);
                Assert.That(Time.timeScale, Is.EqualTo(0.5f));

                Time.timeScale = 0f;
                player.SetPaused(true);
                Assert.That(player.IsPaused, Is.True);
                pauseMenu.Resume();
                Assert.That(player.IsPaused, Is.False);
                Assert.That(Time.timeScale, Is.Zero, "Resume preserves an externally paused clock");

                player.SetPaused(true);
                player.enabled = false;
                float disabledTimeScale = Time.timeScale;
                player.enabled = true;
                Assert.That(disabledTimeScale, Is.Zero);
                Assert.That(Time.timeScale, Is.Zero, "Disable and re-enable preserve an externally paused clock");
                Assert.That(player.IsPaused, Is.False);

                pauseMenu.suppressQuitForTests = true;
                foreach (float initialTimeScale in new[] { 1f, 0f })
                {
                    Time.timeScale = initialTimeScale;
                    player.SetPaused(true);
                    pauseMenu.Quit();
                    Assert.That(pauseMenu.QuitRequested, Is.True);
                    Assert.That(pauseMenu.IsVisible, Is.False);
                    Assert.That(Cursor.lockState, Is.EqualTo(CursorLockMode.None));
                    Assert.That(Cursor.visible, Is.True);
                    Assert.That(Time.timeScale, Is.EqualTo(initialTimeScale));
                }
            }
            finally
            {
                if (player != null)
                {
                    player.injectedInput = true;
                    player.SetPaused(false);
                    player.injectedInput = originalInjected;
                }
                if (keyboard != null) InputSystem.RemoveDevice(keyboard);
                if (mouse != null) InputSystem.RemoveDevice(mouse);
                settings.updateMode = previousMode;
                settings.backgroundBehavior = previousBackground;
                settings.editorInputBehaviorInPlayMode = previousEditorInput;
                Time.captureDeltaTime = previousCaptureDelta;
                Time.timeScale = previousTimeScale;
                Cursor.lockState = CursorLockMode.None;
                Cursor.visible = true;
            }
        }
    }
}
#endif
