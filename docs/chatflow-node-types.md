# Chatflow Node Types Documentation

This document describes the supported node types in the chatflow system, their content structures, and fallback behaviors.

## Node Types Overview

| Type | Description | Processor | Execution Context |
|------|-------------|-----------|-------------------|
| `MESSAGE` | Display messages to user | `MessageNodeProcessor` | Backend |
| `QUESTION` | Collect user input | `QuestionNodeProcessor` | Backend |
| `CONDITION` | Branch based on logic | `ConditionNodeProcessor` | Backend |
| `ACTION` | Execute actions | `ActionNodeProcessor` | Backend |
| `WEBHOOK` | Make HTTP requests | `WebhookNodeProcessor` | Backend |
| `COMPOSITE` | Embed sub-flows | `CompositeNodeProcessor` | Backend |
| `SCRIPT` | Execute custom code | `ScriptNodeProcessor` | **Frontend** |

---

## Flow Entry Point

Flows specify their entry point via the `entry_node_id` field, which should point directly to the first meaningful node (typically a MESSAGE or QUESTION node).

```json
{
  "entry_node_id": "welcome",
  "flow_data": {
    "nodes": [
      {
        "id": "welcome",
        "type": "MESSAGE",
        "content": { "text": "Welcome to the chat!" }
      }
    ]
  }
}
```

---

## MESSAGE Node

Displays one or more messages to the user.

### Content Structure

**Option 1: CMS Content Reference (preferred for production)**
```json
{
  "messages": [
    {
      "content_id": "uuid-of-cms-content",
      "delay": 500
    }
  ],
  "typing_indicator": true,
  "wait_for_ack": false
}
```

**Option 2: Direct Text (fallback)**
```json
{
  "text": "Hello {{ temp.user_name }}! Welcome to our chat.",
  "typing_indicator": true,
  "wait_for_ack": false
}
```

### Fields

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `messages` | array | No | `[]` | Array of message configs with `content_id` |
| `text` | string | No | - | Direct text (used if `messages` is empty) |
| `typing_indicator` | boolean | No | `true` | Show typing animation before message |
| `wait_for_ack` | boolean | No | `false` | Wait for user acknowledgment before continuing |

### Variable Substitution
- Uses `{{ scope.variable_name }}` syntax (Jinja2-style)
- Supported scopes: `temp`, `user`, `context`
- Example: `{{ temp.user_name }}` → `"Brian"`

### Fallback Resolution Order
1. `messages[].content_id` → Load from CMS
2. `text` → Use direct text with variable substitution

---

## QUESTION Node

Collects user input and stores it in session state.

### Content Structure

**Option 1: CMS Content Reference**
```json
{
  "question": {
    "content_id": "uuid-of-question-content"
  },
  "variable": "user_name",
  "input_type": "text",
  "validation": {
    "required": true,
    "min_length": 1,
    "max_length": 100
  },
  "options": []
}
```

**Option 2: Inline Question String**
```json
{
  "question": "What is your name?",
  "variable": "user_name",
  "input_type": "text"
}
```

**Option 3: Direct Text (fallback)**
```json
{
  "text": "What is your name?",
  "variable": "user_name",
  "input_type": "text"
}
```

### Fields

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `question` | string/object | No | `{}` | Question text or CMS reference |
| `text` | string | No | - | Direct question text (fallback) |
| `variable` | string | **Yes** | - | Variable name to store response in `state.temp` |
| `input_type` | string | No | `"text"` | Input type: `text`, `choice`, `multiple_choice`, `number`, `email`, `phone`, `url`, `date`, `slider`, `image_choice`, `carousel` |
| `options` | array | No | `[]` | Options for choice-based inputs |
| `validation` | object | No | `{}` | Validation rules |

### Input Types
- `text` - Free text input
- `number` - Numeric input
- `email` - Email validation
- `choice` - Select from options

### Variable Storage
User responses are stored in session state under the scope specified by the variable name. If no scope prefix is given, `temp` is used by default:
```json
{
  "state": {
    "temp": {
      "user_name": "Brian",
      "age_selection": {
        "label": "8",
        "value": "8",
        "age_number": 8
      }
    }
  }
}
```

