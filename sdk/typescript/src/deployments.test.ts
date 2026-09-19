import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import { deploymentDefaults } from "./deployments.js";

const RECORDS_URL = new URL("../../../deploy/deployments.json", import.meta.url);

test("the committed deployments.json is valid: empty before a deploy, complete after", () => {
  const records = JSON.parse(readFileSync(RECORDS_URL, "utf-8"));
  const d = deploymentDefaults(records, "studio-next");
  if (!records["studio-next"]) {
    // Fresh repo: nothing deployed yet, and nothing fabricated.
    assert.equal(d.factory, "");
    assert.equal(d.seeded, null);
    return;
  }
  assert.equal(d.chainId, 61997);
  assert.match(d.factory, /^0x[0-9a-fA-F]{40}$/);
  assert.match(d.reputation, /^0x[0-9a-fA-F]{40}$/);
  assert.ok(d.challengeWindowSeconds >= 60 && d.challengeWindowSeconds <= 7 * 86400);
  if (d.seeded) assert.equal(d.testMonocle, d.seeded.address);
});

test("malformed or missing records never produce a fake address", () => {
  const d = deploymentDefaults({ "studio-next": { factory: "0xnope", chainId: 61997, testMonocle: 42 } });
  assert.equal(d.factory, "");
  assert.equal(d.testMonocle, "");
  assert.equal(d.seeded, null);
  assert.equal(deploymentDefaults(null).factory, "");
  assert.equal(deploymentDefaults({}, "localnet").chainId, 0);
});

test("window falls back to 3600 and seeded market fields are normalized", () => {
  const d = deploymentDefaults({
    "studio-next": {
      chainId: 61997,
      factory: "0x" + "ab".repeat(20),
      testMonocle: "0x" + "cd".repeat(20),
      submitTxs: ["0x1", "0x2"],
    },
  });
  assert.equal(d.challengeWindowSeconds, 3600);
  assert.deepEqual(d.seeded?.submitTxs, ["0x1", "0x2"]);
  assert.equal(d.seeded?.createTx, "");
});
