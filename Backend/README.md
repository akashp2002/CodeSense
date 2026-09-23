# CodeSense — Autonomous AI Codebase Analysis & Refactoring Agent

CodeSense is an **AI-powered codebase intelligence and autonomous refactoring system** that allows developers to interact with a repository using natural language.

Instead of manually searching through a large codebase, understanding dependencies, locating references, and performing repetitive refactoring tasks, CodeSense uses a combination of:

* **LLMs**
* **LangGraph**
* **MCP (Model Context Protocol)**
* **Tree-sitter**
* **Vector Search**
* **Neo4j**
* **Git/GitHub**
* **FastAPI**
* **Streamlit**

to understand a codebase, analyze dependencies, answer code-related questions, and safely perform refactoring operations.

The system follows a human-in-the-loop approach for potentially destructive operations: the AI can **draft a refactor and generate a Git diff**, but changes are only pushed to GitHub after explicit user approval.

---

# 1. Key Features

### 🔎 Codebase Semantic Search

Ask natural-language questions about the codebase.

Examples:

```text
Where is authentication handled?
How does resume parsing work?
Where is the database connection created?
```

CodeSense uses vector-based semantic search to retrieve relevant code.

---

### 🕸️ Dependency / Impact Analysis

CodeSense builds a dependency graph of the repository using **Neo4j**.

You can ask:

```text
What would be affected if I change get_db?
```

The system can identify dependent files and symbols.

Example:

```text
Changing 'get_db' may impact 3 reference(s)
across 3 files:

- backend/app/core/security.py
- backend/app/api/auth.py
- backend/app/main.py
```

This provides a basic **blast-radius analysis** before making changes.

---

### 🧠 Natural Language Code Explanation

You can ask questions such as:

```text
Explain how authentication works in this project.
```

The system retrieves relevant code and uses an LLM to explain it in natural language.

---

### 🔧 Autonomous Refactoring

CodeSense can perform requests such as:

```text
Rename clean_text to preprocess_text
```

The system:

1. Identifies the refactor intent.
2. Locates the symbol.
3. Resolves the Python symbol using Tree-sitter.
4. Renames the appropriate definition and references.
5. Generates a Git diff.
6. Shows the diff to the user.
7. Waits for approval.
8. Creates a Git branch.
9. Commits the changes.
10. Pushes the branch.
11. Creates a GitHub Pull Request.

---

### 🔐 Human-in-the-Loop Safety

CodeSense deliberately separates **drafting** from **publishing**.

The AI can modify the local clone and generate:

```text
git diff
```

but it does not automatically push the changes.

The user must explicitly select:

```text
Approve & Create PR
```

before GitHub operations occur.

---

# 2. High-Level Architecture

```text
                    ┌───────────────────────┐
                    │       Streamlit       │
                    │        Frontend       │
                    └───────────┬───────────┘
                                │
                                │ HTTP
                                ▼
                    ┌───────────────────────┐
                    │        FastAPI        │
                    │         API           │
                    └───────────┬───────────┘
                                │
                                ▼
                    ┌───────────────────────┐
                    │    Supervisor Agent   │
                    │       LangGraph       │
                    └───────────┬───────────┘
                                │
              ┌─────────────────┼──────────────────┐
              │                 │                  │
              ▼                 ▼                  ▼
       Semantic Search     Dependency Graph    Refactor Agent
          Agent                Agent                │
              │                 │                   │
              ▼                 ▼                   ▼
        Vector Store          Neo4j             MCP Client
                                                    │
                                                    │ stdio
                                                    ▼
                                      ┌─────────────────────────┐
                                      │       MCP Server        │
                                      │      FastMCP Tools       │
                                      └────────────┬────────────┘
                                                   │
                         ┌─────────────────────────┼──────────────────────┐
                         │                         │                      │
                         ▼                         ▼                      ▼
                       Files                     Git                  GitHub
```

---

# 3. Request Flow

A typical request follows this architecture:

```text
User
 │
 ▼
Streamlit
 │
 │ POST /query
 ▼
FastAPI
 │
 ▼
Supervisor
 │
 │ classify intent
 ▼
┌──────────────────────────────────────────────┐
│                                              │
│ search       → Semantic Search Agent         │
│ impact       → Dependency Graph Agent        │
│ explain      → Search + Explanation          │
│ refactor     → Refactor Agent                │
│                                              │
└──────────────────────────────────────────────┘
```

