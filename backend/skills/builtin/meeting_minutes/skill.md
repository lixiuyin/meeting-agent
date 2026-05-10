---
name: meeting_minutes_generator
version: "1.0.0"
display_name: "Meeting Minutes Generator"
description: |
  Convert meeting content into structured meeting minutes.
  Capture agenda, key discussion points, decisions, action items, owners, and due dates.

intent_matching:
  method: hybrid
  threshold: 0.68
  keywords:
    required: ["meeting minutes"]
    optional: ["minutes", "discussion summary", "decisions", "action items", "follow-up"]
    weight: 0.5
    semantic_weight: 0.5
  examples:
    - "Generate meeting minutes from this discussion"
    - "Create structured minutes with decisions and action items"
    - "Summarize this meeting into formal minutes"
    - "Draft minutes with owners and deadlines"
  llm_routing:
    enabled: true
    weight: 0.2

execution:
  mode: prompt_integrated
  timeout: 120

output:
  format: markdown
  sections:
    - title: "1. Meeting Overview"
      required: true
      description: "Meeting title, date, participants, and objective"
    - title: "2. Agenda"
      required: true
      description: "Main agenda items discussed"
    - title: "3. Key Discussion Points"
      required: true
      description: "Condensed summary of major discussion threads"
    - title: "4. Decisions Made"
      required: true
      description: "Explicit decisions and their rationale when available"
    - title: "5. Action Items"
      required: true
      description: "Task, owner, due date, and status assumptions"
    - title: "6. Risks or Blockers"
      required: false
      description: "Open risks, blockers, and dependencies"
    - title: "7. Next Steps"
      required: true
      description: "Immediate next actions and next meeting recommendations"
  post_process:
    - add_header_footer
    - generate_toc

metadata:
  author: "Meeting Agent Team"
  created_at: "2026-04-15"
  tags: ["document", "minutes", "meeting", "decisions", "actions"]
  category: "document_generation"
  use_cases:
    - "post-meeting documentation"
    - "decision traceability"
    - "team follow-up tracking"
---

# Meeting Minutes Generator

## Overview

This skill transforms meeting records into clear, actionable meeting minutes.

## Trigger Conditions

Use this skill when users request meeting minutes, decision summaries, or action-item-based recap.

## Output Format

The output follows a practical minutes structure with dedicated sections for decisions and tasks.

## Usage

1. Ensure the relevant meeting content is uploaded and processed
2. Specify whether the output should emphasize decisions, actions, or both
3. Optionally restrict to selected meetings for focused minutes

## Notes

- If owners or due dates are missing, the model should clearly mark them as unspecified
- Keep wording factual and avoid speculative interpretation
