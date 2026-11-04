# ADR 0004: Moodle activity via a small custom plugin (`local_ncsahub`)

Status: accepted (PRD §6.2); implementation in Phase 5

## Decision

A small Moodle local plugin observes course views, module completion, course completion, enrolments, grades and quiz submissions, writes them to a self-pruning outbox, and delivers them to the harness by **push** (signed batches from a scheduled task; outbound HTTPS from web.illinois.edu works) or **pull** (the harness polls a web-service function). The harness stores xAPI-shaped statements.

## Why not logstore_xapi

Around 300 lines we own, no dependency on a community plugin's release cadence, and the harness does the xAPI shaping.

## Constraints from the host

No `exec()`, each batch well inside 60 s, cron every minute (set 2026-11-02). Moodle keeps logs 180 days (set 2026-11-02), so history before the plugin exists comes from the log exports on Taiga.

## Why last

Phases 1–4 need only web-service tokens. Nothing custom is installed on production Moodle until the platform has proved itself.