---

# 4. Repository Loading

The first step is loading a repository.

The user provides a GitHub repository URL through the Streamlit UI.

The frontend sends:

```http
POST /clone
```

The backend:

1. Extracts the repository name.
2. Clones the repository.
3. Stores it under:

```text
Backend/repos/<repository-name>
```

4. Determines the active repository.
5. Stores the repository path using:

```text
CODESENSE_REPO_PATH
```

Example:

```text
CODESENSE_REPO_PATH=D:\CodeSense\Backend\repos\demo
```

This variable is important because the MCP server uses it to determine which repository it should operate on.

---

# 5. Codebase Indexing

After cloning, CodeSense creates two types of indexes.

## Vector Index

The frontend/backend triggers:

```http
POST /index-vectors
```

The codebase is processed into chunks and embeddings.

The vector index enables semantic questions such as:

```text
Where is authentication implemented?
```

rather than requiring exact keyword matches.

---

## Dependency Graph

The system also triggers:

```http
POST /index-graph
```

The repository is analyzed to build relationships between symbols.

The graph is stored in:

```text
Neo4j
```

This allows CodeSense to answer questions such as:

```text
What depends on get_db?
```

---

# 6. Supervisor Agent

The Supervisor is the entry point for intelligent routing.

It receives the user's request.

For example:

```text
Rename clean_text to preprocess_text
```

The Supervisor sends this to an LLM for intent classification.

Possible intents include:

```text
search
impact
explain
refactor
```

The classification model returns a structured object.

Conceptually:

```python
class IntentClassification:
    intent: Literal[
        "search",
        "impact",
        "explain",
        "refactor"
    ]

    extracted_symbol: str | None
```

For:

```text
Rename clean_text to preprocess_text
```

the Supervisor should produce:

```text
intent = "refactor"
```

and may extract:

```text
clean_text
```

as the target symbol.

The Supervisor then routes execution to the appropriate specialist.

---

# 7. LangGraph Orchestration

The Supervisor is implemented using **LangGraph**.

Conceptually:

```text
                    ┌──────────────┐
                    │   Supervisor │
                    └──────┬───────┘
                           │
              ┌────────────┼─────────────┐
              │            │             │
              ▼            ▼             ▼
           Search        Impact        Explain
              │            │             │
              │            │             ▼
              │            │       Semantic Search
              │            │             │
              │            │             ▼
              │            │         Explainer
              │            │
              │            ▼
              │       Dependency Graph
              │
              ▼
       Semantic Search
              
              
                    Refactor
                       │
                       ▼
                Refactor Agent
```

This allows different specialist agents to handle different types of tasks.

---

# 8. Semantic Search Agent

The Semantic Search Agent is responsible for finding relevant code.

Instead of requiring the user to know exact filenames, it can search based on semantic meaning.

For example:

```text
Where is the resume parsing pipeline?
```

The system retrieves relevant code chunks.

The result is then passed to the LLM to produce the final answer.

---

# 9. Dependency Graph Agent

The Dependency Graph Agent uses Neo4j.

Its primary operation is conceptually:

```python
get_impact(symbol_name)
```

For example:

```text
get_impact("get_db")
```

The graph store searches for symbols that depend on `get_db`.

The agent returns information such as:

```json
{
    "target_symbol": "get_db",
    "dependent_count": 3,
    "dependents": [...],
    "summary": "Changing 'get_db' may impact 3 reference(s)..."
}
```

This provides the developer with the potential blast radius of a change.

---

# 10. Refactor Agent

The Refactor Agent is a separate specialist agent.

It uses its own LLM:

```python
ChatGroq(...)
```

rather than inheriting the Supervisor's model.

Its responsibility is to perform code modifications.

The architecture is:

```text
Supervisor
     │
     ▼
Refactor Agent
     │
     ▼
MCP Client
     │
     │ stdio
     ▼
MCP Server
     │
     ├── search_file
     ├── rename_symbol
     ├── read_file
     ├── replace_lines
     ├── get_git_diff
     ├── create_branch
     ├── commit_changes
     ├── push_branch
     └── create_pull_request
```

---

# 11. MCP Architecture

CodeSense uses **Model Context Protocol (MCP)** to separate the AI reasoning layer from the actual repository operations.

