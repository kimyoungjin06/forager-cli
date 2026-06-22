import {
  severityClasses,
  severityLabels,
  type Severity,
} from './reviewSurface';

type HealthStatus = 'ok' | 'attention' | 'blocked' | 'critical' | 'unknown';

export type HealthItem = {
  id: string;
  label: string;
  status: HealthStatus;
  summary: string;
};

export type ProjectRow = {
  project_key: string;
  display_name: string;
  severity: Severity;
  plan: string;
  runtime: string;
  decisions: number;
  closeout: string;
  truth: string;
  last_activity: string;
  task_items: ProjectTaskItem[];
};

export type AssistantPrompt = {
  label: string;
  prompt: string;
};

export type AssistantRef = {
  label: string;
  reference: string;
  trust: 'receipt-backed' | 'state-ref' | 'inference';
};

export type AssistantAction = {
  label: string;
  kind: string;
  boundary: string;
  target_ref: string;
  command: string;
};

export type ChatContextSurface = {
  schema: string;
  status: string;
  mode: string;
  summary: string;
  actionPolicy: string;
  sourceRefs: SourceRef[];
  scopes: ChatContextScope[];
};

export type ChatContextScope = {
  scope_id: string;
  label: string;
  kind: string;
  answer: string;
  confidence: string;
  citations: AssistantRef[];
  inference_notes: string[];
  suggested_actions: AssistantAction[];
  prompts: AssistantPrompt[];
};

export type ProjectDetail = {
  project_key: string;
  display_name: string;
  severity: Severity;
  status_line: string;
  last_activity: string;
  plan: string;
  runtime: string;
  closeout: string;
  truth: string;
  attention_count: number;
  open_decision_count: number;
  runtime_handoff_count: number;
  truth_recovery_count: number;
  primary_action: string;
  attention_items: ProjectAttentionItem[];
  task_items: ProjectTaskItem[];
  decision_titles: string[];
  runtime_stages: string[];
  truth_recovery_stages: string[];
  graph_title: string;
  graph_edges: GraphEdge[];
  assistant_context: string;
  assistant_answer: string;
  assistant_prompts: AssistantPrompt[];
  assistant_refs: AssistantRef[];
  assistant_suggested_actions: AssistantAction[];
  assistant_inference_notes: string[];
};

export type ProjectAttentionItem = {
  kind: string;
  title: string;
  severity: Severity;
  summary: string;
  status: string;
  reference: string;
  command: string;
  boundary: string;
};

export type ProjectTaskItem = {
  kind: string;
  title: string;
  status: string;
  summary: string;
  reference: string;
  command: string;
  inspection_items: ProjectTaskInspectionItem[];
  receipt_links: ProjectTaskReceiptLink[];
  task_id?: string;
  next_safe_action_kind?: string;
  requires_operator_review?: boolean;
};

export type ProjectTaskReceiptLink = {
  source: string;
  schema: string;
  record_id: string;
  result_status: string;
  recorded_at: string;
  summary: string;
};

export type ProjectTaskInspectionItem = {
  label: string;
  value: string;
  tone: 'neutral' | 'muted' | 'attention' | 'success' | 'danger';
};

export type DecisionItem = {
  decision_id: string;
  kind: string;
  severity: Severity;
  status: string;
  materiality: string;
  raised_by: string;
  project_key: string;
  title: string;
  what_changed: string;
  why_now: string;
  risk: string;
  evidence_refs: EvidenceRef[];
  allowed_actions: string[];
  action_envelopes: ActionEnvelopePreview[];
  recommendation: string;
  default_if_no_reply: string;
  authorization_boundary: string;
  stale_guard: string;
  receipt_ref: string;
  cli_fallback: string;
  updated_at: string;
};

export type EvidenceRef = {
  kind: string;
  label: string;
  reference: string;
};

export type ActionTargetRef = {
  kind: string;
  decision_id: string;
  status: string;
  updated_at: string;
};

export type ActionEnvelopePreview = {
  schema: string;
  action_id: string;
  action_kind: string;
  profile: string;
  project_key: string;
  target_ref: ActionTargetRef;
  observed_hash: string;
  nonce: string;
  ttl: string;
  issued_at: string;
  expires_at: string;
  idempotency_key: string;
  preview: string;
  allowed_command: string;
  forbidden_effects: string[];
  expected_receipt_schema: string;
  requires_confirmation: boolean;
  confirmation_phrase: string;
  stale_rejection_reason: string;
  receipt_history_count: number;
  latest_receipt: ActionEnvelopeReceiptSummary | null;
  execution_history_count: number;
  latest_execution: DecisionActionExecutionSummary | null;
};

export type ActionEnvelopeReceiptSummary = {
  schema: string;
  receipt_id: string;
  processed_at: string;
  result_status: string;
  stale: boolean;
  reason: string;
  current_hash: string;
  failed_checks: string[];
};

export type DecisionActionExecutionSummary = {
  schema: string;
  execution_id: string;
  preflight_id: string;
  executed_at: string;
  result_status: string;
  decision: string;
  decision_appended: boolean;
  mutation_allowed_by_this_command: boolean;
  reason: string;
  handoff_id: string;
  closeout_command: string;
  failed_checks: string[];
};

export type DecisionInboxView = {
  schema: string;
  status: string;
  openCount: number;
  visibleCount: number;
  summary: string;
  emptyTitle: string;
  emptySummary: string;
  emptyCliFallback: string;
  actionMode: string;
  directInputAllowed: boolean;
  mutationPolicy: string;
  receiptPolicy: string;
  items: DecisionItem[];
};

export type RuntimeDispatchView = {
  schema: string;
  status: string;
  candidateCount: number;
  visibleCount: number;
  summary: string;
  emptyTitle: string;
  emptySummary: string;
  emptyCliFallback: string;
  items: RuntimeDispatchItem[];
};

export type RuntimeDispatchItem = {
  project_key: string;
  decision_id: string;
  title: string;
  stage: string;
  severity: Severity;
  closeout_id: string;
  execution_id: string;
  receipt_id: string;
  handoff_id: string;
  latest_preflight: RuntimeDispatchPreflightSummary | null;
  latest_receipt: RuntimeDispatchReceiptSummary | null;
  preflight_command: string;
  dispatch_command: string;
  tick_command: string;
  boundary: string;
};

export type RuntimeDispatchPreflightSummary = {
  schema: string;
  preflight_id: string;
  processed_at: string;
  result_status: string;
  reason: string;
};

export type RuntimeDispatchReceiptSummary = {
  schema: string;
  receipt_id: string;
  preflight_id: string;
  recorded_at: string;
  result_status: string;
  task_id: string;
  reason: string;
};

export type AcceptedTruthRecoveryView = {
  schema: string;
  status: string;
  candidateCount: number;
  visibleCount: number;
  summary: string;
  emptyTitle: string;
  emptySummary: string;
  emptyCliFallback: string;
  items: AcceptedTruthRecoveryItem[];
};

export type AcceptedTruthRecoveryItem = {
  project_key: string;
  closeout_id: string;
  review_id: string;
  receipt_id: string;
  acceptance_status: string;
  verification_status: string;
  stage: string;
  severity: Severity;
  open_decision_count: number;
  open_decision_kinds: string[];
  evidence_status: string;
  retention_review: string;
  wiki_promotion_state: string;
  stale_task_count: number;
  next_safe_action: string;
  artifact_dir: string;
  reviewed_at: string;
  resolve_command: string;
  retire_command: string;
  action_envelopes: AcceptedTruthRecoveryActionEnvelopePreview[];
  boundary: string;
};

export type AcceptedTruthRecoveryTargetRef = {
  kind: string;
  closeout_id: string;
  review_id: string;
  receipt_id: string;
  acceptance_status: string;
  reviewed_at: string;
};

export type AcceptedTruthRecoveryActionEnvelopePreview = {
  schema: string;
  action_id: string;
  action_kind: string;
  profile: string;
  project_key: string;
  target_ref: AcceptedTruthRecoveryTargetRef;
  observed_hash: string;
  nonce: string;
  ttl: string;
  issued_at: string;
  expires_at: string;
  idempotency_key: string;
  preview: string;
  allowed_command: string;
  forbidden_effects: string[];
  expected_receipt_schema: string;
  requires_confirmation: boolean;
  confirmation_phrase: string;
  stale_rejection_reason: string;
  receipt_history_count: number;
  latest_receipt: ActionEnvelopeReceiptSummary | null;
};

export type GraphNode = {
  id: string;
  label: string;
  kind: string;
};

export type GraphEdge = {
  from: string;
  to: string;
  label: string;
};

export type SourceRef = {
  label: string;
  reference: string;
};

