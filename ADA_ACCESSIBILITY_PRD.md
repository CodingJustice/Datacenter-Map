# PRD: ADA Accessible Infrastructure Map

Version: 1.0
Date: July 22, 2026
Product: U.S. Data Centers, Power, Nuclear, and Water Landscape

## Source Of Truth

This PRD uses the current DOJ Title II web rule as the compliance floor and
WCAG 2.2 as a forward-looking product target.

- DOJ ADA Title II web rule fact sheet: https://www.ada.gov/resources/2024-03-08-web-rule/
- DOJ first steps guidance: https://www.ada.gov/resources/web-rule-first-steps/
- W3C WCAG 2.2 quick reference: https://www.w3.org/WAI/WCAG22/quickref/
- W3C non-text contrast guidance: https://www.w3.org/WAI/WCAG22/Understanding/non-text-contrast.html

As of July 22, 2026, DOJ guidance identifies WCAG 2.1 Level AA as the technical
standard for Title II web content and mobile apps. The same DOJ fact sheet says
the April 20, 2026 interim final rule extended compliance dates to April 26,
2027 for public entities with a population of 50,000 or more and April 26, 2028
for public entities with a population under 50,000 or special district
governments. This PRD is not legal advice; counsel should confirm the applying
title, entity type, deadline, exceptions, and procurement obligations before
public launch.

## Problem

The current map communicates a dense geospatial story through markers, color,
clusters, popups, and spatial relationships. That experience can exclude users
who rely on screen readers, keyboard navigation, magnification, high contrast,
reduced motion, or non-color visual cues. A compliant version must preserve the
analytical value of the map while offering equivalent, operable, and robust
access to the same core information.

## Goals

- Meet WCAG 2.1 Level AA for the map page, controls, primary data-center
  exploration flow, details panel, popups, and source links.
- Target WCAG 2.2 Level AA where practical, especially target size, focus
  visibility, and non-text contrast.
- Provide a keyboard-accessible non-map results model for users who cannot use
  or interpret the visual map.
- Avoid communicating status, layer type, or proximity solely by color.
- Preserve the existing static hosting model, restrictive CSP, safe DOM
  rendering, and lazy loading of large map layers.

## Non-Goals

- This release does not certify legal ADA compliance by itself.
- This release does not redesign the data pipeline, source collection process,
  or public data methodology.
- This release does not expose all 15,975 power-plant records in a full data
  grid. The power layer remains a visual, optional layer until a performant
  accessible table/export path is prioritized.

## Users

- Keyboard-only analyst: filters the map, reviews results, opens details, and
  follows source links without a mouse.
- Screen reader user: understands the map purpose, navigates controls in a
  logical order, reviews text results, and receives detail updates.
- Low-vision user: zooms browser text, uses high-contrast settings, and needs
  visible focus indicators and readable controls.
- Color-vision-deficient user: distinguishes statuses and layer types through
  text labels, marker initials, dash patterns, and legends rather than color
  alone.
- Mobility-impaired touch user: needs predictable controls and large enough
  hit targets.

## Functional Requirements

1. Page structure
   - The page must expose skip links for controls and results.
   - The map must have an accessible name and a concise hidden description.
   - The controls must use native form controls with labels, fieldsets, and
     legends.
   - Dynamic count and detail changes must be announced politely to assistive
     technologies.

2. Search and filters
   - Search must have a persistent accessible label, not only placeholder text.
   - Status filters and layer toggles must remain native checkboxes.
   - Filter state must update the visual markers and the accessible results list
     from the same filtered data model.

3. Accessible results model
   - Filtered data-center records must be available as keyboard-focusable result
     buttons.
   - Each result must include name, status, location, capacity where available,
     and nearest nuclear/power/water context where available.
   - Activating a result must pan or jump the visual map to that record and open
     the same detail experience used by marker activation.

4. Visual map model
   - The default basemap must prioritize readable contrast.
   - Data-center status must be encoded by text tags and colors, not color
     alone.
   - Nuclear, power, and water points must include visible letter labels.
   - Proximity lines must use distinguishable dash patterns and sufficient
     opacity/weight.
   - Marker and cluster focus states must be visible when reached by keyboard.

5. Motion and input
   - Animated map movement must respect `prefers-reduced-motion`.
   - Interactive targets should be at least 44 CSS pixels where layout permits.
   - The map must not trap keyboard focus.

6. Responsive behavior
   - Controls and result/detail panels must remain available on mobile.
   - Text must reflow without horizontal page scrolling at common mobile widths.
   - Detail content must not be hidden from assistive technology on narrow
     screens.

## Accessibility Acceptance Criteria

- Automated checks report no critical violations for missing labels, missing
  accessible names, empty buttons, or color contrast in first-party UI.
- A keyboard user can tab from skip links to search, filters, layer toggles,
  results, details, Leaflet controls, and map markers.
- Activating a result with Enter or Space opens the corresponding map marker
  popup and updates the detail panel.
- Screen reader landmarks/regions expose map controls, results, details, and
  map purpose in a logical order.
- Status and layer type are understandable without relying on color.
- Browser zoom to 200 percent preserves access to controls, results, and
  details without two-dimensional text scrolling.
- `prefers-reduced-motion: reduce` disables decorative marker motion and uses
  non-animated map movement.

## Release Plan

1. Implement semantic labels, result list, focus states, larger targets, and
   default high-readability basemap.
2. Test with keyboard-only navigation, browser zoom, reduced motion, and at
   least one automated accessibility scan.
3. Add a public accessibility statement and support contact before production
   launch.
4. Add a full accessible table/export path for large optional layers in the next
   iteration.

## Open Questions

- Is the intended publisher a Title II public entity, a Title III public
  accommodation, or a private informational publisher?
- Does the production INCOMPAS page embed this map in an iframe, and if so, what
  parent heading structure and skip-link behavior should the embedded map honor?
- Should the power-plant layer receive a full accessible table in this release
  or in a follow-up release?