The Refactor Agent acts as the MCP client.

The MCP server is started as a separate process:

```text
python -m codesense.mcp.server
```

The client communicates with the server using:

```text
stdio
```

Conceptually:

```text
Refactor Agent
      │
      │ JSON-RPC messages
      │
      ▼
stdin/stdout
      │
      ▼
MCP Server
      │
      ▼
Repository
```

This separation is important.

The LLM does not directly receive unrestricted Python functions.

Instead, it receives standardized MCP tools.

---

# 12. MCP Tools

The MCP server exposes repository operations as tools.

## `list_files`

Lists Python files in the repository.

Example:

```text
list_files()
```

Returns:

```text
backend/app/main.py
backend/app/core/parser.py
backend/app/api/auth.py
```

---

## `search_file`

Searches Python files for a string.

Example:

```text
search_file("clean_text")
```

Possible result:

```text
backend/app/core/parser.py:32:
def clean_text(raw_text: str) -> str:

backend/app/core/parser.py:42:
cleaned = clean_text(raw_text)
```

This allows the agent to discover where a symbol appears.

---

## `read_file`

Reads a repository file with line numbers.

Example:

```text
read_file("backend/app/core/parser.py")
```

Output:

```text
  32 | def clean_text(raw_text: str) -> str:
  33 |     ...
  42 | cleaned = clean_text(raw_text)
```

---

## `rename_symbol`

This is the safer refactoring tool.

Instead of blindly replacing text, it uses **Tree-sitter** to understand the Python syntax tree.

For example:

```text
clean_text
```

can be renamed to:

```text
preprocess_text
```

while avoiding unrelated occurrences such as:

```python
# clean_text must remain in this comment
```

or:

```python
result = clean_text("clean_text")
```

where the string value should not necessarily be modified.

The purpose is to perform an **identifier-aware rename** rather than a global string replacement.

---

## `replace_in_file`

Performs exact text replacement.

Example:

```text
replace_in_file(
    file_path="parser.py",
    old_text="clean_text",
    new_text="preprocess_text"
)
```

This is less safe than `rename_symbol` because it does not understand Python syntax.

It can modify:

* comments
* strings
* unrelated text

Therefore, it should not be the preferred tool for symbol renaming.

---

## `replace_lines`

Replaces a specific range of lines.

Example:

```text
replace_lines(
    file_path="parser.py",
    start_line=30,
    end_line=35,
    replacement_content="..."
)
```

This is useful for controlled modifications but requires the agent to correctly understand line ranges.

---

## `get_git_diff`

Runs:

```bash
git --no-pager diff
```

inside the active repository.

The result shows exactly what the refactor changed.

Example:

```diff
- def clean_text(raw_text: str):
+ def preprocess_text(raw_text: str):

- cleaned = clean_text(raw_text)
+ cleaned = preprocess_text(raw_text)
```

The diff is then shown to the user.

---

## `create_branch`

Creates a new Git branch.

Example:

```text
codesense/refactor-a1b2c3d4
```

---

## `commit_changes`

Stages and commits repository changes.

Conceptually:

```bash
git add .
git commit -m "refactor: rename clean_text to preprocess_text"
```

---

## `push_branch`

Pushes the branch to GitHub:

```bash
git push -u origin <branch>
```

---

## `create_pull_request`

Uses PyGithub to create the GitHub Pull Request.

It uses:

```text
GITHUB_TOKEN
```

to authenticate with GitHub.

---

# 13. Why MCP Uses a Separate Process

The Refactor Agent does not directly execute:

```python
git
```

or:

```python
open(file)
```

itself.

Instead:

```text
Refactor Agent
      │
      │ tool call
      ▼
MCP Client
      │
      │ stdio
      ▼
MCP Server
      │
      ▼
Python / Git / GitHub
```

For example, the LLM may decide:

```text
Call rename_symbol
```

The MCP client sends that request to the server.

The server executes the actual Python operation.

Then the result is returned to the agent.

This gives a clean boundary between:

```text
AI reasoning
```

and:

```text
system operations
```

---

# 14. Safe Symbol Renaming

Earlier versions of CodeSense used simple text replacement.

For example:

```python
content.replace("clean_text", "preprocess_text")
```

This is dangerous.

Consider:

```python
def clean_text(value):
    return value

result = clean_text("clean_text")

# clean_text must remain in this comment
```