export type DashboardView = {
  sourceLabel: string;
  workstationId: string;
  profile: string;
  generatedAt: string;
  staleLabel: string;
  workspaceRoots: string[];
  sourceRefs: SourceRef[];
  topTitle: string;
  topSummary: string;
  topSeverity: Severity;
  topAction: string;
  nextActionLabel: string;
  nextActionReason: string;
  nextActionCommand: string;
  attentionCounts: { label: string; value: number }[];
  health: HealthItem[];
  capacity: { label: string; value: number }[];
  projects: ProjectRow[];
  projectDetails: ProjectDetail[];
  decisionInbox: DecisionInboxView;
  runtimeDispatch: RuntimeDispatchView;
  acceptedTruthRecovery: AcceptedTruthRecoveryView;
  chatContext: ChatContextSurface;
  decisions: DecisionItem[];
  graphTitle: string;
  graphNodes: GraphNode[];
  graphEdges: GraphEdge[];
};

const FALLBACK_BOUNDARY = 'No project files are mutated from this dashboard.';
const KNOWN_SEVERITIES = new Set(['ok', 'info', 'attention', 'blocked', 'critical']);

export { severityClasses, severityLabels };

export function dashboardViewFromSurface(surface: unknown): DashboardView {
  if (!isRecord(surface) || surface.schema !== 'workstation_surface.v1') {
    throw new Error('expected workstation_surface.v1');
  }

  const top = recordAt(surface, 'top_attention');
  const nextAction = firstRecord(arrayAt(surface, 'next_safe_actions'));
  const graph = recordAt(surface, 'graph_focus');
  const stale = recordAt(surface, 'stale_state');
  const decisionInbox = decisionInboxView(recordAt(surface, 'decision_inbox'), arrayAt(surface, 'decisions'));
  const runtimeDispatch = runtimeDispatchView(recordAt(surface, 'runtime_dispatch'));
  const acceptedTruthRecovery = acceptedTruthRecoveryView(recordAt(surface, 'accepted_truth_recovery'));
  const projects = projectRows(arrayAt(surface, 'projects'));
  const workstationId = fallbackString(stringAt(surface, 'workstation_id'), 'workstation');
  const profile = fallbackString(stringAt(surface, 'profile'), 'default');
  const topTitle = fallbackString(stringAt(top, 'title'), 'No top attention item');
  const chatContext = chatContextViewFromSurface(recordAt(surface, 'chat_context'), {
    workstationId,
    profile,
    topTitle,
    decisionInbox,
    runtimeDispatch,
    acceptedTruthRecovery,
    projects,
  });
  const projectDetails = projectDetailsFromSurface(
    projects,
    decisionInbox,
    runtimeDispatch,
    acceptedTruthRecovery,
    chatContext,
  );

  return {
    sourceLabel: fallbackString(stringAt(surface, 'source_label'), 'workstation_surface.v1'),
    workstationId,
    profile,
    generatedAt: fallbackString(stringAt(surface, 'generated_at'), '-'),
    staleLabel: fallbackString(stringAt(stale, 'status'), 'unknown'),
    workspaceRoots: stringArrayAt(surface, 'workspace_roots'),
    sourceRefs: sourceRefsFromRecord(recordAt(surface, 'source_refs')),
    topTitle,
    topSummary: fallbackString(
      stringAt(top, 'summary'),
      'No urgent operator attention was reported by the workstation surface.',
    ),
    topSeverity: normalizeSeverity(stringAt(top, 'severity')),
    topAction: fallbackString(stringAt(top, 'action_label'), 'Open decisions'),
    nextActionLabel: fallbackString(stringAt(nextAction, 'label'), 'Review dashboard'),
    nextActionReason: fallbackString(
      stringAt(nextAction, 'reason'),
      'Inspect current state before taking action.',
    ),
    nextActionCommand: stringAt(nextAction, 'command'),
    attentionCounts: positiveEntriesFromRecord(recordAt(surface, 'attention_counts')),
    health: healthItems(arrayAt(surface, 'health')),
    capacity: entriesFromRecord(recordAt(surface, 'capacity')),
    projects,
    projectDetails,
    decisionInbox,
    runtimeDispatch,
    acceptedTruthRecovery,
    chatContext,
    decisions: decisionInbox.items,
    graphTitle: fallbackString(stringAt(graph, 'title'), 'Scoped provenance'),
    graphNodes: graphNodes(arrayAt(graph, 'nodes')),
    graphEdges: graphEdges(arrayAt(graph, 'edges')),
  };
}

export function formatLabel(label: string): string {
  return label.replaceAll('_', ' ');
}

export function statusClasses(status: HealthStatus): string {
  switch (status) {
    case 'ok':
      return 'border-emerald-400/45 bg-emerald-400/10 text-emerald-100';
    case 'attention':
      return 'border-brand-400/45 bg-brand-400/10 text-brand-100';
    case 'blocked':
      return 'border-amber-300/55 bg-amber-300/10 text-amber-100';
    case 'critical':
      return 'border-red-400/60 bg-red-400/10 text-red-100';
    default:
      return 'border-slate-700 bg-slate-900/70 text-slate-200';
  }
}

export function dashboardAssistantContext(view: DashboardView): string {
  const scope = dashboardAssistantScope(view);
  if (scope) {
    return scope.answer;
  }
  if (view.decisionInbox.openCount > 0) {
    return `${view.workstationId} has ${view.decisionInbox.openCount} open decision item(s). Start from "${view.topTitle}", cite the decision inbox and capacity state, and mark anything beyond the surface as inference.`;
  }
  if (view.acceptedTruthRecovery.visibleCount > 0) {
    return `${view.workstationId} has accepted-truth recovery work visible. Separate closeout evidence, follow-up state, and accepted-truth recording before proposing any action card.`;
  }
  if (view.runtimeDispatch.visibleCount > 0) {
    return `${view.workstationId} has post-closeout runtime handoffs visible. Distinguish queueing, launch, and monitoring; do not treat this assistant entry as runtime authorization.`;
  }

  return `${view.workstationId} has no open decision item in the current surface. Summarize health, capacity, and project state, and call out stale or inferred claims explicitly.`;
}

export function dashboardAssistantAnswer(view: DashboardView): string {
  return dashboardAssistantContext(view);
}

export function dashboardAssistantPrompts(view: DashboardView): AssistantPrompt[] {
  const scope = dashboardAssistantScope(view);
  if (scope?.prompts.length) {
    return scope.prompts.slice(0, 3);
  }
  const prompts: AssistantPrompt[] = [
    {
      label: 'What needs attention?',
      prompt: `Summarize the current Forager workstation state from workstation_surface.v1 for profile ${view.profile}. Cite top_attention, decision_inbox, capacity, and health refs; mark any inference.`,
    },
    {
      label: 'Explain top item',
      prompt: `Explain why "${view.topTitle}" needs attention. Include the risk, next safe action, and which receipt or state refs must be checked before action.`,
    },
  ];

  if (view.decisionInbox.openCount > 0) {
    prompts.push({
      label: 'Draft manager brief',
      prompt: `Draft a concise manager brief for ${view.decisionInbox.openCount} open decision item(s), grouping by project and stating what is blocked without executing anything.`,
    });
  } else {
    prompts.push({
      label: 'Check readiness',
      prompt: `Review workstation readiness for ${view.workstationId}: summarize health, capacity, workspace roots, and stale-state risks without proposing direct execution.`,
    });
  }

  return prompts.slice(0, 3);
}

export function dashboardAssistantRefs(view: DashboardView): AssistantRef[] {
  const scope = dashboardAssistantScope(view);
  if (scope?.citations.length) {
    return scope.citations.slice(0, 4);
  }
  const refs: AssistantRef[] = [
    {
      label: 'Top attention',
      reference: 'workstation_surface.v1#top_attention',
      trust: 'state-ref',
    },
    {
      label: 'Decision inbox',
      reference: view.decisionInbox.schema,
      trust: 'state-ref',
    },
    {
      label: 'Capacity',
      reference: 'workstation_surface.v1#capacity',
      trust: 'state-ref',
    },
  ];

  const decision = view.decisions[0];
  if (decision) {
    refs.push({
      label: 'Leading decision',
      reference: `decision:${decision.decision_id}`,
      trust: decision.receipt_ref ? 'receipt-backed' : 'state-ref',
    });
  }

  return refs.slice(0, 4);
}

export function dashboardAssistantActions(view: DashboardView): AssistantAction[] {
  const scope = dashboardAssistantScope(view);
  if (scope?.suggested_actions.length) {
    return scope.suggested_actions.slice(0, 2);
  }
  if (view.decisionInbox.openCount > 0) {
    return [{
      label: 'Open decision inbox',
      kind: 'open_surface',
      boundary: 'Proposal only; no decision is applied from the assistant panel.',
      target_ref: view.decisionInbox.schema,
      command: 'forager offdesk decisions --json',
    }];
  }
  if (view.acceptedTruthRecovery.visibleCount > 0) {
    return [{
      label: 'Review truth recovery',
      kind: 'open_surface',
      boundary: 'Proposal only; accepted truth requires review and receipt.',
      target_ref: view.acceptedTruthRecovery.schema,
      command: 'forager ondesk review-surface --json',
    }];
  }
  return [{
    label: 'Review dashboard',
    kind: 'inspect_state',
    boundary: 'Proposal only; inspect current state before starting new work.',
    target_ref: 'workstation_surface.v1',
    command: 'forager status --json',
  }];
}

