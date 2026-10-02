# n-codex-accounts

**Multiple Codex accounts. One shared chat history.**

Switch between accounts in your terminal or VS Code, run separate chats side by side, and pick up an existing conversation with another account. Each account keeps its own login.

**macOS · zsh · Codex CLI · VS Code**

An independent community tool. Windows, WSL, and Linux are not supported.

## Get started

### 1. Check what you need

- **Python 3.11+**
- **Codex CLI 0.160.0** on your `PATH` (0.158.0, 0.159.0, and 0.159.2 also supported)
- A ChatGPT account with Codex access
- **VS Code 1.96.2+**, only if you want editor support

Don't have the CLI yet? Install the reviewed version:

```sh
npm install -g @openai/codex@0.160.0
```

Already using a newer CLI? Check compatibility before changing versions; don't downgrade an existing installation blindly.

### 2. Run the installer

```zsh
zsh <(curl -fsSL https://raw.githubusercontent.com/khandelwaly940/n-codex-accounts/main/install.sh)
```

Follow the prompts. Your existing file-based Codex login becomes **`primary`**. If you don't have one, the installer offers a browser login. You can then add accounts with names such as **`work`** or **`personal`**.

To also install the reviewed VS Code extension, put VS Code in `/Applications` and use this command instead:

```zsh
zsh <(curl -fsSL https://raw.githubusercontent.com/khandelwaly940/n-codex-accounts/main/install.sh) --with-vscode
```

This installs `openai.chatgpt@26.917.62051` and its required extension dependencies. Without the flag, your editor extensions are left alone.

The installer downloads the latest published release and verifies its checksums before installing. This guide's reviewed CLI and extension versions still apply; the installer stops if your CLI version is unsupported by that release.

### 3. Start using it

```zsh
source ~/.zshrc
codex primary
```

Answer **`y`** to open Codex, or **`n`** to select the account without opening a chat. Add another account whenever you need one:

```zsh
codex add work
```

Each new account requires its own browser login. Existing account credentials are not overwritten.

## Already using an older two-account setup?

