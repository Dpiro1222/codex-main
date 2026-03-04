# CLAUDE.md

This file provides guidance to AI assistants (Claude, etc.) working with this codebase.

---

## Project Overview

**AI情報収集 & X投稿案生成ツール** — A Python CLI tool that:
1. Collects the latest AI business news via Perplexity API
2. Scores each item using Claude (Anthropic API)
3. Generates 3 X (Twitter) post drafts per qualifying item using Claude
4. Saves everything to a Notion database

---

## File Structure

```
codex-main/
├── main.py              # Single entry point — runs all 4 steps end-to-end
├── requirements.txt     # Python dependencies
├── .env                 # Secret keys (never commit)
├── .env.example         # Template for .env (safe to commit)
├── README.md            # Setup and usage guide
└── CLAUDE.md            # This file
```

---

## Environment Variables

All secrets are loaded from `.env` via `python-dotenv`. Required keys:

| Variable | Description |
|---|---|
| `ANTHROPIC_API_KEY` | Claude API key |
| `PERPLEXITY_API_KEY` | Perplexity API key |
| `NOTION_API_KEY` | Notion integration secret |
| `NOTION_DATABASE_ID` | Target Notion database ID |

Never hardcode these. Always call `load_dotenv()` at the top of `main.py`.

---

## Processing Flow (4 Steps)

### STEP 1 — Perplexity (Information Gathering)
- Model: `sonar-pro`
- Endpoint: `https://api.perplexity.ai/chat/completions`
- Collects 5 items about: AIエージェント/AI自動化, LINEマーケティング, 中小企業DX, 経営者の課題
- Response parsed as JSON: `{ items: [ { title, url, summary, x_points } ] }`

### STEP 2 — Claude (Scoring)
- Model: `claude-sonnet-4-6`
- Scores each item 1–10 on three axes: buzz potential, relevance to executives, fit with persona
- Items with average score **< 7 are discarded**
- Response parsed as JSON: `{ score: float, reason: str }`

### STEP 3 — Claude (Post Generation)
- Model: `claude-sonnet-4-6`
- Generates 3 X post patterns for each passing item:
  - **Pattern A**: ノウハウ暴露型 (Know-how reveal)
  - **Pattern B**: 業界批評型 (Industry criticism)
  - **Pattern C**: 数字報告型 (Numbers/data-driven)
- Hard constraint: each post must be **≤ 140 characters**
- Response parsed as JSON: `{ pattern_a, pattern_b, pattern_c }`

### STEP 4 — Notion (Save)
- Notion API version: `2022-06-28`
- Creates a new page per item in the target database
- Required Notion database properties (must exist before running):

| Property Name | Type |
|---|---|
| タイトル | title |
| ネタ元URL | url |
| バズスコア | number |
| 投稿案A | rich_text |
| 投稿案B | rich_text |
| 投稿案C | rich_text |
| ステータス | select |
| 収集日 | date |

---

## Key Conventions

### JSON Parsing
All Claude and Perplexity responses are expected as JSON. Use `re.search(r'\{.*\}', content, re.DOTALL)` to extract JSON from mixed text responses before calling `json.loads()`.

### Error Handling
- Each step has `try/except` blocks; API failures print a clear `❌` message
- Perplexity failure → `sys.exit(1)`
- Claude scoring failure for a single item → skip that item, continue
- Notion save failure → log error and continue to next item

### Terminal Output Convention
Use consistent prefix icons for readability:
- `📡` for Perplexity collection
- `🎯` for scoring
- `✍️` for post generation
- `📝` for Notion saving
- `✅` success, `❌` error, `⚠️` warning

---

## Running the Tool

```bash
# Install dependencies
pip install -r requirements.txt

# Set up environment
cp .env.example .env
# Edit .env with your API keys

# Run
python main.py
```

No CLI arguments — running `main.py` executes all 4 steps automatically.

---

## Dependencies

| Package | Purpose |
|---|---|
| `anthropic` | Claude API client |
| `requests` | HTTP calls to Perplexity & Notion |
| `python-dotenv` | Load `.env` file |

See `requirements.txt` for pinned versions.

---

## Notion Setup Notes

Before first run, ensure the Notion database has all required properties with **exact Japanese names and correct types** as listed in the STEP 4 table above. The integration must be connected to the database (share → invite your integration).

---

## What NOT to Do

- Do not commit `.env` — it contains secrets
- Do not change model names without testing; `sonar-pro` and `claude-sonnet-4-6` are required
- Do not remove the 7-point threshold — it is a key quality gate
- Do not increase `max_tokens` for scoring step beyond 300 (responses are short JSON)