function dashboardAssistantScope(view: DashboardView): ChatContextScope | null {
  return view.chatContext.scopes.find((scope) => scope.scope_id === 'overview')
    ?? view.chatContext.scopes[0]
    ?? null;
}

export function decisionAssistantScope(view: DashboardView, decision: DecisionItem): ChatContextScope {
  return view.chatContext.scopes.find((scope) => scope.scope_id === `decision:${decision.decision_id}`)
    ?? synthesizedDecisionAssistantScope(decision);
}

function synthesizedDecisionAssistantScope(decision: DecisionItem): ChatContextScope {
  const latestReceipt = decision.action_envelopes.find((envelope) => envelope.latest_receipt)?.latest_receipt;
  const latestExecution = decision.action_envelopes.find((envelope) => envelope.latest_execution)?.latest_execution;
  const answer = latestReceipt?.stale
    ? `"${decision.title}" should not advance from the current receipt. The latest action receipt is stale, so refresh the decision state and regenerate the envelope before any execution path.`
    : latestExecution
      ? `"${decision.title}" has latest execution status ${latestExecution.result_status}. Verify the execution receipt, closeout or handoff refs, and stale guard before treating the decision as resolved.`
      : latestReceipt
        ? `"${decision.title}" has a non-stale action receipt with status ${latestReceipt.result_status}. It can be discussed as validated preview state, but execution still requires the preflight and receipt path outside this assistant panel.`
        : `"${decision.title}" is still awaiting operator judgment. Explain risk and options from the decision record, then validate an action envelope before any state change.`;
  const citations: AssistantRef[] = [
    {
      label: 'Decision',
      reference: `decision:${decision.decision_id}`,
      trust: 'state-ref',
    },
    {
      label: 'Decision inbox',
      reference: 'decision_inbox_surface.v1',
      trust: 'state-ref',
    },
    ...decision.evidence_refs.slice(0, 2).map((evidence) => ({
      label: `Evidence: ${evidence.label}`,
      reference: evidence.reference,
      trust: 'state-ref' as const,
    })),
  ];
  if (decision.receipt_ref) {
    citations.push({
      label: 'Decision receipt',
      reference: decision.receipt_ref,
      trust: 'receipt-backed',
    });
  }
  if (latestReceipt) {
    citations.push({
      label: 'Latest action receipt',
      reference: latestReceipt.receipt_id,
      trust: 'receipt-backed',
    });
  }
  if (latestExecution) {
    citations.push({
      label: 'Latest execution',
      reference: latestExecution.execution_id,
      trust: 'receipt-backed',
    });
  }

  return {
    scope_id: `decision:${decision.decision_id}`,
    label: decision.title,
    kind: 'decision',
    answer,
    confidence: 'surface-derived',
    citations,
    inference_notes: [
      'Synthesized from workstation_surface.v1 because a decision chat scope was not present.',
    ],
    suggested_actions: [decisionAssistantAction(decision, latestReceipt, latestExecution)],
    prompts: [
      {
        label: 'Can this advance?',
        prompt: `Explain whether "${decision.title}" can advance. Cite the decision record, stale guard, latest receipt or execution, and mark any inference.`,
      },
      {
        label: 'Explain risk',
        prompt: `Explain the risk and safe next step for decision ${decision.decision_id} without executing any action.`,
      },
    ],
  };
}

function decisionAssistantAction(
  decision: DecisionItem,
  latestReceipt: ActionEnvelopeReceiptSummary | null | undefined,
  latestExecution: DecisionActionExecutionSummary | null | undefined,
): AssistantAction {
  if (latestReceipt?.stale) {
    return {
      label: 'Refresh decision envelope',
      kind: 'inspect_state',
      boundary: 'Proposal only; stale receipts must be rejected before any execution path.',
      target_ref: `decision:${decision.decision_id}`,
      command: decision.cli_fallback,
    };
  }
  if (latestExecution) {
    return {
      label: 'Review execution receipt',
      kind: 'inspect_receipt',
      boundary: 'Proposal only; execution result must be reviewed before closeout or runtime handoff.',
      target_ref: latestExecution.execution_id,
      command: latestExecution.closeout_command || decision.cli_fallback,
    };
  }
  if (latestReceipt) {
    return {
      label: 'Run action preflight',
      kind: 'inspect_receipt',
      boundary: 'Proposal only; preflight and execution remain outside the assistant panel.',
      target_ref: latestReceipt.receipt_id,
      command: `forager ondesk action-preflight --receipt-id ${latestReceipt.receipt_id} --json`,
    };
  }
  return {
    label: 'Validate action envelope',
    kind: 'inspect_state',
    boundary: 'Proposal only; validation must produce an action_envelope_receipt.v1 before execution.',
    target_ref: `decision:${decision.decision_id}`,
    command: decision.action_envelopes[0]?.allowed_command || decision.cli_fallback,
  };
}

type ChatContextFallback = {
  workstationId: string;
  profile: string;
  topTitle: string;
  decisionInbox: DecisionInboxView;
  runtimeDispatch: RuntimeDispatchView;
  acceptedTruthRecovery: AcceptedTruthRecoveryView;
  projects: ProjectRow[];
};

function chatContextViewFromSurface(
  surface: Record<string, unknown>,
  fallback: ChatContextFallback,
): ChatContextSurface {
  const scopes = chatContextScopes(arrayAt(surface, 'scopes'));
  if (fallbackString(stringAt(surface, 'schema')) === 'chat_context_surface.v1' && scopes.length > 0) {
    return {
      schema: 'chat_context_surface.v1',
      status: fallbackString(stringAt(surface, 'status'), 'unknown'),
      mode: fallbackString(stringAt(surface, 'mode'), 'read_only_cited_answer'),
      summary: fallbackString(
        stringAt(surface, 'summary'),
        'Assistant scopes cite state or receipt refs and cannot execute actions.',
      ),
      actionPolicy: fallbackString(
        stringAt(surface, 'action_policy'),
        'Suggested actions are review cards only.',
      ),
      sourceRefs: sourceRefsFromRecord(recordAt(surface, 'source_refs')),
      scopes,
    };
  }

  return synthesizedChatContext(fallback);
}

function synthesizedChatContext(fallback: ChatContextFallback): ChatContextSurface {
  const openDecisionCount = fallback.decisionInbox.openCount;
  const answer = openDecisionCount > 0
    ? `${fallback.workstationId} has ${openDecisionCount} open decision item(s). Start from "${fallback.topTitle}". This assistant only explains, drafts, and cites refs; execution still requires action envelopes and receipts.`
    : fallback.acceptedTruthRecovery.visibleCount > 0
      ? `${fallback.workstationId} has accepted-truth recovery work visible. Review closeout evidence and follow-up state before treating output as accepted truth.`
      : fallback.runtimeDispatch.visibleCount > 0
        ? `${fallback.workstationId} has runtime handoffs visible. Distinguish queueing, launch, and monitoring before proposing a runtime action.`
        : `${fallback.workstationId} has no open decision item in the current surface. Summarize health, capacity, and project state, and mark inference explicitly.`;
  const overviewScope: ChatContextScope = {
    scope_id: 'overview',
    label: 'Workstation',
    kind: 'overview',
    answer,
    confidence: 'surface-derived',
    citations: [
      {
        label: 'Top attention',
        reference: 'workstation_surface.v1#top_attention',
        trust: 'state-ref',
      },
      {
        label: 'Decision inbox',
        reference: fallback.decisionInbox.schema,
        trust: 'state-ref',
      },
      {
        label: 'Capacity',
        reference: 'workstation_surface.v1#capacity',
        trust: 'state-ref',
      },
    ],
    inference_notes: ['Synthesized from workstation_surface.v1 because chat_context_surface.v1 was not present.'],
    suggested_actions: dashboardFallbackActions(fallback),
    prompts: [
      {
        label: 'What needs attention?',
        prompt: `Summarize the current Forager workstation state from workstation_surface.v1 for profile ${fallback.profile}. Cite top_attention, decision_inbox, capacity, and health refs; mark any inference.`,
      },
      {
        label: 'Explain top item',
        prompt: `Explain why "${fallback.topTitle}" needs attention. Include the risk, next safe action, and which receipt or state refs must be checked before action.`,
      },
    ],
  };

  return {
    schema: 'chat_context_surface.v1',
    status: openDecisionCount > 0 || fallback.runtimeDispatch.visibleCount > 0 || fallback.acceptedTruthRecovery.visibleCount > 0
      ? 'attention'
      : 'clear',
    mode: 'read_only_cited_answer',
    summary: `1 fallback assistant scope is available for ${fallback.projects.length} project row(s).`,
    actionPolicy: 'Suggested actions are review cards only; execution still requires action envelopes and receipts.',
    sourceRefs: [
      {
        label: 'overview',
        reference: 'workstation_surface.v1#top_attention',
      },
    ],
    scopes: [overviewScope],
  };
}

