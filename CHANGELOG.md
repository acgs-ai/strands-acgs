# Changelog

All notable changes to this project are documented in this file.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/). Entries for new releases are generated with `cz bump`.

## [Unreleased]

## 0.1.0 (unreleased)

### Added

- `ACGSIntervention`, a Strands `InterventionHandler` that maps ACGS verdicts to `Proceed`, `Deny` and `Confirm`
  in `before_tool_call`.
- `format_tool_call`, the documented text representation of a tool call that ACGS rules are evaluated against.
- Fail-closed handling: errors during evaluation return `Deny`, and `on_error` is `"deny"`.
- `on_review` (`"confirm"` or `"deny"`) and `confirm_evaluate` options for rules that require human review.
