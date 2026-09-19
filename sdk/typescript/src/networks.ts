/**
 * Network presets. MONOCLE defaults to GenLayer Studio Next.
 *
 * Studio Next is a browser alias (https://studio-next.genlayer.com) for the
 * Studio development preview. Programmatic clients use its canonical RPC,
 * https://studio-dev.genlayer.com/api, chain 61997 -- exactly genlayer-js
 * 2.0 RC's `studioDevnet`, which we reuse and only relabel. There is no
 * second chain: never point this at studionet (61999) or Bradbury.
 */
import { localnet, studioDevnet } from "genlayer-js/chains";

export const STUDIO_NEXT_CHAIN_ID = 61997;
export const STUDIO_NEXT_RPC = "https://studio-dev.genlayer.com/api";
export const STUDIO_NEXT_BROWSER_URL = "https://studio-next.genlayer.com";
export const STUDIO_NEXT_EXPLORER = "https://explorer-studio-dev.genlayer.com";

export const studioNext = {
  ...studioDevnet,
  id: STUDIO_NEXT_CHAIN_ID,
  name: "GenLayer Studio Next",
  rpcUrls: { default: { http: [STUDIO_NEXT_RPC] } },
  blockExplorers: { default: { name: "GenLayer Studio Next Explorer", url: STUDIO_NEXT_EXPLORER } },
} as typeof studioDevnet;

export type NetworkName = "studio-next" | "localnet";

export const NETWORKS = {
  "studio-next": studioNext,
  localnet,
} as const;

export function resolveNetwork(name: string | undefined) {
  const key = (name ?? "studio-next") as NetworkName;
  const chain = NETWORKS[key];
  if (!chain) {
    throw new Error(`Unknown network "${name}". Valid: ${Object.keys(NETWORKS).join(", ")}`);
  }
  return chain;
}

/**
 * consensusMaxRotations for the two heavy calls (multi-source web fetch +
 * multi-stage LLM verdict, re-run by every validator). The Studio presets
 * default to 3; a leader rotation is how GenLayer recovers from one
 * validator's flaky fetch or malformed model output (MONOCLE's validator
 * deliberately disagrees on LLM_MALFORMED to force one), so we allow more.
 */
export const HEAVY_CONSENSUS_ROTATIONS = 6;
