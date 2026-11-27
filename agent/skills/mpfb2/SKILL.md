---
name: mpfb2
description: Author parametric humanoid characters in Blender with the installed MPFB2 extension and MakeHuman assets, prepare Mixamo or Unity rigs, and publish self-contained Proscenium sources.
---

# MPFB2

## Pre-flight

- Call `get_project_capabilities` first. Honour `request_target`, `reference_versions`
  and `execution_scope`. `planning_only` permits read-only discovery, not scene
  execution, preview rendering, downloads or uploads.
- Use the configured live Blender MCP bridge and follow `blender-authoring` for
  scene ownership and dirty/outside-project checks. Preserve unsaved work. Never
  call `bpy.ops.wm.read_factory_settings` or disable add-ons in
  `blender-execute_blender_code`.
- In Blender, resolve the enabled `bl_ext.blender_org.mpfb` extension as `mpfb_mod`,
  falling back to `bl_ext.user_default.mpfb` for a local extension. Record `mpfb_mod.VERSION`.
  The workflow below was verified with MPFB2 `v2.0.18` on Blender `4.5.16 LTS`.
- Report a missing bridge, extension or required pack as a blocker. Never install
  Blender extensions, download MakeHuman asset packs, alter Blender preferences or
  change independent services automatically. Owner policy Section 2 requires
  explicit recorded confirmation for those changes.
- Inspect all four `LocationService` roots before asset or manifest reads:
  - `os.path.realpath(LocationService.get_mpfb_data())` must equal the extension's own
    `os.path.realpath(os.path.join(os.path.dirname(mpfb_mod.__file__), "data"))`.
  - `os.path.realpath(LocationService.get_user_data())` is `verified_root` only if it
    equals Blender's extension user-data root,
    `os.path.realpath(bpy.utils.extension_path_user(mpfb_mod.__package__, path="data"))`,
    or an owner-imported directory inside the active project root.
  - `LocationService.get_mh_user_data()` and `LocationService.get_second_root()` must
    each be unset (`None` or empty) or resolve to an owner-imported directory inside
    the active project root.
- Verify resolved subdirectories and paths stay inside their approved roots. Reject
  symlinked directories before scanning their tree. Call indirect root-searching
  operations only when all configured MPFB roots are verified in scope. These include
  `HumanService.refit()`, `HumanService.deserialize_from_json_file()`,
  `HumanService.deserialize_from_dict()`, `FaceService.is_faceunits01_installed()`,
  `FaceService.load_targets()`, `FaceService.set_expression()` and
  `TargetService.target_full_path()`. If any root is out of scope or cannot be verified,
  stop and report the configured root as a blocker. Do not change those settings.
- Check `makehuman_system_assets.json` and other required `<pack>.json` manifests
  directly, only under `os.path.join(verified_root, "packs")`. Do not call blanket
  scanners: `AssetService.update_all_asset_lists()`, `list_mhclo_assets()`,
  `list_mhmat_assets()` or `get_asset_list()`. They scan configurable secondary and
  MakeHuman roots. `update_all_asset_lists()` also mutates caches and loads thumbnails,
  so it is not read-only discovery.
- Before writing, resolve each `.blend`, `human.<slug>.json` and `.fbx` path and confirm it
  stays under `assets/<slug>/source/` inside the active project root. Reject symlink escapes.

## Character authoring

1. Import services from the resolved MPFB extension namespace: `LocationService`
   from `services.locationservice`, `AssetService` from `services.assetservice`,
   `TargetService` from `services.targetservice`, `HumanService` from
   `services.humanservice` and `FaceService` from `services.faceservice`.
   Enumerate only verified in-scope subdirectories using
   `AssetService.find_asset_files_matching_pattern([os.path.join(verified_root, section)],
   pattern="*.mhmat")` for `skins` or `pattern="*.mhclo"` for `eyes`, `eyebrows`,
   `eyelashes`, `hair`, `teeth`, `tongue` or `clothes`. For alternate materials such
   as eye colours, use the same helper with `*.mhmat` and a verified subdirectory.
   Check returned `list[Path]` entries and referenced meshes, materials and textures
   before loading. Convert each verified asset path with `str(path)` for `HumanService`.
