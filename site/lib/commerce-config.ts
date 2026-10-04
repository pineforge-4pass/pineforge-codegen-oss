// The typed commerce config for the app and the Functions (bundlers inline
// the JSON). Node scripts read config/commerce.json with fs instead.
import raw from "../config/commerce.json";
import type { CommerceConfig } from "./commerce.ts";

export const commerce = raw as unknown as CommerceConfig;
