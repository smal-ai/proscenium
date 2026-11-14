using UnityEngine;
using UnityEngine.InputSystem;

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
        public float Yaw { get; private set; }
        public float Pitch { get; private set; }
        private CharacterController controller;
        private float verticalSpeed;

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
                move = keyboard == null ? Vector2.zero : new Vector2(
                    (keyboard.dKey.isPressed ? 1 : 0) - (keyboard.aKey.isPressed ? 1 : 0),
                    (keyboard.wKey.isPressed ? 1 : 0) - (keyboard.sKey.isPressed ? 1 : 0));
                look = mouse == null ? Vector2.zero : mouse.delta.ReadValue();
                reset = keyboard != null && keyboard.rKey.wasPressedThisFrame;
                if (keyboard != null && keyboard.escapeKey.wasPressedThisFrame)
                    Cursor.lockState = CursorLockMode.None;
                if (mouse != null && mouse.leftButton.wasPressedThisFrame)
                    Cursor.lockState = CursorLockMode.Locked;
            }
            if (reset || transform.position.y < spawnPosition.y - 20)
            {
                Respawn();
                return;
            }
            Tick(move, look, Mathf.Min(Time.deltaTime, 0.05f));
        }

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
