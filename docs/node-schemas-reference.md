# Node Content Schemas Reference

Complete reference for all Flow Builder node types and their expected content structures.

**Date**: 2025-12-06
**Purpose**: Guide for implementing custom properties panels in the Flow Builder UI

---

## 1. MESSAGE Node

**Node Type**: `message`
**Purpose**: Display text messages, rich media, and quick reply buttons to users

### Schema (Pydantic)
```python
class MessageNodeContent(BaseModel):
    messages: List[Dict[str, Any]]
    typing_indicator: Optional[bool] = True
```

### Example Content
```json
{
  "messages": [
    {
      "content_id": "uuid-of-content-item",
      "delay": 1000
    }
  ],
  "text": "Hello! Welcome to our chatbot.",
  "typing_indicator": true
}
```

### Fields for Custom Panel
- **Messages Array** (required): List of message objects
  - Each message should have:
    - `content_id`: UUID reference to CMS content item
    - `delay`: Milliseconds to wait before showing message
- **Text Fallback** (optional): `text` at the top level is rendered when no CMS content is configured
- **Typing Indicator** (optional, default true): Show "bot is typing..." animation
- **Quick Reply Buttons** (optional): Array of button objects

---

## 2. QUESTION Node

**Node Type**: `question`
**Purpose**: Ask users for input and store their response in session variables

### Schema (Pydantic)
```python
class QuestionNodeContent(BaseModel):
    question: Dict[str, Any]
    input_type: str
    options: Optional[List[Dict[str, str]]] = []
    validation: Optional[Dict[str, Any]] = {}
```

### Example Content
```json
{
  "question": {
    "content_id": "uuid-of-question-content",
    "text": "What is your name?"
  },
  "input_type": "text",
  "variable": "user.name",
  "validation": {
    "required": true,
    "min_length": 2,
    "max_length": 50
  }
}
```

### Fields for Custom Panel
- **Question Text** (required): The question to ask
  - Can reference `content_id` or provide direct `text`
- **Input Type** (required): Type of input expected
  - Options: `text`, `number`, `email`, `phone`, `url`, `date`, `choice`, `multiple_choice`, `slider`, `image_choice`, `carousel`
- **Variable Name** (required): Where to store the answer (e.g., `user.name`, `temp.age`)
- **Options** (for choice types): Array of selectable options
  - Each option: `{ "label": "Option A", "value": "a" }`
- **Validation Rules** (optional):
  - `required`: Boolean
  - `min_length`, `max_length`: For text
  - `min`, `max`: For numbers
  - `pattern`: Regex pattern for validation
  - `error_message`: Custom message for validation failure
- **Retry Configuration** (optional):
  - `max_retries`: Number of attempts allowed
  - `retry_message`: Message shown on invalid input
- **Slider Configuration** (optional, `slider` only): `slider_config` with `min`, `max`, `step`, labels, etc.
- **Carousel Configuration** (optional, `carousel` only): `carousel_config` with item layout metadata

---

## 3. CONDITION Node

**Node Type**: `condition`
**Purpose**: Branch conversation flow based on session state using conditional logic

### Schema (Pydantic)
```python
class ConditionNodeContent(BaseModel):
    conditions: List[Dict[str, Any]]
```

### Example Content
```json
{
  "conditions": [
    {
      "if": {"var": "user.age", "gte": 18},
      "then": "$0"
    },
    {
      "if": {"var": "user.country", "eq": "NZ"},
      "then": "$1"
    }
  ],
  "default_path": "option_2"
}
```

### CEL Expression Format (Alternative)
```json
{
  "conditions": [
    {
      "if": "user.age >= 18 && user.country == 'NZ'",
      "then": "$0"
    }
  ],
  "default_path": "$1"
}
```

### Fields for Custom Panel
- **Conditions Array** (required): List of condition objects evaluated in order
  - Each condition has:
    - `if`: Condition expression (CEL or JSON Logic format)
    - `then`: Connection path to follow (`$0`, `$1`, etc.)
- **Default Path** (required): Fallback path if no conditions match
- **Logic Type** (implicit): Conditions evaluated sequentially (first match wins)

