# HUM: AB's Windows installation and acceptance

**Prerequisite:** Wait until ITGuy gives you the exact private `http://<server-tailnet-ip>:<port>/hum` URL and confirms it is reachable only over your tailnet. `HUM-build.zip` is a normal ZIP, not a Rainmeter `.rmskin` installer.

1. **Join the same tailnet.** Install the official Windows Tailscale client, sign in yourself with the account authorized for your tailnet, and confirm the server appears as connected. Do not share the sign-in code or account credentials with Nora or ITGuy.
2. **Test the endpoint before Rainmeter.** In PowerShell, substitute the exact URL ITGuy gave you:

   `(Invoke-RestMethod 'http://<server-tailnet-ip>:<port>/hum').display.Count`

   Expected output: `10`. If it times out, ask ITGuy to check Tailscale connectivity, bind address, firewall, and tailnet policy before installing the skin. Keep this URL private.
3. **Install Rainmeter.** Download its current standard Windows installer from the official Rainmeter site and run it. Standard installation normally creates the skins folder under `%USERPROFILE%\Documents\Rainmeter\Skins`; if Documents is redirected, use the skins path shown in Rainmeter's Manage window.
4. **Use built-in fonts.** The skin uses Windows-provided Tahoma for labels and Consolas for figures. There is no font download or installation step.
5. **Place the skin.** Extract `HUM-build.zip` somewhere temporary. Create `%USERPROFILE%\Documents\Rainmeter\Skins\HUM\` and copy **only** `HUM.ini` from the extracted `HUM` folder into it. The Python code and ledger stay server-side; do not run them on Windows.
6. **Set your endpoint.** Open `HUM.ini` in Notepad. In `[Variables]`, replace the placeholder line `Endpoint=http://127.0.0.1:8765/hum` with `Endpoint=` followed by the exact private URL from ITGuy. Save as plain text, retaining the `.ini` extension; do not put a password or API key in it.
7. **Load the widget.** Right-click Rainmeter's notification-area icon, choose **Refresh all**, then open **Manage**. Select `HUM\HUM.ini` and click **Load**. If needed set its Position to **On Desktop** and leave **Click through** disabled so the header buttons remain clickable. The skin should sit behind normal application windows.
8. **Perform acceptance checks.** Wait for a successful fetch; the three PRD sections should appear and update on successive 90-second polls. The daily and 24-hour windows may show `PARTIAL` until their collection period is covered. Press `−` to collapse to the header, then `+` to restore. Press `×`: HUM should disappear and be **unloaded**, not merely hidden. To bring it back, use Rainmeter Manage and **Load** again. Rainmeter itself and ITGuy's server-side sampler will remain running.
9. **Check failure behaviour with ITGuy.** Have ITGuy temporarily stop only the private HUM endpoint. The card should retain its last figures and show `STALE` with the last successful update; restoring the endpoint should resume updates. If the screen blanks, the controls fail, or updates drift, send Nora a screenshot and the Rainmeter log excerpt without tokens, credentials, or personal data.

**Official sources:** Rainmeter's installation and manual-skin guides, and Tailscale's Windows installation guide. The chosen fonts are already supplied by Windows; use no third-party font package.
