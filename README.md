# UPIScan

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