### Supported Operators
- **Comparison**: `eq`, `ne`, `gt`, `gte`, `lt`, `lte`
- **Logical**: `and`, `or`, `not`
- **String**: `contains`, `startsWith`, `endsWith`, `matches` (regex)
- **Existence**: `exists`, `missing`

---

## 4. ACTION Node

**Node Type**: `action`
**Purpose**: Execute operations like setting variables, API calls, or business logic

### Schema (Pydantic)
```python
class ActionNodeContent(BaseModel):
    actions: List[Dict[str, Any]]
```

### Example Content (Multiple Actions)
```json
{
  "actions": [
    {
      "type": "set_variable",
      "variable": "user.score",
      "value": 100
    },
    {
      "type": "increment",
      "variable": "session.counter",
      "amount": 1
    },
    {
      "type": "api_call",
      "config": {
        "endpoint": "/v1/recommend",
        "method": "POST",
        "body": {
          "name": "{{user.name}}",
          "email": "{{user.email}}"
        },
        "response_variable": "temp.user_response"
      }
    }
  ],
  "async": false
}
```

### Action Types

#### 1. `set_variable`
Set a session variable to a value
```json
{
  "type": "set_variable",
  "variable": "temp.result",
  "value": "some value"
}
```

#### 2. `increment` / `decrement`
Modify numeric variables
```json
{
  "type": "increment",
  "variable": "user.score",
  "amount": 10
}
```

#### 3. `delete_variable`
Remove a variable from session
```json
{
  "type": "delete_variable",
  "variable": "temp.cache"
}
```

#### 4. `api_call`
Make internal API calls. For known internal endpoints, the call is routed directly to the service layer without HTTP overhead. For other endpoints, a standard HTTP request is made.

```json
{
  "type": "api_call",
  "config": {
    "endpoint": "/v1/recommend",
    "method": "POST",
    "auth_type": "internal",
    "body": {
      "wriveted_identifier": "{{context.school_wriveted_id}}",
      "age": "{{user.age_number}}"
    },
    "response_mapping": {
      "books": "temp.book_results",
      "count": "temp.book_count"
    },
    "response_variable": "temp.api_result"
  }
}
```

#### 5. `aggregate`
Aggregate values from a list using CEL (Common Expression Language) expressions.

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

**Available CEL Aggregation Functions:**
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

**Legacy Format Fields:**
- `source`: Variable path containing a list (e.g., `temp.answers`)
- `field`: Optional field to extract from each object (e.g., `score`)
- `operation`: Aggregation operation (`sum`, `avg`, `max`, `min`, `count`, `merge`, `collect`)
- `target`: Variable path to store the result
- `merge_strategy`: For merge operation - `sum` (default), `max`, `last`

#### 6. `emit_event`
Create application events for analytics and monitoring. Fire-and-forget — errors are logged but never break the chat flow.

**Single Event**
```json
{
  "type": "emit_event",
  "title": "Book Recommended: {{temp.book_title}}",
  "description": "User received a book recommendation",
  "info": {
    "book_id": "{{temp.book_id}}",
    "session_id": "{{context.session_id}}"
  }
}
```

**Iterated Events** — one event per item in a list:
```json
{
  "type": "emit_event",
  "title": "Book Viewed: {{temp.book.title}}",
  "iterate_over": "temp.book_results",
  "item_alias": "book",
  "info": {
    "isbn": "{{temp.book.isbn}}"
  }
}
```

**Fields:**
| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `title` | string | **Yes** | - | Event title (supports variable substitution) |
| `description` | string | No | - | Event description (supports variable substitution) |
| `info` | object | No | `{}` | Arbitrary metadata attached to the event |
| `iterate_over` | string | No | - | Variable path to a list; emits one event per item |
| `item_alias` | string | No | `"item"` | Loop variable name injected into `temp.<alias>` |
| `service_account` | string | No | - | Service account name to associate with event |

Events are created with `commit=False` — they are committed atomically with the session state transaction, so events are never orphaned.

