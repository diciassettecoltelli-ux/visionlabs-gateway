(function () {
  const isStudioRoute = /^\/studio\/?$/.test(window.location.pathname);
  if (!isStudioRoute) {
    return;
  }

  const currentUrl = new URL(window.location.href);
  const checkoutState = currentUrl.searchParams.get("checkout");
  const checkoutSessionId = currentUrl.searchParams.get("session_id");
  if (checkoutState === "success" || checkoutState === "cancel" || checkoutSessionId) {
    const redirectUrl = new URL("/", window.location.origin);
    if (checkoutState) {
      redirectUrl.searchParams.set("checkout", checkoutState);
    }
    if (checkoutSessionId) {
      redirectUrl.searchParams.set("session_id", checkoutSessionId);
    }
    window.location.replace(redirectUrl.toString());
    return;
  }

  const root = document.getElementById("studio-shell-new-root");
  if (!root) {
    return;
  }

  document.documentElement.classList.add("vss-html");
  document.body.classList.add("vss-body");

  const DEFAULT_API_BASE = String(window.VISION_API_BASE || "https://vision-gateway.onrender.com").replace(/\/$/, "");
  const VISION_HISTORY_STORAGE_KEY = "vision_generation_history_v1";
  const VISION_ACCESS_STORAGE_KEY = "vision_access_token";
  const VISION_PENDING_PROMPT_KEY = "vision_pending_prompt";
  const DEFAULT_PACK_ID = "pro";

  const defaultAccess = {
    has_access: false,
    admin: false,
    video_remaining: 0,
    image_remaining: 0,
    access_id: null,
  };

  const defaultUser = {
    authenticated: false,
    user_id: null,
    email: null,
    signup_discount_percent: 20,
  };

  const defaultPack = {
    id: DEFAULT_PACK_ID,
    name: "Vision Pro",
    price_cents: 999,
    currency: "EUR",
  };

  const generationPhases = ["Queued", "Preparing", "Generating", "Finishing", "Ready"];

  const state = {
    mode: "video",
    scene: "idle",
    prompt: "",
    referenceAsset: null,
    access: { ...defaultAccess },
    user: { ...defaultUser },
    packs: [],
    currentPack: { ...defaultPack },
    selectedId: "",
    recents: [],
    currentJob: null,
    currentError: "",
    accountPanelOpen: false,
    authStep: "email",
    authPendingEmail: "",
    authPendingCode: "",
    authNote: "",
    authLoading: false,
    improveLoading: false,
    checkoutLoading: false,
    menuOpenFor: "",
  };

  let pollHandle = null;
  let pendingPollJobId = "";

  const escapeHtml = (value) =>
    String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");

  const parseJsonSafely = async (response) => {
    try {
      return await response.json();
    } catch (error) {
      return null;
    }
  };

  const visionApiUrl = (path) => `${DEFAULT_API_BASE}${path}`;

  const visionAssetUrl = (path) => {
    if (!path) {
      return "";
    }
    if (/^https?:\/\//i.test(path)) {
      return path;
    }
    if (path.startsWith("/")) {
      return `${DEFAULT_API_BASE}${path}`;
    }
    return path;
  };

  const normalizeEmail = (email) => String(email || "").trim().toLowerCase();

  const readStoredAccessToken = () => {
    try {
      return window.localStorage.getItem(VISION_ACCESS_STORAGE_KEY) || "";
    } catch (error) {
      return "";
    }
  };

  const storeAccessToken = (token) => {
    try {
      if (token) {
        window.localStorage.setItem(VISION_ACCESS_STORAGE_KEY, token);
        return;
      }
      window.localStorage.removeItem(VISION_ACCESS_STORAGE_KEY);
    } catch (error) {
      // Ignore storage failures.
    }
  };

  const visionFetch = (path, options) => {
    const opts = options || {};
    const headers = { ...(opts.headers || {}) };
    const token = readStoredAccessToken();
    if (token && !headers.Authorization) {
      headers.Authorization = `Bearer ${token}`;
    }
    return fetch(visionApiUrl(path), {
      credentials: "include",
      ...opts,
      headers,
    });
  };

  const savePendingPrompt = (prompt, mode) => {
    try {
      window.sessionStorage.setItem(
        VISION_PENDING_PROMPT_KEY,
        JSON.stringify({
          prompt: String(prompt || ""),
          mode: mode === "image" ? "image" : "video",
          saved_at: Date.now(),
        }),
      );
    } catch (error) {
      // Ignore storage failures.
    }
  };

  const readPendingPrompt = () => {
    try {
      const raw = window.sessionStorage.getItem(VISION_PENDING_PROMPT_KEY);
      if (!raw) {
        return null;
      }
      const parsed = JSON.parse(raw);
      if (!parsed || !parsed.prompt) {
        return null;
      }
      return {
        prompt: String(parsed.prompt || ""),
        mode: parsed.mode === "image" ? "image" : "video",
      };
    } catch (error) {
      return null;
    }
  };

  const clearPendingPrompt = () => {
    try {
      window.sessionStorage.removeItem(VISION_PENDING_PROMPT_KEY);
    } catch (error) {
      // Ignore storage failures.
    }
  };

  const slugifyPrompt = (prompt) =>
    String(prompt || "")
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "")
      .split("-")
      .filter(Boolean)
      .slice(0, 6)
      .join("-");

  const inferDownloadExtension = (url, outputType) => {
    const fallback = outputType === "image" ? "png" : "mp4";
    if (!url) {
      return fallback;
    }
    try {
      const parsed = new URL(url, window.location.origin);
      const match = parsed.pathname.match(/\.([a-z0-9]+)$/i);
      return match && match[1] ? match[1].toLowerCase() : fallback;
    } catch (error) {
      const fallbackMatch = String(url).match(/\.([a-z0-9]+)(?:\?|#|$)/i);
      return fallbackMatch && fallbackMatch[1] ? fallbackMatch[1].toLowerCase() : fallback;
    }
  };

  const buildDownloadFilename = (item) => {
    const outputType = item.type === "image" ? "image" : "video";
    const base = slugifyPrompt(item.prompt || "") || (outputType === "image" ? "visual" : "render");
    const shortId = String(item.id || "").slice(0, 8);
    const extension = inferDownloadExtension(item.src, outputType);
    return `vision-${outputType}-${base}${shortId ? `-${shortId}` : ""}.${extension}`;
  };

  const formatFileSize = (bytes) => {
    const value = Number(bytes || 0);
    if (!Number.isFinite(value) || value <= 0) {
      return "";
    }
    if (value >= 1024 * 1024) {
      return `${(value / (1024 * 1024)).toFixed(1)} MB`;
    }
    if (value >= 1024) {
      return `${Math.round(value / 1024)} KB`;
    }
    return `${value} B`;
  };

  const clearReferenceAsset = () => {
    if (state.referenceAsset && state.referenceAsset.url) {
      try {
        window.URL.revokeObjectURL(state.referenceAsset.url);
      } catch (error) {
        // Ignore object URL cleanup failures.
      }
    }
    state.referenceAsset = null;
  };

  const setReferenceAsset = (file) => {
    if (!file) {
      return;
    }

    clearReferenceAsset();
    const kind = String(file.type || "").startsWith("video/") ? "video" : "image";
    const url = window.URL.createObjectURL(file);
    state.referenceAsset = {
      name: String(file.name || `${kind}-reference`),
      kind,
      type: String(file.type || ""),
      sizeLabel: formatFileSize(file.size),
      url,
    };
    state.currentError = "";
  };

  const getHistoryStorageKeyForEmail = (email) => {
    const identity = normalizeEmail(email).replace(/[^a-z0-9@._-]+/g, "-") || "guest";
    return `${VISION_HISTORY_STORAGE_KEY}:${identity}`;
  };

  const getHistoryStorageKey = () => getHistoryStorageKeyForEmail(state.user.email || "");

  const readHistoryFromKey = (key) => {
    try {
      const raw = window.localStorage.getItem(key);
      if (!raw) {
        return [];
      }
      const parsed = JSON.parse(raw);
      return Array.isArray(parsed) ? parsed : [];
    } catch (error) {
      return [];
    }
  };

  const writeHistoryToKey = (key, items) => {
    try {
      window.localStorage.setItem(key, JSON.stringify(items));
    } catch (error) {
      // Ignore storage failures.
    }
  };

  const readStudioHistory = () =>
    readHistoryFromKey(getHistoryStorageKey())
      .filter((item) => item && item.id && item.src)
      .map((item) => ({
        ...item,
        src: visionAssetUrl(item.src),
      }));

  const writeStudioHistory = (items) => {
    writeHistoryToKey(getHistoryStorageKey(), items);
  };

  const maybePromoteGuestHistory = () => {
    if (!normalizeEmail(state.user.email)) {
      return;
    }
    const targetKey = getHistoryStorageKey();
    const targetItems = readHistoryFromKey(targetKey);
    if (targetItems.length) {
      return;
    }
    const guestItems = readHistoryFromKey(getHistoryStorageKeyForEmail(""));
    if (!guestItems.length) {
      return;
    }
    writeHistoryToKey(targetKey, guestItems);
  };

  const formatDate = (value) => {
    if (!value) {
      return "Today";
    }
    try {
      return new Intl.DateTimeFormat("en-GB", {
        day: "2-digit",
        month: "short",
      }).format(new Date(value));
    } catch (error) {
      return "Today";
    }
  };

  const formatTime = (value) => {
    if (!value) {
      return "Now";
    }
    try {
      return new Intl.DateTimeFormat("en-GB", {
        hour: "2-digit",
        minute: "2-digit",
      }).format(new Date(value));
    } catch (error) {
      return "Now";
    }
  };

  const summarizePrompt = (prompt, fallback) => {
    const cleaned = String(prompt || "").trim().replace(/\s+/g, " ");
    if (!cleaned) {
      return fallback;
    }
    return cleaned.length > 34 ? `${cleaned.slice(0, 31).trimEnd()}...` : cleaned;
  };

  const getCreditCounts = () => ({
    video: Math.max(0, Number(state.access.video_remaining ?? 0) || 0),
    image: Math.max(0, Number(state.access.image_remaining ?? 0) || 0),
  });

  const hasPackContext = () => !!state.access.admin || !!state.access.access_id || getCreditCounts().video > 0 || getCreditCounts().image > 0;
  const hasAccountContext = () => !!state.user.authenticated || !!state.user.email || hasPackContext();

  const getAccessLabel = () => {
    if (state.access.admin) {
      return "Vision unlocked";
    }
    if (hasPackContext()) {
      const counts = getCreditCounts();
      return `Vision access live · ${counts.video} videos · ${counts.image} images`;
    }
    return "Vision access required";
  };

  const getAccountPillState = () => {
    const counts = getCreditCounts();
    if (!hasAccountContext()) {
      return {
        variant: "guest",
        label: "Access Vision",
        subtitle: "Log in or buy a pack",
      };
    }
    const avatar = state.user.email ? state.user.email.charAt(0).toUpperCase() : "A";
    return {
      variant: "account",
      avatar,
      label: `${counts.video + counts.image}`,
      subtitle: state.access.admin ? "∞" : `${counts.video}·${counts.image}`,
    };
  };

  const syncRecents = () => {
    maybePromoteGuestHistory();
    state.recents = readStudioHistory()
      .slice(0, 12)
      .map((item) => ({
        id: String(item.id),
        kind: item.type === "image" ? "image" : "video",
        src: item.src,
        prompt: String(item.prompt || ""),
        createdAt: item.created_at || "",
      }));

    if (state.selectedId && state.recents.some((item) => item.id === state.selectedId)) {
      return;
    }

    state.selectedId = state.recents[0] ? state.recents[0].id : "";
  };

  const getSelectedRecent = () => state.recents.find((item) => item.id === state.selectedId) || null;

  const saveHistoryItem = (job, src) => {
    if (!job || !job.id || !src) {
      return null;
    }
    const resolvedSrc = visionAssetUrl(src);
    const item = {
      id: String(job.id),
      type: (job.output_type || job.mode || "video").toLowerCase() === "image" ? "image" : "video",
      src: resolvedSrc,
      prompt: String(job.prompt || ""),
      created_at: job.completed_at || job.updated_at || new Date().toISOString(),
    };
    const items = readStudioHistory().filter((entry) => String(entry.id || "") !== item.id);
    items.unshift(item);
    writeStudioHistory(items.slice(0, 16));
    syncRecents();
    state.selectedId = item.id;
    return item;
  };

  const deleteHistoryItem = (id) => {
    const normalizedId = String(id || "").trim();
    if (!normalizedId) {
      return;
    }
    const items = readStudioHistory().filter((entry) => String(entry.id || "") !== normalizedId);
    writeStudioHistory(items);
    syncRecents();
    if (!state.recents.length) {
      state.scene = state.currentJob ? "generating" : "idle";
    } else if (!state.currentJob) {
      state.scene = "result";
    }
  };

  const normalizePackList = (packs) => {
    if (!Array.isArray(packs)) {
      return [];
    }
    return packs.map((pack) => ({
      id: String(pack && pack.id ? pack.id : DEFAULT_PACK_ID).toLowerCase(),
      name: String(pack && pack.name ? pack.name : "Vision Pro"),
      price_cents: Number(pack && pack.price_cents ? pack.price_cents : 999),
      currency: String(pack && pack.currency ? pack.currency : "EUR"),
    }));
  };

  const getPackById = (packId) =>
    state.packs.find((pack) => pack.id === String(packId || "").toLowerCase()) ||
    state.packs[0] || { ...defaultPack };

  const formatPackPrice = (pack) => {
    const amount = Number((pack && pack.price_cents) || 999) / 100;
    const currency = String((pack && pack.currency) || "EUR").toUpperCase();
    try {
      return new Intl.NumberFormat("it-IT", {
        style: "currency",
        currency,
      }).format(amount);
    } catch (error) {
      return `${amount.toFixed(2)} ${currency}`;
    }
  };

  const getJobMode = (job) => (((job && (job.output_type || job.mode)) || state.mode) === "image" ? "image" : "video");

  const getStageCopy = (job) => {
    const status = String((job && job.status) || "queued").toLowerCase();
    const mode = getJobMode(job);
    const specs = mode === "image"
      ? {
          queued: { label: "Queued", start: 0.06, end: 0.16, duration: 5 },
          preparing: { label: "Preparing", start: 0.16, end: 0.34, duration: 8 },
          generating: { label: "Generating", start: 0.34, end: 0.86, duration: 38 },
          downloading: { label: "Finishing", start: 0.86, end: 0.96, duration: 10 },
          ready: { label: "Ready", start: 1, end: 1, duration: 0 },
          failed: { label: "Stopped", start: 1, end: 1, duration: 0 },
        }
      : {
          queued: { label: "Queued", start: 0.05, end: 0.14, duration: 7 },
          preparing: { label: "Preparing", start: 0.14, end: 0.28, duration: 12 },
          generating: { label: "Generating", start: 0.28, end: 0.82, duration: 72 },
          downloading: { label: "Finishing", start: 0.82, end: 0.96, duration: 22 },
          ready: { label: "Ready", start: 1, end: 1, duration: 0 },
          failed: { label: "Stopped", start: 1, end: 1, duration: 0 },
        };
    const stage = specs[status] || specs.generating;
    const phaseIndex = generationPhases.findIndex((entry) => entry.toLowerCase() === stage.label.toLowerCase());
    const startedAt = Date.parse((job && (job.updated_at || job.created_at)) || new Date().toISOString());
    const elapsedStage = Number.isFinite(startedAt) ? Math.max(0, (Date.now() - startedAt) / 1000) : 0;
    const stageProgress = stage.duration ? Math.min(1, elapsedStage / stage.duration) : 1;
    const progress = stage.start + (stage.end - stage.start) * stageProgress;

    const order = ["queued", "preparing", "generating", "downloading"];
    const currentIndex = order.indexOf(status);
    let etaSeconds = Math.max(0, Math.round(stage.duration - elapsedStage));
    if (currentIndex >= 0) {
      for (let index = currentIndex + 1; index < order.length; index += 1) {
        etaSeconds += specs[order[index]].duration;
      }
    }

    const etaLabel = etaSeconds > 0 ? `${Math.floor(etaSeconds / 60)
      .toString()
      .padStart(2, "0")}:${String(etaSeconds % 60).padStart(2, "0")} remaining` : "Finalising now";
    const statusLine = job && job.message ? String(job.message) : stage.label;
    const detailLine =
      status === "generating"
        ? mode === "image"
          ? "Building lighting, texture and still-frame detail."
          : "Building motion, camera and cinematic continuity."
        : status === "preparing"
          ? "Opening the right render lane inside Vision."
          : status === "downloading"
            ? "Importing the result into your Studio canvas."
            : "Queued inside Vision.";

    return {
      phaseIndex: phaseIndex >= 0 ? phaseIndex : 0,
      phaseLabel: stage.label,
      progress,
      etaLabel,
      statusLine,
      detailLine,
    };
  };

  const stopPolling = () => {
    if (pollHandle) {
      window.clearTimeout(pollHandle);
      pollHandle = null;
    }
    pendingPollJobId = "";
  };

  const syncScene = () => {
    if (state.currentJob) {
      const status = String(state.currentJob.status || "").toLowerCase();
      if (["queued", "preparing", "generating", "downloading"].includes(status)) {
        state.scene = "generating";
        return;
      }
    }
    if (getSelectedRecent()) {
      state.scene = "result";
      return;
    }
    state.scene = "idle";
  };

  const refreshAccess = async () => {
    try {
      const response = await visionFetch("/api/access/me");
      if (!response.ok) {
        throw new Error("Vision access unavailable.");
      }
      const payload = await response.json();
      state.user = { ...defaultUser, ...(payload && payload.user ? payload.user : {}) };
      state.access = { ...defaultAccess, ...(payload && payload.access ? payload.access : {}) };
      state.packs = normalizePackList(payload && payload.packs ? payload.packs : []);
      state.currentPack = getPackById((payload && payload.pack && payload.pack.id) || DEFAULT_PACK_ID);
      syncRecents();
      syncScene();
      render();
      return payload;
    } catch (error) {
      state.user = { ...defaultUser };
      state.access = { ...defaultAccess };
      state.packs = [];
      state.currentPack = { ...defaultPack };
      syncRecents();
      syncScene();
      render();
      return null;
    }
  };

  const maybeConfirmCheckout = async () => {
    const url = new URL(window.location.href);
    const sessionId = url.searchParams.get("session_id");
    const checkout = url.searchParams.get("checkout");
    if (!sessionId || checkout !== "success") {
      return false;
    }
    try {
      const response = await visionFetch("/api/checkout/confirm", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ session_id: sessionId }),
      });
      const payload = await parseJsonSafely(response);
      if (!response.ok) {
        throw new Error((payload && (payload.detail || payload.message)) || "Payment confirmation failed.");
      }
      if (payload && payload.access_token) {
        storeAccessToken(payload.access_token);
      }
    } catch (error) {
      state.authNote = error instanceof Error ? error.message : "Payment confirmation failed.";
    } finally {
      url.searchParams.delete("session_id");
      url.searchParams.delete("checkout");
      window.history.replaceState({}, "", `${url.pathname}${url.search}${url.hash}`);
    }
    return true;
  };

  const maybeRestorePendingPrompt = () => {
    const pending = readPendingPrompt();
    if (!pending) {
      return;
    }
    state.prompt = pending.prompt;
    state.mode = pending.mode;
    render();
  };

  const saveGeneratedResult = async (job) => {
    const outputUrl = visionAssetUrl(job.output_url);
    if (!outputUrl) {
      return;
    }
    saveHistoryItem(job, outputUrl);
    state.currentJob = null;
    state.currentError = "";
    syncScene();
    render();
    clearPendingPrompt();
  };

  const handleJobFailure = (job) => {
    state.currentJob = null;
    state.currentError = String((job && (job.error || job.message)) || "Vision could not complete this request.");
    syncScene();
    render();
  };

  const pollJob = async () => {
    if (!pendingPollJobId) {
      return;
    }
    try {
      const response = await visionFetch(`/api/jobs/${pendingPollJobId}`);
      if (!response.ok) {
        throw new Error("Unable to fetch job status.");
      }
      const job = await response.json();
      if (pendingPollJobId !== String(job.id || "")) {
        return;
      }
      state.currentJob = job;
      syncScene();
      render();

      const status = String(job.status || "").toLowerCase();
      if (job.output_url || status === "ready") {
        stopPolling();
        await saveGeneratedResult(job);
        return;
      }
      if (status === "failed" || status === "setup_required") {
        stopPolling();
        handleJobFailure(job);
        return;
      }
      pollHandle = window.setTimeout(pollJob, 2200);
    } catch (error) {
      stopPolling();
      handleJobFailure({
        message: "Vision could not reach the generation engine right now.",
        error: error instanceof Error ? error.message : "Engine unavailable.",
      });
    }
  };

  const handleCheckoutRequired = (detail) => {
    if (detail && detail.access) {
      state.access = { ...defaultAccess, ...detail.access };
    }
    if (detail && detail.packs) {
      state.packs = normalizePackList(detail.packs);
      state.currentPack = getPackById(DEFAULT_PACK_ID);
    }
    state.accountPanelOpen = true;
    state.authStep = "email";
    state.authNote = detail && detail.message ? detail.message : "Unlock a Vision pack to keep creating.";
    state.currentJob = null;
    syncScene();
    render();
  };

  const submitPrompt = async () => {
    const prompt = String(state.prompt || "").trim();
    if (!prompt) {
      return;
    }

    savePendingPrompt(prompt, state.mode);
    state.currentError = "";
    state.currentJob = {
      id: `local-${Date.now()}`,
      prompt,
      status: "queued",
      mode: state.mode,
      output_type: state.mode,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
      message: "Queued inside Vision.",
    };
    state.selectedId = "";
    state.scene = "generating";
    render();

    try {
      const response = await visionFetch("/api/jobs", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          prompt,
          mode: state.mode,
        }),
      });
      const payload = await parseJsonSafely(response);

      if (response.status === 402) {
        handleCheckoutRequired(payload && payload.detail ? payload.detail : null);
        return;
      }

      if (!response.ok || !payload || !payload.id) {
        throw new Error((payload && (payload.detail || payload.message)) || "Vision could not start the generation.");
      }

      stopPolling();
      state.currentJob = payload;
      pendingPollJobId = String(payload.id);
      syncScene();
      render();
      await refreshAccess();
      pollHandle = window.setTimeout(pollJob, 1200);
    } catch (error) {
      stopPolling();
      handleJobFailure({
        message: error instanceof Error ? error.message : "Vision could not start the generation.",
      });
    }
  };

  const requestAccessCode = async () => {
    const email = normalizeEmail(state.authPendingEmail || state.user.email);
    if (!email || email.indexOf("@") === -1) {
      state.authNote = "Enter a valid email address first.";
      render();
      return;
    }
    state.authLoading = true;
    state.authNote = "Sending access code...";
    render();
    try {
      const response = await visionFetch("/api/auth/request-code", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ email }),
      });
      const payload = await parseJsonSafely(response);
      if (!response.ok) {
        throw new Error((payload && (payload.detail || payload.message)) || "Vision could not send the access code.");
      }
      state.authStep = "code";
      state.authPendingEmail = email;
      state.authNote = `We sent a 6-digit Vision access code to ${email}.`;
    } catch (error) {
      state.authNote = error instanceof Error ? error.message : "Vision could not send the access code.";
    } finally {
      state.authLoading = false;
      render();
    }
  };

  const verifyAccessCode = async () => {
    const email = normalizeEmail(state.authPendingEmail);
    const code = String(state.authPendingCode || "").trim();
    if (!email || !code) {
      state.authNote = "Enter the code from your email.";
      render();
      return;
    }
    state.authLoading = true;
    state.authNote = "Verifying...";
    render();
    try {
      const response = await visionFetch("/api/auth/verify-code", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ email, code }),
      });
      const payload = await parseJsonSafely(response);
      if (!response.ok) {
        throw new Error((payload && (payload.detail || payload.message)) || "That access code did not work.");
      }
      if (payload && payload.access_token) {
        storeAccessToken(payload.access_token);
      }
      state.authStep = "account";
      state.authPendingCode = "";
      state.authNote = "";
      await refreshAccess();
      state.accountPanelOpen = false;
      render();
    } catch (error) {
      state.authNote = error instanceof Error ? error.message : "That access code did not work.";
      state.authLoading = false;
      render();
      return;
    }
    state.authLoading = false;
  };

  const logout = async () => {
    state.authLoading = true;
    state.authNote = "Logging out...";
    render();
    try {
      const response = await visionFetch("/api/auth/logout", { method: "POST" });
      const payload = await parseJsonSafely(response);
      if (!response.ok) {
        throw new Error((payload && (payload.detail || payload.message)) || "Vision could not log you out.");
      }
      storeAccessToken("");
      state.user = { ...defaultUser };
      state.access = { ...defaultAccess };
      state.authStep = "email";
      state.authPendingEmail = "";
      state.authPendingCode = "";
      state.accountPanelOpen = false;
      syncRecents();
      syncScene();
    } catch (error) {
      state.authNote = error instanceof Error ? error.message : "Vision could not log you out.";
    } finally {
      state.authLoading = false;
      render();
    }
  };

  const openCheckout = async () => {
    const email = normalizeEmail(state.user.email || state.authPendingEmail);
    if (!email || email.indexOf("@") === -1) {
      state.authNote = "Enter an email before opening secure checkout.";
      render();
      return;
    }
    state.checkoutLoading = true;
    state.authNote = "Opening secure checkout...";
    render();
    try {
      const response = await visionFetch("/api/checkout/session", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          email,
          pack_id: (state.currentPack && state.currentPack.id) || DEFAULT_PACK_ID,
        }),
      });
      const payload = await parseJsonSafely(response);
      if (!response.ok || !payload || !payload.url) {
        throw new Error((payload && (payload.detail || payload.message)) || "Checkout is not configured yet.");
      }
      window.location.assign(payload.url);
    } catch (error) {
      state.checkoutLoading = false;
      state.authNote = error instanceof Error ? error.message : "Checkout could not start.";
      render();
    }
  };

  const improvePrompt = async () => {
    const prompt = String(state.prompt || "").trim();
    if (!prompt) {
      return;
    }
    state.improveLoading = true;
    render();
    try {
      const response = await visionFetch("/api/prompt/improve", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          prompt,
          mode: state.mode,
        }),
      });
      const payload = await parseJsonSafely(response);
      if (!response.ok || !payload || !payload.improved_prompt) {
        throw new Error((payload && (payload.detail || payload.message)) || "Vision could not improve this prompt.");
      }
      state.prompt = String(payload.improved_prompt || prompt);
    } catch (error) {
      state.currentError = error instanceof Error ? error.message : "Vision could not improve this prompt.";
    } finally {
      state.improveLoading = false;
      render();
    }
  };

  const renderHeader = () => {
    const pill = getAccountPillState();
    return `
      <header class="vss-header">
        <div class="vss-brand-cluster">
          <a class="vss-brand" href="/" aria-label="Return to Vision home">
            <span class="vss-brand-mark" aria-hidden="true"><img class="vss-brand-mark-image" src="/brand-logo.svg?v=2" alt="" /></span>
            <span class="vss-brand-name">Vision</span>
          </a>
          <a class="vss-home-link" href="/">Back home</a>
        </div>
        <div class="vss-domain">visionstudiolab.com</div>
        <button class="vss-account-pill${pill.variant === "guest" ? " is-guest" : ""}" id="vss-account-pill" type="button" aria-label="${pill.variant === "guest" ? "Access Vision" : "Vision account and credits"}" aria-haspopup="dialog" aria-expanded="${state.accountPanelOpen ? "true" : "false"}">
          ${
            pill.variant === "guest"
              ? `<span class="vss-account-guest-copy">
                  <span class="vss-account-guest-label">${escapeHtml(pill.label)}</span>
                  <span class="vss-account-guest-note">${escapeHtml(pill.subtitle)}</span>
                </span>`
              : `<span class="vss-account-avatar">${escapeHtml(pill.avatar)}</span>
                 <span class="vss-account-metric">${escapeHtml(pill.label)}</span>
                 <span class="vss-account-metric">${escapeHtml(pill.subtitle)}</span>`
          }
          <span class="vss-account-chevron">⌄</span>
        </button>
      </header>
    `;
  };

  const renderCanvasMedia = () => {
    if (state.scene === "result" && getSelectedRecent()) {
      const item = getSelectedRecent();
      const fullscreenLabel = item.kind === "video" ? "Open full screen" : "Open image";
      const media =
        item.kind === "video"
          ? `<video class="vss-canvas-video" src="${escapeHtml(item.src)}" autoplay muted loop playsinline></video>`
          : `<img class="vss-canvas-image" src="${escapeHtml(item.src)}" alt="${escapeHtml(summarizePrompt(item.prompt, "Vision still"))}" />`;
      return `
        <div class="vss-canvas-media">
          ${media}
          <div class="vss-canvas-scrim"></div>
          <div class="vss-canvas-actions" aria-label="Current result actions">
            <a class="vss-canvas-action" href="${escapeHtml(item.src)}" target="_blank" rel="noreferrer noopener">${escapeHtml(fullscreenLabel)}</a>
            <a class="vss-canvas-action" href="${escapeHtml(item.src)}" download="${escapeHtml(buildDownloadFilename(item))}">Download</a>
          </div>
          <div class="vss-canvas-result-meta">
            <p class="vss-result-label">${item.kind === "video" ? "Latest video" : "Latest image"}</p>
            <h2 class="vss-result-title">${escapeHtml(summarizePrompt(item.prompt, item.kind === "video" ? "Vision render" : "Vision still"))}</h2>
            <p class="vss-result-caption">${escapeHtml(item.prompt || "Generated inside Vision.")}</p>
          </div>
        </div>
      `;
    }

    if (state.scene === "generating" && state.currentJob) {
      const stage = getStageCopy(state.currentJob);
      return `
        <div class="vss-canvas-loading">
          <div class="vss-loading-card">
            <div class="vss-loading-kicker">
              <span class="vss-loading-dots"><span></span><span></span><span></span></span>
              <span>${escapeHtml(stage.phaseLabel)}</span>
            </div>
            <div class="vss-loading-title">Generating inside Vision</div>
            <div class="vss-loading-meta">
              <span>${escapeHtml(stage.etaLabel)}</span>
              <span>•</span>
              <span>${escapeHtml(stage.detailLine)}</span>
            </div>
            <div class="vss-loading-progress"><span style="width:${Math.round(stage.progress * 100)}%"></span></div>
            <div class="vss-loading-phases">
              ${generationPhases
                .map(
                  (phase, index) =>
                    `<span class="vss-loading-phase${index === stage.phaseIndex ? " is-active" : ""}">${escapeHtml(phase)}</span>`,
                )
                .join("")}
            </div>
          </div>
        </div>
      `;
    }

    if (state.currentError) {
      return `
        <div class="vss-canvas-empty">
          <div class="vss-canvas-empty-copy">
            <div class="vss-canvas-empty-label">Vision paused</div>
            <p class="vss-empty-note">${escapeHtml(state.currentError)}</p>
          </div>
        </div>
      `;
    }

    if (state.referenceAsset) {
      const referenceMedia =
        state.referenceAsset.kind === "video"
          ? `<video class="vss-canvas-video" src="${escapeHtml(state.referenceAsset.url)}" autoplay muted loop playsinline></video>`
          : `<img class="vss-canvas-image" src="${escapeHtml(state.referenceAsset.url)}" alt="${escapeHtml(state.referenceAsset.name || "Reference asset")}" />`;
      return `
        <div class="vss-canvas-media vss-canvas-media--reference">
          ${referenceMedia}
          <div class="vss-canvas-scrim"></div>
          <div class="vss-canvas-reference-meta">
            <p class="vss-result-label">Reference ready</p>
            <h2 class="vss-result-title">${escapeHtml(state.referenceAsset.name)}</h2>
            <p class="vss-result-caption">Loaded in Studio and ready for prompt-led refinement. Replace it any time from the + button.</p>
          </div>
        </div>
      `;
    }

    return `
      <div class="vss-canvas-empty">
        <div class="vss-canvas-empty-copy">
          <div class="vss-canvas-empty-label">Describe your video or image</div>
          <div class="vss-canvas-empty-caret" aria-hidden="true"></div>
        </div>
      </div>
    `;
  };

  const renderCanvas = () => `
    <section class="vss-stage">
      <div class="vss-canvas">
        ${renderCanvasMedia()}
      </div>
      ${renderDock()}
    </section>
  `;

  const renderDock = () => `
    <div class="vss-dock">
      <input class="vss-hidden" id="vss-reference-input" type="file" accept="image/*,video/mp4,video/webm,video/quicktime" />
      <form class="vss-prompt-bar" id="vss-prompt-form">
        <button class="vss-add-ref" type="button" aria-label="Upload image or short video reference">+</button>
        <input
          class="vss-prompt-input"
          id="vss-prompt-input"
          type="text"
          placeholder="Describe your video or image..."
          value="${escapeHtml(state.prompt)}"
        />
        <button class="vss-submit" type="submit" aria-label="Generate">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <path d="M7 12h10"></path>
            <path d="m13 6 6 6-6 6"></path>
          </svg>
        </button>
      </form>
      ${
        state.referenceAsset
          ? `<div class="vss-reference-row">
              <span class="vss-reference-chip">
                <span class="vss-reference-chip-label">${escapeHtml(state.referenceAsset.kind === "video" ? "Video reference" : "Image reference")}</span>
                <strong>${escapeHtml(state.referenceAsset.name)}</strong>
                <span>${escapeHtml(state.referenceAsset.sizeLabel || "")}</span>
              </span>
              <button class="vss-reference-clear" id="vss-reference-clear" type="button">Remove</button>
            </div>`
          : ""
      }
      <div class="vss-dock-footer">
        <div class="vss-mode-row">
          <div class="vss-mode-switch" role="tablist" aria-label="Mode switch">
            <button type="button" data-mode="video" class="${state.mode === "video" ? "is-active" : ""}">Video</button>
            <button type="button" data-mode="image" class="${state.mode === "image" ? "is-active" : ""}">Image</button>
          </div>
          <span class="vss-mode-separator" aria-hidden="true"></span>
          <span class="vss-mode-access">${escapeHtml(getAccessLabel())}</span>
        </div>
        <button class="vss-improve${state.prompt.trim() ? "" : " is-disabled"}" id="vss-improve-button" type="button" ${state.prompt.trim() ? "" : "disabled aria-disabled=\"true\""}>${state.improveLoading ? "Improving..." : "Improve Prompt"}</button>
      </div>
    </div>
  `;

  const renderRecentMenu = (item) => {
    const isOpen = state.menuOpenFor === item.id;
    if (!isOpen) {
      return "";
    }
    return `
      <div class="vss-recent-popover" data-menu-panel="${escapeHtml(item.id)}">
        <a href="${escapeHtml(item.src)}" download="${escapeHtml(buildDownloadFilename(item))}" class="vss-recent-popover-action">Download</a>
        <button class="vss-recent-popover-action vss-recent-popover-action--danger" type="button" data-delete-id="${escapeHtml(item.id)}">Delete</button>
      </div>
    `;
  };

  const renderRecents = () => `
    <aside class="vss-rail">
      <div class="vss-rail-inner">
        <div class="vss-rail-head">
          <div class="vss-rail-kicker">Recents</div>
          <button class="vss-view-all" type="button">View all →</button>
        </div>
        ${
          state.recents.length
            ? `<div class="vss-recent-list">
                ${state.recents
                  .map((item) => {
                    const isSelected = state.selectedId === item.id;
                    const media =
                      item.kind === "video"
                        ? `<video src="${escapeHtml(item.src)}" muted loop playsinline autoplay></video>`
                        : `<img src="${escapeHtml(item.src)}" alt="${escapeHtml(summarizePrompt(item.prompt, "Vision still"))}" />`;
                    return `
                      <article class="vss-recent-card${isSelected ? " is-selected" : ""}">
                        <button class="vss-recent-select" type="button" data-recent-id="${escapeHtml(item.id)}">
                          <div class="vss-recent-thumb">
                            ${media}
                            <span class="vss-recent-duration">${item.kind === "video" ? "Video" : "Image"}</span>
                            <span class="vss-recent-overlay" aria-hidden="true"></span>
                          </div>
                          <div class="vss-recent-meta">
                            <div>
                              <p class="vss-recent-title">${escapeHtml(summarizePrompt(item.prompt, item.kind === "video" ? "Vision render" : "Vision still"))}</p>
                              <span class="vss-recent-date">${escapeHtml(`${formatDate(item.createdAt)} · ${formatTime(item.createdAt)}`)}</span>
                            </div>
                            <button class="vss-recent-menu-button" type="button" data-menu-id="${escapeHtml(item.id)}" aria-label="More actions">•••</button>
                          </div>
                        </button>
                        ${renderRecentMenu(item)}
                      </article>
                    `;
                  })
                  .join("")}
              </div>`
            : `<div class="vss-rail-empty">
                <p class="vss-rail-empty-title">No recents yet</p>
                <p class="vss-rail-empty-copy">Your latest Vision outputs will collect here after the first generation.</p>
              </div>`
        }
      </div>
    </aside>
  `;

  const renderAccountPanel = () => {
    if (!state.accountPanelOpen) {
      return "";
    }

    const counts = getCreditCounts();
    const signedIn = hasAccountContext();
    const showAccount = signedIn && state.authStep !== "code";

    return `
      <div class="vss-modal-backdrop" id="vss-modal-backdrop">
        <div class="vss-modal-panel" role="dialog" aria-modal="true" aria-labelledby="vss-account-title">
          <button class="vss-modal-close" id="vss-modal-close" type="button" aria-label="Close panel">Close</button>
          <div class="vss-modal-kicker">${showAccount ? "Vision / Account" : "Vision / Access"}</div>
          <h2 class="vss-modal-title" id="vss-account-title">${showAccount ? "Your Vision account." : "Access your Vision pack."}</h2>
          <p class="vss-modal-copy">
            ${
              showAccount
                ? "See your remaining credits and return to your Studio from any device."
                : "Enter your email and Vision will send a one-time access code, or open secure checkout for a new pack."
            }
          </p>
          ${
            showAccount
              ? `<div class="vss-account-panel">
                  <div class="vss-account-row">
                    <span class="vss-account-label">Email</span>
                    <strong>${escapeHtml(state.user.email || "Vision account")}</strong>
                  </div>
                  <div class="vss-account-grid">
                    <div class="vss-account-tile">
                      <span>Video credits</span>
                      <strong>${state.access.admin ? "∞" : counts.video}</strong>
                    </div>
                    <div class="vss-account-tile">
                      <span>Image credits</span>
                      <strong>${state.access.admin ? "∞" : counts.image}</strong>
                    </div>
                  </div>
                  <div class="vss-modal-actions">
                    ${state.access.admin ? "" : `<button class="vss-modal-primary" id="vss-buy-pack" type="button">${state.checkoutLoading ? "Opening..." : `Buy ${escapeHtml(state.currentPack.name || "Vision pack")}`}</button>`}
                    <button class="vss-modal-secondary" id="vss-logout" type="button">${state.authLoading ? "Logging out..." : "Log out"}</button>
                  </div>
                </div>`
              : `<form class="vss-access-form" id="vss-auth-form">
                  <label class="vss-form-label" for="vss-auth-email">Email</label>
                  <input class="vss-form-input" id="vss-auth-email" type="email" value="${escapeHtml(state.authPendingEmail)}" placeholder="you@example.com" autocomplete="email" />
                  ${
                    state.authStep === "code"
                      ? `<label class="vss-form-label" for="vss-auth-code">Code</label>
                         <input class="vss-form-input" id="vss-auth-code" type="text" value="${escapeHtml(state.authPendingCode)}" inputmode="numeric" autocomplete="one-time-code" placeholder="6-digit code" />`
                      : ""
                  }
                  <div class="vss-modal-actions">
                    <button class="vss-modal-primary" id="vss-auth-submit" type="submit">${state.authLoading ? "Please wait..." : state.authStep === "code" ? "Continue to Vision" : "Send access code"}</button>
                    <button class="vss-modal-secondary" id="vss-buy-pack" type="button">${state.checkoutLoading ? "Opening..." : `Buy ${escapeHtml(state.currentPack.name || "Vision pack")}`}</button>
                  </div>
                </form>`
          }
          <p class="vss-modal-note">${escapeHtml(state.authNote || (showAccount ? "Your pack follows you anywhere you sign into Vision." : "We’ll send a one-time access code so your pack follows you when you come back."))}</p>
        </div>
      </div>
    `;
  };

  const render = () => {
    root.innerHTML = `
      <div class="vss-shell">
        <div class="vss-app">
          ${renderHeader()}
          <main class="vss-main">
            ${renderCanvas()}
            ${renderRecents()}
          </main>
          ${renderAccountPanel()}
        </div>
      </div>
    `;
    bind();
  };

  const bind = () => {
    const promptInput = root.querySelector("#vss-prompt-input");
    const promptForm = root.querySelector("#vss-prompt-form");
    const improveButton = root.querySelector("#vss-improve-button");
    const referenceInput = root.querySelector("#vss-reference-input");
    const addReferenceButton = root.querySelector(".vss-add-ref");
    const clearReferenceButton = root.querySelector("#vss-reference-clear");

    promptInput?.addEventListener("input", (event) => {
      state.prompt = String(event.target.value || "");
    });

    promptForm?.addEventListener("submit", (event) => {
      event.preventDefault();
      state.prompt = String(promptInput && promptInput.value ? promptInput.value : state.prompt || "");
      submitPrompt();
    });

    improveButton?.addEventListener("click", () => {
      improvePrompt();
    });

    addReferenceButton?.addEventListener("click", () => {
      referenceInput?.click();
    });

    referenceInput?.addEventListener("change", (event) => {
      const [file] = Array.from(event.target.files || []);
      if (!file) {
        return;
      }
      setReferenceAsset(file);
      event.target.value = "";
      render();
    });

    clearReferenceButton?.addEventListener("click", () => {
      clearReferenceAsset();
      render();
    });

    root.querySelectorAll("[data-mode]").forEach((button) => {
      button.addEventListener("click", () => {
        state.mode = button.getAttribute("data-mode") === "image" ? "image" : "video";
        render();
      });
    });

    root.querySelectorAll("[data-recent-id]").forEach((button) => {
      button.addEventListener("click", () => {
        state.selectedId = button.getAttribute("data-recent-id") || "";
        state.menuOpenFor = "";
        state.currentError = "";
        syncScene();
        render();
      });
    });

    root.querySelectorAll("[data-menu-id]").forEach((button) => {
      button.addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        const menuId = button.getAttribute("data-menu-id") || "";
        state.menuOpenFor = state.menuOpenFor === menuId ? "" : menuId;
        render();
      });
    });

    root.querySelectorAll("[data-delete-id]").forEach((button) => {
      button.addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        const deleteId = button.getAttribute("data-delete-id") || "";
        state.menuOpenFor = "";
        deleteHistoryItem(deleteId);
        render();
      });
    });

    root.querySelector("#vss-account-pill")?.addEventListener("click", () => {
      state.accountPanelOpen = true;
      state.authStep = hasAccountContext() ? "account" : "email";
      state.authPendingEmail = state.user.email || state.authPendingEmail || "";
      render();
    });

    root.querySelector("#vss-modal-close")?.addEventListener("click", () => {
      state.accountPanelOpen = false;
      state.menuOpenFor = "";
      render();
    });

    root.querySelector("#vss-modal-backdrop")?.addEventListener("click", (event) => {
      if (event.target && event.target.id === "vss-modal-backdrop") {
        state.accountPanelOpen = false;
        render();
      }
    });

    root.querySelector("#vss-auth-email")?.addEventListener("input", (event) => {
      state.authPendingEmail = String(event.target.value || "");
    });

    root.querySelector("#vss-auth-code")?.addEventListener("input", (event) => {
      state.authPendingCode = String(event.target.value || "");
    });

    root.querySelector("#vss-auth-form")?.addEventListener("submit", (event) => {
      event.preventDefault();
      if (state.authStep === "code") {
        verifyAccessCode();
        return;
      }
      requestAccessCode();
    });

    root.querySelector("#vss-buy-pack")?.addEventListener("click", () => {
      openCheckout();
    });

    root.querySelector("#vss-logout")?.addEventListener("click", () => {
      logout();
    });
  };

  const init = async () => {
    render();
    await maybeConfirmCheckout();
    await refreshAccess();
    maybeRestorePendingPrompt();
    syncScene();
    render();
    window.__visionStudioShellReady = true;
    window.dispatchEvent(new CustomEvent("vision-studio-shell-ready"));
  };

  init();
})();