function dashboardFallbackActions(fallback: ChatContextFallback): AssistantAction[] {
  if (fallback.decisionInbox.openCount > 0) {
    return [{
      label: 'Open decision inbox',
      kind: 'open_surface',
      boundary: 'Proposal only; no decision is applied from the assistant panel.',
      target_ref: fallback.decisionInbox.schema,
      command: 'forager offdesk decisions --json',
    }];
  }
  if (fallback.acceptedTruthRecovery.visibleCount > 0) {
    return [{
      label: 'Review truth recovery',
      kind: 'open_surface',
      boundary: 'Proposal only; accepted truth requires review and receipt.',
      target_ref: fallback.acceptedTruthRecovery.schema,
      command: 'forager ondesk review-surface --json',
    }];
  }
  return [{
    label: 'Review dashboard',
    kind: 'inspect_state',
    boundary: 'Proposal only; inspect current state before starting new work.',
    target_ref: 'workstation_surface.v1',
    command: 'forager status --json',
  }];
}

function chatContextScopes(values: unknown[]): ChatContextScope[] {
  return values.filter(isRecord).map((scope) => ({
    scope_id: fallbackString(stringAt(scope, 'scope_id'), 'scope'),
    label: fallbackString(stringAt(scope, 'label'), 'Assistant scope'),
    kind: fallbackString(stringAt(scope, 'kind'), 'overview'),
    answer: fallbackString(
      stringAt(scope, 'answer'),
      'No assistant answer was provided; inspect the cited state refs.',
    ),
    confidence: fallbackString(stringAt(scope, 'confidence'), 'unknown'),
    citations: assistantRefs(arrayAt(scope, 'citations')),
    inference_notes: stringArrayAt(scope, 'inference_notes'),
    suggested_actions: assistantActions(arrayAt(scope, 'suggested_actions')),
    prompts: assistantPrompts(arrayAt(scope, 'prompts')),
  }));
}

function assistantRefs(values: unknown[]): AssistantRef[] {
  return values.filter(isRecord).map((item) => ({
    label: fallbackString(stringAt(item, 'label'), 'Reference'),
    reference: fallbackString(stringAt(item, 'reference'), '-'),
    trust: normalizeAssistantTrust(stringAt(item, 'trust')),
  }));
}

function assistantPrompts(values: unknown[]): AssistantPrompt[] {
  return values.filter(isRecord).map((item) => ({
    label: fallbackString(stringAt(item, 'label'), 'Ask'),
    prompt: fallbackString(stringAt(item, 'prompt'), ''),
  })).filter((item) => item.prompt.length > 0);
}

function assistantActions(values: unknown[]): AssistantAction[] {
  return values.filter(isRecord).map((item) => ({
    label: fallbackString(stringAt(item, 'label'), 'Review state'),
    kind: fallbackString(stringAt(item, 'kind'), 'inspect_state'),
    boundary: fallbackString(
      stringAt(item, 'boundary'),
      'Proposal only; execution requires the relevant control-plane receipt.',
    ),
    target_ref: fallbackString(stringAt(item, 'target_ref'), 'workstation_surface.v1'),
    command: fallbackString(stringAt(item, 'command'), ''),
  }));
}

function normalizeAssistantTrust(value: string): AssistantRef['trust'] {
  if (value === 'receipt-backed' || value === 'state-ref' || value === 'inference') {
    return value;
  }
  return 'inference';
}

function healthItems(values: unknown[]): HealthItem[] {
  const items = values.filter(isRecord).map((item) => ({
    id: fallbackString(stringAt(item, 'id'), stringAt(item, 'label'), 'health'),
    label: fallbackString(stringAt(item, 'label'), 'Health check'),
    status: normalizeHealth(stringAt(item, 'status')),
    summary: fallbackString(stringAt(item, 'summary'), 'No summary available.'),
  }));

  return items.length ? items : [{
    id: 'unknown',
    label: 'Health unavailable',
    status: 'unknown',
    summary: 'No health checks were included in the workstation surface.',
  }];
}

function projectRows(values: unknown[]): ProjectRow[] {
  return values.filter(isRecord).map((item) => ({
    project_key: fallbackString(stringAt(item, 'project_key'), 'project'),
    display_name: fallbackString(stringAt(item, 'display_name'), stringAt(item, 'project_key'), 'Project'),
    severity: normalizeSeverity(stringAt(item, 'severity')),
    plan: fallbackString(stringAt(item, 'plan'), '-'),
    runtime: fallbackString(stringAt(item, 'runtime'), '-'),
    decisions: numberAt(item, 'decisions'),
    closeout: fallbackString(stringAt(item, 'closeout'), '-'),
    truth: fallbackString(stringAt(item, 'truth'), '-'),
    last_activity: fallbackString(stringAt(item, 'last_activity'), '-'),
    task_items: surfaceProjectTaskItems(arrayAt(item, 'task_items')),
  }));
}

function projectDetailsFromSurface(
  projects: ProjectRow[],
  decisionInbox: DecisionInboxView,
  runtimeDispatch: RuntimeDispatchView,
  acceptedTruthRecovery: AcceptedTruthRecoveryView,
  chatContext: ChatContextSurface,
): ProjectDetail[] {
  return projects.map((project) => {
    const decisions = decisionInbox.items.filter((item) => item.project_key === project.project_key);
    const runtimeItems = runtimeDispatch.items.filter((item) => item.project_key === project.project_key);
    const truthItems = acceptedTruthRecovery.items.filter((item) => item.project_key === project.project_key);
    const attentionCount = decisions.length + runtimeItems.length + truthItems.length;
    const chatScope = chatContext.scopes.find((scope) => scope.scope_id === `project:${project.project_key}`);
    const assistantContext = projectAssistantContext(project, decisions, runtimeItems, truthItems);

    return {
      project_key: project.project_key,
      display_name: project.display_name,
      severity: project.severity,
      status_line: [
        `Plan ${project.plan}`,
        `Runtime ${project.runtime}`,
        `Closeout ${project.closeout}`,
        `Truth ${project.truth}`,
      ].join(' / '),
      last_activity: project.last_activity,
      plan: project.plan,
      runtime: project.runtime,
      closeout: project.closeout,
      truth: project.truth,
      attention_count: attentionCount,
      open_decision_count: decisions.length || project.decisions,
      runtime_handoff_count: runtimeItems.length,
      truth_recovery_count: truthItems.length,
      primary_action: projectPrimaryAction(decisions, runtimeItems, truthItems),
      attention_items: projectAttentionItems(decisions, runtimeItems, truthItems),
      task_items: project.task_items.length
        ? project.task_items
        : projectTaskItems(project, decisions, runtimeItems, truthItems),
      decision_titles: decisions.map((item) => item.title).slice(0, 3),
      runtime_stages: runtimeItems.map((item) => `${formatLabel(item.stage)}: ${item.title}`).slice(0, 3),
      truth_recovery_stages: truthItems
        .map((item) => `${formatLabel(item.stage)}: ${item.next_safe_action}`)
        .slice(0, 3),
      graph_title: `${project.display_name} provenance path`,
      graph_edges: projectGraphEdges(project, decisions, runtimeItems, truthItems),
      assistant_context: assistantContext,
      assistant_answer: chatScope?.answer || assistantContext,
      assistant_prompts: chatScope?.prompts.length
        ? chatScope.prompts
        : projectAssistantPrompts(project, decisions, runtimeItems, truthItems),
      assistant_refs: chatScope?.citations.length
        ? chatScope.citations
        : projectAssistantRefs(project, decisions, runtimeItems, truthItems),
      assistant_suggested_actions: chatScope?.suggested_actions.length
        ? chatScope.suggested_actions
        : projectAssistantActions(project, decisions, runtimeItems, truthItems),
      assistant_inference_notes: chatScope?.inference_notes ?? [],
    };
  });
}

function surfaceProjectTaskItems(values: unknown[]): ProjectTaskItem[] {
  return values.filter(isRecord).map((item) => ({
    kind: fallbackString(stringAt(item, 'kind'), 'Task'),
    title: fallbackString(stringAt(item, 'title'), stringAt(item, 'task_id'), 'Task'),
    status: fallbackString(stringAt(item, 'status'), 'unknown'),
    summary: fallbackString(stringAt(item, 'summary'), stringAt(item, 'next_safe_action_kind'), 'No task summary was provided.'),
    reference: fallbackString(stringAt(item, 'reference'), stringAt(item, 'task_id'), 'offdesk_tasks.json'),
    command: fallbackString(stringAt(item, 'command'), 'forager offdesk tasks --json'),
    inspection_items: surfaceProjectTaskInspectionItems(arrayAt(item, 'inspection_items')),
    receipt_links: surfaceProjectTaskReceiptLinks(arrayAt(item, 'receipt_links')),
    task_id: optionalStringAt(item, 'task_id'),
    next_safe_action_kind: optionalStringAt(item, 'next_safe_action_kind'),
    requires_operator_review: booleanAt(item, 'requires_operator_review'),
  }));
}

function surfaceProjectTaskReceiptLinks(values: unknown[]): ProjectTaskReceiptLink[] {
  return values.filter(isRecord).map((item) => ({
    source: fallbackString(stringAt(item, 'source'), 'receipt'),
    schema: fallbackString(stringAt(item, 'schema'), 'unknown_schema'),
    record_id: fallbackString(stringAt(item, 'record_id'), 'unknown_record'),
    result_status: fallbackString(stringAt(item, 'result_status'), 'unknown'),
    recorded_at: fallbackString(stringAt(item, 'recorded_at'), '-'),
    summary: fallbackString(stringAt(item, 'summary'), ''),
  }));
}

