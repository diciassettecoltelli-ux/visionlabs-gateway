#!/bin/sh
set -eu

rm -rf public
mkdir -p public public/studio
mkdir -p public/generated/smoke

cp index.html public/index.html
cp index.html public/studio/index.html
cp style.css public/style.css
cp public-pages.css public/public-pages.css
cp public-footer.css public/public-footer.css
cp app.js public/app.js
cp public-footer.js public/public-footer.js
cp vision-config.js public/vision-config.js
cp vision-tracking.js public/vision-tracking.js
cp favicon.svg public/favicon.svg
cp brand-logo.svg public/brand-logo.svg
cp brand-logo-shared.css public/brand-logo-shared.css
cp studio-shell-new.css public/studio-shell-new.css
cp studio-shell-new.js public/studio-shell-new.js
cp how-it-works.html public/how-it-works.html
cp contact.html public/contact.html
cp faq.html public/faq.html
cp support.html public/support.html
cp downloads.html public/downloads.html
cp accessibility.html public/accessibility.html
cp legal.html public/legal.html

if [ -d assets ]; then
  cp -R assets public/assets
fi

cp assets/candle.mp4 public/generated/smoke/candle-proof.mp4
cp assets/burgundy-editorial-portrait.jpg public/generated/smoke/editorial-proof.jpg
