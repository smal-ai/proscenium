"""Fresh positive M09 allocation, real production composition and observation-only hooks.

Reuses the accepted fixture/observer implementation, never its failed runtime directory.
No response injection, findings repair, native tool replacement or independent service controls.
"""

import base64
import io
import json
import os
import time
from pathlib import Path

import httpx
import m09_host as accepted
import uvicorn
from PIL import Image
from proscenium.api.composition import configure_services
from proscenium.app import create_app
from proscenium.configuration import Settings
from proscenium.services import Services

REPO = Path(__file__).resolve().parents[3]
ROOT = Path.home() / ".cache/proscenium-development/m09-positive-20261114"
ORIGIN = "http://127.0.0.1:18919"


class ObservedHTTP(httpx.BaseTransport):
    def __init__(self) -> None:
        self.delegate = httpx.HTTPTransport(retries=0, trust_env=False)

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        inference = request.method == "POST" and request.url.path == "/v1/chat/completions"
        if inference:
            previous = (
                [json.loads(v) for v in (ROOT / "vlm.jsonl").read_text().splitlines()]
                if (ROOT / "vlm.jsonl").exists()
                else []
            )
            count = sum(v["phase"] == "dispatch" for v in previous)
            maximum = 3 if (ROOT / "actual-defect-third-call.json").exists() else 2
            if count >= maximum or accepted.memory() < 4 * 1024**3:
                raise RuntimeError("Positive M09 inference allocation or protected-memory floor")
            body = json.loads(request.content)
            images = [
                base64.b64decode(c["image_url"]["url"].split(",", 1)[1])
                for m in body["messages"]
                if isinstance(m["content"], list)
                for c in m["content"]
                if c["type"] == "image_url"
            ]
            assert len(images) == 1 and body["max_tokens"] <= 512
            for image in images:
                with Image.open(io.BytesIO(image)) as decoded:
                    assert decoded.size == (640, 360)
            accepted.append(
                "vlm.jsonl",
                dict(
                    phase="dispatch",
                    index=count + 1,
                    model=body["model"],
                    max_tokens=body["max_tokens"],
                    reasoning_effort=body.get("reasoning_effort"),
                    schema=body["response_format"]["json_schema"]["schema"],
                    body_sha256=accepted.sha(request.content),
                    image_sha256=[accepted.sha(i) for i in images],
                    mem_available=accepted.memory(),
                ),
            )
        start = time.monotonic()
        response = self.delegate.handle_request(request)
        response.read()
        if inference:
            value = response.json()
            for choice in value.get("choices", []):
                message = choice.get("message", {})
                message["reasoning_present"] = bool(message.pop("reasoning_content", ""))
                message.pop("reasoning", None)
            accepted.append(
                "vlm.jsonl",
                dict(
                    phase="response",
                    status=response.status_code,
                    seconds=time.monotonic() - start,
                    response_sha256=accepted.sha(response.content),
                    body=value,
                    mem_available=accepted.memory(),
                ),
            )
        return response

    def close(self) -> None:
        self.delegate.close()


def main() -> None:
    if (ROOT / "host-started.json").exists():
        raise SystemExit("Original positive allocation exists. No duplicate host or inference run.")
    assert (ROOT / "baseline.json").exists(), (
        "Record/check the fresh baseline and one authorised load first"
    )
    accepted.ROOT = ROOT
    accepted.PROJECT_ROOT = ROOT / "project"
    accepted.ORIGIN = ORIGIN
    accepted.observe_native()
    settings = Settings(
        data_directory=ROOT / "machine",
        origin=ORIGIN,
        frontend_directory=REPO / "apps/frontend/dist",
        profiles=(
            dict(
                profile_id="authoring",
                name="M09 positive read-only proof",
                lead_model_id="gpt-6.1-sol",
                worker_model_ids=["gpt-6.1-sol"],
                scope_status="project_scoped",
            ),
        ),
        services=(
            dict(
                provider_id="m09-positive-vlm",
                endpoint="http://127.0.0.1:1234",
                capabilities=["vlm"],
                model_id="google/gemma-4-e4b",
                available=None,
            ),
        ),
        provider_options=dict(review_provider_id="m09-positive-vlm", vlm_max_tokens=512),
        runtime=dict(
            cli_path=str(REPO / "node_modules/@github/copilot-linux-x64/copilot"),
            lead_model="gpt-6.1-sol",
            worker_model="gpt-6.1-sol",
            worker_tools=["view", "request_vlm_review", "get_artifact"],
            profile_id="authoring",
            skill_directories=[],
            mcp_servers={},
            max_continues=5,
        ),
    )
    services = Services.create(settings)
    try:
        configure_services(services)
        provider = services.extension("providers")
        provider.client.close()
        provider.client = httpx.Client(
            timeout=120, transport=ObservedHTTP(), follow_redirects=False, trust_env=False
        )
        provider.owns_client = True
        fixture = accepted.fixtures(services)
        review = fixture["native_review"]
        review["review_id"] = "m09-positive-native-review"
        review["criteria"] = (
            "Exactly two concise findings. Finding 1: a visual observation of the blue object's shape/location, citing the image. "
            "Finding 2: measured image width and height in pixels, citing the supplied telemetry ID, not the image. "
            "Each observation and recommendation under 10 words. No scene-size guesses or reasoning text. Structured JSON only."
        )
        prompt = (
            "M09 positive bounded read-only acceptance. This request explicitly authorises its host execution gate. "
            "Do not delegate or create children. Do not edit any file, run Blender/Unity, generate images, request extra reviews or access raw findings. "
            "Call request_vlm_review exactly ONCE with the exact ReviewRequest JSON below. Return ONLY the ticket/status and stop. "
            "Do not poll or retry this tool. No other action is requested. " + json.dumps(review)
        )
        (ROOT / "native-review-prompt.txt").write_text(prompt)
        fixture["fixture_scope"] = (
            "Fresh project/publication and read-only COPY of accepted M01 image. Old M09 failed data untouched."
        )
        (ROOT / "fixture.json").write_text(json.dumps(fixture, indent=2))
        (ROOT / "pair.private").write_text(f"{ORIGIN}/#pair={services.auth.issue_pairing()}")
        os.chmod(ROOT / "pair.private", 0o600)
        info = dict(
            pid=os.getpid(),
            start_ticks=Path(f"/proc/{os.getpid()}/stat").read_text().split(") ")[1].split()[19],
            origin=ORIGIN,
            at=time.time(),
            code_base="ad47260a922612393896c21ff7186d643037c524",
        )
        (ROOT / "host-started.json").write_text(json.dumps(info, indent=2))
        uvicorn.run(
            create_app(settings, services=services, manage_features=True),
            host="127.0.0.1",
            port=18919,
            log_level="warning",
        )
    finally:
        services.close()


if __name__ == "__main__":
    main()