### Choice Option Matching

For choice-based inputs (`choice`, `image_choice`, `button`), the runtime stores the **full option object** rather than just the raw user input string. This preserves custom fields defined on each option (e.g., `age_number`, `hue_map`, `description`).

The matching flow:
1. When a question node is processed, its options are stored in `system._current_options`
2. When the user answers, `_match_option()` compares the input against each option's `value`, `label`, and `text` fields
3. If a match is found, the entire option dict is stored as the variable value
4. If no match is found, the raw input string is stored as a fallback

This enables downstream nodes to access typed fields via dot notation:
```json
{
  "type": "set_variable",
  "variable": "user.age_number",
  "value": "{{temp.age_selection.age_number}}"
}
```

### Fallback Resolution Order
1. `question` (string) → Use as inline text
2. `question.content_id` → Load from CMS
3. `text` → Use as direct question text

---

## CONDITION Node

Evaluates conditions and branches the flow based on session state.

### Content Structure
```json
{
  "conditions": [
    {
      "if": "temp.age >= 18",
      "then": "$0"
    },
    {
      "if": "temp.age < 18",
      "then": "$1"
    }
  ],
  "default_path": "$0"
}
```

### Fields

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `conditions` | array | **Yes** | `[]` | Array of condition rules |
| `conditions[].if` | string | **Yes** | - | CEL expression or JSON condition |
| `conditions[].then` | string | **Yes** | - | Path to take if true (`$0`, `$1`, etc.) |
| `default_path` | string | No | - | Path if no conditions match |

### Connection Path Mapping

Condition paths map to connection types:
- `$0` → `OPTION_0`
- `$1` → `OPTION_1`
- Anything else → `DEFAULT`

### CEL Expressions (Recommended)
Uses Common Expression Language (CEL) for condition evaluation:
```
temp.variable_name == "value"
temp.age >= 18
temp.score > 50 && temp.score < 100
size(temp.items) > 0
has(user.email) && user.email.endsWith("@company.com")
```

#### CEL Variable Naming Limitation

**Important**: The cel-python library has a parsing limitation with identifiers that end in digits. Variable names like `answer1`, `quiz_answer2`, or `option_3` will cause parsing errors when accessed with dot notation.

**Problematic (will fail)**:
```
temp.answer1 == 'correct'
temp.quiz_answer2 == 'yes'
```

**Solutions**:

1. **Use bracket notation** (recommended for existing variables):
```
temp['answer1'] == 'correct'
temp['quiz_answer2'] == 'yes'
```

2. **Avoid trailing digits in variable names** (recommended for new flows):
```
temp.answer_one == 'correct'
temp.quiz_answer_second == 'yes'
temp.first_answer == 'correct'
```

This limitation only affects CEL expressions in CONDITION nodes. Variable storage and template substitution (`{{ temp.answer1 }}`) work normally with any variable name.

### JSON Conditions (Legacy)
For backward compatibility, JSON-based conditions are also supported:
```json
{"var": "temp.age", "gte": 18}
{"and": [{"var": "temp.age", "gte": 18}, {"var": "temp.status", "eq": "active"}]}
{"or": [{"var": "temp.role", "eq": "admin"}, {"var": "temp.role", "eq": "moderator"}]}
```

### JSON Operators
| Operator | Description |
|----------|-------------|
| `eq` | Equal to |
| `ne` | Not equal to |
| `gt` | Greater than |
| `gte` | Greater than or equal |
| `lt` | Less than |
| `lte` | Less than or equal |
| `in` | Value in list |
| `contains` | String contains substring |
| `exists` | Value is not null |
| `and` | Logical AND (array of conditions) |
| `or` | Logical OR (array of conditions) |
| `not` | Logical NOT |

### Connection Types
- `$0` → `OPTION_0`
- `$1` → `OPTION_1`
- `default` → `DEFAULT`

---

## ACTION Node

Executes backend actions like setting variables or triggering events.

