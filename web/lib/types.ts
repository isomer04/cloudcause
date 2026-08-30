/**
 * Mirror of the gateway's Pydantic contracts (packages/contracts).
 *
 * These are read models only. The frontend never derives a cost figure, a
 * percentage, or a confidence score; it formats what the gateway computed.
 */

export type Provider = "aws" | "azure" | "gcp";
export type Risk = "low" | "medium" | "high";
export type InvestigationStatus = "queued" | "running" | "completed" | "failed";
export type WorkerStatus = "ok" | "partial" | "failed" | "skipped";
export type DataMode = "fixtures" | "live";
export type AgentMode = "stub" | "live";

/**
 * Where a number came from. `upload` is data a human handed CloudCause: real, but
 * unverified. It must never be rendered as `live`.
 */
export type DataOrigin = "fixture" | "upload" | "live";

export type DatasetSourceKind = "cost" | "metrics" | "audit" | "inventory" | "recommendations";
export type Dimension = "service" | "region" | "account" | "resource" | "tag_owner";

export interface DateRange {
  start: string;
  end: string;
}

export interface InvestigationRequest {
  providers: Provider[];
  start_date: string;
  end_date: string;
  comparison_start_date: string;
  comparison_end_date: string;
  account_ids: string[];
  question: string;
  scenario_id: string;
  dataset_id: string | null;
  agent_mode: AgentMode;
}

export interface ScenarioSummary {
  id: string;
  title: string;
  providers: Provider[];
  category: string;
  suggested_request: InvestigationRequest;
}

export interface Provenance {
  provider: Provider;
  source: string;
  observed_at: string;
  retrieved_at: string;
  data_through: string;
  origin: DataOrigin;
  /** @deprecated read `origin`; kept only while the gateway still sends it. */
  is_fixture: boolean;
  schema_version: string;
  query_reference: string | null;
}

export interface Evidence {
  evidence_id: string;
  provider: Provider;
  source_type: string;
  source_id: string;
  observed_at: string;
  statement: string;
  numeric_value: number | null;
  numeric_unit: string | null;
  query_reference: string | null;
  data_through: string | null;
  origin: DataOrigin;
  /** @deprecated read `origin`. */
  is_fixture: boolean;
  contains_untrusted_text: boolean;
}

export interface RuleCitation {
  rule_id: string;
  provider: Provider | "focus";
  rule_type: string;
  service: string | null;
  schema_version: string;
  valid_from: string | null;
  valid_to: string | null;
  reviewed_at: string | null;
  source_url: string;
  source_updated_at: string | null;
  confidence: string;
  is_stale: boolean;
  selected_for_date: string | null;
}

export interface Finding {
  finding_id: string;
  provider: Provider;
  category: string;
  suspected_root_cause: string;
  affected_resources: string[];
  evidence: Evidence[];
  confidence: number;
  actual_cost_increase: number;
  estimated_monthly_impact: number;
  recommendation: string;
  risk: Risk;
  requires_human_approval: boolean;
  candidate_id: string | null;
  service_name: string | null;
  region_id: string | null;
  applied_rules: RuleCitation[];
  is_uncertain: boolean;
  warnings: string[];
  agent_mode: AgentMode;
}

export interface ProviderStatus {
  provider: Provider;
  status: WorkerStatus;
  message: string;
  data_through: string | null;
  origin: DataOrigin;
  /** @deprecated read `origin`. */
  is_fixture: boolean;
  finding_count: number;
  duration_seconds: number;
  agent_mode: AgentMode;
}

export interface DailyTotal {
  usage_date: string;
  billed_cost: number;
  effective_cost: number;
}

export interface AnalyticsConfig {
  min_absolute_change: number;
  min_percent_change: number;
  reconciliation_tolerance: number;
  max_candidates_per_provider: number;
  currency: string;
}

export interface AnomalyCandidate {
  candidate_id: string;
  provider: Provider;
  dimension: Dimension;
  key: string;
  billing_account_id: string | null;
  service_name: string | null;
  service_category: string | null;
  region_id: string | null;
  resource_id: string | null;
  resource_name: string | null;
  sku_ids: string[];
  tags: Record<string, string>;
  baseline_cost: number;
  current_cost: number;
  expected_baseline_cost: number;
  absolute_change: number;
  percent_change: number | null;
  baseline_daily_average: number;
  current_daily_average: number;
  baseline_quantity: number;
  current_quantity: number;
  quantity_percent_change: number | null;
  unit_cost_baseline: number | null;
  unit_cost_current: number | null;
  first_spike_date: string | null;
  is_new: boolean;
  currency: string;
}

export interface Reconciliation {
  total_change: number;
  attributed_change: number;
  unattributed_change: number;
  tolerance: number;
  within_tolerance: boolean;
  note: string;
}

export interface ProviderComparison {
  provider: Provider;
  current_period: DateRange;
  baseline_period: DateRange;
  current_cost: number;
  baseline_cost: number;
  expected_baseline_cost: number;
  absolute_change: number;
  percent_change: number | null;
  daily_current: DailyTotal[];
  daily_baseline: DailyTotal[];
  candidates: AnomalyCandidate[];
  reconciliation: Reconciliation;
  currency: string;
}

