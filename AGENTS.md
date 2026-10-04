# AirCard repository workflow

## Personal fork policy

- This fork is for personal customization. Keep the app and user-facing documentation free of donation prompts, social links, contributor showcases, and community participation calls.
- Keep only Apple Wallet card artwork discovery, preparation, and synchronization. Do not reintroduce passcode themes or unrelated features when importing upstream changes.
- Preserve required copyright and license notices in `LICENSE` and any third-party notices that remain applicable. Historical Git attribution and compatibility keys used for saved data are not promotional content.
- When importing upstream changes, retain useful functionality while removing any newly introduced UI or documentation that conflicts with this policy.

## Upstream synchronization

- This repository is the personal fork `highestop/AirCard`. Fetch upstream changes from `mak5er/AirCard` (`upstream`), and push branches only to `highestop/AirCard` (`origin`). If a new clone lacks `upstream`, add `https://github.com/mak5er/AirCard.git` as that remote. Open synchronization PRs against **the fork's `main`**, never against the upstream repository.
- When the user asks to synchronize upstream, fetch `origin` and `upstream`, then create a new `codex/sync-upstream-YYYYMMDD` branch from the current `origin/main`. Do not commit directly to `main`.
- Identify upstream commits by Git ancestry and commit IDs, not by author or commit dates. At setup on 2026-10-04, both `main` branches pointed to `d887c6e44ee0ecf89dd4e9f83d1c0648ca7697eb`. On later runs, exclude commits already applied in the fork, checking both patch equivalence and the source-commit trailers from prior cherry-picks. Keep previously skipped commits eligible for future selection.
- Unless the user gives a different selection rule, apply all eligible upstream changes in dependency order, subject to the personal fork policy above. Use `git cherry-pick -x` for ordinary commits so the source commit ID survives the fork's rebase merge. Inspect upstream merge commits separately so merge-only changes are not missed. Resolve conflicts while preserving the fork's customizations, and run relevant checks.
- Push the synchronization branch to `origin`, open a PR with `highestop/AirCard:main` as its base, and list the upstream source commits and any skipped commits in the PR description. After checks and review are clear, use **rebase merge** and delete the branch. The user's synchronization request authorizes this workflow. If there are no eligible changes, report that and do not create a PR.
