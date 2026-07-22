import type {
  AssistantAction,
  AssistantPrompt,
  AssistantRef,
} from './dashboardSurface';

export type AssistantReadoutOptions = {
  answer: string;
  refs: AssistantRef[];
  actions: AssistantAction[];
  prompts: AssistantPrompt[];
  answerAttrs: string[];
  refsAttr: string;
  actionsAttr: string;
  promptsAttr: string;
  copyPromptAttr: string;
  copyStatusAttr: string;
  copyAriaPrefix: string;
  boundaryText: string;
};

export type AssistantCopyBinding = {
  copyPromptAttr: string;
  copyStatusAttr: string;
};

export function assistantReadoutHtml(options: AssistantReadoutOptions): string {
  return `<p class="mt-3 text-sm leading-relaxed text-slate-200" ${dataAttrs(options.answerAttrs)}>
      ${escapeHtml(options.answer)}
    </p>
    <div class="mt-4 space-y-2" ${dataAttr(options.refsAttr)}>
      ${options.refs.map(assistantRefHtml).join('')}
    </div>
    <div class="mt-4 space-y-2" ${dataAttr(options.actionsAttr)}>
      ${options.actions.map(assistantActionHtml).join('')}
    </div>
    <div class="mt-4 flex flex-wrap gap-2" ${dataAttr(options.promptsAttr)}>
      ${options.prompts.map((prompt) => assistantPromptHtml(prompt, options)).join('')}
    </div>
    <p class="mt-3 text-xs text-brand-200" ${dataAttr(options.copyStatusAttr)} aria-live="polite"></p>
    <p class="mt-4 text-xs leading-relaxed text-slate-500">
      ${escapeHtml(options.boundaryText)}
    </p>`;
}

export function bindAssistantPromptCopies(root: ParentNode, options: AssistantCopyBinding): void {
  const status = root.querySelector(`[${options.copyStatusAttr}]`);
  root.querySelectorAll(`[${options.copyPromptAttr}]`).forEach((button) => {
    button.addEventListener('click', async () => {
      const prompt = button.getAttribute(options.copyPromptAttr) ?? '';
      try {
        await navigator.clipboard?.writeText(prompt);
        if (status) {
          status.textContent = 'Prompt copied.';
        }
      } catch {
        if (status) {
          status.textContent = prompt ? `Prompt ready: ${prompt}` : 'Prompt unavailable.';
        }
      }
    });
  });
}

function assistantRefHtml(ref: AssistantRef): string {
  return `<div class="border border-slate-700 bg-slate-950/35 px-3 py-2">
    <div class="flex flex-wrap items-center justify-between gap-2">
      <span class="text-xs font-semibold text-slate-100">${escapeHtml(ref.label)}</span>
      <span class="font-mono text-[10px] uppercase tracking-[0.1em] text-slate-500">${escapeHtml(formatLabel(ref.trust))}</span>
    </div>
    <p class="mt-1 break-all font-mono text-[10px] text-slate-400">${escapeHtml(ref.reference)}</p>
  </div>`;
}

function assistantActionHtml(action: AssistantAction): string {
  return `<article class="border border-brand-300/25 bg-brand-300/10 px-3 py-2">
    <div class="flex flex-wrap items-center justify-between gap-2">
      <span class="text-xs font-semibold text-brand-100">${escapeHtml(action.label)}</span>
      <span class="font-mono text-[10px] uppercase tracking-[0.1em] text-brand-200">${escapeHtml(formatLabel(action.kind))}</span>
    </div>
    <p class="mt-1 text-xs leading-relaxed text-slate-300">${escapeHtml(action.boundary)}</p>
    <p class="mt-2 break-all font-mono text-[10px] text-slate-500">${escapeHtml(action.target_ref)}</p>
  </article>`;
}

function assistantPromptHtml(prompt: AssistantPrompt, options: AssistantReadoutOptions): string {
  return `<button
    class="border border-slate-600 px-3 py-2 text-xs font-semibold text-slate-200 hover:border-brand-300/70 hover:text-white"
    type="button"
    ${dataAttr(options.copyPromptAttr)}="${escapeHtml(prompt.prompt)}"
    aria-label="${escapeHtml(`${options.copyAriaPrefix}: ${prompt.label}`)}"
  >
    ${escapeHtml(prompt.label)}
  </button>`;
}

function formatLabel(label: string): string {
  return label.replaceAll('_', ' ');
}

function dataAttrs(names: string[]): string {
  return names.map(dataAttr).join(' ');
}

function dataAttr(name: string): string {
  if (!/^data-[a-z0-9-]+$/.test(name)) {
    throw new Error(`invalid data attribute: ${name}`);
  }
  return name;
}

function escapeHtml(value: unknown): string {
  return String(value ?? '').replace(/[&<>"']/g, (char) => {
    switch (char) {
      case '&':
        return '&amp;';
      case '<':
        return '&lt;';
      case '>':
        return '&gt;';
      case '"':
        return '&quot;';
      case "'":
        return '&#39;';
      default:
        return char;
    }
  });
}
