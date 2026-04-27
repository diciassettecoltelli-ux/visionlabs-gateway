const body = document.body;
const atomTrigger = document.querySelector(".atom-trigger");
const atomGuideText = document.querySelector("#atom-guide-text");
const searchCluster = document.querySelector(".search-cluster");
const searchForm = document.querySelector("#vision-search-form");
const promptInput = document.querySelector("#vision-prompt");
const searchSubmit = document.querySelector(".search-launch-button");
const promptHelper = document.querySelector("#prompt-helper");
const improvePromptButton = document.querySelector("#improve-prompt-button");
const modeButtons = document.querySelectorAll("[data-generation-mode]");
const accessPill = document.querySelector("#vision-access-pill");
const galleryButtons = document.querySelectorAll(".gallery-open");
const galleryLightbox = document.querySelector("#gallery-lightbox");
const galleryLightboxVideo = document.querySelector("#gallery-lightbox-video");
const galleryLightboxImage = document.querySelector("#gallery-lightbox-image");
const galleryLightboxTitle = document.querySelector("#gallery-lightbox-title");
const galleryLightboxCaption = document.querySelector("#gallery-lightbox-caption");
const galleryLightboxClose = document.querySelector(".gallery-lightbox-close");
const subscribeTriggers = document.querySelectorAll("[data-subscribe-trigger]");
const studioTriggers = document.querySelectorAll("[data-enter-studio]");
const subscribeModal = document.querySelector("#subscribe-modal");
const subscribeClose = document.querySelector(".subscribe-close");
const subscribeForm = document.querySelector("#subscribe-form");
const subscribeEmail = document.querySelector("#subscribe-email");
const subscribeSuccess = document.querySelector("#subscribe-success");
const subscribeKicker = document.querySelector(".subscribe-kicker");
const subscribeTitle = document.querySelector("#subscribe-title");
const subscribeCopy = document.querySelector(".subscribe-copy");
const subscribePackGrid = document.querySelector("#subscribe-pack-grid");
const subscribeSubmit = document.querySelector(".subscribe-submit");
const subscribeNote = document.querySelector("#subscribe-note");
const subscribeReturningLink = document.querySelector("#subscribe-returning-link");
const footerAccessVision = document.querySelector("#footer-access-vision");
const authModal = document.querySelector("#auth-modal");
const authClose = document.querySelector(".auth-close");
const authTitle = document.querySelector("#auth-title");
const authCopy = document.querySelector("#auth-copy");
const authForm = document.querySelector("#auth-form");
const authEmail = document.querySelector("#auth-email");
const authCodeRow = document.querySelector("#auth-code-row");
const authCode = document.querySelector("#auth-code");
const authSubmit = document.querySelector("#auth-submit");
const authReset = document.querySelector("#auth-reset");
const authNote = document.querySelector("#auth-note");
const authAccount = document.querySelector("#auth-account");
const authAccountEmail = document.querySelector("#auth-account-email");
const authAccountCredits = document.querySelector("#auth-account-credits");
const authEnterStudio = document.querySelector("#auth-enter-studio");
const authBuyPack = document.querySelector("#auth-buy-pack");
const authLogout = document.querySelector("#auth-logout");
const generationModal = document.querySelector("#generation-modal");
const generationClose = document.querySelector(".generation-close");
const generationTitle = document.querySelector("#generation-title");
const generationState = document.querySelector("#generation-state");
const generationCopy = document.querySelector("#generation-copy");
const generationPrompt = document.querySelector("#generation-prompt");
const generationSteps = document.querySelectorAll(".generation-step");
const generationReady = document.querySelector("#generation-ready");
const generationVideo = document.querySelector("#generation-video");
const generationImage = document.querySelector("#generation-image");
const generationPreview = document.querySelector("#generation-preview");
const generationPreviewPlaceholder = document.querySelector("#generation-preview-placeholder");
const generationPreviewStage = document.querySelector("#generation-preview-stage");
const generationPreviewNote = document.querySelector("#generation-preview-note");
const generationExpand = document.querySelector("#generation-expand");
const generationDownload = document.querySelector("#generation-download");
const generationPrepare = document.querySelector("#generation-prepare");
const generationDeliveryTitle = document.querySelector("#generation-delivery-title");
const generationDeliveryNote = document.querySelector("#generation-delivery-note");
const topNav = document.querySelector(".topnav");
const topbarCta = document.querySelector(".topbar-cta");
const legacyRoot = document.querySelector("#vision-legacy-root");
const studioShellRoot = document.querySelector("#studio-shell-new-root");
const legacyStyle = document.querySelector("#vision-legacy-style");
const studioDashboard = document.querySelector("#studio-dashboard");
const studioVideoCredits = document.querySelector("#studio-video-credits");
const studioImageCredits = document.querySelector("#studio-image-credits");
const studioPackStatus = document.querySelector("#studio-pack-status");
const studioHistoryCount = document.querySelector("#studio-history-count");
const studioHistoryBadge = document.querySelector("#studio-history-badge");
const studioHistoryGrid = document.querySelector("#studio-history-grid");
const studioHistoryEmpty = document.querySelector("#studio-history-empty");
const studioAccountButton = document.querySelector("#studio-account-button");
const studioTopupButton = document.querySelector("#studio-topup-button");
const studioOutputShell = document.querySelector(".studio-output-shell");
const studioLoader = document.querySelector("#studio-loader");
const studioOutputStage = document.querySelector("#studio-output-stage");
const studioOutputVideo = document.querySelector("#studio-output-video");
const studioOutputImage = document.querySelector("#studio-output-image");
const studioOutputPlaceholder = document.querySelector("#studio-output-placeholder");
const studioOutputStageLabel = document.querySelector("#studio-output-stage-label");
const studioOutputStageNote = document.querySelector("#studio-output-stage-note");
const studioOutputMeta = document.querySelector("#studio-output-meta");

const configuredApiBase = typeof window.VISION_API_BASE === "string" ? window.VISION_API_BASE.trim() : "";
const runningOnLocalVision = ["localhost", "127.0.0.1"].includes(window.location.hostname);
const VISION_API_BASE =
  configuredApiBase || (runningOnLocalVision ? "http://127.0.0.1:8787" : "https://vision-gateway.onrender.com");
const VISION_STUDIO_PATH = "/studio/";
const STUDIO_SHELL_ASSET_VERSION = "126";
const STUDIO_SHELL_CSS_HREF = `/studio-shell-new.css?v=${STUDIO_SHELL_ASSET_VERSION}`;
const STUDIO_SHELL_JS_HREF = `/studio-shell-new.js?v=${STUDIO_SHELL_ASSET_VERSION}`;
const isStudioRoute = /^\/studio\/?$/.test(window.location.pathname);
const VISION_ACCESS_STORAGE_KEY = "vision_access_token";
const VISION_PENDING_PROMPT_KEY = "vision_pending_prompt";
const VISION_HISTORY_STORAGE_KEY = "vision_generation_history_v1";

const trackVisionEvent = (name, payload = {}) =>
  window.VisionTracking && typeof window.VisionTracking.trackEvent === "function"
    ? window.VisionTracking.trackEvent(name, payload)
    : null;

const getVisionTrackingContext = (overrides = {}) =>
  window.VisionTracking && typeof window.VisionTracking.getContext === "function"
    ? window.VisionTracking.getContext(overrides)
    : { ...overrides };

const defaultPacks = [
  {
    id: "starter",
    name: "Vision Starter",
    subtitle: "Short videos + images",
    description: "For testing ideas, short clips, and images.",
    price_cents: 990,
    original_price_cents: 1490,
    currency: "eur",
    vision_credits: 500000,
    credit_label: "500K credits",
    total_credit_label: "500.000 total credits",
    discount_label: "Save 34%",
    video_credits: 5,
    image_credits: 50,
    video_label: "5 videos",
    duration_label: "Videos up to 15 seconds",
    image_label: "50 images",
    value_label: "Creates up to 5 videos or 50 images.",
    example_label: "Examples: up to 5 short videos or 50 images.",
    badge: "",
    cta_label: "Start with Starter",
    features: ["Prompt enhancement", "Watermark-free exports", "Private downloads"],
  },
  {
    id: "creator",
    name: "Vision Creator",
    subtitle: "Best value for creators",
    description: "Best value for creators and social content.",
    price_cents: 1990,
    original_price_cents: 3990,
    currency: "eur",
    vision_credits: 2000000,
    credit_label: "2M credits",
    total_credit_label: "2.000.000 total credits",
    discount_label: "Save 50%",
    video_credits: 10,
    image_credits: 200,
    video_label: "10 videos",
    duration_label: "Videos up to 15 seconds",
    image_label: "200 images",
    value_label: "Creates up to 10 standard videos or 200 images.",
    example_label: "Examples: up to 10 standard 10s videos or 200 images.",
    badge: "Best value",
    cta_label: "Choose Creator",
    features: ["Full HD video generation", "Sound on/off control", "Premium cinematic prompt refinement", "Watermark-free exports"],
  },
  {
    id: "pro",
    name: "Vision Pro",
    subtitle: "Premium generation",
    description: "For campaigns, premium clips, and heavier creation.",
    price_cents: 2990,
    original_price_cents: 8990,
    currency: "eur",
    vision_credits: 5000000,
    credit_label: "5M credits",
    total_credit_label: "5.000.000 total credits",
    discount_label: "Save 67%",
    video_credits: 25,
    image_credits: 500,
    video_label: "25 videos",
    duration_label: "Videos up to 15 seconds",
    image_label: "500 images",
    value_label: "Creates up to 25 standard videos or 500 images.",
    example_label: "Examples: up to 25 standard 10s videos, premium clips, or 500 images.",
    badge: "Premium",
    cta_label: "Go Pro",
    features: ["Up to 4K-ready output", "Advanced cinematic refinement", "Sound on/off control", "Campaign-ready exports"],
  },
];

const defaultPack = { ...defaultPacks[0] };

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

const promptHelperDefaults = {
  video: "Describe the subject first. Vision sharpens lighting, motion, framing, and realism before generation begins.",
  image: "Describe the image first. Vision sharpens atmosphere, texture, framing, and still-frame impact before generation begins.",
};

const visionApiUrl = (path) => `${VISION_API_BASE}${path}`;

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
  return `${VISION_API_BASE}${assetPath}`;
};

const parseJsonSafely = async (response) => {
  try {
    return await response.json();
  } catch (error) {
    return null;
  }
};

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
    // Ignore storage failures in restricted browsing modes.
  }
};

