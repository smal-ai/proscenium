---
name: mixamo
description: Find and acquire Adobe Mixamo humanoid characters and animations for an active project, then inspect and validate them in Blender or Unity.
---

# Mixamo

## Pre-flight

- Call `get_project_capabilities` first. Honour `request_target`, `reference_versions`
  and `execution_scope`. `planning_only` permits read-only discovery, not downloads,
  uploads, account changes, previews or scene execution.
- Before any other Mixamo command, run `mixamo status` via `bash` from the active
  project root to check the live authenticated session. The CLI itself may
  silently refresh an expiring token and rotate cookies under `~/.config/mixamo/`.
  If the active scope forbids even CLI-managed authentication changes, skip all
  authenticated Mixamo commands, including `status`.
- Stop Mixamo work and report the exact blocker on any failed pre-flight: any
  non-zero exit, missing CLI on `PATH`, expired authentication, or a missing or
  unreadable session. Never run `mixamo renew` manually, install Mixamo, scrape
  credentials or attempt an interactive sign-in. Never read `~/.config/mixamo/token`,
  `~/.config/mixamo/session.json` or any other credentials.

## Search, inspect and acquire

1. Characters: `mixamo search "<query>" --type Character --json` returns `id`,
   `name`, `description` and `thumbnail`. Animations and packs:
   `mixamo search "<query>" --type Motion,MotionPack --json` also returns
   `thumbnail_animated`. Keep private project details out of queries.
2. Before downloading a motion, run `mixamo inspect <product_uuid>`. Pass
   `--character-id <character_uuid>` to inspect against the intended rig. Check
   `supports_inplace`, `loopable`, `duration_seconds`, `default_frame_length` and
   exact named `params` sliders. For a Character UUID, `inspect` returns JSON
   with `type: "Character"` and `download_hint`.
3. Export the base mesh and skeleton with skin in T-Pose:
   `mixamo character <character_uuid> --out assets/<slug>/source --format fbx7_2019 --fps 24`.
   For Unity use `--format fbx7_unity --fps 30`. Use `--pose-id <motion_uuid>` only
   when intentionally replacing the T-Pose.
4. Export clips against the same character UUID:

   ```text
   mixamo download <motion_uuid> --character-id <character_uuid> --out assets/<slug>/source/animations --format fbx7_2019 --fps 24 [--inplace] [--mirror] [--arm-space N] [--trim START,END] [--param NAME=VALUE]
   ```

   Omit `--skin` for skeleton-only clips; add it for standalone skinned exports.
   Use `--inplace` only when supported and use the exact slider names from `inspect`.
   Motion packs download as ZIP files.
5. To retarget onto a custom **rigged** humanoid `.fbx` or `.zip`, obtain recorded
   owner approval for the exact file and Adobe Mixamo destination before running
   `mixamo upload assets/<slug>/source/<rigged>.fbx --timeout 180`. Unrigged meshes
   cannot be auto-rigged headlessly via the API. They require the Mixamo web UI.
6. Pass the character UUID explicitly rather than changing the account default.
   If an account-wide primary character switch is explicitly authorised and needed,
   use `mixamo set-character <character_uuid>`.

`mixamo character` and `mixamo download` return JSON with `status` (`"downloaded"`
or `"skipped"`), `path`, `bytes` and `sha256`. Existing non-empty files are skipped
unless `--overwrite` is used. Use a distinct `--filename` for changed export
settings so a skipped file is not mistaken for a new export.

## Blender and Unity

- **Blender:** use `--format fbx7_2019`. `--fps` accepts only `24`, `30` or `60`.
  Default to `24` or match the scene if its rate is supported. Import with
  `bpy.ops.import_scene.fbx(filepath=..., automatic_bone_orientation=True)`.
  Check armature scale (`0.01` for Mixamo's centimetre-to-metre convention), rest
  pose, mesh deformation and vertex groups, materials and texture packing,
  Action/NLA strip frame ranges, and root motion versus `--inplace`.
- **Unity:** use `--format fbx7_unity`, `--fps 30` or `60`, and `--inplace` for
  locomotion clips that support it. Retarget via a Humanoid Avatar in the active
  project's `unity/` subtree and use `run_unity_workflow` for admitted jobs. The
  workflow does not import raw FBX as a published source or configure the Avatar
  for you. Do not bypass it by launching an untracked Editor.

## Source and validation

- Download only inside the active project root under `assets/<slug>/source/`.
  Never download to `/tmp` or home, or through paths that resolve outside the project.
- Track binary `.fbx`, `.dae` and `.zip` sources with Git LFS before calling
  `checkpoint_sources`. Preserve unrelated staging.
- At publication, record `Provenance` for Mixamo catalogue characters and motions
  with `source_url: "https://www.mixamo.com"`, `creator: "Adobe Mixamo"` and
  `licence: null`. Record the exact character and motion UUIDs and names in
  `Provenance.note`. Do not invent a cleared licence.
- For a custom uploaded rig, preserve the rig's original `source_url`, `creator`
  and `licence`, using `null` for unknowns. Do not attribute the custom rig to
  Adobe Mixamo. Record the exact Mixamo motion UUIDs and names and the retargeting
  step in `Provenance.note`.
- Download and import are not visual or deformation validation. When execution is
  in scope, render inspection frames with `request_preview` and call
  `request_vlm_review` when a VLM provider is configured or ready. Report any
  unverified checks separately.
