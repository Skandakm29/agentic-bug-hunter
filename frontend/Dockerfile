# Multi-stage build for frontend application
FROM node:18-alpine AS builder

WORKDIR /workspace/frontend

COPY frontend/package*.json ./
RUN npm ci

COPY frontend/ ./
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build

# Final production runner stage
FROM node:18-alpine AS runner

WORKDIR /workspace/frontend

ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1

COPY --from=builder /workspace/frontend/package*.json ./
COPY --from=builder /workspace/frontend/node_modules ./node_modules
COPY --from=builder /workspace/frontend/.next ./.next
COPY --from=builder /workspace/frontend/public ./public
COPY --from=builder /workspace/frontend/src ./src

EXPOSE 3000

CMD ["npm", "run", "start"]
