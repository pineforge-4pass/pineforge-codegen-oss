import { spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';

export const CODEGEN_DOCS_RULE = 'codegen-docs-only/v1';
export const CODEGEN_DOCS_BASIS = 'docs-only';
export const CODEGEN_BASE_BRANCH = 'main';

function canonical(value) {
  if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`;
  if (value !== null && typeof value === 'object') {
    return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonical(value[key])}`).join(',')}}`;
  }
  return JSON.stringify(value);
}

// The proof judges actual commit objects: no replace refs, grafts or commit-graph (a forged graph can rewrite
// parents), and the graft hint stays off so a failing call shows its real cause.
const GIT_FLAGS = ['-c', 'core.commitGraph=false', '-c', 'advice.graftFileDeprecated=false'];

function git(dir, args, { binary = false } = {}) {
  const result = spawnSync('git', ['-C', dir, ...GIT_FLAGS, ...args], {
    encoding: binary ? null : 'utf8', timeout: 10_000, maxBuffer: 32 * 1024 * 1024,
    env: { ...process.env, GIT_OPTIONAL_LOCKS: '0', GIT_NO_REPLACE_OBJECTS: '1', GIT_GRAFT_FILE: '/dev/null' },
  });
  if (result.error || result.status !== 0) {
    throw new Error(`cannot read the codegen diff: ${result.error?.message ?? result.stderr.toString().trim()}; fetch origin main and the head into the codegen checkout`);
  }
  return result.stdout;
}

export function codegenCommit(dir, ref) {
  if (typeof ref !== 'string' || !ref || ref.startsWith('-')) throw new Error('codegen ref must name a commit');
  const commit = git(dir, ['rev-parse', '--verify', `${ref}^{commit}`]).trim();
  if (!/^[a-f0-9]{40}$/.test(commit)) throw new Error('codegen ref did not resolve to a full commit');
  return commit;
}

// A control or line-separator character in a path never qualifies, and is shown escaped in a refusal.
const CONTROL = /[\p{Cc}\p{Zl}\p{Zp}]/u;
const escapeControls = (text) => text.replace(/[\p{Cc}\p{Zl}\p{Zp}]/gu, (ch) => `\\u${ch.codePointAt(0).toString(16).padStart(4, '0')}`);

// The build reads these: pyproject.toml names README.md and LICENSE, and the npm package copies LICENSE. An edit of
// either is documentation; deleting it, renaming it away or changing its type is not.
export const CODEGEN_BUILD_READ_DOCS = Object.freeze(['README.md', 'LICENSE']);
const buildReadChange = (path, status) => CODEGEN_BUILD_READ_DOCS.includes(path) && status !== 'M';

export function codegenDocsCategory(path) {
  if (typeof path !== 'string' || !path || path.startsWith('/') || CONTROL.test(path)
    || path.split('/').some((part) => !part || part === '.' || part === '..')) return null;
  if (/\.(?:md|markdown)$/i.test(path)) return 'markdown';
  if (path.startsWith('docs/')) return 'docs';
  if (/^(?:LICENSE|NOTICE)$/.test(path.split('/').at(-1))) return 'legal';
  return null;
}

export function codegenDocsDiff(dir, { base, head, baseBranch = CODEGEN_BASE_BRANCH }) {
  if (baseBranch !== CODEGEN_BASE_BRANCH) throw new Error('the codegen docs basis judges only PRs into main');
  const baseCommit = codegenCommit(dir, base);
  const headCommit = codegenCommit(dir, head);
  const tree = git(dir, ['rev-parse', `${headCommit}^{tree}`]).trim();
  if (git(dir, ['rev-parse', '--is-shallow-repository']).trim() === 'true') {
    return {
      rule: CODEGEN_DOCS_RULE, accepted: false, baseBranch, base: baseCommit,
      mergeBase: null, head: headCommit, tree, diffSha256: null, files: [],
      reasons: ['shallow repository: commit ancestry is incomplete; fetch --unshallow before judging docs, or run the full pr-gate'],
    };
  }
  const mergeBases = git(dir, ['merge-base', '--all', baseCommit, headCommit]).trim().split('\n');
  if (mergeBases.length !== 1) {
    return {
      rule: CODEGEN_DOCS_RULE, accepted: false, baseBranch, base: baseCommit,
      mergeBase: null, mergeBases, head: headCommit, tree, diffSha256: null, files: [],
      reasons: ['multiple merge bases: the documentation diff is ambiguous; full pr-gate required'],
    };
  }
  const mergeBase = mergeBases[0];
  const rawBytes = git(dir, ['diff', '--raw', '--no-abbrev', '--no-renames', '--no-ext-diff', '--no-textconv',
    '--no-relative', '--ignore-submodules=none', '-z', mergeBase, headCommit], { binary: true });
  let raw;
  try { raw = new TextDecoder('utf-8', { fatal: true }).decode(rawBytes); }
  catch { throw new Error('codegen diff paths must be valid UTF-8 for an auditable docs receipt; full pr-gate required'); }
  const fields = raw.split('\0');
  const files = [];
  for (let offset = 0; offset < fields.length - 1; offset += 2) {
    const match = /^:(\d{6}) (\d{6}) ([a-f0-9]{40}) ([a-f0-9]{40}) ([ADMT])$/.exec(fields[offset]);
    if (!match || typeof fields[offset + 1] !== 'string') throw new Error('unrecognised codegen raw diff entry');
    const [, oldMode, newMode, oldSha, newSha, status] = match;
    const path = fields[offset + 1];
    const category = codegenDocsCategory(path);
    const regular = [oldMode, newMode].every((mode) => mode === '000000' || mode === '100644');
    files.push({ path, oldMode, newMode, oldSha, newSha, status, category,
      allowed: regular && category !== null && !buildReadChange(path, status) });
  }
  const reasons = files.filter((file) => !file.allowed).map((file) => {
    if (CONTROL.test(file.path)) return `${escapeControls(JSON.stringify(file.path))}: a control character in the path needs the full gate`;
    if (buildReadChange(file.path, file.status)) return `${file.path}: the build reads it (pyproject.toml, the npm package); only an edit of it is documentation`;
    return `${file.path}: outside the documentation/legal rule (or not a regular non-executable file)`;
  });
  if (!files.length) reasons.push('no changed files: the base already contains the head, or the diff is empty');
  return {
    rule: CODEGEN_DOCS_RULE, accepted: reasons.length === 0, baseBranch, base: baseCommit,
    mergeBase, head: headCommit, tree,
    diffSha256: createHash('sha256').update(rawBytes).digest('hex'), files, reasons,
  };
}

export function codegenDocsDigest(receipt) {
  return createHash('sha256').update(canonical({
    schemaVersion: 'pineforge-codegen-docs-gate-verdict/v1',
    basis: receipt.basis, verdict: receipt.verdict,
    engineCommit: receipt.engineCommit, codegenCommit: receipt.codegenCommit,
    populationSha256: receipt.populationSha256 ?? null,
    baselineSnapshotSha256: receipt.baselineSnapshotSha256 ?? null,
    candidateSnapshotSha256: receipt.candidateSnapshotSha256 ?? null,
    diff: receipt.diff,
  })).digest('hex');
}

export function codegenDocsProblems(receipt, diff) {
  const problems = [];
  const files = receipt.diff?.files;
  if (receipt.basis !== CODEGEN_DOCS_BASIS || receipt.verdict !== 'PASS') problems.push('not a docs-only PASS');
  if (receipt.diff?.rule !== CODEGEN_DOCS_RULE || receipt.diff?.accepted !== true) problems.push('unknown or refused docs rule');
  if (receipt.populationSha256 != null || receipt.baselineSnapshotSha256 != null || receipt.candidateSnapshotSha256 != null) problems.push('a docs-only receipt must name no measured population or snapshot');
  if (!/^[a-f0-9]{40}$/.test(receipt.engineCommit ?? '') || !/^[a-f0-9]{40}$/.test(receipt.codegenCommit ?? '')) problems.push('invalid bound commits');
  if (receipt.diff?.head !== receipt.codegenCommit) problems.push('the docs diff names another head');
  if (receipt.diff?.baseBranch !== CODEGEN_BASE_BRANCH || !/^[a-f0-9]{40}$/.test(receipt.diff?.base ?? '')
    || !/^[a-f0-9]{40}$/.test(receipt.diff?.mergeBase ?? '') || !/^[a-f0-9]{40}$/.test(receipt.diff?.tree ?? '')
    || !/^[a-f0-9]{64}$/.test(receipt.diff?.diffSha256 ?? '') || !Array.isArray(files)
    || !files.length || files.some((file) => !file || file.allowed !== true || !codegenDocsCategory(file.path)
      || buildReadChange(file.path, file.status)
      || file.category !== codegenDocsCategory(file.path) || !/^[ADMT]$/.test(file.status ?? '')
      || !/^[a-f0-9]{40}$/.test(file.oldSha ?? '') || !/^[a-f0-9]{40}$/.test(file.newSha ?? '')
      || ![file.oldMode, file.newMode].every((mode) => mode === '000000' || mode === '100644'))
    || !Array.isArray(receipt.diff?.reasons) || receipt.diff.reasons.length) problems.push('incomplete or non-documentation diff evidence');
  if (!problems.length) {
    const raw = files.map((file) => `:${file.oldMode} ${file.newMode} ${file.oldSha} ${file.newSha} ${file.status}\0${file.path}\0`).join('');
    if (new Set(files.map((file) => file.path)).size !== files.length
      || createHash('sha256').update(raw).digest('hex') !== receipt.diff.diffSha256) problems.push('the raw diff hash does not match its file evidence');
  }
  if (codegenDocsDigest(receipt) !== receipt.verdictSha256) problems.push('docs receipt does not match its verdict digest');
  if (diff && (!diff.accepted || canonical(diff) !== canonical(receipt.diff))) problems.push('the current main/base, tree or diff no longer matches the docs receipt');
  return problems;
}

export async function corroborateCodegenDocs(pool, { receipt, eventKey, experimentId }) {
  const { rows } = await pool.query(
    `SELECT payload_json FROM airflow_experiment_ledger
      WHERE experiment_id = $1 AND event_key = $2 AND event_type = 'experiment_verdict'`,
    [experimentId, eventKey],
  );
  const payload = typeof rows[0]?.payload_json === 'string' ? JSON.parse(rows[0].payload_json) : rows[0]?.payload_json;
  return Boolean(payload && payload.verdictSha256 === receipt.verdictSha256
    && codegenDocsProblems(payload).length === 0 && codegenDocsDigest(payload) === codegenDocsDigest(receipt));
}
