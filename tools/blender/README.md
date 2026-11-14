# Blender authoring bridge

Run from the repository root with the actual active project path:

```bash
python3 -m tools.blender doctor --project /path/to/project
python3 -m tools.blender start --project /path/to/project --virtual-display
```

`start` checks loopback port 9876 before launching. An occupied, unverified,
dirty or outside-project instance is preserved, never replaced. Doctor uses the
installed add-on's read-only diagnostics plus one fixed inspection of
`bpy.data.filepath` and `bpy.data.is_dirty`. No model-authored code is accepted by
this diagnostic transport. A saved clean in-project file permits subsequent
authoring through the ordinary MCP tools. A blank untitled startup is reported
ready but does not grant authority to overwrite a file.

With no display, `start` runs a real Blender UI under `xvfb-run`, using the
already-installed add-on and preferences. It does not install/reconfigure the
add-on or save preferences. A readiness timeout leaves the owned UI intact and
returns `outcome_uncertain`. Inspect the retained launch record/log before any
retry. No stop/global-kill operation exists. The parent owns explicit authoring
test allocation. W03's own native verification does not start this bridge.

The host MCP configuration uses the existing installed integration:

```json
{
  "command": "uvx",
  "args": ["--python", "3.11", "mcp-for-blender==2.0.0"],
  "env": {"DISABLE_TELEMETRY": "1"}
}
```

The MCP client must separately confirm `get_addon_status`, `get_scene_info` and
the active file/dirty state before editing. A healthy stdio server alone is not
proof of a working Blender socket bridge. Long presentation work runs through
`proscenium.previews`, in factory/disable-autoexec processes, not the live UI.

## Reference provenance

These helpers are a new project-scoped implementation informed by the maintained
socket/virtual-display patterns in `smal` commit
`c7e2a8f4faf6143118b3358ade2994ec37086e1c`, `tools/blender/{launch,bridge}.py`
and its README, and the source-preservation rules in `smal-ai-blender` commit
`1db08b7184b309a9ca00fa0845aa1ecf9e12e881`, Blender integration skill and
renderer/worker/I/O resources. Inspection used `git show`, not mutable checkouts.
The latter's licence is Copyright (c) 2026 Quintin De Kok, all rights reserved.
No film-specific source, asset, credential, inherited creative task or binary is
copied. The owner explicitly authorised this same-owner adaptation. No licence
file was present at the pinned `smal` repository root. No broader redistribution
permission is inferred.
