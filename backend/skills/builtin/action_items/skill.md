---
name: action_items_tracker
version: "1.0.0"
display_name: "Action Items Tracker"
description: |
  Extract concrete action items from meeting content and present them as an accountability tracker.
  Focus on task ownership, due dates, dependencies, and execution status.

intent_matching:
  method: hybrid
  threshold: 0.7
  keywords:
    required: ["action items"]
    optional: ["tasks", "owner", "deadline", "follow-up", "to-do", "responsibilities"]
    weight: 0.5
    semantic_weight: 0.5
  examples:
    - "Extract action items from this meeting"
    - "Create an action tracker with owners and deadlines"
    - "List all follow-up tasks and responsibilities"
    - "Turn this discussion into a task execution list"
  llm_routing:
    enabled: true
    weight: 0.2

execution:
  mode: prompt_integrated
  timeout: 120

output:
  format: markdown
  sections:
    - title: "1. Extraction Scope"
      required: true
      description: "Which meeting context was used and any filtering assumptions"
    - title: "2. High-Priority Action Items"
      required: true
      description: "Critical tasks that should be executed first"
    - title: "3. Full Action Item Register"
      required: true
      description: "Task, owner, due date, dependency, and priority"
    - title: "4. Ownership Gaps"
      required: false
      description: "Tasks missing clear owners or deadlines"
    - title: "5. Recommended Follow-Up Cadence"
      required: false
      description: "Suggested check-in rhythm and progress reporting approach"
  post_process:
    - add_header_footer
    - generate_toc

metadata:
  author: "Meeting Agent Team"
  created_at: "2026-04-15"
  tags: ["tasks", "execution", "accountability", "follow-up", "tracker"]
  category: "operations"
  use_cases:
    - "post-meeting task extraction"
    - "cross-team follow-up"
    - "owner and deadline alignment"
---

# Action Items Tracker

## Overview

This skill converts meeting discussions into a structured action tracker optimized for execution.

## Trigger Conditions

Use this skill when users ask for action-item extraction, to-do lists, or owner/deadline tracking.

## Output Format

The output emphasizes task clarity, ownership, and operational follow-through.

## Usage

1. Provide meeting context that contains explicit or implied tasks
2. Optionally request strict filtering for only high-priority tasks
3. Review unresolved ownership gaps after generation

## Notes

- If deadlines are not provided, mark them as "TBD" instead of inventing dates
- Keep each action item atomic and directly executable
