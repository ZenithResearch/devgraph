"""Shared Wallet-owned connection controls; no secret inputs."""

WALLET_CONNECTION_HTML = r'''      <div class="wallet-connection" id="wallet-read-connection">
        <p data-wallet-status role="status" aria-live="polite">Preparing Wallet connection…</p>
        <div class="wallet-connection-actions">
<button type="button" class="button" data-wallet-chrome>Use Chrome Wallet</button>
<button type="button" data-wallet-web>Unlock or import Wallet</button>
<button type="button" data-wallet-advance hidden>Continue in Wallet</button>
<button type="button" data-wallet-request hidden>Request access</button>
<button type="button" data-wallet-disconnect hidden>Disconnect</button>
</div>
        <section class="wallet-embedded" data-wallet-embed hidden
aria-label="Embedded Castalia Wallet">
<header><strong>Castalia Wallet</strong><span data-wallet-origin></span></header>
<div data-wallet-frame></div>
</section>
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
<p>The installed Chrome Wallet is selected automatically. In other browsers, unlock
or import your Wallet below in the embedded Castalia Wallet.
Your file, password and keys stay in Wallet.</p>
<label>Wallet website<input data-wallet-url type="url" inputmode="url" autocomplete="url"
placeholder="https://wallet.example" spellcheck="false">
</label>
<p>Use the address supplied by your Wallet provider.
Your server and Wallet provider must both enable this embedded connection.</p>
</details>
      </div>'''