Download [ncodex-bootstrap.command](https://github.com/khandelwaly940/n-codex-accounts/releases/latest/download/ncodex-bootstrap.command) from the release assets. In Terminal, run:

```zsh
python3 ~/Downloads/ncodex-bootstrap.command
```

Requires macOS and Python 3.11+. The helper checks the latest release, verifies its archive checksum, identifies your installation, and shows the proposed migration. **Nothing is applied until you approve the plan.** For inspection only, add `--plan`.

Existing names and login files are preserved. If transcript folders need joining, the helper validates a combined copy and retains the original folders. Close the affected CLI/editor/daemon processes before approving that migration. It never kills processes or logs accounts in/out.

Marked two-account shell setups using a shared canonical SQLite home, older registry-based setups, and earlier public installations are recognized. Unknown shell layouts, independent account databases, conflicting files, or missing history stop with an explanation. They require a separate reviewed migration—not a forced update.

After success, open a new terminal or run `source ~/.zshrc`.

## Updates that wait for you

On the first interactive `codex <account>` or `vscode-account <account>` launch each day, the helper checks GitHub for up to three seconds. No account names, credentials, or chats are sent.

- **Update now:** see a short change summary and any migration requirement, then approve separately. Enter `d` for concise details or use `ncodex update --plan` to inspect account paths.
- **Not now:** launch normally; the next reminder is on the next day's first launch.
- **Offline:** launch normally. There is no scheduled background job.

```zsh
ncodex update --check   # Check the release without installing
ncodex update --plan    # Inspect changes and migration requirements
ncodex update           # Plan, ask approval, back up, apply, validate
ncodex doctor           # Check layout, SQLite and rollout lineage
ncodex rollback         # Guarded helper/config rollback where safe
```

A helper-only update can leave existing chats running. Don't start additional launches during installation. History migrations require affected writers to stop. Codex CLI and editor extensions are not upgraded by this command. Public launches use normal approvals; this is shown in the migration plan when moving from an older setup.

Backups and an interrupted-update journal live under `~/.local/share/n-codex-accounts`. Completed history migrations are **not automatically rolled back later**, because that could hide newer chats. Original folders are retained for reviewed recovery. If the shell helper is unavailable after interruption, the downloaded bootstrap accepts `--rollback` for eligible helper/config recovery. No rollback replaces credentials or databases.

Update errors show a short reason; longer diagnostic text is saved privately in `~/.local/share/n-codex-accounts/update-error.json`. Users upgrading from 0.2.1 or earlier may see the previous update screen once; the compact screen applies after installing 0.2.2 or later, or immediately through the latest downloaded bootstrap.

## Everyday use

| I want to… | Run |
| --- | --- |
| Use an account | `codex work` |
| See my accounts | `codex accounts` |
| Check my current selection | `codex status` |
| Add another account | `codex add personal` |
| Open VS Code with an account | `vscode-account work` |
| See an account's running processes | `codex processes work` |

**Run accounts side by side:** open separate terminals and select a different account in each. Use a different chat in each terminal.

**Switch VS Code accounts:** fully quit VS Code, then run `vscode-account <name>`. Multiple windows share the editor process, so this launcher selects one account for the running editor. You can pass a project folder after the account name.

**Continue a chat with another account:** finish the current turn and exit that chat first. Then:

```zsh
codex work
# Answer n to select without opening a new chat.
codex resume --all
```

Choose the earlier conversation. Never write to the same chat from two accounts at once. The selected account still needs access to the models and tools used by that chat.

Normal Codex approvals remain enabled. Other CLI subcommands work as usual.

## Compatibility

CLI baseline updated on **2 October 2026**; extension review from 27 September 2026:

| Component | Version |
| --- | --- |
| Codex CLI | **0.160.0** recommended; **0.158.0**, **0.159.0**, **0.159.2** supported |
| VS Code extension `openai.chatgpt` | **26.917.62051** |
| Codex bundled with that extension | **0.155.0-alpha.16.3** |
| This installer | **0.2.4** |

**Offline regression tests cover** installation, repeat installation, account routing, migration, shared-history checks, failure recovery, and daily notices. The extension package and bundled command interface were inspected separately. Real login, model requests, and VS Code chats were not used to certify this release. After installing, try a simple request per account and a sequential cross-account chat resume. Intel macOS has not been tested on Intel hardware.

<details>
<summary><strong>Troubleshooting: one account needs to log in again</strong></summary>

Finish and close only that account's CLI and VS Code sessions. Its background daemon may still be running. For an account named `work`:

```zsh
codex processes work
env CODEX_HOME="$HOME/.codex-accounts/work" CODEX_SQLITE_HOME="$HOME/.codex" \
  "$CODEX_REAL_BINARY" app-server daemon stop
codex processes work
# Continue once the list is empty:
codex work relogin
```

For `primary`, use `CODEX_HOME="$HOME/.codex"` and `codex primary relogin`. Choose the same browser identity and workspace.

Recovery logs in through a temporary home, checks the identity and active processes, saves a private backup, and replaces only that account's credential after checks succeed. Keep its processes closed during recovery. Don't delete credentials or run logout to troubleshoot. A local backup cannot restore a token revoked by the server.

</details>

<details>
<summary><strong>What is shared, and what stays separate?</strong></summary>

- **Shared:** local sessions, archives, attachments, writer locks, and SQLite state.
- **Separate:** Codex credentials, daemon installations, MCP OAuth stores, and plugin caches.
- **MCP settings:** copied from the primary account when adding an account, including any inline configuration values. Later edits don't automatically synchronize. External services may need separate authorization.

Primary login and history live in `~/.codex`. Added accounts live in `~/.codex-accounts/<name>`. The registry at `~/.config/n-codex-accounts/accounts.json` stores names and paths, not credentials.

Existing secondary account homes are not imported or merged. Occupied account directories are refused. This provides account selection, not OS-level isolation: your filesystem user can access all these homes.

</details>

<details>
<summary><strong>Installation options, updates, and removal</strong></summary>

You can inspect [install.sh](install.sh) and [setup/install.py](setup/install.py), or clone this public repository and run `zsh install.sh` locally. Use `--cli /absolute/path/to/codex` if the real executable isn't on `PATH`.

The installer requires file-based ChatGPT credentials. Keychain-only logins and existing custom account switchers need separate review; it refuses automatic conversion. It installs helpers under `~/.local/share/n-codex-accounts`, backs up `.zshrc` and previous helper files, and adds one managed shell block. It doesn't install system prerequisites or silently update Codex. Re-running it refreshes helpers while preserving account credentials and registry entries.

Before upgrading, finish work and close affected CLI/editor processes and daemons. Review CLI and extension versions separately: npm does not update the extension's bundled binary. With a reviewed CLI installed, `app-server daemon update --from-cli` and the selected account's `CODEX_HOME` can pin that daemon to the CLI. This may restart work. Disable extension auto-updates if you want to stay on reviewed versions.

To remove shell integration, delete the `N_CODEX_ACCOUNTS` block from `.zshrc` and open a fresh terminal. Leave account homes and shared history intact.

For explicit unsandboxed execution, select an account with `n`, then run `codex --dangerously-bypass-approvals-and-sandbox`. This is optional and not the default.

</details>

<details>
<summary><strong>Development and releases</strong></summary>

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
```

Build release assets with `python3 tools/build-release.py /path/to/release-output`, then upload the archive, bootstrap helper, and `SHA256SUMS` to the corresponding GitHub Release. Downloads are SHA-256 checked against that release; checksums provide integrity checking, not protection from a compromised publisher account.

Tests use temporary directories and fabricated credentials; they never invoke the relogin flow. Port reviewed changes into `setup/`, add tests, update compatibility notes, scan staged files, and publish a version tag. Export only the allowlisted public files in the release builder. Keep release output outside the repository. Never mirror an internal repository or its history. Keep the bootstrap's download tag aligned with each release.

</details>

[Codex release notes](https://learn.chatgpt.com/docs/changelog) · [Official configuration guide](https://learn.chatgpt.com/docs/developer-settings) · [Authentication](https://learn.chatgpt.com/docs/auth) · [MIT license](LICENSE)
