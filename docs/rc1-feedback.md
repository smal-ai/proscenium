# 0.1.0-rc1 feedback

Branch: `version/0.1.0-rc1`, based on `main` at `5e57e3b`.

## 15 November 2026

- Chat follows new requests, streamed replies and tool activity. Scrolling back pauses following. **Jump to latest** restores it. Composer resizing and switching panels retain the correct position.
- Usage is a dim, right-aligned monospace readout. The expanded usage cards and explanatory footer are removed.
- **Jobs** and **Context** are links beneath the composer. Their panels replace the text box temporarily. Closing a panel restores the draft and focus.
- Failed and uncertain tool calls show their reason immediately, with technical identifiers collapsed. Older events without a recorded reason say so explicitly.
- New tool failures retain bounded, sanitised error messages. Confirmed automatic-policy refusals, owner decisions and host holds are distinguished.
- An attributed native abort holds its original work for explicit Resume instead of leaving it running. This is interruption evidence, not a confirmed Stop barrier or successful completion.

The investigated storyboard run was automatically rejected by the shell guard
because its discovery command included protected environment/credential terms in
exclusion filters. The SDK reported this as a user rejection. No owner decision
had been made. The access policy is unchanged by these reporting fixes.

The pinned SDK's legacy `assistant.usage.cost` is premium-request usage, so its
readout is labelled **premium credits**. Explicit units are retained and missing
amounts are shown as a dash. No AI-credit or currency conversion is invented.
Source: [Copilot SDK 1.0.34 usage reference](https://github.com/github/copilot-sdk/blob/v1.0.34/docs/features/usage-and-billing.md#per-call-token-counts).

Verification: `make check` passed, including 27 tests. An isolated Chromium
component check exercised live following, scrollback, tool failures, usage styling,
composer panels, draft preservation, resizing and hidden-tab restoration. Those
browser inputs were synthetic and made no API/model calls.
