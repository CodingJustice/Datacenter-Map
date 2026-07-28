# ADA Certification Path

Date: July 22, 2026
Product: U.S. Data Centers, Power, Nuclear, and Water Landscape

## Bottom Line

There is no code-only switch that creates a binding legal ADA certification.
Legal certification is reachable as an evidence-backed process, but it requires
human review, documented testing, remediation records, policy support, and final
sign-off by the responsible organization, counsel, or an independent
accessibility auditor.

This repository can support that process. It should not be described as legally
certified until the sign-off steps below are complete.

## Current Legal Standard To Validate

- DOJ Title II web/mobile rule: WCAG 2.1 Level AA is the technical standard for
  state and local government web content and mobile apps.
- DOJ guidance also preserves effective communication, reasonable
  modifications, and equal opportunity obligations even where an exception may
  apply.
- Section508.gov describes an Accessibility Conformance Report (ACR) as the
  document used to explain how an ICT product conforms to accessibility
  standards. The product owner must test the product before completing the ACR.

Sources:

- https://www.ada.gov/resources/2024-03-08-web-rule/
- https://www.ada.gov/resources/web-rule-first-steps/
- https://www.section508.gov/sell/acr/
- https://www.section508.gov/sell/how-to-create-acr-with-vpat/

## Certification Package

1. Technical conformance target
   - WCAG 2.1 Level AA as the ADA Title II floor.
   - WCAG 2.2 Level AA as the product target where practical.

2. Evidence artifacts
   - `ADA_ACCESSIBILITY_PRD.md`
   - `ACCESSIBILITY_CONFORMANCE_REPORT.md`
   - `accessibility.html`
   - Automated test output, manual keyboard notes, screen-reader notes, browser
     zoom screenshots, and reduced-motion verification notes.

3. Required review methods
   - Automated scanning with axe, WAVE, Lighthouse accessibility, or equivalent.
   - Manual keyboard-only review.
   - Screen reader review with at least NVDA/Firefox or NVDA/Chrome on Windows;
     VoiceOver/Safari should be added for public consumer use.
   - Browser zoom/reflow testing at 200 percent.
   - Color-contrast and non-color-cue review.
   - Reduced-motion review.
   - Cognitive/plain-language review of public-facing content.

4. Required sign-offs
   - Product owner confirms scope and intended publisher.
   - Engineering owner confirms remediation and regression testing.
   - Accessibility specialist or independent auditor confirms conformance.
   - Legal counsel confirms ADA title, deadline, exceptions, and public claims.

## Blocking Items Before Public Certification

- Replace the placeholder accessibility contact in `accessibility.html` with a
  monitored email address, phone number, or support form.
- Run and archive an automated accessibility report against the production URL.
- Run and archive manual assistive-technology test notes.
- Confirm whether the publisher is covered by ADA Title II, Title III, Section
  508, state accessibility rules, contractual requirements, or a combination.
- If embedded in an iframe, test the integrated parent page, not only this map.
- Have counsel approve any public claim such as "ADA compliant" or
  "WCAG 2.1 AA conformant."

## Reachability

Reachable: yes, for technical WCAG 2.1 AA conformance of this static map
experience, assuming the production embed, hosting headers, support process,
and accessibility contact are included in scope.

Not reachable by code alone: legal certification, because it depends on the
organization's public claims, maintenance process, procurement context,
communication obligations, and legal interpretation.
