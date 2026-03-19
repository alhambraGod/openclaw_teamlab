# Skills Reference

OpenClaw TeamLab uses a skill-based architecture where each skill is a self-contained AI behavior module. Skills are defined by `SKILL.md` markdown files and optional Python scripts.

## Skill Directory Structure

```
skills/
├── student_progress/
│   ├── SKILL.md           # AI behavior instructions (Chinese)
│   └── scripts/
│       └── progress.py    # Data retrieval and analysis
├── collaboration_recommend/
│   ├── SKILL.md
│   └── scripts/
│       └── recommend.py
├── ... (11 skills total)
```

## Skill List

### Core Skills

| Skill | Directory | Trigger Examples |
|-------|-----------|-----------------|
| **Student Progress** | `student_progress/` | "Show Zhang San's progress", "capability radar" |
| **Collaboration Recommend** | `collaboration_recommend/` | "Any collaboration suggestions?", "complementary skills" |
| **Individual Guidance** | `individual_guidance/` | "Give Li Si some advice", "development suggestions" |
| **Team Survey** | `team_survey/` | "Compare with other teams", "benchmark" |
| **Direction Discovery** | `direction_discovery/` | "Any new research directions?", "explore opportunities" |

### Academic Skills

| Skill | Directory | Trigger Examples |
|-------|-----------|-----------------|
| **Literature Review** | `literature_review/` | "Search papers on XX", "literature survey" |
| **Academic Writing** | `academic_writing/` | "Polish this abstract", "writing help" |

### Operations Skills

| Skill | Directory | Trigger Examples |
|-------|-----------|-----------------|
| **Meeting Record** | `meeting_record/` | "Summarize last meeting", "meeting minutes" |

### Self-Evolution Skills

| Skill | Directory | Trigger Examples |
|-------|-----------|-----------------|
| **Research Trend** | `research_trend/` | "Latest arXiv papers?", "trending topics" |
| **Email Digest** | `email_digest/` | "Send digest email", triggered by scheduler |

### Internal Skills

| Skill | Directory | Description |
|-------|-----------|-------------|
| **Feishu Interaction** | `feishu_interaction/` | Message parsing, intent routing, card building |

## How Skills Work

### 1. Intent Classification

When a message arrives (via Feishu or web chat), the intent router:

1. Matches keywords from `config/agents.yaml` routing rules
2. If no match or low confidence, falls back to LLM classification
3. Returns the matched skill name and extracted parameters

### 2. Skill Loading

The worker loads the skill via `workers/skill_loader.py`:

```python
skill = load_skill("student_progress")
# Returns: {
#   "name": "student_progress",
#   "instruction": "<content of SKILL.md>",
#   "scripts": {"progress.py": "<source code>"},
# }
```

### 3. Execution

The worker sends the skill instruction + user input + context to the LLM:

```
System: <SKILL.md content>
Context: <student data from MySQL, team info, etc.>
User: <original message>
```

The LLM generates a response following the skill's instructions.

### 4. Result Formatting

Results are formatted based on the skill type:
- **Feishu**: Interactive card (built by `feishu/cards.py`)
- **Web API**: JSON response
- **WebSocket**: Real-time push notification

## Writing Custom Skills

### SKILL.md Template

```markdown
# Skill: <name>

## Skill Name
`<identifier>` — <one-line description>

## Trigger Conditions
When users say:
- "pattern 1"
- "pattern 2"
- Keywords: keyword1, keyword2

## Input Format
```json
{
  "param1": "description",
  "param2": "description"
}
```

## Output Format
```json
{
  "field1": "...",
  "field2": "..."
}
```

## Execution Steps
### Step 1: ...
### Step 2: ...

## Notes
- Important constraints
- Error handling
```

### Adding Intent Routes

Add keyword patterns to `config/agents.yaml`:

```yaml
intent_routing:
  rules:
    - skill: your_skill_name
      patterns:
        - "keyword1"
        - "keyword2"
```

## Skill Reference: Detailed

### student_progress

**Purpose**: Track and visualize student capability growth over time.

**Input**: Student name or ID
**Output**: Capability radar data, progress timeline, growth analysis
**Data Sources**: `students`, `capability_scores`, `capability_dimensions`, `progress_events` tables

### collaboration_recommend

**Purpose**: Discover complementary skill pairs and generate research collaboration ideas.

**Input**: Optional student filters
**Output**: Recommended pairs with complementarity scores and research ideas
**Algorithm**: Computes skill vector similarity, identifies complementary gaps, generates collaboration topics via LLM

### individual_guidance

**Purpose**: Generate personalized development suggestions for a specific student.

**Input**: Student name/ID
**Output**: Strengths, weaknesses, recommended focus areas, specific action items
**Data Sources**: Student profile, capability scores, recent events, peer comparisons

### research_trend

**Purpose**: Scan arXiv and Semantic Scholar for papers relevant to the team.

**Input**: Domains (optional, defaults from `pi_config`), time range
**Output**: Ranked papers with relevance scores and Chinese abstracts
**Scheduled**: Daily at 06:00 via APScheduler

### email_digest

**Purpose**: Generate and send email digests of new research trends.

**Input**: Recipient email, digest type (daily/weekly)
**Output**: HTML email via SMTP
**Deduplication**: Content hash prevents sending duplicate digests
