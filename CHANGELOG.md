# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Unreleased

### Added

- Dedicated WhatsApp annotation routing for assigned A/B examples, ratings and optional corrections; task sources, assignments, response snapshots and idempotent events are stored in Postgres with RLS, and source audio is verified in private Tigris storage before registration.
- Read-only planning and explicit import of the two annotation previews already sent, plus a review document describing task choices and the production persistence checks.

### Changed

- All bot audio (agreements, annotation sources and admin experiments) uses private Tigris `burkimbia-store`, with Supabase retaining database records and object references. A read-only/explicit-apply migration verifies existing audio without deleting its source.

- Les accords partiels peuvent être résumés et confirmés avec les informations connues, sans relances successives pour les champs absents.

- WhatsApp opens Wuumtaaba directly; the space selector and standalone BurkimbIA services are removed. Existing agreement drafts and Mooré model integrations are preserved; old portal buttons return to the carnet.

- Postgres connections retain exact floating-point timestamp readback even when the server defaults `extra_float_digits` to zero.

- Data moves from SQLite on the Fly volume to Postgres on Supabase (bia-backend's project, schema `wuumtaaba`); voice notes move to the private Supabase Storage bucket `wuumtaaba-media`. New settings: `DATABASE_URL`, `DATABASE_SCHEMA`, `SUPABASE_URL`, `SUPABASE_API_KEY`, `MEDIA_BUCKET`. `DATABASE_PATH` and `MEDIA_DIRECTORY` are gone.
- The Fly machine has no volume and scales to zero: the bot exits by itself after `IDLE_SHUTDOWN_SECONDS` with empty queues, and the next request starts it again. A daily `pg_cron` call wakes it at 09:00 for reminders.
- The container runs as the `bot` user from the start; `scripts/container_start.py` is gone.
- The project follows the BurkimbIA Python layout: `pyproject.toml` built with hatchling, dependencies locked in `uv.lock`, a `justfile`, prek hooks. `requirements.lock` is gone.

### Added
- Accès direct RunPod pour la transcription cloud de Wumtaaba Check, sans quota de la plateforme BurkimbIA ; traduction cloud retirée (NLLB absent, Mistral et Tengsoaba exclus).
- Wumtaaba Check dans le backoffice authentifié : tests audio privés sur Supabase (`experiments` et Storage), choix des modèles indépendant du bot, écoute et résultats remplacés à chaque analyse.

- The Mac connector can wait in Supabase (table `mac_link`) instead of polling Fly: it writes its presence there and polls Fly only while a job waits, so the machine stops when idle. A connector that still polls Fly keeps working and keeps the machine on.
- `scripts/copy_volume_to_supabase.py` restores a complete SQLite 0.7 snapshot, including payments and mobile sessions. An explicit `--replace-snapshot` restores updates and deletions atomically; schema drift, missing/corrupt audio and differing readback fail closed.

### Fixed

- A slow Supabase write no longer eats a Mac job's deadline: the bot writes `wanted_until` before the job's clock starts. On main, a 10 ms test deadline expired during that write and the job vanished before the connector could take it.

### Security

- Row level security is on for every table of the bot's schema. The bot owns the tables and bypasses it; the policy `mac_connector` keeps the Mac connector's role on its `mac_link` row. `anon`, `authenticated` and `service_role` already had no access to the schema: this guards the day it is exposed to the Data API.
- `search_fold` runs with an empty `search_path`, as Supabase's advisor asks: it only calls Postgres built-ins.

### Intégration de la version 0.7

- Conservation des paiements partiels, sessions mobiles et accords vocaux français/mooré sur Postgres et stockage privé.
- Restauration transactionnelle complète des snapshots SQLite 0.7, vérification des valeurs et refus de schéma inconnu.
- Déploiement Fly limité à une instance et procédure de bascule avec suspension des écritures.

## v0.5.0 - 2026-10-16

### Added

- BurkimbIA services in the WhatsApp menu (translation, Mooré transcription, speech synthesis) and the admin switch between the Mac connector and the RunPod cloud.
- Read-only admin dashboard for agreements, conversations and reminders.
- Durable interaction history, private audio archive and consent-based reminders.
