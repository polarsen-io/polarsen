# Release Notes

## What's New
- Pre-push hook to auto-update PR description from release notes
- Embeddings worker for batch processing message group embeddings
- Bulk embedding generation with `gen_groups_embeddings` function
- User ID tracking on all AI requests for proper token usage attribution
- `processed_at` field to `ChatUpload` model
- Sentry integration for error capture in Telegram bot
- Database setup timing warnings for slow SQL files (>2s)

## Improvements
- Increased listener verbosity from `-v` to `-vv` for better debugging
- Improved file upload error handling in Telegram bot with detailed error messages
- Hide already processed uploads from chat list display

## Bug Fixes
- Embeddings worker error handling - error status was being reset when exceptions were re-raised
- Callback query data parsing for chat selection (was using wrong prefix)
- Telegram file download error handling with user-friendly messages

## Infrastructure
- Docker build workflow now triggers on test completion
- Added `latest-nightly` tag for master branch builds
- Added FSL-1.1-ALv2 license label to Docker images
- SHA tags now generated for all builds
- Automated pre-release (rc) on PR merge to master
- Added pre-release support to release workflow