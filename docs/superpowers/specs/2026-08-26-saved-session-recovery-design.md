# Saved Session Recovery Fix Design

## Goal

Prevent the account page from reporting that an existing Telegram login cannot be restored when the healthy background downloader is already using the same authorized Telethon session.

## Confirmed root cause and constraints

- The real downloader heartbeat is fresh and reports `running`.
- Saved API credentials validate successfully.
- `.runtime/telegram.session` passes SQLite integrity checks and contains an authorization key.
- The current GUI startup path always creates another `TelethonGateway` against that same SQLite session to call `is_user_authorized()`.
- Telethon may update its SQLite session while connecting or caching entities. Opening the same session from the running downloader and the GUI can therefore cause a transient lock conflict.
- The GUI currently discards the exception and replaces it with a generic recovery failure, leaving no retry action.
- The fix must not stop an active download, delete or copy the session, expose credentials, or add a permanent timer, thread, process, or network probe.

## Considered approaches

### 1. Trust a fresh running heartbeat, then fall back to the existing probe

When saved credentials exist and `GuiController.read_status()` returns `status == "running"`, treat the saved session as authorized without opening a second Telethon client. If the background is stopped, stale, needs login, or otherwise unhealthy, use the existing gateway authorization probe.

This is the selected approach. A fresh running heartbeat proves the background service recently connected and is operating with the same project credentials and session. It avoids session contention without interrupting downloads.

### 2. Stop the downloader before every recovery probe

This would make exclusive session access straightforward, but opening the GUI would interrupt active downloads and create avoidable service lifecycle work. It is rejected.

### 3. Probe a copied session database

This would avoid a direct SQLite lock, but a copied Telethon session can become inconsistent and may create a second connection identity. It is rejected.

## Controller behavior

`GuiController.saved_session_authorized()` keeps its current public interface.

1. Load saved credentials. If none exist, return `False`.
2. Read the normalized background status through `read_status()`.
3. If the status is fresh `running`, return `True` without constructing or connecting a gateway.
4. For stopped, stale, `needs_login`, malformed, or missing heartbeat state, construct the gateway and perform the existing connect / authorization / disconnect probe.
5. Always disconnect a gateway created by the fallback path.

The 15-second stale-heartbeat rule remains the single source of truth; no new freshness threshold is added.

## Account-page behavior

- Keep automatic recovery on startup when saved credentials exist.
- Add a lightweight `重试恢复` button in the existing account action row.
- Hide the button initially and while a recovery attempt is running.
- Hide it after an authorized or unauthorized result is received normally.
- On recovery exception, return all login controls to idle, show a redacted status such as `恢复失败：<安全错误>`, and reveal the retry button.
- Clicking the button reruns only the existing saved-session recovery operation.
- Starting QR login, phone login, successful recovery, logout, or closing the GUI hides the retry action.
- Do not show raw API credentials, phone numbers, QR tokens, passwords, or unredacted Telegram details.

## Testing

- Controller regression: a fresh running heartbeat returns authorized without creating a gateway.
- Controller fallback: stale or stopped state still constructs, connects, checks, and disconnects the gateway.
- GUI regression: recovery errors display the existing redacted error text and reveal an enabled retry button.
- GUI interaction: retry starts a new recovery attempt, disables itself while running, and hides after success.
- Existing authorized, unauthorized, QR, phone, logout, startup, and close tests remain green.
- Complete project checks, dependency checks, and a 900x720 account-page layout check must pass.

## Release boundary

- Prepare this fix as v0.3.3 because v0.3.2 is already published.
- No dependency changes.
- No service restart during implementation or automated verification.
- Publishing v0.3.3 requires a separate explicit merge-and-publish instruction after the candidate passes verification.
