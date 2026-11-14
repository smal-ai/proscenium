"""Record an ALREADY OBSERVED failed pre-start guard. No positive execution path.

The original free -b observation is supplied explicitly rather than quietly sampling
again until RAM happens to pass. This recorder cannot start a server or submit inference.
"""

import argparse
import json
import time
from pathlib import Path

FIT_BYTES = 13_804_495_920
RUN = (
    Path.home()
    / ".cache/proscenium-development/image-generation-light/runs/m07-lightweight-20261114"
)


def record_blocker(observed_bytes: int) -> dict:
    if not 0 <= observed_bytes < FIT_BYTES:
        raise ValueError(
            "Only the original failed guard observation may be recorded; this is not execution authority"
        )
    RUN.mkdir(parents=True, mode=0o700, exist_ok=False)
    report = dict(
        recorded_epoch=time.time(),
        measurement_source="Initial free -b tool output from the instruction to execute the prepared plan; not a new or repeated RAM sample",
        original_observation_epoch_not_recorded=True,
        allocation="m07-lightweight-20261114",
        status="BLOCKED_PRE_START_HOST_FIT",
        observed_mem_available_bytes=observed_bytes,
        required_fit_bytes=FIT_BYTES,
        shortfall_bytes=FIT_BYTES - observed_bytes,
        protected_headroom_bytes=4 * 1024**3,
        stopguard_bytes=5 * 1024**3,
        candidate="stabilityai/sd-turbo",
        revision="b261bac6fd2cf515557d5d0707481eafa0485ec2",
        checkpoint="sd_turbo.safetensors",
        checkpoint_bytes=5_214_561_328,
        expected_sha256="3f067a1b943cf162f2b8f8588f6cf5824bd5b4c7d1d88d87164b9ca123616549",
        checkpoint_rehashed_in_this_attempt=False,
        checkpoint_verification_source="Previously verified preparation receipt; pre-start RAM failed before rehash/start",
        checkpoint_or_profile_changed=False,
        own_comfy_started=False,
        endpoint="http://127.0.0.1:18589",
        server_deadline_started=False,
        inference_posts=0,
        actual_image_inferences=0,
        native_cloud_roots=0,
        native_children=0,
        generation_jobs_admitted=0,
        owner_selections=0,
        owner_permission_decisions=0,
        gpu_claim_acquired=False,
        independent_8188_side_effects=False,
        lm_service_changes=False,
        extra_wait_or_resample=False,
        milestone_complete=False,
        note="Immediate before-inference resource blocker. No process/model/native effect and no repeated guard to seek a green sample.",
    )
    with (RUN / "admission.json").open("x") as output:
        json.dump(report, output, indent=2)
        output.write("\n")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--record-failed-observation", type=int, required=True)
    args = parser.parse_args()
    result = record_blocker(args.record_failed_observation)
    print(
        json.dumps(
            {
                "report": str(RUN / "admission.json"),
                "status": result["status"],
                "shortfall_bytes": result["shortfall_bytes"],
            },
            indent=2,
        )
    )
