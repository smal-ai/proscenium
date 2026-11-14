# Retained exact proof source

These `.txt` files retain the exact public source bytes used by the bounded proof.
They are inert evidence inputs, not executable scripts or authority to replay work.

- `c517823de2249bcc21911fdbe92a943efb0cd535`: six launched driver, host, browser,
  opening helper, oracle and import source files.
- `87ea8355bf92760af84d521084a09fb6cb26336b`: the two runtime recovery modules,
  committed before the sole SDK send was released.

The separate `complete-source-mirrors.json` records original revisions, source paths,
retained paths and SHA-256 digests. Evidence checks validate every byte. They do not
require those original fleet-side commit objects to exist in a main-only clone.
The actual executed revision facts in the receipts and summary remain unchanged.
