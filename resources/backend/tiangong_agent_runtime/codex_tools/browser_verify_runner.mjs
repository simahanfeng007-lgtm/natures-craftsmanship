import { createRequire } from "node:module";
import fs from "node:fs/promises";
import path from "node:path";

function print(payload) {
  process.stdout.write(JSON.stringify(payload, null, 2));
}

async function loadRequest() {
  const requestPath = process.argv[2];
  if (!requestPath) {
    throw new Error("missing request path");
  }
  const text = await fs.readFile(requestPath, "utf8");
  return JSON.parse(text);
}

async function loadPlaywright(workspace) {
  const packagePath = path.join(workspace, "package.json");
  const requireFromWorkspace = createRequire(packagePath);
  try {
    return requireFromWorkspace("playwright");
  } catch (error) {
    try {
      return await import("playwright");
    } catch {
      const err = new Error(error?.message || "playwright module not found");
      err.code = "PLAYWRIGHT_NOT_FOUND";
      throw err;
    }
  }
}

async function runAction(page, action) {
  const type = String(action?.type || action?.action || "").trim().toLowerCase();
  const selector = action?.selector;
  const value = action?.value ?? action?.text ?? "";
  const timeout = Number(action?.timeout || 10000);
  if (!type) return { ok: false, error: "missing action type" };
  if (type === "wait") {
    await page.waitForTimeout(Number(action?.ms || value || 500));
    return { ok: true, type };
  }
  if (type === "wait_for_selector" || type === "waitforselector") {
    await page.waitForSelector(selector, { timeout });
    return { ok: true, type, selector };
  }
  if (type === "click") {
    await page.click(selector, { timeout });
    return { ok: true, type, selector };
  }
  if (type === "fill" || type === "type") {
    await page.fill(selector, String(value), { timeout });
    return { ok: true, type, selector };
  }
  if (type === "press") {
    await page.press(selector, String(value), { timeout });
    return { ok: true, type, selector, value };
  }
  if (type === "evaluate") {
    const result = await page.evaluate(String(value));
    return { ok: true, type, result };
  }
  if (type === "screenshot") {
    const screenshotPath = action?.path;
    if (!screenshotPath) return { ok: false, type, error: "screenshot action requires path" };
    await page.screenshot({ path: screenshotPath, fullPage: Boolean(action?.fullPage ?? true) });
    return { ok: true, type, path: screenshotPath };
  }
  return { ok: false, type, error: `unknown action: ${type}` };
}

async function collectDom(page) {
  return await page.evaluate(() => {
    const text = (document.body?.innerText || "").replace(/\s+/g, " ").trim();
    const meta = Array.from(document.querySelectorAll("meta[name], meta[property]"))
      .slice(0, 20)
      .map((node) => ({
        name: node.getAttribute("name") || node.getAttribute("property") || "",
        content: node.getAttribute("content") || "",
      }));
    return {
      title: document.title || "",
      location: location.href,
      body_text_sample: text.slice(0, 2000),
      counts: {
        links: document.querySelectorAll("a").length,
        buttons: document.querySelectorAll("button,[role=button]").length,
        inputs: document.querySelectorAll("input,textarea,select").length,
        forms: document.querySelectorAll("form").length,
        images: document.querySelectorAll("img").length,
      },
      headings: Array.from(document.querySelectorAll("h1,h2,h3"))
        .slice(0, 20)
        .map((node) => ({ tag: node.tagName.toLowerCase(), text: (node.textContent || "").trim().slice(0, 160) })),
      meta,
    };
  });
}

async function main() {
  const request = await loadRequest();
  const workspace = request.workspace || process.cwd();
  let playwright;
  try {
    playwright = await loadPlaywright(workspace);
  } catch (error) {
    print({
      schema: "tiangong.codex.browser_verify.v1",
      ok: false,
      status: "dependency_missing",
      missing: "playwright",
      error: error?.message || String(error),
      install_hint: "npm install -D playwright && npx playwright install chromium",
    });
    return;
  }

  const consoleMessages = [];
  const pageErrors = [];
  const requestFailures = [];
  const actionResults = [];
  let browser;
  try {
    browser = await playwright.chromium.launch({ headless: true });
    const context = await browser.newContext({
      viewport: request.viewport || { width: 1366, height: 768 },
      ignoreHTTPSErrors: true,
    });
    const page = await context.newPage();
    page.on("console", (msg) => {
      consoleMessages.push({
        type: msg.type(),
        text: msg.text().slice(0, 1000),
        location: msg.location(),
      });
    });
    page.on("pageerror", (error) => {
      pageErrors.push({ message: error.message, stack: String(error.stack || "").slice(0, 2000) });
    });
    page.on("requestfailed", (req) => {
      requestFailures.push({
        url: req.url(),
        method: req.method(),
        failure: req.failure()?.errorText || "",
      });
    });
    const response = await page.goto(request.url, {
      waitUntil: request.waitUntil || "domcontentloaded",
      timeout: Number(request.timeout || 30000),
    });
    for (const action of request.actions || []) {
      try {
        actionResults.push(await runAction(page, action));
      } catch (error) {
        actionResults.push({ ok: false, type: action?.type || action?.action || "", error: error?.message || String(error) });
      }
    }
    const dom = request.collectDom === false ? null : await collectDom(page);
    let screenshotPath = "";
    if (request.screenshotPath) {
      await page.screenshot({ path: request.screenshotPath, fullPage: Boolean(request.fullPage ?? true) });
      screenshotPath = request.screenshotPath;
    }
    await context.close();
    print({
      schema: "tiangong.codex.browser_verify.v1",
      ok: pageErrors.length === 0,
      status: pageErrors.length === 0 ? "verified" : "page_errors",
      url: page.url(),
      http_status: response ? response.status() : null,
      console_messages: consoleMessages.slice(0, 80),
      console_error_count: consoleMessages.filter((item) => item.type === "error").length,
      page_errors: pageErrors.slice(0, 20),
      request_failures: requestFailures.slice(0, 40),
      action_results: actionResults,
      dom,
      screenshot_path: screenshotPath,
    });
  } catch (error) {
    print({
      schema: "tiangong.codex.browser_verify.v1",
      ok: false,
      status: "verification_failed",
      error: error?.message || String(error),
      console_messages: consoleMessages.slice(0, 80),
      page_errors: pageErrors.slice(0, 20),
      request_failures: requestFailures.slice(0, 40),
      action_results: actionResults,
    });
  } finally {
    if (browser) {
      await browser.close().catch(() => {});
    }
  }
}

main().catch((error) => {
  print({
    schema: "tiangong.codex.browser_verify.v1",
    ok: false,
    status: "runner_failed",
    error: error?.message || String(error),
  });
  process.exitCode = 1;
});