### Content Structure
```json
{
  "actions": [
    {
      "type": "set_variable",
      "variable": "temp.greeting",
      "value": "Hello!"
    },
    {
      "type": "increment",
      "variable": "temp.counter",
      "amount": 1
    },
    {
      "type": "api_call",
      "config": {
        "endpoint": "/v1/recommend",
        "method": "POST",
        "body": {"name": "{{ temp.user_name }}"},
        "response_mapping": {"id": "temp.user_id"},
        "response_variable": "temp.user_data"
      }
    }
  ]
}
```

### Fields

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `actions` | array | **Yes** | `[]` | Array of actions to execute |

### Action Types

| Type | Required Params | Optional Params | Description |
|------|-----------------|-----------------|-------------|
| `set_variable` | `variable`, `value` | - | Set a session variable |
| `increment` | `variable` | `amount` (default: 1) | Increment numeric variable |
| `decrement` | `variable` | `amount` (default: 1) | Decrement numeric variable |
| `delete_variable` | `variable` | - | Remove a variable from state |
| `aggregate` | `expression`, `target` | - | Aggregate list values using CEL expressions |
| `api_call` | `config.endpoint` | `config.method`, `config.headers`, `config.query_params`, `config.body`, `config.response_mapping`, `config.response_variable`, `config.error_variable` | Make an internal API call |
| `emit_event` | `title` | `description`, `info`, `iterate_over`, `item_alias`, `service_account` | Fire-and-forget analytics event |

Note: `config.endpoint` is relative to the `WRIVETED_INTERNAL_API` base URL.

### Aggregate Action

The `aggregate` action type evaluates CEL (Common Expression Language) expressions to aggregate values from lists. This is particularly useful for collecting user responses and computing scores.

**CEL Expression Format (Recommended)**
```json
{
  "type": "aggregate",
  "expression": "sum(temp.quiz_answers.map(x, x.score))",
  "target": "user.total_score"
}
```

**Legacy Format (Backward Compatible)**
```json
{
  "type": "aggregate",
  "source": "temp.quiz_answers",
  "field": "score",
  "operation": "sum",
  "target": "user.total_score"
}
```

**Available Aggregation Functions**

| Function | Description | Example |
|----------|-------------|---------|
| `sum(list)` | Sum numeric values | `sum(temp.scores)` |
| `avg(list)` | Calculate average | `avg(temp.ratings)` |
| `max(list)` | Find maximum value | `max(temp.scores)` |
| `min(list)` | Find minimum value | `min(temp.times)` |
| `count(list)` | Count items in list | `count(temp.answers)` |
| `merge(list_of_dicts)` | Merge dicts by summing numeric values | `merge(temp.hue_maps)` |
| `merge_max(list_of_dicts)` | Merge dicts taking max values | `merge_max(temp.skills)` |
| `merge_last(list_of_dicts)` | Merge dicts with last value wins | `merge_last(temp.prefs)` |
| `flatten(list_of_lists)` | Flatten nested lists | `flatten(temp.tags)` |
| `collect(list)` | Alias for flatten | `collect(temp.items)` |
| `top_keys(dict, n)` | Get top N keys by value from a dict | `top_keys(user.hue_profile, 5)` |

**Real-World Example: Huey Preference Aggregation**
```json
{
  "type": "aggregate",
  "expression": "merge(temp.preference_answers.map(x, x.hue_map))",
  "target": "user.hue_profile"
}
```

### Emit Event Action

The `emit_event` action creates application events for analytics and monitoring. Events are fire-and-forget — errors are logged but never break the chat flow.

**Single Event**
```json
{
  "type": "emit_event",
  "title": "Book Recommended: {{ temp.book_title }}",
  "description": "User received a book recommendation",
  "info": {
    "book_id": "{{ temp.book_id }}",
    "session_id": "{{ context.session_id }}"
  }
}
```

**Iterated Events** — creates one event per item in a list:
```json
{
  "type": "emit_event",
  "title": "Book Viewed: {{ temp.book.title }}",
  "iterate_over": "temp.book_results",
  "item_alias": "book",
  "info": {
    "isbn": "{{ temp.book.isbn }}"
  }
}
```

