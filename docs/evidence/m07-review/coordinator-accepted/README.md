# Actual corrected M07 image/job/shot readback

**Passed against `eb5ff0d` on 14 November 2026.** The coordinator used the existing
composed application and the exact retained M07 images in a new isolated import.
One 1920×1080 Chromium stack completed in **3.953 seconds**. No API mocks, new
generation, SDK send, provider request or owner selection mutation was used.

All seven actual views passed:

1. The saved storyboard choice opened the correction and its correct job.
2. The job dropdown opened the original generated image.
3. Storyboard navigation returned to the correction and updated its job label.
4. The dropdown returned to the initial image again.
5. The dropdown explicitly selected the correction.
6. The unrelated reference displayed **No associated job**.
7. Reload restored the saved correction with the correct job and target.

Each view verified the decoded image URI, exact SHA-256, 512×512 dimensions,
displayed job label and schema-valid ContextSnapshot-ready selection. Generated
outputs retained `shot-mug`, `origin: concept`, null render job/frame and the
correct concept attachment. No conditioning derivative replaced an output.
These are captured client selections, not newly submitted model requests.

Original and imported protected jobs, requests, selections, source/media and
budget rows remained unchanged, including the original scripted setup orphan.
Only pairing and explicit project activation were performed through owner APIs.
Ten bounded read-only Git operations loaded the actual pinned storyboard source.
The execution gate stayed closed. The server and browser driver exited zero and
were reaped, the browser was closed, and port 18969 was released. Minimum sampled
available RAM was **25,330,642,944 bytes**.

`manifest.json` hashes the actual records and all seven screenshots.
`../coordinator-accepted-checks.xml` records **10 passing checks**, including both
actual-readback oracles. Earlier selector/guard failures remain unchanged in
their own directories. The original colour-correction failure remains unchanged.
