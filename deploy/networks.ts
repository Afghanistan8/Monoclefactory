/**
 * Deploy-time network table. Re-exports the SDK presets so the deploy
 * script and every agent resolve Studio Next identically.
 *
 * Default: Studio Next (chain 61997, canonical RPC studio-dev.genlayer.com/api).
 * Also: localnet (61127) for GenLayer Studio / GLSim running locally.
 * Deliberately absent: studionet (61999) and Bradbury -- the v0.6 RC
 * tooling family must not be mixed with the stable Studionet stack.
 */
export {
  NETWORKS,
  resolveNetwork,
  studioNext,
  STUDIO_NEXT_BROWSER_URL,
  STUDIO_NEXT_CHAIN_ID,
  STUDIO_NEXT_EXPLORER,
  STUDIO_NEXT_RPC,
  type NetworkName,
} from "../sdk/typescript/src/networks.js";
