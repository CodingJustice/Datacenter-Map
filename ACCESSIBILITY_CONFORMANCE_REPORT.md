# Accessibility Conformance Report Draft

Product: U.S. Data Centers, Power, Nuclear, and Water Landscape
Report date: July 22, 2026
Standard evaluated: WCAG 2.1 Level AA, with selected WCAG 2.2 AA hardening
Report status: Draft for independent audit

This draft documents the implementation state after the accessibility remodel.
It is not a final VPAT or legal certification. Final conformance language must
be approved after automated and manual assistive-technology testing on the
production deployment.

## Evaluation Methods

- Source inspection of HTML, CSS, and JavaScript.
- Browser verification on the local static server.
- Keyboard interaction checks for search, filters, layer toggles, results, map
  controls, marker details, and source links.
- DOM checks for accessible names, fieldsets, live regions, result buttons, and
  marker labels.
- JavaScript syntax verification with `node --check app.js`.

## Summary

| Area | Draft status | Notes |
| --- | --- | --- |
| Semantic page structure | Supports | Skip links, named map region, labeled controls, fieldsets, results region, and live detail panel are present. |
| Keyboard operation | Supports, pending audit | Native controls and result buttons are keyboard reachable. Marker keyboard behavior depends on Leaflet's marker keyboard support and should be retested after embedding. |
| Non-text content | Supports, pending audit | Markers include accessible names and visible text tags. Decorative marker internals are hidden from assistive tech. |
| Color and contrast | Supports, pending audit | UI colors were darkened, focus outlines added, and marker meanings are not color-only. Run a contrast tool on the production page. |
| Reflow and zoom | Supports, pending audit | The control shell scrolls within the viewport and mobile detail/results remain available. Verify at 200 percent zoom in production. |
| Motion | Supports | Decorative marker motion is disabled for reduced-motion users; large programmatic map jumps avoid long animations. |
| Dynamic updates | Supports, pending audit | Counts, result list, and detail panel expose polite live updates. |
| Large data layers | Partially supports | Data-center results are text-accessible. The optional power layer is viewport-rendered for performance but does not yet have a full accessible data table. |

## WCAG 2.1 AA Criteria Snapshot

| Criterion | Draft status | Evidence |
| --- | --- | --- |
| 1.1.1 Non-text Content | Supports | Map markers receive accessible names; decorative marker markup is hidden. |
| 1.3.1 Info and Relationships | Supports | Controls use labels, fieldsets, legends, headings, and description lists. |
| 1.3.2 Meaningful Sequence | Supports | Skip links and panel order expose controls, results, then details. |
| 1.4.1 Use of Color | Supports | Status/layer meaning uses text labels and marker initials in addition to color. |
| 1.4.3 Contrast Minimum | Pending audit | CSS was improved; final ratio check required. |
| 1.4.10 Reflow | Pending audit | Responsive shell implemented; verify at 320 CSS px and 200 percent zoom. |
| 1.4.11 Non-text Contrast | Pending audit | Focus rings, borders, and marker labels improved; final tool check required. |
| 2.1.1 Keyboard | Supports, pending audit | Results and form controls are keyboard-operable; map marker traversal needs assistive-tech verification. |
| 2.1.2 No Keyboard Trap | Supports, pending audit | Native controls and Leaflet map region should allow focus to continue. |
| 2.2.2 Pause, Stop, Hide | Supports | Decorative pulse animation is disabled for reduced motion. |
| 2.4.1 Bypass Blocks | Supports | Skip links target controls and results. |
| 2.4.3 Focus Order | Pending audit | DOM order is logical; verify in production embed. |
| 2.4.7 Focus Visible | Supports | Strong focus outlines added for controls, result buttons, links, map, markers, and Leaflet controls. |
| 2.5.3 Label in Name | Supports | Visible control labels match or are contained in accessible names. |
| 3.3.2 Labels or Instructions | Supports | Search and filter groups have labels and helper text. |
| 4.1.2 Name, Role, Value | Supports | Interactive controls have native roles or explicit names; marker buttons are decorated after Leaflet add. |
| 4.1.3 Status Messages | Supports | Result count, result list, and detail updates use live regions. |

## Required Final Evidence

- Automated scan report against the deployed URL.
- Manual keyboard walkthrough notes.
- Screen-reader walkthrough notes.
- 200 percent zoom screenshots.
- Reduced-motion screenshot or notes.
- Production embed review, if iframe integration is used.
- Legal approval of public accessibility claims.
