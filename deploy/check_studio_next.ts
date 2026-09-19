/**
 * Health check for the deployed MONOCLE system on Studio Next.
 *
 *   npx tsx deploy/check_studio_next.ts [studio-next|localnet] [factoryAddress]
 *
 * Reads only; needs no key. Exits 0 only if the chain id matches, the
 * factory has code and every factory view answers.
 */
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { createReadClient, FactoryCalls, MonocleCalls, resolveNetwork, type Address } from "../sdk/typescript/src/index.js";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");

type Line = [label: string, ok: boolean, detail: string];

async function rpc(url: string, method: string, params: unknown[]) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ jsonrpc: "2.0", id: 1, method, params }),
  });
  const body = (await res.json()) as { result?: unknown; error?: { message?: string } };
  if (body.error) throw new Error(body.error.message ?? "RPC error");
  return body.result;
}

async function main() {
  const networkName = process.argv[2] ?? "studio-next";
  const chain = resolveNetwork(networkName);
  const url = chain.rpcUrls.default.http[0];
  const record = JSON.parse(readFileSync(join(ROOT, "deploy", "deployments.json"), "utf-8"))[networkName] ?? {};
  const factory = (process.argv[3] ?? record.factory) as Address | undefined;
  const lines: Line[] = [];
  const add = (label: string, ok: boolean, detail: string) => lines.push([label, ok, detail]);

  try {
    const id = Number.parseInt(String(await rpc(url, "eth_chainId", [])), 16);
    add("RPC", true, url);
    add("Chain id", id === chain.id, `${id} (expected ${chain.id})`);
  } catch (err) {
    add("RPC", false, `${url}: ${(err as Error).message}`);
  }

  if (!factory) {
    add("Factory", false, `no factory recorded for ${networkName}`);
  } else {
    const client = createReadClient(chain);
    // GenLayer contracts are not EVM bytecode: eth_getCode is empty for them
    // on Studio Next. getContractCode (gen_getContractCode) is the real check.
    const hasCode = async (a: Address) => {
      const code = await client.getContractCode(a).catch(() => "");
      return typeof code === "string" && code.length > 0;
    };
    const f = new FactoryCalls(client, factory);
    let viewsOk = true;
    const view = async <T,>(label: string, fn: () => Promise<T>) => {
      try {
        const v = await fn();
        add(label, true, typeof v === "string" ? v : JSON.stringify(v));
        return v;
      } catch (err) {
        viewsOk = false;
        add(label, false, (err as Error).message.split("\n")[0]);
        return undefined;
      }
    };
    const factoryCode = await hasCode(factory);
    const count = await view("get_monocles_count", () => f.getMonoclesCount());
    await view("get_creation_stake", () => f.getCreationStake());
    await view("get_bond_config", () => f.getBondConfig());
    const rep = await view("get_reputation_address", () => f.getReputationAddress());
    add("Factory code", factoryCode && viewsOk, `${factory} (code ${factoryCode ? "present" : "MISSING"}, views ${viewsOk ? "ok" : "failing"})`);
    if (rep) {
      const repFactory = await client
        .readContract({ address: rep as Address, functionName: "get_factory", args: [] })
        .catch((err: Error) => `ERR ${err.message.split("\n")[0]}`);
      add("Reputation", String(repFactory).toLowerCase() === factory.toLowerCase(), `${rep} -> factory ${repFactory}`);
    }
    if (record.testMonocle) {
      const m = new MonocleCalls(client, record.testMonocle);
      const info = await m.getInfo().catch(() => null);
      add(
        "Seeded market",
        Boolean(info),
        info
          ? `${record.testMonocle} round ${info.current_round} ${info.current_round_status}, window ${info.challenge_window_seconds ?? "3600"}s`
          : `${record.testMonocle} unreadable`,
      );
    }
    if (typeof count === "number") add("Monocles", true, String(count));
  }

  const width = Math.max(...lines.map(([l]) => l.length));
  console.log(`MONOCLE health · ${chain.name} (${chain.id})`);
  for (const [label, ok, detail] of lines) console.log(`${ok ? "OK  " : "FAIL"} ${label.padEnd(width)}  ${detail}`);
  const critical = lines.filter(([l]) => ["RPC", "Chain id", "Factory", "Factory code", "get_monocles_count", "get_creation_stake", "get_bond_config", "get_reputation_address"].includes(l));
  const healthy = critical.length > 0 && critical.every(([, ok]) => ok);
  console.log(healthy ? "HEALTHY" : "UNHEALTHY");
  process.exit(healthy ? 0 : 1);
}

main().catch((err) => {
  console.error(err instanceof Error ? err.message : err);
  process.exit(1);
});
