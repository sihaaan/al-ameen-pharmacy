# Frontend Dependency Security Decision

| Field | Value |
|---|---|
| Status | Temporary, reviewed release exceptions; not a permanent acceptance |
| Owner | Al Ameen platform maintainers |
| Decision date | 2026-08-01 |
| Mandatory review/expiry | 2026-09-01 |
| Scope | `frontend/package.json` and `frontend/package-lock.json` |

This document records the release decision for dependencies that cannot be
removed without a major React Router or Create React App toolchain migration.
It does not authorize that modernization work. A new Critical advisory, a new
unlisted High advisory, or an expired exception blocks release until reviewed.

## Audit outcome

The production dependency audit is intentionally run with
`npm audit --omit=dev` because `react-scripts` is a direct dependency used in
the Railway build step. It therefore remains in the production dependency
graph even though its webpack, optimizer, test, and development-server
children are not shipped as executable Node modules in the static browser
bundle.

| Audit | Critical | High | Moderate | Low | Total |
|---|---:|---:|---:|---:|---:|
| Before remediation | 1 | 16 | 7 | 9 | 33 |
| After reviewed overrides | 0 | 11 | 7 | 9 | 27 |

The application is built with `npm run build` and served with
`serve -s build -l 3000`. Production does not run `react-scripts start` or
`webpack-dev-server`. Customer email, document, and image uploads are handled
by the running backend and are not frontend build inputs.

## Applied non-breaking remediation

The following transitive versions are pinned through npm `overrides`. They are
patch/minor-compatible with the consuming package APIs and passed a clean
install, the complete frontend suite, and a production build.

| Package | Before | Pinned | Advisory removed | Dependency paths |
|---|---:|---:|---|---|
| `websocket-driver` | 0.7.4 | 0.7.5 | Critical GHSA-xv26-6w52-cph6; Moderate GHSA-mp7j-qc5w-4988 | `react-scripts -> webpack-dev-server -> sockjs -> websocket-driver`; `sockjs -> faye-websocket -> websocket-driver` |
| `fast-uri` | 3.1.3 | 3.1.5 | High GHSA-v2hh-gcrm-f6hx | `react-scripts -> schema-utils/workbox-build -> ajv -> fast-uri`; `serve -> ajv -> fast-uri` |
| `brace-expansion` 1.x | 1.1.16 | 1.1.18 | High GHSA-mh99-v99m-4gvg | `react-scripts -> eslint -> minimatch -> brace-expansion` |
| `brace-expansion` 2.x | 2.1.2 | 2.1.4 | High GHSA-mh99-v99m-4gvg | `react-scripts -> tailwindcss -> sucrase -> glob -> minimatch -> brace-expansion`; `react-scripts -> workbox-webpack-plugin -> workbox-build -> @surma/rollup-plugin-off-main-thread -> ejs -> jake -> filelist -> minimatch -> brace-expansion` |
| `underscore` | 1.13.6 | 1.13.8 | High GHSA-qpx9-hpmf-5gmw and aggregate `jsonpath`/`bfj` findings | `react-scripts -> bfj -> jsonpath -> underscore` |

Verification:

```text
cd frontend
npm ci --ignore-scripts
npm ls websocket-driver fast-uri brace-expansion underscore --all
npm ls --omit=dev --all
npm audit --omit=dev
```

Safe result: the tree is valid; the pinned versions above are installed; the
audit reports zero Critical findings and no High finding outside the exception
mapping below. While these exceptions remain open, npm still exits nonzero; the
operator must save and compare its JSON report rather than treating the exit
code alone as approval. Do not use `npm audit fix --force`.

## Temporary exceptions

### FE-EX-001 — React Router v6 audit findings

- Audit entries/advisories: direct `react-router-dom` and transitive
  `react-router`; GHSA-jjmj-jmhj-qwj2, GHSA-wrjc-x8rr-h8h6, and
  GHSA-337j-9hxr-rhxg (Moderate).
- Exact path: application -> `react-router-dom@6.30.4` ->
  `react-router@6.30.4`.
- Reachability: React Router is present in the browser bundle. The application
  uses declarative `BrowserRouter`, not framework/data-router SSR hydration.
  The one untrusted post-login `next` consumer is now guarded by the shared
  same-origin internal-path validator. It rejects backslashes, external and
  protocol-relative authorities, controls, malformed values, non-HTTP schemes,
  dot-normalized authority paths, and nested encoded equivalents before
  `navigate()` is called.
- Compensating controls: focused safe/adversarial validator tests; integration
  tests for Login; the axios and Admin Dashboard `next` producers use the same
  validator; no untrusted loader/action redirect or SSR hydration path exists.
- Replacement task: separately approve and test a React Router v7 migration,
  including its Node 20+ requirement and all quotation/Gmail deep links.
- Forced-upgrade risk: 7.18.2 is semver-major; 6.30.4 is the final v6 release.
  A release-blocker patch must not silently change navigation APIs or timing
  across the whole SPA.
- Owner/review: Al Ameen platform maintainers; review or close by 2026-09-01.

The reachable application redirect exposure is fixed; this exception records
the remaining package-version finding and the separately scoped major upgrade.

### FE-EX-002 — legacy SVGR/SVGO chain

- High audit entries: `@svgr/webpack`, `@svgr/plugin-svgo`, `svgo`,
  `css-select`, and `nth-check`.
- Advisories: GHSA-2p49-hgcm-8545 (`svgo` script removal) and
  GHSA-rp65-9cf3-cjxr (`nth-check` regular-expression complexity).
