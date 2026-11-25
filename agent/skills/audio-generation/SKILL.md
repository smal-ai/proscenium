---
name: audio-generation
description: Generate Stable Audio 3 music or sound effects, then assemble them into a published sequence soundtrack.
---

1. Call `get_project_capabilities`. Use a ready `audio` provider and a listed mode. Readiness confirms the catalogue preflight, not inference. Do not start or reconfigure the provider.
2. **Music:** describe genre, rhythm or BPM, instrumentation, mood, dynamics and how the cue starts and ends. For an underscore, specify room for dialogue and whether you want no vocals. **Sound effects:** describe the source, action, distance, texture, attack and decay. Ask for a clean, isolated effect unless you need a specific environment.
3. Call `prepare_audio_generation` with a fresh `operation_id`, prompt, decimal string seed and `duration_seconds`. Use `mode: "music"` (the default, up to 380 seconds) or `mode: "sfx"` (up to 120 seconds). The provider may impose lower limits. Omit steps to use the recipe defaults. Omit `project_id` and `generation_task_id`; the host supplies them. The tool accepts no conditioning artefacts.
4. Call `submit_generation` with the exact `submit_request`. Wait with `get_job`. Use the ready FLAC artefact ID, not the workflow, prompt or provenance document. Review the output before choosing it for the cut.
5. Set `SequenceMap.silent` to `false` and add `audio_tracks` with unique `track_id`, ready `artifact_id` and `start_seconds` relative to the start of the sequence. `trim_start_seconds` skips source audio. Optional `duration_seconds` limits the clip. Use `gain_db` to balance layers and `fade_in_seconds` / `fade_out_seconds` to avoid abrupt cuts. Keep fades within the playable duration and leave headroom for overlapping tracks. For a silent export, use `silent: true` and omit the tracks.
6. Run `validate_authored_document`, checkpoint and publish the sequence, then call `assemble_sequence` for its exact published version. Assembly does not change the authored edit. Studio lets the owner listen to retained audio and the assembled soundtrack before making a selection.