export interface PeriodComparison {
  current_period: DateRange;
  baseline_period: DateRange;
  config: AnalyticsConfig;
  providers: ProviderComparison[];
  total_current_cost: number;
  total_baseline_cost: number;
  total_absolute_change: number;
  total_percent_change: number | null;
  reconciliation: Reconciliation | null;
}

export interface KnowledgeProvenance {
  focus_version: string;
  knowledge_schema_version: string;
  rule_ids: string[];
  oldest_review_date: string | null;
  newest_review_date: string | null;
  stale_rule_ids: string[];
  review_max_age_days: number;
}

export interface ValidationIssue {
  code: string;
  severity: "info" | "warning" | "error";
  detail: string;
  finding_id: string | null;
  provider: Provider | null;
}

export interface ProviderTask {
  provider: Provider;
  question: string;
  candidate_ids: string[];
  focus_areas: string[];
  must_explain: string[];
  max_findings: number;
}

export interface InvestigationPlan {
  investigation_id: string;
  question: string;
  created_at: string;
  current_period: DateRange;
  baseline_period: DateRange;
  tasks: ProviderTask[];
  deterministic_summary: string;
  rationale: string;
  planner_mode: "deterministic" | "live";
}

export interface InvestigationReport {
  investigation_id: string;
  contract_version: string;
  question: string;
  request: InvestigationRequest;
  plan: InvestigationPlan | null;
  generated_at: string;
  current_period: DateRange;
  baseline_period: DateRange;
  total_current_cost: number;
  total_baseline_cost: number;
  total_absolute_change: number;
  total_percent_change: number | null;
  currency: string;
  comparison: PeriodComparison | null;
  findings: Finding[];
  provider_statuses: ProviderStatus[];
  reconciliation: Reconciliation | null;
  validation_issues: ValidationIssue[];
  warnings: string[];
  sources: Provenance[];
  knowledge: KnowledgeProvenance | null;
  data_mode: DataMode;
  data_origin: DataOrigin;
  agent_mode: AgentMode;
  summary: string;
}

export interface InvestigationState {
  investigation_id: string;
  status: InvestigationStatus;
  question: string;
  created_at: string;
  updated_at: string;
  request: InvestigationRequest;
  provider_statuses: ProviderStatus[];
  stage: string;
  message: string;
  report: InvestigationReport | null;
  error: string | null;
}

export interface ProgressEvent {
  investigation_id: string;
  sequence: number;
  at: string;
  stage: string;
  status: "started" | "progress" | "completed" | "failed";
  provider: Provider | null;
  message: string;
  data: Record<string, unknown>;
}

export interface InvestigationCreated {
  investigation_id: string;
  status: InvestigationStatus;
  headline: string;
  state: InvestigationState;
}

export interface GatewayHealth {
  status: string;
  contract_version: string;
  data_mode: DataMode;
  /** Only the fallback for clients that omit `agent_mode`; it gates nothing. */
  default_agent_mode: AgentMode;
  /** Every investigation explicitly chooses its deterministic or live path. */
  agent_mode_selection: "per_investigation";
  supported_agent_modes: AgentMode[];
  /** Whether a model key is configured, and so whether live runs are possible here. */
  live_agents_available: boolean;
  orchestrator: Record<string, unknown>;
  history: Record<string, unknown>;
  datasets: Record<string, unknown>;
  rate_limiter: Record<string, unknown>;
  read_only: boolean;
}

/* ------------------------------------------------------------ your own data */

export interface DatasetRowRejection {
  row_number: number;
  code: string;
  detail: string;
}

export interface DatasetSourceSummary {
  provider: Provider;
  kind: DatasetSourceKind;
  detected_format: string;
  received_at: string;
  raw_rows: number;
  accepted_rows: number;
  rejected_rows: number;
  stored_records: number;
  period_start: string | null;
  period_end: string | null;
  data_through: string | null;
  data_through_note: string;
  currency: string | null;
  byte_size: number;
  compressed: boolean;
}

export interface DatasetIngestReport {
  dataset_id: string;
  expires_at: string;
  sealed: boolean;
  source: DatasetSourceSummary;
  rejections: DatasetRowRejection[];
  warnings: string[];
  total_records: number;
  source_count: number;
}

export interface DatasetSummary {
  dataset_id: string;
  created_at: string;
  expires_at: string;
  sealed: boolean;
  sealed_at: string | null;
  providers: Provider[];
  sources: DatasetSourceSummary[];
  currency: string | null;
  total_records: number;
  period_start: string | null;
  period_end: string | null;
  data_through: string | null;
  available_source_types: Partial<Record<Provider, string[]>>;
  warnings: string[];
  /** A brief over the period the data actually covers, computed by the gateway. */
  suggested_request: InvestigationRequest | null;
}

export interface DatasetCreated {
  dataset_id: string;
  created_at: string;
  expires_at: string;
  max_bytes_per_file: number;
  max_rows_per_file: number;
  max_sources: number;
  max_records: number;
  accepted_content_types: string[];
  source_kinds: string[];
}
