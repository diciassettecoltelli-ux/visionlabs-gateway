#!/bin/sh
set -eu

rm -rf public
mkdir -p public public/studio

cp index.html public/index.html
cp index.html public/studio/index.html
cp style.css public/style.css
cp app.js public/app.js
cp vision-config.js public/vision-config.js
cp favicon.svg public/favicon.svg
cp brand-logo.svg public/brand-logo.svg
cp brand-logo-shared.css public/brand-logo-shared.css
cp studio-shell-new.css public/studio-shell-new.css
cp studio-shell-new.js public/studio-shell-new.js

if [ -d assets ]; then
  cp -R assets public/assets
fi
