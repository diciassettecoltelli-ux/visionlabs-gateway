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
const subscribePlanLine = document.querySelector("#subscribe-plan-line");
const subscribeSubmit = document.querySelector(".subscribe-submit");
const subscribeNote = document.querySelector("#subscribe-note");
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
const generationExpand = document.querySelector("#generation-expand");
const generationDownload = document.querySelector("#generation-download");
const generationPrepare = document.querySelector("#generation-prepare");

const configuredApiBase = typeof window.VISION_API_BASE === "string" ? window.VISION_API_BASE.trim() : "";
const runningOnLocalVision = ["localhost", "127.0.0.1"].includes(window.location.hostname);
const VISION_API_BASE = configuredApiBase || (runningOnLocalVision ? "http://127.0.0.1:8787" : "");
const VISION_STUDIO_PATH = "/studio/";
const isStudioRoute = /^\/studio\/?$/.test(window.location.pathname);
const VISION_ACCESS_STORAGE_KEY = "vision_access_token";
const VISION_PENDING_PROMPT_KEY = "vision_pending_prompt";

const defaultPack = {
  name: "Vision Pack",
  price_cents: 199,
  currency: "eur",
  video_credits: 1,
  image_credits: 5,
};

const defaultAccess = {
  has_access: false,
  admin: false,
  video_remaining: 0,
  image_remaining: 0,
  access_id: null,
};

const promptHelperDefaults = {
  video: "From one raw idea to a sharper cinematic result. Vision strengthens mood, realism, framing, and visual tone before generation begins.",
  image: "From one raw idea to a stronger still image. Vision sharpens atmosphere, composition, texture, and still-frame impact before generation begins.",
};

const visionApiUrl = (path) => `${VISION_API_BASE}${path}`;

const visionAssetUrl = (path) => {
  if (!path) {
    return path;
  }
  if (/^https?:\/\//i.test(path)) {
    return path;
  }
  if (path.startsWith("/") && VISION_API_BASE) {
    return `${VISION_API_BASE}${path}`;
  }
  return path;
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

const enterStudio = () => {
  window.location.assign(VISION_STUDIO_PATH);
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
  `${formatPackPrice(pack)} · one-time unlock · ${pack.video_credits} videos + ${pack.image_credits} images`;

let selectedMode = "video";
let activeGenerationJobId = null;
let generationPollHandle = null;
let visionFocusGuardHandle = null;
let improvePromptInFlight = false;
let accessState = { ...defaultAccess };
let currentPack = { ...defaultPack };
let currentGenerationOutput = null;
let lastSubscribeTrigger = null;

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
  improvePromptButton.disabled = loading;
  improvePromptButton.textContent = loading ? "Improving..." : "Improve Prompt";
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
  });
};

