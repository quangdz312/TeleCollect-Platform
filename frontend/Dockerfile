# TeleCollect frontend — production multi-stage build.
# node:22-slim (Debian) rather than an alpine base: `sharp` (frontend/package.json)
# ships prebuilt binaries per libc target and alpine's musl libc is a common
# source of native-module breakage for it.

# ---- Stage 1: deps + build ----
FROM node:22-slim AS builder
WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .

# NEXT_PUBLIC_* are inlined into the client bundle by `next build` (see
# frontend/lib/api.ts, frontend/lib/real-teleop.ts) — they must be present
# as build args, not just container env, or the bundle falls back to
# http://localhost:8000.
ARG NEXT_PUBLIC_API_URL
ARG NEXT_PUBLIC_API_ORIGIN
ARG NEXT_PUBLIC_WS_BASE
ARG NEXT_PUBLIC_TRAINING_ENABLED
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL \
    NEXT_PUBLIC_API_ORIGIN=$NEXT_PUBLIC_API_ORIGIN \
    NEXT_PUBLIC_WS_BASE=$NEXT_PUBLIC_WS_BASE \
    NEXT_PUBLIC_TRAINING_ENABLED=$NEXT_PUBLIC_TRAINING_ENABLED

RUN npm run typecheck
RUN npm run build

# ---- Stage 2: runtime ----
# next.config.mjs sets output: "standalone", so .next/standalone contains a
# self-contained server plus only the node_modules it actually needs — no
# npm ci / full node_modules copy required here.
FROM node:22-slim AS runner
WORKDIR /app

ENV NODE_ENV=production \
    PORT=3000 \
    HOSTNAME=0.0.0.0

RUN useradd -m appuser
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
COPY --from=builder /app/public ./public
RUN chown -R appuser:appuser /app
USER appuser

EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD node -e "fetch('http://localhost:3000/').then(r=>{if(!r.ok)process.exit(1)}).catch(()=>process.exit(1))"

CMD ["node", "server.js"]
