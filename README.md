# UPIScan

Supported extraction channels: UPI, iDEAL, MoMo, Kakao, and 直卡.

## Account Eligibility Check

The local extraction page can batch-check the Access Tokens entered in the
`Access Token` field before submitting extraction jobs. The frontend calls the
local backend endpoint:

```text
POST /api/account-eligibility-check
```

The backend performs the check locally. It decodes the JWT payload to show
account metadata, then runs a server-side ChatGPT checkout preflight with the
configured promotion. It does not call a third-party eligibility service.

Default local payload:

```json
{"token":"<access_token>","promoId":"plus-1-month-free"}
```

Optional server-side overrides:

```text
ACCOUNT_CHECK_TIMEOUT=30
ACCOUNT_CHECK_MAX_ATTEMPTS=2
ACCOUNT_CHECK_COUNTRY=KR
ACCOUNT_CHECK_CURRENCY=KRW
ACCOUNT_CHECK_PROXY=
ACCOUNT_CHECK_PRE_PROXY=
ACCOUNT_CHECK_DUMP=false
```

## Ready Plus Third-party Tasks

The page also includes a separate `第三方开通` panel. This feature submits
complete ChatGPT Session JSON objects to Ready Plus and polls the async task
until it reaches `completed` or `failed`.

Supported Ready Plus channels in this project:

```text
upi   enabled · 1.2 USDT/item
kakao enabled · 1 USDT/item
```

The API key can be entered and saved in the frontend `第三方开通` panel. It is
stored in the current browser's local storage and sent to the local FastAPI
backend with `X-Ready-Plus-Key` for each Ready Plus request. The backend forwards
it server-to-server and does not persist it.

Server-side environment variables are optional fallback values:

```text
READY_PLUS_API_BASE=https://api.cli-proxy.cn
READY_PLUS_API_KEY=tg_your_api_key
READY_PLUS_TIMEOUT=30
READY_PLUS_DOWNLOAD_TIMEOUT=120
```

The frontend never calls Ready Plus directly. It calls these local FastAPI
endpoints instead:

```text
POST /api/ready-plus/tasks
GET  /api/ready-plus/tasks?limit=20
GET  /api/ready-plus/tasks/{task_id}
GET  /api/ready-plus/items/{item_id}/download-token
GET  /api/ready-plus/items/{item_id}/download?token=...
```

Input must be the complete JSON returned by:

```text
https://chatgpt.com/api/auth/session
```

One third-party submit request supports 1-20 Session JSON items.

Before submitting either UPI or Kakao, set and verify a working password in the
target ChatGPT account settings. Do not submit account passwords or 2FA OTPs to
the API. The correct Ready Plus order is:

```text
set ChatGPT password -> submit complete Session JSON -> Ready Plus handles 2FA/package flow
```

Kakao accepts valid email accounts and recommends Gmail or iCloud. A missing
email or conflicting email fields in the Session JSON may be rejected with:

```text
api_kakao_email_required
```

The frontend generates an `Idempotency-Key` for each Ready Plus submission and
keeps it visible. If a network timeout leaves the result unknown, retry with the
same request body and same key. Use `New submit key` only for a genuinely new
task. When Ready Plus returns `Retry-After`, the UI includes the suggested retry
delay in the error message.

## Email Registration

The `邮箱注册` tab accepts mailbox rows copied from the reference registration
system:

```text
email@example.com----https://mail.example.com/show/token/email
email@example.com----code:https://mail.example.com/api/code/token/email----mail:https://mail.example.com/api/mail/token/email
```

The backend includes the mailbox parser, code API/mail API polling logic, batch
task status, and automatic account-library import after a registration returns
an `access_token`.

The same tab also supports a registration IP pool. Put one proxy per line in
`注册 IP 池`; common proxy shorthands are normalized the same way as extraction
proxies:

```text
http://user:pass@host:port
HOST:PORT:USER:PASS
USER:PASS@HOST:PORT
HOST:PORT
```

Each mailbox registration is assigned a proxy by round-robin order. If one
attempt fails, `失败换 IP 次数` controls how many times the backend retries the
same mailbox with the next proxy in the pool. Logs and saved account notes only
show a redacted proxy label such as `proxy#1234abcd`.

UPIScan includes a migrated browser registration runtime. Docker installs a
Playwright Chromium browser for this flow. The old reference project path is now
only an optional fallback:

```text
UPISCAN_EMAIL_REGISTER_REFERENCE_ROOT=E:\gpt\cx\fucccccckgpt
```

If the built-in executor fails and the fallback path is configured, UPIScan will
try the reference `EmailRegistrationOrchestrator`. Without a fallback, the task
returns the built-in executor error while the rest of UPIScan continues to run.

### Go Email Protocol Worker

The reference Go email-protocol worker has been migrated into
`go-email-protocol/`. It is used for:

