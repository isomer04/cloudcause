/*
 * CloudCause portfolio deployment.
 *
 * Deliberately minimal and read-only in spirit: this defines where the four
 * containers run and nothing about customer cloud accounts. Applying it is
 * optional; the public demo runs on fixture data with stub agents.
 *
 *   terraform init
 *   terraform plan -var project_id=<your-project> -var region=us-central1
 *
 * Nothing here grants write access to a billing account. When live provider
 * mode is added, attach read-only roles only:
 *   AWS   - Cost Explorer read, CloudWatch read, CloudTrail lookup, tagging read
 *   Azure - Cost Management Reader, Reader, Monitoring Reader, Advisor read
 *   GCP   - Billing Account Viewer, BigQuery Data Viewer, Cloud Asset Viewer,
 *           Recommender Viewer, Logging Viewer
 */

terraform {
  required_version = ">= 1.6"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }
}

variable "project_id" {
  description = "Google Cloud project that hosts the demo services."
  type        = string
}

variable "region" {
  description = "Region for Cloud Run services."
  type        = string
  default     = "us-central1"
}

variable "image" {
  description = "Container image built from infra/docker/Dockerfile."
  type        = string
  default     = "gcr.io/PROJECT_ID/cloudcause:latest"
}

variable "data_mode" {
  description = "fixtures (public demo) or live (requires read-only provider credentials)."
  type        = string
  default     = "fixtures"

  validation {
    condition     = contains(["fixtures", "live"], var.data_mode)
    error_message = "data_mode must be fixtures or live."
  }
}

variable "agent_mode" {
  description = "stub (deterministic, free) or live (needs model API keys)."
  type        = string
  default     = "stub"

  validation {
    condition     = contains(["stub", "live"], var.agent_mode)
    error_message = "agent_mode must be stub or live."
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

locals {
  # One image, four commands. Ports match the local Docker Compose layout.
  services = {
    "cloudcause-aws-worker"   = { command = "cloudcause-aws-worker", port = 8101 }
    "cloudcause-azure-worker" = { command = "cloudcause-azure-worker", port = 8102 }
    "cloudcause-orchestrator" = { command = "cloudcause-orchestrator", port = 8100 }
    "cloudcause-api"          = { command = "cloudcause-api", port = 8000 }
  }

  shared_env = {
    CLOUDCAUSE_DATA_MODE         = var.data_mode
    CLOUDCAUSE_AGENT_MODE        = var.agent_mode
    CLOUDCAUSE_ORCHESTRATOR_MODE = "http"
    CLOUDCAUSE_WORKER_MODE       = "http"
    CLOUDCAUSE_HOST              = "0.0.0.0"
  }
}

resource "google_cloud_run_v2_service" "service" {
  for_each = local.services

  name     = each.key
  location = var.region
  ingress  = "INGRESS_TRAFFIC_ALL"

  template {
    containers {
      image   = var.image
      command = [each.value.command]

      ports {
        container_port = each.value.port
      }

      dynamic "env" {
        for_each = local.shared_env
        content {
          name  = env.key
          value = env.value
        }
      }

      resources {
        limits = {
          cpu    = "1"
          memory = "1Gi"
        }
      }
    }

    scaling {
      min_instance_count = 0
      max_instance_count = 2
    }
  }
}

output "service_urls" {
  description = "Public URLs for the deployed services."
  value       = { for name, service in google_cloud_run_v2_service.service : name => service.uri }
}
