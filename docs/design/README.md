# Linear Minimal desktop reference

The owner selected this visual treatment on 2026-11-14. The reference image and original mockup HTML are preserved byte-for-byte:

- [Reference image](proscenium_mockup.png)
- [Original mockup source](proscenium_ui_mockup.html)
- [Source hashes and capture metadata](manifest.json)

The HTML is an archival design artefact. It contains original CDN imports, simulated behaviour and obsolete labels. It is not a production entry point. Use this layout specification and the product contracts when implementing the application.

## Reference geometry

Acceptance viewport: **1920 × 1080 CSS pixels**, desktop Chromium/Brave/Edge. The supplied image is a visual reference rather than an exact-size regression fixture. The initial implementation geometry is:

| Region | Baseline |
| --- | --- |
| Header | 48 px high, full width. Project identity (saved-project dropdown plus workstation path text input for create/open/rebind), source/version and actual status/actions. |
| Left panel | 280 px wide, internally scrollable. Shots, Outliner and Assets tabs. |
| Right panel | 360 px wide, internally scrollable. Copilot/Critic content, decision cards and persistent message controls. |
| Centre | Remaining width, minimum zero in the layout grid. Exactly one review mode is mounted visibly. |
| Timeline | Up to 128 px beneath the viewer when animation/frame navigation is relevant. |
| Panel spacing | 12–16 px internal spacing; 8 px control/card radius. |
| Type | Inter, 13–14 px readable body/control text; JetBrains Mono, 11–12 px technical labels. |

These dimensions are initial engineering defaults which preserve the selected composition. Adjustments require a captured 1920×1080 review and must not obscure primary controls or introduce mobile breakpoints. Prefer internal scrolling and mode-appropriate tools over compressing labels into illegible text.

## Tokens

App/header `#09090b`; panels `#101013`; raised surfaces `#18181c`; inputs `#141418`; viewer background `#0c0c0f`. Borders `#222228` / `#32323a`. Text `#fafafa` / `#a1a1aa`; use muted `#52525b` only where readable at the actual size. Primary action `#e4e4e7`, restrained indigo `#6366f1`, success `#22c55e`. Bundle Inter and JetBrains Mono with their applicable notices in the implementation.

## Behavioural substitutions

- The header presents Git/source versions, not simulated `bpy` transaction undo/redo.
- The asset tab is active-project asset discovery/import, not Qdrant.
- Model names, progress, frame rates, dimensions and device information come from actual records.
- Queue and Steer carry immutable context. Stop is a separate high-visibility runtime control and shows requested/confirmed states.
- Critic approval submits selected findings to Copilot; it does not execute a UI repair function.
- The Unity action delegates to the specialist and produces an Editor-playable scene. No game HUD or cinematic gameplay transition is drawn over the review viewport.
- One theme and three review modes: 3D Inspection, Keyframes and Animation. A mode change does not itself start rendering.
- The outliner lists the published proxy mapping. A row selection is the same object selection as a proxy pick. It does not start a render.
- Selection context retains both object and instance IDs. Owner selections use typed item/storyboard-frame/sequence-entry slots. The UI reads original queued text and attachment context from `RequestRecord` after reconnect, including messages never dispatched to Copilot.
- Keyboard, when focus is not in a text field: `1` `2` `3` select 3D Inspection, Keyframes and Animation. Space plays or pauses animation. Left and Right step one frame. Shift+Left and Shift+Right change shot.
- The current image and drafts remain pinned while new render ranges or newer versions arrive. Progress is visible without retargeting the review.

The mockup's scene colours and dummy measurements are not authored scene requirements. UI styling must not retint the actual Blender images used for material judgement.
