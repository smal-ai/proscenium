# SSH transport follow-up

**Result: bounded SSH product transport passed.** The coordinator ran one newly
allocated temporary sshd/client/browser/server set against `c1b2181` on
14 November 2026. The earlier partial `ssh-product.json` remains unchanged.

```bash
npm run build
uv run python -m tools.operations.ssh_proof \
  --root /home/quintin/.cache/proscenium-development/w10-ssh-product-20261114-b \
  --frontend-dir /home/quintin/play/proscenium/apps/frontend/dist \
  --evidence docs/evidence/w10/ssh-product-b.json \
  --authorised-temporary-sshd
```

Both commands exited zero. The [complete receipt](ssh-product-b.json) has SHA-256
`dd40844355d9b0b498581a52fe64817f04665108a434ed06727d9687dfc92509`.

- Actual OpenSSH `10.2p1` authentication and LocalForward used fixture-only keys
  and a private known-hosts file. No existing SSH configuration was changed.
- Chromium `159.0.8154.6`, 1920 × 1080, loaded the compiled studio through the
  forwarded origin. Owner pairing, project creation/activation, WebSocket replay
  and reconnect, image upload, hash verification and exact HTTP 206 bytes passed.
- Host/Origin rejection remained enabled. The browser made zero off-origin requests.
- The corrected lifespan persisted its actual final audit: zero native/provider/
  prohibited-subprocess attempts, zero work/requests/jobs/native associations, and
  the execution gate closed. Counters were observed, not filled in after failure.
- All owned processes and descendants exited. The original protected SSH session
  retained its start identity. Keys, cookies, pairing material and private logs stay
  in the private test directory.

This proves same-host authenticated SSH transport and the named application
operations. It does not claim a physical remote browser, model-message execution
or whole-film playback over SSH. Those remain part of cumulative M12/M14 acceptance.
