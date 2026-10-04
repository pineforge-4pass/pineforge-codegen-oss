// Generates an Ed25519 license-signing key pair for a new key id (rotation,
// or a separate preview/test key).
//
//   node scripts/generate-signing-key.mjs <kid>
//
// Prints the PRIVATE JWK (set it as the LICENSE_SIGNING_KEY secret, e.g. by
// pasting it into `wrangler pages secret put LICENSE_SIGNING_KEY`; never
// commit it) and the PUBLIC JWK to append to keys/license-public-keys.json.
// Keep old public keys in the keyring so licenses they signed stay verifiable.
//
//   --private-only   print only the private JWK (for piping into wrangler)
const args = process.argv.slice(2);
const privateOnly = args.includes("--private-only");
const kid = args.find((a) => !a.startsWith("--"));
if (!kid || !/^[a-z0-9][a-z0-9-]{2,63}$/.test(kid)) {
  console.error("usage: node scripts/generate-signing-key.mjs <kid> [--private-only]   (e.g. pfl-live-2027-10)");
  process.exit(2);
}
const { publicKey, privateKey } = await crypto.subtle.generateKey({ name: "Ed25519" }, true, ["sign", "verify"]);
const priv = await crypto.subtle.exportKey("jwk", privateKey);
const pub = await crypto.subtle.exportKey("jwk", publicKey);
const privateJwk = { kty: "OKP", crv: "Ed25519", x: priv.x, d: priv.d, kid };
const publicJwk = { kid, alg: "EdDSA", crv: "Ed25519", x: pub.x, kty: "OKP", use: "sig" };

if (privateOnly) {
  process.stdout.write(JSON.stringify(privateJwk));
} else {
  console.log("PRIVATE key (LICENSE_SIGNING_KEY secret; keep it out of the repository):");
  console.log(JSON.stringify(privateJwk));
  console.log("");
  console.log("PUBLIC key (append to keys/license-public-keys.json \"keys\"):");
  console.log(JSON.stringify(publicJwk));
}
