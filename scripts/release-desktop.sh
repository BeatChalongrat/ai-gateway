#!/bin/bash
# R11 B4a: package a desktop release — copies the Inno Setup installer into
# releases/ and (re)writes the auto-update manifest with its SHA-256.
set -e

VERSION=$1
if [ -z "$VERSION" ]; then
    echo "Usage: ./scripts/release-desktop.sh 1.2.0"
    exit 1
fi

INSTALLER="SranDesktop/installer/SranDesktop-${VERSION}-Setup.exe"

if [ ! -f "$INSTALLER" ]; then
    echo "ERROR: Installer not found at $INSTALLER"
    echo "Build it first:"
    echo "  1. Bump <Version> in SranDesktop/src/SranDesktop.App/SranDesktop.App.csproj"
    echo "     and MyAppVersion in SranDesktop/build/SranDesktop.iss to ${VERSION}"
    echo "  2. dotnet publish SranDesktop/src/SranDesktop.App -c Release -r win-x64 -o SranDesktop/publish/win-x64"
    echo "  3. ISCC.exe SranDesktop/build/SranDesktop.iss"
    exit 1
fi

SHA256=$(sha256sum "$INSTALLER" | cut -d' ' -f1)

mkdir -p releases
cp "$INSTALLER" "releases/SranDesktop-${VERSION}-Setup.exe"

cat > releases/latest.json <<EOF
{
  "version": "${VERSION}",
  "releaseDate": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "downloadUrl": "SranDesktop-${VERSION}-Setup.exe",
  "sha256": "${SHA256}",
  "releaseNotes": "",
  "minVersion": "1.0.0",
  "mandatory": false
}
EOF

echo "Release ${VERSION} ready:"
echo "  Installer: releases/SranDesktop-${VERSION}-Setup.exe"
echo "  Manifest:  releases/latest.json"
echo "  SHA-256:   ${SHA256}"
echo ""
echo "downloadUrl is relative to the manifest URL (the desktop client resolves"
echo "it against Sran:UpdateManifestUrl), so the same files work behind any host."
echo "Edit releases/latest.json to add releaseNotes, then deploy."
