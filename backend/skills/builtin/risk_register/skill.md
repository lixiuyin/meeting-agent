---
name: risk_register_generator
version: "1.0.0"
display_name: "Risk Register Generator"
description: |
  Build a structured risk register from meeting discussions.
  Capture risk statements, impact, likelihood, mitigation actions, contingency plans, and owners.

intent_matching:
  method: hybrid
  threshold: 0.7
  keywords:
    required: ["risk register"]
    optional: ["risk", "mitigation", "impact", "likelihood", "contingency", "issue"]
    weight: 0.5
    semantic_weight: 0.5
  examples:
    - "Generate a risk register from this meeting"
    - "Identify project risks and mitigation plans"
    - "Create a risk matrix with owners and actions"
    - "Summarize operational risks discussed in this call"
  llm_routing:
    enabled: true
    weight: 0.2

execution:
  mode: prompt_integrated
  timeout: 120

output:
  format: markdown
  sections:
    - title: "1. Risk Context"
      required: true
      description: "Scope and assumptions used to identify risks"
    - title: "2. Top Risks (Executive View)"
      required: true
      description: "Highest severity risks with short explanations"
    - title: "3. Full Risk Register"
      required: true
      description: "Risk ID, description, impact, likelihood, severity, owner, due date"
    - title: "4. Mitigation and Contingency Plan"
      required: true
      description: "Preventive actions, fallback actions, and trigger conditions"
    - title: "5. Monitoring and Escalation"
      required: false
      description: "Indicators, review cadence, and escalation thresholds"
  post_process:
    - add_header_footer
    - generate_toc

metadata:
  author: "Meeting Agent Team"
  created_at: "2026-04-15"
  tags: ["risk", "register", "mitigation", "project", "operations"]
  category: "risk_management"
  use_cases:
    - "project risk tracking"
    - "governance and compliance reviews"
    - "cross-functional risk alignment"
---

# Risk Register Generator

## Overview

This skill transforms risk-related discussion into a reusable risk register for execution and review.

## Trigger Conditions

Use this skill when users ask for risk identification, risk matrix creation, or mitigation planning.

## Output Format

The output includes both executive summary and detailed risk register sections.

## Usage

1. Include meetings where risks, blockers, or concerns were discussed
2. Optionally request focus on financial, technical, or delivery risk
3. Review severity and ownership assignments before operational rollout

## Notes

- Risks should be specific and testable, not generic statements
- Use explicit "unknown" markers where impact or likelihood is unclear
