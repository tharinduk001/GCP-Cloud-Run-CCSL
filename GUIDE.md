# Full Google Cloud Run Walkthrough — University Demo Guide
### (Windows / PowerShell edition — every command below is a single line, copy-paste as-is)

**Goal:** Take a real Python app from source code -> hardened container -> pushed to
Artifact Registry -> deployed on Cloud Run -> blue/green traffic switch -> fully
automated, health-gated canary CI/CD via Cloud Build.

> Companion files in this project: `main.py`, `templates/index.html`,
> `requirements.txt`, `Dockerfile`, `.dockerignore`, `cloudbuild.yaml` — all
> sitting flat in one folder (e.g. `C:\Users\MSI\Downloads\app`).

> **The one rule that prevents 90% of the errors we hit today:** every
> command in this guide is written as **one single line**. Do not add line
> breaks or backticks. PowerShell's backtick line-continuation is fragile —
> a single trailing space after a backtick silently breaks it with no error
> shown, which is exactly what caused an empty-`$GREEN_URL` problem earlier.
> If a command looks long, that's fine — paste the whole line.

---

## 0. The 8-Phase Roadmap

| Phase | What you'll show the class |
|---|---|
| 1 | The application |
| 2 | Containerizing it (hardened, multi-stage, distroless) |
| 3 | GCP project setup + enabling required services |
| 4 | Pushing the image to Artifact Registry |
| 5 | Deploying to Cloud Run |
| 6 | Blue/Green + canary traffic splitting (manual) |
| 7 | CI/CD automation with Cloud Build (health-gated canary, automated) |
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
   distroless project) — that's what caused an earlier "No module named
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
| `cloudbuild.googleapis.com` | Cloud Build itself — image builds + triggers |
| `iam.googleapis.com` / `iamcredentials.googleapis.com` / `sts.googleapis.com` | IAM management, needed for creating the dedicated Cloud Build service account (Phase 7) |

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

## Phase 6 — Blue/Green (and Canary) Deployments — Manual

Deploy a new version with `--no-traffic`, validate it at a private tagged
URL, then shift traffic gradually or all at once, with zero downtime. Do
this manually once to understand the mechanism, before automating it in
Phase 7.

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

## Phase 7 — CI/CD: Automate the Whole Pipeline with Cloud Build

This is the exact, field-tested sequence — including the two things that
are NOT obvious from Google's own docs and will otherwise cost you an hour
each: (1) 2nd-gen GitHub connections need a dedicated, non-default service
account, and (2) any bash-only variable inside a `cloudbuild.yaml` inline
script must be escaped with `$$`, or Cloud Build tries to resolve it as
one of its own substitution variables and rejects the whole file before
running a single step.

### 7.1 Connect your GitHub repo (one-time, browser-based — can't be scripted)

Console -> **Cloud Build** -> **Repositories** -> **Link repository** ->
in the **Connection** dropdown, choose **+ Create new connection** ->
Region `us-central1` -> Provider **GitHub** -> authorize Google Cloud
Build's GitHub App in the OAuth popup -> pick your repo -> leave
"Repository name: Generated" -> **Link**.

### 7.2 Get the exact resource names (don't guess these)

```powershell
gcloud builds connections list --region=us-central1
```

This prints your connection's `NAME` (e.g. `conn`). Use it here:

```powershell
gcloud builds repositories list --connection=conn --region=us-central1
```

This prints the repository resource `NAME` (e.g.
`your-username-your-repo-name`, auto-prefixed with your GitHub username).
Combine both into the full path you'll need next:

```
projects/YOUR_PROJECT_ID/locations/us-central1/connections/conn/repositories/your-username-your-repo-name
```

### 7.3 Create a DEDICATED service account — do not use the default one