function surfaceProjectTaskInspectionItems(values: unknown[]): ProjectTaskInspectionItem[] {
  return values.filter(isRecord).map((item) => ({
    label: fallbackString(stringAt(item, 'label'), 'State'),
    value: fallbackString(stringAt(item, 'value'), '-'),
    tone: normalizeInspectionTone(stringAt(item, 'tone')),
  }));
}

function normalizeInspectionTone(value: string): ProjectTaskInspectionItem['tone'] {
  if (value === 'muted' || value === 'attention' || value === 'success' || value === 'danger') {
    return value;
  }
  return 'neutral';
}

function projectTaskItems(
  project: ProjectRow,
  decisions: DecisionItem[],
  runtimeItems: RuntimeDispatchItem[],
  truthItems: AcceptedTruthRecoveryItem[],
): ProjectTaskItem[] {
  const decisionTasks = decisions.map((item) => {
    const action = item.action_envelopes[0];
    const latestExecution = action?.latest_execution;
    const latestReceipt = action?.latest_receipt;
    return {
      kind: 'Decision task',
      title: item.title,
      status: latestExecution?.result_status || latestReceipt?.result_status || item.status,
      summary: item.recommendation || item.why_now,
      reference: item.receipt_ref || item.decision_id,
      command: action?.allowed_command || item.cli_fallback,
      inspection_items: [],
      receipt_links: [],
    };
  });

  const runtimeTasks = runtimeItems.map((item) => ({
    kind: 'Runtime task',
    title: item.title,
    status: item.latest_receipt?.result_status || item.latest_preflight?.result_status || item.stage,
    summary: item.latest_preflight?.reason || item.latest_receipt?.reason || item.boundary,
    reference: item.latest_receipt?.receipt_id || item.handoff_id || item.closeout_id || item.decision_id,
    command: item.tick_command || item.dispatch_command || item.preflight_command,
    inspection_items: [],
    receipt_links: [],
  }));

  const truthTasks = truthItems.map((item) => ({
    kind: 'Truth task',
    title: formatLabel(item.stage),
    status: item.acceptance_status,
    summary: item.next_safe_action,
    reference: item.receipt_id || item.closeout_id || item.review_id,
    command: item.resolve_command || item.retire_command,
    inspection_items: [],
    receipt_links: [],
  }));

  const derivedTasks: ProjectTaskItem[] = [];
  if (decisionTasks.length === 0 && project.decisions > 0) {
    derivedTasks.push({
      kind: 'Decision count',
      title: `${project.decisions} decision${project.decisions === 1 ? '' : 's'} reported`,
      status: 'needs inspection',
      summary: 'The workstation surface reports decision pressure, but no scoped decision records were included for this project.',
      reference: `workstation_surface.v1#project:${project.project_key}`,
      command: 'forager offdesk decisions --json',
      inspection_items: [],
      receipt_links: [],
    });
  }
  if (runtimeTasks.length === 0 && project.runtime !== '-' && project.runtime !== 'completed') {
    derivedTasks.push({
      kind: 'Runtime state',
      title: `Runtime ${formatLabel(project.runtime)}`,
      status: project.runtime,
      summary: 'Runtime state is visible in the project row; inspect the queue before launching or stopping work.',
      reference: `workstation_surface.v1#project:${project.project_key}`,
      command: 'forager status --json',
      inspection_items: [],
      receipt_links: [],
    });
  }

  return [
    ...decisionTasks,
    ...runtimeTasks,
    ...truthTasks,
    ...derivedTasks,
  ].slice(0, 6);
}

function projectAttentionItems(
  decisions: DecisionItem[],
  runtimeItems: RuntimeDispatchItem[],
  truthItems: AcceptedTruthRecoveryItem[],
): ProjectAttentionItem[] {
  const decisionItems = decisions.map((item) => {
    const action = item.action_envelopes[0];
    const latestReceipt = action?.latest_receipt;
    const latestExecution = action?.latest_execution;
    const status = latestExecution?.result_status
      ? `latest execution ${latestExecution.result_status}`
      : latestReceipt?.result_status
        ? `latest receipt ${latestReceipt.result_status}`
        : item.status;

    return {
      kind: 'Decision',
      title: item.title,
      severity: item.severity,
      summary: item.why_now,
      status,
      reference: item.receipt_ref || item.decision_id,
      command: action?.allowed_command || item.cli_fallback,
      boundary: action?.stale_rejection_reason || item.authorization_boundary,
    };
  });

  const runtimeAttentionItems = runtimeItems.map((item) => ({
    kind: 'Runtime handoff',
    title: item.title,
    severity: item.severity,
    summary: item.latest_preflight?.reason || item.latest_receipt?.reason || item.boundary,
    status: item.latest_receipt?.result_status || item.latest_preflight?.result_status || item.stage,
    reference: item.latest_receipt?.receipt_id || item.handoff_id || item.closeout_id || item.decision_id,
    command: item.dispatch_command || item.preflight_command || item.tick_command,
    boundary: item.boundary,
  }));

  const truthAttentionItems = truthItems.map((item) => ({
    kind: 'Truth recovery',
    title: formatLabel(item.stage),
    severity: item.severity,
    summary: item.next_safe_action,
    status: `${item.acceptance_status} / ${item.verification_status}`,
    reference: item.receipt_id || item.closeout_id || item.review_id,
    command: item.resolve_command || item.retire_command,
    boundary: item.boundary,
  }));

  return [
    ...decisionItems,
    ...runtimeAttentionItems,
    ...truthAttentionItems,
  ].slice(0, 5);
}

function projectGraphEdges(
  project: ProjectRow,
  decisions: DecisionItem[],
  runtimeItems: RuntimeDispatchItem[],
  truthItems: AcceptedTruthRecoveryItem[],
): GraphEdge[] {
  const projectLabel = project.display_name;
  const edges: GraphEdge[] = [
    {
      from: `${projectLabel} project`,
      to: `${projectLabel} plan`,
      label: formatLabel(project.plan),
    },
    {
      from: `${projectLabel} plan`,
      to: `${projectLabel} runtime`,
      label: formatLabel(project.runtime),
    },
    {
      from: `${projectLabel} runtime`,
      to: `${projectLabel} closeout`,
      label: formatLabel(project.closeout),
    },
    {
      from: `${projectLabel} closeout`,
      to: `${projectLabel} accepted truth`,
      label: formatLabel(project.truth),
    },
  ];

  if (decisions.length > 0) {
    edges.push({
      from: `${projectLabel} decisions`,
      to: `${projectLabel} runtime`,
      label: countLabel(decisions.length, 'open'),
    });
  }
  if (runtimeItems.length > 0) {
    edges.push({
      from: `${projectLabel} closeout`,
      to: `${projectLabel} runtime handoff`,
      label: countLabel(runtimeItems.length, 'handoff'),
    });
  }
  if (truthItems.length > 0) {
    edges.push({
      from: `${projectLabel} closeout`,
      to: `${projectLabel} truth recovery`,
      label: countLabel(truthItems.length, 'item'),
    });
  }

  return edges;
}

function countLabel(count: number, singular: string): string {
  return `${count} ${singular}${count === 1 ? '' : 's'}`;
}

function projectAssistantContext(
  project: ProjectRow,
  decisions: DecisionItem[],
  runtimeItems: RuntimeDispatchItem[],
  truthItems: AcceptedTruthRecoveryItem[],
): string {
  if (decisions.length > 0) {
    return `${project.display_name} needs operator judgment on "${decisions[0].title}". Answers should explain risk, receipt boundary, and the next safe action before suggesting any action card.`;
  }
  if (truthItems.length > 0) {
    return `${project.display_name} has accepted-truth recovery work visible. Answers should separate closeout evidence, follow-up state, and accepted-truth recording.`;
  }
  if (runtimeItems.length > 0) {
    return `${project.display_name} has a receipted runtime handoff. Answers should distinguish queueing, launch, and task-scoped monitoring.`;
  }
  return `${project.display_name} has no project-specific action queued. Answers should summarize current state and call out any inference explicitly.`;
}

function projectAssistantPrompts(
  project: ProjectRow,
  decisions: DecisionItem[],
  runtimeItems: RuntimeDispatchItem[],
  truthItems: AcceptedTruthRecoveryItem[],
): AssistantPrompt[] {
  const prompts: AssistantPrompt[] = [];

  if (decisions.length > 0) {
    prompts.push({
      label: 'Can this advance?',
      prompt: `Explain whether "${decisions[0].title}" can advance, citing the decision state and receipt boundary.`,
    });
  } else if (truthItems.length > 0) {
    prompts.push({
      label: 'What blocks truth?',
      prompt: `Explain what blocks accepted truth for ${project.display_name} and which closeout receipt must be reviewed.`,
    });
  } else if (runtimeItems.length > 0) {
    prompts.push({
      label: 'Runtime next step',
      prompt: `Explain the next safe runtime step for ${project.display_name} without launching work directly.`,
    });
  } else {
    prompts.push({
      label: 'Summarize state',
      prompt: `Summarize ${project.display_name} from the current workstation surface and mark any inference.`,
    });
  }

  prompts.push(
    {
      label: 'Show provenance',
      prompt: `Trace the scoped provenance path for ${project.display_name}.`,
    },
    {
      label: 'Draft safe note',
      prompt: `Draft a safe operator note for ${project.display_name} without executing any action.`,
    },
  );

  return prompts.slice(0, 3);
}

