# Full Google Cloud Run Walkthrough — University Demo Guide
### (Windows / PowerShell edition — every command below is a single line, copy-paste as-is)

**Goal:** Take a real Python app from source code -> hardened container -> pushed to
Artifact Registry -> deployed on Cloud Run -> blue/green traffic switch -> fully
automated CI/CD.

> Companion files in this project: `main.py`, `templates/index.html`,
> `requirements.txt`, `Dockerfile`, `.dockerignore`, `cloudbuild.yaml`,
> `.github/workflows/deploy.yml` — all sitting flat in one folder
> (e.g. `C:\Users\MSI\Downloads\app`), matching your actual setup.

> **The one rule that prevents 90% of the errors we hit today:** every
> command in this guide is written as **one single line**. Do not add line
> breaks or backticks. PowerShell's backtick line-continuation is fragile —
> a single trailing space after a backtick silently breaks it with no error
> shown, which is exactly what caused the empty-`$GREEN_URL` problem
> earlier. If a command looks long, that's fine — paste the whole line.

---

## 0. The 8-Phase Roadmap

| Phase | What you'll show the class |
|---|---|
| 1 | The application |
| 2 | Containerizing it (hardened, multi-stage, distroless) |
| 3 | GCP project setup + enabling required services |
| 4 | Pushing the image to Artifact Registry |
| 5 | Deploying to Cloud Run |
| 6 | Blue/Green + canary traffic splitting |
| 7 | CI/CD automation |
| 8 | Cleanup |

---

## Phase 1 — The Application

A small **Flask** app (`main.py`) built to teach Cloud Run concepts visually:

- Reads Cloud Run's auto-injected env vars (`K_SERVICE`, `K_REVISION`,
  `K_CONFIGURATION`) and shows them on a live dashboard.
- `APP_COLOR` / `APP_VERSION` env vars let you deploy visibly different
  "blue" and "green" versions for the blue/green demo.
- In-memory hit counter shows Cloud Run instances are stateless/ephemeral.
- `/health` -> JSON `200 OK` (Cloud Run health probes).
- `/api/info` -> JSON metadata.

**Run it locally first** (optional sanity check):

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python main.py
```

Visit `http://localhost:8080`. Stop it with `Ctrl+C` when done.

---

## Phase 2 — Containerizing (Best Practices Baked In)

Open `Dockerfile` and talk through these decisions live:

1. **Multi-stage build.** Stage 1 (`builder`, `python:3.11-slim`) installs
   dependencies as plain files into `/deps` using `pip install --target`
   — **not** a virtual environment. A venv's `python3` binary is a symlink
   back to the builder image's interpreter path, which does not exist in
   the distroless final image (a documented limitation of Google's
   distroless project) — that's what caused the earlier "No module named
   gunicorn" error. Stage 2 copies only `/deps` + app code, and runs them
   with the distroless image's *own* Python 3.11 via `PYTHONPATH`.
2. **Distroless final image** (`gcr.io/distroless/python3-debian12:nonroot`
   — Python 3.11, hence the builder also uses 3.11 to match exactly).
   No shell, no package manager. If an attacker got code execution inside
   the container, there's no `bash`, no `apt`, nothing to pivot with.
3. **Non-root by default** — `:nonroot` tag runs as UID `65532`.
4. **Layer-cache friendly** — `requirements.txt` copied and installed
   before app source.
5. **`.dockerignore`** keeps `.git`, docs, and `.venv` out of the build
   context.
6. **Exec-form `ENTRYPOINT`** running `gunicorn.app.wsgiapp` (gunicorn's own
   documented module-invocation path) — required since distroless has no
   shell to interpret shell-form commands.
7. Listens on `$PORT` (Cloud Run injects `PORT=8080`).

**Build & test locally** (Docker Desktop must be running):

```powershell
docker build --no-cache -t cloudrun-demo:local .
```

```powershell
docker run --rm -p 8080:8080 -e APP_COLOR=blue -e APP_VERSION=local cloudrun-demo:local
```

You should see gunicorn's startup lines (`Listening at: http://0.0.0.0:8080`).
Visit `http://localhost:8080`. Stop with `Ctrl+C`.

---

## Phase 3 — Google Cloud Account & Project Setup

### 3.1 Concepts (explain, don't just click)
- **Google Cloud account** = your identity (Gmail login).
- **Billing account** = payment method attached. Cloud Run's always-free
  monthly quota (~2 million requests, 360k GB-seconds memory, 180k
  vCPU-seconds) means a classroom demo will almost certainly cost **$0**.
- **Project** = isolation boundary for resources, IAM, and billing.

### 3.2 Create a project

Project IDs are globally unique across ALL of GCP — pick something specific
to you.

