---
name: stakeholder_update_generator
version: "1.0.0"
display_name: "Stakeholder Update Generator"
description: |
  Generate a stakeholder-facing update from meeting discussions.
  Summarize progress, current status, risks, decisions, and next steps in concise business language.

intent_matching:
  method: hybrid
  threshold: 0.68
  keywords:
    required: ["stakeholder update"]
    optional: ["status update", "leadership update", "executive update", "progress report", "weekly update"]
    weight: 0.5
    semantic_weight: 0.5
  examples:
    - "Generate a stakeholder update from these meetings"
    - "Create a concise executive status update"
    - "Draft a weekly project update for leadership"
    - "Summarize progress and risks for stakeholders"
  llm_routing:
    enabled: true
    weight: 0.2

execution:
  mode: prompt_integrated
  timeout: 120

output:
  format: markdown
  sections:
    - title: "1. Executive Summary"
      required: true
      description: "High-level status in 3-5 bullets"
    - title: "2. Progress Since Last Update"
      required: true
      description: "What was completed and what changed"
    - title: "3. Current Risks and Mitigations"
      required: true
      description: "Material risks with mitigation status"
    - title: "4. Decisions and Dependencies"
      required: false
      description: "Key decisions made and unresolved dependencies"
    - title: "5. Next 1-2 Week Plan"
      required: true
      description: "Near-term plan, milestones, and asks"
  post_process:
    - add_header_footer
    - generate_toc

metadata:
  author: "Meeting Agent Team"
  created_at: "2026-04-15"
  tags: ["stakeholder", "executive", "status", "update", "communication"]
  category: "communication"
  use_cases:
    - "leadership communication"
    - "weekly project updates"
    - "cross-team alignment"
---

# Stakeholder Update Generator

## Overview

This skill creates concise stakeholder updates from meeting content for leadership and partner teams.

## Trigger Conditions

Use this skill when users ask for stakeholder, executive, or leadership updates.

## Output Format

The output is concise, status-oriented, and focused on decision-making context.