function projectAssistantRefs(
  project: ProjectRow,
  decisions: DecisionItem[],
  runtimeItems: RuntimeDispatchItem[],
  truthItems: AcceptedTruthRecoveryItem[],
): AssistantRef[] {
  const refs: AssistantRef[] = [
    {
      label: 'Project state',
      reference: `workstation_surface.v1#project:${project.project_key}`,
      trust: 'state-ref',
    },
  ];
  const decision = decisions[0];
  if (decision) {
    refs.push({
      label: 'Decision',
      reference: `decision:${decision.decision_id}`,
      trust: 'state-ref',
    });
    const receiptId = decision.action_envelopes.find((action) => action.latest_receipt)?.latest_receipt?.receipt_id;
    if (receiptId) {
      refs.push({
        label: 'Latest receipt',
        reference: receiptId,
        trust: 'receipt-backed',
      });
    }
  }
  const runtimeItem = runtimeItems[0];
  if (runtimeItem) {
    refs.push({
      label: 'Runtime handoff',
      reference: runtimeItem.latest_receipt?.receipt_id || runtimeItem.closeout_id || runtimeItem.decision_id,
      trust: runtimeItem.latest_receipt ? 'receipt-backed' : 'state-ref',
    });
  }
  const truthItem = truthItems[0];
  if (truthItem) {
    refs.push({
      label: 'Closeout receipt',
      reference: truthItem.receipt_id || truthItem.closeout_id,
      trust: truthItem.receipt_id ? 'receipt-backed' : 'state-ref',
    });
  }

  return refs.slice(0, 3);
}

function projectAssistantActions(
  project: ProjectRow,
  decisions: DecisionItem[],
  runtimeItems: RuntimeDispatchItem[],
  truthItems: AcceptedTruthRecoveryItem[],
): AssistantAction[] {
  const decision = decisions[0];
  if (decision) {
    return [{
      label: 'Review project decision',
      kind: 'open_surface',
      boundary: 'Proposal only; decision changes require an action envelope and receipt.',
      target_ref: `decision:${decision.decision_id}`,
      command: decision.action_envelopes[0]?.allowed_command || decision.cli_fallback,
    }];
  }
  const truthItem = truthItems[0];
  if (truthItem) {
    return [{
      label: 'Review truth recovery',
      kind: 'open_surface',
      boundary: truthItem.boundary,
      target_ref: truthItem.receipt_id || truthItem.closeout_id,
      command: truthItem.resolve_command || truthItem.retire_command,
    }];
  }
  const runtimeItem = runtimeItems[0];
  if (runtimeItem) {
    return [{
      label: 'Inspect runtime handoff',
      kind: 'inspect_state',
      boundary: runtimeItem.boundary,
      target_ref: runtimeItem.latest_receipt?.receipt_id || runtimeItem.closeout_id || runtimeItem.decision_id,
      command: runtimeItem.tick_command || runtimeItem.dispatch_command || runtimeItem.preflight_command,
    }];
  }
  const task = project.task_items[0];
  if (task) {
    return [{
      label: 'Inspect task drawer',
      kind: 'inspect_state',
      boundary: 'Proposal only; task execution remains controlled by Offdesk commands and receipts.',
      target_ref: task.reference,
      command: task.command,
    }];
  }
  return [{
    label: 'Review project state',
    kind: 'inspect_state',
    boundary: 'Proposal only; no mutation is authorized from this assistant panel.',
    target_ref: `workstation_surface.v1#project:${project.project_key}`,
    command: 'forager status --json',
  }];
}

function projectPrimaryAction(
  decisions: DecisionItem[],
  runtimeItems: RuntimeDispatchItem[],
  truthItems: AcceptedTruthRecoveryItem[],
): string {
  if (decisions.length > 0) {
    return `Decision needed: ${decisions[0].title}`;
  }
  if (truthItems.length > 0) {
    return truthItems[0].next_safe_action;
  }
  if (runtimeItems.length > 0) {
    return `Runtime handoff: ${formatLabel(runtimeItems[0].stage)}`;
  }
  return 'No project-specific action is currently queued.';
}

function decisionItems(values: unknown[]): DecisionItem[] {
  return values.filter(isRecord).map((item) => ({
    decision_id: fallbackString(stringAt(item, 'decision_id'), 'decision'),
    kind: fallbackString(stringAt(item, 'kind'), 'decision'),
    severity: normalizeSeverity(stringAt(item, 'severity')),
    status: fallbackString(stringAt(item, 'status'), 'unknown'),
    materiality: fallbackString(stringAt(item, 'materiality'), 'unknown'),
    raised_by: fallbackString(stringAt(item, 'raised_by'), 'unknown'),
    project_key: fallbackString(stringAt(item, 'project_key'), 'project'),
    title: fallbackString(stringAt(item, 'title'), 'Decision needed'),
    what_changed: fallbackString(stringAt(item, 'what_changed'), stringAt(item, 'title'), 'A decision record changed.'),
    why_now: fallbackString(stringAt(item, 'why_now'), 'Operator review is required.'),
    risk: fallbackString(stringAt(item, 'risk'), 'Review before changing scope, runtime, provider, or accepted-truth state.'),
    evidence_refs: evidenceRefs(arrayAt(item, 'evidence_refs')),
    allowed_actions: arrayAt(item, 'allowed_actions')
      .filter((value): value is string => typeof value === 'string')
      .map((value) => value.trim())
      .filter(Boolean),
    action_envelopes: actionEnvelopePreviews(arrayAt(item, 'action_envelopes')),
    recommendation: fallbackString(stringAt(item, 'recommendation'), ''),
    default_if_no_reply: fallbackString(stringAt(item, 'default_if_no_reply'), ''),
    authorization_boundary: fallbackString(stringAt(item, 'authorization_boundary'), FALLBACK_BOUNDARY),
    stale_guard: fallbackString(stringAt(item, 'stale_guard'), 'Verify the decision is still current before acting.'),
    receipt_ref: fallbackString(stringAt(item, 'receipt_ref'), ''),
    cli_fallback: fallbackString(stringAt(item, 'cli_fallback'), 'forager offdesk decisions --json'),
    updated_at: fallbackString(stringAt(item, 'updated_at'), '-'),
  }));
}

function decisionInboxView(inbox: Record<string, unknown>, legacyDecisions: unknown[]): DecisionInboxView {
  const actionModel = recordAt(inbox, 'action_model');
  const emptyState = recordAt(inbox, 'empty_state');
  const items = decisionItems(arrayAt(inbox, 'items'));
  const fallbackItems = items.length ? items : decisionItems(legacyDecisions);
  const openCount = numberAt(inbox, 'open_count') || fallbackItems.length;

  return {
    schema: fallbackString(stringAt(inbox, 'schema'), 'decision_inbox_surface.v1'),
    status: fallbackString(stringAt(inbox, 'status'), openCount > 0 ? 'attention' : 'clear'),
    openCount,
    visibleCount: numberAt(inbox, 'visible_count') || fallbackItems.length,
    summary: fallbackString(
      stringAt(inbox, 'summary'),
      openCount > 0
        ? `${openCount} open decision record(s) require operator review.`
        : 'No open human decision records are currently visible.',
    ),
    emptyTitle: fallbackString(stringAt(emptyState, 'title'), 'No open decisions'),
    emptySummary: fallbackString(
      stringAt(emptyState, 'summary'),
      'The current workstation surface does not report a human decision item.',
    ),
    emptyCliFallback: fallbackString(stringAt(emptyState, 'cli_fallback'), 'forager offdesk decisions --json'),
    actionMode: fallbackString(stringAt(actionModel, 'mode'), 'read_only_preview'),
    directInputAllowed: booleanAt(actionModel, 'direct_input_allowed'),
    mutationPolicy: fallbackString(
      stringAt(actionModel, 'mutation_policy'),
      'No decision action mutates state from this read-only surface.',
    ),
    receiptPolicy: fallbackString(
      stringAt(actionModel, 'receipt_policy'),
      'Future write actions must produce receipts before execution continues.',
    ),
    items: fallbackItems,
  };
}