```powershell
gcloud projects create your-unique-project-id --name="Cloud Run Demo"
```

```powershell
gcloud config set project your-unique-project-id
```

> From here on, replace `your-unique-project-id` with your **real** project
> ID everywhere in this guide. Check it any time with:
> `gcloud config get-value project`

### 3.3 Enable the required APIs

```powershell
gcloud services enable run.googleapis.com artifactregistry.googleapis.com cloudbuild.googleapis.com iam.googleapis.com iamcredentials.googleapis.com sts.googleapis.com --project=your-unique-project-id
```

| API | Why you need it |
|---|---|
| `run.googleapis.com` | Cloud Run itself |
| `artifactregistry.googleapis.com` | Stores your Docker images (Container Registry is retired) |
| `cloudbuild.googleapis.com` | Google-side image builds + Cloud Build triggers |
| `iam.googleapis.com` / `iamcredentials.googleapis.com` / `sts.googleapis.com` | Needed for Workload Identity Federation (Phase 7) |

### 3.4 Set a default region

```powershell
gcloud config set run/region us-central1
```

(Swap for a region closer to you, e.g. `asia-south1`, if you prefer — just
use the same one everywhere below.)

---

## Phase 4 — Artifact Registry: Create Repo & Push the Image

> Container Registry (`gcr.io` pushes) was fully shut down through 2025.
> Artifact Registry is the only supported path now.

### 4.1 Create the repository (one-time)

```powershell
gcloud artifacts repositories create cloudrun-demo-repo --repository-format=docker --location=us-central1 --description="Images for the Cloud Run demo"
```

### 4.2 Authenticate Docker to Artifact Registry

```powershell
gcloud auth configure-docker us-central1-docker.pkg.dev
```

### 4.3 Set your image variable (run this once per new terminal session)

```powershell
$env:PROJECT_ID = (gcloud config get-value project)
```

```powershell
$env:IMAGE = "us-central1-docker.pkg.dev/$($env:PROJECT_ID)/cloudrun-demo-repo/cloudrun-demo"
```

Confirm it's set correctly:

```powershell
echo $env:IMAGE
```

### 4.4 Build and push

```powershell
docker build -t "$($env:IMAGE):v1" -t "$($env:IMAGE):latest" .
```

```powershell
docker push "$($env:IMAGE):v1"
```

```powershell
docker push "$($env:IMAGE):latest"
```

**Alternative (no local Docker needed):** let Google build it for you:

```powershell
gcloud builds submit --tag "$($env:IMAGE):v1" .
```

Verify the push:

```powershell
gcloud artifacts docker images list "us-central1-docker.pkg.dev/$($env:PROJECT_ID)/cloudrun-demo-repo"
```

---

## Phase 5 — Deploy to Cloud Run

### 5.1 First deploy — via CLI

```powershell
gcloud run deploy cloudrun-demo --image="$($env:IMAGE):v1" --region=us-central1 --allow-unauthenticated --port=8080 --min-instances=0 --max-instances=4 --cpu=1 --memory=256Mi --concurrency=80 --set-env-vars=APP_COLOR=blue,APP_VERSION=v1 --tag=blue
```

Explain each flag live:

| Flag | Meaning |
|---|---|
| `--allow-unauthenticated` | Public URL, no IAM token required |
| `--min-instances=0` | Scale to **zero** when idle -> $0 cost between demos |
| `--max-instances=4` | Hard ceiling on scale-out |
| `--cpu` / `--memory` | Per-instance resource allocation |
| `--concurrency=80` | Max simultaneous requests per instance before scaling out |

Cloud Run prints a service URL immediately — open it, refresh a few times,
point out the hit counter climbing and the revision name.

### 5.2 The same thing via Console

1. Console -> **Cloud Run** -> **Create Service**
2. **Deploy one revision from an existing container image** -> browse
   Artifact Registry -> pick your image
3. Fill in the same settings as above as form fields
4. **Create**

### 5.3 Understanding Revisions

```powershell
gcloud run revisions list --service=cloudrun-demo --region=us-central1
```

Every deploy creates a new, immutable **Revision** — Cloud Run never
overwrites one. This is what makes blue/green and instant rollback possible.

---

## Phase 6 — Blue/Green (and Canary) Deployments

Deploy a new version with `--no-traffic`, validate it at a private tagged
URL, then shift traffic gradually or all at once, with zero downtime.

### 6.1 Build v2 and deploy "green" without sending it any production traffic

```powershell
docker build -t "$($env:IMAGE):v2" .
```

```powershell
docker push "$($env:IMAGE):v2"
```

```powershell
gcloud run deploy cloudrun-demo --image="$($env:IMAGE):v2" --region=us-central1 --no-traffic --tag=green --set-env-vars=APP_COLOR=green,APP_VERSION=v2
```

