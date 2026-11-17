#!/usr/bin/env bash
set -euo pipefail

# Build the full Forager website
# Output: dist/ directory ready for deployment

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
DIST_DIR="$ROOT_DIR/dist"

echo "Building Forager website..."

# Clean previous build
rm -rf "$DIST_DIR"
mkdir -p "$DIST_DIR"

# 1. Build mdbook documentation
echo "Building documentation with mdbook..."
if command -v mdbook &> /dev/null; then
    (cd "$ROOT_DIR" && mdbook build)
else
    echo "Error: mdbook not found. Install with: cargo install mdbook"
    exit 1
fi

# 2. Copy shared assets to website/public before Astro build
echo "Copying assets to website..."
mkdir -p "$ROOT_DIR/website/public/assets"
cp "$ROOT_DIR/assets/logo.svg" "$ROOT_DIR/website/public/assets/"
cp "$ROOT_DIR/assets/logo.png" "$ROOT_DIR/website/public/assets/"
cp "$ROOT_DIR/assets/logo-lockup.svg" "$ROOT_DIR/website/public/assets/" 2>/dev/null || true
cp "$ROOT_DIR/assets/kisti-logo-en.png" "$ROOT_DIR/website/public/assets/"
cp "$ROOT_DIR/assets/social-preview.png" "$ROOT_DIR/website/public/assets/" 2>/dev/null || true
cp "$ROOT_DIR/assets/social-preview.svg" "$ROOT_DIR/website/public/assets/" 2>/dev/null || true
cp "$ROOT_DIR/theme/favicon.png" "$ROOT_DIR/website/public/assets/" 2>/dev/null || true
cp "$ROOT_DIR/docs/assets/tui.png" "$ROOT_DIR/website/public/assets/"

# 3. Build Astro website
echo "Building Astro website..."
(cd "$ROOT_DIR/website" && npm ci && npm run build)

# 4. Copy Astro output to dist/
echo "Copying website..."
cp -r "$ROOT_DIR/website/dist/"* "$DIST_DIR/"

# 5. Copy mdbook output to dist/docs/
echo "Copying documentation..."
cp -r "$ROOT_DIR/book" "$DIST_DIR/docs"

# 6. Copy install script
echo "Copying install script..."
cp "$ROOT_DIR/scripts/install.sh" "$DIST_DIR/"

# 7. Create a standalone 404 page with local navigation.
cat > "$DIST_DIR/404.html" << 'EOF'
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Page Not Found - Forager</title>
  <link rel="icon" type="image/svg+xml" href="/forager-cli/assets/logo.svg">
  <style>
    body { margin: 0; min-height: 100vh; display: grid; place-items: center; background: #081625; color: #f8fafc; font: 1rem/1.6 system-ui, sans-serif; }
    main { max-width: 38rem; padding: 3rem 1.5rem; text-align: center; }
    img { width: 4rem; height: 4rem; }
    h1 { margin: 1rem 0; font-size: clamp(2rem, 6vw, 3rem); line-height: 1.1; }
    p { color: #cbd5e1; }
    a { display: inline-block; margin: 1rem .5rem 0; padding: .75rem 1.25rem; background: #0075ba; color: white; text-decoration: none; }
    a:hover { background: #00639d; }
    a:focus-visible { outline: 3px solid #38bdf8; outline-offset: 4px; }
  </style>
</head>
<body>
  <main>
    <img src="/forager-cli/assets/logo.svg" alt="Forager">
    <h1>Page not found</h1>
    <p>This address does not match a published page.</p>
    <a href="/forager-cli/">Forager home</a>
    <a href="/forager-cli/docs/">Documentation</a>
  </main>
</body>
</html>
EOF

echo ""
echo "Build complete! Output in: $DIST_DIR"
echo ""
echo "Directory structure:"
find "$DIST_DIR" -type f -print 2>/dev/null | head -20 | sed "s|$DIST_DIR|dist|" || true
echo ""
echo "To preview locally:"
echo "  mkdir -p $ROOT_DIR/target/site-preview"
echo "  ln -sfn $DIST_DIR $ROOT_DIR/target/site-preview/forager-cli"
echo "  cd $ROOT_DIR/target/site-preview && python3 -m http.server 8000"
echo "  open http://127.0.0.1:8000/forager-cli/"
echo ""
echo "To deploy to GitHub Pages, Cloudflare Pages, or Netlify:"
echo "  Point your deployment to the dist/ directory"
