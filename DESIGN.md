# Design

## Source of truth
Active — 2026-11-11. The bot package owns the common local/production backoffice. Evidence: admin.py, admin_data.py, static/admin.html, static/check.*, annotation_schema.sql and the earlier Mac conversation simulator. Local prototype files in inference_endpoints are references, not a second production UI.

## Brand
BurkimbIA/Wumtaaba: calm, practical, trustworthy. Retain green, cream and readable typography. Avoid marketing decoration in operational pages.

## Product goals
Iterate locally with a separate database matching the production application schema; publish reviewed page changes through Git and migrations. Show the active environment clearly. Preserve WhatsApp business behavior.

## Personas and jobs
Administrators review agreements and conversations; model contributors compare a saved vocal across models; annotators inspect source proposals and collected judgments; developers rehearse changes without writing production data.

## Information architecture
One shared left navigation: Accords, échéances et rappels; Conversations WhatsApp; Annotation; Wumtaaba Check; Modèles IA. Development also exposes Simulateur WhatsApp. Existing /admin and /admin/check URLs stay valid. Accords and reminders share one page with their own filters/pagination.

## Design principles
Same frontend files in both environments. Explicit DEV/PROD identity. Isolate data and external side effects. Show actual provider capabilities; never disguise the platform cloud proxy as direct RunPod.

## Visual language
Existing green/cream palette, system fonts, spacing and rounded panels. Dense but readable lists; clear section headings; limited motion; no external font or image dependency.

## Components
Shared admin-shell.js/css owns sidebar, active links and environment badge. Existing page renderers own business tables, model cards, conversation threads and fixed Check result zones. Simulator owns its isolated chat content inside the same shell.

## Accessibility
Semantic navigation and labels, keyboard-accessible links/actions, visible focus, textual statuses and errors, no color-only distinction. Preserve native audio controls and play/pause.

## Responsive behavior
Fixed left sidebar on desktop; compact navigation on small screens. Tables may scroll horizontally. Forms/results use one column on narrow screens.

## Interaction states
Loading, empty data, expired session, offline Mac, unavailable cloud translation and partial inference failure are explicit. Check overwrites its three result zones for each run. Navigation must preserve authentication boundaries.

## Content voice
French, short and concrete. Use accord, échéance, rappel, audio, modèle and annotation consistently. Environment and provider labels describe actual behavior.

## Implementation constraints
FastAPI and native HTML/CSS/JavaScript; no new frontend dependencies. Supabase Postgres stores production data; current production audio storage is private Tigris. Dev uses local Postgres and local files, without a WhatsApp worker. Page commits remain reviewable; existing CI deploys a complete versioned container, not isolated files edited in place.

## Open questions
- [ ] Future publication automation: a dedicated page-publishing control is outside the current Git-based workflow.
- [ ] Importing anonymized production samples requires a separate explicit decision; default clone is schema-only.
