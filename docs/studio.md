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
- **Review controls:** A unified command toolbar groups the mode switcher, annotation/details drawers and contextual actions above the viewport. The compact transport toolbar keeps frame stepping, pass selection and media metadata below it. An open annotation draft replaces both bars with drawing tools and a feedback dock. Outside text fields and annotation drafts, use `1`–`3` for **3D Inspection**, **Keyframes** and **Animation**, `Space` for progressive animation playback, and `←` / `→` to step frames (`Shift` to step passes).
- **New projects:** Once project hydration completes, a newly created or empty project opens in **Chat focus** with Navigator collapsed so the Composer has room. Existing projects restore their saved layout or Split view.
- **First open of an existing storyboard project:** In a browser without a saved layout or viewed version, Studio opens in **Split view** with **Shots** selected and the first ready panel in **Keyframes**. It uses the owner's selected storyboard version when available, otherwise the current version. Any interaction during loading takes precedence.

## Starting work: assets, scenes or storyboards

Each project maintains one persistent Conversation. Storyboard-first is optional:

- **Optional starter actions:** Empty projects offer **Plan a storyboard**, **Create an asset** and **Build a scene**, or you can type any prompt directly. Starter buttons prepare a target and draft without sending a request or overwriting existing draft text.
- **Develop a storyboard together:** In the Composer, try “Pitch a complete timed storyboard for this brief, then refine it with me one shot at a time.” Copilot presents the whole board in Conversation first. Start with the opening shot or name another, then revise it until you want to move on. For a published board, use **Target → Existing…** to pin the version and shot. Discussing a pitch does not publish it or generate panels.
- **Define hero assets from a storyboard:** Ask Copilot for a complete hero-asset shortlist in Conversation, then refine one asset at a time. Explicitly ask for concept generation when ready. Copilot can use ready shot panels or attached sketches as references for a standalone asset concept without overwriting storyboard frames.
- **Asset-first workflow:** Use **New asset** at the top of **Navigator → Assets** (or target a new asset in the Composer) to author, import or generate standalone assets without creating a storyboard first.
- **Version history:** Assets starts with version histories collapsed. Expand an item to inspect its versions without changing the owner selection. Only one item or version `⋯` disclosure stays open at a time. Press `Escape` to close it and return focus to its button. **Check selection status** appears only when a selection change needs reconciliation.
- **Pinned reuse:** Publishing a new asset version never silently updates existing scenes or storyboards. Dependent items adopt newer versions only through an explicit revision.

## Reviewing published versions

- **Version status:** The Review header shows **Viewing vX · vY available** when there is a newer publication. Use **View vY** to switch. Unsaved drafts stay on their original version. Dismiss **Draft kept on vX** without changing the draft.
- **3D Inspection:** **Render output format** offers **16:9 · 1920 × 1080**, **16:10 · 1920 × 1200** and **Match viewport**. Fixed formats show a live safe frame inside the canvas, including when inspecting through a restored render camera. Spatial pin placement and editing hide the guide. **Render this view** captures the framed composition at the selected camera aspect, or uses the full viewport at its native pixel size for **Match viewport**. If upload fails, **Retry captured view upload** uses the same capture, camera and retained dimensions rather than taking a new image.
- **Keyframes:** Arrows step between rendered frames in the selected pass, including over gaps. A pass with one rendered frame disables both arrows. If you enter an unavailable frame, the transport explains why and keeps the frame number in sync with the image shown.
- **Pass order:** **Pass** lists numbered shots or scenes with their frame ranges and render purposes. The published preview order takes precedence. Unlisted passes follow by frame range, then by natural shot or scene name. Proxy jobs stay out of the list. Changes made with `set_preview_order` refresh the list without reopening the version.
- **Animation:** **Play** plays the active pass. **Play all** starts at the first animation pass in the list and advances through the remaining passes in order. Playback does not skip a missing scene or substitute another frame for annotation. Press `Space` to pause or resume the current run.
- **Annotations:** The drawer starts on **This version**. Switch to **All versions of this item** to see historical notes. Opening a historical note takes you to its original version.
- **Annotation actions:** The 2D editor and 3D pin dock group **Keep draft & close**, **Discard draft** or **Discard edits**, **Save annotation** and **Queue feedback** at the bottom. **Keep draft & close** retains local edits on this browser. **Discard draft** immediately removes an untouched 2D draft and asks for confirmation before removing notes, marks or pins. **Discard edits** keeps the editor open and atomically restores the acknowledged server revision, including its text, markup and pinned view or camera. If the saved revision is unavailable, the draft remains open. Discard never deletes an in-flight save or an operation pending retry, including one started in another tab.

## Request targeting and references

The **Target** picker above the message input separates what you are viewing from what you want Copilot to change. You can keep editing and send with the picker open. Close it with **Done**, `Escape` or the Target bar to return to your draft.

- **Current view:** Pins the inspected asset, scene, storyboard version or storyboard shot at submission time. An inspected sequence or Unity result is attached as a viewed version (`request_target: null`), not as an item or shot target. With nothing selected, the target is **Project**. Subsequent browsing never changes an admitted request.
- **Project:** General project discussion, research or cross-item planning.
- **Existing…:** Choose a published asset, scene or storyboard and pin an exact version, including older versions. Sending is disabled until you choose an item or switch target mode, even if you close the picker. For a storyboard, choose the entire version or a specific shot. Later publications do not move the pin.
- **New…:** Choose **Asset**, **Scene** or **Storyboard** and optionally name it. This records creation intent without inventing a published version ID.

