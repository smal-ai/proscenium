# Wave 4: pinned source composition measurement

## Restored wardrobe history in the browser

The E9 Playwright test renders the actual authenticated `getVersionRelationships`
responses captured by the Python publish, backup and restore fixture. It changes
only the enclosing project ID for the hermetic Studio harness. Hero v3 shows
Landing v2 and Stairwell v2 as **Included by**. Hero v4 shows only Stairwell v3
as **Included by**. No scene appears a second time as **Declared dependency**.

## Offline boundary measurement

AMD Ryzen AI Max+ 395, Linux x86-64, Python 3.12. Fresh disposable Git repository and
SQLite store under `/tmp/opencode` (tmpfs). No model, network, GPU or native
application ran. This was an instrumented run, not a latency target.

```bash
tmp=$(mktemp -d /tmp/opencode/wave4-re-audit-measure.XXXXXX)
PROSCENIUM_MEASURE_COMPOSITION=1 /usr/bin/time -v uv run pytest -q -s \
  --basetemp "$tmp/pytest" \
  tests/test_source_composition.py::test_composition_bound_measurement
```

The fixture hit all three admission limits together: 128 exact direct producer
pins, 4,096 distinct destinations and 536,870,912 hydrated bytes. It used 4,095
retained destinations backed by one 128 KiB fixture blob and one 128 KiB owned
Git LFS entry. The fixture verifies the source bytes and the written snapshot,
not Blender library evaluation. Shared physical storage does not discount
distinct destinations from the byte limit.

| Phase | Wall-clock time | Peak Python allocations (`tracemalloc`) |
| --- | ---: | ---: |
| Checkpoint, including verification around ref update | 32.798 s | 8.46 MiB |
| Verify and materialise the private snapshot | 10.911 s | 5.13 MiB |

`/usr/bin/time -v` reported a maximum resident set size of 135,388 KiB
(132.21 MiB) for the full test process, including imports and fixture setup.
The test passed in 44.52 seconds. These results describe the retained-heavy
fixture on tmpfs with instrumentation enabled, not a cold physical-disk workload
or a 4,096-object Git/LFS workload. Linked animated Blender integrity-check
cost and native evaluation are recorded separately in the [Wave 5 E10 evidence](version-relationships-wave5-e10-2026-11-28.md).
