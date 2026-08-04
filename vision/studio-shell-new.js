(function () {
  const isStudioRoute = /^\/studio\/?$/.test(window.location.pathname);
  if (!isStudioRoute) {
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
  const VISION_USER_STORAGE_KEY = "vision_user_token";
  const VISION_PENDING_PROMPT_KEY = "vision_pending_prompt";
  const VISION_ASSET_CACHE_DB = "vision_asset_cache_v1";
  const VISION_ASSET_CACHE_STORE = "assets";
  const DEFAULT_PACK_ID = "studio";
  const VISION_DURATION_OPTIONS = [3, 5, 10, 15];
  const VISION_RESOLUTION_OPTIONS = ["1080p", "4k"];
  const VISION_ASPECT_RATIO_OPTIONS = ["1:1", "16:9", "9:16", "4:5", "3:4", "4:3", "3:2"];
  const VISION_ASPECT_RATIO_LABELS = {
    "1:1": "Square 1:1",
    "16:9": "Landscape 16:9",
    "9:16": "Portrait 9:16",
    "4:5": "Portrait 4:5",
    "3:4": "Portrait 3:4",
    "4:3": "Landscape 4:3",
    "3:2": "Landscape 3:2",
  };

  const trackVisionEvent = (name, payload = {}) =>
    window.VisionTracking && typeof window.VisionTracking.trackEvent === "function"
      ? window.VisionTracking.trackEvent(name, payload)
      : null;

  const getVisionTrackingContext = (overrides = {}) =>
    window.VisionTracking && typeof window.VisionTracking.getContext === "function"
      ? window.VisionTracking.getContext(overrides)
      : { ...overrides };

  const defaultAccess = {
    has_access: false,
    admin: false,
    vision_credits_remaining: 0,
    vision_credits_purchased: 0,
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

  const defaultSubscription = {
    status: "none",
    active: false,
    cancel_at_period_end: false,
    current_period_end: null,
  };

  const defaultPack = {
    id: DEFAULT_PACK_ID,
    name: "Vision Studio",
    price_cents: 99,
    original_price_cents: 99,
    currency: "EUR",
    vision_credits: 0,
    credit_label: "Unlimited 4K images",
    total_credit_label: "Unlimited 4K images every month",
    discount_label: "",
  };

  const defaultViewer = {
    open: false,
    kind: "image",
    assetPath: "",
    assetUrl: "",
    title: "",
    caption: "",
  };

  const generationPhases = ["Queued", "Preparing", "Generating", "Finishing", "Ready"];

  const state = {
    mode: "image",
    durationSeconds: 5,
    resolution: "4k",
    aspectRatio: "16:9",
    soundEnabled: false,
    scene: "idle",
    prompt: "",
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
    portalLoading: false,
    subscription: { ...defaultSubscription },
    menuOpenFor: "",
    menuAnchor: null,
    assetStatusByPath: {},
    assetObjectUrlsByPath: {},
    selectionSource: "auto",
    recentValidationPending: false,
    viewer: { ...defaultViewer },
  };

  let pollHandle = null;
  let pendingPollJobId = "";
  const pendingAssetChecks = new Map();
  let recentsValidationToken = 0;
  let assetCacheDbPromise = null;

  const escapeHtml = (value) =>
    String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");

  const normalizeDurationSeconds = (value) => {
    const requested = Number(value || 5);
    if (requested <= 3) {
      return 3;
    }
    if (requested <= 5) {
      return 5;
    }
    if (requested <= 10) {
      return 10;
    }
    return 15;
  };

  const normalizeResolution = (value) => {
    const normalized = String(value || "").trim().toLowerCase();
    return VISION_RESOLUTION_OPTIONS.includes(normalized) ? normalized : "4k";
  };

  const normalizeAspectRatio = (value) => {
    const normalized = String(value || "").trim().toLowerCase().replace(/\s+/g, "");
    return VISION_ASPECT_RATIO_OPTIONS.includes(normalized) ? normalized : "16:9";
  };

  const formatVisionCredits = (value) => {
    const amount = Math.max(0, Number(value || 0));
    if (!Number.isFinite(amount)) {
      return "0";
    }
    return Math.round(amount).toLocaleString("it-IT");
  };

  const getGenerationCost = () => {
    const resolution = normalizeResolution(state.resolution);
    const aspectRatio = normalizeAspectRatio(state.aspectRatio);
    const duration = normalizeDurationSeconds(state.durationSeconds);
    if (state.mode === "image") {
      const premiumImage = resolution === "4k";
      return {
        amount: premiumImage ? 25000 : 10000,
        label: premiumImage ? "Premium image" : "Standard image",
        duration_seconds: null,
        resolution,
        aspect_ratio: aspectRatio,
        sound_enabled: false,
      };
    }
    if (resolution === "4k") {
      return {
        amount: duration * 200000,
        label: `4K video · ${duration}s`,
        duration_seconds: duration,
        resolution,
        aspect_ratio: aspectRatio,
        sound_enabled: Boolean(state.soundEnabled),
      };
    }
    if (state.soundEnabled || resolution === "1080p") {
      return {
        amount: duration * 50000,
        label: `Full HD video · ${duration}s`,
        duration_seconds: duration,
        resolution,
        aspect_ratio: aspectRatio,
        sound_enabled: Boolean(state.soundEnabled),
      };
    }
    return {
      amount: duration * 20000,
      label: `Standard video · ${duration}s`,
      duration_seconds: duration,
      resolution,
      aspect_ratio: aspectRatio,
      sound_enabled: Boolean(state.soundEnabled),
    };
  };

  const parseJsonSafely = async (response) => {
    try {
      return await response.json();
    } catch (error) {
      return null;
    }
  };

  const visionApiUrl = (path) => `${DEFAULT_API_BASE}${path}`;

  const normalizeGeneratedAssetPath = (path) => {
    const raw = String(path || "").trim();
    if (!raw) {
      return "";
    }

    const candidate = raw.startsWith("generated/") ? `/${raw}` : raw;

    try {
      const parsed = /^https?:\/\//i.test(candidate) ? new URL(candidate) : new URL(candidate, window.location.origin);
      const pathname = String(parsed.pathname || "").replace(/\/{2,}/g, "/");
      if (!pathname.startsWith("/generated/") || pathname === "/generated/") {
        return "";
      }
      return `${pathname}${parsed.search}${parsed.hash}`;
    } catch (error) {
      return "";
    }
  };

  const visionAssetUrl = (path) => {
    const assetPath = normalizeGeneratedAssetPath(path);
    if (!assetPath) {
      return "";
    }
    return `${DEFAULT_API_BASE}${assetPath}`;
  };

  const getAssetCandidateUrls = (assetPath) => {
    const candidates = [];
    const currentOrigin = String(window.location.origin || "").replace(/\/$/, "");
    if (currentOrigin && /^https?:\/\//i.test(currentOrigin)) {
      candidates.push(`${currentOrigin}${assetPath}`);
    }
    if (DEFAULT_API_BASE) {
      candidates.push(`${DEFAULT_API_BASE}${assetPath}`);
    }
    return Array.from(new Set(candidates.filter(Boolean)));
  };

  const verifyAssetWithHead = async (assetUrl) => {
    if (!assetUrl) {
      return false;
    }
    const response = await fetch(assetUrl, {
      method: "HEAD",
      cache: "no-store",
      mode: "cors",
      credentials: "omit",
    });
    return response.ok;
  };

  const getAssetAvailability = (path) => {
    const assetPath = normalizeGeneratedAssetPath(path);
    if (!assetPath) {
      return {
        assetPath: "",
        assetUrl: "",
        state: "invalid",
        available: false,
      };
    }
    const record = state.assetStatusByPath[assetPath];
    const status = record && record.state ? record.state : "unknown";
    const candidateUrls = getAssetCandidateUrls(assetPath);
    return {
      assetPath,
      assetUrl: record && record.assetUrl ? record.assetUrl : (candidateUrls[0] || visionAssetUrl(assetPath)),
      state: status,
      available: status === "available",
    };
  };

  const isAssetPending = (asset) => !!asset && (asset.state === "unknown" || asset.state === "checking");

  const verifyAssetAvailability = async (path, options) => {
    const opts = options || {};
    const asset = getAssetAvailability(path);
    if (!asset.assetPath) {
      return asset;
    }

    if (!opts.force && (asset.state === "available" || asset.state === "missing")) {
      return asset;
    }

    const pending = pendingAssetChecks.get(asset.assetPath);
    if (!opts.force && pending) {
      return pending;
    }

    const request = (async () => {
      let nextState = "unknown";
      let resolvedAssetUrl = asset.assetUrl;
      const candidateUrls = getAssetCandidateUrls(asset.assetPath);
      try {
        const cachedAsset = await hydrateAssetFromCache(asset.assetPath);
        if (cachedAsset) {
          nextState = "available";
          resolvedAssetUrl = cachedAsset.assetUrl;
        } else {
          state.assetStatusByPath[asset.assetPath] = {
            state: "checking",
            checkedAt: Date.now(),
          };
          if (opts.renderOnStart) {
            render();
          }

          for (const candidateUrl of candidateUrls) {
            if (await verifyAssetWithHead(candidateUrl)) {
              nextState = "available";
              resolvedAssetUrl = candidateUrl;
              break;
            }
          }
          if (nextState !== "available") {
            const response = await visionFetch(`/api/assets/status?path=${encodeURIComponent(asset.assetPath)}`, {
              cache: "no-store",
            });
            const payload = await parseJsonSafely(response);
            if (response.ok && payload && typeof payload.available === "boolean") {
              nextState = payload.available ? "available" : "missing";
              if (payload.available) {
                resolvedAssetUrl = `${DEFAULT_API_BASE}${String(payload.path || asset.assetPath)}`;
              }
            } else {
              nextState = "missing";
            }
          }
        }
      } catch (error) {
        nextState = nextState === "available" ? "available" : "missing";
      } finally {
        pendingAssetChecks.delete(asset.assetPath);
      }

      state.assetStatusByPath[asset.assetPath] = {
        state: nextState,
        assetUrl: resolvedAssetUrl,
        checkedAt: Date.now(),
      };
      if (nextState === "available" && resolvedAssetUrl) {
        void cacheAssetFromUrl(asset.assetPath, resolvedAssetUrl);
      }
      refreshRecentValidationState();
      reconcileSelectedRecent();
      syncScene();
      render();
      return {
        assetPath: asset.assetPath,
        assetUrl: resolvedAssetUrl,
        state: nextState,
        available: nextState === "available",
      };
    })();

    pendingAssetChecks.set(asset.assetPath, request);
    return request;
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

  const readStoredUserToken = () => {
    try {
      return window.localStorage.getItem(VISION_USER_STORAGE_KEY) || "";
    } catch (error) {
      return "";
    }
  };

  const storeUserToken = (token) => {
    try {
      if (token) {
        window.localStorage.setItem(VISION_USER_STORAGE_KEY, token);
        return;
      }
      window.localStorage.removeItem(VISION_USER_STORAGE_KEY);
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
    const userToken = readStoredUserToken();
    if (userToken && !headers["x-vision-user"]) {
      headers["x-vision-user"] = userToken;
    }
    return fetch(visionApiUrl(path), {
      credentials: "include",
      ...opts,
      headers,
    });
  };

  const openAssetCacheDb = () => {
    if (!window.indexedDB) {
      return Promise.resolve(null);
    }
    if (assetCacheDbPromise) {
      return assetCacheDbPromise;
    }
    assetCacheDbPromise = new Promise((resolve) => {
      try {
        const request = window.indexedDB.open(VISION_ASSET_CACHE_DB, 1);
        request.onupgradeneeded = () => {
          const database = request.result;
          if (!database.objectStoreNames.contains(VISION_ASSET_CACHE_STORE)) {
            database.createObjectStore(VISION_ASSET_CACHE_STORE, { keyPath: "assetPath" });
          }
        };
        request.onsuccess = () => resolve(request.result);
        request.onerror = () => resolve(null);
        request.onblocked = () => resolve(null);
      } catch (error) {
        resolve(null);
      }
    });
    return assetCacheDbPromise;
  };

  const readCachedAssetRecord = async (assetPath) => {
    if (!assetPath) {
      return null;
    }
    const database = await openAssetCacheDb();
    if (!database) {
      return null;
    }
    return new Promise((resolve) => {
      try {
        const transaction = database.transaction(VISION_ASSET_CACHE_STORE, "readonly");
        const request = transaction.objectStore(VISION_ASSET_CACHE_STORE).get(assetPath);
        request.onsuccess = () => resolve(request.result || null);
        request.onerror = () => resolve(null);
      } catch (error) {
        resolve(null);
      }
    });
  };

  const writeCachedAssetRecord = async (assetPath, blob) => {
    if (!assetPath || !blob) {
      return false;
    }
    const database = await openAssetCacheDb();
    if (!database) {
      return false;
    }
    return new Promise((resolve) => {
      try {
        const transaction = database.transaction(VISION_ASSET_CACHE_STORE, "readwrite");
        transaction.objectStore(VISION_ASSET_CACHE_STORE).put({
          assetPath,
          blob,
          cachedAt: Date.now(),
        });
        transaction.oncomplete = () => resolve(true);
        transaction.onerror = () => resolve(false);
        transaction.onabort = () => resolve(false);
      } catch (error) {
        resolve(false);
      }
    });
  };

  const removeCachedAssetRecord = async (assetPath) => {
    if (!assetPath) {
      return false;
    }
    const existingObjectUrl = state.assetObjectUrlsByPath[assetPath];
    if (existingObjectUrl) {
      try {
        window.URL.revokeObjectURL(existingObjectUrl);
      } catch (error) {
        // Ignore object URL cleanup failures.
      }
      delete state.assetObjectUrlsByPath[assetPath];
    }
    const database = await openAssetCacheDb();
    if (!database) {
      return false;
    }
    return new Promise((resolve) => {
      try {
        const transaction = database.transaction(VISION_ASSET_CACHE_STORE, "readwrite");
        transaction.objectStore(VISION_ASSET_CACHE_STORE).delete(assetPath);
        transaction.oncomplete = () => resolve(true);
        transaction.onerror = () => resolve(false);
        transaction.onabort = () => resolve(false);
      } catch (error) {
        resolve(false);
      }
    });
  };

  const setAssetObjectUrl = (assetPath, blob) => {
    if (!assetPath || !blob) {
      return "";
    }
    const previousObjectUrl = state.assetObjectUrlsByPath[assetPath];
    if (previousObjectUrl) {
      try {
        window.URL.revokeObjectURL(previousObjectUrl);
      } catch (error) {
        // Ignore object URL cleanup failures.
      }
    }
    const objectUrl = window.URL.createObjectURL(blob);
    state.assetObjectUrlsByPath[assetPath] = objectUrl;
    return objectUrl;
  };

  const hydrateAssetFromCache = async (assetPath) => {
    const cached = await readCachedAssetRecord(assetPath);
    if (!cached || !cached.blob) {
      return null;
    }
    const assetUrl = setAssetObjectUrl(assetPath, cached.blob);
    const availability = {
      assetPath,
      assetUrl,
      state: "available",
      available: true,
    };
    state.assetStatusByPath[assetPath] = {
      state: "available",
      assetUrl,
      checkedAt: Date.now(),
      source: "cache",
    };
    return availability;
  };

  const cacheAssetFromUrl = async (assetPath, assetUrl) => {
    if (!assetPath || !assetUrl) {
      return null;
    }
    const cached = await hydrateAssetFromCache(assetPath);
    if (cached) {
      return cached;
    }
    try {
      const response = await fetch(assetUrl, {
        cache: "no-store",
        mode: "cors",
        credentials: "omit",
      });
      if (!response.ok) {
        return null;
      }
      const blob = await response.blob();
      if (!(blob instanceof Blob) || !blob.size) {
        return null;
      }
      const stored = await writeCachedAssetRecord(assetPath, blob);
      if (!stored) {
        return null;
      }
      return hydrateAssetFromCache(assetPath);
    } catch (error) {
      return null;
    }
  };

  window.addEventListener("beforeunload", () => {
    Object.values(state.assetObjectUrlsByPath).forEach((objectUrl) => {
      if (!objectUrl) {
        return;
      }
      try {
        window.URL.revokeObjectURL(objectUrl);
      } catch (error) {
        // Ignore object URL cleanup failures.
      }
    });
  });

  const savePendingPrompt = (prompt, mode, aspectRatio) => {
    try {
      window.sessionStorage.setItem(
        VISION_PENDING_PROMPT_KEY,
        JSON.stringify({
          prompt: String(prompt || ""),
          mode: "image",
          aspect_ratio: normalizeAspectRatio(aspectRatio),
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
        mode: "image",
        aspect_ratio: normalizeAspectRatio(parsed.aspect_ratio),
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

  const closeViewer = () => {
    if (!state.viewer.open) {
      return;
    }
    state.viewer = { ...defaultViewer };
    render();
  };

  const openViewerForItem = (item) => {
    if (!item) {
      return;
    }
    const asset = getAssetAvailability(item.src);
    if (!asset.available || !asset.assetUrl) {
      return;
    }
    clearRecentMenu();
    state.viewer = {
      open: true,
      kind: item.kind === "video" ? "video" : "image",
      assetPath: asset.assetPath,
      assetUrl: asset.assetUrl,
      title: summarizePrompt(item.prompt, item.kind === "video" ? "Vision render" : "Vision still", 52),
      caption: summarizeDescription(item.prompt, "Generated inside Vision.", 112),
    };
    trackVisionEvent("ViewerOpened", {
      job_id: item.id,
      asset_id: asset.assetPath || asset.assetUrl,
      media_type: state.viewer.kind,
      platform_context: "web",
    });
    render();
  };

  const markAssetMissing = (path) => {
    const asset = getAssetAvailability(path);
    if (!asset.assetPath) {
      return;
    }
    clearRecentMenu();
    state.assetStatusByPath[asset.assetPath] = {
      state: "missing",
      assetUrl: asset.assetUrl,
      checkedAt: Date.now(),
    };
    if (state.viewer.assetPath === asset.assetPath) {
      state.viewer = { ...defaultViewer };
    }
    reconcileSelectedRecent();
    syncScene();
    render();
  };

  const downloadItemAsset = async (item) => {
    if (!item) {
      return false;
    }
    const asset = getAssetAvailability(item.src);
    if (!asset.available || !asset.assetUrl) {
      return false;
    }

    let objectUrl = "";
    try {
      const response = await fetch(asset.assetUrl, {
        cache: "no-store",
        mode: "cors",
        credentials: "omit",
      });
      if (!response.ok) {
        markAssetMissing(item.src);
        return false;
      }
      const blob = await response.blob();
      objectUrl = window.URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = objectUrl;
      link.download = buildDownloadFilename({
        ...item,
        type: item.kind === "image" ? "image" : "video",
        src: asset.assetUrl,
      });
      link.rel = "noopener";
      document.body.appendChild(link);
      link.click();
      link.remove();
      return true;
    } catch (error) {
      return false;
    } finally {
      if (objectUrl) {
        window.setTimeout(() => {
          try {
            window.URL.revokeObjectURL(objectUrl);
          } catch (error) {
            // Ignore object URL cleanup failures.
          }
        }, 1000);
      }
    }
  };

  const withDownloadFeedback = async (button, item) => {
    if (!button || !item) {
      return;
    }
    const originalLabel = button.textContent || "Download";
    button.disabled = true;
    button.setAttribute("aria-disabled", "true");
    button.textContent = "Preparing download...";

    const succeeded = await downloadItemAsset(item);
    if (!document.body.contains(button)) {
      return;
    }

    const asset = getAssetAvailability(item.src);
    if (!succeeded && !asset.available) {
      button.textContent = "Download unavailable";
      return;
    }
    if (succeeded) {
      trackVisionEvent("AssetDownloaded", {
        job_id: item.id,
        asset_id: asset.assetPath || asset.assetUrl,
        media_type: item.kind === "video" ? "video" : "image",
        platform_context: "web",
      });
    }

    button.disabled = false;
    button.removeAttribute("aria-disabled");
    button.textContent = originalLabel;
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

  const normalizeStoredHistoryItem = (item) => {
    if (!item || !item.id) {
      return null;
    }
    const normalizedSrc = normalizeGeneratedAssetPath(item.src);
    if (!normalizedSrc) {
      return null;
    }
    return {
      ...item,
      id: String(item.id),
      type: item.type === "image" ? "image" : "video",
      src: normalizedSrc,
      prompt: String(item.prompt || ""),
      aspect_ratio: normalizeAspectRatio(item.aspect_ratio),
      created_at: item.created_at || "",
    };
  };

  const sanitizeHistoryItems = (items) => {
    const sanitized = [];
    let dirty = false;
    (Array.isArray(items) ? items : []).forEach((item) => {
      const normalized = normalizeStoredHistoryItem(item);
      if (!normalized) {
        dirty = true;
        return;
      }
      if (
        String(item.id || "") !== normalized.id ||
        String(item.src || "") !== normalized.src ||
        String(item.prompt || "") !== normalized.prompt ||
        String(item.aspect_ratio || "") !== normalized.aspect_ratio ||
        (item.type === "image" ? "image" : "video") !== normalized.type ||
        String(item.created_at || "") !== normalized.created_at
      ) {
        dirty = true;
      }
      sanitized.push(normalized);
    });
    return {
      items: sanitized,
      dirty,
    };
  };

  const writeHistoryToKey = (key, items) => {
    try {
      window.localStorage.setItem(key, JSON.stringify(items));
    } catch (error) {
      // Ignore storage failures.
    }
  };

  const readStudioHistory = (options) => {
    const opts = options || {};
    const key = getHistoryStorageKey();
    const sanitized = sanitizeHistoryItems(readHistoryFromKey(key));
    if (opts.writeBack && sanitized.dirty) {
      writeHistoryToKey(key, sanitized.items);
    }
    return sanitized.items;
  };

  const writeStudioHistory = (items) => {
    writeHistoryToKey(getHistoryStorageKey(), sanitizeHistoryItems(items).items);
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

  const formatRenewalDate = (value) => {
    if (!value) {
      return "";
    }
    const numericValue = Number(value);
    const dateValue = Number.isFinite(numericValue) && numericValue > 0
      ? new Date(numericValue < 100000000000 ? numericValue * 1000 : numericValue)
      : new Date(value);
    if (Number.isNaN(dateValue.getTime())) {
      return "";
    }
    try {
      return new Intl.DateTimeFormat("en-GB", {
        day: "numeric",
        month: "long",
        year: "numeric",
      }).format(dateValue);
    } catch (error) {
      return "";
    }
  };

  const summarizePrompt = (prompt, fallback, maxLength) => {
    const cleaned = String(prompt || "").trim().replace(/\s+/g, " ");
    const limit = Number(maxLength || 34);
    if (!cleaned) {
      return fallback;
    }
    return cleaned.length > limit ? `${cleaned.slice(0, Math.max(0, limit - 3)).trimEnd()}...` : cleaned;
  };

  const summarizeDescription = (prompt, fallback, maxLength) => {
    const cleaned = String(prompt || "").trim().replace(/\s+/g, " ");
    const limit = Number(maxLength || 96);
    if (!cleaned) {
      return fallback;
    }
    return cleaned.length > limit ? `${cleaned.slice(0, Math.max(0, limit - 3)).trimEnd()}...` : cleaned;
  };

  const shortenEmail = (email) => {
    const normalized = normalizeEmail(email);
    if (!normalized) {
      return "Vision account";
    }
    if (normalized.length <= 28) {
      return normalized;
    }
    const [localPart, domainPart] = normalized.split("@");
    if (!domainPart) {
      return `${normalized.slice(0, 25)}...`;
    }
    return `${localPart.slice(0, 12)}...@${domainPart}`;
  };

  const getCreditCounts = () => ({
    vision: Math.max(0, Number(state.access.vision_credits_remaining ?? 0) || 0),
    visionPurchased: Math.max(0, Number(state.access.vision_credits_purchased ?? 0) || 0),
    video: Math.max(0, Number(state.access.video_remaining ?? 0) || 0),
    image: Math.max(0, Number(state.access.image_remaining ?? 0) || 0),
  });
  const isUnlimitedImageCount = (value) => Math.max(0, Number(value || 0)) >= 999000;

  const hasPackContext = () => {
    const counts = getCreditCounts();
    return !!state.access.admin || !!state.access.has_access || counts.vision > 0 || counts.video > 0 || counts.image > 0;
  };
  const hasAccountContext = () => !!state.user.authenticated;
  const isCompactStudioViewport = () =>
    typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia("(max-width: 720px)").matches;

  const getAccessLabel = () => {
    if (state.access.admin) {
      return "Studio active";
    }
    if (hasPackContext()) {
      return "Studio active";
    }
    return "No active plan";
  };

  const getAccountPillState = () => {
    if (!hasAccountContext()) {
      return {
        variant: "guest",
        label: "Sign in",
        subtitle: "Access your Studio",
      };
    }
    const avatar = state.user.email ? state.user.email.charAt(0).toUpperCase() : "A";
    return {
      variant: "account",
      avatar,
      label: "Account",
      subtitle: getAccessLabel(),
    };
  };

  const getRecentById = (id) => state.recents.find((item) => item.id === String(id || "")) || null;

  const clearRecentMenu = () => {
    state.menuOpenFor = "";
    state.menuAnchor = null;
  };

  const getLatestAvailableRecent = () => state.recents.find((item) => getAssetAvailability(item.src).available) || null;

  const refreshRecentValidationState = () => {
    state.recentValidationPending = state.recents.some((item) => isAssetPending(getAssetAvailability(item.src)));
    return state.recentValidationPending;
  };

  const setSelectedRecent = (item, options) => {
    if (!item) {
      return false;
    }
    state.selectedId = item.id;
    state.selectionSource = options && options.source === "manual" ? "manual" : "auto";
    clearRecentMenu();
    state.currentError = "";
    syncScene();
    return true;
  };

  const selectRecent = (id, options) => {
    const item = getRecentById(id);
    const asset = item ? getAssetAvailability(item.src) : null;
    if (!item || !asset || !asset.available) {
      return false;
    }
    setSelectedRecent(item, { source: "manual" });
    if (options && options.openViewer) {
      openViewerForItem(item);
      return true;
    }
    render();
    return true;
  };

  const syncRecents = () => {
    recentsValidationToken += 1;
    maybePromoteGuestHistory();
    state.recents = readStudioHistory({ writeBack: true })
      .slice(0, 12)
      .map((item) => ({
        id: String(item.id),
        kind: item.type === "image" ? "image" : "video",
        src: item.src,
        prompt: String(item.prompt || ""),
        aspectRatio: normalizeAspectRatio(item.aspect_ratio),
        createdAt: item.created_at || "",
      }));
    if (!state.selectedId || !state.recents.some((item) => item.id === state.selectedId)) {
      state.selectedId = "";
      state.selectionSource = "auto";
    }
    refreshRecentValidationState();
    reconcileSelectedRecent();
    verifyRecents();
  };

  const getSelectedRecent = () => state.recents.find((item) => item.id === state.selectedId) || null;

  const reconcileSelectedRecent = (options) => {
    const opts = options || {};
    const selected = getSelectedRecent();
    const selectedAsset = selected ? getAssetAvailability(selected.src) : null;
    const selectedAvailable = !!(selected && selectedAsset && selectedAsset.available);
    if (state.selectionSource === "manual" && selectedAvailable && !opts.forceLatest) {
      return false;
    }
    const fallback = getLatestAvailableRecent();
    const nextSelectedId = fallback ? fallback.id : "";
    const changed = nextSelectedId !== state.selectedId || state.selectionSource !== "auto";
    state.selectedId = nextSelectedId;
    state.selectionSource = "auto";
    return changed;
  };

  const verifyRecents = async () => {
    const token = ++recentsValidationToken;
    const assetPaths = [];
    const seen = new Set();

    state.recents.forEach((item) => {
      const asset = getAssetAvailability(item.src);
      if (!asset.assetPath || seen.has(asset.assetPath)) {
        return;
      }
      seen.add(asset.assetPath);
      assetPaths.push(asset.assetPath);
    });

    refreshRecentValidationState();
    syncScene();
    render();

    for (const assetPath of assetPaths) {
      if (token !== recentsValidationToken) {
        return;
      }
      await verifyAssetAvailability(assetPath, { force: isAssetPending(getAssetAvailability(assetPath)) });
      if (token !== recentsValidationToken) {
        return;
      }
      refreshRecentValidationState();
    }

    if (token !== recentsValidationToken) {
      return;
    }
    refreshRecentValidationState();
    reconcileSelectedRecent();
    syncScene();
    render();
  };

  const saveHistoryItem = (job, src) => {
    if (!job || !job.id || !src) {
      return null;
    }
    const resolvedSrc = normalizeGeneratedAssetPath(src);
    if (!resolvedSrc) {
      return null;
    }
    const item = {
      id: String(job.id),
      type: (job.output_type || job.mode || "video").toLowerCase() === "image" ? "image" : "video",
      src: resolvedSrc,
      prompt: String(job.prompt || ""),
      aspect_ratio: normalizeAspectRatio(
        job.generation_settings && job.generation_settings.aspect_ratio
          ? job.generation_settings.aspect_ratio
          : state.aspectRatio,
      ),
      created_at: job.completed_at || job.updated_at || new Date().toISOString(),
    };
    const items = readStudioHistory().filter((entry) => String(entry.id || "") !== item.id);
    items.unshift(item);
    writeStudioHistory(items.slice(0, 16));
    syncRecents();
    state.selectedId = item.id;
    state.selectionSource = "auto";
    return item;
  };

  const deleteHistoryItem = (id) => {
    const normalizedId = String(id || "").trim();
    if (!normalizedId) {
      return;
    }
    const existingItems = readStudioHistory();
    const removedItem = existingItems.find((entry) => String(entry.id || "") === normalizedId) || null;
    const items = existingItems.filter((entry) => String(entry.id || "") !== normalizedId);
    writeStudioHistory(items);
    const removedAssetPath = removedItem ? normalizeGeneratedAssetPath(removedItem.src) : "";
    const stillReferenced = removedAssetPath && items.some((entry) => normalizeGeneratedAssetPath(entry.src) === removedAssetPath);
    if (removedAssetPath && !stillReferenced) {
      void removeCachedAssetRecord(removedAssetPath);
    }
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
      name: String(pack && pack.name ? pack.name : "Vision Studio"),
      price_cents: Number(pack && pack.price_cents ? pack.price_cents : 99),
      original_price_cents: Number(pack && pack.original_price_cents ? pack.original_price_cents : 99),
      currency: String(pack && pack.currency ? pack.currency : "EUR"),
      vision_credits: Number(pack && pack.vision_credits ? pack.vision_credits : 0),
      credit_label: String(pack && pack.credit_label ? pack.credit_label : ""),
    }));
  };

  const getPackById = (packId) =>
    state.packs.find((pack) => pack.id === String(packId || "").toLowerCase()) ||
    state.packs[0] || { ...defaultPack };

  const formatPackPrice = (pack) => {
    const amount = Number((pack && pack.price_cents) || 99) / 100;
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

  const packTrackingPayload = (pack) => ({
    plan_id: String((pack && pack.id) || DEFAULT_PACK_ID).toLowerCase(),
    currency: String((pack && pack.currency) || "EUR").toUpperCase(),
    value: Number((pack && pack.price_cents) || 0) / 100,
  });

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
    if (state.recents.length && state.recentValidationPending) {
      state.scene = "resolving";
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
      if (payload && payload.user_token) {
        storeUserToken(payload.user_token);
      }
      state.user = { ...defaultUser, ...(payload && payload.user ? payload.user : {}) };
      state.access = { ...defaultAccess, ...(payload && payload.access ? payload.access : {}) };
      state.subscription = {
        ...defaultSubscription,
        ...(payload && payload.subscription ? payload.subscription : {}),
      };
      state.packs = normalizePackList(payload && payload.packs ? payload.packs : []);
      state.currentPack = getPackById((payload && payload.pack && payload.pack.id) || DEFAULT_PACK_ID);
      syncRecents();
      syncScene();
      render();
      return payload;
    } catch (error) {
      state.user = { ...defaultUser };
      state.access = { ...defaultAccess };
      state.subscription = { ...defaultSubscription };
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
    if (checkout === "cancel") {
      state.authNote = "Checkout was cancelled. Your account was not charged.";
      state.accountPanelOpen = true;
      url.searchParams.delete("checkout");
      window.history.replaceState({}, "", `${url.pathname}${url.search}${url.hash}`);
      return false;
    }
    if (!sessionId || checkout !== "success") {
      return false;
    }
    let confirmed = false;
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
      trackVisionEvent("PurchaseCompleted", {
        ads_only: true,
        event_id: `stripe:${sessionId}:PurchaseCompleted`,
        checkout_session_id: sessionId,
        ...packTrackingPayload((payload && payload.pack) || state.currentPack),
        platform_context: "web",
      });
      if (payload && payload.access_token) {
        storeAccessToken(payload.access_token);
      }
      if (payload && payload.user_token) {
        storeUserToken(payload.user_token);
      }
      confirmed = true;
      state.authNote = "Your Vision Studio subscription is active.";
      state.accountPanelOpen = true;
      state.authStep = state.user.authenticated ? "account" : "email";
    } catch (error) {
      state.authNote = error instanceof Error ? error.message : "Payment confirmation failed.";
      state.accountPanelOpen = true;
      state.authStep = state.user.authenticated ? "account" : "email";
      render();
    } finally {
      if (confirmed) {
        url.searchParams.delete("session_id");
        url.searchParams.delete("checkout");
        window.history.replaceState({}, "", `${url.pathname}${url.search}${url.hash}`);
      }
    }
    return confirmed;
  };

  const maybeRestorePendingPrompt = () => {
    const pending = readPendingPrompt();
    if (!pending) {
      return;
    }
    state.prompt = pending.prompt;
    state.mode = pending.mode;
    state.aspectRatio = normalizeAspectRatio(pending.aspect_ratio);
    render();
  };

  const saveGeneratedResult = async (job, verifiedAsset) => {
    const asset = verifiedAsset && verifiedAsset.available ? verifiedAsset : await verifyAssetAvailability(job.output_url, { force: true });
    if (!asset.available || !asset.assetUrl) {
      state.currentJob = null;
      state.currentError = "Vision finished the job, but the generated file could not be found on the gateway.";
      syncScene();
      render();
      return;
    }
    if (asset.assetPath && asset.assetUrl) {
      await cacheAssetFromUrl(asset.assetPath, asset.assetUrl);
    }
    const item = saveHistoryItem(job, asset.assetPath || asset.assetUrl);
    trackVisionEvent("GenerateCompleted", {
      job_id: job.id,
      asset_id: asset.assetPath || asset.assetUrl,
      media_type: item ? item.type : getJobMode(job),
      platform_context: "web",
    });
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
      if (job.output_url) {
        const verifiedAsset = await verifyAssetAvailability(job.output_url, { force: true });
        if (verifiedAsset.available) {
          stopPolling();
          await saveGeneratedResult(job, verifiedAsset);
          return;
        }
      }
      if (status === "ready") {
        stopPolling();
        handleJobFailure({
          message: "Vision finished the job, but the generated file could not be found on the gateway.",
        });
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
    state.authStep = state.user.authenticated ? "account" : "email";
    state.authNote = state.user.authenticated
      ? "Start Vision Studio to create unlimited 4K images."
      : "Sign in first, then start Vision Studio.";
    state.currentJob = null;
    syncScene();
    render();
  };

  const submitPrompt = async () => {
    const prompt = String(state.prompt || "").trim();
    if (!prompt) {
      return;
    }

    const generationCost = getGenerationCost();
    savePendingPrompt(prompt, state.mode, state.aspectRatio);
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
    trackVisionEvent("GenerateStarted", {
      media_type: state.mode,
      value: generationCost.amount,
      platform_context: "web",
      payload: {
        credit_cost: generationCost.amount,
        duration_seconds: generationCost.duration_seconds,
        resolution: generationCost.resolution,
        aspect_ratio: generationCost.aspect_ratio,
        sound_enabled: generationCost.sound_enabled,
      },
    });

    try {
      const response = await visionFetch("/api/jobs", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          prompt,
          mode: state.mode,
          duration_seconds: generationCost.duration_seconds,
          resolution: generationCost.resolution,
          aspect_ratio: generationCost.aspect_ratio,
          sound_enabled: generationCost.sound_enabled,
        }),
      });
      const payload = await parseJsonSafely(response);

      if (response.status === 401 || response.status === 402) {
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
      if (payload && payload.user_token) {
        storeUserToken(payload.user_token);
      }
      state.authStep = "account";
      state.authPendingCode = "";
      state.authNote = "";
      await maybeConfirmCheckout();
      await refreshAccess();
      state.accountPanelOpen = true;
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
    storeAccessToken("");
    storeUserToken("");
    state.user = { ...defaultUser };
    state.access = { ...defaultAccess };
    state.subscription = { ...defaultSubscription };
    state.authStep = "email";
    state.authPendingEmail = "";
    state.authPendingCode = "";
    state.accountPanelOpen = false;
    syncRecents();
    syncScene();
    render();
    try {
      const response = await visionFetch("/api/auth/logout", { method: "POST" });
      const payload = await parseJsonSafely(response);
      if (!response.ok) {
        throw new Error((payload && (payload.detail || payload.message)) || "Vision could not log you out.");
      }
    } catch (error) {
      console.warn("Vision server logout could not be completed.", error);
    } finally {
      state.authLoading = false;
      render();
    }
  };

  const openCheckout = async () => {
    if (!state.user.authenticated) {
      state.accountPanelOpen = true;
      state.authStep = "email";
      state.authNote = "Sign in first, then start Vision Studio.";
      render();
      return;
    }
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
      const selectedPack = state.currentPack || getPackById(DEFAULT_PACK_ID);
      const checkoutEvent = trackVisionEvent("CheckoutStarted", {
        ...packTrackingPayload(selectedPack),
        customer_email: email,
        platform_context: "web",
      });
      const tracking = getVisionTrackingContext({
        event_name: "CheckoutStarted",
        event_id: checkoutEvent && checkoutEvent.event_id,
        ...packTrackingPayload(selectedPack),
      });
      const response = await visionFetch("/api/checkout/session", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          email,
          pack_id: (selectedPack && selectedPack.id) || DEFAULT_PACK_ID,
          return_path: "/studio/",
          tracking,
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

  const openCustomerPortal = async () => {
    if (!state.user.authenticated) {
      state.authStep = "email";
      state.authNote = "Sign in to manage your subscription.";
      render();
      return;
    }
    state.portalLoading = true;
    state.authNote = "Opening secure billing...";
    render();
    try {
      const response = await visionFetch("/api/billing/portal", { method: "POST" });
      const payload = await parseJsonSafely(response);
      if (!response.ok || !payload || !payload.url) {
        throw new Error((payload && (payload.detail || payload.message)) || "Billing management is unavailable right now.");
      }
      window.location.assign(payload.url);
    } catch (error) {
      state.portalLoading = false;
      state.authNote = error instanceof Error ? error.message : "Billing management is unavailable right now.";
      render();
    }
  };

  const improvePrompt = async () => {
    const prompt = String(state.prompt || "").trim();
    if (!prompt) {
      return;
    }
    state.improveLoading = true;
    trackVisionEvent("PromptImproved", {
      media_type: state.mode,
      platform_context: "web",
    });
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
      if (response.status === 401 || response.status === 402) {
        handleCheckoutRequired(payload && payload.detail ? payload.detail : null);
        return;
      }
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
        <div class="vss-header-actions">
          <button class="vss-account-pill${pill.variant === "guest" ? " is-guest" : ""}" id="vss-account-pill" type="button" aria-label="${pill.variant === "guest" ? "Sign in to Vision" : "Open Vision account"}" aria-haspopup="dialog" aria-expanded="${state.accountPanelOpen ? "true" : "false"}">
            ${
              pill.variant === "guest"
                ? `<span class="vss-account-guest-copy">
                    <span class="vss-account-guest-label">${escapeHtml(pill.label)}</span>
                    <span class="vss-account-guest-note">${escapeHtml(pill.subtitle)}</span>
                  </span>`
                : `<span class="vss-account-avatar">${escapeHtml(pill.avatar)}</span>
                   <span class="vss-account-copy">
                     <span class="vss-account-label">${escapeHtml(pill.label)}</span>
                     <span class="vss-account-note">${escapeHtml(pill.subtitle)}</span>
                   </span>`
            }
            <span class="vss-account-chevron">⌄</span>
          </button>
        </div>
      </header>
    `;
  };

  const renderCanvasMedia = () => {
    if (state.scene === "result" && getSelectedRecent()) {
      const item = getSelectedRecent();
      const asset = getAssetAvailability(item.src);
      const hasAsset = asset.available;
      const assetMissing = asset.state === "missing" || asset.state === "invalid";
      const openLabel = item.kind === "video" ? "Open video" : "Open image";
      const downloadLabel = assetMissing ? "Download unavailable" : "Download";
      const resultLabel =
        state.selectionSource === "manual"
          ? (item.kind === "video" ? "Selected video" : "Selected image")
          : (item.kind === "video" ? "Latest video" : "Latest image");
      const media =
        hasAsset
          ? `<button class="vss-canvas-preview" type="button" data-open-current-preview="${escapeHtml(item.id)}" aria-label="${escapeHtml(openLabel)}">
              ${
                item.kind === "video"
                  ? `<video class="vss-canvas-video" src="${escapeHtml(asset.assetUrl)}" autoplay muted loop playsinline></video>`
                  : `<img class="vss-canvas-image" src="${escapeHtml(asset.assetUrl)}" alt="${escapeHtml(summarizePrompt(item.prompt, "Vision still", 48))}" />`
              }
            </button>`
          : `<div class="vss-canvas-missing">${assetMissing ? "Source unavailable" : "Checking source..."}</div>`;
      return `
        <div class="vss-canvas-media">
          ${media}
          <div class="vss-canvas-scrim"></div>
          <div class="vss-canvas-actions" aria-label="Current result actions">
            <button class="vss-canvas-action is-primary" type="button" data-open-current="${escapeHtml(item.id)}" ${hasAsset ? "" : "disabled aria-disabled=\"true\""}>${escapeHtml(openLabel)}</button>
            ${
              hasAsset
                ? `<button class="vss-canvas-action is-secondary" type="button" data-download-current="${escapeHtml(item.id)}">Download</button>`
                : `<button class="vss-canvas-action is-secondary" type="button" disabled aria-disabled="true">${escapeHtml(downloadLabel)}</button>`
            }
          </div>
          <div class="vss-canvas-result-meta">
            <p class="vss-result-label">${
              hasAsset ? resultLabel : (assetMissing ? "Source unavailable" : "Checking source")
            }</p>
            <h2 class="vss-result-title">${escapeHtml(summarizePrompt(item.prompt, item.kind === "video" ? "Vision render" : "Vision still", 52))}</h2>
            <p class="vss-result-caption">${escapeHtml(
              hasAsset
                ? summarizeDescription(item.prompt, "Generated inside Vision.", 74)
                : assetMissing
                  ? "This source is no longer available. Delete it or choose a newer recent."
                  : "Vision is verifying the generated source before enabling open and download.",
            )}</p>
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

    if (state.recents.length && !getSelectedRecent()) {
      return `
        <div class="vss-canvas-empty vss-canvas-empty--status">
          <div class="vss-canvas-empty-copy">
            <div class="vss-canvas-empty-label">${state.recentValidationPending ? "Checking recent outputs" : "No available outputs"}</div>
            <p class="vss-empty-note">${
              state.recentValidationPending
                ? "Vision is verifying your latest media and will promote the newest valid result automatically."
                : "Only unavailable sources are left in recents. Dismiss them from the rail or generate something new."
            }</p>
          </div>
        </div>
      `;
    }

    return `
      <div class="vss-canvas-empty">
        <div class="vss-canvas-empty-copy">
          <svg class="vss-empty-icon" viewBox="0 0 64 64" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <rect x="10" y="13" width="44" height="36" rx="5"></rect>
            <circle cx="42" cy="25" r="5"></circle>
            <path d="m15 44 12-12 10 10 6-6 10 9"></path>
          </svg>
          <div class="vss-canvas-empty-label">Describe the image you want to create</div>
          <div class="vss-canvas-empty-caret" aria-hidden="true"></div>
        </div>
      </div>
    `;
  };

  const renderCanvas = () => `
    <section class="vss-stage">
      <h1 class="vss-page-title">Create an image</h1>
      <div class="vss-canvas">
        ${renderCanvasMedia()}
      </div>
      ${renderDock()}
    </section>
  `;

  const renderGenerationControls = () => {
    const resolutionButtons = VISION_RESOLUTION_OPTIONS.map((resolution) => {
      const active = normalizeResolution(state.resolution) === resolution;
      const label = resolution === "4k" ? "4K" : "1080p";
      return `<button class="vss-control-pill${active ? " is-active" : ""}" type="button" data-resolution="${resolution}" aria-pressed="${active ? "true" : "false"}">${label}</button>`;
    }).join("");
    const aspectRatioButtons = VISION_ASPECT_RATIO_OPTIONS.map((aspectRatio) => {
      const active = normalizeAspectRatio(state.aspectRatio) === aspectRatio;
      const accessibleLabel = VISION_ASPECT_RATIO_LABELS[aspectRatio] || aspectRatio;
      return `<button class="vss-control-pill${active ? " is-active" : ""}" type="button" data-aspect-ratio="${aspectRatio}" aria-label="${accessibleLabel}" title="${accessibleLabel}" aria-pressed="${active ? "true" : "false"}">${aspectRatio}</button>`;
    }).join("");
    return `
      <div class="vss-generation-controls" aria-label="Generation settings">
        <div class="vss-control-group" aria-label="Output resolution">
          <span>Quality</span>
          <div class="vss-control-pills">${resolutionButtons}</div>
        </div>
        <div class="vss-control-group vss-control-group--format" aria-label="Image format">
          <span>Format</span>
          <div class="vss-control-pills">${aspectRatioButtons}</div>
        </div>
      </div>
    `;
  };

  const renderDock = () => {
    return `
    <div class="vss-dock">
      <form class="vss-prompt-bar" id="vss-prompt-form">
        <div class="vss-prompt-field">
          <textarea
            class="vss-prompt-input"
            id="vss-prompt-input"
            rows="2"
            placeholder="Describe the image you want to create"
          >${escapeHtml(state.prompt)}</textarea>
          <button class="vss-improve vss-improve--inline${state.prompt.trim() ? "" : " is-disabled"}" id="vss-improve-button" type="button" ${
            state.prompt.trim() ? "" : "hidden aria-hidden=\"true\""
          } ${state.prompt.trim() ? "" : "disabled aria-disabled=\"true\""}>${state.improveLoading ? "Improving..." : "Improve"}</button>
        </div>
        <div class="vss-prompt-actions">
          <button class="vss-submit" type="submit" aria-label="Generate">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <path d="M7 12h10"></path>
              <path d="m13 6 6 6-6 6"></path>
            </svg>
          </button>
          <span class="vss-submit-label">Generate</span>
        </div>
      </form>
      <div class="vss-dock-settings" aria-label="Studio generation controls">
        ${renderGenerationControls()}
      </div>
    </div>
  `;
  };

  const renderViewer = () => {
    if (!state.viewer.open || !state.viewer.assetUrl) {
      return "";
    }
    const viewerTitle = state.viewer.title || (state.viewer.kind === "video" ? "Vision video" : "Vision image");
    const viewerMedia =
      state.viewer.kind === "video"
        ? `<video class="vss-viewer-video" src="${escapeHtml(state.viewer.assetUrl)}" controls autoplay playsinline></video>`
        : `<img class="vss-viewer-image" src="${escapeHtml(state.viewer.assetUrl)}" alt="${escapeHtml(viewerTitle)}" />`;
    return `
      <div class="vss-viewer-backdrop" id="vss-viewer-backdrop" aria-hidden="false">
        <div class="vss-viewer-panel" role="dialog" aria-modal="true" aria-labelledby="vss-viewer-title">
          <button class="vss-viewer-close" id="vss-viewer-close" type="button" aria-label="Close viewer">Close</button>
          <div class="vss-viewer-media">
            ${viewerMedia}
          </div>
          <div class="vss-viewer-meta">
            <div>
              <div class="vss-viewer-kicker">Studio / Viewer</div>
              <h2 class="vss-viewer-title" id="vss-viewer-title">${escapeHtml(viewerTitle)}</h2>
              <p class="vss-viewer-caption">${escapeHtml(summarizeDescription(state.viewer.caption || "", "Generated inside Vision.", 92))}</p>
            </div>
            <div class="vss-viewer-actions">
              <button class="vss-viewer-action is-primary" type="button" data-viewer-download>Download</button>
              <a class="vss-viewer-link is-secondary" href="${escapeHtml(state.viewer.assetUrl)}" target="_blank" rel="noopener noreferrer">Open in new tab</a>
            </div>
          </div>
        </div>
      </div>
    `;
  };

  const getMenuAnchorStyle = () => {
    if (!state.menuAnchor) {
      return "";
    }
    return `style="top:${Math.round(state.menuAnchor.top)}px; left:${Math.round(state.menuAnchor.left)}px; width:${Math.round(state.menuAnchor.width || 216)}px;"`;
  };

  const renderRecentMenu = () => {
    const item = getRecentById(state.menuOpenFor);
    if (!item || !state.menuAnchor) {
      return "";
    }
    const asset = getAssetAvailability(item.src);
    return `
      <button class="vss-menu-layer" id="vss-menu-layer" type="button" aria-label="Close recent actions"></button>
      <div class="vss-recent-popover is-floating" data-menu-panel="${escapeHtml(item.id)}" role="menu" ${getMenuAnchorStyle()}>
        ${
          asset.available
            ? `<button class="vss-recent-popover-action" type="button" data-open-id="${escapeHtml(item.id)}">Open</button>`
            : `<button class="vss-recent-popover-action" type="button" disabled aria-disabled="true">Open unavailable</button>`
        }
        ${
          asset.available
            ? `<button class="vss-recent-popover-action" type="button" data-download-id="${escapeHtml(item.id)}">Download</button>`
            : `<button class="vss-recent-popover-action" type="button" disabled aria-disabled="true">Download unavailable</button>`
        }
        <button class="vss-recent-popover-action vss-recent-popover-action--danger" type="button" data-delete-id="${escapeHtml(item.id)}">Delete</button>
      </div>
    `;
  };

  const renderRecents = () => `
    <aside class="vss-rail">
      <div class="vss-rail-inner">
        <div class="vss-rail-head">
          <div>
            <div class="vss-rail-kicker">Recent</div>
            <p class="vss-rail-subtitle">Your latest images.</p>
          </div>
          <span class="vss-rail-count">${state.recents.length}</span>
        </div>
        ${
          state.recents.length
            ? `<div class="vss-recent-list">
                ${state.recents
                  .map((item) => {
                    const isSelected = state.selectedId === item.id;
                    const asset = getAssetAvailability(item.src);
                    const assetUrl = asset.assetUrl;
                    const hasAsset = asset.available;
                    const assetMissing = asset.state === "missing" || asset.state === "invalid";
                    const mediaLabel = item.kind === "video" ? "Video" : "Image";
                    const recentTitle = summarizePrompt(item.prompt, item.kind === "video" ? "Vision render" : "Vision still", 46);
                    const media =
                      hasAsset
                        ? item.kind === "video"
                          ? `<video src="${escapeHtml(assetUrl)}" muted loop playsinline autoplay></video>`
                          : `<img src="${escapeHtml(assetUrl)}" alt="${escapeHtml(recentTitle)}" />`
                        : `<div class="vss-recent-missing">${assetMissing ? "Source unavailable" : "Checking source..."}</div>`;
                    return `
                      <article class="vss-recent-card${hasAsset ? " is-media-only" : ""}${isSelected ? " is-selected" : ""}${assetMissing ? " is-stale" : ""}">
                        <button class="vss-recent-select" type="button" data-recent-id="${escapeHtml(item.id)}" aria-label="${escapeHtml(`${mediaLabel}: ${recentTitle}`)}" ${hasAsset ? "" : "disabled aria-disabled=\"true\""}>
                          <div class="vss-recent-thumb">
                            ${media}
                            <span class="vss-recent-overlay" aria-hidden="true"></span>
                          </div>
                        </button>
                        ${
                          hasAsset
                            ? `<button class="vss-recent-menu-button" type="button" data-menu-id="${escapeHtml(item.id)}" aria-label="More actions" aria-haspopup="menu" aria-expanded="${state.menuOpenFor === item.id ? "true" : "false"}">•••</button>`
                            : `<div class="vss-recent-meta">
                                <div class="vss-recent-topline">
                                  <span class="vss-recent-type">${escapeHtml(mediaLabel)}</span>
                                  <span class="vss-recent-date">${escapeHtml(`${formatDate(item.createdAt)} · ${formatTime(item.createdAt)}`)}</span>
                                </div>
                                <p class="vss-recent-title">${escapeHtml(recentTitle)}</p>
                                <div class="vss-recent-bottomline">
                                  <div class="vss-recent-statuses">
                                    ${assetMissing ? '<span class="vss-recent-status is-stale">Source unavailable</span>' : ""}
                                  </div>
                                  <div class="vss-recent-toolbar">
                                    ${assetMissing ? `<button class="vss-recent-dismiss" type="button" data-delete-id="${escapeHtml(item.id)}">Dismiss</button>` : ""}
                                    <button class="vss-recent-menu-button" type="button" data-menu-id="${escapeHtml(item.id)}" aria-label="More actions" aria-haspopup="menu" aria-expanded="${state.menuOpenFor === item.id ? "true" : "false"}">•••</button>
                                  </div>
                                </div>
                              </div>`
                        }
                      </article>
                    `;
                  })
                  .join("")}
              </div>`
            : `<div class="vss-rail-empty">
                <svg class="vss-rail-empty-icon" viewBox="0 0 48 48" fill="none" stroke="currentColor" stroke-width="1.4" aria-hidden="true">
                  <rect x="7" y="9" width="34" height="29" rx="4"></rect>
                  <path d="m11 34 9-9 8 8 5-5 7 7"></path>
                  <circle cx="32" cy="18" r="3"></circle>
                </svg>
                <p class="vss-rail-empty-title">No images yet</p>
                <p class="vss-rail-empty-copy">Generated images will appear here.</p>
              </div>`
        }
      </div>
    </aside>
  `;

  const renderAccountPanel = () => {
    if (!state.accountPanelOpen) {
      return "";
    }

    const signedIn = hasAccountContext();
    const showAccount = signedIn;
    const hasActivePlan = !!state.access.admin || !!state.subscription.active || hasPackContext();
    const emailLabel = shortenEmail(state.user.email);
    const pack = state.currentPack || getPackById(DEFAULT_PACK_ID);
    const priceLabel = `${formatPackPrice(pack)} / month`;
    const renewalDate = formatRenewalDate(state.subscription.current_period_end);
    const renewalCopy = renewalDate
      ? state.subscription.cancel_at_period_end
        ? `Access ends ${renewalDate}`
        : `Renews ${renewalDate}`
      : "Billed monthly. Cancel anytime.";

    return `
      <div class="vss-account-overlay" id="vss-account-overlay">
        <div class="vss-account-panel" role="dialog" aria-modal="true" aria-labelledby="vss-account-title">
          <div class="vss-account-panel-head">
            <div>
              <div class="vss-account-panel-kicker">${showAccount ? "Account" : "Welcome back"}</div>
              <h2 class="vss-account-panel-title" id="vss-account-title">${showAccount ? "Your Vision account" : state.authStep === "code" ? "Check your email" : "Sign in to Vision"}</h2>
              <p class="vss-account-panel-copy">${
                showAccount
                  ? "Manage your Studio plan and account."
                  : state.authStep === "code"
                    ? `Enter the six-digit code sent to ${escapeHtml(state.authPendingEmail)}.`
                    : "Use your email to continue. No password needed."
              }</p>
            </div>
            <button class="vss-account-panel-close" id="vss-account-panel-close" type="button" aria-label="Close account panel">×</button>
          </div>
          ${
            showAccount
              ? `<div class="vss-account-summary">
                  <div class="vss-account-identity">
                    <span class="vss-account-avatar">${escapeHtml((state.user.email || "A").charAt(0).toUpperCase())}</span>
                    <div class="vss-account-summary-copy">
                      <strong>${escapeHtml(emailLabel)}</strong>
                      <span>Signed in</span>
                    </div>
                  </div>
                  <div class="vss-subscription-card${hasActivePlan ? " is-active" : ""}">
                    <div class="vss-subscription-status">
                      <span class="vss-status-dot" aria-hidden="true"></span>
                      <span>${hasActivePlan ? "Subscription active" : "No active plan"}</span>
                    </div>
                    <div class="vss-subscription-heading">
                      <div>
                        <strong>Vision Studio</strong>
                        <span>Unlimited 4K images</span>
                      </div>
                      <strong class="vss-subscription-price">${escapeHtml(priceLabel)}</strong>
                    </div>
                    <p class="vss-subscription-renewal">${escapeHtml(hasActivePlan ? renewalCopy : "Start creating in 4K with one simple monthly plan.")}</p>
                  </div>
                  <div class="vss-account-actions">
                    ${
                      hasActivePlan
                        ? state.access.admin
                          ? ""
                          : `<button class="vss-account-primary" id="vss-manage-subscription" type="button" ${state.portalLoading ? "disabled aria-disabled=\"true\"" : ""}>${state.portalLoading ? "Opening..." : "Manage subscription"}</button>`
                        : `<button class="vss-account-primary" id="vss-buy-pack" type="button" data-buy-credits ${state.checkoutLoading ? "disabled aria-disabled=\"true\"" : ""}>${state.checkoutLoading ? "Opening..." : `Start Vision Studio — ${escapeHtml(formatPackPrice(pack))}`}</button>`
                    }
                    <button class="vss-account-secondary" id="vss-logout" type="button">${state.authLoading ? "Logging out..." : "Log out"}</button>
                  </div>
                </div>`
              : `<form class="vss-access-form" id="vss-auth-form">
                  <label class="vss-form-label" for="vss-auth-email">Email</label>
                  <input class="vss-form-input" id="vss-auth-email" type="email" value="${escapeHtml(state.authPendingEmail)}" placeholder="you@example.com" autocomplete="email" ${state.authStep === "code" ? "readonly" : ""} />
                  ${
                    state.authStep === "code"
                      ? `<label class="vss-form-label" for="vss-auth-code">Code</label>
                         <input class="vss-form-input vss-form-input--code" id="vss-auth-code" type="text" value="${escapeHtml(state.authPendingCode)}" inputmode="numeric" autocomplete="one-time-code" maxlength="6" placeholder="000000" />`
                      : ""
                  }
                  <div class="vss-account-actions">
                    <button class="vss-account-primary" id="vss-auth-submit" type="submit" ${state.authLoading ? "disabled aria-disabled=\"true\"" : ""}>${state.authLoading ? "Please wait..." : state.authStep === "code" ? "Verify and continue" : "Continue with email"}</button>
                    ${state.authStep === "code" ? '<button class="vss-account-secondary" id="vss-auth-change-email" type="button">Use another email</button>' : ""}
                  </div>
                </form>`
          }
          <p class="vss-account-panel-note" role="status">${escapeHtml(state.authNote || (showAccount ? "" : "We’ll email you a one-time sign-in code."))}</p>
        </div>
      </div>
    `;
  };

  const render = () => {
    document.body.classList.toggle("vss-viewer-open", state.viewer.open);
    root.innerHTML = `
      <div class="vss-shell">
        <div class="vss-app">
          ${renderHeader()}
          <main class="vss-main">
            ${renderCanvas()}
            ${renderRecents()}
          </main>
          ${renderRecentMenu()}
          ${renderViewer()}
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
    const recentsList = root.querySelector(".vss-recent-list");

    const syncImproveButton = () => {
      if (!improveButton) {
        return;
      }
      const hasPrompt = !!String(promptInput && promptInput.value ? promptInput.value : state.prompt || "").trim();
      improveButton.hidden = !hasPrompt;
      improveButton.setAttribute("aria-hidden", hasPrompt ? "false" : "true");
      improveButton.disabled = state.improveLoading || !hasPrompt;
      if (state.improveLoading || !hasPrompt) {
        improveButton.setAttribute("aria-disabled", "true");
      } else {
        improveButton.removeAttribute("aria-disabled");
      }
      improveButton.classList.toggle("is-disabled", !hasPrompt);
      improveButton.removeAttribute("title");
    };

    promptInput?.addEventListener("input", (event) => {
      state.prompt = String(event.target.value || "");
      syncImproveButton();
    });

    promptForm?.addEventListener("submit", (event) => {
      event.preventDefault();
      state.prompt = String(promptInput && promptInput.value ? promptInput.value : state.prompt || "");
      submitPrompt();
    });

    improveButton?.addEventListener("click", () => {
      improvePrompt();
    });

    syncImproveButton();

    root.querySelectorAll("[data-mode]").forEach((button) => {
      button.addEventListener("click", () => {
        state.mode = "image";
        render();
      });
    });

    root.querySelectorAll("[data-duration]").forEach((button) => {
      button.addEventListener("click", () => {
        state.durationSeconds = normalizeDurationSeconds(button.getAttribute("data-duration"));
        render();
      });
    });

    root.querySelectorAll("[data-resolution]").forEach((button) => {
      button.addEventListener("click", () => {
        state.resolution = normalizeResolution(button.getAttribute("data-resolution"));
        render();
      });
    });

    root.querySelectorAll("[data-aspect-ratio]").forEach((button) => {
      button.addEventListener("click", () => {
        state.aspectRatio = normalizeAspectRatio(button.getAttribute("data-aspect-ratio"));
        render();
      });
    });

    root.querySelector("[data-sound-toggle]")?.addEventListener("click", () => {
      state.soundEnabled = !state.soundEnabled;
      render();
    });

    root.querySelectorAll("[data-recent-id]").forEach((button) => {
      button.addEventListener("click", () => {
        const nextId = button.getAttribute("data-recent-id") || "";
        selectRecent(nextId, { openViewer: true });
      });
    });

    root.querySelector("[data-open-current-preview]")?.addEventListener("click", () => {
      openViewerForItem(getSelectedRecent());
    });

    root.querySelector("[data-open-current]")?.addEventListener("click", () => {
      openViewerForItem(getSelectedRecent());
    });

    root.querySelector("[data-download-current]")?.addEventListener("click", async (event) => {
      const button = event.currentTarget;
      await withDownloadFeedback(button, getSelectedRecent());
    });

    root.querySelector("[data-viewer-download]")?.addEventListener("click", async (event) => {
      const button = event.currentTarget;
      const selected = getSelectedRecent();
      await withDownloadFeedback(button, selected);
    });

    root.querySelectorAll("[data-menu-id]").forEach((button) => {
      button.addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        const menuId = button.getAttribute("data-menu-id") || "";
        if (state.menuOpenFor === menuId) {
          clearRecentMenu();
          render();
          return;
        }
        const rect = button.getBoundingClientRect();
        const menuWidth = 216;
        const menuHeight = 170;
        const opensUp = rect.bottom + menuHeight + 14 > window.innerHeight;
        state.menuOpenFor = menuId;
        state.menuAnchor = {
          width: menuWidth,
          top: opensUp ? Math.max(16, rect.top - menuHeight - 10) : Math.min(window.innerHeight - menuHeight - 16, rect.bottom + 10),
          left: Math.min(window.innerWidth - menuWidth - 16, Math.max(16, rect.right - menuWidth)),
        };
        render();
      });
    });

    root.querySelectorAll("[data-download-id]").forEach((button) => {
      button.addEventListener("click", async (event) => {
        event.preventDefault();
        event.stopPropagation();
        const buttonNode = event.currentTarget;
        const itemId = buttonNode.getAttribute("data-download-id") || "";
        const item = getRecentById(itemId);
        await withDownloadFeedback(buttonNode, item);
      });
    });

    root.querySelectorAll("[data-open-id]").forEach((button) => {
      button.addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        const itemId = event.currentTarget.getAttribute("data-open-id") || "";
        selectRecent(itemId, { openViewer: true });
      });
    });

    root.querySelectorAll("[data-delete-id]").forEach((button) => {
      button.addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        const deleteId = button.getAttribute("data-delete-id") || "";
        clearRecentMenu();
        deleteHistoryItem(deleteId);
        render();
      });
    });

    root.querySelector("#vss-menu-layer")?.addEventListener("click", () => {
      clearRecentMenu();
      render();
    });

    recentsList?.addEventListener(
      "scroll",
      () => {
        if (!state.menuOpenFor) {
          return;
        }
        clearRecentMenu();
        render();
      },
      { passive: true },
    );

    root.querySelector("#vss-account-pill")?.addEventListener("click", () => {
      clearRecentMenu();
      state.accountPanelOpen = !state.accountPanelOpen;
      if (state.accountPanelOpen) {
        state.authStep = hasAccountContext() ? "account" : "email";
        state.authPendingEmail = state.user.email || state.authPendingEmail || "";
      }
      render();
    });

    root.querySelector("#vss-account-panel-close")?.addEventListener("click", () => {
      state.accountPanelOpen = false;
      state.menuOpenFor = "";
      render();
    });

    root.querySelector("#vss-account-overlay")?.addEventListener("click", (event) => {
      if (event.target && event.target.id === "vss-account-overlay") {
        state.accountPanelOpen = false;
        render();
      }
    });

    root.querySelector("#vss-viewer-close")?.addEventListener("click", () => {
      closeViewer();
    });

    root.querySelector("#vss-viewer-backdrop")?.addEventListener("click", (event) => {
      if (event.target && event.target.id === "vss-viewer-backdrop") {
        closeViewer();
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

    root.querySelector("#vss-auth-change-email")?.addEventListener("click", () => {
      state.authStep = "email";
      state.authPendingCode = "";
      state.authNote = "";
      render();
    });

    root.querySelectorAll("[data-buy-credits]").forEach((button) => {
      button.addEventListener("click", () => {
        if (!state.user.authenticated) {
          state.accountPanelOpen = true;
          state.authStep = "email";
          state.authNote = "Sign in first, then start Vision Studio.";
          render();
          return;
        }
        openCheckout();
      });
    });

    root.querySelector("#vss-manage-subscription")?.addEventListener("click", () => {
      openCustomerPortal();
    });

    root.querySelector("#vss-logout")?.addEventListener("click", () => {
      logout();
    });
  };

  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") {
      return;
    }
    if (state.viewer.open) {
      event.preventDefault();
      closeViewer();
      return;
    }
    if (state.menuOpenFor) {
      event.preventDefault();
      clearRecentMenu();
      render();
      return;
    }
    if (state.accountPanelOpen) {
      state.accountPanelOpen = false;
      render();
    }
  });

  window.addEventListener("resize", () => {
    if (!state.menuOpenFor) {
      return;
    }
    clearRecentMenu();
    render();
  });

  const init = async () => {
    trackVisionEvent("StudioViewed", { platform_context: "web" });
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
