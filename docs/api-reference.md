# API Reference

Base URL: `http://localhost:10301`

## Dashboard

### GET /api/dashboard/overview

Returns team overview data: student counts by status, recent events, active research directions, and system health.

**Response:**
```json
{
  "student_counts": {"active": 5, "graduated": 2, "on_leave": 1},
  "recent_events": [
    {"id": 1, "student_id": 1, "event_type": "paper_submitted", "title": "...", "event_date": "2026-03-10"}
  ],
  "active_directions": 3,
  "system_status": {"queue_length": 0, "active_workers": 3, "gateway": "online"}
}
```

### GET /api/dashboard/activity

Returns recent activity feed for the dashboard timeline.

## Students

### GET /api/claw_students

List all claw_students. Optional query parameters:
- `status` — Filter by status: `active`, `graduated`, `on_leave`

### POST /api/claw_students

Create a new student record.

**Request:**
```json
{
  "name": "Zhang San",
  "email": "zhangsan@example.com",
  "degree_type": "phd",
  "research_area": "Natural Language Processing",
  "enrollment_date": "2024-09-01"
}
```

### GET /api/claw_students/{student_id}

Get detailed student profile.

### PUT /api/claw_students/{student_id}

Update student information.

### DELETE /api/claw_students/{student_id}

Delete a student record (cascades to scores and events).

### GET /api/claw_students/{student_id}/radar

Get capability radar chart data for a student.

**Response:**
```json
{
  "student_id": 1,
  "student_name": "Zhang San",
  "dimensions": [
    {"name": "literature", "label": "Literature Review", "score": 7.5},
    {"name": "coding", "label": "Programming", "score": 8.0},
    {"name": "writing", "label": "Academic Writing", "score": 6.5}
  ]
}
```

### GET /api/claw_students/{student_id}/timeline

Get progress event timeline for a student.

## Meetings

### GET /api/claw_meetings

List claw_meetings. Optional query parameters:
- `meeting_type` — Filter by type: `group`, `individual`, `seminar`, `external`
- `limit` — Max results (default 20)

### POST /api/claw_meetings

Create a meeting record. If `raw_notes` is provided, AI summary is auto-generated.

**Request:**
```json
{
  "title": "Weekly Group Meeting",
  "meeting_type": "group",
  "meeting_date": "2026-03-14T14:00:00",
  "duration_min": 90,
  "attendees": ["Zhang San", "Li Si"],
  "raw_notes": "Discussion notes..."
}
```

### GET /api/claw_meetings/{meeting_id}

Get meeting details with AI summary, topics, and action items.

## Research Directions

### GET /api/directions

Get research direction tree (hierarchical).

**Response:**
```json
[
  {
    "id": 1,
    "title": "Large Language Models",
    "status": "active",
    "source": "pi_defined",
    "children": [
      {"id": 2, "title": "LLM for Code Generation", "status": "exploring"}
    ]
  }
]
```

### POST /api/directions

Create a new research direction.

### PUT /api/directions/{direction_id}

Update a research direction.

## Collaborations

### GET /api/collaborations

Get the collaboration recommendation network (for force-directed graph).

**Response:**
```json
{
  "nodes": [
    {"id": 1, "name": "Zhang San", "research_area": "NLP"}
  ],
  "edges": [
    {
      "source": 1, "target": 2,
      "complementarity_score": 0.85,
      "overlap_score": 0.30,
      "research_idea": "Combine NLP with CV for multimodal analysis",
      "status": "suggested"
    }
  ]
}
```

### POST /api/collaborations/refresh

Trigger re-computation of collaboration recommendations.

## Chat (Task Submission)

### POST /api/chat

Submit a natural language task. The system classifies intent and dispatches to the appropriate skill.

**Request:**
```json
{
  "message": "Show me Zhang San's progress",
  "user_id": "user123"
}
```

**Response:**
```json
{
  "task_id": "abc123",
  "skill": "student_progress",
  "status": "queued"
}
```

### GET /api/chat/result/{task_id}

Poll for task result.

**Response (completed):**
```json
{
  "task_id": "abc123",
  "status": "completed",
  "result_summary": "...",
  "result_data": {...}
}
```

### GET /api/chat/history/{user_id}

Get conversation history for a user.

## System

### GET /api/system/status

System health and status.

**Response:**
```json
{
  "gateway": "online",
  "uptime_seconds": 3600,
  "host": "0.0.0.0",
  "port": 10301,
  "env": "prod",
  "queue_length": 2,
  "workers": [
    {"id": "worker-0", "port": 10310, "status": "idle"}
  ],
  "active_workers": 3,
  "redis": "connected"
}
```

### GET /api/system/config

Get PI configuration values.

### PUT /api/system/config/{key}

Update a PI configuration value.

**Request:**
```json
{
  "value": {"domains": ["cs.AI", "cs.CL", "cs.CV"]},
  "description": "Tracked arXiv domains"
}
```

### POST /api/system/init-db

Initialize database tables (admin only).

## WebSocket

### WS /ws

Real-time updates via WebSocket.

**Connection:**
```javascript
const ws = new WebSocket('ws://localhost:10301/ws?client_id=my-client');
```

**Message types received:**
```json
{"type": "task_update", "task_id": "...", "status": "completed", "data": {...}}
{"type": "notification", "message": "New research trend discovered"}
{"type": "ack", "data": "..."}  // Echo/keepalive
```
