# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Unreleased

### Changed

- The project follows the BurkimbIA Python layout: `pyproject.toml` built with hatchling, dependencies locked in `uv.lock`, a `justfile`, prek hooks. `requirements.lock` is gone.

## v0.5.0 - 2026-10-16

### Added

- BurkimbIA services in the WhatsApp menu (translation, Mooré transcription, speech synthesis) and the admin switch between the Mac connector and the RunPod cloud.
- Read-only admin dashboard for agreements, conversations and reminders.
- Durable interaction history, private audio archive and consent-based reminders.
