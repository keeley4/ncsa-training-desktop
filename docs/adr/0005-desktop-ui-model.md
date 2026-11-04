# ADR 0005: Desktop metaphor, not desktop mechanics

Status: accepted (PRD §4.1)

## Decision

Keep the feel of a desktop: shelves of app tiles, a taskbar with search, "my stuff" later. Do not build draggable, overlapping windows or a Start menu. A tile opens a side sheet (a dialog in Phase 1), not a window.

## Why

Window management fails WCAG 2.1 AA in practice, breaks on phones, and costs build time without helping anyone learn.

## Rules the front door follows

- Every action works by keyboard: `/` focuses search; arrow keys move along a shelf; Enter opens; Esc closes.
- Works at phone width (375 px) with no horizontal page scroll (shelves scroll sideways inside themselves).
- Text from the API is inserted as text, never as HTML.