- Exact path: application -> `react-scripts@5.0.1` ->
  `@svgr/webpack@5.5.0` -> `@svgr/plugin-svgo@5.5.0` -> `svgo@1.3.2` ->
  `css-select` -> `nth-check`.
- Reachability: build-only webpack loader. The only tracked SVG is the
  unreferenced Create React App scaffold file `frontend/src/logo.svg`; no
  application JavaScript or JSX imports an SVG. Create React App's shipped
  webpack configuration also sets the SVGR loader's `svgo` option to `false`.
  This vulnerable optimizer path is therefore installed but not exercised by
  the application build. Customer uploads are not build inputs.
- Compensating controls: lockfile-only clean installs; repository review for
  any future SVG source; no customer-controlled SVG processing in the frontend
  build; static production runtime.
- Replacement task: replace Create React App or upgrade the SVGR/SVGO build
  chain under a separately approved frontend-tooling change.
- Forced-upgrade risk: the fixed SVGO line is a major API/configuration change
  outside the versions accepted by the pinned SVGR loader.
- Owner/review: Al Ameen platform maintainers; review or close by 2026-09-01.

### FE-EX-003 — `resolve-url-loader` PostCSS 7 chain

- High audit entry: `postcss`; aggregate `resolve-url-loader` and
  `react-scripts` entries inherit it.
- Advisories: GHSA-6g55-p6wh-862q and GHSA-r28c-9q8g-f849. The same audit node
  also reports GHSA-7fh5-64p2-3v2j and GHSA-qx2v-qp2m-jg93 as Moderate.
- Exact path: application -> `react-scripts@5.0.1` ->
  `resolve-url-loader@4.0.0` -> `postcss@7.0.39`.
- Reachability: build-only source-map/CSS processing. No `.scss` or `.sass`
  source exists under `frontend/src` at review time, and customer content is
  never passed into the frontend compiler.
- Compensating controls: trusted repository build inputs, clean locked builds,
  no production development server, and review of any future Sass addition.
- Replacement task: move to a maintained frontend build tool or a compatible
  loader chain using patched PostCSS 8.
- Forced-upgrade risk: `resolve-url-loader@4` requires PostCSS 7; forcing
  PostCSS 8 across that unsupported boundary can silently change source-map
  and CSS output.
- Owner/review: Al Ameen platform maintainers; review or close by 2026-09-01.

### FE-EX-004 — legacy minifier/workbox serialization chain

- High audit entries: `serialize-javascript`, `rollup-plugin-terser`,
  `workbox-build`, and `workbox-webpack-plugin`; aggregate
  `react-scripts` inherits them.
- Advisories: GHSA-5c6j-r48x-rmvq (High) and GHSA-qj8w-gfj5-8c6v
  (Moderate).
- Exact paths:
  - application -> `react-scripts@5.0.1` ->
    `css-minimizer-webpack-plugin@3.4.1` -> `serialize-javascript@6.0.2`;
  - application -> `react-scripts@5.0.1` ->
    `workbox-webpack-plugin@6.6.0` -> `workbox-build@6.6.0` ->
    `rollup-plugin-terser@7.0.2` -> `serialize-javascript@4.0.0`.
- Reachability: build-time serialization/minification of repository-controlled
  configuration and compiled assets. These Node modules are not used by the
  static `serve` runtime and receive no customer inquiry or attachment data.
  CSS minimization is active; the workbox InjectManifest branch is inactive
  because the repository has no `frontend/src/service-worker` entry.
- Compensating controls: locked clean build, repository-controlled webpack
  inputs, no runtime compilation, and production-build regression testing.
- Replacement task: replace the unmaintained Create React App/workbox/terser
  chain with a maintained build tool under a separately approved migration.
- Forced-upgrade risk: fixed `serialize-javascript` is a major upgrade outside
  both parent ranges; `rollup-plugin-terser` itself is deprecated. An override
  would be an unverified production-bundle change.
- Owner/review: Al Ameen platform maintainers; review or close by 2026-09-01.

### FE-EX-005 — direct `react-scripts` aggregate

- High audit entry: direct `react-scripts@5.0.1`.
- Exact path: application -> `react-scripts@5.0.1`; npm derives its severity
  from FE-EX-002 through FE-EX-004. It introduces no additional advisory.
- Reachability: direct build/test dependency. Railway executes it while
  producing the static bundle, but production starts `serve`, not
  `react-scripts` or `webpack-dev-server`.
- Compensating controls and replacement: the controls and modernization task
  in FE-EX-002 through FE-EX-004 apply. No supported non-breaking
  `react-scripts` release removes these paths.
- Forced-upgrade risk: npm's forced remediation is not a compatible toolchain
  upgrade and must not be used for this release.
- Owner/review: Al Ameen platform maintainers; review or close by 2026-09-01.

## Release and rollback rules

- Every release reruns `npm audit --omit=dev` from a clean `npm ci` install.
- Zero Critical is mandatory. Every High must map to FE-EX-002 through
  FE-EX-005 until those exceptions expire; any new path blocks release.
- FE-EX-001 remains mandatory to review even though npm currently rates the
  router advisories Moderate because the package is direct and browser-runtime.
- A rollback of the override commit requires a matching package-lock rollback
  and `npm ci`. That rollback reintroduces the Critical `websocket-driver`
  version and is not an acceptable release state. Prefer a forward correction
  that retains the safe override versions.
- This document does not start or authorize the optional major router/build
  modernization work.