2. Start with `macro = TargetService.get_default_macro_info_dict()`. Set only the
   requested parameters: `gender`, `age`, `muscle`, `weight`, `proportions`, `height`,
   `cupsize`, `firmness` and `race` (`asian`, `caucasian`, `african`). Create the base
   with `basemesh = HumanService.create_human(mask_helpers=True, detailed_helpers=True,
   extra_vertex_groups=True, feet_on_ground=True, scale=0.1, macro_detail_dict=macro)`.
   `scale=0.1` gives metres with feet at `Z = 0`.
3. Before equipping any body parts or clothes, call
   `HumanService.add_builtin_rig(basemesh, rig_name, import_weights=True)` so asset
   attachment parents to the armature and transfers weights. Use `mixamo` (52
   `mixamorig:*` bones) for Blender/Mixamo, `mixamo_unity` (64 `mixamorig:*` bones)
   for Unity Humanoid Avatar/Mixamo, or `game_engine` (53 bones) or `default` as needed.
4. Apply skin with `HumanService.set_character_skin(str(mhmat), basemesh,
   skin_type="ENHANCED_SSS", material_instances=True)` for Blender Cycles. For
   Unity/game engines use `skin_type="GAMEENGINE", material_instances=False` or
   `skin_type="MAKESKIN"`. Equip with `HumanService.add_mhclo_asset(str(mhclo), basemesh,
   asset_type=..., material_type="MAKESKIN")` using `Eyes`, `Eyebrows`, `Eyelashes`,
   `Hair`, `Teeth`, `Tongue` or `Clothes`. Prefer `MAKESKIN` for eyes to avoid
   Blender 4.5 shader socket warnings from `PROCEDURAL_EYES`. After post-equip proportion
   or target changes, call `HumanService.refit(basemesh)` only when all configured MPFB
   roots are verified in scope. Otherwise skip refit and report the root blocker.
5. Before loading or posing facial targets, verify `faceunits01.json` and any required
   `visemes01.json` or `visemes02.json` directly under `os.path.join(verified_root, "packs")`.
   Do not rely on `FaceService.is_faceunits01_installed()`: it caches a session boolean,
   not a verified path. If a manifest is missing or any configured MPFB root is not verified
   in scope, skip facial targets and report the blocker. Otherwise use
   `FaceService.load_targets(basemesh,
   load_microsoft_visemes=..., load_meta_visemes=..., load_arkit_faceunits=...)`
   for the requested sets. Pose or reset with `FaceService.set_expression(basemesh, {...})`
   or `FaceService.clear_expression(basemesh)`.

## Mixamo and Unity

- Follow `mixamo` for CLI pre-flight and clip acquisition. For an upload doll,
  activate `basemesh` and set `ANIMOPS_PROPERTIES.set_value("call_fbx", False,
  entity_reference=bpy.context.scene)` from `ui.operations.animops.animopspanel`
  in the resolved extension. The default opens an interactive FBX export dialog.
  `bpy.ops.mpfb.reduced_doll()` copies and links `<rig>_reduced` and `<rig>_body_reduced`
  into the active scene, removes helpers and bakes shape keys. The copied body can
  inherit custom properties, including IDs. Export only the selected reduced pair
  (`use_selection=True`) to a resolved `.fbx` path under `assets/<slug>/source/`.
  Export does not unlink the copies. Track the newly linked objects by identity and
  remove those exact temporary objects and their unused copied mesh and armature
  datablocks immediately, even if export fails, or
  generate and export the pair in a disposable project-owned copy instead.
  Obtain explicit recorded owner approval for the exact file and Adobe Mixamo
  destination before running `mixamo upload`.