Blind replacement could change all three occurrences.

The improved implementation uses Tree-sitter to identify actual Python identifiers.

Therefore:

```python
def clean_text(value):
```

becomes:

```python
def preprocess_text(value):
```

and:

```python
result = clean_text(...)
```

becomes:

```python
result = preprocess_text(...)
```

while unrelated comments or string literals can remain unchanged.

A test was added to verify this behavior.

---

# 15. Refactor Workflow

Suppose the user enters:

```text
Rename clean_text to preprocess_text
```

### Step 1 — User request

Streamlit sends:

```http
POST /query
```

with:

```json
{
    "question": "Rename clean_text to preprocess_text"
}
```

---

### Step 2 — Supervisor

The Supervisor classifies:

```text
refactor
```

and routes the request to:

```text
_refactor_node
```

---

### Step 3 — Refactor Agent

The Refactor Agent starts an MCP server.

```text
python -m codesense.mcp.server
```

---

### Step 4 — Search

The agent calls:

```text
search_file("clean_text")
```

It discovers the relevant Python file.

---

### Step 5 — AST-aware rename

The agent calls:

```text
rename_symbol(
    file_path="backend/app/core/parser.py",
    old_name="clean_text",
    new_name="preprocess_text"
)
```

Tree-sitter identifies the relevant symbols.

---

### Step 6 — Diff

The agent calls:

```text
get_git_diff()
```

The resulting diff is returned.

---

### Step 7 — Human approval

The UI displays:

```text
Changes proposed:

- clean_text
+ preprocess_text
```

and provides:

```text
Approve & Create PR
```

or:

```text
Cancel
```

---

# 16. Pull Request Workflow

The approval phase is intentionally separate.

After the user approves:

```http
POST /create-pr
```

The system performs deterministic Git operations.

```text
Create branch
      ↓
Stage changes
      ↓
Commit
      ↓
Push
      ↓
Create GitHub PR
```

Example branch:

```text
codesense/refactor-a1b2c3d4
```

Example commit:

```text
refactor: rename clean_text to preprocess_text
```

Then GitHub receives the Pull Request.

---

# 17. Why Approval Is Separate

The system does not allow the LLM to automatically push code immediately after generating it.

Instead:

```text
AI
 │
 ├── Understand request
 ├── Modify local clone
 └── Generate diff
          │
          ▼
       HUMAN
          │
     ┌────┴────┐
     │         │
 Approve     Cancel
     │
     ▼
 GitHub
```

This prevents an LLM from autonomously publishing unwanted changes.

---

# 18. Git Diff as a Safety Boundary

The Git diff provides a clear checkpoint.

Before approval, the user can inspect:

```diff
- def clean_text(...)
+ def preprocess_text(...)

- cleaned = clean_text(raw_text)
+ cleaned = preprocess_text(raw_text)
```

Only after reviewing this change does the system proceed to GitHub.

---

# 19. Error Handling

The Refactor Agent has multiple protections.

### Recursion Limit

The LangGraph agent uses:

```python
recursion_limit=8
```

This prevents an agent from endlessly calling tools.

---

### Timeout

The agent has:

```text
120 seconds
```

as its execution timeout.

This protects the backend from an agent or subprocess hanging indefinitely.

---

### Exception Unwrapping

LangGraph/asyncio can produce nested `ExceptionGroup` errors.

CodeSense recursively unwraps them to identify the actual root cause.

For example:

```text
ExceptionGroup
    └── TaskGroup
          └── RateLimitError
```

The system extracts:

```text
RateLimitError
```

instead of only showing:

```text
unhandled errors in a TaskGroup
```

---

# 20. MCP stdio Safety

The MCP server communicates over standard input/output.

Therefore Git subprocesses must not interfere with the MCP communication channel.

Git commands are executed using:

```python
stdin=subprocess.DEVNULL
```

and:

```bash
git --no-pager
```

This prevents Git from:

1. Opening interactive pagers.
2. Waiting for keyboard input.
3. Reading MCP messages from stdin.
4. Blocking the MCP server.

This is particularly important because the MCP transport itself uses stdin/stdout.

---

# 21. Environment Variables

Create a `.env` file based on `.env.example`.

Important variables include:

```env
GROQ_API_KEY=your_groq_api_key

GITHUB_TOKEN=your_github_token

CODESENSE_REPO_PATH=
```

