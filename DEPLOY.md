# Deploying the FastLocations Scoring API (Render)

This puts the scorer online at a public HTTPS URL your site can call. The runtime only
needs the small precomputed files (~2 MB) — the big raw layers are git-ignored and not used.

## Files that get deployed (all already in this folder)
app.py · scorer.py · site_selection_intake.html ·
county_features.json · edo_master_table_dual.json · edo_fips_index.json · incentives_index.json ·
requirements.txt · Procfile · runtime.txt · render.yaml · .gitignore

## One-time: put this folder in a GitHub repo
1. Create an empty repo at github.com (e.g. `fastlocations-scoring-api`).
2. In this folder:
   ```
   git init
   git add .
   git commit -m "FastLocations scoring API"
   git branch -M main
   git remote add origin https://github.com/<you>/fastlocations-scoring-api.git
   git push -u origin main
   ```

## Deploy on Render
1. Sign in at render.com → **New +** → **Blueprint**.
2. Connect the GitHub repo. Render reads `render.yaml` and configures everything
   (build: `pip install -r requirements.txt`, start: `gunicorn app:app --bind 0.0.0.0:$PORT`).
3. Click **Apply / Deploy**. First build takes a few minutes.
4. You get a URL like `https://fastlocations-scoring-api.onrender.com`.

(No `render.yaml`? Use **New + → Web Service**, pick the repo, Runtime = Python,
Start command = `gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --timeout 120`.)

## Verify
- Open `https://<your-app>.onrender.com/health` → should return
  `{"status":"ok","counties":3143,...}`.
- Open `https://<your-app>.onrender.com/` → the intake form, fully working
  (it serves the form and the API from one place; `API_BASE=''` already handles this).

## Attach to fastlocations.ai
Two ways:
- **Simplest:** link/iframe to the Render URL — it already serves the form.
- **Embed on your site:** host `site_selection_intake.html` on fastlocations.ai and set the
  one line near the top of its `<script>`:
  ```
  const API_BASE = 'https://<your-app>.onrender.com';
  ```
  CORS is already enabled, so the cross-domain call works.

## Optional: custom subdomain api.fastlocations.ai
Render → your service → **Settings → Custom Domains → Add** `api.fastlocations.ai`.
Render shows a CNAME target; add that CNAME at your DNS provider. Then use
`const API_BASE = 'https://api.fastlocations.ai';`.

## Heads-up
- Render's **free plan sleeps** after ~15 min idle, so the first request after a lull takes
  ~30–60 s to wake. Upgrade to a paid instance for always-on, or use Railway (usage-based,
  no sleep) — same files, deploy command `gunicorn app:app --bind 0.0.0.0:$PORT`.
- Updating data/code = `git push`; Render auto-redeploys (`autoDeploy: true`).
