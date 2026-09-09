# eirouter Register

Standalone Windows registration tool. It does not import or require zcode-farm.

Run `register_eirouter.bat`, enter the account count, then choose the mailbox
service. It registers accounts sequentially. A failure stops the queue. If Cloudflare asks
for interaction, complete it in the visible browser; the script then continues.

Mailbox names combine random words with a random suffix. Mail.tm domains are
selected randomly from all pages of its active public domain list. If the API
offers only one domain, all Mail.tm addresses necessarily use that domain.

Each Python CLI run (including the batch launcher) writes console output to a
separate UTF-8 file in `logs/run_YYYY-MM-DD_HH-MM-SS_microseconds.log`.
The file path is printed at startup. Logs include timestamps and the exit code;
API keys and proxy credentials are masked in log files. Existing logs are retained.

## AdsPower

The launcher automatically selects AdsPower when `adspower.local.json` exists.
Keep AdsPower running with Local API enabled. The local settings file contains
`base_url` and `api_key`; `ADSPOWER_API_KEY` overrides the saved key. The API is
called directly through httpx, so the Node MCP server is not required.

Each registration creates a separate AdsPower profile and connects through its
CDP endpoint. AdsPower controls the browser fingerprint, language and timezone.
When proxy loading is enabled, `proxies.txt` supplies proxies for new profiles. The mapping from
the account's `device_uuid` to AdsPower's profile ID is kept under
`profiles/<device_uuid>/adspower.json`. Keep that mapping to resume accounts.
After the email, password and API key are saved in JSON and keys are exported
to TXT, the script stops and deletes its newly created profile before the next
registration. One free AdsPower profile slot is sufficient for a sequential
queue, subject to any other AdsPower or service limits. Failed or incomplete
registrations retain their profiles and stop the queue. A deletion failure
also stops the queue; saved credentials remain available.
Explicitly selected profiles and older mappings without an ownership marker
are retained. Previous profiles are not deleted retroactively.
Deleted profiles are marked in the mapping; their cookies are gone and
`--resume` cannot reopen them. The saved email, password and API key remain usable.

The supplied configuration sets `use_proxy_file` to `false`: AdsPower runs
through the system route by default. In live testing, Cloudflare passed in
AdsPower on that route while the supplied proxy did not pass in the same new
profile. The proxy file is retained. Use `--proxies-file proxies.txt` to request
it explicitly, or `--direct` to disable proxies for new profiles.

An existing profile can be selected explicitly for one account:

```powershell
py -3 -m providers.eirouter --browser adspower --adspower-profile PROFILE_ID --mail-service mail.tm --count 1
py -3 -m providers.eirouter --browser adspower --adspower-profile PROFILE_ID --resume EMAIL
```

Existing profiles retain their AdsPower proxy settings and ignore the default
proxy file. Their Mail.tm API connection uses the system route. When the profile
was already running, only tabs opened by the script are closed. Profiles started
by the script are stopped through AdsPower after completion, preserving data.
The AdsPower API key is stored only in the Git-ignored settings file and never printed.
To run the previous Chrome backend, select `--browser chrome`.

Accounts are saved immediately to `eirouter_accounts.json` in this directory.
Each record contains `email`, `password`, `api_key`, `status` and `device_uuid`.
Existing records are retained. `registration_pending` means registration is
unconfirmed; `no_key` means it succeeded but key capture did not finish.
Only confirmed records with a key count towards the completion report.
`eirouter_keys.txt` contains one unique API key per line, without labels or
passwords. It is refreshed atomically from the account JSON after each key is
saved and on startup when that JSON already exists. For a custom `--output`,
the TXT is beside it (for example, `batch_accounts.json` -> `batch_keys.txt`).

## Browser Profiles

With `--browser chrome`, each registration has a separate persistent Chrome profile under
`profiles/<device_uuid>/chrome`. Window/screen dimensions are selected from a
desktop set and retained. Native Chrome supplies the actual User-Agent,
Client Hints, platform and graphics properties. Fingerprints are not guaranteed
unique, and these settings do not guarantee Cloudflare clearance.

By default the launcher uses the main project's compatibility flags:
`--disable-blink-features=AutomationControlled` and omission of
`--enable-automation`. Proxy connections also restrict non-proxied WebRTC UDP.
`--standard-browser` disables the automation-related compatibility flags for
comparison. JavaScript fingerprint patches, fake Chrome versions and CAPTCHA
solver services are not installed.

The timezone and locale are selected from the exit IP using HTTPS to ipwho.is
on the same route as registration. If lookup fails, the browser uses the system
timezone and en-US. `--timezone`, `--locale` and `--no-geo` override this behavior.
Settings stay fixed when resuming a profile.

To change the network route, add your HTTP CONNECT proxies to `proxies.txt`, one
URL per line. See `proxies.example.txt`. The Chrome backend loads this file
automatically and selects proxies in sequence, reusing the list when necessary.
An absent/empty file uses the system route. Browser and Mail.tm use the same proxy.
Changing timezone or locale alone does not change the public IP address.
Resume requires the original proxy URL for that profile. Proxy credentials
are not printed or stored in browser-settings.json.

## Commands

```powershell
py -3 -m providers.eirouter --mail-service mail.tm --count 3
py -3 -m providers.eirouter --mail-service tmail --count 1
py -3 -m providers.eirouter --resume EMAIL
py -3 -m providers.eirouter --check-browser --captcha-timeout 60
py -3 -m providers.eirouter --count 1 --no-geo --locale en-GB --timezone Europe/London
```

`--check-browser` checks page loading and Cloudflare without creating mailboxes
or service accounts. `--output PATH` selects the credential file; `--profiles-dir`
selects another profile location. Cloudflare wait defaults to 600 seconds.
`--resume` reuses cookies, signs in when necessary and copies the existing key.

The batch launcher uses interactive mode: first enter the account count, then
choose `tmail.wibucrypto.pro` (browser-based) or `Mail.tm` (API-based).

## Setup

Python 3.11+ and AdsPower (or system Google Chrome for `--browser chrome`) are required. The batch file prefers
`.venv\Scripts\python.exe`, then the Windows Python launcher and installed Python.

```powershell
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Development checks:

```powershell
py -3 -m pytest core -q
```

Credentials, profiles and proxies are excluded by `.gitignore`.
Keep backups of the account file and profiles. The copied pending records from
zcode-farm have no matching persistent browser profile from earlier runs.

Cloudflare troubleshooting:
https://developers.cloudflare.com/cloudflare-challenges/troubleshooting/challenge-solve-issues/
Mail.tm API: https://docs.mail.tm/getting-started/authentication
AdsPower API: https://localapi-doc-en.adspower.com/docs/FFMFMf