**Fields**

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `title` | string | **Yes** | - | Event title (supports variable substitution) |
| `description` | string | No | - | Event description (supports variable substitution) |
| `info` | object | No | `{}` | Arbitrary metadata attached to the event |
| `iterate_over` | string | No | - | Variable path to a list; emits one event per item |
| `item_alias` | string | No | `"item"` | Name for the loop variable in `temp.<alias>` |
| `service_account` | string | No | - | Service account name to associate with event |

Events are created with `commit=False` and committed as part of the session state transaction.

### Path Routing
- Returns `success` on completion
- Returns `error` if any action fails

---

## WEBHOOK Node

Makes HTTP requests to external services with circuit breaker protection.

### Content Structure
```json
{
  "url": "https://api.example.com/users",
  "method": "POST",
  "headers": {
    "Authorization": "Bearer {{ context.api_token }}"
  },
  "body": {
    "name": "{{ temp.user_name }}"
  },
  "timeout": 30,
  "response_mapping": {
    "user_id": "$.data.id",
    "user_email": "$.data.email"
  },
  "fallback_response": {
    "user_id": null,
    "error": "API unavailable"
  }
}
```

### Fields

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `url` | string | **Yes** | - | Webhook URL (supports variable substitution) |
| `method` | string | No | `"POST"` | HTTP method: GET, POST, PUT, PATCH, DELETE |
| `headers` | object | No | `{}` | Request headers (supports variable substitution) |
| `body` | object | No | `{}` | Request body (supports variable substitution) |
| `timeout` | number | No | `30` | Timeout in seconds |
| `response_mapping` | object | No | `{}` | Map response fields to session variables |
| `fallback_response` | object | No | `{}` | Fallback values if webhook fails |

### Path Routing
- Returns `success` on successful response
- Returns `fallback` if webhook fails but fallback is configured
- Returns `error` if webhook fails without fallback

---

## COMPOSITE Node

Embeds a sub-flow within the current flow with variable scoping.

### Content Structure
```json
{
  "inputs": {
    "user_name": "temp.user_name",
    "user_context": "context"
  },
  "outputs": {
    "processed_result": "temp.result"
  },
  "nodes": [
    {
      "type": "action",
      "content": {
        "actions": [
          {
            "type": "set_variable",
            "variable": "output.processed_result",
            "value": "Processed: {{ input.user_name }}"
          }
        ]
      }
    }
  ]
}
```

### Fields

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `inputs` | object | No | `{}` | Map parent variables to composite `input` scope |
| `outputs` | object | No | `{}` | Map composite `output` scope to parent variables |
| `nodes` | array | **Yes** | `[]` | Array of child nodes to execute sequentially |

### Variable Scopes in Composite Nodes

| Scope | Access | Description |
|-------|--------|-------------|
| `input` | Read-only | Variables mapped from parent scope |
| `output` | Write | Variables mapped back to parent scope |
| `local` | Read/Write | Temporary variables within composite |

### Path Routing
- Returns `complete` when all child nodes execute successfully
- Returns `error` if any child node fails

---

## SCRIPT Node

Executes custom JavaScript/TypeScript code **in the browser** (frontend execution).

> **Important**: SCRIPT nodes are designed for client-side execution in the chat widget. The backend validates the script configuration but does NOT execute the code. Actual execution happens in a sandboxed browser environment.

### Implementation Status

| Component | Status |
|-----------|--------|
| Schema definition | ✅ Complete |
| Backend validation | ✅ Complete |
| Chat widget executor | ❌ Not implemented |
| Sandbox environment | ❌ Not implemented |
| Admin UI editor | ❌ Not implemented |

The backend `ScriptNodeProcessor` validates configuration and returns script metadata to the frontend. Actual script execution must be implemented in the chat widget.

### Content Structure
```json
{
  "code": "const greeting = 'Hello, ' + inputs.user_name + '!'; return { message: greeting };",
  "language": "javascript",
  "sandbox": "strict",
  "inputs": {
    "user_name": "temp.user_name",
    "count": "temp.item_count"
  },
  "outputs": ["message", "computed_value"],
  "dependencies": [
    "https://cdn.jsdelivr.net/npm/lodash@4.17.21/lodash.min.js"
  ],
  "timeout": 5000,
  "description": "Process user greeting with custom logic"
}
```