The deploy output prints a line like:
`The revision can be reached directly at https://green---cloudrun-demo-xxxxx.a.run.app`
— **copy that URL**, that's your tagged test URL. Production traffic is
completely unaffected.

### 6.2 Validate the green revision

> `gcloud run services describe` does **not** support `--filter` (that flag
> only works on `list`-style commands returning multiple resources). Use
> the URL printed by the deploy command above, or parse the JSON directly:

```powershell
$GREEN_URL = "PASTE-THE-URL-FROM-THE-DEPLOY-OUTPUT-HERE"
```

Or fetch it programmatically instead of copy-pasting:

```powershell
$json = gcloud run services describe cloudrun-demo --region=us-central1 --format=json | ConvertFrom-Json
```

```powershell
$GREEN_URL = ($json.status.traffic | Where-Object { $_.tag -eq 'green' }).url
```

```powershell
echo $GREEN_URL
```

Confirm it printed a real `https://green---...` URL, then:

```powershell
curl.exe -sf "$GREEN_URL/health"
```

```powershell
Start-Process $GREEN_URL
```

### 6.3 Shift traffic — gradually (canary) or all at once (classic blue/green)

Gradual / canary, narrating each jump live:

```powershell
gcloud run services update-traffic cloudrun-demo --region=us-central1 --to-tags=green=10
```

```powershell
gcloud run services update-traffic cloudrun-demo --region=us-central1 --to-tags=green=50
```

```powershell
gcloud run services update-traffic cloudrun-demo --region=us-central1 --to-tags=green=100
```

Or cut over instantly (classic blue/green) — just run the `green=100` line
directly.

Refresh the production URL between each command — the badge color/version
flips live; with the canary steps you can mash-refresh and see a mix of
blue and green responses.

### 6.4 Instant rollback

```powershell
gcloud run services update-traffic cloudrun-demo --region=us-central1 --to-tags=blue=100
```

