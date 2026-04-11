import fs from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { fileURLToPath } from "node:url";

const FORMATTER_BUNDLE_URL =
  "https://s15-kling.klingai.com/kos/s101/nlav112918/kling-web/assets/js/formatter-zn7YLI44.js";
const SCRIPT_DIR = path.dirname(fileURLToPath(import.meta.url));
const CACHE_ROOT =
  process.env.VISION_KLING_SIG4_CACHE_ROOT ||
  path.resolve(SCRIPT_DIR, "..", ".runtime", "kling_sig4_bundle");

function setGlobal(name, value) {
  try {
    Object.defineProperty(globalThis, name, {
      value,
      configurable: true,
      writable: true,
    });
  } catch {
    globalThis[name] = value;
  }
}

function ensureBrowserShims() {
  setGlobal("window", globalThis);
  setGlobal("self", globalThis);
  setGlobal("document", {
    cookie: "",
    createElement: () => ({}),
    addEventListener() {},
    removeEventListener() {},
    body: {},
    documentElement: {},
  });
  setGlobal("localStorage", {
    getItem() {
      return null;
    },
    setItem() {},
    removeItem() {},
    clear() {},
  });
  setGlobal("sessionStorage", {
    getItem() {
      return null;
    },
    setItem() {},
    removeItem() {},
    clear() {},
  });
  setGlobal("navigator", {
    userAgent: "vision-kling-session-bridge",
    sendBeacon() {
      return true;
    },
  });
  setGlobal("location", {
    href: "https://kling.ai/app/omni/new",
    origin: "https://kling.ai",
    pathname: "/app/omni/new",
  });
  setGlobal("matchMedia", () => ({
    matches: false,
    addListener() {},
    removeListener() {},
    addEventListener() {},
    removeEventListener() {},
  }));
}

async function fetchText(url) {
  const res = await fetch(url, {
    headers: { "user-agent": "vision-kling-session-bridge" },
  });
  if (!res.ok) {
    throw new Error(`Failed to fetch ${url}: ${res.status} ${res.statusText}`);
  }
  return await res.text();
}

function discoverFormatterContract(formatterText) {
  const vendorMatch = formatterText.match(
    /import\{[^}]*aq as x9[^}]*\}from"\.\/([^"]+\.js)"/
  );
  const projectMatch = formatterText.match(
    /sig4:\{projectInfo:\{appKey:"([^"]+)",radarId:"([^"]+)",debug:(!0|!1)\}\}/
  );
  if (!vendorMatch) {
    throw new Error("Could not discover Kling vendor bundle from formatter.");
  }
  if (!projectMatch) {
    throw new Error("Could not discover Kling sig4 projectInfo from formatter.");
  }
  return {
    vendorRel: vendorMatch[1],
    appKey: projectMatch[1],
    radarId: projectMatch[2],
    debug: projectMatch[3] === "!0",
  };
}

async function downloadModuleTree(entryUrl, cacheDir) {
  const queue = [entryUrl];
  const seen = new Set();

  while (queue.length) {
    const url = queue.shift();
    if (!url || seen.has(url)) continue;
    seen.add(url);

    const text = await fetchText(url);
    const filePath = path.join(cacheDir, path.basename(url));
    await fs.writeFile(filePath, text, "utf8");

    const importRes = [
      ...text.matchAll(/from\s*"\.\/([^"]+\.js)"/g),
      ...text.matchAll(/import\s*"\.\/([^"]+\.js)"/g),
    ];
    for (const match of importRes) {
      const child = new URL(match[1], url).toString();
      if (!seen.has(child)) queue.push(child);
    }
  }
}

async function loadJose() {
  await fs.mkdir(CACHE_ROOT, { recursive: true });
  const formatterText = await fetchText(FORMATTER_BUNDLE_URL);
  const contract = discoverFormatterContract(formatterText);
  const vendorUrl = new URL(contract.vendorRel, FORMATTER_BUNDLE_URL).toString();
  await downloadModuleTree(vendorUrl, CACHE_ROOT);

  ensureBrowserShims();
  if (!globalThis.crypto) {
    const nodeCrypto = await import("node:crypto");
    setGlobal("crypto", nodeCrypto.webcrypto);
  }
  const mod = await import(
    pathToFileURL(path.join(CACHE_ROOT, path.basename(vendorUrl))).href
  );
  if (!mod.aq || typeof mod.aq.call !== "function") {
    throw new Error("Kling Jose runtime is unavailable in vendor bundle.");
  }
  return { Jose: mod.aq, contract };
}

async function signPayload(input) {
  const { Jose, contract } = await loadJose();
  const caver = Jose.call("$getCatVersion") || "2";
  const payload = {
    ...input,
    query: { ...(input.query || {}), caver },
    projectInfo: {
      appKey: contract.appKey,
      radarId: contract.radarId,
      debug: contract.debug,
      ...(input.projectInfo || {}),
    },
  };
  const signResult = await new Promise((resolve, reject) => {
    Jose.call("$encode", [
      payload,
      {
        suc(value) {
          resolve(value);
        },
        err(error) {
          reject(error instanceof Error ? error : new Error(String(error)));
        },
      },
    ]);
  });
  return {
    caver,
    signResult,
    projectInfo: payload.projectInfo,
    payload,
  };
}

async function readStdin() {
  return await new Promise((resolve, reject) => {
    let out = "";
    process.stdin.setEncoding("utf8");
    process.stdin.on("data", (chunk) => {
      out += chunk;
    });
    process.stdin.on("end", () => resolve(out));
    process.stdin.on("error", reject);
  });
}

async function main() {
  const raw = await readStdin();
  const input = raw.trim() ? JSON.parse(raw) : { url: "/api/task/submit", query: {}, requestBody: {} };
  const result = await signPayload(input);
  process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
}

main().catch((error) => {
  console.error(error?.stack || error?.message || String(error));
  process.exit(1);
});
