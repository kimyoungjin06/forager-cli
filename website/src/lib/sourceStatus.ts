export type SourceStatusKind =
  | 'live'
  | 'fixture_fallback'
  | 'live_fetch_failed'
  | 'stale_live'
  | 'missing_surface_url';

export const sourceStatusLabels: Record<SourceStatusKind, string> = {
  live: 'Live source loaded',
  fixture_fallback: 'Fixture fallback',
  live_fetch_failed: 'Live source failed',
  stale_live: 'Live source stale',
  missing_surface_url: 'Live source not configured',
};

export const sourceStatusClasses: Record<SourceStatusKind, string> = {
  live: 'border-emerald-400/35 bg-emerald-400/10 text-emerald-100',
  fixture_fallback: 'border-slate-600 bg-slate-900/75 text-slate-200',
  live_fetch_failed: 'border-amber-300/45 bg-amber-300/10 text-amber-100',
  stale_live: 'border-amber-300/45 bg-amber-300/10 text-amber-100',
  missing_surface_url: 'border-slate-600 bg-slate-900/75 text-slate-200',
};

