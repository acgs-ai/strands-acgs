# Changelog

All notable changes to this project are documented in this file.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/). Entries for new releases are generated with `cz bump`.

## [Unreleased]

## 0.1.1 (2026-10-20)

### Fixed

- When a blocking rule and a review rule both match a tool call, the denial reason now lists only the blocking
  rules after "Blocked by" and names the matched review rules separately ("Review rules that also matched: ...").
  Previously the review rule was also listed under "Blocked by". The verdict was already `deny` and is unchanged;
  `rule_ids` still contains every matched rule.

## 0.1.0 (2026-10-19)

### Added

- `ACGSIntervention`, a Strands `InterventionHandler` that maps ACGS verdicts to `Proceed`, `Deny` and `Confirm`
  in `before_tool_call`.
- `format_tool_call`, the documented text representation of a tool call that ACGS rules are evaluated against.
- Fail-closed handling: errors during evaluation return `Deny`, and `on_error` is `"deny"`.
- `on_review` (`"confirm"` or `"deny"`) and `confirm_evaluate` options for rules that require human review.
