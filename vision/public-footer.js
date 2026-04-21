(() => {
  const isStudioRoute = /^\/studio\/?$/.test(window.location.pathname);
  if (isStudioRoute) {
    return;
  }

  const footerHost = document.querySelector("[data-public-footer]");
  if (!footerHost) {
    return;
  }

  footerHost.innerHTML = `
    <footer class="public-footer" aria-label="Vision public footer">
      <div class="public-footer__inner">
        <div class="public-footer__primary">
          <div class="public-footer__brand">
            <a class="public-footer__brand-link" href="/" aria-label="Vision home">
              <span class="brand-mark" aria-hidden="true">
                <img class="brand-mark-image" src="/brand-logo.svg?v=2" alt="" />
              </span>
              <span class="brand-name">Vision</span>
            </a>
            <p class="public-footer__brand-copy">
              Cinematic image and motion creation with private access and premium exports.
            </p>
          </div>

          <div class="public-footer__columns">
            <section class="public-footer__group" data-footer-group>
              <button class="public-footer__heading" type="button" aria-expanded="false">
                Product
              </button>
              <div class="public-footer__links">
                <a href="/studio/">Studio</a>
                <a href="/#gallery">Gallery</a>
                <a href="/?open=subscribe">Pricing</a>
                <a href="/how-it-works.html">How it works</a>
              </div>
            </section>

            <section class="public-footer__group" data-footer-group>
              <button class="public-footer__heading" type="button" aria-expanded="false">
                Access
              </button>
              <div class="public-footer__links">
                <a href="/?open=access">Log in</a>
                <a href="/?open=subscribe">Buy a Vision pack</a>
                <a href="/?open=access">Account</a>
                <a href="/downloads.html">Downloads</a>
              </div>
            </section>

            <section class="public-footer__group" data-footer-group>
              <button class="public-footer__heading" type="button" aria-expanded="false">
                Company
              </button>
              <div class="public-footer__links">
                <a href="/#about">About</a>
                <a href="/contact.html">Contact</a>
                <a href="/faq.html">FAQ</a>
                <a href="/support.html">Support</a>
              </div>
            </section>

            <section class="public-footer__group" data-footer-group>
              <button class="public-footer__heading" type="button" aria-expanded="false">
                Legal
              </button>
              <div class="public-footer__links">
                <a href="/legal.html#terms">Terms of Use</a>
                <a href="/legal.html#privacy">Privacy Policy</a>
                <a href="/legal.html#cookies">Cookie Policy</a>
                <a href="/legal.html#refunds">Refund Policy</a>
                <a href="/legal.html#copyright">Copyright / Usage</a>
              </div>
            </section>
          </div>
        </div>

        <div class="public-footer__secondary">
          <div class="public-footer__meta">
            <span>© Vision Studio Lab</span>
          </div>

          <div class="public-footer__trust">
            <span>Secure checkout with Stripe</span>
            <span>No watermark on paid exports</span>
          </div>

          <div class="public-footer__social" aria-label="Vision channels">
            <a href="https://instagram.com" target="_blank" rel="noreferrer noopener" aria-label="Instagram">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">
                <rect x="3.5" y="3.5" width="17" height="17" rx="5"></rect>
                <circle cx="12" cy="12" r="3.75"></circle>
                <path d="M17.5 6.5h.01"></path>
              </svg>
            </a>
            <a href="https://tiktok.com" target="_blank" rel="noreferrer noopener" aria-label="TikTok">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">
                <path d="M14 4c1 2.1 2.9 3.6 5 3.9"></path>
                <path d="M9.5 12.5A4.5 4.5 0 1 0 14 17V4"></path>
              </svg>
            </a>
            <a href="https://youtube.com" target="_blank" rel="noreferrer noopener" aria-label="YouTube">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">
                <path d="M22 12s0-3.2-.4-4.7a2.5 2.5 0 0 0-1.7-1.7C18.4 5 12 5 12 5s-6.4 0-7.9.6a2.5 2.5 0 0 0-1.7 1.7C2 8.8 2 12 2 12s0 3.2.4 4.7a2.5 2.5 0 0 0 1.7 1.7C5.6 19 12 19 12 19s6.4 0 7.9-.6a2.5 2.5 0 0 0 1.7-1.7C22 15.2 22 12 22 12Z"></path>
                <path d="m10 9 5 3-5 3V9Z"></path>
              </svg>
            </a>
            <a href="https://linkedin.com" target="_blank" rel="noreferrer noopener" aria-label="LinkedIn">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">
                <path d="M16 8a6 6 0 0 1 6 6v7h-4v-7a2 2 0 0 0-4 0v7h-4v-12h4v2"></path>
                <rect x="2" y="9" width="4" height="12"></rect>
                <circle cx="4" cy="4" r="2"></circle>
              </svg>
            </a>
          </div>

          <div class="public-footer__utility">
            <a href="/accessibility.html">Accessibility</a>
          </div>
        </div>
      </div>
    </footer>
  `;

  const groups = Array.from(footerHost.querySelectorAll("[data-footer-group]"));
  const mq = window.matchMedia("(max-width: 760px)");
  const syncGroups = () => {
    groups.forEach((group, index) => {
      const button = group.querySelector(".public-footer__heading");
      if (!button) {
        return;
      }
      if (mq.matches) {
        const expanded = button.getAttribute("aria-expanded") === "true";
        group.classList.toggle("is-open", expanded);
      } else {
        button.setAttribute("aria-expanded", index === 0 ? "true" : "true");
        group.classList.add("is-open");
      }
    });
  };

  groups.forEach((group, index) => {
    const button = group.querySelector(".public-footer__heading");
    if (!button) {
      return;
    }
    button.setAttribute("aria-expanded", index === 0 ? "true" : "false");
    button.addEventListener("click", () => {
      if (!mq.matches) {
        return;
      }
      const expanded = button.getAttribute("aria-expanded") === "true";
      button.setAttribute("aria-expanded", expanded ? "false" : "true");
      group.classList.toggle("is-open", !expanded);
    });
  });

  if (typeof mq.addEventListener === "function") {
    mq.addEventListener("change", syncGroups);
  } else if (typeof mq.addListener === "function") {
    mq.addListener(syncGroups);
  }
  syncGroups();
})();
