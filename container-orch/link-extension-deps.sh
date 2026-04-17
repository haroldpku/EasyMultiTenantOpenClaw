#!/bin/sh
# Symlink bundled extension node_modules into the openclaw package root
# so Node's module resolver can find them from dist/*.js files.
set -e

OCDIR=/usr/local/lib/node_modules/openclaw

for ext_nm in "$OCDIR"/dist/extensions/*/node_modules; do
    [ -d "$ext_nm" ] || continue
    for pkg in "$ext_nm"/*; do
        name=$(basename "$pkg")
        if [ "${name#@}" != "$name" ]; then
            # Scoped package: link each sub-package
            for subpkg in "$pkg"/*; do
                scope="$name"
                sub=$(basename "$subpkg")
                target="$OCDIR/node_modules/$scope/$sub"
                [ -e "$target" ] && continue
                mkdir -p "$OCDIR/node_modules/$scope"
                ln -s "$subpkg" "$target" 2>/dev/null || true
            done
        else
            target="$OCDIR/node_modules/$name"
            [ -e "$target" ] && continue
            ln -s "$pkg" "$target" 2>/dev/null || true
        fi
    done
done
