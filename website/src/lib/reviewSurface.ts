export type Severity = 'ok' | 'info' | 'attention' | 'blocked' | 'critical';

export type DetailRef = {
  kind: string;
  label: string;
  command?: string;
  path?: string;
  plan_id?: string;
};

export type OperatorStateCard = {
  id: string;
  title: string;
  severity: Severity;
  state_summary: string;
  primary_blocker_or_decision: {
    label: string;
    summary: string;
  };
  next_safe_action: {
    label: string;
    reason: string;
    command?: string;
  };
  detail_ref: DetailRef;
  authorization_boundary: string;
  counts?: Record<string, number>;
};

export type QueueCard = {
  id: string;
  title: string;
  severity: Severity;
  stateSummary: string;
  selected: boolean;
};

export type ReviewCount = {
  label: string;
  value: number;
};

export type ReviewView = {
  sourceLabel: string;
  profile: string;
  projectKey: string;
  openItems: number;
  currentSeverity: Severity;
  currentStateLabel: string;
  queue: QueueCard[];
  selected: OperatorStateCard;
  detailValue: string;
  countEntries: ReviewCount[];
};

const DEFAULT_AUTHORIZATION_BOUNDARY =
  'Read-only review surface; this page does not approve, execute, delete, promote, or start agents.';

const KNOWN_SEVERITIES = new Set(['ok', 'info', 'attention', 'blocked', 'critical']);

export const severityLabels: Record<Severity, string> = {
  ok: 'Normal',
  info: 'Review',
  attention: 'Attention',
  blocked: 'Blocked',
  critical: 'Critical',
};

export const severityClasses: Record<Severity, string> = {
  ok: 'border-emerald-400/45 bg-emerald-400/10 text-emerald-100',
  info: 'border-sky-400/45 bg-sky-400/10 text-sky-100',
  attention: 'border-brand-500/50 bg-brand-500/10 text-brand-100',
  blocked: 'border-amber-300/55 bg-amber-300/10 text-amber-100',
  critical: 'border-red-400/60 bg-red-400/10 text-red-100',
};

export const inactiveQueueClasses = 'border-slate-700 bg-slate-900/70 text-slate-200';

export function detailValue(card: OperatorStateCard): string {
  return card.detail_ref.command ?? card.detail_ref.plan_id ?? card.detail_ref.path ?? card.detail_ref.kind;
}

export function formatCountLabel(label: string): string {
  return label.replaceAll('_', ' ');
}

export function viewFromOperatorCards(
  cards: OperatorStateCard[],
  selectedId = cards[0]?.id ?? '',
): ReviewView {
  const selected = cards.find((card) => card.id === selectedId) ?? cards[0];

  if (!selected) {
    throw new Error('review surface requires at least one fallback card');
  }

  const queue = cards.map((card) => ({
    id: card.id,
    title: card.title,
    severity: card.severity,
    stateSummary: card.state_summary,
    selected: card.id === selected.id,
  }));
  const countEntries = countEntriesFromRecord(selected.counts);

  return {
    sourceLabel: 'Fixture fallback',
    profile: 'default',
    projectKey: 'all',
    openItems: queue.filter((card) => card.severity !== 'ok').length,
    currentSeverity: selected.severity,
    currentStateLabel: severityLabels[selected.severity],
    queue,
    selected,
    detailValue: detailValue(selected),
    countEntries: countEntries.length ? countEntries : [{ label: 'no_open_items', value: 0 }],
  };
}

export function isReviewSurface(value: unknown): value is Record<string, unknown> {
  return isRecord(value) && value.schema === 'review_surface.v1';
}

