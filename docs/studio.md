# Studio guide

## Lexicon

| Term | Meaning |
| --- | --- |
| **Studio** | The complete desktop interface (`1920 × 1080` reference viewport). |
| **Studio header** | Top bar with **Projects**, workspace path, connection health, metadata refresh and configuration. |
| **Navigator** | Left panel containing **Shots**, **Outliner** and **Assets**. |
| **Review workspace** | Central panel containing review modes (**3D Inspection**, **Keyframes**, **Animation**), viewer and review controls. |
| **Viewport** | The active 3D, keyframe or animation viewing surface inside the Review workspace. |
| **Assistant panel** | Right panel containing **Copilot**, **Critic**, activity status, **Chat focus** and collapse controls. |
| **Conversation** | Scrollable Copilot log of requests, replies, tool/specialist activity, output cards and owner decisions. |
| **Composer** | Target bar, message input, attachments, split **Queue/Steer** Send button, **Stop** and **Jobs / Context / Agents** panel links. |
| **Splitter** | Keyboard-accessible draggable divider used to resize Navigator and Assistant panels. |
| **Review selection** | The published version, frame, camera and objects currently inspected in the Review workspace. |
| **Request target** | What the next Copilot message asks to create, discuss or revise. |
| **References** | Supporting published versions, images, annotations and approved VLM findings attached to a request. |

## Layouts and navigation

- **Split view:** Navigator, Review workspace and Assistant panel shown side by side.
- **Chat focus:** Assistant expands across the Review workspace's area while hiding Review. Navigator remains independently collapsible and resizable.
- **Collapse and resize:** Both Navigator and Assistant collapse into narrow icon rails and remember their Split-view widths per project. Drag a **Splitter** or focus it and use `ArrowLeft` / `ArrowRight` (`Shift` for larger steps).
- **Hidden Review safety:** Entering Chat focus preserves the selected version, camera, frame and unsaved annotation drafts while pausing playback, disabling hidden Review shortcuts and suspending 3D render loops until restored.
- **New projects:** Once project hydration completes, a newly created or empty project opens in **Chat focus** with Navigator collapsed so the Composer has room. Existing projects restore their saved layout or Split view.

## Starting work: assets, scenes or storyboards

Each project maintains one persistent Conversation. Storyboard-first is optional:

- **Optional starter actions:** Empty projects offer **Plan a storyboard**, **Create an asset** and **Build a scene**, or you can type any prompt directly. Starter buttons prepare a target and draft without sending a request or overwriting existing draft text.
- **Asset-first workflow:** Use **New asset** at the top of **Navigator → Assets** (or target a new asset in the Composer) to author, import or generate standalone assets without creating a storyboard first.
- **Pinned reuse:** Publishing a new asset version never silently updates existing scenes or storyboards. Dependent items adopt newer versions only through an explicit revision.

## Request targeting and references

The **Target** picker above the Composer separates what you are viewing from what you want Copilot to change:

- **Current view:** Captures the inspected Review item or storyboard shot at submission time (or **Project** when nothing is selected). Subsequent browsing never mutates an admitted request.
- **Project:** General project discussion, research or cross-item planning.
- **Specific asset, scene, storyboard or shot:** Pins an exact published version (including older versions) independently of the Review workspace.
- **New asset / scene / storyboard:** Marks creation intent without inventing a published version ID.
- **Explicit references:** Add supporting published versions (`reference_versions`) or uploaded images (`attachments`) without changing the primary Request target. Targeting `Project`, a `New` item or a different item never flushes an unrelated hidden Review annotation draft.

## Composer controls, counters and history

- **Split Send button:** Click the main button to submit using the active mode (**Queue** or **Steer**). Use the chevron menu to switch modes without sending.
- **Guarded Steer:** Steer is enabled only when eligible work is actively running or awaiting input with execution unfenced. If work finishes or pauses, your Steer selection and draft are preserved with an explicit **Switch to Queue** action.
- **Prompt history:** Press `ArrowUp` in an empty Composer (or with the caret at index `0`) to recall earlier project prompts, and `ArrowDown` to move forward back to your unsent draft. Recalling history restores text only, keeping your current target, mode and attachments intact.
- **Panel links:** **Jobs**, **Context** and **Agents** sit beneath the input as interactive pill links that temporarily replace the text box and restore your draft, caret and focus on close:
  - **Jobs:** Counts unfinished independent render, encode, generation, VLM and Unity jobs.
  - **Context:** Counts logical items in the next request (explicit target, reference versions, deduplicated images, object selections, annotations and approved findings), showing active uploads separately until ready.
  - **Agents:** Counts busy or uncertain product-native specialists (`subagent.*`), showing each specialist's task label, state, freshness and model without counting wrapper `task` calls.

## Live output, usage, failures and retained media

- **Live following:** Conversation follows new requests, streamed replies and tool updates automatically, pauses when you scroll up and offers **Jump to latest**. Empty tool-only assistant turns do not create blank message bubbles.
- **Copilot status:** The fixed Assistant header (and collapsed rail) displays **Sending…**, **Working…**, **Needs your input**, **Paused**, **Stopping…**, **Needs recovery** or **Ready**, marking disconnected states as reconnecting rather than assuming idle.
- **Usage readout:** Reported usage renders as a dim, right-aligned monospace line. Legacy `assistant.usage.cost` from the pinned SDK represents premium-request usage and is labelled **premium credits** without currency or AI-credit conversion ([Copilot SDK 1.0.34 usage reference](https://github.com/github/copilot-sdk/blob/v1.0.34/docs/features/usage-and-billing.md#per-call-token-counts)).
- **Actionable diagnostics:** Failed and uncertain tool calls expand their sanitised reason immediately while keeping technical IDs collapsed. Automatic policy rejections, owner declines, host holds, non-zero shell exit codes and structured schema/publication errors are distinguished explicitly.
- **Retained outputs vs publication:** Registered images remain viewable and downloadable through authenticated artefact cards in Conversation and **Navigator → Assets → Retained outputs** even when catalogue publication fails (**Finished · publication blocked**). Standalone concept images are never labelled as 3D meshes.

## Stopping, holding and explicit Resume

- **Queue holds:** When execution is paused or awaiting reconciliation, the Composer shows the hold reason before submission. Held Queue requests can be cancelled individually with **Cancel held request**.
- **Stop vs Resume:** **Stop** cancels Copilot work through verified native barriers without stopping independent external jobs. After a restart, native abort or uncertain Stop, use **Continue deliberately**:
  - **Reconcile only:** Inspects native state and settles proven barriers without sending a prompt.
  - **Continue original work:** Resumes the selected work with its latest acknowledged request context and original generation budget.
  - **Release held queue:** Unfences an idle project so held Queue requests can dispatch.