```text
POST   /api/go-email-batches
GET    /api/go-email-batches/{batch_id}
DELETE /api/go-email-batches/{batch_id}
```

The email registration page includes a `Go batch registration` panel. Leave
`Go worker URL` empty in Docker deployments; the backend will use:

```text
GO_EMAIL_PROTOCOL_URL=http://go-email-worker:18765
```

For local manual testing, run the worker separately and enter its URL in the
page, for example `http://127.0.0.1:18765`.

The account library `Plus 校验` action can also use the Go worker's
`/v2/plus-verify` endpoint when a worker URL is configured. If no worker URL is
available, the backend keeps using the existing Python subscription check.

Docker Compose starts two services:

```text
upiscan          # FastAPI + frontend, published on 8000
go-email-worker  # internal worker, exposed only inside compose on 18765
```

The Docker defaults point both account storage and resource pool storage to a
single SQLite file so the Go worker can lease mailbox/proxy resources and write
registered accounts back into the same database:

```text
UPISCAN_ACCOUNT_DB=/app/data/upiscan.sqlite3
UPISCAN_RESOURCE_DB=/app/data/upiscan.sqlite3
```

## Phone Registration

The `手机注册` tab migrates the user-provided phone SMS URL flow from the
reference registration system. Each row is one phone number and one SMS polling
URL:

```text
+15551234567|https://sms.example.com/latest?phone=15551234567
+15557654321----https://sms.example.com/latest?phone=15557654321
```

The backend opens the ChatGPT phone registration flow in the migrated browser
runtime, waits for the SMS code from the URL, submits the code, extracts the
`access_token`, and imports the account into the account library.

Phone registration supports the same `注册 IP 池` rotation behavior as email
registration. The `国家拨号码` and `国家名称` fields are used by the phone form
country selector and by phone-number normalization.

The phone registration page now also supports direct SMS provider rental:

```text
HeroSMS
SMSBower
SMS-Activate
```

For provider mode, enter the provider API key in the frontend, choose the
service/country code, and set the number of accounts to register. The API key is
sent only to the local backend for that job. The backend rents a phone number,
submits it to the ChatGPT phone registration flow, polls the SMS code, and
reports success or cancels the activation when the attempt fails.

The `资源池` tab provides durable phone-number pool management. Import rows into
`注册手机号池`, then select `注册手机号池` as the phone registration SMS source.
Each task leases available numbers, marks successful numbers as `used`, releases
unused leases, and cools down or disables failed numbers according to the bind
failure category.

The same `资源池` tab also supports `代理 Seed 池`. Import reusable proxy
credentials in this format:

```text
account:password@host:port
socks5://account:password@host:port
```

Supported seed styles include auto-detect, Kookeey/proxy001, Lajiao,
Bestgo/RRP, and plain username mode. Email registration, phone registration,
and OAuth resume can enable `使用资源池代理 Seed`; the backend will generate
sticky session proxy URLs for the requested country/region and keep the old
manual `注册 IP 池` textarea as an optional fallback.

## OAuth Resume Bind

The `OAuth续跑` tab restores a registered account from its saved browser storage
state, runs the migrated OAuth bind flow, and writes the resulting OAuth tokens
back into the account library.

Supported inputs:

```text
Account library IDs     # reads session_json saved by email/phone registration
Resume JSON             # paste one JSON object, a JSON array, or one JSON per line
```

Each resume record must contain:

```json
{
  "email": "user@example.com",
  "password": "ChatGPTPassword",
  "browser_storage_state_path": "data/registered_accounts/storage_xxx.json"
}
```

`phone_number` can be used instead of `email`. `account_id`, `plan_type`, and
`registration_proxy` are optional but will be preserved when present.

If the OAuth flow asks for an email OTP or lands on an add-email branch, fill the
same mailbox format used by email registration:

```text
email@example.com----https://mail.example.com/show/token/email
email@example.com----code:https://mail.example.com/api/code/token/email----mail:https://mail.example.com/api/mail/token/email
```

The OAuth IP pool uses the same proxy formats and retry rotation behavior as the
registration pages. Successful results are stored inside `session_json` under
`oauth_resume` and `oauth_tokens`; the account library `access_token` is also
updated with the new OAuth access token.

OAuth resume can also provide a phone-binding callback for accounts that land on
OpenAI's `add_phone` step. In the `OAuth续跑` tab, keep `绑定短信来源` empty to
disable phone binding, or select one of:

```text
自备手机号 URL
绑定手机号池
HeroSMS
SMSBower
SMS-Activate
```

The callback rents or reads a phone only when the OAuth flow asks for
`add_phone`. If the bind attempt fails, the backend calls the provider's failure
hook when available, otherwise it cancels/releases the rented number. Optional
server-side fallback environment variables:

