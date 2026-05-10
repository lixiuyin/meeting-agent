---
name: tech_proposal_generator
version: "1.0.0"
display_name: "MOST (PRC) Technical Proposal Generator"
description: |
  Organize meeting content into a technical proposal document aligned with
  the Ministry of Science and Technology of the People's Republic of China (MOST, PRC).
  Automatically extract technical highlights, project goals, and budget details into a
  structured output.

intent_matching:
  method: hybrid
  threshold: 0.7
  keywords:
    required: ["technical proposal"]
    optional: ["MOST", "PRC", "proposal", "feasibility", "application", "project approval"]
    weight: 0.5
    semantic_weight: 0.5
  examples:
    - "Please generate a MOST (PRC) technical proposal"
    - "Create a project proposal based on the meeting discussion"
    - "Draft a technical feasibility analysis report"
    - "Generate an R&D planning document"
  llm_routing:
    enabled: true
    weight: 0.2

execution:
  mode: prompt_integrated
  timeout: 120

output:
  format: markdown
  sections:
    - title: "1. Project Background and Significance"
      required: true
      description: "Explain project background and research significance"
    - title: "2. Domestic and International Research Status"
      required: true
      description: "Summarize recent progress in related fields"
    - title: "3. Research Objectives and Scope"
      required: true
      description: "Define concrete objectives and technical scope"
    - title: "4. Technical Approach and Implementation Plan"
      required: true
      description: "Describe approach, implementation, and innovations"
    - title: "5. Expected Outcomes"
      required: true
      description: "Describe deliverables and evaluation metrics"
    - title: "6. Timeline and Milestones"
      required: true
      description: "Provide project schedule and milestone plan"
    - title: "7. Budget Plan"
      required: true
      description: "Provide detailed budget allocation"
    - title: "8. Risk Analysis"
      required: false
      description: "Identify risks and mitigation strategies"
  post_process:
    - add_header_footer
    - generate_toc

metadata:
  author: "Meeting Agent Team"
  created_at: "2024-01-15"
  tags: ["document", "proposal", "most", "prc", "tech"]
  category: "document_generation"
  use_cases:
    - "research project application"
    - "technical feasibility analysis"
    - "R&D planning"
---

# MOST (PRC) Technical Proposal Generator

## Overview

This skill converts meeting records into a technical proposal format aligned with MOST (PRC).

## Trigger Conditions

Trigger when users request a technical proposal, project proposal, or feasibility report.

## Output Format

The output includes 8 standard sections, with information extracted from meeting content.

## Usage

1. Ensure meeting content has been uploaded and fully processed
2. Clearly specify the proposal topic
3. Optionally specify one or more reference meetings

## Notes

- The generated proposal is for reference and should be reviewed manually
- Remove or mask sensitive data before submission
- Adjust wording and structure according to your target program requirements
