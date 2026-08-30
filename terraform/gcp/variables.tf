variable "project_id" {
  description = "GCP project that owns the registry and both Cloud Run services."
  type        = string
}

variable "region" {
  description = "Region for Artifact Registry and both Cloud Run services."
  type        = string
  default     = "us-central1"
}

variable "name_prefix" {
  description = "Prefix for created resources. Deploys <prefix>-api and <prefix>-web."
  type        = string
  default     = "cloudcause"
}

variable "environment" {
  description = "Value of the environment label on every resource."
  type        = string
  default     = "demo"
}

variable "repository_name" {
  description = "Artifact Registry repository holding the api and web images."
  type        = string
  default     = "cloudcause"
}

variable "image_tag" {
  description = "Tag for both images. Required and deliberately without a default: Cloud Run compares the image string, so re-pushing a reused tag deploys nothing and leaves no way to tell which build is live. Use a git sha or a version."
  type        = string

  validation {
    condition     = var.image_tag != "latest"
    error_message = "Use an immutable tag such as a git sha; \"latest\" cannot identify a revision."
  }
}

# Both optional. With neither set the deployment serves the deterministic
# playbooks and the UI does not offer live AI agents. These land in the Cloud Run
# revision as plain environment variables and in terraform.tfstate.
variable "openai_api_key" {
  description = "Drives the AWS (Strands) and Azure (Agent Framework) agents."
  type        = string
  sensitive   = true
  default     = ""
}

variable "google_api_key" {
  description = "AI Studio key driving the GCP (ADK) agent and the report summary."
  type        = string
  sensitive   = true
  default     = ""
}

variable "agent_mode" {
  description = "Default agent mode for clients that omit it. The UI still chooses per investigation."
  type        = string
  default     = "stub"

  validation {
    condition     = contains(["stub", "live"], var.agent_mode)
    error_message = "agent_mode must be \"stub\" or \"live\"."
  }
}

variable "max_agent_calls" {
  description = "Live-agent call budget for one whole investigation, shared by every provider's agent rather than granted per provider. The three specialists run concurrently and draw from the same pool, so a number sized for one provider lets the first agent exhaust it and forces the other two back to the deterministic playbooks. Sized here for the three-provider default with retry headroom."
  type        = number
  default     = 48

  validation {
    condition     = var.max_agent_calls > 0 && floor(var.max_agent_calls) == var.max_agent_calls
    error_message = "max_agent_calls must be a positive integer."
  }
}

variable "uploads_enabled" {
  description = "Whether /api/v1/datasets accepts uploads. On by default: a cost investigator that cannot read a bill demonstrates nothing, and the demo's whole point is the Your data flow. The gateway is unauthenticated, so anyone reaching it can post a file — bounded by the fact that uploaded bytes are parsed from the request stream and discarded, with no row value logged and nothing raw written to disk (tests/security enforces this), and by the size, row, and TTL caps in packages/contracts. Set false for a deployment where even that is too much."
  type        = bool
  default     = true
}

variable "api_min_instances" {
  description = "Warm gateway instances. Zero by default: with cpu_idle = false a warm instance bills 2 vCPU and 2 GiB continuously whether or not anyone visits, which is roughly $100/month for a demo that is idle almost all of the time. The cost of zero is a cold start on the first visit after a quiet period, which is dominated by pulling the image rather than by starting the process: the gateway reaches /health in about 4 seconds once running. Set 1 while actively sharing the link."
  type        = number
  default     = 0
}

variable "api_max_instances" {
  description = "Gateway ceiling. History and rate limits are per-instance memory here, so keep at 1 without a shared database and Redis."
  type        = number
  default     = 1
}

variable "api_memory" {
  description = "Gateway memory. One container runs the gateway, orchestrator, both workers, and spawns MCP child processes."
  type        = string
  default     = "2Gi"
}

variable "web_max_instances" {
  description = "Frontend ceiling. The web service is stateless."
  type        = number
  default     = 4
}
