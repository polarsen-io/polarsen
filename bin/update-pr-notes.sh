#!/usr/bin/env bash

# DESCRIPTION: Updates PR description with RELEASE_NOTES.md content if the file exists.
# USAGE: ./update-pr-notes.sh

set -eou pipefail

RELEASE_NOTES="RELEASE_NOTES.md"

# Check if release notes exist
if [ ! -f "$RELEASE_NOTES" ]; then
    exit 0
fi

# Check if gh CLI is available
if ! command -v gh &> /dev/null; then
    echo "⚠️  gh CLI not found, skipping PR update"
    exit 0
fi

# Skip if current commit is tagged (already released)
if git describe --exact-match --tags HEAD 2>/dev/null; then
    echo "⏭️  Current commit is tagged, skipping PR update"
    exit 0
fi

# Check if there's an open PR for this branch
PR_NUMBER=$(gh pr view --json number -q '.number' 2>/dev/null || echo "")

if [ -z "$PR_NUMBER" ]; then
    exit 0
fi

echo "📋 Updating PR #$PR_NUMBER with release notes..."

# Read release notes content (skip the first line which is the title)
RELEASE_CONTENT=$(tail -n +3 "$RELEASE_NOTES")

# Get existing PR body
EXISTING_BODY=$(gh pr view --json body -q '.body' 2>/dev/null || echo "")

# Markers for auto-generated section
START_MARKER="<!-- RELEASE_NOTES_START -->"
END_MARKER="<!-- RELEASE_NOTES_END -->"

# Create the auto-generated section
AUTO_SECTION=$(cat <<EOF
$START_MARKER
---
> 📋 *Auto-generated from RELEASE_NOTES.md*
---

$RELEASE_CONTENT
$END_MARKER
EOF
)

# Check if markers exist in the current body
if echo "$EXISTING_BODY" | grep -q "$START_MARKER"; then
    # Replace existing auto-generated section, preserve user content
    PR_BODY=$(echo "$EXISTING_BODY" | sed "/$START_MARKER/,/$END_MARKER/d")
    # Remove leading/trailing blank lines from user content
    PR_BODY=$(echo "$PR_BODY" | sed '/^$/d')
    if [ -n "$PR_BODY" ]; then
        PR_BODY="${PR_BODY}

${AUTO_SECTION}"
    else
        PR_BODY="$AUTO_SECTION"
    fi
else
    # No existing section, append auto-generated content
    if [ -n "$EXISTING_BODY" ]; then
        PR_BODY="${EXISTING_BODY}

${AUTO_SECTION}"
    else
        PR_BODY="$AUTO_SECTION"
    fi
fi

# Update PR
gh pr edit "$PR_NUMBER" --body "$PR_BODY"

echo "✅ PR #$PR_NUMBER updated with release notes"
