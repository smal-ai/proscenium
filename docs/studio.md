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

Open **Services & models** (labelled with the active authoring profile in the header)
to inspect bounded live provider status and workstation bridge configuration. Use
**New profile** or **Edit** to set Copilot lead and worker models, then select the
profile for this project. **Use for this project** selects a newly saved profile.
The generation-attempt limit is editable even before an explicit profile selection.
Changes affect future requests, not settings frozen into existing work. Refreshing
status does not start services or consume generation attempts.

- **Split view:** Navigator, Review workspace and Assistant panel shown side by side.
- **Chat focus:** Assistant expands across the Review workspace's area while hiding Review. Navigator remains independently collapsible and resizable. Selecting any catalogue item, version, storyboard shot or retained output automatically restores **Split view** so the chosen media opens immediately in the Review workspace.
- **Collapse and resize:** Both Navigator and Assistant collapse into narrow icon rails and remember their Split-view widths per project. Drag a **Splitter** or focus it and use `ArrowLeft` / `ArrowRight` (`Shift` for larger steps).
- **Hidden Review safety:** Entering Chat focus preserves the selected version, camera, frame and unsaved annotation drafts while pausing playback, disabling hidden Review shortcuts and suspending 3D render loops until restored.
- **Review controls:** A unified command toolbar groups the mode switcher, annotation/details drawers and contextual actions above the viewport. The compact transport toolbar keeps frame stepping, pass selection and media metadata below it. An open annotation draft replaces both bars with drawing tools and a feedback dock. Outside text fields and annotation drafts, use `1`–`3` for **3D Inspection**, **Keyframes** and **Animation**, `Space` for progressive animation playback, and `←` / `→` to step frames (`Shift` to step shots).
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
  - **Jobs:** Highlights unfinished independent render, encode, generation, VLM and Unity jobs (`N active` with an active indicator), retains settled history (`N done` or attention state on failure/uncertainty) and shows `0` when no jobs exist.
  - **Context:** Counts logical items in the next request (explicit target, reference versions, deduplicated images, object selections, annotations and approved findings), showing active uploads separately until ready.
  - **Agents:** Counts busy or uncertain product-native specialists (`subagent.*`), showing each specialist's task label, state, freshness and model without counting wrapper `task` calls.

## Live output, usage, failures and retained media

- **Live following:** Conversation follows new requests, streamed replies and tool updates automatically, pauses when you scroll up and offers **Jump to latest**. Empty tool-only assistant turns do not create blank message bubbles.
- **Copilot status:** Anchored at the bottom right of the Assistant panel (Composer footer, Critic footer and bottom of the collapsed right rail), displaying **Sending…**, **Working…** (with live active tool, specialist or running-job step detail), **Needs your input**, **Paused**, **Stopping…**, **Needs recovery** or **Ready**, and marking disconnected states as reconnecting rather than assuming idle.
- **Usage readout:** Reported usage renders as a dim, right-aligned monospace line. Legacy `assistant.usage.cost` from the pinned SDK represents premium-request usage and is labelled **premium credits** without currency or AI-credit conversion ([Copilot SDK 1.0.34 usage reference](https://github.com/github/copilot-sdk/blob/v1.0.34/docs/features/usage-and-billing.md#per-call-token-counts)).
- **Actionable diagnostics:** Failed and uncertain tool calls expand their sanitised reason immediately while keeping technical IDs collapsed. Automatic policy rejections, owner declines, host holds, non-zero shell exit codes and structured schema/publication errors are distinguished explicitly.
- **Retained outputs vs publication:** Registered images remain viewable and downloadable through authenticated artefact cards in Conversation and **Navigator → Assets → Retained outputs** even when catalogue publication fails (**Finished · publication blocked**). Clicking a retained image opens it directly in the Review workspace without fabricating a catalogue version, and selecting a published storyboard displays its ready `ShotPlan` panels in **Keyframes** mode. Standalone concept images are never labelled as 3D meshes.

## Stopping, holding and explicit Resume

- **Queue holds:** When execution is genuinely fenced, paused or awaiting reconciliation (never during ordinary active execution), the Composer shows the hold reason before submission. Held Queue requests can be cancelled individually with **Cancel held request**.
- **Stop vs Resume:** **Stop** cancels Copilot work through verified native barriers without stopping independent external jobs. After a restart, native abort or uncertain Stop, the **Resume Copilot** controls stay visible directly above the Composer, outside the scrollable Conversation. Choose a mode, then click **Resume**:
  - **Reconcile only:** Inspects native state and settles proven barriers without sending a prompt.
  - **Continue original work:** Resumes the selected work with its latest acknowledged request context and original generation budget.
  - **Release held queue:** Unfences an idle project so held Queue requests can dispatch.
