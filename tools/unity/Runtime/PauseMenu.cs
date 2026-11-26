using UnityEngine;

namespace Proscenium.Unity
{
    [RequireComponent(typeof(WalkthroughPlayer))]
    public sealed class PauseMenu : MonoBehaviour
    {
        public WalkthroughPlayer player;
        public string title = "Paused";
        public string controlsHint = "WASD Move  ·  Mouse Look  ·  R Reset  ·  Esc Toggle Pause";
        public bool IsVisible => player != null && player.IsPaused;
        public bool QuitRequested { get; private set; }
        public bool suppressQuitForTests;

        private const float CardWidth = 420f;
        private const float CardHeight = 320f;
        private Texture2D backdrop, border, panel, buttonNormal, buttonHover, buttonActive;
        private GUIStyle titleStyle, hintStyle, buttonStyle;

        private void Awake()
        {
            if (player == null) player = GetComponent<WalkthroughPlayer>();
        }

        public void Resume() => player?.SetPaused(false);

        public void ResetAndResume()
        {
            player?.Respawn();
            player?.SetPaused(false);
        }

        public void Quit()
        {
            QuitRequested = true;
            player?.SetPaused(false);
            Cursor.lockState = CursorLockMode.None;
            Cursor.visible = true;
            if (suppressQuitForTests) return;
#if UNITY_EDITOR
            UnityEditor.EditorApplication.isPlaying = false;
#else
            Application.Quit();
#endif
        }

        private void OnGUI()
        {
            if (!IsVisible) return;
            if (buttonStyle == null) CreateStyles();

            GUI.DrawTexture(new Rect(0, 0, Screen.width, Screen.height), backdrop);
            float scale = Mathf.Clamp(Mathf.Min(Screen.width / 1280f, Screen.height / 720f), 0.7f, 2f);
            var origin = new Vector3(Mathf.Round((Screen.width - CardWidth * scale) / 2f),
                Mathf.Round((Screen.height - CardHeight * scale) / 2f), 0);
            Matrix4x4 previousMatrix = GUI.matrix;
            GUI.matrix = previousMatrix * Matrix4x4.TRS(origin, Quaternion.identity, new Vector3(scale, scale, 1));
            try
            {
                GUI.DrawTexture(new Rect(0, 0, CardWidth, CardHeight), border);
                GUI.DrawTexture(new Rect(1, 1, CardWidth - 2, CardHeight - 2), panel);
                GUI.Label(new Rect(24, 24, CardWidth - 48, 44), title, titleStyle);
                GUI.Label(new Rect(24, 72, CardWidth - 48, 34), controlsHint, hintStyle);

                var button = new Rect(24, 124, CardWidth - 48, 48);
                if (GUI.Button(button, "Resume", buttonStyle)) Resume();
                button.y += 60;
                if (GUI.Button(button, "Reset Position", buttonStyle)) ResetAndResume();
                button.y += 60;
                if (GUI.Button(button, "Quit", buttonStyle)) Quit();
            }
            finally
            {
                GUI.matrix = previousMatrix;
            }
        }

        private void CreateStyles()
        {
            backdrop = Swatch(new Color(0.04f, 0.05f, 0.08f, 0.72f));
            border = Swatch(new Color(0.24f, 0.30f, 0.37f));
            panel = Swatch(new Color(0.10f, 0.12f, 0.15f));
            buttonNormal = Swatch(new Color(0.16f, 0.20f, 0.25f));
            buttonHover = Swatch(new Color(0.22f, 0.29f, 0.36f));
            buttonActive = Swatch(new Color(0.28f, 0.37f, 0.45f));
            titleStyle = new GUIStyle(GUI.skin.label) {
                alignment = TextAnchor.MiddleCenter, fontSize = 32, fontStyle = FontStyle.Bold,
                normal = { textColor = Color.white }
            };
            hintStyle = new GUIStyle(GUI.skin.label) {
                alignment = TextAnchor.MiddleCenter, fontSize = 14, wordWrap = false,
                normal = { textColor = new Color(0.86f, 0.90f, 0.95f) }
            };
            buttonStyle = new GUIStyle {
                alignment = TextAnchor.MiddleCenter, fontSize = 18,
                normal = { background = buttonNormal, textColor = Color.white },
                hover = { background = buttonHover, textColor = Color.white },
                active = { background = buttonActive, textColor = Color.white },
                focused = { background = buttonHover, textColor = Color.white }
            };
        }

        private static Texture2D Swatch(Color colour)
        {
            var texture = new Texture2D(1, 1, TextureFormat.RGBA32, false) {
                hideFlags = HideFlags.HideAndDontSave, wrapMode = TextureWrapMode.Clamp
            };
            texture.SetPixel(0, 0, colour);
            texture.Apply(false, true);
            return texture;
        }

        private void OnDestroy()
        {
            if (backdrop == null) return;
            Destroy(backdrop);
            Destroy(border);
            Destroy(panel);
            Destroy(buttonNormal);
            Destroy(buttonHover);
            Destroy(buttonActive);
        }
    }
}
