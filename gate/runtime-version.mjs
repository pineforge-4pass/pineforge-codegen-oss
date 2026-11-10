// Pure validation of exact runtime, target, and peer version identities.
const NUMBER = "(?:0|[1-9][0-9]*)";
const PRERELEASE = "(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*)";
const VERSION_TOKEN = new RegExp(
  `^${NUMBER}\\.${NUMBER}\\.${NUMBER}(?:-${PRERELEASE}(?:\\.${PRERELEASE})*)?(?:\\+[0-9A-Za-z-]+(?:\\.[0-9A-Za-z-]+)*)?$`,
);

export function checkRuntimeVersion(observed, target, peer) {
  const invalid = [];
  for (const [name, value] of [["observed", observed], ["target", target], ["peer", peer]]) {
    if (typeof value !== "string") {
      invalid.push(`${name}=<${value === null ? "null" : typeof value}> (expected an exact version string)`);
    } else if (/\s/.test(value) || !VERSION_TOKEN.test(value)) {
      invalid.push(`${name}=${JSON.stringify(value)} (expected an exact version string)`);
    }
  }
  if (invalid.length) {
    throw new Error(`Pyodide version identity invalid: ${invalid.join("; ")}`);
  }
  if (observed !== target || observed !== peer) {
    throw new Error(
      `Pyodide version identity mismatch: observed=${JSON.stringify(observed)}, target=${JSON.stringify(target)}, peer=${JSON.stringify(peer)}`,
    );
  }
  return observed;
}