(Requires that you tagged the original v1 deploy `--tag=blue` — if you
didn't, roll back by exact revision name instead, from `revisions list`:)

```powershell
gcloud run services update-traffic cloudrun-demo --region=us-central1 --to-revisions=cloudrun-demo-00001-xxx=100
```

> **Teaching point:** unlike traditional blue/green on VMs, you never had to
> provision a second environment — Cloud Run's revision model gives you
> blue/green "for free" as a first-class platform feature.

---

## Phase 7 — CI/CD: Automate the Whole Pipeline

Two options included in this project.

### Option A — GitHub Actions with Workload Identity Federation (recommended)

No long-lived secret sits in GitHub — GitHub's OIDC token is exchanged for
a short-lived GCP token at run time.

**One-time setup** (these are bash-style multi-line commands but you're
running them once from your own terminal to configure GCP, not from
PowerShell — if you're doing this from PowerShell too, run each `gcloud`
command below as a single line, same rule as everywhere else in this guide):

```powershell
$env:PROJECT_ID = (gcloud config get-value project)
```

```powershell
$env:PROJECT_NUMBER = (gcloud projects describe $env:PROJECT_ID --format="value(projectNumber)")
```

```powershell
$env:REPO = "your-github-username/your-repo-name"
```

```powershell
gcloud iam workload-identity-pools create "github-pool" --location="global" --display-name="GitHub Actions Pool"
```

```powershell
gcloud iam workload-identity-pools providers create-oidc "github-provider" --location="global" --workload-identity-pool="github-pool" --display-name="GitHub provider" --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository" --attribute-condition="assertion.repository=='$($env:REPO)'" --issuer-uri="https://token.actions.githubusercontent.com"
```

```powershell
gcloud iam service-accounts create github-deployer --display-name="GitHub Actions Cloud Run Deployer"
```

```powershell
gcloud projects add-iam-policy-binding $env:PROJECT_ID --member="serviceAccount:github-deployer@$($env:PROJECT_ID).iam.gserviceaccount.com" --role="roles/run.admin"
```

```powershell
gcloud projects add-iam-policy-binding $env:PROJECT_ID --member="serviceAccount:github-deployer@$($env:PROJECT_ID).iam.gserviceaccount.com" --role="roles/artifactregistry.writer"
```

```powershell
gcloud projects add-iam-policy-binding $env:PROJECT_ID --member="serviceAccount:github-deployer@$($env:PROJECT_ID).iam.gserviceaccount.com" --role="roles/iam.serviceAccountUser"
```

```powershell
gcloud iam service-accounts add-iam-policy-binding "github-deployer@$($env:PROJECT_ID).iam.gserviceaccount.com" --role="roles/iam.workloadIdentityUser" --member="principalSet://iam.googleapis.com/projects/$($env:PROJECT_NUMBER)/locations/global/workloadIdentityPools/github-pool/attribute.repository/$($env:REPO)"
```

Then edit `.github/workflows/deploy.yml`:
- Replace `your-gcp-project-id` with your real project ID
- Replace `PROJECT_NUMBER` in the `workload_identity_provider` line with
  your real project number

Push to `main` -> GitHub Actions builds, pushes to Artifact Registry,
deploys with `--no-traffic --tag=green`, smoke-tests it, then promotes to
100% automatically. (The workflow's own commands run on GitHub's Linux
runners, so they use normal bash — nothing to translate there.)

### Option B — Native Cloud Build trigger (simpler, no GitHub secrets at all)

```powershell
gcloud builds triggers create github --repo-name="your-repo-name" --repo-owner="your-github-username" --branch-pattern="^main$" --build-config="cloudbuild.yaml"
```

Cloud Build runs *inside* GCP with its own service account — nothing
external to configure.

---

## Phase 8 — Cleanup (so nobody gets a surprise bill)

```powershell
gcloud run services delete cloudrun-demo --region=us-central1
```

```powershell
gcloud artifacts repositories delete cloudrun-demo-repo --location=us-central1
```

Or, simplest of all after the class:

```powershell
gcloud projects delete your-unique-project-id
```

---

## Live Demo Day — Suggested Run Order (~20-25 min)

1. Show `main.py` + `Dockerfile` — explain multi-stage + distroless (3 min)
2. `docker build` + `docker run` locally (2 min)
3. Create Artifact Registry repo, push image (3 min)
4. Deploy v1 ("blue"), open the live URL (3 min)
5. Deploy v2 ("green") with `--no-traffic --tag=green`, hit the tagged URL (3 min)
6. `update-traffic --to-tags green=10/50/100` while refreshing the browser (4 min)
7. Push a commit -> GitHub Actions pipeline runs live -> auto blue/green (5 min)
8. Q&A / cleanup (2 min)

---

## Troubleshooting Cheat Sheet

| Symptom | Cause / fix |
|---|---|
| `The token '&&' is not a valid statement separator` | Bash syntax pasted into PowerShell — use separate lines or `;` |
| `export` not recognized | Bash-ism — use `$env:NAME = "value"` |
| `${IMAGE}` or `$IMAGE` passed literally | PowerShell doesn't expand `${VAR}` — use `"$($env:IMAGE)"` |
| A `$VAR = ...` command leaves `$VAR` empty | Multi-line backtick continuation broke silently — always write it as ONE line, then `echo $VAR` to confirm |
| `jinja2.exceptions.TemplateNotFound: index.html` | `index.html` must be inside a `templates/` folder next to `main.py` |
| `gcloud config set project` says permission denied | You used a placeholder project ID instead of one you created — run `gcloud projects create` first |
| `docker build` error: `"/app": not found` | `Dockerfile`'s `COPY ... /app/` line assumed a subfolder that doesn't exist in your flat layout — should be `COPY . /app/` |
| `No module named gunicorn` at runtime | venv-in-distroless interpreter mismatch — current `Dockerfile` uses `pip install --target=/deps` instead, not a venv |
| `unrecognized arguments: --filter=...` on `services describe` | `describe` doesn't support `--filter` — use the URL from the deploy output, or parse JSON with `ConvertFrom-Json` (see Phase 6.2) |
| `PERMISSION_DENIED` on `gcloud run deploy` | An API isn't enabled — re-run the Phase 3.3 command |
| `docker push` gets `denied` | Run `gcloud auth configure-docker us-central1-docker.pkg.dev` again |
| `service.spec...image: expected a container image path...` | Your `--image` value contains a literal, un-expanded `${...}` — use `"$($env:IMAGE)"` |
| Container fails to start on Cloud Run | Not listening on `$PORT`, or crashing on boot — check `gcloud run services logs read cloudrun-demo --region=us-central1` |
| Tagged URL 404s | Confirm the tag name matches exactly |
| GitHub Actions `auth` step fails | `workload_identity_provider` needs your **project number**, not project ID; `attribute-condition` repo string must match your repo exactly |

---

## Why This Setup Reflects Current (2026) Best Practice

- **Artifact Registry, not Container Registry** — GCR was fully retired
  through 2025.
- **Distroless + multi-stage + non-root + `pip install --target`** —
  minimal attack surface, no interpreter-path mismatch across stages.
- **Workload Identity Federation over service-account JSON keys** —
  Google's current recommended CI/CD auth pattern; no long-lived secrets.
- **Revision-based blue/green via `--no-traffic` + tags** — Cloud Run's
  native mechanism, no extra infrastructure required.
- **Scale-to-zero (`min-instances=0`)** — keeps a classroom demo essentially
  free.
