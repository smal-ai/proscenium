# Retained source-read guard failure

The coordinator's bounded readback used `f2b091a`. Pairing and explicit project
activation passed. The fixture then blocked the production source resolver's
read-only `git ls-tree` operation, so the storyboard could not load. No image/job
view was reached. This was a fixture restriction, not a source or renderer failure.

The source-read guard now also permits exact project-scoped, immutable `ls-tree`
and `cat-file blob` reads, Git common-directory discovery and `git lfs env`.
Fetch, push, checkout, ref updates, shell execution and other processes remain
blocked. The fixture disables lazy fetching and optional Git index writes.

The original failed receipt and screenshot remain unchanged. No SDK or provider
send occurred. The server/browser driver were reaped, the port was released and
the original/imported protected records remained unchanged. The new guard is not
substituted into this historical outcome.