const setGenerationState = (open) => {
  body.classList.toggle("generation-open", open);
  generationModal?.classList.toggle("is-open", open);
  generationModal?.setAttribute("aria-hidden", open ? "false" : "true");

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

const resetGenerationDelivery = (outputType, prompt = "") => {
  const normalizedType = outputType === "image" ? "image" : "video";
  currentGenerationOutput = null;

  if (generationReady) {
    generationReady.hidden = true;
  }

  if (generationDownload) {
    generationDownload.removeAttribute("href");
    generationDownload.removeAttribute("download");
    generationDownload.textContent = normalizedType === "image" ? "Download image" : "Download video";
    generationDownload.setAttribute("aria-disabled", "true");
  }

  if (generationExpand) {
    generationExpand.textContent = normalizedType === "image" ? "Open image" : "Open video";
    generationExpand.setAttribute("aria-disabled", "true");
  }

  if (generationPreview) {
    generationPreview.setAttribute(
      "aria-label",
      normalizedType === "image" ? "Generated image preview unavailable" : "Generated video preview unavailable",
    );
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
  const status = (job.status || "queued").toLowerCase();
  const outputType = (job.output_type || job.mode || "video").toLowerCase() === "image" ? "image" : "video";

  if (["ready", "failed", "setup_required"].includes(status)) {
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
    generationTitle.textContent = friendlyTitles[status] || "Vision render";
  }
  if (generationPrompt) {
    generationPrompt.textContent = job.prompt || "";
  }
  setGenerationStage(status, job.message || job.error || "");

  const isReady = status === "ready" && job.output_url;
  if (generationReady) {
    generationReady.hidden = !isReady;
  }

  if (generationPrepare) {
    generationPrepare.hidden = !["setup_required", "operator_required"].includes(status);
  }

  if (!isReady) {
    resetGenerationDelivery(outputType, job.prompt || "");
    return;
  }

  const resolvedOutputUrl = visionAssetUrl(job.output_url);
  currentGenerationOutput = {
    type: outputType,
    src: resolvedOutputUrl,
    title: outputType === "image" ? "Latest Vision image" : "Latest Vision render",
    caption: job.prompt || "Generated inside Vision.",
  };

  if (generationDownload) {
    generationDownload.href = resolvedOutputUrl;
    generationDownload.setAttribute("download", "");
    generationDownload.removeAttribute("aria-disabled");
    generationDownload.textContent = outputType === "image" ? "Download image" : "Download video";
  }
  if (generationExpand) {
    generationExpand.removeAttribute("aria-disabled");
    generationExpand.textContent = outputType === "image" ? "Open image" : "Open video";
  }
  if (generationPreview) {
    generationPreview.setAttribute("aria-label", outputType === "image" ? "Open generated image" : "Open generated video");
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

    if (["ready", "failed", "setup_required"].includes(job.status)) {
      stopGenerationPolling();
      return;
    }
  } catch (error) {
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

const renderAccessState = (access, pack) => {
  accessState = { ...defaultAccess, ...(access || {}) };
  currentPack = { ...defaultPack, ...(pack || {}) };

  if (subscribePlanLine) {
    subscribePlanLine.hidden = false;
    subscribePlanLine.textContent = formatPackLine(currentPack);
  }

  if (accessPill) {
    if (accessState.admin) {
      accessPill.textContent = "Vision engine unlocked";
    } else if (accessState.access_id) {
      accessPill.textContent = `Vision Pack live · ${accessState.video_remaining ?? 0} videos · ${accessState.image_remaining ?? 0} images`;
    } else {
      accessPill.textContent = "Vision Pack required";
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
    trigger.textContent = accessState.access_id ? "Buy another pack" : "Unlock Vision";
  });
};

const setSubscribeLoading = (loading, label = "Unlock my pack") => {
  if (subscribeSubmit) {
    subscribeSubmit.disabled = loading;
    subscribeSubmit.textContent = loading ? label : "Unlock my pack";
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
      ? "Unlock another cinematic pack."
      : "Turn one prompt into a cinematic pack.";
  const copy = pendingPrompt
    ? `Unlock ${currentPack.video_credits} cinematic videos, ${currentPack.image_credits} still images, and a stronger ${pendingMode} direction shaped by Vision. Your current idea resumes right after payment.`
    : `Unlock ${currentPack.video_credits} cinematic videos, ${currentPack.image_credits} still images, and a prompt shaped by Vision into something sharper, richer, and more campaign-ready before generation begins.`;
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
  setSubscribeContent(context);
  window.setTimeout(() => subscribeEmail?.focus(), 220);
};

const loadAccessState = async () => {
  try {
    const response = await visionFetch("/api/access/me");
    if (!response.ok) {
      return;
    }
    const payload = await response.json();
    renderAccessState(payload.access, payload.pack);
  } catch (error) {
    renderAccessState(defaultAccess, currentPack);
  }
};

const beginCheckout = async (email) => {
  setSubscribeLoading(true, "Opening secure checkout...");
  if (subscribeNote) {
    subscribeNote.textContent = "Preparing secure checkout...";
  }

  try {
    const response = await visionFetch("/api/checkout/session", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ email }),
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
  savePendingPrompt(prompt, mode);
  setSubscribeState(true, {
    reason: detail?.code || "payment_required",
    prompt,
    mode,
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

  setGenerationState(true);
  startVisionFocusGuard(30000);
  presentGenerationJob({
    status: "queued",
    message: "The Vision engine is shaping your prompt into a stronger cinematic direction.",
    prompt,
    output_type: mode,
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
      setSubscribeState(true, { reason: "unlock" });
      if (subscribeNote) {
        subscribeNote.textContent = "Checkout cancelled. You can reopen it any time.";
      }
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
    if (payload?.access_token) {
      storeAccessToken(payload.access_token);
    }
    renderAccessState(payload.access, payload.pack);
    stripUrlParams("checkout", "session_id");
    return true;
  } catch (error) {
    stripUrlParams("checkout", "session_id");
    setSubscribeState(true, { reason: "unlock" });
    if (subscribeNote) {
      subscribeNote.textContent = error instanceof Error ? error.message : "Payment confirmation failed.";
    }
    return false;
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
    renderAccessState(payload.access, payload.pack);
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
renderAccessState(defaultAccess, defaultPack);

if (atomGuideText) {
  atomGuideText.textContent = isStudioRoute ? "Vision Studio" : "Enter Vision Studio";
}

atomTrigger?.addEventListener("click", () => {
  if (!isStudioRoute) {
    enterStudio();
    return;
  }
  setSearchState(true);
});

modeButtons.forEach((button) => {
  button.addEventListener("click", () => {
    setMode(button.dataset.generationMode);
  });
});

studioTriggers.forEach((trigger) => {
  trigger.addEventListener("click", (event) => {
    event.preventDefault();
    enterStudio();
  });
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

subscribeModal?.addEventListener("click", (event) => {
  if (event.target === subscribeModal) {
    setSubscribeState(false);
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
  setMode(selectedMode);
  resetPromptHelper();
  const unlockedAdmin = await unlockAdminIfNeeded();
  const confirmedCheckout = await confirmCheckoutIfNeeded();
  await loadAccessState();
  if (isStudioRoute) {
    setSearchState(true);
  }
  if (unlockedAdmin || confirmedCheckout) {
    await maybeResumePendingPrompt();
  }
};

void initializeVision();
