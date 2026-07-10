# AI-Hub Cockpit — Frontend Deployment Guide

## Overview

AI-Hub Cockpit is a production-ready Next.js frontend for the AI-Hub backend. This guide covers development, staging, and production deployments.

## System Requirements

- Node.js 18+ (recommended: 20 LTS)
- npm 9+
- Linux/macOS/WSL2 (Windows support via WSL2)
- 512MB RAM minimum (production: 1GB recommended)
- 500MB disk space

## Development Setup

```bash
cd cockpit

# Install dependencies
npm ci

# Start dev server (port 3000)
./scripts/start-dev.sh

# Or with custom port
PORT=4000 npm run dev
```

Dev server hot-reloads on changes. Proxy to backend configured in `.env`.

## Production Deployment

### 1. Build

```bash
cd cockpit
npm ci
npm run build
```

Output: `.next/` directory (~200MB)

### 2. Environment Configuration

Create `.env.local` (not committed) with:

```bash
# Backend connection
AIHUB_BASE_URL=http://backend-host:8080
AIHUB_API_KEY=your-secret-key
AIHUB_TIMEOUT_MS=45000

# Runtime
NODE_ENV=production
NEXT_PUBLIC_ENVIRONMENT=production
```

Or pass as environment variables:

```bash
export AIHUB_BASE_URL="http://backend:8080"
export AIHUB_API_KEY="secret"
npm run start
```

### 3. Start Production Server

```bash
# Manual start
./scripts/start-prod.sh

# Or direct (port 3000 by default)
PORT=3000 npm run start
```

### 4. Reverse Proxy Setup (nginx example)

```nginx
server {
    listen 80;
    server_name ai-hub.example.com;

    location / {
        proxy_pass http://localhost:3000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host $host;
        proxy_cache_bypass $http_upgrade;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

### 5. Health Check

```bash
# From your monitoring system
./scripts/health-check.sh

# Expected output:
# ✓ Frontend availability... ✓ OK
# ✓ Frontend API proxy... ✓ OK
# ✓ Backend connectivity... ✓ OK
```

Or curl directly:

```bash
curl http://localhost:3000/
curl http://localhost:3000/api/aihub/system/ping
```

## Docker Deployment

Build image:

```dockerfile
FROM node:20-alpine
WORKDIR /app
COPY cockpit/ .
RUN npm ci && npm run build
EXPOSE 3000
CMD ["npm", "run", "start"]
```

Build & run:

```bash
docker build -t ai-hub-cockpit .
docker run -p 3000:3000 \
  -e AIHUB_BASE_URL=http://backend:8080 \
  -e AIHUB_API_KEY=secret \
  ai-hub-cockpit
```

## systemd Service (Linux)

Create `/etc/systemd/system/ai-hub-cockpit.service`:

```ini
[Unit]
Description=AI-Hub Cockpit Frontend
After=network.target

[Service]
Type=simple
User=aihub
WorkingDirectory=/opt/ai-hub/cockpit
Environment="NODE_ENV=production"
Environment="AIHUB_BASE_URL=http://127.0.0.1:8080"
Environment="AIHUB_API_KEY=your-key"
Environment="PORT=3000"
ExecStart=/usr/local/bin/node /opt/ai-hub/cockpit/node_modules/.bin/next start
Restart=on-failure
RestartSec=10s

[Install]
WantedBy=multi-user.target
```

Enable & start:

```bash
sudo systemctl enable ai-hub-cockpit
sudo systemctl start ai-hub-cockpit
```

## Configuration Reference

### Environment Variables

| Variable                   | Type   | Default                 | Notes                      |
| -------------------------- | ------ | ----------------------- | -------------------------- |
| `AIHUB_BASE_URL`           | string | `http://127.0.0.1:8080` | Backend URL                |
| `AIHUB_API_KEY`            | string | ``                      | API key (server-side only) |
| `AIHUB_TIMEOUT_MS`         | number | `45000`                 | Request timeout            |
| `NODE_ENV`                 | string | `production`            | Runtime environment        |
| `NEXT_PUBLIC_ENVIRONMENT`  | string | `production`            | Exposed to client          |
| `NEXT_PUBLIC_MAX_MESSAGES` | number | `100`                   | UI message limit           |
| `NEXT_PUBLIC_THEME`        | string | `dark`                  | UI theme                   |
| `PORT`                     | number | `3000`                  | Server port                |

**Note:** Variables prefixed with `NEXT_PUBLIC_` are exposed to the browser and should not contain secrets.

## Monitoring

### Logs

Dev mode:

```bash
npm run dev 2>&1 | tee frontend.log
```

Production:

```bash
npm run start 2>&1 | tee -a /var/log/ai-hub-cockpit.log
```

Monitor for:

- `Cannot connect to backend` — Backend is down
- `ERR_HTTP_REQUEST_TIMEOUT` — Backend timeout (increase `AIHUB_TIMEOUT_MS`)
- `EADDRINUSE` — Port already in use

### Metrics

Monitor via HTTP:

```bash
# Frontend alive?
curl -s http://localhost:3000/ | wc -l

# Backend reachable from frontend?
curl -s http://localhost:3000/api/aihub/system/ping | jq '.ok'
```

## Performance Tuning

### Production Build Size

```bash
npm run build

# Check size
du -sh .next
# Expected: ~200MB standalone, ~100MB with sharing
```

### Cache Strategy

Next.js handles caching via `next.config.mjs`:

- Production assets: `max-age=31536000` (1 year, immutable)
- Dynamic content: `no-cache, must-revalidate`
- API proxy: Always fresh (`cache: 'no-store'`)

## Troubleshooting

### Frontend won't start

```bash
# Check Node version
node --version  # Should be 18+

# Check port is free
lsof -i :3000

# Rebuild
rm -rf .next node_modules
npm ci
npm run build
```

### Backend unreachable

```bash
# Test backend directly
curl -v http://127.0.0.1:8080/system/ping

# Test via proxy
curl -v http://localhost:3000/api/aihub/system/ping

# Check AIHUB_BASE_URL
env | grep AIHUB_BASE_URL
```

### High memory usage

```bash
# Check if garbage collection is working
NODE_OPTIONS="--max-old-space-size=512" npm run start

# Monitor during startup
node --inspect=0.0.0.0:9229 node_modules/.bin/next start
```

### TypeScript errors in production

Build includes full type-checking:

```bash
npm run typecheck  # Explicit check
npm run build      # Also runs typecheck
```

If build fails, code won't be deployed.

## Version Pinning

All major dependencies are pinned in `package.json`:

- Next.js: 15.5.x
- React: 19.0.x
- React Query: 5.56.x
- Zustand: 5.0.x
- Tailwind: 3.4.x

To update safely:

```bash
npm update pkg-name
npm test
git commit -m "chore: update pkg-name"
```

## Security Checklist

- [ ] API key stored in `.env.local`, not `.env`
- [ ] Backend URL doesn't expose sensitive data
- [ ] HTTPS enabled in production (reverse proxy)
- [ ] `X-Frame-Options: DENY` headers set (auto in `next.config.mjs`)
- [ ] No debug mode in production (`NEXT_PUBLIC_ENVIRONMENT=production`)
- [ ] Logs don't contain secrets (use redaction)
- [ ] Third-party scripts audited (none by default)

## Support

For issues:

1. Check logs: `scripts/health-check.sh`
2. Test backend: `curl http://backend:8080/system/ping`
3. Check configuration: `env | grep AIHUB_`
4. Report: Include `npm run build` output + error message
