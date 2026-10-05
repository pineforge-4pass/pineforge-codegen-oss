import { appendFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { codegenDocsDiff } from '../src/codegen-docs-rule.mjs';

export function codegenDocsCheck({ dir, base, head, baseBranch = 'main' }, { out = process.stdout, outputPath = process.env.GITHUB_OUTPUT } = {}) {
  const diff = codegenDocsDiff(dir, { base, head, baseBranch });
  const proof = { schemaVersion: 'pineforge-codegen-docs-ci/v1', checks: { preflight: 'PASS', docs: diff.accepted ? 'PASS' : 'full-gate-required' }, diff };
  if (outputPath) appendFileSync(outputPath, `docs_only=${diff.accepted}\nrule=${diff.rule}\ndiff_sha256=${diff.diffSha256 ?? ''}\n`);
  out.write(`${JSON.stringify(proof)}\n`);
  return proof;
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    const [dir, base, head, baseBranch = 'main'] = process.argv.slice(2);
    if (!dir || !base || !head) throw new Error('usage: codegen-docs-check.mjs <checkout> <base-sha> <head-sha> [base-branch]');
    codegenDocsCheck({ dir, base, head, baseBranch });
  } catch (error) {
    process.stderr.write(`codegen docs preflight failed: ${error.message}\n`);
    process.exitCode = 1;
  }
}
