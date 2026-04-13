#!/bin/sh
set -eu

rm -rf public
mkdir -p public

cp index.html public/index.html
cp style.css public/style.css
cp app.js public/app.js
cp vision-config.js public/vision-config.js
cp favicon.svg public/favicon.svg

if [ -d assets ]; then
  cp -R assets public/assets
fi
