# Reference provenance

- Reviewed read-only at `smal-ai-game` commit `65f69c6360fe0bd48f8d4b6d2dc86a22918926b4`: `tools/game.py`, `tools/export_environment.py`, `Assets/Game/Editor/GameBuild.cs`, `Assets/Game/Scripts/WalkthroughPlayer.cs`, `Assets/Game/Scripts/PlayerViewRig.cs`, relevant game tests and `THIRD_PARTY_NOTICES.md`.
- Generalised its project-specific process targeting, bounded process-group cleanup, Editor scene/build APIs, CharacterController movement/reset and yaw/pitch camera patterns. The new exporter uses evaluated triangle JSON and Unity mesh APIs rather than copying its environment FBX/assets.
- The reference pin does not contain a top-level code licence. No cleared code licence is asserted. The development task explicitly authorised this adaptation.
- No game assets, character models, animation clips, textures, private build inputs, scene routes, HUDs or licence credentials were copied. The reference's HumGen/Mixamo and downloaded asset notices do not become dependencies of this tool.
- Unity packages retain their package licences. The native fixture uses locally generated F01 geometry and its own copied, hashed materialisation. Blender and Unity are existing installations, not bundled redistributions.
