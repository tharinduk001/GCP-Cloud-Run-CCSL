# Cloud Run Demo

A small Flask application for demonstrating container deployment to Google Cloud Run.

## Features

- Dashboard with Cloud Run service and revision metadata
- Configurable deployment color and version
- In-memory request counter
- `GET /health` health check
- `GET /api/info` JSON metadata endpoint

## Requirements

- Python 3.11 or later
- Docker Desktop for container builds
- Google Cloud CLI for deployment

## Run Locally

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python main.py
```

Open <http://localhost:8080>.

Optional environment variables:

```powershell
$env:APP_COLOR = "blue"
$env:APP_VERSION = "local"
```

## Run with Docker

```powershell
docker build -t cloudrun-demo:local .
docker run --rm -p 8080:8080 -e APP_COLOR=blue -e APP_VERSION=local cloudrun-demo:local
```

Open <http://localhost:8080>.

The image uses a multi-stage build, a distroless Python runtime, and a non-root user.

## Deploy to Cloud Run

Set your project and region:

```powershell
gcloud config set project YOUR_PROJECT_ID
gcloud config set run/region us-central1
```

Enable the required services:

```powershell
gcloud services enable run.googleapis.com artifactregistry.googleapis.com cloudbuild.googleapis.com
```

For a 2nd-generation Cloud Build connection to GitHub, also enable Secret
Manager because Google stores the GitHub App access token there:

```powershell
gcloud services enable secretmanager.googleapis.com
```

Create an Artifact Registry repository once:

```powershell
gcloud artifacts repositories create cloudrun-demo-repo --repository-format=docker --location=us-central1
gcloud auth configure-docker us-central1-docker.pkg.dev
```

Build and push the image:

```powershell
$env:PROJECT_ID = gcloud config get-value project
$env:IMAGE = "us-central1-docker.pkg.dev/$($env:PROJECT_ID)/cloudrun-demo-repo/cloudrun-demo"
docker build -t "$($env:IMAGE):v1" .
docker push "$($env:IMAGE):v1"
```

Deploy it:

```powershell
gcloud run deploy cloudrun-demo --image="$($env:IMAGE):v1" --region=us-central1 --allow-unauthenticated --port=8080 --min-instances=0 --max-instances=4 --cpu=1 --memory=256Mi --concurrency=80 --set-env-vars=APP_COLOR=blue,APP_VERSION=v1
```

## Blue/Green Deployment

Build and push a second image:

```powershell
docker build -t "$($env:IMAGE):v2" .
docker push "$($env:IMAGE):v2"
```

Deploy it without production traffic:

```powershell
gcloud run deploy cloudrun-demo --image="$($env:IMAGE):v2" --region=us-central1 --no-traffic --tag=green --set-env-vars=APP_COLOR=green,APP_VERSION=v2
```

After testing the tagged revision, shift traffic:

```powershell
gcloud run services update-traffic cloudrun-demo --region=us-central1 --to-tags=green=10
gcloud run services update-traffic cloudrun-demo --region=us-central1 --to-tags=green=50
gcloud run services update-traffic cloudrun-demo --region=us-central1 --to-tags=green=100
```

Rollback to the previous tagged revision:

```powershell
gcloud run services update-traffic cloudrun-demo --region=us-central1 --to-tags=blue=100
```

## Cloud Build

[`cloudbuild.yaml`](cloudbuild.yaml) builds and pushes the image, deploys a green revision, and promotes it to 100% traffic.

When creating a 2nd-generation GitHub repository connection, enable
`secretmanager.googleapis.com` first. The connection uses Secret Manager to
store the GitHub App access token.

Create a Cloud Build trigger connected to this repository and configure these substitutions when needed:

- `_SERVICE`: Cloud Run service name
- `_REGION`: deployment region
- `_IMAGE`: Artifact Registry image path

## Cleanup

```powershell
gcloud run services delete cloudrun-demo --region=us-central1
gcloud artifacts repositories delete cloudrun-demo-repo --location=us-central1
```

Replace `YOUR_PROJECT_ID` with your Google Cloud project ID before running deployment commands.
