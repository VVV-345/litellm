# macOS Camoufox helper management

> Execute with superpowers:subagent-driven-development, followed by focused review and real local verification

## Scope and decisions

The user chose macOS first, then clarified that this work should mainly improve deployment management. Extend only tools/camoufox-helper and this plan. Keep the CLIProxyAPI分支 branch. Commit after local verification; never push. No gateway, dashboard, Docker, or server deployment changes

Provide a double-clickable 管理.command and a dependency-free Node.js manage.mjs menu. Node.js 22+ and independently installed Camoufox remain prerequisites; detect them and give exact remediation. Locate a suitable installed Node even when the user's default shell selects an older version. Do not bootstrap unverified binaries or silently download a browser. The menu owns setup, background start/stop/status/diagnostics, displaying the helper Key on explicit request, local-package install/update and rollback. Existing node cli.mjs and OAuth APIs must keep working

Use a user launchd job local.litellm.camoufox-helper. Store its plist under the private helper state directory, not ~/Library/LaunchAgents, so starting the helper does not opt users into automatic login startup. Do not kill PIDs or stop unrelated/manual helpers. The operator can stop an old manually launched helper before starting the managed one

The state root remains ~/.litellm-camoufox. Save validated settings.json (origins, port, callbackPort, executable) with 0600 permissions. Launchd invokes the installed cli.mjs with the existing explicit flags and the selected state directory. All paths must support spaces and Unicode, without shell interpolation. Use Node built-ins for the management entrypoint so installing dependencies can happen before importing zod or Playwright

Distribute a standalone ZIP containing only helper code, lockfile, README and launch script plus a SHA-256 manifest. Do not include tests, node_modules, keys, browser data or logs. Package generation and installation verify explicit file lists and reject traversal, symlinks and checksum mismatches. Hashes establish integrity, not publisher identity. Online release hosting is not available, so updates consume an extracted, explicitly selected newer local package; rerunning a newly downloaded package's 管理.command offers installation/update. Do not claim automatic online updates

Install to a versioned application directory below the state root, use npm ci --ignore-scripts with the lockfile, and switch the active version only after installation and smoke verification. Preserve settings, helper.key, registry and profiles. Stage failures leave the previous version selected; restart failures restore the previous managed service selection. Retain old versions for rollback, avoid broad cleanup

Preserve existing helper.key, registry.json and profiles byte-for-byte during installation and updates. Environment listing, labels, metadata recording, backup/reset/restore and offline profile opening are deferred so this deliverable stays focused on deployment management. Document how the persistent state is separate from replaceable application versions

## Task 1: Implement the helper manager as one integrated deliverable

Files: create manage.mjs, a small service/config module, a package installer module, corresponding *.test.mjs, 管理.command and package-release.mjs. Choose concise names and avoid unnecessary file sprawl. Update package.json/package-lock.json for a 0.2.0 helper release and Node >=22, and README.md with the short new-user flow

- [x] Write and run failing tests for configuration validation/permissions and safe launchd arguments, versioned install and failure preservation, credential exclusion from packages, traversal/symlink rejection and persistent-state preservation
- [x] Implement dependency-free management modules without changing existing OAuth behavior
- [x] Implement menu and equivalent noninteractive commands with --help, explicit --data-dir for isolated verification, and --json for status/diagnostics where useful. Noninteractive mutations require an explicit command
- [x] Implement standalone package generation and document extract/double-click, configure once, start in background, diagnose failures, update from a new package and roll back
- [x] Run all helper tests with CAMOUFOX_TEST_EXECUTABLE=/Applications/Camoufox.app/Contents/MacOS/camoufox, bash syntax validation, package generation and isolated installation verification
- [x] Review the complete diff, fix findings, rerun affected checks, and commit. Real launchctl integration passed in an isolated temporary data directory; existing installation migration was unnecessary because no managed service was present

## Verification boundaries

Agents must not operate real launchd services, change ~/.litellm-camoufox, open real account profiles, generate OAuth sessions or touch Docker. Tests use temporary directories and injected command runners. Primary verification should use a temporary state root and unused loopback port with the real managed service, then remove that test job; existing Key and account profiles must remain unchanged. Do not claim Windows support or a published download channel