This is the step that isn't documented clearly anywhere. The *default*
Cloud Build service account (`PROJECT_NUMBER@cloudbuild.gserviceaccount.com`)
gets rejected at build-run time with `invalid value for
'build.service_account': provide a user-managed service account or leave
unset`. Simply *omitting* `--service-account` also fails, but at
trigger-*creation* time, with a bare `INVALID_ARGUMENT`. The combination
that actually works is a genuinely separate, user-created service account:

```powershell
gcloud iam service-accounts create cloudbuild-deployer --display-name="Cloud Build Deploy SA"
```

Grant it every role it needs — run each one **separately**. If prompted
with a condition selection menu (this happens because the connection setup
in 7.1 created a conditional IAM binding elsewhere in the policy), type
`2` for **None**:

```powershell
gcloud projects add-iam-policy-binding YOUR_PROJECT_ID --member="serviceAccount:cloudbuild-deployer@YOUR_PROJECT_ID.iam.gserviceaccount.com" --role="roles/run.admin"
```

```powershell
gcloud projects add-iam-policy-binding YOUR_PROJECT_ID --member="serviceAccount:cloudbuild-deployer@YOUR_PROJECT_ID.iam.gserviceaccount.com" --role="roles/artifactregistry.writer"
```

```powershell
gcloud projects add-iam-policy-binding YOUR_PROJECT_ID --member="serviceAccount:cloudbuild-deployer@YOUR_PROJECT_ID.iam.gserviceaccount.com" --role="roles/iam.serviceAccountUser"
```

```powershell
gcloud projects add-iam-policy-binding YOUR_PROJECT_ID --member="serviceAccount:cloudbuild-deployer@YOUR_PROJECT_ID.iam.gserviceaccount.com" --role="roles/cloudbuild.builds.builder"
```

| Role | Why it's needed |
|---|---|
| `roles/run.admin` | Deploy/update the Cloud Run service and shift traffic |
| `roles/artifactregistry.writer` | Push built images |
| `roles/iam.serviceAccountUser` | Allowed to "act as" the Cloud Run runtime service account |
| `roles/cloudbuild.builds.builder` | Allowed to actually execute build steps (the default SA has this automatically; a new custom SA does not) |

### 7.4 Create the trigger, pointing at that dedicated service account

```powershell
gcloud builds triggers create github --name=cloudrun-demo-trigger --repository="projects/YOUR_PROJECT_ID/locations/us-central1/connections/conn/repositories/your-username-your-repo-name" --branch-pattern="^main$" --build-config="cloudbuild.yaml" --region=us-central1 --service-account="projects/YOUR_PROJECT_ID/serviceAccounts/cloudbuild-deployer@YOUR_PROJECT_ID.iam.gserviceaccount.com"
```

### 7.5 What `cloudbuild.yaml` actually does

The companion `cloudbuild.yaml` in this project is a health-gated, gradual
canary pipeline — the same shape a real production rollout uses, not a
straight cutover:

1. **Build** the image
2. **Push** both the commit-SHA tag and `latest` to Artifact Registry
3. **Deploy green at 0% traffic** (`--no-traffic --tag=green`) — blue keeps
   serving 100% of real users throughout
4. **Smoke test gate** — looks up green's private tagged URL via JSON
   (never `--filter`, `describe` doesn't support it) and polls `/health`
   with retries. **If this fails, the whole build stops here** — green
   never receives a single percent of traffic, blue is untouched.
5. **Canary to 10%**, then a short observed pause
6. **Canary to 50%**, then another pause
7. **Promote to 100%**

> Inside that YAML, any all-caps variable meant for bash (like
> `$GREEN_URL`) is written as `$$GREEN_URL`. Cloud Build statically scans
> the *entire* file — including text inside inline bash scripts — for
> `$UPPERCASE_NAME` patterns and tries to resolve them as its own
> substitution variables before the build even starts. An unescaped
> bash-only variable fails validation with something like `key in the
> template "GREEN_URL" is not a valid built-in substitution`, with zero
> steps having run yet. Real Cloud Build substitutions like `${_SERVICE}`
> or the built-in `${SHORT_SHA}` are left as single `$` on purpose — only
> escape the ones that are meant for the shell, not for Cloud Build.

The pauses between canary stages are just `sleep` calls, shortened for a
live demo (`_CANARY_WAIT_SECONDS` substitution, default 30s). Say this out
loud when presenting: real production canary analysis watches actual
error-rate/latency metrics (e.g. via Cloud Monitoring) during that window
automatically, rather than a fixed timer.

### 7.6 Test it

```powershell
git add .
```

```powershell
git commit -m "test cloud build trigger"
```

```powershell
git push origin main
```

Watch it run: Console -> **Cloud Build** -> **History** -> open the build.
You should see all the steps listed above (build, push x2, deploy-green,
smoke-test, canary-10, observe-10, canary-50, observe-50, promote-100). If
you catch it during an "observe" step, quickly check **Cloud Run ->
cloudrun-demo -> Revisions** — you'll see the live traffic split (10% or
50%) in real time.

