# n-codex-accounts

Use multiple ChatGPT accounts with Codex CLI and VS Code. Keep account logins separate, run different chats simultaneously, and continue a local chat under another account after closing its previous session.

**macOS + zsh only.** Windows, WSL, and Linux are not supported by this installer. This is an independent community tool, not an OpenAI product.

## Install

Prerequisites: Python **3.11+**, Codex CLI **0.157.1** on `PATH`, and a ChatGPT account with Codex access. Install the CLI separately with `npm install -g @openai/codex@0.157.1` if needed. Do not downgrade an existing newer installation without reviewing its state migrations.

Run this one command in a macOS Terminal:

```zsh
zsh <(curl -fsSL https://raw.githubusercontent.com/khandelwaly940/n-codex-accounts/v0.1.0/install.sh)
```

The installer reuses your existing file-based login at `~/.codex/auth.json` as **primary**. If none exists, it offers an isolated browser login. It then offers to add more named accounts; each needs its own browser authentication. Existing account credentials are not overwritten. Keychain-only logins and pre-existing custom account switchers require separate review; the installer refuses automatic conversion.

Activate the commands in your current terminal:

```zsh
source ~/.zshrc
```

For VS Code, install the application in `/Applications`, then add `--with-vscode` to the installation command. This installs `openai.chatgpt@26.917.62051` and any extension dependencies required by its publisher. Without that flag, no editor extensions are installed or updated. Inspect [install.sh](install.sh) and [setup/install.py](setup/install.py) before running if you prefer; you can also clone this public repository and run `zsh install.sh` locally.

The installer backs up `.zshrc` and previous helper files, installs under `~/.local/share/n-codex-accounts`, and adds one managed shell block. It does not install macOS packages or silently update Codex. Re-running the installer refreshes these helpers while preserving the registry and credentials. Use `--cli /absolute/path/to/codex` if the real executable is not on `PATH`.

## Everyday commands

| Task | Command |
| --- | --- |
| List accounts | `codex accounts` |
| Add an account later | `codex add work` |
| Select primary / work | `codex primary` / `codex work` |
| Show current shell selection | `codex status` |
| List account processes | `codex processes work` |
| Open VS Code with an account | `vscode-account work [folder]` |

Selection asks whether to launch Codex. Answer `y` to open it with normal approvals; `n` only selects the account. Ordinary Codex subcommands still work. For explicit unsandboxed operation, select an account with `n`, then run `codex --dangerously-bypass-approvals-and-sandbox` yourself.

Use a separate terminal for each active account. Fully quit VS Code before changing its account; multiple VS Code windows share a process and cannot use this launcher to select independent accounts simultaneously.

To continue another account's chat, finish its turn and exit that chat first:

```zsh
codex work
# Answer n.
codex resume --all
```

Pick the existing chat. **Never have two accounts write to the same thread concurrently.** Shared history does not transfer model, workspace, or tool permissions.

## Storage and MCPs

- Primary login/history: `~/.codex`; additional homes: `~/.codex-accounts/<name>`.
- Registry: `~/.config/n-codex-accounts/accounts.json` (names and paths only).
- Sessions, archives, attachments, writer locks, and SQLite state are shared locally; Codex credentials and daemon installations remain separate.
- Adding an account copies primary MCP definitions, including any inline configuration values. Future changes do not auto-sync. MCP OAuth stores and plugin caches are not copied; authorize external services separately where required.

Existing secondary account homes/history are not imported or merged. The tool refuses to overwrite an occupied new-account directory. Your filesystem user can access all these homes; this is account selection, not OS-level security isolation.

## Recover one login

First finish and close only that account's CLI and VS Code sessions. A daemon can remain active after terminals close. For the account named `work`:

```zsh
codex processes work
env CODEX_HOME="$HOME/.codex-accounts/work" CODEX_SQLITE_HOME="$HOME/.codex" \
  "$CODEX_REAL_BINARY" app-server daemon stop
codex processes work
# Once empty:
codex work relogin
```

For primary, use `CODEX_HOME="$HOME/.codex"`. Choose the same browser identity/workspace. Recovery stages login, verifies identity, checks for active processes and changed credentials, saves a private backup, and atomically replaces only that account's auth file. Do not start new account processes during recovery. Do not delete auth files or run logout to troubleshoot. Local backups cannot revive a server-revoked refresh token.

## Compatibility and verification

Release baseline, **27 September 2026**:

| Component | Reviewed version | Evidence |
| --- | --- | --- |
| Codex CLI | **0.157.1** | npm stable version, local version/command checks, daemon command syntax |
| VS Code extension `openai.chatgpt` | **26.917.62051** | Marketplace stable macOS package, SHA-256 and manifest inspected; requires VS Code 1.96.2+ |
| Extension's bundled Codex | **0.155.0-alpha.16.3** | Isolated binary version and resume-help checks |
| Account manager | **0.1.0** | Temporary-home installer, repeat-install, routing, shared-link, integrity and safety regression tests |

These are targeted compatibility versions, not an end-to-end certification. No real account login, relogin, model request, or VS Code chat was used to test this public release. After installing, verify a simple request per account, a sequential cross-account resume, and a VS Code request before relying on the setup. Intel macOS is accommodated by executable discovery but has not been tested on Intel hardware.

Before updating Codex, finish active work and close affected CLI/editor processes and daemons. Review CLI and extension releases separately; npm does not update an editor's bundled binary. Once the new CLI is reviewed, `app-server daemon update --from-cli` with the selected account's `CODEX_HOME` can pin its daemon to that CLI. This may restart work and is not an audit command. Keep extension auto-updates disabled if you need a reviewed version baseline.

References: [Codex release notes](https://learn.chatgpt.com/docs/changelog), [configuration](https://learn.chatgpt.com/docs/developer-settings), [authentication](https://learn.chatgpt.com/docs/auth).

## Development and releases

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
```

Tests use temporary directories and fabricated credentials; they never invoke the relogin flow. Release updates are curated: port reviewed source changes into `setup/`, add tests, update compatibility notes, scan staged files, and publish a version tag. Export only this public tree's source, tests, README, license, installer, and ignore rules. Never mirror an internal repository or its history. Keep the tag in the bootstrap URL aligned with each release.

To disconnect shell integration, remove the `N_CODEX_ACCOUNTS` block from `.zshrc` and open a fresh terminal. Leave account homes and shared history intact; do not delete them as part of removing the helper.
