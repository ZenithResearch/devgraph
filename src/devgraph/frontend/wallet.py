"""Shared Wallet-owned connection controls; no secret inputs."""

WALLET_CONNECTION_HTML = r'''      <div class="wallet-connection" id="wallet-read-connection">
        <header class="wallet-connection-heading">
<strong>Access DevGraph with Wallet</strong>
<span data-wallet-server aria-label="Current DevGraph server"></span>
</header>
<ol class="wallet-connection-steps" aria-label="Wallet access progress">
<li data-wallet-step="1">Connect Wallet</li>
<li data-wallet-step="2">Request or check access</li>
<li data-wallet-step="3">Receive credential</li>
</ol>
        <p data-wallet-status role="status" aria-live="polite">Preparing Wallet connection…</p>
<p data-wallet-session-expiry hidden></p>
<p data-wallet-provider-status role="status" aria-live="polite" hidden></p>
        <div class="wallet-connection-actions">
<button type="button" class="button" data-wallet-chrome>Use Wallet</button>
<button type="button" data-wallet-advance hidden>Continue in Wallet</button>
<button type="button" data-wallet-session-setup hidden>Set up browser session</button>
<button type="button" data-wallet-request hidden>Request access</button>
<button type="button" data-wallet-retry hidden>Retry connection setup</button>
<button type="button" data-wallet-disconnect hidden>Disconnect</button>
</div>
<p data-wallet-request-status role="status" aria-live="polite" hidden></p>
<section data-wallet-request-options hidden aria-label="Access request preferences"></section>
<button type="button" data-wallet-new-request hidden>Request different access</button>
        <label data-wallet-membership-label hidden>Membership<select data-wallet-membership>
<option value="">Choose membership</option>
</select>
</label>
        <details class="wallet-connection-settings" data-wallet-access-details hidden>
<summary>Public connection details for your administrator</summary>
<p>Your administrator can use this Member Key and Wallet installation to assign a scope.
These public details contain no password, key material, membership proof or credential,
and do not grant access.</p>
<pre data-wallet-access-json></pre>
<button type="button" data-wallet-copy>Copy public connection details</button>
</details>
        <details class="wallet-connection-settings" data-wallet-settings>
<summary>Wallet connection help</summary>
<p>A compatible Wallet supplied by your browser or its host app is detected
automatically. This includes a native Wallet connected by an app’s browser.
An extension or native Wallet connects directly.
Your file, password and keys stay in your chosen Wallet.</p>
<p>Wallet checks existing access before you need to request anything.
Request access only when no current grant is available or you need different permissions.
The service owner’s approval remains until its expiry or revocation; signing in
again does not require another owner review. Wallet reviews the read scope and
verifies the returned credential automatically.</p>
<p>If Wallet is not detected, use the Chrome profile where Castalia is installed
and enable it for this site. Choose Check for Wallet to detect it again.
This page never asks you to enter a Wallet website or recovery password.</p>
</details>
      </div>'''