---

## Phase 8 — Cleanup (so nobody gets a surprise bill)

```powershell
gcloud run services delete cloudrun-demo --region=us-central1
```

```powershell
gcloud artifacts repositories delete cloudrun-demo-repo --location=us-central1
```

Or, simplest of all after the class — this also removes the Cloud Build
trigger, connection, and IAM bindings in one shot, with a 30-day undo
window (`gcloud projects undelete YOUR_PROJECT_ID`):

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
7. Push a commit -> Cloud Build trigger runs live -> automated health-gated canary rollout (5 min)
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
| `INVALID_ARGUMENT` on `gcloud builds triggers create github` (legacy flags) | You're using the legacy 1st-gen `--repo-name`/`--repo-owner` flags — needs a 2nd-gen repository connection instead (Phase 7.1) |
| `INVALID_ARGUMENT` on `gcloud builds triggers create github` (2nd-gen path) | Either you passed a full URL instead of a bare repo name somewhere, or `--service-account` is missing entirely (needed at creation time even though it seems optional) |
| Trigger creates fine but the *build* fails: `invalid value for 'build.service_account'` | You pointed the trigger at the **default** Cloud Build service account — create a dedicated one instead (Phase 7.3) |
| `key in the template "..." is not a valid built-in substitution` | An all-caps bash variable in a `cloudbuild.yaml` inline script wasn't escaped — change `$VARNAME` to `$$VARNAME` for anything meant for bash, not Cloud Build |
| A `gcloud ... add-iam-policy-binding` command asks you to choose `[1] [2] [3]` for a condition | The project's IAM policy already has a conditional binding (usually from the Cloud Build connection setup) — type `2` for **None** to add your new grant unconditionally |
| Cloud Build trigger doesn't fire on push | Confirm you pushed to the exact branch in `--branch-pattern` (`^main$` matches only `main`), and that the repo shown in the trigger matches the one you linked |
| Container fails to start on Cloud Run | Not listening on `$PORT`, or crashing on boot — check `gcloud run services logs read cloudrun-demo --region=us-central1` |
| Tagged URL 404s | Confirm the tag name matches exactly |

---

## Why This Setup Reflects Current (2026) Best Practice

- **Artifact Registry, not Container Registry** — GCR was fully retired
  through 2025.
- **Distroless + multi-stage + non-root + `pip install --target`** —
  minimal attack surface, no interpreter-path mismatch across stages.
- **2nd-gen Cloud Build GitHub connections + a dedicated, non-default
  service account** — the legacy 1st-gen trigger flags and the default
  Cloud Build service account are both dead ends under current policy;
  Google's own docs don't make this obvious.
- **Revision-based blue/green via `--no-traffic` + tags** — Cloud Run's
  native mechanism, no extra infrastructure required.
- **Health-gated, gradual canary rollout in CI** — the automated pipeline
  mirrors real production practice (deploy dark, smoke-test, ramp
  gradually) instead of a straight-to-100% promotion.
- **Scale-to-zero (`min-instances=0`)** — keeps a classroom demo essentially
  free.