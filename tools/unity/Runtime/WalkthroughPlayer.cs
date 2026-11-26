using System;
using UnityEngine;
using UnityEngine.InputSystem;
using UnityEngine.InputSystem.LowLevel;

namespace Proscenium.Unity
{
    [RequireComponent(typeof(CharacterController))]
    public sealed class WalkthroughPlayer : MonoBehaviour
    {
        public Camera view;
        public Vector3 spawnPosition;
        public float spawnYaw;
        public float speed = 2;
        public float lookSensitivity = 0.1f;
        public bool injectedInput;
        public Vector2 testMove;
        public Vector2 testLook;
        public bool testReset;
        public bool pauseMenuEnabled = true;
        public bool IsPaused { get; private set; }
        public event Action<bool> PauseChanged;
        public float Yaw { get; private set; }
        public float Pitch { get; private set; }
        public Vector2 LastPolledMove { get; private set; }
        public Vector2 LastPolledLook { get; private set; }
        public bool LastPolledReset { get; private set; }
        public int LastKeyboardId { get; private set; } = -1;
        public int LastMouseId { get; private set; } = -1;
        public int LastPollFrame { get; private set; } = -1;
        public string LastPollPhase { get; private set; }
        private CharacterController controller;
        private float verticalSpeed;
        private float previousTimeScale = 1f;
        private bool timeScalePaused;

        private void Awake()
        {
            controller = GetComponent<CharacterController>();
            Yaw = spawnYaw;
        }

        private void Update()
        {
            Vector2 move = testMove;
            Vector2 look = testLook;
            bool reset = testReset;
            if (!injectedInput)
            {
                var keyboard = Keyboard.current;
                var mouse = Mouse.current;
                LastKeyboardId = keyboard == null ? -1 : keyboard.deviceId;
                LastMouseId = mouse == null ? -1 : mouse.deviceId;
                LastPollFrame = Time.frameCount;
                LastPollPhase = InputState.currentUpdateType.ToString();
                if (keyboard != null && keyboard.escapeKey.wasPressedThisFrame)
                {
                    if (pauseMenuEnabled) SetPaused(!IsPaused);
                    else ReleaseCursor();
                }
                if (IsPaused)
                {
                    LastPolledMove = Vector2.zero;
                    LastPolledLook = Vector2.zero;
                    LastPolledReset = false;
                    ReleaseCursor();
                    return;
                }

                move = keyboard == null ? Vector2.zero : new Vector2(
                    (keyboard.dKey.isPressed ? 1 : 0) - (keyboard.aKey.isPressed ? 1 : 0),
                    (keyboard.wKey.isPressed ? 1 : 0) - (keyboard.sKey.isPressed ? 1 : 0));
                look = mouse == null ? Vector2.zero : mouse.delta.ReadValue();
                reset = keyboard != null && keyboard.rKey.wasPressedThisFrame;
                if (mouse != null && mouse.leftButton.wasPressedThisFrame)
                    CaptureCursor();
                LastPolledMove = move;
                LastPolledLook = look;
                LastPolledReset = reset;
            }
            if (IsPaused) return;
            if (reset || transform.position.y < spawnPosition.y - 20)
            {
                Respawn();
                return;
            }
            Tick(move, look, Mathf.Min(Time.deltaTime, 0.05f));
        }

        public void SetPaused(bool paused)
        {
            if (paused == IsPaused)
            {
                if (paused) ReleaseCursor();
                return;
            }

            IsPaused = paused;
            if (paused)
            {
                previousTimeScale = Time.timeScale;
                Time.timeScale = 0f;
                timeScalePaused = true;
                ReleaseCursor();
            }
            else
            {
                RestoreTimeScale();
                if (!injectedInput && Application.isFocused) CaptureCursor();
            }
            PauseChanged?.Invoke(IsPaused);
        }

        private static void ReleaseCursor()
        {
            Cursor.lockState = CursorLockMode.None;
            Cursor.visible = true;
        }

        private static void CaptureCursor()
        {
            Cursor.lockState = CursorLockMode.Locked;
            Cursor.visible = false;
        }

        private void RestoreTimeScale()
        {
            if (!timeScalePaused) return;
            Time.timeScale = previousTimeScale;
            timeScalePaused = false;
        }

        private void OnApplicationFocus(bool hasFocus)
        {
            if (!hasFocus) ReleaseCursor();
        }

        private void ReleasePause()
        {
            RestoreTimeScale();
            ReleaseCursor();
            if (!IsPaused) return;
            IsPaused = false;
            PauseChanged?.Invoke(IsPaused);
        }

        private void OnDisable() => ReleasePause();
        private void OnDestroy() => ReleasePause();

        public void Tick(Vector2 move, Vector2 mouseDelta, float dt)
        {
            Yaw += mouseDelta.x * lookSensitivity;
            Pitch = Mathf.Clamp(Pitch - mouseDelta.y * lookSensitivity, -80, 80);
            transform.rotation = Quaternion.Euler(0, Yaw, 0);
            if (view != null) view.transform.localRotation = Quaternion.Euler(Pitch, 0, 0);
            Vector3 direction = transform.rotation * Vector3.ClampMagnitude(
                new Vector3(move.x, 0, move.y), 1);
            verticalSpeed = controller.isGrounded ? -2 : Mathf.Max(-30, verticalSpeed - 20 * dt);
            controller.Move((direction * speed + Vector3.up * verticalSpeed) * dt);
        }

        public void Respawn()
        {
            controller.enabled = false;
            transform.SetPositionAndRotation(spawnPosition, Quaternion.Euler(0, spawnYaw, 0));
            controller.enabled = true;
            verticalSpeed = 0;
            Yaw = spawnYaw;
            Pitch = 0;
            if (view != null) view.transform.localRotation = Quaternion.identity;
        }
    }
}
