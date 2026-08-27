---
name: ui-data
description: Explore UI states and local or approved data sources without exposing sensitive data
---

# UI and Data Investigation

Start from the product task and data contract. Describe the primary screen, loading state, empty state, error state, narrow-screen behavior, keyboard path, headings, labels, focus treatment, and responsive breakpoints.

For data work, inspect only the approved local fixture or explicitly approved MCP server. Record the source name, fields observed, assumptions, and validation gaps. Prefer schema and aggregate information over raw records.

If an MCP tool is unavailable, say so and continue with local synthetic data or a written plan. Never invent a successful database query. Never print credentials, cookies, personal data, or production records into an artifact.

Return the following headings:

- UI goals
- Screens and states
- Accessibility and responsive notes
- Data sources and schema observations
- MCP tools used
- Artifacts and paths
- Open questions