const savePendingPrompt = (prompt, mode) => {
  try {
    window.sessionStorage.setItem(
      VISION_PENDING_PROMPT_KEY,
      JSON.stringify({
        prompt,
        mode,
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
    if (!parsed?.prompt) {
      return null;
    }
    return {
      prompt: String(parsed.prompt),
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

const wait = (ms) =>
  new Promise((resolve) => {
    window.setTimeout(resolve, ms);
  });

const visionFetch = (path, options = {}) => {
  const headers = {
    ...(options.headers || {}),
  };
  const token = readStoredAccessToken();
  if (token && !headers.Authorization) {
    headers.Authorization = `Bearer ${token}`;
  }
  return fetch(visionApiUrl(path), {
    credentials: "include",
    ...options,
    headers,
  });
};

const ensureDynamicStylesheet = (href) => {
  const existing = Array.from(document.querySelectorAll('link[rel="stylesheet"]')).find(
    (node) => node.getAttribute("href") === href,
  );
  if (existing) {
    return existing;
  }
  const link = document.createElement("link");
  link.rel = "stylesheet";
  link.href = href;
  document.head.appendChild(link);
  return link;
};

const ensureDynamicScript = (src) => {
  if (studioShellAssetPromise) {
    return studioShellAssetPromise;
  }

  const existing = Array.from(document.querySelectorAll("script[src]")).find(
    (node) => node.getAttribute("src") === src,
  );

  if (existing?.dataset.loaded === "true" || window.__visionStudioShellReady) {
    studioShellAssetPromise = Promise.resolve();
    return studioShellAssetPromise;
  }

  studioShellAssetPromise = new Promise((resolve, reject) => {
    if (existing) {
      existing.addEventListener(
        "load",
        () => {
          existing.dataset.loaded = "true";
          resolve();
        },
        { once: true },
      );
      existing.addEventListener("error", reject, { once: true });
      return;
    }

    const script = document.createElement("script");
    script.src = src;
    script.async = false;
    script.addEventListener(
      "load",
      () => {
        script.dataset.loaded = "true";
        resolve();
      },
      { once: true },
    );
    script.addEventListener("error", reject, { once: true });
    document.body.appendChild(script);
  }).catch((error) => {
    studioShellAssetPromise = null;
    throw error;
  });

  return studioShellAssetPromise;
};

const waitForStudioShellReady = () =>
  new Promise((resolve) => {
    if (window.__visionStudioShellReady) {
      resolve();
      return;
    }
    const handleReady = () => {
      window.removeEventListener("vision-studio-shell-ready", handleReady);
      resolve();
    };
    window.addEventListener("vision-studio-shell-ready", handleReady, { once: true });
  });

const mountStudioInternally = async ({ minimumLoaderMs = 1200 } = {}) => {
  if (studioTransitionInFlight) {
    return;
  }

  studioTransitionInFlight = true;
  const startedAt = performance.now();
  setSubscribeState(false);
  setAuthModalState(false);
  setStudioLoaderState(true);
  body.classList.remove("studio-ready");
  document.body.setAttribute("data-studio-shell", "new");

  if (studioShellRoot) {
    studioShellRoot.hidden = false;
  }

  window.history.pushState({ visionStudio: true }, "", VISION_STUDIO_PATH);

  try {
    ensureDynamicStylesheet(STUDIO_SHELL_CSS_HREF);
    await ensureDynamicScript(STUDIO_SHELL_JS_HREF);
    await waitForStudioShellReady();

    const elapsed = performance.now() - startedAt;
    if (elapsed < minimumLoaderMs) {
      await wait(minimumLoaderMs - elapsed);
    }

    if (legacyRoot) {
      legacyRoot.hidden = true;
      legacyRoot.setAttribute("aria-hidden", "true");
    }
    if (legacyStyle) {
      legacyStyle.disabled = true;
    }

    body.classList.add("studio-ready");
    setStudioLoaderState(false);
  } catch (error) {
    setStudioLoaderState(false);
    studioTransitionInFlight = false;
    throw error;
  }

  studioTransitionInFlight = false;
};

const enterStudio = ({ skipGate = false, trigger = null, reason = "unlock" } = {}) => {
  if (isStudioRoute) {
    setSearchState(true);
    return;
  }

  if (!skipGate) {
    setSubscribeState(true, {
      trigger,
      reason: hasStudioPackContext() ? "insufficient_credits" : reason,
    });
    return;
  }

  void mountStudioInternally();
};

const stripUrlParams = (...params) => {
  const url = new URL(window.location.href);
  let changed = false;
  params.forEach((param) => {
    if (url.searchParams.has(param)) {
      url.searchParams.delete(param);
      changed = true;
    }
  });
  if (changed) {
    window.history.replaceState({}, "", `${url.pathname}${url.search}${url.hash}`);
  }
};

const formatPackPrice = (pack) => {
  const amount = Number(pack?.price_cents ?? defaultPack.price_cents) / 100;
  const currency = String(pack?.currency || defaultPack.currency).toUpperCase();
  try {
    return new Intl.NumberFormat("it-IT", {
      style: "currency",
      currency,
    }).format(amount);
  } catch (error) {
    return `${amount.toFixed(2)} ${currency}`;
  }
};

const formatPackLine = (pack) =>
  `${formatPackPrice(pack)} · one-time unlock · ${pack.credit_label || `${pack.video_credits} videos + ${pack.image_credits} images`}`;

const normalizePack = (pack = {}) => ({
  ...defaultPack,
  ...pack,
  id: String(pack?.id || defaultPack.id || "starter").toLowerCase(),
  features: Array.isArray(pack?.features) && pack.features.length ? pack.features : defaultPack.features,
});

const normalizePackList = (packs) => {
  if (!Array.isArray(packs) || !packs.length) {
    return defaultPacks.map((pack) => normalizePack(pack));
  }
  return packs.map((pack) => normalizePack(pack));
};

const findPackById = (packId, packs = defaultPacks) => {
  const normalizedId = String(packId || "").trim().toLowerCase();
  return normalizePackList(packs).find((pack) => pack.id === normalizedId) || normalizePackList(packs)[0];
};

const packTrackingPayload = (pack) => {
  const normalizedPack = normalizePack(pack || currentPack || defaultPack);
  return {
    plan_id: normalizedPack.id,
    currency: String(normalizedPack.currency || "EUR").toUpperCase(),
    value: Number(normalizedPack.price_cents || 0) / 100,
    plan_credits: normalizedPack.vision_credits || null,
  };
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
    return match?.[1]?.toLowerCase() || fallback;
  } catch (error) {
    const match = String(url).match(/\.([a-z0-9]+)(?:\?|#|$)/i);
    return match?.[1]?.toLowerCase() || fallback;
  }
};

const buildDownloadFilename = ({ prompt, outputType, jobId, url }) => {
  const base = slugifyPrompt(prompt) || (outputType === "image" ? "visual" : "render");
  const shortId = String(jobId || "").slice(0, 8);
  const extension = inferDownloadExtension(url, outputType);
  const suffix = shortId ? `-${shortId}` : "";
  return `vision-${outputType}-${base}${suffix}.${extension}`;
};

let selectedMode = "video";
let activeGenerationJobId = null;
let generationPollHandle = null;
let visionFocusGuardHandle = null;
let improvePromptInFlight = false;
let accessState = { ...defaultAccess };
let currentUser = { ...defaultUser };
let currentPacks = normalizePackList(defaultPacks);
let selectedPackId = "creator";
let currentPack = { ...findPackById(selectedPackId, currentPacks) };
let currentGenerationOutput = null;
let currentStudioJob = null;
let studioSelectedHistoryId = "";
let lastSubscribeTrigger = null;
let authPendingEmail = "";
let authStep = "email";
let studioTransitionInFlight = false;
let studioShellAssetPromise = null;

const getStudioHistoryStorageKey = () => {
  const identity = String(currentUser?.email || "guest")
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9@._-]+/g, "-");
  return `${VISION_HISTORY_STORAGE_KEY}:${identity || "guest"}`;
};

const readStudioHistory = () => {
  try {
    const raw = window.localStorage.getItem(getStudioHistoryStorageKey());
    if (!raw) {
      return [];
    }
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed)
      ? parsed
          .filter((entry) => entry && entry.id && entry.src)
          .map((entry) => ({
            ...entry,
            src: visionAssetUrl(entry.src),
          }))
      : [];
  } catch (error) {
    return [];
  }
};

const writeStudioHistory = (items) => {
  try {
    window.localStorage.setItem(getStudioHistoryStorageKey(), JSON.stringify(items));
  } catch (error) {
    // Ignore storage failures.
  }
};

const formatStudioTimestamp = (value) => {
  if (!value) {
    return "Just now";
  }
  try {
    return new Intl.DateTimeFormat("en-GB", {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    }).format(new Date(value));
  } catch (error) {
    return "Just now";
  }
};

const saveStudioHistoryItem = (job, src) => {
  if (!job?.id || !src) {
    return;
  }
  const outputType = (job.output_type || job.mode || "video").toLowerCase() === "image" ? "image" : "video";
  const resolvedSrc = visionAssetUrl(src);
  const item = {
    id: String(job.id),
    type: outputType,
    src: resolvedSrc,
    prompt: String(job.prompt || "").trim(),
    created_at: job.completed_at || job.updated_at || new Date().toISOString(),
  };
  const items = readStudioHistory().filter((entry) => entry?.id !== item.id);
  items.unshift(item);
  writeStudioHistory(items.slice(0, 10));
  studioSelectedHistoryId = item.id;
};

const getStudioHistoryItemById = (id) => {
  const normalizedId = String(id || "").trim();
  if (!normalizedId) {
    return null;
  }
  return readStudioHistory().find((entry) => String(entry?.id || "") === normalizedId) || null;
};

const getCurrentStudioHistoryItem = () => {
  const items = readStudioHistory();
  if (!items.length) {
    return null;
  }
  if (studioSelectedHistoryId) {
    return items.find((entry) => entry?.id === studioSelectedHistoryId) || items[0] || null;
  }
  return items[0] || null;
};

const deleteStudioHistoryItem = (id) => {
  const normalizedId = String(id || "").trim();
  if (!normalizedId) {
    return;
  }
  const items = readStudioHistory().filter((entry) => String(entry?.id || "") !== normalizedId);
  writeStudioHistory(items);
  if (studioSelectedHistoryId === normalizedId) {
    studioSelectedHistoryId = items[0]?.id || "";
  }
};

const getStudioCreditCounts = () => ({
  vision: Math.max(0, Number(accessState.vision_credits_remaining ?? 0) || 0),
  visionPurchased: Math.max(0, Number(accessState.vision_credits_purchased ?? 0) || 0),
  video: Math.max(0, Number(accessState.video_remaining ?? 0) || 0),
  image: Math.max(0, Number(accessState.image_remaining ?? 0) || 0),
});

const hasStudioCredits = () => {
  const counts = getStudioCreditCounts();
  return counts.vision > 0 || counts.video > 0 || counts.image > 0;
};

const formatVisionCredits = (value) => Math.max(0, Number(value || 0)).toLocaleString("it-IT");

const getStudioCreditLabel = () => {
  const counts = getStudioCreditCounts();
  if (counts.vision > 0 || counts.visionPurchased > 0) {
    return `${formatVisionCredits(counts.vision)} Vision credits`;
  }
  return `${counts.video} videos · ${counts.image} images`;
};

const hasStudioPackContext = () => !!accessState.admin || !!accessState.access_id || hasStudioCredits();

const hasStudioAccountContext = () =>
  !!currentUser.authenticated || !!currentUser.email || hasStudioPackContext();

const syncTopbarCta = () => {
  if (!topbarCta) {
    return;
  }

  if (isStudioRoute) {
    if (currentUser.email) {
      topbarCta.textContent = currentUser.email || "My account";
    } else if (accessState.admin) {
      topbarCta.textContent = "Studio unlocked";
    } else if (hasStudioPackContext()) {
      topbarCta.textContent = getStudioCreditLabel();
    } else {
      topbarCta.textContent = "Access Vision";
    }
    topbarCta.setAttribute("href", VISION_STUDIO_PATH);
    topbarCta.removeAttribute("data-enter-studio");
    topbarCta.removeAttribute("aria-current");
    return;
  }

  if (hasStudioAccountContext()) {
    topbarCta.textContent = "Return to Studio";
    topbarCta.setAttribute("href", VISION_STUDIO_PATH);
    topbarCta.removeAttribute("data-enter-studio");
    topbarCta.removeAttribute("aria-current");
    return;
  }

  topbarCta.textContent = "Enter Vision Studio";
  topbarCta.setAttribute("href", VISION_STUDIO_PATH);
  topbarCta.setAttribute("data-enter-studio", "");
  topbarCta.removeAttribute("aria-current");
};

const getLatestStudioHistoryItem = () => readStudioHistory()[0] || null;

const resetStudioOutputMedia = () => {
  if (studioOutputVideo) {
    studioOutputVideo.pause();
    studioOutputVideo.hidden = true;
    studioOutputVideo.removeAttribute("src");
    studioOutputVideo.load();
  }
  if (studioOutputImage) {
    studioOutputImage.hidden = true;
    studioOutputImage.removeAttribute("src");
    studioOutputImage.alt = "";
  }
};

const hasStudioOutputMedia = () => {
  const videoVisible = !!studioOutputVideo && !studioOutputVideo.hidden && !!studioOutputVideo.getAttribute("src");
  const imageVisible = !!studioOutputImage && !studioOutputImage.hidden && !!studioOutputImage.getAttribute("src");
  return videoVisible || imageVisible;
};

const setStudioOutputState = ({
  state = "empty",
  type = "video",
  src = "",
  prompt = "",
  label = "Your output will appear here.",
  note = "Generate an image or video and Vision will keep the newest result live in this canvas while it builds.",
  meta = "No generation yet. Your latest prompt and result will live here.",
} = {}) => {
  if (
    !studioOutputStage ||
    !studioOutputPlaceholder ||
    !studioOutputStageLabel ||
    !studioOutputStageNote ||
    !studioOutputMeta
  ) {
    return;
  }

  const normalizedType = type === "image" ? "image" : "video";
  studioOutputStage.dataset.state = state;
  studioOutputStage.classList.toggle("is-empty", state === "empty");
  studioOutputStage.classList.toggle("is-loading", state === "loading");
  studioOutputStage.classList.toggle("is-ready", state === "ready");
  studioOutputPlaceholder.hidden = state === "ready";
  studioOutputStageLabel.textContent = label;
  studioOutputStageNote.textContent = note;
  studioOutputMeta.textContent = meta;

  if (state === "loading") {
    studioOutputPlaceholder.hidden = false;
    resetStudioOutputMedia();
    return;
  }

  if (state !== "ready" || !src) {
    resetStudioOutputMedia();
    return;
  }

  if (normalizedType === "image") {
    if (studioOutputImage) {
      studioOutputImage.hidden = false;
      studioOutputImage.src = src;
      studioOutputImage.alt = prompt || "Vision image";
    }
    if (studioOutputVideo) {
      studioOutputVideo.pause();
      studioOutputVideo.hidden = true;
      studioOutputVideo.removeAttribute("src");
      studioOutputVideo.load();
    }
    return;
  }

  if (studioOutputVideo) {
    studioOutputVideo.hidden = false;
    if (studioOutputVideo.getAttribute("src") !== src) {
      studioOutputVideo.src = src;
      studioOutputVideo.load();
    }
    studioOutputVideo.play().catch(() => {});
  }
  if (studioOutputImage) {
    studioOutputImage.hidden = true;
    studioOutputImage.removeAttribute("src");
    studioOutputImage.alt = "";
  }
};

const updateStudioOutputFromJob = (job) => {
  if (!studioDashboard || !job) {
    return;
  }
  const status = String(job.status || "queued").toLowerCase();
  const outputType = (job.output_type || job.mode || "video").toLowerCase() === "image" ? "image" : "video";
  const ui = generationUiCopy(status, outputType);
  const resolvedOutputUrl = visionAssetUrl(job.output_url);

  if (resolvedOutputUrl) {
    setStudioOutputState({
      state: "ready",
      type: outputType,
      src: resolvedOutputUrl,
      prompt: job.prompt || "",
      label: outputType === "image" ? "Latest image ready." : "Latest video ready.",
      note: job.prompt || "Generated inside Vision.",
      meta: `${outputType === "image" ? "Image" : "Video"} ready · ${formatStudioTimestamp(
        job.completed_at || job.updated_at || new Date().toISOString(),
      )}`,
    });
    return;
  }

  if (status === "ready") {
    setStudioOutputState({
      state: "empty",
      type: outputType,
      prompt: job.prompt || "",
      label: "Source unavailable",
      note: "Vision finished the job, but the media asset URL was missing or invalid.",
      meta: "Open and download remain disabled until a valid generated file is available.",
    });
    return;
  }

  if (["queued", "preparing", "generating", "operator_required", "downloading"].includes(status)) {
    setStudioOutputState({
      state: "loading",
      type: outputType,
      prompt: job.prompt || "",
      label: ui.stage,
      note: ui.eta || ui.note,
      meta: `${ui.deliveryTitle} · ${job.prompt || "Working on your latest idea."}`,
    });
    return;
  }

  if (["failed", "setup_required"].includes(status)) {
    setStudioOutputState({
      state: "empty",
      type: outputType,
      prompt: job.prompt || "",
      label: ui.stage,
      note: ui.note,
      meta: ui.deliveryNote,
    });
  }
};

const renderStudioHistory = () => {
  if (!studioHistoryGrid || !studioHistoryEmpty || !studioHistoryCount || !studioHistoryBadge) {
    return;
  }
  const items = readStudioHistory();
  if (!studioSelectedHistoryId && items[0]?.id) {
    studioSelectedHistoryId = items[0].id;
  }
  studioHistoryGrid.innerHTML = "";
  studioHistoryCount.textContent = `${items.length} creation${items.length === 1 ? "" : "s"} saved`;
  studioHistoryBadge.textContent = items.length ? `${items.length} saved` : "Empty";
  studioHistoryGrid.hidden = items.length === 0;
  studioHistoryGrid.style.display = items.length ? "" : "none";
  studioHistoryEmpty.hidden = items.length > 0;
  studioHistoryEmpty.style.display = items.length ? "none" : "";
  studioOutputShell?.classList.toggle("has-history", items.length > 0);

  items.forEach((item) => {
    const isActive = String(item.id) === String(studioSelectedHistoryId || "");
    const hasAsset = !!item.src;
    const card = document.createElement("article");
    card.className = `studio-history-item${isActive ? " is-active" : ""}`;
    const mediaMarkup =
      hasAsset
        ? item.type === "image"
          ? `<img src="${item.src}" alt="${item.prompt || "Vision image"}" loading="lazy" />`
          : `<video src="${item.src}" muted loop playsinline preload="metadata"></video>`
        : `<span class="studio-history-missing">Source unavailable</span>`;
    card.innerHTML = `
      <button class="studio-history-select" type="button" aria-label="Show ${item.type} in canvas" ${hasAsset ? "" : "disabled aria-disabled=\"true\""}>
        <span class="studio-history-media">${mediaMarkup}</span>
        <span class="studio-history-copy">
          <span class="studio-history-meta">${item.type === "image" ? "Image" : "Video"} · ${formatStudioTimestamp(item.created_at)}</span>
          <strong>${item.prompt || "Untitled Vision creation"}</strong>
        </span>
      </button>
      <div class="studio-history-actions">
        <button class="studio-history-action" type="button" data-history-open="${item.id}" ${hasAsset ? "" : "disabled aria-disabled=\"true\""}>Open</button>
        ${
          hasAsset
            ? `<a class="studio-history-action" href="${item.src}" download="${buildDownloadFilename({
                prompt: item.prompt || "",
                outputType: item.type,
                jobId: item.id,
                url: item.src,
              })}">Download</a>`
            : `<button class="studio-history-action" type="button" disabled aria-disabled="true">Download</button>`
        }
        <button class="studio-history-action studio-history-action--danger" type="button" data-history-delete="${item.id}">Delete</button>
      </div>
    `;
    const selectButton = card.querySelector(".studio-history-select");
    selectButton?.addEventListener("click", () => {
      if (!hasAsset) {
        return;
      }
      studioSelectedHistoryId = item.id;
      setStudioOutputState({
        state: "ready",
        type: item.type,
        src: item.src,
        prompt: item.prompt,
        label: item.type === "image" ? "Latest image ready." : "Latest video ready.",
        note: item.prompt || "Generated inside Vision.",
        meta: `${item.type === "image" ? "Image" : "Video"} · ${formatStudioTimestamp(item.created_at)}`,
      });
      renderStudioHistory();
    });
    card.querySelector("[data-history-open]")?.addEventListener("click", () => {
      if (!hasAsset) {
        return;
      }
      setGalleryLightboxState(true, {
        mediaType: item.type,
        src: item.src,
        title: item.type === "image" ? "Latest Vision image" : "Latest Vision render",
        caption: item.prompt || "Generated inside Vision.",
      });
    });
    card.querySelector("[data-history-delete]")?.addEventListener("click", () => {
      deleteStudioHistoryItem(item.id);
      renderStudioDashboard();
    });
    studioHistoryGrid.appendChild(card);
    if (item.type !== "image") {
      const video = card.querySelector("video");
      video?.play?.().catch(() => {});
    }
  });
};

const renderStudioDashboard = () => {
  if (!studioDashboard) {
    return;
  }
  const adminMode = !!accessState.admin;
  const hasPack = hasStudioPackContext();
  const counts = getStudioCreditCounts();
  if (studioVideoCredits) {
    studioVideoCredits.textContent = adminMode ? "∞" : String(counts.video);
  }
  if (studioImageCredits) {
    studioImageCredits.textContent = adminMode ? "∞" : String(counts.image);
  }
  if (studioPackStatus) {
    if (adminMode) {
      studioPackStatus.textContent = "Admin access is active. Vision engine is fully unlocked on this device.";
    } else if (hasPack) {
      studioPackStatus.textContent = `${getStudioCreditLabel()} are ready to use.`;
    } else if (currentUser.authenticated || currentUser.email) {
      studioPackStatus.textContent = "No active pack yet. Unlock one and your credits will appear here instantly.";
    } else {
      studioPackStatus.textContent = "Access Vision or unlock a pack to keep your credits and creations in one place.";
    }
  }
  if (studioTopupButton) {
    studioTopupButton.hidden = adminMode;
    studioTopupButton.textContent = hasPack ? "Buy more credits" : "Buy Vision credits";
  }
  renderStudioHistory();

  if (
    currentStudioJob &&
    ["queued", "preparing", "generating", "operator_required", "downloading"].includes(
      String(currentStudioJob.status || "").toLowerCase(),
    )
  ) {
    updateStudioOutputFromJob(currentStudioJob);
    return;
  }

  const currentItem = getCurrentStudioHistoryItem();
  if (currentItem) {
    setStudioOutputState({
      state: "ready",
      type: currentItem.type,
      src: currentItem.src,
      prompt: currentItem.prompt,
      label: currentItem.type === "image" ? "Latest image ready." : "Latest video ready.",
      note: currentItem.prompt || "Generated inside Vision.",
      meta: `${currentItem.type === "image" ? "Image" : "Video"} · ${formatStudioTimestamp(currentItem.created_at)}`,
    });
    return;
  }

  setStudioOutputState({
    state: "empty",
    label: "Your cinematic output will appear here.",
    note: "Generate an image or video and Vision will keep the newest result live in this canvas while it builds.",
    meta: currentUser.authenticated
      ? "No generation yet. Start with a prompt and Vision will build directly into this live canvas."
      : "Access Vision, then generate directly into this live output canvas.",
  });
};

const setStudioLoaderState = (open) => {
  body.classList.toggle("studio-loader-open", open);
  studioLoader?.classList.toggle("is-open", open);
  studioLoader?.setAttribute("aria-hidden", open ? "false" : "true");
};

const generationUiCopy = (status, outputType = "video") => {
  const isImage = outputType === "image";
  const typeLabel = isImage ? "image" : "render";
  const states = {
    queued: {
      badge: "Queued in Vision",
      stage: "Queued",
      note: isImage
        ? "Preparing your image lane inside Vision."
        : "Preparing your video lane inside Vision.",
      eta: "Usually under 1 minute remaining",
      deliveryTitle: "Queued in Vision",
      deliveryNote: isImage
        ? "Vision is preparing a sharper still with cleaner light, texture, and composition."
        : "Vision is preparing a cleaner cinematic pass with stronger realism and motion.",
      expandLabel: "Preview pending",
      downloadLabel: "Source pending",
    },
    preparing: {
      badge: "Shaping direction",
      stage: "Preparing",
      note: isImage
        ? "Balancing light, atmosphere, texture response, and still-frame hierarchy."
        : "Balancing realism, light direction, subject continuity, and camera language.",
      eta: "Usually 1–2 minutes remaining",
      deliveryTitle: "Preparing inside Vision",
      deliveryNote: isImage
        ? "Vision is tightening composition before the final image render begins."
        : "Vision is tightening mood, movement, and visual hierarchy before the render begins.",
      expandLabel: "Preview pending",
      downloadLabel: "Source pending",
    },
    generating: {
      badge: isImage ? "Rendering image" : "Rendering motion",
      stage: isImage ? "Generating image" : "Generating video",
      note: isImage
        ? "Rendering light falloff, texture detail, clean edges, and premium atmosphere."
        : "Rendering light, skin, fabric response, scene depth, and controlled cinematic motion.",
      eta: "Usually 1–3 minutes remaining",
      deliveryTitle: "Rendering inside Vision",
      deliveryNote: isImage
        ? "Vision is finishing your image with stronger texture, depth, and still-frame impact."
        : "Vision is finishing your motion pass with richer detail and a cleaner premium finish.",
      expandLabel: "Preview pending",
      downloadLabel: "Source pending",
    },
    operator_required: {
      badge: "Engine syncing",
      stage: "Syncing",
      note: "Vision is coordinating the next render lane so your prompt can keep moving.",
      eta: "Usually under 1 minute remaining",
      deliveryTitle: "Syncing inside Vision",
      deliveryNote: "A generation lane is being prepared behind the scenes.",
      expandLabel: "Preview pending",
      downloadLabel: "Source pending",
    },
    downloading: {
      badge: "Importing",
      stage: "Finishing",
      note: `The ${typeLabel} is finished. Vision is packaging the source output back into the studio.`,
      eta: "Final seconds",
      deliveryTitle: "Importing into Vision",
      deliveryNote: "The source output is being attached so preview and download are ready together.",
      expandLabel: "Preview pending",
      downloadLabel: "Source pending",
    },
    ready: {
      badge: "Tap to expand",
      stage: "Preview ready",
      note: `Open the full ${typeLabel}, inspect it, or download the original source file.`,
      deliveryTitle: "Created inside Vision",
      deliveryNote: `Preview it in miniature, open it full-size, or download the original ${typeLabel}.`,
      expandLabel: isImage ? "Open image" : "Open video",
      downloadLabel: isImage ? "Download image" : "Download video",
    },
    failed: {
      badge: "Try another pass",
      stage: "Render not completed",
      note: `This ${typeLabel} did not complete cleanly. Tighten the prompt or try another variation.`,
      deliveryTitle: "Render not completed",
      deliveryNote: isImage
        ? "Try a cleaner image prompt with one subject, one lighting idea, and one strong composition."
        : "Try a tighter motion prompt with one subject, one camera move, and one clear lighting idea.",
      expandLabel: "Preview unavailable",
      downloadLabel: "Source unavailable",
    },
    setup_required: {
      badge: "Engine setup",
      stage: "Engine handshake required",
      note: "Vision needs a quick engine sync before this prompt can run cleanly.",
      deliveryTitle: "Engine handshake required",
      deliveryNote: "Use Prepare engine once, then rerun the prompt.",
      expandLabel: "Preview unavailable",
      downloadLabel: "Source unavailable",
    },
  };
  return states[status] || states.generating;
};

const stopVisionFocusGuard = () => {
  if (visionFocusGuardHandle) {
    window.clearInterval(visionFocusGuardHandle);
    visionFocusGuardHandle = null;
  }
};

const startVisionFocusGuard = (durationMs = 20000) => {
  stopVisionFocusGuard();
  const startedAt = Date.now();
  const refocus = () => {
    if (Date.now() - startedAt >= durationMs) {
      stopVisionFocusGuard();
      return;
    }
    if (!document.hasFocus() || document.visibilityState === "hidden") {
      window.focus();
    }
  };
  window.focus();
  visionFocusGuardHandle = window.setInterval(refocus, 300);
};

const setSearchState = (open) => {
  body.classList.toggle("search-open", open);
  atomTrigger?.setAttribute("aria-expanded", open ? "true" : "false");
  searchCluster?.setAttribute("aria-hidden", open ? "false" : "true");

  if (open) {
    body.classList.add("has-opened-search");
  }

  if (promptInput) {
    promptInput.tabIndex = open ? 0 : -1;
  }

  if (searchSubmit) {
    searchSubmit.tabIndex = open ? 0 : -1;
  }

  if (open && promptInput) {
    window.setTimeout(() => promptInput.focus(), 320);
  }
};

const mountStudioCompose = () => {
  if (!isStudioRoute || !searchCluster || !studioOutputShell) {
    return;
  }
  const recentHead = studioOutputShell.querySelector(".studio-recent-head");
  if (searchCluster.parentElement === studioOutputShell) {
    searchCluster.classList.add("is-studio-embedded");
    return;
  }
  if (recentHead) {
    studioOutputShell.insertBefore(searchCluster, recentHead);
  } else {
    studioOutputShell.append(searchCluster);
  }
  searchCluster.classList.add("is-studio-embedded");
};

const setPromptHelper = (text, tone = "default") => {
  if (!promptHelper) {
    return;
  }
  promptHelper.textContent = text;
  promptHelper.dataset.tone = tone;
};

const resetPromptHelper = () => {
  setPromptHelper(promptHelperDefaults[selectedMode] || promptHelperDefaults.video);
};

const setImprovePromptLoading = (loading) => {
  improvePromptInFlight = loading;
  if (!improvePromptButton) {
    return;
  }
  const hasPrompt = !!promptInput?.value?.trim();
  improvePromptButton.hidden = !hasPrompt;
  improvePromptButton.disabled = loading || !hasPrompt;
  if (loading || !hasPrompt) {
    improvePromptButton.setAttribute("aria-disabled", "true");
  } else {
    improvePromptButton.removeAttribute("aria-disabled");
  }
  improvePromptButton.textContent = loading ? "Improving..." : "Improve Prompt";
};

const syncImprovePromptButton = () => {
  if (!improvePromptButton) {
    return;
  }
  const hasPrompt = !!promptInput?.value?.trim();
  improvePromptButton.hidden = !hasPrompt;
  improvePromptButton.disabled = improvePromptInFlight || !hasPrompt;
  improvePromptButton.setAttribute("aria-hidden", hasPrompt ? "false" : "true");
  if (improvePromptInFlight || !hasPrompt) {
    improvePromptButton.setAttribute("aria-disabled", "true");
  } else {
    improvePromptButton.removeAttribute("aria-disabled");
  }
};

const setGalleryLightboxState = (open, payload = {}) => {
  body.classList.toggle("gallery-lightbox-open", open);
  galleryLightbox?.classList.toggle("is-open", open);
  galleryLightbox?.setAttribute("aria-hidden", open ? "false" : "true");

  if (!galleryLightboxVideo || !galleryLightboxImage) {
    return;
  }

  if (open) {
    const mediaType = payload.mediaType || "video";
    trackVisionEvent("ViewerOpened", {
      job_id: payload.jobId,
      asset_id: normalizeGeneratedAssetPath(payload.src) || payload.src || "",
      media_type: mediaType,
      platform_context: "web",
    });
    if (galleryLightboxTitle) galleryLightboxTitle.textContent = payload.title || "Untitled";
    if (galleryLightboxCaption) galleryLightboxCaption.textContent = payload.caption || "";

    if (mediaType === "image") {
      galleryLightboxVideo.pause();
      galleryLightboxVideo.hidden = true;
      galleryLightboxVideo.removeAttribute("src");
      galleryLightboxVideo.load();
      galleryLightboxImage.hidden = false;
      galleryLightboxImage.src = payload.src || "";
      galleryLightboxImage.alt = payload.title || "Vision image";
      return;
    }

    galleryLightboxImage.hidden = true;
    galleryLightboxImage.removeAttribute("src");
    galleryLightboxImage.alt = "";
    galleryLightboxVideo.hidden = false;
    if (payload.src && galleryLightboxVideo.getAttribute("src") !== payload.src) {
      galleryLightboxVideo.src = payload.src;
    }
    galleryLightboxVideo.currentTime = 0;
    galleryLightboxVideo.play().catch(() => {});
    return;
  }

  galleryLightboxVideo.pause();
  galleryLightboxVideo.hidden = true;
  galleryLightboxVideo.removeAttribute("src");
  galleryLightboxVideo.load();
  galleryLightboxImage.hidden = true;
  galleryLightboxImage.removeAttribute("src");
  galleryLightboxImage.alt = "";
};

const openGenerationLightbox = () => {
  if (!currentGenerationOutput?.src) {
    return;
  }
  setGalleryLightboxState(true, {
    mediaType: currentGenerationOutput.type,
    src: currentGenerationOutput.src,
    title: currentGenerationOutput.title,
    caption: currentGenerationOutput.caption,
    jobId: currentGenerationOutput.jobId,
  });
};

const setGenerationState = (open) => {
  body.classList.toggle("generation-open", open);
  generationModal?.classList.toggle("is-open", open);
  generationModal?.setAttribute("aria-hidden", open ? "false" : "true");

  if (open && generationReady) {
    generationReady.hidden = false;
  }

  if (!open) {
    if (generationVideo) {
      generationVideo.pause();
      generationVideo.removeAttribute("src");
      generationVideo.load();
    }
    if (generationImage) {
      generationImage.hidden = true;
      generationImage.removeAttribute("src");
      generationImage.alt = "";
    }
    currentGenerationOutput = null;
    stopVisionFocusGuard();
    if (generationReady) {
      generationReady.hidden = true;
    }
    if (generationPollHandle) {
      window.clearTimeout(generationPollHandle);
      generationPollHandle = null;
    }
    activeGenerationJobId = null;
  }
};

const setGenerationStage = (status, message = "") => {
  const normalized = (status || "queued").toLowerCase();
  if (generationState) {
    generationState.textContent = normalized.replace(/_/g, " ");
  }
  if (generationCopy && message) {
    generationCopy.textContent = message;
  }
  const order = ["queued", "preparing", "generating", "downloading", "ready"];
  const effectiveStatus = normalized === "operator_required" ? "generating" : normalized;
  const statusIndex = order.indexOf(effectiveStatus);
  generationSteps.forEach((step) => {
    const stepIndex = order.indexOf(step.dataset.step);
    step.classList.toggle("is-active", stepIndex === statusIndex);
    step.classList.toggle("is-complete", statusIndex > -1 && stepIndex > -1 && stepIndex < statusIndex);
  });
};

const resetGenerationDelivery = (outputType, prompt = "", status = "queued") => {
  const normalizedType = outputType === "image" ? "image" : "video";
  const ui = generationUiCopy(status, normalizedType);
  currentGenerationOutput = null;

  if (generationDownload) {
    generationDownload.removeAttribute("href");
    generationDownload.removeAttribute("download");
    generationDownload.textContent = ui.downloadLabel;
    generationDownload.setAttribute("aria-disabled", "true");
  }

  if (generationExpand) {
    generationExpand.textContent = ui.expandLabel;
    generationExpand.setAttribute("aria-disabled", "true");
  }

  if (generationPreview) {
    generationPreview.classList.add("is-loading");
    generationPreview.classList.remove("is-ready");
    generationPreview.dataset.state = status;
    generationPreview.setAttribute(
      "aria-label",
      normalizedType === "image" ? "Generated image preview loading" : "Generated video preview loading",
    );
  }
  if (generationPreviewPlaceholder) {
    generationPreviewPlaceholder.hidden = false;
  }
  if (generationPreviewStage) {
    generationPreviewStage.textContent = ui.stage;
  }
  if (generationPreviewNote) {
    generationPreviewNote.textContent = ui.note;
  }
  if (generationDeliveryTitle) {
    generationDeliveryTitle.textContent = ui.deliveryTitle;
  }
  if (generationDeliveryNote) {
    generationDeliveryNote.textContent = ui.deliveryNote;
  }
  const previewBadge = generationPreview?.querySelector(".generation-preview-badge");
  if (previewBadge) {
    previewBadge.textContent = ui.badge;
  }

  if (generationImage) {
    generationImage.hidden = true;
    generationImage.removeAttribute("src");
    generationImage.alt = prompt || "";
  }

  if (generationVideo) {
    generationVideo.pause();
    generationVideo.hidden = true;
    generationVideo.removeAttribute("src");
    generationVideo.load();
  }
};

const presentGenerationJob = (job) => {
  currentStudioJob = job || null;
  const status = (job.status || "queued").toLowerCase();
  const outputType = (job.output_type || job.mode || "video").toLowerCase() === "image" ? "image" : "video";
  const resolvedOutputUrl = visionAssetUrl(job.output_url);
  const effectiveStatus = status === "ready" && !resolvedOutputUrl ? "failed" : status;
  const ui = generationUiCopy(effectiveStatus, outputType);
  const hasDeliverable = !!resolvedOutputUrl;

  if (hasDeliverable || ["ready", "failed", "setup_required"].includes(status)) {
    stopVisionFocusGuard();
  }

  const friendlyTitles = {
    queued: "Queued in the Vision engine",
    preparing: "Shaping cinematic direction",
    generating: "Creating inside Vision",
    operator_required: "Generation active",
    downloading: "Importing into Vision",
    ready: outputType === "image" ? "Your image is ready" : "Your render is ready",
    setup_required: "Vision engine setup required",
    failed: "Generation failed",
  };

  if (generationTitle) {
    generationTitle.textContent = friendlyTitles[effectiveStatus] || "Vision render";
  }
  if (generationPrompt) {
    generationPrompt.textContent = job.prompt || "";
  }
  setGenerationStage(effectiveStatus, job.message || job.error || "");

  if (generationPrepare) {
    generationPrepare.hidden = !["setup_required", "operator_required"].includes(status);
  }

  if (!hasDeliverable) {
    updateStudioOutputFromJob({ ...job, output_url: resolvedOutputUrl });
    resetGenerationDelivery(outputType, job.prompt || "", effectiveStatus);
    return;
  }

  currentGenerationOutput = {
    type: outputType,
    src: resolvedOutputUrl,
    jobId: job.id,
    title: outputType === "image" ? "Latest Vision image" : "Latest Vision render",
    caption: job.prompt || "Generated inside Vision.",
  };
  saveStudioHistoryItem(job, resolvedOutputUrl);
  trackVisionEvent("GenerateCompleted", {
    job_id: job.id,
    asset_id: normalizeGeneratedAssetPath(resolvedOutputUrl) || resolvedOutputUrl,
    media_type: outputType,
    platform_context: "web",
  });
  currentStudioJob = null;
  renderStudioDashboard();

  if (generationDownload) {
    generationDownload.href = resolvedOutputUrl;
    generationDownload.setAttribute(
      "download",
      buildDownloadFilename({
        prompt: job.prompt || "",
        outputType,
        jobId: job.id,
        url: resolvedOutputUrl,
      }),
    );
    generationDownload.removeAttribute("aria-disabled");
    generationDownload.textContent = ui.downloadLabel;
  }
  if (generationExpand) {
    generationExpand.removeAttribute("aria-disabled");
    generationExpand.textContent = ui.expandLabel;
  }
  if (generationPreview) {
    generationPreview.classList.remove("is-loading");
    generationPreview.classList.add("is-ready");
    generationPreview.dataset.state = effectiveStatus;
    generationPreview.setAttribute("aria-label", outputType === "image" ? "Open generated image" : "Open generated video");
  }
  if (generationPreviewPlaceholder) {
    generationPreviewPlaceholder.hidden = true;
  }
  if (generationDeliveryTitle) {
    generationDeliveryTitle.textContent = ui.deliveryTitle;
  }
  if (generationDeliveryNote) {
    generationDeliveryNote.textContent = ui.deliveryNote;
  }
  const previewBadge = generationPreview?.querySelector(".generation-preview-badge");
  if (previewBadge) {
    previewBadge.textContent = ui.badge;
  }

  if (outputType === "image") {
    if (generationVideo) {
      generationVideo.pause();
      generationVideo.hidden = true;
      generationVideo.removeAttribute("src");
      generationVideo.load();
    }
    if (generationImage) {
      generationImage.hidden = false;
      generationImage.src = resolvedOutputUrl;
      generationImage.alt = job.prompt || "Generated image";
    }
    return;
  }

  if (generationImage) {
    generationImage.hidden = true;
    generationImage.removeAttribute("src");
    generationImage.alt = "";
  }
  if (generationVideo) {
    generationVideo.hidden = false;
    if (generationVideo.src !== resolvedOutputUrl) {
      generationVideo.src = resolvedOutputUrl;
      generationVideo.load();
    }
    generationVideo.play().catch(() => {});
  }
};

const stopGenerationPolling = () => {
  if (generationPollHandle) {
    window.clearTimeout(generationPollHandle);
    generationPollHandle = null;
  }
};

const pollGenerationJob = async () => {
  if (!activeGenerationJobId) {
    return;
  }

  try {
    const response = await visionFetch(`/api/jobs/${activeGenerationJobId}`);
    if (!response.ok) {
      throw new Error("Unable to fetch job status.");
    }
    const job = await response.json();
    presentGenerationJob(job);

    if (job.output_url || ["ready", "failed", "setup_required"].includes(String(job.status || "").toLowerCase())) {
      activeGenerationJobId = null;
      stopGenerationPolling();
      return;
    }
  } catch (error) {
    currentStudioJob = null;
    setStudioOutputState({
      state: "empty",
      label: "Vision could not finish this request.",
      note: "Try again with a cleaner prompt or reopen the generation after the engine reconnects.",
      meta: "The Studio could not reach the generation engine right now.",
    });
    if (generationTitle) generationTitle.textContent = "Vision engine unavailable";
    if (generationCopy) generationCopy.textContent = "Vision could not reach the generation engine for this site right now.";
    stopGenerationPolling();
    stopVisionFocusGuard();
    return;
  }

  generationPollHandle = window.setTimeout(pollGenerationJob, 2600);
};

const setMode = (mode) => {
  selectedMode = mode === "image" ? "image" : "video";
  modeButtons.forEach((button) => {
    const active = button.dataset.generationMode === selectedMode;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-pressed", active ? "true" : "false");
  });
  if (promptInput) {
    promptInput.placeholder = selectedMode === "image" ? "Describe your image..." : "Describe your video or image...";
  }
  if (!improvePromptInFlight) {
    resetPromptHelper();
  }
};

const setAuthModalState = (open) => {
  body.classList.toggle("auth-open", open);
  authModal?.classList.toggle("is-open", open);
  authModal?.setAttribute("aria-hidden", open ? "false" : "true");
  if (!open) {
    return;
  }
  window.setTimeout(() => {
    if (hasStudioAccountContext()) {
      authLogout?.focus();
      return;
    }
    if (authStep === "code") {
      authCode?.focus();
      return;
    }
    authEmail?.focus();
  }, 180);
};

const setAuthLoading = (loading, label) => {
  if (authSubmit) {
    authSubmit.disabled = loading;
    if (label) {
      authSubmit.textContent = label;
    }
  }
  if (authEmail) {
    authEmail.disabled = loading || hasStudioAccountContext();
  }
  if (authCode) {
    authCode.disabled = loading || hasStudioAccountContext();
  }
};

const updateSubscribeSubmitLabel = () => {
  if (!subscribeSubmit) {
    return;
  }
  const packName = currentPack?.name ? currentPack.name.replace(/^Vision\s+/i, "") : "selected pack";
  subscribeSubmit.textContent = `Unlock ${packName}`;
};

const renderSubscribePackOptions = () => {
  if (!subscribePackGrid) {
    return;
  }
  subscribePackGrid.innerHTML = "";
  normalizePackList(currentPacks).forEach((pack) => {
    const selected = pack.id === selectedPackId;
    const card = document.createElement("button");
    card.type = "button";
    card.className = `subscribe-pack-card${selected ? " is-selected" : ""}${pack.badge ? " has-badge" : ""}`;
    card.dataset.packId = pack.id;
    card.setAttribute("aria-pressed", selected ? "true" : "false");
    const featureMarkup = (Array.isArray(pack.features) ? pack.features : [])
      .slice(0, 4)
      .map((feature) => `<li>${feature}</li>`)
      .join("");
    const creditLabel =
      pack.credit_label || (pack.vision_credits ? `${new Intl.NumberFormat("it-IT").format(pack.vision_credits)} Vision credits` : "");
    const creditMatch = String(creditLabel).match(/^([\d.,]+[KM]?)\s+(.+)$/i);
    const creditMarkup = creditMatch
      ? `<span class="subscribe-pack-credit-number">${creditMatch[1]}</span><span class="subscribe-pack-credit-label">${creditMatch[2]}</span>`
      : `<span class="subscribe-pack-credit-number">${creditLabel}</span>`;
    const totalCreditMarkup = pack.total_credit_label ? `<span class="subscribe-pack-total">${pack.total_credit_label}</span>` : "";
    const originalPriceCents = Number(pack.original_price_cents || 0);
    const currentPriceCents = Number(pack.price_cents || 0);
    const originalPriceMarkup =
      originalPriceCents > currentPriceCents
        ? `<del class="subscribe-pack-price-was">${formatPackPrice({ ...pack, price_cents: originalPriceCents })}</del>`
        : "";
    const discountMarkup = pack.discount_label ? `<span class="subscribe-pack-offer">${pack.discount_label}</span>` : "";
    const displayName = String(pack.name || "").replace(/^Vision\s+/i, "") || "Pack";
    card.innerHTML = `
      <div class="subscribe-pack-head">
        <div>
          <span class="subscribe-pack-name">${displayName}</span>
          <strong class="subscribe-pack-credit">${creditMarkup}</strong>
          ${totalCreditMarkup}
          <span class="subscribe-pack-price">${originalPriceMarkup}<span class="subscribe-pack-price-current">${formatPackPrice(pack)}</span><em>one time</em>${discountMarkup}</span>
        </div>
        ${pack.badge ? `<span class="subscribe-pack-badge">${pack.badge}</span>` : ""}
      </div>
      ${pack.subtitle ? `<p class="subscribe-pack-subtitle">${pack.subtitle}</p>` : ""}
      <p class="subscribe-pack-description">${pack.description}</p>
      ${pack.value_label ? `<p class="subscribe-pack-value">${pack.value_label}</p>` : ""}
      <div class="subscribe-pack-specs">
        <span>${pack.video_label}</span>
        <span>${pack.image_label}</span>
      </div>
      ${pack.example_label ? `<p class="subscribe-pack-example">${pack.example_label}</p>` : ""}
      <ul class="subscribe-pack-features">${featureMarkup}</ul>
      <span class="subscribe-pack-card-cta">${pack.cta_label || "Choose pack"}</span>
    `;
    card.addEventListener("click", () => {
      selectedPackId = pack.id;
      currentPack = normalizePack(pack);
      trackVisionEvent("PackSelected", packTrackingPayload(currentPack));
      renderSubscribePackOptions();
      updateSubscribeSubmitLabel();
    });
    subscribePackGrid.appendChild(card);
  });
  updateSubscribeSubmitLabel();
};

const renderAuthState = () => {
  const signedIn = hasStudioAccountContext();
  const showAccount = authStep === "account" || signedIn;
  const counts = getStudioCreditCounts();
  const hasPack = hasStudioPackContext();
  if (authTitle) {
    authTitle.textContent = showAccount ? "Your Vision account." : "Access your Vision pack.";
  }
  if (authCopy) {
    authCopy.textContent = showAccount
      ? "See your remaining credits, manage your current pack, or unlock a new one when you need more."
      : "Enter your email and Vision will send you a one-time access code to return to your pack from any device.";
  }
  if (authAccount) {
    authAccount.hidden = !showAccount;
    authAccount.style.display = showAccount ? "grid" : "none";
  }
  if (authForm) {
    authForm.hidden = showAccount;
    authForm.style.display = showAccount ? "none" : "grid";
  }
  if (authAccountEmail) {
    authAccountEmail.textContent = currentUser.email || (accessState.access_id ? "Pack active on this device" : "Vision account");
  }
  if (authAccountCredits) {
    if (accessState.admin) {
      authAccountCredits.textContent = "Vision engine unlocked";
    } else if (hasPack) {
      authAccountCredits.textContent = `${getStudioCreditLabel()} remaining`;
    } else {
      authAccountCredits.textContent = "No active pack yet.";
    }
  }
  if (authEnterStudio) {
    authEnterStudio.hidden = !showAccount;
  }
  if (authBuyPack) {
    if (accessState.admin) {
      authBuyPack.hidden = true;
    } else {
      authBuyPack.hidden = false;
      authBuyPack.textContent = hasPack ? "Buy more credits" : "Buy Vision credits";
    }
  }
  if (authCodeRow) {
    authCodeRow.hidden = authStep !== "code";
  }
  if (authReset) {
    authReset.hidden = authStep !== "code";
  }
  if (authSubmit) {
    authSubmit.textContent = authStep === "code" ? "Continue to Vision" : "Send access code";
  }
  if (authNote && !showAccount && authStep === "email") {
    authNote.textContent = "We’ll send a one-time access code so your pack follows you when you come back.";
  }
  if (authEmail && currentUser.email && !authEmail.value) {
    authEmail.value = currentUser.email;
  }
  if (subscribeEmail && currentUser.email && !subscribeEmail.value) {
    subscribeEmail.value = currentUser.email;
  }
};

const renderAccessState = (access, pack, user, packs) => {
  accessState = { ...defaultAccess, ...(access || {}) };
  currentPacks = normalizePackList(packs || currentPacks);
  currentUser = { ...defaultUser, ...(user || {}) };
  const fallbackPack = normalizePack(pack || {});
  currentPack = findPackById(selectedPackId || fallbackPack.id, currentPacks);
  selectedPackId = currentPack.id;
  renderSubscribePackOptions();

  if (accessPill) {
    if (accessState.admin) {
      accessPill.textContent = "Vision engine unlocked";
    } else if (hasStudioPackContext()) {
      accessPill.textContent = `Vision access live · ${getStudioCreditLabel()}`;
    } else {
      accessPill.textContent = "Vision access required";
    }
  }

  subscribeTriggers.forEach((trigger) => {
    if (!(trigger instanceof HTMLElement)) {
      return;
    }
    if (accessState.admin) {
      trigger.textContent = "Vision unlocked";
      return;
    }
    trigger.textContent = hasStudioPackContext() ? "Buy more credits" : "Unlock Vision";
  });

  syncTopbarCta();
  renderAuthState();
  renderStudioDashboard();
};

const setSubscribeLoading = (loading, label = null) => {
  if (subscribeSubmit) {
    subscribeSubmit.disabled = loading;
    subscribeSubmit.textContent = loading ? label || subscribeSubmit.textContent : `Unlock ${String(currentPack?.name || "selected pack").replace(/^Vision\s+/i, "")}`;
  }
  if (subscribeEmail) {
    subscribeEmail.disabled = loading;
  }
};

const setSubscribeContent = (context = {}) => {
  const reason = context.reason || "unlock";
  const pendingPrompt = context.prompt || "";
  const pendingMode = context.mode === "image" ? "image" : "video";
  const title =
    reason === "insufficient_credits" || accessState.access_id
      ? "Buy more Vision credits."
      : "Choose your Vision pack.";
  const copy = pendingPrompt
    ? `Choose the pack that fits this ${pendingMode} idea. Vision will resume your current prompt right after payment so you can keep creating without starting over.`
    : "Every pack includes private access, prompt enhancement, watermark-free exports, and private downloads.";
  const note = "Secure checkout with Stripe. Vision resumes your prompt automatically after payment.";

  if (subscribeKicker) {
    subscribeKicker.textContent = reason === "insufficient_credits" ? "Vision / Top up" : "Vision / Access";
  }
  if (subscribeTitle) {
    subscribeTitle.textContent = title;
  }
  if (subscribeCopy) {
    subscribeCopy.textContent = copy;
  }
  renderSubscribePackOptions();
  if (subscribeNote) {
    subscribeNote.textContent = note;
  }
  if (subscribeSuccess) {
    subscribeSuccess.hidden = true;
  }
  if (subscribeForm) {
    subscribeForm.hidden = false;
  }
  setSubscribeLoading(false);
};

const setSubscribeState = (open, context = {}) => {
  body.classList.toggle("subscribe-open", open);
  subscribeModal?.classList.toggle("is-open", open);
  subscribeModal?.setAttribute("aria-hidden", open ? "false" : "true");

  if (!open) {
    return;
  }

  lastSubscribeTrigger = context.trigger || null;
  currentPacks = normalizePackList(context.packs || currentPacks);
  selectedPackId = context.packId || selectedPackId || "creator";
  currentPack = findPackById(selectedPackId, currentPacks);
  setSubscribeContent(context);
  window.setTimeout(() => subscribeEmail?.focus(), 220);
};

const openAccessVision = async ({ forceEmail = false, email = "", autoSend = false } = {}) => {
  const normalizedEmail = String(email || currentUser.email || "").trim();
  authStep = forceEmail ? "email" : hasStudioAccountContext() ? "account" : "email";
  authPendingEmail = "";
  if (authCode) {
    authCode.value = "";
  }
  if (authEmail && normalizedEmail) {
    authEmail.value = normalizedEmail;
  }
  if (authNote && authStep === "email") {
    authNote.textContent = "Enter your email and Vision will send you a one-time access code to return to your pack from any device.";
  }
  renderAuthState();
  setSubscribeState(false);
  setAuthModalState(true);
  if (autoSend && normalizedEmail) {
    await requestAuthCode(normalizedEmail);
  }
};

const loadAccessState = async () => {
  try {
    const response = await visionFetch("/api/access/me");
    if (!response.ok) {
      return;
    }
    const payload = await response.json();
    renderAccessState(payload.access, payload.pack, payload.user, payload.packs);
  } catch (error) {
    renderAccessState(defaultAccess, currentPack, defaultUser, currentPacks);
  }
};

const maybeOpenPublicIntent = async () => {
  if (isStudioRoute) {
    return;
  }

  const url = new URL(window.location.href);
  const intent = url.searchParams.get("open");
  if (!intent) {
    return;
  }

  url.searchParams.delete("open");
  const cleanSearch = url.searchParams.toString();
  const cleanUrl = `${url.pathname}${cleanSearch ? `?${cleanSearch}` : ""}${url.hash}`;
  window.history.replaceState({}, "", cleanUrl);

  if (intent === "subscribe") {
    setSubscribeState(true, {
      reason: accessState.access_id ? "insufficient_credits" : "unlock",
    });
    return;
  }

  if (intent === "access") {
    await openAccessVision();
  }
};

const requestAuthCode = async (email) => {
  authPendingEmail = email;
  setAuthLoading(true, "Sending code...");
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
      throw new Error(payload?.detail || payload?.message || "Vision could not send an access code right now.");
    }
    authStep = "code";
    if (authNote) {
      authNote.textContent = `We sent a 6-digit Vision access code to ${email}.`;
    }
    renderAuthState();
  } catch (error) {
    if (authNote) {
      authNote.textContent = error instanceof Error ? error.message : "Vision could not send an access code right now.";
    }
  } finally {
    setAuthLoading(false);
  }
};

const verifyAuthCode = async (email, code) => {
  setAuthLoading(true, "Verifying...");
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
      throw new Error(payload?.detail || payload?.message || "That access code did not work.");
    }
    if (payload?.access_token) {
      storeAccessToken(payload.access_token);
    }
    renderAccessState(payload.access, payload.pack, payload.user, payload.packs);
    authStep = "email";
    authPendingEmail = "";
    if (authCode) {
      authCode.value = "";
    }
    setAuthModalState(false);
    if (!isStudioRoute) {
      await mountStudioInternally();
    }
  } catch (error) {
    if (authNote) {
      authNote.textContent = error instanceof Error ? error.message : "That access code did not work.";
    }
  } finally {
    setAuthLoading(false);
  }
};

const logoutUser = async () => {
  setAuthLoading(true, "Logging out...");
  try {
    const response = await visionFetch("/api/auth/logout", {
      method: "POST",
    });
    const payload = await parseJsonSafely(response);
    if (!response.ok) {
      throw new Error(payload?.detail || payload?.message || "Vision could not log you out.");
    }
    storeAccessToken("");
    authStep = "email";
    authPendingEmail = "";
    if (authCode) {
      authCode.value = "";
    }
    renderAccessState(payload.access, payload.pack, payload.user, payload.packs);
    setAuthModalState(false);
  } catch (error) {
    if (authNote) {
      authNote.textContent = error instanceof Error ? error.message : "Vision could not log you out.";
    }
  } finally {
    setAuthLoading(false);
  }
};

const beginCheckout = async (email) => {
  setSubscribeLoading(true, "Opening secure checkout...");
  if (subscribeNote) {
    subscribeNote.textContent = "Preparing secure checkout...";
  }

  try {
    const selectedPack = findPackById(selectedPackId, currentPacks);
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
      body: JSON.stringify({ email, pack_id: selectedPackId, tracking }),
    });
    const payload = await parseJsonSafely(response);
    if (!response.ok || !payload?.url) {
      throw new Error(payload?.detail || payload?.message || "Checkout is not configured yet.");
    }
    window.location.assign(payload.url);
  } catch (error) {
    setSubscribeLoading(false);
    if (subscribeNote) {
      subscribeNote.textContent = error instanceof Error ? error.message : "Checkout could not start.";
    }
  }
};

const handleCheckoutRequired = (detail, prompt, mode) => {
  if (detail?.access || detail?.pack || detail?.packs) {
    renderAccessState(detail?.access, detail?.pack, currentUser, detail?.packs);
  }
  savePendingPrompt(prompt, mode);
  setSubscribeState(true, {
    reason: detail?.code || "payment_required",
    prompt,
    mode,
    packs: detail?.packs,
  });
};

const queueGeneration = async (prompt, mode) => {
  if (!prompt) {
    return;
  }

  if (!accessState.has_access && !accessState.admin) {
    handleCheckoutRequired(
      { code: accessState.access_id ? "insufficient_credits" : "payment_required" },
      prompt,
      mode,
    );
    return;
  }

  if (searchSubmit) {
    searchSubmit.disabled = true;
  }

  if (!isStudioRoute) {
    setGenerationState(true);
  } else {
    setGenerationState(false);
  }
  startVisionFocusGuard(30000);
  presentGenerationJob({
    status: "queued",
    message: "The Vision engine is shaping your prompt into a stronger cinematic direction.",
    prompt,
    output_type: mode,
  });
  trackVisionEvent("GenerateStarted", {
    media_type: mode,
    platform_context: "web",
  });

  try {
    const response = await visionFetch("/api/jobs", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ prompt, mode }),
    });
    const payload = await parseJsonSafely(response);

    if (response.status === 402) {
      setGenerationState(false);
      currentStudioJob = null;
      renderStudioDashboard();
      handleCheckoutRequired(payload?.detail, prompt, mode);
      return;
    }

    if (!response.ok || !payload?.id) {
      throw new Error(payload?.detail || payload?.message || "Generation service unavailable.");
    }

    clearPendingPrompt();
    activeGenerationJobId = payload.id;
    presentGenerationJob(payload);
    stopGenerationPolling();
    generationPollHandle = window.setTimeout(pollGenerationJob, 1200);
    await loadAccessState();
  } catch (error) {
    currentStudioJob = null;
    renderStudioDashboard();
    stopVisionFocusGuard();
    if (generationTitle) generationTitle.textContent = "Vision engine unavailable";
    if (generationState) generationState.textContent = "offline";
    if (generationCopy) {
      generationCopy.textContent = error instanceof Error ? error.message : "Vision could not reach the generation engine for this site.";
    }
  } finally {
    if (searchSubmit) {
      searchSubmit.disabled = false;
    }
  }
};

const maybeResumePendingPrompt = async () => {
  const pending = readPendingPrompt();
  if (!pending || (!accessState.has_access && !accessState.admin)) {
    return;
  }
  setSearchState(true);
  setMode(pending.mode);
  if (promptInput) {
    promptInput.value = pending.prompt;
  }
  await queueGeneration(pending.prompt, pending.mode);
};

const confirmCheckoutIfNeeded = async () => {
  const url = new URL(window.location.href);
  const sessionId = url.searchParams.get("session_id");
  const checkoutStatus = url.searchParams.get("checkout");

  if (!sessionId || checkoutStatus !== "success") {
    if (checkoutStatus === "cancel") {
      stripUrlParams("checkout", "session_id");
      setSubscribeState(false);
      setAuthModalState(false);
    }
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
      throw new Error(payload?.detail || "Payment confirmation failed.");
    }
    trackVisionEvent("PurchaseCompleted", {
      ads_only: true,
      event_id: `stripe:${sessionId}:PurchaseCompleted`,
      checkout_session_id: sessionId,
      ...packTrackingPayload(payload?.pack || currentPack),
      platform_context: "web",
    });
    if (payload?.access_token) {
      storeAccessToken(payload.access_token);
    }
    renderAccessState(payload.access, payload.pack, payload.user, payload.packs);
    stripUrlParams("checkout", "session_id");
    return payload;
  } catch (error) {
    stripUrlParams("checkout", "session_id");
    setSubscribeState(true, { reason: "unlock" });
    if (subscribeNote) {
      subscribeNote.textContent = error instanceof Error ? error.message : "Payment confirmation failed.";
    }
    return null;
  }
};

const improvePrompt = async () => {
  const rawPrompt = promptInput?.value.trim() || "";
  if (!rawPrompt) {
    setPromptHelper("Write a raw idea first, then let Vision shape it into something stronger.", "warning");
    promptInput?.focus();
    return;
  }

  setImprovePromptLoading(true);
  setPromptHelper(
    selectedMode === "image"
      ? "Vision is sharpening atmosphere, framing, texture, and still-frame impact."
      : "Vision is sharpening mood, motion, realism, and cinematic direction.",
    "pending",
  );
  trackVisionEvent("PromptImproved", {
    media_type: selectedMode,
    platform_context: "web",
  });

  try {
    const response = await visionFetch("/api/prompt/improve", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        prompt: rawPrompt,
        mode: selectedMode,
      }),
    });
    const payload = await parseJsonSafely(response);
    if (!response.ok || !payload?.improved_prompt) {
      throw new Error(payload?.detail || payload?.message || "Vision could not improve this prompt right now.");
    }
    if (promptInput) {
      promptInput.value = payload.improved_prompt;
    }
    setPromptHelper(payload.summary || "Enhanced by Vision for stronger cinematic direction and a cleaner final result.", "success");
  } catch (error) {
    setPromptHelper(error instanceof Error ? error.message : "Vision could not improve this prompt right now.", "error");
  } finally {
    setImprovePromptLoading(false);
  }
};

const unlockAdminIfNeeded = async () => {
  const url = new URL(window.location.href);
  const token = url.searchParams.get("vision_admin");
  if (!token) {
    return false;
  }

  try {
    const response = await visionFetch("/api/admin/unlock", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ token }),
    });
    const payload = await parseJsonSafely(response);
    if (!response.ok) {
      throw new Error(payload?.detail || "Admin unlock failed.");
    }
    if (payload?.access_token) {
      storeAccessToken(payload.access_token);
    }
    renderAccessState(payload.access, payload.pack, payload.user, payload.packs);
    stripUrlParams("vision_admin");
    return true;
  } catch (error) {
    stripUrlParams("vision_admin");
    return false;
  }
};

setSearchState(false);
setSubscribeState(false);
setGenerationState(false);
setMode("video");
renderAccessState(defaultAccess, defaultPack, defaultUser, defaultPacks);

if (atomGuideText) {
  atomGuideText.textContent = isStudioRoute ? "Vision Studio" : "Tap to begin";
}

atomTrigger?.addEventListener("click", () => {
  enterStudio({ trigger: atomTrigger, reason: "unlock" });
});

modeButtons.forEach((button) => {
  button.addEventListener("click", () => {
    setMode(button.dataset.generationMode);
  });
});

studioTriggers.forEach((trigger) => {
  trigger.addEventListener("click", (event) => {
    if (isStudioRoute && trigger === topbarCta) {
      return;
    }
    if (trigger === topbarCta && !isStudioRoute && hasStudioAccountContext()) {
      return;
    }
    event.preventDefault();
    enterStudio({
      trigger,
      reason: hasStudioPackContext() ? "insufficient_credits" : "unlock",
    });
  });
});

topbarCta?.addEventListener("click", (event) => {
  if (!isStudioRoute) {
    return;
  }
  event.preventDefault();
  authStep = hasStudioAccountContext() ? "account" : "email";
  renderAuthState();
  setAuthModalState(true);
});

subscribeTriggers.forEach((trigger) => {
  trigger.addEventListener("click", (event) => {
    event.preventDefault();
    setSubscribeState(true, {
      trigger,
      reason: accessState.access_id ? "insufficient_credits" : "unlock",
    });
  });
});

searchForm?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const prompt = promptInput?.value?.trim();
  if (!prompt) {
    promptInput?.focus();
    return;
  }
  await queueGeneration(prompt, selectedMode);
});

promptInput?.addEventListener("focus", () => {
  body.classList.add("is-prompt-focused");
});

promptInput?.addEventListener("input", () => {
  syncImprovePromptButton();
});

promptInput?.addEventListener("blur", () => {
  body.classList.remove("is-prompt-focused");
});

galleryButtons.forEach((button) => {
  button.addEventListener("click", () => {
    const mediaType = button.dataset.mediaType || (button.dataset.imageSrc ? "image" : "video");
    setGalleryLightboxState(true, {
      mediaType,
      src: mediaType === "image" ? button.dataset.imageSrc : button.dataset.videoSrc,
      title: button.dataset.imageTitle || button.dataset.videoTitle,
      caption: button.dataset.imageCaption || button.dataset.videoCaption,
    });
  });
});

improvePromptButton?.addEventListener("click", improvePrompt);

generationDownload?.addEventListener("click", (event) => {
  const href = generationDownload.getAttribute("href");
  if (generationDownload.getAttribute("aria-disabled") === "true" || !href || href === "#") {
    event.preventDefault();
    return;
  }
  trackVisionEvent("AssetDownloaded", {
    job_id: currentGenerationOutput && currentGenerationOutput.jobId,
    asset_id: normalizeGeneratedAssetPath(href) || href,
    media_type: currentGenerationOutput && currentGenerationOutput.type,
    platform_context: "web",
  });
});

galleryLightboxClose?.addEventListener("click", () => {
  setGalleryLightboxState(false);
});

galleryLightbox?.addEventListener("click", (event) => {
  if (event.target === galleryLightbox) {
    setGalleryLightboxState(false);
  }
});

subscribeClose?.addEventListener("click", () => {
  setSubscribeState(false);
});

authClose?.addEventListener("click", () => {
  setAuthModalState(false);
});

subscribeModal?.addEventListener("click", (event) => {
  if (event.target === subscribeModal) {
    setSubscribeState(false);
  }
});

authModal?.addEventListener("click", (event) => {
  if (event.target === authModal) {
    setAuthModalState(false);
  }
});

subscribeForm?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const email = subscribeEmail?.value?.trim();
  if (!email) {
    subscribeEmail?.focus();
    return;
  }
  await beginCheckout(email);
});

subscribeReturningLink?.addEventListener("click", () => {
  void openAccessVision();
});

footerAccessVision?.addEventListener("click", () => {
  void openAccessVision();
});

authForm?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const email = authStep === "code" ? authPendingEmail : authEmail?.value?.trim();
  if (!email) {
    authEmail?.focus();
    return;
  }
  if (authStep === "code") {
    const code = authCode?.value?.trim();
    if (!code) {
      authCode?.focus();
      return;
    }
    await verifyAuthCode(email, code);
    return;
  }
  await requestAuthCode(email);
});

authReset?.addEventListener("click", () => {
  authStep = "email";
  authPendingEmail = "";
  if (authCode) {
    authCode.value = "";
  }
  if (authNote) {
    authNote.textContent = "We’ll send a one-time access code so your pack follows you when you come back.";
  }
  renderAuthState();
});

authLogout?.addEventListener("click", async () => {
  await logoutUser();
});

authBuyPack?.addEventListener("click", () => {
  setAuthModalState(false);
  setSubscribeState(true, {
    reason: accessState.access_id ? "insufficient_credits" : "unlock",
  });
});

authEnterStudio?.addEventListener("click", () => {
  setAuthModalState(false);
  if (isStudioRoute) {
    return;
  }
  window.location.assign(VISION_STUDIO_PATH);
});

studioAccountButton?.addEventListener("click", () => {
  authStep = hasStudioAccountContext() ? "account" : "email";
  renderAuthState();
  setAuthModalState(true);
});

studioTopupButton?.addEventListener("click", () => {
  setSubscribeState(true, {
    reason: hasStudioPackContext() ? "insufficient_credits" : "unlock",
  });
});

generationClose?.addEventListener("click", () => {
  setGenerationState(false);
});

generationModal?.addEventListener("click", (event) => {
  if (event.target === generationModal) {
    setGenerationState(false);
  }
});

generationPrepare?.addEventListener("click", async () => {
  generationPrepare.disabled = true;
  generationPrepare.textContent = "Syncing engine...";
  try {
    await visionFetch("/api/engine/prepare", { method: "POST" });
    if (generationCopy) {
      generationCopy.textContent = "Vision is checking the generation engine.";
    }
  } catch (error) {
    if (generationCopy) {
      generationCopy.textContent = "Vision could not reach the generation engine for this site.";
    }
  } finally {
    generationPrepare.disabled = false;
    generationPrepare.textContent = "Prepare engine";
  }
});

generationPreview?.addEventListener("click", () => {
  openGenerationLightbox();
});

generationExpand?.addEventListener("click", () => {
  openGenerationLightbox();
});

studioOutputStage?.addEventListener("click", () => {
  if (currentStudioJob) {
    return;
  }
  const selectedItem = getCurrentStudioHistoryItem();
  if (!selectedItem) {
    return;
  }
  setGalleryLightboxState(true, {
    mediaType: selectedItem.type,
    src: selectedItem.src,
    title: selectedItem.type === "image" ? "Latest Vision image" : "Latest Vision render",
    caption: selectedItem.prompt || "Generated inside Vision.",
  });
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && body.classList.contains("gallery-lightbox-open")) {
    setGalleryLightboxState(false);
    return;
  }

  if (event.key === "Escape" && body.classList.contains("subscribe-open")) {
    setSubscribeState(false);
    lastSubscribeTrigger?.focus();
    return;
  }

  if (event.key === "Escape" && body.classList.contains("auth-open")) {
    setAuthModalState(false);
    topbarCta?.focus();
    return;
  }

  if (event.key === "Escape" && body.classList.contains("generation-open")) {
    setGenerationState(false);
    promptInput?.focus();
    return;
  }

  if (event.key === "Escape" && body.classList.contains("search-open")) {
    setSearchState(false);
    atomTrigger?.focus();
  }
});

const initializeVision = async () => {
  body.classList.toggle("is-studio-route", isStudioRoute);
  if (!isStudioRoute) {
    trackVisionEvent("LandingViewed", { platform_context: "web" });
  }
  if (isStudioRoute) {
    body.classList.remove("studio-ready");
    setStudioLoaderState(true);
  }
  setMode(selectedMode);
  resetPromptHelper();
  syncImprovePromptButton();
  if (isStudioRoute) {
    mountStudioCompose();
    topNav?.setAttribute("aria-hidden", "true");
  }
  syncTopbarCta();
  const unlockedAdmin = await unlockAdminIfNeeded();
  const confirmedCheckout = await confirmCheckoutIfNeeded();
  if (confirmedCheckout && !isStudioRoute) {
    const checkoutEmail = String(confirmedCheckout?.user?.email || currentUser.email || "").trim();
    storeAccessToken("");
    renderAccessState(defaultAccess, currentPack, { ...defaultUser, email: checkoutEmail }, currentPacks);
    await openAccessVision({
      forceEmail: true,
      email: checkoutEmail,
      autoSend: Boolean(checkoutEmail),
    });
    return;
  }
  await loadAccessState();
  await maybeOpenPublicIntent();
  if (isStudioRoute) {
    setSearchState(true);
  }
  renderStudioDashboard();
  if (unlockedAdmin || confirmedCheckout) {
    await maybeResumePendingPrompt();
  }
  if (isStudioRoute) {
    await wait(5000);
    body.classList.add("studio-ready");
    setStudioLoaderState(false);
  }
};

void initializeVision();