`CODESENSE_REPO_PATH` identifies the repository currently being modified.

The GitHub token is required for operations such as:

```text
push branch
create pull request
```

---

# 22. Project Structure

A simplified project structure:

```text
CodeSense/
│
├── Backend/
│   │
│   ├── src/
│   │   └── codesense/
│   │       │
│   │       ├── agents/
│   │       │   ├── supervisor.py
│   │       │   ├── refactor.py
│   │       │   ├── semantic_search.py
│   │       │   ├── dependency_graph.py
│   │       │   └── explainer.py
│   │       │
│   │       ├── mcp/
│   │       │   └── server.py
│   │       │
│   │       ├── api.py
│   │       ├── graph_store.py
│   │       └── ...
│   │
│   ├── tests/
│   │
│   ├── repos/
│   │
│   ├── .env.example
│   ├── docker-compose.yml
│   └── start_backend.ps1
│
├── frontend/
│   └── app.py
│
└── README.md
```

---

# 23. Technology Stack

| Component           | Technology      |
| ------------------- | --------------- |
| Frontend            | Streamlit       |
| Backend             | FastAPI         |
| Agent orchestration | LangGraph       |
| LLM                 | Groq            |
| Agent framework     | LangChain       |
| Tool protocol       | MCP             |
| MCP implementation  | FastMCP         |
| Code parsing        | Tree-sitter     |
| Semantic search     | Vector database |
| Dependency graph    | Neo4j           |
| Git operations      | Git             |
| GitHub API          | PyGithub        |
| Language            | Python          |
| Containerization    | Docker          |

---

# 24. Running the System

## 1. Clone the repository

```bash
git clone https://github.com/akashp2002/CodeSense.git
cd CodeSense
```

---

## 2. Create virtual environment

```bash
python -m venv .venv
```

Activate it on Windows:

```powershell
.venv\Scripts\Activate.ps1
```

---

## 3. Install dependencies

If using your existing project configuration:

```bash
pip install -r requirements.txt
```

or use the project's configured package manager.

---

## 4. Configure environment

Create:

```text
.env
```

from:

```text
.env.example
```

Configure:

```env
GROQ_API_KEY=...
GITHUB_TOKEN=...
```

---

## 5. Start infrastructure

If using Docker:

```bash
docker compose up -d
```

This starts the required infrastructure such as Neo4j and other configured services.

---

## 6. Start backend

From:

```text
D:\CodeSense\Backend
```

run:

```powershell
.\start_backend.ps1
```

The backend runs on:

```text
http://localhost:8000
```

---

## 7. Start Streamlit

Run the frontend according to the project's Streamlit entry point.

Example:

```bash
streamlit run app.py
```

The UI normally runs on:

```text
http://localhost:8501
```

---

# 25. Example Queries

### Search

```text
Where is authentication implemented?
```

---

### Explanation

```text
Explain how resume parsing works.
```

---

### Impact Analysis

```text
What would be affected if I change get_db?
```

---

### Refactoring

```text
Rename clean_text to preprocess_text
```

---

### Future Refactoring

The architecture can eventually support:

```text
Extract this logic into a separate function.
```

```text
Replace this deprecated API.
```

```text
Refactor this class to follow the repository's coding style.
```

```text
Optimize this function.
```

---

# 26. Current Limitations

### 1. No automated test execution yet

The current refactor workflow does not automatically execute the repository's test suite before creating a PR.

A future MCP tool could expose:

```text
run_tests
```

The workflow could become:

```text
Refactor
   ↓
Run Tests
   ↓
Tests pass?
   │
 ┌─┴───┐
No    Yes
│      │
Repair  ↓
│     Diff
└──────┤
       ↓
    Approval
       ↓
       PR
```

---

### 2. Text/AST limitations

Although symbol renaming uses Tree-sitter, not every possible refactoring operation is AST-aware yet.

More sophisticated transformations could eventually use:

```text
Tree-sitter
+
AST analysis
+
Language Server Protocol
```

---

### 3. Current changes are local until approval

The refactor modifies the cloned repository before approval.

Canceling the approval does not automatically revert those local changes.

The changes remain in the local clone until it is reset, discarded, or recloned.

---

### 4. Git identity

The cloned repository must have a valid Git identity configured:

```bash
git config user.name
git config user.email
```

---

### 5. GitHub permissions