### Fields for Custom Panel
- **Actions Array** (required): List of actions to execute
- **Action Type Selector**: Dropdown for each action
- **Type-Specific Fields**: Dynamic form based on action type
- **Async Execution** (optional): Run actions asynchronously
- **Error Handling**:
  - Continue on error vs. abort
  - Error message variable
- **API Call Endpoint**: Relative to `WRIVETED_INTERNAL_API`. Internal endpoints with registered handlers (see `app/services/internal_api_handlers.py`) are called directly via the service layer.
- **Auth Type** (optional): `"internal"` (default) for service-layer calls, or `"bearer"` for authenticated HTTP requests

---

## 5. WEBHOOK Node

**Node Type**: `webhook`
**Purpose**: Call external HTTP endpoints

### Schema (Pydantic)
```python
class WebhookNodeContent(BaseModel):
    url: str
    method: str = "POST"
    headers: Optional[Dict[str, str]] = {}
    body: Optional[Dict[str, Any]] = {}
    response_mapping: Optional[Dict[str, str]] = {}
    timeout: Optional[int] = 30
    fallback_response: Optional[Dict[str, Any]] = {}
```

### Example Content
```json
{
  "url": "https://api.example.com/webhook",
  "method": "POST",
  "headers": {
    "Authorization": "Bearer {{env.API_TOKEN}}",
    "Content-Type": "application/json"
  },
  "body": {
    "event": "user_registered",
    "user": {
      "name": "{{user.name}}",
      "email": "{{user.email}}"
    }
  },
  "timeout": 30,
  "response_mapping": {
    "status": "$.status",
    "request_id": "$.request_id"
  },
  "fallback_response": {
    "status": "fallback"
  }
}
```

### Fields for Custom Panel
- **URL** (required): Webhook endpoint URL
  - Validation: Must be valid HTTP/HTTPS URL
  - Variable interpolation supported: `{{variable}}`
- **HTTP Method** (required): Dropdown with GET, POST, PUT, PATCH, DELETE
- **Headers** (optional): Key-value pairs
  - Add/remove header rows
  - Support for environment variables: `{{env.VAR_NAME}}`
- **Request Body** (optional): JSON body payload
  - For POST/PUT/PATCH methods
  - Variable interpolation supported
- **Authentication** (optional):
  - Type: None, Bearer Token, API Key, Basic Auth
  - Credentials fields based on type
- **Timeout** (optional): Milliseconds (default 5000)
- **Retry Policy** (optional):
  - Max attempts
  - Backoff strategy (linear, exponential)
- **Response Handling**:
  - Success variable: Where to store response
  - Error variable: Where to store error details
- **Test Button**: Send test request with sample data

---

## 6. COMPOSITE Node

**Node Type**: `composite`
**Purpose**: Encapsulate reusable sub-flows with variable mapping

### Schema (Inferred from tests)
```python
# No explicit Pydantic schema, but structure is:
{
  "inputs": Dict[str, str],    # Map composite inputs to session vars
  "outputs": Dict[str, str],   # Map composite outputs to session vars
  "nodes": List[Dict[str, Any]]  # Child nodes to execute
}
```

### Example Content
```json
{
  "inputs": {
    "user_name": "user.name",
    "user_email": "user.email"
  },
  "outputs": {
    "processed_name": "temp.result",
    "status": "temp.status"
  },
  "nodes": [
    {
      "type": "action",
      "content": {
        "actions": [
          {
            "type": "set_variable",
            "variable": "processed_name",
            "value": "PROCESSED_{{user_name}}"
          }
        ]
      }
    }
  ]
}
```

### Alternative: Reference External Flow
```json
{
  "flow_id": "uuid-of-sub-flow",
  "inputs": {
    "user_data": "user",
    "context": "session.context"
  },
  "outputs": {
    "result": "temp.composite_result"
  }
}
```

### Fields for Custom Panel
- **Composition Type**:
  - Inline: Define child nodes directly
  - Reference: Link to existing flow
- **Flow Selector** (if reference): Dropdown of available flows
- **Input Mapping** (optional):
  - Composite variable name → Session variable path
  - Add/remove mappings
  - Variable browser to select from session
- **Output Mapping** (optional):
  - Composite result → Session variable path
  - Add/remove mappings
