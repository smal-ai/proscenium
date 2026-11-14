"""Continue a retained W03 draft proof without rerendering its twelve-frame allocation.

Used after the pre-render camera wire-shape defect was corrected. Fresh runs use pytest.
"""

import json
import sys
from pathlib import Path

from proscenium.auth import Caller
from proscenium.configuration import Settings
from proscenium.previews import install
from proscenium.previews.journal import read
from proscenium.services import Services
from test_production import camera, recipe, wait


def main():
    destination = Path(sys.argv[1])
    proof = read(destination)
    if len(proof["jobs"]) == 4:
        print("All four retained native jobs already completed. No native work repeated.")
        return
    root = Path(proof["output_project"])
    services = Services.create(
        Settings(
            data_directory=root.parent / "machine",
            free_space_reserve_bytes=0,
            execution_profiles=frozenset({"blender-cpu"}),
            rendering={"max_frames": 20, "max_pixels": 640 * 360, "max_samples": 16},
        )
    )
    from proscenium.catalogue import install as catalogue_install

    catalogue = catalogue_install(services)
    previews = install(services)
    services.acquire_project("w03-native")
    services.gate.resume(
        "w03-native", reconciliation_evidence="Explicit continuation of bounded native proof"
    )
    caller = Caller(
        "tool",
        "w03-native-tool",
        "w03-native",
        "work-native",
        services.ownership.epoch,
        operations=("requestPreview",),
        invocation_id="invocation-camera-continuation",
    )
    with services.registry.project_store("w03-native").transaction() as tx:
        tx.put(
            "invocations",
            caller.invocation_id,
            {"work_id": caller.work_id, "owner_epoch": caller.owner_epoch, "state": "running"},
        )
    try:
        for label in ("perspective", "orthographic", "proxy"):
            spec = recipe(
                proof["closure"]["digest"],
                label,
                first=1,
                last=1,
                purpose="proxy" if label == "proxy" else "keyframe",
                camera=None if label == "proxy" else camera(label),
            )
            job, _ = previews.request(
                caller,
                dict(
                    schema_version="1.0",
                    operation_id=f"preview-{label}",
                    project_id="w03-native",
                    version={"item_id": "fixture-scene", "version_id": "fixture-v1"},
                    spec=spec,
                ),
            )
            previews.pump("w03-native")
            complete, observations = wait(previews, job)
            proof["jobs"].append(
                {"job": complete, "observations": observations, "root": str(previews.root(job))}
            )
        proof["proxy_mapping"] = catalogue.proxy_mapping("w03-native", "fixture-v1")
        with services.registry.project_store("w03-native").transaction(write=False) as tx:
            proof["artifacts"] = tx.all("artifacts")
            proof["events"] = tx.events(after=0, limit=100)
    finally:
        previews.close()
        services.close()
        destination.write_text(json.dumps(proof, indent=2) + "\n")


if __name__ == "__main__":
    main()
