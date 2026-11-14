# Targeted readiness/camera/role evidence

UI code: `e85e717`, based on `ad47260`. Public component exports unchanged.

| Evidence | Scope |
| --- | --- |
| `readiness-component-result.json` | Four focused Chromium cases: same-PNG A→B readiness/save/preparer, negative decode states/out-of-order events, exact retained M04 job-camera restoration and null-camera preservation, single correctly typed concept image with explicit extra reference/markup/mask attachments. HTTP fixture responses are synthetic. |
| `readiness-m06-result.json` | Corrected actual compiled-app/actual-backend Full-HD direct transition and new B annotation on a fresh retained-M06 copy. No routes intercepted. |
| `readiness-m06-direct-transition.png` | Direct `kite-rest-start` review, without asset-navigation workaround. |
| `readiness-m06-saved-b.png` | Actual owner annotation saved on `kite-rest-start` in the copy. |
| `readiness-m06-attempt1.json`, `readiness-m06-attempt1-failure.png` | Retained initial copy-setup revision collision, before storyboard review. The initial SIGTERM path did not retain the factory audit; this missing audit is not presented as a passing proof. |
| `readiness-checks.json` | Exact source hashes, commands, input identities and result limits. |

The real M06 plan and two original model roots are accepted dependency evidence
from `ad47260`. This replay did not call an SDK/model/provider/native process. Its
copy preserved the registry storage/domain revision and native association, used
CAS 3→4 for the copied path, and kept all execution gates closed. Original source,
machine/native history and failed evidence remain unchanged.

The actual M04 PNG, receipt and camera come read-only from the accepted retained
run documented by `a2a2097`. The component regression validates their recorded
fingerprints, not another native render. A legacy null-camera annotation is not
silently rewritten.

Commands for the deterministic feature regression:

```bash
npx playwright test --config tests/browser/review-readiness.config.ts --reporter=json \
  > docs/evidence/w05/readiness-component-result.json
npx tsc --project tsconfig.tests.json
npm run typecheck
npm run lint
npm run build
```

**Do not rerun `review-readiness-m06.mjs` without a fresh explicit read/review
allocation.** The one corrected replay authorised for this task has been used.
It starts its own guarded backend and fresh browser, copies existing immutable
planning data and writes a new annotation only on that copy. The parent owns final
M06 criterion/cumulative acceptance. No 126-case suite or broader UI review ran.
