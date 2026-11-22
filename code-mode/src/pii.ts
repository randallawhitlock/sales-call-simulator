/**
 * Reversible PII tokenization at the MCP boundary.
 *
 * Tool results are tokenized before they reach sandbox code (and therefore the model):
 *   "jane@acme.com" -> "[EMAIL_1]"
 * Tool arguments are detokenized on the way out, so real values still reach the
 * destination system. The mapping lives only in the host process.
 */

const DEFAULT_PATTERNS: Record<string, RegExp> = {
  EMAIL: /[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/g,
  // US/intl phone numbers: +1 (555) 123-4567, 555-123-4567, 555.123.4567, +44 20 7946 0958
  PHONE: /(?<![\w-])(?:\+\d{1,3}[\s.-]?)?(?:\(\d{2,4}\)|\d{2,4})[\s.-]\d{3,4}[\s.-]\d{3,4}(?![\w-])/g,
};

const TOKEN_RE = /\[([A-Z][A-Z0-9_]*)_(\d+)\]/g;

export class Tokenizer {
  private patterns: [string, RegExp][];
  private toToken = new Map<string, string>();
  private toValue = new Map<string, string>();
  private counters = new Map<string, number>();
  readonly enabled: boolean;

  constructor(opts: { enabled?: boolean; patterns?: Record<string, string> } = {}) {
    this.enabled = opts.enabled ?? true;
    const extra = Object.entries(opts.patterns ?? {}).map(([k, src]) => [k, new RegExp(src, "g")] as [string, RegExp]);
    this.patterns = [...Object.entries(DEFAULT_PATTERNS), ...extra];
  }

  private tokenFor(label: string, value: string): string {
    const existing = this.toToken.get(value);
    if (existing) return existing;
    const n = (this.counters.get(label) ?? 0) + 1;
    this.counters.set(label, n);
    const token = `[${label}_${n}]`;
    this.toToken.set(value, token);
    this.toValue.set(token, value);
    return token;
  }

  tokenizeString(s: string): string {
    if (!this.enabled) return s;
    let out = s;
    for (const [label, re] of this.patterns) {
      out = out.replace(re, (m) => this.tokenFor(label, m));
    }
    return out;
  }

  detokenizeString(s: string): string {
    if (!this.enabled) return s;
    return s.replace(TOKEN_RE, (m) => this.toValue.get(m) ?? m);
  }

  tokenize<T>(value: T): T {
    return walk(value, (s) => this.tokenizeString(s));
  }

  detokenize<T>(value: T): T {
    return walk(value, (s) => this.detokenizeString(s));
  }

  get size(): number {
    return this.toValue.size;
  }
}

function walk<T>(value: T, fn: (s: string) => string): T {
  if (typeof value === "string") return fn(value) as T;
  if (Array.isArray(value)) return value.map((v) => walk(v, fn)) as T;
  if (value && typeof value === "object") {
    const out: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(value)) out[k] = walk(v, fn);
    return out as T;
  }
  return value;
}
