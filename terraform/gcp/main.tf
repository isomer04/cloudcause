/*
 * CloudCause on Cloud Run: the FastAPI gateway and the Next.js frontend.
 *
 * The gateway runs the ADK orchestrator and both framework workers in process.
 * The distributed topology is not deleted, only not deployed — see ADR 0012 and
 * docker/docker-compose.yml.
 *
 * Images are built by Cloud Build and passed in by tag, so apply needs no local
 * Docker daemon. Deploy steps: docs/usage.md#deploying-to-cloud-run
 */

terraform {
  required_version = ">= 1.5"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

locals {
  registry    = "${var.region}-docker.pkg.dev/${var.project_id}/${var.repository_name}"
  api_service = "${var.name_prefix}-api"
  web_service = "${var.name_prefix}-web"
  api_image   = "${local.registry}/api:${var.image_tag}"
  web_image   = "${local.registry}/web:${var.image_tag}"

  api_port = 8000
  web_port = 3000

  # Shown in the Cloud Run console and usable as a billing breakdown filter.
  common_labels = {
    application = var.name_prefix
    environment = var.environment
    managed-by  = "terraform"
  }

  # for_each cannot take a sensitive input. nonsensitive() here exposes only
  # whether a key was supplied, never its value.
  has_openai = nonsensitive(var.openai_api_key != "" ? toset(["set"]) : toset([]))
  has_google = nonsensitive(var.google_api_key != "" ? toset(["set"]) : toset([]))
}

resource "google_project_service" "apis" {
  for_each = toset([
    "run.googleapis.com",
    "artifactregistry.googleapis.com",
    "cloudbuild.googleapis.com",
  ])

  service            = each.value
  disable_on_destroy = false
}

# Create this before the first `gcloud builds submit`, or the push has nowhere
# to land: terraform apply -target=google_artifact_registry_repository.containers
resource "google_artifact_registry_repository" "containers" {
  location      = var.region
  repository_id = var.repository_name
  format        = "DOCKER"
  description   = "CloudCause gateway and frontend images"
  labels        = local.common_labels

  depends_on = [google_project_service.apis]
}

# ---------------------------------------------------------------------------
# Gateway
# ---------------------------------------------------------------------------
resource "google_cloud_run_v2_service" "api" {
  name                = local.api_service
  location            = var.region
  description         = "CloudCause FastAPI gateway, ADK orchestrator, and both framework workers"
  labels              = merge(local.common_labels, { component = "api" })
  deletion_protection = false
  ingress             = "INGRESS_TRAFFIC_ALL"

  template {
    scaling {
      min_instance_count = var.api_min_instances
      max_instance_count = var.api_max_instances
    }

    # A live investigation outruns the 5 minute default.
    timeout                          = "600s"
    max_instance_request_concurrency = 8
    labels                           = merge(local.common_labels, { component = "api" })

    containers {
      name  = "gateway"
      image = local.api_image

      resources {
        limits = {
          cpu    = "2"
          memory = var.api_memory
        }
        # Live investigations continue in a background task after the response
        # that started them was sent. Without CPU held past the request, that
        # work is throttled to a crawl.
        cpu_idle          = false
        startup_cpu_boost = true
      }

      ports {
        container_port = local.api_port
      }

      # The image sets CLOUDCAUSE_HOST=0.0.0.0 and CLOUDCAUSE_REPO_ROOT already.
      env {
        name  = "CLOUDCAUSE_DATA_MODE"
        value = "fixtures"
      }

      env {
        name  = "CLOUDCAUSE_AGENT_MODE"
        value = var.agent_mode
      }

      # One budget for the whole investigation, shared by all three provider
      # agents (Orchestrator.run binds a single AgentCallBudget). Sized for the
      # multi-cloud default: at a per-provider-sized 12 the three agents race,
      # the first exhausts it, and the rest fall back to deterministic playbooks.
      env {
        name  = "CLOUDCAUSE_MAX_AGENT_CALLS"
        value = tostring(var.max_agent_calls)
      }

      env {
        name  = "CLOUDCAUSE_ORCHESTRATOR_MODE"
        value = "inprocess"
      }

      env {
        name  = "CLOUDCAUSE_WORKER_MODE"
        value = "inprocess"
      }

      env {
        name  = "CLOUDCAUSE_DISPATCH_MODE"
        value = "background"
      }

      # Per-instance, lost on every revision and scale-to-zero.
      env {
        name  = "CLOUDCAUSE_HISTORY_BACKEND"
        value = "memory"
      }

      env {
        name  = "CLOUDCAUSE_UPLOADS_ENABLED"
        value = tostring(var.uploads_enabled)
      }

      # resolve_peer_ip reads the LEFTMOST X-Forwarded-For entry, and Cloud Run
      # appends the real address after whatever the client sent. Trusting the
      # header here would let any caller choose its own rate-limit bucket and
      # evade the live-investigation quota. Every client therefore shares one
      # bucket, and CLOUDCAUSE_GLOBAL_LIVE_STARTS_PER_MINUTE is the real cap.
      env {
        name  = "CLOUDCAUSE_TRUST_PROXY_HEADERS"
        value = "false"
      }

      env {
        name  = "CLOUDCAUSE_LIVE_RATE_LIMIT_ENABLED"
        value = "true"
      }

      # In-memory buckets. Raising api_max_instances needs a Redis backend.
      env {
        name  = "CLOUDCAUSE_RATE_LIMIT_BACKEND"
        value = "memory"
      }

      # Omitted entirely when unset, so the gateway reports live agents as
      # unavailable rather than holding an empty key.
      dynamic "env" {
        for_each = local.has_openai
        content {
          name  = "OPENAI_API_KEY"
          value = var.openai_api_key
        }
      }

      dynamic "env" {
        for_each = local.has_google
        content {
          name  = "GOOGLE_API_KEY"
          value = var.google_api_key
        }
      }

      # AI Studio key, not Vertex credentials.
      dynamic "env" {
        for_each = local.has_google
        content {
          name  = "GOOGLE_GENAI_USE_ENTERPRISE"
          value = "FALSE"
        }
      }

      # Generous because a cold start pulls the image before the process
      # starts. The gateway itself reaches /health in about 4 seconds.
      startup_probe {
        initial_delay_seconds = 10
        period_seconds        = 10
        timeout_seconds       = 5
        failure_threshold     = 12

        http_get {
          path = "/health"
          port = local.api_port
        }
      }

      liveness_probe {
        period_seconds    = 60
        timeout_seconds   = 10
        failure_threshold = 3

        http_get {
          path = "/health"
          port = local.api_port
        }
      }
    }
  }

  depends_on = [
    google_project_service.apis,
    google_artifact_registry_repository.containers,
  ]
}

# ---------------------------------------------------------------------------
# Frontend
# ---------------------------------------------------------------------------
resource "google_cloud_run_v2_service" "web" {
  name                = local.web_service
  location            = var.region
  description         = "CloudCause Next.js frontend, proxying to the gateway server-side"
  labels              = merge(local.common_labels, { component = "web" })
  deletion_protection = false
  ingress             = "INGRESS_TRAFFIC_ALL"

  template {
    scaling {
      min_instance_count = 0
      max_instance_count = var.web_max_instances
    }

    timeout = "300s"
    labels  = merge(local.common_labels, { component = "web" })

    containers {
      name  = "frontend"
      image = local.web_image

      resources {
        limits = {
          cpu    = "1"
          memory = "512Mi"
        }
        startup_cpu_boost = true
      }

      ports {
        container_port = local.web_port
      }

      # Read server-side only; the browser never sees this URL.
      env {
        name  = "CLOUDCAUSE_API_URL"
        value = google_cloud_run_v2_service.api.uri
      }

      startup_probe {
        period_seconds    = 5
        timeout_seconds   = 3
        failure_threshold = 12

        tcp_socket {
          port = local.web_port
        }
      }
    }
  }

  depends_on = [
    google_project_service.apis,
    google_artifact_registry_repository.containers,
  ]
}

# ---------------------------------------------------------------------------
# Access. Both are public: the frontend is the entry point, and its proxy
# reaches the gateway as an anonymous client without minting an ID token.
# If constraints/iam.allowedPolicyMemberDomains blocks allUsers, apply
# allow-all-policy.yaml to the project first.
# ---------------------------------------------------------------------------
resource "google_cloud_run_v2_service_iam_member" "api_public" {
  name     = google_cloud_run_v2_service.api.name
  location = google_cloud_run_v2_service.api.location
  role     = "roles/run.invoker"
  member   = "allUsers"
}

resource "google_cloud_run_v2_service_iam_member" "web_public" {
  name     = google_cloud_run_v2_service.web.name
  location = google_cloud_run_v2_service.web.location
  role     = "roles/run.invoker"
  member   = "allUsers"
}

output "web_url" {
  value       = google_cloud_run_v2_service.web.uri
  description = "Public CloudCause URL. This is the one to open."
}

output "api_url" {
  value       = google_cloud_run_v2_service.api.uri
  description = "Gateway URL. /docs serves the OpenAPI browser."
}

output "api_image" {
  value       = local.api_image
  description = "Image the gateway service expects. Build this tag before applying."
}

output "web_image" {
  value       = local.web_image
  description = "Image the frontend service expects. Build this tag before applying."
}
