# UPIScan

Supported extraction channels: UPI, iDEAL, MoMo, Kakao, and 直卡.

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