function runtimeDispatchView(surface: Record<string, unknown>): RuntimeDispatchView {
  const emptyState = recordAt(surface, 'empty_state');
  const items = runtimeDispatchItems(arrayAt(surface, 'items'));
  const candidateCount = numberAt(surface, 'candidate_count') || items.length;

  return {
    schema: fallbackString(stringAt(surface, 'schema'), 'runtime_dispatch_surface.v1'),
    status: fallbackString(stringAt(surface, 'status'), candidateCount > 0 ? 'attention' : 'clear'),
    candidateCount,
    visibleCount: numberAt(surface, 'visible_count') || items.length,
    summary: fallbackString(
      stringAt(surface, 'summary'),
      candidateCount > 0
        ? `${candidateCount} post-closeout runtime handoff candidate(s) are visible.`
        : 'No post-closeout runtime handoff candidates are visible.',
    ),
    emptyTitle: fallbackString(stringAt(emptyState, 'title'), 'No runtime handoff'),
    emptySummary: fallbackString(
      stringAt(emptyState, 'summary'),
      'Receipted decision-action closeouts are not waiting for runtime dispatch.',
    ),
    emptyCliFallback: fallbackString(stringAt(emptyState, 'cli_fallback'), 'forager ondesk workstation-surface --json'),
    items,
  };
}

function runtimeDispatchItems(values: unknown[]): RuntimeDispatchItem[] {
  return values.filter(isRecord).map((item) => ({
    project_key: fallbackString(stringAt(item, 'project_key'), 'project'),
    decision_id: fallbackString(stringAt(item, 'decision_id'), 'decision'),
    title: fallbackString(stringAt(item, 'title'), 'Runtime handoff'),
    stage: fallbackString(stringAt(item, 'stage'), 'needs_preflight'),
    severity: normalizeSeverity(stringAt(item, 'severity')),
    closeout_id: fallbackString(stringAt(item, 'closeout_id'), ''),
    execution_id: fallbackString(stringAt(item, 'execution_id'), ''),
    receipt_id: fallbackString(stringAt(item, 'receipt_id'), ''),
    handoff_id: fallbackString(stringAt(item, 'handoff_id'), ''),
    latest_preflight: runtimeDispatchPreflightSummary(recordAt(item, 'latest_preflight')),
    latest_receipt: runtimeDispatchReceiptSummary(recordAt(item, 'latest_receipt')),
    preflight_command: fallbackString(stringAt(item, 'preflight_command'), 'forager ondesk runtime-preflight --closeout-id <ID> --json'),
    dispatch_command: fallbackString(stringAt(item, 'dispatch_command'), ''),
    tick_command: fallbackString(stringAt(item, 'tick_command'), ''),
    boundary: fallbackString(
      stringAt(item, 'boundary'),
      'Queueing requires runtime-dispatch; launching still goes through offdesk tick.',
    ),
  }));
}

function runtimeDispatchPreflightSummary(item: Record<string, unknown>): RuntimeDispatchPreflightSummary | null {
  const preflightId = stringAt(item, 'preflight_id');
  if (!preflightId) {
    return null;
  }

  return {
    schema: fallbackString(stringAt(item, 'schema'), 'runtime_dispatch_preflight.v1'),
    preflight_id: preflightId,
    processed_at: fallbackString(stringAt(item, 'processed_at'), '-'),
    result_status: fallbackString(stringAt(item, 'result_status'), 'unknown'),
    reason: fallbackString(stringAt(item, 'reason'), 'No runtime preflight reason was provided.'),
  };
}

function runtimeDispatchReceiptSummary(item: Record<string, unknown>): RuntimeDispatchReceiptSummary | null {
  const receiptId = stringAt(item, 'receipt_id');
  if (!receiptId) {
    return null;
  }

  return {
    schema: fallbackString(stringAt(item, 'schema'), 'runtime_dispatch_receipt.v1'),
    receipt_id: receiptId,
    preflight_id: fallbackString(stringAt(item, 'preflight_id'), ''),
    recorded_at: fallbackString(stringAt(item, 'recorded_at'), '-'),
    result_status: fallbackString(stringAt(item, 'result_status'), 'unknown'),
    task_id: fallbackString(stringAt(item, 'task_id'), ''),
    reason: fallbackString(stringAt(item, 'reason'), 'No runtime dispatch receipt reason was provided.'),
  };
}

function evidenceRefs(values: unknown[]): EvidenceRef[] {
  return values.filter(isRecord).map((item) => ({
    kind: fallbackString(stringAt(item, 'kind'), 'ref'),
    label: fallbackString(stringAt(item, 'label'), 'Evidence'),
    reference: fallbackString(stringAt(item, 'reference'), '-'),
  }));
}

function actionEnvelopePreviews(values: unknown[]): ActionEnvelopePreview[] {
  return values.filter(isRecord).map((item) => {
    const target = recordAt(item, 'target_ref');
    const latestReceipt = actionEnvelopeReceiptSummary(recordAt(item, 'latest_receipt'));
    const latestExecution = decisionActionExecutionSummary(recordAt(item, 'latest_execution'));
    return {
      schema: fallbackString(stringAt(item, 'schema'), 'action_envelope.v1'),
      action_id: fallbackString(stringAt(item, 'action_id'), 'action'),
      action_kind: fallbackString(stringAt(item, 'action_kind'), 'review_decision'),
      profile: fallbackString(stringAt(item, 'profile'), 'default'),
      project_key: fallbackString(stringAt(item, 'project_key'), 'project'),
      target_ref: {
        kind: fallbackString(stringAt(target, 'kind'), 'decision_record.v1'),
        decision_id: fallbackString(stringAt(target, 'decision_id'), 'decision'),
        status: fallbackString(stringAt(target, 'status'), 'unknown'),
        updated_at: fallbackString(stringAt(target, 'updated_at'), '-'),
      },
      observed_hash: fallbackString(stringAt(item, 'observed_hash'), ''),
      nonce: fallbackString(stringAt(item, 'nonce'), ''),
      ttl: fallbackString(stringAt(item, 'ttl'), ''),
      issued_at: fallbackString(stringAt(item, 'issued_at'), ''),
      expires_at: fallbackString(stringAt(item, 'expires_at'), ''),
      idempotency_key: fallbackString(stringAt(item, 'idempotency_key'), ''),
      preview: fallbackString(stringAt(item, 'preview'), 'Preview only.'),
      allowed_command: fallbackString(stringAt(item, 'allowed_command'), 'forager offdesk decisions --json'),
      forbidden_effects: arrayAt(item, 'forbidden_effects')
        .filter((value): value is string => typeof value === 'string')
        .map((value) => value.trim())
        .filter(Boolean),
      expected_receipt_schema: fallbackString(stringAt(item, 'expected_receipt_schema'), 'action_envelope_receipt.v1'),
      requires_confirmation: booleanAt(item, 'requires_confirmation'),
      confirmation_phrase: fallbackString(stringAt(item, 'confirmation_phrase'), ''),
      stale_rejection_reason: fallbackString(
        stringAt(item, 'stale_rejection_reason'),
        'Reject if the observed state no longer matches.',
      ),
      receipt_history_count: numberAt(item, 'receipt_history_count'),
      latest_receipt: latestReceipt,
      execution_history_count: numberAt(item, 'execution_history_count'),
      latest_execution: latestExecution,
    };
  });
}

function actionEnvelopeReceiptSummary(item: Record<string, unknown>): ActionEnvelopeReceiptSummary | null {
  const receiptId = stringAt(item, 'receipt_id');
  if (!receiptId) {
    return null;
  }

  return {
    schema: fallbackString(stringAt(item, 'schema'), 'action_envelope_receipt.v1'),
    receipt_id: receiptId,
    processed_at: fallbackString(stringAt(item, 'processed_at'), '-'),
    result_status: fallbackString(stringAt(item, 'result_status'), 'unknown'),
    stale: booleanAt(item, 'stale'),
    reason: fallbackString(stringAt(item, 'reason'), 'No receipt reason was provided.'),
    current_hash: fallbackString(stringAt(item, 'current_hash'), ''),
    failed_checks: arrayAt(item, 'failed_checks')
      .filter((value): value is string => typeof value === 'string')
      .map((value) => value.trim())
      .filter(Boolean),
  };
}

function decisionActionExecutionSummary(item: Record<string, unknown>): DecisionActionExecutionSummary | null {
  const executionId = stringAt(item, 'execution_id');
  if (!executionId) {
    return null;
  }

  return {
    schema: fallbackString(stringAt(item, 'schema'), 'decision_action_execution.v1'),
    execution_id: executionId,
    preflight_id: fallbackString(stringAt(item, 'preflight_id'), ''),
    executed_at: fallbackString(stringAt(item, 'executed_at'), '-'),
    result_status: fallbackString(stringAt(item, 'result_status'), 'unknown'),
    decision: fallbackString(stringAt(item, 'decision'), 'unknown'),
    decision_appended: booleanAt(item, 'decision_appended'),
    mutation_allowed_by_this_command: booleanAt(item, 'mutation_allowed_by_this_command'),
    reason: fallbackString(stringAt(item, 'reason'), 'No execution reason was provided.'),
    handoff_id: fallbackString(stringAt(item, 'handoff_id'), ''),
    closeout_command: fallbackString(stringAt(item, 'closeout_command'), ''),
    failed_checks: arrayAt(item, 'failed_checks')
      .filter((value): value is string => typeof value === 'string')
      .map((value) => value.trim())
      .filter(Boolean),
  };
}