```text
BIND_SMS_PROVIDER=
BIND_SMS_API_KEY=
BIND_SMS_SERVICE=dr
BIND_SMS_COUNTRY=
BIND_SMS_PROXY=
UPISCAN_RESOURCE_DB=data/resource_pool.sqlite3
```

When `绑定手机号池` is selected, OAuth resume uses the durable `资源池 /
绑定手机号池` records. The resource pool is stored in `data/resource_pool.sqlite3`
by default, or in `UPISCAN_RESOURCE_DB` when configured.

## Docker

Build and run the full FastAPI + React app:

```powershell
docker compose up --build -d
```

Open:

```text
http://127.0.0.1:8000
```

Put proxy seeds in:

```text
data/proxy_seeds.txt
```

`proxy_seeds.txt` supports multiple proxies. Put one proxy per line:

```text
# blank lines and lines beginning with # are ignored
http://user:pass-country=IN-session=abc001@proxy.example.com:8000
http://user:pass-country=IN-session=abc002@proxy.example.com:8000
socks5h://user:pass-country=IN-session=abc003@proxy.example.com:1080
```

Common shorthand formats are accepted and normalized automatically:

```text
HOST:PORT:USER:PASS
HOST:PORT@USER:PASS
USER:PASS:HOST:PORT
USER:PASS@HOST:PORT
HOST:PORT
```

When the extractor needs to derive checkout/promotion/provider countries from
one seed, the proxy username or password must still include a rewriteable
country/region selector, for example `country=IN`, `country-IN`, `region=IN`,
or the equivalent syntax required by your proxy provider.

Comma-separated proxies are also accepted:

```text
http://user:pass-country=IN-session=abc004@proxy.example.com:8000,http://user:pass-country=IN-session=abc005@proxy.example.com:8000
```

The backend loads all seeds, shuffles them, then ranks them by success/failure
history. Failed seeds may be removed from `data/proxy_seeds.txt` when
`UPI_PROXY_REMOVE_FAILED` is enabled.

In the web UI, `Proxy source` has two modes:

```text
Use server proxy_seeds.txt       # use the Docker-mounted data/proxy_seeds.txt
Use custom proxies for this job  # paste proxies in the frontend; they apply only to that job
```

Custom frontend proxies support the same formats as `proxy_seeds.txt`.

The link extraction page can optionally hand off a successful UPI pay link to
the Publisher API. Configure these fields in the `Publisher handoff` section:

```text
Publisher API key
Publisher API base
Publisher task ID
Auto submit after extraction
```

Use the Foarge Publisher API base:

```text
https://foarge.com/api/publisher/v1
```

In Docker/browser deployments, the frontend should use the local proxy base for
task management:

```text
/api/publisher-proxy/v1
```

FastAPI forwards those requests to Foarge server-to-server using
`FOARGE_PUBLISHER_API_BASE`, which defaults to
`https://foarge.com/api/publisher/v1`.

The API key is stored only in the browser session. During handoff it is sent to
your local FastAPI backend for this one request; the backend forwards it to
Foarge and does not persist it. The frontend calls:

```text
POST /api/publisher/submit-checkout
```

The FastAPI backend then performs the server-to-server request to Foarge:

```text
POST https://foarge.com/api/publisher/v1/tasks/{task_id}/submit-checkout
```

This avoids browser CORS restrictions on `Authorization` requests to Foarge.

Useful environment variables are in `docker-compose.yml`, including `UPI_MAX_RETRY`,
`UPI_BOOTSTRAP_COUNTRY`, `UPI_PROMOTION_COUNTRY`, and `UPI_PROVIDER_COUNTRY`.

## Frontend Development

This template provides a minimal setup to get React working in Vite with HMR and some Oxlint rules.

Currently, two official plugins are available:

- [@vitejs/plugin-react](https://github.com/vitejs/vite-plugin-react/blob/main/packages/plugin-react) uses [Oxc](https://oxc.rs)
- [@vitejs/plugin-react-swc](https://github.com/vitejs/vite-plugin-react/blob/main/packages/plugin-react-swc) uses [SWC](https://swc.rs/)

## React Compiler

The React Compiler is not enabled on this template because of its impact on dev & build performances. To add it, see [this documentation](https://react.dev/learn/react-compiler/installation).

## Expanding the Oxlint configuration

If you are developing a production application, we recommend enabling type-aware lint rules by installing `oxlint-tsgolint` and editing `.oxlintrc.json`:

```json
{
  "$schema": "./node_modules/oxlint/configuration_schema.json",
  "plugins": ["react", "typescript", "oxc"],
  "options": {
    "typeAware": true
  },
  "rules": {
    "react/rules-of-hooks": "error",
    "react/only-export-components": ["warn", { "allowConstantExport": true }]
  }
}
```

See the [Oxlint rules documentation](https://oxc.rs/docs/guide/usage/linter/rules) for the full list of rules and categories.

# ananasUpiscan
