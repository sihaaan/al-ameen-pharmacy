# Al Ameen Pharmacy Frontend

React 19 single-page application for the public pharmacy storefront and staff
administration workflows. The root [README](../README.md) describes the full
stack; this file documents frontend-specific commands.

## Requirements

- Node.js 20 (pinned in `.nvmrc` and used by CI)
- npm with the committed `package-lock.json`

## Local Setup

```bash
npm ci
cp .env.example .env
npm start
```

The development server runs at `http://localhost:3000`. Set
`REACT_APP_API_URL` in `.env` to the Django API root, normally
`http://localhost:8000/api`.

## Commands

| Command | Purpose |
| --- | --- |
| `npm start` | Run the local development server. |
| `npm test` | Run Jest in interactive watch mode. |
| `npm run test:ci` | Run the complete frontend test suite once, serially. |
| `npm run build` | Create the production bundle in `build/`. |
| `npm run serve` | Serve the production bundle locally on port 3000. |

Before a pull request, run the same checks as CI:

```bash
npm ci
npm run test:ci
npm run build
```

See [DEPLOYMENT.md](../DEPLOYMENT.md) for Railway configuration.
