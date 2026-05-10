---
name: custom_notes_generator
version: 1.0.0
display_name: Custom Notes Generator
description: Generate custom notes based on selected meetings.
intent_matching:
  method: hybrid
  threshold: 0.7
  keywords:
    required:
    - custom notes
    optional:
    - notes
    - recap
    weight: 0.5
    semantic_weight: 0.5
  examples:
  - Generate custom notes
  llm_routing:
    enabled: true
    weight: 0.2
execution:
  mode: prompt_integrated
  timeout: 120
output:
  format: markdown
  sections:
  - title: 1. Summary
    required: true
    description: Executive summary for Custom Notes Generator
  - title: 2. Key Insights
    required: true
    description: Most important findings from the selected meetings
  - title: 3. Recommendations
    required: true
    description: Actionable recommendations and next steps
  post_process:
  - add_header_footer
  - generate_toc
metadata:
  author: Meeting Agent Team
  created_at: '2026-04-17'
  tags:
  - custom
  - generated
  category: custom
  use_cases:
  - custom workflow
---

# Custom Notes Generator

## Overview

Generate custom notes based on selected meetings.

## Trigger Conditions

Trigger when users ask for outputs related to Custom Notes Generator.

## Output Format

Output follows the configured markdown section structure.