- **Child Nodes** (if inline):
  - Mini node editor or JSON editor
  - Or: Visual sub-flow designer (advanced)
- **Scope Isolation**: Checkbox to isolate variables

---

## 7. SCRIPT Node

**Node Type**: `script`
**Purpose**: Execute custom JavaScript/TypeScript code in frontend

### Schema (Backend)
```python
# Content structure expected by backend
{
  "code": str,
  "language": str,  # "javascript" or "typescript"
  "description": Optional[str],
  "timeout": Optional[int],  # milliseconds
  "sandbox": Optional[bool],  # default True
  "execution_context": str  # "frontend" or "backend"
}
```

### Example Content
```json
{
  "code": "const result = inputs.value * 2;\noutputs['temp.result'] = result;",
  "language": "javascript",
  "description": "Double the input value",
  "timeout": 5000,
  "sandbox": true,
  "execution_context": "frontend"
}
```

### Fields for Custom Panel ✅ **IMPLEMENTED**
- **Description** (optional): What this script does
- **Language** (required): JavaScript or TypeScript
- **Code** (required): Script code (textarea with syntax highlighting)
- **Timeout** (optional): Execution timeout in ms (1000-60000, default 5000)
- **Sandbox** (optional): Run in isolated sandbox (default true)
- **Security Notes**: Display warnings about frontend execution

---

## 8. Flow Entry Point

Flows specify their entry point via `entry_node_id`, which should point directly to the first meaningful node (typically a MESSAGE or QUESTION node).

```json
{
  "entry_node_id": "welcome",
  "flow_data": {
    "nodes": [
      {
        "id": "welcome",
        "type": "MESSAGE",
        "content": { "text": "Welcome!" }
      }
    ]
  }
}
```

For composite nodes, execution begins at the first node in the `nodes` array.

---

## Connection Types

Nodes connect via `FlowConnection` with types:

- `DEFAULT`: Standard connection
- `OPTION_0`, `OPTION_1`: Condition branches (paths `$0` and `$1`)
- `SUCCESS`, `FAILURE`: Action/webhook outcomes
- `TIMEOUT`: Timeout branch
- `ERROR`: Error handling branch

---

## Variable Interpolation

All node types support variable interpolation using `{{variable.path}}` syntax:

```json
{
  "text": "Hello {{user.name}}!",
  "url": "https://api.example.com/users/{{user.id}}",
  "value": "{{session.counter}}"
}
```

### Variable Scopes
- `user.*`: User profile data (persistent, writable by action nodes)
- `session.*`: Session metadata (persistent)
- `temp.*`: Temporary variables (cleared on session end)
- `env.*`: Environment variables (read-only)

---

## Validation Rules

Common validation applied across node types:

1. **Required Fields**: Node-specific required fields must be present
2. **Variable Names**: Must match pattern `^[a-zA-Z_][a-zA-Z0-9_.]*$`
3. **Content IDs**: Must be valid UUIDs when referencing CMS content
4. **URLs**: Must be valid HTTP/HTTPS URLs
5. **Timeouts**: Must be positive integers (typically 1000-60000 ms)
6. **CEL Expressions**: Must be valid Common Expression Language syntax

---

## Implementation Priority

Based on UX assessment and user needs:

1. ✅ **SCRIPT** - Complete (custom panel implemented)
2. 🟡 **MESSAGE** - High priority (simple, high usage)
3. 🟡 **QUESTION** - High priority (complex, high value)
4. 🟡 **CONDITION** - High priority (most difficult UX)
5. 🟠 **ACTION** - Medium priority (multiple action types)
6. 🟠 **COMPOSITE** - Medium priority (complex mapping)
7. 🟠 **WEBHOOK** - Medium priority (external integrations)

---

## References

- Backend schemas: `app/schemas/cms.py`
- Node processors: `app/services/node_processors.py`, `app/services/action_processor.py`
- Integration tests: `app/tests/integration/test_chat_runtime.py`, `app/tests/integration/test_advanced_node_processors.py`
- Frontend enums: `wriveted-admin-ui/adapters/cms.ts`
