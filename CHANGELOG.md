# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Unreleased

### Changed

- Postgres connections retain exact floating-point timestamp readback even when the server defaults `extra_float_digits` to zero.

- Data moves from SQLite on the Fly volume to Postgres on Supabase (bia-backend's project, schema `wuumtaaba`); voice notes move to the private Supabase Storage bucket `wuumtaaba-media`. New settings: `DATABASE_URL`, `DATABASE_SCHEMA`, `SUPABASE_URL`, `SUPABASE_API_KEY`, `MEDIA_BUCKET`. `DATABASE_PATH` and `MEDIA_DIRECTORY` are gone.
- The Fly machine has no volume and scales to zero: the bot exits by itself after `IDLE_SHUTDOWN_SECONDS` with empty queues, and the next request starts it again. A daily `pg_cron` call wakes it at 09:00 for reminders.
- The container runs as the `bot` user from the start; `scripts/container_start.py` is gone.
- The project follows the BurkimbIA Python layout: `pyproject.toml` built with hatchling, dependencies locked in `uv.lock`, a `justfile`, prek hooks. `requirements.lock` is gone.

### Added

- `scripts/copy_volume_to_supabase.py` restores a complete SQLite 0.7 snapshot, including payments and mobile sessions. An explicit `--replace-snapshot` restores updates and deletions atomically; schema drift, missing/corrupt audio and differing readback fail closed.

### Intégration de la version 0.7

- Conservation des paiements partiels, sessions mobiles et accords vocaux français/mooré sur Postgres et stockage privé.
- Restauration transactionnelle complète des snapshots SQLite 0.7, vérification des valeurs et refus de schéma inconnu.
- Déploiement Fly limité à une instance et procédure de bascule avec suspension des écritures.

## v0.5.0 - 2026-10-16

### Added

- BurkimbIA services in the WhatsApp menu (translation, Mooré transcription, speech synthesis) and the admin switch between the Mac connector and the RunPod cloud.
- Read-only admin dashboard for agreements, conversations and reminders.
- Durable interaction history, private audio archive and consent-based reminders.
