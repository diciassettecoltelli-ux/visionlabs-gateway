(function () {
  const DEFAULT_API_BASE = String(window.VISION_API_BASE || "https://vision-gateway.onrender.com").replace(/\/$/, "");
  const ATTRIBUTION_STORAGE_KEY = "vision_attribution_v1";
  const ANONYMOUS_ID_STORAGE_KEY = "vision_anonymous_id";
  const SESSION_ID_STORAGE_KEY = "vision_session_id";
  const SESSION_STARTED_STORAGE_KEY = "vision_session_started_at";
  const SESSION_TTL_MS = 30 * 60 * 1000;
  const attributionKeys = ["utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term", "gclid", "fbclid", "ttclid"];

  const nowIso = () => new Date().toISOString();

  const uuid = () => {
    if (window.crypto && typeof window.crypto.randomUUID === "function") {
      return window.crypto.randomUUID();
    }
    return `evt_${Date.now()}_${Math.random().toString(16).slice(2)}`;
  };

  const readJson = (storage, key, fallback) => {
    try {
      const raw = storage.getItem(key);
      return raw ? JSON.parse(raw) : fallback;
    } catch (error) {
      return fallback;
    }
  };

  const writeJson = (storage, key, value) => {
    try {
      storage.setItem(key, JSON.stringify(value));
    } catch (error) {
      // Ignore storage failures in restricted browsing modes.
    }
  };

  const readString = (storage, key) => {
    try {
      return storage.getItem(key) || "";
    } catch (error) {
      return "";
    }
  };

  const writeString = (storage, key, value) => {
    try {
      storage.setItem(key, value);
    } catch (error) {
      // Ignore storage failures in restricted browsing modes.
    }
  };

  const getOrCreateAnonymousId = () => {
    const existing = readString(window.localStorage, ANONYMOUS_ID_STORAGE_KEY);
    if (existing) {
      return existing;
    }
    const next = uuid();
    writeString(window.localStorage, ANONYMOUS_ID_STORAGE_KEY, next);
    return next;
  };

  const getOrCreateSessionId = () => {
    const startedAt = Number(readString(window.sessionStorage, SESSION_STARTED_STORAGE_KEY) || 0);
    const existing = readString(window.sessionStorage, SESSION_ID_STORAGE_KEY);
    if (existing && startedAt && Date.now() - startedAt < SESSION_TTL_MS) {
      writeString(window.sessionStorage, SESSION_STARTED_STORAGE_KEY, String(Date.now()));
      return existing;
    }
    const next = uuid();
    writeString(window.sessionStorage, SESSION_ID_STORAGE_KEY, next);
    writeString(window.sessionStorage, SESSION_STARTED_STORAGE_KEY, String(Date.now()));
    return next;
  };

  const getQueryAttribution = () => {
    const params = new URLSearchParams(window.location.search || "");
    return attributionKeys.reduce((payload, key) => {
      const value = params.get(key);
      if (value) {
        payload[key] = value;
      }
      return payload;
    }, {});
  };

  const hasAttribution = (payload) => attributionKeys.some((key) => payload && payload[key]);

  const currentPageContext = () => ({
    page_path: window.location.pathname || "/",
    page_url: window.location.href,
    referrer: document.referrer || "",
  });

  const captureAttribution = () => {
    const stored = readJson(window.localStorage, ATTRIBUTION_STORAGE_KEY, {});
    const current = {
      ...getQueryAttribution(),
      landing_url: window.location.href,
      landing_path: window.location.pathname || "/",
      referrer: document.referrer || "",
      captured_at: nowIso(),
    };
    const next = {
      first_touch: stored.first_touch || current,
      last_touch: hasAttribution(current) || !stored.last_touch ? current : stored.last_touch,
    };
    writeJson(window.localStorage, ATTRIBUTION_STORAGE_KEY, next);
    return next;
  };

  let attribution = captureAttribution();
  let configPromise = null;
  let metaLoaded = false;
  let tiktokLoaded = false;
  let googleLoaded = false;

  const trackingApiUrl = (path) => `${DEFAULT_API_BASE}${path}`;

  const getTrackingConfig = () => {
    if (configPromise) {
      return configPromise;
    }
    configPromise = fetch(trackingApiUrl("/api/tracking/config"), {
      credentials: "include",
      cache: "no-store",
    })
      .then((response) => (response.ok ? response.json() : {}))
      .catch(() => ({}));
    return configPromise;
  };

  const consentAllowsAds = () => window.VISION_TRACKING_CONSENT !== false;

  const sha256Normalized = async (value) => {
    const normalized = String(value || "").trim().toLowerCase();
    if (!normalized || !window.crypto || !window.crypto.subtle || !window.TextEncoder) {
      return "";
    }
    const digest = await window.crypto.subtle.digest("SHA-256", new TextEncoder().encode(normalized));
    return Array.from(new Uint8Array(digest))
      .map((byte) => byte.toString(16).padStart(2, "0"))
      .join("");
  };

  const browserAdPayload = (event) => {
    const raw = event.payload && typeof event.payload === "object" ? event.payload : {};
    const clean = { ...raw };
    delete clean.customer_email;
    delete clean.first_touch;
    delete clean.last_touch;
    delete clean.payload;
    delete clean.ads_only;
    return {
      ...clean,
      value: event.value,
      currency: event.currency,
      content_id: event.plan_id || event.asset_id || event.job_id,
    };
  };

  const loadScript = (src) =>
    new Promise((resolve, reject) => {
      const existing = Array.from(document.scripts).find((script) => script.src === src);
      if (existing) {
        resolve(existing);
        return;
      }
      const script = document.createElement("script");
      script.async = true;
      script.src = src;
      script.onload = () => resolve(script);
      script.onerror = reject;
      document.head.appendChild(script);
    });

  const ensureMetaPixel = async (config) => {
    if (metaLoaded || !config.meta_pixel_enabled || !config.meta_pixel_id || !consentAllowsAds()) {
      return false;
    }
    window.fbq =
      window.fbq ||
      function () {
        window.fbq.callMethod ? window.fbq.callMethod.apply(window.fbq, arguments) : window.fbq.queue.push(arguments);
      };
    window.fbq.queue = window.fbq.queue || [];
    window.fbq.loaded = true;
    window.fbq.version = "2.0";
    await loadScript("https://connect.facebook.net/en_US/fbevents.js").catch(() => null);
    window.fbq("init", config.meta_pixel_id);
    metaLoaded = true;
    return true;
  };

  const ensureTikTokPixel = async (config) => {
    if (tiktokLoaded || !config.tiktok_pixel_enabled || !config.tiktok_pixel_id || !consentAllowsAds()) {
      return false;
    }
    window.ttq =
      window.ttq ||
      {
        track() {},
        page() {},
        load() {},
      };
    await loadScript("https://analytics.tiktok.com/i18n/pixel/events.js").catch(() => null);
    if (window.ttq && typeof window.ttq.load === "function") {
      window.ttq.load(config.tiktok_pixel_id);
    }
    tiktokLoaded = true;
    return true;
  };

  const ensureGoogleTag = async (config) => {
    if (googleLoaded || !config.google_tag_enabled || !config.google_tag_id || !consentAllowsAds()) {
      return false;
    }
    window.dataLayer = window.dataLayer || [];
    window.gtag =
      window.gtag ||
      function () {
        window.dataLayer.push(arguments);
      };
    await loadScript(`https://www.googletagmanager.com/gtag/js?id=${encodeURIComponent(config.google_tag_id)}`).catch(() => null);
    window.gtag("js", new Date());
    window.gtag("config", config.google_tag_id);
    googleLoaded = true;
    return true;
  };

  const mapMetaBrowserEvent = (name) =>
    ({
      LandingViewed: "PageView",
      StudioViewed: "ViewContent",
      CheckoutStarted: "InitiateCheckout",
      PurchaseCompleted: "Purchase",
    })[name] || "";

  const mapTikTokBrowserEvent = (name) =>
    ({
      LandingViewed: "ViewContent",
      StudioViewed: "ViewContent",
      CheckoutStarted: "InitiateCheckout",
      PurchaseCompleted: "Purchase",
    })[name] || "";

  const mapGoogleBrowserEvent = (name) =>
    ({
      LandingViewed: "page_view",
      StudioViewed: "view_item",
      CheckoutStarted: "begin_checkout",
      PurchaseCompleted: "purchase",
    })[name] || "";

  const dispatchBrowserPixels = async (event) => {
    const config = await getTrackingConfig();
    if (!config.tracking_enabled || !consentAllowsAds()) {
      return;
    }
    const adPayload = browserAdPayload(event);
    const metaEvent = mapMetaBrowserEvent(event.event_name);
    if (metaEvent && (await ensureMetaPixel(config)) && typeof window.fbq === "function") {
      window.fbq("track", metaEvent, adPayload, { eventID: event.event_id });
    }
    const tiktokEvent = mapTikTokBrowserEvent(event.event_name);
    if (tiktokEvent && (await ensureTikTokPixel(config)) && window.ttq && typeof window.ttq.track === "function") {
      window.ttq.track(tiktokEvent, adPayload, { event_id: event.event_id });
    }
    const googleEvent = mapGoogleBrowserEvent(event.event_name);
    if (googleEvent && (await ensureGoogleTag(config)) && typeof window.gtag === "function") {
      if (config.google_enhanced_conversions_enabled && event.customer_email) {
        const emailHash = await sha256Normalized(event.customer_email);
        if (emailHash) {
          window.gtag("set", "user_data", { sha256_email_address: emailHash });
        }
      }
      window.gtag("event", googleEvent, {
        ...adPayload,
        event_id: event.event_id,
        value: event.value,
        currency: event.currency,
        transaction_id: event.checkout_session_id,
      });
    }
  };

  const buildEvent = (name, payload) => {
    attribution = captureAttribution();
    const lastTouch = attribution.last_touch || {};
    const eventPayload = payload && typeof payload === "object" ? payload : {};
    return {
      ...currentPageContext(),
      ...lastTouch,
      ...eventPayload,
      event_name: String(name || ""),
      event_id: eventPayload.event_id || uuid(),
      event_time: nowIso(),
      session_id: getOrCreateSessionId(),
      anonymous_id: getOrCreateAnonymousId(),
      platform_context: eventPayload.platform_context || "web",
      first_touch: attribution.first_touch || {},
      last_touch: attribution.last_touch || {},
      payload: eventPayload,
    };
  };

  const sendFirstPartyEvent = async (event) => {
    const body = JSON.stringify(event);
    try {
      const response = await fetch(trackingApiUrl("/api/track"), {
        method: "POST",
        credentials: "include",
        keepalive: body.length < 60000,
        headers: {
          "Content-Type": "application/json",
        },
        body,
      });
      return response.ok;
    } catch (error) {
      return false;
    }
  };

  const trackEvent = (name, payload) => {
    const event = buildEvent(name, payload || {});
    event.sent = event.ads_only ? Promise.resolve(false) : sendFirstPartyEvent(event);
    void dispatchBrowserPixels(event);
    return event;
  };

  const getContext = (overrides) => {
    attribution = captureAttribution();
    return {
      ...currentPageContext(),
      ...(attribution.last_touch || {}),
      ...(overrides || {}),
      session_id: getOrCreateSessionId(),
      anonymous_id: getOrCreateAnonymousId(),
      first_touch: attribution.first_touch || {},
      last_touch: attribution.last_touch || {},
    };
  };

  window.VisionTracking = {
    trackEvent,
    getContext,
    getTrackingConfig,
  };
})();