export function projectReviewSurface(value: unknown): ReviewView {
  if (!isReviewSurface(value)) {
    throw new Error('expected review_surface.v1');
  }

  const status = recordAt(value, 'status');
  const acceptedTruth = recordAt(value, 'accepted_truth');
  const closeout = recordAt(value, 'closeout');
  const runtime = recordAt(value, 'runtime');
  const decisions = recordAt(value, 'decisions');
  const adaptiveWiki = recordAt(value, 'adaptive_wiki');
  const implementationPacket = recordAt(value, 'implementation_packet');
  const nextSafeAction = firstRecord(arrayAt(value, 'next_safe_actions'));
  const counts = countsFromReviewSurface({
    runtime,
    closeout,
    decisions,
    adaptiveWiki,
    implementationPacket,
  });

  const severity = normalizeSeverity(stringAt(status, 'severity'), counts);
  const summary = fallbackString(
    stringAt(status, 'summary'),
    stringAt(nextSafeAction, 'detail'),
    'Review the current operator surface before taking action.',
  );
  const title = fallbackString(stringAt(status, 'label'), 'Live review surface');
  const actionLabel = fallbackString(
    stringAt(nextSafeAction, 'label'),
    titleFromKind(stringAt(nextSafeAction, 'kind')),
    'Review current state',
  );
  const actionReason = fallbackString(
    stringAt(nextSafeAction, 'detail'),
    stringAt(nextSafeAction, 'reason'),
    summary,
  );
  const command = stringAt(nextSafeAction, 'command');
  const selected: OperatorStateCard = {
    id: 'live-review-surface',
    title,
    severity,
    state_summary: summary,
    primary_blocker_or_decision: decisionBlock(decisions, acceptedTruth, closeout),
    next_safe_action: {
      label: actionLabel,
      reason: actionReason,
      ...(command ? { command } : {}),
    },
    detail_ref: {
      kind: 'review_surface.v1',
      label: 'review_surface.v1',
      command: 'forager ondesk review-surface --json',
    },
    authorization_boundary: DEFAULT_AUTHORIZATION_BOUNDARY,
    counts,
  };
  const queue = liveQueueCards({
    selected,
    acceptedTruth,
    closeout,
    runtime,
    decisions,
    adaptiveWiki,
    implementationPacket,
  });
  const countEntries = countEntriesFromRecord(counts);

  return {
    sourceLabel: 'Live review_surface.v1',
    profile: fallbackString(stringAt(value, 'profile'), 'default'),
    projectKey: fallbackString(stringAt(value, 'project_key'), 'all'),
    openItems: queue.filter((card) => card.severity !== 'ok').length,
    currentSeverity: selected.severity,
    currentStateLabel: severityLabels[selected.severity],
    queue,
    selected,
    detailValue: detailValue(selected),
    countEntries: countEntries.length ? countEntries : [{ label: 'no_open_items', value: 0 }],
  };
}

function liveQueueCards(input: {
  selected: OperatorStateCard;
  acceptedTruth: Record<string, unknown>;
  closeout: Record<string, unknown>;
  runtime: Record<string, unknown>;
  decisions: Record<string, unknown>;
  adaptiveWiki: Record<string, unknown>;
  implementationPacket: Record<string, unknown>;
}): QueueCard[] {
  const cards: QueueCard[] = [
    {
      id: 'status',
      title: input.selected.title,
      severity: input.selected.severity,
      stateSummary: input.selected.state_summary,
      selected: true,
    },
  ];

  const truthStatus = stringAt(input.acceptedTruth, 'status');
  if (truthStatus && truthStatus !== 'accepted') {
    cards.push({
      id: 'accepted-truth',
      title: `Accepted truth ${truthStatus}`,
      severity: truthStatus === 'missing' ? 'blocked' : 'attention',
      stateSummary: fallbackString(
        stringAt(input.acceptedTruth, 'reason'),
        'Accepted truth still needs operator review.',
      ),
      selected: false,
    });
  }

  const closeoutReview = stringAt(input.closeout, 'review_status');
  const closeoutExecution = stringAt(input.closeout, 'execution_status');
  if (closeoutReview && closeoutReview !== 'accepted') {
    cards.push({
      id: 'closeout',
      title: `Closeout ${closeoutReview}`,
      severity: closeoutExecution === 'failed' ? 'critical' : 'attention',
      stateSummary: `${fallbackString(closeoutExecution, 'unknown')} execution; ${closeoutReview} review.`,
      selected: false,
    });
  }

  const openDecisionCount = numberAt(input.decisions, 'open_count');
  if (openDecisionCount > 0) {
    cards.push({
      id: 'decisions',
      title: `${openDecisionCount} open decisions`,
      severity: 'attention',
      stateSummary: 'Operator judgment is still required before the next autonomous step.',
      selected: false,
    });
  }

  const wikiReviewDue = numberAt(input.adaptiveWiki, 'review_due_count');
  const wikiCandidates = numberAt(input.adaptiveWiki, 'candidate_count');
  const promotionRequired = booleanAt(input.adaptiveWiki, 'promotion_required');
  if (promotionRequired || wikiReviewDue > 0 || wikiCandidates > 0) {
    cards.push({
      id: 'adaptive-wiki',
      title: 'Adaptive wiki review',
      severity: promotionRequired ? 'attention' : 'info',
      stateSummary: `${wikiCandidates} candidates; ${wikiReviewDue} due for review.`,
      selected: false,
    });
  }

  const safeToDelegate = booleanAt(input.implementationPacket, 'safe_to_delegate');
  const revisionCount = arrayAt(input.implementationPacket, 'required_revisions').length;
  if (Object.keys(input.implementationPacket).length > 0 && (!safeToDelegate || revisionCount > 0)) {
    cards.push({
      id: 'implementation-packet',
      title: 'Implementation packet review',
      severity: safeToDelegate ? 'info' : 'blocked',
      stateSummary: `${revisionCount} required revisions before delegation.`,
      selected: false,
    });
  }

  if (cards.every((card) => card.severity === 'ok')) {
    cards[0] = {
      ...cards[0],
      title: 'No operator blockers',
      severity: 'ok',
      stateSummary: input.selected.state_summary,
      selected: true,
    };
  }

  return cards;
}