### Fields

| Field | Type | Required | Default | Constraints | Description |
|-------|------|----------|---------|-------------|-------------|
| `code` | string | **Yes** | - | min 1 char | JavaScript/TypeScript code to execute |
| `language` | string | **Yes** | - | `"javascript"` or `"typescript"` | Script language |
| `sandbox` | string | No | `"strict"` | `"strict"` or `"permissive"` | Sandbox security level |
| `inputs` | object | No | `{}` | - | Map session variables to script inputs |
| `outputs` | array | No | `[]` | - | Variable names to capture from script return |
| `dependencies` | array | No | `[]` | Trusted CDNs only | External script URLs to load |
| `timeout` | number | No | `5000` | 1000-60000 ms | Execution timeout in milliseconds |
| `description` | string | No | - | - | Human-readable description |

### Trusted CDN Domains for Dependencies
- `cdn.jsdelivr.net`
- `unpkg.com`
- `cdnjs.cloudflare.com`

### Sandbox Modes

| Mode | Description |
|------|-------------|
| `strict` | Maximum isolation, limited API access |
| `permissive` | More APIs available, less isolation |

### Script Execution Context
Scripts receive:
- `inputs` object with resolved values from session state
- Must return an object with keys matching `outputs` array

### Example: Processing User Input
```javascript
// inputs: { user_name: "Brian", score: 85 }
// outputs: ["greeting", "grade"]

const greeting = `Hello, ${inputs.user_name}!`;
const grade = inputs.score >= 90 ? 'A' : inputs.score >= 80 ? 'B' : 'C';

return { greeting, grade };
```

### Backend Response
The backend returns script configuration for frontend execution:
```json
{
  "type": "script",
  "execution_context": "frontend",
  "script_config": {
    "code": "...",
    "language": "javascript",
    "sandbox": "strict",
    "inputs": { "user_name": "Brian" },
    "outputs": ["greeting"],
    "dependencies": [],
    "timeout": 5000
  },
  "node_id": "script_node_1"
}
```

---

## Variable Scopes

Variables are organized into scopes:

| Scope | Access | Description |
|-------|--------|-------------|
| `temp` | Read/Write | Temporary session variables (cleared on session end) |
| `user` | Read/Write | User profile data (writable by action nodes via `set_variable`) |
| `context` | Read-only | Custom session context from `session.state.context` (populate via `initial_state`) |
| `input` | Read-only | Input data for composite nodes |
| `output` | Write | Output data for composite nodes |
| `local` | Read/Write | Local variables within composite scope |

### Variable Reference Syntax
```
{{ scope.variable_name }}
{{ temp.user_name }}
{{ user.email }}
{{ context.locale }}
```

### Dot Notation for Nested Values
```
{{ temp.user.profile.name }}
{{ context.locale }}
```

---

## Connection Types

| Type | Value | Description |
|------|-------|-------------|
| `DEFAULT` | `"default"` | Standard flow connection |
| `OPTION_0` | `"$0"` | First branch option (condition/choice) |
| `OPTION_1` | `"$1"` | Second branch option |
| `SUCCESS` | `"success"` | Success path (webhooks, actions) |
| `FAILURE` | `"failure"` | Error/failure handling path |

---

## Input Validation

All node types undergo input validation before processing. The validation system:

1. **Schema Validation**: Validates content structure against Pydantic schemas
2. **Type Checking**: Ensures fields have correct types
3. **Constraint Checking**: Validates min/max lengths, patterns, enums
4. **Dependency Validation**: For SCRIPT nodes, validates CDN URLs

### Validation Response
```json
{
  "is_valid": true,
  "errors": [],
  "warnings": [
    {
      "field": "timeout",
      "message": "Consider using a shorter timeout for better UX"
    }
  ]
}
```

Validation errors prevent node execution; warnings are logged but execution continues.