Open the separate **References** disclosure to add published versions without changing the primary target. You can also include the current Review version when one is open. The Target bar shows the count of distinct references. Use the Composer's attachment control for supporting images. Explicit targets do not capture hidden Review drafts or viewport images.

## Composer controls, counters and history

- **Split Send button:** Click the main button to submit using the active mode (**Queue** or **Steer**). Use the chevron menu to switch modes without sending.
- **Guarded Steer:** Steer is enabled only when eligible work is actively running or awaiting input with execution unfenced. If work finishes or pauses, your Steer selection and draft are preserved with an explicit **Switch to Queue** action.
- **Prompt history:** Press `ArrowUp` in an empty Composer (or with the caret at index `0`) to recall earlier project prompts, and `ArrowDown` to move forward back to your unsent draft. Recalling history restores text only, keeping your current target, mode and attachments intact.
- **Panel links:** **Jobs**, **Context** and **Agents** sit beneath the input. Opening one closes the Target picker and temporarily replaces the text box. Closing it restores your draft, selection and focus.
  - **Jobs:** Shows failed, uncertain, running and queued counts separately, in that order. The badge shows a done count only when no jobs are running, queued or uncertain. A `?` marks last known counts or an uncertain outcome. Failed jobs and uncertain outcomes appear first under **Needs attention**. **Finished (N)** contains only succeeded and cancelled jobs and is collapsed by default. Open a row's **Details** for exact IDs, provider information and, where offered, cancellation of that independent job only.
  - **Context:** Counts logical items in the next request (explicit target, reference versions, deduplicated images, object selections, annotations and approved findings), showing active uploads separately until ready.
  - **Agents:** Counts busy or uncertain product-native specialists (`subagent.*`), showing each specialist's task label, state, freshness and model without counting wrapper `task` calls.

## Live output, failures and retained media

- **Live following:** Conversation follows new requests, streamed replies and tool updates automatically, pauses when you scroll up and offers **Jump to latest**. Empty tool-only assistant turns and `usage.recorded` telemetry events do not render inline rows in Conversation.
- **Copilot status:** Anchored at the bottom right of the Assistant panel (Composer footer, Critic footer and bottom of the collapsed right rail), displaying **Sending…**, **Working…** (with live active tool, specialist or running-job step detail), **Needs your input**, **Paused**, **Stopping…**, **Needs recovery** or **Ready**, and marking disconnected states as reconnecting rather than assuming idle.
- **Actionable diagnostics:** Failed and uncertain tool calls expand their sanitised reason immediately while keeping technical IDs collapsed. Automatic policy rejections, owner declines, host holds, non-zero shell exit codes and structured schema/publication errors are distinguished explicitly.
- **Retained outputs vs publication:** Registered images remain viewable and downloadable through authenticated artefact cards in Conversation and **Navigator → Assets → Retained outputs** even when catalogue publication fails (**Finished · publication blocked**). Clicking a retained image opens it directly in the Review workspace without fabricating a catalogue version, and selecting a published storyboard displays its ready `ShotPlan` panels in **Keyframes** mode. Standalone concept images are never labelled as 3D meshes.

## Stopping, holding and explicit Resume

- **Pending owner Decisions:** Decision cards stay anchored above the Composer alongside **Resume Copilot**, independently of Conversation scrolling. Commands are readable, with original payloads and fingerprints available under **Technical details & fingerprint**. The recovery area scrolls when needed to keep the Composer visible.
- **Queue holds:** When execution is genuinely fenced, paused or awaiting reconciliation (never during ordinary active execution), the Composer shows the hold reason before submission. Held Queue requests can be cancelled individually with **Cancel held request**.
- **Idle restart or project switch:** An eligible idle restart or activation hold shows **Ready**, without a Queue hold notice. Submitting a new **Queue** prompt releases it when no requests were already held. Explicit holds pair the Queue notice with **Resume Copilot**.
- **Conversation history:** Long conversations load the newest events first. Earlier history fills in without moving the reading position when you have scrolled up.
- **Stop vs Resume:** **Stop** cancels Copilot work through verified native barriers without stopping independent external jobs. **Resume Copilot** appears above the Composer when work or held requests require explicit recovery or queue release. It does not appear during ordinary Queue submission or while requests wait their turn behind active work. Choose a mode, then click **Resume**:
  - **Reconcile only:** Inspects native state and settles proven barriers without sending a prompt.
  - **Continue original work:** Resumes the selected work with its latest acknowledged request context and original generation budget.
  - **Release held queue:** Unfences an idle project so held Queue requests can dispatch.

## Visual Critic inbox and chat-aligned review

- **Realtime Critic badge:** The **Critic** tab header counts reviews currently awaiting human attention (once per review, regardless of finding count). Routine agent self-checks stay out of **Pending** unless escalated with an explicit question; owner-requested critiques count while processing and while unreviewed findings remain, clearing automatically on zero-defect completion or when you click **Done without changes**.
- **Pending / All inbox:** Defaults to **Pending**, listing compact review cards newest-first with only the **review name** and **24-hour SAST date/time** (`Date unavailable` when unrecorded).
- **Critique conversation & anchored actions:** Selecting a card displays the original criteria, any lead escalation question, and atomic **Critic** finding entries with clickable target images and expandable evidence, above an anchored Composer that submits selected revisions against the reviewed target/version via **Queue** or **Steer**.
