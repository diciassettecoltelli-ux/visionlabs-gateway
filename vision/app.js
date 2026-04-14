const body = document.body;
const atomTrigger = document.querySelector(".atom-trigger");
const searchCluster = document.querySelector(".search-cluster");
const searchForm = document.querySelector("#vision-search-form");
const promptInput = document.querySelector("#vision-prompt");
const searchSubmit = document.querySelector(".search-launch-button");
const galleryButtons = document.querySelectorAll(".gallery-open");
const galleryLightbox = document.querySelector("#gallery-lightbox");
const galleryLightboxVideo = document.querySelector("#gallery-lightbox-video");
const galleryLightboxImage = document.querySelector("#gallery-lightbox-image");
const galleryLightboxTitle = document.querySelector("#gallery-lightbox-title");
const galleryLightboxCaption = document.querySelector("#gallery-lightbox-caption");
const galleryLightboxClose = document.querySelector(".gallery-lightbox-close");
const subscribeTriggers = document.querySelectorAll("[data-subscribe-trigger]");
const subscribeModal = document.querySelector("#subscribe-modal");
const subscribeClose = document.querySelector(".subscribe-close");
const subscribeForm = document.querySelector("#subscribe-form");
const subscribeEmail = document.querySelector("#subscribe-email");
const subscribeSuccess = document.querySelector("#subscribe-success");
const subscribeKicker = document.querySelector(".subscribe-kicker");
const subscribeTitle = document.querySelector("#subscribe-title");
const subscribeCopy = document.querySelector(".subscribe-copy");
const subscribePlanLine = document.querySelector("#subscribe-plan-line");
const generationModal = document.querySelector("#generation-modal");
const generationClose = document.querySelector(".generation-close");
const generationTitle = document.querySelector("#generation-title");
const generationState = document.querySelector("#generation-state");
const generationCopy = document.querySelector("#generation-copy");
const generationPrompt = document.querySelector("#generation-prompt");
const generationSteps = document.querySelectorAll(".generation-step");
const generationReady = document.querySelector("#generation-ready");
const generationVideo = document.querySelector("#generation-video");
const generationPreview = document.querySelector("#generation-preview");
const generationExpand = document.querySelector("#generation-expand");
const generationDownload = document.querySelector("#generation-download");
const generationPrepare = document.querySelector("#generation-prepare");

const configuredApiBase = typeof window.VISION_API_BASE === "string" ? window.VISION_API_BASE.trim() : "";
const runningOnLocalVision = ["localhost", "127.0.0.1"].includes(window.location.hostname);
const VISION_API_BASE = configuredApiBase || (runningOnLocalVision ? "http://127.0.0.1:8787" : "");

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

const subscribeDefaults = {
  kicker: subscribeKicker?.textContent || "Vision / Invitation",
  title: subscribeTitle?.textContent || "Request access",
  copy: subscribeCopy?.textContent || "Leave your email and we’ll reach out when Vision opens.",
};

let lastSubscribeTrigger = null;
let activeGenerationJobId = null;
let generationPollHandle = null;
let visionFocusGuardHandle = null;

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