function acceptedTruthRecoveryView(surface: Record<string, unknown>): AcceptedTruthRecoveryView {
  const emptyState = recordAt(surface, 'empty_state');
  const items = acceptedTruthRecoveryItems(arrayAt(surface, 'items'));
  const candidateCount = numberAt(surface, 'candidate_count') || items.length;

  return {
    schema: fallbackString(stringAt(surface, 'schema'), 'accepted_truth_recovery_surface.v1'),
    status: fallbackString(stringAt(surface, 'status'), candidateCount > 0 ? 'attention' : 'clear'),
    candidateCount,
    visibleCount: numberAt(surface, 'visible_count') || items.length,
    summary: fallbackString(
      stringAt(surface, 'summary'),
      candidateCount > 0
        ? `${candidateCount} accepted-truth recovery candidate(s) are visible.`
        : 'No closeout receipt currently blocks accepted-truth status.',
    ),
    emptyTitle: fallbackString(stringAt(emptyState, 'title'), 'Accepted truth clear'),
    emptySummary: fallbackString(
      stringAt(emptyState, 'summary'),
      'No latest closeout receipt currently needs accepted-truth recovery.',
    ),
    emptyCliFallback: fallbackString(stringAt(emptyState, 'cli_fallback'), 'forager ondesk review-surface --json'),
    items,
  };
}

function acceptedTruthRecoveryItems(values: unknown[]): AcceptedTruthRecoveryItem[] {
  return values.filter(isRecord).map((item) => ({
    project_key: fallbackString(stringAt(item, 'project_key'), 'project'),
    closeout_id: fallbackString(stringAt(item, 'closeout_id'), ''),
    review_id: fallbackString(stringAt(item, 'review_id'), ''),
    receipt_id: fallbackString(stringAt(item, 'receipt_id'), ''),
    acceptance_status: fallbackString(stringAt(item, 'acceptance_status'), 'unknown'),
    verification_status: fallbackString(stringAt(item, 'verification_status'), 'unknown'),
    stage: fallbackString(stringAt(item, 'stage'), 'needs_review'),
    severity: normalizeSeverity(stringAt(item, 'severity')),
    open_decision_count: numberAt(item, 'open_decision_count'),
    open_decision_kinds: arrayAt(item, 'open_decision_kinds')
      .filter((value): value is string => typeof value === 'string')
      .map((value) => value.trim())
      .filter(Boolean),
    evidence_status: fallbackString(stringAt(item, 'evidence_status'), 'unknown'),
    retention_review: fallbackString(stringAt(item, 'retention_review'), 'unknown'),
    wiki_promotion_state: fallbackString(stringAt(item, 'wiki_promotion_state'), 'unknown'),
    stale_task_count: numberAt(item, 'stale_task_count'),
    next_safe_action: fallbackString(
      stringAt(item, 'next_safe_action'),
      'Review the latest closeout receipt before treating output as accepted truth.',
    ),
    artifact_dir: fallbackString(stringAt(item, 'artifact_dir'), ''),
    reviewed_at: fallbackString(stringAt(item, 'reviewed_at'), '-'),
    resolve_command: fallbackString(stringAt(item, 'resolve_command'), ''),
    retire_command: fallbackString(stringAt(item, 'retire_command'), ''),
    action_envelopes: acceptedTruthRecoveryActionEnvelopePreviews(arrayAt(item, 'action_envelopes')),
    boundary: fallbackString(
      stringAt(item, 'boundary'),
      'This surface does not resolve follow-ups or record accepted truth.',
    ),
  }));
}

function acceptedTruthRecoveryActionEnvelopePreviews(values: unknown[]): AcceptedTruthRecoveryActionEnvelopePreview[] {
  return values.filter(isRecord).map((item) => {
    const target = recordAt(item, 'target_ref');
    const latestReceipt = actionEnvelopeReceiptSummary(recordAt(item, 'latest_receipt'));
    return {
      schema: fallbackString(stringAt(item, 'schema'), 'accepted_truth_recovery_action_envelope.v1'),
      action_id: fallbackString(stringAt(item, 'action_id'), 'truth_action'),
      action_kind: fallbackString(stringAt(item, 'action_kind'), 'resolve_followup'),
      profile: fallbackString(stringAt(item, 'profile'), 'default'),
      project_key: fallbackString(stringAt(item, 'project_key'), 'project'),
      target_ref: {
        kind: fallbackString(stringAt(target, 'kind'), 'accepted_truth_recovery.v1'),
        closeout_id: fallbackString(stringAt(target, 'closeout_id'), ''),
        review_id: fallbackString(stringAt(target, 'review_id'), ''),
        receipt_id: fallbackString(stringAt(target, 'receipt_id'), ''),
        acceptance_status: fallbackString(stringAt(target, 'acceptance_status'), 'unknown'),
        reviewed_at: fallbackString(stringAt(target, 'reviewed_at'), '-'),
      },
      observed_hash: fallbackString(stringAt(item, 'observed_hash'), ''),
      nonce: fallbackString(stringAt(item, 'nonce'), ''),
      ttl: fallbackString(stringAt(item, 'ttl'), ''),
      issued_at: fallbackString(stringAt(item, 'issued_at'), ''),
      expires_at: fallbackString(stringAt(item, 'expires_at'), ''),
      idempotency_key: fallbackString(stringAt(item, 'idempotency_key'), ''),
      preview: fallbackString(stringAt(item, 'preview'), 'Preview only.'),
      allowed_command: fallbackString(stringAt(item, 'allowed_command'), ''),
      forbidden_effects: arrayAt(item, 'forbidden_effects')
        .filter((value): value is string => typeof value === 'string')
        .map((value) => value.trim())
        .filter(Boolean),
      expected_receipt_schema: fallbackString(
        stringAt(item, 'expected_receipt_schema'),
        'accepted_truth_recovery_action_receipt.v1',
      ),
      requires_confirmation: booleanAt(item, 'requires_confirmation'),
      confirmation_phrase: fallbackString(stringAt(item, 'confirmation_phrase'), ''),
      stale_rejection_reason: fallbackString(
        stringAt(item, 'stale_rejection_reason'),
        'Reject if the closeout state no longer matches.',
      ),
      receipt_history_count: numberAt(item, 'receipt_history_count'),
      latest_receipt: latestReceipt,
    };
  });
}

function graphNodes(values: unknown[]): GraphNode[] {
  return values.filter(isRecord).map((item) => ({
    id: fallbackString(stringAt(item, 'id'), stringAt(item, 'label'), 'node'),
    label: fallbackString(stringAt(item, 'label'), 'Node'),
    kind: fallbackString(stringAt(item, 'kind'), 'node'),
  }));
}

function graphEdges(values: unknown[]): GraphEdge[] {
  return values.filter(isRecord).map((item) => ({
    from: fallbackString(stringAt(item, 'from'), '-'),
    to: fallbackString(stringAt(item, 'to'), '-'),
    label: fallbackString(stringAt(item, 'label'), 'related'),
  }));
}

function sourceRefsFromRecord(record: Record<string, unknown>): SourceRef[] {
  return Object.entries(record)
    .map(([label, value]) => ({
      label,
      reference: typeof value === 'string' ? value.trim() : '',
    }))
    .filter((item) => item.reference.length > 0);
}

function entriesFromRecord(record: Record<string, unknown>): { label: string; value: number }[] {
  return Object.entries(record)
    .map(([label, value]) => ({ label, value: typeof value === 'number' && Number.isFinite(value) ? value : 0 }));
}

function positiveEntriesFromRecord(record: Record<string, unknown>): { label: string; value: number }[] {
  return entriesFromRecord(record)
    .filter((entry) => entry.value > 0);
}

function normalizeSeverity(value: string): Severity {
  return KNOWN_SEVERITIES.has(value) ? value as Severity : 'info';
}

function normalizeHealth(value: string): HealthStatus {
  if (value === 'ok' || value === 'attention' || value === 'blocked' || value === 'critical') {
    return value;
  }

  return 'unknown';
}

function firstRecord(values: unknown[]): Record<string, unknown> {
  const first = values.find(isRecord);
  return first ?? {};
}

function recordAt(value: Record<string, unknown>, key: string): Record<string, unknown> {
  const child = value[key];
  return isRecord(child) ? child : {};
}

function stringAt(value: Record<string, unknown>, key: string): string {
  const child = value[key];
  return typeof child === 'string' ? child.trim() : '';
}

function optionalStringAt(value: Record<string, unknown>, key: string): string | undefined {
  const child = stringAt(value, key);
  return child || undefined;
}

function numberAt(value: Record<string, unknown>, key: string): number {
  const child = value[key];
  return typeof child === 'number' && Number.isFinite(child) ? child : 0;
}

function booleanAt(value: Record<string, unknown>, key: string): boolean {
  return value[key] === true;
}

function arrayAt(value: Record<string, unknown>, key: string): unknown[] {
  const child = value[key];
  return Array.isArray(child) ? child : [];
}

function stringArrayAt(value: Record<string, unknown>, key: string): string[] {
  return arrayAt(value, key)
    .filter((item): item is string => typeof item === 'string')
    .map((item) => item.trim())
    .filter(Boolean);
}

function fallbackString(...values: string[]): string {
  return values.find((value) => value.trim().length > 0) ?? '';
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}