function countsFromReviewSurface(input: {
  runtime: Record<string, unknown>;
  closeout: Record<string, unknown>;
  decisions: Record<string, unknown>;
  adaptiveWiki: Record<string, unknown>;
  implementationPacket: Record<string, unknown>;
}): Record<string, number> {
  return compactCounts({
    active_runtime: booleanAt(input.runtime, 'active') ? 1 : 0,
    open_decisions: numberAt(input.decisions, 'open_count'),
    closeout_open_decisions: numberAt(input.closeout, 'receipt_open_decisions'),
    unresolved_risks: arrayAt(input.closeout, 'unresolved_risks').length,
    wiki_candidates: numberAt(input.adaptiveWiki, 'candidate_count'),
    wiki_review_due: numberAt(input.adaptiveWiki, 'review_due_count'),
    required_revisions: arrayAt(input.implementationPacket, 'required_revisions').length,
  });
}

function normalizeSeverity(value: string, counts: Record<string, number>): Severity {
  if (KNOWN_SEVERITIES.has(value)) {
    return value as Severity;
  }

  if (value === 'error' || value === 'failed') {
    return 'critical';
  }
  if (value === 'needs_operator' || value === 'requires_review' || value === 'pending') {
    return 'attention';
  }
  if (counts.required_revisions > 0 || counts.unresolved_risks > 0) {
    return 'blocked';
  }
  if (counts.open_decisions > 0 || counts.closeout_open_decisions > 0 || counts.wiki_review_due > 0) {
    return 'attention';
  }
  return 'info';
}

function decisionBlock(
  decisions: Record<string, unknown>,
  acceptedTruth: Record<string, unknown>,
  closeout: Record<string, unknown>,
): OperatorStateCard['primary_blocker_or_decision'] {
  const openCount = numberAt(decisions, 'open_count');
  if (openCount > 0) {
    return {
      label: `${openCount} open decisions`,
      summary: 'Resolve or explicitly defer the open operator decisions before continuing autonomous work.',
    };
  }

  const truthStatus = stringAt(acceptedTruth, 'status');
  if (truthStatus && truthStatus !== 'accepted') {
    return {
      label: `Accepted truth ${truthStatus}`,
      summary: fallbackString(
        stringAt(acceptedTruth, 'reason'),
        'Accepted truth has not been recorded for the latest handoff.',
      ),
    };
  }

  const closeoutReview = stringAt(closeout, 'review_status');
  if (closeoutReview && closeoutReview !== 'accepted') {
    return {
      label: `Closeout ${closeoutReview}`,
      summary: 'Review the latest closeout before treating the autonomous run as accepted.',
    };
  }

  return {
    label: 'Operator review',
    summary: 'No blocking decision was reported by the live review surface.',
  };
}

function countEntriesFromRecord(counts: Record<string, number> | undefined): ReviewCount[] {
  return Object.entries(counts ?? {})
    .filter(([, value]) => Number.isFinite(value) && value > 0)
    .map(([label, value]) => ({ label, value }));
}

function compactCounts(counts: Record<string, number>): Record<string, number> {
  return Object.fromEntries(
    Object.entries(counts).filter(([, value]) => Number.isFinite(value) && value > 0),
  );
}

function titleFromKind(kind: string): string {
  if (!kind) {
    return '';
  }

  return kind.replaceAll('_', ' ');
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

function fallbackString(...values: string[]): string {
  return values.find((value) => value.trim().length > 0) ?? '';
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}