const setGalleryLightboxState = (open, payload = {}) => {
  body.classList.toggle("gallery-lightbox-open", open);
  galleryLightbox?.classList.toggle("is-open", open);
  galleryLightbox?.setAttribute("aria-hidden", open ? "false" : "true");

  if (!galleryLightboxVideo || !galleryLightboxImage) {
    return;
  }

  if (open) {
    const mediaType = payload.mediaType || "video";
    galleryLightboxTitle.textContent = payload.title || "Untitled";
    galleryLightboxCaption.textContent = payload.caption || "";

    if (mediaType === "image") {
      galleryLightboxVideo.pause();
      galleryLightboxVideo.hidden = true;
      galleryLightboxVideo.removeAttribute("src");
      galleryLightboxVideo.load();

      galleryLightboxImage.hidden = false;
      galleryLightboxImage.src = payload.src || "";
      galleryLightboxImage.alt = payload.title || "Gallery image";
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
  if (!generationVideo?.src) {
    return;
  }

  setGalleryLightboxState(true, {
    mediaType: "video",
    src: generationVideo.src,
    title: "Latest Vision render",
    caption: generationPrompt?.textContent || "Generated inside Vision.",
  });
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

const setGenerationState = (open) => {
  body.classList.toggle("generation-open", open);
  generationModal?.classList.toggle("is-open", open);
  generationModal?.setAttribute("aria-hidden", open ? "false" : "true");

  if (!open) {
    if (generationVideo) {
      generationVideo.pause();
    }
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
  generationSteps.forEach((step) => {
    const current = step.dataset.step;
    const order = ["queued", "preparing", "generating", "downloading", "ready"];
    const effectiveStatus = normalized === "operator_required" ? "generating" : normalized;
    const currentIndex = order.indexOf(current);
    const statusIndex = order.indexOf(effectiveStatus);
    step.classList.toggle("is-active", currentIndex === statusIndex);
    step.classList.toggle("is-complete", statusIndex > -1 && currentIndex > -1 && currentIndex < statusIndex);
  });
};

const presentGenerationJob = (job) => {
  const status = job.status || "queued";
  if (["ready", "failed", "setup_required"].includes(status)) {
    stopVisionFocusGuard();
  }
  const friendlyTitles = {
    queued: "Queued inside Vision",
    preparing: "Preparing generation",
    generating: "Generating in the background",
    operator_required: "Generation active",
    downloading: "Importing result into Vision",
    ready: "Your render is ready",
    setup_required: "Engine setup required",
    failed: "Generation failed",
  };

  if (generationTitle) {
    generationTitle.textContent = friendlyTitles[status] || "Vision render";
  }
  if (generationPrompt) {
    generationPrompt.textContent = job.prompt || "";
  }

  setGenerationStage(status, job.message || "");

  const isReady = status === "ready" && job.output_url;
  if (generationReady) {
    generationReady.hidden = !isReady;
  }
  if (isReady) {
    stopVisionFocusGuard();
    const resolvedOutputUrl = visionAssetUrl(job.output_url);
    if (generationVideo && generationVideo.src !== resolvedOutputUrl) {
      generationVideo.src = resolvedOutputUrl;
      generationVideo.load();
    }
    generationVideo?.play().catch(() => {});
    if (generationDownload) {
      generationDownload.href = resolvedOutputUrl;
    }
  }

  if (generationPrepare) {
    generationPrepare.hidden = !["setup_required", "operator_required"].includes(status);
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
    const response = await fetch(visionApiUrl(`/api/jobs/${activeGenerationJobId}`));
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
    if (generationTitle) {
      generationTitle.textContent = "Generation service offline";
    }
    if (generationCopy) {
      generationCopy.textContent = "The Vision generation service is not reachable for this site yet.";
    }
    stopGenerationPolling();
    return;
  }

  generationPollHandle = window.setTimeout(pollGenerationJob, 2600);
};

const setSubscribeContent = (trigger) => {
  const planName = trigger?.dataset.planName;
  const planPrice = trigger?.dataset.planPrice;
  const planAllocation = trigger?.dataset.planAllocation;

  if (!planName) {
    if (subscribeKicker) subscribeKicker.textContent = subscribeDefaults.kicker;
    if (subscribeTitle) subscribeTitle.textContent = subscribeDefaults.title;
    if (subscribeCopy) subscribeCopy.textContent = subscribeDefaults.copy;
    if (subscribePlanLine) {
      subscribePlanLine.hidden = true;
      subscribePlanLine.textContent = "";
    }
    return;
  }

  if (subscribeKicker) subscribeKicker.textContent = `Vision / ${planName}`;
  if (subscribeTitle) subscribeTitle.textContent = `Join ${planName}`;
  if (subscribeCopy) subscribeCopy.textContent = "Leave your email and we’ll send access details for this plan.";
  if (subscribePlanLine) {
    subscribePlanLine.hidden = false;
    subscribePlanLine.textContent = `${planPrice} · ${planAllocation}`;
  }
};

const setSubscribeState = (open, trigger = null) => {
  body.classList.toggle("subscribe-open", open);
  subscribeModal?.classList.toggle("is-open", open);
  subscribeModal?.setAttribute("aria-hidden", open ? "false" : "true");

  if (!open) {
    return;
  }

  lastSubscribeTrigger = trigger;
  setSubscribeContent(trigger);
  if (subscribeForm) {
    subscribeForm.hidden = false;
  }
  if (subscribeSuccess) {
    subscribeSuccess.hidden = true;
  }

  window.setTimeout(() => subscribeEmail?.focus(), 220);
};

setSearchState(false);
setSubscribeState(false);
setGenerationState(false);

atomTrigger?.addEventListener("click", () => {
  const nextState = !body.classList.contains("search-open");
  setSearchState(nextState);
});

subscribeTriggers.forEach((trigger) => {
  trigger.addEventListener("click", (event) => {
    event.preventDefault();
    setSubscribeState(true, trigger);
  });
});

searchForm?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const prompt = promptInput?.value?.trim();
  if (!prompt) {
    promptInput?.focus();
    return;
  }

  setGenerationState(true);
  startVisionFocusGuard(30000);
  presentGenerationJob({
    status: "queued",
    message: "Preparing your prompt inside Vision.",
    prompt,
  });

  try {
    const response = await fetch(visionApiUrl("/api/jobs"), {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ prompt }),
    });
    if (!response.ok) {
      throw new Error("Generation service unavailable.");
    }
    const job = await response.json();
    activeGenerationJobId = job.id;
    presentGenerationJob(job);
    stopGenerationPolling();
    generationPollHandle = window.setTimeout(pollGenerationJob, 1200);
  } catch (error) {
    stopVisionFocusGuard();
    if (generationTitle) {
      generationTitle.textContent = "Generation service offline";
    }
    if (generationState) {
      generationState.textContent = "offline";
    }
    if (generationCopy) {
      generationCopy.textContent = "Vision could not reach the generation service for this site.";
    }
  }
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

subscribeForm?.addEventListener("submit", (event) => {
  event.preventDefault();
  subscribeForm.hidden = true;
  if (subscribeSuccess) {
    subscribeSuccess.hidden = false;
  }
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
    await fetch(visionApiUrl("/api/engine/prepare"), { method: "POST" });
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
