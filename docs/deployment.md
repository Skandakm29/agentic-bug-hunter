# Production Deployment & Containerization Guide

This document describes how to deploy the **Agentic Bug Hunter** platform in production environments using containerization.

---

## 1. Containerization Architecture

Both the frontend client and the backend server are packaged as standalone Docker container configurations:

* **Backend (`backend/Dockerfile`):** Multi-stage build using a minimal `python:3.10-slim` base image. Unused compile headers are cleaned in the builder stage to keep the final image lightweight and safe. It runs on a non-root user account to limit execution privileges.
* **Frontend (`frontend/Dockerfile`):** Multi-stage build using a minimal `node:18-alpine` base image. Resolves dependencies, compiles static assets (`npm run build`), and serves pages.

---

## 2. Running via Docker Compose

Docker Compose coordinates the runtime environment:

1. Export your Groq API key in your current terminal:
   ```bash
   export GROQ_API_KEY="your_actual_groq_key"
   ```
2. Build and launch the container cluster:
   ```bash
   docker compose up --build
   ```
3. To run the services in the background (detached mode):
   ```bash
   docker compose up -d
   ```
4. Stop the services:
   ```bash
   docker compose down
   ```

---

## 3. Production Hardening Settings

When deploying to a production server:

* **Secure CORS:** Do not use wildcard `FRONTEND_URL` configurations. Change `FRONTEND_URL` to the exact domain address hosting your frontend (e.g. `https://bughunter.company.com`).
* **Manage Secrets Safely:** Do not commit `GROQ_API_KEY` directly to Docker configurations or Git code. Use a secure container orchestration environment configuration (like AWS ECS Secrets, HashiCorp Vault, or Kubernetes Secrets) to mount the key at runtime.
* **Limit Code Size:** Restrict request size parameters to prevent out-of-memory errors on large code submissions.
