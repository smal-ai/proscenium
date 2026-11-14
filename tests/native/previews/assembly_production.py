"""Bounded production assembly continuation: one extra CPU still, no draft rerender."""

import json
import sys
from pathlib import Path

from proscenium.auth import Caller
from proscenium.catalogue import install as catalogue_install
from proscenium.configuration import Settings
from proscenium.contracts import canonical
from proscenium.previews import install
from proscenium.previews.journal import read
from proscenium.services import Services
from proscenium.sources import closure_digest
from proscenium.sources.common import fingerprint, git, sha
from test_production import recipe, wait


def main():
    proof_path, output_path = Path(sys.argv[1]), Path(sys.argv[2])
    if output_path.exists():
        existing = read(output_path)
        if existing.get("job", {}).get("state") == "succeeded":
            print("Verified assembly evidence already exists. No native work repeated.")
            return
        raise ValueError("Inspect the existing partial assembly evidence before continuing")
    proof = read(proof_path)
    root = Path(proof["output_project"])
    services = Services.create(
        Settings(
            data_directory=root.parent / "machine",
            free_space_reserve_bytes=0,
            execution_profiles=frozenset({"blender-cpu"}),
            rendering={"max_frames": 20, "max_pixels": 640 * 360, "max_samples": 16},
        )
    )
    services.acquire_project("w03-native")
    catalogue = catalogue_install(services)
    previews = install(services)
    services.gate.resume(
        "w03-native", reconciliation_evidence="Explicit remaining W03 CPU/encode allocation"
    )
    caller = Caller(
        "tool",
        "w03-native-tool",
        "w03-native",
        "work-native",
        services.ownership.epoch,
        operations=("requestPreview", "admitToolJob", "checkpoint_sources", "publishOutputs"),
        invocation_id="invocation-assembly-native",
    )
    result = {"input_proof_sha256": sha(proof_path.read_bytes())}
    try:
        with services.registry.project_store("w03-native").transaction() as tx:
            tx.put(
                "invocations",
                caller.invocation_id,
                {"work_id": caller.work_id, "owner_epoch": caller.owner_epoch, "state": "running"},
            )
        spec = recipe(
            proof["closure"]["digest"], "incoming-shot-native", first=1, last=1, purpose="keyframe"
        )
        spec["shot_id"] = "incoming-shot"
        incoming, _ = previews.request(
            caller,
            dict(
                schema_version="1.0",
                operation_id="incoming-shot-preview",
                project_id="w03-native",
                version={"item_id": "fixture-scene", "version_id": "fixture-v1"},
                spec=spec,
            ),
        )
        previews.pump("w03-native")
        incoming, observations = wait(previews, incoming)
        result["incoming_render"] = {
            "job": incoming,
            "root": str(previews.root(incoming)),
            "observations": observations,
        }
        outgoing = proof["jobs"][0]["job"]

        def contributor(job, source_frame, numerator=1, denominator=1):
            return {
                "shot_id": job["target"]["shot_id"],
                "version_id": "fixture-v1",
                "job_id": job["job_id"],
                "source_frame": source_frame,
                "weight": {"numerator": numerator, "denominator": denominator},
            }

        entries = []
        for label, first, last, transform in (
            ("cut", 1, 4, "identity"),
            ("hold-drop", 5, 8, "retime"),
            ("dissolve", 9, 12, "transition"),
        ):
            entry = dict(
                entry_id=label,
                shot_id="fixture-shot",
                version_id="fixture-v1",
                job_id=outgoing["job_id"],
                source_frames={"start": first, "end": last},
                sequence_frames={"start": first, "end": last},
                transform=transform,
                mapping_artifact_id="native-assembly-map",
            )
            if transform == "transition":
                entry["contributors"] = [
                    {
                        "shot_id": "fixture-shot",
                        "version_id": "fixture-v1",
                        "job_id": outgoing["job_id"],
                        "source_frames": {"start": 9, "end": 12},
                    },
                    {
                        "shot_id": "incoming-shot",
                        "version_id": "fixture-v1",
                        "job_id": incoming["job_id"],
                        "source_frames": {"start": 1, "end": 1},
                    },
                ]
            entries.append(entry)
        sequence = dict(
            schema_version="1.0",
            project_id="w03-native",
            sequence_version_id="assembly-sequence-v1",
            fps={"numerator": 24, "denominator": 1},
            frame_count=12,
            silent=True,
            entries=entries,
        )
        rows = [
            {"sequence_frame": i, "contributors": [contributor(outgoing, i)]} for i in range(1, 5)
        ]
        rows += [
            {"sequence_frame": i, "contributors": [contributor(outgoing, source)]}
            for i, source in zip(range(5, 9), (5, 5, 7, 8))
        ]
        from fractions import Fraction

        for i in range(4):
            a, b = Fraction(3 - i, 3), Fraction(i, 3)
            rows.append(
                {
                    "sequence_frame": 9 + i,
                    "contributors": [
                        contributor(outgoing, 9 + i, a.numerator, a.denominator),
                        contributor(incoming, 1, b.numerator, b.denominator),
                    ],
                }
            )
        frame_map = dict(
            schema_version="1.0",
            project_id="w03-native",
            sequence_version_id="assembly-sequence-v1",
            fps=sequence["fps"],
            frame_count=12,
            blend_space="linear_rgb",
            frames=rows,
        )
        artifacts = services.extension("artifacts")
        artifact = artifacts.promote(
            "w03-native",
            "native-assembly-map",
            canonical(frame_map),
            kind="frame_map",
            purpose="metadata",
            media_type="application/json",
        )
        with services.registry.project_store("w03-native").transaction() as tx:
            artifacts.put(tx, artifact)
        source = root / "sequences/w03-proof.json"
        source.parent.mkdir(exist_ok=True)
        source.write_bytes(canonical(sequence))
        checkpoint = services.extension("sources").checkpoint(
            caller,
            dict(
                schema_version="1.0",
                operation_id="assembly-checkpoint",
                project_id="w03-native",
                paths=["sequences/w03-proof.json"],
                message="test: pinned W03 cut retime dissolve sequence",
                expected_head=git(root, "rev-parse", "HEAD").decode().strip(),
            ),
        )
        closure = dict(
            entry_path="sequences/w03-proof.json",
            files=[
                {
                    "destination": "sequences/w03-proof.json",
                    "origin": {
                        "kind": "git",
                        "repository_id": "w03-repo",
                        "commit": checkpoint["source_commit"],
                        "path": "sequences/w03-proof.json",
                        "sha256": sha(source.read_bytes()),
                    },
                }
            ],
            dependencies=[{"item_id": "fixture-scene", "version_id": "fixture-v1"}],
            required_runtime={"application": "sequence", "version": "1.0", "features": []},
            digest="0" * 64,
        )
        closure["digest"] = closure_digest(closure)
        publication = dict(
            schema_version="1.0",
            publication_id="assembly-publication",
            project_id="w03-native",
            items=[
                dict(
                    item_id="assembly-sequence",
                    version_id="assembly-sequence-v1",
                    kind="sequence",
                    name="W03 assembly proof",
                    expected_current_version_id=None,
                    source_commit=checkpoint["source_commit"],
                    source=closure,
                    artifact_ids=["native-assembly-map"],
                    preview_requests=[],
                )
            ],
        )
        result["publication"] = catalogue.publish(caller, publication)[0]
        with services.registry.project_store("w03-native").transaction(write=False) as tx:
            needed = {(c["job_id"], c["source_frame"]) for row in rows for c in row["contributors"]}
            receipts = {
                r["artifact_id"]: r
                for r in tx.all("frames")
                if (r["job_id"], r["source_frame"]) in needed
            }
        snapshot = {
            "sequence": sequence,
            "frame_map": frame_map,
            "input_frames": [receipts[key] for key in sorted(receipts)],
        }
        admission = dict(
            schema_version="1.0",
            operation_id="assemble-native",
            project_id="w03-native",
            job_id="assembly-native",
            kind="encode",
            target={
                "target_id": "assembly-sequence",
                "version": {"item_id": "assembly-sequence", "version_id": "assembly-sequence-v1"},
                "shot_id": None,
            },
            input_digest=fingerprint(snapshot),
            input_artifact_ids=sorted(receipts),
            execution_profile_id="blender-cpu",
        )
        job, _ = previews.assembly(caller, admission, sequence, frame_map)
        previews.pump("w03-native")
        job, _ = wait(previews, job)
        result["job"], result["root"] = job, str(previews.root(job))
        result["checkpoint"] = checkpoint
        result["sequence"], result["frame_map"] = sequence, frame_map
        result["artifacts"] = [
            artifacts.get("w03-native", identity) for identity in job["artifact_ids"]
        ]
    finally:
        previews.close()
        services.close()
        output_path.write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