The GitHub token must have sufficient permissions to:

```text
push branches
create pull requests
```

---

# 27. Future Improvements

## Automated Testing

Add an MCP tool:

```text
run_tests
```

Then:

```text
Refactor
   ↓
Run tests
   ↓
Failure?
   ↓
Agent analyzes failure
   ↓
Repair
   ↓
Run tests again
   ↓
Success
   ↓
Human approval
```

---

## Better AST Refactoring

Expand Tree-sitter support for:

* Function extraction
* Import modification
* Class renaming
* Method renaming
* Parameter renaming
* Safe deletion
* Code movement

---

## Persistent Repository State

Currently the active repository path can be held through environment/in-memory state.

A production implementation should persist:

```text
repository ID
repository URL
local path
branch
index status
```

rather than relying on process state.

---

## Authentication and Authorization

Add user authentication and repository-level permissions.

For example:

```text
User
 ↓
Authentication
 ↓
Repository authorization
 ↓
CodeSense
```

---

## Better Observability

Integrate:

```text
LangSmith
structured logs
metrics
tracing
```

to monitor:

* LLM calls
* Tool calls
* latency
* token usage
* failures
* refactor success rate

---

# 28. Design Principles

CodeSense is designed around several important principles.

### Separation of Responsibilities

```text
Supervisor
    ↓
Routing

Specialist Agents
    ↓
Reasoning

MCP Server
    ↓
Actions

GitHub
    ↓
Collaboration
```

---

### Human-in-the-Loop

The AI proposes changes.

The human approves them.

The system publishes them.

---

### Deterministic Operations

The LLM decides **what should happen**.

Tools determine **how it happens**.

For example:

```text
LLM:
"Rename clean_text to preprocess_text"

Tool:
Tree-sitter performs the actual rename.
```

---

### Extensibility

Because tools are exposed through MCP, new capabilities can be added without fundamentally changing the agent architecture.

For example:

```text
run_tests
run_linter
search_github
inspect_dependencies
create_issue
review_pull_request
```

can be added as additional MCP tools.

---

# 29. Complete Refactor Architecture

The final architecture can be summarized as:

```text
                         USER
                           │
                           ▼
                     STREAMLIT UI
                           │
                           │ HTTP
                           ▼
                       FASTAPI
                           │
                           ▼
                   SUPERVISOR AGENT
                      LANGGRAPH
                           │
                    classify intent
                           │
              ┌────────────┼─────────────┐
              │            │             │
              ▼            ▼             ▼
           SEARCH        IMPACT       REFACTOR
              │            │             │
              ▼            ▼             ▼
          Vector DB      Neo4j       Refactor Agent
                                        │
                                        ▼
                                   MCP CLIENT
                                        │
                                   stdio / JSON-RPC
                                        │
                                        ▼
                                   MCP SERVER
                                        │
               ┌────────────────────────┼─────────────────────┐
               │                        │                     │
               ▼                        ▼                     ▼
          Tree-sitter                Git                  GitHub
          Code Tools             Operations              PyGithub
               │                        │                     │
               └────────────────────────┼─────────────────────┘
                                        │
                                        ▼
                                    GIT DIFF
                                        │
                                        ▼
                                  HUMAN APPROVAL
                                   /          \
                              CANCEL          APPROVE
                                               │
                                               ▼
                                         CREATE BRANCH
                                               │
                                               ▼
                                            COMMIT
                                               │
                                               ▼
                                             PUSH
                                               │
                                               ▼
                                        CREATE PR
```

---

# 30. Summary

CodeSense transforms a traditional codebase into an interactive AI development environment.

Instead of manually:

```text
Search code
    ↓
Understand dependencies
    ↓
Find references
    ↓
Modify code
    ↓
Review diff
    ↓
Create branch
    ↓
Commit
    ↓
Push
    ↓
Create PR
```

the developer can communicate naturally:

```text
"What would be affected if I change get_db?"

"Rename clean_text to preprocess_text."
```

CodeSense handles the analysis and automation while maintaining a human approval checkpoint before publishing changes.

The most important architectural idea is the separation between:

```text
LLM reasoning
      ↓
LangGraph orchestration
      ↓
MCP tools
      ↓
Actual code/Git/GitHub operations
```

This makes CodeSense extensible beyond simple code search into an **AI-assisted software engineering platform**.
