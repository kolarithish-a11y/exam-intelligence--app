# Exam Intelligence — Live Application Build

This package contains a production-oriented mobile PWA + FastAPI source-ingestion service.

## What is live-capable

- The Next.js frontend calls `/api/current-affairs`.
- The API fetches configured source feeds server-side.
- Source items are normalized, categorized and deduplicated.
- The UI never labels fallback/sample content as today's live news.
- Docker and Render configuration are included.
- PWA manifest/service worker are included.

## What still requires deployment

A 24/7 live app needs a hosted runtime. This environment cannot create or authorize your personal GitHub/Render/Vercel account. Therefore this package is deployment-ready but no public URL is claimed.

## Production hardening still required before opening to users

- persistent PostgreSQL repository instead of the starter in-memory cache
- scheduled worker/cron for continuous ingestion
- primary-source verification and contradiction handling
- secure authentication and secret management
- AI provider key, if AI generation is enabled
- monitoring/alerts
- backups and rate limits

The larger Exam Intelligence project already contains these components; this package gives the product a clean live-connected surface and a minimal independently runnable source-ingestion service.
