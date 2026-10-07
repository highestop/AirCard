# Apple Wallet Card Skinner repository workflow

## Project language

- Write all repository documentation in English, including the README, files under `docs/`, and agent instructions. Translate documentation imported from upstream into English before including it.
- Use English for new or updated code comments, docstrings, commit messages, and pull request titles and descriptions.
- Preserve command syntax, paths, API identifiers, saved-data compatibility keys, exact diagnostic strings, and required legal notices when translating prose.
- Keep the main interface and artwork editor in Simplified Chinese only. Do not add language selectors or locale-switching infrastructure unless explicitly requested. Repository documentation remains in English; multilingual test fixtures may retain the text needed for their tests.
- Reply to the user in their preferred language; the repository's English documentation policy does not require English conversation.

## Personal fork policy

- Use `Apple Wallet Card Skinner` as the app and product display name in documentation prose and headings, page titles, interface branding, accessible labels, CLI output, and logs.
- Use `AppleWalletCardSkinner` as the project slug in the GitHub repository name, clone-directory name, URLs, exported artwork filenames, and internal executable names. Name the macOS app bundle `Apple Wallet Card Skinner.app`, with spaces matching its display name. Keep `origin` pointing to `https://github.com/highestop/AppleWalletCardSkinner.git`. Preserve the slug in commands and technical path examples; do not replace it with the display name.
- Keep `~/Library/Application Support/AppleWalletCardSkinner/` as the stable technical data path for new installations, independently of the display name. If it does not exist, reuse an existing `apple-wallet-card-skinner` data directory first, then `AirCard`, without moving files. An explicit `--data-dir` always wins.
- Preserve technical compatibility identifiers when changing display names: saved-data keys, legacy directory lookup, device-lock namespaces, HTTP token headers, and the `apple-wallet-card-skinner-artwork` message channel. Historical scanner prefixes must remain readable.
- This fork is for personal customization. Keep the app and user-facing documentation free of donation prompts, social links, contributor showcases, and community participation calls.
- Keep only Apple Wallet card artwork discovery, preparation, and synchronization. Do not reintroduce passcode themes or unrelated features when importing upstream changes.
- The main interface and artwork editor are native SwiftUI / AppKit views in a standalone, locally signed macOS app. Build the Swift service and Objective-C USB helpers using Xcode and system frameworks; the build and finished app must not require Python, Homebrew, or third-party packages. Keep device verification, persistence, and write authorization in the native service, accessed through the app's private standard-input/output pipes. Retained Python and browser sources are legacy reference and optional regression tests only. Do not bundle or load a browser interface, or introduce DMG packaging unless requested.
- Publish native macOS source and portable build instructions only. Keep generated app bundles, native binaries, build caches, and local build diagnostics out of Git and GitHub Releases unless the user explicitly requests artifact distribution. Do not embed developer home paths, real device or card identifiers, private artwork, or signing credentials in source; use synthetic test fixtures. Store local data outside the checkout or in the ignored `local-data/` directory.
- Preserve required copyright and license notices in `LICENSE` and any third-party notices that remain applicable. Historical Git attribution and compatibility keys used for saved data are not promotional content.
- When importing upstream changes, retain useful functionality while removing any newly introduced UI or documentation that conflicts with this policy.

## Upstream synchronization

- This repository is the personal fork `highestop/AppleWalletCardSkinner`. Fetch upstream changes from `mak5er/AirCard` (`upstream`), and push branches only to `highestop/AppleWalletCardSkinner` (`origin`). If a new clone lacks `upstream`, add `https://github.com/mak5er/AirCard.git` as that remote. Open synchronization PRs against **the fork's `main`**, never against the upstream repository.
- When the user asks to synchronize upstream, fetch `origin` and `upstream`, then create a new `codex/sync-upstream-YYYYMMDD` branch from the current `origin/main`. Do not commit directly to `main`.
- Identify upstream commits by Git ancestry and commit IDs, not by author or commit dates. At setup on 2026-10-04, both `main` branches pointed to `d887c6e44ee0ecf89dd4e9f83d1c0648ca7697eb`. On later runs, exclude commits already applied in the fork, checking both patch equivalence and the source-commit trailers from prior cherry-picks. Keep previously skipped commits eligible for future selection.
- Unless the user gives a different selection rule, apply all eligible upstream changes in dependency order, subject to the personal fork policy above. Use `git cherry-pick -x` for ordinary commits so the source commit ID survives the fork's rebase merge. Inspect upstream merge commits separately so merge-only changes are not missed. Resolve conflicts while preserving the fork's customizations, and run relevant checks.
- Push the synchronization branch to `origin`, open a PR with `highestop/AppleWalletCardSkinner:main` as its base, and list the upstream source commits and any skipped commits in the PR description. After checks and review are clear, use **rebase merge** and delete the branch. The user's synchronization request authorizes this workflow. If there are no eligible changes, report that and do not create a PR.