- Select both the imported animated Mixamo armature and the MPFB2 `mixamo` or
  `mixamo_unity` rig. Run `bpy.ops.mpfb.map_mixamo()` for `COPY_ROTATION` on
  matching `mixamorig:*` bones and `COPY_LOCATION` on `mixamorig:Hips`. Check scale
  alignment (`0.01` vs `1.0`), bone orientation and deformation. For standalone
  playback, bake the action before removing the source rig.
- Follow `unity` for the pinned-source handoff and validate the Humanoid Avatar
  where required. `run_unity_workflow` exports evaluated meshes. Do not assume it
  transfers rig, avatar or animation data.

## Source, packaging and validation

- Before saving and checkpointing the `.blend`, verify only intended character meshes
  remain, with no `*_reduced` objects. Every renderable `MESH` needs a stable
  `proscenium_object_id` (shared across instances of the same object) and a unique
  `proscenium_instance_id` per instance. Keep both IDs persistent across revisions.
- Preview and Unity export workers use `blender -b --factory-startup` without MPFB2
  loaded. Verify external image paths are in scope, then call `bpy.ops.file.pack_all()`.
  Confirm zero unpacked external `FILE` images before saving `assets/<slug>/source/<slug>.blend`.
- Save `assets/<slug>/source/human.<slug>.json` alongside it with
  `HumanService.serialize_to_json_file(basemesh, filepath, save_clothes=True)`.
  To restore an in-scope preset, use `settings = HumanService.get_default_deserialization_settings()`.
  `HumanService.deserialize_from_json_file(filepath, settings)` requires a filename
  matching `human.<name>.json`. For other filenames, read the in-scope preset via
  `json.load(...)`, set `human_info["name"]` and call
  `HumanService.deserialize_from_dict(human_info, settings)`. Both restore methods
  resolve skins and clothes across configured roots. Call them only when all configured
  MPFB roots are verified in scope.
- Track `.blend` and any `.fbx` binaries with Git LFS before `checkpoint_sources`.
  Include the preset and use `required_runtime` with `{"application": "blender",
  "version": bpy.app.version_string, "features": []}` by default. Use only supported
  tags from `blender-authoring` (e.g. `packed-textures`), never `"mpfb"` or `"mpfb2"`
  in `required_runtime.features`.
- Read `source`, `author`, `license`, `original_author` and `original_source` from each
  component's `<pack>.json` entry under `os.path.join(verified_root, "packs")`. Even
  CC0-named packs such as `shoes01` can contain `CC-BY` entries. Map the manifest key
  `license` (US spelling) to the component licence in `Provenance.note` and preserve
  all exact observed values there. The Proscenium contract uses `licence` (British
  spelling). Include the MPFB2 version, rig name, preset path, exact pack and asset names,
  Mixamo motion UUIDs and separately sourced materials in `Provenance.note`.
- At item level, use `licence: "CC0"` only if every component (base mesh, skin, eyes,
  body parts, clothes, additional materials and included motions) is verified CC0 and
  there are no separately sourced or Mixamo assets. If all components share another
  verified licence, record it. If unknown, mixed or ineligible for aggregate CC0, use
  `licence: null` without discarding component attribution. Set `source_url` and `creator`
  to the exact observed source and author respectively only when each value is uniform
  across all components. Set the corresponding field to `null` for mixed or unverified
  sources or creators. Do not substitute the extension homepage or `MakeHuman Community`.
- Authoring and packing do not verify appearance or deformation. When execution
  is in scope, validate and publish via `blender-authoring`, then call `request_preview`
  for PNG stills and `purpose: "proxy"` on the pinned version. Inspect ready PNG stills
  with `get_artifact(artifact_id=..., include_image=true)`. For GLB, inspect the proxy
  receipt and `get_artifact(artifact_id=...)` metadata without `include_image=true`.
  Call `request_vlm_review` for the stills when a VLM provider is `configured` or `ready`.
  Report unverified checks separately.
